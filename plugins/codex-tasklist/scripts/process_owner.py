import os
from pathlib import Path
import subprocess
import sys
import time


def process(pid):
    if pid <= 0:
        return None
    try:
        if sys.platform.startswith('linux'):
            root = Path('/proc') / str(pid)
            fields = (root / 'stat').read_text().rsplit(')', 1)[1].split()
            if fields[0] == 'Z':
                return None
            return int(fields[1]), fields[19], (root / 'exe').resolve(strict=True).name
        if sys.platform == 'darwin':
            output = subprocess.run(['ps', '-p', str(pid), '-o', 'ppid=', '-o', 'lstart=', '-o', 'comm='],
                                    check=True, capture_output=True, text=True, timeout=0.25,
                                    env={**os.environ, 'LC_ALL': 'C'}).stdout.split(None, 6)
            if len(output) != 7:
                return None
            return int(output[0]), ' '.join(output[1:6]), Path(output[6].strip()).name
        if os.name == 'nt':
            return windows_process(pid)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None
    return None


def windows_process(pid):
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD), ('pid', wintypes.DWORD),
                    ('heap', ctypes.c_size_t), ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                    ('parent', wintypes.DWORD), ('priority', wintypes.LONG), ('flags', wintypes.DWORD),
                    ('exe', wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return None
    parent = None
    try:
        entry = Entry()
        entry.size = ctypes.sizeof(entry)
        found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            if entry.pid == pid:
                parent = entry.parent
                break
            found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    if parent is None:
        return None
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        name = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(name))
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return None
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            return None
        if not kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(length)):
            return None
        started = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return parent, str(started), Path(name.value).name
    finally:
        kernel.CloseHandle(handle)


def discover():
    pid = os.getppid()
    visited = set()
    deadline = time.monotonic() + 2
    for _ in range(32):
        if pid in visited or time.monotonic() >= deadline:
            return None
        visited.add(pid)
        details = process(pid)
        if details is None:
            return None
        parent, started, name = details
        if name.lower() in ('codex', 'codex.exe'):
            return pid, started
        pid = parent
    return None


def alive(owner):
    details = process(owner[0])
    return details is not None and details[1] == owner[1]
