#!/usr/bin/env python3
# Storage defaults (decision 0023, issue #145).
"""Apply and check TRIM through LUKS and plocate prunes.

  groundwork-storage.py needed
  groundwork-storage.py apply

`needed` is read-only. It exits 0 when a privileged step is still pending and 1
when it is not. `apply` is idempotent. It refuses to change the real machine
unless GROUNDWORK_STORAGE_APPLY=1, which ./install.sh and update-all set.
`groundwork-doctor` imports the checks and does not call `apply`.

This file does not edit Limine, crypttab, or /etc/updatedb.conf. Discard
reveals which encrypted blocks are free; that tradeoff is accepted so TRIM can
run. The plocate drop-in adds names and paths. It does not replace the
distribution lists. A btrfs directory property cannot choose a zstd level, so
this file does not set one and does not change the mount. Doctor reports the
mount's compress option.
"""
import argparse
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

MAPPER = '/dev/mapper/root'
CRYPT_NAME = 'root'
ZERO_GRANULARITY = {'0', '0B', '0b'}
EXTRA_NAMES = ('node_modules', '.turbo', '.next')
PRUNE_RELATIVE = ('.t3/worktrees', '.local/share/pnpm')
DROPIN_RELATIVE = Path('etc/systemd/system/plocate-updatedb.service.d/groundwork-prunes.conf')
STAMP_RELATIVE = Path('var/lib/groundwork/luks-discard-unsupported')
TEMPLATE = Path(__file__).resolve().parent.parent / 'config' / 'plocate-updatedb.service.d.conf'
PROBE_SECONDS = 10
# A person may be asked for the disk passphrase after a long update.
# Sibling prompts in this layer allow 300 seconds. The timer start below is
# not that wait.
CRYPTSETUP_SECONDS = 300
FSTRIM_SECONDS = 30
INSTALL_SECONDS = 30
# update-all stops groundwork-doctor --brief at 15 seconds and discards that
# output. Probes we can signal share this budget. A path check, a command
# stuck in uninterruptible sleep, or a root child we cannot signal is outside it.
DOCTOR_SECONDS = 8
HOME_CHARS = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/+-')
GRANULARITY = re.compile(r'(?P<number>\d+(?:\.\d+)?)(?P<unit>(?:[KMGTPE]i?B?)|B)?\Z', re.IGNORECASE)
HOST_BIN_PREFIXES = (
    '/bin/', '/sbin/', '/usr/bin/', '/usr/sbin/', '/usr/local/bin/', '/usr/local/sbin/',
    '/opt/homebrew/bin/', '/run/',
)
RECOGNIZED_TIMERS = {
    'enabled', 'enabled-runtime', 'disabled', 'masked', 'masked-runtime', 'static',
    'indirect', 'alias', 'linked', 'linked-runtime', 'generated', 'transient',
}
UNSUPPORTED_DISCARDS = (
    'only supported for luks2',
    'integrity protection',
    'cannot use discard',
    'discards are not supported',
    'discard is not supported',
)
TRIM_OK = 'ok (discards on /dev/mapper/root, fstrim.timer enabled)'
COMPRESSION_OK = 'ok (a directory property cannot choose a zstd level, so the mount stays compress=zstd:3)'
REFUSED = object()


class Result:
    def __init__(self, returncode, stdout='', stderr=''):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class Deadline:
    """A shared clock for one doctor pass. Apply does not set one."""

    def __init__(self, seconds):
        self.ends = time.monotonic() + seconds

    def remaining(self):
        return self.ends - time.monotonic()


class Machine:
    def __init__(self, environ, root, run, allow_live=False, real_commands=False):
        self.environ = environ
        self.root = Path(root)
        self.run = run
        self.allow_live = allow_live
        self.real_commands = real_commands

    def path(self, relative):
        return self.root / relative


