"""Windowed milestones in ``EICON_TX_PRIME_SYNC``."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))

import eicon_adsp_sip as sip

GOLD = 'artifacts/eicon-native-tower/run65.ulaw'


def parse(mapping):
    spec = f'{GOLD}:12.4:60:14.0:{mapping}'
    return sip._parse_tx_prime_sync(spec)[4]


class TxPrimeSyncMilestones(unittest.TestCase):

    def test_points_and_windows_parse(self):
        self.assertEqual(
            parse('00b0@17.96,00b2@18.54-23.06,00d0@27.50'),
            {0x00b0: (143680, None),
             0x00b2: (148320, 184480),
             0x00d0: (220000, None)})

    def test_unset_is_none(self):
        self.assertIsNone(sip._parse_tx_prime_sync(''))


if __name__ == '__main__':
    unittest.main()
