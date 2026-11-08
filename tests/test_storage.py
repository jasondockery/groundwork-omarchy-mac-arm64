"""TRIM through LUKS, plocate prunes, and directory compression (decision 0022).

Every command is a stand-in. Nothing here calls cryptsetup, systemctl, or btrfs
on the machine running the tests.
"""
import importlib.util
import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import stat
import tempfile
import unittest

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
        self.props = {}
        self.fail = set()
        self.units = {'fstrim.timer', 'plocate-updatedb.service'}
        unit = self.root / 'usr/lib/systemd/system'
        unit.mkdir(parents=True)
        for name in self.units:
            (unit / name).write_text('[Unit]\n')

    def run(self, args, sudo=False, timeout=10):
        self.calls.append({'sudo': sudo, 'args': list(args), 'timeout': timeout})
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
            return storage.Result(1, 'disabled\n')
        if args[:4] == ['systemctl', 'enable', '--now', 'fstrim.timer']:
            self.timer = 'enabled'
            return storage.Result(0, '')
        if args == ['systemctl', 'daemon-reload']:
            self.reloaded = True
            return storage.Result(0, '')
        if args[:4] == ['systemctl', 'show', '-p', 'DropInPaths']:
            if not self.reloaded:
                return storage.Result(0, '')
            return storage.Result(0, str(self.root / storage.DROPIN_RELATIVE) + '\n')
        if args[0] == 'cryptsetup':
            self.gran = '512B'
            return storage.Result(0, '')
        if args[0] == 'install':
            destination = Path(args[-1])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(Path(args[-2]).read_text(encoding='utf-8'), encoding='utf-8')
            destination.chmod(0o644)
            return storage.Result(0, '')
        if args[0] == 'btrfs' and args[1:] == ['version']:
            if not self.btrfs:
                raise FileNotFoundError('btrfs')
            return storage.Result(0, 'btrfs-progs\n')
        if args[:3] == ['btrfs', 'property', 'get']:
            if self.props.get(args[3]) == 'zstd:1':
                return storage.Result(0, 'compression=zstd:1\n')
            return storage.Result(0, '')
        if args[:3] == ['btrfs', 'property', 'set']:
            self.props[args[3]] = args[5]
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
        self.assertNotIn('jason', text.lower())
        self.assertNotIn('SWORPS', text)
        for path in (SCRIPT, storage.TEMPLATE, REPO / 'docs/decisions/0022-storage-defaults.md'):
            source = path.read_text(encoding='utf-8')
            self.assertNotIn('SWORPS', source)
            self.assertNotIn('/home/jason', source)

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
        }])
        enable = [call for call in self.world.calls if call['args'][:3] == ['systemctl', 'enable', '--now']]
        self.assertEqual(enable[0]['args'], ['systemctl', 'enable', '--now', 'fstrim.timer'])
        self.assertEqual(enable[0]['timeout'], 900)
        crypt_at = next(index for index, call in enumerate(self.world.calls) if call['args'][0] == 'cryptsetup')
        enable_at = next(index for index, call in enumerate(self.world.calls)
                         if call['args'][:3] == ['systemctl', 'enable', '--now'])
        self.assertLess(crypt_at, enable_at)
        dropin = self.root / storage.DROPIN_RELATIVE
        rendered = dropin.read_text(encoding='utf-8')
        self.assertEqual(rendered, storage.render_dropin(storage.TEMPLATE.read_text(encoding='utf-8'), str(self.home)))
        self.assertNotIn('@HOME@', rendered)
        self.assertNotIn('jason', rendered)
        self.assertEqual(stat.S_IMODE(dropin.stat().st_mode), 0o644)
        self.assertFalse((self.root / 'etc/updatedb.conf').exists())
        install = [call for call in self.world.calls if call['args'][0] == 'install']
        self.assertEqual(install[0]['args'][1:8], ['-D', '-o', 'root', '-g', 'root', '-m', '0644'])
        self.assertTrue(install[0]['sudo'])
        self.assertIn({'sudo': True, 'args': ['systemctl', 'daemon-reload'], 'timeout': 30}, self.world.calls)
        for relative in storage.COMPRESS_RELATIVE:
            directory = str(self.home / relative)
            self.assertEqual(self.world.props[directory], 'zstd:1')
            self.assertTrue((self.home / relative).is_dir())
        sets = [call['args'] for call in self.world.calls if call['args'][:3] == ['btrfs', 'property', 'set']]
        self.assertEqual(sets, [
            ['btrfs', 'property', 'set', str(self.home / relative), 'compression', 'zstd:1']
            for relative in storage.COMPRESS_RELATIVE
        ])
        self.assertTrue(all(call['sudo'] is False for call in self.world.calls if call['args'][0] == 'btrfs'))
        self.assertNotIn('mount', self.world.names())
        self.assertLess(self.world.names().index('btrfs'), self.world.names().index('cryptsetup'))
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
        self.assertFalse(any(call['args'][:3] == ['btrfs', 'property', 'set'] for call in self.world.calls))

    def test_doctor_is_read_only_and_names_each_gap(self):
        self.world.calls.clear()
        text, failed = self.report()
        self.assertTrue(failed)
        self.assertIn('discard granularity is 0B', text)
        self.assertIn('sudo cryptsetup refresh --allow-discards --persistent root', text)
        self.assertIn('fstrim.timer is not enabled', text)
        self.assertIn('sudo systemctl enable --now fstrim.timer', text)
        self.assertIn('node_modules', text)
        self.assertIn(str(self.home / '.local/share/pnpm'), text)
        for relative in storage.COMPRESS_RELATIVE:
            self.assertIn(f'{self.home / relative} does not exist', text)
        existing = self.home / 'code'
        existing.mkdir()
        text, failed = self.report()
        self.assertTrue(failed)
        self.assertIn(f'btrfs property set {existing} compression zstd:1', text)
        self.assertNotIn('cryptsetup', self.world.names())
        self.assertNotIn('install', self.world.names())
        self.assertTrue(all(call['sudo'] is False for call in self.world.calls))
        self.assertFalse(any(call['args'][:2] == ['systemctl', 'enable'] for call in self.world.calls))
        self.assertFalse(any(call['args'][:3] == ['btrfs', 'property', 'set'] for call in self.world.calls))
        brief, brief_failed = self.report(brief=True)
        self.assertTrue(brief_failed)
        self.assertTrue(all(line.startswith(('TRIM through LUKS: ', 'Locate index: ', 'Compression: '))
                            for line in brief.splitlines()))

    def test_other_root_device_is_not_opened(self):
        self.world.mapper = '/dev/nvme0n1p2'
        status, _, _ = self.apply()
        self.assertEqual(status, 0)
        self.assertNotIn('cryptsetup', self.world.names())
        text, failed = self.report()
        self.assertTrue(failed)
        self.assertIn('root is /dev/nvme0n1p2, not /dev/mapper/root', text)

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
        for relative in storage.COMPRESS_RELATIVE:
            directory = self.home / relative
            directory.mkdir(parents=True)
            self.world.props[str(directory)] = 'zstd:1'
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
        self.assertIn('Compression: ok (home is not btrfs, so directory compression does not apply)', text)
        self.assertNotIn('does not exist', text)

    def test_unloaded_dropin_is_loaded_again(self):
        self.world.gran = '512B'
        self.world.timer = 'enabled'
        for relative in storage.COMPRESS_RELATIVE:
            directory = self.home / relative
            directory.mkdir(parents=True)
            self.world.props[str(directory)] = 'zstd:1'
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
        self.assertIn({'sudo': True, 'args': ['systemctl', 'daemon-reload'], 'timeout': 30}, self.world.calls)
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
        for relative in storage.COMPRESS_RELATIVE:
            (self.home / relative).mkdir(parents=True)
            self.world.props[str(self.home / relative)] = 'zstd:1'
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
        text, failed = self.report()
        self.assertTrue(failed)
        self.assertIn(f'{self.home / "code"} is a symlink', text)
        self.apply()
        self.assertNotIn(str(self.home / 'code'), self.world.props)
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
        self.assertEqual(self.world.gran, '0B')

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

    def test_apply_layer_calls_the_helper(self):
        text = (REPO / 'scripts/apply-layer.sh').read_text(encoding='utf-8')
        self.assertIn('python3 "$root/scripts/groundwork-storage.py" needed', text)
        self.assertIn('GROUNDWORK_STORAGE_APPLY=1 python3 "$root/scripts/groundwork-storage.py" apply', text)


if __name__ == '__main__':
    unittest.main()