def storage_root(environ):
    """A fixture root is honored only while GROUNDWORK_TESTING=1. Otherwise it is refused."""
    testing = environ.get('GROUNDWORK_TESTING') == '1'
    chosen = environ.get('GROUNDWORK_STORAGE_ROOT') or environ.get('INSTALL_FIXTURE')
    if chosen and not testing:
        return REFUSED
    if chosen:
        return Path(chosen)
    if testing:
        return None
    return Path('/')


def safe_home(environ):
    """Absolute home with no spaces or shell metacharacters, or None."""
    raw = environ.get('HOME', '')
    if len(raw) > 1 and raw.endswith('/'):
        raw = raw.rstrip('/')
    if not raw or not raw.startswith('/') or '//' in raw:
        return None
    parts = raw.split('/')
    if any(part in ('', '..') for part in parts[1:]):
        return None
    if any(char not in HOME_CHARS for char in raw):
        return None
    return raw


def locate_ok(home):
    worktrees = f'{home}/.t3/worktrees'
    pnpm = f'{home}/.local/share/pnpm'
    return f'ok (also skips node_modules, .turbo, .next, {worktrees}, and {pnpm})'


def render_dropin(template, home):
    if '@HOME@' not in template:
        raise ValueError('the plocate drop-in template has no @HOME@ placeholder')
    if not safe_home({'HOME': home}):
        raise ValueError('home is not safe to write into the plocate drop-in')
    return template.replace('@HOME@', home)


def unit_file(machine, name):
    for relative in (f'usr/lib/systemd/system/{name}', f'lib/systemd/system/{name}'):
        path = machine.path(relative)
        if path.is_file():
            return path
    return None


def probe(machine, args, timeout=PROBE_SECONDS):
    """(Result or None, missing). missing is True when the command is not installed."""
    budget = getattr(machine, 'deadline', None)
    if budget is not None:
        timeout = min(timeout, budget.remaining())
        if timeout < 0.1:
            return Result(124, '', 'The storage check ran out of time.'), False
    try:
        return machine.run(args, sudo=False, timeout=timeout), False
    except FileNotFoundError:
        return None, True


def first_line(result):
    if result is None or result.returncode != 0:
        return ''
    text = result.stdout.strip()
    return text.splitlines()[0].strip() if text else ''


def mapper_state(machine):
    """How / relates to the LUKS root this layer knows how to open.

    The third value is the mount option string, or None when it was not read.
    """
    result, missing = probe(machine, ['findmnt', '-n', '-o', 'SOURCE,OPTIONS', '/'])
    if missing:
        return 'missing', '', None
    line = first_line(result)
    if not line:
        return 'failed', '', None
    source, _, options = line.partition(' ')
    if source == MAPPER or source.startswith(MAPPER + '['):
        return 'root', source, options
    return 'other', source, options


def classify_granularity(token):
    """'off', 'on', or 'unrecognized'. A size lsblk did not print is not on."""
    if token in ZERO_GRANULARITY:
        return 'off'
    match = GRANULARITY.fullmatch(token)
    if match is None:
        return 'unrecognized'
    if float(match.group('number')) == 0:
        return 'off'
    return 'on'


def discards_cannot_persist(result):
    """True when cryptsetup says this header cannot store allow-discards."""
    text = f'{result.stderr}\n{result.stdout}'.lower()
    return any(needle in text for needle in UNSUPPORTED_DISCARDS)


def crypt_type(machine):
    result, missing = probe(machine, ['lsblk', '-ndo', 'TYPE', MAPPER])
    if missing:
        return 'missing', ''
    kind = first_line(result)
    if not kind:
        return 'failed', ''
    return 'ok', kind


def discard_granularity(machine):
    result, missing = probe(machine, ['lsblk', '-ndo', 'DISC-GRAN', MAPPER])
    if missing:
        return 'missing', ''
    token = first_line(result).split()[0] if first_line(result) else ''
    if not token or classify_granularity(token) == 'unrecognized':
        return 'failed', token
    return 'ok', token


