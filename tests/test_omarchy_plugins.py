"""Apply the shared Omarchy plugin list against fixture commands."""
import http.client
import importlib.util
import json
import os
from pathlib import Path
import pty
import select
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'scripts/omarchy-plugins.py'
spec = importlib.util.spec_from_file_location('omarchy_plugins_under_test', SCRIPT)
plugins = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugins)

STUB = r'''#!/usr/bin/env python3
import json, os, pathlib, shutil, subprocess, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
fixture = pathlib.Path(os.environ['PLUGIN_FIXTURE'])
with (fixture / 'calls.jsonl').open('a') as log:
    log.write(json.dumps([name, *args]) + '\n')

def control():
    path = fixture / 'control.json'
    return json.loads(path.read_text()) if path.is_file() else {}

def expected_id(url):
    parts = url.rstrip('/').split('/')
    repo = parts[-1][:-4] if parts[-1].endswith('.git') else parts[-1]
    return parts[-2] + '.' + repo

def plugins_root():
    return pathlib.Path.home() / '.config/omarchy/plugins'

def git(directory, *git_args):
    env = dict(os.environ)
    env.pop('GIT_DIR', None)
    env.pop('GIT_WORK_TREE', None)
    result = subprocess.run(['git', '-C', str(directory), *git_args], capture_output=True, text=True, env=env)
    if result.returncode != 0:
        sys.stderr.write(result.stderr or result.stdout)
        sys.exit(result.returncode)

if name == 'uname':
    status = int(os.environ.get('FIXTURE_UNAME_STATUS', '0'))
    if status:
        sys.exit(status)
    print(os.environ.get('FIXTURE_ARCH', 'aarch64'))
    sys.exit(0)
if name == 'omarchy-shell':
    if args == ['shell', 'ping'] and os.environ.get('FIXTURE_SHELL') == '1':
        print('ok')
        sys.exit(0)
    if args == ['shell', 'ping']:
        print(os.environ.get('FIXTURE_SHELL_OUTPUT', 'down'))
        sys.exit(int(os.environ.get('FIXTURE_SHELL_STATUS', '1')))
    sys.exit(1)
if name == 'sudo':
    if args[:2] == ['-n', 'true']:
        sys.exit(0 if os.environ.get('FIXTURE_SUDO_CACHED') == '1' else 1)
    if args == ['-v']:
        sys.exit(0)
    if args[:1] == ['--'] and len(args) >= 2:
        os.execv(args[1], args[1:])
    sys.stderr.write('unexpected sudo\n')
    sys.exit(2)
if name != 'omarchy' or args[:1] != ['plugin']:
    sys.stderr.write('unexpected command\n')
    sys.exit(2)

settings = control()
action = args[1]

def shell_states():
    path = fixture / 'shell-states.json'
    if path.is_file():
        return json.loads(path.read_text())
    return {key: list(value) for key, value in settings.get('listed', {}).items()}

def save_states(states):
    (fixture / 'shell-states.json').write_text(json.dumps(states) + '\n')

if action == 'list' and args[2:] == ['--json']:
    if settings.get('fail_list'):
        sys.exit(1)
    states = shell_states()
    if plugins_root().is_dir():
        for directory in plugins_root().iterdir():
            states.setdefault(directory.name, [False, False])
    print(json.dumps([{'id': key, 'enabled': value[0], 'firstParty': value[1]} for key, value in sorted(states.items())]))
    sys.exit(0)
if action == 'disable' and len(args) == 3:
    plugin_id = args[2]
    if plugin_id in settings.get('fail_disable', []):
        sys.stderr.write('fixture disable failed\n')
        sys.exit(1)
    states = shell_states()
    if plugin_id not in states and not (plugins_root() / plugin_id).is_dir():
        sys.stderr.write(f'unknown plugin {plugin_id}\n')
        sys.exit(1)
    states.setdefault(plugin_id, [False, False])[0] = False
    save_states(states)
    print(f'Disabled {plugin_id}')
    sys.exit(0)
if action == 'add' and args[3:] == ['--yes']:
    repo = args[2]
    expected = expected_id(repo)
    if expected in settings.get('fail_add', []):
        sys.stderr.write('fixture add failed\n')
        sys.exit(1)
    manifest = settings.get('manifest_ids', {}).get(expected, expected)
    body = settings.get('manifest_body_ids', {}).get(expected, manifest)
    directory = plugins_root() / manifest
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'manifest.json').write_text(json.dumps({'id': body}) + '\n')
    source = os.environ.get('GROUNDWORK_PLUGIN_LIST', '')
    entry = {}
    list_path = pathlib.Path(source)
    if list_path.is_file():
        try:
            listed = json.loads(list_path.read_text())
        except json.JSONDecodeError:
            listed = {}
        for item in listed.get('plugins') or []:
            if item.get('repo') == repo:
                entry = item
    setup = entry.get('setup') or {}
    command = setup.get('command')
    if command:
        target = directory.joinpath(*command.split('/'))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('#!/bin/sh\nprintf x >> "$PLUGIN_FIXTURE/setup-ran"\n')
        target.chmod(0o755)
    if os.environ.get('FIXTURE_GIT') == '1':
        git(directory, 'init', '-b', 'main')
        git(directory, 'add', 'manifest.json')
        git(directory, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'add')
    if expected in settings.get('add_then_fail', []):
        sys.stderr.write('fixture add failed\n')
        sys.exit(1)
    print(f'Added {manifest} into {directory}')
    sys.exit(0)
if action == 'enable':
    plugin_id = args[2]
    if plugin_id in settings.get('fail_enable', []):
        sys.stderr.write('fixture enable failed\n')
        sys.exit(1)
    states = shell_states()
    states.setdefault(plugin_id, [False, False])[0] = True
    save_states(states)
    print(f'Enabled {plugin_id}')
    sys.exit(0)
if action == 'remove' and args[3:] == ['--yes']:
    plugin_id = args[2]
    if plugin_id in settings.get('fail_remove', []):
        sys.stderr.write('fixture remove failed\n')
        sys.exit(1)
    shutil.rmtree(plugins_root() / plugin_id, ignore_errors=True)
    if plugin_id in settings.get('remove_then_fail', []):
        sys.stderr.write('fixture remove failed\n')
        sys.exit(1)
    sys.exit(0)
if action == 'update' and args[3:] == ['--yes']:
    plugin_id = args[2]
    if plugin_id in settings.get('fail_update', []):
        sys.stderr.write('fixture update failed\n')
        sys.exit(1)
    directory = plugins_root() / plugin_id
    if os.environ.get('FIXTURE_ADVANCE_HEAD') == '1' or plugin_id in settings.get('update_then_fail', []):
        marker = directory / 'advanced.txt'
        previous = marker.read_text() if marker.is_file() else ''
        marker.write_text(previous + 'advanced\n')
        git(directory, 'add', 'advanced.txt')
        git(directory, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'advance')
    if plugin_id in settings.get('update_then_fail', []):
        sys.stderr.write('fixture update failed\n')
        sys.exit(1)
    print(f'Updated {plugin_id}')
    sys.exit(0)
sys.stderr.write('unexpected plugin command\n')
sys.exit(2)
'''


def plugin(plugin_id='example.widget', **extra):
    name = plugin_id.split('.', 1)[1]
    entry = {
        'id': plugin_id,
        'repo': f'https://github.com/example/{name}.git',
        'reason': 'Fixture',
    }
    entry.update(extra)
    return entry


def refused_https_url():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return f'https://127.0.0.1:{port}/plugins.json'


class PluginApplyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='omarchy-plugins-')
        self.addCleanup(temporary.cleanup)
        self.fixture = Path(temporary.name).resolve()
        self.home = self.fixture / 'home'
        self.home.mkdir()
        self.state = self.home / '.local/state/groundwork'
        self.bin = self.fixture / 'bin'
        self.bin.mkdir()
        stub = self.bin / 'stub'
        stub.write_text(STUB)
        stub.chmod(0o755)
        for name in ('omarchy', 'omarchy-shell', 'uname', 'sudo'):
            (self.bin / name).symlink_to(stub)
        self.list_path = self.fixture / 'plugins.json'
        self.write_list([])
        self.env = os.environ.copy()
        for key in ('GIT_DIR', 'GIT_WORK_TREE', 'PYTHONPATH', 'GROUNDWORK_PLUGIN_LIST'):
            self.env.pop(key, None)
        self.env.update({
            'HOME': str(self.home),
            'XDG_CONFIG_HOME': str(self.home / '.config'),
            'XDG_STATE_HOME': str(self.home / '.local/state'),
            'PATH': f'{self.bin}:/usr/bin:/bin',
            'PLUGIN_FIXTURE': str(self.fixture),
            'GROUNDWORK_PLUGIN_LIST': str(self.list_path),
            'FIXTURE_SHELL': '1',
            'FIXTURE_ARCH': 'aarch64',
            'GIT_CONFIG_GLOBAL': os.devnull,
            'GIT_CONFIG_SYSTEM': os.devnull,
            'GIT_AUTHOR_NAME': 'Fixture',
            'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
            'GIT_COMMITTER_NAME': 'Fixture',
            'GIT_COMMITTER_EMAIL': 'fixture@example.invalid',
            'GIT_TERMINAL_PROMPT': '0',
        })

    def write_list(self, entries, retired=None, version=1):
        self.list_path.write_text(json.dumps({
            'schemaVersion': version,
            'plugins': entries,
            'retired': [] if retired is None else retired,
        }) + '\n')

    def write_cache(self, entries, retired=None):
        path = self.cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            'schemaVersion': 1,
            'plugins': entries,
            'retired': [] if retired is None else retired,
        }) + '\n')

    def write_record(self, recorded):
        path = self.record_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'schemaVersion': 1, 'plugins': recorded}, sort_keys=True) + '\n')

    def seed_directory(self, plugin_id, manifest_id=None):
        directory = self.plugin_dir(plugin_id)
        directory.mkdir(parents=True)
        (directory / 'manifest.json').write_text(json.dumps({'id': manifest_id or plugin_id}) + '\n')
        return directory

    def set_control(self, **kwargs):
        (self.fixture / 'control.json').write_text(json.dumps(kwargs) + '\n')

    def cache_path(self):
        return self.state / 'omarchy-plugins.json'

    def record_path(self):
        return self.state / 'omarchy-plugins-installed.json'

    def plugin_dir(self, plugin_id):
        return self.home / '.config/omarchy/plugins' / plugin_id

    def record(self):
        return json.loads(self.record_path().read_text())

    def calls(self):
        log = self.fixture / 'calls.jsonl'
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def omarchy_calls(self):
        return [call[1:] for call in self.calls() if call[0] == 'omarchy']

    def clear_calls(self):
        log = self.fixture / 'calls.jsonl'
        if log.exists():
            log.unlink()

    def invoke(self, *args, extra=None, timeout=30):
        env = dict(self.env)
        if extra:
            env.update(extra)
        return subprocess.run([sys.executable, '-B', str(SCRIPT), *args], env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=timeout)

    def run_tty(self, *args, extra=None, timeout=30):
        env = dict(self.env)
        if extra:
            env.update(extra)
        master, slave = pty.openpty()
        process = subprocess.Popen([sys.executable, '-B', str(SCRIPT), *args], env=env,
                                   stdin=slave, stdout=slave, stderr=slave, close_fds=True)
        os.close(slave)
        chunks = []
        deadline = time.monotonic() + timeout
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    process.kill()
                    raise AssertionError('plugin helper timed out')
                ready, _, _ = select.select([master], [], [], min(0.2, remaining))
                if ready:
                    try:
                        chunk = os.read(master, 4096)
                    except OSError:
                        chunk = b''
                    if chunk:
                        chunks.append(chunk)
                        continue
                if process.poll() is not None:
                    break
            process.wait(timeout=5)
        finally:
            os.close(master)
        text = b''.join(chunks).decode('utf-8', 'replace')
        return subprocess.CompletedProcess(args, process.returncode, text, '')

    def use_repo_exclusions(self, text):
        path = REPO / 'omarchy-plugins.exclude.json'
        original = path.read_bytes()
        self.addCleanup(path.write_bytes, original)
        path.write_text(text)

    def builtin(self, plugin_id='omarchy.agents', **extra):
        entry = {'id': plugin_id, 'builtIn': True, 'reason': 'Fixture'}
        entry.update(extra)
        return entry

    def test_version_2_list_with_only_cloned_plugins_matches_version_1(self):
        self.write_list([plugin(section='right')], version=2)
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.omarchy_calls(), [
            ['plugin', 'add', 'https://github.com/example/widget.git', '--yes'],
            ['plugin', 'enable', 'example.widget', '--section', 'right'],
        ])
        self.assertEqual(json.loads(self.cache_path().read_text())['schemaVersion'], 2)

    def test_version_2_rules_reject_the_list(self):
        samples = [
            json.dumps({'schemaVersion': 1, 'plugins': [self.builtin()], 'retired': []}),
            json.dumps({'schemaVersion': 2, 'plugins': [self.builtin(repo='https://github.com/example/agents.git')], 'retired': []}),
            json.dumps({'schemaVersion': 2, 'plugins': [self.builtin(setup={'command': 'setup', 'needsSudo': False})], 'retired': []}),
            json.dumps({'schemaVersion': 2, 'plugins': [self.builtin(builtIn='yes')], 'retired': []}),
            json.dumps({'schemaVersion': 2, 'plugins': [self.builtin('omarchy..agents')], 'retired': []}),
            json.dumps({'schemaVersion': 2, 'plugins': [plugin('omarchy.widget')], 'retired': []}),
            json.dumps({'schemaVersion': 2, 'plugins': [plugin(replaces=[])], 'retired': []}),
            json.dumps({'schemaVersion': 2, 'plugins': [plugin(replaces=['example.widget'])], 'retired': []}),
            json.dumps({'schemaVersion': 3, 'plugins': [], 'retired': []}),
        ]
        for sample in samples:
            self.list_path.write_text(sample)
            result = self.invoke()
            self.assertEqual(result.returncode, 0, sample + result.stdout + result.stderr)
            self.assertIn('invalid', result.stdout, sample)
            self.assertFalse(self.omarchy_calls(), sample)
            self.clear_calls()

    def test_enabled_builtin_is_recorded_once_and_left_in_place(self):
        self.set_control(listed={'omarchy.agents': [True, True]})
        self.write_list([self.builtin(section='right')], version=2)
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.omarchy_calls(), [['plugin', 'list', '--json']])
        self.assertEqual(self.record()['plugins']['omarchy.agents'], {'builtIn': True})
        self.clear_calls()
        again = self.invoke('--update')
        self.assertEqual(again.stdout + again.stderr, '')
        self.assertFalse(self.omarchy_calls())

    def test_disabled_builtin_is_enabled_once_then_left_off(self):
        self.set_control(listed={'omarchy.agents': [False, True]})
        self.write_list([self.builtin(section='left')], version=2)
        self.invoke()
        self.assertEqual(self.omarchy_calls(), [
            ['plugin', 'list', '--json'],
            ['plugin', 'enable', 'omarchy.agents', '--section', 'left'],
        ])
        self.assertEqual(self.record()['plugins']['omarchy.agents'], {'builtIn': True, 'enabledHere': True})
        # The user turns it off again; later runs keep it off.
        self.set_control(listed={'omarchy.agents': [False, True]})
        (self.fixture / 'shell-states.json').unlink()
        self.clear_calls()
        self.invoke('--update')
        self.assertFalse(self.omarchy_calls())

    def test_builtin_missing_or_third_party_is_skipped_unrecorded(self):
        for listed in ({}, {'omarchy.agents': [False, False]}):
            self.set_control(listed=listed)
            self.write_list([self.builtin()], version=2)
            result = self.invoke()
            self.assertIn('Omarchy plugin omarchy.agents is not built into this Omarchy; skipped.', result.stdout)
            self.assertNotIn(['plugin', 'enable', 'omarchy.agents'], self.omarchy_calls())
            self.assertFalse(self.record_path().exists())
            self.clear_calls()

    def test_builtin_waits_for_a_shell(self):
        self.write_list([self.builtin()], version=2)
        result = self.invoke(extra={'FIXTURE_SHELL': '0'})
        self.assertIn('deferred plugin changes: omarchy.agents', result.stdout)
        self.assertFalse(self.omarchy_calls())

    def test_unlisted_shell_skips_the_builtin_without_recording(self):
        self.set_control(fail_list=True)
        self.write_list([self.builtin()], version=2)
        result = self.invoke()
        self.assertIn('the shell did not list its plugins', result.stdout)
        self.assertFalse(self.record_path().exists())

    def test_builtin_enable_failure_is_retried_on_the_next_run(self):
        self.set_control(listed={'omarchy.agents': [False, True]}, fail_enable=['omarchy.agents'])
        self.write_list([self.builtin()], version=2)
        result = self.invoke()
        self.assertIn('Omarchy plugin omarchy.agents enable failed', result.stdout)
        self.assertFalse(self.record_path().exists())

    def test_replacement_disables_the_original_once(self):
        self.set_control(listed={'omarchy.workspaces': [True, True]})
        self.write_list([plugin('example.spaces', replaces=['omarchy.workspaces'])], version=2)
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Omarchy plugin omarchy.workspaces was disabled; example.spaces replaces it.', result.stdout)
        self.assertEqual(self.omarchy_calls()[-1], ['plugin', 'disable', 'omarchy.workspaces'])
        self.assertEqual(self.record()['plugins']['example.spaces']['replaced'], ['omarchy.workspaces'])
        # The user turns Workspaces back on; the swap does not repeat.
        states = json.loads((self.fixture / 'shell-states.json').read_text())
        states['omarchy.workspaces'][0] = True
        (self.fixture / 'shell-states.json').write_text(json.dumps(states))
        self.clear_calls()
        again = self.invoke('--update')
        self.assertNotIn('disable', str(self.omarchy_calls()))
        self.assertNotIn('disabled', again.stdout)

    def test_replacement_added_later_runs_for_a_recorded_plugin(self):
        self.seed_directory('example.spaces')
        self.write_record({'example.spaces': {'repo': 'https://github.com/example/spaces.git'}})
        self.set_control(listed={'example.spaces': [True, False], 'omarchy.workspaces': [True, True]})
        self.write_list([plugin('example.spaces', replaces=['omarchy.workspaces'])], version=2)
        self.invoke()
        self.assertEqual(self.omarchy_calls(), [['plugin', 'list', '--json'], ['plugin', 'disable', 'omarchy.workspaces']])
        self.assertEqual(self.record()['plugins']['example.spaces']['replaced'], ['omarchy.workspaces'])

    def test_replacement_the_user_turned_off_keeps_the_original(self):
        self.seed_directory('example.spaces')
        self.write_record({'example.spaces': {'repo': 'https://github.com/example/spaces.git'}})
        self.set_control(listed={'example.spaces': [False, False], 'omarchy.workspaces': [True, True]})
        self.write_list([plugin('example.spaces', replaces=['omarchy.workspaces'])], version=2)
        self.invoke()
        self.assertNotIn(['plugin', 'disable', 'omarchy.workspaces'], self.omarchy_calls())
        self.assertNotIn('replaced', self.record()['plugins']['example.spaces'])

    def test_failed_disable_is_retried_on_the_next_run(self):
        self.set_control(listed={'omarchy.workspaces': [True, True]}, fail_disable=['omarchy.workspaces'])
        self.write_list([plugin('example.spaces', replaces=['omarchy.workspaces'])], version=2)
        result = self.invoke()
        self.assertIn('Omarchy plugin omarchy.workspaces disable failed', result.stdout)
        self.assertNotIn('replaced', self.record()['plugins']['example.spaces'])

    def retire(self, plugin_id='omarchy.agents'):
        self.write_list([], retired=[{'id': plugin_id, 'reason': 'Fixture'}], version=2)

    def test_retired_builtin_enabled_here_is_disabled(self):
        self.write_record({'omarchy.agents': {'builtIn': True, 'enabledHere': True}})
        self.set_control(listed={'omarchy.agents': [True, True]})
        self.retire()
        self.invoke()
        self.assertEqual(self.omarchy_calls(), [['plugin', 'list', '--json'], ['plugin', 'disable', 'omarchy.agents']])
        self.assertNotIn('omarchy.agents', self.record()['plugins'])

    def test_retired_builtin_that_was_already_on_is_left_enabled(self):
        self.write_record({'omarchy.agents': {'builtIn': True}})
        self.set_control(listed={'omarchy.agents': [True, True]})
        self.retire()
        self.invoke()
        self.assertFalse(self.omarchy_calls())
        self.assertNotIn('omarchy.agents', self.record()['plugins'])

    def test_retired_builtin_unlisted_or_off_is_just_dropped(self):
        for listed in ({}, {'omarchy.agents': [False, True]}):
            self.write_record({'omarchy.agents': {'builtIn': True, 'enabledHere': True}})
            self.set_control(listed=listed)
            self.retire()
            result = self.invoke()
            self.assertNotIn('failed', result.stdout)
            self.assertNotIn(['plugin', 'disable', 'omarchy.agents'], self.omarchy_calls())
            self.assertNotIn('omarchy.agents', self.record()['plugins'])
            self.clear_calls()

    def test_retired_builtin_is_deferred_when_the_shell_cannot_list(self):
        self.write_record({'omarchy.agents': {'builtIn': True, 'enabledHere': True}})
        self.set_control(fail_list=True)
        self.retire()
        result = self.invoke()
        self.assertIn('deferred plugin changes: omarchy.agents', result.stdout)
        self.assertIn('omarchy.agents', self.record()['plugins'])

    def test_enabled_here_must_be_a_boolean(self):
        self.write_record({'omarchy.agents': {'builtIn': True, 'enabledHere': 'yes'}})
        self.write_list([self.builtin()], version=2)
        result = self.invoke()
        self.assertIn('install record is invalid', result.stdout)

    def test_retired_replacement_re_enables_what_it_turned_off(self):
        self.seed_directory('example.spaces')
        self.write_record({'example.spaces': {'repo': 'https://github.com/example/spaces.git', 'replaced': ['omarchy.workspaces', 'omarchy.gone']}})
        self.set_control(listed={'example.spaces': [True, False], 'omarchy.workspaces': [False, True]})
        self.retire('example.spaces')
        self.invoke()
        calls = self.omarchy_calls()
        self.assertIn(['plugin', 'remove', 'example.spaces', '--yes'], calls)
        self.assertIn(['plugin', 'enable', 'omarchy.workspaces'], calls)
        self.assertNotIn(['plugin', 'enable', 'omarchy.gone'], calls)
        self.assertFalse(self.record()['plugins'])

    def test_retired_replacement_leaves_an_enabled_original_alone(self):
        self.seed_directory('example.spaces')
        self.write_record({'example.spaces': {'repo': 'https://github.com/example/spaces.git', 'replaced': ['omarchy.workspaces']}})
        self.set_control(listed={'example.spaces': [True, False], 'omarchy.workspaces': [True, True]})
        self.retire('example.spaces')
        self.invoke()
        self.assertNotIn(['plugin', 'enable', 'omarchy.workspaces'], self.omarchy_calls())
        self.assertFalse(self.record()['plugins'])

    def test_retired_replacement_defers_when_the_shell_cannot_list(self):
        self.seed_directory('example.spaces')
        self.write_record({'example.spaces': {'repo': 'https://github.com/example/spaces.git', 'replaced': ['omarchy.workspaces']}})
        self.set_control(fail_list=True)
        self.retire('example.spaces')
        result = self.invoke()
        self.assertIn('deferred plugin changes: example.spaces', result.stdout)
        self.assertNotIn(['plugin', 'remove', 'example.spaces', '--yes'], self.omarchy_calls())
        self.assertIn('example.spaces', self.record()['plugins'])

    def test_replacement_never_disables_an_excluded_id(self):
        exclusions = self.home / '.config/groundwork/omarchy-plugins.exclude.json'
        exclusions.parent.mkdir(parents=True)
        exclusions.write_text(json.dumps({'schemaVersion': 1, 'exclusions': [{'id': 'omarchy.workspaces', 'reason': 'Fixture'}]}))
        self.set_control(listed={'omarchy.workspaces': [True, True]})
        self.write_list([plugin('example.spaces', replaces=['omarchy.workspaces'])], version=2)
        self.invoke()
        self.assertNotIn(['plugin', 'disable', 'omarchy.workspaces'], self.omarchy_calls())

    def test_builtin_record_with_a_repo_is_invalid(self):
        self.write_record({'omarchy.agents': {'builtIn': True, 'repo': 'https://github.com/example/agents.git'}})
        self.write_list([self.builtin()], version=2)
        result = self.invoke()
        self.assertIn('install record is invalid', result.stdout)
        self.assertFalse(self.omarchy_calls())

    def test_exclusion_may_name_a_builtin(self):
        exclusions = self.home / '.config/groundwork/omarchy-plugins.exclude.json'
        exclusions.parent.mkdir(parents=True)
        exclusions.write_text(json.dumps({'schemaVersion': 1, 'exclusions': [{'id': 'omarchy.agents', 'reason': 'Fixture'}]}))
        self.write_list([self.builtin()], version=2)
        result = self.invoke()
        self.assertIn('Skipped Omarchy plugins: excluded omarchy.agents.', result.stdout)
        self.assertFalse(self.omarchy_calls())

    def test_shared_core_list_shape_is_accepted(self):
        text = json.dumps({
            'schemaVersion': 2,
            'plugins': [
                {'id': 'omarchy.agents', 'builtIn': True, 'section': 'right', 'reason': 'Fixture'},
                {'id': 'example.spaces', 'repo': 'https://github.com/example/spaces.git',
                 'replaces': ['omarchy.workspaces'], 'reason': 'Fixture'},
                {'id': 'example.setup', 'repo': 'https://github.com/example/setup.git', 'arch': ['aarch64'],
                 'setup': {'command': 'scripts/setup', 'needsSudo': False}, 'reason': 'Fixture'},
            ],
            'retired': [],
        })
        document = plugins.parse_list(text)
        self.assertIn(document['schemaVersion'], plugins.SCHEMA_VERSIONS)
        self.assertEqual([item['id'] for item in document['plugins']], ['omarchy.agents', 'example.spaces', 'example.setup'])

    def test_fresh_install_is_idempotent(self):
        self.write_list([plugin(section='right', arch=['aarch64'])])
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn('Omarchy plugin list changes: add example.widget.', first.stdout)
        self.assertEqual(self.omarchy_calls(), [
            ['plugin', 'add', 'https://github.com/example/widget.git', '--yes'],
            ['plugin', 'enable', 'example.widget', '--section', 'right'],
        ])
        self.assertIn(['omarchy-shell', 'shell', 'ping'], self.calls())
        self.assertEqual(self.record()['plugins']['example.widget']['repo'], 'https://github.com/example/widget.git')
        self.assertNotIn('enablePending', self.record()['plugins']['example.widget'])
        self.assertEqual(self.cache_path().stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.record_path().stat().st_mode & 0o777, 0o600)
        cached = json.loads(self.cache_path().read_text())
        self.assertEqual(cached['plugins'][0]['section'], 'right')
        saved = self.record_path().read_bytes()
        self.clear_calls()
        second = self.invoke()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(second.stdout, '')
        self.assertEqual(second.stderr, '')
        self.assertFalse(self.omarchy_calls())
        self.assertNotIn(['omarchy-shell', 'shell', 'ping'], self.calls())
        self.assertEqual(self.record_path().read_bytes(), saved)

    def test_unknown_list_fields_are_not_cached(self):
        entry = plugin()
        entry['note'] = 'ignore me'
        self.write_list([entry])
        self.invoke()
        self.assertNotIn('note', json.loads(self.cache_path().read_text())['plugins'][0])

    def test_already_installed_plugin_is_left_alone(self):
        self.seed_directory('example.widget')
        shell = self.home / '.config/omarchy/shell.json'
        shell.parent.mkdir(parents=True, exist_ok=True)
        shell.write_text('{"plugins":["example.widget"]}\n')
        self.write_list([plugin(section='left')])
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.omarchy_calls())
        self.assertFalse(self.record_path().exists())

    def test_user_disabled_plugin_is_not_reenabled(self):
        self.seed_directory('example.widget')
        self.write_record({'example.widget': {'repo': 'https://github.com/example/widget.git'}})
        shell = self.home / '.config/omarchy/shell.json'
        shell.parent.mkdir(parents=True, exist_ok=True)
        shell.write_text('{"plugins":[]}\n')
        self.write_list([plugin(section='right')])
        result = self.invoke('--update')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.omarchy_calls(), [['plugin', 'update', 'example.widget', '--yes']])
        self.assertNotIn('enable', str(self.omarchy_calls()))

    def test_removed_plugin_stays_uninstalled(self):
        self.write_record({'example.widget': {'repo': 'https://github.com/example/widget.git'}})
        self.write_list([plugin()])
        result = self.invoke()
        self.assertIn('Omarchy plugin example.widget was removed locally; leaving it uninstalled.', result.stdout)
        self.assertFalse(self.omarchy_calls())
        self.assertIn('example.widget', self.record()['plugins'])

    def test_no_session_defers_install_until_a_later_run(self):
        self.write_list([plugin()])
        first = self.invoke(extra={'FIXTURE_SHELL': '0'})
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn('Omarchy shell is not running; deferred plugin changes: example.widget.', first.stdout)
        self.assertFalse(self.plugin_dir('example.widget').exists())
        self.assertFalse(self.record_path().exists())
        self.clear_calls()
        second = self.invoke(extra={'FIXTURE_SHELL': '0'})
        self.assertIn('deferred plugin changes: example.widget.', second.stdout)
        self.assertFalse(self.plugin_dir('example.widget').exists())
        self.assertFalse(self.omarchy_calls())

    def test_shell_output_other_than_ok_is_no_session(self):
        self.write_list([plugin()])
        result = self.invoke(extra={'FIXTURE_SHELL': '0', 'FIXTURE_SHELL_STATUS': '0', 'FIXTURE_SHELL_OUTPUT': 'OK'})
        self.assertIn('deferred plugin changes: example.widget.', result.stdout)
        self.assertFalse(self.omarchy_calls())

    def test_arch_skip_leaves_other_plugins_installable(self):
        self.write_list([
            plugin('example.skip', arch=['x86_64']),
            plugin('example.keep', arch=['aarch64', 'x86_64']),
        ])
        result = self.invoke()
        self.assertIn('Skipped Omarchy plugins: arch example.skip.', result.stdout)
        self.assertEqual([call[2] for call in self.omarchy_calls() if call[1] == 'add'],
                         ['https://github.com/example/keep.git'])
        self.assertFalse(self.plugin_dir('example.skip').exists())
        self.assertTrue(self.plugin_dir('example.keep').is_dir())

    def test_unreadable_architecture_skips_only_arch_specific_plugins(self):
        self.write_list([plugin('example.skip', arch=['aarch64']), plugin('example.keep')])
        result = self.invoke(extra={'FIXTURE_UNAME_STATUS': '1'})
        self.assertIn('architecture could not be read', result.stdout)
        self.assertIn('arch example.skip', result.stdout)
        self.assertTrue(self.plugin_dir('example.keep').is_dir())
        self.assertFalse(self.plugin_dir('example.skip').exists())

    def test_repo_exclude_skips_only_the_listed_id(self):
        self.use_repo_exclusions(json.dumps({
            'schemaVersion': 1,
            'exclusions': [{'id': 'example.widget', 'reason': 'Skipped by this layer'}],
        }) + '\n')
        self.write_list([plugin(), plugin('example.keep')])
        result = self.invoke()
        self.assertIn('excluded example.widget', result.stdout)
        self.assertFalse(self.plugin_dir('example.widget').exists())
        self.assertTrue(self.plugin_dir('example.keep').is_dir())

    def test_machine_exclude_skips_the_id(self):
        path = self.home / '.config/groundwork/omarchy-plugins.exclude.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps([{'id': 'example.widget', 'reason': 'Skipped on this machine'}]) + '\n')
        self.write_list([plugin()])
        result = self.invoke()
        self.assertIn('excluded example.widget', result.stdout)
        self.assertFalse(self.omarchy_calls())

    def test_invalid_exclude_file_skips_plugin_changes(self):
        path = self.home / '.config/groundwork/omarchy-plugins.exclude.json'
        path.parent.mkdir(parents=True)
        path.write_text('{')
        self.write_list([plugin()])
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('plugin changes were skipped', result.stdout)
        self.assertFalse(self.omarchy_calls())
        self.assertFalse(self.plugin_dir('example.widget').exists())

    def test_repo_exclude_file_starts_empty(self):
        data = json.loads((REPO / 'omarchy-plugins.exclude.json').read_text())
        self.assertIs(type(data['schemaVersion']), int)
        self.assertEqual(data['schemaVersion'], 1)
        self.assertEqual(data['exclusions'], [])

    def test_retired_recorded_plugin_is_removed_once(self):
        self.seed_directory('example.widget')
        self.write_record({'example.widget': {'repo': 'https://github.com/example/widget.git'}})
        self.write_list([], retired=[{'id': 'example.widget', 'reason': 'Retired fixture'}])
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.omarchy_calls(), [['plugin', 'remove', 'example.widget', '--yes']])
        self.assertNotIn('left in place', result.stdout)
        self.assertEqual(self.record()['plugins'], {})
        self.assertFalse(self.plugin_dir('example.widget').exists())

    def test_retired_plugin_not_recorded_is_left_in_place(self):
        self.seed_directory('example.widget')
        self.write_list([], retired=[{'id': 'example.widget', 'reason': 'Retired fixture'}])
        result = self.invoke()
        self.assertIn('were not installed by this layer and were left in place: example.widget.', result.stdout)
        self.assertFalse(self.omarchy_calls())
        self.assertTrue(self.plugin_dir('example.widget').is_dir())

    def test_retired_notice_prints_once_and_only_when_the_directory_exists(self):
        self.seed_directory('example.widget')
        self.write_list([], retired=[
            {'id': 'example.widget', 'reason': 'Retired fixture'},
            {'id': 'example.absent', 'reason': 'Not installed here'},
        ])
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        notice = [line for line in first.stdout.splitlines() if 'left in place' in line]
        self.assertEqual(notice, [
            'Omarchy plugins retired in the shared list were not installed by this layer and were left in place: example.widget.',
        ])
        self.assertNotIn('example.absent', notice[0])
        self.assertIn('example.widget', self.record()['noticedRetired'])
        self.assertFalse(self.omarchy_calls())
        self.clear_calls()
        second = self.invoke()
        self.assertNotIn('left in place', second.stdout)
        self.assertFalse(self.omarchy_calls())
        self.assertTrue(self.plugin_dir('example.widget').is_dir())

    def test_retired_removal_ignores_an_exclusion(self):
        path = self.home / '.config/groundwork/omarchy-plugins.exclude.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps([{'id': 'example.widget', 'reason': 'Excluded and retired'}]) + '\n')
        self.seed_directory('example.widget')
        self.write_record({'example.widget': {'repo': 'https://github.com/example/widget.git'}})
        self.write_list([], retired=[{'id': 'example.widget', 'reason': 'Retired fixture'}])
        self.invoke()
        self.assertEqual(self.omarchy_calls(), [['plugin', 'remove', 'example.widget', '--yes']])

    def test_retired_removal_waits_for_a_shell(self):
        self.seed_directory('example.widget')
        self.write_record({'example.widget': {'repo': 'https://github.com/example/widget.git'}})
        self.write_list([], retired=[{'id': 'example.widget', 'reason': 'Retired fixture'}])
        result = self.invoke(extra={'FIXTURE_SHELL': '0'})
        self.assertIn('deferred plugin changes: example.widget.', result.stdout)
        self.assertFalse(self.omarchy_calls())
        self.assertIn('example.widget', self.record()['plugins'])

    def test_invalid_list_changes_nothing(self):
        self.list_path.write_text('{')
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('invalid', result.stdout)
        self.assertIn('nothing changed', result.stdout)
        self.assertFalse(self.omarchy_calls())
        self.assertFalse(self.cache_path().exists())

    def test_bad_schema_ids_and_repos_reject_the_list(self):
        samples = [
            '{"schemaVersion": "1", "plugins": [], "retired": []}',
            '{"schemaVersion": true, "plugins": [], "retired": []}',
            json.dumps({'schemaVersion': 1, 'plugins': [plugin('omarchy.widget')], 'retired': []}),
            json.dumps({'schemaVersion': 1, 'plugins': [plugin('example..widget')], 'retired': []}),
            json.dumps({'schemaVersion': 1, 'plugins': [plugin(repo='https://gitlab.com/example/widget.git')], 'retired': []}),
            json.dumps({'schemaVersion': 1, 'plugins': [plugin(setup={'command': '../escape', 'needsSudo': False})], 'retired': []}),
            '{"schemaVersion": 1, "plugins": []}',
        ]
        for sample in samples:
            self.list_path.write_text(sample)
            result = self.invoke()
            self.assertEqual(result.returncode, 0, sample + result.stdout + result.stderr)
            self.assertIn('invalid', result.stdout, sample)
            self.assertFalse(self.omarchy_calls(), sample)
            self.clear_calls()

    def test_cache_is_used_when_the_list_file_is_missing(self):
        self.write_cache([plugin(section='left')])
        result = self.invoke(extra={'GROUNDWORK_PLUGIN_LIST': str(self.fixture / 'missing.json')})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('using the saved copy', result.stdout)
        self.assertIn(['plugin', 'enable', 'example.widget', '--section', 'left'], self.omarchy_calls())

    def test_cache_is_used_when_the_download_fails(self):
        self.write_cache([plugin()])
        result = self.invoke(extra={'GROUNDWORK_PLUGIN_LIST': refused_https_url()}, timeout=25)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('unavailable; using the saved copy', result.stdout)
        self.assertTrue(any(call[:2] == ['plugin', 'add'] for call in self.omarchy_calls()))

    def test_invalid_list_does_not_replace_a_saved_copy(self):
        self.write_cache([plugin()])
        before = self.cache_path().read_bytes()
        self.list_path.write_text('{')
        result = self.invoke()
        self.assertIn('using the saved copy', result.stdout)
        self.assertEqual(self.cache_path().read_bytes(), before)
        self.assertTrue(self.plugin_dir('example.widget').is_dir())

    def test_missing_list_without_a_cache_is_a_quiet_success(self):
        result = self.invoke(extra={'GROUNDWORK_PLUGIN_LIST': refused_https_url()}, timeout=25)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('unavailable; nothing changed', result.stdout)
        self.assertFalse(self.omarchy_calls())

    def test_non_https_list_is_rejected_without_a_connection(self):
        started = time.monotonic()
        result = self.invoke(extra={'GROUNDWORK_PLUGIN_LIST': 'http://127.0.0.1:9/plugins.json'}, timeout=5)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('invalid', result.stdout)
        self.assertIn('https', result.stdout)
        self.assertFalse(self.omarchy_calls())

    def test_omarchy_sections_are_forwarded_and_other_sections_are_invalid(self):
        self.write_list([
            plugin('example.left', section='left'),
            plugin('example.center', section='center'),
            plugin('example.right', section='right'),
            plugin('example.tray', section='tray'),
            plugin('example.begin', section='centerBegin'),
            plugin('example.end', section='centerEnd'),
            plugin('example.top', section='top'),
            plugin('example.lower', section='Left'),
            plugin('example.plain'),
        ])
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('plugin list is invalid', result.stdout)
        self.assertIn(['plugin', 'enable', 'example.left', '--section', 'left'], self.omarchy_calls())
        self.assertIn(['plugin', 'enable', 'example.center', '--section', 'center'], self.omarchy_calls())
        self.assertIn(['plugin', 'enable', 'example.right', '--section', 'right'], self.omarchy_calls())
        self.assertIn(['plugin', 'enable', 'example.tray', '--section', 'tray'], self.omarchy_calls())
        self.assertIn(['plugin', 'enable', 'example.begin', '--section', 'centerBegin'], self.omarchy_calls())
        self.assertIn(['plugin', 'enable', 'example.end', '--section', 'centerEnd'], self.omarchy_calls())
        self.assertIn(['plugin', 'enable', 'example.plain'], self.omarchy_calls())
        cached = json.loads(self.cache_path().read_text())
        self.assertEqual(
            [item['id'] for item in cached['plugins']],
            ['example.left', 'example.center', 'example.right', 'example.tray', 'example.begin', 'example.end', 'example.plain'],
        )
        self.assertNotIn('skippedSections', cached)
        for plugin_id in ('example.top', 'example.lower'):
            self.assertIn(f'Omarchy plugin {plugin_id} was skipped; section must be left, centerBegin, center, centerEnd, tray, or right.', result.stdout)
            self.assertFalse(self.plugin_dir(plugin_id).exists())
            self.assertNotIn(plugin_id, [item['id'] for item in cached['plugins']])

    def test_update_flag_updates_an_installed_plugin_without_reenabling_it(self):
        self.seed_directory('example.widget')
        self.write_record({'example.widget': {'repo': 'https://github.com/example/widget.git'}})
        self.write_list([plugin(section='right')])
        idle = self.invoke()
        self.assertEqual(idle.returncode, 0, idle.stdout + idle.stderr)
        self.assertFalse(self.omarchy_calls())
        updated = self.invoke('--update')
        self.assertEqual(updated.returncode, 0, updated.stdout + updated.stderr)
        self.assertEqual(self.omarchy_calls(), [['plugin', 'update', 'example.widget', '--yes']])

    def test_one_failed_add_does_not_block_the_next_plugin(self):
        self.set_control(fail_add=['example.widget'])
        self.write_list([plugin(section='right'), plugin('example.gadget')])
        first = self.invoke()
        self.assertIn('Omarchy plugin example.widget add failed: fixture add failed.', first.stdout)
        self.assertTrue(self.plugin_dir('example.gadget').is_dir())
        self.assertFalse(self.plugin_dir('example.widget').exists())
        self.set_control()
        self.clear_calls()
        second = self.invoke()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn(['plugin', 'add', 'https://github.com/example/widget.git', '--yes'], self.omarchy_calls())
        self.assertTrue(self.plugin_dir('example.widget').is_dir())

    def test_failed_enable_is_removed_and_retried(self):
        self.set_control(fail_enable=['example.widget'])
        self.write_list([plugin(section='right'), plugin('example.gadget')])
        first = self.invoke()
        self.assertIn('enable failed', first.stdout)
        self.assertIn(['plugin', 'remove', 'example.widget', '--yes'], self.omarchy_calls())
        self.assertFalse(self.plugin_dir('example.widget').exists())
        self.assertTrue(self.plugin_dir('example.gadget').is_dir())
        self.assertNotIn('example.widget', self.record()['plugins'])
        self.set_control()
        self.clear_calls()
        second = self.invoke()
        self.assertIn(['plugin', 'add', 'https://github.com/example/widget.git', '--yes'], self.omarchy_calls())
        self.assertIn(['plugin', 'enable', 'example.widget', '--section', 'right'], self.omarchy_calls())

    def test_failed_enable_retries_enable_when_removal_fails(self):
        self.set_control(fail_enable=['example.widget'], fail_remove=['example.widget'])
        self.write_list([plugin(section='right')])
        first = self.invoke()
        self.assertIn('remove failed', first.stdout)
        self.assertTrue(self.record()['plugins']['example.widget']['enablePending'])
        self.set_control()
        self.clear_calls()
        second = self.invoke()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(self.omarchy_calls(), [
            ['plugin', 'enable', 'example.widget', '--section', 'right'],
        ])
        self.assertNotIn('enablePending', self.record()['plugins']['example.widget'])

    def test_add_that_creates_a_directory_and_fails_retries_enable(self):
        self.set_control(add_then_fail=['example.widget'])
        self.write_list([plugin(section='right'), plugin('example.gadget')])
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn('Omarchy plugin example.widget add failed: fixture add failed.', first.stdout)
        self.assertIn(['plugin', 'enable', 'example.widget', '--section', 'right'], self.omarchy_calls())
        self.assertIn(['plugin', 'add', 'https://github.com/example/gadget.git', '--yes'], self.omarchy_calls())
        self.assertTrue(self.plugin_dir('example.widget').is_dir())
        self.assertNotIn('enablePending', self.record()['plugins']['example.widget'])
        self.assertIn('example.gadget', self.record()['plugins'])
        self.set_control()
        self.clear_calls()
        second = self.invoke()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertFalse(self.omarchy_calls(), second.stdout)
        self.assertNotIn('removed locally', second.stdout)

    def test_partial_add_with_a_manifest_mismatch_is_removed_and_the_next_plugin_continues(self):
        self.set_control(add_then_fail=['example.widget'], manifest_body_ids={'example.widget': 'example.other'})
        self.write_list([plugin(), plugin('example.gadget')])
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('add failed', result.stdout)
        self.assertIn('manifest id is example.other; removed.', result.stdout)
        self.assertNotIn('stopped', result.stdout)
        self.assertIn(['plugin', 'remove', 'example.widget', '--yes'], self.omarchy_calls())
        self.assertFalse(any(call[:3] == ['plugin', 'enable', 'example.widget'] for call in self.omarchy_calls()))
        self.assertFalse(self.plugin_dir('example.widget').exists())
        self.assertTrue(self.plugin_dir('example.gadget').is_dir())
        self.assertNotIn('example.widget', self.record()['plugins'])

    def test_remove_that_deletes_the_directory_and_fails_is_added_again(self):
        self.set_control(fail_enable=['example.widget'], remove_then_fail=['example.widget'])
        self.write_list([plugin(), plugin('example.gadget')])
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn('remove failed', first.stdout)
        self.assertNotIn('example.widget', self.record()['plugins'])
        self.assertNotIn('enablePending', self.record_path().read_text())
        self.assertFalse(self.plugin_dir('example.widget').exists())
        self.assertTrue(self.plugin_dir('example.gadget').is_dir())
        self.set_control()
        self.clear_calls()
        second = self.invoke()
        self.assertIn(['plugin', 'add', 'https://github.com/example/widget.git', '--yes'], self.omarchy_calls())
        self.assertNotIn('removed locally', second.stdout)
        self.assertTrue(self.plugin_dir('example.widget').is_dir())

    def test_enable_pending_without_a_directory_is_added_again(self):
        self.write_record({'example.widget': {
            'repo': 'https://github.com/example/widget.git',
            'enablePending': True,
        }})
        self.write_list([plugin(section='right')])
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('removed locally', result.stdout)
        self.assertIn(['plugin', 'add', 'https://github.com/example/widget.git', '--yes'], self.omarchy_calls())
        self.assertIn(['plugin', 'enable', 'example.widget', '--section', 'right'], self.omarchy_calls())
        self.assertNotIn('enablePending', self.record()['plugins']['example.widget'])

    def test_manifest_id_mismatch_removes_the_checkout_and_continues(self):
        self.set_control(manifest_ids={'example.widget': 'example.other'})
        self.write_list([plugin(), plugin('example.gadget')])
        result = self.invoke()
        self.assertIn('Omarchy plugin example.widget manifest id is example.other; removed.', result.stdout)
        self.assertNotIn('stopped', result.stdout)
        self.assertIn(['plugin', 'remove', 'example.other', '--yes'], self.omarchy_calls())
        self.assertFalse(any(call[:3] == ['plugin', 'enable', 'example.widget'] for call in self.omarchy_calls()))
        self.assertFalse(self.plugin_dir('example.other').exists())
        self.assertFalse(self.plugin_dir('example.widget').exists())
        self.assertTrue(self.plugin_dir('example.gadget').is_dir())
        self.assertNotIn('example.widget', self.record()['plugins'])
        self.assertIn('example.gadget', self.record()['plugins'])

    def test_manifest_body_mismatch_removes_the_checkout_and_continues(self):
        self.set_control(manifest_body_ids={'example.widget': 'example.other'})
        self.write_list([plugin(), plugin('example.gadget')])
        result = self.invoke()
        self.assertIn('manifest id is example.other; removed.', result.stdout)
        self.assertNotIn('stopped', result.stdout)
        self.assertIn(['plugin', 'remove', 'example.widget', '--yes'], self.omarchy_calls())
        self.assertFalse(any(call[:3] == ['plugin', 'enable', 'example.widget'] for call in self.omarchy_calls()))
        self.assertFalse(self.plugin_dir('example.widget').exists())
        self.assertTrue(self.plugin_dir('example.gadget').is_dir())
        self.assertNotIn('example.widget', self.record()['plugins'])

    def test_symbolic_link_plugin_path_is_not_changed(self):
        directory = self.plugin_dir('example.widget')
        directory.parent.mkdir(parents=True)
        target = self.fixture / 'elsewhere'
        target.mkdir()
        directory.symlink_to(target)
        self.write_list([plugin()])
        result = self.invoke('--update')
        self.assertIn('symbolic link', result.stdout)
        self.assertFalse(self.omarchy_calls())
        self.assertEqual(list(target.iterdir()), [])

    def test_setup_is_deferred_without_a_tty(self):
        self.write_list([plugin(setup={'command': 'setup', 'needsSudo': False})])
        result = self.invoke()
        script = self.plugin_dir('example.widget') / 'setup'
        self.assertIn(f'Omarchy plugin example.widget setup was deferred; run it in a terminal: {script}', result.stdout)
        self.assertNotIn('sudo --', result.stdout)
        self.assertFalse((self.fixture / 'setup-ran').exists())
        self.assertTrue(self.record()['plugins']['example.widget']['setup']['pending'])
        self.assertEqual(self.record()['plugins']['example.widget']['setup']['command'], 'setup')
        self.clear_calls()
        again = self.invoke()
        self.assertIn(str(script), again.stdout)
        self.assertFalse(self.omarchy_calls())

    def test_sudo_setup_is_deferred_without_calling_sudo(self):
        self.write_list([plugin(setup={'command': 'bin/setup', 'needsSudo': True})])
        result = self.invoke()
        script = self.plugin_dir('example.widget') / 'bin' / 'setup'
        self.assertIn(f'Omarchy plugin example.widget setup was deferred; run it in a terminal: {script}', result.stdout)
        self.assertNotIn('sudo --', result.stdout)
        self.assertFalse(any(call[0] == 'sudo' for call in self.calls()))
        self.assertFalse((self.fixture / 'setup-ran').exists())
        self.assertTrue(self.record()['plugins']['example.widget']['setup']['pending'])
        self.assertIs(self.record()['plugins']['example.widget']['setup']['needsSudo'], True)

    def test_setup_runs_on_a_tty_and_again_only_after_update_moves_head(self):
        self.write_list([plugin(setup={'command': 'setup', 'needsSudo': False})])
        first = self.run_tty(extra={'FIXTURE_GIT': '1'})
        self.assertEqual(first.returncode, 0, first.stdout)
        marker = self.fixture / 'setup-ran'
        self.assertEqual(marker.read_text(), 'x')
        head = self.record()['plugins']['example.widget']['setup']['head']
        self.assertRegex(head, r'^[0-9a-f]{40}$')
        observed = subprocess.check_output(['git', '-C', str(self.plugin_dir('example.widget')), 'rev-parse', 'HEAD'],
                                           text=True, env=self.env).strip()
        self.assertEqual(head, observed)
        self.clear_calls()
        second = self.run_tty(extra={'FIXTURE_GIT': '1'})
        self.assertEqual(marker.read_text(), 'x', second.stdout)
        self.assertFalse(self.omarchy_calls())
        subprocess.run(['git', '-C', str(self.plugin_dir('example.widget')), '-c', 'user.name=Fixture',
                        '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-m', 'local'],
                       check=True, env=self.env, capture_output=True, text=True)
        third = self.run_tty(extra={'FIXTURE_GIT': '1'})
        self.assertEqual(marker.read_text(), 'x', third.stdout)
        self.assertFalse(any(call[1] == 'update' for call in self.omarchy_calls()))
        self.clear_calls()
        fourth = self.run_tty('--update', extra={'FIXTURE_GIT': '1', 'FIXTURE_ADVANCE_HEAD': '1'})
        self.assertEqual(fourth.returncode, 0, fourth.stdout)
        self.assertIn(['plugin', 'update', 'example.widget', '--yes'], self.omarchy_calls())
        self.assertEqual(marker.read_text(), 'xx')
        self.assertNotEqual(self.record()['plugins']['example.widget']['setup']['head'], head)
        self.assertNotIn('pending', self.record()['plugins']['example.widget']['setup'])
        script = self.plugin_dir('example.widget') / 'setup'
        script.write_text('#!/bin/sh\nexit 1\n')
        script.chmod(0o755)
        self.clear_calls()
        failed = self.run_tty('--update', extra={'FIXTURE_GIT': '1', 'FIXTURE_ADVANCE_HEAD': '1'})
        self.assertEqual(failed.returncode, 0, failed.stdout)
        self.assertIn('setup failed', failed.stdout)
        self.assertEqual(marker.read_text(), 'xx')
        self.assertTrue(self.record()['plugins']['example.widget']['setup']['pending'])
        script.write_text('#!/bin/sh\nprintf x >> "$PLUGIN_FIXTURE/setup-ran"\n')
        script.chmod(0o755)
        self.clear_calls()
        retried = self.run_tty(extra={'FIXTURE_GIT': '1'})
        self.assertEqual(retried.returncode, 0, retried.stdout)
        self.assertEqual(marker.read_text(), 'xxx', retried.stdout)
        self.assertNotIn('pending', self.record()['plugins']['example.widget']['setup'])
        self.assertFalse(any(call[1] == 'update' for call in self.omarchy_calls()))
        self.clear_calls()
        deferred = self.invoke('--update', extra={'FIXTURE_GIT': '1', 'FIXTURE_ADVANCE_HEAD': '1'})
        self.assertEqual(deferred.returncode, 0, deferred.stdout + deferred.stderr)
        self.assertIn('setup was deferred', deferred.stdout)
        self.assertIn(['plugin', 'update', 'example.widget', '--yes'], self.omarchy_calls())
        self.assertEqual(marker.read_text(), 'xxx')
        self.assertTrue(self.record()['plugins']['example.widget']['setup']['pending'])
        self.clear_calls()
        finished = self.run_tty(extra={'FIXTURE_GIT': '1'})
        self.assertEqual(finished.returncode, 0, finished.stdout)
        self.assertEqual(marker.read_text(), 'xxxx', finished.stdout)
        self.assertNotIn('pending', self.record()['plugins']['example.widget']['setup'])
        self.assertFalse(any(call[1] == 'update' for call in self.omarchy_calls()))

    def test_failed_update_reruns_setup_when_head_moves(self):
        self.write_list([plugin(setup={'command': 'setup', 'needsSudo': False})])
        first = self.run_tty(extra={'FIXTURE_GIT': '1'})
        self.assertEqual(first.returncode, 0, first.stdout)
        self.assertEqual((self.fixture / 'setup-ran').read_text(), 'x')
        self.set_control(update_then_fail=['example.widget'])
        second = self.run_tty('--update', extra={'FIXTURE_GIT': '1'})
        self.assertEqual(second.returncode, 0, second.stdout)
        self.assertIn('update failed', second.stdout)
        self.assertEqual((self.fixture / 'setup-ran').read_text(), 'xx', second.stdout)
        self.assertNotIn('pending', self.record()['plugins']['example.widget']['setup'])

    def test_sudo_setup_uses_one_cached_credential(self):
        self.write_list([plugin(setup={'command': 'setup', 'needsSudo': True})])
        result = self.run_tty(extra={'FIXTURE_SUDO_CACHED': '1'})
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual((self.fixture / 'setup-ran').read_text(), 'x')
        sudo_calls = [call[1:] for call in self.calls() if call[0] == 'sudo']
        self.assertEqual(sudo_calls, [['-n', 'true']])
        self.assertNotIn('pending', self.record()['plugins']['example.widget']['setup'])

    def test_sudo_setup_prompts_once_when_the_credential_is_cold(self):
        self.write_list([plugin(setup={'command': 'setup', 'needsSudo': True})])
        result = self.run_tty()
        self.assertEqual(result.returncode, 0, result.stdout)
        sudo_calls = [call[1:] for call in self.calls() if call[0] == 'sudo']
        self.assertEqual(sudo_calls, [['-n', 'true'], ['-v']])
        self.assertEqual((self.fixture / 'setup-ran').read_text(), 'x')

    def test_trackpad_warning_names_the_later_file(self):
        hypr = self.home / '.config/hypr'
        hypr.mkdir(parents=True)
        (hypr / 'mouse.lua').write_text('touchpad = { natural_scroll = true }\n')
        (hypr / 'omacontrol.lua').write_text('touchpad = { natural_scroll = true }\n')
        (hypr / 'hyprland.lua').write_text('require("hypr.mouse")\nrequire("hypr.omacontrol")\n')
        result = self.invoke()
        self.assertIn(f'Warning: {hypr / "omacontrol.lua"} sets touchpad.natural_scroll differently', result.stderr)
        self.assertIn('wins.', result.stderr)

    def test_trackpad_warning_names_mouse_when_it_is_the_only_difference(self):
        hypr = self.home / '.config/hypr'
        hypr.mkdir(parents=True)
        (hypr / 'mouse.lua').write_text('touchpad = { natural_scroll = true }\n')
        result = self.invoke()
        self.assertIn(str(hypr / 'mouse.lua'), result.stderr)
        self.assertIn('is not loaded by hyprland.lua', result.stderr)
        self.assertIn('the stowed input.lua still applies', result.stderr)
        self.assertNotIn('wins.', result.stderr)

    def test_trackpad_warning_uses_omacontrol_when_no_require_is_present(self):
        hypr = self.home / '.config/hypr'
        hypr.mkdir(parents=True)
        (hypr / 'mouse.lua').write_text('touchpad = { natural_scroll = true }\n')
        (hypr / 'omacontrol.lua').write_text('touchpad = { natural_scroll = true }\n')
        (hypr / 'hyprland.lua').write_text('require("hypr.input")\n')
        result = self.invoke()
        self.assertIn(str(hypr / 'mouse.lua'), result.stderr)
        self.assertIn(str(hypr / 'omacontrol.lua'), result.stderr)
        self.assertIn('are not loaded by hyprland.lua', result.stderr)
        self.assertIn('the stowed input.lua still applies', result.stderr)
        self.assertNotIn('wins.', result.stderr)

    def test_matching_touchpad_value_is_quiet(self):
        hypr = self.home / '.config/hypr'
        hypr.mkdir(parents=True)
        (hypr / 'mouse.lua').write_text('''
        input = {
          natural_scroll = true,
          touchpad = {
            -- natural_scroll = true
            --[[ natural_scroll = true ]]
            natural_scroll = false,
          },
        }
        ''')
        result = self.invoke()
        self.assertEqual(result.stderr, '')

    def test_helper_is_called_once_after_hyprland_reload(self):
        text = (REPO / 'scripts/apply-layer.sh').read_text()
        self.assertLess(text.index('hyprctl reload'), text.index('scripts/omarchy-plugins.py" --update'))
        self.assertLess(text.index('omarchy-plugins.py'), text.index('install_package keyd'))
        self.assertIn("python3 \"$root/scripts/omarchy-plugins.py\" --update", text)
        self.assertIn("python3 \"$root/scripts/omarchy-plugins.py\" || plugin_status=$?", text)
        updater = (REPO / 'scripts/groundwork-update.py').read_text()
        self.assertIn("apply_args.append('--update-plugins')", updater)

    def test_default_list_url_is_the_shared_https_document(self):
        self.assertEqual(
            plugins.DEFAULT_LIST_URL,
            'https://raw.githubusercontent.com/jasondockery/groundwork-core/main/omarchy/plugins.json',
        )
        seen = []

        def fake(url, deadline):
            seen.append(url)
            self.assertGreater(deadline, time.monotonic())
            raise plugins.FetchError('offline')

        with patch.dict(os.environ, {
            'HOME': str(self.home),
            'XDG_CONFIG_HOME': str(self.home / '.config'),
            'XDG_STATE_HOME': str(self.home / '.local/state'),
        }):
            os.environ.pop('GROUNDWORK_PLUGIN_LIST', None)
            with patch.object(plugins, 'fetch_https', fake):
                document, fresh, notice = plugins.resolve_document()
        self.assertIsNone(document)
        self.assertFalse(fresh)
        self.assertEqual(seen, [plugins.DEFAULT_LIST_URL])
        self.assertIn('unavailable; nothing changed', notice)


