"""Original V8.ANA modulation tone generators, on the emulator and the card.

The evidence file holds the firmware words these probes import, the boot
payload hash of every probe and the actual capture from all eight DSPs of
spare SN12196, so a changed routine or probe cannot reuse the hardware run.
"""
import json
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import dsp_conformance as d
import eicon_dsp_sample_replay as transport
import eicon_modem_routine_replay as modem
import eicon_modulation_tone_replay as tone

DATA = json.loads((Path(__file__).parent/'data'/'modulation_tone_card12196.json').read_text())
PM = {int(a,16):int(w,16) for a,w in DATA['firmware_pm'].items()}
OVERLAY = modem.OVERLAY/'pm.words'


def rebuild(probe):
    if probe['kind'] == 'carrier':
        return tone.build_carrier(probe['tone'],probe['count'],PM)
    return tone.build_fsk(probe['channel'],probe['bit'],probe['count'],
                          probe['bit_samples'],probe['gain'],PM)


class ProvenanceTests(unittest.TestCase):
    def test_imported_words_are_the_original_firmware(self):
        if not OVERLAY.exists():
            self.skipTest('local V8.ANA overlay extraction unavailable')
        pm = modem.load_firmware()
        for kind in ('carrier','fsk'):
            for addr,word in tone.firmware_subset(pm,kind).items():
                self.assertEqual(word,PM[addr],f'PM {addr:04x}')

    def test_routines_stay_at_their_original_addresses(self):
        spec = tone.build_carrier('ansam',64,PM)
        loaded = dict(spec['pm_init'])
        for addr in list(range(tone.OSCILLATOR,0x3F42))+list(range(0x3C80,0x3D00)):
            self.assertEqual(int(loaded[addr],16) if isinstance(loaded[addr],str)
                             else loaded[addr],PM[addr])

    def test_carrier_site_changes_only_the_increment_immediate(self):
        site = [PM[a] for a in range(*tone.CARRIER_SITE)]
        for name,(increment,_,_) in tone.CARRIERS.items():
            spec = tone.build_carrier(name,64,PM)
            words = [int(w,16) for w in spec['pm']]
            start = next(i for i in range(len(words)-len(site))
                         if words[i] == site[0] and words[i+2] == site[2])
            built = words[start:start+len(site)]
            self.assertEqual(built[1],(site[1] & ~(0xFFFF << 4))|increment << 4)
            self.assertEqual([built[j] for j in (0,2,3,4,5)],
                             [site[j] for j in (0,2,3,4,5)])

    def test_increments_match_the_published_tones(self):
        for _,(increment,expected,_) in tone.CARRIERS.items():
            self.assertAlmostEqual(tone.tone_hz(increment),expected,delta=0.2)
        for _,(space,mark,space_hz,mark_hz) in tone.FSK_CHANNELS.items():
            self.assertAlmostEqual(tone.tone_hz(space),space_hz,delta=0.2)
            self.assertAlmostEqual(tone.tone_hz(mark),mark_hz,delta=0.2)

    def test_shaping_filters_are_symmetric_linear_phase(self):
        for base in (0x3D42,0x3D7F):
            taps = [PM[base+i] >> 8 for i in range(tone.FSK_TAPS)]
            taps = [v-65536 if v & 0x8000 else v for v in taps]
            self.assertEqual(taps,taps[::-1])
            self.assertEqual(max(range(len(taps)),key=lambda i:abs(taps[i])),
                             tone.FSK_DELAY)


class BoundsTests(unittest.TestCase):
    def test_rejects_unknown_tone_channel_and_counts(self):
        for call in (lambda: tone.build_carrier('v29',64,PM),
                     lambda: tone.build_carrier('ansam',0,PM),
                     lambda: tone.build_carrier('ansam',tone.MAX_SAMPLES+1,PM),
                     lambda: tone.build_fsk(3,1,64,0,0x4000,PM),
                     lambda: tone.build_fsk(1,1,0,0,0x4000,PM),
                     lambda: tone.build_fsk(1,1,64,0,0x8000,PM),
                     lambda: tone.bit_vector(8,2,0),
                     lambda: tone.bit_vector(8,1,-1)):
            with self.assertRaises(ValueError):
                call()

    def test_capture_cannot_overlap_the_data_bit_vector(self):
        spec = tone.build_fsk(1,1,tone.MAX_SAMPLES,0,0x4000,PM)
        self.assertLessEqual(spec['result_end'],tone.INPUT)

    def test_bit_vector_holds_then_toggles(self):
        self.assertEqual(tone.bit_vector(4,1,0),[1,1,1,1])
        self.assertEqual(tone.bit_vector(8,1,2),[1,1,0,0,1,1,0,0])
        self.assertEqual(tone.bit_vector(8,0,2),[0,0,1,1,0,0,1,1])

    def test_channel_select_bit_picks_the_firmware_branch(self):
        for channel,bit in ((1,8),(2,0)):
            spec = tone.build_fsk(channel,1,64,0,0x4000,PM)
            self.assertIn([tone.CHANNEL_SELECT,bit],spec['dm_init'])


