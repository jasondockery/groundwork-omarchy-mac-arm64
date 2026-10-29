"""Brave through Omarchy's browser installer (decision 0015).

The installer fixture is the one in test_install_keyd. These tests do not run
pacman, flatpak, omarchy, or sudo on the host, and they do not launch a real
Brave. A passing fixture is not acceptance on the 16 KiB-page mini.
"""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

import test_install_keyd as base
import test_migration_offer as offers


REPO = base.REPO
EXTENSION_ID = 'nngceckbapebfimnlniiiahkandclblb'
INSTALL_BRAVE = ['omarchy-install-browser', 'brave']
REMOVE_BRAVE = ['omarchy-remove-browser', 'brave']
SET_DEFAULT = ['omarchy-default-browser', 'brave']

spec = importlib.util.spec_from_file_location(
    'groundwork_brave_updater', REPO / 'scripts/groundwork-update.py')
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


class BraveInstallTests(unittest.TestCase):
    setUp = base.InstallKeydTests.setUp
    calls = base.InstallKeydTests.calls
    run_install = offers.MigrationOfferTests.run_install
    run_update = offers.MigrationOfferTests.run_update
    run_in_terminal = offers.MigrationOfferTests.run_in_terminal
    reset_calls = offers.MigrationOfferTests.reset_calls
    saved = offers.MigrationOfferTests.saved

    def setUp(self):
        base.InstallKeydTests.setUp(self)
        self.page = os.sysconf('SC_PAGESIZE')
        self.env.update(
            FIXTURE_PAGESIZE=str(self.page),
            FIXTURE_LOAD_ALIGN='0x10000',
            FIXTURE_FEED='brave-bin',
        )
        for name in ('pacman', 'omarchy-install-browser', 'omarchy-remove-browser', 'omarchy-default-browser'):
            (self.bin / name).symlink_to(self.bin / 'stub')

    def state(self, name):
        return self.root / 'home/.local/state/groundwork' / name

    def remembered(self, name):
        path = self.state(name)
        if not path.exists():
            return []
        return json.loads(path.read_text()).get(str(self.page), [])

    def policy_file(self):
        return self.root / 'etc/brave/policies/managed/groundwork.json'

    def profile(self):
        directory = self.root / 'home/.config/BraveSoftware/Brave-Browser/Default'
        directory.mkdir(parents=True)
        marker = directory / 'Preferences'
        marker.write_text('{"fixture": "profile"}\n')
        return marker

    def flatpak_data(self):
        directory = self.root / 'home/.var/app/com.brave.Browser'
        directory.mkdir(parents=True)
        marker = directory / 'keep'
        marker.write_text('flatpak data\n')
        (self.root / 'brave-flatpak').touch()
        return marker

    def policy_writes(self):
        return [call for call in self.calls()
                if call[0] == 'sudo' and any('/etc/brave' in arg or arg.endswith('groundwork.json') for arg in call)]

    def launch_call(self):
        return next(call for call in self.calls() if call[0] == 'brave')

    def test_new_install_uses_omarchy_and_does_not_change_the_default(self):
        profile = self.profile()
        result = self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertIn(INSTALL_BRAVE, self.calls())
        self.assertNotIn(REMOVE_BRAVE, self.calls())
        self.assertNotIn(SET_DEFAULT, self.calls())
        self.assertNotIn('[y/N]', result.stdout + result.stderr)
        self.assertIn('migration default-browser-brave', result.stdout)
        self.assertIn('Brave is installed, and Chromium stays.', result.stdout)
        self.assertIn('not the default browser', result.stdout)
        self.assertFalse((self.root / 'default-browser').exists())
        self.assertEqual(self.remembered('brave-accepted.json'), ['1.73.91-1'])
        self.assertEqual(self.remembered('brave-rejected.json'), [])
        self.assertTrue((self.bin / 'brave').exists())
        self.assertTrue((self.bin / 'chromium').exists())
        self.assertEqual(profile.read_text(), '{"fixture": "profile"}\n')
        launch = self.launch_call()
        self.assertIn('--headless=new', launch)
        self.assertIn('--dump-dom', launch)
        self.assertIn('about:blank', launch)
        data_dir = next(arg for arg in launch if arg.startswith('--user-data-dir='))
        self.assertIn('groundwork-brave-launch-', data_dir)
        self.assertNotIn(str(self.root / 'home'), data_dir)
        policy = json.loads(self.policy_file().read_text())
        self.assertNotIn('ExtensionInstallForcelist', policy)
        self.assertNotIn('reason', policy)
        install = next(call for call in self.policy_writes() if call[1] == 'install' and '-T' in call)
        self.assertIn('-o', install)
        self.assertIn('root', install)
        self.assertIn('-m', install)
        self.assertIn('0644', install)
        self.assertTrue(any(call[1] == 'install' and '-d' in call and '0755' in call for call in self.policy_writes()))

    def test_unchanged_rerun_does_not_rewrite_policy_or_neighbor_files(self):
        self.run_install('--without-keyd', '--without-screen-sharing')
        managed = self.policy_file().parent
        (managed / 'omarchy.json').write_text('{"omarchy": true}\n')
        (managed / 'color.json').write_text('{"color": true}\n')
        self.reset_calls()
        self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertEqual(self.policy_writes(), [])
        self.assertEqual((managed / 'omarchy.json').read_text(), '{"omarchy": true}\n')
        self.assertEqual((managed / 'color.json').read_text(), '{"color": true}\n')
        self.assertNotIn(INSTALL_BRAVE, self.calls())

    def test_alignment_failure_removes_only_a_brave_this_run_added(self):
        profile = self.profile()
        self.env['FIXTURE_LOAD_ALIGN'] = '0x100'
        result = self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertIn(INSTALL_BRAVE, self.calls())
        self.assertLess(self.calls().index(INSTALL_BRAVE), self.calls().index(REMOVE_BRAVE))
        self.assertIn('not aligned', result.stderr)
        self.assertIn('Chromium stays the default.', result.stderr)
        self.assertIn('Removed the Brave browser this run had added.', result.stderr)
        self.assertIn('1.73.91-1', self.remembered('brave-rejected.json'))
        self.assertFalse((self.bin / 'brave').exists())
        self.assertFalse(self.policy_file().exists())
        self.assertFalse((self.root / 'default-browser').exists())
        self.assertTrue((self.bin / 'chromium').exists())
        self.assertEqual(profile.read_text(), '{"fixture": "profile"}\n')
        self.assertNotIn(SET_DEFAULT, self.calls())
        self.reset_calls()
        self.env['FIXTURE_LOAD_ALIGN'] = '0x10000'
        result = self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertNotIn(INSTALL_BRAVE, self.calls())
        self.assertIn('already failed', result.stderr)
        self.assertIn('was not installed again', result.stderr)
        self.assertNotIn('Brave is installed, and Chromium stays.', result.stdout)
        self.assertFalse((self.bin / 'brave').exists())

    def test_preinstalled_misaligned_brave_is_remembered_and_not_removed(self):
        subprocess.run([str(self.bin / 'omarchy-install-browser'), 'brave'], env=self.env, check=True, timeout=10)
        self.env['FIXTURE_LOAD_ALIGN'] = '0x100'
        self.reset_calls()
        result = self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertNotIn(INSTALL_BRAVE, self.calls())
        self.assertNotIn(REMOVE_BRAVE, self.calls())
        self.assertIn('not aligned', result.stderr)
        self.assertIn('1.73.91-1', self.remembered('brave-rejected.json'))
        self.assertTrue((self.bin / 'brave').exists())
        self.assertTrue((self.bin / 'chromium').exists())
        self.assertFalse((self.root / 'default-browser').exists())

    def test_launch_crash_is_remembered_like_a_bad_alignment(self):
        self.env['FIXTURE_BRAVE_LAUNCH_STATUS'] = '139'
        result = self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertIn('SIGSEGV', result.stderr)
        self.assertIn('brave/brave-browser#42312', result.stderr)
        self.assertIn(REMOVE_BRAVE, self.calls())
        self.assertIn('1.73.91-1', self.remembered('brave-rejected.json'))
        self.assertFalse((self.bin / 'brave').exists())
        self.assertFalse((self.root / 'default-browser').exists())
        self.assertTrue((self.bin / 'chromium').exists())

    def test_launch_exit_one_is_not_remembered_or_removed(self):
        self.env['FIXTURE_BRAVE_LAUNCH_STATUS'] = '1'
        result = self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertIn('exit 1', result.stderr)
        self.assertIn('Chromium stays the default.', result.stderr)
        self.assertNotIn(REMOVE_BRAVE, self.calls())
        self.assertNotIn(SET_DEFAULT, self.calls())
        self.assertEqual(self.remembered('brave-rejected.json'), [])
        self.assertEqual(self.remembered('brave-accepted.json'), [])
        self.assertTrue((self.bin / 'brave').exists())
        self.assertTrue(self.policy_file().is_file())
        self.assertNotIn('migration default-browser-brave', result.stdout)

    def test_yes_makes_brave_the_default_and_a_no_is_saved(self):
        output = self.run_in_terminal(['y'], '--without-keyd', '--without-screen-sharing')
        self.assertIn('Migrate Brave now? [y/N]', output)
        self.assertIn('Brave is the default browser.', output)
        self.assertIn('Web apps open in Brave', output)
        self.assertEqual((self.root / 'default-browser').read_text().strip(), 'brave')
        self.assertNotIn('default-browser-brave', self.saved().get('declined_migrations', ''))
        self.reset_calls()
        (self.root / 'default-browser').write_text('chromium\n')
        output = self.run_in_terminal(['n'], '--without-keyd', '--without-screen-sharing')
        self.assertIn('will not ask again', output)
        self.assertIn('default-browser-brave', self.saved()['declined_migrations'])
        self.assertNotIn(SET_DEFAULT, self.calls())
        self.assertEqual((self.root / 'default-browser').read_text().strip(), 'chromium')
        output = self.run_in_terminal(['y'], '--without-keyd', '--without-screen-sharing')
        self.assertNotIn('Migrate Brave now? [y/N]', output)
        self.assertIn('as you chose', output)

    def test_update_prints_the_default_notice_once_and_does_not_ask(self):
        self.run_install('--without-keyd', '--without-screen-sharing')
        self.reset_calls()
        result = self.run_update()
        self.assertEqual(result.stdout.count('migration default-browser-brave'), 1)
        self.assertNotIn('[y/N]', result.stdout + result.stderr)
        self.assertNotIn(SET_DEFAULT, self.calls())
        self.assertNotIn(INSTALL_BRAVE, self.calls())

    def test_a_saved_decline_is_quiet_on_update(self):
        self.run_in_terminal(['n'], '--without-keyd', '--without-screen-sharing')
        self.reset_calls()
        result = self.run_update()
        self.assertNotIn('default-browser-brave', result.stdout + result.stderr)
        self.assertNotIn(SET_DEFAULT, self.calls())

    def test_rejected_version_is_not_installed_when_migrating_a_flatpak(self):
        self.env['FIXTURE_LOAD_ALIGN'] = '0x100'
        self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertIn('1.73.91-1', self.remembered('brave-rejected.json'))
        marker = self.flatpak_data()
        self.env['FIXTURE_LOAD_ALIGN'] = '0x10000'
        self.reset_calls()
        result = self.run_install('--migrate-to-omarchy-installers', '--without-keyd', '--without-screen-sharing')
        self.assertNotIn(INSTALL_BRAVE, self.calls())
        self.assertNotIn(REMOVE_BRAVE, self.calls())
        self.assertIn('already failed', result.stderr)
        self.assertIn('was not installed again', result.stderr)
        self.assertNotIn('migration brave-flatpak', result.stdout)
        self.assertNotIn('[y/N]', result.stdout + result.stderr)
        self.assertTrue((self.root / 'brave-flatpak').exists())
        self.assertEqual(marker.read_text(), 'flatpak data\n')
        self.assertFalse((self.bin / 'brave').exists())
        self.assertTrue((self.bin / 'chromium').exists())

    def test_flatpak_is_offered_and_migration_keeps_its_data(self):
        marker = self.flatpak_data()
        result = self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertNotIn(INSTALL_BRAVE, self.calls())
        self.assertIn('migration brave-flatpak', result.stdout)
        self.assertIn('the user Flatpak com.brave.Browser', result.stdout)
        self.assertEqual(marker.read_text(), 'flatpak data\n')
        self.assertTrue((self.root / 'brave-flatpak').exists())
        self.reset_calls()
        result = self.run_install('--migrate-to-omarchy-installers', '--without-keyd', '--without-screen-sharing')
        self.assertIn(INSTALL_BRAVE, self.calls())
        self.assertNotIn(REMOVE_BRAVE, self.calls())
        self.assertFalse(any(call[0] == 'flatpak' and '--delete-data' in call for call in self.calls()))
        self.assertNotIn(['flatpak', '--user', 'uninstall', '--noninteractive', 'com.brave.Browser'], self.calls())
        self.assertTrue((self.root / 'brave-flatpak').exists())
        self.assertEqual(marker.read_text(), 'flatpak data\n')
        self.assertIn('were kept', result.stdout)
        self.assertIn('brave://settings/importData', result.stdout)
        self.assertEqual((self.root / 'default-browser').read_text().strip(), 'brave')
        self.assertTrue((self.bin / 'chromium').exists())

    def test_a_brave_command_another_package_owns_is_only_reported(self):
        command = self.bin / 'brave'
        command.symlink_to(self.bin / 'stub')
        (self.root / 'pacman-owned').write_text(f'{command} other-pkg\n')
        result = self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertNotIn(INSTALL_BRAVE, self.calls())
        self.assertNotIn(REMOVE_BRAVE, self.calls())
        self.assertIn('which brave-bin does not own', result.stdout)
        self.assertIn('migration brave-flatpak', result.stdout)
        self.assertTrue(command.is_symlink())

    def test_missing_installer_skips_brave(self):
        for name in ('omarchy-install-browser', 'omarchy-remove-browser', 'omarchy-default-browser'):
            (self.bin / name).unlink()
        result = self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertNotIn('Brave', result.stdout + result.stderr)
        self.assertFalse((self.bin / 'brave').exists())
        self.assertFalse(self.policy_file().exists())
        self.assertTrue((self.bin / 'chromium').exists())

    def test_installer_without_brave_support_skips_it(self):
        self.env['FIXTURE_BRAVE_INSTALLER_SUPPORTS'] = '0'
        result = self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertNotIn(INSTALL_BRAVE, self.calls())
        self.assertNotIn('migration brave-flatpak', result.stdout)
        self.assertNotIn('migration default-browser-brave', result.stdout)
        self.assertFalse((self.bin / 'brave').exists())

    def test_a_changed_version_is_checked_again(self):
        self.run_install('--without-keyd', '--without-screen-sharing')
        version = self.root / 'pacman-installed/brave-bin.version'
        version.write_text('1.74.0-1\n')
        self.reset_calls()
        self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertNotIn(INSTALL_BRAVE, self.calls())
        self.assertIn(['pacman', '-Qlq', 'brave-bin'], self.calls())
        self.assertIn(self.launch_call()[0], ('brave',))
        self.assertIn('1.74.0-1', self.remembered('brave-accepted.json'))
        self.assertNotIn('1.74.0-1', self.remembered('brave-rejected.json'))

    def test_a_changed_version_that_fails_is_not_removed(self):
        self.run_install('--without-keyd', '--without-screen-sharing')
        (self.root / 'pacman-installed/brave-bin.version').write_text('1.74.0-1\n')
        self.env['FIXTURE_LOAD_ALIGN'] = '0x100'
        self.reset_calls()
        result = self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertNotIn(REMOVE_BRAVE, self.calls())
        self.assertIn('not aligned', result.stderr)
        self.assertIn('1.74.0-1', self.remembered('brave-rejected.json'))
        self.assertTrue((self.bin / 'brave').exists())

    def test_update_does_not_install_brave_without_a_terminal_and_cached_sudo(self):
        for name in ('omarchy-install-browser', 'omarchy-remove-browser', 'omarchy-default-browser'):
            (self.bin / name).unlink()
        self.run_install('--without-keyd', '--without-screen-sharing')
        for name in ('omarchy-install-browser', 'omarchy-remove-browser', 'omarchy-default-browser'):
            (self.bin / name).symlink_to(self.bin / 'stub')
        self.reset_calls()
        self.env['FIXTURE_SUDO_CACHED'] = '0'
        result = subprocess.run(
            ['python3', str(REPO / 'scripts/groundwork-update.py'), 'setup', str(REPO), '--update'],
            env=self.env, capture_output=True, text=True, timeout=30, start_new_session=True)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('no terminal is available', result.stderr)
        self.assertNotIn(INSTALL_BRAVE, self.calls())
        self.assertFalse((self.bin / 'brave').exists())

    def test_policy_symlink_is_refused_and_neighbor_files_stay(self):
        self.run_install('--without-keyd', '--without-screen-sharing')
        managed = self.policy_file().parent
        (managed / 'omarchy.json').write_text('{"omarchy": true}\n')
        (managed / 'color.json').write_text('{"color": true}\n')
        self.policy_file().unlink()
        self.policy_file().symlink_to(managed / 'omarchy.json')
        self.reset_calls()
        result = self.run_install('--without-keyd', '--without-screen-sharing', success=False)
        self.assertIn('Refusing to write Brave policy through a symlink', result.stderr)
        self.assertTrue(self.policy_file().is_symlink())
        self.assertEqual((managed / 'omarchy.json').read_text(), '{"omarchy": true}\n')
        self.assertEqual((managed / 'color.json').read_text(), '{"color": true}\n')
        self.assertEqual(self.policy_writes(), [])

    def test_remove_failure_names_the_command_and_keeps_chromium(self):
        self.env.update(FIXTURE_LOAD_ALIGN='0x100', FIXTURE_BRAVE_REMOVE_STATUS='1')
        result = self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertIn(REMOVE_BRAVE, self.calls())
        self.assertIn('Could not remove the Brave browser this run added.', result.stderr)
        self.assertIn('omarchy-remove-browser brave', result.stderr)
        self.assertIn('1.73.91-1', self.remembered('brave-rejected.json'))
        self.assertTrue((self.bin / 'brave').exists())
        self.assertTrue((self.bin / 'chromium').exists())

    def test_no_elf_is_remembered_and_removed(self):
        # No ELF is a remembered alignment failure: nothing was shown to load.
        self.env['FIXTURE_BRAVE_NO_ELF'] = '1'
        result = self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertIn('installed no ELF file', result.stderr)
        self.assertIn(REMOVE_BRAVE, self.calls())
        self.assertIn('1.73.91-1', self.remembered('brave-rejected.json'))
        self.assertFalse((self.bin / 'brave').exists())
        self.assertTrue((self.bin / 'chromium').exists())


