#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GEO 探针：查询 -> 预览 -> 只取单个文件。

本脚本是 P3-01「GEO 文件清单与元数据协议」的参考实现与证据生成器，
只依赖标准库 + requests（读 h5ad/loom 内部结构时才需要 h5py，且是惰性导入）。

设计约束（与协议文档 docs/p3/geo-protocol.md 对应）
--------------------------------------------------
1. 元数据走 SOFT（acc.cgi?form=text&view=brief 或 GSE*_family.soft.gz），不走 HTML 抓取。
   HTML 目录列表只用于「文件清单兜底」，且它给出的 Size 是四舍五入值（113K/16M/1.6G），
   **不能**当作字节数使用 —— 精确字节数一律用 HEAD Content-Length 或 filelist.txt。
2. GDS（db=gds）的 UID 不是 GSE 号。esearch 返回的是 esummary 的 UID，
   必须取 esummary 里的 accession 并过滤 entrytype == 'GSE'。
3. 列出文件与大小不需要下载任何文件；只有显式 --download / verify 才落地数据。

用法
----
    python geo_probe.py gse GSE72857                 # Series 元数据 + 文件清单（含逐样本展开）
    python geo_probe.py gse GSE72857 --no-samples    # 只看 Series 级文件
    python geo_probe.py gsm GSM7093919               # Sample 元数据 + 自己的补充文件
    python geo_probe.py search "pancreas single cell" --max 20
    python geo_probe.py files GSE274591 --json       # 完整清单（JSON，含来源/大小/证据等级）
    python geo_probe.py verify data/_probe/x.loom.gz # 解析文件内部 -> 剪接证据等级
    python geo_probe.py download --url <URL> --out DIR   # 只下载单个指定文件

退出码：0 正常；2 参数/编号错误；3 网络或数据源错误。
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from typing import Dict, Iterable, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter

# ---------------------------------------------------------------- 常量

USER_AGENT = ('CellFlow/0.3 (GEO protocol probe; +https://github.com/cellflow) '
              'python-requests')
TIMEOUT_META = (10, 40)      # 元数据/目录列表：短超时，失败快
TIMEOUT_BIG = (10, 180)      # 大文件下载：只用于显式 --download
MAX_RETRIES = 3              # 有限次重试，不做无限退避

EUTILS = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/{fn}.fcgi'
GEO_ACC = 'https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi'
GEO_SERIES_DIR = 'https://ftp.ncbi.nlm.nih.gov/geo/series/{stub}/{acc}/suppl/'
GEO_SAMPLE_DIR = 'https://ftp.ncbi.nlm.nih.gov/geo/samples/{stub}/{acc}/suppl/'
GEO_FAMILY_SOFT = 'https://ftp.ncbi.nlm.nih.gov/geo/series/{stub}/{acc}/soft/{acc}_family.soft.gz'
PUBMED_URL = 'https://pubmed.ncbi.nlm.nih.gov/{pmid}/'

# 容器格式 -> (生物学内容, 推荐用途, 是否可能承载剪接信息)
TYPE_RULES: Tuple[Tuple[str, str], ...] = (
    ('_raw.tar', 'tar'), ('.tar.gz', 'tar'), ('.tgz', 'tar'), ('.tar', 'tar'),
    ('.loom.gz', 'loom'), ('.loom', 'loom'),
    ('.h5ad', 'h5ad'), ('.h5', 'h5'),
    ('.rds.gz', 'rds'), ('.rds', 'rds'), ('.rda', 'rds'),
    ('.mtx.gz', 'mtx'), ('.mtx', 'mtx'),
    ('.csv.gz', 'csv'), ('.csv', 'csv'),
    ('.tsv.gz', 'tsv'), ('.tsv', 'tsv'),
    ('.txt.gz', 'txt'), ('.txt', 'txt'),
    ('.bw', 'bigwig'), ('.bigwig', 'bigwig'),
    ('.bed.gz', 'bed'), ('.bed', 'bed'),
    ('.xls.gz', 'xls'), ('.xls', 'xls'),
    ('.zip', 'zip'), ('.gz', 'gz'),
)

# 剪接证据等级
GRADE_VERIFIED = 'verified_by_content'
GRADE_METADATA = 'metadata_supported'
GRADE_FILENAME = 'filename_only'
GRADE_ABSENT = 'absent'
GRADE_ORDER = (GRADE_VERIFIED, GRADE_METADATA, GRADE_FILENAME, GRADE_ABSENT)


class GeoProbeError(RuntimeError):
    """协议层错误（编号不合法、端点返回异常等）。"""


# ---------------------------------------------------------------- HTTP

def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({'User-Agent': USER_AGENT, 'Accept-Encoding': 'gzip, deflate'})
    try:
        from urllib3.util.retry import Retry
        retry = Retry(total=MAX_RETRIES, connect=MAX_RETRIES, read=MAX_RETRIES,
                      backoff_factor=0.6, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=frozenset(['GET', 'HEAD']), raise_on_status=False)
        adapter = HTTPAdapter(max_retries=retry)
    except Exception:  # pragma: no cover - 老版本 urllib3
        adapter = HTTPAdapter()
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session


SESSION = make_session()


def _get(url: str, *, params=None, timeout=TIMEOUT_META, allow_redirects=True) -> requests.Response:
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = SESSION.get(url, params=params, timeout=timeout,
                               allow_redirects=allow_redirects)
            if resp.status_code >= 500:
                last = GeoProbeError(f'HTTP {resp.status_code} for {url}')
            else:
                return resp
        except requests.RequestException as exc:
            last = exc
        if attempt < MAX_RETRIES - 1:
            time.sleep(0.7 * (attempt + 1))
    raise GeoProbeError(f'请求失败（已重试 {MAX_RETRIES} 次）：{url} :: {last}')


def _text(resp: requests.Response) -> str:
    """GEO 的 Content-Type 是 geo/text（无 charset），requests 会退回 ISO-8859-1，
    直接把 UTF-8 字节显示成乱码（例如 Yaâ\u0080\u0099ara）。这里强制按 UTF-8 解码。"""
    if resp.encoding and resp.encoding.lower() not in ('iso-8859-1', 'latin-1', 'ascii'):
        return resp.text
    return resp.content.decode('utf-8', errors='replace')


