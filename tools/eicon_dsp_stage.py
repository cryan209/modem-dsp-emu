#!/usr/bin/env python3
"""Build the DSP code image the MIPS protocol firmware expects in card RAM.

On a real card the host driver stages a DSP download image at
`DspCodeBaseAddr` before releasing the MIPS, and writes that address into
the protocol image header at `OFFS_DSP_CODE_BASE_ADDR` (0x6c).  The MIPS
entry (`0x80082f90` in te_dmlt.pm) reads the address from the header and
then reads the download table:

    lui   $s1, 0xa001
    lw    $s1, 0x106c($s1)   # protocol image + 0x6c = DspCodeBaseAddr
    ...
    lhu   $s2, ($s1)         # download count
    addiu $s1, $s1, 4        # -> t_dsp_portable_desc[], stride 0x30

Without this image the count reads 0, every DSP object is constructed with
an empty code table (`0x80085394`, fields +0/+4), and no overlay can ever be
assigned — which is the real reason a modem ASSIGN produces no host writes,
independent of how many DSPs the card init enumerates.

The layout reproduces `pri_telindus_load` (kernel/s_pri.c) and
`dsp_read_file` (divactrl/load/common/dsp_file.c) from the shipping Linux
driver:

    +0x0000  dword  download_count
    +0x0004  t_dsp_portable_desc[DSP_MAX_DOWNLOAD_COUNT]   (128 * 0x30)
    +0x1804  section data, dword-aligned, in dsp_read_file order

Each descriptor's seven pointer fields hold the card address of that
section, or 0 when the section is empty (`dsp_card_load_portable`).

Which downloads are staged is decided by the combifile itself: its
directory maps a `card_type_number` (a CARDTYPE_* value, e.g. 23 for
CARDTYPE_DIVASRV_P_30M_PCI) to a file-set number, and each download carries
a usage-mask bit for that file set.

`extra_download_ids` stages downloads the card's own file set does not ask
for.  That is not what a shipping driver does, and it is deliberate: the
protocol image decides what a channel is capable of by *searching this
table*, so a download the table does not carry is a capability the firmware
reports as unsupported.  V.90A is the case that matters — `te_dmlt.pm` looks
up id `0x026b` at `0x80091f9c` and traces "V.90A not supported" when the
search fails, and file set 5 (the PRI) is simply the set that omits it.
"""

from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eicon_dsp_extract import FILE_HEADER, FormatError, parse_combifile

# kernel/dsp_defs.h
DSP_MAX_DOWNLOAD_COUNT = 128
PORTABLE_DESC = struct.Struct("<10H7I")  # t_dsp_portable_desc, 0x30 bytes
assert PORTABLE_DESC.size == 0x30

# kernel/mi_pc.h
OFFS_DSP_CODE_BASE_ADDR = 0x6C
OFFS_PROTOCOL_END_ADDR = 0x7C

# kernel/cardtype.h: CARDTYPE_DIVASRV_P_30M_PCI, the card te_dmlt.pm targets.
CARDTYPE_DIVASRV_P_30M_PCI = 23

# The task kernel every modem overlay runs under.  The combifile ships four
# variants of it under this one id (V90, C34, F34, ANA), and which one a file
# set selects is what makes two file sets' overlays interchangeable or not.
DOWNLOAD_TASK_KERNEL = 0x0258

# V.90 APCM, the analogue-side V.90 overlay.  Not in the PRI's file set.
DOWNLOAD_V90_APCM = 0x026B


def _align4(value: int) -> int:
    return (value + 3) & ~3


@dataclass
class StagedDownload:
    download_id: int
    description: str
    address: int
    size: int


@dataclass
class DspCodeImage:
    base_addr: int
    data: bytes
    card_type: int
    file_set: int
    downloads: list[StagedDownload] = field(default_factory=list)
    # {download_id: {dm address: tuple of words}} for every staged download's
    # DM blocks. A partial overlay repeats whole blocks of the page it extends
    # -- byte for byte -- and re-applying those over a page that has already
    # run its init resets the page's own workspace. The loader needs to be able
    # to tell a partial's new content from its duplication; see
    # NativeMipsModem._service_partial_overlay(). Session 186.
    dm_blocks: dict = field(default_factory=dict)

    @property
    def end_addr(self) -> int:
        return self.base_addr + len(self.data)


