"""/tmp on disk: mask tmp.mount, never write fstab, keep tmpfiles aging, optional scratch.

Protects: apply putting /tmp on a RAM tmpfs again, writing fstab, copying a
tmpfiles unit, or creating scratch under a git clone or ~/code.
Boundary exercised: scripts/groundwork-tmp.py apply and doctor against a private
root, plus apply-layer.sh calling that helper.
Distinct value: no other test covers tmp.mount, fstab /tmp, or override-config scratch.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'scripts/groundwork-tmp.py'
APPLY = REPO / 'scripts/apply-layer.sh'
spec = importlib.util.spec_from_file_location('groundwork_tmp', SCRIPT)
tmp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tmp)

UPSTREAM_TMPFILES = 'q /tmp 1777 root root 10d\n'
CACHEDIR_SIGNATURE = 'Signature: 8a477f597d28d172789f06886806bc55'


class FakeRun:
    def __init__(self, host_root):
        self.root = Path(host_root)
        self.calls = []
        self.fstype = 'ext4'
        self.mask_status = 0

    def __call__(self, argv):
        self.calls.append(list(argv))
        if argv[:1] == ['findmnt']:
            return subprocess.CompletedProcess(argv, 0, self.fstype + '\n', '')
        if argv[:3] == ['sudo', 'systemctl', 'mask'] and argv[3:] == ['tmp.mount']:
            if self.mask_status:
                return subprocess.CompletedProcess(argv, self.mask_status, '', 'mask failed\n')
            unit = self.root / 'etc/systemd/system/tmp.mount'
            unit.parent.mkdir(parents=True, exist_ok=True)
            if unit.exists() or unit.is_symlink():
                unit.unlink()
            unit.symlink_to('/dev/null')
            return subprocess.CompletedProcess(argv, 0, '', '')
        raise AssertionError('unexpected command: ' + repr(argv))


class TmpTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='groundwork-tmp-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        (self.root / 'usr/lib/tmpfiles.d').mkdir(parents=True)
        (self.root / 'usr/lib/tmpfiles.d/tmp.conf').write_text(UPSTREAM_TMPFILES)
        self.environ = {
            'HOME': str(self.home),
            'PATH': '/usr/bin:/bin',
            'GROUNDWORK_TESTING': '1',
            'GROUNDWORK_TEST_ROOT': str(self.root),
            'XDG_CONFIG_HOME': str(self.home / '.config'),
            'XDG_CACHE_HOME': str(self.home / '.cache'),
        }
        self.run = FakeRun(self.root)

    def host(self):
        return tmp.Host(self.environ, run=self.run)

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = tmp.main(list(args), self.environ, run=self.run)
        return status, out.getvalue(), err.getvalue()

    def overrides(self):
        path = self.home / '.config/groundwork/overrides.json'
        return json.loads(path.read_text()) if path.is_file() else None

    def add_scratch(self, name='scratch', reason='disk-backed workspace under ~/.cache'):
        path = self.home / '.config/groundwork/overrides.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            'schema': 1,
            'overrides': [
                {'id': 'scratch', 'value': name, 'reason': reason},
            ],
        }
        path.write_text(json.dumps(document, indent=2) + '\n')

    def mask(self):
        unit = self.root / 'etc/systemd/system/tmp.mount'
        unit.parent.mkdir(parents=True, exist_ok=True)
        if unit.exists() or unit.is_symlink():
            unit.unlink()
        unit.symlink_to('/dev/null')

    def test_apply_masks_tmp_mount_without_unmounting_or_writing_fstab(self):
        status, out, err = self.invoke('apply')
        self.assertEqual((status, err), (0, ''), out)
        self.assertIn(['sudo', 'systemctl', 'mask', 'tmp.mount'], self.run.calls)
        self.assertFalse(any('--now' in call or 'umount' in call or 'fstab' in call
                             for call in self.run.calls))
        self.assertTrue((self.root / 'etc/systemd/system/tmp.mount').is_symlink())
        self.assertEqual(os.readlink(self.root / 'etc/systemd/system/tmp.mount'), '/dev/null')
        self.assertFalse((self.root / 'etc/fstab').exists())
        self.assertFalse((self.root / 'etc/tmpfiles.d/tmp.conf').exists())
        self.assertEqual((self.root / 'usr/lib/tmpfiles.d/tmp.conf').read_text(), UPSTREAM_TMPFILES)
        recorded = self.overrides()['overrides'][0]
        self.assertEqual(recorded['id'], 'tmp.mount')
        self.assertEqual(recorded['value'], 'masked')
        self.assertTrue(recorded['reason'].strip())
        self.assertIn('Masked tmp.mount', out)
        self.assertIn('/etc/fstab was not changed', out)
        self.assertIn('systemd-tmpfiles still ages /tmp', out)

    def test_second_apply_is_a_noop_for_the_mask(self):
        self.assertEqual(self.invoke('apply')[0], 0)
        self.run.calls.clear()
        status, out, err = self.invoke('apply')
        self.assertEqual((status, err), (0, ''), out)
        self.assertNotIn(['sudo', 'systemctl', 'mask', 'tmp.mount'], self.run.calls)
        self.assertIn('already masked', out)

    def test_needed_is_true_only_while_unmasked(self):
        self.assertEqual(self.invoke('needed')[0], 0)
        self.mask()
        self.assertEqual(self.invoke('needed')[0], 1)

    def test_fstab_tmp_line_refuses_apply_and_changes_nothing(self):
        fstab = self.root / 'etc/fstab'
        fstab.parent.mkdir(parents=True, exist_ok=True)
        fstab.write_text('UUID=root / btrfs defaults 0 1\ntmpfs /tmp tmpfs defaults 0 0\n')
        before = fstab.read_text()
        status, _, err = self.invoke('apply')
        self.assertEqual(status, 1)
        self.assertIn('fstab has a /tmp line', err)
        self.assertNotIn(['sudo', 'systemctl', 'mask', 'tmp.mount'], self.run.calls)
        self.assertEqual(fstab.read_text(), before)
        self.assertFalse((self.root / 'etc/systemd/system/tmp.mount').exists())
        self.assertIsNone(self.overrides())

    def test_fstab_comment_is_not_a_tmp_mount(self):
        fstab = self.root / 'etc/fstab'
        fstab.parent.mkdir(parents=True, exist_ok=True)
        fstab.write_text('# tmpfs /tmp tmpfs defaults 0 0\nUUID=root / btrfs defaults 0 1\n')
        status, _, err = self.invoke('apply')
        self.assertEqual((status, err), (0, ''))
        self.assertIn(['sudo', 'systemctl', 'mask', 'tmp.mount'], self.run.calls)
        self.assertEqual(fstab.read_text(), '# tmpfs /tmp tmpfs defaults 0 0\nUUID=root / btrfs defaults 0 1\n')

    def test_scratch_is_created_from_local_config_under_cache(self):
        self.add_scratch('build')
        status, out, err = self.invoke('apply')
        self.assertEqual((status, err), (0, ''), out)
        scratch = self.home / '.cache/build'
        self.assertTrue(scratch.is_dir())
        self.assertFalse(scratch.is_symlink())
        self.assertIn(CACHEDIR_SIGNATURE, (scratch / 'CACHEDIR.TAG').read_text())
        self.assertIn(str(scratch), out)
        ids = [item['id'] for item in self.overrides()['overrides']]
        self.assertEqual(ids.count('scratch'), 1)
        self.assertIn('tmp.mount', ids)

    def test_scratch_name_is_not_invented(self):
        self.assertEqual(self.invoke('apply')[0], 0)
        cache = self.home / '.cache'
        self.assertFalse(cache.exists())
        self.assertEqual([item['id'] for item in self.overrides()['overrides']], ['tmp.mount'])

    def test_scratch_under_code_is_refused(self):
        self.environ['XDG_CACHE_HOME'] = str(self.home / 'code' / 'cache')
        self.add_scratch()
        status, _, err = self.invoke('apply')
        self.assertEqual(status, 1)
        self.assertIn('~/code', err)
        self.assertNotIn(['sudo', 'systemctl', 'mask', 'tmp.mount'], self.run.calls)
        self.assertFalse((self.home / 'code/cache/scratch').exists())

    def test_scratch_inside_a_git_clone_is_refused(self):
        cache = self.home / '.cache'
        (cache / '.git').mkdir(parents=True)
        self.add_scratch()
        status, _, err = self.invoke('apply')
        self.assertEqual(status, 1)
        self.assertIn('git clone', err)
        self.assertFalse((cache / 'scratch').exists())

    def test_reserved_or_path_scratch_names_are_refused(self):
        for name in ('groundwork', '../scratch', 'a/b', '', '.', '..'):
            with self.subTest(name=name):
                self.add_scratch(name)
                status, _, err = self.invoke('apply')
                self.assertEqual(status, 1, err)
                self.assertIn('not allowed', err)
                self.assertNotIn(['sudo', 'systemctl', 'mask', 'tmp.mount'], self.run.calls)

    def test_existing_tmp_mount_reason_is_kept(self):
        path = self.home / '.config/groundwork/overrides.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            'schema': 1,
            'overrides': [
                {'id': 'tmp.mount', 'value': 'masked', 'reason': 'machine-local reason'},
                {'id': 'other', 'value': 'x', 'reason': 'keep me'},
            ],
        }, indent=2) + '\n')
        self.assertEqual(self.invoke('apply')[0], 0)
        items = {item['id']: item for item in self.overrides()['overrides']}
        self.assertEqual(items['tmp.mount']['reason'], 'machine-local reason')
        self.assertEqual(items['other'], {'id': 'other', 'value': 'x', 'reason': 'keep me'})

    def test_doctor_reports_each_problem_with_a_fix(self):
        status, out, err = self.invoke('doctor')
        self.assertEqual((status, err), (1, ''))
        self.assertIn('tmp.mount is not masked', out)
        self.assertIn('Apply this layer, then reboot', out)
        self.assertIn('override config does not record', out)
        self.mask()
        self.run.fstype = 'tmpfs'
        path = self.home / '.config/groundwork/overrides.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            'schema': 1,
            'overrides': [{'id': 'tmp.mount', 'value': 'masked', 'reason': 'masked for RAM'}],
        }) + '\n')
        status, out, _ = self.invoke('doctor')
        self.assertEqual(status, 1)
        self.assertIn('still tmpfs', out)
        self.assertIn('Reboot', out)
        self.run.fstype = 'btrfs'
        status, out, _ = self.invoke('doctor')
        self.assertEqual(status, 0)
        self.assertIn('tmp: ok (', out)
        self.assertIn('tmp.mount masked', out)
        self.assertEqual(self.invoke('doctor', '--brief'), (0, '', ''))

    def test_doctor_reports_fstab_and_tmpfiles_override(self):
        self.mask()
        fstab = self.root / 'etc/fstab'
        fstab.parent.mkdir(parents=True, exist_ok=True)
        fstab.write_text('tmpfs /tmp tmpfs defaults 0 0\n')
        (self.root / 'etc/tmpfiles.d').mkdir(parents=True)
        (self.root / 'etc/tmpfiles.d/tmp.conf').write_text('q /tmp 1777 root root -\n')
        status, out, _ = self.invoke('doctor')
        self.assertEqual(status, 1)
        self.assertIn('fstab has a /tmp line', out)
        self.assertIn('/etc/tmpfiles.d/tmp.conf overrides', out)
        self.assertIn('does not ship a copy', out)

    def test_doctor_reports_missing_configured_scratch(self):
        self.mask()
        self.add_scratch('build')
        path = self.home / '.config/groundwork/overrides.json'
        document = json.loads(path.read_text())
        document['overrides'].insert(0, {
            'id': 'tmp.mount', 'value': 'masked', 'reason': 'masked for RAM',
        })
        path.write_text(json.dumps(document) + '\n')
        status, out, _ = self.invoke('doctor')
        self.assertEqual(status, 1)
        self.assertIn('scratch directory', out)
        self.assertIn('missing', out)
        self.assertEqual(self.invoke('apply')[0], 0)
        status, out, _ = self.invoke('doctor')
        self.assertEqual(status, 0, out)
        self.assertIn('scratch', out)

    def test_doctor_brief_prints_one_prefixed_line_per_problem(self):
        status, out, err = self.invoke('doctor', '--brief')
        self.assertEqual((status, err), (0, ''))
        lines = out.splitlines()
        self.assertGreaterEqual(len(lines), 2)
        self.assertTrue(all(line.startswith('tmp: ') for line in lines))

    def test_apply_layer_requires_and_runs_the_helper(self):
        text = APPLY.read_text()
        self.assertIn('groundwork-tmp.py" apply', text)
        self.assertIn('"$root/scripts/groundwork-tmp.py"', text.split('Fail before installing')[1].split('}')[0])
        self.assertIn('tmp_mount_masked || return 0', text)
        tmp_block = text.split('# /tmp on disk')[1].split('Omarchy owns Obsidian')[0]
        self.assertIn('Never write /etc/fstab for /tmp', tmp_block)
        self.assertNotIn('>>', tmp_block)
        self.assertNotIn('tee', tmp_block)
        self.assertNotIn('systemctl mask', tmp_block)
        self.assertNotIn('tmpfiles.d', tmp_block)

    def test_bad_usage_exits_2(self):
        self.assertEqual(self.invoke('--nope')[0], 2)
        result = subprocess.run([sys.executable, str(SCRIPT), '--nope'], env=self.environ,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
