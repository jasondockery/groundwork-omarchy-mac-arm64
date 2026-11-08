#!/usr/bin/env python3
# Groundwork Omarchy temp storage (decision 0022)
"""/tmp on disk and per-user scratch roots (issue #138).

  groundwork-tmp.py fstab-tmp      print each /etc/fstab line that mounts /tmp;
                                   exit 0 when there is one, 1 when there is none
  groundwork-tmp.py tmp-unit       print /etc/systemd/system/tmp.mount when it is a
                                   unit of its own, which systemctl mask cannot
                                   replace; exit 0 when it is, 1 when not
  groundwork-tmp.py tmp-backing    print the filesystem type behind /tmp (tmpfs,
                                   btrfs, ...), or "unknown"
  groundwork-tmp.py apply-scratch  create each scratch root that omarchy.json's
                                   scratch_dirs names under ~/.cache, mode 0700

apply-layer.sh runs these; it masks tmp.mount itself with sudo. This helper
never needs root, never edits /etc/fstab, and never touches the live /tmp.
groundwork-doctor imports report() for its "Temp storage" check.

A scratch root is refused, and not created, when it resolves onto a tmpfs or
ramfs, into a git work tree, into ~/code (GROUNDWORK_CODE_DIR), or into a
Syncthing folder this layer manages (scripts/groundwork-syncthing.py). Omarchy,
not this layer, manages snapper, and it snapshots only the root subvolume;
~/.cache is on /home, which has no snapper config.

Exit status: 0 on success; 1 from fstab-tmp or tmp-unit when there is none. 1 when
apply-scratch refused or could not create a scratch root. 2 when fstab-tmp,
tmp-unit, or tmp-backing could not read the system, 64 for bad usage.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

HERE = Path(__file__).resolve().parent
RAM_FILESYSTEMS = frozenset({'tmpfs', 'ramfs'})
SCRATCH_MODE = 0o700
SYSTEMCTL_SECONDS = 10
# Overrides of the upstream tmpfiles rule, in the order systemd-tmpfiles reads them.
TMP_UNIT = '/etc/systemd/system/tmp.mount'
CLEAN_TIMER = 'systemd-tmpfiles-clean.timer'
TMPFILES_UPSTREAM = '/usr/lib/tmpfiles.d/tmp.conf'
TMPFILES_OVERRIDES = ('/etc/tmpfiles.d/tmp.conf', '/run/tmpfiles.d/tmp.conf')


class TmpError(Exception):
    pass


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def system_root(environ):
    """GROUNDWORK_TEST_ROOT relocates system paths, only with the harness's GROUNDWORK_TESTING=1."""
    relocated = environ.get('GROUNDWORK_TEST_ROOT')
    if not relocated:
        return Path('/')
    if environ.get('GROUNDWORK_TESTING') != '1':
        raise TmpError('GROUNDWORK_TEST_ROOT is a test-only variable. Unset it and rerun; nothing was changed.')
    return Path(relocated)


def system_path(environ, path):
    return system_root(environ) / path.lstrip('/')


def lstat_entry(path):
    """lstat of `path`, or None when it is absent.

    Path.exists and Path.is_symlink return false on EACCES, so an unsearchable
    directory would look like no unit and setup would mask over a file it
    cannot see. lstat raises that error instead.
    """
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def unescape(field):
    """fstab and mountinfo write a space as \\040 and other bytes the same way."""
    return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match.group(1), 8)), field)


def mounts(environ):
    """(mount point, filesystem type) for each line of /proc/self/mountinfo, in mount order."""
    try:
        text = system_path(environ, '/proc/self/mountinfo').read_text()
    except FileNotFoundError:
        return []
    found = []
    for line in text.splitlines():
        fields = line.split()
        if ' - ' not in line or len(fields) < 5:
            continue
        after = line.split(' - ', 1)[1].split()
        if after:
            found.append((unescape(fields[4]), after[0]))
    return found


def filesystem_of(path, mount_list):
    """The type of the last-mounted filesystem whose mount point holds `path`, or None."""
    path = os.path.normpath(str(path))
    best, best_length = None, -1
    for point, kind in mount_list:
        point = os.path.normpath(point)
        inside = path == point or path.startswith(point.rstrip('/') + '/')
        if inside and len(point) >= best_length:
            best, best_length = kind, len(point)
    return best


def tmp_backing(environ):
    return filesystem_of('/tmp', mounts(environ)) or 'unknown'


