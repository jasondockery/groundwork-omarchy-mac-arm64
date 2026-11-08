"""groundwork-doctor's host-tools check (decision 0021, issue #130).

Every run uses a private PATH of stub binaries and a private system root with
/tmp already on disk, so the host's own tools, /tmp, and the LUKS root never
decide a result. The temp-storage findings are in test_tmp_on_disk.
"""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import test_tmp_on_disk

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'scripts/groundwork-doctor.py'
spec = importlib.util.spec_from_file_location('groundwork_doctor', SCRIPT)
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)

TOOLS = ('lsof', 'ps', 'rg', 'jq', 'shellcheck', 'ssh', 'git')
# procps-ng prints a start time for `ps -o lstart= -p PID`.
PROCPS_PS = '#!/bin/sh\n[ "$1" = -o ] && [ "$2" = lstart= ] && { echo "Tue Nov  3 21:38:13 2026"; exit 0; }\nexit 0\n'
# busybox ps rejects the lstart format.
TEMP_OK = '/tmp on btrfs, tmp.mount masked (/tmp on disk on), tmpfiles ages /tmp after 10d, 0 scratch roots'
BUSYBOX_PS = '#!/bin/sh\necho "ps: bad -o argument \'lstart\'" >&2\nexit 1\n'


class HostToolsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='groundwork-doctor-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for name in TOOLS:
            self.stub(name, PROCPS_PS if name == 'ps' else '#!/bin/sh\nexit 0\n')
        home = self.root / 'home'
        home.mkdir()
        system = self.root / 'system'
        test_tmp_on_disk.seed_system(system)
        test_tmp_on_disk.fake_systemctl(self.bin)
        self.environ = {
            'PATH': str(self.bin), 'HOME': str(home), 'GROUNDWORK_TESTING': '1',
            'GROUNDWORK_TEST_ROOT': str(system), 'GROUNDWORK_STORAGE_ROOT': str(self.root),
        }
        self.install_storage_stubs(home)
        for key, sub in (('XDG_CONFIG_HOME', '.config'), ('XDG_STATE_HOME', '.local/state'),
                         ('XDG_CACHE_HOME', '.cache'), ('XDG_DATA_HOME', '.local/share'),
                         ('GNUPGHOME', '.gnupg')):
            self.environ[key] = str(home / sub)

    def stub(self, name, body):
        path = self.bin / name
        path.write_text(body)
        path.chmod(0o755)

    def install_storage_stubs(self, home):
        """A healthy storage fixture so host-tool tests do not read this machine."""
        for relative in ('.t3/worktrees', 'code', '.cache'):
            (home / relative).mkdir(parents=True)
        unit = self.root / 'usr/lib/systemd/system'
        unit.mkdir(parents=True)
        (unit / 'fstrim.timer').write_text('[Timer]\n')
        (unit / 'plocate-updatedb.service').write_text('[Service]\nExecStart=/usr/bin/updatedb\n')
        rendered = doctor.STORAGE.render_dropin(doctor.STORAGE.TEMPLATE.read_text(encoding='utf-8'), str(home))
        dropin = self.root / doctor.STORAGE.DROPIN_RELATIVE
        dropin.parent.mkdir(parents=True)
        dropin.write_text(rendered)
        dropin.chmod(0o644)
        self.stub('findmnt', '#!/bin/sh\n[ "$3" = FSTYPE ] && { echo btrfs; exit 0; }\n'
                              '[ "$1" = -n ] && [ "$4" = / ] && echo "/dev/mapper/root compress=zstd:3"\n')
        self.stub('lsblk', '#!/bin/sh\ncase "$2" in TYPE) echo crypt ;; DISC-GRAN) echo 512B ;; esac\n')
        systemctl = test_tmp_on_disk.FAKE_SYSTEMCTL
        extra = (
            "if sys.argv[1:] == ['is-enabled', 'fstrim.timer']:\n"
            "    print('enabled')\n"
            "    sys.exit(0)\n"
            "if sys.argv[1:4] == ['show', '-p', 'DropInPaths']:\n"
            "    print(os.environ['GROUNDWORK_STORAGE_ROOT'] + "
            "'/etc/systemd/system/plocate-updatedb.service.d/groundwork-prunes.conf')\n"
            "    sys.exit(0)\n"
        )
        needle = "if sys.argv[1:] != ['is-enabled', 'tmp.mount']:"
        self.assertIn(needle, systemctl)
        self.stub('systemctl', systemctl.replace(needle, extra + needle, 1))
        self.stub('btrfs', '#!/bin/sh\necho "btrfs must not be used" >&2\nexit 1\n')

    def storage_ok(self):
        home = self.environ['HOME']
        storage = doctor.STORAGE
        return (f'TRIM through LUKS: {storage.TRIM_OK}\n'
                f'Locate index: {storage.locate_ok(home)}\n'
                f'Compression: {storage.COMPRESSION_OK}\n')

    def doctor(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = doctor.main(list(args), self.environ)
        return status, output.getvalue()

    def test_all_present_passes(self):
        status, text = self.doctor()
        self.assertEqual(status, 0)
        self.assertEqual(text, 'Host tools: ok (lsof, ps -o lstart=, rg, jq, shellcheck, ssh, git)\n'
                               f'Temp storage: ok ({TEMP_OK})\n' + self.storage_ok())
        self.assertEqual(self.doctor('--brief'), (0, ''))

    def test_each_missing_tool_names_its_install_line(self):
        expected = {
            'lsof': 'sudo pacman -S --needed lsof',
            'ps': 'sudo pacman -S --needed procps-ng',
            'rg': 'sudo pacman -S --needed ripgrep',
            'jq': 'sudo pacman -S --needed jq',
            'shellcheck': 'omarchy pkg aur add shellcheck-bin',
            'ssh': 'sudo pacman -S --needed openssh',
            'git': 'sudo pacman -S --needed git',
        }
        self.assertEqual(set(expected), set(TOOLS))
        for name, install in expected.items():
            with self.subTest(name=name):
                body = (self.bin / name).read_text()
                (self.bin / name).unlink()
                try:
                    status, text = self.doctor()
                    self.assertEqual(status, 1)
                    self.assertIn(f'{name} is missing', text)
                    self.assertIn(f'Install it with: {install}', text)
                    self.assertIn('Host tools: 1 problem\n', text)
                finally:
                    self.stub(name, body)

    def test_lsof_missing_is_reported_and_fails(self):
        (self.bin / 'lsof').unlink()
        status, text = self.doctor()
        self.assertEqual(status, 1)
        self.assertEqual(text, 'Host tools: 1 problem\n'
                               '  lsof is missing (lsof). Install it with: sudo pacman -S --needed lsof\n'
                               f'Temp storage: ok ({TEMP_OK})\n'
                               + self.storage_ok())

    def test_several_missing_tools_are_each_listed(self):
        (self.bin / 'lsof').unlink()
        (self.bin / 'shellcheck').unlink()
        status, text = self.doctor()
        self.assertEqual(status, 1)
        self.assertIn('Host tools: 2 problems', text)
        self.assertIn('shellcheck is missing (shellcheck-bin). Install it with: omarchy pkg aur add shellcheck-bin', text)

    def test_ps_without_lstart_is_reported(self):
        self.stub('ps', BUSYBOX_PS)
        status, text = self.doctor()
        self.assertEqual(status, 1)
        self.assertIn('does not support `ps -o lstart=`', text)
        self.assertIn('sudo pacman -S --needed procps-ng', text)

    def test_ps_with_empty_lstart_is_reported(self):
        self.stub('ps', '#!/bin/sh\nexit 0\n')
        status, text = self.doctor()
        self.assertEqual(status, 1)
        self.assertIn('does not support `ps -o lstart=`', text)

    def test_non_executable_file_counts_as_missing(self):
        (self.bin / 'jq').chmod(0o644)
        status, text = self.doctor()
        self.assertEqual(status, 1)
        self.assertIn('jq is missing', text)

    def test_brief_prints_one_line_per_problem_and_never_fails(self):
        (self.bin / 'lsof').unlink()
        self.stub('ps', BUSYBOX_PS)
        status, text = self.doctor('--brief')
        self.assertEqual(status, 0)
        lines = text.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(line.startswith('Host tools: ') for line in lines))
        self.assertIn('sudo pacman -S --needed lsof', lines[0])

    def test_command_line_uses_the_given_path(self):
        (self.bin / 'git').unlink()
        result = subprocess.run([sys.executable, str(SCRIPT)], env=self.environ,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('git is missing (git). Install it with: sudo pacman -S --needed git', result.stdout)
        self.stub('git', '#!/bin/sh\nexit 0\n')
        result = subprocess.run([sys.executable, str(SCRIPT), '--brief'], env=self.environ,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual((result.returncode, result.stdout), (0, ''), result.stderr)

    def test_help_and_unknown_do_not_need_the_storage_helper(self):
        """A checkout that has the doctor but not the storage sibling still answers usage."""
        alone = self.root / 'alone'
        alone.mkdir()
        script = alone / 'groundwork-doctor.py'
        script.write_text(SCRIPT.read_text())
        script.chmod(0o755)
        help_result = subprocess.run([sys.executable, str(script), '--help'],
                                     capture_output=True, text=True, timeout=10)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn('groundwork-doctor', help_result.stdout)
        self.assertNotIn('Traceback', help_result.stderr)
        unknown = subprocess.run([sys.executable, str(script), '--nope'],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(unknown.returncode, 2, unknown.stderr)
        self.assertNotIn('Traceback', unknown.stderr)
        check = subprocess.run([sys.executable, str(script)],
                               env={'PATH': str(self.bin), 'HOME': self.environ['HOME']},
                               capture_output=True, text=True, timeout=10)
        self.assertEqual(check.returncode, 1, check.stderr)
        self.assertIn('groundwork-storage.py is missing from this checkout.', check.stdout)
        self.assertIn('groundwork-tmp.py is missing from this checkout.', check.stdout)
        self.assertNotIn('Traceback', check.stderr)
        brief = subprocess.run([sys.executable, str(script), '--brief'],
                               env={'PATH': str(self.bin), 'HOME': self.environ['HOME']},
                               capture_output=True, text=True, timeout=10)
        self.assertEqual(brief.returncode, 0, brief.stderr)
        self.assertEqual(brief.stdout, 'Temp storage: groundwork-tmp.py is missing from this checkout.\n'
                                       'Storage defaults: groundwork-storage.py is missing from this checkout.\n')

    def test_bad_usage_exits_2(self):
        result = subprocess.run([sys.executable, str(SCRIPT), '--nope'], env=self.environ,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)

    def test_setup_installs_the_tools_omarchy_does_not_ship(self):
        apply = (REPO / 'scripts/apply-layer.sh').read_text()
        self.assertIn('\ninstall_package lsof\n', apply)
        self.assertIn('omarchy pkg aur add shellcheck-bin', apply)
        self.assertIn('obsidian bw readelf lsof; do', apply)
        self.assertIn('sudo -n true 2>/dev/null; then', apply.split('install_shellcheck() {', 1)[1])
        self.assertEqual(dict((c, p) for c, p, _ in doctor.HOST_TOOLS)['shellcheck'], 'shellcheck-bin')


if __name__ == '__main__':
    unittest.main()
