"""The freestanding guest executable must have no unresolved references."""
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from eicon_bri_guest_capi import build, executable


@unittest.skipUnless(shutil.which('clang'), 'clang assembler unavailable')
class GuestCapiBuildTest(unittest.TestCase):
    def test_actual_guest_client_is_a_linux_elf_with_nonexecuting_stack(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'capi'
            build(output, shutil.which('clang'))
            data = (output / 'client').read_bytes()
            header = struct.unpack_from('<16sHHIQQQIHHHHHH', data)
            self.assertEqual(header[1:4], (2, 62, 1))
            self.assertEqual(header[4], 0x401000)
            self.assertEqual(header[10], 2)
            load = struct.unpack_from('<IIQQQQQQ', data, 64)
            self.assertEqual(load[:2], (1, 5))
            self.assertEqual(load[5], len(data))
            stack = struct.unpack_from('<IIQQQQQQ', data, 120)
            self.assertEqual(stack[:2], (0x6474e551, 6))
            self.assertEqual((output / 'client').stat().st_mode & 0o777, 0o755)

    def test_reconnect_script_supports_64_cycles_and_rejects_out_of_range(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build(root / 'base', shutil.which('clang'))
            report = build(root / 'soak', shutil.which('clang'),
                           call_client=root / 'base/client', call_repeats=64)
            script = (root / 'soak/run').read_text()
            self.assertIn('for round in ' + ' '.join(map(str, range(1, 65))) + '; do', script)
            self.assertIn('/bri-capi/client 4 "$round"', script)
            self.assertEqual(report['call_repeats'], 64)
            for invalid in (0, 65):
                with self.assertRaises(ValueError):
                    build(root / 'invalid', shutil.which('clang'), call_repeats=invalid)
                self.assertFalse((root / 'invalid').exists())

    def test_unresolved_call_and_other_allocated_sections_are_rejected(self):
        for source in ('.text\n.global _start\n_start: call undefined_symbol\n',
                       '.text\n.global _start\n_start: ret\n.data\n.byte 1\n'):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'client.S').write_text(source)
                subprocess.run([shutil.which('clang'), '-target', 'x86_64-linux-gnu', '-c',
                                str(root / 'client.S'), '-o', str(root / 'client.o')], check=True)
                with self.assertRaises(ValueError):
                    executable((root / 'client.o').read_bytes())


if __name__ == '__main__':
    unittest.main()
