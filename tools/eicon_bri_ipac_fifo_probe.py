"""Bounded internal B-HDLC loop on stopped spare SN12196, DSPs held reset."""
import argparse
from contextlib import ExitStack
import ctypes
import json
import mmap
import os
from pathlib import Path
import subprocess
import time
from eicon_dsp_bench_card import check_adapter


def probe(read, write, sleep=time.sleep, now=time.monotonic, stock_clock_setup=False, address_matrix=False, no_transmit_crc=False, address_filter=False, compare_pairs=False, filter_smoke=False):
    records = []
    payloads = (b'123456789', b'\x33\x00\x7e\x7d\xff\x80\x01', b'\x00\xff\x00', bytes(range(32)))
    if address_matrix:
        payloads = tuple(bytes((first, second, 0x55))
                         for first in (0, 1, 2, 3, 0x30, 0x31, 0xfc, 0xfd, 0xfe, 0xff)
                         for second in (0, 1, 2, 3, 0xfc, 0xfd, 0xfe, 0xff))
    if no_transmit_crc:
        payloads = (b'123456789', b'123456789\x6e\x90', b'123456789\x6f\x90',
                    b'\x33\x00\x7e\x7d\xff\x80\x01\x63\x0c',
                    b'\x33\x00\x7e\x7d\xff\x80\x01\x62\x0c')
    if address_filter:
        payloads = tuple(bytes.fromhex(value) for value in (
            '201155','223355','40ff55','fc1155','feff55','601155',
            '205555','605555','115555','335555','ff5555','55ff55'))
    if compare_pairs:
        payloads = tuple(bytes((first, second, 0x55))
                         for first in (0x20, 0x22, 0x40, 0x42, 0xfc, 0xfe, 0x60)
                         for second in (0x11, 0x13, 0x31, 0x33, 0xff, 0x55))
        payloads += tuple(bytes((first, 0x55, 0x55)) for first in (0x11,0x13,0x31,0x33,0xff,0x55))
    if filter_smoke:
        payloads = tuple(bytes.fromhex(value) for value in
                         ('201155','223355','40ff55','605555','115555','335555'))
    for port in (range(4) if filter_smoke else range(1) if address_matrix or address_filter else range(4)):
        iom_reset = read(port, 0x57)
        if stock_clock_setup:
            for register, value in ((0x30, 0), (0x32, 0x40), (0x3f, 0xff),
                    (0x3e, 0x40), (0x3c, 0x80), (0x3d, 1), (0x50, 0xf8),
                    (0x53, 0xa0), (0x55, 0x80), (0x57, 0x58)):
                write(port, register, value)
        iom = 0x58 if stock_clock_setup else 8
        write(port, 0x57, iom | 0x80)  # Software power-up requests IOM clocks.
        write(port, 0x2e, 0x0f)  # TIM requests clocks while line is inactive.
        sleep(0.01)
        write(port, 0x57, iom)
        sleep(0.01)
        cir = read(port, 0x2e)
        for channel in (range(2) if filter_smoke else range(1) if address_matrix or address_filter else range(2)):
            base = 0x70 + channel * 0x10
            if stock_clock_setup:
                write(port, 0x51 + channel, 8)  # Enable each controller BC1 timeslot.
            mode_values = (0x48, 0x68) if compare_pairs else (0xe8, 0xa8, 0x48, 0x68)
            modes = ((mode, sra) for mode in mode_values
                     for sra in (0, 0x10)) if address_filter else ((0xc8, 0),)
            if address_filter:
                for register, value in ((base+5, 0x20), (base+6, 0x40),
                                        (base+7, 0x13 if compare_pairs else 0x11), (base+8, 0x31 if compare_pairs else 0x33)):
                    write(port, register, value)
            for mode, sra in modes:
                for store_crc in ((False,) if address_matrix else (False, True)):
                    for payload in payloads:
                        write(port, 0x57, iom)  # IOM enabled, BCL clock enabled.
                        write(port, base + 2, mode)  # Transparent mode 0, RAC.
                        write(port, base + 3, (4 if store_crc else 0) | (8 if no_transmit_crc else 0) | sra)
                        write(port, base + 9, 1)  # Internal layer-2 test loop.
                        write(port, base, 0)       # Unmask ISTAB; PCI IRQ stays masked.
                        write(port, base + 1, 0xc1)
                        sleep(0.001)
                        initial = read(port, base)
                        for value in payload:
                            write(port, base + 10, value)
                        write(port, base + 1, 0x0a)
                        deadline = now() + 0.05
                        events = []
                        while now() < deadline:
                            flags = read(port, base)
                            if flags:
                                events.append(flags)
                            if flags & 0x80:
                                break
                            sleep(0.001)
                        count = read(port, base + 6) | ((read(port, base + 7) & 15) << 8)
                        status = read(port, base + 8)
                        data = bytes(read(port, base + 10) for _ in range(min(count, 64))) if 0x80 & (events[-1] if events else 0) else b''
                        records.append(dict(port=port, channel=channel, store_crc=store_crc,
                            payload=payload.hex(), mode=mode, store_address=bool(sra), iom_reset=iom_reset, cir=cir, initial=initial, events=events,
                            count=count, status=status, fifo=data.hex()))
                        write(port, base + 1, 0xc1)
                        write(port, base + 9, 0)
    all_received = all(r['fifo'] for r in records)
    completed = (any(r['fifo'] for r in records) and not all_received) if address_filter else all_received
    return dict(records=records, completed=completed, all_received=all_received)