def timer_enabled(machine):
    result, missing = probe(machine, ['systemctl', 'is-enabled', 'fstrim.timer'])
    if missing:
        return 'missing'
    if result.returncode == 124:
        return 'timed-out'
    text = result.stdout.strip()
    if text in ('enabled', 'enabled-runtime') and result.returncode == 0:
        return text
    if text in RECOGNIZED_TIMERS - {'enabled', 'enabled-runtime'}:
        return text
    return 'unknown'


def dropin_state(machine, home):
    """'missing-unit', 'bad-home', 'bad-template', 'missing', 'differs', 'unsafe', 'unloaded', 'unconfirmed', 'systemctl-missing', or 'ok'."""
    if unit_file(machine, 'plocate-updatedb.service') is None:
        return 'missing-unit'
    if home is None:
        return 'bad-home'
    try:
        expected = render_dropin(TEMPLATE.read_text(encoding='utf-8'), home)
    except OSError:
        return 'bad-template'
    except ValueError:
        return 'bad-home'
    path = machine.path(DROPIN_RELATIVE)
    try:
        current = path.read_text(encoding='utf-8') if path.is_file() else None
    except OSError:
        return 'differs'
    if current is None:
        return 'missing'
    if current != expected:
        return 'differs'
    try:
        mode = path.stat().st_mode
    except OSError:
        return 'differs'
    if mode & 0o022:
        return 'unsafe'
    if machine.root.resolve() == Path('/').resolve() and path.stat().st_uid != 0:
        return 'unsafe'
    loaded = dropin_loaded(machine, path)
    if loaded == 'loaded':
        return 'ok'
    if loaded == 'missing':
        return 'systemctl-missing'
    if loaded == 'failed':
        return 'unconfirmed'
    return 'unloaded'


def dropin_loaded(machine, path):
    """'loaded', 'unloaded', 'missing', or 'failed'. A question we cannot answer is not loaded."""
    result, missing = probe(
        machine, ['systemctl', 'show', '-p', 'DropInPaths', '--value', 'plocate-updatedb.service'])
    if missing or result is None:
        return 'missing'
    if result.returncode != 0:
        return 'failed'
    if str(path) in result.stdout.split():
        return 'loaded'
    return 'unloaded'


def gather(machine):
    home = safe_home(machine.environ)
    where, source, options = mapper_state(machine)
    kind_state, kind = ('skipped', '')
    gran_state, gran = ('skipped', '')
    if where == 'root':
        kind_state, kind = crypt_type(machine)
        if kind_state == 'ok' and kind == 'crypt':
            gran_state, gran = discard_granularity(machine)
    return {
        'home': home,
        'where': where,
        'source': source,
        'options': options,
        'kind_state': kind_state,
        'kind': kind,
        'gran_state': gran_state,
        'gran': gran,
        'discard_unsupported': machine.path(STAMP_RELATIVE).is_file(),
        'timer_unit': unit_file(machine, 'fstrim.timer') is not None,
        'timer': timer_enabled(machine) if unit_file(machine, 'fstrim.timer') is not None else 'absent',
        'dropin': dropin_state(machine, home),
    }


