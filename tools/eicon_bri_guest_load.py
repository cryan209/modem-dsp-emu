"""Prepare archived Linux divas admin packets for genuine guest-side loading.

The initial board backend stays empty. Only the guest driver writes these
legacy firmware bytes through BAR2 and issues the physical START sequence.
"""
import argparse
import json
from pathlib import Path
import struct
from eicon_bri_card import BriCard
from eicon_bri_legacy import stage


def build(firmware_dir, output, wire_pairs=False, fpga_image=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    commands = []

    def packet(name, command, words=(0, 0, 0), payload=b''):
        data = struct.pack('<6I', 1, command, *words, len(payload)) + payload
        (output / name).write_bytes(data)
        commands.append(f'/bin/busybox dd if=/bri-load/{name} bs={len(data)} count=1 >&3')

    script = ['#!/bin/sh', 'set -eu',
              'major=',
              'while read number name; do',
              '    if [ \"$name\" = Divas ]; then major=$number; break; fi',
              'done </proc/devices',
              '[ -n "$major" ]', 'mknod /dev/Divas c "$major" 0',
              'exec 3<>/dev/Divas']
    packet('ordinal.bin', 0)
    script += commands; commands.clear()
    script += ['/bin/busybox dd bs=4 count=1 <&3 >/dev/null']
    packet('reset.bin', 6)
    if fpga_image is not None:
        image = Path(fpga_image).read_bytes()
        packet('fpga.bin', 2, (0, len(image), 0), image)
        commands.append('echo BRI_FPGA_DOWNLOAD_OK')
    with BriCard() as card:
        layout = stage(card, firmware_dir)
        if wire_pairs:
            for controller in range(4):
                card.configure_port(controller, 'nt' if controller & 1 else 'te')
        size = 65536
        blocks = 0
        for offset in range(0, len(card.ram), size):
            payload = bytes(card.ram[offset:offset + size]).rstrip(b'\0')
            if not any(payload):
                continue
            if offset + len(payload) >= len(card.ram):
                raise ValueError('driver disallows a write ending at the SDRAM limit')
            packet(f'ram-{offset:08x}.bin', 1, (0, offset, len(payload)), payload)
            blocks += 1
    packet('start.bin', 7, (0, 0xff, 0))
    script += commands
    script += ['echo BRI_FIRMWARE_START_RETURNED',
               'for info in /proc/net/eicon/adapter*/info; do cat "$info"; done',
               'cat /proc/interrupts', 'echo BRI_FIRMWARE_LOAD_OK']
    (output / 'run').write_text('\n'.join(script) + '\n')
    (output / 'run').chmod(0o755)
    manifest = dict(layout=layout['layout'], downloads=layout['downloads'],
                    blocks=blocks, block_size=size, firmware_staged_by='guest divas driver',
                    port_roles=['te', 'nt', 'te', 'nt'] if wire_pairs else ['te'] * 4,
                    fpga_image=str(fpga_image) if fpga_image is not None else None)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware-dir', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--wire-pairs', action='store_true', help='configure TE/NT pairs for the explicit external wire fixture')
    parser.add_argument('--fpga-image', type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.firmware_dir, args.out, args.wire_pairs, args.fpga_image)))
