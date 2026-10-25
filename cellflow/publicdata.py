"""公开单细胞数据下载 → 标准 .h5ad。

功能参考并改写了 h5adify（MIT License, https://pypi.org/project/h5adify/）的设计：
- 同样提供「搜索 → 按 ID 下载 → 转成标准 h5ad → 补齐 obs 元数据」的流程；
- 但本模块刻意保持轻量：**不引入 GEOparse / beautifulsoup4 / lxml**，GEO 直接走 NCBI 的
  HTTPS 目录与 SOFT 元数据，CELLxGENE 走其公开 API + 直链下载。

只做三件事：找到文件、下载、转成 AnnData。所有数据都落盘在本机，不上传任何东西。

用法（命令行）：
    python -m cellflow.cli search --source geo --query "pancreas scRNA-seq"
    python -m cellflow.cli fetch  --source geo --id GSE84133 --out data
    python -m cellflow.cli fetch  --source cellxgene --id <uuid> --out data
    python -m cellflow.cli fetch  --source url --id https://.../x.h5ad --out data

许可说明见 docs/第三方代码与许可.md。
"""
from __future__ import annotations

import gzip
import html
import io
import re
import shutil
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np

HTTP_TIMEOUT = (10, 120)
HTTP_TIMEOUT_SHORT = (6, 25)
USER_AGENT = 'CellFlow/0.2 (local single-cell workbench; +https://pypi.org/project/h5adify/)'

GEO_SERIES = 'https://ftp.ncbi.nlm.nih.gov/geo/series/{stub}/{gse}/suppl/'
GEO_SOFT = 'https://ftp.ncbi.nlm.nih.gov/geo/series/{stub}/{gse}/soft/{gse}_family.soft.gz'
CXG_DATASETS = 'https://api.cellxgene.cziscience.com/curation/v1/datasets'
CXG_DOWNLOAD = 'https://datasets.cellxgene.cziscience.com/{uuid}.h5ad'
EUTILS = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi'

SOURCES = {
    'geo': 'GEO（NCBI Gene Expression Omnibus，按 GSE 编号）',
    'cellxgene': 'CELLxGENE Discover（按数据集 UUID 或 .h5ad 直链）',
    'url': '任意 .h5ad 直链',
}

OBS_REQUIRED = ('source', 'dataset_id', 'sample_id', 'species',
                'technology', 'modality', 'tissue', 'condition', 'disease')


@dataclass
class SearchResult:
    source: str
    dataset_id: str
    title: str
    url: str = ''
    extra: Dict[str, str] = field(default_factory=dict)


class DownloadCancelled(Exception):
    """用户取消下载。"""


_hooks = __import__('threading').local()


def set_download_hook(byte_progress=None, should_cancel=None):
    """在当前线程安装下载进度/取消钩子；download_file 会每块检查一次。"""
    _hooks.byte_progress = byte_progress
    _hooks.should_cancel = should_cancel


def _emit(done, total):
    fn = getattr(_hooks, 'byte_progress', None)
    if fn is not None:
        fn(done, total)


def _cancelled():
    fn = getattr(_hooks, 'should_cancel', None)
    return bool(fn and fn())


class PublicDataError(RuntimeError):
    """下载/转换失败时抛出，信息尽量是中文可操作提示。"""


# ---------------------------------------------------------------- HTTP
def _session():
    import requests
    from requests.adapters import HTTPAdapter
    try:
        from urllib3.util.retry import Retry
        retry = Retry(total=4, connect=4, read=4, backoff_factor=0.5,
                      status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=frozenset(['GET', 'HEAD']))
        adapter = HTTPAdapter(max_retries=retry)
    except Exception:
        adapter = HTTPAdapter()
    s = requests.Session()
    s.headers.update({'User-Agent': USER_AGENT})
    s.mount('https://', adapter)
    s.mount('http://', adapter)
    return s


