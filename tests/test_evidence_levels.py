"""证据等级：运行成功不能自动升级为参考对照/生物学证据。"""
import json
import tempfile
import unittest
from pathlib import Path

from cellflow.evidence import assess

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / 'examples' / 'synthetic.npz'


class EvidenceUnit(unittest.TestCase):
    def test_success_only_reaches_l1(self):
        quality = {'ok': True, 'checks': [{'check': 'a', 'ok': True}]}
        levels = assess({'metrics': {}}, quality, {'status': 'completed'})
        self.assertEqual(levels['max_level'], 1)
        statuses = {item['level']: item['status'] for item in levels['items']}
        self.assertEqual(statuses[0], 'met')
        self.assertEqual(statuses[1], 'met')
        self.assertEqual(statuses[2], 'not_reached')
        self.assertEqual(statuses[3], 'not_reached')
        self.assertEqual(statuses[4], 'not_reached')

    def test_explicit_reference_record_unlocks_l2(self):
        quality = {'ok': True, 'checks': [{'check': 'a', 'ok': True}]}
        levels = assess({}, quality, {'status': 'completed',
                                      'reference_comparison': {'kind': 'scvelo'}})
        self.assertEqual(levels['max_level'], 2)


class EvidenceInRun(unittest.TestCase):
    def test_qc_run_records_levels_and_report_section(self):
        from cellflow.runner import run
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'run'
            config = {'method': 'qc', 'seed': 1, 'n_top_genes': 60, 'n_neighbors': 10,
                      'n_pcs': 10, 'raw_x_confirmed': True}
            provenance = run(DEMO, config, output)
            self.assertEqual(provenance['status'], 'completed')
            result = json.loads((output / 'result.json').read_text(encoding='utf-8'))
            levels = result['evidence_levels']
            self.assertEqual(levels['max_level'], 1)
            self.assertEqual(provenance['evidence_levels']['max_level'], 1)
            report = (output / 'A_生物学分析报告.md').read_text(encoding='utf-8')
            self.assertIn('4.12 证据等级', report)
            self.assertIn('L2', report)
            self.assertIn('未达到', report)
            tech = (output / 'B_技术与复现报告.md').read_text(encoding='utf-8')
            self.assertIn('证据等级', tech)


if __name__ == '__main__':
    unittest.main()
