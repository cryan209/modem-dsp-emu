#!/usr/bin/env python3
"""Cold-boot the Diva 4BRI-v1 protocol image under Unicorn.

Snapshot replay cannot recover the object behind the null-pointer trap: the
dump is the state *after* the code on that path ran, and the path is not
idempotent (see `tools/eicon_4bri_find_object.py`).  The pointer has to come
from a machine that built the object itself, which means booting the image
from its reset vector.

What this models, and what it does not:

- **SDRAM** -- 4 MiB at physical 0, which is all of it (`MQ_MEMORY_SIZE`).
  The image is loaded at offset 0 exactly as the driver loads it, with the two
  header patches the driver applies: `0x68 = 0x04` (four initial tasks) and
  `0x69 = 0x16` (card type 22).
- **The reset vector** -- the image's own first instruction, which jumps
  through kseg1 to physical 0x44004 and runs the memory-controller setup.
- **Everything else on the card** is discovered rather than declared.  Any
  access outside SDRAM maps a zero page on demand and is logged, so a boot
  attempt prints the register map it wanted instead of dying on the first
  touch.  `--mmio` shows every one of those accesses; the summary shows the
  distinct pages.

This is a boot harness, not a card model.  Reaching the trap needs the timer
that fires it, and a stub register that always reads zero is not that timer.
Read the ladder it prints as "how far the image gets", and add models from
there.
"""

from __future__ import annotations

import argparse
import struct
import sys
from collections import Counter
from pathlib import Path

try:
    from unicorn import (Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN,
                         UC_HOOK_CODE, UC_HOOK_MEM_UNMAPPED, UC_HOOK_MEM_READ,
                         UC_HOOK_MEM_WRITE, UcError)
    from unicorn.mips_const import UC_MIPS_REG_PC
    from unicorn import mips_const
except ImportError:  # pragma: no cover - the environment carries Unicorn or it does not
    print("unicorn is required; run this with the venv that has it, e.g.\n"
          "  ../v90modem/.venv/bin/python tools/eicon_4bri_boot.py ...",
          file=sys.stderr)
    raise SystemExit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eicon_mips_image

CARD_RAM = 0x400000            # MQ_MEMORY_SIZE, kernel/mi_pc.h
RESET_VECTOR = 0x80000000      # the image's first instruction
PAGE = 0x1000

# The driver's header patches, recovered from the live card and recorded in
# docs/4bri_v1_firmware_replay.md.
PATCH_TASKS = (0x68, 0x04)     # four initial tasks
PATCH_CARDTYPE = (0x69, 0x16)  # card type 22

# The driver writes more than those two.  On the live card the words from 0x6c
# to 0x7c carry the protocol end address and three shared-memory pointers, and
# the image reads 0x6c at 0x800b534c to place its heap: without it the arena
# lands at 0x2000, on top of the image itself.  Copy the whole run from a
# snapshot rather than guess it.
HEADER_PATCH_RANGE = (0x68, 0x80)

# Shared RAM, and the one host configuration item the boot path reads out of
# it.  `diva_configure_protocol` builds a tag/length/value list at offset 224
# (PCINIT_OFFSET); the routine at 0x80060100 walks it for tag 0x34,
# PCINIT_DSP_IMAGE_LENGTH, and the heap is placed just past the protocol image
# plus that length.  Leave it zero and every allocation lands 0x94000 low --
# the size of the DSP code the driver stages but this harness does not.
SHARED_RAM = 0x1000
PCINIT_OFFSET = 224
PCINIT_DSP_IMAGE_LENGTH = 0x34

REGISTER_NAMES = (
    "zero", "at", "v0", "v1", "a0", "a1", "a2", "a3",
    "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
    "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7",
    "t8", "t9", "k0", "k1", "gp", "sp", "fp", "ra",
)


def unicorn_registers():
    return tuple(getattr(mips_const, f"UC_MIPS_REG_{name.upper()}")
                 for name in REGISTER_NAMES)


def _lui(rt: int, imm: int) -> int:
    return 0x3C000000 | (rt << 16) | (imm & 0xFFFF)


def _ori(rt: int, imm: int) -> int:
    return 0x34000000 | (rt << 21) | (rt << 16) | (imm & 0xFFFF)


def _mtc0(rt: int, rd: int, sel: int = 0) -> int:
    return 0x40800000 | (rt << 16) | (rd << 11) | sel


