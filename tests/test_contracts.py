"""契约测试：数据前提、方法准入、配置守卫、内容一致性。

运行：.venv\\Scripts\\python.exe -m unittest discover -s tests -v
这些测试只用内置模拟数据，不联网；临时文件用系统临时目录，不写项目目录。
"""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from scipy import sparse

from cellflow import advisor, method_content
from cellflow.compare import compare
from cellflow.data import AUTO_GROUP, inspect, load, make_demo, validate
from cellflow.registry import (FAMILIES, INDEX, METHODS, STATUS_LABELS, eligibility,
                               installed, status_of)
from cellflow.runner import DEFAULTS, normalize_config, run


class Contracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = make_demo(self.root / 'input.npz')
        self.data = load(self.path)
        self.c = {**DEFAULTS, 'raw_x_confirmed': True, 'raw_su_confirmed': True,
                  'method': 'demo', 'group_key': 'group',
                  'root_cell': 'demo_cell_000', 'root_reason': '模拟生成器已知起点，仅供教学'}

    def tearDown(self):
        self.tmp.cleanup()

    # ------------------------------------------------------------ 数据前提
    def test_missing_su_does_not_impute_velocity(self):
        self.data.layers = {}
        facts = validate(self.data, self.c)
        rows = eligibility(facts, self.c, check_environment=False)
        self.assertFalse(next(x for x in rows if x['key'] == 'scvelo_stochastic')['can_run'])
        self.assertTrue(next(x for x in rows if x['key'] == 'qc')['can_run'])

    def test_all_sparse_values_checked(self):
        self.data.layers['spliced'] = sparse.csr_matrix(self.data.layers['spliced'])
        self.data.layers['spliced'].data[-1] = np.nan
        self.assertTrue(validate(self.data, self.c)['errors'])

    def test_integer_appearance_does_not_prove_raw(self):
        facts = validate(self.data, {**self.c, 'raw_su_confirmed': False})
        self.assertFalse(facts['velocity_counts'])
        facts2 = validate(self.data, {**self.c, 'raw_x_confirmed': False})
        self.assertFalse(facts2['raw_x'])

    def test_duplicates_block_instead_of_rename(self):
        self.data.genes[1] = self.data.genes[0]
        self.assertTrue(validate(self.data, self.c)['errors'])

    def test_normalized_layer_cannot_be_raw_counts(self):
        self.data.layers['unspliced'] = self.data.layers['unspliced'] / 7
        self.assertFalse(validate(self.data, self.c)['velocity_counts'])

    def test_dpt_requires_biological_root(self):
        c = {**self.c, 'root_cell': '', 'root_reason': ''}
        rows = eligibility(validate(self.data, c), c, check_environment=False)
        dpt = next(x for x in rows if x['key'] == 'dpt')
        self.assertFalse(dpt['can_run'])
        self.assertTrue(any('起始细胞' in reason for reason in dpt['scientific_blockers']))

    def test_poor_splicing_signal_keeps_qc_available(self):
        self.data.layers['unspliced'][:200] = 0
        facts = validate(self.data, self.c)
        rows = eligibility(facts, self.c, check_environment=False)
        self.assertFalse(facts['velocity_counts'])
        self.assertTrue(next(x for x in rows if x['key'] == 'qc')['can_run'])

    def test_auto_group_satisfies_group_premise(self):
        self.data.layers = {}
        c = {**self.c, 'group_key': AUTO_GROUP}
        facts = validate(self.data, c)
        self.assertFalse(facts['errors'])
        rows = eligibility(facts, c, check_environment=False)
        paga = next(x for x in rows if x['key'] == 'paga')
        self.assertFalse([r for r in paga['scientific_blockers'] if '分群' in r or '分组' in r])

    # ------------------------------------------------------------ 方法准入
    def test_planned_models_are_not_executable(self):
        rows = eligibility(validate(self.data, self.c), self.c, check_environment=False)
        for row in rows:
            if not row['implemented']:
                self.assertFalse(row['can_run'], row['key'])
                self.assertEqual(row['status'], 'planned')
                self.assertEqual(row['status_label'], STATUS_LABELS['planned'][0])

    def test_registry_is_consistent(self):
        keys = [m.key for m in METHODS]
        self.assertEqual(len(keys), len(set(keys)), '方法 key 不能重复')
        for method in METHODS:
            self.assertIn(method.family, FAMILIES, method.key)
            self.assertTrue(method.note, method.key)
            self.assertTrue(method.gpu_note, method.key)
            if method.implemented:
                self.assertTrue(method.outputs, method.key + ' 已接入却没有声明输出')
        self.assertIn('regvelo', INDEX)
        self.assertEqual(status_of(INDEX['multivelo']), 'planned')

    def test_missing_package_is_reported_as_none(self):
        fake = installed(('definitely-not-a-package-xyz',))
        self.assertIsNone(fake['definitely-not-a-package-xyz'])

    def test_blockers_are_human_readable(self):
        self.data.layers = {}
        rows = eligibility(validate(self.data, self.c), self.c, check_environment=True)
        joined = ' '.join(sum((r['scientific_blockers'] + r['runtime_blockers'] for r in rows), []))
        self.assertNotIn('缺少前提：raw_SU', joined)
        self.assertIn('剪接', joined)

    # ------------------------------------------------------------ 配置与建议
    def test_config_guard_rejects_unknown_and_bad_types(self):
        with self.assertRaises(ValueError):
            normalize_config({**self.c, 'not_a_field': 1})
        with self.assertRaises(ValueError):
            normalize_config({**self.c, 'n_neighbors': 'many'})
        with self.assertRaises(ValueError):
            normalize_config({**self.c, 'raw_x_confirmed': 'yes'})
        with self.assertRaises(ValueError):
            normalize_config({**self.c, 'leiden_resolution': 99})
        ok = normalize_config({**self.c, 'leiden_resolution': 1.2})
        self.assertAlmostEqual(ok['leiden_resolution'], 1.2)

    def test_advisor_recommends_by_data_facts(self):
        facts = validate(self.data, self.c)
        plan = advisor.recommend(facts, self.c)
        keys = [s['key'] for s in plan['steps']]
        self.assertIn('qc', keys)
        self.assertIn('scvelo_stochastic', keys)
        self.assertTrue(plan['budget']['memory_text'])
        self.assertTrue(plan['notes'])
        no_su = dict(facts, velocity_counts=False)
        plan2 = advisor.recommend(no_su, {**self.c, 'group_key': ''})
        self.assertNotIn('scvelo_stochastic', [s['key'] for s in plan2['steps']])
        self.assertTrue(any('速度' in gap or 'velocity' in gap for gap in plan2['gaps']))

    # ------------------------------------------------------------ 内容一致性
    def test_method_content_complete_and_consistent(self):
        missing = {k: v for k, v in method_content.completeness().items() if v}
        self.assertEqual(missing, {}, f'模型介绍内容缺字段：{missing}')
        self.assertEqual(len(method_content.METHOD_CONTENT), len(METHODS))
        for method in METHODS:
            card = method_content.METHOD_CONTENT[method.key]
            extra = [x for x in card.get('we_output', []) if x not in method.outputs]
            self.assertEqual(extra, [], f'{method.key} 的 we_output 超出 registry.outputs：{extra}')
            self.assertTrue(card['not_question'], method.key)

    # ------------------------------------------------------------ 模型切换与设备
    def test_velocity_model_registry_is_consistent(self):
        from cellflow.registry import DEFAULT_VELOCITY_MODEL, INDEX, METHODS, VELOCITY_MODELS
        self.assertIn(DEFAULT_VELOCITY_MODEL, VELOCITY_MODELS)
        for key, info in VELOCITY_MODELS.items():
            self.assertIn('label', info)
            self.assertIn(info['method'], INDEX, f'{key} 指向了不存在的方法 {info["method"]}')
            self.assertTrue(INDEX[info['method']].implemented, f'{key} 指向的方法没有适配器')
            for field in ('speed', 'gpu', 'assumption', 'when'):
                self.assertTrue(info.get(field), f'{key} 缺少 {field}')
        implemented = [m.key for m in METHODS if m.implemented]
        self.assertGreaterEqual(len(implemented), 3)

    def test_device_and_velocity_model_config(self):
        from cellflow.registry import VELOCITY_MODELS
        ok = normalize_config({**self.c, 'device': 'cpu', 'velocity_model': 'deterministic'})
        self.assertEqual(ok['device'], 'cpu')
        self.assertEqual(ok['velocity_model'], 'deterministic')
        with self.assertRaises(ValueError):
            normalize_config({**self.c, 'device': 'tpu'})
        with self.assertRaises(ValueError):
            normalize_config({**self.c, 'velocity_model': 'not-a-model'})

    def test_device_resolution_reports_reason(self):
        from cellflow import device as device_mod
        info = device_mod.resolve('cpu')
        self.assertEqual(info.device, 'cpu')
        self.assertTrue(info.reason)
        # 本机没有 CUDA 版 PyTorch 时，明确要求 cuda 应给出原因并回退到 CPU
        forced = device_mod.resolve('cuda')
        self.assertIn(forced.device, ('cpu', 'cuda'))
        if forced.device == 'cpu':
            self.assertIn('不可用', forced.reason)

    def test_isolated_env_helper(self):
        from cellflow.registry import INDEX, env_python
        root = Path(__file__).resolve().parents[1]
        self.assertIsNotNone(env_python('qc', root=root), '主环境解释器应当存在')
        self.assertEqual(INDEX['velovi'].runtime_env, '.venv-velovi')
        self.assertEqual(INDEX['regvelo'].runtime_env, '.venv-regvelo')
        # 环境不存在时必须返回 None，而不是编一个路径出来
        self.assertIsNone(env_python('unitvelo', root=root))

    # ------------------------------------------------------------ 格式体检
    def test_inspect_describes_data(self):
        fmt = inspect(self.data, self.path)
        self.assertEqual(fmt['shape'], {'n_cells': 240, 'n_genes': 120})
        self.assertEqual(sorted(fmt['layers_present']), ['spliced', 'unspliced'])
        self.assertTrue(fmt['matrices']['X']['integer_like'])
        self.assertEqual(fmt['var']['mt_matched'], 2)
        self.assertTrue(fmt['var']['unique'])
        self.assertIsNotNone(fmt['splice'])
        self.assertGreaterEqual(fmt['splice']['both_positive_pct'], 90)
        names = [o['name'] for o in fmt['obs']]
        self.assertIn('group', names)
        self.assertTrue(next(o for o in fmt['obs'] if o['name'] == 'group')['is_group_candidate'])
        self.assertIsInstance(fmt['warnings'], list)

    # ------------------------------------------------------------ 端到端
    def test_input_unchanged_and_failure_report_exists(self):
        original = self.path.read_bytes()
        out = self.root / 'failed'
        provenance = run(self.path, {**self.c, 'method': 'multivelo'}, out)
        self.assertEqual(provenance['status'], 'failed')
        self.assertEqual(self.path.read_bytes(), original, '输入文件不能被修改')
        self.assertTrue((out / 'A_生物学分析报告.md').exists())
        self.assertTrue((out / 'B_技术与复现报告.md').exists())
        self.assertFalse((out / 'result.json').exists())
        body = (out / 'A_生物学分析报告.md').read_text(encoding='utf-8')
        self.assertIn('失败', body)

    def test_demo_is_reproducible_and_marked(self):
        a, b = self.root / 'a', self.root / 'b'
        self.assertEqual(run(self.path, self.c, a)['status'], 'completed')
        self.assertEqual(run(self.path, self.c, b)['status'], 'completed')
        ra = json.loads((a / 'result.json').read_text(encoding='utf-8'))
        rb = json.loads((b / 'result.json').read_text(encoding='utf-8'))
        self.assertTrue(ra['synthetic'])
        self.assertIsNone(ra['velocity'])
        self.assertEqual(ra['embedding'], rb['embedding'])
        self.assertFalse(compare(a, b)['comparable'])
        for name in ('figures/qc.svg', 'figures/embedding.svg',
                     'A_生物学分析报告.md', 'B_技术与复现报告.md', 'format.json'):
            self.assertTrue((a / name).exists(), name)

    def test_unreadable_input_still_writes_failure_report(self):
        """读取阶段就失败时，也必须留下 provenance 与 A/B 报告，而不是抛异常掩盖原因。"""
        broken = self.root / 'broken.h5ad'
        broken.write_bytes(b'not an h5ad file at all')
        out = self.root / 'broken-run'
        provenance = run(broken, {**self.c, 'method': 'qc'}, out)
        self.assertEqual(provenance['status'], 'failed')
        self.assertEqual(provenance.get('failed_stage'), 'read_input')
        self.assertTrue((out / 'provenance.json').exists())
        self.assertTrue((out / 'A_生物学分析报告.md').exists())
        self.assertTrue((out / 'B_技术与复现报告.md').exists())
        self.assertIn('读取', (out / 'run.log').read_text(encoding='utf-8', errors='replace'))

    def test_object_pickle_is_not_loaded(self):
        path = self.root / 'evil.npz'
        np.savez(path, X=np.array([object()]), cells=['a'], genes=['b'])
        with self.assertRaises(ValueError):
            load(path)


if __name__ == '__main__':
    unittest.main()
