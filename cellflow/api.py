"""本机 HTTP API：把现有分析引擎暴露给 React 前端。

只做“转发与整理”，不改 runner / adapters / reports 的行为。
监听 127.0.0.1，仅供本机窗口使用。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel

from . import workspace
from .data import inspect as inspect_data
from .data import load as load_data
from .queue import start_worker, submit
from .registry import (DEFAULT_VELOCITY_MODEL, FAMILIES, INDEX, METHODS, VELOCITY_MODELS,
                       eligibility, env_python, missing_needs, status_of)
from .reports import bundle_entries
from .runner import DEFAULTS, normalize_config, validate

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_ROOTS = (ROOT / 'results' / 'inputs', ROOT / 'data' / 'public', ROOT / 'examples')
WEB_DIST = ROOT / 'web' / 'dist'
LOGS = ROOT / 'logs'

app = FastAPI(title='CellFlow API', docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware,
                   allow_origins=['http://127.0.0.1:8600', 'http://localhost:8600',
                                  'http://127.0.0.1:5173', 'http://localhost:5173'],
                   allow_methods=['GET', 'POST'], allow_headers=['Content-Type'])


@app.exception_handler(Exception)
async def unhandled_exception(request, exc):
    """未预期异常：写本地错误日志，返回可读中文，不把 traceback 直接抛给界面。"""
    import datetime as _dt
    import traceback as _tb
    LOGS.mkdir(parents=True, exist_ok=True)
    try:
        with (LOGS / 'api-errors.log').open('a', encoding='utf-8') as handle:
            handle.write(f'[{_dt.datetime.now():%Y-%m-%d %H:%M:%S}] {getattr(request, "method", "?")} '
                         f'{getattr(request, "url", "?")}\n')
            handle.write(_tb.format_exc() + '\n')
    except Exception:
        pass
    return JSONResponse(status_code=500, content={
        'detail': '本地服务内部错误；详情已写入 logs/api-errors.log，请把该文件发给维护者。',
        'error': f'{type(exc).__name__}: {exc}',
    })


_CACHE: Dict[str, tuple] = {}


def _safe(path_text: str) -> Path:
    path = Path(path_text).expanduser().resolve()
    if not path.exists():
        raise HTTPException(404, f'路径不存在：{path}')
    allowed = [root.resolve() for root in ALLOWED_ROOTS]
    if not any(path.is_relative_to(root) for root in allowed):
        raise HTTPException(403, '只允许读取项目内的数据目录')
    return path


def _dataset_id(path: Path) -> str:
    return hashlib.sha1(str(path).encode('utf-8')).hexdigest()[:12]


def _dataset_rows() -> List[dict]:
    rows = []
    for item in workspace.list_datasets():
        rows.append(dict(id=_dataset_id(item['path']), path=str(item['path']),
                         label=item['label'], source=item['source'], kind=item['kind'],
                         size=item['size'], mtime=item['mtime']))
    return rows


def _digest(path: Path, limit=256 * 1024 * 1024):
    """返回 (hash, kind)：<=256 MB 全量 sha256；更大为首尾 4 MB + 大小的部分指纹。"""
    import hashlib
    size = path.stat().st_size
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        if size <= limit:
            for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b''):
                digest.update(chunk)
        else:
            digest.update(handle.read(4 * 1024 * 1024))
            handle.seek(max(0, size - 4 * 1024 * 1024))
            digest.update(handle.read(4 * 1024 * 1024))
            digest.update(str(size).encode('ascii'))
    return digest.hexdigest(), ('sha256' if size <= limit else 'sha256-partial-headtail-size')


def _payload(path: Path):
    from .memory import precheck
    reason, _info = precheck(path)
    if reason:
        raise HTTPException(413, reason)
    stat = path.stat()
    key = f'{path}|{stat.st_mtime_ns}|{stat.st_size}'
    if key not in _CACHE:
        data = load_data(path)
        _CACHE.clear()
        _CACHE[key] = (data, inspect_data(data, path))
    return _CACHE[key]


def _config(overrides: Optional[dict] = None) -> dict:
    raw = dict(DEFAULTS)
    if overrides:
        unknown = set(overrides) - set(DEFAULTS)
        if unknown:
            raise ValueError('未知配置字段：' + '、'.join(sorted(unknown)))
        raw.update(overrides)
    return normalize_config(raw)


class UploadBody(BaseModel):
    name: str
    content_b64: str


class PublicBody(BaseModel):
    source: str
    identifier: str


class RunBody(BaseModel):
    path: str
    method: str
    config: Optional[dict] = None
    name: Optional[str] = None


class ProjectBody(BaseModel):
    name: str


@app.get('/api/health')
def health():
    from .device import resolve as resolve_device
    return {'ok': True, 'root': str(ROOT), 'queue': workspace.counts(workspace.active_project()['id']),
            'device': resolve_device('auto').device,
            'projects': workspace.active_project()['id']}


@app.get('/api/meta')
def meta():
    project = workspace.project()
    datasets = _dataset_rows()
    counts = workspace.counts(project['id'])
    return dict(project=project, datasets=datasets, counts=counts,
                projects=workspace.projects()['items'],
                active_project=workspace.active_project(),
                methods=[{'key': m.key, 'name': m.name, 'family': FAMILIES[m.family]['label'],
                          'status': status_of(m), 'needs': list(m.needs),
                          'packages': list(m.packages)} for m in METHODS],
                velocity_models=[{'key': k, **v} for k, v in VELOCITY_MODELS.items()],
                default_velocity_model=DEFAULT_VELOCITY_MODEL)


@app.get('/api/methods')
def methods_route():
    return {'methods': [{'key': m.key, 'name': m.name, 'family': FAMILIES[m.family]['label'],
                         'status': status_of(m), 'needs': list(m.needs),
                         'packages': list(m.packages)} for m in METHODS]}


class ProjectCreate(BaseModel):
    name: str
    note: str = ''


@app.post('/api/projects')
def create_project(body: ProjectCreate):
    item = workspace.create_project(body.name, body.note)
    return {'ok': True, 'project': item}


@app.post('/api/projects/{project_id}/activate')
def activate_project(project_id: str, ):
    item = workspace.activate_project(project_id)
    if item is None:
        raise HTTPException(404, '项目不存在')
    return {'ok': True, 'project': item}


@app.post('/api/projects/{project_id}/archive')
def archive_project(project_id: str, archived: int = 1):
    item = workspace.archive_project(project_id, bool(archived))
    if item is None:
        raise HTTPException(400, '项目不存在、已归档，或不能归档最后一个未归档项目')
    return {'ok': True, 'project': item}


@app.delete('/api/projects/{project_id}')
def delete_project(project_id: str):
    ok, message = workspace.delete_project(project_id)
    if not ok:
        raise HTTPException(400, message)
    return {'ok': True, 'message': message}


@app.post('/api/project')
def rename_project(body: ProjectBody):
    workspace.rename_project(body.name)
    return {'ok': True, 'name': workspace.project()['name']}


@app.get('/api/datasets')
def datasets():
    return {'datasets': _dataset_rows()}


ALLOWED_SUFFIX = {'.h5ad', '.loom', '.h5', '.csv', '.tsv', '.txt', '.mtx', '.zip', '.gz', '.tgz', '.tar'}
ALLOWED_COMPOUND = ('.csv.gz', '.tsv.gz', '.txt.gz', '.tar.gz')


def _upload_suffix_ok(name: str) -> bool:
    low = (name or '').lower()
    if low.endswith(ALLOWED_COMPOUND):
        return True
    return Path(low).suffix in ALLOWED_SUFFIX
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024


@app.post('/api/datasets/upload')
async def upload(file: UploadFile):
    """流式落盘 + 边写边限流：超限立即中止，不把整个文件读进内存。"""
    import uuid

    filename = file.filename or 'data'
    if not _upload_suffix_ok(filename):
        suffix = Path(filename).suffix.lower()
        raise HTTPException(415, f'不支持的格式：{suffix or "无扩展名"}')
    low = filename.lower()
    compound = next((item for item in ALLOWED_COMPOUND if low.endswith(item)), None)
    suffix = compound or Path(low).suffix
    target_dir = ROOT / 'results' / 'inputs'
    target_dir.mkdir(parents=True, exist_ok=True)
    temporary = target_dir / f'.upload-{uuid.uuid4().hex}{suffix}'
    digest = hashlib.sha256()
    total = 0
    try:
        with open(temporary, 'wb') as handle:
            while True:
                chunk = await file.read(4 * 1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, f'文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MiB 上限')
                handle.write(chunk)
                digest.update(chunk)
        if total == 0:
            raise HTTPException(400, '文件为空')
        target = target_dir / (digest.hexdigest()[:16] + suffix)
        os.replace(temporary, target)
    except HTTPException:
        temporary.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise HTTPException(400, f'保存上传文件失败：{type(exc).__name__}: {exc}')
    try:
        load_data(target)
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(400, f'无法解析这个文件：{type(exc).__name__}: {exc}')
    workspace.register_dataset(target, name=filename, source='上传')
    return {'ok': True, 'path': str(target), 'id': _dataset_id(target), 'name': filename}


@app.delete('/api/datasets/{dataset_id}')
def delete_dataset(dataset_id: str):
    """移除数据集登记（不删文件）；被任务引用时拒绝，避免任务失去来源。"""
    match = next((item for item in workspace.list_datasets()
                  if _dataset_id(item['path']) == dataset_id), None)
    if match is None:
        raise HTTPException(404, '数据集不存在或已移除')
    from .queue import list_jobs
    project = workspace.active_project()['id']
    references = list_jobs(dataset=match['path'], project=project)
    if references:
        raise HTTPException(
            400,
            f'该数据集仍被 {len(references)} 个任务引用（如 {references[0]["id"][:8]}）；'
            '请先删除这些任务记录，或保留数据集。文件不会被删除。')
    removed = workspace.unregister_dataset(match['path'], project=project)
    return {'ok': removed, 'message': '已移除登记；文件仍保留在原位置'}


class DownloadBody(BaseModel):
    source: str
    identifier: str


@app.post('/api/downloads')
def start_download(body: DownloadBody):
    """启动后台下载（GEO / CELLxGENE / URL），返回任务 id。"""
    from . import downloads
    from .publicdata import SOURCES
    source = (body.source or '').lower()
    if source not in SOURCES:
        raise HTTPException(400, '不支持的来源：' + source)
    identifier = (body.identifier or '').strip()
    if not identifier:
        raise HTTPException(400, '请填写编号、UUID 或链接')
    task_id = downloads.submit(source, identifier, ROOT / 'data' / 'public',
                               project=workspace.active_project()['id'])
    return {'ok': True, 'id': task_id}


@app.get('/api/downloads/{task_id}')
def download_status(task_id: str):
    from . import downloads
    task = downloads.get(task_id)
    if task is None or (task.get('project') or 'default') != workspace.active_project()['id']:
        raise HTTPException(404, '下载任务不存在（可能已过期或服务已重启）')
    return task


@app.get('/api/downloads')
def download_list():
    from . import downloads
    return {'downloads': downloads.list_tasks(project=workspace.active_project()['id'])}


@app.post('/api/downloads/{task_id}/cancel')
def download_cancel(task_id: str):
    download_status(task_id)
    from . import downloads
    ok, message = downloads.cancel(task_id)
    if not ok:
        raise HTTPException(400, message)
    return {'ok': True, 'message': message}


@app.post('/api/datasets/public')
def public_fetch(body: PublicBody):
    from .publicdata import fetch
    identifier = (body.identifier or '').strip()
    if not identifier:
        raise HTTPException(400, '缺少标识符')
    source = body.source if body.source in ('geo', 'cellxgene', 'url') else (
        'geo' if identifier.upper().startswith('GSE') else 'url')
    try:
        files = fetch(source, identifier, ROOT / 'data' / 'public')
    except Exception as exc:
        raise HTTPException(502, f'下载失败：{type(exc).__name__}: {exc}')
    if isinstance(files, (str, Path)):
        files = [Path(files)]
    files = [Path(item) for item in (files or [])]
    if not files:
        raise HTTPException(502, '没有取回可用文件')
    for path in files:
        workspace.register_dataset(path)
    return {'ok': True, 'files': [str(p) for p in files]}


def _dataset_preview(data, limit=5):
    """轻量预览：表达范围、非零比例、前几个细胞/基因与 obs 示例。"""
    import numpy as np

    matrix = data.X
    from scipy import sparse as _sparse
    if _sparse.issparse(matrix):
        nnz = int(matrix.nnz)
        total = int(matrix.shape[0]) * int(matrix.shape[1])
        explicit = np.asarray(matrix.data, dtype=float)
        total_sum = float(matrix.sum())
        stats = {
            'min': min(0.0, float(explicit.min())) if nnz < total and explicit.size else (float(explicit.min()) if explicit.size else 0.0),
            'max': float(explicit.max()) if explicit.size else 0.0,
            'mean': total_sum / total if total else 0.0,
            'nonzero_pct': round(100.0 * nnz / total, 2) if total else 0.0,
            'dtype': str(getattr(matrix, 'dtype', '')),
            'sparse': True,
            'nnz': nnz,
        }
    else:
        values = np.asarray(matrix, dtype=float).ravel()
        stats = {
            'min': float(values.min()) if values.size else 0.0,
            'max': float(values.max()) if values.size else 0.0,
            'mean': float(values.mean()) if values.size else 0.0,
            'nonzero_pct': round(float((values != 0).mean() * 100), 2) if values.size else 0.0,
            'dtype': str(getattr(matrix, 'dtype', '')),
            'sparse': False,
            'nnz': int((values != 0).sum()) if values.size else 0,
        }
    obs_preview = {}
    obs_summary = {}
    for key, column in list((data.obs or {}).items())[:8]:
        array = np.asarray(column)
        if array.ndim != 1:
            continue
        obs_preview[str(key)] = [str(item) for item in array[:limit]]
        try:
            values, counts = np.unique(array.astype(str), return_counts=True)
            order = np.argsort(counts)[::-1][:limit]
            from .terms import lookup as _term
            obs_summary[str(key)] = {
                'unique': int(len(values)),
                'top': [{'value': str(values[i]), 'count': int(counts[i]),
                         'zh': _term(values[i])} for i in order],
            }
        except Exception:
            continue
    return {
        'stats': stats,
        'cells': [str(item) for item in np.asarray(data.cells)[:limit]],
        'genes': [str(item) for item in np.asarray(data.genes)[:10]],
        'layers': list((data.layers or {}).keys()),
        'obs_preview': obs_preview,
        'obs_summary': obs_summary,
    }


@app.get('/api/terms')
def terms():
    """术语参考：只收录含义明确的通用词；未收录保持原文，不做模糊翻译。"""
    from . import terms as glossary
    return {'terms': glossary.glossary(),
            'note': '参考翻译，需自行核对；未收录内容不翻译。'}


@app.get('/api/datasets/{dataset_id}/detail')
def dataset_detail(dataset_id: str):
    """数据集详情：哈希、维度、obs、层、引用任务；不改文件。"""
    match = next((item for item in workspace.list_datasets()
                  if _dataset_id(item['path']) == dataset_id), None)
    if match is None:
        raise HTTPException(404, '数据集不存在或已移除')
    target = _safe(match['path'])
    data, fmt = _payload(target)
    try:
        facts = validate(data, _config({}))
    except Exception as exc:
        facts = {'errors': [f'{type(exc).__name__}: {exc}'], 'warnings': []}
    from .queue import list_jobs
    references = list_jobs(dataset=str(target.resolve()))
    return {
        'id': dataset_id,
        'label': match.get('label') or target.stem,
        'source': match.get('source') or '',
        'kind': match.get('kind') or '',
        'size': match.get('size') or '',
        'mtime': match.get('mtime'),
        'path': str(target),
        'content_hash': _digest(target)[0],
        'hash_kind': _digest(target)[1],
        'n_cells': int(data.X.shape[0]),
        'n_genes': int(data.X.shape[1]),
        'layers': list((fmt or {}).get('layers_present') or []),
        'obs': [item.get('name') if isinstance(item, dict) else str(item)
                for item in ((fmt or {}).get('obs') or [])],
        'x_kind': (facts or {}).get('x_kind') or (fmt or {}).get('x_kind_text') or '',
        'warnings': list((facts or {}).get('warnings') or []) + list((facts or {}).get('errors') or []),
        'tasks': [{'id': job['id'], 'name': job.get('name') or job['id'],
                   'state': job.get('state')} for job in references[:50]],
        'task_count': len(references),
        'memory': __import__('cellflow.memory', fromlist=['estimate']).estimate(target),
        'preview': _dataset_preview(data),
    }


@app.get('/api/datasets/inspect')
def inspect_dataset(path: str, raw_x: int = 0, raw_su: int = 0):
    target = _safe(path)
    data, fmt = _payload(target)
    config = _config({'raw_x_confirmed': bool(raw_x), 'raw_su_confirmed': bool(raw_su)})
    try:
        facts = validate(data, config)
    except Exception as exc:
        facts = {'errors': [f'{type(exc).__name__}: {exc}'], 'warnings': []}
    rows = []
    for row in eligibility(facts, config):
        method = INDEX.get(row['key'])
        if method is None:
            continue
        if method.runtime_env not in ('.venv', '') and env_python(method.key) is not None:
            row['runtime_blockers'] = []
            row['can_run'] = not row.get('scientific_blockers')
        rows.append(dict(key=method.key, name=method.name,
                         status=status_of(method), can_run=bool(row.get('can_run')),
                         needs=list(method.needs), packages=list(method.packages),
                         missing=missing_needs(facts, config, method),
                         blockers=(row.get('runtime_blockers') or []) + (row.get('scientific_blockers') or []),
                         family=FAMILIES[method.family]['label']))
    obs_names = [o.get('name') if isinstance(o, dict) else str(o) for o in (fmt.get('obs') or [])]
    fmt_view = {**fmt, 'obs': obs_names}
    return dict(path=str(target), id=_dataset_id(target), format=fmt_view,
                facts={k: facts[k] for k in ('n_cells', 'n_genes', 'x_kind', 'x_kind_text',
                                             'warnings', 'errors', 'velocity_counts',
                                             'group_present', 'obs_keys', 'synthetic')
                       if k in facts},
                methods=rows, config=config)


@app.post('/api/datasets/demo')
def make_demo():
    from .data import make_demo as create
    target = ROOT / 'examples' / 'synthetic.npz'
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        create(target)
    workspace.register_dataset(target)
    return {'ok': True, 'path': str(target), 'id': _dataset_id(target)}


@app.post('/api/runs')
def create_run(body: RunBody):
    path = _safe(body.path)
    if body.method not in INDEX:
        raise HTTPException(400, f'未知方法：{body.method}')
    try:
        config = _config(body.config)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if body.method == 'auto' and config.get('velocity_model') == 'velovi':
        raise HTTPException(400, '一键分析不直接调用 veloVI；请在模型分析中单独选择 veloVI 运行')
    try:
        job_id = submit(path, {**config, 'method': body.method}, dataset=path, name=body.name)
        start_worker()
    except Exception as exc:
        raise HTTPException(500, f'提交失败：{type(exc).__name__}: {exc}')
    return {'ok': True, 'id': job_id}


@app.get('/api/runs')
def runs(state: str = '', method: str = '', dataset: str = '', q: str = '', limit: int = 100,
         offset: int = 0, project: str = ''):
    from .queue import count_jobs, list_jobs
    items = []
    target = project or workspace.active_project()['id']
    limit = max(1, min(int(limit or 100), 200))
    offset = max(0, int(offset or 0))
    filters = dict(state=state or None, method=method or None,
                   dataset=dataset or None, query=q or None, project=target or 'all')
    total = count_jobs(**filters)
    rows = list_jobs(**filters, limit=limit, offset=offset)
    for job in rows:
        items.append(dict(id=job['id'], name=job.get('name') or job['id'],
                          method=job.get('method'),
                          method_label=INDEX[job['method']].name
                          if job.get('method') in INDEX else job.get('method'),
                          state=job.get('state'), submitted=job.get('submitted'),
                          updated=job.get('updated'), output=job.get('output'),
                          input=job.get('input'), dataset=job.get('dataset') or '',
                          error=job.get('error') or ''))
    return {'runs': items, 'total': total, 'limit': limit,
            'offset': offset, 'has_more': offset + len(items) < total}


class RenameBody(BaseModel):
    name: str


@app.patch('/api/runs/{job_id}')
def rename_run(job_id: str, body: RenameBody):
    _job(job_id)
    from .queue import rename
    ok, message = rename(job_id, body.name)
    if not ok:
        raise HTTPException(400, message)
    return {'ok': True, 'name': body.name.strip()}


@app.delete('/api/runs/{job_id}')
def delete_run(job_id: str):
    _job(job_id)
    from .queue import delete
    ok, message = delete(job_id)
    if not ok:
        raise HTTPException(400, message)
    return {'ok': True, 'message': message}


@app.post('/api/runs/{job_id}/cancel')
def cancel_run(job_id: str):
    _job(job_id)
    from .queue import cancel
    ok, message = cancel(job_id)
    if not ok:
        raise HTTPException(400, message)
    return {'ok': True, 'message': message}


def _job(job_id: str) -> dict:
    from .queue import get_job
    job = get_job(job_id)
    if job is None or job.get('project_id', 'default') != workspace.active_project()['id']:
        raise HTTPException(404, '当前项目中任务不存在')
    return job


def _read(folder: Path, name: str):
    path = folder / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except Exception:
        return None


@app.get('/api/runs/{job_id}')
def run_detail(job_id: str, compact: int = 1):
    job = _job(job_id)
    folder = Path(job['output'])
    figures = []
    fig_dir = folder / 'figures'
    if fig_dir.exists():
        for name in ('embedding.svg', 'qc.svg', 'phase.svg', 'trends.svg', 'paga.svg', 'fate.svg', 'stream.png'):
            if (fig_dir / name).exists():
                figures.append(name)
    quality = _read(folder, 'quality.json')
    provenance = _read(folder, 'provenance.json') or {}
    result = _read(folder, 'result.json')
    omitted = []
    if compact and isinstance(result, dict):
        result = dict(result)
        for key in ('embedding', 'velocity', 'velocity_uncertainty', 'fate_probabilities',
                    'paga_connectivities', 'paga_groups', 'gene_trends'):
            if key in result:
                value = result[key]
                size = len(value) if hasattr(value, '__len__') else 1
                result.pop(key, None)
                omitted.append({'key': key, 'items': int(size)})
        if omitted:
            result['_omitted'] = {'reason': '轮询默认压缩大数组；?compact=0 取完整结果',
                                  'fields': omitted}
    return dict(
        id=job_id, name=job.get('name') or job_id, method=job.get('method'),
        method_label=INDEX[job['method']].name if job.get('method') in INDEX else job.get('method'),
        state=job.get('state'), error=job.get('error') or '',
        submitted=job.get('submitted'), updated=job.get('updated'),
        output=str(folder), input=str(job.get('input') or ''),
        config=_read(folder, 'config.json'), result=result,
        qc=_read(folder, 'qc.json'), provenance=provenance,
        stage=provenance.get('last_stage') or '',
        elapsed_seconds=provenance.get('elapsed_seconds'),
        figures=figures,
        reports={name: (folder / name).exists() for name in
                 ('A_生物学分析报告.md', 'B_技术与复现报告.md', 'A_report.html', 'B_report.html')},
        artifacts={'a_report': (folder / 'A_生物学分析报告.md').exists(),
                   'b_report': (folder / 'B_技术与复现报告.md').exists(),
                   'result': (folder / 'result.json').exists()},
        dataset=job.get('dataset') or '',
        quality=quality,
    )


def _ensure_report_pdf(folder, kind):
    """确保报告 PDF 存在（按需生成）；失败返回 None，不抛给列表接口。"""
    names = {'a': 'A_report.html', 'b': 'B_report.html'}
    if kind not in names:
        return None
    html = Path(folder) / names[kind]
    if not html.is_file():
        return None
    pdf = html.with_suffix('.pdf')
    version_file = Path(folder) / '.pdf_version'
    version = '3-unified-units'
    try:
        cached_version = version_file.read_text(encoding='utf-8').strip()
    except Exception:
        cached_version = ''
    if (pdf.is_file() and pdf.stat().st_mtime >= html.stat().st_mtime
            and cached_version == version):
        return pdf
    import subprocess
    import sys
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
    try:
        header = 'CellFlow · 任务 ' + Path(folder).name + ' · ' + __import__('datetime').datetime.fromtimestamp(html.stat().st_mtime).strftime('%Y-%m-%d')
        subprocess.run([sys.executable, '-m', 'cellflow.pdf', str(html), str(pdf), header],
                       cwd=ROOT, capture_output=True, text=True, timeout=180,
                       creationflags=flags, check=False)
    except Exception:
        return None
    if pdf.is_file() and pdf.stat().st_size > 1024:
        try:
            version_file.write_text(version, encoding='utf-8')
        except Exception:
            pass
        return pdf
    return None


@app.get('/api/runs/{job_id}/compare/{other_id}')
def compare_runs(job_id: str, other_id: str):
    """比较两次运行：配置差异 + 指标差异 + 图像可比性判断。"""
    first = _job(job_id)
    second = _job(other_id)
    first_folder = Path(first['output'])
    second_folder = Path(second['output'])
    left = _read(first_folder, 'config.json') or {}
    right = _read(second_folder, 'config.json') or {}
    keys = sorted(set(left) | set(right))
    changes = []
    for key in keys:
        a = left.get(key)
        b = right.get(key)
        if a != b:
            changes.append({'key': key, 'a': a, 'b': b})

    first_result = _read(first_folder, 'result.json') or {}
    second_result = _read(second_folder, 'result.json') or {}
    metric_a = first_result.get('metrics') or {}
    metric_b = second_result.get('metrics') or {}
    metric_changes = []
    for key in sorted(set(metric_a) | set(metric_b)):
        a = metric_a.get(key)
        b = metric_b.get(key)
        if a != b:
            metric_changes.append({'key': key, 'a': a, 'b': b})

    def figure_names(folder):
        path = folder / 'figures'
        return sorted(item.name for item in path.glob('*.svg')) if path.exists() else []

    figures_a = figure_names(first_folder)
    figures_b = figure_names(second_folder)

    from .compare import figure_contract
    contract = figure_contract(first_result, second_result, left, right, figures_a, figures_b,
                              identical=(job_id == other_id))
    can_compare = bool(contract['can_compare_figures'])
    reasons = list(contract['reasons'])

    return {
        'a': job_id,
        'b': other_id,
        'changes': changes,
        'same': not changes,
        'metric_changes': metric_changes,
        'figures': {'a': figures_a, 'b': figures_b},
        'can_compare_figures': can_compare,
        'compare_reason': '；'.join(reasons),
        'reasons': reasons,
        'same_coordinates': contract['same_coordinates'],
        'same_input_identity': contract['same_input_identity'],
        'metrics_comparable': contract['metrics_comparable'],
        'metrics_note': contract['metrics_note'],
        'side_by_side': contract['side_by_side'],
        'input_identity': contract['input_identity'],
    }


@app.get('/api/runs/{job_id}/report/{kind}/pdf')
def run_report_pdf(job_id: str, kind: str):
    """按需把报告 HTML 转成 PDF（Qt QTextDocument，本地生成）。"""
    job = _job(job_id)
    folder = Path(job['output'])
    names = {'a': 'A_report.html', 'b': 'B_report.html'}
    if kind not in names:
        raise HTTPException(400, '只支持 a / b 两种报告')
    html = folder / names[kind]
    if not html.is_file():
        raise HTTPException(404, '报告 HTML 尚未生成')
    pdf = _ensure_report_pdf(folder, kind)
    if pdf is None:
        raise HTTPException(500, 'PDF 生成失败，请查看 logs/api.log')
    from fastapi.responses import FileResponse
    return FileResponse(pdf, media_type='application/pdf',
                        filename=f'{job_id}_{kind.upper()}_report.pdf')


@app.get('/api/runs/{job_id}/figure/{name}')
def run_figure(job_id: str, name: str):
    job = _job(job_id)
    if '/' in name or '\\' in name:
        raise HTTPException(400, '非法文件名')
    path = Path(job['output']) / 'figures' / name
    if not path.exists():
        raise HTTPException(404, '图形不存在')
    media = 'image/svg+xml' if path.suffix == '.svg' else 'image/png'
    return FileResponse(path, media_type=media)


def _fix_report_refs(text: str, job_id: str) -> str:
    return (text.replace('](figures/', f'](/api/runs/{job_id}/figure/')
                .replace('src="figures/', f'src="/api/runs/{job_id}/figure/')
                .replace("src='figures/", f"src='/api/runs/{job_id}/figure/"))


@app.get('/api/runs/{job_id}/report/{kind}')
def run_report(job_id: str, kind: str):
    job = _job(job_id)
    folder = Path(job['output'])
    names = {'a': 'A_生物学分析报告.md', 'b': 'B_技术与复现报告.md',
             'a_html': 'A_report.html', 'b_html': 'B_report.html'}
    if kind not in names:
        raise HTTPException(400, '未知报告类型')
    path = folder / names[kind]
    if not path.exists():
        raise HTTPException(404, '报告不存在')
    text = path.read_text(encoding='utf-8', errors='replace')
    if kind.endswith('html'):
        return HTMLResponse(_fix_report_refs(text, job_id))
    return PlainTextResponse(_fix_report_refs(text, job_id),
                             media_type='text/markdown; charset=utf-8')


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


@app.get('/api/runs/{job_id}/zip')
def run_zip(job_id: str, full: int = 0):
    """导出 ZIP：默认报告包；full=1 时尽量附带可复现资产（大输入只列哈希）。"""
    job = _job(job_id)
    folder = Path(job['output'])
    for kind in ('a', 'b'):
        _ensure_report_pdf(folder, kind)
    assets = []
    external = []
    hashes = {}
    for name in ('config.json', 'environment.json', 'quality.json', 'provenance.json',
                 'velocity.npz', 'analysis.h5ad', 'result.json'):
        path = folder / name
        if path.is_file():
            hashes[name] = _file_sha256(path)
            assets.append((path, name))
    input_path = Path(job['input']) if job.get('input') else None
    input_hash = None
    if input_path and input_path.is_file():
        input_hash = _file_sha256(input_path)
        size = input_path.stat().st_size
        hashes['input/' + input_path.name] = input_hash
        if full and size <= 200 * 1024 * 1024:
            assets.append((input_path, 'input/' + input_path.name))
        else:
            external.append({'name': input_path.name, 'sha256': input_hash, 'bytes': size,
                             'note': '输入未打包（体积/隐私）；按哈希在本地放置后可复跑'})
    model_budget = 200 * 1024 * 1024
    for model_dir in (folder / 'velovi_model', folder / 'model'):
        if full and model_dir.is_dir():
            for path in sorted(model_dir.rglob('*')):
                if path.is_file() and model_budget - path.stat().st_size >= 0:
                    model_budget -= path.stat().st_size
                    rel = str(path.relative_to(model_dir)).replace(chr(92), '/')
                    assets.append((path, 'model/' + rel))
    source_dir = folder / 'source'
    if full and source_dir.is_dir():
        for path in sorted(source_dir.rglob('*')):
            if path.is_file() and path.stat().st_size < 5 * 1024 * 1024:
                rel = str(path.relative_to(source_dir)).replace(chr(92), '/')
                assets.append((path, 'source/' + rel))
    manifest = {
        'task_id': job_id,
        'name': job.get('name') or job_id,
        'method': job.get('method'),
        'state': job.get('state'),
        'dataset': job.get('dataset') or '',
        'package_kind': 'reproducibility-pack' if full else 'report-pack',
        'reproduce_scope': ('含输入/源码/速度与嵌入，可离线重跑' if full else
                            '报告与验收资产；不含输入/源码，复跑需按 external 的哈希放置输入'),
        'config': _read(folder, 'config.json') or {},
        'environment': _read(folder, 'environment.json') or {},
        'quality': _read(folder, 'quality.json') or {},
        'files': sorted(path.name for path in folder.iterdir() if path.is_file()),
        'file_sha256': hashes,
        'external': external,
        'notes': '本包由 CellFlow 导出；PDF 由报告 HTML 本地转换。报告包不是完整复现包。',
    }
    reproduce = ['# 复跑说明', '',
                 f'- 任务：`{job_id}`（{job.get("method")}）',
                 f'- 包类型：{manifest["package_kind"]}',
                 f'- 输入：`{input_path.name if input_path else "未记录"}`',
                 f'- 输入哈希：`{input_hash or "未记录"}`',
                 '', '## 本地复跑（在 CellFlow 项目根）', '```powershell',
                 '.venv/Scripts/python.exe -m cellflow.cli run `',
                 '  --input <按哈希放置的输入> `',
                 '  --config config.json --output <新目录>', '```', '',
                 '## 注意', '- 输入文件因体积/隐私默认不打包；用 manifest 的 SHA-256 校验后再复跑。',
                 '- 报告包只含报告与图形，不等同于完整复现包。']
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr('REPRODUCE.md', chr(10).join(reproduce) + chr(10))
        bundled = set()
        for path, arcname in bundle_entries(folder):
            zf.write(path, arcname)
            bundled.add(arcname)
        for path, arcname in assets:
            if arcname not in bundled:
                zf.write(path, arcname)
                bundled.add(arcname)
    data = buf.getvalue()
    return Response(content=data, media_type='application/zip',
                    headers={'Content-Disposition': f'attachment; filename="{job_id}_reports.zip"'})


@app.get('/api/logs/{job_id}')
def run_log(job_id: str):
    job = _job(job_id)
    path = Path(job['output']) / 'run.log'
    if not path.exists():
        return {'log': '', 'truncated': False}
    limit = 128 * 1024
    size = path.stat().st_size
    with open(path, 'rb') as handle:
        if size > limit:
            handle.seek(size - limit)
        data = handle.read()
    return {'log': data.decode('utf-8', errors='replace')[-20000:], 'truncated': size > limit}


@app.get('/api/env')
def environment():
    """环境诊断：版本、设备、磁盘、数据目录与前端构建状态。"""
    import platform
    import shutil
    import sys
    from importlib import metadata

    versions = {}
    for name in ('streamlit', 'fastapi', 'uvicorn', 'anndata', 'scanpy', 'scvelo', 'cellrank',
                 'numpy', 'scipy', 'PySide6'):
        try:
            versions[name] = metadata.version(name)
        except Exception:
            versions[name] = None

    from .device import resolve as resolve_device
    device = resolve_device('auto')
    usage = shutil.disk_usage(ROOT)
    results_size = sum(path.stat().st_size for path in (ROOT / 'results').rglob('*')
                       if path.is_file()) if (ROOT / 'results').exists() else 0
    return dict(
        python=sys.version.split()[0], executable=sys.executable,
        platform=platform.platform(), versions=versions,
        device=device.as_dict(),
        disk={'total_gb': round(usage.total / 1024 ** 3, 1),
              'free_gb': round(usage.free / 1024 ** 3, 1),
              'results_gb': round(results_size / 1024 ** 3, 3)},
        paths={'root': str(ROOT), 'results': str(ROOT / 'results'),
               'logs': str(ROOT / 'logs'), 'web_dist': str(WEB_DIST)},
        web_built=(WEB_DIST / 'index.html').is_file(),
        queue=workspace.counts(),
    )



if WEB_DIST.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount('/', StaticFiles(directory=str(WEB_DIST), html=True), name='web')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8600, log_level='warning')
