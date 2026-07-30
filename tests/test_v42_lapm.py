import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from v42_lapm import (ADP_C, ADP_E, ADP_V42_SUPPORTED, HdlcDecoder,
                      LapmEndpoint, ODP_EVEN, ODP_ODD, encode_frame,
                      fcs16)


class HdlcTests(unittest.TestCase):
    def test_known_x25_fcs(self):
        self.assertEqual(fcs16(b'123456789'), 0x906E)

    def test_fragmented_stuffed_round_trip(self):
        body = bytes((0x03, 0xAF, 0xFF, 0xF8, 0x7E, 0x00))
        wire = encode_frame(body)
        decoder = HdlcDecoder()
        got = []
        for start in range(0, len(wire), 7):
            got.extend(decoder.feed(wire[start:start + 7]))
        self.assertEqual(got, [body])
        self.assertEqual(decoder.good, 1)
        self.assertEqual(decoder.bad_fcs, 0)

    def test_bad_fcs_is_rejected(self):
        wire = encode_frame(b'\x03\xaf\x82')
        wire[12] ^= 1
        decoder = HdlcDecoder()
        self.assertEqual(decoder.feed(wire), [])
        self.assertEqual(decoder.bad_fcs, 1)


class LapmTests(unittest.TestCase):
    def test_xid_and_sabme_response(self):
        logs = []
        endpoint = LapmEndpoint(log=logs.append, detect=False)
        # Drain constructor's idle flag, then send an XID command and SABME(P).
        endpoint.take(8)
        xid = b'\x03\xaf\x82\x80\x00\x00'
        endpoint.feed(encode_frame(xid) + encode_frame(b'\x03\x7f'))
        wire = endpoint.take(len(encode_frame(xid)) + len(encode_frame(b'\x03\x73')) + 24)
        decoder = HdlcDecoder()
        frames = decoder.feed(wire)
        self.assertIn(xid, frames)
        self.assertIn(b'\x03\x73', frames)
        self.assertTrue(endpoint.connected)
        self.assertEqual(endpoint.stats.xid_rx, 1)
        self.assertEqual(endpoint.stats.sabme_rx, 1)

    def test_i_frame_is_acknowledged(self):
        endpoint = LapmEndpoint(log=lambda _: None, detect=False)
        endpoint.take(8)
        endpoint.connected = True
        # N(S)=0, N(R)=0, payload "hi".
        endpoint.feed(encode_frame(b'\x03\x00\x00hi'))
        decoder = HdlcDecoder()
        frames = decoder.feed(endpoint.take(128))
        self.assertIn(b'\x03\x01\x02', frames)
        self.assertEqual(endpoint.rx_data, b'hi')


