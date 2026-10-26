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
import json
import re
import shutil
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np

from . import paths

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
    # 下载中一律写 <name>.part，只有完整收完才改名为正式文件；
    # 已存在的正式文件**不能**直接当成完整：旧版本用正式文件名做断点续传，
    # 可能留下截断文件。这里把它降级为 .part，用 Range 继续收完（服务器不支持
    # Range 时会从头重下），宁可多一次请求也不静默接受半成品。
    part = paths.partial_path(target)
    if target.is_file() and target.stat().st_size > 0:
        if part.exists():
            target.unlink(missing_ok=True)
        else:
            target.replace(part)
    session = _session()
    last_error = None
    for attempt in range(attempts):
        have = part.stat().st_size if part.exists() else 0
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
                    part.replace(target)   # 服务器认为已发完；完整性由调用方校验
                    return target
                else:
                    response.raise_for_status()
                    mode, total = 'wb', 0
                # 空间门禁：大小未知时也至少要求 MIN_FREE_BYTES 的余量
                paths.ensure_free_space(part.parent, total or 0, label='下载')
                done = have
                with open(part, mode) as handle:
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
            size = part.stat().st_size if part.exists() else 0
            if total and size < total:
                raise PublicDataError(f'下载不完整：{size}/{total} 字节')
            part.replace(target)
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
    """列出某个 GSE 的补充文件直链（旧接口，保留兼容）。"""
    return _list_geo_supplementary(gse)


# ---------------------------------------------------------------- P3-01 清单
# GEO 的 suppl 目录是 Apache "Index of" 页面，<pre> 里每行是
#   <a href="NAME">NAME</a>  YYYY-MM-DD HH:MM  SIZE
# 其中 SIZE 是 113K / 16M / 1.2G 这类四舍五入值。
# 因此**不下载任何文件**就能列出文件名、大小与修改时间；精确字节数可按需 HEAD。
GEO_SAMPLE = 'https://ftp.ncbi.nlm.nih.gov/geo/samples/{stub}/{gsm}/suppl/'
EUTILS_SUMMARY = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi'

#: 可被 CellFlow 直接尝试导入的扩展名（顺序有意义：复合扩展名在前）
IMPORTABLE_SUFFIXES = ('.loom.gz', '.h5ad', '.loom', '.h5', '.mtx.gz', '.mtx',
                       '.tar', '.tar.gz', '.tgz', '.zip', '.csv', '.tsv', '.txt.gz')

_SIZE_UNITS = {'B': 1, 'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3, 'T': 1024 ** 4}


def geo_stub(accession: str) -> str:
    """GSE72857 -> GSE72nnn；GSM7093919 -> GSM709nnn（NCBI 的目录分片规则）。"""
    acc = (accession or '').strip().upper()
    digits = re.sub(r'\D', '', acc)
    if len(digits) < 4:
        raise PublicDataError(f'无法识别编号：{accession}')
    return acc[:3] + digits[:-3] + 'nnn'


def geo_supplementary_url(gse: str) -> str:
    gse = gse.strip().upper()
    return GEO_SERIES.format(stub=geo_stub(gse), gse=gse)


def geo_sample_url(gsm: str) -> str:
    gsm = gsm.strip().upper()
    return GEO_SAMPLE.format(stub=geo_stub(gsm), gsm=gsm)


def _size_to_bytes(text: str) -> Optional[int]:
    """113K -> 115712（按 1K=1024 取上界，作为空间预检查的保守估算）。"""
    text = (text or '').strip().upper()
    if not text or text == '-':
        return None
    match = re.match(r'^([0-9]+(?:\.[0-9]+)?)\s*([BKMGTP]?)', text)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2) or 'B'
    factor = _SIZE_UNITS.get(unit)
    if factor is None:
        return None
    return int(value * factor)


def _parse_ftp_index(text: str) -> List[Dict[str, object]]:
    """解析 Apache 目录页里的文件行（只接受能解析出日期的行）。"""
    entries: List[Dict[str, object]] = []
    line_re = re.compile(
        r'<a href="(?P<name>[^"/?][^"]*)">.*?</a>\s+'
        r'(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{2}:\d{2})\s+(?P<size>[-0-9.]+[BKMGTP]?)',
        re.IGNORECASE)
    for line in (text or '').splitlines():
        match = line_re.search(line)
        if not match:
            continue
        name = match.group('name').strip()
        if not name or name in ('.', '..'):
            continue
        size_text = match.group('size').strip()
        entries.append({
            'name': name,
            'modified': f"{match.group('date')} {match.group('time')}",
            'size_text': size_text,
            'size_bytes': _size_to_bytes(size_text),
            'size_source': 'ftp_listing' if size_text != '-' else 'unknown',
        })
    return entries


