"""Exercise installer effects with fake package/service commands and a private /etc."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
STUB = '''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
root = pathlib.Path(os.environ['INSTALL_FIXTURE'])
with (root / 'calls.jsonl').open('a') as log:
    log.write(json.dumps([name, *args]) + '\\n')
if name == 'uname':
    print(os.environ.get('FIXTURE_ARCH', 'aarch64'))
elif name == 'sudo':
    if args[0] == 'keyd':
        sys.exit(int(os.environ.get('FIXTURE_RELOAD_STATUS', '0')))
    if args[0] == 'systemctl':
        sys.exit(0)
    if args[0] not in ('mkdir', 'cmp', 'cp'):
        raise SystemExit('Unexpected privileged operation: ' + repr(args))
    args = [str(root / 'etc/keyd') + arg[len('/etc/keyd'):] if arg == '/etc/keyd' or arg.startswith('/etc/keyd/') else arg for arg in args]
    sys.exit(subprocess.call(['/usr/bin/' + args[0], *args[1:]]))
elif name == 'stow':
    source = pathlib.Path(args[args.index('--dir') + 1]) / 'hypr'
    target = pathlib.Path(args[args.index('--target') + 1])
    for path in source.rglob('*'):
        if path.is_file():
            link = target / path.relative_to(source)
            link.parent.mkdir(parents=True, exist_ok=True)
            if not link.is_symlink():
                link.symlink_to(path)
elif name == 'hyprctl':
    if args == ['reload']:
        sys.exit(int(os.environ.get('FIXTURE_HYPR_RELOAD_STATUS', '0')))
    if args == ['configerrors']:
        print(os.environ.get('FIXTURE_HYPR_ERRORS', ''), end='')
    else:
        raise SystemExit('Unexpected Hyprland command')
elif name == 'curl':
    status = int(os.environ.get('FIXTURE_CURL_STATUS', '0'))
    if status:
        sys.exit(status)
    pathlib.Path(args[args.index('-o') + 1]).write_text("""#!/bin/sh