def fstab_tmp_lines(environ):
    """Lines of /etc/fstab that mount /tmp. A missing fstab has none."""
    try:
        text = system_path(environ, '/etc/fstab').read_text()
    except FileNotFoundError:
        return []
    lines = []
    for line in text.splitlines():
        fields = line.split()
        if not fields or fields[0].startswith('#') or len(fields) < 2:
            continue
        # normpath keeps a leading "//", so collapse runs of slashes first.
        if os.path.normpath(re.sub('/+', '/', unescape(fields[1]))) == '/tmp':
            lines.append(line.strip())
    return lines


def custom_tmp_unit(environ):
    """TMP_UNIT when it is a unit of its own (systemctl edit --full, a copy, or a link elsewhere), else None.

    systemctl mask writes a symlink to /dev/null there and refuses to replace a
    file it did not write, so this layer reports it and does not mask.
    An unreadable path raises OSError; that is not the same as no unit.
    """
    path = system_path(environ, TMP_UNIT)
    info = lstat_entry(path)
    if info is None:
        return None
    if stat.S_ISLNK(info.st_mode) and os.readlink(path) == '/dev/null':
        return None
    return TMP_UNIT


def tmpfiles_problems(environ):
    """Problems with upstream ageing of /tmp. Empty when it is mode 1777 with an age."""
    problems = []
    for override in TMPFILES_OVERRIDES:
        path = system_path(environ, override)
        try:
            info = lstat_entry(path)
        except OSError as error:
            problems.append(f'{override} could not be checked ({error.strerror}), so /tmp ageing could not be confirmed. '
                            f'Fix that, then rerun groundwork-doctor.')
            continue
        if info is not None:
            problems.append(f'{override} replaces the upstream {TMPFILES_UPSTREAM}, so /tmp may not be aged. '
                            f'Compare the two; to use upstream ageing, remove it with: sudo rm {override}')
    try:
        text = system_path(environ, TMPFILES_UPSTREAM).read_text()
    except FileNotFoundError:
        problems.append(f'{TMPFILES_UPSTREAM} is missing, so nothing ages /tmp. Reinstall systemd: sudo pacman -S systemd')
        return problems
    rules = [line.split() for line in text.splitlines()
             if line.split() and not line.lstrip().startswith('#') and len(line.split()) >= 2
             and line.split()[1] == '/tmp']
    good = [rule for rule in rules if rule[0].rstrip('!+-=~^').lower() in ('q', 'd', 'v')
            and len(rule) >= 6 and rule[2] == '1777' and rule[5] not in ('-', '')]
    if not good:
        found = ' '.join(rules[0]) if rules else 'no /tmp line'
        problems.append(f'{TMPFILES_UPSTREAM} does not give /tmp mode 1777 with an age ({found}). '
                        'Upstream systemd ships "q /tmp 1777 root root 10d"; reinstall it with: sudo pacman -S systemd')
    return problems


def tmpfiles_age(environ):
    try:
        text = system_path(environ, TMPFILES_UPSTREAM).read_text()
    except FileNotFoundError:
        return None
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 6 and fields[1] == '/tmp' and not fields[0].startswith('#'):
            return fields[5]
    return None


def config_path(environ):
    base = environ.get('XDG_CONFIG_HOME') or str(Path(environ['HOME']) / '.config')
    return Path(base) / 'groundwork/omarchy.json'


def saved_choices(environ):
    """(tmp_on_disk, scratch dir names) from omarchy.json, validated by the updater's rules."""
    updater = load('groundwork_update', 'groundwork-update.py')
    path = config_path(environ)
    if any(part.is_symlink() for part in [*path.parents, path]):
        raise TmpError(f'{path} or a parent is a symbolic link. Preserve and repair it, then rerun.')
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        value = None
    except ValueError as error:
        raise TmpError(f'{path} is not valid JSON ({error}). Preserve and repair it, then rerun.') from error
    if value is not None and not isinstance(value, dict):
        raise TmpError(f'{path} is not a JSON object. Preserve and repair it, then rerun.')
    try:
        return updater.tmp_on_disk_enabled(value), updater.scratch_dir_names(value)
    except updater.UpdateError as error:
        raise TmpError(str(error).replace(str(updater.CONFIG), str(path))) from error


