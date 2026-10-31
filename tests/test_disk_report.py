"""Disk report: fixture HOME and fake commands only (decision 0019). Nothing is deleted."""
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'scripts/groundwork-disk-report.py'
spec = importlib.util.spec_from_file_location('groundwork_disk_report', SCRIPT)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)

FLATPAK_TABLE = (
    'Info: runtimes marked for removal:\n\n'
    ' ID                               Branch  Op\n'
    ' 1. org.freedesktop.Platform      23.08   r\n'
    ' 2. org.gnome.Platform            45      r\n'
)


class DiskReport(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name) / 'home'
        (self.home / '.cache/yay/pkg').mkdir(parents=True)
        (self.home / '.cache/yay/pkg/a').write_bytes(b'x' * 2048)
        (self.home / '.local/share/flatpak').mkdir(parents=True)
        env = {'HOME': str(self.home)}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ('XDG_CACHE_HOME', 'XDG_DATA_HOME'):
            os.environ.pop(key, None)
        self.commands = []

    def fake_run(self, argv):
        self.commands.append(argv)
        if argv[0] == 'journalctl':
            return 'Archived and active journals take up 1.5G in the file system.\n'
        if argv[0] == 'flatpak':
            return FLATPAK_TABLE
        return None

    def sizes(self, path):
        return {'/var/cache/pacman/pkg': 3 * report.GIB}.get(str(path), report.tree_size(path)
                if str(path).startswith(str(self.home)) else None)

    def collect(self, free=50 * report.GIB):
        return {r[0]: r for r in report.collect(self.home, run=self.fake_run, tree_size=self.sizes, free_bytes=free)}

    def test_reports_each_area_with_a_command(self):
        rows = self.collect()
        self.assertEqual(rows['Pacman cache'][1], '3.0 GiB')
        self.assertEqual(rows['Pacman cache'][3], 'sudo paccache -rk2')
        self.assertEqual(rows['AUR helper cache (yay)'][1], '2.0 KiB')
        self.assertEqual(rows['AUR helper cache (yay)'][3], 'yay -Sc')
        self.assertEqual(rows['systemd journal'][1], '1.5 GiB')
        self.assertIn('--vacuum-size', rows['systemd journal'][3])
        flat = rows['Flatpak user installation']
        self.assertIn('2 unused', flat[2])
        self.assertEqual(flat[3], 'flatpak uninstall --user --unused')
        self.assertNotIn('Flatpak system installation', rows)

    def test_only_read_only_commands_are_run(self):
        self.collect()
        for argv in self.commands:
            self.assertIn(argv[0], ('journalctl', 'flatpak'))
            if argv[0] == 'flatpak':
                self.assertIn('--assumeno', argv)
            else:
                self.assertEqual(argv, ['journalctl', '--disk-usage'])

    def test_low_free_space_is_flagged_against_omarchys_threshold(self):
        self.assertIn('under that now', self.collect(free=5 * report.GIB)['Free space on /'][2])
        self.assertNotIn('under that now', self.collect()['Free space on /'][2])

    def test_missing_tools_and_caches_give_no_command(self):
        (self.home / '.cache/yay/pkg/a').unlink()
        rows = {r[0]: r for r in report.collect(
            self.home, run=lambda argv: None, tree_size=lambda p: None, free_bytes=report.GIB)}
        self.assertEqual(rows['Pacman cache'][1], 'not found')
        self.assertIsNone(rows['Pacman cache'][3])
        self.assertEqual(rows['systemd journal'][1], 'unknown')
        self.assertIsNone(rows['systemd journal'][3])
        self.assertNotIn('Flatpak user installation', rows)

    def test_parsers(self):
        self.assertEqual(report.journal_bytes('take up 323.8M in'), int(323.8 * 1024 ** 2))
        self.assertIsNone(report.journal_bytes('garbage'))
        self.assertEqual(report.unused_flatpak_refs(FLATPAK_TABLE), 2)
        self.assertEqual(report.unused_flatpak_refs(''), 0)

    def test_cli_prints_and_rejects_arguments(self):
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.home), 'XDG_CACHE_HOME': str(self.home / '.cache'),
               'XDG_DATA_HOME': str(self.home / '.local/share')}
        done = subprocess.run([sys.executable, '-I', str(SCRIPT)], env=env, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('deletes nothing', done.stdout)
        self.assertIn('disktree', done.stdout)
        bad = subprocess.run([sys.executable, '-I', str(SCRIPT), '--delete'], env=env, capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)
        self.assertTrue((self.home / '.cache/yay/pkg/a').exists())

    def test_no_dust_in_packages(self):
        names = [l.strip() for l in (REPO / 'packages.txt').read_text().splitlines()]
        self.assertNotIn('dust', names)


if __name__ == '__main__':
    unittest.main()