def _mfc0(rt: int, rd: int, sel: int = 0) -> int:
    return 0x40000000 | (rt << 16) | (rd << 11) | sel


TLBWI = 0x42000002

# CP0 register numbers used by the stub.
CP0_INDEX, CP0_ENTRYLO0, CP0_ENTRYLO1, CP0_PAGEMASK, CP0_ENTRYHI = 0, 2, 3, 5, 10
CP0_STATUS = 12
STATUS_ERL = 1 << 2

# 256 MiB pages, two per entry, so one entry covers 512 MiB.
PAGEMASK_256M = 0x1FFFE000
# Uncached, dirty, valid, global.
ENTRYLO_FLAGS = (2 << 3) | (1 << 2) | (1 << 1) | 1

# Every virtual window a MIPS translates through the TLB: useg below
# 0x80000000, and kseg2/kseg3 above 0xc0000000.  kseg0/kseg1 need no entry.
# The firmware's allocator hands out raw addresses like 0x00002000 and its
# registers live at 0xffffe200, so it needs both ends mapped -- on the card by
# a fixed mapping (or a permanently set Status.ERL), here by wired entries.
TLB_WINDOWS = (0x00000000, 0x20000000, 0x40000000, 0x60000000,
               0xC0000000, 0xE0000000)

# The image's own TLB init runs 16 invalidating `tlbwi`s at 0x8004429c and
# falls out of the loop here, executing uncached through kseg1.  This is the
# first instruction past the loop's delay slot -- stopping in the slot itself
# leaves the emulator mid-branch.  Anything we put in the TLB before this is
# gone afterwards, so the harness reinstalls it.
TLB_WIPE_END = 0xA00442BC

STUB_PHYSICAL = 0x00600000     # outside SDRAM; run it through kseg1
STUB_VIRTUAL = 0xA0600000


def entry_stub(windows: tuple[int, ...]) -> bytes:
    """Machine code that puts the CPU in the mode this firmware assumes.

    Two things, both of which the card gives the image for free and QEMU does
    not:

    `Status.ERL` is set.  The image's own boot code invalidates all 16 TLB
    entries at `0x80044298` and then allocates from a heap whose pointers are
    raw addresses like `0x00002040` -- useg, which a MIPS translates through
    the TLB it just emptied.  That only works with ERL set, which makes useg
    unmapped and uncached, and the image never clears it: the `mtc0` at
    `0x80044044` clears bit 0 (IE) and leaves the rest alone.

    Then one wired TLB entry per 512 MiB window, translating each address to
    itself.  ERL does not cover kseg2/kseg3, where the memory-controller
    registers at `0xffffe200` live, and these entries turn those accesses into
    plain physical ones -- which then miss in Unicorn instead, where a hook can
    map them on demand and log what the image wanted.  The image's TLB wipe
    clears them again a few hundred instructions later, which is harmless: by
    then the register writes are done.
    """
    code = [_mfc0(8, CP0_STATUS),
            _ori(8, STATUS_ERL),
            _mtc0(8, CP0_STATUS)]
    for index, base in enumerate(windows):
        even = (base >> 12) << 6 | ENTRYLO_FLAGS
        odd = ((base + 0x10000000) >> 12) << 6 | ENTRYLO_FLAGS
        for register, value in ((CP0_INDEX, index),
                                (CP0_PAGEMASK, PAGEMASK_256M),
                                (CP0_ENTRYHI, base),
                                (CP0_ENTRYLO0, even),
                                (CP0_ENTRYLO1, odd)):
            code.append(_lui(8, value >> 16))
            code.append(_ori(8, value))
            code.append(_mtc0(8, register))
        code.append(TLBWI)
    return b"".join(struct.pack("<I", word) for word in code)


