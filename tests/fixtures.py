"""测试夹具：不依赖历史结果，按需生成一个真实完成的 QC 任务。"""
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / 'examples' / 'synthetic.npz'
_CACHE = {}


def ensure_completed_run(timeout=180):
    if _CACHE.get('job'):
        return _CACHE['job']
    from cellflow import queue
    completed = queue.list_jobs(state='completed')
    if completed:
        _CACHE['job'] = completed[0]
        return _CACHE['job']
    job_id = queue.submit(DEMO, {'method': 'qc', 'seed': 1, 'n_top_genes': 60,
                                 'n_neighbors': 10, 'n_pcs': 10,
                                 'raw_x_confirmed': True})
    queue.start_worker()
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = [item for item in queue.list_jobs() if item['id'] == job_id]
        if rows and rows[0]['state'] in ('completed', 'failed', 'cancelled'):
            _CACHE['job'] = rows[0]
            return rows[0]
        time.sleep(0.3)
    raise RuntimeError('夹具任务没有在超时内完成')


def ensure_second_run():
    from cellflow import queue
    rows = queue.list_jobs(limit=2)
    if len(rows) >= 2:
        return rows[1]
    job_id = queue.submit(DEMO, {'method': 'qc', 'seed': 2, 'n_top_genes': 60,
                                 'n_neighbors': 10, 'n_pcs': 10,
                                 'raw_x_confirmed': True})
    queue.start_worker()
    deadline = time.time() + 180
    while time.time() < deadline:
        rows = [item for item in queue.list_jobs() if item['id'] == job_id]
        if rows and rows[0]['state'] in ('completed', 'failed', 'cancelled'):
            return rows[0]
        time.sleep(0.3)
    raise RuntimeError('第二个夹具任务没有在超时内完成')