printf '%s' "$T3CODE_CHANNEL" > "$INSTALL_FIXTURE/t3-channel"
test -z "${T3CODE_VERSION:-}" || exit 1
if [ "${FIXTURE_INSTALL_STATUS:-0}" != 0 ]; then exit "$FIXTURE_INSTALL_STATUS"; fi
bin_dir="${T3CODE_INSTALL_BIN_DIR:-$HOME/.local/bin}"
mkdir -p "$bin_dir"
cp "$INSTALL_FIXTURE/nightly-t3" "$bin_dir/t3"
chmod +x "$bin_dir/t3"
exit "${FIXTURE_INSTALL_STATUS:-0}"
""")
'''


class InstallKeydTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='groundwork-install-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        # A private PATH keeps installed host tools (notably Herdr) from making
        # a missing-package fixture pass accidentally.
        for name in ('bash', 'sh', 'python3', 'env', 'timeout', 'mktemp', 'dirname',
                     'rm', 'mkdir', 'cp', 'chmod', 'mv', 'find', 'readlink'):
            (self.bin / name).symlink_to(shutil.which(name))
        stub = self.bin / 'stub'
        stub.write_text(STUB)
        stub.chmod(0o755)
        for name in ('uname', 'omarchy', 'stow', 't3', 'herdr', 'tailscale', 'keyd', 'sudo', 'hyprctl', 'curl'):
            (self.bin / name).symlink_to(stub)
        (self.root / 'home').mkdir()
        (self.root / 'nightly-t3').write_text('#!/bin/sh\nprintf "t3 0.0.0-nightly.20261018.1\\n"\n')
        self.config = self.root / 'etc/keyd/default.conf'
        self.env = dict(os.environ, INSTALL_FIXTURE=str(self.root),
                        HYPRLAND_INSTANCE_SIGNATURE='',
                        HOME=str(self.root / 'home'),
                        PATH=f'{self.root}/home/.local/bin:{self.bin}')
        self.env.pop('T3CODE_INSTALL_BIN_DIR', None)

    def run_install(self, *args, success=True):
        result = subprocess.run(['bash', str(REPO / 'install.sh'), *args],
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def calls(self):
        log = self.root / 'calls.jsonl'
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def seed_config(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text('[ids]\n*\n[main]\ncapslock = esc\n')
        return self.config.read_bytes()

    def test_default_installs_enables_and_reloads(self):
        self.run_install()
        self.assertEqual(self.config.read_bytes(), (REPO / 'keyd/default.conf').read_bytes())
        calls = self.calls()
        self.assertIn(['omarchy', 'pkg', 'add', 'herdr'], calls)
        self.assertIn(['omarchy', 'pkg', 'add', 'tailscale'], calls)
        self.assertIn(['sudo', 'systemctl', 'enable', '--now', 'tailscaled'], calls)
        package = calls.index(['omarchy', 'pkg', 'add', 'keyd'])
        enable = calls.index(['sudo', 'systemctl', 'enable', '--now', 'keyd'])
        reload = calls.index(['sudo', 'keyd', 'reload'])
        self.assertLess(package, enable)
        self.assertLess(enable, reload)

    def test_existing_config_backup_and_idempotent_rerun(self):
        original = self.seed_config()
        self.run_install()
        self.assertEqual(self.config.with_name('default.conf.~1~').read_bytes(), original)
        self.run_install()
        self.assertFalse(self.config.with_name('default.conf.~2~').exists())
        self.assertEqual(self.calls().count(['sudo', 'keyd', 'reload']), 2)

    def test_opt_out_preserves_existing_configuration_and_service(self):
        original = self.seed_config()
        self.run_install('--without-keyd')
        self.assertEqual(self.config.read_bytes(), original)
        self.assertFalse(any('keyd' in arg for call in self.calls() for arg in call))

    def test_opt_out_on_fresh_install_does_not_create_config(self):
        self.run_install('--without-keyd')
        self.assertFalse(self.config.exists())

    def test_legacy_opt_in_still_works(self):
        self.run_install('--with-keyd')
        self.assertIn(['sudo', 'keyd', 'reload'], self.calls())

    def test_invalid_arguments_and_help_do_not_mutate(self):
        for args, success in [(('--typo',), False),
                              (('--with-keyd', '--without-keyd'), False),
                              (('--without-keyd', '--with-keyd'), False),
                              (('--help',), True)]:
            with self.subTest(args=args):
                self.run_install(*args, success=success)
                self.assertEqual(self.calls(), [])

    def test_unsupported_architecture_does_not_mutate(self):
        self.env['FIXTURE_ARCH'] = 'x86_64'
        self.run_install(success=False)
        self.assertTrue(all(call[0] == 'uname' for call in self.calls()))

    def test_reload_failure_is_not_reported_as_success(self):
        self.env['FIXTURE_RELOAD_STATUS'] = '1'
        result = self.run_install(success=False)
        self.assertNotIn('keyd is running.', result.stdout)

    def test_existing_input_is_backed_up_and_link_is_reused(self):
        target = self.root / 'home/.config/hypr/input.lua'
        target.parent.mkdir(parents=True)
        target.write_text('-- personal input\n')
        backup = target.with_name('input.lua.bak-omarchy-groundwork')
        backup.write_text('-- previous backup\n')
        self.run_install()
        self.assertEqual(target.resolve(), REPO / 'packages/hypr/.config/hypr/input.lua')
        self.assertEqual(backup.read_text(), '-- personal input\n')
        self.assertEqual(backup.with_name(backup.name + '.~1~').read_text(), '-- previous backup\n')
        self.run_install()
        self.assertFalse(backup.with_name(backup.name + '.~2~').exists())

    def test_desktop_reload_is_checked_before_keyd(self):
        self.env['HYPRLAND_INSTANCE_SIGNATURE'] = 'fixture-only'
        self.run_install()
        calls = self.calls()
        self.assertLess(calls.index(['hyprctl', 'reload']), calls.index(['hyprctl', 'configerrors']))
        self.assertLess(calls.index(['hyprctl', 'configerrors']), calls.index(['sudo', 'keyd', 'reload']))

    def test_desktop_config_errors_fail_without_success_claim(self):
        self.env.update(HYPRLAND_INSTANCE_SIGNATURE='fixture-only', FIXTURE_HYPR_ERRORS='bad input option')
        result = self.run_install(success=False)
        self.assertIn('bad input option', result.stderr)
        self.assertNotIn(['sudo', 'keyd', 'reload'], self.calls())
        self.assertNotIn('are installed.', result.stdout)

    def test_failed_desktop_reload_stops_before_config_or_keyd(self):
        self.env.update(HYPRLAND_INSTANCE_SIGNATURE='fixture-only', FIXTURE_HYPR_RELOAD_STATUS='1')
        self.run_install(success=False)
        self.assertNotIn(['hyprctl', 'configerrors'], self.calls())
        self.assertNotIn(['sudo', 'keyd', 'reload'], self.calls())

    def test_installer_download_failure_stops_before_home_changes(self):
        self.env['FIXTURE_CURL_STATUS'] = '1'
        self.run_install(success=False)
        self.assertTrue(any(call[0] == 'curl' for call in self.calls()))
        self.assertFalse(any(call[0] in ('stow', 'hyprctl') for call in self.calls()))
        self.assertFalse(self.config.exists())

    def test_nightly_is_selected_even_with_existing_t3_and_ambient_version(self):
        self.env['T3CODE_VERSION'] = 'old-stable-fixture'
        self.run_install()
        self.assertEqual((self.root / 't3-channel').read_text(), 'nightly')
        self.run_install()
        self.assertEqual(sum(call[0] == 'curl' for call in self.calls()), 2)

    def test_nightly_installer_failure_stops_before_home_changes(self):
        self.env['FIXTURE_INSTALL_STATUS'] = '1'
        self.run_install(success=False)
        self.assertFalse(any(call[0] in ('stow', 'hyprctl') for call in self.calls()))

    def test_package_helper_successful_skip_fails_before_configuration(self):
        for package in ('herdr', 'tailscale'):
            with self.subTest(package=package):
                (self.bin / package).unlink()
                result = self.run_install(success=False)
                self.assertIn(f'{package} is still unavailable', result.stderr)
                self.assertNotIn('are installed.', result.stdout)
                self.assertFalse(any(call[0] in ('curl', 'stow', 'hyprctl') for call in self.calls()))
                (self.bin / package).symlink_to(self.bin / 'stub')

    def test_stable_t3_shadowing_is_reported_without_removing_it(self):
        self.env['PATH'] = f'{self.bin}:{self.root}/home/.local/bin'
        result = self.run_install(success=False)
        self.assertIn('T3 command shadowing', result.stderr)
        self.assertTrue((self.bin / 't3').is_symlink())
        self.assertTrue((self.root / 'home/.local/bin/t3').is_file())
        self.assertFalse(any(call[0] in ('stow', 'hyprctl') for call in self.calls()))

    def test_missing_local_bin_on_path_is_reported(self):
        (self.bin / 't3').unlink()
        self.env['PATH'] = str(self.bin)
        result = self.run_install(success=False)
        self.assertIn('missing PATH entry', result.stderr)

    def test_custom_install_bin_is_respected(self):
        custom = self.root / 'custom-bin'
        self.env['T3CODE_INSTALL_BIN_DIR'] = str(custom)
        self.env['PATH'] = f'{custom}:' + self.env['PATH']
        self.run_install()
        self.assertTrue((custom / 't3').is_file())

    def test_wrong_channel_fails_before_configuration(self):
        (self.root / 'nightly-t3').write_text('#!/bin/sh\nprintf "t3 0.0.0-stable\\n"\n')
        result = self.run_install(success=False)
        self.assertIn('Expected T3 Code Nightly', result.stderr)
        self.assertFalse(any(call[0] in ('stow', 'hyprctl') for call in self.calls()))


if __name__ == '__main__':
    unittest.main()