# ---------------------------------------------------------------- 编号与 URL

def _digits(acc: str, prefix: str) -> str:
    acc = (acc or '').strip().upper()
    if not acc.startswith(prefix):
        raise GeoProbeError(f'编号应以 {prefix} 开头，收到 {acc!r}')
    digits = acc[len(prefix):]
    if not digits.isdigit():
        raise GeoProbeError(f'{acc} 的数字部分不合法：{digits!r}')
    return digits


def series_stub(gse: str) -> str:
    """GSE72857 -> GSE72nnn；不足 3 位数字时补 nnn（GEO 目录分桶规则）。"""
    digits = _digits(gse, 'GSE')
    return 'GSE' + digits[:-3] + 'nnn' if len(digits) >= 3 else 'GSE' + 'nnn'


def sample_stub(gsm: str) -> str:
    digits = _digits(gsm, 'GSM')
    return 'GSM' + digits[:-3] + 'nnn' if len(digits) >= 3 else 'GSM' + 'nnn'


def series_dir_url(gse: str) -> str:
    return GEO_SERIES_DIR.format(stub=series_stub(gse), acc=gse.upper())


def sample_dir_url(gsm: str) -> str:
    return GEO_SAMPLE_DIR.format(stub=sample_stub(gsm), acc=gsm.upper())


def family_soft_url(gse: str) -> str:
    return GEO_FAMILY_SOFT.format(stub=series_stub(gse), acc=gse.upper())


# ---------------------------------------------------------------- 文件大小（三级降级）

def head_size(url: str) -> Tuple[Optional[int], str]:
    """精确字节数。降级顺序：HEAD Content-Length -> Range 0-0 的 Content-Range 总长。

    返回 (bytes, 来源标记)。目录列表的 K/M/G 只是四舍五入值，不在此函数内使用。

    注意：这里**必须**用 HEAD，不能为了省事用 GET —— GEO 上有 6 GB 级的 RAW.tar，
    一次 GET 就会把整个归档拉下来（实测会直接卡死探针）。
    降级路径也必须加 stream=True 并且只读头部，绝不把 body 读进内存。
    """
    try:
        resp = SESSION.head(url, timeout=TIMEOUT_META, allow_redirects=True)
        if resp.status_code == 200:
            length = resp.headers.get('Content-Length')
            if length:
                try:
                    return int(length), 'head'
                except ValueError:
                    pass
    except requests.RequestException:
        pass
    # 降级：只请求 1 个字节，从 Content-Range 读总长度（顺带确认服务端支持断点续传）
    try:
        rng = SESSION.get(url, headers={'Range': 'bytes=0-0'}, timeout=TIMEOUT_META, stream=True)
        try:
            content_range = rng.headers.get('Content-Range') or ''
            m = re.search(r'/(\d+)\s*$', content_range)
            if rng.status_code == 206 and m:
                return int(m.group(1)), 'range'
        finally:
            rng.close()
    except requests.RequestException:
        pass
    return None, 'unknown'


def gzip_isize(url: str, size_bytes: Optional[int] = None) -> Tuple[Optional[int], str]:
    """gzip 的 ISIZE（最后 4 字节）= 解压后字节数 mod 2^32。

    只下载 4 个字节就能给出解压预估 —— 这是本协议里「解压预估」列的最优来源。
    超过 4 GiB 的文件 ISIZE 会回绕，因此结果只作为参考值（见协议文档 §4）。
    """
    if size_bytes is not None and size_bytes < 8:
        return None, 'not-gzip'
    try:
        # stream=True：万一对端忽略 Range 返回 200，也最多只读几字节就掐断，
        # 绝不会把一个 GB 级 gz 整个拉下来。
        resp = SESSION.get(url, headers={'Range': 'bytes=-4'}, timeout=TIMEOUT_META, stream=True)
        try:
            if resp.status_code != 206:
                return None, 'range-unsupported'
            payload = resp.raw.read(4, decode_content=False)
        finally:
            resp.close()
        if len(payload) == 4:
            return struct.unpack('<I', payload)[0], 'gzip-isize'
    except (requests.RequestException, struct.error):
        pass
    return None, 'unavailable'


# ---------------------------------------------------------------- 目录列表（HTML 兜底）

_ROW_RE = re.compile(
    r'<a href="(?P<href>[^"?][^"]*)"[^>]*>(?P<name>[^<]*)</a>\s*'
    r'(?P<mtime>\d{4}-\d{2}-\d{2} \d{2}:\d{2}|\s*-\s*)\s+(?P<size>[\d.]+[KMGT]?|-)\s*$')


def dir_listing(url: str) -> List[Dict[str, str]]:
    """解析 Apache 风格的 FTP-over-HTTPS 目录页。仅作兜底：Size 是四舍五入值。"""
    resp = _get(url, timeout=TIMEOUT_META)
    if resp.status_code == 404:
        return []
    if resp.status_code != 200:
        raise GeoProbeError(f'目录列表 HTTP {resp.status_code}：{url}')
    body = _text(resp)
    block = body.split('<pre>')[-1].split('</pre>')[0] if '<pre>' in body else body
    rows: List[Dict[str, str]] = []
    for line in block.splitlines():
        if 'Parent Directory' in line:
            continue
        m = _ROW_RE.search(line.rstrip())
        if not m:
            continue
        rows.append({'name': m.group('name').strip(), 'href': url + m.group('name').strip(),
                     'mtime': m.group('mtime').strip(), 'size_display': m.group('size')})
    return rows


# ---------------------------------------------------------------- filelist.txt 清单

def parse_filelist(text: str) -> List[Dict[str, object]]:
    """GEO 自动生成的 filelist.txt：Tab 分隔，Size 是**精确字节数**。

    格式：
        #Archive/File\tName\tTime\tSize\tType
        Archive\tGSE227256_RAW.tar\t06/17/2023 09:57:00\t20080640\tTAR
        File\tGSM7093919_kdm6bwt.loom.gz\t03/13/2023 17:55:09\t10159290\tLOOM
    """
    rows: List[Dict[str, object]] = []
    for line in text.splitlines():
        line = line.rstrip('\n')
        if not line or line.startswith('#') or line.startswith('Archive/File'):
            continue
        parts = line.split('\t')
        if len(parts) < 5:
            continue
        kind, name, mtime, size, ftype = (p.strip() for p in parts[:5])
        try:
            size_int = int(size)
        except ValueError:
            size_int = None
        rows.append({'role': kind.lower(), 'name': name, 'mtime': mtime,
                     'size_bytes': size_int, 'type': ftype})
    return rows


