import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from eicon_bri_pci import BriPci


class BriPciTest(unittest.TestCase):
    def test_shipping_driver_feature_read_and_serial_match_physical_card(self):
        import json
        import struct
        from eicon_bri_pci_probe import read_vpd
        fixture = json.loads((Path(__file__).parent / 'data/bri_pci_card12196.json').read_text())
        pci = BriPci()
        def read(offset, size):
            return pci.read(offset, size).to_bytes(size, 'little')
        def write(offset, data):
            pci.write(offset, len(data), int.from_bytes(data, 'little'))
        captured = read_vpd(read, write, sleep=lambda _: None)
        self.assertEqual(captured.hex(), fixture['vpd'])
        self.assertEqual(struct.unpack_from('<I', captured, 252)[0], fixture['serial'])
        physical = bytes.fromhex(fixture['configuration'])
        self.assertEqual(bytes(pci.data[0x40:0x4e]), physical[0x40:0x4e])
        self.assertEqual(pci.read(0x34, 1), 0x40)
        with self.assertRaises(NotImplementedError):
            pci.write(0x4e, 2, 0x8000)
        self.assertEqual(captured.hex(), fixture['vpd'])
        custom = BriPci(serial=12345)
        custom.write(0x4e, 2, 252)
        self.assertEqual(custom.read(0x50, 4), 12345)

    def test_vpd_probe_never_issues_eeprom_write_even_on_timeout(self):
        from eicon_bri_pci_probe import read_vpd
        import struct
        writes = []
        def read(offset, size):
            return struct.pack('<H', 0x80fc if not writes else 0)
        def write(offset, data):
            writes.append((offset, struct.unpack('<H', data)[0]))
        with self.assertRaises(RuntimeError):
            read_vpd(read, write, sleep=lambda _: None)
        self.assertEqual(writes[-1], (0x4e, 0xfc))
        self.assertTrue(all(offset == 0x4e and not value & 0x8000 for offset, value in writes))

    def test_enumerate_identity_size_and_assign_four_bars(self):
        pci = BriPci()
        self.assertEqual(pci.read(0, 4), 0xe0131133)
        self.assertEqual(pci.read(0x2c, 4), 0x00148001)
        self.assertEqual(pci.read(0x3d, 1), 1)
        for index, expected_size in enumerate((256, 256, 16*1024*1024, 65536)):
            register = 0x10 + 4 * index
            original = pci.read(register, 4)
            pci.write(register, 4, 0xffffffff)
            mask = pci.read(register, 4)
            address_mask = 0xfffffffc if index == 1 else 0xfffffff0
            self.assertEqual((~(mask & address_mask) + 1) & 0xffffffff, expected_size)
            pci.write(register, 4, original)
            base = 0x1000 if index == 1 else 0x10000000 * (index + 1)
            pci.write(register, 4, base)
            self.assertEqual(pci.read(register, 4) & address_mask, base)
        pci.write(4, 2, 7)
        self.assertEqual(pci.read(4, 2), 7)
        pci.write(0, 4, 0)
        self.assertEqual(pci.read(0, 4), 0xe0131133)
        pci.write(0x3c, 1, 22)
        self.assertEqual(pci.read(0x3c, 1), 22)
        self.assertEqual(pci.read(0x20, 4), 0, 'BAR4 is absent')


if __name__ == '__main__':
    unittest.main()
