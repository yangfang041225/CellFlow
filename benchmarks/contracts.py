"""真实数据输入契约：只做检查与报告，不修改数据。"""
from __future__ import annotations

from pathlib import Path
import json

import numpy as np


def _matrix_stats(matrix):
    from scipy import sparse
    values = matrix.data if sparse.issparse(matrix) else np.asarray(matrix).ravel()
    values = np.asarray(values, dtype=float)
    return {
        'sparse': bool(sparse.issparse(matrix)),
        'dtype': str(getattr(matrix, 'dtype', '')),
        'finite': bool(np.isfinite(values).all()),
        'nonnegative': bool((values >= 0).all()) if values.size else True,
        'min': float(values.min()) if values.size else 0.0,
        'max': float(values.max()) if values.size else 0.0,
    }


def validate_adata(adata, require_splicing=False, expected_shape=None) -> dict:
    """返回契约报告；`ok` 仅表示硬性条件满足，不代表生物学可信。"""
    issues, warnings = [], []
    shape = (int(adata.n_obs), int(adata.n_vars))
    if expected_shape and tuple(expected_shape) != shape:
        issues.append(f'形状不符：{shape} != {tuple(expected_shape)}')
    if shape[0] == 0 or shape[1] == 0:
        issues.append('空矩阵')
    obs_index = adata.obs_names.astype(str)
    var_index = adata.var_names.astype(str)
    duplicate_cells = int(len(obs_index) - len(set(obs_index)))
    duplicate_genes = int(len(var_index) - len(set(var_index)))
    if duplicate_cells:
        issues.append(f'重复细胞 ID：{duplicate_cells}')
    if duplicate_genes:
        issues.append(f'重复基因名：{duplicate_genes}')
    if np.asarray(obs_index).tolist().count('') or np.asarray(var_index).tolist().count(''):
        issues.append('存在空标识符')

    matrices = {'X': _matrix_stats(adata.X)}
    for key in ('spliced', 'unspliced'):
        if key in adata.layers:
            matrices[key] = _matrix_stats(adata.layers[key])
    for key, stats in matrices.items():
        if not stats['finite']:
            issues.append(f'{key} 含非有限值')
        if not stats['nonnegative']:
            (issues if require_splicing and key in ('spliced', 'unspliced') else warnings).append(
                f'{key} 含负值（计数层应为非负）')
    if require_splicing and not {'spliced', 'unspliced'} <= set(adata.layers):
        issues.append('缺少 spliced/unspliced 层，不能做 RNA velocity')

    alignment = {}
    for key in ('X_pca', 'X_umap'):
        if key in adata.obsm:
            shape_ok = int(adata.obsm[key].shape[0]) == shape[0]
            alignment[key] = {'shape': list(adata.obsm[key].shape), 'aligned': shape_ok}
            if not shape_ok:
                issues.append(f'{key} 与细胞数不一致')
    for key in ('Ms', 'Mu'):
        if key in adata.layers:
            if adata.layers[key].shape != adata.X.shape:
                issues.append(f'{key} 形状与 X 不一致')

    return {
        'shape': list(shape),
        'obs_columns': list(map(str, adata.obs.columns)),
        'var_columns': list(map(str, adata.var.columns)),
        'layers': list(map(str, adata.layers.keys())),
        'obsm': list(map(str, adata.obsm.keys())),
        'matrices': matrices,
        'duplicate_cells': duplicate_cells,
        'duplicate_genes': duplicate_genes,
        'alignment': alignment,
        'issues': issues,
        'warnings': warnings,
        'ok': not issues,
        'note': '契约通过只说明输入结构可用，不代表速度或谱系结论正确。',
    }


def validate_file(path, require_splicing=False, expected_shape=None) -> dict:
    import anndata as ad
    adata = ad.read_h5ad(Path(path))
    report = validate_adata(adata, require_splicing=require_splicing,
                            expected_shape=expected_shape)
    report['file'] = str(path)
    return report
