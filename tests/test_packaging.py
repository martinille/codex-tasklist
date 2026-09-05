import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/codex-tasklist'


class PackagingTest(unittest.TestCase):
    def test_installed_plugin_contains_same_license(self):
        self.assertEqual((PLUGIN / 'LICENSE').read_bytes(), (ROOT / 'LICENSE').read_bytes())

    def test_marketplace_matches_packaged_manifest(self):
        marketplace = json.loads((ROOT / '.agents/plugins/marketplace.json').read_text())
        entry, = marketplace['plugins']
        self.assertEqual(entry['source']['source'], 'local')
        self.assertEqual((ROOT / entry['source']['path']).resolve(), PLUGIN.resolve())
        manifest = json.loads((PLUGIN / '.codex-plugin/plugin.json').read_text())
        self.assertEqual(entry['name'], manifest['name'])
        self.assertEqual(manifest['version'], '1.0.2')
        self.assertTrue((PLUGIN / manifest['skills']).is_dir())

    def test_copied_plugin_runs_without_repository_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / 'installed'
            shutil.copytree(PLUGIN, package, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            result = subprocess.run([sys.executable, '-I', '-B', '-c',
                                     "import runpy, sys; sys.path.insert(0, sys.argv[1]); "
                                     "sys.argv = [sys.argv[1] + '/tasklist.py', '--help']; "
                                     "runpy.run_path(sys.argv[0], run_name='__main__')",
                                     str(package / 'scripts')],
                                    cwd=directory, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Codex Tasklist', result.stdout)
            self.assertTrue((package / 'scripts/process_owner.py').is_file())


if __name__ == '__main__':
    unittest.main()
