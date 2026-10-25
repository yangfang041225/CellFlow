"""图形几何/语义与报告导出的回归测试（审查项 F12/F13/F14/F17/F18）。

覆盖：
- F12 箭头几何：点坐标与速度向量必须经过**同一个**仿射变换；基向量、45° 向量的屏幕方向
  与仿射矩阵一致（stretch 模式下 10:1 数据的 45° 向量屏幕斜率 = 410/70 ≈ 5.857；
  equal 模式下保角，仍是 45°）。
- F13 绘图语义：图标题/图注由同一份 figure_specs 生成；颜色字段跟随标题实际声明的语义
  （命运概率 / 群标签 / 时间）；PAGA 使用原始 connectivities 与固定 0–1 色阶，不逐行归一化。
- F14 报告真实性：速度模式取自 calls.json 的真实调用；auto 未计算速度时不得宣称 scVelo；
  deterministic/dynamical 分支各自准确；推断值不得放进“直接观察”。
- F17 PDF：实际生成的 PDF 是 A4、正文 7–10 pt、内容铺满页面、页眉/页码存在、分页正确。
- F18 ZIP：导出清单包含被 HTML/Markdown 引用的 figures/*.png + *.svg（含 stream.png），
  导出前校验引用存在，打包后离线可打开。
"""
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np

from cellflow import reports

ROOT = Path(__file__).resolve().parents[1]
ARROW_RE = re.compile(r'd="M([\d.\-]+) ([\d.\-]+) l([\d.\-]+) ([\d.\-]+)"')


def _arrow_vectors(svg):
    return [(float(m.group(3)), float(m.group(4))) for m in ARROW_RE.finditer(svg)]


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


class ArrowAffineF12(unittest.TestCase):
    """F12：坐标与向量共用同一个仿射矩阵。"""

    def setUp(self):
        # 数据跨度 10:1，向量 45°，与审查探针一致
        self.coords = np.array([[0.0, 0.0], [10.0, 1.0]])
        self.arrows = np.array([[1.0, 1.0], [1.0, 1.0]])

    def test_affine_maps_points_and_basis_vectors_identically(self):
        affine = reports.ScreenAffine((0.0, 0.0), (10.0, 1.0),
                                     reports.PLOT_BOX, reports.PLOT_ORIGIN, 'stretch')
        # 基向量：x → (sx, 0)，y → (0, −sy)。同一矩阵作用在点上时位移必须一致。
        self.assertAlmostEqual(affine.vector(1.0, 0.0)[0], affine.sx, places=9)
        self.assertAlmostEqual(affine.vector(1.0, 0.0)[1], 0.0, places=9)
        self.assertAlmostEqual(affine.vector(0.0, 1.0)[0], 0.0, places=9)
        self.assertAlmostEqual(affine.vector(0.0, 1.0)[1], -affine.sy, places=9)
        origin = affine.point(0.0, 0.0)
        step_x = affine.point(1.0, 0.0)
        step_y = affine.point(0.0, 1.0)
        self.assertAlmostEqual(step_x[0] - origin[0], affine.vector(1.0, 0.0)[0], places=9)
        self.assertAlmostEqual(step_x[1] - origin[1], affine.vector(1.0, 0.0)[1], places=9)
        self.assertAlmostEqual(step_y[1] - origin[1], affine.vector(0.0, 1.0)[1], places=9)

    def test_stretch_45deg_screen_slope_matches_probe_5857(self):
        svg = reports.scatter_svg(self.coords, [0.0, 1.0], 'probe', ['a', 'b'],
                                  self.arrows, arrow_density=20, aspect='stretch')
        dx, dy = _arrow_vectors(svg)[0]
        slope = abs(dy / dx)
        # 旧实现用原始单位向量绘制 → 斜率恒为 1；同一仿射后应为 sy/sx = 410/70 ≈ 5.857
        self.assertAlmostEqual(slope, 410.0 / 70.0, delta=0.02)

    def test_equal_mode_keeps_45deg(self):
        svg = reports.scatter_svg(self.coords, [0.0, 1.0], 'probe', ['a', 'b'],
                                  self.arrows, arrow_density=20, aspect='equal')
        dx, dy = _arrow_vectors(svg)[0]
        self.assertAlmostEqual(abs(dy / dx), 1.0, delta=0.02)
        affine = reports.ScreenAffine((0.0, 0.0), (10.0, 1.0),
                                      reports.PLOT_BOX, reports.PLOT_ORIGIN, 'equal')
        self.assertAlmostEqual(affine.sx, affine.sy, places=9)

    def test_basis_vectors_render_axis_aligned(self):
        svg = reports.scatter_svg(self.coords, [1.0, 0.0], 'basis', ['a', 'b'],
                                  np.array([[1.0, 0.0], [0.0, 1.0]]),
                                  arrow_density=20, aspect='stretch')
        (dx_x, dy_x), (dx_y, dy_y) = _arrow_vectors(svg)[:2]
        self.assertAlmostEqual(dy_x, 0.0, delta=1e-6, msg='x 基向量必须水平')
        self.assertAlmostEqual(dx_y, 0.0, delta=1e-6, msg='y 基向量必须竖直')
        self.assertLess(dy_y, 0.0, '数据 y 向上，屏幕 y 向下')

    def test_report_figures_use_equal_aspect(self):
        self.assertEqual(reports.FIGURE_ASPECT, 'equal')
        result = {'embedding': [[0.0, 0.0], [1.0, 3.0], [2.0, 6.0]],
                  'embedding_basis': 'umap', 'embedding_name': 'UMAP 与速度投影',
                  'arrows': [[1.0, 2.0], [1.0, 2.0], [1.0, 2.0]]}
        spec = reports.figure_specs(result, {})['embedding.svg']
        self.assertEqual(spec['aspect'], 'equal')
        self.assertAlmostEqual(spec['affine']['sx'], spec['affine']['sy'], places=6)


