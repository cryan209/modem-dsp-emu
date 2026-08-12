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
from dataclasses import dataclass
from pathlib import Path

try:
    from unicorn import (Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN,
                         UC_HOOK_CODE, UC_HOOK_MEM_UNMAPPED, UC_HOOK_MEM_READ,
                         UC_HOOK_MEM_WRITE, UcError)
    from unicorn.mips_const import (UC_MIPS_REG_PC, UC_MIPS_REG_RA,
                                    UC_MIPS_REG_V0)
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
# Register windows the image is known to touch, pre-mapped so ordinary read and
# write hooks see the accesses.  A page mapped from inside the unmapped-access
# hook satisfies the faulting access but does not report it, so discovery and
# logging need both mechanisms.
DEVICE_WINDOWS = (0x1F800000, 0x1FA00000, 0xFFFFE000)

# The ADSP host port, as 0x800b61d0 and 0x800b6200 drive it: write the DSP
# address to +8, then read or write the data at +0.  kernel/mi_pc.h puts DSP 1
# at 0x0000/0x0008 and DSP 2 at 0x0200/0x0208 within each subboard.
DSP_DATA_OFFSET = 0x0
DSP_ADDR_OFFSET = 0x8
DSP_PORT_MASK = 0xF
# What a live ADSP answers with once it is out of reset.  The probe at
# 0x800baf50 polls up to 999 times for it and reports "DSP test failed"
# otherwise, which is what makes hardware initialisation fail.
DSP_ALIVE = 0xA5A5
# The presence test at 0x800bad50 is a plain echo: write 0x5a5a to DSP address
# 0x4000 and read it back, then the same with 0xa5a5.  The liveness test at
# 0x800baf50 writes a command instead and polls the same location for the
# DSP's own acknowledgement.  Echoing the one probe pattern and acknowledging
# everything else satisfies both.
DSP_ECHO = 0x5A5A

SHARED_RAM = 0x1000
PCINIT_OFFSET = 224
PCINIT_DSP_IMAGE_LENGTH = 0x34

# kernel/pr_pc.h.  `struct pr_ram` sits at the base of the adapter's shared
# memory -- the card publishes 0x4447 ("GD") at +0x1e when it is ready, which
# is the signature `idi_diva_4bri_start_adapter` waits three seconds for.
PR_RAM = SHARED_RAM
PR_NEXT_REQ = 0x00            # word: offset into B[] of the next free buffer
PR_REQ_INPUT = 0x06           # byte: request buffers the host has sent
PR_REQ_OUTPUT = 0x07          # byte: request buffers the card has returned
PR_NEXT_RC = 0x02             # word
PR_RC_OUTPUT = 0x0B           # byte: return codes waiting for the host
PR_SIGNATURE = 0x1E           # word
PR_BUFFERS = 0x20             # B[], where the REQ/RC/IND buffers live

# `struct _IDI_REQ`, same header.
REQ_NEXT = 0x00               # word
REQ_REQ = 0x02                # byte: the request code
REQ_ID = 0x03                 # byte: entity id
REQ_CH = 0x04                 # byte
REQ_REFERENCE = 0x06          # word, written by the card
REQ_XBUFFER = 0x10            # word length, then 270 bytes of parameters

# `struct _IDI_RC`.
RC_NEXT = 0x00                # word
RC_RC = 0x02                  # byte: the return code
RC_ID = 0x03                  # byte: the id the card assigned
RC_CH = 0x04                  # byte

ASSIGN = 0x01                 # pc.h: the same code for every entity
ASSIGN_OK = 0xEF              # anything else with the top nibble 0xe is a
ASSIGN_RC = 0xE0              # rejection carrying its own reason

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
CP0_COUNT = 9
CP0_COMPARE = 11
CP0_STATUS = 12
CP0_CAUSE = 13
CP0_EPC = 14
STATUS_ERL = 1 << 2
STATUS_EXL = 1 << 1