def fetch_filelist(acc: str, level: str = 'series') -> List[Dict[str, object]]:
    url = (sample_dir_url(acc) if level == 'sample' else series_dir_url(acc)) + 'filelist.txt'
    resp = _get(url, timeout=TIMEOUT_META)
    if resp.status_code != 200:
        return []
    return parse_filelist(_text(resp))


# ---------------------------------------------------------------- SOFT 元数据

_FAMILY_SOFT_CACHE: Dict[str, str] = {}


def fetch_soft(acc: str, family: bool = False) -> str:
    """取 SOFT 文本。

    family=True 时用 GSE*_family.soft.gz（一次请求拿到 Series + 全部 Sample，体积仅数 KB）；
    否则用 acc.cgi?targ=self&form=text&view=brief（单条记录）。
    """
    if family:
        key = acc.upper()
        if key in _FAMILY_SOFT_CACHE:   # 同一 GSE 的 series_metadata 与样本展开各要一次，缓存避免重复请求
            return _FAMILY_SOFT_CACHE[key]
        resp = _get(GEO_FAMILY_SOFT.format(stub=series_stub(key), acc=key), timeout=TIMEOUT_META)
        if resp.status_code != 200:
            raise GeoProbeError(f'family SOFT HTTP {resp.status_code}：{acc}')
        try:
            text = gzip.decompress(resp.content).decode('utf-8', errors='replace')
        except OSError as exc:
            raise GeoProbeError(f'family SOFT 解压失败：{exc}')
        _FAMILY_SOFT_CACHE[key] = text
        return text
    resp = _get(GEO_ACC, params={'acc': acc.upper(), 'targ': 'self',
                                 'form': 'text', 'view': 'brief'}, timeout=TIMEOUT_META)
    if resp.status_code != 200:
        raise GeoProbeError(f'acc.cgi HTTP {resp.status_code}：{acc}')
    return _text(resp)


@dataclass
class SoftRecord:
    kind: str                    # 'SERIES' / 'SAMPLE'
    accession: str
    fields: Dict[str, List[str]] = field(default_factory=dict)

    def one(self, key: str, default: str = '') -> str:
        values = self.fields.get(key) or []
        return values[0] if values else default

    def many(self, key: str) -> List[str]:
        return list(self.fields.get(key) or [])


def parse_soft(text: str) -> List[SoftRecord]:
    """按 ^SERIES/^SAMPLE 分段；键去掉前缀，允许同键多次出现（characteristics 等）。"""
    records: List[SoftRecord] = []
    current: Optional[SoftRecord] = None
    prefix = ''
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith('^SERIES'):
            prefix = 'Series_'
            body = line.split('=', 1)[-1].strip()
            current = SoftRecord('SERIES', body)
            records.append(current)
        elif line.startswith('^SAMPLE'):
            prefix = 'Sample_'
            body = line.split('=', 1)[-1].strip()
            current = SoftRecord('SAMPLE', body)
            records.append(current)
        elif line.startswith('!') and current is not None:
            key, _, value = line[1:].partition('=')
            key = key.strip()
            if key.startswith(prefix):
                key = key[len(prefix):]
            current.fields.setdefault(key, []).append(value.strip())
    return records


def supplementary_urls(record: SoftRecord) -> List[str]:
    """取一个记录声明的补充文件 URL。

    SOFT 的键名带序号：!Sample_supplementary_file_1 / _2 / ... / _n。
    早期只读 _1 会漏掉一个样本的多个文件（实测 GSE274591 每样本 4~5 个，
    只读 _1 会漏 11 个），因此这里按前缀收集全部序号。
    """
    urls: List[str] = []
    for key, values in record.fields.items():
        low = key.lower()
        if low == 'supplementary_file' or re.fullmatch(r'supplementary_file_\d+', low):
            urls.extend(values)
    return [u for u in (_ftp_to_https(u.strip()) for u in urls) if u]


def _ftp_to_https(url: str) -> str:
    if url.startswith('ftp://ftp.ncbi.nlm.nih.gov/'):
        return 'https://ftp.ncbi.nlm.nih.gov/' + url[len('ftp://ftp.ncbi.nlm.nih.gov/'):]
    return url


_CHAR_TISSUE_KEYS = ('tissue', 'organ', 'source name')


def _pick_characteristic(characteristics: Iterable[str], wanted: str) -> List[str]:
    out = []
    for item in characteristics:
        key, _, value = item.partition(':')
        if key.strip().lower() == wanted:
            out.append(value.strip())
    return sorted(set(x for x in out if x))


def series_metadata(gse: str) -> Dict[str, object]:
    """Series 层元数据。SOFT 给字段；esummary 只用于补 PubMed/平台/公共日期等。"""
    text = fetch_soft(gse, family=True)
    records = parse_soft(text)
    series = next((r for r in records if r.kind == 'SERIES'), None)
    if series is None:
        raise GeoProbeError(f'{gse} 的 SOFT 里没有 ^SERIES 段（编号是否存在？）')
    samples = [r for r in records if r.kind == 'SAMPLE']

    organism = sorted({o for s in samples for o in s.many('organism_ch1')})
    characteristics = [c for s in samples for c in s.many('characteristics_ch1')]
    tissue = sorted({t for t in (_pick_characteristic(characteristics, 'tissue')
                                 + _pick_characteristic(characteristics, 'organ'))})
    source_names = sorted({c for s in samples for c in s.many('source_name_ch1')})
    instruments = sorted({i for s in samples for i in s.many('instrument_model')})
    pubmed = series.many('pubmed_id')
    relations = series.many('relation')
    bioproject = ''
    for rel in relations:
        if rel.lower().startswith('bioproject'):
            bioproject = rel.split(':', 1)[-1].strip()
    return {
        'accession': series.accession,
        'title': series.one('title'),
        'organism': organism or [series.one('organism')] if (organism or series.one('organism')) else [],
        'tissue': tissue,
        'source_name': source_names,
        'technology': series.one('type'),
        'platform': series.one('platform_id'),
        'instrument_models': instruments,
        'n_samples': len(samples),
        'sample_ids': [s.accession for s in samples],
        'status': series.one('status'),
        'submission_date': series.one('submission_date'),
        'last_update_date': series.one('last_update_date'),
        'pubmed_ids': pubmed,
        'pubmed_urls': [PUBMED_URL.format(pmid=p) for p in pubmed],
        'bioproject': bioproject,
        'summary': series.one('summary'),
        'overall_design': series.one('overall_design'),
        'contributors': series.many('contributor'),
        'relations': relations,
        'supplementary_files_declared': supplementary_urls(series),
        'metadata_source': 'SOFT(Series, family.soft.gz)',
    }


