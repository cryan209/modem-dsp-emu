#!/usr/bin/env python3
"""Audit what the card actually puts on the wire, and whether it comes back.

Session 244 showed the received upstream only carries about 20 dB and that the
card's receiver extracts all of it, which moves the question to the other
direction: what are we transmitting, and is any of it leaking into our own
receive path?  This reads the paired captures written by
`tools/eicon_adsp_sip.py` -- `<run>.ulaw` is what the card sent, `<run>.rx.ulaw`
what it received -- and reports three things.

**Level and constellation.**  V.90 downstream codepoints are exact mu-law codes,
so the transmitted alphabet is directly readable.  In the settled data era of
both run73 and run76 it is 18 equiprobable codes, uniformly spaced about 128
linear units apart, spanning only `+/-1052` of the codec's `+/-32124`:

```text
  -1052 -924 -812 -684 -556 -428 -292 -180 -56 +56 +164 +292 ... +1052
```

Eighteen codes is right for 32,000 bit/s (four bits per 8 kHz symbol, sixteen
points plus signalling).  The level is not: peak `-29.7 dBFS`, rms `-34 dBFS`,
where a normal V.90 downstream runs near `-12..-16 dBm0`, roughly `-17 dBFS`
rms.  We transmit about 17 dB quiet, and build the constellation out of the
bottom of the codepoint table instead of spreading it across the range.  On this
all-digital RTP path that still decodes -- both runs negotiated and carried
32,000 downstream -- but it is a real deviation from what a card would send.

**Gaps.**  Transmit stops completely for whole stretches: run76 has 10.0 s of
digital silence in runs of 100 ms or longer, run73 has 5.2 s.  One of those gaps
covers 12.0..14.5 s, which is the entire window in which the far end trains and
in which the upstream residual was measured.

**Echo.**  Because of that gap the echo question answers itself, and the
correlation confirms it: the best `|rho|` between transmit and receive anywhere
in the training window is 0.021, at an implausible 473 ms lag, which is the
noise floor of the estimate.  None of the receive-side error is our own signal
coming back, so the 20 dB is genuinely what arrived.

    /tmp/eicon-venv/bin/python tools/v90_tx_wire_audit.py \\
        artifacts/eicon-native-tower/run76

Needs numpy only, and borrows the mu-law codec and level helpers from
`tools/v90_rx_reference_demod.py`.
"""
from __future__ import annotations

import argparse
import collections
import math
from pathlib import Path

import numpy as np

from v90_rx_reference_demod import mulaw_decode

SAMPLE_RATE = 8000.0
FULL_SCALE = 32124.0              # the largest magnitude a mu-law code decodes to
# A V.90 downstream normally runs near -12..-16 dBm0; 0 dBm0 rms is full scale
# over root two, so that is about -17 dBFS rms.
NOMINAL_DOWNSTREAM_DBFS = -17.0


def dbfs(value: float) -> float:
    return 20.0 * math.log10(value / FULL_SCALE) if value > 0 else float('-inf')


def silence_runs(linear: np.ndarray, minimum: int):
    """Start and length of every run of exact digital zero at least `minimum`."""
    quiet = np.concatenate(([False], linear == 0.0, [False]))
    edges = np.flatnonzero(np.diff(quiet.astype(np.int8)))
    spans = [(a, b - a) for a, b in zip(edges[::2], edges[1::2])]
    return [span for span in spans if span[1] >= minimum]


def report_eras(linear: np.ndarray, step: float, limit: float) -> None:
    print(f'{"time":>7} {"rms":>8} {"peak":>8} {"dBFS":>8}  codes')
    window = int(step * SAMPLE_RATE)
    for start in range(0, min(len(linear), int(limit * SAMPLE_RATE)), window):
        block = linear[start:start + window]
        peak = float(np.max(np.abs(block))) if len(block) else 0.0
        print(f'{start / SAMPLE_RATE:7.1f} {block.std():8.0f} {peak:8.0f} '
              f'{dbfs(peak):8.1f}  {len(np.unique(block)):4d}')


