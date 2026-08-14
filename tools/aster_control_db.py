#!/usr/bin/env python3
"""Extract the management database embedded in the Aster 5 68000 control image.

The image carries a self-describing, SNMP-style attribute database in a nested
tag/length/value encoding. Two record shapes are recovered here, both verified
by their internal length fields:

  attribute   0xA3 <len> 'T' <len> name NUL ... optionally 0x39 <len> with a
              'T' name / 'V' value pair giving the attribute's default symbol
  enum item   'I' <len> 'T' <len> name NUL 'V' <len> value
              where <len> after 'I' covers exactly the T and V fields

Enum items appear in contiguous runs, one run per enumerated type. The runs
live in a different region of the image from the attribute records and are not
positionally associated with them, so runs are reported separately; an
attribute's inline default symbol is the one association this tool asserts.

The remaining tags in each record (0x19, 0x1B, 0x1C, 0x24-0x28, 'U', 'Y', '^',
'+', 'H', '\\') are not decoded. They carry object identifiers and access/type
metadata; nothing here depends on them.

    ./tools/aster_control_db.py "docs/firmware/Aster 5 Control/T8261018.00"
    ./tools/aster_control_db.py <file> --grep 'modul|train|speed'
    ./tools/aster_control_db.py <file> --enums
"""

import argparse
import json
import re
import sys

NAME = rb"[A-Za-z][A-Za-z0-9_+/]{0,29}"

ATTR_RE = re.compile(rb"\xa3(.)T(.)(" + NAME + rb")\x00", re.S)
DEFAULT_RE = re.compile(rb"9(.)T(.)([A-Za-z0-9][A-Za-z0-9_+/]{0,29})\x00V(.)", re.S)
ENUM_RE = re.compile(rb"I(.)T(.)(" + NAME + rb")\x00V(.)", re.S)

# How far past an attribute's name to look for its inline default symbol.
DEFAULT_WINDOW = 48


def _value(buf, off, length):
    if not 1 <= length <= 4 or off + length > len(buf):
        return None
    return int.from_bytes(buf[off:off + length], "big")


def enum_items(buf):
    """Every well-formed enum item, as (offset, name, value)."""
    items = []
    for m in ENUM_RE.finditer(buf):
        ilen, tlen, name, vlen = m.group(1)[0], m.group(2)[0], m.group(3), m.group(4)[0]
        if tlen != len(name) + 1:
            continue
        # The 'I' length covers the T field (2 + tlen) and the V field (2 + vlen).
        if ilen != 4 + tlen + vlen:
            continue
        value = _value(buf, m.end(), vlen)
        if value is None:
            continue
        items.append((m.start(), name.decode(), value, m.end() + vlen))
    return items


def enum_runs(buf, gap=8):
    """Group enum items into runs; each run is one enumerated type."""
    runs = []
    for off, name, value, end in enum_items(buf):
        if runs and off - runs[-1][-1][3] <= gap:
            runs[-1].append((off, name, value, end))
        else:
            runs.append([(off, name, value, end)])
    return runs


def attributes(buf):
    """Every attribute record, with its default symbol where one is present."""
    out = []
    for m in ATTR_RE.finditer(buf):
        tlen, name = m.group(2)[0], m.group(3)
        if tlen != len(name) + 1:
            continue
        entry = {"offset": m.start(), "name": name.decode(),
                 "default": None, "default_value": None}
        window = buf[m.end():m.end() + DEFAULT_WINDOW]
        d = DEFAULT_RE.search(window)
        if d and d.group(2)[0] == len(d.group(3)) + 1:
            vlen = d.group(4)[0]
            value = _value(buf, m.end() + d.end(), vlen)
            if value is not None:
                entry["default"] = d.group(3).decode()
                entry["default_value"] = value
        out.append(entry)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path")
    ap.add_argument("--grep", help="case-insensitive regex over names")
    ap.add_argument("--enums", action="store_true", help="list enum runs instead of attributes")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    with open(args.path, "rb") as fh:
        buf = fh.read()
    want = re.compile(args.grep, re.I) if args.grep else None

    if args.enums:
        runs = enum_runs(buf)
        shown = []
        for run in runs:
            names = [n for _, n, _, _ in run]
            if want and not any(want.search(n) for n in names):
                continue
            shown.append({"offset": run[0][0],
                          "items": [{"name": n, "value": v} for _, n, v, _ in run]})
        if args.json:
            print(json.dumps(shown, indent=2))
            return 0
        print(f"{args.path}: {len(runs)} enum runs, showing {len(shown)}")
        for run in shown:
            body = "  ".join(f"{i['name']}={i['value']}" for i in run["items"])
            print(f"\n  0x{run['offset']:06x}  ({len(run['items'])} symbols)\n    {body}")
        return 0

    attrs = attributes(buf)
    shown = [a for a in attrs if not want or want.search(a["name"])]
    if args.json:
        print(json.dumps(shown, indent=2))
        return 0
    print(f"{args.path}: {len(attrs)} attribute records "
          f"({len({a['name'] for a in attrs})} distinct), showing {len(shown)}")
    for a in shown:
        default = "" if a["default"] is None else f"  default {a['default']} = {a['default_value']}"
        print(f"  0x{a['offset']:06x}  {a['name']:<32}{default}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
