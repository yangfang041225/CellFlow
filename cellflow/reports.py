"""生成两份中文报告 + 静态 SVG 图形。

A 生物学分析报告：数据质量、实际产生的轨迹/动力学结果、候选基因、参数解释、限制与证据来源。
B 技术与复现报告：数据格式、环境版本、真实调用、超参数、命令、耗时、种子、日志、失败原因、路径与复现方式。

原则：只写程序真正算出来的东西；模拟结果必须标记；没有证据的结论一律留白。

图形约定（对应审查项 F12/F13/F39）：
- 所有散布图的点坐标与速度向量都经过**同一个** `ScreenAffine`，方向不再失真；
  `aspect='equal'`（默认，保角、画布随数据 aspect 自适应）与 `aspect='stretch'`
  （把数据按 x/y 各自填满 700×410 画布）两种模式都由同一矩阵同时作用于点与向量。
- 每张图的标题、色条、图注都由同一份 `figure_specs()` 规格生成，并落盘
  `figures/figure_specs.json`；图注不会声称图里没有的字段。
- 颜色字段按标题**实际声明**的语义选取（命运概率 / 群标签 / 时间），声明字段缺失时
  退回时间或文库大小，并在标题与图注里写明实际字段。
"""
from pathlib import Path
from html.parser import HTMLParser
import hashlib
import html
import json
import math
import re
import numpy as np
from .registry import METHODS, CHECKED, INDEX

EVIDENCE_NOTE = (
    '证据分级：**observed（观测）**＝直接从数据算出的数值；**assumed（假设）**＝需要你声明或核实的前提；'
    '**inferred（推断）**＝模型输出（速度方向、拟时序、命运概率、调控关系）；**unknown（未知）**＝本次没有证据，'
    '程序不会替你补写。'
)

# ---------------------------------------------------------------- 固定视觉规范（F39）
FONT_STACK = '"Microsoft YaHei","Noto Sans CJK SC",system-ui,sans-serif'
PALETTE = ('#176b87', '#e1783b', '#745ac4', '#289976', '#b0497c',
           '#8a6d1f', '#2f4b7c', '#a05195', '#4c72b0', '#dd8452')
# 'equal'：等比例嵌入（保角，画布随数据 aspect 自适应）；'stretch'：x/y 分别填满画布。
FIGURE_ASPECT = 'equal'
PLOT_BOX = (700.0, 410.0)
PLOT_ORIGIN = (60.0, 65.0)
ARROW_BASE_PX = 34.0
FIGURE_ORDER = ('qc.svg', 'embedding.svg', 'stream.png', 'phase.svg',
                'trends.svg', 'paga.svg', 'fate.svg')
_GRADIENT_ID = [0]


def _hsl(value):
    """连续色阶：与历史配色一致（蓝 → 橙）。返回 #rrggbb（SVG 属性不认 hsl()）。"""
    hue = (235.0 - 200.0 * float(value)) % 360.0
    sat, light = 0.68, 0.49
    chroma = (1.0 - abs(2.0 * light - 1.0)) * sat
    sector = hue / 60.0
    second = chroma * (1.0 - abs(sector % 2.0 - 1.0))
    if sector < 1:
        rgb = (chroma, second, 0.0)
    elif sector < 2:
        rgb = (second, chroma, 0.0)
    elif sector < 3:
        rgb = (0.0, chroma, second)
    elif sector < 4:
        rgb = (0.0, second, chroma)
    elif sector < 5:
        rgb = (second, 0.0, chroma)
    else:
        rgb = (chroma, 0.0, second)
    base = light - chroma / 2.0
    return '#%02x%02x%02x' % tuple(int(round(min(1.0, max(0.0, item + base)) * 255))
                                   for item in rgb)


def _text_width(text, font_size=18.0):
    """粗略估算 SVG 文本宽度（CJK 按一个字宽，拉丁按 0.58 字宽）。"""
    total = 0.0
    for char in str(text):
        total += font_size if ord(char) > 0x2E80 else font_size * 0.58
    return total


def _fmt(value, digits=3):
    try:
        number = float(value)
    except Exception:
        return str(value)
    if not math.isfinite(number):
        return 'NA'
    return f'{number:.{digits}g}'


def _sha(values):
    """坐标/字段指纹：让图注可以指向“画的到底是哪一份数据”（审查要求 coordinates hash）。"""
    try:
        array = np.asarray(values, dtype=float)
        return hashlib.sha256(np.round(array.reshape(len(array), -1), 6).tobytes()).hexdigest()[:16]
    except Exception:
        return ''


# ---------------------------------------------------------------- 仿射（F12）
class ScreenAffine:
    """数据坐标 → SVG 屏幕坐标的**单一仿射变换**：点与向量共用同一个矩阵。

    点：p' = origin + (p − lo) · S，其中 y 轴翻转；
    向量：v' = (v_x·sx, −v_y·sy)。

    因此“屏幕方向”就是数据方向经同一映射后的结果，不会出现“点被拉伸、箭头没被拉伸”
    的方向失真。`aspect='equal'` 时 sx == sy（保角）；`aspect='stretch'` 时
    sx、sy 分别填满画布（10:1 数据上的 45° 向量屏幕斜率为 sy/sx = 410/70 ≈ 5.857）。
    """

    def __init__(self, lo, span, box=PLOT_BOX, origin=PLOT_ORIGIN, aspect=None):
        self.lo = np.asarray(lo, dtype=float)[:2]
        self.span = np.maximum(np.asarray(span, dtype=float)[:2], 1e-9)
        box_w, box_h = float(box[0]), float(box[1])
        ox, oy = float(origin[0]), float(origin[1])
        self.aspect = 'equal' if str(aspect or FIGURE_ASPECT) == 'equal' else 'stretch'
        if self.aspect == 'equal':
            scale = float(min(box_w / self.span[0], box_h / self.span[1]))
            self.sx = self.sy = scale
            self.width = float(self.span[0] * scale)
            self.height = float(self.span[1] * scale)
            self.x0 = ox + (box_w - self.width) / 2.0
            self.y0 = oy + (box_h - self.height) / 2.0
        else:
            self.sx = float(box_w / self.span[0])
            self.sy = float(box_h / self.span[1])
            self.width = box_w
            self.height = box_h
            self.x0 = ox
            self.y0 = oy

    # -- 点与向量：同一矩阵的两个入口 -------------------------------------
    def point(self, x, y):
        return (self.x0 + (float(x) - self.lo[0]) * self.sx,
                self.y0 + self.height - (float(y) - self.lo[1]) * self.sy)

    def points(self, coords):
        array = np.asarray(coords, dtype=float)[:, :2]
        xs = self.x0 + (array[:, 0] - self.lo[0]) * self.sx
        ys = self.y0 + self.height - (array[:, 1] - self.lo[1]) * self.sy
        return np.column_stack([xs, ys])

    def vector(self, vx, vy):
        return float(vx) * self.sx, -float(vy) * self.sy

    def vectors(self, arrows):
        array = np.asarray(arrows, dtype=float)[:, :2]
        return np.column_stack([array[:, 0] * self.sx, -array[:, 1] * self.sy])

    def bounds(self):
        return (self.x0, self.y0, self.x0 + self.width, self.y0 + self.height)

    def as_dict(self):
        return dict(aspect=self.aspect, sx=round(self.sx, 6), sy=round(self.sy, 6),
                    x0=round(self.x0, 3), y0=round(self.y0, 3),
                    width=round(self.width, 3), height=round(self.height, 3))


def _finite_extent(coords):
    points = np.asarray(coords, dtype=float)[:, :2]
    good = np.isfinite(points).all(1)
    if not good.any():
        good = np.ones(len(points), bool)
    lo = np.nanmin(points[good], axis=0)
    hi = np.nanmax(points[good], axis=0)
    return lo, np.maximum(hi - lo, 1e-9)


def screen_affine(coords, aspect=None):
    """该组坐标实际使用的仿射对象；测试与审计可以直接用它验证“屏幕方向”。"""
    lo, span = _finite_extent(coords)
    return ScreenAffine(lo, span, PLOT_BOX, PLOT_ORIGIN, aspect)


# ---------------------------------------------------------------- 色条 / 图例
def _color_range_text(colorbar):
    """只描述色阶/类别数量，不重复字段名。"""
    colorbar = colorbar or {}
    kind = colorbar.get('kind')
    if kind == 'categorical':
        entries = colorbar.get('entries') or []
        return f'{len(entries)} 类（图例见右）'
    lo, hi = colorbar.get('lo'), colorbar.get('hi')
    unit = colorbar.get('unit') or ''
    if lo is None or hi is None:
        return '范围未记录'
    if colorbar.get('fixed'):
        return f'固定色阶 {_fmt(lo)}–{_fmt(hi)}{unit}'
    return f'色阶 {_fmt(lo)}–{_fmt(hi)}{unit}（本次数据范围）'


def _color_field_text(colorbar):
    """色条/图注共用的“颜色是什么、范围是多少”描述（F13）。"""
    colorbar = colorbar or {}
    label = colorbar.get('label') or ('类别' if colorbar.get('kind') == 'categorical' else '数值')
    return f'{label}，{_color_range_text(colorbar)}'


def _colorbar_svg(colorbar, x, top, height):
    if not colorbar:
        return ''
    kind = colorbar.get('kind')
    label = colorbar.get('label') or ''
    if kind == 'categorical':
        entries = list(colorbar.get('entries') or [])
        rows = [f'<text x="{x:.1f}" y="{top - 8:.1f}" font-size="11" font-family={FONT_STACK!r}>'
                f'{html.escape(str(label))}</text>']
        for index, (name, color) in enumerate(entries[:10]):
            y = top + index * 21
            rows.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="13" height="13" rx="2" '
                        f'fill="{html.escape(str(color))}" stroke="#c8d4e0"/>'
                        f'<text x="{x + 19:.1f}" y="{y + 11:.1f}" font-size="10" '
                        f'font-family={FONT_STACK!r}>{html.escape(str(name))}</text>')
        if len(entries) > 10:
            rows.append(f'<text x="{x:.1f}" y="{top + 10 * 21 + 4:.1f}" font-size="10" '
                        f'font-family={FONT_STACK!r}>… 另有 {len(entries) - 10} 类</text>')
        return ''.join(rows)
    _GRADIENT_ID[0] += 1
    ident = f'cgrad{_GRADIENT_ID[0]}'
    lo = colorbar.get('lo')
    hi = colorbar.get('hi')
    lo = 0.0 if lo is None else float(lo)
    hi = 1.0 if hi is None else float(hi)
    unit = html.escape(str(colorbar.get('unit') or ''))
    return (
        f'<defs><linearGradient id="{ident}" x1="0" y1="1" x2="0" y2="0">'
        f'<stop offset="0" stop-color="{_hsl(0.0)}"/>'
        f'<stop offset="1" stop-color="{_hsl(1.0)}"/></linearGradient></defs>'
        f'<text x="{x:.1f}" y="{top - 8:.1f}" font-size="11" font-family={FONT_STACK!r}>'
        f'{html.escape(str(label))}</text>'
        f'<rect x="{x:.1f}" y="{top:.1f}" width="14" height="{height:.1f}" '
        f'fill="url(#{ident})" stroke="#c8d4e0"/>'
        f'<text x="{x + 19:.1f}" y="{top + 10:.1f}" font-size="10" font-family={FONT_STACK!r}>'
        f'{html.escape(_fmt(hi))}{unit}</text>'
        f'<text x="{x + 19:.1f}" y="{top + height:.1f}" font-size="10" font-family={FONT_STACK!r}>'
        f'{html.escape(_fmt(lo))}{unit}</text>'
    )


