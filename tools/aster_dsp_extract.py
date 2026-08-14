#!/usr/bin/env python3
"""Parse the Telindus IDMA/BDMA boot file format (addspv90guide.pdf section 6.1).

This is the container the Telindus Aster DSP images ship in, the sibling of the
Eicon combifile handled by tools/eicon_dsp_extract.py. Both describe ADSP-218x
PM and DM loads; this one is organised by the guide's bootpage numbers, which
is what makes it directly comparable to the Eicon overlay set.

    ./tools/aster_dsp_extract.py "docs/firmware/Aster 5 DSP/T8660014.00"
    ./tools/aster_dsp_extract.py <file> --image 0 --page 6 -o /tmp/aster-v8

All multi-byte fields are big-endian.
"""

import argparse
import json
import os
import struct
import sys

# addspv90guide.pdf, Table 1 (reproduced in docs/dial_v8_call.md).
PAGE_NAMES = {
    0: "DIAL (idle)",
    1: "V.22",
    2: "V.32",
    3: "FSK",
    4: "FAX",
    6: "V.8",
    7: "INFO",
    8: "V.34",
    9: "STARTUP",
    11: "AT-set offline",
    12: "AT-set online",
    13: "V.90A",
    14: "V.90D",
    15: "fax protocol",
    16: "LL page",
}

HEADER_LEN_MIN = 22  # through the maximum-pageblock-length field


class FormatError(Exception):
    pass


def _be16(buf, off):
    return struct.unpack_from(">H", buf, off)[0]


def _be32(buf, off):
    return struct.unpack_from(">I", buf, off)[0]


def _ascii(buf, off, n):
    return buf[off:off + n].split(b"\x00")[0].decode("ascii", "replace").strip()


class Header:
    """Section 6.1.1 header table."""

    def __init__(self, buf, base):
        if base + HEADER_LEN_MIN > len(buf):
            raise FormatError(f"header at 0x{base:x} runs past end of file")
        self.base = base
        self.header_len = _be16(buf, base)
        self.body_bytes = _be32(buf, base + 2)
        self.checksum = _be32(buf, base + 6)
        self.version = _be16(buf, base + 10)
        self.version_xx = buf[base + 12]
        self.year = buf[base + 13]
        self.month = buf[base + 14]
        self.day = buf[base + 15]
        self.hour = buf[base + 16]
        self.minute = buf[base + 17]
        self.file_format = _be16(buf, base + 18)
        self.max_pageblock = _be16(buf, base + 20)
        self.config = _ascii(buf, base + 22, 16)
        self.rcs = _ascii(buf, base + 38, 16)
        if self.header_len < HEADER_LEN_MIN:
            raise FormatError(f"header length {self.header_len} too small at 0x{base:x}")
        if base + self.header_len + self.body_bytes > len(buf):
            raise FormatError(f"image at 0x{base:x} declares {self.body_bytes} body bytes, past end of file")

    @property
    def body_base(self):
        return self.base + self.header_len

    @property
    def end(self):
        return self.body_base + self.body_bytes

    @property
    def has_overlay_field(self):
        """File format bit 0: the IDMA overlay word is present in every pageblock."""
        return bool(self.file_format & 0x0001)

    @property
    def compact_pm(self):
        """File format bit 8: program code is 3 bytes per instruction, not 4."""
        return bool(self.file_format & 0x0100)

    def timestamp(self):
        # All BCD; the year is the low byte of the version/year word.
        def bcd(v):
            return f"{v >> 4:x}{v & 0xF:x}"
        return f"20{bcd(self.year)}-{bcd(self.month)}-{bcd(self.day)} {bcd(self.hour)}:{bcd(self.minute)}"


class PageBlock:
    """One pageblock: length / [IDMA overlay] / IDMA control / data."""

    def __init__(self, length, overlay, control, data_off):
        self.length = length          # 16-bit words of payload
        self.overlay = overlay        # None when file format bit 0 is clear
        self.control = control
        self.data_off = data_off

    @property
    def is_dm(self):
        """IDMA control b14: DM when set, PM when clear (2187 register 0x3fe0)."""
        return bool(self.control & 0x4000)

    @property
    def address(self):
        return self.control & 0x3FFF

    @property
    def pmovlay(self):
        return None if self.overlay is None else self.overlay & 0x000F

    @property
    def dmovlay(self):
        return None if self.overlay is None else (self.overlay >> 4) & 0x000F


