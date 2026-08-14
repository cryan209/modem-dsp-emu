"""Analogue codec/DAA boundary behavior."""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from analog_line import AnalogLineInterface, DtmfDetector


class AnalogLineInterfaceTests(unittest.TestCase):
    def test_on_hook_line_is_silent(self):
        line = AnalogLineInterface()
        self.assertEqual(line.receive(12000), 0)
        self.assertEqual(line.transmit(12000), 0)

    def test_seized_default_path_is_linear_and_transparent(self):
        line = AnalogLineInterface()
        line.set_hook(True)
        self.assertEqual(line.receive(-12345), -12345)
        self.assertEqual(line.transmit(23456), 23456)

    def test_gains_and_codec_saturation(self):
        line = AnalogLineInterface(rx_gain_db=6.020599913, tx_gain_db=6.020599913)
        line.set_hook(True)
        self.assertEqual(line.receive(1000), 2000)
        self.assertEqual(line.transmit(20000), 32767)

    def test_delayed_hybrid_echo_is_added_to_far_signal(self):
        line = AnalogLineInterface(echo_db=20, echo_delay=1)
        line.set_hook(True)
        self.assertEqual(line.receive(100), 100)
        line.transmit(10000)
        # receive precedes transmit per sample; one-sample delay appears now.
        self.assertEqual(line.receive(100), 1100)

    def test_release_flushes_hybrid_history(self):
        line = AnalogLineInterface(echo_db=20, echo_delay=0)
        line.set_hook(True)
        line.transmit(10000)
        line.set_hook(False)
        line.set_hook(True)
        self.assertEqual(line.receive(0), 0)


class DtmfDetectorTests(unittest.TestCase):
    @staticmethod
    def tone(low, high, milliseconds, level=6000):
        count = milliseconds * 8
        return [int(level * (math.sin(2 * math.pi * low * n / 8000)
                             + math.sin(2 * math.pi * high * n / 8000)))
                for n in range(count)]

    def test_collects_digits_and_finishes_after_interdigit_silence(self):
        detector = DtmfDetector(finish_gap_ms=240)
        waveform = []
        for low, high in ((697, 1209), (770, 1336), (941, 1477)):
            waveform += self.tone(low, high, 120)
            waveform += [0] * 1920
        emitted = []
        for offset in range(0, len(waveform), 160):
            emitted += detector.feed(waveform[offset:offset + 160])
        self.assertEqual(emitted, ['1', '5', '#'])
        self.assertEqual(detector.digits, '15#')
        self.assertTrue(detector.finished)

    def test_bench_tone_replaces_the_modem_transmit(self):
        line = AnalogLineInterface(tone_hz=1000, tone_amplitude=1000,
                                   tone_rate=8000)
        line.set_hook(True)
        emitted = [line.transmit(31000) for _ in range(8000)]
        self.assertLessEqual(max(emitted), 1000)
        self.assertGreaterEqual(max(emitted), 990)
        self.assertGreaterEqual(sum(1 for s in emitted if s == 0), 1)

    def test_bench_tone_cadence_gates_the_burst(self):
        line = AnalogLineInterface(tone_hz=1300, tone_amplitude=800,
                                   tone_rate=8000, tone_on_s=0.6,
                                   tone_off_s=1.4)
        line.set_hook(True)
        emitted = [line.transmit(0) for _ in range(16000)]
        self.assertTrue(any(emitted[:4800]))
        self.assertFalse(any(emitted[4900:15900]))

    def test_bench_tone_start_delays_the_first_burst(self):
        line = AnalogLineInterface(tone_hz=1300, tone_amplitude=800,
                                   tone_rate=8000, tone_start_s=1.5,
                                   tone_on_s=0.6, tone_off_s=1.4)
        line.set_hook(True)
        emitted = [line.transmit(0) for _ in range(20000)]
        self.assertFalse(any(emitted[:11900]))
        self.assertTrue(any(emitted[12100:16600]))

    def test_no_tone_configured_leaves_the_transmit_alone(self):
        line = AnalogLineInterface()
        line.set_hook(True)
        self.assertEqual(line.transmit(4321), 4321)

    def test_rejects_analog_modem_820_hz_tone(self):
        detector = DtmfDetector()
        detector.feed(self.tone(820, 820, 1000, level=3000))
        self.assertEqual(detector.digits, '')
        self.assertFalse(detector.finished)


if __name__ == "__main__":
    unittest.main()
