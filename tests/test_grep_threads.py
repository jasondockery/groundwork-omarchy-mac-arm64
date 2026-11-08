"""grep.threads cap (decision 0001), against fixture homes only.

Protects: the Groundwork-owned include renders exactly 4, a later run of that
exact text does not rewrite it, a file that merely parses as 4 is replaced,
and every copy of a stale grep-threads.gitconfig include path is removed.
Distinct value: no other test writes or parses this include.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / 'scripts/groundwork-grep-threads.py'
APPLY_LAYER = REPO / 'scripts/apply-layer.sh'
INCLUDE_TEXT = (
    '# Agents run many git grep searches. Upstream starts one worker per core.\n'
    '[grep]\n'
    '\tthreads = 4\n'
)


class GrepThreadsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='groundwork-grep-threads-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'
        self.home.mkdir()
        self.neutral = Path(self.temp.name) / 'neutral'
        self.neutral.mkdir()
        self.env = {'PATH': os.environ['PATH'], 'HOME': str(self.home), 'GIT_CONFIG_NOSYSTEM': '1'}

    def apply(self, **extra):
        env = dict(self.env)
        env.update(extra)
        return subprocess.run(['python3', '-B', str(HELPER), 'apply'], env=env, text=True,
                              capture_output=True, timeout=30)

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd or self.neutral, env=self.env, text=True,
                              capture_output=True, timeout=30)

    def include(self):
        return self.home / '.config/groundwork/grep-threads.gitconfig'

    def gitconfig(self):
        return self.home / '.config/git/config'

    def file_stamp(self, path):
        stat = path.stat()
        return (path.read_bytes(), stat.st_mtime_ns, stat.st_ino)

    def test_rendered_value_is_exactly_4(self):
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        include = self.include()
        rendered = self.git('config', '--file', str(include), '--get-all', 'grep.threads')
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        self.assertEqual(rendered.stdout, '4\n')
        names = self.git('config', '--file', str(include), '--name-only', '--list')
        self.assertEqual(names.stdout, 'grep.threads\n')
        self.assertIn('Agents run many git grep searches. Upstream starts one worker per core.',
                      include.read_text())
        effective = self.git('config', '--get-all', 'grep.threads')
        self.assertEqual(effective.returncode, 0, effective.stderr)
        self.assertEqual(effective.stdout, '4\n')
        included = self.git('config', '--file', str(self.gitconfig()), '--get-all', 'include.path')
        self.assertEqual(included.stdout, str(include) + '\n')

    def test_second_run_does_not_rewrite_when_value_is_4(self):
        first = self.apply()
        self.assertEqual(first.returncode, 0, first.stderr)
        include_before = self.file_stamp(self.include())
        config_before = self.file_stamp(self.gitconfig())
        second = self.apply()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.file_stamp(self.include()), include_before)
        self.assertEqual(self.file_stamp(self.gitconfig()), config_before)
        self.assertEqual(second.stdout, '')

    def test_threads_4_beside_another_key_is_rewritten(self):
        include = self.include()
        include.parent.mkdir(parents=True)
        include.write_text('[grep]\n\tthreads = 4\n[pull]\n\trebase = true\n')
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(include.read_text(), INCLUDE_TEXT)
        names = self.git('config', '--file', str(include), '--name-only', '--list')
        self.assertEqual(names.stdout, 'grep.threads\n', names.stderr)
        rendered = self.git('config', '--file', str(include), '--get-all', 'grep.threads')
        self.assertEqual(rendered.stdout, '4\n')
        self.assertNotIn('rebase', include.read_text())

    def test_threads_4_without_the_reason_comment_is_rewritten(self):
        include = self.include()
        include.parent.mkdir(parents=True)
        include.write_text('[grep]\n\tthreads = 4\n')
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(include.read_text(), INCLUDE_TEXT)

    def test_different_value_is_rewritten_to_exactly_4(self):
        include = self.include()
        include.parent.mkdir(parents=True)
        include.write_text('[grep]\n\tthreads = 8\n')
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = self.git('config', '--file', str(include), '--get-all', 'grep.threads')
        self.assertEqual(rendered.stdout, '4\n', rendered.stderr)
        self.assertIn('Upstream starts one worker per core.', include.read_text())
        names = self.git('config', '--file', str(include), '--name-only', '--list')
        self.assertEqual(names.stdout, 'grep.threads\n')

    def test_several_values_are_replaced_with_one_4(self):
        include = self.include()
        include.parent.mkdir(parents=True)
        include.write_text('[grep]\n\tthreads = 4\n\tthreads = 8\n')
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = self.git('config', '--file', str(include), '--get-all', 'grep.threads')
        self.assertEqual(rendered.stdout, '4\n', rendered.stderr)

    def test_other_include_path_is_kept(self):
        other = self.home / 'other.gitconfig'
        other.write_text('[diff]\n\trenames = true\n')
        config = self.gitconfig()
        config.parent.mkdir(parents=True)
        config.write_text(f'[include]\n\tpath = {other}\n')
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        included = self.git('config', '--file', str(config), '--get-all', 'include.path')
        self.assertEqual(included.stdout.splitlines(), [str(other), str(self.include())])
        self.assertIn(f'path = {other}', config.read_text())
        effective = self.git('config', '--get-all', 'grep.threads')
        self.assertEqual(effective.stdout, '4\n')

    def test_later_stale_grep_include_is_removed(self):
        first = self.apply()
        self.assertEqual(first.returncode, 0, first.stderr)
        stale = self.home / 'stale' / 'grep-threads.gitconfig'
        stale.parent.mkdir(parents=True)
        stale.write_text('[grep]\n\tthreads = 8\n')
        other = self.home / 'other.gitconfig'
        other.write_text('[diff]\n\trenames = true\n')
        config = self.gitconfig()
        with config.open('a', encoding='utf-8') as output:
            output.write(f'[include]\n\tpath = {stale}\n\tpath = {other}\n')
        before = self.git('config', '--get', 'grep.threads')
        self.assertEqual(before.stdout, '8\n', before.stderr)
        include_before = self.file_stamp(self.include())
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.file_stamp(self.include()), include_before)
        effective = self.git('config', '--get-all', 'grep.threads')
        self.assertEqual(effective.stdout, '4\n', effective.stderr)
        included = self.git('config', '--file', str(config), '--get-all', 'include.path')
        self.assertEqual(included.stdout.splitlines(), [str(self.include()), str(other)])
        self.assertNotIn(str(stale), config.read_text())

    def test_only_stale_grep_include_is_replaced(self):
        stale = self.home / 'old' / 'grep-threads.gitconfig'
        stale.parent.mkdir(parents=True)
        stale.write_text('[grep]\n\tthreads = 8\n')
        config = self.gitconfig()
        config.parent.mkdir(parents=True)
        config.write_text(f'[include]\n\tpath = {stale}\n')
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        effective = self.git('config', '--get-all', 'grep.threads')
        self.assertEqual(effective.stdout, '4\n', effective.stderr)
        included = self.git('config', '--file', str(config), '--get-all', 'include.path')
        self.assertEqual(included.stdout.splitlines(), [str(self.include())])

    def test_repeated_stale_grep_include_is_removed(self):
        first = self.apply()
        self.assertEqual(first.returncode, 0, first.stderr)
        stale = self.home / 'stale' / 'grep-threads.gitconfig'
        stale.parent.mkdir(parents=True)
        stale.write_text('[grep]\n\tthreads = 8\n')
        other = self.home / 'other.gitconfig'
        other.write_text('[diff]\n\trenames = true\n')
        config = self.gitconfig()
        with config.open('a', encoding='utf-8') as output:
            output.write(
                f'[include]\n\tpath = {stale}\n\tpath = {stale}\n\tpath = {other}\n')
        before = self.git('config', '--get', 'grep.threads')
        self.assertEqual(before.stdout, '8\n', before.stderr)
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(str(stale), config.read_text())
        effective = self.git('config', '--get-all', 'grep.threads')
        self.assertEqual(effective.stdout, '4\n', effective.stderr)
        included = self.git('config', '--file', str(config), '--get-all', 'include.path')
        self.assertEqual(included.stdout.splitlines(), [str(self.include()), str(other)])
        again = self.apply()
        self.assertEqual(again.returncode, 0, again.stderr)
        effective = self.git('config', '--get-all', 'grep.threads')
        self.assertEqual(effective.stdout, '4\n', effective.stderr)

    def test_xdg_config_home_is_honored(self):
        xdg = Path(self.temp.name) / 'xdg'
        result = self.apply(XDG_CONFIG_HOME=str(xdg))
        self.assertEqual(result.returncode, 0, result.stderr)
        include = xdg / 'groundwork/grep-threads.gitconfig'
        rendered = self.git('config', '--file', str(include), '--get-all', 'grep.threads')
        self.assertEqual(rendered.stdout, '4\n')
        self.assertFalse(self.include().exists())


class WiringTests(unittest.TestCase):
    def test_apply_layer_runs_the_helper(self):
        text = APPLY_LAYER.read_text()
        self.assertIn('"$root/scripts/groundwork-grep-threads.py" apply', text)
        section = text.split('Fail before installing')[1].split('}')[0]
        self.assertIn('"$root/scripts/groundwork-grep-threads.py"', section)


if __name__ == '__main__':
    unittest.main()
