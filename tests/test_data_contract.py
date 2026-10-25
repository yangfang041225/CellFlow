"""数据契约：缺失值/重复名显式记录，AnnData 轴不被猜测，缺失分组不成为类别。"""
import tempfile
import unittest
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
from scipy import sparse

from cellflow import adapters, importers


class DataContract(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def test_csv_missing_values_and_duplicate_genes_are_recorded(self):
        path = self.root / 'messy.csv'
        path.write_text('cell,g1,g1,g2,g3,g4,g5\n'
                        'c1,1,,2,3,4,5\n'
                        'c2,0,1,2,3,4,5\n', encoding='utf-8')
        result = importers.import_any(path)
        extras = result.dataset.extras['import']
        self.assertGreaterEqual(extras['missing_values_filled'], 1)
        self.assertGreaterEqual(extras['duplicate_header_names'], 1)
        notes = ' '.join(result.notes)
        self.assertIn('缺失', notes)
        self.assertIn('重复列名', notes)

    def test_metadata_missing_kept_as_empty_not_string(self):
        path = self.root / 'meta.csv'
        path.write_text('cell,group,g1,g2,g3,g4,g5\n'
                        'c1,,1,1,1,1,1\n'
                        'c2,B,2,2,2,2,2\n', encoding='utf-8')
        result = importers.import_any(path)
        values = list(result.dataset.obs['group'])
        self.assertIn(None, values)
        self.assertNotIn('None', values)
        self.assertNotIn('nan', values)

    def test_missing_group_is_rejected_not_turned_into_cluster(self):
        a = ad.AnnData(X=sparse.csr_matrix(np.ones((3, 5), dtype=np.float32)))
        a.obs_names = ['c1', 'c2', 'c3']
        a.var_names = [f'g{i}' for i in range(5)]
        a.obs['group'] = pd.Series(['A', None, 'B'], index=a.obs_names)
        with self.assertRaises(ValueError):
            adapters._group_labels(a, {'group_key': 'group'}, lambda *args, **kwargs: None,
                                   lambda *args, **kwargs: None)

    def test_h5ad_axes_are_authoritative(self):
        matrix = sparse.random(100, 6000, density=0.01, format='csr', dtype=np.float32)
        a = ad.AnnData(X=matrix)
        a.obs_names = [f'cell{i}' for i in range(100)]
        a.var_names = [f'g{i}' for i in range(6000)]
        path = self.root / 'big.h5ad'
        a.write_h5ad(path)
        result = importers.import_any(path)
        self.assertFalse(result.transposed)
        self.assertEqual(result.dataset.X.shape, (100, 6000))
        self.assertEqual(len(result.dataset.cells), 100)
        self.assertEqual(len(result.dataset.genes), 6000)


if __name__ == '__main__':
    unittest.main()
