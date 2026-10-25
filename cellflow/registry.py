"""方法注册表：科学前提 + 工程状态。

两条红线：
1. 条件不满足时**明确说明缺什么**，绝不自动换成别的方法。
2. `implemented=False`（尚未接入）的条目永远不可运行，即使误装了同名包。
"""
from dataclasses import dataclass, asdict

from .data import AUTO_GROUP

CHECKED = '2026-10-24'

# ---------------------------------------------------------------- 用途分层
# 用户最需要先分清的是“这个问题归哪一类”，所以用途是一级分类。
FAMILIES = {
    'auto':       dict(label='一键分析（推荐）', order=0, short='一个矩阵进去，一份报告出来',
                       question='我不想一个个挑方法：能不能一次跑完体检、分群、轨迹，有剪接层就加速度和命运，最后给我一份报告？'),
    'demo':       dict(label='演示与教学', order=1, short='先熟悉软件流程',
                       question='还没有数据，想先看懂界面、图形和报告长什么样。'),
    'qc':         dict(label='质控与结构', order=2, short='数据能不能用、长什么样',
                       question='数据质量如何？哪些细胞/基因异常？整体表达结构怎样？'),
    'trajectory': dict(label='轨迹与分群', order=3, short='细胞处在什么相对位置',
                       question='细胞群之间怎么连接？从一个有依据的起点看，细胞按什么相对顺序排列？'),
    'velocity':   dict(label='速度推断', order=4, short='表达正在往哪个方向变',
                       question='剪接数据是否支持细胞状态的转变方向与相对动力学？'),
    'regulatory': dict(label='调控动力学', order=5, short='谁在调控这个变化',
                       question='在已有调控先验或多组学条件下，能否探索调控关系与动力学？'),
    'fate':       dict(label='下游命运分析', order=6, short='细胞最终可能去哪里',
                       question='在上游动力学/拟时序已成立的前提下，细胞走向各终末状态的概率如何？'),
}

# 输入前提：键 -> (中文说明, 数据里通常长什么样)
NEEDS = {
    'synthetic':    ('内置模拟数据', '程序自带的 240 细胞 × 120 虚构基因示例'),
    'raw_X':        ('X 为未归一化的原始计数', 'h5ad 的 X 矩阵，整数计数；已 log 化的矩阵不满足'),
    'raw_SU':       ('spliced / unspliced 原始计数层', 'h5ad 的 layers["spliced"] 与 layers["unspliced"]'),
    'groups':       ('细胞分组标签', 'obs 里的一列群标签，或由本程序“自动分群”产生'),
    'root':         ('起始细胞 + 生物学依据', '一个细胞 ID，以及你认为它是起点的实验/文献理由'),
    'prior_GRN':    ('有来源说明的先验调控网络', 'TF×target 的 0/1 CSV，需说明物种、方向、基因 ID 与版本'),
    'pseudotime':   ('可用的拟时序', '由 DPT 等方法产生，依赖指定起点'),
    'matched_ATAC': ('匹配的 ATAC 数据', '同一批细胞的染色质可及性矩阵 + 峰到基因映射'),
    'protocol':     ('明确实验协议', '常规剪接数据，或真正的代谢标记层与实验时间'),
}

STATUS_LABELS = {
    'ready':        ('可运行', 'ok'),
    'experimental': ('实验接入', 'warn'),
    'planned':      ('尚未接入', 'muted'),
}


@dataclass(frozen=True)
class Method:
    key: str
    name: str            # 中文名（界面主标题）
    name_en: str         # 英文/包名（便于查文献）
    family: str
    needs: tuple
    packages: tuple      # 运行所需的 Python 包（用于依赖检查）
    implemented: bool    # 是否已有真实适配器代码
    note: str            # 一句话科学警示（界面会显示）
    source: str          # 官方文档/代码
    runtime_env: str = '.venv'      # 计划使用的解释器环境目录
    outputs: tuple = ()             # 本程序**实际**能产出的结果
    optional: tuple = ()            # 可选输入（有则更好，无则不影响准入）
    gpu_note: str = 'CPU 可运行'
    data_limit: str = ''
    needs_resource: str = 'medium'  # light / medium / heavy（用于 16GB 内存提示）
    maturity: str = 'stable'        # stable / experimental：experimental 永不显示为“可运行”
    input_protocol: str = ''        # 输入契约（层/尺度/前提），逐条写清“喂给模型的是什么”
    output_protocol: str = ''       # 实际写出的产物（必须与适配器一致，未验证不得写满）


