#!/usr/bin/env python3
"""Apply the shared Omarchy shell-plugin list. This file names no plugin ids."""
import http.client
import json
import os
from pathlib import Path
import re
import shlex
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.parse


DEFAULT_LIST_URL = 'https://raw.githubusercontent.com/jasondockery/groundwork-core/main/omarchy/plugins.json'
LIST_DEADLINE_SECONDS = 20
MAX_BYTES = 1_048_576
MAX_REDIRECTS = 5
SECTIONS = {'left', 'center', 'right'}
ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
ARCH_RE = re.compile(r'^[A-Za-z0-9_]+$')
REPO_ROOT = Path(__file__).resolve().parent.parent
TRACKPAD_FILES = (
    ('mouse.lua', 'mouse'),
    ('omacontrol.lua', 'omacontrol'),
)


class ListError(Exception):
    """The plugin document or an exclusion file is not usable."""


class FetchError(Exception):
    """The plugin document could not be downloaded."""


def flat(message):
    return ' '.join(str(message).split())[:240]


def say(message):
    text = flat(message)
    if text:
        print(text, flush=True)


def warn(message):
    text = flat(message)
    if text:
        print(text, file=sys.stderr, flush=True)


def parse_args(argv):
    if argv == []:
        return False
    if argv == ['--update']:
        return True
    print('Usage: omarchy-plugins.py [--update]', file=sys.stderr)
    raise SystemExit(2)


def state_dir():
    base = os.environ.get('XDG_STATE_HOME', '').strip()
    root = Path(base) if base else Path.home() / '.local/state'
    return root / 'groundwork'


def config_dir():
    base = os.environ.get('XDG_CONFIG_HOME', '').strip()
    root = Path(base) if base else Path.home() / '.config'
    return root / 'groundwork'


def plugins_root():
    return Path.home() / '.config/omarchy/plugins'


def refuse_symlinks(path):
    for candidate in [*path.parents, path]:
        if candidate.is_symlink():
            raise OSError(f'Refusing a symbolic link at {candidate}')


def atomic_write(path, text):
    refuse_symlinks(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not path.is_file():
        raise OSError(f'Refusing to replace {path}')
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write(text)
            os.fchmod(output.fileno(), 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def dump_json(value):
    return json.dumps(value, indent=2, sort_keys=True) + '\n'


def read_private_json(path):
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        raise OSError(f'Refusing {path}')
    if path.stat().st_size > MAX_BYTES:
        raise ListError('plugin list is too large')
    return json.loads(path.read_text(encoding='utf-8'))


def valid_id(value):
    return (type(value) is str and len(value) <= 200 and bool(ID_RE.fullmatch(value))
            and '..' not in value and not value.startswith('omarchy.'))


def valid_reason(value):
    return type(value) is str and bool(value.strip()) and len(value) <= 500 and '\n' not in value and '\r' not in value


def valid_repo(url):
    if type(url) is not str or len(url) > 300 or any(ord(ch) < 32 or ord(ch) == 127 for ch in url):
        return False
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme != 'https' or parts.netloc != 'github.com' or parts.username or parts.password
            or parts.query or parts.fragment):
        return False
    segments = parts.path.split('/')
    # urlsplit keeps a leading empty segment for an absolute path.
    if len(segments) != 3 or segments[0] != '' or '' in segments[1:]:
        return False
    owner, repo = segments[1], segments[2]
    if repo.endswith('.git'):
        repo = repo[:-4]
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', owner):
        return False
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,100}', repo) or '..' in repo or repo.startswith('.'):
        return False
    return True


def valid_setup_command(command):
    if type(command) is not str or not command or len(command) > 200:
        return False
    if command.startswith('/') or '\\' in command or '\n' in command or '\0' in command:
        return False
    parts = command.split('/')
    return bool(parts) and all(part not in ('', '.', '..') for part in parts)


def validate_setup(value):
    if type(value) is not dict:
        raise ListError('plugin setup command is invalid')
    command = value.get('command')
    if not valid_setup_command(command):
        raise ListError('plugin setup command is invalid')
    needs_sudo = value.get('needsSudo', False)
    if type(needs_sudo) is not bool:
        raise ListError('plugin setup needsSudo must be a boolean')
    return {'command': command, 'needsSudo': needs_sudo}


