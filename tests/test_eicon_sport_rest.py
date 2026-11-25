"""Physical pattern/TX evidence and passive SPORT0 routing controls."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import dsp_conformance as d
import eicon_dsp_bench_card as runner
import eicon_sport_clock_probe as clock
import eicon_sport_pattern_probe as pattern
import eicon_sport_tx_probe as tx


def programs():
    return [pattern.build(), pattern.build_sport0(), clock.build(True), tx.build_overhead()]+[
        tx.build(i) for i in range(8)]


class ProbeSafetyTests(unittest.TestCase):
    def test_external_clock_and_frame_inputs_only(self):
        for p in programs():
            for i, word in enumerate(p.pm):
                if word >> 20 == 9 and (word >> 18) & 3 == 0:
                    addr = word >> 4 & 0x3FFF
                    if addr in (0x3FF6, 0x3FF2):
                        prior = p.pm[i-1]
                        self.assertEqual(prior >> 20, 4)
                        value = prior >> 4 & 0xFFFF
                        # In MC mode bit 9 is MCL, not internally driven TFS.
                        mask = 0x4100 if addr == 0x3FF6 and value & 0x8000 else 0x4300
                        self.assertEqual(value & mask, 0, p.suite)

    def test_all_programs_finish_without_a_clock(self):
        for p in programs():
            with self.subTest(suite=p.suite):
                d.check_encoding(p)
                self.assertEqual(d.emulate(p.as_json())['done'], d.DONE_VALUE)

    def test_static_injection_rejects_transmit_before_accessing_card(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / 'bad.bundle'
            bundle.write_bytes(b'')
            bundle.with_suffix('.meta.json').write_text(json.dumps({'suites':['sport1_tx_i0']}))
            argv = ['runner', str(bundle), '--pci', '0000:02:02.0', '--adapter', '5',
                    '--serial', '12196', '--out', str(Path(temp)/'out.json'),
                    '--takeover-stock-interfaces', '--ipac-fixture', 'inject']
            with patch.object(sys, 'argv', argv), patch.object(runner, 'check_adapter') as check:
                with self.assertRaisesRegex(SystemExit, 'restricted to known RX-only'):
                    runner.main()
                check.assert_not_called()


class HardwareEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.card = json.loads((Path(__file__).parent/'data'/'sport_rest_card12196.json').read_text())

    def test_program_hashes(self):
        for p in programs():
            digest = hashlib.sha256(bytes.fromhex(''.join(p.as_json()['pm']))).hexdigest()
            self.assertEqual(digest, self.card['program_sha256'][p.suite])

    def test_known_pattern_framing_and_expansion(self):
        spec = pattern.build().as_json()
        for chip in self.card['runs']['pattern']['dsps']:
            for c in spec['cases']:
                a = c['inputs']
                if a['bits'] < 8:
                    continue  # SDS width can trigger multiple short words.
                off = c['addr']-d.RESULT_BASE
                active, control, settled = [int(v,16) for v in chip['dm'][off:off+3]]
                if a['dtype'] == 0:
                    word = 0x5AA5 if a['alternate'] else 0xB54B
                    expected = word >> (16-a['bits'])
                else:
                    expected = ({1:0x5A, 2:0xFF75, 3:0xFFE1} if a['alternate'] else
                                {1:0xFFB5, 2:0x032F, 3:0x0420})[a['dtype']]
                self.assertEqual([active, settled], [expected]*2)

    def test_every_transmit_dag_returns_nonconstant_words(self):
        expected = [v<<8 | (v^255) for v in tx.PATTERN]
        for i, chip in enumerate(self.card['runs']['tx']['dsps']):
            spec = tx.build(i).as_json()
            for c in spec['cases']:
                if c['op'] != 'SPORT1 TX initiation' or not c['inputs']['alternate'] or c['inputs']['bits'] != 16:
                    continue
                off = c['addr']-d.RESULT_BASE
                values = [int(v,16) for v in chip['dm'][off:off+36]]
                count = min(values[0]-tx.RX, 32)
                received = values[8:4+count]  # omit four startup words
                self.assertGreaterEqual(len(received), 24)
                self.assertTrue(any(all(word == expected[(n+phase)%8]
                                         for n, word in enumerate(received)) for phase in range(8)))
                self.assertEqual(values[2], 4)  # TX wrap interrupts

    def test_transmit_overhead_is_one_cycle(self):
        spec = tx.build_overhead().as_json()
        for chip in self.card['runs']['overhead']['dsps']:
            for c in spec['cases']:
                off = c['addr']-d.RESULT_BASE
                start, end, before, after = [int(v,16) for v in chip['dm'][off:off+4]]
                self.assertEqual(((before-after)&0xFFFF)-32770, end-start)

    def test_sport0_scan_has_live_sport1_control(self):
        spec = pattern.build_sport0().as_json()
        for chip in self.card['runs']['sport0']['dsps']:
            for c in spec['cases']:
                if c['op'] != 'SPORT0 route and MC clock':
                    continue
                off = c['addr']-d.RESULT_BASE
                values = [int(v,16) for v in chip['dm'][off:off+22]]
                self.assertEqual(values[0], 0x2800)
                self.assertEqual(values[2], 0)
                self.assertEqual(values[6:14], [0xBEEF]*8)
                self.assertTrue(any(v != 0xBEEF for v in values[14:22]))


if __name__ == '__main__':
    unittest.main()