METHODS = [
    Method('auto', '一键分析（推荐流程）', 'CellFlow auto pipeline', 'auto',
           (), ('scanpy', 'anndata'), True,
           '一次跑完：质控 → 自动分群 → PAGA →（有剪接层）速度 →（有速度或起点）命运概率。'
           '每一步的结果与限制都会分开写清，不会把推断当成事实。',
           'https://scanpy.scverse.org/en/stable/',
           outputs=('数据质量指标与质量图', '自动分群 + UMAP 图', '群间连接（PAGA）',
                    '有剪接层时：速度矩阵、箭头图、速度伪时序',
                    '有速度时：终末状态与命运概率；给了起点时另有 DPT 拟时序',
                    '一份汇总的 A 生物学报告 + B 技术复现报告'),
           optional=('起始细胞 + 依据（给了就用 DPT + 拟时序核算命运）', '细胞分组列'),
           gpu_note='纯 CPU；小数据约 20–40 秒，大数据会明显变慢',
           data_limit='需要 X 为原始计数；velocity 需要 spliced/unspliced 两层',
           needs_resource='medium'),

    Method('qc', '数据质控与 PCA', 'Scanpy QC / PCA', 'qc', (), ('scanpy', 'anndata'), True,
           '只做描述性质控，不推断细胞命运；不自动按阈值删细胞。',
           'https://scanpy.scverse.org/en/stable/api/plotting.html',
           outputs=('每细胞总表达 / 检测基因数 / 线粒体比例', 'PCA 二维投影',
                    '质量检查图与剪接相图（有剪接层时）', '质控风险清单'),
           gpu_note='纯 CPU，几秒级', data_limit='本机：矩阵合计约 3 GB 内存',
           needs_resource='light'),

    Method('demo', '模拟教学示例', 'Built-in synthetic demo', 'demo', ('synthetic',), (), True,
           '展示已知模拟时间，不拟合任何速度模型；结果永远标记为模拟。',
           '', outputs=('一套示例图、示例指标与两份示例报告',),
           gpu_note='纯 CPU，几秒级', data_limit='内置 240 细胞 × 120 虚构基因',
           needs_resource='light'),

    Method('leiden', '自动分群（Leiden + UMAP）', 'Scanpy Leiden (igraph)', 'trajectory',
           (), ('scanpy', 'anndata'), True,
           '分群结果取决于分辨率和邻域参数，不等于“真实细胞类型”；群内高表达基因只是描述性线索。',
           'https://scanpy.scverse.org/en/stable/generated/scanpy.tl.leiden.html',
           outputs=('每个细胞的群编号', 'UMAP 二维图（按群着色）', '群大小表',
                    '每群相对高表达基因（描述性）'),
           gpu_note='纯 CPU；UMAP 在万级细胞上约数十秒', data_limit='细胞越多 UMAP 越慢；本机矩阵合计约 3 GB 内存',
           needs_resource='medium'),

    Method('dpt', '扩散拟时序 DPT', 'Scanpy Diffusion Pseudotime', 'trajectory',
           ('root',), ('scanpy', 'anndata'), True,
           '必须指定起点并说明依据；拟时序是相对顺序，不是真实小时数，也不是 RNA velocity。',
           'https://scanpy.scverse.org/en/stable/generated/scanpy.tl.dpt.html',
           outputs=('每个细胞的 DPT 拟时序（含可达性诊断）', '按拟时序着色的图',
                    '沿拟时序的探索性基因趋势'),
           optional=('细胞分组（用于给群排序）',),
           data_limit='不连通区域可能没有可解释的拟时序', needs_resource='medium'),

    Method('paga', 'PAGA 群间连接', 'Scanpy PAGA', 'trajectory',
           ('groups',), ('scanpy', 'anndata', 'igraph'), True,
           '输出的是群间**无向**连通强度，不能独立说明“谁分化成谁”；方向需速度或拟时序支持。',
           'https://scanpy.scverse.org/en/stable/generated/scanpy.tl.paga.html',
           outputs=('群间连通性矩阵与热图', 'UMAP/PCA 上的群连接图',
                    '若同时提供起点，可给出群平均拟时序作为方向线索'),
           optional=('起始细胞（给出方向线索）',),
           data_limit='分组需完整、2–30 群', needs_resource='medium'),

    Method('scvelo_deterministic', 'scVelo · deterministic（稳态）', 'scVelo steady-state', 'velocity',
           ('raw_SU',), ('scvelo', 'scanpy', 'anndata'), True,
           '一阶稳态模型（La Manno 2018）；假设未剪接/已剪接比例处于平衡，对瞬态系统容易失真。'
           '这是 scVelo 官方 mode 之一，不是自写算法。',
           'https://scvelo.readthedocs.io/en/stable/VelocityBasics.html',
           outputs=('基因空间速度矩阵', '二维速度箭头投影', '速度伪时序',
                    '速度长度/置信度诊断', '候选高速度基因'),
           optional=('细胞分组', '起始细胞'),
           gpu_note='CPU 可运行（scVelo 官方实现为 CPU）',
           data_limit='需足量剪接信号；比 stochastic/dynamical 更快但假设更强',
           needs_resource='light'),

    Method('scvelo_stochastic', 'scVelo · stochastic', 'scVelo stochastic', 'velocity',
           ('raw_SU',), ('scvelo', 'scanpy', 'anndata'), True,
           '二阶矩方法，依赖稳态假设；箭头与内部一致性指标不能证明真实分化方向。',
           'https://scvelo.readthedocs.io/en/stable/VelocityBasics.html',
           outputs=('基因空间速度矩阵', '二维速度箭头投影', '速度伪时序',
                    '速度长度/置信度诊断', '候选高速度基因'),
           optional=('细胞分组', '起始细胞'),
           gpu_note='CPU 可运行，无需 CUDA', data_limit='需足量剪接信号；过滤后基因不足会明确报错',
           needs_resource='medium'),

    Method('scvelo_dynamical', 'scVelo · dynamical', 'scVelo dynamical', 'velocity',
           ('raw_SU',), ('scvelo', 'scanpy', 'anndata'), True,
           '逐基因拟合动力学；更高似然只说明在该模型内拟合更好，高似然基因不是已验证的因果驱动基因。',
           'https://scvelo.readthedocs.io/en/stable/DynamicalModeling.html',
           outputs=('速度矩阵', '潜在时间 latent time', '每基因转录/剪接/降解速率与似然',
                    '速度图与拟合诊断'),
           optional=('细胞分组',),
           gpu_note='CPU 可运行，比 stochastic 慢', data_limit='动态状态覆盖不足时拟合失败率高',
           needs_resource='heavy'),

    Method('cellrank', 'CellRank 2 · 命运概率', 'CellRank 2 (PseudotimeKernel)', 'fate',
           ('root',), ('cellrank', 'scanpy', 'anndata'), True,
           '命运概率是**模型推断**，依赖上游拟时序与终末状态设定；终末状态需生物学确认，概率高不等于已验证谱系。',
           'https://cellrank.readthedocs.io/en/stable/',
           outputs=('终末状态识别结果', '每个细胞到各终末状态的命运概率',
                    '按命运概率着色的图', '驱动命运概率的候选基因（描述性）'),
           optional=('细胞分组（辅助识别终末状态）',),
           gpu_note='纯 CPU；比 DPT 慢一些', data_limit='需要可用的 DPT 拟时序（本程序内部先生成）',
           needs_resource='medium'),

    Method('regvelo', 'RegVelo · 带先验的调控动力学', 'RegVelo 0.4.2', 'regulatory',
           ('raw_SU', 'prior_GRN'), ('regvelo', 'torch', 'scvi-tools'), True,
           '实验接入：GRN 来源/物种/方向/基因映射需人工核查；推断边不是验证过的因果关系。',
           'https://regvelo.readthedocs.io/en/latest/getting_started.html',
           runtime_env='.venv-regvelo',
           outputs=('速度与模型内部相对时间', '基因空间速度矩阵（velocity.npz）',
                    '候选基因表（描述性）', '结果对象与二维展示坐标'),
           optional=('细胞分组',),
           gpu_note='官方实现部分函数写死 CUDA；本机 CPU 不调用该函数，因此**不输出调控边**',
           data_limit='限定 0.4.2；预处理后 10–3000 基因；本机未安装环境、未真实训练，结果不可信',
           needs_resource='heavy',
           maturity='experimental',
           input_protocol='原始 S/U 计数 → filter_genes → normalize_per_cell(1e4) → log1p(X) → '
                          'HVG(n_top_genes) → moments(Ms/Mu)；训练输入 Ms/Mu；先验 GRN 为 TF×target 0/1',
           output_protocol='velocity.npz（速度）+ 相对时间 + 候选基因表；**不写调控边**'
                           '（0.4.2 的 inferred_grn 写死 cuda:0）；设备取实际解析结果，不再写死 cpu'),

    Method('velovi', 'veloVI · 变分速度与不确定性', 'scvi-tools VELOVI', 'velocity',
           ('raw_SU',), ('scvi-tools',), True,
           '变分推断给的是近似后验；它和 scVelo 的方向差异应看作模型不确定性，而不是“谁对谁错”。'
           '训练损失不能与其他模型的似然/损失排名。',
           'https://docs.scvi-tools.org/en/stable/user_guide/models/velovi.html',
           runtime_env='.venv-velovi',
           outputs=('基因空间速度矩阵', '速度后验标准差（不确定性）', '潜在时间',
                    '二维速度箭头投影', '速度一致性诊断', '候选高速度基因', '训练好的模型检查点'),
           optional=('细胞分组',),
           gpu_note='CPU 可训练（慢）；有 NVIDIA 时可切 GPU，本程序按设备设置自动选择',
           data_limit='输入必须是原始 S/U 计数（程序自己归一化/平滑）；训练轮数需足够才收敛',
           needs_resource='heavy',
           input_protocol='原始 S/U 计数 → filter_genes → normalize_per_cell(1e4, 含 S/U) → log1p(X) → '
                          'HVG → moments → 用 layers["Ms"]/["Mu"] 训练'
                          '（scvi-tools 1.2.0 的 is_count_data=False，官方教程即用 Ms/Mu）',
           output_protocol='velocity.npz + velocity_uncertainty.npz + latent_time.npz + velovi_model/ '
                           '+ velovi_protocol.json（输入层与训练审计）'),

    Method('deepvelo', 'DeepVelo · 细胞相关动力学', 'DeepVelo (bowang-lab)', 'velocity',
           ('raw_SU',), ('deepvelo', 'dgl'), False,
           '官方实现依赖较旧 Python 与 DGL，需要独立环境；同名其他项目不是同一实现。',
           'https://github.com/bowang-lab/DeepVelo',
           runtime_env='.venv-deepvelo',
           gpu_note='官方默认 GPU；CPU 训练慢且版本受限', needs_resource='heavy'),

    Method('unitvelo', 'UniTVelo · 共同时间动力学', 'UniTVelo', 'velocity',
           ('raw_SU',), ('unitvelo', 'tensorflow'), False,
           '共同时间假设需要结合研究系统核查；TensorFlow 环境需独立安装。',
           'https://unitvelo.readthedocs.io/en/latest/install.html',
           runtime_env='.venv-unitvelo',
           gpu_note='CPU 可运行；TensorFlow 栈较大', needs_resource='heavy'),

    Method('veloae', 'VeloAE · 低维速度', 'VeloAE (veloproj)', 'velocity',
           ('raw_SU',), ('veloproj', 'torch-geometric'), False,
           '低维速度与原始基因空间速度不可逐元素比较，需要额外映射。',
           'https://github.com/qiaochen/VeloAE',
           runtime_env='.venv-veloae',
           gpu_note='CPU 可训练；需要 PyTorch Geometric', needs_resource='heavy'),

    Method('dynamo', 'dynamo · 动力学与向量场', 'dynamo-release', 'regulatory',
           ('protocol',), ('dynamo-release',), False,
           '常规剪接分支与代谢标记分支必须分开；普通 scRNA 不能凭空生成标记层或实验时间。',
           'https://github.com/aristoteleo/dynamo-release',
           runtime_env='.venv-dynamo',
           gpu_note='CPU 可运行；完整安装体积较大', needs_resource='heavy'),

    Method('multivelo', 'MultiVelo · 多组学动力学', 'MultiVelo', 'regulatory',
           ('raw_SU', 'matched_ATAC'), ('multivelo',), False,
           '必须同时具备匹配的 RNA 与 ATAC 数据；只有 scRNA 时条件不成立。',
           'https://github.com/welch-lab/MultiVelo',
           runtime_env='.venv-multivelo',
           gpu_note='CPU 可运行但数据准备复杂', needs_resource='heavy'),
]

