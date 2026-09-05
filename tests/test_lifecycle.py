from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import select
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from test_tasklist import SCRIPT, tasklist

if os.name != 'nt':
    import fcntl
    import pty
    import struct
    import termios


class LifecycleTest(unittest.TestCase):
    def test_concurrent_opens_create_one_panel(self):
        parent = {'pane_id': 1, 'tab_id': 4, 'size': {'rows': 40}}
        panes = [parent]
        first_split = threading.Event()
        second_split = threading.Event()
        lock = threading.Lock()
        splits = []

        def wezterm(command, *args):
            if command == 'list':
                with lock:
                    return json.dumps(panes)
            if command == 'split-pane':
                with lock:
                    number = len(splits) + 2
                    splits.append(number)
                    panes.append({'pane_id': number, 'tab_id': 4})
                if number == 2:
                    first_split.set()
                    second_split.wait(1)
                else:
                    second_split.set()
                return str(number)
            if command == 'activate-pane':
                return ''
            raise AssertionError(command)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tasklist.connect(root).close()

            def open_panel(wait):
                db = tasklist.connect(root)
                try:
                    if wait and not first_split.wait(3):
                        raise AssertionError('First opener never reached split-pane')
                    tasklist.open_panel(db, root, 'parallel')
                finally:
                    db.close()

            with patch.dict(os.environ, {'WEZTERM_PANE': '1', 'WEZTERM_UNIX_SOCKET': 'test'}), \
                    patch.object(tasklist.shutil, 'which', return_value='/usr/bin/wezterm'), \
                    patch.object(tasklist.process_owner, 'discover', return_value=(
                        os.getpid(), tasklist.process_owner.process(os.getpid())[1])), \
                    patch.object(tasklist, 'wezterm', side_effect=wezterm), \
                    ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(open_panel, False)
                second = executor.submit(open_panel, True)
                first.result(timeout=15)
                second.result(timeout=15)
        self.assertEqual(len(splits), 1)

    @unittest.skipIf(os.name == 'nt', 'POSIX PTY integration; Windows needs a real console')
    def test_session_end_exits_view_and_preserves_tasks(self):
        for reason in ('session_end', 'owner_death', 'resume', 'moved_parent', 'pid_reuse',
                       'unregistered_creator_death'):
            with self.subTest(reason=reason):
                self.check_view_exit(reason)

    def check_view_exit(self, reason):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = tasklist.connect(root)
            master, slave = pty.openpty()
            process = None
            creator = None
            owner = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
            try:
                identity = (owner.pid, tasklist.process_owner.process(owner.pid)[1])
                tasklist.add(db, 'lifecycle', 'Keep after session ends', 'pending')
                with db:
                    db.execute('INSERT INTO sessions(session,parent,owner_pid,owner_start) VALUES (?, ?, ?, ?)',
                               ('lifecycle', 'parent-shell', *identity))
                environment = {key: value for key, value in os.environ.items() if key != 'WEZTERM_PANE'}
                extra = []
                if reason == 'unregistered_creator_death':
                    creator = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
                    with db:
                        db.execute('INSERT INTO panel_openers VALUES (?,?,?,?)',
                                   ('lifecycle', creator.pid, tasklist.process_owner.process(creator.pid)[1], 'creation'))
                    environment['WEZTERM_PANE'] = '2'
                    extra = ['--opening-token', 'creation']
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 5, 80, 0, 0))
                process = subprocess.Popen([sys.executable, str(SCRIPT), '--data-dir', directory,
                                            '--session', 'lifecycle', 'view',
                                            '--owner-pid', str(identity[0]), '--owner-start', identity[1], *extra],
                                           stdin=slave, stdout=slave, stderr=slave, env=environment)
                output = b''
                deadline = time.monotonic() + 3
                while b'Keep after session ends' not in output and time.monotonic() < deadline:
                    if select.select([master], [], [], 0.1)[0]:
                        output += os.read(master, 65536)
                self.assertIn(b'Keep after session ends', output)
                if reason == 'session_end':
                    with patch.object(tasklist.process_owner, 'discover', return_value=identity):
                        tasklist.hook(db, root, {'session_id': 'lifecycle', 'hook_event_name': 'SessionEnd'})
                elif reason == 'owner_death':
                    owner.terminate()
                    owner.wait(timeout=3)
                    self.assertIsNotNone(tasklist.process_owner.process(os.getpid()))
                elif reason == 'unregistered_creator_death':
                    creator.kill()
                    creator.wait(timeout=3)
                    self.assertIsNone(owner.poll())
                else:
                    with db:
                        column, value = {'resume': ('owner_pid', os.getpid()),
                                         'moved_parent': ('parent', 'different-parent'),
                                         'pid_reuse': ('owner_start', 'different-start')}[reason]
                        db.execute(f'UPDATE sessions SET {column}=? WHERE session=?', (value, 'lifecycle'))
                self.assertEqual(process.wait(timeout=3), 0)
                db.close()
                db = tasklist.connect(root)
                self.assertEqual(tasklist.tasks(db, 'lifecycle'), [
                    {'id': 1, 'title': 'Keep after session ends', 'status': 'pending'}])
            finally:
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                os.close(master)
                os.close(slave)
                db.close()
                if owner.poll() is None:
                    owner.terminate()
                    owner.wait(timeout=3)
                if creator is not None and creator.poll() is None:
                    creator.kill()
                    creator.wait(timeout=3)

    def test_old_database_migration_and_stale_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = sqlite3.connect(root / 'tasks.sqlite3')
            old.execute('CREATE TABLE sessions(session TEXT PRIMARY KEY,pane TEXT,parent TEXT,socket TEXT,closed INTEGER DEFAULT 0)')
            old.execute("INSERT INTO sessions(session) VALUES ('resumed')")
            old.execute('CREATE TABLE tasks(session TEXT,id INTEGER,title TEXT,status TEXT,PRIMARY KEY(session,id))')
            old.execute("INSERT INTO tasks VALUES ('resumed',1,'Retained','pending')")
            old.commit()
            old.close()
            db = tasklist.connect(root)
            try:
                self.assertEqual(tasklist.tasks(db, 'resumed')[0]['title'], 'Retained')
                self.assertIsNone(db.execute('SELECT owner_pid FROM sessions').fetchone()[0])
                with db:
                    db.execute('UPDATE sessions SET owner_pid=20,owner_start=?', ('new',))
                with patch.object(tasklist.process_owner, 'discover', return_value=(10, 'old')):
                    tasklist.hook(db, root, {'session_id': 'resumed', 'hook_event_name': 'SessionEnd'})
                self.assertEqual(db.execute('SELECT closed FROM sessions').fetchone()[0], 0)
                with patch.object(tasklist.process_owner, 'discover', return_value=(20, 'new')):
                    tasklist.hook(db, root, {'session_id': 'resumed', 'hook_event_name': 'SessionEnd'})
                self.assertEqual(db.execute('SELECT closed FROM sessions').fetchone()[0], 1)
            finally:
                db.close()
            reopened = tasklist.connect(root)
            try:
                self.assertEqual(tasklist.tasks(reopened, 'resumed')[0]['title'], 'Retained')
            finally:
                reopened.close()

    def test_ownerless_panel_is_refused_but_queue_works(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = tasklist.connect(root)
            try:
                with patch.dict(os.environ, {'WEZTERM_PANE': '1'}), \
                        patch.object(tasklist.shutil, 'which', return_value='wezterm'), \
                        patch.object(tasklist.process_owner, 'discover', return_value=None), \
                        patch.object(tasklist, 'wezterm') as terminal:
                    with self.assertRaisesRegex(ValueError, 'no live Codex owner'):
                        tasklist.open_panel(db, root, 'ownerless')
                    result = tasklist.hook(db, root, {'session_id': 'ownerless', 'hook_event_name': 'SessionStart'})
                    self.assertIn('Start a fresh Codex CLI', result['systemMessage'])
                    terminal.assert_not_called()
                    with self.assertRaisesRegex(ValueError, 'Start Codex CLI'):
                        tasklist.view(db, 'ownerless', None)
                tasklist.add(db, 'ownerless', 'Queue without panel', 'pending')
                tasklist.update(db, 'ownerless', 1, status='done')
                self.assertEqual(tasklist.tasks(db, 'ownerless')[0]['status'], 'done')
            finally:
                db.close()

    def test_killed_creator_releases_session_without_blocking_queue_or_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = tasklist.connect(root)
            identity = (os.getpid(), tasklist.process_owner.process(os.getpid())[1])
            code = '''
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
import tasklist
from pathlib import Path
tasklist.process_owner.discover = lambda: (int(sys.argv[3]), sys.argv[4])
tasklist.shutil.which = lambda name: 'wezterm'
os.environ['WEZTERM_PANE'] = '1'
def wezterm(command, *args):
    if command == 'list':
        return json.dumps([{'pane_id': 1, 'tab_id': 4, 'size': {'rows': 40}}])
    if command == 'split-pane':
        time.sleep(30)
        return '2'
    return ''
tasklist.wezterm = wezterm
db = tasklist.connect(Path(sys.argv[2]))
tasklist.open_panel(db, Path(sys.argv[2]), 'crash')
'''
            creator = subprocess.Popen([sys.executable, '-c', code, str(SCRIPT.parent), directory,
                                        str(identity[0]), identity[1]], stdout=subprocess.DEVNULL,
                                       stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 3
                while not db.execute("SELECT 1 FROM sessions WHERE session='crash'").fetchone():
                    if time.monotonic() >= deadline or creator.poll() is not None:
                        self.fail('Creator did not reach its external split call')
                    time.sleep(0.01)
                started = time.monotonic()
                tasklist.add(db, 'crash', 'Keep after recovery', 'pending')
                with patch.object(tasklist.process_owner, 'discover', return_value=identity):
                    tasklist.hook(db, root, {'session_id': 'crash', 'hook_event_name': 'SessionEnd'})
                self.assertLess(time.monotonic() - started, 2)
                self.assertEqual(db.execute('SELECT closed FROM sessions').fetchone()[0], 1)
                creator.kill()
                creator.wait(timeout=3)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM panel_openers').fetchone()[0], 1)
                with patch.dict(os.environ, {'WEZTERM_PANE': '1'}), \
                        patch.object(tasklist.process_owner, 'discover', return_value=identity), \
                        patch.object(tasklist.shutil, 'which', return_value='wezterm'), \
                        patch.object(tasklist, 'wezterm', side_effect=[json.dumps([
                            {'pane_id': 1, 'tab_id': 4, 'size': {'rows': 40}}]), '2', '']):
                    tasklist.open_panel(db, root, 'crash')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM panel_openers').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT pane FROM sessions').fetchone()[0], '2')
                self.assertEqual(tasklist.tasks(db, 'crash')[0]['title'], 'Keep after recovery')
            finally:
                if creator.poll() is None:
                    creator.kill()
                    creator.wait(timeout=3)
                creator.stderr.close()
                db.close()


if __name__ == '__main__':
    unittest.main()