def download_file(url: str, target: Path, progress: Optional[Callable[[int, int], None]] = None,
                  attempts: int = 4) -> Path:
    """下载文件，支持断点续传与自动重试。

    公共数据库（尤其 NCBI）经常在中途断开连接；直接用一次 GET 会让大文件永远下不完。
    这里用 Range 头续传，失败后按次数退避重试。
    """
    import time

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    session = _session()
    last_error = None
    for attempt in range(attempts):
        have = target.stat().st_size if target.exists() else 0
        headers = {'Range': f'bytes={have}-'} if have else {}
        try:
            with session.get(url, stream=True, timeout=HTTP_TIMEOUT, headers=headers) as response:
                if have and response.status_code == 206:
                    mode = 'ab'
                    total = have + int(response.headers.get('content-length') or 0)
                elif response.status_code in (200, 206):
                    mode = 'wb'
                    have = 0
                    total = int(response.headers.get('content-length') or 0)
                elif response.status_code == 416 and have:
                    return target  # 已经完整
                else:
                    response.raise_for_status()
                    mode, total = 'wb', 0
                done = have
                with open(target, mode) as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        if _cancelled():
                            raise DownloadCancelled('用户取消下载')
                        handle.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
                        _emit(done, total)
            if total and target.stat().st_size < total:
                raise PublicDataError(f'下载不完整：{target.stat().st_size}/{total} 字节')
            return target
        except DownloadCancelled:
            raise
        except Exception as exc:  # 连接中断、超时、5xx 都重试
            last_error = exc
            if attempt == attempts - 1:
                break
            time.sleep(1.5 * (attempt + 1))
    raise PublicDataError(f'下载失败（重试 {attempts} 次）：{type(last_error).__name__}: {last_error}')


# ---------------------------------------------------------------- 元数据
def _species_from_var(var_names) -> str:
    names = [str(v).upper() for v in list(var_names)[:2000]]
    if any(n.startswith('ENSMUSG') for n in names):
        return 'mouse'
    if any(n.startswith('ENSG') for n in names):
        return 'human'
    return 'unknown'


def standardize(adata, source: str, dataset_id: str, sample_id: Optional[str] = None,
                species: str = '', overrides: Optional[Dict[str, str]] = None):
    """补齐 obs 元数据（只填空缺，不覆盖已有列的内容）。"""
    adata = adata.copy()
    for key in OBS_REQUIRED:
        if key not in adata.obs:
            adata.obs[key] = 'unknown'
        else:
            series = adata.obs[key].astype(str)
            adata.obs[key] = series.replace({'': 'unknown', 'nan': 'unknown', 'None': 'unknown'})
    adata.obs['source'] = str(source)
    adata.obs['dataset_id'] = str(dataset_id)
    if sample_id:
        adata.obs['sample_id'] = str(sample_id)
    else:
        existing = adata.obs['sample_id'].astype(str)
        if existing.nunique() <= 1 or str(existing.iloc[0]) in ('unknown', 'nan', ''):
            adata.obs['sample_id'] = str(dataset_id)
    if species:
        adata.obs['species'] = str(species)
    elif str(adata.obs['species'].iloc[0]) in ('unknown', ''):
        adata.obs['species'] = _species_from_var(adata.var_names)
    for key, value in (overrides or {}).items():
        if key in adata.obs and value not in ('', None):
            adata.obs[key] = str(value)
    adata.uns.setdefault('cellflow_public', {})
    adata.uns['cellflow_public'] = dict(source=source, dataset_id=str(dataset_id),
                                        sample_id=str(sample_id or dataset_id),
                                        downloaded_at_raw=True)
    return adata


# ---------------------------------------------------------------- GEO
def _geo_stub(gse: str) -> str:
    digits = gse[3:]
    if not digits.isdigit():
        raise PublicDataError('GEO 编号格式应为 GSE 加数字，例如 GSE84133')
    if len(digits) < 3:
        raise PublicDataError('GEO 编号看起来不完整（至少 3 位数字）')
    return 'GSE' + digits[:-3] + 'nnn'


def geo_series_url(gse: str) -> str:
    gse = gse.upper().strip()
    return GEO_SERIES.format(stub=_geo_stub(gse), gse=gse)


def geo_soft_url(gse: str) -> str:
    gse = gse.upper().strip()
    return GEO_SOFT.format(stub=_geo_stub(gse), gse=gse)


