#!/usr/bin/env python3
"""Deterministic paired FIR-only replay, not modem or live timing qualification.

Timers surround whole batches, never individual samples/taps. The reference
push is frozen from f6a1e0d; both implementations use the same filter design.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import struct
import time

from analog_kernel_dispatch import RationalResampler


class ReferenceResampler(RationalResampler):
    """Pre-optimization scalar implementation, including original bounds tests."""
    def push(self, sample: float) -> list[float]:
        self.history.append(float(sample))
        available = self.consumed + len(self.history) - 1
        out: list[float] = []
        while True:
            position = self.produced * self.down + self.phase_offset
            base, phase = divmod(position, self.up)
            if base > available:
                break
            total = 0.0
            for k in range(self.taps):
                tap = phase + k * self.up
                if tap >= len(self.h):
                    break
                index = base - k - self.consumed
                if 0 <= index < len(self.history):
                    total += self.h[tap] * self.history[index]
            out.append(total)
            self.produced += 1
        keep = self.taps + 2
        oldest_needed = (self.produced * self.down) // self.up - self.taps
        drop = max(0, oldest_needed - self.consumed - keep)
        if drop > 0:
            del self.history[:drop]
            self.consumed += drop
        return out


def stimulus(count):
    return [float((((i * 997) ^ (i >> 3)) & 0xffff) - 32768) for i in range(count)]


def batch(kind, inputs):
    incoming, outgoing = kind(6, 5), kind(5, 6)
    output = []
    wall, cpu = time.monotonic_ns(), time.thread_time_ns()
    for sample in inputs:
        for codec_sample in incoming.push(sample):
            output.extend(outgoing.push(codec_sample))
    cpu_ns, wall_ns = time.thread_time_ns() - cpu, time.monotonic_ns() - wall
    # Hash outside the measured span; do not round, clamp or tolerate epsilon.
    digest = hashlib.sha256(b''.join(struct.pack('>d', v) for v in output)).hexdigest()
    return dict(wall_ns=wall_ns, cpu_ns=cpu_ns, outputs=len(output), sha256=digest,
                cpu_ms_per_160=cpu_ns / len(inputs) * 160 / 1e6,
                state=[dict(produced=r.produced, consumed=r.consumed,
                            history_sha256=hashlib.sha256(b''.join(struct.pack('>d', v) for v in r.history)).hexdigest())
                       for r in (incoming, outgoing)])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--samples', type=int, default=32000)
    ap.add_argument('--pairs', type=int, default=4)
    args = ap.parse_args()
    if args.output.exists() or not 160 <= args.samples <= 1000000 or not 1 <= args.pairs <= 20:
        ap.error('output must be new; samples 160..1000000; pairs 1..20')
    inputs = stimulus(args.samples)
    rows = []
    for pair in range(args.pairs):
        order = ('reference', 'optimized') if pair % 2 == 0 else ('optimized', 'reference')
        results = {name: batch(ReferenceResampler if name == 'reference' else RationalResampler, inputs)
                   for name in order}
        a, b = results['reference'], results['optimized']
        rows.append(dict(pair=pair, order=list(order), results=results,
                         bit_exact=all(a[k] == b[k] for k in ('outputs', 'sha256', 'state')),
                         cpu_ratio_reference_over_optimized=a['cpu_ns'] / b['cpu_ns']))
    result = dict(scope='FIR-only synthetic replay; no DSP/audio/pacing qualification',
                  reference_commit='f6a1e0d', samples=args.samples, pairs=args.pairs,
                  python_version=platform.python_version(), observations=rows,
                  source_sha256={name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                 for name in ('benchmark_resampler.py', 'analog_kernel_dispatch.py')},
                  bit_exact=all(row['bit_exact'] for row in rows))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    from v34_payload import write_json
    write_json(args.output, result)
    print(json.dumps(result, indent=2))
    return 0 if result['bit_exact'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
