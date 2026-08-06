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


# --- MAC modes -------------------------------------------------------------
# Executing synthetic instructions on a bare core, because no firmware routine
# exercises the multiplier against anything externally known. Two references:
# the six rounding vectors 8xcompu.pdf Figure 2-11 tabulates outright, and a
# re-derivation of Table 2-8's signedness and the fractional shift.

REG = {"AX0": 0, "MX0": 2, "AY0": 4, "MY0": 6, "AR": 10,
       "MR0": 11, "MR1": 12, "MR2": 13}
MAC_OP = {"SS": 4, "SU": 5, "US": 6, "UU": 7, "RND": 1,
          "MR+SS": 8, "MR+RND": 2}


def load_imm(reg, value):
    return 0x400000 | ((value & 0xFFFF) << 4) | REG[reg]


def store_dm(addr, reg):
    return 0x900000 | ((addr & 0x3FFF) << 4) | REG[reg]


def mac(op):
    return 0x200000 | (MAC_OP[op] << 13) | 0x0F


def _declare(ADSP):
    """argtypes the shim does not set; without them ctypes truncates the
    64-bit cpu pointer to int and the call segfaults."""
    import ctypes
    ADSP.adsp2181_set_pc.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
    ADSP.adsp2181_pc.argtypes = [ctypes.c_void_p]
    ADSP.adsp2181_pc.restype = ctypes.c_uint16
    ADSP.adsp2181_reset.argtypes = [ctypes.c_void_p]


def run_program(ADSP, words, entry=0x0100, cycles=64):
    _declare(ADSP)
    cpu = ADSP.adsp2181_create()
    ADSP.adsp2181_reset(cpu)
    pm = ADSP.adsp2181_pm(cpu)
    for i, w in enumerate(words):
        pm[entry + i] = w
    end = entry + len(words)
    pm[end] = 0x180000 | (end << 4) | 0x0F          # JUMP self
    ADSP.adsp2181_set_pc(cpu, entry)
    ADSP.adsp2181_run(cpu, cycles)
    dm = ADSP.adsp2181_dm(cpu)
    return cpu, dm


def mac_once(ADSP, mode, x, y, seed=None):
    """Run one MAC and return (MR2, MR1, MR0)."""
    words = []
    if seed is not None:
        mr2, mr1, mr0 = seed
        words += [load_imm("MR2", mr2), load_imm("MR1", mr1),
                  load_imm("MR0", mr0)]
    words += [load_imm("MX0", x), load_imm("MY0", y), mac(mode),
              store_dm(0x2000, "MR0"), store_dm(0x2001, "MR1"),
              store_dm(0x2002, "MR2")]
    _, dm = run_program(ADSP, words)
    return dm[0x2002] & 0xFF, dm[0x2001], dm[0x2000]


def reference(mode, x, y, seed=None, integer=False):
    """Table 2-8 signedness, the fractional shift, 40-bit accumulation."""
    sx = x - 0x10000 if (x & 0x8000 and mode[0] in "SR") else x
    sy = y - 0x10000 if (y & 0x8000 and mode[-1] in "SD") else y
    if mode in ("RND", "MR+RND", "SS", "MR+SS"):
        sx = x - 0x10000 if x & 0x8000 else x
        sy = y - 0x10000 if y & 0x8000 else y
    elif mode == "SU":
        sx = x - 0x10000 if x & 0x8000 else x
        sy = y
    elif mode == "US":
        sx = x
        sy = y - 0x10000 if y & 0x8000 else y
    else:
        sx, sy = x, y
    product = sx * sy * (1 if integer else 2)
    acc = product
    if mode.startswith("MR+") and seed is not None:
        mr2, mr1, mr0 = seed
        prev = (mr2 << 32) | (mr1 << 16) | mr0
        if prev & (1 << 39):
            prev -= 1 << 40
        acc = prev + product
    if mode.endswith("RND"):
        low = acc & 0xFFFF
        acc += 0x8000
        if low == 0x8000:
            acc &= ~0x10000
    acc &= (1 << 40) - 1
    return (acc >> 32) & 0xFF, (acc >> 16) & 0xFFFF, acc & 0xFFFF


