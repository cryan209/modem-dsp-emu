#!/usr/bin/env python3
"""Watch the V.90A caller's transmit producer against a 2400 Hz Goertzel,
gated to the outer state the machine was in when each sample left.

`docs/analog_rxsample_correction.md` establishes the V.90A blocker by measuring
the *wire*: a real analogue client (`run48.rx.ulaw`) transmits a sustained clean
2400 Hz tone (dominant in 46% of its active frames) that the digital side keys
on to leave `0x00b0`, and our caller emits a broadband, noise-like signal
instead (2400 Hz dominant in only 6%, spectral flatness ~0.17 vs the gold's
~0.001).  That measurement is over the whole call; the caller walks a dozen
outer states before it parks at `0x0092`, so "6% overall" does not say *which*
state produces the tone and which produces broadband.

This gates the Goertzel by state.  It reads the `[v90a] ... state=XXXX` trace
lines from the caller endpoint log to build a (sample -> outer state) timeline,
reads the caller's transmit (`answerer.rx.ulaw`, i.e. what the answerer
receives), and reports, per state, the fraction of active transmit frames where
2400 Hz dominates and the mean spectral flatness.  A state that transmits the
tone shows high 2400 Hz dominance and low flatness; a state running the data
modulator shows the opposite.

    tools/v90a_tx_tone_probe.py <capture-dir> [--tone 2400] [--frame-ms 40]

The transmit sample the wire carries is what the serializer at `PM 0x1a1e`
produced (`DM(0x3FB4)` on page 14; the caller dereferences it), so the wire is a
faithful proxy for the producer's output -- G.711 mu-law preserves a tone.  To
watch the producer *inside* the DSP instead, write-watch the sample word the
serializer stores and feed that stream here with `--samples`.
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path


def ulaw_to_linear(data: bytes) -> list[int]:
    out = []
    for code in data:
        value = (~code) & 0xFF
        sample = (((value & 0x0F) << 3) + 0x84) << ((value & 0x70) >> 4)
        sample -= 0x84
        out.append(-sample if value & 0x80 else sample)
    return out


def state_timeline(log: Path) -> list[tuple[int, int]]:
    """[(sample, outer_state)] transitions from the [v90a] trace, in order."""
    pat = re.compile(r'\[v90a\] sample (\d+) .*? state=([0-9a-f]+)')
    rows = []
    for line in log.read_text(errors='replace').splitlines():
        m = pat.search(line)
        if m:
            rows.append((int(m.group(1)), int(m.group(2), 16)))
    return rows


def state_at(timeline: list[tuple[int, int]], sample: int) -> int | None:
    """The outer state in force at `sample` (last transition at or before it)."""
    state = None
    for at, st in timeline:
        if at > sample:
            break
        state = st
    return state


def goertzel_power(frame: list[int], tone: float, fs: int) -> float:
    k = 2.0 * math.cos(2.0 * math.pi * tone / fs)
    s0 = s1 = s2 = 0.0
    for x in frame:
        s0 = x + k * s1 - s2
        s2, s1 = s1, s0
    # The closed form s1^2+s2^2-k*s1*s2 dips slightly negative at bin edges
    # from rounding; clamp so it stays a power and the log in the flatness
    # calculation is always defined.
    return max(0.0, s1 * s1 + s2 * s2 - k * s1 * s2)


def band_powers(frame: list[int], fs: int, bins: int = 16) -> list[float]:
    return [goertzel_power(frame, (i + 0.5) * (fs / 2) / bins, fs)
            for i in range(bins)]


def frame_metrics(frame: list[int], tone: float, fs: int) -> tuple[bool, float]:
    """(is the tone dominant, spectral flatness 0..1) for one frame.

    Flatness is the geometric/arithmetic mean ratio of the band powers,
    normalised so it is bounded in [0, 1]: 1 is a flat (broadband) spectrum,
    near 0 is a single tone.
    """
    powers = band_powers(frame, fs)
    total = sum(powers)
    if total <= 0:
        return False, 1.0
    norm = [p / total for p in powers]
    geo = math.exp(sum(math.log(p + 1e-12) for p in norm) / len(norm))
    arith = sum(norm) / len(norm)
    flat = min(1.0, geo / arith)
    tone_dominant = goertzel_power(frame, tone, fs) > 0.5 * total
    return tone_dominant, flat


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('capture_dir', type=Path,
                    help='loopback --capture-dir with caller.endpoint.log and '
                         'answerer.rx.ulaw')
    ap.add_argument('--tx', type=Path, default=None,
                    help='transmit u-law to analyse (default '
                         '<dir>/answerer.rx.ulaw -- what the answerer received)')
    ap.add_argument('--log', type=Path, default=None,
                    help='caller endpoint log (default '
                         '<dir>/caller.endpoint.log)')
    ap.add_argument('--reference', type=Path, default=None,
                    help='gold analogue-client transmit for a side-by-side row, '
                         'e.g. artifacts/eicon-native-tower/run48.rx.ulaw')
    ap.add_argument('--tone', type=float, default=2400.0)
    ap.add_argument('--fs', type=int, default=8000)
    ap.add_argument('--frame-ms', type=float, default=40.0)
    ap.add_argument('--active-rms', type=float, default=200.0,
                    help='frames quieter than this RMS are idle, not counted')
    args = ap.parse_args()

    log = args.log or args.capture_dir / 'caller.endpoint.log'
    tx = args.tx or args.capture_dir / 'answerer.rx.ulaw'
    if not log.exists() or not tx.exists():
        print(f'need {log} and {tx}', file=sys.stderr)
        return 1

    timeline = state_timeline(log)
    samples = ulaw_to_linear(tx.read_bytes())
    fl = int(args.frame_ms * args.fs / 1000)
    print(f'{tx.name}: {len(samples)/args.fs:.1f}s, tone {args.tone:.0f}Hz, '
          f'frame {args.frame_ms:.0f}ms, {len(timeline)} state transitions')

    # per-state accumulators: [active_frames, tone_dominant, flatness_sum]
    per_state: dict[int, list[float]] = {}
    for start in range(0, len(samples) - fl, fl):
        frame = samples[start:start + fl]
        rms = math.sqrt(sum(x * x for x in frame) / len(frame))
        if rms < args.active_rms:
            continue
        st = state_at(timeline, start)
        if st is None:
            continue
        dominant, flat = frame_metrics(frame, args.tone, args.fs)
        acc = per_state.setdefault(st, [0.0, 0.0, 0.0])
        acc[0] += 1
        acc[1] += 1 if dominant else 0
        acc[2] += flat

    print(f'\n{"state":>7}  {"frames":>7}  {"2400Hz-dom":>10}  {"flatness":>8}')
    for st in sorted(per_state):
        n, dom, fsum = per_state[st]
        print(f'  0x{st:04x}  {int(n):7d}  {100*dom/n:9.0f}%  {fsum/n:8.3f}')

    if args.reference and args.reference.exists():
        ref = ulaw_to_linear(args.reference.read_bytes())
        n = dom = 0
        fsum = 0.0
        for start in range(0, len(ref) - fl, fl):
            frame = ref[start:start + fl]
            if math.sqrt(sum(x * x for x in frame) / len(frame)) < args.active_rms:
                continue
            dominant, flat = frame_metrics(frame, args.tone, args.fs)
            n += 1
            dom += 1 if dominant else 0
            fsum += flat
        print(f'\n  {args.reference.name} (gold): {int(n)} frames, '
              f'2400Hz-dom {100*dom/max(n,1):.0f}%, flatness {fsum/max(n,1):.3f}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
