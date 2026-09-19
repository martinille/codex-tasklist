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


def arguments(pid):
    try:
        if sys.platform.startswith('linux'):
            return (Path('/proc') / str(pid) / 'cmdline').read_bytes().decode(errors='replace').split('\0')[:-1]
        if sys.platform == 'darwin':
            return subprocess.run(['ps', '-p', str(pid), '-o', 'args='], check=True, capture_output=True,
                                  text=True, timeout=0.25).stdout.split()
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return []


def daemon(pid):
    return 'app-server' in arguments(pid)[1:]


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


def clients():
    found = []
    if sys.platform.startswith('linux'):
        for entry in Path('/proc').iterdir():
            try:
                if not entry.name.isdigit() or (entry / 'comm').read_text().strip() != 'codex':
                    continue
                pid = int(entry.name)
                details = process(pid)
                if details is None or details[2] != 'codex':
                    continue
                found.append({'identity': (pid, details[1]), 'order': int(details[1]), 'tty': terminal_tty(pid),
                              'arguments': arguments(pid), 'cwd': os.readlink(entry / 'cwd')})
            except (OSError, ValueError):
                continue
    elif sys.platform == 'darwin':
        try:
            output = subprocess.run(['ps', '-axo', 'pid=,tty=,lstart=,args='], check=True, capture_output=True,
                                    text=True, timeout=1, env={**os.environ, 'LC_ALL': 'C'}).stdout
        except (OSError, subprocess.SubprocessError):
            return found
        for line in output.splitlines():
            parts = line.split(None, 7)
            if len(parts) < 8 or Path(parts[7].split()[0]).name != 'codex':
                continue
            started = ' '.join(parts[2:7])
            try:
                order = time.mktime(time.strptime(started, '%a %b %d %H:%M:%S %Y'))
            except ValueError:
                order = 0
            found.append({'identity': (int(parts[0]), started), 'order': order,
                          'tty': '/dev/' + parts[1] if parts[1].startswith('ttys') else '',
                          'arguments': parts[7].split(), 'cwd': None})
    return found


def client(session, cwd, exclude=()):
    found = [item for item in clients() if item['tty'] and item['identity'] not in exclude and
             item['arguments'][1:2] not in (['exec'], ['e'], ['review'], ['agents'], ['app-server'])]
    exact = [item for item in found if session in item['arguments']]
    same = [item for item in found if cwd and item['cwd'] == cwd]
    for group in (exact, same, found):
        if group:
            return max(group, key=lambda item: item['order'])['identity']
    return None


def environment(pid):
    try:
        if sys.platform.startswith('linux'):
            pairs = (Path('/proc') / str(pid) / 'environ').read_bytes().decode(errors='replace').split('\0')
            return dict(pair.split('=', 1) for pair in pairs if '=' in pair)
    except (OSError, ValueError):
        pass
    return None


def alive(owner):
    details = process(owner[0])
    return details is not None and details[1] == owner[1]


def terminal_tty(pid):
    """Controlling terminal, even when the hook's stdin/stdout are pipes."""
    try:
        if sys.platform.startswith('linux'):
            value = int((Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[4])
            return str(value & 0xffffffff) if value else ''
        if sys.platform == 'darwin':
            value = subprocess.run(['ps', '-p', str(pid), '-o', 'tty='], check=True,
                                   capture_output=True, text=True, timeout=0.25).stdout.strip()
            return '/dev/' + value if value.startswith('ttys') else ''
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        pass
    return ''


def device(path):
    try:
        return str(os.stat(path).st_rdev) if sys.platform.startswith('linux') else path
    except OSError:
        return ''


def in_windows_wezterm():
    # Only traverse known CLI wrappers; another terminal/app must not inherit ownership.
    wrappers = {'python.exe', 'python3.exe', 'py.exe', 'powershell.exe', 'pwsh.exe',
                'cmd.exe', 'node.exe', 'codex.exe', 'codex-command-runner.exe',
                'codex-code-mode-host.exe', 'conhost.exe'}
    pid, seen = os.getpid(), set()
    for _ in range(32):
        if pid in seen:
            break
        seen.add(pid)
        details = process(pid)
        if details is None:
            break
        pid, _, name = details
        name = name.lower()
        if name in ('wezterm-gui.exe', 'wezterm-mux-server.exe'):
            return True
        if name not in wrappers:
            break
    return False
