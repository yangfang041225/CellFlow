"""本机持久队列：单 worker、去重、孤儿恢复。

改动（审查 F07/F08/F13）：
- 提交时按「输入内容 + 方法 + 配置」算指纹；已有排队/运行中的同指纹任务直接复用，避免重复计算；
- 领取任务时记录 worker PID；启动时检查 running 任务的 PID 是否还活着，失联标记为 failed/interrupted，不再永久堵塞；
- 记录原始数据集路径，便于界面按数据集筛选任务。
"""
from contextlib import closing
from pathlib import Path
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid

from .runner import normalize_config

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / 'results'


def connect(store=STORE):
    store = Path(store)
    store.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(store / 'queue.sqlite', timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, method TEXT, state TEXT, '
               'submitted REAL, updated REAL, input TEXT, config TEXT, output TEXT, runtime TEXT, error TEXT)')
    columns = {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
    for name, ddl in (('fingerprint', 'TEXT'), ('pid', 'INTEGER'), ('dataset', 'TEXT'),
                      ('name', 'TEXT'), ('cancel_requested', 'INTEGER'),
                      ('project_id', 'TEXT')):
        if name not in columns:
            db.execute(f'ALTER TABLE jobs ADD COLUMN {name} {ddl}')
    db.execute('CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_jobs_fp ON jobs(fingerprint)')
    db.commit()
    return db


def fingerprint(path, config):
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256((digest + '|' + payload).encode('utf-8')).hexdigest()


def find_active(store, fp):
    with closing(connect(store)) as db:
        row = db.execute("SELECT id FROM jobs WHERE fingerprint=? AND state IN ('queued','running') "
                         "ORDER BY submitted LIMIT 1", (fp,)).fetchone()
        return row['id'] if row else None


def _display_name(method, dataset_path):
    from .registry import INDEX
    label = INDEX[method].name if method in INDEX else method
    stem = Path(dataset_path).stem
    if len(stem) >= 16 and all(ch in '0123456789abcdef' for ch in stem[:16]):
        stem = '导入数据'
    return f'{label} · {stem} · {time.strftime("%Y-%m-%d %H:%M")}'


def submit(path, config, store=STORE, dataset=None, name=None, project=None):
    c = normalize_config(config)
    if project is None:
        try:
            from .workspace import active_project
            project = active_project()["id"]
        except Exception:
            project = "default"
    identity = uuid.uuid4().hex[:16]
    store = Path(store).resolve()
    inputs = store / 'inputs'
    inputs.mkdir(parents=True, exist_ok=True)
    source = Path(path)
    snapshot = inputs / (identity + source.suffix.lower())
    shutil.copyfile(source, snapshot)
    from .registry import env_python
    runtime = env_python(c['method'], root=ROOT) or Path(sys.executable)
    output = store / identity
    fp = fingerprint(snapshot, c)
    with closing(connect(store)) as db, db:
        existing = db.execute("SELECT id FROM jobs WHERE fingerprint=? AND state IN ('queued','running') "
                              "ORDER BY submitted LIMIT 1", (fp,)).fetchone()
        if existing:
            return existing['id']
        dataset_path = str(Path(dataset or path).resolve())
        display = name or _display_name(c['method'], dataset_path)
        db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (identity, c['method'], 'queued', time.time(), time.time(), str(snapshot),
                    json.dumps(c), str(output), str(runtime), '', fp, None,
                    dataset_path, display, 0, project or 'default'))
    return identity


def list_jobs(store=STORE, state=None, method=None, dataset=None, query=None, limit=None,
              project=None):
    """服务端筛选：状态 / 方法 / 数据集 / 名称或 ID 关键词。"""
    clauses, values = [], []
    if state:
        clauses.append('state = ?')
        values.append(state)
    if method:
        clauses.append('method = ?')
        values.append(method)
    if dataset:
        clauses.append('dataset = ?')
        values.append(str(Path(dataset).resolve()))
    if project and project != 'all':
        clauses.append("COALESCE(project_id, 'default') = ?")
        values.append(project)
    if query:
        clauses.append('(id LIKE ? OR name LIKE ? OR method LIKE ?)')
        like = f'%{query}%'
        values.extend([like, like, like])
    where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
    sql = f'SELECT * FROM jobs {where} ORDER BY submitted DESC'
    if limit:
        sql += f' LIMIT {int(limit)}'
    with closing(connect(store)) as db:
        return [dict(r) for r in db.execute(sql, values)]