def validate_plugin(value):
    if type(value) is not dict or not valid_id(value.get('id')):
        raise ListError('invalid plugin id')
    if not valid_repo(value.get('repo')):
        raise ListError('plugin repo must be an https GitHub URL')
    if not valid_reason(value.get('reason')):
        raise ListError('plugin reason is missing')
    plugin = {'id': value['id'], 'repo': value['repo'], 'reason': value['reason']}
    if 'batch' in value:
        if type(value['batch']) is not int or value['batch'] < 0:
            raise ListError('plugin batch must be an integer')
        plugin['batch'] = value['batch']
    if 'arch' in value:
        arch = value['arch']
        if type(arch) is not list or not arch or any(type(item) is not str or not ARCH_RE.fullmatch(item) for item in arch):
            raise ListError('plugin arch is invalid')
        plugin['arch'] = list(arch)
    if 'section' in value:
        if value['section'] not in SECTIONS:
            raise ListError('plugin section must be left, center, or right')
        plugin['section'] = value['section']
    if 'setup' in value:
        plugin['setup'] = validate_setup(value['setup'])
    return plugin


def validate_retired(value):
    if type(value) is not dict or not valid_id(value.get('id')):
        raise ListError('invalid plugin id')
    if not valid_reason(value.get('reason')):
        raise ListError('plugin reason is missing')
    return {'id': value['id'], 'reason': value['reason']}


def validate_list(data):
    if type(data) is not dict or type(data.get('schemaVersion')) is not int or data['schemaVersion'] != 1:
        raise ListError('unsupported schemaVersion')
    plugins = data.get('plugins')
    retired = data.get('retired')
    if type(plugins) is not list:
        raise ListError('plugins must be a list')
    if type(retired) is not list:
        raise ListError('retired must be a list')
    normalized_plugins = [validate_plugin(item) for item in plugins]
    normalized_retired = [validate_retired(item) for item in retired]
    ids = [item['id'] for item in normalized_plugins]
    retired_ids = [item['id'] for item in normalized_retired]
    if len(ids) != len(set(ids)) or len(retired_ids) != len(set(retired_ids)):
        raise ListError('plugin id is duplicated')
    if set(ids) & set(retired_ids):
        raise ListError('plugin id is in both plugins and retired')
    return {'schemaVersion': 1, 'plugins': normalized_plugins, 'retired': normalized_retired}


def parse_list(raw):
    if len(raw.encode('utf-8')) > MAX_BYTES:
        raise ListError('plugin list is too large')
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ListError('plugin list is not valid JSON') from error
    return validate_list(data)


