#!/usr/bin/env python3
# Groundwork Omarchy T3 launcher
"""Keep one T3 Code launcher entry, and tell whether T3 Code is running.

Omarchy's installer (omarchy-install-ai-t3-code, decision 0020) writes
t3code.desktop. The running app later registers
com.t3tools.T3Code.desktop for t3code:// and points the scheme handler at it.
This helper points x-scheme-handler/t3code back at t3code.desktop with
xdg-mime, then removes the app's regenerable duplicate. The app re-creates the
duplicate with NoDisplay=true, which the launcher hides, and a later run
removes it again. Nothing changes while T3 Code is running, or when that cannot
be established.

`running` exits 0 when a T3 Code process runs or that cannot be ruled out, and
1 when none does. It also counts the standalone bun CLI from t3.codes
(~/.t3/runtime/versions/*). The updater and the t3-cli migration use it.
"""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

PRIMARY = 't3code.desktop'
DUPLICATE = 'com.t3tools.T3Code.desktop'
SCHEME = 'x-scheme-handler/t3code'
APPIMAGE = 'T3-Code.AppImage'
# AppImage mounts are /tmp/.mount_<first six characters of the name>XXXXXX.
MOUNT_PREFIX = '.mount_T3-Cod'
# APPIMAGE_EXTRACT_AND_RUN=1 (no FUSE) runs from /tmp/appimage_extracted_<hash>.
EXTRACTED_PREFIX = 'appimage_extracted_'


def data_home():
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')


def launch_lines(file):
    try:
        lines = Path(file).read_text(errors='replace').splitlines()
    except OSError:
        return []
    return [value for key, _, value in (line.partition('=') for line in lines) if key in ('Exec', 'TryExec')]


def t3_home():
    """T3CODE_HOME, as Omarchy's installer and the app read it."""
    return Path(os.environ.get('T3CODE_HOME') or Path.home() / '.t3')


def exec_paths(*files):
    """AppImage paths named by the T3 desktop entries, plus the installer default."""
    paths = {str(Path.home() / '.local/share/t3code' / APPIMAGE)}
    for file in files:
        for value in launch_lines(file):
            try:
                words = shlex.split(value)
            except ValueError:
                continue
            # Omarchy's installer writes a literal % in the Exec path as %%.
            paths.update(word.replace('%%', '%') for word in words[:1] if word.endswith(APPIMAGE))
    return paths


def t3_running(paths, proc=Path('/proc'), prefixes=()):
    """Return (running, reason). running is True when unsure, so callers skip.

    A process counts when its executable or one of its first two arguments is
    in `paths`, when it runs from an AppImage mount of T3 Code or from an
    extract-and-run directory (as t3code), or when its executable starts
    with one of `prefixes`.
    """
    try:
        pids = [entry for entry in proc.iterdir() if entry.name.isdigit()]
    except OSError as error:
        return True, f'could not list processes ({error})'
    if not pids:
        return True, 'no processes were visible'
    for entry in pids:
        try:
            exe = os.readlink(entry / 'exe').removesuffix(' (deleted)')
            argv = (entry / 'cmdline').read_bytes().split(b'\0')
        except (PermissionError, FileNotFoundError, ProcessLookupError):
            continue  # another user's process, or one that just exited
        except OSError as error:
            return True, f'could not inspect process {entry.name} ({error})'
        parent = Path(exe).parent.name
        if exe in paths or parent.startswith(MOUNT_PREFIX) \
                or (parent.startswith(EXTRACTED_PREFIX) and Path(exe).name == 't3code') \
                or any(exe.startswith(prefix) for prefix in prefixes) \
                or any(part.decode(errors='replace') in paths for part in argv[:2]):
            return True, f'T3 Code is running (process {entry.name})'
    return False, ''


def proc_root():
    """/proc, or the fixture tree GROUNDWORK_TEST_PROC under GROUNDWORK_TESTING=1."""
    if os.environ.get('GROUNDWORK_TESTING') == '1' and os.environ.get('GROUNDWORK_TEST_PROC'):
        return Path(os.environ['GROUNDWORK_TEST_PROC'])
    return Path('/proc')


