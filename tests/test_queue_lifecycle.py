"""任务生命周期与项目级任务去重的回归测试（审查 F02/F03/F05/F22/F23）。

覆盖的行为：
- F02 取消：领取时记录计算子进程 PID 与创建时间；取消先请求、再按受控进程树终止，
  确认父子进程都退出后才写终态 cancelled；PID 被复用（创建时间不符）时不误杀。
- F03 并发：supervisor 全局租约 + start_worker 幂等；领取时检查 max_concurrent（默认 1）；
  两个不同任务同时提交时运行峰值不超过上限。
- F05 去重：去重键含 project_id；跨项目不复用；同项目并发提交仍唯一；复用时不再复制快照。
- F22 恢复：worker/drain 启动即恢复；running 任务写 heartbeat/lease_until；只有 PID 不存在
  或租约超时才判失联；失联（failure_kind=interrupted）与失败（failed）分开；重启后继续调度。
- F23 准备失败留档：领取后写配置/读配置/启动 CLI 的异常都写终态与 error，不留下永久 running。

所有用例只用 CPU（python 子进程 + 受控 .cmd 替身），不需要 CUDA。
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path

from cellflow import queue
from cellflow.queue import connect

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / 'examples' / 'synthetic.npz'

# 只需 CPU 的快速 qc 配置（约 2–3 秒）
FAST_CONFIG = {'method': 'qc', 'seed': 1, 'n_top_genes': 60, 'n_neighbors': 10, 'n_pcs': 10,
               'raw_x_confirmed': True}
ACTIVE = ('completed', 'failed', 'cancelled')


# ------------------------------------------------------------------ 辅助
def _row(store, job_id):
    with closing(connect(store)) as db:
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        return dict(row) if row else None


def _wait(predicate, timeout, interval=0.1):
    """轮询直到 predicate 返回真值；超时返回最后一次结果。"""
    deadline = time.time() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.time() >= deadline:
            return value
        time.sleep(interval)


def _wait_state(store, job_id, states=ACTIVE, timeout=180):
    def check():
        row = _row(store, job_id)
        return row if row and row['state'] in states else None
    return _wait(check, timeout)


def _insert_job(store, job_id, **overrides):
    """直接插一条任务行（用于构造取消/恢复/准备失败等受控场景）。"""
    now = time.time()
    row = dict(id=job_id, method='qc', state='queued', submitted=now, updated=now,
               input=str(store / 'input.npz'), config=json.dumps(FAST_CONFIG),
               output=str(store / job_id), runtime=sys.executable, error='',
               fingerprint='fp-' + job_id, pid=None, dataset=str(DEMO), name=job_id,
               cancel_requested=0, project_id='alpha', pid_created=None, child_pid=None,
               child_created=None, heartbeat=now, lease_until=None, failure_kind=None,
               attempt=0)
    row.update(overrides)
    columns = ', '.join(row)
    placeholders = ', '.join(':' + key for key in row)
    with closing(connect(store)) as db, db:
        db.execute(f'INSERT INTO jobs ({columns}) VALUES ({placeholders})', row)
    return row


def _snapshots(store):
    folder = Path(store) / 'inputs'
    if not folder.exists():
        return []
    return sorted(path.name for path in folder.iterdir())


def _write_shim(folder, seconds=3):
    """写一个受控计算替身（不跑真实模型，只等待）。"""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        target = folder / 'slow.cmd'
        target.write_text('@echo off\r\n"%s" -c "import time; time.sleep(%d)"\r\nexit /b 0\r\n'
                          % (sys.executable, int(seconds)), encoding='utf-8')
    else:
        target = folder / 'slow.sh'
        target.write_text('#!/bin/sh\nsleep %d\nexit 0\n' % int(seconds), encoding='utf-8')
        target.chmod(0o755)
    return target


def _shim_job(store, shim, seed, project='alpha'):
    job = queue.submit(DEMO, {**FAST_CONFIG, 'seed': seed}, store=store, project=project)
    with closing(connect(store)) as db, db:
        db.execute('UPDATE jobs SET runtime=? WHERE id=?', (str(shim), job))
    return job


def _spawn_sleep(seconds=120):
    """一个独立的长命进程（模拟计算进程/无关进程）。"""
    return subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(%d)' % int(seconds)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def _spawn_tree(seconds=120):
    """父 python 启动一个 sleep 子进程：用于验证受控进程树取消。"""
    code = ('import subprocess, sys, time\n'
            'child = subprocess.Popen([sys.executable, "-c", '
            '"import time; time.sleep(%d)"])\n'
            'print(child.pid, flush=True)\n'
            'time.sleep(%d)\n' % (int(seconds), int(seconds)))
    parent = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    child_pid = int(parent.stdout.readline().strip())
    return parent, child_pid


def _reap(proc):
    try:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=15)
    except Exception:
        pass
    stream = getattr(proc, 'stdout', None)
    if stream is not None:
        try:
            stream.close()
        except Exception:
            pass


def _kill_pid(pid):
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(int(pid))],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        else:
            os.kill(int(pid), 9)
    except Exception:
        pass


class QueueLifecycleTest(unittest.TestCase):
    def setUp(self):
        self._folder = tempfile.TemporaryDirectory(prefix='cellflow-queue-',
                                                   ignore_cleanup_errors=True)
        self.addCleanup(self._folder.cleanup)
        self.root = Path(self._folder.name)
        self.store = self.root / 'results'

    # ------------------------------------------------------------- F05 项目去重
    def test_dedupe_key_includes_project_id(self):
        first = queue.submit(DEMO, dict(FAST_CONFIG), store=self.store, project='alpha')
        second = queue.submit(DEMO, dict(FAST_CONFIG), store=self.store, project='beta')
        self.assertNotEqual(first, second, '跨项目不能复用同一个任务 ID')
        self.assertEqual([row['id'] for row in queue.list_jobs(store=self.store, project='alpha')],
                         [first])
        self.assertEqual([row['id'] for row in queue.list_jobs(store=self.store, project='beta')],
                         [second])
        snapshots = _snapshots(self.store)
        self.assertEqual(len(snapshots), 2, '两个项目各自持有一份输入快照')
        referenced = {Path(row['input']).name
                      for row in queue.list_jobs(store=self.store, project='all')}
        self.assertEqual(referenced, set(snapshots), '不能留下没有任务引用的快照')

    def test_same_project_reuse_does_not_copy_snapshot(self):
        first = queue.submit(DEMO, dict(FAST_CONFIG), store=self.store, project='alpha')
        before = _snapshots(self.store)
        second = queue.submit(DEMO, dict(FAST_CONFIG), store=self.store, project='alpha')
        self.assertEqual(first, second, '同项目相同内容与配置应复用活动任务')
        self.assertEqual(_snapshots(self.store), before, '复用时不能再复制输入快照')
        self.assertEqual(len(queue.list_jobs(store=self.store, project='alpha')), 1)

    def test_concurrent_same_project_submits_remain_unique(self):
        barrier = threading.Barrier(6)
        results, errors = [], []

        def worker():
            try:
                barrier.wait(20)
                results.append(queue.submit(DEMO, dict(FAST_CONFIG), store=self.store,
                                            project='alpha'))
            except Exception as exc:   # pragma: no cover - 失败时把异常带回主线程
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(120)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 6)
        self.assertEqual(len(set(results)), 1, '同项目并发提交必须得到同一个任务 ID')
        self.assertEqual(len(queue.list_jobs(store=self.store, project='alpha')), 1)
        snapshots = _snapshots(self.store)
        self.assertEqual(len(snapshots), 1, '并发提交也只能留下一个输入快照')
        self.assertFalse([name for name in snapshots if '.part' in name])

    def test_failed_submit_leaves_no_job_or_snapshot(self):
        with self.assertRaises(OSError):
            queue.submit(self.root / 'missing.npz', dict(FAST_CONFIG), store=self.store,
                         project='alpha')
        self.assertEqual(queue.list_jobs(store=self.store, project='all'), [])
        self.assertEqual(_snapshots(self.store), [], '提交失败不能留下无主快照')

    # ------------------------------------------------------------- F02 取消进程树
    def test_cancel_running_job_terminates_process_tree(self):
        parent, child_pid = _spawn_tree()
        self.addCleanup(_reap, parent)
        self.addCleanup(_kill_pid, child_pid)
        created = queue._process_create_time(parent.pid)
        child_created = queue._process_create_time(child_pid)
        self.assertIsNotNone(created)
        self.assertIsNotNone(child_created)
        _insert_job(self.store, 'job-tree', state='running', pid=parent.pid,
                    pid_created=created, child_pid=child_pid, child_created=child_created,
                    lease_until=time.time() + 600)

        ok, message = queue.cancel('job-tree', store=self.store)
        if os.name == 'nt':
            self.assertFalse(ok, '未登记到 Job Object 的旧任务不得按 PID 强杀')
            self.assertTrue(queue._pid_alive(child_pid))
        else:
            self.assertTrue(ok, message)
            self.assertFalse(queue._pid_alive(child_pid))
        self.assertTrue(queue._pid_alive(parent.pid), '取消不能结束 supervisor')

    def test_cancel_terminates_orphan_compute_child(self):
        parent, child_pid = _spawn_tree()
        self.addCleanup(_reap, parent)
        self.addCleanup(_kill_pid, child_pid)
        created = queue._process_create_time(parent.pid)
        _insert_job(self.store, 'job-orphan', state='running', pid=parent.pid,
                    pid_created=created, child_pid=child_pid,
                    child_created=queue._process_create_time(child_pid),
                    lease_until=time.time() + 600)
        # 只结束 supervisor（不带进程树），复现审查里的「supervisor 死了、计算还在跑」
        parent.kill()
        parent.wait(timeout=15)
        self.assertFalse(queue._pid_alive(parent.pid))
        self.assertTrue(queue._pid_alive(child_pid), '子进程应当成为孤儿')

        ok, message = queue.cancel('job-orphan', store=self.store)
        if os.name == 'nt':
            self.assertFalse(ok, '缺少受控 Job Object 时必须 fail closed')
            self.assertTrue(queue._pid_alive(child_pid))
        else:
            self.assertTrue(ok, message)
            self.assertFalse(queue._pid_alive(child_pid))

    def test_cancel_does_not_kill_reused_pid(self):
        innocent = _spawn_sleep()
        self.addCleanup(_reap, innocent)
        created = queue._process_create_time(innocent.pid)
        _insert_job(self.store, 'job-reuse', state='running', pid=innocent.pid,
                    pid_created=(created or 0.0) + 3600.0, lease_until=time.time() + 600)

        ok, message = queue.cancel('job-reuse', store=self.store)
        self.assertFalse(ok, '由 supervisor 处理缺少可验证计算身份的记录')
        self.assertTrue(queue._pid_alive(innocent.pid))
        self.assertEqual(_row(self.store, 'job-reuse')['cancel_requested'], 1)

    def test_running_job_records_compute_process(self):
        shim = _write_shim(self.root / 'shim', seconds=4)
        job = _shim_job(self.store, shim, seed=11)
        thread = threading.Thread(target=queue.drain,
                                  kwargs={'store': self.store, 'token': 'test-record'},
                                  daemon=True)
        thread.start()
        self.addCleanup(thread.join, 60)

        def running_with_child():
            row = _row(self.store, job)
            return row if row and row['state'] == 'running' and row['child_pid'] else None

        row = _wait(running_with_child, 60, interval=0.05)
        self.assertIsNotNone(row, '运行中的任务应记录计算子进程 PID')
        self.assertTrue(queue._pid_alive(row['child_pid']), '记录的计算进程应当存活')
        self.assertIsNotNone(row['child_created'], '运行中的任务应记录计算进程创建时间')
        self.assertIsNotNone(row['heartbeat'], '运行中的任务应写 heartbeat')
        self.assertIsNotNone(row['lease_until'], '运行中的任务应有租约')
        thread.join(120)
        final = _row(self.store, job)
        self.assertEqual(final['state'], 'completed')
        self.assertIsNone(final['child_pid'], '收尾后应清空计算进程记录')
        self.assertIsNone(final['lease_until'])

    def test_cancel_real_worker_stops_compute_and_resumes_queue(self):
        """真实 supervisor 链路：取消仅结束计算树，预先排队任务自动继续。"""
        shim = _write_shim(self.root / 'shim', seconds=6)
        job = _shim_job(self.store, shim, seed=61)
        next_job = _shim_job(self.store, shim, seed=62)
        self.assertTrue(queue.start_worker(store=self.store))

        def running_with_child():
            row = _row(self.store, job)
            return row if row and row['state'] == 'running' and row['child_pid'] else None

        row = _wait(running_with_child, 60, interval=0.05)
        self.assertIsNotNone(row, 'supervisor 没有领取任务')
        self.assertTrue(queue._pid_alive(row['child_pid']))
        ok, message = queue.cancel(job, store=self.store)
        self.assertTrue(ok, message)
        self.assertFalse(queue._pid_alive(row['child_pid']), '取消后计算子进程必须退出')
        self.assertTrue(queue._pid_alive(row['pid']), '取消后 supervisor 必须继续调度')
        final = _row(self.store, job)
        self.assertEqual(final['state'], 'cancelled')
        self.assertIn('取消', final['error'])

        # supervisor 被结束，租约残留在库里；新提交应能重新拉起 worker 并继续调度
        self.assertFalse(queue.start_worker(store=self.store), '既有 supervisor 仍然存活')
        final = _wait_state(self.store, next_job, timeout=180)
        self.assertIsNotNone(final)
        self.assertEqual(final['state'], 'completed', self._worker_log())

    # ------------------------------------------------------------- F03 并发上限/租约
    def test_claim_respects_global_concurrency_limit(self):
        queue.submit(DEMO, {**FAST_CONFIG, 'seed': 31}, store=self.store, project='alpha')
        queue.submit(DEMO, {**FAST_CONFIG, 'seed': 32}, store=self.store, project='alpha')
        first = queue.claim_next(store=self.store, max_concurrent=1)
        self.assertIsNotNone(first)
        self.assertIsNone(queue.claim_next(store=self.store, max_concurrent=1),
                          '达到全局并发上限后不能再领取新任务')
        second = queue.claim_next(store=self.store, max_concurrent=2)
        self.assertIsNotNone(second, '提高上限后应能领取第二个任务')
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(queue._running_count(self.store), 2)

    def test_max_concurrent_is_configurable(self):
        old = os.environ.get('CELLFLOW_MAX_CONCURRENT')
        try:
            os.environ['CELLFLOW_MAX_CONCURRENT'] = '3'
            self.assertEqual(queue.max_concurrent_limit(), 3)
            os.environ['CELLFLOW_MAX_CONCURRENT'] = 'not-a-number'
            self.assertEqual(queue.max_concurrent_limit(), 1, '非法配置回落到默认 1')
        finally:
            if old is None:
                os.environ.pop('CELLFLOW_MAX_CONCURRENT', None)
            else:
                os.environ['CELLFLOW_MAX_CONCURRENT'] = old

    def test_start_worker_is_idempotent(self):
        shim = _write_shim(self.root / 'shim', seconds=3)
        job = _shim_job(self.store, shim, seed=21)
        self.assertTrue(queue.start_worker(store=self.store), '第一次应启动 supervisor')
        self.assertFalse(queue.start_worker(store=self.store), '已有存活 supervisor 时不能再启动')
        self.assertFalse(queue.start_worker(store=self.store))
        lease = queue.worker_lease(self.store)
        self.assertIsNotNone(lease, '启动后应存在 supervisor 租约')
        self.assertTrue(lease['token'])
        final = _wait_state(self.store, job, timeout=180)
        self.assertIsNotNone(final)
        self.assertEqual(final['state'], 'completed',
                         (final.get('error'), self._worker_log()))

    def test_second_supervisor_does_not_take_over(self):
        shim = _write_shim(self.root / 'shim', seconds=3)
        job = _shim_job(self.store, shim, seed=22)
        self.assertTrue(queue.start_worker(store=self.store))
        self.assertFalse(queue.drain(store=self.store, token='intruder'),
                         '已有存活 supervisor 时不应接管租约')
        final = _wait_state(self.store, job, timeout=180)
        self.assertIsNotNone(final)
        self.assertEqual(final['state'], 'completed', self._worker_log())

    def test_two_simultaneous_submits_peak_at_limit(self):
        # 两个不同任务 + 两个并发提交请求：受控计算替身，只关心队列侧并发峰值
        shim = _write_shim(self.root / 'shim', seconds=3)
        jobs = [_shim_job(self.store, shim, seed=41), _shim_job(self.store, shim, seed=42)]
        barrier = threading.Barrier(2)
        started, errors = [], []

        def launch():
            try:
                barrier.wait(20)
                started.append(queue.start_worker(store=self.store))
            except Exception as exc:   # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=launch) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(started), [False, True], '同时提交只应启动一个 supervisor')

        peak = 0
        deadline = time.time() + 240
        rows = []
        while time.time() < deadline:
            rows = queue.list_jobs(store=self.store, project='all')
            peak = max(peak, sum(1 for row in rows if row['state'] == 'running'))
            if len(rows) >= 2 and all(row['state'] in ACTIVE for row in rows):
                break
            time.sleep(0.1)
        self.assertEqual(len(rows), 2)
        self.assertLessEqual(peak, 1, f'运行峰值 {peak} 超过全局上限 1')
        by_id = {row['id']: row for row in rows}
        for job in jobs:
            self.assertIn(job, by_id)
            self.assertEqual(by_id[job]['state'], 'completed', (job, by_id[job]['error']))

    # ------------------------------------------------------------- F22 恢复/心跳
    def test_heartbeat_extends_lease_before_recovery(self):
        now = time.time()
        _insert_job(self.store, 'job-heartbeat', state='running', pid=os.getpid(),
                    pid_created=queue._process_create_time(os.getpid()),
                    heartbeat=now - 120, updated=now - 120, lease_until=now - 60)
        touched = queue.heartbeat(store=self.store)
        self.assertGreaterEqual(touched, 1)
        row = _row(self.store, 'job-heartbeat')
        self.assertGreater(row['heartbeat'], now - 5)
        self.assertGreater(row['lease_until'], now)
        self.assertEqual(queue.recover_orphans(store=self.store), [],
                         '心跳续租后不能被判失联')

    def test_alive_worker_beyond_24h_is_not_recovered(self):
        now = time.time()
        _insert_job(self.store, 'job-long', state='running', pid=os.getpid(),
                    pid_created=queue._process_create_time(os.getpid()),
                    heartbeat=now - 25 * 3600, updated=now - 25 * 3600,
                    lease_until=now + 600)
        self.assertEqual(queue.recover_orphans(store=self.store), [],
                         'PID 存在且租约有效时，updated 久未变化也不能判失败')
        self.assertEqual(_row(self.store, 'job-long')['state'], 'running')

    def test_expired_lease_is_recovered_as_interrupted(self):
        now = time.time()
        _insert_job(self.store, 'job-expired', state='running', pid=os.getpid(),
                    pid_created=queue._process_create_time(os.getpid()),
                    heartbeat=now - 900, updated=now - 900, lease_until=now - 60)
        recovered = queue.recover_orphans(store=self.store)
        self.assertIn('job-expired', recovered)
        row = _row(self.store, 'job-expired')
        self.assertEqual(row['state'], 'failed')
        self.assertEqual(row['failure_kind'], 'interrupted', '失联必须与执行失败区分记录')
        self.assertIn('租约', row['error'])

    def test_dead_worker_is_recorded_as_interrupted(self):
        dead = _spawn_sleep()
        created = queue._process_create_time(dead.pid)
        _reap(dead)
        self.assertFalse(queue._pid_alive(dead.pid))
        _insert_job(self.store, 'job-dead', state='running', pid=dead.pid,
                    pid_created=created, lease_until=time.time() + 600)
        recovered = queue.recover_orphans(store=self.store)
        self.assertIn('job-dead', recovered)
        row = _row(self.store, 'job-dead')
        self.assertEqual(row['state'], 'failed')
        self.assertEqual(row['failure_kind'], 'interrupted')
        self.assertIn('失联', row['error'])

    def test_reused_pid_is_treated_as_lost_not_killed(self):
        innocent = _spawn_sleep()
        self.addCleanup(_reap, innocent)
        created = queue._process_create_time(innocent.pid)
        _insert_job(self.store, 'job-reused', state='running', pid=innocent.pid,
                    pid_created=(created or 0.0) - 3600.0, lease_until=time.time() + 600)
        recovered = queue.recover_orphans(store=self.store)
        self.assertIn('job-reused', recovered, 'PID 创建时间不符说明进程已失联')
        self.assertTrue(queue._pid_alive(innocent.pid), '不能结束占用同名 PID 的无关进程')
        self.assertEqual(_row(self.store, 'job-reused')['failure_kind'], 'interrupted')

    def test_worker_startup_recovers_and_resumes_queued(self):
        shim = _write_shim(self.root / 'shim', seconds=2)
        first = _shim_job(self.store, shim, seed=51)
        second = _shim_job(self.store, shim, seed=52)
        dead = _spawn_sleep()
        created = queue._process_create_time(dead.pid)
        _reap(dead)
        _insert_job(self.store, 'job-stale', state='running', pid=dead.pid,
                    pid_created=created, lease_until=time.time() + 600,
                    fingerprint='fp-stale')

        self.assertTrue(queue.start_worker(store=self.store), '重启后应能启动 supervisor')
        for job in (first, second):
            final = _wait_state(self.store, job, timeout=240)
            self.assertIsNotNone(final, '重启 worker 后应继续调度 queued 任务')
            self.assertEqual(final['state'], 'completed', self._worker_log())
        stale = _wait(lambda: (lambda row: row if row and row['state'] in ACTIVE else None)(
            _row(self.store, 'job-stale')), 120, interval=0.05)
        self.assertIsNotNone(stale, '启动时应恢复上次崩溃留下的 running 记录')
        self.assertEqual(stale['state'], 'failed')
        self.assertEqual(stale['failure_kind'], 'interrupted')

    # ------------------------------------------------------------- F23 准备失败留档
    def test_config_write_failure_leaves_terminal_state(self):
        _insert_job(self.store, 'job-write-fail', fingerprint='fp-write-fail')
        (self.store / 'job-write-fail.json.part').mkdir(parents=True)
        self.assertTrue(queue.drain(store=self.store, token='test-write-fail'))
        row = _row(self.store, 'job-write-fail')
        self.assertEqual(row['state'], 'failed', '写配置失败也必须落到终态')
        self.assertEqual(row['failure_kind'], 'failed')
        self.assertTrue(row['error'])
        self.assertIsNone(row['pid'])
        self.assertIsNone(row['lease_until'])

    def test_unreadable_config_leaves_terminal_state(self):
        _insert_job(self.store, 'job-bad-config', fingerprint='fp-bad-config',
                    config='{"method": "qc", ')
        self.assertTrue(queue.drain(store=self.store, token='test-bad-config'))
        row = _row(self.store, 'job-bad-config')
        self.assertEqual(row['state'], 'failed')
        self.assertEqual(row['failure_kind'], 'failed')
        self.assertTrue(row['error'])
        self.assertIsNone(row['pid'])

    def test_missing_runtime_leaves_terminal_state(self):
        _insert_job(self.store, 'job-no-runtime', fingerprint='fp-no-runtime',
                    runtime=str(self.root / 'no-such-python.exe'))
        self.assertTrue(queue.drain(store=self.store, token='test-no-runtime'))
        row = _row(self.store, 'job-no-runtime')
        self.assertEqual(row['state'], 'failed')
        self.assertEqual(row['failure_kind'], 'failed')
        self.assertIn('Error', row['error'])
        self.assertIsNone(row['pid'])
        self.assertIsNone(row['lease_until'])

    def test_queue_never_stays_running_after_prepare_failure(self):
        _insert_job(self.store, 'job-no-runtime-2', fingerprint='fp-no-runtime-2',
                    runtime=str(self.root / 'no-such-python.exe'))
        queue.drain(store=self.store, token='test-never-running')
        states = [row['state'] for row in queue.list_jobs(store=self.store, project='all')]
        self.assertNotIn('running', states)
        self.assertNotIn('queued', states)

    # ------------------------------------------------------------- 工具
    def _worker_log(self):
        path = Path(self.store) / 'worker.log'
        try:
            return path.read_text(encoding='utf-8', errors='replace')[-2000:]
        except OSError:
            return ''


if __name__ == '__main__':
    unittest.main()
