"""Brave through Omarchy's browser installer (decision 0015).

The installer fixture is the one in test_install_keyd. These tests do not run
pacman, flatpak, omarchy, or sudo on the host, and they do not launch a real
Brave. A passing fixture is not acceptance on the 16 KiB-page mini.
"""
from contextlib import redirect_stderr
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

import test_install_keyd as base
import test_migration_offer as offers


REPO = base.REPO
EXTENSION_ID = 'nngceckbapebfimnlniiiahkandclblb'
INSTALL_BRAVE = ['omarchy-install-browser', 'brave']
REMOVE_BRAVE = ['omarchy-remove-browser', 'brave']
SET_DEFAULT = ['omarchy-default-browser', 'brave']
OMARCHY_SET_DEFAULT = ['omarchy', 'default', 'browser', 'brave']
OMARCHY_ANNOUNCEMENT = 'Make it the default via Setup > Defaults > Browser.'

spec = importlib.util.spec_from_file_location(
    'groundwork_brave_updater', REPO / 'scripts/groundwork-update.py')
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


def wayland_socket(path):
    """A Unix socket file at `path`, like a compositor's wayland-N."""
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(path))
    finally:
        listener.close()
    return path


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
        if self.page > 4096:
            # On a large-page host, setup pins T3 Code; the fixture installer
            # accepts only that pin.
            self.env['FIXTURE_PINNED_T3'] = updater.T3_KNOWN_GOOD_LARGE_PAGE
        for name in ('pacman', 'omarchy-install-browser', 'omarchy-remove-browser', 'omarchy-default-browser'):
            (self.bin / name).symlink_to(self.bin / 'stub')
        # The launch check opens a window, so it needs this user's Wayland
        # socket. A bound, closed socket file is enough for the fixture.
        wayland_socket(self.root / 'runtime' / 'wayland-fixture')
        self.env.update(WAYLAND_DISPLAY='wayland-fixture', FIXTURE_BRAVE_SETTLE_SECONDS='0')

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

    def accept_once_then_restore_chromium(self):
        """A first run that accepts Brave and makes it the default, then a user who switched back."""
        self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertEqual((self.root / 'default-browser').read_text().strip(), 'brave')
        (self.root / 'default-browser').write_text('chromium\n')
        self.reset_calls()

    def test_new_install_uses_omarchy_and_makes_a_working_brave_the_default(self):
        profile = self.profile()
        result = self.run_install('--without-keyd', '--without-screen-sharing')
        text = result.stdout + result.stderr
        self.assertIn(INSTALL_BRAVE, self.calls())
        self.assertNotIn(REMOVE_BRAVE, self.calls())
        self.assertIn(OMARCHY_SET_DEFAULT, self.calls())
        self.assertLess(self.calls().index(self.launch_call()), self.calls().index(OMARCHY_SET_DEFAULT))
        self.assertNotIn('[y/N]', text)
        self.assertNotIn('migration default-browser-brave', text)
        # Omarchy's own "make it the default yourself" line is dropped; Groundwork says what it did.
        self.assertNotIn(OMARCHY_ANNOUNCEMENT, text)
        self.assertIn('Installing Brave...', result.stdout)
        self.assertEqual(text.count('Brave is the default browser.'), 1)
        self.assertIn('omarchy default browser chromium', result.stdout)
        self.assertIn('Brave is installed, and Chromium stays.', result.stdout)
        self.assertIn('registered for Chromium and Brave', result.stdout)
        self.assertNotIn('Restart Chromium,', result.stdout)
        self.assertIn('a Brave window with a throwaway profile opens', result.stdout)
        self.assertEqual((self.root / 'default-browser').read_text().strip(), 'brave')
        self.assertEqual(self.remembered('brave-accepted.json'), ['1.73.91-1'])
        self.assertEqual(self.remembered('brave-rejected.json'), [])
        self.assertTrue((self.bin / 'brave').exists())
        self.assertTrue((self.bin / 'chromium').exists())
        self.assertEqual(profile.read_text(), '{"fixture": "profile"}\n')
        launch = self.launch_call()
        # The real windowed path: Wayland and the GPU, not a headless dump.
        self.assertNotIn('--headless=new', launch)
        self.assertNotIn('--disable-gpu', launch)
        self.assertNotIn('--dump-dom', launch)
        self.assertIn('--ozone-platform=wayland', launch)
        self.assertIn('--remote-debugging-pipe', launch)
        self.assertFalse(any(arg.startswith('--remote-debugging-port') for arg in launch))
        self.assertIn('--password-store=basic', launch)
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
        # One line says what happened, what was remembered and removed, and the default.
        line = next(line for line in result.stderr.splitlines() if 'SIGSEGV' in line)
        self.assertIn('Remembered brave-bin 1.73.91-1', line)
        self.assertIn('Removed the Brave browser this run had added.', line)
        self.assertTrue(line.endswith('Chromium stays the default.'), line)
        self.assertEqual((result.stdout + result.stderr).count('Chromium stays the default.'), 1)
        self.assertNotIn(OMARCHY_ANNOUNCEMENT, result.stdout + result.stderr)
        self.assertIn('registered for Chromium.', result.stdout)
        self.assertIn(REMOVE_BRAVE, self.calls())
        self.assertNotIn(OMARCHY_SET_DEFAULT, self.calls())
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

    def test_no_desktop_session_is_retried_and_keeps_chromium(self):
        self.env.pop('WAYLAND_DISPLAY')
        result = self.run_install('--without-keyd', '--without-screen-sharing')
        text = result.stdout + result.stderr
        self.assertIn('no Wayland desktop session', result.stderr)
        self.assertEqual(text.count('Chromium stays the default.'), 1)
        self.assertNotIn(OMARCHY_ANNOUNCEMENT, text)
        self.assertFalse(any(call[0] == 'brave' for call in self.calls()))
        self.assertNotIn(REMOVE_BRAVE, self.calls())
        self.assertNotIn(OMARCHY_SET_DEFAULT, self.calls())
        self.assertEqual(self.remembered('brave-rejected.json'), [])
        self.assertEqual(self.remembered('brave-accepted.json'), [])
        self.assertTrue((self.bin / 'brave').exists())
        # The next run from the desktop checks it and makes it the default.
        self.env['WAYLAND_DISPLAY'] = 'wayland-fixture'
        self.reset_calls()
        result = self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertIn(OMARCHY_SET_DEFAULT, self.calls())
        self.assertEqual((self.root / 'default-browser').read_text().strip(), 'brave')

    def test_a_saved_decline_stops_the_first_automatic_switch(self):
        # A transient first check leaves Brave installed and unaccepted, and saves choices.
        self.env['FIXTURE_BRAVE_LAUNCH_STATUS'] = '1'
        self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertEqual(self.remembered('brave-accepted.json'), [])
        config = self.root / 'home/.config/groundwork/omarchy.json'
        saved = json.loads(config.read_text())
        saved['declined_migrations'] = ['default-browser-brave']
        config.write_text(json.dumps(saved) + '\n')
        self.env['FIXTURE_BRAVE_LAUNCH_STATUS'] = '0'
        self.reset_calls()
        result = self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertNotIn(OMARCHY_SET_DEFAULT, self.calls())
        self.assertNotIn(SET_DEFAULT, self.calls())
        self.assertIn('as you chose', result.stdout)
        self.assertNotIn('Brave is the default browser.', result.stdout)
        self.assertFalse((self.root / 'default-browser').exists())
        # The first acceptance is still recorded after the default step.
        self.assertEqual(self.remembered('brave-accepted.json'), ['1.73.91-1'])

    def test_an_unset_default_is_switched_once(self):
        # No default set counts as one the person did not choose (core v0.8.0).
        self.env['FIXTURE_DEFAULT_BROWSER'] = ''
        result = self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertIn(OMARCHY_SET_DEFAULT, self.calls())
        self.assertNotIn('migration default-browser-brave', result.stdout)
        self.assertEqual(result.stdout.count('Brave is the default browser.'), 1)
        self.assertEqual((self.root / 'default-browser').read_text().strip(), 'brave')
        # Once only: a default the person clears later is not switched again.
        (self.root / 'default-browser').unlink()
        self.reset_calls()
        result = self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertNotIn(OMARCHY_SET_DEFAULT, self.calls())
        self.assertIn('migration default-browser-brave', result.stdout)
        self.assertIn('the default is nothing', result.stdout)

    def test_a_default_other_than_chromium_is_only_offered(self):
        self.env['FIXTURE_DEFAULT_BROWSER'] = 'firefox'
        result = self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertNotIn(OMARCHY_SET_DEFAULT, self.calls())
        self.assertNotIn(SET_DEFAULT, self.calls())
        self.assertIn('migration default-browser-brave', result.stdout)
        self.assertIn('the default is firefox', result.stdout)
        self.assertFalse((self.root / 'default-browser').exists())

    def test_a_failed_default_change_is_one_line_and_setup_continues(self):
        self.env['FIXTURE_DEFAULT_BROWSER_STATUS'] = '1'
        result = self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertIn(OMARCHY_SET_DEFAULT, self.calls())
        self.assertIn('omarchy default browser brave failed, so the default browser was not changed. '
                      'Chromium stays the default.', result.stderr)
        self.assertNotIn('Brave is the default browser.', result.stdout)

    def test_yes_makes_brave_the_default_and_a_no_is_saved(self):
        self.accept_once_then_restore_chromium()
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
        # A default the user changed after Groundwork set Brave is left alone.
        self.accept_once_then_restore_chromium()
        result = self.run_update()
        self.assertEqual(result.stdout.count('migration default-browser-brave'), 1)
        self.assertNotIn('[y/N]', result.stdout + result.stderr)
        self.assertNotIn(SET_DEFAULT, self.calls())
        self.assertNotIn(OMARCHY_SET_DEFAULT, self.calls())
        self.assertNotIn(INSTALL_BRAVE, self.calls())
        self.assertEqual((self.root / 'default-browser').read_text().strip(), 'chromium')

    def test_a_saved_decline_is_quiet_on_update(self):
        self.accept_once_then_restore_chromium()
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

    def test_a_failed_recheck_does_not_say_chromium_stays_the_default(self):
        self.run_install('--without-keyd', '--without-screen-sharing')
        (self.root / 'default-browser').write_text('brave\n')
        (self.root / 'pacman-installed/brave-bin.version').write_text('1.74.0-1\n')
        self.env['FIXTURE_LOAD_ALIGN'] = '0x100'
        self.reset_calls()
        result = self.run_install('--without-keyd', '--without-screen-sharing')
        text = result.stdout + result.stderr
        self.assertIn('Brave stays the default.', text)
        self.assertIn('omarchy default browser chromium', text)
        self.assertNotIn('Chromium stays the default.', text)
        self.assertNotIn(REMOVE_BRAVE, self.calls())
        self.assertEqual((self.root / 'default-browser').read_text().strip(), 'brave')
        self.assertTrue((self.bin / 'brave').exists())

    def test_ctrl_c_during_launch_stops_setup(self):
        self.env['FIXTURE_BRAVE_LAUNCH_STATUS'] = 'sleep'
        out = tempfile.TemporaryFile(mode='w+')
        err = tempfile.TemporaryFile(mode='w+')
        process = subprocess.Popen(
            ['bash', str(REPO / 'install.sh'), '--without-keyd', '--without-screen-sharing'],
            env=self.env, stdout=out, stderr=err, text=True, start_new_session=True)
        checker = None
        try:
            deadline = time.monotonic() + 40
            needle = str(self.bin / 'brave').encode()
            while time.monotonic() < deadline and process.poll() is None:
                for entry in Path('/proc').iterdir():
                    if not entry.name.isdigit():
                        continue
                    try:
                        raw = (entry / 'cmdline').read_bytes()
                    except OSError:
                        continue
                    if b'check-brave-launch' in raw and needle in raw:
                        checker = int(entry.name)
                        break
                if checker is not None:
                    break
                time.sleep(0.05)
            self.assertIsNotNone(checker, 'launch check did not start')
            os.kill(checker, signal.SIGINT)
            process.wait(timeout=30)
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=10)
            root_bytes = str(self.root).encode()
            for entry in Path('/proc').iterdir():
                if not entry.name.isdigit() or int(entry.name) == os.getpid():
                    continue
                try:
                    raw = (entry / 'cmdline').read_bytes()
                except OSError:
                    continue
                if root_bytes in raw:
                    try:
                        os.kill(int(entry.name), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            out.seek(0)
            err.seek(0)
            stdout, stderr = out.read(), err.read()
            out.close()
            err.close()
        self.assertEqual(process.returncode, 130, stdout + stderr)
        self.assertIn('Update cancelled.', stderr)
        self.assertNotIn('Remembered', stderr)
        self.assertNotIn('Chromium stays the default.', stdout + stderr)
        self.assertEqual(self.remembered('brave-rejected.json'), [])
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

    def test_update_retries_a_transient_launch_without_sudo(self):
        self.env['FIXTURE_BRAVE_LAUNCH_STATUS'] = '1'
        self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertTrue(self.policy_file().is_file())
        self.assertEqual(self.remembered('brave-accepted.json'), [])
        self.assertEqual(self.remembered('brave-rejected.json'), [])
        before = self.policy_file().read_bytes()
        self.reset_calls()
        self.env['FIXTURE_SUDO_CACHED'] = '0'
        result = subprocess.run(
            ['python3', str(REPO / 'scripts/groundwork-update.py'), 'setup', str(REPO), '--update'],
            env=self.env, capture_output=True, text=True, timeout=30, start_new_session=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('no terminal is available', result.stdout + result.stderr)
        self.assertNotIn('Nothing was changed.', result.stdout + result.stderr)
        self.assertNotIn(INSTALL_BRAVE, self.calls())
        self.assertIn('--remote-debugging-pipe', self.launch_call())
        self.assertEqual(self.policy_file().read_bytes(), before)
        self.assertEqual(self.remembered('brave-accepted.json'), [])
        self.assertEqual(self.remembered('brave-rejected.json'), [])

    def test_matching_policy_with_the_wrong_owner_is_chowned(self):
        self.run_install('--without-keyd', '--without-screen-sharing')
        before = self.policy_file().read_bytes()
        owners = json.loads((self.root / 'ownership.json').read_text())
        for path in (self.root / 'etc/brave', self.root / 'etc/brave/policies',
                     self.policy_file().parent, self.policy_file()):
            self.assertEqual(owners[str(path)], 'root:root', path)
        stat = subprocess.run([str(self.bin / 'stat'), '-c', '%U:%G', str(self.policy_file())],
                              env=self.env, capture_output=True, text=True, check=True)
        self.assertEqual(stat.stdout.strip(), 'root:root')
        (self.root / 'ownership.json').unlink()
        self.reset_calls()
        self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertEqual(self.policy_file().read_bytes(), before)
        chowns = [call for call in self.calls() if call[:3] == ['sudo', 'chown', 'root:root']]
        self.assertEqual(len(chowns), 1)
        self.assertIn(str(self.policy_file()), chowns[0])
        self.assertFalse(any(call[0] == 'sudo' and 'install' in call and '-T' in call for call in self.calls()))
        owners = json.loads((self.root / 'ownership.json').read_text())
        self.assertEqual(owners[str(self.policy_file())], 'root:root')

    def test_invalid_brave_memory_stops_before_install(self):
        for name, payload in (('brave-rejected.json', '{'), ('brave-accepted.json', '[]\n')):
            with self.subTest(name=name):
                path = self.state(name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(payload)
                result = self.run_install('--without-keyd', '--without-screen-sharing', success=False)
                self.assertIn(str(path), result.stderr)
                self.assertIn('before installing Brave', result.stderr)
                self.assertNotIn(INSTALL_BRAVE, self.calls())
                self.assertFalse((self.bin / 'brave').exists())
                path.unlink()
                self.reset_calls()

    def test_a_declined_default_survives_a_policy_symlink_refusal(self):
        self.accept_once_then_restore_chromium()
        target = self.root / 'policy-target'
        target.mkdir()
        brave = self.root / 'etc/brave'
        shutil.rmtree(brave)
        brave.symlink_to(target)
        output = self.run_in_terminal(['n'], '--without-keyd', '--without-screen-sharing', success=False)
        self.assertIn('Refusing to write Brave policy through a symlink', output)
        self.assertIn('default-browser-brave', self.saved()['declined_migrations'])
        self.assertTrue(brave.is_symlink())
        self.assertEqual(brave.resolve(), target.resolve())
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(self.policy_writes(), [])

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
    def test_brave_memory_exits_2_when_the_file_is_not_the_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with patch.object(updater, 'STATE', state), \
                    patch.object(updater, 'brave_version', return_value='1.2.3'), \
                    patch.object(updater, 'page_size', return_value=16384), \
                    patch('sys.stderr', new=io.StringIO()):
                self.assertEqual(updater.main(['brave-rejected']), 1)
                self.assertEqual(updater.main(['brave-accepted']), 1)
                (state / 'brave-rejected.json').write_text('{')
                self.assertEqual(updater.main(['brave-rejected']), 2)
                accepted = state / 'brave-accepted.json'
                accepted.write_text('[]\n')
                self.assertEqual(updater.main(['brave-accepted']), 2)
                accepted.write_text('{"16384": ["1.2.3"]}\n')
                self.assertEqual(updater.main(['brave-accepted']), 0)
                rejected = state / 'brave-rejected.json'
                rejected.unlink()
                rejected.symlink_to(accepted)
                self.assertEqual(updater.main(['brave-rejected']), 2)

    def test_rendered_policy_matches_the_core_spec_and_differs_from_upstream(self):
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

    FAKE_BRAVE = textwrap.dedent("""\
        #!/usr/bin/env python3
        # A fake brave that speaks the DevTools pipe. MODE picks what it does.
        import json, os, resource, signal, sys, time
        MODE = {mode!r}
        SEEN = {seen!r}
        soft, hard = resource.getrlimit(resource.RLIMIT_CORE)
        with open(SEEN, 'w') as handle:
            for key, value in os.environ.items():
                handle.write(f'ENV {{key}}={{value}}\\n')
            handle.write(f'RLIMIT_CORE={{soft}},{{hard}}\\n')
            handle.write('FDS ' + ' '.join(sorted(os.listdir('/proc/self/fd'), key=int)) + '\\n')
            for arg in sys.argv[1:]:
                handle.write(f'ARG {{arg}}\\n')
        def die(number):
            if number != signal.SIGKILL:
                signal.signal(number, signal.SIG_DFL)
            os.kill(os.getpid(), number)
            time.sleep(5)
        if MODE == 'crash-open':
            die(signal.SIGSEGV)
        if MODE == 'exit-1':
            sys.exit(1)
        if MODE == 'silent':
            time.sleep(60)
        buffer = b''
        while True:
            chunk = os.read(3, 65536)
            if not chunk:
                sys.exit(0)
            buffer += chunk
            while b'\\0' in buffer:
                raw, buffer = buffer.split(b'\\0', 1)
                message = json.loads(raw)
                if message['method'] == 'Browser.close':
                    if MODE == 'crash-close':
                        die(signal.SIGABRT)
                    if MODE == 'hang-close':
                        time.sleep(60)
                    if MODE == 'close-exit-3':
                        sys.exit(3)
                    os.write(4, json.dumps({{'id': message['id'], 'result': {{}}}}).encode() + b'\\0')
                    sys.exit(0)
                os.write(4, json.dumps({{'id': message['id'], 'result': {{'product': 'Fake/1'}}}}).encode() + b'\\0')
                if MODE == 'crash-window':
                    die(signal.SIGTRAP)
                if MODE == 'killed-window':
                    die(signal.SIGKILL)
                if MODE == 'terminated-window':
                    die(signal.SIGTERM)
        """)

    def launch(self, mode, **constants):
        """Run brave_launch_result against a fake brave in a fake desktop session."""
        directory = Path(tempfile.mkdtemp(prefix='groundwork-brave-unit-'))
        self.addCleanup(shutil.rmtree, directory, True)
        runtime = directory / 'runtime'
        runtime.mkdir()
        wayland_socket(runtime / 'wayland-unit')
        binary = directory / 'brave'
        seen = directory / 'seen'
        binary.write_text(self.FAKE_BRAVE.format(mode=mode, seen=str(seen)))
        binary.chmod(0o755)
        # A descriptor the caller could leak, and caller variables that must not pass.
        pipe_read, leak_write = os.pipe()
        # Above 4, so it cannot be mistaken for the DevTools pipe.
        leak_read = fcntl.fcntl(pipe_read, fcntl.F_DUPFD, 20)
        os.close(pipe_read)
        os.set_inheritable(leak_read, True)
        self.addCleanup(os.close, leak_read)
        self.addCleanup(os.close, leak_write)
        environment = {
            'WAYLAND_DISPLAY': 'wayland-unit',
            'XDG_RUNTIME_DIR': str(runtime),
            'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/should/not/leak',
            'DISPLAY': ':99',
            'BRAVE_SHOULD_NOT_LEAK': 'secret-value',
        }
        settings = dict(BRAVE_SETTLE_SECONDS=0.2, **constants)
        with patch.dict(os.environ, environment), \
                patch.multiple(updater, **settings):
            os.environ.pop('GROUNDWORK_TESTING', None)
            result = updater.brave_launch_result(binary)
        self.assertFalse(any(Path(tempfile.gettempdir()).glob('groundwork-brave-launch-*/profile/fake-marker')))
        record = seen.read_text() if seen.exists() else ''
        return result, record, runtime / 'wayland-unit', leak_read

    def test_launch_opens_a_private_window_on_the_session_display(self):
        (kind, message), record, socket_path, leak = self.launch('ok')
        self.assertEqual((kind, message), ('ok', ''))
        env = dict(line[4:].split('=', 1) for line in record.splitlines() if line.startswith('ENV '))
        args = [line[4:] for line in record.splitlines() if line.startswith('ARG ')]
        fds = next(line for line in record.splitlines() if line.startswith('FDS ')).split()[1:]
        self.assertEqual(env['WAYLAND_DISPLAY'], str(socket_path))
        self.assertNotEqual(env['XDG_RUNTIME_DIR'], str(socket_path.parent))
        self.assertIn('groundwork-brave-launch-', env['XDG_RUNTIME_DIR'])
        self.assertIn('groundwork-brave-launch-', env['HOME'])
        self.assertEqual(env['PATH'], '/usr/bin:/bin')
        for key in ('DBUS_SESSION_BUS_ADDRESS', 'DISPLAY', 'BRAVE_SHOULD_NOT_LEAK', 'GROUNDWORK_TESTING'):
            self.assertNotIn(key, env)
        self.assertNotIn('secret-value', record)
        self.assertIn('RLIMIT_CORE=1,1', record)
        # stdin/stdout/stderr, the DevTools pipe on 3 and 4, and the fake's own
        # record file and directory listing. Nothing else from the caller.
        self.assertIn('3', fds)
        self.assertIn('4', fds)
        self.assertNotIn(str(leak), fds)
        self.assertLessEqual(len(fds), 7, fds)
        for flag in ('--ozone-platform=wayland', '--remote-debugging-pipe', '--password-store=basic',
                     '--no-first-run', 'about:blank'):
            self.assertIn(flag, args)
        for flag in ('--headless=new', '--disable-gpu', '--dump-dom'):
            self.assertNotIn(flag, args)
        self.assertFalse(any(arg.startswith('--remote-debugging-port') for arg in args))
        data_dir = next(arg for arg in args if arg.startswith('--user-data-dir='))
        self.assertIn('groundwork-brave-launch-', data_dir)
        self.assertFalse(Path(data_dir.split('=', 1)[1]).exists())

    def test_launch_signals_are_remembered_crashes(self):
        for mode, name, when in (('crash-open', 'SIGSEGV', 'while opening a window'),
                                 ('crash-window', 'SIGTRAP', 'with its window open'),
                                 ('crash-close', 'SIGABRT', 'while closing its window')):
            with self.subTest(mode=mode):
                (kind, message), _record, _socket, _leak = self.launch(mode)
                self.assertEqual(kind, 'remember', message)
                self.assertIn(name, message)
                self.assertIn(when, message)

    def test_a_signal_from_outside_brave_is_retried_not_remembered(self):
        # SIGKILL from the OOM killer or SIGTERM at logout says nothing about the build.
        for mode, name in (('killed-window', 'SIGKILL'), ('terminated-window', 'SIGTERM')):
            with self.subTest(mode=mode):
                (kind, message), _record, _socket, _leak = self.launch(mode)
                self.assertEqual(kind, 'transient', message)
                self.assertIn(name, message)
                self.assertIn('tried again next run', message)
        for code, kind in ((-9, 'transient'), (137, 'transient'), (143, 'transient'), (-15, 'transient'),
                           (-11, 'remember'), (133, 'remember'), (134, 'remember'), (-7, 'remember'),
                           (-4, 'remember'), (136, 'remember')):
            with self.subTest(code=code):
                self.assertEqual(updater.brave_exit_result(code, 16384, 'while testing')[0], kind)

    def test_launch_failures_that_are_not_signals_are_retried(self):
        cases = (
            ('exit-1', {}, 'exit 1'),
            ('close-exit-3', {}, 'exit 3'),
            ('silent', {'BRAVE_LAUNCH_SECONDS': 1}, 'within 1 seconds'),
            ('hang-close', {'BRAVE_CLOSE_SECONDS': 1}, 'did not close its test window within 1 seconds'),
        )
        for mode, constants, text in cases:
            with self.subTest(mode=mode):
                (kind, message), _record, _socket, _leak = self.launch(mode, **constants)
                self.assertEqual(kind, 'transient', message)
                self.assertIn(text, message)

    def test_launch_needs_this_users_wayland_socket(self):
        with tempfile.TemporaryDirectory(prefix='groundwork-brave-unit-') as directory:
            root = Path(directory)
            binary = root / 'brave'
            binary.write_text('#!/bin/sh\nexit 0\n')
            binary.chmod(0o755)
            (root / 'not-a-socket').write_text('')
            for environment in ({}, {'WAYLAND_DISPLAY': 'missing', 'XDG_RUNTIME_DIR': directory},
                                {'WAYLAND_DISPLAY': 'not-a-socket', 'XDG_RUNTIME_DIR': directory},
                                {'WAYLAND_DISPLAY': 'wayland-0', 'XDG_RUNTIME_DIR': 'relative'}):
                with self.subTest(environment=environment):
                    with patch.dict(os.environ, environment, clear=False):
                        for key in ('WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR'):
                            if key not in environment:
                                os.environ.pop(key, None)
                        kind, message = updater.brave_launch_result(binary)
                    self.assertEqual(kind, 'transient')
                    self.assertIn('no Wayland desktop session', message)
            kind, _message = updater.brave_launch_result(root / 'missing')
            self.assertEqual(kind, 'transient')
            binary.chmod(stat.S_IRUSR | stat.S_IWUSR)
            kind, message = updater.brave_launch_result(binary)
            self.assertEqual(kind, 'transient')
            self.assertIn('not executable', message)

    def test_any_accepted_version_is_reported_per_page_size(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with patch.object(updater, 'STATE', state), \
                    patch.object(updater, 'page_size', return_value=16384), \
                    patch('sys.stderr', new=io.StringIO()):
                self.assertEqual(updater.main(['brave-accepted-any']), 1)
                accepted = state / 'brave-accepted.json'
                accepted.write_text('{"4096": ["1.2.3"]}\n')
                self.assertEqual(updater.main(['brave-accepted-any']), 1)
                accepted.write_text('{"16384": ["1.2.3"]}\n')
                self.assertEqual(updater.main(['brave-accepted-any']), 0)
                accepted.write_text('[]\n')
                self.assertEqual(updater.main(['brave-accepted-any']), 2)

    def test_launch_checker_oserror_is_not_a_remembered_crash(self):
        with patch.object(updater.tempfile, 'mkdtemp', side_effect=OSError(28, 'No space left on device')):
            with redirect_stderr(io.StringIO()) as err:
                status = updater.main(['check-brave-launch', '/bin/true'])
        self.assertEqual(status, 2)
        self.assertIn('No space left on device', err.getvalue())
        with patch.object(updater, 'brave_launch_result', side_effect=updater.UpdateError('temporary directory')):
            with redirect_stderr(io.StringIO()):
                status = updater.main(['check-brave-launch', '/bin/true'])
        self.assertEqual(status, 2)
        with patch.object(updater, 'brave_launch_result', side_effect=KeyboardInterrupt(signal.SIGINT)):
            with self.assertRaises(KeyboardInterrupt):
                updater.main(['check-brave-launch', '/bin/true'])


if __name__ == '__main__':
    unittest.main()
