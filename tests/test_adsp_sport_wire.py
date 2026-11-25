"""External SPORT events, actual serial bits, and card-backed DMA behavior."""
import ctypes
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import dsp_conformance as d
import eicon_sport_wire_probe as probe


class WireTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict('os.environ', {'EICON_ADSP_SPORT_WIRE':'1', 'EICON_ADSP_TIMER':'1'})
        env.start()
        self.addCleanup(env.stop)
        self.lib = d.load_library()
        for name, args in {
            'adsp2181_sport_clock_edge':[ctypes.c_void_p]+[ctypes.c_int]*5,
            'adsp2181_sport_receive_word':[ctypes.c_void_p, ctypes.c_int, ctypes.c_uint16],
            'adsp2181_sport_transmit_word':[ctypes.c_void_p, ctypes.c_int],
            'adsp2181_pc':[ctypes.c_void_p], 'adsp2181_idle':[ctypes.c_void_p],
            'adsp2181_i':[ctypes.c_void_p, ctypes.c_int],
            'adsp2181_set_pc':[ctypes.c_void_p, ctypes.c_uint16],
            'adsp2181_set_imask':[ctypes.c_void_p, ctypes.c_uint16],
            'adsp2181_cycles':[ctypes.c_void_p],
            'adsp2181_dm':[ctypes.c_void_p], 'adsp2181_pm':[ctypes.c_void_p],
        }.items():
            getattr(self.lib, name).argtypes = args
        self.lib.adsp2181_cycles.restype = ctypes.c_uint64
        self.lib.adsp2181_dm.restype = ctypes.POINTER(ctypes.c_uint16)
        self.lib.adsp2181_pm.restype = ctypes.POINTER(ctypes.c_uint32)
        self.core = self.lib.adsp2181_create()
        self.addCleanup(self.lib.adsp2181_destroy, self.core)

    def boot(self, spec):
        self.lib.adsp2181_reset(self.core)
        self.lib.adsp2181_set_idma_boot_hold(self.core, 1)
        for addr, value, pm in d.boot_writes(spec):
            self.lib.adsp2181_idma_addr_write(self.core, addr if pm else 0x4000|addr)
            if pm:
                self.lib.adsp2181_idma_data_write(self.core, value>>8)
            self.lib.adsp2181_idma_data_write(self.core, value & (255 if pm else 65535))

    def setup_program(self, setup=(), ctl=0x3C0F, ab=1, port=1):
        p = d.Program('wire_unit')
        d.preamble(p)
        p.emit(d.load_dag('I0', 0x2800), d.load_dag('L0', 8), d.load_dag('M0', 1), *setup)
        here = len(p.pm)
        p.emit(d.jump(here))
        p.dm_init = [[0x3FFF, 0x0C07 if port else 0x1407],
                     [0x3FF2 if port else 0x3FF6, ctl],
                     [0x3FEF if port else 0x3FF3, ab],
                     [d.TIMER_TSCALE, 0], [d.TIMER_TCOUNT, 65535],
                     [d.TIMER_TPERIOD, 65535]]
        self.boot(p.as_json())
        self.lib.adsp2181_run(self.core, here)
        return here

    @property
    def dm(self):
        return self.lib.adsp2181_dm(self.core)

    def rx(self, word, port=1):
        return self.lib.adsp2181_sport_receive_word(self.core, port, word)

    def edge(self, rising, bit=0, sync=0):
        return self.lib.adsp2181_sport_clock_edge(self.core, 1, rising, bit, sync, sync)

    def test_rx_formatting_matches_nonconstant_card_inputs(self):
        card = json.loads((ROOT/'tests/data/sport_rest_card12196.json').read_text())
        import eicon_sport_pattern_probe as patterns
        spec = patterns.build().as_json()
        for case in spec['cases']:
            a = case['inputs']
            if a['bits'] < 8:
                continue
            raw = (0x5AA5 if a['alternate'] else 0xB54B) >> (16-a['bits'])
            ctl = (0x3000 if a['alternate'] else 0x2000) | a['dtype']<<4 | a['bits']-1
            self.setup_program(ctl=ctl)
            self.rx(raw)
            off = case['addr']-d.RESULT_BASE
            for chip in card['runs']['pattern']['dsps']:
                self.assertEqual(self.dm[0x2800], int(chip['dm'][off], 16))

    def test_dma_steals_one_cycle_without_executing_an_instruction(self):
        pc = self.setup_program([d.mode_control(0x20, True)])
        before, ticks = self.lib.adsp2181_cycles(self.core), self.dm[d.TIMER_TCOUNT]
        self.rx(0x1234)
        self.assertEqual(self.dm[0x2800], 0x1234)
        self.lib.adsp2181_run(self.core, 1)
        self.assertEqual(self.lib.adsp2181_pc(self.core), pc)
        self.assertEqual(self.lib.adsp2181_cycles(self.core)-before, 1)
        self.assertEqual((ticks-self.dm[d.TIMER_TCOUNT]) & 65535, 1)

    def test_bit_loop_transfers_card_transmit_pattern(self):
        import eicon_sport_tx_probe as tx
        expected = [v<<8 | (v^255) for v in tx.PATTERN]
        self.setup_program([d.load_dag('I1', 0x2900), d.load_dag('L1', 8),
                            d.load_dag('M1', 1)], ab=0x283)
        for n, word in enumerate(expected): self.dm[0x2900+n] = word
        for word in expected*2:
            received = 0
            for k in range(16):
                dt = self.edge(1, sync=k==0)
                self.assertTrue(dt & 2)
                received = (received<<1) | (dt & 1)
                self.lib.adsp2181_run(self.core, 4)
                self.edge(0, dt&1, k==0)
                self.lib.adsp2181_run(self.core, 4)
            self.assertEqual(received, word)
        self.assertEqual(list(self.dm[0x2800:0x2808]), expected)
        self.assertEqual(self.lib.adsp2181_i(self.core, 0), 0x2800)
        self.assertEqual(self.lib.adsp2181_i(self.core, 1), 0x2900)

    def test_every_tx_dag_matches_card_pattern_without_cpu_write_callbacks(self):
        import eicon_sport_tx_probe as tx
        expected = [v<<8 | (v^255) for v in tx.PATTERN]
        writes = []
        callback_type = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, ctypes.c_int32)
        callback = callback_type(lambda cpu, port, value: writes.append((port,value)))
        self.lib.adsp2181_set_callbacks.argtypes = [ctypes.c_void_p]*4
        self.lib.adsp2181_set_callbacks(self.core, None, callback, None)
        for i in range(8):
            m = ((i+1)%4) | (i&4)
            ab = (i<<9) | ((m%4)<<7) | 2
            self.setup_program([d.load_dag(f'I{i}',0x2900), d.load_dag(f'L{i}',8),
                                d.load_dag(f'M{m}',1)], ab=ab)
            for n, value in enumerate(expected): self.dm[0x2900+n] = value
            values = []
            for _ in range(24):
                values.append(self.lib.adsp2181_sport_transmit_word(self.core,1))
                self.lib.adsp2181_run(self.core,1)
            self.assertEqual(values, expected*3)
            self.assertEqual(self.lib.adsp2181_i(self.core,i),0x2900)
        self.assertEqual(writes, [])

    def test_normal_framing_reproduces_measured_one_bit_offset(self):
        self.setup_program(ctl=0x200F)
        for k, bit in enumerate([int(x) for x in f'{0x5AA5:016b}']+[1]):
            self.edge(0, bit, k==0)
            self.lib.adsp2181_run(self.core, 4)
        self.assertEqual(self.dm[0x2800], 0xB54B)

    def test_disabled_partial_reset_and_unsupported_modes(self):
        self.setup_program()
        for k in range(7): self.edge(0, 1, k==0)
        # A CPU control write must discard the unfinished serial word.
        pm = self.lib.adsp2181_pm(self.core)
        pm[0x300] = d.load_dreg('AX0', 0x0407)
        pm[0x301] = d.store_dreg(0x3FFF, 'AX0')
        self.lib.adsp2181_set_pc(self.core, 0x300)
        self.lib.adsp2181_run(self.core, 2)
        self.assertEqual(self.rx(0x1234), -1)
        self.assertEqual(self.edge(0, 1, 1), 0)
        self.setup_program(ctl=0x700F)
        self.assertEqual(self.edge(0, 1, 1), -1)
        self.setup_program(ctl=0x8207, port=0)
        self.assertEqual(self.rx(0x1234, port=0), -1)
        self.setup_program()
        self.rx(0x55AA)
        self.lib.adsp2181_reset(self.core)
        self.lib.adsp2181_run(self.core, 1)
        self.assertNotEqual(self.lib.adsp2181_pc(self.core), 0)  # no stale DMA debt

    def test_tx_cpu_buffer_is_independent_of_active_shift_word(self):
        self.setup_program([d.load_dreg('AX0', 0x1234),
                            d.reg_move(3, 11, 0, d.DREG['AX0'])], ab=0)
        bits = []
        for k in range(16):
            bits.append(self.edge(1, sync=k==0)&1)
            self.edge(0, 0, k==0)
            if k == 7:
                pm = self.lib.adsp2181_pm(self.core)
                pm[0x300] = d.load_dreg('AX0', 0xABCD)
                pm[0x301] = d.reg_move(3, 11, 0, d.DREG['AX0'])
                self.lib.adsp2181_set_pc(self.core, 0x300)
                self.lib.adsp2181_run(self.core, 2)
        self.assertEqual(int(''.join(map(str,bits)),2), 0x1234)
        self.assertEqual(self.lib.adsp2181_sport_transmit_word(self.core, 1), 0xABCD)
        self.assertEqual(self.lib.adsp2181_sport_transmit_word(self.core, 1), -1)

    def test_wrap_irq_waits_for_dma_and_idle_entry_across_single_cycle_slices(self):
        p = d.Program('wire_irq')
        p.emit(d.jump(0), *[d.nop()]*3)
        for _ in range(d.IRQ_VECTORS):
            p.emit(d.load_dreg('AX0', 0xCAFE), d.store_dreg(0x2000, 'AX0'), d.rts(rti=True), d.nop())
        p.pm[0] = d.jump(len(p.pm))
        d.preamble(p)
        p.emit(d.load_dag('I0', 0x2800), d.load_dag('L0', 2), d.load_dag('M0', 1),
               d.load_group3('IMASK', 2), d.idle())
        p.dm_init = [[0x3FFF,0xC07], [0x3FF2,0x300F], [0x3FEF,1], [0x2000,0]]
        self.boot(p.as_json())
        self.lib.adsp2181_run(self.core, 100)
        self.assertTrue(self.lib.adsp2181_idle(self.core))
        self.rx(0x1234)
        self.lib.adsp2181_run(self.core, 1)
        self.assertTrue(self.lib.adsp2181_idle(self.core))  # no wrap yet
        self.rx(0x5678)
        for _ in range(4):  # one DMA cycle, three IDLE wake/entry cycles
            self.assertEqual(self.lib.adsp2181_run(self.core, 1), 1)
            self.assertEqual(self.dm[0x2000], 0)
        self.assertEqual(self.lib.adsp2181_pc(self.core), 0x24)
        self.lib.adsp2181_run(self.core, 2)
        self.assertEqual(self.dm[0x2000], 0xCAFE)

    def test_original_tx_overhead_program_obeys_card_cycle_equation(self):
        import eicon_sport_tx_probe as tx
        spec = tx.build_overhead().as_json()
        self.boot(spec)
        for _ in range(1000):
            self.lib.adsp2181_run(self.core, 1024)
            self.lib.adsp2181_sport_transmit_word(self.core, 1)
            if self.dm[d.DONE_ADDR] == d.DONE_VALUE: break
        self.assertEqual(self.dm[d.DONE_ADDR], d.DONE_VALUE)
        for case in spec['cases']:
            start, end, before, after = self.dm[case['addr']:case['addr']+4]
            self.assertEqual(((before-after)&65535)-32770, end-start)

    def test_real_card_dag_programs_with_explicit_word_clock(self):
        card = json.loads((ROOT/'tests/data/sport_wire_card12196.json').read_text())
        for index in range(8):
            spec = probe.build_index(index).as_json()
            self.boot(spec)
            # The host supplies a frame clock. Exact absolute oscillator
            # phase was not measured on the card; compare data/wrap state,
            # not case-start phase or absolute timer stamps.
            for _ in range(400):
                self.lib.adsp2181_run(self.core, 8192)
                self.rx(0xFF)
                if self.dm[d.DONE_ADDR] == d.DONE_VALUE: break
            self.assertEqual(self.dm[d.DONE_ADDR], d.DONE_VALUE)
            hardware = card['runs']['dag']['dsps'][index]['dm']
            for case in spec['cases']:
                off = case['addr']-d.RESULT_BASE
                if case['op'] == 'ring':
                    self.assertEqual(list(self.dm[case['addr']:case['addr']+8]),
                                     [int(v,16) for v in hardware[off:off+8]])
                elif case['op'] == 'SPORT1 RX circular DAG':
                    # First two outputs are final index and IRQ count.
                    # Records then contain timestamp, post-wrap I, RX word.
                    base = case['addr']+10
                    self.assertGreater(self.dm[case['addr']+1], 0)
                    for n in range(min(self.dm[case['addr']+1], 16)):
                        self.assertEqual(self.dm[base+3*n+1], int(hardware[off+10+3*n+1],16))
                        self.assertEqual(self.dm[base+3*n+2], 0xFF)

    def test_duplex_probe_obeys_card_cycle_equation(self):
        import eicon_sport_duplex_probe as duplex
        spec = duplex.build().as_json()
        self.boot(spec)
        for _ in range(4000):
            self.lib.adsp2181_run(self.core, 1024)
            self.rx(0xFF)
            self.lib.adsp2181_sport_transmit_word(self.core, 1)
            if self.dm[d.DONE_ADDR] == d.DONE_VALUE:
                break
        self.assertEqual(self.dm[d.DONE_ADDR], d.DONE_VALUE)
        rows = list(duplex.measurements(spec, list(self.dm[spec['result_base']:spec['result_end']])))
        self.assertEqual(len(rows), 64)
        for row in rows:
            self.assertTrue(row['matches'], row)
            if row['rx']:
                self.assertGreater(row['rx_min'], 0)
            if row['tx']:
                self.assertGreater(row['tx_min'], 0)

    def test_two_pending_dma_transfers_steal_two_cycles(self):
        here = self.setup_program(
            [d.load_dag('I4', 0x2900), d.load_dag('L4', 8), d.load_dag('M4', 1)],
            ab=(4 << 9) | 3)
        self.dm[0x2900] = 0xCAFE
        self.assertEqual(self.lib.adsp2181_sport_transmit_word(self.core, 1), 0xCAFE)
        self.rx(0x1234)
        before = self.lib.adsp2181_cycles(self.core)
        for _ in range(2):
            self.assertEqual(self.lib.adsp2181_run(self.core, 1), 1)
            self.assertEqual(self.lib.adsp2181_pc(self.core), here)
        self.assertEqual(self.lib.adsp2181_cycles(self.core)-before, 2)
        self.assertEqual(self.lib.adsp2181_i(self.core, 0), 0x2801)
        self.assertEqual(self.lib.adsp2181_i(self.core, 4), 0x2901)
        self.assertEqual(self.dm[0x2800], 0x1234)

    def test_extended_stride_programs_match_card_rings_and_wrap_records(self):
        card = json.loads((ROOT/'tests/data/sport_duplex_card12196.json').read_text())
        for index in range(8):
            spec = probe.build_strides(index).as_json()
            self.boot(spec)
            for _ in range(800):
                self.lib.adsp2181_run(self.core, 8192)
                self.rx(255)
                if self.dm[d.DONE_ADDR] == d.DONE_VALUE:
                    break
            self.assertEqual(self.dm[d.DONE_ADDR], d.DONE_VALUE)
            hw = [int(v,16) for v in card['runs']['strides']['dsps'][index]['dm']]
            for c in spec['cases']:
                addr, off = c['addr'], c['addr']-d.RESULT_BASE
                if c['op'] == 'ring':
                    self.assertEqual(list(self.dm[addr:addr+8]), hw[off:off+8])
                elif c['op'] == 'SPORT1 RX circular DAG':
                    if not c['inputs']['stride']:
                        self.assertEqual(self.dm[addr+1], 0)
                    else:
                        self.assertGreater(self.dm[addr+1], 0)
                        n = min(self.dm[addr+1], hw[off+1], 16)
                        for k in range(n):
                            self.assertEqual(self.dm[addr+11+3*k], hw[off+11+3*k])
                            self.assertEqual(self.dm[addr+12+3*k], hw[off+12+3*k])


if __name__ == '__main__': unittest.main()
