#!/usr/bin/env python3
"""Own the local WayVNC service; never listen outside the active Tailscale IP."""
import argparse
import errno
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time


UNIT = '''[Unit]
Description=Groundwork screen sharing over Tailscale
# Start after the graphical session so WayVNC has a Wayland display.
After=graphical-session.target
# Stop the listener when the graphical session ends.
PartOf=graphical-session.target

[Service]
ExecStart="%h/.local/bin/groundwork-screen-share" serve
# Bring the listener back after a crash.
Restart=on-failure
# Avoid a hot loop while bind_listener waits out TIME_WAIT.
RestartSec=10
# Match stop_child's two 5s waits so a stuck WayVNC cannot hold restart past configure's 20s timeout.
TimeoutStopSec=10
# Keep the control socket private.
UMask=0077

[Install]
WantedBy=graphical-session.target
'''


def private_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('Screen-sharing state must be an owned, regular directory.')
    path.chmod(0o700)


def replace_file(path, content, mode):
    if path.is_symlink():
        raise RuntimeError('Refusing to replace a symlink: ' + str(path))
    if path.exists() and path.read_bytes() == content:
        path.chmod(mode)
        return False
    if path.exists():
        number = 1
        while path.with_name(path.name + '.bak.' + str(number)).exists():
            number += 1
        path.rename(path.with_name(path.name + '.bak.' + str(number)))
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            os.fchmod(output.fileno(), mode)
            output.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def config_path():
    # A stable home-relative location is shared by setup and the user manager.
    return Path.home() / '.config/groundwork/remote-desktop/wayvnc.conf'


def config_text(password):
    # Apple Screen Sharing requires legacy VNC auth. Tailscale supplies encryption.
    return ('enable_auth=true\nrelax_encryption=true\nallow_broken_crypto=true\n'
            'password=' + password + '\n')