# ---------------------------------------------------------------- SVG 工具
def stream_png(coords, arrows, out_path, grid=45, aspect=None):
    """速度流线图：把细胞上的向量插值到规则网格后用 streamplot 画。

    坐标与向量先经过与嵌入图**同一个**仿射变换，再在屏幕空间插值，
    因此流线方向与箭头图一致；插值会平滑掉局部细节。
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
        from scipy.interpolate import griddata
    except Exception:
        return None
    points = np.asarray(coords, dtype=float)[:, :2]
    vectors = np.asarray(arrows, dtype=float)[:, :2]
    if points.ndim != 2 or vectors.shape != points.shape:
        return None
    good = np.isfinite(points).all(1) & np.isfinite(vectors).all(1)
    if good.sum() < 3:
        return None
    lo, span = _finite_extent(points[good])
    affine = ScreenAffine(lo, span, PLOT_BOX, (0.0, 0.0), aspect)
    screen = affine.points(points)
    screen_vectors = affine.vectors(vectors)
    # SVG y 轴向下，matplotlib y 轴向上：整体翻转不改变任何方向关系
    screen[:, 1] = -screen[:, 1]
    screen_vectors[:, 1] = -screen_vectors[:, 1]
    x, y = screen[good, 0], screen[good, 1]
    u, v = screen_vectors[good, 0], screen_vectors[good, 1]
    gx = np.linspace(np.nanmin(x), np.nanmax(x), grid)
    gy = np.linspace(np.nanmin(y), np.nanmax(y), grid)
    gxx, gyy = np.meshgrid(gx, gy)
    try:
        uu = griddata((x, y), u, (gxx, gyy), method='linear', fill_value=0.0)
        vv = griddata((x, y), v, (gxx, gyy), method='linear', fill_value=0.0)
    except Exception:
        return None
    speed = np.hypot(uu, vv)
    fig, ax = plt.subplots(figsize=(7.2, 5.0), dpi=150)
    ax.scatter(x, y, s=3, c='#b9c6d4', alpha=.35, linewidths=0)
    ax.streamplot(gxx, gyy, uu, vv, color=speed, cmap='viridis', density=1.1,
                  linewidth=0.9, arrowsize=1.0)
    ax.set_title('Velocity stream field (same affine as embedding.svg; grid-interpolated)',
                 fontsize=10)
    ax.set_aspect('equal')
    ax.set_xticks([])
    ax.set_yticks([])
    for side in ('top', 'right'):
        ax.spines[side].set_visible(False)
    fig.tight_layout()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, format='png')
    plt.close(fig)
    return out_path


def scatter_svg(coords, color, title, labels, arrows=None, arrow_scale=1.0,
                arrow_density=120, aspect=None, colorbar=None):
    """细胞散布图：坐标与速度向量共用同一个仿射矩阵（F12），色条由 colorbar 规格生成（F13）。

    colorbar: None / dict(kind='continuous', label, lo, hi, unit) /
              dict(kind='categorical', label, entries=[(名称, 颜色)])
    """
    points = np.asarray(coords, dtype=float)[:, :2]
    if points.ndim != 2 or points.shape[1] < 2:
        raise ValueError('coords 必须是 N×2 数组')
    labels = list(labels)
    category = colorbar is not None and colorbar.get('kind') == 'categorical'
    if category:
        values = [str(item) for item in color]
        lookup = {str(name): str(hexcolor) for name, hexcolor in (colorbar.get('entries') or [])}
    else:
        values = np.asarray([np.nan if item is None else float(item) for item in color], dtype=float)
    good = np.isfinite(points).all(1)
    if not good.any():
        good = np.ones(len(points), bool)
    affine = screen_affine(points, aspect)
    xy = affine.points(points)
    x0, y0, x1, y1 = affine.bounds()

    if affine.aspect == 'equal':
        # 画布随数据 aspect 自适应：宽/扁的数据不会被强行撑成 850×570 的大白边
        canvas_w = max(420.0, x1 + 230.0, 90.0 + _text_width(title))
        canvas_h = max(300.0, y1 + 118.0)
    else:
        canvas_w = max(850.0, 90.0 + _text_width(title))
        canvas_h = 570.0
    canvas_w = float(math.ceil(canvas_w))
    canvas_h = float(math.ceil(canvas_h))
    bar_x = x1 + 26.0
    bar_h = min(220.0, max(90.0, affine.height * 0.62))

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {canvas_w:.0f} {canvas_h:.0f}" '
             f'role="img" aria-label="{html.escape(str(title))}">'
             f'<rect width="{canvas_w:.0f}" height="{canvas_h:.0f}" fill="#fafcff"/>'
             f'<text x="45" y="35" font-size="18" font-family={FONT_STACK!r}>'
             f'{html.escape(str(title))}</text>'
             f'<path d="M{x0:.1f} {y0:.1f} V{y1:.1f} H{x1:.1f}" fill="none" stroke="#a8b9ca"/>']

    if category:
        for index in np.flatnonzero(good):
            name = values[index] if index < len(values) else ''
            fill = lookup.get(name, '#9aa7b4')
            parts.append(f'<circle cx="{xy[index, 0]:.2f}" cy="{xy[index, 1]:.2f}" r="3.1" '
                         f'opacity=".72" fill="{html.escape(fill)}"><title>'
                         f'{html.escape(str(labels[index] if index < len(labels) else index))}: '
                         f'{html.escape(name)}</title></circle>')
    else:
        finite = values[np.isfinite(values)]
        cmin, cmax = (float(finite.min()), float(finite.max())) if finite.size else (0.0, 1.0)
        if not colorbar:
            colorbar = dict(kind='continuous', label='数值', lo=cmin, hi=cmax)
        for index in np.flatnonzero(good):
            value = values[index] if index < len(values) else np.nan
            if not np.isfinite(value):
                fill, label = '#aaa', '不可用'
            else:
                ratio = float(np.clip((value - cmin) / max(cmax - cmin, 1e-9), 0, 1))
                fill, label = _hsl(ratio), _fmt(value, 4)
            parts.append(f'<circle cx="{xy[index, 0]:.2f}" cy="{xy[index, 1]:.2f}" r="3.1" '
                         f'opacity=".7" fill="{fill}"><title>'
                         f'{html.escape(str(labels[index] if index < len(labels) else index))}: '
                         f'{html.escape(label)}</title></circle>')

    if arrows is not None and len(arrows) == len(points):
        vectors = affine.vectors(arrows)
        norms = np.hypot(vectors[:, 0], vectors[:, 1])
        finite_norm = norms[np.isfinite(norms)]
        scale = float(np.nanpercentile(finite_norm, 90)) if finite_norm.size else 0.0
        if math.isfinite(scale) and scale > 0:
            parts.append('<defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="5.5" '
                         'refY="3.5" orient="auto"><path d="M0 0 L7 3.5 L0 7" fill="#182e4a"/>'
                         '</marker></defs>')
            step = max(1, len(points) // max(1, int(arrow_density)))
            for index in np.flatnonzero(good)[::step]:
                if index >= len(norms) or not np.isfinite(norms[index]) or norms[index] <= 0:
                    continue
                # 只影响显示：同一仿射给出的屏幕方向，长度按 90 分位归一到 34px，并限制 6–70px
                length = min(70.0, max(6.0, norms[index] / scale * ARROW_BASE_PX * float(arrow_scale)))
                ux, uy = vectors[index] / norms[index] * length
                parts.append(f'<path d="M{xy[index, 0]:.2f} {xy[index, 1]:.2f} '
                             f'l{ux:.2f} {uy:.2f}" stroke="#182e4a" stroke-width="1.3" '
                             f'marker-end="url(#arrow)" opacity=".8"/>')

    parts.append(_colorbar_svg(colorbar, bar_x, y0 + 4.0, bar_h))
    parts.append(f'<text x="45" y="{canvas_h - 58:.0f}" font-size="12" font-family={FONT_STACK!r}>'
                 f'Color: {html.escape(_color_field_text(colorbar))}; gray = unavailable</text>')
    arrow_note = ('arrows: velocity vectors mapped by the same affine as coordinates; '
                  'display length normalized to the 90th percentile (34 px), display only'
                  if arrows is not None else 'no velocity arrows in this figure')
    parts.append(f'<text x="45" y="{canvas_h - 34:.0f}" font-size="11.5" font-family={FONT_STACK!r}>'
                 f'{len(points)} cells · {affine.aspect} affine (sx={affine.sx:.2f}, '
                 f'sy={affine.sy:.2f}) · {html.escape(arrow_note)} · hover for values</text>')
    parts.append('</svg>')
    return ''.join(parts)


def bar_svg(title, items, unit='', note='', colors=None):
    """items: [(标签, 数值)]，横向条形图；颜色可逐条指定（固定配色）。"""
    if not items:
        return ''
    top = max(max(float(value) for _, value in items), 1e-9)
    colors = list(colors or [])
    rows = []
    for i, (label, value) in enumerate(items):
        y = 90 + i * 34
        width = 520 * (float(value) / top)
        color = colors[i % len(colors)] if colors else PALETTE[i % len(PALETTE)]
        rows.append(f'<text x="150" y="{y + 16}" font-size="13" text-anchor="end" '
                    f'font-family={FONT_STACK!r}>{html.escape(str(label))}</text>'
                    f'<rect x="165" y="{y}" width="{max(width, 1):.1f}" height="20" rx="4" '
                    f'fill="{html.escape(str(color))}" opacity=".85"/>'
                    f'<text x="{172 + width:.1f}" y="{y + 15}" font-size="12" '
                    f'font-family={FONT_STACK!r}>{_fmt(value)}{html.escape(unit)}</text>')
    height = max(150, 130 + 34 * len(items))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 850 {height}" role="img">'
            f'<rect width="850" height="{height}" fill="#fafcff"/>'
            f'<text x="45" y="38" font-size="17" font-family={FONT_STACK!r}>'
            f'{html.escape(str(title))}</text>'
            + ''.join(rows) +
            f'<text x="45" y="{height - 18}" font-size="12" font-family={FONT_STACK!r}>'
            f'{html.escape(str(note))}</text>'
            f'</svg>')


def matrix_heatmap_svg(title, groups, matrix, note='', vmin=0.0, vmax=1.0):
    """对称矩阵热图：使用原始数值与**固定**色阶，不做逐行归一化（F13）。"""
    array = np.asarray(matrix, dtype=float)
    n = len(groups)
    size = min(520, max(360, 40 * n + 120))
    cell = (size - 130) / max(n, 1)
    lo = 0.0 if vmin is None else float(vmin)
    hi = 1.0 if vmax is None else float(vmax)

    def color_of(value):
        ratio = float(np.clip((float(value) - lo) / max(hi - lo, 1e-9), 0, 1))
        return ('rgb(%d,%d,%d)' % (int(245 - 140 * ratio), int(250 - 90 * ratio),
                                   int(255 - 60 * ratio)))

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 850 {size + 70}" role="img">'
             f'<rect width="850" height="{size + 70}" fill="#fafcff"/>'
             f'<text x="45" y="34" font-size="17" font-family={FONT_STACK!r}>'
             f'{html.escape(str(title))}</text>']
    for j in range(n):
        parts.append(f'<text x="{130 + j * cell + cell / 2:.1f}" y="70" font-size="11" '
                     f'text-anchor="end" transform="rotate(-45 {130 + j * cell + cell / 2:.1f} 70)" '
                     f'font-family={FONT_STACK!r}>{html.escape(str(groups[j]))}</text>')
    for i in range(n):
        parts.append(f'<text x="120" y="{80 + i * cell + cell / 2 + 4:.1f}" font-size="11" '
                     f'text-anchor="end" font-family={FONT_STACK!r}>{html.escape(str(groups[i]))}</text>')
        for j in range(n):
            try:
                value = float(array[i][j])
            except Exception:
                value = float('nan')
            fill = color_of(value) if math.isfinite(value) else '#aaaaaa'
            parts.append(f'<rect x="{130 + j * cell:.1f}" y="{80 + i * cell:.1f}" '
                         f'width="{cell - 1:.1f}" height="{cell - 1:.1f}" fill="{fill}">'
                         f'<title>{html.escape(str(groups[i]))} → {html.escape(str(groups[j]))}: '
                         f'{_fmt(value)}</title></rect>')
    # 固定色阶图例
    _GRADIENT_ID[0] += 1
    ident = f'hgrad{_GRADIENT_ID[0]}'
    bar_x, bar_y = 130 + n * cell + 26, 82
    bar_h = min(200, max(100, cell * n * 0.6))
    parts.append(f'<defs><linearGradient id="{ident}" x1="0" y1="1" x2="0" y2="0">'
                 f'<stop offset="0" stop-color="{color_of(lo)}"/>'
                 f'<stop offset="1" stop-color="{color_of(hi)}"/></linearGradient></defs>'
                 f'<rect x="{bar_x:.1f}" y="{bar_y}" width="14" height="{bar_h:.1f}" '
                 f'fill="url(#{ident})" stroke="#c8d4e0"/>'
                 f'<text x="{bar_x + 19:.1f}" y="{bar_y + 10}" font-size="10" '
                 f'font-family={FONT_STACK!r}>{_fmt(hi)}</text>'
                 f'<text x="{bar_x + 19:.1f}" y="{bar_y + bar_h:.1f}" font-size="10" '
                 f'font-family={FONT_STACK!r}>{_fmt(lo)}</text>')
    parts.append(f'<text x="45" y="{size + 44}" font-size="12" font-family={FONT_STACK!r}>'
                 f'fixed color scale {_fmt(lo)}–{_fmt(hi)} · values are raw connectivities '
                 f'(not row-normalized) · symmetric / undirected</text>'
                 f'<text x="45" y="{size + 64}" font-size="12" font-family={FONT_STACK!r}>'
                 f'{html.escape(str(note))}</text></svg>')
    return ''.join(parts)


# ---------------------------------------------------------------- 图形规格（F13）
_CLAIM_RE = re.compile(r'（[^（）]*(?:着色|颜色)[^（）]*）')


def _clean_embedding_name(name):
    return _CLAIM_RE.sub('', str(name or '')).strip()


def _continuous(values, field, label, lo=None, hi=None, unit=''):
    array = np.asarray([np.nan if item is None else item for item in values], dtype=float)
    finite = array[np.isfinite(array)]
    if lo is None:
        lo = float(finite.min()) if finite.size else 0.0
    if hi is None:
        hi = float(finite.max()) if finite.size else 1.0
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        hi = float(lo) + 1e-9
    return list(array), dict(kind='continuous', field=field, label=label,
                             lo=float(lo), hi=float(hi), unit=unit)


def _categorical(values, field, label):
    names = [str(item) for item in values]
    categories = sorted(set(names))
    entries = [(name, PALETTE[index % len(PALETTE)])
               for index, name in enumerate(categories)]
    return names, dict(kind='categorical', field=field, label=label, entries=entries)


def embedding_color(result, qc):
    """按标题**声明的字段**决定着色；字段缺失时退回时间/文库大小并返回警告文本（F13）。"""
    result = result or {}
    qc = qc or {}
    name = str(result.get('embedding_name') or '')
    embedding = result.get('embedding') or []
    n = len(embedding)
    fate = result.get('fate') or {}
    probabilities = np.asarray(fate.get('probabilities') or [], dtype=float)
    groups = result.get('groups') or {}
    group_labels = groups.get('labels')
    time_values = result.get('time')
    time_label = str(result.get('time_name') or '模型时间 / 拟时序')
    warning = ''

    if '命运' in name:
        if probabilities.ndim == 2 and probabilities.shape[0] == n and probabilities.size:
            values, bar = _continuous(probabilities.max(axis=1), 'fate.probabilities',
                                      '每个细胞的最大命运概率（模型推断）', 0.0, 1.0)
            bar['fixed'] = True
            return values, bar, ''
        warning = '标题声明的“命运概率”字段缺失，已改用实际存在的字段着色。'
    if ('分群' in name or '群' in name) and group_labels and len(group_labels) == n:
        values, bar = _categorical(group_labels, 'groups.labels', '群标签（算法分群）')
        return values, bar, warning
    if ('分群' in name or '群' in name) and not warning:
        warning = '标题声明的“群标签”字段缺失，已改用实际存在的字段着色。'
    if time_values and len(time_values) == n:
        values, bar = _continuous(time_values, 'time', '模型时间（无实验标定）')
        return values, bar, warning
    total = qc.get('total')
    if total is not None and len(np.asarray(total).ravel()) == n:
        values, bar = _continuous(np.asarray(total).ravel(), 'qc.total',
                                  '每细胞总计数（文库大小）')
        return values, bar, warning
    return ([np.nan] * n,
            dict(kind='continuous', field='none', label='没有可用着色字段（全部灰色）',
                 lo=0.0, hi=1.0), warning)


def figure_specs(result, qc, config=None, data=None):
    """所有图形的**唯一规格来源**：标题、颜色字段、色阶、箭头、aspect、图注。

    返回 {文件名: spec}；make_figures 用它画图，write_reports 用同一份规格写图注，
    并落盘 figures/figure_specs.json 供审计核对。
    """
    result = result or {}
    qc = qc or {}
    config = dict(config or {})
    prefix = 'SIMULATION / ' if (data is not None and getattr(data, 'synthetic', False)) else ''
    specs = {}

    # ---- 质控图
    if qc.get('total') is not None and qc.get('detected') is not None:
        mt_pct = qc.get('mt_pct') if qc.get('mt_pct') is not None else [0.0] * len(qc['total'])
        _, colorbar = _continuous(mt_pct, 'qc.mt_pct', '线粒体基因比例（%）')
        colorbar['unit'] = '%'
        specs['qc.svg'] = dict(
            file='qc.svg', aspect=FIGURE_ASPECT, color=colorbar,
            title=prefix + 'Counts vs detected genes · 颜色: 线粒体基因比例（%）',
            caption=('每细胞质量控制：x = 每细胞总计数，y = 检测基因数，颜色 = 线粒体基因比例（%）；'
                     '阈值由研究者结合组织判断，本图不自动过滤任何细胞。'),
            coords_sha256=_sha(np.c_[qc['total'], qc['detected']]),
            color_sha256=_sha(np.asarray(mt_pct, dtype=float)))

    # ---- 主嵌入图
    if result.get('embedding'):
        values, colorbar, mismatch = embedding_color(result, qc)
        basis = str(result.get('embedding_basis') or 'PCA')
        base = _clean_embedding_name(result.get('embedding_name')) or 'Embedding'
        arrows = bool(result.get('arrows'))
        numeric = bool(len(values)) and not isinstance(values[0], str)
        caption_body = (f'细胞在 {basis.upper()} 上的二维投影（二维投影会失真，距离不等于生物学差异）；'
                        f'颜色字段：{colorbar.get("label")}（来源 {colorbar.get("field")}，'
                        f'{_color_range_text(colorbar)}）；' +
                        ('箭头是速度向量的投影，与细胞坐标使用同一个仿射变换，'
                         '长度按 90 分位归一化仅用于显示，不改变底层速度值。'
                         if arrows else '本图没有速度箭头。'))
        if mismatch:
            caption_body += mismatch
        specs['embedding.svg'] = dict(
            file='embedding.svg', aspect=FIGURE_ASPECT, color=colorbar,
            values=values, arrows=arrows,
            title=prefix + base + ' · 颜色: ' + str(colorbar.get('label')),
            caption=caption_body,
            coords_sha256=_sha(result.get('embedding')),
            color_sha256=_sha(np.asarray(values, dtype=float)) if numeric else '',
            affine=ScreenAffine((0, 0), (1, 1), PLOT_BOX, PLOT_ORIGIN, FIGURE_ASPECT).as_dict())

    # ---- 速度流线图
    if result.get('arrows') and result.get('embedding'):
        specs['stream.png'] = dict(
            file='stream.png', aspect=FIGURE_ASPECT, arrows=True,
            title='Velocity stream field（与 embedding.svg 同一仿射，网格插值仅用于显示）',
            caption=('速度流线场：把细胞向量插值到规则网格后用 streamplot 绘制，坐标与向量使用与 '
                     'embedding.svg 同一仿射变换；仅用于观察走向与密度，插值会平滑局部差异，'
                     '不是单细胞追踪。'),
            coords_sha256=_sha(result.get('embedding')))

    # ---- 剪接相图
    if data is not None and all(key in getattr(data, 'layers', {}) for key in ('spliced', 'unspliced')):
        specs['phase.svg'] = dict(
            file='phase.svg', aspect=FIGURE_ASPECT,
            title=prefix + 'Phase portrait · x: spliced; y: unspliced',
            caption=('剪接相图：spliced（x）与 unspliced（y）计数；用于检查剪接动力学假设是否合理，'
                     '单张相图不能证明速度方向。'))

    # ---- 基因趋势
    if result.get('gene_trends'):
        specs['trends.svg'] = dict(
            file='trends.svg', aspect='stretch',
            title='Exploratory expression trends · log1p(CP10k)',
            caption=('候选基因表达趋势：按相对时间等细胞数分箱后的 log1p(CP10k) 均值；'
                     '基因是在同一份数据上挑的，探索性展示，未做显著性检验。'),
            color_sha256=_sha(np.asarray([bin_['mean_expression'] for row in result['gene_trends']
                                          for bin_ in row.get('bins') or []], dtype=float)))

    # ---- PAGA 热图
    paga = result.get('paga') or {}
    if paga.get('connectivities'):
        matrix = np.asarray(paga['connectivities'], dtype=float)
        specs['paga.svg'] = dict(
            file='paga.svg', aspect='stretch',
            title=prefix + 'PAGA connectivity（原始 connectivities，固定 0–1 色阶，无向）',
            caption=('PAGA 群间连接：使用原始 connectivities 与固定 0–1 色阶，无向对称、未做逐行归一化；'
                     '颜色只表示群间转录相似性强度，不代表真实谱系、方向或因果。'),
            matrix_sha256=_sha(matrix),
            vmin=0.0, vmax=1.0)

    # ---- 命运概率
    fate = result.get('fate') or {}
    probabilities = np.asarray(fate.get('probabilities') or [], dtype=float)
    names = [str(name) for name in (fate.get('terminal_states') or [])]
    if names and probabilities.ndim == 2 and probabilities.shape[1] == len(names) and probabilities.size:
        specs['fate.svg'] = dict(
            file='fate.svg', aspect='stretch',
            title=prefix + 'Mean fate probability per terminal state（模型推断）',
            caption=('终末状态与命运概率：柱高为每个终末状态的细胞平均命运概率'
                     f'（来源 fate.probabilities，估计器 {fate.get("estimator") or "CellRank"}，'
                     f'kernel {fate.get("kernel") or "未记录"}）；这是模型推断，'
                     '不是实验观测到的命运，也没有验证谱系方向。'),
            probabilities_sha256=_sha(probabilities))
    return specs


def _public_spec(spec):
    """只保留可 JSON 序列化的字段（用于 figures/figure_specs.json）。"""
    allowed = ('file', 'title', 'caption', 'aspect', 'color', 'arrows', 'vmin', 'vmax',
               'coords_sha256', 'color_sha256', 'matrix_sha256', 'probabilities_sha256',
               'affine')
    return {key: spec[key] for key in allowed if key in spec}


# ---------------------------------------------------------------- 图形总装
def make_figures(folder, data, qc, result, config=None):
    folder = Path(folder)
    result = result or {}
    qc = qc or {}
    config = dict(config or {})
    figures = folder / 'figures'
    figures.mkdir(parents=True, exist_ok=True)
    labels = [str(item) for item in getattr(data, 'cells', [])]
    specs = figure_specs(result, qc, config, data)
    written = []

    def write(name, markup):
        (figures / name).write_text(markup, encoding='utf-8')
        written.append(name)

    spec = specs.get('qc.svg')
    if spec:
        spec['affine'] = screen_affine(np.c_[qc['total'], qc['detected']], spec['aspect']).as_dict()
        write('qc.svg', scatter_svg(np.c_[qc['total'], qc['detected']], qc['mt_pct'],
                                    spec['title'], labels, aspect=spec['aspect'],
                                    colorbar=spec['color']))

    spec = specs.get('embedding.svg')
    if spec:
        spec['affine'] = screen_affine(result['embedding'], spec['aspect']).as_dict()
        write('embedding.svg', scatter_svg(
            result['embedding'], spec.get('values'), spec['title'], labels, result.get('arrows'),
            arrow_scale=float(config.get('arrow_scale', 1.0)),
            arrow_density=int(config.get('arrow_density', 120)),
            aspect=spec['aspect'], colorbar=spec['color']))

    spec = specs.get('stream.png')
    if spec:
        try:
            produced = stream_png(result['embedding'], result['arrows'],
                                  figures / 'stream.png', aspect=spec['aspect'])
            if produced:
                written.append('stream.png')
        except Exception:
            pass

    spec = specs.get('phase.svg')
    if spec:
        gene = int(np.argmax(np.asarray(data.layers['spliced'].sum(0)).ravel()))

        def column(matrix, index=gene):
            values = matrix[:, index]
            return np.asarray(values.toarray() if hasattr(values, 'toarray') else values).ravel()

        phase_coords = np.c_[column(data.layers['spliced']), column(data.layers['unspliced'])]
        if result.get('time'):
            phase_color, phase_bar = _continuous(result['time'], 'time',
                                                 '模型时间（无实验标定）')
        else:
            total = qc.get('total') if qc.get('total') is not None else []
            phase_color, phase_bar = _continuous(total, 'qc.total', '每细胞总计数（文库大小）')
        spec['title'] = (str(spec['title']) + ' · 颜色: ' + str(phase_bar.get('label'))
                         + ' · gene: ' + str(data.genes[gene]))
        spec['color'] = phase_bar
        spec['coords_sha256'] = _sha(phase_coords)
        spec['affine'] = screen_affine(phase_coords, spec['aspect']).as_dict()
        spec['caption'] = (str(spec.get('caption') or '剪接相图：spliced 与 unspliced 计数。')
                           + f'实际着色字段：{phase_bar.get("label")}'
                             f'（来源 {phase_bar.get("field")}，{_color_range_text(phase_bar)}）。')
        write('phase.svg', scatter_svg(phase_coords, phase_color, spec['title'], labels,
                                       aspect=spec['aspect'], colorbar=phase_bar))

    spec = specs.get('trends.svg')
    if spec:
        series = result.get('gene_trends') or []
        max_y = max((bin_['mean_expression'] for row in series for bin_ in row['bins']), default=1.0)
        all_t = [bin_['time'] for row in series for bin_ in row['bins']]
        lo_t = min(all_t) if all_t else 0.0
        span = max((max(all_t) - lo_t) if all_t else 1e-9, 1e-9)
        parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 850 560" role="img">'
                 '<rect width="850" height="560" fill="#fafcff"/>'
                 f'<text x="50" y="35" font-family={FONT_STACK!r} font-size="17">'
                 f'{html.escape(spec["title"])}</text>'
                 '<path d="M60 65 V475 H790" fill="none" stroke="#a8b9ca"/>']
        for index, (row, color_) in enumerate(zip(series, PALETTE * 4)):
            points = ' '.join(f"{60 + (bin_['time'] - lo_t) / span * 700:.2f},"
                             f"{475 - bin_['mean_expression'] / max(max_y, 1e-9) * 380:.2f}"
                             for bin_ in row['bins'])
            parts.append(f'<polyline points="{points}" fill="none" stroke="{color_}" '
                         'stroke-width="2.5"/>')
            parts.append(f'<text x="{60 + 120 * index}" y="510" fill="{color_}" '
                         f'font-family={FONT_STACK!r} font-size="12">'
                         f'{html.escape(str(row["gene"]))}</text>')
        parts.append(f'<text x="60" y="545" font-family={FONT_STACK!r} font-size="12">'
                     'y = log1p(CP10k) mean · x = relative time bins · descriptive only, '
                     'not causal or independent validation</text></svg>')
        write('trends.svg', ''.join(parts))

    spec = specs.get('paga.svg')
    if spec:
        paga = result['paga']
        matrix = np.clip(np.asarray(paga['connectivities'], dtype=float), 0.0, 1.0)
        write('paga.svg', matrix_heatmap_svg(
            spec['title'], [str(item) for item in paga['groups']], matrix,
            note='无向对称矩阵：颜色只表示转录相似性强弱，不代表谱系方向或因果',
            vmin=spec.get('vmin', 0.0), vmax=spec.get('vmax', 1.0)))

    spec = specs.get('fate.svg')
    if spec:
        fate = result['fate']
        names = [str(item) for item in fate['terminal_states']]
        probabilities = np.asarray(fate['probabilities'], dtype=float)
        items = [(name, float(probabilities[:, index].mean()))
                 for index, name in enumerate(names) if index < probabilities.shape[1]]
        colors = [PALETTE[index % len(PALETTE)] for index in range(len(items))]
        note = ('来源 fate.probabilities（模型推断，CellRank），不是实验观测 · '
                '只识别到 1 个终末状态时没有区分度'
                if len(names) < 2 else
                '来源 fate.probabilities（模型推断，CellRank）· 概率高不等于谱系已被验证')
        write('fate.svg', bar_svg(spec['title'], items, note=note, colors=colors))

    # 便携交互图（内嵌 plotly，不访问 CDN）
    try:
        import plotly.graph_objects as go
        embedding = np.asarray(result['embedding'])
        spec = specs.get('embedding.svg') or {}
        color_values = spec.get('values') if spec else None
        if color_values is not None and not isinstance(color_values[0], str):
            marker = dict(size=5, color=color_values, colorscale='Viridis', showscale=True,
                          colorbar=dict(title=spec['color'].get('label')))
        else:
            marker = dict(size=5, color='#176b87')
        figure = go.Figure(go.Scattergl(x=embedding[:, 0], y=embedding[:, 1], mode='markers',
                                        text=labels, marker=marker))
        figure.update_layout(title=spec.get('title') or 'Embedding', template='plotly_white',
                             height=700)
        figure.write_html(figures / 'embedding.html', include_plotlyjs=True, full_html=True)
    except Exception:
        pass

    public = {name: _public_spec(spec) for name, spec in specs.items() if name in written}
    dump_json(figures / 'figure_specs.json', public)


def dump_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False,
                                     default=str), encoding='utf-8')


# ---------------------------------------------------------------- 实际执行步骤（F14）
_VELOCITY_MODE_BY_METHOD = {
    'scvelo_deterministic': 'deterministic',
    'scvelo_stochastic': 'stochastic',
    'scvelo_dynamical': 'dynamical',
}

STEP_LABELS = {
    'embedding': '降维展示', 'clustering': '自动分群', 'paga': 'PAGA 群间连接',
    'velocity': 'RNA velocity 速度', 'velocity_graph': '速度图',
    'fate': 'CellRank 命运概率', 'dpt': 'DPT 拟时序', 'latent_time': '潜在时间',
    'trends': '探索性基因趋势', 'recover_dynamics': 'dynamical 动力学参数',
    'velocity_gene_table': '速度候选基因表', 'drivers': '命运驱动基因',
}


def _steps_text(steps):
    return '、'.join(STEP_LABELS.get(step, step) for step in (steps or [])) or '质控与降维'


def _read_calls(folder):
    if folder is None:
        return []
    try:
        payload = json.loads((Path(folder) / 'calls.json').read_text(encoding='utf-8'))
    except Exception:
        return []
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def executed_steps(folder, result, config=None):
    """从 result 字段与 calls.json（真实调用记录）还原**本次实际执行**的步骤与模型模式。

    报告里的“scVelo（某模式）”必须来自这里，而不是按方法名或配置默认值填空（F14）。
    """
    result = result or {}
    config = dict(config or {})
    calls = _read_calls(folder)
    call_names = [str(item.get('function')) for item in calls]
    mode = None
    for item in calls:
        if item.get('function') == 'scvelo.tl.velocity':
            arguments = item.get('arguments') or {}
            mode = str(arguments.get('mode') or mode) if arguments.get('mode') else mode
    method = str(config.get('method') or result.get('method') or '')
    if mode is None:
        mode = _VELOCITY_MODE_BY_METHOD.get(method)
    has_velocity = bool(result.get('velocity') or result.get('arrows'))
    if mode is None and has_velocity:
        candidate = str(config.get('velocity_model') or '')
        mode = candidate if candidate in ('deterministic', 'stochastic', 'dynamical') else None
    fate = result.get('fate') or {}
    time_name = str(result.get('time_name') or '')
    steps = []
    if result.get('embedding'):
        steps.append('embedding')
    if (result.get('groups') or {}).get('sizes'):
        steps.append('clustering')
    if (result.get('paga') or {}).get('connectivities'):
        steps.append('paga')
    if has_velocity:
        steps.append('velocity')
        steps.append('velocity_graph')
    if any(name == 'scvelo.tl.velocity_graph' for name in call_names) and 'velocity_graph' not in steps:
        steps.append('velocity_graph')
    if any(name == 'scvelo.tl.recover_dynamics' for name in call_names):
        steps.append('recover_dynamics')
    if 'latent_time' in time_name or any('latent_time' in name for name in call_names):
        steps.append('latent_time')
    if any(name == 'scanpy.tl.dpt' for name in call_names) or 'DPT' in time_name:
        steps.append('dpt')
    if fate.get('terminal_states'):
        steps.append('fate')
    if fate.get('drivers'):
        steps.append('drivers')
    if result.get('gene_trends'):
        steps.append('trends')
    if result.get('velocity_table'):
        steps.append('velocity_gene_table')
    return dict(steps=steps, calls=call_names, velocity_mode=mode, has_velocity=has_velocity,
                has_fate=bool(fate.get('terminal_states')), kernel=fate.get('kernel') or '',
                time_name=time_name, method=method)


# ---------------------------------------------------------------- 报告正文
def _pct(value, digits=1):
    try:
        return f'{float(value) * 100:.{digits}f}%'
    except Exception:
        return '未知'


def _fmt_num(value, digits=2):
    try:
        return f'{float(value):.{digits}f}'
    except Exception:
        return str(value)


def _method_name(key):
    return INDEX[key].name if key in INDEX else str(key)


def _simulation_banner(simulated):
    if simulated:
        return ('> ⚠️ **模拟教学数据**：本报告只验证软件流程是否跑通，**不构成任何生物学发现**；'
                '所有趋势、方向和概率都来自人为生成的虚拟基因。')
    return ''


def _headline(method, info, error):
    if error:
        return '本次没有产生可解释的模型结果，不能据此推断任何趋势或关键基因。'
    if method in ('qc', 'demo'):
        return ('本次只做了描述性质控与降维展示，没有拟合动力学模型，'
                '因此**不提供**速度方向、谱系或调控结论。')
    labels = {'embedding': '降维展示', 'clustering': '自动分群', 'paga': 'PAGA 群间连接',
              'velocity': 'RNA velocity 速度', 'fate': 'CellRank 命运概率',
              'dpt': 'DPT 拟时序', 'trends': '探索性基因趋势',
              'recover_dynamics': 'dynamical 动力学参数'}
    done = _steps_text([step for step in (info.get('steps') or []) if step in labels])
    inferred = [name for name in ('velocity', 'fate', 'dpt')
                if name in (info.get('steps') or [])]
    if info.get('has_velocity') and info.get('has_fate'):
        return (f'本次用 {_method_name(method)} 在**全部细胞**上执行了 {done}；'
                '其中速度方向与命运概率属于模型推断，需要独立实验证据才能支持生物学结论。')
    if inferred:
        return (f'本次用 {_method_name(method)} 在**全部细胞**上执行了 {done}；'
                '其中涉及时间/方向的结论依赖模型假设，属于推断而非观测。')
    return (f'本次用 {_method_name(method)} 在**全部细胞**上执行了 {done}；'
            '**没有**产生 RNA velocity 速度、潜在时间或命运概率结论（见 4.10 的分层说明）。')


def interpretation_layers(result, config, method_key, folder=None, facts=None):
    """把解释拆成“直接观察 / 派生统计 / 计算推断 / 生物学假说”四层。

    每一层只允许出现本次**真的产出**的字段：速度模式取自 calls.json 的真实调用，
    没有速度矩阵时绝不写“由 scVelo 推断”（F14）。
    """
    result = result or {}
    config = dict(config or {})
    facts = facts or {}
    info = executed_steps(folder, result, config)
    steps = info.get('steps') or []
    metrics = (result or {}).get('metrics') or {}
    groups = (result or {}).get('groups') or {}
    fate = (result or {}).get('fate') or {}
    gene_table = (result or {}).get('gene_table') or []

    observed = []
    if facts.get('n_cells') and facts.get('n_genes'):
        observed.append(f'输入数据为 {int(facts["n_cells"])} 个细胞 × {int(facts["n_genes"])} 个基因'
                        '（读取阶段记录的实际维度）。')
    if 'clustering' in steps:
        sizes = groups.get('sizes') or {}
        n_groups = metrics.get('n_groups') or (len(sizes) if sizes else None)
        if n_groups:
            observed.append(f'分群得到 {int(n_groups)} 个群（{_md(groups.get("key") or "leiden")}，'
                            f'分辨率 {_md(config.get("leiden_resolution", "本次配置未记录"))}）；'
                            '群编号是算法产物，不能自动等同于细胞类型。')
        if sizes:
            observed.append('群大小：' + '，'.join(f'{_md(key)}={_md(value)} 个细胞'
                                                 for key, value in sizes.items()) + '。')
    if 'embedding' in steps:
        observed.append(f'降维展示使用 '
                        f'{_md(str(result.get("embedding_basis") or "pca").upper())} '
                        '的二维坐标（投影会失真，不等于真实距离）。')
    if 'paga' in steps:
        observed.append('输出包含 PAGA 群间连接矩阵（无向、0–1，未逐行归一化）。')
    if not observed:
        observed.append('本次输出以质控与结构指标为主，没有可重复读出的动力学数值。')

    derived = []
    if metrics.get('velocity_length_median') is not None:
        derived.append(f'速度向量长度中位数 {_fmt_num(metrics["velocity_length_median"])}'
                       '（由速度矩阵计算的相对量，不能换算成物理速度）。')
    if metrics.get('velocity_confidence_median') is not None:
        derived.append(f'速度置信度中位数 {_fmt_num(metrics["velocity_confidence_median"])}'
                       '（局部方向一致性的内部诊断，不是准确率）。')
    if metrics.get('mean_max_fate_probability') is not None:
        derived.append(f'每个细胞最大命运概率的均值 '
                       f'{_fmt_num(metrics["mean_max_fate_probability"])}'
                       '（由概率矩阵计算的派生统计，不是实验观测）。')
    if metrics.get('n_terminal_states') is not None:
        derived.append(f'识别到 {int(metrics["n_terminal_states"])} 个终末状态'
                       '（算法在细胞图上识别的吸收端，需要生物学确认）。')
    if 'dpt' in steps and metrics.get('dpt_reachable') is not None:
        derived.append(f'DPT 可达细胞 {int(metrics["dpt_reachable"])} 个'
                       '（依赖指定起点；拟时序是相对顺序，不是真实时间）。')
    if metrics.get('velocity_length_median') is not None and 'velocity' not in steps:
        derived.append('注意：报告里存在速度相关统计量，但本次没有速度矩阵/箭头输出，'
                       '这些数值需要复核来源。')
    if not derived:
        derived.append('本次没有产生可计算的模型派生统计。')

    inferred = []
    if 'velocity' in steps:
        mode = info.get('velocity_mode') or '未在 calls.json 中记录'
        inferred.append(f'速度方向由 scVelo（{mode} 模式）在 spliced/unspliced 计数上推断，'
                        '依赖剪接动力学假设。')
        if mode == 'deterministic':
            inferred.append('deterministic 是稳态模型：本次没有 recover_dynamics，'
                            '也没有潜在时间（latent time）。')
        elif mode == 'dynamical':
            inferred.append('dynamical 模式先执行 recover_dynamics 再取潜在时间；'
                            '潜在时间是无量纲的模型内部时间，不是实验小时数。')
        else:
            inferred.append('stochastic 模式给出速度与速度伪时序；'
                            '其损失/似然与 dynamical 不可直接比较。')
        if 'velocity_graph' in steps:
            inferred.append('速度图（velocity_graph）是细胞间的方向权重，'
                            '用于伪时序与命运核；它是模型输出，不是谱系追踪。')
        if 'fate' in steps:
            kernel = str(fate.get('kernel') or '未记录')
            if 'Pseudotime' in kernel:
                inferred.append(f'命运概率来自 CellRank（{_md(kernel)}）的转移矩阵，'
                                '输入是 DPT 拟时序而不是速度核；概率高不等于谱系已被验证。')
            else:
                inferred.append(f'命运概率来自 CellRank（{_md(kernel)}）的速度核转移矩阵；'
                                '概率高不等于谱系已被验证。')
    else:
        inferred.append('本次没有执行 RNA velocity：没有速度矩阵、箭头或命运概率输出，'
                        '因此不给出方向性结论。')
    if 'paga' in steps:
        inferred.append('群间连接（PAGA）是转录相似性的汇总，连线不代表真实谱系或因果。')
    if gene_table:
        inferred.append('候选基因按模型内部统计量排序，未做显著性检验，'
                        '不代表标记基因或调控关系。')
    if method_key == 'velovi' and 'velocity' in steps:
        inferred.append('速度与潜在时间来自 veloVI 变分推断（后验近似），'
                        '与 scVelo 的点估计不等价。')
    elif method_key == 'regvelo':
        inferred.append('RegVelo 结果是实验性接入：CPU 路径不输出调控边，'
                        '不能据此宣称调控网络。')
    if not inferred:
        inferred.append('本方法不产生动力学推断；结构结果只描述当前数据的几何关系。')

    hypotheses = []
    if gene_table:
        hypotheses.append('候选基因可作为后续实验（敲低/过表达、时间序列）验证的起点；'
                          '本报告不据此断言细胞功能、分化终点或因果关系。')
    else:
        hypotheses.append('当前输出不足以形成可检验的生物学假说；'
                          '需要真实元数据、分组依据或外部证据。')
    hypotheses.append('方向、命运与调控结论都需要独立证据'
                      '（时间序列、谱系追踪、扰动实验或跨供体复现），本报告不替你做这个判断。')

    lines = ['### 4.10 解释分层（观察 / 派生统计 / 推断 / 假说）', '',
             '**直接观察（原始数据或输出中可直接读出的结构）**', '']
    lines += ['- ' + item for item in observed]
    lines += ['', '**派生统计（由本次输出计算的描述量）**', '']
    lines += ['- ' + item for item in derived]
    lines += ['', '**计算推断（依赖模型假设）**', '']
    lines += ['- ' + item for item in inferred]
    lines += ['', '**生物学假说（需要独立证据）**', '']
    lines += ['- ' + item for item in hypotheses]
    lines += ['', '四层不可互换：观察可以复现，派生统计依赖输出，推断依赖模型，'
                  '假说需要实验或外部数据支持。']
    return '\n'.join(lines) + '\n\n'


# ---------------------------------------------------------------- 图注（F13）
_FALLBACK_CAPTIONS = {
    'qc.svg': '每细胞质量控制（x = 总计数，y = 检测基因数，颜色 = 线粒体基因比例）；'
              '阈值由研究者判断，本图不自动过滤。',
    'stream.png': '速度流线场：把细胞向量插值到规则网格后绘制，与嵌入图使用同一仿射变换；'
                  '仅用于观察走向与密度，不是单细胞追踪。',
    'phase.svg': '剪接相图：spliced 与 unspliced 计数；用于检查剪接动力学假设是否合理。',
    'trends.svg': '候选基因表达趋势：按相对时间等细胞数分箱的 log1p(CP10k) 均值；'
                  '探索性展示，未做显著性检验。',
    'paga.svg': 'PAGA 群间连接（原始 connectivities，固定 0–1 色阶，无向对称，'
                '未做逐行归一化）；颜色只表示转录相似性，不代表真实谱系或因果。',
    'fate.svg': '终末状态与命运概率：CellRank 在细胞图上的模型推断，'
                '是转移权重而非实验观测到的命运。',
    'embedding.svg': '细胞在 {basis} 上的二维投影；箭头与坐标使用同一仿射变换，'
                     '长度按 90 分位归一化仅用于显示。',
}


def _fallback_caption(stem, result):
    result = result or {}
    template = _FALLBACK_CAPTIONS.get(stem) or _FALLBACK_CAPTIONS.get(stem + '.svg')
    if not template:
        return f'{stem} 输出（报告没有记录该图的数据规格）'
    basis = str(result.get('embedding_basis') or 'UMAP').upper()
    return template.replace('{basis}', basis)


def _figure_caption(filename, result, config, index, qc=None, specs=None):
    """图注：由与绘图**相同**的规格生成，只描述图里真实存在的字段与限制。"""
    name = Path(filename).name
    stem = Path(filename).stem
    spec = None
    if specs and name in specs:
        spec = specs.get(name)
    else:
        try:
            computed = figure_specs(result or {}, qc or {}, config or {})
        except Exception:
            computed = {}
        spec = computed.get(name) or computed.get(Path(filename).name.lower())
    if spec and spec.get('caption'):
        return f'图 {index} · {spec["caption"]}'
    return f'图 {index} · ' + _fallback_caption(stem, result)


# ---------------------------------------------------------------- 导出资源完整性（F18）
_REF_RE = re.compile(r'(?:src=["\']|!\[[^\]]*\]\()\s*(figures/[^"\')\s>]+)')


def report_refs(folder):
    """每条报告引用了哪些本地 figures 资源（相对路径）。"""
    folder = Path(folder)
    refs = {}
    for name in ('A_生物学分析报告.md', 'B_技术与复现报告.md', 'A_report.html', 'B_report.html'):
        path = folder / name
        if not path.is_file():
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        values = []
        for match in _REF_RE.finditer(text):
            ref = match.group(1).replace('\\', '/')
            if ref not in values:
                values.append(ref)
        refs[name] = values
    return refs


def missing_report_refs(folder):
    """导出前校验：HTML/Markdown 引用的本地图片是否都存在。返回 {报告: [缺失引用]}。"""
    folder = Path(folder)
    missing = {}
    for name, values in report_refs(folder).items():
        absent = [ref for ref in values if not (folder / ref).is_file()]
        if absent:
            missing[name] = absent
    return missing


def bundle_entries(folder):
    """ZIP 导出清单：(绝对路径, 包内相对路径)。

    从实际 figures 目录枚举被引用/已生成的 png+svg（含 stream.png），
    保留 figures/ 相对目录，确保导出后离线可打开。
    """
    folder = Path(folder)
    entries = []
    seen = set()

    def add(path, arcname):
        path = Path(path)
        if not path.is_file() or path in seen:
            return
        entries.append((path, arcname.replace('\\', '/')))
        seen.add(path)

    for pattern in ('*.md', '*.html', '*.pdf', '*.json', '*.log'):
        for path in sorted(folder.glob(pattern)):
            add(path, path.name)
    for name, values in report_refs(folder).items():
        for ref in values:
            if Path(ref).suffix.lower() in ('.png', '.svg', '.jpg', '.jpeg', '.webp'):
                add(folder / ref, ref)
    figures = folder / 'figures'
    if figures.is_dir():
        for path in sorted(figures.glob('*.svg')) + sorted(figures.glob('*.png')):
            add(path, 'figures/' + path.name)
        add(figures / 'figure_specs.json', 'figures/figure_specs.json')
    return entries


# ---------------------------------------------------------------- 报告渲染
def _render_markdown(text):
    """把报告 Markdown 渲染成 HTML；缺少 markdown 库时退回 <pre>，不假装渲染成功。"""
    try:
        import markdown as _markdown
        return _markdown.markdown(text, extensions=['extra', 'sane_lists'])
    except Exception:
        return '<pre>' + html.escape(text) + '</pre>'


# ---------------------------------------------------------------- 输出安全（F16）
# 数据单元（数据集名、obs 值、基因名、config 文本、错误信息、路径）进入 Markdown
# 之前先做 HTML 转义；最终 HTML 再走一遍白名单清洗 + CSP，双重防线。
CSP_CONTENT = ("default-src 'self'; script-src 'none'; "
               "img-src 'self' data: file:; style-src 'self' 'unsafe-inline'; "
               "base-uri 'none'; form-action 'none'; object-src 'none'")


def _md(value):
    """数据单元进入 Markdown**文本**前的 HTML 转义（& < > " '）。"""
    return html.escape(str(value), quote=True)