class EmulatorTests(unittest.TestCase):
    def test_every_probe_generates_its_tone(self):
        for probe in DATA['probes']:
            spec = rebuild(probe)
            result = d.emulate(spec)
            self.assertEqual(result['done'],d.DONE_VALUE)
            measured = tone.measure(spec,[int(w,16) for w in result['dm']])
            self.assertEqual(measured['error_hz'],0.0,probe)

    def test_held_tones_are_clean_and_toggling_carries_bits(self):
        spec = tone.build_carrier('ansam',4800,PM)
        measured = tone.measure(spec,[int(w,16) for w in d.emulate(spec)['dm']])
        self.assertEqual(measured['peak_hz'],2100)
        self.assertLess(measured['second_ratio'],1e-6)
        self.assertAlmostEqual(measured['rms'],32767/math.sqrt(2),delta=2)
        spec = tone.build_fsk(1,1,4800,32,0x4000,PM)
        measured = tone.measure(spec,[int(w,16) for w in d.emulate(spec)['dm']])
        self.assertEqual(measured['baud'],300)
        self.assertEqual(measured['bit_errors'],0)
        self.assertGreaterEqual(measured['bits'],100)

    def test_above_the_v21_rate_the_discriminator_fails(self):
        spec = tone.build_fsk(1,1,4800,16,0x4000,PM)
        measured = tone.measure(spec,[int(w,16) for w in d.emulate(spec)['dm']])
        self.assertEqual(measured['baud'],600)
        self.assertGreater(measured['bit_errors'],0)

    def test_gain_scales_the_fsk_output(self):
        quiet = tone.build_fsk(1,1,1024,0,0x1000,PM)
        loud = tone.build_fsk(1,1,1024,0,0x4000,PM)
        a = tone.measure(quiet,[int(w,16) for w in d.emulate(quiet)['dm']])
        b = tone.measure(loud,[int(w,16) for w in d.emulate(loud)['dm']])
        self.assertEqual(a['peak_hz'],b['peak_hz'])
        self.assertLess(a['peak_amplitude'],b['peak_amplitude'])


class HardwareTests(unittest.TestCase):
    def test_boot_payloads_match_the_recorded_run(self):
        for probe in DATA['probes']:
            self.assertEqual(transport.boot_digest(rebuild(probe)),probe['boot_sha256'])

    def test_every_physical_word_matches_the_emulator(self):
        card = DATA['card']
        self.assertTrue(card['finished'])
        self.assertEqual(len(card['dsps']),len(DATA['probes']))
        specs = [rebuild(p) for p in DATA['probes']]
        emulated = {'runs':[d.emulate(s) for s in specs]}
        summary = tone.compare(specs,card,emulated)
        self.assertTrue(summary['matches'],summary['dsps'])
        for row in summary['dsps']:
            self.assertEqual(row['mismatch_count'],0,row)
            self.assertEqual(row['card'],row['emulator'],row['port'])

    def test_physical_tones_land_on_the_published_frequencies(self):
        peaks = {}
        for probe,chip in zip(DATA['probes'],DATA['card']['dsps']):
            spec = rebuild(probe)
            self.assertEqual(chip['done'],d.DONE_VALUE)
            measured = tone.measure(spec,[int(w,16) for w in chip['dm']])
            self.assertEqual(measured['error_hz'],0.0,probe)
            if probe['expected_hz'] is not None:
                peaks[measured['peak_hz']] = probe
                self.assertGreater(measured['peak_amplitude'],1000)
            else:
                self.assertEqual(measured['bit_errors'],0)
                self.assertEqual(measured['baud'],300)
        self.assertEqual(sorted(peaks),[980,1080,1180,1650,1750,1850,2100])

    def test_capture_is_rejected_when_a_word_is_corrupted(self):
        specs = [rebuild(p) for p in DATA['probes']]
        emulated = {'runs':[d.emulate(s) for s in specs]}
        card = json.loads(json.dumps(DATA['card']))
        words = list(card['dsps'][3]['dm'])
        words[100] = f'{(int(words[100],16) ^ 1):04x}'
        card['dsps'][3]['dm'] = words
        summary = tone.compare(specs,card,emulated)
        self.assertFalse(summary['matches'])
        self.assertEqual(summary['dsps'][3]['first_mismatch']['word'],100)

    def test_compare_rejects_mismatched_specification_counts(self):
        specs = [rebuild(p) for p in DATA['probes']]
        emulated = {'runs':[d.emulate(specs[0])]}
        with self.assertRaises(ValueError):
            tone.compare(specs,DATA['card'],emulated)


if __name__ == '__main__':
    unittest.main()
