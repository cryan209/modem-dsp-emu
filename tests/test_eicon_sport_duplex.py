"""Actual full-duplex cost and extended RX wrap evidence from SN12196."""
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import dsp_conformance as d
import eicon_sport_duplex_probe as duplex
import eicon_sport_wire_probe as wire


class DuplexEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.card = json.loads((Path(__file__).parent/'data'/'sport_duplex_card12196.json').read_text())

    def test_program_hashes_and_external_clock_configuration(self):
        for p in [duplex.build()]+[wire.build_strides(i) for i in range(8)]:
            digest = hashlib.sha256(bytes.fromhex(''.join(p.as_json()['pm']))).hexdigest()
            self.assertEqual(digest, self.card['program_sha256'][p.suite])
            for i, word in enumerate(p.pm):
                if word >> 20 == 9 and (word >> 18) & 3 == 0 and (word >> 4 & 0x3FFF) == 0x3FF2:
                    self.assertEqual(p.pm[i-1] >> 20, 4)
                    self.assertEqual((p.pm[i-1] >> 4 & 0xFFFF) & 0x4300, 0)

    def test_duplex_cost_is_sum_of_rx_and_tx_transfers(self):
        spec = duplex.build().as_json()
        total = both = 0
        for chip in self.card['runs']['duplex']['dsps']:
            self.assertEqual(chip['done'], d.DONE_VALUE)
            for row in duplex.measurements(spec, [int(v,16) for v in chip['dm']]):
                self.assertTrue(row['exact'], row)
                self.assertTrue(row['matches'], row)
                self.assertEqual(row['overhead'], row['rx_min']+row['tx_min'])
                for name in ('rx', 'tx'):
                    self.assertEqual(row[f'{name}_min'] > 0, row[name])
                total += 1
                both += row['rx'] and row['tx']
        self.assertEqual((total, both), (512, 128))

    def test_pointer_brackets_do_not_hide_sampling_boundary(self):
        spec = duplex.build().as_json()
        # One RX transfer occurs in the start bracket, outside the timer
        # interval. A raw pointer delta would falsely report -1 residual.
        words = [65535, 65535-32774, 0x2400, 0x2800,
                 0x2401, 0x2800, 0x2401, 0x2800, 0x2401, 0x2800]
        spec = {**spec, 'cases': spec['cases'][:1]}
        row, = duplex.measurements(spec, words)
        self.assertFalse(row['exact'])
        self.assertTrue(row['matches'])
        words[1] -= 2  # two overhead cycles cannot be explained by this bracket
        row, = duplex.measurements(spec, words)
        self.assertFalse(row['matches'])

    def test_non_power_of_two_and_zero_stride_rings_and_wraps(self):
        runs = [('strides', i) for i in range(8)] + [('i3_repeat', 3)]*8
        for chip_index, (name, index) in enumerate(runs):
            chip = self.card['runs'][name]['dsps'][chip_index % 8]
            spec = wire.build_strides(index).as_json()
            dm = [int(v,16) for v in chip['dm']]
            self.assertEqual(chip['done'], d.DONE_VALUE)
            for c in spec['cases']:
                if c['op'] != 'SPORT1 RX circular DAG':
                    continue
                a, off = c['inputs'], c['addr']-d.RESULT_BASE
                length, stride = a['length'], a['stride']
                pointer, count = dm[off:off+2]
                self.assertTrue(wire.RING <= pointer < wire.RING+length)
                # Derive visited locations and wrap points independently
                # from the emulator's address helper.
                pos, visited, wraps = 0, set(), []
                for _ in range(128):
                    visited.add(pos)
                    raw = pos+stride
                    pos = raw % length
                    if raw != pos:
                        wraps.append(wire.RING+pos)
                self.assertEqual(dm[off+2:off+10],
                                 [255 if n in visited else 0xBEEF for n in range(8)])
                self.assertEqual(count == 0, stride == 0)
                if stride:
                    self.assertLess(count, 32)
                    n = min(count, 16)
                    self.assertEqual(dm[off+11:off+10+3*n:3], wraps[:n])
                    self.assertEqual(dm[off+12:off+10+3*n:3], [255]*n)

    def test_probes_finish_with_no_external_activity(self):
        for p in [duplex.build()]+[wire.build_strides(i) for i in range(8)]:
            out = d.emulate(p.as_json())
            self.assertEqual(out['done'], d.DONE_VALUE)


if __name__ == '__main__':
    unittest.main()