def _list_geo_supplementary(gse: str) -> List[str]:
    session = _session()
    url = geo_series_url(gse)
    response = session.get(url, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    names = re.findall(r'href="([^"?/][^"]*)"', response.text)
    files = []
    for name in names:
        low = name.lower()
        if any(low.endswith(ext) for ext in ('.tar', '.tar.gz', '.tgz', '.h5ad', '.h5',
                                             '.mtx', '.mtx.gz', '.zip', '.csv', '.tsv', '.txt.gz')):
            if name.startswith('GSE'):
                files.append(url + name)
    return sorted(set(files))


def list_geo_files(gse: str) -> List[str]:
    """列出某个 GSE 的补充文件直链（给界面/命令行预览用）。"""
    return _list_geo_supplementary(gse)


def _parse_soft(soft_text: str) -> Dict[str, Dict[str, str]]:
    """从 SOFT 家庭文件里抽取按样本的元数据（尽量省事，抽不到就留空）。"""
    info: Dict[str, Dict[str, str]] = {}
    current = None
    series: Dict[str, str] = {}
    for line in soft_text.splitlines():
        if line.startswith('^SAMPLE'):
            current = line.split('=')[-1].strip()
            info[current] = {}
        elif line.startswith('^SERIES'):
            current = '__series__'
            info[current] = {}
        elif line.startswith('!') and current:
            key, _, value = line.partition('=')
            key = key.strip().lstrip('!')
            # SOFT 里的键长这样：Sample_organism_ch1 / Series_title，统一去掉前缀
            for prefix in ('Sample_', 'Series_'):
                if key.startswith(prefix):
                    key = key[len(prefix):]
                    break
            info[current].setdefault(key.lower(), value.strip())
    # Series 级的 title/summary 给界面看
    base = {}
    for key in ('title', 'summary', 'overall_design'):
        if key in info.get('__series__', {}):
            base['Series_' + key] = info['__series__'][key]
    info['__base__'] = base
    return info


def _guess_species(text: str) -> str:
    low = (text or '').lower()
    for needle, name in (('homo sapiens', 'human'), ('mus musculus', 'mouse'),
                         ('rattus', 'rat'), ('macaca', 'macaque')):
        if needle in low:
            return name
    return ''


def geo_search(query: str, max_results: int = 20) -> List[SearchResult]:
    session = _session()
    params = {'db': 'gds', 'term': query, 'retmax': str(max_results), 'retmode': 'json'}
    response = session.get(EUTILS, params=params, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    try:
        payload = response.json()
        ids = payload['esearchresult'].get('idlist', [])
    except Exception:
        ids = re.findall(r'<Id>(\d+)</Id>', response.text)
    out = []
    for ident in ids[:max_results]:
        out.append(SearchResult(source='geo', dataset_id=str(ident),
                                title=f'NCBI GDS {ident}（在 GEO 页面上确认是不是 GSE 编号）',
                                url=f'https://www.ncbi.nlm.nih.gov/gds/?term={ident}'))
    return out


def _read_first_column(path: Path) -> List[str]:
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8', errors='replace') as handle:
        return [line.split('\t')[0].strip() for line in handle if line.strip()]


def _read_features(path: Path) -> List[str]:
    """features.tsv：第 1 列是 ID、第 2 列是基因名；优先用基因名。"""
    opener = gzip.open if str(path).endswith('.gz') else open
    out = []
    with opener(path, 'rt', encoding='utf-8', errors='replace') as handle:
        for line in handle:
            parts = line.rstrip('\n').split('\t')
            if not parts or not parts[0].strip():
                continue
            out.append((parts[1] if len(parts) > 1 and parts[1].strip() else parts[0]).strip())
    return out


def _read_10x_mtx_dir(folder: Path, notes=None):
    """读 10x 三件套，并像 Cell Ranger 一样校验三件套是否自洽。

    硬错误直接 ValueError（维度对不上、缺 features）；缺 barcodes 时生成占位名并记入 notes。
    """
    import anndata as ad
    import pandas as pd
    import scipy.io
    from scipy import sparse
    folder = Path(folder)
    notes = notes if notes is not None else []
    candidates = sorted(folder.glob('matrix.mtx*'))
    if not candidates:
        return None
    mtx_path = candidates[0]
    opener = gzip.open if str(mtx_path).endswith('.gz') else open
    with opener(mtx_path, 'rb') as handle:
        mat = scipy.io.mmread(handle)
    mat = sparse.csr_matrix(mat)
    if mat.shape[0] == 0 or mat.shape[1] == 0:
        raise ValueError(f'matrix.mtx 是空矩阵：{mat.shape}')

    feature_file = next((p for p in folder.glob('features.tsv*')), None) or \
        next((p for p in folder.glob('genes.tsv*')), None)
    barcode_file = next((p for p in folder.glob('barcodes.tsv*')), None)
    if feature_file is None:
        raise ValueError('10x 三件套缺少 features.tsv(.gz)/genes.tsv(.gz)')
    genes = _read_features(feature_file)
    if not genes:
        raise ValueError(f'{feature_file.name} 里没有读到基因名')
    if barcode_file is not None:
        cells = _read_first_column(barcode_file)
        if not cells:
            raise ValueError(f'{barcode_file.name} 里没有读到细胞条码')
    else:
        cells = []
        notes.append('缺少 barcodes.tsv：已用 cell_1… 占位，样本信息不可追溯')

    # 10x 约定是 基因 × 细胞；按维度对应关系决定要不要转置
    if mat.shape[0] == len(genes):
        matrix = mat.T.tocsr()
        cells_axis = mat.shape[1]
    elif mat.shape[1] == len(genes):
        matrix = mat.tocsr()
        cells_axis = mat.shape[0]
    else:
        raise ValueError(
            f'matrix.mtx 维度 {mat.shape} 与 features 数 {len(genes)} 对不上；'
            '请确认 matrix / features / barcodes 来自同一次 Cell Ranger 输出')

    if cells and len(cells) != cells_axis:
        raise ValueError(
            f'barcodes 数 {len(cells)} 与矩阵细胞轴 {cells_axis} 不一致；'
            '常见原因是混入了不同样本或不同过滤版本的文件')
    if not cells:
        cells = [f'cell_{i+1}' for i in range(cells_axis)]

    values = matrix.data
    if values.size and (values < 0).any():
        raise ValueError('matrix.mtx 含负数，10x 原始计数不应为负；请检查是否被归一化过')
    if values.size and not bool((values == values.round()).all()):
        notes.append('matrix.mtx 不是整数计数：可能已归一化，速度类方法需要原始计数')

    adata = ad.AnnData(X=matrix,
                       obs=pd.DataFrame(index=pd.Index(cells, dtype=str)),
                       var=pd.DataFrame(index=pd.Index(genes, dtype=str)))
    adata.var_names_make_unique()
    adata.obs_names_make_unique()
    return adata


def _load_any_matrix(folder: Path):
    """在解压目录里找一个能读的表达矩阵（10x mtx / h5ad / 10x h5）。"""
    import anndata as ad
    import scanpy as sc
    folder = Path(folder)

    for h5ad in list(folder.rglob('*.h5ad')):
        try:
            return ad.read_h5ad(h5ad)
        except Exception:
            continue

    for h5 in list(folder.rglob('*.h5')):
        try:
            return sc.read_10x_h5(str(h5))
        except Exception:
            continue

    for mtx in list(folder.rglob('matrix.mtx')) + list(folder.rglob('matrix.mtx.gz')):
        try:
            adata = _read_10x_mtx_dir(mtx.parent)
        except Exception:
            adata = None
        if adata is not None:
            return adata
        # 兜底：交给 scanpy（某些版本要求 matrix.mtx.gz）
        parent = mtx.parent
        for extra in list(parent.glob('*.gz')):
            try:
                with gzip.open(extra, 'rb') as fin:
                    out = extra.with_suffix('')
                    with open(out, 'wb') as fout:
                        shutil.copyfileobj(fin, fout)
                extra.unlink(missing_ok=True)
            except Exception:
                continue
        try:
            return sc.read_10x_mtx(str(parent), var_names='gene_symbols', make_unique=True)
        except Exception:
            continue
    return None


META_COLUMNS = {'barcode', 'barcodes', 'assigned_cluster', 'celltype', 'cell_type', 'cluster',
                'clusters', 'sample', 'batch', 'n_counts', 'n_genes', 'percent_mito', 'umi',
                'read_count', 'lib_size', 'size_factor', 'cell', 'cell_id', 'index'}


def _looks_like_table(path: Path) -> bool:
    low = path.name.lower()
    return low.endswith(('.csv', '.csv.gz', '.tsv', '.tsv.gz', '.txt.gz', '.txt')) and \
        not any(word in low for word in ('series_matrix', 'soft', 'readme', 'metadata'))


def _read_table_matrix(path: Path):
    """读 CSV/TSV 表达矩阵（第一列是细胞 ID，其余数值列是基因）。

    GEO 上大量经典数据集（例如 Baron 胰腺）就是这种格式，而不是 10x 三件套。
    """
    import anndata as ad
    import pandas as pd
    from scipy import sparse
    sep = ',' if '.csv' in path.name.lower() else '\t'
    frame = pd.read_csv(path, sep=sep, index_col=0, low_memory=False)
    if frame.shape[0] < 2 or frame.shape[1] < 2:
        return None
    obs_cols = [c for c in frame.columns
                if str(c).lower() in META_COLUMNS or not pd.api.types.is_numeric_dtype(frame[c])]
    gene_cols = [c for c in frame.columns if c not in obs_cols]
    if len(gene_cols) < 10:
        return None
    matrix = frame[gene_cols].apply(pd.to_numeric, errors='coerce').fillna(0.0).astype(np.float32)
    adata = ad.AnnData(X=sparse.csr_matrix(matrix.to_numpy()),
                       obs=pd.DataFrame(index=pd.Index(frame.index.astype(str), dtype=str)),
                       var=pd.DataFrame(index=pd.Index([str(g) for g in gene_cols], dtype=str)))
    for column in obs_cols:
        try:
            adata.obs[str(column)] = frame[column].astype(str).to_numpy()
        except Exception:
            continue
    adata.var_names_make_unique()
    adata.obs_names_make_unique()
    return adata


def _load_all_matrices(folder: Path):
    """在目录里找出所有可识别的表达矩阵，返回 [(名字, AnnData)]。"""
    import anndata as ad
    import scanpy as sc
    folder = Path(folder)
    found = []

    for h5ad in sorted(folder.rglob('*.h5ad')):
        try:
            found.append((h5ad.stem, ad.read_h5ad(h5ad)))
        except Exception:
            continue
    for h5 in sorted(folder.rglob('*.h5')):
        try:
            found.append((h5.stem, sc.read_10x_h5(str(h5))))
        except Exception:
            continue
    for mtx in sorted(list(folder.rglob('matrix.mtx')) + list(folder.rglob('matrix.mtx.gz'))):
        try:
            adata = _read_10x_mtx_dir(mtx.parent)
        except Exception:
            adata = None
        if adata is not None:
            found.append((mtx.parent.name or 'sample', adata))
    for table in sorted(folder.rglob('*')):
        if not table.is_file() or not _looks_like_table(table):
            continue
        try:
            adata = _read_table_matrix(table)
        except Exception:
            adata = None
        if adata is not None:
            found.append((table.name.split('.')[0], adata))
    # 去掉明显重复（同一个矩阵被 h5ad 与 csv 同时读取的情况很少，这里按形状+名字去重）
    unique, seen = [], set()
    for name, adata in found:
        key = (name, adata.shape)
        if key in seen:
            continue
        seen.add(key)
        unique.append((name, adata))
    return unique


def _extract_archive(path: Path, target: Path) -> Path:
    from .archive import safe_extract
    safe_extract(path, target)
    return target


def _sample_species(name: str, soft: Dict[str, Dict[str, str]]) -> str:
    """从 SOFT 元数据里找样本对应的物种（按 GSM 前缀匹配文件名）。"""
    for key, meta in (soft or {}).items():
        if key.startswith('GSM') and name.startswith(key):
            text = ' '.join(str(meta.get(field, '')) for field in
                            ('organism_ch1', 'source_name_ch1', 'characteristics_ch1'))
            found = _guess_species(text)
            if found:
                return found
    return ''


def geo_fetch(gse: str, outdir: Path, progress: Optional[Callable[[str], None]] = None) -> List[Path]:
    """下载一个 GSE 的全部可识别矩阵，合并成一个标准 h5ad。"""
    import anndata as ad
    gse = gse.upper().strip()
    if not gse.startswith('GSE'):
        raise PublicDataError('GEO 只接受 GSE 系列编号，例如 GSE84133')
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix=f'cellflow_geo_{gse}_'))

    def say(message):
        if progress:
            progress(message)

    try:
        files = _list_geo_supplementary(gse)
        if not files:
            raise PublicDataError(
                f'{gse} 的 supplementary 目录里没有找到可下载的矩阵文件。'
                '有些数据集只提供 SRA 原始数据或 .rds，需要先自行转换。')
        say(f'找到 {len(files)} 个补充文件，开始下载')
        species = ''
        soft_title = ''
        soft_path = workdir / f'{gse}_family.soft.gz'
        try:
            download_file(geo_soft_url(gse), soft_path)
            with gzip.open(soft_path, 'rt', encoding='utf-8', errors='replace') as handle:
                soft = _parse_soft(handle.read())
            base = soft.get('__base__', {})
            soft_title = base.get('Series_title', '')
            species = _guess_species(' '.join(str(v) for v in base.values()))
        except Exception:
            soft = {}

        collected = []  # (样本名, AnnData, 物种)
        for url in files:
            name = url.rstrip('/').split('/')[-1]
            say(f'下载 {name}')
            local = workdir / name
            try:
                download_file(url, local)
            except DownloadCancelled:
                raise
            except Exception as exc:
                say(f'跳过 {name}：下载失败（{exc}）')
                continue
            target_dir = workdir / (name + '_x')
            items = []
            try:
                low = name.lower()
                if low.endswith(('.tar', '.tar.gz', '.tgz', '.zip')):
                    _extract_archive(local, target_dir)
                    items = _load_all_matrices(target_dir)
                elif low.endswith('.h5ad'):
                    items = [(Path(name).stem, ad.read_h5ad(local))]
                elif low.endswith('.h5'):
                    import scanpy as sc
                    items = [(Path(name).stem, sc.read_10x_h5(str(local)))]
                elif _looks_like_table(local):
                    items = [(Path(name).stem, _read_table_matrix(local))]
                else:
                    items = _load_all_matrices(workdir)
            except Exception as exc:
                say(f'跳过 {name}：无法解析（{exc}）')
                items = []
            items = [(item_name, adata) for item_name, adata in items if adata is not None]
            if not items:
                say(f'跳过 {name}：没有可识别的表达矩阵')
                continue
            for item_name, adata in items:
                collected.append((item_name, adata, _sample_species(item_name, soft)))

        if not collected:
            raise PublicDataError(
                f'{gse} 的补充文件里没有可直接读取的表达矩阵（可能是 .rds/Seurat 或纯原始数据）。'
                '建议在 GEO 页面确认文件类型，或换成提供 10x mtx / h5ad / CSV 计数表的数据集。')

        # 不同物种不能混在一个 h5ad 里：按物种分组，只保留细胞数最多的那一组
        by_species: Dict[str, List] = {}
        for item in collected:
            by_species.setdefault(item[2] or 'unknown', []).append(item)
        keep = max(by_species, key=lambda key: sum(int(a.n_obs) for _, a, _ in by_species[key]))
        dropped = {key: len(value) for key, value in by_species.items() if key != keep}
        kept = by_species[keep]
        names = [item[0] for item in kept]
        adatas = [item[1] for item in kept]
        if not species:
            species = keep if keep != 'unknown' else ''
        if dropped:
            say('该数据集包含多个物种，只保留 ' + keep + '；已忽略：' +
                '，'.join(f'{k}({v} 个样本)' for k, v in dropped.items()))

        say(f'共 {len(adatas)} 个样本，开始合并')
        if len(adatas) == 1:
            merged = adatas[0]
            merged.obs['sample_id'] = names[0]
        else:
            merged = ad.concat(adatas, join='outer', label='sample_id',
                               keys=names, index_unique='-', merge='same')
        merged.var_names_make_unique()
        merged.obs_names_make_unique()
        merged = standardize(merged, 'geo', gse, species=species)
        if soft_title:
            merged.uns['cellflow_public']['title'] = soft_title
        if dropped:
            merged.uns['cellflow_public']['dropped_species'] = dropped
        out = outdir / f'geo_{gse}.h5ad'
        merged.write_h5ad(out)
        say(f'已保存 {out}')
        return [out]
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------- CELLxGENE
def cellxgene_search(query: str, max_results: int = 20) -> List[SearchResult]:
    session = _session()
    try:
        response = session.get(CXG_DATASETS, params={'limit': max_results, 'q': query},
                               timeout=HTTP_TIMEOUT_SHORT)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise PublicDataError(
            '访问 CELLxGENE 失败（' + type(exc).__name__ + '）。'
            '本机到该站点的网络可能受限，建议改用 GEO（按 GSE 编号）或直接给 .h5ad 链接。') from exc
    if isinstance(payload, list):
        hits = payload
    elif isinstance(payload, dict):
        hits = payload.get('datasets') or payload.get('data') or payload.get('results') or []
    else:
        hits = []
    out = []
    for item in hits[:max_results]:
        did = str(item.get('id') or item.get('dataset_id') or '')
        title = item.get('title') or item.get('name') or did
        out.append(SearchResult(source='cellxgene', dataset_id=did, title=str(title),
                                url=f'https://cellxgene.cziscience.com/e/{did}.cxg/' if did else ''))
    return out


