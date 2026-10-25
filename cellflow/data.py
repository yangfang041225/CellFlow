"""数据读取、格式体检与科学准入。

设计原则：
1. 只做“观察”，不做猜测。数值检查是 observed，用户的来源声明是 assumed。
2. 普通表达矩阵不会被猜成剪接计数；缺少条件时明确说明缺什么。
3. 体格检查（inspect）与准入判断（validate）分开：前者给界面看，后者给队列看。
"""
from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import zipfile
import numpy as np
from scipy import sparse

# 规模上限：按「矩阵元素数」+「实际内存」双重判断，而不是死卡一个元素数。
# 参考：8k 细胞 × 20k 基因 = 1.7 亿元素，稀疏存储约 200 MB；16 GB 内存可以轻松处理。
# 真正的风险是“稠密矩阵”和“很多层/大层”，所以内存才是硬约束。
MAX_ENTRIES = 2_000_000_000      # 元素数上限（约 30k 细胞 × 60k 基因；只作粗筛）
MAX_MATRIX_GB = 3.0              # X + 各数据层合计的内存上限（GB）
MAX_BYTES = 4 * 1024 ** 3        # 单个输入文件上限（4 GiB，本机文件）
MAX_UPLOAD_MB = 1024             # 网页上传上限（MiB，同时受 .streamlit/config.toml 控制）

# 分组列的特殊取值：表示“运行时用 Leiden 自动分群”，而不是数据里已有的列。
AUTO_GROUP = '__auto_leiden__'


@dataclass
class Dataset:
    X: object
    cells: np.ndarray
    genes: np.ndarray
    layers: dict = field(default_factory=dict)
    obs: dict = field(default_factory=dict)
    synthetic: bool = False
    extras: dict = field(default_factory=dict)


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def ids_hash(ids):
    return hashlib.sha256('\0'.join(map(str, ids)).encode()).hexdigest()


def make_demo(path, seed=42):
    """内置模拟数据：已知潜在时间 + 4 个模拟供体 + 早/中/晚群标签。

    标记 synthetic=True，任何结果页面与报告都会显示“模拟，仅供教学”。
    """
    rng = np.random.default_rng(seed)
    n, g = 240, 120
    t = np.linspace(0, 1, n)
    centers = np.linspace(0.05, 0.95, g)
    rate = 2 + 30 * np.exp(-((t[:, None] - centers) / 0.18) ** 2)
    s = rng.poisson(rate).astype('float32')
    u = rng.poisson(0.25 * (2 + 30 * np.exp(-((t[:, None] + 0.045 - centers) / 0.18) ** 2))).astype('float32')
    genes = np.array(['MT-DEMO1', 'MT-DEMO2'] + [f'DEMO_G{i:03}' for i in range(g - 2)])
    donors = np.array([f'demo_donor_{i % 4 + 1}' for i in range(n)])
    np.savez_compressed(path, X=s + u, spliced=s, unspliced=u,
                        cells=np.array([f'demo_cell_{i:03}' for i in range(n)]), genes=genes,
                        obs_demo_time=t, obs_group=np.where(t < .33, 'early', np.where(t < .66, 'middle', 'late')),
                        obs_sample=donors, synthetic=np.array(True))
    return Path(path)


