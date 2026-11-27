"""Antigravity settings merge, page check, and key binding (issue 162)."""
import importlib.util
import io
import json
from pathlib import Path
import stat
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
AARCH64_ELF = bytes([0x7f, 0x45, 0x4c, 0x46, 0x02, 0x01]) + bytes(12) + bytes([0xb7, 0x00])
SECRET = 'sekret-value'


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, REPO / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


antigravity = load('groundwork_antigravity', 'groundwork-antigravity.py')
updater = load('groundwork_antigravity_updater', 'groundwork-update.py')


class SettingsMergeTests(unittest.TestCase):
    def setUp(self):
        self.temp = __import__('tempfile').TemporaryDirectory(prefix='groundwork-antigravity-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.agy = self.root / 'usr' / 'bin' / 'agy'
        self.agy.parent.mkdir(parents=True)
        self.agy.write_bytes(AARCH64_ELF)
        self.agy.chmod(0o755)
        self.environ = {'HOME': str(self.home), 'PATH': '/usr/bin:/bin'}

    def settings_path(self, home=None):
        return Path(home or self.home) / '.t3' / 'userdata' / 'settings.json'

    def merge(self, binary=None, environ=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = antigravity.merge_settings(str(self.agy if binary is None else binary),
                                                 self.environ if environ is None else environ)
        return status, stdout.getvalue(), stderr.getvalue()

    def test_adds_the_instance_when_it_is_absent(self):
        path = self.settings_path()
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            'theme': 'omarchy',
            'providerInstances': {
                'claude': {'driver': 'claude', 'config': {'apiKey': SECRET}},
            },
        }, indent=2) + '\n')
        path.chmod(0o640)
        status, stdout, stderr = self.merge()
        self.assertEqual(status, 0)
        self.assertEqual(stdout, 'added\n')
        self.assertNotIn(SECRET, stdout + stderr)
        saved = json.loads(path.read_text())
        self.assertEqual(saved['theme'], 'omarchy')
        self.assertEqual(saved['providerInstances']['claude']['config']['apiKey'], SECRET)
        instance = saved['providerInstances']['antigravity']
        self.assertEqual(instance, {
            'driver': 'antigravity',
            'enabled': True,
            'config': {'binaryPath': str(self.agy)},
        })
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)

    def test_existing_instance_is_a_byte_for_byte_no_op(self):
        path = self.settings_path()
        path.parent.mkdir(parents=True)
        original = (
            '{\n'
            '  "providerInstances": {\n'
            '    "antigravity": {\n'
            '      "driver": "antigravity",\n'
            '      "enabled": false,\n'
            '      "config": {"binaryPath": "/tmp/custom-agy", "apiKey": "' + SECRET + '"}\n'
            '    }\n'
            '  }\n'
            '}\n'
        )
        path.write_text(original)
        before = path.stat().st_mtime_ns
        status, stdout, stderr = self.merge()
        self.assertEqual(status, 0, stderr)
        self.assertEqual(stdout, 'already-present\n')
        self.assertNotIn('pinned', stdout)
        self.assertEqual(path.read_text(), original)
        self.assertNotIn(SECRET, stdout + stderr)
        self.assertEqual(path.stat().st_mtime_ns, before)

    def test_an_enabled_instance_at_this_agy_reports_already_present_pinned(self):
        path = self.settings_path()
        path.parent.mkdir(parents=True)
        original = json.dumps({
            'providerInstances': {
                'antigravity': {
                    'driver': 'antigravity',
                    'enabled': True,
                    'config': {'binaryPath': str(self.agy), 'apiKey': SECRET},
                },
            },
        }, indent=2) + '\n'
        path.write_text(original)
        status, stdout, stderr = self.merge()
        self.assertEqual(status, 0, stderr)
        self.assertEqual(stdout, 'already-present pinned\n')
        self.assertEqual(path.read_text(), original)
        self.assertNotIn(SECRET, stdout + stderr)

    def test_missing_file_is_created_mode_0600(self):
        status, stdout, stderr = self.merge()
        path = self.settings_path()
        self.assertEqual(status, 0, stderr)
        self.assertEqual(stdout, 'added\n')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        saved = json.loads(path.read_text())
        self.assertEqual(saved['providerInstances']['antigravity']['config']['binaryPath'], str(self.agy))
        self.assertNotIn(SECRET, stdout + stderr)

    def test_missing_provider_instances_object_is_added(self):
        path = self.settings_path()
        path.parent.mkdir(parents=True)
        path.write_text('{"theme": "omarchy"}\n')
        status, stdout, stderr = self.merge()
        self.assertEqual(status, 0, stderr)
        self.assertEqual(stdout, 'added\n')
        saved = json.loads(path.read_text())
        self.assertEqual(saved['theme'], 'omarchy')
        self.assertEqual(saved['providerInstances']['antigravity']['driver'], 'antigravity')

    def test_invalid_json_is_left_untouched(self):
        path = self.settings_path()
        path.parent.mkdir(parents=True)
        original = '{ "apiKey": "' + SECRET + '" \n'
        path.write_text(original)
        status, stdout, stderr = self.merge()
        self.assertEqual(status, 0)
        self.assertEqual(stdout, 'left-untouched\n')
        self.assertEqual(path.read_text(), original)
        self.assertIn('not valid JSON', stderr)
        self.assertNotIn(SECRET, stdout + stderr)

    def test_non_object_root_and_provider_list_are_left_untouched(self):
        path = self.settings_path()
        path.parent.mkdir(parents=True)
        for original, message in (
            ('[1, 2]\n', 'not a JSON object'),
            ('{"providerInstances": ["antigravity"]}\n', 'providerInstances is not a JSON object'),
        ):
            with self.subTest(original=original):
                path.write_text(original)
                status, stdout, stderr = self.merge()
                self.assertEqual(status, 0)
                self.assertEqual(stdout, 'left-untouched\n')
                self.assertEqual(path.read_text(), original)
                self.assertIn(message, stderr)

    def test_t3code_home_is_honoured_and_empty_means_the_default(self):
        custom = self.root / 'custom-t3'
        status, _stdout, stderr = self.merge(environ={**self.environ, 'T3CODE_HOME': str(custom)})
        self.assertEqual(status, 0, stderr)
        self.assertTrue((custom / 'userdata' / 'settings.json').is_file())
        self.assertFalse(self.settings_path().exists())
        status, _stdout, stderr = self.merge(environ={**self.environ, 'T3CODE_HOME': ''})
        self.assertEqual(status, 0, stderr)
        self.assertTrue(self.settings_path().is_file())

    def test_relative_t3code_home_and_bad_agy_paths_do_not_write(self):
        status, stdout, stderr = self.merge(environ={**self.environ, 'T3CODE_HOME': 'relative'})
        self.assertEqual(status, 1)
        self.assertEqual(stdout, '')
        self.assertIn('absolute', stderr)
        self.assertFalse(self.settings_path().exists())
        for binary in ('relative/agy', str(self.agy) + '\n', ''):
            with self.subTest(binary=binary):
                status, stdout, stderr = self.merge(binary=binary)
                self.assertEqual(status, 1)
                self.assertEqual(stdout, '')
                self.assertFalse((self.home / '.t3').exists())

    def test_symlink_settings_are_not_followed(self):
        target = self.root / 'elsewhere.json'
        target.write_text('{"apiKey": "' + SECRET + '"}\n')
        path = self.settings_path()
        path.parent.mkdir(parents=True)
        path.symlink_to(target)
        status, stdout, stderr = self.merge()
        self.assertEqual(status, 0)
        self.assertEqual(stdout, 'left-untouched\n')
        self.assertEqual(target.read_text(), '{"apiKey": "' + SECRET + '"}\n')
        self.assertIn('symlink', stderr)
        self.assertNotIn(SECRET, stdout + stderr)


class PageCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = __import__('tempfile').TemporaryDirectory(prefix='groundwork-antigravity-pages-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def elf(self, name, machine=183):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        header = bytearray(AARCH64_ELF)
        header[18:20] = machine.to_bytes(2, 'little')
        path.write_bytes(bytes(header))
        return path

    def check(self, files, readelf, page):
        def fake_run(args, **_kwargs):
            if args[:2] == ['pacman', '-Qlq']:
                return ''.join(str(path) + '\n' for path in files)
            if args[:1] == ['readelf']:
                return readelf
            raise AssertionError(args)

        def which(name, *args, **kwargs):
            if name == 'readelf':
                return '/bin/readelf'
            return None

        with patch.object(updater, 'run', side_effect=fake_run), patch.object(updater.shutil, 'which', side_effect=which):
            relaxed = updater.package_page_problem('antigravity-cli', page=page)
            strict = updater.package_alignment_problem('antigravity-cli')
        return relaxed, strict

    def test_relaxed_rule_passes_a_segment_the_strict_rule_refuses(self):
        # p_align 0x100 is below a 4 KiB page. Offset and address are multiples
        # of 16 KiB, so decision 0018 maps it and the strict rule does not.
        elf = self.elf('usr/bin/agy')
        note = self.root / 'README'
        note.write_text('not elf\n')
        segment = '  LOAD 0x000000 0x0000000000000000 0x0000000000000000 0x100 0x100 R E 0x100\n'
        relaxed, strict = self.check([elf, note], segment, page=16384)
        self.assertIsNone(relaxed)
        self.assertIn('not aligned', strict)
        relaxed_4k, _strict = self.check([elf, note], segment, page=4096)
        self.assertIsNone(relaxed_4k)

    def test_unaligned_offset_is_refused_on_16kib_pages(self):
        elf = self.elf('usr/bin/agy')
        segment = '  LOAD 0x100 0x100 0x100 0x100 0x100 R E 0x100\n'
        relaxed, _strict = self.check([elf], segment, page=16384)
        self.assertIn('cannot be mapped on 16 KiB pages', relaxed)

    def test_a_package_with_no_aarch64_elf_is_refused(self):
        elf = self.elf('usr/bin/agy', machine=62)
        segment = '  LOAD 0x0 0x0 0x0 0x100 0x100 R E 0x10000\n'
        relaxed, _strict = self.check([elf], segment, page=16384)
        self.assertIn('no aarch64 ELF', relaxed)


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = __import__('tempfile').TemporaryDirectory(prefix='groundwork-antigravity-bind-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.environ = {'HOME': str(self.home), 'PATH': str(self.bin)}

    def bindings(self):
        return self.home / '.config' / 'hypr' / 'bindings.lua'

    def lua(self):
        return self.home / '.config' / 'hypr' / 'groundwork-antigravity.lua'

    def invoke(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = antigravity.main(argv, self.environ)
        return status, stdout.getvalue(), stderr.getvalue()

    def test_template_lists_only_the_new_binding(self):
        text = (REPO / 'config/groundwork-antigravity.lua').read_text()
        binds = [line for line in text.splitlines() if line.startswith('o.bind(')]
        self.assertEqual(binds, [
            'o.bind("SUPER + SHIFT + V", "Antigravity", { launch = "antigravity", focus = "^antigravity$" })',
        ])
        self.assertIn('2026-11-27', text)
        self.assertIn('Groundwork Antigravity binding', text)
        self.assertNotIn('SUPER + SHIFT + A"', text)
        self.assertNotIn('SUPER + SHIFT + G"', text)

    def test_append_keeps_the_rest_of_bindings_lua(self):
        path = self.bindings()
        path.parent.mkdir(parents=True)
        original = '-- user binds\no.bind("SUPER + SHIFT + O", "Obsidian")\n'
        path.write_text(original)
        path.chmod(0o664)
        status, _stdout, stderr = self.invoke(['bind'])
        self.assertEqual(status, antigravity.CHANGED, stderr)
        text = path.read_text()
        self.assertTrue(text.startswith(original))
        self.assertEqual(text.count(antigravity.BINDING_LINE), 1)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o664)
        lua = self.lua()
        self.assertFalse(lua.is_symlink())
        self.assertNotEqual(lua.resolve(), (REPO / 'config/groundwork-antigravity.lua').resolve())
        again, _stdout, stderr = self.invoke(['bind'])
        self.assertEqual(again, 0, stderr)
        self.assertEqual(path.read_text(), text)

    def test_missing_bindings_file_is_only_the_dofile_line(self):
        status, _stdout, stderr = self.invoke(['bind'])
        self.assertEqual(status, antigravity.CHANGED, stderr)
        self.assertEqual(self.bindings().read_text(), antigravity.BINDING_LINE + '\n')
        self.assertFalse(self.bindings().is_symlink())

    def test_a_symlink_bindings_file_is_not_edited(self):
        target = self.root / 'real-bindings.lua'
        target.write_text('-- real\n')
        path = self.bindings()
        path.parent.mkdir(parents=True)
        path.symlink_to(target)
        status, _stdout, stderr = self.invoke(['bind'])
        # The copied lua file can be new. The symlink is not a loaded binding.
        self.assertEqual(status, antigravity.NOT_BOUND)
        self.assertEqual(target.read_text(), '-- real\n')
        self.assertIn('symlink', stderr)
        self.assertTrue(path.is_symlink())
        self.assertEqual(path.resolve(), target.resolve())
        again, _stdout, stderr = self.invoke(['bind'])
        self.assertEqual(again, antigravity.NOT_BOUND, stderr)

    def test_a_non_regular_or_non_utf8_bindings_file_is_not_bound(self):
        path = self.bindings()
        path.parent.mkdir(parents=True)
        path.mkdir()
        status, _stdout, stderr = self.invoke(['bind'])
        self.assertEqual(status, antigravity.NOT_BOUND, stderr)
        self.assertIn('not a regular file', stderr)
        path.rmdir()
        path.write_bytes(b'\xff\xfe not utf-8\n')
        status, _stdout, stderr = self.invoke(['bind'])
        self.assertEqual(status, antigravity.NOT_BOUND, stderr)
        self.assertIn('not UTF-8', stderr)
        self.assertEqual(path.read_bytes(), b'\xff\xfe not utf-8\n')

    def test_unbind_removes_only_the_owned_line(self):
        path = self.bindings()
        path.parent.mkdir(parents=True)
        path.write_text('-- user\n' + antigravity.BINDING_LINE + '\n-- trailing\n')
        status, _stdout, stderr = self.invoke(['unbind'])
        self.assertEqual(status, antigravity.CHANGED, stderr)
        self.assertEqual(path.read_text(), '-- user\n-- trailing\n')
        again, _stdout, _stderr = self.invoke(['unbind'])
        self.assertEqual(again, 0)
        self.assertEqual(path.read_text(), '-- user\n-- trailing\n')

    def test_focus_class_comes_from_the_package_desktop_entry(self):
        desktop = self.root / 'antigravity.desktop'
        desktop.write_text('[Desktop Entry]\nExec=antigravity %u\nStartupWMClass=Antigravity\n')
        pacman = self.bin / 'pacman'
        pacman.write_text('#!/bin/sh\n[ "$1" = -Qlq ] && [ "$2" = antigravity ] && printf "%s\\n" "$FIXTURE_DESKTOP"\n')
        pacman.chmod(0o755)
        self.environ['FIXTURE_DESKTOP'] = str(desktop)
        status, _stdout, stderr = self.invoke(['bind'])
        self.assertIn(status, (0, antigravity.CHANGED))
        self.assertIn('focus = "^Antigravity$"', self.lua().read_text(), stderr)
        self.assertFalse((self.home / '.local/share/applications/antigravity.desktop').exists())

    def test_a_package_without_a_desktop_entry_gets_a_user_launcher(self):
        pacman = self.bin / 'pacman'
        pacman.write_text('#!/bin/sh\n[ "$1" = -Qlq ] && [ "$2" = antigravity ] && exit 0\nexit 1\n')
        pacman.chmod(0o755)
        status, _stdout, stderr = self.invoke(['bind'])
        self.assertEqual(status, antigravity.CHANGED, stderr)
        launcher = self.home / '.local/share/applications/antigravity.desktop'
        self.assertIn('X-Groundwork-Antigravity=1', launcher.read_text())
        self.assertIn('Exec=antigravity', launcher.read_text())
        owned = launcher.read_text()
        launcher.write_text('user owned launcher\n')
        self.invoke(['bind'])
        self.assertEqual(launcher.read_text(), 'user owned launcher\n')
        self.assertNotIn(owned, launcher.read_text())

    def test_an_unsafe_window_class_keeps_the_command_name(self):
        desktop = self.root / 'antigravity.desktop'
        desktop.write_text('[Desktop Entry]\nExec=antigravity\nStartupWMClass=Not A Class\n')
        pacman = self.bin / 'pacman'
        pacman.write_text('#!/bin/sh\nprintf "%s\\n" "$FIXTURE_DESKTOP"\n')
        pacman.chmod(0o755)
        self.environ['FIXTURE_DESKTOP'] = str(desktop)
        self.invoke(['bind'])
        self.assertIn('focus = "^antigravity$"', self.lua().read_text())


if __name__ == '__main__':
    unittest.main()
