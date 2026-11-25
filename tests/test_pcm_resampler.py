"""Verify analogue PCM band fidelity and streaming sample cadence."""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from analog_kernel_dispatch import RationalResampler


class PcmResamplerTest(unittest.TestCase):
    def test_clock_ratio_from_first_sample_without_startup_burst(self):
        for up, down in ((6, 5), (5, 6)):
            converter = RationalResampler(up, down)
            total = 0
            for count in range(1, 2001):
                outputs = converter.push(count)
                self.assertLessEqual(len(outputs), math.ceil(up / down))
                total += len(outputs)
                self.assertEqual(total, math.ceil(count * up / down))
            self.assertLess(len(converter.history), 4 * converter.taps)

    def test_constant_input_has_no_phase_dependent_gain(self):
        for up, down in ((6, 5), (5, 6)):
            converter = RationalResampler(up, down)
            outputs = [y for _ in range(2000) for y in converter.push(-8031)]
            for y in outputs[200:]:
                self.assertAlmostEqual(y, -8031, places=8)

    def test_upper_modem_band_preserves_gain_and_rejects_images(self):
        # A full one-second output interval makes the sine/cosine fit
        # orthogonal. Discard a second to exclude filter startup.
        for up, down, fs in ((6, 5, 8000), (5, 6, 9600)):
            output_rate = fs * up // down
            for frequency in (1000, 3000, 3400, 3600):
                with self.subTest(ratio=(up, down), frequency=frequency):
                    converter = RationalResampler(up, down)
                    outputs = [y for n in range(fs * 2)
                               for y in converter.push(
                                   10000 * math.sin(2 * math.pi * frequency * n / fs))]
                    outputs = outputs[output_rate:2 * output_rate]
                    sines = [math.sin(2 * math.pi * frequency * n / output_rate)
                             for n in range(len(outputs))]
                    cosines = [math.cos(2 * math.pi * frequency * n / output_rate)
                               for n in range(len(outputs))]
                    a = 2 * sum(x * y for x, y in zip(outputs, sines)) / len(outputs)
                    b = 2 * sum(x * y for x, y in zip(outputs, cosines)) / len(outputs)
                    gain_db = 20 * math.log10(math.hypot(a, b) / 10000)
                    residual = sum((y - a * s - b * c)**2
                                   for y, s, c in zip(outputs, sines, cosines))
                    signal = sum((a * s + b * c)**2 for s, c in zip(sines, cosines))
                    snr_db = 10 * math.log10(signal / residual)
                    self.assertLess(abs(gain_db), 0.01)
                    self.assertGreater(snr_db, 70)


if __name__ == '__main__':
    unittest.main()
