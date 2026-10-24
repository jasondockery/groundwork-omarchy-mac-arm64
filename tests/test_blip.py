"""Blip preference, plugin-list parsing, and the shared bun alignment check."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('groundwork_blip_updater', REPO / 'scripts/groundwork-update.py')
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


class BunAlignmentTests(unittest.TestCase):
    def test_bun_check_uses_the_t3_load_alignment_helper(self):
        with patch.object(updater, 'load_alignments', return_value=[65536]) as helper, \
                patch.object(updater, 'page_size', return_value=16384), \
                patch.object(updater.shutil, 'which', return_value='/usr/bin/readelf'):
            self.assertIsNone(updater.bun_alignment_problem('/usr/bin/bun'))
        helper.assert_called_once_with('/usr/bin/bun')

    def test_misaligned_bun_names_the_page_size_and_upstream_issue(self):
        with patch.object(updater, 'load_alignments', return_value=[4096]), \
                patch.object(updater, 'page_size', return_value=16384), \
                patch.object(updater.shutil, 'which', return_value='/usr/bin/readelf'):
            problem = updater.bun_alignment_problem('/usr/bin/bun')
        self.assertIn('16 KiB', problem)
        self.assertIn('oven-sh/bun#17627', problem)
        self.assertIn('https://github.com/oven-sh/bun/issues/17627', problem)

    def test_bun_with_no_load_segment_is_refused(self):
        with patch.object(updater, 'load_alignments', return_value=[]), \
                patch.object(updater, 'page_size', return_value=16384), \
                patch.object(updater.shutil, 'which', return_value='/usr/bin/readelf'):
            self.assertIn('oven-sh/bun#17627', updater.bun_alignment_problem('/usr/bin/bun'))

    def test_missing_readelf_is_named(self):
        with patch.object(updater.shutil, 'which', return_value=None), \
                patch.object(updater, 'page_size', return_value=16384), \
                patch.object(updater, 'load_alignments', side_effect=AssertionError('readelf parser was copied')):
            problem = updater.bun_alignment_problem('/usr/bin/bun')
        self.assertIn('readelf is missing', problem)
        self.assertIn('oven-sh/bun#17627', problem)


class BlipPreferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='groundwork-blip-')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.config = self.home / 'omarchy.json'
        self.patch = patch.object(updater, 'CONFIG', self.config)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def write(self, **extra):
        value = dict(schema=1, source='/tmp/groundwork-source', keyd=False, screen_sharing=True,
                     obsidian_vault='Notes')
        value.update(extra)
        self.config.write_text(json.dumps(value) + '\n')

    def test_missing_blip_choice_stays_off(self):
        self.write()
        self.assertNotIn('blip', json.loads(self.config.read_text()))
        self.assertFalse(updater.blip_enabled(updater.settings()))
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(updater.main(['preferences', 'setup']), 0)
        self.assertEqual(output.getvalue().splitlines(), ['0', '1', 'Notes', '0'])

    def test_non_boolean_blip_choice_is_rejected(self):
        for bad in ('yes', 1, None):
            with self.subTest(bad=bad):
                self.write(blip=bad)
                with self.assertRaisesRegex(updater.UpdateError, 'Invalid updater settings'):
                    updater.settings()


class PluginListTests(unittest.TestCase):
    def run_listed(self, payload):
        return subprocess.run([sys.executable, str(REPO / 'scripts/groundwork-update.py'), 'plugin-listed', 'nixfred.blip'],
                              input=payload, capture_output=True, text=True, timeout=5)

    def test_listed_absent_and_unusable_json_are_distinct(self):
        present = self.run_listed('[{"id": "nixfred.blip", "enabled": false}]\n')
        absent = self.run_listed('[{"id": "blip.steam"}]\n')
        broken = self.run_listed('not-json')
        self.assertEqual(present.returncode, 0, present.stderr)
        self.assertEqual(absent.returncode, 1, absent.stderr)
        self.assertEqual(broken.returncode, 2, broken.stderr)
        self.assertNotEqual(broken.returncode, absent.returncode)


if __name__ == '__main__':
    unittest.main()