def sample_metadata(gsm: str) -> Dict[str, object]:
    text = fetch_soft(gsm)
    records = parse_soft(text)
    sample = next((r for r in records if r.kind == 'SAMPLE'), None)
    if sample is None:
        raise GeoProbeError(f'{gsm} 的 SOFT 里没有 ^SAMPLE 段（编号是否存在？）')
    characteristics = sample.many('characteristics_ch1')
    parsed_chars: Dict[str, List[str]] = {}
    for item in characteristics:
        key, _, value = item.partition(':')
        parsed_chars.setdefault(key.strip(), []).append(value.strip())
    suppl = supplementary_urls(sample)
    return {
        'accession': sample.accession,
        'title': sample.one('title'),
        'organism': sample.one('organism_ch1'),
        'taxid': sample.one('taxid_ch1'),
        'source_name': sample.one('source_name_ch1'),
        'characteristics': parsed_chars,
        'molecule': sample.one('molecule_ch1'),
        'instrument_model': sample.one('instrument_model'),
        'library_strategy': sample.one('library_strategy'),
        'description': sample.one('description'),
        'data_processing': sample.many('data_processing'),
        'platform': sample.one('platform_id'),
        'series_ids': sample.many('series_id'),
        'submission_date': sample.one('submission_date'),
        'last_update_date': sample.one('last_update_date'),
        'supplementary_files_declared': [u for u in suppl if u.upper() != 'NONE'],
        'supplementary_declared_none': bool(suppl) and suppl[0].upper() == 'NONE',
        'metadata_source': 'SOFT(Sample, acc.cgi?form=text&view=brief)',
    }


# ---------------------------------------------------------------- 检索（GDS UID != GSE）

def _esearch(term: str, retmax: int) -> List[str]:
    resp = _get(EUTILS.format(fn='esearch'), params={'db': 'gds', 'term': term,
                                                    'retmax': str(retmax), 'retmode': 'json'})
    resp.raise_for_status()
    return list(resp.json().get('esearchresult', {}).get('idlist', []))


def _esummary(ids: List[str]) -> Dict[str, Dict[str, object]]:
    if not ids:
        return {}
    resp = _get(EUTILS.format(fn='esummary'), params={'db': 'gds', 'id': ','.join(ids),
                                                      'retmode': 'json'})
    resp.raise_for_status()
    payload = resp.json().get('result', {})
    return {uid: payload[uid] for uid in ids if uid in payload}


def search_series(term: str, max_results: int = 20) -> List[Dict[str, object]]:
    """关键词检索 -> GSE 列表。

    重要：db=gds 的 esearch 返回的是 esummary UID，**不是** GSE 号；
    同一个 GSE 会命中 GSE/GPL/GSM 三种记录的多个 UID（GPL 记录的 gse 字段还会
    列出该平台下成千上万个 GSE），所以必须读 esummary 的 accession 并过滤 entrytype。
    """
    # 不要把用户输入硬套成 "...[All Fields]"：实测 'pancreatic islet single cell RNA-seq' 有 1042 条，
    # 加 [All Fields] 后变成 0 条（连字符短语被拆坏）。原样透传，字段限定由用户自己写。
    query = term.strip()
    ids = _esearch(query, max(10, min(200, max_results * 4)))
    summary = _esummary(ids)
    results: List[Dict[str, object]] = []
    for uid in ids:
        rec = summary.get(uid) or {}
        if str(rec.get('entrytype', '')).upper() != 'GSE':
            continue
        accession = str(rec.get('accession') or '')
        if not accession.startswith('GSE'):
            continue
        pubmed = rec.get('pubmedids') or []
        results.append({
            'uid': uid,
            'accession': accession,
            'title': rec.get('title') or '',
            'organism': rec.get('taxon') or '',
            'n_samples': rec.get('n_samples'),
            'technology': rec.get('gdstype') or '',
            'public_date': rec.get('pdat') or '',
            'pubmed_ids': pubmed,
            'pubmed_urls': [PUBMED_URL.format(pmid=p) for p in pubmed],
            'supplementary_type_hint': rec.get('suppfile') or '',
            'ftp_link': rec.get('ftplink') or '',
            'metadata_source': 'esummary(db=gds).accession',
        })
        if len(results) >= max_results:
            break
    return results


# ---------------------------------------------------------------- 类型判定与证据分级

def classify_type(name: str) -> str:
    low = name.lower()
    for suffix, kind in TYPE_RULES:
        if low.endswith(suffix):
            return kind
    if '_raw.tar' in low:
        return 'tar'
    return 'other'


def biological_content(ftype: str) -> str:
    return {
        'h5ad': 'AnnData 对象：表达矩阵 + obs/var 注释（可能含 layers）',
        'loom': 'loom(HDF5)：表达矩阵；速度分析用 loom 常含 spliced/unspliced/ambiguous 层',
        'h5': 'HDF5（多为 10x Cell Ranger 输出）',
        'mtx': '稀疏表达矩阵（10x/Matrix Market）',
        'rds': 'R 对象（Seurat/自定义），需 R 或 rds2py 才能读',
        'csv': '表格（常为 counts 矩阵）',
        'tsv': '表格（barcode/feature/matrix 分件）',
        'txt': '文本（常为设计表/UMI 表）',
        'tar': '归档：内部成员需解包后才能确认内容',
        'bigwig': '基因组信号轨道（非表达矩阵）',
        'bed': '区间文件',
        'xls': 'Excel 表格',
        'zip': '归档',
        'other': '未知',
    }.get(ftype, '未知')


