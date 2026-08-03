#!/usr/bin/env python3
"""Decode the V.34 page's state scripts out of the overlay's DM image.

Session 102 established the shape: the script is a run of three-word entries,
each carrying one (field, value) pair; a block ends at a terminator field
(`0x19` for sequencer A, `0x24` for sequencer B); the value lands at
`0x2137 + field`; and the two roles' scripts are *byte-interleaved into the
same words*, the calling side reading low bytes at PM 0x2E1A and the answering
side high bytes at PM 0x2E24.

Session 114g scanned for field-4 entries by testing every word position, which
cannot tell a real entry from a chance alignment.  This walks the entries from
a base address instead, so an entry is only reported if the block structure
actually puts it there.

The decoders, read off PM 0x2E1A and PM 0x2E24 with SE = -8:

    calling   field = word0 & 0xff
              value = ((word2 & 0xff) << 8) | (word1 & 0xff)
    answering field = word0 >> 8
              value = ((word2 >> 8) << 8) | (word1 >> 8)

PM 0x2E2D stores the value and PM 0x2E2E loops while `field != terminator`,
so a block is every entry up to and including the terminating one.

Fields that have names, from Session 102's record map:

    0x04  the gate the V.34 page waits on -- DM(0x213B), tested for bit 15
          at PM 0x285e..0x2861
    0x0E  detector threshold        0x0F  countdown
    0x10  state -> TrnProgress      0x11..0x14  branch targets
    0x15..0x19  test routines

Usage:
    python3 tools/v34_script.py --role answer
    python3 tools/v34_script.py --role answer --field 0x04
    python3 tools/v34_script.py --role call --base 0x1a2e --blocks 40
"""
from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

RECORD_BASE = 0x2137
GATE_FIELD = 0x04
STATE_FIELD = 0x10

FIELD_NAMES = {
    0x04: 'gate DM(0x213B)',
    0x0E: 'threshold',
    0x0F: 'countdown',
    0x10: 'state',
    0x11: 'branch0', 0x12: 'branch1', 0x13: 'branch2', 0x14: 'branch3',
    0x15: 'test0', 0x16: 'test1', 0x17: 'test2', 0x18: 'test3', 0x19: 'test4',
}

# Session 102: sequencer A is shared, sequencer B forks by role.
DEFAULT_BASES = {'call': 0x1EA2, 'answer': 0x1E81, 'shared': 0x1A2E}
DEFAULT_TERMINATOR = {'call': 0x24, 'answer': 0x24, 'shared': 0x19}


@dataclass(frozen=True)
class Entry:
    address: int
    field: int
    value: int

    @property
    def name(self) -> str:
        return FIELD_NAMES.get(self.field, '')


@dataclass(frozen=True)
class Block:
    address: int
    entries: tuple[Entry, ...]

    def field(self, which: int) -> int | None:
        for entry in self.entries:
            if entry.field == which:
                return entry.value
        return None


def load_dm(path: Path) -> tuple[int, ...]:
    data = path.read_bytes()
    return struct.unpack(f'<{len(data) // 2}H', data)


def decode(words: tuple[int, ...], address: int, answering: bool) -> Entry:
    word0, word1, word2 = words[address], words[address + 1], words[address + 2]
    if answering:
        field = word0 >> 8
        value = ((word2 >> 8) << 8) | (word1 >> 8)
    else:
        field = word0 & 0xFF
        value = ((word2 & 0xFF) << 8) | (word1 & 0xFF)
    return Entry(address, field, value)


def walk(words: tuple[int, ...], base: int, answering: bool, terminator: int,
         limit: int, max_entries: int = 64) -> list[Block]:
    """Read consecutive blocks, each ending at the terminator field."""
    blocks: list[Block] = []
    address = base
    while len(blocks) < limit and address + 3 <= len(words):
        start = address
        entries: list[Entry] = []
        while address + 3 <= len(words) and len(entries) < max_entries:
            entry = decode(words, address, answering)
            entries.append(entry)
            address += 3
            if entry.field == terminator:
                break
        else:
            break
        if entries and entries[-1].field != terminator:
            break
        blocks.append(Block(start, tuple(entries)))
    return blocks


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dm', type=Path,
                    default=Path('artifacts/eicon-dsp/overlays/'
                                 '0261-v.34-overlay/dm.bin'))
    ap.add_argument('--role', choices=('call', 'answer', 'shared'),
                    default='answer')
    ap.add_argument('--base', type=lambda t: int(t, 0),
                    help='script base (default per role)')
    ap.add_argument('--terminator', type=lambda t: int(t, 0),
                    help='terminating field (default per role)')
    ap.add_argument('--blocks', type=int, default=24, help='blocks to walk')
    ap.add_argument('--field', type=lambda t: int(t, 0),
                    help='only report blocks carrying this field')
    ap.add_argument('--bit15', action='store_true',
                    help='only report blocks whose gate field has bit 15 set')
    args = ap.parse_args()

    if not args.dm.exists():
        print(f'{args.dm} not found', file=sys.stderr)
        return 2
    words = load_dm(args.dm)
    base = args.base if args.base is not None else DEFAULT_BASES[args.role]
    terminator = (args.terminator if args.terminator is not None
                  else DEFAULT_TERMINATOR[args.role])
    answering = args.role == 'answer'

    blocks = walk(words, base, answering, terminator, args.blocks)
    print(f'{args.dm.name}  role={args.role}  base={base:#06x}  '
          f'terminator=field {terminator:#04x}  '
          f'{"high" if answering else "low"} bytes  '
          f'-> {len(blocks)} blocks')

    shown = 0
    for block in blocks:
        gate = block.field(GATE_FIELD)
        if args.bit15 and not (gate is not None and gate & 0x8000):
            continue
        if args.field is not None and block.field(args.field) is None:
            continue
        shown += 1
        state = block.field(STATE_FIELD)
        head = f'block {block.address:#06x}'
        if state is not None:
            head += f'  state {state:#06x}'
        if gate is not None:
            head += f'  gate {gate:#06x}'
            if gate & 0x8000:
                head += ' [bit 15 SET]'
        print(head)
        for entry in block.entries:
            name = f'  {entry.name}' if entry.name else ''
            print(f'    {entry.address:#06x}  field {entry.field:#04x} = '
                  f'{entry.value:#06x}{name}')
    if not shown:
        print('  (no block matched)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