def trim_problems(state):
    problems = []
    where = state['where']
    if where == 'other':
        return []
    elif where == 'missing':
        problems.append('findmnt is missing, so discards were not checked. Install it with: sudo pacman -S --needed util-linux')
    elif where == 'failed':
        problems.append('findmnt could not name the root filesystem, so discards were not checked.')
    elif state['kind_state'] == 'missing':
        problems.append('lsblk is missing, so discards were not checked. Install it with: sudo pacman -S --needed util-linux')
    elif state['kind_state'] == 'failed':
        problems.append(f'lsblk could not read {MAPPER}, so discards were not checked.')
    elif state['kind'] != 'crypt':
        return []
    elif state['gran_state'] == 'missing':
        problems.append('lsblk is missing, so the discard granularity was not checked. Install it with: sudo pacman -S --needed util-linux')
    elif state['gran_state'] == 'failed':
        problems.append(f'lsblk did not report a discard granularity for {MAPPER}, so discards were not checked.')
    elif state.get('discard_unsupported') and classify_granularity(state['gran']) == 'off':
        pass
    elif classify_granularity(state['gran']) == 'off':
        problems.append(
            f'discards are off on {MAPPER} (discard granularity is {state["gran"]}). '
            'Discard reveals which encrypted blocks are free. '
            f'Enable it with: sudo cryptsetup refresh --allow-discards --persistent {CRYPT_NAME}')
    if not state['timer_unit']:
        problems.append('fstrim.timer is not installed, so scheduled TRIM is not enabled. Install it with: sudo pacman -S --needed util-linux')
    elif state['timer'] == 'missing':
        problems.append('systemctl is missing, so fstrim.timer was not checked. Install it with: sudo pacman -S --needed systemd')
    elif state['timer'] in ('timed-out', 'unknown'):
        problems.append('systemctl did not show whether fstrim.timer is enabled, so it was not changed.')
    elif state['timer'] == 'disabled':
        if fstrim_is_safe(state):
            problems.append('fstrim.timer is not enabled. Enable it with: sudo systemctl enable --now fstrim.timer')
        elif discards_are_off(state):
            problems.append('fstrim.timer stays disabled until discards are on, so the first trim is not a catch-up while discards are blocked.')
        elif state.get('discard_unsupported'):
            pass
        else:
            problems.append('fstrim.timer stays disabled until discards on /dev/mapper/root are confirmed.')
    return problems


def discards_are_off(state):
    """The LUKS root is open, discards are still zero, and a refresh has not already failed to change that."""
    if state.get('discard_unsupported'):
        return False
    return (state['where'] == 'root' and state['kind'] == 'crypt' and state['gran_state'] == 'ok'
            and classify_granularity(state['gran']) == 'off')


def fstrim_is_safe(state):
    """True only when this LUKS root already has a non-zero discard granularity."""
    return (state['where'] == 'root' and state['kind'] == 'crypt' and state['gran_state'] == 'ok'
            and classify_granularity(state['gran']) == 'on')


def locate_problems(state):
    dropin = state['dropin']
    if dropin == 'missing-unit':
        return ['plocate-updatedb.service is not installed, so the extra prunes are not applied. Install it with: sudo pacman -S --needed plocate']
    if dropin == 'bad-template':
        return ['the plocate drop-in template is missing from the checkout, so the extra prunes were not written.']
    if dropin == 'bad-home':
        return ['the home directory is not a plain absolute path, so the plocate prunes were not written.']
    if dropin == 'unsafe':
        return ['the plocate drop-in is group-writable, world-writable, or not owned by root. Rerun update-all.']
    if dropin == 'systemctl-missing':
        return ['systemctl is missing, so the plocate drop-in was not confirmed. Install it with: sudo pacman -S --needed systemd']
    if dropin == 'unconfirmed':
        return ['systemctl could not show whether the plocate drop-in is loaded. Rerun update-all.']
    if dropin == 'unloaded':
        return ['the plocate drop-in is on disk, but systemd has not loaded it. Rerun update-all.']
    if dropin in ('missing', 'differs'):
        home = state['home']
        return ['plocate still uses only the distribution prunes. '
                f'Rerun update-all to add {", ".join(EXTRA_NAMES)}, and to skip {home}/.t3/worktrees and {home}/.local/share/pnpm.']
    return []


def trim_ok_line(state):
    if state['where'] == 'other':
        return f'ok (root is {state["source"]}, not {MAPPER}, so TRIM through LUKS does not apply)'
    if state['kind_state'] == 'ok' and state['kind'] != 'crypt':
        return f'ok ({MAPPER} is type {state["kind"]}, not crypt, so TRIM through LUKS does not apply)'
    if state.get('discard_unsupported') and classify_granularity(state['gran']) == 'off':
        return ('ok (discards stay off, so the refresh is not retried; '
                'delete /var/lib/groundwork/luks-discard-unsupported to try again)')
    if state['timer'] == 'enabled':
        return TRIM_OK
    if state['timer'] in ('absent', 'disabled', 'missing'):
        return TRIM_OK
    return f'ok (discards on {MAPPER}, fstrim.timer is {state["timer"]} and was left alone)'


