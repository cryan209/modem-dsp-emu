#!/usr/bin/env python3
"""Find the sample on which a PM address first executes, with the page gated.

Session 191 located the V.90 decision as two adjacent arms -- PM 0x2bc1 on the
call that takes V.90 and PM 0x2b9a on the Conexant's -- each entered exactly
once. Reading what they are conditional on needs the *frame* they run in, so
that `EICON_TRACE_FRAMES` can be aimed at it: the shim's trace is armed by
sample number and a whole frame is ~4,000 lines, so naming the wrong one costs
a run and tells you nothing.

Coverage is gated to the overlay for the same reason as info_page_diff.py: a PM
address is a different instruction on every page, and an ungated count is spent
by the boot and V.8 pages long before the page under test.

    branch_frame.py CAPTURE.rx.ulaw 0x2b9a[,0x2bc1] [--to SECONDS]
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

INFO_OVERLAY = 0x0260
SAMPLE_RATE = 8000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('capture', type=Path)
    ap.add_argument('addresses')
    ap.add_argument('--to', type=float, default=6.0)
    ap.add_argument('--overlay', type=lambda s: int(s, 0), default=INFO_OVERLAY)
    args = ap.parse_args()

    wanted = [int(field, 0) for field in args.addresses.split(',')]
    data = args.capture.read_bytes()
    card = create_native_mips_modem(KERNEL, TIKRNL, 'pcmu',
                                    force_info_after_v8=True,
                                    tx_prbs=True,
                                    native_bearer_activation=True)
    cpu = card.cpu
    ADSP.adsp2181_coverage_clear(cpu)
    # The core's counter defaults to on; the gate has to be pushed down before
    # the first transition or every earlier page is counted as this one.
    ADSP.adsp2181_coverage_gate(cpu, 0)
    gated = False
    seen = {address: 0 for address in wanted}
    hits: list[tuple[int, int, int]] = []

    for index, code in enumerate(data):
        if index / SAMPLE_RATE > args.to:
            break
        want = (card.resident == args.overlay)
        if want != gated:
            ADSP.adsp2181_coverage_gate(cpu, 1 if want else 0)
            gated = want
        card.frame_fast(code, index)
        if not want:
            continue
        for address in wanted:
            count = int(ADSP.adsp2181_coverage_count(cpu, address))
            if count != seen[address]:
                # `_media_samples`, not the loop index: that is the counter
                # EICON_TRACE_FRAMES is armed against, and it leads the index
                # by one because _frame_core increments it on entry.
                hits.append((address, index, count - seen[address],
                             card._media_samples))
                seen[address] = count

    print(f'\n=== {args.capture.name} ===')
    for address, index, delta, frame in hits:
        print(f'PM 0x{address:04x}  sample {index}  '
              f'({index / SAMPLE_RATE:.3f} s)  +{delta}  '
              f'EICON_TRACE_FRAMES={frame}')
    if not hits:
        print('no hits')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
