"""统一导入入口：用户不需要关心文件格式与矩阵方向。

支持：
- `.h5ad`（AnnData，原生）
- `.loom`（velocyto / scanpy 常见，自带 spliced/unspliced 更好）
- `.h5`（10x Cell Ranger 的 h5）
- 10x 三件套目录或压缩包（matrix.mtx + barcodes + features）
- `.csv / .tsv / .txt`（含 gz）：常见 GEO 表达表
- 上述任意格式打成的 `.zip / .tar.gz`
- 一个目录：自动在目录里找可读的矩阵

**矩阵方向由程序判断**：用户拿到的 CSV 可能是「细胞 × 基因」也可能是「基因 × 细胞」，
h5ad 也可能被转置过。这里用「基因名 vs 细胞条码」的特征做判断，必要时自动转置，
并把做了什么写进 `Dataset.extras['import']`，最终在报告里可见。

内部统一使用 cellflow.data.Dataset（cells × genes），所以下游方法完全不用改动。
"""
from __future__ import annotations

import gzip
import re
import shutil
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import numpy as np

from .data import Dataset, values

TEXT_SUFFIXES = ('.csv', '.tsv', '.txt')
ARCHIVE_SUFFIXES = ('.zip', '.tar', '.tar.gz', '.tgz')
SUPPORTED_SUFFIXES = ('.h5ad', '.loom', '.h5', '.mtx', '.gz') + TEXT_SUFFIXES + ARCHIVE_SUFFIXES

# 典型细胞条码：10x 的 16 个 ACGT + "-1"，或很长的随机串
_BARCODE = re.compile(r'^[ACGTN]{10,}[-_]?\d*$')
_GENE_ID = re.compile(r'^(ENS[A-Z]*G\d+|ENST\d+)$')
_GENE_SYMBOL = re.compile(r'^[A-Za-z][A-Za-z0-9\.\-_]{0,19}$')
_MT_GENE = re.compile(r'^(MT-|MT\.|MT_)')


@dataclass
class ImportResult:
    dataset: Dataset
    kind: str                     # h5ad / loom / 10x-h5 / 10x-mtx / table / archive / directory
    transposed: bool = False
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------- 名称判断
def _is_cell_like(name: str) -> bool:
    text = str(name).strip()
    if not text:
        return False
    if _BARCODE.match(text.upper()):
        return True
    return len(text) >= 12 and sum(ch.isdigit() for ch in text) >= 2


def _is_gene_like(name: str) -> bool:
    text = str(name).strip()
    if not text:
        return False
    if _GENE_ID.match(text.upper()):
        return True
    if _MT_GENE.match(text.upper()):
        return True
    if not _GENE_SYMBOL.match(text):
        return False
    # 基因名通常不含大量数字，也不会带分隔的长哈希
    if len(text) > 22:
        return False
    return not _is_cell_like(text)


def _axes_kind(names) -> tuple:
    sample = [str(x) for x in list(names)[:2000]]
    if not sample:
        return 0.0, 0.0
    gene = sum(1 for x in sample if _is_gene_like(x)) / len(sample)
    cell = sum(1 for x in sample if _is_cell_like(x)) / len(sample)
    return gene, cell


def looks_transposed(obs_names, var_names) -> Optional[bool]:
    """判断 AnnData 是否是「基因 × 细胞」（需要转置）。

    True  = 行是基因、列是细胞 → 需要转置
    False = 行是细胞、列是基因 → 正常
    None  = 名字看不出，交给形状判断
    """
    obs_gene, obs_cell = _axes_kind(obs_names)
    var_gene, var_cell = _axes_kind(var_names)
    if var_cell - var_gene > 0.3 and obs_gene - obs_cell > 0.3:
        return True
    if obs_cell - obs_gene > 0.3 and var_gene - var_cell > 0.3:
        return False
    return None


def _shape_hint(n_rows: int, n_cols: int) -> bool:
    """名字看不出方向时的兜底：基因数通常远多于细胞数（10k–30k vs 几百–几万）。"""
    return n_rows < n_cols and n_cols >= 5000


def _to_dataset(adata, notes: List[str], kind: str, trust_axes: bool = False) -> ImportResult:
    """AnnData → Dataset，并处理方向、层与稀疏化。"""
    import anndata as ad

    transposed = False
    if trust_axes:
        # AnnData 的 obs/var 是权威轴，不能再用名字/形状猜测转置
        notes.append('按 AnnData 的 obs/var 原样读取（不自动转置）')
        return _finish_dataset(adata, notes, kind, False)
    verdict = looks_transposed(adata.obs_names, adata.var_names)
    if verdict is None:
        transposed = _shape_hint(adata.n_obs, adata.n_vars)
    else:
        transposed = verdict
    if transposed:
        adata = adata.T.copy()
        notes.append('检测到矩阵是“基因 × 细胞”（或行列反了），已自动转成“细胞 × 基因”。')

    return _finish_dataset(adata, notes, kind, transposed)