INDEX = {m.key: m for m in METHODS}

# 主流 RNA velocity 模型的“切换表”：界面上一张下拉，背后对到具体方法。
# 说明全部来自上游官方文档/源码（scVelo 的 mode 参数就是这三种；veloVI 见 scvi-tools）。
VELOCITY_MODELS = {
    'deterministic': dict(
        label='scVelo · deterministic（稳态模型）', method='scvelo_deterministic',
        speed='最快', gpu='CPU（官方实现）',
        assumption='假设未剪接/已剪接比例处于稳态（La Manno 2018）',
        when='数据接近稳态、想快速看方向；对瞬态过程容易失真'),
    'stochastic': dict(
        label='scVelo · stochastic（二阶矩，推荐基线）', method='scvelo_stochastic',
        speed='中等', gpu='CPU（官方实现）',
        assumption='用一阶矩 + 二阶矩估计，比稳态模型稳健（Bergen 2020）',
        when='常规 scRNA-seq 有 spliced/unspliced 时的默认选择'),
    'dynamical': dict(
        label='scVelo · dynamical（完整动力学）', method='scvelo_dynamical',
        speed='最慢', gpu='CPU（官方实现）',
        assumption='逐基因拟合转录/剪接/降解 ODE（Bergen 2020）',
        when='需要潜在时间、基因动力学参数，且数据覆盖足够'),
    'velovi': dict(
        label='veloVI（变分推断，给不确定性）', method='velovi',
        speed='慢（建议 GPU）', gpu='GPU 建议',
        assumption='变分自编码 + 剪接动力学（scvi-tools 实现）',
        when='想要速度的不确定性区间；需要单独装 scvi-tools + PyTorch'),
}
DEFAULT_VELOCITY_MODEL = 'stochastic'


