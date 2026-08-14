"""The V.8 script-record decoder, pinned to values measured on a live call.

The masks and condition indices asserted here were read off the running DSP
(docs/handoff.md, the `EICON_ANALOG_TRACE_CURSOR` walks), so if the decoder
drifts these fail rather than quietly producing a plausible-looking table.
"""

import unittest
from pathlib import Path

import sys

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'tools'))

import v8_script_records as vsr  # noqa: E402

EICON_V8 = (REPO / 'artifacts/eicon-dsp/build-109-789-analog/overlays'
            / '025f-v8.ana-overlay/dm.words')
ASTER = REPO / 'docs/firmware/Aster 5 DSP/T8660014.00'


@unittest.skipUnless(EICON_V8.is_file(), 'extract the build-109 overlay set first')
class EiconV8Records(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.words = vsr.load_words(EICON_V8)
        cls.records = vsr.decode_records(cls.words, 0x0050, 0x034A)
        cls.by_addr = {r.addr: r for r in cls.records}
        cls.conds, cls.dests = vsr.read_tables(cls.words, 0x034A, 0x035B, 19)

    def test_record_count(self):
        """43 records, the count established by the live cursor walk."""
        self.assertEqual(len(self.records), 43)

    def test_records_tile_the_region_exactly(self):
        self.assertEqual(self.records[0].addr, 0x0050)
        self.assertEqual(self.records[-1].end, 0x034A)
        for a, b in zip(self.records, self.records[1:]):
            self.assertEqual(a.end, b.addr)

    def test_action_masks_match_the_live_call(self):
        for addr, mask in ((0x01DC, 0x0086),   # CI
                           (0x021B, 0x0016),   # CM
                           (0x031D, 0x0100),
                           (0x033B, 0x0001)):
            self.assertEqual(self.by_addr[addr].mask, mask, hex(addr))

    def test_fall_through_condition_indices_match_the_live_call(self):
        for addr, cond0 in ((0x029F, 9), (0x02AB, 0), (0x021B, 5)):
            self.assertEqual(self.by_addr[addr].get(0x11), cond0, hex(addr))

    def test_exactly_one_record_builds_a_cm(self):
        builders = [r.addr for r in self.records if r.mask & (1 << 4)]
        self.assertEqual(builders, [0x021B])

    def test_destination_table(self):
        for index, target in ((10, 0x031D), (14, 0x02B7), (17, 0x021B)):
            self.assertEqual(self.dests[index], target, index)

    def test_condition_table_head(self):
        self.assertEqual(self.conds[0], 0x37D5)   # constant true, never LE
        self.assertEqual(self.conds[1], 0x37D7)   # the countdown
        self.assertEqual(self.conds[14], 0x3525)  # the DM(0x3F4B) bit-8 gate

    def test_02ab_overrides_its_conditions_to_the_never_taken_one(self):
        """0x02AB carries no destination index at all -- DM(0x0790)/DM(0x0791)
        persist from 0x029F -- and rewrites both the fall-through and slot-1
        conditions to index 0, the constant true that `IF LE` never takes.

        Taken with 0x028D leaving the slot-2 condition at index 0 as well, the
        record data says 0x02AB has no script-level exit. The archived live
        trace has it reaching 0x031D, so one of the two is incomplete; see
        docs/v8_script_records.md.
        """
        rec = self.by_addr[0x02AB]
        self.assertIsNone(rec.get(0x0D))
        self.assertIsNone(rec.get(0x0E))
        self.assertEqual(rec.get(0x0F), 0)
        self.assertEqual(rec.get(0x11), 0)
        self.assertEqual(self.by_addr[0x028D].get(0x10), 0)

    def test_the_only_entry_to_02b7_is_01bb_slot1(self):
        """0x01BB slot 1, under the DM(0x3F4B) bit-8 condition, is the sole way
        into 0x02B7 -- confirmed live by pinning that bit."""
        entries = [(r.addr, slot)
                   for r in self.records
                   for slot, off in ((1, 0x0D), (2, 0x0E))
                   if (idx := r.get(off)) is not None
                   and self.dests[idx] == 0x02B7]
        self.assertEqual(entries, [(0x01BB, 1)])
        self.assertEqual(self.by_addr[0x01BB].get(0x0F), 14)

    def test_02b7_cannot_fall_through(self):
        """Its fall-through condition is index 16 = PM 0x3529 = `AR = 0 + 1`,
        the same constant true as index 0. So 0x02C9 and 0x02D5 are unreachable
        and 0x02D5's destination index 17 is dead data."""
        self.assertEqual(self.by_addr[0x02B7].get(0x11), 16)
        self.assertEqual(self.conds[16], 0x3529)
        self.assertEqual(self.conds[0], 0x37D5)
        # Different addresses, byte-identical bodies: `AR = 0 + 1` then RTS.
        pm = (EICON_V8.parent / 'pm.bin').read_bytes()
        body = lambda a: pm[a * 3:(a + 2) * 3]
        self.assertEqual(body(0x3529), body(0x37D5))
        self.assertEqual(body(0x3529).hex(), '0f38220f000a')

    def test_the_route_to_the_cm_builder_starts_at_01c7_slot1(self):
        """0x0200 is destination index 6 and 0x01C7 slot 1 is what reaches it,
        under condition index 3 -- the ANSam detector counter DM(0x07BD)
        against its 0x0780 threshold (docs/analog_v8_oracle.md)."""
        self.assertEqual(self.dests[6], 0x0200)
        rec = self.by_addr[0x01C7]
        self.assertEqual(rec.get(0x0D), 6)
        self.assertEqual(rec.get(0x0F), 3)
        self.assertEqual(self.conds[3], 0x37DC)

    def test_0200_fall_through_is_the_live_route_to_the_cm_builder(self):
        """0x0200 is contiguous with 0x021B, pins both slots to the never-taken
        condition, and gates fall-through on the countdown it loads."""
        rec = self.by_addr[0x0200]
        self.assertEqual(rec.end, 0x021B)
        self.assertEqual(rec.get(0x0F), 0)
        self.assertEqual(rec.get(0x10), 0)
        self.assertEqual(rec.get(0x11), 1)
        self.assertEqual(rec.get(0x0A), 0x0866)


@unittest.skipUnless(ASTER.is_file(), 'Aster 5 DSP image not present')
class AsterV8Records(unittest.TestCase):
    """The Telindus 2005 build decodes with the same interpreter."""

    COND_BASE, DEST_BASE, COUNT = 0x046A, 0x0481, 23

    @classmethod
    def setUpClass(cls):
        cls.words = vsr.load_aster(ASTER, image=0, page=6)
        cls.records = vsr.decode_records(cls.words, 0x0050, cls.COND_BASE)
        cls.by_addr = {r.addr: r for r in cls.records}
        cls.conds, cls.dests = vsr.read_tables(
            cls.words, cls.COND_BASE, cls.DEST_BASE, cls.COUNT)

    def test_records_tile_the_region_exactly(self):
        self.assertEqual(len(self.records), 67)
        self.assertEqual(self.records[-1].end, self.COND_BASE)

    def test_same_builder_masks_as_eicon(self):
        self.assertEqual(self.by_addr[0x0257].mask, 0x0086)   # CI
        self.assertEqual(self.by_addr[0x015E].mask, 0x0020)   # JM
        self.assertEqual([r.addr for r in self.records if r.mask & (1 << 4)],
                         [0x02C9])                            # CM, and only it

    def test_cm_branch_is_gated_by_condition_14_here_too(self):
        """0x0236 slot 1 -> 0x038F under condition 14, the same shape as Eicon's
        0x01BB slot 1 -> 0x02B7."""
        rec = self.by_addr[0x0236]
        self.assertEqual(self.dests[rec.get(0x0D)], 0x038F)
        self.assertEqual(rec.get(0x0F), 14)

    def test_the_two_data_pumps_share_the_record_table(self):
        other = vsr.load_aster(ASTER, image=1, page=6)
        region = range(0x0000, self.DEST_BASE + self.COUNT)
        self.assertEqual([self.words.get(a) for a in region],
                         [other.get(a) for a in region])


if __name__ == '__main__':
    unittest.main()
