#!/usr/bin/env python3
# Groundwork Omarchy /tmp on disk (decision 0022, issue #138)
"""Mask tmp.mount so /tmp lives on disk, keep systemd-tmpfiles aging, optional scratch.

systemd's tmp.mount is a RAM tmpfs capped at half of RAM. This layer masks that
unit so /tmp is a directory on the root filesystem after the next reboot. It
never writes an /etc/fstab line for /tmp (that would defeat the mask) and never
unmounts /tmp during apply. Upstream systemd-tmpfiles aging is checked, not
copied: q /tmp 1777 root root 10d in /usr/lib/tmpfiles.d/tmp.conf, with no
/etc/tmpfiles.d/tmp.conf override.

An optional per-user scratch directory lives under ~/.cache. Its name is set in
the machine override config (~/.config/groundwork/overrides.json), not in this
repository. Apply creates it, writes a CACHEDIR.TAG so backup tools skip it, and
refuses a name that would land under a git clone, ~/code, or ~/Shared.

The override file lists only settings that differ from upstream defaults, each
with a reason. Apply records tmp.mount = masked. Scratch is recorded only when
the machine already named it.

Subcommands: apply, doctor [--brief], needed.
Exit status: 0 done or healthy (doctor --brief is 0 even with findings), 1 a
step failed or doctor found a problem, 2 usage.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

SCHEMA = 1
TMP_MOUNT_ID = 'tmp.mount'
SCRATCH_ID = 'scratch'
TMP_MOUNT_VALUE = 'masked'
TMP_MOUNT_REASON = (
    'systemd mounts /tmp as a RAM tmpfs capped at 50% of RAM; builds filled it '
    'on 16 GB Apple Silicon Omarchy. Mask tmp.mount so /tmp lives on the disk '
    'root after reboot. Do not enlarge the tmpfs, and do not write /etc/fstab '
    'for /tmp.'
)
# systemd-tmpfiles types that create /tmp as a directory or subvolume.
TMPFILES_TYPES = frozenset({'q', 'Q', 'd', 'D', 'D!'})
SCRATCH_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
# This layer already uses ~/.cache/groundwork for T3 checks.
RESERVED_SCRATCH_NAMES = frozenset({'groundwork'})
CACHEDIR_TAG = (
    'Signature: 8a477f597d28d172789f06886806bc55\n'
    '# This file is a cache directory tag created by Groundwork.\n'
    '# For information about cache directory tags, see:\n'
    '#	https://bford.info/cachedir/\n'
)
COMMAND_SECONDS = 60


class TmpError(Exception):
    pass


def test_root(environ):
    """Fixture root when GROUNDWORK_TESTING=1 and GROUNDWORK_TEST_ROOT is set."""
    if environ.get('GROUNDWORK_TESTING') != '1':
        if environ.get('GROUNDWORK_TEST_ROOT'):
            raise TmpError(
                'GROUNDWORK_TEST_ROOT is a test-only variable. Unset it and rerun; nothing was changed.'
            )
        return Path('/')
    root = environ.get('GROUNDWORK_TEST_ROOT')
    if not root:
        return Path('/')
    return Path(root)


def home(environ):
    return Path(environ.get('HOME') or Path.home())


def cache_home(environ):
    configured = environ.get('XDG_CACHE_HOME')
    if configured:
        return Path(configured)
    return home(environ) / '.cache'


def config_home(environ):
    configured = environ.get('XDG_CONFIG_HOME')
    if configured:
        return Path(configured)
    return home(environ) / '.config'


def overrides_path(environ):
    return config_home(environ) / 'groundwork' / 'overrides.json'


def default_run(argv, environ):
    try:
        return subprocess.run(
            argv, env=environ, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, errors='replace', timeout=COMMAND_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, '', f'{argv[0]} did not finish within {COMMAND_SECONDS} seconds.\n')
    except OSError as error:
        return subprocess.CompletedProcess(argv, 127, '', str(error) + '\n')


class Host:
    """Machine paths and commands, relocated under GROUNDWORK_TEST_ROOT in tests."""

    def __init__(self, environ, run=None):
        self.environ = dict(os.environ if environ is None else environ)
        self.root = test_root(self.environ)
        self._run = run

    @property
    def fstab(self):
        return self.root / 'etc' / 'fstab'

    @property
    def tmp_mount_mask(self):
        return self.root / 'etc' / 'systemd' / 'system' / 'tmp.mount'

    @property
    def tmpfiles(self):
        return self.root / 'usr' / 'lib' / 'tmpfiles.d' / 'tmp.conf'

    @property
    def tmpfiles_override(self):
        return self.root / 'etc' / 'tmpfiles.d' / 'tmp.conf'

    def run(self, argv):
        if self._run is not None:
            return self._run(argv)
        return default_run(argv, self.environ)


def masked(host):
    """True when tmp.mount is masked by a symlink to /dev/null."""
    path = host.tmp_mount_mask
    try:
        return path.is_symlink() and os.readlink(path) == '/dev/null'
    except OSError:
        return False


def fstab_tmp_lines(path):
    """fstab lines whose mount point is /tmp. A symlink is untrusted."""
    if path.is_symlink():
        raise TmpError(f'Refusing to read a symlinked fstab: {path}')
    if not path.is_file():
        return []
    found = []
    for line in path.read_text(errors='replace').splitlines():
        stripped = line.split('#', 1)[0].strip()
        if not stripped:
            continue
        fields = stripped.split()
        if len(fields) >= 2 and fields[1].rstrip('/') == '/tmp':
            found.append(line)
    return found


def tmpfiles_tmp_entry(path):
    """Last /tmp line in a tmpfiles.d file, or None. A symlink is untrusted."""
    if path.is_symlink():
        raise TmpError(f'Refusing to read a symlinked tmpfiles file: {path}')
    if not path.is_file():
        return None
    last = None
    for line in path.read_text(errors='replace').splitlines():
        stripped = line.split('#', 1)[0].strip()
        if not stripped:
            continue
        fields = stripped.split()
        if len(fields) < 6:
            continue
        kind, target, mode, user, group, age = fields[:6]
        if target.rstrip('/') != '/tmp':
            continue
        last = (kind, mode, user, group, age)
    return last


def aging_ok(entry):
    if entry is None:
        return False
    kind, mode, user, group, age = entry
    return kind in TMPFILES_TYPES and mode in ('1777', '01777') and user == 'root' and group == 'root' and age == '10d'


def effective_tmpfiles(host):
    """The tmpfiles file that actually controls /tmp: /etc overrides /usr/lib."""
    if host.tmpfiles_override.exists() or host.tmpfiles_override.is_symlink():
        return host.tmpfiles_override, True
    return host.tmpfiles, False


def tmp_fstype(host):
    """Filesystem type of /tmp, or None when findmnt is missing or fails."""
    result = host.run(['findmnt', '-n', '-o', 'FSTYPE', '--target', '/tmp'])
    if result.returncode != 0:
        return None
    value = (result.stdout or '').strip()
    return value or None


def load_overrides(path):
    """Parsed override file, or an empty schema-1 document when missing."""
    if path.is_symlink():
        raise TmpError(f'Refusing to use a symlinked override config: {path}')
    for parent in path.parents:
        if parent.is_symlink():
            raise TmpError(f'Refusing a symbolic link in override config storage: {parent}')
    if not path.exists():
        return {'schema': SCHEMA, 'overrides': []}
    if not path.is_file():
        raise TmpError(f'Override config must be a regular file: {path}')
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TmpError(f'Override config could not be read ({error}). Preserve {path} and repair it.') from error
    if value.get('schema') != SCHEMA or not isinstance(value.get('overrides'), list):
        raise TmpError(f'Invalid override config in {path}; preserve and repair that file.')
    return value


def override_map(document):
    found = {}
    for item in document['overrides']:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str):
            raise TmpError('Each override must be an object with an id, a value, and a reason.')
        if item['id'] in found:
            raise TmpError(f'Override {item["id"]!r} is listed more than once.')
        found[item['id']] = item
    return found


def scratch_name(document):
    item = override_map(document).get(SCRATCH_ID)
    if item is None:
        return None
    value = item.get('value')
    reason = item.get('reason')
    if not isinstance(value, str) or not isinstance(reason, str) or not reason.strip():
        raise TmpError('The scratch override needs a directory name and a reason.')
    return value


def valid_scratch_name(name):
    return isinstance(name, str) and SCRATCH_NAME.fullmatch(name) and name not in RESERVED_SCRATCH_NAMES


def scratch_path(environ, name):
    return cache_home(environ) / name


def inside(path, ancestor):
    try:
        path.relative_to(ancestor)
        return True
    except ValueError:
        return False


def resolve_existing(path):
    try:
        return path.resolve()
    except OSError:
        return path


def scratch_forbidden_reason(environ, path):
    """Why this scratch path is refused, or None when it is allowed."""
    cache = resolve_existing(cache_home(environ))
    target = path if not path.exists() else resolve_existing(path)
    if path.is_symlink():
        return f'{path} is a symlink'
    if not inside(target, cache):
        return f'{path} is not under {cache}'
    home_dir = resolve_existing(home(environ))
    for label, relative in (('~/code', 'code'), ('~/Shared', 'Shared')):
        ancestor = resolve_existing(home_dir / relative)
        if inside(target, ancestor):
            return f'{path} is under {label}'
    for parent in [target, *target.parents]:
        if parent == home_dir:
            break
        git = parent / '.git'
        if git.exists() or git.is_symlink():
            return f'{path} is inside a git clone at {parent}'
    return None


def atomic_write(path, text, mode=0o600):
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise TmpError(f'Refusing a symbolic link in override config storage: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(text)
            os.fchmod(output.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def record_tmp_mount(document):
    """Ensure tmp.mount is listed; keep an existing valid reason."""
    items = override_map(document)
    current = items.get(TMP_MOUNT_ID)
    reason = TMP_MOUNT_REASON
    if (isinstance(current, dict) and isinstance(current.get('reason'), str)
            and current['reason'].strip()):
        reason = current['reason']
    items[TMP_MOUNT_ID] = {'id': TMP_MOUNT_ID, 'value': TMP_MOUNT_VALUE, 'reason': reason}
    ordered = []
    seen = set()
    for item in document['overrides']:
        ident = item.get('id') if isinstance(item, dict) else None
        if ident == TMP_MOUNT_ID:
            ordered.append(items[TMP_MOUNT_ID])
            seen.add(TMP_MOUNT_ID)
        elif ident:
            ordered.append(item)
            seen.add(ident)
    if TMP_MOUNT_ID not in seen:
        ordered.insert(0, items[TMP_MOUNT_ID])
    return {'schema': SCHEMA, 'overrides': ordered}


def mask_tmp_mount(host):
    if masked(host):
        return False
    result = host.run(['sudo', 'systemctl', 'mask', 'tmp.mount'])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        raise TmpError(
            'Could not mask tmp.mount'
            + (f': {detail}' if detail else '')
            + '. /tmp was left as it is. Retry from a terminal with administrator permission.'
        )
    if not masked(host):
        raise TmpError(
            'systemctl mask tmp.mount returned success, but '
            f'{host.tmp_mount_mask} is not a symlink to /dev/null. /tmp was left as it is.'
        )
    return True


def create_scratch(environ, name):
    if not valid_scratch_name(name):
        raise TmpError(
            f'Scratch directory name {name!r} is not allowed. Use one path component '
            'of letters, digits, dots, underscores, or hyphens, not groundwork, and not a path.'
        )
    path = scratch_path(environ, name)
    reason = scratch_forbidden_reason(environ, path)
    if reason:
        raise TmpError(
            f'Scratch {reason}. Choose a name that stays under ~/.cache and is not '
            'inside a git clone, ~/code, or ~/Shared.'
        )
    created = False
    if not path.exists():
        cache_home(environ).mkdir(mode=0o700, parents=True, exist_ok=True)
        path.mkdir(mode=0o700)
        created = True
    elif not path.is_dir() or path.is_symlink():
        raise TmpError(f'Scratch path {path} exists and is not a directory. It was left alone.')
    tag = path / 'CACHEDIR.TAG'
    if tag.is_symlink():
        raise TmpError(f'Refusing to replace a symlinked CACHEDIR.TAG: {tag}')
    if not tag.exists():
        tag.write_text(CACHEDIR_TAG)
        os.chmod(tag, 0o644)
    return path, created


def apply(host):
    document = load_overrides(overrides_path(host.environ))
    name = scratch_name(document)
    if name is not None and not valid_scratch_name(name):
        raise TmpError(
            f'Scratch directory name {name!r} is not allowed. Use one path component '
            'of letters, digits, dots, underscores, or hyphens, not groundwork, and not a path. '
            'Nothing was changed.'
        )
    if name is not None:
        reason = scratch_forbidden_reason(host.environ, scratch_path(host.environ, name))
        if reason:
            raise TmpError(
                f'Scratch {reason}. Choose a name that stays under ~/.cache and is not '
                'inside a git clone, ~/code, or ~/Shared. Nothing was changed.'
            )
    fstab_lines = fstab_tmp_lines(host.fstab)
    if fstab_lines:
        raise TmpError(
            '/etc/fstab has a /tmp line, which would defeat the tmp.mount mask. '
            'Remove that line; this layer never writes /etc/fstab. Nothing was changed.'
        )
    changed = mask_tmp_mount(host)
    if changed:
        print('Masked tmp.mount. /tmp will live on the disk root after reboot.')
    else:
        print('tmp.mount is already masked.')
    print('/etc/fstab was not changed.')
    try:
        source, overridden = effective_tmpfiles(host)
        if aging_ok(tmpfiles_tmp_entry(source)):
            print('systemd-tmpfiles still ages /tmp for 10 days with mode 1777.')
        else:
            where = '/etc/tmpfiles.d/tmp.conf' if overridden else '/usr/lib/tmpfiles.d/tmp.conf'
            print(
                f'{where} does not age /tmp for 10 days with mode 1777. '
                'Restore upstream systemd-tmpfiles; this layer does not ship a copy.',
                file=sys.stderr,
            )
    except TmpError as error:
        print(str(error), file=sys.stderr)
    if name is not None:
        path, created = create_scratch(host.environ, name)
        if created:
            print(f'Created {path} as the optional scratch directory. It is not synced.')
    document = record_tmp_mount(document)
    path = overrides_path(host.environ)
    text = json.dumps(document, indent=2) + '\n'
    try:
        unchanged = path.is_file() and not path.is_symlink() and path.read_text() == text
    except OSError:
        unchanged = False
    if not unchanged:
        atomic_write(path, text)
    return 0


def problems(host):
    """One line per problem with a known fix. Empty when this machine matches the policy."""
    found = []
    try:
        fstab_lines = fstab_tmp_lines(host.fstab)
    except TmpError as error:
        found.append(str(error))
        fstab_lines = ['unreadable']
    else:
        if fstab_lines:
            found.append(
                '/etc/fstab has a /tmp line, which would defeat the tmp.mount mask. '
                'Remove that line; this layer never writes /etc/fstab.'
            )
    if not masked(host):
        found.append(
            'tmp.mount is not masked, so /tmp stays a RAM tmpfs. Apply this layer, then reboot.'
        )
    else:
        fstype = tmp_fstype(host)
        if fstype == 'tmpfs':
            found.append('tmp.mount is masked, but /tmp is still tmpfs. Reboot to put /tmp on disk.')
    try:
        source, overridden = effective_tmpfiles(host)
        if source.is_symlink():
            found.append(
                f'Refusing to trust a symlinked tmpfiles file: {source}. '
                'Restore a regular upstream file; this layer does not ship a copy.'
            )
        elif not aging_ok(tmpfiles_tmp_entry(source)):
            if overridden:
                found.append(
                    '/etc/tmpfiles.d/tmp.conf overrides upstream aging of /tmp. '
                    'Remove that override so systemd-tmpfiles keeps 10 days and mode 1777. '
                    'This layer does not ship a copy.'
                )
            else:
                found.append(
                    'upstream systemd-tmpfiles no longer ages /tmp for 10 days with mode 1777. '
                    'Restore /usr/lib/tmpfiles.d/tmp.conf from systemd; this layer does not ship a copy.'
                )
    except TmpError as error:
        found.append(str(error))
    try:
        document = load_overrides(overrides_path(host.environ))
        items = override_map(document)
    except TmpError as error:
        found.append(str(error))
        return found
    recorded = items.get(TMP_MOUNT_ID)
    if not (isinstance(recorded, dict) and recorded.get('value') == TMP_MOUNT_VALUE
            and isinstance(recorded.get('reason'), str) and recorded['reason'].strip()):
        found.append(
            'groundwork override config does not record that tmp.mount is masked, with a reason. '
            'Apply this layer.'
        )
    name = None
    try:
        name = scratch_name(document)
    except TmpError as error:
        found.append(str(error))
    if name is not None:
        if not valid_scratch_name(name):
            found.append(
                f'scratch directory name {name!r} is not allowed. Use one path component '
                'under ~/.cache, not a git clone or ~/code.'
            )
        else:
            path = scratch_path(host.environ, name)
            reason = scratch_forbidden_reason(host.environ, path)
            if reason:
                found.append(
                    f'scratch {reason}. Change the name in the override config so it stays '
                    'under ~/.cache and not inside a git clone, ~/code, or ~/Shared.'
                )
            elif not path.is_dir() or path.is_symlink():
                found.append(f'scratch directory {path} is missing. Apply this layer.')
            elif not isinstance(items[SCRATCH_ID].get('reason'), str) or not items[SCRATCH_ID]['reason'].strip():
                found.append(
                    'scratch is named in the override config without a reason. Add a reason; '
                    'the override file lists only settings that differ from upstream defaults.'
                )
    return found


def doctor_ok_line(host):
    parts = ['tmp.mount masked', 'no /tmp in fstab', 'systemd-tmpfiles ages /tmp for 10 days']
    try:
        document = load_overrides(overrides_path(host.environ))
        name = scratch_name(document)
    except TmpError:
        name = None
    if name and valid_scratch_name(name):
        parts.append(f'scratch {scratch_path(host.environ, name)}')
    return 'tmp: ok (' + ', '.join(parts) + ')'


def doctor(host, brief=False):
    found = problems(host)
    if brief:
        for line in found:
            print(f'tmp: {line}')
        return 0
    if found:
        print(f'tmp: {len(found)} problem{"s" if len(found) != 1 else ""}')
        for line in found:
            print(f'  {line}')
        return 1
    print(doctor_ok_line(host))
    return 0


def main(argv=None, environ=None, run=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    environ = dict(os.environ if environ is None else environ)
    if argv in ([], ['--help'], ['-h']):
        print('Usage: groundwork-tmp apply|doctor [--brief]|needed')
        print('Mask tmp.mount so /tmp is on disk, check systemd-tmpfiles aging, and create optional scratch.')
        return 0 if argv else 2
    try:
        host = Host(environ, run=run)
        if argv == ['apply']:
            return apply(host)
        if argv == ['doctor']:
            return doctor(host, brief=False)
        if argv == ['doctor', '--brief']:
            return doctor(host, brief=True)
        if argv == ['needed']:
            # 0: apply still needs administrator permission to mask tmp.mount.
            return 0 if not masked(host) else 1
    except TmpError as error:
        print(str(error), file=sys.stderr)
        return 1
    print('Usage: groundwork-tmp apply|doctor [--brief]|needed', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
