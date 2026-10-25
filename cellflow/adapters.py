"""模型适配器：真实调用 + 统一的输出 schema。

约定：
- 每个适配器都在**输入副本**上工作，返回同一个字典结构，供界面与报告消费。
- 任何无法满足的前提都直接报错说明原因，绝不“降级成另一个方法”糊弄过去。
- 结果里必须带 `caveats`（中文解释边界），图形与指标只做它真正算出来的事。
"""
from pathlib import Path
import hashlib
import inspect
import json
import numpy as np
from scipy import sparse
from scipy.sparse.linalg import LinearOperator, svds
from .data import to_anndata, ids_hash, AUTO_GROUP
from . import device as device_mod

# ---------------------------------------------------------------- 基础工具
def pca2(data, config):
    """不依赖 scanpy 的快速二维 PCA（质控/演示用）。"""
    x = sparse.csr_matrix(data.X, dtype=np.float64)
    if config.get('raw_x_confirmed'):
        x = sparse.diags(1e4 / np.maximum(np.asarray(x.sum(1)).ravel(), 1)) @ x
        x.data = np.log1p(x.data)
    means = np.asarray(x.mean(0)).ravel()
    variances = np.asarray(x.multiply(x).mean(0)).ravel() - means ** 2
    genes = np.argsort(variances, kind='stable')[-min(1000, x.shape[1]):]
    x = x[:, genes].tocsr()
    means = np.asarray(x.mean(0)).ravel()
    n, g = x.shape
    centered = LinearOperator((n, g),
                              matvec=lambda v: np.asarray(x @ v).ravel() - np.dot(means, np.asarray(v).ravel()),
                              rmatvec=lambda v: np.asarray(x.T @ v).ravel() - means * np.asarray(v).sum(),
                              matmat=lambda v: x @ v - (means @ v)[None, :],
                              rmatmat=lambda v: x.T @ v - means[:, None] * v.sum(0)[None, :])
    u, s, vt = svds(centered, k=2, random_state=config['seed'])
    order = np.argsort(s)[::-1]
    coords = u[:, order] * s[order]
    for j in range(2):
        if vt[order[j], np.argmax(np.abs(vt[order[j]]))] < 0:
            coords[:, j] *= -1
    return coords


