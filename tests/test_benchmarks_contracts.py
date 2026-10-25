"""基准数据契约与下载完整性（不联网：本地 HTTP 服务器）。"""
import hashlib
import http.server
import socketserver
import tempfile
import threading
import unittest
from pathlib import Path

import anndata as ad
import numpy as np
import scipy.sparse as sp

from benchmarks import contracts, fetch, subset


class _Handler(http.server.BaseHTTPRequestHandler):
    payload = b''

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Length', str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *args):
        pass


class FetchIntegrity(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.server = socketserver.TCPServer(('127.0.0.1', 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}/data.bin'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.folder.cleanup()

    def test_download_verifies_sha_and_reuses(self):
        _Handler.payload = b'x' * 1000
        expected = hashlib.sha256(_Handler.payload).hexdigest()
        target = self.root / 'data.bin'
        result = fetch.download(self.url, target, expected_sha256=expected,
                                expected_bytes=1000, retries=2)
        self.assertEqual(result['sha256'], expected)
        self.assertFalse(result['reused'])
        again = fetch.download(self.url, target, expected_sha256=expected,
                               expected_bytes=1000, retries=2)
        self.assertTrue(again['reused'])

    def test_hash_mismatch_is_rejected(self):
        _Handler.payload = b'y' * 100
        with self.assertRaises(ValueError):
            fetch.download(self.url, self.root / 'bad.bin',
                           expected_sha256='0' * 64, expected_bytes=100, retries=1)
        self.assertFalse((self.root / 'bad.bin').exists())
        self.assertTrue((self.root / 'bad.bin.part').exists())


class ContractAndSubset(unittest.TestCase):
    def _adata(self, n=30, genes=20):
        rng = np.random.default_rng(0)
        x = sp.csr_matrix(rng.poisson(1.0, size=(n, genes)).astype(np.float32))
        a = ad.AnnData(X=x)
        a.layers['spliced'] = x.copy()
        a.layers['unspliced'] = x.copy() * 0
        a.obs['clusters'] = ['A'] * (n // 2) + ['B'] * (n - n // 2)
        return a

    def test_contract_flags_missing_splicing(self):
        a = self._adata()
        del a.layers['spliced']
        report = contracts.validate_adata(a, require_splicing=True)
        self.assertFalse(report['ok'])
        self.assertTrue(any('spliced' in issue for issue in report['issues']))

    def test_contract_accepts_clean_counts(self):
        report = contracts.validate_adata(self._adata(), require_splicing=True,
                                          expected_shape=(30, 20))
        self.assertTrue(report['ok'], report['issues'])
        self.assertIn('spliced', report['matrices'])

    def test_subset_is_deterministic_and_preserves_layers(self):
        a = self._adata(40, 15)
        first = subset.stratified_subset(a, per_group=5, seed=42)
        second = subset.stratified_subset(a, per_group=5, seed=42)
        self.assertEqual(list(first.obs_names), list(second.obs_names))
        self.assertLessEqual(first.n_obs, 10)
        self.assertTrue({'spliced', 'unspliced'} <= set(first.layers))
        np.testing.assert_array_equal(first.layers['spliced'].toarray(),
                                      a[first.obs_names].layers['spliced'].toarray())


if __name__ == '__main__':
    unittest.main()
