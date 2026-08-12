"""The derived image layout must reproduce the shim's hardcoded PRI constants.

`eicon_mips_shim.py` has carried `te_dmlt.pm`'s layout as module constants since
the harness was written, and every anchor in it is `BIAS + <file offset>`.  The
point of `eicon_mips_image.derive_layout` is to get those same four numbers out
of any protocol image, so the first thing it has to do is agree with the four
that are known to work -- a derivation that produced a *different* base for
`.pm` would be wrong no matter how plausible it looked on a new image.

The BRI v2 expectations are from Session 105's static analysis of `te_dmlt.2q0`:
load base 0x80000000 (0x11000 below the PRI image), entry 0x8008e5f8, which is
the routine that reads the card type from `0xa0000068` and defaults it to 53.

These need `docs/firmware/`, which is tracked, so they are not skipped when the
images are absent -- a missing firmware image is a broken checkout, not an
optional extra.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import eicon_mips_image as mi

FIRMWARE = ROOT / 'docs' / 'firmware'


class PriLayoutTests(unittest.TestCase):
    """The values eicon_mips_shim.py hardcodes, derived instead."""

    @classmethod
    def setUpClass(cls):
        cls.layout = mi.derive_layout(FIRMWARE / 'te_dmlt.pm')

    def test_base_matches_the_shim_bias(self):
        self.assertEqual(self.layout.base, 0x80011000)

    def test_entry_matches_mips_entry(self):
        self.assertEqual(self.layout.entry, 0x80082F90)

    def test_gp_matches_the_shim_constant(self):
        self.assertEqual(self.layout.gp, 0x800FA3B5)

    def test_stack_top_matches_the_shim_constant(self):
        self.assertEqual(self.layout.stack_top, 0x80338700)

    def test_stack_top_is_the_protocol_end_address(self):
        # The host derives DspCodeBaseAddr from header +0x7c and the image
        # starts its stack there, so the two agreeing is a cross-check on both.
        self.assertEqual(self.layout.stack_top, self.layout.protocol_end)

    def test_anchor_offsets_still_resolve(self):
        # HOST_WRITE, the anchor every host-port transaction goes through.
        self.assertEqual(self.layout.addr(0x71950), 0x80082950)
        self.assertEqual(self.layout.offset(0x80082950), 0x71950)

    def test_addresses_outside_the_image_are_rejected(self):
        with self.assertRaises(mi.FormatError):
            self.layout.offset(0x80338700)


class BriV2LayoutTests(unittest.TestCase):
    """te_dmlt.2q0..2q3: one build linked four times, 4 MB apart."""

    def test_first_adapter(self):
        layout = mi.derive_layout(FIRMWARE / 'te_dmlt.2q0')
        self.assertEqual(layout.base, 0x80000000)
        self.assertEqual(layout.entry, 0x8008E5F8)
        self.assertEqual(layout.gp, 0x8015231C)
        self.assertEqual(layout.stack_top, 0x801EBFD0)
        self.assertEqual(layout.stack_top, layout.protocol_end)

    def test_the_four_adapters_are_the_same_image_relocated(self):
        layouts = [mi.derive_layout(FIRMWARE / f'te_dmlt.2q{n}')
                   for n in range(4)]
        self.assertEqual([l.base for l in layouts],
                         [0x80000000, 0x80400000, 0x80800000, 0x80C00000])
        self.assertEqual({l.size for l in layouts}, {0x152290})
        # Same code at the same file offset in all four.
        self.assertEqual({l.entry - l.base for l in layouts}, {0x8E5F8})
        self.assertEqual({l.build for l in layouts},
                         {'TE_DMLT, Build 108-971, Protocol 6.03(V14) 104-8 '
                          '[F#00FF]'})

    def test_bri_image_loads_below_the_pri_one(self):
        # The reason a .pm-derived anchor address means nothing in a .2q0:
        # the same file offset is 0x11000 lower in virtual address.
        pri = mi.derive_layout(FIRMWARE / 'te_dmlt.pm')
        bri = mi.derive_layout(FIRMWARE / 'te_dmlt.2q0')
        self.assertEqual(pri.base - bri.base, 0x11000)


class FlatVectorLayoutTests(unittest.TestCase):
    """Later files include reset/shared RAM and use absolute, no-gp code."""

    def test_122_11_analog_image(self):
        layout = mi.derive_layout(FIRMWARE / 'te_dmlt.am')
        self.assertEqual(layout.base, 0x80000000)
        self.assertEqual(layout.entry, 0x8014EA24)
        self.assertIsNone(layout.gp)
        self.assertEqual(layout.stack_top, 0x8022B410)
        self.assertEqual(layout.stack_top, layout.protocol_end)

    def test_other_flat_images(self):
        expected = {
            'te_dmlt.2qm': (0x80174BE0, 0x802A56A0),
            'te_dmlt.qm': (0x800BA318, 0x80135E20),
        }
        for name, (entry, stack) in expected.items():
            with self.subTest(image=name):
                layout = mi.derive_layout(FIRMWARE / name)
                self.assertEqual(layout.base, 0x80000000)
                self.assertEqual(layout.entry, entry)
                self.assertIsNone(layout.gp)
                self.assertEqual(layout.stack_top, stack)
                self.assertEqual(layout.stack_top, layout.protocol_end)


if __name__ == '__main__':
    unittest.main()
