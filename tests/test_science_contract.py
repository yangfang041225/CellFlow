"""科学契约回归测试（F11 / F19 / F20）。

原则：**只断言本机真的验证过的东西**。
- F11 veloVI：静态断言 + 不需要 scvi 的契约单测 + 隔离环境存在时跑真实 smoke
  （记录输入层统计，断言训练前注册的是 Ms/Mu、不是原始计数）。
- F19 预处理声明：跑真实 pipeline，断言「声明 / 观察 / 有效结论 / 实际执行步骤」进入运行记录，
  且 auto 与单独 scVelo 走同一套协议（Ms/Mu 指纹一致）、unknown 时不做改数值的操作。
- F20 RegVelo：断言状态仍为 experimental（不是“可运行”），并且缺少速度层时报错而不是伪造。

本机没有 RegVelo 环境，因此 RegVelo 的**真实训练**不会被断言；文中标注为 static / skip。
"""
import importlib.util
import inspect as pyinspect
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np
from scipy import sparse

from cellflow import adapters
from cellflow.data import (PREPROCESSING_PROTOCOL, Dataset, declaration, load, make_demo,
                           require_protocol_scale, to_anndata)
from cellflow.registry import INDEX, METHODS, eligibility, env_python, isolated_ready, status_of
from cellflow.runner import DEFAULTS, run

ROOT = Path(__file__).resolve().parents[1]
HAS_SCANPY = importlib.util.find_spec('scanpy') is not None
HAS_SCVELO = importlib.util.find_spec('scvelo') is not None
VELOVI_PYTHON = env_python('velovi', root=ROOT)
if VELOVI_PYTHON is None:
    # 隔离环境不在项目根时（例如只读镜像、CI 缓存目录）可以用这个变量指定解释器；
    # 正常安装（.venv-velovi 就在项目根）不需要它，也不会走这条分支。
    _override = os.environ.get('CELLFLOW_VELOVI_PYTHON')
    VELOVI_PYTHON = Path(_override) if _override else None
HAS_VELOVI = (VELOVI_PYTHON is not None and Path(VELOVI_PYTHON).exists()
              and (isolated_ready('velovi') or bool(os.environ.get('CELLFLOW_VELOVI_PYTHON'))))

BASE = {**DEFAULTS, 'seed': 42, 'n_top_genes': 40, 'n_neighbors': 8, 'n_pcs': 8,
        'min_shared_counts': 5, 'max_iter': 1, 'max_epochs': 1, 'use_umap': False,
        'raw_x_confirmed': True, 'raw_su_confirmed': True, 'mt_prefix': 'MT-',
        'species': '模拟物种', 'tissue': '模拟系统', 'sample_key': 'sample',
        'group_key': 'group', 'root_cell': 'demo_cell_000',
        'root_reason': '模拟生成器已知起点，仅供教学'}
VELOCITY_CHAIN = ['filter_genes', 'normalize_per_cell', 'log1p',
                  'highly_variable_genes', 'moments']


def _read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _actions(declaration_record):
    return [str(step.get('action')) for step in (declaration_record or {}).get('steps') or []]


