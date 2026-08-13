#!/usr/bin/env python3
"""Recover Analog-card POTS/DAA configuration records from te_dmlt.am.

Build 109 is a flat absolute-address MIPS image with no symbols, but its
management database retains names. Each 28-byte descriptor stores the name
pointer, type/access word, callback, and controller-field offset. Resolving
those records gives concrete POTS state to emulate without guessing from
strings alone.
"""
from __future__ import annotations

import argparse
import struct
from pathlib import Path

from eicon_mips_image import derive_layout

NAMES = (
    "Ring", "OffHook", "rxhook", "txhook", "Timeslots",
    "AudioTS# Enable", "AudioCh# Enable", "Playing Gain dB",
    "Recording Gain dB", "LI Gain Boost dB", "RingsUntilAnswer",
)
CARD_TABLE = 0x1C4880
CARD_RECORD_SIZE = 28


def cstrings(data: bytes):
    start = 0
    while start < len(data):
        end = data.find(b"\0", start)
        if end < 0:
            return
        if end > start:
            try:
                text = data[start:end].decode("ascii")
            except UnicodeDecodeError:
                text = ""
            if text:
                yield start, text
        start = end + 1


def find_all(data: bytes, needle: bytes):
    pos = 0
    while True:
        pos = data.find(needle, pos)
        if pos < 0:
            return
        yield pos
        pos += 1


def descriptors(path: Path):
    data = path.read_bytes()
    layout = derive_layout(path)
    strings = {text: off for off, text in cstrings(data) if text in NAMES}
    for name in NAMES:
        string_off = strings.get(name)
        if string_off is None:
            continue
        pointer = layout.base + string_off
        for record_off in find_all(data, struct.pack("<I", pointer)):
            if record_off + 16 > len(data):
                continue
            kind, callback, field = struct.unpack_from("<III", data,
                                                       record_off + 4)
            # Real management descriptors point back into executable image and
            # use a small controller-relative field. This rejects constants in
            # code and duplicate string references that are not records.
            if not (layout.base <= callback < layout.base + len(data)):
                continue
            if field >= 0x10000:
                continue
            yield name, record_off, kind, callback, field


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("image", nargs="?", type=Path,
                    default=Path("docs/firmware/build-109/te_dmlt.am"))
    ap.add_argument("--card-type", type=int, default=77,
                    help="print this card-property record too (default 77)")
    args = ap.parse_args()
    layout = derive_layout(args.image)
    print(f"{args.image}: {layout.build}")
    print("name                    record      kind       callback    field")
    for name, off, kind, callback, field in descriptors(args.image):
        print(f"{name:<23} 0x{off:06x}  0x{kind:08x} 0x{callback:08x} +0x{field:03x}")

    data = args.image.read_bytes()
    off = CARD_TABLE + args.card_type * CARD_RECORD_SIZE
    if off + CARD_RECORD_SIZE <= len(data):
        record = data[off:off + CARD_RECORD_SIZE]
        print(f"card type {args.card_type}: property record @0x{off:06x}, "
              f"channels={record[13]}, class={record[10]}, "
              f"raw={record.hex()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