def _pid_alive(pid):
    if not pid:
        return False
    if os.name == 'nt':
        import ctypes
        process = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not process:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(process, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(process)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def cancel(job_id, store=STORE):
    """取消任务：排队中的直接取消；运行中的终止其 worker 进程。"""
    with closing(connect(store)) as db:
        row = db.execute('SELECT state, pid FROM jobs WHERE id=?', (job_id,)).fetchone()
    if row is None:
        return False, '任务不存在'
    if row['state'] not in ('queued', 'running'):
        return False, f'任务当前状态为 {row["state"]}，不能取消'
    pid = row['pid']
    if row['state'] == 'running' and pid and _pid_alive(pid):
        if os.name == 'nt':
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, int(pid))
            if handle:
                ctypes.windll.kernel32.TerminateProcess(handle, 1)
                ctypes.windll.kernel32.CloseHandle(handle)
        else:
            import signal
            os.kill(int(pid), signal.SIGTERM)
    with closing(connect(store)) as db, db:
        db.execute("UPDATE jobs SET state='cancelled', cancel_requested=1, updated=?, "
                   "error=? WHERE id=?",
                   (time.time(), '用户取消', job_id))
    return True, '已取消'


def recover_orphans(store=STORE):
    """把失联的 running 任务标为失败，返回被回收的 id 列表。"""
    recovered = []
    with closing(connect(store)) as db, db:
        rows = db.execute("SELECT id, pid, updated FROM jobs WHERE state='running'").fetchall()
        for row in rows:
            alive = _pid_alive(row['pid']) if row['pid'] else False
            stale = (time.time() - (row['updated'] or 0)) > 24 * 3600
            if alive and not stale:
                continue
            db.execute("UPDATE jobs SET state='failed', updated=?, error=? WHERE id=?",
                       (time.time(), 'worker 进程已不存在（interrupted）；任务未完成，日志保留',
                        row['id']))
            recovered.append(row['id'])
    return recovered


def start_worker(store=STORE):
    store = Path(store).resolve()
    store.mkdir(exist_ok=True, parents=True)
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
    with (store / 'worker.log').open('a', encoding='utf-8') as handle:
        subprocess.Popen([sys.executable, '-m', 'cellflow.queue', '--store', str(store)],
                         cwd=ROOT, stdin=subprocess.DEVNULL, stdout=handle, stderr=handle, **kwargs)


def drain(store=STORE):
    store = Path(store).resolve()
    recover_orphans(store)
    while True:
        with closing(connect(store)) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM jobs WHERE state='queued' AND COALESCE(cancel_requested,0)=0 "
                             "ORDER BY submitted LIMIT 1").fetchone()
            if not row:
                db.rollback()
                return
            job = dict(row)
            db.execute("UPDATE jobs SET state='running', updated=?, pid=? WHERE id=?",
                       (time.time(), os.getpid(), job['id']))
            db.commit()

        config_path = store / (job['id'] + '.json')
        config_path.write_text(job['config'], encoding='utf-8')
        env = dict(os.environ, PYTHONHASHSEED=str(json.loads(job['config'])['seed']),
                   OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
                   MKL_NUM_THREADS='1', NUMBA_NUM_THREADS='1')
        state, error = 'completed', ''
        try:
            with (store / (job['id'] + '.worker.log')).open('w', encoding='utf-8') as log:
                proc = subprocess.run([job['runtime'], '-m', 'cellflow.cli', 'run',
                                       '--input', job['input'], '--config', str(config_path),
                                       '--output', job['output']],
                                      cwd=ROOT, env=env, stdout=log, stderr=log,
                                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if proc.returncode != 0:
                    state = 'failed'
                    error = '执行失败；查看结果目录中的 run.log 或任务 worker.log'
        except Exception as exc:
            state = 'failed'
            error = f'{type(exc).__name__}: {exc}'
        with closing(connect(store)) as db, db:
            current = db.execute('SELECT state FROM jobs WHERE id=?', (job['id'],)).fetchone()
            if current and current['state'] == 'cancelled':
                db.execute('UPDATE jobs SET updated=?, pid=NULL WHERE id=?',
                           (time.time(), job['id']))
            else:
                db.execute('UPDATE jobs SET state=?, updated=?, error=?, pid=NULL WHERE id=?',
                           (state, time.time(), error, job['id']))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--store', type=Path, default=STORE)
    drain(parser.parse_args().store)

def rename(job_id, name, store=STORE):
    """修改任务显示名称（不影响执行与产物）。"""
    name = (name or '').strip()
    if not name:
        return False, '名称不能为空'
    with closing(connect(store)) as db, db:
        row = db.execute('SELECT id FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            return False, '任务不存在'
        db.execute('UPDATE jobs SET name=?, updated=? WHERE id=?', (name, time.time(), job_id))
    return True, '已重命名'


def delete(job_id, store=STORE):
    """删除任务记录；运行中的任务不允许删除。结果目录默认保留。"""
    with closing(connect(store)) as db, db:
        row = db.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            return False, '任务不存在'
        if row['state'] in ('queued', 'running'):
            return False, '任务仍在排队或运行中，请先取消'
        db.execute('DELETE FROM jobs WHERE id=?', (job_id,))
    return True, '已删除任务记录（结果文件保留）'
