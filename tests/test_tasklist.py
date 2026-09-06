import importlib.util
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

if os.name != 'nt':
    import pty
    import termios
    import fcntl
    import struct


SCRIPT = Path(__file__).resolve().parents[1] / 'plugins/codex-tasklist/scripts/tasklist.py'
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location('tasklist', SCRIPT)
tasklist = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tasklist)
TERMINAL_ENV = dict.fromkeys(('TMUX', 'STY', 'ZELLIJ', 'SSH_CONNECTION', 'SSH_TTY'), '')
TERMINAL_ENV['TERM_PROGRAM'] = 'WezTerm'


class TasklistTest(unittest.TestCase):
    def setUp(self):
        ownership = patch.object(tasklist.terminals.Terminal, 'owns', return_value=True)
        ownership.start()
        self.addCleanup(ownership.stop)
        environment = patch.dict(os.environ, TERMINAL_ENV)
        environment.start()
        self.addCleanup(environment.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = tasklist.connect(self.root)
        self.owner = (os.getpid(), tasklist.process_owner.process(os.getpid())[1])
        self.discovery = patch.object(tasklist.process_owner, 'discover', return_value=self.owner)
        self.discovery.start()

    def tearDown(self):
        self.discovery.stop()
        self.db.close()
        self.temp.cleanup()

    def test_stable_ids_sorting_and_session_isolation(self):
        for name, status in [('One', 'done'), ('Two', 'pending'), ('Three', 'active'), ('Four', 'blocked')]:
            tasklist.add(self.db, 'first', name, status)
        tasklist.add(self.db, 'second', 'Other session', 'active')
        self.assertEqual([row['id'] for row in tasklist.tasks(self.db, 'first')], [3, 2, 4, 1])
        tasklist.update(self.db, 'first', 1, 'Reopened', 'active')
        self.assertEqual([row['id'] for row in tasklist.tasks(self.db, 'first')], [1, 3, 2, 4])
        self.assertEqual(tasklist.tasks(self.db, 'second')[0]['title'], 'Other session')
        with self.assertRaises(ValueError):
            tasklist.update(self.db, 'first', 99, status='done')

    def test_concurrent_creates_do_not_overwrite(self):
        processes = [subprocess.Popen([sys.executable, str(SCRIPT), '--data-dir', str(self.root),
                                      '--session', 'parallel', 'add', f'Request {number}'],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for number in range(8)]
        for process in processes:
            output, error = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, error)
        self.assertEqual([row['id'] for row in tasklist.tasks(self.db, 'parallel')], list(range(1, 9)))

    def test_trust_boundary_and_unicode(self):
        for value in ['', 'a\ncommand', '\x1b[31mred', 'x' * 301, '\u202ehidden']:
            with self.assertRaises(ValueError):
                tasklist.add(self.db, 'valid', value, 'pending')
        self.assertEqual(tasklist.clipped('Žluť 世界', 7), 'Žluť 世')
        self.assertNotIn('\x1b', tasklist.clipped('bad\x1btitle', 40))
        with self.assertRaises(ValueError):
            tasklist.session_id('../wrong')

    def test_malformed_hook_payload_reports_error_without_traceback(self):
        payloads = [None, [], {'session_id': 'valid'}]
        payloads += [{'session_id': 'valid', 'hook_event_name': event}
                     for event in (None, 1, [], '', ' ')]
        payloads += [{'session_id': session, 'hook_event_name': 'SessionStart'}
                     for session in (1, ['valid'], {'id': 'valid'}, True)]
        for payload in payloads:
            with self.subTest(payload=payload):
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), '--data-dir', str(self.root), 'hook'],
                    input=json.dumps(payload), text=True, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 1)
                self.assertTrue(result.stderr.startswith('Codex Tasklist:'))
                self.assertNotIn('Traceback', result.stderr)

    def test_captured_unicode_list_without_utf8_environment(self):
        tasklist.add(self.db, 'unicode', 'Živý test — 世界', 'done')
        environment = {key: value for key, value in os.environ.items()
                       if key not in ('PYTHONUTF8', 'PYTHONIOENCODING')}
        environment['PYTHONUTF8'] = '0'
        environment['PYTHONIOENCODING'] = 'cp1250'
        result = subprocess.run([sys.executable, str(SCRIPT), '--data-dir', str(self.root),
                                 '--session', 'unicode', 'list'], env=environment,
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.decode('utf-8'))[0]['title'], 'Živý test — 世界')

    def test_open_is_idempotent_and_end_does_not_delete_tasks(self):
        parent = {'pane_id': 1, 'tab_id': 4, 'size': {'rows': 40}}
        responses = [json.dumps([parent]), '2', '', json.dumps([parent, {'pane_id': 2, 'tab_id': 4}])]
        with patch.dict(os.environ, {'WEZTERM_PANE': '1', 'WEZTERM_UNIX_SOCKET': 'test'}), \
                patch.object(tasklist.terminals.shutil, 'which', return_value='/usr/bin/wezterm'), \
                patch.object(tasklist.terminals.Terminal, 'command', side_effect=responses) as call:
            tasklist.open_panel(self.db, self.root, 'session')
            tasklist.open_panel(self.db, self.root, 'session')
            self.assertEqual(sum(args.args[0] == 'split-pane' for args in call.call_args_list), 1)
        tasklist.add(self.db, 'session', 'Keep me', 'active')
        stop = {'session_id': 'session', 'hook_event_name': 'Stop'}
        self.assertEqual(tasklist.hook(self.db, self.root, stop)['decision'], 'block')
        self.assertEqual(tasklist.hook(self.db, self.root, {**stop, 'stop_hook_active': True}), {})
        tasklist.hook(self.db, self.root, {'session_id': 'session', 'hook_event_name': 'SessionEnd'})
        self.assertEqual(self.db.execute('SELECT closed FROM sessions').fetchone()[0], 1)
        self.assertEqual(len(tasklist.tasks(self.db, 'session')), 1)

    def test_split_failure_releases_reservation_and_can_retry(self):
        parent = {'pane_id': 1, 'tab_id': 4, 'size': {'rows': 40}}
        responses = [json.dumps([parent]), subprocess.CalledProcessError(1, 'split-pane'),
                     json.dumps([parent]), '2', '']
        with patch.dict(os.environ, {'WEZTERM_PANE': '1'}), \
                patch.object(tasklist.terminals.shutil, 'which', return_value='wezterm'), \
                patch.object(tasklist.terminals.Terminal, 'command', side_effect=responses):
            with self.assertRaises(subprocess.CalledProcessError):
                tasklist.open_panel(self.db, self.root, 'retry')
            self.assertEqual(self.db.execute('SELECT COUNT(*) FROM panel_openers').fetchone()[0], 0)
            tasklist.open_panel(self.db, self.root, 'retry')
        self.assertEqual(self.db.execute('SELECT pane FROM sessions').fetchone()[0], '2')

    def test_registration_failure_closes_only_new_pane(self):
        parent = {'pane_id': 1, 'tab_id': 4, 'size': {'rows': 40}}
        self.db.execute("CREATE TRIGGER reject_registration BEFORE UPDATE OF pane ON sessions "
                        "BEGIN SELECT RAISE(FAIL, 'test registration failure'); END")
        with patch.dict(os.environ, {'WEZTERM_PANE': '1'}), \
                patch.object(tasklist.terminals.shutil, 'which', return_value='wezterm'), \
                patch.object(tasklist.terminals.Terminal, 'command', side_effect=[json.dumps([parent, {'pane_id': 9}]), '2', '', '']) as call:
            with self.assertRaises(tasklist.sqlite3.IntegrityError):
                tasklist.open_panel(self.db, self.root, 'registration')
        self.assertEqual([args.args for args in call.call_args_list if args.args[0] == 'kill-pane'],
                         [('kill-pane', '--pane-id', '2')])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM panel_openers').fetchone()[0], 0)

    def test_stale_owner_cannot_steal_live_session(self):
        other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            started = tasklist.process_owner.process(other.pid)[1]
            with self.db:
                self.db.execute('INSERT INTO sessions(session,pane,parent,owner_pid,owner_start) VALUES (?,?,?,?,?)',
                                ('claimed', '2', '1', other.pid, started))
            with patch.dict(os.environ, {'WEZTERM_PANE': '1'}), \
                    patch.object(tasklist.terminals.shutil, 'which', return_value='wezterm'), \
                    patch.object(tasklist.terminals.Terminal, 'command', return_value=json.dumps([
                        {'pane_id': 1, 'tab_id': 4, 'size': {'rows': 40}}])) as call:
                with self.assertRaisesRegex(ValueError, 'another live Codex'):
                    tasklist.open_panel(self.db, self.root, 'claimed')
                self.assertEqual(call.call_count, 1)
            self.assertEqual(self.db.execute('SELECT owner_pid FROM sessions').fetchone()[0], other.pid)
        finally:
            other.terminate()
            other.wait(timeout=3)

    @unittest.skipIf(os.name == 'nt', 'POSIX PTY integration; Windows console needs a real terminal')
    def test_live_panel_scroll_resize_and_update(self):
        for number in range(20):
            tasklist.add(self.db, 'panel', f'Request {number + 1}', 'pending')
        with self.db:
            self.db.execute('INSERT INTO sessions(session,owner_pid,owner_start) VALUES (?,?,?)',
                            ('panel', *self.owner))
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 5, 80, 0, 0))
        process = subprocess.Popen([sys.executable, str(SCRIPT), '--data-dir', str(self.root),
                                    '--session', 'panel', 'view', '--owner-pid', str(self.owner[0]),
                                    '--owner-start', self.owner[1]], stdin=slave, stdout=slave, stderr=slave,
                                   env={key: value for key, value in os.environ.items() if key != 'WEZTERM_PANE'})

        def read_until(expected):
            result = b''
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    result += os.read(master, 65536)
                    if expected.encode() in result:
                        return result.decode()
            self.fail(f'Missing {expected!r}: {result!r}')

        try:
            self.assertIn('#T01', read_until('#T05'))
            os.write(master, b'G')
            self.assertIn('#T16', read_until('#T20'))
            os.write(master, b'g')
            read_until('#T01')
            os.write(master, b'\x1b[<65;10;3M')
            read_until('#T06')
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 8, 80, 0, 0))
            read_until('#T09')
            tasklist.update(self.db, 'panel', 20, 'Changed live', 'active')
            os.write(master, b'g')
            read_until('#T20 Changed live')
            os.write(master, b'q')
            process.wait(timeout=3)
            self.assertEqual(process.returncode, 0)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)
            os.close(master)
            os.close(slave)


if __name__ == '__main__':
    unittest.main()
