import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from v34_info import (CRC_BITS, SAMPLE_RATE, SYNC_BITS, SYNC_CODE, Frame,
                      crc_bits, decode, decode_ulaw, find_frames)


def frame_bits(payload, lead=20, tail=10):
    """The wire form the framers at PM 0x3520 accept."""
    bits = [1] * lead
    bits += [(SYNC_CODE >> (SYNC_BITS - 1 - i)) & 1 for i in range(SYNC_BITS)]
    bits += list(payload)
    crc = crc_bits(list(payload))
    bits += [(crc >> i) & 1 for i in range(CRC_BITS)]
    return bits + [1] * tail


def modulate(bits, carrier=1200.0, rate=600.0, phase=0.7, amplitude=4000.0,
             offset_hz=0.0, lead_samples=500):
    """Differentially encoded binary PSK, one phase reversal per set bit."""
    encoded = []
    state = 0
    for bit in bits:
        state ^= bit
        encoded.append(state)
    samples = [0.0] * lead_samples
    span = SAMPLE_RATE / rate
    for index in range(int(span * len(encoded)) + 40):
        slot = int(index / span)
        symbol = encoded[slot] if slot < len(encoded) else 0
        angle = 2.0 * math.pi * (carrier + offset_hz) * index / SAMPLE_RATE
        samples.append(amplitude * math.cos(angle + phase +
                                            (math.pi if symbol else 0.0)))
    return samples + [0.0] * 500


class CrcTests(unittest.TestCase):
    def test_matches_the_framer_polynomial(self):
        # Reflected 0x8408 preset 0xffff is CRC-16/X-25 without the final
        # complement, so the classic check value is the complement of 0x906e.
        bits = []
        for byte in b'123456789':
            bits.extend((byte >> index) & 1 for index in range(8))
        self.assertEqual(crc_bits(bits) ^ 0xFFFF, 0x906E)

    def test_payload_and_crc_leave_zero_residue(self):
        payload = [1, 0, 1, 1, 0, 0, 0, 1, 1, 1, 0]
        crc = crc_bits(payload)
        trailing = [(crc >> index) & 1 for index in range(CRC_BITS)]
        found = find_frames(frame_bits(payload), range(len(payload),
                                                       len(payload) + 1))
        self.assertEqual(found, [(30, tuple(payload))])
        self.assertEqual(len(trailing), CRC_BITS)


class PackingTests(unittest.TestCase):
    def frame(self, payload):
        return Frame(sample=0, seconds=0.0, payload=tuple(payload),
                     carrier=1200.0, bit_rate=600.0, offset=0.0, inverted=False)

    def test_lsb_first_reproduces_the_captured_low_nibble(self):
        # abifix-2's answer-side message begins 1111 and the card published
        # DM(0x3F88)=0x000f; abifix-3's begins 0000 and it published 0x0000.
        failing = self.frame([1, 1, 1, 1] + [0] * 12)
        working = self.frame([0] * 13 + [1, 1, 1])
        self.assertEqual(failing.pack(False)[0] & 0x000F, 0x000F)
        self.assertEqual(working.pack(False)[0] & 0x000F, 0x0000)

    def test_both_orders_agree_that_bits_six_to_twelve_are_clear(self):
        working = self.frame([0] * 13 + [1, 1, 1])
        self.assertEqual(working.fields(False)[2], 0)
        self.assertEqual(working.fields(True)[2], 0)

    def test_msb_first_is_the_bit_reversal_of_lsb_first(self):
        payload = [1, 0, 1, 1, 0, 0, 0, 1, 0, 1, 1, 0, 1, 0, 0, 1]
        frame = self.frame(payload)
        reversed_word = int(''.join(str(bit) for bit in payload), 2)
        self.assertEqual(frame.pack(True)[0], reversed_word)
        self.assertEqual(frame.pack(False)[0],
                         int(''.join(str(bit) for bit in reversed(payload)), 2))


class DemodulationTests(unittest.TestCase):
    def setUp(self):
        random.seed(11)
        self.payload = [random.getrandbits(1) for _ in range(38)]
        self.wire = frame_bits(self.payload)

    def recovered(self, samples, carrier=1200.0):
        frames = decode(samples, carrier, 600.0, 16, range(10, 121), 0)
        return [list(frame.payload) for frame in frames]

    def test_clean_signal_round_trips(self):
        self.assertIn(self.payload, self.recovered(modulate(self.wire)))

    def test_survives_noise_and_a_carrier_phase_offset(self):
        random.seed(5)
        samples = [value + random.gauss(0.0, 1200.0)
                   for value in modulate(self.wire, phase=2.6)]
        self.assertIn(self.payload, self.recovered(samples))

    def test_survives_a_carrier_frequency_error(self):
        samples = modulate(self.wire, offset_hz=40.0)
        self.assertIn(self.payload, self.recovered(samples))

    def test_answer_side_carrier_round_trips(self):
        samples = modulate(self.wire, carrier=2400.0)
        self.assertIn(self.payload, self.recovered(samples, carrier=2400.0))

    def test_the_other_direction_is_not_decoded_as_this_one(self):
        # The call and answer modems do not share a carrier.  Decoding a
        # capture at one carrier alone is what made the peer look silent.
        samples = modulate(self.wire, carrier=2400.0)
        self.assertNotIn(self.payload, self.recovered(samples, carrier=1200.0))

    def test_noise_alone_produces_no_frames(self):
        random.seed(19)
        noise = [random.gauss(0.0, 2000.0) for _ in range(SAMPLE_RATE * 5)]
        self.assertEqual(self.recovered(noise), [])


class CodecTests(unittest.TestCase):
    def test_ulaw_endpoints(self):
        self.assertEqual(decode_ulaw(0xFF), 0)
        self.assertEqual(decode_ulaw(0x7F), 0)
        self.assertGreater(decode_ulaw(0x80), 30000)
        self.assertLess(decode_ulaw(0x00), -30000)


if __name__ == '__main__':
    unittest.main()
