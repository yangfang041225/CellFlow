"""本机持久队列：全局租约 supervisor、项目级去重、心跳/租约、进程树取消、崩溃恢复。

本轮审计修复（F02/F03/F05/F22/F23）：
- F02 取消：领取任务时记录计算子进程 PID 与创建时间；取消先写 cancel_requested，
  再用受控进程树（Windows `taskkill /T /F`，POSIX 进程组）结束计算进程及 supervisor，
  确认进程真的退出后才写终态 cancelled，避免「界面已取消、计算还在跑」。
- F03 并发：supervisor 表存单行全局租约，`start_worker` 幂等（已有存活 supervisor 时不再启动）；
  领取任务时在同一事务里检查全局并发上限 `max_concurrent`（默认 1，环境变量可配置），
  两个不同任务同时提交时运行峰值不会超过上限。
- F05 项目去重：去重键 = 内容指纹 + project_id；跨项目不复用；同项目用部分唯一索引兜底并发；
  去重判定在复制快照之前完成，复用时不再留下无主快照。
- F22 恢复：drain/worker 启动即 `recover_orphans`；running 任务由心跳线程写 heartbeat/lease_until；
  只有「PID 不存在」或「租约超时」才判失联；失联（failure_kind=interrupted）与执行失败
  （failure_kind=failed）分开记录；worker 重启后会继续调度 queued 任务。
- F23 准备失败留档：领取之后的所有步骤（写配置、读配置、启动 CLI、等待、收尾）都在保护块内，
  任何异常都写终态与 error，不会留下永久 running。

公共签名保持兼容：submit/list_jobs/cancel/recover_orphans/drain/start_worker/rename/delete。
"""
from contextlib import closing
from pathlib import Path
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

from .runner import normalize_config

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / 'results'

ACTIVE_STATES = ('queued', 'running')

# 可配置项（环境变量优先，默认值即验收要求的默认行为）
DEFAULT_MAX_CONCURRENT = 1
DEFAULT_JOB_LEASE_SECONDS = 900.0
DEFAULT_HEARTBEAT_SECONDS = 30.0
DEFAULT_CANCEL_TIMEOUT = 15.0
DEFAULT_DRAIN_WAIT_SECONDS = 600.0
SUPERVISOR_START_GRACE = 30.0      # 暂定租约（还没写 pid）的有效期
PID_CREATE_TOLERANCE = 1e-6         # 创建时间比较容差（秒）

# F02/F22 新增列：计算进程、心跳、租约、失联原因、尝试次数
_EXTRA_COLUMNS = (
    ('fingerprint', 'TEXT'), ('pid', 'INTEGER'), ('dataset', 'TEXT'), ('name', 'TEXT'),
    ('cancel_requested', 'INTEGER'), ('project_id', 'TEXT'),
    ('pid_created', 'REAL'), ('child_pid', 'INTEGER'), ('child_created', 'REAL'),
    ('heartbeat', 'REAL'), ('lease_until', 'REAL'), ('failure_kind', 'TEXT'),
    ('attempt', 'INTEGER'),
)

_JOB_COLUMNS = ('id', 'method', 'state', 'submitted', 'updated', 'input', 'config', 'output',
                'runtime', 'error', 'fingerprint', 'pid', 'dataset', 'name',
                'cancel_requested', 'project_id', 'pid_created', 'child_pid', 'child_created',
                'heartbeat', 'lease_until', 'failure_kind', 'attempt')


# ------------------------------------------------------------------ 配置
def _env_number(name, default):
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == '':
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def max_concurrent_limit():
    """全局计算并发上限（默认 1）；可用环境变量 CELLFLOW_MAX_CONCURRENT 配置。"""
    return max(1, int(_env_number('CELLFLOW_MAX_CONCURRENT', DEFAULT_MAX_CONCURRENT)))


def _job_lease_seconds():
    """单个任务租约时长；心跳会不断续租。可用 CELLFLOW_JOB_LEASE_SECONDS 配置。"""
    return max(5.0, _env_number('CELLFLOW_JOB_LEASE_SECONDS', DEFAULT_JOB_LEASE_SECONDS))


def _heartbeat_seconds():
    return max(0.05, _env_number('CELLFLOW_HEARTBEAT_SECONDS', DEFAULT_HEARTBEAT_SECONDS))


def _cancel_timeout():
    return max(1.0, _env_number('CELLFLOW_CANCEL_TIMEOUT', DEFAULT_CANCEL_TIMEOUT))