def installed(packages):
    out = {}
    for package in packages:
        try:
            from importlib.metadata import version
            out[package] = version(package)
        except Exception:
            out[package] = None
    return out


def env_python(method_or_key, root=None):
    """返回某个方法隔离环境的解释器路径；不存在时返回 None。

    `.venv` 就是主环境；其它（.venv-velovi / .venv-regvelo …）是独立环境。
    这样界面与队列都用同一套判断，不再为每个模型写一次 if。
    """
    import os
    from pathlib import Path
    method = INDEX[method_or_key] if isinstance(method_or_key, str) else method_or_key
    base = Path(root or Path(__file__).resolve().parents[1]) / method.runtime_env
    if not base.exists():
        return None
    candidate = base / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    return candidate if candidate.exists() else None


def isolated_ready(method_or_key):
    """隔离环境是否已就绪（存在解释器，且有对应的依赖锁文件）。"""
    method = INDEX[method_or_key] if isinstance(method_or_key, str) else method_or_key
    if method.runtime_env in ('.venv', ''):
        return False
    if env_python(method) is None:
        return False
    from pathlib import Path
    lock = Path(__file__).resolve().parents[1] / ('environment-' + method.runtime_env.lstrip('.') + '.lock.txt')
    return lock.exists()


def status_of(method, versions=None):
    """三元状态：可运行 / 实验接入 / 尚未接入（按本机实际安装情况判定）。

    F20：`maturity == 'experimental'` 的方法**永远**返回 experimental——
    因为“装了包”不等于“在本机真实训练验证过”（RegVelo 就是这种情况）。
    """
    if not method.implemented:
        return 'planned'
    if getattr(method, 'maturity', 'stable') == 'experimental':
        return 'experimental'
    versions = installed(method.packages) if versions is None else versions
    if any(v is None for v in versions.values()):
        # 包在主环境里没有，但可能装在隔离环境里
        return 'ready' if isolated_ready(method) else 'experimental'
    return 'ready'


