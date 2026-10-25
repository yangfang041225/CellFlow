"""后台公开数据下载：进度、取消、失败隔离。

- 每个下载有独立临时目录 `data/public/.downloads/<id>/`，成功后才移动到 `data/public/`；
- 取消或失败会删除临时目录，避免半成品被当成完整数据集；
- 状态保存在内存（重启后丢失）；下载本身不写队列库，因为它是 I/O 任务而非分析任务。
"""
from pathlib import Path
import shutil
import threading
import time
import uuid

_TASKS = {}
_LOCK = threading.Lock()


def _public_root(outdir):
    return Path(outdir)


def submit(source, identifier, outdir, fetch_func=None):
    task_id = uuid.uuid4().hex[:12]
    task = {
        'id': task_id,
        'source': source,
        'identifier': identifier,
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
    thread = threading.Thread(target=_run, args=(task_id, Path(outdir), fetch_func), daemon=True)
    thread.start()
    return task_id


def get(task_id):
    with _LOCK:
        task = _TASKS.get(task_id)
        return dict(task) if task else None


def list_tasks():
    with _LOCK:
        return [dict(task) for task in sorted(_TASKS.values(), key=lambda item: item['created'],
                                              reverse=True)]


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
        return True, '已请求取消'


def _update(task_id, **values):
    with _LOCK:
        task = _TASKS.get(task_id)
        if task is None:
            return
        task.update(values)
        task['updated'] = time.time()


def _run(task_id, outdir, fetch_func=None):
    from .publicdata import DownloadCancelled, fetch as default_fetch, set_download_hook

    task = get(task_id)
    if task is None:
        return
    if task.get('cancel'):
        _update(task_id, state='cancelled', message='已取消')
        return

    staging = outdir / '.downloads' / task_id
    staging.mkdir(parents=True, exist_ok=True)
    fetch = fetch_func or default_fetch

    def should_cancel():
        current = get(task_id) or {}
        return bool(current.get('cancel'))

    def byte_progress(done, total):
        _update(task_id, done=int(done), total=int(total or 0))

    def message_progress(message):
        _update(task_id, message=str(message))

    set_download_hook(byte_progress, should_cancel)
    _update(task_id, state='running', message='开始下载')
    try:
        result = Path(fetch(task['source'], task['identifier'], staging,
                            progress=message_progress))
        final = outdir / result.name
        if final.exists():
            final.unlink()
        shutil.move(str(result), str(final))
        for leftover in staging.rglob('*'):
            if leftover.is_file():
                leftover.unlink(missing_ok=True)
        _update(task_id, state='done', path=str(final), message='完成',
                done=final.stat().st_size, total=final.stat().st_size)
    except DownloadCancelled:
        _update(task_id, state='cancelled', message='已取消')
    except Exception as exc:
        _update(task_id, state='failed', error=f'{type(exc).__name__}: {exc}',
                message='下载失败')
    finally:
        set_download_hook(None, None)
        shutil.rmtree(staging, ignore_errors=True)
