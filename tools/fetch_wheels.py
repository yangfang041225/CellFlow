"""离线 wheel 收集器：把一组 Python 依赖连同依赖树下载成 wheels/ 目录。

为什么需要它：
- uv / pip 在本机网络下会长时间卡在下载上；但用普通 HTTP（requests + 断点续传）
  同一台机器能稳定跑到数百 KB/s，所以这里自己解析依赖、自己下载。
- 也用于「连课题组服务器」：在能联网的机器上跑一次，把 wheels/ 拷到服务器，
  用 `pip install --no-index --find-links wheels ...` 离线安装。

用法：
    python tools/fetch_wheels.py --spec-file requirements-velovi.txt --with-torch-cpu -o wheels
    python tools/fetch_wheels.py "scvelo==0.3.4" "scanpy==1.10.4" --python 3.11 -o wheels
    python tools/fetch_wheels.py ... --list-only      # 只解析，不下载

限制（写清楚）：
- 只处理带 wheel 的包，不编译源码；没有合适 wheel 的包会明确报出来。
- 依赖解析是「贪心 + 约束合并」，不是完整的回溯求解器；冲突时会报出具体包和约束。
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import requests

PYPI_JSON = 'https://pypi.org/pypi/{name}/json'
PYPI_VERSION = 'https://pypi.org/pypi/{name}/{version}/json'
TORCH_CPU_INDEX = 'https://download.pytorch.org/whl/cpu/torch/'
USER_AGENT = 'CellFlow-fetch-wheels/1.0'
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': USER_AGENT})


def _get(url: str, timeout=(10, 60), retries: int = 4) -> requests.Response:
    last = None
    for attempt in range(retries):
        try:
            response = SESSION.get(url, timeout=timeout)
            if response.status_code == 404:
                return response
            response.raise_for_status()
            return response
        except Exception as exc:  # 网络抖动重试
            last = exc
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f'请求失败 {url}: {last}')


# ---------------------------------------------------------------- 标签
def python_tag(version: str) -> str:
    major, minor = version.split('.')[:2]
    return f'cp{major}{minor}'


def wheel_matches(filename: str, tag: str, platform: str) -> bool:
    """判断 wheel 是否适用于目标 Python / 平台。

    需要处理三种常见情况：
    - 精确匹配：`...-cp311-cp311-win_amd64.whl`
    - 通用标签：`...-py3-none-any.whl`
    - 稳定 ABI：`...-cp39-abi3-win_amd64.whl`（Python 3.9+ 通用，例如 igraph）
    """
    if not filename.endswith('.whl'):
        return False
    parts = filename[:-4].split('-')
    if len(parts) < 5:
        return False
    py_tags = parts[-3].split('.')
    abi_tags = parts[-2].split('.')
    plat_tags = parts[-1].split('.')
    match = re.match(r'cp3(\d+)$', tag)
    target_minor = int(match.group(1)) if match else None

    def py_ok() -> bool:
        for item in py_tags:
            if item == tag:
                return True
            generic = re.match(r'py3(\d*)$', item)
            if generic and 'abi3' not in abi_tags:
                # py3 / py37 / py38 …：通用纯 Python 标签
                if not generic.group(1) or int(generic.group(1)) <= (target_minor or 99):
                    return True
            stable = re.match(r'cp3(\d+)$', item)
            if stable and 'abi3' in abi_tags and target_minor is not None:
                if int(stable.group(1)) <= target_minor:
                    return True
        return False

    def plat_ok() -> bool:
        for item in plat_tags:
            if item == 'any':
                return True
            if platform in item:
                return True
            if platform.startswith('win') and item.startswith('win'):
                return True
            if platform.startswith('linux') and ('manylinux' in item or 'linux' in item):
                return True
        return False

    def abi_ok() -> bool:
        return any(item in ('none', 'abi3', tag) for item in abi_tags)

    return py_ok() and plat_ok() and abi_ok()


# ---------------------------------------------------------------- 解析
@dataclass
class Pick:
    name: str
    version: str
    url: str
    filename: str
    requires: List[str] = field(default_factory=list)


def _normalize(name: str) -> str:
    return re.sub(r'[-_.]+', '-', name).lower()


def _requirements_of(name: str, version: str) -> List[str]:
    response = _get(PYPI_VERSION.format(name=name, version=version))
    if response.status_code != 200:
        return []
    info = response.json().get('info', {})
    return [r for r in (info.get('requires_dist') or []) if ';' not in r or 'extra' not in r]


def _parse_requirement(text: str):
    try:
        from packaging.requirements import Requirement
        from packaging.markers import default_environment
        req = Requirement(text)
        if req.marker is not None:
            env = default_environment()
            env.setdefault('extra', '')
            if not req.marker.evaluate(env):
                return None
        return req
    except Exception:
        return None


def _candidate(release_files: List[dict], tag: str, platform: str) -> Optional[dict]:
    for item in release_files:
        if item.get('packagetype') != 'bdist_wheel':
            continue
        if wheel_matches(item['filename'], tag, platform):
            return item
    return None


def resolve(requirements: List[str], tag: str, platform: str,
            extra_specs: Optional[Dict[str, str]] = None) -> Dict[str, Pick]:
    """贪心解析依赖树，返回每个包选中的 wheel。"""
    from packaging.specifiers import SpecifierSet

    specs: Dict[str, SpecifierSet] = {}
    picks: Dict[str, Pick] = {}
    meta_cache: Dict[str, dict] = {}
    queue: List[str] = list(requirements)

    def fetch_meta(name: str) -> dict:
        key = _normalize(name)
        if key not in meta_cache:
            response = _get(PYPI_JSON.format(name=name))
            if response.status_code != 200:
                raise RuntimeError(f'PyPI 上没有找到包：{name}')
            meta_cache[key] = response.json()
        return meta_cache[key]

    while queue:
        raw = queue.pop()
        req = _parse_requirement(raw)
        if req is None:
            continue
        name = req.name
        key = _normalize(name)
        if req.specifier:
            specs[key] = (specs.get(key, SpecifierSet('')) & req.specifier)
        current = picks.get(key)
        if current and (not req.specifier or current.version in req.specifier):
            continue

        meta = fetch_meta(name)
        releases = meta.get('releases') or {}
        versions = []
        for version, files in releases.items():
            if not _candidate(files, tag, platform):
                continue
            try:
                if specs.get(key) and not specs[key].contains(version, prereleases=False):
                    continue
            except Exception:
                continue
            versions.append(version)

        def sort_key(value: str):
            parts = re.split(r'[.rcab+-]', value)
            out = []
            for part in parts:
                out.append(int(part) if part.isdigit() else 0)
            return out

        if not versions:
            raise RuntimeError(f'{name} 没有可用于 {tag}/{platform} 的 wheel'
                               f'（约束：{specs.get(key)}）；需要源码编译，本工具不处理')
        version = sorted(versions, key=sort_key)[-1]
        selected = _candidate(releases[version], tag, platform)
        pick = Pick(name=name, version=version, url=selected['url'],
                    filename=selected['filename'],
                    requires=_requirements_of(name, version))
        picks[key] = pick
        print(f'  {name}=={version}  {pick.filename}')
        queue.extend(pick.requires)

    if extra_specs:
        for key, version in extra_specs.items():
            if key in picks:
                continue
            meta = fetch_meta(key)
            files = (meta.get('releases') or {}).get(version)
            if not files:
                raise RuntimeError(f'{key}=={version} 在 PyPI 不存在')
            selected = _candidate(files, tag, platform)
            if not selected:
                raise RuntimeError(f'{key}=={version} 没有 {tag}/{platform} 的 wheel')
            picks[key] = Pick(name=key, version=version, url=selected['url'],
                              filename=selected['filename'],
                              requires=_requirements_of(key, version))
    return picks


def resolve_torch_cpu(version: str, tag: str, platform: str) -> Pick:
    """torch 的 CPU wheel 不在 PyPI 主站，去 PyTorch 自己的索引里找。"""
    html = _get(TORCH_CPU_INDEX).text
    pattern = re.compile(r'href="([^"]*torch-%s[^"]*\.whl[^"]*)"' % re.escape(version))
    for match in pattern.finditer(html):
        url = match.group(1)
        if not url.startswith('http'):
            url = 'https://download.pytorch.org' + ('' if url.startswith('/') else '/') + url
        filename = url.split('/')[-1].split('#')[0]
        if wheel_matches(filename, tag, platform):
            return Pick(name='torch', version=version, url=url, filename=filename,
                        requires=['filelock', 'typing-extensions', 'sympy', 'networkx',
                                  'jinja2', 'fsspec'])
    raise RuntimeError(f'在 PyTorch CPU 索引里没找到 torch=={version} 的 {tag}/{platform} wheel')


# ---------------------------------------------------------------- 下载
def download(item: Pick, outdir: Path, counter: Dict[str, int], lock: threading.Lock) -> Path:
    target = outdir / item.filename
    if target.exists() and target.stat().st_size > 0:
        with lock:
            counter['skip'] += 1
        return target
    last = None
    for attempt in range(4):
        try:
            with SESSION.get(item.url, stream=True, timeout=(10, 120)) as response:
                response.raise_for_status()
                total = int(response.headers.get('content-length') or 0)
                done = 0
                tmp = target.with_suffix(target.suffix + '.part')
                with open(tmp, 'wb') as handle:
                    for chunk in response.iter_content(1024 * 256):
                        if chunk:
                            handle.write(chunk)
                            done += len(chunk)
                tmp.replace(target)
            with lock:
                counter['ok'] += 1
                counter['bytes'] += target.stat().st_size
            return target
        except Exception as exc:
            last = exc
            time.sleep(1.0 * (attempt + 1))
    with lock:
        counter['fail'] += 1
    raise RuntimeError(f'下载失败 {item.filename}: {last}')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='把依赖树下载成离线 wheels 目录')
    parser.add_argument('requirements', nargs='*', help='依赖声明，例如 "scanpy==1.10.4"')
    parser.add_argument('--spec-file', action='append', default=[], help='从文件读依赖（每行一个）')
    parser.add_argument('--python', default='3.11', help='目标 Python 版本（默认 3.11）')
    parser.add_argument('--platform', default='win_amd64', help='目标平台（默认 win_amd64；Linux 用 manylinux2014_x86_64）')
    parser.add_argument('--with-torch-cpu', metavar='VERSION', help='额外从 PyTorch 索引下载 CPU 版 torch')
    parser.add_argument('-o', '--out', default='wheels', help='输出目录')
    parser.add_argument('--list-only', action='store_true', help='只解析不下载')
    parser.add_argument('--jobs', type=int, default=6, help='并行下载数（默认 6）')
    args = parser.parse_args(argv)

    requirements = list(args.requirements)
    for spec in args.spec_file:
        for line in Path(spec).read_text(encoding='utf-8').splitlines():
            line = line.split('#')[0].strip()
            if line:
                requirements.append(line)
    if not requirements:
        parser.error('至少给一个依赖，或用 --spec-file')

    tag = python_tag(args.python)
    print(f'目标：Python {args.python}（{tag}） / {args.platform}')
    print('解析依赖树 …')
    picks = resolve(requirements, tag, args.platform)
    if args.with_torch_cpu:
        picks['torch'] = resolve_torch_cpu(args.with_torch_cpu, tag, args.platform)
        for extra in ('filelock', 'typing-extensions', 'sympy', 'networkx', 'jinja2', 'fsspec'):
            if _normalize(extra) not in picks:
                picks.update(resolve([extra], tag, args.platform))

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    print(f'共 {len(picks)} 个包，开始下载到 {outdir} …')
    if args.list_only:
        for item in picks.values():
            print(f'  {item.name}=={item.version}')
        return 0

    counter = {'ok': 0, 'skip': 0, 'fail': 0, 'bytes': 0}
    lock = threading.Lock()
    failures = []
    with futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        jobs = {pool.submit(download, item, outdir, counter, lock): item for item in picks.values()}
        for job in futures.as_completed(jobs):
            item = jobs[job]
            try:
                job.result()
                print(f'  ✓ {item.filename}')
            except Exception as exc:
                failures.append(str(exc))
                print(f'  ✗ {item.filename}: {exc}')

    manifest = {key: dict(name=item.name, version=item.version, filename=item.filename)
                for key, item in picks.items()}
    (outdir / 'wheels-manifest.json').write_text(
        json.dumps(dict(python=args.python, platform=args.platform, packages=manifest),
                   ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'完成：下载 {counter["ok"]}，跳过 {counter["skip"]}，失败 {counter["fail"]}，'
          f'共 {counter["bytes"] / 1024 / 1024:.0f} MB')
    if failures:
        print('失败清单：')
        for item in failures:
            print(' -', item)
        return 1
    print('离线安装命令示例：')
    print(f'  pip install --no-index --find-links {outdir} ' +
          ' '.join(f'{item.name}=={item.version}' for item in picks.values()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