class LapmTransmitTests(unittest.TestCase):
    """The outbound I-frame path: window, acknowledgement and recovery."""

    def setUp(self):
        self.endpoint = LapmEndpoint(log=lambda _: None, window=3, n401=4, detect=False)
        self.endpoint.take(8)
        # SABME establishes the link and zeroes both sequence directions.
        self.endpoint.feed(encode_frame(b'\x03\x7f'))
        self.decoder = HdlcDecoder()

    def frames(self, bits=4096):
        return self.decoder.feed(self.endpoint.take(bits))

    @staticmethod
    def i_frames(frames):
        return [f for f in frames if len(f) >= 3 and not f[1] & 0x01]

    def test_send_segments_into_n401_sized_i_frames(self):
        self.endpoint.send(b'ABCDEFGH')
        sent = self.i_frames(self.frames())
        # n401=4, so two full frames; window=3 allows both.
        self.assertEqual([f[3:] for f in sent], [b'ABCD', b'EFGH'])
        self.assertEqual([(f[1] >> 1) & 0x7F for f in sent], [0, 1])
        self.assertEqual(self.endpoint.stats.i_tx, 2)

    def test_window_limits_outstanding_frames(self):
        self.endpoint.send(b'A' * 40)  # 10 frames of 4 octets
        sent = self.i_frames(self.frames())
        self.assertEqual(len(sent), 3)          # window=3
        self.assertEqual(self.endpoint.outstanding, 3)
        # RR acknowledging N(R)=2 releases two and admits two more.
        self.endpoint.feed(encode_frame(b'\x03\x01\x04'))
        self.assertEqual(self.endpoint.va, 2)
        self.assertEqual(self.endpoint.outstanding, 1)
        sent = self.i_frames(self.frames())
        self.assertEqual([(f[1] >> 1) & 0x7F for f in sent], [3, 4])

    def test_i_frame_carries_current_receive_state(self):
        # Receive one I frame, then send: our N(R) must acknowledge it.
        self.endpoint.feed(encode_frame(b'\x03\x00\x00hi'))
        self.endpoint.send(b'ok')
        sent = self.i_frames(self.frames())
        self.assertEqual(len(sent), 1)
        self.assertEqual((sent[0][2] >> 1) & 0x7F, 1)  # N(R) = V(R) = 1

    def test_rej_retransmits_from_nr(self):
        self.endpoint.send(b'AAAABBBBCCCC')
        self.assertEqual(len(self.i_frames(self.frames())), 3)
        self.endpoint.stats.i_retx = 0
        # REJ with N(R)=1: frame 0 is acknowledged, resend from 1.
        self.endpoint.feed(encode_frame(b'\x03\x09\x02'))
        resent = self.i_frames(self.frames())
        self.assertEqual([f[3:] for f in resent], [b'BBBB', b'CCCC'])
        self.assertEqual(self.endpoint.stats.i_retx, 2)
        self.assertEqual(self.endpoint.stats.rej_rx, 1)

    def test_rnr_stops_the_window_and_rr_resumes_it(self):
        self.endpoint.feed(encode_frame(b'\x03\x05\x00'))  # RNR N(R)=0
        self.endpoint.send(b'AAAA')
        self.assertEqual(self.i_frames(self.frames()), [])
        self.assertTrue(self.endpoint.peer_busy)
        self.endpoint.feed(encode_frame(b'\x03\x01\x00'))  # RR N(R)=0
        self.assertFalse(self.endpoint.peer_busy)
        self.assertEqual([f[3:] for f in self.i_frames(self.frames())], [b'AAAA'])

    def test_stalled_window_polls_then_retransmits(self):
        endpoint = LapmEndpoint(log=lambda _: None, window=2, n401=4,
                                poll_after=3, retransmit_after=5, detect=False)
        endpoint.take(8)
        endpoint.feed(encode_frame(b'\x03\x7f'))
        endpoint.send(b'AAAA')
        decoder = HdlcDecoder()
        decoder.feed(endpoint.take(2048))       # first transmission
        self.assertEqual(endpoint.stats.poll_tx, 0)
        for _ in range(3):
            decoder.feed(endpoint.take(2048))
        self.assertEqual(endpoint.stats.poll_tx, 1)   # probed, not resent
        self.assertEqual(endpoint.stats.i_retx, 0)
        for _ in range(3):
            decoder.feed(endpoint.take(2048))
        self.assertGreaterEqual(endpoint.stats.i_retx, 1)  # then went back N

    def test_sabme_resets_transmit_state(self):
        self.endpoint.send(b'AAAA')
        self.frames()
        self.assertEqual(self.endpoint.vs, 1)
        self.endpoint.feed(encode_frame(b'\x03\x7f'))
        self.assertEqual((self.endpoint.vs, self.endpoint.va), (0, 0))
        self.assertEqual(self.endpoint.unacked, {})

    def test_sequence_numbers_wrap_at_128(self):
        endpoint = LapmEndpoint(log=lambda _: None, window=1, n401=1, detect=False)
        endpoint.take(8)
        endpoint.feed(encode_frame(b'\x03\x7f'))
        endpoint.send(b'x' * 130)
        for expected in range(130):
            endpoint.take(512)
            self.assertEqual(endpoint.vs, (expected + 1) & 0x7F)
            # Acknowledge each frame so the window of 1 keeps moving.
            nr = (expected + 1) & 0x7F
            endpoint.feed(encode_frame(bytes((0x03, 0x01, (nr << 1) & 0xFE))))
        self.assertEqual(endpoint.tx_stream, b'')