def recommended_use(ftype: str) -> str:
    return {
        'h5ad': '直接读入 AnnData（首选）',
        'loom': '读入后按需取 spliced/unspliced；速度分析首选',
        'h5': '按 10x 格式读入',
        'mtx': '配 barcodes/features 组 10x 三件套后读入',
        'rds': '需 R 侧转换，Windows 端不推荐直接解析',
        'csv': '转 h5ad 后使用',
        'tsv': '多为分件，需与 matrix 配对',
        'txt': '通常仅作设计表/校验，不作表达矩阵',
        'tar': '先枚举成员，再只取需要的单个成员',
        'bigwig': '不用于表达矩阵流程',
        'bed': '不用于表达矩阵流程',
        'xls': '转表后使用',
        'zip': '解包后确认',
    }.get(ftype, '待确认')


_VELOCITY_NAME_TOKENS = ('velocyto', 'spliced', 'unspliced', 'loom', 'velocity', 'bam')


def splice_evidence_grade(name: str, ftype: str,
                          content_probe: Optional[Dict[str, object]] = None,
                          manifest_type: str = '') -> Tuple[str, str]:
    """剪接证据分级。返回 (等级, 理由)。

    判定优先级（只有真正解析过文件内部才允许 verified）：
      1. content_probe 解析出 spliced/unspliced 层 -> verified_by_content
      2. content_probe 解析过但没有剪接层      -> absent（已证否，这是 GSE274591 的情形）
      3. 未解析，但 GEO filelist.txt 的 Type 字段声明为 LOOM，或 Sample 的
         data_processing 明确写「loom files for RNA velocity」 -> metadata_supported
      4. 仅文件名含 velocyto/spliced/unspliced 等词    -> filename_only
      5. 其余                                          -> absent
    """
    if content_probe is not None:
        layers = [str(x).lower() for x in (content_probe.get('layers') or [])]
        if 'spliced' in layers and 'unspliced' in layers:
            return GRADE_VERIFIED, '已解析容器内部：layers 含 spliced + unspliced'
        if content_probe.get('layers') == [] or (content_probe.get('probed') and not layers):
            return GRADE_ABSENT, ('已解析容器内部：layers 为空' if content_probe.get('layers') == []
                                  else '已解析容器内部：未发现剪接层')
        return GRADE_ABSENT, '已解析容器内部：未发现 spliced/unspliced'
    low = name.lower()
    if ftype == 'loom' and manifest_type.upper() == 'LOOM':
        return GRADE_METADATA, 'GEO filelist.txt 的 Type 字段声明为 LOOM（结构化元数据，非文件名推断）'
    if any(tok in low for tok in _VELOCITY_NAME_TOKENS):
        return GRADE_FILENAME, '仅文件名含剪接相关词（velocyto/spliced/unspliced/loom/bam）'
    return GRADE_ABSENT, '文件名与元数据均无剪接线索'


# ---------------------------------------------------------------- 内容探针（h5py 惰性导入）

def probe_container(path: str) -> Dict[str, object]:
    """解析 h5ad / loom 内部结构。这是唯一能把证据标成 verified 的途径。"""
    import h5py  # 惰性导入：不用 --verify 时不依赖 h5py

    path = str(path)
    info: Dict[str, object] = {'path': path, 'probed': True, 'kind': None,
                              'layers': [], 'layer_shapes': {}, 'keys': [],
                              'matrix_shape': None, 'orientation': None}
    gz = path.lower().endswith('.gz')
    tmp_path = None
    if gz:
        tmp_path = path[:-3] + '.probe.tmp'
        with gzip.open(path, 'rb') as src, open(tmp_path, 'wb') as dst:
            while True:
                chunk = src.read(1 << 22)
                if not chunk:
                    break
                dst.write(chunk)
        target = tmp_path
    else:
        target = path
    try:
        with h5py.File(target, 'r') as handle:
            info['keys'] = list(handle.keys())
            if 'encoding-type' in handle.attrs or 'X' in handle.keys():
                info['kind'] = 'h5ad'
            if 'matrix' in handle.keys() and 'row_attrs' in handle.keys():
                info['kind'] = 'loom'
            if 'layers' in handle.keys() and isinstance(handle['layers'], h5py.Group):
                info['layers'] = list(handle['layers'].keys())
                shapes = {}
                for key in info['layers']:
                    obj = handle['layers'][key]
                    shapes[key] = list(getattr(obj, 'shape', ()) or ())
                info['layer_shapes'] = shapes
            if info['kind'] == 'loom':
                info['matrix_shape'] = list(handle['matrix'].shape)
                genes = handle['row_attrs']['Gene'].shape[0] if 'row_attrs' in handle and 'Gene' in handle['row_attrs'] else None
                cells = handle['col_attrs']['CellID'].shape[0] if 'col_attrs' in handle and 'CellID' in handle['col_attrs'] else None
                info['n_genes_row_attrs'] = genes
                info['n_cells_col_attrs'] = cells
                if genes is not None and cells is not None:
                    info['orientation'] = ('genes x cells（rows=基因，需转置成 cells x genes）'
                                           if genes == handle['matrix'].shape[0] else 'cells x genes')
            if info['kind'] == 'h5ad':
                for group in ('obs', 'var'):
                    if group in handle and isinstance(handle[group], h5py.Group):
                        info[f'{group}_keys'] = list(handle[group].keys())
                for group in ('obsm', 'varm', 'obsp', 'uns'):
                    if group in handle and isinstance(handle[group], h5py.Group):
                        info[f'{group}_keys'] = list(handle[group].keys())
                x = handle['X']
                info['X_shape'] = list(x.shape) if hasattr(x, 'shape') else 'sparse-group'
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    return info


# ---------------------------------------------------------------- 文件清单

