import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from v42_lapm import (HdlcDecoder, LapmEndpoint, XidParameters, encode_frame,
                      encode_xid_parameters, parse_xid_parameters)
from v42bis import (V42bisDecoder, V42bisEncoder, V42bisError,
                    V42bisParameters)


class V42bisXidTests(unittest.TestCase):
    def test_annex_a_private_parameter_group_is_exact(self):
        params = XidParameters(v42bis=V42bisParameters(3, 512, 32))
        encoded = encode_xid_parameters(params)
        self.assertEqual(
            encoded[-18:],
            bytes.fromhex('f0000f000356343201010302020200030120'))
        self.assertEqual(parse_xid_parameters(encoded), params)

    def test_invalid_negotiated_limits_reject_the_xid(self):
        encoded = bytearray(encode_xid_parameters(
            XidParameters(v42bis=V42bisParameters())))
        # Change the P1 value itself rather than relying on an encoder to make
        # a proposal V.42bis explicitly forbids.
        p1 = encoded.index(bytes((2, 2, 2, 0)))
        encoded[p1 + 2:p1 + 4] = b'\x00\x03'
        self.assertIsNone(parse_xid_parameters(bytes(encoded)))


class V42bisCodecTests(unittest.TestCase):
    def round_trip(self, data, *, codewords=512, max_string=32,
                   compress_after=16, fragment=1):
        encoder = V42bisEncoder(codewords, max_string, compress_after)
        wire = encoder.feed(data) + encoder.flush()
        decoder = V42bisDecoder(codewords, max_string)
        decoded = bytearray()
        for start in range(0, len(wire), fragment):
            decoded.extend(decoder.feed(wire[start:start + fragment]))
        self.assertEqual(bytes(decoded), data)
        return wire

    def test_short_input_stays_transparent(self):
        self.assertEqual(self.round_trip(b'hello'), b'hello')

    def test_repetition_enters_compressed_mode_and_shrinks(self):
        data = b'ABCDEFGH' * 128
        wire = self.round_trip(data)
        self.assertLess(len(wire), len(data) // 4)

    def test_escape_values_cycle_in_both_modes(self):
        data = bytes((0x00, 0x33, 0x66, 0x99, 0xCC, 0xFF)) * 80
        self.round_trip(data, fragment=3)

    def test_flush_preserves_dictionary_context_between_transfers(self):
        chunks = [b'ABCD' * 20, b'xyz' * 30, bytes(range(64)) * 2,
                  b'A' * 500]
        encoder = V42bisEncoder(2048, 64, 16)
        decoder = V42bisDecoder(2048, 64)
        decoded = bytearray()
        for chunk in chunks:
            wire = encoder.feed(chunk) + encoder.flush()
            for octet in wire:
                decoded.extend(decoder.feed(bytes((octet,))))
        self.assertEqual(bytes(decoded), b''.join(chunks))

    def test_dictionary_leaf_recovery_stays_synchronized(self):
        data = bytes(range(256)) * 20
        self.round_trip(data, codewords=512, max_string=250, fragment=7)

    def test_reserved_transparent_command_is_an_error(self):
        with self.assertRaises(V42bisError):
            V42bisDecoder().feed(b'\x00\x03')


class LapmCompressionTests(unittest.TestCase):
    def setUp(self):
        self.endpoint = LapmEndpoint(log=lambda _: None, detect=False,
                                     compression=True)
        self.endpoint.take(8)
        proposal = encode_xid_parameters(XidParameters(
            v42bis=V42bisParameters(3, 512, 32)))
        self.endpoint.feed(encode_frame(b'\x03\xaf' + proposal))
        self.endpoint.feed(encode_frame(b'\x03\x7f'))
        self.endpoint.take(4096)

    def test_negotiation_enables_both_directions(self):
        self.assertIsNotNone(self.endpoint.tx_compressor)
        self.assertIsNotNone(self.endpoint.rx_decompressor)
        self.assertEqual(self.endpoint.xid.v42bis,
                         V42bisParameters(3, 512, 32))

    def test_transmit_information_is_compressed(self):
        data = b'ABCDEFGH' * 128
        self.endpoint.send(data)
        frames = HdlcDecoder().feed(self.endpoint.take(16384))
        transfer = b''.join(frame[3:] for frame in frames
                            if not frame[1] & 1)
        self.assertLess(len(transfer), len(data))
        self.assertEqual(V42bisDecoder().feed(transfer), data)

    def test_received_information_is_decompressed(self):
        data = b'the quick brown fox ' * 30
        encoder = V42bisEncoder()
        transfer = encoder.feed(data) + encoder.flush()
        sequence = 0
        for start in range(0, len(transfer), self.endpoint.n401):
            payload = transfer[start:start + self.endpoint.n401]
            self.endpoint.feed(encode_frame(
                bytes((0x03, sequence << 1, 0x00)) + payload))
            sequence += 1
        self.assertEqual(self.endpoint.rx_data, data)

    def test_compression_is_not_advertised_by_default(self):
        endpoint = LapmEndpoint(log=lambda _: None, detect=False)
        self.assertIsNone(endpoint.xid.v42bis)
        self.assertIsNone(endpoint.tx_compressor)
        self.assertIsNone(endpoint.rx_decompressor)

    def test_two_endpoints_negotiate_and_exchange_compressed_data(self):
        originator = LapmEndpoint(log=lambda _: None, detect=False,
                                  role='originator', compression=True)
        answerer = LapmEndpoint(log=lambda _: None, detect=False,
                                role='answerer', compression=True)
        for _ in range(4):
            answerer.feed(originator.take(4096))
            originator.feed(answerer.take(4096))
        self.assertTrue(originator.connected)
        self.assertTrue(answerer.connected)

        from_originator = b'originator-data-' * 100
        from_answerer = b'answerer-data-' * 100
        originator.send(from_originator)
        answerer.send(from_answerer)
        for _ in range(8):
            answerer.feed(originator.take(4096))
            originator.feed(answerer.take(4096))
        self.assertEqual(answerer.rx_data, from_originator)
        self.assertEqual(originator.rx_data, from_answerer)
        self.assertEqual(originator.outstanding, 0)
        self.assertEqual(answerer.outstanding, 0)


if __name__ == '__main__':
    unittest.main()
