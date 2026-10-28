"""Detect-and-offer migrations to Omarchy's own installers (decision 0014).

These reuse the isolated installer fixture from test_install_keyd. Nothing here
runs pacman, flatpak, omarchy, or sudo on the host.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import test_install_keyd as base


REPO = base.REPO
FLATPAK_UNINSTALL = ['flatpak', '--user', 'uninstall', '--noninteractive', 'com.bitwarden.desktop']
FLATPAK_INSTALL = ['flatpak', '--user', 'install', '--noninteractive', 'flathub', 'com.bitwarden.desktop']
NATIVE_ADD = ['omarchy', 'pkg', 'add', 'bitwarden']
PROMPT = b'Migrate Bitwarden desktop now? [y/N] '

# Answers each [y/N] prompt in turn. "EOF" sends Ctrl+D at an empty line.
PTY_HELPER = r"""
import json, os, pty, select, sys, time
answers = json.loads(sys.argv[1])
command = sys.argv[2:]
pid, master = pty.fork()
if pid == 0:
    os.execvpe(command[0], command, os.environ)
deadline = time.monotonic() + 25
output = b""
seen = 0
status = None
while True:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        os.kill(pid, 15)
        raise SystemExit(124)
    ready, _, _ = select.select([master], [], [], min(0.2, remaining))
    if ready:
        try:
            data = os.read(master, 65536)
        except OSError:
            data = b""
        output += data
        while output.count(b"[y/N] ") > seen:
            seen += 1
            answer = answers.pop(0) if answers else "EOF"
            os.write(master, b"\x04" if answer == "EOF" else answer.encode() + b"\n")
    waited, candidate = os.waitpid(pid, os.WNOHANG)
    if waited == pid:
        status = candidate
        break
try:
    while True:
        data = os.read(master, 65536)
        if not data:
            break
        output += data
except OSError:
    pass
