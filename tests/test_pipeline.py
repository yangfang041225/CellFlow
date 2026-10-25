"""端到端测试：真实调用 scanpy / scVelo / CellRank，并检查报告的必备内容。

较慢（约 1–2 分钟），但只在本机 CPU 上跑内置模拟数据，不联网。
本机没有安装的可选方法（RegVelo 等）会被准入规则拦住，不会假装运行。
"""
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from cellflow.data import AUTO_GROUP, load, make_demo
from cellflow.registry import env_python, isolated_ready
from cellflow.runner import DEFAULTS, run

HAS_SCANPY = importlib.util.find_spec('scanpy') is not None
HAS_SCVELO = importlib.util.find_spec('scvelo') is not None
HAS_CELLRANK = importlib.util.find_spec('cellrank') is not None

ROOT = Path(__file__).resolve().parents[1]
VELOVI_PYTHON = env_python('velovi', root=ROOT)
HAS_VELOVI = VELOVI_PYTHON is not None and isolated_ready('velovi')

BASE = {**DEFAULTS, 'seed': 42, 'n_top_genes': 60, 'n_neighbors': 10, 'n_pcs': 10,
        'min_shared_counts': 5, 'max_iter': 1, 'max_epochs': 1,
        'raw_x_confirmed': True, 'raw_su_confirmed': True, 'mt_prefix': 'MT-',
        'species': '模拟物种', 'tissue': '模拟系统', 'sample_key': 'sample',
        'group_key': 'group', 'root_cell': 'demo_cell_000',
        'root_reason': '模拟生成器已知起点，仅供教学', 'use_umap': True}


def _read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


