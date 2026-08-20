"""The direct V.90D PCMU table probe must use staged firmware values."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import dial_tikrnl_drive as drive


class FakeCard:
    restore = drive.Card._restore_v90d_pcmu_ucode_table

    def __init__(self) -> None:
        self.modem_law = "pcmu"
        self.dm = [0] * 0x4000
        self._v90d_staged_ucode = tuple(
            [0] + list(range(1, 128)))
        self._v90d_ucode_restored = False


class V90DPCMUUcodeTest(unittest.TestCase):

    def test_restores_staged_table_and_preserves_zero_sentinel(self):
        card = FakeCard()
        for address in range(0x1F14, 0x1F94):
            card.dm[address] = 0xAAAA
        with mock.patch.object(drive, "V90D_PCMU_UCODE_TABLE", True):
            card.restore()
        self.assertEqual(card.dm[0x1F14], 8)
        self.assertEqual(card.dm[0x1F15:0x1F94], list(range(1, 128)))
        self.assertTrue(card._v90d_ucode_restored)

    def test_non_pcmu_does_not_touch_the_table(self):
        card = FakeCard()
        card.modem_law = "pcma"
        card.dm[0x1F14] = 0x1234
        with mock.patch.object(drive, "V90D_PCMU_UCODE_TABLE", True):
            card.restore()
        self.assertEqual(card.dm[0x1F14], 0x1234)


if __name__ == "__main__":
    unittest.main()
