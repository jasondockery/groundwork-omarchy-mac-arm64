"""EasyEffects default-on preference and the Groundwork-owned user unit (decision 0024)."""
from contextlib import redirect_stdout
import configparser
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
UNIT = REPO / 'config/easyeffects.service'
spec = importlib.util.spec_from_file_location('groundwork_easyeffects_updater', REPO / 'scripts/groundwork-update.py')
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='groundwork-easyeffects-')
        self.addCleanup(temporary.cleanup)
        self.config = Path(temporary.name) / 'omarchy.json'
        self.patch = patch.object(updater, 'CONFIG', self.config)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def write(self, **extra):
        value = dict(schema=1, source='/tmp/groundwork-source', keyd=False, screen_sharing=True,
                     obsidian_vault='Notes')
        value.update(extra)
        self.config.write_text(json.dumps(value) + '\n')

    def test_missing_choice_stays_on(self):
        # Protects: a setup saved before this choice still gets EasyEffects.
        self.write()
        self.assertNotIn('easyeffects', json.loads(self.config.read_text()))
        self.assertTrue(updater.easyeffects_enabled(updater.settings()))
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(updater.main(['preferences', 'setup']), 0)
        self.assertEqual(output.getvalue().splitlines(), ['0', '1', 'Notes', '0', '0'])

    def test_opt_out_adds_the_ninth_preferences_line(self):
        self.write(easyeffects=False)
        self.assertFalse(updater.easyeffects_enabled(updater.settings()))
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(updater.main(['preferences', 'setup']), 0)
        self.assertEqual(output.getvalue().splitlines(), ['0', '1', 'Notes', '0', '0', '', '', '', '0'])

    def test_non_boolean_choice_is_rejected(self):
        self.write(easyeffects='yes')
        with self.assertRaisesRegex(updater.UpdateError, 'Invalid updater settings'):
            updater.settings()


class UnitFileTests(unittest.TestCase):
    def test_unit_lists_only_the_documented_deltas(self):
        # Protects: a later edit does not copy upstream defaults or GTK flags
        # into the only EasyEffects override this layer ships.
        text = UNIT.read_text()
        self.assertNotIn('DISPLAY', text)
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(text)
        self.assertEqual(set(parser.sections()), {'Unit', 'Service', 'Install'})
        self.assertEqual(dict(parser['Unit']), {
            'description': 'EasyEffects',
            'after': 'pipewire.service',
            'wants': 'pipewire.service',
            'partof': 'graphical-session.target',
        })
        self.assertEqual(dict(parser['Service']), {
            'execstart': 'easyeffects --hide-window --service-mode',
            'restart': 'on-failure',
            'restartsec': '2',
        })
        self.assertNotIn('--gapplication-service', parser['Service']['execstart'])
        self.assertEqual(dict(parser['Install']), {
            'wantedby': 'graphical-session.target',
        })


if __name__ == '__main__':
    unittest.main()
