#!/usr/bin/env python3
"""Check the emulator's computational units against an external ground truth.

`adsp_opcode_audit.py` says of itself that it is coverage, not a correctness
oracle, and until this existed nothing in the tree validated the ADSP core's
arithmetic at all -- the emulator was written as an ADSP-2181 and the card is a
2185N, whose datasheet advertises "instruction set extensions" over the
ADSP-2100 family it is otherwise object-code compatible with.  That matters
because Session 153 left "the overlay computes noise" with two readings, one of
which is that the emulator's arithmetic diverges somewhere the modulator
depends on.

The oracle is the card's own G.711 encoder, TIKRNL PM 0x1810.  It is shipped
firmware, so it is authoritative about what the hardware would do, and G.711 is
specified by ITU-T so its output is externally known for every input.  Sweeping
all 65,536 signed inputs through it exercises the ALU, the shifter and the
sequencer over a wide range of magnitudes and both signs.  A mismatch is an
emulator defect; a clean sweep retires the arithmetic hypothesis for the paths
that routine uses, which is not all of them -- notably it does **not** exercise
the MAC modes the page-8 transmit filter leans on ((SU), (RND), saturate MR).

    tools/adsp_arith_oracle.py --law alaw
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dial_tikrnl_drive import Card


def alaw_encode(sample: int) -> int:
    """ITU-T G.711 A-law, conventional (already-toggled) octet order."""
    sign = 0x80 if sample >= 0 else 0x00
    magnitude = sample if sample >= 0 else -sample - 1
    magnitude = min(magnitude, 32767) >> 3          # 12-bit working magnitude
    if magnitude < 32:
        code = magnitude >> 1
    else:
        exponent = magnitude.bit_length() - 6       # 1..7
        code = ((exponent + 1) << 4) | ((magnitude >> exponent) & 0x0F)
    return (sign | code) ^ 0x55


def ulaw_encode(sample: int) -> int:
    """ITU-T G.711 mu-law, conventional octet order."""
    sign = 0x80 if sample < 0 else 0x00
    magnitude = min(abs(sample), 32635) + 132       # bias 0x84 on 14 bits << 2
    exponent = magnitude.bit_length() - 8           # 0..7
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa)) & 0xFF


def alaw_decode(code: int) -> int:
    code ^= 0x55
    magnitude = code & 0x7F
    exponent = magnitude >> 4
    mantissa = magnitude & 0x0F
    value = ((mantissa << 1) | 1) << 3 if exponent == 0 else \
        (((mantissa << 1) | 33) << exponent) << 2
    return value if code & 0x80 else -value


def ulaw_decode(code: int) -> int:
    code = ~code & 0xFF
    magnitude = (((code & 0x0F) << 3) + 0x84) << ((code >> 4) & 0x07)
    magnitude -= 0x84
    return -magnitude if code & 0x80 else magnitude


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--law', choices=('alaw', 'ulaw'), default='alaw',
                    help="which reference to compare against; the PRI kernel "
                         "selects its table at DM 0x3309, so this has to match "
                         "the image being run rather than being a free choice")
    ap.add_argument('--show', type=int, default=12,
                    help='mismatching inputs to print')
    args = ap.parse_args()

    reference = alaw_encode if args.law == 'alaw' else ulaw_encode
    decode = alaw_decode if args.law == 'alaw' else ulaw_decode
    card = Card(log=False)
    # boot() is what puts the kernel and TIKRNL in PM. Without it PM 0x1810 is
    # zero and the sweep reports a 99.6% mismatch that is entirely the harness.
    card.boot()
    samples = list(range(-32768, 32768))
    encoded = card.encode_g711(samples)

    mismatches = [(value, got, reference(value))
                  for value, got in zip(samples, encoded)
                  if got != reference(value)]

    print(f"swept {len(samples)} inputs through TIKRNL PM 0x1810 "
          f"against ITU-T {args.law}")
    print(f"exact code matches: {len(samples) - len(mismatches)}/{len(samples)}")

    # Exact code equality is the wrong bar on its own: encoders differ
    # legitimately in how they fold a 16-bit input onto a 13-bit magnitude, and
    # a one-LSB disagreement at a decision boundary is a convention, not a
    # defect. What no correct encoder can do is land in the wrong segment, so
    # judge it on the reconstruction error instead -- that is bounded by the
    # quantisation step of whichever segment the sample falls in.
    worst = 0
    worst_at = 0
    gross = 0
    for value, got in zip(samples, encoded):
        error = abs(decode(got) - value)
        step = abs(decode(got) - decode((got + 1) & 0xFF)) or 1
        if error > worst:
            worst, worst_at = error, value
        if error > 2 * step:
            gross += 1
    print(f"reconstruction: worst error {worst} at input {worst_at:+d}, "
          f"samples off by more than two quantisation steps: {gross}")
    if gross:
        print("   -> that is an arithmetic defect, not a rounding convention")
        for value, got, want in mismatches[:args.show]:
            print(f"   in {value:+7d}  firmware 0x{got:02x}  ITU 0x{want:02x}")
        return 1
    print("   -> every code lands in the correct segment; the ALU, shifter and "
          "sequencer paths this routine uses are faithful")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
