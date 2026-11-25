"""Host-side segmentation and flow-control protocol checks."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from eicon_bri_host import BriDataSender, BriModemSignals


class Host:
    def __init__(self):
        self.requests = []
        self.available = True
        self.last_reference = 0

    def request(self, entity, code, channel=0, parameters=b''):
        if not self.available:
            return False
        self.last_reference += 1
        self.requests.append((entity, code, channel, bytes(parameters)))
        return True


class BriSenderTest(unittest.TestCase):
    def test_modem_requires_carrier_and_clear_to_send_in_either_order(self):
        import struct
        for order in ((2, 4), (4, 2)):
            signals = BriModemSignals()
            self.assertFalse(signals.ready)
            packet = bytearray(20)
            struct.pack_into('<II', packet, 12, 2400, 1200)
            packet[0] = order[0]
            self.assertTrue(signals.observe(packet))
            self.assertFalse(signals.ready, 'network connect or one signal is insufficient')
            packet[0] = order[1]
            signals.observe(packet)
            self.assertTrue(signals.ready)
            self.assertEqual((signals.tx_bps, signals.rx_bps), (2400, 1200))
            signals.observe(b'\x03')  # CTS off pauses further requests.
            self.assertFalse(signals.ready)
            signals.observe(b'\x04')
            self.assertTrue(signals.ready)
            signals.observe(b'\x01')  # Carrier loss independently clears ready.
            self.assertFalse(signals.ready)
            self.assertFalse(signals.observe(b''))
            self.assertFalse(signals.observe(b'\x80\x00'))
            self.assertFalse(signals.ready)

    def test_frame_continuation_waits_for_return_and_ready_interrupt(self):
        host = Host()
        sender = BriDataSender(host, 3)
        frame = bytes(range(256)) * 3
        sender.queue(frame)
        self.assertTrue(sender.pump())
        self.assertFalse(sender.pump(), 'one outstanding request per entity')
        self.assertEqual(host.requests[0][1], 1)
        sender.observe_returns([dict(entity=3, channel=1, code=0xfc, reference=1)])
        self.assertFalse(sender.pump(), 'OK_FC requires READY_INT before another fragment')
        sender.observe_returns([dict(entity=4, channel=0, code=0xfd)])
        self.assertFalse(sender.pump(), 'another entity cannot release flow control')
        sender.observe_returns([dict(entity=3, channel=0, code=0xfd)])
        self.assertTrue(sender.pump())
        sender.observe_returns([dict(entity=3, channel=1, code=0xff, reference=2)])
        self.assertTrue(sender.pump())
        sender.observe_returns([dict(entity=3, channel=1, code=0xff, reference=3)])
        self.assertTrue(sender.idle)
        self.assertEqual([r[1] for r in host.requests], [1, 1, 8])
        self.assertEqual([len(r[3]) for r in host.requests], [270, 270, 228])
        self.assertEqual(b''.join(r[3] for r in host.requests), frame)
        self.assertEqual(sender.accepted_bytes, len(frame))

    def test_request_buffer_backpressure_and_rejected_fragment(self):
        host = Host()
        sender = BriDataSender(host, 3, max_frame=500)
        sender.queue(b'first')
        host.available = False
        self.assertFalse(sender.pump())
        self.assertFalse(sender.idle)
        host.available = True
        self.assertTrue(sender.pump())
        sender.observe_returns([dict(entity=3, channel=2, code=0xff, reference=1)])
        self.assertFalse(sender.idle, 'another channel cannot acknowledge the fragment')
        sender.observe_returns([dict(entity=3, channel=1, code=0xff, reference=99)])
        self.assertFalse(sender.idle, 'another request reference cannot acknowledge the fragment')
        with self.assertRaises(RuntimeError):
            sender.observe_returns([dict(entity=3, channel=1, code=7, reference=1)])
        self.assertEqual(sender.accepted_bytes, 0)
        with self.assertRaises(ValueError):
            sender.queue(bytes(501))


if __name__ == '__main__':
    unittest.main()
