import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import eicon_4bri_trap as trap


class TrapFrameTests(unittest.TestCase):
    def make_snapshot(self):
        data = bytearray(0x200)
        struct.pack_into("<I", data, trap.TRAP_ID_OFFSET, 0x99999999)
        regs = list(range(32))
        words = [0x1040EC03, 0x00001008, 0x800B86C0, 0x0C685460,
                 *regs, 0x5761, 0x7480, 0, 0x101]
        struct.pack_into("<40I", data, trap.TRAP_FRAME_OFFSET, *words)
        return bytes(data)

    def test_parse_exception_frame(self):
        frame = trap.parse_trap(self.make_snapshot())
        self.assertEqual(frame.cause_name, "TLB load/DBOUND")
        self.assertEqual(frame.epc, 0x800B86C0)
        self.assertEqual(frame.bad_vaddr, 0x0C685460)
        self.assertEqual(frame.reg("gp"), 28)
        self.assertEqual(frame.lo, 0x5761)
        self.assertEqual(frame.hi, 0x7480)
        self.assertEqual(frame.xclass, 0x101)

    def test_rejects_snapshot_without_trap_marker(self):
        with self.assertRaises(trap.SnapshotError):
            trap.parse_trap(bytes(0x200))

    def test_instruction_uses_kseg_physical_alias(self):
        data = bytearray(0x200)
        struct.pack_into("<I", data, 0x100, 0x8F88036C)
        self.assertEqual(trap.instruction_word(data, 0x80000100), 0x8F88036C)

    def test_gp_relative_effective_address(self):
        frame = trap.parse_trap(self.make_snapshot())
        regs = list(frame.regs)
        regs[28] = 0x0C6850F4
        frame = trap.TrapFrame(frame.sr, frame.cause, frame.epc,
                               frame.bad_vaddr, tuple(regs), frame.lo,
                               frame.hi, frame.reserved, frame.xclass)
        self.assertEqual(trap.load_effective_address(0x8F88036C, frame),
                         0x0C685460)


class PlausibilityTests(unittest.TestCase):
    """A trap marker can be set with no usable frame behind it (build 107-234)."""

    def frame(self, *, epc, sp, ra):
        regs = [0] * 32
        regs[29] = sp
        regs[31] = ra
        return trap.TrapFrame(0x1040EC03, 0x1008, epc, 0, tuple(regs),
                              0, 0, 0, 0x101)

    def test_live_context_is_accepted(self):
        # The live null-pointer trap: sane epc, sp and ra.
        frame = self.frame(epc=0x80063F68, sp=0x80134260, ra=0x8009B348)
        self.assertEqual(trap.implausible(frame), [])

    def test_bootstrap_frame_is_rejected(self):
        # Build 107-234 dies in bootstrap: sp and ra zero, epc in low memory.
        frame = self.frame(epc=0x00001008, sp=0, ra=0)
        reasons = trap.implausible(frame)
        self.assertTrue(any('sp is zero' in r for r in reasons))
        self.assertTrue(any('ra is zero' in r for r in reasons))
        self.assertTrue(any('cached image window' in r for r in reasons))

    def test_sp_outside_cached_window_is_rejected(self):
        frame = self.frame(epc=0x80063F68, sp=0x0C685460, ra=0x8009B348)
        self.assertTrue(trap.implausible(frame))

    def test_sp_above_declared_stack_top_is_rejected(self):
        class Layout:
            stack_top = 0x801343B0
        frame = self.frame(epc=0x80063F68, sp=0x80200000, ra=0x8009B348)
        reasons = trap.implausible(frame, Layout())
        self.assertTrue(any('above the declared stack top' in r for r in reasons))

    def test_archived_task3_trap_still_analysed(self):
        """The real stack overflow must not be filtered out by the guard."""
        frame = self.frame(epc=0x800B86C0, sp=0x801300E8, ra=0x800B86C0)
        self.assertEqual(trap.implausible(frame), [])


if __name__ == "__main__":
    unittest.main()
