import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from eicon_ipacx import Ipacx


class IpacxTest(unittest.TestCase):
    def test_iom_clock_enable_reset_matches_all_four_physical_interfaces(self):
        import json
        fixture = json.loads((Path(__file__).parent / 'data/bri_ipac_fifo_card12196.json').read_text())
        ports = {record['port']: record['iom_reset'] for record in fixture['records']}
        self.assertEqual(set(ports), set(range(4)))
        for port, value in ports.items():
            with self.subTest(port=port):
                device = Ipacx()
                self.assertEqual(device.read(0x57), value)
                device.write(0x57, 0)
                device.reset()
                self.assertEqual(device.read(0x57), value)
                self.assertFalse(device.iom_configured)

    def test_bearer_fifo_multiblock_frame_and_read_write_aliases(self):
        left, right = Ipacx(), Ipacx()
        left.connect(right)
        left.write(0x2e, 0x8e)
        right.write(0x72, 0xc8)  # transparent mode 0, receiver enabled
        right.write(0x70, 0)
        right.read(0x70)         # consume unmasked reset XPR
        right.write(0x60, 0x7f)
        right.write(0x70, 0)
        payload = bytes(range(150))
        for start in range(0, len(payload), 64):
            for value in payload[start:start + 64]:
                left.write(0x7a, value)
            left.write(0x71, 2 if start + 64 >= len(payload) else 8)
        self.assertEqual(left.bearers[0].transmitted, [payload])
        right.write(0x76, 0x5a)  # RAH2 writes must not replace RBCL reads
        self.assertEqual(right.read(0x76), 151)
        self.assertEqual(right.read(0x78), 0xac)
        data = bytearray()
        for expected, count in ((0x40, 64), (0x40, 64), (0x80, 23)):
            self.assertTrue(right.irq)
            self.assertEqual(right.read(0x60) & 0x80, 0x80)
            self.assertEqual(right.read(0x70), expected)
            data.extend(right.read(0x7a) for _ in range(count))
            right.write(0x71, 0x80)
        self.assertEqual(data, payload + b'\xac')
        self.assertFalse(right.irq)
        self.assertFalse(right.bearers[0].rx_busy)

    def test_bearers_reset_masks_overflow_and_internal_loop(self):
        device = Ipacx()
        for base in (0x70, 0x80):
            self.assertEqual(device.read(base), 0)
            device.write(base, 0)
            self.assertEqual(device.read(base), 0x10)
            device.write(base, 0xff)
            self.assertEqual(device.read(base + 2), 0xc0)
            self.assertEqual(device.read(base + 8), 0x0f)
        device.write(0x82, 0xc8)
        device.write(0x83, 0xa0)  # 32-byte transmit/receive pools
        device.write(0x89, 1)     # internal B2 test loop, no active line
        for value in range(32):
            device.write(0x8a, value)
        self.assertEqual(device.read(0x81) & 0x40, 0)
        with self.assertRaises(BufferError):
            device.write(0x8a, 0)
        device.write(0x81, 2)
        self.assertFalse(device.irq, 'masked events stay pending')
        self.assertFalse(device.receive_b_frame(1, b'overflow'))
        device.write(0x80, 0)
        self.assertEqual(device.read(0x80) & 0xe0, 0x60)
        self.assertEqual(len(device.bearers[1].rx_fifo), 32)
        device.write(0x81, 0x80)
        self.assertEqual(device.read(0x80), 0x80)
        self.assertEqual(device.read(0x8a), 0xac)
        device.write(0x81, 0xc1)
        self.assertEqual(device.read(0x80), 0x10)
        self.assertEqual(device.read(0x88), 0x0f)
        self.assertEqual(device.bearers[0].transmitted, [])
        self.assertEqual(device.read(0x76), 0)
        self.assertFalse(device.receive_b_frame(0, b'inactive receiver'))

    def test_transmit_multi_fifo_frame_and_receive_chunk_acknowledgement(self):
        left, right = Ipacx(), Ipacx()
        left.connect(right)
        left.write(0x2e, 0x8e)
        right.write(0x60, 0xfe)
        right.write(0x20, 0)
        payload = bytes(range(130))
        for start in (0, 32, 64, 96, 128):
            for code in payload[start:start+32]:
                left.write(0, code)
            left.write(0x21, 0x0a if start == 128 else 0x08)
        self.assertEqual(left.transmitted, [payload])
        received = bytearray()
        for expected_flag in (0x40, 0x40, 0x40, 0x40, 0x80):
            self.assertTrue(right.irq)
            self.assertEqual(right.read(0x60) & 1, 1)
            self.assertEqual(right.read(0x20), expected_flag)
            self.assertFalse(right.irq)
            count = (right.read(0x26) & 31) or 32
            if expected_flag == 0x40:
                count = 32
            for _ in range(count):
                received.append(right.read(0))
            right.write(0x21, 0x80)
        self.assertEqual(received, payload + b'\xa0')
        self.assertFalse(right.irq)

    def test_xme_alone_transmits_last_block_like_stock_firmware(self):
        left, right = Ipacx(), Ipacx()
        left.connect(right)
        left.write(0x2e, 0x8e)
        frame = bytes.fromhex('fcff030f26c601ff')
        for code in frame:
            left.write(0, code)
        left.write(0x21, 2)
        self.assertEqual(left.transmitted, [frame])
        self.assertEqual(bytes(right.rx_fifo), frame + b'\xa0')

    def test_masks_do_not_discard_receive_event_and_overflow_is_reported(self):
        device = Ipacx()
        self.assertTrue(device.receive_frame(b'first'))
        self.assertFalse(device.irq)
        self.assertFalse(device.receive_frame(b'second'))
        device.write(0x60, 0xfe)
        device.write(0x20, 0)
        self.assertTrue(device.irq)
        self.assertEqual(device.read(0x20), 0xa0)
        device.write(0x21, 0x40)
        self.assertEqual(device.rx_fifo, b'')
        self.assertEqual(device.rx_packet, b'')

    def test_cir_changes_and_timing_without_connected_peer(self):
        device = Ipacx()
        self.assertEqual(device.read(0x2e), 0x1a)
        self.assertEqual(device.read(0x2e), 0x12)
        device.write(0x60, 0xef)
        device.write(0x2e, 0x0f)
        device.serial_tick()
        self.assertTrue(device.irq)
        self.assertEqual(device.read(0x2e), 0xfa)  # buffered reset completion
        self.assertFalse(device.irq)
        device.serial_tick()
        self.assertEqual(device.read(0x2e), 0x7a)
        self.assertFalse(device.irq)
        device.write(0x2e, 0x0f)
        device.serial_tick()
        self.assertFalse(device.irq)
        device.write(0x2e, 0x8e)
        self.assertFalse(device.active, 'no activation confirmation without a line peer')


if __name__ == '__main__':
    unittest.main()
