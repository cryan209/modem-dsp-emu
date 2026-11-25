"""Stage the shipping four-image 4BRI layout and distributed DSP sections.

Follows qBri_protocol_load/qBri_download_buffer in kernel/s_4bri.c:
the table lives in the highest controller's free region, while each DSP
section uses the first controller region with sufficient remaining room.
"""
from pathlib import Path
import struct
from eicon_bri_card import MEMORY_SIZE, INSTANCE_SIZE, SHARED_SIZE
from eicon_dsp_extract import FILE_HEADER, parse_combifile
from eicon_dsp_stage import (build_dsp_code_image, required_downloads,
                             PORTABLE_DESC, DSP_MAX_DOWNLOAD_COUNT)


def stage(card, firmware_dir, card_type=53, law='pcmu'):
    if card.reentrant or card.cpu_running:
        raise ValueError('legacy staging requires a halted legacy board')
    firmware_dir = Path(firmware_dir)
    layouts = [card.stage_protocol(i, firmware_dir / f'te_dmlt.2q{i}') for i in range(4)]
    bases = [(layout.protocol_end + 3) & ~3 for layout in layouts]
    limits = [0x80000000 + (i + 1) * INSTANCE_SIZE - SHARED_SIZE for i in range(4)]
    table_base = bases[-1]
    table_bytes = 4 + DSP_MAX_DOWNLOAD_COUNT * PORTABLE_DESC.size
    cursors = list(bases)
    cursors[-1] = (cursors[-1] + table_bytes + 3) & ~3
    if any(not (0x80000000 + i * INSTANCE_SIZE <= base < limit)
           for i, (base, limit) in enumerate(zip(bases, limits))):
        raise ValueError('legacy protocol regions do not fit SDRAM')
    combifile = firmware_dir / 'dspdload.bin'
    flat = build_dsp_code_image(combifile, card_type, 0)
    downloads, _ = required_downloads(parse_combifile(combifile), card_type)
    table = bytearray(struct.pack('<I', len(downloads)))
    sections = []
    for index, download in enumerate(downloads):
        descriptor = PORTABLE_DESC.unpack_from(flat.data, 4 + index * PORTABLE_DESC.size)
        fields = FILE_HEADER.unpack_from(download['raw'])[1:]
        lengths = (fields[5] - FILE_HEADER.size, fields[6], fields[7], fields[9],
                   fields[11], fields[13], fields[15])
        pointers = []
        for old_pointer, length in zip(descriptor[10:], lengths):
            if not length:
                pointers.append(0)
                continue
            region = next((i for i in range(4) if length <= limits[i] - cursors[i]), None)
            if region is None:
                raise ValueError('distributed DSP image exceeds board SDRAM')
            address = cursors[region]
            data = flat.data[old_pointer:old_pointer + length]
            if len(data) != length:
                raise ValueError('truncated serialized DSP section')
            offset = address & 0x1fffffff
            card.ram[offset:offset + length] = data
            cursors[region] = (address + length + 3) & ~3
            pointers.append(address)
            sections.append(dict(download=download['download_id'], region=region,
                                 address=address, length=length))
        table.extend(PORTABLE_DESC.pack(*descriptor[:10], *pointers))
    table.extend(bytes(table_bytes - len(table)))
    offset = table_base & 0x1fffffff
    if offset + len(table) > MEMORY_SIZE:
        raise ValueError('DSP table exceeds board SDRAM')
    card.ram[offset:offset + len(table)] = table
    for i in range(4):
        protocol = i * INSTANCE_SIZE
        card.ram[protocol + 0x68:protocol + 0x6a] = bytes((4, card_type))
        struct.pack_into('<I', card.ram, protocol + 0x6c, table_base)
        shared = card.controller_memory(i, shared=True)
        shared[:256] = bytes(256)
        shared[19] = 0x81
        card.configure_port(i, law=law)
    return dict(layout='legacy', table_base=table_base, downloads=len(downloads),
                regions=[dict(base=base, end=cursor, limit=limit)
                         for base, cursor, limit in zip(bases, cursors, limits)], sections=sections)
