"""Test the read-only PATH-shadow report with fake pacman and mise answers."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/groundwork-path-shadow.py'
spec = importlib.util.spec_from_file_location('groundwork_path_shadow', SCRIPT)
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)


def make_exe(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\nexit 99\n')
    path.chmod(0o755)
    return str(path)


class PathShadowTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='path-shadow-'))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.pkg_bin = self.root / 'usr/bin'
        self.owned = set()
        self.mise = {}
        self.calls = []
        self.envs = []
        self.timeout = set()

    def environ(self, *dirs, **extra):
        env = {'OMARCHY_PATH': str(self.root / 'o'), 'HOME': str(self.home), 'PATH': ':'.join(str(d) for d in dirs)}
        env.update(extra)
        return env

    def runner(self, argv, environ, cwd):
        self.calls.append(argv)
        self.envs.append((argv[0], dict(environ)))
        if argv[0] in self.timeout:
            return None, ''
        if argv[:2] == ['pacman', '-Qoq']:
            path = argv[-1]
            return (0, 'pkg-' + os.path.basename(path) + '\n') if path in self.owned else (1, '')
        if argv[:2] == ['mise', 'which']:
            found = self.mise.get(argv[2])
            return (0, found + '\n') if found else (1, '')
        raise AssertionError(argv)

    def check(self, name, owner, env):
        return ps.check_command(name, owner, env, self.runner)

    def test_pacman_first_hit_is_ok(self):
        exe = make_exe(self.pkg_bin / 'herdr')
        self.owned.add(exe)
        result = self.check('herdr', 'pacman', self.environ(self.pkg_bin))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['package'], 'pkg-herdr')

    def test_vendor_copy_shadows_package(self):
        exe = make_exe(self.pkg_bin / 'opencode')
        vendor = make_exe(self.home / '.opencode/bin/opencode')
        self.owned.add(exe)
        result = self.check('opencode', 'pacman', self.environ(self.home / '.opencode/bin', self.pkg_bin))
        self.assertEqual(result['status'], 'shadowed')
        self.assertEqual(result['winner'], vendor)
        self.assertEqual(result['managed'], exe)

    def test_no_package_owner_is_unmanaged(self):
        make_exe(self.pkg_bin / 'cursor')
        result = self.check('cursor', 'pacman', self.environ(self.pkg_bin))
        self.assertEqual(result['status'], 'unmanaged')

    def test_absent_command_is_not_reported(self):
        self.assertEqual(self.check('keyd', 'pacman', self.environ(self.pkg_bin))['status'], 'absent')

    def test_mise_wrapper_and_install_dirs_are_ok(self):
        data = self.home / '.local/share/mise'
        wrapper = make_exe(data / 'command-wrappers/bin/claude')
        self.mise['claude'] = str(data / 'installs/claude/latest/claude')
        result = self.check('claude', 'mise', self.environ(data / 'command-wrappers/bin'))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['winner'], wrapper)

    def test_mise_data_dir_override(self):
        data = self.root / 'elsewhere'
        make_exe(data / 'shims/codex')
        env = self.environ(data / 'shims', MISE_DATA_DIR=str(data))
        self.assertEqual(self.check('codex', 'mise', env)['status'], 'ok')

    def test_mise_shadowed_by_other_directory(self):
        data = self.home / '.local/share/mise'
        managed = make_exe(data / 'installs/grok/latest/grok')
        self.mise['grok'] = managed
        make_exe(self.home / '.grok/bin/grok')
        env = self.environ(self.home / '.grok/bin', data / 'installs/grok/latest')
        result = self.check('grok', 'mise', env)
        self.assertEqual(result['status'], 'shadowed')
        self.assertEqual(result['managed'], managed)

    def test_mise_symlink_to_managed_binary_is_ok(self):
        data = self.home / '.local/share/mise'
        managed = make_exe(data / 'installs/codex/latest/codex')
        self.mise['codex'] = managed
        link = self.home / '.local/bin/codex'
        link.parent.mkdir(parents=True)
        link.symlink_to(managed)
        self.assertEqual(self.check('codex', 'mise', self.environ(self.home / '.local/bin'))['status'], 'ok')

    def test_mise_managed_but_directory_off_path_is_missing(self):
        data = self.home / '.local/share/mise'
        self.mise['grok'] = make_exe(data / 'installs/grok/latest/grok')
        self.assertEqual(self.check('grok', 'mise', self.environ(self.pkg_bin))['status'], 'missing')

    def test_t3_installed_path(self):
        installed = make_exe(self.home / '.local/bin/t3')
        self.assertEqual(self.check('t3', 'groundwork', self.environ(self.home / '.local/bin'))['status'], 'ok')
        other = make_exe(self.root / 'npm/bin/t3')
        result = self.check('t3', 'groundwork', self.environ(self.root / 'npm/bin', self.home / '.local/bin'))
        self.assertEqual(result['status'], 'shadowed')
        self.assertEqual(result['winner'], other)
        self.assertEqual(result['managed'], installed)

    def test_t3_install_dir_override(self):
        make_exe(self.root / 'tbin/t3')
        env = self.environ(self.root / 'tbin', T3CODE_INSTALL_BIN_DIR=str(self.root / 'tbin'))
        self.assertEqual(self.check('t3', 'groundwork', env)['status'], 'ok')

    def test_attribution_names_file_line_and_effect(self):
        bashrc = self.home / '.bashrc'
        bashrc.write_text('# export PATH="$HOME/.opencode/bin:$PATH"\n'
                          'alias x=y\n'
                          'export PATH="$HOME/.opencode/bin:$PATH"\n')
        uwsm = self.home / '.config/uwsm/env'
        uwsm.parent.mkdir(parents=True)
        uwsm.write_text('export PATH="$PATH:${HOME}/.opencode/bin"\n')
        files = ps.shell_files(str(self.home), str(self.root / 'omarchy'), str(self.root / 'sys'))
        found = ps.attribute(str(self.home / '.opencode/bin'), files, str(self.home))
        self.assertEqual([(Path(f['file']).name, f['line'], f['effect']) for f in found],
                         [('.bashrc', 3, 'prepends'), ('env', 1, 'appends')])

    def test_attribution_reads_omarchy_defaults_and_cargo_env(self):
        bootstrap = self.root / 'omarchy/default/bash/env-bootstrap'
        bootstrap.parent.mkdir(parents=True)
        bootstrap.write_text('PATH="/opt/vendor/bin${PATH:+:$PATH}"\n')
        (self.home / '.profile').write_text('# . "$HOME/.cargo/env"\n. "$HOME/.cargo/env"\n')
        files = ps.shell_files(str(self.home), str(self.root / 'omarchy'), str(self.root / 'sys'))
        self.assertEqual(len(ps.attribute('/opt/vendor/bin', files, str(self.home))), 1)
        cargo = ps.attribute(str(self.home / '.cargo/bin'), files, str(self.home))
        self.assertEqual([c['line'] for c in cargo], [2])

    def test_unattributed_winner_is_said_plainly(self):
        exe = make_exe(self.pkg_bin / 'syncthing')
        make_exe(self.root / 'odd/syncthing')
        self.owned.add(exe)
        env = self.environ(self.root / 'odd', self.pkg_bin)
        results = ps.scan(env, self.runner, commands=['syncthing'], files=[])
        text = ps.render(results, str(self.home))
        self.assertIn('shadowed', text)
        self.assertIn('no scanned shell file names', text)

    def test_scan_never_modifies_files(self):
        exe = make_exe(self.pkg_bin / 'tailscale')
        vendor = make_exe(self.root / 'v/tailscale')
        self.owned.add(exe)
        (self.home / '.bashrc').write_text('export PATH="%s:$PATH"\n' % (self.root / 'v'))
        before = sorted((str(p), p.stat().st_mtime_ns, p.read_bytes()) for p in self.root.rglob('*') if p.is_file())
        ps.scan(self.environ(self.root / 'v', self.pkg_bin, OMARCHY_PATH=str(self.root / 'o')), self.runner,
                commands=['tailscale'], system_root=str(self.root / 'sys'))
        after = sorted((str(p), p.stat().st_mtime_ns, p.read_bytes()) for p in self.root.rglob('*') if p.is_file())
        self.assertEqual(before, after)
        self.assertTrue(os.path.exists(vendor))
        self.assertTrue(all(c[0] in ('pacman', 'mise') and c[1] in ('-Qoq', 'which') for c in self.calls))

    def test_cli_with_fake_pacman_on_path(self):
        fakes = self.root / 'fakes'
        pacman = fakes / 'pacman'
        pacman.parent.mkdir()
        pacman.write_text('#!/bin/sh\n[ "$3" = "%s" ] && echo herdr-pkg\n' % (self.pkg_bin / 'herdr') + 'exit 0\n')
        pacman.chmod(0o755)
        # the fake exits 0 with no output for unowned files, which is not an owner
        make_exe(self.pkg_bin / 'herdr')
        make_exe(self.root / 'v/herdr')
        (self.home / '.profile').write_text('export PATH="%s:$PATH"\n' % (self.root / 'v'))
        env = self.environ(self.root / 'v', self.pkg_bin, fakes)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(ps.main(['herdr', '--json'], env, str(self.root / 'sys')), 0)
            item = json.loads(out.getvalue())[0]
        self.assertEqual(item['status'], 'shadowed')
        self.assertEqual(item['attribution'][0]['effect'], 'prepends')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(ps.main(['herdr', '--strict'], env, str(self.root / 'sys')), 1)
        self.assertIn('Report only', out.getvalue())

    def test_mise_which_is_offline_and_never_installs(self):
        make_exe(self.home / '.opencode/bin/opencode')
        self.check('opencode', 'mise', self.environ(self.home / '.opencode/bin', MISE_OFFLINE='0'))
        env = next(e for name, e in self.envs if name == 'mise')
        for key in ('MISE_OFFLINE', 'MISE_AUTO_INSTALL', 'MISE_EXEC_AUTO_INSTALL',
                    'MISE_NOT_FOUND_AUTO_INSTALL', 'MISE_TASK_RUN_AUTO_INSTALL'):
            self.assertEqual(env[key], '1' if key == 'MISE_OFFLINE' else '0', key)

    def test_mise_timeout_is_unknown_not_unmanaged(self):
        make_exe(self.home / '.opencode/bin/opencode')
        self.timeout.add('mise')
        result = self.check('opencode', 'mise', self.environ(self.home / '.opencode/bin'))
        self.assertEqual(result['status'], 'unknown')

    def test_mise_timeout_still_ok_inside_mise_data_dir(self):
        data = self.home / '.local/share/mise'
        make_exe(data / 'shims/claude')
        self.timeout.add('mise')
        self.assertEqual(self.check('claude', 'mise', self.environ(data / 'shims'))['status'], 'ok')

    def test_pacman_timeout_is_unknown(self):
        make_exe(self.pkg_bin / 'herdr')
        self.timeout.add('pacman')
        self.assertEqual(self.check('herdr', 'pacman', self.environ(self.pkg_bin))['status'], 'unknown')

    def test_trailing_comment_is_not_attribution(self):
        self.assertEqual(ps.strip_comment('export A=1 # PATH=/x'), 'export A=1')
        self.assertEqual(ps.strip_comment('export PATH="/a#b:$PATH" # note'), 'export PATH="/a#b:$PATH"')
        (self.home / '.bashrc').write_text('export FOO=1 # PATH="$HOME/.bun/bin:$PATH"\n'
                                           'export PATH="$HOME/.bun/bin:$PATH" # bun\n')
        files = ps.shell_files(str(self.home), str(self.root / 'o'), str(self.root / 'sys'))
        found = ps.attribute(str(self.home / '.bun/bin'), files, str(self.home))
        self.assertEqual([f['line'] for f in found], [2])

    def test_scan_reads_only_the_injected_system_root(self):
        make_exe(self.pkg_bin / 'keyd')
        sys_root = self.root / 'sys'
        (sys_root / 'etc/profile.d').mkdir(parents=True)
        (sys_root / 'etc/profile.d/x.sh').write_text('export PATH="%s:$PATH"\n' % self.pkg_bin)
        env = self.environ(self.pkg_bin, OMARCHY_PATH=str(self.root / 'o'))
        item = ps.scan(env, self.runner, commands=['keyd'], system_root=str(sys_root))[0]
        self.assertEqual(item['status'], 'unmanaged')
        self.assertEqual(Path(item['attribution'][0]['file']), sys_root / 'etc/profile.d/x.sh')

    def test_unknown_command_is_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            ps.main(['rm'], self.environ())


if __name__ == '__main__':
    unittest.main()
