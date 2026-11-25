"""Preserve negotiated PCMU symbols at the direct PRI RTP boundary."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from dial_tikrnl_drive import Card, V90D_ID, sport_rx_word, linear_to_mulaw


class V90DTxPCM16Tests(unittest.TestCase):
    def convert(self, sample, **changes):
        state = dict(firmware_set='pri117', resident=V90D_ID, modem_law='pcmu')
        state.update(changes)
        return Card._v90d_tx_pcm16(SimpleNamespace(**state), sample)

    def test_nonzero_pcmu_alphabet_round_trips_to_the_same_wire_code(self):
        # +/-2 is deliberately reserved for signed zero by the serializer.
        for code in range(256):
            word = sport_rx_word(code)
            sample = word - 65536 if word & 32768 else word
            if abs(sample) <= 2:
                continue
            self.assertEqual(linear_to_mulaw(self.convert(sample)), code)

    def test_signed_zero_tokens_do_not_become_ucode_one(self):
        self.assertEqual(linear_to_mulaw(self.convert(2)), 0xff)
        self.assertEqual(linear_to_mulaw(self.convert(-2)), 0x7f)

    def test_full_scale_and_context_restore_pointer(self):
        self.assertEqual(self.convert(8031), 32124)
        self.assertEqual(self.convert(-8031), -32124)
        self.assertEqual(self.convert(0x3764), 0x3764)

    def test_other_pages_laws_and_analog_output_keep_their_domain(self):
        for change in ({'resident': 0x0260}, {'modem_law': 'pcma'},
                       {'firmware_set': 'analog109'}):
            self.assertEqual(self.convert(2079, **change), 2079)

    def test_channel_law_configuration_still_selects_pcmu(self):
        card = SimpleNamespace(dm=[0] * 0x4000)
        Card.configure_g711_law(card, 'pcmu')
        self.assertEqual(card.dm[0x3309], 0x35be)
        self.assertEqual(card.dm[0x2f22], 0x3c27)
        Card.configure_g711_law(card, 'pcma')
        self.assertEqual(card.dm[0x2f22], 0x3c07)


if __name__ == '__main__':
    unittest.main()
