from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import select
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
    @unittest.expectedFailure
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
                    patch.object(tasklist, 'wezterm', side_effect=wezterm), \
                    ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(open_panel, False)
                second = executor.submit(open_panel, True)
                first.result(timeout=15)
                second.result(timeout=15)
        self.assertEqual(len(splits), 1)

    @unittest.skipIf(os.name == 'nt', 'POSIX PTY integration; Windows needs a real console')
    def test_session_end_exits_view_and_preserves_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = tasklist.connect(root)
            master, slave = pty.openpty()
            process = None
            try:
                tasklist.add(db, 'lifecycle', 'Keep after session ends', 'pending')
                with db:
                    db.execute('INSERT INTO sessions(session) VALUES (?)', ('lifecycle',))
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 5, 80, 0, 0))
                process = subprocess.Popen([sys.executable, str(SCRIPT), '--data-dir', directory,
                                            '--session', 'lifecycle', 'view'],
                                           stdin=slave, stdout=slave, stderr=slave)
                output = b''
                deadline = time.monotonic() + 3
                while b'Keep after session ends' not in output and time.monotonic() < deadline:
                    if select.select([master], [], [], 0.1)[0]:
                        output += os.read(master, 65536)
                self.assertIn(b'Keep after session ends', output)
                tasklist.hook(db, root, {'session_id': 'lifecycle', 'hook_event_name': 'SessionEnd'})
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


if __name__ == '__main__':
    unittest.main()