def _drain_wait_seconds():
    return max(0.0, _env_number('CELLFLOW_DRAIN_WAIT_SECONDS', DEFAULT_DRAIN_WAIT_SECONDS))


# ------------------------------------------------------------------ 存储
_SCHEMA_READY = set()
_SCHEMA_LOCK = threading.Lock()


def _migrate(store):
    """建表/补列/建索引一次完成（进程内只跑一次，跨进程用事务串行化）。

    以前每个 connect() 都跑一遍 DDL：多个并发提交会同时 ALTER TABLE，
    第二个连接报 `duplicate column name` 或 `database is locked`。
    """
    db = sqlite3.connect(Path(store) / 'queue.sqlite', timeout=30)
    try:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, method TEXT, state TEXT, '
                   'submitted REAL, updated REAL, input TEXT, config TEXT, output TEXT, '
                   'runtime TEXT, error TEXT)')
        columns = {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
        for name, ddl in _EXTRA_COLUMNS:
            if name not in columns:
                db.execute(f'ALTER TABLE jobs ADD COLUMN {name} {ddl}')
        db.execute('CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state)')
        db.execute('CREATE INDEX IF NOT EXISTS idx_jobs_fp ON jobs(fingerprint)')
        # F05：同一项目内同一去重键最多只有一个活动任务；跨项目互不影响。
        # 旧库里可能已有历史重复行，建索引失败时退回业务层去重，不让连接直接失败。
        try:
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_active_dedupe "
                       "ON jobs(COALESCE(project_id, 'default'), fingerprint) "
                       "WHERE state IN ('queued', 'running')")
        except sqlite3.IntegrityError:
            pass
        db.execute('CREATE TABLE IF NOT EXISTS supervisor (id INTEGER PRIMARY KEY CHECK (id = 1), '
                   'token TEXT, pid INTEGER, pid_created REAL, heartbeat REAL, lease_until REAL, '
                   'started REAL)')
        db.commit()
        try:
            db.execute('PRAGMA journal_mode=WAL')
        except sqlite3.Error:
            pass
    finally:
        db.close()


def _ensure_schema(store):
    key = os.path.abspath(str(store))
    with _SCHEMA_LOCK:
        if key in _SCHEMA_READY:
            return
        error = None
        for attempt in range(6):
            try:
                _migrate(store)
                if len(_SCHEMA_READY) > 4096:
                    _SCHEMA_READY.clear()
                _SCHEMA_READY.add(key)
                return
            except sqlite3.OperationalError as exc:   # 与另一个进程的建表事务撞车
                error = exc
                time.sleep(0.05 * (attempt + 1))
        if error is not None:
            raise error


def connect(store=STORE):
    store = Path(store)
    store.mkdir(parents=True, exist_ok=True)
    _ensure_schema(store)
    db = sqlite3.connect(store / 'queue.sqlite', timeout=30)
    db.row_factory = sqlite3.Row
    return db


def fingerprint(path, config):
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256((digest + '|' + payload).encode('utf-8')).hexdigest()


def find_active(store, fp, project=None):
    """查找同指纹的活动任务；给 project 时只在项目内查找（F05）。"""
    sql = "SELECT id FROM jobs WHERE fingerprint=? AND state IN ('queued','running')"
    values = [fp]
    if project is not None:
        sql += " AND COALESCE(project_id, 'default')=?"
        values.append(str(project or 'default'))
    sql += ' ORDER BY submitted LIMIT 1'
    with closing(connect(store)) as db:
        row = db.execute(sql, values).fetchone()
        return row['id'] if row else None


# ------------------------------------------------------------------ 进程工具（F02/F22）
def _open_process(pid, access):
    import ctypes
    from ctypes import wintypes
    api = ctypes.windll.kernel32
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    api.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.c_void_p] * 4
    return api.OpenProcess(int(access), False, int(pid))


