"""内存预检：是否可能装不下，而不是精确预测。"""
import tempfile
import unittest
from pathlib import Path

from cellflow import memory

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / 'examples' / 'synthetic.npz'


class Memory(unittest.TestCase):
    def test_estimate_demo(self):
        info = memory.estimate(DEMO)
        self.assertGreater(info['bytes'], 0)
        self.assertIn('method', info)
        self.assertGreaterEqual(info['ratio'], 0)
        self.assertGreater(info['available_gb'], 0)

    def test_estimate_sparse_h5ad_shape(self):
        import anndata as ad
        import numpy as np
        import scipy.sparse as sp
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'toy.h5ad'
            matrix = sp.csr_matrix(np.ones((30, 10)))
            adata = ad.AnnData(X=matrix)
            adata.write_h5ad(path)
            info = memory.estimate(path)
            self.assertIn('sparse', info['method'])
            self.assertIn('30×10', info['method'])

    def test_precheck_refuses_when_over_limit(self):
        reason, info = memory.precheck(DEMO, limit=-1.0)
        self.assertIsNotNone(reason)
        self.assertIn('已拒绝加载', reason)
        self.assertIn('GB', reason)

    def test_require_stops_when_memory_is_low(self):
        from unittest import mock
        with mock.patch('cellflow.memory.available_bytes', return_value=int(0.2 * 1024 ** 3)):
            with self.assertRaises(MemoryError) as ctx:
                memory.require(1.0)
            self.assertIn('可用内存', str(ctx.exception))
            self.assertIn('已停止', str(ctx.exception))
            self.assertAlmostEqual(memory.require(0.1), 0.2, places=2)

    def test_require_passes_with_enough_memory(self):
        from unittest import mock
        with mock.patch('cellflow.memory.available_bytes', return_value=8 * 1024 ** 3):
            self.assertAlmostEqual(memory.require(1.0), 8.0, places=2)

    def test_api_rejects_when_memory_check_fails(self):
        from unittest import mock
        from fastapi.testclient import TestClient
        from cellflow import api

        client = TestClient(api.app)
        datasets = client.get('/api/meta').json()['datasets']
        demo = next(item for item in datasets if item['source'] == '示例')
        with mock.patch('cellflow.memory.precheck',
                        return_value=('预计需要 99 GB 内存，已拒绝加载。', {})):
            response = client.get('/api/datasets/inspect', params={'path': demo['path']})
        self.assertEqual(response.status_code, 413)
        self.assertIn('拒绝加载', response.json()['detail'])


class H5LayerEstimate(unittest.TestCase):
    def test_layers_increase_estimate(self):
        import tempfile
        from pathlib import Path
        import anndata as ad
        import numpy as np
        import scipy.sparse as sp
        from cellflow.memory import estimate

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            x = sp.random(40, 30, density=0.1, format='csr', dtype=np.float32)
            plain = ad.AnnData(X=x.copy())
            plain.write_h5ad(root / 'plain.h5ad')
            layered = ad.AnnData(X=x.copy())
            layered.layers['spliced'] = x.copy()
            layered.layers['unspliced'] = x.copy()
            layered.write_h5ad(root / 'layered.h5ad')
            plain_info = estimate(root / 'plain.h5ad')
            layered_info = estimate(root / 'layered.h5ad')
        self.assertGreater(layered_info['bytes'], plain_info['bytes'])
        self.assertIn('层', layered_info['method'])


if __name__ == '__main__':
    unittest.main()
