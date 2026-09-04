import contextlib
import io
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import rtp_pcap_timing as timing


class LocalEndpointTests(unittest.TestCase):
    def rows(self):
        packet = struct.pack('!BBHII', 0x80, 0, 1, 0, 1) + bytes(160)
        return [(1., 'gateway', 'local', 12345, 4000, packet),
                (1., 'local', 'gateway', 4000, 12345, packet)]

    def test_single_call_requires_explicit_local_address(self):
        with patch.object(sys, 'argv', ['timing', 'x', '--buffer']), \
             patch.object(timing, 'read_pcap', return_value=self.rows()), \
             contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as result:
                timing.main()
        self.assertEqual(result.exception.code, 2)
        self.assertIn('pass --local IP', err.getvalue())

    def test_explicit_address_allows_single_call(self):
        with patch.object(sys, 'argv', ['timing', 'x', '--buffer', '--local', 'local']), \
             patch.object(timing, 'read_pcap', return_value=self.rows()), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(timing.main(), 0)
