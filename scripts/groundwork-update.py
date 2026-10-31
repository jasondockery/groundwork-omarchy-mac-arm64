#!/usr/bin/env python3
"""Groundwork Omarchy updater. Groundwork owns this layer; Omarchy owns the OS."""
import fcntl
from contextlib import contextmanager
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import platform
import resource
import select
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
import zipfile

MARKER = '# Groundwork Omarchy updater'


def xdg_base(name, default):
    """Directory for one XDG_* variable. Empty or unset means the default.

    This matches the shell's ${VAR:-default}, which is how apply-layer.sh
    reads the same variables. A value of only spaces is still set.
    """
    value = os.environ.get(name)
    if not value:
        return Path(default)
    return Path(value)


CONFIG = xdg_base('XDG_CONFIG_HOME', Path.home() / '.config') / 'groundwork/omarchy.json'
STATE = xdg_base('XDG_STATE_HOME', Path.home() / '.local/state') / 'groundwork'
KEYD_CONFIG = Path('/etc/keyd/default.conf')
T3_REPO = 'pingdotgg/t3code'
T3_RELEASES = f'https://api.github.com/repos/{T3_REPO}/releases?per_page=100'
T3_DOWNLOADS = f'https://github.com/{T3_REPO}/releases/download'
T3_PAGE_SIZE_ISSUE = 'pingdotgg/t3code#23433'
T3_INSTALLER = 'https://t3.codes/install.sh'
# Bun on non-4 KiB aarch64 pages is unproven upstream (oven-sh/bun#17627).
BUN_PAGE_SIZE_ISSUE = 'oven-sh/bun#17627'
BUN_PAGE_SIZE_URL = 'https://github.com/oven-sh/bun/issues/17627'
BUN_LINUX_ARCHIVE = 'bun-linux-aarch64.zip'
BLIP_ID = 'nixfred.blip'
# Setup's apply stage, including any migration prompt, must finish within this.
SETUP_APPLY_SECONDS = 900
# Ids in declined_migrations: lowercase letters, digits, and hyphens.
MIGRATION_ID_CHARACTERS = frozenset('abcdefghijklmnopqrstuvwxyz0123456789-')
# Last nightly whose ELF files all load on 16 KiB pages (pingdotgg/t3code#23433).
T3_KNOWN_GOOD_LARGE_PAGE = '0.1.2-nightly.20261021.3499'
# A downloaded candidate must finish --version inside its temp directory.
T3_CANDIDATE_SMOKE_SECONDS = 30
# The LOAD rule t3-rejected.json was written under (decision 0018). A file
# without this marker was written under the old p_align-only rule; its
# versions are dropped once so they are checked and smoke-run again.
T3_REJECT_RULE = '0018'
# Private windowed launch used to see whether brave-bin starts on this kernel
# and desktop: Wayland, GPU process, and a real window, with a throwaway
# profile. A 16 KiB-page crash is brave/brave-browser#42312. A timeout is not
# remembered. The window must answer within BRAVE_LAUNCH_SECONDS, stay up for
# BRAVE_SETTLE_SECONDS, then close cleanly within BRAVE_CLOSE_SECONDS.
BRAVE_LAUNCH_SECONDS = 30
BRAVE_SETTLE_SECONDS = 5
BRAVE_CLOSE_SECONDS = 15
# Guard for the only extension the Brave spec may offer. The update URL is read
# from the spec; this id is the refusal check, not a second copy of the manifest.
BRAVE_EXTENSION_ID = 'nngceckbapebfimnlniiiahkandclblb'
# bubblewrap ships with Flatpak on Omarchy. A scrubbed environment is not a
# substitute: without a mount and network namespace the candidate can still
# read the home directory and open sockets, so a missing bwrap skips this run
# instead of executing the download. The version is not rejected.
T3_NO_BWRAP = (
    'bubblewrap is missing, so the newest build was not smoke-tested or installed '
    '(install flatpak, which ships bwrap)'
)
# Captured stages print progress at this interval. Interactive stages own the
# terminal and stay quiet so a prompt (for example sudo) is never interrupted.
HEARTBEAT_SECONDS = 30
# On a deadline or an interrupt, the stage gets SIGINT and this long to clean
# up before SIGTERM and then SIGKILL.
INTERRUPT_GRACE_SECONDS = 10


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
            or '\n' in value['obsidian_vault']
            or ('blip' in value and type(value['blip']) is not bool)
            or ('local_inference' in value and type(value['local_inference']) is not bool)
            or ('git_filter_repo' in value and type(value['git_filter_repo']) is not bool)
            or ('declined_migrations' in value and not valid_migration_ids(value['declined_migrations']))):
        raise UpdateError(f'Invalid updater settings in {CONFIG}; preserve and repair that file before updating.')
    return value


def valid_migration_id(name):
    return (isinstance(name, str) and 0 < len(name) <= 64 and name[0].isalnum()
            and all(character in MIGRATION_ID_CHARACTERS for character in name))


def valid_migration_ids(names):
    return isinstance(names, list) and all(valid_migration_id(name) for name in names)


def declined_migrations(value):
    """Migration offers the person declined (decision 0014). Older setups have none."""
    if value is None or 'declined_migrations' not in value:
        return []
    names = value['declined_migrations']
    if not valid_migration_ids(names):
        raise UpdateError(f'Invalid updater settings in {CONFIG}; preserve and repair that file before updating.')
    return list(dict.fromkeys(names))


def parse_declined_migrations(text):
    """Comma-separated ids from apply-layer.sh. An empty string means none."""
    names = [name for name in text.split(',') if name]
    if not valid_migration_ids(names):
        raise UpdateError('Declined migration ids must be lowercase letters, digits, and hyphens.')
    return list(dict.fromkeys(names))


def blip_enabled(value):
    """Saved Blip choice. Setups written before the choice existed stay off."""
    if value is None or 'blip' not in value:
        return False
    flag = value['blip']
    if type(flag) is not bool:
        raise UpdateError(f'Invalid updater settings in {CONFIG}; preserve and repair that file before updating.')
    return flag


def local_inference_enabled(value):
    """Saved local-inference choice. Setups written before the choice existed stay off."""
    if value is None or 'local_inference' not in value:
        return False
    flag = value['local_inference']
    if type(flag) is not bool:
        raise UpdateError(f'Invalid updater settings in {CONFIG}; preserve and repair that file before updating.')
    return flag


def git_filter_repo_enabled(value):
    """Saved git-filter-repo choice. Setups written before the choice existed stay off."""
    if value is None or 'git_filter_repo' not in value:
        return False
    flag = value['git_filter_repo']
    if type(flag) is not bool:
        raise UpdateError(f'Invalid updater settings in {CONFIG}; preserve and repair that file before updating.')
    return flag


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


def register(source, keyd, screen_sharing, vault, blip='0', local_inference='0', declined=None, git_filter_repo=None):
    source = Path(source).resolve()
    previous = validate_registration(source)
    if blip not in ('0', '1'):
        raise UpdateError('Blip choice must be 0 or 1.')
    if local_inference not in ('0', '1'):
        raise UpdateError('Local inference choice must be 0 or 1.')
    if git_filter_repo not in (None, '0', '1'):
        raise UpdateError('git-filter-repo choice must be 0 or 1.')
    value = dict(previous or {}, schema=1, source=str(source), keyd=keyd == '1',
                 screen_sharing=screen_sharing == '1', obsidian_vault=vault, blip=blip == '1',
                 local_inference=local_inference == '1')
    # None keeps whatever was saved. apply-layer.sh always passes the full list.
    if git_filter_repo is not None:
        value['git_filter_repo'] = git_filter_repo == '1'
    if declined is not None:
        names = parse_declined_migrations(declined)
        if names:
            value['declined_migrations'] = names
        else:
            value.pop('declined_migrations', None)
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


MARKERS = {'ufw-screen-share-rule', 'syncthing-pending'}
STOP_SIGNALS = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}


def signal_group(process, signum):
    """Signal the stage's process group; False once the group is gone."""
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        return False
    except PermissionError:
        # A child that became root (sudo, pkexec) cannot be signalled by us.
        print(f'Could not send {signal.Signals(signum).name} to every process started by {process.args[0]}; '
              'a privileged child may still be running.', file=sys.stderr, flush=True)
    return True


def group_alive(process, warned=None):
    # `warned` is a per-stop set, so a polling loop reports an unreadable group once.
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        if warned is None or process.pid not in warned:
            if warned is not None:
                warned.add(process.pid)
            print(f'Could not check the remaining processes started by {process.args[0]}; '
                  'waiting for them as if they were still running.', file=sys.stderr, flush=True)
        return True
    return True


def reap(process):
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        print(f'{process.args[0]} (process {process.pid}) is still running after SIGKILL.', file=sys.stderr, flush=True)