def _classify_file(name: str) -> Dict[str, object]:
    """按扩展名给出用途提示。**只按名字分类，不代表文件里真有该数据。**"""
    low = name.lower()
    compression = ''
    for suffix in ('.gz', '.bz2', '.zip', '.tar', '.tgz'):
        if low.endswith(suffix):
            compression = suffix
            break
    # 判断真实类型时要先去掉压缩后缀：barcodes.tsv.gz 属于 table，不是 other
    bare = low
    for suffix in ('.gz', '.bz2'):
        if bare.endswith(suffix):
            bare = bare[: -len(suffix)]
            break
    if low.endswith('.loom.gz') or low.endswith('.loom'):
        kind = 'loom'
    elif low.endswith('.h5ad'):
        kind = 'h5ad'
    elif low.endswith('.h5'):
        kind = '10x_h5'
    elif bare.endswith('.mtx'):
        kind = '10x_mtx'
    elif low.endswith(('.tar', '.tgz', '.zip')):
        kind = 'archive'
    elif bare.endswith(('.csv', '.tsv', '.txt')):
        kind = 'table'
    else:
        kind = 'other'
    # 「文件名暗示」是证据等级最低的一档，绝不等于已验证
    filename_splice_hint = any(token in low for token in
                               ('velocyto', 'spliced', 'unspliced', 'splicing', 'loom'))
    importable = any(low.endswith(suffix) for suffix in IMPORTABLE_SUFFIXES)
    if kind == 'loom':
        evidence = 'filename_only'
    elif filename_splice_hint:
        evidence = 'filename_only'
    else:
        evidence = 'absent'
    return {
        'kind': kind,
        'compression': compression,
        'importable': bool(importable),
        'splice_evidence': evidence,
        'splice_filename_hint': bool(filename_splice_hint),
    }


def _file_record(name: str, url: str, level: str, accession: str,
                 gse: str = '', gsm: str = '', size_bytes=None,
                 size_text='', size_source='unknown', modified='') -> Dict[str, object]:
    info = _classify_file(name)
    record = {
        'name': name,
        'url': url,
        'level': level,                 # series / sample
        'accession': accession,         # 该目录对应的 GSE 或 GSM
        'series': gse,
        'sample': gsm,
        'modified': modified,
        'size_bytes': size_bytes,
        'size_text': size_text,
        'size_source': size_source,
        'recommended_for': [],
        'splice_evidence': info['splice_evidence'],
        'splice_filename_hint': info['splice_filename_hint'],
        'kind': info['kind'],
        'compression': info['compression'],
        'importable': info['importable'],
        'download_state': 'not_downloaded',
        'checksum': None,
        'metadata_source': 'ftp_listing',
    }
    if info['kind'] in ('loom', 'h5ad'):
        record['recommended_for'] = ['expression', 'splice_candidate']
    elif info['kind'] in ('10x_h5', '10x_mtx', 'table'):
        record['recommended_for'] = ['expression']
    return record


def geo_list_files(gse: str) -> Dict[str, object]:
    """列出某个 GSE 的补充文件（**只读目录页，不下载任何数据**）。"""
    gse = (gse or '').strip().upper()
    if not re.match(r'^GSE\d{3,}$', gse):
        raise PublicDataError('GEO 只接受 GSE 系列编号，例如 GSE84133')
    url = geo_supplementary_url(gse)
    session = _session()
    response = session.get(url, timeout=HTTP_TIMEOUT)
    if response.status_code == 404:
        raise PublicDataError(f'{gse} 没有补充文件目录。该研究可能只提供 SRA 原始数据。')
    response.raise_for_status()
    entries = _parse_ftp_index(response.text)
    files = []
    for entry in entries:
        name = str(entry['name'])
        if not any(name.lower().endswith(suffix) for suffix in IMPORTABLE_SUFFIXES):
            continue            # supplementary 里的 pdf/png/xlsx 等不属于可导入数据
        files.append(_file_record(name, url + name, 'series', gse, gse=gse,
                                  size_bytes=entry['size_bytes'],
                                  size_text=str(entry['size_text']),
                                  size_source=str(entry['size_source']),
                                  modified=str(entry['modified'])))
    return {
        'ok': True,
        'gse': gse,
        'dir_url': url,
        'files': files,
        'file_count': len(files),
        'total_bytes': sum(int(f['size_bytes'] or 0) for f in files),
        'total_bytes_known': all(f['size_bytes'] is not None for f in files),
        'note': '大小来自 NCBI 目录页（四舍五入）；勾选后下载前会用 HEAD 复核精确字节数。',
    }