# The image installs its exception vectors at run time: 0x80000200 and
# 0x80000380 both jump to 0x800442e0, distinguished by the k1 the vector loads.
# The dispatcher at 0x800b4e68 reads that back as the frame's class byte and
# treats anything non-zero as fatal, so an interrupt has to arrive through the
# k1 = 0 vector at 0x80000380.  The live trap's class of 0x00000101 is the
# other one -- k1 = 1, entered at 0x80000200.
INTERRUPT_VECTOR = 0x80000380
# Cause with IP7 set and ExcCode 0: the CP0 timer, which is what the image arms
# with `mtc0 a0, $11` at 0x80044234.
CAUSE_TIMER = 1 << 15

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
                 header: bytes | None = None, dsp_length: int = 0,
                 dsp_code: "DspCode | None" = None) -> None:
        ram = bytearray(image[:CARD_RAM].ljust(CARD_RAM, b"\0"))
        if patch:
            ram[PATCH_TASKS[0]] = PATCH_TASKS[1]
            ram[PATCH_CARDTYPE[0]] = PATCH_CARDTYPE[1]
        if header is not None:
            first, last = HEADER_PATCH_RANGE
            ram[first:last] = header[first:last]
        if dsp_code is not None:
            # Where the driver stages the DSP download table, and the length
            # the image needs published in order to place its heap past it.
            first = dsp_code.base & 0x1FFFFFFF
            ram[first:first + len(dsp_code.data)] = dsp_code.data
            dsp_length = len(dsp_code.data)
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
        self.stubbed: list[tuple[int, int]] = []
        self.ticks = 0
        self.posted: list[tuple[int, int]] = []
        self.returned: list[tuple[int, int, int]] = []
        self.null_reads: list[tuple[int, int, dict]] = []
        for window in DEVICE_WINDOWS:
            self.uc.mem_map(window, PAGE)
        self.uc.mem_map(BOOT_ROM, PAGE)
        for address, k1 in BOOT_ROM_VECTORS:
            self.uc.mem_write(address, vector_stub(k1))
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

    def interrupt(self, resume_pc: int) -> None:
        """Take a timer interrupt at `resume_pc`.

        Unicorn does not run the CP0 timer, so `Count` never reaches the
        `Compare` the image arms and the interrupt it is waiting for never
        arrives.  Rather than fake a clock, hand it the interrupt directly:
        park the interrupted pc in EPC, put a timer cause in Cause, raise EXL
        and enter the vector, which is what the hardware would have done.
        `k0`/`k1` are clobbered, which is what they are for.
        """
        code = []
        for value, register in ((resume_pc, CP0_EPC), (CAUSE_TIMER, CP0_CAUSE)):
            code += [_lui(26, value >> 16), _ori(26, value), _mtc0(26, register)]
        code += [_mfc0(26, CP0_STATUS), _ori(26, STATUS_EXL), _mtc0(26, CP0_STATUS)]
        # Unicorn does not run Count either, and a timer service that reads it
        # to decide what has expired would see a clock stopped at zero.  Push
        # Count just past whatever Compare the image last armed, so one
        # injected interrupt is worth exactly one of its own timer periods and
        # the rate is the image's own rather than a number invented here.
        code += [_mfc0(26, CP0_COMPARE), 0x275A0001, _mtc0(26, CP0_COUNT)]
        blob = b"".join(struct.pack("<I", word) for word in code)
        self.uc.mem_write(STUB_PHYSICAL + 0x800, blob)
        self.uc.emu_start(STUB_VIRTUAL + 0x800, STUB_VIRTUAL + 0x800 + len(blob))

    def attach_dsps(self, library: Path, cycles: int = 2000) -> None:
        """Put a real ADSP-2181 behind each DSP host port.

        The port is the chip's own host interface: `0x800b61d0` writes a DSP
        address to `+8` and reads data at `+0`, and the address's bit 14
        selects DM over PM -- which is exactly what `adsp2181_host_read` and
        `adsp2181_host_write` take.  So this is not a model of the port, it is
        the port, with `tools/adsp2181emu` on the other side of it.

        The core runs `cycles` instructions after each host access, which is
        what gives the downloaded kernel time to answer the MIPS between
        polls.
        """
        import ctypes

        lib = ctypes.CDLL(str(library))
        lib.adsp2181_create.restype = ctypes.c_void_p
        lib.adsp2181_reset.argtypes = [ctypes.c_void_p]
        lib.adsp2181_host_write.argtypes = [ctypes.c_void_p, ctypes.c_uint16,
                                            ctypes.c_uint16]
        lib.adsp2181_host_read.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
        lib.adsp2181_host_read.restype = ctypes.c_uint16
        lib.adsp2181_run.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.adsp2181_pc.argtypes = [ctypes.c_void_p]
        lib.adsp2181_pc.restype = ctypes.c_uint16

        self.adsp_lib = lib
        self.adsp_cores: dict[int, int] = {}
        self.dsp_latched = {}

        def core_for(port: int) -> int:
            core = self.adsp_cores.get(port)
            if core is None:
                core = lib.adsp2181_create()
                lib.adsp2181_reset(core)
                self.adsp_cores[port] = core
            return core

        def on_write(uc, access, address, size, value, user):
            port, offset = address & ~DSP_PORT_MASK, address & DSP_PORT_MASK
            if offset == DSP_ADDR_OFFSET:
                self.dsp_latched[port] = value & 0xFFFF
            elif offset == DSP_DATA_OFFSET:
                core = core_for(port)
                lib.adsp2181_host_write(core, self.dsp_latched.get(port, 0),
                                        value & 0xFFFF)

        def on_read(uc, access, address, size, user_value, user):
            port, offset = address & ~DSP_PORT_MASK, address & DSP_PORT_MASK
            if offset != DSP_DATA_OFFSET:
                return
            core = core_for(port)
            lib.adsp2181_run(core, cycles)
            answer = lib.adsp2181_host_read(core, self.dsp_latched.get(port, 0))
            uc.mem_write(address, struct.pack("<H", answer))

        for window in (0x1F800000, 0x1FA00000):
            self.uc.hook_add(UC_HOOK_MEM_WRITE, on_write,
                             begin=window, end=window + PAGE - 1)
            self.uc.hook_add(UC_HOOK_MEM_READ, on_read,
                             begin=window, end=window + PAGE - 1)

    def model_dsps(self) -> None:
        """Answer the DSP boot handshake.

        Not an ADSP -- a port that behaves like a DSP which is answering.  The
        address register latches, and DSP address 0x4000 behaves as the
        command/answer mailbox it is: the presence test writes a probe pattern
        and reads it back, while the liveness test writes a command (0x3e8,
        0x3e9, ...) and polls the same location for the DSP's reply.  A model
        that echoes everything fails the second with "download not running";
        one that answers everything fails the first with "No DSP present".

        This is enough for the probe to stop polling.  It is not a DSP and
        cannot run downloaded code, so anything that needs the download to
        execute will still fail -- visibly, in the card's own log.
        """
        self.dsp_memory: dict[tuple[int, int], int] = {}
        self.dsp_latched: dict[int, int] = {}

        def on_write(uc, access, address, size, value, user):
            port, offset = address & ~DSP_PORT_MASK, address & DSP_PORT_MASK
            if offset == DSP_ADDR_OFFSET:
                self.dsp_latched[port] = value & 0xFFFF
            elif offset == DSP_DATA_OFFSET:
                self.dsp_memory[(port, self.dsp_latched.get(port, 0))] = value & 0xFFFF

        def on_read(uc, access, address, size, user_value, user):
            port, offset = address & ~DSP_PORT_MASK, address & DSP_PORT_MASK
            if offset != DSP_DATA_OFFSET:
                return
            key = (port, self.dsp_latched.get(port, 0))
            written = self.dsp_memory.get(key)
            answer = DSP_ECHO if written == DSP_ECHO else DSP_ALIVE
            uc.mem_write(address, struct.pack("<H", answer))

        # Only the DSP windows.  The memory controller at 0xffffe000 is also
        # outside SDRAM and its reads mean something entirely different.
        for window in (0x1F800000, 0x1FA00000):
            self.uc.hook_add(UC_HOOK_MEM_WRITE, on_write,
                             begin=window, end=window + PAGE - 1)
            self.uc.hook_add(UC_HOOK_MEM_READ, on_read,
                             begin=window, end=window + PAGE - 1)

    def byte(self, address: int) -> int:
        return self.uc.mem_read(address & 0x1FFFFFFF, 1)[0]

    def half(self, address: int) -> int:
        return struct.unpack("<H", self.uc.mem_read(address & 0x1FFFFFFF, 2))[0]

    def write_byte(self, address: int, value: int) -> None:
        self.uc.mem_write(address & 0x1FFFFFFF, bytes((value & 0xFF,)))

    def write_half(self, address: int, value: int) -> None:
        self.uc.mem_write(address & 0x1FFFFFFF, struct.pack("<H", value & 0xFFFF))

    def ready(self) -> bool:
        """Has the card published its signature yet?"""
        return self.half(PR_RAM + PR_SIGNATURE) == 0x4447

    def free_requests(self) -> int:
        """`pr_ready` from di.c: ReqOutput - ReqInput, modulo a byte."""
        return (self.byte(PR_RAM + PR_REQ_OUTPUT)
                - self.byte(PR_RAM + PR_REQ_INPUT)) & 0xFF

    def request(self, entity: int, code: int = ASSIGN,
                channel: int = 0, parameters: bytes = b"") -> bool:
        """Post one request the way `pr_out` does.

        Take the buffer at `NextReq`, fill it in, relink `NextReq` from the
        buffer's own `next`, and bump `ReqInput`.  There is no doorbell in the
        driver's request path -- `ReqInput` moving is the signal, and the card
        picks it up in its own time.
        """
        if not self.free_requests():
            return False
        buffer = PR_RAM + PR_BUFFERS + self.half(PR_RAM + PR_NEXT_REQ)
        self.write_half(buffer + REQ_XBUFFER, len(parameters))
        if parameters:
            self.uc.mem_write((buffer + REQ_XBUFFER + 2) & 0x1FFFFFFF, parameters)
        self.write_byte(buffer + REQ_ID, entity)
        self.write_byte(buffer + REQ_CH, channel)
        self.write_byte(buffer + REQ_REQ, code)
        self.write_half(PR_RAM + PR_NEXT_REQ, self.half(buffer + REQ_NEXT))
        self.write_byte(PR_RAM + PR_REQ_INPUT,
                        (self.byte(PR_RAM + PR_REQ_INPUT) + 1) & 0xFF)
        self.posted.append((entity, code))
        return True

    def collect(self) -> list[tuple[int, int, int]]:
        """Drain the return-code queue the way `pr_dpc` does.

        Without this the card fills its Rc ring and stops answering, and --
        more to the point -- an ASSIGN that the card *rejected* looks exactly
        like one it accepted.  Returns (Rc, RcId, RcCh) per entry.
        """
        waiting = self.byte(PR_RAM + PR_RC_OUTPUT)
        if not waiting:
            return []
        codes = []
        buffer = PR_RAM + PR_BUFFERS + self.half(PR_RAM + PR_NEXT_RC)
        for _ in range(waiting):
            code = self.byte(buffer + RC_RC)
            if code:
                codes.append((code, self.byte(buffer + RC_ID),
                              self.byte(buffer + RC_CH)))
                self.write_byte(buffer + RC_RC, 0)
            buffer = PR_RAM + PR_BUFFERS + self.half(buffer + RC_NEXT)
        self.write_byte(PR_RAM + PR_RC_OUTPUT, 0)
        self.returned.extend(codes)
        return codes

    def watch_null(self, bound: int) -> None:
        """Stop on a data load below `bound`, the way the card's DBOUND does.

        The hardware trap is exception code 2, which this core reports as
        TLB-load *or* a bounds violation -- and since it runs with an empty TLB
        and a fixed mapping, a load of `0xb8` is not a translation failure at
        all.  It is a bounds register catching a null-pointer dereference.

        This machine has no such register: identity-mapped useg makes the exact
        faulting instruction, with the exact faulting operand, read a zero out
        of the image and carry on.  Without this hook the emulated card cannot
        reproduce the fault even if it reaches it.

        An address window alone is not enough to tell a null dereference from
        ordinary data: the image reads its own `DspCodeBaseAddr` at 0x6c from
        `0x800b6adc`, and its protocol banner at 0x80 -- which runs to 0xb6,
        so the `+0xb8` the card faults on is the very next word.  So the test
        is the actual condition rather than the address: decode the load at the
        faulting pc and check that its *base register holds zero*.  That is a
        null pointer, whatever the offset.
        """
        registers = unicorn_registers()

        def on_low_read(uc, access, address, size, value, user):
            at = uc.reg_read(UC_MIPS_REG_PC)
            try:
                word = struct.unpack("<I", uc.mem_read(at & 0x1FFFFFFF, 4))[0]
            except UcError:
                return
            base = (word >> 21) & 0x1F
            if uc.reg_read(registers[base]) != 0:
                return                    # a real object, low in memory
            self.null_reads.append((at, address, self.registers()))
            uc.emu_stop()
        self.uc.hook_add(UC_HOOK_MEM_READ, on_low_read,
                         begin=NULL_WINDOW_START, end=bound - 1)

    def stub(self, address: int, result: int = 0) -> None:
        """Return `result` from `address` without running the function.

        For the parts of the card this harness does not model.  The one that
        matters is `0x800b5e48`, hardware initialisation, which drives the
        ISAC and the eight ADSP cores through register pointers and reports
        "Hardware Initialisation failed" against a machine that has none of
        them.  Stubbing it is an approximation and is worth stating as one --
        check what the boot produces against a snapshot afterwards rather than
        assuming the rest of the path is unaffected.
        """
        def jump_to_ra(uc, at, size, user):
            uc.reg_write(UC_MIPS_REG_V0, result)
            uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))
        self.uc.hook_add(UC_HOOK_CODE, jump_to_ra, begin=address, end=address)
        self.stubbed.append((address, result))

    def registers(self) -> dict[str, int]:
        return {name: self.uc.reg_read(reg)
                for name, reg in zip(REGISTER_NAMES, unicorn_registers())}