def stop(process, grace=0):
    # Hold Ctrl+C, SIGTERM, and SIGHUP until the stage is cleaned up, so a
    # repeated interrupt cannot escape halfway and leave the child running.
    # A held signal is delivered, and raises as usual, when the mask returns.
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, STOP_SIGNALS)
    try:
        stop_group(process, grace)
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def stop_group(process, grace):
    # On a deadline or an interrupt, forward SIGINT first so interactive tools
    # (omarchy update, pacman, sudo) can clean up, as if Ctrl+C was pressed.
    warned = set()
    if grace:
        if not signal_group(process, signal.SIGINT):
            reap(process)
            return
        ends = time.monotonic() + grace
        while time.monotonic() < ends:
            if process.poll() is not None and not group_alive(process, warned):
                return
            if STOP_SIGNALS & signal.sigpending():
                # A second interrupt asks to stop now: skip the rest of the grace.
                signal_group(process, signal.SIGKILL)
                reap(process)
                return
            time.sleep(0.05)
    # A completed wrapper can leave a child in its session. Drain that owned
    # group before releasing the updater lock, even after the leader exited.
    if not signal_group(process, signal.SIGTERM):
        reap(process)
        return
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    signal_group(process, signal.SIGKILL)
    if process.poll() is None:
        reap(process)


def foreground(fd, group):
    previous = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    try:
        os.tcsetpgrp(fd, group)
    finally:
        signal.signal(signal.SIGTTOU, previous)


def disable_core_dumps():
    """Give this process RLIMIT_CORE 1 before it execs.

    Linux dumps to a pipe for every core limit except exactly 1. That is the
    limit systemd-coredump's piped core_pattern still honors; 0 is replaced
    with infinity and the pipe still runs. A file pattern writes nothing when
    the limit is below the page size, so 1 writes nothing there either. Exec
    resets the dumpable flag, so this cannot clear it for a binary this
    process did not write. A fixture that signals itself clears the flag.
    """
    resource.setrlimit(resource.RLIMIT_CORE, (1, 1))


def run(args, cwd=None, deadline=20, capture=True, lock_fd=None, extra_env=None, stdin=None, env=None,
        limit_core=False):
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
    interrupted = False
    try:
        if env is None:
            environment = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_PAGER='cat')
        else:
            # Replacement environment. Do not copy the caller's variables.
            environment = dict(env)
        environment.update(extra_env or {})
        if lock_fd is not None:
            environment['GROUNDWORK_OMARCHY_LOCK_FD'] = str(lock_fd)
        process = subprocess.Popen(args, cwd=cwd, text=True, stdin=stdin, **group_options,
                                   pass_fds=() if lock_fd is None else (lock_fd,),
                                   stdout=subprocess.PIPE if capture else None,
                                   stderr=subprocess.PIPE if capture else None,
                                   env=environment,
                                   preexec_fn=disable_core_dumps if limit_core else None)
        if terminal is not None:
            try:
                foreground(terminal, process.pid)
                os.killpg(process.pid, signal.SIGCONT)
            except ProcessLookupError:
                if process.poll() is None:
                    raise
        try:
            while True:
                remaining = deadline - (time.monotonic() - started)
                if remaining <= 0:
                    interrupted = True
                    raise UpdateError(f'{args[0]} exceeded its {deadline}-second deadline. Retry after repairing that stage.')
                try:
                    stdout, stderr = process.communicate(timeout=min(remaining, HEARTBEAT_SECONDS))
                    break
                except subprocess.TimeoutExpired:
                    # A child that owns the terminal may be waiting at a prompt,
                    # so only captured or terminal-less (cron) stages report.
                    if (capture or terminal is None) and time.monotonic() - started < deadline:
                        print(f'Still running {args[0]}; elapsed {int(time.monotonic() - started)} seconds.', flush=True)
        except KeyboardInterrupt:
            interrupted = True
            raise
        if process.returncode in (-signal.SIGINT, -signal.SIGTERM, -signal.SIGHUP, 130, 143, 129):
            raise KeyboardInterrupt(abs(process.returncode) if process.returncode < 0 else process.returncode - 128)
        if process.returncode:
            raise UpdateError(f'{args[0]} failed (exit {process.returncode}).\n{stderr or "See the stage output above."}')
        return (stdout or '').strip()
    finally:
        # Hold interrupts from the first cleanup instruction, so none can land
        # between the stage ending and stop() taking over.
        held = signal.pthread_sigmask(signal.SIG_BLOCK, STOP_SIGNALS)
        try:
            try:
                if process:
                    stop(process, INTERRUPT_GRACE_SECONDS if interrupted else 0)
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
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, held)


def page_size():
    # Same value as `getconf PAGESIZE`, without a subprocess.
    return os.sysconf('SC_PAGESIZE')


class HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != 'https':
            raise UpdateError(f'Refusing a non-https redirect from {req.full_url}.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


https_opener = urllib.request.build_opener(HttpsOnlyRedirects)


def fetch(url, deadline=60):
    ends = time.monotonic() + deadline
    request = urllib.request.Request(url, headers={'User-Agent': 'groundwork-omarchy-updater'})
    chunks = []
    try:
        with https_opener.open(request, timeout=min(deadline, 30)) as response:
            # urllib exposes no public socket. If a later Python moves this
            # private attribute, fall back to the opener's fixed timeout.
            sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
            # The response closes itself on the last byte.
            while not response.isclosed():
                # A socket timeout bounds one recv, not the transfer. Bound each
                # read by the time left, so a stalled read cannot pass the deadline.
                left = ends - time.monotonic()
                if left <= 0:
                    raise UpdateError(f'Downloading {url} exceeded {deadline} seconds.')
                if sock is not None:
                    sock.settimeout(left)
                chunks.append(response.read1(1 << 20))
    except TimeoutError as error:
        raise UpdateError(f'Downloading {url} exceeded {deadline} seconds.') from error
    return b''.join(chunks)


def reported_t3_version(reported):
    fields = (reported or '').split()
    if not fields:
        raise UpdateError('t3 --version printed nothing.')
    return fields[-1].removeprefix('v')


def t3_version():
    return reported_t3_version(run(['t3', '--version']))


def t3_latest_nightly():
    # Same selection as https://t3.codes/install.sh: the newest nightly tag.
    for release in json.loads(fetch(T3_RELEASES)):
        tag = release.get('tag_name') or ''
        version = tag.removeprefix('v')
        if (tag.startswith('v') and not release.get('draft') and '-nightly.' in version
                and all(part.isdigit() for part in version.replace('-nightly.', '.').split('.'))):
            return version
    raise UpdateError('No T3 Code nightly release was found.')


def t3_download(version, directory):
    """Verify and unpack one linux-arm64 release archive; return its root."""
    archive = f't3-{version}-linux-arm64.tar.gz'
    expected = None
    for line in fetch(f'{T3_DOWNLOADS}/v{version}/SHA256SUMS').decode().splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1].lstrip('*') == archive:
            expected = fields[0]
    if not expected:
        raise UpdateError(f'{archive} is not listed in SHA256SUMS.')
    data = fetch(f'{T3_DOWNLOADS}/v{version}/{archive}', deadline=300)
    if hashlib.sha256(data).hexdigest() != expected:
        raise UpdateError(f'Checksum mismatch for {archive}.')
    root = Path(directory) / 'release'
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as bundle:
        bundle.extractall(root, filter='data')
    binaries = [path for path in root.glob('*/t3') if path.is_file()]
    if len(binaries) != 1:
        raise UpdateError(f'{archive} has no single t3 executable.')
    return root


def load_segments(binary):
    """(p_offset, p_vaddr, p_align) of every LOAD program header, from readelf -lW."""
    segments = []
    for line in run(['readelf', '-lW', str(binary)]).splitlines():
        fields = line.split()
        if fields[:1] == ['LOAD']:
            segments.append((int(fields[1], 16), int(fields[2], 16), int(fields[-1], 16)))
    return segments


def load_alignments(binary):
    return [align for _, _, align in load_segments(binary)]


def t3_load_segment_mappable(offset, vaddr, align, page):
    """True when the kernel and ld.so can map this LOAD segment with `page`-sized pages.

    mmap needs p_offset congruent to p_vaddr modulo the page size, with the
    mapping starting on a page boundary. p_align is what the linker promised,
    not what the loader checks. A p_align of at least the page passes, as it
    always has. A smaller p_align passes only when p_offset and p_vaddr are
    both multiples of the page: then the segment starts on its own page, so it
    cannot share a page with the segment before it. This is the T3 rule only
    (decision 0018): such a build must still pass the smoke launch before it
    is installed.
    """
    if align >= page:
        return True
    return offset % page == 0 and vaddr % page == 0


def verified_bun_executable(directory):
    """Verify bun-linux-aarch64.zip against SHASUMS256.txt and return its bun ELF.

    The archive stays in `directory`. The ELF is written to `directory/bun`.
    Members that escape the archive, and symlink entries named bun, are refused.
    """
    directory = Path(directory)
    archive = directory / BUN_LINUX_ARCHIVE
    sums = directory / 'SHASUMS256.txt'
    if not archive.is_file() or archive.is_symlink() or not sums.is_file() or sums.is_symlink():
        raise UpdateError('The bun release download is incomplete.')
    expected = None
    for line in sums.read_text().splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1].lstrip('*') == BUN_LINUX_ARCHIVE:
            expected = fields[0].lower()
            break
    if expected is None or len(expected) != 64 or any(character not in '0123456789abcdef' for character in expected):
        raise UpdateError(f'{BUN_LINUX_ARCHIVE} is not listed in SHASUMS256.txt.')
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise UpdateError(f'Checksum mismatch for {BUN_LINUX_ARCHIVE}.')
    destination = directory / 'bun'
    with zipfile.ZipFile(archive) as bundle:
        chosen = None
        for info in bundle.infolist():
            name = Path(info.filename)
            if name.is_absolute() or '..' in name.parts:
                raise UpdateError(f'Refusing a path in {BUN_LINUX_ARCHIVE}: {info.filename}')
            mode = info.external_attr >> 16
            if name.name != 'bun' or info.is_dir() or (mode & 0o170000) == 0o120000:
                continue
            if chosen is not None:
                raise UpdateError(f'{BUN_LINUX_ARCHIVE} has more than one bun executable.')
            chosen = info
        if chosen is None:
            raise UpdateError(f'{BUN_LINUX_ARCHIVE} has no bun executable.')
        with bundle.open(chosen) as source, destination.open('wb') as output:
            shutil.copyfileobj(source, output)
    os.chmod(destination, 0o755)
    return destination


