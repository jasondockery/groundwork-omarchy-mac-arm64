"""Test private credentials and fail-closed Tailscale listener lifecycle."""
import ctypes
import errno
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, call, patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/groundwork-screen-share.py'
SHOW_ACTIVE_STATE = [
    'systemctl', 'show', '--user', '-p', 'ActiveState', '--value',
    'groundwork-screen-share.service',
]


def _tcp_states(address, port):
    # /proc/net/tcp prints IPv4 addresses little-endian. 06 is TIME_WAIT.
    token = socket.inet_aton(address)[::-1].hex().upper() + f':{port:04X}'
    states = []
    for line in Path('/proc/net/tcp').read_text().splitlines()[1:]:
        local, _remote, state = line.split()[1:4]
        if local == token:
            states.append(state)
    return states
spec = importlib.util.spec_from_file_location('screen_share', SCRIPT)
screen_share = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screen_share)


def _load_xkbcommon():
    if _load_xkbcommon.library is not None or _load_xkbcommon.missing:
        return _load_xkbcommon.library
    try:
        library = ctypes.CDLL('libxkbcommon.so.0')
    except OSError:
        _load_xkbcommon.missing = True
        return None
    library.xkb_context_new.restype = ctypes.c_void_p
    library.xkb_keymap_new_from_names.argtypes = [ctypes.c_void_p, ctypes.POINTER(_XkbNames), ctypes.c_int]
    library.xkb_keymap_new_from_names.restype = ctypes.c_void_p
    library.xkb_keymap_unref.argtypes = [ctypes.c_void_p]
    library.xkb_context_unref.argtypes = [ctypes.c_void_p]
    library.xkb_state_new.argtypes = [ctypes.c_void_p]
    library.xkb_state_new.restype = ctypes.c_void_p
    library.xkb_state_unref.argtypes = [ctypes.c_void_p]
    library.xkb_state_update_key.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int]
    library.xkb_state_serialize_mods.argtypes = [ctypes.c_void_p, ctypes.c_int]
    library.xkb_state_serialize_mods.restype = ctypes.c_uint32
    library.xkb_keymap_min_keycode.argtypes = [ctypes.c_void_p]
    library.xkb_keymap_min_keycode.restype = ctypes.c_uint32
    library.xkb_keymap_max_keycode.argtypes = [ctypes.c_void_p]
    library.xkb_keymap_max_keycode.restype = ctypes.c_uint32
    library.xkb_keymap_num_levels_for_key.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
    library.xkb_keymap_num_levels_for_key.restype = ctypes.c_int
    library.xkb_keymap_key_get_syms_by_level.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_uint32))]
    library.xkb_keymap_key_get_syms_by_level.restype = ctypes.c_int
    library.xkb_keymap_key_get_name.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    library.xkb_keymap_key_get_name.restype = ctypes.c_char_p
    library.xkb_keysym_from_name.argtypes = [ctypes.c_char_p, ctypes.c_int]
    library.xkb_keysym_from_name.restype = ctypes.c_uint32
    _load_xkbcommon.library = library
    return library


_load_xkbcommon.library = None
_load_xkbcommon.missing = False


class _XkbNames(ctypes.Structure):
    _fields_ = [('rules', ctypes.c_char_p), ('model', ctypes.c_char_p),
                ('layout', ctypes.c_char_p), ('variant', ctypes.c_char_p),
                ('options', ctypes.c_char_p)]


def _compile_keymap(library, layout, options):
    # NULL rules match WayVNC: libxkbcommon reads XKB_DEFAULT_RULES.
    names = _XkbNames(None, b'pc105', layout, None, options)
    context = library.xkb_context_new(0)
    keymap = library.xkb_keymap_new_from_names(context, ctypes.byref(names), 0)
    library.xkb_context_unref(context)
    if options and not keymap:
        raise AssertionError('WayVNC keymap failed to compile')
    return keymap


def _keycode(library, keymap, keysym_name):
    wanted = library.xkb_keysym_from_name(keysym_name.encode(), 0)
    for code in range(library.xkb_keymap_min_keycode(keymap), library.xkb_keymap_max_keycode(keymap) + 1):
        for level in range(library.xkb_keymap_num_levels_for_key(keymap, code, 0)):
            symbols = ctypes.POINTER(ctypes.c_uint32)()
            count = library.xkb_keymap_key_get_syms_by_level(keymap, code, 0, level, ctypes.byref(symbols))
            if any(symbols[index] == wanted for index in range(max(count, 0))):
                return code
    raise AssertionError('missing keysym ' + keysym_name)


def _mods(library, keymap, keysym_names):
    state = library.xkb_state_new(keymap)
    for name in keysym_names:
        library.xkb_state_update_key(state, _keycode(library, keymap, name), 1)
    mask = library.xkb_state_serialize_mods(state, 1)
    library.xkb_state_unref(state)
    return mask


def _evdev_post_is_honored(library):
    # rules/<ruleset>.post is composed from every include path starting with
    # libxkbcommon 1.13. Older libraries ignore the fragment.
    if _evdev_post_is_honored.cached is not None:
        return _evdev_post_is_honored.cached
    honored = False
    with tempfile.TemporaryDirectory(prefix='groundwork-xkb-post-') as temporary:
        root = Path(temporary)
        extra = root / 'xkb'
        (extra / 'rules').mkdir(parents=True)
        (extra / 'symbols').mkdir()
        (extra / 'rules' / 'evdev.post').write_text(
            '! option = symbols\n'
            '  groundwork:post_probe = +groundwork(post_probe)\n')
        (extra / 'symbols' / 'groundwork').write_text(
            'partial modifier_keys\n'
            'xkb_symbols "post_probe" {\n'
            '    replace key <LALT> {\n'
            '        type[Group1] = "ONE_LEVEL",\n'
            '        symbols[Group1] = [ Alt_L ],\n'
            '        actions[Group1] = [ SetMods(modifiers=Mod4,clearLocks) ]\n'
            '    };\n'
            '};\n')
        home = root / 'home'
        home.mkdir()
        saved_stderr = os.dup(2)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 2)
        try:
            with patch.dict(os.environ, {
                'HOME': str(home),
                'XDG_CONFIG_HOME': str(home / '.config'),
                'XKB_CONFIG_EXTRA_PATH': str(extra),
                'XKB_DEFAULT_RULES': 'evdev',
            }):
                try:
                    keymap = _compile_keymap(library, layout=None, options=b'groundwork:post_probe')
                except AssertionError:
                    keymap = None
        finally:
            os.dup2(saved_stderr, 2)
            os.close(saved_stderr)
            os.close(devnull)
        if keymap:
            honored = _mods(library, keymap, ['Alt_L']) == 0x40
            library.xkb_keymap_unref(keymap)
    _evdev_post_is_honored.cached = honored
    return honored


_evdev_post_is_honored.cached = None


def _named_key_mods(library, keymap, key_name):
    state = library.xkb_state_new(keymap)
    code = None
    for candidate in range(library.xkb_keymap_min_keycode(keymap), library.xkb_keymap_max_keycode(keymap) + 1):
        if library.xkb_keymap_key_get_name(keymap, candidate) == key_name:
            code = candidate
            break
    if code is None:
        library.xkb_state_unref(state)
        raise AssertionError('missing key ' + key_name.decode())
    library.xkb_state_update_key(state, code, 1)
    mask = library.xkb_state_serialize_mods(state, 1)
    library.xkb_state_unref(state)
    return mask


class ScreenShareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='groundwork-screen-share-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.home_patch = patch.object(Path, 'home', return_value=self.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        # served_config_changed reads the process runtime dir, not Path.home.
        # A desktop's live config.sha256 would otherwise look like a change.
        self.runtime = self.home / 'runtime'
        self.runtime.mkdir()
        self.marker = self.home / '.local/state/groundwork/screen-share-restart-needed'
        self.runtime_patch = patch.dict(os.environ, {'XDG_RUNTIME_DIR': str(self.runtime)})
        self.runtime_patch.start()
        self.addCleanup(self.runtime_patch.stop)

    def _seed_matching_files(self, password='abcdefgh'):
        config = screen_share.config_path()
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(screen_share.config_text(password))
        binary = self.home / '.local/bin/groundwork-screen-share'
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(Path(screen_share.__file__).read_bytes())
        unit = self.home / '.config/systemd/user/groundwork-screen-share.service'
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(screen_share.UNIT)
        return config, unit

    def _commands(self, run):
        return [call.args[0] for call in run.call_args_list]

    def test_configuration_is_private_idempotent_and_automatically_enabled(self):
        with patch.object(screen_share.subprocess, 'run') as run:
            with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'fixture-wayland'}):
                with redirect_stdout(io.StringIO()) as first_out:
                    screen_share.configure()
                first = screen_share.config_path().read_bytes()
                first_commands = [call.args[0] for call in run.call_args_list]
                run.reset_mock()
                with redirect_stdout(io.StringIO()) as second_out:
                    screen_share.configure()
                second_commands = [call.args[0] for call in run.call_args_list]
        config = screen_share.config_path()
        self.assertEqual(first, config.read_bytes())
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(config.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(len(screen_share.read_config(config)), 8)
        self.assertIn('enable_auth=true', config.read_text())
        unit = (self.home / '.config/systemd/user/groundwork-screen-share.service').read_text()
        self.assertIn('WantedBy=graphical-session.target', unit)
        self.assertIn('PartOf=graphical-session.target', unit)
        restart = ['systemctl', '--user', 'restart', 'groundwork-screen-share.service']
        self.assertIn(restart, first_commands)
        self.assertIn('Restarting screen sharing; an active remote session will drop.',
                      first_out.getvalue())
        self.assertNotIn(restart, second_commands)
        self.assertIn(['systemctl', '--user', 'enable', 'groundwork-screen-share.service'], second_commands)
        self.assertNotIn('will drop', second_out.getvalue())

    def test_unchanged_unit_helper_and_config_are_not_restarted(self):
        config = screen_share.config_path()
        config.parent.mkdir(parents=True)
        config.write_text(screen_share.config_text('abcdefgh'))
        binary = self.home / '.local/bin/groundwork-screen-share'
        binary.parent.mkdir(parents=True)
        binary.write_bytes(Path(screen_share.__file__).read_bytes())
        unit = self.home / '.config/systemd/user/groundwork-screen-share.service'
        unit.parent.mkdir(parents=True)
        unit.write_text(screen_share.UNIT)
        with patch.object(screen_share.subprocess, 'run') as run:
            with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'fixture-wayland'}):
                with redirect_stdout(io.StringIO()) as out:
                    screen_share.configure()
        commands = [call.args[0] for call in run.call_args_list]
        self.assertNotIn(['systemctl', '--user', 'restart', 'groundwork-screen-share.service'], commands)
        self.assertIn(['systemctl', '--user', 'daemon-reload'], commands)
        self.assertIn(['systemctl', '--user', 'enable', 'groundwork-screen-share.service'], commands)
        self.assertNotIn('will drop', out.getvalue())
        self.assertEqual(config.read_text(), screen_share.config_text('abcdefgh'))

    def test_changed_unit_restarts_and_warns_that_the_session_drops(self):
        with patch.object(screen_share.subprocess, 'run') as run:
            with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'fixture-wayland'}):
                with redirect_stdout(io.StringIO()):
                    screen_share.configure()
                unit = self.home / '.config/systemd/user/groundwork-screen-share.service'
                unit.write_text(screen_share.UNIT.replace('RestartSec=10', 'RestartSec=11'))
                run.reset_mock()
                with redirect_stdout(io.StringIO()) as out:
                    screen_share.configure()
        self.assertIn(['systemctl', '--user', 'restart', 'groundwork-screen-share.service'],
                      [call.args[0] for call in run.call_args_list])
        self.assertIn('Restarting screen sharing; an active remote session will drop.', out.getvalue())
        self.assertIn('RestartSec=10', unit.read_text())

    def test_deleted_unit_is_rewritten_and_restarted(self):
        with patch.object(screen_share.subprocess, 'run') as run:
            with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'fixture-wayland'}):
                with redirect_stdout(io.StringIO()):
                    screen_share.configure()
                unit = self.home / '.config/systemd/user/groundwork-screen-share.service'
                unit.unlink()
                run.reset_mock()
                with redirect_stdout(io.StringIO()) as out:
                    screen_share.configure()
        self.assertEqual(unit.read_text(), screen_share.UNIT)
        self.assertIn(['systemctl', '--user', 'restart', 'groundwork-screen-share.service'],
                      [call.args[0] for call in run.call_args_list])
        self.assertIn('Restarting screen sharing; an active remote session will drop.', out.getvalue())

    def test_served_config_stamp_restarts_after_a_valid_password_edit(self):
        config = screen_share.config_path()
        config.parent.mkdir(parents=True)
        config.write_text(screen_share.config_text('abcdefgh'))
        binary = self.home / '.local/bin/groundwork-screen-share'
        binary.parent.mkdir(parents=True)
        binary.write_bytes(Path(screen_share.__file__).read_bytes())
        unit = self.home / '.config/systemd/user/groundwork-screen-share.service'
        unit.parent.mkdir(parents=True)
        unit.write_text(screen_share.UNIT)
        runtime = self.home / 'runtime'
        restart = ['systemctl', '--user', 'restart', 'groundwork-screen-share.service']
        with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'fixture-wayland', 'XDG_RUNTIME_DIR': str(runtime)}):
            screen_share.remember_served_config(config.read_bytes())
            with patch.object(screen_share.subprocess, 'run') as run:
                with redirect_stdout(io.StringIO()) as same:
                    screen_share.configure()
                same_commands = [call.args[0] for call in run.call_args_list]
                config.write_text(screen_share.config_text('ijklmnop'))
                run.reset_mock()
                with redirect_stdout(io.StringIO()) as edited:
                    screen_share.configure()
                edited_commands = [call.args[0] for call in run.call_args_list]
        self.assertNotIn(restart, same_commands)
        self.assertNotIn('will drop', same.getvalue())
        self.assertIn(restart, edited_commands)
        self.assertIn('Restarting screen sharing; an active remote session will drop.', edited.getvalue())
        self.assertEqual(config.read_text(), screen_share.config_text('ijklmnop'))

    def test_serve_waits_without_recording_a_config_stamp(self):
        config = screen_share.config_path()
        config.parent.mkdir(parents=True)
        config.write_text(screen_share.config_text('abcdefgh'))
        with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'fixture-wayland'}):
            with patch.object(screen_share, 'tailscale_address', return_value=None):
                with patch.object(screen_share.time, 'sleep', side_effect=KeyboardInterrupt):
                    with patch.object(screen_share.signal, 'signal'):
                        with self.assertRaises(KeyboardInterrupt):
                            screen_share.serve()
        self.assertFalse((self.runtime / 'groundwork-screen-share' / 'config.sha256').exists())

    def test_config_stamp_is_taken_immediately_before_wayvnc_starts(self):
        config = screen_share.config_path()
        config.parent.mkdir(parents=True)
        config.write_text(screen_share.config_text('abcdefgh'))

        def edit_after_bind(listener, address, port=5900, device=b'tailscale0', wait=70, pause=0.25):
            config.write_text(screen_share.config_text('ijklmnop'))

        listener = Mock()
        listener.fileno.return_value = 42
        with patch.object(screen_share.socket, 'socket') as create:
            create.return_value.__enter__.return_value = listener
            with patch.object(screen_share, 'bind_listener', side_effect=edit_after_bind):
                with patch.object(screen_share, 'tailscale_address', return_value='100.64.0.2'):
                    with patch.object(screen_share.subprocess, 'Popen', return_value=Mock()) as spawn:
                        screen_share.start_server('100.64.0.2', self.runtime)
        stamp = self.runtime / 'groundwork-screen-share' / 'config.sha256'
        digest = hashlib.sha256(config.read_bytes()).hexdigest()
        self.assertEqual(stamp.read_text(), digest + '\n')
        self.assertEqual(stamp.stat().st_mode & 0o777, 0o600)
        self.assertIn('--config=' + str(config), spawn.call_args.args[0])

    def test_failed_restart_is_retried_after_the_files_match(self):
        restart = ['systemctl', '--user', 'restart', 'groundwork-screen-share.service']
        marker = self.marker

        def fail_restart(command, **kwargs):
            if command == restart:
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0)

        with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'fixture-wayland'}):
            with patch.object(screen_share.subprocess, 'run', side_effect=fail_restart):
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(subprocess.CalledProcessError):
                        screen_share.configure()
            self.assertEqual(marker.read_text(), 'pending\n')
            with patch.object(screen_share.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
                with redirect_stdout(io.StringIO()) as out:
                    screen_share.configure()
        self.assertIn(restart, [call.args[0] for call in run.call_args_list])
        self.assertIn('Restarting screen sharing; an active remote session will drop.', out.getvalue())
        self.assertFalse(marker.exists())

    def test_restart_marker_is_recorded_before_daemon_reload(self):
        marker = self.marker
        reload = ['systemctl', '--user', 'daemon-reload']

        def fail_reload(command, **kwargs):
            if command == reload:
                self.assertEqual(marker.read_text(), 'pending\n')
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0)

        with patch.object(screen_share.subprocess, 'run', side_effect=fail_reload) as run:
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(subprocess.CalledProcessError):
                    screen_share.configure()
        self.assertEqual(marker.read_text(), 'pending\n')
        self.assertNotIn(['systemctl', '--user', 'restart', 'groundwork-screen-share.service'],
                         self._commands(run))

    def test_active_service_restarts_without_a_wayland_display(self):
        self._seed_matching_files()
        unit = self.home / '.config/systemd/user/groundwork-screen-share.service'
        unit.write_text(screen_share.UNIT.replace('RestartSec=10', 'RestartSec=11'))
        marker = self.marker
        restart = ['systemctl', '--user', 'restart', 'groundwork-screen-share.service']
        seen = []

        def answer(command, **kwargs):
            if command == ['systemctl', '--user', 'daemon-reload']:
                seen.append(marker.read_text())
            if command == SHOW_ACTIVE_STATE:
                return subprocess.CompletedProcess(command, 0, stdout='active\n')
            return subprocess.CompletedProcess(command, 0, stdout='')

        with patch.dict(os.environ):
            os.environ.pop('WAYLAND_DISPLAY', None)
            with patch.object(screen_share.subprocess, 'run', side_effect=answer) as run:
                with redirect_stdout(io.StringIO()) as out:
                    screen_share.configure()
        self.assertEqual(seen, ['pending\n'])
        self.assertIn(SHOW_ACTIVE_STATE, self._commands(run))
        self.assertIn(restart, self._commands(run))
        self.assertIn('Restarting screen sharing; an active remote session will drop.', out.getvalue())
        self.assertFalse(marker.exists())

    def test_inactive_service_drops_the_marker_without_restarting(self):
        marker = self.marker
        recorded = []

        def answer(command, **kwargs):
            if command == ['systemctl', '--user', 'daemon-reload']:
                recorded.append(marker.read_text())
            if command == SHOW_ACTIVE_STATE:
                return subprocess.CompletedProcess(command, 0, stdout='inactive\n')
            return subprocess.CompletedProcess(command, 0, stdout='')

        with patch.dict(os.environ):
            os.environ.pop('WAYLAND_DISPLAY', None)
            with patch.object(screen_share.subprocess, 'run', side_effect=answer) as run:
                with redirect_stdout(io.StringIO()) as out:
                    screen_share.configure()
        self.assertEqual(recorded, ['pending\n'])
        self.assertNotIn(['systemctl', '--user', 'restart', 'groundwork-screen-share.service'],
                         self._commands(run))
        self.assertNotIn('will drop', out.getvalue())
        self.assertFalse(marker.exists())

    def test_systemctl_error_keeps_the_restart_marker(self):
        self._seed_matching_files()
        unit = self.home / '.config/systemd/user/groundwork-screen-share.service'
        unit.write_text(screen_share.UNIT.replace('RestartSec=10', 'RestartSec=11'))
        marker = self.marker
        restart = ['systemctl', '--user', 'restart', 'groundwork-screen-share.service']

        def unknown(command, **kwargs):
            if command == SHOW_ACTIVE_STATE:
                return subprocess.CompletedProcess(command, 1, stdout='')
            return subprocess.CompletedProcess(command, 0, stdout='')

        def active(command, **kwargs):
            if command == SHOW_ACTIVE_STATE:
                return subprocess.CompletedProcess(command, 0, stdout='active\n')
            return subprocess.CompletedProcess(command, 0, stdout='')

        with patch.dict(os.environ):
            os.environ.pop('WAYLAND_DISPLAY', None)
            with patch.object(screen_share.subprocess, 'run', side_effect=unknown) as first:
                with redirect_stdout(io.StringIO()):
                    screen_share.configure()
            self.assertEqual(marker.read_text(), 'pending\n')
            self.assertNotIn(restart, self._commands(first))
            unit.write_text(screen_share.UNIT)
            with patch.object(screen_share.subprocess, 'run', side_effect=active) as second:
                with redirect_stdout(io.StringIO()) as out:
                    screen_share.configure()
        self.assertIn(restart, self._commands(second))
        self.assertIn('Restarting screen sharing; an active remote session will drop.', out.getvalue())
        self.assertFalse(marker.exists())

    def test_systemctl_oserror_keeps_the_restart_marker(self):
        marker = self.marker

        def fail(command, **kwargs):
            if command == SHOW_ACTIVE_STATE:
                raise OSError('systemctl is unavailable')
            return subprocess.CompletedProcess(command, 0, stdout='')

        with patch.dict(os.environ):
            os.environ.pop('WAYLAND_DISPLAY', None)
            with patch.object(screen_share.subprocess, 'run', side_effect=fail) as run:
                with redirect_stdout(io.StringIO()) as out:
                    screen_share.configure()
        self.assertEqual(marker.read_text(), 'pending\n')
        self.assertNotIn(['systemctl', '--user', 'restart', 'groundwork-screen-share.service'],
                         self._commands(run))
        self.assertNotIn('will drop', out.getvalue())

    def test_home_restart_record_restarts_while_a_runtime_dir_exists(self):
        self._seed_matching_files()
        self.marker.parent.mkdir(parents=True)
        self.marker.write_text('pending\n')
        restart = ['systemctl', '--user', 'restart', 'groundwork-screen-share.service']

        def active(command, **kwargs):
            if command == SHOW_ACTIVE_STATE:
                return subprocess.CompletedProcess(command, 0, stdout='active\n')
            return subprocess.CompletedProcess(command, 0, stdout='')

        with patch.dict(os.environ):
            os.environ.pop('WAYLAND_DISPLAY', None)
            with patch.object(screen_share.subprocess, 'run', side_effect=active) as run:
                with redirect_stdout(io.StringIO()) as out:
                    screen_share.configure()
        self.assertTrue(self.runtime.is_dir())
        self.assertIn(restart, self._commands(run))
        self.assertIn('Restarting screen sharing; an active remote session will drop.', out.getvalue())
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.runtime / 'groundwork-screen-share' / 'restart-needed').exists())

    def test_failed_unit_is_restarted(self):
        self._seed_matching_files()
        self.marker.parent.mkdir(parents=True)
        self.marker.write_text('pending\n')
        restart = ['systemctl', '--user', 'restart', 'groundwork-screen-share.service']

        def failed(command, **kwargs):
            if command == SHOW_ACTIVE_STATE:
                return subprocess.CompletedProcess(command, 0, stdout='failed\n')
            return subprocess.CompletedProcess(command, 0, stdout='')

        with patch.dict(os.environ):
            os.environ.pop('WAYLAND_DISPLAY', None)
            with patch.object(screen_share.subprocess, 'run', side_effect=failed) as run:
                with redirect_stdout(io.StringIO()) as out:
                    screen_share.configure()
        self.assertIn(restart, self._commands(run))
        self.assertIn('Restarting screen sharing; an active remote session will drop.', out.getvalue())
        self.assertFalse(self.marker.exists())

    def test_activating_unit_keeps_the_restart_record(self):
        self._seed_matching_files()
        self.marker.parent.mkdir(parents=True)
        self.marker.write_text('pending\n')
        restart = ['systemctl', '--user', 'restart', 'groundwork-screen-share.service']

        def activating(command, **kwargs):
            if command == SHOW_ACTIVE_STATE:
                return subprocess.CompletedProcess(command, 0, stdout='activating\n')
            return subprocess.CompletedProcess(command, 0, stdout='')

        with patch.dict(os.environ):
            os.environ.pop('WAYLAND_DISPLAY', None)
            with patch.object(screen_share.subprocess, 'run', side_effect=activating) as run:
                with redirect_stdout(io.StringIO()) as out:
                    screen_share.configure()
        self.assertIn(SHOW_ACTIVE_STATE, self._commands(run))
        self.assertNotIn(restart, self._commands(run))
        self.assertNotIn('will drop', out.getvalue())
        self.assertEqual(self.marker.read_text(), 'pending\n')

    def test_deactivating_unit_keeps_the_restart_record(self):
        self._seed_matching_files()
        self.marker.parent.mkdir(parents=True)
        self.marker.write_text('pending\n')
        restart = ['systemctl', '--user', 'restart', 'groundwork-screen-share.service']

        def deactivating(command, **kwargs):
            if command == SHOW_ACTIVE_STATE:
                return subprocess.CompletedProcess(command, 0, stdout='deactivating\n')
            return subprocess.CompletedProcess(command, 0, stdout='')

        with patch.dict(os.environ):
            os.environ.pop('WAYLAND_DISPLAY', None)
            with patch.object(screen_share.subprocess, 'run', side_effect=deactivating) as run:
                with redirect_stdout(io.StringIO()) as out:
                    screen_share.configure()
        self.assertIn(SHOW_ACTIVE_STATE, self._commands(run))
        self.assertNotIn(restart, self._commands(run))
        self.assertNotIn('will drop', out.getvalue())
        self.assertEqual(self.marker.read_text(), 'pending\n')

    def test_reloading_unit_is_restarted(self):
        self._seed_matching_files()
        self.marker.parent.mkdir(parents=True)
        self.marker.write_text('pending\n')
        restart = ['systemctl', '--user', 'restart', 'groundwork-screen-share.service']

        def reloading(command, **kwargs):
            if command == SHOW_ACTIVE_STATE:
                return subprocess.CompletedProcess(command, 0, stdout='reloading\n')
            return subprocess.CompletedProcess(command, 0, stdout='')

        with patch.dict(os.environ):
            os.environ.pop('WAYLAND_DISPLAY', None)
            with patch.object(screen_share.subprocess, 'run', side_effect=reloading) as run:
                with redirect_stdout(io.StringIO()) as out:
                    screen_share.configure()
        self.assertIn(restart, self._commands(run))
        self.assertIn('Restarting screen sharing; an active remote session will drop.', out.getvalue())
        self.assertFalse(self.marker.exists())

    def test_failed_unit_restarts_when_nothing_else_changed(self):
        config, _unit = self._seed_matching_files()
        screen_share.remember_served_config(config.read_bytes())
        self.assertFalse(self.marker.exists())
        restart = ['systemctl', '--user', 'restart', 'groundwork-screen-share.service']
        stamp = self.runtime / 'groundwork-screen-share' / 'config.sha256'
        current = stamp.read_text()

        def failed(command, **kwargs):
            if command == ['systemctl', '--user', 'daemon-reload']:
                self.assertFalse(self.marker.exists())
            if command == SHOW_ACTIVE_STATE:
                return subprocess.CompletedProcess(command, 0, stdout='failed\n')
            if command == restart:
                self.assertEqual(stamp.read_text(), current)
                self.assertFalse(self.marker.exists())
            return subprocess.CompletedProcess(command, 0, stdout='')

        with patch.dict(os.environ):
            os.environ.pop('WAYLAND_DISPLAY', None)
            with patch.object(screen_share.subprocess, 'run', side_effect=failed) as run:
                with redirect_stdout(io.StringIO()) as out:
                    screen_share.configure()
        self.assertIn(SHOW_ACTIVE_STATE, self._commands(run))
        self.assertIn(restart, self._commands(run))
        self.assertIn('Restarting screen sharing; an active remote session will drop.', out.getvalue())
        self.assertFalse(self.marker.exists())
        self.assertEqual(stamp.read_text(), current)

        def fail_restart(command, **kwargs):
            if command == SHOW_ACTIVE_STATE:
                return subprocess.CompletedProcess(command, 0, stdout='failed\n')
            if command == restart:
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0, stdout='')

        with patch.dict(os.environ):
            os.environ.pop('WAYLAND_DISPLAY', None)
            with patch.object(screen_share, 'remember_served_config',
                              wraps=screen_share.remember_served_config) as remember:
                with patch.object(screen_share.subprocess, 'run', side_effect=fail_restart):
                    with redirect_stdout(io.StringIO()):
                        with self.assertRaises(subprocess.CalledProcessError):
                            screen_share.configure()
        remember.assert_not_called()
        self.assertFalse(self.marker.exists())
        self.assertEqual(stamp.read_text(), current)

    def test_restart_record_is_written_before_helper_or_unit_bytes_change(self):
        _config, unit = self._seed_matching_files()
        binary = self.home / '.local/bin/groundwork-screen-share'
        binary.write_bytes(b'previous-helper')
        previous_unit = screen_share.UNIT.replace('RestartSec=10', 'RestartSec=11')
        unit.write_text(previous_unit)

        def refuse():
            self.assertEqual(binary.read_bytes(), b'previous-helper')
            self.assertEqual(unit.read_text(), previous_unit)
            raise OSError('restart record unavailable')

        with patch.object(screen_share, 'mark_restart_needed', side_effect=refuse):
            with patch.object(screen_share.subprocess, 'run') as run:
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(OSError):
                        screen_share.configure()
        self.assertEqual(binary.read_bytes(), b'previous-helper')
        self.assertEqual(unit.read_text(), previous_unit)
        run.assert_not_called()

    def test_user_runtime_stamp_is_a_config_change_without_xdg_runtime_dir(self):
        config, _unit = self._seed_matching_files()
        user_runtime = self.home / 'user-runtime'
        stamp = user_runtime / 'groundwork-screen-share' / 'config.sha256'
        stamp.parent.mkdir(parents=True)
        stamp.write_text(hashlib.sha256(screen_share.config_text('ijklmnop').encode()).hexdigest() + '\n')
        restart = ['systemctl', '--user', 'restart', 'groundwork-screen-share.service']
        with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'fixture-wayland'}):
            os.environ.pop('XDG_RUNTIME_DIR', None)
            with patch.object(screen_share, 'user_runtime_root', return_value=user_runtime):
                with patch.object(screen_share.subprocess, 'run',
                                  return_value=subprocess.CompletedProcess([], 0)) as run:
                    with redirect_stdout(io.StringIO()) as out:
                        screen_share.configure()
        self.assertIn(restart, self._commands(run))
        self.assertIn('Restarting screen sharing; an active remote session will drop.', out.getvalue())
        self.assertEqual(config.read_text(), screen_share.config_text('abcdefgh'))
        self.assertFalse((user_runtime / 'groundwork-screen-share' / 'restart-needed').exists())

    def test_missing_user_runtime_stamp_is_not_a_config_change(self):
        self._seed_matching_files()
        user_runtime = self.home / 'user-runtime'
        user_runtime.mkdir()
        with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'fixture-wayland'}):
            os.environ.pop('XDG_RUNTIME_DIR', None)
            with patch.object(screen_share, 'user_runtime_root', return_value=user_runtime):
                stamp = screen_share.config_stamp_path()
                self.assertEqual(stamp, user_runtime / 'groundwork-screen-share' / 'config.sha256')
                self.assertFalse(stamp.exists())
                self.assertFalse(screen_share.served_config_changed(screen_share.config_path()))
                with patch.object(screen_share.subprocess, 'run',
                                  return_value=subprocess.CompletedProcess([], 0)) as run:
                    with redirect_stdout(io.StringIO()) as out:
                        screen_share.configure()
        self.assertNotIn(['systemctl', '--user', 'restart', 'groundwork-screen-share.service'],
                         self._commands(run))
        self.assertNotIn('will drop', out.getvalue())
        self.assertFalse((user_runtime / 'groundwork-screen-share' / 'config.sha256').exists())

    def test_state_file_marker_is_used_when_no_runtime_dir_is_writable(self):
        blocked = self.home / 'norun'
        blocked.mkdir()
        blocked.chmod(0)
        self.addCleanup(blocked.chmod, 0o700)
        xdg = self.home / 'xdg-file'
        xdg.write_text('not a directory')
        marker = self.home / '.local/state/groundwork/screen-share-restart-needed'

        def fail_reload(command, **kwargs):
            if command == ['systemctl', '--user', 'daemon-reload']:
                self.assertEqual(marker.read_text(), 'pending\n')
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0)

        with patch.dict(os.environ, {'XDG_RUNTIME_DIR': str(xdg)}):
            os.environ.pop('WAYLAND_DISPLAY', None)
            with patch.object(screen_share, 'user_runtime_root', return_value=blocked / '1000'):
                self.assertIsNone(screen_share.config_stamp_path())
                self.assertFalse(screen_share.served_config_changed(screen_share.config_path()))
                with patch.object(screen_share.subprocess, 'run', side_effect=fail_reload):
                    with redirect_stdout(io.StringIO()):
                        with self.assertRaises(subprocess.CalledProcessError):
                            screen_share.configure()
        self.assertEqual(marker.read_text(), 'pending\n')
        self.assertEqual(marker.stat().st_mode & 0o777, 0o600)

    def test_second_apply_after_a_successful_restart_leaves_the_service_running(self):
        config, _unit = self._seed_matching_files()
        # The previous process stamped other bytes and has not bound the new file.
        screen_share.remember_served_config(screen_share.config_text('ijklmnop').encode())
        fallback = self.home / '.local/state/groundwork/screen-share-restart-needed'
        fallback.parent.mkdir(parents=True)
        fallback.write_text('pending\n')
        restart = ['systemctl', '--user', 'restart', 'groundwork-screen-share.service']
        stamp = self.runtime / 'groundwork-screen-share' / 'config.sha256'
        with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'fixture-wayland'}):
            with patch.object(screen_share.subprocess, 'run',
                              return_value=subprocess.CompletedProcess([], 0)) as first:
                with redirect_stdout(io.StringIO()) as first_out:
                    screen_share.configure()
            self.assertIn(restart, self._commands(first))
            self.assertIn('Restarting screen sharing; an active remote session will drop.',
                          first_out.getvalue())
            self.assertEqual(stamp.read_text(), hashlib.sha256(config.read_bytes()).hexdigest() + '\n')
            self.assertFalse(self.marker.exists())
            self.assertFalse((self.runtime / 'groundwork-screen-share' / 'restart-needed').exists())
            with patch.object(screen_share.subprocess, 'run',
                              return_value=subprocess.CompletedProcess([], 0)) as second:
                with redirect_stdout(io.StringIO()) as second_out:
                    screen_share.configure()
        self.assertNotIn(restart, self._commands(second))
        self.assertNotIn('will drop', second_out.getvalue())

    def test_inactive_apply_records_the_stamp_and_clears_the_home_record(self):
        config, unit = self._seed_matching_files()
        screen_share.remember_served_config(screen_share.config_text('ijklmnop').encode())
        unit.write_text(screen_share.UNIT.replace('RestartSec=10', 'RestartSec=11'))
        self.marker.parent.mkdir(parents=True)
        self.marker.write_text('pending\n')
        restart = ['systemctl', '--user', 'restart', 'groundwork-screen-share.service']
        stamp = self.runtime / 'groundwork-screen-share' / 'config.sha256'

        def inactive(command, **kwargs):
            if command == SHOW_ACTIVE_STATE:
                return subprocess.CompletedProcess(command, 0, stdout='inactive\n')
            return subprocess.CompletedProcess(command, 0, stdout='')

        with patch.dict(os.environ):
            os.environ.pop('WAYLAND_DISPLAY', None)
            with patch.object(screen_share.subprocess, 'run', side_effect=inactive) as run:
                with redirect_stdout(io.StringIO()) as out:
                    screen_share.configure()
        self.assertNotIn(restart, self._commands(run))
        self.assertNotIn('will drop', out.getvalue())
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.runtime / 'groundwork-screen-share' / 'restart-needed').exists())
        self.assertEqual(stamp.read_text(), hashlib.sha256(config.read_bytes()).hexdigest() + '\n')
        blocked = self.home / 'norun'
        blocked.mkdir()
        blocked.chmod(0)
        self.addCleanup(blocked.chmod, 0o700)
        xdg = self.home / 'xdg-file'
        xdg.write_text('not a directory')
        with patch.dict(os.environ, {'XDG_RUNTIME_DIR': str(xdg), 'WAYLAND_DISPLAY': 'fixture-wayland'}):
            with patch.object(screen_share, 'user_runtime_root', return_value=blocked / '1000'):
                self.assertFalse(screen_share.restart_is_pending())
                with patch.object(screen_share.subprocess, 'run',
                                  return_value=subprocess.CompletedProcess([], 0)) as later:
                    with redirect_stdout(io.StringIO()) as later_out:
                        screen_share.configure()
        self.assertNotIn(restart, self._commands(later))
        self.assertNotIn('will drop', later_out.getvalue())

    def test_stop_child_kills_when_the_wait_times_out(self):
        child = subprocess.Popen(
            [sys.executable, '-c',
             'import signal, sys, time\n'
             'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
             'sys.stdout.write("ready\\n"); sys.stdout.flush()\n'
             'time.sleep(30)\n'],
            stdout=subprocess.PIPE)
        self.addCleanup(child.stdout.close)
        self.addCleanup(lambda: child.poll() is None and child.kill())
        self.assertEqual(child.stdout.readline(), b'ready\n')
        screen_share.stop_child(child, wait=0.2)
        self.assertEqual(child.poll(), -signal.SIGKILL)

    def test_stop_child_exits_when_sigterm_arrives_during_the_wait(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        self.addCleanup(lambda: child.poll() is None and child.kill())
        previous = signal.signal(signal.SIGTERM, lambda *_: None)
        original_wait = child.wait

        def wait_and_signal(timeout=None):
            os.kill(os.getpid(), signal.SIGTERM)
            return original_wait(timeout=timeout)

        try:
            with patch.object(child, 'wait', side_effect=wait_and_signal):
                with self.assertRaises(SystemExit) as caught:
                    screen_share.stop_child(child, wait=2)
            self.assertEqual(caught.exception.code, 0)
        finally:
            signal.signal(signal.SIGTERM, previous)
        self.assertIsNotNone(child.poll())

    def test_modified_config_is_preserved_and_service_not_started(self):
        config = screen_share.config_path()
        config.parent.mkdir(parents=True)
        config.write_text('enable_auth=false\n')
        with patch.object(screen_share.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'Preserved modified'):
                screen_share.configure()
        self.assertEqual(config.read_text(), 'enable_auth=false\n')
        run.assert_not_called()

    @unittest.skipUnless(shutil.which('systemd-analyze'), 'systemd unit verifier requires Linux')
    def test_enabled_desktop_target_has_no_startup_cycle(self):
        unit = self.home / 'groundwork-screen-share.service'
        target = self.home / 'graphical-session.target'
        good = screen_share.UNIT.replace('"%h/.local/bin/groundwork-screen-share" serve', '/usr/bin/true')
        for should_pass in (True, False):
            target.write_text('[Unit]\nDescription=Isolated desktop transaction\nWants=groundwork-screen-share.service\n'
                              + ('' if should_pass else 'After=groundwork-screen-share.service\n'))
            unit.write_text(good)
            result = subprocess.run(
                ['systemd-analyze', '--user', 'verify', '--man=no', str(target), str(unit)],
                env=dict(os.environ, SYSTEMD_UNIT_PATH=str(self.home) + ':'),
                capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode == 0, should_pass, result.stdout + result.stderr)
            if not should_pass:
                self.assertIn('cycle', result.stderr.lower())

    def test_fifo_credentials_fail_without_waiting_for_a_writer(self):
        config = screen_share.config_path()
        config.parent.mkdir(parents=True)
        os.mkfifo(config)
        result = subprocess.run(['python3', str(SCRIPT), 'configure'],
                                env=dict(os.environ, HOME=str(self.home)),
                                capture_output=True, text=True, timeout=3)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unsafe screen-sharing credentials', result.stderr)

    def test_symlink_credentials_are_rejected_without_target_changes(self):
        target = self.home / 'unrelated'
        target.write_text('keep me\n')
        config = screen_share.config_path()
        config.parent.mkdir(parents=True)
        config.symlink_to(target)
        with self.assertRaises(OSError):
            screen_share.read_config(config)
        self.assertEqual(target.read_text(), 'keep me\n')

    def test_password_command_does_not_disclose_credentials_to_captured_logs(self):
        result = subprocess.run(['python3', str(SCRIPT), 'password'],
                                env=dict(os.environ, HOME=str(self.home)),
                                capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertIn('outside recorded logs', result.stderr)

    def test_only_online_tailscale_ipv4_is_accepted(self):
        for backend, online, addresses, expected in [
            ('Running', True, ['fd7a:115c:a1e0::1', '100.64.0.2'], '100.64.0.2'),
            ('NeedsLogin', True, ['100.64.0.2'], None),
            ('Running', False, ['100.64.0.2'], None),
            ('Running', True, ['0.0.0.0', '192.168.1.2'], None),
            ('Running', True, ['not-an-address'], None),
        ]:
            with self.subTest(backend=backend, online=online, addresses=addresses):
                value = {'BackendState': backend, 'Self': {'Online': online, 'TailscaleIPs': addresses}}
                result = subprocess.CompletedProcess([], 0, stdout=json.dumps(value))
                with patch.object(screen_share.subprocess, 'run', return_value=result):
                    self.assertEqual(screen_share.tailscale_address(), expected)
        with patch.object(screen_share.subprocess, 'run', side_effect=subprocess.TimeoutExpired('tailscale', 5)):
            self.assertIsNone(screen_share.tailscale_address())

    def test_listener_waits_for_login_stops_on_logout_and_rebinds_after_change(self):
        config = screen_share.config_path()
        config.parent.mkdir(parents=True)
        config.write_text(screen_share.config_text('abcdefgh'))
        child1, child2 = Mock(), Mock()
        child1.poll.return_value = child2.poll.return_value = None
        addresses = [None, '100.64.0.2', None, '100.64.0.3']
        with patch.dict(os.environ, WAYLAND_DISPLAY='fixture-wayland', XDG_RUNTIME_DIR=str(self.home)):
            with patch.object(screen_share, 'tailscale_address', side_effect=addresses):
                with patch.object(screen_share, 'start_server', side_effect=[child1, child2]) as spawn:
                    with patch.object(screen_share.time, 'sleep', side_effect=[None, None, None, KeyboardInterrupt]):
                        with patch.object(screen_share.signal, 'signal'):
                            with self.assertRaises(KeyboardInterrupt):
                                screen_share.serve()
        self.assertEqual([call.args[0] for call in spawn.call_args_list], ['100.64.0.2', '100.64.0.3'])
        child1.terminate.assert_called_once()
        child2.terminate.assert_called_once()

    def test_inherited_listener_is_bound_to_tailscale_interface_before_start(self):
        config = screen_share.config_path()
        config.parent.mkdir(parents=True)
        config.write_text(screen_share.config_text('abcdefgh'))
        listener = Mock()
        listener.fileno.return_value = 42
        with patch.object(screen_share.socket, 'socket') as create:
            create.return_value.__enter__.return_value = listener
            with patch.object(screen_share, 'tailscale_address', return_value='100.64.0.2'):
                with patch.object(screen_share.subprocess, 'Popen') as spawn:
                    screen_share.start_server('100.64.0.2', self.home)
        self.assertEqual(listener.setsockopt.call_args_list, [
            call(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1),
            call(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b'tailscale0\0'),
        ])
        listener.bind.assert_called_once_with(('100.64.0.2', 5900))
        self.assertEqual(spawn.call_args.args[0][-1], 'fd:42')
        self.assertEqual(spawn.call_args.kwargs['pass_fds'], (42,))
        self.assertFalse(any('password=' in arg for arg in spawn.call_args.args[0]))
        environment = spawn.call_args.kwargs['env']
        self.assertEqual(environment['XKB_CONFIG_EXTRA_PATH'], str(screen_share.xkb_root()))
        self.assertIn('groundwork:apple_vnc', environment['XKB_DEFAULT_OPTIONS'].split(','))
        self.assertNotIn('password=', ''.join(environment.values()))
        self.assertNotEqual(environment.get('XDG_CONFIG_HOME'), str(screen_share.xkb_root().parent))

    def test_apple_screen_sharing_keymap_is_private_to_wayvnc(self):
        with patch.dict(os.environ, {
            'XKB_DEFAULT_OPTIONS': 'compose:caps',
            'XKB_DEFAULT_RULES': 'evdev',
            'XKB_CONFIG_EXTRA_PATH': '/usr/share/X11/xkb',
            'XDG_CONFIG_HOME': str(self.home / 'real-config'),
        }, clear=False):
            environment = screen_share.wayvnc_environment()
        self.assertEqual(environment['XKB_DEFAULT_OPTIONS'], 'compose:caps,groundwork:apple_vnc')
        self.assertEqual(environment['XDG_CONFIG_HOME'], str(self.home / 'real-config'))
        self.assertEqual(environment['XKB_CONFIG_EXTRA_PATH'], str(screen_share.xkb_root()))
        root = screen_share.xkb_root()
        self.assertEqual(root.parent.stat().st_mode & 0o777, 0o700)
        rules = (root / 'rules/evdev').read_text()
        post = (root / 'rules/evdev.post').read_text()
        symbols = (root / 'symbols/groundwork').read_text()
        compat = (root / 'compat/groundwork').read_text()
        self.assertIn('! include %S/evdev', rules)
        self.assertEqual(rules.count('groundwork:apple_vnc = +groundwork(apple_vnc)'), 2)
        self.assertEqual(post, screen_share.XKB_OPTION_RULES)
        self.assertNotIn('! include', post)
        self.assertEqual(post.count('groundwork:apple_vnc = +groundwork(apple_vnc)'), 2)
        self.assertIn('key <LALT>', symbols)
        self.assertIn('key <LWIN>', symbols)
        self.assertIn('key <RWIN>', symbols)
        self.assertNotIn('<RALT>', symbols)
        self.assertIn('modifiers=Mod4', symbols)
        self.assertIn('modifiers=Mod1', symbols)
        self.assertIn('interpret Alt_R', compat)
        self.assertIn('interpret Meta_R', compat)
        self.assertNotIn('ISO_Level3_Shift', compat)
        self.assertNotIn('xkb_', screen_share.config_text('abcdefgh'))
        with patch.dict(os.environ, {'XKB_DEFAULT_OPTIONS': environment['XKB_DEFAULT_OPTIONS']}):
            self.assertEqual(screen_share.wayvnc_environment()['XKB_DEFAULT_OPTIONS'],
                             'compose:caps,groundwork:apple_vnc')

    def test_apple_modifier_swap_stays_on_for_every_value_except_zero(self):
        for value in (None, '', '1', 'false', '00'):
            with self.subTest(value=value):
                with patch.dict(os.environ, {'XKB_DEFAULT_OPTIONS': 'compose:caps'}):
                    os.environ.pop('GROUNDWORK_SCREEN_SHARE_APPLE_MODIFIERS', None)
                    if value is not None:
                        os.environ['GROUNDWORK_SCREEN_SHARE_APPLE_MODIFIERS'] = value
                    environment = screen_share.wayvnc_environment()
                self.assertEqual(environment['XKB_DEFAULT_OPTIONS'],
                                 'compose:caps,groundwork:apple_vnc')
                self.assertEqual(environment['XKB_CONFIG_EXTRA_PATH'], str(screen_share.xkb_root()))

    def test_apple_modifier_opt_out_skips_appending_the_option(self):
        with patch.dict(os.environ, {
            'GROUNDWORK_SCREEN_SHARE_APPLE_MODIFIERS': '0',
            'XKB_DEFAULT_OPTIONS': 'compose:caps',
            'XKB_CONFIG_EXTRA_PATH': '/usr/share/X11/xkb:/tmp/other',
        }):
            environment = screen_share.wayvnc_environment()
        self.assertEqual(environment['XKB_DEFAULT_OPTIONS'], 'compose:caps')
        self.assertNotIn('groundwork:apple_vnc', environment['XKB_DEFAULT_OPTIONS'].split(','))
        self.assertEqual(environment['XKB_CONFIG_EXTRA_PATH'], str(screen_share.xkb_root()))
        self.assertEqual(environment['GROUNDWORK_SCREEN_SHARE_APPLE_MODIFIERS'], '0')
        with patch.dict(os.environ, {'GROUNDWORK_SCREEN_SHARE_APPLE_MODIFIERS': '0',
                                      'XKB_DEFAULT_OPTIONS': 'compose:caps,groundwork:apple_vnc'}):
            kept = screen_share.wayvnc_environment()
        self.assertEqual(kept['XKB_DEFAULT_OPTIONS'], 'compose:caps,groundwork:apple_vnc')
        with patch.dict(os.environ, {'GROUNDWORK_SCREEN_SHARE_APPLE_MODIFIERS': '0'}):
            os.environ.pop('XKB_DEFAULT_OPTIONS', None)
            absent = screen_share.wayvnc_environment()
        self.assertNotIn('XKB_DEFAULT_OPTIONS', absent)

    @unittest.skipUnless(_load_xkbcommon(), 'libxkbcommon is required to compile the WayVNC keymap')
    def test_compiled_wayvnc_keymap_swaps_command_and_option_modifiers(self):
        screen_share.install_vnc_keymap()
        library = _load_xkbcommon()
        with patch.dict(os.environ, {
            'XKB_CONFIG_EXTRA_PATH': str(screen_share.xkb_root()),
            'XKB_DEFAULT_RULES': 'evdev',
        }):
            stock = _compile_keymap(library, layout=None, options=b'')
            swapped = _compile_keymap(library, layout=None, options=b'groundwork:apple_vnc')
            german = _compile_keymap(library, layout=b'de', options=b'groundwork:apple_vnc')
        self.assertEqual(_mods(library, stock, ['Alt_L']), 0x08)
        self.assertEqual(_mods(library, stock, ['Super_L']), 0x40)
        self.assertEqual(_mods(library, swapped, ['Alt_L']), 0x40)
        self.assertEqual(_mods(library, swapped, ['Super_L']), 0x08)
        self.assertEqual(_mods(library, swapped, ['Super_R']), 0x08)
        self.assertEqual(_mods(library, swapped, ['Alt_R']), 0x40)
        self.assertEqual(_mods(library, swapped, ['Shift_L', 'Alt_L', '1']), 0x41)
        self.assertEqual(_mods(library, swapped, ['Shift_L', 'Alt_R', '1']), 0x41)
        self.assertEqual(_mods(library, swapped, ['Control_L', 'Alt_L', 'z']), 0x44)
        self.assertEqual(_mods(library, swapped, ['Alt_L', 'Control_L', 'Super_L', 'z']), 0x4c)
        self.assertEqual(_mods(library, swapped, ['Alt_L', 'minus']), 0x40)
        self.assertEqual(_mods(library, swapped, ['Shift_L', 'Alt_L', 'equal']), 0x41)
        self.assertEqual(_mods(library, swapped, ['Shift_L']), 0x01)
        self.assertEqual(_mods(library, swapped, ['Control_L']), 0x04)
        if german:
            self.assertEqual(_mods(library, german, ['Alt_L']), 0x40)
            self.assertEqual(_named_key_mods(library, german, b'RALT'), 0x80)

    @unittest.skipUnless(_load_xkbcommon(), 'libxkbcommon is required to compile the WayVNC keymap')
    def test_compiled_keymap_uses_wayvnc_environment_when_extra_path_is_preset(self):
        library = _load_xkbcommon()
        with patch.dict(os.environ, {
            'HOME': str(self.home),
            'XKB_CONFIG_EXTRA_PATH': '/usr/share/X11/xkb:' + str(self.home / 'other-xkb'),
            'XKB_DEFAULT_RULES': 'evdev',
        }):
            os.environ.pop('XDG_CONFIG_HOME', None)
            os.environ.pop('XKB_DEFAULT_OPTIONS', None)
            environment = screen_share.wayvnc_environment()
        self.assertEqual(environment['XKB_CONFIG_EXTRA_PATH'], str(screen_share.xkb_root()))
        self.assertIn('groundwork:apple_vnc', environment['XKB_DEFAULT_OPTIONS'].split(','))
        with patch.dict(os.environ, environment, clear=True):
            keymap = _compile_keymap(library, layout=None, options=None)
        self.assertTrue(keymap)
        self.assertEqual(_mods(library, keymap, ['Alt_L']), 0x40)
        library.xkb_keymap_unref(keymap)

    @unittest.skipUnless(_load_xkbcommon(), 'libxkbcommon is required to compile the WayVNC keymap')
    def test_compiled_keymap_keeps_alt_swap_when_user_evdev_rules_are_present(self):
        library = _load_xkbcommon()
        if not _evdev_post_is_honored(library):
            self.skipTest('rules/evdev.post composition requires libxkbcommon 1.13 or newer')
        # The stub extends the system rules and does not map groundwork:apple_vnc,
        # so it would drop the swap if it won the main-file lookup and evdev.post
        # were ignored. groundwork:user_stub changes Control so a keymap that
        # never read this file cannot satisfy the assertion.
        xkb = self.home / '.config' / 'xkb'
        (xkb / 'rules').mkdir(parents=True)
        (xkb / 'symbols').mkdir()
        (xkb / 'rules' / 'evdev').write_text(
            '! include %S/evdev\n'
            '\n'
            '! option = symbols\n'
            '  groundwork:user_stub = +userstub(marker)\n')
        (xkb / 'symbols' / 'userstub').write_text(
            'partial modifier_keys\n'
            'xkb_symbols "marker" {\n'
            '    replace key <LCTL> {\n'
            '        type[Group1] = "ONE_LEVEL",\n'
            '        symbols[Group1] = [ Control_L ],\n'
            '        actions[Group1] = [ SetMods(modifiers=Mod4,clearLocks) ]\n'
            '    };\n'
            '};\n')
        with patch.dict(os.environ, {
            'HOME': str(self.home),
            'XKB_DEFAULT_OPTIONS': 'groundwork:user_stub',
            'XKB_CONFIG_EXTRA_PATH': '/usr/share/X11/xkb:/tmp/other',
            'XKB_DEFAULT_RULES': 'evdev',
        }):
            os.environ.pop('XDG_CONFIG_HOME', None)
            environment = screen_share.wayvnc_environment()
        self.assertEqual(environment['XKB_DEFAULT_OPTIONS'],
                         'groundwork:user_stub,groundwork:apple_vnc')
        with patch.dict(os.environ, environment, clear=True):
            keymap = _compile_keymap(library, layout=None, options=None)
        self.assertTrue(keymap)
        self.assertEqual(_mods(library, keymap, ['Control_L']), 0x40)
        self.assertEqual(_mods(library, keymap, ['Alt_L']), 0x40)
        library.xkb_keymap_unref(keymap)

    @unittest.skipUnless(_load_xkbcommon(), 'libxkbcommon is required to compile the WayVNC keymap')
    def test_compiled_keymap_leaves_alt_unswapped_when_apple_modifiers_are_off(self):
        library = _load_xkbcommon()
        with patch.dict(os.environ, {
            'HOME': str(self.home),
            'GROUNDWORK_SCREEN_SHARE_APPLE_MODIFIERS': '0',
            'XKB_DEFAULT_OPTIONS': 'compose:caps',
            'XKB_CONFIG_EXTRA_PATH': '/usr/share/X11/xkb',
            'XKB_DEFAULT_RULES': 'evdev',
        }):
            os.environ.pop('XDG_CONFIG_HOME', None)
            environment = screen_share.wayvnc_environment()
        self.assertEqual(environment['XKB_DEFAULT_OPTIONS'], 'compose:caps')
        with patch.dict(os.environ, environment, clear=True):
            keymap = _compile_keymap(library, layout=None, options=None)
        self.assertTrue(keymap)
        self.assertEqual(_mods(library, keymap, ['Alt_L']), 0x08)
        library.xkb_keymap_unref(keymap)

    def test_bind_retry_drops_a_stale_tailscale_address_without_starting_wayvnc(self):
        config = screen_share.config_path()
        config.parent.mkdir(parents=True)
        config.write_text(screen_share.config_text('abcdefgh'))
        listener = Mock()
        listener.fileno.return_value = 42
        listener.bind.side_effect = OSError(errno.EADDRINUSE, 'in use')
        answers = iter(['100.64.0.2', '100.64.0.3'])

        with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'fixture-wayland', 'XDG_RUNTIME_DIR': str(self.runtime)}):
            with patch.object(screen_share.socket, 'socket') as create:
                create.return_value.__enter__.return_value = listener
                with patch.object(screen_share, 'tailscale_address', side_effect=lambda: next(answers)):
                    with patch.object(screen_share.subprocess, 'Popen') as spawn:
                        with patch.object(screen_share.time, 'sleep', side_effect=KeyboardInterrupt):
                            with patch.object(screen_share.signal, 'signal'):
                                with redirect_stderr(io.StringIO()):
                                    with self.assertRaises(KeyboardInterrupt):
                                        screen_share.serve()
        spawn.assert_not_called()
        listener.close.assert_called()
        listener.bind.assert_called_once_with(('100.64.0.2', 5900))

    def test_address_change_after_bind_does_not_start_wayvnc(self):
        config = screen_share.config_path()
        config.parent.mkdir(parents=True)
        config.write_text(screen_share.config_text('abcdefgh'))
        listener = Mock()
        listener.fileno.return_value = 42
        with patch.object(screen_share.socket, 'socket') as create:
            create.return_value.__enter__.return_value = listener
            with patch.object(screen_share, 'tailscale_address', return_value='100.64.0.3'):
                with patch.object(screen_share.subprocess, 'Popen') as spawn:
                    started = screen_share.start_server('100.64.0.2', self.runtime)
        self.assertIsNone(started)
        spawn.assert_not_called()
        listener.bind.assert_called_once_with(('100.64.0.2', 5900))
        listener.close.assert_called()

    def test_unavailable_interface_binding_never_starts_a_fallback_listener(self):
        listener = Mock()

        def setsockopt(_level, option, _value):
            if option == socket.SO_BINDTODEVICE:
                raise PermissionError('interface isolation unavailable')

        listener.setsockopt.side_effect = setsockopt
        with patch.object(screen_share.socket, 'socket') as create:
            create.return_value.__enter__.return_value = listener
            with patch.object(screen_share.subprocess, 'Popen') as spawn:
                with self.assertRaises(PermissionError):
                    screen_share.start_server('100.64.0.2', self.home)
        listener.bind.assert_not_called()
        spawn.assert_not_called()

    @unittest.skipUnless(sys.platform == 'linux' and hasattr(socket, 'SO_BINDTODEVICE'),
                         'Linux socket interface isolation')
    def test_kernel_rejects_ingress_from_a_different_interface(self):
        others = [name for _, name in socket.if_nameindex() if name != 'lo']
        if not others:
            self.skipTest('a non-loopback interface is required for the negative ingress test')
        # Each case owns its socket and ephemeral port. Loopback succeeds only
        # when it is the permitted ingress interface; no Tailscale login needed.
        for interface, allowed in [('lo', True), (others[0], False)]:
            with socket.socket() as listener, socket.socket() as client:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode() + b'\0')
                listener.bind(('127.0.0.1', 0))
                listener.listen(1)
                client.settimeout(0.3)
                self.assertEqual(client.connect_ex(listener.getsockname()) == 0, allowed)

    @unittest.skipUnless(sys.platform == 'linux' and hasattr(socket, 'SO_BINDTODEVICE'),
                         'Linux socket interface isolation')
    def test_restart_rebinds_while_previous_connection_is_in_time_wait(self):
        previous = socket.socket()
        self.addCleanup(previous.close)
        previous.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        previous.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b'lo\0')
        previous.bind(('127.0.0.1', 0))
        port = previous.getsockname()[1]
        previous.listen(1)
        client = socket.socket()
        self.addCleanup(client.close)
        client.connect(('127.0.0.1', port))
        accepted, _ = previous.accept()
        self.addCleanup(accepted.close)
        accepted.close()
        client.close()
        previous.close()
        self.assertIn('06', _tcp_states('127.0.0.1', port))
        listener = socket.socket()
        self.addCleanup(listener.close)
        screen_share.bind_listener(listener, '127.0.0.1', port=port, device=b'lo', wait=2)
        self.assertEqual(listener.getsockname(), ('127.0.0.1', port))

    @unittest.skipUnless(sys.platform == 'linux' and hasattr(socket, 'SO_BINDTODEVICE'),
                         'Linux socket interface isolation')
    def test_bind_waits_for_the_previous_listener_instead_of_raising_eaddrinuse(self):
        holder = socket.socket()
        self.addCleanup(holder.close)
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b'lo\0')
        holder.bind(('127.0.0.1', 0))
        port = holder.getsockname()[1]
        holder.listen(1)
        busy = socket.socket()
        self.addCleanup(busy.close)
        with self.assertRaises(OSError) as caught:
            screen_share.bind_listener(busy, '127.0.0.1', port=port, device=b'lo', wait=0)
        self.assertEqual(caught.exception.errno, errno.EADDRINUSE)

        def release():
            time.sleep(0.2)
            holder.close()

        thread = threading.Thread(target=release)
        thread.start()
        self.addCleanup(thread.join)
        listener = socket.socket()
        self.addCleanup(listener.close)
        with redirect_stderr(io.StringIO()) as err:
            with patch.object(screen_share, 'tailscale_address', return_value='127.0.0.1'):
                screen_share.bind_listener(listener, '127.0.0.1', port=port, device=b'lo', wait=5, pause=0.05)
        self.assertEqual(listener.getsockname(), ('127.0.0.1', port))
        self.assertIn('Screen sharing is waiting for TCP ' + str(port) + ' to be released.', err.getvalue())

    @unittest.skipUnless(sys.platform == 'linux' and hasattr(socket, 'SO_BINDTODEVICE'),
                         'Linux socket interface isolation')
    def test_bind_gives_up_when_the_port_stays_busy(self):
        holder = socket.socket()
        self.addCleanup(holder.close)
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b'lo\0')
        holder.bind(('127.0.0.1', 0))
        port = holder.getsockname()[1]
        holder.listen(1)
        listener = socket.socket()
        self.addCleanup(listener.close)
        started = time.monotonic()
        with redirect_stderr(io.StringIO()) as err:
            with patch.object(screen_share, 'tailscale_address', return_value='127.0.0.1'):
                with self.assertRaises(OSError) as caught:
                    screen_share.bind_listener(listener, '127.0.0.1', port=port, device=b'lo', wait=0.3, pause=0.05)
        self.assertEqual(caught.exception.errno, errno.EADDRINUSE)
        self.assertIn('to be released', err.getvalue())
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 0.3)
        self.assertLess(elapsed, 2)

    @unittest.skipUnless(sys.platform == 'linux' and hasattr(socket, 'SO_BINDTODEVICE'),
                         'Linux socket interface isolation')
    def test_time_wait_without_reuse_keeps_the_address_busy(self):
        previous = socket.socket()
        self.addCleanup(previous.close)
        previous.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b'lo\0')
        previous.bind(('127.0.0.1', 0))
        port = previous.getsockname()[1]
        previous.listen(1)
        client = socket.socket()
        self.addCleanup(client.close)
        client.connect(('127.0.0.1', port))
        accepted, _ = previous.accept()
        self.addCleanup(accepted.close)
        accepted.close()
        client.close()
        previous.close()
        self.assertIn('06', _tcp_states('127.0.0.1', port))
        listener = socket.socket()
        self.addCleanup(listener.close)
        with redirect_stderr(io.StringIO()):
            with patch.object(screen_share, 'tailscale_address', return_value='127.0.0.1'):
                with self.assertRaises(OSError) as caught:
                    screen_share.bind_listener(listener, '127.0.0.1', port=port, device=b'lo', wait=0.2, pause=0.05)
        self.assertEqual(caught.exception.errno, errno.EADDRINUSE)
