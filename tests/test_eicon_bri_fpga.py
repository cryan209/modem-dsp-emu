"""Replay the real programming waveform; do not infer DONE from byte count."""
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from eicon_bri_card import BriCard
from eicon_bri_fpga import BriFpgaSerial, PROG, CS, CCLK, DATA
from eicon_bri_fpga_probe import program


def clock(port, data):
    for byte in data:
        for bit in range(7, -1, -1):
            value = CS | PROG | (DATA if byte & (1 << bit) else 0)
            port.write(value)
            port.write(value | CCLK)
            port.write(value | CCLK)
            port.write(value)


class FpgaTest(unittest.TestCase):
    def setUp(self):
        self.image = (ROOT / 'docs/divas4linux-master/firmware/ds4bri2.bit').read_bytes()
        self.stream = self.image[self.image.index(255):]
        self.fixture = json.loads((ROOT / 'tests/data/bri_fpga_card12196.json').read_text())

    def test_stock_gpio_waveform_matches_all_physical_samples(self):
        with BriCard() as card:
            actual = program(lambda: card.read(0, 0x6e, 2), lambda value: card.write(0, 0x6e, 2, value),
                             self.image, sleep=lambda _: None)
            for key, value in actual.items():
                self.assertEqual(value, self.fixture[key], key)
            self.assertEqual(card.fpga.clocks, 233652)
            self.assertEqual(card.read(0, 0x68, 4), self.fixture['intcsr_before'])
            card.write(0, 0x68, 4, 0x0f010800)
            self.assertEqual(card.read(0, 0x68, 4), self.fixture['intcsr_masked'])
            self.assertFalse(card.irq_asserted)
            self.assertEqual(card.read(3, 0x200, 4), self.fixture['risc_after'])
            self.assertEqual(card.read(3, 0x400, 4), self.fixture['irq_after'])
            card.write(3, 0x400, 4, 0)
            self.assertEqual(card.read(0, 0x68, 4), self.fixture['intcsr_after_ack'])

    def test_duplicate_high_writes_are_not_clocks_and_reset_aborts_configuration(self):
        port = BriFpgaSerial();port.write(CS);port.write(CS | PROG)
        clock(port, self.stream[:64])
        self.assertEqual(port.clocks, 512)
        self.assertFalse(port.done)
        port.write(CS)
        self.assertEqual(port.clocks, 0)
        port.write(CS | PROG)
        clock(port, self.stream)
        self.assertTrue(port.done)

    def test_unknown_personality_cannot_enable_the_fixed_diva_logic(self):
        port = BriFpgaSerial();port.write(CS);port.write(CS | PROG)
        changed = bytearray(self.stream);changed[100] ^= 1
        clock(port, changed)
        self.assertFalse(port.done)
        self.assertEqual(port.error, 'unsupported FPGA configuration personality')

    def test_mips_cannot_run_during_configuration_and_eeprom_controls_remain_explicit(self):
        with BriCard() as card:
            card.write(0, 0x6e, 2, CS)
            with self.assertRaises(RuntimeError):card.write(3, 0x200, 4, 3)
            with self.assertRaises(NotImplementedError):card.write(0, 0x6e, 2, CS | PROG | 0x200)
            with self.assertRaises(NotImplementedError):card.write(0, 0x6e, 1, CS)


if __name__ == '__main__':
    unittest.main()
