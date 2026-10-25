"""HTML 报告安全回归测试（审查项 F16）。

覆盖：
- 数据单元（数据集名、obs 值、基因名、config 文本、错误信息、路径）进入 Markdown 前
  必须 HTML 转义，恶意载荷只能以文本形式出现在最终 HTML；
- 最终 HTML 经过白名单清洗：无 script/iframe/object/embed/svg、无 on* 事件属性、
  无 javascript:/vbscript: URL，同时保留报告自身的 figure/表格/样式；
- 导出 HTML 带 CSP（default-src 'none'，允许内联样式与本地图片，不允许脚本）；
- 正常报告（中文、表格、代码块、图注、图片引用）不被清洗破坏。
"""
import json
import re
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from cellflow import reports

SCRIPT = '<script>alert(1)</script>'
IMG_EVENT = '<img src=x onerror=alert(2)>'
SVG_EVENT = '"><svg/onload=alert(3)>'
JS_URL = 'javascript:alert(4)'

FORBIDDEN_TAGS = {'script', 'noscript', 'iframe', 'frame', 'frameset', 'object', 'embed',
                  'applet', 'form', 'input', 'button', 'select', 'textarea', 'svg', 'math',
                  'template', 'base', 'link', 'video', 'audio', 'source', 'canvas'}