@unittest.skipUnless(HAS_SCANPY and HAS_SCVELO, 'scanpy/scvelo 未安装')
class PreprocessingDeclaration(unittest.TestCase):
    """F19：声明 + 观察 + 有效结论 + 实际执行步骤必须进入运行记录。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.demo = make_demo(cls.root / 'input.npz')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _run(self, method, tag=None, **overrides):
        folder = self.root / (method + ('-' + tag if tag else ''))
        return folder, run(self.demo, {**BASE, 'method': method, **overrides}, folder)

    def test_declaration_reaches_every_run_record(self):
        """声明要同时出现在 validation/format/result/preprocessing 与 provenance 的层契约里。"""
        for method in ('qc', 'leiden'):
            with self.subTest(method=method):
                folder, prov = self._run(method)
                self.assertEqual(prov['status'], 'completed', prov.get('error'))
                facts = _read_json(folder / 'validation.json')
                decl = facts.get('preprocessing') or {}
                self.assertEqual(decl.get('protocol'), PREPROCESSING_PROTOCOL)
                for key in ('declared', 'observed', 'effective', 'steps'):
                    self.assertIn(key, decl, f'validation.json 缺 {key}')
                # 观察与声明必须分开记录（不能只有一个“x_kind”猜出来的结论）
                self.assertEqual(decl['observed']['X'], 'counts')
                self.assertEqual(decl['effective']['X'], 'counts')
                self.assertTrue(decl['declared_basis']['X'])
                # format.json（数据体检）也带协议头
                fmt = _read_json(folder / 'format.json')
                self.assertEqual((fmt.get('preprocessing') or {}).get('protocol'),
                                 PREPROCESSING_PROTOCOL)
                # result.json 带的是“跑完之后”的版本，含实际执行步骤
                result = _read_json(folder / 'result.json')
                executed = result.get('preprocessing') or {}
                self.assertEqual(executed.get('protocol'), PREPROCESSING_PROTOCOL)
                self.assertEqual(result.get('preprocessing_protocol'), PREPROCESSING_PROTOCOL)
                # 独立审计文件与 result 一致
                sidecar = _read_json(folder / 'preprocessing.json')
                self.assertEqual(sidecar.get('protocol'), PREPROCESSING_PROTOCOL)
                self.assertEqual(_actions(sidecar), _actions(executed))
                # provenance 至少携带“声明的层契约”（provenance 由 runner 写，只能取到 layers）
                self.assertEqual(sorted(prov.get('layers') or []), sorted(sidecar.get('layers_present') or []))

    def test_executed_steps_match_input_scale(self):
        """counts 才允许 normalize_total/log1p；日志里记的必须是真做过的步骤。"""
        folder, prov = self._run('leiden', tag='steps')
        self.assertEqual(prov['status'], 'completed', prov.get('error'))
        decl = _read_json(folder / 'preprocessing.json')
        self.assertEqual(_actions(decl)[:2], ['normalize_total', 'log1p'])
        self.assertNotIn('highly_variable_genes_by_variance', _actions(decl))
        self.assertEqual(decl['effective']['X'], 'counts')

        scalar = Dataset(X=np.asarray(self._normalized_like(), dtype=np.float64),
                         cells=np.array([f'c{i}' for i in range(240)]),
                         genes=np.asarray(load(self.demo).genes))
        fact = declaration(scalar, {})
        self.assertEqual(fact['observed']['X'], 'normalized')
        self.assertEqual(fact['effective']['X'], 'unknown')
        self.assertTrue(any('不做任何改数值' in note for note in fact['notes']), fact['notes'])

    def _normalized_like(self):
        data = load(self.demo)
        counts = np.asarray(data.X.todense() if sparse.issparse(data.X) else data.X, dtype=float)
        return counts / np.maximum(counts.sum(1, keepdims=True), 1) * 1e4 * 10.0  # 非整数、max>=30

    def test_unknown_scale_keeps_values_untouched(self):
        """未声明 + 无法判定 → 不做归一化/取对数（只允许不改变数值的操作）。"""
        import anndata as ad
        data = load(self.demo)
        a = to_anndata(data)
        a.X = sparse.csr_matrix(self._normalized_like())
        a.layers['spliced'] = sparse.csr_matrix(np.asarray(
            data.layers['spliced'].todense() if sparse.issparse(data.layers['spliced'])
            else data.layers['spliced'], dtype=float))
        a.layers['unspliced'] = sparse.csr_matrix(np.asarray(
            data.layers['unspliced'].todense() if sparse.issparse(data.layers['unspliced'])
            else data.layers['unspliced'], dtype=float))
        path = self.root / 'normalized_x.h5ad'
        a.write_h5ad(path)
        folder, prov = self._run_path(path, 'leiden-unknown', raw_x_confirmed=False)
        self.assertEqual(prov['status'], 'completed', prov.get('error'))
        decl = _read_json(folder / 'preprocessing.json')
        self.assertNotIn('normalize_total', _actions(decl))
        self.assertNotIn('log1p', _actions(decl))
        self.assertEqual(decl['effective']['X'], 'unknown')

    def test_unconfirmed_counts_are_refused(self):
        """整数外观 + 未确认原始计数 → 拒绝做会改数值的归一化（不能“先跑了再说”）。"""
        folder, prov = self._run('leiden', tag='unconfirmed', raw_x_confirmed=False)
        self.assertEqual(prov['status'], 'failed')
        self.assertIn('原始计数', str(prov.get('error')))

    def _run_path(self, path, tag, **overrides):
        folder = self.root / tag
        return folder, run(path, {**BASE, 'method': 'leiden', **overrides}, folder)

    def test_auto_and_standalone_share_velocity_protocol(self):
        """一键分析与单独 scVelo 必须走同一套预处理，且 Ms/Mu 数值指纹一致。"""
        folder_auto, prov_auto = self._run('auto', tag='shared')
        self.assertEqual(prov_auto['status'], 'completed', prov_auto.get('error'))
        folder_solo, prov_solo = self._run('scvelo_stochastic', tag='shared')
        self.assertEqual(prov_solo['status'], 'completed', prov_solo.get('error'))
        auto = _read_json(folder_auto / 'preprocessing.json')
        solo = _read_json(folder_solo / 'preprocessing.json')
        auto_chain = [action for action in _actions(auto) if action in VELOCITY_CHAIN]
        solo_chain = [action for action in _actions(solo) if action in VELOCITY_CHAIN]
        self.assertEqual(auto_chain, VELOCITY_CHAIN)
        self.assertEqual(auto_chain, solo_chain)
        self.assertEqual(auto.get('ms_mu_fingerprint'), solo.get('ms_mu_fingerprint'),
                         'auto 与单独 scVelo 的 Ms/Mu 预处理结果必须一致')


class VelovlInputContract(unittest.TestCase):
    """F11：VELOVI 的输入契约（不需要 scvi-tools 也能测的部分）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.demo = make_demo(cls.root / 'input.npz')
        cls.data = load(cls.demo)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_contract_requires_raw_splice_counts(self):
        """未声明 / 已归一化的 S/U 一律不满足协议（不能把计数当 normalized 喂进去）。"""
        decl = declaration(self.data, {**BASE, 'raw_su_confirmed': False})
        with self.assertRaises(ValueError) as ctx:
            require_protocol_scale(decl, ('spliced', 'unspliced'), 'veloVI')
        self.assertIn('原始计数', str(ctx.exception))

        spliced_layer = self.data.layers['spliced']
        raw = np.asarray(spliced_layer.todense() if sparse.issparse(spliced_layer) else spliced_layer, dtype=float)
        scaled = raw / 7.0  # 非整数 → 观察不再是 counts
        normalized = Dataset(X=self.data.X, cells=self.data.cells, genes=self.data.genes,
                             layers={'spliced': sparse.csr_matrix(scaled),
                                     'unspliced': self.data.layers['unspliced']})
        decl2 = declaration(normalized, {**BASE, 'raw_su_confirmed': True})
        self.assertIn(decl2['observed']['spliced'], ('log', 'normalized'))
        with self.assertRaises(ValueError):
            require_protocol_scale(decl2, ('spliced', 'unspliced'), 'veloVI')

    def test_adapter_registers_ms_mu_not_raw_counts(self):
        """静态断言：setup_anndata 传的是 Ms/Mu（防止回退成原始 spliced/unspliced）。"""
        source = pyinspect.getsource(adapters._velovi)
        self.assertIn("spliced_layer='Ms'", source)
        self.assertIn("unspliced_layer='Mu'", source)
        self.assertNotIn("spliced_layer='spliced'", source)
        self.assertNotIn("unspliced_layer='unspliced'", source)
        self.assertIn('velovi_input', source)

    def test_platform_choices_are_explicit(self):
        """版本相关前提写成常量，换版本时测试会先失败而不是悄悄跑错。"""
        self.assertEqual(adapters.VELOVI_INPUT_LAYERS, ('Ms', 'Mu'))
        self.assertEqual(adapters.VELOVI_INPUT, 'ms_mu_normalized')


