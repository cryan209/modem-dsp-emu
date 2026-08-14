#!/usr/bin/env python3
"""Disassemble the Aster 5 68000 control image with capstone.

The image is a multi-segment loader image (AST5LDR), and its load addresses are
not yet recovered -- see docs/aster5_control_image.md. So addresses here are
FILE OFFSETS, not runtime addresses. PC-relative control flow (Bcc, BSR, DBcc)
is therefore correct as printed; absolute references (jsr $xxxxxx.l, movea.l
#$xxxxxx) are runtime addresses and do NOT index this file. Pass --base to
subtract a candidate load address from them once one is known.

Needs capstone with the M68K architecture, which is not in the system Python:

    /tmp/eicon-venv/bin/pip install capstone
    /tmp/eicon-venv/bin/python tools/aster68k_dis.py <file> 0x20a --count 24

Linear sweep resynchronises by stepping two bytes past anything that will not
decode, so output inside data regions is noise; use it on known code.
"""

import argparse
import re
import sys

try:
    from capstone import CS_ARCH_M68K, CS_MODE_BIG_ENDIAN, CS_MODE_M68K_040, Cs
except ImportError:
    sys.exit("capstone not available; try /tmp/eicon-venv/bin/python " + sys.argv[0])

ABS_RE = re.compile(r"\$([0-9a-f]+)\.l")
FLOW_END = {"rts", "rte", "rtr", "jmp", "bra"}


def sweep(data, start, end, md):
    """Yield instructions, resynchronising two bytes at a time past bad decodes."""
    pos = start
    while pos < end:
        decoded = False
        for insn in md.disasm(data[pos:min(pos + 32, end)], pos):
            yield insn
            pos = insn.address + insn.size
            decoded = True
            break
        if not decoded:
            pos += 2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path")
    ap.add_argument("offset", help="file offset to start at, e.g. 0x20a")
    ap.add_argument("--count", type=int, default=32, help="instructions to print")
    ap.add_argument("--until-return", action="store_true",
                    help="stop at the first rts/rte/jmp/bra instead of after --count")
    ap.add_argument("--base", type=lambda s: int(s, 0), default=None,
                    help="candidate load address; annotates absolute refs with a file offset")
    args = ap.parse_args(argv)

    with open(args.path, "rb") as fh:
        data = fh.read()
    start = int(args.offset, 0)
    md = Cs(CS_ARCH_M68K, CS_MODE_BIG_ENDIAN | CS_MODE_M68K_040)

    shown = 0
    for insn in sweep(data, start, len(data), md):
        text = f"{insn.mnemonic} {insn.op_str}".strip()
        note = ""
        if args.base is not None:
            for m in ABS_RE.finditer(text):
                off = int(m.group(1), 16) - args.base
                if 0 <= off < len(data):
                    note = f"    ; file 0x{off:06x}"
                    break
        print(f"{insn.address:06x}  {insn.bytes.hex():<14} {text}{note}")
        shown += 1
        if args.until_return and insn.mnemonic in FLOW_END:
            break
        if not args.until_return and shown >= args.count:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
