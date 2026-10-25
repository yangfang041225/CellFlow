"""按数据事实给建议：哪些方法值得先做、大概要多少内存和时间。

这里的输出全部是**基于规则的提示**，不是自动选方法：
- 它解释“为什么建议这一步”；
- 它不会因为某个方法“看起来最好”就替用户决定；
- 内存/耗时是量级估算，标明了假设，不承诺精确。
"""
from .registry import INDEX, FAMILIES, METHODS

# 这台电脑的可用预算（保守值，留给系统和界面）
RAM_TOTAL_GB = 16.0
RAM_USABLE_GB = 10.0

NAME = {m.key: m.name for m in METHODS}
FAMILY = {m.key: FAMILIES[m.family]['label'] for m in METHODS}


def _fmt_gb(gb):
    return f'约 {gb:.1f} GB' if gb >= 1 else f'约 {gb * 1024:.0f} MB'


def budget(facts, keys=()):
    """粗算内存与耗时量级。假设写在 assumptions 里，供用户判断是否可信。"""
    n = int(facts.get('n_cells', 0) or 0)
    g = int(facts.get('n_genes', 0) or 0)
    density = 0.10
    mats = facts.get('matrices', {}) or {}
    if mats.get('X', {}).get('nonzero') and n and g:
        density = max(mats['X']['nonzero'] / (n * g), 1e-4)
    nnz = n * g * density
    layers = len(facts.get('matrices', {}))
    n_top = 2000
    # 稀疏矩阵副本 + PCA/邻域 + 模型中间量
    mem = nnz * 16 * (3 + layers) + n * min(n_top, max(g, 1)) * 8 * 3
    gb = mem / (1024 ** 3)
    if 'scvelo_dynamical' in keys or 'cellrank' in keys:
        gb *= 1.8
    level = '低' if gb < 1.5 else ('中' if gb < 5 else '高')
    tlevel = '低' if n < 2000 else ('中' if n < 20000 else '高')
    if any(k in keys for k in ('scvelo_dynamical', 'regvelo')):
        tlevel = '高' if tlevel == '中' else tlevel
    advice = []
    if gb > RAM_USABLE_GB:
        advice.append(f'估算内存 {_fmt_gb(gb)} 已接近本机可用上限（{RAM_USABLE_GB:.0f} GB），建议先做随机子集。')
    if n > 20000:
        advice.append('细胞数超过 2 万时，velocity 与 UMAP 在 16 GB 内存上会明显变慢，建议先抽样或先只做 QC。')
    if n > 50000:
        advice.append('细胞数超过 5 万时，本原型很可能不适合直接全量运行。')
    advice.append('计算子进程默认限制为单线程，避免 16 GB 机器被同时占满；换来的是更慢但更稳。')
    return dict(
        cells=n, genes=g,
        density=density,
        memory_gb=round(gb, 2), memory_text=_fmt_gb(gb), memory_level=level,
        time_level=tlevel,
        time_text={'低': '数十秒级', '中': '几分钟级', '高': '十几分钟到更久'}[tlevel],
        advice=advice,
        assumptions=['按稀疏矩阵副本、2000 个高变基因、单线程估算',
                     '不含解释器启动与首次导入库的时间',
                     '实际耗时受数据稀疏度、基因数、邻域与收敛情况影响'],
    )


