"""The emulated ADSP-2181 against results from the real chips.

tests/data/dsp_conformance_card12196.json is what eight ADSP-2181s on the
eicon420 4BRI v2 (SN 12196) wrote for each suite on 2026-10-22, in three
runs (alu/mac/shift, then edges, then norm_flags), booted by
tools/eicon_dsp_bench.py's MIPS stub.  The suites must stay byte-identical to
the programs that produced it: change one and it needs a new card run.
"""
import json
import unittest
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import dsp_conformance  # noqa: E402

CARD = json.loads((ROOT / 'tests/data/dsp_conformance_card12196.json').read_text())


def card_mismatches(suite):
    spec = dsp_conformance.SUITES[suite]().as_json()
    emulated = [int(v, 16) for v in dsp_conformance.emulate(spec)['dm']]
    card = [int(v, 16) for v in CARD['suites'][suite]]
    return dsp_conformance.mismatches(spec, card, emulated)


class ConformanceTests(unittest.TestCase):
    def test_suites_encode_and_finish(self):
        for suite, build in dsp_conformance.SUITES.items():
            program = build()
            dsp_conformance.check_encoding(program)
            result = dsp_conformance.emulate(program.as_json())
            self.assertEqual(result['done'], dsp_conformance.DONE_VALUE, suite)
            self.assertEqual(len(result['dm']), len(CARD['suites'][suite]), suite)

    def test_every_suite_matches_card(self):
        # Covers the two fixes the card forced: AZ/AN after AR_SAT come from
        # the saturated value, and NORM by an immediate shifts by +n with no
        # AC fill while NORM (HI) by SE fills with AC only when AV is set.
        for suite in dsp_conformance.SUITES:
            with self.subTest(suite=suite):
                self.assertEqual(card_mismatches(suite), [])


class EmulatedCardTests(unittest.TestCase):
    def test_stub_boot_path_matches_direct_run(self):
        try:
            import unicorn  # noqa: F401
        except ImportError:
            self.skipTest('unicorn required')
        import eicon_dsp_bench
        spec = dsp_conformance.suite_alu().as_json()
        image, meta = eicon_dsp_bench.build_bundle(spec, eicon_dsp_bench.PORTS[:2])
        result = eicon_dsp_bench.EmulatedCard(image).run(meta)
        self.assertTrue(result['finished'])
        direct = dsp_conformance.emulate(spec)['dm']
        for dsp in result['dsps']:
            self.assertEqual(dsp['probe'], ['5a5a', 'a5a5'])
            self.assertEqual(dsp['dm'], direct)


if __name__ == '__main__':
    unittest.main()
