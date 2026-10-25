"""Windows Job Object boundary established before any computation can spawn."""
import ctypes
from ctypes import wintypes
import hashlib
from pathlib import Path
import subprocess
import sys


def job_name(store, job_id):
    digest = hashlib.sha256(str(Path(store).resolve()).casefold().encode()).hexdigest()[:20]
    return 'Local\\CellFlow-' + digest + '-' + job_id


def _api():
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    for name, args, result in [
        ('CreateJobObjectW', [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        ('OpenJobObjectW', [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
        ('SetInformationJobObject', [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
        ('AssignProcessToJobObject', [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        ('TerminateJobObject', [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        ('GetCurrentProcess', [], wintypes.HANDLE),
        ('CloseHandle', [wintypes.HANDLE], wintypes.BOOL),
    ]:
        fn = getattr(api, name)
        fn.argtypes, fn.restype = args, result
    return api


def close(handle):
    _api().CloseHandle(handle)


def create(name):
    class Basic(ctypes.Structure):
        _fields_ = [('times', ctypes.c_longlong * 2), ('flags', wintypes.DWORD),
                    ('min_working', ctypes.c_size_t), ('max_working', ctypes.c_size_t),
                    ('active', wintypes.DWORD), ('affinity', ctypes.c_size_t),
                    ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [('basic', Basic), ('io', ctypes.c_ulonglong * 6),
                    ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                    ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]

    api = _api()
    handle = api.CreateJobObjectW(None, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    info = Extended()
    info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway.
    if not api.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
        error = ctypes.get_last_error()
        close(handle)
        raise ctypes.WinError(error)
    return handle


def terminate(name):
    api = _api()
    handle = api.OpenJobObjectW(0x0008, False, name)  # JOB_OBJECT_TERMINATE
    if not handle:
        return False
    try:
        return bool(api.TerminateJobObject(handle, 1))
    finally:
        close(handle)


def main():
    api = _api()
    handle = api.OpenJobObjectW(0x0001, False, sys.argv[1])  # ASSIGN_PROCESS
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if not api.AssignProcessToJobObject(handle, api.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())
    # Parent owns lifetime. Drop our handle before spawning; parent death must kill us.
    close(handle)
    Path(sys.argv[2]).write_text('assigned', encoding='ascii')
    return subprocess.call(sys.argv[3:], creationflags=subprocess.CREATE_NO_WINDOW)


if __name__ == '__main__':
    sys.exit(main())
