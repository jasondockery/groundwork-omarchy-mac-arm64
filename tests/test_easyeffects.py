"""EasyEffects default EQ: unit flags, opt-out, and saved preference (decision 0024)."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'scripts/groundwork-easyeffects.py'
spec = importlib.util.spec_from_file_location('groundwork_easyeffects', SCRIPT)
ee = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ee)

updater_spec = importlib.util.spec_from_file_location(
    'groundwork_easyeffects_updater', REPO / 'scripts/groundwork-update.py')
updater = importlib.util.module_from_spec(updater_spec)
updater_spec.loader.exec_module(updater)


class EasyEffectsHelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='groundwork-easyeffects-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'
        self.home.mkdir()
        self.bin = Path(self.temp.name) / 'bin'
        self.bin.mkdir()
        self.easyeffects = self.bin / 'easyeffects'
        self.easyeffects.write_text('#!/bin/sh\nexit 0\n')
        self.easyeffects.chmod(0o755)
        self.calls = []

    def run_configure(self, which=None, systemctl_status=0):
        if which is None:
            which_path = self.easyeffects
        elif which is False:
            which_path = None
        else:
            which_path = which

        def fake_run(argv, check=True, timeout=None):
            self.calls.append(list(argv))
            if systemctl_status:
                raise subprocess.CalledProcessError(systemctl_status, argv)
            return subprocess.CompletedProcess(argv, 0)

        with patch.object(ee, 'unit_path',
                          lambda: self.home / '.config/systemd/user' / ee.UNIT_NAME), \
                patch.object(ee.shutil, 'which',
                             return_value=str(which_path) if which_path else None), \
                patch.object(ee.subprocess, 'run', side_effect=fake_run):
            return ee.configure()

    def unit_text(self):
        return (self.home / '.config/systemd/user' / ee.UNIT_NAME).read_text()

    def test_configure_writes_qt_background_flags_and_enables_without_starting(self):
        self.run_configure()
        text = self.unit_text()
        self.assertIn(ee.MARKER, text)
        self.assertIn('--hide-window --service-mode', text)
        self.assertNotIn('--gapplication-service', text)
        self.assertIn('WantedBy=graphical-session.target', text)
        self.assertIn('After=pipewire.service graphical-session.target', text)
        self.assertIn('PartOf=graphical-session.target', text)
        resolved = str(self.easyeffects.resolve())
        self.assertIn(f'ExecStart="{resolved}" --hide-window --service-mode', text)
        self.assertIn(f'ExecStop="{resolved}" --quit', text)
        self.assertNotIn('waybar', text.lower())
        self.assertEqual(self.calls, [
            ['systemctl', '--user', 'daemon-reload'],
            ['systemctl', '--user', 'enable', ee.UNIT_NAME],
        ])
        self.assertFalse((self.home / '.config/easyeffects').exists())

    def test_configure_leaves_existing_presets_alone(self):
        preset = self.home / '.config/easyeffects/output/keep.json'
        preset.parent.mkdir(parents=True)
        preset.write_text('{"user": "preset"}\n')
        self.run_configure()
        self.assertEqual(preset.read_text(), '{"user": "preset"}\n')

    def test_configure_does_not_chmod_the_shared_unit_directory(self):
        unit_dir = self.home / '.config/systemd/user'
        unit_dir.mkdir(parents=True)
        unit_dir.chmod(0o755)
        other = unit_dir / 'other.service'
        other.write_text('[Unit]\nDescription=kept\n')
        self.run_configure()
        self.assertEqual(unit_dir.stat().st_mode & 0o777, 0o755)
        self.assertEqual(other.read_text(), '[Unit]\nDescription=kept\n')

    def test_unchanged_unit_is_not_backed_up(self):
        self.run_configure()
        self.calls.clear()
        self.run_configure()
        unit = self.home / '.config/systemd/user' / ee.UNIT_NAME
        self.assertFalse(unit.with_name(unit.name + '.bak.1').exists())
        self.assertEqual(self.calls, [
            ['systemctl', '--user', 'daemon-reload'],
            ['systemctl', '--user', 'enable', ee.UNIT_NAME],
        ])

    def test_different_unit_is_backed_up(self):
        unit = self.home / '.config/systemd/user' / ee.UNIT_NAME
        unit.parent.mkdir(parents=True)
        unit.write_text('[Unit]\nDescription=previous\n')
        self.run_configure()
        self.assertEqual(unit.with_name(unit.name + '.bak.1').read_text(),
                         '[Unit]\nDescription=previous\n')
        self.assertIn(ee.MARKER, unit.read_text())

    def test_symlink_unit_is_refused(self):
        unit = self.home / '.config/systemd/user' / ee.UNIT_NAME
        unit.parent.mkdir(parents=True)
        target = self.home / 'elsewhere.service'
        target.write_text('[Unit]\n')
        unit.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            self.run_configure()
        self.assertEqual(target.read_text(), '[Unit]\n')
        self.assertEqual(self.calls, [])

    def test_missing_easyeffects_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'not on PATH'):
            self.run_configure(which=False)
        self.assertEqual(self.calls, [])
        self.assertFalse((self.home / '.config/systemd/user' / ee.UNIT_NAME).exists())

    def test_whitespace_path_is_refused(self):
        spaced = Path(self.temp.name) / 'easy effects' / 'easyeffects'
        spaced.parent.mkdir()
        spaced.write_text('#!/bin/sh\nexit 0\n')
        spaced.chmod(0o755)
        with self.assertRaisesRegex(RuntimeError, 'whitespace'):
            self.run_configure(which=spaced)
        self.assertEqual(self.calls, [])

    def test_cli_configure_uses_the_helper(self):
        with patch.object(ee, 'configure') as configure:
            self.assertEqual(ee.main(['configure']), 0)
            configure.assert_called_once_with()

    def test_source_does_not_name_waybar(self):
        text = SCRIPT.read_text()
        self.assertNotIn('waybar', text.lower())
        self.assertIn('--hide-window', text)
        self.assertIn('--service-mode', text)
        self.assertIn('deprecated', text)
        self.assertIn('gapplication-service', text)


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='groundwork-ee-pref-')
        self.addCleanup(temporary.cleanup)
        self.config = Path(temporary.name) / 'omarchy.json'
        self.patch = patch.object(updater, 'CONFIG', self.config)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def write(self, **extra):
        value = dict(schema=1, source='/tmp/groundwork-source', keyd=False, screen_sharing=True,
                     obsidian_vault='Notes')
        value.update(extra)
        self.config.write_text(json.dumps(value) + '\n')

    def test_missing_choice_stays_on(self):
        self.write()
        self.assertTrue(updater.easyeffects_enabled(updater.settings()))
        self.assertTrue(updater.easyeffects_enabled(None))
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(updater.main(['preferences', 'setup']), 0)
        self.assertEqual(output.getvalue().splitlines(), ['0', '1', 'Notes', '0', '0'])

    def test_opt_out_adds_a_ninth_line(self):
        self.write(easyeffects=False)
        self.assertFalse(updater.easyeffects_enabled(updater.settings()))
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(updater.main(['preferences', 'setup']), 0)
        self.assertEqual(output.getvalue().splitlines(),
                         ['0', '1', 'Notes', '0', '0', '', '', '', '0'])

    def test_opt_out_with_tmp_off_keeps_empty_git_filter_repo_and_tmp_zero(self):
        self.write(easyeffects=False, tmp_on_disk=False)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(updater.main(['preferences', 'setup']), 0)
        self.assertEqual(output.getvalue().splitlines(),
                         ['0', '1', 'Notes', '0', '0', '', '', '0', '0'])

    def test_non_boolean_choice_is_rejected(self):
        self.write(easyeffects='yes')
        with self.assertRaisesRegex(updater.UpdateError, 'Invalid updater settings'):
            updater.settings()

    def test_help_documents_the_default_and_the_opt_out(self):
        result = subprocess.run(['bash', str(REPO / 'install.sh'), '--help'],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--with-easyeffects', result.stdout)
        self.assertIn('--without-easyeffects', result.stdout)
        self.assertIn('docs/apps/easyeffects.md', result.stdout)
        updater_help = subprocess.run(
            [sys.executable, str(REPO / 'scripts/groundwork-update.py'), 'setup',
             '/tmp/groundwork-source', '--help'],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(updater_help.returncode, 0, updater_help.stderr)
        self.assertIn('--without-easyeffects', updater_help.stdout)
        self.assertIn('docs/apps/easyeffects.md', updater_help.stdout)

    def test_conflicting_setup_flags_change_nothing(self):
        self.assertEqual(
            updater.main(['setup', '/tmp/groundwork-source', '--with-easyeffects',
                          '--without-easyeffects']),
            64)

    def test_apply_layer_does_not_name_waybar(self):
        text = (REPO / 'scripts/apply-layer.sh').read_text()
        self.assertNotIn('waybar', text.lower())
        self.assertIn('groundwork-easyeffects.py', text)
        self.assertIn('--without-easyeffects', text)


class ApplyLayerSourceTests(unittest.TestCase):
    def test_unit_is_enabled_not_started_in_apply(self):
        text = (REPO / 'scripts/apply-layer.sh').read_text()
        self.assertIn('python3 "$root/scripts/groundwork-easyeffects.py" configure', text)
        self.assertNotIn('systemctl --user start groundwork-easyeffects', text)
        self.assertNotIn('systemctl --user restart groundwork-easyeffects', text)
        self.assertIn('does not start the process now', text)

    def test_docs_exist_and_name_quickshell(self):
        decision = (REPO / 'docs/decisions/0024-easyeffects-default-eq.md').read_text()
        app = (REPO / 'docs/apps/easyeffects.md').read_text()
        self.assertIn('Quickshell', decision)
        self.assertIn('Quickshell', app)
        self.assertIn('--hide-window --service-mode', decision)
        self.assertIn('would be the wrong desktop', decision)
        self.assertIn('Never write', app)
        helper = (REPO / 'scripts/groundwork-easyeffects.py').read_text()
        apply = (REPO / 'scripts/apply-layer.sh').read_text()
        self.assertNotIn('~/.config/waybar', helper)
        self.assertNotIn('~/.config/waybar', apply)


if __name__ == '__main__':
    unittest.main()
