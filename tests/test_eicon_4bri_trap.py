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


if __name__ == "__main__":
    unittest.main()
