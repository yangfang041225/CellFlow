"""生成两份中文报告 + 静态 SVG 图形。

A 生物学分析报告：数据质量、实际产生的轨迹/动力学结果、候选基因、参数解释、限制与证据来源。
B 技术与复现报告：数据格式、环境版本、真实调用、超参数、命令、耗时、种子、日志、失败原因、路径与复现方式。

原则：只写程序真正算出来的东西；模拟结果必须标记；没有证据的结论一律留白。
"""
from pathlib import Path
import html
import json
import numpy as np
from .registry import METHODS, CHECKED, INDEX

EVIDENCE_NOTE = (
    '证据分级：**observed（观测）**＝直接从数据算出的数值；**assumed（假设）**＝需要你声明或核实的前提；'
    '**inferred（推断）**＝模型输出（速度方向、拟时序、命运概率、调控关系）；**unknown（未知）**＝本次没有证据，'
    '程序不会替你补写。'
)


# ---------------------------------------------------------------- SVG 工具
def stream_png(coords, arrows, out_path, grid=45):
    """速度流线图：把细胞上的向量插值到规则网格后用 streamplot 画。

    仅用于显示走向与密度，不代表单细胞追踪；插值会平滑掉局部细节。
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
        from scipy.interpolate import griddata
    except Exception:
        return None
    points = np.asarray(coords, dtype=float)
    vectors = np.asarray(arrows, dtype=float)
    if points.ndim != 2 or vectors.shape != points.shape:
        return None
    x, y = points[:, 0], points[:, 1]
    u, v = vectors[:, 0], vectors[:, 1]
    if not (np.isfinite(x).all() and np.isfinite(y).all()):
        return None
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
    ax.set_title('Velocity stream field (grid-interpolated for display)', fontsize=10)
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


def scatter_svg(coords, color, title, labels, arrows=None, arrow_scale=1.0, arrow_density=120):
    p = np.asarray(coords, dtype=float)
    c = np.asarray([v if v is not None else np.nan for v in color], dtype=float)
    good = np.isfinite(p).all(1)
    if not good.any():
        good = np.ones(len(p), bool)
    lo = np.nanmin(p[good], axis=0)
    hi = np.nanmax(p[good], axis=0)
    xy = 60 + (p - lo) / np.maximum(hi - lo, 1e-9) * np.array([700, 410])
    xy[:, 1] = 510 - xy[:, 1]
    finite = c[np.isfinite(c)]
    cmin, cmax = (float(finite.min()), float(finite.max())) if len(finite) else (0, 1)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 850 570" role="img">'
             f'<rect width="850" height="570" fill="#fafcff"/>'
             f'<text x="45" y="35" font-size="19" font-family="sans-serif">{html.escape(title)}</text>'
             f'<path d="M60 65 V485 H790" fill="none" stroke="#a8b9ca"/>']
    for i in np.flatnonzero(good):
        value = np.clip((c[i] - cmin) / max(cmax - cmin, 1e-9), 0, 1) if np.isfinite(c[i]) else 0
        fill = f'hsl({235 - 200 * value:.1f},68%,49%)' if np.isfinite(c[i]) else '#aaa'
        parts.append(f'<circle cx="{xy[i, 0]:.2f}" cy="{xy[i, 1]:.2f}" r="3.1" opacity=".7" fill="{fill}">'
                     f'<title>{html.escape(str(labels[i]))}: {c[i]:.3g}</title></circle>')
    if arrows is not None:
        v = np.asarray(arrows, dtype=float)
        norm = np.linalg.norm(v, axis=1)
        finite_norm = norm[np.isfinite(norm)]
        scale = np.nanpercentile(finite_norm, 90) if finite_norm.size else 0
        if np.isfinite(scale) and scale > 0:
            parts.append('<defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="5.5" refY="3.5" '
                         'orient="auto"><path d="M0 0 L7 3.5 L0 7" fill="#182e4a"/></marker></defs>')
            step = max(1, len(p) // max(1, int(arrow_density)))
            for i in np.flatnonzero(good)[::step]:
                if not np.isfinite(v[i]).all():
                    continue
                length_i = float(norm[i])
                if not np.isfinite(length_i) or length_i <= 0:
                    continue
                # 只影响显示：方向不变，长度按 90 分位归一到 34px，并限制 8–60px 保证可见
                length = min(70.0, max(6.0, length_i / scale * 34.0 * float(arrow_scale)))
                unit = v[i] / length_i
                dx, dy = unit * length
                parts.append(f'<path d="M{xy[i, 0]:.2f} {xy[i, 1]:.2f} l{dx:.2f} {-dy:.2f}" '
                             f'stroke="#182e4a" stroke-width="1.3" marker-end="url(#arrow)" opacity=".8"/>')
    parts.append(f'<text x="60" y="525" font-size="14" font-family="sans-serif">'
                 f'Color range: {cmin:.3g} — {cmax:.3g}; gray = unavailable</text>'
                 f'<text x="60" y="552" font-size="13" font-family="sans-serif">{len(p)} cells · '
                 f'arrows normalized to the 90th percentile (34 px, display only) · hover for values</text></svg>')
    return ''.join(parts)


def bar_svg(title, items, unit='', note=''):
    """items: [(标签, 数值)]，横向条形图。"""
    if not items:
        return ''
    top = max(max(v for _, v in items), 1e-9)
    rows = []
    for i, (label, value) in enumerate(items):
        y = 90 + i * 34
        width = 560 * (value / top)
        rows.append(f'<text x="150" y="{y + 16}" font-size="13" text-anchor="end" '
                    f'font-family="sans-serif">{html.escape(str(label))}</text>'
                    f'<rect x="165" y="{y}" width="{max(width, 1):.1f}" height="20" rx="4" fill="#176b87" opacity=".8"/>'
                    f'<text x="{172 + width:.1f}" y="{y + 15}" font-size="12" '
                    f'font-family="sans-serif">{value:.3g}{html.escape(unit)}</text>')
    height = max(140, 130 + 34 * len(items))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 850 {height}" role="img">'
            f'<rect width="850" height="{height}" fill="#fafcff"/>'
            f'<text x="45" y="38" font-size="18" font-family="sans-serif">{html.escape(title)}</text>'
            + ''.join(rows) +
            f'<text x="45" y="{height - 18}" font-size="12" font-family="sans-serif">{html.escape(note)}</text>'
            f'</svg>')


def matrix_heatmap_svg(title, groups, matrix, note=''):
    n = len(groups)
    size = min(520, max(360, 40 * n + 120))
    cell = (size - 130) / max(n, 1)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 850 {size + 60}" role="img">'
             f'<rect width="850" height="{size + 60}" fill="#fafcff"/>'
             f'<text x="45" y="34" font-size="18" font-family="sans-serif">{html.escape(title)}</text>']
    for j in range(n):
        parts.append(f'<text x="{130 + j * cell + cell / 2:.1f}" y="{70}" font-size="11" text-anchor="end" '
                     f'transform="rotate(-45 {130 + j * cell + cell / 2:.1f} 70)" '
                     f'font-family="sans-serif">{html.escape(str(groups[j]))}</text>')
    for i in range(n):
        parts.append(f'<text x="120" y="{80 + i * cell + cell / 2 + 4:.1f}" font-size="11" text-anchor="end" '
                     f'font-family="sans-serif">{html.escape(str(groups[i]))}</text>')
        for j in range(n):
            v = float(np.clip(matrix[i][j], 0, 1))
            color = f'rgb({int(245 - 140 * v)},{int(250 - 90 * v)},{int(255 - 60 * v)})'
            parts.append(f'<rect x="{130 + j * cell:.1f}" y="{80 + i * cell:.1f}" width="{cell - 1:.1f}" '
                         f'height="{cell - 1:.1f}" fill="{color}"><title>{html.escape(str(groups[i]))} → '
                         f'{html.escape(str(groups[j]))}: {v:.3f}</title></rect>')
    parts.append(f'<text x="45" y="{size + 40}" font-size="12" font-family="sans-serif">'
                 f'{html.escape(note)}</text></svg>')
    return ''.join(parts)


# ---------------------------------------------------------------- 图形总装
def make_figures(folder, data, qc, result, config=None):
    figures = folder / 'figures'
    figures.mkdir(exist_ok=True)
    labels = data.cells
    title = ('SIMULATION / ' if data.synthetic else '')
    (figures / 'qc.svg').write_text(
        scatter_svg(np.c_[qc['total'], qc['detected']], qc['mt_pct'],
                    title + 'Counts vs detected genes · color: mt %', labels), encoding='utf-8')
    if result.get('time'):
        color = result['time']
        color_name = 'model time'
    else:
        color = qc['total']
        color_name = 'total counts (library size)'
    name_text = (result.get('embedding_name') or 'Embedding').replace('（按自动分群结果着色）', '')
    cfg = config or {}
    (figures / 'embedding.svg').write_text(
        scatter_svg(result['embedding'], color,
                    title + name_text + ' · color: ' + color_name, labels, result.get('arrows'),
                    arrow_scale=float(cfg.get('arrow_scale', 1.0)),
                    arrow_density=int(cfg.get('arrow_density', 120))),
        encoding='utf-8')
    if result.get('arrows') and result.get('embedding'):
        try:
            stream_png(result['embedding'], result['arrows'], figures / 'stream.png')
        except Exception:
            pass
    if result.get('gene_trends'):
        series = result['gene_trends']
        colors = ['#176b87', '#e1783b', '#745ac4', '#289976', '#b0497c']
        max_y = max(b['mean_expression'] for r in series for b in r['bins'])
        all_t = [b['time'] for r in series for b in r['bins']]
        lo = min(all_t)
        span = max(max(all_t) - lo, 1e-9)
        parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 850 560">'
                 '<rect width="850" height="560" fill="#fafcff"/>'
                 '<text x="50" y="35" font-family="sans-serif" font-size="18">'
                 'Exploratory expression trends · log1p(CP10k)</text>'
                 '<path d="M60 65 V475 H790" fill="none" stroke="#a8b9ca"/>']
        for k, (r, color_) in enumerate(zip(series, colors)):
            points = ' '.join(f"{60 + (b['time'] - lo) / span * 700:.2f},"
                              f"{475 - b['mean_expression'] / max(max_y, 1e-9) * 380:.2f}" for b in r['bins'])
            parts.append(f'<polyline points="{points}" fill="none" stroke="{color_}" stroke-width="2.5"/>')
            parts.append(f'<text x="{60 + 120 * k}" y="510" fill="{color_}" font-family="sans-serif" '
                         f'font-size="12">{html.escape(r["gene"])}</text>')
        parts.append('<text x="60" y="545" font-family="sans-serif" font-size="13">Genes selected on same data · '
                     'descriptive only · not causal or independent validation</text></svg>')
        (figures / 'trends.svg').write_text(''.join(parts), encoding='utf-8')
    if all(k in data.layers for k in ('spliced', 'unspliced')):
        g = int(np.argmax(np.asarray(data.layers['spliced'].sum(0)).ravel()))

        def col(matrix):
            x = matrix[:, g]
            return np.asarray(x.toarray() if hasattr(x, 'toarray') else x).ravel()

        (figures / 'phase.svg').write_text(
            scatter_svg(np.c_[col(data.layers['spliced']), col(data.layers['unspliced'])],
                        result.get('time') or qc['total'],
                        title + 'Phase portrait: ' + str(data.genes[g]) + ' · x: spliced; y: unspliced',
                        labels), encoding='utf-8')
    fate = result.get('fate') or {}
    if fate.get('terminal_states'):
        names = fate['terminal_states']
        probs = np.asarray(fate.get('probabilities') or [], dtype=float)
        groups = (result.get('groups') or {})
        note_lines = []
        if probs.size and probs.shape[1] == len(names):
            labels_g = groups.get('labels')
            items = []
            if labels_g and len(labels_g) == probs.shape[0]:
                for gname in sorted(set(map(str, labels_g))):
                    mask = np.asarray(labels_g, dtype=str) == gname
                    items += [(f'{gname} · {n}', float(probs[mask, k].mean()))
                              for k, n in enumerate(names)]
            else:
                items = [(n, float(probs[:, k].mean())) for k, n in enumerate(names)]
            (figures / 'fate.svg').write_text(
                bar_svg(title + 'Mean fate probability per state', items, unit='',
                        note='Model inference only · terminal states need biological confirmation'),
                encoding='utf-8')
        stats = fate.get('terminal_state_stats') or {}
        for name, info in stats.items():
            note_lines.append(f'{name}: {info["cells"]} cells, mean pseudotime {info["mean_time"]:.3g}')
    paga = result.get('paga') or {}
    if paga.get('connectivities'):
        matrix = np.asarray(paga['connectivities'], dtype=float)
        rows = matrix / np.maximum(matrix.max(axis=1, keepdims=True), 1e-9)
        (figures / 'paga.svg').write_text(
            matrix_heatmap_svg(title + 'PAGA connectivity (row-normalized)',
                               [str(g) for g in paga['groups']], rows.tolist(),
                               note='Undirected connectivity · not lineage direction'),
            encoding='utf-8')
    # 便携交互图（内嵌 plotly，不访问 CDN）
    try:
        import plotly.graph_objects as go
        p = np.asarray(result['embedding'])
        fig = go.Figure(go.Scattergl(x=p[:, 0], y=p[:, 1], mode='markers', text=labels,
                                     marker=dict(size=5, color=color, colorscale='Viridis', showscale=True)))
        fig.update_layout(title=title + (result.get('embedding_name') or ''), template='plotly_white', height=700)
        fig.write_html(figures / 'embedding.html', include_plotlyjs=True, full_html=True)
    except Exception:
        pass


def dump_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False, default=str),
                          encoding='utf-8')


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



def interpretation_layers(result, config, method_key):
    """把解释拆成“直接观察 / 计算推断 / 生物学假说”三层，避免把推断写成事实。"""
    metrics = (result or {}).get('metrics') or {}
    groups = (result or {}).get('groups') or {}
    fate = (result or {}).get('fate') or {}
    gene_table = (result or {}).get('gene_table') or []

    observed = []
    if metrics.get('n_groups'):
        observed.append(f"分群得到 {int(metrics['n_groups'])} 个群（Leiden 分辨率 {config.get('leiden_resolution', '默认')}）。")
    if metrics.get('velocity_length_median') is not None:
        observed.append(f"速度向量长度中位数 {_fmt_num(metrics['velocity_length_median'])}（相对单位，不能换算成速度）。")
    if metrics.get('velocity_confidence_median') is not None:
        observed.append(f"速度置信度中位数 {_fmt_num(metrics['velocity_confidence_median'])}（越高表示局部方向越一致）。")
    if metrics.get('mean_max_fate_probability') is not None:
        observed.append(f"每个细胞最大命运概率的均值 {_fmt_num(metrics['mean_max_fate_probability'])}。")
    if metrics.get('n_terminal_states') is not None:
        observed.append(f"识别到 {int(metrics['n_terminal_states'])} 个终末状态。")
    if fate.get('states'):
        observed.append('终末状态：' + '、'.join(str(item) for item in fate['states'][:6]) + '。')
    if not observed:
        observed.append('本次输出以质控与结构指标为主，没有可重复读出的动力学数值。')

    inferred = []
    if method_key in ('scvelo', 'auto', 'scvelo_dynamical', 'scvelo_stochastic'):
        mode = config.get('velocity_model') or 'stochastic'
        inferred.append(f"速度方向由 scVelo（{mode}）在 spliced/unspliced 计数上推断，依赖剪接动力学假设。")
    elif method_key == 'velovi':
        inferred.append('速度与潜在时间来自 veloVI 变分推断（后验近似），与 scVelo 的点估计不等价。')
    elif method_key == 'cellrank':
        inferred.append('命运概率由 CellRank 在速度/拟时序核上计算，是转移概率权重，不是实验测得的命运。')
    elif method_key == 'dpt':
        inferred.append('拟时序是扩散距离的排序，不是真实物理时间；换起点会改变顺序。')
    if groups:
        inferred.append('群间连接（PAGA）是转录相似性的汇总，连线不代表真实谱系或因果。')
    if gene_table:
        inferred.append('候选基因按模型内部统计量排序，未做显著性检验，不代表标记基因或调控关系。')
    if not inferred:
        inferred.append('本方法不产生动力学推断；结构结果只描述当前数据的几何关系。')

    hypotheses = []
    if gene_table:
        hypotheses.append('候选基因可作为后续实验（敲低/过表达、时间序列）验证的起点；本报告不据此断言细胞功能、分化终点或因果关系。')
    else:
        hypotheses.append('当前输出不足以形成可检验的生物学假说；需要真实元数据、分组依据或外部证据。')

    lines = ['### 4.10 解释分层（观察 / 推断 / 假说）', '',
             '**直接观察（可从输出直接读出）**', '']
    lines += ['- ' + item for item in observed]
    lines += ['', '**计算推断（依赖模型假设）**', '']
    lines += ['- ' + item for item in inferred]
    lines += ['', '**生物学假说（需要独立证据）**', '']
    lines += ['- ' + item for item in hypotheses]
    lines += ['', '三层不可互换：观察可以复现，推断依赖模型，假说需要实验或外部数据支持。']
    return '\n'.join(lines) + '\n\n'


def _figure_caption(filename, result, config, index):
    """图注：说明画的是什么、颜色/箭头含义、以及不能从图里推出什么。"""
    stem = Path(filename).stem
    result = result or {}
    basis = result.get('embedding_basis') or 'UMAP'
    embedding = str(basis).upper()
    if stem == 'embedding':
        text = (f'细胞在 {embedding} 上的投影；二维表示可能失真，不等于真实距离；'
                '颜色为本次运行的分组或模型时间')
        if result.get('arrows'):
            text += ('；箭头是速度向量的投影，长度按 90 分位归一化仅用于显示，'
                     '不改变底层速度值')
    elif stem == 'stream':
        text = ('速度流线场：把细胞向量插值到规则网格后用 streamplot 绘制，'
                '仅用于观察走向与密度；插值会平滑局部差异，不是单细胞追踪')
    elif stem == 'qc':
        text = '每细胞质量控制：总计数、检测基因数、线粒体基因比例；阈值由研究者结合组织判断，本图不自动过滤'
    elif stem == 'phase':
        text = '剪接相图：spliced 与 unspliced 计数的关系；用于检查剪接动力学假设是否合理'
    elif stem == 'trends':
        text = '候选基因表达趋势：按相对时间等细胞数分箱的 log1p(CP10k) 均值；探索性，未做显著性检验'
    elif stem == 'paga':
        text = 'PAGA 群间连接：节点为 Leiden 群，连线为转录相似性的汇总；无向，不代表真实谱系或因果'
    elif stem == 'fate':
        text = ('终末状态与命运概率：CellRank 在速度/拟时序核上的模型推断，'
                '是转移权重而非实验观测到的命运')
    else:
        text = f'{stem} 输出'
    return f'图 {index} · {text}'


def _render_markdown(text):
    """把报告 Markdown 渲染成 HTML；缺少 markdown 库时退回 <pre>，不假装渲染成功。"""
    try:
        import markdown as _markdown
        return _markdown.markdown(text, extensions=['extra', 'sane_lists'])
    except Exception:
        return '<pre>' + html.escape(text) + '</pre>'


def write_reports(folder, facts, qc, result, config, provenance, error=None, fmt=None):
    folder = Path(folder)
    status = '失败/已阻止' if error else '已完成'
    simulated = bool(facts.get('synthetic'))
    method = config.get('method', '未知')
    bio, tech = [], []

    # =========================== A：生物学分析报告
    bio.append('# A 生物学分析报告')
    bio.append(f'任务状态：**{status}**｜方法：**{_method_name(method)}**（{method}）｜'
               f'核查日期：{CHECKED}')
    if _simulation_banner(simulated):
        bio.append(_simulation_banner(simulated))
    bio.append('## 0. 一句话结论\n\n' + (
        '本次没有产生可解释的模型结果，不能据此推断任何趋势或关键基因。' if error else
        ('本次只做了描述性质控与降维展示，没有拟合动力学模型，因此**不提供**速度方向、谱系或调控结论。'
         if method in ('qc', 'demo') else
         f'本次用 {_method_name(method)} 在**全部细胞**上做了一次探索性拟合；'
         '结果属于模型推断，需要独立实验证据才能支持生物学结论。')))
    bio.append(EVIDENCE_NOTE)

    # 1. 数据与输入
    shape = fmt.get('shape') if fmt else {}
    bio.append('## 1. 数据与输入\n\n' + '\n'.join([
        f'- 细胞数 × 基因数：{facts.get("n_cells", "未知")} × {facts.get("n_genes", "未知")}'
        + (f'（文件：{fmt["file"]["name"]}，{fmt["file"]["size_text"]}）' if fmt else ''),
        f'- 物种 / 组织：{config.get("species") or "未提供"} / {config.get("tissue") or "未提供"}',
        f'- 供体·样本列：{config.get("sample_key") or "未指定"}'
        f'（未指定时细胞**不是**独立生物学重复）',
        f'- 数据层：{", ".join(fmt["layers_present"]) if fmt and fmt.get("layers_present") else "只有 X 表达矩阵"}',
        f'- 基因 ID 类型：{fmt["var"]["id_kind"] if fmt else "未知"}',
    ]))

    # 2. 质量
    if qc:
        mt_text = (f'线粒体前缀 `{qc.get("mt_prefix", config.get("mt_prefix"))}` 命中 '
                   f'{qc.get("mt_genes_found", 0)} 个基因，比例中位数 '
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
    bio.append('## 3. 方法准入与数据前提\n\n' + ('\n'.join('- ' + str(x) for x in blockers) if blockers
                                                 else '- 未记录到阻塞问题。'))

    # 4. 本次结果
    if error:
        bio.append('## 4. 运行限制\n\n```text\n' + str(error) + '\n```\n\n'
                   '本次没有可解释的模型结果；**禁止**从失败任务推断趋势、方向或关键基因。'
                   '请查看 B 报告的失败原因与日志后再决定下一步。')
    elif result:
        lines = ['## 4. 本次实际得到的结果（' + ('观测 + 推断' if method not in ('qc', 'demo') else '仅观测') + '）', '']
        embedding_name = str(result.get('embedding_name') or '').replace('（二维投影可能失真）', '')
        lines.append(f'- 降维展示：{embedding_name}（二维投影会失真，距离不等于生物学差异）')
        lines.append(f'- 时间/排序字段：{result.get("time_name") or "本次没有产生时间或拟时序"}')
        effective = result.get('effective') or {}
        if effective:
            lines.append('- 实际使用的预处理：' + '，'.join(f'{k}={v}' for k, v in effective.items()))
        bio.append('\n'.join(lines))
        if (folder / 'figures' / 'embedding.svg').exists():
            bio.append('![降维展示](figures/embedding.svg)')

        groups = result.get('groups') or {}
        if groups.get('sizes'):
            sizes = '，'.join(f'{k}: {v} 个细胞' for k, v in
                             sorted(groups['sizes'].items(), key=lambda kv: -kv[1]))
            bio.append('### 4.1 分群（' + str(groups.get('key')) + '）\n\n'
                       f'- 共 {len(groups["sizes"])} 群：{sizes}\n'
                       '- 群编号是算法产物；换参数会得到不同的群，不能自动等同于细胞类型。')
            gt = result.get('gene_table') or []
            if gt and isinstance(gt[0], dict) and 'genes' in gt[0]:
                rows = ['| 群 | 细胞数 | 相对高表达基因（描述性） |', '|---|---|---|']
                for row in gt:
                    genes = '、'.join(g['gene'] for g in row['genes'][:5]) or '无'
                    rows.append(f'| {row["group"]} | {row["n"]} | {genes} |')
                bio.append('\n'.join(rows) + '\n\n> 这是“组内均值 − 组外均值”的排序，没有做检验，不是标记基因结论。')

        paga = result.get('paga') or {}
        if paga.get('groups'):
            bio.append('### 4.2 群间连接（PAGA，无向）\n\n'
                       f'- 群：{"、".join(map(str, paga["groups"]))}\n'
                       f'- 连接强度矩阵保存在 result.json 的 `paga.connectivities`；热图见 figures/paga.svg。\n'
                       '- PAGA 只说明群之间“像不像、连不连”，**不说明谁分化成谁**。')
            hint = paga.get('direction_hint') or {}
            if hint.get('table'):
                rows = ['| 群 | 细胞数 | 平均拟时序 |', '|---|---|---|']
                for row in hint['table']:
                    rows.append(f'| {row["group"]} | {row["n"]} | {row["mean_time"]:.3f} |')
                bio.append('\n'.join(rows) + '\n\n> 方向线索来自指定的起点；拟时序是相对顺序，不是真实时间。')

        fate = result.get('fate') or {}
        if fate.get('terminal_states'):
            probs = np.asarray(fate.get('probabilities') or [], dtype=float)
            names = fate['terminal_states']
            bio.append('### 4.3 终末状态与命运概率（模型推断）\n\n'
                       f'- 识别到的终末状态：{"、".join(names)}（估计器 {fate.get("estimator")}，'
                       f'kernel {fate.get("kernel")}）')
            stats = fate.get('terminal_state_stats') or {}
            if stats:
                rows = ['| 终末状态 | 细胞数 | 平均拟时序 |', '|---|---|---|']
                for name, info in stats.items():
                    rows.append(f'| {name} | {info["cells"]} | {info["mean_time"]:.3f} |')
                bio.append('\n'.join(rows))
            if probs.size:
                bio.append(f'- 每个细胞对 {probs.shape[1]} 个终末状态的概率之和约为 1；'
                           f'最强概率的平均值为 {np.mean(probs.max(1)):.3f}。')
            early = fate.get('early_terminal_states') or []
            if early:
                bio.append(f'- ⚠️ **疑似“早端”终末状态：{"、".join(early)}**，其平均拟时序低于整体中位数。'
                           '这通常来自图结构或吸收态，**不能**解释为“细胞最终回到早期状态”。')
            drivers = fate.get('drivers') or []
            if drivers:
                bio.append(f'- 命运驱动基因候选（前 {min(5, len(drivers))} 条）：' +
                           '、'.join(str(d.get('gene')) for d in drivers[:5]) +
                           '（相关性排序，未做因果或扰动验证）')
            if (folder / 'figures' / 'fate.svg').exists():
                bio.append('![命运概率](figures/fate.svg)')
            bio.append('> 终末状态是算法在细胞图上识别的“吸收端”，需要生物学确认；'
                       '命运概率高不等于谱系已被验证。')

        velocity_table = result.get('velocity_table') or []
        if velocity_table:
            rows = ['| 基因 | ' + ' | '.join(k for k in velocity_table[0] if k != 'gene') + ' |',
                    '|---|' + '---|' * (len(velocity_table[0]) - 1)]
            for row in velocity_table[:10]:
                cells = [_fmt_num(v, 3) if isinstance(v, (int, float)) else str(v)
                         for k, v in row.items() if k != 'gene']
                rows.append(f'| {row.get("gene")} | ' + ' | '.join(cells) + ' |')
            bio.append('### 4.4 速度候选基因（模型内部排序）\n\n' + '\n'.join(rows) +
                       '\n\n> 排序只在该模型、该数据设定内可比；高似然不等于“驱动基因”，也不证明因果。')
        dpt = result.get('dpt') or {}
        if dpt:
            lines = [f'- 可达细胞：{dpt.get("reachable", "未知")} / {dpt.get("n_cells", "未知")}',
                     f'- diffusion 成分数：{dpt.get("diffmap_components", "未知")}']
            order = (dpt.get('group_order') or {}).get('table') or []
            if order:
                lines.append('- 各群平均拟时序（方向线索，不是谱系证据）：\n')
                lines.append('| 群 | 细胞数 | 平均拟时序 |')
                lines.append('|---|---|---|')
                for row in order:
                    lines.append(f'| {row["group"]} | {row["n"]} | {row["mean_time"]:.3f} |')
            bio.append('### 4.5 扩散拟时序（DPT）\n\n' + '\n'.join(lines) +
                       '\n\n> 拟时序是相对顺序，依赖你指定的起点；不是真实小时数。')

        metrics = result.get('metrics') or {}
        if metrics:
            rows = ['| 指标 | 数值 |', '|---|---|']
            for key, value in metrics.items():
                if isinstance(value, (dict, list)):
                    continue
                rows.append(f'| {key} | {_fmt_num(value, 4)} |')
            bio.append('### 4.6 模型内部诊断指标\n\n' + '\n'.join(rows) +
                       '\n\n> 这些是内部一致性诊断，**不是**外部准确率；不同方法的损失/似然不可直接比较。')

        genes = result.get('gene_table') or []
        flat_genes = [g for g in genes if isinstance(g, dict) and 'gene' in g]
        if flat_genes:
            rows = ['| 基因 | ' + ' | '.join(k for k in flat_genes[0] if k != 'gene') + ' |',
                    '|---|' + '---|' * (len(flat_genes[0]) - 1)]
            for g in flat_genes[:12]:
                cells = []
                for k, v in g.items():
                    if k == 'gene':
                        continue
                    cells.append(_fmt_num(v, 3) if isinstance(v, (int, float)) else str(v))
                rows.append(f'| {g["gene"]} | ' + ' | '.join(cells) + ' |')
            bio.append('### 4.7 候选基因指标（前 12 条）\n\n' + '\n'.join(rows) +
                       '\n\n> 拟合参数的单位依赖缩放；排序只在该模型、该数据设定内可比。'
                       '候选基因不等于因果驱动基因。')

        trends = result.get('gene_trends') or []
        if trends:
            bio.append('### 4.8 探索性基因趋势\n\n'
                       '按相对时间等细胞数分箱，计算 log1p(CP10k) 均值；基因是在**同一份数据**上挑的，'
                       '没有独立检验。图见 figures/trends.svg。\n\n' +
                       '\n'.join(f'- {r["gene"]}：探索性 Spearman ρ = {r["exploratory_spearman"]:.3f}'
                                 for r in trends))
            if (folder / 'figures' / 'trends.svg').exists():
                bio.append('![基因趋势](figures/trends.svg)')

        if result.get('caveats'):
            bio.append('### 4.9 本次结果自带的解释边界\n\n' +
                       '\n'.join('- ' + str(x) for x in result['caveats']))
        bio.append(interpretation_layers(result, config, method))
        if result.get('evidence'):
            bio.append('### 4.11 证据来源\n\n' + '\n'.join('- ' + str(x) for x in result['evidence']))

    # 5. 参数解释
    bio.append('## 5. 参数解释（本次真实取值）\n\n' + '\n'.join([
        f'- `n_top_genes = {config.get("n_top_genes")}`：高变基因数量；越多越慢，基因少时自动受限。',
        f'- `n_neighbors = {config.get("n_neighbors")}`：邻域大小；越大越平滑。',
        f'- `n_pcs = {config.get("n_pcs")}`：邻域使用的 PCA 维度。',
        f'- `min_shared_counts = {config.get("min_shared_counts")}`：剪接共同计数阈值（velocity 用）。',
        f'- `max_iter = {config.get("max_iter")}`：dynamical 优化迭代上限；不保证收敛。',
        f'- `leiden_resolution = {config.get("leiden_resolution")}`：自动分群分辨率。',
        f'- `use_umap = {config.get("use_umap")}`：是否用 UMAP 展示（关闭时用 PCA）。',
        f'- `seed = {config.get("seed")}`：随机种子，影响 PCA/UMAP/分群与模型初始化。',
        '- 默认值是**计算起点**，不是适用于所有组织的最佳参数；参数敏感性需要单独评估。',
    ]))

    # 6. 证据边界
    bio.append('## 6. 证据边界与可比性\n\n' + '\n'.join([
        '- 潜在时间/拟时序**不是**真实时钟；箭头**不是**已验证的谱系；候选调控关系**不是**因果证据。',
        '- 不同方法的损失、似然与时间尺度不可直接排名；本程序不给出“最佳模型”。',
        '- 正式评测需要按供体/样本预先拆分，特征选择、先验网络、调参与评价标签之间不能泄漏。',
        '- 细胞不是独立生物学重复；本程序不做组间显著性检验。',
        '- 缺少外部时间点、谱系追踪、独立扰动或跨供体复现时，方向/命运/调控结论都应保持为“未知”。',
    ]))
    bio.append('## 7. 官方方法来源\n\n核查日期：' + CHECKED + '。运行版本以 B 报告与环境快照为准。\n\n' +
               '\n'.join(f'- [{m.name}]({m.source})' for m in METHODS
                         if m.source and (m.key == method or m.key == 'qc'
                                          or (method == 'auto' and m.key in
                                              ('scvelo_stochastic', 'cellrank')))))

    # =========================== B：技术与复现报告
    prov = provenance or {}
    tech.append('# B 技术与复现报告')
    tech.append(f'任务状态：**{status}**｜方法：`{method}`｜任务目录：`{folder.resolve()}`'
                + ('｜**模拟数据**' if simulated else ''))
    if error:
        tech.append('## 0. 失败摘要\n\n```text\n' + str(error) + '\n```\n\n'
                    '失败任务同样保留了输入、配置、环境、日志与报告，便于定位问题。')

    tech.append('## 1. 运行环境\n\n' + '\n'.join([
        f'- Python：{prov.get("python", "未知")}',
        f'- 解释器：`{prov.get("executable", "未知")}`',
        f'- 平台：{prov.get("platform", "未知")}',
        f'- 计算设备：{prov.get("device", "cpu")}'
        + ('（使用 CUDA）' if prov.get('device') == 'cuda' else '（CPU，未调用 CUDA）'),
        f'- 线程限制：{json.dumps(prov.get("thread_limits", {}), ensure_ascii=False)}',
        '- 完整依赖版本：见同目录 `environment.json`（本次解释器真实安装的所有包）。',
    ]))

    if fmt:
        mat = fmt['matrices'].get('X', {})
        tech.append('## 2. 数据格式检查（observed）\n\n' + '\n'.join([
            f'- 文件：`{fmt["file"]["name"]}`（{fmt["file"]["suffix"]}，{fmt["file"]["size_text"]}）',
            f'- 维度：{fmt["shape"]["n_cells"]} 细胞 × {fmt["shape"]["n_genes"]} 基因',
            f'- X：dtype={mat.get("dtype")}，稀疏={mat.get("sparse")}，非零比例={mat.get("nonzero_pct", 0):.2f}%，'
            f'整数外观={mat.get("integer_like")}，max={mat.get("max")}',
            f'- 数据层：{", ".join(fmt["layers_present"]) or "无"}',
            f'- obs 列：{", ".join(o["name"] for o in fmt["obs"]) or "无"}',
            f'- var：{fmt["var"]["id_kind"]}，唯一={fmt["var"]["unique"]}，'
            f'线粒体命中 {fmt["var"]["mt_matched"]} 个',
            f'- 剪接层：{json.dumps(fmt["splice"], ensure_ascii=False) if fmt["splice"] else "无"}',
            f'- 附加对象：{json.dumps(fmt["extras"], ensure_ascii=False) if fmt["extras"] else "无"}',
        ]))
        if fmt.get('warnings'):
            tech.append('### 2.1 格式体检发现的风险\n\n' + '\n'.join('- ' + w for w in fmt['warnings']))
    else:
        tech.append('## 2. 数据格式检查\n\n本次未生成格式体检记录（可能在读取阶段就失败）。')

    tech.append('## 3. 输入指纹与预处理指纹\n\n' + '\n'.join([
        f'- 输入 SHA-256：`{prov.get("input_sha256", "未知")}`',
        f'- 细胞 ID 哈希：`{(result or {}).get("cells_hash") or facts.get("cells_hash", "未知")}`',
        f'- 基因 ID 哈希：`{(result or {}).get("genes_hash") or facts.get("genes_hash", "未知")}`',
        f'- 预处理指纹：`{(result or {}).get("preprocessing_hash") or "本次方法未产生"}`',
        f'- 先验 GRN SHA-256：`{prov.get("prior_grn_sha256") or "未使用"}`',
        f'- 代码指纹：`code_sha256` 记录在 provenance.json（共 {len(prov.get("code_sha256", {}))} 个文件）。',
    ]))

    tech.append('## 4. 超参数快照\n\n```json\n' +
                json.dumps({k: v for k, v in config.items() if k != 'grn_path'}, ensure_ascii=False, indent=2) +
                '\n```\n\n完整配置（含 GRN 路径）：`config.json`。')

    calls_path = folder / 'calls.json'
    if calls_path.exists():
        try:
            calls = json.loads(calls_path.read_text(encoding='utf-8'))
            rows = ['| # | 真实调用 | 关键参数 |', '|---|---|---|']
            for i, call in enumerate(calls[:40], 1):
                args = json.dumps(call.get('arguments', {}), ensure_ascii=False)
                if len(args) > 160:
                    args = args[:157] + '...'
                rows.append(f'| {i} | `{call.get("function")}` | `{args}` |')
            tech.append('## 5. 真实调用序列\n\n共记录 ' + str(len(calls)) + ' 次函数调用'
                        '（含安装版本签名，见 `calls.json`）。\n\n' + '\n'.join(rows))
        except Exception:
            tech.append('## 5. 真实调用序列\n\n`calls.json` 存在但无法解析。')
    else:
        tech.append('## 5. 真实调用序列\n\n本次没有记录到模型调用'
                    '（可能是质控/演示方法，或在读取阶段就已失败）。')

    tech.append('## 6. 耗时、状态与随机性\n\n' + '\n'.join([
        f'- 开始：{prov.get("started_at", "未知")}',
        f'- 结束：{prov.get("finished_at", "未知")}',
        f'- 耗时：{prov.get("elapsed_seconds", "未知")} 秒（含解释器启动与图形导出）',
        f'- 状态：{prov.get("status", status)}',
        f'- 随机种子：{prov.get("seed", "未知")}（同时用于 PCA/UMAP/分群）',
        '- 固定种子不保证逐字节相同：并行调度、线性代数后端与平台差异都会带来数值差异。',
    ]))

    tech.append('## 7. 复现方式\n\n'
                f'1. 切到本次保存的源码目录：`{prov.get("reproduce_cwd", "未知")}`\n'
                '2. 用同一解释器与 `environment.json` 中的版本执行：\n\n```text\n'
                + prov.get('reproduce_command', '未知') + '\n```\n'
                '3. 若报告状态为 failed，先看 `run.log`；修复后建议复现到新目录，不要覆盖旧结果。'
                '\n4. `source/` 保存了本次运行时的完整源码副本，可用于对照或重放。')

    files = []
    for name in ('config.json', 'environment.json', 'validation.json', 'format.json',
                 'eligibility.json', 'qc.json', 'result.json', 'gene_table.json', 'provenance.json',
                 'calls.json', 'run.log', 'analysis.h5ad', 'velocity.npz', 'velocity_uncertainty.npz'):
        target = folder / name
        if target.exists():
            files.append(f'- `{name}`（{target.stat().st_size / 1024:.0f} KB）')
    figures = sorted((folder / 'figures').glob('*')) if (folder / 'figures').exists() else []
    if figures:
        files.append('- `figures/`：' + '、'.join(p.name for p in figures))
    tech.append('## 8. 结果文件清单\n\n' + ('\n'.join(files) if files else '- 本次没有生成文件。'))

    tech.append('## 9. 保密、联网与清理\n\n' + '\n'.join([
        '- 数据、图、报告、日志、样本 ID 与绝对路径**都只在本机**；本次分析没有云端调用、不需要 API 密钥。',
        '- 安装/更新依赖、点击官方文档链接需要联网；界面上的外部入口都会标注“需联网”。',
        '- 报告与日志可能包含基因/细胞标识与本地路径，分享前请自行检查。',
        f'- 清理本次结果：删除 `{folder.resolve()}` 即可；原始导入文件不会被修改。',
    ]))

    figure_files = (sorted((folder / 'figures').glob('*.svg')) +
                    sorted((folder / 'figures').glob('*.png'))) if (folder / 'figures').exists() else []
    if figure_files:
        bio.append('### 4.12 图注\n\n' + '\n'.join(
            '- ' + _figure_caption(item.name, result, config, index)
            for index, item in enumerate(figure_files, start=1)))

    (folder / 'A_生物学分析报告.md').write_text('\n\n'.join(bio) + '\n', encoding='utf-8')
    (folder / 'B_技术与复现报告.md').write_text('\n\n'.join(tech) + '\n', encoding='utf-8')
    for letter, title, parts in [('A', '生物学分析报告', bio), ('B', '技术与复现报告', tech)]:
        images = ''
        if letter == 'A':
            figures = (sorted((folder / 'figures').glob('*.svg')) +
                       sorted((folder / 'figures').glob('*.png'))) if (folder / 'figures').exists() else []
            images = ''.join(
                f'<figure id="fig-{index}"><img alt="{p.stem}" src="figures/{p.name}">'
                f'<figcaption>{_figure_caption(p.name, result, config, index)}</figcaption></figure>'
                for index, p in enumerate(figures, start=1))
        markup = ('<!doctype html><meta charset="utf-8"><title>' + title + '</title>'
                  '<style>body{max-width:980px;margin:48px auto;padding:0 24px;color:#20334b;'
                  'font:16px/1.7 system-ui}h1{font-size:1.6rem}h2{font-size:1.25rem;margin-top:1.8em}'
                  'h3{font-size:1.02rem;margin-top:1.4em}table{border-collapse:collapse;width:100%;'
                  'font-size:.9rem}th,td{border:1px solid #dde5ee;padding:4px 8px;text-align:left}'
                  'code{background:#f2f5f8;padding:1px 4px;border-radius:4px}'
                  'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f8fa;'
                  'padding:12px;border-radius:8px}blockquote{margin:1em 0;padding:6px 14px;'
                  'border-left:3px solid #d7a24a;background:#fff8ec}'
                  'figure{margin:18px 0}img{max-width:100%;border:1px solid #e1e8f0;'
                  'border-radius:8px}figcaption{color:#5b7186;font-size:.85rem}'
                  '@media print{body{margin:0}img{break-inside:avoid}}</style>'
                  '<h1>' + title + '</h1>' + images + _render_markdown('\n\n'.join(parts)))
        (folder / (letter + '_report.html')).write_text(markup, encoding='utf-8')