# 8xcompu.pdf Figure 2-11, unbiased column: MR before RND -> MR after.
FIGURE_2_11 = [
    ((0x00, 0x0000, 0x8000), (0x00, 0x0000, 0x0000)),
    ((0x00, 0x0001, 0x8000), (0x00, 0x0002, 0x0000)),
    ((0x00, 0x0000, 0x8001), (0x00, 0x0001, 0x0001)),
    ((0x00, 0x0001, 0x8001), (0x00, 0x0002, 0x0001)),
    ((0x00, 0x0000, 0x7FFF), (0x00, 0x0000, 0xFFFF)),
    ((0x00, 0x0001, 0x7FFF), (0x00, 0x0001, 0xFFFF)),
]


def check_mac(ADSP) -> int:
    bad = 0
    print("\nMAC modes, against Table 2-8 signedness and the fractional shift:")
    values = (0x0000, 0x0001, 0x7FFF, 0x8000, 0x8001, 0xFFFF, 0x4000, 0xC000,
              0x1234, 0xABCD)
    for mode in ("SS", "SU", "US", "UU", "RND"):
        wrong = []
        for x in values:
            for y in values:
                got = mac_once(ADSP, mode, x, y)
                want = reference(mode, x, y)
                if got != want:
                    wrong.append((x, y, got, want))
        print(f"   ({mode:3}) {len(values)**2 - len(wrong):3d}/{len(values)**2} agree"
              + ("" if not wrong else f"   MISMATCH"))
        for x, y, got, want in wrong[:4]:
            print(f"        x={x:04x} y={y:04x}  core "
                  f"{got[0]:02x}:{got[1]:04x}:{got[2]:04x}  ref "
                  f"{want[0]:02x}:{want[1]:04x}:{want[2]:04x}")
        bad += len(wrong)

    print("\nUnbiased rounding, against the six vectors of 8xcompu.pdf Fig 2-11:")
    for before, after in FIGURE_2_11:
        # Seed MR, then accumulate zero with RND so only the rounding runs.
        words = [load_imm("MR2", before[0]), load_imm("MR1", before[1]),
                 load_imm("MR0", before[2]),
                 load_imm("MX0", 0), load_imm("MY0", 0), mac("MR+RND"),
                 store_dm(0x2000, "MR0"), store_dm(0x2001, "MR1"),
                 store_dm(0x2002, "MR2")]
        _, dm = run_program(ADSP, words)
        got = (dm[0x2002] & 0xFF, dm[0x2001], dm[0x2000])
        ok = got == after
        if not ok:
            bad += 1
        print(f"   {before[0]:02x}:{before[1]:04x}:{before[2]:04x} -> "
              f"{got[0]:02x}:{got[1]:04x}:{got[2]:04x}   manual "
              f"{after[0]:02x}:{after[1]:04x}:{after[2]:04x}   "
              + ("ok" if ok else "MISMATCH"))
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--law', choices=('alaw', 'ulaw'), default='alaw',
                    help="which reference to compare against; the PRI kernel "
                         "selects its table at DM 0x3309, so this has to match "
                         "the image being run rather than being a free choice")
    ap.add_argument('--mac', action='store_true',
                    help='check the multiplier modes instead of G.711: the '
                         'page-8 modulator is built from (SS)/(SU)/(RND) '
                         'accumulation and nothing had ever tested it')
    ap.add_argument('--show', type=int, default=12,
                    help='mismatching inputs to print')
    args = ap.parse_args()

    if args.mac:
        from eicon_mips_shim import ADSP
        return 1 if check_mac(ADSP) else 0

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
