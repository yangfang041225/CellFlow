"""显式方向/元数据导入：预览 → 确认 → 转换登记；不静默猜方向。"""
import io
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from cellflow import api, workspace


class TablePreviewImport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(api.app)

    def _cleanup(self, payload):
        path = Path(payload['path'])
        workspace.unregister_dataset(path)
        path.unlink(missing_ok=True)

    def _csv(self):
        return ('cell,group,g1,g2,g3,g4,g5,g6\n'
                'c1,A,1,2,3,4,5,6\n'
                'c2,A,2,3,4,5,6,7\n'
                'c3,B,3,4,5,6,7,8\n'
                'c4,B,4,5,6,7,8,9\n').encode('utf-8')

    def test_preview_then_import_with_metadata(self):
        preview = self.client.post('/api/datasets/table-preview',
                                   files={'file': ('cells.csv', io.BytesIO(self._csv()), 'text/csv')})
        self.assertEqual(preview.status_code, 200, preview.text[:300])
        body = preview.json()
        self.assertEqual(body['preview']['metadata_candidates'], ['group'])
        self.assertIn(body['preview']['orientation_confidence'], ('high', 'low'))
        imported = self.client.post('/api/datasets/table-import', json={
            'preview_id': body['preview_id'], 'orientation': 'cells_by_genes',
            'metadata_columns': ['group']})
        self.assertEqual(imported.status_code, 200, imported.text[:500])
        payload = imported.json()
        try:
            self.assertEqual(payload['n_cells'], 4)
            self.assertEqual(payload['n_genes'], 6)
            self.assertEqual(payload['name'], 'cells.csv')
            detail = self.client.get('/api/datasets/' + payload['id'] + '/detail').json()
            self.assertEqual(detail['n_cells'], 4)
            self.assertIn('group', detail['obs'])
            rows = self.client.get('/api/meta').json()['datasets']
            self.assertTrue(any(row['label'] == 'cells.csv' for row in rows))
        finally:
            self._cleanup(payload)

    def test_genes_by_cells_orientation_is_respected(self):
        table = ('gene,c1,c2,c3,c4,c5\n'
                 'g1,1,2,3,4,5\n'
                 'g2,2,3,4,5,6\n'
                 'g3,3,4,5,6,7\n'
                 'g4,4,5,6,7,8\n'
                 'g5,5,6,7,8,9\n').encode('utf-8')
        preview = self.client.post('/api/datasets/table-preview',
                                   files={'file': ('genes.csv', io.BytesIO(table), 'text/csv')}).json()
        imported = self.client.post('/api/datasets/table-import', json={
            'preview_id': preview['preview_id'], 'orientation': 'genes_by_cells',
            'metadata_columns': []})
        self.assertEqual(imported.status_code, 200, imported.text[:500])
        payload = imported.json()
        try:
            self.assertEqual(payload['n_cells'], 5)
            self.assertEqual(payload['n_genes'], 5)
        finally:
            self._cleanup(payload)

    def test_missing_values_are_rejected(self):
        bad = b'cell,g1,g2,g3,g4,g5\nc1,1,,3,4,5\n'
        preview = self.client.post('/api/datasets/table-preview',
                                   files={'file': ('bad.csv', io.BytesIO(bad), 'text/csv')})
        self.assertEqual(preview.status_code, 200)
        response = self.client.post('/api/datasets/table-import', json={
            'preview_id': preview.json()['preview_id'], 'orientation': 'cells_by_genes',
            'metadata_columns': []})
        self.assertEqual(response.status_code, 400)

    def test_unknown_preview_id_is_rejected(self):
        response = self.client.post('/api/datasets/table-import', json={
            'preview_id': '0' * 16, 'orientation': 'cells_by_genes', 'metadata_columns': []})
        self.assertEqual(response.status_code, 404)


if __name__ == '__main__':
    unittest.main()
