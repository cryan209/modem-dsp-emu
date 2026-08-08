#!/usr/bin/env python3
"""Diff what the INFO page executes on a call that takes V.90 against one that does not.

Run from the repository root: the firmware paths the shim reads are relative.

Session 190 left a sharp question: `INFO_mode` reaches 0x0009 identically on
calls that load overlay 0x026a and on calls that go to V.34 instead, so the
INFO parse is not what diverges. Something after it fails to turn that state
into a bootpage-14 request.

This replays a capture, gates per-address execution coverage to the INFO
overlay (0x0260), and writes the counts to a file. Run it on a capture whose
call took V.90 and on one whose call did not, then diff: the first PM address
that one executes and the other does not is the branch that decides.

Coverage is gated on residency because a PM address is a different instruction
on every page -- ungated counts are what made Session 188l wrong twice.

    info_diff.py CAPTURE.rx.ulaw OUT.cov [--to SECONDS]
    info_diff.py --compare A.cov B.cov
"""
from __future__ import annotations

import argparse
import ctypes
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eicon_mips_shim import create_native_mips_modem, ADSP  # noqa: E402

BUILD = Path('artifacts/eicon-dsp/build-117-926')
KERNEL = BUILD / 'kernel' / '0009-diva-server-pri-30m-kernel'
TIKRNL = BUILD / 'tikrnl' / '0258-tikrnl81.f34-task'

INFO_OVERLAY = 0x0260
PM_SIZE = 0x4000
SAMPLE_RATE = 8000


def collect(capture: Path, end: float) -> dict:
    data = capture.read_bytes()
    card = create_native_mips_modem(KERNEL, TIKRNL, 'pcmu',
                                    force_info_after_v8=True,
                                    tx_prbs=True,
                                    native_bearer_activation=True)
    cpu = card.cpu if hasattr(card, 'cpu') else None
    if cpu is None:
        raise SystemExit('no cpu handle on the card object; check the shim API')

    ADSP.adsp2181_coverage_clear(cpu)
    # The core's counter defaults to on, so a `gated = False` that is never
    # pushed down leaves every page before this one counted -- which is the
    # ungated count this tool exists to avoid. Say it out loud.
    ADSP.adsp2181_coverage_gate(cpu, 0)
    gated = False
    pages = []
    last_resident = None

    for index, code in enumerate(data):
        if index / SAMPLE_RATE > end:
            break
        resident = card.resident
        if resident != last_resident:
            pages.append((round(index / SAMPLE_RATE, 3), resident))
            last_resident = resident
        # Gate the counter to the INFO page only.
        want = (resident == INFO_OVERLAY)
        if want != gated:
            ADSP.adsp2181_coverage_gate(cpu, 1 if want else 0)
            gated = want
        card.frame_fast(code, index)

    counts = {}
    for pc in range(PM_SIZE):
        n = ADSP.adsp2181_coverage_count(cpu, pc)
        if n:
            counts[pc] = int(n)
    return {'capture': str(capture), 'pages': pages, 'counts': counts,
            'took_v90': any(p == 0x026A for _, p in pages)}


def compare(a_path: Path, b_path: Path) -> None:
    a = json.loads(a_path.read_text())
    b = json.loads(b_path.read_text())
    ac = {int(k): v for k, v in a['counts'].items()}
    bc = {int(k): v for k, v in b['counts'].items()}
    print(f"A {Path(a['capture']).name:28} V.90={a['took_v90']}  "
          f"{len(ac)} PM addresses")
    print(f"B {Path(b['capture']).name:28} V.90={b['took_v90']}  "
          f"{len(bc)} PM addresses")
    print(f"  A pages: {' '.join(f'{p:#06x}@{t}' for t, p in a['pages'])}")
    print(f"  B pages: {' '.join(f'{p:#06x}@{t}' for t, p in b['pages'])}")

    only_a = sorted(set(ac) - set(bc))
    only_b = sorted(set(bc) - set(ac))
    print(f"\nexecuted only in A ({len(only_a)}):")
    for pc in only_a[:40]:
        print(f"  PM {pc:#06x}  x{ac[pc]}")
    print(f"\nexecuted only in B ({len(only_b)}):")
    for pc in only_b[:40]:
        print(f"  PM {pc:#06x}  x{bc[pc]}")

    both = sorted(set(ac) & set(bc))
    skew = [(pc, ac[pc], bc[pc]) for pc in both
            if max(ac[pc], bc[pc]) > 8 * max(1, min(ac[pc], bc[pc]))]
    print(f"\nshared but >8x count skew ({len(skew)}):")
    for pc, x, y in skew[:25]:
        print(f"  PM {pc:#06x}  A={x:<10} B={y}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('args', nargs='*')
    ap.add_argument('--to', type=float, default=20.0)
    ap.add_argument('--compare', action='store_true')
    a = ap.parse_args()
    if a.compare:
        compare(Path(a.args[0]), Path(a.args[1]))
        return 0
    capture, out = Path(a.args[0]), Path(a.args[1])
    result = collect(capture, a.to)
    out.write_text(json.dumps(result))
    pages = ' '.join(f'{p:#06x}@{t}' for t, p in result['pages'])
    print(f'{capture.name}: V.90={result["took_v90"]} '
          f'{len(result["counts"])} PM addresses on the INFO page')
    print(f'  pages: {pages}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
