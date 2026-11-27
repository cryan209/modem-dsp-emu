#!/usr/bin/env python3
"""Alternating full Analog replay; whole-batch timers, not live pacing proof."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import struct
import time

ROOT = Path(__file__).resolve().parents[1]


def stimulus(count):
    return [(0, 0x7fff, 0x8000, 0xffff)[i] if i < 4 else ((i * 997) ^ (i >> 3)) & 0xffff
            for i in range(count)]


def digest(values, format):
    return hashlib.sha256(b''.join(struct.pack(format, value) for value in values)).hexdigest()


def snapshot(modem, lib):
    from producer_detail import native_reader
    return dict(dm_sha256=digest((modem.dm[a] for a in range(0x4000)), '>H'),
                pm_sha256=digest((modem.pm[a] for a in range(0x4000)), '>I'),
                cycles=native_reader(lib, modem)(), frames=modem.driver.frames,
                tx_written=modem.driver.tx_written, tx_callbacks=modem.driver.sport_callback_writes,
                tx_latch=modem.driver.last_sport1_tx, frame_result=modem.driver.last_frame_result,
                last_out=modem._last_out, pending_sha256=digest(modem._bearer_out, '>d'),
                converters=[None if r is None else dict(consumed=r.consumed, produced=r.produced,
                            history_sha256=digest(r.history, '>d'))
                            for r in (modem._to_codec, modem._to_bearer)])


def batch(explicit, inputs, rate, sport_tx):
    import dial_tikrnl_drive as drive
    drive.select_firmware_set('analog109')
    import analog_kernel_dispatch as akd
    previous = akd.ANALOG_USE_SPORT_TX
    akd.ANALOG_USE_SPORT_TX = sport_tx
    modem = akd.AnalogKernelModem(codec_rate=rate, record_sport_history=False,
                                 explicit_sport_rx=explicit)
    try:
        modem.boot()
        modem.configure_modem('calling', 'pcmu')
        # Equal warmup, outside timing. No GC-policy changes or per-frame clocks.
        for i, word in enumerate(stimulus(1024)):
            modem.frame_fast(word, i)
        outputs = []
        wall, cpu = time.monotonic_ns(), time.thread_time_ns()
        for i, word in enumerate(inputs, 1024):
            outputs.append(modem.frame_fast(word, i))
        cpu_ns, wall_ns = time.thread_time_ns() - cpu, time.monotonic_ns() - wall
        return dict(cpu_ns=cpu_ns, wall_ns=wall_ns, outputs=len(outputs),
                    output_sha256=digest(outputs, '>i'), state=snapshot(modem, akd.ADSP),
                    cpu_ms_per_160=cpu_ns / len(inputs) * 160 / 1e6)
    finally:
        akd.ANALOG_USE_SPORT_TX = previous
        akd.ADSP.adsp2181_destroy.argtypes = [ctypes.c_void_p]
        akd.ADSP.adsp2181_destroy.restype = None
        akd.ADSP.adsp2181_destroy(modem.card.cpu)


def equivalent(a, b):
    return all(a[k] == b[k] for k in ('outputs', 'output_sha256', 'state'))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--samples', type=int, default=32000)
    ap.add_argument('--pairs', type=int, default=4)
    ap.add_argument('--codec-rate', type=int, choices=(8000, 9600), default=9600)
    ap.add_argument('--sport-tx', action='store_true')
    args = ap.parse_args()
    if args.output.exists() or not 160 <= args.samples <= 1000000 or not 1 <= args.pairs <= 20:
        ap.error('output must be new; samples 160..1000000; pairs 1..20')
    removed = sorted(k for k in os.environ if k.startswith(('EICON_', 'ADSP_')))
    for k in removed:
        del os.environ[k]
    os.environ['EICON_MODULATION'] = 'v34'
    inputs = stimulus(args.samples)
    rows = []
    for pair in range(args.pairs):
        order = ('callback', 'explicit') if pair % 2 == 0 else ('explicit', 'callback')
        results = {name: batch(name == 'explicit', inputs, args.codec_rate, args.sport_tx) for name in order}
        rows.append(dict(pair=pair, order=list(order), results=results,
                         bit_exact=equivalent(results['callback'], results['explicit']),
                         cpu_ratio_callback_over_explicit=results['callback']['cpu_ns'] / results['explicit']['cpu_ns']))
    paths = ['tools/benchmark_sport_rx.py', 'tools/analog_kernel_dispatch.py', 'tools/dial_tikrnl_drive.py',
             'tools/adsp2181emu/adsp2181_core.c', 'tools/adsp2181emu/adsp2181_core.h',
             'tools/adsp2181emu/libadsp2181.dylib']
    paths += [str(p.relative_to(ROOT)) for p in sorted((ROOT / 'artifacts/eicon-dsp/build-109-789-analog').rglob('*'))
              if p.is_file()]
    result = dict(scope='offline Analog replay, not trained duplex, native-only CPU or live pacing qualification',
                  samples=args.samples, pairs=args.pairs, codec_rate=args.codec_rate, sport_tx=args.sport_tx,
                  python_version=platform.python_version(), platform=platform.platform(),
                  removed_environment_keys=removed, observations=rows,
                  input_sha256=digest(inputs, '>H'),
                  source_sha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths},
                  bit_exact=all(r['bit_exact'] for r in rows))
    from v34_payload import write_json
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, result)
    print(json.dumps(result, indent=2))
    return 0 if result['bit_exact'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
