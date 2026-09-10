#!/usr/bin/env python3
"""Unpack a PortMaster ADSP-2181 download image (dp2.bin, 2181_*.bin/.ovl).

These are not flat PM images.  The file is a stream of 16-bit little-endian
values read as pairs: a pair holds one 24-bit ADSP-2181 word as

    word = (first << 8) | (second & 0x00ff)

so the high byte of the *second* half is normally zero.  A non-zero high byte
there marks a record header, whose first half is the record's PM load address:

    <addr:16> <tag:16> <word:24 as a pair> ...

The record runs until the next header.  Lengths are not stored -- consecutive
record addresses are contiguous, so a record's length is the gap to the next
header, and the address 0xf000 terminates the stream.

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
    """Yield (addr, tag, [24-bit words]) for every record in the image."""
    h = struct.unpack("<%dH" % (len(data) // 2), data[: len(data) // 2 * 2])
    pairs = len(h) // 2
    k = 0
    while k < pairs:
        addr, tag = h[2 * k], h[2 * k + 1]
        if addr == END_ADDR:
            return
        j = k + 1
        while j < pairs and (h[2 * j + 1] >> 8) == 0:
            j += 1
        yield addr, tag, [(h[2 * p] << 8) | (h[2 * p + 1] & 0xFF)
                          for p in range(k + 1, j)]
        if j >= pairs or h[2 * j] < addr:
            return                      # end of PM records; DM data follows
        k = j


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        return print(__doc__.rstrip()) or 2
    data = open(args[0], "rb").read()
    pm = [0] * PM_WORDS
    for addr, tag, words in records(data):
        if "--map" in sys.argv:
            print("  %04x  tag=%04x  %5d words  -> %04x" %
                  (addr, tag, len(words), addr + len(words)))
        for i, w in enumerate(words):
            if addr + i < PM_WORDS:
                pm[addr + i] = w
    out = bytearray()
    for w in pm:
        out += bytes([w & 0xFF, (w >> 8) & 0xFF, (w >> 16) & 0xFF])
    open(args[1], "wb").write(bytes(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
