"""TRIM through LUKS and plocate prunes (decision 0023).

Every command is a stand-in. Nothing here calls cryptsetup, systemctl, or btrfs
on the machine running the tests.
"""
import importlib.util
import io
import os
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'scripts/groundwork-storage.py'
spec = importlib.util.spec_from_file_location('groundwork_storage', SCRIPT)
storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage)


class World:
    def __init__(self, root, home):
        self.root = Path(root)
        self.home = str(home)
        self.calls = []
        self.gran = '0B'
        self.timer = 'disabled'
        self.mapper = 'root'
        self.kind = 'crypt'
        self.mount_options = 'compress=zstd:3'
        self.reloaded = False
        self.show = 'ok'
        self.fail = set()
        self.fail_text = {}
        self.missing = set()
        self.keep_gran = False
        self.units = {'fstrim.timer', 'plocate-updatedb.service'}
        unit = self.root / 'usr/lib/systemd/system'
        unit.mkdir(parents=True)
        for name in self.units:
            (unit / name).write_text('[Unit]\n')

    def run(self, args, sudo=False, timeout=10, terminal=False):
        self.calls.append({'sudo': sudo, 'args': list(args), 'timeout': timeout, 'terminal': terminal})
        if args[0] in self.missing:
            raise FileNotFoundError(args[0])
        if args[0] in self.fail:
            return storage.Result(1, '', self.fail_text.get(args[0], f'{args[0]} failed'))
        if args[0] == 'findmnt':
            if self.mapper == 'missing':
                raise FileNotFoundError('findmnt')
            if self.mapper == 'failed':
                return storage.Result(1, '', 'findmnt failed')
            if self.mapper == 'root':
                source = '/dev/mapper/root'
            elif self.mapper == 'subvol':
                source = '/dev/mapper/root[/@]'
            else:
                source = self.mapper
            line = f'{source} {self.mount_options}' if self.mount_options else source
            return storage.Result(0, line + '\n')
        if args[0] == 'lsblk':
            if self.kind == 'missing':
                raise FileNotFoundError('lsblk')
            field = args[2]
            if field == 'TYPE':
                return storage.Result(0, self.kind + '\n')
            if field == 'DISC-GRAN':
                return storage.Result(0, self.gran + '\n')
        if args[:2] == ['systemctl', 'is-enabled']:
            if self.timer == 'enabled':
                return storage.Result(0, 'enabled\n')
            if self.timer in ('enabled-runtime', 'static', 'indirect'):
                return storage.Result(0, self.timer + '\n')
            if self.timer == 'masked':
                return storage.Result(1, 'masked\n')
            if self.timer == 'unknown':
                return storage.Result(1, 'unexpected\n')
            if self.timer == 'timed-out':
                return storage.Result(124, '', 'timed out')
            return storage.Result(1, 'disabled\n')
        if args[:4] == ['systemctl', 'enable', '--now', 'fstrim.timer']:
            self.timer = 'enabled'
            return storage.Result(0, '')
        if args == ['systemctl', 'daemon-reload']:
            self.reloaded = True
            return storage.Result(0, '')
        if args[:4] == ['systemctl', 'show', '-p', 'DropInPaths']:
            if self.show == 'missing':
                raise FileNotFoundError('systemctl')
            if self.show == 'failed':
                return storage.Result(1, '', 'systemctl show failed')
            if not self.reloaded:
                return storage.Result(0, '')
            return storage.Result(0, str(self.root / storage.DROPIN_RELATIVE) + '\n')
        if args[0] == 'cryptsetup':
            if not self.keep_gran:
                self.gran = '512B'
            return storage.Result(0, '')
        if args[0] == 'install':
            destination = Path(args[-1])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(Path(args[-2]).read_text(encoding='utf-8'), encoding='utf-8')
            destination.chmod(0o644)
            return storage.Result(0, '')
        raise AssertionError(args)

    def machine(self, allow_live=False):
        return storage.Machine({'HOME': self.home, 'PATH': '/nonexistent'}, self.root, self.run, allow_live=allow_live)

    def names(self, sudo=None):
        found = []
        for call in self.calls:
            if sudo is None or call['sudo'] is sudo:
                found.append(call['args'][0])
        return found


class StorageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='groundwork-storage-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.world = World(self.root, self.home)
        terminal = mock.patch.object(storage, 'terminal_available', return_value=True)
        terminal.start()
        self.addCleanup(terminal.stop)

    def apply(self):
        output = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = storage.apply_storage(self.world.machine())
        return status, output.getvalue(), errors.getvalue()

    def report(self, brief=False):
        text, failed = storage.format_report(storage.gather(self.world.machine()), brief=brief)
        return text, failed

    def test_template_lists_only_the_added_prunes(self):
        text = storage.TEMPLATE.read_text(encoding='utf-8')
        comments = [line for line in text.splitlines() if line.startswith('#')]
        settings = [line for line in text.splitlines() if line and not line.startswith('#')]
        self.assertEqual(comments, [
            '# The empty ExecStart clears the package command so the next line is the only start.',
            '# Skip build output and large dependency trees from heavy JS monorepo test runs.',
        ])
        self.assertEqual(settings[0], '[Service]')
        self.assertEqual(settings[1], 'ExecStart=')
        self.assertIn('--add-prunenames "node_modules .turbo .next"', settings[2])
        self.assertIn('--add-prunepaths "@HOME@/.t3/worktrees @HOME@/.local/share/pnpm"', settings[2])
        body = text.replace('--add-prunenames', '').replace('--add-prunepaths', '')
        self.assertNotIn('--prunenames', body)
        self.assertNotIn('--prunepaths', body)
        self.assertNotIn('.git', text)
        account = 'ja' + 'son'
        self.assertNotIn(account, text.lower())
        self.assertNotIn('SW' + 'ORPS', text)
        for path in (SCRIPT, storage.TEMPLATE, REPO / 'docs/decisions/0023-storage-defaults.md', Path(__file__)):
            source = path.read_text(encoding='utf-8')
            self.assertNotIn('SW' + 'ORPS', source)
            self.assertNotIn('/home/' + account, source)
            self.assertNotIn(account, source.lower())

    def test_apply_sets_each_default_once(self):
        status, output, errors = self.apply()
        self.assertEqual(status, 0, errors)
        self.assertIn('Discard reveals which encrypted blocks are free.', output)
        self.assertIn('sudo cryptsetup refresh --allow-discards --persistent root', output)
        self.assertNotIn('crypttab', output)
        cryptsetup = [call for call in self.world.calls if call['args'][0] == 'cryptsetup']
        self.assertEqual(cryptsetup, [{
            'sudo': True,
            'args': ['cryptsetup', 'refresh', '--allow-discards', '--persistent', 'root'],
            'timeout': storage.CRYPTSETUP_SECONDS,
            'terminal': True,
        }])
        self.assertLess(storage.CRYPTSETUP_SECONDS, 900)
        self.assertGreaterEqual(storage.CRYPTSETUP_SECONDS, 300)
        self.assertIn('The passphrase wait is 300 seconds.', output)
        enable = [call for call in self.world.calls if call['args'][:3] == ['systemctl', 'enable', '--now']]
        self.assertEqual(enable[0]['args'], ['systemctl', 'enable', '--now', 'fstrim.timer'])
        self.assertEqual(enable[0]['timeout'], storage.FSTRIM_SECONDS)
        self.assertLess(storage.FSTRIM_SECONDS, 900)
        self.assertFalse(enable[0]['terminal'])
        crypt_at = next(index for index, call in enumerate(self.world.calls) if call['args'][0] == 'cryptsetup')
        enable_at = next(index for index, call in enumerate(self.world.calls)
                         if call['args'][:3] == ['systemctl', 'enable', '--now'])
        self.assertLess(crypt_at, enable_at)
        dropin = self.root / storage.DROPIN_RELATIVE
        rendered = dropin.read_text(encoding='utf-8')
        self.assertEqual(rendered, storage.render_dropin(storage.TEMPLATE.read_text(encoding='utf-8'), str(self.home)))
        self.assertNotIn('@HOME@', rendered)
        self.assertNotIn('ja' + 'son', rendered)
        self.assertEqual(stat.S_IMODE(dropin.stat().st_mode), 0o644)
        self.assertFalse((self.root / 'etc/updatedb.conf').exists())
        install = [call for call in self.world.calls if call['args'][0] == 'install']
        self.assertEqual(install[0]['args'][1:8], ['-D', '-o', 'root', '-g', 'root', '-m', '0644'])
        self.assertTrue(install[0]['sudo'])
        self.assertIn(
            {'sudo': True, 'args': ['systemctl', 'daemon-reload'], 'timeout': 30, 'terminal': False},
            self.world.calls)
        self.assertNotIn('btrfs', self.world.names())
        self.assertNotIn('mount', self.world.names())
        text, failed = self.report()
        self.assertFalse(failed)
        self.assertIn('TRIM through LUKS: ' + storage.TRIM_OK, text)
        self.assertIn(str(self.home / '.t3/worktrees'), text)
        self.assertIn(str(self.home / '.local/share/pnpm'), text)
        self.assertIn(storage.COMPRESSION_OK, text)
        self.world.calls.clear()
        status, output, errors = self.apply()
        self.assertEqual((status, output, errors), (0, '', ''))
        self.assertNotIn(True, [call['sudo'] for call in self.world.calls])
        self.assertNotIn('cryptsetup', self.world.names())
        self.assertNotIn('install', self.world.names())
        self.assertNotIn('btrfs', self.world.names())

    def test_doctor_is_read_only_and_names_each_gap(self):
        self.world.calls.clear()
        text, failed = self.report()
        self.assertTrue(failed)
        self.assertIn('discard granularity is 0B', text)
        self.assertIn('sudo cryptsetup refresh --allow-discards --persistent root', text)
        self.assertIn('fstrim.timer stays disabled until discards are on', text)
        self.assertNotIn('systemctl enable --now fstrim.timer', text)
        self.assertIn('node_modules', text)
        self.assertIn(str(self.home / '.local/share/pnpm'), text)
        self.assertIn(storage.COMPRESSION_OK, text)
        self.assertNotIn('zstd:1', text)
        self.assertNotIn('btrfs-progs', text)
        self.assertNotIn('cryptsetup', self.world.names())
        self.assertNotIn('install', self.world.names())
        self.assertTrue(all(call['sudo'] is False for call in self.world.calls))
        self.assertFalse(any(call['args'][:2] == ['systemctl', 'enable'] for call in self.world.calls))
        self.assertNotIn('btrfs', self.world.names())
        brief, brief_failed = self.report(brief=True)
        self.assertTrue(brief_failed)
        self.assertTrue(all(line.startswith(('TRIM through LUKS: ', 'Locate index: ', 'Compression: '))
                            for line in brief.splitlines()))

    def test_other_root_device_is_not_opened(self):
        self.world.mapper = '/dev/nvme0n1p2'
        status, _, _ = self.apply()
        self.assertEqual(status, 0)
        self.assertNotIn('cryptsetup', self.world.names())
        self.assertFalse(any(call['args'][:3] == ['systemctl', 'enable', '--now'] for call in self.world.calls))
        text, _failed = self.report()
        self.assertIn('root is /dev/nvme0n1p2, not /dev/mapper/root, so TRIM through LUKS does not apply', text)
        self.world.mapper = '/dev/mapper/luks-other'
        self.world.calls.clear()
        status, _, errors = self.apply()
        self.assertEqual(status, 0, errors)
        self.assertNotIn('cryptsetup', self.world.names())
        self.assertFalse(any(call['args'][:3] == ['systemctl', 'enable', '--now'] for call in self.world.calls))
        self.world.mapper = 'root'
        self.world.kind = 'disk'
        self.world.calls.clear()
        text, _failed = self.report()
        self.assertIn('/dev/mapper/root is type disk, not crypt, so TRIM through LUKS does not apply', text)
        status, _, errors = self.apply()
        self.assertEqual(status, 0, errors)
        self.assertNotIn('cryptsetup', self.world.names())
        self.assertFalse(any(call['args'][:3] == ['systemctl', 'enable', '--now'] for call in self.world.calls))

    def test_btrfs_subvolume_suffix_still_enables_discards(self):
        self.world.mapper = 'subvol'
        self.world.timer = 'enabled'
        self.world.units = set()
        status, _, errors = self.apply()
        self.assertEqual(status, 0, errors)
        self.assertIn('cryptsetup', self.world.names())

    def test_nonzero_granularity_and_enabled_timer_skip_privilege(self):
        self.world.gran = '512B'
        self.world.timer = 'enabled'
        rendered = storage.render_dropin(storage.TEMPLATE.read_text(encoding='utf-8'), str(self.home))
        dropin = self.root / storage.DROPIN_RELATIVE
        dropin.parent.mkdir(parents=True)
        dropin.write_text(rendered)
        dropin.chmod(0o644)
        self.world.reloaded = True
        self.assertFalse(storage.needs_privilege(self.world.machine()))
        self.world.calls.clear()
        status, output, _ = self.apply()
        self.assertEqual((status, output), (0, ''))
        self.assertFalse(any(call['sudo'] for call in self.world.calls))

    def test_a_mount_without_zstd3_is_not_called_zstd3(self):
        self.world.mount_options = 'defaults'
        status, _, errors = self.apply()
        self.assertEqual(status, 0, errors)
        self.assertFalse((self.home / 'code').exists())
        self.assertNotIn('btrfs', self.world.names())
        text, failed = self.report()
        self.assertFalse(failed)
        self.assertNotIn(storage.COMPRESSION_OK, text)
        self.assertIn('does not set compress=zstd:3', text)
        self.assertNotIn('btrfs-progs', text)

    def test_unloaded_dropin_is_loaded_again(self):
        self.world.gran = '512B'
        self.world.timer = 'enabled'
        dropin = self.root / storage.DROPIN_RELATIVE
        dropin.parent.mkdir(parents=True)
        dropin.write_text(storage.render_dropin(storage.TEMPLATE.read_text(encoding='utf-8'), str(self.home)))
        dropin.chmod(0o644)
        self.assertTrue(storage.needs_privilege(self.world.machine()))
        text, failed = self.report()
        self.assertTrue(failed)
        self.assertIn('systemd has not loaded it', text)
        status, _, errors = self.apply()
        self.assertEqual(status, 0, errors)
        self.assertTrue(self.world.reloaded)
        self.assertIn(
            {'sudo': True, 'args': ['systemctl', 'daemon-reload'], 'timeout': 30, 'terminal': False},
            self.world.calls)
        self.assertFalse(storage.needs_privilege(self.world.machine()))

    def test_ten_bytes_is_not_treated_as_off(self):
        self.world.gran = '10B'
        self.world.timer = 'enabled'
        self.assertFalse(any(kind == 'trim' for kind, _, _ in storage.privileged_actions(storage.gather(self.world.machine()))))

    def test_missing_plocate_and_fstrim_are_reported_and_not_forced(self):
        for name in ('fstrim.timer', 'plocate-updatedb.service'):
            (self.root / 'usr/lib/systemd/system' / name).unlink()
        self.world.gran = '512B'
        text, failed = self.report()
        self.assertTrue(failed)
        self.assertIn('sudo pacman -S --needed util-linux', text)
        self.assertIn('sudo pacman -S --needed plocate', text)
        self.assertFalse(storage.needs_privilege(self.world.machine()))

    def test_unsafe_dropin_is_replaced(self):
        self.world.gran = '512B'
        self.world.timer = 'enabled'
        dropin = self.root / storage.DROPIN_RELATIVE
        dropin.parent.mkdir(parents=True)
        dropin.write_text(storage.render_dropin(storage.TEMPLATE.read_text(encoding='utf-8'), str(self.home)))
        dropin.chmod(0o666)
        self.assertTrue(storage.needs_privilege(self.world.machine()))
        status, _, errors = self.apply()
        self.assertEqual(status, 0, errors)
        self.assertEqual(stat.S_IMODE(dropin.stat().st_mode), 0o644)

    def test_symlink_directory_is_left_alone(self):
        target = self.root / 'elsewhere'
        target.mkdir()
        (self.home / 'code').symlink_to(target)
        text, _failed = self.report()
        self.assertNotIn('symlink', text)
        self.assertIn(storage.COMPRESSION_OK, text)
        self.apply()
        self.assertNotIn('btrfs', self.world.names())
        self.assertTrue((self.home / 'code').is_symlink())

    def test_spaced_home_does_not_block_trim(self):
        self.world.home = '/home/bad name'
        self.world.machine = lambda allow_live=False: storage.Machine(
            {'HOME': self.world.home}, self.root, self.world.run, allow_live=allow_live)
        status, _, errors = self.apply()
        self.assertEqual(status, 1)
        self.assertIn('not a plain absolute path', errors)
        self.assertIn('cryptsetup', self.world.names())
        self.assertNotIn('install', self.world.names())
        text, failed = self.report()
        self.assertTrue(failed)
        self.assertIn('not a plain absolute path', text)

    def test_failed_refresh_is_a_failure(self):
        self.world.fail.add('cryptsetup')
        status, _, errors = self.apply()
        self.assertEqual(status, 1)
        self.assertIn('trim was not applied', errors)
        self.assertIn('Leaving fstrim.timer disabled', errors)
        self.assertEqual(self.world.gran, '0B')
        self.assertEqual(self.world.timer, 'disabled')
        self.assertFalse(any(call['args'][:3] == ['systemctl', 'enable', '--now'] for call in self.world.calls))

    def test_missing_cryptsetup_does_not_enable_the_timer(self):
        self.world.missing.add('cryptsetup')
        status, _, errors = self.apply()
        self.assertEqual(status, 1, errors)
        self.assertIn('trim was not applied', errors)
        self.assertEqual(self.world.timer, 'disabled')
        self.assertEqual(self.world.gran, '0B')
        self.assertFalse(any(call['args'][:3] == ['systemctl', 'enable', '--now'] for call in self.world.calls))

    def test_timer_is_enabled_only_after_discards_are_on(self):
        self.world.gran = '512B'
        text, failed = self.report()
        self.assertTrue(failed)
        self.assertIn('sudo systemctl enable --now fstrim.timer', text)
        kinds = [kind for kind, _, _ in storage.privileged_actions(storage.gather(self.world.machine()))]
        self.assertIn('fstrim', kinds)
        self.assertNotIn('trim', kinds)

    def test_unconfirmed_dropin_is_not_reported_ok(self):
        self._ready_except_dropin_load()
        self.world.show = 'failed'
        text, failed = self.report()
        self.assertTrue(failed)
        self.assertNotIn('Locate index: ok', text)
        self.assertIn('could not show whether the plocate drop-in is loaded', text)
        self.assertTrue(storage.needs_privilege(self.world.machine()))
        status, _, errors = self.apply()
        self.assertEqual(status, 0, errors)
        self.assertIn(
            {'sudo': True, 'args': ['systemctl', 'daemon-reload'], 'timeout': 30, 'terminal': False},
            self.world.calls)

    def test_missing_systemctl_show_is_not_a_loaded_dropin(self):
        self._ready_except_dropin_load()
        self.world.show = 'missing'
        text, failed = self.report()
        self.assertTrue(failed)
        self.assertNotIn('Locate index: ok', text)
        self.assertIn('systemctl is missing, so the plocate drop-in was not confirmed', text)
        self.assertFalse(storage.needs_privilege(self.world.machine()))

    def test_doctor_probes_share_a_budget_under_the_report_deadline(self):
        update = (REPO / 'scripts/groundwork-update.py').read_text(encoding='utf-8')
        self.assertIn("('Host tools check', 'groundwork-doctor.py', ('--brief',), 15)", update)
        self.assertLess(storage.DOCTOR_SECONDS, 15)
        doctor = (REPO / 'scripts/groundwork-doctor.py').read_text(encoding='utf-8')
        self.assertIn('machine.deadline = STORAGE.Deadline(STORAGE.DOCTOR_SECONDS)', doctor)
        calls = []

        def run(args, sudo=False, timeout=10):
            calls.append(timeout)
            return storage.Result(124, '', 'stalled')

        machine = storage.Machine({'HOME': str(self.home), 'PATH': '/nonexistent'}, self.root, run)
        machine.deadline = storage.Deadline(0)
        result, missing = storage.probe(machine, ['findmnt', '-n', '-o', 'SOURCE', '/'], storage.PROBE_SECONDS)
        self.assertEqual(calls, [])
        self.assertFalse(missing)
        self.assertEqual(result.returncode, 124)
        machine.deadline = storage.Deadline(2)
        storage.probe(machine, ['lsblk', '-ndo', 'TYPE', storage.MAPPER], storage.PROBE_SECONDS)
        self.assertEqual(len(calls), 1)
        self.assertLess(calls[0], storage.PROBE_SECONDS)
        self.assertLessEqual(calls[0], 2)

    def _ready_except_dropin_load(self):
        self.world.gran = '512B'
        self.world.timer = 'enabled'
        self.world.reloaded = True
        dropin = self.root / storage.DROPIN_RELATIVE
        dropin.parent.mkdir(parents=True, exist_ok=True)
        dropin.write_text(storage.render_dropin(storage.TEMPLATE.read_text(encoding='utf-8'), str(self.home)))
        dropin.chmod(0o644)

    def test_live_apply_without_the_flag_does_not_probe(self):
        calls = []
        machine = storage.Machine({'HOME': '/home/example'}, '/', lambda *args, **kwargs: calls.append(args), allow_live=False)
        errors = io.StringIO()
        with redirect_stderr(errors):
            status = storage.apply_storage(machine)
        self.assertEqual(status, 1)
        self.assertEqual(calls, [])
        self.assertIn('GROUNDWORK_STORAGE_APPLY=1', errors.getvalue())
        with redirect_stderr(io.StringIO()):
            self.assertEqual(storage.main(['apply'], {'HOME': '/home/example', 'PATH': '/nonexistent'}), 1)

    def test_testing_without_a_fixture_does_not_look_at_the_host(self):
        self.assertIsNone(storage.machine_from({'GROUNDWORK_TESTING': '1', 'HOME': '/home/example'}))
        self.assertEqual(storage.main(['needed'], {'GROUNDWORK_TESTING': '1', 'HOME': '/home/example'}), 1)
        self.assertEqual(storage.main(['apply'], {'GROUNDWORK_TESTING': '1', 'HOME': '/home/example'}), 0)

    def test_refresh_without_a_terminal_does_not_change_the_header(self):
        with mock.patch.object(storage, 'terminal_available', return_value=False):
            status, _output, errors = self.apply()
        self.assertEqual(status, 1)
        self.assertNotIn('cryptsetup', self.world.names())
        self.assertIn('sudo cryptsetup refresh --allow-discards --persistent root', errors)
        self.assertIn('LUKS header was not changed', errors)
        self.assertEqual(self.world.timer, 'disabled')
        self.assertFalse(any(call['args'][:3] == ['systemctl', 'enable', '--now'] for call in self.world.calls))

    def test_a_refresh_that_leaves_discards_off_is_not_repeated(self):
        self.world.keep_gran = True
        status, _output, errors = self.apply()
        self.assertEqual(status, 1)
        self.assertIn('still zero', errors)
        self.assertIn('luks-discard-unsupported', errors)
        self.assertTrue((self.root / storage.STAMP_RELATIVE).is_file())
        self.assertEqual(self.world.gran, '0B')
        self.assertEqual(self.world.timer, 'disabled')
        self.world.calls.clear()
        status, output, errors = self.apply()
        self.assertEqual((status, output, errors), (0, '', ''))
        self.assertNotIn('cryptsetup', self.world.names())
        text, failed = self.report()
        self.assertFalse(failed)
        self.assertIn('not retried', text)
        self.assertNotIn('systemctl enable --now', text)

    def test_a_masked_timer_is_left_alone(self):
        self._ready_except_dropin_load()
        self.world.timer = 'masked'
        text, failed = self.report()
        self.assertFalse(failed)
        self.assertIn('fstrim.timer is masked and was left alone', text)
        self.assertNotIn(storage.TRIM_OK, text)
        self.assertNotIn('enable --now', text)
        self.assertFalse(storage.needs_privilege(self.world.machine()))
        status, output, errors = self.apply()
        self.assertEqual((status, output, errors), (0, '', ''))
        self.assertFalse(any(call['args'][:2] == ['systemctl', 'enable'] for call in self.world.calls))

    def test_an_unknown_timer_does_not_queue_enable(self):
        self.world.gran = '512B'
        self.world.timer = 'unknown'
        status, _output, errors = self.apply()
        self.assertEqual(status, 1)
        self.assertIn('did not show whether fstrim.timer is enabled', errors)
        self.assertFalse(any(call['args'][:3] == ['systemctl', 'enable', '--now'] for call in self.world.calls))

    def test_a_failed_probe_fails_the_apply(self):
        self.world.mapper = 'failed'
        status, _output, errors = self.apply()
        self.assertEqual(status, 1)
        self.assertIn('findmnt could not name the root filesystem', errors)
        self.assertNotIn('cryptsetup', self.world.names())

    def test_a_fixture_root_without_testing_runs_no_command(self):
        called = []

        def run(_environ):
            def inner(*args, **kwargs):
                called.append(args)
                raise AssertionError(args)
            return inner

        with mock.patch.object(storage, 'default_run', run):
            errors = io.StringIO()
            with redirect_stderr(errors):
                status = storage.main(['apply'], {
                    'GROUNDWORK_STORAGE_ROOT': str(self.root),
                    'HOME': str(self.home),
                })
        self.assertEqual(status, 64)
        self.assertEqual(called, [])
        self.assertIn('test-only', errors.getvalue())
        with mock.patch.object(storage, 'default_run', run):
            errors = io.StringIO()
            with redirect_stderr(errors):
                status = storage.main(['needed'], {'INSTALL_FIXTURE': str(self.root), 'HOME': str(self.home)})
        self.assertEqual(status, 64)
        self.assertEqual(called, [])

    def test_real_commands_do_not_mutate_a_fixture_root(self):
        machine = self.world.machine()
        machine.real_commands = True
        errors = io.StringIO()
        with redirect_stderr(errors):
            status = storage.apply_storage(machine)
        self.assertEqual(status, 64)
        self.assertIn('Nothing was changed', errors.getvalue())
        self.assertNotIn('cryptsetup', self.world.names())
        self.assertFalse(any(call['args'][:3] == ['systemctl', 'enable', '--now'] for call in self.world.calls))

    def test_a_denied_kill_does_not_wait_without_a_limit(self):
        class Process:
            pid = 4242
            returncode = None

            def communicate(self, timeout=None):
                raise subprocess.TimeoutExpired(['sudo'], timeout or 0)

            def wait(self, timeout=None):
                if timeout is None:
                    raise AssertionError('waited without a limit')
                raise subprocess.TimeoutExpired(['sudo'], timeout)

            def kill(self):
                raise PermissionError('denied')

        errors = io.StringIO()
        with mock.patch.object(storage.subprocess, 'Popen', lambda *args, **kwargs: Process()), \
             mock.patch.object(storage.os, 'killpg', side_effect=PermissionError('denied')), \
             redirect_stderr(errors):
            result = storage.run_process(['sudo', 'cryptsetup'], 0.5, {}, terminal=False)
        self.assertEqual(result.returncode, 124)
        self.assertIn('may still be running', errors.getvalue())

    def test_the_passphrase_prompt_keeps_the_terminal(self):
        seen = {}
        ignored = []

        class Process:
            pid = 7
            returncode = 0

            def wait(self, timeout=None):
                return 0

        def popen(command, **kwargs):
            seen['command'] = command
            seen['kwargs'] = kwargs
            seen['stdout_is_tty'] = os.isatty(kwargs['stdout'])
            return Process()

        def tcsetpgrp(fd, group):
            ignored.append(signal.getsignal(signal.SIGTTOU) is signal.SIG_IGN)
            seen.setdefault('foreground', []).append((fd, group))

        terminal = os.open(os.devnull, os.O_RDWR)
        with mock.patch.object(storage.subprocess, 'Popen', popen), \
             mock.patch.object(storage, 'terminal_stdio', return_value=terminal), \
             mock.patch.object(storage.os, 'tcgetpgrp', return_value=1), \
             mock.patch.object(storage.os, 'tcsetpgrp', tcsetpgrp), \
             mock.patch.object(storage.os, 'kill'):
            result = storage.run_process(
                ['sudo', 'cryptsetup', 'refresh'], 30, {'PATH': '/bin'}, terminal=True, keep_session=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(seen['kwargs']['stdin'], terminal)
        self.assertEqual(seen['kwargs']['stdout'], seen['kwargs']['stderr'])
        self.assertNotEqual(seen['kwargs']['stdout'], terminal)
        self.assertTrue(seen['stdout_is_tty'])
        self.assertEqual(seen['kwargs'].get('process_group'), 0)
        self.assertNotIn('preexec_fn', seen['kwargs'])
        self.assertNotIn('start_new_session', seen['kwargs'])
        self.assertIn((terminal, 7), seen['foreground'])
        self.assertIn((terminal, 1), seen['foreground'])
        self.assertTrue(ignored)
        self.assertTrue(all(ignored))
        self.assertIsNot(signal.getsignal(signal.SIGTTOU), signal.SIG_IGN)

    def test_sudo_keeps_the_terminal_session(self):
        seen = {}

        class Process:
            pid = 7
            returncode = 0

            def communicate(self, timeout=None):
                return ('', '')

        def popen(command, **kwargs):
            seen['kwargs'] = kwargs
            return Process()

        with mock.patch.object(storage.subprocess, 'Popen', popen):
            result = storage.run_process(['sudo', 'systemctl'], 30, {}, terminal=False, keep_session=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(seen['kwargs'].get('process_group'), 0)
        self.assertNotIn('preexec_fn', seen['kwargs'])
        self.assertNotIn('start_new_session', seen['kwargs'])
        self.assertIs(seen['kwargs']['stdin'], subprocess.DEVNULL)

    def test_a_probe_uses_its_own_session(self):
        seen = {}

        class Process:
            pid = 7
            returncode = 0

            def communicate(self, timeout=None):
                return ('', '')

        def popen(command, **kwargs):
            seen['kwargs'] = kwargs
            return Process()

        with mock.patch.object(storage.subprocess, 'Popen', popen):
            storage.run_process(['findmnt'], 10, {}, terminal=False, keep_session=False)
        self.assertTrue(seen['kwargs']['start_new_session'])

    def test_sudo_without_a_terminal_does_not_wait_for_a_password(self):
        captured = {}

        def fake_run_process(command, timeout, environ, terminal, keep_session=False):
            captured['command'] = command
            captured['keep_session'] = keep_session
            return storage.Result(0)

        with mock.patch.object(storage.shutil, 'which', return_value='/usr/bin/sudo'), \
             mock.patch.object(storage, 'terminal_available', return_value=False), \
             mock.patch.object(storage, 'run_process', fake_run_process):
            storage.default_run({'PATH': '/usr/bin'}, Path('/'))(
                ['systemctl', 'daemon-reload'], sudo=True, timeout=30)
        self.assertEqual(captured['command'][:2], ['/usr/bin/sudo', '-n'])
        self.assertFalse(captured['keep_session'])

    def test_sudo_with_a_terminal_keeps_the_session_and_omits_noninteractive(self):
        captured = {}

        def fake_run_process(command, timeout, environ, terminal, keep_session=False):
            captured['command'] = command
            captured['keep_session'] = keep_session
            captured['terminal'] = terminal
            return storage.Result(0)

        with mock.patch.object(storage.shutil, 'which', return_value='/usr/bin/sudo'), \
             mock.patch.object(storage, 'terminal_available', return_value=True), \
             mock.patch.object(storage, 'run_process', fake_run_process):
            storage.default_run({'PATH': '/usr/bin'}, Path('/'))(
                ['systemctl', 'enable', '--now', 'fstrim.timer'], sudo=True, timeout=30, terminal=False)
        self.assertNotIn('-n', captured['command'])
        self.assertTrue(captured['keep_session'])
        self.assertFalse(captured['terminal'])

    def test_a_fixture_root_does_not_run_a_host_binary(self):
        def which(name, path=None):
            return '/usr/bin/findmnt' if name == 'findmnt' else None

        with mock.patch.object(storage.shutil, 'which', which), \
             mock.patch.object(storage.subprocess, 'Popen', side_effect=AssertionError('host command ran')):
            machine = storage.machine_from({
                'GROUNDWORK_TESTING': '1',
                'GROUNDWORK_STORAGE_ROOT': str(self.root),
                'PATH': '/usr/bin',
                'HOME': str(self.home),
            })
            state = storage.gather(machine)
        self.assertEqual(state['where'], 'missing')

    def test_a_fixture_root_can_run_its_own_stub(self):
        bindir = self.root / 'bin'
        bindir.mkdir()
        stub = bindir / 'findmnt'
        stub.write_text('#!/bin/sh\necho "/dev/mapper/root compress=zstd:3"\n')
        stub.chmod(0o755)
        machine = storage.machine_from({
            'GROUNDWORK_TESTING': '1',
            'GROUNDWORK_STORAGE_ROOT': str(self.root),
            'PATH': str(bindir),
            'HOME': str(self.home),
        })
        state = storage.gather(machine)
        self.assertEqual(state['where'], 'root')
        self.assertEqual(state['options'], 'compress=zstd:3')

    def test_enabled_runtime_and_static_do_not_fail_or_enable(self):
        for timer in ('enabled-runtime', 'static'):
            with self.subTest(timer=timer):
                self.world.calls.clear()
                self._ready_except_dropin_load()
                self.world.timer = timer
                text, failed = self.report()
                self.assertFalse(failed, text)
                self.assertIn(f'fstrim.timer is {timer} and was left alone', text)
                self.assertNotIn(storage.TRIM_OK, text)
                status, output, errors = self.apply()
                self.assertEqual((status, output, errors), (0, '', ''))
                self.assertFalse(any(call['args'][:2] == ['systemctl', 'enable'] for call in self.world.calls))

    def test_an_unrecognized_granularity_does_not_start_trim(self):
        self.world.gran = '-'
        status, _output, errors = self.apply()
        self.assertEqual(status, 1)
        self.assertIn('discard granularity', errors)
        self.assertNotIn('cryptsetup', self.world.names())
        self.assertFalse(any(call['args'][:3] == ['systemctl', 'enable', '--now'] for call in self.world.calls))

    def test_a_failed_stamp_does_not_claim_the_refresh_is_done(self):
        self.world.keep_gran = True
        self.world.fail.add('install')
        status, _output, errors = self.apply()
        self.assertEqual(status, 1)
        self.assertIn('still zero', errors)
        self.assertIn('ask again', errors)
        self.assertNotIn('will not be retried', errors)
        self.assertFalse((self.root / storage.STAMP_RELATIVE).is_file())

    def test_an_unsupported_header_is_recorded_and_not_retried(self):
        self.world.fail.add('cryptsetup')
        self.world.fail_text['cryptsetup'] = 'Option --persistent is only supported for LUKS2.'
        status, _output, errors = self.apply()
        self.assertEqual(status, 1)
        self.assertIn('cannot store allow-discards', errors)
        self.assertIn('will not be retried', errors)
        self.assertTrue((self.root / storage.STAMP_RELATIVE).is_file())
        self.assertEqual(self.world.timer, 'disabled')
        self.world.calls.clear()
        status, output, errors = self.apply()
        self.assertEqual((status, output, errors), (0, '', ''))
        self.assertNotIn('cryptsetup', self.world.names())

    def test_a_plus_or_trailing_slash_in_home_does_not_block_the_dropin(self):
        plus = self.root / 'a+b'
        plus.mkdir()
        for home in (str(plus), str(self.home) + '/'):
            with self.subTest(home=home):
                self.world.calls.clear()
                self.world.home = home
                self.world.gran = '512B'
                self.world.timer = 'enabled'
                status, _output, errors = self.apply()
                self.assertEqual(status, 0, errors)
                self.assertIn('install', self.world.names())

    def test_explain_names_the_passphrase_without_running_cryptsetup(self):
        lines = storage.explain_lines(storage.gather(self.world.machine()))
        self.assertIn('disk passphrase is separate', '\n'.join(lines))
        self.assertIn('sudo cryptsetup refresh --allow-discards --persistent root', '\n'.join(lines))
        self.assertNotIn('cryptsetup', self.world.names())
        self.world.gran = '512B'
        self.assertEqual(storage.explain_lines(storage.gather(self.world.machine())), [])

    def _pty_exit(self, argv_repr, send_repr, timeout):
        """Run one command on a fresh terminal, outside this process.

        Forking the test process is unsafe once it has other threads. A new
        interpreter has none. The timeout is the bound if that child stalls.
        """
        script = f'''
import errno, importlib.util, os, pty, sys, time, fcntl
spec = importlib.util.spec_from_file_location("groundwork_storage", {str(SCRIPT)!r})
storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage)
pid, fd = pty.fork()
if pid == 0:
    try:
        result = storage.run_process({argv_repr}, {timeout}, {{"PATH": "/usr/bin:/bin"}}, terminal=True, keep_session=True)
        os._exit(result.returncode if result.returncode >= 0 else 9)
    except BaseException:
        import traceback
        traceback.print_exc()
        os._exit(9)
flags = fcntl.fcntl(fd, fcntl.F_GETFL)
fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
time.sleep(0.4)
try:
    os.write(fd, {send_repr})
except BlockingIOError:
    sys.stderr.write("write blocked\\n")
deadline = time.monotonic() + {timeout} + 3
status = None
while time.monotonic() < deadline:
    try:
        chunk = os.read(fd, 200)
        if chunk:
            sys.stderr.write(repr(chunk) + "\\n")
    except BlockingIOError:
        pass
    except OSError as error:
        # Linux returns EIO once the other end of the terminal has closed.
        if error.errno != errno.EIO:
            raise
    waited, status = os.waitpid(pid, os.WNOHANG)
    if waited:
        code = os.waitstatus_to_exitcode(status)
        sys.stderr.write("wait %s\\n" % code)
        os._exit(code & 0xff)
    time.sleep(0.05)
sys.stderr.write("deadline\\n")
os.kill(pid, 9)
os._exit(9)
'''
        completed = subprocess.run([sys.executable, '-I', '-c', script], timeout=8, capture_output=True)
        return completed.returncode, completed.stderr.decode()

    def test_ctrl_c_at_the_prompt_returns_130(self):
        # Protects: Ctrl+C during the passphrase prompt exits 130 without a traceback.
        # Distinct value: the Popen stand-in never owns a terminal.
        code, errors = self._pty_exit("['/bin/sleep', '30']", r"b'\x03'", 2)
        self.assertEqual(code, 130, errors)

    def test_terminal_text_reaches_the_helper(self):
        # Protects: a header cryptsetup rejects is recorded from the helper's copy of the text.
        # Distinct value: the stand-in returns that text already, so it never reads a terminal.
        phrase = 'Option --persistent is only supported for LUKS2.'
        argv = repr([
            sys.executable, '-I', '-c',
            'import sys; sys.stderr.write(%r); sys.exit(2)' % (phrase + '\n'),
        ])
        code, errors = self._pty_helper_text(argv, 'only supported for luks2')
        self.assertEqual(code, 0, errors)
        self.assertIn(phrase, errors)

    def _pty_helper_text(self, argv_repr, needle):
        script = f'''
import errno, importlib.util, os, pty, sys, time, fcntl
spec = importlib.util.spec_from_file_location("groundwork_storage", {str(SCRIPT)!r})
storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage)

def read_terminal(fd):
    try:
        return os.read(fd, 200)
    except BlockingIOError:
        return None
    except OSError as error:
        # Linux returns EIO once the other end of the terminal has closed.
        if error.errno == errno.EIO:
            return b""
        raise

pid, fd = pty.fork()
if pid == 0:
    try:
        result = storage.run_process({argv_repr}, 3, {{"PATH": "/usr/bin:/bin"}}, terminal=True, keep_session=True)
        text = (result.stdout + "\\n" + result.stderr).lower()
        os._exit(0 if {needle!r} in text else 7)
    except BaseException:
        import traceback
        traceback.print_exc()
        os._exit(9)
flags = fcntl.fcntl(fd, fcntl.F_GETFL)
fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
deadline = time.monotonic() + 6
while time.monotonic() < deadline:
    chunk = read_terminal(fd)
    if chunk:
        sys.stderr.write(chunk.decode("utf-8", "replace"))
    waited, status = os.waitpid(pid, os.WNOHANG)
    if waited:
        while True:
            chunk = read_terminal(fd)
            if not chunk:
                break
            sys.stderr.write(chunk.decode("utf-8", "replace"))
        os._exit(os.waitstatus_to_exitcode(status) & 0xff)
    time.sleep(0.05)
os.kill(pid, 9)
os._exit(9)
'''
        completed = subprocess.run([sys.executable, '-I', '-c', script], timeout=10, capture_output=True)
        return completed.returncode, completed.stderr.decode()

    def test_taking_the_terminal_back_does_not_stop_the_caller(self):
        # Protects: after the passphrase command exits, restoring the terminal does not stop the update.
        # Distinct value: the stand-in never calls tcsetpgrp, so it cannot be stopped by the terminal.
        script = f'''
import importlib.util, os, pty, signal, time
spec = importlib.util.spec_from_file_location("groundwork_storage", {str(SCRIPT)!r})
storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage)
master, slave = pty.openpty()
leader = os.fork()
if leader == 0:
    os.close(master)
    try:
        os.setsid()
        tty_fd = os.open(os.ttyname(slave), os.O_RDWR)
        os.dup2(tty_fd, 0)
        os.dup2(tty_fd, 1)
        os.dup2(tty_fd, 2)
        if slave > 2:
            os.close(slave)
        if tty_fd > 2:
            os.close(tty_fd)
        worker = os.fork()
        if worker == 0:
            os.setpgid(0, 0)
            previous = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
            try:
                os.tcsetpgrp(0, os.getpgrp())
            except OSError:
                os._exit(6)
            finally:
                signal.signal(signal.SIGTTOU, previous)
            try:
                result = storage.run_process(
                    ["/bin/sleep", "0.3"], 5, {{"PATH": "/bin:/usr/bin"}}, terminal=True, keep_session=True)
            except BaseException:
                os._exit(8)
            os._exit(0 if result.returncode == 0 else 8)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            wpid, status = os.waitpid(worker, os.WUNTRACED | os.WNOHANG)
            if wpid == 0:
                time.sleep(0.05)
                continue
            if os.WIFSTOPPED(status):
                os.kill(worker, signal.SIGKILL)
                os.waitpid(worker, 0)
                os._exit(3)
            if os.WIFEXITED(status):
                os._exit(os.WEXITSTATUS(status))
            os._exit(4)
        os.kill(worker, signal.SIGKILL)
        os._exit(5)
    except BaseException:
        os._exit(9)
os.close(slave)
import fcntl
flags = fcntl.fcntl(master, fcntl.F_GETFL)
fcntl.fcntl(master, fcntl.F_SETFL, flags | os.O_NONBLOCK)
deadline = time.monotonic() + 6
while time.monotonic() < deadline:
    try:
        os.read(master, 4096)
    except (BlockingIOError, OSError):
        pass
    wpid, status = os.waitpid(leader, os.WNOHANG)
    if wpid:
        os._exit(os.waitstatus_to_exitcode(status) & 0xff)
    time.sleep(0.05)
os.kill(leader, signal.SIGKILL)
os._exit(5)
'''
        completed = subprocess.run([sys.executable, '-I', '-c', script], timeout=10, capture_output=True)
        detail = completed.stderr.decode() + completed.stdout.decode()
        self.assertNotEqual(completed.returncode, 3, 'restoring the terminal stopped the caller\n' + detail)
        self.assertEqual(completed.returncode, 0, detail)

    def test_a_failed_storage_apply_keeps_its_status(self):
        # Protects: apply-layer reports the helper's status. Inside `if !`, $? is 0.
        # Distinct value: the stub exits 130 and is not the real helper.
        layer = (REPO / 'scripts/apply-layer.sh').read_text(encoding='utf-8')
        start = layer.index('storage_status=0\n')
        end = layer.index('exit "$storage_status"\n', start)
        end = layer.index('fi\n', end)
        block = layer[start:end + len('fi\n')]
        with tempfile.TemporaryDirectory() as directory:
            stub = Path(directory) / 'python3'
            stub.write_text('#!/bin/sh\nexit 130\n')
            stub.chmod(0o755)
            completed = subprocess.run(
                ['bash', '-c', 'set -euo pipefail\nroot=/tmp\n' + block],
                env={'PATH': directory + ':/usr/bin:/bin', 'TERM': 'dumb'},
                capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 130, completed.stderr)
        self.assertIn('not fully applied', completed.stderr)
        self.assertNotIn('Groundwork settings applied', completed.stdout)

    def test_the_prompt_reads_the_terminal(self):
        # Protects: the passphrase is read from the terminal when stdin is not that terminal.
        # Distinct value: the stand-in records arguments and never reads a terminal.
        argv = repr([sys.executable, '-I', '-c',
                     'import sys; sys.exit(0 if sys.stdin.readline()=="ready\\n" else 3)'])
        code, errors = self._pty_exit(argv, "b'ready\\n'", 2)
        self.assertEqual(code, 0, errors)

    def test_apply_layer_calls_the_helper(self):
        text = (REPO / 'scripts/apply-layer.sh').read_text(encoding='utf-8')
        self.assertIn('python3 "$root/scripts/groundwork-storage.py" needed', text)
        self.assertIn('python3 "$root/scripts/groundwork-storage.py" explain', text)
        self.assertIn('GROUNDWORK_STORAGE_APPLY=1 python3 "$root/scripts/groundwork-storage.py" apply', text)
        self.assertIn('exit "$storage_status"', text)


if __name__ == '__main__':
    unittest.main()