# kernel/mi_pc.h names the header words the driver writes.  0x6c is the DSP
# code base address, which the driver sets to the end of the protocol image --
# the image adds the DSP length to it to place its heap.  The three after it
# are the card's own debug log.
OFFS_DSP_CODE_BASE_ADDR = 0x6C
OFFS_XLOG_BUF_ADDR = 0x70
OFFS_XLOG_COUNT_ADDR = 0x74
OFFS_XLOG_OUT_ADDR = 0x78

XLOG_HEADER = 8               # word timestamp, then flags and a code
CARD_TYPE = 22                # CARDTYPE_DIVASRV_Q_8M_PCI, this card

# Above the image header, below the exception vectors it installs at 0x180.
NULL_WINDOW_START = 0x80


@dataclass(frozen=True)
class DspCode:
    """A staged DSP download table and where it goes in card RAM."""
    base: int
    data: bytes



def xlog(card: "Card", limit: int = 200) -> list[tuple[int, str]]:
    """Read the card's own log out of the buffer the driver pointed it at.

    This is the same text `/var/log/diva1.log` carries, produced by the
    emulated card rather than the hardware, which makes it the most direct
    check there is that a boot did what the card does.  Entries are a 2-byte
    timestamp in milliseconds, six more header bytes, a NUL-terminated string,
    and padding to the next even address.
    """
    def word(address: int) -> int:
        return struct.unpack("<I", card.uc.mem_read(address & 0x1FFFFFFF, 4))[0]

    buffer = word(OFFS_XLOG_BUF_ADDR)
    written = word(word(OFFS_XLOG_COUNT_ADDR))
    if not buffer or not written:
        return []
    blob = bytes(card.uc.mem_read(buffer & 0x1FFFFFFF, 0x4000))
    entries = []
    at = 0
    while at + XLOG_HEADER < len(blob) and len(entries) < limit:
        timestamp = struct.unpack_from("<H", blob, at)[0]
        end = blob.find(b"\0", at + XLOG_HEADER)
        if end < 0:
            break
        text = blob[at + XLOG_HEADER:end]
        if not text:
            break
        entries.append((timestamp, text.decode("latin-1")))
        at = end + 1
        at += at & 1
    return entries