def required_downloads(combi: dict, card_type: int) -> tuple[list[dict], int]:
    """Select the downloads the combifile marks as required for a card type."""
    file_set = None
    for entry in combi["directory"]:
        if entry["card_type"] == card_type:
            file_set = entry["file_set"]
            break
    if file_set is None:
        raise FormatError(f"card type {card_type} is not in the combifile directory")
    mask_offset, mask_bit = file_set // 8, 1 << (file_set & 7)
    if mask_offset >= combi["usage_mask_size"]:
        raise FormatError(
            f"card type {card_type} maps to invalid file set {file_set}"
        )
    selected = [
        download
        for download in combi["downloads"]
        if bytes.fromhex(download["usage_mask"])[mask_offset] & mask_bit
    ]
    return selected, file_set


def _file_sets(combi: dict, download: dict) -> set[int]:
    """The file-set numbers whose usage-mask bit this download carries."""
    mask = bytes.fromhex(download["usage_mask"])
    return {index for index in range(len(mask) * 8)
            if mask[index // 8] & (1 << (index & 7))}


def _compatible_file_sets(combi: dict, file_set: int) -> set[int]:
    """File sets running the same task kernel variant as `file_set`.

    An overlay is code for a task, so the question "may this file set's
    overlay be staged for that card" is the question "do the two run the same
    0x0258 task kernel".  File set 5 (PRI) selects TIKRNL81.F34, and so do
    9..12 and 15, which is why the V.90 APCM overlay those carry is the same
    kind of object as the V.90 DPCM overlay the PRI already runs.  The .ANA
    variants of both are a different family and are excluded by this.
    """
    for download in combi["downloads"]:
        if download["download_id"] != DOWNLOAD_TASK_KERNEL:
            continue
        sets = _file_sets(combi, download)
        if file_set in sets:
            return sets
    return {file_set}


def resolve_extra_download(combi: dict, download_id: int, file_set: int,
                           selected: list[dict]) -> dict:
    """Pick the variant of `download_id` that fits `file_set`'s task family.

    The combifile ships several downloads under one id — 0x026b is both
    "V.90 APCM Overlay" and "V90.ANA APCM Overlay" — so an id alone does not
    name a record.  Resolving it against the file sets that share this one's
    task kernel is what makes the choice determinate rather than a guess;
    an ambiguous or empty result is an error, not a default.
    """
    if any(download["download_id"] == download_id for download in selected):
        raise FormatError(
            f"download 0x{download_id:04x} is already in file set {file_set}"
        )
    family = _compatible_file_sets(combi, file_set)
    candidates = [download for download in combi["downloads"]
                  if download["download_id"] == download_id
                  and _file_sets(combi, download) & family]
    if not candidates:
        present = [f"0x{d['download_id']:04x} {d['description']}"
                   for d in combi["downloads"]
                   if d["download_id"] == download_id]
        raise FormatError(
            f"no variant of download 0x{download_id:04x} belongs to a file set "
            f"sharing file set {file_set}'s task kernel"
            + (f"; the file has " + ", ".join(present) if present else "")
        )
    if len(candidates) > 1:
        raise FormatError(
            f"download 0x{download_id:04x} is ambiguous within file set "
            f"{file_set}'s task family: "
            + ", ".join(d["description"] for d in candidates)
        )
    return candidates[0]


def build_dsp_code_image(
    combifile: Path,
    card_type: int = CARDTYPE_DIVASRV_P_30M_PCI,
    base_addr: int = 0,
    max_download_count: int = DSP_MAX_DOWNLOAD_COUNT,
    extra_download_ids: "tuple[int, ...] | list[int]" = (),
) -> DspCodeImage:
    """Lay out the count + descriptor table + section data for `card_type`.

    `base_addr` is the card address the image will be written to; the
    descriptor pointers are absolute card addresses, so it must match where
    the image is actually placed.

    `extra_download_ids` appends downloads the card's file set does not
    select.  Order does not matter to the firmware, which searches the table
    by id rather than indexing it.
    """
    combi = parse_combifile(combifile)
    selected, file_set = required_downloads(combi, card_type)
    extra = [resolve_extra_download(combi, download_id, file_set, selected)
             for download_id in extra_download_ids]
    selected = selected + extra
    if len(selected) > max_download_count:
        raise FormatError(
            f"download table overflow: {len(selected)} required downloads "
            f"exceed the {max_download_count}-entry table"
        )

    table_bytes = 4 + max_download_count * PORTABLE_DESC.size
    payload = bytearray()
    payload_base = _align4(base_addr + table_bytes)
    descriptors: list[bytes] = []
    staged: list[StagedDownload] = []

    def append_section(data: bytes) -> int:
        """Stage one section; returns its card address (0 when empty).

        Mirrors dsp_card_load_portable + pri_download_buffer: empty sections
        anchor at NULL, and the download pointer is dword-aligned after each.
        """
        if not data:
            return 0
        address = payload_base + len(payload)
        payload.extend(data)
        while len(payload) % 4:
            payload.append(0)
        return address

    for download in selected:
        raw = download["raw"]
        fields = FILE_HEADER.unpack_from(raw, 0)[1:]
        (
            _version,
            download_id,
            flags,
            processing_power,
            interface_channels,
            header_size,
            description_size,
            memory_table_size,
            memory_count,
            segment_table_size,
            segment_count,
            symbol_table_size,
            symbol_count,
            dm_size,
            dm_count,
            pm_size,
            pm_count,
        ) = fields
        excess_header_size = header_size - FILE_HEADER.size

        # Sections in the order dsp_read_file streams them out of the file.
        pos = FILE_HEADER.size
        sections = []
        for length in (
            excess_header_size,
            description_size,
            memory_table_size,
            segment_table_size,
            symbol_table_size,
            dm_size,
            pm_size,
        ):
            sections.append(raw[pos : pos + length])
            pos += length
        if pos != len(raw):
            raise FormatError(
                f"download 0x{download_id:04x}: section sizes sum to 0x{pos:x}, "
                f"record is 0x{len(raw):x} bytes"
            )

        start = payload_base + len(payload)
        pointers = [append_section(section) for section in sections]
        descriptors.append(
            PORTABLE_DESC.pack(
                download_id,
                flags,
                processing_power,
                interface_channels,
                excess_header_size,
                memory_count,
                segment_count,
                symbol_count,
                dm_count,
                pm_count,
                *pointers,
            )
        )
        staged.append(
            StagedDownload(
                download_id=download_id,
                description=download["description"],
                address=start,
                size=payload_base + len(payload) - start,
            )
        )

    image = bytearray(struct.pack("<I", len(selected)))
    for descriptor in descriptors:
        image.extend(descriptor)
    image.extend(bytes(table_bytes - len(image)))
    image.extend(bytes(payload_base - (base_addr + len(image))))
    image.extend(payload)

    return DspCodeImage(
        base_addr=base_addr,
        data=bytes(image),
        card_type=card_type,
        file_set=file_set,
        downloads=staged,
        dm_blocks={
            download["download_id"]: {
                block.address: tuple(block.values)
                for block in download["dm_blocks"] if block.domain == "dm"
            }
            for download in selected
        },
    )


def protocol_end_addr(image: Path) -> int:
    """DspCodeBaseAddr the driver derives from the protocol image header."""
    header = image.read_bytes()[:0x80]
    end = struct.unpack_from("<I", header, OFFS_PROTOCOL_END_ADDR)[0]
    if end == 0:
        raise FormatError("protocol image declares no end address")
    return _align4(end)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("combifile", type=Path)
    parser.add_argument("--card-type", type=lambda s: int(s, 0),
                        default=CARDTYPE_DIVASRV_P_30M_PCI)
    parser.add_argument("--image", type=Path,
                        default=Path("docs/firmware/te_dmlt.pm"),
                        help="protocol image the base address is derived from")
    parser.add_argument("--base", type=lambda s: int(s, 0), default=None,
                        help="override DspCodeBaseAddr")
    parser.add_argument("--extra-download", metavar="ID", action="append",
                        type=lambda s: int(s, 0), default=[],
                        help="stage a download the card's file set does not "
                             "select, e.g. 0x026b for the V.90 APCM overlay. "
                             "Repeatable")
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()

    base = args.base if args.base is not None else protocol_end_addr(args.image)
    dsp_image = build_dsp_code_image(args.combifile, args.card_type, base,
                                     extra_download_ids=args.extra_download)
    print(f"card type {dsp_image.card_type} -> file set {dsp_image.file_set}: "
          f"{len(dsp_image.downloads)} downloads")
    print(f"DspCodeBaseAddr 0x{dsp_image.base_addr:08x}..0x{dsp_image.end_addr:08x} "
          f"({len(dsp_image.data)} bytes)")
    for entry in dsp_image.downloads:
        print(f"  id=0x{entry.download_id:04x} @0x{entry.address:08x} "
              f"{entry.size:7d}  {entry.description}")
    if args.output:
        args.output.write_bytes(dsp_image.data)
        print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