class Page:
    def __init__(self, index, offset_words, blocks):
        self.index = index
        self.offset_words = offset_words
        self.blocks = blocks

    @property
    def name(self):
        if self.index is None:
            return "STARTUP"
        return PAGE_NAMES.get(self.index, f"page {self.index}")

    def word_counts(self, compact_pm=False):
        """DM words, and PM *instructions*.

        Pageblock lengths are 16-bit words throughout, but a PM instruction is
        24 bits: 4 bytes in the standard format, 3 in the compact one. Reporting
        instructions is what makes the count comparable to an Eicon overlay's.
        """
        dm = sum(b.length for b in self.blocks if b.is_dm)
        pm_words = sum(b.length for b in self.blocks if not b.is_dm)
        pm = pm_words * 2 // 3 if compact_pm else pm_words // 2
        return dm, pm


class Image:
    def __init__(self, buf, base):
        self.header = Header(buf, base)
        h = self.header
        body = h.body_base
        # Section 6.1.2 intro part, renumbered from the start of the body table.
        self.startup_offset = _be32(buf, body)
        self.index_len = _be16(buf, body + 4)
        self.pages = []
        for i in range(self.index_len):
            off = _be32(buf, body + 6 + 4 * i)
            if off == 0:
                continue  # "A 0 means that the page is not contained in the file"
            self.pages.append(Page(i, off, self._read_page(buf, body + 2 * off)))
        self.startup = None
        if self.startup_offset:
            self.startup = Page(None, self.startup_offset,
                                self._read_page(buf, body + 2 * self.startup_offset))

    def _read_page(self, buf, off):
        blocks = []
        stride = 3 if self.header.has_overlay_field else 2
        while True:
            length = _be16(buf, off)
            if length == 0:
                break  # "a 0 page block length indicates the end of this page"
            overlay = _be16(buf, off + 2) if self.header.has_overlay_field else None
            control = _be16(buf, off + 2 * (stride - 1))
            data_off = off + 2 * stride
            blocks.append(PageBlock(length, overlay, control, data_off))
            off = data_off + 2 * length
            if off > len(buf):
                raise FormatError(f"pageblock at 0x{off:x} runs past end of file")
        return blocks

    def all_pages(self):
        return ([self.startup] if self.startup else []) + self.pages


def parse(buf):
    """Return the images in a boot file.

    The file opens with a 12-byte outer preamble (magic, header offset, total
    length of the first image, checksum). Further images follow the first one
    directly, with no preamble of their own.
    """
    if len(buf) < 12:
        raise FormatError("file too short")
    magic = _be16(buf, 0)
    header_off = _be16(buf, 2)
    if header_off < 12 or header_off > len(buf):
        raise FormatError(f"implausible header offset 0x{header_off:x}; not a Telindus boot file?")
    images = []
    base = header_off
    while base + HEADER_LEN_MIN <= len(buf):
        img = Image(buf, base)
        images.append(img)
        if img.header.end <= base:
            raise FormatError(f"image at 0x{base:x} makes no progress")
        base = img.header.end
    return magic, images


def _range(page, dm):
    """Lowest and highest address touched in one space, or None."""
    addrs = [b.address for b in page.blocks if b.is_dm == dm]
    if not addrs:
        return None
    ends = [b.address + b.length for b in page.blocks if b.is_dm == dm]
    return [min(addrs), max(ends) - 1]