class PagaSemanticsF13(unittest.TestCase):
    """F13：PAGA 原始矩阵、固定 0–1 色阶、不逐行归一化。"""

    MATRIX = [[0.2, 0.1, 0.05],
              [0.1, 0.4, 0.2],
              [0.05, 0.2, 0.6]]

    def _cells(self, svg):
        cells = {}
        pattern = re.compile(r'fill="(rgb\([^)]*\))"><title>([^<]*)</title></rect>')
        for fill, title in pattern.findall(svg):
            if ' → ' not in title:
                continue
            left, right = title.split(' → ')
            group, _, value = right.partition(':')
            cells[(left.strip(), group.strip())] = (fill, float(value))
        return cells

    @staticmethod
    def _expected(value):
        ratio = min(1.0, max(0.0, float(value)))
        return 'rgb(%d,%d,%d)' % (int(245 - 140 * ratio), int(250 - 90 * ratio),
                                  int(255 - 60 * ratio))

    def test_fixed_scale_and_no_row_normalization(self):
        svg = reports.matrix_heatmap_svg('PAGA', ['0', '1', '2'], self.MATRIX)
        cells = self._cells(svg)
        self.assertEqual(len(cells), 9)
        # 原始值：0.2 仍按 0–1 色阶着色，而不是被行归一化乘成 1.0
        self.assertEqual(cells[('0', '0')][0], self._expected(0.2))
        self.assertNotEqual(cells[('0', '0')][0], self._expected(1.0))
        self.assertAlmostEqual(cells[('0', '0')][1], 0.2, places=6)
        self.assertIn('fixed color scale 0–1', svg)
        self.assertIn('not row-normalized', svg)

    def test_symmetry_is_preserved(self):
        svg = reports.matrix_heatmap_svg('PAGA', ['0', '1', '2'], self.MATRIX)
        cells = self._cells(svg)
        for left, right in list(cells):
            if left < right:
                self.assertEqual(cells[(left, right)][0], cells[(right, left)][0],
                                 f'{left}→{right} 与 {right}→{left} 颜色必须相同')

    def test_asymmetric_input_is_not_faked_symmetric(self):
        matrix = [[0.2, 0.9], [0.1, 0.4]]
        svg = reports.matrix_heatmap_svg('PAGA', ['0', '1'], matrix)
        cells = self._cells(svg)
        self.assertNotEqual(cells[('0', '1')][0], cells[('1', '0')][0])