class HtmlAudit(HTMLParser):
    """解析最终 HTML，收集标签/属性做安全断言。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.attrs = []
        self.urls = []

    def handle_starttag(self, tag, attrs):
        self._record(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._record(tag, attrs)

    def _record(self, tag, attrs):
        self.tags.append(str(tag).lower())
        for name, value in attrs:
            name = str(name).lower()
            value = '' if value is None else str(value)
            self.attrs.append((str(tag).lower(), name, value))
            if name in ('href', 'src'):
                self.urls.append(value.strip().lower())


def audit(path):
    parser = HtmlAudit()
    text = Path(path).read_text(encoding='utf-8')
    parser.feed(text)
    parser.close()
    return text, parser


def _figures(folder):
    figures = Path(folder) / 'figures'
    figures.mkdir(parents=True)
    for name in ('qc.svg', 'embedding.svg', 'paga.svg', 'fate.svg', 'trends.svg', 'phase.svg'):
        (figures / name).write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>',
                                    encoding='utf-8')
    (figures / 'stream.png').write_bytes(b'\x89PNG\r\n\x1a\nx')


def _write(folder, config=None, facts=None, fmt=None, result=None, provenance=None,
           error=None, qc=None):
    """写一份报告；参数默认是良性数据，测试按需覆盖成恶意值。"""
    folder = Path(folder)
    _figures(folder)
    base_config = {'method': 'qc', 'species': '人', 'tissue': '血液', 'sample_key': 'donor',
                   'mt_prefix': 'MT-', 'n_top_genes': 100, 'n_neighbors': 15, 'n_pcs': 20,
                   'min_shared_counts': 5, 'max_iter': 10, 'leiden_resolution': 1.0,
                   'use_umap': True, 'seed': 1, 'velocity_model': 'stochastic'}
    base_fmt = {
        'file': {'name': 'input.h5ad', 'suffix': '.h5ad', 'size_text': '72 KB'},
        'shape': {'n_cells': 3, 'n_genes': 2},
        'matrices': {'X': {'dtype': 'float32', 'sparse': True, 'nonzero_pct': 42.0,
                           'integer_like': True, 'max': 9}},
        'layers_present': ['spliced', 'unspliced'],
        'obs': [{'name': 'donor'}],
        'var': {'id_kind': 'gene symbol', 'unique': True, 'mt_matched': 2},
        'splice': None, 'extras': None, 'warnings': ['X 计数看起来正常'],
    }
    base_qc = {'total': [10.0, 20.0, 30.0], 'detected': [5.0, 6.0, 7.0],
               'mt_pct': [1.0, 2.0, 3.0], 'mt_prefix': 'MT-', 'mt_genes_found': 2}
    base_result = {
        'embedding': [[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]],
        'embedding_basis': 'umap',
        'embedding_name': 'UMAP（按群标签着色）',
        'time': [0.0, 0.5, 1.0],
        'time_name': '速度伪时序',
        'groups': {'key': 'leiden', 'labels': ['0', '1', '0'],
                   'sizes': {'0': 2, '1': 1}},
        'fate': {'terminal_states': ['A'], 'probabilities': [[1.0], [1.0], [1.0]],
                 'estimator': 'GPCCA', 'kernel': 'VelocityKernel(velocity_graph)',
                 'terminal_state_stats': {'A': {'cells': 3, 'mean_time': 0.5}}},
        'paga': {'groups': ['0', '1'], 'connectivities': [[1.0, 0.4], [0.4, 1.0]]},
        'gene_table': [{'gene': 'CD3D', 'logFC': 1.2}],
        'metrics': {'n_groups': 2, 'velocity_length_median': 1.0},
        'gene_trends': [{'gene': 'CD3D', 'exploratory_spearman': 0.5,
                         'bins': [{'time': 0.0, 'mean_expression': 1.0},
                                  {'time': 1.0, 'mean_expression': 2.0}]}],
        'caveats': ['速度一致性是内部诊断。'],
        'evidence': ['Leiden/igraph 分群 + UMAP 降维（scanpy）'],
    }
    base_prov = {'python': '3.11', 'executable': 'C:/venv/python.exe', 'platform': 'Windows',
                 'device': 'cpu', 'thread_limits': {}, 'input_sha256': 'a' * 64,
                 'started_at': '2026-10-25T10:00:00', 'finished_at': '2026-10-25T10:00:05',
                 'elapsed_seconds': 5.0, 'status': 'completed', 'seed': 1,
                 'reproduce_cwd': 'C:/run/source', 'reproduce_command': 'python -m cellflow.cli',
                 'code_sha256': {'a.py': 'b' * 64}}
    base_facts = {'n_cells': 3, 'n_genes': 2, 'synthetic': True,
                  'warnings': ['模拟数据：仅验证流程'], 'errors': []}
    if config is not None:
        base_config = {**base_config, **config}
    if facts is not None:
        base_facts = {**base_facts, **facts}
    if fmt is not None:
        base_fmt = {**base_fmt, **fmt}
    if result is not None:
        base_result = {**base_result, **result}
    if provenance is not None:
        base_prov = {**base_prov, **provenance}
    if qc is not None:
        base_qc = {**base_qc, **qc}
    if error is None:
        error = ''
    reports.write_reports(folder, base_facts, base_qc, base_result or None, base_config,
                          base_prov, error=error or None, fmt=base_fmt)
    return folder


class SanitizerUnit(unittest.TestCase):
    """白名单清洗的单元级验证。"""

    def test_dangerous_tags_and_attributes_are_removed(self):
        markup = ('<p onclick="alert(1)" style="color:red">文字</p>'
                  '<script>alert(2)</script>'
                  '<iframe src="https://evil"></iframe>'
                  '<object data="x"></object><embed src="y">'
                  '<img src="x" onerror="alert(3)" alt="pic">'
                  '<a href="javascript:alert(4)">链接</a>'
                  '<svg onload="alert(5)"><text>svg</text></svg>'
                  '<marquee>滚动</marquee>')
        cleaned = reports.sanitize_html(markup)
        lowered = cleaned.lower()
        for bad in ('<script', '<iframe', '<object', '<embed', '<svg', 'onerror', 'onload',
                    'onclick', 'javascript:'):
            self.assertNotIn(bad, lowered, bad)
        self.assertIn('<p style="color:red">文字</p>', cleaned)
        self.assertIn('<img src="x" alt="pic"/>', cleaned)
        self.assertIn('链接', cleaned)
        self.assertIn('滚动', cleaned)

    def test_css_and_style_attack_payloads_removed(self):
        cleaned = reports.sanitize_html(
            '<style>body{color:red}@import url(http://evil/x.css)</style>'
            '<p style="background:url(javascript:alert(1))">x</p>')
        self.assertNotIn('@import', cleaned)
        self.assertNotIn('javascript:', cleaned.lower())
        self.assertIn('body{color:red}', cleaned)

    def test_report_markup_survives_and_sanitize_is_idempotent(self):
        markup = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                  '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'">'
                  '<title>报告</title><style>body{color:#20334b}</style></head><body>'
                  '<h1>报告</h1><figure><img src="figures/qc.svg" width="640" alt="qc">'
                  '<figcaption>图 1 · 质控</figcaption></figure>'
                  '<table><thead><tr><th>指标</th></tr></thead>'
                  '<tbody><tr><td>1</td></tr></tbody></table>'
                  '<pre><code>a &lt; b</code></pre>'
                  '<blockquote>引用</blockquote></body></html>')
        once = reports.sanitize_html(markup)
        twice = reports.sanitize_html(once)
        self.assertEqual(once, twice, '清洗必须幂等')
        for piece in ('<figure>', 'src="figures/qc.svg"', '<table>', '<thead>', '<th>指标</th>',
                      '<figcaption>图 1 · 质控</figcaption>', '<pre>', '<code>a &lt; b</code>',
                      '<style>body{color:#20334b}</style>', 'Content-Security-Policy'):
            self.assertIn(piece, once, piece)

    def test_url_scheme_filtering(self):
        cleaned = reports.sanitize_html(
            '<a href="  jav&#x09;ascript:alert(1)">a</a>'
            '<a href="//evil.example/x">b</a>'
            '<a href="https://ok.example/x">c</a>'
            '<a href="mailto:someone@example.org">d</a>'
            '<img src="data:text/html;base64,PHNjcmlwdD4=">'
            '<img src="data:image/png;base64,AAAA">')
        lowered = cleaned.lower()
        self.assertNotIn('javascript', lowered)
        self.assertNotIn('evil.example', cleaned)
        self.assertNotIn('data:text/html', lowered)
        self.assertIn('https://ok.example/x', cleaned)
        self.assertIn('mailto:someone@example.org', cleaned)
        self.assertIn('data:image/png', cleaned)


class ReportIsInert(unittest.TestCase):
    """恶意数据进入报告后，最终 HTML 不可执行、文本仍可见。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.folder = _write(Path(cls.tmp.name) / 'evil', config={
            'species': SCRIPT, 'tissue': SVG_EVENT, 'sample_key': IMG_EVENT,
            'mt_prefix': JS_URL,
        }, facts={
            'warnings': [SCRIPT, IMG_EVENT],
        }, fmt={
            'file': {'name': SCRIPT + '.h5ad', 'suffix': '.h5ad', 'size_text': '1 KB'},
            'layers_present': ['spliced', IMG_EVENT],
            'obs': [{'name': SVG_EVENT}],
            'var': {'id_kind': SVG_EVENT, 'unique': True, 'mt_matched': 0},
            'splice': None, 'extras': None, 'warnings': [SVG_EVENT, IMG_EVENT],
        }, result={
            'groups': {'key': SCRIPT, 'labels': ['0', '1', '0'],
                       'sizes': {SCRIPT: 2, IMG_EVENT: 1}},
            'gene_table': [{'gene': IMG_EVENT, 'logFC': 1.0}],
            'paga': {'groups': [SCRIPT, SVG_EVENT],
                     'connectivities': [[1.0, 0.5], [0.5, 1.0]]},
            'fate': {'terminal_states': [IMG_EVENT, SCRIPT],
                     'probabilities': [[0.9, 0.1], [0.2, 0.8], [0.5, 0.5]],
                     'estimator': SVG_EVENT, 'kernel': IMG_EVENT,
                     'terminal_state_stats': {SCRIPT: {'cells': 1, 'mean_time': 0.2}}},
            'caveats': [SCRIPT], 'evidence': [SVG_EVENT],
            'velocity_table': [{'gene': IMG_EVENT, 'fit_likelihood': 0.5}],
            'gene_trends': [{'gene': SVG_EVENT, 'exploratory_spearman': 0.5,
                             'bins': [{'time': 0.0, 'mean_expression': 1.0}]}],
            'metrics': {'n_groups': 2, SCRIPT: 1.0},
        }, provenance={
            'python': SCRIPT, 'executable': IMG_EVENT, 'platform': SVG_EVENT,
            'thread_limits': {SCRIPT: IMG_EVENT}, 'input_sha256': SCRIPT,
            'started_at': SVG_EVENT, 'reproduce_cwd': SVG_EVENT,
            'reproduce_command': SCRIPT,
        }, qc={'mt_prefix': SCRIPT})

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_final_html_has_no_executable_markup(self):
        for name in ('A_report.html', 'B_report.html'):
            text, parsed = audit(self.folder / name)
            self.assertFalse(FORBIDDEN_TAGS & set(parsed.tags),
                             f'{name} 出现危险标签：{FORBIDDEN_TAGS & set(parsed.tags)}')
            bad_attrs = [(tag, attr) for tag, attr, _ in parsed.attrs
                         if attr.startswith('on') or attr in ('srcdoc', 'formaction')]
            self.assertEqual(bad_attrs, [], f'{name} 出现事件属性')
            bad_urls = [url for url in parsed.urls
                        if url.startswith(('javascript:', 'vbscript:', 'data:text/html'))]
            self.assertEqual(bad_urls, [], f'{name} 出现伪协议 URL')
            self.assertNotIn('<script', text.lower())

    def test_payload_text_is_visible_but_escaped(self):
        text, _ = audit(self.folder / 'A_report.html')
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', text)
        self.assertIn('&lt;img src=x onerror=alert(2)&gt;', text)
        self.assertIn('&lt;svg/onload=alert(3)&gt;', text)
        self.assertIn('alert(1)', text)
        self.assertIn('alert(2)', text)

        markdown = (self.folder / 'A_生物学分析报告.md').read_text(encoding='utf-8')
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', markdown)
        # 行内代码/围栏代码里可以保留原文，但必须不在可执行的标签上下文
        outside_code = re.sub(r'`[^`]*`', '', markdown)
        self.assertNotIn('<script', outside_code.lower())

    def test_csp_meta_present_and_blocks_scripts(self):
        text = (self.folder / 'A_report.html').read_text(encoding='utf-8')
        match = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)"', text)
        self.assertIsNotNone(match, '缺少 CSP meta')
        policy = match.group(1)
        self.assertIn("default-src 'self'", policy)          # 默认仅本站资源
        self.assertIn("script-src 'none'", policy)           # 不允许任何脚本
        self.assertIn("style-src 'self' 'unsafe-inline'", policy)   # 允许内联样式
        self.assertNotIn("file:", policy)  # 不允许任意本机 file URL
        self.assertIn("object-src 'none'", policy)
        self.assertNotIn("'unsafe-eval'", policy)
        self.assertNotIn('<script', text.lower())


