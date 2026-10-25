"""公开数据下载故障的回归测试。

覆盖两个真实缺陷：
  R1 准备工作失败（临时目录不可创建）曾让任务永远停在 queued：
     界面无限“排队中”，没有错误、没有结束。
  R2 两个并发下载必须各自上报自己的进度/取消（下载钩子是线程局部的，
     不得退回成进程级全局钩子，否则后一个任务会顶掉前一个的进度与取消）。
另含一条前端对账所依赖的接口契约：GET /api/downloads 必须给出
  id / source / identifier / project / created，
  前端在 POST 于传输层失败后靠它判断“任务是否其实已经创建”。
"""
import tempfile
import threading
import time
import unittest
from pathlib import Path

from cellflow import downloads


def wait_state(task_id, states, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = downloads.get(task_id)
        if task and task['state'] in states:
            return task
        time.sleep(0.03)
    return downloads.get(task_id)


class PreparationFailure(unittest.TestCase):
    """R1：准备阶段异常必须落到任务状态里。"""

    def test_unwritable_staging_marks_task_failed(self):
        with tempfile.TemporaryDirectory() as folder:
            # outdir 是一个“文件”，所以 outdir/.downloads/<id> 无法创建。
            blocker = Path(folder) / 'not-a-directory'
            blocker.write_text('x', encoding='utf-8')

            task_id = downloads.submit('geo', 'GSE72857', blocker,
                                       fetch_func=lambda *a, **k: None)
            task = wait_state(task_id, {'failed', 'done', 'cancelled'})

            self.assertIsNotNone(task, '任务记录不应丢失')
            self.assertEqual(task['state'], 'failed',
                             '准备阶段失败必须是 failed，不能停留在 queued')
            self.assertTrue(task['error'], '失败必须带可读原因')
            self.assertIn('Error', task['error'])

    def test_preparation_failure_does_not_leave_queued_forever(self):
        with tempfile.TemporaryDirectory() as folder:
            blocker = Path(folder) / 'blocked'
            blocker.write_text('x', encoding='utf-8')
            task_id = downloads.submit('geo', 'GSE181411', blocker,
                                       fetch_func=lambda *a, **k: None)
            time.sleep(1.0)   # 远大于线程调度时间
            task = downloads.get(task_id)
            self.assertEqual(task['state'], 'failed',
                             '超过 1 秒还停在 queued 就是“幽灵任务”缺陷复现')


class ConcurrentProgress(unittest.TestCase):
    """R2：并发下载各自上报自己的进度，不互相覆盖。"""

    def test_two_downloads_keep_separate_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            outdir = Path(folder)
            release = threading.Event()

            def make_fetch(name, done, total):
                def fetch(source, identifier, staging, progress=None):
                    target = Path(staging) / name
                    target.write_bytes(b'x' * 32)
                    progress(f'{name} 开始')
                    from cellflow.publicdata import _emit
                    _emit(done, total)
                    release.wait(8)        # 停在“下载中”，让测试读取并发进度
                    return target
                return fetch

            first = downloads.submit('fake', 'a', outdir, fetch_func=make_fetch('a.h5ad', 10, 100))
            second = downloads.submit('fake', 'b', outdir, fetch_func=make_fetch('b.h5ad', 20, 200))

            # 等两个任务都上报过字节进度（而不是完成后的文件大小）
            deadline = time.time() + 8
            while time.time() < deadline:
                task_a = downloads.get(first) or {}
                task_b = downloads.get(second) or {}
                if task_a.get('total') and task_b.get('total'):
                    break
                time.sleep(0.03)

            self.assertEqual(task_a.get('total'), 100, 'A 的进度不能被 B 覆盖')
            self.assertEqual(task_b.get('total'), 200, 'B 的进度不能被 A 覆盖')
            self.assertEqual(task_a.get('done'), 10)
            self.assertEqual(task_b.get('done'), 20)

            release.set()
            self.assertEqual(wait_state(first, {'done', 'failed'})['state'], 'done')
            self.assertEqual(wait_state(second, {'done', 'failed'})['state'], 'done')


class ReconcileContract(unittest.TestCase):
    """前端 POST 断网后靠 GET /api/downloads 对账，字段不能缺。"""

    def test_list_endpoint_exposes_reconcile_fields(self):
        try:
            from fastapi.testclient import TestClient
            from cellflow import api, publicdata
        except Exception as exc:                       # pragma: no cover
            self.skipTest(f'缺少 fastapi 运行环境：{exc}')

        name = 'hotfix_reconcile_test.h5ad'
        final = Path(__file__).resolve().parents[1] / 'data' / 'public' / name

        def fake(source, identifier, outdir, progress=None):
            target = Path(outdir) / name
            target.write_bytes(b'x' * 64)
            return target

        original = publicdata.fetch
        publicdata.fetch = fake
        try:
            client = TestClient(api.app)
            started = client.post('/api/downloads',
                                  json={'source': 'url', 'identifier': 'https://example.com/a.h5ad'})
            self.assertEqual(started.status_code, 200, started.text[:200])
            task_id = started.json()['id']
            wait_state(task_id, {'done', 'failed'})

            listing = client.get('/api/downloads')
            self.assertEqual(listing.status_code, 200)
            rows = listing.json()['downloads']
            match = [row for row in rows if row['id'] == task_id]
            self.assertTrue(match, '新建任务必须出现在列表里（前端对账依赖它）')
            row = match[0]
            for field in ('id', 'source', 'identifier', 'created', 'project', 'state'):
                self.assertIn(field, row, f'对账字段缺少 {field}')
            self.assertEqual(row['source'], 'url')
            self.assertEqual(row['identifier'], 'https://example.com/a.h5ad')
        finally:
            publicdata.fetch = original
            final.unlink(missing_ok=True)


if __name__ == '__main__':
    unittest.main()
