import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import eicon_mips_shim as fw
from eicon_idi import IsdnOptions, N_DATA, RC_OK, READY_INT, OK_FC
from eicon_isdn_firmware_bchannel import FirmwareBChannel


def card():
    shim = SimpleNamespace(idi_context=(1, 2, 3), nl_entity_id=4,
                           service_assign_block=8, cores={8: None},
                           nl_connected=True, call=lambda *a, **kw: None)
    return FirmwareBChannel(shim, IsdnOptions(9))


class FirmwareBearerTests(unittest.TestCase):
    def test_large_frame_is_chained_without_overwriting_pr_ring(self):
        bearer = card()
        data = bytes(range(256)) * 4
        bearer.queue_data(data)
        fragments = list(bearer.pending)
        self.assertEqual([r for r, p in fragments], [1, 1, 1, N_DATA])
        self.assertEqual([len(p) for r, p in fragments], [270, 270, 270, 214])
        self.assertEqual(b''.join(p for r, p in fragments), data)
        with self.assertRaises(ValueError):
            fw.post_request(None, 0, N_DATA, 4, 2, b'x' * 271)

    def test_receive_fragments_reassemble_one_packet(self):
        bearer = card()
        indications = [(1, 4, 2, 0, b'first'), (1, 4, 2, 0, b'middle'),
                       (N_DATA, 4, 2, 0, b'last')]
        with patch.object(fw, 'drain_indications', return_value=indications), \
             patch.object(fw, 'drain_return_codes', return_value=[]):
            bearer._pump()
        self.assertEqual(bearer.take_frames(), [b'firstmiddlelast'])
        self.assertEqual(bearer.take_frames(), [])

    def test_ready_interrupt_releases_flow_control(self):
        bearer = card()
        bearer.busy = (33, 256)
        with patch.object(fw, 'drain_indications', return_value=[]), \
             patch.object(fw, 'drain_return_codes', return_value=[(OK_FC, 4, 2, 33)]):
            bearer._pump()
        self.assertTrue(bearer.flow_controlled)
        self.assertEqual(bearer.accepted_octets, 256)
        with patch.object(fw, 'drain_indications', return_value=[]), \
             patch.object(fw, 'drain_return_codes', return_value=[(READY_INT, 4, 2, 0)]):
            bearer._pump()
        self.assertFalse(bearer.flow_controlled)

    def test_serial_word_order_matches_independent_wire_convention(self):
        bearer = card()
        bearer.feed_wire(b'\x02')
        with patch.object(bearer, '_pump'), \
             patch.object(fw.ADSP, 'adsp2181_sport0_frame', return_value=0x10080) as clock:
            self.assertEqual(bearer.take_wire(1), b'\x01')
        clock.assert_called_once_with(None, 0x40, 3000)
        self.assertEqual(bearer.unwritten, 0)

    def test_disconnect_closes_queue_gate(self):
        bearer = card()
        with patch.object(fw, 'drain_indications', return_value=[(4, 4, 2, 0, b'')]), \
             patch.object(fw, 'drain_return_codes', return_value=[]):
            bearer._pump()
        self.assertFalse(bearer.connected)
        self.assertEqual(bearer.disconnects, 1)
        with self.assertRaises(RuntimeError):
            bearer.queue_data(b'data')


if __name__ == '__main__':
    unittest.main()
