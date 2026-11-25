"""Physical V8.ANA subroutines replayed with their original instructions."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import dsp_conformance as d
import eicon_dsp_sample_replay as transport
import eicon_modem_routine_replay as replay

DATA = json.loads((Path(__file__).parent/'data'/'modem_routines_card12196.json').read_text())
FIRMWARE = {int(a,16):int(w,16) for a,w in DATA['firmware_pm'].items()}


def build(v):
    return replay.build(v['samples'],v['kind'],v['table'],FIRMWARE,
                        v['rate'],v['source'],v['offset'],v.get('initial_counter',0))


class ModemRoutineTests(unittest.TestCase):
    def test_boot_payloads_and_original_routine_words_are_unchanged(self):
        for run in DATA['runs'].values():
            for v in run['vectors']:
                spec = build(v)
                self.assertEqual(transport.boot_digest(spec),v['boot_sha256'])
                for address,word in spec['pm_init']:
                    self.assertEqual(word,FIRMWARE[address])
                if v['kind'] == 'biquad':
                    site = [FIRMWARE[a] for a in range(0x3EF5,0x3EFA)]
                    site[2] = (site[2] & ~(0x3FFF << 4)) | v['table'] << 4
                    words = [int(w,16) for w in spec['pm']]
                    self.assertTrue(any(words[i:i+5] == site for i in range(len(words)-4)))

    @unittest.skipUnless((replay.OVERLAY/'pm.words').exists(),'extracted overlay unavailable')
    def test_firmware_provenance_matches_local_extraction(self):
        path = replay.OVERLAY/'pm.words'
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),DATA['firmware_pm_words_sha256'])
        self.assertEqual(replay.firmware_subset(replay.load_firmware()),FIRMWARE)

    def test_every_physical_trace_word_matches_emulator(self):
        count = 0
        for run in DATA['runs'].values():
            specs = [build(v) for v in run['vectors']]
            emulated = {'runs':[d.emulate(s) for s in specs]}
            summary = replay.compare(specs,run['card'],emulated)
            self.assertTrue(summary['matches'],summary)
            count += sum(r['samples'] for r in summary['dsps'])
            for v,chip in zip(run['vectors'],run['card']['dsps']):
                width = len(v['fields'])
                self.assertEqual([int(w,16) for w in chip['dm'][:width*len(v['samples']):width]],
                                 [x & 65535 for x in v['samples']])
                self.assertEqual(chip['dm'][-3:],['0407','0000','0000'])
        self.assertEqual(count,4096)

    def test_complete_detector_decimates_and_updates_threshold_counter(self):
        run = DATA['runs']['detector']
        increases = decreases = clamps = 0
        for v,chip in zip(run['vectors'],run['card']['dsps']):
            fields = v['fields']
            width = len(fields)
            words = [int(w,16) for w in chip['dm']]
            previous_level, previous_counter = 0,v['initial_counter']
            for n in range(len(v['samples'])):
                row = dict(zip(fields,words[n*width:(n+1)*width]))
                self.assertEqual(row['countdown'],15-(n+1) % 15)
                updates = (n+1)//15
                self.assertEqual(row['queue_pointer'],0x0600+updates % 4)
                if (n+1) % 15:
                    self.assertEqual(row['level'],previous_level)
                    self.assertEqual(row['counter'],previous_counter)
                else:
                    expected = previous_counter+1 if row['level'] >= 200 else max(0,previous_counter-1)
                    self.assertEqual(row['counter'],expected)
                    increases += expected > previous_counter
                    decreases += expected < previous_counter
                    clamps += row['level'] < 200 and previous_counter == 0
                previous_level,previous_counter = row['level'],row['counter']
        self.assertGreater(increases,0)
        self.assertGreater(decreases,0)
        self.assertGreater(clamps,0)

    def test_state_mismatch_identifies_sample_and_history_field(self):
        v = DATA['runs']['magnitude']['vectors'][0]
        spec = build(v)
        chip = copy.deepcopy(DATA['runs']['magnitude']['card']['dsps'][0])
        chip['dm'][44+15] = f'{int(chip["dm"][44+15],16) ^ 1:04x}'
        summary = replay.compare(spec,{'finished':True,'dsps':[chip]}, {'runs':[d.emulate(spec)]})
        self.assertFalse(summary['matches'])
        first = summary['dsps'][0]['first_mismatch']
        self.assertEqual((first['sample'],first['field']),(1,'history0'))
        with self.assertRaises(ValueError):
            replay.compare([],{'finished':True,'dsps':[]},{'runs':[]})

    def test_bounds_and_partial_batches_finish(self):
        for kind,limit in [('biquad',256),('magnitude',128),('detector',128)]:
            for count in (1,7,29):
                spec = replay.build(transport.demo_samples(count),kind,firmware=FIRMWARE)
                self.assertEqual(d.emulate(spec)['done'],d.DONE_VALUE)
                self.assertLess(spec['result_end'],replay.INPUT)
            for samples in ([],[0]*(limit+1),[32768]):
                with self.assertRaises(ValueError):
                    replay.build(samples,kind,firmware=FIRMWARE)
        with self.assertRaises(ValueError):
            replay.build([0],'magnitude',table=0x3D10,firmware=FIRMWARE)


@unittest.skipUnless((replay.OVERLAY/'pm.words').exists(),'extracted overlay unavailable')
class ExistingBenchHardwareTests(unittest.TestCase):
    def test_existing_biquad_bench_matches_all_hardware_outputs_and_state(self):
        import v8_envelope_filter_bench as old
        run = DATA['runs']['biquad']
        for v,chip in zip(run['vectors'],run['card']['dsps']):
            b = old.Bench(v['table'])
            try:
                b.reset_state()
                words = [int(w,16) for w in chip['dm']]
                for n,value in enumerate(v['samples']):
                    self.assertEqual(b.push(value) & 65535,words[16*n+1])
                    self.assertEqual([b.dm[old.STATE+i] for i in range(4)],words[16*n+9:16*n+13])
            finally:
                old.ADSP.adsp2181_destroy(b.cpu)

    def test_corrected_magnitude_bench_matches_hardware_and_full_history(self):
        import v8_envelope_filter_bench as old
        run = DATA['runs']['magnitude']
        for v,chip in zip(run['vectors'],run['card']['dsps']):
            b = old.MagnitudeBench()
            try:
                b.reset_chain()
                self.assertEqual(b.dm[0x073E],0x04A0)
                words = [int(w,16) for w in chip['dm']]
                for n,value in enumerate(v['samples']):
                    self.assertEqual(b.push_sample(value),words[44*n+1])
                    self.assertEqual(b.dm[0x073E],words[44*n+9])
                    self.assertEqual([b.dm[0x04A0+i] for i in range(29)],words[44*n+15:44*(n+1)])
                b.reset_chain()
                self.assertEqual(b.dm[0x073E],0x04A0)
                self.assertEqual([b.dm[0x04A0+i] for i in range(29)],[0]*29)
            finally:
                old.ADSP.adsp2181_destroy(b.cpu)


if __name__ == '__main__':
    unittest.main()
