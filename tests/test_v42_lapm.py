import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from v42_lapm import HdlcDecoder, LapmEndpoint, encode_frame, fcs16


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
        endpoint = LapmEndpoint(log=logs.append)
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
        endpoint = LapmEndpoint(log=lambda _: None)
        endpoint.take(8)
        endpoint.connected = True
        # N(S)=0, N(R)=0, payload "hi".
        endpoint.feed(encode_frame(b'\x03\x00\x00hi'))
        decoder = HdlcDecoder()
        frames = decoder.feed(endpoint.take(128))
        self.assertIn(b'\x03\x01\x02', frames)
        self.assertEqual(endpoint.rx_data, b'hi')


if __name__ == '__main__':
    unittest.main()
