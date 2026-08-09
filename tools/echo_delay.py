#!/usr/bin/env python3
"""Measure the real echo delay of the live path by cross-correlating TX and RX.

The capture writes both directions at 8 kHz: <prefix>.ulaw is what the card
sent and <prefix>.rx.ulaw is what came back.  Whatever of our own TX appears in
RX is the echo the canceller has to remove, and its lag is what the bulk delay
lengths should be set to. This local echo peak is distinct from the modem's
round-trip training interval: INFO measures that interval at 2400 Hz in
DM(0x3fc9), publishes it as RTDelay in DM(0x3f87), and scales it to 8 kHz
sample units in DM(0x3fcb).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np




def ulaw_decode(data: bytes) -> np.ndarray:
    u = ~np.frombuffer(data, dtype=np.uint8).astype(np.int32) & 0xFF
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    magnitude = ((mantissa << 1) + 33) << exponent
    magnitude -= 33
    return np.where(sign, -magnitude, magnitude).astype(np.float64)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('prefix', type=Path)
    ap.add_argument('--from', dest='start', type=float, default=8.0,
                    help='seconds; skip handshake, analyse steady state')
    ap.add_argument('--window', type=float, default=8.0)
    ap.add_argument('--max-lag-ms', type=float, default=400.0)
    args = ap.parse_args()

    tx = ulaw_decode(Path(f'{args.prefix}.ulaw').read_bytes())
    rx = ulaw_decode(Path(f'{args.prefix}.rx.ulaw').read_bytes())

    a = int(args.start * 8000)
    n = int(args.window * 8000)
    max_lag = int(args.max_lag_ms * 8)
    if len(tx) < a + n + max_lag or len(rx) < a + n + max_lag:
        print(f'capture too short: tx={len(tx)/8000:.1f}s rx={len(rx)/8000:.1f}s')
        return 1

    # Correlate RX against TX over positive lags only: echo arrives after the
    # transmission that caused it.
    ref = tx[a:a + n]
    ref = ref - ref.mean()
    scores = np.empty(max_lag)
    for lag in range(max_lag):
        seg = rx[a + lag:a + lag + n]
        seg = seg - seg.mean()
        denominator = np.sqrt((ref * ref).sum() * (seg * seg).sum())
        scores[lag] = (ref * seg).sum() / denominator if denominator else 0.0

    order = np.argsort(-np.abs(scores))[:6]
    print(f'{args.prefix.name}: analysed {args.window:.0f} s from '
          f'{args.start:.0f} s, lags 0..{args.max_lag_ms:.0f} ms')
    print('  strongest correlations (lag -> |r|):')
    for lag in sorted(order):
        print(f'    {lag:>5} pairs = {lag / 8:>7.2f} ms   r={scores[lag]:+.4f}')
    best = int(np.argmax(np.abs(scores)))
    print(f'  peak: {best} pairs = {best / 8:.2f} ms, r={scores[best]:+.4f}')
    print(f'  median |r| = {np.median(np.abs(scores)):.4f} '
          f'(noise floor; a real echo should stand well clear)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