def load(path):
    path = Path(path)
    size_gb = path.stat().st_size / 1024 ** 3
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f'文件有 {size_gb:.1f} GB，超过本机单文件上限 {MAX_BYTES / 1024 ** 3:.0f} GB；'
                         '请先生成受控子集。')
    if path.suffix.lower() == '.npz':
        with zipfile.ZipFile(path) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_BYTES or len(z.infolist()) > 40:
                raise ValueError('解压后数据过大或成员过多')
        with np.load(path, allow_pickle=False) as z:
            x = z['X']
            if x.ndim != 2 or x.size > MAX_ENTRIES:
                raise ValueError(f'矩阵必须为 cells × genes，且不超过 {MAX_ENTRIES // 10 ** 6} 百万个元素')
            return Dataset(x, z['cells'].astype(str), z['genes'].astype(str),
                           {k: z[k] for k in ('spliced', 'unspliced') if k in z},
                           {k[4:]: z[k] for k in z.files if k.startswith('obs_')},
                           bool(z['synthetic']) if 'synthetic' in z else False,
                           {'obsm': [], 'obsp': [], 'uns': [], 'varm': [], 'raw': False})
    if path.suffix.lower() not in ('.h5ad',):
        # 其它格式（loom / 10x / CSV / zip / 目录）交给统一导入器：
        # 用户不需要关心格式，也不需要关心矩阵方向。
        from .importers import import_any
        return import_any(path).dataset
    import anndata as ad
    import h5py
    # 先看维度，再读入：AnnData 即使 backed 也可能加载层。
    with h5py.File(path, 'r') as handle:
        if 'X' not in handle:
            raise ValueError('h5ad 缺少 X 矩阵')
        node = handle['X']
        shape = node.shape if isinstance(node, h5py.Dataset) else tuple(node.attrs.get('shape', ()))
        if len(shape) != 2 or int(shape[0]) * int(shape[1]) > MAX_ENTRIES:
            raise ValueError(f'矩阵超过本原型的 {MAX_ENTRIES // 10 ** 6} 百万元素上限；请先生成子集')
        est = _h5_node_bytes(node)
        if est > MAX_MATRIX_GB * 1024 ** 3:
            raise ValueError(f'X 大约要占 {est / 1024 ** 3:.1f} GB，超过本机安全上限 '
                             f'{MAX_MATRIX_GB:.0f} GB；请先生成子集（例如随机抽 2 万细胞）')
        extras = {
            'obsm': [k for k in handle['obsm']] if 'obsm' in handle else [],
            'obsp': [k for k in handle['obsp']] if 'obsp' in handle else [],
            'uns': [k for k in handle['uns']] if 'uns' in handle else [],
            'varm': [k for k in handle['varm']] if 'varm' in handle else [],
            'raw': 'raw' in handle,
        }
    a = ad.read_h5ad(path)
    return Dataset(a.X, a.obs_names.to_numpy(dtype=str), a.var_names.to_numpy(dtype=str),
                   {k: a.layers[k] for k in ('spliced', 'unspliced') if k in a.layers},
                   {str(k): a.obs[k].to_numpy() for k in a.obs}, bool(a.uns.get('cellflow_synthetic', False)),
                   extras)


def values(matrix):
    return matrix.data if sparse.issparse(matrix) else np.asarray(matrix)


def matrix_bytes(matrix):
    """粗略估计一个矩阵在内存里占多少字节（稀疏按 data+indices+indptr）。"""
    if sparse.issparse(matrix):
        return int(matrix.data.nbytes + matrix.indices.nbytes + matrix.indptr.nbytes)
    return int(np.asarray(matrix).nbytes)


def _h5_node_bytes(node):
    """在真正读入之前，估算 h5 文件里某个节点占多少字节。"""
    import h5py
    try:
        if isinstance(node, h5py.Dataset):
            return int(node.nbytes)
        total = 0
        for key in ('data', 'indices', 'indptr'):
            if key in node:
                total += int(node[key].nbytes)
        if total:
            return total
        shape = tuple(node.attrs.get('shape', ()))
        return int(np.prod(shape)) * 4 if shape else 0
    except Exception:
        return 0


def summary(matrix):
    v = values(matrix)
    numeric = np.issubdtype(v.dtype, np.number) and not np.issubdtype(v.dtype, np.complexfloating)
    finite = bool(numeric and (np.size(v) == 0 or np.isfinite(v).all()))
    nonnegative = bool(finite and (np.size(v) == 0 or (v >= 0).all()))
    counts = bool(nonnegative and (np.size(v) == 0 or np.allclose(v, np.rint(v), rtol=0, atol=1e-5)))
    return dict(shape=list(matrix.shape), finite=finite, nonnegative=nonnegative, integer_like=counts,
                nonzero=int(np.count_nonzero(v)), sparse=bool(sparse.issparse(matrix)))


# ---------------------------------------------------------------- 格式体检
def _id_kind(names):
    names = [str(v) for v in names]
    sample = names[:2000]
    ens = sum(1 for v in sample if v.upper().startswith('ENS'))
    if ens > len(sample) * .6:
        return 'Ensembl ID（如 ENSG00000141510）'
    numeric = sum(1 for v in sample if v.replace('.', '').isdigit())
    if numeric > len(sample) * .8:
        return '纯数字 ID'
    symbol = sum(1 for v in sample if any(c.isalpha() for c in v) and len(v) <= 20)
    if symbol > len(sample) * .6:
        return '基因名/符号（如 CD3D、MT-CO1）'
    return '其他自定义 ID'


