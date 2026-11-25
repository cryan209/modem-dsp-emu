"""Original ANSam firmware and actual SPORT1 tone transmit/receive evidence."""
import cmath
import ctypes
import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import dsp_conformance as d
import eicon_dsp_sample_replay as transport
import eicon_modem_tone_replay as tone

DATA = json.loads((Path(__file__).parent/'data'/'modem_tone_card12196.json').read_text())
PM = {int(a,16):int(w,16) for a,w in DATA['firmware_pm'].items()}


class ToneReplayTests(unittest.TestCase):
    def test_generator_boot_payload_and_every_hardware_sample(self):
        a = DATA['generator']
        spec = tone.build_generator(a['count'],a['gain'],PM)
        self.assertEqual(transport.boot_digest(spec),a['boot_sha256'])
        result = d.emulate(spec)
        self.assertEqual(result['done'],d.DONE_VALUE)
        self.assertTrue(a['card']['finished'])
        for chip in a['card']['dsps']:
            self.assertEqual(chip['done'],d.DONE_VALUE)
            self.assertEqual(chip['dm'],result['dm'])
        self.assertEqual(len(result['dm']),7685)

    def test_native_carrier_and_phase_reversal(self):
        samples = tone.signed([int(v,16) for v in DATA['generator']['card']['dsps'][0]['dm'][:7680]])
        self.assertEqual(tone.spectrum(samples[:4096],9600)['peak_hz'],2100)
        def phasor(start):
            return sum(samples[n]*cmath.exp(-2j*math.pi*2100*n/9600) for n in range(start,start+160))
        before,after = phasor(4000),phasor(4480)
        self.assertLess(abs(abs(cmath.phase(after/before))-math.pi),0.05)
        footer = [int(v,16) for v in DATA['generator']['card']['dsps'][0]['dm'][-5:]]
        self.assertEqual(footer[0],(7680*0x1C00+0x4000) & 65535)
        self.assertEqual(footer[1],(7680*51) & 65535)
        self.assertEqual(footer[3],962)

    def test_wire_payload_comes_from_actual_generator_output(self):
        spec = tone.wire_from_generator(DATA['generator']['card'])
        self.assertEqual(spec['tone']['samples'],DATA['wire']['samples'])
        self.assertEqual(spec['tone']['source'],DATA['wire']['source'])
        self.assertEqual(transport.boot_digest(spec),DATA['wire']['boot_sha256'])

    def test_physical_received_words_and_carrier_on_every_chip(self):
        spec = tone.build_wire(DATA['wire']['samples'])
        card = DATA['wire']['card']
        self.assertTrue(card['finished'])
        for chip in card['dsps']:
            self.assertEqual(chip['done'],d.DONE_VALUE)
            words = [int(v,16) for v in chip['dm']]
            self.assertIsNotNone(tone.wire_phase(spec,words))
            self.assertGreaterEqual(words[-1],1)  # RX wrap proves whole ring filled
            self.assertGreaterEqual(words[-2],1)  # TX wrap
            self.assertEqual(tone.spectrum(tone.signed(words[:1024]),8000)['peak_hz'],2100)
        words = [int(v,16) for v in card['dsps'][0]['dm']]
        words[100] ^= 1
        self.assertIsNone(tone.wire_phase(spec,words))
        self.assertIsNone(tone.wire_phase(spec,[]))

    def test_wire_program_finishes_without_clocks(self):
        spec = tone.build_wire(DATA['wire']['samples'])
        out = d.emulate(spec,cycles=20000000)
        self.assertEqual(out['done'],d.DONE_VALUE)
        self.assertEqual(out['dm'][:1024],['beef']*1024)

    def test_wire_program_with_emulator_word_events(self):
        spec = tone.build_wire(DATA['wire']['samples'])
        with patch.dict('os.environ',{'EICON_ADSP_SPORT_WIRE':'1'}):
            lib = d.load_library()
            lib.adsp2181_sport_transmit_word.argtypes = [ctypes.c_void_p,ctypes.c_int]
            lib.adsp2181_sport_receive_word.argtypes = [ctypes.c_void_p,ctypes.c_int,ctypes.c_uint16]
            lib.adsp2181_dm.argtypes = [ctypes.c_void_p]
            lib.adsp2181_dm.restype = ctypes.POINTER(ctypes.c_uint16)
            cpu = lib.adsp2181_create()
            try:
                lib.adsp2181_set_idma_boot_hold(cpu,1)
                for addr,value,pm in d.boot_writes(spec):
                    lib.adsp2181_idma_addr_write(cpu,addr if pm else addr | 0x4000)
                    if pm:
                        lib.adsp2181_idma_data_write(cpu,value >> 8)
                    lib.adsp2181_idma_data_write(cpu,value & (255 if pm else 65535))
                dm = lib.adsp2181_dm(cpu)
                for _ in range(4000):
                    lib.adsp2181_run(cpu,8192)
                    word = lib.adsp2181_sport_transmit_word(cpu,1)
                    if word >= 0:
                        lib.adsp2181_sport_receive_word(cpu,1,word)
                    if dm[d.DONE_ADDR] == d.DONE_VALUE:
                        break
                self.assertEqual(dm[d.DONE_ADDR],d.DONE_VALUE)
                self.assertIsNotNone(tone.wire_phase(spec,list(dm[spec['result_base']:spec['result_end']])))
            finally:
                lib.adsp2181_destroy(cpu)


if __name__ == '__main__':
    unittest.main()
