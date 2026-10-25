"""Desktop lifecycle helpers. No scientific calculation or API behavior."""
from pathlib import Path
import json
import os
import shutil
import struct


def pe_subsystem(path):
    """Do not trust the filename: a renamed console shim is not pythonw."""
    with Path(path).open('rb') as f:
        if f.read(2) != b'MZ':
            raise ValueError('Not a Windows PE executable')
        f.seek(60)
        offset = struct.unpack('<I', f.read(4))[0]
        f.seek(offset)
        if f.read(4) != b'PE\0\0':
            raise ValueError('Invalid PE header')
        f.seek(offset + 92)
        return struct.unpack('<H', f.read(2))[0]




def _replace_with_retry(source, target, attempts=10, delay=0.2):
    """Windows 上刚释放/杀毒扫描可能短暂占用目标；重试替换而不是直接失败。"""
    import time as _time
    last = None
    for _ in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError as exc:
            last = exc
            _time.sleep(delay)
    raise last


def repair_gui_python(env_dir):
    """Restore this environment's own official CPython GUI redirector."""
    env_dir = Path(env_dir)
    target = env_dir / 'Scripts/pythonw.exe'
    if target.exists() and pe_subsystem(target) == 2:
        return False
    cfg = {}
    for line in (env_dir / 'pyvenv.cfg').read_text(encoding='utf-8').splitlines():
        key, sep, value = line.partition('=')
        if sep:
            cfg[key.strip()] = value.strip()
    source = Path(cfg['home']) / 'Lib/venv/scripts/nt/pythonw.exe'
    if not source.is_file() or pe_subsystem(source) != 2:
        raise RuntimeError('Python 发行版缺少正规 GUI 重定向器：' + str(source))
    temporary = target.with_suffix('.exe.repair')
    try:
        shutil.copyfile(source, temporary)
        _replace_with_retry(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def repair_console_python(env_dir):
    """Use CPython's matching redirector, which preserves hidden child startup."""
    env_dir = Path(env_dir)
    cfg = {}
    for line in (env_dir / 'pyvenv.cfg').read_text(encoding='utf-8').splitlines():
        key, sep, value = line.partition('=')
        if sep:
            cfg[key.strip()] = value.strip()
    source = Path(cfg['home']) / 'Lib/venv/scripts/nt/python.exe'
    if not source.is_file() or pe_subsystem(source) != 3:
        raise RuntimeError('Python 发行版缺少正规控制台重定向器：' + str(source))
    target = env_dir / 'Scripts/python.exe'
    if target.exists() and target.read_bytes() == source.read_bytes():
        return False
    temporary = target.with_suffix('.exe.repair')
    try:
        shutil.copyfile(source, temporary)
        _replace_with_retry(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return True


class DesktopInstance:
    """OS-held lock: stale metadata cannot suppress a later launch."""
    def __init__(self, folder):
        self.folder = Path(folder)
        self.path = self.folder / 'desktop.json'
        self.handle = None

    def acquire(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        handle = (self.folder / 'desktop.lock').open('a+b')
        if handle.seek(0, 2) == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def publish(self, **fields):
        if self.handle is None:
            raise RuntimeError('Only the instance owner may publish state')
        temporary = self.path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps({'pid': os.getpid(), **fields}, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, self.path)

    def owner(self):
        try:
            return json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def close(self):
        if self.handle is None:
            return
        if self.owner().get('pid') == os.getpid():
            self.path.unlink(missing_ok=True)
        self.handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
        self.handle = None


def focus_owner(pid):
    """Focus only windows owned by the PID holding this project's lock."""
    if os.name != 'nt' or not isinstance(pid, int) or pid <= 0:
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    if not found:
        return False
    user32.ShowWindow(found[0], 9)
    user32.SetForegroundWindow(found[0])
    return True
