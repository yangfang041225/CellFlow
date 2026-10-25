"""API 规模：服务端分页、单行查询、日志尾读。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from cellflow import api


class Pagination(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.fixtures import ensure_completed_run, ensure_second_run
        ensure_completed_run()
        ensure_second_run()
        cls.client = TestClient(api.app)

    def test_runs_are_paginated_with_total(self):
        first = self.client.get('/api/runs?limit=2&offset=0').json()
        second = self.client.get('/api/runs?limit=2&offset=2').json()
        self.assertEqual(len(first['runs']), 2)
        self.assertGreaterEqual(first['total'], 2)
        self.assertEqual(first['limit'], 2)
        self.assertEqual(first['offset'], 0)
        self.assertEqual(second['offset'], 2)
        if first['total'] > 2:
            self.assertTrue(first['has_more'])
            self.assertNotEqual(first['runs'][0]['id'], second['runs'][0]['id'])

    def test_limit_is_capped(self):
        body = self.client.get('/api/runs?limit=9999').json()
        self.assertLessEqual(body['limit'], 200)
        self.assertLessEqual(len(body['runs']), 200)

    def test_meta_counts_are_project_scoped_aggregate(self):
        body = self.client.get('/api/meta').json()
        counts = body['counts']
        for key in ('queued', 'running', 'completed', 'failed'):
            self.assertIn(key, counts)
        runs = self.client.get('/api/runs?limit=1').json()
        self.assertGreaterEqual(runs['total'], counts.get('completed', 0))

    def test_log_reads_tail_not_whole_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'run.log').write_text('x' * (256 * 1024) + 'TAIL-MARKER', encoding='utf-8')
            fake = {'id': 'fake', 'output': str(root)}
            with mock.patch.object(api, '_job', return_value=fake):
                body = self.client.get('/api/logs/fake').json()
            self.assertTrue(body['truncated'])
            self.assertLessEqual(len(body['log']), 20000)
            self.assertIn('TAIL-MARKER', body['log'])

    def test_run_detail_compacts_heavy_arrays(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'result.json').write_text(json.dumps({
                'metrics': {'n_groups': 3},
                'embedding': [[0.0, 1.0], [1.0, 2.0]],
                'velocity': [[0.1, 0.2]],
                'gene_table': [{'gene': 'g1'}],
            }), encoding='utf-8')
            fake = {'id': 'fake', 'output': str(root), 'method': 'qc', 'state': 'completed'}
            with mock.patch.object(api, '_job', return_value=fake):
                compact = self.client.get('/api/runs/fake').json()
                full = self.client.get('/api/runs/fake?compact=0').json()
        self.assertEqual(compact['result']['metrics']['n_groups'], 3)
        self.assertNotIn('embedding', compact['result'])
        self.assertIn('_omitted', compact['result'])
        self.assertIn('embedding', full['result'])


if __name__ == '__main__':
    unittest.main()