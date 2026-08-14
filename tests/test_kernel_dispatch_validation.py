"""The V90D configuration check accepts what live answerers actually carry.

The check used to demand equality on `Info0_setup` and `Norm_H`, which are
host inputs the firmware then adds bits to.  It therefore rejected a card that
had got *further* -- one that had loaded V.8 -- rather than one that was
misconfigured, and that was enough to stop `--answerer-kernel-dispatch` from
running at all.  The values below are measured, not invented: 0xF1FD from the
native-MIPS answerer and 0xF8FD from this backend, both against the guide's
0xF0FD default, and Norm_H 0x0021, which `addsp_database.md` records as
constant across every live capture.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from dial_kernel_dispatch import DM_DB, LiveKernelModem


DELAY_CORRECTION = 0x0010

GOOD = {
    0x00: 0x00C4, 0x01: 0x0484, 0x02: 0x0030, 0x04: 0x6000,
    0x07: 0xF0FD, 0x24: DELAY_CORRECTION, 0x28: 0x0001, 0x29: 0x8100,
    0x2A: 0x001F, 0x79: 0x003F, 0x7A: 0xFFFF, 0x7B: 0x03B7,
    0x7C: 0x000E, 0x7D: 0x0015, 0x7E: 0x000E, 0x7F: 0x0015,
}


class Stub:
    """Only what the check touches."""

    def __init__(self, overrides=None):
        words = dict(GOOD)
        words.update(overrides or {})
        self.dm = {DM_DB + offset: value for offset, value in words.items()}
        self.delay_correction = DELAY_CORRECTION

    def check(self):
        LiveKernelModem._validate_v90d_configuration(self)


class ValidationTests(unittest.TestCase):
    def test_guide_default_configuration_passes(self):
        Stub().check()

    def test_accepts_the_native_mips_info0_setup(self):
        Stub({0x07: 0xF1FD}).check()

    def test_accepts_this_backends_info0_setup(self):
        Stub({0x07: 0xF8FD}).check()

    def test_accepts_norm_h_with_v8_resident(self):
        Stub({0x28: 0x0021}).check()

    def test_rejects_info0_setup_with_a_host_bit_cleared(self):
        with self.assertRaises(RuntimeError) as raised:
            Stub({0x07: 0xF0FC}).check()
        self.assertIn("+07=f0fc", str(raised.exception))

    def test_rejects_norm_h_without_v8(self):
        with self.assertRaises(RuntimeError) as raised:
            Stub({0x28: 0x0020}).check()
        self.assertIn("+28=0020", str(raised.exception))

    def test_still_rejects_a_wrong_rate_ceiling(self):
        with self.assertRaises(RuntimeError):
            Stub({0x7D: 0x000E}).check()

    def test_still_rejects_a_lost_gen_setup2(self):
        with self.assertRaises(RuntimeError):
            Stub({0x02: 0x0000}).check()


if __name__ == "__main__":
    unittest.main()
