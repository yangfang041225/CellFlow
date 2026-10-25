"""数据集身份：保留原始名，接受复合扩展名。"""
import gzip
import io
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from cellflow import api, workspace


class DatasetIdentity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(api.app)

    def _cleanup(self, payload):
        path = Path(payload['path'])
        workspace.unregister_dataset(path)
        path.unlink(missing_ok=True)

    def test_upload_keeps_original_filename(self):
        content = b'cell,g1,g2,g3,g4,g5\nc1,1,1,1,1,1\nc2,2,2,2,2,2\n'
        response = self.client.post('/api/datasets/upload',
                                    files={'file': ('中文 名称.csv', io.BytesIO(content), 'text/csv')})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        try:
            self.assertEqual(payload['name'], '中文 名称.csv')
            rows = self.client.get('/api/meta').json()['datasets']
            row = next(item for item in rows if item['id'] == payload['id'])
            self.assertEqual(row['label'], '中文 名称.csv')
        finally:
            self._cleanup(payload)

    def test_compound_gzip_suffix_is_accepted(self):
        content = gzip.compress(b'cell,g1,g2,g3,g4,g5\nc1,1,1,1,1,1\nc2,2,2,2,2,2\n')
        response = self.client.post('/api/datasets/upload',
                                    files={'file': ('矩阵 数据.csv.gz', io.BytesIO(content), 'application/gzip')})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        try:
            self.assertTrue(payload['path'].endswith('.csv.gz'))
            self.assertEqual(payload['name'], '矩阵 数据.csv.gz')
        finally:
            self._cleanup(payload)

    def test_unsupported_suffix_is_rejected(self):
        response = self.client.post('/api/datasets/upload',
                                    files={'file': ('bad.exe', io.BytesIO(b'MZ'), 'application/octet-stream')})
        self.assertEqual(response.status_code, 415)


if __name__ == '__main__':
    unittest.main()
