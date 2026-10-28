#!/usr/bin/env python3
# Groundwork Omarchy syncthing
"""Apply the Groundwork Syncthing contract on this machine and pair devices.

The contract is groundwork-core/docs/syncthing.md. This file implements the
Omarchy side: the listen-address apply, the two shared folders, and
`groundwork syncthing-pair`. It talks only to the Syncthing on this machine.
The API key is read from the local config.xml and sent as a header to
127.0.0.1; it is never printed, logged, or passed as an argument.
"""
import ipaddress
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET


MARKER = '# Groundwork Omarchy syncthing'
SERVICE = 'syncthing.service'
MINIMUM_VERSION = (2, 1, 2)
GUI_ADDRESS = '127.0.0.1:8384'
SYNC_PORT = 22000
TAILSCALE_V4 = ipaddress.ip_network('100.64.0.0/10')
TAILSCALE_V6 = ipaddress.ip_network('fd7a:115c:a1e0::/48')
ALLOWED_NETWORKS = ['100.64.0.0/10', 'fd7a:115c:a1e0::/48']
SHARED = {'id': 'groundwork-shared', 'label': 'Shared'}
FREETUBE = {'id': 'groundwork-freetube', 'label': 'FreeTube'}
DEVICE_ID = re.compile(r'^(?:[A-Z2-7]{7}-){7}[A-Z2-7]{7}$')
HOSTNAME = re.compile(r'^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$')
# Exit status of `apply` when Tailscale has no address yet: retry on the next apply.
EXIT_WAITING = 75


class SyncthingError(Exception):
    pass


class Waiting(Exception):
    pass


class ApiError(SyncthingError):
    def __init__(self, status, path):
        super().__init__(f'Syncthing answered {status} for {path}.')
        self.status = status


def shared_path():
    return str(Path.home() / 'Shared')


def freetube_path():
    return str(Path.home() / 'Shared' / 'FreeTube')


def state_home():
    """The state directory the user service will use, not this shell's guess."""
    shown = systemctl('show-environment', check=False, capture=True)
    if shown.returncode == 0:
        for line in (shown.stdout or '').splitlines():
            if line.startswith('XDG_STATE_HOME=') and line.split('=', 1)[1]:
                return Path(line.split('=', 1)[1])
        return Path.home() / '.local/state'
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')


def config_dir():
    new = state_home() / 'syncthing'
    old = Path.home() / '.config/syncthing'
    # Pre-1.27 path wins only while the new one has no config.
    if (old / 'config.xml').exists() and not (new / 'config.xml').exists():
        return old
    return new


# ---- binary, units, and environment -------------------------------------

def unit_binary():
    shown = systemctl('show', SERVICE, '-p', 'ExecStart', check=False, capture=True)
    match = re.search(r'path=(\S+)', shown.stdout or '')
    return match.group(1) if match and Path(match.group(1)).is_file() else None


def syncthing_command():
    """The binary the user service runs; PATH must not name a different one."""
    unit, found = unit_binary(), shutil.which('syncthing')
    if unit and found and Path(unit).resolve() != Path(found).resolve():
        raise SyncthingError(f'The syncthing on PATH ({found}) is not the one {SERVICE} runs ({unit}). '
                             'Remove the extra copy; two copies would be two peers. Nothing was changed.')
    return unit or 'syncthing'


