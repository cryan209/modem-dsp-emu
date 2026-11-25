"""Safety and completion checks for the receive-only hardware probe."""
import sys
import json
import hashlib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import dsp_conformance as d
import eicon_dsp_bench as bench
import eicon_sport_clock_probe as probe
import eicon_sport_wire_probe as wire


class SportClockProbeTests(unittest.TestCase):
    def test_no_clock_frame_outputs_or_tx_writes(self):
        programs = [probe.build(), wire.build_overhead(), wire.build_format()]
        programs += [wire.build_index(i) for i in range(8)]
        for p in programs:
            for i, word in enumerate(p.pm):
                if word >> 20 == 9 and (word >> 18) & 3 == 0:
                    addr = word >> 4 & 0x3FFF
                    if addr in (0x3FF6, 0x3FF2):
                        previous = p.pm[i - 1]
                        self.assertEqual(previous >> 20, 4)
                        self.assertEqual((previous >> 4 & 0xFFFF) & 0x4300, 0)
                # Group-3 internal destination: RX is allowed; TX is not.
                if word & 0xFF0000 == 0x0D0000 and word >> 10 & 3 == 3:
                    self.assertNotIn(word >> 4 & 15, (9, 11))

    def test_probe_finishes_and_counter_control_runs(self):
        result = d.emulate(probe.build().as_json())
        self.assertEqual(result['done'], d.DONE_VALUE)
        self.assertEqual(result['dm'][-1], '0002')

    def test_wire_probes_finish_when_external_clock_is_absent(self):
        programs = [wire.build_overhead(), wire.build_format()]
        programs += [wire.build_index(i) for i in range(8)]
        for p in programs:
            with self.subTest(suite=p.suite):
                self.assertEqual(d.emulate(p.as_json())['done'], d.DONE_VALUE)

    def test_reset_release_is_explicit_and_restricted(self):
        default = bench.build_stub()
        self.assertEqual(default, bench.build_stub(1))
        released = bench.build_stub(0x1F)
        differences = [i for i in range(0, len(default), 4)
                       if default[i:i+4] != released[i:i+4]]
        self.assertEqual(len(differences), 1)
        for value in (0, 2, 0xFFFF):
            with self.assertRaises(ValueError):
                bench.build_stub(value)
        bench.build_stub(0x1F, 0x1E)
        for release, hold in ((1, 0x1E), (0x1F, 1), (0x1F, 0x1F)):
            with self.assertRaises(ValueError):
                bench.build_stub(release, hold)


class SportWireHardwareEvidenceTests(unittest.TestCase):
    """Keep the measured programs byte-identical to the physical card runs.

    This validates the captured evidence, not an emulated serial clock.
    """
    @classmethod
    def setUpClass(cls):
        cls.card = json.loads((Path(__file__).parent / 'data' /
                               'sport_wire_card12196.json').read_text())

    def test_hardware_programs_are_unchanged(self):
        programs = [wire.build_index(i) for i in range(8)]
        programs += [wire.build_overhead(), wire.build_format()]
        for p in programs:
            digest = hashlib.sha256(bytes.fromhex(''.join(p.as_json()['pm']))).hexdigest()
            self.assertEqual(digest, self.card['program_sha256'][p.suite], p.suite)

    def test_physical_receive_ring_and_wrap_pointers(self):
        for i, chip in enumerate(self.card['runs']['dag']['dsps']):
            cases = wire.build_index(i).as_json()['cases']
            for case in cases:
                if case['op'] != 'SPORT1 RX circular DAG':
                    continue
                off = case['addr'] - d.RESULT_BASE
                values = [int(v, 16) for v in chip['dm'][off:off+58]]
                inputs = case['inputs']
                length, stride = inputs['length'], inputs['stride']
                self.assertGreater(values[1], 0)
                self.assertEqual(values[2:10], [255]*length + [0xBEEF]*(8-length))
                log = values[10:]
                records = [log[k:k+3] for k in range(0, 48, 3) if log[k] != 0xDEAD]
                for stamp, pointer, received in records:
                    self.assertEqual(pointer, wire.RING + (0 if stride == 1 else length-1))
                    self.assertEqual(received, 255)
                # First interval can contain startup effects. Every later
                # interval is near 8191.5 DSP cycles per received frame.
                for a, b in zip(records[1:], records[2:]):
                    ticks = (a[0] - b[0]) & 0xFFFF
                    self.assertLessEqual(abs(ticks - length*4095.75), 2)

    def test_physical_transfer_overhead_is_one_cycle(self):
        spec = wire.build_overhead().as_json()
        for chip in self.card['runs']['overhead']['dsps']:
            if chip['suite'] != spec['suite']:
                continue
            for case in spec['cases']:
                off = case['addr'] - d.RESULT_BASE
                before, after, index = [int(v, 16) for v in chip['dm'][off:off+3]]
                self.assertEqual(((before-after) & 0xFFFF) - 32770, index-wire.RING)

    def test_physical_idle_word_formatting(self):
        spec = wire.build_format().as_json()
        expected = [0xFF, 0xFFFF, 0xFFFF, 0xFFFF, 0, 0x6A]
        for chip in self.card['runs']['format']['dsps']:
            for case, value in zip(spec['cases'], expected):
                off = case['addr'] - d.RESULT_BASE
                first, second, control, settled = [int(v, 16) for v in chip['dm'][off:off+4]]
                self.assertEqual([first, second, settled], [value]*3)
                self.assertEqual(control & 0x7FFF,
                                 0x2000 | case['inputs']['dtype'] << 4 | (case['inputs']['bits']-1))


if __name__ == '__main__':
    unittest.main()