def recommend(facts, config, installed_keys=None):
    """返回建议流程。level: 推荐 / 可尝试 / 不建议。"""
    installed_keys = set(installed_keys if installed_keys is not None else
                         [m.key for m in METHODS if m.implemented])
    has_su = bool(facts.get('velocity_counts'))
    raw_x = bool(facts.get('raw_x'))
    n = int(facts.get('n_cells', 0) or 0)
    root = bool(facts.get('root_cell_present') and str(config.get('root_reason', '')).strip())
    groups = bool(config.get('group_key'))
    synthetic = bool(facts.get('synthetic'))
    gaps, steps, notes = [], [], []

    def add(key, why, level='推荐'):
        if key in INDEX and key in installed_keys:
            steps.append(dict(key=key, title=NAME[key], family=FAMILY[key], why=why, level=level))

    if synthetic:
        add('demo', '先用模拟数据把“提交 → 排队 → 结果 → 报告”整条流程走一遍。')
    add('auto', '一个按钮跑完推荐流程：质控 → 自动分群 → PAGA →（有剪接层）速度 →（有速度/起点）命运，'
                '最后给一份汇总报告。')
    add('qc', '任何数据都先看质量：总表达、检测基因数、线粒体比例和整体结构。')

    kind = facts.get('x_kind') or ('counts' if raw_x else 'log')
    if kind == 'counts':
        notes.append('X 是整数计数：程序按原始计数做归一化与对数化。')
    elif kind == 'log':
        notes.append('X 看起来已经取过对数：程序不会再归一化或取对数，直接用现有数值做特征选择与降维。')
    elif kind == 'scaled':
        notes.append('X 像是已缩放到人为总量：程序只会取对数，不会再次缩放。')
    if kind == 'empty':
        gaps.append('X 看起来是全零或无法判断：请先确认矩阵内容与基因/细胞方向。')
    else:
        add('leiden', '自动分群不需要你预先提供标签，是后续 PAGA 和终末状态识别的常用输入。')
        if groups:
            add('paga', '你已有分组列，可以直接看群与群之间的连接强度（无向）。')
        else:
            add('paga', '未指定分组时会先自动分群，再计算群间连接强度。', '可尝试')

    if root:
        add('dpt', '你提供了起点和依据，可以计算扩散拟时序（相对顺序，不是真实时间）。')
    else:
        gaps.append('DPT 需要起始细胞 ID 和选择它的生物学依据；没有依据时程序不会随便挑一个起点。'
                    '（一键分析在没有起点时不会推断方向与命运。）')

    if has_su:
        add('scvelo_stochastic', '先跑 stochastic 作为速度基线，代价低、便于先看剪接信号是否够。')
        if n <= 20000:
            add('scvelo_dynamical', '在 stochastic 结果可解释的前提下，再试 dynamical 拿到潜在时间与每基因动力学参数。',
                '可尝试')
        else:
            gaps.append(f'当前 {n} 个细胞，dynamical 在 16 GB 内存上代价较高，建议先做子集。')
        if root:
            add('cellrank', '有起点 → 有拟时序 → 讨论终末状态与命运概率（下游，与上游速度不同层）。', '可尝试')
        else:
            notes.append('有速度时不需要起点：一键分析会用速度图作核估计命运概率；'
                         '若想用 DPT 拟时序做核，再补一个起始细胞 ID。')
    else:
        gaps.append('没有确认的 spliced/unspliced 原始计数层：所有 velocity 方法'
                    '（scVelo、veloVI、DeepVelo、UniTVelo、VeloAE、RegVelo、MultiVelo）都不满足前提。')
    if not groups and kind != 'empty':
        gaps.append('未指定细胞分组：PAGA、以及需要群标签的诊断会改用自动分群结果。')

    notes += [
        '这些建议按“先质控、再轨迹、再速度、最后命运”的顺序排列，回答的是不同问题，不需要每样都跑。',
        '不同方法的损失、似然和潜在时间尺度不能直接排名；本程序不给出“最佳模型”。',
    ]
    if facts.get('synthetic'):
        notes.append('当前是模拟数据：结果只能说明软件流程跑通，不能当作生物学发现。')
    if n > 20000:
        notes.append('细胞数较多时，建议先用随机子集把参数调通，再回到全量数据。')

    head = ('模拟数据：先跑通流程' if synthetic else
            ('有剪接层：可以讨论速度方向' if has_su else '只有表达矩阵：可以做质控、分群与轨迹，但不能做速度'))
    return dict(headline=head, steps=steps, gaps=gaps, notes=notes,
                budget=budget(facts, [s['key'] for s in steps]))


def config_hints(facts, report=None):
    """给出保守参数建议，并说明每个参数影响什么。"""
    n = int(facts.get('n_cells', 0) or 0)
    g = int(facts.get('n_genes', 0) or 0)
    hints = []
    top = min(2000, max(200, g))
    hints.append(dict(key='n_top_genes', value=top,
                      why='高变基因数量：越大保留越多结构，也越慢；基因少时会被自动限制。'))
    nb = 30 if n >= 300 else 15
    hints.append(dict(key='n_neighbors', value=nb,
                      why='邻居数：越大越平滑、越容易把不同群连起来；小数据用 15 更敏感。'))
    pcs = 30 if min(n, g) >= 500 else 20
    hints.append(dict(key='n_pcs', value=pcs,
                      why='主成分数：决定邻域使用多少维度；不会超过基因数或细胞数。'))
    hints.append(dict(key='min_shared_counts', value=20,
                      why='剪接共同计数阈值：过滤低信号基因；阈值过高会剩不下基因，程序会明确报错。'))
    hints.append(dict(key='max_iter', value=10,
                      why='scVelo dynamical 的优化迭代上限；调大更慢，但不保证收敛。'))
    hints.append(dict(key='leiden_resolution', value=1.0,
                      why='分群分辨率：调大得到更多、更细的群。'))
    if report:
        sug = report.get('suggestions', {})
        if sug.get('mt_prefix'):
            hints.append(dict(key='mt_prefix', value=sug['mt_prefix'],
                              why='按现有基因名推断的线粒体前缀，请自行确认后再用。'))
    return hints
