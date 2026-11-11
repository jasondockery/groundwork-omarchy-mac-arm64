#!/usr/bin/env python3
"""Enable EasyEffects as a graphical-session background service.

EasyEffects 8.x (Qt) equalizes PipeWire output only while its process is
running. Omarchy's Quickshell tray shows its StatusNotifier icon only then.
This helper writes a Groundwork-owned user unit and enables it for the next
graphical login. It does not start or restart the process during apply, so a
live audio session is left alone. It never writes ~/.config/easyeffects or
EasyEffects' own XDG autostart file.
"""
import argparse
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

MARKER = '# Groundwork EasyEffects (decision 0024)'
UNIT_NAME = 'groundwork-easyeffects.service'
SYSTEMCTL_SECONDS = 20

# EasyEffects 8.x (Qt). --gapplication-service is the old GTK flag: still
# accepted for compatibility, marked deprecated in command_line_parser.cpp,
# and must not be used for a new unit (wwmm/easyeffects#4446, #4466).
SERVICE_ARGS = ('--hide-window', '--service-mode')


def unit_path():
    return Path.home() / '.config/systemd/user' / UNIT_NAME


def unit_text(executable):
    # WantedBy=graphical-session.target matches screen sharing: start with the
    # desktop (Quickshell tray, PipeWire), not at boot via linger.
    quoted = '"' + str(executable) + '"'
    flags = ' '.join(SERVICE_ARGS)
    return (
        f'{MARKER}\n'
        '[Unit]\n'
        'Description=Groundwork EasyEffects background service\n'
        '# PipeWire must be up before the filter graph is created.\n'
        'After=pipewire.service graphical-session.target\n'
        'Wants=pipewire.service\n'
        '# Stop with the desktop so EQ and the tray icon do not outlive the session.\n'
        'PartOf=graphical-session.target\n'
        '\n'
        '[Service]\n'
        f'ExecStart={quoted} {flags}\n'
        f'ExecStop={quoted} --quit\n'
        'Restart=on-failure\n'
        'RestartSec=5\n'
        '\n'
        '[Install]\n'
        'WantedBy=graphical-session.target\n'
    )


def private_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('EasyEffects unit directory must be an owned, regular directory.')
    path.chmod(0o700)


def replace_file(path, content, mode):
    if path.is_symlink():
        raise RuntimeError('Refusing to replace a symlink: ' + str(path))
    data = content.encode()
    if path.exists() and path.read_bytes() == data:
        path.chmod(mode)
        return False
    if path.exists():
        number = 1
        while path.with_name(path.name + '.bak.' + str(number)).exists():
            number += 1
        path.rename(path.with_name(path.name + '.bak.' + str(number)))
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            os.fchmod(output.fileno(), mode)
            output.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def resolved_easyeffects():
    found = shutil.which('easyeffects')
    if not found:
        raise RuntimeError('easyeffects is not on PATH. Install it with: omarchy pkg add easyeffects')
    path = Path(found)
    if not path.is_absolute():
        raise RuntimeError('easyeffects on PATH is not an absolute path: ' + found)
    if any(ch.isspace() for ch in found):
        raise RuntimeError('easyeffects path contains whitespace; not written into the unit: ' + found)
    return path.resolve()


def systemctl(*args):
    subprocess.run(['systemctl', '--user', *args], check=True, timeout=SYSTEMCTL_SECONDS)


def configure():
    executable = resolved_easyeffects()
    path = unit_path()
    private_directory(path.parent)
    replace_file(path, unit_text(executable), 0o644)
    systemctl('daemon-reload')
    # Enable only. A start or restart here would insert filters into a live
    # session, or run without a Wayland display from SSH. The unit starts with
    # the next graphical login.
    systemctl('enable', UNIT_NAME)
    print('EasyEffects will start with the next desktop login as a background '
          'service (no window). ~/.config/easyeffects was not changed.')


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Enable EasyEffects as a graphical-session background service.')
    parser.add_argument('command', choices=('configure',),
                        help='Write and enable the user unit. Does not start it.')
    args = parser.parse_args(argv)
    if args.command == 'configure':
        configure()
        return 0
    return 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as error:
        print(error, file=sys.stderr)
        sys.exit(error.returncode or 1)
    except RuntimeError as error:
        print(error, file=sys.stderr)
        sys.exit(1)
