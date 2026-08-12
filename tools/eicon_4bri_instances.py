#!/usr/bin/env python3
"""Read the 4BRI protocol instances out of a BAR2 dump.

The `te_dmlt.qm` 107-136 image keeps one instance structure per logical
adapter and a pointer to the current one in the global at `0x800442d4`.  The
XLOG's `Instance(0)=0x801ca000` line is that global; the other three follow at
a 0xd40 stride, each carrying the instance-0 address in its own word 0, which
is what this tool keys on.

Three fields matter for the null-pointer trap, all of them allocated and
zeroed at startup by the routine at `0x8009e000`:

    +0x6d0   10 x 32-byte slot table  (320 bytes)
    +0x6d8   592-byte parameter block, whose bytes 0..4 are the two
             descriptors the object constructor at 0x800821a8 is gated on
    +0x7f0   pointer to the most recently constructed object, or null
    +0x7fc   pool of 10 x 1364-byte objects

The object at `+12` of one of those pool entries is the statistics block whose
absence is blamed for the trap.  Printing the pool says whether any such object
exists at all -- on this card none ever has.  Addresses are read as a flat
map of card SDRAM: BAR2 offset == virtual address - 0x80000000.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

BASE = 0x80000000
INSTANCE_GLOBAL = 0x800442D4
INSTANCE_STRIDE = 0xD40
INSTANCE_COUNT = 4

OFFS_SLOT_TABLE = 0x6D0
OFFS_PARAM_BLOCK = 0x6D8
OFFS_CURRENT_OBJECT = 0x7F0
OFFS_OBJECT_POOL = 0x7FC

PARAM_BLOCK_SIZE = 592
OBJECT_SIZE = 1364
OBJECT_COUNT = 10
SLOT_TABLE_SIZE = 320


class OutOfDump(Exception):
    pass


def _read(dump: bytes, addr: int, length: int) -> bytes:
    offset = addr - BASE
    if offset < 0 or offset + length > len(dump):
        raise OutOfDump(f"0x{addr:08x}+{length} is outside the dump")
    return dump[offset:offset + length]


def word(dump: bytes, addr: int) -> int:
    return struct.unpack("<I", _read(dump, addr, 4))[0]


def report(dump: bytes) -> int:
    current = word(dump, INSTANCE_GLOBAL)
    print(f"global 0x{INSTANCE_GLOBAL:08x} -> instance 0x{current:08x}")
    if current == 0:
        print("no current instance; the card never got as far as running one")
        return 1

    instance0 = current
    for index in range(INSTANCE_COUNT):
        instance = instance0 + index * INSTANCE_STRIDE
        try:
            back = word(dump, instance)
            table = word(dump, instance + OFFS_SLOT_TABLE)
            params = word(dump, instance + OFFS_PARAM_BLOCK)
            pool = word(dump, instance + OFFS_OBJECT_POOL)
            active = word(dump, instance + OFFS_CURRENT_OBJECT)
        except OutOfDump as exc:
            print(f"instance {index} at 0x{instance:08x}: {exc}")
            continue

        confirmed = "instance-0 backref ok" if back == instance0 else \
                    f"word0=0x{back:08x} (expected 0x{instance0:08x})"
        print(f"\ninstance {index} at 0x{instance:08x}  [{confirmed}]")
        print(f"  slot table   0x{table:08x}")
        print(f"  param block  0x{params:08x}")
        print(f"  object pool  0x{pool:08x}")
        print(f"  current obj  0x{active:08x}")

        try:
            block = _read(dump, params, PARAM_BLOCK_SIZE)
        except OutOfDump as exc:
            print(f"  param block: {exc}")
        else:
            print(f"  descriptors  type1={block[0]:#04x} value={block[1]:#04x}"
                  f"  type2={block[2]:#04x} value={block[3]:#04x}"
                  f"  ({sum(1 for b in block if b)} of {PARAM_BLOCK_SIZE}"
                  " bytes non-zero)")
            if (block[0], block[2]) != (1, 2):
                print("               constructor gate cannot pass: the caller"
                      " at 0x8009ccdc requires type1==1 and type2==2")

        used = 0
        for slot in range(OBJECT_COUNT):
            addr = pool + slot * OBJECT_SIZE
            try:
                obj = _read(dump, addr, OBJECT_SIZE)
            except OutOfDump as exc:
                print(f"  slot {slot}: {exc}")
                continue
            if not any(obj):
                continue
            used += 1
            stats = struct.unpack("<I", obj[12:16])[0]
            print(f"  slot {slot} at 0x{addr:08x}: in-use marker"
                  f" {obj[0]:#04x} state+107={obj[107]:#04x}"
                  f" statistics+12=0x{stats:08x}")
        if used == 0:
            print(f"  all {OBJECT_COUNT} pool slots are entirely zero:"
                  " no object of this type has ever been constructed")

        try:
            slots = _read(dump, table, SLOT_TABLE_SIZE)
        except OutOfDump as exc:
            print(f"  slot table: {exc}")
        else:
            live = sum(1 for i in range(0, SLOT_TABLE_SIZE, 32)
                       if any(slots[i:i + 32]))
            print(f"  slot table:  {live} of {SLOT_TABLE_SIZE // 32}"
                  " entries non-zero")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("snapshot", type=Path, help="read-only 4 MiB BAR2 dump")
    args = parser.parse_args()

    dump = args.snapshot.read_bytes()
    if len(dump) < 0x400000:
        print(f"{args.snapshot}: {len(dump)} bytes, expected a 4 MiB BAR2 dump",
              file=sys.stderr)
    return report(dump)


if __name__ == "__main__":
    raise SystemExit(main())