class CaptionSemanticsF13(unittest.TestCase):
    """F13：图标题/图注由同一份实际着色字段生成。"""

    def setUp(self):
        self.qc = {'total': [10.0, 20.0, 30.0], 'detected': [5.0, 6.0, 7.0],
                   'mt_pct': [1.0, 2.0, 3.0]}
        self.result = {
            'embedding': [[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]],
            'embedding_basis': 'umap',
            'embedding_name': 'UMAP（按主导命运概率着色）',
            'arrows': [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]],
            'time': [0.0, 0.5, 1.0],
            'time_name': '速度伪时序',
            'groups': {'key': 'leiden', 'labels': ['0', '1', '0'], 'sizes': {'0': 2, '1': 1}},
            'fate': {'terminal_states': ['A', 'B'],
                     'probabilities': [[0.9, 0.1], [0.2, 0.8], [0.5, 0.5]],
                     'estimator': 'GPCCA', 'kernel': 'VelocityKernel(velocity_graph)'},
            'paga': {'groups': ['0', '1'], 'connectivities': [[1.0, 0.4], [0.4, 1.0]]},
        }

    def test_fate_claim_uses_probability_field(self):
        spec = reports.figure_specs(self.result, self.qc)['embedding.svg']
        self.assertEqual(spec['color']['field'], 'fate.probabilities')
        self.assertEqual(spec['color']['kind'], 'continuous')
        self.assertTrue(spec['color'].get('fixed'))
        self.assertIn('命运概率', spec['title'])
        self.assertIn('fate.probabilities', spec['caption'])
        caption = reports._figure_caption('embedding.svg', self.result, {}, 1, qc=self.qc)
        self.assertIn('fate.probabilities', caption)

    def test_group_claim_uses_group_labels(self):
        result = dict(self.result, embedding_name='UMAP（按群标签着色）')
        spec = reports.figure_specs(result, self.qc)['embedding.svg']
        self.assertEqual(spec['color']['kind'], 'categorical')
        self.assertEqual(spec['color']['field'], 'groups.labels')
        self.assertIn('群', spec['color']['label'])
        self.assertIn('群', spec['title'])

    def test_missing_declared_field_is_reported_not_faked(self):
        result = dict(self.result, fate={})
        spec = reports.figure_specs(result, self.qc)['embedding.svg']
        self.assertNotEqual(spec['color']['field'], 'fate.probabilities')
        self.assertIn('字段缺失', spec['caption'])
        self.assertNotIn('命运概率', spec['title'])

    def test_titles_and_captions_come_from_same_spec(self):
        specs = reports.figure_specs(self.result, self.qc)
        for name, spec in specs.items():
            self.assertIn('title', spec, name)
            self.assertIn('caption', spec, name)
        # 图注里出现的颜色字段必须与标题里的着色字段一致
        embedding = specs['embedding.svg']
        self.assertIn(str(embedding['color']['label']), embedding['title'])
        self.assertIn(str(embedding['color']['label']), embedding['caption'])

    def test_static_limits_of_each_figure(self):
        specs = reports.figure_specs(self.result, self.qc)
        self.assertIn('不代表真实谱系', specs['paga.svg']['caption'])
        self.assertIn('模型推断', specs['fate.svg']['caption'])
        self.assertIn('fate.probabilities', specs['fate.svg']['caption'])
        self.assertIn('不自动过滤', specs['qc.svg']['caption'])
        self.assertIn('归一化', specs['embedding.svg']['caption'])
        caption = reports._figure_caption('paga.svg', {}, {}, 3)
        self.assertIn('不代表真实谱系', caption)
        self.assertIn('固定 0–1', caption)

    def test_stream_png_figure_is_registered(self):
        specs = reports.figure_specs(self.result, self.qc)
        self.assertIn('stream.png', specs)
        self.assertIn('同一仿射', specs['stream.png']['caption'])