def https_url(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        raise ListError('plugin list URL must use https')
    return parts


def arm(sock, deadline):
    """Bound the next socket read by the time left on the download deadline."""
    left = deadline - time.monotonic()
    if sock is None or left <= 0:
        raise TimeoutError('plugin list download exceeded its deadline')
    sock.settimeout(left)


def read_bounded(response, sock, deadline, limit):
    chunks = []
    total = 0
    while total <= limit and not response.isclosed():
        arm(sock, deadline)
        chunk = response.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ListError('plugin list is too large')
        chunks.append(chunk)
    return b''.join(chunks)


def discard_bounded(response, sock, deadline):
    remaining = 65536
    while remaining > 0 and not response.isclosed():
        arm(sock, deadline)
        chunk = response.read(min(8192, remaining))
        if not chunk:
            break
        remaining -= len(chunk)


def fetch_https(url, deadline):
    current = url
    for _redirect in range(MAX_REDIRECTS + 1):
        parts = https_url(current)
        path = parts.path or '/'
        if parts.query:
            path += '?' + parts.query
        leftover = deadline - time.monotonic()
        if leftover <= 0:
            raise FetchError('plugin list download exceeded its deadline')
        connection = http.client.HTTPSConnection(parts.hostname, parts.port or 443, timeout=leftover,
                                                  context=ssl.create_default_context())
        response = None
        try:
            connection.request('GET', path, headers={
                'User-Agent': 'groundwork-omarchy-plugins',
                'Accept': 'application/json',
                'Connection': 'close',
            })
            # A Connection: close response clears connection.sock before the body
            # is read. Keep this socket and bound every later read by it.
            sock = connection.sock
            arm(sock, deadline)
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location:
                    raise FetchError('plugin list redirect is missing a location')
                target = urllib.parse.urljoin(current, location)
                if urllib.parse.urlsplit(target).scheme != 'https':
                    raise ListError('rejected a non-https redirect')
                discard_bounded(response, sock, deadline)
                current = target
                continue
            if response.status != 200:
                discard_bounded(response, sock, deadline)
                raise FetchError(f'plugin list download failed with HTTP {response.status}')
            return read_bounded(response, sock, deadline, MAX_BYTES)
        except ListError:
            raise
        except (TimeoutError, socket.timeout) as error:
            raise FetchError('plugin list download exceeded its deadline') from error
        except OSError as error:
            raise FetchError('plugin list download failed') from error
        finally:
            if response is not None:
                response.close()
            connection.close()
    raise FetchError('plugin list redirected too many times')


def cache_path():
    return state_dir() / 'omarchy-plugins.json'


def record_path():
    return state_dir() / 'omarchy-plugins-installed.json'


def load_cache():
    try:
        data = read_private_json(cache_path())
    except (OSError, ListError, json.JSONDecodeError, UnicodeError):
        return None
    if data is None:
        return None
    try:
        return validate_list(data)
    except ListError:
        return None


def load_source():
    source = os.environ.get('GROUNDWORK_PLUGIN_LIST', '').strip() or DEFAULT_LIST_URL
    if '://' in source:
        body = fetch_https(source, time.monotonic() + LIST_DEADLINE_SECONDS)
        try:
            text = body.decode('utf-8')
        except UnicodeError as error:
            raise ListError('plugin list is not valid JSON') from error
        return parse_list(text)
    path = Path(source)
    try:
        data = read_private_json(path)
    except OSError as error:
        raise FetchError('plugin list is missing') from error
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ListError('plugin list is not valid JSON') from error
    if data is None:
        raise FetchError('plugin list is missing')
    try:
        return validate_list(data)
    except ListError:
        raise
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ListError('plugin list is not valid JSON') from error


def resolve_document():
    cached = load_cache()
    try:
        document = load_source()
    except ListError as error:
        if cached is not None:
            return cached, False, f'Omarchy plugin list is invalid ({flat(error)}); using the saved copy.'
        return None, False, f'Omarchy plugin list is invalid ({flat(error)}); nothing changed.'
    except FetchError:
        if cached is not None:
            return cached, False, 'Omarchy plugin list is unavailable; using the saved copy.'
        return None, False, 'Omarchy plugin list is unavailable; nothing changed.'
    return document, True, None


def describe_changes(previous, document):
    old_plugins = {item['id']: item for item in previous['plugins']} if previous else {}
    old_retired = [item['id'] for item in previous['retired']] if previous else []
    added = [item['id'] for item in document['plugins'] if item['id'] not in old_plugins]
    dropped = [item['id'] for item in old_plugins.values() if item['id'] not in {plugin['id'] for plugin in document['plugins']}]
    repo_changed, section_changed, setup_changed = [], [], []
    for item in document['plugins']:
        old = old_plugins.get(item['id'])
        if not old:
            continue
        if item['repo'] != old['repo']:
            repo_changed.append(item['id'])
        if item.get('section') != old.get('section'):
            section_changed.append(item['id'])
        if item.get('setup') != old.get('setup'):
            setup_changed.append(item['id'])
    new_retired = [item['id'] for item in document['retired']]
    retired = [item for item in new_retired if item not in old_retired]
    restored = [item for item in old_retired if item not in new_retired]
    parts = []
    for label, values in (('add', added), ('drop', dropped), ('repo', repo_changed),
                          ('section', section_changed), ('setup', setup_changed),
                          ('retire', retired), ('restore', restored)):
        if values:
            parts.append(label + ' ' + ', '.join(values))
    return '; '.join(parts)


def parse_exclusions(data):
    if type(data) is list:
        items = data
    elif (type(data) is dict and type(data.get('schemaVersion')) is int and data['schemaVersion'] == 1
            and type(data.get('exclusions')) is list and set(data) <= {'schemaVersion', 'exclusions'}):
        items = data['exclusions']
    else:
        raise ListError('exclusions must be a schemaVersion 1 object')
    found = {}
    for item in items:
        if type(item) is not dict or set(item) != {'id', 'reason'} or not valid_id(item.get('id')) or not valid_reason(item.get('reason')):
            raise ListError('each exclusion needs an id and a reason')
        if item['id'] in found:
            raise ListError('plugin id is duplicated')
        found[item['id']] = item['reason']
    return found


def load_exclusions():
    found = {}
    for path in (REPO_ROOT / 'omarchy-plugins.exclude.json', config_dir() / 'omarchy-plugins.exclude.json'):
        try:
            data = read_private_json(path)
        except (OSError, ListError, json.JSONDecodeError, UnicodeError) as error:
            raise ListError(f'Omarchy plugin exclusions in {path} are invalid; plugin changes were skipped.') from error
        if data is None:
            continue
        try:
            found.update(parse_exclusions(data))
        except ListError as error:
            raise ListError(f'Omarchy plugin exclusions in {path} are invalid; plugin changes were skipped.') from error
    return found


def load_record():
    path = record_path()
    try:
        data = read_private_json(path)
    except (OSError, ListError, json.JSONDecodeError, UnicodeError) as error:
        raise ListError(f'Omarchy plugin install record is invalid; plugin changes were skipped ({flat(error)}).') from error
    if data is None:
        return {'schemaVersion': 1, 'plugins': {}}
    plugins = data.get('plugins')
    if type(data) is not dict or type(data.get('schemaVersion')) is not int or data['schemaVersion'] != 1 or type(plugins) is not dict:
        raise ListError('Omarchy plugin install record is invalid; plugin changes were skipped.')
    clean = {}
    for plugin_id, entry in plugins.items():
        if not valid_id(plugin_id) or type(entry) is not dict or not valid_repo(entry.get('repo')):
            raise ListError('Omarchy plugin install record is invalid; plugin changes were skipped.')
        item = {'repo': entry['repo']}
        pending = entry.get('enablePending', False)
        if type(pending) is not bool:
            raise ListError('Omarchy plugin install record is invalid; plugin changes were skipped.')
        if pending:
            item['enablePending'] = True
        setup = entry.get('setup')
        if setup is not None:
            if (type(setup) is not dict or not valid_setup_command(setup.get('command'))
                    or type(setup.get('needsSudo')) is not bool):
                raise ListError('Omarchy plugin install record is invalid; plugin changes were skipped.')
            head = setup.get('head')
            if head is not None and (type(head) is not str or not re.fullmatch(r'[0-9a-fA-F]{4,64}', head)):
                raise ListError('Omarchy plugin install record is invalid; plugin changes were skipped.')
            item['setup'] = {'command': setup['command'], 'needsSudo': setup['needsSudo'], 'head': head}
        clean[plugin_id] = item
    return {'schemaVersion': 1, 'plugins': clean}


def save_json(path, value):
    text = dump_json(value)
    try:
        current = path.read_text(encoding='utf-8') if path.is_file() and not path.is_symlink() else None
    except OSError:
        current = None
    if current == text:
        return
    atomic_write(path, text)


def machine_arch():
    try:
        result = subprocess.run(['uname', '-m'], capture_output=True, text=True, timeout=5, check=True)
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return None
    arch = result.stdout.strip()
    if not ARCH_RE.fullmatch(arch):
        return None
    return arch


def shell_is_running():
    try:
        result = subprocess.run(['omarchy-shell', 'shell', 'ping'], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == 'ok'


def git_head(directory):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    env.pop('GIT_DIR', None)
    env.pop('GIT_WORK_TREE', None)
    try:
        result = subprocess.run(['git', '-C', str(directory), 'rev-parse', 'HEAD'],
                                capture_output=True, text=True, timeout=10, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    head = result.stdout.strip()
    if result.returncode != 0 or not re.fullmatch(r'[0-9a-fA-F]{4,64}', head):
        return None
    return head


def run_omarchy(args, timeout):
    try:
        return subprocess.run(['omarchy', *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return error


def command_failed(outcome):
    return isinstance(outcome, Exception) or outcome.returncode != 0


def failure_text(outcome):
    if isinstance(outcome, Exception):
        return flat(outcome)
    detail = (outcome.stderr or outcome.stdout or '').strip().splitlines()
    return flat(detail[0]) if detail else f'exit {outcome.returncode}'


def plugin_path(plugin_id):
    return plugins_root() / plugin_id


def path_exists(path):
    return path.exists() or path.is_symlink()


def manifest_id(directory):
    path = directory / 'manifest.json'
    if path.is_symlink() or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if type(data) is not dict or not valid_id(data.get('id')):
        return None
    return data['id']


def have_tty():
    return sys.stdin.isatty() and sys.stdout.isatty()


def command_line(executable, needs_sudo):
    quoted = shlex.quote(str(executable))
    if needs_sudo:
        return f'sudo -- {quoted}'
    return quoted


def setup_executable(directory, command):
    current = directory
    if current.is_symlink() or not current.is_dir():
        raise OSError(f'Refusing setup outside {directory}')
    for part in command.split('/'):
        current = current / part
        if current.is_symlink():
            raise OSError(f'Refusing a symbolic link at {current}')
    if not current.is_file():
        raise OSError(f'Setup command is missing: {current}')
    resolved = current.resolve()
    root = directory.resolve()
    if resolved != root and root not in resolved.parents:
        raise OSError(f'Refusing setup outside {directory}')
    return current


def sudo_ready(state):
    if state['ready'] is not None:
        return state['ready']
    if os.geteuid() == 0:
        state['ready'] = True
        return True
    try:
        probe = subprocess.run(['sudo', '-n', 'true'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        probe = None
    if probe is not None and probe.returncode == 0:
        state['ready'] = True
        return True
    if not have_tty():
        state['ready'] = False
        return False
    tty = None
    try:
        tty = os.open('/dev/tty', os.O_RDWR)
    except OSError:
        tty = None
    try:
        if tty is None:
            result = subprocess.run(['sudo', '-v'], timeout=300)
        else:
            result = subprocess.run(['sudo', '-v'], stdin=tty, stdout=tty, stderr=tty, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        state['ready'] = False
        return False
    finally:
        if tty is not None:
            os.close(tty)
    state['ready'] = result.returncode == 0
    return state['ready']


def setup_due(entry, recorded, head_moved, current_head):
    setup = entry.get('setup')
    if not setup or recorded is None:
        return False
    done = recorded.get('setup')
    if type(done) is not dict:
        return True
    if done.get('command') != setup['command'] or done.get('needsSudo') is not setup['needsSudo']:
        return True
    return bool(head_moved and done.get('head') != current_head)


def perform_setup(entry, directory, recorded, sudo_state):
    setup = entry['setup']
    try:
        executable = setup_executable(directory, setup['command'])
    except OSError as error:
        say(f"Omarchy plugin {entry['id']} setup failed: {flat(error)}.")
        return
    line = command_line(executable, setup['needsSudo'])
    if not have_tty():
        say(f"Omarchy plugin {entry['id']} setup was deferred; run it in a terminal: {line}")
        return
    if setup['needsSudo'] and not sudo_ready(sudo_state):
        say(f"Omarchy plugin {entry['id']} setup was deferred; run it in a terminal: {line}")
        return
    args = ['sudo', '--', str(executable)] if setup['needsSudo'] else [str(executable)]
    try:
        result = subprocess.run(args, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as error:
        say(f"Omarchy plugin {entry['id']} setup failed: {flat(error)}.")
        return
    if result.returncode != 0:
        say(f"Omarchy plugin {entry['id']} setup failed: exit {result.returncode}.")
        return
    recorded['setup'] = {'command': setup['command'], 'needsSudo': setup['needsSudo'], 'head': git_head(directory)}


def save_record(record):
    record['plugins'] = dict(sorted(record['plugins'].items()))
    save_json(record_path(), record)


def enable_command(item):
    command = ['plugin', 'enable', item['id']]
    if 'section' in item:
        command.extend(['--section', item['section']])
    return command


def hold_for_enable_retry(record, plugin_id, repo):
    """Drop a plugin this run just added so a failed enable can be retried.

    `omarchy plugin add` fails while the directory remains. Removing it keeps
    the next run retryable. If removal cannot clear that directory, remember
    enablePending and retry enable only. A directory that was already there is
    never removed or enabled again.
    """
    rollback = run_omarchy(['plugin', 'remove', plugin_id, '--yes'], 60)
    if not command_failed(rollback) and not path_exists(plugin_path(plugin_id)):
        return
    if command_failed(rollback):
        say(f'Omarchy plugin {plugin_id} remove failed: {failure_text(rollback)}.')
    else:
        say(f'Omarchy plugin {plugin_id} remove failed: the plugin directory is still present.')
    remember(record, plugin_id, repo, enable_pending=True)


def remember(record, plugin_id, repo, enable_pending=False):
    current = record['plugins'].get(plugin_id, {})
    current['repo'] = repo
    if enable_pending:
        current['enablePending'] = True
    else:
        current.pop('enablePending', None)
    record['plugins'][plugin_id] = current
    save_record(record)
    return record['plugins'][plugin_id]


def apply_plugins(updating):
    try:
        exclusions = load_exclusions()
    except ListError as error:
        say(error)
        return
    try:
        record = load_record()
    except ListError as error:
        say(error)
        return
    document, fresh, notice = resolve_document()
    if notice:
        say(notice)
    if document is None:
        return
    if fresh:
        changes = describe_changes(load_cache(), document)
        if changes:
            say(f'Omarchy plugin list changes: {changes}.')
        try:
            save_json(cache_path(), document)
        except OSError as error:
            say(f'Omarchy plugin list cache was not written: {flat(error)}.')
    arch_skipped = []
    excluded = []
    selected = []
    needs_machine = any('arch' in item for item in document['plugins'])
    machine = machine_arch() if needs_machine else None
    if needs_machine and machine is None:
        say('Omarchy plugin architecture could not be read; architecture-specific plugins were skipped.')
    for item in document['plugins']:
        if item['id'] in exclusions:
            excluded.append(item['id'])
            continue
        allowed = item.get('arch')
        if allowed is not None and machine not in allowed:
            arch_skipped.append(item['id'])
            continue
        selected.append(item)
    parts = []
    if arch_skipped:
        parts.append('arch ' + ', '.join(arch_skipped))
    if excluded:
        parts.append('excluded ' + ', '.join(excluded))
    if parts:
        say('Skipped Omarchy plugins: ' + '; '.join(parts) + '.')

    retired_ids = {item['id'] for item in document['retired']}
    shell_state = {'value': None}

    def shell_up():
        if shell_state['value'] is None:
            shell_state['value'] = shell_is_running()
        return shell_state['value']

    deferred = []
    installed_by_groundwork = set(record['plugins'])
    for item in document['retired']:
        plugin_id = item['id']
        recorded = record['plugins'].get(plugin_id)
        if recorded is None:
            continue
        directory = plugin_path(plugin_id)
        if not path_exists(directory):
            record['plugins'].pop(plugin_id, None)
            save_record(record)
            continue
        if not shell_up():
            deferred.append(plugin_id)
            continue
        outcome = run_omarchy(['plugin', 'remove', plugin_id, '--yes'], 60)
        if command_failed(outcome):
            say(f'Omarchy plugin {plugin_id} remove failed: {failure_text(outcome)}.')
            continue
        record['plugins'].pop(plugin_id, None)
        save_record(record)
    unnoticed = [item['id'] for item in document['retired'] if item['id'] not in installed_by_groundwork]
    if unnoticed:
        say('Omarchy plugins retired in the shared list were not installed by this layer and were left in place: ' + ', '.join(unnoticed) + '.')

    sudo_state = {'ready': None}
    stopped = False
    for item in selected:
        if stopped or item['id'] in retired_ids:
            continue
        plugin_id = item['id']
        directory = plugin_path(plugin_id)
        recorded = record['plugins'].get(plugin_id)
        head_moved = False
        if directory.is_symlink():
            say(f'Omarchy plugin {plugin_id} was not changed; its path is a symbolic link.')
            continue
        if path_exists(directory):
            pending = bool(recorded and recorded.get('enablePending'))
            if updating or pending:
                if not shell_up():
                    deferred.append(plugin_id)
                else:
                    if updating:
                        before = git_head(directory)
                        outcome = run_omarchy(['plugin', 'update', plugin_id, '--yes'], 180)
                        if command_failed(outcome):
                            say(f'Omarchy plugin {plugin_id} update failed: {failure_text(outcome)}.')
                        else:
                            after = git_head(directory)
                            head_moved = before is not None and after is not None and before != after
                    if pending:
                        outcome = run_omarchy(enable_command(item), 60)
                        if command_failed(outcome):
                            say(f'Omarchy plugin {plugin_id} enable failed: {failure_text(outcome)}.')
                        else:
                            recorded.pop('enablePending', None)
                            save_record(record)
            if recorded is not None and not recorded.get('enablePending') and item.get('setup'):
                current_head = git_head(directory)
                if setup_due(item, recorded, head_moved, current_head):
                    perform_setup(item, directory, recorded, sudo_state)
                    save_record(record)
            continue
        if recorded is not None:
            say(f'Omarchy plugin {plugin_id} was removed locally; leaving it uninstalled.')
            continue
        if not shell_up():
            deferred.append(plugin_id)
            continue
        outcome = run_omarchy(['plugin', 'add', item['repo'], '--yes'], 180)
        if command_failed(outcome):
            say(f'Omarchy plugin {plugin_id} add failed: {failure_text(outcome)}.')
            continue
        added = ''
        if not isinstance(outcome, Exception):
            match = re.search(r'^Added (\S+) into ', outcome.stdout or '', re.MULTILINE)
            if match:
                added = match.group(1)
        if added and added != plugin_id:
            say(f'Omarchy plugin {plugin_id} manifest id is {added}; stopped.')
            stopped = True
            continue
        if directory.is_symlink() or not directory.is_dir():
            say(f'Omarchy plugin {plugin_id} add failed: the plugin directory is missing.')
            continue
        actual = manifest_id(directory)
        if actual != plugin_id:
            say(f'Omarchy plugin {plugin_id} manifest id is {actual or "unreadable"}; stopped.')
            stopped = True
            continue
        outcome = run_omarchy(enable_command(item), 60)
        if command_failed(outcome):
            say(f'Omarchy plugin {plugin_id} enable failed: {failure_text(outcome)}.')
            hold_for_enable_retry(record, plugin_id, item['repo'])
            continue
        recorded = remember(record, plugin_id, item['repo'])
        if item.get('setup') and setup_due(item, recorded, False, git_head(directory)):
            perform_setup(item, directory, recorded, sudo_state)
            save_record(record)
    if deferred:
        say('Omarchy shell is not running; deferred plugin changes: ' + ', '.join(deferred) + '.')


def strip_lua_comments(text):
    output = []
    index = 0
    length = len(text)
    while index < length:
        if text.startswith('--[[', index):
            end = text.find(']]', index + 4)
            output.append('\n')
            index = length if end < 0 else end + 2
            continue
        if text.startswith('--', index):
            end = text.find('\n', index)
            index = length if end < 0 else end
            continue
        quote = text[index]
        if quote in ('"', "'"):
            output.append(quote)
            index += 1
            while index < length:
                output.append(text[index])
                if text[index] == '\\':
                    index += 1
                    if index < length:
                        output.append(text[index])
                    index += 1
                    continue
                if text[index] == quote:
                    index += 1
                    break
                index += 1
            continue
        output.append(text[index])
        index += 1
    return ''.join(output)


def read_lua(path):
    try:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
        return path.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return None


def touchpad_natural_scroll(path):
    text = read_lua(path)
    if text is None:
        return None
    cleaned = strip_lua_comments(text)
    found = None
    for match in re.finditer(r'(?<![\w.])touchpad\s*=\s*\{', cleaned):
        depth = 0
        body = None
        for index in range(match.end() - 1, len(cleaned)):
            if cleaned[index] == '{':
                depth += 1
            elif cleaned[index] == '}':
                depth -= 1
                if depth == 0:
                    body = cleaned[match.end():index]
                    break
        if body is None:
            continue
        values = re.findall(r'(?<![\w.])natural_scroll\s*=\s*(true|false)\b', body)
        if values:
            found = values[-1] == 'true'
    return found


def require_order(path):
    text = read_lua(path)
    if text is None:
        return []
    return re.findall(r"""require\s*(?:\(\s*)?['"]hypr\.(mouse|omacontrol)['"]\s*\)?""", strip_lua_comments(text))


def warn_trackpad():
    base = touchpad_natural_scroll(REPO_ROOT / 'packages/hypr/.config/hypr/input.lua')
    if base is None:
        return
    hypr = Path.home() / '.config/hypr'
    differing = []
    for filename, module_name in TRACKPAD_FILES:
        path = hypr / filename
        value = touchpad_natural_scroll(path)
        if value is not None and value != base:
            differing.append((module_name, path))
    if not differing:
        return
    order = require_order(hypr / 'hyprland.lua')
    ranked = [(order.index(module_name), path) for module_name, path in differing if module_name in order]
    winner = sorted(ranked)[-1][1] if ranked else differing[-1][1]
    warn(f'Warning: {winner} sets touchpad.natural_scroll differently from the stowed input.lua and wins.')


def main(argv):
    updating = parse_args(argv)
    try:
        apply_plugins(updating)
    except Exception as error:
        warn(f'Omarchy plugins could not be applied: {flat(error)}.')
    finally:
        try:
            warn_trackpad()
        except Exception as error:
            warn(f'Omarchy trackpad check failed: {flat(error)}.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except BrokenPipeError:
        sys.exit(0)
