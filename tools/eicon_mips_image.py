#!/usr/bin/env python3
"""Derive a protocol image's load layout from the image itself.

`eicon_mips_shim.py` hardcodes `te_dmlt.pm`'s layout: load base 0x80011000,
`$gp` 0x800fa3b5, initial `$sp` 0x80338700, entry 0x80082f90.  Every anchor
address in the shim is written as `BIAS + <file offset>`, so pointing the
harness at any other protocol image silently executes the wrong bytes.

The four values are not magic -- the image's own boot stub sets three of them
and jumps to the fourth, in the first few hundred instructions, and the stub
has the same shape in every build of this generation:

    lui  $gp, hi ; addiu $gp, $gp, lo          # global pointer
    lui  $sp, hi ; addiu $sp, $sp, lo ; addiu  # stack top (== OFFS_PROTOCOL_END_ADDR)
    ...
    lui  $t, hi  ; addiu $t, $t, lo ; jr/jalr  # firmware entry

The load base is recovered from the .bss clear, which starts at the first
address past the file image: `clear_start - len(image)`.  For `te_dmlt.pm`
that is 0x80100230 - 0xef230 = 0x80011000, and for `te_dmlt.2q0` it is
0x80152290 - 0x152290 = 0x80000000 -- the BRI v2 image is linked a full
0x11000 lower than the PRI one, which is the first thing that has to be right
before any anchor address in a new image means anything.

Builds 122-11 and later (`te_dmlt.am`, `.2qm`, `.2qf`, `.qpm`, and `te_dmlt.qm`)
set no global `$gp` at all; `derive_layout` reports `gp = None` for them rather
than guessing, since those need a different code model in the shim anyway.
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

# kernel/mi_pc.h
OFFS_PROTOCOL_END_ADDR = 0x7C
OFFS_PROTOCOL_ID_STRING = 0x80

# The stub lives at the very start of the image; everything derived here is
# inside it.  Bounding the scan keeps a stray `lui $gp` in ordinary code from
# being mistaken for the global-pointer setup.
STUB_SCAN_BYTES = 0x2000

# MIPS register numbers used by the stub patterns.
_GP = 28
_SP = 29

# Plausible kseg0 link bases for these images.  Used only to reject nonsense
# candidates from the .bss-clear heuristic.  The upper bound has to clear the
# 4BRI's fourth logical adapter at 0x80c00000: the four `.2q<n>` images are one
# build linked at 4 MB intervals in the card's shared SDRAM.
_BASE_MIN = 0x80000000
_BASE_MAX = 0x81000000


class FormatError(Exception):
    pass


@dataclass(frozen=True)
class ImageLayout:
    """Where a protocol image loads and what its boot stub sets up."""
    path: Path
    size: int
    base: int              # virtual address the image is loaded at
    entry: int             # firmware entry the boot stub jumps to
    gp: int | None         # global pointer, or None if the build sets none
    stack_top: int         # initial $sp
    protocol_end: int      # header +0x7c, the host's DspCodeBaseAddr input
    build: str

    def addr(self, file_offset: int) -> int:
        """Virtual address of a file offset."""
        return self.base + file_offset

    def offset(self, addr: int) -> int:
        """File offset of a virtual address, checked against the image."""
        off = addr - self.base
        if not 0 <= off < self.size:
            raise FormatError(f"0x{addr:08x} is outside {self.path.name}")
        return off


def _words(data: bytes, start: int, end: int):
    for off in range(start, min(end, len(data) - 3), 4):
        yield off, struct.unpack_from("<I", data, off)[0]


def _lui(word: int) -> tuple[int, int] | None:
    """(rt, imm) for `lui rt, imm`."""
    if word >> 26 != 0x0F:
        return None
    return (word >> 16) & 0x1F, word & 0xFFFF


def _addiu(word: int) -> tuple[int, int, int] | None:
    """(rt, rs, signed imm) for `addiu rt, rs, imm`."""
    if word >> 26 != 0x09:
        return None
    imm = word & 0xFFFF
    return (word >> 16) & 0x1F, (word >> 21) & 0x1F, imm - (0x10000 if imm & 0x8000 else 0)


def _register_constants(data: bytes, limit: int):
    """Every `lui r, hi` + `addiu r, r, lo` pair in the stub.

    Yields (file offset of the lui, register, value, instructions consumed).
    A third `addiu r, r, imm` is folded in when present: the stack setup is
    written as two adds (`+0x4700` then `+0x4000`) in both known builds.
    """
    for off, word in _words(data, 0, limit):
        lui = _lui(word)
        if lui is None:
            continue
        reg, hi = lui
        nxt = _addiu(struct.unpack_from("<I", data, off + 4)[0])
        if nxt is None or nxt[0] != reg or nxt[1] != reg:
            continue
        value = (hi << 16) + nxt[2]
        consumed = 2
        third = _addiu(struct.unpack_from("<I", data, off + 8)[0])
        if third is not None and third[0] == reg and third[1] == reg:
            value += third[2]
            consumed = 3
        yield off, reg, value & 0xFFFFFFFF, consumed


def _derive_base(data: bytes, limit: int) -> int:
    """Load base, from the .bss clear that starts just past the image."""
    size = len(data)
    candidates = []
    for _, reg, value, _ in _register_constants(data, limit):
        if reg in (_GP, _SP):
            continue
        base = value - size
        # A link base is page-aligned; the .bss start is not, in general.
        if _BASE_MIN <= base <= _BASE_MAX and base & 0xFFF == 0:
            candidates.append(base)
    if not candidates:
        raise FormatError("no .bss clear start matches the image length; "
                          "cannot derive the load base")
    # Both known builds produce exactly one candidate.  If a future image
    # produces more, the lowest is the conservative choice: a spurious match
    # can only come from a constant *above* the real .bss start.
    return min(candidates)


def _derive_entry(data: bytes, base: int, limit: int) -> int:
    """The address the stub's register-indirect jump out of the stub targets.

    `jr`/`jalr` on a register the stub has just loaded with a constant.  The
    PRI stub ends `lui $at, 0x8008; addiu $at, $at, 0x2f90; jr $at`; the BRI v2
    stub uses `lui $t1, 0x8009; addiu $t1, $t1, -0x1a08; jalr $t1` and then
    keeps going, so "the last one" is not the rule -- the BRI stub's later
    jumps are calls to stub-local helpers.  What separates the entry from those
    is distance: the helpers live inside the stub, the entry does not.
    """
    found = []
    for off, reg, value, consumed in _register_constants(data, limit):
        after = struct.unpack_from("<I", data, off + 4 * consumed)[0]
        if after >> 26 != 0:
            continue
        funct = after & 0x3F
        if funct not in (0x08, 0x09):          # jr, jalr
            continue
        if (after >> 21) & 0x1F != reg:
            continue
        if base + limit <= value < base + len(data):
            found.append(value)
    if not found:
        raise FormatError("no register-indirect jump out of the boot stub")
    if len(set(found)) > 1:
        targets = ", ".join(f"0x{v:08x}" for v in dict.fromkeys(found))
        raise FormatError(f"boot stub leaves for several addresses: {targets}")
    return found[0]


def derive_layout(path: Path, scan: int = STUB_SCAN_BYTES) -> ImageLayout:
    data = path.read_bytes()
    if len(data) < OFFS_PROTOCOL_ID_STRING + 0x40:
        raise FormatError(f"{path.name} is too short to be a protocol image")

    protocol_end = struct.unpack_from("<I", data, OFFS_PROTOCOL_END_ADDR)[0]
    if protocol_end == 0:
        raise FormatError("protocol image declares no end address")

    ident = data[OFFS_PROTOCOL_ID_STRING:OFFS_PROTOCOL_ID_STRING + 0x40]
    match = re.match(rb"[ -~]+", ident)
    build = match.group().decode() if match else ""

    base = _derive_base(data, scan)
    entry = _derive_entry(data, base, scan)

    gp = None
    stack_top = None
    for _, reg, value, _ in _register_constants(data, scan):
        if reg == _GP and gp is None:
            gp = value
        elif reg == _SP and stack_top is None:
            stack_top = value
    if stack_top is None:
        raise FormatError("boot stub sets no stack pointer")

    return ImageLayout(path=path, size=len(data), base=base, entry=entry,
                       gp=gp, stack_top=stack_top, protocol_end=protocol_end,
                       build=build)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", type=Path, nargs="+")
    args = parser.parse_args()
    for path in args.image:
        try:
            layout = derive_layout(path)
        except FormatError as exc:
            print(f"{path.name}: {exc}")
            continue
        gp = "none" if layout.gp is None else f"0x{layout.gp:08x}"
        print(f"{path.name}: {layout.build}")
        print(f"  base 0x{layout.base:08x}  size 0x{layout.size:x}  "
              f"entry 0x{layout.entry:08x}")
        print(f"  gp {gp}  sp 0x{layout.stack_top:08x}  "
              f"protocol end 0x{layout.protocol_end:08x}"
              f"{'' if layout.stack_top == layout.protocol_end else '  (sp != end!)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