def _inline_code(value):
    """把数据放进行内代码：按内容里的反引号数量选分隔符，避免被内容截断。"""
    text = str(value)
    longest = max((len(match.group(0)) for match in re.finditer(r'`+', text)), default=0)
    fence = '`' * (longest + 1)
    padding = ' ' if text.startswith('`') or text.endswith('`') else ''
    return f'{fence}{padding}{text}{padding}{fence}'


def _num3(value):
    """三位小数；数值异常时退回转义后的原文，不让格式化异常或原始 HTML 进入报告。"""
    try:
        return f'{float(value):.3f}'
    except Exception:
        return _md(value)


def _fenced(text, lang='text'):
    """围栏代码块：围栏长度随内容动态选择，防止内容里的 ``` 提前结束代码块。"""
    text = str(text)
    longest = max((len(match.group(0)) for match in re.finditer(r'`+', text)), default=0)
    fence = '`' * max(3, longest + 1)
    return f'{fence}{lang}\n{text}\n{fence}'


# 白名单：只保留报告自身与 Markdown 渲染会产生、且可安全展示的标签/属性。
_ALLOWED_TAGS = {
    'html', 'head', 'meta', 'title', 'style', 'body',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'div', 'span', 'small', 'br', 'hr',
    'strong', 'b', 'em', 'i', 'u', 'del', 'ins', 's', 'sup', 'sub', 'mark',
    'ul', 'ol', 'li', 'dl', 'dt', 'dd',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col',
    'blockquote', 'pre', 'code', 'a', 'img', 'figure', 'figcaption',
}
_VOID_TAGS = {'meta', 'br', 'hr', 'img', 'col'}
# 这些标签连同内容一起丢掉（内容本身也不允许出现）
_DROP_WITH_CONTENT = {'script', 'noscript', 'iframe', 'frame', 'frameset', 'object', 'embed',
                      'applet', 'form', 'input', 'button', 'select', 'textarea', 'option',
                      'template', 'svg', 'math', 'base', 'link', 'video', 'audio', 'source',
                      'track', 'canvas', 'portal'}