def read_config_bytes(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise RuntimeError('Refusing unsafe screen-sharing credentials.')
        os.fchmod(source.fileno(), 0o600)
        data = source.read(4096)
    try:
        text = data.decode()
    except UnicodeDecodeError:
        text = ''
    match = re.search(r'^password=([A-Za-z0-9_-]{8})$', text, re.MULTILINE)
    if not match or text != config_text(match[1]):
        raise RuntimeError('Preserved modified screen-sharing config; reconcile it locally before setup.')
    return data


def read_config(path):
    match = re.search(r'^password=([A-Za-z0-9_-]{8})$', read_config_bytes(path).decode(), re.MULTILINE)
    return match[1]


def user_runtime_root():
    # The user manager sets XDG_RUNTIME_DIR to this path. SSH and cron often
    # leave the variable unset while the desktop service still stores its stamp here.
    return Path('/run/user') / str(os.getuid())


def directory_writable(path):
    # A missing directory is not this path. Walking to a writable ancestor
    # would hide the runtime directory the desktop service actually uses.
    try:
        info = path.lstat()
    except OSError:
        return False
    return (not stat.S_ISLNK(info.st_mode) and stat.S_ISDIR(info.st_mode)
            and os.access(path, os.W_OK | os.X_OK))


def runtime_state_dir():
    # Prefer the variable this process inherited, then the directory the user
    # service uses, so a stamp written at login is visible to a later apply.
    candidates = []
    root = os.environ.get('XDG_RUNTIME_DIR')
    if root:
        candidates.append(Path(root))
    candidates.append(user_runtime_root())
    for candidate in candidates:
        if directory_writable(candidate):
            return candidate / 'groundwork-screen-share'
    return None


def config_stamp_path():
    directory = runtime_state_dir()
    if directory is None:
        return None
    return directory / 'config.sha256'


def remember_served_config(data):
    # The running WayVNC keeps the password it was started with. Record the
    # bytes this process actually read so a later edit can restart it.
    stamp = config_stamp_path()
    if stamp is None:
        return
    private_directory(stamp.parent)
    replace_file(stamp, (hashlib.sha256(data).hexdigest() + '\n').encode(), 0o600)


def served_config_changed(path):
    stamp = config_stamp_path()
    if stamp is None:
        return False
    try:
        fd = os.open(stamp, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return False
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            return False
        recorded = source.read(80).decode().strip()
    return recorded != hashlib.sha256(read_config_bytes(path)).hexdigest()


def restart_marker_path():
    # This file outlives the runtime directory. A record written before the
    # user bus is up must still be visible after login.
    return Path.home() / '.local/state/groundwork/screen-share-restart-needed'


def restart_is_pending():
    path = restart_marker_path()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return False
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        return stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()


def mark_restart_needed():
    path = restart_marker_path()
    private_directory(path.parent)
    replace_file(path, b'pending\n', 0o600)


def clear_restart_marker():
    path = restart_marker_path()
    if path.is_symlink() or not path.is_file():
        return
    path.unlink()


def finish_restart_record(path):
    # Stamp before clearing so a process that has not bound yet does not look
    # like a new edit. serve() replaces the stamp with the bytes it passes to WayVNC.
    try:
        remember_served_config(read_config_bytes(path))
    except (OSError, RuntimeError):
        pass
    clear_restart_marker()


def service_active_state():
    # is-active exits 3 for inactive, failed, activating, and deactivating.
    # ActiveState is the distinction. Any other result leaves the record.
    try:
        result = subprocess.run(
            ['systemctl', 'show', '--user', '-p', 'ActiveState', '--value',
             'groundwork-screen-share.service'],
            check=False, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or '').strip()


def configure():
    path = config_path()
    private_directory(path.parent)
    # A failed restart leaves the new helper and unit in place. The marker
    # is the only record that the running process still has the old ones.
    changed = restart_is_pending()
    if path.exists() or path.is_symlink():
        read_config(path)
        # A valid hand edit, such as a new password, is still the file WayVNC
        # has not loaded. The serve stamp is the bytes the running process read.
        if served_config_changed(path):
            changed = True
    else:
        # DES uses only eight characters. Never reuse an account password here.
        replace_file(path, config_text(secrets.token_urlsafe(6)).encode(), 0o600)
        changed = True
    binary = Path.home() / '.local/bin/groundwork-screen-share'
    binary.parent.mkdir(parents=True, exist_ok=True)
    if replace_file(binary, Path(__file__).read_bytes(), 0o755):
        changed = True
    unit = Path.home() / '.config/systemd/user/groundwork-screen-share.service'
    unit.parent.mkdir(parents=True, exist_ok=True)
    if replace_file(unit, UNIT.encode(), 0o644):
        changed = True
    # Persist this before daemon-reload. An SSH apply has no WAYLAND_DISPLAY,
    # and a reload or interrupt must not leave the new files without a record.
    if changed:
        try:
            mark_restart_needed()
        except (OSError, RuntimeError):
            # The state directory can fail to be created. A service that is
            # already running still needs the restart below.
            pass
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True, timeout=20)
    subprocess.run(['systemctl', '--user', 'enable', 'groundwork-screen-share.service'], check=True, timeout=20)
    # The running service keeps the session when its unit, helper, and
    # WayVNC config are the same bytes. Restarting here is what drops it.
    if changed:
        if os.environ.get('WAYLAND_DISPLAY'):
            action = 'restart'
        else:
            state = service_active_state()
            if state in ('active', 'reloading', 'failed'):
                action = 'restart'
            elif state == 'inactive':
                action = 'clear'
            else:
                # activating, deactivating, or a systemctl error.
                action = 'keep'
        if action == 'restart':
            print('Restarting screen sharing; an active remote session will drop.')
            subprocess.run(['systemctl', '--user', 'restart', 'groundwork-screen-share.service'],
                           check=True, timeout=20)
            finish_restart_record(path)
        elif action == 'clear':
            finish_restart_record(path)
    print('Screen sharing enabled for desktop login; it waits for Tailscale sign-in.')
    print('Show its password in a local terminal: groundwork-screen-share password')


def tailscale_address():
    try:
        result = subprocess.run(['tailscale', 'status', '--json'], capture_output=True,
                                text=True, timeout=5, check=True)
        state = json.loads(result.stdout)
        own = state.get('Self') or {}
        if state.get('BackendState') != 'Running' or own.get('Online') is not True:
            return None
        for value in own.get('TailscaleIPs') or []:
            address = ipaddress.ip_address(value)
            if address.version == 4 and address in ipaddress.ip_network('100.64.0.0/10'):
                return str(address)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def stop_child(child, wait=5):
    if not child or child.poll() is not None:
        return
    # WayVNC holds the listener until it exits. Block SIGTERM for the wait so
    # a stop that arrives during an address change stays pending, then exit
    # once the mask is restored.
    signal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGTERM])
    try:
        child.terminate()
        try:
            child.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=wait)
    finally:
        pending = signal.SIGTERM in signal.sigpending()
        signal.pthread_sigmask(signal.SIG_UNBLOCK, [signal.SIGTERM])
    if pending:
        sys.exit(0)