# The image installs its exception handlers at 0x80000200 and 0x80000380, the
# BEV=0 vectors -- but it runs with Status.BEV set (0x1040ec01, and the live
# trap frame's 0x1040ec03 agrees), so the hardware vectors through the boot ROM
# at 0xbfc00200/0xbfc00380 instead.  That ROM is not in any dump we have, and a
# real exception lands in a zero page and dies as a reserved instruction a few
# hundred nops later.  Mirroring the image's own stubs there is what the ROM
# must be doing.
# The stub is the image's own, byte for byte: load the handler address, jump to
# it, and leave the vector's identity in k1 -- 1 for the general vector, 0 for
# the interrupt one, which the dispatcher at 0x800b4e68 reads back as the
# frame's class.
BOOT_ROM = 0x1FC00000
BOOT_ROM_VECTORS = ((0x1FC00200, 1), (0x1FC00380, 0))
EXCEPTION_HANDLER = 0x800442E0


def vector_stub(k1: int) -> bytes:
    return b"".join(struct.pack("<I", word) for word in (
        _lui(27, EXCEPTION_HANDLER >> 16),
        0x27000000 | (27 << 21) | (27 << 16) | (EXCEPTION_HANDLER & 0xFFFF),
        0x03600008,                       # jr k1
        0x241B0000 | (k1 & 0xFFFF)))      # addiu k1, zero, n  (delay slot)


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

    # The global holds the *current* instance, which the scheduler moves; every
    # instance carries instance 0's address in its own word 0, so compare the
    # bases rather than whichever one happened to be running.
    booted, real = word(INSTANCE_GLOBAL), live(INSTANCE_GLOBAL)
    print(f"\ncurrent instance: booted 0x{booted:08x}, card 0x{real:08x}")
    if not booted or not real:
        return False
    booted, real = word(booted), live(real)
    print(f"instance 0:       booted 0x{booted:08x}, card 0x{real:08x}"
          f"  {'match' if booted == real else 'DIFFER'}")
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
         trace_depth: int = 0, ticks: int = 0,
         requests: tuple[tuple[int, int, bytes], ...] = (),
         dbound: int = 0) -> tuple[int, str | None, list[int]]:
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
        target = stop_at if stop_at is not None else 0
        card.uc.emu_start(TLB_WIPE_END, target, count=steps)
        if dbound:
            card.watch_null(dbound)
        # Leg 3: the image settles into its scheduler waiting for a timer that
        # this machine has no clock for.  Deliver them by hand, one per slice,
        # until the target is reached or the ticks run out.  Host requests wait
        # for the card to publish its signature, which it only does once the
        # clock is running -- posting before that writes into a queue the card
        # has not set up.
        pending = list(requests)
        for _ in range(ticks):
            pc = card.uc.reg_read(UC_MIPS_REG_PC)
            if stop_at is not None and pc == stop_at:
                break
            card.collect()
            while pending and card.ready() and card.free_requests():
                entity, code, payload = pending.pop(0)
                card.request(entity, code, parameters=payload)
            card.interrupt(pc)
            card.ticks += 1
            card.uc.emu_start(INTERRUPT_VECTOR, target, count=steps)
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
    parser.add_argument("--assign", action="append", default=[],
                        metavar="ID[=KIND]",
                        help="post an ASSIGN for this entity id once the card "
                             "is up, e.g. 0xe0 for MAN_ID or 0 for DSIG_ID.  "
                             "KIND=sig attaches the CAI and user id that "
                             "add_b1() sends, instead of a bare ASSIGN")
    parser.add_argument("--xlog", action="store_true",
                        help="print the card's own log after the run")
    parser.add_argument("--watch", action="append", default=[], metavar="ADDR",
                        help="count entries to ADDR during the run, e.g. "
                             "0x8009b2a0 for the trapping function")
    parser.add_argument("--dbound", type=lambda v: int(v, 0), default=0,
                        help="stop on a data load between 0x80 and this "
                             "address once the card is up, standing in for the "
                             "bounds register that makes the hardware trap.  "
                             "0x180 covers the 0xb8 the card faults on")
    parser.add_argument("--ticks", type=int, default=0,
                        help="timer interrupts to deliver once the image is "
                             "running, for faults that need the clock")
    parser.add_argument("--dsp-image", type=Path, default=None,
                        metavar="COMBIFILE",
                        help="stage the DSP download the driver would stage, "
                             "e.g. docs/firmware/dspdload.bin.108-744.  Sets "
                             "the DSP length from the image rather than taking "
                             "it on trust")
    parser.add_argument("--dsp", action="store_true",
                        help="answer the DSP boot handshake with a stand-in, "
                             "so hardware initialisation runs instead of being "
                             "stubbed.  Cannot execute downloaded DSP code")
    parser.add_argument("--adsp", type=Path, nargs="?",
                        const=Path(__file__).resolve().parent / "adsp2181emu"
                            / "libadsp2181.dylib",
                        default=None,
                        help="put a real ADSP-2181 from tools/adsp2181emu "
                             "behind each DSP host port")
    parser.add_argument("--stub", action="append", default=[],
                        metavar="ADDR[=RESULT]",
                        help="return RESULT (default 0) from ADDR without "
                             "running it, e.g. 0x800b5e48 for hardware init")
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
    dsp_code = None
    if args.dsp_image is not None:
        import eicon_dsp_stage
        base = struct.unpack("<I", args.firmware.read_bytes()[
            OFFS_DSP_CODE_BASE_ADDR:OFFS_DSP_CODE_BASE_ADDR + 4])[0]
        if header is not None:
            base = struct.unpack("<I", header[
                OFFS_DSP_CODE_BASE_ADDR:OFFS_DSP_CODE_BASE_ADDR + 4])[0]
        staged = eicon_dsp_stage.build_dsp_code_image(
            args.dsp_image, CARD_TYPE, base)
        dsp_code = DspCode(base, staged.data)
        print(f"  DSP code: {len(staged.downloads)} downloads, "
              f"{len(staged.data)} bytes at 0x{base:08x} "
              f"(file set {staged.file_set})")
    card = Card(args.firmware.read_bytes(), patch=not args.no_patch,
                header=header, dsp_length=args.dsp_length, dsp_code=dsp_code)
    watched = {int(spec, 0): 0 for spec in args.watch}
    if watched:
        def count(uc, address, size, user):
            watched[address] += 1
        for address in watched:
            card.uc.hook_add(UC_HOOK_CODE, count, begin=address, end=address)
    for spec in args.stub:
        address, _, result = spec.partition("=")
        card.stub(int(address, 0), int(result, 0) if result else 0)
    if card.stubbed:
        print("  stubbed: " + ", ".join(f"0x{a:08x}->{r}" for a, r in card.stubbed))
    if args.adsp is not None:
        card.attach_dsps(args.adsp)
    elif args.dsp:
        card.model_dsps()
    if args.mmio:
        card.watch_mmio()

    requests = []
    for spec in args.assign:
        entity, _, kind = spec.partition("=")
        payload = b""
        if kind == "sig":
            # tools/eicon_idi.py builds this from divas4linux's own add_b1(),
            # so the ASSIGN carries what the driver's carries rather than
            # nothing.  A bare ASSIGN creates the entity and configures it with
            # no B1 descriptor at all.
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import eicon_idi
            payload = eicon_idi.sig_assign_payload()
        elif kind:
            parser.error(f"unknown assign kind {kind!r}")
        requests.append((int(entity, 0), ASSIGN, payload))
    requests = tuple(requests)
    pc, error, trace = boot(card, args.steps, args.stop_at, args.trace,
                            args.ticks, requests, args.dbound)
    card.collect()
    if card.returned:
        print("\nreturn codes from the card:")
        for code, entity, channel in card.returned:
            verdict = ("ASSIGN_OK" if code == ASSIGN_OK else
                       "ASSIGN rejected" if code & 0xF0 == ASSIGN_RC else "")
            print(f"  Rc=0x{code:02x} Id=0x{entity:02x} Ch=0x{channel:02x}"
                  + (f"  {verdict}" if verdict else ""))
    print(f"\ncard signature published: {card.ready()}, "
          f"{card.free_requests()} request buffer(s) free, "
          f"{len(card.posted)} posted")

    if card.ticks:
        print(f"\ndelivered {card.ticks} timer interrupt(s)")
    print(f"stopped at pc = 0x{pc:08x}" + (f"  ({error})" if error else ""))
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
    if card.null_reads:
        print("\nnull-pointer reads caught by the bounds check:")
        for at, address, registers in card.null_reads:
            print(f"  pc=0x{at:08x} read 0x{address:08x}")
            print("    " + "  ".join(f"{name}=0x{registers[name]:08x}"
                                     for name in ("s0", "s1", "a0", "a1", "ra")))
    if args.xlog:
        entries = xlog(card)
        print(f"\ncard log ({len(entries)} entries):")
        for timestamp, text in entries:
            print(f"  {timestamp // 1000}:{timestamp % 1000:03d}  {text}")
    if watched:
        print("\nwatched addresses:")
        for address, hits in watched.items():
            print(f"  0x{address:08x}: {hits} entries")
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
