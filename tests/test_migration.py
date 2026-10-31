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
if name == 'git' and sys.argv[1:4] == ['-C', sys.argv[2], 'remote'] and 'get-url' in sys.argv:
    print(os.environ['FIXTURE_ORIGIN_' + ('OLD' if sys.argv[2].endswith('old') else 'NEW')])
elif name == 'uname':
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

    def test_credentialed_origins_are_never_printed(self):
        secret = 's3' + 'cr' + 'et' + str(os.getpid())
        for side in ('old', 'new'):
            (self.root / side / '.git').mkdir(parents=True)
        self.env['FIXTURE_ORIGIN_OLD'] = f'https://deploy:{secret}-old@github.com/example/old.git?t={secret}-q#{secret}-f'
        self.env['FIXTURE_ORIGIN_NEW'] = f'ssh://user:{secret}-new@github.com/example/new.git'
        result = self.run_helper()
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertIn('https://***@github.com/example/old.git', result.stdout)
        self.assertIn('ssh://***@github.com/example/new.git', result.stdout)

    def run_origins(self, old, new):
        for side in ('old', 'new'):
            (self.root / side / '.git').mkdir(parents=True, exist_ok=True)
        self.env['FIXTURE_ORIGIN_OLD'] = old
        self.env['FIXTURE_ORIGIN_NEW'] = new
        result = self.run_helper()
        return result.stdout + result.stderr

    def test_slash_in_password_is_fully_masked(self):
        secret = 's3' + 'cr' + 'et' + str(os.getpid())
        out = self.run_origins(f'https://u:pa/{secret}x@github.com/o/r.git',
                               f'u:pa/{secret}x@github.com:o/r.git')
        self.assertNotIn(secret, out)
        self.assertNotIn('pa/', out)
        self.assertIn('https://***@github.com/o/r.git', out)
        self.assertIn('***@github.com:o/r.git', out)

    def test_token_shaped_text_is_masked_anywhere(self):
        tok = 'gh' + 'p_' + 'A' * 36 + str(os.getpid())
        pat = 'github' + '_pat_' + 'B' * 22
        out = self.run_origins(f'https://github.com/o/r@{tok}',
                               f'https://github.com/o/{pat}/r.git')
        self.assertNotIn(tok, out)
        self.assertNotIn(pat, out)
        self.assertIn('https://github.com/o/***/r.git', out)

    def test_redact_url_passes_core_shared_vectors(self):
        checker = REPO / 'core/redaction/check-redact-url.sh'
        if not checker.exists():
            self.skipTest('core submodule is not checked out')
        script = REPO / 'scripts/migrate-from-groundwork.sh'
        function = subprocess.run(['sed', '-n', '/^redact_url() {/,/^}/p', str(script)],
                                  capture_output=True, text=True, check=True).stdout
        self.assertIn('redact_url', function)
        env = {'PATH': os.environ['PATH'], 'HOME': str(self.root), 'XDG_CONFIG_HOME': str(self.root / 'xdg')}
        result = subprocess.run(['bash', str(checker), 'bash', '-c', function + '\nredact_url "$1"', '_'],
                                env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_short_ghp_name_is_not_masked(self):
        out = self.run_origins('https://github.com/o/ghp_utils.git', 'https://github.com/o/new.git')
        self.assertIn('https://github.com/o/ghp_utils.git', out)

    def test_plain_origin_is_still_shown(self):
        (self.root / 'old' / '.git').mkdir(parents=True)
        self.env['FIXTURE_ORIGIN_OLD'] = 'git@github.com:example/old.git'
        self.assertIn('git@github.com:example/old.git', self.run_helper().stdout)


if __name__ == '__main__':
    unittest.main()
