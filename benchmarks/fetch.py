"""有界下载、断点续传、SHA-256 与 AnnData 完整性校验。

设计原则：
- 先写 `.part`，校验通过后才原子改名为正式文件；
- 支持 HTTP Range 续传；失败重试有限次，绝不无限重试；
- 已存在且哈希匹配时直接复用（本地缓存）；
- 任何截断/哈希不符都报错，绝不把半成品交给计算。
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
import time
import re

import requests

CHUNK = 1024 * 1024


class IntegrityError(ValueError):
    """下载内容与预期不符；重试无意义，直接失败。"""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b''):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, target: Path, expected_sha256=None, expected_bytes=None,
             retries: int = 3, timeout=(20, 120), progress=None,
             max_bytes=1024 ** 3, total_timeout=600) -> dict:
    """下载到 target；返回 {path, sha256, bytes, downloaded, reused}。"""
    target = Path(target)
    if retries < 1 or max_bytes <= 0 or total_timeout <= 0:
        raise ValueError('重试、大小上限、总时间必须为正')
    if expected_bytes is not None and not 0 < int(expected_bytes) <= max_bytes:
        raise ValueError('预期大小超出下载上限')
    deadline = time.monotonic() + total_timeout
    def check_time():
        if time.monotonic() >= deadline:
            raise IntegrityError('下载总时间超限；保留临时文件')
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        digest = sha256_file(target)
        if (not expected_sha256 or digest == expected_sha256) and \
                (not expected_bytes or target.stat().st_size == int(expected_bytes)):
            _validate_h5ad(target)
            return {'path': str(target), 'sha256': digest, 'bytes': target.stat().st_size,
                    'downloaded': 0, 'reused': True}
        raise ValueError(f'已存在文件与预期哈希/大小不符，拒绝静默覆盖：{target}')

    part = target.with_name(target.name + '.part')
    last_error = None
    for attempt in range(1, int(retries) + 1):
        check_time()
        existing = part.stat().st_size if part.exists() else 0
        limit = min(max_bytes, int(expected_bytes)) if expected_bytes else max_bytes
        if existing > limit:
            raise IntegrityError('临时文件超过大小上限')
        headers = {'Accept-Encoding': 'identity'}
        if existing:
            headers['Range'] = f'bytes={existing}-'
        try:
            remaining = max(0.001, deadline - time.monotonic())
            bounded_timeout = tuple(min(float(t), remaining) for t in timeout) if isinstance(timeout, tuple) else min(float(timeout), remaining)
            with requests.get(url, stream=True, headers=headers, timeout=bounded_timeout,
                              allow_redirects=True) as response:
                if response.status_code == 416 and existing:
                    match = re.fullmatch(r'bytes \*/(\d+)', response.headers.get('Content-Range', ''))
                    if not match or int(match[1]) != existing or not expected_sha256:
                        raise IntegrityError('416 无法证明临时文件完整')
                elif response.status_code not in (200, 206):
                    raise RuntimeError(f'HTTP {response.status_code}：{url}')
                else:
                    check_time()
                    announced = response.headers.get('Content-Length')
                    if announced is not None and int(announced) > limit:
                        raise IntegrityError('响应超过大小上限')
                    expected_response = None
                    if response.status_code == 206:
                        match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                        if not match:
                            raise IntegrityError('206 缺失/非法 Content-Range')
                        start, end, total = map(int, match.groups())
                        if start != existing or end < start or end != total - 1 or total > limit:
                            raise IntegrityError('206 范围与续传偏移或上限不符')
                        if expected_bytes is not None and total != int(expected_bytes):
                            raise IntegrityError('206 总大小不符')
                        expected_response = end - start + 1
                    mode = 'ab' if (existing and response.status_code == 206) else 'wb'
                    received = 0
                    with open(part, mode) as handle:
                        # read1 returns available bytes instead of buffering a whole CHUNK,
                        # so a slow trickle cannot postpone the wall-clock deadline check.
                        while True:
                            chunk = response.raw.read1(CHUNK, decode_content=True)
                            check_time()
                            if not chunk:
                                break
                            if chunk:
                                if handle.tell() + len(chunk) > limit:
                                    raise IntegrityError('响应超过大小上限')
                                handle.write(chunk)
                                received += len(chunk)
                                if progress:
                                    progress(handle.tell())
                    if expected_response is not None and received != expected_response:
                        raise IntegrityError('206 响应截断或范围长度不符')
            check_time()
            if expected_bytes and part.stat().st_size != int(expected_bytes):
                raise IntegrityError(f'大小不符：{part.stat().st_size} != {expected_bytes}')
            digest = sha256_file(part)
            if expected_sha256 and digest != expected_sha256:
                raise IntegrityError(f'SHA-256 不符：{digest} != {expected_sha256}')
            _validate_h5ad(part, format_suffix=target.suffix)
            check_time()
            # Never overwrite an existing published file, including one created concurrently.
            import os
            os.link(part, target)
            part.unlink()
            return {'path': str(target), 'sha256': digest, 'bytes': target.stat().st_size,
                    'downloaded': target.stat().st_size, 'reused': False}
        except IntegrityError:
            raise
        except Exception as exc:      # 有界重试；保留 .part 以便续传
            last_error = exc
            if attempt < retries:
                time.sleep(min(2 * attempt, 8))
    raise RuntimeError(f'下载失败（已重试 {retries} 次）：{last_error}; 残留 {part}')


def _validate_h5ad(path: Path, format_suffix=None):
    """只对 .h5ad 做 HDF5 可读性检查；其他格式交给调用方。"""
    if (format_suffix or path.suffix).lower() != '.h5ad':
        return
    import anndata as ad
    try:
        a = ad.read_h5ad(path)
        from .contracts import validate_adata
        report = validate_adata(a)
        if not report['ok']:
            raise ValueError(str(report['issues']))
    except Exception as exc:
        raise IntegrityError('H5AD 结构校验失败：' + str(exc)) from exc


def fetch(dataset: dict, cache_dir='data/benchmarks', progress=None) -> dict:
    """按 catalog 描述获取数据并写 manifest。"""
    cache = Path(cache_dir)
    target = cache / Path(dataset['url']).name
    result = download(dataset['url'], target,
                      expected_sha256=dataset.get('sha256'),
                      expected_bytes=dataset.get('bytes'),
                      progress=progress)
    manifest = {
        'dataset': dataset['key'],
        'source_url': dataset['url'],
        'expected_sha256_origin': dataset.get('sha256_origin', 'unspecified'),
        'citation': dataset['citation'],
        'license_note': dataset['license_note'],
        'sha256': result['sha256'],
        'bytes': result['bytes'],
        'reused': result['reused'],
        'fetched_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'file': str(target),
    }
    (cache / f"{dataset['key']}.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest
