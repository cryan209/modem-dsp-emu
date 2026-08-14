#!/usr/bin/env python3
"""Bench the V.8 envelope biquad, PM 0x3F1D, against a chosen coefficient table.

`docs/analog_rxsample_correction.md` localises the Analog caller's failure to
build a CM to one filter: inside the ANSam window the raw Hilbert magnitude
stored at `PM 0x3EE4` has a median of 7,648, and the same word after this
routine, stored at `PM 0x3EF9`, has a median of 0 — against the 905 the
downstream integrator needs to cross its threshold.

`docs/analog_v8_oracle.md` swept the *other* detector's tables
(`0x3D04/0x3D16/0x3D1C/0x3D22`) and exonerated them. **Table `0x3D10`, the one
on this path, was never in that sweep**, so nothing here is retired by it.

The method deliberately does not depend on reading the coefficients correctly.
A biquad is linear and time-invariant, so its behaviour can be *measured*: drive
the real routine, in the real emulator, with an impulse and with sinusoids, and
report the impulse response and the frequency response. That distinguishes the
three cases that matter:

* a sane bandpass whose passband simply does not contain the signal — the
  firmware is right and the input is wrong;
* a filter that is dead or near-dead at every frequency — the coefficients are
  being fetched or applied wrongly;
* a response that is not LTI at all (impulse response does not predict the sine
  response) — an emulator arithmetic defect.

The routine is called the way `PM 0x3EF5..0x3EF8` calls it::

    3ef5: SR1 = DM($0776)      ; the input sample
    3ef6: I0  = $07AC          ; four words of filter state
    3ef7: I4  = $3D10          ; six PM words of coefficients
    3ef8: CALL $3F1D
    3ef9: DM($0776) = SR1      ; the output

Usage::

    ./tools/v8_envelope_filter_bench.py --impulse
    ./tools/v8_envelope_filter_bench.py --sweep --rate 148
    ./tools/v8_envelope_filter_bench.py --table 0x3D1C --sweep   # a known-good table
"""

from __future__ import annotations

import argparse
import cmath
import ctypes
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADSP = ctypes.CDLL(str(REPO / 'tools/adsp2181emu/libadsp2181.dylib'))

OVERLAY = (REPO / 'artifacts/eicon-dsp/build-109-789-analog/overlays'
           / '025f-v8.ana-overlay')

FILTER = 0x3F1D          # the routine under test
STATE = 0x07AC           # I0: four words of filter state
TABLE = 0x3D10           # I4: six PM words of coefficients
STUB = 0x0200            # scratch PM for the driver stub
IN_DM = 0x2000           # scratch DM for input
OUT_DM = 0x2001          # scratch DM for output

# Register encodings, as tools/adsp_arith_oracle.py uses them.
REG_SR1 = 0x0F
REG_AR = 0x0A


def _declare() -> None:
    ADSP.adsp2181_create.restype = ctypes.c_void_p
    for name in ('adsp2181_reset', 'adsp2181_destroy'):
        getattr(ADSP, name).argtypes = [ctypes.c_void_p]
    ADSP.adsp2181_set_pc.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
    ADSP.adsp2181_pc.argtypes = [ctypes.c_void_p]
    ADSP.adsp2181_pc.restype = ctypes.c_uint16
    ADSP.adsp2181_run.argtypes = [ctypes.c_void_p, ctypes.c_int]
    for name in ('adsp2181_pm', 'adsp2181_dm'):
        getattr(ADSP, name).argtypes = [ctypes.c_void_p]
    ADSP.adsp2181_pm.restype = ctypes.POINTER(ctypes.c_uint32)
    ADSP.adsp2181_dm.restype = ctypes.POINTER(ctypes.c_uint16)


def read_words(path: Path) -> dict[int, int]:
    words = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            addr, value = line.split()
            words[int(addr, 16)] = int(value, 16)
    return words


class Bench:
    """A bare core carrying the V8.ANA overlay, driving one routine."""

    def __init__(self, table: int = TABLE, verbose: bool = False) -> None:
        _declare()
        self.cpu = ADSP.adsp2181_create()
        ADSP.adsp2181_reset(self.cpu)
        self.pm = ADSP.adsp2181_pm(self.cpu)
        self.dm = ADSP.adsp2181_dm(self.cpu)

        pm_words = read_words(OVERLAY / 'pm.words')
        dm_words = read_words(OVERLAY / 'dm.words')
        for addr, value in pm_words.items():
            self.pm[addr] = value
        for addr, value in dm_words.items():
            self.dm[addr] = value
        if verbose:
            print(f'loaded {len(pm_words)} PM and {len(dm_words)} DM words '
                  f'from {OVERLAY.name}')
            print('coefficients at PM 0x%04X: %s' % (
                table, ' '.join(f'{self.pm[table + i] & 0xFFFFFF:06x}'
                                for i in range(6))))

        # The driver stub is the real call site, lifted verbatim from
        # PM 0x3EF5..0x3EF9, with only the address fields substituted:
        # the input comes from IN_DM instead of DM(0x0776), the output goes to
        # OUT_DM, and I4 points at whichever table is under test. Hand-encoding
        # these was how the first version of this bench came out dead across
        # every table, including ones already known good -- so the opcodes are
        # taken from the firmware rather than derived.
        def retarget(word: int, address: int) -> int:
            return (word & ~(0x3FFF << 4)) | ((address & 0x3FFF) << 4)

        site = [self.pm[a] & 0xFFFFFF for a in range(0x3EF5, 0x3EFA)]
        code = [
            retarget(site[0], IN_DM),        # SR1 = DM(IN_DM)
            retarget(site[1], STATE),        # I0  = STATE
            retarget(site[2], table),        # I4  = table
            site[3],                         # CALL 0x3F1D
            retarget(site[4], OUT_DM),       # DM(OUT_DM) = SR1
        ]
        for i, word in enumerate(code):
            self.pm[STUB + i] = word
        end = STUB + len(code)
        self.pm[end] = 0x180000 | (end << 4) | 0x0F  # JUMP self
        self.end = end
        self.table = table
        self._init_dags()

    def _init_dags(self) -> None:
        """M0=0, M1=1, M5=1, L0=0, L4=0 -- linear addressing, unit stride.

        The routine sets M3 itself. These are the values the live call site
        leaves in place; a wrong L register would make the state buffer
        circular and is exactly the kind of thing this bench must not
        accidentally introduce.
        """
        # Encoding, read off the firmware rather than guessed: the immediate is
        # bits 17:4 and bits 23:18 select the register group -- 0x34xxxx is
        # DAG1 (I0-3 = nibble 0-3, M0-3 = 4-7, L0-3 = 8-11) and 0x38xxxx is
        # DAG2 (I4-7, M4-7, L4-7 on the same nibbles). Confirmed against
        # `37fff7 M3 = -1`, `3401d8 L0 = $001D`, `380016 M6 = 1`,
        # `380408 L4 = $0040` and `38014b L7 = $0014`.
        def dag(group: int, nibble: int, value: int) -> int:
            return (0x340000 if group == 1 else 0x380000) \
                | ((value & 0x3FFF) << 4) | nibble

        setup = [
            dag(1, 0x4, 0),      # M0 = 0
            dag(1, 0x5, 1),      # M1 = 1
            dag(2, 0x5, 1),      # M5 = 1
            dag(1, 0x8, 0),      # L0 = 0  -- linear, not circular
            dag(2, 0x8, 0),      # L4 = 0
        ]
        base = 0x0280
        for i, word in enumerate(setup):
            self.pm[base + i] = word
        self.pm[base + len(setup)] = (0x180000
                                      | ((base + len(setup)) << 4) | 0x0F)
        ADSP.adsp2181_set_pc(self.cpu, base)
        ADSP.adsp2181_run(self.cpu, 32)

    def reset_state(self) -> None:
        for i in range(8):
            self.dm[STATE + i] = 0

    def push(self, value: int) -> int:
        """One sample in, one sample out. Returns a signed 16-bit result."""
        self.dm[IN_DM] = value & 0xFFFF
        self.dm[OUT_DM] = 0
        ADSP.adsp2181_set_pc(self.cpu, STUB)
        ADSP.adsp2181_run(self.cpu, 400)
        out = self.dm[OUT_DM]
        return out - 0x10000 if out & 0x8000 else out


