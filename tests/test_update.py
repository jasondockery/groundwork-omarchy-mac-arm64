"""Real local Git transport; isolated home and mocked machine-owner commands."""
import fcntl
from contextlib import contextmanager, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import socket
import stat
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
CANDIDATE = '0.1.7-nightly.20261101.4000'
INSTALLED = '0.1.6-nightly.20261031.3956'


READELF_BAD = '''
Program Headers:
  Type           Offset   VirtAddr           PhysAddr           FileSiz  MemSiz   Flg Align
  PHDR           0x6dad000 0x00000000071ad000 0x00000000071ad000 0x0002a0 0x0002a0 R   0x8
  LOAD           0x000000 0x0000000000400000 0x0000000000400000 0x41d390 0x41d390 R   0x10000
  LOAD           0x420000 0x0000000000820000 0x0000000000820000 0x29d82f0 0x29d82f0 R E 0x10000
  LOAD           0x6dad000 0x00000000071ad000 0x00000000071ad000 0x000508 0x000508 R   0x1000
  LOAD           0x8faf000 0x000000001015d000 0x000000001015d000 0x1003000 0x1003000 R   0x1000
'''


# Decision 0018: the T3 0.1.5 and 0.1.6 nightlies have one LOAD with p_align
# 0x1000 whose p_offset and p_vaddr are both 64 KiB multiples.
READELF_RELAXED = '''
Program Headers:
  Type           Offset   VirtAddr           PhysAddr           FileSiz  MemSiz   Flg Align
  LOAD           0x000000 0x0000000000400000 0x0000000000400000 0x41d390 0x41d390 R   0x10000
  LOAD           0x420000 0x0000000000820000 0x0000000000820000 0x29d82f0 0x29d82f0 R E 0x10000
  LOAD           0x6dc0000 0x00000000071c0000 0x00000000071c0000 0x000508 0x000508 R   0x1000
'''


def synthetic_elf(segments, machine=183):
    """A little-endian ELF64 executable header with one LOAD per (p_offset, p_vaddr, p_align).

    `machine` is e_machine: 183 is EM_AARCH64, 62 is EM_X86_64. Only the
    headers are written. readelf -lW reads them as they are.
    """
    import struct
    phoff, phentsize = 64, 56
    header = struct.pack('<4sBBBBB7sHHIQQQIHHHHHH', b'\x7fELF', 2, 1, 1, 0, 0, b'\0' * 7,
                         2, machine, 1, segments[0][1], phoff, 0, 0, 64, phentsize, len(segments), 64, 0, 0)
    headers = b''.join(struct.pack('<IIQQQQQQ', 1, 4, offset, vaddr, vaddr, 0, 0x1000, align)
                       for offset, vaddr, align in segments)
    return header + headers


# A real SIGSEGV death. Exec resets the dumpable flag, so this process clears
# it (PR_SET_DUMPABLE = 4) immediately before signalling. That is what stops a
# piped core_pattern. The script does not lower RLIMIT_CORE: the smoke run sets
# that limit to 1, and the regression test raises it so only this prctl can
# explain a missing core file.
CRASH_WITHOUT_DUMP = '''#!/usr/bin/python3
import ctypes, os, signal
ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)
os.kill(os.getpid(), signal.SIGSEGV)
'''


def core_ceiling():
    """A core limit large enough that a file pattern would write a core."""
    _soft, hard = resource.getrlimit(resource.RLIMIT_CORE)
    if hard == 0 or (hard != resource.RLIM_INFINITY and hard <= 1):
        raise AssertionError(f'hard RLIMIT_CORE is {hard}, so the fixture cannot prove prctl stops the core')
    if hard == resource.RLIM_INFINITY or hard > 1 << 20:
        return 1 << 20
    return hard


def _allow_file_core(ceiling):
    resource.setrlimit(resource.RLIMIT_CORE, (ceiling, ceiling))


def _predicted_core(pattern, pid, cwd):
    """Path a file core_pattern would use, or None when it is a pipe or unknown."""
    if pattern.startswith('|'):
        return None
    comm = Path(sys.executable).name[:15]
    text = pattern.replace('%%', '\0')
    for spec in ('%h', '%t', '%s', '%c', '%d', '%P', '%i', '%I', '%u', '%g', '%E', '%f'):
        if spec in text:
            return None
    text = text.replace('%e', comm).replace('%p', str(pid)).replace('\0', '%')
    if '%' in text:
        return None
    path = Path(text)
    if not path.is_absolute():
        path = Path(cwd) / path
    return path


