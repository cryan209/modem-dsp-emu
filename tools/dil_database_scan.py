#!/usr/bin/env python3
"""Read the guide-named database words out of archived .adsp-dm.bin captures.

Every name here comes from docs/addsp_database.md, which is the ADDSP guide's
own table. The point of the tool is that a `.adsp-dm.bin` already holds all 256
words of the memory-mapped interface for every RTP packet of every call ever
captured, so a question of the form "what does the card say about the line at
DIL time" is a read over the archive rather than a live call.

Index into the 256-word snapshot is the guide offset directly: write half
0x00..0x7F, read half 0x80..0xFF (guide 6.5, and handoff §6).

Two things this tool exists to keep honest:

  * `--span` reports how many distinct values each word took over the whole
    call, which is the positive control for a word that reads zero. A word that
    is constant zero while its neighbours move is never written; a word that is
    constant zero in a capture where nothing moves means the capture is the
    problem. Session 207 established the echo block is the first case.
  * the DIL read is taken at the first record at or past `TrnProgress 0x007a`,
    and the peak TrnProgress of the call is printed beside it, because the
    archive's `0x00d0` calls are mostly page 2 (V.32) replays of one call and a
    split measured over "successes" that are all one call is not a measurement.
    `--v90-only` keeps the four captures that are actually on the V.90 page.

Usage:

    python3 tools/dil_database_scan.py artifacts/*/*.adsp-dm.bin
    python3 tools/dil_database_scan.py --span --v90-only artifacts/*/*.adsp-dm.bin
"""
from __future__ import annotations

import argparse
import struct
from pathlib import Path

MAGIC = b'EADSPDM2'
RECORD = struct.Struct('<Q256H')

# Write half: host-to-DSP configuration. None of these was read by any tool in
# this repo before Session 207.
WRITE = {
    0x00: 'GEN_setup0', 0x01: 'GEN_setup1', 0x04: 'V8_setup', 0x06: 'V34_setup',
    0x08: 'TD', 0x0B: 'DCD_OFF', 0x0C: 'DCD_HYST',
    0x28: 'Norm_H', 0x29: 'Norm_L', 0x2A: 'speed_sel_h', 0x2B: 'speed_sel_l',
    0x2C: 'Maxtimer', 0x2D: 'Mintimer',
    0x55: 'MinReduction_dbs', 0x56: 'AddReduction_dbs',
    0x79: 'speed_sel_V90_H', 0x7A: 'speed_sel_V90_L', 0x7B: 'Info0D_setup',
    0x7C: 'MAXTXSPEED', 0x7D: 'MAXTXSPEED_V90',
    0x7E: 'MAXRXSPEED', 0x7F: 'MAXRXSPEED_V90',
}

# Read half: what the card says about the line, plus the words that identify
# which call this is.
READ = {
    0x80: 'DatagramRate', 0x81: 'DATASTATEspeedTx', 0x82: 'DATASTATESpeed',
    0x84: 'ErrorMessage',
    0x98: 'RXLevel', 0x99: 'EcLevel', 0x9A: 'NearEcLevel', 0x9B: 'FarEcLevel',
    0x9C: 'FarEchoPhaseRoll', 0x9D: 'SNRatio',
    0xA5: 'SNRPROB', 0xA6: 'Signalquality', 0xA7: 'RTDelay',
    0xD0: 'bootpage_nr', 0xE0: 'Rstatus_ch', 0xE2: 'TrnProgress',
    0xE4: 'classifier',      # DM(0x3FC4), reserved in the guide; a Norm_L mask
}

DIL_ENTRY = 0x007a

# Speed number -> rate, by bit position in the two speed masks. speed_sel_l
# carries numbers 0..15 and speed_sel_h numbers 16..31; the '--' entries in the
# guide's speed_sel_h table are the missing V.34 rates, which is what makes
# speed_sel_h = 0x001f read as 24000..33600.
V34_RATES = {
    1: 75, 2: 110, 3: 150, 4: 300, 5: 600, 6: 1200, 7: 2400, 8: 4800,
    9: 7200, 10: 9600, 11: 12000, 12: 14400, 13: 16800, 14: 19200, 15: 21600,
    16: 24000, 17: 26400, 18: 28800, 19: 31200, 20: 33600, 29: 9600,
}
V90_RATES = {number: round(28000 + number * 4000 / 3) for number in range(16)}
V90_RATES.update({16 + number: round(49000 + number * 4000 / 3)
                  for number in range(6)})


