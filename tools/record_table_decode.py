#!/usr/bin/env python3
"""Decode the state-record tables the V.34/V.90 pages interpret.

The V.34, V.90D and V.90A pages all run the same two-level machine: an outer
scheduler walks a table of *records* in data memory, and an unpacker copies
each record into a fixed block of DM words that the state's handlers then read.
The three pages carry the unpacker at three addresses -- V.34 `PM 0x2E24`,
V.90D `PM 0x2FE4`, V.90A `PM 0x33DD` -- but the instruction sequence is
byte-identical in all three, so one decoder reads all of them::

    SE = $FFF8                       ; shift right by 8
    AR  = DM(I4,M5)                  ; A
    SR  = LSHIFT AR (LO), AR = DM(I4,M5)     ; B
    AF  = SR0 + 0,       SR0 = DM(I4,M5)     ; C
    SR  = LSHIFT SR0 (LO)
    SR  = LSHIFT SR0 (HI)
    SR  = LSHIFT AR (LO, OR)
    AR  = MR0 + AF                   ; base + index
    I0  = AR
    DM(I0,M1) = SR0, AR = MR1 XOR AF ; store, and test for the terminator

so an entry is three DM words wide and carries

    index = A >> 8
    value = (C & 0xFF00) | (B >> 8)

and a record ends at the entry whose index equals `MR1`, which is also the
block's last index and therefore its size.  V.34 (`PM 0x2D7E`) and V.90A both
load 25, for a 26-word block; **V.90D loads 23** (`PM 0x2FB5`) over a block
based at `DM(0x1FE9)`, so pass `--terminator 23` for it -- and note its state
word is `DM(0x1FF7)`, index 14 rather than 16, so `--state` needs moving too
before its listing is readable.  Its table start is not yet located; the two
pages the V.90 loopback blocker lives on decode as they are.  Entries are
sparse:
a record writes only the words it changes, and everything else persists from
the record before it, which is what makes the block a running configuration
rather than a per-state parameter list.

Why this exists.  Session 249 asked which record sets bit 11 of `DM(0x20EF)`,
the word the V.90A caller's `0x0092` waits on, and answering it by reading the
image by eye is how a session goes missing.  Decoded, the whole V.90A table is
51 records and the question takes one command.  The same command answers it for
V.34, whose table this project has never read.

    tools/record_table_decode.py <dm.bin> --start 0x1689
    tools/record_table_decode.py <dm.bin> --start 0x1689 --index 6

`--index` reports every record that writes one word, which is the form the
"who sets this bit" question actually takes.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

# Index 16 of the block is the state number and index 15 its dwell, measured
# against the live trace: V.90A's block base is DM(0x20E9), so state is
# DM(0x20F9) and dwell DM(0x20F8), which is what `--trace-v90a-state` prints.
STATE_INDEX = 16
DWELL_INDEX = 15
# The terminator is whatever the caller loads into MR1, and it differs by page:
# V.34 (PM 0x2D7E) and V.90A (PM 0x2621's caller) use 0x0019, V.90D (PM 0x2FB5)
# uses 0x0017.  It is also the block's last index, so it sizes the block.
TERMINATOR = 25
# The *inner* machine reads the same three words with the other byte of each.
# `PM 0x33D2` is its unpacker and it is `PM 0x33DD` with the shifts moved:
# index is `A & 0xFF` rather than `A >> 8` and the value's low byte is `B &
# 0xFF` rather than `B >> 8`, while both take the same `C & 0xFF00` for the
# high byte.  So one entry carries an assignment for each machine, and the
# table this file already decoded is *two* programs.  The inner block is based
# at `DM(0x20E9)` too, its terminator is 36 (`PM 0x33BB` loads `MR1 = 0x24`),
# its state word is `DM(0x2104)` -- index 27, one before the next-address slots
# at `DM(0x2105..0x2108)`, the same relationship the outer machine has -- and
# its cursor is `DM(0x2127)`, which `--trace-v90a-state` prints as `iptr`.
INNER_STATE_INDEX = 27
INNER_DWELL_INDEX = 26
INNER_TERMINATOR = 36


def load(path: Path) -> "list[int]":
    raw = path.read_bytes()
    return list(struct.unpack(f"<{len(raw) // 2}H", raw[: len(raw) // 2 * 2]))


def decode_record(dm: "list[int]", address: int, terminator: int = TERMINATOR,
                  inner: bool = False):
    """One record as [(index, value)], plus the address after it.

    Returns None if the words at `address` do not decode as a record, which is
    how the end of a chain is recognised: there is no length field, so walking
    off the table produces an index above the terminator almost immediately.
    """
    entries = []
    while address + 2 < len(dm):
        a, b, c = dm[address], dm[address + 1], dm[address + 2]
        index = (a & 0xFF) if inner else (a >> 8)
        value = (c & 0xFF00) | ((b & 0xFF) if inner else (b >> 8))
        address += 3
        if index > terminator:
            return None
        entries.append((index, value))
        if index == terminator:
            return entries, address
        if len(entries) > terminator + 5:
            return None
    return None


def walk(dm: "list[int]", start: int, limit: int,
         terminator: int = TERMINATOR, inner: bool = False):
    """The chain of records from `start`, as [(address, entries)]."""
    records = []
    address = start
    for _ in range(limit):
        decoded = decode_record(dm, address, terminator, inner)
        if decoded is None:
            break
        entries, address = decoded
        records.append((records and address or address, entries))
    # Recover each record's own address rather than the advanced cursor.
    out, address = [], start
    for _, entries in records:
        out.append((address, entries))
        address += 3 * len(entries)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dm", type=Path, help="dm.bin from tools/eicon_dsp_extract.py")
    ap.add_argument("--start", type=lambda v: int(v, 0), required=True,
                    help="first record of the chain, e.g. 0x1689 for V.90A")
    ap.add_argument("--limit", type=int, default=200,
                    help="stop after this many records (default 200)")
    ap.add_argument("--index", type=lambda v: int(v, 0), default=None,
                    help="report only records that write this block index, "
                         "which is the form 'what sets this word' takes")
    ap.add_argument("--mask", type=lambda v: int(v, 0), default=None,
                    help="with --index, report only values that have these "
                         "bits set")
    ap.add_argument("--terminator", type=lambda v: int(v, 0), default=TERMINATOR,
                    help="the MR1 the page's unpacker caller loads: 25 for "
                         "V.34 and V.90A, 23 for V.90D (default 25)")
    ap.add_argument("--full", action="store_true",
                    help="print every entry of every record")
    ap.add_argument("--inner", action="store_true",
                    help="read the *other* program in the same table: the "
                         "inner machine's unpacker (V.90A `PM 0x33D2`) takes "
                         "the low byte of each entry's first two words where "
                         "the outer one takes the high byte, so one table is "
                         "two programs. Implies terminator 36 and moves the "
                         "state word to index 27, `DM(0x2104)`")
    args = ap.parse_args()

    state_index, dwell_index = STATE_INDEX, DWELL_INDEX
    if args.inner:
        state_index, dwell_index = INNER_STATE_INDEX, INNER_DWELL_INDEX
        if args.terminator == TERMINATOR:
            args.terminator = INNER_TERMINATOR

    dm = load(args.dm)
    records = walk(dm, args.start, args.limit, args.terminator, args.inner)
    if not records:
        print(f"no record decodes at 0x{args.start:04x}", file=sys.stderr)
        return 1

    print(f"{len(records)} records from 0x{args.start:04x}")
    matched = 0
    for address, entries in records:
        fields = dict(entries)
        state = fields.get(state_index)
        dwell = fields.get(dwell_index)
        if args.index is not None:
            if args.index not in fields:
                continue
            value = fields[args.index]
            if args.mask is not None and not value & args.mask:
                continue
            matched += 1
            print(f"0x{address:04x} state={_hex(state)} "
                  f"index {args.index} = {value:04x}")
            continue
        print(f"0x{address:04x} state={_hex(state)} dwell={_hex(dwell)} "
              f"entries={len(entries)}")
        if args.full:
            for index, value in entries:
                print(f"           [{index:2d}] = {value:04x}")
    if args.index is not None:
        print(f"{matched} record(s) write index {args.index}"
              + (f" with mask 0x{args.mask:04x}" if args.mask else ""))
    return 0


def _hex(value):
    return "----" if value is None else f"{value:04x}"


if __name__ == "__main__":
    raise SystemExit(main())