def _finish_dataset(adata, notes, kind, transposed):
    # 只保留下游需要的层，并统一成稀疏
    from scipy import sparse
    layers = {}
    for key in ('spliced', 'unspliced'):
        if key in adata.layers:
            matrix = adata.layers[key]
            layers[key] = sparse.csr_matrix(matrix) if not sparse.issparse(matrix) else matrix
    X = adata.X
    if not sparse.issparse(X):
        X = sparse.csr_matrix(np.asarray(X))
    obs = {}
    for key in adata.obs.columns:
        try:
            obs[str(key)] = adata.obs[key].to_numpy()
        except Exception:
            continue
    extra = dict(adata.uns.get('cellflow_public') or {})
    extras = {'import': dict(kind=kind, transposed=transposed, notes=notes,
                             n_obs=int(adata.n_obs), n_vars=int(adata.n_vars)),
              'public': extra, 'obsm': list(adata.obsm.keys()),
              'layers_all': list(adata.layers.keys())}
    synthetic = bool(adata.uns.get('cellflow_synthetic', False))
    dataset = Dataset(X, adata.obs_names.to_numpy(dtype=str), adata.var_names.to_numpy(dtype=str),
                      layers, obs, synthetic, extras)
    return ImportResult(dataset=dataset, kind=kind, transposed=transposed, notes=notes)


# ---------------------------------------------------------------- 各类读取
def from_h5ad(path: Path) -> ImportResult:
    import anndata as ad
    notes = []
    adata = ad.read_h5ad(path)
    return _to_dataset(adata, notes, 'h5ad', trust_axes=True)


def from_loom(path: Path) -> ImportResult:
    """loom：genes × cells；如果带 spliced/unspliced（velocyto 输出）就一起保留。"""
    import anndata as ad
    import loompy
    notes = ['按 loom 约定（基因 × 细胞）读取']
    with loompy.connect(str(path), validate=False) as handle:
        matrix = handle.sparse().T.tocsr()  # cells × genes
        genes = np.asarray(handle.ra['Gene'], dtype=str) if 'Gene' in handle.ra else \
            np.asarray(handle.ra[handle.ra.keys()[0]], dtype=str)
        cells = np.asarray(handle.ca['CellID'], dtype=str) if 'CellID' in handle.ca else \
            np.asarray(handle.ca[handle.ca.keys()[0]], dtype=str)
        layers = {}
        for key in ('spliced', 'unspliced'):
            if key in handle.layers.keys():
                layers[key] = handle.layers[key].T.tocsr()
        obs_frame = None
    adata = ad.AnnData(X=matrix)
    adata.obs_names = cells
    adata.var_names = genes
    for key, value in layers.items():
        adata.layers[key] = value
    result = _to_dataset(adata, notes, 'loom')
    if 'spliced' in result.dataset.layers:
        result.notes.append('loom 中检测到 spliced/unspliced 层（可能是 velocyto 输出）')
    return result


def from_10x_h5(path: Path) -> ImportResult:
    import scanpy as sc
    notes = ['按 10x Cell Ranger 的 .h5 读取']
    adata = sc.read_10x_h5(str(path))
    return _to_dataset(adata, notes, '10x-h5')


def _header_extra_duplicates(path: Path, sep: str) -> int:
    """pandas 会把重复列名改成 g1.1，因此在读取表头时先统计原始重复。"""
    import csv
    try:
        with open(path, encoding='utf-8', errors='replace') as handle:
            first = handle.readline()
        fields = next(csv.reader([first], delimiter=sep))[1:]
    except Exception:
        return 0
    seen = {}
    for name in fields:
        seen[name] = seen.get(name, 0) + 1
    return sum(count - 1 for count in seen.values() if count > 1)


def _table_metadata_columns(frame):
    # Metadata is explicit: a damaged expression column must never disappear as obs.
    metadata = {'group', 'cluster', 'batch', 'sample', 'cell_type', 'celltype',
                'condition', 'donor', 'barcode', 'cell_id', 'label', 'cell', 'time',
                'assigned_cluster'}
    return [c for c in frame.columns if str(c).lower() in metadata]


