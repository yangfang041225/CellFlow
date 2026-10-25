"""磁盘余量门禁：空间不足时任务明确失败，不产生半成品完成。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cellflow.memory as memory
from cellflow.runner import run

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / 'examples' / 'synthetic.npz'


class DiskGuard(unittest.TestCase):
    def test_require_disk_rejects_low_space(self):
        with mock.patch.object(memory, 'disk_free', return_value=0):
            with self.assertRaises(OSError):
                memory.require_disk(tempfile.gettempdir(), min_gb=1.0)

    def test_runner_fails_cleanly_when_disk_low(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch('cellflow.memory.require_disk',
                            side_effect=OSError('磁盘空间不足：test')):
                provenance = run(DEMO, {'method': 'qc', 'seed': 1, 'raw_x_confirmed': True},
                                 Path(folder) / 'run')
            self.assertEqual(provenance['status'], 'failed')
            self.assertIn('磁盘空间不足', provenance.get('error', ''))
            self.assertFalse((Path(folder) / 'run' / 'result.json').exists())


if __name__ == '__main__':
    unittest.main()
