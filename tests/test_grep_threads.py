"""grep.threads cap (decision 0001), against fixture homes only.

Protects: the Groundwork-owned include renders exactly 4, and a later run that
already finds 4 does not rewrite that file.
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

    def test_existing_4_is_not_rewritten(self):
        include = self.include()
        include.parent.mkdir(parents=True)
        original = '# kept\n[grep]\n\tthreads = 4\n'
        include.write_text(original)
        before = self.file_stamp(include)
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.file_stamp(include), before)
        self.assertEqual(include.read_text(), original)
        included = self.git('config', '--file', str(self.gitconfig()), '--get-all', 'include.path')
        self.assertEqual(included.stdout, str(include) + '\n')
        effective = self.git('config', '--get-all', 'grep.threads')
        self.assertEqual(effective.stdout, '4\n')

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