def rate_of(speed_word: int) -> str:
    """Decode DATASTATESpeed / DATASTATEspeedTx (guide 5.3.2, and handoff §6).

    Bits 4..0 are the speed number, bits 9..5 the norm number, bit C a V.32bis
    trellis flag and bit D picks which speed mask the number indexes. Reading
    bit D is what lets one decoder serve both modulations: the V.32 captures
    publish 0x11aa here and the V.90 ones 0x2029, and the second is only a rate
    at all under the V.90 mask.
    """
    if not speed_word or speed_word == 0xFFFF:
        return '-'
    number = speed_word & 0x1F
    norm = (speed_word >> 5) & 0x1F
    v90_format = bool(speed_word & 0x2000)
    table = V90_RATES if v90_format else V34_RATES
    rate = table.get(number)
    return (f'{rate if rate else "?"}'
            f'{"" if v90_format else f"/norm{norm}"}'
            f'{"/V90" if v90_format else ""}')


def records(path: Path):
    with path.open('rb') as handle:
        if handle.read(len(MAGIC)) != MAGIC:
            raise ValueError(f'{path}: not an EADSPDM2 capture')
        while len(chunk := handle.read(RECORD.size)) == RECORD.size:
            values = RECORD.unpack(chunk)
            yield values[0], values[1:]


def scan(path: Path):
    """Return (peak TrnProgress, DIL record, last record, per-word value sets)."""
    peak, dil, last = 0, None, None
    seen: dict[int, set[int]] = {offset: set() for offset in (*WRITE, *READ)}
    for sample, dm in records(path):
        peak = max(peak, dm[0xE2])
        if dil is None and DIL_ENTRY <= dm[0xE2] <= 0x00FF:
            dil = (sample, dm)
        last = (sample, dm)
        for offset in seen:
            seen[offset].add(dm[offset])
    return peak, dil, last, seen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('captures', type=Path, nargs='+')
    parser.add_argument('--span', action='store_true',
                        help='per word, how many distinct values it took over '
                             'the whole call: the positive control for a zero')
    parser.add_argument('--v90-only', action='store_true',
                        help='only captures that were on the V.90 page (14)')
    parser.add_argument('--write', action='store_true',
                        help='also print the write half')
    args = parser.parse_args()

    for path in args.captures:
        try:
            peak, dil, last, seen = scan(path)
        except ValueError as error:
            print(f'{path}: {error}')
            continue
        if last is None:
            print(f'{path}: empty')
            continue
        if args.v90_only and 14 not in seen[0xD0]:
            continue
        tag, (sample, dm) = ('DIL', dil) if dil else ('end', last)
        print(f'\n=== {path}')
        print(f'  peak TrnProgress 0x{peak:04x}  pages {sorted(seen[0xD0])}  '
              f'classifier {[f"0x{value:04x}" for value in sorted(seen[0xE4])]}  '
              f'read at {tag} sample {sample} ({sample / 8000:.2f}s)')
        print(f'  rate  DATASTATEspeedTx {rate_of(dm[0x81])}  '
              f'DATASTATESpeed {rate_of(dm[0x82])}  '
              f'(over the call: '
              f'{" ".join(sorted({rate_of(v) for v in seen[0x82]}))})')
        tables = (('read', READ),) if not args.write else (('write', WRITE),
                                                          ('read', READ))
        for label, table in tables:
            cells = []
            for offset, name in sorted(table.items()):
                cell = f'{name}=0x{dm[offset]:04x}'
                if args.span:
                    values = seen[offset]
                    cell += (f'[const]' if len(values) == 1
                             else f'[{len(values)}v,max=0x{max(values):04x}]')
                cells.append(cell)
            print(f'  [{label}] ' + '  '.join(cells))


if __name__ == '__main__':
    main()
