"""隔离环境健康：真实探测模块是否可导入（spec 级，快速）。"""
import sys
import tempfile
import unittest
from pathlib import Path

from cellflow.registry import _probe_ready


class IsolatedHealth(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def test_existing_module_is_ready(self):
        self.assertTrue(_probe_ready(sys.executable, ['json'], root=self.root))

    def test_missing_module_is_not_ready(self):
        self.assertFalse(_probe_ready(sys.executable, ['cellflow_missing_pkg_xyz'], root=self.root))

    def test_cache_file_is_written(self):
        _probe_ready(sys.executable, ['json'], root=self.root)
        cache = self.root / 'results' / 'isolated-health.json'
        self.assertTrue(cache.is_file())
        self.assertIn('json', cache.read_text(encoding='utf-8'))

    def test_broken_interpreter_is_not_ready(self):
        fake = self.root / 'nope.exe'
        self.assertFalse(_probe_ready(fake, ['json'], root=self.root, timeout=5))


if __name__ == '__main__':
    unittest.main()
