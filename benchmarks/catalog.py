"""Phase II 科学验证：数据目录与固定版本记录。

大型文件只落在 `data/benchmarks/`（gitignore），不进入仓库。
"""
from __future__ import annotations

PANCREAS = {
    'key': 'scvelo_pancreas_day15',
    'title': 'Pancreatic endocrinogenesis (E15.5)',
    'source': 'scVelo curated dataset (theislab/scvelo_notebooks)',
    'url': 'https://github.com/theislab/scvelo_notebooks/raw/master/data/Pancreas/endocrinogenesis_day15.h5ad',
    'citation': 'Bastidas-Ponce et al. 2019, Developmental Cell',
    'license_note': 'Public research dataset; redistributed by scVelo notebooks. Not committed to GitHub.',
    'sha256': '9e3e459eca00ba06b496ec80def32941b5b2889918720e3e7aa6ffb811fbe7c6',
    'sha256_origin': 'project-pinned local download digest; no upstream checksum attestation',
    'raw_count_identity': 'S/U source provenance requires upstream confirmation; dtype/nonnegative/integer-like values are insufficient',
    'bytes': 52477898,
    'expected_shape': (3696, 27998),
    'layers': ('spliced', 'unspliced'),
}

SYNTHETIC_ENGINEERING = {
    'key': 'synthetic_engineering',
    'title': '内置模拟数据（240 细胞 × 120 虚构基因）',
    'source': 'examples/synthetic.npz',
    'citation': 'CellFlow engineering fixture; not biological',
    'license_note': 'Repository file',
    'sha256': None,
    'bytes': None,
    'expected_shape': (240, 120),
    'layers': ('spliced', 'unspliced'),
}

DATASETS = {item['key']: item for item in (PANCREAS, SYNTHETIC_ENGINEERING)}


def get(key):
    if key not in DATASETS:
        raise KeyError(f'未知数据集：{key}；可选：{sorted(DATASETS)}')
    return DATASETS[key]
