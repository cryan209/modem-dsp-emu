"""SPORT conversion pipelines survive host slices and reset without extra writes."""
import ctypes
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import dsp_conformance as dsp


class SportTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict('os.environ',
                                 {'EICON_ADSP_SPORT': '1', 'EICON_ADSP_TIMER': '0'})
        environment.start()
        self.addCleanup(environment.stop)
        self.lib = dsp.load_library()
        self.core = self.lib.adsp2181_create()
        self.addCleanup(self.lib.adsp2181_destroy, self.core)

    def boot(self, words):
        program = dsp.Program('sport_host_contract')
        program.emit(*words)
        program.finish()
        program.dm_init = [[0x3FF6, 0x27], [0x100, 0], [0x101, 0]]
        self.lib.adsp2181_reset(self.core)
        self.lib.adsp2181_set_idma_boot_hold(self.core, 1)
        for addr, value, is_pm in dsp.boot_writes(program.as_json()):
            self.lib.adsp2181_idma_addr_write(self.core, addr if is_pm else 0x4000 | addr)
            if is_pm:
                self.lib.adsp2181_idma_data_write(self.core, value >> 8)
                value &= 0xFF
            self.lib.adsp2181_idma_data_write(self.core, value)

    def read(self, addr):
        self.lib.adsp2181_idma_addr_write(self.core, 0x4000 | addr)
        return self.lib.adsp2181_idma_data_read(self.core)

    def writes(self):
        return [dsp.load_dreg('AX0', 0x1234),
                dsp.reg_move(3, 9, 0, dsp.DREG['AX0'])]

    def test_conversion_latency_survives_single_instruction_slices(self):
        self.boot(self.writes() + [dsp.reg_move(0, dsp.DREG['AX1'], 3, 9),
                                  dsp.reg_move(0, dsp.DREG['AY0'], 3, 9),
                                  dsp.store_dreg(0x100, 'AX1'),
                                  dsp.store_dreg(0x101, 'AY0')])
        for _ in range(6):
            self.lib.adsp2181_run(self.core, 1)
        # The card returns the raw write immediately and native mu-law later.
        self.assertEqual(self.read(0x100), 0x1234)
        self.assertEqual(self.read(0x101), 0xFF8D)

    def test_reset_discards_an_unfinished_conversion(self):
        self.boot(self.writes())
        self.lib.adsp2181_run(self.core, 2)
        self.boot([dsp.reg_move(0, dsp.DREG['AX1'], 3, 9), dsp.nop(),
                   dsp.reg_move(0, dsp.DREG['AY0'], 3, 9),
                   dsp.store_dreg(0x100, 'AX1'), dsp.store_dreg(0x101, 'AY0')])
        self.lib.adsp2181_run(self.core, 16)
        self.assertEqual(self.read(0x100), 0)
        self.assertEqual(self.read(0x101), 0)

    def test_conversion_does_not_duplicate_transmit_write_callbacks(self):
        writes = []
        callback_type = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, ctypes.c_int32)
        callback = callback_type(lambda cpu, port, value: writes.append((port, value)))
        self.lib.adsp2181_set_callbacks.argtypes = [ctypes.c_void_p] * 4
        self.lib.adsp2181_set_callbacks.restype = None
        self.lib.adsp2181_set_callbacks(self.core, None, callback, None)
        self.boot(self.writes() + [dsp.nop(), dsp.nop()])
        self.lib.adsp2181_run(self.core, 16)
        self.assertEqual(writes, [(0, 0x1234)])

    def test_legacy_sport1_frame_preserves_duplex_words_and_write_indicator(self):
        # A small ISR reproduces the native Analog RX/previous-TX swap. On a
        # zero input it deliberately omits TX1 to check that the frame API
        # reports a retained latch without claiming a new transmit write.
        words = [dsp.jump(0x30)] + [dsp.nop()] * 0x1F
        words += [dsp.jump(0x40)]
        words += [dsp.nop()] * (0x30 - len(words))
        words += [dsp.idle(), dsp.jump(0x30)]
        words += [dsp.nop()] * (0x40 - len(words))
        words += [dsp.reg_move(0, dsp.DREG['SR0'], 3, 10),
                  dsp.dm_read_abs(0x102, 'AR'),
                  0x2A7EEA,  # AR = SR0 + 0, SR0 = previous AR (parallel)
                  dsp.store_dreg(0x100, 'AR'),
                  dsp.store_dreg(0x101, 'SR0'),
                  dsp.jump(0x47, cond=0),  # IF EQ skip the TX write
                  dsp.reg_move(3, 11, 0, dsp.DREG['SR0']), dsp.rts(rti=True)]
        with patch.dict('os.environ', {'EICON_ADSP_SPORT': '0'}):
            self.boot(words)
        self.lib.adsp2181_set_imask.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
        self.lib.adsp2181_idle.argtypes = [ctypes.c_void_p]
        self.lib.adsp2181_sport1_frame.argtypes = [ctypes.c_void_p, ctypes.c_uint16,
                                                ctypes.c_int]
        self.lib.adsp2181_sport1_frame.restype = ctypes.c_uint32
        self.lib.adsp2181_set_imask(self.core, 4)  # IRQ1 / SPORT1 TX alias
        self.lib.adsp2181_run(self.core, 64)
        self.assertTrue(self.lib.adsp2181_idle(self.core))
        last_tx = 0
        for index, rx in enumerate((0, 0xFB2E, 0x04D2, 0x8000, 0x7FFF, 0, 0xFFFF)):
            with self.subTest(rx=hex(rx)):
                tx = 0x1356 + index * 2  # distinct from RX and preceding TX
                self.lib.adsp2181_idma_addr_write(self.core, 0x4102)
                self.lib.adsp2181_idma_data_write(self.core, tx)
                frame = self.lib.adsp2181_sport1_frame(self.core, rx, 64)
                self.assertEqual(self.read(0x100), rx)
                self.assertEqual(self.read(0x101), tx)
                if rx:
                    last_tx = tx
                self.assertEqual(frame, last_tx | (0x10000 if rx else 0))
                self.assertTrue(self.lib.adsp2181_idle(self.core))

    def test_legacy_frame_adapter_mode_preserves_unconverted_latches(self):
        with patch.dict('os.environ', {'EICON_ADSP_SPORT': '0'}):
            self.boot(self.writes() + [dsp.nop(), dsp.nop(),
                                      dsp.reg_move(0, dsp.DREG['AX1'], 3, 9),
                                      dsp.store_dreg(0x100, 'AX1')])
        self.lib.adsp2181_run(self.core, 16)
        self.assertEqual(self.read(0x100), 0x1234)


if __name__ == '__main__':
    unittest.main()
