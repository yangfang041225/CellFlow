"""h5ad 往返测试：稀疏矩阵、层、obs 与模拟标记都要原样保留。"""
import importlib.util
import tempfile
import unittest
from pathlib import Path

import numpy as np
from scipy import sparse

from cellflow.data import load, make_demo, to_anndata, validate
from cellflow.runner import DEFAULTS


@unittest.skipUnless(importlib.util.find_spec('anndata'), 'AnnData not installed in this interpreter')
class H5adContract(unittest.TestCase):
    def test_sparse_h5ad_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = load(make_demo(root / 'source.npz'))
            a = to_anndata(data)
            a.X = sparse.csr_matrix(a.X)
            for key in a.layers:
                a.layers[key] = sparse.csr_matrix(a.layers[key])
            a.write_h5ad(root / 'input.h5ad')
            restored = load(root / 'input.h5ad')
            facts = validate(restored, {**DEFAULTS, 'raw_x_confirmed': True, 'raw_su_confirmed': True})
            self.assertFalse(facts['errors'])
            self.assertTrue(facts['velocity_counts'])
            self.assertTrue(facts['matrices']['X']['sparse'])
            self.assertTrue(restored.synthetic)
            self.assertTrue(np.array_equal(restored.cells, data.cells))
            self.assertEqual(sorted(restored.layers), ['spliced', 'unspliced'])
            self.assertIn('group', restored.obs)


if __name__ == '__main__':
    unittest.main()
