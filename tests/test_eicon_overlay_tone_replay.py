"""Tone generators in the six other overlays carrying the cosine table.

The evidence file holds each overlay's extraction hash, the firmware words
every probe imports, each probe's boot payload hash, and the captures from
both eight-DSP runs on spare SN12196.
"""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import dsp_conformance as d
import eicon_dsp_sample_replay as transport
import eicon_modem_routine_replay as modem
import eicon_overlay_tone_replay as ov

DATA = json.loads((Path(__file__).parent/'data'/'overlay_tone_card12196.json').read_text())
PM = {name:{int(a,16):int(w,16) for a,w in words.items()}
      for name,words in DATA['firmware_pm'].items()}


def rebuild(probe):
    return ov.build(probe['overlay'],probe['kind'],probe['tone'],
                    probe['count'],PM[probe['overlay']])


def all_probes():
    for run in DATA['runs'].values():
        for probe,chip in zip(run['probes'],run['card']['dsps']):
            yield probe,chip


class TableTests(unittest.TestCase):
    def test_every_overlay_carries_the_v8_table_word_identical(self):
        if not (modem.OVERLAY/'pm.words').exists():
            self.skipTest('local overlay extraction unavailable')
        v8 = modem.load_firmware()
        reference = [v8[a] for a in range(0x3C80,0x3D00)]
        for name,info in sorted(ov.OVERLAY.items()):
            pm = ov.load_overlay(name)
            base = info['table']
            self.assertEqual([pm[base+i] for i in range(128)],reference,name)

    def test_recorded_overlay_hashes_match_the_extraction(self):
        for name,digest in DATA['overlay_sha256'].items():
            if not ov.overlay_path(name).exists():
                self.skipTest('local overlay extraction unavailable')
            self.assertEqual(ov.overlay_digest(name),digest,name)

    def test_imported_words_are_the_original_firmware(self):
        for name,words in PM.items():
            if not ov.overlay_path(name).exists():
                self.skipTest('local overlay extraction unavailable')
            pm = ov.load_overlay(name)
            for addr,word in words.items():
                self.assertEqual(pm[addr],word,f'{name} PM {addr:04x}')

    def test_oscillator_families_are_what_the_descriptor_claims(self):
        for name,info in sorted(ov.OVERLAY.items()):
            pm = PM[name]
            osc = info['oscillator']
            self.assertEqual(pm[osc],0x380808)                 # L4 = $0080
            m7 = 0x61 if info['family'] == 'A' else 0x5F
            self.assertEqual(pm[osc+1],0x380007|m7 << 4)       # M7 = $00xx
            base = info['table']+(1 if info['family'] == 'A' else 0)
            self.assertIn(0x400004|base << 4,
                          [pm[osc+n] for n in range(2,12)],name)

    def test_increment_to_frequency_holds_for_every_tone(self):
        for name,info in sorted(ov.OVERLAY.items()):
            for tone,(increment,expected) in info['tones'].items():
                self.assertAlmostEqual(increment*info['rate']/32768,expected,
                                       delta=0.05,msg=f'{name} {tone}')


class BuildTests(unittest.TestCase):
    def test_rejects_unknown_overlays_tones_and_counts(self):
        for call in (lambda: ov.build_oscillator('v8.ana','ansam',64),
                     lambda: ov.build_oscillator('info.ana','nope',64),
                     lambda: ov.build_oscillator('info.ana','v34-info-calling',0),
                     lambda: ov.build_native('v22fc.ana','v22-answer',64),
                     lambda: ov.build_quadrature('info.ana','v34-info-calling',64),
                     lambda: ov.build_oscillator('info.ana','v34-info-calling',99999)):
            with self.assertRaises(ValueError):
                call()

    def test_native_sequence_changes_only_its_increment_immediates(self):
        for name in ('info.ana','infoh.ana','v29fc.ana'):
            info = ov.OVERLAY[name]
            lo,hi,slots,*_ = info['native']
            site = [PM[name][a] for a in range(lo,hi)]
            for tone,(increment,_) in info['tones'].items():
                spec = ov.build_native(name,tone,64,PM[name])
                words = [int(w,16) for w in spec['pm']]
                start = next(i for i in range(len(words)-len(site))
                             if all(words[i+j] == site[j] for j in range(len(site))
                                    if j not in slots))
                built = words[start:start+len(site)]
                for j,word in enumerate(site):
                    if j in slots:
                        self.assertEqual(built[j],ov._patch_immediate(word,increment))
                    else:
                        self.assertEqual(built[j],word,f'{name} word {j}')

    def test_quadrature_probe_sets_a_unity_rotation(self):
        for name in ('v22fc.ana','v22bisfc.ana','v22v32.ana-lec'):
            wrapper = ov.OVERLAY[name]['wrapper']
            spec = ov.build_quadrature(name,'v22-answer',64,PM[name])
            self.assertIn([wrapper['rotation'][0],ov.UNITY],spec['dm_init'])
            self.assertIn([wrapper['rotation'][1],0],spec['dm_init'])

    def test_capture_stays_inside_the_result_window(self):
        for name,info in sorted(ov.OVERLAY.items()):
            tone = sorted(info['tones'])[0]
            per = ov.words_per_sample(name,'oscillator')
            count = (ov.LIMIT-ov.RESULT-2)//per
            spec = ov.build_oscillator(name,tone,count,PM[name])
            self.assertLessEqual(spec['result_end'],ov.LIMIT)