# HTML 里可以自闭合/没有结束标签的“空元素”；丢弃它们时不能进入“跳过内容”状态
_VOID_HTML = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta',
              'param', 'source', 'track', 'wbr', 'frame'}
_GLOBAL_ATTRS = {'class', 'id', 'title', 'style', 'dir', 'lang', 'role'}
_TAG_ATTRS = {
    'html': {'lang', 'dir'},
    'meta': {'http-equiv', 'content', 'charset', 'name'},
    'a': {'href', 'name', 'target', 'rel'},
    'img': {'src', 'alt', 'width', 'height', 'loading'},
    'th': {'colspan', 'rowspan', 'scope', 'align', 'valign', 'width'},
    'td': {'colspan', 'rowspan', 'align', 'valign', 'width'},
    'col': {'span', 'width'},
    'colgroup': {'span'},
    'ol': {'start', 'type'},
    'table': {'summary', 'border', 'cellpadding', 'cellspacing'},
}
_URL_ATTRS = {'href', 'src'}
_BAD_SCHEMES = ('javascript:', 'vbscript:', 'data:text/html', 'data:text/xml',
                'data:application/xhtml', 'livescript:', 'mocha:')
_SAFE_DATA_IMAGE = ('data:image/png', 'data:image/jpeg', 'data:image/gif',
                    'data:image/webp', 'data:image/bmp')


