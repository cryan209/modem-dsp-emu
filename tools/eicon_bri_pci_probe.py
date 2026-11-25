"""Read PCI header/VPD from the stopped, identity-checked spare SN12196.

Only VPD address selections with bit 15 CLEAR are written. No VPD data or
EEPROM write request is ever issued. The caller restores stock firmware.
"""
import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import time
from eicon_dsp_bench_card import check_adapter


def read_vpd(read, write, sleep=time.sleep):
    saved = struct.unpack('<H', read(0x4e, 2))[0] & 0x7fff
    words = []
    try:
        for offset in range(0, 256, 4):
            write(0x4e, struct.pack('<H', offset))
            for _ in range(5):
                sleep(0.001)
                status = struct.unpack('<H', read(0x4e, 2))[0]
                if status & 0x8000:
                    break
            else:
                raise RuntimeError(f'VPD read timed out at {offset:#x}')
            words.append(read(0x50, 4))
    finally:
        # Restore the address as a READ, never the read-complete flag.
        write(0x4e, struct.pack('<H', saved))
    return b''.join(words)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pci', required=True)
    parser.add_argument('--adapter', required=True, type=int)
    parser.add_argument('--serial', required=True, type=int)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    if (args.pci, args.adapter, args.serial) != ('0000:02:02.0', 5, 12196):
        raise SystemExit('PCI/VPD probing is restricted to spare SN12196')
    check_adapter(args.pci, args.adapter, args.serial)
    state = subprocess.check_output(['/usr/lib/divas/divactrl', 'load', '-c', '5',
                                     '-CardState'], text=True).strip()
    if state != 'ready':
        raise SystemExit(f'spare must be stopped before probing; state={state!r}')
    fd = os.open(f'/sys/bus/pci/devices/{args.pci}/config', os.O_RDWR)
    try:
        def read(offset, count):
            data = os.pread(fd, count, offset)
            if len(data) != count:
                raise RuntimeError('short PCI configuration read')
            return data
        def write(offset, data):
            if offset != 0x4e or len(data) != 2 or struct.unpack('<H', data)[0] & 0x8000:
                raise RuntimeError('only read-only VPD address selection is allowed')
            if os.pwrite(fd, data, offset) != len(data):
                raise RuntimeError('short PCI configuration write')
        header = read(0, 256)
        vpd = read_vpd(read, write)
    finally:
        os.close(fd)
    serial = struct.unpack_from('<I', vpd, 252)[0]
    if serial != args.serial:
        raise RuntimeError(f'VPD serial {serial} differs from identity-checked spare')
    args.out.write_text(json.dumps(dict(pci=args.pci, serial=serial,
        configuration=header.hex(), vpd=vpd.hex(), state=state), indent=2) + '\n')
    print(f'read 256 VPD bytes; serial {serial}; no EEPROM write requests')


if __name__ == '__main__':
    main()
