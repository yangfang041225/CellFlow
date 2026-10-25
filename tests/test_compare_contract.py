"""比较契约：只有真正同一身份/坐标才允许图直接比较，指标不排名。"""
import unittest

from cellflow.compare import figure_contract

BASE_RESULT = {
    'cells_hash': 'c' * 16,
    'genes_hash': 'g' * 16,
    'preprocessing_hash': 'p' * 16,
    'embedding': [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]],
}
BASE_CONFIG = {
    'n_top_genes': 120, 'use_umap': False, 'n_neighbors': 15,
    'n_pcs': 20, 'seed': 7, 'min_shared_counts': 5,
}
FIGURES = ['embedding.svg', 'qc.svg']


class CompareContract(unittest.TestCase):
    def test_identical_runs_can_share_coordinates(self):
        contract = figure_contract(BASE_RESULT, dict(BASE_RESULT), dict(BASE_CONFIG),
                                   dict(BASE_CONFIG), FIGURES, FIGURES)
        self.assertTrue(contract['can_compare_figures'])
        self.assertTrue(contract['same_coordinates'])
        self.assertTrue(contract['metrics_comparable'])
        self.assertTrue(contract['side_by_side'])
        self.assertEqual(contract['reasons'], [])

    def test_different_hvg_blocks_figure_comparison(self):
        config_b = dict(BASE_CONFIG, n_top_genes=300)
        contract = figure_contract(BASE_RESULT, dict(BASE_RESULT), BASE_CONFIG, config_b,
                                   FIGURES, FIGURES)
        self.assertFalse(contract['can_compare_figures'])
        self.assertTrue(any('n_top_genes' in reason for reason in contract['reasons']))
        self.assertTrue(contract['side_by_side'])

    def test_use_umap_switch_blocks_coordinate_comparison(self):
        config_b = dict(BASE_CONFIG, use_umap=True)
        contract = figure_contract(BASE_RESULT, dict(BASE_RESULT), BASE_CONFIG, config_b,
                                   FIGURES, FIGURES)
        self.assertFalse(contract['can_compare_figures'])

    def test_different_embedding_blocks_even_if_config_same(self):
        other = dict(BASE_RESULT, embedding=[[9.0, 9.0], [8.0, 8.0], [7.0, 7.0]])
        contract = figure_contract(BASE_RESULT, other, BASE_CONFIG, BASE_CONFIG, FIGURES, FIGURES)
        self.assertFalse(contract['can_compare_figures'])
        self.assertFalse(contract['same_coordinates'])
        self.assertTrue(any('嵌入坐标' in reason for reason in contract['reasons']))

    def test_different_gene_space_is_not_metric_comparable(self):
        other = dict(BASE_RESULT, genes_hash='x' * 16)
        contract = figure_contract(BASE_RESULT, other, BASE_CONFIG, BASE_CONFIG, FIGURES, FIGURES)
        self.assertFalse(contract['can_compare_figures'])
        self.assertFalse(contract['metrics_comparable'])
        self.assertIn('不能直接比较', contract['metrics_note'])

    def test_missing_figure_is_reported(self):
        contract = figure_contract(BASE_RESULT, dict(BASE_RESULT), BASE_CONFIG, BASE_CONFIG,
                                   FIGURES, ['qc.svg'])
        self.assertFalse(contract['can_compare_figures'])
        self.assertTrue(any('embedding.svg' in reason for reason in contract['reasons']))


if __name__ == '__main__':
    unittest.main()
