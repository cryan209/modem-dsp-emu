"""The direct and Analog SPORT backends must honor the requested menu."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import dial_tikrnl_drive as drive


class DirectModulationTests(unittest.TestCase):
    def configure(self, selection):
        card = SimpleNamespace(dm=[0] * 0x4000,
                               configure_g711_law=lambda law: None)
        with patch.dict('os.environ', {'EICON_MODULATION': selection}):
            drive.Card.configure_modem(card, 'calling')
        return card.dm[0x3f0a], card.dm[0x3f0b]

    def test_v34_exact_9600_capability_mask(self):
        self.assertEqual(self.configure('v34,0,9600,9600,9600,9600'),
                         (0, 0x0400))

    def test_v34_maximum_and_minimum_select_only_allowed_rates(self):
        self.assertEqual(self.configure('v34,0,4800,14400,4800,14400'),
                         (0, 0x1f00))
        self.assertEqual(self.configure('v34,0,,9600,,9600'), (0, 0x0780))
        self.assertEqual(self.configure('v34,0,24000,33600,24000,33600'),
                         (0x001f, 0))

    def test_common_mask_respects_both_direction_ranges(self):
        self.assertEqual(self.configure('v34,0,4800,14400,9600,19200'),
                         (0, 0x1c00))
        with self.assertRaisesRegex(ValueError, 'do not overlap'):
            self.configure('v34,0,4800,7200,9600,14400')

    def test_unbounded_defaults_are_preserved(self):
        for selection in ('', 'v34,0', 'v90,0'):
            self.assertEqual(self.configure(selection), (0x001f, 0xff00))

    def test_database_menu_tracks_selection_for_both_roles(self):
        for role in ('calling', 'answer'):
            for selection, menu in (('', 0xa13f), ('v34,0', 0x0100),
                                    ('v34', 0x213f), ('v22b,0', 0x0004)):
                with self.subTest(role=role, selection=selection):
                    card = SimpleNamespace(dm=[0] * 0x4000,
                                           configure_g711_law=lambda law: None)
                    with patch.dict('os.environ', {'EICON_MODULATION': selection}):
                        drive.Card.configure_modem(card, role)
                    self.assertEqual(card.dm[drive.DM_DB + 0x29], menu)
                    self.assertEqual(card.dm[drive.DM_DB + 1],
                                     0x048c if role == 'calling' else 0x0484)
