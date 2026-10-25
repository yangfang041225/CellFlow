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
             retries: int = 3, timeout=(20, 120), progress=None) -> dict:
    """下载到 target；返回 {path, sha256, bytes, downloaded, reused}。"""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        digest = sha256_file(target)
        if (not expected_sha256 or digest == expected_sha256) and \
                (not expected_bytes or target.stat().st_size == int(expected_bytes)):
            return {'path': str(target), 'sha256': digest, 'bytes': target.stat().st_size,
                    'downloaded': 0, 'reused': True}
        raise ValueError(f'已存在文件与预期哈希/大小不符，拒绝静默覆盖：{target}')

    part = target.with_name(target.name + '.part')
    last_error = None
    for attempt in range(1, int(retries) + 1):
        existing = part.stat().st_size if part.exists() else 0
        headers = {'Range': f'bytes={existing}-'} if existing else {}
        try:
            with requests.get(url, stream=True, headers=headers, timeout=timeout,
                              allow_redirects=True) as response:
                if response.status_code == 416 and existing:
                    pass  # 已经下载完整，交给校验
                elif response.status_code not in (200, 206):
                    raise RuntimeError(f'HTTP {response.status_code}：{url}')
                else:
                    mode = 'ab' if (existing and response.status_code == 206) else 'wb'
                    with open(part, mode) as handle:
                        for chunk in response.iter_content(CHUNK):
                            if chunk:
                                handle.write(chunk)
                                if progress:
                                    progress(handle.tell())
            if expected_bytes and part.stat().st_size != int(expected_bytes):
                raise IntegrityError(f'大小不符：{part.stat().st_size} != {expected_bytes}')
            digest = sha256_file(part)
            if expected_sha256 and digest != expected_sha256:
                raise IntegrityError(f'SHA-256 不符：{digest} != {expected_sha256}')
            part.replace(target)
            _validate_h5ad(target)
            return {'path': str(target), 'sha256': digest, 'bytes': target.stat().st_size,
                    'downloaded': target.stat().st_size, 'reused': False}
        except IntegrityError:
            raise
        except Exception as exc:      # 有界重试；保留 .part 以便续传
            last_error = exc
            if attempt < retries:
                time.sleep(min(2 * attempt, 8))
    raise RuntimeError(f'下载失败（已重试 {retries} 次）：{last_error}; 残留 {part}')


def _validate_h5ad(path: Path):
    """只对 .h5ad 做 HDF5 可读性检查；其他格式交给调用方。"""
    if path.suffix.lower() != '.h5ad':
        return
    import anndata as ad
    ad.read_h5ad(path)


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
