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
spec = importlib.util.spec_from_file_location('tasklist', SCRIPT)
tasklist = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tasklist)


class TasklistTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = tasklist.connect(self.root)

    def tearDown(self):
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

    def test_open_is_idempotent_and_end_does_not_delete_tasks(self):
        parent = {'pane_id': 1, 'tab_id': 4, 'size': {'rows': 40}}
        responses = [json.dumps([parent]), '2', '', json.dumps([parent, {'pane_id': 2, 'tab_id': 4}])]
        with patch.dict(os.environ, {'WEZTERM_PANE': '1', 'WEZTERM_UNIX_SOCKET': 'test'}), \
                patch.object(tasklist.shutil, 'which', return_value='/usr/bin/wezterm'), \
                patch.object(tasklist, 'wezterm', side_effect=responses) as call:
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

    @unittest.skipIf(os.name == 'nt', 'POSIX PTY integration; Windows console needs a real terminal')
    def test_live_panel_scroll_resize_and_update(self):
        for number in range(20):
            tasklist.add(self.db, 'panel', f'Request {number + 1}', 'pending')
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 5, 80, 0, 0))
        process = subprocess.Popen([sys.executable, str(SCRIPT), '--data-dir', str(self.root),
                                    '--session', 'panel', 'view'], stdin=slave, stdout=slave, stderr=slave)

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