@unittest.skipUnless(HAS_VELOVI and HAS_SCANPY and HAS_SCVELO,
                     'veloVI 隔离环境（.venv-velovi）未就绪：真实 smoke 未运行，不代表通过')
class VeloviSmoke(unittest.TestCase):
    """F11：真实训练 smoke（只证明接口与输入层，不声称收敛或科学有效性）。"""

    def test_velovi_trains_on_ms_mu(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            demo = make_demo(root / 'input.npz')
            config = {**BASE, 'method': 'velovi', 'max_epochs': 1}
            config_path = root / 'config.json'
            config_path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
            out = root / 'velovi'
            env = {**os.environ, 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
                   'MKL_NUM_THREADS': '1', 'NUMBA_NUM_THREADS': '1'}
            proc = subprocess.run(
                [str(VELOVI_PYTHON), '-m', 'cellflow.cli', 'run', '--input', str(demo),
                 '--config', str(config_path), '--output', str(out)],
                cwd=str(ROOT), capture_output=True, text=True, encoding='utf-8',
                errors='replace', env=env, timeout=1200)
            self.assertEqual(proc.returncode, 0,
                             f'veloVI 运行失败：{proc.stdout[-1500:]}{proc.stderr[-1500:]}')
            protocol = _read_json(out / 'velovi_protocol.json')
            self.assertEqual(protocol['velovi_input'], 'ms_mu_normalized')
            self.assertEqual(protocol['model_input_layers'], ['Ms', 'Mu'])
            self.assertIs(protocol['is_count_data'], False)
            self.assertEqual(protocol['setup_anndata_args'],
                             {'spliced_layer': 'Ms', 'unspliced_layer': 'Mu'})
            # 模型真正注册的层名（从 AnnDataManager 读出来）
            self.assertEqual(protocol['registered_layers']['X']['layer'], 'Ms')
            self.assertEqual(protocol['registered_layers']['U']['layer'], 'Mu')
            # 训练前输入层不是整数计数：这是 is_count_data=False 成立的前提
            for layer in ('Ms', 'Mu'):
                stats = protocol['layer_stats_before_training'][layer]
                self.assertFalse(stats['integer_like'], f'{layer} 仍是整数外观')
            self.assertTrue((out / 'velocity.npz').exists())
            steps = _actions(_read_json(out / 'preprocessing.json'))
            self.assertIn('setup_anndata', steps)
            self.assertIn('moments', steps)


class RegVeloContract(unittest.TestCase):
    """F20：未验证前必须是 experimental，且不得伪造速度。"""

    def test_regvelo_is_never_reported_ready(self):
        method = INDEX['regvelo']
        self.assertTrue(method.implemented)
        self.assertEqual(method.maturity, 'experimental')
        self.assertEqual(status_of(method), 'experimental')
        facts = {'errors': [], 'velocity_counts': True, 'obs_keys': [],
                 'root_cell_present': False, 'raw_x': True, 'protocol_known': True}
        row = next(item for item in eligibility(facts, {'grn_path': 'x.csv', 'grn_confirmed': True,
                                                       'grn_source': 'src'}, check_environment=False)
                   if item['key'] == 'regvelo')
        self.assertEqual(row['status'], 'experimental')
        self.assertEqual(row['status_label'], '实验接入')
        self.assertNotEqual(row['status'], 'ready')
        self.assertIn('未在本机真实训练验证', row['status_note'])
        self.assertTrue(row['input_protocol'])
        self.assertTrue(row['output_protocol'])

    def test_regvelo_output_protocol_is_declared_truthfully(self):
        method = INDEX['regvelo']
        self.assertTrue(any('velocity.npz' in item for item in method.outputs))
        self.assertTrue(any('调控边' in item for item in [method.gpu_note]))
        self.assertTrue(method.data_limit)
        self.assertIn('未安装环境', method.data_limit)

    def test_regvelo_refuses_to_fabricate_velocity(self):
        """没有 velocity 层时必须报错（不写空矩阵、不拿别的模型顶替）。"""
        import anndata as ad
        a = ad.AnnData(np.zeros((4, 3), dtype=float))
        a.obs_names = [f'c{i}' for i in range(4)]
        a.var_names = ['g1', 'g2', 'g3']
        with tempfile.TemporaryDirectory() as folder:
            result = {'caveats': []}
            with self.assertRaises(ValueError) as ctx:
                adapters._regvelo_velocity(a, Path(folder), result)
            self.assertIn('velocity', str(ctx.exception))
            self.assertFalse((Path(folder) / 'velocity.npz').exists())

    def test_regvelo_audit_fields_are_not_hardcoded(self):
        """静态断言：设备取实际解析值，且 n_top_genes 真的进了预处理。"""
        prep = pyinspect.getsource(adapters._regvelo_prep)
        self.assertIn('n_top_genes', prep)
        self.assertIn('gpu_note' if False else 'n_top_genes_used', prep)
        train = pyinspect.getsource(adapters._regvelo)
        self.assertIn('device=dev.device', train)
        self.assertNotIn("device='cpu'", train)
        self.assertIn('verified', train)


class FateFallback(unittest.TestCase):
    """GPCCA 求解器在部分 scipy/BLAS 上产生负概率时，必须能回退到可控吸收求解。"""

    def _kernel_and_terms(self):
        import numpy as np
        import pandas as pd
        import scipy.sparse as sp
        from types import SimpleNamespace
        matrix = sp.csr_matrix(np.array([
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.4, 0.1, 0.3, 0.2],
            [0.1, 0.3, 0.2, 0.4],
        ]))
        terms = pd.Series(pd.Categorical(['T1', 'T2', None, None]))
        return SimpleNamespace(transition_matrix=matrix), terms

    def test_direct_absorption_is_finite_and_normalized(self):
        import numpy as np
        from cellflow.adapters import _absorption_direct
        kernel, terms = self._kernel_and_terms()
        names, probs, counts, negatives = _absorption_direct(kernel, terms)
        self.assertEqual(names, ['T1', 'T2'])
        self.assertEqual(probs.shape, (4, 2))
        self.assertTrue(np.isfinite(probs).all())
        self.assertTrue((probs >= 0).all())
        np.testing.assert_allclose(probs.sum(axis=1), 1.0, rtol=1e-9)
        self.assertEqual(counts['T1'], 1)
        self.assertGreaterEqual(negatives, 0)

    def test_compute_fate_recovers_from_solver_error(self):
        import numpy as np
        from types import SimpleNamespace
        from cellflow.adapters import _compute_fate
        kernel, terms = self._kernel_and_terms()

        class BrokenEstimator:
            terminal_states = terms

            def compute_fate_probabilities(self, *args, **kwargs):
                raise ValueError('`3` value(s) are negative. use a direct solver')

        names, probs, got_terms, counts, negatives = _compute_fate(
            BrokenEstimator(), kernel, lambda *a, **k: None, lambda *a, **k: None)
        self.assertEqual(names, ['T1', 'T2'])
        self.assertTrue((probs >= 0).all())
        np.testing.assert_allclose(probs.sum(axis=1), 1.0, rtol=1e-9)
        self.assertIs(got_terms, terms)


if __name__ == '__main__':
    unittest.main()
