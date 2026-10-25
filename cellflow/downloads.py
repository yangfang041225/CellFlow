"""后台公开数据下载：进度、取消、失败隔离、持久状态与内容版本化。

- 每个下载有独立临时目录 `data/public/.downloads/<id>/`，成功后才发布到 `data/public/`；
- 发布文件名为 `<原名>__<内容哈希8>.<ext>`，同名不同内容不会覆盖旧版本；
- 任务状态持久化到 `data/public/.downloads/state.json`，重启后残留任务标记为 interrupted；
- 记录提交时项目，列表可按项目过滤。
"""
from pathlib import Path
import hashlib
import json
import shutil
import threading
import time
import uuid

_TASKS = {}
_LOCK = threading.Lock()
_LOADED = {}          # outdir -> bool
_LAST_PERSIST = {}    # outdir -> timestamp
_ACTIVE_STATES = ('queued', 'running', 'cancelling')


def _state_file(outdir):
    return Path(outdir) / '.downloads' / 'state.json'


def _serialise(task):
    data = dict(task)
    data.pop('cancel', None)
    return data


def _persist(outdir, force=False):
    outdir = Path(outdir)
    now = time.time()
    if not force and now - _LAST_PERSIST.get(str(outdir), 0) < 2.0:
        return
    _LAST_PERSIST[str(outdir)] = now
    try:
        path = _state_file(outdir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            tasks = [_serialise(task) for task in _TASKS.values()
                     if str(_public_root(task.get('outdir'))) == str(outdir)]
        tmp = path.with_name(path.name + '.tmp')
        tmp.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(path)
    except Exception:
        pass


def _load(outdir):
    outdir = Path(outdir)
    key = str(outdir)
    if _LOADED.get(key):
        return
    _LOADED[key] = True
    try:
        raw = json.loads(_state_file(outdir).read_text(encoding='utf-8'))
    except Exception:
        return
    with _LOCK:
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict) or not item.get('id'):
                continue
            item.setdefault('source', '')
            item.setdefault('identifier', '')
            item.setdefault('done', 0)
            item.setdefault('total', 0)
            item.setdefault('path', '')
            item.setdefault('error', '')
            item.setdefault('project', 'default')
            item.setdefault('outdir', str(outdir))
            if item.get('state') in _ACTIVE_STATES:
                item['state'] = 'interrupted'
                item['message'] = '上次退出时下载未完成，已标记为中断（不覆盖已有文件）'
            item.setdefault('updated', item.get('created', time.time()))
            _TASKS[item['id']] = item


def _public_root(outdir):
    return Path(outdir)


def _default_outdir():
    return Path(__file__).resolve().parents[1] / 'data' / 'public'


def submit(source, identifier, outdir, fetch_func=None, project=None):
    from .workspace import active_project
    outdir = Path(outdir)
    _load(outdir)
    task_id = uuid.uuid4().hex[:12]
    task = {
        'id': task_id,
        'source': source,
        'identifier': identifier,
        'outdir': str(outdir),
        'project': project or active_project()['id'],
        'state': 'queued',
        'done': 0,
        'total': 0,
        'message': '排队中',
        'path': '',
        'error': '',
        'created': time.time(),
        'updated': time.time(),
    }
    with _LOCK:
        _TASKS[task_id] = task
    _persist(outdir, force=True)
    thread = threading.Thread(target=_run, args=(task_id, outdir, fetch_func), daemon=True)
    thread.start()
    return task_id


def get(task_id):
    _load(_default_outdir())
    with _LOCK:
        task = _TASKS.get(task_id)
        if task is not None:
            return dict(task)
    for outdir in list(_LOADED):
        _load(outdir)
        with _LOCK:
            task = _TASKS.get(task_id)
            if task is not None:
                return dict(task)
    return None


def list_tasks(project=None, outdir=None):
    if outdir is not None:
        _load(outdir)
    _load(_default_outdir())
    for extra in list(_LOADED):
        _load(extra)
    with _LOCK:
        items = [dict(task) for task in _TASKS.values()
                 if project is None or (task.get('project') or 'default') == project]
    items.sort(key=lambda item: item['created'], reverse=True)
    return items


