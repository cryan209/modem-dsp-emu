"""Recovered build-109 POTS management descriptor layout."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analog_pots_layout import descriptors


class AnalogPotsLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        image = ROOT / "docs/firmware/build-109/te_dmlt.am"
        cls.records = {(name, field): callback
                       for name, _, _, callback, field in descriptors(image)}

    def test_audio_timeslot_mask_callback(self):
        self.assertEqual(self.records[("AudioTS# Enable", 0x050)], 0x80099D1C)

    def test_audio_channel_field(self):
        self.assertEqual(self.records[("AudioCh# Enable", 0x050)], 0x80096488)

    def test_hook_inputs_share_physical_callback(self):
        self.assertEqual(self.records[("rxhook", 0x4D4)], 0x8009B294)
        self.assertEqual(self.records[("txhook", 0x07B)], 0x8009B294)

    def test_codec_gain_fields(self):
        self.assertEqual(self.records[("Playing Gain dB", 0x29C)], 0x80095818)
        self.assertEqual(self.records[("Recording Gain dB", 0x29E)], 0x80095818)


if __name__ == "__main__":
    unittest.main()