def elf_alignment_problem(binary):
    """Reason an ELF cannot be mapped on this kernel, or None when it can.

    A file whose header is not ELF returns the string 'not-elf' so callers can
    skip data files. No LOAD segment is a refusal.
    """
    page = page_size()
    path = Path(binary)
    try:
        with path.open('rb') as handle:
            header = handle.read(4)
    except OSError as error:
        return f'Could not read {binary} ({error}).'
    if header != b'\x7fELF':
        return 'not-elf'
    if not shutil.which('readelf'):
        return (f'readelf is missing, so {binary} cannot be checked for {page // 1024} KiB pages. '
                'Install binutils.')
    try:
        alignments = load_alignments(binary)
    except UpdateError as error:
        return f'Could not read LOAD alignment for {binary} ({error}).'
    if not alignments or any(alignment < page for alignment in alignments):
        return f'{binary} is not aligned for {page // 1024} KiB pages and cannot start.'
    return None


def package_alignment_problem(package):
    """Reason an installed pacman package's ELF files cannot load here, or None.

    Lists the package with `pacman -Qlq` and checks every ELF file with
    `elf_alignment_problem`, the same readelf -lW check as local inference.
    A package with no ELF file is a refusal: nothing was shown to load.
    """
    try:
        listed = run(['pacman', '-Qlq', package], deadline=60)
    except (UpdateError, OSError) as error:
        return f'Could not list the files of {package} ({error}).'
    checked = 0
    for line in listed.splitlines():
        path = Path(line)
        if not line or line.endswith('/') or path.is_symlink() or not path.is_file():
            continue
        problem = elf_alignment_problem(path)
        if problem == 'not-elf':
            continue
        if problem:
            return problem
        checked += 1
    if not checked:
        return f'{package} installed no ELF file, so it was not shown to start on this kernel.'
    return None


def brave_spec_path():
    """groundwork-core's Brave spec. This checkout does not keep a second copy."""
    return Path(__file__).resolve().parent.parent / 'core' / 'browser' / 'brave.json'


def load_brave_spec():
    path = brave_spec_path()
    if path.is_symlink() or not path.is_file():
        raise UpdateError(f'Could not read the Brave spec at {path}.')
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise UpdateError(f'Could not read the Brave spec at {path} ({error}).') from error
    if not isinstance(value, dict) or value.get('schemaVersion') != 1:
        raise UpdateError(f'Brave spec at {path} is not schema version 1.')
    return value


def brave_policy_document(spec=None):
    """Policy JSON for /etc/brave/policies/managed. Reasons stay in the spec."""
    spec = load_brave_spec() if spec is None else spec
    policies = spec.get('policies')
    if not isinstance(policies, list) or not policies:
        raise UpdateError('The Brave spec lists no policies.')
    document = {}
    for item in policies:
        if not isinstance(item, dict):
            raise UpdateError('A Brave policy entry is not an object.')
        name = item.get('name')
        if not isinstance(name, str) or not name or any(character.isspace() for character in name):
            raise UpdateError('A Brave policy entry has no name.')
        if name in document:
            raise UpdateError(f'Brave policy {name} is listed twice.')
        if 'value' not in item:
            raise UpdateError(f'Brave policy {name} has no value.')
        value = item['value']
        if isinstance(value, bool) or isinstance(value, str) or type(value) is int:
            document[name] = value
        else:
            raise UpdateError(f'Brave policy {name} has a value this layer cannot write.')
    return document


def render_brave_policy():
    return json.dumps(brave_policy_document(), indent=2) + '\n'


def brave_offered_extension(spec=None):
    """The one extension Brave may offer. Anything else is refused."""
    spec = load_brave_spec() if spec is None else spec
    extensions = spec.get('extensions')
    if not isinstance(extensions, list):
        raise UpdateError('The Brave spec has no extension list.')
    offered = [item for item in extensions if isinstance(item, dict) and item.get('install') == 'offer']
    if len(offered) != 1:
        raise UpdateError('The Brave spec must offer exactly one extension.')
    item = offered[0]
    if item.get('name') != 'Bitwarden' or item.get('id') != BRAVE_EXTENSION_ID:
        raise UpdateError('The Brave spec offers an extension other than Bitwarden.')
    url = item.get('updateUrl')
    if not isinstance(url, str) or not url.startswith('https://'):
        raise UpdateError('The Bitwarden extension update URL is missing or not https.')
    return item


def render_brave_extension():
    item = brave_offered_extension()
    return json.dumps({'external_update_url': item['updateUrl']}, indent=2) + '\n'


def brave_wayland_socket():
    """Absolute path of this session's Wayland socket, or None.

    WAYLAND_DISPLAY is a name under XDG_RUNTIME_DIR or an absolute path. Only
    a socket this user owns counts. Passing the absolute path lets the launch
    keep a private XDG_RUNTIME_DIR.
    """
    display = os.environ.get('WAYLAND_DISPLAY', '')
    if not display:
        return None
    if os.path.isabs(display):
        path = Path(display)
    else:
        runtime = os.environ.get('XDG_RUNTIME_DIR', '')
        if not os.path.isabs(runtime):
            return None
        path = Path(runtime) / display
    try:
        info = path.stat()
    except OSError:
        return None
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        return None
    return path


def brave_launch_environment(home, tmp, runtime, socket):
    """Private environment for the windowed launch. The caller's variables are not copied.

    The only session value passed on is the Wayland socket, as an absolute
    path, so Brave can open a window. D-Bus, the keyring, X11, and the
    caller's runtime directory are not passed.

    GROUNDWORK_TESTING=1 is the installer fixture. Only then are INSTALL_FIXTURE
    and FIXTURE_* copied, so the fake brave command can read its knobs. A normal
    run does not set that variable.
    """
    env = {
        'PATH': '/usr/bin:/bin',
        'HOME': str(home),
        'TMPDIR': str(tmp),
        'LANG': 'C.UTF-8',
        'XDG_RUNTIME_DIR': str(runtime),
        'XDG_CONFIG_HOME': str(home / '.config'),
        'XDG_CACHE_HOME': str(home / '.cache'),
        'XDG_DATA_HOME': str(home / '.local/share'),
        'WAYLAND_DISPLAY': str(socket),
    }
    if os.environ.get('GROUNDWORK_TESTING') == '1':
        for key, value in os.environ.items():
            if key == 'INSTALL_FIXTURE' or key.startswith('FIXTURE_'):
                env[key] = value
    return env


class BraveDevTools:
    """DevTools protocol over --remote-debugging-pipe (fd 3 in, fd 4 out).

    No port is opened. Messages are JSON, each followed by a NUL byte.
    """

    def __init__(self, send_fd, receive_fd):
        self.send_fd = send_fd
        self.receive_fd = receive_fd
        self.buffer = b''
        self.next_id = 0

    def call(self, method, deadline):
        """('reply', message), ('closed', None) at EOF, or ('timeout', None)."""
        self.next_id += 1
        wanted = self.next_id
        request = json.dumps({'id': wanted, 'method': method}).encode() + b'\0'
        try:
            os.write(self.send_fd, request)
        except (BrokenPipeError, ConnectionResetError):
            return 'closed', None
        while True:
            while b'\0' in self.buffer:
                raw, self.buffer = self.buffer.split(b'\0', 1)
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(message, dict) and message.get('id') == wanted:
                    return 'reply', message
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return 'timeout', None
            ready, _, _ = select.select([self.receive_fd], [], [], remaining)
            if not ready:
                return 'timeout', None
            chunk = os.read(self.receive_fd, 65536)
            if not chunk:
                return 'closed', None
            self.buffer += chunk


# Run as `python3 -I -S -c BRAVE_FD_SHIM <read fd> <write fd> <brave argv...>`.
# os.dup2 leaves 3 and 4 inheritable; every other descriptor this interpreter
# opens is close-on-exec, so Brave gets only 0 to 4.
BRAVE_FD_SHIM = (
    'import os, sys\n'
    'read, write = int(sys.argv[1]), int(sys.argv[2])\n'
    'os.dup2(read, 3)\n'
    'os.dup2(write, 4)\n'
    'os.close(read)\n'
    'os.close(write)\n'
    'os.execv(sys.argv[3], sys.argv[3:])\n'
)


def brave_settle_seconds():
    """BRAVE_SETTLE_SECONDS, or the installer fixture's shorter wait."""
    if os.environ.get('GROUNDWORK_TESTING') == '1':
        value = os.environ.get('FIXTURE_BRAVE_SETTLE_SECONDS', '')
        try:
            return max(0.0, float(value))
        except ValueError:
            pass
    return BRAVE_SETTLE_SECONDS


# Signals a process raises on itself when it crashes: a CHECK (TRAP), abort,
# or fault. Any other signal (SIGKILL from the OOM killer, SIGTERM or SIGHUP at
# logout) came from outside and says nothing about this build.
BRAVE_CRASH_SIGNALS = frozenset({
    signal.SIGSEGV, signal.SIGTRAP, signal.SIGABRT, signal.SIGBUS, signal.SIGILL, signal.SIGFPE})


def brave_exit_result(code, page, when):
    """Classify a Brave exit code. Only a crash signal is a remembered crash."""
    kib = page // 1024
    number = -code if code < 0 else code - 128 if code > 128 else None
    if number is not None:
        try:
            name = signal.Signals(number).name
        except ValueError:
            name = f'signal {number}'
        if number in BRAVE_CRASH_SIGNALS:
            return ('remember',
                    f'Brave crashed ({name}) {when} during its launch check on this {kib} KiB-page kernel.')
        return ('transient',
                f'Brave was stopped by {name} {when} during its launch check on this {kib} KiB-page kernel. '
                'That signal came from outside Brave, so the check is tried again next run.')
    return 'transient', f'Brave exited (exit {code}) {when} during its launch check on this {kib} KiB-page kernel.'