class NormalReportUnchanged(unittest.TestCase):
    """良性报告仍保留中文、表格、代码块、图注与图片引用。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.folder = _write(Path(cls.tmp.name) / 'normal',
                            result={'gene_table': [{'gene': 'A&B', 'logFC': 1.25}]})

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_markdown_structure_intact(self):
        markdown = (self.folder / 'A_生物学分析报告.md').read_text(encoding='utf-8')
        self.assertIn('# A 生物学分析报告', markdown)
        self.assertIn('## 4. 本次实际得到的结果', markdown)
        self.assertIn('| 基因 | logFC |', markdown)
        self.assertIn('![数据质量](figures/qc.svg)', markdown)
        self.assertIn('### 4.12 图注', markdown)
        self.assertIn('图 1 ·', markdown)
        tech = (self.folder / 'B_技术与复现报告.md').read_text(encoding='utf-8')
        self.assertIn('## 4. 超参数快照', tech)
        self.assertIn('```json', tech)
        self.assertIn('## 8. 结果文件清单', tech)

    def test_html_keeps_figures_tables_and_captions(self):
        text, parsed = audit(self.folder / 'A_report.html')
        self.assertGreaterEqual(text.count('<img src="figures/'), 6)
        self.assertIn('<table>', text)
        self.assertIn('<th>', text)
        self.assertIn('<figcaption>', text)
        self.assertIn('figures/stream.png', text)
        self.assertIn('Content-Security-Policy', text)
        self.assertFalse(FORBIDDEN_TAGS & set(parsed.tags))

    def test_benign_ampersand_is_not_double_escaped(self):
        text, _ = audit(self.folder / 'A_report.html')
        self.assertIn('A&amp;B', text)
        self.assertNotIn('A&amp;amp;B', text)

    def test_error_report_keeps_fenced_block_and_is_inert(self):
        with tempfile.TemporaryDirectory() as name:
            folder = _write(Path(name) / 'failed',
                            error=SCRIPT + ' Traceback ``` 围栏 ``` ' + IMG_EVENT)
            html = (folder / 'A_report.html').read_text(encoding='utf-8')
            self.assertNotIn('<script', html.lower())
            self.assertIn('&lt;script&gt;', html)
            self.assertIn('<pre>', html)
            markdown = (folder / 'A_生物学分析报告.md').read_text(encoding='utf-8')
            self.assertIn('## 4. 运行限制', markdown)
            self.assertIn('Traceback', markdown)
            tech = (folder / 'B_技术与复现报告.md').read_text(encoding='utf-8')
            self.assertIn('## 0. 失败摘要', tech)


if __name__ == '__main__':
    unittest.main()
