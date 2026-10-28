"""Exercise installer effects with fake package/service commands and a private /etc."""
import json
import re
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
# Linux CI provides these as GNU tools under /usr/bin. A macOS proof uses
# Homebrew coreutils when that path is absent, because the installer passes
# GNU flags such as cp --backup.
GNU_BIN = '/opt/homebrew/opt/coreutils/libexec/gnubin'


def host_tool(name):
    for directory in ('/usr/bin', GNU_BIN, '/bin'):
        candidate = os.path.join(directory, name)
        if os.path.exists(candidate):
            return candidate
    found = shutil.which(name)
    if not found:
        raise AssertionError('missing tool: ' + name)
    return found


STUB = '''#!/usr/bin/env python3
import hashlib, io, json, os, pathlib, subprocess, sys, zipfile
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
root = pathlib.Path(os.environ['INSTALL_FIXTURE'])
with (root / 'calls.jsonl').open('a') as log:
    log.write(json.dumps([name, *args]) + '\\n')
if name == 'uname':
    print('Linux' if args == ['-s'] else os.environ.get('FIXTURE_ARCH', 'aarch64'))
elif name == 'sudo':
    if args[:2] == ['-n', 'true']:
        sys.exit(0 if os.environ.get('FIXTURE_SUDO_CACHED', '1') == '1' else 1)
    if args == ['-v']:
        if os.environ.get('FIXTURE_REQUIRE_TTY') == '1':
            terminal = os.open('/dev/tty', os.O_RDWR)
            os.write(terminal, b'Fixture password:')
            answer = os.read(terminal, 128).strip()
            os.close(terminal)
            if answer != b'fixture-password':
                sys.exit(1)
        sys.exit(int(os.environ.get('FIXTURE_SUDO_V_STATUS', '0')))
    if args in (['/usr/bin/true'], ['-n', '/usr/bin/true']):
        if os.environ.get('FIXTURE_SUDO_CACHED', '1') == '1':
            sys.exit(0)
        if args[0] == '-n':
            sys.exit(1)
        if os.environ.get('FIXTURE_REQUIRE_TTY') == '1':
            terminal = os.open('/dev/tty', os.O_RDWR)
            os.write(terminal, b'Fixture password:')
            answer = os.read(terminal, 128).strip()
            os.close(terminal)
            sys.exit(0 if answer == b'fixture-password' else 1)
        sys.exit(int(os.environ.get('FIXTURE_SUDO_V_STATUS', '0')))
    if args[0] == 'keyd':
        sys.exit(int(os.environ.get('FIXTURE_RELOAD_STATUS', '0')))
    if args[0] in ('systemctl', 'ufw'):
        sys.exit(0)
    if args[0] not in ('mkdir', 'install', 'cmp', 'cp', 'chmod'):
        raise SystemExit('Unexpected privileged operation: ' + repr(args))
    if args[0] == 'cp' and '/usr/share/chromium/extensions/' in args[-1]:
        if os.environ.get('FIXTURE_EXTENSION_WRITE_FAIL') == '1':
            sys.exit(1)
    for prefix in ('/etc/keyd', '/usr/share/chromium'):
        args = [str(root / prefix.lstrip('/')) + arg[len(prefix):] if arg == prefix or arg.startswith(prefix + '/') else arg for arg in args]
    host_binary = None
    for directory in ('/usr/bin', '/opt/homebrew/opt/coreutils/libexec/gnubin', '/bin'):
        candidate = directory + '/' + args[0]
        if os.path.exists(candidate):
            host_binary = candidate
            break
    if host_binary is None:
        raise SystemExit('missing host tool: ' + args[0])
    sys.exit(subprocess.call([host_binary, *args[1:]]))
elif name == 'omarchy-mise-default-tools':
    sys.exit(1 if os.environ.get('FIXTURE_MISE_OPTED_OUT') == '1' else 0)
elif name == 'mise':
    if os.environ.get('FIXTURE_MISE_FAILURE') == '1':
        sys.exit(1)
    if args == ['latest', 'bun']:
        print(os.environ.get('FIXTURE_BUN_LATEST', '1.2.23'))
        sys.exit(0)
    if args == ['current', 'bun']:
        print(os.environ.get('FIXTURE_BUN_CURRENT', os.environ.get('FIXTURE_BUN_LATEST', '1.2.23')))
        sys.exit(0)
    provider = args[-1]
    if args[0] == 'which':
        if provider == 'bun' and os.environ.get('FIXTURE_BUN_WHICH'):
            target = pathlib.Path(os.environ['FIXTURE_BUN_WHICH'])
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_text('#!/bin/sh\\nexit 0\\n')
                target.chmod(0o755)
            print(target)
            sys.exit(0)
        target = root / 'mise-installed' / provider
        if target.is_file():
            print(target)
            sys.exit(0)
        sys.exit(1)
    if os.environ.get('FIXTURE_MISE_UNRESOLVED') == '1':
        sys.exit(0)
    installed = root / 'mise-installed' / provider
    installed.parent.mkdir(exist_ok=True)
    installed.write_text('#!/bin/sh\\nexit 0\\n')
    installed.chmod(0o755)
    target = root / 'bin' / provider
    if not target.exists():
        target.symlink_to(root / 'bin' / 'stub')
elif name == 'omarchy-install-cursor':
    status = int(os.environ.get('FIXTURE_CURSOR_INSTALL_STATUS', '0'))
    if status:
        sys.exit(status)
    if os.environ.get('FIXTURE_CURSOR_INSTALL_SKIP_BIN') == '1':
        sys.exit(0)
    cursor_bin = root / 'bin' / 'cursor'
    if not cursor_bin.exists():
        cursor_bin.symlink_to(root / 'bin' / 'stub')
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
    output = pathlib.Path(args[args.index('-o') + 1])
    url = next((arg for arg in args if arg.startswith('https://') or arg.startswith('http://')), '')
    if 'github.com/oven-sh/bun/releases/download/' in url:
        if not url.startswith('https://'):
            sys.exit(1)
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, 'w') as archive:
            # Zip timestamps move every two seconds. The archive and its
            # checksum are separate downloads, so the entry date stays fixed.
            archive.writestr(zipfile.ZipInfo('bun-linux-aarch64/bun', date_time=(1980, 1, 1, 0, 0, 0)), b'\\x7fELFfixture-bun')
        data = payload.getvalue()
        if url.endswith('/bun-linux-aarch64.zip'):
            output.write_bytes(data)
            sys.exit(0)
        if url.endswith('/SHASUMS256.txt'):
            digest = hashlib.sha256(data).hexdigest()
            if os.environ.get('FIXTURE_BUN_BAD_CHECKSUM') == '1':
                digest = '0' * 64
            output.write_text(digest + '  bun-linux-aarch64.zip\\n')
            sys.exit(0)
        sys.exit(1)
    output.write_text("""#!/bin/sh
printf '%s' "$T3CODE_CHANNEL" > "$INSTALL_FIXTURE/t3-channel"
test -z "${T3CODE_VERSION:-}" || test "$T3CODE_VERSION" = "${FIXTURE_PINNED_T3:-}" || exit 1
printf '%s' "${T3CODE_VERSION:-}" > "$INSTALL_FIXTURE/t3-version"
if [ "${FIXTURE_INSTALL_STATUS:-0}" != 0 ]; then exit "$FIXTURE_INSTALL_STATUS"; fi
bin_dir="${T3CODE_INSTALL_BIN_DIR:-$HOME/.local/bin}"
mkdir -p "$bin_dir"
cp "$INSTALL_FIXTURE/nightly-t3" "$bin_dir/t3"
chmod +x "$bin_dir/t3"
exit "${FIXTURE_INSTALL_STATUS:-0}"
""")
elif name == 'flatpak':
    app = next((arg for arg in reversed(args) if '.' in arg and not arg.startswith('-')), '')
    marker = {
        'com.bitwarden.desktop': root / 'bitwarden-flatpak',
        'io.freetubeapp.FreeTube': root / 'freetube-flatpak',
    }.get(app)
    if 'info' in args:
        sys.exit(0 if marker is not None and marker.exists() else 1)
    if 'install' in args:
        status_var = 'FIXTURE_FREETUBE_FLATPAK_STATUS' if app == 'io.freetubeapp.FreeTube' else 'FIXTURE_FLATPAK_STATUS'
        status = int(os.environ.get(status_var, '0'))
        if not status and marker is not None:
            marker.touch()
        sys.exit(status)
elif name == 'systemctl':
    if args[:1] in (['is-enabled'], ['is-active']):
        sys.exit(3 if args[-1] in os.environ.get('FIXTURE_SERVICES_DOWN', '').split() else 0)
    sys.exit(int(os.environ.get('FIXTURE_USER_SYSTEMCTL_STATUS', '0')))
elif name == 'getconf':
    print(os.environ.get('FIXTURE_PAGESIZE', '4096') if args == ['PAGESIZE'] else '')
elif name == 'readelf':
    align = os.environ.get('FIXTURE_LOAD_ALIGN', '')
    if args[:1] == ['-lW'] and align:
        print('  LOAD 0x000000 0x0000000000400000 0x0000000000400000 0x000100 0x000100 R E ' + align)
elif name == 'bun':
    if args == ['--version']:
        sys.exit(int(os.environ.get('FIXTURE_BUN_STATUS', '0')))
    if args == ['-v']:
        sys.exit(0)
elif name == 'omarchy-install-dev-env':
    if args != ['bun']:
        sys.exit(1)
    installed = root / 'mise-installed' / 'bun'
    installed.parent.mkdir(exist_ok=True)
    if not installed.exists():
        installed.write_text('#!/bin/sh\\nexit 0\\n')
        installed.chmod(0o755)
    target = root / 'bin' / 'bun'
    if not target.exists():
        target.symlink_to(root / 'bin' / 'stub')
    # mise verify() runs `bun -v` after the files are in place.
    sys.exit(subprocess.call([str(target), '-v']))
elif name == 'omarchy-remove-dev-env':
    if args != ['bun']:
        sys.exit(1)
    for path in (root / 'bin' / 'bun', root / 'mise-installed' / 'bun'):
        if path.is_symlink() or path.is_file():
            path.unlink()
elif name == 'omarchy':
    if args[:2] == ['pkg', 'add'] and len(args) > 2 and args[2] in ('ollama', 'ollama-vulkan', 'vulkan-tools'):
        if args[2] == 'ollama':
            target = root / 'bin' / 'ollama'
            if not target.exists():
                target.write_bytes(b'\\x7fELF' + bytes(16))
                target.chmod(0o755)
        elif args[2] == 'ollama-vulkan':
            dest = root / 'usr/lib/ollama/vulkan'
            dest.mkdir(parents=True, exist_ok=True)
            lib = dest / 'libggml-vulkan.so'
            if not lib.exists():
                lib.write_bytes(b'\\x7fELF' + bytes(16))
        else:
            target = root / 'bin' / 'vulkaninfo'
            if not target.exists():
                target.symlink_to(root / 'bin' / 'stub')
    if args[:2] == ['pkg', 'add'] and os.environ.get('FIXTURE_MATERIALIZE_ADDED') == '1' and len(args) > 2:
        mapping = {'openssh': 'ssh', 'libnotify': 'notify-send', 'wl-clipboard': 'wl-copy', 'xdg-utils': 'xdg-open'}
        executable = mapping.get(args[2], args[2])
        target = root / 'bin' / executable
        if not target.exists():
            target.symlink_to(root / 'bin' / 'stub')
    if args[:2] == ['plugin', 'list'] and '--json' in args:
        status = int(os.environ.get('FIXTURE_PLUGIN_LIST_STATUS', '0'))
        if status:
            sys.exit(status)
        print(os.environ.get('FIXTURE_PLUGIN_LIST', '[]'))
'''


class InstallKeydTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='groundwork-install-')
        self.addCleanup(self.temp.cleanup)
        # Resolve macOS /var -> /private/var so the vault walk does not treat
        # that directory symlink as part of the fixture path. Linux /tmp is
        # already a real directory, so this stays the same path there.
        self.root = Path(self.temp.name).resolve()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        # A private PATH keeps installed host tools (notably Herdr) from making
        # a missing-package fixture pass accidentally.
        for name in ('bash', 'sh', 'python3', 'env'):
            (self.bin / name).symlink_to(shutil.which(name))
        for name in ('timeout', 'mktemp', 'dirname', 'rm', 'mkdir', 'cp', 'chmod',
                     'mv', 'find', 'readlink', 'sleep', 'cmp', 'stat'):
            (self.bin / name).symlink_to(host_tool(name))
        stub = self.bin / 'stub'
        stub.write_text(STUB)
        stub.chmod(0o755)
        for name in ('uname', 'omarchy', 'omarchy-mise-default-tools', 'omarchy-install-dev-env', 'omarchy-remove-dev-env', 'stow', 't3', 'herdr', 'tailscale', 'keyd', 'sudo', 'hyprctl', 'curl', 'obsidian', 'bw', 'flatpak', 'chromium', 'claude', 'codex', 'opencode', 'grok', 'cursor', 'mise', 'wayvnc', 'systemctl', 'ufw', 'getconf', 'readelf'):
            (self.bin / name).symlink_to(stub)
        (self.root / 'home').mkdir()
        (self.root / 'runtime').mkdir()
        (self.root / 'nightly-t3').write_text('#!/bin/sh\nprintf "t3 0.0.0-nightly.20261018.1\\n"\n')
        plugin_list = self.root / 'plugins.json'
        plugin_list.write_text('{"schemaVersion": 1, "plugins": [], "retired": []}\n')
        self.config = self.root / 'etc/keyd/default.conf'
        self.env = dict(os.environ, INSTALL_FIXTURE=str(self.root), GROUNDWORK_TEST_ROOT=str(self.root),
                        GROUNDWORK_TESTING='1',
                        HYPRLAND_INSTANCE_SIGNATURE='',
                        HOME=str(self.root / 'home'),
                        XDG_CONFIG_HOME=str(self.root / 'home/.config'),
                        XDG_STATE_HOME=str(self.root / 'home/.local/state'),
                        # The served-config stamp is read here. The directory
                        # must exist so a host runtime directory is not used.
                        XDG_RUNTIME_DIR=str(self.root / 'runtime'),
                        GROUNDWORK_PLUGIN_LIST=str(plugin_list),
                        PATH=f'{self.root}/home/.local/bin:{self.bin}')
        self.env.pop('T3CODE_INSTALL_BIN_DIR', None)
        self.env.pop('GROUNDWORK_OBSIDIAN_VAULT', None)
        self.env.pop('WAYLAND_DISPLAY', None)
        # A host XDG or mise directory would point the shim checks at the real home.
        for key in ('XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME', 'MISE_DATA_DIR'):
            self.env.pop(key, None)

    def run_install(self, *args, success=True):
        result = subprocess.run(['bash', str(REPO / 'install.sh'), *args],
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def calls(self):
        log = self.root / 'calls.jsonl'
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def test_install_writes_plugin_cache_without_calling_plugin_add(self):
        self.run_install()
        cache = self.root / 'home/.local/state/groundwork/omarchy-plugins.json'
        self.assertEqual(json.loads(cache.read_text())['plugins'], [])
        self.assertFalse(any(call[:2] == ['omarchy', 'plugin'] for call in self.calls()))

    def test_saved_exclusions_and_vault_survive_a_bare_rerun(self):
        vault = self.root / 'home/Notes'
        self.env['GROUNDWORK_OBSIDIAN_VAULT'] = str(vault)
        self.run_install('--without-keyd', '--without-screen-sharing')
        self.env.pop('GROUNDWORK_OBSIDIAN_VAULT')
        (self.root / 'calls.jsonl').unlink()
        self.run_install()
        calls = self.calls()
        self.assertNotIn(['sudo', 'keyd', 'reload'], calls)
        self.assertNotIn(['systemctl', '--user', 'enable', 'groundwork-screen-share.service'], calls)
        saved = json.loads((self.root / 'home/.config/groundwork/omarchy.json').read_text())
        self.assertFalse(saved['keyd'])
        self.assertFalse(saved['screen_sharing'])
        self.assertEqual(saved['obsidian_vault'], str(vault))

    def test_routine_apply_preserves_exclusions_and_skips_t3_reinstallation(self):
        self.run_install('--without-keyd', '--without-screen-sharing')
        (self.root / 'calls.jsonl').unlink()
        result = subprocess.run(['python3', str(REPO / 'scripts/groundwork-update.py'), 'setup', str(REPO), '--update'],
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('finish vault', result.stdout)
        self.assertFalse(any(call[0] == 'curl' for call in self.calls()))
        self.assertNotIn(['sudo', 'keyd', 'reload'], self.calls())

    def run_with_controlling_terminal(self, *args, through_updater=False, command=None):
        # pty.fork gives the install a controlling terminal, which is what
        # /dev/tty requires. A captured subprocess has no terminal to answer.
        helper = r"""
import os, pty, select, sys, time
command = sys.argv[1:]
pid, master = pty.fork()
if pid == 0:
    os.execvpe(command[0], command, os.environ)
deadline = time.monotonic() + 9
chunks = []
status = None
while True:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        try:
            os.kill(pid, 15)
        except ProcessLookupError:
            pass
        raise SystemExit(124)
    ready, _, _ = select.select([master], [], [], min(0.2, remaining))
    if ready:
        try:
            data = os.read(master, 65536)
        except OSError:
            data = b""
        if data:
            chunks.append(data)
            if b'Fixture password:' in data:
                os.write(master, b'fixture-password\n')
    waited, candidate = os.waitpid(pid, os.WNOHANG)
    if waited == pid:
        status = candidate
        break
sys.stdout.buffer.write(b"".join(chunks))
if status is None or not os.WIFEXITED(status):
    raise SystemExit(124)
raise SystemExit(os.WEXITSTATUS(status))
"""
        command = command or ['bash', str(REPO / 'install.sh'), *args]
        if through_updater:
            bridge = "import os,runpy,sys;u=runpy.run_path(sys.argv[1]);u['run'](sys.argv[2:],capture=False,deadline=8);assert os.tcgetpgrp(0)==os.getpgrp();print('TTY restored')"
            command = [sys.executable, '-c', bridge, str(REPO / 'scripts/groundwork-update.py'), *command]
        return subprocess.run(
            [sys.executable, "-c", helper, *command],
            env=self.env, capture_output=True, timeout=10)

    def test_updater_hands_uncached_authorization_a_real_foreground_terminal(self):
        self.env.update(FIXTURE_SUDO_CACHED='0', FIXTURE_REQUIRE_TTY='1')
        result = self.run_with_controlling_terminal(through_updater=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(b'TTY restored', result.stdout)
        self.assertIn(['sudo', '-v'], self.calls())

    def run_update(self):
        return subprocess.run(['python3', str(REPO / 'scripts/groundwork-update.py'), 'setup', str(REPO), '--update'],
                              env=self.env, capture_output=True, text=True, timeout=10)

    def prepare_routine_update(self, *setup_args):
        self.run_install(*(setup_args or ('--without-keyd',)))
        (self.root / 'calls.jsonl').unlink()
        self.env['FIXTURE_SUDO_CACHED'] = '0'

    def test_update_with_nothing_to_change_never_calls_sudo(self):
        self.prepare_routine_update('--without-keyd', '--without-screen-sharing')
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('sudo was not used', result.stdout)
        self.assertFalse(any(call[0] == 'sudo' for call in self.calls()))

    def test_update_primes_sudo_only_when_a_privileged_change_is_needed(self):
        self.prepare_routine_update('--without-keyd', '--without-screen-sharing')
        self.env.update(FIXTURE_SUDO_CACHED='1', FIXTURE_SERVICES_DOWN='tailscaled')
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        privileged = [call for call in self.calls() if call[0] == 'sudo']
        self.assertEqual(privileged[0], ['sudo', '-n', '/usr/bin/true'])
        self.assertIn(['sudo', 'systemctl', 'enable', '--now', 'tailscaled'], privileged)
        self.assertNotIn(['sudo', '-v'], privileged)

    def test_update_without_terminal_or_cached_sudo_stops_before_changes(self):
        self.prepare_routine_update('--without-keyd', '--without-screen-sharing')
        self.env['FIXTURE_SERVICES_DOWN'] = 'tailscaled'
        result = subprocess.run(['python3', str(REPO / 'scripts/groundwork-update.py'), 'setup', str(REPO), '--update'],
                                env=self.env, capture_output=True, text=True, timeout=10, start_new_session=True)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('no terminal is available', result.stderr)
        self.assertNotIn(['sudo', 'systemctl', 'enable', '--now', 'tailscaled'], self.calls())

    def test_update_primes_sudo_at_the_foreground_terminal(self):
        self.prepare_routine_update('--without-keyd', '--without-screen-sharing')
        self.env.update(FIXTURE_REQUIRE_TTY='1', FIXTURE_SERVICES_DOWN='tailscaled')
        result = self.run_with_controlling_terminal(command=[
            'python3', str(REPO / 'scripts/groundwork-update.py'), 'setup', str(REPO), '--update'])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(b'Fixture password:', result.stdout)
        privileged = [call for call in self.calls() if call[0] == 'sudo']
        self.assertEqual(privileged[0], ['sudo', '/usr/bin/true'])
        self.assertIn(['sudo', 'systemctl', 'enable', '--now', 'tailscaled'], privileged)

    def test_screen_sharing_firewall_rule_is_added_once(self):
        self.prepare_routine_update('--without-keyd')
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(any(call[0] == 'sudo' for call in self.calls()))
        (self.root / 'home/.local/state/groundwork/ufw-screen-share-rule').unlink()
        self.env['FIXTURE_SUDO_CACHED'] = '1'
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(['sudo', 'ufw', 'allow', 'in', 'on', 'tailscale0', 'to', 'any', 'port', '5900', 'proto', 'tcp',
                       'comment', 'Groundwork screen sharing'], self.calls())

    def privileged_calls(self):
        return [call for call in self.calls() if call[0] == 'sudo']

    def test_update_with_current_keyd_and_extension_never_calls_sudo(self):
        self.prepare_routine_update('--with-keyd', '--without-screen-sharing')
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.privileged_calls(), [])

    def test_update_reapplies_drifted_keyd_config_after_priming_sudo(self):
        self.prepare_routine_update('--with-keyd', '--without-screen-sharing')
        self.config.write_text('local drift\n')
        self.env['FIXTURE_SUDO_CACHED'] = '1'
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        privileged = self.privileged_calls()
        self.assertEqual(privileged[0], ['sudo', '-n', '/usr/bin/true'])
        self.assertIn(['sudo', 'keyd', 'reload'], privileged)
        self.assertEqual(self.config.read_text(), (REPO / 'keyd/default.conf').read_text())

    def test_update_restarts_a_stopped_keyd_service_after_priming_sudo(self):
        self.prepare_routine_update('--with-keyd', '--without-screen-sharing')
        self.env.update(FIXTURE_SUDO_CACHED='1', FIXTURE_SERVICES_DOWN='keyd')
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        privileged = self.privileged_calls()
        self.assertEqual(privileged[0], ['sudo', '-n', '/usr/bin/true'])
        self.assertIn(['sudo', 'systemctl', 'enable', '--now', 'keyd'], privileged)

    def test_update_repairs_chromium_extension_drift_after_priming_sudo(self):
        self.prepare_routine_update('--without-keyd', '--without-screen-sharing')
        manifest = self.root / 'usr/share/chromium/extensions/nngceckbapebfimnlniiiahkandclblb.json'
        for drift in (lambda: manifest.chmod(0o600), lambda: manifest.write_text('{}')):
            with self.subTest(drift=drift):
                drift()
                (self.root / 'calls.jsonl').unlink(missing_ok=True)
                self.env['FIXTURE_SUDO_CACHED'] = '1'
                result = self.run_update()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                privileged = self.privileged_calls()
                self.assertEqual(privileged[0], ['sudo', '-n', '/usr/bin/true'])
                self.assertEqual(oct(manifest.stat().st_mode & 0o777), '0o644')
                self.assertEqual(manifest.read_bytes(), (REPO / 'chromium/extensions' / manifest.name).read_bytes())

    def test_update_primes_sudo_when_readelf_is_missing(self):
        self.prepare_routine_update('--without-keyd', '--without-screen-sharing')
        (self.bin / 'readelf').unlink()
        self.env['FIXTURE_SUDO_CACHED'] = '1'
        result = self.run_update()
        self.assertNotIn('sudo was not used', result.stdout)
        self.assertEqual(self.privileged_calls()[0], ['sudo', '-n', '/usr/bin/true'])

    def test_test_root_without_the_harness_guard_is_refused(self):
        del self.env['GROUNDWORK_TESTING']
        result = self.run_install(success=False)
        self.assertIn('GROUNDWORK_TEST_ROOT is a test-only variable', result.stderr)
        self.assertFalse(any(call[0] in ('sudo', 'omarchy', 'stow', 'curl') for call in self.calls()))

    def test_disabling_screen_sharing_forgets_the_firewall_rule(self):
        self.run_install('--without-keyd')
        marker = self.root / 'home/.local/state/groundwork/ufw-screen-share-rule'
        self.assertTrue(marker.is_file())
        self.run_install('--without-keyd', '--without-screen-sharing')
        self.assertFalse(marker.exists())

    def test_symlinked_firewall_rule_marker_is_refused(self):
        self.run_install('--without-keyd')
        marker = self.root / 'home/.local/state/groundwork/ufw-screen-share-rule'
        target = self.root / 'elsewhere'
        target.write_text('keep')
        marker.unlink()
        marker.symlink_to(target)
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('symbolic link', result.stderr)
        self.assertEqual(target.read_text(), 'keep')

    def test_other_checkout_ownership_is_checked_before_any_machine_mutation(self):
        self.run_install()
        settings = self.root / 'home/.config/groundwork/omarchy.json'
        saved = json.loads(settings.read_text())
        saved['source'] = str(self.root / 'another-checkout')
        settings.write_text(json.dumps(saved))
        original = settings.read_bytes()
        input_path = self.root / 'home/.config/hypr/input.lua'
        link = input_path.readlink()
        mapping = self.config.read_bytes()
        (self.root / 'calls.jsonl').unlink()
        result = self.run_install(success=False)
        self.assertIn('Another checkout owns', result.stderr)
        self.assertEqual(settings.read_bytes(), original)
        self.assertEqual(input_path.readlink(), link)
        self.assertEqual(self.config.read_bytes(), mapping)
        self.assertFalse(any(call[0] in ('sudo', 'omarchy', 'stow') for call in self.calls()))

    def test_foreign_update_command_is_preserved_before_any_machine_mutation(self):
        command = self.root / 'home/.local/bin/update-all'
        command.parent.mkdir(parents=True)
        command.write_text('user-owned updater')
        self.seed_config()
        mapping = self.config.read_bytes()
        self.run_install(success=False)
        self.assertEqual(command.read_text(), 'user-owned updater')
        self.assertEqual(self.config.read_bytes(), mapping)
        self.assertFalse(any(call[0] in ('sudo', 'omarchy', 'stow') for call in self.calls()))

    def test_setup_cannot_change_saved_choices_while_an_update_holds_the_lock(self):
        self.run_install()
        settings = self.root / 'home/.config/groundwork/omarchy.json'
        before = settings.read_bytes()
        lock_path = self.root / 'home/.local/state/groundwork/update.lock'
        (self.root / 'calls.jsonl').unlink()
        with lock_path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_install('--without-keyd', success=False)
        self.assertIn('Another Groundwork', result.stderr)
        self.assertEqual(settings.read_bytes(), before)
        self.assertFalse(any(call[0] in ('sudo', 'omarchy', 'stow') for call in self.calls()))

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
        self.assertIn(['omarchy', 'pkg', 'add', 'obsidian'], calls)
        self.assertIn(['omarchy', 'pkg', 'add', 'bitwarden-cli'], calls)
        self.assertTrue((self.root / 'bitwarden-flatpak').exists())
        self.assertTrue((self.root / 'freetube-flatpak').exists())
        self.assertIn(['flatpak', '--user', 'install', '--noninteractive', 'flathub', 'io.freetubeapp.FreeTube'], calls)
        settings = self.root / 'home/Documents/Obsidian/my_obsidian_vault/.obsidian/app.json'
        self.assertEqual(json.loads(settings.read_text()), {'vimMode': True})
        self.assertIn(['sudo', 'systemctl', 'enable', '--now', 'tailscaled'], calls)
        package = calls.index(['omarchy', 'pkg', 'add', 'keyd'])
        enable = calls.index(['sudo', 'systemctl', 'enable', '--now', 'keyd'])
        reload = calls.index(['sudo', 'keyd', 'reload'])
        self.assertLess(package, enable)
        self.assertLess(enable, reload)
        self.assertIn(['omarchy', 'pkg', 'add', 'wayvnc'], calls)
        self.assertIn(['systemctl', '--user', 'enable', 'groundwork-screen-share.service'], calls)
        self.assertIn(['sudo', 'ufw', 'allow', 'in', 'on', 'tailscale0', 'to', 'any', 'port', '5900', 'proto', 'tcp', 'comment', 'Groundwork screen sharing'], calls)

    def test_screen_sharing_opt_out_preserves_existing_local_state(self):
        directory = self.root / 'home/.config/groundwork/remote-desktop'
        directory.mkdir(parents=True)
        config = directory / 'wayvnc.conf'
        config.write_text('user owned settings\n')
        self.run_install('--without-screen-sharing')
        self.assertEqual(config.read_text(), 'user owned settings\n')
        self.assertFalse(any(call[0] == 'systemctl' for call in self.calls()))
        self.assertNotIn(['omarchy', 'pkg', 'add', 'wayvnc'], self.calls())

    def test_failed_screen_sharing_service_setup_stops_before_input_changes(self):
        self.env['FIXTURE_USER_SYSTEMCTL_STATUS'] = '1'
        self.run_install(success=False)
        self.assertFalse(any(call[0] in ('stow', 'hyprctl') for call in self.calls()))

    def test_existing_config_backup_and_idempotent_rerun(self):
        original = self.seed_config()
        self.run_install()
        self.assertEqual(self.config.with_name('default.conf.~1~').read_bytes(), original)
        self.run_install()
        self.assertFalse(self.config.with_name('default.conf.~2~').exists())
        self.assertEqual(self.calls().count(['sudo', 'keyd', 'reload']), 2)

    def test_existing_native_bitwarden_avoids_its_flatpak_and_still_installs_freetube(self):
        (self.bin / 'bitwarden').symlink_to(self.bin / 'stub')
        self.run_install()
        calls = self.calls()
        self.assertNotIn(['flatpak', '--user', 'install', '--noninteractive', 'flathub', 'com.bitwarden.desktop'], calls)
        self.assertFalse((self.root / 'bitwarden-flatpak').exists())
        self.assertIn(['flatpak', '--user', 'install', '--noninteractive', 'flathub', 'io.freetubeapp.FreeTube'], calls)
        self.assertTrue((self.root / 'freetube-flatpak').exists())
        self.assertEqual(calls.count(['omarchy', 'pkg', 'add', 'flatpak']), 1)

    def test_chromium_directories_are_readable_with_private_umask_and_on_rerun(self):
        directory = self.root / 'usr/share/chromium/extensions'
        for attempt in range(2):
            if attempt:
                directory.chmod(0o700)
                directory.parent.chmod(0o700)
            result = subprocess.run(
                ['bash', '-c', 'umask 077; exec bash "$1"', 'fixture', str(REPO / 'install.sh')],
                env=self.env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(directory.parent.stat().st_mode & 0o777, 0o755)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o755)
            manifest = directory / 'nngceckbapebfimnlniiiahkandclblb.json'
            self.assertEqual(manifest.stat().st_mode & 0o777, 0o644)

    def test_official_chromium_extension_and_other_manifests_are_preserved(self):
        directory = self.root / 'usr/share/chromium/extensions'
        directory.mkdir(parents=True)
        other = directory / 'other.json'
        other.write_text('{"keep": true}\n')
        target = directory / 'nngceckbapebfimnlniiiahkandclblb.json'
        original = '{"external_update_url": "https://old.example.invalid"}\n'
        target.write_text(original)
        self.run_install()
        self.assertEqual(json.loads(target.read_text()), {
            'external_update_url': 'https://clients2.google.com/service/update2/crx'})
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)
        self.assertEqual(target.with_name(target.name + '.~1~').read_text(), original)
        self.assertEqual(other.read_text(), '{"keep": true}\n')
        self.run_install()
        self.assertFalse(target.with_name(target.name + '.~2~').exists())

    def test_missing_chromium_skips_only_extension(self):
        (self.bin / 'chromium').unlink()
        result = self.run_install()
        self.assertIn('skipped its Bitwarden extension', result.stdout)
        self.assertFalse((self.root / 'usr/share/chromium').exists())
        self.assertTrue(self.config.exists())

    def test_extension_write_failure_stops_before_input_changes(self):
        self.env['FIXTURE_EXTENSION_WRITE_FAIL'] = '1'
        result = self.run_install(success=False)
        self.assertNotIn('extension is registered', result.stdout)
        self.assertFalse(self.config.exists())
        self.assertFalse(any(call[0] in ('stow', 'hyprctl') for call in self.calls()))

    def test_existing_flatpak_is_reused_without_install(self):
        (self.root / 'bitwarden-flatpak').touch()
        (self.root / 'freetube-flatpak').touch()
        self.run_install()
        self.assertFalse(any(call[0] == 'flatpak' and 'install' in call for call in self.calls()))
        self.assertNotIn(['omarchy', 'pkg', 'add', 'flatpak'], self.calls())

    def test_failed_bitwarden_install_stops_before_configuration(self):
        self.env['FIXTURE_FLATPAK_STATUS'] = '1'
        result = self.run_install(success=False)
        self.assertNotIn('are installed.', result.stdout)
        self.assertFalse(any(call[0] in ('stow', 'hyprctl') for call in self.calls()))
        self.assertFalse(self.config.exists())

    def test_failed_freetube_install_continues_through_stow(self):
        (self.root / 'bitwarden-flatpak').touch()
        self.env['FIXTURE_FREETUBE_FLATPAK_STATUS'] = '1'
        result = self.run_install()
        calls = self.calls()
        self.assertIn(['flatpak', '--user', 'install', '--noninteractive', 'flathub', 'io.freetubeapp.FreeTube'], calls)
        self.assertIn('FreeTube could not be installed', result.stderr)
        self.assertIn('FreeTube was not installed.', result.stdout)
        self.assertTrue(any(call[0] == 'curl' for call in calls))
        self.assertTrue(any(call[0] == 'stow' for call in calls))
        self.assertIn(['sudo', 'keyd', 'reload'], calls)
        self.assertIn(['systemctl', '--user', 'enable', 'groundwork-screen-share.service'], calls)
        self.assertTrue(self.config.exists())

    def test_missing_flatpak_package_still_stops_freetube_setup(self):
        (self.bin / 'bitwarden').symlink_to(self.bin / 'stub')
        (self.bin / 'flatpak').unlink()
        result = self.run_install(success=False)
        self.assertIn('flatpak is still unavailable', result.stderr)
        self.assertFalse(any(call[0] == 'stow' for call in self.calls()))
        self.assertFalse(self.config.exists())

    def test_update_installs_missing_freetube_once(self):
        self.prepare_routine_update('--without-keyd', '--without-screen-sharing')
        (self.root / 'freetube-flatpak').unlink()
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        install = ['flatpak', '--user', 'install', '--noninteractive', 'flathub', 'io.freetubeapp.FreeTube']
        self.assertEqual(self.calls().count(install), 1)
        self.assertNotIn(['flatpak', '--user', 'install', '--noninteractive', 'flathub', 'com.bitwarden.desktop'], self.calls())
        self.assertFalse(any(call[0] == 'sudo' for call in self.calls()))
        (self.root / 'calls.jsonl').unlink()
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(install, self.calls())

    def test_existing_obsidian_settings_and_custom_path_are_preserved(self):
        vault = self.root / 'home/Notes'
        settings = vault / '.obsidian/app.json'
        settings.parent.mkdir(parents=True)
        original = '{"vimMode": false, "attachmentFolderPath": "assets"}\n'
        settings.write_text(original)
        (vault / 'my-note.md').write_text('keep this note\n')
        self.env['GROUNDWORK_OBSIDIAN_VAULT'] = str(vault)
        self.run_install()
        self.assertEqual(settings.read_text(), original)
        self.assertEqual((vault / 'my-note.md').read_text(), 'keep this note\n')
        self.assertFalse((self.root / 'home/Documents').exists())

    def test_obsidian_symlink_destination_is_rejected(self):
        vault = self.root / 'home/Notes'
        outside = self.root / 'outside'
        outside.mkdir()
        vault.symlink_to(outside, target_is_directory=True)
        self.env['GROUNDWORK_OBSIDIAN_VAULT'] = str(vault)
        result = self.run_install(success=False)
        self.assertIn('Refusing a symlink', result.stderr)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse(self.config.exists())

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
                              (('--with-blip', '--without-blip'), False),
                              (('--with-local-inference', '--without-local-inference'), False),
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

    def cursor_argv(self):
        return self.root / 'home/.config/Cursor/argv.json'

    def test_existing_cursor_keeps_its_command_and_gains_a_password_store(self):
        (self.bin / 'omarchy-install-cursor').symlink_to(self.bin / 'stub')
        argv = self.cursor_argv()
        argv.parent.mkdir(parents=True)
        argv.write_text('{\n  "disable-hardware-acceleration": true\n}\n')
        self.run_install()
        calls = self.calls()
        self.assertNotIn(['omarchy', 'pkg', 'add', 'cursor-bin'], calls)
        self.assertNotIn(['omarchy-install-cursor'], calls)
        self.assertFalse(any(call[0] == 'mise' for call in calls))
        saved = json.loads(argv.read_text())
        self.assertEqual(saved['password-store'], 'gnome-libsecret')
        self.assertTrue(saved['disable-hardware-acceleration'])

    def test_missing_cursor_uses_omarchy_installer(self):
        (self.bin / 'cursor').unlink()
        (self.bin / 'omarchy-install-cursor').symlink_to(self.bin / 'stub')
        self.run_install()
        self.assertIn(['omarchy-install-cursor'], self.calls())
        self.assertNotIn(['omarchy', 'pkg', 'add', 'cursor-bin'], self.calls())
        self.assertEqual(json.loads(self.cursor_argv().read_text())['password-store'], 'gnome-libsecret')

    def test_missing_cursor_without_omarchy_installer_stops(self):
        (self.bin / 'cursor').unlink()
        result = self.run_install(success=False)
        self.assertIn('omarchy-install-cursor', result.stderr)
        self.assertNotIn(['omarchy', 'pkg', 'add', 'cursor-bin'], self.calls())
        self.assertFalse(self.cursor_argv().exists())
        self.assertFalse(any(call[0] in ('stow', 'hyprctl') for call in self.calls()))

    def test_cursor_installer_without_a_command_stops(self):
        (self.bin / 'cursor').unlink()
        (self.bin / 'omarchy-install-cursor').symlink_to(self.bin / 'stub')
        self.env['FIXTURE_CURSOR_INSTALL_SKIP_BIN'] = '1'
        result = self.run_install(success=False)
        self.assertIn('did not provide a cursor command', result.stderr)
        self.assertFalse(any(call[0] in ('stow', 'hyprctl') for call in self.calls()))

    def test_existing_cursor_password_store_is_preserved(self):
        argv = self.cursor_argv()
        argv.parent.mkdir(parents=True)
        original = '{"password-store": "basic", "enable-crash-reporter": false}\n'
        argv.write_text(original)
        self.run_install()
        self.assertEqual(argv.read_text(), original)

    def test_commented_cursor_argv_gains_a_password_store(self):
        argv = self.cursor_argv()
        argv.parent.mkdir(parents=True)
        argv.write_text('// Cursor keeps comments in this file.\n{\n  "disable-hardware-acceleration": true\n}\n')
        self.run_install()
        saved = json.loads(self.cursor_argv().read_text())
        self.assertEqual(saved['password-store'], 'gnome-libsecret')
        self.assertTrue(saved['disable-hardware-acceleration'])

    def test_commented_cursor_argv_with_a_password_store_is_preserved(self):
        argv = self.cursor_argv()
        argv.parent.mkdir(parents=True)
        original = '// leave this comment\n{"password-store": "gnome-libsecret"}\n'
        argv.write_text(original)
        self.run_install()
        self.assertEqual(argv.read_text(), original)

    def test_invalid_cursor_argv_stops_without_rewriting_it(self):
        argv = self.cursor_argv()
        argv.parent.mkdir(parents=True)
        argv.write_text('{ not json\n')
        result = self.run_install(success=False)
        self.assertIn('left unchanged', result.stderr)
        self.assertEqual(argv.read_text(), '{ not json\n')
        self.assertFalse(any(call[0] in ('stow', 'hyprctl') for call in self.calls()))

    def test_symlinked_cursor_argv_is_refused(self):
        argv = self.cursor_argv()
        argv.parent.mkdir(parents=True)
        target = self.root / 'elsewhere.json'
        target.write_text('{}\n')
        argv.symlink_to(target)
        result = self.run_install(success=False)
        self.assertIn('not a regular file', result.stderr)
        self.assertEqual(target.read_text(), '{}\n')

    def test_update_does_not_reinstall_an_existing_cursor(self):
        (self.bin / 'omarchy-install-cursor').symlink_to(self.bin / 'stub')
        self.run_install('--without-keyd', '--without-screen-sharing')
        (self.root / 'calls.jsonl').unlink()
        result = subprocess.run(['python3', str(REPO / 'scripts/groundwork-update.py'), 'setup', str(REPO), '--update'],
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(['omarchy-install-cursor'], self.calls())
        self.assertNotIn(['omarchy', 'pkg', 'add', 'cursor-bin'], self.calls())

    def test_missing_agent_uses_omarchy_mise_without_user_configuration(self):
        (self.bin / 'grok').unlink()
        self.run_install()
        self.assertIn(['mise', 'install', 'grok'], self.calls())
        self.assertFalse((self.root / 'home/.config/mise/config.toml').exists())

    def test_failed_agent_install_stops_before_hyprland_changes(self):
        (self.bin / 'grok').unlink()
        self.env['FIXTURE_MISE_FAILURE'] = '1'
        self.run_install(success=False)
        self.assertNotIn(['hyprctl', 'reload'], self.calls())

    def seed_missing_mise_provider(self, kind='shims'):
        (self.bin / 'opencode').unlink()
        data = self.root / 'mise'
        shim = data / kind / 'opencode'
        shim.parent.mkdir(parents=True)
        shim.symlink_to(self.bin / 'stub')
        self.env['MISE_DATA_DIR'] = str(data)
        self.env['PATH'] = str(shim.parent) + ':' + self.env['PATH']

    def test_existing_mise_dispatcher_with_missing_tool_is_repaired(self):
        self.seed_missing_mise_provider()
        self.run_install()
        self.assertIn(['omarchy-mise-default-tools', 'declares', 'opencode'], self.calls())
        self.assertIn(['mise', 'install', 'opencode'], self.calls())
        self.assertEqual(self.calls().count(['mise', 'which', 'opencode']), 2)

    def test_mise_wrapper_still_missing_after_successful_install_fails(self):
        self.seed_missing_mise_provider('command-wrappers/bin')
        self.env['FIXTURE_MISE_UNRESOLVED'] = '1'
        result = self.run_install(success=False)
        self.assertIn('opencode is still unavailable', result.stderr)
        self.assertFalse(any(call[0] in ('stow', 'hyprctl') for call in self.calls()))

    def test_opted_out_mise_defaults_are_not_reenabled(self):
        self.seed_missing_mise_provider()
        self.env['FIXTURE_MISE_OPTED_OUT'] = '1'
        result = self.run_install(success=False)
        self.assertIn('not an enabled Omarchy default tool', result.stderr)
        self.assertFalse(any(call[:2] == ['mise', 'install'] for call in self.calls()))
        self.assertFalse((self.root / 'home/.config/mise/config.toml').exists())

    def test_no_terminal_stops_before_any_install(self):
        self.env['FIXTURE_SUDO_CACHED'] = '0'
        result = subprocess.run(
            ['bash', str(REPO / 'install.sh')],
            env=self.env, capture_output=True, text=True, timeout=10,
            start_new_session=True)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('no terminal is available', result.stderr)
        self.assertFalse(any(call[:3] == ['omarchy', 'pkg', 'add'] for call in self.calls()))

    def test_terminal_asks_once_before_privileged_steps(self):
        self.env['FIXTURE_SUDO_CACHED'] = '0'
        result = self.run_with_controlling_terminal()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls()
        self.assertLess(
            calls.index(['sudo', '-v']),
            calls.index(['sudo', 'systemctl', 'enable', '--now', 'tailscaled']))
        self.assertIn(['omarchy', 'pkg', 'add', 'herdr'], calls)

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

    def test_small_pages_install_the_newest_nightly(self):
        self.run_install()
        self.assertEqual((self.root / 't3-version').read_text(), '')

    def test_large_pages_pin_the_last_known_good_nightly(self):
        self.env.update(FIXTURE_PAGESIZE='16384', FIXTURE_PINNED_T3='0.1.2-nightly.20261021.3499',
                        T3CODE_VERSION='ambient-fixture')
        result = self.run_install()
        self.assertEqual((self.root / 't3-version').read_text(), '0.1.2-nightly.20261021.3499')
        self.assertEqual((self.root / 't3-channel').read_text(), 'nightly')
        self.assertIn('pingdotgg/t3code#23433', result.stdout)

    def test_large_pages_keep_a_working_nightly_on_rerun(self):
        self.env.update(FIXTURE_PAGESIZE='16384', FIXTURE_PINNED_T3='0.1.2-nightly.20261021.3499')
        self.run_install()
        (self.root / 'calls.jsonl').unlink()
        (self.root / 't3-version').unlink()
        (self.bin / 't3').unlink()
        result = self.run_install()
        self.assertFalse(any(call[0] == 'curl' for call in self.calls()))
        self.assertFalse((self.root / 't3-version').exists())
        self.assertIn('Keeping the installed T3 Code Nightly on this 16 KiB-page kernel', result.stdout)

    def test_readelf_comes_from_binutils(self):
        (self.bin / 'readelf').unlink()
        result = self.run_install(success=False)
        self.assertIn(['omarchy', 'pkg', 'add', 'binutils'], self.calls())
        self.assertIn('binutils is still unavailable', result.stderr)

    def test_nightly_installer_failure_stops_before_home_changes(self):
        self.env['FIXTURE_INSTALL_STATUS'] = '1'
        self.run_install(success=False)
        self.assertFalse(any(call[0] in ('stow', 'hyprctl') for call in self.calls()))

    def test_package_helper_successful_skip_fails_before_configuration(self):
        for package, executable in (('herdr', 'herdr'), ('tailscale', 'tailscale'),
                                    ('obsidian', 'obsidian'), ('bitwarden-cli', 'bw'),
                                    ('flatpak', 'flatpak')):
            with self.subTest(package=package):
                (self.bin / executable).unlink()
                result = self.run_install(success=False)
                self.assertIn(f'{package} is still unavailable', result.stderr)
                self.assertNotIn('are installed.', result.stdout)
                self.assertFalse(any(call[0] in ('curl', 'stow', 'hyprctl') for call in self.calls()))
                (self.bin / executable).symlink_to(self.bin / 'stub')

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

    def seed_blip_commands(self, *, socat=True, bun=False):
        for name in ('jq', 'ssh', 'notify-send', 'wl-copy', 'xdg-open'):
            target = self.bin / name
            if not target.exists():
                target.symlink_to(self.bin / 'stub')
        socat_path = self.bin / 'socat'
        if socat and not socat_path.exists():
            socat_path.symlink_to(self.bin / 'stub')
        elif not socat and socat_path.is_symlink():
            socat_path.unlink()
        bun_path = self.bin / 'bun'
        if bun and not bun_path.exists():
            bun_path.symlink_to(self.bin / 'stub')

    def prepare_blip(self, *, align='0x10000', plugins='[]', session=True, socat=True, bun=False):
        self.seed_blip_commands(socat=socat, bun=bun)
        self.env['FIXTURE_LOAD_ALIGN'] = align
        self.env['FIXTURE_PLUGIN_LIST'] = plugins
        self.env['HYPRLAND_INSTANCE_SIGNATURE'] = 'fixture-session' if session else ''

    def blip_mentions(self, calls):
        needles = ('bun', 'socat', 'nixfred', 'blip', 'omarchy-install-dev-env', 'jq', 'openssh',
                   'libnotify', 'wl-clipboard', 'xdg-utils', 'notify-send', 'wl-copy', 'xdg-open', 'ffmpeg', 'mpv')
        # Whole words only: a random mktemp name such as tmp.jq2MOwqXUk is not jq.
        def words(arg):
            return set(re.split(r'[^A-Za-z0-9-]+', arg)) | set(re.split(r'[^A-Za-z0-9]+', arg))
        return [call for call in calls if any(needle in words(arg) for arg in call for needle in needles)]

    def test_default_install_leaves_blip_off_and_an_existing_copy_alone(self):
        self.env['FIXTURE_PLUGIN_LIST'] = '[{"id": "nixfred.blip", "enabled": true}]'
        result = self.run_install()
        saved = json.loads((self.root / 'home/.config/groundwork/omarchy.json').read_text())
        self.assertFalse(saved['blip'])
        self.assertEqual(self.blip_mentions(self.calls()), [])
        self.assertFalse((self.root / 'home/.config/omarchy/shell.json').exists())
        self.assertNotIn('blip-setup', result.stdout)
        self.run_install('--without-blip')
        saved = json.loads((self.root / 'home/.config/groundwork/omarchy.json').read_text())
        self.assertFalse(saved['blip'])
        self.assertFalse(any(call[:3] == ['omarchy', 'plugin', 'remove'] for call in self.calls()))

    def test_blip_preference_is_stored_and_reused_without_readding(self):
        self.prepare_blip()
        result = self.run_install('--with-blip')
        saved = json.loads((self.root / 'home/.config/groundwork/omarchy.json').read_text())
        self.assertTrue(saved['blip'])
        calls = self.calls()
        self.assertIn(['omarchy', 'plugin', 'add', 'https://github.com/nixfred/blip.git', '--enable', '--yes'], calls)
        self.assertIn(['omarchy-install-dev-env', 'bun'], calls)
        self.assertIn('blip-setup GATEWAY_USER@GATEWAY_MAGICDNS_NAME', result.stdout)
        self.assertFalse((self.root / 'home/.config/omarchy/shell.json').exists())
        bun_curls = [call for call in calls if call[0] == 'curl' and any('oven-sh/bun' in arg for arg in call)]
        self.assertEqual(
            [next(arg for arg in call if arg.startswith('https://')) for call in bun_curls],
            ['https://github.com/oven-sh/bun/releases/download/bun-v1.2.23/SHASUMS256.txt',
             'https://github.com/oven-sh/bun/releases/download/bun-v1.2.23/bun-linux-aarch64.zip'])
        for call in bun_curls:
            self.assertIn('--proto', call)
            self.assertIn('=https', call)
            self.assertIn('--proto-redir', call)
        self.assertFalse(any('bun.sh' in arg for call in calls for arg in call))
        readelfs = [index for index, call in enumerate(calls) if call[:2] == ['readelf', '-lW']]
        probe_at = calls.index(['bun', '-v'])
        version_at = calls.index(['bun', '--version'])
        install_at = calls.index(['omarchy-install-dev-env', 'bun'])
        self.assertLess(readelfs[0], install_at)
        self.assertLess(install_at, probe_at)
        self.assertLess(probe_at, version_at)
        self.assertTrue(any(index > probe_at and index < version_at for index in readelfs))
        self.assertNotIn(str(self.bin / 'bun'), calls[readelfs[0]][2])
        self.assertIn(str(self.root / 'mise-installed' / 'bun'), [calls[index][2] for index in readelfs])
        self.env['FIXTURE_PLUGIN_LIST'] = '[{"id": "nixfred.blip", "enabled": true}]'
        (self.root / 'calls.jsonl').unlink()
        self.run_install()
        saved = json.loads((self.root / 'home/.config/groundwork/omarchy.json').read_text())
        self.assertTrue(saved['blip'])
        calls = self.calls()
        self.assertIn(['omarchy', 'plugin', 'list', '--json'], calls)
        self.assertFalse(any(call[:3] == ['omarchy', 'plugin', 'add'] for call in calls))
        self.assertNotIn(['omarchy-install-dev-env', 'bun'], calls)
        self.assertFalse(any(call[0] == 'curl' and any('oven-sh/bun' in arg for arg in call) for call in calls))

    def test_blip_add_is_skipped_when_the_plugin_is_already_listed(self):
        self.prepare_blip(plugins='[{"id": "nixfred.blip", "enabled": false}]', bun=True)
        result = self.run_install('--with-blip')
        self.assertIn('already installed', result.stdout)
        self.assertFalse(any(call[:3] == ['omarchy', 'plugin', 'add'] for call in self.calls()))
        self.assertFalse(any(call[:3] == ['omarchy', 'plugin', 'enable'] for call in self.calls()))
        self.assertNotIn(['omarchy-install-dev-env', 'bun'], self.calls())

    def test_blip_installs_only_missing_packages(self):
        self.prepare_blip(socat=False, bun=True)
        self.env['FIXTURE_MATERIALIZE_ADDED'] = '1'
        self.run_install('--with-blip')
        calls = self.calls()
        self.assertIn(['omarchy', 'pkg', 'add', 'socat'], calls)
        for package in ('jq', 'openssh', 'libnotify', 'wl-clipboard', 'xdg-utils', 'ffmpeg', 'mpv'):
            self.assertNotIn(['omarchy', 'pkg', 'add', package], calls)

    def test_blip_is_not_enabled_when_bun_fails_the_alignment_check(self):
        self.prepare_blip(align='0x100')
        result = self.run_install('--with-blip', success=False)
        calls = self.calls()
        self.assertIn('oven-sh/bun#17627', result.stderr)
        self.assertIn('https://github.com/oven-sh/bun/issues/17627', result.stderr)
        self.assertIn('16 KiB', result.stderr + result.stdout) if os.sysconf('SC_PAGESIZE') == 16384 else self.assertIn('KiB pages', result.stderr)
        self.assertFalse(any(call[:3] == ['omarchy', 'plugin', 'add'] for call in calls))
        self.assertNotIn(['bun', '--version'], calls)
        self.assertNotIn(['omarchy-install-dev-env', 'bun'], calls)
        check_at = next(index for index, call in enumerate(calls) if call[:2] == ['readelf', '-lW'])
        probe_at = next((index for index, call in enumerate(calls) if call == ['bun', '-v']), None)
        # The release ELF is refused before mise's install probe can run `bun -v`.
        self.assertIsNone(probe_at)
        self.assertLess(check_at, probe_at if probe_at is not None else len(calls))
        self.assertNotIn(str(self.bin / 'bun'), calls[check_at][2])
        self.assertNotIn('are installed.', result.stdout)
        self.assertFalse((self.root / 'home/.config/groundwork/omarchy.json').exists())

    def test_blip_does_not_install_bun_when_the_release_checksum_fails(self):
        self.prepare_blip()
        self.env['FIXTURE_BUN_BAD_CHECKSUM'] = '1'
        result = self.run_install('--with-blip', success=False)
        self.assertIn('Checksum mismatch', result.stderr)
        self.assertNotIn(['omarchy-install-dev-env', 'bun'], self.calls())
        self.assertNotIn(['bun', '-v'], self.calls())
        self.assertNotIn(['bun', '--version'], self.calls())

    def test_blip_refuses_an_unexpected_mise_bun_version_before_download(self):
        self.prepare_blip()
        self.env['FIXTURE_BUN_LATEST'] = '../evil'
        result = self.run_install('--with-blip', success=False)
        calls = self.calls()
        self.assertIn('unexpected version', result.stderr)
        self.assertNotIn(['omarchy-install-dev-env', 'bun'], calls)
        self.assertFalse(any(call[0] == 'curl' and any('bun' in arg for arg in call) for call in calls))

    def test_blip_removes_bun_when_the_installed_version_was_not_checked(self):
        self.prepare_blip()
        self.env['FIXTURE_BUN_CURRENT'] = '9.9.9'
        result = self.run_install('--with-blip', success=False)
        calls = self.calls()
        self.assertIn('9.9.9', result.stderr)
        self.assertIn('1.2.23', result.stderr)
        self.assertIn(['omarchy-remove-dev-env', 'bun'], calls)
        self.assertNotIn(['bun', '--version'], calls)
        probe_at = calls.index(['bun', '-v'])
        remove_at = calls.index(['omarchy-remove-dev-env', 'bun'])
        readelfs = [index for index, call in enumerate(calls) if call[:2] == ['readelf', '-lW']]
        self.assertLess(readelfs[0], probe_at)
        self.assertTrue(any(index > probe_at and index < remove_at for index in readelfs))
        self.assertFalse((self.root / 'home/.config/groundwork/omarchy.json').exists())

    def test_blip_reads_the_installed_version_from_the_mise_path(self):
        self.prepare_blip()
        installed = self.root / 'mise/installs/bun/9.9.9/bin/bun'
        self.env['FIXTURE_BUN_WHICH'] = str(installed)
        result = self.run_install('--with-blip', success=False)
        calls = self.calls()
        self.assertIn('9.9.9', result.stderr)
        self.assertIn(['omarchy-remove-dev-env', 'bun'], calls)
        self.assertNotIn(['mise', 'current', 'bun'], calls)
        self.assertNotIn(['bun', '--version'], calls)
        self.assertIn(str(installed), [call[2] for call in calls if call[:2] == ['readelf', '-lW']])

    def test_blip_is_not_enabled_when_bun_version_fails(self):
        self.prepare_blip()
        self.env['FIXTURE_BUN_STATUS'] = '1'
        result = self.run_install('--with-blip', success=False)
        self.assertIn(['bun', '--version'], self.calls())
        self.assertIn('oven-sh/bun#17627', result.stderr)
        self.assertFalse(any(call[:3] == ['omarchy', 'plugin', 'add'] for call in self.calls()))

    def test_blip_outside_a_desktop_session_prints_the_command_and_does_not_add(self):
        self.prepare_blip(session=False)
        result = self.run_install('--with-blip')
        self.assertIn('omarchy plugin add https://github.com/nixfred/blip.git --enable --yes', result.stdout)
        self.assertIn('blip-setup GATEWAY_USER@GATEWAY_MAGICDNS_NAME', result.stdout)
        self.assertFalse(any(call[:2] == ['omarchy', 'plugin'] for call in self.calls()))
        saved = json.loads((self.root / 'home/.config/groundwork/omarchy.json').read_text())
        self.assertTrue(saved['blip'])

    def test_blip_refuses_unusable_plugin_list_json(self):
        self.prepare_blip(plugins='not-json')
        result = self.run_install('--with-blip', success=False)
        self.assertIn('could not be read', result.stderr)
        self.assertFalse(any(call[:3] == ['omarchy', 'plugin', 'add'] for call in self.calls()))

    def test_blip_stops_when_plugin_list_fails(self):
        self.prepare_blip()
        self.env['FIXTURE_PLUGIN_LIST_STATUS'] = '1'
        self.run_install('--with-blip', success=False)
        self.assertFalse(any(call[:3] == ['omarchy', 'plugin', 'add'] for call in self.calls()))

    def test_blip_checks_the_resolved_bun_binary_not_the_mise_shim(self):
        shim_dir = self.root / 'home/.local/share/mise/shims'
        shim_dir.mkdir(parents=True)
        (shim_dir / 'bun').symlink_to(self.bin / 'stub')
        resolved = self.root / 'mise-installed/bun'
        resolved.parent.mkdir()
        resolved.write_text('#!/bin/sh\nexit 0\n')
        resolved.chmod(0o755)
        self.env['PATH'] = f'{shim_dir}:{self.env["PATH"]}'
        self.prepare_blip(bun=True)
        self.run_install('--with-blip')
        readelf_calls = [call for call in self.calls() if call[:2] == ['readelf', '-lW']]
        self.assertIn(str(resolved), [call[2] for call in readelf_calls])
        self.assertFalse(any('shims' in call[2] for call in readelf_calls))
        self.assertFalse(any('oven-sh/bun' in arg for call in self.calls() for arg in call))

    def test_blip_stops_when_the_mise_shim_cannot_be_resolved(self):
        shim_dir = self.root / 'home/.local/share/mise/shims'
        shim_dir.mkdir(parents=True)
        (shim_dir / 'bun').symlink_to(self.bin / 'stub')
        self.env['PATH'] = f'{shim_dir}:{self.env["PATH"]}'
        self.prepare_blip()
        result = self.run_install('--with-blip', success=False)
        self.assertIn('could not resolve', result.stderr)
        self.assertFalse(any(call[:3] == ['omarchy', 'plugin', 'add'] for call in self.calls()))

    def test_blip_update_asks_for_administrator_permission_when_a_package_is_missing(self):
        self.prepare_blip(bun=True)
        self.run_install('--with-blip')
        (self.bin / 'socat').unlink()
        (self.root / 'calls.jsonl').unlink()
        self.env['FIXTURE_SUDO_CACHED'] = '0'
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('no terminal is available', result.stderr)
        self.assertNotIn(['omarchy', 'pkg', 'add', 'socat'], self.calls())

    def test_help_mentions_blip(self):
        result = self.run_install('--help')
        self.assertIn('--with-blip', result.stdout)
        self.assertIn('--without-blip', result.stdout)
        self.assertIn('--with-local-inference', result.stdout)
        self.assertIn('--without-local-inference', result.stdout)

    def seed_honeykrisp_icd(self):
        icd = self.root / 'usr/share/vulkan/icd.d'
        icd.mkdir(parents=True)
        (icd / 'asahi_icd.json').write_text('{"ICD": {"library_path": "libvulkan_asahi.so"}}\n')

    def test_default_install_does_not_add_ollama(self):
        self.run_install()
        self.assertFalse(any(call[:3] == ['omarchy', 'pkg', 'add'] and call[3] in ('ollama', 'ollama-vulkan', 'vulkan-tools')
                             for call in self.calls()))
        saved = json.loads((self.root / 'home/.config/groundwork/omarchy.json').read_text())
        self.assertFalse(saved['local_inference'])
        self.assertNotIn(['systemctl', 'enable', 'ollama.service'], self.calls())

    def test_local_inference_requires_a_regular_asahi_icd(self):
        icd = self.root / 'usr/share/vulkan/icd.d'
        icd.mkdir(parents=True)
        link = icd / 'asahi_icd.json'
        link.symlink_to('/tmp/private-icd.json')
        result = self.run_install('--with-local-inference', success=False)
        self.assertIn('No Asahi Vulkan ICD', result.stderr)
        self.assertFalse(any(call[:4] == ['omarchy', 'pkg', 'add', 'ollama'] for call in self.calls()))
        self.assertFalse((self.root / 'home/.config/groundwork/omarchy.json').exists())

    def test_local_inference_installs_both_packages_and_skips_the_service(self):
        self.seed_honeykrisp_icd()
        self.env['FIXTURE_LOAD_ALIGN'] = '0x10000'
        result = self.run_install('--with-local-inference')
        self.assertIn('no completed M1 GPU run', result.stdout)
        self.assertIn('verify-local-inference.py run', result.stdout)
        calls = self.calls()
        self.assertIn(['omarchy', 'pkg', 'add', 'ollama'], calls)
        self.assertIn(['omarchy', 'pkg', 'add', 'ollama-vulkan'], calls)
        self.assertIn(['omarchy', 'pkg', 'add', 'vulkan-tools'], calls)
        self.assertLess(calls.index(['omarchy', 'pkg', 'add', 'ollama']),
                        calls.index(['omarchy', 'pkg', 'add', 'ollama-vulkan']))
        self.assertFalse(any(call[:1] == ['systemctl'] and 'ollama' in call for call in calls))
        self.assertFalse(any('huggingface.co' in arg for call in calls for arg in call))
        saved = json.loads((self.root / 'home/.config/groundwork/omarchy.json').read_text())
        self.assertTrue(saved['local_inference'])
        (self.root / 'calls.jsonl').unlink()
        update = self.run_update()
        self.assertEqual(update.returncode, 0, update.stdout + update.stderr)
        self.assertNotIn(['omarchy', 'pkg', 'add', 'ollama'], self.calls())
        self.assertNotIn(['omarchy', 'pkg', 'add', 'ollama-vulkan'], self.calls())

    def test_misaligned_ollama_does_not_save_the_preference(self):
        self.seed_honeykrisp_icd()
        result = self.run_install('--with-local-inference', success=False)
        self.assertIn('not aligned', result.stderr)
        self.assertIn(['omarchy', 'pkg', 'add', 'ollama-vulkan'], self.calls())
        self.assertFalse((self.root / 'home/.config/groundwork/omarchy.json').exists())

    def test_without_local_inference_records_off_and_adds_nothing(self):
        self.seed_honeykrisp_icd()
        self.run_install('--without-local-inference')
        self.assertFalse(any(call[:3] == ['omarchy', 'pkg', 'add'] and call[3] in ('ollama', 'ollama-vulkan', 'vulkan-tools')
                             for call in self.calls()))
        saved = json.loads((self.root / 'home/.config/groundwork/omarchy.json').read_text())
        self.assertFalse(saved['local_inference'])


if __name__ == '__main__':
    unittest.main()
