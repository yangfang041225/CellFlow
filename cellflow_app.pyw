"""CellFlow native Qt desktop entry. No browser or console fallback."""
from pathlib import Path
import datetime as dt
import json
import os
import socket
import subprocess
import sys
import time
import traceback
import urllib.request

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from cellflow.desktop import DesktopInstance, focus_owner

LOGS = ROOT / 'logs'
LOGS.mkdir(exist_ok=True)
LOG = LOGS / 'app.log'
ERR = LOGS / 'app-error.log'
DIST = ROOT / 'web/dist'
TITLE = 'CellFlow'
PORT = int(os.environ.get('CELLFLOW_PORT', '8600'))
if not 1024 <= PORT <= 65535:
    raise ValueError('CELLFLOW_PORT must be between 1024 and 65535')


def log(message):
    with LOG.open('a', encoding='utf-8') as f:
        f.write(f'[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] pid={os.getpid()} {message}\n')


def fail(message):
    with ERR.open('a', encoding='utf-8') as f:
        f.write(f'[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n')
    log('ERROR: ' + message)
    if os.name == 'nt':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message[:1400], 'CellFlow 启动失败', 0x10)


def healthy(port, timeout=1.0):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f'http://127.0.0.1:{port}/api/health', timeout=timeout) as response:
            data = json.load(response)
        return data.get('ok') is True and Path(data.get('root', '')).resolve() == ROOT
    except Exception:
        return False


def start_api(port):
    executable = ROOT / '.venv/Scripts/pythonw.exe'
    command = [str(executable), '-m', 'uvicorn', 'cellflow.api:app',
               '--host', '127.0.0.1', '--port', str(port), '--log-level', 'warning']
    log('启动 API: ' + subprocess.list2cmdline(command))
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
    from cellflow.queue import _process_create_time
    created = _process_create_time(os.getpid())
    if created is None:
        raise RuntimeError('无法确认启动器身份，拒绝启动无身份绑定的 API')
    env = dict(os.environ, CELLFLOW_PARENT_PID=str(os.getpid()), CELLFLOW_PARENT_CREATED=str(created))
    with (LOGS / 'api.log').open('a', encoding='utf-8') as handle:
        process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                   stdout=handle, stderr=handle, creationflags=flags, env=env)
    for _ in range(80):
        if process.poll() is not None:
            return process, False
        if healthy(port):
            return process, True
        time.sleep(0.25)
    return process, False


def stop_api(process):
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
    log('API 已停止')


def main():
    log('启动 CellFlow；python=' + sys.executable)
    instance = DesktopInstance(LOGS)
    if not instance.acquire():
        for _ in range(40):
            if focus_owner(instance.owner().get('pid')):
                break
            time.sleep(0.1)
        log('已有本项目实例；未创建第二个窗口或 API')
        return 0
    process = None
    try:
        instance.publish(title=TITLE, state='starting')
        (LOGS / 'desktop-ready.json').unlink(missing_ok=True)
        if not (DIST / 'index.html').is_file():
            raise RuntimeError('缺少 web/dist/index.html，请先构建前端。')
        with socket.socket() as check:
            if check.connect_ex(('127.0.0.1', PORT)) == 0:
                raise RuntimeError(f'{PORT} 端口已被独立服务占用；为避免连接旧界面，本窗口不会复用未知服务。')
        process, ok = start_api(PORT)
        if not ok:
            raise RuntimeError('本机 API 启动失败，详情见 logs/api.log。')
        from cellflow.desktop_qt import run_window
        return run_window(ROOT, LOGS, f'http://127.0.0.1:{PORT}/', instance, log)
    except Exception:
        fail(traceback.format_exc())
        return 1
    finally:
        try:
            stop_api(process)
        finally:
            instance.close()
            (LOGS / 'app.port').unlink(missing_ok=True)


if __name__ == '__main__':
    raise SystemExit(main())
