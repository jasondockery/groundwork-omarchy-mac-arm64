"""The Hyprland overlay lists only real diffs from Omarchy's input defaults."""
from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parents[1]
INPUT = REPO / 'packages/hypr/.config/hypr/input.lua'


class InputOverrideTests(unittest.TestCase):
    def setUp(self):
        self.text = INPUT.read_text()

    def test_layers_on_omarchy_defaults_with_reasons(self):
        self.assertIn('/usr/share/omarchy/default/hypr/input.lua', self.text)
        self.assertIn('List only settings that', self.text)
        self.assertIn('hl.config({', self.text)

    def test_contains_only_real_overrides(self):
        self.assertIn('sensitivity = 0.35', self.text)
        self.assertIn('tap_to_click = true', self.text)
        self.assertIn('drag_3fg = 1', self.text)
        self.assertIn('scroll_factor = 0.2', self.text)
        self.assertNotIn('natural_scroll', self.text)
        self.assertNotIn('clickfinger_behavior', self.text)
        for keyboard in ('repeat_rate', 'repeat_delay', 'numlock_by_default',
                         'kb_layout', 'kb_options'):
            self.assertNotIn(keyboard, self.text)