def geo_sample_files(gsm: str) -> Dict[str, object]:
    """列出某个 GSM 的补充文件。很多样本没有独立 suppl 目录，需要回退到所属 Series。"""
    gsm = (gsm or '').strip().upper()
    if not re.match(r'^GSM\d{3,}$', gsm):
        raise PublicDataError('GEO 样本编号应形如 GSM7093919')
    url = geo_sample_url(gsm)
    session = _session()
    response = session.get(url, timeout=HTTP_TIMEOUT)
    if response.status_code == 404:
        return {'ok': True, 'gsm': gsm, 'dir_url': url, 'files': [], 'file_count': 0,
                'has_own_directory': False,
                'note': '该样本没有独立的 suppl 目录；文件通常挂在所属 Series 下。'}
    response.raise_for_status()
    files = []
    for entry in _parse_ftp_index(response.text):
        name = str(entry['name'])
        if not any(name.lower().endswith(suffix) for suffix in IMPORTABLE_SUFFIXES):
            continue
        files.append(_file_record(name, url + name, 'sample', gsm, gsm=gsm,
                                  size_bytes=entry['size_bytes'],
                                  size_text=str(entry['size_text']),
                                  size_source=str(entry['size_source']),
                                  modified=str(entry['modified'])))
    return {'ok': True, 'gsm': gsm, 'dir_url': url, 'files': files,
            'file_count': len(files), 'has_own_directory': True}


def _eutils_summary(accession: str) -> Dict[str, object]:
    """用 esearch + esummary 取官方元数据，并**始终返回真正的 accession**。"""
    session = _session()
    search = session.get(EUTILS, params={
        'db': 'gds', 'term': f'{accession}[ACCN]', 'retmax': '5', 'retmode': 'json',
    }, timeout=HTTP_TIMEOUT)
    search.raise_for_status()
    ids = []
    try:
        ids = list(search.json().get('esearchresult', {}).get('idlist', []) or [])
    except Exception:
        ids = re.findall(r'<Id>(\d+)</Id>', search.text)
    if not ids:
        return {'ok': False, 'accession': accession, 'reason': 'esearch 未命中'}
    summary = session.get(EUTILS_SUMMARY, params={
        'db': 'gds', 'id': ','.join(ids), 'retmode': 'json',
    }, timeout=HTTP_TIMEOUT)
    summary.raise_for_status()
    payload = summary.json().get('result', {})
    for uid in ids:
        item = payload.get(uid) or {}
        if not isinstance(item, dict):
            continue
        acc = str(item.get('accession') or '').upper()
        if acc == accession.upper():
            return {
                'ok': True,
                'uid': uid,                       # GDS 内部 ID，仅用于查询，不能当 GSE 用
                'accession': acc,
                'title': item.get('title') or '',
                'summary': item.get('summary') or '',
                'taxon': item.get('taxon') or '',
                'entry_type': item.get('entryType') or '',
                'n_samples': item.get('n_samples'),
                'pdat': item.get('PDAT') or '',
                'gdstype': item.get('gdsType') or '',
                'source': 'NCBI E-utilities esummary(db=gds)',
            }
    first = payload.get(ids[0]) or {}
    return {'ok': True, 'uid': ids[0],
            'accession': str(first.get('accession') or accession),
            'title': str(first.get('title') or ''),
            'summary': str(first.get('summary') or ''),
            'taxon': str(first.get('taxon') or ''),
            'n_samples': first.get('n_samples'),
            'pdat': str(first.get('PDAT') or ''),
            'source': 'NCBI E-utilities esummary(db=gds)'}


