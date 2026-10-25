"""F34：install.py --check 的环境判定（用临时假环境，不重装、不联网）。

覆盖：
- 解释器不存在 → not_installed / 整体 not ready；
- 解释器存在但包缺失、版本不符、探测失败 → not_ready；
- 全部满足 → ready，manifest 含产品版本/Python/包版本/检查时间；
- 隔离环境用**自己的解释器**探测，存在但损坏时整体不报 OK；
- check_environment 只读，不创建/修改文件。
"""
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import install  # noqa: E402


def _min_version(spec):
    """把 `>=1.11,<1.12` 变成满足条件的下界 `1.11`。"""
    for clause in spec.split(','):
        clause = clause.strip()
        if clause.startswith('>='):
            return clause[2:].strip()
        if clause.startswith('>'):
            return clause[1:].strip()
        if clause.startswith('=='):
            return clause[2:].strip()
    return '1.0'


def _compliant(requirements):
    return {module: _min_version(spec) for module, _distribution, spec in requirements}


class FakeProbe:
    """模拟目标解释器返回的探测结果，并记录被调用的解释器路径。"""

    def __init__(self, versions=None, missing=None, errors=None):
        self.versions = versions or {}
        self.missing = missing or {}
        self.errors = errors or {}
        self.calls = []

    def __call__(self, python, requirements):
        folder = Path(python).parent.parent.name
        self.calls.append((str(python), [item[0] for item in requirements]))
        if folder in self.errors:
            return {'error': self.errors[folder], 'packages': {}}
        versions = self.versions.get(folder) or _compliant(requirements)
        missing = set(self.missing.get(folder, ()))
        packages = {}
        for module, _distribution, _spec in requirements:
            if module in missing:
                packages[module] = {'imported': False, 'version': None,
                                    'error': 'ModuleNotFoundError: fake missing'}
            else:
                packages[module] = {'imported': True, 'version': versions[module]}
        return {'python_version': '3.11.15', 'packages': packages}


class CheckEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / 'web' / 'dist').mkdir(parents=True)
        (self.root / 'web' / 'dist' / 'index.html').write_text('<!doctype html>', encoding='utf-8')
        (self.root / 'cellflow_app.pyw').write_text('# fake entry\n', encoding='utf-8')
        (self.root / 'cellflow').mkdir()
        (self.root / 'cellflow' / '__init__.py').write_text("__version__ = '9.9.9'\n",
                                                            encoding='utf-8')

    def tearDown(self):
        self._tmp.cleanup()

    def make_env(self, folder):
        scripts = self.root / folder / ('Scripts' if os.name == 'nt' else 'bin')
        scripts.mkdir(parents=True, exist_ok=True)
        exe = scripts / ('python.exe' if os.name == 'nt' else 'python')
        exe.write_text('', encoding='utf-8')
        return exe

    def check(self, probe):
        self._last_probe = probe
        return install.check_environment(root=self.root, probe=probe,
                                         now='2026-10-25T20:00:00+08:00')

    # ------------------------------------------------------------- 环境缺失
    def test_missing_core_environment_is_not_ready(self):
        manifest = self.check(FakeProbe())
        self.assertFalse(manifest['ready'])
        self.assertEqual(manifest['environments']['.venv']['status'], 'not_installed')
        self.assertTrue(any('.venv' in item for item in manifest['problems']))

    def test_optional_environment_missing_does_not_block_core(self):
        self.make_env('.venv')
        manifest = self.check(FakeProbe())
        self.assertTrue(manifest['ready'])
        self.assertEqual(manifest['environments']['.venv-velovi']['status'], 'not_installed')
        self.assertTrue(manifest['environments']['.venv-velovi']['optional'])

    # ------------------------------------------------------------- 包缺失 / 版本不符
    def test_missing_package_is_not_ready(self):
        self.make_env('.venv')
        manifest = self.check(FakeProbe(missing={'.venv': {'scanpy'}}))
        entry = manifest['environments']['.venv']
        self.assertFalse(manifest['ready'])
        self.assertEqual(entry['status'], 'not_ready')
        self.assertIn('scanpy', '；'.join(entry['problems']))

    def test_version_mismatch_is_not_ready(self):
        self.make_env('.venv')
        versions = _compliant(install.REQUIRED_PACKAGES['.venv'])
        versions['scanpy'] = '1.9.0'
        manifest = self.check(FakeProbe(versions={'.venv': versions}))
        entry = manifest['environments']['.venv']
        self.assertFalse(manifest['ready'])
        self.assertEqual(entry['status'], 'not_ready')
        joined = '；'.join(entry['problems'])
        self.assertIn('1.9.0', joined)
        self.assertIn('>=1.11,<1.12', joined)

    def test_probe_failure_is_not_ready(self):
        self.make_env('.venv')
        manifest = self.check(FakeProbe(errors={'.venv': 'ImportError: broken interpreter'}))
        entry = manifest['environments']['.venv']
        self.assertFalse(manifest['ready'])
        self.assertEqual(entry['status'], 'not_ready')
        self.assertIn('探测失败', '；'.join(entry['problems']))

    # ------------------------------------------------------------- 全部满足
    def test_ready_manifest_contains_structured_fields(self):
        self.make_env('.venv')
        manifest = self.check(FakeProbe())
        self.assertTrue(manifest['ready'])
        self.assertEqual(manifest['product'], 'CellFlow')
        self.assertEqual(manifest['product_version'], '9.9.9')
        self.assertEqual(manifest['generated_at'], '2026-10-25T20:00:00+08:00')
        self.assertEqual(manifest['environments']['.venv']['python_version'], '3.11.15')
        self.assertEqual(manifest['checker_python']['version'], sys.version.split()[0])
        self.assertTrue(all(item['ok'] for item in
                            manifest['environments']['.venv']['packages'].values()))
        # 机器可读 JSON 必须能解析，且包含产品版本与检查时间
        payload = json.loads(install.manifest_json(manifest))
        self.assertEqual(payload['product_version'], '9.9.9')
        self.assertIn('generated_at', payload)

    # ------------------------------------------------------------- 隔离环境
    def test_isolated_environment_uses_its_own_interpreter(self):
        self.make_env('.venv')
        velovi = self.make_env('.venv-velovi')
        manifest = self.check(FakeProbe())
        self.assertTrue(manifest['ready'])
        self.assertTrue(manifest['environments']['.venv-velovi']['optional'])
        calls = {Path(python).parent.parent.name: modules for python, modules in
                 self._last_probe.calls}
        self.assertIn('scvi', calls['.venv-velovi'])
        self.assertIn('torch', calls['.venv-velovi'])
        self.assertIn(str(velovi), [str(python) for python, _modules in self._last_probe.calls])

    def test_present_but_broken_isolated_environment_is_not_ready(self):
        self.make_env('.venv')
        self.make_env('.venv-velovi')
        manifest = self.check(FakeProbe(missing={'.venv-velovi': {'scvi'}}))
        self.assertFalse(manifest['ready'])
        self.assertEqual(manifest['environments']['.venv']['status'], 'ready')
        self.assertEqual(manifest['environments']['.venv-velovi']['status'], 'not_ready')
        self.assertTrue(any('.venv-velovi' in item for item in manifest['problems']))

    # ------------------------------------------------------------- 只读
    def test_check_does_not_create_or_modify_files(self):
        self.make_env('.venv')
        self.make_env('.venv-velovi')

        def snapshot():
            state = {}
            for path in sorted(self.root.rglob('*')):
                if path.is_file():
                    state[str(path.relative_to(self.root))] = path.read_bytes()
                else:
                    state[str(path.relative_to(self.root))] = None
            return state

        before = snapshot()
        manifest = self.check(FakeProbe())
        self.assertTrue(manifest['ready'])
        self.assertEqual(before, snapshot())


class VersionSatisfiesTests(unittest.TestCase):
    def test_lower_and_upper_bounds(self):
        self.assertTrue(install.version_satisfies('1.11.5', '>=1.11,<1.12'))
        self.assertFalse(install.version_satisfies('1.10.9', '>=1.11,<1.12'))
        self.assertFalse(install.version_satisfies('1.12.0', '>=1.11,<1.12'))

    def test_local_version_suffix_is_ignored(self):
        self.assertTrue(install.version_satisfies('2.5.1+cpu', '>=2.5,<2.6'))
        self.assertFalse(install.version_satisfies('2.6.0', '>=2.5,<2.6'))

    def test_missing_or_unparsable_version_fails_closed(self):
        self.assertFalse(install.version_satisfies(None, '>=1.0'))
        self.assertFalse(install.version_satisfies('unknown', '>=1.0'))
        self.assertTrue(install.version_satisfies('1.0', ''))


class RealProbeTests(unittest.TestCase):
    """真实子进程探测：确认 PROBE_JSON 协议能被解析（只用标准库 json）。"""

    def test_real_probe_roundtrip(self):
        report = install.probe_environment(sys.executable, [('json', 'json', '')])
        self.assertFalse(report.get('error'))
        self.assertEqual(report['python_version'], platform.python_version())
        self.assertTrue(report['packages']['json']['imported'])


if __name__ == '__main__':
    unittest.main()
