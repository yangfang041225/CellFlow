"""数据登记安全：项目归属、公共数据不复活、预览统计、哈希标注、质量门禁。"""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import scipy.sparse as sp

from cellflow import api, workspace
from cellflow.data import Dataset
from cellflow.runner import _quality_report

ROOT = Path(__file__).resolve().parents[1]


class RegistrySafety(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = Path(self.folder.name)
        self._old = (workspace.STORE, workspace.PROJECTS_FILE, workspace.PROJECT_FILE,
                     workspace.DATASET_FILE, workspace.PUBLIC)
        workspace.STORE = self.store
        workspace.PROJECTS_FILE = self.store / 'projects.json'
        workspace.PROJECT_FILE = self.store / 'workspace.json'
        workspace.DATASET_FILE = self.store / 'datasets.json'
        workspace.PUBLIC = self.store / 'public'
        workspace.PUBLIC.mkdir()

    def tearDown(self):
        (workspace.STORE, workspace.PROJECTS_FILE, workspace.PROJECT_FILE,
         workspace.DATASET_FILE, workspace.PUBLIC) = self._old
        self.folder.cleanup()

    def test_unregister_one_project_keeps_other_association(self):
        source = self.store / 'data.h5ad'
        source.write_bytes(b'x')
        workspace.register_dataset(source)  # default
        other = workspace.create_project('B')
        workspace.register_dataset(source)  # B
        self.assertTrue(workspace.unregister_dataset(source, project=other['id']))
        default_rows = workspace.list_datasets(project='default')
        self.assertTrue(any(row['path'] == source for row in default_rows))
        b_rows = workspace.list_datasets(project=other['id'])
        self.assertFalse(any(row['path'] == source for row in b_rows))
        self.assertTrue(source.exists(), '移除登记不能删文件')

    def test_removed_public_file_does_not_resurrect(self):
        public_file = workspace.PUBLIC / 'geo_demo.h5ad'
        public_file.write_bytes(b'x')
        self.assertTrue(any(row['path'] == public_file for row in workspace.list_datasets()))
        self.assertTrue(workspace.unregister_dataset(public_file))
        self.assertFalse(any(row['path'] == public_file for row in workspace.list_datasets()),
                         '刷新后不应重新出现')
        # 再调用一次仍不可见（忽略列表持久化）
        self.assertFalse(any(row['path'] == public_file for row in workspace.list_datasets()))


class PreviewStats(unittest.TestCase):
    def test_sparse_stats_use_total_elements(self):
        data = Dataset(X=sp.csr_matrix(np.array([[0.0, 2.0], [0.0, 0.0]])),
                       cells=np.array(['c1', 'c2']), genes=np.array(['g1', 'g2']))
        response = api._dataset_preview(data)['stats']
        self.assertEqual(response['nnz'], 1)
        self.assertAlmostEqual(response['nonzero_pct'], 25.0)
        self.assertAlmostEqual(response['mean'], 0.5)
        self.assertAlmostEqual(response['min'], 0.0)
        self.assertAlmostEqual(response['max'], 2.0)
        self.assertTrue(response['sparse'])

    def test_dense_stats(self):
        data = Dataset(X=np.array([[0.0, 2.0], [0.0, 0.0]]),
                       cells=np.array(['c1', 'c2']), genes=np.array(['g1', 'g2']))
        stats = api._dataset_preview(data)['stats']
        self.assertFalse(stats['sparse'])
        self.assertAlmostEqual(stats['nonzero_pct'], 25.0)
        self.assertAlmostEqual(stats['mean'], 0.5)


class HashAndQuality(unittest.TestCase):
    def test_hash_kind_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'a.bin'
            path.write_bytes(b'x' * 1024)
            value, kind = api._digest(path)
            self.assertEqual(kind, 'sha256')
            self.assertEqual(len(value), 64)

    def test_quality_failure_blocks_completed(self):
        """硬质量失败必须让任务 failed，而不是 completed。"""
        import cellflow.runner as runner
        old = runner._quality_report
        runner._quality_report = lambda folder, result, data: {
            'checks': [{'check': 'forced', 'ok': False, 'detail': 'forced', 'soft': False}],
            'warnings': ['forced failure'], 'ok': False}
        try:
            with tempfile.TemporaryDirectory() as folder:
                output = Path(folder) / 'run'
                config = {'method': 'qc', 'seed': 1, 'n_top_genes': 60,
                          'n_neighbors': 10, 'n_pcs': 10, 'raw_x_confirmed': True}
                prov = runner.run(ROOT / 'examples' / 'synthetic.npz', config, output)
                self.assertEqual(prov['status'], 'failed')
                self.assertIn('quality', str(prov.get('failed_stage', '')))
                self.assertIn('结构校验失败', str(prov.get('error', '')))
        finally:
            runner._quality_report = old


if __name__ == '__main__':
    unittest.main()