def syncthing_version():
    try:
        result = subprocess.run([syncthing_command(), '--version'], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as error:
        raise SyncthingError(f'Cannot run syncthing: {error}') from error
    return parse_version(result.stdout)


def parse_version(text):
    match = re.search(r'\bv?(\d+)\.(\d+)\.(\d+)', text or '')
    if not match:
        raise SyncthingError('Cannot read the Syncthing version.')
    return tuple(int(part) for part in match.groups())


def require_supported(version):
    # Through 2.1.1 a folder PATCH reset every omitted field (upstream 10389, 10746).
    if version < MINIMUM_VERSION:
        raise SyncthingError('Syncthing %s is older than 2.1.2. Update the syncthing package; nothing was changed.'
                             % '.'.join(map(str, version)))


def runtime_env():
    # SSH and cron often lack the user manager's variables.
    env = dict(os.environ)
    runtime = Path('/run/user') / str(os.getuid())
    if not env.get('XDG_RUNTIME_DIR') and runtime.is_dir():
        env['XDG_RUNTIME_DIR'] = str(runtime)
    return env


def systemctl(*args, check=True, capture=False):
    return subprocess.run(['systemctl', '--user', *args], env=runtime_env(), check=check, timeout=60,
                          capture_output=capture, text=True)


def service_active():
    return systemctl('is-active', '--quiet', SERVICE, check=False).returncode == 0


def gui_override():
    """Return where STGUIADDRESS or --gui-address is set, or None."""
    if os.environ.get('STGUIADDRESS'):
        return 'the environment of this command'
    shown = systemctl('show-environment', check=False, capture=True)
    if 'STGUIADDRESS=' in (shown.stdout or ''):
        return 'the user service manager environment'
    unit = systemctl('show', SERVICE, '-p', 'Environment', '-p', 'ExecStart', '-p', 'EnvironmentFiles',
                     check=False, capture=True)
    if re.search(r'STGUIADDRESS=|--gui-address', unit.stdout or ''):
        return SERVICE
    for line in (unit.stdout or '').splitlines():
        if line.startswith('EnvironmentFiles='):
            path = line.split('=', 1)[1].split()[0] if line.split('=', 1)[1].split() else ''
            try:
                if path and 'STGUIADDRESS' in Path(path.lstrip('-')).read_text(errors='replace'):
                    return path
            except OSError:
                pass
    return None


def dropin_path():
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'systemd/user/syncthing.service.d/groundwork.conf'


DROPIN = '[Service]\n# The first start otherwise creates ~/Sync. The shared folder is ~/Shared.\nEnvironment=STNODEFAULTFOLDER=1\n'


def atomic_write(path, content, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise SyncthingError('Refusing to replace a symlink: ' + str(path))
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            os.fchmod(output.fileno(), mode)
            output.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def ensure_dropin():
    path = dropin_path()
    if path.is_file() and not path.is_symlink() and path.read_text() == DROPIN:
        return False
    atomic_write(path, DROPIN.encode(), 0o644)
    return True


LAUNCHER = f'''#!/usr/bin/env bash
{MARKER}
set -euo pipefail
case "${{1:-}}" in
  syncthing-pair)
    shift
    exec "$HOME/.local/bin/groundwork-syncthing" pair "$@"
    ;;
  *)
    echo "This command only provides syncthing-pair on this machine: groundwork syncthing-pair show|add ..." >&2
    exit 64
    ;;
esac
'''


def install_commands():
    """Copy this helper beside the other Groundwork commands; never replace a foreign file."""
    bin_dir = Path.home() / '.local/bin'
    bin_dir.mkdir(parents=True, exist_ok=True)
    helper = Path(__file__).read_bytes()
    atomic_write_owned(bin_dir / 'groundwork-syncthing', helper)
    try:
        atomic_write_owned(bin_dir / 'groundwork', LAUNCHER.encode())
    except SyncthingError as error:
        print(f'{error} Run the helper as groundwork-syncthing pair ... instead.', file=sys.stderr)


def atomic_write_owned(path, content):
    if path.is_symlink() or (path.exists() and (not path.is_file() or MARKER not in path.read_text(errors='replace'))):
        raise SyncthingError(f'An existing command belongs to another setup: {path}.')
    if path.exists() and path.read_bytes() == content and os.access(path, os.X_OK):
        return
    atomic_write(path, content, 0o755)


# ---- Tailscale ------------------------------------------------------------

def tailscale_ip(flag, network):
    try:
        result = subprocess.run(['tailscale', 'ip', flag], capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in result.stdout.split():
        try:
            address = ipaddress.ip_address(line)
        except ValueError:
            continue
        if address in network:
            return str(address)
    return None


def tailscale_running():
    # Like screen sharing: an address printed while Tailscale is stopped is not usable.
    try:
        result = subprocess.run(['tailscale', 'status', '--json'], capture_output=True, text=True, timeout=10, check=True)
        state = json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return state.get('BackendState') == 'Running' and (state.get('Self') or {}).get('Online') is True


def tailscale_addresses():
    if not tailscale_running():
        raise Waiting()
    v4 = tailscale_ip('-4', TAILSCALE_V4)
    if v4 is None:
        raise Waiting()
    return v4, tailscale_ip('-6', TAILSCALE_V6)


def listen_addresses(v4, v6):
    urls = [f'tcp://127.0.0.1:{SYNC_PORT}', f'tcp://{v4}:{SYNC_PORT}', f'quic://{v4}:{SYNC_PORT}']
    if v6:
        urls += [f'tcp://[{v6}]:{SYNC_PORT}', f'quic://[{v6}]:{SYNC_PORT}']
    return urls


# ---- config.xml -----------------------------------------------------------

OPTION_FLAGS = ('relaysEnabled', 'globalAnnounceEnabled', 'localAnnounceEnabled', 'natEnabled')


def check_private_file(path):
    try:
        info = os.lstat(path)
    except OSError:
        raise SyncthingError(f'{path} is missing. Run groundwork-update first.') from None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise SyncthingError(f'{path} must be a regular file owned by you with mode 0600. Nothing was changed.')


def read_config(path):
    try:
        return ET.parse(path)
    except (OSError, ET.ParseError) as error:
        raise SyncthingError(f'Cannot read {path}: {error}') from error


def read_api_key(path):
    check_private_file(path)
    options = read_config(path).getroot().find('gui')
    key = options.findtext('apikey') if options is not None else None
    if not key:
        raise SyncthingError(f'{path} has no API key.')
    return key.strip()


def gui_tls(path):
    gui = read_config(path).getroot().find('gui')
    return gui is not None and gui.get('tls') == 'true'


def edit_options_xml(path, addresses):
    """Before the first serve, write the overrides into the local config."""
    tree = read_config(path)
    root = tree.getroot()
    options = root.find('options')
    if options is None:
        raise SyncthingError(f'{path} has no options element.')
    present = [element.text for element in options.findall('listenAddress')]
    flags = {name: options.findtext(name) for name in OPTION_FLAGS}
    if set(present) == set(addresses) and all(value == 'false' for value in flags.values()):
        return False
    index = next((position for position, child in enumerate(options) if child.tag == 'listenAddress'), 0)
    for element in options.findall('listenAddress'):
        options.remove(element)
    for offset, address in enumerate(addresses):
        element = ET.Element('listenAddress')
        element.text = address
        options.insert(index + offset, element)
    for name in OPTION_FLAGS:
        element = options.find(name)
        if element is None:
            element = ET.SubElement(options, name)
        element.text = 'false'
    write_config(path, tree)
    return True


def write_config(path, tree):
    ET.indent(tree)
    buffer = io.BytesIO()
    tree.write(buffer, encoding='utf-8', xml_declaration=True)
    atomic_write(path, buffer.getvalue(), 0o600)


def gui_address_in_config(path):
    gui = read_config(path).getroot().find('gui')
    return gui.findtext('address') if gui is not None else None


# ---- REST -----------------------------------------------------------------

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        # The API key must not follow a redirect anywhere.
        return None


class Api:
    def __init__(self, key, base=None):
        self.key = key
        self.base = base or 'http://' + GUI_ADDRESS
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)

    def request(self, method, path, body=None, allow_missing=False):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method,
                                         headers={'X-API-Key': self.key, 'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=20) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            if allow_missing and error.code == 404:
                return None
            raise ApiError(error.code, path) from None
        except (urllib.error.URLError, OSError) as error:
            raise SyncthingError(f'Cannot reach Syncthing at {self.base}: {getattr(error, "reason", error)}') from None
        return json.loads(raw) if raw.strip() else None

    def get(self, path, allow_missing=False):
        return self.request('GET', path, allow_missing=allow_missing)

    def post(self, path, body=None):
        return self.request('POST', path, body)

    def put(self, path, body):
        return self.request('PUT', path, body)

    def patch(self, path, body):
        return self.request('PATCH', path, body)


def wait_for(check, seconds, pause=0.5):
    deadline = time.monotonic() + seconds
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() >= deadline:
            return value
        time.sleep(pause)


def wait_for_api(api, seconds=60):
    def ready():
        try:
            return api.get('/rest/system/status')
        except SyncthingError:
            return None
    status = wait_for(ready, seconds)
    if not status:
        raise SyncthingError('Syncthing did not answer on ' + GUI_ADDRESS + '. Check: systemctl --user status ' + SERVICE)
    return status


def require_daemon_supported(api):
    # The binary can be upgraded while an older daemon keeps running.
    require_supported(parse_version((api.get('/rest/system/version') or {}).get('version', '')))


def listening_on_gui():
    host, port = GUI_ADDRESS.rsplit(':', 1)
    token = ''.join(f'{int(part):02X}' for part in reversed(host.split('.'))) + f':{int(port):04X}'
    try:
        lines = Path('/proc/net/tcp').read_text().splitlines()[1:]
    except OSError:
        return False
    # State 0A is LISTEN. Only the exact loopback socket, owned by this user, counts:
    # the API key is sent to it.
    return any(fields[1] == token and fields[3] == '0A' and fields[7] == str(os.getuid())
               for fields in (line.split() for line in lines) if len(fields) > 7)


# ---- folders --------------------------------------------------------------

def own_folder_device(device_id, defaults):
    entry = {key: defaults[key] for key in ('introducedBy', 'encryptionPassword') if key in defaults}
    entry.update(deviceID=device_id)
    entry.setdefault('introducedBy', '')
    entry.setdefault('encryptionPassword', '')
    return entry


def trashcan_current(versioning):
    versioning = versioning or {}
    return versioning.get('type') == 'trashcan' and str((versioning.get('params') or {}).get('cleanoutDays')) == '0'


def trashcan(versioning):
    result = dict(versioning or {})
    result['type'] = 'trashcan'
    result['params'] = dict(result.get('params') or {}, cleanoutDays='0')
    return result


def folder_spec(folder):
    return (shared_path() if folder is SHARED else freetube_path())


def new_folder(api, folder, device_id):
    defaults = api.get('/rest/config/defaults/folder')
    device_defaults = api.get('/rest/config/defaults/device')
    created = dict(defaults, id=folder['id'], label=folder['label'], path=folder_spec(folder), type='sendreceive')
    created['devices'] = [own_folder_device(device_id, device_defaults)]
    if folder is FREETUBE:
        created['versioning'] = trashcan(created.get('versioning'))
    return created


def check_folder_path(folder, current):
    if current.get('path') != folder_spec(folder):
        raise SyncthingError(f"Folder {folder['id']} points at {current.get('path')!r}, not {folder_spec(folder)!r}. "
                             'It was not changed. Move it back or remove it in the GUI, then rerun.')


def ensure_folders(api, device_id):
    for folder in (SHARED, FREETUBE):
        current = api.get('/rest/config/folders/' + folder['id'], allow_missing=True)
        if current is None:
            api.post('/rest/config/folders', new_folder(api, folder, device_id))
            print(f"Created the {folder['label']} folder.")
            continue
        check_folder_path(folder, current)
        # A later apply never PUTs a default folder object over an existing one,
        # and never pauses or resumes it. Only the FreeTube versioning is ours.
        if folder is FREETUBE and not trashcan_current(current.get('versioning')):
            api.patch('/rest/config/folders/' + folder['id'], {'versioning': trashcan(current.get('versioning'))})
            print('Restored trash-can versioning on the FreeTube folder.')
    separate_freetube_from_parent(api)


def separate_freetube_from_parent(api):
    """groundwork-shared must not have indexed FreeTube's databases."""
    listing = api.get('/rest/db/browse?folder=%s&prefix=FreeTube' % SHARED['id'], allow_missing=True)
    if not listing:
        return
    current = api.get('/rest/config/folders/' + SHARED['id'])
    me = api.get('/rest/system/status')['myID']
    # Un-share first so the rebuilt index is never announced as deletions.
    # Say so before the reset: a rerun after an interruption finds nothing to rebuild.
    print('Removing other devices from the Shared folder and rebuilding its index without FreeTube. '
          'Pair again with groundwork syncthing-pair add.')
    current['devices'] = [entry for entry in current.get('devices', []) if entry.get('deviceID') == me]
    api.put('/rest/config/folders/' + SHARED['id'], current)
    ensure_stignore(Path(shared_path()) / '.stignore', '/FreeTube', first=True)
    api.post('/rest/system/reset?folder=' + SHARED['id'])
    # The reset restarts Syncthing and rescans. An empty browse before then proves nothing.
    wait_for_api(api)
    if not wait_for(lambda: folder_idle(api), 180, pause=1):
        raise SyncthingError('groundwork-shared did not finish rescanning after the reset. Rerun apply to check it again.')
    if api.get('/rest/db/browse?folder=%s&prefix=FreeTube' % SHARED['id'], allow_missing=True):
        raise SyncthingError('groundwork-shared still lists FreeTube files after the reset. Other devices were removed from '
                             'that folder; fix the ignore file at ' + str(Path(shared_path()) / '.stignore') + ' and rerun.')
    print('Rebuilt the Shared index without FreeTube.')


def folder_idle(api):
    try:
        return (api.get('/rest/db/status?folder=' + SHARED['id']) or {}).get('state') == 'idle'
    except SyncthingError:
        return False


def ensure_stignore(path, pattern, first):
    if path.is_symlink():
        raise SyncthingError('Refusing to use a symlinked ignore file: ' + str(path))
    lines = path.read_text().splitlines() if path.exists() else []
    wanted = [pattern] + [line for line in lines if line != pattern] if first else lines + ([] if pattern in lines else [pattern])
    if wanted == lines:
        return False
    atomic_write(path, ('\n'.join(wanted) + '\n').encode(), 0o644)
    return True


def ensure_directories():
    for path in (Path(shared_path()), Path(freetube_path())):
        path.mkdir(parents=True, exist_ok=True)
    # The parent ignores FreeTube first, so the databases are never published through it.
    # The temp pattern keeps a crash leftover such as history.db~ out of the share.
    # Both files exist before either folder is added and before the first scan.
    ensure_stignore(Path(shared_path()) / '.stignore', '/FreeTube', first=True)
    ensure_stignore(Path(freetube_path()) / '.stignore', '*~', first=False)


# ---- apply ----------------------------------------------------------------

def generate_config(config_path):
    if config_path.exists():
        return
    if service_active():
        raise SyncthingError(f'{SERVICE} is running but {config_path} is missing. Nothing was changed.')
    # Write where the user service will read, not where this shell would.
    env = dict(os.environ, STNODEFAULTFOLDER='1', XDG_STATE_HOME=str(state_home()))
    subprocess.run([syncthing_command(), 'generate'], env=env, check=True, timeout=120, capture_output=True)
    if not config_path.exists():
        raise SyncthingError(f'syncthing generate did not write {config_path}.')


def apply():
    require_supported(syncthing_version())
    install_commands()
    try:
        v4, v6 = tailscale_addresses()
    except Waiting:
        v4 = v6 = None
    serving = service_active()
    if v4 is None and not serving:
        print('Syncthing is installed but not started: Tailscale has no address yet. '
              'Sign in with sudo tailscale up, then rerun groundwork-update.', file=sys.stderr)
        return EXIT_WAITING
    where = gui_override()
    if where:
        raise SyncthingError(f'STGUIADDRESS or --gui-address is set in {where}. Remove it; Syncthing was not started.')
    if ensure_dropin():
        systemctl('daemon-reload')
    config_path = config_dir() / 'config.xml'
    generate_config(config_path)
    if gui_tls(config_path):
        raise SyncthingError('The Syncthing GUI uses TLS here; Groundwork talks to http://' + GUI_ADDRESS + '. Nothing more was changed.')
    ensure_directories()
    addresses = listen_addresses(v4, v6) if v4 else None
    if not serving:
        # Never serve on the upstream "default" listen addresses, even briefly.
        if gui_address_in_config(config_path) != GUI_ADDRESS:
            fix_gui_address_xml(config_path)
        edit_options_xml(config_path, addresses)
        systemctl('daemon-reload')
        systemctl('enable', '--now', SERVICE)
    api = Api(read_api_key(config_path))
    try:
        status = wait_for_api(api)
    except SyncthingError:
        if not serving:
            # It may have read a different config directory and be listening on the upstream defaults.
            systemctl('disable', '--now', SERVICE, check=False)
            raise SyncthingError(f'{SERVICE} did not answer with the API key in {config_path}, so it was stopped and disabled. '
                                 'Check which state directory the user service uses, then rerun.') from None
        raise
    require_daemon_supported(api)
    ensure_gui_address(api)
    restart_needed = ensure_options(api, addresses)
    if serving and addresses is not None and not restart_needed and not wait_for(lambda: listeners_match(api, addresses), 10):
        # An earlier run may have stopped between the PATCH and the restart.
        restart_needed = True
    ensure_folders(api, status['myID'])
    if restart_needed:
        systemctl('restart', SERVICE)
        wait_for_api(api)
    confirm(api, addresses)
    if addresses is None:
        print('Tailscale has no address. Kept the listen addresses already in effect; the next apply retries them.',
              file=sys.stderr)
        return EXIT_WAITING
    print('Syncthing is running as a user service and shares ~/Shared over the tailnet.')
    print('Pair with another machine using: groundwork syncthing-pair show')
    return 0


def fix_gui_address_xml(path):
    tree = read_config(path)
    gui = tree.getroot().find('gui')
    if gui is None:
        raise SyncthingError(f'{path} has no gui element.')
    address = gui.find('address')
    if address is None:
        address = ET.SubElement(gui, 'address')
    address.text = GUI_ADDRESS
    write_config(path, tree)


def ensure_gui_address(api):
    current = api.get('/rest/config/gui')
    if current.get('address') != GUI_ADDRESS:
        # PUT would clear the password.
        api.patch('/rest/config/gui', {'address': GUI_ADDRESS})


def ensure_options(api, addresses):
    """Return True when the listen list changed and the service must restart."""
    options = api.get('/rest/config/options')
    body = {name: False for name in OPTION_FLAGS if options.get(name) is not False}
    changed = False
    if addresses is not None and set(options.get('listenAddresses') or []) != set(addresses):
        # PATCH replaces the list, so it carries every URL.
        body['listenAddresses'] = addresses
        changed = True
    if body:
        api.patch('/rest/config/options', body)
    return changed


def listeners_match(api, addresses):
    status = api.get('/rest/system/status').get('connectionServiceStatus') or {}
    return set(status) == set(addresses) and all(not (entry or {}).get('error') for entry in status.values())


def confirm(api, addresses):
    where = gui_override()
    if where:
        raise SyncthingError(f'STGUIADDRESS or --gui-address is set in {where}. The step is unfinished until it is removed.')
    if not wait_for(listening_on_gui, 30):
        raise SyncthingError(f'Syncthing is not listening on {GUI_ADDRESS}. The step is unfinished.')
    if addresses is None:
        return
    configured = api.get('/rest/config/options').get('listenAddresses') or []
    if set(configured) != set(addresses):
        raise SyncthingError('Syncthing did not keep the Groundwork listen addresses.')

    if not wait_for(lambda: listeners_match(api, addresses), 30):
        raise SyncthingError('The running Syncthing listeners do not match the Groundwork list, or one failed to bind. '
                             'The step is unfinished.')


# ---- pairing --------------------------------------------------------------

def device_id_from_binary():
    binary = syncthing_command()
    for command in ([binary, 'device-id'], [binary, '--device-id']):
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            continue
        found = result.stdout.strip()
        if result.returncode == 0 and DEVICE_ID.match(found):
            return found
    return None


def pair_show():
    require_supported(syncthing_version())
    found = device_id_from_binary()
    if not found:
        raise SyncthingError('Cannot read this machine\'s device ID from the syncthing binary.')
    print(found)
    return 0


def start_hint():
    return 'systemctl --user enable --now ' + SERVICE


def pair_add(device_id, name, magicdns):
    require_supported(syncthing_version())
    if not DEVICE_ID.match(device_id):
        raise SyncthingError('That is not a Syncthing device ID.')
    if not name or len(name) > 64 or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise SyncthingError('NAME must be 1 to 64 printable characters.')
    if not HOSTNAME.match(magicdns):
        raise SyncthingError('MAGICDNS must be the other machine\'s host name.')
    if not service_active() or not listening_on_gui():
        print('Syncthing is not running on ' + GUI_ADDRESS + '. Start it with: ' + start_hint(), file=sys.stderr)
        return 1
    config_path = config_dir() / 'config.xml'
    check_private_file(config_path)
    check_private_file(config_path.parent / 'key.pem')
    api = Api(read_api_key(config_path))
    require_daemon_supported(api)
    me = api.get('/rest/system/status')['myID']
    if device_id == me:
        raise SyncthingError('That is this machine\'s own device ID. Run add with the other machine\'s ID.')
    # A missing folder is created below with the peer already in it. Its ignore
    # files must exist first, and the parent must not have indexed FreeTube.
    ensure_directories()
    separate_freetube_from_parent(api)
    wanted = {'name': name, 'addresses': [f'tcp://{magicdns}:{SYNC_PORT}', f'quic://{magicdns}:{SYNC_PORT}'],
              'allowedNetworks': ALLOWED_NETWORKS}
    existing = api.get('/rest/config/devices/' + device_id, allow_missing=True)
    if existing is None:
        created = api.get('/rest/config/defaults/device')
        created.update(deviceID=device_id, **wanted)
        api.post('/rest/config/devices', created)
    elif any(existing.get(key) != value for key, value in wanted.items()):
        existing.update(wanted)
        api.put('/rest/config/devices/' + device_id, existing)
    device_defaults = api.get('/rest/config/defaults/device')
    for folder in (SHARED, FREETUBE):
        share_folder(api, folder, device_id, device_defaults, me)
    print(f"Shared the {SHARED['label']} and {FREETUBE['label']} folders with {name}. "
          'The other machine runs groundwork syncthing-pair add with this machine\'s device ID.')
    return 0


def share_folder(api, folder, device_id, device_defaults, me):
    current = api.get('/rest/config/folders/' + folder['id'], allow_missing=True)
    if current is None:
        current = new_folder(api, folder, me)
        current['devices'].append(own_folder_device(device_id, device_defaults))
        api.post('/rest/config/folders', current)
        return
    check_folder_path(folder, current)
    dirty = False
    if device_id not in [entry.get('deviceID') for entry in current.get('devices', [])]:
        current.setdefault('devices', []).append(own_folder_device(device_id, device_defaults))
        dirty = True
    if folder is FREETUBE and not trashcan_current(current.get('versioning')):
        current['versioning'] = trashcan(current.get('versioning'))
        dirty = True
    if dirty:
        # The object from the GET, so a paused folder stays paused.
        api.put('/rest/config/folders/' + folder['id'], current)


def usage():
    print('Usage: groundwork syncthing-pair show', file=sys.stderr)
    print('       groundwork syncthing-pair add --device-id DEVICE_ID --name NAME --magicdns MAGICDNS', file=sys.stderr)
    return 64


def parse_add(args):
    values = {}
    names = {'--device-id': 'device_id', '--name': 'name', '--magicdns': 'magicdns'}
    while args:
        if len(args) < 2 or args[0] not in names or names[args[0]] in values:
            return None
        values[names[args[0]]] = args[1]
        args = args[2:]
    return values if set(values) == set(names.values()) else None


def main(args):
    if args == ['apply']:
        return apply()
    if args[:1] == ['pair']:
        if args[1:] == ['show']:
            return pair_show()
        if args[1:2] == ['add']:
            values = parse_add(args[2:])
            return pair_add(**values) if values else usage()
        return usage()
    print('Use groundwork syncthing-pair, or let install.sh and groundwork-update run apply.', file=sys.stderr)
    return 64


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Waiting:
        sys.exit(EXIT_WAITING)
    except (SyncthingError, OSError, subprocess.SubprocessError, ValueError, KeyError, AttributeError, TypeError) as error:
        print(f'Groundwork Syncthing: {error}', file=sys.stderr)
        sys.exit(1)
