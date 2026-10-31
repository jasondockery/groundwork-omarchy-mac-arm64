#!/usr/bin/env python3
# Groundwork disk report (decision 0019)
"""Report what could be cleaned up and print the command for each. Deletes nothing.

Omarchy already prunes the pacman cache during `omarchy-update` (paccache -rk2),
refuses an update under 10 GiB free, and ships Disktree as its graphical disk
usage tool. This only measures the pieces Omarchy leaves alone: the AUR helper
cache, the systemd journal, and Flatpak installations (unused runtimes are removed
with flatpak's own prompt; this does not count them). Every action line
is a command for the person to read and run themselves.

Exit status: 0 always for a finished report, 2 for bad usage.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

GIB = 1024 ** 3
OMARCHY_UPDATE_MIN_FREE = 10 * GIB  # omarchy-update-requires-free-space
PACMAN_CACHE = Path('/var/cache/pacman/pkg')
FLATPAK_SYSTEM = Path('/var/lib/flatpak')


def human(n):
    n = float(n)
    for unit in ('B', 'KiB', 'MiB', 'GiB'):
        if n < 1024 or unit == 'GiB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024


def tree_size(path):
    """Bytes under path without following symlinks; None if it does not exist."""
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return None
    total = 0
    stack = [str(path)]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def run(argv):
    """Stdout of a read-only command, or None when it is missing or fails."""
    if shutil.which(argv[0]) is None:
        return None
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def journal_bytes(text):
    match = re.search(r'take up ([\d.]+)([KMGT]?)\b', text or '')
    if not match:
        return None
    scale_key = match.group(2)
    scale = {'': 1, 'K': 1024, 'M': 1024 ** 2, 'G': GIB, 'T': 1024 ** 4}[scale_key]
    return int(float(match.group(1)) * scale)


def collect(home, run=None, tree_size=None, free_bytes=None):
    run = run or globals()['run']
    tree_size = tree_size or globals()['tree_size']
    home = Path(home)
    cache_home = Path(os.environ.get('XDG_CACHE_HOME') or home / '.cache')
    data_home = Path(os.environ.get('XDG_DATA_HOME') or home / '.local/share')
    rows = []
    if free_bytes is None:
        free_bytes = shutil.disk_usage('/').free
    rows.append(('Free space on /', human(free_bytes),
                 f'Omarchy refuses updates under {human(OMARCHY_UPDATE_MIN_FREE)}.'
                 + (' You are under that now.' if free_bytes < OMARCHY_UPDATE_MIN_FREE else ''),
                 None))
    size = tree_size(PACMAN_CACHE)
    rows.append(('Pacman cache', 'not found' if size is None else human(size),
                 "Omarchy trims this to two versions on every update; Groundwork adds nothing.",
                 'sudo paccache -rk2' if size else None))
    size = tree_size(cache_home / 'yay')
    rows.append(('AUR helper cache (yay)', 'not found' if size is None else human(size),
                 'Omarchy does not clean this. yay asks before it deletes.',
                 'yay -Sc' if size else None))
    out = run(['journalctl', '--disk-usage'])
    size = journal_bytes(out)
    rows.append(('systemd journal', 'unknown' if size is None else human(size),
                 'Keeps old logs for debugging; vacuum only if you do not need them.',
                 'sudo journalctl --vacuum-size=500M' if size else None))
    for scope, flag, root in (('user', '--user', data_home / 'flatpak'), ('system', '--system', FLATPAK_SYSTEM)):
        size = tree_size(root)
        if size is None:
            continue
        rows.append((f'Flatpak {scope} installation', human(size),
                     'Unused runtimes are not counted here. The command below shows its own list and asks before it removes anything.',
                     f'flatpak uninstall {flag} --unused'))
    return rows


def main(argv):
    if argv and argv[0] in ('-h', '--help'):
        print(__doc__.strip())
        return 0
    if argv:
        print('usage: groundwork-disk-report [-h]', file=sys.stderr)
        return 2
    print('Disk report. This only measures; it deletes nothing.\n')
    for label, value, note, command in collect(Path.home()):
        print(f'{label}: {value}')
        print(f'  {note}')
        if command:
            print(f'  To clean: {command}')
    print('\nTo see what fills the disk, open Disktree (Omarchy installs it): disktree')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
