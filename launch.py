"""启动本地界面：已经有服务就直接开浏览器，没有就先起服务、等它就绪再开。

为什么这么写：
- 双击 .cmd 时，失败原因不能“一闪就没”。所有输出同时写进 logs/launcher.log；
- 浏览器打开用 Windows 的 `os.startfile`（比 webbrowser 更可靠），失败时打印地址让手动粘贴；
- 8501 被别的程序占用时自动换 8502/8503，不会傻等；
- 服务起不来时保持窗口并打印诊断信息，方便把日志发给维护者。
"""
from pathlib import Path
import datetime as dt
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / 'logs'
LOGS.mkdir(exist_ok=True)
LOG = LOGS / 'launcher.log'


def log(message):
    stamp = dt.datetime.now().strftime('%H:%M:%S')
    line = f'[{stamp}] {message}'
    print(line, flush=True)
    try:
        with LOG.open('a', encoding='utf-8') as handle:
            handle.write(line + '\n')
    except Exception:
        pass


def healthy(url, timeout=1.5):
    try:
        with urllib.request.urlopen(url + '/_stcore/health', timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def port_busy(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.6)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def open_browser(url):
    """尽量打开默认浏览器；失败也不影响服务本身。"""
    try:
        os.startfile(url)  # Windows: 交给默认程序，最可靠
        log('已在浏览器打开：' + url)
        return True
    except Exception as exc:
        log('os.startfile 打开浏览器失败：' + repr(exc))
    try:
        if webbrowser.open(url, new=2):
            log('已用 webbrowser 打开：' + url)
            return True
    except Exception as exc:
        log('webbrowser 打开失败：' + repr(exc))
    log('浏览器没能自动打开，请手动把下面这个地址粘贴到浏览器：' + url)
    return False


def pick_port():
    """优先 8501；被别的程序占着就往后找，避免“启动失败但不知道原因”。"""
    for port in (8501, 8502, 8503, 8504):
        if not port_busy(port):
            return port
        url = f'http://127.0.0.1:{port}'
        if healthy(url):
            log(f'发现这台电脑上已经有 CellFlow 在运行（{url}），直接打开它。')
            open_browser(url)
            return None
        log(f'端口 {port} 被其他程序占用，换下一个。')
    raise SystemExit('8501–8504 都被占用，无法启动。请先关掉占用的程序后重试。')


def main():
    log('=' * 46)
    log('CellFlow 启动器')
    port = pick_port()
    if port is None:
        return 0
    url = f'http://127.0.0.1:{port}'
    if healthy(url):
        log('服务已经在运行：' + url)
        open_browser(url)
        return 0

    log(f'正在启动服务（端口 {port}）…')
    command = [sys.executable, '-m', 'streamlit', 'run', str(ROOT / 'app.py'),
               '--server.address', '127.0.0.1', '--server.port', str(port),
               '--server.headless', 'true', '--browser.gatherUsageStats', 'false']
    try:
        process = subprocess.Popen(command, cwd=ROOT,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        log('启动子进程失败：' + repr(exc))
        return 1

    ready = False
    for _ in range(90):
        time.sleep(1)
        if process.poll() is not None:
            log(f'服务进程提前退出（返回码 {process.returncode}）。')
            break
        if healthy(url):
            ready = True
            break

    if not ready:
        log('服务没有在 90 秒内就绪。')
        log('排查建议：① 看 logs/launcher.log；'
            '② 手工运行 .venv\\Scripts\\python.exe -m streamlit run app.py 看报错。')
        return 1

    log('服务已就绪：' + url)
    open_browser(url)
    log('关闭这个窗口，或按 Ctrl+C，即可停止服务。')
    try:
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        log('已停止服务。')
        return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # 兜底：不要把错误吞掉
        log('启动器出现未预期错误：' + repr(exc))
        raise SystemExit(1)
