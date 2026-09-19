from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'plugins/codex-tasklist/scripts'
sys.path.insert(0, str(SCRIPTS))
import storage
import tasklist


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.archive = Path(self.temp.name).resolve() / 'persistent'
        self.runtime = storage.working_directory(self.archive)
        self.addCleanup(shutil.rmtree, self.runtime)
        self.env = {**os.environ, 'PLUGIN_DATA': str(self.archive), 'TERM_PROGRAM': '',
                    'TMUX': '', 'STY': '', 'ZELLIJ': '', 'WEZTERM_PANE': '',
                    'KITTY_LISTEN_ON': '', 'GHOSTTY_RESOURCES_DIR': ''}

    def hook(self, event):
        result = subprocess.run([sys.executable, str(SCRIPTS / 'tasklist.py'), 'hook'],
                                input=json.dumps({'hook_event_name': event, 'session_id': 'storage-test'}),
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_hook_cli_checkpoint_and_resume_after_temp_cleanup(self):
        output = self.hook('SessionStart')
        context = output['hookSpecificOutput']['additionalContext']
        self.assertIn(str(self.runtime), context)
        self.assertNotIn(str(self.archive), context)
        command = [sys.executable, str(SCRIPTS / 'tasklist.py'), '--data-dir', str(self.runtime),
                   '--session', 'storage-test']
        for args in [('add', 'Živá úloha 世界', '--status', 'active'), ('rows', '7')]:
            result = subprocess.run(command + list(args), capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.hook('PostToolUse')
        with closing(sqlite3.connect(self.archive / 'runtime.sqlite3')) as saved:
            self.assertEqual(saved.execute('SELECT id,title,status FROM tasks').fetchone(),
                             (1, 'Živá úloha 世界', 'active'))
        self.assertEqual(self.hook('Stop')['decision'], 'block')
        subprocess.run(command + ['update', '1', '--status', 'done'], check=True, timeout=5)
        self.assertEqual(self.hook('Stop'), {})
        self.hook('SessionEnd')
        shutil.rmtree(self.runtime)
        self.hook('SessionStart')
        with closing(tasklist.connect(self.runtime)) as db, db:
            self.assertEqual(tasklist.tasks(db, 'storage-test')[0]['status'], 'done')
            self.assertEqual(tasklist.row_count(db), 7)

    def test_legacy_migration_preserves_ids_and_all_sessions(self):
        shutil.rmtree(self.runtime)
        with closing(tasklist.connect(self.archive)) as db, db:
            tasklist.add(db, 'first', 'Existing', 'done')
            tasklist.add(db, 'second', 'Other conversation', 'blocked')
        self.hook('SessionStart')
        with closing(tasklist.connect(self.runtime)) as db, db:
            self.assertEqual(tasklist.tasks(db, 'first')[0]['id'], 1)
            self.assertEqual(tasklist.tasks(db, 'second')[0]['status'], 'blocked')
            tasklist.update(db, 'first', 1, status='active')
        self.hook('UserPromptSubmit')
        with closing(tasklist.connect(self.runtime)) as db, db:
            self.assertEqual(tasklist.tasks(db, 'first')[0]['status'], 'active')
        with closing(tasklist.connect(self.archive)) as db, db:
            self.assertEqual(tasklist.tasks(db, 'first')[0]['status'], 'done')

    def test_concurrent_hooks_and_queue_writes_share_one_database(self):
        shutil.rmtree(self.runtime)
        processes = [subprocess.Popen([sys.executable, str(SCRIPTS / 'tasklist.py'), 'hook'],
                                      env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True) for _ in range(6)]
        for process in processes:
            process.stdin.write(json.dumps({'hook_event_name': 'PostToolUse', 'session_id': 'parallel'}))
            process.stdin.close()
            process.stdin = None
        for process in processes:
            out, err = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, err)
        with closing(tasklist.connect(self.runtime)) as db, db:
            tasklist.add(db, 'parallel', 'Latest', 'done')
        self.hook('PostToolUse')
        with closing(sqlite3.connect(self.archive / 'runtime.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT title FROM tasks').fetchone()[0], 'Latest')

    @unittest.skipIf(os.name == 'nt', 'POSIX permissions and symlinks')
    def test_rejects_unsafe_runtime_directory(self):
        self.runtime.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            storage.working_directory(self.archive)
        self.runtime.chmod(0o700)
        shutil.rmtree(self.runtime)
        self.runtime.symlink_to(self.temp.name, target_is_directory=True)
        try:
            with self.assertRaisesRegex(ValueError, 'unsafe'):
                storage.working_directory(self.archive)
        finally:
            self.runtime.unlink()
            self.runtime.mkdir(mode=0o700)

    def test_failed_checkpoint_reports_error_without_false_success(self):
        self.hook('PostToolUse')
        with closing(tasklist.connect(self.runtime)) as db, db, patch.object(storage.sqlite3, 'connect',
                                                               side_effect=sqlite3.OperationalError('disk full')):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'disk full'):
                storage.checkpoint(db, self.archive)

    @unittest.skipUnless(sys.platform == 'linux' and os.environ.get('TASKLIST_TEST_SANDBOX') == '1',
                         'opt-in Linux mount sandbox regression')
    def test_agent_writes_with_persistent_storage_mounted_readonly(self):
        tasklist.connect(self.archive).close()
        self.hook('SessionStart')
        sandbox = ['bwrap', '--ro-bind', '/', '/', '--bind', str(self.runtime), str(self.runtime),
                   '--dev', '/dev', '--proc', '/proc', '--unshare-net', '--', sys.executable,
                   '-B', str(SCRIPTS / 'tasklist.py')]
        old = subprocess.run(sandbox + ['--data-dir', str(self.archive), '--session', 'storage-test',
                                        'add', 'Old path'], capture_output=True, text=True, timeout=10)
        self.assertNotEqual(old.returncode, 0)
        self.assertIn('readonly', old.stderr)
        new = subprocess.run(sandbox + ['--data-dir', str(self.runtime), '--session', 'storage-test',
                                        'add', 'Sandbox write', '--status', 'done'],
                             capture_output=True, text=True, timeout=10)
        self.assertEqual(new.returncode, 0, new.stderr)
        self.hook('PostToolUse')
        with closing(sqlite3.connect(self.archive / 'runtime.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT title FROM tasks').fetchone()[0], 'Sandbox write')


if __name__ == '__main__':
    unittest.main()