def app_running(proc=None, cli=False):
    """(running, reason) for the T3 Code app, and with `cli` the bun CLI too."""
    proc = proc_root() if proc is None else proc
    apps = data_home() / 'applications'
    versions = t3_home() / 'runtime/versions'
    # /proc/<pid>/exe is a resolved path, so match the real directory too.
    prefixes = tuple(dict.fromkeys((str(versions) + '/', os.path.realpath(versions) + '/'))) if cli else ()
    return t3_running(exec_paths(apps / PRIMARY, apps / DUPLICATE), proc, prefixes)


def default_handler():
    result = subprocess.run(['xdg-mime', 'query', 'default', SCHEME], capture_output=True,
                            text=True, timeout=15, stdin=subprocess.DEVNULL)
    return result.stdout.strip()


def drop_stale_association():
    """Remove the removed entry from [Added Associations] x-scheme-handler/t3code."""
    config = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'mimeapps.list'
    try:
        lines = config.read_text().splitlines(keepends=True)
    except OSError:
        return []
    section, changed, out = '', False, []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('['):
            section = stripped
        key, _, value = stripped.partition('=')
        if section == '[Added Associations]' and key == SCHEME and DUPLICATE in value.split(';'):
            kept = [item for item in value.split(';') if item and item != DUPLICATE]
            changed = True
            if not kept:
                continue
            line = f"{key}={';'.join(kept)};\n"
        out.append(line)
    if not changed:
        return []
    try:
        temporary = config.with_name(config.name + '.groundwork-tmp')
        temporary.write_text(''.join(out))
        os.replace(temporary, config)
    except OSError as error:
        return [f'Could not tidy {config.name} ({error}).']
    return [f'Dropped the stale {DUPLICATE} association from {config.name}.']


def reconcile(proc=Path('/proc')):
    """Return a list of messages. Always safe to repeat."""
    apps = data_home() / 'applications'
    primary, duplicate = apps / PRIMARY, apps / DUPLICATE
    if not shutil.which('xdg-mime'):
        # Nothing to repoint without it. Say so only when a duplicate exists.
        return ['T3 Code launcher check skipped: xdg-mime is not installed.'] if duplicate.exists() else []
    try:
        handler = default_handler()
    except (OSError, subprocess.SubprocessError) as error:
        return [f'T3 Code launcher check skipped: xdg-mime failed ({error}).']
    if not duplicate.exists() and handler != DUPLICATE:
        return []
    if not primary.is_file():
        return [f'T3 Code launcher check skipped: {primary} is missing.']
    running, reason = t3_running(exec_paths(primary, duplicate), proc)
    if running:
        return [f'T3 Code launcher check skipped: {reason}. Close T3 Code, then rerun ./install.sh or update-all.']
    messages = []
    if handler == DUPLICATE:
        try:
            subprocess.run(['xdg-mime', 'default', PRIMARY, SCHEME], check=True,
                           capture_output=True, timeout=15, stdin=subprocess.DEVNULL)
            handler = default_handler()
        except (OSError, subprocess.SubprocessError) as error:
            return [f'T3 Code launcher left alone: could not point {SCHEME} at {PRIMARY} ({error}).']
        if handler != PRIMARY:
            return [f'T3 Code launcher left alone: {SCHEME} is still handled by {handler}.']
        messages.append(f'{SCHEME} now opens {PRIMARY}.')
    if duplicate.exists():
        if not any(APPIMAGE in value for value in launch_lines(duplicate)):
            return messages + [f'{duplicate.name} does not launch T3 Code; left in place.']
        try:
            duplicate.unlink()
        except OSError as error:
            return messages + [f'{duplicate.name} could not be removed ({error}).']
        messages.append(f'Removed the duplicate launcher entry {duplicate.name}.')
    messages.extend(drop_stale_association())
    return messages


def main(argv):
    if argv[1:] == ['running']:
        running, reason = app_running(cli=True)
        if running:
            print(reason, flush=True)
        return 0 if running else 1
    if argv[1:] != ['reconcile']:
        print(f'usage: {Path(argv[0]).name} reconcile|running', file=sys.stderr)
        return 2
    for message in reconcile(proc_root()):
        print(message, flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
