"""T3 launcher reconcile against a temp XDG tree, a fake xdg-mime, and a fake /proc."""
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/groundwork-t3-launcher.py'
spec = importlib.util.spec_from_file_location('groundwork_t3_launcher', SCRIPT)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)

FAKE_XDG_MIME = '''#!/bin/sh
state="$XDG_CONFIG_HOME/default"
case "$1" in
  query) cat "$state" 2>/dev/null ;;
  default) [ -n "$FAKE_XDG_FAIL" ] && exit 1; printf '%s\\n' "$2" >"$state" ;;
esac
'''


class T3LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.home = root / 'home'
        self.apps = self.home / '.local/share/applications'
        self.apps.mkdir(parents=True)
        self.config = root / 'config'
        self.config.mkdir()
        self.proc = root / 'proc'
        self.proc.mkdir()
        self.appimage = self.home / '.local/share/t3code/T3-Code.AppImage'
        bin_dir = root / 'bin'
        bin_dir.mkdir()
        fake = bin_dir / 'xdg-mime'
        fake.write_text(FAKE_XDG_MIME)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        env = dict(HOME=str(self.home), XDG_DATA_HOME=str(self.home / '.local/share'),
                   XDG_CONFIG_HOME=str(self.config), PATH=f'{bin_dir}:{os.environ["PATH"]}')
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.primary = self.apps / 't3code.desktop'
        self.duplicate = self.apps / 'com.t3tools.T3Code.desktop'
        self.primary.write_text(f'[Desktop Entry]\nExec="{self.appimage}" %U\nTryExec={self.appimage}\n')
        self.duplicate.write_text(f'[Desktop Entry]\nExec={self.appimage} %U\nNoDisplay=true\n')
        self.set_default('com.t3tools.T3Code.desktop')

    def set_default(self, name):
        (self.config / 'default').write_text(name + '\n')

    def default(self):
        return (self.config / 'default').read_text().strip()

    def process(self, pid, exe, cmdline=''):
        directory = self.proc / str(pid)
        directory.mkdir()
        os.symlink(exe, directory / 'exe')
        (directory / 'cmdline').write_bytes(cmdline.encode())

    def reconcile(self):
        return launcher.reconcile(self.proc)

    def test_idle_repoints_handler_and_removes_duplicate(self):
        self.process(1, '/usr/bin/bash', 'bash\0')
        messages = self.reconcile()
        self.assertEqual(self.default(), 't3code.desktop')
        self.assertFalse(self.duplicate.exists())
        self.assertTrue(self.primary.exists())
        self.assertEqual(len(messages), 2)

    def test_idempotent_and_survives_recreation(self):
        self.process(1, '/usr/bin/bash')
        self.reconcile()
        self.assertEqual(self.reconcile(), [])
        self.duplicate.write_text('[Desktop Entry]\nExec=%s %%U\nNoDisplay=true\n' % self.appimage)
        self.reconcile()
        self.assertFalse(self.duplicate.exists())
        self.assertEqual(self.default(), 't3code.desktop')

    def test_running_by_exe_or_mount_changes_nothing(self):
        for exe in (str(self.appimage), '/tmp/.mount_T3-CodAbCdEf/t3code', f'{self.appimage} (deleted)'):
            with self.subTest(exe=exe):
                directory = self.proc / '7'
                if directory.exists():
                    (directory / 'exe').unlink()
                    (directory / 'cmdline').unlink()
                    directory.rmdir()
                self.process(7, exe)
                messages = self.reconcile()
                self.assertEqual(len(messages), 1)
                self.assertIn('running', messages[0])
                self.assertTrue(self.duplicate.exists())
                self.assertEqual(self.default(), 'com.t3tools.T3Code.desktop')

    def test_running_by_argv(self):
        self.process(5, '/usr/bin/bwrap', f'bwrap\0{self.appimage}\0')
        self.assertIn('running', self.reconcile()[0])
        self.assertTrue(self.duplicate.exists())

    def test_unreadable_proc_fails_safe(self):
        messages = launcher.reconcile(self.proc / 'missing')
        self.assertIn('skipped', messages[0])
        self.assertTrue(self.duplicate.exists())
        self.assertEqual(self.default(), 'com.t3tools.T3Code.desktop')

    def test_handler_failure_keeps_duplicate(self):
        self.process(1, '/usr/bin/bash')
        with patch.dict(os.environ, FAKE_XDG_FAIL='1'):
            messages = self.reconcile()
        self.assertIn('left alone', messages[0])
        self.assertTrue(self.duplicate.exists())

    def test_missing_primary_changes_nothing(self):
        self.process(1, '/usr/bin/bash')
        self.primary.unlink()
        self.assertIn('missing', self.reconcile()[0])
        self.assertTrue(self.duplicate.exists())

    def test_foreign_duplicate_is_kept(self):
        self.process(1, '/usr/bin/bash')
        self.duplicate.write_text('[Desktop Entry]\nExec=/usr/bin/other %U\n')
        self.reconcile()
        self.assertTrue(self.duplicate.exists())

    def test_nothing_to_do_is_silent(self):
        self.process(1, '/usr/bin/bash')
        self.duplicate.unlink()
        self.set_default('t3code.desktop')
        self.assertEqual(self.reconcile(), [])

    def test_stale_added_association_is_dropped_only_for_the_removed_file(self):
        self.process(1, '/usr/bin/bash')
        mime = self.config / 'mimeapps.list'
        mime.write_text('[Default Applications]\nx-scheme-handler/t3code=t3code.desktop;\n\n'
                        '[Added Associations]\nx-scheme-handler/t3code=com.t3tools.T3Code.desktop;\n'
                        'x-scheme-handler/other=com.t3tools.T3Code.desktop;\n')
        self.reconcile()
        self.assertEqual(mime.read_text(), '[Default Applications]\nx-scheme-handler/t3code=t3code.desktop;\n\n'
                         '[Added Associations]\nx-scheme-handler/other=com.t3tools.T3Code.desktop;\n')
        before = mime.read_text()
        self.set_default('com.t3tools.T3Code.desktop')
        self.reconcile()
        self.assertEqual(mime.read_text(), before)

    def test_mixed_association_keeps_other_handlers(self):
        self.process(1, '/usr/bin/bash')
        mime = self.config / 'mimeapps.list'
        mime.write_text('[Added Associations]\nx-scheme-handler/t3code=com.t3tools.T3Code.desktop;t3code.desktop;\n')
        self.reconcile()
        self.assertEqual(mime.read_text(), '[Added Associations]\nx-scheme-handler/t3code=t3code.desktop;\n')

    def test_association_is_untouched_while_running(self):
        self.process(1, str(self.appimage))
        mime = self.config / 'mimeapps.list'
        text = '[Added Associations]\nx-scheme-handler/t3code=com.t3tools.T3Code.desktop;\n'
        mime.write_text(text)
        self.reconcile()
        self.assertEqual(mime.read_text(), text)

    def test_missing_xdg_mime_is_quiet_without_a_duplicate(self):
        with patch.dict(os.environ, PATH='/nonexistent'):
            self.assertIn('not installed', launcher.reconcile(self.proc)[0])
            self.duplicate.unlink()
            self.assertEqual(launcher.reconcile(self.proc), [])

    def test_omarchy_installer_entry_with_an_escaped_percent(self):
        # omarchy-install-t3code-app writes Exec="<path>" %U with % doubled, and TryExec unescaped.
        appimage = self.home / '.local/share/t3code%dir/T3-Code.AppImage'
        escaped = str(appimage).replace('%', '%%')
        self.primary.write_text('[Desktop Entry]\nName=T3 Code (Nightly)\n'
                                f'Exec="{escaped}" %U\nTryExec={appimage}\n'
                                'MimeType=x-scheme-handler/t3code;x-scheme-handler/t3code-dev;\n')
        self.assertIn(str(appimage), launcher.exec_paths(self.primary))
        self.assertNotIn(escaped, launcher.exec_paths(self.primary))
        self.process(3, str(appimage))
        self.assertIn('running', self.reconcile()[0])
        self.assertTrue(self.duplicate.exists())

    def test_running_counts_the_bun_cli_only_when_asked(self):
        cli = self.home / '.t3/runtime/versions/0.1.2-nightly.20261021.3457/t3'
        self.process(9, str(cli))
        self.assertEqual(launcher.app_running(self.proc), (False, ''))
        running, reason = launcher.app_running(self.proc, cli=True)
        self.assertTrue(running)
        self.assertIn('process 9', reason)
        with patch.dict(os.environ, T3CODE_HOME=str(self.home / 'elsewhere')):
            self.assertEqual(launcher.app_running(self.proc, cli=True), (False, ''))

    def test_extract_and_run_counts_as_running(self):
        self.process(4, '/tmp/appimage_extracted_0123abcd/other')
        self.assertEqual(launcher.app_running(self.proc), (False, ''))
        self.process(5, '/tmp/appimage_extracted_0123abcd/t3code')
        self.assertTrue(launcher.app_running(self.proc)[0])

    def test_running_matches_the_real_path_of_a_linked_t3_home(self):
        real = self.home / 'data/t3'
        (real / 'runtime/versions').mkdir(parents=True)
        (self.home / '.t3').symlink_to(real)
        self.process(9, str(real / 'runtime/versions/0.1.2-nightly.20261021.3457/t3'))
        self.assertTrue(launcher.app_running(self.proc, cli=True)[0])

    def test_running_command_exit_status(self):
        self.process(1, '/usr/bin/bash')
        with patch.dict(os.environ, GROUNDWORK_TESTING='1', GROUNDWORK_TEST_PROC=str(self.proc)):
            self.assertEqual(launcher.main(['launcher', 'running']), 1)
            self.process(2, '/tmp/.mount_T3-CodAbCdEf/t3code')
            with patch('sys.stdout'):
                self.assertEqual(launcher.main(['launcher', 'running']), 0)
        # Unsure counts as running.
        with patch.dict(os.environ, GROUNDWORK_TESTING='1', GROUNDWORK_TEST_PROC=str(self.proc / 'missing')), \
                patch('sys.stdout'):
            self.assertEqual(launcher.main(['launcher', 'running']), 0)

    def test_the_fixture_proc_needs_the_testing_switch(self):
        with patch.dict(os.environ, GROUNDWORK_TEST_PROC=str(self.proc)):
            os.environ.pop('GROUNDWORK_TESTING', None)
            self.assertEqual(launcher.proc_root(), Path('/proc'))

    def test_message_names_the_commands_to_rerun(self):
        self.process(1, str(self.appimage))
        self.assertIn('update-all', self.reconcile()[0])


class HookTests(unittest.TestCase):
    """The reconcile must run once per setup and once per update-all."""
    ROOT = Path(__file__).resolve().parents[1]

    def test_apply_layer_runs_it_only_outside_an_update(self):
        text = (self.ROOT / 'scripts/apply-layer.sh').read_text()
        call = 'python3 "$root/scripts/groundwork-t3-launcher.py" reconcile'
        self.assertEqual(text.count(call), 1)
        before = text[:text.index(call)]
        self.assertTrue(before.rstrip().endswith('if [[ $updating -eq 0 ]]; then'))

    def test_update_all_runs_it_after_the_t3_check(self):
        text = (self.ROOT / 'scripts/groundwork-update.py').read_text()
        call = "str(source / 'scripts/groundwork-t3-launcher.py'), 'reconcile'"
        self.assertEqual(text.count(call), 1)
        self.assertLess(text.index('update_t3(completed, warnings, source)\n'), text.index(call))


if __name__ == '__main__':
    unittest.main()
