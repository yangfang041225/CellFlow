"""真实 worker 链路：提交 → 后台 worker → CLI → 结果/报告/质量。"""
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from cellflow import queue

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / 'examples' / 'synthetic.npz'


class Worker(unittest.TestCase):
    def test_worker_completes_qc_job(self):
        folder = Path(tempfile.mkdtemp(prefix='cellflow-worker-'))
        # worker 进程可能在测试结束后才释放 worker.log；清理失败不应让测试失败
        self.addCleanup(lambda: shutil.rmtree(folder, ignore_errors=True))
        store = folder / 'results'
        job_id = queue.submit(DEMO, {'method': 'qc', 'seed': 1, 'n_top_genes': 60,
                                     'n_neighbors': 10, 'n_pcs': 10,
                                     'raw_x_confirmed': True}, store=store)
        queue.start_worker(store=store)
        deadline = time.time() + 180
        job = None
        while time.time() < deadline:
            rows = [item for item in queue.list_jobs(store=store) if item['id'] == job_id]
            job = rows[0] if rows else None
            if job and job['state'] in ('completed', 'failed', 'cancelled'):
                break
            time.sleep(0.25)
        self.assertIsNotNone(job, 'worker 没有处理任务')
        self.assertEqual(job['state'], 'completed',
                         (job.get('error'), (store / 'worker.log').read_text(encoding='utf-8')[-1500:]))
        output = Path(job['output'])
        self.assertTrue((output / 'result.json').is_file())
        self.assertTrue((output / 'A_生物学分析报告.md').is_file())
        quality = json.loads((output / 'quality.json').read_text(encoding='utf-8'))
        self.assertTrue(quality['ok'], quality.get('warnings'))
        provenance = json.loads((output / 'provenance.json').read_text(encoding='utf-8'))
        self.assertEqual(provenance['last_stage'], 'reports')


if __name__ == '__main__':
    unittest.main()
