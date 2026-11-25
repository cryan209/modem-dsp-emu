"""The external wire fixture may change port roles, never firmware contents."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from eicon_bri_guest_load import build


class GuestLoadTest(unittest.TestCase):
    def test_wire_fixture_only_changes_the_two_nt_pcinit_role_bits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ordinary = build(ROOT / 'docs/firmware', root / 'ordinary')
            paired = build(ROOT / 'docs/firmware', root / 'paired', wire_pairs=True)
            self.assertEqual(ordinary['port_roles'], ['te'] * 4)
            self.assertEqual(paired['port_roles'], ['te', 'nt', 'te', 'nt'])
            changed = []
            files = sorted((root / 'ordinary').glob('ram-*.bin'))
            self.assertEqual(len(files), ordinary['blocks'])
            for file in files:
                before = file.read_bytes()
                after = (root / 'paired' / file.name).read_bytes()
                self.assertEqual(before[:24], after[:24])
                offset = struct.unpack_from('<6I', before)[3]
                changed.extend((offset+i, a, b) for i, (a, b) in enumerate(zip(before[24:], after[24:])) if a != b)
            self.assertEqual(changed, [(0x7f000e, 2, 10), (0xff000e, 2, 10)])
            self.assertEqual((root / 'ordinary/start.bin').read_bytes(), (root / 'paired/start.bin').read_bytes())


if __name__ == '__main__':
    unittest.main()
