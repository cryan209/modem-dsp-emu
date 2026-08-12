#!/usr/bin/env python3
"""Disassemble MIPS-I code out of a Diva protocol image.

Enough of the ISA to read the 4BRI/PRI protocol images: the integer subset the
firmware is actually built from.  Anything unrecognised prints as `.word`,
which is also how data regions announce themselves -- repeated identical
`.word` lines mean the address being read is a table, not code.

Addresses are virtual (`0x80000000`-based); the file offset is derived from the
image layout, so the same address seen in an exception frame can be passed
straight in.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import eicon_mips_image

REGS = (
    "zero", "at", "v0", "v1", "a0", "a1", "a2", "a3",
    "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
    "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7",
    "t8", "t9", "k0", "k1", "gp", "sp", "s8", "ra",
)

_SPECIAL = {
    0x00: "sll", 0x02: "srl", 0x03: "sra", 0x04: "sllv", 0x06: "srlv",
    0x07: "srav", 0x08: "jr", 0x09: "jalr", 0x0C: "syscall", 0x0D: "break",
    0x10: "mfhi", 0x11: "mthi", 0x12: "mflo", 0x13: "mtlo", 0x18: "mult",
    0x19: "multu", 0x1A: "div", 0x1B: "divu", 0x20: "add", 0x21: "addu",
    0x22: "sub", 0x23: "subu", 0x24: "and", 0x25: "or", 0x26: "xor",
    0x27: "nor", 0x2A: "slt", 0x2B: "sltu",
}
_IMM = {
    0x08: "addi", 0x09: "addiu", 0x0A: "slti", 0x0B: "sltiu", 0x0C: "andi",
    0x0D: "ori", 0x0E: "xori",
}
_MEM = {
    0x20: "lb", 0x21: "lh", 0x23: "lw", 0x24: "lbu", 0x25: "lhu",
    0x28: "sb", 0x29: "sh", 0x2B: "sw",
}
_BRANCH = {0x04: "beq", 0x05: "bne", 0x06: "blez", 0x07: "bgtz",
           # MIPS-II "likely" forms.  The firmware uses these for type checks,
           # and decoding them as `.word` hides real control flow.  On a likely
           # branch the delay slot is nullified when the branch is NOT taken.
           0x14: "beql", 0x15: "bnel", 0x16: "blezl", 0x17: "bgtzl"}
_TWO_REG_BRANCH = {0x04, 0x05, 0x14, 0x15}

# Memory-op opcodes whose base register makes an access gp-relative.
GP_REG = 28


def disassemble(word: int, pc: int) -> str:
    """One instruction, AT&T-ish MIPS syntax.  `pc` resolves branch targets."""
    if word == 0:
        return "nop"
    op = word >> 26
    rs, rt, rd = (word >> 21) & 31, (word >> 16) & 31, (word >> 11) & 31
    sa, fn = (word >> 6) & 31, word & 63
    imm = word & 0xFFFF
    simm = imm - 0x10000 if imm & 0x8000 else imm

    if op == 0:
        name = _SPECIAL.get(fn)
        if name is None:
            return f".word 0x{word:08x}"
        if name == "jr":
            return f"jr    {REGS[rs]}"
        if name == "jalr":
            return f"jalr  {REGS[rs]}"
        if name in ("sll", "srl", "sra"):
            return f"{name:<5} {REGS[rd]}, {REGS[rt]}, {sa}"
        if name in ("mfhi", "mflo"):
            return f"{name:<5} {REGS[rd]}"
        if name in ("mthi", "mtlo"):
            return f"{name:<5} {REGS[rs]}"
        if name in ("mult", "multu", "div", "divu"):
            return f"{name:<5} {REGS[rs]}, {REGS[rt]}"
        if name in ("syscall", "break"):
            return name
        return f"{name:<5} {REGS[rd]}, {REGS[rs]}, {REGS[rt]}"

    if op == 0x0F:
        return f"lui   {REGS[rt]}, 0x{imm:04x}"
    if op in (0x02, 0x03):
        target = (pc & 0xF0000000) | ((word & 0x03FFFFFF) << 2)
        return f"{'j' if op == 0x02 else 'jal':<5} 0x{target:08x}"
    if op in _BRANCH:
        target = pc + 4 + simm * 4
        if op in _TWO_REG_BRANCH:
            return f"{_BRANCH[op]:<5} {REGS[rs]}, {REGS[rt]}, 0x{target:08x}"
        return f"{_BRANCH[op]:<5} {REGS[rs]}, 0x{target:08x}"
    if op in _MEM:
        return f"{_MEM[op]:<5} {REGS[rt]}, {simm}({REGS[rs]})"
    if op in _IMM:
        return f"{_IMM[op]:<5} {REGS[rt]}, {REGS[rs]}, {simm}"
    return f".word 0x{word:08x}"


def is_gp_relative(word: int) -> bool:
    """True for a load/store based on $gp.

    The 4BRI images never establish `$gp` -- `derive_layout` reports it as
    `None`, the `set_gp` accessor has no callers, and the only other write
    reloads it from an uninitialised task-context slot.  Every gp-relative
    access is therefore a latent fault; see docs/4bri_v1_firmware_replay.md.
    """
    return (word >> 26) in _MEM and ((word >> 21) & 31) == GP_REG


def is_struct_access(word: int, imm: int) -> bool:
    """True for a load/store whose displacement is `imm`.

    Structure fields in this firmware are addressed as `lw v0, 1752(s0)` and
    nothing else, so scanning for one displacement enumerates every reader and
    writer of one field -- which is how "the pointer at +12 has exactly two
    writers" was established.  Data misread as code produces false positives;
    judge each hit in context.
    """
    return (word >> 26) in _MEM and (word & 0xFFFF) == (imm & 0xFFFF)


def is_call_to(word: int, target: int) -> bool:
    """True for a `jal` whose target is `target`.

    Only direct calls.  A function reached solely through a dispatch table
    (`jalr`) has no `jal` site and will look uncalled to this scan.
    """
    return word == (0x0C000000 | ((target >> 2) & 0x03FFFFFF))


def _iter_words(data: bytes, offset: int, count: int):
    for i in range(count):
        pos = offset + i * 4
        if pos + 4 > len(data):
            return
        yield i, struct.unpack_from("<I", data, pos)[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="protocol image (.qm/.am/...)")
    parser.add_argument("address", nargs="?",
                        help="virtual start address, e.g. 0x80063f68")
    parser.add_argument("-n", "--count", type=int, default=16,
                        help="instructions to print (default 16)")
    parser.add_argument("--scan-gp", action="store_true",
                        help="instead, list every gp-relative access in the image")
    parser.add_argument("--field",
                        help="instead, list every load/store at this structure "
                             "displacement, e.g. 0x6d8")
    parser.add_argument("--callers",
                        help="instead, list every jal site targeting this "
                             "address, e.g. 0x800821a8")
    args = parser.parse_args()

    data = args.image.read_bytes()
    try:
        base = eicon_mips_image.derive_layout(args.image).base
    except Exception:
        base = 0x80000000

    if args.scan_gp:
        found = 0
        for i, word in _iter_words(data, 0, len(data) // 4):
            if is_gp_relative(word):
                addr = base + i * 4
                print(f"  {addr:08x}: {word:08x}  {disassemble(word, addr)}")
                found += 1
        print(f"{found} word(s) decode as gp-relative accesses.")
        print("This scan cannot tell code from data: the image's table region "
              "produces\nmany false positives, recognisable by repeating byte "
              "patterns (0x93939393)\nand by clustering in the high address "
              "range.  Judge each hit in context.")
        return 0

    if args.field is not None:
        imm = int(args.field, 0)
        found = 0
        for i, word in _iter_words(data, 0, len(data) // 4):
            if is_struct_access(word, imm):
                addr = base + i * 4
                print(f"  {addr:08x}: {word:08x}  {disassemble(word, addr)}")
                found += 1
        print(f"{found} access(es) at displacement 0x{imm:x}.")
        return 0

    if args.callers is not None:
        target = int(args.callers, 0)
        found = 0
        for i, word in _iter_words(data, 0, len(data) // 4):
            if is_call_to(word, target):
                addr = base + i * 4
                print(f"  {addr:08x}: {word:08x}  {disassemble(word, addr)}")
                found += 1
        print(f"{found} jal site(s) targeting 0x{target:08x}.")
        return 0

    if args.address is None:
        parser.error("an address is required unless a scan option is given")
    start = int(args.address, 0)
    offset = start - base
    if offset < 0 or offset >= len(data):
        print(f"address 0x{start:08x} outside image "
              f"0x{base:08x}..0x{base + len(data):08x}", file=sys.stderr)
        return 1
    for i, word in _iter_words(data, offset, args.count):
        addr = start + i * 4
        print(f"  {addr:08x}: {word:08x}  {disassemble(word, addr)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
