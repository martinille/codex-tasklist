import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from test_tasklist import tasklist


owner = tasklist.process_owner


class ProcessOwnerTest(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'POSIX controlling terminal')
    def test_controlling_tty_survives_captured_hook_output(self):
        import pty
        master, slave = pty.openpty()
        code = '''import os,sys,fcntl,termios
sys.path.insert(0,sys.argv[1])
import process_owner
os.setsid()
fcntl.ioctl(0,termios.TIOCSCTTY,0)
expected=str(os.stat(os.ttyname(0)).st_rdev) if sys.platform.startswith('linux') else os.ttyname(0)
assert not sys.stdout.isatty()
assert process_owner.terminal_tty(os.getpid()) == expected
print('matched')
'''
        try:
            result = subprocess.run([sys.executable, '-c', code, str(tasklist.SCRIPT.parent)],
                                    stdin=slave, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), b'matched')
        finally:
            os.close(master)
            os.close(slave)

    def test_current_process_and_missing_or_reused_pid(self):
        details = owner.process(os.getpid())
        self.assertIsNotNone(details)
        self.assertTrue(owner.alive((os.getpid(), details[1])))
        self.assertFalse(owner.alive((os.getpid(), details[1] + '-reused')))
        self.assertIsNone(owner.process(-1))
        with patch.object(owner, 'process', return_value=None):
            self.assertFalse(owner.alive((123, 'start')))

    def test_nearest_native_codex_and_bounded_unavailable_ancestry(self):
        chain = {40: (30, 'shell', 'powershell.exe'), 30: (20, 'nearest', 'codex.exe'),
                 20: (10, 'older', 'codex')}
        with patch.object(owner.os, 'getppid', return_value=40), \
                patch.object(owner, 'process', side_effect=chain.get) as lookup:
            self.assertEqual(owner.discover(), (30, 'nearest'))
            self.assertEqual(lookup.call_count, 2)
        for details in (None, (40, 'cycle', 'bash'), (0, 'unrelated', 'codex-code-mode-host')):
            with self.subTest(details=details), patch.object(owner.os, 'getppid', return_value=40), \
                    patch.object(owner, 'process', return_value=details) as lookup:
                self.assertIsNone(owner.discover())
                self.assertLessEqual(lookup.call_count, 32)
        with patch.object(owner.os, 'getppid', return_value=100), \
                patch.object(owner, 'process', side_effect=lambda pid: (pid + 1, 'start', 'shell')) as lookup:
            self.assertIsNone(owner.discover())
            self.assertEqual(lookup.call_count, 32)

    def test_daemon_hooks_bind_the_session_to_an_interactive_client(self):
        def entry(pid, tty='pts', arguments=('codex',), cwd='/work'):
            return {'identity': (pid, f'start{pid}'), 'order': pid, 'tty': tty, 'arguments': list(arguments), 'cwd': cwd}
        found = [entry(1, arguments=('codex', 'app-server', '--managed-daemon'), tty=''),
                 entry(2, arguments=('codex', 'exec', 'task')), entry(3, cwd='/other'),
                 entry(4, arguments=('codex', 'resume', 'abc')), entry(5), entry(6, tty='')]
        with patch.object(owner, 'clients', return_value=found):
            self.assertEqual(owner.client('abc', '/work'), (4, 'start4'))
            self.assertEqual(owner.client('other', '/work'), (5, 'start5'))
            self.assertEqual(owner.client('other', '/work', exclude={(5, 'start5')}), (4, 'start4'))
            self.assertEqual(owner.client('other', '/none'), (5, 'start5'))
            self.assertEqual(owner.client('other', None, exclude={(4, 'start4'), (5, 'start5')}), (3, 'start3'))
            self.assertIsNone(owner.client('other', None, exclude={(3, 'start3'), (4, 'start4'), (5, 'start5')}))
        with patch.object(owner, 'arguments', return_value=['codex', 'app-server', '--listen', 'unix://']):
            self.assertTrue(owner.daemon(1))
        with patch.object(owner, 'arguments', return_value=['codex', 'resume', 'app-server']):
            self.assertTrue(owner.daemon(1))
        with patch.object(owner, 'arguments', return_value=['codex']):
            self.assertFalse(owner.daemon(1))
        self.assertFalse(owner.daemon(os.getpid()))

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Reads /proc')
    def test_linux_client_listing_reports_tty_arguments_cwd_and_environment(self):
        details = owner.process(os.getpid())
        with patch.object(owner.Path, 'iterdir', return_value=[owner.Path('/proc') / str(os.getpid())]), \
                patch.object(owner, 'process', return_value=(details[0], details[1], 'codex')), \
                patch.object(owner.Path, 'read_text', return_value='codex\n'):
            found = owner.clients()
        self.assertEqual(found[0]['identity'], (os.getpid(), details[1]))
        self.assertEqual(found[0]['cwd'], os.getcwd())
        self.assertEqual(found[0]['arguments'], owner.arguments(os.getpid()))
        self.assertIn('unittest', ' '.join(found[0]['arguments']))
        self.assertEqual(found[0]['tty'], owner.terminal_tty(os.getpid()))
        self.assertEqual(owner.environment(os.getpid()).get('PATH'), os.environ.get('PATH'))
        self.assertIsNone(owner.environment(-1))

    def test_macos_metadata_and_lookup_failure(self):
        result = subprocess.CompletedProcess([], 0, ' 42 Sat Sep  5 20:00:00 2026 /Applications/Codex CLI/codex\n')
        with patch.object(owner.sys, 'platform', 'darwin'), patch.object(owner.subprocess, 'run', return_value=result) as run:
            self.assertEqual(owner.process(12), (42, 'Sat Sep 5 20:00:00 2026', 'codex'))
            self.assertNotIn('args=', run.call_args.args[0])
            self.assertLessEqual(run.call_args.kwargs['timeout'], 1)
        with patch.object(owner.sys, 'platform', 'darwin'), \
                patch.object(owner.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ps', 0.25)):
            self.assertIsNone(owner.process(12))


if __name__ == '__main__':
    unittest.main()