def synced_folders(home):
    """The Syncthing folders groundwork-syncthing manages, under `home`."""
    syncthing = load('groundwork_syncthing', 'groundwork-syncthing.py')
    real_home = Path.home()
    folders = []
    for folder in (syncthing.shared_path(), syncthing.freetube_path()):
        folders.append(home / Path(folder).relative_to(real_home))
    return folders


def inside(path, parent):
    return path == parent or parent in path.parents


def scratch_problem(name, environ, mount_list=None):
    """Why the scratch root ~/.cache/<name> is refused, or None when its place is allowed."""
    home = Path(environ['HOME'])
    cache = home / '.cache'
    target = cache / name
    if target.is_symlink():
        return f'{target} is a symbolic link. A scratch root must be a real directory; move the link away and rerun.'
    if cache.exists() and not cache.is_dir():
        return f'{cache} is not a directory.'
    resolved = cache.resolve() / name
    if target.exists() and not target.is_dir():
        return f'{target} exists and is not a directory. Move it away and rerun.'
    kind = filesystem_of(resolved, mounts(environ) if mount_list is None else mount_list)
    if kind in RAM_FILESYSTEMS:
        return (f'{target} resolves to {resolved}, which is on {kind} (RAM). A scratch root must be on disk; '
                f'point ~/.cache at disk or remove {name} from scratch_dirs.')
    for parent in [resolved, *resolved.parents]:
        git = parent / '.git'
        if git.exists() or git.is_symlink():
            return (f'{target} resolves into the git work tree at {parent}. A scratch root must not live in a '
                    f'clone; remove {name} from scratch_dirs or move ~/.cache out of that tree.')
    code = Path(environ.get('GROUNDWORK_CODE_DIR') or home / 'code')
    code = code.resolve() if code.exists() else code
    if inside(resolved, code):
        return f'{target} resolves into {code}, where clones live. Remove {name} from scratch_dirs.'
    for folder in synced_folders(home):
        folder = folder.resolve() if folder.exists() else folder
        if inside(resolved, folder) or inside(folder, resolved):
            return (f'{target} resolves to {resolved}, which overlaps the Syncthing folder {folder}. '
                    f'Scratch roots are never synced; remove {name} from scratch_dirs.')
    return None


def apply_scratch(environ):
    """Create each configured scratch root with mode 0700. Returns the exit status."""
    _, names = saved_choices(environ)
    status = 0
    for name in names:
        problem = scratch_problem(name, environ)
        if problem:
            print(f'Scratch root refused: {problem}', file=sys.stderr)
            status = 1
            continue
        target = Path(environ['HOME']) / '.cache' / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.mkdir(target, SCRATCH_MODE)
            created = True
        except FileExistsError:
            created = False
        info = os.lstat(target)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            print(f'Scratch root refused: {target} is not a directory you own. Nothing was changed there.',
                  file=sys.stderr)
            status = 1
            continue
        if stat.S_IMODE(info.st_mode) != SCRATCH_MODE:
            os.chmod(target, SCRATCH_MODE)
            if not created:
                print(f'Set scratch root {target} to mode 0700.')
        if created:
            print(f'Created scratch root {target} (mode 0700, on disk, not synced).')
    return status


