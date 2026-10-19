#!/usr/bin/env python3
"""Own the local WayVNC service; never listen outside the active Tailscale IP."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys
import tempfile
import time


UNIT = '''[Unit]
Description=Groundwork screen sharing over Tailscale
After=graphical-session.target
PartOf=graphical-session.target

[Service]
ExecStart="%h/.local/bin/groundwork-screen-share" serve
Restart=on-failure
RestartSec=10
TimeoutStopSec=10
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
        return
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


def config_path():
    # A stable home-relative location is shared by setup and the user manager.
    return Path.home() / '.config/groundwork/remote-desktop/wayvnc.conf'


def config_text(password):
    # Apple Screen Sharing requires legacy VNC auth. Tailscale supplies encryption.
    return ('enable_auth=true\nrelax_encryption=true\nallow_broken_crypto=true\n'
            'password=' + password + '\n')


def read_config(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise RuntimeError('Refusing unsafe screen-sharing credentials.')
        os.fchmod(source.fileno(), 0o600)
        text = source.read(4096)
    match = re.search(r'^password=([A-Za-z0-9_-]{8})$', text, re.MULTILINE)
    if not match or text != config_text(match[1]):
        raise RuntimeError('Preserved modified screen-sharing config; reconcile it locally before setup.')
    return match[1]


def configure():
    path = config_path()
    private_directory(path.parent)
    if path.exists() or path.is_symlink():
        read_config(path)
    else:
        # DES uses only eight characters. Never reuse an account password here.
        replace_file(path, config_text(secrets.token_urlsafe(6)).encode(), 0o600)
    binary = Path.home() / '.local/bin/groundwork-screen-share'
    binary.parent.mkdir(parents=True, exist_ok=True)
    replace_file(binary, Path(__file__).read_bytes(), 0o755)
    unit = Path.home() / '.config/systemd/user/groundwork-screen-share.service'
    unit.parent.mkdir(parents=True, exist_ok=True)
    replace_file(unit, UNIT.encode(), 0o644)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True, timeout=20)
    subprocess.run(['systemctl', '--user', 'enable', 'groundwork-screen-share.service'], check=True, timeout=20)
    if os.environ.get('WAYLAND_DISPLAY'):
        subprocess.run(['systemctl', '--user', 'restart', 'groundwork-screen-share.service'], check=True, timeout=20)
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


def stop_child(child):
    if child and child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=2)


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
                child = subprocess.Popen([
                    'wayvnc', '--config=' + str(config_path()),
                    '--socket=' + str(runtime / 'control'), '--log-level=error',
                    address + ':5900'])
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
