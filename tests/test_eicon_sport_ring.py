"""The continuous SPORT1 media ring: program, host bookkeeping, hardware runs."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import dsp_conformance as d
import eicon_sport_ring as ring
import eicon_sport_ring_card as card

DATA = json.loads((Path(__file__).parent/'data'/'sport_ring_card12196.json').read_text())
ISOLATION = json.loads((Path(__file__).parent/'data'/'sport_ring_isolation_card12196.json').read_text())
SPORT1_RX = 0        # aliases IRQ0 on the 2181: vector 0x24, IMASK bit 1


class ProgramTests(unittest.TestCase):
    def test_vectors_land_on_the_sport1_entries(self):
        spec = ring.build()
        words = [int(w, 16) for w in spec['pm']]
        r = spec['ring']
        self.assertEqual(words[ring.TX_VECTOR], d.jump(r['tx_isr']))
        self.assertEqual(words[ring.RX_VECTOR], d.jump(r['rx_isr']))
        # Every other vector returns at once, so a stray interrupt cannot run
        # into the handler that follows it.
        for v in range(1, ring.VECTORS):
            if v*4 in (ring.TX_VECTOR, ring.RX_VECTOR):
                continue
            self.assertEqual(words[v*4], d.rts(rti=True), f'vector {v*4:#04x}')

    def test_handlers_are_the_five_expected_instructions(self):
        spec = ring.build()
        words = [int(w, 16) for w in spec['pm']]
        r = spec['ring']
        self.assertEqual(words[r['rx_isr']:r['rx_isr']+5], [
            d.reg_move(0, d.DREG['AX1'], 3, d.GROUP3['RX1']),
            d.compute_dm(0, 'AX1', 1, 1, write=True),
            d.modify(3, 3), d.store_dag(ring.RX_COUNT, 'I3'), d.rts(rti=True)])
        self.assertEqual(words[r['tx_isr']:r['tx_isr']+5], [
            d.compute_dm(0, 'AY1', 2, 2),
            d.reg_move(3, d.GROUP3['TX1'], 0, d.DREG['AY1']),
            d.modify(0, 0), d.store_dag(ring.TX_COUNT, 'I0'), d.rts(rti=True)])

    def test_sport1_is_externally_clocked_with_autobuffering_off(self):
        spec = ring.build()
        words = [int(w, 16) for w in spec['pm']]
        self.assertIn(d.load_dreg('AX0', ring.SPORT1_EXTERNAL), words)
        self.assertIn(d.store_dreg(ring.SPORT1_CONTROL, 'AX0'), words)
        self.assertIn(d.store_dreg(ring.SPORT1_AUTOBUF, 'AX0'), words)
        self.assertIn(d.load_group3('IMASK', ring.IMASK_SPORT1), words)

    def test_rejects_bad_rings(self):
        for call in (lambda: ring.build(ring=1000),          # not a power of two
                     lambda: ring.build(ring=8),             # too small
                     lambda: ring.build(rx_ring=0x1001),     # unaligned
                     lambda: ring.build(rx_ring=0x1000, tx_ring=0x1200),  # overlap
                     lambda: ring.build(tx_fill=[0]*8)):     # wrong fill length
            with self.assertRaises(ValueError):
                call()

    def test_transmit_ring_is_preset_with_the_pattern(self):
        spec = ring.build(tx_fill=[ring.ramp(n) for n in range(ring.RING)])
        presets = dict((a, v) for a, v in spec['dm_init'])
        for n in range(ring.RING):
            self.assertEqual(presets[ring.TX_RING+n], ring.ramp(n))
        self.assertEqual(presets[ring.STOP], 0)


class EmulatorTests(unittest.TestCase):
    """Full duplex on the core: words in land in the ring, words out follow."""
    rx_shadow = False
    log_zeros = False

    def setUp(self):
        environment = patch.dict('os.environ',
                                 {'EICON_ADSP_SPORT': '1', 'EICON_ADSP_TIMER': '0'})
        environment.start()
        self.addCleanup(environment.stop)
        self.lib = d.load_library()
        for name, args, res in (
                ('adsp2181_run', [ctypes.c_void_p, ctypes.c_int], ctypes.c_int),
                ('adsp2181_sport1_frame',
                 [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_int], ctypes.c_uint32),
                ('adsp2181_set_irq',
                 [ctypes.c_void_p, ctypes.c_int, ctypes.c_int], None)):
            getattr(self.lib, name).argtypes = args
            if res is not None:
                getattr(self.lib, name).restype = res
        self.core = self.lib.adsp2181_create()
        self.addCleanup(self.lib.adsp2181_destroy, self.core)
        self.spec = ring.build(rx_shadow=self.rx_shadow, log_zeros=self.log_zeros,
                               tx_fill=[ring.ramp(n) for n in range(ring.RING)])
        self.lib.adsp2181_reset(self.core)
        self.lib.adsp2181_set_idma_boot_hold(self.core, 1)
        for addr, value, is_pm in d.boot_writes(self.spec):
            self.lib.adsp2181_idma_addr_write(self.core, addr if is_pm else 0x4000 | addr)
            if is_pm:
                self.lib.adsp2181_idma_data_write(self.core, value >> 8)
                value &= 0xFF
            self.lib.adsp2181_idma_data_write(self.core, value)
        self.lib.adsp2181_run(self.core, 400)

    def read(self, addr):
        self.lib.adsp2181_idma_addr_write(self.core, 0x4000 | addr)
        return self.lib.adsp2181_idma_data_read(self.core)

    def frame(self, word):
        # adsp2181_sport1_frame raises only the transmit interrupt, so latch
        # the receive one here. SPORT1 RX shares IRQ0 and ICNTL 3 makes it
        # edge sensitive, so the latch holds until the handler takes it.
        self.lib.adsp2181_set_irq(self.core, SPORT1_RX, 1)
        self.lib.adsp2181_set_irq(self.core, SPORT1_RX, 0)
        return self.lib.adsp2181_sport1_frame(self.core, word, 60)

    def test_setup_enables_sport1_and_primes_the_transmitter(self):
        r = self.spec['ring']
        self.assertEqual(self.read(ring.SPORT1_CONTROL), ring.SPORT1_EXTERNAL)
        self.assertEqual(self.read(ring.SYSTEM), ring.SPORT1_ON)
        self.assertEqual(self.read(r['tx_count']), 1)      # primed, not yet sent
        self.assertEqual(self.read(r['rx_count']), 0)
        self.assertGreater(self.read(r['alive']), 0)       # foreground is running

    def test_every_frame_is_received_into_the_ring_and_answered(self):
        r = self.spec['ring']
        sent, out = [], []
        for n in range(300):
            word = (0xA000+n) & 0xFFFF
            frame = self.frame(word)
            sent.append(word)
            out.append((frame & 0xFFFF, bool(frame >> 16 & 1)))
        self.assertEqual(self.read(r['rx_count']), 300)
        self.assertEqual(self.read(r['tx_count']), 301)
        stored = [self.read(r['rx_ring']+i % r['ring']) for i in range(300)]
        self.assertEqual(stored, sent)
        self.assertTrue(all(written for _, written in out))
        self.assertEqual([w for w, _ in out],
                         [ring.ramp(n) for n in range(1, 301)])

    def test_the_stop_cell_ends_the_foreground(self):
        r = self.spec['ring']
        for _ in range(20):
            self.frame(0x1111)
        self.lib.adsp2181_idma_addr_write(self.core, 0x4000 | r['stop'])
        self.lib.adsp2181_idma_data_write(self.core, 1)
        self.lib.adsp2181_run(self.core, 200)
        self.assertEqual(self.read(d.DONE_ADDR), d.DONE_VALUE)
        self.assertEqual(self.read(ring.SYSTEM), ring.SPORTS_OFF)


class ShadowControlTests(EmulatorTests):
    rx_shadow = True

    def test_shadow_tracks_received_words_across_the_wrap(self):
        for n in range(ring.RING+37):
            self.frame(n ^ 0xA55A)
        base = self.spec['ring']['rx_shadow']
        for n in range(37, ring.RING+37):
            self.assertEqual(self.read(base+n % ring.RING), n ^ 0xA55A)


class ZeroLogControlTests(EmulatorTests):
    log_zeros = True

    def test_only_actual_zero_samples_are_logged_at_their_receive_indices(self):
        zeros = [3, 1003, 1028]
        for n in range(1100):
            self.frame(0 if n in zeros else 0x5AA5)
        cells = self.spec['ring']
        self.assertEqual(self.read(cells['zero_count']), len(zeros))
        self.assertEqual([self.read(cells['zero_log']+i) for i in range(len(zeros))], zeros)


class AutobufferControlTests(EmulatorTests):
    # Reuse boot helpers, without inheriting the frame-adapter assertions.
    test_setup_enables_sport1_and_primes_the_transmitter = None
    test_every_frame_is_received_into_the_ring_and_answered = None
    test_the_stop_cell_ends_the_foreground = None

    def test_autonomous_rx_capture_preserves_words_without_interrupts(self):
        env = patch.dict('os.environ', {'EICON_ADSP_SPORT_WIRE': '1'})
        env.start()
        self.addCleanup(env.stop)
        spec = ring.build_rx_capture()
        self.lib.adsp2181_reset(self.core)
        self.lib.adsp2181_set_idma_boot_hold(self.core, 1)
        for addr, value, pm in d.boot_writes(spec):
            self.lib.adsp2181_idma_addr_write(self.core, addr if pm else 0x4000 | addr)
            if pm:
                self.lib.adsp2181_idma_data_write(self.core, value >> 8)
            self.lib.adsp2181_idma_data_write(self.core, value & (255 if pm else 65535))
        self.lib.adsp2181_run(self.core, 400)
        rx = self.lib.adsp2181_sport_receive_word
        rx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint16]
        rx.restype = ctypes.c_int
        for n in range(9000):
            rx(self.core, 1, 0 if n == 8990 else 0x5AA5)
            self.lib.adsp2181_run(self.core, 10)
        self.assertEqual(self.read(spec['capture']['pointer']), 9000 % 8192)
        for i in range(8192):
            self.assertEqual(self.read(i), 0 if i == 8990 % 8192 else 0x5AA5, i)

    def test_native_tx_repeats_without_per_word_host_or_cpu_writes(self):
        env = patch.dict('os.environ', {'EICON_ADSP_SPORT_WIRE': '1'})
        env.start()
        self.addCleanup(env.stop)
        spec = ring.build(tx_autobuffer=True,
                          tx_fill=[ring.ramp(n) for n in range(ring.RING)])
        self.lib.adsp2181_reset(self.core)
        self.lib.adsp2181_set_idma_boot_hold(self.core, 1)
        for addr, value, pm in d.boot_writes(spec):
            self.lib.adsp2181_idma_addr_write(self.core, addr if pm else 0x4000 | addr)
            if pm:
                self.lib.adsp2181_idma_data_write(self.core, value >> 8)
            self.lib.adsp2181_idma_data_write(self.core, value & (255 if pm else 65535))
        self.lib.adsp2181_run(self.core, 400)
        tx = self.lib.adsp2181_sport_transmit_word
        tx.argtypes = [ctypes.c_void_p, ctypes.c_int]
        tx.restype = ctypes.c_int
        words = []
        for _ in range(3*ring.RING):
            words.append(tx(self.core, 1))
            self.lib.adsp2181_run(self.core, 60)
        for n, word in enumerate(words):
            self.assertEqual(word, n % ring.RING, f'sample {n}')
        self.assertEqual(self.read(ring.TX_COUNT), 1+3*ring.RING)


class FakeDsp:
    """A ring that advances a fixed number of samples per counter poll."""

    def __init__(self, size, cells, delay=1, per_poll=160, zero_every=None):
        self.size, self.cells, self.delay = size, cells, delay
        self.per_poll, self.zero_every = per_poll, zero_every
        self.tx = [ring.ramp(n) for n in range(size)]
        self.rx = [0]*size
        self.sent, self.n = {}, 0

    def _advance(self):
        for _ in range(self.per_poll):
            n = self.n
            self.sent[n] = self.tx[n % self.size]
            word = self.sent.get(n-self.delay, 0)
            if self.zero_every and n and not n % self.zero_every:
                word = 0
            self.rx[n % self.size] = word
            self.n += 1
        for old in [k for k in self.sent if k < self.n-2*self.size]:
            del self.sent[old]

    def read_dm(self, addr):
        if addr == self.cells['rx_count']:
            self._advance()
            return self.n & 0x3FFF
        if addr == self.cells['tx_count']:
            return (self.n+1) & 0x3FFF
        if addr == self.cells['alive']:
            return self.n & 0x3FFF
        base = addr-self.cells['rx_ring']
        if 0 <= base < self.size:
            return self.rx[base]
        return 0

    def write_dm(self, addr, value):
        pass

    def read_block(self, addr, count):
        base = addr-self.cells['rx_ring']
        return [self.rx[(base+i) % self.size] for i in range(count)]

    def write_block(self, addr, words):
        base = addr-self.cells['tx_ring']
        for i, word in enumerate(words):
            self.tx[(base+i) % self.size] = word & 0xFFFF


class ServiceTests(unittest.TestCase):
    """The host loop's bookkeeping, with the clock and the DSP both faked."""

    def run_service(self, dsp, seconds=0.05, period=0.0, margin=300):
        with patch.object(card.time, 'sleep', lambda _s: None):
            return card.service(dsp, ring.build()['ring'], seconds, period,
                                margin, verbose=False)

    def cells(self):
        return ring.build()['ring']

    def test_a_kept_up_ring_reports_no_overrun_or_underrun(self):
        cells = self.cells()
        dsp = FakeDsp(cells['ring'], cells)
        out = self.run_service(dsp)
        self.assertEqual(out['overruns'], 0)
        self.assertEqual(out['underruns'], 0)
        self.assertEqual(out['loop_delay'], 1)
        self.assertEqual(out['anomalies'], 0)
        self.assertGreater(out['verified_samples'], 0)
        self.assertLessEqual(out['peak_occupancy'], cells['ring'])

    def test_a_starved_ring_is_caught_not_silently_passed(self):
        cells = self.cells()
        dsp = FakeDsp(cells['ring'], cells, per_poll=4*cells['ring'])
        out = self.run_service(dsp)
        self.assertGreater(out['overruns'], 0)
        self.assertGreater(out['underruns'], 0)
        self.assertGreater(out['peak_occupancy'], cells['ring'])

    def test_substituted_words_are_counted_and_classified(self):
        cells = self.cells()
        dsp = FakeDsp(cells['ring'], cells, zero_every=500)
        out = self.run_service(dsp)
        self.assertGreater(out['anomalies'], 0)
        self.assertEqual(out['anomalies'], out['zero_words'])
        self.assertEqual(out['other_words'], 0)
        # The fake always re-reads the same stored word, so a zero that is
        # really in memory must be reported as still wrong.
        self.assertEqual(out['reread_correct'], 0)
        self.assertEqual(out['reread_still_wrong'], out['anomalies'])

    def test_the_warmup_window_is_not_verified(self):
        cells = self.cells()
        dsp = FakeDsp(cells['ring'], cells)
        out = self.run_service(dsp)
        self.assertEqual(out['warmup'], 2*cells['ring'])
        self.assertGreaterEqual(out['delay_locked_at'], out['warmup'])

    def test_the_margin_must_outrun_one_poll_period(self):
        for period, margin in ((0.020, 100), (0.020, 1024), (0.005, 10)):
            with self.subTest(period=period, margin=margin):
                dsp = FakeDsp(ring.RING, self.cells())
                with self.assertRaises(ValueError):
                    self.run_service(dsp, period=period, margin=margin)
                self.assertEqual(dsp.n, 0)

    def test_invalid_durations_are_rejected_before_servicing(self):
        for seconds, period in ((0, .02), (-1, .02), (float('nan'), .02),
                                (float('inf'), .02), (1, -.01),
                                (1, float('nan')), (1, float('inf'))):
            with self.subTest(seconds=seconds, period=period):
                dsp = FakeDsp(ring.RING, self.cells())
                with self.assertRaises(ValueError):
                    self.run_service(dsp, seconds=seconds, period=period)
                self.assertEqual(dsp.n, 0)

    def test_continuity_does_not_hide_corrupted_samples(self):
        out = self.run_service(FakeDsp(ring.RING, self.cells(), zero_every=500))
        verdict = card.classify_result(out)
        self.assertTrue(verdict['flow_continuous'])
        self.assertFalse(verdict['anomaly_free'])
        self.assertFalse(verdict['clean'])
        good = self.run_service(FakeDsp(ring.RING, self.cells()))
        self.assertTrue(card.classify_result(good)['clean'])

    def test_cyclic_control_does_not_write_transmit_memory(self):
        cells = self.cells()
        dsp = FakeDsp(ring.RING, cells)
        with patch.object(dsp, 'write_block', side_effect=AssertionError('TX write')):
            out = card.service(dsp, cells, .02, 0, 300, False, cyclic_tx=True)
        self.assertEqual(out['tx_mode'], 'cyclic')
        self.assertEqual(out['pattern_modulus'], ring.RING)
        self.assertEqual(out['anomalies'], 0)
        self.assertGreater(out['verified_samples'], 0)

    def test_constant_receive_control_checks_words_without_learning_a_delay(self):
        cells = self.cells()
        dsp = FakeDsp(ring.RING, cells)
        advance = dsp._advance
        def constant_input():
            advance()
            dsp.rx[:] = [0x5AA5]*ring.RING
            dsp.rx[(dsp.n-1) % ring.RING] = 0
        dsp._advance = constant_input
        out = card.service(dsp, cells, .02, 0, 300, False,
                           cyclic_tx=True, rx_constant=0x5AA5)
        self.assertGreater(out['anomalies'], 0)
        self.assertEqual(out['anomalies'], out['zero_words'])
        self.assertEqual(out['loop_delay'], 0)
        self.assertEqual(out['rx_constant'], 0x5AA5)


