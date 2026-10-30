"""Opt-in git-filter-repo: per-user, proven by its help text, and never a rewrite."""
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / 'scripts/install-git-filter-repo.sh'
spec = importlib.util.spec_from_file_location('groundwork_gfr_updater', REPO / 'scripts/groundwork-update.py')
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)

# A fake git answers only `git filter-repo -h`, from a state file. A fake mise
# "installs" by writing that state file. Every call is logged.
FAKE_GIT = """#!/usr/bin/env bash
echo "git $*" >> "$FAKE_LOG"
if [[ "$1" == "filter-repo" && "$2" == "-h" ]]; then
  [[ -f "$FAKE_STATE" ]] && cat "$FAKE_STATE" && exit 0
  echo "git: 'filter-repo' is not a git command." >&2
  exit 1
fi
exit 0
"""
FAKE_MISE = """#!/usr/bin/env bash
echo "mise $*" >> "$FAKE_LOG"
[[ "$1 $2 $3" == "use -g pipx:git-filter-repo" ]] && printf '%s\\n' "$FAKE_HELP" > "$FAKE_STATE"
exit 0
"""
FAKE_UV = "#!/usr/bin/env bash\nexit 0\n"


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='groundwork-gfr-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        (self.root / 'home').mkdir()
        for name in ('bash', 'grep', 'timeout', 'env', 'dirname', 'cat'):
            found = shutil.which(name)
            self.assertTrue(found, name)
            (self.bin / name).symlink_to(found)
        self.log = self.root / 'log'
        self.state = self.root / 'state'
        self.fake('git', FAKE_GIT)

    def fake(self, name, body):
        path = self.bin / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def run_helper(self, mise=True, uv=True, help_text='usage: git filter-repo --sensitive-data-removal'):
        if mise:
            self.fake('mise', FAKE_MISE)
        if uv:
            self.fake('uv', FAKE_UV)
        env = {'HOME': str(self.root / 'home'), 'PATH': str(self.bin), 'FAKE_LOG': str(self.log),
               'FAKE_STATE': str(self.state), 'FAKE_HELP': help_text}
        return subprocess.run(['bash', str(HELPER)], env=env, text=True, capture_output=True, timeout=30)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_installs_per_user_with_mise_and_checks_the_help_text(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls().count('mise use -g pipx:git-filter-repo'), 1)
        self.assertIn('git filter-repo -h', self.calls())
        self.assertIn('installed for this user', result.stdout)

    def test_second_run_is_idempotent(self):
        self.run_helper()
        self.log.unlink()
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse([call for call in self.calls() if call.startswith('mise')])

    def test_old_build_without_the_flag_fails_the_proof_without_failing_setup(self):
        result = self.run_helper(help_text='usage: git filter-repo (older build)')
        self.assertEqual(result.returncode, 0)
        self.assertIn('does not list --sensitive-data-removal', result.stderr)
        self.assertNotIn('installed for this user', result.stdout)

    def test_missing_mise_or_installer_backend_installs_nothing(self):
        for kwargs, message in (({'mise': False}, 'mise is unavailable'), ({'uv': False}, 'Neither uv nor pipx')):
            with self.subTest(kwargs=kwargs):
                for entry in self.bin.glob('m*ise'):
                    entry.unlink()
                (self.bin / 'uv').unlink(missing_ok=True)
                self.state.unlink(missing_ok=True)
                self.log.unlink(missing_ok=True)
                result = self.run_helper(**kwargs)
                self.assertEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse(self.state.exists())
                self.assertFalse([call for call in self.calls() if call.startswith('mise use')])

    def test_helper_has_no_root_or_rewrite_commands(self):
        text = HELPER.read_text()
        for forbidden in ('sudo', 'pacman', 'omarchy pkg', 'filter-branch', 'git push', '--force', '--replace-text',
                          '--sensitive-data-removal --'):
            self.assertNotIn(forbidden, text)
        self.run_helper()
        for call in self.calls():
            self.assertIn(call, ('git filter-repo -h', 'mise use -g pipx:git-filter-repo'))


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='groundwork-gfr-pref-')
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / 'omarchy.json'
        self.original = updater.CONFIG
        updater.CONFIG = self.config
        self.addCleanup(setattr, updater, 'CONFIG', self.original)

    def write(self, **extra):
        value = dict(schema=1, keyd=True, screen_sharing=True, source=self.temp.name, obsidian_vault='', **extra)
        self.config.write_text(json.dumps(value))

    def test_missing_choice_stays_off(self):
        self.write()
        self.assertFalse(updater.git_filter_repo_enabled(updater.settings()))
        self.assertFalse(updater.git_filter_repo_enabled(None))

    def test_saved_yes_is_read_and_non_boolean_is_rejected(self):
        self.write(git_filter_repo=True)
        self.assertTrue(updater.git_filter_repo_enabled(updater.settings()))
        self.write(git_filter_repo='yes')
        with self.assertRaises(updater.UpdateError):
            updater.settings()

    def test_help_documents_the_opt_in_and_the_decline(self):
        result = subprocess.run(['bash', str(REPO / 'install.sh'), '--help'], capture_output=True, text=True, timeout=20)
        self.assertIn('--with-git-filter-repo', result.stdout)
        self.assertIn('--without-git-filter-repo', result.stdout)
        self.assertIn('docs/history-purge.md', (REPO / 'docs/git-filter-repo.md').read_text())


if __name__ == '__main__':
    unittest.main()
