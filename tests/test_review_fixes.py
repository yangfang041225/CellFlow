"""独立审查（2026-10-25）后新增的回归测试：覆盖 P0/P1 修复点。"""
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / 'examples' / 'synthetic.npz'


class ReviewFixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cellflow import api
        cls.client = TestClient(api.app)
        from tests.fixtures import ensure_completed_run
        ensure_completed_run()
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_methods_route_exists(self):
        response = self.client.get('/api/methods')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(any(item['key'] == 'qc' for item in body['methods']))

    def test_auto_velovi_is_rejected(self):
        response = self.client.post('/api/runs', json={
            'path': str(DEMO), 'method': 'auto',
            'config': {'velocity_model': 'velovi', 'raw_x_confirmed': True, 'raw_su_confirmed': True},
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('veloVI', response.json()['detail'])

    def test_unknown_config_key_is_rejected(self):
        response = self.client.post('/api/runs', json={
            'path': str(DEMO), 'method': 'qc', 'config': {'seeeed': 1},
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('未知配置字段', response.json()['detail'])

    def test_zip_export_returns_zip(self):
        from cellflow.queue import list_jobs
        done = [job for job in list_jobs() if job.get('state') == 'completed']
        if not done:
            self.skipTest('没有已完成任务')
        response = self.client.get(f"/api/runs/{done[0]['id']}/zip")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'PK'))

    def test_path_whitelist_blocks_sibling_directory(self):
        target = ROOT / 'examples_audit_boundary' / 'synthetic.npz'
        target.parent.mkdir(exist_ok=True)
        try:
            with open(target, 'wb') as handle:
                handle.write(b'x')
            response = self.client.get('/api/datasets/inspect', params={'path': str(target)})
            self.assertEqual(response.status_code, 403)
        finally:
            target.unlink(missing_ok=True)
            target.parent.rmdir()

    def test_queue_deduplicates_active_runs(self):
        from cellflow.queue import connect, list_jobs, submit
        store = Path(self.tmp.name) / 'queue-dedupe'
        config = {'method': 'qc', 'seed': 42}
        first = submit(DEMO, config, store=store)
        second = submit(DEMO, config, store=store)
        self.assertEqual(first, second, '相同输入与配置的活动任务应当复用')
        with connect(store) as db:
            count = db.execute('SELECT COUNT(*) AS n FROM jobs').fetchone()['n']
        self.assertEqual(count, 1)
        # 完成后再次提交应创建新任务
        with connect(store) as db, db:
            db.execute("UPDATE jobs SET state='completed' WHERE id=?", (first,))
        third = submit(DEMO, config, store=store)
        self.assertNotEqual(first, third)

    def test_unconfirmed_counts_are_refused(self):
        from cellflow.adapters import run_adapter
        from cellflow.data import load
        from cellflow.runner import DEFAULTS
        data = load(DEMO)
        config = {**DEFAULTS, 'method': 'leiden', 'raw_x_confirmed': False,
                  'raw_su_confirmed': False, 'n_top_genes': 60}
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError) as ctx:
                run_adapter('leiden', data, config, Path(folder), lambda message: None)
            self.assertIn('原始计数', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
