"""T3 Code through Omarchy's installer (decision 0020): setup, its check, and the t3-cli offer.

Same private PATH, home, /etc, and fixture stubs as the installer tests. The
fake omarchy-install-ai-t3-code writes Omarchy's layout. A fake bwrap stands in
for the sandbox: it unpacks the fake AppImage and answers t3code --version.
No test reaches the network: HTTPS_PROXY points at a closed port.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import test_install_keyd as base
import test_migration_offer as offers

REPO = base.REPO
INSTALLER = ['omarchy-install-ai-t3-code', 'nightly', '--no-launch']
OLD_CLI = '0.1.2-nightly.20261021.3457'
VERSION = '0.1.6-nightly.20261031.3956'

# Runs under the updater's scrubbed environment, so its behavior is baked in
# by the test (the config path is the only input).
FAKE_BWRAP = '''#!/usr/bin/env python3
import json, os, shutil, sys
config = json.load(open(%r))
args = sys.argv[sys.argv.index('--') + 1:]
binary, rest = args[0], args[1:]
if rest == ['--appimage-extract']:
    os.makedirs('squashfs-root/resources/prebuilds', exist_ok=True)
    shutil.copyfile(binary, 'squashfs-root/t3code')
    with open('squashfs-root/t3code.desktop', 'w') as entry:
        entry.write('[Desktop Entry]\\nName=T3 Code\\nX-AppImage-Version=%%s\\n' %% config['version'])
    sys.exit(0)
if rest == ['--version']:
    if config.get('crash'):
        sys.exit(139)
    print(config['version'])
    sys.exit(0)
sys.exit(2)
'''


class T3InstallTests(unittest.TestCase):
    calls = base.InstallKeydTests.calls
    run_in_terminal = offers.MigrationOfferTests.run_in_terminal
    saved = offers.MigrationOfferTests.saved

    def setUp(self):
        base.InstallKeydTests.setUp(self)
        self.home = self.root / 'home'
        self.appimage = self.home / '.local/share/t3code/T3-Code.AppImage'
        self.link = self.home / '.local/bin/t3'
        self.app_cli = self.home / '.t3/bin/t3'
        self.state = self.home / '.local/state/groundwork'
        self.env.update(HTTPS_PROXY='http://127.0.0.1:9', https_proxy='http://127.0.0.1:9', NO_PROXY='', no_proxy='')

    def run_install(self, *args, success=True):
        result = subprocess.run(['bash', str(REPO / 'install.sh'), *args],
                                env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def run_update(self, success=True):
        result = subprocess.run(['python3', str(REPO / 'scripts/groundwork-update.py'), 'setup', str(REPO), '--update'],
                                env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def reset_calls(self):
        (self.root / 'calls.jsonl').unlink(missing_ok=True)

    def installer_calls(self):
        return [call for call in self.calls() if call[0] == 'omarchy-install-ai-t3-code']

    def large_pages(self, crash=False):
        """16 KiB pages, readelf reporting 0x10000, and a fake bwrap."""
        config = self.root / 'bwrap.json'
        config.write_text(json.dumps({'version': VERSION, 'crash': crash}))
        bwrap = self.bin / 'bwrap'
        bwrap.write_text(FAKE_BWRAP % str(config))
        bwrap.chmod(0o755)
        self.env.update(FIXTURE_PAGESIZE='16384', FIXTURE_LOAD_ALIGN='0x10000')

    def seed_cli(self):
        """The bun CLI from t3.codes/install.sh, and T3 data that must survive."""
        build = self.home / '.t3/runtime/versions' / OLD_CLI / 't3'
        build.parent.mkdir(parents=True)
        build.write_text(f'#!/bin/sh\nprintf "t3 v{OLD_CLI}\\n"\n')
        build.chmod(0o755)
        self.link.parent.mkdir(parents=True, exist_ok=True)
        self.link.symlink_to(build)
        kept = {
            self.home / '.t3/userdata/state.sqlite': 'threads',
            self.home / '.config/t3code/settings.json': '{"theme": "omarchy"}',
            self.home / '.config/t3code-v2/state.json': '{}',
            self.home / '.config/t3code-flags.conf': '--ozone-platform=wayland\n',
            self.home / '.cache/t3code-updater/installer.log': 'log',
        }
        for path, text in kept.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return build, kept

    def seed_app(self):
        subprocess.run([str(self.bin / 'omarchy-install-ai-t3-code'), 'nightly', '--no-launch'],
                       env=self.env, check=True, timeout=10)
        self.reset_calls()

    def seed_app_cli(self, version=VERSION):
        self.app_cli.parent.mkdir(parents=True, exist_ok=True)
        self.app_cli.write_text(f'#!/bin/sh\nprintf "t3 v{version}\\n"\n')
        self.app_cli.chmod(0o755)

    def process(self, pid, exe):
        directory = self.root / 'proc' / str(pid)
        directory.mkdir()
        (directory / 'exe').symlink_to(exe)
        (directory / 'cmdline').write_bytes(exe.encode() + b'\0')

    def assert_kept(self, kept):
        for path, text in kept.items():
            self.assertEqual(path.read_text(), text, path)
        self.assertTrue((self.home / '.t3/runtime/versions' / OLD_CLI / 't3').is_file())

    # New installs.

    def test_new_install_runs_omarchys_installer_once(self):
        result = self.run_install()
        self.assertEqual(self.installer_calls(), [INSTALLER])
        self.assertFalse(any(call[0] == 'curl' for call in self.calls()))
        self.assertTrue(self.appimage.is_file())
        self.assertEqual(os.readlink(self.link), str(self.app_cli))
        self.assertIn('Open T3 Code once from the app launcher', result.stdout)
        self.assertNotIn('migration t3-cli', result.stdout)
        self.reset_calls()
        self.run_install()
        self.assertEqual(self.installer_calls(), [])
        result = self.run_update()
        self.assertEqual(self.installer_calls(), [])
        self.assertIn('sudo was not used', result.stdout)

    def test_an_update_reinstalls_a_missing_app_with_administrator_permission(self):
        self.run_install()
        self.appimage.unlink()
        self.reset_calls()
        result = self.run_update()
        self.assertEqual(self.installer_calls(), [INSTALLER])
        self.assertNotIn('sudo was not used', result.stdout)

    def test_installer_failure_stops_before_home_changes(self):
        self.env['FIXTURE_T3_INSTALL_STATUS'] = '1'
        result = self.run_install(success=False)
        self.assertIn('omarchy-install-ai-t3-code failed', result.stderr)
        self.assertFalse(any(call[0] in ('stow', 'hyprctl') for call in self.calls()))

    def test_installer_without_the_app_stops(self):
        self.env['FIXTURE_T3_INSTALL_SKIP_APP'] = '1'
        result = self.run_install(success=False)
        self.assertIn(f'{self.appimage} is missing', result.stderr)
        self.assertFalse(any(call[0] == 'stow' for call in self.calls()))

    def test_missing_omarchy_installer_asks_for_an_omarchy_update(self):
        (self.bin / 'omarchy-install-ai-t3-code').unlink()
        result = self.run_install(success=False)
        self.assertIn('omarchy-install-ai-t3-code is missing', result.stderr)
        self.assertIn('Update Omarchy', result.stderr)

    def test_the_apps_t3_is_checked_once_it_exists(self):
        self.seed_app_cli()
        self.run_install()
        self.reset_calls()
        other = self.bin / 't3'
        other.write_text('#!/bin/sh\necho t3 v0.0.0\n')
        other.chmod(0o755)
        self.env['PATH'] = f'{self.bin}:{self.home}/.local/bin'
        result = self.run_install(success=False)
        self.assertIn('T3 command shadowing', result.stderr)
        self.assertFalse(any(call[0] in ('stow', 'hyprctl') for call in self.calls()))
        self.assertTrue(other.is_file())

    def test_a_t3_that_does_not_report_a_version_stops(self):
        self.seed_app_cli()
        self.app_cli.write_text('#!/bin/sh\nexit 1\n')
        result = self.run_install(success=False)
        self.assertIn('did not report a version', result.stderr)

    def test_a_t3_of_the_users_own_is_left_alone(self):
        self.link.parent.mkdir(parents=True)
        self.link.write_text('#!/bin/sh\necho mine\n')
        self.link.chmod(0o755)
        result = self.run_install()
        self.assertEqual(self.installer_calls(), [INSTALLER])
        self.assertEqual(self.link.read_text(), '#!/bin/sh\necho mine\n')
        self.assertIn('is not T3 Code\'s t3', result.stdout)

    # The check on kernels with pages larger than 4 KiB.

    def test_large_pages_check_the_new_install_and_keep_a_copy(self):
        self.large_pages()
        result = self.run_install()
        self.assertIn(f'T3 Code {VERSION} passed; kept a copy for rollback.', result.stdout)
        self.assertEqual(json.loads((self.state / 't3/last-good.json').read_text())['version'], VERSION)
        self.assertEqual((self.state / 't3/T3-Code.AppImage').read_bytes(), self.appimage.read_bytes())
        self.assertFalse((self.state / 't3-rejected.json').exists())

    def test_large_pages_a_failing_new_install_finishes_setup_then_fails(self):
        self.large_pages(crash=True)
        result = self.run_install(success=False)
        self.assertIn(f'T3 Code {VERSION} fails on 16 KiB pages: t3code --version crashed (SIGSEGV)', result.stdout)
        self.assertIn('T3 Code did not pass its check on this kernel', result.stderr)
        # No older build could be downloaded here, so nothing replaced it.
        self.assertIn('no older build could be downloaded', result.stderr)
        self.assertTrue(any(call[0] == 'stow' for call in self.calls()))
        rejected = json.loads((self.state / 't3-rejected.json').read_text())
        self.assertEqual(rejected['16384'], [VERSION])
        self.assertEqual(rejected['reasons']['16384'][VERSION], 'smoke-crash')

    def test_large_pages_without_bubblewrap_report_it_at_the_end(self):
        self.env.update(FIXTURE_PAGESIZE='16384', FIXTURE_LOAD_ALIGN='0x10000')
        result = self.run_install(success=False)
        self.assertIn('bubblewrap is missing', result.stderr)
        self.assertTrue(any(call[0] == 'stow' for call in self.calls()))

    # The t3-cli offer (decision 0014).

    def test_bun_cli_is_kept_and_only_reported(self):
        build, kept = self.seed_cli()
        result = self.run_install()
        self.assertEqual(self.installer_calls(), [])
        self.assertEqual(os.readlink(self.link), str(build))
        self.assertIn('(migration t3-cli)', result.stdout)
        self.assertIn('./install.sh --migrate-to-omarchy-installers', result.stdout)
        result = self.run_update()
        self.assertIn('(migration t3-cli)', result.stdout)
        self.assertEqual(os.readlink(self.link), str(build))
        self.assertEqual(self.installer_calls(), [])
        self.assert_kept(kept)

    def test_no_offer_without_omarchys_installer(self):
        self.seed_cli()
        (self.bin / 'omarchy-install-ai-t3-code').unlink()
        result = self.run_install()
        self.assertNotIn('t3-cli', result.stdout)

    def test_migration_without_the_app_installs_it_then_moves_the_link(self):
        build, kept = self.seed_cli()
        result = self.run_install('--migrate-to-omarchy-installers')
        self.assertEqual(self.installer_calls(), [INSTALLER])
        self.assertEqual(os.readlink(self.link), str(self.app_cli))
        self.assertIn(f't3 now runs T3 Code\'s bundled CLI ({self.link} -> {self.app_cli}).', result.stdout)
        self.assertIn(f'{self.home}/.t3/runtime/versions are safe to delete', result.stdout)
        self.assertIn(f'ln -sfn {build} {self.link}', result.stdout)
        self.assert_kept(kept)
        self.reset_calls()
        result = self.run_install()
        self.assertNotIn('t3-cli', result.stdout)
        self.assertEqual(self.installer_calls(), [])

    def test_migration_with_the_app_installed_only_moves_the_link(self):
        build, kept = self.seed_cli()
        self.seed_app()
        # Omarchy's installer left the bun CLI's link alone.
        self.assertEqual(os.readlink(self.link), str(build))
        self.run_install('--migrate-to-omarchy-installers')
        self.assertEqual(self.installer_calls(), [])
        self.assertEqual(os.readlink(self.link), str(self.app_cli))
        self.assert_kept(kept)

    def test_migration_is_refused_while_the_cli_or_the_app_runs(self):
        build, kept = self.seed_cli()
        for pid, exe in ((40, str(build)), (41, '/tmp/.mount_T3-CodAbCdEf/t3code')):
            with self.subTest(exe=exe):
                self.process(pid, exe)
                self.reset_calls()
                result = self.run_install('--migrate-to-omarchy-installers', success=False)
                self.assertIn('T3 Code is running', result.stderr)
                self.assertIn('Nothing was changed', result.stderr)
                self.assertEqual(os.readlink(self.link), str(build))
                self.assertEqual(self.installer_calls(), [])
                self.assertFalse(self.appimage.exists())
                # The rest of setup still ran.
                self.assertTrue(any(call[0] == 'stow' for call in self.calls()))
                self.assert_kept(kept)
                for name in ('exe', 'cmdline'):
                    (self.root / 'proc' / str(pid) / name).unlink()
                (self.root / 'proc' / str(pid)).rmdir()

    def test_a_failed_check_keeps_the_cli_link(self):
        build, kept = self.seed_cli()
        self.large_pages(crash=True)
        result = self.run_install('--migrate-to-omarchy-installers', success=False)
        self.assertEqual(self.installer_calls(), [INSTALLER])
        self.assertEqual(os.readlink(self.link), str(build))
        self.assertIn(f'The bun CLI link {self.link} -> {build} was kept.', result.stderr)
        self.assert_kept(kept)

    def test_large_pages_migration_checks_the_build_before_moving_the_link(self):
        _, kept = self.seed_cli()
        self.large_pages()
        result = self.run_install('--migrate-to-omarchy-installers')
        self.assertIn(f'T3 Code {VERSION} passed', result.stdout)
        self.assertEqual(os.readlink(self.link), str(self.app_cli))
        self.assert_kept(kept)

    def test_terminal_no_is_remembered_and_the_flag_overrides_it(self):
        build, kept = self.seed_cli()
        output = self.run_in_terminal(['n'])
        self.assertIn('Migrate T3 Code now? [y/N]', output)
        self.assertIn('t3-cli', self.saved()['declined_migrations'])
        self.assertEqual(os.readlink(self.link), str(build))
        output = self.run_in_terminal([])
        self.assertIn('T3 Code stays on', output)
        self.run_install('--migrate-to-omarchy-installers')
        self.assertEqual(os.readlink(self.link), str(self.app_cli))
        self.assertNotIn('declined_migrations', self.saved())
        self.assert_kept(kept)


if __name__ == '__main__':
    unittest.main()
