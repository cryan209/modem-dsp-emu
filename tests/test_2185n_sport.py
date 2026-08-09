"""Bit-exact ADSP-2185N SPORT receive companding."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from dial_tikrnl_drive import sport_rx_word


def signed16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def reference_mulaw(code: int) -> int:
    """Right-justified 14-bit G.711 µ-law expansion."""
    value = (~code) & 0xFF
    magnitude = (((value & 0x0F) << 1) + 33) << ((value >> 4) & 7)
    magnitude -= 33
    return -magnitude if value & 0x80 else magnitude


def reference_alaw(code: int) -> int:
    """Right-justified 13-bit G.711 A-law expansion."""
    value = code ^ 0x55
    mantissa = value & 0x0F
    segment = (value >> 4) & 7
    if segment == 0:
        magnitude = (mantissa << 1) + 1
    elif segment == 1:
        magnitude = (mantissa << 1) + 33
    else:
        magnitude = ((mantissa << 1) + 33) << (segment - 1)
    return magnitude if value & 0x80 else -magnitude


class SportCompandingTests(unittest.TestCase):
    def test_all_mulaw_codes_match_right_justified_14_bit_reference(self):
        for code in range(256):
            with self.subTest(code=code):
                self.assertEqual(signed16(sport_rx_word(code, "pcmu")),
                                 reference_mulaw(code))

    def test_all_alaw_codes_match_right_justified_13_bit_reference(self):
        for code in range(256):
            with self.subTest(code=code):
                self.assertEqual(signed16(sport_rx_word(code, "pcma")),
                                 reference_alaw(code))

    def test_documented_effective_widths(self):
        mulaw = [abs(reference_mulaw(code)) for code in range(256)]
        alaw = [abs(reference_alaw(code)) for code in range(256)]
        self.assertEqual(max(mulaw), 8031)
        self.assertEqual(max(alaw), 4032)


if __name__ == "__main__":
    unittest.main()