class DetectionPhaseTests(unittest.TestCase):
    """V.42 7.2.1 answerer role: mark, then ADP once the ODP is seen."""

    MARK = [1] * 12

    def odp(self, count):
        """`count` DC1s of alternating parity, separated by mark fill."""
        bits = []
        for index in range(count):
            bits += list(ODP_EVEN if index % 2 == 0 else ODP_ODD) + self.MARK
        return bits

    def test_answerer_starts_on_mark_not_flags(self):
        endpoint = LapmEndpoint(log=lambda _: None)
        self.assertEqual(endpoint.detection, 'mark')
        self.assertEqual(endpoint.take(64), [1] * 64)

    def test_four_alternating_dc1s_trigger_the_adp(self):
        endpoint = LapmEndpoint(log=lambda _: None)
        endpoint.feed(self.odp(3))
        self.assertEqual(endpoint.detection, 'mark')   # three is not enough
        endpoint.feed(self.odp(1) if endpoint._odp_parity else self.odp(2)[10:])
        self.assertEqual(endpoint.detection, 'adp')
        self.assertEqual(endpoint.stats.adp_tx, 1)

    def test_adp_is_v42_supported_and_repeated_ten_times(self):
        endpoint = LapmEndpoint(log=lambda _: None)
        endpoint.feed(self.odp(4))
        sent = endpoint.take(len(ADP_V42_SUPPORTED) * 10)
        self.assertEqual(sent, list(ADP_V42_SUPPORTED) * 10)
        # (E) and (C) are both present, which is what distinguishes "V.42
        # supported" from the (E)+(Null) "no error-correcting protocol" pattern.
        self.assertIn(list(ADP_E), [sent[:len(ADP_E)]])
        self.assertIn(list(ADP_C), [sent[len(ADP_E) + 12:][:len(ADP_C)]])

    def test_flags_follow_the_adp(self):
        endpoint = LapmEndpoint(log=lambda _: None)
        endpoint.feed(self.odp(4))
        endpoint.take(len(ADP_V42_SUPPORTED) * 10)
        self.assertEqual(endpoint.detection, 'adp')
        tail = endpoint.take(16)
        self.assertEqual(endpoint.detection, 'protocol')
        self.assertEqual(tail, list(FLAG := (0, 1, 1, 1, 1, 1, 1, 0)) * 2)

    def test_repeated_same_parity_dc1s_do_not_count(self):
        endpoint = LapmEndpoint(log=lambda _: None)
        for _ in range(8):
            endpoint.feed(list(ODP_EVEN) + self.MARK)
        self.assertEqual(endpoint.detection, 'mark')

    def test_lapm_frame_enters_protocol_without_an_odp(self):
        # An originator with detection disabled goes straight to SABME.
        endpoint = LapmEndpoint(log=lambda _: None)
        endpoint.feed(encode_frame(b'\x03\x7f'))
        self.assertEqual(endpoint.detection, 'protocol')
        self.assertTrue(endpoint.connected)

    def test_detection_timeout_stays_on_mark(self):
        endpoint = LapmEndpoint(log=lambda _: None, detect_timeout=3)
        for _ in range(6):
            self.assertEqual(set(endpoint.take(8)), {1})
        self.assertEqual(endpoint.detection, 'mark')

    def test_detect_false_reproduces_the_old_behaviour(self):
        endpoint = LapmEndpoint(log=lambda _: None, detect=False)
        self.assertEqual(endpoint.detection, 'protocol')
        self.assertEqual(endpoint.take(8), [0, 1, 1, 1, 1, 1, 1, 0])


if __name__ == '__main__':
    unittest.main()
