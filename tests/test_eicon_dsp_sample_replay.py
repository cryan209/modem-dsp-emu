"""IDMA-fed sample processing on real DSPs, emulator and integer reference."""
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import dsp_conformance as d
import eicon_dsp_sample_replay as replay


def from_vector(v):
    return replay.build(v['samples'], v['coefficients'], v['rate'], v['source'], v['offset'])


class SampleReplayTests(unittest.TestCase):
    def test_pcm_and_wav_import_preserve_exact_samples(self):
        values = [-32768, -1234, 0, 1234, 32767]
        raw = struct.pack('<5h', *values)
        with tempfile.TemporaryDirectory() as temp:
            pcm, wav = Path(temp)/'input.pcm', Path(temp)/'input.wav'
            pcm.write_bytes(raw)
            with wave.open(str(wav), 'wb') as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(9600)
                f.writeframes(raw)
            self.assertEqual(replay.read_samples(pcm, 1, 3, 8000), (values[1:4], 8000))
            self.assertEqual(replay.read_samples(wav, 1, 3), (values[1:4], 9600))
            for path in (pcm, wav):
                with self.assertRaises(ValueError):
                    replay.read_samples(path, 4, 2)
                with self.assertRaises(ValueError):
                    replay.read_samples(path, -1, 2)
            with wave.open(str(wav), 'wb') as f:
                f.setnchannels(2)
                f.setsampwidth(2)
                f.setframerate(8000)
                f.writeframes(raw*2)
            with self.assertRaisesRegex(ValueError, 'mono'):
                replay.read_samples(wav, 0, 2)

    def test_bounds_and_history_wrap_for_partial_blocks(self):
        for count in (1, 7, 8, 9, 1024):
            spec = replay.build(replay.demo_samples(count))
            result = d.emulate(spec)
            self.assertEqual(result['done'], d.DONE_VALUE)
            self.assertEqual([int(v,16) for v in result['dm']], replay.reference(spec))
            self.assertLess(spec['result_end'], replay.INPUT)
        for samples, coefficients in (([], replay.TAPS), ([0]*1025, replay.TAPS),
                                      ([32768], replay.TAPS), ([0], [0]*7)):
            with self.assertRaises(ValueError):
                replay.build(samples, coefficients)

    def test_reference_negative_rail_saturates_and_impulse_taps_align(self):
        spec = replay.build([-32768], [-32768]+[0]*7)
        # (-1)*(-1) in fractional MAC overflows the positive Q15 rail.
        self.assertEqual(replay.reference(spec)[:5], [0x8000, 0, 0x8000, 0, 0x7FFF])
        spec = replay.build([16384]+[0]*7)
        self.assertEqual(replay.reference(spec)[4:40:5], [(c//2) & 65535 for c in replay.TAPS])

    def test_compare_reports_corrupt_sample_and_rejects_incomplete_run(self):
        spec = replay.build(replay.demo_samples(16))
        result = d.emulate(spec)
        card = {'finished':True, 'dsps':[{'port':'test', 'done':result['done'], 'dm':result['dm'][:]}]}
        self.assertTrue(replay.compare(spec, card, {'runs':[result]})['matches'])
        card['dsps'][0]['dm'][5*3+4] = 'abcd'
        summary = replay.compare(spec, card, {'runs':[result]})
        self.assertFalse(summary['matches'])
        self.assertEqual(summary['dsps'][0]['first_mismatch']['sample'], 3)
        self.assertEqual(summary['dsps'][0]['first_mismatch']['field'], 'PCM16')
        self.assertFalse(summary['dsps'][0]['emulator_match'])
        card = {'finished':False, 'dsps':[{'port':'test', 'done':result['done'], 'dm':result['dm']}]}
        self.assertFalse(replay.compare(spec, card)['matches'])
        with self.assertRaises(ValueError):
            replay.compare(spec, {'finished':True, 'dsps':[]})


class HardwareReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((Path(__file__).parent/'data'/'sample_replay_card12196.json').read_text())

    def test_boot_hash_covers_input_samples_coefficients_and_program(self):
        for run in self.data['runs'].values():
            for v in run['vectors']:
                spec = from_vector(v)
                self.assertEqual(replay.boot_digest(spec), v['boot_sha256'])
                changed = copy.deepcopy(spec)
                changed['dm_init'][0][1] ^= 1
                self.assertNotEqual(replay.boot_digest(changed), v['boot_sha256'])

    def test_all_physical_outputs_match_emulator_and_independent_convolution(self):
        samples = 0
        for name, run in self.data['runs'].items():
            specs = [from_vector(v) for v in run['vectors']]
            emulated = {'runs':[d.emulate(s) for s in specs]}
            spec = specs[0] if name == 'demo' else specs
            summary = replay.compare(spec, run['card'], emulated)
            self.assertTrue(summary['matches'], summary)
            samples += sum(r['samples'] for r in summary['dsps'])
            for chip in run['card']['dsps']:
                # Footer explicitly records both SPORT DMA enables cleared.
                self.assertEqual(chip['dm'][-3:], ['0407', '0000', '0000'])
        self.assertEqual(samples, 12288)

    def test_captured_modem_vectors_are_active_and_exactly_echoed(self):
        run = self.data['runs']['matrix']
        for i in range(1, 5):
            v, chip = run['vectors'][i], run['card']['dsps'][i]
            self.assertGreater(len(set(v['samples'])), 100)
            self.assertGreater(max(abs(x) for x in v['samples']), 2000)
            self.assertEqual([int(x,16) for x in chip['dm'][:5*1024:5]],
                             [x & 65535 for x in v['samples']])


if __name__ == '__main__':
    unittest.main()