def run_dump_child(source, ceiling):
    """Run source with a raised core limit. Return status, stdout, and core paths."""
    temporary = tempfile.TemporaryDirectory(prefix='groundwork-core-')
    try:
        directory = temporary.name
        path = Path(directory) / 'fixture.py'
        path.write_text(source)
        path.chmod(0o755)
        process = subprocess.Popen(
            [sys.executable, str(path)], cwd=directory,
            preexec_fn=lambda: _allow_file_core(ceiling),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, _stderr = process.communicate(timeout=30)
        cores = []
        predicted = _predicted_core(Path('/proc/sys/kernel/core_pattern').read_text().strip(), process.pid, directory)
        if predicted is not None:
            cores.append(predicted)
        cores.extend(Path('/tmp').glob(f'core.*{process.pid}*'))
        cores.extend(Path(directory).glob(f'core.*{process.pid}*'))
        found = []
        for core in cores:
            if core.is_file() and core not in found:
                found.append(core)
        try:
            return process.returncode, stdout, found
        finally:
            for core in found:
                core.unlink(missing_ok=True)
    finally:
        temporary.cleanup()


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
        self.blip_listed = False
        self.blip_rewrite = None
        self.blip_plugin = None
        self.freetube_update_failure = False
        self.execute_apply = False
        self.real_run = updater.run
        self.run_patch = patch.object(updater, 'run', side_effect=self.run_command)
        self.run_patch.start()
        self.addCleanup(self.run_patch.stop)
        # The closing reports: recorded, quiet unless a test sets an answer.
        self.report_calls = []
        self.report_answers = {}
        self.real_run_report = updater.run_report

        # A closure, so other test classes that borrow this setUp get it too.
        def fake_report(label, argv, deadline):
            self.report_calls.append((label, argv, deadline))
            # The update has finished and recorded its receipt before any report runs.
            self.assertTrue((self.state / 'update-all-last-success.json').is_file())
            return self.report_answers.get(label, '')
        self.report_patch = patch.object(updater, 'run_report', side_effect=fake_report)
        self.report_patch.start()
        self.addCleanup(self.report_patch.stop)
        os.environ.pop(updater.SKIP_REPORTS_ENV, None)
        # Nothing may replace the test process (issue #119); tests that hand off patch this.
        reexec_patch = patch.object(updater, 'reexec', side_effect=AssertionError('unexpected re-exec'))
        reexec_patch.start()
        self.addCleanup(reexec_patch.stop)
        os.environ.pop(updater.REEXEC_ENV, None)

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
        if self.execute_apply and args[0] == 'bash' and str(args[1]).endswith('apply-layer.sh'):
            self.machine_calls.append(args)
            completed = subprocess.run(args, cwd=self.source, capture_output=True, text=True)
            if completed.returncode:
                raise updater.UpdateError(completed.stderr or completed.stdout or 'apply failed')
            return (completed.stdout or '').strip()
        self.machine_calls.append(args)
        self.machine_kwargs.append(kwargs)
        if args[0] == self.failure:
            raise updater.UpdateError('fixture stage failed')
        if args[0] == 't3':
            # Only an updater from before decision 0020 runs t3 (the previous-main fixture).
            return 't3 v0.0.1-nightly.fixture'
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

    def test_update_all_reconciles_the_t3_launcher_once_and_layer_does_not(self):
        updater.update('all')
        calls = [call for call in self.machine_calls if call[-2:-1] and call[-2].endswith('groundwork-t3-launcher.py')]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-1], 'reconcile')
        self.machine_calls.clear()
        updater.update('layer')
        self.assertFalse(any('groundwork-t3-launcher.py' in ' '.join(call) for call in self.machine_calls))

    def test_all_delegates_system_and_selected_app_updates_to_their_owners(self):
        updater.update('all')
        self.assertEqual(self.machine_calls[0], ['omarchy', 'update'])
        # T3 Code updates itself (decision 0020); update-all runs no t3 command.
        self.assertFalse(any(call[0] == 't3' for call in self.machine_calls))
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

    def test_setup_and_update_check_out_core_before_apply(self):
        text = (REPO / 'scripts/groundwork-update.py').read_text()
        update = text.split('def update(', 1)[1].split('def main(args):', 1)[0]
        merged = update.index("'merge', '--ff-only'")
        checked = update.index('checkout_core(source)', merged)
        applied = update.index('scripts/apply-layer.sh', checked)
        self.assertLess(merged, checked)
        self.assertLess(checked, applied)
        setup = text.split("args[0] == 'setup'", 1)[1].split("args[0] == 'marker'", 1)[0]
        self.assertLess(setup.index('checkout_core('), setup.index('apply-layer.sh'))

    def test_fast_forward_checks_out_core_before_apply_reads_the_brave_spec(self):
        # The spec is only in the submodule commit the gitlink moves to.
        # Apply must see it after the fast-forward, without a copy in this repo.
        os.environ['GIT_CONFIG_COUNT'] = '1'
        os.environ['GIT_CONFIG_KEY_0'] = 'protocol.file.allow'
        os.environ['GIT_CONFIG_VALUE_0'] = 'always'
        sub = self.root / 'core.git'
        work = self.root / 'core-src'
        self.command('git', 'init', '--bare', '--initial-branch=main', str(sub))
        self.command('git', 'clone', str(sub), str(work))
        (work / 'README').write_text('without the spec\n')
        self.commit(work)
        self.command('git', '-C', str(work), 'push', 'origin', 'main')
        old = self.command('git', '-C', str(work), 'rev-parse', 'HEAD')
        spec = '{"schemaVersion": 1, "marker": "only-after-checkout"}\n'
        (work / 'browser').mkdir()
        (work / 'browser/brave.json').write_text(spec)
        self.commit(work)
        self.command('git', '-C', str(work), 'push', 'origin', 'main')
        new = self.command('git', '-C', str(work), 'rev-parse', 'HEAD')
        self.command('git', '-C', str(self.seed), 'submodule', 'add', '--', str(sub), 'core')
        self.command('git', '-C', str(self.seed / 'core'), 'checkout', old)
        self.commit(self.seed)
        self.command('git', '-C', str(self.seed), 'push', 'origin', 'main')
        self.command('git', '-C', str(self.source), 'pull', '--ff-only', 'origin', 'main')
        self.command('git', '-C', str(self.source), 'submodule', 'update', '--init', '--', 'core')
        self.assertFalse((self.source / 'core/browser/brave.json').exists())
        pinned = self.git('rev-parse', 'HEAD')
        self.command('git', '-C', str(self.seed / 'core'), 'checkout', new)
        (self.seed / 'scripts/apply-layer.sh').write_text(
            '#!/bin/bash\nset -euo pipefail\n'
            'test -f core/browser/brave.json\n'
            'grep -q only-after-checkout core/browser/brave.json\n')
        self.commit(self.seed)
        self.command('git', '-C', str(self.seed), 'push', 'origin', 'main')
        self.git('fetch', 'origin', 'main')
        self.git('merge', '--ff-only', 'origin/main')
        self.assertFalse((self.source / 'core/browser/brave.json').exists())
        updater.preflight(self.source)
        (self.source / 'core/README').write_text('local edit\n')
        with self.assertRaisesRegex(updater.UpdateError, 'core submodule has local changes'):
            updater.preflight(self.source)
        self.command('git', '-C', str(self.source / 'core'), 'checkout', '--', 'README')
        updater.checkout_core(self.source)
        self.assertEqual((self.source / 'core/browser/brave.json').read_text(), spec)
        self.git('reset', '--hard', pinned)
        self.command('git', '-C', str(self.source / 'core'), 'checkout', old)
        self.assertFalse((self.source / 'core/browser/brave.json').exists())
        self.execute_apply = True
        updater.update('layer')
        self.assertEqual((self.source / 'core/browser/brave.json').read_text(), spec)
        self.assertIn(['bash', str(self.source / 'scripts/apply-layer.sh'), '--update'], self.machine_calls)
        self.assertEqual(self.git('status', '--porcelain', '--ignore-submodules=all'), '')
        shutil.rmtree(self.source / 'core')
        self.assertFalse((self.source / 'core/browser/brave.json').exists())
        updater.checkout_core(self.source)
        self.assertEqual((self.source / 'core/browser/brave.json').read_text(), spec)
        self.assertFalse((REPO / 'browser/brave.json').exists())
        calls = []
        real_git = updater.git

        def spy(source, *args, **kwargs):
            calls.append(args)
            return real_git(source, *args, **kwargs)

        with patch.object(updater, 'git', side_effect=spy):
            updater.checkout_core(self.source)
        self.assertFalse(any(args[:2] == ('submodule', 'update') for args in calls))
        self.assertEqual((self.source / 'core/browser/brave.json').read_text(), spec)

    def main_updater_text(self):
        """The updater update-all still execs until a successful apply replaces it.

        This is scripts/groundwork-update.py from d65ca1f, the last revision before
        checkout_core was added (#74). CI checks out a single commit, so it is kept
        as a fixture file instead of read from git.
        """
        text = (REPO / 'tests/fixtures/groundwork-update-before-core.py').read_text()
        if 'def checkout_core' in text:
            raise AssertionError('the fixture must be an updater from before checkout_core existed')
        return text

    def test_update_all_from_mains_installed_updater_checks_out_core(self):
        # Protects: an already-set-up machine whose update-all is still main's
        # program. That program fast-forwards and runs apply-layer.sh, and it
        # never calls checkout_core. Distinct value: the test above calls this
        # revision's checkout_core directly, so a missing spec in apply still passes.
        os.environ['GIT_CONFIG_COUNT'] = '1'
        os.environ['GIT_CONFIG_KEY_0'] = 'protocol.file.allow'
        os.environ['GIT_CONFIG_VALUE_0'] = 'always'
        production = (REPO / 'scripts/apply-layer.sh').read_text()
        checkout_at = production.index('submodule update --init -- core')
        incomplete_at = production.index('The checkout is incomplete:')
        register_at = production.index('register_choices() {')
        self.assertLess(checkout_at, incomplete_at)
        self.assertLess(incomplete_at, register_at)
        start = production.index('if [[ ! -f "$root/core/browser/brave.json"')
        end = production.index('\n# Ask once', start)
        script = (
            '#!/bin/bash\nset -euo pipefail\n'
            'root=$(cd "$(dirname "$0")/.." && pwd)\n'
            'cd "$root"\n'
            + production[start:end]
            + 'exit 0\n')
        sub = self.root / 'core.git'
        work = self.root / 'core-src'
        self.command('git', 'init', '--bare', '--initial-branch=main', str(sub))
        self.command('git', 'clone', str(sub), str(work))
        (work / 'README').write_text('main pin, no browser spec\n')
        self.commit(work)
        self.command('git', '-C', str(work), 'push', 'origin', 'main')
        old = self.command('git', '-C', str(work), 'rev-parse', 'HEAD')
        spec = '{"schemaVersion": 1, "marker": "checked-out-by-apply"}\n'
        (work / 'browser').mkdir()
        (work / 'browser/brave.json').write_text(spec)
        self.commit(work)
        self.command('git', '-C', str(work), 'push', 'origin', 'main')
        new = self.command('git', '-C', str(work), 'rev-parse', 'HEAD')
        self.command('git', '-C', str(self.seed), 'submodule', 'add', '--', str(sub), 'core')
        self.command('git', '-C', str(self.seed / 'core'), 'checkout', old)
        self.commit(self.seed)
        self.command('git', '-C', str(self.seed), 'push', 'origin', 'main')
        self.command('git', '-C', str(self.source), 'pull', '--ff-only', 'origin', 'main')
        self.command('git', '-C', str(self.source), 'submodule', 'update', '--init', '--', 'core')
        self.assertFalse((self.source / 'core/browser/brave.json').exists())
        pinned = self.git('rev-parse', 'HEAD')
        installed_path = self.home / '.local/lib/groundwork/omarchy-update.py'
        installed_path.write_text(self.main_updater_text())
        launcher = (self.home / '.local/bin/update-all').read_text()
        self.assertIn('"$HOME/.local/lib/groundwork/omarchy-update.py" all', launcher)
        self.command('git', '-C', str(self.seed / 'core'), 'checkout', new)
        (self.seed / 'scripts/apply-layer.sh').write_text(script)
        for name in ('seed-obsidian.py', 'groundwork-screen-share.py',
                     'groundwork-syncthing.py', 'omarchy-plugins.py',
                     'groundwork-node-pin.py', 'groundwork-commit-guard.py'):
            (self.seed / 'scripts' / name).write_text('# fixture\n')
        self.commit(self.seed)
        self.command('git', '-C', str(self.seed), 'push', 'origin', 'main')
        self.assertEqual(self.git('rev-parse', 'HEAD'), pinned)
        self.assertFalse((self.source / 'core/browser/brave.json').exists())
        spec_loader = importlib.util.spec_from_file_location('installed_main_updater', installed_path)
        installed = importlib.util.module_from_spec(spec_loader)
        spec_loader.loader.exec_module(installed)
        self.assertFalse(hasattr(installed, 'checkout_core'))
        patches = [
            patch.object(installed, 'CONFIG', self.config),
            patch.object(installed, 'STATE', self.state),
            patch.object(installed, 'page_size', return_value=4096),
            patch.object(installed.platform, 'system', return_value='Linux'),
            patch.object(installed.platform, 'machine', return_value='aarch64'),
            patch.object(installed.shutil, 'which', side_effect=lambda name: '/fixture/' + name),
            patch.object(installed, 'run', side_effect=self.run_command),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        installed.preflight(self.source)
        self.execute_apply = True
        self.assertEqual(installed.update('all'), 'completed')
        self.assertNotEqual(self.git('rev-parse', 'HEAD'), pinned)
        self.assertEqual((self.source / 'core/browser/brave.json').read_text(), spec)
        self.assertFalse((self.source / 'browser/brave.json').exists())
        self.assertEqual(installed_path.read_text(), self.main_updater_text())
        installed.preflight(self.source)
        self.assertIn(['bash', str(self.source / 'scripts/apply-layer.sh'), '--update', '--update-plugins'],
                      self.machine_calls)

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

    def test_unfinished_syncthing_is_a_warning_not_a_clean_success(self):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / 'syncthing-pending').write_text('Syncthing is waiting for Tailscale to have an address.\n')
        with patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO) as errors:
            self.assertEqual(updater.main(['layer']), 1)
        receipt = json.loads((self.state / 'groundwork-update-last-success.json').read_text())
        self.assertEqual(receipt['result'], 'completed_with_warnings')
        self.assertEqual(receipt['warnings'], ['Syncthing is waiting for Tailscale to have an address.'])
        self.assertIn('waiting for Tailscale', errors.getvalue())

    def test_syncthing_marker_is_not_followed_through_a_symlink(self):
        self.state.mkdir(parents=True, exist_ok=True)
        target = self.state / 'elsewhere'
        target.write_text('not a marker')
        (self.state / 'syncthing-pending').symlink_to(target)
        # An unreadable marker is reported, not treated as absent.
        self.assertIn('cannot be read', updater.syncthing_pending())

    def test_an_exported_but_empty_config_home_means_unset(self):
        home = self.home / 'empty-config-home'
        config = home / '.config/groundwork'
        config.mkdir(parents=True)
        (config / 'omarchy.json').write_text(json.dumps({
            'schema': 1, 'keyd': True, 'screen_sharing': False,
            'source': str(self.source), 'obsidian_vault': str(home / 'vault'),
        }))
        work = self.root / 'empty-config-cwd'
        work.mkdir()
        env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME='')
        result = subprocess.run(['python3', '-B', str(REPO / 'scripts/groundwork-update.py'),
                                 'preferences', 'update'],
                                env=env, cwd=str(work), capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[0], '1')
        self.assertFalse((work / 'groundwork').exists())

    def test_an_exported_but_empty_state_home_means_unset(self):
        home = self.home / 'empty-state-home'
        home.mkdir()
        env = dict(os.environ, HOME=str(home), XDG_STATE_HOME='')
        result = subprocess.run(['python3', '-B', str(REPO / 'scripts/groundwork-update.py'),
                                 'marker', 'write', 'syncthing-pending', 'waiting'],
                                env=env, cwd=str(home), capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((home / '.local/state/groundwork/syncthing-pending').read_text(), 'waiting\n')
        self.assertFalse((home / 'groundwork').exists())

    def test_the_syncthing_marker_is_written_and_removed_by_the_setup_adapter(self):
        self.assertEqual(updater.main(['marker', 'write', 'syncthing-pending', 'waiting']), 0)
        self.assertEqual(updater.syncthing_pending(), 'waiting')
        self.assertEqual(updater.main(['marker', 'remove', 'syncthing-pending']), 0)
        self.assertIsNone(updater.syncthing_pending())

    def test_flatpak_failure_cannot_mark_full_success(self):
        for failure in ['flatpak']:
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
        self.assertIn('T3 Code updates itself; no page-size check on 4 KiB pages', receipt['completed'])
        self.assertIn('Bitwarden user Flatpak update command completed', receipt['completed'])
        self.assertIn('FreeTube user Flatpak update failed', receipt['completed'])
        self.assertNotIn('FreeTube user Flatpak update command completed', receipt['completed'])
        self.assertEqual(len(receipt['warnings']), 1)
        self.assertIn('FreeTube user Flatpak update failed', receipt['warnings'][0])
        self.assertIn('Warning: FreeTube user Flatpak update failed', errors.getvalue())
        self.assertIn(['flatpak', '--user', 'update', '--noninteractive', 'com.bitwarden.desktop'], self.machine_calls)
        self.assertIn(['flatpak', '--user', 'update', '--noninteractive', 'io.freetubeapp.FreeTube'], self.machine_calls)

    def update_all(self, result='completed'):
        with patch('sys.stdout', new_callable=io.StringIO) as output, patch('sys.stderr', new_callable=io.StringIO) as errors:
            self.assertEqual(updater.update('all'), result)
        receipt = json.loads((self.state / 'update-all-last-success.json').read_text())
        self.assertEqual(receipt['result'], result)
        self.assertIn('Bitwarden user Flatpak update command completed', receipt['completed'])
        self.assertIn('FreeTube user Flatpak update command completed', receipt['completed'])
        return output.getvalue() + errors.getvalue(), receipt

    def test_update_all_checks_t3_with_the_checkout(self):
        app = self.home / '.local/share/t3code/T3-Code.AppImage'
        app.parent.mkdir(parents=True)
        app.write_bytes(b'fixture')
        def gate(source, completed, warnings, pending=True, page=None):
            self.assertEqual(Path(source), self.source)
            warnings.append('fixture T3 warning')
            return False
        with patch.object(updater, 'page_size', return_value=16384), patch.object(updater, 't3_gate', side_effect=gate):
            _, receipt = self.update_all(result='completed_with_warnings')
        self.assertEqual(receipt['warnings'], ['fixture T3 warning'])
        calls = [call for call in self.machine_calls if call[-1:] == ['reconcile']]
        self.assertEqual(len(calls), 1)

    def test_successful_update_all_exits_zero(self):
        with patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(updater.main(['all']), 0)

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


    # The closing reports (issues #18, #107, #103).

    def test_update_all_runs_each_report_after_the_update_and_layer_does_not(self):
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(updater.update('all'), 'completed')
        python = sys.executable
        self.assertEqual([(label, argv) for label, argv, _ in self.report_calls], [
            ('Omarchy feed watch', [python, str(self.source / 'scripts/omarchy-feed-watch.py'), 'report', '--brief']),
            ('PATH shadow check', [python, str(self.source / 'scripts/groundwork-path-shadow.py'), '--brief']),
            ('Disk report', [python, str(self.source / 'scripts/groundwork-disk-report.py'), '--brief']),
        ])
        self.assertTrue(all(0 < deadline <= 60 for _, _, deadline in self.report_calls))
        self.assertIn('Reports: no Omarchy offers, PATH shadows, or low disk space.', output.getvalue())
        self.report_calls.clear()
        updater.update('layer')
        updater.update('all', check=True)
        self.assertEqual(self.report_calls, [])

    def test_quiet_reports_print_one_line_and_findings_print_only_themselves(self):
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            updater.update('all')
        tail = output.getvalue().split('FreeTube user Flatpak update command completed', 1)[1]
        self.assertEqual([line for line in tail.splitlines() if line.strip()],
                         ['Reports: no Omarchy offers, PATH shadows, or low disk space.'])
        self.report_answers['Disk report'] = 'Disk: 9.0 GiB free on /, under the 10.0 GiB Omarchy needs to update.'
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            updater.update('all')
        tail = output.getvalue().split('FreeTube user Flatpak update command completed', 1)[1]
        self.assertEqual([line for line in tail.splitlines() if line.strip()],
                         [self.report_answers['Disk report']])

    def test_skip_variable_runs_no_report(self):
        for value in ('1', 'yes'):
            with patch.dict(os.environ, {updater.SKIP_REPORTS_ENV: value}), \
                    patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(updater.update('all'), 'completed')
            self.assertEqual(self.report_calls, [])
            self.assertNotIn('Reports', output.getvalue())
        with patch.dict(os.environ, {updater.SKIP_REPORTS_ENV: '0'}), patch('sys.stdout', new_callable=io.StringIO):
            updater.update('all')
        self.assertEqual(len(self.report_calls), 3)

    def test_failing_and_hanging_reports_never_fail_update_all(self):
        # Real report processes from the fetched checkout, with short deadlines.
        self.report_patch.stop()
        scripts = self.seed / 'scripts'
        (scripts / 'omarchy-feed-watch.py').write_text('import sys\nprint("table broken", file=sys.stderr)\nsys.exit(1)\n')
        (scripts / 'groundwork-path-shadow.py').write_text(
            'import subprocess, sys, time\n'
            'subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])\n'
            'time.sleep(60)\n')
        (scripts / 'groundwork-disk-report.py').write_text('print("Disk: fixture finding")\n')
        self.commit(self.seed)
        self.command('git', '-C', str(self.seed), 'push', 'origin', 'main')
        reports = tuple((label, script, args, 2) for label, script, args, _ in updater.REPORTS)
        started = time.monotonic()
        with patch.object(updater, 'REPORTS', reports), \
                patch('sys.stdout', new_callable=io.StringIO) as output, \
                patch('sys.stderr', new_callable=io.StringIO) as errors:
            self.assertEqual(updater.main(['all']), 0)
        self.assertLess(time.monotonic() - started, 30)
        receipt = json.loads((self.state / 'update-all-last-success.json').read_text())
        self.assertEqual(receipt['result'], 'completed')
        self.assertNotIn('warnings', receipt)
        self.assertIn('Omarchy feed watch failed (exit 1); skipped. table broken', errors.getvalue())
        self.assertIn('PATH shadow check did not finish within 2 seconds; skipped.', errors.getvalue())
        self.assertNotIn('Update incomplete', errors.getvalue())
        self.assertIn('Disk: fixture finding', output.getvalue())
        self.assertNotIn('Reports: no Omarchy offers', output.getvalue())

    def test_a_report_that_cannot_start_or_is_interrupted_is_only_noted(self):
        with patch('sys.stderr', new_callable=io.StringIO) as errors:
            self.assertIsNone(self.real_run_report('Fixture report', [str(self.root / 'missing')], 5))
        self.assertIn('Fixture report could not start', errors.getvalue())
        self.report_patch.stop()
        with patch.object(updater, 'run_report', side_effect=KeyboardInterrupt), \
                patch('sys.stdout', new_callable=io.StringIO), \
                patch('sys.stderr', new_callable=io.StringIO) as errors:
            self.assertEqual(updater.update('all'), 'completed')
        self.assertIn('Reports cancelled. The update itself had already finished.', errors.getvalue())
        self.assertNotIn('Update incomplete', errors.getvalue())

    def test_a_report_printing_invalid_bytes_is_shown_and_does_not_fail(self):
        script = self.root / 'bytes.py'
        script.write_text('import sys\nsys.stdout.buffer.write(b"Disk: \\xff odd\\n")\n')
        self.report_patch.stop()
        reports = (('Disk report', str(script), (), 10),)
        with patch.object(updater, 'REPORTS', reports), \
                patch.object(updater, 'run_report', side_effect=lambda label, argv, deadline:
                             self.real_run_report(label, [sys.executable, str(script)], deadline)), \
                patch('sys.stdout', new_callable=io.StringIO) as output, \
                patch('sys.stderr', new_callable=io.StringIO) as errors:
            self.assertEqual(updater.main(['all']), 0)
        self.assertIn('Disk: \ufffd odd', output.getvalue())
        self.assertNotIn('Update incomplete', errors.getvalue())
        self.assertEqual(json.loads((self.state / 'update-all-last-success.json').read_text())['result'], 'completed')

    def test_a_report_runner_that_raises_or_a_closed_stdout_is_only_noted(self):
        self.report_patch.stop()
        for failure in (RuntimeError('fixture bug'), BrokenPipeError(32, 'Broken pipe')):
            with self.subTest(failure=type(failure).__name__), \
                    patch.object(updater, 'run_report', side_effect=failure), \
                    patch('sys.stdout', new_callable=io.StringIO), \
                    patch('sys.stderr', new_callable=io.StringIO) as errors:
                self.assertEqual(updater.main(['all']), 0)
            expected = ('Reports stopped: the output was closed.' if isinstance(failure, BrokenPipeError)
                        else f'Reports stopped ({type(failure).__name__}')
            self.assertIn(expected, errors.getvalue())
            self.assertIn('The update itself had already finished.', errors.getvalue())
            self.assertNotIn('Update incomplete', errors.getvalue())
        # stdout already closed when the reports start.
        closed = io.StringIO()
        with patch('sys.stderr', new_callable=io.StringIO) as errors:
            with patch('sys.stdout', closed):
                closed.close()
                updater.run_reports(self.source)
        self.assertIn('Reports stopped (ValueError', errors.getvalue())

    def test_a_reader_that_quits_early_still_leaves_exit_zero(self):
        # update-all | head: the pipe closes before the reports print. The
        # process must not exit 120 from Python's final flush.
        ready = self.root / 'reader-closed'
        child = self.root / 'child.py'
        child.write_text(
            'import importlib.util, os, sys, time\n'
            'from pathlib import Path\n'
            f'spec = importlib.util.spec_from_file_location("u", {str(REPO / "scripts/groundwork-update.py")!r})\n'
            'u = importlib.util.module_from_spec(spec); spec.loader.exec_module(u)\n'
            'u.run_report = lambda label, argv, deadline: "Disk: fixture finding\\n" * 1000\n'
            f'while not os.path.exists({str(ready)!r}): time.sleep(0.05)\n'
            f'u.run_reports(Path({str(self.source)!r}))\n'
            'print("after the reports")\n')
        process = subprocess.Popen([sys.executable, str(child)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process.stdout.close()
        ready.touch()
        _, errors = process.communicate(timeout=30)
        self.assertEqual(process.returncode, 0, errors)
        self.assertIn(b'Reports stopped: the output was closed.', errors)
        self.assertNotIn(b'Exception ignored', errors)

    def test_a_timed_out_report_leaves_no_process_behind(self):
        marker = self.root / 'child.pid'
        script = self.root / 'hang.py'
        script.write_text('import subprocess, sys, time\n'
                          'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])\n'
                          f'open({str(marker)!r}, "w").write(str(child.pid))\n'
                          'time.sleep(60)\n')
        with patch('sys.stderr', new_callable=io.StringIO):
            self.assertIsNone(self.real_run_report('Fixture report', [sys.executable, str(script)], 5))
        pid = int(marker.read_text())
        for _ in range(50):
            try:
                # Gone, or a zombie waiting for its new parent to reap it.
                if Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].startswith('Z'):
                    break
            except (FileNotFoundError, ProcessLookupError):
                break
            time.sleep(0.1)
        else:
            self.fail(f'report child {pid} still running')
    # Issue #119: a source update that changes the updater hands the rest of
    # the run to the installed copy.

    NEW_UPDATER_FIXTURE = """
# Fixture: the updater a source update installs (issue #119). Machine commands
# are logged, not run; git is real.
_fixture_real_run = run
def run(args, **kwargs):
    if args[0] == 'git':
        return _fixture_real_run(args, **kwargs)
    with open(os.environ['FIXTURE_UPDATER_LOG'], 'a') as log:
        log.write(json.dumps(args) + '\\n')
    if args[:3] == ['flatpak', '--user', 'list']:
        return 'com.bitwarden.desktop\\nio.freetubeapp.FreeTube'
    return ''
shutil.which = lambda name: '/fixture/' + name
def update_t3(completed, warnings, source):
    completed.append('New updater ran the app stages')
def run_report(label, argv, deadline):
    with open(os.environ['FIXTURE_UPDATER_LOG'], 'a') as log:
        log.write(json.dumps(['report', label]) + '\\n')
    return ''

"""

    def runtime(self):
        return self.home / '.local/lib/groundwork/omarchy-update.py'

    def release_new_updater(self):
        """Push an updater with the fixture above; apply installs it, as setup's register does."""
        text = (REPO / 'scripts/groundwork-update.py').read_text()
        head, tail = text.rsplit("if __name__ == '__main__':", 1)
        (self.seed / 'scripts/groundwork-update.py').write_text(
            head + self.NEW_UPDATER_FIXTURE + "if __name__ == '__main__':" + tail)
        self.commit(self.seed)
        self.command('git', '-C', str(self.seed), 'push', 'origin', 'main')
        previous = self.run_command

        def apply_installs_the_updater(args, **kwargs):
            result = previous(args, **kwargs)
            if args[0] == 'bash' and str(args[1]).endswith('apply-layer.sh'):
                self.runtime().write_text((self.source / 'scripts/groundwork-update.py').read_text())
            return result
        self.run_patch.stop()
        self.run_patch = patch.object(updater, 'run', side_effect=apply_installs_the_updater)
        self.run_patch.start()
        self.addCleanup(self.run_patch.stop)

    def test_a_new_updater_runs_the_remaining_stages_in_the_same_run(self):
        self.release_new_updater()
        log = self.root / 'new-updater.jsonl'
        handed = []

        class Replaced(BaseException):
            pass

        def fake_exec(argv, env):
            # What execve would run: the installed updater, in this process's
            # place, with the lock's descriptor still open.
            handed.append((argv, env))
            fd = int(env['GROUNDWORK_OMARCHY_LOCK_FD'])
            child = subprocess.run(argv, env=dict(env, FIXTURE_UPDATER_LOG=str(log)), pass_fds=(fd,),
                                   capture_output=True, text=True, timeout=60)
            handed.append(child)
            raise Replaced
        with patch.object(updater, 'reexec', side_effect=fake_exec), \
                patch('sys.stdout', new_callable=io.StringIO) as output, \
                patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(Replaced):
            updater.update('all')
        self.assertEqual(len(handed), 2)
        (argv, env), child = handed
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
        self.assertEqual(argv[:3], [sys.executable, str(self.runtime()), updater.RESUME_COMMAND])
        self.assertEqual(env[updater.REEXEC_ENV], '1')
        handoff = json.loads(argv[3])
        self.assertEqual((handoff['scope'], handoff['source'], handoff['before']),
                         ('all', str(self.source), self.initial))
        self.assertIn('This run installed a new updater; continuing with it.', output.getvalue())
        # The old code stopped after apply; the app stages ran in the new code.
        self.assertFalse(any(call[0] == 'flatpak' for call in self.machine_calls))
        self.assertFalse(any('groundwork-t3-launcher.py' in ' '.join(call) for call in self.machine_calls))
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertIn(['flatpak', '--user', 'update', '--noninteractive', 'io.freetubeapp.FreeTube'], calls)
        self.assertTrue(any(call[-1] == 'reconcile' for call in calls))
        # The closing reports ran in the new code, once each, and not in the old.
        self.assertEqual([call[1] for call in calls if call[0] == 'report'],
                         [label for label, *_ in updater.REPORTS])
        self.assertEqual(self.report_calls, [])
        receipt = json.loads((self.state / 'update-all-last-success.json').read_text())
        self.assertEqual(receipt['result'], 'completed')
        self.assertEqual(receipt['completed'][:3], ['Omarchy update command completed',
                                                    'Groundwork configuration applied',
                                                    'New updater ran the app stages'])
        self.assertEqual(receipt['source_after'], self.git('rev-parse', 'HEAD'))
        self.assertEqual(self.git('for-each-ref', 'refs/groundwork'), '')
        self.assertNotIn('continuing with it', child.stdout)

    def test_an_unchanged_updater_finishes_in_this_process(self):
        self.candidate()
        with patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(updater.update('all'), 'completed')
        self.assertIn(['flatpak', '--user', 'update', '--noninteractive', 'io.freetubeapp.FreeTube'], self.machine_calls)

    def test_a_handed_off_run_never_hands_off_again(self):
        self.runtime().write_text(self.runtime().read_text() + '\n# a different updater\n')
        self.assertIsNotNone(updater.newer_updater())
        with patch.dict(os.environ, {updater.REEXEC_ENV: '1'}):
            self.assertIsNone(updater.newer_updater())
        # The resumed run itself: lock held and inherited, a changed installed
        # updater, and still no second exec.
        candidate = self.git('rev-parse', 'HEAD')
        self.git('update-ref', 'refs/groundwork/update/fixture', candidate)
        handoff = dict(schema=updater.HANDOFF_SCHEMA, scope='all', source=str(self.source), before=self.initial,
                       candidate=candidate, candidate_ref='refs/groundwork/update/fixture',
                       completed=['Omarchy update command completed', 'Groundwork configuration applied'],
                       warnings=[])
        with updater.transaction() as lock, \
                patch.dict(os.environ, {'GROUNDWORK_OMARCHY_LOCK_FD': str(lock.fileno()), updater.REEXEC_ENV: '1'}), \
                patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(updater.main([updater.RESUME_COMMAND, json.dumps(handoff)]), 0)
        receipt = json.loads((self.state / 'update-all-last-success.json').read_text())
        self.assertEqual(receipt['completed'][:2], handoff['completed'])
        self.assertIn('FreeTube user Flatpak update command completed', receipt['completed'])
        self.assertEqual(self.git('for-each-ref', 'refs/groundwork'), '')

    def test_resume_needs_the_lock_and_a_valid_handoff(self):
        with self.assertRaisesRegex(updater.UpdateError, 'transaction lock'):
            updater.main([updater.RESUME_COMMAND, '{}'])
        bad = [{}, dict(schema=2), dict(schema=1, scope='all', source=str(self.source), before='a', candidate='b',
                                         candidate_ref='refs/heads/main', completed=[], warnings=[])]
        with updater.transaction() as lock, patch.dict(os.environ, {'GROUNDWORK_OMARCHY_LOCK_FD': str(lock.fileno())}):
            for value in bad:
                with self.assertRaisesRegex(updater.UpdateError, 'hand-off'):
                    updater.main([updater.RESUME_COMMAND, json.dumps(value)])
            with self.assertRaisesRegex(updater.UpdateError, 'hand-off'):
                updater.main([updater.RESUME_COMMAND, 'not json'])
        self.assertEqual(self.machine_calls, [])

    def test_a_failed_exec_finishes_with_the_previous_updater_and_warns(self):
        self.release_new_updater()
        with patch.object(updater, 'reexec', side_effect=OSError('exec format error')), \
                patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(updater.update('all'), 'completed_with_warnings')
        receipt = json.loads((self.state / 'update-all-last-success.json').read_text())
        self.assertIn('The new updater could not take over (exec format error)', receipt['warnings'][0])
        self.assertIn(['flatpak', '--user', 'update', '--noninteractive', 'io.freetubeapp.FreeTube'], self.machine_calls)
        self.assertEqual(self.git('for-each-ref', 'refs/groundwork'), '')

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

    def test_load_segment_rule(self):
        page = 16384
        mappable = updater.t3_load_segment_mappable
        self.assertTrue(mappable(0x410000, 0x810000, 0x10000, page))
        self.assertTrue(mappable(0x6dc0000, 0x71c0000, 0x1000, page))
        self.assertTrue(mappable(0x4000, 0x14000, 0x4, page))
        self.assertFalse(mappable(0x6dad000, 0x71ac000, 0x1000, page))
        self.assertFalse(mappable(0x6dac000, 0x71ad000, 0x1000, page))
        self.assertFalse(mappable(0x6dad000, 0x71ad000, 0x1000, page))
        self.assertTrue(mappable(0x6dad000, 0x71ad000, 0x1000, 4096))
        self.assertFalse(mappable(0x4000, 0x14000, 0x4000, 65536))

    @unittest.skipUnless(shutil.which('readelf'), 'readelf is not installed')
    def test_synthetic_elf_headers_through_readelf(self):
        cases = {
            'strict': ([(0, 0x400000, 0x10000), (0x410000, 0x810000, 0x10000)], True),
            'relaxed': ([(0, 0x400000, 0x10000), (0x6dc0000, 0x71c0000, 0x1000)], True),
            'offset': ([(0, 0x400000, 0x10000), (0x6dad000, 0x71ac000, 0x1000)], False),
            'vaddr': ([(0, 0x400000, 0x10000), (0x6dac000, 0x71ad000, 0x1000)], False),
            'congruent': ([(0, 0x400000, 0x10000), (0x6dad000, 0x71ad000, 0x1000)], False),
        }
        for name, (segments, passes) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                binary = root / f't3-{CANDIDATE}-linux-arm64' / 't3'
                binary.parent.mkdir()
                binary.write_bytes(synthetic_elf(segments))
                self.assertEqual(updater.load_segments(binary), segments)
                self.assertEqual(updater.load_alignments(binary), [align for _, _, align in segments])
                self.assertEqual(updater.misaligned_elf_files(root, 16384),
                                 [] if passes else [f't3-{CANDIDATE}-linux-arm64/t3'])
                self.assertEqual(updater.misaligned_elf_files(root, 4096), [])

    @unittest.skipUnless(shutil.which('readelf'), 'readelf is not installed')
    def test_relaxed_rule_is_only_for_t3(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'bun'
            binary.write_bytes(synthetic_elf([(0, 0x400000, 0x10000), (0x6dc0000, 0x71c0000, 0x1000)]))
            with patch.object(updater, 'page_size', return_value=16384):
                self.assertIn('is not aligned for 16 KiB pages', updater.bun_alignment_problem(binary))
                self.assertIn('is not aligned for 16 KiB pages', updater.elf_alignment_problem(binary))

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

    def test_a_core_limited_child_reports_rlimit_core_one(self):
        reported = updater.run(
            [sys.executable, '-c',
             'import ctypes, resource\n'
             'dumpable = ctypes.CDLL(None).prctl(3, 0, 0, 0, 0)\n'
             'soft, hard = resource.getrlimit(resource.RLIMIT_CORE)\n'
             'print(f"{dumpable} {soft} {hard}")'],
            limit_core=True)
        # Exec resets dumpable to 1. The limit stays 1, which is what a piped
        # core pattern treats as do-not-dump.
        self.assertEqual(reported, '1 1 1')

    def test_run_replacement_env_drops_the_caller_environment(self):
        reported = updater.run([sys.executable, '-c', 'import os; print("\\n".join(sorted(os.environ)))'],
                               env={'FOO': 'bar', 'PATH': '/usr/bin:/bin'})
        names = set(reported.splitlines())
        # CPython may add LC_CTYPE for its own filesystem encoding. Nothing else from the caller is copied.
        self.assertTrue({'FOO', 'PATH'} <= names)
        self.assertFalse(names - {'FOO', 'PATH', 'LC_CTYPE'})
        self.assertNotIn('HOME', names)
        self.assertNotIn(os.environ.get('HOME', ''), reported)


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


# --- T3 Code desktop app gate (decision 0020) --------------------------------

FIXTURE_MARK = b'\0GROUNDWORK-T3-FIXTURE'
PTY_X64 = 'resources/app.asar.unpacked/node_modules/node-pty/prebuilds/linux-x64/pty.node'
PTY_ARM64 = 'resources/app.asar.unpacked/node_modules/node-pty/prebuilds/linux-arm64/pty.node'


def fake_appimage(version=INSTALLED, **behavior):
    """An aarch64 ELF header followed by what the fake sandbox should do with it.

    behavior: runtime='bad' (its own LOAD fails), extract='crash'|'hang'|'empty',
    load=<relative path of an extracted ELF that fails>, smoke='crash'|'hang'|'wrong',
    desktop=<X-AppImage-Version, default the version, None for none>.
    """
    return synthetic_elf([(0, 0x400000, 0x10000)]) + FIXTURE_MARK + json.dumps(dict(version=version, **behavior)).encode()


def fixture_meta(path):
    return json.loads(Path(path).read_bytes().split(FIXTURE_MARK, 1)[1])


def appimage_sha512(data):
    import base64
    return base64.b64encode(hashlib.sha512(data).digest()).decode()


class T3GateTests(unittest.TestCase):
    """update_t3 against a temp HOME, a fake pending directory, and a fake sandbox."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='groundwork-t3-gate-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.state = self.home / '.local/state/groundwork'
        self.app = self.home / '.local/share/t3code/T3-Code.AppImage'
        self.pending = self.home / '.cache/t3code-updater/pending'
        self.source = self.root / 'checkout'
        self.running = (False, '')
        self.missing = set()
        self.calls = []
        self.readelf = []
        self.fetched = []
        self.releases = {}
        for item in (patch.dict(os.environ, HOME=str(self.home), XDG_CACHE_HOME=str(self.home / '.cache'),
                                XDG_STATE_HOME=str(self.home / '.local/state')),
                     patch.object(updater, 'STATE', self.state),
                     patch.object(updater.shutil, 'which',
                                  side_effect=lambda name: None if name in self.missing else '/fixture/' + name),
                     patch.object(updater, 'page_size', return_value=16384),
                     patch.object(updater, 'run', side_effect=self.fake_run),
                     patch.object(updater, 'fetch', side_effect=self.fake_fetch),
                     patch.object(updater, 't3_running', side_effect=lambda source: self.running)):
            item.start()
            self.addCleanup(item.stop)

    # The fake sandbox, readelf, and release downloads.

    def fake_run(self, args, **kwargs):
        if args[:2] == ['readelf', '-lW']:
            path = Path(args[2])
            self.readelf.append(path)
            meta = fixture_meta(path)
            return {'bad': READELF_BAD, 'relaxed': READELF_RELAXED}.get(meta.get('load'), READELF_GOOD)
        if Path(args[0]).name != 'bwrap':
            raise AssertionError(f'unexpected command {args}')
        self.calls.append((args, kwargs))
        binary, rest = args[args.index('--') + 1], args[args.index('--') + 2:]
        meta = fixture_meta(binary)
        if rest == ['--appimage-extract']:
            if meta.get('extract') == 'crash':
                raise updater.UpdateError(f'{args[0]} failed (exit 139).')
            if meta.get('extract') == 'killed':
                raise updater.UpdateError(f'{args[0]} failed (exit -9).')
            if meta.get('extract') == 'hang':
                raise updater.UpdateError(f'{args[0]} exceeded its 180-second deadline. Retry after repairing that stage.')
            root = Path(kwargs['cwd']) / 'squashfs-root'
            root.mkdir()
            if meta.get('extract') == 'empty':
                return ''
            def elf(relative, machine=183, load=None):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                if load is None:
                    load = 'bad' if meta.get('load') == relative else 'relaxed' if meta.get('relaxed') == relative else None
                path.write_bytes(synthetic_elf([(0, 0x400000, 0x1000 if machine != 183 else 0x10000)], machine)
                                 + FIXTURE_MARK + json.dumps(dict(meta, load=load)).encode())
            elf('t3code')
            elf('chrome-sandbox')
            elf('libffmpeg.so')
            elf(PTY_ARM64)
            # node-pty's x86-64 prebuild: p_align 0x1000. It must never be read.
            elf(PTY_X64, machine=62, load='bad')
            (root / 'resources/app.asar').write_bytes(b'not an ELF file')
            if meta.get('desktop_link'):
                (root / 'elsewhere.desktop').write_text(f'[Desktop Entry]\nX-AppImage-Version={CANDIDATE}\n')
                (root / 't3code.desktop').symlink_to('elsewhere.desktop')
                return ''
            desktop = meta.get('desktop', meta['version'])
            (root / 't3code.desktop').write_text(
                '[Desktop Entry]\nName=T3 Code\nExec=AppRun --no-sandbox %U\n'
                + (f'X-AppImage-Version={desktop}\n' if desktop else ''))
            return '\n'.join(str(path.relative_to(root.parent)) for path in root.rglob('*'))
        if rest == ['--version']:
            smoke = meta.get('smoke')
            if smoke == 'crash':
                raise updater.UpdateError(f'{args[0]} failed (exit 139).')
            if smoke == 'killed':
                raise updater.UpdateError(f'{args[0]} failed (exit 137).')
            if smoke == 'hang':
                raise updater.UpdateError(f'{args[0]} exceeded its 30-second deadline. Retry after repairing that stage.')
            return '0.0.0-nightly.wrong' if smoke == 'wrong' else meta['version']
        raise AssertionError(f'unexpected sandbox arguments {rest}')

    def fake_fetch(self, url, deadline=60, out=None):
        self.fetched.append(url)
        if url == updater.T3_RELEASES:
            return json.dumps([{'tag_name': 'v0.2.0'}] + [{'tag_name': f'v{version}'} for version in self.releases]).encode()
        for version, data in self.releases.items():
            name = f'T3-Code-{version}-arm64.AppImage'
            base = f'{updater.T3_DOWNLOADS}/v{version}/'
            if url == base + updater.T3_FEED:
                return (f'version: {version}\nfiles:\n  - url: {name}\n    sha512: {appimage_sha512(data)}\n'
                        f'    size: {len(data)}\npath: {name}\nsha512: {appimage_sha512(data)}\n'
                        "releaseDate: '2026-10-31T00:00:00.000Z'\n").encode()
            if url == base + name:
                if out is None:
                    return data
                out.write(data)
                return b''
        raise OSError(f'fixture has no {url}')

    # Fixtures.

    def install(self, data):
        self.app.parent.mkdir(parents=True, exist_ok=True)
        self.app.write_bytes(data)
        self.app.chmod(0o755)

    def stage(self, data, version=CANDIDATE, sha512=None):
        self.pending.mkdir(parents=True, exist_ok=True)
        name = f'T3-Code-{version}-arm64.AppImage'
        (self.pending / name).write_bytes(data)
        (self.pending / 'update-info.json').write_text(json.dumps(
            {'fileName': name, 'sha512': sha512 or appimage_sha512(data), 'isAdminRightsRequired': False}))
        return self.pending / name

    def gate(self):
        completed, warnings = [], []
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            updater.update_t3(completed, warnings, self.source)
        return completed, warnings, output.getvalue()

    def rejected(self):
        return json.loads((self.state / 't3-rejected.json').read_text())

    def last_good(self):
        return json.loads((self.state / 't3/last-good.json').read_text())

    def assert_confined(self, call, kwargs):
        self.assertEqual(Path(call[0]).name, 'bwrap')
        for flag in ('--die-with-parent', '--unshare-pid', '--unshare-net', '--ro-bind', '--bind', '--chdir', '--'):
            self.assertIn(flag, call)
        bound = call[call.index('--bind') + 1]
        self.assertIn('groundwork-t3-', bound)
        self.assertEqual(call[call.index('--chdir') + 1], kwargs['cwd'])
        self.assertTrue(kwargs['cwd'].startswith(bound))
        self.assertIs(kwargs['limit_core'], True)
        self.assertIs(kwargs['stdin'], subprocess.DEVNULL)
        env = kwargs['env']
        self.assertEqual(env['PATH'], '/usr/bin:/bin')
        for key, value in env.items():
            if key != 'PATH':
                self.assertTrue(value.startswith(bound + '/'), key)
        self.assertNotEqual(env['HOME'], str(self.home))
        self.assertFalse(Path(bound).exists())

    # Pages and presence.

    def test_small_pages_check_nothing(self):
        self.install(fake_appimage())
        with patch.object(updater, 'page_size', return_value=4096):
            completed, warnings, _ = self.gate()
        self.assertEqual(completed, ['T3 Code updates itself; no page-size check on 4 KiB pages'])
        self.assertEqual((warnings, self.calls, self.fetched), ([], [], []))

    def test_no_app_checks_nothing(self):
        completed, warnings, _ = self.gate()
        self.assertEqual(completed, ['T3 Code app not installed; its check was skipped'])
        self.assertFalse(self.calls)

    def test_missing_bwrap_or_readelf_is_a_warning_and_runs_nothing(self):
        self.install(fake_appimage())
        for tool, text in (('bwrap', updater.T3_NO_BWRAP), ('readelf', 'readelf is missing')):
            with self.subTest(tool=tool):
                self.missing = {tool}
                _, warnings, _ = self.gate()
                self.assertEqual(len(warnings), 1)
                self.assertIn(text, warnings[0])
                self.assertFalse(self.calls)

    # After an update: the installed AppImage.

    def test_installed_build_passes_and_a_copy_is_kept(self):
        data = fake_appimage()
        self.install(data)
        completed, warnings, output = self.gate()
        self.assertEqual(warnings, [])
        self.assertEqual(completed, [f'T3 Code {INSTALLED} checked'])
        self.assertIn('kept a copy for rollback', output)
        self.assertEqual((self.state / 't3/T3-Code.AppImage').read_bytes(), data)
        self.assertEqual(self.last_good(), {'version': INSTALLED, 'sha512': appimage_sha512(data)})
        extract, smoke = self.calls
        self.assertEqual(extract[0][-1], '--appimage-extract')
        self.assertEqual(smoke[0][-2:], [smoke[0][-2], '--version'])
        self.assertEqual(Path(smoke[0][-2]).name, 't3code')
        for call, kwargs in self.calls:
            self.assert_confined(call, kwargs)
        self.assertEqual(smoke[1]['deadline'], updater.T3_CANDIDATE_SMOKE_SECONDS)
        self.assertFalse((self.state / 't3-rejected.json').exists())
        # The same file is not unpacked again.
        self.calls.clear()
        completed, warnings, _ = self.gate()
        self.assertEqual((completed, warnings, self.calls), ([f'T3 Code {INSTALLED} already checked'], [], []))

    def test_pty_node_for_x86_64_is_never_read(self):
        self.install(fake_appimage())
        _, warnings, _ = self.gate()
        self.assertEqual(warnings, [])
        names = sorted(str(path).split('squashfs-root/')[-1] for path in self.readelf if 'squashfs-root' in str(path))
        self.assertEqual(names, sorted(['t3code', 'chrome-sandbox', 'libffmpeg.so', PTY_ARM64]))
        self.assertFalse(any(PTY_X64 in str(path) for path in self.readelf))

    def test_relaxed_load_passes_after_the_smoke_run(self):
        self.install(fake_appimage(relaxed='libffmpeg.so'))
        completed, warnings, _ = self.gate()
        self.assertEqual((completed, warnings), ([f'T3 Code {INSTALLED} checked'], []))

    def test_failing_installed_build_is_rolled_back_to_the_kept_copy(self):
        good = fake_appimage()
        self.install(good)
        self.gate()
        # The app applied a staged build on quit before update-all ran.
        self.install(fake_appimage(CANDIDATE, smoke='crash'))
        completed, warnings, output = self.gate()
        self.assertEqual(warnings, [])
        self.assertIn(f'T3 Code {CANDIDATE} fails on 16 KiB pages: t3code --version crashed (SIGSEGV)', output)
        self.assertEqual(completed, [f'T3 Code restored to {INSTALLED}'])
        self.assertEqual(self.app.read_bytes(), good)
        self.assertEqual(self.app.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.rejected(), {'rule': '0020', '16384': [CANDIDATE],
                                           'reasons': {'16384': {CANDIDATE: 'smoke-crash'}}})
        self.assertFalse(self.fetched)

    def test_failing_installed_build_is_not_replaced_while_t3_runs(self):
        self.install(fake_appimage())
        self.gate()
        bad = fake_appimage(CANDIDATE, load='t3code')
        self.install(bad)
        self.running = (True, 'T3 Code is running (process 42)')
        completed, warnings, _ = self.gate()
        self.assertEqual(completed, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn('T3 Code is running (process 42), so nothing was replaced', warnings[0])
        self.assertIn('rerun update-all', warnings[0])
        self.assertEqual(self.app.read_bytes(), bad)
        self.assertEqual(self.rejected()['reasons']['16384'][CANDIDATE], 'misaligned-load')
        # Once T3 Code quits, the next run restores the copy.
        self.running = (False, '')
        completed, warnings, _ = self.gate()
        self.assertEqual((completed, warnings), ([f'T3 Code restored to {INSTALLED}'], []))

    def test_without_a_copy_a_checked_older_build_is_downloaded(self):
        # The runtime crashes before unpacking, so the installed version is
        # unknown. The newest release is that same build: downloaded, rejected
        # with its version, and the next one is installed.
        crash = fake_appimage(CANDIDATE, extract='crash')
        self.releases = {CANDIDATE: crash, INSTALLED: fake_appimage(INSTALLED)}
        self.install(crash)
        completed, warnings, output = self.gate()
        self.assertEqual(warnings, [])
        self.assertIn('This T3 Code build fails on 16 KiB pages: its AppImage runtime crashed (SIGSEGV)', output)
        self.assertIn(f'T3 Code {CANDIDATE} fails on 16 KiB pages', output)
        self.assertEqual(completed, [f'T3 Code replaced with checked {INSTALLED}'])
        self.assertEqual(self.app.read_bytes(), self.releases[INSTALLED])
        self.assertEqual(self.last_good()['version'], INSTALLED)
        self.assertEqual(self.rejected()['reasons']['16384'], {CANDIDATE: 'extract-crash'})
        self.assertIn(f'{updater.T3_DOWNLOADS}/v{CANDIDATE}/{updater.T3_FEED}', self.fetched)

    def test_fallback_skips_rejected_and_failing_builds_and_installs_the_next(self):
        older = '0.1.5-nightly.20261029.3873'
        self.releases = {CANDIDATE: fake_appimage(CANDIDATE), INSTALLED: fake_appimage(INSTALLED, smoke='crash'),
                         older: fake_appimage(older)}
        self.install(fake_appimage(CANDIDATE, load='libffmpeg.so'))
        completed, warnings, output = self.gate()
        self.assertEqual(warnings, [])
        # CANDIDATE is the installed build that failed, so it is not downloaded.
        self.assertFalse(any(f'v{CANDIDATE}/' in url for url in self.fetched))
        self.assertIn(f'T3 Code {INSTALLED} fails on 16 KiB pages: t3code --version crashed', output)
        self.assertEqual(completed, [f'T3 Code replaced with checked {older}'])
        self.assertEqual(self.app.read_bytes(), self.releases[older])
        self.assertEqual(self.last_good()['version'], older)
        self.assertEqual(sorted(self.rejected()['16384']), sorted([CANDIDATE, INSTALLED]))
        self.assertEqual(self.rejected()['reasons']['16384'][CANDIDATE], 'misaligned-load')

    def test_fallback_download_is_kept_but_not_placed_while_t3_runs(self):
        older = '0.1.5-nightly.20261029.3873'
        self.releases = {older: fake_appimage(older)}
        bad = fake_appimage(CANDIDATE, smoke='crash')
        self.install(bad)
        # Running only from the moment the download passed.
        states = iter([(False, ''), (True, 'T3 Code is running (process 7)')])
        with patch.object(updater, 't3_running', side_effect=lambda source: next(states)):
            completed, warnings, _ = self.gate()
        self.assertEqual(self.app.read_bytes(), bad)
        self.assertEqual(len(warnings), 1)
        self.assertIn(f'T3 Code {older} passed and is kept, but was not put in place', warnings[0])
        self.assertEqual(self.last_good()['version'], older)
        completed, warnings, _ = self.gate()
        self.assertEqual((completed, warnings), ([f'T3 Code restored to {older}'], []))

    def test_fallback_with_a_bad_download_checksum_is_a_warning(self):
        older = '0.1.5-nightly.20261029.3873'
        self.releases = {older: fake_appimage(older)}
        bad = fake_appimage(CANDIDATE, smoke='crash')
        self.install(bad)
        real = self.fake_fetch
        def corrupt(url, deadline=60, out=None):
            if out is not None:
                real(url, deadline, out)
                out.write(b'tampered')
                return b''
            return real(url, deadline)
        with patch.object(updater, 'fetch', side_effect=corrupt):
            completed, warnings, _ = self.gate()
        self.assertEqual(len(warnings), 1)
        self.assertIn('does not match its sha512', warnings[0])
        self.assertEqual(self.app.read_bytes(), bad)

    def test_fallback_without_any_passing_build_says_so(self):
        self.releases = {INSTALLED: fake_appimage(INSTALLED, smoke='crash')}
        self.install(fake_appimage(CANDIDATE, smoke='crash'))
        _, warnings, _ = self.gate()
        self.assertEqual(len(warnings), 1)
        self.assertIn('No T3 Code nightly that passes on 16 KiB pages was found', warnings[0])

    def test_installed_smoke_timeout_or_wrong_version_is_a_warning_and_not_remembered(self):
        for behavior, text in ((dict(smoke='hang'), 'deadline'),
                               (dict(smoke='wrong'), 'printed 0.0.0-nightly.wrong'),
                               (dict(extract='hang'), 'could not be extracted'),
                               (dict(extract='empty'), 'no aarch64 t3code')):
            with self.subTest(**behavior):
                data = fake_appimage(**behavior)
                self.install(data)
                completed, warnings, _ = self.gate()
                self.assertEqual(completed, [])
                self.assertEqual(len(warnings), 1)
                self.assertIn(text, warnings[0])
                self.assertEqual(self.app.read_bytes(), data)
                self.assertFalse((self.state / 't3-rejected.json').exists())
                self.assertFalse((self.state / 't3/last-good.json').exists())

    def test_sigkill_is_a_skip_not_a_rejection(self):
        for behavior in (dict(smoke='killed'), dict(extract='killed')):
            with self.subTest(**behavior):
                self.install(fake_appimage(CANDIDATE, **behavior))
                completed, warnings, _ = self.gate()
                self.assertEqual(completed, [])
                self.assertEqual(len(warnings), 1)
                self.assertIn('SIGKILL', warnings[0])
                self.assertFalse((self.state / 't3-rejected.json').exists())

    def test_the_work_directory_is_on_disk_under_the_cache_and_removed(self):
        self.install(fake_appimage())
        self.gate()
        work = self.home / '.cache/groundwork'
        for call, kwargs in self.calls:
            self.assertTrue(kwargs['cwd'].startswith(str(work) + '/groundwork-t3-'), kwargs['cwd'])
        self.assertEqual(list(work.iterdir()), [])

    def test_stale_work_directories_from_a_killed_check_are_swept(self):
        work = self.home / '.cache/groundwork'
        work.mkdir(parents=True)
        old = time.time() - updater.T3_STALE_WORK_SECONDS - 60
        def make(name, age=old):
            path = work / name
            (path / 'squashfs-root').mkdir(parents=True)
            (path / 'squashfs-root/t3code').write_bytes(b'unpacked')
            os.utime(path, (age, age))
            return path
        stale = make('groundwork-t3-abc123')
        stale_download = make('groundwork-t3-download-def456')
        fresh = make('groundwork-t3-fresh', age=time.time())
        other = make('other-old')
        outside = self.root / 'outside-groundwork-t3-target'
        (outside / 'keep').mkdir(parents=True)
        os.utime(outside, (old, old))
        link = work / 'groundwork-t3-link'
        link.symlink_to(outside)
        os.utime(link, (old, old), follow_symlinks=False)
        plain = work / 'groundwork-t3-file'
        plain.write_text('not a directory')
        os.utime(plain, (old, old))
        self.install(fake_appimage())
        completed, warnings, output = self.gate()
        self.assertEqual((completed, warnings), ([f'T3 Code {INSTALLED} checked'], []))
        self.assertFalse(stale.exists())
        self.assertFalse(stale_download.exists())
        self.assertIn(f'Removed the stale T3 Code work directory {stale}.', output)
        self.assertTrue((fresh / 'squashfs-root/t3code').exists())
        self.assertTrue((other / 'squashfs-root/t3code').exists())
        self.assertTrue(link.is_symlink())
        self.assertTrue((outside / 'keep').is_dir())
        self.assertEqual(plain.read_text(), 'not a directory')
        self.assertEqual(sorted(path.name for path in work.iterdir()),
                         ['groundwork-t3-file', 'groundwork-t3-fresh', 'groundwork-t3-link', 'other-old'])

    def test_a_read_only_folder_does_not_block_a_stale_sweep(self):
        work = self.home / '.cache/groundwork'
        stale = work / 'groundwork-t3-readonly'
        locked = stale / 'squashfs-root/resources'
        (locked / 'inner').mkdir(parents=True)
        (locked / 'inner/app.asar').write_bytes(b'unpacked')
        (locked / 'file').write_bytes(b'unpacked')
        (locked / 'inner').chmod(0o500)
        locked.chmod(0o500)
        self.addCleanup(lambda: [path.chmod(0o700) for path in (locked, locked / 'inner') if path.exists()])
        outside = self.root / 'outside'
        (outside / 'kept').mkdir(parents=True)
        outside.chmod(0o500)
        self.addCleanup(outside.chmod, 0o700)
        (locked.parent / 'link').symlink_to(outside)
        old = time.time() - updater.T3_STALE_WORK_SECONDS - 60
        os.utime(stale, (old, old))
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), patch('sys.stderr', errors):
            self.assertEqual(updater.t3_work_dir(), str(work))
        self.assertFalse(stale.exists())
        self.assertIn(f'Removed the stale T3 Code work directory {stale}.', output.getvalue())
        self.assertEqual(errors.getvalue(), '')
        # The symbolic link was removed, not followed: its target keeps its mode.
        self.assertTrue((outside / 'kept').is_dir())
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o500)

    def test_remove_tree_raises_what_a_retry_cannot_fix(self):
        missing = self.root / 'missing'
        with self.assertRaises(FileNotFoundError):
            updater.remove_tree(missing)
        tree = self.root / 'tree'
        (tree / 'sub').mkdir(parents=True)
        (tree / 'sub/file').write_text('x')
        real_unlink = os.unlink
        def denied(path, *args, **kwargs):
            if str(path).endswith('file'):
                raise PermissionError(13, 'denied', str(path))
            return real_unlink(path, *args, **kwargs)
        # shutil and the updater share the os module, so this covers both.
        with patch.object(updater.os, 'unlink', side_effect=denied), self.assertRaises(PermissionError):
            updater.remove_tree(tree)
        self.assertTrue((tree / 'sub/file').exists())

    def test_the_sweep_skips_the_callers_own_work_directory(self):
        work = self.home / '.cache/groundwork'
        old = time.time() - updater.T3_STALE_WORK_SECONDS - 60
        own = work / 'groundwork-t3-download-own'
        own.mkdir(parents=True)
        (own / 'T3-Code.AppImage').write_bytes(b'downloading')
        other = work / 'groundwork-t3-download-other'
        other.mkdir()
        for path in (own, other):
            os.utime(path, (old, old))
        with redirect_stdout(io.StringIO()):
            updater.t3_work_dir(keep=own / 'T3-Code.AppImage')
        self.assertEqual((own / 'T3-Code.AppImage').read_bytes(), b'downloading')
        self.assertFalse(other.exists())

    def test_a_download_that_looks_stale_after_suspend_is_still_checked_and_installed(self):
        # The download's own directory ages past the stale limit while it is
        # still in use, as wall-clock time does across a long suspend.
        crash = fake_appimage(CANDIDATE, extract='crash')
        self.releases = {INSTALLED: fake_appimage(INSTALLED)}
        self.install(crash)
        real_fetch = self.fake_fetch
        old = time.time() - updater.T3_STALE_WORK_SECONDS - 60
        def suspended(url, deadline=60, out=None):
            result = real_fetch(url, deadline=deadline, out=out)
            if out is not None:
                os.utime(Path(out.name).parent, (old, old))
            return result
        with patch.object(updater, 'fetch', side_effect=suspended):
            completed, warnings, output = self.gate()
        self.assertEqual(warnings, [])
        self.assertEqual(completed, [f'T3 Code replaced with checked {INSTALLED}'])
        self.assertNotIn('Removed the stale T3 Code work directory', output)
        self.assertEqual(self.app.read_bytes(), self.releases[INSTALLED])
        self.assertEqual(list((self.home / '.cache/groundwork').iterdir()), [])

    def test_a_symlinked_work_directory_is_refused(self):
        target = self.root / 'elsewhere'
        target.mkdir()
        (target / 'groundwork-t3-old').mkdir()
        old = time.time() - updater.T3_STALE_WORK_SECONDS - 60
        os.utime(target / 'groundwork-t3-old', (old, old))
        (self.home / '.cache').mkdir(parents=True)
        (self.home / '.cache/groundwork').symlink_to(target)
        self.install(fake_appimage())
        self.stage(fake_appimage(CANDIDATE, smoke='crash'))
        completed, warnings, _ = self.gate()
        self.assertEqual(completed, [])
        self.assertEqual(len(warnings), 2)
        for warning in warnings:
            self.assertIn('is a symbolic link', warning)
        self.assertFalse(self.calls)
        self.assertTrue((target / 'groundwork-t3-old').is_dir())
        self.assertEqual([path.name for path in target.iterdir()], ['groundwork-t3-old'])
        # Nothing was checked, so nothing was rejected or removed from pending.
        self.assertFalse((self.state / 't3-rejected.json').exists())
        self.assertTrue((self.pending / 'update-info.json').exists())

    def test_only_the_extraction_drops_ipc_and_uts(self):
        self.install(fake_appimage())
        self.gate()
        extract, smoke = (call for call, _ in self.calls)
        for flag in ('--unshare-ipc', '--unshare-uts'):
            self.assertIn(flag, extract[:extract.index('--')])
            self.assertNotIn(flag, smoke)

    def test_a_symlinked_desktop_entry_is_not_read(self):
        self.install(fake_appimage(INSTALLED, desktop_link=True))
        _, warnings, _ = self.gate()
        # No t3code was unpacked either, so this is a skip, and the linked version is never used.
        self.assertIsNone(updater.t3_desktop_version(self.root))
        root = self.root / 'tree'
        root.mkdir()
        (root / 'other.desktop').write_text(f'X-AppImage-Version={CANDIDATE}\n')
        (root / 't3code.desktop').symlink_to('other.desktop')
        self.assertIsNone(updater.t3_desktop_version(root))
        (root / 't3code.desktop').unlink()
        (root / 't3code.desktop').write_text(f'X-AppImage-Version={CANDIDATE}\n')
        self.assertEqual(updater.t3_desktop_version(root), CANDIDATE)

    def test_setup_does_not_download_an_older_build(self):
        self.releases = {INSTALLED: fake_appimage(INSTALLED)}
        self.install(fake_appimage(CANDIDATE, smoke='crash'))
        completed, warnings = [], []
        with patch('sys.stdout', new_callable=io.StringIO):
            passed = updater.t3_gate(self.source, completed, warnings, pending=False, page=16384, download=False)
        self.assertFalse(passed)
        self.assertEqual(self.fetched, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn('Do not open T3 Code yet. Run update-all', warnings[0])
        # A kept copy is still restored during setup.
        self.install(fake_appimage())
        updater.t3_gate(self.source, [], [], pending=False, page=16384, download=False)
        self.install(fake_appimage(CANDIDATE, smoke='crash'))
        completed = []
        with patch('sys.stdout', new_callable=io.StringIO):
            self.assertTrue(updater.t3_gate(self.source, completed, [], pending=False, page=16384, download=False))
        self.assertEqual(completed, [f'T3 Code restored to {INSTALLED}'])
        self.assertEqual(self.fetched, [])

    def test_bad_runtime_load_is_rejected_without_unpacking(self):
        self.install(fake_appimage(CANDIDATE, load='bad'))
        self.running = (True, 'T3 Code is running (process 1)')
        _, warnings, output = self.gate()
        self.assertIn('its AppImage runtime cannot be mapped', output)
        self.assertFalse(self.calls)
        self.assertEqual(len(warnings), 1)

    # Before an update: the staged build in ~/.cache/t3code-updater/pending.

    def test_staged_build_that_passes_stays_staged(self):
        self.install(fake_appimage())
        staged = self.stage(fake_appimage(CANDIDATE))
        completed, warnings, output = self.gate()
        self.assertEqual(warnings, [])
        self.assertIn(f'T3 Code staged {CANDIDATE} checked', completed)
        self.assertIn('T3 Code applies it when it quits', output)
        self.assertTrue(staged.is_file())
        self.assertTrue((self.pending / 'update-info.json').is_file())

    def test_rejected_staged_build_is_removed_and_remembered(self):
        cases = (
            (dict(load='t3code'), 'misaligned-load', 'ELF file(s) cannot be mapped (t3code)'),
            (dict(load=PTY_ARM64), 'misaligned-load', PTY_ARM64),
            (dict(load='bad'), 'misaligned-load', 'its AppImage runtime cannot be mapped'),
            (dict(extract='crash'), 'extract-crash', 'its AppImage runtime crashed (SIGSEGV)'),
            (dict(smoke='crash'), 'smoke-crash', 't3code --version crashed (SIGSEGV)'),
        )
        self.install(fake_appimage())
        self.gate()
        for behavior, reason, text in cases:
            with self.subTest(reason=reason, **behavior):
                (self.state / 't3-rejected.json').unlink(missing_ok=True)
                staged = self.stage(fake_appimage(CANDIDATE, **behavior))
                # The app's other files in pending are not touched.
                other = self.pending / 'temp-T3-Code-next.AppImage'
                other.write_bytes(b'partial download')
                completed, warnings, output = self.gate()
                self.assertEqual(warnings, [])
                self.assertIn(f'T3 Code staged {CANDIDATE} rejected and removed', completed)
                self.assertIn(text, output)
                self.assertFalse(staged.exists())
                self.assertFalse((self.pending / 'update-info.json').exists())
                self.assertEqual(other.read_bytes(), b'partial download')
                self.assertEqual(self.rejected(), {'rule': '0020', '16384': [CANDIDATE],
                                                   'reasons': {'16384': {CANDIDATE: reason}}})

    def test_a_rejected_version_staged_again_is_removed_without_a_check(self):
        self.install(fake_appimage())
        self.gate()
        self.stage(fake_appimage(CANDIDATE, smoke='crash'))
        self.gate()
        self.calls.clear()
        staged = self.stage(fake_appimage(CANDIDATE, smoke='crash'))
        completed, warnings, output = self.gate()
        self.assertEqual(warnings, [])
        self.assertIn(f'T3 Code staged {CANDIDATE} removed (rejected)', completed)
        self.assertIn('still rejected', output)
        self.assertFalse(staged.exists())
        self.assertFalse(self.calls)
        # A newer build clears the hold.
        newer = '0.1.7-nightly.20261102.4100'
        staged = self.stage(fake_appimage(newer), version=newer)
        completed, _, _ = self.gate()
        self.assertIn(f'T3 Code staged {newer} checked', completed)
        self.assertTrue(staged.exists())

    def test_staged_timeout_is_a_warning_and_stays_staged(self):
        self.install(fake_appimage())
        staged = self.stage(fake_appimage(CANDIDATE, smoke='hang'))
        _, warnings, _ = self.gate()
        self.assertEqual(len(warnings), 1)
        self.assertIn(f'T3 Code staged {CANDIDATE} was not checked', warnings[0])
        self.assertIn('deadline', warnings[0])
        self.assertTrue(staged.exists())
        self.assertFalse((self.state / 't3-rejected.json').exists())

    def test_staged_file_that_does_not_match_its_sha512_is_removed_but_not_remembered(self):
        self.install(fake_appimage())
        staged = self.stage(fake_appimage(CANDIDATE), sha512=appimage_sha512(b'something else'))
        _, warnings, _ = self.gate()
        self.assertEqual(len(warnings), 1)
        self.assertIn('did not match its sha512 and was removed', warnings[0])
        self.assertFalse(staged.exists())
        self.assertFalse((self.state / 't3-rejected.json').exists())

    def test_staged_record_that_names_no_appimage_or_a_symlink_is_left_alone(self):
        self.install(fake_appimage())
        self.pending.mkdir(parents=True)
        info = self.pending / 'update-info.json'
        for record in ({'fileName': '../../evil.AppImage', 'sha512': 'x'}, {'fileName': 'T3-Code-1.AppImage'}, []):
            with self.subTest(record=record):
                info.write_text(json.dumps(record))
                _, warnings, _ = self.gate()
                self.assertEqual(len(warnings), 1)
                self.assertIn('does not name a T3 Code AppImage', warnings[0])
                self.assertTrue(info.exists())
        target = self.root / 'elsewhere.AppImage'
        target.write_bytes(fake_appimage(CANDIDATE, smoke='crash'))
        name = f'T3-Code-{CANDIDATE}-arm64.AppImage'
        (self.pending / name).symlink_to(target)
        info.write_text(json.dumps({'fileName': name, 'sha512': appimage_sha512(target.read_bytes())}))
        _, warnings, _ = self.gate()
        self.assertIn('is a symbolic link', warnings[0])
        self.assertTrue(target.exists())

    def test_nothing_staged_or_still_downloading_is_quiet(self):
        self.install(fake_appimage())
        self.gate()
        self.pending.mkdir(parents=True)
        (self.pending / 'update-info.json').write_text(json.dumps(
            {'fileName': f'T3-Code-{CANDIDATE}-arm64.AppImage', 'sha512': 'x'}))
        completed, warnings, _ = self.gate()
        self.assertEqual((completed, warnings), ([f'T3 Code {INSTALLED} already checked'], []))

    # t3-rejected.json from before decision 0020.

    def test_bun_cli_rejections_are_dropped_once(self):
        self.install(fake_appimage())
        self.state.mkdir(parents=True)
        path = self.state / 't3-rejected.json'
        for previous in ({'16384': [CANDIDATE]},
                         {'rule': '0018', '16384': [CANDIDATE], 'reasons': {'16384': {CANDIDATE: 'smoke-crash'}}}):
            with self.subTest(rule=previous.get('rule')):
                path.write_text(json.dumps(previous))
                (self.state / 't3/last-good.json').unlink(missing_ok=True)
                staged = self.stage(fake_appimage(CANDIDATE))
                _, warnings, output = self.gate()
                self.assertIn(f'checking again 1 build(s) rejected before decision 0020 ({CANDIDATE})', output)
                self.assertEqual(warnings, [])
                self.assertTrue(staged.exists())
                self.assertEqual(json.loads(path.read_text()), {'rule': '0020'})
                _, _, output = self.gate()
                self.assertNotIn('checking again', output)

    def test_file_names_and_versions(self):
        version = updater.t3_appimage_version
        self.assertEqual(version(f'T3-Code-{CANDIDATE}-arm64.AppImage'), CANDIDATE)
        self.assertEqual(version('T3-Code-0.1.5.AppImage'), '0.1.5')
        self.assertEqual(version('T3-Code-0.1.5-arm64.AppImage'), '0.1.5')
        for name in ('T3-Code-0.1.5-x86_64.AppImage', '../T3-Code-0.1.5.AppImage', 'T3-Code-.AppImage',
                     'T3-Code-0.1.5-arm64.AppImage.zsync', None, 'T3-Code-0.1.5/x.AppImage'):
            self.assertIsNone(version(name), name)

    def test_nightly_versions_follow_omarchys_selection(self):
        self.releases = {CANDIDATE: b'', INSTALLED: b''}
        with patch.object(updater, 'fetch', return_value=json.dumps([
                {'tag_name': 'v0.2.0'}, {'tag_name': 'v0.1.3-preview.1.2'}, {'tag_name': f'v{CANDIDATE}', 'draft': True},
                {'tag_name': f'v{INSTALLED}'}, {'tag_name': 'v0.1.5-nightly.20261029.3873'}]).encode()):
            self.assertEqual(updater.t3_nightly_versions(), [INSTALLED, '0.1.5-nightly.20261029.3873'])

    def test_setup_adapter_checks_only_the_installed_build(self):
        with patch.object(updater, 'verify_lock') as lock, \
                patch.object(updater, 't3_gate', return_value=True) as gate:
            self.assertEqual(updater.main(['t3-check-installed', '16384']), 0)
        lock.assert_called_once_with()
        _, kwargs = gate.call_args
        self.assertEqual((kwargs['pending'], kwargs['page'], kwargs['download']), (False, 16384, False))
        with patch.object(updater, 'verify_lock'), patch.object(updater, 't3_gate', return_value=False), \
                patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(updater.main(['t3-check-installed', '0']), 1)
        with patch.object(updater, 't3_gate', side_effect=AssertionError('unexpected')), \
                patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(updater.main(['t3-check-installed']), 64)
            self.assertEqual(updater.main(['t3-check-installed', 'x']), 64)


# The fake AppImage for the real sandbox: a shell script that unpacks a t3code
# script with --appimage-extract. ELF checks are patched for these, so only
# bubblewrap, the scrubbed environment, and the core limit are real.
SHELL_APPIMAGE = """#!/bin/sh
case "$1" in
  --appimage-extract)
    {extract}
    mkdir -p squashfs-root
    cat > squashfs-root/t3code <<'GROUNDWORK_T3CODE'
{t3code}
GROUNDWORK_T3CODE
    chmod 755 squashfs-root/t3code
    printf '[Desktop Entry]\\nX-AppImage-Version={version}\\n' > squashfs-root/t3code.desktop
    ;;
  *) exit 2 ;;
esac
"""


@unittest.skipUnless(shutil.which('bwrap'), 'bubblewrap is not installed')
class T3SandboxTests(unittest.TestCase):
    def check(self, t3code, extract='', deadline=None):
        paths = []
        real_temporary = tempfile.TemporaryDirectory
        class Recording(real_temporary):
            def __enter__(self):
                path = super().__enter__()
                if 'groundwork-t3-' in path:
                    paths.append(path)
                return path
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / 'home'
            (home / '.cache').mkdir(parents=True)
            (home / 'sentinel').write_text('home contents must not be visible\n')
            self.sentinel = home / 'sentinel'
            appimage = Path(directory) / 'T3-Code.AppImage'
            t3code = t3code.replace('__SENTINEL__', str(self.sentinel))
            appimage.write_text(SHELL_APPIMAGE.format(extract=extract, t3code=t3code.rstrip('\n'), version=CANDIDATE))
            with patch.object(updater, 'STATE', Path(directory) / 'state'), \
                    patch.dict(os.environ, HOME=str(home), XDG_CACHE_HOME=str(home / '.cache')), \
                    patch.object(updater, 'elf_machine', return_value=updater.EM_AARCH64), \
                    patch.object(updater, 'load_segments', return_value=[(0, 0x400000, 0x10000)]), \
                    patch.object(updater.tempfile, 'TemporaryDirectory', Recording), \
                    patch.object(updater, 'T3_CANDIDATE_SMOKE_SECONDS', deadline or updater.T3_CANDIDATE_SMOKE_SECONDS), \
                    patch.object(updater, 'INTERRUPT_GRACE_SECONDS', 0.2):
                result = updater.t3_check_appimage(appimage, 16384, version=CANDIDATE)
        self.assertTrue(paths)
        self.assertFalse(Path(paths[-1]).exists())
        return result

    def test_a_crash_is_rejected_and_a_hang_is_retried(self):
        result = self.check(CRASH_WITHOUT_DUMP)
        self.assertEqual((result.status, result.reason), ('reject', 'smoke-crash'))
        self.assertIn('crashed (SIGSEGV)', result.detail)
        # The rejection above still passes if the fixture goes back to
        # `kill -SEGV`. This is the check that does not: the same script prints
        # PR_GET_DUMPABLE 0 and its raised core limit, and the SIGSEGV itself
        # leaves no core file. Removing the prctl fails both.
        ceiling = core_ceiling()
        printer = CRASH_WITHOUT_DUMP.replace(
            'os.kill(os.getpid(), signal.SIGSEGV)\n',
            'import resource\n'
            'dumpable = ctypes.CDLL(None).prctl(3, 0, 0, 0, 0)\n'
            'soft, hard = resource.getrlimit(resource.RLIMIT_CORE)\n'
            'print(f"{dumpable} {soft} {hard}")\n')
        status, stdout, cores = run_dump_child(printer, ceiling)
        self.assertEqual(status, 0, stdout)
        self.assertEqual(stdout.strip(), f'0 {ceiling} {ceiling}')
        self.assertEqual(cores, [])
        status, _stdout, cores = run_dump_child(CRASH_WITHOUT_DUMP, ceiling)
        self.assertEqual(status, -signal.SIGSEGV)
        self.assertEqual(cores, [])
        limited = (
            '#!/usr/bin/python3\n'
            'import ctypes, resource\n'
            'dumpable = ctypes.CDLL(None).prctl(3, 0, 0, 0, 0)\n'
            'soft, hard = resource.getrlimit(resource.RLIMIT_CORE)\n'
            f'print("{CANDIDATE}" if dumpable == 1 and soft == 1 and hard == 1 else "0.0.0-unlimited")\n'
        )
        result = self.check(limited)
        self.assertEqual((result.status, result.version), ('pass', CANDIDATE))
        result = self.check('#!/bin/sh\nsleep 30\n', deadline=0.2)
        self.assertEqual(result.status, 'skip')
        self.assertIn('deadline', result.detail)

    def test_an_extraction_crash_is_rejected(self):
        result = self.check('#!/bin/sh\nexit 0\n', extract='kill -SEGV $$')
        self.assertEqual((result.status, result.reason), ('reject', 'extract-crash'))

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
case "$(pwd)" in */groundwork-t3-*/squashfs-root) ;; *) ok=0 ;; esac
[ "$PATH" = /usr/bin:/bin ] || ok=0
[ -z "$USER" ] || ok=0
[ ! -e "$SENTINEL" ] || ok=0
if [ "$ok" -eq 1 ]; then echo "{CANDIDATE}"; else echo "0.0.0-unscrubbed"; fi
# A real double-fork that ignores the shell exiting. The pid namespace must reap it.
(
  setsid bash -c 'exec -a {token} sleep 30' </dev/null >/dev/null 2>&1 &
) &
exit 0
'''
        self.addCleanup(subprocess.run, ['pkill', '-f', token], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # The work directory is under the (fixture) home's cache, so only its
        # parents exist in the sandbox; a file beside them must not.
        result = self.check(script.replace('$SENTINEL', '__SENTINEL__'))
        self.assertEqual((result.status, result.version), ('pass', CANDIDATE), result.detail)
        time.sleep(0.3)
        found = subprocess.run(['pgrep', '-a', '-f', token], capture_output=True, text=True)
        self.assertNotEqual(found.returncode, 0, found.stdout)


# A real aarch64 AppImage stand-in: on --appimage-extract it copies itself to
# squashfs-root/t3code with a desktop entry and an x86-64 pty.node (p_align
# 0x1000). As t3code, --version prints VERSION, or crashes with -DCRASH.
FAKE_APPIMAGE_C = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <sys/stat.h>
static void copy(const char *from, const char *to) {
  FILE *a = fopen(from, "rb"), *b = fopen(to, "wb"); char buf[65536]; size_t n;
  if (!a || !b) exit(3);
  while ((n = fread(buf, 1, sizeof buf, a)) > 0) fwrite(buf, 1, n, b);
  fclose(a); fclose(b); chmod(to, 0755);
}
int main(int argc, char **argv) {
  if (argc > 1 && !strcmp(argv[1], "--appimage-extract")) {
    mkdir("squashfs-root", 0755);
    mkdir("squashfs-root/prebuilds", 0755);
    copy("/proc/self/exe", "squashfs-root/t3code");
    FILE *d = fopen("squashfs-root/t3code.desktop", "w");
    fprintf(d, "[Desktop Entry]\nX-AppImage-Version=%s\n", VERSION); fclose(d);
    unsigned char h[120] = {0x7f, 'E', 'L', 'F', 2, 1, 1};
    h[16] = 3; h[18] = 62; h[20] = 1; h[32] = 64; h[52] = 64; h[54] = 56; h[56] = 1;
    h[64] = 1; h[68] = 5; h[113] = 0x10;
    FILE *x = fopen("squashfs-root/prebuilds/pty.node", "wb"); fwrite(h, 1, sizeof h, x); fclose(x);
    return 0;
  }
  if (argc > 1 && !strcmp(argv[1], "--version")) {
#ifdef CRASH
    raise(SIGSEGV);
#endif
    printf("%s\n", VERSION);
    return 0;
  }
  return 1;
}
'''


@unittest.skipUnless(updater.platform.machine() == 'aarch64' and shutil.which('gcc') and shutil.which('bwrap')
                     and shutil.which('readelf'), 'needs an aarch64 host with gcc, bwrap, and readelf')
class T3RealAppImageTests(unittest.TestCase):
    """The whole check, unpatched: readelf, the sandbox, and the e_machine filter."""

    def build(self, directory, version, crash=False):
        source = Path(directory) / 'fake.c'
        source.write_text(FAKE_APPIMAGE_C)
        output = Path(directory) / f'{version}{"-crash" if crash else ""}.AppImage'
        subprocess.run(['gcc', '-O1', '-static', f'-DVERSION="{version}"', *(['-DCRASH'] if crash else []),
                        '-o', str(output), str(source)], check=True, capture_output=True)
        return output

    def test_a_real_build_passes_and_a_crashing_one_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(updater, 'STATE', Path(directory) / 'state'):
            good = self.build(directory, INSTALLED)
            result = updater.t3_check_appimage(good, 16384, keep=Path(directory) / 'kept.AppImage')
            self.assertEqual((result.status, result.version), ('pass', INSTALLED), result.detail)
            self.assertEqual((Path(directory) / 'kept.AppImage').read_bytes(), good.read_bytes())
            crash = self.build(directory, CANDIDATE, crash=True)
            result = updater.t3_check_appimage(crash, 16384, version=CANDIDATE)
            self.assertEqual((result.status, result.reason), ('reject', 'smoke-crash'))


if __name__ == '__main__':
    unittest.main()
