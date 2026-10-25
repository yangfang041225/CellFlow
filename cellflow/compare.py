from pathlib import Path
import hashlib
import json
import numpy as np


def compare(left, right):
    """Agreement only; NEVER calls agreement accuracy or declares a winner."""
    left, right = Path(left), Path(right)
    read = lambda p, n: json.loads((p / n).read_text(encoding='utf-8'))
    a, b = read(left, 'result.json'), read(right, 'result.json')
    pa, pb = read(left, 'provenance.json'), read(right, 'provenance.json')
    ca, cb = read(left, 'config.json'), read(right, 'config.json')
    blockers = []
    if a.get('synthetic') or b.get('synthetic'):
        blockers.append('模拟结果不进入真实模型比较')
    if pa['input_sha256'] != pb['input_sha256']:
        blockers.append('输入文件 SHA-256 不同')
    if pa.get('code_sha256') != pb.get('code_sha256'):
        blockers.append('分析代码版本不同')
    ea, eb = read(left, 'environment.json'), read(right, 'environment.json')
    for package in ('numpy', 'scipy', 'scanpy', 'scvelo', 'anndata'):
        if ea.get(package) != eb.get(package):
            blockers.append('关键依赖版本不同：' + package)
    for key in ('cells_hash', 'genes_hash', 'preprocessing_hash'):
        if not a.get(key) or a.get(key) != b.get(key):
            blockers.append(key + ' 缺失或不一致')
    for key in ('seed', 'n_top_genes', 'n_neighbors', 'n_pcs', 'min_shared_counts'):
        if ca[key] != cb[key]:
            blockers.append('预处理/随机配置不同：' + key)
    for key in ('max_iter', 'max_epochs'):
        if ca[key] != cb[key]:
            blockers.append('计算预算配置不同：' + key)
    if not a.get('velocity') or not b.get('velocity'):
        blockers.append('两者没有共同基因空间速度；拟时序、PAGA 和速度不可直接比较')
    if blockers:
        return dict(comparable=False, reasons=blockers, explanation='只展示各自诊断，不能排名。')
    with np.load(left / a['velocity'], allow_pickle=False) as x, \
            np.load(right / b['velocity'], allow_pickle=False) as y:
        if not np.array_equal(x['cells'], y['cells']) or not np.array_equal(x['genes'], y['genes']):
            return dict(comparable=False, reasons=['速度矩阵 ID 顺序不一致'])
        v, w = x['velocity'].astype(float), y['velocity'].astype(float)
    norms = np.linalg.norm(v, axis=1) * np.linalg.norm(w, axis=1)
    valid = np.isfinite(v).all(1) & np.isfinite(w).all(1) & (norms > 1e-12)
    if not valid.any():
        return dict(comparable=False, reasons=['没有有效的配对速度向量'])
    cosine = np.einsum('ij,ij->i', v[valid], w[valid]) / norms[valid]
    return dict(comparable=True, paired_cells=int(valid.sum()),
                excluded_cells=int((~valid).sum()),
                median_gene_space_cosine=float(np.median(cosine)),
                explanation='这是方向一致性，不是准确率；未比较不同模型似然、损失或潜在时间。'
                            '需独立时间/谱系证据评价正确性。')


def _embedding_digest(result):
    embedding = result.get('embedding')
    if embedding is None:
        return None
    array = np.asarray(embedding, dtype='<f8')
    if array.ndim != 2 or array.shape[1] < 2:
        return None
    digest = hashlib.sha256()
    digest.update(np.ascontiguousarray(array[:, :2]).tobytes())
    return digest.hexdigest()


def figure_contract(result_a, result_b, config_a, config_b, figures_a, figures_b, identical=False):
    """两张嵌入图能否直接并排/叠加比较。只决定“同坐标比较”，不给模型排名。"""
    reasons = []
    if identical:
        missing = 'embedding.svg' not in figures_a
        return {
            'can_compare_figures': not missing,
            'reasons': ['缺少 embedding.svg'] if missing else [],
            'same_coordinates': True,
            'same_input_identity': True,
            'metrics_comparable': True,
            'metrics_note': '同一个任务，指标当然一致；这不是跨模型比较',
            'side_by_side': False,
            'input_identity': {'cells_hash': result_a.get('cells_hash'),
                               'genes_hash': result_a.get('genes_hash'),
                               'preprocessing_hash': result_a.get('preprocessing_hash')},
        }
    for key, label in (('cells_hash', '细胞集合'), ('genes_hash', '基因集合'),
                       ('preprocessing_hash', '预处理')):
        if not result_a.get(key) or result_a.get(key) != result_b.get(key):
            reasons.append(f'{label}不一致或缺少哈希（{key}）')
    for key in ('n_top_genes', 'use_umap', 'n_neighbors', 'n_pcs', 'seed', 'min_shared_counts'):
        if config_a.get(key) != config_b.get(key):
            reasons.append(f'影响坐标/预处理的参数不同：{key}')
    hash_a, hash_b = _embedding_digest(result_a), _embedding_digest(result_b)
    if not hash_a or not hash_b:
        reasons.append('缺少二维嵌入坐标，无法确认两图同坐标')
    elif hash_a != hash_b:
        reasons.append('两次运行的实际嵌入坐标不同（同一参数也可能因上游版本/顺序不同）')
    for name, figures in (('A', figures_a), ('B', figures_b)):
        if 'embedding.svg' not in figures:
            reasons.append(f'运行 {name} 没有 embedding.svg')
    same_identity = all(result_a.get(key) and result_a.get(key) == result_b.get(key)
                        for key in ('cells_hash', 'genes_hash', 'preprocessing_hash'))
    return {
        'can_compare_figures': not reasons,
        'reasons': reasons,
        'same_coordinates': hash_a is not None and hash_a == hash_b,
        'same_input_identity': same_identity,
        'metrics_comparable': same_identity,
        'metrics_note': ('指标可并列阅读，但不能据此排名模型；不同预处理/参数下指标不可直接比较'
                         if same_identity else
                         '输入/预处理/基因空间不同，指标只能各自阅读，不能直接比较'),
        'side_by_side': True,
        'input_identity': {
            'cells_hash': result_a.get('cells_hash'),
            'genes_hash': result_a.get('genes_hash'),
            'preprocessing_hash': result_a.get('preprocessing_hash'),
            'embedding_hash': hash_a,
        },
    }
