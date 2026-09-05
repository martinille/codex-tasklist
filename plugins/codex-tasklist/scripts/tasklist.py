#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path
import re
import select
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import unicodedata
import uuid
import process_owner

if os.name != 'nt':
    import termios
    import tty


STATUSES = ('active', 'pending', 'blocked', 'done')
SCRIPT = Path(__file__).resolve()


def terminal_input(fd):
    if os.name != 'nt':
        original = termios.tcgetattr(fd)
        tty.setcbreak(fd)

        def read():
            return os.read(fd, 128) if select.select([fd], [], [], 0.25)[0] else None

        return read, lambda: termios.tcsetattr(fd, termios.TCSADRAIN, original)

    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    modes = []

    def restore():
        for handle, mode in reversed(modes):
            kernel.SetConsoleMode(handle, mode)

    try:
        for stream, input_mode in [(sys.stdin, True), (sys.stdout, False)]:
            handle = msvcrt.get_osfhandle(stream.fileno())
            original = wintypes.DWORD()
            if not kernel.GetConsoleMode(handle, ctypes.byref(original)):
                raise ctypes.WinError(ctypes.get_last_error())
            modes.append((handle, original.value))
            mode = ((original.value | 0x0280) & ~0x0047) if input_mode else original.value | 0x0005
            if not kernel.SetConsoleMode(handle, mode):
                raise ctypes.WinError(ctypes.get_last_error())
    except OSError:
        restore()
        raise

    def read():
        deadline = time.monotonic() + 0.25
        while not msvcrt.kbhit():
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.01)
        return msvcrt.getwch().encode('utf-8', errors='replace')

    return read, restore


