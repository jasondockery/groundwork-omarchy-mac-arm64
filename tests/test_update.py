"""Real local Git transport; isolated home and mocked machine-owner commands."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('groundwork_updater', REPO / 'scripts/groundwork-update.py')
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


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
                        patch.object(updater.shutil, 'which', side_effect=lambda name: '/fixture/' + name)]
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
        if args[0] == self.failure:
            raise updater.UpdateError('fixture stage failed')
        if args[:2] == ['t3', '--version']:
            return 't3 v0.0.1-nightly.fixture'
        if args[:3] == ['flatpak', '--user', 'list']:
            return 'com.bitwarden.desktop\norg.example.Unowned'
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

    def test_all_delegates_system_and_selected_app_updates_to_their_owners(self):
        updater.update('all')
        self.assertEqual(self.machine_calls[0], ['omarchy', 'update'])
        self.assertIn(['t3', 'update', '--channel', 'nightly', '--yes'], self.machine_calls)
        self.assertIn(['flatpak', '--user', 'update', '--noninteractive', 'com.bitwarden.desktop'], self.machine_calls)
        self.assertNotIn('org.example.Unowned', str(self.machine_calls))

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

    def test_concurrent_update_is_rejected(self):
        updater.private_parent(self.state / 'update.lock')
        with (self.state / 'update.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(updater.UpdateError, 'Another Groundwork'):
                updater.update('layer')

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


class ProcessTests(unittest.TestCase):
    def test_deadline_terminates_owned_child(self):
        with self.assertRaisesRegex(updater.UpdateError, 'deadline'):
            updater.run([sys.executable, '-c', 'import time; time.sleep(30)'], deadline=0.1)


if __name__ == '__main__':
    unittest.main()
