"""Class 1 N_DATA trailer decoding at the DSP-to-DTE boundary."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))

from eicon_adsp_sip import decode_class1_n_data


class Class1NDataTests(unittest.TestCase):
    def test_plain_data_is_a_fragment(self):
        self.assertEqual(decode_class1_n_data(b'page\x00'),
                         (b'page', False, None))

    def test_flushed_data_reports_no_carrier(self):
        self.assertEqual(decode_class1_n_data(b'page\x01'),
                         (b'page', True, 'NO CARRIER'))

    def test_valid_hdlc_strips_fcs_flags_and_reports_ok(self):
        self.assertEqual(decode_class1_n_data(b'frame\x12\x34\x08\x02'),
                         (b'frame', True, 'OK'))

    def test_aborted_hdlc_strips_only_flags_and_is_silent(self):
        self.assertIsNone(decode_class1_n_data(b'partial\x08\x03'))

    def test_bad_crc_hdlc_reports_error(self):
        self.assertEqual(decode_class1_n_data(b'frame\x12\x34\x08\x04'),
                         (b'frame', True, 'ERROR'))

    def test_flushed_hdlc_strips_only_flags_and_reports_error(self):
        self.assertEqual(decode_class1_n_data(b'junk\x08\x05'),
                         (b'junk', True, 'ERROR'))

    def test_unknown_or_empty_indication_is_ignored(self):
        self.assertIsNone(decode_class1_n_data(b''))
        self.assertIsNone(decode_class1_n_data(b'bad\xff'))


if __name__ == '__main__':
    unittest.main()
