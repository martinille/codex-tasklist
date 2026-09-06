import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_tasklist import SCRIPT, tasklist
import launcher
import terminals


class TerminalTest(unittest.TestCase):
    def test_windows_uses_codex_directly_without_creating_or_executing_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(launcher, 'os', SimpleNamespace(name='nt', environ={})), \
                    patch.object(launcher.shutil, 'which') as lookup:
                with self.assertRaisesRegex(ValueError, 'run codex directly'):
                    launcher.install(root / 'tasklist.py', root / 'data', root / 'bin')
                with self.assertRaisesRegex(ValueError, 'run codex directly'):
                    launcher.start(['--', 'literal&value', '%PATH%'])
                lookup.assert_not_called()
            self.assertEqual(list(root.iterdir()), [])

    def test_detection_and_inner_terminal_boundaries(self):
        cases = [
            ({'WEZTERM_PANE': '1'}, 'wezterm'),
            ({'TMUX': '/tmp/tmux,123,0', 'TMUX_PANE': '%2', 'WEZTERM_PANE': '1'}, 'tmux'),
            ({'TMUX': '/tmp/tmux,123,0', 'TMUX_PANE': 'invalid', 'WEZTERM_PANE': '1'}, None),
            ({'STY': 'screen', 'WEZTERM_PANE': '1'}, None),
            ({'SSH_CONNECTION': 'remote', 'WEZTERM_PANE': '1'}, None),
            ({'TERM_PROGRAM': 'vscode', 'WEZTERM_PANE': '1'}, None),
            ({'KITTY_WINDOW_ID': '2', 'KITTY_LISTEN_ON': 'unix:/tmp/kitty'}, 'kitty'),
            ({'TERM_PROGRAM': 'WezTerm', 'TERM': 'xterm-kitty', 'WEZTERM_PANE': '1',
              'KITTY_WINDOW_ID': '2', 'KITTY_LISTEN_ON': 'unix:/tmp/kitty'}, 'kitty'),
            ({'TERM_PROGRAM': 'vscode', 'TERM': 'xterm-256color',
              'KITTY_WINDOW_ID': '2', 'KITTY_LISTEN_ON': 'unix:/tmp/kitty'}, None),
            ({'KITTY_WINDOW_ID': '2'}, None),
            ({'KITTY_WINDOW_ID': '2', 'KITTY_LISTEN_ON': 'tcp:example.org:5000'}, None),
            ({}, None),
        ]
        for environment, expected in cases:
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True), \
                    patch.object(terminals.shutil, 'which', side_effect=lambda name: '/bin/' + name):
                found = terminals.detect()
                self.assertEqual(found.kind if found else None, expected)

    def test_tmux_preserves_literal_arguments_and_targets_correct_pane(self):
        terminal = terminals.Terminal('tmux', '%3', '/tmp/socket with spaces')
        arguments = ['/python with spaces', '/task.py', 'title $(touch BAD); "quoted"']
        owner = {'pane_id': '%3', 'tab_id': '@1', 'size': {'rows': 30}}
        with patch.object(terminals, 'run', side_effect=['%3\t@1\t30', '%4', '', '']) as run:
            self.assertEqual(terminal.panes(), [owner])
            self.assertEqual(terminal.split(owner, 5, arguments), '%4')
            terminal.resize('%4', 8, 3)
            terminal.close('%4')
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(shlex.split(commands[1][-1]), ['exec', *arguments])
        self.assertEqual(commands[1][:3], ['tmux', '-S', '/tmp/socket with spaces'])
        self.assertIn('-d', commands[1])
        self.assertEqual(commands[2][-4:], ['-t', '%4', '-y', 8])
        self.assertEqual(commands[3][-3:], ['kill-pane', '-t', '%4'])

    def test_kitty_requires_splits_and_targets_parent_without_focus(self):
        terminal = terminals.Terminal('kitty', '7', 'unix:/tmp/kitty')
        data = [{'tabs': [{'id': 3, 'layout': 'splits', 'windows': [{'id': 7, 'lines': 40}]}]}]
        with patch.object(terminals, 'run', side_effect=[json.dumps(data), '8']) as run, \
                patch.object(terminal, 'owns', return_value=True):
            owner = terminal.owner(terminal.panes())
            self.assertTrue(terminal.ready(owner))
            self.assertFalse(terminal.ready({**owner, 'layout': 'stack'}))
            self.assertEqual(terminal.split(owner, 5, ['python3', 'view.py']), '8')
        command = run.call_args.args[0]
        self.assertEqual(command[command.index('--next-to') + 1], 'id:7')
        self.assertEqual(command[command.index('--match') + 1], 'id:3')
        self.assertIn('--dont-take-focus', command)

    def test_applescript_values_are_arguments_not_source(self):
        arguments = ['/python path', '/script "quoted".py', '$(touch BAD)']
        owner = {'pane_id': 'UUID-1', 'size': {'rows': 40}}
        for kind in ('iterm2', 'ghostty'):
            with self.subTest(kind=kind), patch.object(terminals, 'run', return_value='UUID-2') as run:
                terminal = terminals.Terminal(kind, 'UUID-1', '')
                self.assertEqual(terminal.split(owner, 5, arguments), 'UUID-2')
                command = run.call_args.args[0]
                self.assertNotIn('$(touch BAD)', command[2])
                self.assertIn(shlex.join(arguments), command[3:])
                self.assertIn('UUID-1', command[3:])
                if kind == 'ghostty':
                    self.assertIn('if frontmost then set previousFocus', command[2])
                    self.assertLess(command[2].index('set previousFocus to missing value'),
                                    command[2].index('set child to split'))
                    self.assertIn('try\nif previousFocus is not missing value and frontmost then focus previousFocus\n'
                                  'end try\nreturn id of child', command[2])

    def test_stale_native_ids_cannot_claim_another_tty(self):
        for kind in ('wezterm', 'kitty'):
            terminal = terminals.Terminal(kind, '7', '/tmp/socket')
            pane = {'pane_id': 7, 'pid': 42, 'tty_name': '/dev/pts/2'}
            with self.subTest(kind=kind), patch.object(terminals, 'os', SimpleNamespace(
                    name='posix', getpid=lambda: 1, stat=lambda _: SimpleNamespace(st_rdev=42))), \
                    patch.object(terminals.sys, 'platform', 'linux'), \
                    patch.object(terminals.process_owner, 'terminal_tty', side_effect=lambda pid:'42' if pid==42 else '99'):
                self.assertIsNone(terminal.owner([pane]))
            with patch.object(terminals, 'os', SimpleNamespace(
                    name='posix', getpid=lambda: 1, stat=lambda _: SimpleNamespace(st_rdev=42))), \
                    patch.object(terminals.sys, 'platform', 'linux'), \
                    patch.object(terminals.process_owner, 'terminal_tty', return_value='42'):
                self.assertEqual(terminal.owner([pane]), pane)

    def test_windows_wezterm_rejects_another_terminal_host(self):
        import process_owner
        for host, expected in [('wezterm-gui.exe', True), ('wezterm-mux-server.exe', True),
                               ('WindowsTerminal.exe', False), ('unknown.exe', False)]:
            with self.subTest(host=host), patch.object(process_owner.os, 'getpid', return_value=1), \
                    patch.object(process_owner, 'process', side_effect=[(2, 'a', 'python.exe'),
                                                                          (3, 'b', 'codex.exe'), (4, 'c', host)]):
                self.assertEqual(process_owner.in_windows_wezterm(), expected)

    def test_kitty_resize_corrects_rounding_and_bounds_clamped_retries(self):
        terminal = terminals.Terminal('kitty', '1', 'unix:/tmp/kitty')
        for heights, increments in (([4, 5], [-3, 1]), ([4, 4, 4], [-3, 1, 1])):
            with self.subTest(heights=heights), patch.object(terminal, 'command') as command, \
                    patch.object(terminal, 'panes', side_effect=[
                        [{'pane_id': 2, 'size': {'rows': rows}}] for rows in heights]):
                terminal.resize('2', 5, -3)
                self.assertEqual([call.args[-1] for call in command.call_args_list], increments)

    def test_ghostty_targets_controlling_tty_not_front_window(self):
        with patch.dict(os.environ, {'TERM_PROGRAM': 'ghostty'}, clear=True), \
                patch.object(terminals.sys, 'platform', 'darwin'), \
                patch.object(terminals.shutil, 'which', return_value='/usr/bin/osascript'), \
                patch.object(terminals, 'run', return_value='ttys016'):
            terminal = terminals.detect()
        panes = [{'pane_id': 'other', 'tty': '/dev/ttys015'},
                 {'pane_id': 'correct', 'tty': '/dev/ttys016'}]
        self.assertEqual(terminal.owner(panes)['pane_id'], 'correct')

    def test_fallback_hook_keeps_queue_and_does_not_repeat_notice(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            db = tasklist.connect(Path(directory))
            try:
                tasklist.add(db, 'fallback', 'Retained', 'pending')
                payload = {'session_id': 'fallback', 'hook_event_name': 'SessionStart'}
                self.assertIn('Task storage remains available', tasklist.hook(db, Path(directory), payload)['systemMessage'])
                payload['hook_event_name'] = 'UserPromptSubmit'
                self.assertNotIn('systemMessage', tasklist.hook(db, Path(directory), payload))
                self.assertEqual(tasklist.tasks(db, 'fallback')[0]['title'], 'Retained')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM panel_openers').fetchone()[0], 0)
            finally:
                db.close()

    def test_tmux_open_is_idempotent_and_migration_preserves_ids(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {'TMUX': '/tmp/socket,12,0', 'TMUX_PANE': '%1'}, clear=True), \
                patch.object(terminals.shutil, 'which', return_value='/bin/tmux'), \
                patch.object(terminals, 'run', side_effect=['%1\t@1\t40', '%2', '%1\t@1\t34\n%2\t@1\t5']) as run:
            db = tasklist.connect(Path(directory))
            identity = (os.getpid(), tasklist.process_owner.process(os.getpid())[1])
            try:
                tasklist.add(db, 'test', 'Retained', 'done')
                with patch.object(tasklist.process_owner, 'discover', return_value=identity):
                    tasklist.open_panel(db, Path(directory), 'test')
                    tasklist.open_panel(db, Path(directory), 'test')
                state = db.execute('SELECT * FROM sessions').fetchone()
                self.assertEqual((state['backend'], state['pane'], state['socket']), ('tmux', '%2', '/tmp/socket'))
                self.assertTrue(state['panel_token'])
                self.assertEqual(tasklist.tasks(db, 'test')[0]['id'], 1)
                self.assertEqual(sum('split-window' in call.args[0] for call in run.call_args_list), 1)
            finally:
                db.close()


@unittest.skipIf(os.name == 'nt', 'Launcher is only for Linux/macOS/WSL')
class LauncherTest(unittest.TestCase):
    def test_start_preserves_umask_and_queue_data_remains_private(self):
        before = os.umask(0o022)
        try:
            with patch.object(sys, 'argv', ['tasklist.py', 'start']), \
                    patch.object(tasklist.launcher, 'start', side_effect=lambda _: os.umask(0o022)) as start:
                self.assertEqual(tasklist.main(), 0o022)
                start.assert_called_once()
            with tempfile.TemporaryDirectory() as directory:
                data = Path(directory) / 'private'
                result = subprocess.run([sys.executable, str(SCRIPT), '--data-dir', str(data),
                                         '--session', 'private', 'add', 'Private'], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(data.stat().st_mode & 0o777, 0o700)
                self.assertEqual((data / 'tasks.sqlite3').stat().st_mode & 0o777, 0o600)
        finally:
            os.umask(before)

    def test_launcher_preserves_codex_arguments_and_uses_private_tmux(self):
        arguments = ['resume', 'abc', 'text ; $(touch BAD)']
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(launcher.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name), \
                patch.object(launcher.terminals, 'detect', return_value=None), \
                patch.object(sys.stdin, 'isatty', return_value=True), \
                patch.object(sys.stdout, 'isatty', return_value=True), \
                patch.object(launcher.subprocess, 'call', return_value=0) as execute:
            launcher.start(arguments)
        command = execute.call_args.args[0]
        self.assertEqual(command[0], '/usr/bin/tmux')
        self.assertTrue(command[2].startswith('codex-tasklist-'))
        self.assertIn(os.devnull, command)
        self.assertEqual(shlex.split(command[8])[4:], ['/usr/bin/codex', *arguments])
        self.assertIn('destroy-unattached', command)

    def test_tmux_setup_failure_falls_back_but_never_restarts_started_codex(self):
        for already_started in (False, True):
            def fail(command):
                if already_started:
                    Path(shlex.split(command[8])[3]).touch()
                return 1

            with self.subTest(already_started=already_started), patch.dict(os.environ, {}, clear=True), \
                    patch.object(launcher.shutil, 'which', side_effect=lambda name: '/bin/' + name), \
                    patch.object(launcher.terminals, 'detect', return_value=None), \
                    patch.object(sys.stdin, 'isatty', return_value=True), \
                    patch.object(sys.stdout, 'isatty', return_value=True), \
                    patch.object(launcher.subprocess, 'call', side_effect=fail), \
                    patch.object(launcher.subprocess, 'run'), \
                    patch.object(launcher.os, 'execv') as execute, patch.object(sys, 'stderr'):
                launcher.start(['resume'])
            self.assertEqual(execute.call_count, 0 if already_started else 1)

    def test_no_tmux_and_existing_mux_do_not_prevent_codex_start(self):
        for environment in ({}, {'TMUX': 'existing'}):
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True), \
                    patch.object(launcher.shutil, 'which', side_effect=lambda name: '/bin/codex' if name == 'codex' else None), \
                    patch.object(launcher.terminals, 'detect', return_value=None), \
                    patch.object(launcher.os, 'execv') as execute, patch.object(sys, 'stderr'):
                launcher.start(['--', 'resume', 'abc'])
            execute.assert_called_once_with('/bin/codex', ['/bin/codex', 'resume', 'abc'])

    def test_install_and_run_wrapper_without_shell_interpretation(self):
        with tempfile.TemporaryDirectory(prefix='tasklist space ') as directory:
            root = Path(directory)
            script = root / "task 'script.py"
            script.write_text('import json, sys; print(json.dumps(sys.argv[1:]))')
            path = launcher.install(script, root / 'data', root / 'bin')
            launcher.install(script, root / 'data', root / 'bin')
            arguments = ['resume', 'literal $(touch BAD); "quoted"']
            result = subprocess.run([str(path), *arguments], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout), ['--data-dir', str(root / 'data'), 'start', '--', *arguments])
            path.write_text('unrelated command')
            with self.assertRaises(ValueError):
                launcher.install(script, root, root / 'bin')
            self.assertEqual(path.read_text(), 'unrelated command')


if __name__ == '__main__':
    unittest.main()
