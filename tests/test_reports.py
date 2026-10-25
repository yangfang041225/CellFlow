"""图形参数：箭头密度与显示缩放（只影响 SVG，不改速度值）。"""
import re
import unittest

import numpy as np

from cellflow.reports import scatter_svg


class Arrows(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(1)
        self.coords = rng.normal(size=(120, 2))
        self.arrows = np.ones((120, 2))
        self.color = np.linspace(0, 1, 120)

    def _count(self, svg):
        return svg.count('marker-end="url(#arrow)"')

    def test_density_controls_arrow_count(self):
        low = scatter_svg(self.coords, self.color, 't', list(range(120)), self.arrows,
                          arrow_density=10)
        high = scatter_svg(self.coords, self.color, 't', list(range(120)), self.arrows,
                           arrow_density=120)
        self.assertLessEqual(self._count(low), 12)
        self.assertGreaterEqual(self._count(high), 100)

    def test_scale_changes_display_length_only(self):
        base = scatter_svg(self.coords, self.color, 't', list(range(120)), self.arrows,
                           arrow_scale=1.0, arrow_density=20)
        big = scatter_svg(self.coords, self.color, 't', list(range(120)), self.arrows,
                          arrow_scale=2.0, arrow_density=20)

        def first_length(svg):
            match = re.search(r'l(-?[\d.]+) (-?[\d.]+)', svg)
            return float(match.group(1)) if match else None

        small_len = first_length(base)
        big_len = first_length(big)
        self.assertIsNotNone(small_len)
        self.assertIsNotNone(big_len)
        self.assertAlmostEqual(big_len, small_len * 2, delta=abs(small_len) * 0.1 + 0.5)

    def test_note_states_display_only(self):
        svg = scatter_svg(self.coords, self.color, 't', [0] * 120, self.arrows)
        self.assertIn('display only', svg)

    def test_stream_png_writes_png(self):
        import tempfile
        from pathlib import Path
        from cellflow.reports import stream_png
        out = Path(tempfile.mkdtemp()) / 'stream.png'
        self.assertIsNotNone(stream_png(self.coords, self.arrows, out), 'matplotlib 不可用？')
        self.assertTrue(out.read_bytes().startswith(bytes([0x89]) + b'PNG'))


if __name__ == '__main__':
    unittest.main()