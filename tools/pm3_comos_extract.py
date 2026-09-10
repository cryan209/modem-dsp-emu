#!/usr/bin/env python3
"""Unpack a Livingston/Lucent PortMaster ComOS upgrade image.

The distributed .upg file is a text archive: `#` comment lines, `file <name>`
headers, and a uuencoded body per section terminated by a short line.  Most
sections are themselves a run of gzip members, each carrying the original
filename of the component -- the ComOS executable, the modem controller image
and the ADSP-2181 data pump / WAN control overlays.

The 2181 images are record-structured download images, not flat PM: pairs of
16-bit little-endian values, each pair one 24-bit word, with record headers
carrying a PM load address.  --pack writes an assembled flat `.pm` beside each
such file -- the form tools/adsp2181_dis.py and the emulator core load.  See
docs/pm3_comos_contents.md and tools/pm3_dp2_unpack.py for the format.

Usage: pm3_comos_extract.py <image> <outdir> [--pack]
"""
import binascii
import os

import pm3_dp2_unpack
import sys
import zlib


def sections(path):
    """Yield (name, decoded_bytes) for every `file` section in the archive."""
    name, body = None, bytearray()
    for line in open(path, "rb"):
        line = line.rstrip(b"\r\n")
        if line.startswith(b"file "):
            if name is not None:
                yield name, bytes(body)
            name, body = line[5:].decode().strip(), bytearray()
            continue
        if name is None or line.startswith(b"#") or line == b"end":
            continue
        if not line or line == b"`":
            continue
        try:
            body += binascii.a2b_uu(line)
        except binascii.Error:
            pass                        # padding / stray short line
    if name is not None:
        yield name, bytes(body)


def members(blob):
    """Yield (filename, decompressed) for each gzip member packed in blob."""
    pos = 0
    while True:
        start = blob.find(b"\x1f\x8b\x08", pos)
        if start < 0:
            return
        flg = blob[start + 3]
        p = start + 10
        inner = ""
        if flg & 8:                     # FNAME: the component's own filename
            end = blob.find(b"\0", p)
            if end < 0:
                return
            inner = blob[p:end].decode(errors="replace")
            p = end + 1
        obj = zlib.decompressobj(-15)   # raw deflate, header already skipped
        try:
            data = obj.decompress(blob[p:])
            obj.flush()
        except zlib.error:
            pos = start + 3             # not a member, just a matching byte run
            continue
        yield inner, data
        # 8 trailing bytes of CRC32 + ISIZE follow the deflate stream.
        pos = len(blob) - len(obj.unused_data) + 8

def is_adsp2181(data):
    """True when the file reads as 16-bit pairs holding 24-bit words.

    A pair's second half carries the word's low byte, so its high byte is zero
    except on record headers -- a few per cent of pairs.
    """
    if len(data) < 64:
        return False
    pairs = len(data) // 4
    zero = sum(1 for i in range(3, pairs * 4, 4) if data[i] == 0)
    return zero >= pairs * 0.9


def pack24(data):
    """Assemble the record stream into a flat 16K-word PM image."""
    pm = [0] * pm3_dp2_unpack.PM_WORDS
    for addr, _tag, words in pm3_dp2_unpack.records(data):
        for i, w in enumerate(words):
            if addr + i < pm3_dp2_unpack.PM_WORDS:
                pm[addr + i] = w
    return b"".join(bytes([w & 0xFF, (w >> 8) & 0xFF, (w >> 16) & 0xFF])
                    for w in pm)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pack = "--pack" in sys.argv[1:]
    if len(args) != 2:
        sys.exit(__doc__)
    image, outdir = args
    os.makedirs(outdir, exist_ok=True)

    for name, blob in sections(image):
        safe = name.strip("/").replace("/", "_")
        found = False
        for inner, data in members(blob):
            found = True
            out = os.path.join(outdir, f"{safe}.{inner}" if inner else safe)
            open(out, "wb").write(data)
            note = ""
            if is_adsp2181(data):
                note = f"  ADSP-2181, {len(data) // 4} words"
                if pack:
                    open(out.rsplit(".", 1)[0] + ".pm"
                         if out.endswith(".bin") else out + ".pm",
                         "wb").write(pack24(data))
            print(f"{name:24s} -> {os.path.basename(out):24s} "
                  f"{len(data):8d}{note}")
        if not found:
            out = os.path.join(outdir, safe)
            open(out, "wb").write(blob)
            note = f"  ADSP-2181, {len(blob) // 4} words" if is_adsp2181(blob) else ""
            if is_adsp2181(blob) and pack:
                open(out + ".pm", "wb").write(pack24(blob))
            print(f"{name:24s} -> {os.path.basename(out):24s} "
                  f"{len(blob):8d}{note}")


if __name__ == "__main__":
    main()