def _pid_alive(pid):
    """PID 是否仍然存在（Windows 用 GetExitCodeProcess==STILL_ACTIVE）。"""
    if pid in (None, '', 0):
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        handle = _open_process(pid, 0x1000)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _process_create_time(pid):
    """进程创建时间，用于识别 PID 复用。

    Windows 返回 Unix 纪元秒；POSIX 返回 /proc/<pid>/stat 的启动时钟（仅做相等比较）。
    进程不存在或拿不到时返回 None。
    """
    if pid in (None, '', 0):
        return None
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    if os.name == 'nt':
        import ctypes

        class _FileTime(ctypes.Structure):
            _fields_ = [('dwLowDateTime', ctypes.c_uint32),
                        ('dwHighDateTime', ctypes.c_uint32)]

        handle = _open_process(pid, 0x1000)
        if not handle:
            return None
        try:
            created, exited = _FileTime(), _FileTime()
            kernel_t, user_t = _FileTime(), _FileTime()
            ok = ctypes.windll.kernel32.GetProcessTimes(
                handle, ctypes.byref(created), ctypes.byref(exited),
                ctypes.byref(kernel_t), ctypes.byref(user_t))
            if not ok:
                return None
            ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
            return ticks / 1e7 - 11644473600.0
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        stat = Path(f'/proc/{pid}/stat').read_text(encoding='utf-8', errors='replace')
        fields = stat.rsplit(')', 1)[1].split()
        return float(fields[19])
    except Exception:
        return None


def _identity_matches(pid, created):
    """PID 存在且创建时间一致，才认为仍是同一个进程。

    缺失或无法读取身份时禁止杀进程。
    """
    if not _pid_alive(pid):
        return False
    if created in (None, '', 0, 0.0):
        return False
    current = _process_create_time(pid)
    if current is None:
        return False
    try:
        return abs(float(current) - float(created)) <= PID_CREATE_TOLERANCE
    except (TypeError, ValueError):
        return False