@dataclass
class FileEntry:
    name: str
    level: str                 # Series / Sample / ArchiveMember
    owner: str                 # GSE / GSM / archive 名
    url: str
    ftype: str
    size_bytes: Optional[int] = None
    size_source: str = 'unknown'      # head / range / filelist / dirlisting(round)
    size_display: str = ''
    mtime: str = ''
    member_of: str = ''
    uncompressed_bytes: Optional[int] = None
    uncompressed_method: str = ''
    compression_ratio: Optional[float] = None
    biological_content: str = ''
    splice_evidence: str = GRADE_ABSENT
    splice_reason: str = ''
    recommended_use: str = ''
    download_state: str = 'not_downloaded'
    checksum: str = 'not_computed'
    metadata_source: str = ''
    downloadable: bool = True
    alternative_route: bool = False
    note: str = ''

    def as_dict(self) -> Dict[str, object]:
        return asdict(self)


def _human(n: Optional[int]) -> str:
    if n is None:
        return '?'
    units = ('B', 'KB', 'MB', 'GB', 'TB')
    value = float(n)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f'{value:.1f} {unit}'
        value /= 1024.0
    return f'{n} B'


def _fill_sizes(entries: List[FileEntry], parallel: int = 8) -> None:
    """并发 HEAD 补齐精确字节数（只对可单独下载的 URL 做；归档内成员用清单值）。"""
    targets = [e for e in entries if e.downloadable and e.size_bytes is None]
    if not targets:
        return

    def work(entry: FileEntry) -> None:
        size, source = head_size(entry.url)
        entry.size_bytes = size
        entry.size_source = source

    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        list(pool.map(work, targets))

    for entry in targets:
        # 注意：判据必须是文件名后缀，不能用 ftype —— .loom.gz/.txt.gz 的 ftype 是 loom/txt，
        # 早期写成 ftype == 'gz' 会让所有「有实义后缀的 gz」漏掉解压预估。
        if entry.size_bytes is not None and entry.name.lower().endswith('.gz'):
            raw, method = gzip_isize(entry.url, entry.size_bytes)
            if raw is not None:
                entry.uncompressed_bytes = raw
                entry.uncompressed_method = method
                if entry.size_bytes:
                    entry.compression_ratio = round(entry.size_bytes / max(raw, 1), 4)


def series_inventory(gse: str, expand_samples: bool = True, fill_size: bool = True) -> Dict[str, object]:
    """Series 级 + 逐 Sample 级 + 归档成员，全部对齐到同一张表。"""
    meta = series_metadata(gse)
    entries: List[FileEntry] = []

    # 1) Series 级：SOFT 的 !Series_supplementary_file 是权威清单
    declared = list(meta.get('supplementary_files_declared') or [])
    for url in declared:
        entries.append(FileEntry(name=url.rsplit('/', 1)[-1], level='Series', owner=meta['accession'],
                                 url=url, ftype=classify_type(url.rsplit('/', 1)[-1]),
                                 metadata_source='SOFT !Series_supplementary_file'))

    # 2) 目录列表兜底：能发现 SOFT 未声明的文件（如 filelist.txt），但 Size 是四舍五入值
    seen = {e.name for e in entries}
    try:
        for row in dir_listing(series_dir_url(gse)):
            name = row['name']
            if name in seen or name == 'filelist.txt':
                continue
            entries.append(FileEntry(name=name, level='Series', owner=meta['accession'],
                                     url=row['href'], ftype=classify_type(name),
                                     size_display=row['size_display'], size_source='dirlisting(round)',
                                     mtime=row['mtime'], metadata_source='HTTPS 目录列表(href)'))
            seen.add(name)
    except GeoProbeError:
        pass

    # 3) 逐 Sample 展开：family SOFT 一次请求就带回所有 !Sample_supplementary_file_*
    sample_entries: List[FileEntry] = []
    if expand_samples:
        text = fetch_soft(gse, family=True)
        for record in parse_soft(text):
            if record.kind != 'SAMPLE':
                continue
            urls = supplementary_urls(record)
            for url in urls:
                if url.upper() == 'NONE':
                    continue
                name = url.rsplit('/', 1)[-1]
                sample_entries.append(FileEntry(
                    name=name, level='Sample', owner=record.accession, url=url,
                    ftype=classify_type(name),
                    metadata_source='SOFT !Sample_supplementary_file_1 (family.soft.gz)'))
        entries.extend(sample_entries)

    # 4) 归档成员：filelist.txt 给出精确字节数与类型，但成员**不可单独下载**
    manifest_types: Dict[str, str] = {}
    archives = [e for e in entries if e.ftype == 'tar']
    extra_members: List[FileEntry] = []
    for archive in archives:
        try:
            rows = parse_filelist(_text(_get(archive.url.rsplit('/', 1)[0] + '/filelist.txt')))
        except GeoProbeError:
            rows = []
        for row in rows:
            name = str(row['name'])
            manifest_types.setdefault(name, str(row['type']))
            if row['role'] != 'file':
                continue
            entry = FileEntry(
                name=name, level='ArchiveMember', owner=archive.owner,
                url='', ftype=classify_type(name) or str(row['type']).lower(),
                size_bytes=row['size_bytes'], size_source='filelist',
                mtime=str(row['mtime']), member_of=archive.name, downloadable=False,
                metadata_source=f'filelist.txt({archive.name})',
                note=f'位于 {archive.name} 内；单独下载需解包，否则要整包下载')
            extra_members.append(entry)
        if rows:
            archive.note = f'归档内含 {sum(1 for r in rows if r["role"] == "file")} 个成员，见 filelist.txt'
    entries.extend(extra_members)

    # 同一逻辑文件可能既有 Sample 级直链、又出现在归档里。
    # 有直链时以直链为主路径（不用下整包），归档成员标成 alternative_route。
    direct_names = {e.name for e in entries if e.downloadable}
    for entry in entries:
        if entry.level == 'ArchiveMember' and entry.name in direct_names:
            entry.alternative_route = True
            entry.note = '同一文件另有 Sample 级直链，优先用直链，避免下载整个归档'

    if fill_size:
        _fill_sizes(entries)
    for entry in entries:
        entry.ftype = entry.ftype or classify_type(entry.name)
        entry.biological_content = biological_content(entry.ftype)
        entry.recommended_use = recommended_use(entry.ftype)
        entry.size_display = entry.size_display or _human(entry.size_bytes)
        grade, reason = splice_evidence_grade(entry.name, entry.ftype,
                                              manifest_type=manifest_types.get(entry.name, ''))
        entry.splice_evidence, entry.splice_reason = grade, reason

    by_name: Dict[str, List[FileEntry]] = {}
    for entry in entries:
        by_name.setdefault(entry.name, []).append(entry)
    duplicates = {name: [e.level for e in group] for name, group in by_name.items() if len(group) > 1}

    return {
        'series': meta,
        'files': [e.as_dict() for e in entries],
        'counts': {
            'series_level': sum(1 for e in entries if e.level == 'Series'),
            'sample_level': sum(1 for e in entries if e.level == 'Sample'),
            'archive_members': sum(1 for e in entries if e.level == 'ArchiveMember'),
            'total': len(entries),
            'unique_files': len(by_name),
            # 旧行为（下载整个 Series）的代价：Series 级文件 + 所有归档 + 各样本文件全下
            'naive_download_all_bytes': sum(
                e.size_bytes or 0 for e in entries if not e.alternative_route),
            'cheapest_single_file_rules': '同名文件优先取可直链的层级，归档仅在无直链时使用',
        },
        'same_file_multiple_routes': duplicates,
        'total_bytes_series': sum(e.size_bytes or 0 for e in entries
                                  if e.level == 'Series' and e.ftype != 'tar'),
        'endpoints': {
            'series_dir': series_dir_url(gse),
            'family_soft': family_soft_url(gse),
            'soft_series': f'{GEO_ACC}?acc={gse.upper()}&targ=self&form=text&view=brief',
        },
    }