def _size_text(num):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if num < 1024:
            return f'{num:.0f} {unit}' if unit == 'B' else f'{num:.1f} {unit}'
        num /= 1024
    return f'{num:.1f} TB'


def inspect(data, path=None, mt_prefix='MT-'):
    """生成给人看的“数据体检报告”。不修改数据，不做任何过滤。"""
    n, g = data.X.shape
    mats = {'X': summary(data.X), **{k: summary(v) for k, v in data.layers.items()}}
    detail = {}
    for name, mat in [('X', data.X)] + list(data.layers.items()):
        v = values(mat)
        v = v[np.isfinite(v)] if v.size else v
        detail[name] = {
            **mats[name],
            'dtype': str(np.asarray(values(mat)).dtype),
            'nnz': int(np.count_nonzero(values(mat))),
            'nonzero_pct': float(100.0 * np.count_nonzero(values(mat)) / max(mat.shape[0] * mat.shape[1], 1)),
            'min': float(v.min()) if v.size else None,
            'max': float(v.max()) if v.size else None,
            'mean_nonzero': float(v.mean()) if v.size else None,
        }
    obs = []
    for key, arr in data.obs.items():
        arr = np.asarray(arr)
        try:
            flat = arr.reshape(n, -1)[:, 0] if arr.dtype == object else arr
        except Exception:
            flat = arr
        nunique = len(np.unique(flat[~_nan_mask(flat)])) if flat.dtype.kind in 'OUifb' else 0
        missing = int(_nan_mask(flat).sum()) if flat.dtype.kind in 'OUif' else 0
        obs.append({
            'name': str(key),
            'dtype': str(arr.dtype),
            'n_unique': int(nunique),
            'n_missing': missing,
            'is_group_candidate': bool(2 <= nunique <= 200 and missing == 0),
            'is_sample_candidate': bool(2 <= nunique <= 50 and missing == 0 and nunique < n * .5),
            'example': _short(flat),
        })
    var = {
        'unique': len(set(map(str, data.genes))) == g,
        'id_kind': _id_kind(data.genes),
        'first': [str(v) for v in data.genes[:5]],
        'last': [str(v) for v in data.genes[-3:]],
    }
    upper = [str(v).upper() for v in data.genes]
    prefix = (mt_prefix or 'MT-').upper()
    mt_hits = [str(v) for v in data.genes if str(v).upper().startswith(prefix)] if prefix else []
    var['mt_matched'] = len(mt_hits)
    var['mt_examples'] = mt_hits[:5]
    var['mt_alt_hint'] = _mt_hint(upper, prefix)
    splice = None
    if 'spliced' in data.layers and 'unspliced' in data.layers:
        s = values(data.layers['spliced']).astype(float)
        u = values(data.layers['unspliced']).astype(float)
        ssum = np.asarray(data.layers['spliced'].sum(1)).ravel()
        usum = np.asarray(data.layers['unspliced'].sum(1)).ravel()
        both = (ssum > 0) & (usum > 0)
        frac = np.divide(usum, ssum + usum, out=np.zeros_like(usum, dtype=float), where=(ssum + usum) > 0)
        splice = {
            'both_positive_pct': float(100.0 * both.mean()),
            'median_unspliced_fraction': float(np.median(frac)),
            'empty_cells_pct': float(100.0 * (~both).mean()),
            'shared_nonzero_genes': int(((np.asarray(data.layers['spliced'].sum(0)).ravel() > 0) &
                                         (np.asarray(data.layers['unspliced'].sum(0)).ravel() > 0)).sum()),
        }
    kind = x_kind(data.X, mats['X'])
    report = {
        'file': {
            'name': Path(path).name if path else '内置示例',
            'suffix': Path(path).suffix.lower() if path else '.npz',
            'size_bytes': int(Path(path).stat().st_size) if path and Path(path).exists() else None,
            'size_text': _size_text(Path(path).stat().st_size) if path and Path(path).exists() else '未知',
        },
        'shape': {'n_cells': int(n), 'n_genes': int(g)},
        'matrices': detail,
        'layers_present': [k for k in data.layers],
        'obs': obs,
        'var': var,
        'extras': data.extras or {},
        'splice': splice,
        'x_kind': kind,
        'x_kind_text': X_KIND_TEXT[kind],
        'synthetic': bool(data.synthetic),
        'warnings': [],
        'suggestions': {},
    }
    # ---- 观察到的风险（只陈述事实，不下生物学结论）
    w = report['warnings']
    x = detail['X']
    if kind == 'counts':
        w.append('X 外观是整数计数——这只是外观，不能证明它就是原始 UMI 计数（上游可能已取整或缩放）。')
    elif kind == 'log':
        w.append('X 看起来已经取过对数（数值小、非整数）：程序不会再归一化或取对数，直接用于特征选择与降维。')
    elif kind == 'scaled':
        w.append('X 非整数且数值较大：像是已缩放到人为总量；程序只会取对数，不会再次缩放。')
    if splice is not None and kind != 'counts':
        w.append('X 不是原始计数、但剪接层存在：velocity 仍可运行（scVelo 用剪接层，X 只用于降维）。')
    if x['nonzero_pct'] < 1:
        w.append(f'X 只有 {x["nonzero_pct"]:.2f}% 非零，可能过度稀疏或基因集不匹配（例如基因 ID 不一致）。')
    zero_cells = int((np.asarray(data.X.sum(1)).ravel() == 0).sum())
    if zero_cells:
        w.append(f'有 {zero_cells} 个细胞总表达为 0，通常需要先移除。')
    if not var['unique']:
        w.append('基因 ID 有重复；程序不会静默改名，需要你提供正确映射。')
    if len(set(map(str, data.cells))) != n:
        w.append('细胞 ID 有重复。')
    if splice is None:
        w.append('没有 spliced/unspliced 层：RNA velocity 类方法不能运行（普通表达矩阵无法补出剪接层）。')
    elif splice['shared_nonzero_genes'] < .1 * g:
        w.append(f'同时有 spliced 与 unspliced 信号的基因只有 {splice["shared_nonzero_genes"]} 个，剪接信号偏弱。')
    if var['mt_matched'] == 0:
        w.append(f'按前缀 “{prefix}” 找不到线粒体基因：线粒体比例这一项将没有意义（可直接填 0 或留空）。')
    if 'spliced' in data.layers:
        sp = detail['spliced']
        if sp['max'] is not None and sp['max'] <= 30 and not sp['integer_like']:
            w.append('spliced 层看起来也已经归一化；velocity 方法需要原始剪接计数。')
    # ---- 建议
    sug = report['suggestions']
    if var['mt_alt_hint']:
        sug['mt_prefix'] = var['mt_alt_hint']
    groups = [o['name'] for o in obs if o['is_group_candidate']]
    samples = [o['name'] for o in obs if o['is_sample_candidate']]
    sug['group_key'] = groups[0] if groups else ''
    sug['sample_key'] = samples[0] if samples else ''
    sug['group_candidates'] = groups
    sug['sample_candidates'] = samples
    sug['can_cluster'] = bool(n >= 50 and g >= 50)
    return report


