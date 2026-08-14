#!/usr/bin/env python3
"""Decode the V.8 page's script-record table out of an ADDSP V.90 overlay.

The V.8 page is not a hand-written state machine.  It is an interpreter over a
table of variable-length *records*, one per V.8 state, and nearly everything the
page does -- which message it builds, which detector it runs, where it goes next
-- is record data rather than code.  Two builds of the package can therefore be
compared as data, which is what this tool is for.

The encoding is read straight off the loader, ``PM 0x37B7`` in the Eicon
``0x025f V8.ANA`` overlay::

    37b7: AY0 = $00FF            ; field mask
    37b8: MR0 = $073F            ; field base
    37b9: AX0 = DM(I4,M5)        ; w0  -- offset
    37ba: AF = AX0 AND AY0       ;       offset = w0 & 0xFF
    37bb: AR = DM(I4,M5) AND AY0 ; w1  -- value low byte
    37bb: SR0 = DM(I4,M5)        ; w2  -- value high byte
    37bc: AR = MR0 + AF, SR1 = AR;       address = 0x073F + offset
    37be: SR = LSHIFT SR0 (HI) BY 8 (OR) ; value = (w2 & 0xFF) << 8 | (w1 & 0xFF)
    37bf: DM(I0,M1) = SR1
    37c0: IF NE JUMP $37B9       ;       until offset == MR1 == 0x11
    37c1: DM($0780) = M1         ;       "a new record was loaded"

So a record is a run of three-word triples, terminated by -- and *including* --
the triple whose offset is ``0x11``.  Records are contiguous, which is what
makes fall-through possible; ``I4`` is the cursor ``DM(0x049F)`` and is left
pointing at the next record on exit.

Field offsets that matter, all relative to base ``0x073F``:

===== ======== ===============================================================
off   DM       role
===== ======== ===============================================================
0x01  0x0740   action mask -- dispatched through ``PM 0x3B41``/``0x3B6B``
                against the 9-entry routine table ``PM 0x3DF6``; bit 4 is the
                CM builder ``PM 0x3828``, bit 5 the JM builder, bit 7 CI
0x03  0x0742   secondary action mask (table ``PM 0x3E0F``, 13 entries)
0x0D  0x074C   slot-1 destination index -> ``DM(0x0790)``
0x0E  0x074D   slot-2 destination index -> ``DM(0x0791)``
0x0F  0x074E   slot-1 condition index   -> ``DM(0x0792)``
0x10  0x074F   slot-2 condition index   -> ``DM(0x0793)``
0x11  0x0750   slot-0 condition index   -> ``DM(0x0794)``, the fall-through
                gate, and the record terminator
===== ======== ===============================================================

``PM 0x379A..0x37A3`` resolves the indices through two tables -- destinations at
``DM(0x035B) + index``, conditions at ``DM(0x034A) + index`` -- and
``PM 0x37A4..0x37AF`` then runs the three slots in order: slot 0 <= 0 means fall
through to the contiguously next record, slot 1 or slot 2 <= 0 means jump to
that slot's destination, nothing taken means the state repeats.

Usage::

    ./tools/v8_script_records.py <dm.words|dm.bin> [--start 0x0000] [--end 0x036D]
    ./tools/v8_script_records.py <dm.words> --graph
    ./tools/v8_script_records.py <dm.words> --find-mask-bit 4

For the Aster (Telindus) side the DM image comes from
``tools/aster_dsp_extract.py -o <dir>``, which writes concatenated blocks rather
than a sparse image, so pass ``--aster <file> --image N --page 6`` instead and
this tool will assemble the sparse image itself from the block addresses.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

FIELD_BASE = 0x073F
TERMINATOR = 0x11

# Field offset -> name, from the dispatcher and the index resolver.
FIELD_NAMES = {
    0x01: 'action_mask',
    0x03: 'action_mask2',
    0x04: 'action_mask3',
    0x07: 'action_mask4',
    0x0B: 'timer',
    0x0D: 'dest1_idx',
    0x0E: 'dest2_idx',
    0x0F: 'cond1_idx',
    0x10: 'cond2_idx',
    0x11: 'cond0_idx',
}

# Bit -> routine, for the 9-entry table at PM 0x3DF6 selected by field 0x01.
MASK1_BITS = {
    4: 'CM builder (PM 0x3828)',
    5: 'JM builder (PM 0x385E)',
    7: 'CI builder',
}


class Record:
    __slots__ = ('addr', 'end', 'fields', 'order')

    def __init__(self, addr: int) -> None:
        self.addr = addr
        self.end = addr
        self.fields: dict[int, int] = {}
        self.order: list[int] = []

    @property
    def mask(self) -> int:
        return self.fields.get(0x01, 0)

    def get(self, off: int) -> int | None:
        return self.fields.get(off)

    def __repr__(self) -> str:
        return f'<Record 0x{self.addr:04X} mask=0x{self.mask:04X}>'


def load_words(path: Path) -> dict[int, int]:
    """Read a sparse ``dm.words`` map, or a dense 64K-word ``dm.bin``."""
    if path.suffix == '.words' or path.name.endswith('.words'):
        words: dict[int, int] = {}
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            addr, value = line.split()
            words[int(addr, 16)] = int(value, 16)
        return words
    raw = path.read_bytes()
    return {i: int.from_bytes(raw[i * 2:i * 2 + 2], 'little')
            for i in range(len(raw) // 2)}


def load_aster(path: Path, image: int, page: int) -> dict[int, int]:
    """Assemble a sparse DM image for one Aster page from its pageblocks.

    tools/aster_dsp_extract.py knows the container; import it rather than
    re-implement the header walk, so the two stay in step.
    """
    sys.path.insert(0, str(REPO / 'tools'))
    import aster_dsp_extract as ax

    buf = path.read_bytes()
    _magic, images = ax.parse(buf)
    if image >= len(images):
        raise SystemExit(f'{path}: no image {image}')
    for pg in images[image].pages:
        if pg.index != page:
            continue
        words: dict[int, int] = {}
        for block in pg.blocks:
            if not block.is_dm:
                continue
            raw = buf[block.data_off:block.data_off + 2 * block.length]
            for i in range(block.length):
                words[block.address + i] = int.from_bytes(
                    raw[i * 2:i * 2 + 2], 'big')
        return words
    raise SystemExit(f'{path}: image {image} has no page {page}')


def decode_records(words: dict[int, int], start: int, end: int) -> list[Record]:
    """Walk contiguous records from ``start`` until ``end``.

    Mirrors PM 0x37B7 exactly: triples until offset 0x11 inclusive.  A triple
    whose words are missing from the sparse image, or a record that runs past
    ``end`` without terminating, stops the walk -- that is the table's end, not
    an error.
    """
    records: list[Record] = []
    cursor = start
    while cursor + 2 <= end:
        record = Record(cursor)
        addr = cursor
        while True:
            if addr + 2 > end:
                return records
            w0, w1, w2 = (words.get(addr), words.get(addr + 1),
                          words.get(addr + 2))
            if w0 is None or w1 is None or w2 is None:
                return records
            offset = w0 & 0xFF
            value = ((w2 & 0xFF) << 8) | (w1 & 0xFF)
            if offset not in record.fields:
                record.order.append(offset)
            record.fields[offset] = value
            addr += 3
            if offset == TERMINATOR:
                break
        record.end = addr
        records.append(record)
        cursor = addr
    return records


def read_tables(words: dict[int, int], cond_base: int, dest_base: int,
                count: int) -> tuple[list[int | None], list[int | None]]:
    conds = [words.get(cond_base + i) for i in range(count)]
    dests = [words.get(dest_base + i) for i in range(count)]
    return conds, dests


def format_record(rec: Record, dests: list[int | None]) -> str:
    def dest(idx: int | None) -> str:
        if idx is None:
            return '-'
        target = dests[idx] if idx < len(dests) else None
        shown = f'0x{target:04X}' if target is not None else '?'
        return f'{idx}->{shown}'

    bits = [f'b{b}' for b in range(16) if rec.mask & (1 << b)]
    parts = [
        f'0x{rec.addr:04X}',
        f'len={rec.end - rec.addr:3d}',
        f'mask=0x{rec.mask:04X} [{",".join(bits) or "-"}]',
        f'cond0={rec.get(0x11)}',
        f'slot1={dest(rec.get(0x0D))}/c{rec.get(0x0F)}',
        f'slot2={dest(rec.get(0x0E))}/c{rec.get(0x10)}',
    ]
    line = '  '.join(parts)
    named = [n for b, n in MASK1_BITS.items() if rec.mask & (1 << b)]
    if named:
        line += '   <- ' + ', '.join(named)
    return line


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('path', type=Path,
                    help='dm.words / dm.bin, or the Aster container with --aster')
    ap.add_argument('--aster', action='store_true',
                    help='path is a Telindus Aster DSP container, not a DM image')
    ap.add_argument('--image', type=int, default=0)
    ap.add_argument('--page', type=int, default=6)
    ap.add_argument('--start', type=lambda s: int(s, 0), default=0x0000)
    ap.add_argument('--end', type=lambda s: int(s, 0), default=None,
                    help='end of the record region (default: the condition '
                         'table base, which follows the last record)')
    ap.add_argument('--cond-base', type=lambda s: int(s, 0), default=0x034A)
    ap.add_argument('--dest-base', type=lambda s: int(s, 0), default=0x035B)
    ap.add_argument('--table-count', type=int, default=19)
    ap.add_argument('--find-mask-bit', type=int, action='append', default=[],
                    help='report only records whose field 0x01 sets this bit')
    ap.add_argument('--graph', action='store_true',
                    help='print, per record, every record that can reach it')
    ap.add_argument('--fields', action='store_true',
                    help='dump every field of every record')
    args = ap.parse_args()

    if args.aster:
        words = load_aster(args.path, args.image, args.page)
    else:
        words = load_words(args.path)
    if not words:
        raise SystemExit(f'{args.path}: no DM words')

    end = args.end if args.end is not None else args.cond_base
    records = decode_records(words, args.start, end)
    conds, dests = read_tables(words, args.cond_base, args.dest_base,
                               args.table_count)

    print(f'{args.path}: {len(words)} DM words, '
          f'{len(records)} records in 0x{args.start:04X}..0x{end:04X}')
    print(f'condition table @ 0x{args.cond_base:04X}: '
          + ' '.join('-' if c is None else f'{i}:0x{c:04X}'
                     for i, c in enumerate(conds)))
    print(f'destination table @ 0x{args.dest_base:04X}: '
          + ' '.join('-' if d is None else f'{i}:0x{d:04X}'
                     for i, d in enumerate(dests)))
    print()

    by_addr = {r.addr: r for r in records}
    wanted = set(args.find_mask_bit)
    for i, rec in enumerate(records):
        if wanted and not any(rec.mask & (1 << b) for b in wanted):
            continue
        print(format_record(rec, dests))
        if args.fields:
            for off in rec.order:
                name = FIELD_NAMES.get(off, '')
                print(f'      +0x{off:02X} DM(0x{FIELD_BASE + off:04X}) '
                      f'= 0x{rec.fields[off]:04X}  {name}')
        if args.graph:
            preds = []
            for other in records:
                for slot, idx_off in ((1, 0x0D), (2, 0x0E)):
                    idx = other.get(idx_off)
                    if idx is not None and idx < len(dests) \
                            and dests[idx] == rec.addr:
                        preds.append(f'0x{other.addr:04X}(slot{slot})')
            if i and records[i - 1].get(0x11) is not None:
                preds.append(f'0x{records[i - 1].addr:04X}(fall-through)')
            print('      reached from: ' + (', '.join(preds) or 'NOTHING'))

    unreferenced = [r for r in records
                    if not any(d == r.addr for d in dests if d is not None)]
    if not wanted:
        print()
        print(f'{len(by_addr)} records; '
              f'{len(unreferenced)} are not a destination-table target')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