def probe_burst(read, write, sleep=time.sleep, unique_ids=False):
    suites = []
    for port in range(4):
        for register, value in ((0x30,0),(0x32,0x40),(0x3f,0xff),(0x3e,0x40),
                (0x3c,0x80),(0x3d,1),(0x50,0xf8),(0x53,0xa0),(0x55,0x80),(0x57,0x58)):
            write(port, register, value)
        write(port, 0x57, 0xd8); write(port, 0x2e, 0x0f)
        sleep(0.01); write(port, 0x57, 0x58); sleep(0.01)
        for channel in range(2):
            base = 0x70 + channel*16
            write(port, 0x51+channel, 8)
            for stored in (False, True):
                for payloads in ((b'123456789', b'\x33\x00\x7e\x7d\xff\x80\x01', b'\x00\xff\x00'),
                                 tuple(b'12345678'+bytes((i,)) for i in range(15)) if unique_ids else (b'123456789',)*15):
                    write(port, base+2, 0xc8); write(port, base+3, 4 if stored else 0)
                    write(port, base+9, 1); write(port, base, 0)
                    write(port, base+1, 0xc1); sleep(0.001)
                    initial = read(port, base)
                    def snapshot():
                        return dict(event=read(port, base), count=read(port,base+6) |
                            ((read(port,base+7)&15)<<8), status=read(port,base+8))
                    received = []
                    for payload in payloads:
                        for byte in payload: write(port, base+10, byte)
                        write(port, base+1, 0x0a); sleep(0.005)
                        received.append(snapshot())
                    drains = []
                    for _ in range(20):
                        state = snapshot()
                        if not state['count']:
                            break
                        state['fifo'] = bytes(read(port,base+10) for _ in range(min(128,state['count']))).hex()
                        drains.append(state)
                        write(port,base+1,0x80); sleep(0.001)
                    suites.append(dict(port=port,channel=channel,store_crc=stored,
                        payloads=[p.hex() for p in payloads], initial=initial,
                        received=received, drains=drains, final=snapshot()))
                    write(port,base+1,0xc1);write(port,base+9,0)
    return dict(suites=suites, completed=all(s['drains'] for s in suites))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pci', required=True)
    parser.add_argument('--adapter', required=True, type=int)
    parser.add_argument('--serial', required=True, type=int)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--stock-clock-setup', action='store_true')
    parser.add_argument('--address-matrix', action='store_true')
    parser.add_argument('--no-transmit-crc', action='store_true')
    parser.add_argument('--address-filter', action='store_true')
    parser.add_argument('--compare-pairs', action='store_true')
    parser.add_argument('--filter-smoke', action='store_true')
    parser.add_argument('--burst', action='store_true')
    parser.add_argument('--burst-ids', action='store_true')
    args = parser.parse_args()
    if (args.pci, args.adapter, args.serial) != ('0000:02:02.0', 5, 12196):
        raise SystemExit('FIFO probe restricted to spare SN12196')
    check_adapter(args.pci, args.adapter, args.serial)
    state = subprocess.check_output(['/usr/lib/divas/divactrl', 'load', '-c', '5', '-CardState'], text=True).strip()
    if state != 'ready':
        raise SystemExit(f'spare must be stopped; state={state!r}')
    with ExitStack() as stack:
        maps = []
        for bar, size in ((0, 0x100), (3, 0x10000)):
            fd = os.open(f'/sys/bus/pci/devices/{args.pci}/resource{bar}', os.O_RDWR | os.O_SYNC)
            stack.callback(os.close, fd)
            maps.append(stack.enter_context(mmap.mmap(fd, size, flags=mmap.MAP_SHARED,
                                  prot=mmap.PROT_READ | mmap.PROT_WRITE)))
        bridge, controls = maps
        if ctypes.c_uint32.from_buffer(controls, 0x200).value != 0:
            raise RuntimeError('halted RISC required')
        saved = ctypes.c_uint8.from_buffer(bridge, 0x69).value & 0x7f
        saved_clock = ctypes.c_uint8.from_buffer(controls, 0x300).value
        ctypes.c_uint8.from_buffer(bridge, 0x69).value = 0
        try:
            ctypes.c_uint16.from_buffer(controls, 0).value = 0
            time.sleep(0.001)
            ctypes.c_uint16.from_buffer(controls, 0).value = 0x1e
            time.sleep(0.001)
            def read(port, register):
                return ctypes.c_uint8.from_buffer(controls, (port+1)*0x1000+register*4).value
            def write(port, register, value):
                ctypes.c_uint8.from_buffer(controls, (port+1)*0x1000+register*4).value = value
            if args.stock_clock_setup:
                ctypes.c_uint8.from_buffer(controls, 0x300).value = 15
            if args.burst:
                report = probe_burst(read, write, unique_ids=args.burst_ids)
            else:
                report = probe(read, write, stock_clock_setup=args.stock_clock_setup, address_matrix=args.address_matrix, no_transmit_crc=args.no_transmit_crc, address_filter=args.address_filter, compare_pairs=args.compare_pairs, filter_smoke=args.filter_smoke)
            report['board_config'] = ctypes.c_uint8.from_buffer(controls, 0x300).value
        finally:
            ctypes.c_uint16.from_buffer(controls, 0).value = 0
            ctypes.c_uint8.from_buffer(controls, 0x300).value = saved_clock
            ctypes.c_uint32.from_buffer(controls, 0x400).value = 0
            ctypes.c_uint8.from_buffer(bridge, 0x69).value = saved
    report.update(pci=args.pci, serial=args.serial, state=state, stock_clock_setup=args.stock_clock_setup, address_matrix=args.address_matrix, no_transmit_crc=args.no_transmit_crc, address_filter=args.address_filter, compare_pairs=args.compare_pairs, filter_smoke=args.filter_smoke, burst=args.burst, burst_ids=args.burst_ids)
    args.out.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))
    raise SystemExit(0 if report['completed'] else 1)


if __name__ == '__main__':
    main()
