"""Migration guidance prepares a checkout without applying another environment."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
STUB = '''#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ['MIGRATION_FIXTURE'])
name = pathlib.Path(sys.argv[0]).name
with (root / 'calls').open('a') as log:
    log.write(json.dumps([name, *sys.argv[1:]]) + '\\n')
if name == 'uname':
    print(os.environ.get('FIXTURE_OS', 'Darwin'))
elif name == 'git' and sys.argv[1] == 'clone':
    destination = pathlib.Path(sys.argv[-1])
    destination.mkdir()
    (destination / '.git').mkdir()
    (destination / 'bootstrap-mac.sh').write_text('touch "$MIGRATION_FIXTURE/applied"\\n')
'''


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='groundwork-migration-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        binary_dir = self.root / 'bin'
        binary_dir.mkdir()
        stub = binary_dir / 'stub'
        stub.write_text(STUB)
        stub.chmod(0o755)
        for name in ('uname', 'git', 'gh', 'omarchy'):
            (binary_dir / name).symlink_to(stub)
        self.env = dict(os.environ, MIGRATION_FIXTURE=str(self.root),
                        PATH=f'{binary_dir}:/usr/bin:/bin',
                        GROUNDWORK_OLD_DIR=str(self.root / 'old'),
                        GROUNDWORK_MAC_DIR=str(self.root / 'new'))

    def run_helper(self, *args, status=0):
        result = subprocess.run(['bash', str(REPO / 'scripts/migrate-from-groundwork.sh'), *args],
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, status, result.stdout + result.stderr)
        return result

    def calls(self):
        path = self.root / 'calls'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_help_and_invalid_input_have_no_external_effects(self):
        self.run_helper('--help')
        self.run_helper('--typo', status=1)
        self.assertEqual(self.calls(), [])

    def test_mac_checkout_uses_https_without_auth_or_apply(self):
        self.run_helper('--clone-mac')
        clones = [call for call in self.calls() if call[0] == 'git']
        self.assertEqual(clones, [['git', 'clone', '--recurse-submodules',
                                  'https://github.com/jasondockery/groundwork-mac.git',
                                  str(self.root / 'new')]])
        self.assertFalse(any(call[0] == 'gh' for call in self.calls()))
        self.assertFalse((self.root / 'applied').exists())

    def test_existing_checkout_is_preserved(self):
        destination = self.root / 'new'
        (destination / '.git').mkdir(parents=True)
        sentinel = destination / 'user-file'
        sentinel.write_text('preserve me')
        self.run_helper('--clone-mac')
        self.assertFalse(any(call[1:2] == ['clone'] for call in self.calls()))
        self.assertEqual(sentinel.read_text(), 'preserve me')

    def test_omarchy_refuses_mac_clone_before_creating_checkout(self):
        self.env['FIXTURE_OS'] = 'Linux'
        result = self.run_helper('--clone-mac', status=1)
        self.assertIn('Refusing --clone-mac on Omarchy', result.stderr)
        self.assertFalse((self.root / 'new').exists())
        self.assertFalse(any(call[0] == 'git' for call in self.calls()))


if __name__ == '__main__':
    unittest.main()
