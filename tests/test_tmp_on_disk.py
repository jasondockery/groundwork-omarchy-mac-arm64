"""/tmp on disk and scratch roots (decision 0022, issue #138).

Every system path comes from a private GROUNDWORK_TEST_ROOT and every command
from a private PATH, so the host's /tmp, fstab, tmpfiles and systemctl never
decide a result, and nothing here can mask, mount, or reboot anything.
"""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, REPO / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tmp = load('groundwork_tmp', 'groundwork-tmp.py')
doctor = load('groundwork_doctor', 'groundwork-doctor.py')

UPSTREAM_TMPFILES = 'q /tmp 1777 root root 10d\nq /var/tmp 1777 root root 30d\n'
# Reads the fixture's mask the way systemctl mask writes it.
FAKE_SYSTEMCTL = f'''#!{sys.executable}
import os, sys
if sys.argv[1:] != ['is-enabled', 'tmp.mount']:
    sys.exit(1)
unit = os.path.join(os.environ['GROUNDWORK_TEST_ROOT'], 'etc/systemd/system/tmp.mount')
if os.path.islink(unit) and os.readlink(unit) == '/dev/null':
    print('masked')
    sys.exit(1)
print('static')
'''


def mountinfo_line(number, point, kind):
    return f'{number} 1 0:{number} / {point} rw,relatime shared:{number} - {kind} {kind} rw\n'


def seed_system(root, tmp_kind=None, masked=True):
    """A machine root: btrfs /, /tmp on `tmp_kind` (None means no own mount), upstream tmpfiles."""
    lines = [mountinfo_line(20, '/', 'btrfs')]
    if tmp_kind:
        lines.append(mountinfo_line(21, '/tmp', tmp_kind))
    write(root / 'proc/self/mountinfo', ''.join(lines))
    write(root / 'usr/lib/tmpfiles.d/tmp.conf', '# See tmpfiles.d(5)\n' + UPSTREAM_TMPFILES)
    write(root / 'etc/fstab', 'UUID=x / btrfs noatime,subvol=@ 0 0\nUUID=x /home btrfs subvol=@home 0 0\n')
    unit = root / 'etc/systemd/system/tmp.mount'
    if unit.is_symlink():
        unit.unlink()
    if masked:
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.symlink_to('/dev/null')


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def fake_systemctl(bin_dir):
    path = bin_dir / 'systemctl'
    path.write_text(FAKE_SYSTEMCTL)
    path.chmod(0o755)


class TempStorageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='groundwork-tmp-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.system = self.root / 'system'
        self.home = self.root / 'home'
        (self.home / '.cache').mkdir(parents=True)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        fake_systemctl(self.bin)
        seed_system(self.system)
        self.environ = {'PATH': str(self.bin), 'HOME': str(self.home),
                        'XDG_CONFIG_HOME': str(self.home / '.config'),
                        'GROUNDWORK_TESTING': '1', 'GROUNDWORK_TEST_ROOT': str(self.system),
                        'GROUNDWORK_CODE_DIR': str(self.home / 'code')}

    def save(self, **choices):
        write(self.home / '.config/groundwork/omarchy.json', json.dumps(
            dict({'schema': 1, 'source': str(REPO), 'keyd': True, 'screen_sharing': True, 'obsidian_vault': 'v'}, **choices)))

    def tmp_cli(self, *args):
        result = subprocess.run([sys.executable, str(REPO / 'scripts/groundwork-tmp.py'), *args], env=self.environ,
                                capture_output=True, text=True, timeout=20)
        return result

    def report(self):
        return tmp.report(self.environ)

    # fstab: report a /tmp line, never write one.

    def test_fstab_tmp_line_is_reported_and_left_alone(self):
        fstab = self.system / 'etc/fstab'
        before = (fstab.read_text() + 'tmpfs /tmp tmpfs defaults,size=12G 0 0\n# tmpfs /tmp tmpfs x 0 0\n'
                  'tmpfs //tmp/ tmpfs defaults 0 0\n')
        fstab.write_text(before)
        result = self.tmp_cli('fstab-tmp')
        self.assertEqual((result.returncode, result.stdout),
                         (0, 'tmpfs /tmp tmpfs defaults,size=12G 0 0\ntmpfs //tmp/ tmpfs defaults 0 0\n'))
        problems, _ = self.report()
        self.assertTrue(any('/etc/fstab mounts /tmp' in p and 'remove that line yourself' in p for p in problems), problems)
        self.assertEqual(fstab.read_text(), before)

    def test_no_fstab_tmp_line_exits_1(self):
        self.assertEqual(self.tmp_cli('fstab-tmp').returncode, 1)
        (self.system / 'etc/fstab').unlink()
        self.assertEqual(self.tmp_cli('fstab-tmp').returncode, 1)

    def test_test_root_without_the_harness_guard_is_refused(self):
        del self.environ['GROUNDWORK_TESTING']
        self.assertEqual(self.tmp_cli('fstab-tmp').returncode, 64)

    # Doctor findings for /tmp.

    def test_disk_tmp_with_mask_and_upstream_ageing_is_clean(self):
        problems, summary = self.report()
        self.assertEqual(problems, [])
        self.assertEqual(summary, '/tmp on btrfs, tmp.mount masked (/tmp on disk on), tmpfiles ages /tmp after 10d, '
                                  '0 scratch roots')
        self.assertEqual(self.tmp_cli('tmp-backing').stdout, 'btrfs\n')

    def test_tmpfs_without_mask_names_the_fix(self):
        seed_system(self.system, tmp_kind='tmpfs', masked=False)
        self.assertEqual(self.report()[0], ['/tmp is tmpfs (RAM) and tmp.mount is not masked. '
                                            'Run groundwork-update (it masks tmp.mount), then reboot.'])

    def test_disk_tmp_without_mask_is_worded_by_its_backing(self):
        seed_system(self.system, masked=False)
        self.assertEqual(self.report()[0], ['/tmp is on btrfs now, but tmp.mount is not masked, so a later boot can '
                                            'mount it in RAM again. Run groundwork-update (it masks tmp.mount).'])

    def test_missing_or_failing_systemctl_is_an_unknown_mask_state(self):
        # A failing systemctl's output is not trusted, whatever it printed.
        for body in (None, f'#!{sys.executable}\nimport sys\nprint("static")\nsys.exit(4)\n'):
            with self.subTest(systemctl='missing' if body is None else 'fails'):
                (self.bin / 'systemctl').unlink(missing_ok=True)
                if body:
                    (self.bin / 'systemctl').write_text(body)
                    (self.bin / 'systemctl').chmod(0o755)
                problems, summary = self.report()
                self.assertEqual(len(problems), 1)
                self.assertIn('mask state is unknown', problems[0])
                self.assertNotIn('not masked', problems[0] + summary)
                self.assertIn('tmp.mount mask state unknown', summary)

    def test_custom_tmp_mount_unit_is_reported_and_not_masked(self):
        # What systemctl edit --full tmp.mount leaves behind; systemctl mask refuses to replace it.
        unit = self.system / 'etc/systemd/system/tmp.mount'
        unit.unlink()
        unit.write_text('[Mount]\nWhat=tmpfs\nWhere=/tmp\nType=tmpfs\nOptions=size=12G\n')
        result = self.tmp_cli('tmp-unit')
        self.assertEqual((result.returncode, result.stdout), (0, '/etc/systemd/system/tmp.mount\n'))
        problems, _ = self.report()
        self.assertEqual(len(problems), 1)
        self.assertIn('/etc/systemd/system/tmp.mount is a tmp.mount unit of its own', problems[0])
        self.assertIn('sudo systemctl revert tmp.mount', problems[0])
        unit.unlink()
        unit.symlink_to('/dev/null')
        self.assertEqual(self.tmp_cli('tmp-unit').returncode, 1)

    def test_masked_but_still_tmpfs_is_a_pending_reboot(self):
        (self.system / 'proc/self/mountinfo').write_text(
            mountinfo_line(20, '/', 'btrfs') + mountinfo_line(21, '/tmp', 'tmpfs'))
        problems, _ = self.report()
        self.assertEqual(len(problems), 1)
        self.assertIn('still tmpfs until the next reboot. Reboot to move /tmp to disk', problems[0])

    def test_opted_out_tmpfs_is_not_a_problem(self):
        seed_system(self.system, tmp_kind='tmpfs', masked=False)
        self.save(tmp_on_disk=False)
        problems, summary = self.report()
        self.assertEqual(problems, [])
        self.assertIn('off by choice', summary)

    def test_tmpfiles_override_or_lost_ageing_is_reported(self):
        write(self.system / 'etc/tmpfiles.d/tmp.conf', 'D /tmp 1777 root root -\n')
        problems, _ = self.report()
        self.assertEqual(len(problems), 1)
        self.assertIn('sudo rm /etc/tmpfiles.d/tmp.conf', problems[0])
        (self.system / 'etc/tmpfiles.d/tmp.conf').unlink()
        (self.system / 'usr/lib/tmpfiles.d/tmp.conf').write_text('q /tmp 1777 root root -\n')
        problems, _ = self.report()
        self.assertEqual(len(problems), 1)
        self.assertIn('does not give /tmp mode 1777 with an age (q /tmp 1777 root root -)', problems[0])

    # Scratch roots from omarchy.json.

    def test_scratch_root_is_created_once_with_mode_0700(self):
        self.save(scratch_dirs=['work-scratch'])
        target = self.home / '.cache/work-scratch'
        problems, _ = self.report()
        self.assertEqual(problems, [f'Scratch root {target} is missing. Run groundwork-update to create it.'])
        first = self.tmp_cli('apply-scratch')
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn('Created scratch root', first.stdout)
        self.assertEqual(target.stat().st_mode & 0o7777, 0o700)
        second = self.tmp_cli('apply-scratch')
        self.assertEqual((second.returncode, second.stdout, second.stderr), (0, '', ''))
        self.assertEqual(self.report(), ([], '/tmp on btrfs, tmp.mount masked (/tmp on disk on), '
                                             'tmpfiles ages /tmp after 10d, 1 scratch root'))

    def test_no_scratch_dirs_creates_nothing(self):
        self.save()
        self.assertEqual(self.tmp_cli('apply-scratch').returncode, 0)
        self.assertEqual(list((self.home / '.cache').iterdir()), [])

    def test_scratch_root_on_tmpfs_is_refused(self):
        self.save(scratch_dirs=['work-scratch'])
        with (self.system / 'proc/self/mountinfo').open('a') as mountinfo:
            mountinfo.write(mountinfo_line(30, str(self.home / '.cache'), 'tmpfs'))
        result = self.tmp_cli('apply-scratch')
        self.assertEqual(result.returncode, 1)
        self.assertIn('which is on tmpfs (RAM)', result.stderr)
        self.assertFalse((self.home / '.cache/work-scratch').exists())
        self.assertIn('on tmpfs (RAM)', self.report()[0][0])

    def test_scratch_root_in_a_git_work_tree_is_refused(self):
        clone = self.root / 'clone'
        (clone / '.git').mkdir(parents=True)
        (self.home / '.cache').rmdir()
        (self.home / '.cache').symlink_to(clone)
        self.save(scratch_dirs=['work-scratch'])
        result = self.tmp_cli('apply-scratch')
        self.assertEqual(result.returncode, 1)
        self.assertIn(f'resolves into the git work tree at {clone}', result.stderr)
        self.assertFalse((clone / 'work-scratch').exists())

    def test_scratch_root_in_code_or_a_synced_folder_is_refused(self):
        for place in ('code', 'Shared'):
            with self.subTest(place=place):
                cache = self.home / '.cache'
                if cache.is_symlink():
                    cache.unlink()
                elif cache.exists():
                    cache.rmdir()
                (self.home / place).mkdir(exist_ok=True)
                cache.symlink_to(self.home / place)
                self.save(scratch_dirs=['work-scratch'])
                result = self.tmp_cli('apply-scratch')
                self.assertEqual(result.returncode, 1)
                self.assertIn('where clones live' if place == 'code' else 'overlaps the Syncthing folder', result.stderr)
                self.assertFalse((self.home / place / 'work-scratch').exists())

    def test_scratch_folders_follow_groundwork_syncthing(self):
        syncthing = load('groundwork_syncthing_paths', 'groundwork-syncthing.py')
        folders = tmp.synced_folders(self.home)
        self.assertEqual(folders, [self.home / Path(syncthing.shared_path()).relative_to(Path.home()),
                                   self.home / Path(syncthing.freetube_path()).relative_to(Path.home())])

    def test_scratch_names_must_be_one_plain_segment(self):
        for bad in ('..', '.', '.hidden', 'a/b', '', 'x' * 65, 7):
            with self.subTest(bad=bad):
                self.save(scratch_dirs=[bad])
                result = self.tmp_cli('apply-scratch')
                self.assertEqual(result.returncode, 1)
                self.assertIn('Invalid scratch_dirs', result.stderr)
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), ['.cache', '.config'])
        self.assertEqual(list((self.home / '.cache').iterdir()), [])

    # The doctor's section.

    def test_doctor_reports_temp_storage_with_a_next_step(self):
        seed_system(self.system, tmp_kind='tmpfs', masked=False)
        for name in ('lsof', 'rg', 'jq', 'shellcheck', 'ssh', 'git'):
            (self.bin / name).write_text('#!/bin/sh\nexit 0\n')
            (self.bin / name).chmod(0o755)
        (self.bin / 'ps').write_text('#!/bin/sh\necho "Tue Nov  3 21:38:13 2026"\n')
        (self.bin / 'ps').chmod(0o755)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = doctor.main([], self.environ)
        self.assertEqual(status, 1)
        self.assertIn('Host tools: ok', output.getvalue())
        self.assertIn('Temp storage: 1 problem\n  /tmp is tmpfs (RAM) and tmp.mount is not masked', output.getvalue())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(doctor.main(['--brief'], self.environ), 0)
        self.assertEqual(output.getvalue().splitlines(),
                         ['Temp storage: /tmp is tmpfs (RAM) and tmp.mount is not masked. '
                          'Run groundwork-update (it masks tmp.mount), then reboot.'])


if __name__ == '__main__':
    unittest.main()
