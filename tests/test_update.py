"""Real local Git transport; isolated home and mocked machine-owner commands."""
import fcntl
from contextlib import contextmanager, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('groundwork_updater', REPO / 'scripts/groundwork-update.py')
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)

# `readelf -lW` program headers from pingdotgg/t3code#23433.
READELF_GOOD = '''
Program Headers:
  Type           Offset   VirtAddr           PhysAddr           FileSiz  MemSiz   Flg Align
  LOAD           0x000000 0x0000000000400000 0x0000000000400000 0x409510 0x409510 R   0x10000
  LOAD           0x410000 0x0000000000810000 0x0000000000810000 0x2978c90 0x2978c90 R E 0x10000
  LOAD           0x8dde000 0x0000000010dde000 0x0000000010dde000 0xffe000 0xffe000 R   0x10000
'''
CANDIDATE = '0.1.3-nightly.20261024.3604'


def t3_archive(version):
    buffer = io.BytesIO()
    stem = f't3-{version}-linux-arm64'
    with tarfile.open(fileobj=buffer, mode='w:gz') as bundle:
        for name, payload in [('t3', b'\x7fELF executable'), ('node_modules/addon/addon.node', b'\x7fELF shared object'),
                              ('README.md', b'not an ELF file')]:
            info = tarfile.TarInfo(f'{stem}/{name}')
            info.size = len(payload)
            bundle.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


READELF_BAD = '''
Program Headers:
  Type           Offset   VirtAddr           PhysAddr           FileSiz  MemSiz   Flg Align
  PHDR           0x6dad000 0x00000000071ad000 0x00000000071ad000 0x0002a0 0x0002a0 R   0x8
  LOAD           0x000000 0x0000000000400000 0x0000000000400000 0x41d390 0x41d390 R   0x10000
  LOAD           0x420000 0x0000000000820000 0x0000000000820000 0x29d82f0 0x29d82f0 R E 0x10000
  LOAD           0x6dad000 0x00000000071ad000 0x00000000071ad000 0x000508 0x000508 R   0x1000
  LOAD           0x8faf000 0x000000001015d000 0x000000001015d000 0x1003000 0x1003000 R   0x1000
'''


class UpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='groundwork-update-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.config = self.home / '.config/groundwork/omarchy.json'
        self.state = self.home / '.local/state/groundwork'
        self.environment = patch.dict(os.environ, HOME=str(self.home),
                                      XDG_CONFIG_HOME=str(self.home / '.config'),
                                      XDG_STATE_HOME=str(self.home / '.local/state'),
                                      GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.patches = [patch.object(updater, 'CONFIG', self.config),
                        patch.object(updater, 'STATE', self.state),
                        patch.object(updater.platform, 'system', return_value='Linux'),
                        patch.object(updater.platform, 'machine', return_value='aarch64'),
                        patch.object(updater.shutil, 'which', side_effect=lambda name: '/fixture/' + name),
                        patch.object(updater, 'page_size', return_value=4096)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.remote = self.root / 'origin.git'
        self.seed = self.root / 'seed'
        self.source = self.root / 'source'
        self.command('git', 'init', '--bare', '--initial-branch=main', str(self.remote))
        self.command('git', 'clone', str(self.remote), str(self.seed))
        (self.seed / 'packages/hypr/.config/hypr').mkdir(parents=True)
        (self.seed / 'packages/hypr/.config/hypr/input.lua').write_text('old settings\n')
        (self.seed / 'scripts').mkdir()
        (self.seed / 'scripts/apply-layer.sh').write_text('#!/bin/bash\nexit 0\n')
        (self.seed / 'scripts/groundwork-update.py').write_text((REPO / 'scripts/groundwork-update.py').read_text())
        (self.seed / 'keyd').mkdir()
        (self.seed / 'keyd/default.conf').write_text('fixture key mapping\n')
        self.commit(self.seed)
        self.command('git', '-C', str(self.seed), 'push', 'origin', 'main')
        self.command('git', 'clone', str(self.remote), str(self.source))
        self.input = self.home / '.config/hypr/input.lua'
        self.input.parent.mkdir(parents=True)
        self.input.symlink_to(self.source / 'packages/hypr/.config/hypr/input.lua')
        updater.register(str(self.source), '0', '0', str(self.home / 'Notes'))
        self.initial = self.git('rev-parse', 'HEAD')
        self.machine_calls = []
        self.failure = None
        self.readelf = {}
        self.machine_kwargs = []
        self.t3_installed = '0.0.1-nightly.fixture'
        self.t3_broken = False
        self.break_after_update = False
        self.blip_listed = False
        self.blip_rewrite = None
        self.blip_plugin = None
        self.install_failures = set()
        self.freetube_update_failure = False
        self.candidate_crashes = False
        self.candidate_smoke_error = None
        self.smoke_version = CANDIDATE
        self.t3_update_fails = False
        self.t3_update_error = None
        self.break_version_after_failed_update = False
        self.version_after_failed_update = None
        self.real_run = updater.run
        self.run_patch = patch.object(updater, 'run', side_effect=self.run_command)
        self.run_patch.start()
        self.addCleanup(self.run_patch.stop)

    def command(self, *args):
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL).strip()

    def git(self, *args):
        return self.command('git', '-C', str(self.source), *args)

    def commit(self, path):
        self.command('git', '-C', str(path), 'add', '.')
        self.command('git', '-C', str(path), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                     'commit', '-m', 'fixture')

    def candidate(self):
        (self.seed / 'packages/hypr/.config/hypr/input.lua').write_text('new settings\n')
        self.commit(self.seed)
        self.command('git', '-C', str(self.seed), 'push', 'origin', 'main')

    def run_command(self, args, **kwargs):
        if args[0] == 'git':
            return self.real_run(args, **kwargs)
        self.machine_calls.append(args)
        self.machine_kwargs.append(kwargs)
        if args[0] == self.failure:
            raise updater.UpdateError('fixture stage failed')
        if args[:2] == ['t3', '--version']:
            if self.t3_broken:
                raise updater.UpdateError('t3 failed (exit -11).')
            return f't3 v{self.t3_installed}'
        if args[:2] == ['t3', 'update'] and self.t3_update_fails:
            if self.break_version_after_failed_update:
                self.t3_broken = True
            if self.version_after_failed_update:
                self.t3_installed = self.version_after_failed_update
            if self.t3_update_error is not None:
                raise self.t3_update_error
            raise updater.UpdateError('t3 failed (exit 1).')
        if args[:2] == ['t3', 'update'] and len(args) == 6:
            self.t3_installed = args[-1]
            self.t3_broken = self.break_after_update
        if Path(args[0]).name == 'bwrap' and '--version' in args:
            if self.candidate_crashes:
                raise updater.UpdateError(f'{args[0]} failed (exit -11).')
            if self.candidate_smoke_error is not None:
                raise self.candidate_smoke_error
            return f't3 v{self.smoke_version}'
        if args[0] == 'sh':
            version = kwargs['extra_env']['T3CODE_VERSION']
            if version in self.install_failures:
                raise updater.UpdateError('fixture installer failed')
            self.t3_installed, self.t3_broken = version, False
        if args[:2] == ['readelf', '-lW']:
            return self.readelf.get(Path(args[2]).name, READELF_GOOD)
        if args == ['flatpak', '--user', 'update', '--noninteractive', 'io.freetubeapp.FreeTube'] and self.freetube_update_failure:
            raise updater.UpdateError('FreeTube user Flatpak update failed')
        if args[:3] == ['flatpak', '--user', 'list']:
            return 'com.bitwarden.desktop\nio.freetubeapp.FreeTube\norg.example.Unowned'
        if args == ['omarchy', 'plugin', 'list', '--json']:
            if self.blip_listed:
                return '[{"id": "nixfred.blip", "enabled": true}]'
            return '[]'
        if args[:4] == ['omarchy', 'plugin', 'update', 'nixfred.blip']:
            if '--yes' in args:
                raise AssertionError('Blip update must not pass --yes')
            plugin = self.blip_plugin
            if plugin is not None and self.blip_rewrite == 'bridge':
                (plugin / 'bridge/mac/install.sh').write_text('new\n')
                self.commit(plugin)
            elif plugin is not None and self.blip_rewrite == 'readme':
                (plugin / 'README.md').write_text('newer\n')
                self.commit(plugin)
            return ''
        return ''

    def test_check_keeps_live_symlink_head_index_and_configuration_unchanged(self):
        self.candidate()
        config = self.config.read_bytes()
        index = (self.source / '.git/index').read_bytes()
        updater.update('layer', check=True)
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.initial)
        self.assertEqual(self.input.read_text(), 'old settings\n')
        self.assertEqual((self.source / '.git/index').read_bytes(), index)
        self.assertEqual(self.config.read_bytes(), config)
        self.assertFalse(self.machine_calls)
        self.assertFalse(list(self.state.glob('*success*')))
        self.assertEqual(self.git('for-each-ref', 'refs/groundwork'), '')

    def test_layer_apply_uses_fresh_runner_and_preserves_saved_choices(self):
        self.candidate()
        updater.update('layer')
        self.assertEqual(self.input.read_text(), 'new settings\n')
        self.assertEqual(self.machine_calls, [['bash', str(self.source / 'scripts/apply-layer.sh'), '--update']])
        saved = json.loads(self.config.read_text())
        self.assertFalse(saved['keyd'])
        self.assertFalse(saved['screen_sharing'])
        self.assertEqual(saved['obsidian_vault'], str(self.home / 'Notes'))
        self.assertFalse((self.state / 'update-all-last-success.json').exists())

    def test_update_all_requests_plugin_updates_and_layer_apply_does_not(self):
        updater.update('all')
        self.assertIn(['bash', str(self.source / 'scripts/apply-layer.sh'), '--update', '--update-plugins'],
                      self.machine_calls)
        self.machine_calls.clear()
        updater.update('layer')
        self.assertEqual(self.machine_calls, [['bash', str(self.source / 'scripts/apply-layer.sh'), '--update']])

    def test_all_delegates_system_and_selected_app_updates_to_their_owners(self):
        updater.update('all')
        self.assertEqual(self.machine_calls[0], ['omarchy', 'update'])
        self.assertIn(['t3', 'update', '--channel', 'nightly', '--yes'], self.machine_calls)
        self.assertIn(['flatpak', '--user', 'update', '--noninteractive', 'com.bitwarden.desktop'], self.machine_calls)
        self.assertIn(['flatpak', '--user', 'update', '--noninteractive', 'io.freetubeapp.FreeTube'], self.machine_calls)
        self.assertNotIn('org.example.Unowned', str(self.machine_calls))
        self.assertFalse(any(call[:2] == ['omarchy', 'plugin'] for call in self.machine_calls))

    def test_dirty_or_untracked_source_refuses_before_machine_mutation(self):
        for path in [self.source / 'packages/hypr/.config/hypr/input.lua', self.source / 'untracked']:
            old = path.read_bytes() if path.exists() else None
            path.write_text('keep local work')
            with self.assertRaisesRegex(updater.UpdateError, 'local changes'):
                updater.update('all')
            if old is None:
                path.unlink()
            else:
                path.write_bytes(old)
        self.assertFalse(self.machine_calls)
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.initial)

    def test_detached_missing_upstream_and_ahead_source_preserve_work(self):
        self.git('checkout', '--detach')
        with self.assertRaises(updater.UpdateError):
            updater.update('layer')
        self.git('checkout', 'main')
        self.git('branch', '--unset-upstream')
        with self.assertRaises(updater.UpdateError):
            updater.update('layer')
        self.git('branch', '--set-upstream-to=origin/main')
        (self.source / 'local').write_text('keep local commit')
        self.commit(self.source)
        ahead = self.git('rev-parse', 'HEAD')
        with self.assertRaisesRegex(updater.UpdateError, 'ahead of or diverged'):
            updater.update('layer')
        self.assertEqual(self.git('rev-parse', 'HEAD'), ahead)
        self.assertFalse(self.machine_calls)

    def test_failed_fetch_leaves_checkout_and_success_receipt_untouched(self):
        updater.atomic_write(self.state / 'groundwork-update-last-success.json', 'previous')
        self.git('remote', 'set-url', 'origin', str(self.root / 'missing.git'))
        with self.assertRaises(updater.UpdateError):
            updater.update('layer')
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.initial)
        self.assertEqual((self.state / 'groundwork-update-last-success.json').read_text(), 'previous')
        self.assertFalse(self.machine_calls)

    def test_apply_failure_keeps_prior_success_and_same_head_retry_reapplies(self):
        self.candidate()
        updater.atomic_write(self.state / 'groundwork-update-last-success.json', 'previous')
        self.failure = 'bash'
        with self.assertRaises(updater.UpdateError):
            updater.update('layer')
        self.assertEqual((self.state / 'groundwork-update-last-success.json').read_text(), 'previous')
        self.failure = None
        updater.update('layer')
        receipt = json.loads((self.state / 'groundwork-update-last-success.json').read_text())
        self.assertEqual(receipt['source_before'], receipt['source_after'])
        self.assertEqual(receipt['result'], 'completed')

    def test_omarchy_failure_stops_before_advancing_or_applying(self):
        self.candidate()
        self.failure = 'omarchy'
        with self.assertRaises(updater.UpdateError):
            updater.update('all')
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.initial)
        self.assertEqual(self.machine_calls, [['omarchy', 'update']])

    def test_failed_keyd_copy_and_newer_candidate_keep_both_retry_boundaries_recoverable(self):
        installed = self.root / 'installed-keyd.conf'
        installed.write_bytes((self.source / 'keyd/default.conf').read_bytes())
        updater.register(str(self.source), '1', '0', str(self.home / 'Notes'))
        (self.seed / 'keyd/default.conf').write_text('new desired mapping\n')
        apply = self.seed / 'scripts/apply-layer.sh'
        apply.write_text(f'''#!/bin/bash
set -eu
cp "{self.source}/keyd/default.conf" "{installed}"
test -f "{self.root}/reload-ok" || exit 42
python3 "{self.source}/scripts/groundwork-update.py" register "{self.source}" 1 0 "{self.home}/Notes"
''')
        self.candidate()
        def executed(args, **kwargs):
            if args[0] in ('git', 'bash'):
                return self.real_run(args, **dict(kwargs, capture=True))
            return self.run_command(args, **kwargs)
        with patch.object(updater, 'KEYD_CONFIG', installed), patch.object(updater, 'run', side_effect=executed):
            with self.assertRaises(updater.UpdateError):
                updater.update('layer')
            pending = json.loads(self.config.read_text())['pending_keyd']
            self.assertEqual(pending['sha256'], hashlib.sha256(installed.read_bytes()).hexdigest())
            self.assertFalse((self.state / 'groundwork-update-last-success.json').exists())
            copied_digest = hashlib.sha256(installed.read_bytes()).hexdigest()
            (self.seed / 'keyd/default.conf').write_text('newer desired mapping\n')
            apply.write_text(f'''#!/bin/bash
set -eu
test -f "{self.root}/copy-ok" || exit 43
cp "{self.source}/keyd/default.conf" "{installed}"
test -f "{self.root}/reload-ok" || exit 42
python3 "{self.source}/scripts/groundwork-update.py" register "{self.source}" 1 0 "{self.home}/Notes"
''')
            self.candidate()
            with self.assertRaises(updater.UpdateError):
                updater.update('layer')
            pending = json.loads(self.config.read_text())['pending_keyd']
            self.assertEqual(pending['previous_sha256'], copied_digest)
            self.assertEqual(hashlib.sha256(installed.read_bytes()).hexdigest(), copied_digest)
            (self.root / 'copy-ok').touch()
            (self.root / 'reload-ok').touch()
            updater.update('layer')
            self.assertNotIn('pending_keyd', json.loads(self.config.read_text()))
            self.assertEqual(installed.read_text(), 'newer desired mapping\n')
            installed.write_text('user edit after apply')
            with self.assertRaisesRegex(updater.UpdateError, 'changed locally'):
                updater.update('layer')

    def test_t3_or_flatpak_failure_cannot_mark_full_success(self):
        for failure in ['t3', 'flatpak']:
            self.failure = failure
            with self.assertRaises(updater.UpdateError):
                updater.update('all')
            self.assertFalse((self.state / 'update-all-last-success.json').exists())

    def test_freetube_flatpak_update_failure_warns_and_finishes(self):
        self.freetube_update_failure = True
        with patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO) as errors:
            self.assertEqual(updater.main(['all']), 1)
        receipt = json.loads((self.state / 'update-all-last-success.json').read_text())
        self.assertEqual(receipt['result'], 'completed_with_warnings')
        self.assertIn('Omarchy update command completed', receipt['completed'])
        self.assertIn('Groundwork configuration applied', receipt['completed'])
        self.assertIn('T3 Code Nightly update command completed', receipt['completed'])
        self.assertIn('Bitwarden user Flatpak update command completed', receipt['completed'])
        self.assertIn('FreeTube user Flatpak update failed', receipt['completed'])
        self.assertNotIn('FreeTube user Flatpak update command completed', receipt['completed'])
        self.assertEqual(len(receipt['warnings']), 1)
        self.assertIn('FreeTube user Flatpak update failed', receipt['warnings'][0])
        self.assertIn('Warning: FreeTube user Flatpak update failed', errors.getvalue())
        self.assertIn(['flatpak', '--user', 'update', '--noninteractive', 'com.bitwarden.desktop'], self.machine_calls)
        self.assertIn(['flatpak', '--user', 'update', '--noninteractive', 'io.freetubeapp.FreeTube'], self.machine_calls)

    def large_pages(self, page=16384, candidate=CANDIDATE, archive=None, sums=None, error=None):
        archive = t3_archive(candidate) if archive is None else archive
        sums = sums or f'{hashlib.sha256(archive).hexdigest()}  t3-{candidate}-linux-arm64.tar.gz\n'
        responses = {updater.T3_RELEASES: json.dumps([{'tag_name': 'v0.2.0'}, {'tag_name': f'v{candidate}'}]).encode(),
                     f'{updater.T3_DOWNLOADS}/v{candidate}/SHA256SUMS': sums.encode(),
                     f'{updater.T3_DOWNLOADS}/v{candidate}/t3-{candidate}-linux-arm64.tar.gz': archive,
                     updater.T3_INSTALLER: b'#!/bin/sh\n'}
        self.smoke_version = candidate
        self.fetched = []
        def fetch(url, deadline=60):
            self.fetched.append(url)
            if error:
                raise error
            return responses[url]
        for item in [patch.object(updater, 'page_size', return_value=page), patch.object(updater, 'fetch', side_effect=fetch)]:
            item.start()
            self.addCleanup(item.stop)

    def update_all(self, result='completed'):
        with patch('sys.stdout', new_callable=io.StringIO) as output, patch('sys.stderr', new_callable=io.StringIO) as errors:
            self.assertEqual(updater.update('all'), result)
        receipt = json.loads((self.state / 'update-all-last-success.json').read_text())
        self.assertEqual(receipt['result'], result)
        self.assertIn('Bitwarden user Flatpak update command completed', receipt['completed'])
        self.assertIn('FreeTube user Flatpak update command completed', receipt['completed'])
        return output.getvalue() + errors.getvalue(), receipt

    def candidate_smokes(self):
        return [(call, kwargs) for call, kwargs in zip(self.machine_calls, self.machine_kwargs)
                if Path(call[0]).name == 'bwrap' and '--version' in call]

    def assert_smoke_is_confined(self, call, kwargs):
        self.assertEqual(Path(call[0]).name, 'bwrap')
        for flag in ('--die-with-parent', '--unshare-pid', '--unshare-net', '--ro-bind', '--bind', '--chdir', '--'):
            self.assertIn(flag, call)
        binary = call[call.index('--') + 1]
        chdir = call[call.index('--chdir') + 1]
        self.assertEqual(call[call.index('--') + 2], '--version')
        self.assertEqual(chdir, str(Path(binary).parent))
        self.assertEqual(kwargs['cwd'], chdir)
        bound = None
        index = 0
        while index < len(call) and call[index] != '--':
            if call[index] == '--bind':
                bound = call[index + 1]
            if call[index] == '--ro-bind':
                self.assertNotIn('groundwork-t3-', call[index + 1])
            index += 1
        self.assertIsNotNone(bound)
        self.assertIn('groundwork-t3-', bound)
        self.assertIn('/usr', call)
        env = kwargs['env']
        self.assertEqual(set(env), {'HOME', 'TMPDIR', 'PATH', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME',
                                     'XDG_DATA_HOME', 'XDG_RUNTIME_DIR', 'XDG_STATE_HOME'})
        self.assertEqual(env['PATH'], '/usr/bin:/bin')
        self.assertTrue(env['HOME'].startswith(bound + '/'))
        self.assertTrue(env['TMPDIR'].startswith(bound + '/'))
        for key, value in env.items():
            if key.startswith('XDG_'):
                self.assertTrue(value.startswith(bound + '/'), key)
        self.assertNotEqual(env['HOME'], os.environ.get('HOME'))
        self.assertIs(kwargs.get('stdin'), subprocess.DEVNULL)
        self.assertEqual(kwargs['deadline'], updater.T3_CANDIDATE_SMOKE_SECONDS)
        self.assertFalse(Path(bound).exists())

    def installer_versions(self):
        return [kwargs['extra_env']['T3CODE_VERSION'] for call, kwargs in zip(self.machine_calls, self.machine_kwargs)
                if call[0] == 'sh']

    def assert_t3_kept(self, output, reason):
        self.assertFalse(any(call[:2] == ['t3', 'update'] for call in self.machine_calls))
        self.assertEqual(self.t3_installed, '0.0.1-nightly.fixture')
        self.assertIn(f'T3 Code update skipped: {reason}', output)
        self.assertIn('keeping 0.0.1-nightly.fixture.', output)

    def test_large_pages_keep_installed_t3_when_candidate_has_4k_segments(self):
        self.large_pages()
        self.readelf = {'t3': READELF_BAD}
        output, _ = self.update_all()
        self.assert_t3_kept(output, 'newer linux-arm64 builds crash on 16 KiB pages (pingdotgg/t3code#23433)')
        rejected = json.loads((self.state / 't3-rejected.json').read_text())
        self.assertEqual(rejected, {'16384': [CANDIDATE]})
        self.assertFalse(self.candidate_smokes())

    def test_rejected_candidate_is_not_downloaded_again(self):
        self.large_pages()
        self.readelf = {'t3': READELF_BAD}
        self.update_all()
        self.fetched.clear()
        output, _ = self.update_all()
        self.assert_t3_kept(output, 'newer linux-arm64 builds crash on 16 KiB pages')
        self.assertFalse(any(url.endswith('.tar.gz') for url in self.fetched))

    def test_every_elf_file_in_the_candidate_is_checked(self):
        self.large_pages()
        self.readelf = {'addon.node': READELF_BAD}
        output, _ = self.update_all()
        self.assert_t3_kept(output, 'newer linux-arm64 builds crash')
        checked = [Path(call[2]).name for call in self.machine_calls if call[0] == 'readelf']
        self.assertEqual(sorted(checked), ['addon.node', 't3'])

    def test_page_size_is_named_in_the_skip_message(self):
        self.large_pages(page=65536)
        self.readelf = {'t3': READELF_GOOD.replace('0x10000', '0x4000')}
        output, _ = self.update_all()
        self.assert_t3_kept(output, 'newer linux-arm64 builds crash on 64 KiB pages')

    def test_large_pages_install_exactly_the_checked_candidate(self):
        self.large_pages()
        output, receipt = self.update_all()
        self.assertIn(['t3', 'update', '--channel', 'nightly', '--yes', CANDIDATE], self.machine_calls)
        self.assertIn(f'T3 Code Nightly updated to {CANDIDATE}', receipt['completed'])
        smokes = self.candidate_smokes()
        self.assertEqual(len(smokes), 1)
        self.assert_smoke_is_confined(*smokes[0])

    def test_smoke_command_uses_the_resolved_path_when_tmpdir_is_a_symlink(self):
        self.large_pages()
        real = self.root / 'real-tmp'
        real.mkdir()
        link = self.root / 'linked-tmp'
        link.symlink_to(real, target_is_directory=True)
        previous = tempfile.tempdir
        tempfile.tempdir = str(link)
        self.addCleanup(setattr, tempfile, 'tempdir', previous)
        with patch.dict(os.environ, TMPDIR=str(link)):
            self.update_all()
        smokes = self.candidate_smokes()
        self.assertEqual(len(smokes), 1)
        call, kwargs = smokes[0]
        self.assert_smoke_is_confined(call, kwargs)
        binary = call[call.index('--') + 1]
        chdir = call[call.index('--chdir') + 1]
        bind = call[call.index('--bind') + 1]
        resolved = os.path.realpath(link)
        self.assertEqual(call[call.index('--bind') + 2], bind)
        self.assertEqual(bind, os.path.realpath(bind))
        self.assertTrue(bind.startswith(resolved + os.sep))
        self.assertEqual(chdir, os.path.realpath(chdir))
        self.assertTrue(chdir.startswith(bind + os.sep))
        self.assertEqual(binary, os.path.realpath(binary))
        self.assertEqual(str(Path(binary).parent), chdir)
        self.assertEqual(kwargs['cwd'], chdir)
        for argument in call:
            self.assertNotIn(str(link), argument)
        for key, value in kwargs['env'].items():
            if key == 'PATH':
                continue
            self.assertEqual(value, os.path.realpath(value))
            self.assertTrue(value.startswith(bind + os.sep), key)
            self.assertNotIn(str(link), value)

    def test_aligned_candidate_that_crashes_is_rejected(self):
        self.large_pages()
        self.candidate_crashes = True
        output, _ = self.update_all()
        self.assert_t3_kept(output, 'the newest linux-arm64 build does not start on 16 KiB pages '
                            '(crashed (SIGSEGV); pingdotgg/t3code#23433)')
        rejected = json.loads((self.state / 't3-rejected.json').read_text())
        self.assertEqual(rejected, {'16384': [CANDIDATE]})
        smokes = self.candidate_smokes()
        self.assertEqual(len(smokes), 1)
        self.assert_smoke_is_confined(*smokes[0])
        call, kwargs = smokes[0]
        readelfs = [item[2] for item in self.machine_calls if item[:2] == ['readelf', '-lW']]
        self.assertTrue(readelfs)
        self.assertTrue(all(path.startswith(str(Path(kwargs['cwd']).parent)) for path in readelfs))

    def test_smoke_timeout_or_wrong_version_is_not_remembered(self):
        self.large_pages()
        self.candidate_smoke_error = updater.UpdateError(
            f'bwrap exceeded its {updater.T3_CANDIDATE_SMOKE_SECONDS}-second deadline. Retry after repairing that stage.')
        output, receipt = self.update_all(result='completed_with_warnings')
        self.assert_t3_kept(output, 'the newest linux-arm64 build was not smoke-tested')
        self.assertIn('exceeded its', output)
        self.assertIn('deadline', output)
        self.assertFalse((self.state / 't3-rejected.json').exists())
        self.assertTrue(receipt['warnings'])
        self.candidate_smoke_error = None
        self.smoke_version = '0.0.0-nightly.nope'
        self.machine_calls.clear()
        output, _ = self.update_all(result='completed_with_warnings')
        self.assert_t3_kept(output, f'the newest linux-arm64 build reported 0.0.0-nightly.nope, not {CANDIDATE}')
        self.assertFalse((self.state / 't3-rejected.json').exists())

    def test_missing_bwrap_skips_without_running_the_candidate(self):
        self.large_pages()
        def which(name):
            return None if name == 'bwrap' else '/fixture/' + name
        with patch.object(updater.shutil, 'which', side_effect=which):
            output, receipt = self.update_all(result='completed_with_warnings')
        self.assert_t3_kept(output, updater.T3_NO_BWRAP)
        self.assertIn(f'Warning: {updater.T3_NO_BWRAP}', output)
        self.assertEqual(receipt['warnings'], [updater.T3_NO_BWRAP])
        self.assertFalse(self.candidate_smokes())
        self.assertFalse(any(url.endswith('.tar.gz') for url in self.fetched))
        self.assertFalse((self.state / 't3-rejected.json').exists())

    def test_failed_t3_update_keeps_a_working_install(self):
        self.large_pages()
        self.t3_update_fails = True
        with patch('sys.stdout', new_callable=io.StringIO) as output, patch('sys.stderr', new_callable=io.StringIO) as errors:
            self.assertEqual(updater.main(['all']), 1)
        text = output.getvalue() + errors.getvalue()
        detail = 't3 failed (exit 1)'
        self.assertNotIn('Update incomplete', text)
        self.assertIn(f'T3 Code {CANDIDATE} was not installed ({detail}). Keeping 0.0.1-nightly.fixture.', text)
        self.assertIn(f'Warning: T3 Code {CANDIDATE} was not installed ({detail}); kept 0.0.1-nightly.fixture.', text)
        self.assertEqual(self.t3_installed, '0.0.1-nightly.fixture')
        self.assertIn(['t3', 'update', '--channel', 'nightly', '--yes', CANDIDATE], self.machine_calls)
        self.assertIn(['flatpak', '--user', 'update', '--noninteractive', 'com.bitwarden.desktop'], self.machine_calls)
        receipt = json.loads((self.state / 'update-all-last-success.json').read_text())
        self.assertEqual(receipt['result'], 'completed_with_warnings')
        self.assertEqual(receipt['warnings'], [f'T3 Code {CANDIDATE} was not installed ({detail}); kept 0.0.1-nightly.fixture.'])
        self.assertIn('T3 Code update skipped on 16 KiB pages; kept 0.0.1-nightly.fixture', receipt['completed'])
        self.assertIn('Bitwarden user Flatpak update command completed', receipt['completed'])
        self.assertFalse((self.state / 't3-rejected.json').exists())

    def test_t3_update_failure_is_remembered_only_when_the_candidate_did_not_start(self):
        cases = [
            (updater.UpdateError('t3 exceeded its 300-second deadline. Retry after repairing that stage.'),
             False, 'exceeded its 300-second deadline'),
            (OSError('network unreachable'), False, 'network unreachable'),
            (updater.UpdateError('t3 failed (exit 1).\nconnection refused'), False, 'connection refused'),
            (updater.UpdateError('t3 failed (exit 1).\nPinnedRuntimeInstallError: staged binary crashed'),
             True, 'PinnedRuntimeInstallError'),
            (updater.UpdateError('t3 failed (exit -11).'), True, 'SIGSEGV'),
            (updater.UpdateError('bwrap failed (exit 139).'), True, 'SIGSEGV'),
        ]
        for error, remembered, detail in cases:
            with self.subTest(detail=detail):
                self.machine_calls.clear()
                self.machine_kwargs.clear()
                self.t3_installed = '0.0.1-nightly.fixture'
                self.t3_broken = False
                rejected = self.state / 't3-rejected.json'
                if rejected.exists():
                    rejected.unlink()
                receipt = self.state / 'update-all-last-success.json'
                if receipt.exists():
                    receipt.unlink()
                self.large_pages()
                self.t3_update_fails = True
                self.t3_update_error = error
                output, saved = self.update_all(result='completed_with_warnings')
                self.assertIn(detail, output)
                self.assertIn(f'was not installed ({updater.t3_failure_reason(error)})', output)
                self.assertEqual(self.t3_installed, '0.0.1-nightly.fixture')
                self.assertIn(['flatpak', '--user', 'update', '--noninteractive', 'com.bitwarden.desktop'], self.machine_calls)
                self.assertEqual(rejected.exists(), remembered)
                if remembered:
                    self.assertEqual(json.loads(rejected.read_text())['16384'], [CANDIDATE])
                self.assertTrue(any(detail in warning for warning in saved['warnings']))

    def test_failed_t3_update_repairs_when_the_installed_binary_no_longer_starts(self):
        self.large_pages()
        self.t3_update_fails = True
        self.break_version_after_failed_update = True
        output, receipt = self.update_all()
        self.assertIn(f'T3 Code {CANDIDATE} does not start after updating', output)
        self.assertEqual(self.installer_versions(), ['0.0.1-nightly.fixture'])
        self.assertIn('T3 Code repaired with 0.0.1-nightly.fixture', receipt['completed'])
        self.assertNotIn('warnings', receipt)
        self.assertFalse((self.state / 't3-rejected.json').exists())

    def test_failed_t3_update_reraises_when_the_version_changed(self):
        self.large_pages()
        self.t3_update_fails = True
        self.version_after_failed_update = '9.9.9-nightly.changed'
        with self.assertRaises(updater.UpdateError) as caught:
            updater.update('all')
        self.assertIn('t3 failed (exit 1)', str(caught.exception))
        self.assertEqual(self.t3_installed, '9.9.9-nightly.changed')
        self.assertFalse((self.state / 'update-all-last-success.json').exists())
        self.assertFalse(any(call[:3] == ['flatpak', '--user', 'update'] for call in self.machine_calls))
        self.assertFalse((self.state / 't3-rejected.json').exists())

    def test_large_pages_skip_t3_when_installed_build_is_newest(self):
        self.large_pages(candidate='0.0.1-nightly.fixture')
        self.update_all()
        self.assertFalse(any(call[:2] == ['t3', 'update'] for call in self.machine_calls))
        self.assertFalse(any(url.endswith('.tar.gz') for url in self.fetched))

    def test_large_pages_keep_installed_t3_when_candidate_cannot_be_downloaded(self):
        self.large_pages(error=OSError('network unreachable'))
        output, _ = self.update_all()
        self.assert_t3_kept(output, 'the newest linux-arm64 build could not be checked for 16 KiB pages (network unreachable)')

    def test_checksum_mismatch_keeps_installed_t3(self):
        self.large_pages(sums=f'{"0" * 64}  t3-{CANDIDATE}-linux-arm64.tar.gz\n')
        output, _ = self.update_all()
        self.assert_t3_kept(output, 'the newest linux-arm64 build could not be checked')
        self.assertIn('Checksum mismatch', output)

    def test_corrupt_archive_keeps_installed_t3(self):
        self.large_pages(archive=b'not a gzip archive')
        output, _ = self.update_all()
        self.assert_t3_kept(output, 'the newest linux-arm64 build could not be checked')

    def test_missing_readelf_is_named(self):
        self.large_pages()
        with patch.object(updater.shutil, 'which', side_effect=lambda name: None if name == 'readelf' else '/fixture/' + name):
            output, _ = self.update_all()
        self.assert_t3_kept(output, 'readelf is missing, so the newest build cannot be checked (install binutils)')

    def test_broken_installed_t3_is_repaired_with_an_aligned_candidate(self):
        self.large_pages()
        self.t3_broken = True
        output, receipt = self.update_all()
        self.assertEqual(self.installer_versions(), [CANDIDATE])
        self.assertIn(f'T3 Code repaired with {CANDIDATE}', receipt['completed'])
        self.assertIn('The installed T3 Code does not start', output)

    def test_broken_installed_t3_falls_back_to_the_known_good_pin(self):
        self.large_pages()
        self.t3_broken = True
        self.readelf = {'t3': READELF_BAD}
        _, receipt = self.update_all()
        self.assertEqual(self.installer_versions(), [updater.T3_KNOWN_GOOD_LARGE_PAGE])
        self.assertIn(f'T3 Code repaired with {updater.T3_KNOWN_GOOD_LARGE_PAGE}', receipt['completed'])

    def test_unrepairable_t3_prints_the_restore_command_and_continues(self):
        self.large_pages()
        self.t3_broken = True
        self.install_failures = {CANDIDATE, updater.T3_KNOWN_GOOD_LARGE_PAGE}
        output, receipt = self.update_all(result='completed_with_warnings')
        self.assertIn(f'T3CODE_VERSION={updater.T3_KNOWN_GOOD_LARGE_PAGE} sh', output)
        self.assertIn('T3 Code repair failed; restore command printed', receipt['completed'])
        self.assertIn('Warning: T3 Code does not start and could not be repaired', output)
        self.assertEqual(len(receipt['warnings']), 1)

    def test_unrepairable_t3_makes_update_all_exit_non_zero(self):
        self.large_pages()
        self.t3_broken = True
        self.install_failures = {CANDIDATE, updater.T3_KNOWN_GOOD_LARGE_PAGE}
        with patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(updater.main(['all']), 1)
        receipt = json.loads((self.state / 'update-all-last-success.json').read_text())
        self.assertEqual(receipt['result'], 'completed_with_warnings')
        self.assertIn(['flatpak', '--user', 'update', '--noninteractive', 'com.bitwarden.desktop'], self.machine_calls)
        self.assertIn(['flatpak', '--user', 'update', '--noninteractive', 'io.freetubeapp.FreeTube'], self.machine_calls)

    def test_successful_update_all_exits_zero(self):
        with patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(updater.main(['all']), 0)

    def test_candidate_that_fails_after_updating_is_rolled_back(self):
        self.large_pages()
        self.break_after_update = True
        output, receipt = self.update_all()
        self.assertIn(f'T3 Code {CANDIDATE} does not start after updating (pingdotgg/t3code#23433).', output)
        self.assertEqual(self.installer_versions(), ['0.0.1-nightly.fixture'])
        self.assertIn('T3 Code repaired with 0.0.1-nightly.fixture', receipt['completed'])

    def test_small_pages_update_t3_without_checking_the_candidate(self):
        with patch.object(updater, 't3_latest_nightly', side_effect=AssertionError('unexpected check')):
            updater.update('all')
        self.assertIn(['t3', 'update', '--channel', 'nightly', '--yes'], self.machine_calls)

    def test_concurrent_update_is_rejected(self):
        updater.private_parent(self.state / 'update.lock')
        with (self.state / 'update.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(updater.UpdateError, 'Another Groundwork'):
                updater.update('layer')

    def test_update_reads_choices_after_a_setup_completes_before_lock_acquisition(self):
        updater.register(str(self.source), '1', '0', str(self.home / 'old-vault'))
        real_transaction = updater.transaction
        @contextmanager
        def setup_finishes_first():
            updater.register(str(self.source), '0', '0', str(self.home / 'new-vault'))
            with real_transaction() as lock:
                yield lock
        with patch.object(updater, 'transaction', side_effect=setup_finishes_first):
            updater.update('layer')
        saved = json.loads(self.config.read_text())
        self.assertFalse(saved['keyd'])
        self.assertEqual(saved['obsidian_vault'], str(self.home / 'new-vault'))
        self.assertNotIn('pending_keyd', saved)

    def test_changed_managed_file_or_symlink_settings_are_preserved(self):
        self.input.unlink()
        self.input.write_text('local override')
        with self.assertRaisesRegex(updater.UpdateError, 'local override'):
            updater.update('layer')
        self.assertEqual(self.input.read_text(), 'local override')
        self.config.rename(self.config.with_suffix('.original'))
        self.config.symlink_to(self.config.with_suffix('.original'))
        with self.assertRaisesRegex(updater.UpdateError, 'regular'):
            updater.settings()

    def test_help_and_invalid_arguments_run_no_commands_or_writes(self):
        before = self.config.read_bytes()
        with patch.object(updater, 'run', side_effect=AssertionError('unexpected command')):
            for scope in ['layer', 'all']:
                self.assertEqual(updater.main([scope, '--help']), 0)
                for invalid in [['--unknown'], ['extra'], ['--help', '--unknown'], ['--check', 'extra']]:
                    self.assertEqual(updater.main([scope, *invalid]), 64)
        self.assertEqual(self.config.read_bytes(), before)

    def test_registration_preserves_foreign_update_command(self):
        command = self.home / '.local/bin/update-all'
        command.write_text('user-owned command')
        with self.assertRaisesRegex(updater.UpdateError, 'another setup'):
            updater.register(str(self.source), '0', '0', str(self.home / 'Notes'))
        self.assertEqual(command.read_text(), 'user-owned command')

    def test_actual_installed_launchers_expose_help_and_reject_invalid_input(self):
        for name in ['update-all', 'groundwork-update']:
            script = self.home / '.local/bin' / name
            for arguments, expected in [(['--help'], 0), (['--unknown'], 64), (['--help', 'extra'], 64)]:
                result = subprocess.run(['bash', str(script), *arguments], capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, expected, result.stderr)


    def enable_saved_blip(self):
        saved = json.loads(self.config.read_text())
        saved['blip'] = True
        self.config.write_text(json.dumps(saved) + '\n')
        self.blip_listed = True

    def blip_update_calls(self):
        return [call for call in self.machine_calls if call[:3] == ['omarchy', 'plugin', 'update']]

    def test_blip_update_is_skipped_without_a_tty(self):
        self.enable_saved_blip()
        output = io.StringIO()
        with redirect_stdout(output):
            updater.update('all')
        self.assertEqual(self.blip_update_calls(), [])
        self.assertNotIn('--yes', [arg for call in self.machine_calls if call[:2] == ['omarchy', 'plugin'] for arg in call])
        self.assertIn('interactive terminal', output.getvalue())
        self.assertIn('never passed --yes', output.getvalue())

    def test_layer_update_does_not_update_blip(self):
        self.enable_saved_blip()
        with patch.object(updater, 'interactive_terminal', return_value=True):
            updater.update('layer')
        self.assertFalse(any(call[:2] == ['omarchy', 'plugin'] for call in self.machine_calls))

    def seed_blip_checkout(self):
        plugin = self.home / '.config/omarchy/plugins/nixfred.blip'
        (plugin / 'bridge/mac').mkdir(parents=True)
        (plugin / 'bridge/mac/install.sh').write_text('old\n')
        (plugin / 'README.md').write_text('readme\n')
        self.command('git', 'init', '-b', 'main', str(plugin))
        self.commit(plugin)
        self.blip_plugin = plugin
        return plugin

    def test_blip_update_reminds_when_bridge_changes_and_never_passes_yes(self):
        self.enable_saved_blip()
        self.seed_blip_checkout()
        self.blip_rewrite = 'bridge'
        output = io.StringIO()
        with redirect_stdout(output), patch.object(updater, 'interactive_terminal', return_value=True):
            updater.update('all')
        updates = self.blip_update_calls()
        self.assertEqual(updates, [['omarchy', 'plugin', 'update', 'nixfred.blip']])
        index = self.machine_calls.index(updates[0])
        self.assertIs(self.machine_kwargs[index].get('capture'), False)
        text = output.getvalue()
        self.assertIn('Blip changed files under bridge/.', text)
        self.assertIn('Re-run blip-setup.', text)
        self.assertIn('GATEWAY_USER@GATEWAY_MAGICDNS_NAME', text)
        self.assertFalse(any('blip-setup' in arg for call in self.machine_calls for arg in call))

    def test_blip_update_without_bridge_changes_does_not_remind(self):
        self.enable_saved_blip()
        self.seed_blip_checkout()
        self.blip_rewrite = 'readme'
        output = io.StringIO()
        with redirect_stdout(output), patch.object(updater, 'interactive_terminal', return_value=True):
            updater.update('all')
        self.assertEqual(self.blip_update_calls(), [['omarchy', 'plugin', 'update', 'nixfred.blip']])
        self.assertNotIn('Re-run blip-setup.', output.getvalue())


class T3CandidateTests(unittest.TestCase):
    def test_load_alignments_read_only_load_segments(self):
        with patch.object(updater, 'run', return_value=READELF_BAD):
            self.assertEqual(updater.load_alignments('t3'), [0x10000, 0x10000, 0x1000, 0x1000])
        with patch.object(updater, 'run', return_value=READELF_GOOD):
            self.assertEqual(min(updater.load_alignments('t3')), 0x10000)
        with patch.object(updater, 'run', return_value='not an ELF file'):
            self.assertEqual(updater.load_alignments('t3'), [])

    @unittest.skipUnless(shutil.which('readelf'), 'readelf is not installed')
    def test_load_alignments_parse_real_readelf_output(self):
        binary = os.path.realpath(sys.executable)
        listing = subprocess.run(['readelf', '-lW', binary], capture_output=True, text=True, check=True).stdout
        expected = [int(line.split()[-1], 16) for line in listing.splitlines() if line.split()[:1] == ['LOAD']]
        alignments = updater.load_alignments(binary)
        self.assertTrue(alignments)
        self.assertEqual(alignments, expected)
        for alignment in alignments:
            self.assertGreater(alignment, 0)
            self.assertEqual(alignment & (alignment - 1), 0, hex(alignment))

    def test_download_is_checksum_verified_and_every_elf_is_checked(self):
        archive = t3_archive(CANDIDATE)
        digest = hashlib.sha256(archive).hexdigest()
        responses = {f'{updater.T3_DOWNLOADS}/v{CANDIDATE}/SHA256SUMS': f'{digest}  t3-{CANDIDATE}-linux-arm64.tar.gz\n'.encode(),
                     f'{updater.T3_DOWNLOADS}/v{CANDIDATE}/t3-{CANDIDATE}-linux-arm64.tar.gz': archive}
        alignments = {'t3': READELF_GOOD, 'addon.node': READELF_BAD}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(updater, 'fetch', side_effect=lambda url, deadline=60: responses[url]), \
                patch.object(updater, 'run', side_effect=lambda args, **kwargs: alignments[Path(args[2]).name]):
            root = updater.t3_download(CANDIDATE, directory)
            self.assertEqual(updater.misaligned_elf_files(root, 16384),
                             [f't3-{CANDIDATE}-linux-arm64/node_modules/addon/addon.node'])
            self.assertEqual(updater.misaligned_elf_files(root, 4096), [])
        responses[f'{updater.T3_DOWNLOADS}/v{CANDIDATE}/SHA256SUMS'] = b'0' * 64 + f'  t3-{CANDIDATE}-linux-arm64.tar.gz\n'.encode()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(updater, 'fetch', side_effect=lambda url, deadline=60: responses[url]):
            with self.assertRaisesRegex(updater.UpdateError, 'Checksum mismatch'):
                updater.t3_download(CANDIDATE, directory)

    def test_fetch_enforces_a_total_deadline(self):
        class Raw:
            _sock = socket.socket()
        class Slow:
            fp = type('Fp', (), {'raw': Raw})()
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def isclosed(self):
                return False
            def read1(self, size):
                time.sleep(0.05)
                return b'x'
        self.addCleanup(Raw._sock.close)
        with patch.object(updater.https_opener, 'open', return_value=Slow()):
            with self.assertRaisesRegex(updater.UpdateError, 'exceeded'):
                updater.fetch('https://example.invalid/archive', deadline=0.2)

    def test_fetch_works_without_the_private_socket_attribute(self):
        class Response:
            fp = object()
            def __init__(self):
                self.chunks = [b'ab', b'cd']
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def isclosed(self):
                return not self.chunks
            def read1(self, size):
                return self.chunks.pop(0)
        with patch.object(updater.https_opener, 'open', return_value=Response()):
            self.assertEqual(updater.fetch('https://example.invalid/archive'), b'abcd')

    def serve_once(self, respond):
        server = socket.socket()
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        def handle():
            connection, _ = server.accept()
            with connection:
                connection.recv(4096)
                respond(connection)
        thread = threading.Thread(target=handle, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.close)
        return f'http://127.0.0.1:{server.getsockname()[1]}/archive'

    def test_fetch_stalled_read_ends_at_the_deadline_not_after_it(self):
        def respond(connection):
            connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n')
            time.sleep(1.2)
            connection.sendall(b'x')
            # Stall here until the client gives up.
            connection.settimeout(5)
            try:
                connection.recv(1)
            except OSError:
                pass
        url = self.serve_once(respond)
        started = time.monotonic()
        with self.assertRaisesRegex(updater.UpdateError, 'exceeded 2 seconds'):
            updater.fetch(url, deadline=2)
        # With a fixed per-read timeout the stall would end near 3.2 seconds.
        self.assertLess(time.monotonic() - started, 2.6)

    def test_fetch_refuses_a_redirect_to_http(self):
        url = self.serve_once(lambda connection: connection.sendall(
            b'HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:1/elsewhere\r\nContent-Length: 0\r\n\r\n'))
        with self.assertRaisesRegex(updater.UpdateError, 'non-https redirect'):
            updater.fetch(url)

    def test_redirect_handler_allows_https_and_refuses_other_schemes(self):
        handler = updater.HttpsOnlyRedirects()
        request = updater.urllib.request.Request('https://example.invalid/a')
        headers = {}
        followed = handler.redirect_request(request, None, 302, 'Found', headers, 'https://example.invalid/b')
        self.assertEqual(followed.full_url, 'https://example.invalid/b')
        for target in ('http://example.invalid/b', 'ftp://example.invalid/b', 'file:///etc/passwd'):
            with self.assertRaises(updater.UpdateError):
                handler.redirect_request(request, None, 302, 'Found', headers, target)

    def test_latest_nightly_matches_the_upstream_installer_selection(self):
        releases = [{'tag_name': 'v0.2.0'}, {'tag_name': 'v0.1.3-preview.1.2'},
                    {'tag_name': 'v0.1.3-nightly.20261024.3604'}, {'tag_name': 'v0.1.3-nightly.20261023.3592'}]
        with patch.object(updater, 'fetch', return_value=json.dumps(releases).encode()):
            self.assertEqual(updater.t3_latest_nightly(), '0.1.3-nightly.20261024.3604')

    def test_run_replacement_env_drops_the_caller_environment(self):
        reported = updater.run([sys.executable, '-c', 'import os; print("\\n".join(sorted(os.environ)))'],
                               env={'FOO': 'bar', 'PATH': '/usr/bin:/bin'})
        names = set(reported.splitlines())
        # CPython may add LC_CTYPE for its own filesystem encoding. Nothing else from the caller is copied.
        self.assertTrue({'FOO', 'PATH'} <= names)
        self.assertFalse(names - {'FOO', 'PATH', 'LC_CTYPE'})
        self.assertNotIn('HOME', names)
        self.assertNotIn(os.environ.get('HOME', ''), reported)

    def _candidate_responses(self, script):
        payload = script.encode()
        buffer = io.BytesIO()
        info = tarfile.TarInfo(f't3-{CANDIDATE}-linux-arm64/t3')
        info.size = len(payload)
        info.mode = 0o755
        with tarfile.open(fileobj=buffer, mode='w:gz') as bundle:
            bundle.addfile(info, io.BytesIO(payload))
        packed = buffer.getvalue()
        digest = hashlib.sha256(packed).hexdigest()
        return {updater.T3_RELEASES: json.dumps([{'tag_name': f'v{CANDIDATE}'}]).encode(),
                f'{updater.T3_DOWNLOADS}/v{CANDIDATE}/SHA256SUMS': f'{digest}  t3-{CANDIDATE}-linux-arm64.tar.gz\n'.encode(),
                f'{updater.T3_DOWNLOADS}/v{CANDIDATE}/t3-{CANDIDATE}-linux-arm64.tar.gz': packed}

    def test_candidate_check_rejects_a_crash_and_retries_a_hang(self):
        if shutil.which('bwrap') is None:
            self.skipTest('bubblewrap is not installed')
        real_which = shutil.which
        paths = []
        real_temporary = tempfile.TemporaryDirectory
        class Recording(real_temporary):
            def __enter__(self):
                path = super().__enter__()
                if 'groundwork-t3-' in path:
                    paths.append(path)
                return path
        def which(name):
            if name == 'bwrap':
                return real_which('bwrap')
            if name == 'readelf':
                return real_which('readelf') or '/usr/bin/readelf'
            return real_which(name)
        def check(script, deadline, grace):
            responses = self._candidate_responses(script)
            paths.clear()
            with tempfile.TemporaryDirectory() as state, \
                    patch.object(updater, 'STATE', Path(state)), \
                    patch.object(updater, 'fetch', side_effect=lambda url, deadline=60: responses[url]), \
                    patch.object(updater.shutil, 'which', side_effect=which), \
                    patch.object(updater.tempfile, 'TemporaryDirectory', Recording), \
                    patch.object(updater, 'T3_CANDIDATE_SMOKE_SECONDS', deadline), \
                    patch.object(updater, 'INTERRUPT_GRACE_SECONDS', grace):
                reason, version = updater.t3_candidate_check('0.0.1-nightly.fixture', 16384, [])
                rejected = (Path(state) / 't3-rejected.json').exists()
            self.assertEqual(version, CANDIDATE)
            self.assertTrue(paths)
            self.assertFalse(Path(paths[-1]).exists())
            return reason, rejected
        reason, rejected = check('#!/bin/sh\nkill -SEGV $$\n', updater.T3_CANDIDATE_SMOKE_SECONDS, 0.2)
        self.assertIn('crashed (SIGSEGV)', reason)
        self.assertTrue(rejected)
        reason, rejected = check('#!/bin/sh\nsleep 30\n', 0.2, 0.2)
        self.assertIn('deadline', reason)
        self.assertFalse(rejected)

    @unittest.skipUnless(shutil.which('bwrap'), 'bubblewrap is not installed')
    def test_sandbox_reaps_a_double_fork_and_sees_a_scrubbed_env(self):
        token = 'groundwork-t3-smoke-orphan'
        script = f'''#!/bin/bash
ok=1
case "$HOME" in */groundwork-t3-*/home) ;; *) ok=0 ;; esac
case "$TMPDIR" in */groundwork-t3-*/tmp) ;; *) ok=0 ;; esac
case "$XDG_CONFIG_HOME" in */groundwork-t3-*/home/.config) ;; *) ok=0 ;; esac
case "$XDG_CACHE_HOME" in */groundwork-t3-*) ;; *) ok=0 ;; esac
case "$XDG_DATA_HOME" in */groundwork-t3-*) ;; *) ok=0 ;; esac
case "$XDG_STATE_HOME" in */groundwork-t3-*) ;; *) ok=0 ;; esac
case "$XDG_RUNTIME_DIR" in */groundwork-t3-*) ;; *) ok=0 ;; esac
case "$(pwd)" in */t3-{CANDIDATE}-linux-arm64) ;; *) ok=0 ;; esac
[ "$PATH" = /usr/bin:/bin ] || ok=0
[ -z "$USER" ] || ok=0
if [ "$ok" -eq 1 ]; then echo "t3 v{CANDIDATE}"; else echo "t3 vunscrubbed"; fi
# A real double-fork that ignores the shell exiting. The pid namespace must reap it.
(
  setsid bash -c 'exec -a {token} sleep 30' </dev/null >/dev/null 2>&1 &
) &
exit 0
'''
        self.addCleanup(subprocess.run, ['pkill', '-f', token], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        paths = []
        real_temporary = tempfile.TemporaryDirectory
        class Recording(real_temporary):
            def __enter__(self):
                path = super().__enter__()
                if 'groundwork-t3-' in path:
                    paths.append(path)
                return path
        responses = self._candidate_responses(script)
        real_which = shutil.which
        def which(name):
            if name == 'bwrap':
                return real_which('bwrap')
            if name == 'readelf':
                return real_which('readelf') or '/usr/bin/readelf'
            return real_which(name)
        with tempfile.TemporaryDirectory() as state, \
                patch.object(updater, 'STATE', Path(state)), \
                patch.object(updater, 'fetch', side_effect=lambda url, deadline=60: responses[url]), \
                patch.object(updater.shutil, 'which', side_effect=which), \
                patch.object(updater.tempfile, 'TemporaryDirectory', Recording):
            reason, version = updater.t3_candidate_check('0.0.1-nightly.fixture', 16384, [])
        self.assertEqual(version, CANDIDATE)
        self.assertIsNone(reason)
        self.assertTrue(paths)
        self.assertFalse(Path(paths[-1]).exists())
        time.sleep(0.3)
        found = subprocess.run(['pgrep', '-a', '-f', token], capture_output=True, text=True)
        self.assertNotEqual(found.returncode, 0, found.stdout)


# A child that records which signal stopped it.
SIGNAL_CHILD = r"""
import os, signal, sys, time
report = sys.argv[1]
def record(signum, _frame):
    with open(report, 'w') as output:
        output.write(signal.Signals(signum).name)
    sys.exit(130)
if len(sys.argv) > 2:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
else:
    signal.signal(signal.SIGINT, record)
signal.signal(signal.SIGTERM, record)
with open(report + '.pid', 'w') as output:
    output.write(str(os.getpid()))
time.sleep(30)
"""

# Run one interactive stage under a real pseudo-terminal and echo its output.
PTY_BRIDGE = r"""
import importlib.util, os, pty, select, sys, time
if sys.argv[1] == 'child':
    spec = importlib.util.spec_from_file_location('updater', sys.argv[2])
    updater = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(updater)
    updater.HEARTBEAT_SECONDS = 0.1
    updater.run([sys.executable, '-c', 'import time; time.sleep(0.8)'], capture=False, deadline=8)
    assert os.tcgetpgrp(0) == os.getpgrp()
    print('interactive stage finished')
    raise SystemExit(0)
pid, master = pty.fork()
if pid == 0:
    os.execv(sys.executable, [sys.executable, '-B', sys.argv[0], 'child', sys.argv[1]])
chunks, deadline = [], time.monotonic() + 9
while time.monotonic() < deadline:
    ready, _, _ = select.select([master], [], [], 0.1)
    if ready:
        try:
            data = os.read(master, 65536)
        except OSError:
            data = b''
        chunks.append(data)
    waited, status = os.waitpid(pid, os.WNOHANG)
    if waited == pid:
        break
sys.stdout.buffer.write(b''.join(chunks))
raise SystemExit(os.WEXITSTATUS(status) if waited == pid else 124)
"""


class ProcessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='groundwork-process-')
        self.addCleanup(temporary.cleanup)
        self.report = Path(temporary.name) / 'signal'

    def child(self, *extra):
        return [sys.executable, '-c', SIGNAL_CHILD, str(self.report), *extra]

    def wait_for_child(self):
        for _ in range(100):
            if Path(str(self.report) + '.pid').exists():
                return
            time.sleep(0.02)

    def test_deadline_terminates_owned_child(self):
        with self.assertRaisesRegex(updater.UpdateError, 'deadline'):
            updater.run([sys.executable, '-c', 'import time; time.sleep(30)'], deadline=0.1)

    def test_interactive_stage_prints_no_heartbeat_over_its_terminal(self):
        script = Path(self.report.parent) / 'bridge.py'
        script.write_text(PTY_BRIDGE)
        result = subprocess.run([sys.executable, '-B', str(script), str(REPO / 'scripts/groundwork-update.py')],
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(b'interactive stage finished', result.stdout)
        self.assertNotIn(b'Still running', result.stdout)

    def test_captured_stage_still_reports_progress(self):
        with patch.object(updater, 'HEARTBEAT_SECONDS', 0.1), patch('sys.stdout', new_callable=io.StringIO) as output:
            updater.run([sys.executable, '-c', 'import time; time.sleep(0.5)'], deadline=8)
        self.assertIn('Still running', output.getvalue())

    def test_deadline_forwards_sigint_before_terminating(self):
        real_communicate = subprocess.Popen.communicate
        def communicate(process, *args, **kwargs):
            # Let the deadline fire only once the child has its SIGINT handler.
            self.wait_for_child()
            return real_communicate(process, *args, **kwargs)
        with patch.object(subprocess.Popen, 'communicate', communicate):
            with self.assertRaisesRegex(updater.UpdateError, 'deadline'):
                updater.run(self.child(), deadline=1)
        self.assertEqual(self.report.read_text(), 'SIGINT')

    def test_interrupt_forwards_sigint_to_the_stage(self):
        def interrupt(_signum, _frame):
            raise KeyboardInterrupt
        previous = signal.signal(signal.SIGALRM, interrupt)
        self.addCleanup(signal.signal, signal.SIGALRM, previous)
        self.addCleanup(signal.setitimer, signal.ITIMER_REAL, 0)
        real_communicate = subprocess.Popen.communicate
        def communicate(process, *args, **kwargs):
            # Interrupt only once the child has installed its SIGINT handler.
            if not signal.getitimer(signal.ITIMER_REAL)[0]:
                self.wait_for_child()
                signal.setitimer(signal.ITIMER_REAL, 0.2)
            return real_communicate(process, *args, **kwargs)
        with patch.object(subprocess.Popen, 'communicate', communicate):
            with self.assertRaises(KeyboardInterrupt):
                updater.run(self.child(), deadline=8)
        self.assertEqual(self.report.read_text(), 'SIGINT')

    def test_second_interrupt_during_the_grace_period_still_stops_the_child(self):
        previous = signal.signal(signal.SIGINT, signal.default_int_handler)
        self.addCleanup(signal.signal, signal.SIGINT, previous)
        # A separate process interrupts this one during the 30-second grace, but
        # only once the forwarded first SIGINT has been sent, so the second
        # cannot arrive before the grace loop starts.
        sent = Path(str(self.report) + '.sigint-sent')
        interrupter = subprocess.Popen(['sh', '-c', f'until [ -e {sent} ]; do sleep 0.02; done; kill -INT {os.getpid()}'])
        self.addCleanup(interrupter.wait)
        self.addCleanup(interrupter.kill)
        real_signal_group = updater.signal_group
        def signal_group(process, signum):
            alive = real_signal_group(process, signum)
            if signum == signal.SIGINT:
                sent.touch()
            return alive
        started = time.monotonic()
        with patch.object(updater, 'INTERRUPT_GRACE_SECONDS', 30), patch.object(updater, 'signal_group', signal_group):
            with self.assertRaises(KeyboardInterrupt):
                updater.run(self.child('ignore-sigint'), deadline=1)
        self.assertLess(time.monotonic() - started, 10)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(Path(str(self.report) + '.pid').read_text()), 0)

    def test_permission_errors_while_stopping_are_reported_not_raised(self):
        process = subprocess.Popen([sys.executable, '-c', 'pass'], start_new_session=True)
        process.wait()
        with patch.object(updater.os, 'killpg', side_effect=PermissionError), \
                patch('sys.stderr', new_callable=io.StringIO) as errors:
            updater.stop(process, grace=0.2)
        self.assertIn('Could not send SIGINT', errors.getvalue())

    def test_unreadable_process_group_is_still_stopped(self):
        process = subprocess.Popen([sys.executable, '-c', 'pass'], start_new_session=True)
        process.wait()
        with patch.object(updater.os, 'killpg', side_effect=PermissionError), \
                patch('sys.stderr', new_callable=io.StringIO) as errors:
            self.assertTrue(updater.group_alive(process))
        self.assertIn('Could not check the remaining processes', errors.getvalue())
        checked = []
        def killpg(group, signum):
            checked.append(signum)
            if signum == 0:
                raise PermissionError
        # The leader has exited, so only group_alive decides whether TERM follows.
        with patch.object(updater.os, 'killpg', killpg), patch('sys.stderr', new_callable=io.StringIO) as stopped:
            updater.stop(process, grace=0.2)
        self.assertIn(signal.SIGTERM, checked)
        self.assertGreater(checked.count(0), 1)
        self.assertEqual(stopped.getvalue().count('Could not check the remaining processes'), 1)

    def test_terminal_less_interactive_stage_keeps_its_heartbeat(self):
        with patch.object(updater, 'HEARTBEAT_SECONDS', 0.1), patch('sys.stdout', new_callable=io.StringIO) as output, \
                patch.object(updater.os, 'isatty', return_value=False):
            updater.run([sys.executable, '-c', 'import time; time.sleep(0.5)'], capture=False, deadline=8)
        self.assertIn('Still running', output.getvalue())

    def test_stage_ignoring_sigint_is_terminated_after_the_grace_period(self):
        started = time.monotonic()
        with patch.object(updater, 'INTERRUPT_GRACE_SECONDS', 0.3):
            with self.assertRaisesRegex(updater.UpdateError, 'deadline'):
                updater.run(self.child('ignore-sigint'), deadline=1)
        self.assertEqual(self.report.read_text(), 'SIGTERM')
        self.assertLess(time.monotonic() - started, 6)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(Path(str(self.report) + '.pid').read_text()), 0)


if __name__ == '__main__':
    unittest.main()
