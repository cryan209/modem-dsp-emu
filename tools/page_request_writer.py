#!/usr/bin/env python3
"""Log every DSP write to the page-request words, with the writer's PC.

Session 192 asked which processor decides the bootpage. The harness reads the
request out of DM(0x3132) (`wanted`) with DM(0x3131) as the flag and DM(0x3FB0)
as the bootpage, and serves whatever it finds -- so if the DSP writes those
words, the DSP is asking, and the MIPS protocol image is not in the loop for
this decision.

The core's DM write watch logs the storing PC plus the preceding 24, and skips
host writes: the shim's own `self.dm[...] = ...` assignments go through the
array rather than the store path, so `--force-info-after-v8` rewriting
DM(0x3132) does not appear here. What appears is the firmware.

Both the watch gate and the coverage gate default to *on* in the core, so both
are pushed down explicitly before the run -- see Session 192.

    page_request_writer.py CAPTURE.rx.ulaw [--to SECONDS] [--overlay 0x0260]
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

# DM(0x3131) request flag, DM(0x3132) download id, DM(0x3FB0) bootpage. Walking
# back from these is the whole method, so --words takes whatever the last hop
# named.
REQUEST_WORDS = '0x3131,0x3132,0x3FB0'
SAMPLE_RATE = 8000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('capture', type=Path)
    ap.add_argument('--to', type=float, default=6.0)
    ap.add_argument('--overlay', type=lambda s: int(s, 0), default=0x0260,
                    help='gate the watch to this overlay; 0 for ungated')
    ap.add_argument('--limit', type=int, default=200,
                    help='per-address log budget')
    ap.add_argument('--words', default=REQUEST_WORDS,
                    help='comma-separated DM addresses to watch')
    args = ap.parse_args()
    words = tuple(int(field, 0) for field in args.words.split(','))

    data = args.capture.read_bytes()
    card = create_native_mips_modem(KERNEL, TIKRNL, 'pcmu',
                                    force_info_after_v8=True,
                                    tx_prbs=True,
                                    native_bearer_activation=True)
    cpu = card.cpu
    ADSP.adsp2181_watch_gate(cpu, 0 if args.overlay else 1)
    for address in words:
        ADSP.adsp2181_watch_dm_writes(cpu, address, args.limit)

    gated = not args.overlay
    for index, code in enumerate(data):
        if index / SAMPLE_RATE > args.to:
            break
        if args.overlay:
            want = (card.resident == args.overlay)
            if want != gated:
                ADSP.adsp2181_watch_gate(cpu, 1 if want else 0)
                gated = want
        card.frame_fast(code, index)
    print(f'=== {args.capture.name} done ===')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