class Card:
    """SDRAM plus whatever registers the image turns out to touch."""

    def __init__(self, image: bytes, patch: bool = True,
                 header: bytes | None = None, dsp_length: int = 0) -> None:
        ram = bytearray(image[:CARD_RAM].ljust(CARD_RAM, b"\0"))
        if patch:
            ram[PATCH_TASKS[0]] = PATCH_TASKS[1]
            ram[PATCH_CARDTYPE[0]] = PATCH_CARDTYPE[1]
        if header is not None:
            first, last = HEADER_PATCH_RANGE
            ram[first:last] = header[first:last]
        if dsp_length:
            at = SHARED_RAM + PCINIT_OFFSET
            ram[at] = PCINIT_DSP_IMAGE_LENGTH
            ram[at + 1] = 4
            ram[at + 2:at + 6] = struct.pack("<I", dsp_length)
        self.uc = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
        self.uc.mem_map(0, CARD_RAM)
        self.uc.mem_write(0, bytes(ram))
        self.mmio_pages: Counter[int] = Counter()
        self.mmio_log: list[tuple[str, int, int, int, int]] = []
        self.refused: list[tuple[int, int, int, str]] = []
        self.uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._on_unmapped)
        self._install_tlb()

    def _install_tlb(self, first: bool = True) -> None:
        """Put the CPU in the mode the image assumes."""
        stub = entry_stub(TLB_WINDOWS)
        if first:
            self.uc.mem_map(STUB_PHYSICAL, PAGE)
        self.uc.mem_write(STUB_PHYSICAL, stub)
        self.uc.emu_start(STUB_VIRTUAL, STUB_VIRTUAL + len(stub))

    def _on_unmapped(self, uc, access, address, size, value, user) -> bool:
        page = address & ~(PAGE - 1)
        try:
            uc.mem_map(page, PAGE)
        except UcError as exc:
            self.refused.append((uc.reg_read(UC_MIPS_REG_PC), address, size, str(exc)))
            return False               # already mapped, or not mappable here
        self.mmio_pages[page] += 1
        return True                    # retry the access against the new page

    def watch_mmio(self) -> None:
        """Log every access outside SDRAM, once the pages exist."""
        def note(kind):
            def hook(uc, access, address, size, value, user):
                if address >= CARD_RAM and not (0x80000000 <= address < 0x80000000 + CARD_RAM):
                    self.mmio_log.append((kind, uc.reg_read(UC_MIPS_REG_PC),
                                          address, size, value))
            return hook
        self.uc.hook_add(UC_HOOK_MEM_READ, note("read"))
        self.uc.hook_add(UC_HOOK_MEM_WRITE, note("write"))

    def registers(self) -> dict[str, int]:
        return {name: self.uc.reg_read(reg)
                for name, reg in zip(REGISTER_NAMES, unicorn_registers())}


INSTANCE_GLOBAL = 0x800442D4
INSTANCE_STRIDE = 0xD40
INSTANCE_FIELDS = ((0x6D0, "table"), (0x6D8, "params"),
                   (0x7F0, "current"), (0x7FC, "pool"))


def verify(card: Card, snapshot: bytes) -> bool:
    """Compare the booted machine's allocations against the real card's.

    The instance structures are the harness's own correctness check: they are
    allocated in a fixed order out of a heap whose base the driver decides, so
    if the emulated boot puts all four -- and their pools, tables and parameter
    blocks -- exactly where the card put them, the boot took the same path the
    card took.
    """
    def word(address: int) -> int:
        return struct.unpack("<I", card.uc.mem_read(address & 0x1FFFFFFF, 4))[0]

    def live(address: int) -> int:
        offset = address & 0x1FFFFFFF
        return struct.unpack("<I", snapshot[offset:offset + 4])[0]

    booted, real = word(INSTANCE_GLOBAL), live(INSTANCE_GLOBAL)
    print(f"\ninstance pointer: booted 0x{booted:08x}, card 0x{real:08x}"
          f"  {'match' if booted == real else 'DIFFER'}")
    if not booted or not real:
        return False
    agree = booted == real
    for index in range(4):
        instance = booted + index * INSTANCE_STRIDE
        parts = []
        for offset, name in INSTANCE_FIELDS:
            mine, theirs = word(instance + offset), live(instance + offset)
            agree &= mine == theirs
            parts.append(f"{name}={mine:08x}" + ("" if mine == theirs else f"!={theirs:08x}"))
        print(f"  instance {index} @0x{instance:08x}  " + "  ".join(parts))
    print("all instance allocations match the card" if agree
          else "allocations differ from the card")
    return agree