def _strict_expression(frame):
    import pandas as pd
    numeric = frame.apply(pd.to_numeric, errors='coerce')
    values = numeric.to_numpy(dtype=np.float32)
    invalid = ~np.isfinite(values)
    if invalid.any():
        row, col = np.argwhere(invalid)[0]
        raise ValueError(f'表达矩阵包含缺失/非数值/非有限值：行 {frame.index[row]}，列 {frame.columns[col]}；默认拒绝，请修正源文件。')
    return numeric


def _from_table_streaming(path: Path, sep: str, chunk_rows: int = 4000, flipped=None) -> ImportResult:
    """大表分块读取：逐块转 float32 稀疏再 vstack，避免整表 DataFrame + 稠密矩阵双份峰值。"""
    import anndata as ad
    import pandas as pd
    from scipy import sparse

    from .memory import estimate
    info = estimate(path)

    head = pd.read_csv(path, sep=sep, index_col=0, nrows=5)
    meta_cols = _table_metadata_columns(head)
    numeric_cols = [c for c in head.columns if c not in meta_cols]

    gene_rows, cell_rows = _axes_kind(head.index)
    gene_cols, cell_cols = _axes_kind(head.columns)
    if flipped is None:
        if cell_cols - gene_cols > 0.3 and gene_rows - cell_rows > 0.3:
            flipped = True
        elif gene_cols - cell_cols > 0.3 and cell_rows - gene_rows > 0.3:
            flipped = False
        else:
            flipped = _shape_hint(head.shape[0], head.shape[1])

    header_duplicates = _header_extra_duplicates(path, sep)
    notes = [f'按表格分块读取（分隔符 {"逗号" if sep == "," else "制表符"}，每块 {chunk_rows} 行）',
             f'文件 {info["file_mb"]} MB，按经验估算峰值约 {info["gb"]} GB（读取前已做内存检查）']
    if header_duplicates:
        notes.append(f'表头有 {header_duplicates} 个重复列名，pandas 已加后缀区分（原始名见文件）')
    if flipped:
        notes.append('原表是“基因 × 细胞”，已逐块转置为“细胞 × 基因”。')
        if meta_cols:
            notes.append(f'转置方向下忽略了 {len(meta_cols)} 个非数值列（它们描述的是基因轴）')

    blocks, cells, genes, metas = [], [], None, []
    missing_total = 0
    with pd.read_csv(path, sep=sep, index_col=0, chunksize=chunk_rows, low_memory=False) as reader:
        for chunk in reader:
            if flipped:
                numeric = _strict_expression(chunk)
                missing_total += int(numeric.isna().sum().sum())
                dense = numeric.to_numpy(dtype=np.float32).T
                if not cells:
                    cells = [str(name) for name in chunk.columns]
                genes = (genes or []) + [str(name) for name in chunk.index]
            else:
                if genes is None:
                    genes = [str(name) for name in numeric_cols]
                numeric = _strict_expression(chunk[numeric_cols])
                missing_total += int(numeric.isna().sum().sum())
                dense = numeric.to_numpy(dtype=np.float32)
                cells.extend(str(name) for name in chunk.index)
                if meta_cols:
                    metas.append(chunk[meta_cols])
            blocks.append(sparse.csr_matrix(dense))

    if not blocks:
        raise ValueError('表格没有读到任何数据行')
    matrix = (sparse.hstack(blocks) if flipped else sparse.vstack(blocks)).tocsr()
    if matrix.shape[0] != len(cells) or matrix.shape[1] != len(genes or []):
        raise ValueError(f'分块读取后维度不一致：{matrix.shape} vs {len(cells)} 细胞 / {len(genes or [])} 基因')
    if matrix.shape[1] < 5:
        raise ValueError('表格里没找到足够的数值列（基因数 < 5）；请确认表格是表达矩阵')

    adata = ad.AnnData(X=matrix,
                       obs=pd.DataFrame(index=pd.Index(cells, dtype=str)),
                       var=pd.DataFrame(index=pd.Index(genes, dtype=str)))
    if metas and not flipped:
        meta = pd.concat(metas)
        for column in meta.columns:
            adata.obs[str(column)] = meta[column].astype(object).where(meta[column].notna(), None).to_numpy()
        notes.append(f'识别到 {len(meta.columns)} 个非数值列（当作元数据，如 barcode/cluster）')
    duplicate_genes = int(pd.Index(genes or []).duplicated().sum())
    duplicate_cells = int(pd.Index(cells).duplicated().sum())
    adata.var_names_make_unique()
    adata.obs_names_make_unique()
    if missing_total:
        notes.append(f'表达矩阵有 {missing_total} 个缺失/非数值单元，已按 0 读取（会改变观测含义，请在源头补全）')
    if duplicate_genes:
        notes.append(f'{duplicate_genes} 个重复基因名已加后缀区分（原始名未丢失，见 import 记录）')
    if duplicate_cells:
        notes.append(f'{duplicate_cells} 个重复细胞 ID 已加后缀区分')
    result = _to_dataset(adata, notes, 'table')
    result.transposed = flipped or result.transposed
    result.dataset.extras['import'].update({
        'missing_values_filled': missing_total,
        'duplicate_genes_renamed': duplicate_genes,
        'duplicate_cells_renamed': duplicate_cells,
        'duplicate_header_names': header_duplicates,
    })
    return result


