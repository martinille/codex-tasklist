"""Run with tmux on PATH; uses a private socket and never touches existing sessions."""
import os
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from test_tasklist import tasklist, SCRIPT


@unittest.skipUnless(os.name != 'nt' and shutil.which('tmux'), 'Requires tmux (Linux/macOS/WSL)')
class LiveTmuxTest(unittest.TestCase):
    def test_launcher_preserves_failure_status_and_child_umask(self):
        import fcntl
        import pty
        import struct
        import termios

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'codex'
            ready = root / 'ready'
            fake.write_text('#!' + sys.executable + '\nimport os,sys,signal\n'
                            'mask=os.umask(0o022)\n'
                            'if mask != 0o022: sys.exit(99)\n'
                            "if sys.argv[1]=='interrupt':\n"
                            ' signal.signal(signal.SIGINT, lambda *_: sys.exit(23))\n'
                            f' open({str(ready)!r}, "w").close()\n'
                            ' while True: signal.pause()\n'
                            'sys.exit(int(sys.argv[1]))\n')
            fake.chmod(0o755)
            environment = {key: value for key, value in os.environ.items()
                           if key not in ('TMUX', 'STY', 'ZELLIJ', 'WEZTERM_PANE', 'KITTY_WINDOW_ID')}
            environment.update(TERM_PROGRAM='test', TERM='xterm-256color', PATH=str(root)+os.pathsep+os.environ['PATH'])
            for status in (0, 7, 'interrupt'):
                with self.subTest(status=status):
                    master, slave = pty.openpty()
                    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 80, 0, 0))
                    process = subprocess.Popen([sys.executable, str(SCRIPT), 'start', '--', str(status)],
                                               stdin=slave, stdout=slave, stderr=slave, env=environment, umask=0o022)
                    try:
                        if status == 'interrupt':
                            deadline = time.monotonic() + 5
                            while not ready.exists() and time.monotonic() < deadline:
                                time.sleep(0.02)
                            self.assertTrue(ready.exists(), 'Codex child did not start')
                            os.write(master, b'\x03')
                        self.assertEqual(process.wait(timeout=8), 23 if status == 'interrupt' else status)
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.wait(timeout=3)
                        os.close(master)
                        os.close(slave)

    def test_simple_launcher_creates_private_session_and_forwards_options(self):
        import fcntl
        import pty
        import struct
        import termios

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / 'report.json'
            fake = root / 'codex'
            fake.write_text('#!' + sys.executable + '\nimport json, os, sys, time\n'
                            f'open({str(report)!r}, "w").write(json.dumps([os.environ.get("TMUX"), sys.argv[1:], os.getcwd()]))\n'
                            'time.sleep(0.4)\n')
            fake.chmod(0o755)
            environment = {key: value for key, value in os.environ.items()
                           if key not in ('TMUX', 'STY', 'ZELLIJ', 'WEZTERM_PANE', 'KITTY_WINDOW_ID', 'ITERM_SESSION_ID')}
            environment.update(TERM_PROGRAM='test', TERM='xterm-256color', PATH=str(root) + os.pathsep + os.environ['PATH'])
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 80, 0, 0))
            process = subprocess.Popen([sys.executable, str(SCRIPT), 'start', '--', '--model', 'literal;$(value)'],
                                       stdin=slave, stdout=slave, stderr=slave, env=environment, cwd=root)
            try:
                self.assertEqual(process.wait(timeout=8), 0)
                socket, arguments, cwd = json.loads(report.read_text())
                self.assertIn('codex-tasklist-', socket)
                self.assertEqual(arguments, ['--model', 'literal;$(value)'])
                self.assertEqual(cwd, str(root))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=3)
                if report.exists():
                    socket = json.loads(report.read_text())[0].rsplit(',', 2)[0]
                    subprocess.run(['tmux', '-S', socket, 'kill-server'], capture_output=True, timeout=3)
                os.close(master)
                os.close(slave)

    def test_live_panel_updates_resize_reopen_and_owner_death(self):
        with tempfile.TemporaryDirectory(prefix='tasklist tmux ') as directory:
            root = Path(directory)
            socket = str(root / 'socket')
            command = ['tmux', '-S', socket]

            def tmux(*arguments):
                return subprocess.run([*command, *arguments], capture_output=True, text=True,
                                      check=True, timeout=5).stdout.strip()

            def eventually(check):
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if check():
                        return
                    time.sleep(0.05)
                self.fail('tmux panel did not reach the expected state')

            db = tasklist.connect(root)
            owner = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
            try:
                tmux('-f', os.devnull, 'new-session', '-d', '-x', '80', '-y', '30', '-s', 'test', 'sleep 30')
                parent = tmux('display-message', '-p', '-t', 'test', '#{pane_id}')
                identity = (owner.pid, tasklist.process_owner.process(owner.pid)[1])
                environment = {'TMUX': socket + ',1,0', 'TMUX_PANE': parent}
                with patch.dict(os.environ, environment), patch.object(tasklist.process_owner, 'discover', return_value=identity):
                    tasklist.add(db, 'live', 'Before update', 'pending')
                    tasklist.open_panel(db, root, 'live')
                    tasklist.open_panel(db, root, 'live')
                    panel = db.execute('SELECT pane FROM sessions').fetchone()[0]
                    self.assertEqual(len(tmux('list-panes', '-t', 'test').splitlines()), 2)
                    eventually(lambda: 'Before update' in tmux('capture-pane', '-p', '-t', panel))
                    self.assertEqual(tmux('display-message', '-p', '-t', parent, '#{pane_active}'), '1')
                    tasklist.update(db, 'live', 1, 'Updated live', 'active')
                    eventually(lambda: 'Updated live' in tmux('capture-pane', '-p', '-t', panel))
                    with db:
                        db.execute("INSERT INTO settings VALUES ('rows', 8)")
                    eventually(lambda: tmux('display-message', '-p', '-t', panel, '#{pane_height}') == '8')
                    tmux('send-keys', '-t', panel, 'q')
                    eventually(lambda: len(tmux('list-panes', '-t', 'test').splitlines()) == 1)
                    tasklist.open_panel(db, root, 'live')
                    self.assertEqual(len(tmux('list-panes', '-t', 'test').splitlines()), 2)
                    owner.terminate()
                    owner.wait(timeout=3)
                    eventually(lambda: len(tmux('list-panes', '-t', 'test').splitlines()) == 1)
                    self.assertEqual(tasklist.tasks(db, 'live')[0]['title'], 'Updated live')
            finally:
                subprocess.run([*command, 'kill-server'], capture_output=True, timeout=5)
                if owner.poll() is None:
                    owner.terminate()
                    owner.wait(timeout=3)
                db.close()


if __name__ == '__main__':
    unittest.main()
