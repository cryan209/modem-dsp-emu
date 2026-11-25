#!/usr/bin/env python3
"""Measure what a host-serviced media ring over the PCI memory map can do.

Standard library only; runs on the card host against a STOPPED card.
Scratch region is SDRAM at 0x400000, above the program lists and below the
0x800000 result blocks, so nothing the bench uses is touched.
"""
import ctypes, mmap, os, statistics, struct, sys, time

SCRATCH = 0x400000
PCI = sys.argv[1] if len(sys.argv) > 1 else '0000:02:02.0'


def map_bar(pci, index):
    path = f'/sys/bus/pci/devices/{pci}/resource{index}'
    fd = os.open(path, os.O_RDWR | getattr(os, 'O_SYNC', 0))
    try:
        return mmap.mmap(fd, os.path.getsize(path), mmap.MAP_SHARED,
                         mmap.PROT_READ | mmap.PROT_WRITE)
    finally:
        os.close(fd)


def timed(fn, iterations):
    best = None
    for _ in range(5):
        start = time.perf_counter_ns()
        fn(iterations)
        elapsed = time.perf_counter_ns()-start
        best = elapsed if best is None else min(best, elapsed)
    return best/iterations


def main():
    ram = map_bar(PCI, 2)
    print(f'BAR2 size {len(ram)} bytes ({len(ram)/2**20:.1f} MiB)')
    word = ctypes.c_uint32.from_buffer(ram, SCRATCH)
    local = ctypes.c_uint32.from_buffer(bytearray(4))

    def mmio_read(n):
        for _ in range(n):
            word.value
    def local_read(n):
        for _ in range(n):
            local.value
    def mmio_write(n):
        for i in range(n):
            word.value = i
    def local_write(n):
        for i in range(n):
            local.value = i

    r, rb = timed(mmio_read, 20000), timed(local_read, 20000)
    w, wb = timed(mmio_write, 20000), timed(local_write, 20000)
    print(f'single 32-bit read   {r:7.1f} ns  (python baseline {rb:5.1f} ns'
          f' -> MMIO {r-rb:6.1f} ns)')
    print(f'single 32-bit write  {w:7.1f} ns  (python baseline {wb:5.1f} ns'
          f' -> MMIO {w-wb:6.1f} ns)')

    for size in (320, 1024, 4096, 65536):
        payload = bytes(range(256))*(size//256) if size >= 256 else bytes(size)
        payload = (payload+bytes(size))[:size]

        def bulk_read(n, size=size):
            for _ in range(n):
                ram[SCRATCH:SCRATCH+size]

        def bulk_write(n, size=size, payload=payload):
            for _ in range(n):
                ram[SCRATCH:SCRATCH+size] = payload

        iters = max(20, 2**22//size)
        rd, wr = timed(bulk_read, iters), timed(bulk_write, iters)
        print(f'block {size:6d} B  read {rd/1000:8.2f} us '
              f'({size/rd*1000:7.1f} MB/s)   write {wr/1000:8.2f} us '
              f'({size/wr*1000:7.1f} MB/s)')

    # A 20 ms media block at 8000 words/s is 160 words = 320 bytes.
    print()
    for period_ms in (20, 5, 1):
        target = period_ms/1000
        deltas = []
        nxt = time.perf_counter()+target
        for _ in range(300):
            while True:
                now = time.perf_counter()
                if now >= nxt:
                    break
                time.sleep(max(0, (nxt-now)*0.5))
            deltas.append((now-nxt)*1e6)
            nxt += target
        print(f'poll loop {period_ms:2d} ms: overshoot median '
              f'{statistics.median(deltas):8.1f} us  p95 '
              f'{sorted(deltas)[int(0.95*len(deltas))]:9.1f} us  max '
              f'{max(deltas):9.1f} us')
    del word
    ram.close()


if __name__ == '__main__':
    main()
