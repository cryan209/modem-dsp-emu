"""Hardware-timer IDLE waits persist across host run slices and mask changes."""
import ctypes
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import dsp_conformance as dsp


class IdleTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict('os.environ', {'EICON_ADSP_TIMER': '1'})
        self.environment.start()
        self.lib = dsp.load_library()
        for name in ('adsp2181_idle', 'adsp2181_pc'):
            getattr(self.lib, name).argtypes = [ctypes.c_void_p]
        self.lib.adsp2181_set_imask.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
        self.lib.adsp2181_set_irq.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        self.core = self.lib.adsp2181_create()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.lib.adsp2181_destroy, self.core)

    def boot(self, spec):
        self.lib.adsp2181_reset(self.core)
        self.lib.adsp2181_set_idma_boot_hold(self.core, 1)
        for addr, value, is_pm in dsp.boot_writes(spec):
            self.lib.adsp2181_idma_addr_write(self.core, addr if is_pm else 0x4000 | addr)
            if is_pm:
                self.lib.adsp2181_idma_data_write(self.core, value >> 8)
                value &= 0xFF
            self.lib.adsp2181_idma_data_write(self.core, value)

    def read(self, addr):
        self.lib.adsp2181_idma_addr_write(self.core, 0x4000 | addr)
        return self.lib.adsp2181_idma_data_read(self.core)

    def program(self, timer=False):
        p = dsp.Program('idle_contract')
        if timer:
            p.emit(dsp.mode_control(0x20, True))
        p.emit(dsp.idle(), dsp.load_dreg('AX0', 0xBEEF),
               dsp.store_dreg(0x100, 'AX0'), dsp.mode_control(0x20, False), dsp.idle())
        vector = 0x28 if timer else 0x18  # timer or IRQE
        p.emit(*([dsp.nop()] * (vector - len(p.pm))), dsp.rts(rti=True))
        p.dm_init = [[0x100, 0], [dsp.TIMER_TSCALE, 0],
                     [dsp.TIMER_TCOUNT, 20], [dsp.TIMER_TPERIOD, 1024]]
        return p.as_json()

    def test_idle_without_timer_waits_across_slices_for_external_irq(self):
        self.boot(self.program())
        for budget in (8, 64, 128):
            self.lib.adsp2181_run(self.core, budget)
            self.assertEqual(self.read(0x100), 0)
            self.assertEqual(self.lib.adsp2181_pc(self.core), 1)
            self.assertTrue(self.lib.adsp2181_idle(self.core))
        self.lib.adsp2181_set_imask(self.core, 0x10)
        self.lib.adsp2181_set_irq(self.core, 6, 1)  # IRQE
        self.lib.adsp2181_run(self.core, 32)
        self.assertEqual(self.read(0x100), 0xBEEF)
        self.assertTrue(self.lib.adsp2181_idle(self.core))

    def test_masked_timer_does_not_wake_until_unmasked(self):
        self.boot(self.program(timer=True))
        self.lib.adsp2181_run(self.core, 100)
        self.assertEqual(self.read(0x100), 0)
        self.assertEqual(self.lib.adsp2181_pc(self.core), 2)
        self.assertTrue(self.lib.adsp2181_idle(self.core))
        self.lib.adsp2181_set_imask(self.core, 1)
        self.lib.adsp2181_run(self.core, 32)
        self.assertEqual(self.read(0x100), 0xBEEF)
        self.assertTrue(self.lib.adsp2181_idle(self.core))

    def test_timer_wakeup_results_do_not_depend_on_run_slice_size(self):
        spec = dsp.suite_idle_timer().as_json()
        expected = dsp.emulate(spec)['dm']
        self.boot(spec)
        for _ in range(10_000):
            self.lib.adsp2181_run(self.core, 17)
        self.assertEqual(self.read(spec['done_addr']), dsp.DONE_VALUE)
        actual = [f'{self.read(addr):04x}'
                  for addr in range(spec['result_base'], spec['result_end'])]
        self.assertEqual(actual, expected)

    def test_legacy_run_to_idle_contract_is_preserved(self):
        with patch.dict('os.environ', {'EICON_ADSP_TIMER': '0'}):
            self.boot(self.program())
        self.lib.adsp2181_run(self.core, 32)
        self.assertEqual(self.read(0x100), 0)
        self.assertTrue(self.lib.adsp2181_idle(self.core))
        self.lib.adsp2181_run(self.core, 32)
        self.assertEqual(self.read(0x100), 0xBEEF)
        self.assertTrue(self.lib.adsp2181_idle(self.core))


if __name__ == '__main__':
    unittest.main()