def status_note(method, status=None):
    """给界面/报告的一句话状态说明：状态标签 + 该方法的接入成熟度。"""
    status = status or status_of(method)
    label = STATUS_LABELS[status][0]
    if getattr(method, 'maturity', 'stable') == 'experimental':
        return label + '：适配器已写好但未在本机真实训练验证，结果需逐项复核，不能当作成熟可运行'
    if status == 'planned':
        return label + '：只有注册与说明，没有可调用代码'
    if status == 'experimental':
        return label + '：隔离环境未就绪或依赖缺失'
    return label + '：适配器与本机依赖齐全（真实数据有效性仍需自行核查）'


def family_label(method):
    return FAMILIES[method.family]['label']


def missing_needs(facts, config, method):
    """返回缺失前提的中文说明列表（用于界面“缺什么”一栏）。"""
    checks = {
        'synthetic': (facts.get('synthetic', False), '需要内置模拟数据'),
        'raw_X': (facts.get('raw_x', False),
                  'X 需确认为未归一化计数（当前未确认，或看起来已经归一化/取过对数）'),
        'raw_SU': (facts.get('velocity_counts', False),
                   '需要 spliced/unspliced 原始计数层（普通表达矩阵无法补出剪接层）'),
        'groups': (bool(config.get('group_key')) and
                   (config.get('group_key') == AUTO_GROUP or config.get('group_key') in facts.get('obs_keys', [])),
                   '需要一个细胞分组列，或改用“自动分群”产生群标签'),
        'root': (facts.get('root_cell_present', False) and bool(str(config.get('root_reason', '')).strip()),
                 '需要数据中存在的起始细胞 ID，并填写选择它的生物学依据'),
        'prior_GRN': (bool(config.get('grn_path')) and bool(config.get('grn_confirmed')) and bool(config.get('grn_source')),
                      '需要先验 GRN CSV，并勾选已核查来源/物种/基因 ID'),
        'pseudotime': (bool(facts.get('velocity_counts', False)) or bool(config.get('root_cell')),
                       '需要先得到拟时序（例如先做 DPT）'),
        'matched_ATAC': (bool(facts.get('has_atac', False)),
                         '需要同一批细胞的 ATAC 可达性矩阵与峰到基因映射'),
        'protocol': (bool(facts.get('protocol_known', False)),
                     '需要明确是常规剪接数据还是代谢标记数据（含实验时间）'),
    }
    missing = []
    for need in method.needs:
        ok, why = checks.get(need, (False, '缺少前提：' + need))
        if not ok:
            missing.append(why)
    return missing