class HardwareTests(unittest.TestCase):
    def test_the_recorded_program_is_what_this_builder_makes(self):
        spec = ring.build(tx_fill=[ring.ramp(n) for n in range(ring.RING)])
        self.assertEqual(spec['pm'], DATA['program']['pm'])
        self.assertEqual(spec['ring'], DATA['program']['ring'])
        self.assertEqual(len(spec['dm_init']), DATA['program']['dm_init_words'])

    def test_every_run_held_eight_kilohertz_with_no_overrun_or_underrun(self):
        self.assertEqual(len(DATA['runs']), 3)
        for name, run in DATA['runs'].items():
            with self.subTest(run=name):
                self.assertTrue(run['clean'], run)
                self.assertEqual(run['overruns'], 0)
                self.assertEqual(run['underruns'], 0)
                self.assertGreater(run['rx_samples'], 470000)
                self.assertAlmostEqual(run['rx_rate'], 8000, delta=20)
                self.assertAlmostEqual(run['tx_rate'], 8000, delta=20)
                self.assertLess(run['peak_occupancy'], run['ring'])
                self.assertAlmostEqual(run['seconds'], 60, delta=1)

    def test_the_stream_never_slipped(self):
        """A constant loop delay over 478,000 samples means no sample moved."""
        for name, run in DATA['runs'].items():
            with self.subTest(run=name):
                self.assertEqual(run['loop_delay'], 1)
                self.assertGreater(run['verified_samples'], 470000)
                # Counts match within one poll, so no interrupt was missed.
                self.assertLessEqual(abs(run['tx_samples']-run['rx_samples']), 200)

    def test_the_substituted_words_were_in_memory_not_in_the_read(self):
        run = DATA['runs']['ring_final']
        self.assertEqual(run['anomalies'], run['zero_words'])
        self.assertEqual(run['other_words'], 0)
        self.assertEqual(run['reread_correct'], 0)
        self.assertEqual(run['reread_still_wrong'], run['anomalies'])
        self.assertLess(run['anomaly_rate'], 1e-4)

    def test_the_host_drove_the_dsp_port_directly(self):
        for run in DATA['runs'].values():
            self.assertTrue(run['echo'])
            self.assertGreater(run['alive_last'], 0)
            self.assertEqual(run['stock_interfaces']['reset'], 0x1F)


