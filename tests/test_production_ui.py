"""The existing Windows CI test-discovery step also gates the production UI path."""
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(os.name == 'nt', 'Production desktop acceptance targets Windows')
class ProductionUi(unittest.TestCase):
    def test_qt_react_declaration_to_completed_computation(self):
        root = Path(__file__).resolve().parents[1]
        self.assertTrue((root / 'web/dist/index.html').is_file(), 'Build web/dist before acceptance')
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            output = Path(temporary)
            env = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONIOENCODING='utf-8',
                       OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
            proc = subprocess.run([sys.executable, '-m', 'tools.acceptance_ui', '--output', str(output)],
                                  cwd=root, env=env, capture_output=True, timeout=180)
            artifact = root / '.tmp' / 'ui-acceptance'
            artifact.mkdir(parents=True, exist_ok=True)
            for path in output.iterdir():
                if path.is_file() and (path.suffix == '.png' or path.name in ('result.json', 'api.log', 'qt-native.log')):
                    shutil.copy2(path, artifact / path.name)
            evidence = output / 'result.json'
            detail = evidence.read_text(encoding='utf-8') if evidence.exists() else ''
            native_log = output / 'qt-native.log'
            if proc.returncode and native_log.exists():
                detail += native_log.read_text(encoding='utf-8', errors='replace')[-4000:]
            self.assertEqual(proc.returncode, 0, detail or repr(proc.stderr[-2000:]))
            result = json.loads(evidence.read_text(encoding='utf-8'))
            self.assertTrue(result['ok'], detail)
            self.assertEqual(len([item for item in result['checks'] if 'view' in item]), 5)
            self.assertTrue(any(item.get('quality_ok') for item in result['checks']))