def eligibility(facts, config, check_environment=True):
    """逐方法给出准入结论。can_run 同时要求科学前提成立且依赖齐全。"""
    rows = []
    for method in METHODS:
        scientific = list(facts.get('errors', []))
        scientific += missing_needs(facts, config, method)
        versions = installed(method.packages) if check_environment else {}
        runtime = []
        if check_environment:
            runtime += ['缺少 Python 包：' + k for k, v in versions.items() if v is None]
        if not method.implemented:
            runtime.append('适配器尚未完成：方法库已注册，但没有可调用代码')
        if method.key == 'regvelo' and check_environment and versions.get('regvelo') not in (None, '0.4.2'):
            runtime.append('当前适配器仅支持 RegVelo 0.4.2')
        status = status_of(method, versions if check_environment else None)
        rows.append({
            **asdict(method),
            'family_label': family_label(method),
            'status': status,
            'status_label': STATUS_LABELS[status][0],
            'status_kind': STATUS_LABELS[status][1],
            'status_note': status_note(method, status),
            'maturity': getattr(method, 'maturity', 'stable'),
            'input_protocol': method.input_protocol,
            'output_protocol': method.output_protocol,
            'needs_text': [NEEDS.get(n, (n, ''))[0] for n in method.needs],
            'scientific_blockers': scientific,
            'runtime_blockers': runtime,
            'versions': versions,
            'can_run': not scientific and not runtime,
        })
    return rows


def implemented_keys():
    return [m.key for m in METHODS if m.implemented]