def mask_state(environ, unit='tmp.mount'):
    """systemctl is-enabled for `unit` ("masked", "static", ...), or None when unreadable. Read-only."""
    try:
        result = subprocess.run(['systemctl', 'is-enabled', unit], env=environ, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=SYSTEMCTL_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None
    # is-enabled exits 1 for a masked unit; anything else past 1 is a failure.
    if result.returncode not in (0, 1):
        return None
    return result.stdout.strip() or None


def report(environ):
    """(problems, summary) for groundwork-doctor. Each problem names its next step."""
    try:
        enabled, names = saved_choices(environ)
    except TmpError as error:
        return [str(error)], ''
    backing = tmp_backing(environ)
    state = mask_state(environ)
    masked = state == 'masked'
    fstab = fstab_tmp_lines(environ)
    try:
        unit = custom_tmp_unit(environ)
        unit_error = None
    except OSError as error:
        unit = None
        unit_error = error
    problems = []
    if enabled:
        for line in fstab:
            problems.append(f'/etc/fstab mounts /tmp ("{line}"), which defeats masking tmp.mount. Setup does not edit '
                            'fstab: remove that line yourself, then run groundwork-update and reboot.')
        if unit_error is not None:
            problems.append(f'{TMP_UNIT} could not be checked ({unit_error.strerror}), so it was not treated as absent '
                            f'and setup will not mask tmp.mount. Fix that, then rerun groundwork-doctor.')
        elif unit:
            problems.append(f'{unit} is a tmp.mount unit of its own (from systemctl edit --full or a copy), so '
                            'systemctl mask cannot replace it and setup did not mask tmp.mount. Review it; to drop it, '
                            'run: sudo systemctl revert tmp.mount, then groundwork-update, then reboot.')
        if fstab or unit or unit_error is not None:
            pass  # The conflict above is the finding; setup does not mask while it exists.
        elif state is None:
            problems.append('The tmp.mount mask state is unknown: systemctl is-enabled tmp.mount gave no answer. '
                            'Check it with: systemctl is-enabled tmp.mount')
        elif not masked and backing in RAM_FILESYSTEMS:
            problems.append(f'/tmp is {backing} (RAM) and tmp.mount is not masked. '
                            'Run groundwork-update (it masks tmp.mount), then reboot.')
        elif not masked:
            problems.append(f'/tmp is on {backing} now, but tmp.mount is not masked, so a later boot can mount '
                            'it in RAM again. Run groundwork-update (it masks tmp.mount).')
        elif backing in RAM_FILESYSTEMS:
            problems.append('tmp.mount is masked, but /tmp is still tmpfs until the next reboot. '
                            'Reboot to move /tmp to disk, then check it with: findmnt -T /tmp')
    problems.extend(tmpfiles_problems(environ))
    # The timer that runs the ageing. Only read, never changed.
    timer = mask_state(environ, CLEAN_TIMER)
    if timer is None:
        problems.append(f'The {CLEAN_TIMER} state is unknown: systemctl is-enabled {CLEAN_TIMER} gave no answer, '
                        f'so /tmp ageing could not be confirmed. Check it with: systemctl is-enabled {CLEAN_TIMER}')
    elif timer.startswith('masked'):
        problems.append(f'{CLEAN_TIMER} is masked, so nothing ages /tmp. '
                        f'Unmask it with: sudo systemctl unmask {CLEAN_TIMER}')
    for name in names:
        target = Path(environ['HOME']) / '.cache' / name
        problem = scratch_problem(name, environ)
        if problem:
            problems.append(f'Scratch root refused: {problem}')
        elif not target.is_dir():
            problems.append(f'Scratch root {target} is missing. Run groundwork-update to create it.')
        elif target.stat().st_uid != os.getuid():
            problems.append(f'Scratch root {target} is not owned by you. Move it away, then run groundwork-update.')
        elif stat.S_IMODE(target.stat().st_mode) != SCRATCH_MODE:
            problems.append(f'Scratch root {target} is not mode 0700. Run groundwork-update, or: chmod 700 {target}')
    choice = 'on' if enabled else 'off by choice'
    mask = 'masked' if masked else ('mask state unknown' if state is None else 'not masked')
    summary = (f'/tmp on {backing}, tmp.mount {mask} (/tmp on disk {choice}), '
               f'tmpfiles ages /tmp after {tmpfiles_age(environ) or "?"}, '
               f'{len(names)} scratch root{"" if len(names) == 1 else "s"}')
    return problems, summary


def main(argv=None, environ=None):
    argv = sys.argv[1:] if argv is None else argv
    environ = dict(os.environ if environ is None else environ)
    if len(argv) != 1 or argv[0] not in ('fstab-tmp', 'tmp-unit', 'tmp-backing', 'apply-scratch'):
        print('Usage: groundwork-tmp.py fstab-tmp|tmp-unit|tmp-backing|apply-scratch', file=sys.stderr)
        return 64
    try:
        if argv[0] == 'fstab-tmp':
            lines = fstab_tmp_lines(environ)
            for line in lines:
                print(line)
            return 0 if lines else 1
        if argv[0] == 'tmp-unit':
            unit = custom_tmp_unit(environ)
            if unit:
                print(unit)
            return 0 if unit else 1
        if argv[0] == 'tmp-backing':
            print(tmp_backing(environ))
            return 0
        return apply_scratch(environ)
    except (TmpError, OSError) as error:
        print(f'Groundwork temp storage: {error}', file=sys.stderr)
        # 1 from fstab-tmp and tmp-unit means "none", so a failure there is 2.
        if 'test-only' in str(error):
            return 64
        return 1 if argv[0] == 'apply-scratch' else 2

if __name__ == '__main__':
    sys.exit(main())