class NarrativeF14(unittest.TestCase):
    """F14：解释分层依据真实执行记录。"""

    def _calls(self, folder, calls):
        _write_json(Path(folder) / 'calls.json', calls)

    def test_velocity_mode_comes_from_real_call(self):
        with tempfile.TemporaryDirectory() as folder:
            self._calls(folder, [
                {'function': 'scvelo.tl.recover_dynamics', 'arguments': {'n_jobs': 1}},
                {'function': 'scvelo.tl.velocity', 'arguments': {'mode': 'dynamical'}},
            ])
            info = reports.executed_steps(
                folder, {'velocity': 'velocity.npz', 'arrows': [[0.0, 0.0]]},
                {'method': 'auto', 'velocity_model': 'stochastic'})
        self.assertEqual(info['velocity_mode'], 'dynamical')
        self.assertIn('recover_dynamics', info['steps'])
        self.assertTrue(info['has_velocity'])

    def test_auto_without_velocity_does_not_claim_scvelo(self):
        text = reports.interpretation_layers(
            {'metrics': {'n_groups': 2},
             'groups': {'key': 'leiden', 'sizes': {'0': 2, '1': 1}}},
            {'method': 'auto', 'velocity_model': 'stochastic'}, 'auto')
        self.assertIn('没有执行 RNA velocity', text)
        self.assertNotIn('scVelo（stochastic 模式）', text)
        self.assertIn('直接观察', text)
        self.assertIn('计算推断', text)
        self.assertIn('生物学假说', text)

    def test_headline_does_not_overclaim_for_auto_without_velocity(self):
        info = {'steps': ['embedding', 'clustering'], 'has_velocity': False, 'has_fate': False}
        line = reports._headline('auto', info, None)
        self.assertIn('没有', line)
        self.assertIn('RNA velocity', line)

    def test_deterministic_branch_is_labeled(self):
        result = {'velocity': 'velocity.npz', 'arrows': [[0.0, 0.0]],
                  'embedding': [[0.0, 0.0]],
                  'metrics': {'velocity_length_median': 1.0}}
        text = reports.interpretation_layers(result, {'method': 'scvelo_deterministic'},
                                             'scvelo_deterministic')
        self.assertIn('deterministic', text)
        self.assertIn('稳态模型', text)
        self.assertIn('没有 recover_dynamics', text)
        self.assertNotIn('dynamical 模式先执行 recover_dynamics', text)

    def test_dynamical_branch_mentions_latent_time(self):
        with tempfile.TemporaryDirectory() as folder:
            self._calls(folder, [
                {'function': 'scvelo.tl.recover_dynamics', 'arguments': {}},
                {'function': 'scvelo.tl.velocity', 'arguments': {'mode': 'dynamical'}},
                {'function': 'scvelo.tl.latent_time', 'arguments': {}},
            ])
            text = reports.interpretation_layers(
                {'velocity': 'velocity.npz', 'arrows': [[0.0, 0.0]],
                 'embedding': [[0.0, 0.0]],
                 'time': [0.0], 'time_name': 'scVelo 潜在时间（模型内部时间，无实验标定）'},
                {'method': 'auto'}, 'auto', folder=folder)
        self.assertIn('dynamical', text)
        self.assertIn('潜在时间', text)
        self.assertIn('recover_dynamics', text)

    def test_inferred_numbers_are_not_listed_as_observations(self):
        text = reports.interpretation_layers(
            {'metrics': {'velocity_length_median': 1.2,
                         'mean_max_fate_probability': 0.9}},
            {}, 'auto')
        observed_end = text.index('派生统计')
        self.assertNotIn('每细胞最大命运概率的均值 0.90', text[:observed_end])
        self.assertGreater(text.index('每个细胞最大命运概率的均值'), observed_end)
        self.assertGreater(text.index('速度向量长度中位数'), observed_end)

    def test_old_evidence_note_still_lists_layers(self):
        text = reports.interpretation_layers(
            {'metrics': {'n_groups': 4, 'velocity_length_median': 1.2,
                         'velocity_confidence_median': 0.7, 'n_terminal_states': 2},
             'groups': {'key': 'leiden'},
             'fate': {'states': ['0', '3']},
             'gene_table': [{'gene': 'A', 'diff': 1.0}]},
            {'velocity_model': 'stochastic'}, 'auto')
        for word in ('直接观察', '计算推断', '生物学假说', '不代表标记基因', '需要独立证据'):
            self.assertIn(word, text)


