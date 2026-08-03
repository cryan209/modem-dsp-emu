import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from v34_script import GATE_FIELD, RECORD_BASE, STATE_FIELD, decode, walk


def entry(field_hi, field_lo, value_hi, value_lo):
    """One three-word script entry carrying both roles' halves."""
    return ((field_hi << 8) | field_lo,
            ((value_hi & 0xFF) << 8) | (value_lo & 0xFF),
            ((value_hi >> 8) << 8) | (value_lo >> 8))


def script(*entries):
    words = []
    for item in entries:
        words.extend(item)
    return tuple(words)


class DecodeTests(unittest.TestCase):
    def test_the_two_roles_read_different_halves_of_one_word(self):
        words = script(entry(0x04, 0x10, 0x8200, 0x0066))
        answering = decode(words, 0, answering=True)
        calling = decode(words, 0, answering=False)
        self.assertEqual((answering.field, answering.value), (0x04, 0x8200))
        self.assertEqual((calling.field, calling.value), (0x10, 0x0066))

    def test_record_base_and_gate_field_match_the_firmware_map(self):
        # PM 0x2e2b adds the field to the record base; the gate the V.34 page
        # tests at PM 0x285e is DM(0x213B).
        self.assertEqual(RECORD_BASE + GATE_FIELD, 0x213B)
        self.assertEqual(RECORD_BASE + STATE_FIELD, 0x2147)


class WalkTests(unittest.TestCase):
    def test_a_block_ends_at_the_terminating_field(self):
        words = script(entry(0x00, 0x10, 0x0000, 0x0050),
                       entry(0x00, 0x19, 0x0000, 0x0001),
                       entry(0x00, 0x10, 0x0000, 0x0052),
                       entry(0x00, 0x19, 0x0000, 0x0001))
        blocks = walk(words, 0, answering=False, terminator=0x19, limit=8)
        self.assertEqual(len(blocks), 2)
        self.assertEqual([b.address for b in blocks], [0, 6])
        self.assertEqual(blocks[0].field(STATE_FIELD), 0x0050)
        self.assertEqual(blocks[1].field(STATE_FIELD), 0x0052)

    def test_field_returns_none_when_the_block_does_not_carry_it(self):
        words = script(entry(0x00, 0x10, 0x0000, 0x0064),
                       entry(0x00, 0x19, 0x0000, 0x0001))
        block = walk(words, 0, answering=False, terminator=0x19, limit=4)[0]
        self.assertIsNone(block.field(GATE_FIELD))
        self.assertEqual(block.field(STATE_FIELD), 0x0064)

    def test_walking_stops_rather_than_running_off_the_end(self):
        # No terminator anywhere: the walk must not invent blocks.
        words = script(*[entry(0x00, 0x10, 0x0000, 0x0050) for _ in range(5)])
        self.assertEqual(walk(words, 0, answering=False, terminator=0x19,
                              limit=8), [])

    def test_the_gate_bit_is_read_from_the_value_not_the_field(self):
        words = script(entry(0x00, GATE_FIELD, 0x0000, 0x8200),
                       entry(0x00, 0x19, 0x0000, 0x0001))
        block = walk(words, 0, answering=False, terminator=0x19, limit=4)[0]
        gate = block.field(GATE_FIELD)
        self.assertEqual(gate, 0x8200)
        self.assertTrue(gate & 0x8000)

    def test_a_cleared_gate_is_distinguished_from_an_absent_one(self):
        words = script(entry(0x00, GATE_FIELD, 0x0000, 0x0200),
                       entry(0x00, 0x19, 0x0000, 0x0001))
        block = walk(words, 0, answering=False, terminator=0x19, limit=4)[0]
        self.assertEqual(block.field(GATE_FIELD), 0x0200)
        self.assertFalse(block.field(GATE_FIELD) & 0x8000)


if __name__ == '__main__':
    unittest.main()
