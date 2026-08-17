"""The tone probe must tell a 2400 Hz carrier from broadband, and must place
each transmit frame under the outer state that was in force when it left."""
import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))

import v90a_tx_tone_probe as probe


def tone(freq, n, fs=8000, amp=8000):
    return [int(amp * math.sin(2 * math.pi * freq * i / fs)) for i in range(n)]


class MetricsTests(unittest.TestCase):
    def test_pure_2400_is_dominant_and_tonal(self):
        dominant, flat = probe.frame_metrics(tone(2400, 320), 2400.0, 8000)
        self.assertTrue(dominant)
        self.assertLess(flat, 0.2)

    def test_white_noise_is_neither(self):
        random.seed(1)
        noise = [random.randint(-8000, 8000) for _ in range(320)]
        dominant, flat = probe.frame_metrics(noise, 2400.0, 8000)
        self.assertFalse(dominant)
        self.assertGreater(flat, 0.4)

    def test_off_tone_is_not_dominant(self):
        # a clean 1200 Hz tone must not read as 2400 Hz dominant
        dominant, _ = probe.frame_metrics(tone(1200, 320), 2400.0, 8000)
        self.assertFalse(dominant)

    def test_goertzel_power_never_negative(self):
        # bin-edge rounding used to drive the closed form slightly negative
        for f in (125, 2375, 2400, 2625, 3999):
            self.assertGreaterEqual(
                probe.goertzel_power(tone(2400, 320), f, 8000), 0.0)

    def test_flatness_bounded(self):
        for frame in (tone(2400, 320), [0] * 320, tone(400, 320)):
            _, flat = probe.frame_metrics(frame, 2400.0, 8000)
            self.assertGreaterEqual(flat, 0.0)
            self.assertLessEqual(flat, 1.0)


class TimelineTests(unittest.TestCase):
    def test_state_at_takes_last_transition_at_or_before(self):
        timeline = [(100, 0x50), (200, 0x60), (400, 0x92)]
        self.assertIsNone(probe.state_at(timeline, 50))
        self.assertEqual(probe.state_at(timeline, 100), 0x50)
        self.assertEqual(probe.state_at(timeline, 250), 0x60)
        self.assertEqual(probe.state_at(timeline, 9999), 0x92)

    def test_state_timeline_parses_v90a_trace(self, ):
        text = (
            "noise\n"
            "[v90a] sample 74765 (9.3s): optr=19f2 state=0050 dwell=0001 x\n"
            "[v90a] sample 100372 (12.5s): optr=17cd state=0092 dwell=ffff x\n")
        p = Path(self._tmp())
        p.write_text(text)
        self.assertEqual(probe.state_timeline(p),
                         [(74765, 0x50), (100372, 0x92)])

    def _tmp(self):
        import tempfile
        fd, name = tempfile.mkstemp(suffix='.log')
        import os
        os.close(fd)
        self.addCleanup(lambda: Path(name).unlink(missing_ok=True))
        return name


if __name__ == '__main__':
    unittest.main()
