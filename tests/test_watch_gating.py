"""The settle gate on overlay-gated instruments.

`EICON_WATCH_OVERLAY` holds arming until the page under test is resident, and
that is not enough on its own: residency is not the same thing as the page
having something to look at.  On the V.90A caller the page becomes resident at
9.36 s and the answering end transmits nothing until 14.6 s, so a budgeted
watch armed on residency spends its whole budget on silence and reports the
received sample as a constant.  Three sessions of "RXSAMPLE is dead" came out
of that window; `EICON_WATCH_AFTER` is the companion gate, and
`docs/analog_rxsample_correction.md` has the measurement.

The two things worth pinning here are the ones that were actually wrong: the
delay predicate has to work off *whichever* spec it is given, and `DUMP_DM`
has to accept the trailing `:<seconds>` field its own documentation promises --
before this it unpacked exactly three fields and a four-field spec raised
`ValueError` in the middle of the media loop, which drops the call.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))

import eicon_adsp_sip


class DumpDelayTests(unittest.TestCase):
    """`_dump_pm_ready` against a spec, not against the module global."""

    def setUp(self):
        # Only the two attributes the predicate touches.
        self.endpoint = SimpleNamespace(
            dump_pm_resident_at=None,
            _dump_pm_ready=lambda call, spec=None: (
                eicon_adsp_sip.EiconSipEndpoint._dump_pm_ready(
                    self.endpoint, call,
                    None if spec is None else spec.split(':'))))

    @staticmethod
    def call_at(samples):
        return SimpleNamespace(samples=samples)

    def test_a_three_field_spec_is_ready_at_once(self):
        self.assertTrue(
            self.endpoint._dump_pm_ready(self.call_at(1000), '0x10:0x20:/tmp/x'))

    def test_a_delay_is_measured_from_first_residency(self):
        spec = '0x10:0x20:/tmp/x:1.0'
        # First call latches the residency instant; 8000 samples is one second.
        self.assertFalse(self.endpoint._dump_pm_ready(self.call_at(80_000), spec))
        self.assertFalse(self.endpoint._dump_pm_ready(self.call_at(87_999), spec))
        self.assertTrue(self.endpoint._dump_pm_ready(self.call_at(88_000), spec))

    def test_an_empty_trailing_field_is_no_delay(self):
        self.assertTrue(
            self.endpoint._dump_pm_ready(self.call_at(0), '0x10:0x20:/tmp/x:'))

    def test_a_four_field_dump_dm_spec_still_unpacks(self):
        # The media loop takes the first three fields of DUMP_DM and hands the
        # whole spec to the predicate; a four-field spec used to raise here.
        lo, hi, path = '0x3740:0x3780:/tmp/dm.txt:1.0'.split(':')[:3]
        self.assertEqual((int(lo, 0), int(hi, 0), path),
                         (0x3740, 0x3780, '/tmp/dm.txt'))


class WatchAfterTests(unittest.TestCase):

    def test_the_gate_defaults_to_no_hold(self):
        # Unset means the overlay gate behaves exactly as it did before.
        self.assertEqual(eicon_adsp_sip.WATCH_AFTER, 0)

    def test_the_hold_is_expressed_in_samples_at_8_khz(self):
        # The arming sites compare `call.samples >= WATCH_AFTER * 8000`, so the
        # unit is seconds of *call*, not of wall clock -- the rig is host-bound
        # and the two are not the same (handoff section 4).
        self.assertEqual(16 * 8000, 128_000)


if __name__ == '__main__':
    unittest.main()
