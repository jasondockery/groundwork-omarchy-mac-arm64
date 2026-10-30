"""Commit identity guard install (decision 0017), against fixture HOMEs only."""
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / 'scripts/groundwork-commit-guard.py'
GUARD = REPO / 'core/commit-guard/commit-identity-guard.sh'
APPLY_LAYER = REPO / 'scripts/apply-layer.sh'


class CommitGuardTests(unittest.TestCase):
    def setUp(self):
        if not GUARD.exists():
            if os.environ.get('CI'):
                self.fail('core submodule is not checked out')
            self.skipTest('core submodule is not checked out')
        self.temp = tempfile.TemporaryDirectory(prefix='groundwork-commit-guard-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'
        self.home.mkdir()
        self.gw = self.home / '.config/groundwork'
        self.hooks = self.gw / 'git-hooks'
        self.settings = self.home / '.claude/settings.json'
        self.env = {'PATH': os.environ['PATH'], 'HOME': str(self.home), 'GIT_CONFIG_NOSYSTEM': '1'}

    def helper(self, *args, **extra):
        env = dict(self.env)
        env.update(extra)
        return subprocess.run(['python3', '-B', str(HELPER), *args], env=env, text=True,
                              capture_output=True, timeout=30)

    def apply(self, **extra):
        return self.helper('apply', '--guard', str(GUARD), **extra)

    def git(self, *args, cwd=None, **extra):
        env = dict(self.env, GIT_AUTHOR_NAME='T', GIT_AUTHOR_EMAIL='t@example.com',
                   GIT_COMMITTER_NAME='T', GIT_COMMITTER_EMAIL='t@example.com')
        env.update(extra)
        return subprocess.run(['git', *args], cwd=cwd or self.home, env=env, text=True,
                              capture_output=True, timeout=30)

    def snapshot(self):
        state = {}
        for path in sorted(self.home.rglob('*')):
            if path.is_symlink():
                state[str(path)] = ('link', os.readlink(path))
            elif path.is_file():
                state[str(path)] = ('file', path.read_bytes(), path.stat().st_mtime_ns)
        return state

    def make_repo(self):
        repo = self.home / 'repo'
        repo.mkdir()
        self.git('init', '-q', '-b', 'main', cwd=repo)
        (repo / 'f').write_text('x')
        self.git('add', 'f', cwd=repo)
        return repo

    def test_install(self):
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(os.readlink(self.hooks / 'commit-identity-guard.sh'), str(GUARD))
        for name in ('commit-msg', 'pre-push', 'pre-commit'):
            self.assertEqual(os.readlink(self.hooks / name), 'dispatch')
        self.assertTrue(os.access(self.hooks / 'dispatch', os.X_OK))
        self.assertEqual(self.git('config', '--global', 'core.hooksPath').stdout.strip(), str(self.hooks))
        self.assertFalse((self.home / '.gitconfig').exists())
        self.assertEqual((self.gw / 'commit-denylist').read_text(), '')
        self.assertEqual(json.loads(self.settings.read_text()), {'attribution': {'commit': '', 'pr': ''}})
        self.assertTrue(os.access(self.home / '.local/bin/groundwork-commit-guard', os.X_OK))

    def test_hooks_block_coauthor_and_keep_repo_hook(self):
        self.apply()
        repo = self.make_repo()
        marker = self.home / 'repo-hook-ran'
        hook = repo / '.git/hooks/pre-commit'
        hook.write_text(f'#!/bin/sh\ntouch {marker}\n')
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
        bad = self.git('commit', '-q', '-m', 'm\n\nCo-authored-by: A <a@example.com>', cwd=repo)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn('groundwork-commit-guard off', bad.stderr)
        self.assertTrue(marker.exists())
        self.assertEqual(self.git('commit', '-q', '-m', 'clean', cwd=repo).returncode, 0)

    def test_pre_push_blocks_and_forwards_stdin(self):
        self.apply()
        remote = self.home / 'remote.git'
        self.git('init', '-q', '--bare', str(remote))
        repo = self.make_repo()
        self.git('remote', 'add', 'origin', str(remote), cwd=repo)
        self.git('commit', '-q', '-m', 'ok', cwd=repo)
        seen = self.home / 'stdin-seen'
        hook = repo / '.git/hooks/pre-push'
        hook.write_text(f'#!/bin/sh\ncat > {seen}\n')
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
        self.assertEqual(self.git('push', '-q', 'origin', 'main', cwd=repo).returncode, 0)
        self.assertIn('refs/heads/main', seen.read_text())
        (repo / 'g').write_text('y')
        self.git('add', 'g', cwd=repo)
        self.git('commit', '-q', '--no-verify', '-m', 'x\n\nco-authored-by: A <a@example.com>', cwd=repo)
        self.assertNotEqual(self.git('push', '-q', 'origin', 'main', cwd=repo).returncode, 0)

    def test_denylist_blocks_identity(self):
        self.apply()
        (self.gw / 'commit-denylist').write_text('blocked-fixture-name\n')
        repo = self.make_repo()
        result = self.git('commit', '-q', '-m', 'm', cwd=repo, GIT_AUTHOR_NAME='Blocked-Fixture-Name')
        self.assertNotEqual(result.returncode, 0)

    def test_existing_hookspath_is_preserved(self):
        (self.home / '.gitconfig').write_text('[core]\n\thooksPath = /somewhere/else\n')
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('already set to /somewhere/else', result.stdout)
        self.assertEqual(self.git('config', '--global', 'core.hooksPath').stdout.strip(), '/somewhere/else')
        self.assertFalse((self.home / '.config/git/config').exists())
        self.assertEqual((self.home / '.gitconfig').read_text(), '[core]\n\thooksPath = /somewhere/else\n')

    def test_gitconfig_hookspath_overrides_ours(self):
        self.apply()
        (self.home / '.gitconfig').write_text('[core]\n\thooksPath = /mine\n')
        self.assertEqual(self.git('config', '--global', 'core.hooksPath').stdout.strip(), '/mine')

    def test_existing_attribution_is_preserved(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(json.dumps(
            {'model': 'x', 'attribution': {'commit': 'Mine', 'other': 1}, 'hooks': {'a': [1]}}))
        self.apply()
        merged = json.loads(self.settings.read_text())
        self.assertEqual(merged['attribution'], {'commit': 'Mine', 'other': 1, 'pr': ''})
        self.assertEqual((merged['model'], merged['hooks']), ('x', {'a': [1]}))

    def test_fully_set_attribution_is_not_rewritten(self):
        self.settings.parent.mkdir(parents=True)
        text = json.dumps({'attribution': {'commit': 'A', 'pr': 'B'}})
        self.settings.write_text(text)
        self.apply()
        self.assertEqual(self.settings.read_text(), text)
        self.assertEqual(list(self.settings.parent.glob('settings.json.bak.*')), [])

    def test_json_merge_keeps_keys_mode_and_numbered_backups(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text('{"env": {"A": "1"}, "permissions": {"allow": ["x"]}}')
        self.settings.chmod(0o600)
        self.apply()
        merged = json.loads(self.settings.read_text())
        self.assertEqual((merged['env'], merged['permissions']), ({'A': '1'}, {'allow': ['x']}))
        self.assertEqual(stat.S_IMODE(self.settings.stat().st_mode), 0o600)
        self.assertEqual(json.loads((self.settings.parent / 'settings.json.bak.1').read_text())['env'], {'A': '1'})
        self.settings.write_text('{"attribution": {}, "k": 1}')
        self.apply()
        self.assertTrue((self.settings.parent / 'settings.json.bak.2').exists())
        self.assertTrue((self.settings.parent / 'settings.json.bak.1').exists())

    def test_invalid_settings_json_is_left_untouched(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text('{not json')
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.settings.read_text(), '{not json')
        self.assertIn('not a JSON object', result.stdout)

    def test_symlinked_settings_keep_the_link(self):
        real = self.home / 'dotfiles/settings.json'
        real.parent.mkdir(parents=True)
        real.write_text('{"k": 1}')
        self.settings.parent.mkdir(parents=True)
        self.settings.symlink_to(real)
        self.apply()
        self.assertTrue(self.settings.is_symlink())
        self.assertEqual(json.loads(real.read_text())['k'], 1)
        self.assertEqual(json.loads(real.read_text())['attribution'], {'commit': '', 'pr': ''})

    def test_existing_denylist_is_not_touched(self):
        self.gw.mkdir(parents=True)
        (self.gw / 'commit-denylist').write_text('keep-me\n')
        self.apply()
        self.assertEqual((self.gw / 'commit-denylist').read_text(), 'keep-me\n')

    def test_rerun_changes_nothing(self):
        self.apply()
        before = self.snapshot()
        again = self.apply()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(again.stdout, '')
        self.assertEqual(self.snapshot(), before)

    def test_xdg_config_home_is_honored(self):
        xdg = Path(self.temp.name) / 'xdg'
        result = self.apply(XDG_CONFIG_HOME=str(xdg))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((xdg / 'groundwork/commit-denylist').exists())
        self.assertTrue((xdg / 'groundwork/git-hooks/pre-push').is_symlink())
        self.assertFalse(self.gw.exists())

    def test_opt_out_installs_nothing_and_on_restores(self):
        self.assertEqual(self.helper('off').returncode, 0)
        self.assertTrue((self.gw / 'commit-guard-off').exists())
        result = self.apply()
        self.assertIn('opted out', result.stdout)
        self.assertFalse(self.hooks.exists())
        self.assertFalse(self.settings.exists())
        self.assertFalse((self.gw / 'commit-denylist').exists())
        self.assertEqual(self.helper('on').returncode, 0)
        self.assertFalse((self.gw / 'commit-guard-off').exists())
        self.apply()
        self.assertTrue(self.hooks.exists())

    def test_opt_out_flag_and_env_disable_installed_hooks(self):
        self.apply()
        repo = self.make_repo()
        message = 'm\n\nCo-authored-by: A <a@example.com>'
        by_env = self.git('commit', '-q', '-m', message, cwd=repo, GROUNDWORK_COMMIT_GUARD='off')
        self.assertEqual(by_env.returncode, 0, by_env.stderr)
        (repo / 'g').write_text('y')
        self.git('add', 'g', cwd=repo)
        self.helper('off')
        by_flag = self.git('commit', '-q', '-m', message, cwd=repo)
        self.assertEqual(by_flag.returncode, 0, by_flag.stderr)

    def test_foreign_command_is_not_replaced(self):
        target = self.home / '.local/bin/groundwork-commit-guard'
        target.parent.mkdir(parents=True)
        target.write_text('#!/bin/sh\necho mine\n')
        self.apply()
        self.assertEqual(target.read_text(), '#!/bin/sh\necho mine\n')

    def test_doctor_reports_guard_output(self):
        self.apply()
        result = self.helper('doctor')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('core.hooksPath points at the Groundwork hooks', result.stdout)
        self.assertIn('Claude Code attribution commit and pr are empty', result.stdout)

    def test_doctor_reports_opt_out_and_missing_install(self):
        self.helper('off')
        result = self.helper('doctor')
        self.assertIn('opted out', result.stdout)
        self.assertIn('not installed', result.stdout)

    def test_installer_writes_no_names_or_denylist_values(self):
        self.apply()
        self.assertEqual((self.gw / 'commit-denylist').stat().st_size, 0)
        written = sorted(str(p.relative_to(self.home)) for p in self.home.rglob('*')
                         if p.is_file() and not p.is_symlink())
        self.assertEqual(written, [
            '.claude/settings.json', '.config/git/config', '.config/groundwork/commit-denylist',
            '.config/groundwork/git-hooks/dispatch', '.local/bin/groundwork-commit-guard'])
        for rel in ('.claude/settings.json', '.config/git/config'):
            self.assertNotIn('@', (self.home / rel).read_text(), rel)

    def test_apply_layer_requires_and_runs_the_helper(self):
        text = APPLY_LAYER.read_text()
        self.assertIn('groundwork-commit-guard.py" apply --guard "$root/core/commit-guard/commit-identity-guard.sh"', text)
        self.assertIn('"$root/scripts/groundwork-commit-guard.py"', text.split('Fail before installing')[1].split('}')[0])


if __name__ == '__main__':
    unittest.main()