def gsm_inventory(gsm: str, fill_size: bool = True) -> Dict[str, object]:
    meta = sample_metadata(gsm)
    entries: List[FileEntry] = []
    for url in meta.get('supplementary_files_declared') or []:
        entries.append(FileEntry(name=url.rsplit('/', 1)[-1], level='Sample', owner=meta['accession'],
                                 url=url, ftype=classify_type(url.rsplit('/', 1)[-1]),
                                 metadata_source='SOFT !Sample_supplementary_file_1'))
    seen = {e.name for e in entries}
    try:
        for row in dir_listing(sample_dir_url(gsm)):
            if row['name'] in seen or row['name'] == 'filelist.txt':
                continue
            entries.append(FileEntry(name=row['name'], level='Sample', owner=meta['accession'],
                                     url=row['href'], ftype=classify_type(row['name']),
                                     size_display=row['size_display'], size_source='dirlisting(round)',
                                     mtime=row['mtime'], metadata_source='HTTPS 目录列表(href)'))
            seen.add(row['name'])
    except GeoProbeError:
        pass
    if not entries and meta.get('supplementary_declared_none'):
        return {'sample': meta, 'files': [], 'note': 'SOFT 明确声明 !Sample_supplementary_file_1 = NONE',
                'endpoints': {'sample_dir': sample_dir_url(gsm)}}
    if fill_size:
        _fill_sizes(entries)
    # Sample 目录本身没有 filelist.txt，但母 Series 的 filelist.txt 会给同名成员标注 Type。
    # 用它把证据等级从 filename_only 提升到 metadata_supported（结构化元数据，仍不是内容验证）。
    manifest_types: Dict[str, str] = {}
    for series_id in (meta.get('series_ids') or [])[:2]:
        try:
            for row in fetch_filelist(str(series_id), level='series'):
                manifest_types[str(row['name'])] = str(row['type'])
        except GeoProbeError:
            continue
    for entry in entries:
        entry.ftype = entry.ftype or classify_type(entry.name)
        entry.biological_content = biological_content(entry.ftype)
        entry.recommended_use = recommended_use(entry.ftype)
        entry.size_display = entry.size_display or _human(entry.size_bytes)
        grade, reason = splice_evidence_grade(entry.name, entry.ftype,
                                              manifest_type=manifest_types.get(entry.name, ''))
        entry.splice_evidence, entry.splice_reason = grade, reason
    return {'sample': meta, 'files': [e.as_dict() for e in entries],
            'endpoints': {'sample_dir': sample_dir_url(gsm),
                          'soft_sample': f'{GEO_ACC}?acc={gsm.upper()}&targ=self&form=text&view=brief'}}


# ---------------------------------------------------------------- 下载（只下一个文件）

def download_one(url: str, out_dir: str, expected_size: Optional[int] = None,
                 resume: bool = True) -> Dict[str, object]:
    os.makedirs(out_dir, exist_ok=True)
    name = url.rsplit('/', 1)[-1]
    target = os.path.join(out_dir, name)
    have = os.path.getsize(target) if (resume and os.path.exists(target)) else 0
    if expected_size is not None and have == expected_size:
        return {'path': target, 'bytes': have, 'skipped': True}
    headers = {'Range': f'bytes={have}-'} if have else {}
    started = time.time()
    with SESSION.get(url, headers=headers, timeout=TIMEOUT_BIG, stream=True) as resp:
        resp.raise_for_status()
        mode = 'ab' if (have and resp.status_code == 206) else 'wb'
        if mode == 'wb':
            have = 0
        with open(target, mode) as handle:
            for chunk in resp.iter_content(1 << 20):
                if chunk:
                    handle.write(chunk)
                    have += len(chunk)
    return {'path': target, 'bytes': have, 'seconds': round(time.time() - started, 1),
            'matches_expected': (expected_size is None or have == expected_size)}


# ---------------------------------------------------------------- 输出

def print_series(meta: Dict[str, object]) -> None:
    print(f"== {meta['accession']}  {meta['title']}")
    print(f"   物种      : {', '.join(meta['organism']) or '-'}")
    print(f"   组织      : {', '.join(meta['tissue']) or '（SOFT 未提供，需人工）'}")
    print(f"   来源名    : {', '.join(meta['source_name'][:4]) or '-'}")
    print(f"   技术      : {meta['technology'] or '-'}  (平台 {meta['platform'] or '-'})")
    print(f"   仪器      : {', '.join(meta['instrument_models']) or '-'}")
    print(f"   样本数    : {meta['n_samples']}")
    print(f"   提交日期  : {meta['submission_date']}   公开: {meta['status']}")
    print(f"   PubMed    : {', '.join(meta['pubmed_urls']) or '无'}")
    print(f"   BioProject: {meta['bioproject'] or '-'}")