def compression_line(options):
    """Say what the mount is doing. Never claim compress=zstd:3 unless findmnt did."""
    if options is None:
        return ('ok (a directory property cannot choose a zstd level, and the mount options '
                'were not read, so nothing was changed)')
    parts = [part for part in options.split(',') if part]
    if 'compress=zstd:3' in parts:
        return COMPRESSION_OK
    compress = next((part for part in parts if part.startswith('compress=')), '')
    if compress:
        return (f'ok (a directory property cannot choose a zstd level; the mount says {compress} '
                'and was not changed)')
    return ('ok (a directory property cannot choose a zstd level; this mount does not set '
            'compress=zstd:3 and was not changed)')


def sections(state):
    """(title, problems, ok line) for the three doctor checks."""
    home = state['home'] or ''
    return (
        ('TRIM through LUKS', trim_problems(state), trim_ok_line(state)),
        ('Locate index', locate_problems(state), locate_ok(home) if home else 'ok'),
        ('Compression', [], compression_line(state.get('options'))),
    )


def format_report(state, brief=False):
    chunks = []
    failed = False
    for title, problems, ok in sections(state):
        if problems:
            failed = True
            if brief:
                chunks.extend(f'{title}: {problem}' for problem in problems)
            else:
                noun = 'problem' if len(problems) == 1 else 'problems'
                chunks.append(f'{title}: {len(problems)} {noun}')
                chunks.extend(f'  {problem}' for problem in problems)
        elif not brief:
            chunks.append(f'{title}: {ok}')
    return '\n'.join(chunks) + ('\n' if chunks else ''), failed


def privileged_actions(state):
    """Commands a root step still has to run. Empty when those steps are done or do not apply."""
    actions = []
    refreshing = discards_are_off(state)
    if refreshing:
        actions.append(('trim', ['cryptsetup', 'refresh', '--allow-discards', '--persistent', CRYPT_NAME], CRYPTSETUP_SECONDS))
    # Queue the timer with the refresh so a successful refresh still starts it
    # in this run. apply_storage drops it when that refresh does not succeed.
    if state['timer_unit'] and state['timer'] == 'disabled' and (fstrim_is_safe(state) or refreshing):
        actions.append(('fstrim', ['systemctl', 'enable', '--now', 'fstrim.timer'], FSTRIM_SECONDS))
    if state['dropin'] in ('missing', 'differs', 'unsafe', 'unloaded', 'unconfirmed'):
        actions.append(('locate', None, INSTALL_SECONDS))
    return actions


def blocking_probe_failures(state):
    """Failed or timed-out probes. A missing command is not one of these."""
    reasons = []
    if state['where'] == 'failed':
        reasons.append('findmnt could not name the root filesystem, so storage defaults were not applied.')
    if state['kind_state'] == 'failed':
        reasons.append(f'lsblk could not read {MAPPER}, so storage defaults were not applied.')
    if state['gran_state'] == 'failed':
        reasons.append(f'lsblk did not report a discard granularity for {MAPPER}, so storage defaults were not applied.')
    if state['timer'] in ('timed-out', 'unknown'):
        reasons.append('systemctl did not show whether fstrim.timer is enabled, so it was not changed.')
    return reasons


def mutations_allowed(machine):
    """Real commands may change a machine only when the storage root is /."""
    if not machine.real_commands:
        return True
    try:
        return machine.root.resolve() == Path('/').resolve()
    except OSError:
        return False


def terminal_available():
    """True when /dev/tty can take a passphrase prompt."""
    try:
        fd = os.open('/dev/tty', os.O_RDWR)
    except OSError:
        return False
    os.close(fd)
    return True


def stop_child(process, group):
    """Stop a timed-out child. A root child cannot be killed or waited out from here."""
    try:
        if group:
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        return
    except PermissionError:
        print('Could not stop a privileged child; it may still be running.', file=sys.stderr)
        return
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def terminal_stdio():
    """The controlling terminal, or None when this process has none."""
    try:
        return os.open('/dev/tty', os.O_RDWR)
    except OSError:
        return None


