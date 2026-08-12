import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import eicon_mips_dis as md

FIRMWARE = ROOT / 'docs' / 'firmware'


class DisassembleTests(unittest.TestCase):
    """Words taken from the 4BRI reset vector and the live trap frame."""

    def test_reset_vector(self):
        # docs/4bri_v1_firmware_replay.md: BAR2 offset 0, jumps to 0x44004.
        expected = [
            (0x80000000, 0x3C088004, 'lui   t0, 0x8004'),
            (0x80000004, 0x25084004, 'addiu t0, t0, 16388'),
            (0x80000008, 0x3C01A000, 'lui   at, 0xa000'),
            (0x8000000C, 0x01014025, 'or    t0, t0, at'),
            (0x80000010, 0x01000008, 'jr    t0'),
        ]
        for pc, word, text in expected:
            self.assertEqual(md.disassemble(word, pc), text)

    def test_faulting_instruction(self):
        # The live null-pointer trap: lw v1, 0xb8(a0) with a0 == 0.
        self.assertEqual(md.disassemble(0x8C8300B8, 0x80063F68),
                         'lw    v1, 184(a0)')

    def test_caller_sets_a0_from_structure_field(self):
        self.assertEqual(md.disassemble(0x8E04000C, 0x8009B338),
                         'lw    a0, 12(s0)')
        self.assertEqual(md.disassemble(0x0C018FDA, 0x8009B340),
                         'jal   0x80063f68')
        self.assertEqual(md.disassemble(0x00408821, 0x8009B344),
                         'addu  s1, v0, zero')

    def test_gp_accessors(self):
        self.assertEqual(md.disassemble(0x0080E021, 0x80044258),
                         'addu  gp, a0, zero')
        self.assertEqual(md.disassemble(0x8FBC0080, 0x80044410),
                         'lw    gp, 128(sp)')

    def test_nop_and_unknown(self):
        self.assertEqual(md.disassemble(0, 0x80000000), 'nop')
        self.assertEqual(md.disassemble(0x40800000, 0x80000000),
                         '.word 0x40800000')

    def test_branch_target_is_pc_relative(self):
        # beq v0, zero, +0x4e words from the delay slot.
        self.assertEqual(md.disassemble(0x1040004E, 0x8009B2C0),
                         'beq   v0, zero, 0x8009b3fc')

    def test_likely_branches_are_decoded(self):
        """The TLV type checks guarding the statistics attach use bnel."""
        self.assertEqual(md.disassemble(0x5462007B, 0x8009CCE4),
                         'bnel  v1, v0, 0x8009ced4')
        self.assertEqual(md.disassemble(0x54620077, 0x8009CCF4),
                         'bnel  v1, v0, 0x8009ced4')

    def test_gate_branches(self):
        self.assertEqual(md.disassemble(0x1260000E, 0x80082294),
                         'beq   s3, zero, 0x800822d0')
        self.assertEqual(md.disassemble(0x16820003, 0x800822D4),
                         'bne   s4, v0, 0x800822e4')


class GpRelativeTests(unittest.TestCase):

    def test_recognises_gp_base(self):
        self.assertTrue(md.is_gp_relative(0x8F88036C))     # lw t0, 0x36c(gp)
        self.assertFalse(md.is_gp_relative(0x8C8300B8))    # lw v1, 0xb8(a0)
        self.assertFalse(md.is_gp_relative(0x0080E021))    # addu gp, a0, zero

    def test_image_has_unguarded_gp_accesses(self):
        """$gp is never established, so these are latent faults."""
        image = FIRMWARE / 'te_dmlt.qm.107-136'
        data = image.read_bytes()
        import struct
        count = sum(1 for i in range(len(data) // 4)
                    if md.is_gp_relative(struct.unpack_from('<I', data, i * 4)[0]))
        self.assertGreater(count, 0)


if __name__ == '__main__':
    unittest.main()
