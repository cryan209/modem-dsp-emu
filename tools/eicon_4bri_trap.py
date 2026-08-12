#!/usr/bin/env python3
"""Replay a trapped Diva 4BRI-v1 MIPS CPU from a card SDRAM snapshot.

The divas4linux recovery path stores the MIPS exception frame at physical
SDRAM offset 0x90 after writing 0x99999999/0x99999901 at 0x80.  A read-only
snapshot of BAR2 therefore contains both the firmware and all CPU registers
needed to reproduce the fault under Unicorn.

This is deliberately a snapshot replay, not yet a cold-boot 4BRI harness.  It
provides a small, exact oracle while the 4BRI memory map, DSP ports, and host
request sequence are being retargeted from the existing PRI harness.
"""

from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

import eicon_mips_image

TRAP_ID_OFFSET = 0x80
TRAP_FRAME_OFFSET = 0x90
TRAP_IDS = (0x99999999, 0x99999901)
TRAP_FRAME_DWORDS = 40

CAUSES = (
    "Interrupt", "TLB modification", "TLB load/DBOUND", "TLB store",
    "Address error load", "Address error store", "Instruction load bus error",
    "Data load/store bus error", "Syscall", "Breakpoint", "Reserved instruction",
    "Coprocessor unusable", "Overflow", "TRAP", "VCEI",
    "Floating Point Exception", "CP2", "Reserved 17", "Reserved 18",
    "Reserved 19", "Reserved 20", "Reserved 21", "Reserved 22", "WATCH",
    "Reserved 24", "Reserved 25", "Reserved 26", "Reserved 27", "Reserved 28",
    "Reserved 29", "Reserved 30", "VCED",
)

REGISTER_NAMES = (
    "zero", "at", "v0", "v1", "a0", "a1", "a2", "a3",
    "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
    "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7",
    "t8", "t9", "k0", "k1", "gp", "sp", "s8", "ra",
)


class SnapshotError(Exception):
    pass


@dataclass(frozen=True)
class TrapFrame:
    sr: int
    cause: int
    epc: int
    bad_vaddr: int
    regs: tuple[int, ...]
    lo: int
    hi: int
    reserved: int
    xclass: int

    @property
    def cause_name(self) -> str:
        return CAUSES[(self.cause & 0x7C) >> 2]

    def reg(self, name: str) -> int:
        return self.regs[REGISTER_NAMES.index(name)]


def parse_trap(snapshot: bytes, base: int = 0) -> TrapFrame:
    """Parse the divas4linux MP_XCPTC frame from a BAR2 snapshot."""
    required = base + TRAP_FRAME_OFFSET + TRAP_FRAME_DWORDS * 4
    if len(snapshot) < required:
        raise SnapshotError(
            f"snapshot is too short: need 0x{required:x}, got 0x{len(snapshot):x}")
    trap_id = struct.unpack_from("<I", snapshot, base + TRAP_ID_OFFSET)[0]
    if trap_id not in TRAP_IDS:
        raise SnapshotError(
            f"no MIPS trap at BAR2+0x{base + TRAP_ID_OFFSET:x}: "
            f"found 0x{trap_id:08x}")
    words = struct.unpack_from(
        f"<{TRAP_FRAME_DWORDS}I", snapshot, base + TRAP_FRAME_OFFSET)
    sr, cause, epc, bad_vaddr = words[:4]
    regs = tuple(words[4:36])
    lo, hi, reserved, xclass = words[36:40]
    return TrapFrame(sr, cause, epc, bad_vaddr, regs, lo, hi, reserved, xclass)


def _unicorn_registers():
    from unicorn import mips_const

    unicorn_names = tuple(name.upper() if name != "s8" else "FP"
                          for name in REGISTER_NAMES)
    return tuple(getattr(mips_const, f"UC_MIPS_REG_{name}")
                 for name in unicorn_names)


def replay(snapshot: bytes, frame: TrapFrame, steps: int = 1,
           gp: int | None = None) -> tuple[int, tuple[int, int, int] | None, str | None]:
    """Run from EPC and return `(final_pc, invalid_access, unicorn_error)`.

    `invalid_access` is `(access, address, size)`.  A GP override is useful for
    testing the observed corrupt-GP hypothesis without modifying the snapshot.
    """
    try:
        from unicorn import (Uc, UcError, UC_ARCH_MIPS, UC_HOOK_MEM_INVALID,
                             UC_MODE_32, UC_MODE_LITTLE_ENDIAN)
        from unicorn.mips_const import (UC_MIPS_REG_HI, UC_MIPS_REG_LO,
                                        UC_MIPS_REG_PC)
    except ImportError as exc:
        raise RuntimeError("Unicorn is required for replay") from exc

    size = (len(snapshot) + 0xFFF) & ~0xFFF
    uc = Uc(UC_ARCH_MIPS, UC_MODE_LITTLE_ENDIAN | UC_MODE_32)
    uc.mem_map(0, size)
    uc.mem_write(0, snapshot)
    for register, value in zip(_unicorn_registers(), frame.regs):
        uc.reg_write(register, value)
    uc.reg_write(UC_MIPS_REG_LO, frame.lo)
    uc.reg_write(UC_MIPS_REG_HI, frame.hi)
    if gp is not None:
        uc.reg_write(_unicorn_registers()[28], gp)
    uc.reg_write(UC_MIPS_REG_PC, frame.epc)

    invalid = None

    def invalid_memory(_uc, access, address, size, _value, _user):
        nonlocal invalid
        invalid = (access, address, size)
        return False

    uc.hook_add(UC_HOOK_MEM_INVALID, invalid_memory)
    error = None
    try:
        uc.emu_start(frame.epc, 0, count=steps)
    except UcError as exc:
        error = str(exc)
    return uc.reg_read(UC_MIPS_REG_PC), invalid, error