def _clean_url(value, tag):
    """只放行相对路径 / http(s) / mailto / 图片 data:；拦掉 javascript: 等伪协议。"""
    text = re.sub(r'[\x00-\x20]+', '', str(value or ''))
    lowered = text.lower()
    if not text:
        return None
    if text.startswith('//'):
        return None             # 协议相对 URL：报告不需要，直接拒绝
    if lowered.startswith('data:'):
        return text if lowered.startswith(_SAFE_DATA_IMAGE) else None
    if any(lowered.startswith(bad) for bad in _BAD_SCHEMES):
        return None
    if lowered.startswith('file:') and tag != 'img':
        return None
    return text


def _clean_style(value):
    text = str(value or '')
    lowered = text.lower()
    for bad in ('javascript:', 'expression(', 'behavior:', '-moz-binding',
                '@import', 'url(', '&#', '\\'):
        if bad in lowered:
            return None
    return text


def _clean_css(text):
    """内联 <style> 内容：去掉可加载外部资源或执行表达式的写法。"""
    result = str(text or '')
    for pattern in (r'(?i)javascript\s*:', r'(?i)expression\s*\(', r'(?i)@import',
                    r'(?i)behavior\s*:', r'(?i)-moz-binding', r'(?i)url\s*\(',
                    r'</\s*style'):
        result = re.sub(pattern, '', result)
    return result