sys.stdout.buffer.write(output)
raise SystemExit(os.WEXITSTATUS(status) if os.WIFEXITED(status) else 124)
"""


class MigrationOfferTests(unittest.TestCase):
    # Same private PATH, home, and fixture stubs as the installer tests.
    setUp = base.InstallKeydTests.setUp
    calls = base.InstallKeydTests.calls

    def run_install(self, *args, success=True):
        result = subprocess.run(['bash', str(REPO / 'install.sh'), *args],
                                env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def run_update(self, success=True):
        result = subprocess.run(['python3', str(REPO / 'scripts/groundwork-update.py'), 'setup', str(REPO), '--update'],
                                env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def run_in_terminal(self, answers, *args, command=None, success=True):
        command = command or ['bash', str(REPO / 'install.sh'), *args]
        result = subprocess.run([sys.executable, '-c', PTY_HELPER, json.dumps(answers), *command],
                                env=self.env, capture_output=True, timeout=30)
        self.assertEqual(result.returncode == 0, success, result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace'))
        return result.stdout.decode(errors='replace')

    def reset_calls(self):
        (self.root / 'calls.jsonl').unlink(missing_ok=True)

    def saved(self):
        return json.loads((self.root / 'home/.config/groundwork/omarchy.json').read_text())

    def seed_bitwarden_flatpak(self):
        (self.root / 'bitwarden-flatpak').touch()
        data = self.root / 'home/.var/app/com.bitwarden.desktop/config/Bitwarden'
        data.mkdir(parents=True)
        (data / 'data.json').write_text('{"fixture": "local Flatpak data"}\n')
        return data / 'data.json'

    def feed_has_native_bitwarden(self):
        # pacman -Si bitwarden succeeds, and omarchy pkg add provides the command.
        (self.bin / 'pacman').symlink_to(self.bin / 'stub')
        self.env.update(FIXTURE_FEED='bitwarden', FIXTURE_MATERIALIZE_ADDED='1')

    def assert_not_migrated(self):
        calls = self.calls()
        self.assertNotIn(FLATPAK_UNINSTALL, calls)
        self.assertNotIn(NATIVE_ADD, calls)
        self.assertTrue((self.root / 'bitwarden-flatpak').exists())
        self.assertFalse((self.bin / 'bitwarden').exists())

    def assert_migrated(self, data):
        calls = self.calls()
        self.assertIn(NATIVE_ADD, calls)
        self.assertIn(FLATPAK_UNINSTALL, calls)
        # The package is in place before the Flatpak is removed.
        self.assertLess(calls.index(NATIVE_ADD), calls.index(FLATPAK_UNINSTALL))
        self.assertFalse(any(call[0] == 'flatpak' and '--delete-data' in call for call in calls))
        self.assertFalse((self.root / 'bitwarden-flatpak').exists())
        self.assertTrue((self.bin / 'bitwarden').exists())
        self.assertEqual(data.read_text(), '{"fixture": "local Flatpak data"}\n')

    # New installs.

    def test_new_install_uses_omarchy_package_when_the_feed_has_it(self):
        self.feed_has_native_bitwarden()
        result = self.run_install()
        calls = self.calls()
        self.assertIn(['pacman', '-Si', 'bitwarden'], calls)
        self.assertIn(NATIVE_ADD, calls)
        self.assertNotIn(FLATPAK_INSTALL, calls)
        self.assertFalse((self.root / 'bitwarden-flatpak').exists())
        self.assertIn(['flatpak', '--user', 'install', '--noninteractive', 'flathub', 'io.freetubeapp.FreeTube'], calls)
        self.assertNotIn('Migrate', result.stdout)

    def test_new_install_falls_back_to_flatpak_when_the_package_does_not_install(self):
        (self.bin / 'pacman').symlink_to(self.bin / 'stub')
        self.env['FIXTURE_FEED'] = 'bitwarden'
        result = self.run_install()
        self.assertIn(NATIVE_ADD, self.calls())
        self.assertIn(FLATPAK_INSTALL, self.calls())
        self.assertIn('using the Bitwarden Flatpak instead', result.stderr)
        # The fallback Flatpak is then a candidate, reported without a terminal.
        self.assertIn("Omarchy's preferred method is Omarchy's bitwarden package", result.stdout)

    def test_no_native_package_keeps_the_flatpak_and_offers_nothing(self):
        (self.bin / 'pacman').symlink_to(self.bin / 'stub')
        data = self.seed_bitwarden_flatpak()
        result = self.run_install('--migrate-to-omarchy-installers')
        self.assert_not_migrated()
        self.assertNotIn(FLATPAK_INSTALL, self.calls())
        self.assertNotIn('Bitwarden desktop is installed as', result.stdout)
        self.assertIn('Nothing was migrated.', result.stdout)
        self.assertNotIn('declined_migrations', self.saved())
        self.assertTrue(data.exists())

    def test_existing_native_bitwarden_is_reused_without_an_offer(self):
        self.feed_has_native_bitwarden()
        (self.bin / 'bitwarden').symlink_to(self.bin / 'stub')
        result = self.run_install()
        self.assertNotIn(NATIVE_ADD, self.calls())
        self.assertNotIn(FLATPAK_INSTALL, self.calls())
        self.assertNotIn('Bitwarden desktop is installed as', result.stdout)

    # Detected Flatpak, no consent.

    def test_without_a_terminal_setup_prints_one_notice_and_changes_nothing(self):
        self.feed_has_native_bitwarden()
        self.seed_bitwarden_flatpak()
        result = self.run_install()
        self.assert_not_migrated()
        notices = [line for line in result.stdout.splitlines() if line.startswith('Bitwarden desktop is installed as')]
        self.assertEqual(len(notices), 1, result.stdout)
        self.assertIn('the user Flatpak com.bitwarden.desktop', notices[0])
        self.assertIn("Omarchy's preferred method is Omarchy's bitwarden package", notices[0])
        self.assertIn('./install.sh --migrate-to-omarchy-installers', notices[0])
        self.assertNotIn('declined_migrations', self.saved())

    def test_update_in_a_terminal_only_reports_and_needs_no_sudo(self):
        self.feed_has_native_bitwarden()
        self.seed_bitwarden_flatpak()
        self.run_install('--without-keyd', '--without-screen-sharing')
        self.reset_calls()
        self.env['FIXTURE_SUDO_CACHED'] = '0'
        output = self.run_in_terminal(
            ['y'], command=['python3', str(REPO / 'scripts/groundwork-update.py'), 'setup', str(REPO), '--update'])
        self.assertNotIn('[y/N]', output)
        self.assertIn('Bitwarden desktop is installed as the user Flatpak', output)
        self.assertIn('sudo was not used', output)
        self.assertFalse(any(call[0] == 'sudo' for call in self.calls()))
        self.assert_not_migrated()

    def test_update_rejects_the_migration_flag(self):
        result = subprocess.run(['python3', str(REPO / 'scripts/groundwork-update.py'), 'setup', str(REPO),
                                 '--update', '--migrate-to-omarchy-installers'],
                                env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 64, result.stdout + result.stderr)
        self.assertEqual(self.calls(), [])
        direct = subprocess.run(['bash', str(REPO / 'scripts/apply-layer.sh'), '--update', '--migrate-to-omarchy-installers'],
                                env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(direct.returncode, 64, direct.stdout + direct.stderr)

    # Interactive setup.

    def test_interactive_decline_is_saved_and_not_asked_again(self):
        self.feed_has_native_bitwarden()
        data = self.seed_bitwarden_flatpak()
        output = self.run_in_terminal(['n'])
        self.assertEqual(output.count('[y/N]'), 1, output)
        self.assertIn('Setup will not ask again', output)
        self.assertIn('flatpak --user uninstall com.bitwarden.desktop', output)
        self.assert_not_migrated()
        self.assertEqual(self.saved()['declined_migrations'], ['bitwarden-flatpak'])
        self.reset_calls()
        output = self.run_in_terminal(['y'])
        self.assertNotIn('[y/N]', output)
        self.assertIn('stays on the user Flatpak com.bitwarden.desktop, as you chose', output)
        self.assert_not_migrated()
        self.assertEqual(self.saved()['declined_migrations'], ['bitwarden-flatpak'])
        # Routine updates stay silent about a declined offer.
        self.reset_calls()
        update = self.run_update()
        self.assertNotIn('Bitwarden desktop', update.stdout)
        self.assertEqual(self.saved()['declined_migrations'], ['bitwarden-flatpak'])
        self.assertTrue(data.exists())

    def test_enter_takes_the_default_no(self):
        self.feed_has_native_bitwarden()
        self.seed_bitwarden_flatpak()
        self.run_in_terminal([''])
        self.assert_not_migrated()
        self.assertEqual(self.saved()['declined_migrations'], ['bitwarden-flatpak'])

    def test_end_of_input_keeps_the_app_and_is_not_remembered(self):
        self.feed_has_native_bitwarden()
        self.seed_bitwarden_flatpak()
        output = self.run_in_terminal(['EOF'])
        self.assertIn('Setup asks again next time', output)
        self.assert_not_migrated()
        self.assertNotIn('declined_migrations', self.saved())

    def test_invalid_answer_is_asked_again(self):
        self.feed_has_native_bitwarden()
        self.seed_bitwarden_flatpak()
        output = self.run_in_terminal(['maybe', 'n'])
        self.assertEqual(output.count('[y/N]'), 2, output)
        self.assertIn('Answer y or n.', output)
        self.assert_not_migrated()

    def test_interactive_yes_installs_the_package_then_removes_the_flatpak(self):
        self.feed_has_native_bitwarden()
        data = self.seed_bitwarden_flatpak()
        output = self.run_in_terminal(['y'])
        self.assertIn('Bitwarden now uses Omarchy\'s bitwarden package. Sign in again', output)
        self.assert_migrated(data)
        self.assertNotIn('declined_migrations', self.saved())
        # Nothing is offered once only the package remains.
        self.reset_calls()
        output = self.run_in_terminal(['y'])
        self.assertNotIn('[y/N]', output)
        self.assertNotIn(FLATPAK_UNINSTALL, self.calls())
        self.assertNotIn(FLATPAK_INSTALL, self.calls())

    def test_package_and_flatpak_both_present_removes_only_the_flatpak(self):
        self.feed_has_native_bitwarden()
        data = self.seed_bitwarden_flatpak()
        (self.bin / 'bitwarden').symlink_to(self.bin / 'stub')
        self.run_install('--migrate-to-omarchy-installers')
        self.assertNotIn(NATIVE_ADD, self.calls())
        self.assertIn(FLATPAK_UNINSTALL, self.calls())
        self.assertFalse((self.root / 'bitwarden-flatpak').exists())
        self.assertTrue(data.exists())

    # Explicit flag.

    def test_flag_migrates_without_a_terminal_and_clears_a_saved_decline(self):
        self.feed_has_native_bitwarden()
        data = self.seed_bitwarden_flatpak()
        self.run_in_terminal(['n'])
        self.assertEqual(self.saved()['declined_migrations'], ['bitwarden-flatpak'])
        self.reset_calls()
        result = self.run_install('--migrate-to-omarchy-installers')
        self.assertIn('Migrating Bitwarden desktop', result.stdout)
        self.assert_migrated(data)
        self.assertNotIn('declined_migrations', self.saved())

    def test_flag_with_nothing_to_migrate_leaves_cursor_alone(self):
        self.feed_has_native_bitwarden()
        (self.bin / 'bitwarden').symlink_to(self.bin / 'stub')
        result = self.run_install('--migrate-to-omarchy-installers')
        self.assertIn('Nothing was migrated.', result.stdout)
        calls = self.calls()
        self.assertFalse(any(call[0] in ('cursor', 'omarchy-install-cursor') for call in calls))
        self.assertFalse(any('cursor-bin' in call for call in calls))
        self.assertNotIn(FLATPAK_UNINSTALL, calls)

    # Failed migrations keep the Flatpak, still save setup, and exit nonzero.

    def test_running_flatpak_is_not_migrated(self):
        self.feed_has_native_bitwarden()
        self.seed_bitwarden_flatpak()
        self.env['FIXTURE_FLATPAK_RUNNING'] = 'io.freetubeapp.FreeTube\ncom.bitwarden.desktop'
        result = self.run_install('--migrate-to-omarchy-installers', success=False)
        self.assertIn('Quit Bitwarden', result.stderr)
        self.assert_not_migrated()
        self.assertIn('rest of setup was applied and saved', result.stderr)
        self.assertTrue((self.root / 'home/.local/bin/groundwork-update').exists())

    def test_package_failure_keeps_the_flatpak(self):
        (self.bin / 'pacman').symlink_to(self.bin / 'stub')
        self.env['FIXTURE_FEED'] = 'bitwarden'
        self.seed_bitwarden_flatpak()
        result = self.run_install('--migrate-to-omarchy-installers', success=False)
        self.assertIn(NATIVE_ADD, self.calls())
        self.assertNotIn(FLATPAK_UNINSTALL, self.calls())
        self.assertTrue((self.root / 'bitwarden-flatpak').exists())
        self.assertIn('the Bitwarden Flatpak was kept unchanged', result.stderr)
        self.assertTrue(any(call[0] == 'stow' for call in self.calls()))
        self.assertTrue(self.saved()['keyd'])

    def test_uninstall_failure_names_the_command_to_finish(self):
        self.feed_has_native_bitwarden()
        self.seed_bitwarden_flatpak()
        self.env['FIXTURE_FLATPAK_UNINSTALL_STATUS'] = '1'
        result = self.run_install('--migrate-to-omarchy-installers', success=False)
        self.assertIn('Finish with: flatpak --user uninstall com.bitwarden.desktop', result.stderr)
        self.assertTrue((self.root / 'bitwarden-flatpak').exists())

    # Privilege on routine updates.

    def test_update_installs_a_missing_package_with_sudo_only_then(self):
        self.feed_has_native_bitwarden()
        self.run_install('--without-keyd', '--without-screen-sharing')
        self.reset_calls()
        self.env['FIXTURE_SUDO_CACHED'] = '0'
        result = self.run_update()
        self.assertIn('sudo was not used', result.stdout)
        self.assertNotIn(NATIVE_ADD, self.calls())
        (self.bin / 'bitwarden').unlink()
        self.reset_calls()
        self.env['FIXTURE_SUDO_CACHED'] = '1'
        result = self.run_update()
        self.assertNotIn('sudo was not used', result.stdout)
        self.assertIn(NATIVE_ADD, self.calls())


class DeclinedMigrationSettingsTests(unittest.TestCase):
    def run_updater(self, *args):
        return subprocess.run([sys.executable, str(REPO / 'scripts/groundwork-update.py'), *args],
                              env=self.env, capture_output=True, text=True, timeout=10)

    def setUp(self):
        import tempfile
        temporary = tempfile.TemporaryDirectory(prefix='groundwork-declined-')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.env = dict(os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / '.config'),
                        XDG_STATE_HOME=str(self.home / '.local/state'))
        self.config = self.home / '.config/groundwork/omarchy.json'
        self.config.parent.mkdir(parents=True)

    def write(self, **extra):
        value = dict(schema=1, source='/tmp/groundwork-source', keyd=False, screen_sharing=True,
                     obsidian_vault='Notes')
        value.update(extra)
        self.config.write_text(json.dumps(value) + '\n')

    def test_preferences_add_a_sixth_line_only_after_a_decline(self):
        self.write()
        self.assertEqual(self.run_updater('preferences', 'setup').stdout.splitlines(), ['0', '1', 'Notes', '0', '0'])
        self.write(declined_migrations=['bitwarden-flatpak', 'later-app'])
        self.assertEqual(self.run_updater('preferences', 'setup').stdout.splitlines()[5], 'bitwarden-flatpak,later-app')

    def test_invalid_declines_are_refused(self):
        for bad in ('bitwarden-flatpak', ['Bitwarden'], ['a,b'], [''], [1]):
            with self.subTest(bad=bad):
                self.write(declined_migrations=bad)
                result = self.run_updater('preferences', 'setup')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Invalid updater settings', result.stderr)


if __name__ == '__main__':
    unittest.main()
