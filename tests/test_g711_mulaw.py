"""Every `linear_to_mulaw` in this tree, against ITU-T G.711 over full scale.

Session 205: all seven copies shifted by 5 where a 16-bit input needs 8, so the
segment search ran past segment 7 and took the saturation arm for every
magnitude at or above 3964 -- -18.3 dBfs. The default probe stimulus, a
20000-amplitude sine, came out clipped on 87.5% of its samples spanning 7 of
its 33 codes, with a 1700 Hz alias only 10.2 dB below the 2100 Hz fundamental.

Nothing caught it because nothing compared these functions to the reference:
the exhaustive sweep in `tools/adsp_arith_oracle.py` checks the *firmware*
encoder at PM 0x1810, which is what the live transmit path actually uses and
which is correct. These are the host-side copies that build stimuli.

Sweeping all 65536 inputs per copy is the whole point -- the defect lived
entirely above -18 dBfs and any spot check near zero passes.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

from adsp_arith_oracle import ulaw_encode  # noqa: E402  the reference

MODULES = (
    "dial_sport_drive",
    "dial_standalone_drive",
    "dial_tikrnl_drive",
    "dial_v8_call",
    "dial_v8_supervisor",
    "eicon_mips_shim",
    "v8_standalone_capture",
)


def extract(module: str):
    """Pull the function out of its source rather than importing the module.

    `eicon_mips_shim` needs unicorn and a built dylib, and none of that is
    relevant to arithmetic on 65536 integers.
    """
    source = (TOOLS / f"{module}.py").read_text()
    match = re.search(r"def linear_to_mulaw.*?(?=\n(?:def |class |@))",
                      source, re.S)
    if match is None:
        raise AssertionError(f"{module} has no linear_to_mulaw")
    namespace: dict = {}
    exec(match.group(0), namespace)
    return namespace["linear_to_mulaw"]


class MulawEncoderTests(unittest.TestCase):
    def test_every_copy_matches_itu_over_full_scale(self):
        inputs = range(-32768, 32768)
        for module in MODULES:
            with self.subTest(module=module):
                encode = extract(module)
                wrong = [value for value in inputs
                         if encode(value) != ulaw_encode(value)]
                self.assertEqual(
                    wrong[:8], [],
                    f"{module}: {len(wrong)} of 65536 inputs disagree with "
                    "ITU-T G.711")

    def test_no_copy_saturates_early(self):
        """The specific shape of the defect, named so a regression is legible.

        A shift-by-5 search saturates from 3964 up; asserting on the count of
        distinct codes across a full-scale sine catches that directly, where an
        assertion on any single sample would not.
        """
        import math
        sine = [int(20000 * math.sin(2 * math.pi * 2100 * i / 8000))
                for i in range(8000)]
        for module in MODULES:
            with self.subTest(module=module):
                encode = extract(module)
                self.assertEqual(len(set(encode(s) for s in sine)),
                                 len(set(ulaw_encode(s) for s in sine)))


if __name__ == "__main__":
    unittest.main()