class EmulatorTests(unittest.TestCase):
    def test_every_probe_of_every_overlay_generates_its_tone(self):
        for name,info in sorted(ov.OVERLAY.items()):
            kinds = ['oscillator']+(['native'] if info.get('native') else [])+(
                ['quadrature'] if info.get('wrapper') else [])
            for kind in kinds:
                for tone in sorted(info['tones']):
                    spec = ov.build(name,kind,tone,2400,PM[name])
                    result = d.emulate(spec)
                    self.assertEqual(result['done'],d.DONE_VALUE)
                    measured = ov.measure(spec,[int(w,16) for w in result['dm']])
                    # The scan steps in 1 Hz, so a fractional expectation such
                    # as the V.29 carrier's 1700.02 Hz cannot read back exact.
                    self.assertLess(measured['error_hz'],1.0,
                                    f'{name} {kind} {tone}')

    def test_family_b_outputs_are_exactly_ninety_degrees_apart(self):
        for name in ('v22fc.ana','v22bisfc.ana','v22v32.ana-lec'):
            for kind in ('oscillator','quadrature'):
                spec = ov.build(name,kind,'v22-answer',2400,PM[name])
                measured = ov.measure(spec,[int(w,16) for w in d.emulate(spec)['dm']])
                self.assertAlmostEqual(measured['quadrature_degrees'],90.0,
                                       delta=0.01,msg=f'{name} {kind}')

    def test_family_a_probes_report_one_channel(self):
        spec = ov.build_oscillator('v29fc.ana','v29-carrier',1024,PM['v29fc.ana'])
        measured = ov.measure(spec,[int(w,16) for w in d.emulate(spec)['dm']])
        self.assertNotIn('quadrature_degrees',measured)
        self.assertEqual(len(measured['peaks']),1)

    def test_oscillator_and_native_agree_where_both_exist(self):
        for name in ('info.ana','infoh.ana','v29fc.ana'):
            for tone in sorted(ov.OVERLAY[name]['tones']):
                a = ov.build_oscillator(name,tone,1200,PM[name])
                b = ov.build_native(name,tone,1200,PM[name])
                wa = [int(w,16) for w in d.emulate(a)['dm']][:1200]
                wb = [int(w,16) for w in d.emulate(b)['dm']][:1200]
                self.assertEqual(wa,wb,f'{name} {tone}')


class HardwareTests(unittest.TestCase):
    def test_boot_payloads_match_the_recorded_runs(self):
        for probe,_ in all_probes():
            self.assertEqual(transport.boot_digest(rebuild(probe)),
                             probe['boot_sha256'],probe)

    def test_every_physical_word_matches_the_emulator(self):
        for key,run in sorted(DATA['runs'].items()):
            card = run['card']
            self.assertTrue(card['finished'],key)
            specs = [rebuild(p) for p in run['probes']]
            emulated = {'runs':[d.emulate(s) for s in specs]}
            summary = ov.compare(specs,card,emulated)
            self.assertTrue(summary['matches'],summary['dsps'])
            for row in summary['dsps']:
                self.assertEqual(row['mismatch_count'],0,row)
                self.assertEqual(row['card'],row['emulator'],row['port'])

    def test_physical_tones_cover_every_overlay_exactly(self):
        seen = {}
        for probe,chip in all_probes():
            spec = rebuild(probe)
            self.assertEqual(chip['done'],d.DONE_VALUE)
            measured = ov.measure(spec,[int(w,16) for w in chip['dm']])
            self.assertLess(measured['error_hz'],1.0,probe)
            for peak in measured['peaks']:
                self.assertEqual(peak,round(probe['expected_hz']),probe)
            seen.setdefault(probe['overlay'],set()).add(probe['kind'])
        self.assertEqual(sorted(seen),sorted(ov.OVERLAY))

    def test_physical_quadrature_is_ninety_degrees(self):
        checked = 0
        for probe,chip in all_probes():
            spec = rebuild(probe)
            measured = ov.measure(spec,[int(w,16) for w in chip['dm']])
            if 'quadrature_degrees' not in measured:
                continue
            self.assertAlmostEqual(measured['quadrature_degrees'],90.0,delta=0.01,
                                   msg=probe)
            checked += 1
        self.assertEqual(checked,6)

    def test_capture_is_rejected_when_a_word_is_corrupted(self):
        run = DATA['runs']['b']
        specs = [rebuild(p) for p in run['probes']]
        emulated = {'runs':[d.emulate(s) for s in specs]}
        card = json.loads(json.dumps(run['card']))
        words = list(card['dsps'][1]['dm'])
        words[50] = f'{(int(words[50],16) ^ 2):04x}'
        card['dsps'][1]['dm'] = words
        summary = ov.compare(specs,card,emulated)
        self.assertFalse(summary['matches'])
        self.assertEqual(summary['dsps'][1]['first_mismatch']['word'],50)

    def test_compare_rejects_mismatched_specification_counts(self):
        run = DATA['runs']['a']
        specs = [rebuild(p) for p in run['probes']]
        with self.assertRaises(ValueError):
            ov.compare(specs,run['card'],{'runs':[d.emulate(specs[0])]})


if __name__ == '__main__':
    unittest.main()
