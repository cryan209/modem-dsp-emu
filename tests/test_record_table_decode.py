"""The state-record tables the V.34 and V.90A pages interpret.

These assert the two things Session 250 established and that a future change to
the decoder would silently break: the record format reproduces the state walk
the live trace prints, and `DM(0x20EF)` -- the word the V.90A caller's `0x0092`
waits on -- is written by exactly one record in the whole table, with `0x0000`.
That negative is the whole basis for "nothing in the firmware can set bit 11",
so it is worth an assertion rather than a paragraph.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import record_table_decode as rtd

ROOT = Path(__file__).resolve().parents[1]
V90A_DM = (ROOT / 'artifacts/eicon-dsp/build-109-789-analog/overlays'
           / '026b-v90.ana-apcm-overlay/dm.bin')
V34_DM = ROOT / 'artifacts/eicon-dsp/overlays/0261-v.34-overlay/dm.bin'

# The chain the page's own initialisation names: PM 0x3348 sets both cursors,
# DM(0x120E) and DM(0x2127), to 0x1689.
V90A_START = 0x1689
# PM 0x2D6B/0x2D71 load the V.34 equivalent.
V34_START = 0x1A2E


def states(dm_path, start):
    dm = rtd.load(dm_path)
    return [dict(entries).get(rtd.STATE_INDEX)
            for _, entries in rtd.walk(dm, start, 200)]


class RecordTableTest(unittest.TestCase):
    def test_v90a_walk_matches_the_live_trace(self):
        """The states --trace-v90a-state reports, in the order it reports them.

        Everything up to 0x0092 is what the loopback caller actually walks; the
        tail past it is what it would walk, and is the reason the table was
        decoded at all.
        """
        walked = states(V90A_DM, V90A_START)
        for state in (0x0050, 0x0060, 0x0062, 0x0064, 0x0070, 0x0071, 0x0072,
                      0x0073, 0x0075, 0x0076, 0x0092, 0x0094, 0x0095, 0x00B0):
            self.assertIn(state, walked, f'0x{state:04x} missing from the table')
        ordered = [s for s in walked if s in (0x0076, 0x0092, 0x0094, 0x0095)]
        self.assertEqual(ordered, [0x0076, 0x0092, 0x0094, 0x0095])

    def test_only_one_v90a_record_writes_dm_20ef(self):
        """DM(0x20EF) is block index 6, and one record writes it: zero.

        With no absolute or indirect writer anywhere in the analog109 set
        either, bit 11 cannot be raised by the firmware in this configuration.
        """
        dm = rtd.load(V90A_DM)
        writers = [(address, dict(entries)[6])
                   for address, entries in rtd.walk(dm, V90A_START, 200)
                   if 6 in dict(entries)]
        self.assertEqual(writers, [(0x1689, 0x0000)])

    def test_v34_uses_the_same_format(self):
        """The V.34 page's table decodes with the same rules.

        The unpacker is byte-identical across the pages, so this is what makes
        the V.90A instrument reusable on the V.34 blocker rather than a
        V.90-only decode.
        """
        walked = states(V34_DM, V34_START)
        self.assertGreater(len(walked), 50)
        for state in (0x0050, 0x0090, 0x00B0, 0x00B2, 0x00D0):
            self.assertIn(state, walked, f'0x{state:04x} missing from the table')


if __name__ == '__main__':
    unittest.main()


class InnerValueFormat(unittest.TestCase):
    """The inner unpacker's high byte is C's *low* byte.

    `PM 0x33D9` is `SR = LSHIFT SR0 (HI, OR) BY 8` -- a left shift by 8 into the
    high half -- where the outer unpacker reaches the same slot through
    `SE = 0xFFF8`. A first reading of the decoder took `C & 0xFF00` for both,
    which silently mis-decodes every inner value whose two halves of C differ.

    The check is a live write rather than a re-reading: the inner unpacker
    `PM 0x33DB` is observed storing `0x4010` into `DM(0x20EB)` on the V.90A
    caller, and only the corrected formula produces it -- the entry at DM 0x1737
    decodes as `0x1310` under the old one. The two records that carry bit 15 of
    that word are invisible under the old formula too, which is what made
    "nothing in the firmware sets it" look true.
    """

    def test_inner_value_takes_the_low_byte_of_the_third_word(self):
        # A = index 2, B = 0x??10, C = 0x??40 -> 0x4010, not 0x1310.
        dm = [0x0002, 0x1310, 0x1340, 0x0024, 0x0000, 0x0000]
        entries, _ = rtd.decode_record(dm, 0, terminator=rtd.INNER_TERMINATOR,
                                       inner=True)
        self.assertEqual(dict(entries)[2], 0x4010)

    def test_two_inner_records_set_bit_15_of_dm_20eb(self):
        # DM(0x20EB) is block index 2, and bit 15 is what the outer machine's
        # 0x00c0 waits on at PM 0x3495. Inner states 0x62 and 0x64 write it.
        dm = rtd.load(V90A_DM)
        records = rtd.walk(dm, V90A_START, 200, rtd.INNER_TERMINATOR, True)
        carriers = [(address, dict(entries)[rtd.INNER_STATE_INDEX])
                    for address, entries in records
                    if dict(entries).get(2, 0) & 0x8000]
        self.assertEqual(carriers, [(0x17C4, 0x0062), (0x17D3, 0x0064)])