def geo_manifest(gse: str) -> Dict[str, object]:
    """预览用的完整清单：官方元数据 + 文件列表（不下载数据）。"""
    gse = (gse or '').strip().upper()
    listing = geo_list_files(gse)
    info: Dict[str, object]
    try:
        info = _eutils_summary(gse)
    except Exception as exc:
        info = {'ok': False, 'accession': gse, 'reason': f'{type(exc).__name__}: {exc}'}
    return {
        'ok': True,
        'accession': gse,
        'accession_kind': 'GSE',
        'info': info,
        'files': listing['files'],
        'file_count': listing['file_count'],
        'total_bytes': listing['total_bytes'],
        'total_bytes_known': listing['total_bytes_known'],
        'dir_url': listing['dir_url'],
        'official_page': f'https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={gse}',
        'note': listing['note'],
    }


def geo_exact_size(url: str) -> Optional[int]:
    """用 HEAD 取精确字节数（不下载内容）。失败返回 None。"""
    session = _session()
    try:
        response = session.head(url, timeout=HTTP_TIMEOUT_SHORT, allow_redirects=True)
        if response.status_code >= 400:
            return None
        length = response.headers.get('content-length')
        return int(length) if length is not None else None
    except Exception:
        return None


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
    if not ids:
        return []
    # GDS 的 UID 只是内部检索号，**不能**当 GSE 编号去下载。
    # 必须再取一次 esummary，用返回里的 accession（GSE…）作为唯一身份。
    results: List[SearchResult] = []
    try:
        summary = session.get(EUTILS_SUMMARY, params={
            'db': 'gds', 'id': ','.join(ids[:max_results]), 'retmode': 'json',
        }, timeout=HTTP_TIMEOUT)
        summary.raise_for_status()
        payload = summary.json().get('result', {})
    except Exception:
        payload = {}
    for ident in ids[:max_results]:
        item = payload.get(ident) or {}
        accession = str(item.get('accession') or '').upper()
        title = str(item.get('title') or '').strip()
        if not accession:
            continue                      # 拿不到真实编号就不返回，避免用户误用内部 ID
        results.append(SearchResult(
            source='geo',
            dataset_id=accession,
            title=title or accession,
            url=f'https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={accession}',
            extra={
                'uid': ident,             # 仅作调试用，不能用于下载
                'taxon': str(item.get('taxon') or ''),
                'n_samples': str(item.get('n_samples') or ''),
                'type': str(item.get('entryType') or ''),
            }))
    return results


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


def _read_table_matrix(path: Path, chunk_rows: int = 8192):
    """读 CSV/TSV 表达矩阵（第一列是细胞 ID，其余数值列是基因）。

    GEO 上大量经典数据集（例如 Baron 胰腺）就是这种格式，而不是 10x 三件套。

    **内存**：旧实现一次性把整张表读成 float64 DataFrame，再 astype(float32)
    再 to_numpy 再转稀疏，峰值约为 4.5 倍稠密体积。GSE72857 的 27297×10368
    表因此在本机实测触发 MemoryError（连 213 KiB 的小数组都分配不出来）。
    现在改为：先读少量行判定列类型 -> 分块读取基因列并即时转稀疏 -> 纵向拼装，
    峰值只与「块大小 × 基因数」和最终 nnz 有关。
    """
    import anndata as ad
    import pandas as pd
    from scipy import sparse
    sep = ',' if '.csv' in path.name.lower() else '\t'
    try:
        head = pd.read_csv(path, sep=sep, index_col=0, nrows=200, low_memory=False)
    except Exception:
        return None
    if head.shape[0] < 2 or head.shape[1] < 2:
        return None
    obs_cols = [c for c in head.columns
                if str(c).lower() in META_COLUMNS or not pd.api.types.is_numeric_dtype(head[c])]
    gene_cols = [c for c in head.columns if c not in obs_cols]
    if len(gene_cols) < 10:
        return None
    blocks = []
    cell_ids = []
    for chunk in pd.read_csv(path, sep=sep, index_col=0,
                             chunksize=max(512, int(chunk_rows)), low_memory=False):
        if gene_cols[0] not in chunk.columns:
            continue
        cell_ids.extend([str(item) for item in chunk.index])
        values = (chunk[gene_cols]
                  .apply(pd.to_numeric, errors='coerce')
                  .fillna(0.0)
                  .to_numpy(dtype=np.float32))
        blocks.append(sparse.csr_matrix(values))
    if not blocks:
        return None
    matrix = blocks[0] if len(blocks) == 1 else sparse.vstack(blocks, format='csr')
    adata = ad.AnnData(X=matrix,
                       obs=pd.DataFrame(index=pd.Index(cell_ids, dtype=str)),
                       var=pd.DataFrame(index=pd.Index([str(g) for g in gene_cols], dtype=str)))
    if obs_cols:
        try:
            meta = pd.read_csv(path, sep=sep, index_col=0, usecols=[index_name] + obs_cols
                               if index_name else obs_cols, low_memory=False)
            for column in obs_cols:
                if column in meta.columns:
                    adata.obs[str(column)] = meta[column].astype(str).to_numpy()
        except Exception:
            pass
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
    # P3-02：解压/转换的大文件必须落在用户数据根目录下的 staging，
    # 不能落到系统盘 %TEMP%（默认在 C 盘）。
    workdir = paths.make_workdir(f'cellflow_geo_{gse}_', kind='conversion')

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
        # 修复：旧代码在 out 赋值前引用它（UnboundLocalError 被 except 吞掉），
        # 导致「多物种只保留一组」的元数据从未真正写出。
        if dropped:
            try:
                meta_path = out.with_name(out.name + '.meta.json')
                meta_path.write_text(json.dumps({
                    'species_kept': keep,
                    'species_dropped': {key: int(value) for key, value in dropped.items()},
                    'samples_total': int(len(collected)),
                    'samples_kept': int(len(kept)),
                }, ensure_ascii=False, indent=2), encoding='utf-8')
            except Exception:
                pass
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
        outputs = geo_fetch(identifier, outdir, progress)
    elif source in ('cellxgene', 'cxg'):
        outputs = cellxgene_fetch(identifier, outdir, progress, species)
    elif source == 'url':
        outputs = url_fetch(identifier, outdir, progress)
    else:
        raise PublicDataError(f'未知来源：{source}（可用：{", ".join(SOURCES)}）')
    return Path(outputs[0])