def from_table(path: Path) -> ImportResult:
    """CSV/TSV：自动识别表头与方向（细胞×基因 / 基因×细胞都行）。"""
    import anndata as ad
    import pandas as pd
    from scipy import sparse

    sep = ',' if '.csv' in path.name.lower() else '\t'
    import os as _os
    if path.stat().st_size > 32 * 1024 * 1024 or _os.environ.get('CELLFLOW_STREAM_CSV') == '1':
        return _from_table_streaming(path, sep)
    frame = pd.read_csv(path, sep=sep, index_col=0, low_memory=False)
    from .memory import estimate
    info = estimate(path)
    header_duplicates = _header_extra_duplicates(path, sep)
    notes = [f'按表格读取（分隔符 {"逗号" if sep == "," else "制表符"}），',
             f'原表 {frame.shape[0]} 行 × {frame.shape[1]} 列',
             f'文件 {info["file_mb"]} MB，按经验估算峰值约 {info["gb"]} GB（读取前已做内存检查）']

    # 先看行列名字像什么：行是基因、列是细胞 → 转置
    gene_rows, cell_rows = _axes_kind(frame.index)
    gene_cols, cell_cols = _axes_kind(frame.columns)
    flipped = False
    if cell_cols - gene_cols > 0.3 and gene_rows - cell_rows > 0.3:
        flipped = True
    elif gene_cols - cell_cols > 0.3 and cell_rows - gene_rows > 0.3:
        flipped = False
    else:
        flipped = _shape_hint(frame.shape[0], frame.shape[1])
    if flipped:
        frame = frame.T
        notes.append('原表是“基因 × 细胞”，已自动转置为“细胞 × 基因”。')

    meta_cols = _table_metadata_columns(frame)
    meta = frame[meta_cols].copy() if meta_cols else None
    num = frame.drop(columns=meta_cols)
    if num.shape[1] < 5:
        raise ValueError('表格里没找到足够的数值列（基因数 < 5）；'
                         '请确认表格是表达矩阵，而不是分组信息表')
    numeric = _strict_expression(num)
    missing_total = int(numeric.isna().sum().sum())
    matrix = numeric.astype(np.float32)
    adata = ad.AnnData(X=sparse.csr_matrix(matrix.to_numpy()),
                       obs=pd.DataFrame(index=pd.Index(frame.index.astype(str), dtype=str)),
                       var=pd.DataFrame(index=pd.Index([str(c) for c in matrix.columns], dtype=str)))
    meta_missing = 0
    for column in meta_cols:
        values = meta[column]
        meta_missing += int(values.isna().sum())
        adata.obs[str(column)] = values.astype(object).where(values.notna(), None).to_numpy()
    if meta_cols:
        notes.append(f'识别到 {len(meta_cols)} 个非数值列（当作元数据，如 barcode/cluster）')
    if missing_total:
        notes.append(f'表达矩阵有 {missing_total} 个缺失/非数值单元，已按 0 读取（会改变观测含义，请在源头补全）')
    if meta_missing:
        notes.append(f'元数据有 {meta_missing} 个缺失值，保留为空值，不会变成 “None”/“nan” 类别')
    duplicate_genes = int(pd.Index([str(c) for c in matrix.columns]).duplicated().sum())
    duplicate_cells = int(pd.Index(frame.index.astype(str)).duplicated().sum())
    if duplicate_genes:
        notes.append(f'{duplicate_genes} 个重复基因名已加后缀区分')
    if duplicate_cells:
        notes.append(f'{duplicate_cells} 个重复细胞 ID 已加后缀区分')
    if header_duplicates:
        notes.append(f'表头有 {header_duplicates} 个重复列名，pandas 已加后缀区分（原始名见文件）')
    adata.var_names_make_unique()
    adata.obs_names_make_unique()
    result = _to_dataset(adata, notes, 'table')
    result.transposed = flipped or result.transposed
    result.dataset.extras['import'].update({
        'missing_values_filled': missing_total,
        'metadata_missing': meta_missing,
        'duplicate_genes_renamed': duplicate_genes,
        'duplicate_cells_renamed': duplicate_cells,
        'duplicate_header_names': header_duplicates,
    })
    return result


