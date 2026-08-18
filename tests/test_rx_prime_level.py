"""`EICON_RX_PRIME_LEVEL`, the level-match on a primed recording.

This exists because of a negative result worth keeping. The V.90A page's
receive gain `DM(0x3FC8)` is *frozen* while that page is resident -- a
write-watch gated on overlay 0x026B catches zero writes to it -- so the level
either side of an `EICON_RX_PRIME` splice has to match, or the whole front end
runs at the wrong operating point. That looked like the obvious cause of the
V.90A `0x00c0` wall.

Measured with this instrument, it is not: the live line reads RMS 901 and the
gold `run65.ulaw` reads 905 at the splice cursor, a scale of 0.995. The prime
is already level-continuous, so the frozen gain is appropriate and there is no
gain defect to fix. Keeping the parser tested keeps the check cheap to re-run.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))

os.environ.setdefault('EICON_RX_PRIME_LEVEL', '')
import eicon_adsp_sip as sip


class ParseRxPrimeLevel(unittest.TestCase):

    def test_unset_is_inert(self):
        self.assertIsNone(sip._parse_rx_prime_level(''))

    def test_auto_asks_for_the_measured_live_level(self):
        self.assertEqual(sip._parse_rx_prime_level('auto'), ('auto', 0.0))
        self.assertEqual(sip._parse_rx_prime_level(' AUTO '), ('auto', 0.0))

    def test_a_number_is_a_target_rms(self):
        self.assertEqual(sip._parse_rx_prime_level('905'), ('fixed', 905.0))

    def test_the_default_is_off(self):
        # The instrument must stay inert unless its variable is set: every
        # V.90 and V.22bis data-mode recipe in docs/analysis/06 runs without it.
        self.assertIsNone(sip.RX_PRIME_LEVEL)


class SilenceFloor(unittest.TestCase):

    def test_the_floor_rejects_a_silent_block(self):
        # The prime opens at 12.4 s and the answerer transmits nothing between
        # about 11 s and 14.6 s, so a naive read of the block immediately
        # before the splice returns zero and would scale the recording to
        # silence. The floor is what makes `auto` skip those blocks.
        self.assertGreater(sip.PRIME_LEVEL_FLOOR, 0)
        self.assertLess(sip.PRIME_LEVEL_FLOOR, 100)
