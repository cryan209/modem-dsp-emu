#!/usr/bin/env python3
"""Replay a capture to a given moment and search DM for a sequence of words.

The backward walk of Session 193 needs an address to start from. When the thing
you are chasing is a message that went out on the wire, its assembled words are
somewhere in DM at the moment it was sent, and the words are distinctive enough
to find: search for them, then put a write watch on whatever address they turn
up at.

Both the coverage counter and the watch gate default to on in the core, and are
pushed down here for the same reason as everywhere else (Session 192).

    dm_find.py CAPTURE.rx.ulaw --at 5.35 --words 0000,8068,0100,0740,0010
    dm_find.py CAPTURE.rx.ulaw --at 5.35 --words 8068,0100 --mask-first
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eicon_mips_shim import create_native_mips_modem, ADSP  # noqa: E402

BUILD = Path('artifacts/eicon-dsp/build-117-926')
KERNEL = BUILD / 'kernel' / '0009-diva-server-pri-30m-kernel'
TIKRNL = BUILD / 'tikrnl' / '0258-tikrnl81.f34-task'
SAMPLE_RATE = 8000
DM_SIZE = 0x4000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('capture', type=Path)
    ap.add_argument('--at', type=float, required=True,
                    help='seconds into the capture to stop and search')
    ap.add_argument('--words', required=True,
                    help='comma-separated 16-bit words, hex, in order')
    ap.add_argument('--skip-zero-prefix', action='store_true',
                    help='ignore leading all-zero words when matching, which '
                         'otherwise hit everywhere in cleared memory')
    args = ap.parse_args()

    want = [int(w, 16) & 0xFFFF for w in args.words.split(',')]
    offset = 0
    if args.skip_zero_prefix:
        while want and want[0] == 0:
            want.pop(0)
            offset += 1
    if not want:
        raise SystemExit('nothing left to search for')

    data = args.capture.read_bytes()
    card = create_native_mips_modem(KERNEL, TIKRNL, 'pcmu',
                                    force_info_after_v8=True, tx_prbs=True,
                                    native_bearer_activation=True)
    ADSP.adsp2181_watch_gate(card.cpu, 0)
    ADSP.adsp2181_coverage_gate(card.cpu, 0)
    for index, code in enumerate(data):
        if index / SAMPLE_RATE > args.at:
            break
        card.frame_fast(code, index)

    dm = card.dm
    hits = []
    for base in range(DM_SIZE - len(want)):
        if all(dm[base + i] == want[i] for i in range(len(want))):
            hits.append(base - offset)
    print(f'\n=== {args.capture.name} at {args.at:.3f}s, resident '
          f'0x{card.resident:04x} ===')
    print(f'searching for {len(want)} word(s): '
          + ' '.join(f'{w:04x}' for w in want))
    if not hits:
        print('  no match')
    for base in hits:
        window = ' '.join(f'{dm[a] & 0xFFFF:04x}'
                          for a in range(max(0, base - 2),
                                         min(DM_SIZE, base + len(want) + offset + 3)))
        print(f'  DM 0x{base:04x}   [{window}]')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