def finished_code(process):
    code = process.returncode
    if code is None:
        return 1
    if code < 0:
        return 128 - code
    return code


def is_host_binary(path):
    """True for a system binary. A fixture root must not run one."""
    try:
        resolved = str(Path(path).resolve())
    except OSError:
        return True
    return any(resolved == prefix[:-1] or resolved.startswith(prefix) for prefix in HOST_BIN_PREFIXES)


def run_process(command, timeout, environ, terminal, keep_session=False):
    """Run one command.

    A new session has no controlling terminal, so stock sudo ignores the
    per-terminal credential apply-layer just primed. Privileged commands stay
    in this session and use their own process group, which can still be
    signalled without signalling this process. The passphrase prompt is given
    the terminal itself, not whatever stdin the parent inherited.
    """
    tty_fd = None
    previous = None
    if terminal:
        tty_fd = terminal_stdio()
        if tty_fd is None:
            return Result(1, '', 'No terminal is available to ask for a passphrase.')
        try:
            previous = os.tcgetpgrp(tty_fd)
        except OSError:
            previous = None
    process = None
    try:
        try:
            if terminal:
                # process_group keeps the session, so sudo's per-terminal
                # credential still matches, and it does not run Python between
                # fork and exec. The command may be stopped by the terminal
                # until this process makes that group the foreground.
                process = subprocess.Popen(
                    command, stdin=tty_fd, stdout=tty_fd, stderr=tty_fd, process_group=0)
                try:
                    os.tcsetpgrp(tty_fd, process.pid)
                except OSError:
                    pass
                try:
                    os.kill(process.pid, signal.SIGCONT)
                except OSError:
                    pass
            elif keep_session:
                process = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, env=environ, process_group=0)
            else:
                process = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, env=environ, start_new_session=True)
            if terminal:
                process.wait(timeout=timeout)
                return Result(finished_code(process), '', '')
            stdout, stderr = process.communicate(timeout=timeout)
            return Result(finished_code(process), stdout, stderr)
        except subprocess.TimeoutExpired:
            stop_child(process, group=True)
            return Result(124, '', f'Timed out after {timeout} seconds.')
        except KeyboardInterrupt:
            if process is not None:
                stop_child(process, group=True)
            return Result(130, '', 'Stopped.')
    finally:
        if tty_fd is not None and previous is not None:
            try:
                os.tcsetpgrp(tty_fd, previous)
            except OSError:
                pass
        if tty_fd is not None:
            os.close(tty_fd)


def default_run(environ, root):
    path = environ.get('PATH', os.defpath)
    try:
        fixture = Path(root).resolve() != Path('/').resolve()
    except OSError:
        fixture = True

    def consider(binary, label):
        if binary is None or (fixture and is_host_binary(binary)):
            raise FileNotFoundError(label)
        return binary

    def run(args, sudo=False, timeout=PROBE_SECONDS, terminal=False):
        if sudo and os.geteuid() != 0:
            command = [consider(shutil.which('sudo', path=path), 'sudo')]
            # No terminal: fail immediately instead of waiting for a prompt
            # that cannot be answered. The caller prints the manual command.
            if not terminal_available():
                command.append('-n')
            command.extend(args)
        else:
            command = [consider(shutil.which(args[0], path=path), args[0]), *args[1:]]
        if terminal and not terminal_available():
            return Result(1, '', 'No terminal is available to ask for a passphrase.')
        keep_session = terminal or (sudo and terminal_available())
        return run_process(command, timeout, environ, terminal, keep_session=keep_session)

    return run


def machine_from(environ, run=None, allow_live=False):
    root = storage_root(environ)
    if root is None:
        return None
    if root is REFUSED:
        return REFUSED
    return Machine(environ, root, run or default_run(environ, root), allow_live=allow_live,
                   real_commands=run is None)


