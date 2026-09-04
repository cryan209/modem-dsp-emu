"""RTP boundaries must not alter DSP audio or the 8 kHz timestamp clock."""
import sys
import struct
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from eicon_adsp_sip import Call, EiconSipEndpoint


class PacketSizeTests(unittest.TestCase):
    def endpoint(self, ms, buffered=True):
        e = EiconSipEndpoint.__new__(EiconSipEndpoint)
        e.rtp_packet_ms, e.rtp_packet_samples = ms, ms * 8
        e.rtp_packet_seconds = ms / 1000
        e.tx_target_quanta = 1 if buffered else 0
        e.law, e.payload_type, e.codec_name = 'pcma', 8, 'PCMA'
        e.rtp_port = 4000
        e.codec = NS(encode_g711=bytes)
        e.capture = None
        e.sent = []
        e.rtp = NS(sendto=lambda packet, peer: e.sent.append(packet))
        return e

    def test_packetization_preserves_audio_and_timestamp_wrap(self):
        for ms in (1, 7, 10, 15, 20, 30, 200):
            for buffered in (False, True):
                with self.subTest(ms=ms, buffered=buffered):
                    e = self.endpoint(ms, buffered)
                    c = Call(None, None, 'test', 'tag', None)
                    c.tx_seq, c.tx_timestamp = 65534, 0xfffffff0
                    audio = bytes(i % 256 for i in range(4800))
                    with patch('eicon_adsp_sip.HOST_PCMU_ENCODER_AFTER_STATE', None):
                        for start in range(0, len(audio), 160):
                            e._queue_rtp(c, audio[start:start+160])
                    count = len(audio) // (ms * 8)
                    if buffered:
                        for n in range(count):
                            e.service_transmit(c, 100 + n * e.rtp_packet_seconds + (1e-7 if n else 0))
                            self.assertEqual(len(e.sent), n + 1)
                    self.assertEqual(len(e.sent), count)
                    self.assertEqual(b''.join(p[12:] for p in e.sent) + c.tx_pending, audio)
                    for n, packet in enumerate(e.sent):
                        self.assertEqual(len(packet), 12 + ms * 8)
                        _, pt, seq, ts, _ = struct.unpack('!BBHII', packet[:12])
                        self.assertEqual(seq, (65534+n) & 65535)
                        self.assertEqual(ts, (0xfffffff0+n*ms*8) & 0xffffffff)
                        self.assertEqual(bool(pt & 128), n == 0)
                    self.assertIn(f'a=ptime:{ms}\r\n', e.local_sdp('127.0.0.1'))

    def test_invalid_duration_rejected_before_opening_sockets(self):
        import inspect
        sig = inspect.signature(EiconSipEndpoint)
        required = {name: None for name, p in sig.parameters.items()
                    if p.default is inspect.Parameter.empty}
        for ms in (0, -1, 201, 15.5):
            with self.subTest(ms=ms), self.assertRaises(ValueError):
                EiconSipEndpoint(**required, rtp_packet_ms=ms)
