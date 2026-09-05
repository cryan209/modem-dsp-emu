"""The direct and Analog SPORT backends must honor the requested menu."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import dial_tikrnl_drive as drive


class DirectModulationTests(unittest.TestCase):
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
