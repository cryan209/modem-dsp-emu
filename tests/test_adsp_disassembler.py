"""The displayed opcode and mnemonic must describe the same PM word."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('c++'), 'C++ compiler required')
class DisassemblerTests(unittest.TestCase):
    def test_nonzero_addresses_and_high_program_memory(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            executable = directory / 'dasm'
            subprocess.run(['c++', '-std=c++17', '-O2', '-o', str(executable),
                            str(root / 'tools/adsp2181emu/dasm/dasm_main.cpp')],
                           check=True, capture_output=True)
            words = [0] * 0x4000
            words[0x100] = 0x0A000F  # unconditional RTS
            words[0x101] = 0x400014  # AY0 = 1
            words[0x3FFF] = 0x0A000F
            firmware = directory / 'pm.bin'
            firmware.write_bytes(b''.join(w.to_bytes(3, 'little') for w in words))
            def decode(start, end):
                return subprocess.check_output(
                    [str(executable), str(firmware), hex(start), hex(end)], text=True)
            self.assertEqual(decode(0x100, 0x102).splitlines(),
                             ['0100: 0a000f  RTS', '0101: 400014  AY0 = $0001'])
            self.assertEqual(decode(0x3FFF, 0x4000).strip(), '3fff: 0a000f  RTS')
            invalid = subprocess.run([str(executable), str(firmware),
                                      '0x4000', '0x4001'], capture_output=True)
            self.assertEqual(invalid.returncode, 2)


if __name__ == '__main__':
    unittest.main()