def _attr_text(value):
    """属性值转义：& < > " 必须转义；单引号保留（属性用双引号包裹，CSP 内容保持可读）。"""
    return (str('' if value is None else value)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;'))


class _Sanitizer(HTMLParser):
    """白名单清洗：删危险标签/事件属性/伪协议 URL，保留报告自身的结构与样式。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = []          # 正在丢弃内容的标签栈
        self.style_depth = 0

    # -- 标签 ----------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self.skip:
            if tag == self.skip[-1] or (tag in _DROP_WITH_CONTENT and tag not in _VOID_HTML):
                self.skip.append(tag)
            return
        if tag in _DROP_WITH_CONTENT:
            if tag not in _VOID_HTML:
                self.skip.append(tag)
            return
        if tag not in _ALLOWED_TAGS:
            return              # 丢标签、留文本（例如 <marquee>）
        self.parts.append(self._open(tag, attrs))
        if tag == 'style':
            self.style_depth += 1

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self.skip or tag in _DROP_WITH_CONTENT or tag not in _ALLOWED_TAGS:
            return
        self.parts.append(self._open(tag, attrs, self_closing=True))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.skip:
            if tag == self.skip[-1]:
                self.skip.pop()
            elif tag in self.skip:
                while self.skip:
                    if self.skip.pop() == tag:
                        break
            return
        if tag == 'style' and self.style_depth:
            self.style_depth -= 1
        if tag in _VOID_TAGS or tag not in _ALLOWED_TAGS:
            return
        self.parts.append(f'</{tag}>')

    def _open(self, tag, attrs, self_closing=False):
        rendered = []
        allowed = _GLOBAL_ATTRS | _TAG_ATTRS.get(tag, set())
        for name, value in attrs:
            name = str(name).lower()
            if name.startswith('on') or name in ('srcdoc', 'formaction', 'xlink:href',
                                                 'xmlns', 'background', 'poster'):
                continue
            if name not in allowed:
                continue
            if name == 'http-equiv' and str(value or '').lower() != 'content-security-policy':
                continue
            if name in _URL_ATTRS:
                value = _clean_url(value, tag)
                if not value:
                    continue
            elif name == 'style':
                value = _clean_style(value)
                if not value:
                    continue
            rendered.append(f' {name}="{_attr_text(value)}"')
        closing = '/>' if (self_closing or tag in _VOID_TAGS) else '>'
        return f'<{tag}{"".join(rendered)}{closing}'

    # -- 文本 / 注释 ---------------------------------------------------
    def handle_data(self, data):
        if self.skip:
            return
        if self.style_depth:
            self.parts.append(_clean_css(data))
        else:
            self.parts.append(html.escape(data, quote=False))

    def handle_entityref(self, name):
        if not self.skip:
            self.handle_data(f'&{name};')

    def handle_charref(self, name):
        if not self.skip:
            self.handle_data(f'&#{name};')

    def handle_comment(self, data):
        return              # 注释整段丢弃（可能藏条件注释/脚本片段）

    def handle_decl(self, decl):
        if str(decl or '').lower().startswith('doctype'):
            self.parts.append('<!' + str(decl) + '>')


def sanitize_html(markup):
    """最终 HTML 导出的白名单清洗入口（幂等：对已清洗文本再跑一次结果不变）。"""
    parser = _Sanitizer()
    parser.feed(str(markup))
    parser.close()
    return ''.join(parser.parts)


def _figure_files(folder):
    folder = Path(folder)
    figures = folder / 'figures'
    if not figures.is_dir():
        return []
    names = [name for name in FIGURE_ORDER if (figures / name).is_file()]
    extra = sorted(path.name for path in figures.iterdir()
                   if path.is_file() and path.suffix.lower() in ('.svg', '.png')
                   and path.name not in names)
    return names + extra


def _report_css():
    return (
        '<style>body{max-width:980px;margin:0 auto;padding:0 24px;color:#20334b;'
        f'font:10.5pt/1.7 {FONT_STACK}}}'
        'h1{font-size:20pt;margin:0 0 .6em}h2{font-size:15pt;margin:1.6em 0 .5em}'
        'h3{font-size:12pt;margin:1.3em 0 .4em}'
        'table{border-collapse:collapse;width:100%;font-size:9.5pt}'
        'th,td{border:1px solid #dde5ee;padding:4px 8px;text-align:left}'
        'code{font-size:9.5pt;background:#f2f5f8;padding:1px 4px;border-radius:4px}'
        'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f8fa;padding:12px;'
        'border-radius:8px;font-size:9.5pt}'
        'blockquote{margin:1em 0;padding:6px 14px;border-left:3px solid #d7a24a;'
        'background:#fff8ec}'
        'figure{margin:18px 0}img{max-width:100%;height:auto;border:1px solid #e1e8f0;'
        'border-radius:8px}figcaption{color:#5b7186;font-size:9pt}'
        '@media print{body{margin:0;padding:0;max-width:none}img{break-inside:avoid}}'
        '</style>'
    )


def _write_html_report(path, title, body_parts, figures, captions):
    images = ''.join(
        f'<figure id="fig-{index}"><img src="figures/{html.escape(name, quote=True)}" '
        f'width="640" alt="{html.escape(Path(name).stem, quote=True)}">'
        f'<figcaption>{html.escape(captions.get(name) or "", quote=True)}</figcaption></figure>'
        for index, name in enumerate(figures, start=1))
    csp = (f'<meta http-equiv="Content-Security-Policy" content="{CSP_CONTENT}">'
           '<meta name="referrer" content="no-referrer">')
    markup = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
              + csp +
              f'<title>{html.escape(title)}</title>' + _report_css() + '</head><body>'
              f'<h1>{html.escape(title)}</h1>' + images +
              _render_markdown('\n\n'.join(body_parts)) + '</body></html>')
    # 最终导出前再走一遍白名单清洗（数据单元已在生成端转义，这里是第二道防线）
    Path(path).write_text(sanitize_html(markup), encoding='utf-8')


# ---------------------------------------------------------------- 报告总装
def write_reports(folder, facts, qc, result, config, provenance, error=None, fmt=None):
    folder = Path(folder)
    facts = facts or {}
    qc = qc or {}
    config = dict(config or {})
    status = '失败/已阻止' if error else '已完成'
    simulated = bool(facts.get('synthetic'))
    method = config.get('method', '未知')
    info = executed_steps(folder, result, config) if not error else dict(
        steps=[], calls=[], velocity_mode=None, has_velocity=False, has_fate=False, kernel='')
    bio, tech = [], []

    # =========================== A：生物学分析报告
    bio.append('# A 生物学分析报告')
    bio.append(f'任务状态：**{status}**｜方法：**{_method_name(method)}**（{_md(method)}）｜'
               f'核查日期：{CHECKED}')
    if _simulation_banner(simulated):
        bio.append(_simulation_banner(simulated))
    bio.append('## 0. 一句话结论\n\n' + _headline(method, info, error))
    bio.append(EVIDENCE_NOTE)

    # 1. 数据与输入（所有数据单元先转义再进 Markdown）
    bio.append('## 1. 数据与输入\n\n' + '\n'.join([
        f'- 细胞数 × 基因数：{_md(facts.get("n_cells", "未知"))} × '
        f'{_md(facts.get("n_genes", "未知"))}'
        + (f'（文件：{_md(fmt["file"]["name"])}，{_md(fmt["file"]["size_text"])}）' if fmt else ''),
        f'- 物种 / 组织：{_md(config.get("species") or "未提供")} / '
        f'{_md(config.get("tissue") or "未提供")}',
        f'- 供体·样本列：{_md(config.get("sample_key") or "未指定")}'
        f'（未指定时细胞**不是**独立生物学重复）',
        f'- 数据层：{_md(", ".join(fmt["layers_present"])) if fmt and fmt.get("layers_present") else "只有 X 表达矩阵"}',
        f'- 基因 ID 类型：{_md(fmt["var"]["id_kind"]) if fmt else "未知"}',
    ]))

    # 2. 质量
    if qc:
        mt_text = (f'线粒体前缀 {_inline_code(qc.get("mt_prefix", config.get("mt_prefix")))} 命中 '
                   f'{_md(qc.get("mt_genes_found", 0))} 个基因，比例中位数 '
                   f'{np.median(qc["mt_pct"]):.2f}%；未命中时该比例没有解释意义。')
        bio.append('## 2. 数据质量（observed）\n\n' + '\n'.join([
            f'- 每细胞总表达中位数：{np.median(qc["total"]):.1f}',
            f'- 每细胞检测基因数中位数：{np.median(qc["detected"]):.1f}',
            f'- {mt_text}',
            '- 本程序**没有**按通用阈值删除细胞，也没有做双细胞/环境 RNA 校正；是否过滤由研究者决定。',
        ]))
        if (folder / 'figures' / 'qc.svg').exists():
            bio.append('![数据质量](figures/qc.svg)')

    # 3. 准入
    blockers = list(facts.get('warnings', [])) + list(facts.get('errors', []))
    bio.append('## 3. 方法准入与数据前提\n\n' +
               ('\n'.join('- ' + _md(item) for item in blockers)
                if blockers else '- 未记录到阻塞问题。'))

    # 4. 本次结果
    if error:
        bio.append('## 4. 运行限制\n\n' + _fenced(error) + '\n\n'
                   '本次没有可解释的模型结果；**禁止**从失败任务推断趋势、方向或关键基因。'
                   '请查看 B 报告的失败原因与日志后再决定下一步。')
    elif result:
        inference = [step for step in info.get('steps') or []
                     if step in ('velocity', 'fate', 'dpt', 'recover_dynamics', 'latent_time')]
        label = '观测 + 派生统计 + 推断' if inference else '仅观测与派生统计'
        lines = [f'## 4. 本次实际得到的结果（{label}）', '']
        lines.append('- 实际执行并输出的步骤：' + _steps_text(info.get('steps')))
        if info.get('velocity_mode'):
            lines.append(f'- 实际使用的速度模型模式：{_inline_code(info["velocity_mode"])}'
                         '（取自真实调用记录，不是配置默认值）')
        embedding_name = str(result.get('embedding_name') or '').replace('（二维投影可能失真）', '')
        lines.append(f'- 降维展示：{_md(embedding_name)}（二维投影会失真，距离不等于生物学差异）')
        lines.append(f'- 时间/排序字段：'
                     f'{_md(result.get("time_name") or "本次没有产生时间或拟时序")}')
        effective = result.get('effective') or {}
        if effective:
            lines.append('- 实际使用的预处理：' +
                         '，'.join(f'{_md(key)}={_md(value)}'
                                  for key, value in effective.items()))
        bio.append('\n'.join(lines))
        if (folder / 'figures' / 'embedding.svg').exists():
            bio.append('![降维展示](figures/embedding.svg)')

        groups = result.get('groups') or {}
        if groups.get('sizes'):
            sizes = '，'.join(f'{_md(key)}: {_md(value)} 个细胞' for key, value in
                             sorted(groups['sizes'].items(), key=lambda kv: -kv[1]))
            bio.append('### 4.1 分群（' + _md(groups.get('key')) + '）\n\n'
                       f'- 共 {len(groups["sizes"])} 群：{sizes}\n'
                       '- 群编号是算法产物；换参数会得到不同的群，不能自动等同于细胞类型。')
            gene_table = result.get('gene_table') or []
            if gene_table and isinstance(gene_table[0], dict) and 'genes' in gene_table[0]:
                rows = ['| 群 | 细胞数 | 相对高表达基因（描述性） |', '|---|---|---|']
                for row in gene_table:
                    genes = '、'.join(_md(item.get('gene')) for item in row['genes'][:5]) or '无'
                    rows.append(f'| {_md(row.get("group"))} | {_md(row.get("n"))} | {genes} |')
                bio.append('\n'.join(rows) + '\n\n> 这是“组内均值 − 组外均值”的排序，'
                                            '没有做检验，不是标记基因结论。')

        paga = result.get('paga') or {}
        if paga.get('groups'):
            bio.append('### 4.2 群间连接（PAGA，无向）\n\n'
                       f'- 群：{"、".join(_md(item) for item in paga["groups"])}\n'
                       '- 连接强度矩阵保存在 result.json 的 `paga.connectivities`；'
                       '热图见 figures/paga.svg（原始 0–1 数值，未逐行归一化）。\n'
                       '- PAGA 只说明群之间“像不像、连不连”，**不说明谁分化成谁**。')
            hint = paga.get('direction_hint') or {}
            if hint.get('table'):
                rows = ['| 群 | 细胞数 | 平均拟时序 |', '|---|---|---|']
                for row in hint['table']:
                    rows.append(f'| {_md(row.get("group"))} | {_md(row.get("n"))} | '
                                f'{_num3(row.get("mean_time"))} |')
                bio.append('\n'.join(rows) + '\n\n> 方向线索来自指定的起点；'
                                            '拟时序是相对顺序，不是真实时间。')

        fate = result.get('fate') or {}
        if fate.get('terminal_states'):
            probs = np.asarray(fate.get('probabilities') or [], dtype=float)
            names = fate['terminal_states']
            bio.append('### 4.3 终末状态与命运概率（模型推断）\n\n'
                       f'- 识别到的终末状态：{"、".join(_md(name) for name in names)}'
                       f'（估计器 {_md(fate.get("estimator"))}，'
                       f'kernel {_md(fate.get("kernel"))}）')
            stats = fate.get('terminal_state_stats') or {}
            if stats:
                rows = ['| 终末状态 | 细胞数 | 平均拟时序 |', '|---|---|---|']
                for name, item in stats.items():
                    rows.append(f'| {_md(name)} | {_md(item.get("cells"))} | '
                                f'{_num3(item.get("mean_time"))} |')
                bio.append('\n'.join(rows))
            if probs.size:
                bio.append(f'- 每个细胞对 {probs.shape[1]} 个终末状态的概率之和约为 1；'
                           f'最强概率的平均值为 {np.mean(probs.max(1)):.3f}。')
            early = fate.get('early_terminal_states') or []
            if early:
                bio.append(f'- ⚠️ **疑似“早端”终末状态：'
                           f'{"、".join(_md(name) for name in early)}**，'
                           '其平均拟时序低于整体中位数。这通常来自图结构或吸收态，'
                           '**不能**解释为“细胞最终回到早期状态”。')
            drivers = fate.get('drivers') or []
            if drivers:
                bio.append(f'- 命运驱动基因候选（前 {min(5, len(drivers))} 条）：' +
                           '、'.join(_md(item.get('gene')) for item in drivers[:5]) +
                           '（相关性排序，未做因果或扰动验证）')
            if (folder / 'figures' / 'fate.svg').exists():
                bio.append('![命运概率](figures/fate.svg)')
            bio.append('> 终末状态是算法在细胞图上识别的“吸收端”，需要生物学确认；'
                       '命运概率高不等于谱系已被验证。')

        velocity_table = result.get('velocity_table') or []
        if velocity_table:
            rows = ['| 基因 | ' + ' | '.join(_md(key) for key in velocity_table[0]
                                            if key != 'gene') + ' |',
                    '|---|' + '---|' * (len(velocity_table[0]) - 1)]
            for row in velocity_table[:10]:
                cells = [_md(_fmt_num(value, 3)) if isinstance(value, (int, float))
                         else _md(value) for key, value in row.items() if key != 'gene']
                rows.append(f'| {_md(row.get("gene"))} | ' + ' | '.join(cells) + ' |')
            bio.append('### 4.4 速度候选基因（模型内部排序）\n\n' + '\n'.join(rows) +
                       '\n\n> 排序只在该模型、该数据设定内可比；高似然不等于“驱动基因”，'
                       '也不证明因果。')
        dpt = result.get('dpt') or {}
        if dpt:
            lines = [f'- 可达细胞：{_md(dpt.get("reachable", "未知"))} / '
                     f'{_md(dpt.get("n_cells", "未知"))}',
                     f'- diffusion 成分数：{_md(dpt.get("diffmap_components", "未知"))}']
            order = (dpt.get('group_order') or {}).get('table') or []
            if order:
                lines.append('- 各群平均拟时序（方向线索，不是谱系证据）：\n')
                lines.append('| 群 | 细胞数 | 平均拟时序 |')
                lines.append('|---|---|---|')
                for row in order:
                    lines.append(f'| {_md(row.get("group"))} | {_md(row.get("n"))} | '
                                 f'{_num3(row.get("mean_time"))} |')
            bio.append('### 4.5 扩散拟时序（DPT）\n\n' + '\n'.join(lines) +
                       '\n\n> 拟时序是相对顺序，依赖你指定的起点；不是真实小时数。')

        metrics = result.get('metrics') or {}
        if metrics:
            rows = ['| 指标 | 数值 |', '|---|---|']
            for key, value in metrics.items():
                if isinstance(value, (dict, list)):
                    continue
                rows.append(f'| {_md(key)} | {_md(_fmt_num(value, 4))} |')
            bio.append('### 4.6 模型内部诊断指标\n\n' + '\n'.join(rows) +
                       '\n\n> 这些是内部一致性诊断，**不是**外部准确率；'
                       '不同方法的损失/似然不可直接比较。')

        genes = result.get('gene_table') or []
        flat_genes = [item for item in genes if isinstance(item, dict) and 'gene' in item]
        if flat_genes:
            rows = ['| 基因 | ' + ' | '.join(_md(key) for key in flat_genes[0]
                                            if key != 'gene') + ' |',
                    '|---|' + '---|' * (len(flat_genes[0]) - 1)]
            for item in flat_genes[:12]:
                cells = []
                for key, value in item.items():
                    if key == 'gene':
                        continue
                    cells.append(_md(_fmt_num(value, 3)) if isinstance(value, (int, float))
                                 else _md(value))
                rows.append(f'| {_md(item["gene"])} | ' + ' | '.join(cells) + ' |')
            bio.append('### 4.7 候选基因指标（前 12 条）\n\n' + '\n'.join(rows) +
                       '\n\n> 拟合参数的单位依赖缩放；排序只在该模型、该数据设定内可比。'
                       '候选基因不等于因果驱动基因。')

        trends = result.get('gene_trends') or []
        if trends:
            bio.append('### 4.8 探索性基因趋势\n\n'
                       '按相对时间等细胞数分箱，计算 log1p(CP10k) 均值；基因是在**同一份数据**上挑的，'
                       '没有独立检验。图见 figures/trends.svg。\n\n' +
                       '\n'.join(f'- {_md(row.get("gene"))}：探索性 Spearman ρ = '
                                 f'{_num3(row.get("exploratory_spearman"))}'
                                 for row in trends))
            if (folder / 'figures' / 'trends.svg').exists():
                bio.append('![基因趋势](figures/trends.svg)')

        if result.get('caveats'):
            bio.append('### 4.9 本次结果自带的解释边界\n\n' +
                       '\n'.join('- ' + _md(item) for item in result['caveats']))
        bio.append(interpretation_layers(result, config, method, folder=folder, facts=facts))
        if result.get('evidence'):
            bio.append('### 4.11 证据来源\n\n' + '\n'.join('- ' + _md(item)
                                                          for item in result['evidence']))

    # 5. 参数解释
    dynamics_used = 'recover_dynamics' in (info.get('steps') or [])
    velocity_used = 'velocity' in (info.get('steps') or [])
    bio.append('## 5. 参数解释（本次真实取值）\n\n' + '\n'.join([
        f'- `n_top_genes = {_md(config.get("n_top_genes"))}`：高变基因数量；'
        '越多越慢，基因少时自动受限。',
        f'- `n_neighbors = {_md(config.get("n_neighbors"))}`：邻域大小；越大越平滑。',
        f'- `n_pcs = {_md(config.get("n_pcs"))}`：邻域使用的 PCA 维度。',
        f'- `min_shared_counts = {_md(config.get("min_shared_counts"))}`：'
        '剪接共同计数阈值（velocity 用）'
        + ('' if velocity_used else '——本次未触发 velocity，该值未被使用。'),
        f'- `max_iter = {_md(config.get("max_iter"))}`：dynamical 优化迭代上限；不保证收敛'
        + ('' if dynamics_used else '——本次未运行 recover_dynamics，该值未被使用。'),
        f'- `leiden_resolution = {_md(config.get("leiden_resolution"))}`：自动分群分辨率。',
        f'- `use_umap = {_md(config.get("use_umap"))}`：是否用 UMAP 展示（关闭时用 PCA）。',
        f'- `seed = {_md(config.get("seed"))}`：随机种子，影响 PCA/UMAP/分群与模型初始化。',
        '- 默认值是**计算起点**，不是适用于所有组织的最佳参数；参数敏感性需要单独评估。',
    ]))

    # 6. 证据边界
    bio.append('## 6. 证据边界与可比性\n\n' + '\n'.join([
        '- 潜在时间/拟时序**不是**真实时钟；箭头**不是**已验证的谱系；'
        '候选调控关系**不是**因果证据。',
        '- 不同方法的损失、似然与时间尺度不可直接排名；本程序不给出“最佳模型”。',
        '- 正式评测需要按供体/样本预先拆分，特征选择、先验网络、调参与评价标签之间不能泄漏。',
        '- 细胞不是独立生物学重复；本程序不做组间显著性检验。',
        '- 缺少外部时间点、谱系追踪、独立扰动或跨供体复现时，方向/命运/调控结论都应保持为“未知”。',
    ]))

    # 7. 官方方法来源：只列出本次真的执行过的方法
    source_keys = {'qc'}
    if method != 'auto':
        source_keys.add(method)
    else:
        source_keys.add('auto')
        if info.get('has_velocity'):
            mode = info.get('velocity_mode') or 'stochastic'
            source_keys.add('scvelo_' + mode if 'scvelo_' + mode in INDEX else 'scvelo_stochastic')
            source_keys.add('cellrank')
        elif info.get('has_fate'):
            source_keys.add('cellrank')
    bio.append('## 7. 官方方法来源\n\n核查日期：' + CHECKED +
               '。运行版本以 B 报告与环境快照为准。'
               + ('' if error else '仅列出本次实际执行的方法与质控。') + '\n\n' +
               '\n'.join(f'- [{_md(item.name)}]({html.escape(str(item.source), quote=True)})'
                         for item in METHODS
                         if item.source and item.key in source_keys))

    # =========================== B：技术与复现报告
    prov = provenance or {}
    tech.append('# B 技术与复现报告')
    tech.append(f'任务状态：**{status}**｜方法：{_inline_code(method)}｜'
                f'任务目录：{_inline_code(folder.resolve())}'
                + ('｜**模拟数据**' if simulated else ''))
    if error:
        tech.append('## 0. 失败摘要\n\n' + _fenced(error) + '\n\n'
                    '失败任务同样保留了输入、配置、环境、日志与报告，便于定位问题。')

    tech.append('## 1. 运行环境\n\n' + '\n'.join([
        f'- Python：{_md(prov.get("python", "未知"))}',
        f'- 解释器：{_inline_code(prov.get("executable", "未知"))}',
        f'- 平台：{_md(prov.get("platform", "未知"))}',
        f'- 计算设备：{_md(prov.get("device", "cpu"))}'
        + ('（使用 CUDA）' if prov.get('device') == 'cuda' else '（CPU，未调用 CUDA）'),
        f'- 线程限制：{_md(json.dumps(prov.get("thread_limits", {}), ensure_ascii=False))}',
        '- 完整依赖版本：见同目录 `environment.json`（本次解释器真实安装的所有包）。',
    ]))

    if fmt:
        mat = fmt['matrices'].get('X', {})
        tech.append('## 2. 数据格式检查（observed）\n\n' + '\n'.join([
            f'- 文件：{_inline_code(fmt["file"]["name"])}'
            f'（{_md(fmt["file"]["suffix"])}，{_md(fmt["file"]["size_text"])}）',
            f'- 维度：{_md(fmt["shape"]["n_cells"])} 细胞 × {_md(fmt["shape"]["n_genes"])} 基因',
            f'- X：dtype={_md(mat.get("dtype"))}，稀疏={_md(mat.get("sparse"))}，'
            f'非零比例={mat.get("nonzero_pct", 0):.2f}%，'
            f'整数外观={_md(mat.get("integer_like"))}，max={_md(mat.get("max"))}',
            f'- 数据层：{_md(", ".join(fmt["layers_present"])) or "无"}',
            f'- obs 列：{_md(", ".join(item["name"] for item in fmt["obs"])) or "无"}',
            f'- var：{_md(fmt["var"]["id_kind"])}，唯一={_md(fmt["var"]["unique"])}，'
            f'线粒体命中 {_md(fmt["var"]["mt_matched"])} 个',
            f'- 剪接层：{_md(json.dumps(fmt["splice"], ensure_ascii=False)) if fmt["splice"] else "无"}',
            f'- 附加对象：{_md(json.dumps(fmt["extras"], ensure_ascii=False)) if fmt["extras"] else "无"}',
        ]))
        if fmt.get('warnings'):
            tech.append('### 2.1 格式体检发现的风险\n\n' +
                        '\n'.join('- ' + _md(item) for item in fmt['warnings']))
    else:
        tech.append('## 2. 数据格式检查\n\n本次未生成格式体检记录（可能在读取阶段就失败）。')

    tech.append('## 3. 输入指纹与预处理指纹\n\n' + '\n'.join([
        f'- 输入 SHA-256：{_inline_code(prov.get("input_sha256", "未知"))}',
        f'- 细胞 ID 哈希：'
        f'{_inline_code((result or {}).get("cells_hash") or facts.get("cells_hash", "未知"))}',
        f'- 基因 ID 哈希：'
        f'{_inline_code((result or {}).get("genes_hash") or facts.get("genes_hash", "未知"))}',
        f'- 预处理指纹：'
        f'{_inline_code((result or {}).get("preprocessing_hash") or "本次方法未产生")}',
        f'- 先验 GRN SHA-256：{_inline_code(prov.get("prior_grn_sha256") or "未使用")}',
        f'- 代码指纹：`code_sha256` 记录在 provenance.json'
        f'（共 {len(prov.get("code_sha256", {}))} 个文件）。',
    ]))

    tech.append('## 4. 超参数快照\n\n' +
                _fenced(json.dumps({key: value for key, value in config.items()
                                    if key != 'grn_path'},
                                   ensure_ascii=False, indent=2), lang='json') +
                '\n\n完整配置（含 GRN 路径）：`config.json`。')

    calls_path = folder / 'calls.json'
    if calls_path.exists():
        try:
            calls = json.loads(calls_path.read_text(encoding='utf-8'))
            rows = ['| # | 真实调用 | 关键参数 |', '|---|---|---|']
            for index, call in enumerate(calls[:40], 1):
                arguments = json.dumps(call.get('arguments', {}), ensure_ascii=False)
                if len(arguments) > 160:
                    arguments = arguments[:157] + '...'
                rows.append(f'| {index} | {_inline_code(call.get("function"))} | '
                            f'{_inline_code(arguments)} |')
            tech.append('## 5. 真实调用序列\n\n共记录 ' + str(len(calls)) + ' 次函数调用'
                        '（含安装版本签名，见 `calls.json`）。\n\n' + '\n'.join(rows))
        except Exception:
            tech.append('## 5. 真实调用序列\n\n`calls.json` 存在但无法解析。')
    else:
        tech.append('## 5. 真实调用序列\n\n本次没有记录到模型调用'
                    '（可能是质控/演示方法，或在读取阶段就已失败）。')

    tech.append('## 6. 耗时、状态与随机性\n\n' + '\n'.join([
        f'- 开始：{_md(prov.get("started_at", "未知"))}',
        f'- 结束：{_md(prov.get("finished_at", "未知"))}',
        f'- 耗时：{_md(prov.get("elapsed_seconds", "未知"))} 秒（含解释器启动与图形导出）',
        f'- 状态：{_md(prov.get("status", status))}',
        f'- 随机种子：{_md(prov.get("seed", "未知"))}（同时用于 PCA/UMAP/分群）',
        '- 固定种子不保证逐字节相同：并行调度、线性代数后端与平台差异都会带来数值差异。',
    ]))

    tech.append('## 7. 复现方式\n\n'
                f'1. 切到本次保存的源码目录：'
                f'{_inline_code(prov.get("reproduce_cwd", "未知"))}\n'
                '2. 用同一解释器与 `environment.json` 中的版本执行：\n\n'
                + _fenced(prov.get('reproduce_command', '未知')) + '\n'
                '3. 若报告状态为 failed，先看 `run.log`；修复后建议复现到新目录，不要覆盖旧结果。'
                '\n4. `source/` 保存了本次运行时的完整源码副本，可用于对照或重放。')

    files = []
    for name in ('config.json', 'environment.json', 'validation.json', 'format.json',
                 'eligibility.json', 'qc.json', 'result.json', 'gene_table.json', 'provenance.json',
                 'calls.json', 'run.log', 'analysis.h5ad', 'velocity.npz', 'velocity_uncertainty.npz'):
        target = folder / name
        if target.exists():
            files.append(f'- {_inline_code(name)}（{target.stat().st_size / 1024:.0f} KB）')
    figures = sorted((folder / 'figures').glob('*')) if (folder / 'figures').exists() else []
    if figures:
        files.append('- `figures/`：' + '、'.join(_md(path.name) for path in figures))
    tech.append('## 8. 结果文件清单\n\n' + ('\n'.join(files) if files else '- 本次没有生成文件。'))

    # 图注：与绘图使用同一份规格（优先读取落盘的 figure_specs.json）
    figure_files = _figure_files(folder)
    stored = {}
    spec_path = folder / 'figures' / 'figure_specs.json'
    if spec_path.is_file():
        try:
            stored = json.loads(spec_path.read_text(encoding='utf-8'))
        except Exception:
            stored = {}
    captions = {}
    for index, name in enumerate(figure_files, start=1):
        caption = _figure_caption(name, result, config, index, qc=qc, specs=stored)
        captions[name] = caption
    if captions:
        bio.append('### 4.12 图注\n\n' +
                   '\n'.join('- ' + _md(captions[name]) for name in figure_files))

    # 先落盘 A 报告，再按**实际写出的文件**校验本地图片引用（F18：导出前校验）
    (folder / 'A_生物学分析报告.md').write_text('\n\n'.join(bio) + '\n', encoding='utf-8')
    _write_html_report(folder / 'A_report.html', 'A 生物学分析报告', bio, figure_files, captions)

    refs = report_refs(folder)
    all_refs = sorted({ref for values in refs.values() for ref in values})
    missing = missing_report_refs(folder)
    asset_lines = [f'- HTML/Markdown 共引用 {len(all_refs)} 个本地图片：'
                   + ('、'.join(_inline_code(ref) for ref in all_refs) if all_refs else '无')]
    if missing:
        for name, values in missing.items():
            asset_lines.append(f'- ⚠️ {_inline_code(name)} 缺失引用：'
                               + '、'.join(_inline_code(ref) for ref in values))
    else:
        asset_lines.append('- 校验结果：全部存在，ZIP 导出后可在离线环境打开'
                           '（含 figures/ 下所有 png/svg，例如 stream.png）。')
    if (folder / 'figures' / 'figure_specs.json').exists():
        asset_lines.append('- 图形规格（实际着色字段/色阶/坐标哈希）：'
                           '`figures/figure_specs.json`。')
    tech.append('## 8.1 导出资源完整性\n\n' + '\n'.join(asset_lines))

    tech.append('## 9. 保密、联网与清理\n\n' + '\n'.join([
        '- 数据、图、报告、日志、样本 ID 与绝对路径**都只在本机**；'
        '本次分析没有云端调用、不需要 API 密钥。',
        '- 安装/更新依赖、点击官方文档链接需要联网；界面上的外部入口都会标注“需联网”。',
        '- 报告与日志可能包含基因/细胞标识与本地路径，分享前请自行检查。',
        f'- 清理本次结果：删除 {_inline_code(folder.resolve())} 即可；原始导入文件不会被修改。',
    ]))

    (folder / 'B_技术与复现报告.md').write_text('\n\n'.join(tech) + '\n', encoding='utf-8')
    _write_html_report(folder / 'B_report.html', 'B 技术与复现报告', tech, [], {})
