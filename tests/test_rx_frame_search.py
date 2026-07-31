"""The receive-framing search must recover known frames and only those.

The tool's whole value is that a null result is trustworthy: when it reports
that no hypothesis frames the stream, that has to mean the stream is not framed
data, not that the search is broken. So the test plants frames under one
hypothesis and requires the search to find them there and nowhere else.
"""
import struct
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

from v42_lapm import encode_frame

MAGIC = b'ERXD0001'
RECORD = struct.Struct('<IHHH')


def write_capture(path, bits, *, count, msb_first=True):
    out = bytearray(MAGIC)
    for index, start in enumerate(range(0, len(bits) - count, count)):
        group = bits[start:start + count]
        if msb_first:
            word = sum(group[bit] << (15 - bit) for bit in range(count))
        else:
            word = sum(group[bit] << bit for bit in range(count))
        out += RECORD.pack(index, count, 0x2000, word)
    path.write_bytes(bytes(out))


def search(path, *extra):
    result = subprocess.run(
        [sys.executable, str(ROOT / 'tools' / 'rx_frame_search.py'),
         str(path), *extra],
        capture_output=True, text=True, check=True)
    return result.stdout


class RxFrameSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__file__).parent / '_rx_search_tmp.rxd'
        self.addCleanup(lambda: self.tmp.unlink(missing_ok=True))

    def test_planted_frames_are_found_at_the_right_hypothesis(self):
        bits = [1] * 64
        for body in (b'\x03\xaf\x82', b'\x03\x73\x11', b'\x01\x00\x00hello'):
            bits += list(encode_frame(body)) + [1] * 24
        write_capture(self.tmp, bits, count=13)
        out = search(self.tmp, '--bits', '12,13,14')
        self.assertIn('best: 3 good frames at 13 bits, MSB-first', out)

    def test_noise_yields_no_frames_under_any_hypothesis(self):
        # A valid FCS is a 1-in-65536 accident, so a clean null on this much
        # noise is what makes a null result on a real capture meaningful.
        lfsr = 0xACE1
        bits = []
        for _ in range(60000):
            bit = ((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5)) & 1
            lfsr = (lfsr >> 1) | (bit << 15)
            bits.append(bit)
        write_capture(self.tmp, bits, count=3)
        out = search(self.tmp, '--bits', '3,8,13')
        self.assertIn('no hypothesis produced a single valid FCS', out)

    def test_a_constant_mailbox_is_called_out(self):
        write_capture(self.tmp, [1] * 30000, count=3)
        out = search(self.tmp, '--bits', '3')
        self.assertIn('the receiver is not producing a demodulated stream', out)


if __name__ == '__main__':
    unittest.main()
