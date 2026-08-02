import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from v42_lapm import (HdlcDecoder, LapmEndpoint, XidParameters, encode_frame,
                      encode_xid_parameters, parse_xid_parameters)
from v44 import V44Decoder, V44Encoder, V44Error, V44Parameters, _BitWriter


class V44XidTests(unittest.TestCase):
    CX_OFFER = bytes.fromhex(
        '8280001303038a8900050204000602040007010f08010f'
        'ff40035634344101034201034302020044020200450120460120'
        '4702040048020400')

    def test_cx_user_data_offer_is_encoded_exactly(self):
        params = XidParameters(optional_functions_octets=3,
                               v44=V44Parameters(capability=3))
        self.assertEqual(encode_xid_parameters(params), self.CX_OFFER)
        self.assertEqual(parse_xid_parameters(self.CX_OFFER), params)

    def test_a_directional_offer_is_complemented_in_the_response(self):
        peer = V44Parameters(
            capability=3, directions=1,
            tx_codewords=1024, rx_codewords=768,
            tx_max_string=64, rx_max_string=48,
            tx_history=3072, rx_history=2048)
        endpoint = LapmEndpoint(log=lambda _: None, detect=False, v44=True)
        endpoint.take(8)
        endpoint.feed(encode_frame(
            b'\x03\xaf' + encode_xid_parameters(
                XidParameters(v44=peer))))
        frames = HdlcDecoder().feed(endpoint.take(4096))
        response = parse_xid_parameters(frames[0][2:]).v44
        self.assertEqual(response.directions, 2)
        self.assertEqual(response.rx_codewords, 512)
        self.assertEqual(response.rx_max_string, 32)
        self.assertEqual(response.rx_history, 1024)
        self.assertIsNone(endpoint.tx_compressor)
        self.assertIsInstance(endpoint.rx_decompressor, V44Decoder)

    def test_v42bis_and_v44_cannot_share_one_xid(self):
        from v42bis import V42bisParameters
        with self.assertRaises(ValueError):
            encode_xid_parameters(XidParameters(
                v42bis=V42bisParameters(), v44=V44Parameters()))


class V44CodecTests(unittest.TestCase):
    def round_trip(self, data, *, codewords=512, max_string=32,
                   history=1024, fragment=1):
        encoder = V44Encoder(codewords, max_string, history)
        wire = encoder.feed(data) + encoder.flush()
        decoder = V44Decoder(codewords, max_string, history)
        decoded = bytearray()
        for start in range(0, len(wire), fragment):
            decoded.extend(decoder.feed(wire[start:start + fragment]))
        self.assertEqual(bytes(decoded), data)
        return wire

    def test_literals_round_trip(self):
        self.round_trip(b'hello')

    def test_repetition_uses_dictionary_codewords(self):
        data = b'ABCDEFGH' * 128
        wire = self.round_trip(data)
        self.assertLess(len(wire), len(data) // 4)

    def test_ordinal_and_codeword_stepup(self):
        self.round_trip(bytes(range(256)) * 4, codewords=2048,
                        max_string=64, history=4096, fragment=3)

    def test_history_reinitialization(self):
        self.round_trip(b'abc' * 4000, history=512, fragment=7)

    def test_flush_keeps_dictionary_continuity(self):
        chunks = [b'ABCD' * 100, b'ABCD' * 200, b'xyz' * 100]
        encoder = V44Encoder()
        decoder = V44Decoder()
        result = bytearray()
        for chunk in chunks:
            wire = encoder.feed(chunk) + encoder.flush()
            result.extend(decoder.feed(wire))
        self.assertEqual(bytes(result), b''.join(chunks))

    def test_decoder_accepts_a_string_extension(self):
        # Ordinals A/B/C create codeword 4 = AB. Emitting codeword 4 followed
        # by extension length 1 copies the C which follows that original AB in
        # history, producing ABCABC.
        writer = _BitWriter()
        for value in b'ABC':
            writer.put(0, 1)
            writer.put(value, 7)
        writer.put(1, 1)
        writer.put(4, 6)
        writer.put(0, 1)       # extension prefix 01, transmission order
        writer.put(1, 1)
        writer.put(1, 1)       # extension length 1
        writer.put(1, 1)
        writer.put(1, 6)       # FLUSH
        writer.align()
        self.assertEqual(V44Decoder().feed(writer.take()), b'ABCABC')

    def test_overlapping_string_extension_from_cx93001(self):
        # First V.44 I-frame captured from a CX93001 after negotiating the
        # default 512/32/1024 limits. Its 30-character extension of C1's
        # "AA" string deliberately overlaps the history being appended.
        encoded = bytes.fromhex(
            'c6f05aec68685a8217316632994c2693c96432994c267311a106')
        decoder = V44Decoder()
        decoded = decoder.feed(encoded) + decoder.feed(bytes.fromhex('c500'))
        self.assertEqual(decoded, b'cx-v44-' + b'A' * 512 + b'\r\n')

    def test_codeword_greater_than_c1_is_an_error(self):
        # Prefix 1 followed by six-bit codeword 10 while initial C1 is 4.
        with self.assertRaises(V44Error):
            V44Decoder().feed(bytes((1 | (10 << 1),)))


class LapmV44Tests(unittest.TestCase):
    def test_two_endpoints_negotiate_and_exchange_v44(self):
        originator = LapmEndpoint(log=lambda _: None, detect=False,
                                  role='originator', v44=True)
        answerer = LapmEndpoint(log=lambda _: None, detect=False,
                                role='answerer', v44=True)
        for _ in range(4):
            answerer.feed(originator.take(4096))
            originator.feed(answerer.take(4096))
        self.assertTrue(originator.connected)
        self.assertTrue(answerer.connected)
        self.assertIsInstance(originator.tx_compressor, V44Encoder)
        self.assertIsInstance(answerer.rx_decompressor, V44Decoder)

        forward = b'originator-v44-' + b'A' * 1000
        reverse = b'answerer-v44-' + b'B' * 1000
        originator.send(forward)
        answerer.send(reverse)
        for _ in range(12):
            answerer.feed(originator.take(4096))
            originator.feed(answerer.take(4096))
        self.assertEqual(answerer.rx_data, forward)
        self.assertEqual(originator.rx_data, reverse)
        self.assertEqual(originator.outstanding, 0)
        self.assertEqual(answerer.outstanding, 0)


if __name__ == '__main__':
    unittest.main()
