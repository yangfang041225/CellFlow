import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import numpy as np
from cellflow.runner import _quality_report


class FateQualityGate(unittest.TestCase):
    def test_invalid_fates_are_hard_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / 'figures').mkdir()
            (folder / 'figures' / 'test.svg').write_text('<svg/>')
            data = SimpleNamespace(X=np.ones((2, 5)), genes=['g'] * 5, cells=['a', 'b'])
            for probabilities in [[[.4, .6], [.5, .5]],
                                  [[-.01, 1.01], [.5, .5]],
                                  [[.2, .2], [.5, .5]],
                                  [[float('nan'), 1], [.5, .5]],
                                  [[1], [1]]]:
                result = {'fate': {'terminal_states': ['A', 'B'], 'probabilities': probabilities}}
                (folder / 'result.json').write_text(json.dumps(result))
                report = _quality_report(folder, result, data)
                self.assertEqual(report['ok'], probabilities == [[.4, .6], [.5, .5]])
