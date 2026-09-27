#!/usr/bin/env python3
"""Extract PatchArray from Intel's unstripped ELF32 hamcore.lib."""

import argparse
import hashlib
import struct
from pathlib import Path


def cstring(blob: bytes, offset: int) -> str:
    end = blob.index(0, offset)
    return blob[offset:end].decode("ascii")


def extract_symbol(elf: bytes, wanted: str) -> bytes:
    if elf[:6] != b"\x7fELF\x01\x01":
        raise ValueError("input is not a little-endian ELF32 object")
    header = struct.unpack_from("<16sHHIIIIIHHHHHH", elf)
    shoff, shentsize, shnum, shstrndx = header[6], header[11], header[12], header[13]
    sections = [
        struct.unpack_from("<IIIIIIIIII", elf, shoff + i * shentsize)
        for i in range(shnum)
    ]
    symtab_index = next(i for i, section in enumerate(sections) if section[1] == 2)
    symtab = sections[symtab_index]
    strtab = sections[symtab[6]]
    strings = elf[strtab[4] : strtab[4] + strtab[5]]
    count = symtab[5] // symtab[9]
    for i in range(count):
        entry = struct.unpack_from("<IIIBBH", elf, symtab[4] + i * symtab[9])
        name, value, size, _, _, section_index = entry
        if cstring(strings, name) == wanted:
            section = sections[section_index]
            start = section[4] + value
            return elf[start : start + size]
    raise ValueError(f"symbol {wanted!r} not found")


def records(patch: bytes):
    offset = 0
    while offset < len(patch):
        record_type = patch[offset]
        if record_type == 0x30:
            return
        if offset + 6 > len(patch):
            raise ValueError(f"truncated record header at {offset:#x}")
        overlay = patch[offset + 1]
        address, words = struct.unpack_from("<HH", patch, offset + 2)
        end = offset + 6 + words * 2
        if end > len(patch):
            raise ValueError(f"truncated record data at {offset:#x}")
        yield offset, record_type, overlay, address, words
        offset = end
    raise ValueError("missing 0x30 terminator")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("hamcore", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    patch = extract_symbol(args.hamcore.read_bytes(), "PatchArray")
    parsed = list(records(patch))
    boot = [r for r in parsed if r[2] == 0]
    if len(patch) != 0x747C or len(parsed) != 43 or len(boot) != 3:
        raise ValueError("unexpected PatchArray layout")
    args.output.write_bytes(patch)
    print(f"wrote {len(patch)} bytes to {args.output}")
    print(f"sha256={hashlib.sha256(patch).hexdigest()}")
    for offset, record_type, overlay, address, words in boot:
        print(
            f"boot record @{offset:#06x}: type={record_type:#04x} "
            f"address={address:#06x} words={words:#06x}"
        )


if __name__ == "__main__":
    main()
