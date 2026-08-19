"""Windowed milestones in `EICON_RX_PRIME_SYNC`.

A milestone may be a point, `00c0@23.14`, or a window, `00b3@18.54-23.06`. The
window is the one reactive thing a replay can honestly do: a recording runs on
regardless of what the receiver is doing, where a real peer sustains its Phase-3
segment until the far end responds. Without it, a state the caller dwells in
longer than the gold call did runs off the end of its segment and trains the
caller's equaliser on whatever comes next -- measured in docs/analysis/06
Session 263 as 22 of 216 LMS taps driven to the rail.

The point form has to keep working exactly as it did, because every V.90
data-mode recipe in the tree uses it.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))

import eicon_adsp_sip as sip

GOLD = 'artifacts/eicon-native-tower/run65.ulaw'


def parse(mapping):
    spec = f'{GOLD}:12.4:60:14.0:{mapping}'
    return sip._parse_rx_prime_sync(spec)[4]


class PointMilestones(unittest.TestCase):

    def test_a_point_has_no_end(self):
        # The Session-253 recipe, which must keep parsing as it always did.
        milestones = parse('00b0@17.96,00c0@23.14,00d0@27.5')
        self.assertEqual(milestones, {0x00b0: (143680, None),
                                      0x00c0: (185120, None),
                                      0x00d0: (220000, None)})


class WindowMilestones(unittest.TestCase):

    def test_a_window_carries_both_bounds(self):
        milestones = parse('00b3@18.54-23.06')
        self.assertEqual(milestones, {0x00b3: (148320, 184480)})

    def test_points_and_windows_mix(self):
        milestones = parse('00b0@17.96,00b3@18.54-23.06,00c0@23.14')
        self.assertEqual(milestones[0x00b0], (143680, None))
        self.assertEqual(milestones[0x00b3], (148320, 184480))
        self.assertEqual(milestones[0x00c0], (185120, None))

    def test_the_window_the_gold_call_gives_0x00b3(self):
        # run65's own trace has the digital side in 0x00b2 -- the long Phase-3
        # training segment -- from 18.54 s to 23.06 s, which is the segment the
        # caller's 6.8 s 0x00b3 corresponds to by role.
        start, end = parse('00b3@18.54-23.06')[0x00b3]
        self.assertEqual((end - start) / 8000, 4.52)


class Inert(unittest.TestCase):

    def test_unset_is_none(self):
        self.assertIsNone(sip._parse_rx_prime_sync(''))
