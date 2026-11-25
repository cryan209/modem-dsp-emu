"""Measure D0 -> D3hot -> D0 on stopped, identity-checked spare SN12196.

Configuration writes touch only PMCSR power-state bits. The optional latch
probe seeds IRQ_TEST with both interrupt enables masked. No BAR access occurs
in D3; all test controls are restored afterward.
Caller must stop the spare first and restore stock firmware after exit.
"""
import argparse
from contextlib import ExitStack
import ctypes
import json
import mmap
import os
from pathlib import Path
import struct
import subprocess
import time
from eicon_dsp_bench_card import check_adapter


def cycle(read, write, snapshot, sleep=time.sleep):
    pmcsr = struct.unpack('<H', read(0x44, 2))[0]
    if pmcsr & 3:
        raise RuntimeError('power probe requires initial D0')
    report = dict(before=snapshot(), config_before=read(0, 64).hex(), pmcsr_before=pmcsr)
    try:
        write(0x44, struct.pack('<H', (pmcsr & ~0x8003) | 3))
        sleep(0.01)
        report['pmcsr_d3'] = struct.unpack('<H', read(0x44, 2))[0]
        report['config_d3'] = read(0, 64).hex()
    finally:
        write(0x44, struct.pack('<H', pmcsr & ~0x8003))
        sleep(0.02)
    report['pmcsr_after'] = struct.unpack('<H', read(0x44, 2))[0]
    report['config_after'] = read(0, 64).hex()
    report['after'] = snapshot()
    report['completed'] = report['pmcsr_d3'] & 3 == 3 and report['pmcsr_after'] & 3 == 0
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pci', required=True)
    parser.add_argument('--adapter', required=True, type=int)
    parser.add_argument('--serial', required=True, type=int)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--control', action='store_true',
                        help='hold D0 for the same interval without PMCSR writes')
    parser.add_argument('--pending-interrupt', action='store_true',
                        help='seed the test latch with local and PCI delivery disabled')
    args = parser.parse_args()
    if (args.pci, args.adapter, args.serial) != ('0000:02:02.0', 5, 12196):
        raise SystemExit('power probe restricted to spare SN12196')
    check_adapter(args.pci, args.adapter, args.serial)
    state = subprocess.check_output(['/usr/lib/divas/divactrl', 'load', '-c', '5', '-CardState'], text=True).strip()
    if state != 'ready':
        raise SystemExit(f'spare must be stopped; state={state!r}')
    with ExitStack() as stack:
        config = os.open(f'/sys/bus/pci/devices/{args.pci}/config', os.O_RDWR)
        stack.callback(os.close, config)
        def read(offset, count):
            data = os.pread(config, count, offset)
            if len(data) != count:
                raise RuntimeError('short config read')
            return data
        def write(offset, data):
            if offset != 0x44 or len(data) != 2 or struct.unpack('<H', data)[0] & ~3:
                raise RuntimeError('power probe may only write PMCSR power-state bits')
            if os.pwrite(config, data, offset) != len(data):
                raise RuntimeError('short config write')
        maps = []
        for bar, size in ((0, 0x100), (3, 0x10000), (2, 0x1000000)):
            fd = os.open(f'/sys/bus/pci/devices/{args.pci}/resource{bar}', os.O_RDWR | os.O_SYNC)
            stack.callback(os.close, fd)
            maps.append(stack.enter_context(mmap.mmap(fd, size, flags=mmap.MAP_SHARED,
                                    prot=mmap.PROT_READ | mmap.PROT_WRITE)))
        bridge, controls, ram = maps
        def snapshot():
            if struct.unpack('<H', read(0x44, 2))[0] & 3:
                raise RuntimeError('BAR access forbidden outside D0')
            def word(mapping, offset):
                return ctypes.c_uint32.from_buffer(mapping, offset).value
            return dict(prom=ctypes.c_uint16.from_buffer(bridge, 0x6e).value,
                        intcsr=word(bridge, 0x68), las0rr=word(bridge, 0),
                        lbrd0=word(bridge, 0x18), mabr0=word(bridge, 0xac),
                        risc=word(controls, 0x200), irq=word(controls, 0x400),
                        sdram=bytes(ram[0x80000:0x80010]).hex())
        baseline = snapshot()
        try:
            if args.pending_interrupt:
                if baseline['risc'] != 0:
                    raise RuntimeError('interrupt probe requires halted RISC')
                # Mask local source as well as PCI delivery: a shared-line ISR
                # must not recognize or acknowledge this test latch.
                ctypes.c_uint8.from_buffer(bridge, 0x69).value = 0
                ctypes.c_uint32.from_buffer(controls, 0x400).value = 1
            if args.control:
                report = dict(before=snapshot(), config_before=read(0, 64).hex())
                time.sleep(0.03)
                report.update(after=snapshot(), config_after=read(0, 64).hex(),
                              pmcsr_after=struct.unpack('<H', read(0x44, 2))[0], completed=True)
            else:
                report = cycle(read, write, snapshot)
        finally:
            if args.pending_interrupt and struct.unpack('<H', read(0x44, 2))[0] & 3 == 0:
                ctypes.c_uint32.from_buffer(controls, 0x400).value = baseline['irq']
                ctypes.c_uint8.from_buffer(bridge, 0x69).value = (baseline['intcsr'] >> 8) & 0x7f
        report['baseline'] = baseline
        report['pending_interrupt'] = args.pending_interrupt
        report['control'] = args.control
    report.update(pci=args.pci, serial=args.serial, state=state)
    args.out.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    raise SystemExit(0 if report['completed'] else 1)


if __name__ == '__main__':
    main()