class ParserTests(unittest.TestCase):
    def test_stowed_input_omits_upstream_natural_scroll(self):
        value = plugins.touchpad_natural_scroll(REPO / 'packages/hypr/.config/hypr/input.lua')
        self.assertIsNone(value)

    def test_touchpad_assignment_ignores_comments_and_input_level_values(self):
        temporary = tempfile.TemporaryDirectory(prefix='omarchy-lua-')
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / 'mouse.lua'
        path.write_text('''
        input = {
          natural_scroll = true,
          touchpad = { natural_scroll = false },
        }
        ''')
        self.assertIs(plugins.touchpad_natural_scroll(path), False)
        path.write_text('touchpad = { natural_scroll = false, natural_scroll = true }\n')
        self.assertIs(plugins.touchpad_natural_scroll(path), True)

    def test_read_bounded_sets_the_timeout_before_each_read(self):
        timeouts = []

        class Sock:
            def settimeout(self, value):
                timeouts.append(value)

        class Response:
            def __init__(self):
                self.chunks = [b'abc', b'def', b'']

            def isclosed(self):
                return False

            def read(self, _size):
                return self.chunks.pop(0)

        deadline = time.monotonic() + 5
        body = plugins.read_bounded(Response(), Sock(), deadline, 100)
        self.assertEqual(body, b'abcdef')
        self.assertEqual(len(timeouts), 3)
        self.assertTrue(all(0 < timeout <= 5 for timeout in timeouts))

    def test_read_bounded_does_not_read_after_the_deadline(self):
        class Response:
            def isclosed(self):
                return False

            def read(self, _size):
                raise AssertionError('read after the deadline')

        with self.assertRaises(TimeoutError):
            plugins.read_bounded(Response(), type('Sock', (), {'settimeout': lambda *args: None})(),
                                 time.monotonic() - 1, 100)


class FetchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='omarchy-fetch-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.cert = self.directory / 'cert.pem'
        self.key = self.directory / 'key.pem'
        subprocess.run([
            'openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-keyout', str(self.key), '-out', str(self.cert),
            '-days', '1', '-nodes', '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1',
        ], check=True, capture_output=True)

    def serve_https(self, respond):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, self.key)
        raw = socket.socket()
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        raw.bind(('127.0.0.1', 0))
        raw.listen(8)
        raw.settimeout(0.2)
        port = raw.getsockname()[1]
        stop = threading.Event()

        def loop():
            while not stop.is_set():
                try:
                    connection, _address = raw.accept()
                except socket.timeout:
                    continue
                tls = None
                try:
                    tls = context.wrap_socket(connection, server_side=True)
                    tls.settimeout(2)
                    data = b''
                    while b'\r\n\r\n' not in data and len(data) < 16384:
                        chunk = tls.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                    respond(tls, data, stop)
                except Exception:
                    pass
                finally:
                    try:
                        if tls is not None:
                            tls.close()
                        else:
                            connection.close()
                    except Exception:
                        pass
            raw.close()

        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(stop.set)
        return port

    def fetch(self, url, seconds):
        with patch.dict(os.environ, {'SSL_CERT_FILE': str(self.cert)}):
            return plugins.fetch_https(url, time.monotonic() + seconds)

    def test_non_https_redirect_is_rejected_before_the_next_connection(self):
        hits = []
        plain = socket.socket()
        plain.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        plain.bind(('127.0.0.1', 0))
        plain.listen(1)
        plain.settimeout(0.2)
        http_port = plain.getsockname()[1]
        stop = threading.Event()

        def accept_plain():
            while not stop.is_set():
                try:
                    connection, _address = plain.accept()
                except socket.timeout:
                    continue
                hits.append(True)
                connection.close()
            plain.close()

        thread = threading.Thread(target=accept_plain, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 1)
        self.addCleanup(stop.set)

        def respond(tls, _data, _stop):
            location = f'http://127.0.0.1:{http_port}/body'
            tls.sendall(f'HTTP/1.1 302 Found\r\nLocation: {location}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'.encode())

        port = self.serve_https(respond)
        started = time.monotonic()
        with self.assertRaises(plugins.ListError) as caught:
            self.fetch(f'https://127.0.0.1:{port}/list', 5)
        self.assertLess(time.monotonic() - started, 3)
        self.assertIn('rejected a non-https redirect', str(caught.exception))
        time.sleep(0.2)
        self.assertEqual(hits, [])

    def test_https_redirect_is_followed(self):
        body = b'{"schemaVersion":1,"plugins":[],"retired":[]}'

        def respond(tls, data, _stop):
            path = data.decode('iso-8859-1', 'replace').split(' ', 2)[1]
            if path.startswith('/list'):
                tls.sendall(b'HTTP/1.1 302 Found\r\nLocation: /body\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                return
            header = f'HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode()
            tls.sendall(header + body)

        port = self.serve_https(respond)
        self.assertEqual(self.fetch(f'https://127.0.0.1:{port}/list', 5), body)

    def test_a_stalled_body_stops_at_the_remaining_deadline(self):
        def respond(tls, _data, stop):
            tls.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 80\r\nConnection: close\r\n\r\npartial')
            stop.wait(5)

        port = self.serve_https(respond)
        started = time.monotonic()
        with self.assertRaises(plugins.FetchError) as caught:
            self.fetch(f'https://127.0.0.1:{port}/plugins.json', 0.6)
        elapsed = time.monotonic() - started
        self.assertGreater(elapsed, 0.3, caught.exception)
        self.assertLess(elapsed, 3, caught.exception)
        self.assertIn('deadline', str(caught.exception))

    def test_userinfo_is_rejected_before_a_connection(self):
        started = time.monotonic()
        with self.assertRaises(plugins.ListError) as caught:
            plugins.fetch_https('https://user:pass@127.0.0.1/plugins.json', time.monotonic() + 5)
        self.assertLess(time.monotonic() - started, 2)
        self.assertIn('https', str(caught.exception))

    def test_redirect_may_move_only_to_the_starting_host_or_github(self):
        started = 'downloads.example.test'
        for host in (started, 'RAW.GITHUBUSERCONTENT.COM', 'objects.githubusercontent.com', 'github.com'):
            plugins.allowed_redirect(started, f'https://{host}/plugins.json')
        for target in (
            'https://example.com/plugins.json',
            'http://github.com/plugins.json',
            'https://github.com.evil/plugins.json',
        ):
            with self.assertRaises(plugins.ListError):
                plugins.allowed_redirect(started, target)

    def test_redirect_to_another_host_is_rejected_and_keeps_the_cache(self):
        def respond(tls, _data, _stop):
            tls.sendall(b'HTTP/1.1 302 Found\r\nLocation: https://example.com/plugins.json\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')

        port = self.serve_https(respond)
        state = self.directory / 'state'
        cache = state / 'groundwork' / 'omarchy-plugins.json'
        cache.parent.mkdir(parents=True)
        cache.write_text(json.dumps({'schemaVersion': 1, 'plugins': [], 'retired': []}) + '\n')
        before = cache.read_bytes()
        home = self.directory / 'home'
        home.mkdir()
        env = {
            'SSL_CERT_FILE': str(self.cert),
            'XDG_STATE_HOME': str(state),
            'XDG_CONFIG_HOME': str(home / '.config'),
            'HOME': str(home),
            'GROUNDWORK_PLUGIN_LIST': f'https://127.0.0.1:{port}/plugins.json',
        }
        started = time.monotonic()
        with patch.dict(os.environ, env):
            document, fresh, notice = plugins.resolve_document()
        self.assertLess(time.monotonic() - started, 3)
        self.assertFalse(fresh)
        self.assertIn('different host', notice)
        self.assertIn('using the saved copy', notice)
        self.assertEqual(cache.read_bytes(), before)
        self.assertEqual(document['plugins'], [])

    def test_incomplete_chunked_body_uses_the_saved_copy(self):
        def respond(tls, _data, _stop):
            tls.sendall(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n64\r\nshort\r\n')

        port = self.serve_https(respond)
        with self.assertRaises(plugins.FetchError) as caught:
            self.fetch(f'https://127.0.0.1:{port}/plugins.json', 5)
        self.assertIn('download failed', str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, http.client.HTTPException)
        state = self.directory / 'state'
        cache = state / 'groundwork' / 'omarchy-plugins.json'
        cache.parent.mkdir(parents=True)
        cache.write_text(json.dumps({
            'schemaVersion': 1,
            'plugins': [plugin()],
            'retired': [],
        }) + '\n')
        before = cache.read_bytes()
        home = self.directory / 'home'
        home.mkdir()
        env = {
            'SSL_CERT_FILE': str(self.cert),
            'XDG_STATE_HOME': str(state),
            'XDG_CONFIG_HOME': str(home / '.config'),
            'HOME': str(home),
            'GROUNDWORK_PLUGIN_LIST': f'https://127.0.0.1:{port}/plugins.json',
        }
        with patch.dict(os.environ, env):
            document, fresh, notice = plugins.resolve_document()
        self.assertFalse(fresh)
        self.assertIn('using the saved copy', notice)
        self.assertEqual(cache.read_bytes(), before)
        self.assertEqual(document['plugins'][0]['id'], 'example.widget')


if __name__ == '__main__':
    unittest.main()