@unittest.skipUnless(HAS_SCANPY and HAS_SCVELO, 'scanpy/scvelo 未安装')
class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.input = make_demo(cls.root / 'input.npz')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _run(self, method, tag=None, **overrides):
        # 每个用例一个独立目录：同一方法可能有多个变体（例如有/无起点）
        folder = self.root / (method + ('-' + tag if tag else ''))
        provenance = run(self.input, {**BASE, 'method': method, **overrides}, folder)
        return folder, provenance

    # ---------------------------------------------------------------- 分群
    def test_leiden_end_to_end(self):
        folder, prov = self._run('leiden')
        self.assertEqual(prov['status'], 'completed', prov.get('error'))
        result = _read_json(folder / 'result.json')
        self.assertGreaterEqual(result['metrics']['n_groups'], 2)
        self.assertEqual(len(result['groups']['labels']), result['embedding'].__len__())
        top = result['gene_table'][0]
        self.assertIn('genes', top)
        self.assertEqual(result['embedding_basis'], 'umap')
        self.assertTrue((folder / 'analysis.h5ad').exists())
        self.assertTrue((folder / 'calls.json').exists())

    # ---------------------------------------------------------------- 一键分析
    def test_auto_pipeline_produces_one_combined_result(self):
        """一键分析应当一次产出：分群 + PAGA + 速度 + 命运 + DPT，并且只有一份报告。"""
        folder, prov = self._run('auto', tag='withroot')
        self.assertEqual(prov['status'], 'completed', prov.get('error'))
        result = _read_json(folder / 'result.json')
        for key in ('groups', 'paga', 'velocity', 'arrows', 'fate', 'dpt', 'cluster_genes'):
            self.assertIn(key, result, f'一键分析缺少 {key}')
        self.assertEqual(result['method'], 'auto')
        self.assertGreaterEqual(len(result['groups']['sizes']), 2)
        self.assertGreaterEqual(len(result['fate']['terminal_states']), 1)
        self.assertEqual(len(result['fate']['probabilities']), len(result['embedding']))
        report = (folder / 'A_生物学分析报告.md').read_text(encoding='utf-8')
        for word in ('分群', '群间连接', '终末状态', '命运概率', '参数解释', '解释分层'):
            self.assertIn(word, report)
        for word in ('直接观察', '计算推断', '生物学假说'):
            self.assertIn(word, report)
        self.assertIn('4.12 图注', report)
        self.assertIn('不代表真实谱系', report)
        html_report = (folder / 'A_report.html').read_text(encoding='utf-8')
        self.assertIn('<h2>', html_report, 'HTML 报告应渲染 Markdown，而不是整篇塞进 <pre>')
        self.assertNotIn('<pre># A', html_report)
        self.assertIn('<table>', html_report)
        self.assertIn('图 1 ·', html_report)
        self.assertIn('id="fig-1"', html_report)
        self.assertIn('不代表真实谱系', html_report)

    def test_auto_without_root_uses_velocity_kernel(self):
        """没有起点时不应硬凑方向，但可用速度图作核估计命运。"""
        folder, prov = self._run('auto', tag='noroot', root_cell='', root_reason='')
        self.assertEqual(prov['status'], 'completed', prov.get('error'))
        result = _read_json(folder / 'result.json')
        self.assertNotIn('dpt', result)
        self.assertIn('速度伪时序', result['time_name'])
        self.assertIn('VelocityKernel', result['fate']['kernel'])

    def test_auto_accepts_preprocessed_x_with_raw_splice_layers(self):
        """真实数据常见形态：X 已归一化，但 spliced/unspliced 是整数计数。"""
        import anndata as ad
        import numpy as np
        from scipy import sparse
        from cellflow.data import load, make_demo, to_anndata
        src = make_demo(self.root / 'raw_for_log.npz')
        data = load(src)
        a = to_anndata(data)
        # 把 X 换成 log 化的归一化值（模拟已处理过的表达矩阵），剪接层保持整数
        a.X = np.log1p(a.X / np.maximum(np.asarray(a.X.sum(1)).ravel(), 1)[:, None] * 1e4)
        a.X = sparse.csr_matrix(a.X)
        for key in ('spliced', 'unspliced'):
            a.layers[key] = sparse.csr_matrix(a.layers[key])
        path = self.root / 'preprocessed_x.h5ad'
        a.write_h5ad(path)
        folder = self.root / 'auto-realstyle'
        prov = run(path, {**BASE, 'method': 'auto', 'raw_x_confirmed': False,
                          'root_cell': '', 'root_reason': ''}, folder)
        self.assertEqual(prov['status'], 'completed', prov.get('error'))
        facts = _read_json(folder / 'validation.json')
        self.assertEqual(facts['x_kind'], 'log')
        self.assertTrue(facts['velocity_counts'])
        result = _read_json(folder / 'result.json')
        self.assertIn('velocity', result)
        self.assertTrue((folder / 'velocity.npz').exists())
        report = (folder / 'B_技术与复现报告.md').read_text(encoding='utf-8')
        self.assertIn('取过对数', report)

    # ---------------------------------------------------------------- veloVI（隔离环境）
    @unittest.skipUnless(HAS_VELOVI and HAS_SCANPY, 'veloVI 隔离环境（.venv-velovi）未就绪')
    def test_velovi_runs_in_isolated_environment(self):
        """veloVI 必须用隔离环境的解释器跑（主环境没有 scvi-tools）。"""
        folder = self.root / 'velovi'
        config = {**BASE, 'method': 'velovi', 'max_epochs': 2, 'n_top_genes': 60}
        config_path = self.root / 'velovi-config.json'
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
        # 与生产路径一致：限制 BLAS/Numba 线程，避免并行 lowering 在测试机上病态变慢
        env = {**os.environ, 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
               'MKL_NUM_THREADS': '1', 'NUMBA_NUM_THREADS': '1'}
        proc = subprocess.run(
            [str(VELOVI_PYTHON), '-m', 'cellflow.cli', 'run',
             '--input', str(self.input), '--config', str(config_path), '--output', str(folder)],
            cwd=str(ROOT), capture_output=True, text=True, encoding='utf-8',
            errors='replace', env=env, timeout=900)
        self.assertEqual(proc.returncode, 0, f'veloVI 运行失败：{proc.stdout[-1500:]}{proc.stderr[-1500:]}')
        result = _read_json(folder / 'result.json')
        self.assertTrue(result.get('velocity'))
        self.assertTrue((folder / 'velocity.npz').exists())
        self.assertIn('veloVI', result.get('time_name', ''))
        self.assertTrue(result.get('metrics', {}).get('n_genes_used'))
        report = (folder / 'A_生物学分析报告.md').read_text(encoding='utf-8')
        self.assertIn('veloVI', report)
        self.assertIn('变分', report)

    # ---------------------------------------------------------------- PAGA
    def test_paga_with_auto_group(self):
        folder, prov = self._run('paga', group_key=AUTO_GROUP)
        self.assertEqual(prov['status'], 'completed', prov.get('error'))
        result = _read_json(folder / 'result.json')
        self.assertEqual(result['groups']['key'], 'leiden')
        matrix = result['paga']['connectivities']
        self.assertEqual(len(matrix), len(result['paga']['groups']))
        self.assertTrue((folder / 'figures' / 'paga.svg').exists())

    # ---------------------------------------------------------------- 速度
    def test_scvelo_deterministic_end_to_end(self):
        folder, prov = self._run('scvelo_deterministic')
        self.assertEqual(prov['status'], 'completed', prov.get('error'))
        result = _read_json(folder / 'result.json')
        self.assertTrue(result['velocity'])
        self.assertTrue((folder / 'velocity.npz').exists())
        self.assertIn('velocity_confidence_median', result['metrics'])
        self.assertTrue((prov.get('quality') or {}).get('ok'), prov.get('quality'))

    def test_scvelo_stochastic_end_to_end(self):
        folder, prov = self._run('scvelo_stochastic')
        self.assertEqual(prov['status'], 'completed', prov.get('error'))
        result = _read_json(folder / 'result.json')
        self.assertTrue(result['velocity'])
        self.assertTrue((folder / 'velocity.npz').exists())
        self.assertIn('arrows', result)
        self.assertTrue((folder / 'figures' / 'stream.png').exists(), '应生成本地流线图')
        self.assertIsNotNone(result['time'])
        self.assertIn('velocity_confidence_median', result['metrics'])
        self.assertTrue(result['preprocessing_hash'])
        report = (folder / 'A_生物学分析报告.md').read_text(encoding='utf-8')
        for word in ('数据质量', '参数解释', '证据边界', '官方方法来源', '模型内部诊断'):
            self.assertIn(word, report)
        tech = (folder / 'B_技术与复现报告.md').read_text(encoding='utf-8')
        for word in ('运行环境', '超参数快照', '真实调用序列', '复现方式', '结果文件清单'):
            self.assertIn(word, tech)

    def test_scvelo_dynamical_produces_latent_time(self):
        folder, prov = self._run('scvelo_dynamical')
        self.assertEqual(prov['status'], 'completed', prov.get('error'))
        result = _read_json(folder / 'result.json')
        self.assertIn('潜在时间', result['time_name'])
        self.assertTrue(any('fit_likelihood' == k for k in (result['gene_table'][0] if result['gene_table'] else {})))

    # ---------------------------------------------------------------- 命运
    @unittest.skipUnless(HAS_CELLRANK, 'cellrank 未安装')
    def test_cellrank_fate_probabilities(self):
        folder, prov = self._run('cellrank')
        self.assertEqual(prov['status'], 'completed', prov.get('error'))
        result = _read_json(folder / 'result.json')
        fate = result['fate']
        self.assertGreaterEqual(len(fate['terminal_states']), 1)
        probs = fate['probabilities']
        self.assertEqual(len(probs), len(result['embedding']))
        self.assertEqual(len(probs[0]), len(fate['terminal_states']))
        report = (folder / 'A_生物学分析报告.md').read_text(encoding='utf-8')
        self.assertIn('终末状态', report)
        self.assertIn('命运概率', report)
        self.assertIn('模型推断', report)

    # ---------------------------------------------------------------- 失败可解释
    def test_missing_optional_environment_fails_cleanly(self):
        folder = self.root / 'regvelo-blocked'
        prov = run(self.input, {**BASE, 'method': 'regvelo'}, folder)
        self.assertEqual(prov['status'], 'failed')
        self.assertIn('准入', prov['error'])
        self.assertTrue((folder / 'eligibility.json').exists())
        gates = _read_json(folder / 'eligibility.json')
        reg = next(g for g in gates if g['key'] == 'regvelo')
        self.assertTrue(reg['runtime_blockers'] or reg['scientific_blockers'])


if __name__ == '__main__':
    unittest.main()
