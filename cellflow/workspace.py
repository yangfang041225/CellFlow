"""工作区数据层：项目、数据集、分析任务。

只做“读”的整理，不改变后端行为：
- 数据集来自三处：内置示例、用户导入的快照（results/inputs）、下载的公开数据（data/public）；
- 任务来自队列库（results/queue.sqlite），与数据集按输入文件路径对应；
- 项目信息存在 results/workspace.json，默认一个项目，缺少时自动创建。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / 'results'
PUBLIC = ROOT / 'data' / 'public'
EXAMPLES = ROOT / 'examples'
PROJECT_FILE = STORE / 'workspace.json'
DATASET_FILE = STORE / 'datasets.json'
PROJECTS_FILE = STORE / 'projects.json'

_REGISTRY_LOCK = threading.RLock()

_kind_suffix = {
    '.h5ad': 'AnnData',
    '.h5': '10x HDF5',
    '.loom': 'loom',
    '.csv': 'CSV 表',
    '.tsv': 'TSV 表',
    '.txt': '文本表',
    '.zip': '压缩包',
    '.gz': '压缩包',
    '.npz': '内置示例',
    '.mtx': '10x 矩阵',
}


def _kind(path: Path) -> str:
    name = path.name.lower()
    if name.endswith('.tar.gz'):
        return '压缩包'
    if name.startswith('matrix.mtx'):
        return '10x 矩阵'
    return _kind_suffix.get(path.suffix.lower(), path.suffix or '文件')


def _size(size: int) -> str:
    if size >= 1024 ** 3:
        return f'{size / 1024 ** 3:.2f} GB'
    if size >= 1024 ** 2:
        return f'{size / 1024 ** 2:.1f} MB'
    if size >= 1024:
        return f'{size / 1024:.0f} KB'
    return f'{size} B'


# ---------------------------------------------------------------- 项目
def _load_projects() -> Dict:
    """读取项目表；不存在时用旧 workspace.json 的名字创建 default 项目。"""
    if PROJECTS_FILE.exists():
        try:
            data = json.loads(PROJECTS_FILE.read_text(encoding='utf-8'))
            if data.get('items'):
                return data
        except Exception:
            pass
    legacy = {}
    if PROJECT_FILE.exists():
        try:
            legacy = json.loads(PROJECT_FILE.read_text(encoding='utf-8'))
        except Exception:
            legacy = {}
    item = {'id': 'default', 'name': legacy.get('name') or '未命名项目',
            'note': legacy.get('note') or '', 'created': legacy.get('created') or time.strftime('%Y-%m-%d %H:%M')}
    data = {'active': 'default', 'items': [item]}
    _save_projects(data)
    return data


def _save_projects(data: Dict) -> None:
    STORE.mkdir(parents=True, exist_ok=True)
    tmp = PROJECTS_FILE.with_name(PROJECTS_FILE.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, PROJECTS_FILE)


def projects() -> Dict:
    data = _load_projects()
    if not any(item['id'] == data.get('active') for item in data['items']):
        data['active'] = data['items'][0]['id']
    return data


def active_project() -> Dict:
    data = projects()
    return next(item for item in data['items'] if item['id'] == data['active'])


def project() -> Dict:
    """兼容旧接口：返回当前项目。"""
    return active_project()


def create_project(name: str, note: str = '') -> Dict:
    data = projects()
    name = (name or '').strip() or '未命名项目'
    item = {'id': 'p' + os.urandom(4).hex(), 'name': name, 'note': note or '',
            'created': time.strftime('%Y-%m-%d %H:%M')}
    data['items'].append(item)
    data['active'] = item['id']
    _save_projects(data)
    return item


def activate_project(project_id: str) -> Optional[Dict]:
    data = projects()
    item = next((entry for entry in data['items'] if entry['id'] == project_id), None)
    if item is None or item.get('archived'):
        return None
    data['active'] = project_id
    _save_projects(data)
    return item


def rename_project(name: str) -> None:
    data = projects()
    item = next((entry for entry in data['items'] if entry['id'] == data['active']), None)
    if item is None:
        return
    item['name'] = (name or '').strip() or '未命名项目'
    item['updated'] = time.strftime('%Y-%m-%d %H:%M')
    _save_projects(data)
    try:
        PROJECT_FILE.write_text(json.dumps(
            {'name': item['name'], 'created': item.get('created', ''),
             'note': item.get('note', '')}, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass


# ---------------------------------------------------------------- 数据集
def _entries() -> List[Dict]:
    if not DATASET_FILE.exists():
        return []
    try:
        raw = json.loads(DATASET_FILE.read_text(encoding='utf-8'))
    except Exception:
        return []
    entries = []
    for item in raw:
        if isinstance(item, str):
            entries.append({'path': item, 'project': 'default'})
        elif isinstance(item, dict) and item.get('path'):
            entries.append({'path': str(item['path']), 'project': item.get('project') or 'default'})
    return entries


def registry(project: Optional[str] = None) -> List[str]:
    """返回登记路径；project=None 表示全部（兼容旧调用）。"""
    entries = _entries()
    if project is not None:
        entries = [item for item in entries if item['project'] == project]
    return [str(Path(item['path']).resolve()) for item in entries]


def register_dataset(path) -> None:
    """登记到当前项目：加锁 + 原子替换 + 按规范化路径去重。"""
    resolved = str(Path(path).resolve())
    active = active_project()['id']
    with _REGISTRY_LOCK:
        entries = _entries()
        for item in entries:
            if (str(Path(item['path']).resolve()) == resolved
                    and (item.get('project') or 'default') == active):
                return
        entries.append({'path': resolved, 'project': active})
        STORE.mkdir(parents=True, exist_ok=True)
        tmp = DATASET_FILE.with_name(DATASET_FILE.name + '.tmp')
        tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, DATASET_FILE)


def unregister_dataset(path) -> bool:
    """只移除登记，不删除文件；返回是否真的移除了。"""
    resolved = str(Path(path).resolve())
    with _REGISTRY_LOCK:
        entries = _entries()
        kept = [item for item in entries if str(Path(item['path']).resolve()) != resolved]
        if len(kept) == len(entries):
            return False
        STORE.mkdir(parents=True, exist_ok=True)
        tmp = DATASET_FILE.with_name(DATASET_FILE.name + '.tmp')
        tmp.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, DATASET_FILE)
    return True


def list_datasets(project: Optional[str] = None) -> List[Dict]:
    items: List[Dict] = []
    seen = set()

    def add(plan):
        if not plan:
            return
        key = str(plan['path'].resolve())
        if key in seen:
            return
        seen.add(key)
        items.append(plan)

    project = project or active_project()['id']
    for path_text in registry(project=project):
        plan = _dataset_candidate(Path(path_text))
        if plan:
            plan['source'] = '我导入的'
            add(plan)
    public = PUBLIC
    if public.exists():
        for path in sorted(public.iterdir()):
            plan = _dataset_candidate(path)
            if plan:
                plan['source'] = '公开数据'
                add(plan)
    demo = EXAMPLES / 'synthetic.npz'
    if demo.exists():
        add(dict(path=demo, label='内置模拟示例', source='示例', kind=_kind(demo),
                 size=_size(demo.stat().st_size), mtime=demo.stat().st_mtime))
    items.sort(key=lambda item: (item['source'] != '示例', -item['mtime']))
    return items


def _dataset_candidate(path: Path) -> Optional[Dict]:
    if not path.is_file():
        return None
    name = path.name
    # 跳过任务快照以外的杂项（队列配置、worker 日志、sqlite）
    if path.suffix.lower() in ('.json', '.log', '.sqlite', '.sqlite3', '.txt'):
        return None
    if name.endswith('.worker.log') or name.endswith('.mtx.part'):
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    return dict(path=path, label=_infer_label(path), source='内置' if 'examples' in str(path) else '导入',
                kind=_kind(path), size=_size(stat.st_size), mtime=stat.st_mtime)


def _infer_label(path: Path) -> str:
    stem = path.stem
    if len(stem) >= 16 and all(ch in '0123456789abcdef' for ch in stem[:16]):
        # 上传快照用哈希命名，界面上给出可读名：原文件名记在旁边
        return f'导入数据 {stem[:8]}…'
    return stem


def dataset_label(path) -> str:
    path = Path(path)
    for item in list_datasets():
        if item['path'] == path:
            return item['label']
    return path.name


# ---------------------------------------------------------------- 任务
def runs() -> List[Dict]:
    from .queue import list_jobs
    try:
        return list_jobs()
    except Exception:
        return []


def runs_for(path) -> List[Dict]:
    resolved = str(Path(path).resolve())
    return [job for job in runs()
            if str(job.get('dataset') or '') == resolved]


def job_summary(job: Dict) -> Dict:
    """把队列行整理成界面需要的最小信息（不读结果文件）。"""
    from .registry import INDEX
    method = INDEX.get(job.get('method'))
    return dict(
        id=job.get('id'),
        method=job.get('method'),
        method_label=method.name if method else (job.get('method') or ''),
        state=job.get('state') or '',
        submitted=job.get('submitted') or 0,
        updated=job.get('updated') or 0,
        output=job.get('output'),
        error=job.get('error') or '',
    )


def latest_run() -> Optional[Dict]:
    items = runs()
    return items[0] if items else None


def counts() -> Dict[str, int]:
    items = runs()
    out = {'queued': 0, 'running': 0, 'completed': 0, 'failed': 0}
    for job in items:
        out[job.get('state', 'failed')] = out.get(job.get('state', 'failed'), 0) + 1
    return out

def unregister_dataset(path) -> bool:
    """只移除登记，不删除文件；文件仍可被任务引用，故调用方需先检查引用。"""
    resolved = str(Path(path).resolve())
    with _REGISTRY_LOCK:
        items = [str(Path(item).resolve()) for item in registry()]
        if resolved not in items:
            return False
        items = [item for item in items if item != resolved]
        STORE.mkdir(parents=True, exist_ok=True)
        tmp = DATASET_FILE.with_name(DATASET_FILE.name + '.tmp')
        tmp.write_text(json.dumps(sorted(set(items)), ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, DATASET_FILE)
    return True

def archive_project(project_id: str, archived: bool = True) -> Optional[Dict]:
    """归档/恢复项目；归档当前项目时自动切到第一个未归档项目。"""
    data = projects()
    item = next((entry for entry in data['items'] if entry['id'] == project_id), None)
    if item is None:
        return None
    item['archived'] = bool(archived)
    if item['archived'] and data['active'] == project_id:
        candidates = [entry for entry in data['items'] if not entry.get('archived')]
        if not candidates:
            item['archived'] = False
            return None
        data['active'] = candidates[0]['id']
    _save_projects(data)
    return item


def delete_project(project_id: str) -> tuple:
    """删除空项目；default、当前项目、仍有数据集/任务的项目一律拒绝。"""
    data = projects()
    item = next((entry for entry in data['items'] if entry['id'] == project_id), None)
    if item is None:
        return False, '项目不存在'
    if project_id == 'default':
        return False, '默认项目不能删除'
    if data['active'] == project_id:
        return False, '当前项目不能删除，请先切换到其他项目'
    datasets = registry(project=project_id)
    if datasets:
        return False, f'该项目仍登记了 {len(datasets)} 个数据集，请先移除登记'
    try:
        from .queue import list_jobs
        jobs = list_jobs(project=project_id)
    except Exception:
        jobs = []
    if jobs:
        return False, f'该项目仍有 {len(jobs)} 个任务记录，请先删除任务'
    data['items'] = [entry for entry in data['items'] if entry['id'] != project_id]
    _save_projects(data)
    return True, '已删除项目'
