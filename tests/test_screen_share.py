"""Test private credentials and fail-closed Tailscale listener lifecycle."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/groundwork-screen-share.py'
spec = importlib.util.spec_from_file_location('screen_share', SCRIPT)
screen_share = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screen_share)


class ScreenShareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='groundwork-screen-share-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.home_patch = patch.object(Path, 'home', return_value=self.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)

    def test_configuration_is_private_idempotent_and_automatically_enabled(self):
        with patch.object(screen_share.subprocess, 'run') as run:
            with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'fixture-wayland'}):
                screen_share.configure()
                first = screen_share.config_path().read_bytes()
                screen_share.configure()
        config = screen_share.config_path()
        self.assertEqual(first, config.read_bytes())
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(config.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(len(screen_share.read_config(config)), 8)
        self.assertIn('enable_auth=true', config.read_text())
        unit = (self.home / '.config/systemd/user/groundwork-screen-share.service').read_text()
        self.assertIn('WantedBy=graphical-session.target', unit)
        self.assertIn('PartOf=graphical-session.target', unit)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(['systemctl', '--user', 'restart', 'groundwork-screen-share.service'], commands)

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
        listener = Mock()
        listener.fileno.return_value = 42
        with patch.object(screen_share.socket, 'socket') as create:
            create.return_value.__enter__.return_value = listener
            with patch.object(screen_share.subprocess, 'Popen') as spawn:
                screen_share.start_server('100.64.0.2', self.home)
        listener.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b'tailscale0\0')
        listener.bind.assert_called_once_with(('100.64.0.2', 5900))
        self.assertEqual(spawn.call_args.args[0][-1], 'fd:42')
        self.assertEqual(spawn.call_args.kwargs['pass_fds'], (42,))
        self.assertFalse(any('password=' in arg for arg in spawn.call_args.args[0]))

    def test_unavailable_interface_binding_never_starts_a_fallback_listener(self):
        listener = Mock()
        listener.setsockopt.side_effect = PermissionError('interface isolation unavailable')
        with patch.object(screen_share.socket, 'socket') as create:
            create.return_value.__enter__.return_value = listener
            with patch.object(screen_share.subprocess, 'Popen') as spawn:
                with self.assertRaises(PermissionError):
                    screen_share.start_server('100.64.0.2', self.home)
        listener.bind.assert_not_called()
        spawn.assert_not_called()

    @unittest.skipUnless(hasattr(socket, 'SO_BINDTODEVICE'), 'Linux socket interface isolation')
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
