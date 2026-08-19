#!/usr/bin/env python3
"""Count the words page 14 publishes per serializer pass, before changing anything.

Session 266 localised the emulated V.90D transmit defect to one line of
`dial_tikrnl_drive.frame_fast`: on page 14 it *polls* DM(0x3FB4) once per line
sample instead of dereferencing it, and 83% of what it publishes is zero.  Two
readings of that fit the evidence and they differ in exactly one observable:

  (a) the sample really does live *in* DM(0x3FB4) -- then DM(0x3764..) is never
      written while page 14 transmits, and the one-in-six is a producer defect
      further upstream;
  (b) DM(0x3764) is a live *block* pointer and the page publishes a block of
      samples per serializer pass -- then DM(0x3764..) is written N words per
      pass, the host takes one per 8 kHz tick, and the emitted signal is the
      block decimated by N.

`adsp2181_dm_census()` counts writes per DM address, so this asks it directly:
replay the real card's own receive capture into the direct (non-native) card,
which is the backend that owns the defective line, and sample the census across
a window at 0x3764 every page-14 frame.  Reports writes per frame per address,
split by TrnProgress, plus the per-frame distribution -- which is what "six
samples per pass" would have to look like.

Measurement only.  Nothing is patched and no behaviour changes.

    make -C tools/adsp2181emu
    /tmp/eicon-venv/bin/python tools/v90d_tx_block_census.py \
        artifacts/eicon-native-tower/run65.rx.ulaw --to 30
"""
from __future__ import annotations

import argparse
import collections
import ctypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dial_tikrnl_drive as DRIVE
from dial_tikrnl_drive import ADSP, Card

KERNEL = Path('artifacts/eicon-dsp/build-117-926/kernel/'
              '0009-diva-server-pri-30m-kernel')
TIKRNL = Path('artifacts/eicon-dsp/build-117-926/tikrnl/'
              '0258-tikrnl81.f34-task')

# dial_tikrnl_drive declares argtypes only for the entry points it uses, and a
# ctypes call with no argtypes truncates the 64-bit core handle to an int --
# which segfaults on the first census call rather than returning a wrong count.
ADSP.adsp2181_dm_census.argtypes = [ctypes.c_void_p, ctypes.c_int]
ADSP.adsp2181_dm_census_clear.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_dm_census_count.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
ADSP.adsp2181_dm_census_count.restype = ctypes.c_uint64

SAMPLE_RATE = 8000
PAGE14 = DRIVE.V90D_ID
DM_TRN = DRIVE.DM_TRNPROGRESS
DM_TXPTR = DRIVE.DM_TX_POINTER
DM_SERIAL = 0x20DE
BLOCK = 0x3764