def _nan_mask(arr):
    try:
        if arr.dtype.kind in 'fc':
            return ~np.isfinite(arr)
        if arr.dtype.kind in 'O':
            return np.array([v is None or (isinstance(v, float) and np.isnan(v)) for v in arr])
        return np.zeros(arr.shape, bool)
    except Exception:
        return np.zeros(getattr(arr, 'shape', (0,)), bool)


def _short(arr, k=3):
    try:
        vals = [str(v) for v in np.asarray(arr).ravel()[:k]]
    except Exception:
        vals = []
    return vals


def _mt_hint(upper, prefix):
    """线粒体前缀猜错时给出可操作的建议。"""
    if not upper:
        return ''
    cands = {'MT-': 0, 'MT.': 0, 'MT_': 0, 'MT': 0}
    for g in upper:
        for c in cands:
            if g.startswith(c):
                cands[c] += 1
    cands[prefix] = 0 if prefix in cands else cands.get(prefix, 0)
    best = max(cands.items(), key=lambda kv: kv[1])
    if best[1] > 0 and best[0] != prefix:
        return best[0]
    return ''


# ---------------------------------------------------------------- 科学准入
def x_kind(matrix, info=None):
    """判断 X 大致处于哪种状态，决定“要不要再归一化”。

    - counts：整数外观，按原始计数处理（做 CP10k + log1p）
    - log：非整数且数值较小（<30），几乎肯定已经 log 过（不再归一化、不再取对数）
    - scaled：非整数但数值很大，像是已经被缩放到某个人为总量（只取对数，不再次缩放）
    - empty：全零或无法判断
    """
    info = info or summary(matrix)
    v = values(matrix)
    if v.size == 0 or not np.any(v > 0):
        return 'empty'
    if info['integer_like']:
        return 'counts'
    return 'log' if float(np.nanmax(v)) < 30 else 'scaled'