class BraveSpecTests(unittest.TestCase):
    def test_rendered_policy_matches_v070_and_differs_from_upstream(self):
        spec_path = REPO / 'core/browser/brave.json'
        document = json.loads(spec_path.read_text())
        self.assertEqual(document['schemaVersion'], 1)
        rendered = json.loads(updater.render_brave_policy())
        expected = {}
        for item in document['policies']:
            self.assertNotEqual(item['value'], item['upstreamDefault'], item['name'])
            expected[item['name']] = item['value']
        self.assertEqual(list(rendered), list(expected))
        self.assertEqual(rendered, expected)
        self.assertEqual(rendered['BraveAIChatEnabled'], False)
        self.assertEqual(rendered['BraveLocalAIEnabled'], False)
        self.assertNotIn('reason', rendered)
        self.assertNotIn('upstreamDefault', rendered)
        extension = json.loads(updater.render_brave_extension())
        self.assertEqual(extension, {
            'external_update_url': 'https://clients2.google.com/service/update2/crx',
        })
        self.assertEqual(updater.brave_offered_extension()['id'], EXTENSION_ID)
        offered = [item for item in document['extensions'] if item.get('install') == 'offer']
        self.assertEqual([item['id'] for item in offered], [EXTENSION_ID])

    def test_a_spec_that_offers_anything_but_bitwarden_is_refused(self):
        base_spec = {
            'schemaVersion': 1,
            'policies': [{'name': 'BraveAIChatEnabled', 'value': False}],
            'extensions': [{
                'name': 'Bitwarden',
                'id': EXTENSION_ID,
                'updateUrl': 'https://clients2.google.com/service/update2/crx',
                'install': 'offer',
            }],
        }
        other = json.loads(json.dumps(base_spec))
        other['extensions'].append({
            'name': 'Other', 'id': 'a' * 32, 'updateUrl': 'https://example.com/crx', 'install': 'offer',
        })
        with self.assertRaises(updater.UpdateError):
            updater.brave_offered_extension(other)
        renamed = json.loads(json.dumps(base_spec))
        renamed['extensions'][0]['id'] = 'b' * 32
        with self.assertRaises(updater.UpdateError):
            updater.brave_offered_extension(renamed)
        insecure = json.loads(json.dumps(base_spec))
        insecure['extensions'][0]['updateUrl'] = 'http://example.com/crx'
        with self.assertRaises(updater.UpdateError):
            updater.brave_offered_extension(insecure)

    def test_launch_outcomes_and_a_private_environment(self):
        with tempfile.TemporaryDirectory(prefix='groundwork-brave-unit-') as directory:
            root = Path(directory)
            binary = root / 'brave'
            seen = root / 'env'
            binary.write_text(textwrap.dedent(f"""\
                #!/bin/sh
                env > {seen}
                exit 0
                """))
            binary.chmod(0o755)
            saved = {key: os.environ.get(key) for key in (
                'BRAVE_SHOULD_NOT_LEAK', 'GROUNDWORK_TESTING', 'FIXTURE_BRAVE_LAUNCH_STATUS')}
            os.environ['BRAVE_SHOULD_NOT_LEAK'] = 'secret-value'
            os.environ['GROUNDWORK_TESTING'] = '1'
            os.environ['FIXTURE_BRAVE_LAUNCH_STATUS'] = '0'
            try:
                kind, message = updater.brave_launch_result(binary)
            finally:
                for key, value in saved.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            self.assertEqual((kind, message), ('ok', ''))
            dumped = seen.read_text()
            self.assertNotIn('secret-value', dumped)
            self.assertNotIn('BRAVE_SHOULD_NOT_LEAK', dumped)
            self.assertIn('PATH=/usr/bin:/bin', dumped)
            self.assertIn('FIXTURE_BRAVE_LAUNCH_STATUS=0', dumped)
            self.assertIn('groundwork-brave-launch-', dumped)
            binary.write_text('#!/bin/sh\nexit 139\n')
            kind, message = updater.brave_launch_result(binary)
            self.assertEqual(kind, 'remember')
            self.assertIn('SIGSEGV', message)
            binary.write_text('#!/bin/sh\nexit 1\n')
            kind, message = updater.brave_launch_result(binary)
            self.assertEqual(kind, 'transient')
            self.assertIn('exit 1', message)
            missing = root / 'missing'
            kind, _message = updater.brave_launch_result(missing)
            self.assertEqual(kind, 'transient')
            binary.write_text('#!/bin/sh\nexit 0\n')
            binary.chmod(stat.S_IRUSR | stat.S_IWUSR)
            kind, message = updater.brave_launch_result(binary)
            self.assertEqual(kind, 'transient')
            self.assertIn('not executable', message)

    def test_launch_timeout_is_not_a_remembered_crash(self):
        with tempfile.TemporaryDirectory(prefix='groundwork-brave-timeout-') as directory:
            binary = Path(directory) / 'brave'
            binary.write_text('#!/bin/sh\nsleep 30\n')
            binary.chmod(0o755)
            with patch.object(updater, 'BRAVE_LAUNCH_SECONDS', 1):
                kind, message = updater.brave_launch_result(binary)
            self.assertEqual(kind, 'transient')
            self.assertIn('within 1 seconds', message)


if __name__ == '__main__':
    unittest.main()