def signed(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('capture', type=Path, help='a .rx.ulaw capture (PCMU)')
    ap.add_argument('--to', dest='end', type=float, default=30.0)
    ap.add_argument('--window', type=int, default=16,
                    help='words from 0x3764 to census (default 16)')
    ap.add_argument('--also', type=lambda t: int(t, 0), action='append',
                    default=[], help='extra DM address to census (repeatable)')
    ap.add_argument('--state', type=lambda t: int(t, 0), default=None,
                    help='report only this TrnProgress state')
    ap.add_argument('--backend', choices=('direct', 'native'), default='direct',
                    help='direct = dial_tikrnl_drive.Card (the backend the '
                         'defective line lives in); native = the MIPS tower')
    ap.add_argument('--block', type=lambda t: int(t, 0), action='append',
                    default=[], help='extra block base to census six words of')
    ap.add_argument('--values', action='store_true',
                    help='also record the distinct values written into the '
                         'block, sampled at the frame boundary')
    args = ap.parse_args()

    data = args.capture.read_bytes()
    if args.backend == 'native':
        from eicon_mips_shim import create_native_mips_modem
        card = create_native_mips_modem(KERNEL, TIKRNL, 'pcmu',
                                        force_info_after_v8=True,
                                        tx_prbs=True,
                                        native_bearer_activation=True)
    else:
        card = Card(force_info_after_v8=True)
        card.boot()
        card.configure_modem('answer', 'pcmu')
    for base in args.block:
        args.also.extend(range(base, base + 6))
    watched = list(range(BLOCK, BLOCK + args.window)) + [DM_TXPTR] + args.also
    dm = card.dm
    ADSP.adsp2181_dm_census_clear(card.cpu)
    ADSP.adsp2181_dm_census(card.cpu, 1)
    print(f'[census] direct card ready; watching '
          f'DM({BLOCK:#06x}..{BLOCK + args.window - 1:#06x}) + '
          f'{[f"{a:#06x}" for a in [DM_TXPTR] + args.also]}', flush=True)

    previous = {a: 0 for a in watched}
    frames = collections.Counter()
    writes = collections.defaultdict(collections.Counter)
    per_frame = collections.defaultdict(collections.Counter)
    values = collections.defaultdict(collections.Counter)
    first_state = {}
    block_frames = 0
    published_nonzero = 0
    total_frames = 0

    for index, code in enumerate(data):
        seconds = index / SAMPLE_RATE
        if seconds > args.end:
            break
        sample = card.frame_fast(code, index)
        now = {a: ADSP.adsp2181_dm_census_count(card.cpu, a) for a in watched}
        delta = {a: now[a] - previous[a] for a in watched}
        previous = now
        if card.resident != PAGE14:
            continue
        state = dm[DM_TRN]
        first_state.setdefault(state, seconds)
        if args.state is not None and state != args.state:
            continue
        total_frames += 1
        frames[state] += 1
        if sample:
            published_nonzero += 1
        for addr, n in delta.items():
            if n:
                writes[state][addr] += n
                per_frame[addr][n] += 1
        if any(delta[a] for a in range(BLOCK, BLOCK + args.window)):
            block_frames += 1
        if args.values:
            for addr in range(BLOCK, BLOCK + args.window):
                if delta[addr]:
                    values[addr][dm[addr]] += 1

    print(f'\n[census] page-14 frames: {total_frames}')
    print('[census] TrnProgress reached: ' +
          ', '.join(f'{s:04x}@{t:.3f}' for s, t in sorted(first_state.items())))
    if not total_frames:
        return 0
    print(f'[census] frames publishing a nonzero line sample: '
          f'{published_nonzero}/{total_frames} '
          f'({100.0 * published_nonzero / total_frames:.1f}%)')
    print(f'[census] frames with any write into the block: '
          f'{block_frames}/{total_frames} '
          f'({100.0 * block_frames / total_frames:.1f}%)')
    for state in sorted(frames):
        n = frames[state]
        live = {a: c for a, c in writes[state].items() if c}
        if not live:
            print(f'[census] state {state:04x}: {n} frames, no writes at all')
            continue
        print(f'[census] state {state:04x}: {n} frames')
        for addr in sorted(live):
            print(f'    DM {addr:04x}: {live[addr]:9d} writes '
                  f'= {live[addr] / n:7.3f}/frame  last={dm[addr]:04x}'
                  f'({signed(dm[addr])})')
    print('[census] writes per frame, distribution:')
    for addr in sorted(per_frame):
        dist = ' '.join(f'{k}x{v}' for k, v in sorted(per_frame[addr].items()))
        print(f'    DM {addr:04x}: {dist}')
    if args.values:
        print('[census] values seen at the frame boundary:')
        for addr in sorted(values):
            top = values[addr].most_common(6)
            print(f'    DM {addr:04x}: ' +
                  ' '.join(f'{v:04x}={signed(v)}x{n}' for v, n in top))
    print(f'[census] DM({DM_TXPTR:04x})={dm[DM_TXPTR]:04x} '
          f'DM({DM_SERIAL:04x})={dm[DM_SERIAL]:04x}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
