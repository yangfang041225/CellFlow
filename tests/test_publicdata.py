"""公开数据下载（publicdata）测试。

离线部分：用本地 HTTP 服务器 + 自己造的 10x 三件套，验证下载、解压、转换、元数据标准化。
联网部分：只做一次 GEO 目录列举；网络不可用时自动跳过，不算失败。
"""
import gzip
import http.server
import io
import shutil
import socket
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np
from scipy import sparse
from scipy.io import mmread, mmwrite

from cellflow import publicdata


def _make_10x_dir(root: Path, n_cells=6, n_genes=8) -> Path:
    """造一个最小的 10x 三件套目录（genes × cells 的 mtx + barcodes + features）。"""
    folder = root / 'filtered_feature_bc_matrix'
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    mat = sparse.coo_matrix(rng.poisson(3, size=(n_genes, n_cells)).astype(float))
    mmwrite(folder / 'matrix.mtx', mat)
    (folder / 'barcodes.tsv').write_text('\n'.join(f'CELL{i:03d}-1' for i in range(n_cells)) + '\n',
                                         encoding='utf-8')
    features = [f'ENSG{i:011d}\tGENE{i}\tGene Expression' for i in range(n_genes)]
    (folder / 'features.tsv').write_text('\n'.join(features) + '\n', encoding='utf-8')
    return folder


def _tar_folder(folder: Path, target: Path) -> Path:
    import tarfile
    with tarfile.open(target, 'w:gz') as tf:
        tf.add(folder, arcname=folder.name)
    return target


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # 静音
        pass


class PublicDataOffline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.tenx = _make_10x_dir(cls.root)
        # 本地 HTTP 服务：用于验证 download_file 的真实下载路径
        cls.serve_dir = cls.root / 'serve'
        cls.serve_dir.mkdir()
        _tar_folder(cls.tenx, cls.serve_dir / 'sample_RAW.tar.gz')
        handler = lambda *a, **k: _Handler(*a, directory=str(cls.serve_dir), **k)  # noqa: E731
        cls.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.tmp.cleanup()

    # ------------------------------------------------------------ URL 规则
    def test_geo_url_layout(self):
        self.assertEqual(publicdata._geo_stub('GSE84133'), 'GSE84nnn')
        self.assertEqual(publicdata._geo_stub('GSE999'), 'GSEnnn')
        self.assertEqual(publicdata._geo_stub('GSE1234'), 'GSE1nnn')
        url = publicdata.geo_series_url('gse84133')
        self.assertIn('/GSE84nnn/GSE84133/suppl/', url)
        self.assertIn('GSE84133_family.soft.gz', publicdata.geo_soft_url('GSE84133'))
        with self.assertRaises(publicdata.PublicDataError):
            publicdata._geo_stub('ABCDE')

    def test_cellxgene_identifier_validation(self):
        self.assertTrue(publicdata._is_uuid('0d5f2f4a-1111-2222-3333-444455556666'))
        self.assertFalse(publicdata._is_uuid('not-a-uuid'))
        with self.assertRaises(publicdata.PublicDataError):
            publicdata.cellxgene_fetch('not-a-uuid', self.root / 'out')
        with self.assertRaises(publicdata.PublicDataError):
            publicdata.fetch('unknown-source', 'x', self.root / 'out')

    # ------------------------------------------------------------ 下载与转换
    def test_download_file_over_http(self):
        url = f'http://127.0.0.1:{self.port}/sample_RAW.tar.gz'
        target = self.root / 'downloaded.tar.gz'
        seen = []
        out = publicdata.download_file(url, target, progress=lambda done, total: seen.append(done))
        self.assertTrue(out.exists())
        self.assertGreater(out.stat().st_size, 100)
        self.assertTrue(seen, '进度回调应当被调用')
        # 已存在时不应重复下载（直接返回）
        again = publicdata.download_file(url, target)
        self.assertEqual(again, target)

    def test_extract_and_convert_10x_archive(self):
        archive = self.serve_dir / 'sample_RAW.tar.gz'
        target = self.root / 'extracted'
        publicdata._extract_archive(archive, target)
        found = list(target.rglob('matrix.mtx'))
        self.assertTrue(found, '解压后应能找到 matrix.mtx')
        expected = float(mmread(found[0]).sum())
        adata = publicdata._load_any_matrix(target)
        self.assertIsNotNone(adata, '10x 三件套应当能转成 AnnData')
        self.assertEqual(adata.shape, (6, 8))  # cells × genes
        self.assertAlmostEqual(float(adata.X.sum()), expected, places=6)
        self.assertEqual(adata.var_names[0], 'GENE0')
        self.assertEqual(adata.obs_names[0], 'CELL000-1')

    def test_standardize_fills_required_obs(self):
        adata = publicdata._load_any_matrix(self.root)
        std = publicdata.standardize(adata, 'geo', 'GSE000001', sample_id='GSM1', species='human')
        for key in publicdata.OBS_REQUIRED:
            self.assertIn(key, std.obs, key)
        self.assertEqual(str(std.obs['source'].iloc[0]), 'geo')
        self.assertEqual(str(std.obs['dataset_id'].iloc[0]), 'GSE000001')
        self.assertEqual(str(std.obs['sample_id'].iloc[0]), 'GSM1')
        self.assertEqual(str(std.obs['species'].iloc[0]), 'human')
        self.assertEqual(std.uns['cellflow_public']['dataset_id'], 'GSE000001')
        # 原始对象不应被修改
        self.assertNotIn('dataset_id', adata.obs)

    def test_species_guess_from_var_names(self):
        self.assertEqual(publicdata._species_from_var(['ENSMUSG00000000001']), 'mouse')
        self.assertEqual(publicdata._species_from_var(['ENSG00000141510']), 'human')
        self.assertEqual(publicdata._species_from_var(['CD3D', 'MT-CO1']), 'unknown')


class PublicDataNetwork(unittest.TestCase):
    """联网测试：网络不可用时跳过。"""

    def test_geo_listing(self):
        try:
            files = publicdata.list_geo_files('GSE84133')
        except Exception as exc:
            self.skipTest('本机无法访问 NCBI：' + type(exc).__name__)
        self.assertTrue(files, 'GSE84133 应当有可下载的补充文件')
        self.assertTrue(any(f.endswith(('.tar', '.tar.gz', '.h5', '.h5ad')) for f in files))


if __name__ == '__main__':
    unittest.main()
