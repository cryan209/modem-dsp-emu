#!/usr/bin/env python3
"""Unpack a PortMaster ADSP-2181 download image (dp2.bin, 2181_*.bin/.ovl).

These are not flat PM images.  They are the literal stream consumed by the
ComOS ``mdp2_card_dload`` routine::

    <IDMA address:16> <transfer count:16> <count 16-bit writes> ...

For program memory (address bit 0x4000 clear), two writes hold one 24-bit
ADSP-2181 word as

    word = (first << 8) | (second & 0x00ff)

Data-memory records (address bit 0x4000 set) contain one 16-bit word per write.
The final record has address zero: its two writes install PM[0], releasing an
ADSP-2181 held in IDMA boot mode.  One trailing 16-bit checksum follows it.

This is why an interrupt-vector scan over the raw file finds nothing: the
records tile PM from 0x0030 up, and 0x0000-0x002f -- the 2181's 48-word vector
table -- is simply not present in the image.  The controller supplies it.

Usage: pm3_dp2_unpack.py <image> <out.pm> [--map]
"""
import struct
import sys

PM_WORDS = 0x4000
END_ADDR = 0xf000


def records(data):
    """Yield (IDMA address, transfer count, decoded words) for each record."""
    h = struct.unpack("<%dH" % (len(data) // 2), data[: len(data) // 2 * 2])
    i = 0
    while i + 1 < len(h):
        addr, count = h[i], h[i + 1]
        i += 2
        if i + count > len(h):
            raise ValueError(f"record at byte 0x{(i - 2) * 2:x} overruns image")
        raw = h[i:i + count]
        i += count
        if addr & 0x4000:
            words = list(raw)
        else:
            if count & 1:
                raise ValueError(f"odd PM transfer count {count} at 0x{addr:04x}")
            words = [(raw[j] << 8) | (raw[j + 1] & 0xff)
                     for j in range(0, count, 2)]
        yield addr, count, words
        if addr == 0:
            return


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        return print(__doc__.rstrip()) or 2
    data = open(args[0], "rb").read()
    pm = [0] * PM_WORDS
    for addr, count, words in records(data):
        if "--map" in sys.argv:
            kind = "DM" if addr & 0x4000 else "PM"
            base = addr & 0x3fff
            print("  %04x  %s  %5d transfers  %5d words  -> %04x" %
                  (addr, kind, count, len(words), base + len(words)))
        if addr & 0x4000:
            continue
        base = addr & 0x3fff
        for i, w in enumerate(words):
            if base + i < PM_WORDS:
                pm[base + i] = w
    out = bytearray()
    for w in pm:
        out += bytes([w & 0xFF, (w >> 8) & 0xFF, (w >> 16) & 0xFF])
    open(args[1], "wb").write(bytes(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
