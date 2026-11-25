"""Measured PLX reset/reload, aperture isolation and preserved SDRAM/FPGA."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from eicon_bri_card import BriCard


class BriResetTest(unittest.TestCase):
    def test_physical_spare_reset_and_interrupt_trace(self):
        fixture = json.loads((ROOT / 'tests/data/bri_reset_card12196.json').read_text())
        with BriCard() as card:
            def compare(expected):
                for name, offset in (('las0rr', 0), ('lbrd0', 0x18), ('mabr0', 0xac)):
                    self.assertEqual(card.read(0, offset, 4), expected[name], name)
                for name, offset in (('risc', 0x200), ('irq', 0x400), ('device_reset', 0)):
                    self.assertEqual(card.read(3, offset, 4), expected[name], name)
                self.assertEqual(card.read(0, 0x6e, 2), expected['prom'])
                # Physical local-bus parity-error bit is separately unqualified.
                self.assertEqual(card.read(0, 0x68, 4), expected['intcsr'] & ~0x80)
                self.assertEqual(b''.join(card.read(2, 0x80000+i, 4).to_bytes(4, 'little')
                                         for i in range(0, 16, 4)).hex(), expected['sdram'])
            card.ram[0x80000:0x80010] = bytes.fromhex(fixture['before']['sdram'])
            compare(fixture['before'])
            card.ram[0x80000:0x80010] = b'BRI-RESET-12196!'
            card.write(3, 0x400, 4, 1)
            compare(fixture['pending'])
            for phase, bit in zip(fixture['phases'], (0x4000, 0x2000)):
                pins = card.read(0, 0x6e, 2)
                card.write(0, 0x6e, 2, pins | bit)
                self.assertEqual(card.read(0, 0x6e, 2), phase['asserted_prom'])
                card.write(0, 0x6e, 2, pins & ~bit)
                compare(phase['after'])
            card.write(0, 0x69, 1, 8)
            compare(fixture['local_enabled'])
            card.write(3, 0x400, 4, 1)
            compare(fixture['local_pending'])
            self.assertFalse(card.irq_asserted)
            card.write(3, 0x400, 4, 0)
            compare(fixture['local_acknowledged'])

    def test_missing_reload_blocks_reads_and_writes_and_retains_sdram(self):
        with BriCard() as card:
            card.write(2, 0x80000, 4, 0x12345678)
            card.write(3, 0x200, 4, 3)
            pins = card.read(0, 0x6e, 2)
            card.write(0, 0x6e, 2, pins | 0x4000)
            self.assertFalse(card.cpu_running)
            card.write(0, 0x6e, 2, pins)
            for size in (1, 2, 4):
                self.assertEqual(card.read(2, 0x80000, size), (1 << (size*8))-1)
                card.write(2, 0x80000, size, 0)
            card.write(3, 0x200, 4, 3)
            self.assertFalse(card.cpu_running)
            card.write(0, 0x6e, 2, pins | 0x2000)
            card.write(0, 0x6e, 2, pins)
            self.assertEqual(card.read(2, 0x80000, 4), 0x12345678)
            self.assertTrue(card.fpga.done)
            card.write(3, 0x200, 4, 3)
            self.assertTrue(card.cpu_running)


if __name__ == '__main__':
    unittest.main()
