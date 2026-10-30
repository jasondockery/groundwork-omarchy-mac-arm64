"""Node pin in the user's mise config, against fixture HOMEs only (decision 0016)."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'scripts/groundwork-node-pin.py'
spec = importlib.util.spec_from_file_location('groundwork_node_pin', SCRIPT)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

OMARCHY_DEFAULT_CONFIG = '[settings.upgrade]\n'


class FixtureHome(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        patcher = patch.dict(os.environ, {'HOME': str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ('XDG_CONFIG_HOME', 'MISE_CONFIG_DIR', 'MISE_GLOBAL_CONFIG_FILE'):
            os.environ.pop(name, None)
        self.mise_dir = self.home / '.config/mise'
        self.config = self.mise_dir / 'config.toml'

    def run_step(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = helper.main(['groundwork-node-pin.py', *args])
        return status, out.getvalue(), err.getvalue()

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def backups(self, directory=None):
        directory = directory or self.mise_dir
        return sorted(p.name for p in directory.glob('*.~*~'))

    def assertKept(self, path, text):
        status, out, err = self.run_step('apply')
        self.assertEqual((status, err), (0, ''), out)
        self.assertIn('already sets node', out)
        self.assertEqual(path.read_text(), text)
        self.assertEqual(self.backups(), [])


class NodePinTests(FixtureHome):
    def test_creates_config_when_absent_and_reports_the_pin(self):
        status, out, _ = self.run_step('apply')
        self.assertEqual(status, 3)
        self.assertIn('not installed by this step', out)
        self.assertEqual(tomllib.loads(self.config.read_text()), {'tools': {'node': '26'}})
        self.assertEqual(self.backups(), [])
        created = sorted(p.relative_to(self.home).as_posix() for p in self.home.rglob('*') if p.is_file())
        self.assertEqual(created, ['.config/mise/config.toml'])

    def test_version_prints_the_pin_for_the_installer(self):
        status, out, _ = self.run_step('version')
        self.assertEqual((status, out), (0, helper.NODE_PIN + '\n'))

    def test_adds_node_under_existing_tools_and_keeps_everything_else(self):
        original = ('# my settings\n[settings]\nexperimental = true\n\n'
                    '[tools]\n# keep\nbun = "latest"  # comment\npython = "3.13"\n\n[env]\nFOO = "bar"\n')
        self.write(self.config, original)
        status, _, _ = self.run_step('apply')
        self.assertEqual(status, 3)
        updated = self.config.read_text()
        expected = tomllib.loads(original)
        expected['tools']['node'] = '26'
        self.assertEqual(tomllib.loads(updated), expected)
        self.assertIn('bun = "latest"  # comment\n', updated)
        self.assertIn('# my settings\n', updated)
        self.assertEqual(self.backups(), ['config.toml.~1~'])
        self.assertEqual((self.mise_dir / 'config.toml.~1~').read_text(), original)

    def test_appends_tools_table_when_missing_even_without_trailing_newline(self):
        self.write(self.config, '[settings.upgrade]\nx = 1')
        self.assertEqual(self.run_step('apply')[0], 3)
        self.assertEqual(tomllib.loads(self.config.read_text()),
                         {'settings': {'upgrade': {'x': 1}}, 'tools': {'node': '26'}})

    def test_existing_node_entries_are_respected(self):
        for text in ('[tools]\nnode = "24"\n', '[tools]\nnode = "latest"\n', '[tools]\nnode = "lts"\n',
                     '[tools]\nnode = { version = "22", lazy = true }\n', '[tools]\nnode = "26"\n'):
            with self.subTest(text=text):
                self.write(self.config, text)
                self.assertKept(self.config, text)

    def test_other_user_config_files_that_set_node_are_respected(self):
        self.write(self.config, OMARCHY_DEFAULT_CONFIG)
        for relative in ('config.local.toml', 'config.work.toml', 'conf.d/90-mine.toml'):
            with self.subTest(file=relative):
                other = self.mise_dir / relative
                self.write(other, '[tools]\nnode = "lts"\n')
                self.assertKept(self.config, OMARCHY_DEFAULT_CONFIG)
                other.unlink()

    def test_tool_versions_that_sets_node_is_respected(self):
        self.write(self.config, OMARCHY_DEFAULT_CONFIG)
        tool_versions = self.home / '.tool-versions'
        self.write(tool_versions, '# versions\nbun 1.2.0\nnode 24.1.0  # mine\n')
        self.assertKept(self.config, OMARCHY_DEFAULT_CONFIG)
        # Only a real node line counts, not a comment or another tool.
        self.write(tool_versions, '# node 24\nnodejs 22\npython 3.13\n')
        self.assertEqual(self.run_step('apply')[0], 3)

    def test_home_level_files_that_override_global_config_are_respected(self):
        self.write(self.config, OMARCHY_DEFAULT_CONFIG)
        for relative in ('.mise.toml', 'mise.toml', '.mise.local.toml', 'mise.local.toml', '.config/mise.toml'):
            with self.subTest(file=relative):
                other = self.home / relative
                self.write(other, '[tools]\nbun = "latest"\nnode = "24"\n')
                self.assertKept(self.config, OMARCHY_DEFAULT_CONFIG)
                other.unlink()

    def test_home_level_toml_without_node_does_not_block_the_pin(self):
        self.write(self.home / '.mise.toml', '[tools]\nbun = "latest"\n')
        self.assertEqual(self.run_step('apply')[0], 3)
        self.assertEqual(self.home.joinpath('.mise.toml').read_text(), '[tools]\nbun = "latest"\n')

    def test_invalid_home_level_toml_is_left_alone(self):
        self.write(self.home / '.mise.toml', '[tools\n')
        status, _, err = self.run_step('apply')
        self.assertEqual(status, 1)
        self.assertIn('could not be read', err)
        self.assertFalse(self.config.exists())

    def test_node_version_file_in_home_is_respected(self):
        self.write(self.config, OMARCHY_DEFAULT_CONFIG)
        self.write(self.home / '.node-version', 'v24.1.0\n')
        self.assertKept(self.config, OMARCHY_DEFAULT_CONFIG)
        # An empty or comment-only file sets nothing.
        self.write(self.home / '.node-version', '# none\n\n')
        self.assertEqual(self.run_step('apply')[0], 3)

    def test_mise_config_dir_is_honored(self):
        custom = self.root / 'custom-mise'
        with patch.dict(os.environ, {'MISE_CONFIG_DIR': str(custom)}):
            self.assertEqual(self.run_step('apply')[0], 3)
        self.assertEqual(tomllib.loads((custom / 'config.toml').read_text()), {'tools': {'node': '26'}})
        self.assertFalse(self.mise_dir.exists())

    def test_xdg_config_home_is_honored(self):
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.root / 'xdg')}):
            self.assertEqual(self.run_step('apply')[0], 3)
        self.assertTrue((self.root / 'xdg/mise/config.toml').is_file())
        self.assertFalse(self.mise_dir.exists())

    def test_mise_global_config_file_is_the_target(self):
        target = self.root / 'elsewhere/global.toml'
        self.write(target, '[tools]\nbun = "latest"\n')
        with patch.dict(os.environ, {'MISE_GLOBAL_CONFIG_FILE': str(target)}):
            self.assertEqual(self.run_step('apply')[0], 3)
        self.assertEqual(tomllib.loads(target.read_text()), {'tools': {'bun': 'latest', 'node': '26'}})
        self.assertFalse(self.mise_dir.exists())
        self.assertEqual(self.backups(target.parent), ['global.toml.~1~'])
        # A node entry in the file it names wins over the default location.
        self.write(target, '[tools]\nnode = "24"\n')
        with patch.dict(os.environ, {'MISE_GLOBAL_CONFIG_FILE': str(target)}):
            status, out, _ = self.run_step('apply')
        self.assertEqual(status, 0)
        self.assertIn('already sets node', out)

    def test_global_config_file_that_is_not_toml_is_left_alone(self):
        target = self.root / 'versions'
        self.write(target, 'bun 1.2\n')
        with patch.dict(os.environ, {'MISE_GLOBAL_CONFIG_FILE': str(target)}):
            status, out, _ = self.run_step('apply')
        self.assertEqual(status, 0)
        self.assertIn('not a .toml file', out)
        self.assertEqual(target.read_text(), 'bun 1.2\n')

    def test_invalid_toml_is_left_alone(self):
        self.write(self.config, '[tools\nnode =')
        status, _, err = self.run_step('apply')
        self.assertEqual(status, 1)
        self.assertIn('could not be read', err)
        self.assertEqual(self.config.read_text(), '[tools\nnode =')

    def test_inline_tools_table_is_left_alone_without_damage(self):
        text = 'tools = { bun = "latest" }\n'
        self.write(self.config, text)
        status, _, err = self.run_step('apply')
        self.assertEqual(status, 1)
        self.assertIn('would not stay valid', err)
        self.assertEqual(self.config.read_text(), text)
        self.assertEqual(self.backups(), [])

    def test_symlinked_config_and_symlinked_directory_are_refused(self):
        target = self.root / 'dotfiles-config.toml'
        target.write_text('[tools]\nbun = "latest"\n')
        self.mise_dir.mkdir(parents=True)
        self.config.symlink_to(target)
        status, _, err = self.run_step('apply')
        self.assertEqual((status, 'symlink' in err), (1, True))
        self.assertEqual(target.read_text(), '[tools]\nbun = "latest"\n')
        self.config.unlink()
        self.mise_dir.rmdir()
        elsewhere = self.root / 'dotfiles-mise'
        elsewhere.mkdir()
        self.mise_dir.symlink_to(elsewhere)
        status, _, err = self.run_step('apply')
        self.assertEqual((status, 'symlink' in err), (1, True))
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_preserves_file_mode(self):
        self.write(self.config, OMARCHY_DEFAULT_CONFIG)
        self.config.chmod(0o600)
        self.run_step('apply')
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)

    def test_second_run_is_a_no_op(self):
        self.write(self.config, OMARCHY_DEFAULT_CONFIG)
        self.assertEqual(self.run_step('apply')[0], 3)
        after_first = self.config.read_text()
        status, out, _ = self.run_step('apply')
        self.assertEqual(status, 0)
        self.assertIn('already sets node', out)
        self.assertEqual(self.config.read_text(), after_first)
        self.assertEqual(self.backups(), ['config.toml.~1~'])

    def test_usage_error(self):
        for args in ((), ('bogus',), ('env',), ('apply', 'extra')):
            with self.subTest(args=args):
                status, _, err = self.run_step(*args)
                self.assertEqual(status, 2)
                self.assertIn('usage', err)

    def test_runs_as_a_script_against_a_fixture_home(self):
        env = {'PATH': os.environ['PATH'], 'HOME': str(self.home)}
        result = subprocess.run([sys.executable, '-I', str(SCRIPT), 'apply'], env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertTrue(self.config.is_file())


class WiringTests(unittest.TestCase):
    def test_apply_layer_installs_the_exact_pin_and_says_when_it_could_not(self):
        text = (REPO / 'scripts/apply-layer.sh').read_text()
        self.assertIn('"$root/scripts/groundwork-node-pin.py" apply', text)
        self.assertIn('-f "$root/scripts/groundwork-node-pin.py"', text)
        self.assertIn('mise install "node@$node_pin_version"', text)
        self.assertIn('NOT installed yet. Run: mise install node@', text)
        self.assertIn('the Node pin was skipped', text)

    def test_no_session_path_override_is_installed(self):
        # Decision 0016: Omarchy already activates mise in the session, and a
        # session PATH cannot fix launches through mise's command wrappers.
        self.assertFalse((REPO / 'scripts/groundwork-session-env.py').exists())
        self.assertNotIn('uwsm', (REPO / 'scripts/groundwork-node-pin.py').read_text())


if __name__ == '__main__':
    unittest.main()