# ---------------------------------------------------------------- P3-01 下载
def _read_loom_gz(local: Path, workdir: Path):
    """读取 .loom.gz：在数据根 staging 里解压后交给 loom 读取器。

    解压产生的 .loom 与压缩包同量级，必须落在 staging（P3-02），
    不能写 %TEMP%。调用方负责清理 workdir。
    """
    import anndata as ad
    local = Path(local)
    target = Path(workdir) / (local.name[:-3] if local.name.lower().endswith('.gz') else local.name + '.loom')
    target.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(local, 'rb') as source, open(target, 'wb') as sink:
        shutil.copyfileobj(source, sink, length=1024 * 1024)
        if _cancelled():
            raise DownloadCancelled('用户取消下载')
    if target.stat().st_size == 0:
        raise PublicDataError('loom.gz 解压后为空，文件可能已损坏')
    return [(local.name[:60], ad.read_loom(str(target)))]


def _items_from_file(name: str, local: Path, workdir: Path, soft: Dict[str, Dict[str, str]]):
    """把一个已下载文件解析成 [(样本名, AnnData)]；返回 (items, 错误说明)。"""
    import anndata as ad
    target_dir = Path(workdir) / (name + '_x')
    try:
        low = name.lower()
        if low.endswith(('.tar', '.tar.gz', '.tgz', '.zip')):
            _extract_archive(local, target_dir)
            items = _load_all_matrices(target_dir)
        elif low.endswith('.loom.gz'):
            items = _read_loom_gz(local, target_dir)
        elif low.endswith('.loom'):
            items = [(Path(name).stem, ad.read_loom(str(local)))]
        elif low.endswith('.h5ad'):
            items = [(Path(name).stem, ad.read_h5ad(local))]
        elif low.endswith('.h5'):
            import scanpy as sc
            items = [(Path(name).stem, sc.read_10x_h5(str(local)))]
        elif _looks_like_table(local):
            items = [(Path(name).stem, _read_table_matrix(local))]
        else:
            items = _load_all_matrices(Path(workdir))
    except DownloadCancelled:
        raise
    except MemoryError:
        return [], ('MemoryError: 解析这个文件所需内存超过本机可用内存。'
                    '请改用该研究提供的 .h5ad/.loom，或在大内存机器上转换后再导入。')
    except Exception as exc:
        return [], f'{type(exc).__name__}: {exc}'
    return [(n, a) for n, a in items if a is not None], ''