def needs_privilege(machine):
    if machine is None or machine is REFUSED:
        return False
    return bool(privileged_actions(gather(machine)))


def install_dropin(machine, home):
    content = render_dropin(TEMPLATE.read_text(encoding='utf-8'), home)
    destination = machine.path(DROPIN_RELATIVE)
    handle = tempfile.NamedTemporaryFile('w', encoding='utf-8', prefix='groundwork-plocate-', delete=False)
    try:
        handle.write(content)
        handle.close()
        result = machine.run(
            ['install', '-D', '-o', 'root', '-g', 'root', '-m', '0644', handle.name, str(destination)],
            sudo=True, timeout=INSTALL_SECONDS)
    finally:
        handle.close()
        os.unlink(handle.name)
    if result.returncode != 0:
        return result
    return machine.run(['systemctl', 'daemon-reload'], sudo=True, timeout=INSTALL_SECONDS)


def quoted(args, sudo):
    rendered = ' '.join(shlex.quote(arg) for arg in args)
    return f'sudo {rendered}' if sudo else rendered


def write_stamp(machine):
    handle = tempfile.NamedTemporaryFile('w', encoding='utf-8', prefix='groundwork-discard-', delete=False)
    try:
        handle.write('discards stay off, so the refresh is not retried.\n')
        handle.close()
        return machine.run(
            ['install', '-D', '-o', 'root', '-g', 'root', '-m', '0644', handle.name, str(machine.path(STAMP_RELATIVE))],
            sudo=True, timeout=INSTALL_SECONDS)
    finally:
        handle.close()
        os.unlink(handle.name)


def apply_storage(machine):
    """Return 0 when every applicable step is done. A test root never needs allow_live."""
    if machine is None:
        return 0
    if machine is REFUSED:
        print('GROUNDWORK_STORAGE_ROOT and INSTALL_FIXTURE are test-only. Unset them and rerun; nothing was changed.',
              file=sys.stderr)
        return 64
    if machine.root.resolve() == Path('/').resolve() and not machine.allow_live:
        print('Refusing to apply storage defaults on this machine without GROUNDWORK_STORAGE_APPLY=1. '
              'Run ./install.sh or update-all.', file=sys.stderr)
        return 1
    state = gather(machine)
    failed = False
    for reason in blocking_probe_failures(state):
        print(reason, file=sys.stderr)
        failed = True
    if state['dropin'] == 'bad-template':
        print('The plocate drop-in template is missing from the checkout, so the extra prunes were not written.',
              file=sys.stderr)
        failed = True
    if state['home'] is None and state['dropin'] == 'bad-home':
        print('The home directory is not a plain absolute path, so the plocate prunes were not written.',
              file=sys.stderr)
        failed = True
    actions = privileged_actions(state)
    if actions and not mutations_allowed(machine):
        print('Storage commands are not run when the storage root is a fixture. Nothing was changed.',
              file=sys.stderr)
        return 64
    trim_failed = False
    for kind, command, timeout in actions:
        if kind == 'fstrim' and trim_failed:
            print('Leaving fstrim.timer disabled because discards are still off.', file=sys.stderr)
            failed = True
            continue
        try:
            if kind == 'locate':
                print('plocate will also skip node_modules, .turbo, .next, worktrees, and the pnpm store.')
                result = install_dropin(machine, state['home'])
            elif kind == 'trim':
                shown = quoted(command, sudo=True)
                if not terminal_available():
                    print('No terminal is available, so the LUKS header was not changed. '
                          f'Run this from a terminal: {shown}', file=sys.stderr)
                    failed = True
                    trim_failed = True
                    continue
                print(f'Enabling discards on {MAPPER}. Discard reveals which encrypted blocks are free.', flush=True)
                print('This stores allow-discards in the LUKS2 header. If that header already has other flags, '
                      'they can be replaced. Read the Flags line from cryptsetup luksDump before the first run.',
                      flush=True)
                print(f'The passphrase wait is {timeout} seconds. A timeout fails this run. '
                      'A root cryptsetup that cannot be stopped may still be running.', flush=True)
                print(f'Running: {shown}', flush=True)
                result = machine.run(command, sudo=True, timeout=timeout, terminal=True)
            else:
                print('Enabling fstrim.timer. This waits for the timer to start, not for the trim to finish.',
                      flush=True)
                print(f'Running: {quoted(command, sudo=True)}', flush=True)
                result = machine.run(command, sudo=True, timeout=timeout)
        except FileNotFoundError as error:
            print(f'{kind} was not applied. {error} is missing.', file=sys.stderr)
            failed = True
            if kind == 'trim':
                trim_failed = True
            continue
        if result.returncode == 130:
            print('Stopped. This storage step did not finish.', file=sys.stderr)
            return 130
        if kind == 'trim' and result.returncode == 0:
            gran_state, gran = discard_granularity(machine)
            if not (gran_state == 'ok' and classify_granularity(gran) == 'on'):
                trim_failed = True
                failed = True
                if gran_state == 'ok' and classify_granularity(gran) == 'off':
                    record_unsupported(machine, 'The refresh finished, but the discard granularity is still zero.')
                else:
                    print('The discard granularity could not be read after the refresh, so fstrim.timer stays disabled.',
                          file=sys.stderr)
                continue
        if kind == 'trim' and result.returncode != 0 and discards_cannot_persist(result):
            trim_failed = True
            failed = True
            record_unsupported(machine, 'This header cannot store allow-discards.')
            continue
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            extra = f' Run: {quoted(command, sudo=True)}.' if command else ''
            print(f'{kind} was not applied.{extra} {detail}'.rstrip(), file=sys.stderr)
            failed = True
            if kind == 'trim':
                trim_failed = True
    return 1 if failed else 0