class IsolationEvidenceTests(unittest.TestCase):
    def test_programs_and_boot_inputs_match_the_physical_controls(self):
        for name, options in [('ring', {}), ('ring-auto', {'tx_autobuffer': True}),
                              ('ring-shadow', {'rx_shadow': True}),
                              ('ring-log', {'log_zeros': True}), ('rx-capture', None)]:
            with self.subTest(program=name):
                spec = (ring.build_rx_capture() if options is None else
                        ring.build(tx_fill=list(range(ring.RING)), **options))
                saved = ISOLATION['programs'][name]
                self.assertEqual(spec['pm'], saved['pm'])
                self.assertEqual(spec.get('ring', spec.get('capture')), saved['configuration'])
                boot = json.dumps(ring.boot_words(spec), separators=(',', ':')).encode()
                self.assertEqual(hashlib.sha256(boot).hexdigest(), saved['boot_sha256'])

    def test_both_receive_stores_contain_the_same_zero(self):
        run = ISOLATION['runs']['ring-shadow']
        self.assertEqual(run['anomalies'], 7)
        for event in run['delay_mismatches']:
            self.assertEqual(event['word'], 0)
            self.assertEqual(event['shadow_word'], 0)

    def test_zero_events_survive_without_live_host_idma(self):
        run = ISOLATION['runs']['ring-quiet']
        self.assertTrue(run['no_live_idma'])
        self.assertFalse(run['zero_log_overflow'])
        self.assertEqual(len(run['zero_log']), run['zero_count'])
        unexpected = card.unexpected_zero_indices(run['zero_log'], 16384, ring.RING)
        self.assertEqual(len(unexpected), 8)
        self.assertGreater(unexpected[-1], 65536)

    def test_autonomous_rx_dma_contains_real_zero_substitutions(self):
        for trial, count in ((1, 0), (2, 1), (3, 1)):
            run = ISOLATION['runs'][f'rx-capture-{trial}']
            self.assertTrue(run['autonomous_rx_capture'])
            self.assertEqual(len(run['words']), 8192)
            bad = [i for i, word in enumerate(run['words']) if word != 0x5AA5]
            self.assertEqual(bad, run['anomaly_offsets'])
            self.assertEqual(len(bad), count)
            self.assertTrue(all(run['words'][i] == 0 for i in bad))


if __name__ == '__main__':
    unittest.main()
