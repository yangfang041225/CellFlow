"""数据根目录与临时文件治理（P3-02）。

设计约束（必须遵守）
--------------------
1. **默认行为等于改造前**：数据根目录默认仍是 «<repo>/data»，因此既有安装升级后
   已登记数据集的绝对路径全部保持不变，不需要迁移。
2. 大型中间文件（下载中、解压、格式转换）不再写 «%TEMP%»（默认在系统盘），
   统一落到 «<data_root>/staging/<kind>/»。
3. 只有用户显式选择新的数据根目录时才发生路径变化；该操作需要用户确认，
   本模块只提供能力与校验，不做任何自动迁移。
4. 不递归删除用户目录；清理只针对 staging 下本程序创建的任务目录。

配置优先级：环境变量 CELLFLOW_DATA_ROOT > 设置文件 > 默认 <repo>/data
设置文件：%LOCALAPPDATA%/CellFlow/settings.json（可用 CELLFLOW_SETTINGS_FILE 覆盖，便于测试）
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Dict, Optional

DATA_ROOT_ENV = 'CELLFLOW_DATA_ROOT'
SETTINGS_ENV = 'CELLFLOW_SETTINGS_FILE'
APP_NAME = 'CellFlow'

#: 安全余量：预检查时要求 need * (1 + MARGIN) + MIN_FREE_BYTES 可用。
FREE_SPACE_MARGIN = 0.15
MIN_FREE_BYTES = 512 * 1024 ** 2

#: staging 下的分类目录：下载中 / 解压 / 格式转换 / 导入
STAGING_KINDS = ('downloads', 'extraction', 'conversion', 'import')


class StorageError(RuntimeError):
    """存储相关错误（路径不可用、空间不足等），信息应可直接展示给用户。"""


class InsufficientSpace(StorageError):
    """可用空间不足。"""


# ------------------------------------------------------------------ 基础路径
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def settings_file() -> Path:
    override = os.environ.get(SETTINGS_ENV)
    if override:
        return Path(override).expanduser()
    base = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA')
    if base:
        return Path(base) / APP_NAME / 'settings.json'
    return repo_root() / '.cellflow-settings.json'


def read_settings() -> Dict[str, object]:
    try:
        raw = json.loads(settings_file().read_text(encoding='utf-8'))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def write_settings(patch: Dict[str, object]) -> Dict[str, object]:
    current = read_settings()
    current.update(patch)
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)
    return current


# ------------------------------------------------------------------ 数据根
def default_data_root() -> Path:
    return repo_root() / 'data'


def data_root() -> Path:
    """当前生效的数据根目录（不保证存在）。"""
    env = os.environ.get(DATA_ROOT_ENV)
    if env:
        return Path(env).expanduser()
    configured = read_settings().get('data_root')
    if isinstance(configured, str) and configured.strip():
        return Path(configured).expanduser()
    return default_data_root()


def validate_root(path: Path) -> Dict[str, object]:
    """检查候选数据根是否可用：存在性、是否目录、写权限、可用空间。"""
    path = Path(path).expanduser()
    report = {
        'path': str(path),
        'exists': path.exists(),
        'is_dir': path.is_dir() if path.exists() else False,
        'writable': False,
        'free_bytes': None,
        'total_bytes': None,
        'problem': '',
    }
    if not path.exists():
        report['problem'] = '路径不存在'
        return report
    if not path.is_dir():
        report['problem'] = '路径不是目录'
        return report
    probe = path / f'.cellflow-write-probe-{os.getpid()}'
    try:
        probe.write_text('ok', encoding='utf-8')
        probe.unlink()
        report['writable'] = True
    except Exception as exc:
        report['problem'] = f'不可写入：{type(exc).__name__}: {exc}'
    try:
        usage = shutil.disk_usage(path)
        report['free_bytes'] = usage.free
        report['total_bytes'] = usage.total
    except Exception:
        pass
    return report


def set_data_root(path, create: bool = False) -> Path:
    """切换数据根目录。**不做迁移**，只记录选择并校验。"""
    target = Path(path).expanduser()
    if create:
        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            raise StorageError(f'无法创建数据目录 {target}：{type(exc).__name__}: {exc}') from exc
    report = validate_root(target)
    if not report['exists'] or not report['is_dir']:
        raise StorageError(f'数据目录不可用：{report["problem"] or target}')
    if not report['writable']:
        raise StorageError(f'数据目录不可写：{report["problem"] or target}')
    write_settings({'data_root': str(target)})
    ensure_layout(target)
    return target


def layout(root: Optional[Path] = None) -> Dict[str, Path]:
    """标准目录结构；键名即用途。"""
    root = Path(root).expanduser() if root else data_root()
    return {
        'root': root,
        'datasets': root / 'datasets',
        'downloaded': root / 'datasets' / 'downloaded',
        'imported': root / 'datasets' / 'imported',
        'manifests': root / 'datasets' / 'manifests',
        'staging': root / 'staging',
        'staging_downloads': root / 'staging' / 'downloads',
        'staging_extraction': root / 'staging' / 'extraction',
        'staging_conversion': root / 'staging' / 'conversion',
        'staging_import': root / 'staging' / 'import',
        'results': root / 'results',
        'reports': root / 'reports',
        'cache': root / 'cache',
        'logs': root / 'logs',
        # 兼容改造前的位置：旧安装的数据在 <root>/public
        'public_legacy': root / 'public',
    }


def ensure_layout(root: Optional[Path] = None) -> Dict[str, Path]:
    paths = layout(root)
    for key, path in paths.items():
        if key == 'root' or key == 'public_legacy':
            continue
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception:
            # 只读介质或权限不足时不要中断启动；具体操作时再报错
            pass
    return paths


def datasets_dir(root: Optional[Path] = None) -> Path:
    """新建数据集的默认落点。

    兼容策略：如果旧布局 «<root>/public» 已存在（改造前的安装），继续用它，
    避免已登记数据集的绝对路径发生变化。
    """
    paths = layout(root)
    legacy = paths['public_legacy']
    if legacy.is_dir():
        return legacy
    target = paths['downloaded']
    target.mkdir(parents=True, exist_ok=True)
    return target


def staging_dir(kind: str = 'conversion', task_id: str = '',
                root: Optional[Path] = None) -> Path:
    if kind not in STAGING_KINDS:
        raise StorageError(f'未知的临时目录类别：{kind}（可用：{", ".join(STAGING_KINDS)}）')
    base = layout(root)['staging'] / kind
    if task_id:
        base = base / str(task_id)
    base.mkdir(parents=True, exist_ok=True)
    return base


def make_workdir(prefix: str, kind: str = 'conversion',
                 root: Optional[Path] = None) -> Path:
    """在数据根下创建一次性工作目录（替代 tempfile.mkdtemp）。

    返回的目录位于 «<data_root>/staging/<kind>/»，因此解压/转换产生的大文件
    不会再落到系统盘。调用方负责在用完后删除（或用 cleanup_staging 清理）。
    """
    import uuid
    safe = ''.join(ch if ch.isalnum() or ch in '-_.' else '_' for ch in str(prefix))[:48]
    base = staging_dir(kind, root=root)
    for _ in range(8):
        candidate = base / f'{safe}_{time.strftime("%Y%m%d-%H%M%S")}_{uuid.uuid4().hex[:6]}'
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        except FileExistsError:
            continue
    raise StorageError('无法创建临时工作目录（重名过多）')


# ------------------------------------------------------------------ 空间门禁
def space_report(path: Path) -> Dict[str, object]:
    path = Path(path).expanduser()
    try:
        usage = shutil.disk_usage(path)
        return {'path': str(path), 'free_bytes': usage.free, 'total_bytes': usage.total,
                'used_bytes': usage.used}
    except Exception as exc:
        return {'path': str(path), 'free_bytes': None, 'total_bytes': None,
                'used_bytes': None, 'error': f'{type(exc).__name__}: {exc}'}


def required_bytes(need_bytes: int, margin: float = FREE_SPACE_MARGIN) -> int:
    need = max(0, int(need_bytes))
    return int(need * (1.0 + margin)) + MIN_FREE_BYTES


def ensure_free_space(path: Path, need_bytes: int, label: str = '下载',
                      margin: float = FREE_SPACE_MARGIN) -> Dict[str, object]:
    """下载/解压前预检查。空间不足抛 InsufficientSpace（中文可读）。"""
    report = space_report(path)
    free = report.get('free_bytes')
    required = required_bytes(need_bytes, margin)
    report['required_bytes'] = required
    if free is None:
        report['checked'] = False
        return report
    report['checked'] = True
    if int(free) < required:
        raise InsufficientSpace(
            f'{label}需要约 {_mb(required)} 可用空间，但 {report["path"]} 只剩 {_mb(int(free))}。'
            '请清理空间或更换数据目录（设置 → 数据与存储）。')
    return report


def _mb(value: int) -> str:
    return f'{value / 1024 ** 2:.1f} MB' if value < 1024 ** 3 else f'{value / 1024 ** 3:.2f} GB'


# ------------------------------------------------------------------ 文件提交
def partial_path(target: Path) -> Path:
    """下载中的临时文件名：只有校验通过才会改名成正式文件。"""
    target = Path(target)
    return target.with_name(target.name + '.part')


def commit_file(temp: Path, final: Path) -> Path:
    """把校验完成的临时文件原子地转入正式路径（同分区 rename）。"""
    temp = Path(temp)
    final = Path(final)
    if not temp.is_file():
        raise StorageError(f'临时文件不存在：{temp}')
    final.parent.mkdir(parents=True, exist_ok=True)
    if final.exists():
        # 同名不同内容不得静默覆盖：交由调用方决定（下载层做内容版本化命名）
        raise StorageError(f'目标文件已存在：{final}')
    temp.replace(final)
    return final


# ------------------------------------------------------------------ 清理
def scan_staging(root: Optional[Path] = None) -> Dict[str, object]:
    """列出 staging 下的任务目录及其体积/年龄，供孤儿清理与界面展示。"""
    base = layout(root)['staging']
    items = []
    total = 0
    if base.is_dir():
        now = time.time()
        for child in sorted(base.rglob('*')):
            if not child.is_dir():
                continue
            # 只看「类别/任务」两级目录
            if child.parent.parent != base:
                continue
            size = 0
            files = 0
            for path in child.rglob('*'):
                if path.is_file():
                    try:
                        size += path.stat().st_size
                        files += 1
                    except OSError:
                        pass
            items.append({
                'path': str(child),
                'kind': child.parent.name,
                'size_bytes': size,
                'files': files,
                'age_seconds': int(now - child.stat().st_mtime),
            })
            total += size
    return {'root': str(base), 'items': items, 'total_bytes': total, 'count': len(items)}


def cleanup_staging(max_age_seconds: int = 24 * 3600, dry_run: bool = True,
                    root: Optional[Path] = None) -> Dict[str, object]:
    """清理 staging 里的陈旧任务目录。

    安全规则：只删除 «<data_root>/staging/<kind>/<task>» 这一层的子目录，
    不跟随符号链接，不触碰 staging 之外的任何路径。
    """
    base = layout(root)['staging']
    removed = []
    kept = []
    scan = scan_staging(root)
    for item in scan['items']:
        path = Path(item['path'])
        try:
            path.resolve().relative_to(base.resolve())
        except Exception:
            continue                      # 不在 staging 内：绝不处理
        if item['age_seconds'] < max_age_seconds:
            kept.append(item)
            continue
        if dry_run:
            removed.append({**item, 'dry_run': True})
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed.append(item)
    return {
        'dry_run': bool(dry_run),
        'root': str(base),
        'removed': removed,
        'kept': kept,
        'removed_bytes': sum(int(i.get('size_bytes') or 0) for i in removed),
    }
