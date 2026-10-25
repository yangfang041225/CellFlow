"""后台下载：进度、取消、失败隔离。"""
import tempfile
import time
import unittest
from pathlib import Path

from cellflow import downloads
from cellflow.publicdata import DownloadCancelled, _cancelled, _emit


def _wait(task_id, states, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = downloads.get(task_id)
        if task and task['state'] in states:
            return task
        time.sleep(0.03)
    return downloads.get(task_id)


class Downloads(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.outdir = Path(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def test_progress_then_done_moves_file(self):
        def fake(source, identifier, staging, progress=None):
            target = Path(staging) / 'demo_standard.h5ad'
            for index in range(4):
                time.sleep(0.02)
                progress(f'块 {index}')
                _emit(index * 25, 100)
            target.write_bytes(b'x' * 256)
            return target

        task_id = downloads.submit('fake', 'demo', self.outdir, fetch_func=fake)
        task = _wait(task_id, {'done', 'failed', 'cancelled'})
        self.assertEqual(task['state'], 'done', task)
        final = Path(task['path'])
        self.assertTrue(final.is_file())
        self.assertEqual(final.parent, self.outdir)
        self.assertFalse((self.outdir / '.downloads' / task_id).exists(), '临时目录应清理')

    def test_cancel_stops_and_cleans_partial(self):
        def slow(source, identifier, staging, progress=None):
            partial = Path(staging) / 'partial.h5ad'
            partial.write_bytes(b'0' * 128)
            for _ in range(200):
                time.sleep(0.02)
                if _cancelled():
                    raise DownloadCancelled('用户取消下载')
            return partial

        task_id = downloads.submit('fake', 'slow', self.outdir, fetch_func=slow)
        _wait(task_id, {'running'})
        ok, _ = downloads.cancel(task_id)
        self.assertTrue(ok)
        task = _wait(task_id, {'cancelled', 'done', 'failed'})
        self.assertEqual(task['state'], 'cancelled', task)
        self.assertFalse((self.outdir / '.downloads' / task_id).exists(), '取消后临时目录应删除')
        leftovers = [path for path in self.outdir.glob('*.h5ad')]
        self.assertEqual(leftovers, [])

    def test_failure_is_reported(self):
        def broken(source, identifier, staging, progress=None):
            raise RuntimeError('网络不通')

        task_id = downloads.submit('fake', 'bad', self.outdir, fetch_func=broken)
        task = _wait(task_id, {'failed'})
        self.assertEqual(task['state'], 'failed')
        self.assertIn('网络不通', task['error'])

    def test_api_starts_and_reports_download(self):
        import cellflow.publicdata as publicdata
        from fastapi.testclient import TestClient
        from cellflow import api

        name = 'api_demo_download_test.h5ad'
        final = Path(__file__).resolve().parents[1] / 'data' / 'public' / name

        def fake(source, identifier, outdir, progress=None):
            target = Path(outdir) / name
            target.write_bytes(b'x' * 64)
            return target

        original = publicdata.fetch
        publicdata.fetch = fake
        try:
            client = TestClient(api.app)
            response = client.post('/api/downloads',
                                   json={'source': 'url', 'identifier': 'http://example.com/a.h5ad'})
            self.assertEqual(response.status_code, 200, response.text[:200])
            task_id = response.json()['id']
            status = {}
            for _ in range(200):
                status = client.get('/api/downloads/' + task_id).json()
                if status['state'] in ('done', 'failed'):
                    break
                time.sleep(0.02)
            self.assertEqual(status['state'], 'done', status)
            self.assertTrue(Path(status['path']).is_file())
            self.assertEqual(client.get('/api/downloads/nope').status_code, 404)
        finally:
            publicdata.fetch = original
            final.unlink(missing_ok=True)

    def test_cancel_finished_task_is_rejected(self):
        def quick(source, identifier, staging, progress=None):
            target = Path(staging) / 'q.h5ad'
            target.write_bytes(b'x')
            return target

        task_id = downloads.submit('fake', 'q', self.outdir, fetch_func=quick)
        _wait(task_id, {'done'})
        ok, message = downloads.cancel(task_id)
        self.assertFalse(ok)
        self.assertIn('不能取消', message)


if __name__ == '__main__':
    unittest.main()