def instruction_word(snapshot: bytes, pc: int) -> int:
    physical = pc & 0x1FFFFFFF
    if physical + 4 > len(snapshot):
        raise SnapshotError(f"PC 0x{pc:08x} is outside the BAR2 snapshot")
    return struct.unpack_from("<I", snapshot, physical)[0]


def load_effective_address(word: int, frame: TrapFrame) -> int | None:
    """Effective address when `word` is a MIPS load/store, otherwise None."""
    opcode = word >> 26
    # lb/lbu/lh/lhu/lw/lwl/lwr and sb/sh/sw/swl/swr.
    if opcode not in (0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26,
                      0x28, 0x29, 0x2A, 0x2B, 0x2E):
        return None
    base = (word >> 21) & 0x1F
    immediate = word & 0xFFFF
    if immediate & 0x8000:
        immediate -= 0x10000
    return (frame.regs[base] + immediate) & 0xFFFFFFFF


def stack_analysis(frame: TrapFrame, firmware: Path) -> tuple[eicon_mips_image.ImageLayout, int, int]:
    """Return `(layout, bytes_below_top, bytes_inside_image)` for trapped SP."""
    layout = eicon_mips_image.derive_layout(firmware)
    sp = frame.reg("sp")
    below_top = layout.stack_top - sp
    image_end = layout.base + layout.size
    inside = max(0, image_end - sp)
    return layout, below_top, inside


# A trap marker can be present without a usable frame behind it.  Build 107-234
# fails during bootstrap and leaves 0x99999999 with an all-but-empty context --
# sp and ra zero, epc down in the low shared-memory hole.  Run through the stack
# arithmetic above, that produces a confident two-gigabyte "overflow", so the
# frame has to be sanity-checked before any of it is believed.
CACHED_WINDOW = (0x80000000, 0x80400000)


def implausible(frame: TrapFrame,
                layout: eicon_mips_image.ImageLayout | None = None) -> list[str]:
    """Reasons the frame is not a live MIPS context.  Empty means it looks real."""
    low, high = CACHED_WINDOW
    reasons: list[str] = []
    sp = frame.reg("sp")
    if sp == 0:
        reasons.append("sp is zero")
    elif not low <= sp < high:
        reasons.append(f"sp 0x{sp:08x} is outside 0x{low:08x}..0x{high:08x}")
    if not low <= frame.epc < high:
        reasons.append(f"epc 0x{frame.epc:08x} is not in the cached image window")
    if frame.reg("ra") == 0:
        reasons.append("ra is zero")
    if layout is not None and sp and sp > layout.stack_top:
        reasons.append(
            f"sp 0x{sp:08x} is above the declared stack top 0x{layout.stack_top:08x}")
    return reasons


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="read-only 4 MiB BAR2 dump")
    parser.add_argument("--base", type=lambda value: int(value, 0), default=0,
                        help="physical base of the boot/trap frame (default: 0)")
    parser.add_argument("--steps", type=int, default=1,
                        help="instructions to execute from EPC (default: 1)")
    parser.add_argument("--gp", type=lambda value: int(value, 0),
                        help="override the trapped GP register")
    parser.add_argument("--firmware", type=Path,
                        help="matching protocol image; diagnoses stack/image overlap")
    args = parser.parse_args()

    snapshot = args.snapshot.read_bytes()
    try:
        frame = parse_trap(snapshot, args.base)
        word = instruction_word(snapshot, frame.epc)
    except SnapshotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"cause={frame.cause_name} sr=0x{frame.sr:08x} "
          f"cause-reg=0x{frame.cause:08x}")
    print(f"epc=0x{frame.epc:08x} instruction=0x{word:08x} "
          f"bad-vaddr=0x{frame.bad_vaddr:08x}")
    print(f"gp=0x{frame.reg('gp'):08x} sp=0x{frame.reg('sp'):08x} "
          f"ra=0x{frame.reg('ra'):08x} class=0x{frame.xclass:08x}")
    effective = load_effective_address(word, frame)
    if effective is not None:
        verdict = "matches BadVAddr" if effective == frame.bad_vaddr else "DIFFERS from BadVAddr"
        print(f"effective-address=0x{effective:08x} ({verdict})")

    doubts = implausible(frame)
    if doubts:
        print("warning: this is not a live context (" + "; ".join(doubts) + ")")
        print("         the marker is set but the frame is not usable; treat every "
              "value above as unreliable")

    if args.firmware is not None:
        try:
            layout, used, intrusion = stack_analysis(frame, args.firmware)
        except eicon_mips_image.FormatError as exc:
            print(f"error: cannot derive {args.firmware}: {exc}", file=sys.stderr)
            return 2
        print(f"firmware={layout.build}")
        print(f"image=0x{layout.base:08x}..0x{layout.base + layout.size:08x} "
              f"stack-top=0x{layout.stack_top:08x} trapped-sp=0x{frame.reg('sp'):08x}")
        if implausible(frame, layout):
            print("stack analysis skipped: the frame is not a live context, so its "
                  "depth and image overlap would be meaningless")
        else:
            print(f"stack-depth={used} bytes image-intrusion={intrusion} bytes")
            if intrusion:
                print("diagnosis: stack crossed into the protocol image; saved register/return "
                      "state is corrupt and the invalid GP-relative read is secondary")

    try:
        pc, invalid, error = replay(snapshot, frame, args.steps, args.gp)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if invalid is not None:
        _, address, size = invalid
        print(f"replayed-fault: read/write size={size} address=0x{address:08x} "
              f"pc=0x{pc:08x}")
    else:
        print(f"replay stopped at pc=0x{pc:08x}")
    if error:
        print(f"unicorn: {error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
