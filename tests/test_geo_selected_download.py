"""P3-01 单文件下载的集成测试（本地 HTTP 服务，不联网）。

核心断言：geo_fetch_selected 只下载**传入的那一个文件**，
不会像 geo_fetch 那样遍历整个 Series。
"""
import http.server
import json
import os
import tempfile
import threading
import unittest
from functools import partial
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
import scipy.sparse as sp

from cellflow import paths, publicdata


class _CountingHandler(http.server.SimpleHTTPRequestHandler):
    """记录被请求过的路径，用于证明「只下载了选中的文件」。"""

    hits = []

    def do_GET(self):  # noqa: N802
        _CountingHandler.hits.append(self.path)
        return super().do_GET()

    def log_message(self, *args):
        pass


def _make_h5ad(path: Path, cells: int = 40, genes: int = 12, tag: str = 'A'):
    names = [f'{tag}_cell{i}' for i in range(cells)]
    genes_names = [f'GENE{i}' for i in range(genes)]
    rng = np.random.default_rng(7)
    matrix = sp.csr_matrix(rng.integers(0, 5, size=(cells, genes)).astype('float32'))
    adata = ad.AnnData(X=matrix, obs=pd.DataFrame(index=names),
                       var=pd.DataFrame(index=genes_names))
    adata.write_h5ad(path)


class SelectedDownload(unittest.TestCase):
    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        self.base = Path(self._folder.name)
        self.served = self.base / 'served'
        self.served.mkdir()
        self.out = self.base / 'out'
        self.out.mkdir()
        _make_h5ad(self.served / 'wanted.h5ad', tag='W')
        _make_h5ad(self.served / 'unwanted.h5ad', tag='U')
        (self.served / 'huge_archive.tar.gz').write_bytes(b'\x1f\x8b' + b'0' * 64)

        _CountingHandler.hits = []
        handler = partial(_CountingHandler, directory=str(self.served))
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

        self._env = {
            paths.DATA_ROOT_ENV: str(self.base / 'data-root'),
            paths.SETTINGS_ENV: str(self.base / 'settings.json'),
        }
        self._saved = {k: os.environ.get(k) for k in self._env}
        os.environ.update(self._env)
        # 不联网：把 Series SOFT 拿掉（真实实现里失败会被吞掉，这里直接给空）
        self._orig_soft = publicdata._series_soft
        publicdata._series_soft = lambda gse, workdir: {}

    def tearDown(self):
        publicdata._series_soft = self._orig_soft
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.server.shutdown()
        self.server.server_close()
        self._folder.cleanup()

    def url(self, name):
        return f'http://127.0.0.1:{self.port}/{name}'

    def test_only_selected_file_is_downloaded(self):
        result = publicdata.geo_fetch_selected(
            'GSE99999', [self.url('wanted.h5ad')], self.out, verify_sizes=False)
        self.assertEqual(len(result), 1)
        self.assertTrue(Path(result[0]).is_file())
        self.assertEqual(_CountingHandler.hits, ['/wanted.h5ad'],
                         '只能请求用户选中的那一个文件')
        self.assertFalse(any('unwanted' in hit for hit in _CountingHandler.hits))

    def test_output_name_reflects_selected_file(self):
        result = publicdata.geo_fetch_selected(
            'GSE99999', [self.url('wanted.h5ad')], self.out, verify_sizes=False)
        self.assertEqual(Path(result[0]).name, 'geo_GSE99999_wanted.h5ad')

    def test_empty_selection_is_rejected(self):
        with self.assertRaises(publicdata.PublicDataError) as ctx:
            publicdata.geo_fetch_selected('GSE99999', [], self.out)
        self.assertIn('没有选择', str(ctx.exception))

    def test_bad_accession_is_rejected(self):
        with self.assertRaises(publicdata.PublicDataError):
            publicdata.geo_fetch_selected('GSM123', [self.url('wanted.h5ad')], self.out)

    def test_workdir_is_cleaned_and_part_files_removed(self):
        publicdata.geo_fetch_selected(
            'GSE99999', [self.url('wanted.h5ad')], self.out, verify_sizes=False)
        staging = paths.layout()['staging_conversion']
        leftovers = [p for p in staging.glob('*') if p.is_dir()] if staging.is_dir() else []
        self.assertEqual(leftovers, [], '转换工作目录必须清理')
        self.assertEqual(list(self.out.glob('*.part')), [], '不能残留 .part 文件')

    def test_unparseable_file_is_reported_not_faked(self):
        with self.assertRaises(publicdata.PublicDataError) as ctx:
            publicdata.geo_fetch_selected(
                'GSE99999', [self.url('huge_archive.tar.gz')], self.out, verify_sizes=False)
        message = str(ctx.exception)
        self.assertIn('huge_archive.tar.gz', message)
        self.assertFalse(any(self.out.glob('*.h5ad')), '解析失败不能产出假数据集')

    def test_meta_json_records_selection(self):
        result = publicdata.geo_fetch_selected(
            'GSE99999', [self.url('wanted.h5ad')], self.out, verify_sizes=False)
        meta_path = Path(result[0]).with_name(Path(result[0]).name + '.meta.json')
        self.assertTrue(meta_path.is_file())
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        self.assertEqual(meta['selected_files'], ['wanted.h5ad'])
        self.assertEqual(meta['selection_source'], 'user_selected')


class DownloadFilePart(unittest.TestCase):
    """download_file 的中断语义：半成品存成 .part，绝不冒充完成。"""

    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        self.base = Path(self._folder.name)
        self._env = {paths.DATA_ROOT_ENV: str(self.base), paths.SETTINGS_ENV: str(self.base / 's.json')}
        self._saved = {k: os.environ.get(k) for k in self._env}
        os.environ.update(self._env)

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._folder.cleanup()

    def test_truncated_existing_file_is_resumed_not_trusted(self):
        """旧版本用正式文件名做续传，会留下被当成完整的截断文件。

        新语义：已存在的正式文件先降级为 .part，再用 Range 收完；
        服务器不支持 Range 时从头重下。绝不能静默接受半成品。
        """
        served = self.base / 'served'
        served.mkdir()
        payload = b'0123456789' * 1000
        (served / 'full.h5ad').write_bytes(payload)

        handler = partial(_CountingHandler, directory=str(served))
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f'http://127.0.0.1:{server.server_address[1]}/full.h5ad'
            target = self.base / 'out' / 'full.h5ad'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload[:3000])          # 模拟被截断的历史文件
            publicdata.download_file(url, target)
            self.assertEqual(target.read_bytes(), payload, '必须续传/重下到完整内容')
            self.assertEqual(list(target.parent.glob('*.part')), [], '不能残留 .part')
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