def cancel(task_id):
    with _LOCK:
        task = _TASKS.get(task_id)
        if task is None:
            return False, '下载任务不存在'
        if task['state'] not in ('queued', 'running'):
            return False, f'当前状态为 {task["state"]}，不能取消'
        task['cancel'] = True
        task['state'] = 'cancelling'
        task['message'] = '正在取消…'
        outdir = task.get('outdir')
    if outdir:
        _persist(outdir, force=True)
    return True, '已请求取消'


def _update(task_id, **values):
    outdir = None
    with _LOCK:
        task = _TASKS.get(task_id)
        if task is None:
            return
        task.update(values)
        task['updated'] = time.time()
        outdir = task.get('outdir')
    if outdir:
        _persist(outdir)


def _publish(outdir, result: Path) -> Path:
    """内容版本化发布：同名不同内容绝不覆盖。"""
    digest = hashlib.sha256()
    with open(result, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    short = digest.hexdigest()[:8]
    suffix = ''.join(result.suffixes) or result.suffix
    stem = result.name[:-len(suffix)] if suffix and result.name.endswith(suffix) else result.stem
    final = Path(outdir) / f'{stem}__{short}{suffix}'
    counter = 1
    while final.exists() and final.stat().st_size != result.stat().st_size:
        final = Path(outdir) / f'{stem}__{short}-{counter}{suffix}'
        counter += 1
    if final.exists():
        result.unlink(missing_ok=True)
        return final
    shutil.move(str(result), str(final))
    return final


def _fail(task_id, exc):
    """线程内任何阶段的异常都落到任务记录里，绝不留下“永远排队”的幽灵任务。"""
    _update(task_id, state='failed', error=f'{type(exc).__name__}: {exc}',
            message='下载失败')


def _run(task_id, outdir, fetch_func=None):
    """后台线程入口。

    修复的缺陷：旧版把「导入 publicdata」「读取任务」「创建临时目录」放在
    try/except 之外。这些步骤一旦失败（目录不可写、磁盘满、依赖缺失、
    进程回收临时目录），线程直接结束，任务永远停在 queued：
    界面会一直显示“排队中”，既不报错也不会结束，用户只能重启软件。
    现在准备工作与下载本身都在同一层异常处理内。
    """
    staging = Path(outdir) / '.downloads' / task_id
    restore_hook = None
    try:
        from . import publicdata
        restore_hook = publicdata.set_download_hook

        task = get(task_id)
        if task is None:
            return
        if task.get('cancel'):
            _update(task_id, state='cancelled', message='已取消')
            return

        staging.mkdir(parents=True, exist_ok=True)
        fetch = fetch_func or publicdata.fetch

        def should_cancel():
            current = get(task_id) or {}
            return bool(current.get('cancel'))

        def byte_progress(done, total):
            _update(task_id, done=int(done), total=int(total or 0))

        def message_progress(message):
            _update(task_id, message=str(message))

        restore_hook(byte_progress, should_cancel)
        _update(task_id, state='running', message='开始下载')
        try:
            result = Path(fetch(task['source'], task['identifier'], staging,
                                progress=message_progress))
            if not result.is_file():
                raise RuntimeError('下载接口没有产出文件')
            species_filtered = None
            meta_path = result.with_name(result.name + '.meta.json')
            if meta_path.is_file():
                try:
                    species_filtered = json.loads(meta_path.read_text(encoding='utf-8'))
                except Exception:
                    species_filtered = None
            final = _publish(outdir, result)
            if species_filtered:
                _update(task_id, species_filtered=species_filtered)
            for leftover in staging.rglob('*'):
                if leftover.is_file():
                    leftover.unlink(missing_ok=True)
            _update(task_id, state='done', path=str(final), message='完成',
                    done=final.stat().st_size, total=final.stat().st_size)
        except publicdata.DownloadCancelled:
            _update(task_id, state='cancelled', message='已取消')
        except Exception as exc:
            _fail(task_id, exc)
    except Exception as exc:
        _fail(task_id, exc)
    finally:
        if restore_hook is not None:
            try:
                restore_hook(None, None)
            except Exception:
                pass
        shutil.rmtree(staging, ignore_errors=True)
        _persist(outdir, force=True)
