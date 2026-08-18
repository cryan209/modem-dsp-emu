"""The receive band limit, and the measurement that keeps it switched off.

`AnalogLineInterface.receive()` had no band limit at all: it handed u-law
codewords to the DSP with only a gain and the hybrid echo applied, where a real
path (the central office's reconstruction filter, the loop, the modem's own
anti-alias filter) passes nothing at 4 kHz. That is a genuine gap in the model.

It is also *not* what stops the gold V.90 Phase-3 tone from being read, and
these assert why, because it is the kind of thing that gets re-argued. The tone
is a period-6 square at 8 kHz, which is a construction aimed squarely at a
sign-and-magnitude detector: constant magnitude on every sample and an
unambiguous three-positive/three-negative pattern. Band-limiting leaves a
period-6 sine whose samples sit on its own zero crossings, so a third of them
fall under the |x| >= 0x200 floor `PM 0x2FD1` applies.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))

from analog_line import AnalogLineInterface

DETECTOR_FLOOR = 0x200


def square(periods: int) -> list[float]:
    return [924.0, 924, 924, -924, -924, -924] * periods


def convolve(taps, signal):
    out = []
    for index in range(len(taps), len(signal)):
        out.append(sum(tap * signal[index - offset]
                       for offset, tap in enumerate(taps)))
    return out


class BandLimitIsOffByDefault(unittest.TestCase):

    def test_default_leaves_the_sample_untouched(self):
        line = AnalogLineInterface()
        line.set_connected(True)
        line.set_hook(True)
        self.assertEqual(line.rx_bandlimit_hz, 0.0)
        for value in (0, 924, -924, 12345):
            self.assertEqual(line.receive(value), value)


class NyquistNull(unittest.TestCase):

    def test_an_even_tap_design_nulls_exactly_at_nyquist(self):
        taps = AnalogLineInterface._voiceband_taps(4000.0, 24)
        self.assertEqual(len(taps) % 2, 0)
        nyquist = sum(tap * (-1) ** index for index, tap in enumerate(taps))
        self.assertAlmostEqual(nyquist, 0.0, places=12)
        self.assertAlmostEqual(sum(taps), 1.0, places=12)

    def test_disabled_when_cutoff_is_zero(self):
        self.assertEqual(AnalogLineInterface._voiceband_taps(0.0, 24), ())


class WhyItStaysOff(unittest.TestCase):

    def test_the_raw_square_clears_the_floor_on_every_sample(self):
        self.assertTrue(all(abs(v) >= DETECTOR_FLOOR for v in square(20)))
        self.assertEqual({abs(v) for v in square(20)}, {924.0})

    def test_band_limiting_puts_a_third_of_the_samples_under_the_floor(self):
        taps = AnalogLineInterface._voiceband_taps(4000.0, 24)
        filtered = convolve(taps, square(40))
        under = sum(1 for v in filtered if abs(v) < DETECTOR_FLOOR)
        self.assertGreater(under / len(filtered), 0.30)
        self.assertLess(under / len(filtered), 0.36)
