"""Physical sample format at PRI and Analog modem line boundaries."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from analog_line import AnalogLineInterface
from dial_tikrnl_drive import line_codec_rx_word


class AnalogDaaTests(unittest.TestCase):
    def test_exchange_battery_and_loop_current(self):
        line = AnalogLineInterface(line_voltage=48, loop_current_ma=24)
        self.assertTrue(line.in_service)
        self.assertEqual(line.sensed_voltage, 48)
        self.assertEqual(line.sensed_current_ma, 0)
        self.assertEqual(line.daa_line_status, 0x40)
        self.assertEqual(line.line_voltage_sense, 48)
        line.set_hook(True)
        self.assertEqual(line.sensed_voltage, 9)
        self.assertEqual(line.sensed_current_ma, 24)
        self.assertEqual(line.loop_current_sense, 4)
        self.assertEqual(line.daa_line_status, 0x50)
        self.assertEqual(line.line_voltage_sense, 9)
        line.set_connected(False)
        self.assertFalse(line.in_service)
        self.assertEqual(line.sensed_voltage, 0)
        self.assertEqual(line.sensed_current_ma, 0)
        self.assertEqual(line.daa_line_status, 0)
        self.assertEqual(line.line_voltage_sense, 0)
        self.assertFalse(line.seized)


class LineCodecFormatTests(unittest.TestCase):
    def test_pri_receives_companded_timeslot_octet(self):
        self.assertEqual(line_codec_rx_word("pri117", 0xAB, -12345), 0xAB)

    def test_analog_receives_signed_linear_codec_sample(self):
        self.assertEqual(line_codec_rx_word("analog109", 0xAB, -12345), -12345)

    def test_pri_octet_is_bounded_to_eight_bits(self):
        self.assertEqual(line_codec_rx_word("pri117", 0x12AB, 0), 0xAB)


if __name__ == "__main__":
    unittest.main()