class Recorder:
    """把每一次真实函数调用记到 calls.json，便于技术报告复现。"""

    def __init__(self, folder, log):
        self.folder = Path(folder)
        self.log = log
        self.calls = []

    def __call__(self, name, function, **kwargs):
        try:
            signature = str(inspect.signature(function))
        except (TypeError, ValueError):
            signature = '（该对象无可用签名，例如实例方法）'
        self.calls.append(dict(function=name, arguments=kwargs, installed_signature=signature))
        (self.folder / 'calls.json').write_text(
            json.dumps(self.calls, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        self.log(name + ' ' + json.dumps(kwargs, ensure_ascii=False, default=str))


def base_result(method, data, config):
    info = device_mod.resolve(config.get('device', 'auto'))
    return dict(method=method, synthetic=bool(data.synthetic),
                cells_hash=ids_hash(data.cells), genes_hash=ids_hash(data.genes),
                embedding=pca2(data, config).tolist(),
                embedding_name='PCA（展示结构，不代表分化方向）', embedding_basis='pca',
                time=None, time_name=None, metrics={}, gene_table=[], caveats=[],
                velocity=None, evidence=[],
                device=info.device, device_info=info.as_dict(),
                device_note=device_mod.note_for(method, info.device))


def _prep_expression(a, config, seed, record, log):
    """表达量方法的统一预处理，按 X 的实际状态决定要不要归一化。

    - counts：CP10k 归一化 + log1p
    - scaled：只取对数（不再缩放）
    - log：原样使用（避免二次归一化把数值搞坏）
    """
    import scanpy as sc
    from .data import x_kind
    kind = x_kind(a.X)
    if kind == 'counts' and not config.get('raw_x_confirmed'):
        raise ValueError('X 看起来是原始计数（整数），但尚未勾选“X 是未归一化原始计数”。'
                         '为避免在未知处理史上做会改变数值的归一化，本次停止。'
                         '请先在数据页确认该声明，或提供已归一化/对数化的 X。')
    if kind == 'counts':
        record('scanpy.pp.normalize_total', sc.pp.normalize_total, target_sum=1e4)
        sc.pp.normalize_total(a, target_sum=1e4)
        sc.pp.log1p(a)
        log('X 是整数计数：已做 CP10k 归一化 + log1p')
    elif kind == 'scaled':
        sc.pp.log1p(a)
        log('X 像是已缩放到人为总量：只做 log1p，未再缩放')
    else:
        log('X 看起来已经取过对数：跳过归一化与 log1p，直接用于特征选择')
    n_top = int(min(config['n_top_genes'], a.n_vars))
    record('scanpy.pp.highly_variable_genes', sc.pp.highly_variable_genes,
           n_top_genes=n_top, flavor='seurat')
    sc.pp.highly_variable_genes(a, n_top_genes=n_top, flavor='seurat')
    a = a[:, a.var['highly_variable']].copy()
    if a.n_vars < 10:
        raise ValueError('高变基因过滤后不足 10 个基因；不强行运行')
    n_pcs = int(min(config['n_pcs'], a.n_vars - 1, a.n_obs - 1))
    if n_pcs < 2:
        raise ValueError('特征选择后不足 3 个基因；不强行运行')
    record('scanpy.pp.pca', sc.pp.pca, n_comps=n_pcs, random_state=seed)
    sc.pp.pca(a, n_comps=n_pcs, random_state=seed)
    k = int(min(config['n_neighbors'], a.n_obs - 1))
    record('scanpy.pp.neighbors', sc.pp.neighbors, n_neighbors=k, n_pcs=n_pcs, random_state=seed)
    sc.pp.neighbors(a, n_neighbors=k, n_pcs=n_pcs, random_state=seed)
    log(f'预处理完成：高变基因 {a.n_vars} 个，PCA {n_pcs} 维，邻居 {k} 个')
    if config.get('use_umap') and a.n_obs >= 50:
        record('scanpy.tl.umap', sc.tl.umap, random_state=seed)
        sc.tl.umap(a, random_state=seed)
    return a, dict(n_hvg=int(a.n_vars), n_pcs=n_pcs, n_neighbors=k)


def _leiden(a, config, seed, record, log, key='leiden'):
    import scanpy as sc
    res = float(config.get('leiden_resolution', 1.0))
    record('scanpy.tl.leiden', sc.tl.leiden, resolution=res, random_state=seed,
           flavor='igraph', n_iterations=2, directed=False, key_added=key)
    sc.tl.leiden(a, resolution=res, random_state=seed, flavor='igraph',
                 n_iterations=2, directed=False, key_added=key)
    a.obs[key] = a.obs[key].astype(str).astype('category')
    log(f'Leiden 自动分群完成：{a.obs[key].nunique()} 个群（分辨率 {res}）')
    return key


def _embedding(a, config):
    if config.get('use_umap') and 'X_umap' in a.obsm:
        return np.asarray(a.obsm['X_umap'][:, :2]), 'UMAP', 'umap'
    return np.asarray(a.obsm['X_pca'][:, :2]), 'PCA', 'pca'


def _group_labels(a, config, record, log):
    """返回分组列名。没有现成分组时自动 Leiden 分群。"""
    key = str(config.get('group_key') or '')
    if key and key != AUTO_GROUP and key in a.obs:
        values = a.obs[key]
        missing = int(values.isna().sum())
        if missing:
            raise ValueError(f'分组列 {key} 有 {missing} 个缺失值；请先补全，程序不会把它们并成 “None” 群')
        a.obs[key] = values.astype(str).astype('category')
        if not 2 <= a.obs[key].nunique() <= 30:
            raise ValueError('分组列必须包含 2–30 个群；程序不会自动用别的标签替换它')
        return key
    key = _leiden(a, config, int(config['seed']), record, log)
    if not 2 <= a.obs[key].nunique() <= 30:
        raise ValueError(f'自动分群得到 {a.obs[key].nunique()} 个群，超出 2–30 的可用范围；'
                         '请调整分群分辨率或提供自己的分组列')
    return key


def _top_genes_per_group(a, key, top=5):
    """描述性：某群组内平均表达减去其余群平均表达，取最高的几个基因。

    不做检验、不给 p 值，只作为“看看这群里什么基因偏高”的线索。
    """
    X = a.X.tocsr() if sparse.issparse(a.X) else sparse.csr_matrix(a.X)
    labels = a.obs[key].astype(str).to_numpy()
    genes = np.asarray(a.var_names, dtype=str)
    out = []
    for group in sorted(set(labels)):
        inside = labels == group
        if inside.all() or not inside.any():
            continue
        m_in = np.asarray(X[inside].mean(0)).ravel()
        m_out = np.asarray(X[~inside].mean(0)).ravel()
        diff = m_in - m_out
        idx = np.argsort(diff)[-top:][::-1]
        out.append(dict(group=str(group), n=int(inside.sum()),
                        genes=[dict(gene=str(genes[i]), diff=round(float(diff[i]), 4),
                                    mean_in=round(float(m_in[i]), 4)) for i in idx if diff[i] > 0]))
    return out


def _diffmap_dpt(a, config, seed, record, log, root_cell):
    import scanpy as sc
    if root_cell not in a.obs_names:
        raise ValueError('指定的起始细胞 ID 不存在于数据中')
    a.uns['iroot'] = int(a.obs_names.get_loc(root_cell))
    n_dcs = int(min(10, a.n_obs - 2))
    record('scanpy.tl.diffmap', sc.tl.diffmap, n_comps=n_dcs, random_state=seed)
    sc.tl.diffmap(a, n_comps=n_dcs, random_state=seed)
    record('scanpy.tl.dpt', sc.tl.dpt, n_dcs=n_dcs, n_branchings=0)
    sc.tl.dpt(a, n_dcs=n_dcs, n_branchings=0)
    t = np.asarray(a.obs['dpt_pseudotime'], dtype=float)
    log(f'DPT 完成：起点 {root_cell}，可达细胞 {int(np.isfinite(t).sum())}/{a.n_obs}')
    return t, n_dcs


def _group_time_summary(t, labels):
    """每个群的拟时序均值（仅作方向线索，不是谱系证据）。"""
    rows = []
    finite = np.isfinite(t)
    labels = np.asarray(labels, dtype=str)
    for group in sorted(set(labels.tolist())):
        mask = (labels == group) & finite
        if not mask.any():
            continue
        rows.append(dict(group=group, n=int(mask.sum()),
                         mean_time=float(np.nanmean(t[mask])),
                         median_time=float(np.nanmedian(t[mask]))))
    ordered = sorted(rows, key=lambda r: r['mean_time'])
    return dict(table=rows, order=[r['group'] for r in ordered])


# ---------------------------------------------------------------- velocity 工具箱
def _prepare_velocity_x(a, log):
    """scVelo 需要 counts 形式的 X。

    真实数据常见情况：X 已经归一化/取过对数，但 spliced/unspliced 仍是原始计数
    （velocyto、STARsolo 的输出就是这样）。这时用 spliced+unspliced 重建 X，
    这是 scVelo 的标准约定，而不是“偷偷改数据”。
    """
    from .data import x_kind
    kind = x_kind(a.X)
    if kind == 'counts':
        return 'X 为整数计数，直接使用', kind
    ok = all(k in a.layers for k in ('spliced', 'unspliced'))
    if ok and x_kind(a.layers['spliced']) == 'counts' and x_kind(a.layers['unspliced']) == 'counts':
        total = a.layers['spliced'] + a.layers['unspliced']
        a.X = sparse.csr_matrix(total) if sparse.issparse(total) else np.asarray(total)
        return 'X 不是原始计数：用 spliced+unspliced 重建计数矩阵（scVelo 的标准做法）', 'counts'
    return 'X 不是原始计数，且剪接层也不是整数计数：velocity 结果可能不可靠，请先核实数据来源', kind


def _scvelo_prepare(a, config, seed, record, log):
    """scVelo 专用预处理：过滤 → 归一化(含剪接层) → log1p → HVG → PCA/邻域/UMAP → moments。"""
    import scanpy as sc
    import scvelo as scv
    record('scvelo.pp.filter_genes', scv.pp.filter_genes, min_shared_counts=config['min_shared_counts'])
    scv.pp.filter_genes(a, min_shared_counts=config['min_shared_counts'])
    record('scvelo.pp.normalize_per_cell', scv.pp.normalize_per_cell,
           counts_per_cell_after=1e4, layers=['spliced', 'unspliced'], enforce=True)
    scv.pp.normalize_per_cell(a, counts_per_cell_after=1e4, layers=['spliced', 'unspliced'], enforce=True)
    sc.pp.log1p(a)
    n_top = int(min(config['n_top_genes'], a.n_vars))
    record('scanpy.pp.highly_variable_genes', sc.pp.highly_variable_genes, n_top_genes=n_top, flavor='seurat')
    sc.pp.highly_variable_genes(a, n_top_genes=n_top, flavor='seurat')
    a = a[:, a.var['highly_variable']].copy()
    if a.n_vars < 10:
        raise ValueError('剪接共同计数过滤后不足 10 个基因；请检查数据或有依据地调整 min_shared_counts')
    n_pcs = int(min(config['n_pcs'], a.n_vars - 1, a.n_obs - 1))
    record('scanpy.pp.pca', sc.pp.pca, n_comps=n_pcs, random_state=seed)
    sc.pp.pca(a, n_comps=n_pcs, random_state=seed)
    k = int(min(config['n_neighbors'], a.n_obs - 1))
    record('scanpy.pp.neighbors', sc.pp.neighbors, n_neighbors=k, n_pcs=n_pcs, random_state=seed)
    sc.pp.neighbors(a, n_neighbors=k, n_pcs=n_pcs, random_state=seed)
    if config.get('use_umap') and a.n_obs >= 50:
        record('scanpy.tl.umap', sc.tl.umap, random_state=seed)
        sc.tl.umap(a, random_state=seed)
    record('scvelo.pp.moments', scv.pp.moments, n_pcs=None, n_neighbors=None)
    scv.pp.moments(a, n_pcs=None, n_neighbors=None)
    log(f'velocity 预处理完成：基因 {a.n_vars}，PCA {n_pcs} 维，邻居 {k}')
    return a, dict(n_hvg=int(a.n_vars), n_pcs=n_pcs, n_neighbors=k,
                   min_shared_counts=int(config['min_shared_counts']))


def _velocity_core(a, mode, config, record, log):
    """速度推断 + 投影 + 诊断，结果写回同一个 AnnData（保证箭头与降维同一坐标系）。"""
    import scvelo as scv
    if mode == 'dynamical':
        record('scvelo.tl.recover_dynamics', scv.tl.recover_dynamics,
               n_jobs=1, max_iter=config['max_iter'], show_progress_bar=False)
        scv.tl.recover_dynamics(a, n_jobs=1, max_iter=config['max_iter'], show_progress_bar=False)
    record('scvelo.tl.velocity', scv.tl.velocity, mode=mode)
    scv.tl.velocity(a, mode=mode)
    if 'velocity' not in a.layers:
        raise ValueError('适配器未产生 velocity 层')
    v = np.asarray(a.layers['velocity'].toarray() if sparse.issparse(a.layers['velocity'])
                   else a.layers['velocity'])
    valid = np.isfinite(v).all(0) & (np.linalg.norm(np.nan_to_num(v), axis=0) > 0)
    if valid.sum() < 2:
        raise ValueError('少于 2 个有效速度基因；拟合失败，停止后续推断')
    record('scvelo.tl.velocity_graph', scv.tl.velocity_graph, n_jobs=1, show_progress_bar=False)
    scv.tl.velocity_graph(a, n_jobs=1, show_progress_bar=False)
    record('scvelo.tl.velocity_confidence', scv.tl.velocity_confidence)
    scv.tl.velocity_confidence(a)
    basis = 'umap' if 'X_umap' in a.obsm else 'pca'
    record('scvelo.tl.velocity_embedding', scv.tl.velocity_embedding, basis=basis)
    scv.tl.velocity_embedding(a, basis=basis)
    record('scvelo.tl.velocity_pseudotime', scv.tl.velocity_pseudotime)
    scv.tl.velocity_pseudotime(a)
    if mode == 'dynamical':
        record('scvelo.tl.latent_time', scv.tl.latent_time)
        scv.tl.latent_time(a)
    log(f'速度推断完成（{mode} 模式），投影到 {basis}')
    return dict(mode=mode, basis=basis, n_valid_genes=int(valid.sum()))


def _velocity_payload(a, mode, info):
    """把 velocity 结果整理成统一字段。"""
    basis = info['basis']
    payload = dict(
        velocity='velocity.npz', embedding_basis=basis,
        arrows=np.asarray(a.obsm['velocity_' + basis][:, :2]).tolist()
        if 'velocity_' + basis in a.obsm else None,
        embedding_name=('UMAP' if basis == 'umap' else 'PCA') + ' 与速度投影（二维投影可能失真）',
        metrics={}, caveats=[], evidence=['scVelo 真实调用（%s 模式，CPU）' % mode],
    )
    for key in ('velocity_length', 'velocity_confidence', 'velocity_confidence_transition'):
        if key in a.obs:
            payload['metrics'][key + '_median'] = float(np.nanmedian(np.asarray(a.obs[key], dtype=float)))
    vpt = np.asarray(a.obs['velocity_pseudotime'], dtype=float) if 'velocity_pseudotime' in a.obs else None
    payload['velocity_pseudotime'] = [float(x) if np.isfinite(x) else None for x in vpt] if vpt is not None else None
    if mode == 'dynamical' and 'latent_time' in a.obs:
        payload['latent_time'] = [float(x) if np.isfinite(x) else None
                                  for x in np.asarray(a.obs['latent_time'], dtype=float)]
    columns = [c for c in ('fit_likelihood', 'fit_alpha', 'fit_beta', 'fit_gamma', 'velocity_r2')
               if c in a.var]
    if columns:
        table = a.var[columns].replace([np.inf, -np.inf], np.nan).copy()
        if 'fit_likelihood' in table:
            table = table.sort_values('fit_likelihood', ascending=False)
        table.insert(0, 'gene', table.index.astype(str))
        payload['gene_table'] = json.loads(table.head(30).to_json(orient='records'))
    else:
        payload['gene_table'] = []
    payload['caveats'] = ['速度一致性是内部诊断：数值高不代表方向正确。',
                          '基因排名是候选关联，不证明因果；高似然不等于“驱动基因”。',
                          '不同模型（stochastic / dynamical）的损失、似然与时间尺度不可直接比较。']
    payload['coords'] = np.asarray(a.obsm['X_' + basis][:, :2]).tolist()
    return payload


def _fate_block(a, config, record, log, cluster_key, use_velocity):
    """CellRank 命运概率。有速度用速度图做核（不需要起点），否则用 DPT 拟时序做核。"""
    import cellrank as cr
    n_states = int(config.get('cellrank_n_states', 3))
    if use_velocity:
        record('cellrank.kernels.VelocityKernel', cr.kernels.VelocityKernel)
        kernel = cr.kernels.VelocityKernel(a)
        record('VelocityKernel.compute_transition_matrix', kernel.compute_transition_matrix)
        kernel.compute_transition_matrix(show_progress_bar=False)
        kernel_name = 'VelocityKernel(velocity_graph)'
    else:
        record('cellrank.kernels.PseudotimeKernel', cr.kernels.PseudotimeKernel, time_key='dpt_pseudotime')
        kernel = cr.kernels.PseudotimeKernel(a, time_key='dpt_pseudotime')
        record('PseudotimeKernel.compute_transition_matrix', kernel.compute_transition_matrix,
               n_jobs=1, backend='threading', show_progress_bar=False)
        kernel.compute_transition_matrix(n_jobs=1, backend='threading', show_progress_bar=False)
        kernel_name = 'PseudotimeKernel(dpt_pseudotime)'
    estimator = cr.estimators.GPCCA(kernel)
    n_comp = int(min(20, a.n_obs - 2))
    record('GPCCA.compute_schur', estimator.compute_schur, n_components=n_comp)
    estimator.compute_schur(n_components=n_comp)
    record('GPCCA.compute_macrostates', estimator.compute_macrostates,
           n_states=n_states, cluster_key=cluster_key)
    estimator.compute_macrostates(n_states=n_states, cluster_key=cluster_key)
    record('GPCCA.predict_terminal_states', estimator.predict_terminal_states)
    estimator.predict_terminal_states()
    record('GPCCA.compute_fate_probabilities', estimator.compute_fate_probabilities,
           use_petsc=False, n_jobs=1, backend='threading', show_progress_bar=False)
    estimator.compute_fate_probabilities(use_petsc=False, n_jobs=1, backend='threading',
                                         show_progress_bar=False)
    fp = estimator.fate_probabilities
    names = [str(x) for x in fp.names]
    probs = np.asarray(fp.X, dtype=float)
    terms = estimator.terminal_states
    term_counts = {str(k): int(v) for k, v in terms.value_counts().items()} if terms is not None else {}
    caveats, stats, early = [], {}, []
    try:
        macro = np.asarray(estimator.macrostates.astype(str))
        if 'dpt_pseudotime' in a.obs:
            t = np.asarray(a.obs['dpt_pseudotime'], dtype=float)
            finite = np.isfinite(t)
            median_t = float(np.nanmedian(t[finite])) if finite.any() else float('nan')
            for name in names:
                mask = (macro == name) & finite
                if mask.any():
                    stats[name] = dict(cells=int(mask.sum()), mean_time=float(np.nanmean(t[mask])))
                    if np.isfinite(median_t) and stats[name]['mean_time'] < median_t:
                        early.append(name)
    except Exception as exc:
        log('终末状态时序诊断未完成：' + repr(exc))
    fate = dict(terminal_states=names, terminal_state_cells=term_counts,
                terminal_state_stats=stats, early_terminal_states=early,
                degenerate=len(names) < 2, probabilities=probs.tolist(),
                estimator='GPCCA', kernel=kernel_name, cluster_key=cluster_key, n_states=n_states,
                drivers=[])
    try:
        drivers = estimator.compute_lineage_drivers(use_raw=False, n_jobs=1)
        keep = [c for c in drivers.columns if c.endswith('_corr') or c.endswith('_pval')]
        drivers = drivers[keep].head(30).copy()
        drivers.insert(0, 'gene', drivers.index.astype(str))
        fate['drivers'] = json.loads(drivers.to_json(orient='records'))
    except Exception as exc:
        caveats.append('未能计算命运驱动基因：' + type(exc).__name__ +
                       '（通常是因为只识别到 1 个终末状态）')
    if len(names) < 2:
        caveats.append('只识别到 1 个终末状态：所有细胞的命运概率都接近 1，'
                       '这张图没有区分度：该设定下只识别到一个吸收态，概率接近 1；不能据此判断真实分支数或方向。')
    if early:
        caveats.append('疑似“早端”终末状态：' + '、'.join(early) +
                       '（平均拟时序低于整体中位数）。这通常由细胞图结构或吸收态造成，'
                       '不应解释为“细胞最终回到早期状态”。')
    caveats += ['终末状态由 GPCCA 在细胞图上识别，可能只是采样边界，需要生物学确认。',
                '命运概率是模型推断，不是已验证的谱系；概率高不等于事实。']
    metrics = {'n_terminal_states': len(names), 'n_states': n_states,
               'mean_max_fate_probability': float(np.mean(probs.max(1)))}
    return fate, metrics, caveats


def _auto_pipeline(data, config, folder, record, log, result):
    """一键分析：质控 → 分群 → PAGA →（有剪接层）速度 →（有速度/起点）命运。"""
    import scanpy as sc
    seed = int(config['seed'])
    a = to_anndata(data)
    a.uns.clear()
    a.uns['cellflow_synthetic'] = bool(data.synthetic)
    has_su = all(k in data.layers for k in ('spliced', 'unspliced'))
    use_velocity = bool(has_su and config.get('raw_su_confirmed'))
    root_ok = (bool(config.get('root_cell')) and config['root_cell'] in a.obs_names
               and bool(str(config.get('root_reason', '')).strip()))

    if use_velocity:
        message, _ = _prepare_velocity_x(a, log)
        log('一键分析 · velocity 分支：' + message)
        a, eff = _scvelo_prepare(a, config, seed, record, log)
        result['caveats'].append('本次按剪接层流程处理：' + message)
    else:
        a, eff = _prep_expression(a, config, seed, record, log)
        why = ('数据里没有 spliced/unspliced 层' if not has_su
               else '剪接层未确认是原始计数')
        result['caveats'].append(f'{why}：本次没有做 RNA velocity，也没有命运概率。')
    result['effective'] = eff

    # ---- 分群与群间连接
    key = _leiden(a, config, seed, record, log)
    result['groups'] = dict(key=key, method='leiden',
                            labels=a.obs[key].astype(str).tolist(),
                            sizes={str(k): int(v) for k, v in a.obs[key].value_counts().items()})
    result['cluster_genes'] = _top_genes_per_group(a, key)
    record('scanpy.tl.paga', sc.tl.paga, groups=key)
    sc.tl.paga(a, groups=key)
    conn = a.uns['paga']['connectivities'].toarray()
    result['paga'] = dict(groups=[str(g) for g in a.obs[key].cat.categories],
                          connectivities=conn.tolist(), key=key)
    result['metrics']['n_groups'] = int(a.obs[key].nunique())
    result['metrics']['paga_groups'] = int(len(conn))

    # ---- 速度
    if use_velocity:
        mode = str(config.get('velocity_model') or 'stochastic')
        if mode == 'velovi':
            raise ValueError('一键分析不直接调用 veloVI：它需要独立环境和单独的训练过程，'
                             '请在「模型分析」中选择 veloVI 单独运行。本次不替换为其他模型。')
        if mode not in ('deterministic', 'stochastic', 'dynamical'):
            mode = 'stochastic'
        info = _velocity_core(a, mode, config, record, log)
        payload = _velocity_payload(a, mode, info)
        result['velocity'] = payload['velocity']
        result['embedding'] = payload['coords']
        result['arrows'] = payload['arrows']
        result['embedding_basis'] = payload['embedding_basis']
        result['embedding_name'] = payload['embedding_name']
        result['metrics'].update(payload['metrics'])
        result['caveats'] += payload['caveats']
        result['evidence'] += payload['evidence']
        result['velocity_table'] = payload['gene_table']
        velocity = np.asarray(a.layers['velocity'].toarray() if sparse.issparse(a.layers['velocity'])
                              else a.layers['velocity'])
        np.savez_compressed(folder / 'velocity.npz', velocity=velocity,
                            cells=np.asarray(a.obs_names, dtype=str),
                            genes=np.asarray(a.var_names, dtype=str))
        result['genes_hash'] = ids_hash(a.var_names)

    # ---- 拟时序（需要起点）
    if root_ok:
        t, n_dcs = _diffmap_dpt(a, config, seed, record, log, config['root_cell'])
        result['dpt'] = dict(
            reachable=int(np.isfinite(t).sum()), n_cells=int(a.n_obs), diffmap_components=n_dcs,
            group_order=_group_time_summary(t, a.obs[key].to_numpy()),
        )
        result['time'] = [float(v) if np.isfinite(v) else None for v in t]
        result['time_name'] = 'DPT 拟时序（依赖指定起点，是相对顺序，不是真实时间）'
        result['metrics']['dpt_reachable'] = int(np.isfinite(t).sum())

    # ---- 主展示坐标
    if not result.get('embedding') or result.get('embedding_basis') == 'pca':
        coords, name, basis = _embedding(a, config)
        result['embedding'] = coords.tolist()
        result['embedding_basis'] = basis
        if not use_velocity:
            result['embedding_name'] = name + '（二维投影，不代表分化方向）'

    if not root_ok and use_velocity and 'velocity_pseudotime' in a.obs:
        vpt = np.asarray(a.obs['velocity_pseudotime'], dtype=float)
        result['time'] = [float(v) if np.isfinite(v) else None for v in vpt]
        result['time_name'] = '速度伪时序（基于速度图，无实验标定）'

    # ---- 命运概率
    try:
        import importlib.util
        has_cellrank = importlib.util.find_spec('cellrank') is not None
    except Exception:
        has_cellrank = False
    if has_cellrank and (use_velocity or root_ok):
        if a.n_obs > 6000:
            result['caveats'].append(f'细胞数 {a.n_obs} 超过 CellRank 的安全上限（6000）：'
                                     '本次跳过了命运概率分析（GPCCA 需要稠密分解）。')
        else:
            try:
                fate, fmetrics, fcaveats = _fate_block(
                    a, config, record, log, key, use_velocity=bool(use_velocity))
                result['fate'] = fate
                result['metrics'].update(fmetrics)
                result['caveats'] += fcaveats
                result['evidence'].append('CellRank 2 GPCCA + 命运概率（CPU）')
            except Exception as exc:
                result['caveats'].append('命运概率计算未完成：' + type(exc).__name__ + ': ' + str(exc))
    elif not has_cellrank:
        result['caveats'].append('本环境没有安装 CellRank：跳过命运概率（其余结果不受影响）。')

    result['gene_table'] = result.get('cluster_genes') or []
    result['evidence'].insert(0, 'CellFlow 一键流程（scanpy 分群/PAGA + scVelo 速度 + CellRank 命运）')
    result['caveats'] += [
        '群编号是算法产物，不是细胞类型；群内“相对高表达基因”是描述性线索，没有做检验。',
        'PAGA 连接矩阵是无向的：它说明哪些群靠得近，不说明“谁变成谁”。',
    ]
    log('一键分析完成：分群 %d 个，速度=%s，命运=%s' % (
        a.obs[key].nunique(), bool(use_velocity), 'fate' in result))
    return result, a



def _write_anndata(a, path, log):
    """把 AnnData 写到 h5ad，并绕开第三方对象与 anndata 版本的写入不兼容。

    cellrank 2.0.7 的 Lineage 对象用旧签名调用 anndata 0.12 的 write_basic，
    直接写会抛 TypeError。这里把 obsm 转成普通数组、把模型对象从 uns 里去掉，
    保证“结果对象能保存”不会因为上游版本错配而失败。
    """
    dropped = []
    for key in list(a.obsm.keys()):
        value = a.obsm[key]
        # Lineage 等 ndarray 子类会被 anndata 按第三方类型分派去调用旧签名，
        # 所以这里必须转成“纯 ndarray”，不能只判断 isinstance。
        if type(value) is not np.ndarray:
            try:
                a.obsm[key] = np.array(value, dtype=float)
            except Exception:
                del a.obsm[key]
                dropped.append('obsm/' + key)
    for key in list(a.uns.keys()):
        value = a.uns[key]
        module = type(value).__module__ or ''
        if module.startswith('cellrank') or module.startswith('scvelo') or hasattr(value, '__dataclass_fields__'):
            del a.uns[key]
            dropped.append('uns/' + key)
    try:
        a.write_h5ad(path)
    except Exception as exc:
        log('h5ad 写入失败，改为最小保存：' + repr(exc))
        for key in list(a.obsm.keys()):
            if type(a.obsm[key]) is not np.ndarray:
                a.obsm[key] = np.array(a.obsm[key], dtype=float)
        a.write_h5ad(path)
    if dropped:
        log('为兼容 h5ad 写入而暂不保存的对象：' + ', '.join(dropped))


# ---------------------------------------------------------------- 主入口
def run_adapter(method, data, config, folder, log):
    seed = int(config['seed'])
    np.random.seed(seed)
    folder = Path(folder)
    record = Recorder(folder, log)
    result = base_result(method, data, config)

    if method in ('qc', 'demo'):
        if method == 'demo' and 'demo_time' in data.obs:
            result['time'] = np.asarray(data.obs['demo_time']).tolist()
            result['time_name'] = '已知模拟时间（由生成器给出，不是模型推断）'
        result['caveats'].append('未拟合 RNA velocity，未预测终末细胞或调控网络。')
        result['metrics']['note'] = '仅描述性质控与降维展示'
        log('PCA 与描述性质控完成')
        return result

    if method == 'auto':
        result, a = _auto_pipeline(data, config, folder, record, log, result)
        _write_anndata(a, folder / 'analysis.h5ad', log)
        return result

    import scanpy as sc
    a = to_anndata(data)
    a.uns.clear()
    a.uns['cellflow_synthetic'] = bool(data.synthetic)

    if method == 'leiden':
        a, eff = _prep_expression(a, config, seed, record, log)
        key = _leiden(a, config, seed, record, log)
        result['effective'] = eff
        result['groups'] = dict(
            key=key, method='leiden',
            labels=a.obs[key].astype(str).tolist(),
            sizes={str(k): int(v) for k, v in a.obs[key].value_counts().items()},
        )
        result['gene_table'] = _top_genes_per_group(a, key)
        coords, name, basis = _embedding(a, config)
        result['embedding'] = coords.tolist()
        result['embedding_name'] = name + '（按自动分群结果着色）'
        result['embedding_basis'] = basis
        result['metrics'].update({'n_groups': int(a.obs[key].nunique()),
                                  'resolution': float(config['leiden_resolution'])})
        result['caveats'] += [
            '群编号是算法产物，不是细胞类型；分辨率不同会得到不同的群。',
            '群内“相对高表达基因”是描述性线索，没有做显著性检验，也不等于标记基因。',
        ]
        result['evidence'].append('Leiden/igraph 分群 + UMAP 降维（scanpy）')
        log('自动分群完成')

    elif method == 'dpt':
        a, eff = _prep_expression(a, config, seed, record, log)
        result['effective'] = eff
        t, n_dcs = _diffmap_dpt(a, config, seed, record, log, config['root_cell'])
        result['time'] = [float(v) if np.isfinite(v) else None for v in t]
        result['time_name'] = 'DPT 拟时序（相对顺序，依赖指定起点）'
        result['metrics'].update({'reachable_cells': int(np.isfinite(t).sum()),
                                  'n_cells': int(a.n_obs), 'diffmap_components': n_dcs})
        if config.get('group_key') and config['group_key'] in a.obs:
            key = config['group_key']
            a.obs[key] = a.obs[key].astype(str).astype('category')
            result['groups'] = dict(key=key, labels=a.obs[key].astype(str).tolist(),
                                    sizes={str(k): int(v) for k, v in a.obs[key].value_counts().items()})
            result['metrics']['group_order_by_time'] = _group_time_summary(t, a.obs[key].to_numpy())
        coords, name, basis = _embedding(a, config)
        result['embedding'] = coords.tolist()
        result['embedding_name'] = name + '（按 DPT 拟时序着色）'
        result['embedding_basis'] = basis
        result['caveats'] += [
            '拟时序是相对顺序，不是真实小时数；起点不同，顺序可能改变。',
            '不连通区域可能没有有限拟时序，这些细胞不应强行解释。',
        ]
        result['evidence'].append('Scanpy diffusion map + DPT')
        log('DPT 完成')

    elif method == 'paga':
        a, eff = _prep_expression(a, config, seed, record, log)
        result['effective'] = eff
        key = _group_labels(a, config, record, log)
        record('scanpy.tl.paga', sc.tl.paga, groups=key)
        sc.tl.paga(a, groups=key)
        conn = a.uns['paga']['connectivities'].toarray()
        groups = list(map(str, a.obs[key].cat.categories))
        result['paga'] = dict(groups=groups, connectivities=conn.tolist(), key=key)
        result['groups'] = dict(key=key, labels=a.obs[key].astype(str).tolist(),
                                sizes={str(k): int(v) for k, v in a.obs[key].value_counts().items()})
        result['gene_table'] = _top_genes_per_group(a, key)
        result['metrics'].update({'paga_groups': int(len(conn))})
        if config.get('root_cell') and config['root_cell'] in a.obs_names:
            t, _ = _diffmap_dpt(a, config, seed, record, log, config['root_cell'])
            result['paga']['direction_hint'] = _group_time_summary(t, a.obs[key].to_numpy())
            result['metrics']['direction_hint'] = '群平均拟时序（方向线索，不是谱系证据）'
        coords, name, basis = _embedding(a, config)
        result['embedding'] = coords.tolist()
        result['embedding_name'] = name + '（按群标签着色）'
        result['embedding_basis'] = basis
        result['caveats'] += [
            'PAGA 连接矩阵是无向的：它说明哪些群靠得近，不说明“谁变成谁”。',
            '若要谈方向，需要速度或拟时序证据，并明确其假设。',
        ]
        result['evidence'].append('Scanpy PAGA（无向连通强度）')
        log('PAGA 完成')

    elif method in ('scvelo_deterministic', 'scvelo_stochastic', 'scvelo_dynamical'):
        import scvelo as scv
        # scVelo 0.3.4 的 filter_and_normalize 已不再接收 n_top_genes/log1p；
        # 这里显式逐步调用，避免依赖过期教程的包装行为。
        record('scvelo.pp.filter_genes', scv.pp.filter_genes, min_shared_counts=config['min_shared_counts'])
        scv.pp.filter_genes(a, min_shared_counts=config['min_shared_counts'])
        record('scvelo.pp.normalize_per_cell', scv.pp.normalize_per_cell,
               counts_per_cell_after=1e4, layers=['spliced', 'unspliced'], enforce=True)
        scv.pp.normalize_per_cell(a, counts_per_cell_after=1e4, layers=['spliced', 'unspliced'], enforce=True)
        sc.pp.log1p(a)
        n_top = int(min(config['n_top_genes'], a.n_vars))
        record('scanpy.pp.highly_variable_genes', sc.pp.highly_variable_genes, n_top_genes=n_top, flavor='seurat')
        sc.pp.highly_variable_genes(a, n_top_genes=n_top, flavor='seurat')
        a = a[:, a.var['highly_variable']].copy()
        if a.n_vars < 10:
            raise ValueError('剪接共同计数过滤后不足 10 个基因；请检查数据或有依据地调整阈值')
        n_pcs = int(min(config['n_pcs'], a.n_vars - 1, a.n_obs - 1))
        record('scanpy.pp.pca', sc.pp.pca, n_comps=n_pcs, random_state=seed)
        sc.pp.pca(a, n_comps=n_pcs, random_state=seed)
        k = int(min(config['n_neighbors'], a.n_obs - 1))
        record('scanpy.pp.neighbors', sc.pp.neighbors, n_neighbors=k, n_pcs=n_pcs, random_state=seed)
        sc.pp.neighbors(a, n_neighbors=k, n_pcs=n_pcs, random_state=seed)
        if config.get('use_umap') and a.n_obs >= 50:
            record('scanpy.tl.umap', sc.tl.umap, random_state=seed)
            sc.tl.umap(a, random_state=seed)
        record('scvelo.pp.moments', scv.pp.moments, n_pcs=None, n_neighbors=None)
        scv.pp.moments(a, n_pcs=None, n_neighbors=None)
        result['effective'] = dict(n_hvg=int(a.n_vars), n_pcs=n_pcs, n_neighbors=k,
                                   min_shared_counts=int(config['min_shared_counts']))
        log('S/U 共享预处理与 moments 完成')
        # scVelo 官方 mode：deterministic(稳态) / stochastic / dynamical，直接透传给上游，不自写算法
        mode = {'scvelo_dynamical': 'dynamical',
                'scvelo_deterministic': 'deterministic'}.get(method, 'stochastic')
        if mode == 'dynamical':
            record('scvelo.tl.recover_dynamics', scv.tl.recover_dynamics,
                   n_jobs=1, max_iter=config['max_iter'], show_progress_bar=False)
            scv.tl.recover_dynamics(a, n_jobs=1, max_iter=config['max_iter'], show_progress_bar=False)
        record('scvelo.tl.velocity', scv.tl.velocity, mode=mode)
        scv.tl.velocity(a, mode=mode)
        if 'velocity' not in a.layers:
            raise ValueError('适配器未产生 velocity 层')
        v_raw = np.asarray(a.layers['velocity'].toarray() if sparse.issparse(a.layers['velocity'])
                           else a.layers['velocity'])
        valid = np.isfinite(v_raw).all(0) & (np.linalg.norm(np.nan_to_num(v_raw), axis=0) > 0)
        if valid.sum() < 2:
            raise ValueError('少于 2 个有效速度基因；拟合失败，停止后续推断')
        if not valid.all():
            a = a[:, valid].copy()
            result['caveats'].append(f'{int((~valid).sum())} 个非有限或全零速度基因未进入图；详见基因表。')
        fingerprint = hashlib.sha256()
        for matrix in (a.layers['Ms'], a.layers['Mu'], a.obsp['connectivities']):
            csr = sparse.csr_matrix(matrix)
            for value in (csr.data, csr.indices, csr.indptr):
                fingerprint.update(np.ascontiguousarray(value).tobytes())
            fingerprint.update(str(csr.shape).encode())
        result['preprocessing_hash'] = fingerprint.hexdigest()
        record('scvelo.tl.velocity_graph', scv.tl.velocity_graph, n_jobs=1, show_progress_bar=False)
        scv.tl.velocity_graph(a, n_jobs=1, show_progress_bar=False)
        record('scvelo.tl.velocity_confidence', scv.tl.velocity_confidence)
        scv.tl.velocity_confidence(a)
        basis = 'umap' if 'X_umap' in a.obsm else 'pca'
        record('scvelo.tl.velocity_embedding', scv.tl.velocity_embedding, basis=basis)
        scv.tl.velocity_embedding(a, basis=basis)
        coords = np.asarray(a.obsm['X_' + basis][:, :2])
        result['embedding'] = coords.tolist()
        result['embedding_basis'] = basis
        result['embedding_name'] = ('UMAP' if basis == 'umap' else 'PCA') + ' 与速度投影（二维投影可能失真）'
        if 'velocity_' + basis in a.obsm:
            result['arrows'] = np.asarray(a.obsm['velocity_' + basis][:, :2]).tolist()
        for key in ('velocity_length', 'velocity_confidence', 'velocity_confidence_transition'):
            if key in a.obs:
                vals = np.asarray(a.obs[key], dtype=float)
                result['metrics'][key + '_median'] = float(np.nanmedian(vals))
        record('scvelo.tl.velocity_pseudotime', scv.tl.velocity_pseudotime)
        scv.tl.velocity_pseudotime(a)
        vpt = np.asarray(a.obs['velocity_pseudotime'], dtype=float)
        result['metrics']['velocity_pseudotime_reachable'] = int(np.isfinite(vpt).sum())
        if method == 'scvelo_dynamical':
            record('scvelo.tl.latent_time', scv.tl.latent_time)
            scv.tl.latent_time(a)
            result['time'] = [float(v) if np.isfinite(v) else None for v in a.obs['latent_time']]
            result['time_name'] = 'scVelo 潜在时间（模型内部时间，无实验标定）'
            result['metrics']['time_scale'] = 'latent time（无量纲）'
        else:
            result['time'] = [float(v) if np.isfinite(v) else None for v in vpt]
            result['time_name'] = '速度伪时序（基于速度图，无实验标定）'
            result['metrics']['time_scale'] = 'velocity pseudotime（无量纲）'
        columns = [c for c in ('fit_likelihood', 'fit_alpha', 'fit_beta', 'fit_gamma',
                               'velocity_r2', 'fit_t_', 'fit_steady_states') if c in a.var]
        table = a.var[columns].replace([np.inf, -np.inf], np.nan).copy() if columns else None
        if table is not None and 'fit_likelihood' in table:
            table = table.sort_values('fit_likelihood', ascending=False)
        if table is not None:
            table.insert(0, 'gene', table.index.astype(str))
            result['gene_table'] = json.loads(table.head(30).to_json(orient='records'))
        velocity = np.asarray(a.layers['velocity'].toarray() if sparse.issparse(a.layers['velocity'])
                              else a.layers['velocity'])
        result['velocity'] = 'velocity.npz'
        np.savez_compressed(folder / 'velocity.npz', velocity=velocity,
                            cells=np.asarray(a.obs_names, dtype=str),
                            genes=np.asarray(a.var_names, dtype=str))
        result['genes_hash'] = ids_hash(a.var_names)
        result['caveats'] += [
            '速度一致性是内部诊断：数值高不代表方向正确。',
            '基因排名是候选关联，不证明因果；高似然不等于“驱动基因”。',
            'stochastic 与 dynamical 的损失、似然与时间尺度不可直接比较。',
        ]
        result['evidence'].append('scVelo 真实调用（%s 模式，CPU）' % mode)
        log('scVelo ' + mode + ' 完成')

    elif method == 'cellrank':
        import cellrank as cr
        a, eff = _prep_expression(a, config, seed, record, log)
        result['effective'] = eff
        if int(a.n_obs) > 6000:
            raise ValueError('CellRank 的 GPCCA 分解需要把细胞×细胞矩阵稠密化；'
                             f'当前 {a.n_obs} 个细胞会占用约 {a.n_obs ** 2 * 8 / 1024 ** 3:.1f} GB 内存。'
                             '请先用受控子集运行，或改用 DPT + PAGA 观察方向。')
        t, _ = _diffmap_dpt(a, config, seed, record, log, config['root_cell'])
        cluster_key = _group_labels(a, config, record, log)
        record('cellrank.kernels.PseudotimeKernel', cr.kernels.PseudotimeKernel,
               time_key='dpt_pseudotime')
        kernel = cr.kernels.PseudotimeKernel(a, time_key='dpt_pseudotime')
        record('PseudotimeKernel.compute_transition_matrix', kernel.compute_transition_matrix,
               n_jobs=1, backend='threading', show_progress_bar=False)
        kernel.compute_transition_matrix(n_jobs=1, backend='threading', show_progress_bar=False)
        n_states = int(config.get('cellrank_n_states', 3))
        estimator = cr.estimators.GPCCA(kernel)
        n_comp = int(min(20, a.n_obs - 2))
        record('GPCCA.compute_schur', estimator.compute_schur, n_components=n_comp)
        estimator.compute_schur(n_components=n_comp)
        record('GPCCA.compute_macrostates', estimator.compute_macrostates,
               n_states=n_states, cluster_key=cluster_key)
        estimator.compute_macrostates(n_states=n_states, cluster_key=cluster_key)
        record('GPCCA.predict_terminal_states', estimator.predict_terminal_states)
        estimator.predict_terminal_states()
        record('GPCCA.compute_fate_probabilities', estimator.compute_fate_probabilities,
               use_petsc=False, n_jobs=1, backend='threading', show_progress_bar=False)
        estimator.compute_fate_probabilities(use_petsc=False, n_jobs=1, backend='threading',
                                             show_progress_bar=False)
        fp = estimator.fate_probabilities
        names = [str(x) for x in fp.names]
        probs = np.asarray(fp.X, dtype=float)
        terms = estimator.terminal_states
        term_counts = {str(k): int(v) for k, v in terms.value_counts().items()} if terms is not None else {}
        # 诊断：终末状态“落在拟时序早端”时，多半是图结构/吸收态造成的，
        # 不应被读成“细胞最终回到早期状态”。这里先算出来，报告里明确标注。
        term_stats, early_states = {}, []
        try:
            macro = np.asarray(estimator.macrostates.astype(str))
            finite = np.isfinite(t)
            median_t = float(np.nanmedian(t[finite])) if finite.any() else float('nan')
            for name in names:
                mask = (macro == name) & finite
                if mask.any():
                    term_stats[name] = dict(cells=int(mask.sum()),
                                            mean_time=float(np.nanmean(t[mask])))
                    if np.isfinite(median_t) and term_stats[name]['mean_time'] < median_t:
                        early_states.append(name)
        except Exception as exc:  # 诊断失败不影响主结果
            log('终末状态时序诊断未能完成：' + repr(exc))
        result['fate'] = dict(
            terminal_states=names,
            terminal_state_cells=term_counts,
            terminal_state_stats=term_stats,
            early_terminal_states=early_states,
            degenerate=len(names) < 2,
            probabilities=probs.tolist(),
            estimator='GPCCA',
            kernel='PseudotimeKernel(dpt_pseudotime)',
            cluster_key=cluster_key,
            n_states=n_states,
        )
        result['groups'] = dict(key=cluster_key, labels=a.obs[cluster_key].astype(str).tolist(),
                                sizes={str(k): int(v) for k, v in a.obs[cluster_key].value_counts().items()})
        result['metrics'].update({'n_terminal_states': len(names),
                                  'n_states': n_states,
                                  'mean_max_fate_probability': float(np.mean(probs.max(1)))})
        result['time'] = [float(v) if np.isfinite(v) else None for v in t]
        result['time_name'] = 'DPT 拟时序（CellRank 的输入排序，依赖起点）'
        coords, name, basis = _embedding(a, config)
        result['embedding'] = coords.tolist()
        result['embedding_name'] = (name + '（按主导命运概率着色）') if len(names) > 1 else (name + '（按命运概率着色）')
        result['embedding_basis'] = basis
        try:
            drivers = estimator.compute_lineage_drivers(use_raw=False, n_jobs=1)
            keep = [c for c in drivers.columns if c.endswith('_corr') or c.endswith('_pval')]
            drivers = drivers[keep].head(30).copy()
            drivers.insert(0, 'gene', drivers.index.astype(str))
            result['fate']['drivers'] = json.loads(drivers.to_json(orient='records'))
        except Exception as exc:  # 单一终末状态时 CellRank 自身无法给出驱动基因
            result['caveats'].append('未能计算命运驱动基因：' + type(exc).__name__ +
                                     '（通常是因为只识别到 1 个终末状态）')
            result['fate']['drivers'] = []
        if len(names) < 2:
            result['caveats'].append('只识别到 1 个终末状态：所有细胞的命运概率都接近 1，'
                                     '这张图没有区分度，只说明数据在模型下只有一条主方向。')
        if early_states:
            result['caveats'].append(
                '疑似“早端”终末状态：' + '、'.join(early_states) +
                '（其平均拟时序低于整体中位数）。这通常由细胞图结构或吸收态造成，'
                '不应解释为“细胞最终回到早期状态”；请人工核查终末状态是否符合实验预期。')
        result['caveats'] += [
            '终末状态由 GPCCA 在细胞图上识别，可能只是采样边界，需要生物学确认。',
            '命运概率是模型推断，不是已验证的谱系；概率高不等于事实。',
        ]
        result['evidence'].append('CellRank 2 PseudotimeKernel + GPCCA + 命运概率（CPU）')
        log('CellRank 命运概率完成：终末状态 ' + ','.join(names))

    elif method == 'velovi':
        a, eff = _velovi(a, config, record, log, folder, result)
        result['effective'] = eff
    elif method == 'regvelo':
        a = _regvelo_prep(a, config, seed, record, log)
        a = _regvelo(a, config, folder, log)
        result['effective'] = {'n_genes_after_preprocess': int(a.n_vars)}
        result['caveats'].append('RegVelo 0.4.2 CPU 接入未输出调控边；官方 inferred_grn 函数固定使用 CUDA。')
        if 'cellflow_regvelo_time' in a.obs:
            result['time'] = a.obs['cellflow_regvelo_time'].astype(float).tolist()
            result['time_name'] = 'RegVelo 模型内部共享相对时间（未实验标定）'
        result['evidence'].append('RegVelo 0.4.2 CPU（实验接入）')
    else:
        raise ValueError('未知方法：' + str(method))

    _write_anndata(a, folder / 'analysis.h5ad', log)
    return result


def _to_numpy(value):
    """把 DataFrame / ndarray / 稀疏矩阵统一成 numpy float 数组。"""
    if hasattr(value, 'to_numpy'):
        value = value.to_numpy()
    if sparse.issparse(value):
        value = value.toarray()
    return np.asarray(value, dtype=float)


def _velovi(a, config, record, log, folder, result):
    """veloVI（YosefLab，scvi-tools 官方实现）：变分推断，给出速度**和不确定性**。

    这不是本程序自写算法：模型、训练、推断全部调用 scvi-tools 官方的 VELOVI，
    我们只负责数据准备、把输出转成统一结果结构、以及把限制写清楚。

    兼容性：scvi-tools 1.2 起 VELOVI 在 `scvi.external`；更早版本在 `scvi.model`。
    """
    try:
        import scvi
        try:
            from scvi.external import VELOVI          # scvi-tools >= 1.2
        except ImportError:                            # 老版本
            from scvi.model import VELOVI
    except Exception as exc:  # 隔离环境没装好时给出可操作提示
        raise ValueError('当前解释器没有 scvi-tools，veloVI 需要独立环境 `.venv-velovi`（见 README）。'
                         '原始错误：' + repr(exc)) from exc

    import scanpy as sc
    import scvelo as scv

    device = device_mod.resolve(config.get('device', 'auto'))
    accelerator = 'gpu' if device.device == 'cuda' else 'cpu'
    seed = int(config['seed'])
    try:
        scvi.settings.seed = seed
    except Exception:
        pass

    # 预处理：X 用于选高变基因/降维（需要归一化 + log1p）；
    # **不能动 layers 里的 spliced/unspliced**——VELOVI 需要原始计数自己估计尺寸因子。
    from .data import x_kind
    record('scvelo.pp.filter_genes', scv.pp.filter_genes, min_shared_counts=config['min_shared_counts'])
    scv.pp.filter_genes(a, min_shared_counts=config['min_shared_counts'])
    kind = x_kind(a.X)
    if kind == 'counts':
        sc.pp.normalize_total(a, target_sum=1e4)
        sc.pp.log1p(a)
        log('veloVI 预处理：X 按原始计数归一化 + log1p（layers 保持原始计数）')
    elif kind == 'scaled':
        sc.pp.log1p(a)
        log('veloVI 预处理：X 已缩放，只取对数（layers 保持原始计数）')
    else:
        log('veloVI 预处理：X 已是 log 尺度，直接用于选高变基因')

    n_top = int(min(config['n_top_genes'], a.n_vars))
    record('scanpy.pp.highly_variable_genes', sc.pp.highly_variable_genes,
           n_top_genes=n_top, flavor='seurat')
    sc.pp.highly_variable_genes(a, n_top_genes=n_top, flavor='seurat')
    a = a[:, a.var['highly_variable']].copy()
    if a.n_vars < 10:
        raise ValueError('高变基因过滤后不足 10 个基因；请检查数据或调大 n_top_genes')
    n_pcs = int(min(config['n_pcs'], a.n_vars - 1, a.n_obs - 1))
    sc.pp.pca(a, n_comps=n_pcs, random_state=seed)
    k = int(min(config['n_neighbors'], a.n_obs - 1))
    sc.pp.neighbors(a, n_neighbors=k, n_pcs=n_pcs, random_state=seed)
    if config.get('use_umap') and a.n_obs >= 50:
        sc.tl.umap(a, random_state=seed)

    log(f'veloVI：{a.n_obs} 细胞 × {a.n_vars} 基因，设备 {device.device}（accelerator={accelerator}）')
    record('scvi.model.VELOVI.setup_anndata', VELOVI.setup_anndata,
           spliced_layer='spliced', unspliced_layer='unspliced')
    VELOVI.setup_anndata(a, spliced_layer='spliced', unspliced_layer='unspliced')

    n_latent = int(min(10, max(2, a.n_obs - 1)))
    record('scvi.model.VELOVI', VELOVI, n_hidden=256, n_latent=n_latent, n_layers=2, dropout_rate=0.1)
    model = VELOVI(a, n_hidden=256, n_latent=n_latent, n_layers=2, dropout_rate=0.1)

    max_epochs = int(config.get('max_epochs') or 100)
    train_kwargs = dict(max_epochs=max_epochs, accelerator=accelerator, devices=1,
                        early_stopping=True, enable_progress_bar=False)
    record('VELOVI.train', model.train, **train_kwargs)
    try:
        model.train(**train_kwargs)
    except TypeError:
        # 不同 scvi-tools 小版本的参数名有差异，去掉可选参数重试
        train_kwargs.pop('enable_progress_bar', None)
        model.train(**train_kwargs)

    try:
        velocity = _to_numpy(model.get_velocity(return_numpy=True))
    except TypeError:
        velocity = _to_numpy(model.get_velocity())
    if velocity.shape[0] != a.n_obs:
        velocity = velocity.T
    a.layers['velocity'] = velocity

    latent_time = None
    try:
        try:
            latent_time = _to_numpy(model.get_latent_time(return_numpy=True))
        except TypeError:
            latent_time = _to_numpy(model.get_latent_time())
    except Exception as exc:
        log('veloVI 未能取得潜在时间：' + repr(exc))

    # 不确定性 = 速度后验样本的标准差（每个细胞 × 每个基因）。
    # 说明：scvi-tools 里 get_directional_uncertainty 返回的是「细胞 × 细胞类型」的方向一致性，
    # 不是逐基因方差，所以这里直接用官方 get_velocity 的多后验采样接口，语义更清楚。
    uncertainty = None
    n_post = 20
    try:
        samples = model.get_velocity(n_samples=n_post, return_mean=False, return_numpy=True)
        samples = np.asarray(samples, dtype=float)
        if samples.ndim == 3:
            if samples.shape[1] == a.n_obs:
                uncertainty = samples.std(axis=0)
            else:
                uncertainty = np.transpose(samples, (1, 0, 2)).std(axis=0)
        elif samples.ndim == 2 and samples.shape[0] == a.n_obs:
            uncertainty = np.zeros_like(samples)  # 只拿到均值时明确记为 0 方差
    except Exception as exc:
        log('veloVI 未取得后验不确定性（不影响速度本身）：' + repr(exc))

    # 与 scVelo 相同的下游：速度图、置信度、二维投影、速度伪时序。
    # scVelo 的置信度函数需要平滑层 Ms/Mu，这里补算一次（只算平滑层，不动 velocity）。
    try:
        record('scvelo.pp.moments', scv.pp.moments, n_pcs=None, n_neighbors=None)
        scv.pp.moments(a, n_pcs=None, n_neighbors=None)
    except Exception as exc:
        log('补算平滑层 Ms/Mu 失败（部分诊断会缺失）：' + repr(exc))
    record('scvelo.tl.velocity_graph', scv.tl.velocity_graph, n_jobs=1, show_progress_bar=False)
    scv.tl.velocity_graph(a, n_jobs=1, show_progress_bar=False)
    try:
        record('scvelo.tl.velocity_confidence', scv.tl.velocity_confidence)
        scv.tl.velocity_confidence(a)
    except Exception as exc:
        log('速度置信度计算跳过（不影响速度矩阵本身）：' + repr(exc))
    basis = 'umap' if 'X_umap' in a.obsm else 'pca'
    record('scvelo.tl.velocity_embedding', scv.tl.velocity_embedding, basis=basis)
    scv.tl.velocity_embedding(a, basis=basis)
    try:
        record('scvelo.tl.velocity_pseudotime', scv.tl.velocity_pseudotime)
        scv.tl.velocity_pseudotime(a)
    except Exception as exc:
        log('veloVI 速度伪时序不可用：' + repr(exc))

    result['velocity'] = 'velocity.npz'
    np.savez_compressed(folder / 'velocity.npz', velocity=np.asarray(velocity),
                        cells=np.asarray(a.obs_names, dtype=str),
                        genes=np.asarray(a.var_names, dtype=str))
    if uncertainty is not None:
        np.savez_compressed(folder / 'velocity_uncertainty.npz', std=np.asarray(uncertainty),
                            cells=np.asarray(a.obs_names, dtype=str),
                            genes=np.asarray(a.var_names, dtype=str))
        result['uncertainty'] = 'velocity_uncertainty.npz'
        result['metrics']['velocity_std_median'] = float(np.nanmedian(uncertainty))
    result['embedding'] = np.asarray(a.obsm['X_' + basis][:, :2]).tolist()
    result['embedding_basis'] = basis
    result['embedding_name'] = ('UMAP' if basis == 'umap' else 'PCA') + ' 与 veloVI 速度投影（二维投影可能失真）'
    if 'velocity_' + basis in a.obsm:
        result['arrows'] = np.asarray(a.obsm['velocity_' + basis][:, :2]).tolist()
    for key in ('velocity_length', 'velocity_confidence', 'velocity_confidence_transition'):
        if key in a.obs:
            result['metrics'][key + '_median'] = float(np.nanmedian(np.asarray(a.obs[key], dtype=float)))
    if latent_time is not None and np.size(latent_time):
        latent = np.asarray(latent_time, dtype=float)
        if latent.ndim == 2:
            # veloVI 的潜在时间是「细胞 × 基因」，这里按基因取平均得到每个细胞的相对时间
            per_cell = np.nanmean(latent, axis=1) if latent.shape[0] == a.n_obs else np.nanmean(latent, axis=0)
            np.savez_compressed(folder / 'latent_time.npz', latent_time=latent,
                                cells=np.asarray(a.obs_names, dtype=str),
                                genes=np.asarray(a.var_names, dtype=str))
            result['latent_time'] = 'latent_time.npz'
            result['time_name'] = 'veloVI 潜在时间（按基因平均到每个细胞；模型内部时间，无实验标定）'
        else:
            per_cell = latent
            result['time_name'] = 'veloVI 潜在时间（模型内部时间，无实验标定）'
        result['time'] = [float(v) if np.isfinite(v) else None for v in np.ravel(per_cell)]
    elif 'velocity_pseudotime' in a.obs:
        result['time'] = [float(v) if np.isfinite(v) else None
                          for v in np.asarray(a.obs['velocity_pseudotime'], dtype=float)]
        result['time_name'] = '速度伪时序（基于速度图，无实验标定）'

    # 候选基因：按速度绝对值均值排序（描述性，不做因果声明）
    mean_abs = np.nanmean(np.abs(velocity), axis=0)
    order = np.argsort(mean_abs)[::-1][:30]
    result['gene_table'] = [dict(gene=str(a.var_names[i]), mean_abs_velocity=float(mean_abs[i]))
                            for i in order if np.isfinite(mean_abs[i])]
    result['metrics']['n_genes_used'] = int(a.n_vars)
    result['metrics']['max_epochs'] = max_epochs
    result['metrics']['accelerator'] = accelerator
    result['results_device'] = device.device

    try:
        model.save(str(folder / 'velovi_model'), overwrite=True)
        result['evidence'].append('veloVI 模型检查点：velovi_model/')
    except Exception as exc:
        log('veloVI 模型保存失败：' + repr(exc))

    result['caveats'] += [
        'veloVI 是变分推断：给出的是后验近似，不是确定值；把它与 scVelo 的方向差当作模型不确定性来看，而不是“谁对谁错”。',
        '训练轮数有限（本次 max_epochs=%d），未做收敛性评估；真实数据建议增加到收敛或看损失曲线。' % max_epochs,
        '潜在时间同样是模型内部尺度，不是真实小时数；候选基因是描述性排序，不证明因果。',
    ]
    result['evidence'].append('veloVI（scvi-tools %s，%s）' % (
        getattr(scvi, '__version__', 'unknown'), accelerator))
    log('veloVI 完成：速度矩阵 + 潜在时间' + ('+ 不确定性' if uncertainty is not None else ''))
    return a, dict(n_hvg=int(a.n_vars), n_latent=n_latent, max_epochs=max_epochs,
                   accelerator=accelerator, device=device.device)


def _regvelo_prep(a, config, seed, record, log):
    import scanpy as sc
    import scvelo as scv
    record('scvelo.pp.filter_genes', scv.pp.filter_genes, min_shared_counts=config['min_shared_counts'])
    scv.pp.filter_genes(a, min_shared_counts=config['min_shared_counts'])
    record('scvelo.pp.normalize_per_cell', scv.pp.normalize_per_cell,
           counts_per_cell_after=1e4, layers=['spliced', 'unspliced'], enforce=True)
    scv.pp.normalize_per_cell(a, counts_per_cell_after=1e4, layers=['spliced', 'unspliced'], enforce=True)
    sc.pp.log1p(a)
    n_pcs = int(min(config['n_pcs'], a.n_vars - 1, a.n_obs - 1))
    sc.pp.pca(a, n_comps=n_pcs, random_state=seed)
    k = int(min(config['n_neighbors'], a.n_obs - 1))
    sc.pp.neighbors(a, n_neighbors=k, n_pcs=n_pcs, random_state=seed)
    scv.pp.moments(a, n_pcs=None, n_neighbors=None)
    return a


def _regvelo(a, config, folder, log):
    import importlib.metadata
    import pandas as pd
    import regvelo as rgv
    import torch
    import scvi
    if importlib.metadata.version('regvelo') != '0.4.2':
        raise ValueError('仅支持经过接口检查的 RegVelo 0.4.2')
    scvi.settings.seed = config['seed']
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True, warn_only=True)
    grn = pd.read_csv(config['grn_path'], index_col=0)
    if grn.index.has_duplicates or grn.columns.has_duplicates or not np.isin(grn.to_numpy(), [0, 1]).all():
        raise ValueError('GRN 必须为 TF 行 × target 列的唯一基因 ID 二元矩阵')
    needed = ['REGVELOVI']
    if any(not hasattr(rgv, k) for k in needed):
        raise ValueError('RegVelo API 不匹配；停止而非猜测替代调用')
    if not hasattr(rgv, 'pp') or not hasattr(rgv.pp, 'preprocess_data') or not hasattr(rgv.pp, 'set_prior_grn'):
        raise ValueError('该发行版没有官方文档中的预处理 API，需版本专项适配；停止运行')
    a2 = rgv.pp.preprocess_data(a)
    if a2.n_vars < 10 or a2.n_vars > 3000:
        raise ValueError('RegVelo 预处理后需要 10–3000 个基因（内存保护）')
    aligned = grn.reindex(index=a2.var_names, columns=a2.var_names, fill_value=0)
    coverage = int((aligned.sum(0) > 0).sum()) / a2.n_vars
    if coverage < .1 or int(aligned.to_numpy().sum()) == 0:
        raise ValueError('过滤后 GRN 目标基因覆盖不足 10% 或没有边；这是保守门槛，需核查物种和 ID')
    a2 = rgv.pp.set_prior_grn(a2, aligned.T)
    if a2.n_vars < 10:
        raise ValueError('GRN 对齐和边过滤后不足 10 个基因；停止训练')
    tf = a2.var_names[np.asarray(a2.uns['skeleton']).sum(1) != 0]
    W = torch.tensor(np.asarray(a2.uns['skeleton']).T, dtype=torch.int)
    rgv.REGVELOVI.setup_anndata(a2, spliced_layer='Ms', unspliced_layer='Mu')
    model = rgv.REGVELOVI(a2, W=W, regulators=tf)
    # 训练设备跟随用户设置：有 CUDA 就用 GPU，否则 CPU（不再写死）
    _dev = device_mod.resolve(config.get('device', 'auto'))
    _accel = 'gpu' if _dev.device == 'cuda' else 'cpu'
    log(f'RegVelo 训练设备：{_dev.device}（accelerator={_accel}）')
    model.train(max_epochs=config['max_epochs'], accelerator=_accel, devices=1)
    model.save(str(folder / 'regvelo_model'), overwrite=False, save_anndata=True)
    if not hasattr(rgv, 'tl') or not hasattr(rgv.tl, 'set_output'):
        raise ValueError('RegVelo 结果写回接口不匹配；模型检查点已保留')
    rgv.tl.set_output(a2, model, n_samples=30, batch_size=min(128, a2.n_obs))
    a2.obs['cellflow_regvelo_time'] = model.compute_shared_time(np.asarray(a2.layers['latent_time_velovi']).copy())
    (folder / 'regvelo_calls.json').write_text(json.dumps(dict(
        version='0.4.2', setup_layers=['Ms', 'Mu'], prior_input_orientation='TF x target',
        model_W_orientation='target x TF', device='cpu', max_epochs=config['max_epochs'],
        train_signature=str(inspect.signature(model.train)), posterior_samples=30,
        batch_size=min(128, a2.n_obs), grn_source=config['grn_source']), ensure_ascii=False, indent=2),
        encoding='utf-8')
    a2.uns['cellflow_regulatory_interpretation'] = (
        'CPU adapter does not infer GRN edges: official inferred_grn in 0.4.2 hardcodes cuda:0; '
        'no causal edge claims.')
    log('RegVelo 训练完成；调控关系解释仍需独立验证')
    return a2
