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
import shutil
import signal
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


def register(source, keyd, screen_sharing, vault, blip='0', local_inference='0', declined=None):
    source = Path(source).resolve()
    previous = validate_registration(source)
    if blip not in ('0', '1'):
        raise UpdateError('Blip choice must be 0 or 1.')
    if local_inference not in ('0', '1'):
        raise UpdateError('Local inference choice must be 0 or 1.')
    value = dict(previous or {}, schema=1, source=str(source), keyd=keyd == '1',
                 screen_sharing=screen_sharing == '1', obsidian_vault=vault, blip=blip == '1',
                 local_inference=local_inference == '1')
    # None keeps whatever was saved. apply-layer.sh always passes the full list.
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


def run(args, cwd=None, deadline=20, capture=True, lock_fd=None, extra_env=None, stdin=None, env=None):
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
                                   env=environment)
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


def load_alignments(binary):
    alignments = []
    for line in run(['readelf', '-lW', str(binary)]).splitlines():
        fields = line.split()
        if fields[:1] == ['LOAD']:
            alignments.append(int(fields[-1], 16))
    return alignments


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
        header = path.open('rb').read(4)
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


def bun_alignment_problem(binary):
    """Reason `binary` cannot be mapped on this kernel, or None.

    Calls `load_alignments`, the same readelf -lW LOAD check the T3 guard uses.
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
    # Same predicate as misaligned_elf_files: a LOAD alignment below the page cannot be mapped.
    if not alignments or any(alignment < page for alignment in alignments):
        return f'{binary} is not aligned for {page // 1024} KiB pages and cannot start. {issue}'
    return None


def misaligned_elf_files(root, page):
    """Return every ELF file under root with a LOAD segment aligned below page."""
    misaligned = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open('rb') as handle:
            if handle.read(4) != b'\x7fELF':
                continue
        alignments = load_alignments(path)
        if path.name == 't3' and path.parent.parent == root and not alignments:
            raise UpdateError(f'readelf found no LOAD segments in {path.name}.')
        if any(alignment < page for alignment in alignments):
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


def rejected_t3(page):
    return rejected_versions(rejected_t3_path(), page)


def reject_t3(page, version):
    reject_version(rejected_t3_path(), page, version)


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

    An executable or shared object whose LOAD segment is aligned below the
    kernel page size cannot be mapped there (pingdotgg/t3code#23433). An
    aligned build is smoke-tested with `--version` from its own directory.
    Only a signal death is remembered. A timeout or an OS error skips this
    run; the same version is checked again on the next update-all.
    """
    candidate = t3_latest_nightly()
    if candidate == installed:
        return None, candidate
    if candidate in rejected_t3(page):
        return page_size_reason(page), candidate
    if not shutil.which('readelf'):
        return 'readelf is missing, so the newest build cannot be checked (install binutils)', candidate
    if shutil.which('bwrap') is None:
        return t3_remember_skip(warnings, T3_NO_BWRAP), candidate
    with tempfile.TemporaryDirectory(prefix='groundwork-t3-') as directory:
        root = t3_download(candidate, directory)
        if misaligned_elf_files(root, page):
            reject_t3(page, candidate)
            return page_size_reason(page), candidate
        binary = next(path for path in root.glob('*/t3') if path.is_file())
        binary, directory = t3_resolved_smoke_paths(binary, directory)
        try:
            reported = run(t3_sandbox_command(binary, directory), cwd=str(binary.parent),
                           deadline=T3_CANDIDATE_SMOKE_SECONDS, stdin=subprocess.DEVNULL,
                           env=t3_smoke_environment(directory))
            got = reported_t3_version(reported)
        except (UpdateError, OSError, ValueError) as error:
            if t3_candidate_rejected(error):
                reject_t3(page, candidate)
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
            reject_t3(page, candidate)
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
                or {'--with-local-inference', '--without-local-inference'} <= set(options)):
            print('Choose only one option for each setup preference; nothing changed.', file=sys.stderr)
            return 64
        if '--help' in options or '-h' in options:
            print('Usage: ./install.sh [--with-keyd|--without-keyd] [--with-screen-sharing|--without-screen-sharing] [--with-blip|--without-blip] [--with-local-inference|--without-local-inference] [--migrate-to-omarchy-installers]')
            print('Saved setup choices are reused. Explicit options change those choices.')
            print('--migrate-to-omarchy-installers moves apps installed another way to Omarchy\'s own installer, even after an earlier no. See docs/decisions/0014-offer-migration-to-omarchy-installers.md.')
            print('Blip is off unless --with-blip is set. --without-blip leaves an existing Blip install unchanged.')
            print('Local inference is off unless --with-local-inference is set. --without-local-inference records that preference as off. See docs/local-inference.md for package and model removal.')
            return 0
        if run(['uname', '-s']) != 'Linux' or run(['uname', '-m']) != 'aarch64' or not shutil.which('omarchy'):
            raise UpdateError('This layer requires Apple Silicon Omarchy.')
        validate_registration(source)
        with transaction() as lock:
            validate_registration(source)
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
            # A sixth line only when an offer was declined, so older readers see five.
            declined = declined_migrations(value)
            if declined:
                lines.append(','.join(declined))
            print('\n'.join(str(line) for line in lines))
        return 0
    if args and args[0] == 'register' and len(args) in (5, 6, 7, 8) and args[2] in ('0', '1') and args[3] in ('0', '1'):
        if len(args) >= 6 and args[5] not in ('0', '1'):
            print('Invalid Blip choice; nothing changed.', file=sys.stderr)
            return 64
        if len(args) >= 7 and args[6] not in ('0', '1'):
            print('Invalid local inference choice; nothing changed.', file=sys.stderr)
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
