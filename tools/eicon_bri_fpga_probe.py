"""Bounded programming-pin probe on stopped spare SN12196 only.

Programs only the checksum-verified, installed stock volatile FPGA image.
EEPROM CS is never asserted. Caller must restore stock firmware afterward.
"""
import argparse
import ctypes
import hashlib
import json
import mmap
import os
from pathlib import Path
import struct
import subprocess
import time
from eicon_dsp_bench_card import check_adapter

STOCK_SHA256 = 'a3973120d0869f8ee2f20b10863616e449dd0bb3deffa19b77b681e77fcc533f'
PROG, DONE, CS, CCLK, DATA = 1, 2, 0xc, 0x100, 0x400


def program(read, write, image, sleep=time.sleep, now=time.monotonic):
    if hashlib.sha256(image).hexdigest() != STOCK_SHA256:
        raise ValueError('probe accepts only the verified stock ds4bri2.bit image')
    start = image.index(255)
    stream = image[start:]
    report = dict(image_sha256=STOCK_SHA256, image_bytes=len(image), stream_offset=start,
                  stream_bytes=len(stream), initial=read(), samples=[], first_done=None)
    deadline = now() + 20
    write(CS)
    sleep(0.05)
    report['program_low'] = read()
    write(CS | PROG)
    sleep(0.2)
    report['cleared'] = read()
    if report['cleared'] & DONE:
        raise RuntimeError('stock PROGRAM clear did not lower DONE')
    for index, byte in enumerate(stream):
        for bit in range(8):
            base = CS | PROG | (DATA if byte & (0x80 >> bit) else 0)
            write(base)
            write(base | CCLK)
            write(base | CCLK)  # Shipping driver's duplicate-high write.
            if index >= len(stream) - 8:
                value = read()
                report['samples'].append(dict(bits=index * 8 + bit + 1, edge='high', value=value))
                if value & DONE and report['first_done'] is None:
                    report['first_done'] = index * 8 + bit + 1
            write(base)
        count = index + 1
        if count % 4096 == 0 or count in (1, 5, 32) or count >= len(stream) - 8:
            report['samples'].append(dict(bits=count * 8, edge='low', value=read()))
        if count % 256 == 0 and now() > deadline:
            raise RuntimeError('bounded stock FPGA programming exceeded 20 seconds')
    sleep(0.12)
    report['final'] = read()
    report['completed'] = bool(report['final'] & DONE)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pci', required=True)
    parser.add_argument('--adapter', required=True, type=int)
    parser.add_argument('--serial', required=True, type=int)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    if (args.pci, args.adapter, args.serial) != ('0000:02:02.0', 5, 12196):
        raise SystemExit('FPGA probing is restricted to spare SN12196')
    check_adapter(args.pci, args.adapter, args.serial)
    state = subprocess.check_output(['/usr/lib/divas/divactrl', 'load', '-c', '5', '-CardState'], text=True).strip()
    if state != 'ready':
        raise SystemExit(f'spare must be stopped before probing; state={state!r}')
    image = Path('/usr/lib/divas/ds4bri2.bit').read_bytes()
    if hashlib.sha256(image).hexdigest() != STOCK_SHA256:
        raise SystemExit('installed stock FPGA image differs from the reviewed image')
    fd = os.open(f'/sys/bus/pci/devices/{args.pci}/resource0', os.O_RDWR | os.O_SYNC)
    try:
        with mmap.mmap(fd, 0x100, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ | mmap.PROT_WRITE) as registers:
            def read():
                return ctypes.c_uint16.from_buffer(registers, 0x6e).value
            def write(value):
                if value & ~(PROG | CS | CCLK | DATA):
                    raise RuntimeError('FPGA probe must never assert EEPROM CS or other PLX controls')
                ctypes.c_uint16.from_buffer(registers, 0x6e).value = value
            report = program(read, write, image)
            if report['completed']:
                report['intcsr_before'] = ctypes.c_uint32.from_buffer(registers, 0x68).value
                masked = (report['intcsr_before'] & ~(1 << 8)) | (1 << 11)
                ctypes.c_uint32.from_buffer(registers, 0x68).value = masked
                report['intcsr_masked'] = ctypes.c_uint32.from_buffer(registers, 0x68).value
                ctl_fd = os.open(f'/sys/bus/pci/devices/{args.pci}/resource3', os.O_RDWR | os.O_SYNC)
                try:
                    with mmap.mmap(ctl_fd, 0x10000, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ | mmap.PROT_WRITE) as controls:
                        report['risc_after'] = ctypes.c_uint32.from_buffer(controls, 0x200).value
                        report['irq_after'] = ctypes.c_uint32.from_buffer(controls, 0x400).value
                        ctypes.c_uint32.from_buffer(controls, 0x400).value = 0
                        report['intcsr_after_ack'] = ctypes.c_uint32.from_buffer(registers, 0x68).value
                finally:
                    os.close(ctl_fd)
                    ctypes.c_uint32.from_buffer(registers, 0x68).value = report['intcsr_before']

    finally:
        os.close(fd)
    report.update(pci=args.pci, serial=args.serial, state=state)
    args.out.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('completed', 'initial', 'program_low', 'cleared', 'first_done', 'final')}))
    raise SystemExit(0 if report['completed'] else 1)


if __name__ == '__main__':
    main()