def report_constellation(octets: bytes, lo: int, hi: int) -> None:
    census = collections.Counter(octets[lo:hi])
    total = hi - lo
    print(f'{len(census)} distinct codes over {lo / SAMPLE_RATE:.0f}'
          f'..{hi / SAMPLE_RATE:.0f} s')
    print(f'{"code":>6} {"linear":>8} {"share":>7}')
    levels = []
    for code, count in sorted(census.items(),
                              key=lambda kv: mulaw_decode(bytes([kv[0]]))[0]):
        level = float(mulaw_decode(bytes([code]))[0])
        levels.append(level)
        print(f'  0x{code:02x} {level:8.0f} {100.0 * count / total:6.2f}%')
    if len(levels) > 1:
        spacing = np.diff(levels)
        print(f'spacing: min {spacing.min():.0f}, max {spacing.max():.0f}, '
              f'median {np.median(spacing):.0f}')
    print(f'span {levels[0]:.0f}..{levels[-1]:.0f} of +/-{FULL_SCALE:.0f} '
          f'= {dbfs(max(abs(levels[0]), abs(levels[-1]))):.1f} dBFS peak')


def report_echo(tx: np.ndarray, rx: np.ndarray, lo: int, hi: int,
                search: int) -> None:
    received = rx[lo:hi]
    received = received - received.mean()
    denominator = len(received) * received.std()
    if denominator == 0:
        print('receive window is silent; no echo estimate')
        return
    best = []
    for lag in range(-search, search + 1):
        a, b = lo + lag, hi + lag
        if a < 0 or b > len(tx):
            continue
        sent = tx[a:b]
        if sent.std() == 0:
            continue
        sent = sent - sent.mean()
        best.append((abs(float(np.dot(received, sent)))
                     / (denominator * (sent.std() or 1.0)), lag,
                     float(np.count_nonzero(tx[a:b])) / (b - a)))
    if not best:
        print(f'transmit is silent across the whole +/-{search / 8:.0f} ms '
              'search: no echo is possible in this window')
        return
    best.sort(reverse=True)
    # The live fraction matters as much as rho: a lag whose transmit window is
    # mostly digital silence cannot show an echo whatever the correlation says,
    # so a high rho there is an artefact of a tiny sample, not evidence.
    print('best transmit-to-receive correlations:')
    for rho, lag, live in best[:5]:
        print(f'  rho={rho:.4f} at {lag:+5d} samples ({lag / 8.0:+7.1f} ms), '
              f'transmit window {100.0 * live:5.1f}% non-silent')


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('run', type=Path,
                    help='capture prefix, e.g. artifacts/.../run76')
    ap.add_argument('--from', dest='start', type=float, default=30.0,
                    help='constellation census window start')
    ap.add_argument('--to', dest='end', type=float, default=40.0)
    ap.add_argument('--echo-from', type=float, default=12.7)
    ap.add_argument('--echo-to', type=float, default=14.6)
    ap.add_argument('--echo-search', type=int, default=4000,
                    help='lag search half-width in samples')
    ap.add_argument('--eras', type=float, default=0.5,
                    help='seconds per level line; 0 skips the timeline')
    ap.add_argument('--eras-limit', type=float, default=40.0)
    args = ap.parse_args()

    sent = Path(str(args.run) + '.ulaw')
    received = Path(str(args.run) + '.rx.ulaw')
    octets = sent.read_bytes()
    tx = mulaw_decode(octets)
    rx = mulaw_decode(received.read_bytes())
    print(f'[tx-audit] {sent.name} {len(tx) / SAMPLE_RATE:.1f}s, '
          f'{received.name} {len(rx) / SAMPLE_RATE:.1f}s')

    gaps = silence_runs(tx, int(0.1 * SAMPLE_RATE))
    total = sum(length for _, length in gaps) / SAMPLE_RATE
    print(f'[tx-audit] {len(gaps)} transmit gaps of 100 ms or more, '
          f'{total:.2f}s total')
    for start, length in gaps[:12]:
        print(f'    {start / SAMPLE_RATE:7.2f}..'
              f'{(start + length) / SAMPLE_RATE:7.2f} s '
              f'({length / SAMPLE_RATE:.2f}s)')

    lo, hi = int(args.start * SAMPLE_RATE), int(args.end * SAMPLE_RATE)
    hi = min(hi, len(octets))
    block = tx[lo:hi]
    print(f'[tx-audit] data era rms {block.std():.0f} '
          f'({dbfs(block.std()):.1f} dBFS), nominal downstream is about '
          f'{NOMINAL_DOWNSTREAM_DBFS:.0f} dBFS rms -- '
          f'{NOMINAL_DOWNSTREAM_DBFS - dbfs(block.std()):+.0f} dB')
    report_constellation(octets, lo, hi)

    print()
    report_echo(tx, rx, int(args.echo_from * SAMPLE_RATE),
                int(args.echo_to * SAMPLE_RATE), args.echo_search)

    if args.eras:
        print()
        report_eras(tx, args.eras, args.eras_limit)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
