"""Session tool path and Node pin, against fixture HOMEs only (decision 0016)."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'scripts/groundwork-session-env.py'
spec = importlib.util.spec_from_file_location('groundwork_session_env', SCRIPT)
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
        env = {'HOME': str(self.home)}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop('XDG_CONFIG_HOME', None)
        self.config = self.home / '.config'
        self.dropin = self.config / 'uwsm/env.d' / helper.DROPIN
        self.mise_config = self.config / 'mise/config.toml'

    def run_step(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = helper.main(['groundwork-session-env.py', *args])
        return status, out.getvalue(), err.getvalue()


class SessionEnvFileTests(FixtureHome):
    def test_writes_owned_dropin_under_the_fixture_home(self):
        status, out, _ = self.run_step('env')
        self.assertEqual(status, 0)
        self.assertEqual(self.dropin.read_text(), helper.ENV_TEXT)
        self.assertTrue(self.dropin.read_text().startswith(helper.OWNER_LINE))
        self.assertIn('Log out', out)
        self.assertEqual(self.dropin.stat().st_mode & 0o777, 0o644)
        # Nothing but the drop-in was created.
        created = sorted(p.relative_to(self.home).as_posix() for p in self.home.rglob('*') if p.is_file())
        self.assertEqual(created, ['.config/uwsm/env.d/' + helper.DROPIN])

    def test_rerun_changes_nothing(self):
        self.run_step('env')
        before = self.dropin.stat().st_mtime_ns
        status, out, _ = self.run_step('env')
        self.assertEqual((status, out.strip()), (0, 'Session tool path is current.'))
        self.assertEqual(self.dropin.stat().st_mtime_ns, before)

    def test_stale_owned_file_is_rewritten(self):
        self.dropin.parent.mkdir(parents=True)
        self.dropin.write_text(helper.OWNER_LINE + '\nPATH=/old\n')
        status, out, _ = self.run_step('env')
        self.assertEqual(status, 0)
        self.assertIn('Updated', out)
        self.assertEqual(self.dropin.read_text(), helper.ENV_TEXT)

    def test_file_without_header_is_the_users_and_is_kept(self):
        self.dropin.parent.mkdir(parents=True)
        self.dropin.write_text('# mine\nexport PATH="$HOME/bin:$PATH"\n')
        status, out, _ = self.run_step('env')
        self.assertEqual(status, 0)
        self.assertIn('is yours', out)
        self.assertEqual(self.dropin.read_text(), '# mine\nexport PATH="$HOME/bin:$PATH"\n')

    def test_users_own_uwsm_env_is_never_touched(self):
        user_env = self.config / 'uwsm/env'
        user_env.parent.mkdir(parents=True)
        user_env.write_text('export FOO=1\n')
        self.run_step('env')
        self.assertEqual(user_env.read_text(), 'export FOO=1\n')

    def test_symlinked_target_and_parent_are_refused(self):
        elsewhere = self.root / 'elsewhere'
        elsewhere.mkdir()
        self.dropin.parent.mkdir(parents=True)
        self.dropin.symlink_to(elsewhere / 'file')
        status, _, err = self.run_step('env')
        self.assertEqual(status, 1)
        self.assertIn('symlink', err)
        self.assertFalse((elsewhere / 'file').exists())
        self.dropin.unlink()
        shutil.rmtree(self.config / 'uwsm')
        (self.config / 'uwsm').symlink_to(elsewhere)
        status, _, err = self.run_step('env')
        self.assertEqual(status, 1)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_xdg_config_home_selects_the_directory(self):
        custom = self.root / 'xdg'
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(custom)}):
            self.run_step('env')
        self.assertTrue((custom / 'uwsm/env.d' / helper.DROPIN).is_file())
        self.assertFalse(self.config.exists())


class SessionEnvShellTests(FixtureHome):
    """Source the real file text in a plain shell and read the resulting PATH."""

    SHELLS = [shell for shell in ('sh', 'dash', 'bash') if shutil.which(shell)]

    def source(self, path, shell='sh', **extra):
        env = {'HOME': str(self.home), 'PATH': path, **extra}
        script = '. "$1"; . "$1"; printf %s "$PATH"'
        self.dropin.parent.mkdir(parents=True, exist_ok=True)
        self.dropin.write_text(helper.ENV_TEXT)
        result = subprocess.run([shutil.which(shell), '-c', script, 'sh', str(self.dropin)],
                                env=env, capture_output=True, text=True, check=True)
        return result.stdout

    def test_shims_move_ahead_of_system_but_behind_wrappers(self):
        mise = self.home / '.local/share/mise'
        shims, wrap = f'{mise}/shims', f'{mise}/command-wrappers/bin'
        for shell in self.SHELLS:
            with self.subTest(shell=shell):
                # Omarchy's order: wrappers first, shims appended last.
                path = self.source(f'{wrap}:/tmp/mount:/usr/bin:/bin:{self.home}/.local/bin:{shims}', shell)
                self.assertEqual(path, f'{wrap}:{shims}:/tmp/mount:/usr/bin:/bin:{self.home}/.local/bin')

    def test_a_shims_first_path_still_ends_behind_wrappers(self):
        # A user env that prepended the shims in front of the wrappers is corrected.
        mise = self.home / '.local/share/mise'
        shims, wrap = f'{mise}/shims', f'{mise}/command-wrappers/bin'
        path = self.source(f'{shims}:{wrap}:/usr/bin')
        self.assertEqual(path, f'{wrap}:{shims}:/usr/bin')

    def test_without_wrappers_or_shims_on_path(self):
        shims = f'{self.home}/.local/share/mise/shims'
        self.assertEqual(self.source('/usr/bin:/bin'), f'{shims}:/usr/bin:/bin')
        self.assertEqual(self.source(''), shims)

    def test_mise_data_dir_and_xdg_data_home_are_honored(self):
        path = self.source('/usr/bin', MISE_DATA_DIR='/data/mise')
        self.assertEqual(path, '/data/mise/shims:/usr/bin')
        path = self.source('/usr/bin', XDG_DATA_HOME='/xdg/data')
        self.assertEqual(path, '/xdg/data/mise/shims:/usr/bin')

    def test_helper_variables_do_not_leak(self):
        self.dropin.parent.mkdir(parents=True)
        self.dropin.write_text(helper.ENV_TEXT)
        result = subprocess.run(['sh', '-c', '. "$1"; set | grep "^_gw_" || true', 'sh', str(self.dropin)],
                                env={'HOME': str(self.home), 'PATH': '/usr/bin:/bin'},
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, '')

    def test_shim_node_beats_system_node_and_bw_style_env_lookup(self):
        mise = self.home / '.local/share/mise'
        (mise / 'shims').mkdir(parents=True)
        system = self.root / 'usr-bin'
        system.mkdir()
        for directory, version in ((mise / 'shims', 'v26'), (system, 'v22')):
            node = directory / 'node'
            node.write_text(f'#!/bin/sh\necho {version}\n')
            node.chmod(0o755)
        before = f'{system}:{mise}/shims'
        self.assertEqual(subprocess.run(['/usr/bin/env', 'node'], env={'PATH': before},
                                        capture_output=True, text=True).stdout.strip(), 'v22')
        after = self.source(before)
        # The same lookup `#!/usr/bin/env node` does for /usr/bin/bw.
        self.assertEqual(subprocess.run(['/usr/bin/env', 'node'], env={'PATH': after},
                                        capture_output=True, text=True).stdout.strip(), 'v26')


class NodePinTests(FixtureHome):
    def write_config(self, text):
        self.mise_config.parent.mkdir(parents=True, exist_ok=True)
        self.mise_config.write_text(text)

    def backups(self):
        return sorted(p.name for p in self.mise_config.parent.glob('config.toml.~*~'))

    def test_creates_config_when_absent(self):
        status, out, _ = self.run_step('node')
        self.assertEqual(status, 3)
        self.assertEqual(tomllib.loads(self.mise_config.read_text()), {'tools': {'node': '26'}})
        self.assertEqual(self.backups(), [])

    def test_adds_node_under_existing_tools_and_keeps_everything_else(self):
        original = ('# my settings\n[settings]\nexperimental = true\n\n'
                    '[tools]\n# keep\nbun = "latest"  # comment\npython = "3.13"\n\n[env]\nFOO = "bar"\n')
        self.write_config(original)
        status, _, _ = self.run_step('node')
        self.assertEqual(status, 3)
        updated = self.mise_config.read_text()
        expected = tomllib.loads(original)
        expected['tools']['node'] = '26'
        self.assertEqual(tomllib.loads(updated), expected)
        self.assertIn('bun = "latest"  # comment\n', updated)
        self.assertIn('# my settings\n', updated)
        self.assertEqual(self.backups(), ['config.toml.~1~'])
        self.assertEqual((self.mise_config.parent / 'config.toml.~1~').read_text(), original)

    def test_appends_tools_table_when_missing_even_without_trailing_newline(self):
        self.write_config('[settings.upgrade]\nx = 1')
        status, _, _ = self.run_step('node')
        self.assertEqual(status, 3)
        self.assertEqual(tomllib.loads(self.mise_config.read_text()),
                         {'settings': {'upgrade': {'x': 1}}, 'tools': {'node': '26'}})

    def test_existing_node_entries_are_respected(self):
        for text in ('[tools]\nnode = "24"\n', '[tools]\nnode = "latest"\n',
                     '[tools]\nnode = { version = "22", lazy = true }\n', '[tools]\nnode = "26"\n'):
            with self.subTest(text=text):
                self.write_config(text)
                status, out, _ = self.run_step('node')
                self.assertEqual(status, 0)
                self.assertIn('already sets node', out)
                self.assertEqual(self.mise_config.read_text(), text)
                self.assertEqual(self.backups(), [])

    def test_node_in_conf_d_is_respected(self):
        self.write_config(OMARCHY_DEFAULT_CONFIG)
        conf_d = self.mise_config.parent / 'conf.d'
        conf_d.mkdir()
        (conf_d / '90-mine.toml').write_text('[tools]\nnode = "lts"\n')
        status, _, _ = self.run_step('node')
        self.assertEqual(status, 0)
        self.assertEqual(self.mise_config.read_text(), OMARCHY_DEFAULT_CONFIG)

    def test_invalid_toml_is_left_alone(self):
        self.write_config('[tools\nnode =')
        status, _, err = self.run_step('node')
        self.assertEqual(status, 1)
        self.assertIn('could not be read', err)
        self.assertEqual(self.mise_config.read_text(), '[tools\nnode =')

    def test_inline_tools_table_is_left_alone_without_damage(self):
        text = 'tools = { bun = "latest" }\n'
        self.write_config(text)
        status, _, err = self.run_step('node')
        self.assertEqual(status, 1)
        self.assertIn('would not stay valid', err)
        self.assertEqual(self.mise_config.read_text(), text)
        self.assertEqual(self.backups(), [])

    def test_symlinked_config_is_refused(self):
        target = self.root / 'dotfiles-config.toml'
        target.write_text('[tools]\nbun = "latest"\n')
        self.mise_config.parent.mkdir(parents=True)
        self.mise_config.symlink_to(target)
        status, _, err = self.run_step('node')
        self.assertEqual(status, 1)
        self.assertIn('symlink', err)
        self.assertEqual(target.read_text(), '[tools]\nbun = "latest"\n')

    def test_preserves_file_mode(self):
        self.write_config(OMARCHY_DEFAULT_CONFIG)
        self.mise_config.chmod(0o600)
        self.run_step('node')
        self.assertEqual(self.mise_config.stat().st_mode & 0o777, 0o600)

    def test_second_run_is_a_no_op(self):
        self.write_config(OMARCHY_DEFAULT_CONFIG)
        self.run_step('node')
        after_first = self.mise_config.read_text()
        status, out, _ = self.run_step('node')
        self.assertEqual(status, 0)
        self.assertEqual(self.mise_config.read_text(), after_first)
        self.assertEqual(self.backups(), ['config.toml.~1~'])


class WiringTests(FixtureHome):
    def test_apply_runs_both_steps_and_reports_the_pin_status(self):
        status, out, _ = self.run_step('apply')
        self.assertEqual(status, 3)
        self.assertTrue(self.dropin.is_file())
        self.assertTrue(self.mise_config.is_file())
        status, out, _ = self.run_step('apply')
        self.assertEqual(status, 0)

    def test_one_failing_step_does_not_stop_the_other(self):
        self.mise_config.parent.mkdir(parents=True)
        self.mise_config.write_text('[tools\n')
        status, _, _ = self.run_step('apply')
        self.assertEqual(status, 1)
        self.assertTrue(self.dropin.is_file())

    def test_usage_error(self):
        status, _, err = self.run_step('bogus')
        self.assertEqual(status, 2)
        self.assertIn('usage', err)

    def test_apply_layer_calls_the_helper_and_requires_it(self):
        text = (REPO / 'scripts/apply-layer.sh').read_text()
        self.assertIn('"$root/scripts/groundwork-session-env.py" apply', text)
        self.assertIn('-f "$root/scripts/groundwork-session-env.py"', text)


if __name__ == '__main__':
    unittest.main()