def _extract(image, page, buf, outdir):
    """Write one page's DM and PM loads as flat images.

    DM is 16-bit words; PM instructions are 24 bits, stored either 4 bytes with
    a zero pad (standard) or 3 bytes (compact), per file format bit 8.
    """
    os.makedirs(outdir, exist_ok=True)
    dm = bytearray()
    pm = bytearray()
    for b in page.blocks:
        raw = buf[b.data_off:b.data_off + 2 * b.length]
        (dm if b.is_dm else pm).extend(raw)
    written = []
    for name, blob in (("dm.bin", dm), ("pm.bin", pm)):
        if blob:
            path = os.path.join(outdir, name)
            with open(path, "wb") as fh:
                fh.write(blob)
            written.append(path)
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path")
    ap.add_argument("--image", type=int, help="restrict to one image index")
    ap.add_argument("--page", type=int, help="restrict to one page index")
    ap.add_argument("--blocks", action="store_true", help="list every pageblock")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-o", "--out", help="write dm.bin/pm.bin per page under this directory")
    args = ap.parse_args(argv)

    with open(args.path, "rb") as fh:
        buf = fh.read()
    try:
        magic, images = parse(buf)
    except FormatError as exc:
        print(f"{args.path}: {exc}", file=sys.stderr)
        return 2

    report = {"file": args.path, "size": len(buf), "magic": magic, "images": []}
    for idx, img in enumerate(images):
        if args.image is not None and idx != args.image:
            continue
        h = img.header
        entry = {
            "index": idx,
            "offset": h.base,
            "config": h.config,
            "rcs": h.rcs,
            "checksum": h.checksum,
            "built": h.timestamp(),
            "body_bytes": h.body_bytes,
            "file_format": h.file_format,
            "overlay_field": h.has_overlay_field,
            "compact_pm": h.compact_pm,
            "max_pageblock": h.max_pageblock,
            "pages": [],
        }
        for page in img.all_pages():
            if args.page is not None and page.index != args.page:
                continue
            dm_words, pm_instrs = page.word_counts(h.compact_pm)
            pentry = {
                "index": page.index,
                "name": page.name,
                "offset_words": page.offset_words,
                "blocks": len(page.blocks),
                "dm_words": dm_words,
                "pm_instrs": pm_instrs,
                "dm_range": _range(page, True),
                "pm_range": _range(page, False),
            }
            if args.blocks:
                pentry["block_list"] = [
                    {"space": "DM" if b.is_dm else "PM", "address": b.address,
                     "words": b.length, "pmovlay": b.pmovlay, "dmovlay": b.dmovlay}
                    for b in page.blocks
                ]
            if args.out:
                slug = page.name.split(" ")[0].lower().replace(".", "")
                name = "startup" if page.index is None else f"{page.index:02}-{slug}"
                sub = os.path.join(args.out, f"image{idx}", name)
                pentry["files"] = _extract(img, page, buf, sub)
            entry["pages"].append(pentry)
        report["images"].append(entry)

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"{args.path}: {len(buf)} bytes, magic 0x{magic:04x}, {len(images)} image(s)")
    for entry in report["images"]:
        print(f"\nimage {entry['index']} @ 0x{entry['offset']:x}  {entry['config']}  {entry['rcs']}")
        print(f"  built {entry['built']}  checksum H#{entry['checksum']:08X}  body {entry['body_bytes']} bytes")
        print(f"  file format 0x{entry['file_format']:04x} "
              f"(overlay field {'yes' if entry['overlay_field'] else 'no'}, "
              f"PM {'compact' if entry['compact_pm'] else 'standard'}), "
              f"max pageblock {entry['max_pageblock']} bytes")
        print(f"  {'page':>5}  {'name':<16} {'blocks':>6} {'DM words':>9} {'PM instrs':>10}"
              f"  {'DM range':<15} {'PM range':<15}")
        for p in entry["pages"]:
            num = "S" if p["index"] is None else str(p["index"])
            def rng(r):
                return "-" if r is None else f"0x{r[0]:04x}-0x{r[1]:04x}"
            print(f"  {num:>5}  {p['name']:<16} {p['blocks']:>6} {p['dm_words']:>9} {p['pm_instrs']:>10}"
                  f"  {rng(p['dm_range']):<15} {rng(p['pm_range']):<15}")
            if args.blocks:
                for b in p["block_list"]:
                    ov = "" if b["pmovlay"] is None else f"  pmovlay {b['pmovlay']} dmovlay {b['dmovlay']}"
                    print(f"        {b['space']} 0x{b['address']:04x}  {b['words']:>5} words{ov}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