def print_files(files: List[Dict[str, object]]) -> None:
    if not files:
        print('   （没有补充文件）')
        return
    print(f"   {'层级':<15}{'所属':<12}{'类型':<7}{'字节':>13}{'解压后':>16}  证据等级            文件名")
    for item in files:
        size = item['size_bytes']
        size_text = f'{size:,}' if isinstance(size, int) else (item.get('size_display') or '?')
        raw = item.get('uncompressed_bytes')
        raw_text = f'{raw:,}' if isinstance(raw, int) else '-'
        flag = '*' if item.get('alternative_route') else ' '
        print(f"   {item['level']:<15}{item['owner']:<12}{item['ftype']:<7}{size_text:>13}{raw_text:>16}  "
              f"{item['splice_evidence']:<20}{flag}{item['name'][:60]}")


def emit(payload: Dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if 'series' in payload:
        print_series(payload['series'])  # type: ignore[arg-type]
        files = payload.get('files') or []
        print(f"\n== 文件清单（{len(files)} 项）")
        print_files(files)  # type: ignore[arg-type]
        counts = payload.get('counts') or {}
        if counts:
            print(f"\n   Series 级 {counts.get('series_level')} 个 / "
                  f"Sample 级 {counts.get('sample_level')} 个 / "
                  f"归档成员 {counts.get('archive_members')} 个")
        dup = payload.get('same_file_multiple_routes') or {}
        if dup:
            print(f"   同一文件多条路径（可选直链，避免下整包）：{len(dup)} 组")
            for name, levels in list(dup.items())[:6]:
                print(f"     - {name}: {levels}")
    elif 'sample' in payload:
        meta = payload['sample']
        print(f"== {meta['accession']}  {meta['title']}")  # type: ignore[index]
        print(f"   物种 {meta['organism']}   来源 {meta['source_name']}")  # type: ignore[index]
        chars = meta.get('characteristics') or {}  # type: ignore[union-attr]
        if chars:
            print(f"   特征 {dict(list(chars.items())[:6])}")
        print(f"   所属 Series: {', '.join(meta['series_ids']) or '-'}")  # type: ignore[index]
        print(f"\n== 该 Sample 的补充文件")
        print_files(payload.get('files') or [])  # type: ignore[arg-type]
        if payload.get('note'):
            print(f"   注：{payload['note']}")
    elif 'results' in payload:
        results = payload['results'] or []
        print(f"== 检索命中 {len(results)} 个 GSE")
        for item in results:  # type: ignore[union-attr]
            print(f"   {item['accession']:<12}{item.get('organism',''):<16}"
                  f"n={item.get('n_samples')!s:<5}{item.get('public_date','')}  {item['title'][:70]}")
            if item.get('pubmed_urls'):
                print(f"      PubMed: {', '.join(item['pubmed_urls'])}")
    elif 'probe' in payload:
        probe = payload['probe']
        print(f"== 内容探针 {probe['path']}")  # type: ignore[index]
        for key in ('kind', 'layers', 'layer_shapes', 'matrix_shape', 'orientation',
                    'obs_keys', 'obsm_keys', 'X_shape'):
            if key in probe:  # type: ignore[operator]
                print(f"   {key:<14}{probe[key]}")  # type: ignore[index]
        print(f"   剪接证据等级 : {payload['splice_evidence']}")
        print(f"   判定理由     : {payload['splice_reason']}")


# ---------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='GEO 探针：查询 -> 预览 -> 只取单个文件（不批量下载）',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command')

    p_gse = sub.add_parser('gse', help='Series 元数据 + 文件清单')
    p_gse.add_argument('accession')
    p_gse.add_argument('--no-samples', action='store_true', help='不展开 Sample 级文件')
    p_gse.add_argument('--no-size', action='store_true', help='不做 HEAD，只用清单里的字节数')
    p_gse.add_argument('--json', action='store_true')

    p_gsm = sub.add_parser('gsm', help='Sample 元数据 + 该样本的补充文件')
    p_gsm.add_argument('accession')
    p_gsm.add_argument('--no-size', action='store_true')
    p_gsm.add_argument('--json', action='store_true')

    p_search = sub.add_parser('search', help='关键词检索 GSE（esearch+esummary, db=gds）')
    p_search.add_argument('term')
    p_search.add_argument('--max', type=int, default=20)
    p_search.add_argument('--json', action='store_true')

    p_files = sub.add_parser('files', help='只要文件清单（= gse --json 的 files 部分）')
    p_files.add_argument('accession')
    p_files.add_argument('--no-samples', action='store_true')
    p_files.add_argument('--json', action='store_true')

    p_verify = sub.add_parser('verify', help='解析本地 h5ad/loom 内部结构并给出证据等级')
    p_verify.add_argument('path')
    p_verify.add_argument('--json', action='store_true')

    p_dl = sub.add_parser('download', help='只下载一个指定文件（支持断点续传）')
    p_dl.add_argument('--url', required=True)
    p_dl.add_argument('--out', default='data/_probe')
    p_dl.add_argument('--expect-size', type=int, default=None)
    p_dl.add_argument('--json', action='store_true')
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    try:
        if args.command in ('gse', 'files'):
            payload = series_inventory(args.accession.upper(),
                                       expand_samples=not args.no_samples,
                                       fill_size=not getattr(args, 'no_size', False))
            if args.command == 'files' and not args.json:
                print_files(payload['files'])  # type: ignore[arg-type]
                return 0
            emit(payload, args.json)
        elif args.command == 'gsm':
            payload = gsm_inventory(args.accession.upper(), fill_size=not args.no_size)
            emit(payload, args.json)
        elif args.command == 'search':
            payload = {'results': search_series(args.term, args.max), 'query': args.term}
            emit(payload, args.json)
        elif args.command == 'verify':
            probe = probe_container(args.path)
            grade, reason = splice_evidence_grade(os.path.basename(args.path),
                                                  classify_type(args.path), content_probe=probe)
            emit({'probe': probe, 'splice_evidence': grade, 'splice_reason': reason}, args.json)
        elif args.command == 'download':
            result = download_one(args.url, args.out, expected_size=args.expect_size)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"下载完成：{result['path']}  {result['bytes']:,} 字节")
            return 0
    except GeoProbeError as exc:
        print(f'错误：{exc}', file=sys.stderr)
        return 3
    except requests.RequestException as exc:
        print(f'网络错误：{exc}', file=sys.stderr)
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
