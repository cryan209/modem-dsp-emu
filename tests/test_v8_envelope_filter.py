"""The V.8 envelope biquad bench, pinned so it cannot silently read as dead.

The first version of `tools/v8_envelope_filter_bench.py` reported "the filter is
dead" for every coefficient table, including ones `docs/analog_v8_oracle.md` had
already exonerated. The cause was two hand-encoded instructions in the bench's
own driver stub, not the firmware. These tests exist so that failure mode is
loud: a bench that produces no output, or a filter with no recursion, fails here
rather than being written up as a finding.
"""

import unittest
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'tools'))

import v8_envelope_filter_bench as bench  # noqa: E402

TABLES = (0x3D04, 0x3D10, 0x3D16, 0x3D1C, 0x3D22)


@unittest.skipUnless((bench.OVERLAY / 'pm.words').is_file(),
                     'extract the build-109 overlay set first')
class EnvelopeFilter(unittest.TestCase):
    def test_every_table_responds_to_an_impulse(self):
        """A dead response here means the bench is broken, not the firmware."""
        for table in TABLES:
            with self.subTest(table=hex(table)):
                b = bench.Bench(table)
                h = bench.impulse(b, 32, 7648)
                self.assertNotEqual(sum(abs(v) for v in h), 0)

    def test_the_filter_is_recursive(self):
        """A biquad must ring. Only h[0] being non-zero was the symptom of the
        DAG registers being set on the wrong bank, so the state buffer never
        fed back."""
        b = bench.Bench(0x3D10)
        h = bench.impulse(b, 32, 7648)
        self.assertNotEqual(h[0], 0)
        self.assertGreater(sum(1 for v in h[1:] if v != 0), 8,
                           'no ringing: the state buffer is not feeding back')

    def test_state_buffer_is_written(self):
        b = bench.Bench(0x3D10)
        b.reset_state()
        b.push(7648)
        state = [b.dm[bench.STATE + i] for i in range(4)]
        self.assertNotEqual(state, [0, 0, 0, 0])

    def test_response_is_linear_and_time_invariant(self):
        """Predicting the sine response from the impulse response is the check
        that matters for an emulator-arithmetic defect: a bad shift or a
        misplaced saturation breaks LTI, and reading coefficients would not
        reveal it."""
        b = bench.Bench(0x3D10)
        h = bench.impulse(b, 256, 7648)
        for freq in (0.05, 0.10, 0.20, 0.35):
            with self.subTest(freq=freq):
                predicted, measured = bench.lti_check(b, h, freq, 7648)
                self.assertGreater(predicted, 0)
                self.assertLess(abs(measured - predicted) / predicted, 0.45,
                                f'not LTI at {freq} cyc/sample')

    def test_table_3d10_falls_short_of_the_integrator_threshold(self):
        """The measured shortfall, so a change that fixes it is visible here.

        The downstream integrator needs |filtered| >= 905 to push DM(0x0777)
        past its 200 threshold. Fed the live median raw magnitude, steady, this
        filter produces far less -- which is the open finding in
        docs/analog_rxsample_correction.md, not a bug in this test.
        """
        b = bench.Bench(0x3D10)
        b.reset_state()
        out = [abs(b.push(7648)) for _ in range(300)][100:]
        self.assertLess(max(out), 905)



@unittest.skipUnless((bench.OVERLAY / 'pm.words').is_file(),
                     'extract the build-109 overlay set first')
class EnvelopePassband(unittest.TestCase):
    """Where table 0x3D10's passband is, in units that carry no rate assumption.

    The number matters because it is what identifies the rate the detector
    chain is designed to run at: 0.0225 cycles/sample against a 15 Hz ANSam
    envelope implies 667 Hz, and 9600/15 -- the codec rate divided by the
    DM(0x07BE) reload -- is 640.
    """

    def test_passband_centre(self):
        b = bench.Bench(0x3D10)
        peaks = {}
        for i in range(1, 25):
            fn = i * 0.0025
            peaks[fn] = bench.bench_peak(b, fn, 7648, int(0.20 * 7648),
                                         n=700, settle=300)
        centre = max(peaks, key=peaks.get)
        self.assertAlmostEqual(centre, 0.0225, delta=0.006)

    def test_at_its_passband_the_filter_clears_the_threshold(self):
        """So the detector is not under-gained -- it is being evaluated at the
        wrong rate. 905 is what the downstream integrator needs."""
        b = bench.Bench(0x3D10)
        peak = bench.bench_peak(b, 0.0225, 7648, int(0.20 * 7648))
        self.assertGreater(peak, 905)


if __name__ == '__main__':
    unittest.main()
