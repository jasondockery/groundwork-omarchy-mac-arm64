"""TRIM through LUKS and plocate prunes (decision 0023).

Every command is a stand-in. Nothing here calls cryptsetup, systemctl, or btrfs
on the machine running the tests.
"""
import importlib.util
import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
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
        self.fstype = 'btrfs'
        self.btrfs = True
        self.reloaded = False
        self.show = 'ok'
        self.props = {}
        self.fail = set()
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
            return storage.Result(1, '', f'{args[0]} failed')
        if args[0] == 'findmnt':
            if len(args) >= 4 and args[3] == 'FSTYPE':
                if self.fstype == 'missing':
                    raise FileNotFoundError('findmnt')
                if self.fstype == 'failed':
                    return storage.Result(1, '', 'findmnt failed')
                return storage.Result(0, self.fstype + '\n')
            if self.mapper == 'missing':
                raise FileNotFoundError('findmnt')
            if self.mapper == 'failed':
                return storage.Result(1, '', 'findmnt failed')
            if self.mapper == 'root':
                return storage.Result(0, '/dev/mapper/root\n')
            if self.mapper == 'subvol':
                return storage.Result(0, '/dev/mapper/root[/@]\n')
            return storage.Result(0, f'{self.mapper}\n')
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
            'timeout': 30,
            'terminal': True,
        }])
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

    def test_home_that_is_not_btrfs_is_left_alone(self):
        self.world.fstype = 'ext4'
        status, _, errors = self.apply()
        self.assertEqual(status, 0, errors)
        self.assertFalse((self.home / 'code').exists())
        self.assertFalse(any(call['args'][:3] == ['btrfs', 'property', 'set'] for call in self.world.calls))
        text, failed = self.report()
        self.assertFalse(failed)
        self.assertIn(storage.COMPRESSION_OK, text)
        self.assertNotIn('btrfs-progs', text)
        self.assertNotIn('does not exist', text)

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
        dropin.parent.mkdir(parents=True)
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

        class Process:
            pid = 7
            returncode = 0

            def wait(self, timeout=None):
                return 0

        def popen(command, **kwargs):
            seen['command'] = command
            seen['kwargs'] = kwargs
            return Process()

        with mock.patch.object(storage.subprocess, 'Popen', popen):
            result = storage.run_process(['sudo', 'cryptsetup', 'refresh'], 30, {'PATH': '/bin'}, terminal=True)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn('stdin', seen['kwargs'])
        self.assertNotIn('start_new_session', seen['kwargs'])

    def test_apply_layer_calls_the_helper(self):
        text = (REPO / 'scripts/apply-layer.sh').read_text(encoding='utf-8')
        self.assertIn('python3 "$root/scripts/groundwork-storage.py" needed', text)
        self.assertIn('GROUNDWORK_STORAGE_APPLY=1 python3 "$root/scripts/groundwork-storage.py" apply', text)


if __name__ == '__main__':
    unittest.main()
