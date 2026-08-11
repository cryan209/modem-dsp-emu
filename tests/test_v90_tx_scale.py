"""Page 14's transmit line word, and why x4 scaling it was wrong.

Session 245 read `DM(0x3FB4)` as a right-justified 14-bit SPORT word needing a
x4 expansion for `encode_g711()`, because 100% of the published words are exact
mu-law codepoints at x4 and 0-1% at x1. **Session 248 disproved it against the
peer**, which publishes its own timing estimate: two matched tower calls,
identical but for the flag, gave `Timing Offset [ppm] = +8493` and a link error
with the scaling on, and `+0.328` -- run76's own figure -- with it off, going on
to 189 s of data mode at 29,333 bit/s. The scaling is off by default now.

The lesson worth keeping is why the evidence did not decide it, and the first
test below is the proof: a right-justified 14-bit mu-law expansion *is* a
quarter of a PCM16 codepoint, exhaustively, for all 256 codes. So "the words
need scaling" and "the words are already correct in the DSP's own domain" make
the *same* prediction about the x4 share, and the census that looked like
confirmation could never have distinguished them. It is still a real check that
the publisher emits mu-law codepoints in *some* domain; it says nothing about
which.

The mechanics below are still exercised because the knob still exists, as the
A/B that establishes the above.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))


def extract(module: str, pattern: str, namespace: dict | None = None) -> dict:
    """Exec one definition out of a module's source.

    Same reason as `test_g711_mulaw.py`: `eicon_mips_shim` needs unicorn and a
    built dylib, and neither is relevant to arithmetic on 256 integers.
    """
    source = (TOOLS / f"{module}.py").read_text()
    match = re.search(pattern, source, re.S)
    if match is None:
        raise AssertionError(f"{module} has no definition matching {pattern!r}")
    namespace = {} if namespace is None else namespace
    body = match.group(0)
    if body.startswith(" "):        # a method, lifted out of its class
        indent = len(body) - len(body.lstrip(" "))
        body = "\n".join(line[indent:] if line.strip() else line
                         for line in body.splitlines())
    exec(body, namespace)
    return namespace


def signed(word: int) -> int:
    return word - 0x10000 if word & 0x8000 else word


SPORT = extract("dial_tikrnl_drive",
                r"def sport_rx_word.*?(?=\ndef |\nclass |\n#)")
sport_rx_word = SPORT["sport_rx_word"]

SHIM = extract("eicon_mips_shim",
               r"def _mulaw_codepoints.*?(?=\nMULAW_CODEPOINTS)")
MULAW_CODEPOINTS = SHIM["_mulaw_codepoints"]()


class Codepoints(unittest.TestCase):
    def test_table_is_the_mulaw_alphabet(self):
        # 255, not 256: mu-law has two codes for zero.
        self.assertEqual(len(MULAW_CODEPOINTS), 255)
        self.assertIn(0, MULAW_CODEPOINTS)
        self.assertIn(32124, MULAW_CODEPOINTS)
        self.assertIn(-32124, MULAW_CODEPOINTS)
        self.assertEqual(max(MULAW_CODEPOINTS), 32124)

    def test_sport_expansion_times_four_is_a_codepoint_for_every_code(self):
        """Why the x4 census could not decide it (248).

        Right-justified x4 == PCM16 codepoint, for every one of the 256 codes.
        Both readings of the published word therefore predict a ~100% x4 share,
        so the share is not evidence for either. Measured live with the scaling
        off, the share is still 100.0%.
        """
        for code in range(256):
            expanded = signed(sport_rx_word(code, "pcmu"))
            self.assertIn(expanded * 4, MULAW_CODEPOINTS,
                          f"code 0x{code:02x} expands to {expanded}, and "
                          f"{expanded * 4} is not a mu-law codepoint")

    def test_the_right_justified_word_is_almost_never_a_codepoint_itself(self):
        """The x1 share is near zero, which is what makes the census readable.

        It separates "these are mu-law codepoints scaled by four" from "these
        are arbitrary words", which is a live check on the publisher. It does
        not separate the two *domains*: see the test above.
        """
        itself = sum(1 for code in range(256)
                     if signed(sport_rx_word(code, "pcmu")) in MULAW_CODEPOINTS)
        self.assertLess(itself / 256.0, 0.2)

    def test_full_scale_needs_no_clamp(self):
        """x4 of the largest expansion lands exactly on full scale."""
        largest = max(abs(signed(sport_rx_word(code, "pcmu")))
                      for code in range(256))
        self.assertEqual(largest, 8031)
        self.assertEqual(largest * 4, 32124)


class TransmitScale(unittest.TestCase):
    """`_sport_tx_sample` itself, on a stub carrying only its own state."""

    def build(self, enabled: bool):
        namespace = extract(
            "eicon_mips_shim",
            r"    def _sport_tx_sample.*?(?=\n    def )",
            {"MULAW_CODEPOINTS": MULAW_CODEPOINTS,
             "V90D_TX_SPORT_SCALE": enabled})
        method = namespace["_sport_tx_sample"]

        class Card:
            _tx_scale_samples = 0
            _tx_scale_on_codepoint4 = 0
            _tx_scale_on_codepoint1 = 0
            _tx_scale_pointer_words = 0
            _tx_scale_logged = True      # suppress the one-shot print
            _media_samples = 0

        Card._sport_tx_sample = method
        return Card()

    def test_scales_by_four_and_counts_the_alphabet(self):
        card = self.build(True)
        # 0x7F and 0xFF are the two codes for zero, and a zero line word is not
        # counted either way -- it is silence, not evidence about the scale.
        codes = [0x00, 0x40, 0x80, 0xCF, 0x7F, 0xFF]
        for code in codes:
            expanded = signed(sport_rx_word(code, "pcmu"))
            self.assertEqual(card._sport_tx_sample(expanded), expanded * 4)
        self.assertEqual(card._tx_scale_samples, 4)
        self.assertEqual(card._tx_scale_on_codepoint4, 4)
        self.assertEqual(card._tx_scale_pointer_words, 0)

    def test_signed_zero_sentinel_keeps_its_polarity(self):
        """The firmware's +/-2 Ucode-zero token must not become Ucode 1."""
        card = self.build(True)
        self.assertEqual(card._sport_tx_sample(2), 2)
        self.assertEqual(card._sport_tx_sample(-2), -2)

    def test_census_runs_with_the_correction_disabled(self):
        """The evidence and the change are not on the same switch."""
        card = self.build(False)
        expanded = signed(sport_rx_word(0x40, "pcmu"))
        self.assertEqual(card._sport_tx_sample(expanded), expanded)
        self.assertEqual(card._tx_scale_samples, 1)
        self.assertEqual(card._tx_scale_on_codepoint4, 1)

    def test_the_un_overwritten_pointer_passes_through_unscaled(self):
        """0x3764 is the pointer PM 0x19ee re-primes, not a line sample.

        It is above the 8031 a right-justified mu-law word can reach, so it is
        recognisable, and scaling it would put a full-scale DC level on the
        line where the existing behaviour puts -7 dBFS. Left alone, counted
        separately, and kept out of the codepoint shares it would otherwise
        drag down.
        """
        card = self.build(True)
        self.assertEqual(card._sport_tx_sample(0x3764), 0x3764)
        self.assertEqual(card._sport_tx_sample(-20000), -20000)
        self.assertEqual(card._tx_scale_pointer_words, 2)
        self.assertEqual(card._tx_scale_samples, 0)
        self.assertEqual(card._tx_scale_on_codepoint4, 0)

    def test_the_largest_real_sample_is_still_scaled(self):
        """The boundary is inclusive: 8031 is a sample, 8032 is not."""
        card = self.build(True)
        self.assertEqual(card._sport_tx_sample(8031), 32124)
        self.assertEqual(card._sport_tx_sample(-8031), -32124)
        self.assertEqual(card._tx_scale_pointer_words, 0)
        self.assertEqual(card._tx_scale_samples, 2)


if __name__ == "__main__":
    unittest.main()
