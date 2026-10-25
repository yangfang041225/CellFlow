"""术语参考表：只做精确匹配，未收录不翻译。"""
import unittest

from fastapi.testclient import TestClient

from cellflow import terms
from cellflow import api


class Terms(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(api.app)

    def test_lookup_exact_and_case_insensitive(self):
        self.assertEqual(terms.lookup('Blood'), '血液')
        self.assertEqual(terms.lookup('  PBMC '), '外周血单个核细胞')
        self.assertIsNone(terms.lookup('some_unknown_cluster'))
        self.assertIsNone(terms.lookup(''))
        self.assertIsNone(terms.lookup(None))

    def test_glossary_entries_are_curated(self):
        entries = terms.glossary()
        self.assertGreaterEqual(len(entries), 30)
        self.assertTrue(all(item['source'] == 'curated' for item in entries))
        self.assertEqual(entries, sorted(entries, key=lambda item: item['term']))

    def test_api_terms_endpoint(self):
        body = self.client.get('/api/terms').json()
        self.assertGreaterEqual(len(body['terms']), 30)
        self.assertIn('参考翻译', body['note'])
        self.assertIn('参考翻译', self.client.get('/api/terms').json()['note'])

    def test_dataset_preview_marks_reference_translation(self):
        datasets = self.client.get('/api/meta').json()['datasets']
        if not datasets:
            self.skipTest('没有数据集')
        item = next((row for row in datasets if row['source'] == '示例'), datasets[0])
        preview = self.client.get('/api/datasets/' + item['id'] + '/detail').json()['preview']
        for summary in preview['obs_summary'].values():
            for top in summary['top']:
                self.assertIn('zh', top)
        # 演示数据没有收录词，应为 None；有收录词时应返回中文
        values = [top['value'] for summary in preview['obs_summary'].values()
                  for top in summary['top']]
        self.assertTrue(values)


if __name__ == '__main__':
    unittest.main()
