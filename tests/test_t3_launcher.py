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


if __name__ == '__main__':
    unittest.main()