def record_unsupported(machine, happened):
    """Record that the refresh must not run again, and say so only after the record exists."""
    stamp = write_stamp(machine)
    if stamp.returncode != 0:
        print(f'{happened} That was not recorded, so the next run will ask again.', file=sys.stderr)
        detail = stamp.stderr.strip()
        if detail:
            print(detail, file=sys.stderr)
        return False
    print(f'{happened} It will not be retried.', file=sys.stderr)
    print('Delete /var/lib/groundwork/luks-discard-unsupported to try again.', file=sys.stderr)
    return True


def explain_lines(state):
    if not discards_are_off(state):
        return []
    shown = quoted(['cryptsetup', 'refresh', '--allow-discards', '--persistent', CRYPT_NAME], sudo=True)
    return [
        'Discards are still off. The disk passphrase is separate from the administrator password.',
        f'Without a terminal, the LUKS header is not changed. Run this from a terminal: {shown}',
    ]


def explain(machine):
    if machine is None:
        return 0
    if machine is REFUSED:
        print('GROUNDWORK_STORAGE_ROOT and INSTALL_FIXTURE are test-only. Unset them and rerun; nothing was changed.',
              file=sys.stderr)
        return 64
    for line in explain_lines(gather(machine)):
        print(line)
    return 0


def main(argv=None, environ=None):
    parser = argparse.ArgumentParser(prog='groundwork-storage')
    parser.add_argument('command', choices=('needed', 'apply', 'explain'))
    args = parser.parse_args(argv)
    environ = dict(os.environ if environ is None else environ)
    allow_live = environ.get('GROUNDWORK_STORAGE_APPLY') == '1' and args.command == 'apply'
    machine = machine_from(environ, allow_live=allow_live)
    if machine is REFUSED:
        print('GROUNDWORK_STORAGE_ROOT and INSTALL_FIXTURE are test-only. Unset them and rerun; nothing was changed.',
              file=sys.stderr)
        return 64
    if args.command == 'explain':
        return explain(machine)
    if args.command == 'needed':
        return 0 if needs_privilege(machine) else 1
    try:
        return apply_storage(machine)
    except KeyboardInterrupt:
        print('Stopped. This storage step did not finish.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