# Apple Screen Sharing sends Command as Alt and Option as Super so a Mac
# server will see the keys the viewer pressed. WayVNC looks those keysyms
# up in the keymap it publishes on its virtual keyboard and forwards the
# resulting modifier mask. Swapping the symbols would make that lookup pick
# the other key and keep the original modifier, so this map keeps the
# symbols and changes the modifier each key sets. Hyprland then matches its
# existing Super and Alt bindings. The keymap travels with the virtual
# keyboard only. Right Alt is untouched so layouts that use it as AltGr
# keep level 3. replace on Left Alt also replaces lv3:lalt_switch, so
# left Alt is no longer level 3.
APPLE_VNC_OPTION = 'groundwork:apple_vnc'
# libxkbcommon 1.13 appends rules/evdev.post from every include path after
# the main rules file. A user rules/evdev wins that main-file lookup, so the
# option has to live in the post fragment too. The full rules file remains
# for libraries that do not compose .post files.
XKB_OPTION_RULES = """! option = symbols
  groundwork:apple_vnc = +groundwork(apple_vnc)

! option = compat
  groundwork:apple_vnc = +groundwork(apple_vnc)
"""
XKB_RULES = "! include %S/evdev\n\n" + XKB_OPTION_RULES
XKB_SYMBOLS = """partial modifier_keys
xkb_symbols "apple_vnc" {
    replace key <LALT> {
        type[Group1] = "ONE_LEVEL",
        symbols[Group1] = [ Alt_L ],
        actions[Group1] = [ SetMods(modifiers=Mod4,clearLocks) ]
    };
    replace key <LWIN> {
        type[Group1] = "ONE_LEVEL",
        symbols[Group1] = [ Super_L ],
        actions[Group1] = [ SetMods(modifiers=Mod1,clearLocks) ]
    };
    replace key <RWIN> {
        type[Group1] = "ONE_LEVEL",
        symbols[Group1] = [ Super_R ],
        actions[Group1] = [ SetMods(modifiers=Mod1,clearLocks) ]
    };
};
"""
XKB_COMPAT = """partial xkb_compatibility "apple_vnc" {
    interpret Alt_R+Any {
        action = SetMods(modifiers=Mod4,clearLocks);
    };
    interpret Alt_R {
        action = SetMods(modifiers=Mod4,clearLocks);
    };
    interpret Meta_R+Any {
        action = SetMods(modifiers=Mod4,clearLocks);
    };
    interpret Meta_R {
        action = SetMods(modifiers=Mod4,clearLocks);
    };
};
"""


def xkb_root():
    return config_path().parent / 'xkb'


def install_vnc_keymap():
    root = xkb_root()
    private_directory(root.parent)
    files = {
        'rules/evdev': XKB_RULES,
        'rules/evdev.post': XKB_OPTION_RULES,
        'symbols/groundwork': XKB_SYMBOLS,
        'compat/groundwork': XKB_COMPAT,
    }
    for relative, content in files.items():
        path = root / relative
        private_directory(path.parent)
        replace_file(path, content.encode(), 0o600)


