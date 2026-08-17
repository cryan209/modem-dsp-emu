#!/usr/bin/env python3
"""Disassemble an EICON_DUMP_PM text dump (``addr opcode6hex`` per line).

``adsp2181_dis.py`` reads a flat PM image from address 0; a live DUMP_PM only
has the window we asked for, so route each opcode through its ``disas`` decoder
directly. Usage: disasm_dump.py <dump.txt>
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adsp2181_dis as dis


def main() -> int:
    for line in Path(sys.argv[1]).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        addr_s, _, op_s = line.partition(' ')
        addr = int(addr_s, 16)
        op = int(op_s, 16)
        print(f'{addr:04x}: {op:06x}  {dis.disas(op)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
