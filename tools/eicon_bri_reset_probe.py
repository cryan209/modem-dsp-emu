"""Bounded shipping PLX reset sequence on stopped spare SN12196 only.

No EEPROM writes: RELOAD reads the existing EEPROM into bridge registers.
Caller must stop the spare and restore its stock firmware after mappings close.
"""
import argparse
import ctypes
import json
import mmap
import os
from pathlib import Path
import subprocess
import time
from eicon_dsp_bench_card import check_adapter


def probe(bridge, controls, ram, sleep=time.sleep):
    def read(mapping, offset, kind=ctypes.c_uint32):
        return kind.from_buffer(mapping, offset).value

    def write(mapping, offset, value, kind=ctypes.c_uint32):
        kind.from_buffer(mapping, offset).value = value

    def pins():
        return read(bridge, 0x6e, ctypes.c_uint16)

    def snapshot():
        return dict(prom=pins(), intcsr=read(bridge, 0x68),
                    las0rr=read(bridge, 0), lbrd0=read(bridge, 0x18),
                    mabr0=read(bridge, 0xac), risc=read(controls, 0x200),
                    irq=read(controls, 0x400), device_reset=read(controls, 0),
                    sdram=bytes(ram[0x80000:0x80010]).hex())

    report = {'before': snapshot(), 'phases': []}
    if report['before']['risc'] != 0:
        raise RuntimeError('RISC must be halted before reset probing')
    original_ram = bytes(ram[0x80000:0x80010])
    # PCI interrupts stay masked throughout. Raise only the board test latch.
    write(bridge, 0x68, report['before']['intcsr'] & ~0x900)
    marker = b'BRI-RESET-12196!'
    ram[0x80000:0x80010] = marker
    write(controls, 0x400, 1)
    report['pending'] = snapshot()
    try:
        for name, bit in (('soft_reset', 0x4000), ('reload_eeprom', 0x2000)):
            write(bridge, 0x6e, pins() | bit, ctypes.c_uint16)
            sleep(0.001)
            asserted = pins()
            write(bridge, 0x6e, pins() & ~bit, ctypes.c_uint16)
            sleep(0.001)
            report['phases'].append(dict(name=name, asserted_prom=asserted, after=snapshot()))
        write(controls, 0x200, 0)  # Shipping reset_qBri_hardware final step.
        report['final'] = snapshot()
        report['completed'] = (report['final']['sdram'] == marker.hex()
            and report['final']['risc'] == 0 and report['final']['irq'] == 0
            and bool(report['final']['prom'] & 2))
        write(controls, 0x400, 0)
        report['acknowledged'] = snapshot()
        write(bridge, 0x69, 8, ctypes.c_uint8)  # Local source only; PCI IRQ disabled.
        report['local_enabled'] = snapshot()
        write(controls, 0x400, 1)
        report['local_pending'] = snapshot()
        write(controls, 0x400, 0)
        report['local_acknowledged'] = snapshot()
    finally:
        write(controls, 0x400, 0)
        ram[0x80000:0x80010] = original_ram
        # Restore only IRQ enables. EEPROM reload status bits are device-owned.
        write(bridge, 0x68, report['before']['intcsr'])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pci', required=True)
    parser.add_argument('--adapter', required=True, type=int)
    parser.add_argument('--serial', required=True, type=int)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    if (args.pci, args.adapter, args.serial) != ('0000:02:02.0', 5, 12196):
        raise SystemExit('reset probe restricted to spare SN12196')
    check_adapter(args.pci, args.adapter, args.serial)
    state = subprocess.check_output(['/usr/lib/divas/divactrl', 'load', '-c', '5', '-CardState'], text=True).strip()
    if state != 'ready':
        raise SystemExit(f'spare must be stopped; state={state!r}')
    from contextlib import ExitStack
    with ExitStack() as stack:
        maps = []
        for bar, size in ((0, 0x100), (3, 0x10000), (2, 0x1000000)):
            fd = os.open(f'/sys/bus/pci/devices/{args.pci}/resource{bar}', os.O_RDWR | os.O_SYNC)
            stack.callback(os.close, fd)
            maps.append(stack.enter_context(mmap.mmap(fd, size, flags=mmap.MAP_SHARED,
                              prot=mmap.PROT_READ | mmap.PROT_WRITE)))
        report = probe(*maps)
    report.update(pci=args.pci, serial=args.serial, state=state)
    args.out.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    raise SystemExit(0 if report['completed'] else 1)


if __name__ == '__main__':
    main()