X_KIND_TEXT = {
    'counts': '整数计数（可直接按原始计数处理）',
    'log': '已经取过对数（不会再次归一化/取对数）',
    'scaled': '已缩放到人为总量（只会取对数，不会再次缩放）',
    'empty': '全零或无法判断',
}


def validate(data, config):
    errors, warnings = [], []
    if len(data.X.shape) != 2:
        raise ValueError('X 必须为二维矩阵')
    n, g = data.X.shape
    if n < 20 or g < 10:
        errors.append('本原型的保守保护：至少 20 个细胞和 10 个基因（并非所有方法的理论阈值）')
    if len(data.cells) != n or len(data.genes) != g:
        errors.append('细胞/基因 ID 数量与矩阵维度不一致')
    for name, ids in [('细胞', data.cells), ('基因', data.genes)]:
        if len(set(ids)) != len(ids) or any(not str(v).strip() for v in ids):
            errors.append(name + ' ID 重复或为空；请明确修复映射，程序不会静默重命名')
    matrices = {'X': summary(data.X), **{k: summary(v) for k, v in data.layers.items()}}
    for name, info in matrices.items():
        if info['shape'] != [n, g]:
            errors.append(name + ' 与 X 不对齐')
        if not info['finite'] or not info['nonnegative']:
            errors.append(name + ' 含非数值、NaN、Inf 或负数')
    for key, arr in data.obs.items():
        if np.asarray(arr).shape != (n,):
            errors.append('obs 列维度错误：' + key)
    total_bytes = matrix_bytes(data.X) + sum(matrix_bytes(layer) for layer in data.layers.values())
    if total_bytes > MAX_MATRIX_GB * 1024 ** 3:
        errors.append(f'X 与各数据层合计约 {total_bytes / 1024 ** 3:.1f} GB，'
                      f'超过本机安全上限 {MAX_MATRIX_GB:.0f} GB；请先生成子集（例如随机抽 2 万细胞）')
    n_entries = n * g
    if n_entries > MAX_ENTRIES:
        errors.append(f'矩阵有 {n_entries / 10 ** 6:.0f} 百万个元素，超过上限 {MAX_ENTRIES // 10 ** 6} 百万')
    kind = x_kind(data.X, matrices['X'])
    raw_x = bool(config.get('raw_x_confirmed') and matrices['X']['integer_like'])
    if config.get('raw_x_confirmed') and not matrices['X']['integer_like']:
        warnings.append('你勾选了“X 为原始计数”，但矩阵不是整数外观：'
                        '不会按原始计数解释，也不会做二次归一化')
    if kind == 'counts' and not raw_x:
        warnings.append('X 看起来是整数计数，但未勾选“原始计数”：只做描述性质控，不做会改变数值的预处理')
    if kind == 'log':
        warnings.append('X 看起来已经取过对数（数值小且非整数）：'
                        '程序不会再次归一化或取对数，直接用它做特征选择与降维')
    if kind == 'scaled':
        warnings.append('X 不是整数且数值较大：像是已缩放到人为总量，程序只会取对数，不再缩放')
    su = all(k in matrices and matrices[k]['integer_like'] and matrices[k]['nonzero'] > 0
             for k in ('spliced', 'unspliced'))
    velocity = bool(su and config.get('raw_su_confirmed'))
    if not su:
        warnings.append('没有可用的 spliced/unspliced 原始计数层：velocity 方法禁用。普通 10x counts 不等于剪接层。')
    elif not config.get('raw_su_confirmed'):
        warnings.append('剪接层未确认是原始计数：velocity 方法禁用（请核实来源后再勾选）')
    if su and kind != 'counts':
        warnings.append('X 不是原始计数、但剪接层是：velocity 会照常运行（scVelo 用的是剪接层；'
                        'X 只用于特征选择与降维）')
    if not config.get('species'):
        warnings.append('物种未知；不做物种特异性注释或通路推断')
    if not config.get('sample_key'):
        warnings.append('未指定生物学样本/供体列；细胞不是独立生物学重复，不提供组间显著性检验')
    elif config['sample_key'] not in data.obs:
        errors.append('指定的供体/样本列不存在')
    if config.get('group_key') and config['group_key'] != AUTO_GROUP and config['group_key'] not in data.obs:
        errors.append('指定的分组列不存在')
    if config.get('root_cell') and config['root_cell'] not in set(data.cells):
        warnings.append('指定起始细胞不在数据中，DPT / CellRank 不能运行')
    if data.synthetic:
        warnings.append('模拟数据：仅验证软件流程，不能支持任何生物学发现')
    if not errors:
        totals = np.asarray(data.X.sum(axis=1)).ravel()
        if np.any(totals == 0):
            errors.append('存在总表达为 0 的细胞；需审阅并移除后重新导入')
        if velocity:
            for layer in ('spliced', 'unspliced'):
                totals_layer = np.asarray(data.layers[layer].sum(axis=1)).ravel()
                if np.mean(totals_layer == 0) > .2:
                    warnings.append(layer + ' 超过 20% 细胞全零：本原型保守拒绝 velocity，仍可质控，需专家核查')
                    velocity = False
    obs_keys = [k for k in data.obs]
    return dict(n_cells=n, n_genes=g, matrices=matrices, errors=errors, warnings=warnings,
                raw_x=raw_x, velocity_counts=velocity, synthetic=data.synthetic,
                root_cell_present=bool(config.get('root_cell') in set(data.cells)),
                group_present=bool(config.get('group_key') and
                                   (config.get('group_key') == AUTO_GROUP or config.get('group_key') in obs_keys)),
                sample_present=bool(config.get('sample_key') in obs_keys),
                has_atac=bool(any('atac' in str(k).lower() for k in obs_keys) or 'atac' in str(data.extras).lower()),
                protocol_known=bool(velocity),
                x_kind=kind, x_kind_text=X_KIND_TEXT[kind],
                obs_keys=obs_keys, cells_hash=ids_hash(data.cells), genes_hash=ids_hash(data.genes))


