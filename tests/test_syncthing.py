"""Test the Syncthing apply and pairing helper against a fake local Syncthing."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/groundwork-syncthing.py'
spec = importlib.util.spec_from_file_location('groundwork_syncthing', SCRIPT)
gs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gs)

API_KEY = 'fixture-api-key-not-real'
ME = 'AAAAAAA-BBBBBBB-CCCCCCC-DDDDDDD-EEEEEEE-FFFFFFF-GGGGGGG-AAAAAAA'
PEER = 'HHHHHHH-IIIIIII-JJJJJJJ-KKKKKKK-LLLLLLL-MMMMMMM-NNNNNNN-OOOOOOO'
V4 = '100.101.102.103'
V6 = 'fd7a:115c:a1e0::1234'

CONFIG_XML = '''<configuration version="51">
    <device id="%s" name="mini" compression="metadata"></device>
    <gui enabled="true" tls="false" debugging="false">
        <address>127.0.0.1:8384</address>
        <apikey>%s</apikey>
    </gui>
    <options>
        <listenAddress>default</listenAddress>
        <globalAnnounceEnabled>true</globalAnnounceEnabled>
        <localAnnounceEnabled>true</localAnnounceEnabled>
        <relaysEnabled>true</relaysEnabled>
        <natEnabled>true</natEnabled>
        <urAccepted>-1</urAccepted>
    </options>
</configuration>
''' % (ME, API_KEY)

SYNCTHING = '''#!/bin/bash
case "$1" in
  --version) echo "${FAKE_SYNCTHING_VERSION:-syncthing v2.1.7 \\"Hafnium Hornet\\" (go1.25 linux-arm64)}" ;;
  device-id) echo "%(me)s" ;;
  generate)
    [ "${STNODEFAULTFOLDER:-}" = 1 ] || { echo "generate would create ~/Sync" >&2; exit 1; }
    mkdir -p "$XDG_STATE_HOME/syncthing"
    cp "$FAKE_CONFIG_TEMPLATE" "$XDG_STATE_HOME/syncthing/config.xml"
    chmod 600 "$XDG_STATE_HOME/syncthing/config.xml"
    : > "$XDG_STATE_HOME/syncthing/key.pem"; chmod 600 "$XDG_STATE_HOME/syncthing/key.pem"
    ;;
esac
''' % {'me': ME}

SYSTEMCTL = '''#!/bin/bash
echo "$*" >> "$FAKE_STATE/systemctl.log"
shift  # --user
case "$1" in
  is-active) [ -e "$FAKE_STATE/active" ] ;;
  enable) touch "$FAKE_STATE/active" ;;
  show-environment) cat "$FAKE_STATE/manager-env" 2>/dev/null; exit 0 ;;
  show) cat "$FAKE_STATE/unit-show" 2>/dev/null; exit 0 ;;
esac
'''

TAILSCALE = '''#!/bin/bash
case "$2" in
  -4) [ -n "${FAKE_TS4:-}" ] && echo "$FAKE_TS4" || exit 1 ;;
  -6) [ -n "${FAKE_TS6:-}" ] && echo "$FAKE_TS6" || exit 1 ;;
esac
'''


class FakeSyncthing:
    """Just enough of the REST API; remembers every write."""

    def __init__(self, key=API_KEY):
        self.key = key
        self.folders = {}
        self.devices = {}
        self.options = {'listenAddresses': ['default'], 'relaysEnabled': True, 'globalAnnounceEnabled': True,
                        'localAnnounceEnabled': True, 'natEnabled': True}
        self.gui = {'address': '127.0.0.1:8384', 'user': 'jason', 'password': 'hash'}
        self.browse = []
        self.writes = []
        self.requests = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, status, body=None):
                data = json.dumps(body).encode() if body is not None else b''
                self.send_response(status)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def handle_any(self, method):
                path = self.path
                length = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                if self.headers.get('X-API-Key') != fake.key:
                    return self.respond(403)
                fake.requests.append((method, path, body))
                if method != 'GET':
                    fake.writes.append((method, path, body))
                status, payload = fake.route(method, path, body)
                self.respond(status, payload)

            def do_GET(self):
                self.handle_any('GET')

            def do_POST(self):
                self.handle_any('POST')

            def do_PUT(self):
                self.handle_any('PUT')

            def do_PATCH(self):
                self.handle_any('PATCH')

        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.address = '127.0.0.1:%d' % self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def load_options(self, path):
        options = gs.ET.parse(path).getroot().find('options')
        self.options['listenAddresses'] = [e.text for e in options.findall('listenAddress')]
        for name in gs.OPTION_FLAGS:
            self.options[name] = options.findtext(name) == 'true'

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def route(self, method, path, body):
        if path == '/rest/system/status':
            return 200, {'myID': ME, 'connectionServiceStatus': {url: {'error': None} for url in self.options['listenAddresses']}}
        if path == '/rest/config/defaults/folder':
            return 200, {'id': '', 'label': '', 'path': '~', 'type': 'sendreceive', 'paused': False, 'devices': [],
                         'versioning': {'type': '', 'params': {}, 'cleanupIntervalS': 3600, 'fsPath': '', 'fsType': 'basic'}}
        if path == '/rest/config/defaults/device':
            return 200, {'deviceID': '', 'name': '', 'addresses': ['dynamic'], 'allowedNetworks': [],
                         'introducedBy': '', 'encryptionPassword': '', 'paused': False}
        if path == '/rest/config/options':
            if method == 'PATCH':
                self.options.update(body)
                return 200, None
            return 200, self.options
        if path == '/rest/config/gui':
            if method == 'PATCH':
                self.gui.update(body)
                return 200, None
            return 200, self.gui
        if path.startswith('/rest/db/browse'):
            return 200, self.browse
        if path.startswith('/rest/system/reset'):
            self.browse = []
            return 200, None
        for kind, store, key in (('folders', self.folders, 'id'), ('devices', self.devices, 'deviceID')):
            base = '/rest/config/' + kind
            if path == base and method == 'POST':
                store[body[key]] = body
                return 200, None
            if path.startswith(base + '/'):
                item = path[len(base) + 1:]
                if method == 'GET':
                    return (200, store[item]) if item in store else (404, None)
                if method == 'PUT':
                    store[item] = body
                    return 200, None
                if method == 'PATCH':
                    store[item] = self.merge(store[item], body)
                    return 200, None
        return 404, None

    @staticmethod
    def merge(base, patch_body):
        result = dict(base)
        for key, value in patch_body.items():
            result[key] = FakeSyncthing.merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
        return result


class SyncthingCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='groundwork-syncthing-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / 'home'
        self.state = self.root / 'fake-state'
        self.bin = self.root / 'bin'
        for path in (self.home, self.state, self.bin):
            path.mkdir()
        for name, text in (('syncthing', SYNCTHING), ('systemctl', SYSTEMCTL), ('tailscale', TAILSCALE)):
            (self.bin / name).write_text(text)
            (self.bin / name).chmod(0o755)
        (self.root / 'config.xml').write_text(CONFIG_XML)
        self.server = FakeSyncthing()
        self.addCleanup(self.server.close)
        env = {'HOME': str(self.home), 'XDG_STATE_HOME': str(self.home / '.local/state'),
               'XDG_CONFIG_HOME': str(self.home / '.config'), 'XDG_RUNTIME_DIR': str(self.root),
               'PATH': f'{self.bin}:/usr/bin:/bin', 'FAKE_STATE': str(self.state),
               'FAKE_CONFIG_TEMPLATE': str(self.root / 'config.xml'), 'FAKE_TS4': V4}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop('STGUIADDRESS', None)
        for target, value in (('GUI_ADDRESS', self.server.address), ('listening_on_gui', lambda: True)):
            patch.object(gs, target, value).start()
        original = gs.systemctl

        def systemctl(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[:1] == ('enable',):
                # The started process reads the options the helper wrote.
                self.server.load_options(self.config)
            return result
        patch.object(gs, 'systemctl', systemctl).start()
        self.addCleanup(patch.stopall)
        self.config = self.home / '.local/state/syncthing/config.xml'

    def systemctl_log(self):
        log = self.state / 'systemctl.log'
        return log.read_text().splitlines() if log.exists() else []

    def seed_config(self, text=CONFIG_XML):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(text.replace('127.0.0.1:8384', self.server.address))
        self.config.chmod(0o600)
        (self.config.parent / 'key.pem').write_text('')
        (self.config.parent / 'key.pem').chmod(0o600)

    def mark_active(self):
        (self.state / 'active').touch()

    def run_apply(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gs.main(['apply'])
        return code, out.getvalue() + err.getvalue()


class VersionAndAddressTests(SyncthingCase):
    def test_versions_older_than_2_1_2_are_refused(self):
        for text in ('syncthing v2.1.1 "x"', 'syncthing v1.30.0 "x"'):
            with self.assertRaises(gs.SyncthingError):
                gs.require_supported(gs.parse_version(text))
        gs.require_supported(gs.parse_version('syncthing v2.1.2 "x"'))
        gs.require_supported(gs.parse_version('syncthing v2.10.0 "x"'))

    def test_listen_list_has_loopback_and_tailnet_only(self):
        self.assertEqual(gs.listen_addresses(V4, None), [
            'tcp://127.0.0.1:22000', f'tcp://{V4}:22000', f'quic://{V4}:22000'])
        self.assertEqual(gs.listen_addresses(V4, V6)[3:], [f'tcp://[{V6}]:22000', f'quic://[{V6}]:22000'])

    def test_non_tailnet_addresses_are_not_accepted(self):
        os.environ['FAKE_TS4'] = '192.168.1.5'
        with self.assertRaises(gs.Waiting):
            gs.tailscale_addresses()
        del os.environ['FAKE_TS4']
        with self.assertRaises(gs.Waiting):
            gs.tailscale_addresses()
        os.environ.update(FAKE_TS4=V4, FAKE_TS6='2001:db8::1')
        self.assertEqual(gs.tailscale_addresses(), (V4, None))

    def test_config_directory_prefers_the_new_path(self):
        self.assertEqual(gs.config_dir(), self.home / '.local/state/syncthing')
        old = self.home / '.config/syncthing'
        old.mkdir(parents=True)
        (old / 'config.xml').write_text('<configuration/>')
        self.assertEqual(gs.config_dir(), old)
        self.seed_config()
        self.assertEqual(gs.config_dir(), self.home / '.local/state/syncthing')


class FileTests(SyncthingCase):
    def test_parent_ignores_freetube_first_and_keeps_other_lines(self):
        shared = self.home / 'Shared'
        shared.mkdir()
        (shared / '.stignore').write_text('!keep\n/FreeTube\n*.tmp\n')
        gs.ensure_directories()
        self.assertEqual((shared / '.stignore').read_text(), '/FreeTube\n!keep\n*.tmp\n')
        self.assertEqual((shared / 'FreeTube/.stignore').read_text(), '*~\n')
        before = (shared / '.stignore').stat().st_mtime_ns
        gs.ensure_directories()
        self.assertEqual((shared / '.stignore').stat().st_mtime_ns, before)

    def test_listen_edit_replaces_default_and_disables_the_four_flags(self):
        self.seed_config()
        addresses = gs.listen_addresses(V4, V6)
        self.assertTrue(gs.edit_options_xml(self.config, addresses))
        text = self.config.read_text()
        self.assertNotIn('>default<', text)
        self.assertEqual(text.count('<listenAddress>'), 5)
        for flag in gs.OPTION_FLAGS:
            self.assertIn(f'<{flag}>false</{flag}>', text)
        self.assertIn('<urAccepted>-1</urAccepted>', text)
        self.assertEqual(oct(self.config.stat().st_mode & 0o777), '0o600')
        self.assertFalse(gs.edit_options_xml(self.config, addresses))

    def test_dropin_sets_stnodefaultfolder_once(self):
        self.assertTrue(gs.ensure_dropin())
        self.assertFalse(gs.ensure_dropin())
        text = gs.dropin_path().read_text()
        self.assertIn('Environment=STNODEFAULTFOLDER=1', text)
        self.assertNotIn('STGUIADDRESS', text)


class ApplyTests(SyncthingCase):
    def test_fresh_machine_generates_binds_to_the_tailnet_then_starts(self):
        os.environ['FAKE_TS6'] = V6
        code, output = self.run_apply()
        self.assertEqual(code, 0, output)
        # The unit started only after the config carried the tailnet list.
        text = self.config.read_text()
        self.assertNotIn('>default<', text)
        self.assertIn(f'<listenAddress>quic://[{V6}]:22000</listenAddress>', text)
        self.assertIn('--user enable --now syncthing.service', self.systemctl_log())
        self.assertFalse((self.home / 'Sync').exists())
        folders = self.server.folders
        self.assertEqual(sorted(folders), ['groundwork-freetube', 'groundwork-shared'])
        self.assertEqual(folders['groundwork-shared']['path'], str(self.home / 'Shared'))
        self.assertEqual(folders['groundwork-shared']['label'], 'Shared')
        self.assertEqual(folders['groundwork-freetube']['path'], str(self.home / 'Shared/FreeTube'))
        self.assertEqual(folders['groundwork-freetube']['versioning']['type'], 'trashcan')
        self.assertEqual(folders['groundwork-freetube']['versioning']['params'], {'cleanoutDays': '0'})
        self.assertNotEqual(folders['groundwork-shared']['versioning'].get('type'), 'trashcan')
        self.assertEqual([entry['deviceID'] for entry in folders['groundwork-shared']['devices']], [ME])
        self.assertTrue(all(not folder['paused'] for folder in folders.values()))
        self.assertTrue((self.home / '.local/bin/groundwork-syncthing').is_file())
        self.assertIn('syncthing-pair', (self.home / '.local/bin/groundwork').read_text())
        self.assertNotIn(API_KEY, output)

    def test_tailscale_down_on_a_new_machine_starts_nothing(self):
        del os.environ['FAKE_TS4']
        code, output = self.run_apply()
        self.assertEqual(code, gs.EXIT_WAITING, output)
        self.assertEqual([line for line in self.systemctl_log() if 'enable' in line or 'daemon-reload' in line], [])
        self.assertFalse(self.config.exists())
        self.assertEqual(self.server.writes, [])

    def test_service_override_stops_before_starting(self):
        os.environ['STGUIADDRESS'] = '0.0.0.0:8384'
        with self.assertRaises(gs.SyncthingError):
            gs.apply()
        self.assertEqual([line for line in self.systemctl_log() if 'enable' in line], [])

    def test_unit_with_gui_address_flag_is_refused(self):
        (self.state / 'unit-show').write_text('ExecStart={ path=/usr/bin/syncthing ; argv[]=/usr/bin/syncthing serve --gui-address=0.0.0.0:8384 }\n')
        with self.assertRaises(gs.SyncthingError):
            gs.apply()
        self.assertEqual([line for line in self.systemctl_log() if 'enable' in line], [])

    def test_old_binary_is_refused_before_any_change(self):
        os.environ['FAKE_SYNCTHING_VERSION'] = 'syncthing v2.1.1 "x"'
        with self.assertRaises(gs.SyncthingError):
            gs.apply()
        self.assertFalse((self.home / '.local/bin').exists())
        self.assertFalse((self.home / 'Shared').exists())

    def test_serving_machine_gets_a_patch_and_one_restart_only_when_the_list_changed(self):
        self.seed_config()
        self.mark_active()
        self.server.options['listenAddresses'] = ['default']
        code, output = self.run_apply()
        self.assertEqual(code, 0, output)
        patches = [body for method, path, body in self.server.writes if path == '/rest/config/options']
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0]['listenAddresses'], gs.listen_addresses(V4, None))
        for flag in gs.OPTION_FLAGS:
            self.assertIs(patches[0][flag], False)
        self.assertEqual([line for line in self.systemctl_log() if line.startswith('--user restart')], ['--user restart syncthing.service'])
        self.server.writes.clear()
        (self.state / 'systemctl.log').unlink()
        code, output = self.run_apply()
        self.assertEqual(code, 0, output)
        self.assertEqual([w for w in self.server.writes if w[1].startswith('/rest/config/options')], [])
        self.assertEqual([line for line in self.systemctl_log() if 'restart' in line], [])

    def test_serving_machine_without_a_tailscale_address_keeps_its_list(self):
        self.seed_config()
        self.mark_active()
        kept = ['tcp://127.0.0.1:22000', 'tcp://100.1.1.1:22000']
        self.server.options.update(listenAddresses=kept, relaysEnabled=True)
        del os.environ['FAKE_TS4']
        code, output = self.run_apply()
        self.assertEqual(code, gs.EXIT_WAITING, output)
        patch_bodies = [body for method, path, body in self.server.writes if path == '/rest/config/options']
        self.assertEqual(patch_bodies, [{name: False for name in gs.OPTION_FLAGS}])
        self.assertEqual(self.server.options['listenAddresses'], kept)
        self.assertEqual([line for line in self.systemctl_log() if 'restart' in line], [])

    def test_gui_address_is_written_back_with_only_the_address(self):
        self.seed_config()
        self.mark_active()
        self.server.gui['address'] = '0.0.0.0:8384'
        self.run_apply()
        self.assertIn(('PATCH', '/rest/config/gui', {'address': gs.GUI_ADDRESS}), self.server.writes)
        self.assertEqual(self.server.gui['user'], 'jason')
        self.assertFalse([w for w in self.server.writes if w[0] == 'PUT' and w[1] == '/rest/config/gui'])

    def test_later_apply_leaves_existing_folders_alone_and_never_unpauses(self):
        self.seed_config()
        self.mark_active()
        self.run_apply()
        for folder in self.server.folders.values():
            folder['paused'] = True
        self.server.folders['groundwork-freetube']['type'] = 'receiveonly'
        self.server.writes.clear()
        code, output = self.run_apply()
        self.assertEqual(code, 0, output)
        self.assertEqual([w for w in self.server.writes if 'folders' in w[1]], [])
        self.assertTrue(all(f['paused'] for f in self.server.folders.values()))

    def test_freetube_versioning_is_restored_with_a_versioning_only_patch(self):
        self.seed_config()
        self.mark_active()
        self.run_apply()
        self.server.folders['groundwork-freetube']['versioning'] = {'type': '', 'params': {}}
        self.server.folders['groundwork-shared']['versioning'] = {'type': 'simple', 'params': {'keep': '5'}}
        self.server.writes.clear()
        self.run_apply()
        writes = [w for w in self.server.writes if 'folders' in w[1]]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][:2], ('PATCH', '/rest/config/folders/groundwork-freetube'))
        self.assertEqual(set(writes[0][2]), {'versioning'})
        self.assertEqual(self.server.folders['groundwork-shared']['versioning']['type'], 'simple')

    def test_a_moved_folder_is_reported_and_not_retargeted(self):
        self.seed_config()
        self.mark_active()
        self.run_apply()
        self.server.folders['groundwork-shared']['path'] = '/elsewhere'
        self.server.writes.clear()
        with self.assertRaises(gs.SyncthingError) as caught:
            gs.apply()
        self.assertIn('/elsewhere', str(caught.exception))
        self.assertEqual([w for w in self.server.writes if 'folders/groundwork-shared' in w[1]], [])

    def test_a_parent_that_already_indexed_freetube_is_rebuilt_without_other_devices(self):
        self.seed_config()
        self.mark_active()
        self.run_apply()
        self.server.folders['groundwork-shared']['devices'].append({'deviceID': PEER})
        self.server.browse = [{'name': 'history.db'}]
        self.server.writes.clear()
        code, output = self.run_apply()
        self.assertEqual(code, 0, output)
        order = [(m, p.split('?')[0]) for m, p, _ in self.server.writes]
        self.assertEqual(order.index(('PUT', '/rest/config/folders/groundwork-shared')) < order.index(('POST', '/rest/system/reset')), True)
        self.assertEqual([d['deviceID'] for d in self.server.folders['groundwork-shared']['devices']], [ME])
        self.assertTrue((self.home / 'Shared/.stignore').read_text().startswith('/FreeTube\n'))


class PairTests(SyncthingCase):
    def ready(self):
        self.seed_config()
        self.mark_active()
        self.run_apply()
        self.server.writes.clear()

    def add(self, **override):
        values = dict(device_id=PEER, name='macbook', magicdns='macbook.tailnet.example')
        values.update(override)
        with contextlib.redirect_stdout(io.StringIO()):
            return gs.pair_add(**values)

    def test_show_prints_only_the_device_id(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(gs.main(['pair', 'show']), 0)
        self.assertEqual(out.getvalue(), ME + '\n')

    def test_add_creates_the_device_and_shares_both_folders(self):
        self.ready()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(gs.pair_add(PEER, 'macbook', 'macbook.tailnet.example'), 0)
        device = self.server.devices[PEER]
        self.assertEqual(device['addresses'], ['tcp://macbook.tailnet.example:22000', 'quic://macbook.tailnet.example:22000'])
        self.assertEqual(device['allowedNetworks'], ['100.64.0.0/10', 'fd7a:115c:a1e0::/48'])
        self.assertEqual(device['name'], 'macbook')
        for folder in self.server.folders.values():
            self.assertEqual([d['deviceID'] for d in folder['devices']], [ME, PEER])
        self.assertIn('Shared', out.getvalue())
        self.assertIn('FreeTube', out.getvalue())
        self.assertNotIn(API_KEY, out.getvalue())

    def test_second_add_with_the_same_arguments_writes_nothing(self):
        self.ready()
        self.add()
        self.server.writes.clear()
        self.assertEqual(self.add(), 0)
        self.assertEqual(self.server.writes, [])

    def test_changed_magicdns_updates_only_the_device_fields(self):
        self.ready()
        self.add()
        self.server.devices[PEER]['paused'] = True
        self.server.writes.clear()
        self.add(magicdns='new.tailnet.example')
        puts = [w for w in self.server.writes if w[0] == 'PUT']
        self.assertEqual([w[1] for w in puts], ['/rest/config/devices/' + PEER])
        self.assertTrue(self.server.devices[PEER]['paused'])
        self.assertEqual(self.server.devices[PEER]['addresses'][0], 'tcp://new.tailnet.example:22000')
        self.assertEqual([w for w in self.server.writes if 'folders' in w[1]], [])

    def test_a_paused_folder_stays_paused_when_a_device_is_added(self):
        self.ready()
        self.server.folders['groundwork-freetube']['paused'] = True
        self.add()
        self.assertTrue(self.server.folders['groundwork-freetube']['paused'])
        self.assertFalse(self.server.folders['groundwork-shared']['paused'])

    def test_bad_input_is_rejected_before_any_write(self):
        self.ready()
        for override in ({'device_id': 'not-an-id'}, {'device_id': ME}, {'magicdns': 'bad host!'}, {'name': ''}):
            with self.assertRaises(gs.SyncthingError):
                self.add(**override)
        self.assertEqual(self.server.writes, [])

    def test_a_moved_folder_stops_the_pairing(self):
        self.ready()
        self.server.folders['groundwork-shared']['path'] = '/elsewhere'
        with self.assertRaises(gs.SyncthingError):
            self.add()
        self.assertEqual([w for w in self.server.writes if 'folders' in w[1]], [])

    def test_stopped_service_prints_the_start_command_and_fails(self):
        self.seed_config()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self.add(), 1)
        self.assertIn('systemctl --user enable --now syncthing.service', err.getvalue())
        self.assertEqual(self.server.requests, [])

    def test_loose_config_permissions_are_refused(self):
        self.ready()
        self.config.chmod(0o644)
        with self.assertRaises(gs.SyncthingError):
            self.add()
        self.config.chmod(0o600)
        (self.config.parent / 'key.pem').chmod(0o640)
        with self.assertRaises(gs.SyncthingError):
            self.add()
        self.assertEqual(self.server.writes, [])

    def test_old_binary_is_refused(self):
        self.ready()
        os.environ['FAKE_SYNCTHING_VERSION'] = 'syncthing v2.1.1 "x"'
        with self.assertRaises(gs.SyncthingError):
            self.add()
        with self.assertRaises(gs.SyncthingError):
            gs.pair_show()
        self.assertEqual(self.server.writes, [])

    def test_usage_errors_do_not_touch_anything(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for args in (['pair'], ['pair', 'add', '--name', 'x'], ['pair', 'add', '--device-id', PEER, '--device-id', PEER]):
                self.assertEqual(gs.main(args), 64)
        self.assertEqual(self.server.requests, [])


class SourceTests(unittest.TestCase):
    def test_no_device_id_or_key_lives_in_the_repo_script(self):
        text = SCRIPT.read_text()
        self.assertNotRegex(text, r'(?:[A-Z2-7]{7}-){7}[A-Z2-7]{7}')
        # The Tailscale range itself (100.64.0.0/10) is policy, not an address.
        self.assertNotRegex(text, r'\b100\.\d+\.\d+\.\d+(?!/|\d)')


if __name__ == '__main__':
    unittest.main()
