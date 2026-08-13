"""Tone probe DSP primitives."""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from modem_tone_probe import goertzel, ulaw


class ModemToneProbeTests(unittest.TestCase):
    def test_mulaw_silence(self):
        self.assertEqual(ulaw(0xFF), 0)

    def test_goertzel_finds_generated_tone(self):
        samples = [int(10000 * math.sin(2 * math.pi * 820 * n / 8000))
                   for n in range(800)]
        at_tone = goertzel(samples, 820)
        away = goertzel(samples, 1200)
        self.assertGreater(at_tone, away * 1000)


if __name__ == "__main__":
    unittest.main()