class BundleF18(unittest.TestCase):
    """F18：导出清单覆盖被引用的 png/svg（含 stream.png）并可离线打开。"""

    def _folder(self, root):
        folder = Path(root)
        (folder / 'figures').mkdir()
        (folder / 'figures' / 'qc.svg').write_text('<svg/>', encoding='utf-8')
        (folder / 'figures' / 'embedding.svg').write_text('<svg/>', encoding='utf-8')
        (folder / 'figures' / 'stream.png').write_bytes(b'\x89PNG\r\n\x1a\nfake')
        (folder / 'figures' / 'unreferenced.svg').write_text('<svg/>', encoding='utf-8')
        (folder / 'A_生物学分析报告.md').write_text(
            '# A\n\n![质控](figures/qc.svg)\n![嵌入](figures/embedding.svg)\n'
            '![流线](figures/stream.png)\n', encoding='utf-8')
        (folder / 'A_report.html').write_text(
            '<html><body><img src="figures/qc.svg"><img src="figures/stream.png"></body></html>',
            encoding='utf-8')
        (folder / 'B_技术与复现报告.md').write_text('# B\n', encoding='utf-8')
        (folder / 'B_report.html').write_text('<html><body>B</body></html>', encoding='utf-8')
        (folder / 'result.json').write_text('{}', encoding='utf-8')
        return folder

    def test_bundle_entries_include_referenced_png_and_svg(self):
        with tempfile.TemporaryDirectory() as root:
            folder = self._folder(root)
            entries = reports.bundle_entries(folder)
            names = {arcname for _, arcname in entries}
            for expected in ('figures/qc.svg', 'figures/embedding.svg', 'figures/stream.png',
                             'A_生物学分析报告.md', 'A_report.html', 'result.json'):
                self.assertIn(expected, names)
            # 未被引用但存在于 figures 的图也一并带走，避免出现半套图
            self.assertIn('figures/unreferenced.svg', names)

    def test_all_references_exist_before_export(self):
        with tempfile.TemporaryDirectory() as root:
            folder = self._folder(root)
            self.assertEqual(reports.missing_report_refs(folder), {})
            refs = reports.report_refs(folder)
            self.assertIn('figures/stream.png', refs['A_生物学分析报告.md'])
            self.assertIn('figures/stream.png', refs['A_report.html'])

    def test_missing_stream_png_is_detected(self):
        with tempfile.TemporaryDirectory() as root:
            folder = self._folder(root)
            (folder / 'figures' / 'stream.png').unlink()
            missing = reports.missing_report_refs(folder)
            self.assertIn('figures/stream.png', missing.get('A_生物学分析报告.md', []))
            names = {arcname for _, arcname in reports.bundle_entries(folder)}
            self.assertNotIn('figures/stream.png', names)

    def test_zip_from_bundle_entries_opens_offline(self):
        with tempfile.TemporaryDirectory() as root:
            folder = self._folder(root)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
                for path, arcname in reports.bundle_entries(folder):
                    archive.write(path, arcname)
            payload = buffer.getvalue()
            self.assertTrue(payload.startswith(b'PK'))
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                names = set(archive.namelist())
                self.assertIn('figures/stream.png', names)
            # 离线包：HTML 里每个本地引用都能在包内找到
            html = (folder / 'A_report.html').read_text(encoding='utf-8')
            for ref in re.findall(r'src="(figures/[^"]+)"', html):
                self.assertIn(ref, names, f'{ref} 不在 ZIP 内，离线打开会断图')


