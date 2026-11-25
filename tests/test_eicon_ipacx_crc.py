"""Compare B-HDLC FCS, status, counts and FIFO bytes to spare SN12196."""
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from eicon_ipacx import Ipacx


class IpacCrcTest(unittest.TestCase):
    def test_all_eight_engines_and_address_matrix_match_physical_frames(self):
        for name in ('bri_ipac_crc_card12196.json', 'bri_ipac_address_card12196.json',
                     'bri_ipac_bad_crc_card12196.json'):
            fixture = json.loads((Path(__file__).parent / 'data' / name).read_text())
            self.assertTrue(fixture['completed'])
            for record in fixture['records']:
                with self.subTest(capture=name, port=record['port'], channel=record['channel'],
                                  payload=record['payload'], crc=record['store_crc']):
                    device = Ipacx()
                    base = 0x70 + record['channel'] * 16
                    device.write(0x57, 0x58)
                    device.write(0x51 + record['channel'], 8)
                    device.write(base+2, 0xc8)
                    device.write(base+3, (4 if record['store_crc'] else 0)
                                 | (8 if fixture.get('no_transmit_crc') else 0))
                    device.write(base+9, 1)
                    device.write(base, 0)
                    device.write(base+1, 0xc1)
                    self.assertEqual(device.read(base), record['initial'])
                    for byte in bytes.fromhex(record['payload']):
                        device.write(base+10, byte)
                    device.write(base+1, 0x0a)
                    self.assertEqual(device.read(base), 0x90)
                    count = device.read(base+6) | ((device.read(base+7)&15) << 8)
                    self.assertEqual(count, record['count'])
                    self.assertEqual(device.read(base+8), record['status'])
                    data = bytes(device.read(base+10) for _ in range(count))
                    self.assertEqual(data.hex(), record['fifo'])

    def test_corrupt_fcs_crosses_peer_and_pool_boundary_then_good_frame_recovers(self):
        fixture = json.loads((Path(__file__).parent / 'data/bri_ipac_bad_crc_card12196.json').read_text())
        left, right = Ipacx(), Ipacx()
        left.connect(right)
        left.write(0x2e, 0x8e)
        left.write(0x73, 8)       # Send caller-supplied FCS, no automatic CRC.
        right.write(0x72, 0xc8)
        right.write(0x73, 0x64)   # Eight-byte RX pools and actual FCS storage.
        right.write(0x70, 0)
        right.read(0x70)
        sent = []
        for ending in ('620c', '630c'):
            record = next(r for r in fixture['records'] if r['store_crc']
                          and r['payload'].startswith('3300') and r['payload'].endswith(ending))
            octets = bytes.fromhex(record['payload'])
            sent.append(octets)
            for byte in octets:
                left.write(0x7a, byte)
            left.write(0x71, 0x0a)
            self.assertEqual(right.read(0x78), record['status'])
            data = bytearray()
            for size, event in ((8, 0x40), (2, 0x80)):
                self.assertEqual(right.read(0x70), event)
                data.extend(right.read(0x7a) for _ in range(size))
                right.write(0x71, 0x80)
            self.assertEqual(data.hex(), record['fifo'])
            self.assertFalse(right.bearers[0].rx_busy)
        self.assertEqual(left.bearers[0].transmitted, sent)
        self.assertEqual(right.read(0x78) & 0x20, 0x20)
        right.write(0x72, 0xc0)
        self.assertFalse(right.receive_b_frame_with_fcs(0, b''))

    def test_crc_and_status_remain_in_order_across_receive_pool_boundaries(self):
        fixture = json.loads((Path(__file__).parent / 'data/bri_ipac_crc_card12196.json').read_text())
        for length, pools in ((32, ((8,0x40), (8,0x40), (8,0x40), (8,0x40), (3,0x80))),
                              (7, ((8,0x40), (2,0x80)))):
            record = next(r for r in fixture['records'] if r['store_crc'] and len(bytes.fromhex(r['payload'])) == length)
            device = Ipacx()
            device.write(0x72, 0xc8)
            device.write(0x73, 0x64)  # Eight-byte receive pools, stored CRC.
            device.write(0x70, 0)
            device.read(0x70)
            self.assertTrue(device.receive_b_frame(0, bytes.fromhex(record['payload'])))
            data = bytearray()
            for size, event in pools:
                self.assertEqual(device.read(0x70), event)
                data.extend(device.read(0x7a) for _ in range(size))
                device.write(0x71, 0x80)
            self.assertEqual(data.hex(), record['fifo'])
            self.assertFalse(device.bearers[0].rx_busy)



if __name__ == '__main__':
    unittest.main()
