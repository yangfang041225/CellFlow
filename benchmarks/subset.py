"""确定性分层子集：不改变表达值，只选细胞。"""
from __future__ import annotations

from pathlib import Path
import json

import numpy as np


def stratified_subset(adata, group_key='clusters', per_group=100, seed=42):
    if group_key not in adata.obs:
        raise KeyError(f'缺少分组列 {group_key}')
    if not isinstance(per_group, int) or isinstance(per_group, bool) or per_group < 1:
        raise ValueError('per_group 必须为正整数')
    if not adata.obs_names.is_unique or not adata.var_names.is_unique:
        raise ValueError('子集要求唯一细胞/基因 ID')
    if adata.obs[group_key].isna().any():
        raise ValueError('分层类别含空值；请明确类别含义')
    labels = adata.obs[group_key].astype(str).to_numpy()
    rng = np.random.default_rng(seed)
    picked = []
    for label in sorted(set(labels)):
        index = np.flatnonzero(labels == label)
        take = min(len(index), int(per_group))
        picked.extend(rng.choice(index, take, replace=False).tolist())
    picked = np.sort(np.asarray(picked, dtype=int))
    subset = adata[picked].copy()
    subset.uns['cellflow_subset'] = {
        'group_key': group_key,
        'per_group': int(per_group),
        'seed': int(seed),
        'cells': int(subset.n_obs),
        'note': '确定性分层抽样；表达值未改，仅用于计算与对照。',
        'ordering': 'sorted category labels, seeded sampling of source row indices, output source order; source order affects selection',
    }
    return subset


def build_subset(source, target, group_key='clusters', per_group=100, seed=42):
    import anndata as ad
    adata = ad.read_h5ad(Path(source))
    subset = stratified_subset(adata, group_key=group_key, per_group=per_group, seed=seed)
    if Path(target).exists():
        raise FileExistsError('拒绝覆盖已有子集：' + str(target))
    subset.write_h5ad(Path(target))
    meta = dict(subset.uns['cellflow_subset'])
    meta['source'] = str(source)
    meta['target'] = str(target)
    meta['groups'] = {str(k): int(v) for k, v in subset.obs[group_key].value_counts().items()}
    from .fetch import sha256_file
    meta.update(source_sha256=sha256_file(Path(source)), target_sha256=sha256_file(Path(target)),
                cells=list(map(str, subset.obs_names)), genes=list(map(str, subset.var_names)))
    (Path(target).with_name(Path(target).name + '.subset.json')).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    return meta