class PdfGeometryF17(unittest.TestCase):
    """F17：真实 PDF 的页面尺寸、正文字号、页眉页码与分页。"""

    @classmethod
    def setUpClass(cls):
        try:
            from pypdf import PdfReader  # noqa: F401
        except Exception as exc:  # pragma: no cover - 环境缺 pypdf 时跳过
            raise unittest.SkipTest(f'pypdf 不可用：{exc}')
        try:
            import PySide6  # noqa: F401
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f'PySide6 不可用：{exc}')
        cls.tmp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.tmp.name)
        cls.html = cls.folder / 'report.html'
        cls.pdf = cls.folder / 'report.pdf'
        paragraphs = '\n'.join(
            f'<p>Para-{index:03d}：这是一段用于分页与字号检查的中文正文，'
            f'包含数字 {index} 与英文 CellFlow。</p>' for index in range(1, 121))
        cls.html.write_text(
            '<!doctype html><meta charset="utf-8"><style>'
            'body{font-family:"Microsoft YaHei";font-size:10.5pt;line-height:1.55;margin:0}'
            'h1{font-size:20pt}h2{font-size:15pt}table{font-size:9.5pt}'
            'figcaption{font-size:9pt}</style>'
            '<h1>生物学分析报告</h1><h2>1. 数据与输入</h2>'
            '<table><tr><td>指标</td><td>数值</td></tr><tr><td>细胞数</td><td>240</td></tr></table>'
            + paragraphs +
            '<figcaption>图 1 · 细胞在 UMAP 上的投影</figcaption>', encoding='utf-8')
        proc = subprocess.run(
            [sys.executable, '-m', 'cellflow.pdf', str(cls.html), str(cls.pdf),
             'CellFlow · 任务 test · 2026-10-25'],
            cwd=str(ROOT), capture_output=True, text=True, encoding='utf-8', timeout=180)
        if proc.returncode != 0 or not cls.pdf.exists():
            raise unittest.SkipTest('PDF 生成失败（本机 Qt 打印环境不可用）：'
                                    + (proc.stdout or '')[-500:] + (proc.stderr or '')[-500:])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _runs(self):
        from pypdf import PdfReader
        reader = PdfReader(str(self.pdf))
        pages = []
        for page in reader.pages:
            rows = []

            def visit(text, cm, tm, font_dict, font_size, _rows=rows):
                if not text or not text.strip():
                    return
                scale = abs(cm[0] * cm[3] - cm[1] * cm[2]) ** 0.5
                _rows.append((float(font_size) * scale,
                              tm[4] * cm[0] + tm[5] * cm[2] + cm[4],
                              tm[4] * cm[1] + tm[5] * cm[3] + cm[5], text))

            page.extract_text(visitor_text=visit)
            pages.append(rows)
        return reader, pages

    def test_page_is_a4_and_text_is_readable(self):
        reader, pages = self._runs()
        box = [float(value) for value in reader.pages[0].mediabox]
        self.assertAlmostEqual(box[2] - box[0], 595, delta=2)
        self.assertAlmostEqual(box[3] - box[1], 842, delta=2)
        sizes = sorted(size for page in pages for size, _, _, _ in page)
        self.assertTrue(sizes, 'PDF 里没有可提取的文字')
        median = sizes[len(sizes) // 2]
        # 修复前正文中位字号约 0.72 pt；修复后应落在 7–10 pt 区间
        self.assertGreaterEqual(median, 7.0, f'正文字号过小：{median}')
        self.assertLessEqual(median, 10.6, f'正文字号过大：{median}')
        self.assertGreaterEqual(min(sizes), 6.0, '页脚/小字不应小于 6 pt')

    def test_content_fills_page_and_has_header_footer(self):
        _, pages = self._runs()
        first = pages[0]
        xs = [x for _, x, _, _ in first]
        ys = [y for _, _, y, _ in first]
        self.assertGreater(max(xs) - min(xs), 300, '正文没有铺满页面宽度')
        self.assertGreater(max(ys) - min(ys), 400, '正文没有铺满页面高度')
        self.assertGreaterEqual(min(xs), 20.0)
        self.assertLessEqual(max(xs), 575.0)
        header = [text for size, _, y, text in first if 7.0 <= size <= 9.0 and y > 770]
        self.assertTrue(any('CellFlow' in text for text in header), '缺少页眉')
        self.assertTrue(any('页' in text or '/' in text for text in header), '缺少页码')

    def test_pagination_does_not_repeat_first_page(self):
        _, pages = self._runs()
        self.assertGreaterEqual(len(pages), 2, '长正文应分成多页')
        joined = [' '.join(text for _, _, _, text in page) for page in pages]
        first_page = next((index for index, text in enumerate(joined) if 'Para-001' in text), None)
        last_page = next((index for index, text in enumerate(joined) if 'Para-120' in text), None)
        self.assertIsNotNone(first_page, '第一段文本缺失')
        self.assertIsNotNone(last_page, '最后一段文本缺失')
        self.assertLess(first_page, last_page, '分页没有前进（可能在重复绘制第一页）')


if __name__ == '__main__':
    unittest.main()
