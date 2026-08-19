"""The V.90 mapping-frame block has to survive six samples, and it did not.

Page 14 fills `DM(0x3FA7..0x3FAC)` once per 1333 Hz mapping frame and its
serializer reads one slot per 8 kHz sample from the cursor `DM(0x20DE)`, so the
six words have to live for six samples.  The resident kernel's frame path
zeroes all six of them every sample at `PM 0x06C6`, which leaves one live slot
per pass: censused in data mode on the direct backend, `DM(0x3FA8..0x3FAC)`
took 1.167 writes per frame (1.000 clear + 0.167 refill) and exactly 16.7% of
published samples were nonzero.

`Card._hold_tx_block()` holds that store while page 14 is resident and puts it
back on the way out.  These tests cover the state machine around it -- the
store lives in the resident kernel rather than in the overlay, so a page load
does not restore it and the exit path has to test the page being *left*, which
is the bug the native tower's own version of this had.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import dial_tikrnl_drive as drive

CLEAR = drive.V90D_TX_BLOCK_CLEAR
OPCODE = 0xA00001               # DM(I0,M1) = 0x0000, the six-count clear store


class FakeCard:
    """Just the attributes `_hold_tx_block` touches."""

    def __init__(self, resident: int = 0x0260) -> None:
        self.pm = [0] * 0x4000
        self.pm[CLEAR] = OPCODE
        self.resident = resident
        self._v90d_saved_clear = None

    hold = drive.Card._hold_tx_block

    def load(self, download_id: int) -> None:
        self.hold(download_id)
        self.resident = download_id


class TxBlockHoldTest(unittest.TestCase):

    def test_the_clear_is_held_while_page_14_is_resident(self):
        card = FakeCard()
        card.load(drive.V90D_ID)
        self.assertEqual(card.pm[CLEAR], 0x000000)
        self.assertEqual(card._v90d_saved_clear, OPCODE)

    def test_it_is_restored_on_the_way_out(self):
        card = FakeCard()
        card.load(drive.V90D_ID)
        card.load(0x0261)
        self.assertEqual(card.pm[CLEAR], OPCODE)
        self.assertIsNone(card._v90d_saved_clear)

    def test_reloading_page_14_does_not_save_the_nopped_store(self):
        # The saved word is what gets written back. Saving again while the
        # store is already NOP'd would restore a NOP for the rest of the call,
        # so V.34 after a V.90 fallback would inherit a kernel that never
        # clears the block.
        card = FakeCard()
        card.load(drive.V90D_ID)
        card.load(drive.V90D_ID)
        self.assertEqual(card._v90d_saved_clear, OPCODE)
        card.load(0x0261)
        self.assertEqual(card.pm[CLEAR], OPCODE)

    def test_a_page_load_that_never_touched_page_14_is_left_alone(self):
        card = FakeCard()
        card.load(0x0261)
        self.assertEqual(card.pm[CLEAR], OPCODE)
        self.assertIsNone(card._v90d_saved_clear)

    def test_the_held_address_is_the_store_the_kernel_clears_with(self):
        # A wrong address would NOP an unrelated kernel instruction and the
        # 16.7% would stay exactly where it was, so pin the opcode: the clear
        # is the immediate store the DO loop at PM 0x06C5 runs six times.
        self.assertEqual(CLEAR, 0x06C6)
        self.assertEqual(drive.V90D_TX_CENSUS_FRAME, 0x3FA7)


if __name__ == "__main__":
    unittest.main()