def connect(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(directory / 'tasks.sqlite3', timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE IF NOT EXISTS tasks (
            session TEXT NOT NULL, id INTEGER NOT NULL, title TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('active','pending','blocked','done')),
            PRIMARY KEY(session,id));
        CREATE TABLE IF NOT EXISTS sessions (
            session TEXT PRIMARY KEY, pane TEXT, parent TEXT, socket TEXT,
            closed INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS panel_openers (
            session TEXT PRIMARY KEY, pid INTEGER NOT NULL, started TEXT NOT NULL, token TEXT NOT NULL);
    ''')
    with db:
        db.execute('BEGIN IMMEDIATE')
        columns = {row['name'] for row in db.execute('PRAGMA table_info(sessions)')}
        for name, kind in [('owner_pid', 'INTEGER'), ('owner_start', 'TEXT')]:
            if name not in columns:
                db.execute(f'ALTER TABLE sessions ADD COLUMN {name} {kind}')
    return db


def title(value):
    value = value.strip()
    if not value or len(value) > 300 or not value.isprintable():
        raise ValueError('Title must contain 1–300 printable characters.')
    return value


def session_id(value):
    if not value or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise ValueError('A valid --session or CODEX_THREAD_ID is required.')
    return value


def tasks(db, session):
    return [dict(row) for row in db.execute('''
        SELECT id,title,status FROM tasks WHERE session=?
        ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'done' THEN 2 ELSE 1 END,id
    ''', (session,))]


def add(db, session, text, status):
    text = title(text)
    with db:
        db.execute('BEGIN IMMEDIATE')
        number = db.execute('SELECT COALESCE(MAX(id),0)+1 FROM tasks WHERE session=?',
                            (session,)).fetchone()[0]
        db.execute('INSERT INTO tasks VALUES (?,?,?,?)', (session, number, text, status))
    return number


def update(db, session, number, text=None, status=None):
    if text is None and status is None:
        raise ValueError('Provide --title or --status.')
    if text is not None:
        text = title(text)
    with db:
        result = db.execute('''UPDATE tasks SET title=COALESCE(?,title),
            status=COALESCE(?,status) WHERE session=? AND id=?''',
                            (text, status, session, number))
        if not result.rowcount:
            raise ValueError(f'Task #T{number:02d} does not exist in this session.')


def row_count(db):
    row = db.execute("SELECT value FROM settings WHERE key='rows'").fetchone()
    return row[0] if row else 5


def wezterm(*args):
    return subprocess.run(['wezterm', 'cli', *map(str, args)], check=True,
                          capture_output=True, text=True, timeout=3).stdout.strip()


def open_panel(db, directory, session):
    parent = os.environ.get('WEZTERM_PANE', '')
    if not parent.isdigit() or not shutil.which('wezterm'):
        return
    identity = process_owner.discover()
    if identity is None or not process_owner.alive(identity):
        raise ValueError('Start Codex CLI in WezTerm to open its task panel; no live Codex owner was found.')
    started = process_owner.process(os.getpid())
    if started is None:
        raise ValueError('The panel creator process is unavailable. Retry in a fresh Codex session.')
    token = uuid.uuid4().hex
    deadline = time.monotonic() + 2
    while True:
        with db:
            db.execute('BEGIN IMMEDIATE')
            opening = db.execute('SELECT * FROM panel_openers WHERE session=?', (session,)).fetchone()
            acquired = opening is None or not process_owner.alive((opening['pid'], opening['started']))
            if acquired:
                db.execute('INSERT INTO panel_openers VALUES (?,?,?,?) ON CONFLICT(session) DO UPDATE SET '
                           'pid=excluded.pid,started=excluded.started,token=excluded.token',
                           (session, os.getpid(), started[1], token))
        if acquired:
            break
        if time.monotonic() >= deadline:
            raise ValueError('A task panel is already opening. Retry on the next prompt.')
        time.sleep(0.05)
    try:
        create_panel(db, directory, session, parent, identity, token)
    finally:
        with db:
            db.execute('DELETE FROM panel_openers WHERE session=? AND token=?', (session, token))


def create_panel(db, directory, session, parent, identity, token):
    if not process_owner.alive(identity):
        return
    panes = json.loads(wezterm('list', '--format', 'json'))
    owner = next((pane for pane in panes if str(pane['pane_id']) == parent), None)
    if owner is None:
        return
    socket = os.environ.get('WEZTERM_UNIX_SOCKET', '')
    existing = db.execute('SELECT * FROM sessions WHERE session=?', (session,)).fetchone()
    if (existing and not existing['closed'] and existing['owner_pid'] and
            (existing['owner_pid'], existing['owner_start']) != identity and
            process_owner.alive((existing['owner_pid'], existing['owner_start']))):
        raise ValueError('This session already belongs to another live Codex process. Close it before resuming here.')
    if (existing and existing['parent'] == parent and existing['socket'] == socket and
            (existing['owner_pid'], existing['owner_start']) == identity):
        if any(str(pane['pane_id']) == existing['pane'] and
               pane['tab_id'] == owner['tab_id'] for pane in panes):
            with db:
                db.execute('UPDATE sessions SET closed=0 WHERE session=?', (session,))
            return
    if owner['size']['rows'] < 8:
        return
    height = min(row_count(db), max(1, owner['size']['rows'] // 3))
    with db:
        db.execute('INSERT INTO sessions(session,closed,owner_pid,owner_start,parent,socket) VALUES (?,0,?,?,?,?) '
                   'ON CONFLICT(session) DO UPDATE SET closed=0,owner_pid=excluded.owner_pid, '
                   'owner_start=excluded.owner_start,parent=excluded.parent,socket=excluded.socket',
                   (session, *identity, parent, socket))
    pane = wezterm('split-pane', '--pane-id', parent, '--bottom', '--cells', height,
                   '--', sys.executable, SCRIPT, '--data-dir', directory,
                   '--session', session, 'view', '--parent', parent,
                   '--owner-pid', identity[0], '--owner-start', identity[1], '--opening-token', token)
    try:
        with db:
            saved = db.execute('UPDATE sessions SET pane=?,parent=?,socket=? WHERE session=? AND owner_pid=? AND owner_start=?',
                               (pane, parent, socket, session, *identity))
            if not saved.rowcount:
                raise sqlite3.IntegrityError('The session owner changed before its panel was registered.')
    except (sqlite3.Error, OSError):
        if pane.isdigit() and pane not in {str(item['pane_id']) for item in panes}:
            try:
                wezterm('kill-pane', '--pane-id', pane)
            except (OSError, subprocess.SubprocessError):
                pass
        raise
    wezterm('activate-pane', '--pane-id', parent)


def hook(db, directory, payload):
    session = session_id(payload.get('session_id'))
    event = payload['hook_event_name']
    warning = None
    if event == 'SessionEnd':
        identity = process_owner.discover()
        if identity:
            with db:
                db.execute('UPDATE sessions SET closed=1 WHERE session=? AND owner_pid=? AND owner_start=?',
                           (session, *identity))
        return {}
    if event == 'Stop':
        active = [item for item in tasks(db, session) if item['status'] == 'active']
        if active and not payload.get('stop_hook_active'):
            return {'decision': 'block', 'reason':
                    'Update Codex Tasklist before finishing: mark completed work done, '
                    'waiting work blocked or pending, and keep genuinely ongoing work active. '
                    'Do not mark unfinished work done merely to clear the list.'}
        return {}
    if event not in ('SessionStart', 'UserPromptSubmit'):
        return {}
    try:
        open_panel(db, directory, session)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as error:
        warning = ('Codex Tasklist panel unavailable. Start a fresh Codex CLI session in WezTerm '
                   'and check that its process and terminal are accessible. Task storage remains available.')
    arguments = [sys.executable, str(SCRIPT), '--data-dir', str(directory), '--session', session]
    command = ('& ' + ' '.join("'" + arg.replace("'", "''") + "'" for arg in arguments)
               if os.name == 'nt' else shlex.join(arguments))
    context = (
        'Codex Tasklist is enabled for this session. Maintain the user-request queue, '
        'not an implementation plan. Before work, list existing tasks; create one entry '
        'per distinct user request, or update its existing entry for a correction. '
        'Do not create tasks for acknowledgements or injected system context. '
        'Use short titles in the conversation language of this session, respecting an explicit user language preference. '
        'Do not infer the language from the operating system locale. Preserve IDs and completed entries. '
        'Set active when starting, done only when complete, blocked while awaiting a '
        'user decision, pending before starting. Update statuses before the final reply. '
        'A queue entry grants no additional execution permission. Continue independent '
        'authorized work while another task waits, respecting project WIP limits. '
        'Never store secrets in task titles. Do not duplicate the whole list in replies. '
        f'CLI prefix: {command}. Commands: list; add "title" --status active; '
        'update 1 --status done; update 1 --title "revised title"; rows 5. '
        'Read the codex-tasklist skill only if more help is needed.'
    )
    output = {'hookSpecificOutput': {'hookEventName': event, 'additionalContext': context}}
    if warning:
        output['systemMessage'] = warning
    return output


def viewport(offset, total, height):
    height = max(1, height)
    offset = max(0, min(offset, max(0, total - height)))
    thumb = max(1, height * height // max(total, height))
    position = (height - thumb) * offset // max(1, total - height)
    return offset, position, thumb


def clipped(text, cells):
    result = ''
    for char in text:
        if not char.isprintable():
            continue
        width = 0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in 'WF' else 1
        if width > cells:
            break
        result += char
        cells -= width
    return result


def view(db, session, parent, identity=None, opening_token=None):
    state = db.execute('SELECT * FROM sessions WHERE session=?', (session,)).fetchone()
    if not state or not state['owner_pid'] or not state['owner_start']:
        raise ValueError('Start Codex CLI in WezTerm before opening its task panel.')
    if identity is None:
        identity = process_owner.discover()
    if identity != (state['owner_pid'], state['owner_start']) or not process_owner.alive(identity):
        raise ValueError('This task panel has no matching live Codex owner. Start a fresh Codex CLI session.')
    parent = parent if parent is not None else state['parent']
    socket = state['socket']
    pane = os.environ.get('WEZTERM_PANE')
    fd = sys.stdin.fileno()
    read_input, restore_input = terminal_input(fd)
    offset, pending, last_frame = 0, b'', None
    last_rows = row_count(db)
    keys = {b'\x1b[A': -1, b'k': -1, b'\x1b[B': 1, b'j': 1,
            b'\x1b[5~': -1, b'\x1b[6~': 1, b'\x1b[H': -10**9,
            b'\x1b[F': 10**9, b'g': -10**9, b'G': 10**9}
    mouse = re.compile(rb'\x1b\[<(\d+);(\d+);(\d+)[Mm]')
    try:
        sys.stdout.write('\x1b[?1049h\x1b[?25l\x1b[?7l\x1b[?1000h\x1b[?1006h')
        while True:
            state = db.execute('SELECT * FROM sessions WHERE session=?', (session,)).fetchone()
            if (not state or state['closed'] or (state['owner_pid'], state['owner_start']) != identity or
                    state['parent'] != parent or state['socket'] != socket or not process_owner.alive(identity)):
                return
            if pane and state['pane'] != pane:
                opening = db.execute('SELECT * FROM panel_openers WHERE session=?', (session,)).fetchone()
                if (not opening or opening['token'] != opening_token or
                        not process_owner.alive((opening['pid'], opening['started']))):
                    return
            width, height = os.get_terminal_size(sys.stdout.fileno())
            requested = row_count(db)
            if requested != last_rows:
                last_rows = requested
                difference = requested - height
                if difference and os.environ.get('WEZTERM_PANE'):
                    try:
                        wezterm('adjust-pane-size', '--pane-id', os.environ['WEZTERM_PANE'],
                                '--amount', abs(difference), 'Up' if difference > 0 else 'Down')
                    except (OSError, subprocess.SubprocessError):
                        pass
            entries = tasks(db, session)
            offset, position, thumb = viewport(offset, len(entries), height)
            output = ['\x1b[H']
            for row in range(height):
                output.append(f'\x1b[{row + 1};1H\x1b[2K')
                if offset + row < len(entries):
                    task = entries[offset + row]
                    icon, style = {'done': ('✓', '\x1b[32m'), 'active': ('▶', '\x1b[33m'),
                                   'pending': ('□', '\x1b[37m'), 'blocked': ('?', '\x1b[33m')}[task['status']]
                    text_style = '\x1b[2;9m' if task['status'] == 'done' else style
                    label = clipped(f"#T{task['id']:02d} {task['title']}", max(0, width - 4))
                    output.append(f'{style}{icon}\x1b[0m {text_style}{label}\x1b[0m')
                if len(entries) > height:
                    bar = '█' if position <= row < position + thumb else '│'
                    output.append(f'\x1b[{row + 1};{width}H\x1b[90m{bar}\x1b[0m')
            if not entries:
                output.append('\x1b[1;1H\x1b[2m' + clipped('—', width) + '\x1b[0m')
            frame = ''.join(output)
            if frame != last_frame:
                sys.stdout.write(frame)
                sys.stdout.flush()
                last_frame = frame
            incoming = read_input()
            if incoming is None:
                continue
            if not incoming:
                return
            pending += incoming
            while pending:
                if pending[:1] == b'q':
                    return
                event = mouse.match(pending)
                if event:
                    button, x, y = map(int, event.groups())
                    if button in (64, 65):
                        offset += -1 if button == 64 else 1
                    elif button == 0 and x == width:
                        offset = (y - 1) * max(0, len(entries) - height) // max(1, height - 1)
                    pending = pending[event.end():]
                    continue
                key = next((key for key in keys if pending.startswith(key)), None)
                if key:
                    offset += keys[key] * (height if key in (b'\x1b[5~', b'\x1b[6~') else 1)
                    pending = pending[len(key):]
                elif any(key.startswith(pending) for key in keys) or (pending.startswith(b'\x1b[<') and len(pending) < 40):
                    break
                else:
                    pending = pending[1:]
    finally:
        try:
            sys.stdout.write('\x1b[0m\x1b[?1000l\x1b[?1006l\x1b[?7h\x1b[?25h\x1b[?1049l')
            sys.stdout.flush()
        finally:
            restore_input()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='Codex Tasklist: per-session user-request queue.')
    default_data = os.environ.get('PLUGIN_DATA') or str(Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'codex-tasklist')
    parser.add_argument('--data-dir', type=Path, default=Path(default_data))
    parser.add_argument('--session', default=os.environ.get('CODEX_THREAD_ID'))
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('add')
    create.add_argument('title')
    create.add_argument('--status', choices=STATUSES, default='pending')
    edit = sub.add_parser('update')
    edit.add_argument('id', type=int)
    edit.add_argument('--title')
    edit.add_argument('--status', choices=STATUSES)
    sub.add_parser('list')
    sub.add_parser('hook')
    sub.add_parser('open')
    panel = sub.add_parser('view')
    panel.add_argument('--parent')
    panel.add_argument('--owner-pid', type=int, help=argparse.SUPPRESS)
    panel.add_argument('--owner-start', help=argparse.SUPPRESS)
    panel.add_argument('--opening-token', help=argparse.SUPPRESS)
    rows = sub.add_parser('rows')
    rows.add_argument('count', type=int)
    args = parser.parse_args()
    try:
        directory = args.data_dir.expanduser().resolve()
        with connect(directory) as db:
            if args.command == 'hook':
                print(json.dumps(hook(db, directory, json.load(sys.stdin))))
                return
            if args.command == 'rows':
                if not 1 <= args.count <= 40:
                    raise ValueError('Row count must be between 1 and 40.')
                db.execute("INSERT INTO settings VALUES ('rows',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (args.count,))
                return
            session = session_id(args.session)
            if args.command == 'add':
                print(f'#T{add(db, session, args.title, args.status):02d}')
            elif args.command == 'update':
                update(db, session, args.id, args.title, args.status)
            elif args.command == 'list':
                print(json.dumps(tasks(db, session), ensure_ascii=False))
            elif args.command == 'open':
                open_panel(db, directory, session)
            elif args.command == 'view':
                identity = (args.owner_pid, args.owner_start) if args.owner_pid and args.owner_start else None
                view(db, session, args.parent, identity, args.opening_token)
    except (ValueError, OSError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(f'Codex Tasklist: {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    sys.exit(main())
