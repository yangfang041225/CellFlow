"""真实 HTTP 下载链路：本地 http.server → download_file → standardize → 移入目录。

不依赖外网；验证进度、完成移动、取消清理。
"""
import http.server
import tempfile
import threading
import time
import unittest
from pathlib import Path
from functools import partial

import numpy as np
import anndata as ad
import pandas as pd
import scipy.sparse as sp

from cellflow import downloads


class _SlowHandler(http.server.BaseHTTPRequestHandler):
    """慢速发送 8 MB，便于取消。"""
    payload = b'\0' * (8 * 1024 * 1024)

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header('Content-Length', str(len(self.payload)))
        self.end_headers()
        for offset in range(0, len(self.payload), 512 * 1024):
            try:
                self.wfile.write(self.payload[offset:offset + 512 * 1024])
                self.wfile.flush()
            except Exception:
                return
            time.sleep(0.05)

    def log_message(self, *args):
        pass


def _serve(directory, handler=None):
    if handler is None:
        handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, server.server_address[1]


def _wait(task_id, states, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = downloads.get(task_id)
        if task and task['state'] in states:
            return task
        time.sleep(0.05)
    return downloads.get(task_id)


class DownloadNetwork(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def _make_h5ad(self, path):
        cells = [f'cell_{i}' for i in range(120)]
        genes = [f'GENE{i}' for i in range(40)]
        rng = np.random.default_rng(11)
        matrix = sp.csr_matrix(rng.integers(0, 5, size=(len(cells), len(genes))).astype('float32'))
        adata = ad.AnnData(X=matrix, obs=pd.DataFrame(index=cells), var=pd.DataFrame(index=genes))
        adata.write_h5ad(path)

    def test_url_download_completes(self):
        self._make_h5ad(self.root / 'tiny.h5ad')
        server, _thread, port = _serve(self.root)
        try:
            outdir = self.root / 'out'
            task_id = downloads.submit('url', f'http://127.0.0.1:{port}/tiny.h5ad', outdir)
            task = _wait(task_id, {'done', 'failed', 'cancelled'})
            self.assertEqual(task['state'], 'done', task)
            final = Path(task['path'])
            self.assertTrue(final.is_file())
            self.assertGreater(final.stat().st_size, 0)
            self.assertTrue(final.name.endswith('.h5ad'))
        finally:
            server.shutdown()

    def test_url_download_cancel_cleans_partial(self):
        server, _thread, port = _serve(self.root, handler=_SlowHandler)
        try:
            outdir = self.root / 'out'
            task_id = downloads.submit('url', f'http://127.0.0.1:{port}/big.h5ad', outdir)
            _wait(task_id, {'running'}, timeout=30)
            ok, _message = downloads.cancel(task_id)
            self.assertTrue(ok)
            task = _wait(task_id, {'cancelled', 'done', 'failed'}, timeout=60)
            self.assertEqual(task['state'], 'cancelled', task)
            staged = outdir / '.downloads' / task_id
            self.assertFalse(staged.exists(), '取消后临时目录应删除')
        finally:
            server.shutdown()


if __name__ == '__main__':
    unittest.main()
