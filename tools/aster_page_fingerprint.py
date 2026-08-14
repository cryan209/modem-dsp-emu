#!/usr/bin/env python3
"""Identify Telindus Aster boot pages by matching DM word values against Eicon overlays.

Both firmwares are builds of the same ADDSP V.90 package, so a page and the
Eicon overlay implementing the same modulation share large runs of identical
coefficient and state tables at identical DM addresses, even though the code
around them was laid out by different builds. The score is the fraction of
shared DM addresses that hold the same word.

Calibration, from the pages whose identity is already known by index:

    V.34 vs V34.ANA          83.7%    <- same modulation, same package
    V.32 vs V22V32.ANA LEC   61%
    INFO vs INFO.ANA         61%
    V.8  vs V8.ANA           35%
    anything unrelated       0-6%     <- noise floor

So a score in the tens identifies a page; single digits mean no relation.

Extract the Eicon side first, then point this at the directory:

    ./tools/eicon_dsp_extract.py docs/firmware/dspdload.bin \
        --match '\\.ANA' -o /tmp/eicon
    ./tools/aster_page_fingerprint.py "docs/firmware/Aster 5 DSP/T8660014.00" /tmp/eicon
"""

import argparse
import glob
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aster_dsp_extract import parse  # noqa: E402


def aster_dm(buf, page):
    """Address -> 16-bit word, for the DM blocks of one page."""
    words = {}
    for b in page.blocks:
        if not b.is_dm:
            continue
        for i in range(b.length):
            words[b.address + i] = struct.unpack_from(">H", buf, b.data_off + 2 * i)[0]
    return words


def eicon_dm(overlay_dir):
    """Address -> 16-bit word, from an eicon_dsp_extract.py dm.words map."""
    words = {}
    path = os.path.join(overlay_dir, "dm.words")
    with open(path) as fh:
        for line in fh:
            parts = line.split()
            if len(parts) == 2:
                words[int(parts[0], 16)] = int(parts[1], 16)
    return words


def score(a, b):
    common = set(a) & set(b)
    if not common:
        return 0.0, 0, 0
    same = sum(1 for addr in common if a[addr] == b[addr])
    return same / len(common), same, len(common)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("aster_file")
    ap.add_argument("eicon_dir", help="output directory of eicon_dsp_extract.py -o")
    ap.add_argument("--image", type=int, default=0)
    ap.add_argument("--page", type=int, help="restrict to one page index")
    ap.add_argument("--top", type=int, default=6, help="candidates to show per page")
    ap.add_argument("--min", type=float, default=0.0, help="hide scores below this fraction")
    args = ap.parse_args(argv)

    with open(args.aster_file, "rb") as fh:
        buf = fh.read()
    _, images = parse(buf)
    img = images[args.image]

    overlays = []
    for d in sorted(glob.glob(os.path.join(args.eicon_dir, "*"))):
        if os.path.exists(os.path.join(d, "dm.words")):
            overlays.append((os.path.basename(d), eicon_dm(d)))
    if not overlays:
        print(f"no extracted overlays under {args.eicon_dir}", file=sys.stderr)
        return 2

    for page in img.all_pages():
        if args.page is not None and page.index != args.page:
            continue
        a = aster_dm(buf, page)
        rows = []
        for name, e in overlays:
            frac, same, common = score(a, e)
            if common and frac >= args.min:
                rows.append((frac, same, common, name))
        rows.sort(reverse=True)
        label = "STARTUP" if page.index is None else f"page {page.index} ({page.name})"
        print(f"\n{label}: {len(a)} DM words")
        for frac, same, common, name in rows[:args.top]:
            print(f"  {frac * 100:5.1f}%  {same:6}/{common:<6} {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