def brave_launch_result(binary):
    """('ok', '') when a private Brave window opens, stays up, and closes with exit 0.

    The launch is the real windowed path: Wayland, the GPU process, and a
    page, with no --headless and no --disable-gpu. The profile, HOME, and
    runtime directory are a throwaway temporary directory, and the window is
    closed through the DevTools pipe. 'remember' is a crash of this build.
    'transient' is a timeout, a missing desktop session, or another exit,
    which the next run may try again. The user's profile is not used.
    """
    path = Path(binary)
    if (not path.is_file()) or (path.is_symlink() and not path.exists()):
        return 'transient', f'{binary} is not an executable, so Brave was not shown to start.'
    if not os.access(path, os.X_OK):
        return 'transient', f'{binary} is not executable, so Brave was not shown to start.'
    page = page_size()
    socket = brave_wayland_socket()
    if socket is None:
        return ('transient',
                'Brave was not launch-checked because this run has no Wayland desktop session '
                '(WAYLAND_DISPLAY). Run update-all from a terminal on the desktop to check it.')
    parent = tempfile.mkdtemp(prefix='groundwork-brave-launch-')
    process = None
    pipes = []
    try:
        home = Path(parent) / 'home'
        tmp = Path(parent) / 'tmp'
        runtime = Path(parent) / 'runtime'
        profile = Path(parent) / 'profile'
        for directory in (home, tmp, runtime, profile, home / '.config', home / '.cache', home / '.local/share'):
            directory.mkdir(parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
        env = brave_launch_environment(home, tmp, runtime, socket)
        # Every pipe end sits at fd 10 or above, so moving the child's ends
        # onto 3 and 4 cannot close one of them first.
        for descriptor in os.pipe() + os.pipe():
            pipes.append(fcntl.fcntl(descriptor, fcntl.F_DUPFD_CLOEXEC, 10))
            os.close(descriptor)
        to_brave_read, to_brave_write, from_brave_read, from_brave_write = pipes
        argv = [
            str(path),
            '--ozone-platform=wayland',
            '--password-store=basic',
            '--no-first-run',
            '--no-default-browser-check',
            '--disable-extensions',
            '--disable-background-networking',
            '--disable-component-update',
            f'--user-data-dir={profile}',
            '--remote-debugging-pipe',
            'about:blank',
        ]
        # --remote-debugging-pipe reads fd 3 and writes fd 4. subprocess closes
        # every other descriptor after preexec_fn, so this interpreter moves
        # the two pipe ends into place, closes the originals, and execs Brave.
        # Not /bin/sh: dash, Debian's and Ubuntu's sh, rejects redirections of
        # descriptors above 9, and both ends sit at 10 or above.
        process = subprocess.Popen(
            [sys.executable, '-I', '-S', '-c', BRAVE_FD_SHIM,
             str(to_brave_read), str(from_brave_write), *argv],
            env=env, cwd=parent, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, pass_fds=(to_brave_read, from_brave_write),
            start_new_session=True, preexec_fn=disable_core_dumps)
        os.close(to_brave_read)
        os.close(from_brave_write)
        pipes = [to_brave_write, from_brave_read]
        devtools = BraveDevTools(to_brave_write, from_brave_read)

        outcome, _reply = devtools.call('Browser.getVersion', time.monotonic() + BRAVE_LAUNCH_SECONDS)
        if outcome == 'timeout':
            return ('transient',
                    f'Brave did not open its test window within {BRAVE_LAUNCH_SECONDS} seconds '
                    f'on this {page // 1024} KiB-page kernel.')
        if outcome == 'closed':
            try:
                process.wait(timeout=BRAVE_CLOSE_SECONDS)
            except subprocess.TimeoutExpired:
                return 'transient', 'Brave closed its DevTools pipe without exiting during its launch check.'
            return brave_exit_result(process.returncode, page, 'while opening a window')

        # The window is up. Let the GPU process and the first page run.
        settle = time.monotonic() + brave_settle_seconds()
        while time.monotonic() < settle and process.poll() is None:
            time.sleep(0.1)
        if process.poll() is not None:
            return brave_exit_result(process.returncode, page, 'with its window open')

        close_deadline = time.monotonic() + BRAVE_CLOSE_SECONDS
        devtools.call('Browser.close', close_deadline)
        try:
            process.wait(timeout=max(0.1, close_deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            return ('transient',
                    f'Brave did not close its test window within {BRAVE_CLOSE_SECONDS} seconds '
                    f'on this {page // 1024} KiB-page kernel.')
        if process.returncode == 0:
            return 'ok', ''
        return brave_exit_result(process.returncode, page, 'while closing its window')
    finally:
        for descriptor in pipes:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        if process is not None:
            # Helpers Brave started in the same session (zygote, GPU, crashpad).
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        shutil.rmtree(parent, ignore_errors=True)


def bun_alignment_problem(binary):
    """Reason `binary` cannot be mapped on this kernel, or None.

    Calls `load_alignments`, the same readelf -lW LOAD parse the T3 guard uses, with the strict rule.
    No LOAD segment is a refusal: the file was not shown to be mappable.
    """
    page = page_size()
    issue = f'{BUN_PAGE_SIZE_ISSUE}: {BUN_PAGE_SIZE_URL}'
    if not shutil.which('readelf'):
        return (f'readelf is missing, so {binary} cannot be checked for {page // 1024} KiB pages. '
                f'Install binutils. {issue}')
    try:
        alignments = load_alignments(binary)
    except UpdateError as error:
        return f'Could not read LOAD alignment for {binary} ({error}). {issue}'
    # The strict predicate: a LOAD alignment below the page is refused. Bun has
    # no smoke launch, so the T3 relaxed rule (decision 0018) does not apply.
    if not alignments or any(alignment < page for alignment in alignments):
        return f'{binary} is not aligned for {page // 1024} KiB pages and cannot start. {issue}'
    return None


def misaligned_elf_files(root, page):
    """Return every ELF file under root with a LOAD segment that cannot be mapped with page.

    The predicate is `t3_load_segment_mappable`. Passing here is not
    acceptance: the caller still smoke-runs the candidate.
    """
    misaligned = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open('rb') as handle:
            if handle.read(4) != b'\x7fELF':
                continue
        segments = load_segments(path)
        if path.name == 't3' and path.parent.parent == root and not segments:
            raise UpdateError(f'readelf found no LOAD segments in {path.name}.')
        if not all(t3_load_segment_mappable(*segment, page) for segment in segments):
            misaligned.append(str(path.relative_to(root)))
    return misaligned


def rejected_t3_path():
    return STATE / 't3-rejected.json'


def rejected_bitwarden_path():
    """Bitwarden package versions that failed setup's checks, by page size."""
    return STATE / 'bitwarden-rejected.json'


def rejected_versions(path, page):
    try:
        check_path(path)
        value = json.loads(path.read_text())
    except (OSError, ValueError, UpdateError):
        return []
    versions = value.get(str(page)) if isinstance(value, dict) else None
    return [version for version in versions if isinstance(version, str)] if isinstance(versions, list) else []


def reject_version(path, page, version):
    try:
        check_path(path)
        value = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    versions = [*rejected_versions(path, page), version][-50:]
    value[str(page)] = sorted(set(versions))
    atomic_write(path, json.dumps(value, indent=2) + '\n')


def rejected_t3_document():
    """The t3-rejected.json mapping, or {} when it is missing or not a JSON object."""
    path = rejected_t3_path()
    check_path(path)
    try:
        value = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def migrate_rejected_t3():
    """Drop versions rejected before decision 0018, once; return them.

    A file whose `rule` is not T3_REJECT_RULE (every list written before
    0018) is replaced by one holding only the marker, so those versions are
    downloaded, checked with the current rule, and smoke-run again. A missing
    file stays missing. A file that already has the marker is not changed.
    """
    path = rejected_t3_path()
    check_path(path)
    if not path.exists():
        return []
    value = rejected_t3_document()
    if value.get('rule') == T3_REJECT_RULE:
        return []
    dropped = sorted({version for key, versions in value.items() if key.isdigit() and isinstance(versions, list)
                      for version in versions if isinstance(version, str)})
    atomic_write(path, json.dumps({'rule': T3_REJECT_RULE}, indent=2) + '\n')
    return dropped


def rejected_t3(page):
    return rejected_versions(rejected_t3_path(), page)


def reject_t3(page, version, reason):
    """Remember `version` for `page` with why it failed, under T3_REJECT_RULE.

    `reason` is one of `misaligned-load`, `smoke-crash`, `smoke-pinned-runtime`,
    `update-crash`, `update-pinned-runtime`. A list written under another rule
    is not carried into the new file.
    """
    path = rejected_t3_path()
    value = rejected_t3_document()
    if value.get('rule') != T3_REJECT_RULE:
        value = {}
    key = str(page)
    previous = rejected_versions(path, page) if value else []
    versions = sorted(set([*previous, version][-50:]))
    reasons = value.get('reasons') if isinstance(value.get('reasons'), dict) else {}
    known = reasons.get(key) if isinstance(reasons.get(key), dict) else {}
    known = {item: why for item, why in known.items() if item in versions and isinstance(why, str)}
    known[version] = reason
    reasons[key] = known
    value.update({'rule': T3_REJECT_RULE, key: versions, 'reasons': reasons})
    atomic_write(path, json.dumps(value, indent=2) + '\n')


def t3_rejection_reason(stage, error):
    """`<stage>-pinned-runtime` or `<stage>-crash` for an error t3_candidate_rejected accepts."""
    return f'{stage}-pinned-runtime' if 'PinnedRuntimeInstallError' in str(error) else f'{stage}-crash'


def bitwarden_version():
    """Installed bitwarden version, else the one the sync database offers, else None.

    That is the version a migration or new install would check.
    """
    try:
        fields = run(['pacman', '-Q', 'bitwarden'], deadline=30).split()
        if len(fields) == 2:
            return fields[1]
    except (UpdateError, OSError):
        pass
    try:
        for line in run(['pacman', '-Si', 'bitwarden'], deadline=30).splitlines():
            key, _, value = line.partition(':')
            if key.strip() == 'Version' and value.strip():
                return value.strip()
    except (UpdateError, OSError):
        pass
    return None


def bitwarden_package_command():
    """True only when Omarchy's bitwarden package owns the bitwarden on PATH."""
    executable = shutil.which('bitwarden')
    if not executable:
        return False
    try:
        return run(['pacman', '-Qoq', executable], deadline=30) == 'bitwarden'
    except (UpdateError, OSError):
        return False


def rejected_brave_path():
    """brave-bin versions that failed the page-size or launch check, by page size."""
    return STATE / 'brave-rejected.json'


def accepted_brave_path():
    """brave-bin versions that passed both checks on this page size."""
    return STATE / 'brave-accepted.json'


def remembered_version_document(path):
    """None when path is absent. UpdateError when it exists and is not the schema.

    The schema is a JSON object of page size to lists of version strings. A
    missing file means the version is not remembered. A symlink, directory,
    unreadable file, or other shape is a refusal, so a known-bad version is
    not installed again and a damaged accept file cannot skip the launch check.
    """
    if not path.exists() and not path.is_symlink():
        return None
    try:
        check_path(path)
        if path.is_symlink() or not path.is_file():
            raise UpdateError(f'{path} is not a regular file of remembered versions.')
        value = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise UpdateError(f'{path} is not a regular file of remembered versions ({error}).') from error
    if not isinstance(value, dict) or any(
            not isinstance(key, str) or not isinstance(versions, list)
            or any(not isinstance(version, str) for version in versions)
            for key, versions in value.items()):
        raise UpdateError(f'{path} is not a map of page size to version lists.')
    return value


def brave_memory_status(path):
    """0 when this brave-bin version is listed, 1 when it is not, 2 when the file is unusable."""
    try:
        document = remembered_version_document(path)
    except UpdateError as error:
        print(f'{error} Setup stopped before installing Brave.', file=sys.stderr)
        return 2
    version = brave_version()
    versions = [] if document is None else document.get(str(page_size()), [])
    return 0 if version and version in versions else 1


def brave_any_accepted_status():
    """0 when any brave-bin version passed on this page size, 1 when none, 2 when the file is unusable."""
    try:
        document = remembered_version_document(accepted_brave_path())
    except UpdateError as error:
        print(f'{error} Setup stopped before installing Brave.', file=sys.stderr)
        return 2
    return 0 if document and document.get(str(page_size())) else 1


def brave_version():
    """Installed brave-bin version, else the one `pacman -Si` offers, else None."""
    try:
        fields = run(['pacman', '-Q', 'brave-bin'], deadline=30).split()
        if len(fields) == 2:
            return fields[1]
    except (UpdateError, OSError):
        pass
    try:
        for line in run(['pacman', '-Si', 'brave-bin'], deadline=30).splitlines():
            key, _, value = line.partition(':')
            if key.strip() == 'Version' and value.strip():
                return value.strip()
    except (UpdateError, OSError):
        pass
    return None


def drop_remembered_version(path, page, version):
    try:
        check_path(path)
        value = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError, UpdateError):
        return
    if not isinstance(value, dict):
        return
    key = str(page)
    versions = [item for item in rejected_versions(path, page) if item != version]
    if versions:
        value[key] = versions
    else:
        value.pop(key, None)
    if any(isinstance(items, list) and items for items in value.values()):
        atomic_write(path, json.dumps(value, indent=2) + '\n')
    elif path.exists() and not path.is_symlink():
        path.unlink()


def reject_brave_version(page, version):
    drop_remembered_version(accepted_brave_path(), page, version)
    reject_version(rejected_brave_path(), page, version)


def accept_brave_version(page, version):
    drop_remembered_version(rejected_brave_path(), page, version)
    reject_version(accepted_brave_path(), page, version)


def page_size_reason(page):
    return f'newer linux-arm64 builds crash on {page // 1024} KiB pages ({T3_PAGE_SIZE_ISSUE})'


def t3_signal_number(error):
    """Signal number when `error` is a signal death, else None.

    `run` reports a killed process as a negative code (`exit -11`). bubblewrap
    reports the same death as `128 + signal` (`exit 139`).
    """
    line = str(error).splitlines()[0] if str(error) else ''
    marker = '(exit '
    start = line.rfind(marker)
    if start < 0:
        return None
    end = line.find(')', start)
    if end < 0:
        return None
    try:
        code = int(line[start + len(marker):end])
    except ValueError:
        return None
    number = -code if code < 0 else code - 128 if code > 128 else None
    if number is None:
        return None
    try:
        signal.Signals(number)
    except ValueError:
        return None
    return number


def t3_candidate_rejected(error):
    """True only when this build itself failed to start.

    A deadline, a transport failure, or any other OS error can succeed on the
    next run, so those must not be written to t3-rejected.json.
    """
    if 'PinnedRuntimeInstallError' in str(error):
        return True
    return t3_signal_number(error) is not None


def t3_failure_reason(error):
    """One line naming the actual reason a candidate was not installed."""
    text = str(error).strip()
    if 'PinnedRuntimeInstallError' in text:
        return 'pinned runtime refused to start (PinnedRuntimeInstallError)'
    number = t3_signal_number(error)
    if number is not None:
        return f'crashed ({signal.Signals(number).name})'
    lines = [line.strip().rstrip('.') for line in text.splitlines() if line.strip()]
    if not lines:
        return 'exited unsuccessfully'
    if len(lines) > 1 and '(exit ' in lines[0]:
        return f'{lines[0]}: {lines[1]}'
    return lines[0]


def t3_smoke_environment(directory):
    """Environment for a candidate smoke run. The caller's variables are not copied."""
    root = Path(directory)
    home = root / 'home'
    tmp = root / 'tmp'
    runtime = tmp / 'runtime'
    for path in (home, tmp, home / '.cache', home / '.config', home / '.local/share',
                 home / '.local/state', runtime):
        path.mkdir(parents=True, exist_ok=True)
    os.chmod(runtime, 0o700)
    return {
        'HOME': str(home),
        'TMPDIR': str(tmp),
        'PATH': '/usr/bin:/bin',
        'XDG_CACHE_HOME': str(home / '.cache'),
        'XDG_CONFIG_HOME': str(home / '.config'),
        'XDG_DATA_HOME': str(home / '.local/share'),
        'XDG_RUNTIME_DIR': str(runtime),
        'XDG_STATE_HOME': str(home / '.local/state'),
    }


def t3_resolved_smoke_paths(binary, directory):
    """Return one real download directory and the candidate inside it.

    Bubblewrap binds that directory, then chdirs to the executable's parent.
    TMPDIR or /tmp is often a symlink. Binding the real path and then naming
    the symlink in --chdir, the executable, cwd, or HOME/TMPDIR/XDG_* makes
    bwrap exit 1 with "Can't chdir", so every newer nightly is skipped.
    """
    directory = os.path.realpath(directory)
    binary = Path(os.path.realpath(binary))
    return binary, directory


def t3_sandbox_command(binary, directory):
    """Confined `t3 --version`, or None when bubblewrap is not installed.

    `binary` and `directory` are the real paths from `t3_resolved_smoke_paths`.
    System directories are read-only. The download directory is the only
    writable path. The home directory is not mounted. `--unshare-pid` plus
    `--die-with-parent` reaps a double-fork when bwrap exits.
    """
    bwrap = shutil.which('bwrap')
    if not bwrap:
        return None
    binary = Path(binary)
    directory = str(directory)
    argv = [bwrap, '--die-with-parent', '--unshare-pid', '--unshare-net']
    for path in ('/usr', '/etc'):
        if Path(path).is_dir() and not Path(path).is_symlink():
            argv.extend(['--ro-bind', path, path])
    for path in ('/bin', '/lib', '/lib64', '/sbin'):
        if Path(path).is_symlink():
            argv.extend(['--symlink', os.readlink(path), path])
        elif Path(path).is_dir():
            argv.extend(['--ro-bind', path, path])
    argv.extend(['--proc', '/proc', '--dev', '/dev'])
    # Parents are empty directories in the new root, so host /tmp is not visible.
    acc = Path('/')
    for part in Path(directory).parts[1:]:
        acc = acc / part
        argv.extend(['--dir', str(acc)])
    argv.extend(['--bind', directory, directory, '--chdir', str(binary.parent),
                 '--', str(binary), '--version'])
    return argv


def t3_remember_skip(warnings, reason):
    if warnings is not None:
        warnings.append(reason)
    return reason


def t3_candidate_check(installed, page, warnings=None):
    """Return (reason the newest nightly must not be installed or None, candidate).

    An executable or shared object with a LOAD segment the kernel cannot map
    with its page size is rejected (pingdotgg/t3code#23433). The rule is
    `t3_load_segment_mappable`: p_align of at least the page, or p_offset and
    p_vaddr both page-aligned (decision 0018). A build that passes is
    smoke-tested with `--version` from its own directory and installed only
    when that run prints the candidate.
    Only a signal death is remembered. A timeout or an OS error skips this
    run; the same version is checked again on the next update-all.
    """
    candidate = t3_latest_nightly()
    if candidate == installed:
        return None, candidate
    dropped = migrate_rejected_t3()
    if dropped:
        print(f'T3 Code: checking again {len(dropped)} build(s) rejected before the LOAD rule of decision 0018 '
              f'({", ".join(dropped)}).', flush=True)
    if candidate in rejected_t3(page):
        return page_size_reason(page), candidate
    if not shutil.which('readelf'):
        return 'readelf is missing, so the newest build cannot be checked (install binutils)', candidate
    if shutil.which('bwrap') is None:
        return t3_remember_skip(warnings, T3_NO_BWRAP), candidate
    with tempfile.TemporaryDirectory(prefix='groundwork-t3-') as directory:
        root = t3_download(candidate, directory)
        if misaligned_elf_files(root, page):
            reject_t3(page, candidate, 'misaligned-load')
            return page_size_reason(page), candidate
        binary = next(path for path in root.glob('*/t3') if path.is_file())
        binary, directory = t3_resolved_smoke_paths(binary, directory)
        try:
            reported = run(t3_sandbox_command(binary, directory), cwd=str(binary.parent),
                           deadline=T3_CANDIDATE_SMOKE_SECONDS, stdin=subprocess.DEVNULL,
                           env=t3_smoke_environment(directory), limit_core=True)
            got = reported_t3_version(reported)
        except (UpdateError, OSError, ValueError) as error:
            if t3_candidate_rejected(error):
                reject_t3(page, candidate, t3_rejection_reason('smoke', error))
                return (f'the newest linux-arm64 build does not start on {page // 1024} KiB pages '
                        f'({t3_failure_reason(error)}; {T3_PAGE_SIZE_ISSUE})'), candidate
            return t3_remember_skip(
                warnings, f'the newest linux-arm64 build was not smoke-tested ({t3_failure_reason(error)})'), candidate
        if got != candidate:
            return t3_remember_skip(
                warnings, f'the newest linux-arm64 build reported {got}, not {candidate}'), candidate
    return None, candidate


def install_t3_version(version):
    with tempfile.TemporaryDirectory(prefix='groundwork-t3-installer-') as directory:
        installer = Path(directory) / 'install.sh'
        installer.write_bytes(fetch(T3_INSTALLER))
        run(['sh', str(installer)], deadline=300, capture=False,
            extra_env=dict(T3CODE_CHANNEL='nightly', T3CODE_VERSION=version))
    if t3_version() != version:
        raise UpdateError(f'T3 Code did not report {version} after installing it.')


def repair_t3(completed, warnings, versions, problem):
    print(f'{problem} Reinstalling a build that starts on this kernel…', flush=True)
    for version in dict.fromkeys(version for version in versions if version):
        try:
            install_t3_version(version)
        except (UpdateError, OSError, ValueError, http.client.HTTPException) as error:
            print(f'Installing T3 Code {version} failed: {error}', file=sys.stderr, flush=True)
            continue
        print(f'T3 Code {version} installed. Restart its background service if one is running.', flush=True)
        completed.append(f'T3 Code repaired with {version}')
        return
    print(f'T3 Code is still not starting. Restore it with: curl -fsSL {T3_INSTALLER} | '
          f'T3CODE_CHANNEL=nightly T3CODE_VERSION={T3_KNOWN_GOOD_LARGE_PAGE} sh', file=sys.stderr, flush=True)
    completed.append('T3 Code repair failed; restore command printed')
    warnings.append('T3 Code does not start and could not be repaired; run the printed restore command.')


T3_CHECK_ERRORS = (UpdateError, OSError, ValueError, tarfile.TarError, http.client.HTTPException)


def update_t3(completed, warnings):
    page = page_size()
    if page <= 4096:
        print('Updating T3 Code Nightly (its background service may restart)…', flush=True)
        run(['t3', 'update', '--channel', 'nightly', '--yes'], deadline=300, capture=False)
        if '-nightly.' not in run(['t3', '--version']):
            raise UpdateError('T3 Code did not report the selected Nightly channel after updating.')
        completed.append('T3 Code Nightly update command completed')
        return
    try:
        installed = t3_version()
    except (UpdateError, OSError) as error:
        installed, broken = None, error
    try:
        reason, candidate = t3_candidate_check(installed, page, warnings)
    except T3_CHECK_ERRORS as error:
        reason, candidate = f'the newest linux-arm64 build could not be checked for {page // 1024} KiB pages ({error})', None
    if installed is None:
        repair_t3(completed, warnings, [None if reason else candidate, T3_KNOWN_GOOD_LARGE_PAGE],
                  f'The installed T3 Code does not start ({broken}).')
        return
    if reason:
        print(f'T3 Code update skipped: {reason}; keeping {installed}.', flush=True)
        completed.append(f'T3 Code update skipped on {page // 1024} KiB pages; kept {installed}')
        return
    if candidate == installed:
        print(f'T3 Code Nightly is already {installed}.', flush=True)
        completed.append(f'T3 Code Nightly already current ({installed})')
        return
    # Install exactly the build that was checked, never a newer unchecked one.
    print(f'Updating T3 Code Nightly to {candidate} (its background service may restart)…', flush=True)
    try:
        run(['t3', 'update', '--channel', 'nightly', '--yes', candidate], deadline=300, capture=False)
    except (UpdateError, OSError) as error:
        try:
            kept = t3_version()
        except (UpdateError, OSError):
            repair_t3(completed, warnings, [installed, T3_KNOWN_GOOD_LARGE_PAGE],
                      f'T3 Code {candidate} does not start after updating ({T3_PAGE_SIZE_ISSUE}).')
            return
        # The installed build still starts. Remember the candidate only when
        # that build itself failed (signal exit or PinnedRuntimeInstallError).
        # A 300s deadline, a transport error, or any other OSError is retried
        # on the next update-all and is not written to t3-rejected.json.
        if kept != installed:
            raise
        detail = t3_failure_reason(error)
        if t3_candidate_rejected(error):
            reject_t3(page, candidate, t3_rejection_reason('update', error))
        print(f'T3 Code {candidate} was not installed ({detail}). Keeping {installed}.', flush=True)
        warnings.append(f'T3 Code {candidate} was not installed ({detail}); kept {installed}.')
        completed.append(f'T3 Code update skipped on {page // 1024} KiB pages; kept {installed}')
        return
    try:
        updated = t3_version()
    except (UpdateError, OSError):
        repair_t3(completed, warnings, [installed, T3_KNOWN_GOOD_LARGE_PAGE],
                  f'T3 Code {candidate} does not start after updating ({T3_PAGE_SIZE_ISSUE}).')
        return
    if updated != candidate:
        raise UpdateError(f'T3 Code reported {updated} after updating to {candidate}.')
    completed.append(f'T3 Code Nightly updated to {candidate}')


def git(source, *args, **kwargs):
    return run(['git', '--no-optional-locks', '-C', str(source), *args], **kwargs)


def checkout_core(source):
    """Check out the core gitlink so apply can read core/browser/brave.json.

    git merge --ff-only updates the gitlink and leaves an existing submodule
    worktree at the previous commit. Setup and update both run this after that
    fast-forward (setup has no fast-forward of its own) and before apply.
    A checkout with no core gitlink, such as an updater fixture, is unchanged.
    """
    line = git(source, 'ls-tree', 'HEAD', '--', 'core')
    if not line:
        return
    parts = line.split()
    if len(parts) < 4 or parts[0] != '160000' or parts[-1] != 'core':
        raise UpdateError('The core entry is not a submodule, so its browser spec cannot be checked out.')
    recorded = parts[2]
    core = Path(source) / 'core'
    # A worktree already at that commit, with no local edits, does not need
    # git-submodule. That script fetches and is what made a routine setup
    # rerun slow enough to hit the CI job limit.
    if (core / '.git').exists():
        try:
            checked_out = git(core, 'rev-parse', 'HEAD')
        except UpdateError:
            checked_out = ''
        if checked_out == recorded and not git(core, 'status', '--porcelain', '--untracked-files=all'):
            return
    # git-submodule is a shell script. It needs basename and sed. A setup
    # fixture PATH that only contains installer stubs must not hide those.
    git_path = '/usr/bin:/bin'
    current = os.environ.get('PATH')
    if current:
        git_path = f'{git_path}:{current}'
    git(source, 'submodule', 'update', '--init', '--', 'core', deadline=120,
        extra_env={'PATH': git_path})


def preflight(source):
    if str(source) != git(source, 'rev-parse', '--show-toplevel'):
        raise UpdateError('The saved source must be the root of its own Git checkout.')
    # A submodule commit that does not match the gitlink is repaired by
    # checkout_core. Real edits inside the submodule are still local changes.
    if git(source, 'status', '--porcelain', '--untracked-files=all', '--ignore-submodules=all'):
        raise UpdateError('The Groundwork checkout has local changes. Commit or reconcile them, then retry; no changes were stashed or discarded.')
    core = Path(source) / 'core'
    if (core / '.git').exists() and git(core, 'status', '--porcelain', '--untracked-files=all'):
        raise UpdateError('The core submodule has local changes. Commit or reconcile them in that checkout, then retry; no changes were stashed or discarded.')
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


def interactive_terminal():
    # Same pair omarchy plugin update uses before it will show a diff.
    return os.isatty(0) and os.isatty(1)


def plugin_is_listed(raw, plugin_id):
    """True when plugin list JSON contains plugin_id. ValueError if it is not a list."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f'omarchy plugin list --json was not JSON ({error}).') from error
    if not isinstance(data, list):
        raise ValueError('omarchy plugin list --json did not return a list.')
    return any(isinstance(item, dict) and item.get('id') == plugin_id for item in data)


def blip_setup_command():
    return f'~/.config/omarchy/plugins/{BLIP_ID}/scripts/blip-setup GATEWAY_USER@GATEWAY_MAGICDNS_NAME'


def remind_blip_setup(completed, reason):
    print(f'{reason} Re-run blip-setup. It was not run automatically:', flush=True)
    print(f'  {blip_setup_command()}', flush=True)
    completed.append('Blip bridge reminder printed')


def update_blip(completed):
    """Fast-forward Blip only from an interactive terminal. Never pass --yes."""
    if not blip_enabled(settings()):
        return
    try:
        listed = plugin_is_listed(run(['omarchy', 'plugin', 'list', '--json'], deadline=30), BLIP_ID)
    except (UpdateError, OSError, ValueError) as error:
        print(f'Blip update skipped: the plugin list could not be read ({error}).', flush=True)
        completed.append('Blip update skipped; plugin list unavailable')
        return
    if not listed:
        completed.append('Blip plugin not installed; update skipped')
        return
    if not interactive_terminal():
        print('Blip update skipped: run update-all in an interactive terminal to review '
              f'omarchy plugin update {BLIP_ID}. It is never passed --yes unattended.', flush=True)
        completed.append('Blip update skipped; no interactive terminal')
        return
    plugin = Path.home() / '.config/omarchy/plugins' / BLIP_ID
    before = None
    if (plugin / '.git').is_dir():
        try:
            before = run(['git', '-C', str(plugin), 'rev-parse', 'HEAD'])
        except (UpdateError, OSError):
            before = None
    print(f'Updating {BLIP_ID}. Review the diff before accepting it.', flush=True)
    run(['omarchy', 'plugin', 'update', BLIP_ID], deadline=300, capture=False)
    completed.append('Blip plugin update command completed')
    if not before:
        remind_blip_setup(completed, 'Could not compare bridge/ for this Blip update.')
        return
    try:
        after = run(['git', '-C', str(plugin), 'rev-parse', 'HEAD'])
        names = run(['git', '-C', str(plugin), 'diff', '--name-only', before, after, '--', 'bridge'])
    except (UpdateError, OSError) as error:
        remind_blip_setup(completed, f'Could not compare bridge/ after the Blip update ({error}).')
        return
    if names.strip():
        remind_blip_setup(completed, 'Blip changed files under bridge/.')


def syncthing_pending():
    """What apply-layer.sh left unfinished for Syncthing, or None. Never follows a symlink."""
    path = STATE / 'syncthing-pending'
    try:
        check_path(path)
        return path.read_text().strip() or None if path.is_file() else None
    except (UpdateError, OSError):
        return f'Syncthing may be unfinished: {path} cannot be read. Inspect it, then rerun.'


def update(scope, check=False):
    if platform.system() != 'Linux' or platform.machine() != 'aarch64' or not shutil.which('omarchy'):
        raise UpdateError('This updater requires Apple Silicon Omarchy. Use your platform entry point on other systems.')
    with transaction() as lock:
        value = settings()
        source = Path(value['source'])
        if source.is_symlink() or not source.is_absolute() or source.resolve() != source:
            raise UpdateError('The saved source must be an absolute, canonical checkout path.')
        before, branch, remote, merge = preflight(source)
        managed_files(value, source)
        candidate_ref = f'refs/groundwork/update/{uuid.uuid4().hex}'
        candidate = None
        candidate_cleaned = False
        completed = []
        # Problems that let the remaining stages run but must not read as success.
        warnings = []
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
            checkout_core(source)
            if value['keyd']:
                value['pending_keyd'] = dict(source=candidate,
                                             sha256=hashlib.sha256((source / 'keyd/default.conf').read_bytes()).hexdigest(),
                                             previous_sha256=hashlib.sha256(KEYD_CONFIG.read_bytes()).hexdigest())
                atomic_write(CONFIG, json.dumps(value, indent=2) + '\n')
            print('Applying Groundwork settings using saved setup choices…', flush=True)
            apply_args = ['bash', str(source / 'scripts/apply-layer.sh'), '--update']
            if scope == 'all':
                apply_args.append('--update-plugins')
            run(apply_args, deadline=900, capture=False, lock_fd=lock.fileno())
            pending = syncthing_pending()
            if pending:
                warnings.append(pending)
            value = settings()
            if value['keyd']:
                value['keyd_sha256'] = hashlib.sha256((source / 'keyd/default.conf').read_bytes()).hexdigest()
                atomic_write(CONFIG, json.dumps(value, indent=2) + '\n')
            completed.append('Groundwork configuration applied')
            if scope == 'all':
                update_t3(completed, warnings)
                try:
                    run([sys.executable, str(source / 'scripts/groundwork-t3-launcher.py'), 'reconcile'],
                        capture=False)
                except (UpdateError, OSError) as error:
                    warnings.append(f'T3 Code launcher check failed: {error}')
                if shutil.which('flatpak'):
                    # Groundwork's user Flatpaks only. Other installed apps stay untouched.
                    installed = set(run(['flatpak', '--user', 'list', '--app', '--columns=application']).splitlines())
                    for app, label in (
                        ('com.bitwarden.desktop', 'Bitwarden'),
                        ('io.freetubeapp.FreeTube', 'FreeTube'),
                    ):
                        if app not in installed:
                            # A native Bitwarden desktop is the expected state, not a skip.
                            if app != 'com.bitwarden.desktop' or not bitwarden_package_command():
                                completed.append(f'{label} user Flatpak not installed; skipped')
                            continue
                        print(f'Updating the installed {label} user Flatpak…', flush=True)
                        try:
                            run(['flatpak', '--user', 'update', '--noninteractive', app],
                                deadline=600, capture=False)
                        except (UpdateError, OSError) as error:
                            if app != 'io.freetubeapp.FreeTube':
                                raise
                            message = f'FreeTube user Flatpak update failed: {error}'
                            print(message, file=sys.stderr, flush=True)
                            warnings.append(message)
                            completed.append('FreeTube user Flatpak update failed')
                            continue
                        completed.append(f'{label} user Flatpak update command completed')
                else:
                    completed.append('User Flatpak tooling not installed; skipped')
                update_blip(completed)
            git(source, 'update-ref', '-d', candidate_ref, candidate)
            candidate_cleaned = True
            result = 'completed_with_warnings' if warnings else 'completed'
            receipt = dict(schema=1, scope=scope, source_before=before, source_after=candidate,
                           completed=completed, result=result, ended_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
            if warnings:
                receipt['warnings'] = warnings
            atomic_write(STATE / f'{"update-all" if scope == "all" else "groundwork-update"}-last-success.json', json.dumps(receipt, indent=2) + '\n')
            print('\n' + '\n'.join(completed))
            if scope == 'layer':
                print('Routine app and system upgrades were not requested. Use update-all for those.')
            for warning in warnings:
                print(f'Warning: {warning}', file=sys.stderr)
            return result
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
    if args and args[0] == 'check-load-alignment' and len(args) == 2:
        problem = bun_alignment_problem(args[1])
        if problem:
            print(problem, file=sys.stderr)
            return 1
        return 0
    if args and args[0] == 'check-package-alignment' and len(args) == 2:
        problem = package_alignment_problem(args[1])
        if problem:
            print(problem, file=sys.stderr)
            return 1
        return 0
    if args and args[0] == 'check-brave-launch' and len(args) == 2:
        try:
            kind, message = brave_launch_result(args[1])
        except (OSError, UpdateError) as error:
            # A full disk or a binary that vanishes is not a page-size crash.
            print(error, file=sys.stderr)
            return 2
        if kind != 'ok':
            print(message, file=sys.stderr)
        return {'ok': 0, 'remember': 1, 'transient': 2}[kind]
    if args and args[0] == 'render-brave-policy' and len(args) == 1:
        sys.stdout.write(render_brave_policy())
        return 0
    if args and args[0] == 'render-brave-extension' and len(args) == 1:
        sys.stdout.write(render_brave_extension())
        return 0
    if args and args[0] == 'brave-extension-id' and len(args) == 1:
        print(brave_offered_extension()['id'])
        return 0
    if args and args[0] == 'brave-rejected' and len(args) == 1:
        return brave_memory_status(rejected_brave_path())
    if args and args[0] == 'brave-accepted' and len(args) == 1:
        return brave_memory_status(accepted_brave_path())
    if args and args[0] == 'brave-accepted-any' and len(args) == 1:
        return brave_any_accepted_status()
    if args and args[0] == 'reject-brave' and len(args) == 1:
        version = brave_version()
        if not version:
            print('The brave-bin version could not be read, so it was not remembered.', file=sys.stderr)
            return 1
        reject_brave_version(page_size(), version)
        print(version)
        return 0
    if args and args[0] == 'accept-brave' and len(args) == 1:
        version = brave_version()
        if not version:
            print('The brave-bin version could not be read, so it was not recorded.', file=sys.stderr)
            return 1
        accept_brave_version(page_size(), version)
        print(version)
        return 0
    if args and args[0] == 'bitwarden-rejected' and len(args) == 1:
        # Exit 0 when the version setup would check already failed on this kernel.
        version = bitwarden_version()
        return 0 if version and version in rejected_versions(rejected_bitwarden_path(), page_size()) else 1
    if args and args[0] == 'reject-bitwarden' and len(args) == 1:
        version = bitwarden_version()
        if not version:
            print('The bitwarden package version could not be read, so it was not remembered.', file=sys.stderr)
            return 1
        reject_version(rejected_bitwarden_path(), page_size(), version)
        print(version)
        return 0
    if args and args[0] == 'check-elf-alignment' and len(args) == 2:
        problem = elf_alignment_problem(args[1])
        if problem == 'not-elf':
            return 2
        if problem:
            print(problem, file=sys.stderr)
            return 1
        return 0
    if args and args[0] == 'verify-bun-archive' and len(args) == 2:
        print(verified_bun_executable(args[1]))
        return 0
    if args and args[0] == 'plugin-listed' and len(args) == 2:
        try:
            found = plugin_is_listed(sys.stdin.read(), args[1])
        except ValueError as error:
            print(error, file=sys.stderr)
            return 2
        return 0 if found else 1
    if args and args[0] == 'setup' and len(args) >= 2:
        source, *options = args[1:]
        valid = {'--with-keyd', '--without-keyd', '--with-screen-sharing', '--without-screen-sharing',
                 '--with-blip', '--without-blip', '--with-local-inference', '--without-local-inference',
                 '--with-git-filter-repo', '--without-git-filter-repo',
                 '--migrate-to-omarchy-installers', '--update', '--help', '-h'}
        if any(option not in valid for option in options):
            print('Unknown setup argument; nothing changed.', file=sys.stderr)
            return 64
        if {'--migrate-to-omarchy-installers', '--update'} <= set(options):
            print('--migrate-to-omarchy-installers is a setup option. Run ./install.sh --migrate-to-omarchy-installers; nothing changed.',
                  file=sys.stderr)
            return 64
        if ({'--with-keyd', '--without-keyd'} <= set(options)
                or {'--with-screen-sharing', '--without-screen-sharing'} <= set(options)
                or {'--with-blip', '--without-blip'} <= set(options)
                or {'--with-local-inference', '--without-local-inference'} <= set(options)
                or {'--with-git-filter-repo', '--without-git-filter-repo'} <= set(options)):
            print('Choose only one option for each setup preference; nothing changed.', file=sys.stderr)
            return 64
        if '--help' in options or '-h' in options:
            print('Usage: ./install.sh [--with-keyd|--without-keyd] [--with-screen-sharing|--without-screen-sharing] [--with-blip|--without-blip] [--with-local-inference|--without-local-inference] [--with-git-filter-repo|--without-git-filter-repo] [--migrate-to-omarchy-installers]')
            print('Saved setup choices are reused. Explicit options change those choices.')
            print('--migrate-to-omarchy-installers moves apps installed another way to Omarchy\'s own installer, even after an earlier no. See docs/decisions/0014-offer-migration-to-omarchy-installers.md.')
            print('Blip is off unless --with-blip is set. --without-blip leaves an existing Blip install unchanged.')
            print('Local inference is off unless --with-local-inference is set. --without-local-inference records that preference as off. See docs/local-inference.md for package and model removal.')
            print('git-filter-repo is off unless --with-git-filter-repo is set. It installs per user with mise, never with sudo. --without-git-filter-repo records that preference as off and leaves an installed copy alone. See docs/git-filter-repo.md.')
            return 0
        if run(['uname', '-s']) != 'Linux' or run(['uname', '-m']) != 'aarch64' or not shutil.which('omarchy'):
            raise UpdateError('This layer requires Apple Silicon Omarchy.')
        validate_registration(source)
        with transaction() as lock:
            validate_registration(source)
            checkout_core(Path(source))
            # The apply child bounds the migration prompt by this deadline.
            run(['bash', str(Path(source) / 'scripts/apply-layer.sh'), *options],
                deadline=SETUP_APPLY_SECONDS, capture=False, lock_fd=lock.fileno(),
                extra_env=dict(GROUNDWORK_APPLY_DEADLINE_SECONDS=str(SETUP_APPLY_SECONDS)))
        return 0
    if args and args[0] == 'marker' and len(args) >= 3 and args[2] in MARKERS:
        # Setup records state it cannot read back without root, using the same
        # atomic, symlink-refusing writes as the rest of the updater state.
        path = STATE / args[2]
        if args[1] == 'write' and len(args) == 4:
            atomic_write(path, args[3] + '\n')
            return 0
        if args[1] == 'remove' and len(args) == 3:
            check_path(path)
            path.unlink(missing_ok=True)
            return 0
    if args and args[0] == 'validate' and len(args) == 2:
        validate_registration(args[1])
        return 0
    if args and args[0] == 'preferences' and len(args) == 2 and args[1] in ('setup', 'update'):
        value = settings(args[1] == 'update')
        if value:
            lines = [int(value['keyd']), int(value['screen_sharing']), value['obsidian_vault'],
                     int(blip_enabled(value)), int(local_inference_enabled(value))]
            # A sixth line only when an offer was declined or git-filter-repo is on, so
            # older readers see five. The seventh line is git-filter-repo, only when on.
            declined = declined_migrations(value)
            gfr = git_filter_repo_enabled(value)
            if declined or gfr:
                lines.append(','.join(declined))
            if gfr:
                lines.append(1)
            print('\n'.join(str(line) for line in lines))
        return 0
    if args and args[0] == 'register' and len(args) in (5, 6, 7, 8, 9) and args[2] in ('0', '1') and args[3] in ('0', '1'):
        if len(args) >= 6 and args[5] not in ('0', '1'):
            print('Invalid Blip choice; nothing changed.', file=sys.stderr)
            return 64
        if len(args) >= 7 and args[6] not in ('0', '1'):
            print('Invalid local inference choice; nothing changed.', file=sys.stderr)
            return 64
        if len(args) >= 9 and args[8] not in ('0', '1'):
            print('Invalid git-filter-repo choice; nothing changed.', file=sys.stderr)
            return 64
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
            print('Also run omarchy update, update listed Omarchy shell plugins, update T3 Nightly (including its background service), and update the installed Bitwarden and FreeTube user Flatpaks.')
            print(f'When Blip is opted in, update {BLIP_ID} only in an interactive terminal, without --yes, so its diff can be reviewed.')
        else:
            print('Listed shell plugins are installed or retired. Existing plugin checkouts stay at their current revisions.')
        print('--check fetches and displays repository changes without advancing the checkout or applying settings.')
        return 0
    if options not in ([], ['--check']):
        print('Unknown or combined arguments. Use --help; nothing changed.', file=sys.stderr)
        return 64
    # Remaining stages finished, but a warning still needs attention.
    return 1 if update(scope, check=options == ['--check']) == 'completed_with_warnings' else 0


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
