#!/usr/bin/env python3
"""Groundwork Omarchy updater. Groundwork owns this layer; Omarchy owns the OS."""
import fcntl
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

MARKER = '# Groundwork Omarchy updater'
CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'groundwork/omarchy.json'
STATE = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'groundwork'
KEYD_CONFIG = Path('/etc/keyd/default.conf')


class UpdateError(Exception):
    pass


def check_path(path):
    for parent in [*reversed(path.parents), path]:
        if parent.is_symlink():
            raise UpdateError(f'Refusing a symbolic link in updater storage: {parent}')


def private_parent(path):
    check_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def transaction():
    private_parent(STATE / 'update.lock')
    with (STATE / 'update.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise UpdateError('Another Groundwork update or setup is running. Let it finish, then retry.') from error
        yield lock


def verify_lock():
    try:
        fd = int(os.environ['GROUNDWORK_OMARCHY_LOCK_FD'])
        expected = (STATE / 'update.lock').stat()
        inherited = os.fstat(fd)
        if (expected.st_dev, expected.st_ino) != (inherited.st_dev, inherited.st_ino):
            raise UpdateError('The inherited update lock has a different identity.')
        with (STATE / 'update.lock').open('r') as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
        raise UpdateError('The inherited update lock is not held.')
    except (KeyError, ValueError, OSError) as error:
        raise UpdateError('Run ./install.sh or the installed update command; direct apply requires its transaction lock.') from error


def atomic_write(path, content, mode=0o600, owned=False):
    private_parent(path)
    if path.exists() and owned and MARKER not in path.read_text():
        raise UpdateError(f'An existing command belongs to another setup: {path}. Preserve it before installing this updater.')
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(content)
            os.fchmod(output.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def settings(required=True):
    private_parent_check = [*CONFIG.parents, CONFIG]
    if any(path.is_symlink() for path in private_parent_check):
        raise UpdateError('Updater settings must use regular directories and a regular file.')
    if not CONFIG.exists():
        if required:
            raise UpdateError('Run ./install.sh once from your Groundwork Omarchy checkout to save setup choices and install the update commands.')
        return None
    value = json.loads(CONFIG.read_text())
    if (value.get('schema') != 1 or type(value.get('keyd')) is not bool
            or type(value.get('screen_sharing')) is not bool
            or not isinstance(value.get('source'), str)
            or not isinstance(value.get('obsidian_vault'), str)
            or '\n' in value['obsidian_vault']):
        raise UpdateError(f'Invalid updater settings in {CONFIG}; preserve and repair that file before updating.')
    return value


def validate_registration(source):
    source = Path(source).resolve()
    previous = settings(False)
    if previous and Path(previous['source']).resolve() != source:
        raise UpdateError('Another checkout owns this setup. Use its update commands; moving the source requires reviewing omarchy.json first.')
    for path in [Path.home() / '.local/lib/groundwork/omarchy-update.py',
                 Path.home() / '.local/bin/groundwork-update', Path.home() / '.local/bin/update-all']:
        check_path(path)
        if path.exists() and (not path.is_file() or MARKER not in path.read_text()):
            raise UpdateError(f'An existing command belongs to another setup: {path}. Preserve it before installing this updater.')
    return previous


def register(source, keyd, screen_sharing, vault):
    source = Path(source).resolve()
    previous = validate_registration(source)
    value = dict(previous or {}, schema=1, source=str(source), keyd=keyd == '1',
                 screen_sharing=screen_sharing == '1', obsidian_vault=vault)
    if value['keyd']:
        value['keyd_sha256'] = hashlib.sha256((source / 'keyd/default.conf').read_bytes()).hexdigest()
    value.pop('pending_keyd', None)
    atomic_write(CONFIG, json.dumps(value, indent=2) + '\n')
    runtime = Path.home() / '.local/lib/groundwork/omarchy-update.py'
    atomic_write(runtime, (source / 'scripts/groundwork-update.py').read_text(), owned=True)
    for command, scope in [('groundwork-update', 'layer'), ('update-all', 'all')]:
        launcher = (f'#!/usr/bin/env bash\n{MARKER}\nset -euo pipefail\n'
                    f'exec python3 "$HOME/.local/lib/groundwork/omarchy-update.py" {scope} "$@"\n')
        atomic_write(Path.home() / '.local/bin' / command, launcher, 0o755, owned=True)


def stop(process):
    # A completed wrapper can leave a child in its session. Drain that owned
    # group before releasing the updater lock, even after the leader exited.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        process.wait(timeout=3)


def foreground(fd, group):
    previous = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    try:
        os.tcsetpgrp(fd, group)
    finally:
        signal.signal(signal.SIGTTOU, previous)


def run(args, cwd=None, deadline=20, capture=True, lock_fd=None):
    terminal = None
    original_group = None
    process = None
    group_options = dict(start_new_session=True)
    if not capture and os.isatty(0) and os.isatty(2):
        terminal = os.open('/dev/tty', os.O_RDWR | os.O_CLOEXEC)
        original_group = os.tcgetpgrp(terminal)
        if original_group != os.getpgrp():
            os.close(terminal)
            raise UpdateError('Run the updater in the foreground of your terminal so it can request authorization.')
        # Keep the controlling terminal, but give this stage an owned process
        # group. Hand terminal input and Ctrl+C to it, then restore the caller.
        group_options = dict(process_group=0)
    started = time.monotonic()
    try:
        environment = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_PAGER='cat')
        if lock_fd is not None:
            environment['GROUNDWORK_OMARCHY_LOCK_FD'] = str(lock_fd)
        process = subprocess.Popen(args, cwd=cwd, text=True, **group_options,
                                   pass_fds=() if lock_fd is None else (lock_fd,),
                                   stdout=subprocess.PIPE if capture else None,
                                   stderr=subprocess.PIPE if capture else None,
                                   env=environment)
        if terminal is not None:
            try:
                foreground(terminal, process.pid)
                os.killpg(process.pid, signal.SIGCONT)
            except ProcessLookupError:
                if process.poll() is None:
                    raise
        while True:
            remaining = deadline - (time.monotonic() - started)
            if remaining <= 0:
                raise UpdateError(f'{args[0]} exceeded its {deadline}-second deadline. Retry after repairing that stage.')
            try:
                stdout, stderr = process.communicate(timeout=min(remaining, 30))
                break
            except subprocess.TimeoutExpired:
                print(f'Still running {args[0]}; elapsed {int(time.monotonic() - started)} seconds.', flush=True)
        if process.returncode in (-signal.SIGINT, -signal.SIGTERM, -signal.SIGHUP, 130, 143, 129):
            raise KeyboardInterrupt(abs(process.returncode) if process.returncode < 0 else process.returncode - 128)
        if process.returncode:
            raise UpdateError(f'{args[0]} failed (exit {process.returncode}).\n{stderr or "See the stage output above."}')
        return (stdout or '').strip()
    finally:
        try:
            if process:
                stop(process)
        finally:
            if terminal is not None:
                try:
                    foreground(terminal, original_group)
                finally:
                    os.close(terminal)
            if process:
                for stream in (process.stdout, process.stderr):
                    if stream:
                        stream.close()


def git(source, *args, **kwargs):
    return run(['git', '--no-optional-locks', '-C', str(source), *args], **kwargs)


def preflight(source):
    if str(source) != git(source, 'rev-parse', '--show-toplevel'):
        raise UpdateError('The saved source must be the root of its own Git checkout.')
    if git(source, 'status', '--porcelain', '--untracked-files=all'):
        raise UpdateError('The Groundwork checkout has local changes. Commit or reconcile them, then retry; no changes were stashed or discarded.')
    branch = git(source, 'symbolic-ref', '--short', 'HEAD')
    remote = git(source, 'config', f'branch.{branch}.remote')
    merge = git(source, 'config', f'branch.{branch}.merge')
    if remote == '.' or remote.startswith('-') or not merge.startswith('refs/heads/'):
        raise UpdateError('Groundwork needs a branch tracking a remote branch. Review its upstream before updating.')
    git(source, 'remote', 'get-url', remote)
    return git(source, 'rev-parse', 'HEAD'), branch, remote, merge


def managed_files(value, source):
    target = Path.home() / '.config/hypr/input.lua'
    expected = source / 'packages/hypr/.config/hypr/input.lua'
    if not target.is_symlink() or target.resolve() != expected.resolve():
        raise UpdateError('The Hyprland input override no longer links to this checkout. Preserve your local override and reconcile it before updating.')
    if value['keyd'] and value.get('keyd_sha256'):
        installed = KEYD_CONFIG
        allowed = {value['keyd_sha256']}
        pending = value.get('pending_keyd')
        if (isinstance(pending, dict) and pending.get('source') == git(source, 'rev-parse', 'HEAD')
                and pending.get('sha256') == hashlib.sha256((source / 'keyd/default.conf').read_bytes()).hexdigest()):
            allowed.add(pending['sha256'])
            if isinstance(pending.get('previous_sha256'), str):
                allowed.add(pending['previous_sha256'])
        if not installed.is_file() or installed.is_symlink() or hashlib.sha256(installed.read_bytes()).hexdigest() not in allowed:
            raise UpdateError('The installed keyd configuration changed locally. Preserve and reconcile it before updating.')


def update(scope, check=False):
    if platform.system() != 'Linux' or platform.machine() != 'aarch64' or not shutil.which('omarchy'):
        raise UpdateError('This updater requires Apple Silicon Omarchy. Use your platform entry point on other systems.')
    value = settings()
    source = Path(value['source'])
    if source.is_symlink() or not source.is_absolute() or source.resolve() != source:
        raise UpdateError('The saved source must be an absolute, canonical checkout path.')
    with transaction() as lock:
        before, branch, remote, merge = preflight(source)
        managed_files(value, source)
        candidate_ref = f'refs/groundwork/update/{uuid.uuid4().hex}'
        candidate = None
        candidate_cleaned = False
        completed = []
        try:
            print('Checking Groundwork repository updates…', flush=True)
            git(source, 'fetch', '--no-tags', '--no-recurse-submodules', remote,
                f'{merge}:{candidate_ref}', deadline=120)
            candidate = git(source, 'rev-parse', candidate_ref)
            try:
                git(source, 'merge-base', '--is-ancestor', before, candidate)
            except UpdateError as error:
                raise UpdateError('The checkout is ahead of or diverged from its remote branch. Reconcile it manually; no reset or stash was attempted.') from error
            summary = git(source, 'diff', '--no-ext-diff', '--no-textconv', '--stat', before, candidate, '--')
            print(summary or 'Groundwork source is current.', flush=True)
            if check:
                print('Check complete. The checkout and applied configuration were not changed.')
                return
            if scope == 'all':
                print('Updating Omarchy through omarchy update…', flush=True)
                run(['omarchy', 'update'], deadline=3600, capture=False)
                completed.append('Omarchy update command completed')
            if preflight(source) != (before, branch, remote, merge):
                raise UpdateError('Checkout or upstream changed during the update. Retry after reviewing it.')
            managed_files(value, source)
            git(source, 'merge', '--ff-only', '--no-edit', candidate)
            if value['keyd']:
                value['pending_keyd'] = dict(source=candidate,
                                             sha256=hashlib.sha256((source / 'keyd/default.conf').read_bytes()).hexdigest(),
                                             previous_sha256=hashlib.sha256(KEYD_CONFIG.read_bytes()).hexdigest())
                atomic_write(CONFIG, json.dumps(value, indent=2) + '\n')
            print('Applying Groundwork settings using saved setup choices…', flush=True)
            run(['bash', str(source / 'scripts/apply-layer.sh'), '--update'],
                deadline=900, capture=False, lock_fd=lock.fileno())
            value = settings()
            if value['keyd']:
                value['keyd_sha256'] = hashlib.sha256((source / 'keyd/default.conf').read_bytes()).hexdigest()
                atomic_write(CONFIG, json.dumps(value, indent=2) + '\n')
            completed.append('Groundwork configuration applied')
            if scope == 'all':
                print('Updating T3 Code Nightly (its background service may restart)…', flush=True)
                run(['t3', 'update', '--channel', 'nightly', '--yes'], deadline=300, capture=False)
                if '-nightly.' not in run(['t3', '--version']):
                    raise UpdateError('T3 Code did not report the selected Nightly channel after updating.')
                completed.append('T3 Code Nightly update command completed')
                if shutil.which('flatpak'):
                    # Only Groundwork's installed Bitwarden Flatpak is in scope.
                    installed = run(['flatpak', '--user', 'list', '--app', '--columns=application'])
                    if 'com.bitwarden.desktop' in installed.splitlines():
                        print('Updating the installed Bitwarden user Flatpak…', flush=True)
                        run(['flatpak', '--user', 'update', '--noninteractive', 'com.bitwarden.desktop'],
                            deadline=600, capture=False)
                        completed.append('Bitwarden user Flatpak update command completed')
                    else:
                        completed.append('Bitwarden user Flatpak not installed; skipped')
                else:
                    completed.append('User Flatpak tooling not installed; skipped')
            git(source, 'update-ref', '-d', candidate_ref, candidate)
            candidate_cleaned = True
            receipt = dict(schema=1, scope=scope, source_before=before, source_after=candidate,
                           completed=completed, result='completed', ended_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
            atomic_write(STATE / f'{"update-all" if scope == "all" else "groundwork-update"}-last-success.json', json.dumps(receipt, indent=2) + '\n')
            print('\n' + '\n'.join(completed))
            if scope == 'layer':
                print('Routine app and system upgrades were not requested. Use update-all for those.')
        except BaseException:
            try:
                observed = git(source, 'rev-parse', 'HEAD')
            except (UpdateError, OSError):
                observed = 'unknown; inspect git status'
            print(f'Update incomplete. Source started at {before}; current HEAD is {observed}.', file=sys.stderr)
            for stage in completed:
                print(f'Completed: {stage}', file=sys.stderr)
            print('Prior successful receipts were preserved. Repair the reported stage, then rerun the same command.', file=sys.stderr)
            raise
        finally:
            if candidate and not candidate_cleaned:
                try:
                    git(source, 'update-ref', '-d', candidate_ref, candidate)
                except (UpdateError, OSError) as error:
                    print(f'Candidate reference cleanup also failed: {error}', file=sys.stderr)


def main(args):
    # Private setup adapters accept an exact argument count before any writes.
    if args and args[0] == 'verify-lock' and len(args) == 1:
        verify_lock()
        return 0
    if args and args[0] == 'setup' and len(args) >= 2:
        source, *options = args[1:]
        valid = {'--with-keyd', '--without-keyd', '--with-screen-sharing', '--without-screen-sharing', '--update', '--help', '-h'}
        if any(option not in valid for option in options):
            print('Unknown setup argument; nothing changed.', file=sys.stderr)
            return 64
        if ({'--with-keyd', '--without-keyd'} <= set(options)
                or {'--with-screen-sharing', '--without-screen-sharing'} <= set(options)):
            print('Choose only one option for each setup preference; nothing changed.', file=sys.stderr)
            return 64
        if '--help' in options or '-h' in options:
            print('Usage: ./install.sh [--with-keyd|--without-keyd] [--with-screen-sharing|--without-screen-sharing]')
            print('Saved setup choices are reused. Explicit options change those choices.')
            return 0
        if run(['uname', '-s']) != 'Linux' or run(['uname', '-m']) != 'aarch64' or not shutil.which('omarchy'):
            raise UpdateError('This layer requires Apple Silicon Omarchy.')
        validate_registration(source)
        with transaction() as lock:
            validate_registration(source)
            run(['bash', str(Path(source) / 'scripts/apply-layer.sh'), *options],
                deadline=900, capture=False, lock_fd=lock.fileno())
        return 0
    if args and args[0] == 'validate' and len(args) == 2:
        validate_registration(args[1])
        return 0
    if args and args[0] == 'preferences' and len(args) == 2 and args[1] in ('setup', 'update'):
        value = settings(args[1] == 'update')
        if value:
            print(f"{int(value['keyd'])}\n{int(value['screen_sharing'])}\n{value['obsidian_vault']}")
        return 0
    if args and args[0] == 'register' and len(args) == 5 and args[2] in ('0', '1') and args[3] in ('0', '1'):
        register(*args[1:])
        return 0
    if not args or args[0] not in ('all', 'layer'):
        print('Use update-all or groundwork-update; this file is their installed implementation.', file=sys.stderr)
        return 64
    scope, *options = args
    if options in (['--help'], ['-h']):
        print(f'Usage: {"update-all" if scope == "all" else "groundwork-update"} [--check]')
        print('Synchronize and apply this Groundwork layer using saved setup choices.')
        if scope == 'all':
            print('Also run omarchy update, update T3 Nightly (including its background service), and update the installed Bitwarden user Flatpak.')
        print('--check fetches and displays repository changes without advancing the checkout or applying settings.')
        return 0
    if options not in ([], ['--check']):
        print('Unknown or combined arguments. Use --help; nothing changed.', file=sys.stderr)
        return 64
    update(scope, check=options == ['--check'])
    return 0


if __name__ == '__main__':
    def cancelled(signum, _frame):
        raise KeyboardInterrupt(signum)
    for name in ('SIGINT', 'SIGTERM', 'SIGHUP'):
        signal.signal(getattr(signal, name), cancelled)
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt as error:
        print('Update cancelled. Retry to finish any already-applied stages.', file=sys.stderr)
        sys.exit(128 + (error.args[0] if error.args and isinstance(error.args[0], int) else signal.SIGINT))
    except (UpdateError, OSError, ValueError) as error:
        print(f'Groundwork: {error}', file=sys.stderr)
        sys.exit(1)