def _series_soft(gse: str, workdir: Path) -> Dict[str, Dict[str, str]]:
    try:
        soft_path = Path(workdir) / f'{gse}_family.soft.gz'
        download_file(geo_soft_url(gse), soft_path)
        with gzip.open(soft_path, 'rt', encoding='utf-8', errors='replace') as handle:
            return _parse_soft(handle.read())
    except DownloadCancelled:
        raise
    except Exception:
        return {}


def geo_fetch_selected(gse: str, urls: List[str], outdir: Path,
                       progress: Optional[Callable[[str], None]] = None,
                       verify_sizes: bool = True) -> List[Path]:
    """只下载用户明确选中的补充文件（P3-01 的核心）。

    与 geo_fetch 的区别：**不遍历整个 Series**，只处理传入的 URL 列表。
    返回可导入的 h5ad 路径列表；单个文件对应一个 h5ad，多个文件按样本合并。
    """
    import anndata as ad
    gse = (gse or '').strip().upper()
    if not re.match(r'^GSE\d{3,}$', gse):
        raise PublicDataError('GEO 只接受 GSE 系列编号，例如 GSE84133')
    chosen = [str(u).strip() for u in (urls or []) if str(u).strip()]
    if not chosen:
        raise PublicDataError('没有选择任何文件：请先在文件清单里勾选要下载的文件。')
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    def say(message):
        if progress:
            progress(message)

    # 下载前按已知大小做一次总空间预检查（避免下到一半才发现空间不足）
    known = [geo_exact_size(url) for url in chosen] if verify_sizes else []
    total = sum(size for size in known if size)
    if total:
        paths.ensure_free_space(outdir, total, label=f'{gse} 的 {len(chosen)} 个文件')

    workdir = paths.make_workdir(f'cellflow_geo_{gse}_sel', kind='conversion')
    try:
        soft = _series_soft(gse, workdir)
        collected = []
        failures = []
        for url in chosen:
            name = url.rstrip('/').split('/')[-1]
            say(f'下载 {name}')
            local = workdir / name
            try:
                download_file(url, local)
            except DownloadCancelled:
                raise
            except Exception as exc:
                failures.append({'file': name, 'stage': 'download',
                                 'error': f'{type(exc).__name__}: {exc}'})
                say(f'下载失败 {name}：{exc}')
                continue
            items, error = _items_from_file(name, local, workdir, soft)
            if not items:
                failures.append({'file': name, 'stage': 'parse', 'error': error or '没有可识别的表达矩阵'})
                say(f'跳过 {name}：{error or "没有可识别的表达矩阵"}')
                continue
            for item_name, adata in items:
                collected.append((item_name, adata, _sample_species(item_name, soft)))
        if not collected:
            detail = '；'.join(f"{f['file']}（{f['stage']}）：{f['error']}" for f in failures) or '没有可用文件'
            raise PublicDataError(f'{gse} 选中的文件无法导入：{detail}')

        by_species: Dict[str, List] = {}
        for item in collected:
            by_species.setdefault(item[2] or 'unknown', []).append(item)
        keep = max(by_species, key=lambda key: sum(int(a.n_obs) for _, a, _ in by_species[key]))
        kept = by_species[keep]
        names = [item[0] for item in kept]
        adatas = [item[1] for item in kept]
        species = '' if keep == 'unknown' else keep

        stem = Path(chosen[0]).name
        for suffix in ('.loom.gz', '.h5ad', '.loom', '.h5', '.mtx.gz', '.txt.gz', '.csv', '.tsv', '.gz'):
            if stem.lower().endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        stem = re.sub(r'[^A-Za-z0-9._-]+', '_', stem)[:60]
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
        out = outdir / f'geo_{gse}_{stem}.h5ad'
        merged.write_h5ad(out)
        try:
            meta_path = out.with_name(out.name + '.meta.json')
            meta_path.write_text(json.dumps({
                'selected_files': [Path(u).name for u in chosen],
                'downloaded_files': [Path(u).name for u in chosen if Path(u).name not in
                                     {f['file'] for f in failures if f['stage'] == 'download'}],
                'failures': failures,
                'file_sizes': {Path(u).name: size for u, size in zip(chosen, known) if size},
                'selection_source': 'user_selected',
            }, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass
        say(f'已保存 {out}')
        return [out]
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


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