def _is_uuid(text: str) -> bool:
    return bool(re.match(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}'
                         r'-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$', (text or '').strip()))


def cellxgene_fetch(identifier: str, outdir: Path, progress: Optional[Callable[[str], None]] = None,
                    species: str = '') -> List[Path]:
    import anndata as ad
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    say = progress or (lambda message: None)
    ident = identifier.strip()
    if ident.startswith('http') and ident.lower().endswith('.h5ad'):
        url, canonical = ident, Path(ident).stem
    elif _is_uuid(ident):
        url, canonical = CXG_DOWNLOAD.format(uuid=ident), ident
    else:
        raise PublicDataError('CELLxGENE 需要数据集 UUID（形如 0d5f2f4a-…）或 .h5ad 直链')
    say('下载 ' + url)
    local = outdir / f'cellxgene_{canonical[:8]}.h5ad'
    download_file(url, local)
    adata = ad.read_h5ad(local)
    modality = 'spatial' if any('spatial' in str(k).lower() for k in adata.obsm.keys()) else 'sc/snRNA'
    adata = standardize(adata, 'cellxgene', canonical, species=species,
                        overrides={'modality': modality})
    out = outdir / f'cellxgene_{canonical[:8]}_standard.h5ad'
    adata.write_h5ad(out)
    local.unlink(missing_ok=True)
    say(f'已保存 {out}')
    return [out]