def impulse(bench: Bench, n: int, amplitude: int) -> list[int]:
    bench.reset_state()
    return [bench.push(amplitude if i == 0 else 0) for i in range(n)]


def sine_response(bench: Bench, freq_norm: float, amplitude: int,
                  n: int = 512, settle: int = 256) -> float:
    """Peak |output| for a sinusoid at `freq_norm` cycles/sample."""
    bench.reset_state()
    peak = 0
    for i in range(n + settle):
        x = int(amplitude * math.sin(2 * math.pi * freq_norm * i))
        y = bench.push(max(-32768, min(32767, x)))
        if i >= settle:
            peak = max(peak, abs(y))
    return peak


def lti_check(bench: Bench, response: list[int], freq_norm: float,
              amplitude: int) -> tuple[float, float]:
    """Predict the sine amplitude from the impulse response and compare.

    If the emulator's arithmetic is sound this prediction holds; a large
    mismatch is the signature of a non-linear defect (saturation in the wrong
    place, a bad shift, a truncation), which no amount of reading coefficients
    would reveal.
    """
    acc = sum(value * cmath.exp(-2j * math.pi * freq_norm * k)
              for k, value in enumerate(response))
    # `response` is the response to an impulse of height `amplitude`, so |H|
    # already carries that gain and the predicted sine peak is just |H|.
    predicted = abs(acc)
    measured = sine_response(bench, freq_norm, amplitude)
    return predicted, measured


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--table', type=lambda s: int(s, 0), default=TABLE,
                    help='PM address of the six coefficient words '
                         '(default 0x3D10, the path under investigation)')
    ap.add_argument('--amplitude', type=int, default=7648,
                    help='input magnitude; the default is the measured live '
                         'median of DM(0x0776) inside the ANSam window')
    ap.add_argument('--impulse', action='store_true')
    ap.add_argument('--sweep', action='store_true')
    ap.add_argument('--rate', type=float, default=148.0,
                    help='evaluation rate in Hz, for labelling only; the '
                         'measured live rate is ~148 Hz (667 calls in 4.5 s)')
    ap.add_argument('--points', type=int, default=25)
    args = ap.parse_args()

    if not (OVERLAY / 'pm.words').is_file():
        raise SystemExit(f'{OVERLAY} not extracted; see docs/handoff.md')

    bench = Bench(args.table, verbose=True)
    print()

    response = impulse(bench, 64, args.amplitude)
    if args.impulse or not args.sweep:
        print(f'impulse response to {args.amplitude}, first 24 samples:')
        print('  ' + ' '.join(str(v) for v in response[:24]))
        energy = sum(abs(v) for v in response)
        print(f'  sum|h| = {energy}   peak = {max(map(abs, response))}   '
              f'tail|h[48:]| = {sum(abs(v) for v in response[48:])}')
        if energy == 0:
            print('  *** the filter is dead: a full-scale impulse produces '
                  'nothing at all ***')
        print()

    if args.sweep:
        print(f'frequency response, {args.points} points, '
              f'input amplitude {args.amplitude}')
        print(f'{"cyc/sample":>11} {"Hz @%.0f" % args.rate:>10} '
              f'{"measured":>9} {"from h[]":>9}  ratio')
        for i in range(1, args.points + 1):
            fn = 0.5 * i / args.points
            predicted, measured = lti_check(bench, response, fn,
                                            args.amplitude)
            ratio = (measured / predicted) if predicted else float('nan')
            print(f'{fn:11.4f} {fn * args.rate:10.2f} {measured:9d} '
                  f'{predicted:9.0f}  {ratio:5.2f}')
        print()
        print('ANSam\'s envelope modulation is 15 Hz; at %.0f Hz that is '
              '%.4f cycles/sample.' % (args.rate, 15.0 / args.rate))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