def from_10x_dir(folder: Path) -> ImportResult:
    from .publicdata import _read_10x_mtx_dir
    extra: list = []
    adata = _read_10x_mtx_dir(folder, notes=extra)
    if adata is None:
        raise ValueError('这个目录里没找到 matrix.mtx（或 matrix.mtx.gz）')
    notes = ['按 10x 三件套（matrix.mtx + barcodes + features）读取'] + extra
    return _to_dataset(adata, notes, '10x-mtx')


def from_archive(path: Path) -> ImportResult:
    """zip / tar.gz：解压后递归找可读矩阵。"""
    work = Path(tempfile.mkdtemp(prefix='cellflow_import_'))
    try:
        from .archive import safe_extract
        safe_extract(path, work)
        candidates = find_candidates(work)
        if not candidates:
            raise ValueError('压缩包里没有找到可识别的表达矩阵（h5ad / loom / h5 / 10x 三件套 / CSV）')
        result = import_any(candidates[0])
        result.notes.insert(0, f'从压缩包 {path.name} 中读取：{candidates[0].name}')
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def find_candidates(folder: Path) -> List[Path]:
    """在一个目录里按优先级找可读的数据文件。"""
    folder = Path(folder)
    order = ['.h5ad', '.loom', '.h5']
    found: List[Path] = []
    for suffix in order:
        found.extend(sorted(folder.rglob('*' + suffix)))
    mtx_dirs = [p.parent for p in folder.rglob('matrix.mtx*')]
    for directory in sorted(set(mtx_dirs)):
        found.append(directory)
    for pattern in ('*.csv', '*.csv.gz', '*.tsv', '*.tsv.gz', '*.txt.gz'):
        found.extend(sorted(folder.rglob(pattern)))
    for pattern in ('*.zip', '*.tar.gz', '*.tgz'):
        found.extend(sorted(folder.rglob(pattern)))
    return found


# ---------------------------------------------------------------- 统一入口
def import_any(path, log=None) -> ImportResult:
    """把任何支持的文件/目录转成统一的 Dataset（cells × genes）。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f'找不到路径：{path}')
    from .memory import precheck
    reason, _memory_info = precheck(path)
    if reason:
        raise ValueError(reason)
    suffix = path.suffix.lower()
    if path.is_dir():
        return from_10x_dir(path) if list(path.glob('matrix.mtx*')) else \
            import_any(find_candidates(path)[0])
    if suffix == '.h5ad':
        return from_h5ad(path)
    if suffix == '.loom':
        return from_loom(path)
    if suffix == '.h5':
        return from_10x_h5(path)
    if suffix == '.mtx' or path.name.lower().endswith('matrix.mtx.gz'):
        return from_10x_dir(path.parent)
    if suffix in ('.zip', '.tgz') or path.name.lower().endswith('.tar.gz'):
        return from_archive(path)
    if suffix in ('.csv', '.tsv', '.txt') or path.name.lower().endswith(('.csv.gz', '.tsv.gz', '.txt.gz')):
        return from_table(path)
    if suffix == '.gz':
        # 可能是 matrix.mtx.gz 或压缩的表格
        if path.name.lower().startswith('matrix.mtx'):
            return from_10x_dir(path.parent)
        raise ValueError(f'无法识别这个 gz 文件：{path.name}（支持 matrix.mtx.gz、*.csv.gz、*.tsv.gz）')
    raise ValueError(f'暂不支持的文件类型：{suffix or path.name}。'
                     '支持 .h5ad / .loom / .h5 / 10x 三件套 / CSV / TSV / zip / tar.gz。')


def describe(result: ImportResult) -> dict:
    """给界面/报告用的一段中文说明。"""
    dataset = result.dataset
    return dict(
        kind=result.kind,
        transposed=result.transposed,
        n_cells=int(dataset.X.shape[0]),
        n_genes=int(dataset.X.shape[1]),
        layers=sorted(dataset.layers.keys()),
        notes=result.notes,
    )


def summarize_kind(kind: str) -> str:
    return {
        'h5ad': 'AnnData（.h5ad）',
        'loom': 'loom（常见于 velocyto 输出）',
        '10x-h5': '10x Cell Ranger（.h5）',
        '10x-mtx': '10x 三件套（matrix.mtx + barcodes + features）',
        'table': 'CSV/TSV 表达表',
        'archive': '压缩包',
        'directory': '目录',
    }.get(kind, kind)