# ---------------------------------------------------------------- 统一入口
def url_fetch(url: str, outdir: Path, progress: Optional[Callable[[str], None]] = None) -> List[Path]:
    import anndata as ad
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    say = progress or (lambda message: None)
    if not url.lower().startswith('http'):
        raise PublicDataError('请填写 http(s) 开头的 .h5ad 下载链接')
    say('下载 ' + url)
    local = outdir / ('url_' + re.sub(r'[^A-Za-z0-9._-]+', '_', url.split('/')[-1])[:60])
    download_file(url, local)
    adata = ad.read_h5ad(local)
    adata = standardize(adata, 'url', local.stem)
    out = outdir / (local.stem + '_standard.h5ad')
    adata.write_h5ad(out)
    local.unlink(missing_ok=True)
    say(f'已保存 {out}')
    return [out]


def search(source: str, query: str, max_results: int = 20) -> List[SearchResult]:
    source = (source or '').lower()
    if source == 'geo':
        return geo_search(query, max_results)
    if source in ('cellxgene', 'cxg'):
        return cellxgene_search(query, max_results)
    raise PublicDataError('search 只支持 geo 与 cellxgene；其它请直接用 ID 或链接下载')


def fetch(source: str, identifier: str, outdir: Path,
          progress: Optional[Callable[[str], None]] = None, species: str = '') -> Path:
    """下载并转成标准 h5ad，返回文件路径。"""
    source = (source or '').lower()
    if source == 'geo':
        paths = geo_fetch(identifier, outdir, progress)
    elif source in ('cellxgene', 'cxg'):
        paths = cellxgene_fetch(identifier, outdir, progress, species)
    elif source == 'url':
        paths = url_fetch(identifier, outdir, progress)
    else:
        raise PublicDataError(f'未知来源：{source}（可用：{", ".join(SOURCES)}）')
    return Path(paths[0])


def describe(path: Path) -> Dict[str, object]:
    """给界面用的摘要：大小、维度、是否有剪接层。"""
    import anndata as ad
    path = Path(path)
    info = dict(path=str(path), size_mb=round(path.stat().st_size / 1024 / 1024, 2))
    try:
        handle = ad.read_h5ad(path, backed='r')
        try:
            info.update(n_obs=int(handle.n_obs), n_vars=int(handle.n_vars),
                        layers=list(handle.layers.keys()), obs=list(handle.obs.columns)[:12])
        finally:
            try:
                handle.file.close()
            except Exception:
                pass
    except Exception as exc:
        info['error'] = str(exc)
    return info
