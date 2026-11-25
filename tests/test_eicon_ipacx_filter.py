"""Replay native B-controller address acceptance, stripping, CRC and status."""
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from eicon_ipacx import Ipacx


class IpacFilterTest(unittest.TestCase):
    def replay(self, filename):
        fixture = json.loads((Path(__file__).parent / 'data' / filename).read_text())
        low = (0x13, 0x31) if fixture.get('compare_pairs') else (0x11, 0x33)
        for record in fixture['records']:
            with self.subTest(capture=filename, mode=record['mode'], address=record['store_address'],
                              crc=record['store_crc'], payload=record['payload']):
                device = Ipacx()
                base = 0x70 + record['channel']*16
                device.write(0x57, 0x58)
                device.write(0x51 + record['channel'], 8)
                for offset, value in ((5,0x20),(6,0x40),(7,low[0]),(8,low[1])):
                    device.write(base+offset, value)
                device.write(base+2, record['mode'])
                device.write(base+3, (4 if record['store_crc'] else 0) | (16 if record['store_address'] else 0))
                device.write(base+9, 1)
                device.write(base, 0)
                device.write(base+1, 0xc1)
                self.assertEqual(device.read(base), record['initial'])
                payload = bytes.fromhex(record['payload'])
                for byte in payload:
                    device.write(base+10, byte)
                device.write(base+1, 0x0a)
                self.assertEqual(device.bearers[record['channel']].transmitted, [payload])
                self.assertEqual(device.read(base), 0x90 if record['fifo'] else 0x10)
                count = device.read(base+6) | ((device.read(base+7)&15)<<8)
                self.assertEqual(count, record['count'])
                self.assertEqual(device.read(base+8), record['status'])
                self.assertEqual(bytes(device.read(base+10) for _ in range(count)).hex(), record['fifo'])
                self.assertEqual(device.bearers[record['channel']].rx_busy, bool(record['fifo']))

    def test_all_eight_engines_match_filtered_frames(self):
        self.replay('bri_ipac_filter_all8_card12196.json')

    def test_rejected_frame_preserves_an_unacknowledged_matching_frame(self):
        device = Ipacx()
        for register, value in ((0x72,0xe8),(0x73,4),(0x75,0x20),(0x76,0x40),
                                (0x77,0x11),(0x78,0x33),(0x70,0)):
            device.write(register, value)
        device.read(0x70)
        self.assertTrue(device.receive_b_frame(0, bytes.fromhex('201155')))
        before = (device.read(0x76), device.read(0x78), bytes(device.bearers[0].rx_fifo))
        self.assertFalse(device.receive_b_frame(0, bytes.fromhex('601155')))
        self.assertEqual((device.read(0x76), device.read(0x78), bytes(device.bearers[0].rx_fifo)), before)
        self.assertEqual(device.read(0x70), 0x80)

    def test_unqualified_address_masks_fail_without_fabricating_receive_data(self):
        device = Ipacx()
        device.write(0x72, 0xe8)
        device.write(0x75, 0x21)
        with self.assertRaises(NotImplementedError):
            device.receive_b_frame(0, bytes.fromhex('201155'))
        self.assertEqual(device.read(0x76), 0)
        self.assertEqual(device.read(0x78), 0x0f)
        self.assertEqual(bytes(device.bearers[0].rx_fifo), b'')

    def test_non_auto_pairs_and_received_command_bit_match_physical_card(self):
        self.replay('bri_ipac_filter_pairs_card12196.json')

    def test_four_modes_match_acceptance_and_fifo_layout(self):
        self.replay('bri_ipac_filter_card12196.json')


if __name__ == '__main__':
    unittest.main()
