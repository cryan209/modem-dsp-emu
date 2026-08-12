#!/usr/bin/env python3
"""Find the object the 4BRI-v1 null-pointer trap was dereferencing.

The `MP_XCPTC` frame does not carry it.  `s0` is recorded as `0x00000001`,
which cannot be right -- the caller had just executed `lw a0, 12(s0)` without
faulting -- so the pointer that reached `0x80063f68` with a null statistics
block has to be recovered some other way.

The idea was to use the frozen SDRAM.  The trap halts the MIPS, so the BAR2
dump is the machine state at the moment of the fault; replaying the trapping
function against it with a candidate `s0` should either reproduce the fault or
not.

    0x8009b2a0   the trapping function; takes the object in a0
    0x8009b184   called first, unconditionally
    0x8009b330   jalr through the state table at 0x8011d490
    0x8009b338   lw a0, 12(s0)      <- the null
    0x8009b340   jal 0x80063f68     <- run until here, then read a0

Candidates are every 4-aligned address whose `+80` has bit 0x20000 set (the
function returns immediately otherwise) and whose `+12` is null.  Each run
starts with `sp` at the caller's real stack position, so the frame lands where
it landed on the card, and gets a fresh machine so one run's writes cannot
colour the next.

**It does not work, and the tool exists to record why.**  On both trap
snapshots not one of ~7100 candidates reaches even the state dispatch: they
are all lost inside `0x8009b184`, the call the function makes before it.  The
snapshot is the state *after* that call ran, and it is not idempotent -- a
queue it drained is empty, a state byte it advanced has moved -- so replaying
it a second time diverges no matter which pointer is passed.  The method
cannot confirm the real object either, and a null result here is not evidence
that the object is absent.

Recovering the pointer needs a cold boot, where the firmware creates the
object itself.  Keep this as the record of a closed avenue, and as a harness
for any replay that starts from a state the code has not already consumed.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
from pathlib import Path

try:
    from unicorn import (Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN,
                         UC_HOOK_CODE, UC_HOOK_MEM_INVALID, UcError)
    from unicorn.mips_const import (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_V0,
                                    UC_MIPS_REG_SP, UC_MIPS_REG_RA, UC_MIPS_REG_PC)
except ImportError:  # pragma: no cover - the environment carries Unicorn or it does not
    print("unicorn is required; run this with the venv that has it, e.g.\n"
          "  ../v90modem/.venv/bin/python tools/eicon_4bri_find_object.py ...",
          file=sys.stderr)
    raise SystemExit(2)

KSEG0 = 0x80000000
CARD_RAM = 0x400000            # MQ_MEMORY_SIZE, kernel/mi_pc.h: all of the
                               # card's SDRAM, so a 4 MiB dump is the whole of it
HEAP_START = 0x80131000        # first page past the protocol image (ends 0x80130370)

TRAP_FUNCTION = 0x8009B2A0     # takes the object in a0
DISPATCH_CALL = 0x8009B330     # jalr through the state table at 0x8011d490
AFTER_DISPATCH = 0x8009B338    # lw a0, 12(s0), the instruction after it
COUNTER_CALL = 0x8009B340      # jal 0x80063f68, the instruction we run up to
CALLER_SP = 0x80134288         # trapped sp + 40, i.e. sp before the prologue
RETURN_MAGIC = 0x7F000000      # unmapped-on-purpose: an early return lands here

FLAGS_OFFSET = 80
FLAGS_REQUIRED = 0x00020000    # 0x8009b2b4..0x8009b2c0 returns unless this is set
STATS_OFFSET = 12
OBJECT_SIZE = 1364

# The frame preserves these two, and they agree with each other and with `ra`:
# the delay slot at 0x8009b344 copies v0 into s1, so a faithful replay of the
# dispatched handler reproduces both.
FRAME_V0 = 0x8009B340


def replay_isolated(image: bytes, candidate: int, steps: int,
                    skip_dispatch: bool = False) -> tuple[bool, bool, int, int, int]:
    """`replay` in a child process, so a hostile candidate cannot take us down.

    Most candidates are data that merely happens to match the filter, and
    dispatching on a garbage state byte runs the emulator into code it was
    never meant to execute.  Unicorn survives nearly all of that as a clean
    `UcError`, but not all of it -- some candidates segfault the process.  A
    fork per candidate turns that into a miss instead of the end of the scan.
    """
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:                                        # child
        os.close(read_fd)
        try:
            result = replay(image, candidate, steps, skip_dispatch)
            payload = struct.pack("<5I", int(result[0]), int(result[1]), *result[2:])
            os.write(write_fd, payload)
        except BaseException:
            pass
        finally:
            os._exit(0)
    os.close(write_fd)
    payload = b""
    while len(payload) < 20:
        chunk = os.read(read_fd, 20 - len(payload))
        if not chunk:
            break
        payload += chunk
    os.close(read_fd)
    os.waitpid(pid, 0)
    if len(payload) < 20:
        return (False, False, 0, 0, 0)
    dispatch, reached, a0, a1, v0 = struct.unpack("<5I", payload)
    return (bool(dispatch), bool(reached), a0, a1, v0)


def replay(image: bytes, candidate: int, steps: int,
           skip_dispatch: bool = False) -> tuple[bool, bool, int, int, int]:
    """Replay the trapping function with `a0 = candidate`.

    Returns (reached the dispatch, reached the counter call, a0, a1, v0).  A fresh machine per
    candidate: the dispatched state handler writes to memory, and one run's
    writes must not colour the next.  Physical memory is mapped at 0 --
    Unicorn's MIPS core translates kseg0 and kseg1 itself, so this answers both
    the `0x80xxxxxx` addresses the firmware executes from and the `0xa0xxxxxx`
    aliases it uses for shared memory.

    `skip_dispatch` steps over the `jalr` at 0x8009b330.  The snapshot is the
    state *after* that handler ran, so replaying it is not idempotent -- a
    handler that consumed a queue or advanced a state byte will not take the
    same path a second time, and the real object can fail the replay for that
    reason alone.  Skipping it tests only the path the function takes before
    the dispatch, which is idempotent.
    """
    uc = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    uc.mem_map(0, CARD_RAM)
    uc.mem_write(0, image)
    uc.hook_add(UC_HOOK_MEM_INVALID, lambda *_: False)
    if skip_dispatch:
        uc.hook_add(UC_HOOK_CODE,
                    lambda uc, address, size, user:
                        uc.reg_write(UC_MIPS_REG_PC, AFTER_DISPATCH),
                    begin=DISPATCH_CALL, end=DISPATCH_CALL)
    uc.reg_write(UC_MIPS_REG_A0, candidate)
    uc.reg_write(UC_MIPS_REG_SP, CALLER_SP)
    uc.reg_write(UC_MIPS_REG_RA, RETURN_MAGIC)
    # Two legs, so a miss says *where* it was lost: the pre-dispatch calls at
    # 0x8009b184 / 0x8009c10c, or the state handler after them.
    try:
        uc.emu_start(TRAP_FUNCTION, DISPATCH_CALL, count=steps)
    except UcError:
        return (False, False, 0, 0, 0)
    if uc.reg_read(UC_MIPS_REG_PC) != DISPATCH_CALL:
        return (False, False, 0, 0, 0)
    try:
        uc.emu_start(DISPATCH_CALL, COUNTER_CALL, count=steps)
    except UcError:
        return (True, False, 0, 0, 0)
    reached = uc.reg_read(UC_MIPS_REG_PC) == COUNTER_CALL
    return (True, reached,
            uc.reg_read(UC_MIPS_REG_A0),
            uc.reg_read(UC_MIPS_REG_A1),
            uc.reg_read(UC_MIPS_REG_V0))


def candidates(dump: bytes, first: int) -> list[int]:
    found = []
    limit = min(len(dump), CARD_RAM) - OBJECT_SIZE
    for offset in range(first - KSEG0, limit, 4):
        flags = struct.unpack_from("<I", dump, offset + FLAGS_OFFSET)[0]
        if not flags & FLAGS_REQUIRED:
            continue
        if struct.unpack_from("<I", dump, offset + STATS_OFFSET)[0]:
            continue
        found.append(KSEG0 + offset)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("snapshot", type=Path, help="read-only 4 MiB BAR2 dump")
    parser.add_argument("--steps", type=int, default=20000,
                        help="instruction budget per candidate (default 20000)")
    parser.add_argument("--skip-dispatch", action="store_true",
                        help="step over the state handler at 0x8009b330, whose "
                             "replay against post-handler memory is not "
                             "idempotent")
    parser.add_argument("--max", type=int, default=0,
                        help="stop after this many candidates (0 = all)")
    parser.add_argument("--from", dest="first", type=lambda v: int(v, 0),
                        default=HEAP_START,
                        help="lowest candidate address (default 0x%x, past the "
                             "end of the protocol image; below that a match is "
                             "data misread as a structure)" % HEAP_START)
    args = parser.parse_args()

    dump = args.snapshot.read_bytes()
    image = dump[:CARD_RAM].ljust(CARD_RAM, b"\0")
    pool = candidates(dump, args.first)
    if args.max:
        pool = pool[:args.max]
    print(f"{len(pool)} candidate object(s) to replay")

    hits = []
    survived_prologue = 0
    for index, candidate in enumerate(pool):
        dispatch, reached, a0, a1, v0 = replay_isolated(
            image, candidate, args.steps, args.skip_dispatch)
        survived_prologue += dispatch
        if reached and a0 == 0:
            hits.append((candidate, a1, v0))
            mark = " <- v0 matches the frame" if v0 == FRAME_V0 else ""
            print(f"  0x{candidate:08x}: reaches 0x{COUNTER_CALL:08x} with a0 = 0, "
                  f"a1 = 0x{a1:x}, v0 = 0x{v0:08x}{mark}")
        if index and index % 2000 == 0:
            print(f"  ... {index}/{len(pool)}", file=sys.stderr)

    print(f"\n{survived_prologue} of {len(pool)} candidate(s) reach the state "
          f"dispatch at 0x{DISPATCH_CALL:08x}")
    print(f"{len(hits)} reproduce the null dereference")
    exact = [hit for hit in hits if hit[2] == FRAME_V0]
    if exact:
        print(f"{len(exact)} of them also reproduce the frame's "
              f"v0 = 0x{FRAME_V0:08x}:")
        for candidate, a1, _ in exact:
            print(f"  0x{candidate:08x}  (a1 = 0x{a1:x})")
    elif survived_prologue == 0:
        print("\nNo candidate survives even the pre-dispatch calls, so this "
              "method cannot\nconfirm the real object either: the snapshot is "
              "the state *after* those calls\nran, and they are not idempotent."
              "  A null result here is not evidence that the\nobject is absent "
              "-- the whole of card RAM is in the dump "
              f"(MQ_MEMORY_SIZE is 0x{CARD_RAM:x}).\nRecovering the "
              "pointer needs a cold boot, where the firmware creates the "
              "object\nitself.")
    else:
        print("no candidate reproduces the frame's v0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