def boot(card: Card, steps: int, stop_at: int | None,
         trace_depth: int = 0) -> tuple[int, str | None, list[int]]:
    """Run from the reset vector.  Returns (final pc, error, tail of the trace)."""
    trace: list[int] = []
    if trace_depth:
        def record(uc, address, size, user):
            trace.append(address)
            if len(trace) > trace_depth:
                del trace[0]
        card.uc.hook_add(UC_HOOK_CODE, record)

    error = None
    try:
        # Leg 1: reset vector to the far side of the image's TLB wipe.
        card.uc.emu_start(RESET_VECTOR, TLB_WIPE_END, count=steps)
        if card.uc.reg_read(UC_MIPS_REG_PC) != TLB_WIPE_END:
            return card.uc.reg_read(UC_MIPS_REG_PC), "lost before the TLB wipe", trace
        card._install_tlb(first=False)
        # Leg 2: everything after it, now that useg is addressable again.
        card.uc.emu_start(TLB_WIPE_END, stop_at if stop_at is not None else 0,
                          count=steps)
    except UcError as exc:
        error = str(exc)
    return card.uc.reg_read(UC_MIPS_REG_PC), error, trace


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("firmware", type=Path, help="protocol image, e.g. te_dmlt.qm.107-136")
    parser.add_argument("--steps", type=int, default=1_000_000,
                        help="instruction budget (default 1e6)")
    parser.add_argument("--stop-at", type=lambda v: int(v, 0), default=None,
                        help="run until this address, e.g. 0x8009b340")
    parser.add_argument("--trace", type=int, default=16,
                        help="instructions of trailing trace to print (default 16)")
    parser.add_argument("--mmio", action="store_true",
                        help="print every access outside SDRAM")
    parser.add_argument("--no-patch", action="store_true",
                        help="skip the driver's header patches")
    parser.add_argument("--verify", type=Path, default=None,
                        help="BAR2 snapshot to check the booted allocations "
                             "against")
    parser.add_argument("--dsp-length", type=lambda v: int(v, 0), default=0,
                        help="DSP image length the driver would have staged, "
                             "published as PCINIT tag 0x34.  0x94000 puts the "
                             "heap where the live card has it")
    parser.add_argument("--driver-state", type=Path, default=None,
                        help="BAR2 snapshot to take the driver-written header "
                             "words 0x68..0x7f from, instead of the two "
                             "patches this harness knows how to synthesise")
    args = parser.parse_args()

    layout = eicon_mips_image.derive_layout(args.firmware)
    print(f"{args.firmware.name}: {layout.build}")
    print(f"  base 0x{layout.base:08x}  size 0x{layout.size:x}  "
          f"entry 0x{layout.entry:08x}  stack top 0x{layout.stack_top:08x}")

    header = args.driver_state.read_bytes() if args.driver_state else None
    if header is not None:
        first, last = HEADER_PATCH_RANGE
        print(f"  driver header 0x{first:02x}..0x{last:02x} from "
              f"{args.driver_state.name}: {header[first:last].hex()}")
    card = Card(args.firmware.read_bytes(), patch=not args.no_patch,
                header=header, dsp_length=args.dsp_length)
    if args.mmio:
        card.watch_mmio()

    pc, error, trace = boot(card, args.steps, args.stop_at, args.trace)

    print(f"\nstopped at pc = 0x{pc:08x}" + (f"  ({error})" if error else ""))
    if args.stop_at is not None and pc == args.stop_at:
        print("reached the requested address")
        registers = card.registers()
        for row in range(0, 32, 4):
            print("  " + "  ".join(
                f"{name:>4}=0x{registers[name]:08x}"
                for name in REGISTER_NAMES[row:row + 4]))
    if trace:
        print("\nlast instructions:")
        print("  " + " ".join(f"{address:08x}" for address in trace))
    if card.refused:
        print("\naccesses that could not be satisfied:")
        for pc_at, address, size, why in card.refused[:10]:
            print(f"  {pc_at:08x}  {size} bytes @ 0x{address:08x}  ({why})")
    if args.verify is not None:
        verify(card, args.verify.read_bytes())
    if card.mmio_pages:
        print("\nregister pages the image touched (address: times mapped):")
        for page, count in sorted(card.mmio_pages.items()):
            print(f"  0x{page:08x}  {count}")
    if args.mmio and card.mmio_log:
        print("\naccesses outside SDRAM:")
        for kind, pc_at, address, size, value in card.mmio_log[:200]:
            detail = f" = 0x{value:x}" if kind == "write" else ""
            print(f"  {pc_at:08x}  {kind:5} {size} @ 0x{address:08x}{detail}")
        if len(card.mmio_log) > 200:
            print(f"  ... {len(card.mmio_log) - 200} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
