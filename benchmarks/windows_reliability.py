"""Windows 可靠性探针：连续任务、损坏输入、队列终态、磁盘门禁接口。

可自动验证的部分写入 JSON；强制关窗、系统级磁盘写满等需要真机操作的项，
在 `docs/scientific_validation/phase2_windows.md` 中单独标注证据来源。
"""
from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / 'examples' / 'synthetic.npz'


def _wait(jobs, store, ids, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = {row['id']: row for row in jobs(store=store)}
        if all(rid in rows and rows[rid]['state'] in ('completed', 'failed', 'cancelled')
               for rid in ids):
            return rows
        time.sleep(0.3)
    return {row['id']: row for row in jobs(store=store)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'data' / 'benchmarks'
                        / 'windows_reliability.json')
    args = parser.parse_args()
    from cellflow import queue

    evidence = {'started_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'checks': []}
    with tempfile.TemporaryDirectory(prefix='cellflow-winrel-') as folder:
        store = Path(folder) / 'store'
        config = {'method': 'qc', 'seed': 1, 'n_top_genes': 60, 'n_neighbors': 10,
                  'n_pcs': 10, 'raw_x_confirmed': True}
        first = queue.submit(DEMO, config, store=store)
        second = queue.submit(DEMO, dict(config, seed=2), store=store)
        queue.start_worker(store=store)
        rows = _wait(queue.list_jobs, store, [first, second])
        states = [rows.get(first, {}).get('state'), rows.get(second, {}).get('state')]
        evidence['checks'].append({
            'name': 'two_sequential_jobs', 'ok': states == ['completed', 'completed'],
            'states': states,
        })
        running = queue.list_jobs(store=store, state='running')
        evidence['checks'].append({
            'name': 'no_running_after_completion', 'ok': not running,
            'running': len(running),
        })
        # 损坏输入：必须 failed，且错误可读
        bad = Path(folder) / 'bad.h5ad'
        bad.write_bytes(b'not-an-hdf5-file')
        bad_job = queue.submit(bad, config, store=store)
        queue.start_worker(store=store)
        rows = _wait(queue.list_jobs, store, [bad_job])
        row = rows.get(bad_job, {})
        evidence['checks'].append({
            'name': 'corrupt_input_fails_cleanly', 'ok': row.get('state') == 'failed',
            'state': row.get('state'), 'has_error': bool(row.get('error')),
            'error_head': (row.get('error') or '')[:200],
        })
        # 终态唯一：同一任务不会同时 running/completed
        states_all = [item['state'] for item in queue.list_jobs(store=store)]
        evidence['checks'].append({
            'name': 'terminal_states_are_single', 'ok': states_all.count('running') == 0,
            'states': states_all,
        })
    evidence['ok'] = all(item['ok'] for item in evidence['checks'])
    evidence['note'] = ('只覆盖可在进程内控制的场景；强制关窗、系统磁盘写满、'
                        '管理员权限/多屏 DPI 需真机记录。')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'ok': evidence['ok'],
                      'checks': [item['name'] for item in evidence['checks']]}, ensure_ascii=False))
    return 0 if evidence['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