def _terminate_tree(pid):
    """结束一个进程及其全部子进程（受控进程树）。

    Windows：`taskkill /T /F`（/T 连子进程一起结束），失败时退回 TerminateProcess；
    POSIX：先 SIGTERM 进程组，再 SIGKILL 单进程。绝不结束当前进程自己。
    """
    if pid in (None, '', 0):
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0 or pid == os.getpid():
        return False
    if os.name == 'nt':
        try:
            completed = subprocess.run(['taskkill', '/T', '/F', '/PID', str(pid)],
                                       stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if completed.returncode == 0:
                return True
        except OSError:
            pass
        import ctypes
        handle = _open_process(pid, 0x0001)  # PROCESS_TERMINATE
        if not handle:
            return False
        try:
            return bool(ctypes.windll.kernel32.TerminateProcess(handle, 1))
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    import signal
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        return True
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
            return True
        except OSError:
            return False


def _wait_dead(pids, timeout):
    """等待给定 PID 全部退出；返回超时后仍存活的 PID 列表。"""
    deadline = time.time() + max(0.0, float(timeout))
    alive = [int(pid) for pid in pids if _pid_alive(pid)]
    while alive and time.time() < deadline:
        time.sleep(0.1)
        alive = [pid for pid in alive if _pid_alive(pid)]
    return alive


# ------------------------------------------------------------------ 提交（F05/F23）
def _display_name(method, dataset_path):
    from .registry import INDEX
    label = INDEX[method].name if method in INDEX else method
    stem = Path(dataset_path).stem
    if len(stem) >= 16 and all(ch in '0123456789abcdef' for ch in stem[:16]):
        stem = '导入数据'
    return f'{label} · {stem} · {time.strftime("%Y-%m-%d %H:%M")}'


def _active_project_id():
    try:
        from .workspace import active_project
        return active_project()["id"]
    except Exception:
        return 'default'


def _insert_values(row):
    return {key: row.get(key) for key in _JOB_COLUMNS}


def submit(path, config, store=STORE, dataset=None, name=None, project=None):
    """提交任务；同一项目内相同内容+配置的活动任务直接复用（F05）。

    去重键包含 project_id：项目 B 提交相同内容不会复用项目 A 的任务。
    去重判定在复制快照之前完成；只有确定要新建任务时才复制，复用时不会留下无主快照。
    """
    c = normalize_config(config)
    if project is None:
        project = _active_project_id()
    project = str(project or 'default')
    store = Path(store).resolve()
    source = Path(path)
    # 先按原始文件算指纹，这样复用路径完全不落快照（F05）
    fp = fingerprint(source, c)
    existing = find_active(store, fp, project=project)
    if existing:
        return existing

    inputs = store / 'inputs'
    inputs.mkdir(parents=True, exist_ok=True)
    identity = uuid.uuid4().hex[:16]
    snapshot = inputs / (identity + source.suffix.lower())
    staging = inputs / (identity + '.part' + source.suffix.lower())
    try:
        shutil.copyfile(source, staging)
        os.replace(staging, snapshot)   # 先校验再正式发布
    except BaseException:
        staging.unlink(missing_ok=True)
        snapshot.unlink(missing_ok=True)
        raise

    from .registry import env_python
    runtime = env_python(c['method'], root=ROOT) or Path(sys.executable)
    output = store / identity
    dataset_path = str(Path(dataset or path).resolve())
    now = time.time()
    row = dict(id=identity, method=c['method'], state='queued', submitted=now, updated=now,
               input=str(snapshot), config=json.dumps(c), output=str(output),
               runtime=str(runtime), error='', fingerprint=fp, pid=None,
               dataset=dataset_path, name=name or _display_name(c['method'], dataset_path),
               cancel_requested=0, project_id=project, pid_created=None, child_pid=None,
               child_created=None, heartbeat=now, lease_until=None, failure_kind=None,
               attempt=0)

    db = connect(store)
    try:
        db.execute('BEGIN IMMEDIATE')
        # 事务内再查一次：另一个进程可能刚刚抢先创建（同项目并发提交仍唯一）
        existing = db.execute(
            "SELECT id FROM jobs WHERE fingerprint=? AND COALESCE(project_id, 'default')=? "
            "AND state IN ('queued','running') ORDER BY submitted LIMIT 1",
            (fp, project)).fetchone()
        if existing:
            db.rollback()
            snapshot.unlink(missing_ok=True)
            return existing['id']
        placeholders = ', '.join(':' + key for key in _JOB_COLUMNS)
        db.execute(f'INSERT INTO jobs ({", ".join(_JOB_COLUMNS)}) VALUES ({placeholders})',
                   _insert_values(row))
        db.commit()
    except sqlite3.IntegrityError:
        # 部分唯一索引兜底：并发下同一项目同一指纹只会留下一个任务
        db.rollback()
        snapshot.unlink(missing_ok=True)
        existing = find_active(store, fp, project=project)
        if existing:
            return existing
        raise
    except BaseException:
        db.rollback()
        snapshot.unlink(missing_ok=True)
        raise
    finally:
        db.close()
    return identity


# ------------------------------------------------------------------ 查询/元数据
def _job_filters(state=None, method=None, dataset=None, query=None, project=None):
    clauses, values = [], []
    if state:
        clauses.append('state = ?')
        values.append(state)
    if method:
        clauses.append('method = ?')
        values.append(method)
    if dataset:
        clauses.append('dataset = ?')
        values.append(str(Path(dataset).resolve()))
    if project and project != 'all':
        clauses.append("COALESCE(project_id, 'default') = ?")
        values.append(project)
    if query:
        clauses.append('(id LIKE ? OR name LIKE ? OR method LIKE ?)')
        like = f'%{query}%'
        values.extend([like, like, like])
    return (('WHERE ' + ' AND '.join(clauses)) if clauses else ''), values


def list_jobs(store=STORE, state=None, method=None, dataset=None, query=None, limit=None,
              project=None, offset=None):
    """服务端筛选 + 分页：状态 / 方法 / 数据集 / 名称或 ID 关键词。"""
    where, values = _job_filters(state, method, dataset, query, project)
    sql = f'SELECT * FROM jobs {where} ORDER BY submitted DESC'
    if limit:
        sql += f' LIMIT {int(limit)}'
        if offset:
            sql += f' OFFSET {int(offset)}'
    with closing(connect(store)) as db:
        return [dict(r) for r in db.execute(sql, values)]


def count_jobs(store=STORE, state=None, method=None, dataset=None, query=None, project=None):
    where, values = _job_filters(state, method, dataset, query, project)
    with closing(connect(store)) as db:
        row = db.execute(f'SELECT COUNT(*) AS n FROM jobs {where}', values).fetchone()
        return int(row['n']) if row else 0


def counts(store=STORE, project=None):
    """按项目聚合任务状态；不遍历全部行。"""
    clause, values = '', []
    if project and project != 'all':
        clause = "WHERE COALESCE(project_id, 'default') = ?"
        values = [project]
    out = {'queued': 0, 'running': 0, 'completed': 0, 'failed': 0, 'cancelled': 0}
    with closing(connect(store)) as db:
        for row in db.execute(f'SELECT state, COUNT(*) AS n FROM jobs {clause} GROUP BY state', values):
            out[row['state']] = int(row['n'])
    return out


def get_job(job_id, store=STORE):
    return _job_row(store, job_id)


def _job_row(store, job_id):
    with closing(connect(store)) as db:
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        return dict(row) if row else None


def _running_count(store):
    with closing(connect(store)) as db:
        return db.execute("SELECT COUNT(*) AS n FROM jobs WHERE state='running'").fetchone()['n']


def _pending_count(store):
    with closing(connect(store)) as db:
        return db.execute("SELECT COUNT(*) AS n FROM jobs WHERE state='queued' "
                          "AND COALESCE(cancel_requested,0)=0").fetchone()['n']


def worker_lease(store=STORE):
    """当前 supervisor 全局租约（没有则 None）；供诊断与测试使用。"""
    with closing(connect(store)) as db:
        row = db.execute('SELECT * FROM supervisor WHERE id=1').fetchone()
        return dict(row) if row else None


# ------------------------------------------------------------------ 取消（F02）
def cancel(job_id, store=STORE):
    """取消任务：排队中的直接取消；运行中的结束受控进程树并确认退出后才写终态。"""
    row = _job_row(store, job_id)
    if row is None:
        return False, '任务不存在'
    state = row.get('state')
    if state not in ACTIVE_STATES:
        return False, f'任务当前状态为 {state}，不能取消'

    if state == 'queued':
        with closing(connect(store)) as db, db:
            db.execute("UPDATE jobs SET state='cancelled', cancel_requested=1, updated=?, "
                       "error=?, lease_until=NULL, failure_kind=NULL WHERE id=?",
                       (time.time(), '用户取消', job_id))
        return True, '已取消'

    # 运行中：先请求取消（计算端/supervisor 都能看到），再终止受控进程树
    with closing(connect(store)) as db, db:
        db.execute('UPDATE jobs SET cancel_requested=1, updated=? WHERE id=?',
                   (time.time(), job_id))

    targets = []
    pid, created = row.get('child_pid'), row.get('child_created')
    if pid and _pid_alive(pid) and not _identity_matches(pid, created):
        return False, '已请求取消；进程身份无法验证，未执行终止'
    if _identity_matches(pid, created) and int(pid) != os.getpid():
        targets.append(int(pid))
        if os.name == 'nt':
            from .process_job import terminate, job_name
            if not terminate(job_name(store, job_id)):
                return False, '已请求取消；无法打开受控 Job Object，未按 PID 强制终止'
        else:
            _terminate_tree(pid)
    if not targets:
        return False, '已请求取消，等待 supervisor 确认（启动期间不提前写终态）'
    alive = _wait_dead(targets, _cancel_timeout())

    now = time.time()
    if alive:
        # 没有确认退出：保持 running + cancel_requested，交给 supervisor 收尾
        with closing(connect(store)) as db, db:
            db.execute('UPDATE jobs SET updated=? WHERE id=?', (now, job_id))
        return False, f'已请求取消，但进程 {alive} 未在 {_cancel_timeout():.0f} 秒内退出'

    with closing(connect(store)) as db, db:
        db.execute("UPDATE jobs SET state='cancelled', cancel_requested=1, updated=?, error=?, "
                   "pid=NULL, child_pid=NULL, lease_until=NULL, failure_kind=NULL WHERE id=?",
                   (now, '用户取消', job_id))
    return True, '已取消'


# ------------------------------------------------------------------ 恢复/心跳（F22）
def recover_orphans(store=STORE):
    """回收失联的 running 任务，返回被回收的 id 列表。

    只有两种情况算失联：worker PID 已不存在（或创建时间不符，说明 PID 被复用），
    或者任务租约已超时。PID 活着且租约有效时，无论 updated 多久没变都不判失败。
    """
    recovered = []
    now = time.time()
    with closing(connect(store)) as db, db:
        rows = db.execute("SELECT id, pid, pid_created, child_pid, child_created, heartbeat, "
                          "lease_until, updated FROM jobs WHERE state='running'").fetchall()
        for row in rows:
            alive = _identity_matches(row['pid'], row['pid_created'])
            lease = row['lease_until']
            expired = lease is not None and now > float(lease)
            if alive and not expired:
                continue
            if row['pid'] and _pid_alive(row['pid']) and _process_create_time(row['pid']) is None:
                continue  # 身份未知时不能释放并发名额或误杀
            if _identity_matches(row['child_pid'], row['child_created']):
                if os.name == 'nt':
                    from .process_job import terminate, job_name
                    if not terminate(job_name(store, row['id'])):
                        continue
                else:
                    _terminate_tree(row['child_pid'])
                if _wait_dead([row['child_pid']], _cancel_timeout()):
                    continue
            reason = '任务租约超时' if expired else 'worker 进程已不存在'
            db.execute("UPDATE jobs SET state='failed', failure_kind='interrupted', updated=?, "
                       "error=?, pid=NULL, child_pid=NULL, lease_until=NULL WHERE id=?",
                       (now, f'失联：{reason}（interrupted）；任务未完成，日志保留', row['id']))
            recovered.append(row['id'])
    return recovered


def heartbeat(store=STORE, pid=None, job_id=None, lease_seconds=None):
    """续租：刷新 running 任务与 supervisor 的 heartbeat/lease_until，返回刷新行数。"""
    now = time.time()
    lease = now + (lease_seconds if lease_seconds is not None else _job_lease_seconds())
    pid = os.getpid() if pid in (None, '') else int(pid)
    with closing(connect(store)) as db, db:
        if job_id:
            cursor = db.execute("UPDATE jobs SET heartbeat=?, updated=?, lease_until=? "
                                "WHERE id=? AND state='running'", (now, now, lease, job_id))
        else:
            cursor = db.execute("UPDATE jobs SET heartbeat=?, updated=?, lease_until=? "
                                "WHERE state='running' AND pid=?", (now, now, lease, pid))
        db.execute('UPDATE supervisor SET heartbeat=?, lease_until=? WHERE id=1 AND pid=?',
                   (now, lease, pid))
        return cursor.rowcount


# ------------------------------------------------------------------ supervisor 全局租约（F03）
def _supervisor_row(db):
    return db.execute('SELECT * FROM supervisor WHERE id=1').fetchone()


def _supervisor_alive(row):
    if row is None:
        return False
    if row['pid'] and _identity_matches(row['pid'], row['pid_created']):
        return True
    # 暂定租约：刚启动、worker 还没写 pid；给一个短保护期避免重复拉起
    if not row['pid'] and row['token']:
        return (time.time() - float(row['heartbeat'] or 0)) < SUPERVISOR_START_GRACE
    return False


def start_worker(store=STORE):
    """启动（或复用）唯一 supervisor；返回是否真的启动了新进程（幂等）。"""
    store = Path(store).resolve()
    store.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    db = connect(store)
    try:
        db.execute('BEGIN IMMEDIATE')
        if _supervisor_alive(_supervisor_row(db)):
            db.rollback()
            return False
        now = time.time()
        db.execute('INSERT OR REPLACE INTO supervisor '
                   '(id, token, pid, pid_created, heartbeat, lease_until, started) '
                   'VALUES (1, ?, NULL, NULL, ?, ?, ?)',
                   (token, now, now + _job_lease_seconds(), now))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()

    args = [sys.executable, '-m', 'cellflow.queue', '--store', str(store), '--token', token]
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {
        'start_new_session': True}
    handle = (store / 'worker.log').open('a', encoding='utf-8')
    try:
        subprocess.Popen(args, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=handle,
                         stderr=handle, **kwargs)
    except OSError:
        _release_supervisor(store, token)
        return False
    finally:
        handle.close()
    return True


def _adopt_supervisor(store, token):
    """worker 启动时接管 supervisor 租约；已有别的存活 supervisor 时返回 False。"""
    db = connect(store)
    try:
        db.execute('BEGIN IMMEDIATE')
        row = _supervisor_row(db)
        now = time.time()
        if row is not None and row['token'] != token and _supervisor_alive(row):
            db.rollback()
            return False
        pid = os.getpid()
        created = _process_create_time(pid)
        db.execute('INSERT OR REPLACE INTO supervisor '
                   '(id, token, pid, pid_created, heartbeat, lease_until, started) '
                   'VALUES (1, ?, ?, ?, ?, ?, ?)',
                   (token, pid, created, now, now + _job_lease_seconds(),
                    row['started'] if row is not None else now))
        db.commit()
        return True
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _release_supervisor(store, token):
    try:
        with closing(connect(store)) as db, db:
            db.execute('DELETE FROM supervisor WHERE id=1 AND token=?', (token,))
    except sqlite3.Error:
        pass


def _start_heartbeat_thread(store, interval=None):
    """后台心跳：只刷新本进程名下 running 任务的租约。"""
    interval = _heartbeat_seconds() if interval is None else interval
    stop = threading.Event()
    pid = os.getpid()

    def loop():
        while not stop.wait(interval):
            try:
                heartbeat(store, pid=pid)
            except Exception:
                # 心跳失败不能让计算中断；租约到期后会被判定失联
                pass

    thread = threading.Thread(target=loop, name='cellflow-queue-heartbeat', daemon=True)
    thread.start()
    return thread, stop


# ------------------------------------------------------------------ 领取/执行（F03/F23）
def claim_next(store=STORE, max_concurrent=None, worker_pid=None):
    """原子领取一个 queued 任务；达到全局并发上限或没有任务时返回 None。"""
    limit = max(1, int(max_concurrent if max_concurrent is not None else max_concurrent_limit()))
    db = connect(store)
    try:
        db.execute('BEGIN IMMEDIATE')
        running = db.execute("SELECT COUNT(*) AS n FROM jobs WHERE state='running'").fetchone()['n']
        if running >= limit:
            db.rollback()
            return None
        row = db.execute("SELECT * FROM jobs WHERE state='queued' AND COALESCE(cancel_requested,0)=0 "
                         "ORDER BY submitted LIMIT 1").fetchone()
        if row is None:
            db.rollback()
            return None
        now = time.time()
        pid = os.getpid() if worker_pid is None else int(worker_pid)
        created = _process_create_time(pid)
        lease = now + _job_lease_seconds()
        db.execute("UPDATE jobs SET state='running', updated=?, pid=?, pid_created=?, heartbeat=?, "
                   "lease_until=?, attempt=COALESCE(attempt, 0)+1 WHERE id=?",
                   (now, pid, created, now, lease, row['id']))
        db.commit()
        job = dict(row)
        job.update(state='running', pid=pid, pid_created=created, heartbeat=now,
                   lease_until=lease, cancel_requested=0)
        return job
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _finalize(store, job_id, state, error='', failure_kind=None):
    """写终态；已取消的任务不被迟到的执行结果覆盖。"""
    with closing(connect(store)) as db, db:
        row = db.execute('SELECT state, cancel_requested FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            return
        if row['state'] not in ('running', 'cancelled'):
            return
        if row['state'] == 'cancelled' or row['cancel_requested']:
            db.execute("UPDATE jobs SET state='cancelled', updated=?, error=?, failure_kind=NULL, "
                       "pid=NULL, child_pid=NULL, lease_until=NULL WHERE id=?",
                       (time.time(), '用户取消', job_id))
            return
        db.execute('UPDATE jobs SET state=?, updated=?, error=?, failure_kind=?, pid=NULL, '
                   'child_pid=NULL, lease_until=NULL WHERE id=?',
                   (state, time.time(), error, failure_kind, job_id))


def _record_child(store, job_id, pid, created):
    with closing(connect(store)) as db, db:
        db.execute('UPDATE jobs SET child_pid=?, child_created=?, updated=? WHERE id=?',
                   (pid, created, time.time(), job_id))


def _run_job(store, job):
    """执行一个已领取的任务；任何准备/执行异常都写终态（F23）。"""
    job_id = job['id']
    try:
        current = _job_row(store, job_id)
        if current is None:
            return
        if current['state'] == 'cancelled' or current.get('cancel_requested'):
            _finalize(store, job_id, 'cancelled', '用户取消')
            return

        config_path = store / (job_id + '.json')
        staging = store / (job_id + '.json.part')
        staging.write_text(job['config'], encoding='utf-8')
        os.replace(staging, config_path)     # 原子发布，避免半份配置
        config = json.loads(job['config'])
        env = dict(os.environ, PYTHONHASHSEED=str(config.get('seed', 0)),
                   OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
                   MKL_NUM_THREADS='1', NUMBA_NUM_THREADS='1')
        log = (store / (job_id + '.worker.log')).open('w', encoding='utf-8')
        try:
            command = [job['runtime'], '-m', 'cellflow.cli', 'run',
                       '--input', job['input'], '--config', str(config_path),
                       '--output', job['output']]
            job_handle = None
            if os.name == 'nt':
                from .process_job import create, job_name
                name = job_name(store, job_id)
                job_handle = create(name)
                ready_path = store / (job_id + '.job-ready')
                ready_path.unlink(missing_ok=True)
                command = [sys.executable, '-m', 'cellflow.process_job', name, str(ready_path), *command]
            proc = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=log,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                                    start_new_session=os.name != 'nt')
            if os.name == 'nt':
                deadline = time.monotonic() + 10
                while not ready_path.exists() and proc.poll() is None:
                    if time.monotonic() > deadline:
                        proc.kill()  # wrapper has not started any computation yet
                        proc.wait()
                        raise RuntimeError('Job Object assignment handshake timed out')
                    time.sleep(0.02)
                if not ready_path.exists():
                    raise RuntimeError('Job Object assignment failed; computation was not started')
            created = _process_create_time(proc.pid)
            _record_child(store, job_id, proc.pid, created)
            # 取消可能发生在「领取」与「启动 CLI」之间：启动后立刻复查
            fresh = _job_row(store, job_id)
            if fresh is not None and fresh.get('cancel_requested'):
                if os.name == 'nt':
                    from .process_job import terminate
                    terminate(name)
                else:
                    _terminate_tree(proc.pid)
            returncode = proc.wait()
        finally:
            if job_handle is not None:
                from .process_job import close
                close(job_handle)
            log.close()

        if returncode == 0:
            _finalize(store, job_id, 'completed')
        else:
            _finalize(store, job_id, 'failed',
                      '执行失败；查看结果目录中的 run.log 或任务 worker.log', 'failed')
    except BaseException as exc:
        _finalize(store, job_id, 'failed', f'{type(exc).__name__}: {exc}', 'failed')


# ------------------------------------------------------------------ 调度主循环
def drain(store=STORE, token=None, once=False, max_concurrent=None, max_wait=None):
    """supervisor 主循环：接管租约 → 启动恢复 → 循环领取执行。

    返回是否真正接管了 supervisor 租约；已有存活 supervisor 时立即返回 False。
    """
    store = Path(store).resolve()
    store.mkdir(parents=True, exist_ok=True)
    token = token or ('direct-' + uuid.uuid4().hex)
    if not _adopt_supervisor(store, token):
        return False
    thread, stop = _start_heartbeat_thread(store)
    wait_seconds = _drain_wait_seconds() if max_wait is None else max(0.0, float(max_wait))
    wait_deadline = time.time() + wait_seconds
    try:
        recover_orphans(store)   # F22：worker/drain 启动时恢复
        while True:
            job = claim_next(store, max_concurrent=max_concurrent)
            if job is None:
                if once or _pending_count(store) == 0 or time.time() > wait_deadline:
                    break
                # 有排队任务但并发已满：等其它 supervisor 收尾或租约到期
                time.sleep(0.2)
                continue
            _run_job(store, job)
            if once:
                break
    finally:
        stop.set()
        thread.join(timeout=2)
        _release_supervisor(store, token)
        # 收尾竞态：supervisor 判断「队列已空」到释放租约之间可能有新提交，
        # 而那次提交的 start_worker 会看到旧租约、不再拉起 worker。这里补一次唤醒。
        if not once:
            try:
                if _running_count(store) == 0 and _pending_count(store) > 0:
                    start_worker(store)
            except Exception:
                pass
    return True


# ------------------------------------------------------------------ 元数据维护
def rename(job_id, name, store=STORE):
    """修改任务显示名称（不影响执行与产物）。"""
    name = (name or '').strip()
    if not name:
        return False, '名称不能为空'
    with closing(connect(store)) as db, db:
        row = db.execute('SELECT id FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            return False, '任务不存在'
        db.execute('UPDATE jobs SET name=?, updated=? WHERE id=?', (name, time.time(), job_id))
    return True, '已重命名'


def delete(job_id, store=STORE):
    """删除任务记录；运行中的任务不允许删除。结果目录默认保留。"""
    with closing(connect(store)) as db, db:
        row = db.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            return False, '任务不存在'
        if row['state'] in ACTIVE_STATES:
            return False, '任务仍在排队或运行中，请先取消'
        db.execute('DELETE FROM jobs WHERE id=?', (job_id,))
    return True, '已删除任务记录（结果文件保留）'


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='CellFlow 队列 supervisor')
    parser.add_argument('--store', type=Path, default=STORE)
    parser.add_argument('--token', default=None)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--max-concurrent', type=int, default=None)
    args = parser.parse_args()
    drain(args.store, token=args.token, once=args.once, max_concurrent=args.max_concurrent)