def wayvnc_environment():
    # WayVNC leaves xkb_options empty. libxkbcommon then reads
    # XKB_DEFAULT_OPTIONS for every client and for QEMU extended key events,
    # so the swap belongs to this process rather than to one viewer.
    # XKB_CONFIG_EXTRA_PATH is one directory; a colon-joined value is dropped.
    # GROUNDWORK_SCREEN_SHARE_APPLE_MODIFIERS=0 skips appending the option.
    # The password file, XDG_CONFIG_HOME, and other processes stay unchanged.
    install_vnc_keymap()
    environment = os.environ.copy()
    environment.setdefault('XKB_DEFAULT_RULES', 'evdev')
    environment['XKB_CONFIG_EXTRA_PATH'] = str(xkb_root())
    if environment.get('GROUNDWORK_SCREEN_SHARE_APPLE_MODIFIERS') != '0':
        parts = [part.strip() for part in environment.get('XKB_DEFAULT_OPTIONS', '').split(',') if part.strip()]
        if APPLE_VNC_OPTION not in parts:
            parts.append(APPLE_VNC_OPTION)
        environment['XKB_DEFAULT_OPTIONS'] = ','.join(parts)
    return environment


def bind_listener(listener, address, port=5900, device=b'tailscale0', wait=70, pause=0.25):
    # An active client leaves this address in TIME_WAIT for about 60s after
    # the old listener closes. SO_REUSEADDR lets the new socket bind during
    # that window only when the old socket had the option too. A predecessor
    # without it makes bind() return EADDRINUSE, and Restart= would exit and
    # retry every 10s until the tuple expires. Wait in this process instead.
    # The option still refuses a second live listener on this address.
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, device + b'\0')
    deadline = time.monotonic() + wait
    announced = False
    while True:
        try:
            listener.bind((address, port))
            break
        except OSError as error:
            if error.errno != errno.EADDRINUSE or time.monotonic() >= deadline:
                raise
            if not announced:
                print('Screen sharing is waiting for TCP ' + str(port) + ' to be released.',
                      file=sys.stderr)
                announced = True
            time.sleep(pause)
    listener.listen(16)


def start_server(address, runtime):
    # Linux has weak-host routing: binding a Tailscale IP alone does not reject
    # packets routed to it through a LAN interface. Pass an interface-bound
    # listening descriptor to WayVNC instead. Failure never falls back to TCP.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        bind_listener(listener, address)
        # Record the bytes WayVNC is about to load. The copy read when serve()
        # started can be older than this file.
        remember_served_config(read_config_bytes(config_path()))
        return subprocess.Popen([
            'wayvnc', '--config=' + str(config_path()),
            '--socket=' + str(runtime / 'control'), '--log-level=error',
            'fd:' + str(listener.fileno())], pass_fds=(listener.fileno(),),
            env=wayvnc_environment())


def serve():
    read_config(config_path())
    if not os.environ.get('WAYLAND_DISPLAY') or not os.environ.get('XDG_RUNTIME_DIR'):
        raise RuntimeError('A running Wayland desktop is required; sign in locally first.')
    runtime = Path(os.environ['XDG_RUNTIME_DIR']) / 'groundwork-screen-share'
    private_directory(runtime)
    child = None
    active = None
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        while True:
            address = tailscale_address()
            if child and child.poll() is not None:
                raise RuntimeError('WayVNC exited; inspect the user service journal.')
            if child and address != active:
                stop_child(child)
                child = None
            if address and child is None:
                child = start_server(address, runtime)
                active = address
            time.sleep(5)
    finally:
        stop_child(child)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('configure', 'serve', 'password'))
    args = parser.parse_args()
    try:
        if args.action == 'configure':
            configure()
        elif args.action == 'serve':
            serve()
        else:
            if not sys.stdout.isatty():
                raise RuntimeError('Show the password only in your local terminal, outside recorded logs.')
            print(read_config(config_path()))
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print('Screen sharing: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
