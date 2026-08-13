#!/usr/bin/env python3
"""Report dominant narrowband tones in an 8 kHz G.711 modem capture."""
from __future__ import annotations

import argparse
import math
from pathlib import Path


def ulaw(code: int) -> int:
    value = (~code) & 0xFF
    magnitude = ((((value & 0x0F) << 3) + 0x84) << ((value >> 4) & 7)) - 0x84
    return -magnitude if value & 0x80 else magnitude


def goertzel(samples: list[int], frequency: float, rate: int = 8000) -> float:
    omega = 2 * math.pi * frequency / rate
    coefficient = 2 * math.cos(omega)
    s0 = s1 = s2 = 0.0
    for sample in samples:
        s0 = sample + coefficient * s1 - s2
        s2, s1 = s1, s0
    return s1 * s1 + s2 * s2 - coefficient * s1 * s2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('capture', type=Path)
    ap.add_argument('--from', dest='start', type=float, default=0.0)
    ap.add_argument('--to', type=float, default=None)
    ap.add_argument('--window-ms', type=int, default=100)
    ap.add_argument('--step-hz', type=int, default=10)
    args = ap.parse_args()
    raw = args.capture.read_bytes()
    samples = [ulaw(code) for code in raw]
    start = max(0, int(args.start * 8000))
    end = len(samples) if args.to is None else min(len(samples), int(args.to * 8000))
    width = args.window_ms * 8
    print('time_s,rms,peak_hz,tone_db_over_mean')
    for pos in range(start, end - width + 1, width):
        block = samples[pos:pos + width]
        mean = sum(block) / len(block)
        rms = math.sqrt(sum((value - mean) ** 2 for value in block) / len(block))
        if rms == 0:
            print(f'{pos / 8000:.3f},0.0,0,0.0')
            continue
        powers = [(goertzel(block, frequency), frequency)
                  for frequency in range(200, 3801, args.step_hz)]
        power, frequency = max(powers)
        average = sum(item[0] for item in powers) / len(powers)
        prominence = 10 * math.log10(max(power, 1e-30) / max(average, 1e-30))
        print(f'{pos / 8000:.3f},{rms:.1f},{frequency:.0f},{prominence:.1f}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
