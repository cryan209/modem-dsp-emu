"""The emulated ADSP-2181 against results from the real chips.

tests/data/dsp_conformance_card12196.json is what the ADSP-2181s on the
eicon420 4BRI v2 (SN 12196) wrote for each suite on 2026-10-22, booted by
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


# seqprobe and seqprobe2 are not asserted: they exist to be re-run on the
# card, and what they still disagree about -- the moment SSTAT's PC_EMPTY bit
# turns on around a TOPPC read -- is written up in
# docs/dsp_conformance_20261022.md rather than frozen here.
ASSERTED = [s for s in sorted(dsp_conformance.SUITES) if s in CARD['suites']]


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
            # A suite that does not reach the completion word under the
            # emulator must never go near the card: a program that spins there
            # takes its DSP's whole run with it.
            self.assertEqual(result['done'], dsp_conformance.DONE_VALUE, suite)
            if suite in CARD['suites']:
                self.assertEqual(len(result['dm']), len(CARD['suites'][suite]),
                                 suite)

    def test_every_suite_matches_card(self):
        # Covers every fix the card has forced so far: AZ/AN after AR_SAT come
        # from the saturated value; NORM by an immediate shifts by +n with no
        # AC fill while NORM (HI) by SE fills with AC only when AV is set;
        # AR_SAT acts on this operation's overflow, not on a latched AV, and
        # its flags follow the saturated value for an AF destination too; a
        # multifunction MAC or shift beats the transfer beside it to their
        # result registers, with SE the wired-OR exception; EXPADJ only ever
        # raises SB; the circular-buffer wrap is one conditional add; CE
        # means "the counter holds 1" and only a jump, call, return or loop
        # end spends a count; a count of 0 runs 16,384 passes; an empty
        # counter or status stack reads as zeros; the status stack holds
        # twelve entries; and a read of TOPPC gives zero.
        for suite in ASSERTED:
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