def qc_metrics(data, config):
    total = np.asarray(data.X.sum(axis=1)).ravel().astype(float)
    detected = np.asarray((data.X > 0).sum(axis=1)).ravel()
    # 前缀可配置；不使用通用阈值删细胞。
    prefix = config.get('mt_prefix', 'MT-').upper()
    mt = np.array([str(g).upper().startswith(prefix) for g in data.genes]) if prefix else np.zeros(len(data.genes), bool)
    mt_pct = 100 * np.asarray(data.X[:, mt].sum(axis=1)).ravel() / np.maximum(total, 1)
    fraction = None
    if all(k in data.layers for k in ('spliced', 'unspliced')):
        s = np.asarray(data.layers['spliced'].sum(axis=1)).ravel()
        u = np.asarray(data.layers['unspliced'].sum(axis=1)).ravel()
        fraction = np.divide(u, s + u, out=np.zeros_like(u, dtype=float), where=(s + u) > 0)
    return dict(total=total.tolist(), detected=detected.tolist(), mt_pct=mt_pct.tolist(),
                mt_genes_found=int(mt.sum()), mt_prefix=prefix,
                unspliced_fraction=None if fraction is None else fraction.tolist(),
                complexity=(detected / np.maximum(total, 1)).tolist())


def to_anndata(data):
    import anndata as ad
    import pandas as pd
    return ad.AnnData(data.X.copy(), obs=pd.DataFrame(data.obs, index=data.cells),
                      var=pd.DataFrame(index=data.genes), layers={k: v.copy() for k, v in data.layers.items()},
                      uns={'cellflow_synthetic': bool(data.synthetic)})
