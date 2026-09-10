#!/usr/bin/env python3
"""Boot or probe a PortMaster 3 dp2 image on the ADSP-2181 emulator.

For a raw ``dp2.bin`` this replays the exact IDMA address/data stream used by
ComOS, including DM initialization and the final PM[0] write that releases the
processor.  Flat 16K-word ``.pm`` inputs retain the older diagnostic reset shim.
"""
import argparse
import ctypes
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pm3_dp2_unpack


PM_WORDS = 0x4000


def load_library(repo: Path):
    lib = ctypes.CDLL(str(repo / "tools/adsp2181emu/libadsp2181.dylib"))
    lib.adsp2181_create.restype = ctypes.c_void_p
    for name, args in {
        "reset": [ctypes.c_void_p],
        "destroy": [ctypes.c_void_p],
        "pm": [ctypes.c_void_p],
        "dm": [ctypes.c_void_p],
        "run": [ctypes.c_void_p, ctypes.c_int],
        "pc": [ctypes.c_void_p],
        "idle": [ctypes.c_void_p],
        "i": [ctypes.c_void_p, ctypes.c_int],
        "coverage_clear": [ctypes.c_void_p],
        "coverage_count": [ctypes.c_void_p, ctypes.c_uint16],
    }.items():
        getattr(lib, "adsp2181_" + name).argtypes = args
    lib.adsp2181_pm.restype = ctypes.POINTER(ctypes.c_uint32)
    lib.adsp2181_dm.restype = ctypes.POINTER(ctypes.c_uint16)
    lib.adsp2181_pc.restype = ctypes.c_uint16
    lib.adsp2181_idle.restype = ctypes.c_int
    lib.adsp2181_i.restype = ctypes.c_uint16
    lib.adsp2181_coverage_count.restype = ctypes.c_uint64
    lib.adsp2181_set_idma_boot_hold.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.adsp2181_idma_addr_write.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
    lib.adsp2181_idma_data_write.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
    return lib


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("image", type=Path, help="raw dp2.bin or flat 16K-word .pm")
    ap.add_argument("--cycles", type=int, default=1_000_000)
    ap.add_argument("--stack", type=lambda x: int(x, 0), default=0x3D00,
                    help="initial I4 software-stack pointer (default: 0x3d00)")
    args = ap.parse_args()
    data = args.image.read_bytes()
    raw_dp2 = len(data) != PM_WORDS * 3

    repo = Path(__file__).resolve().parent.parent
    lib = load_library(repo)
    cpu = lib.adsp2181_create()
    try:
        lib.adsp2181_reset(cpu)
        pm, dm = lib.adsp2181_pm(cpu), lib.adsp2181_dm(cpu)
        if raw_dp2:
            lib.adsp2181_set_idma_boot_hold(cpu, 1)
            transfers = blocks = cursor = 0
            for addr, count, _words in pm3_dp2_unpack.records(data):
                # Replay raw values, not decoded words: this is the physical
                # sequence emitted by ComOS to the IDMA data port.
                raw = data[cursor + 4:cursor + 4 + count * 2]
                lib.adsp2181_idma_addr_write(cpu, addr)
                for i in range(0, len(raw), 2):
                    lib.adsp2181_idma_data_write(
                        cpu, int.from_bytes(raw[i:i + 2], "little"))
                transfers += count
                blocks += 1
                cursor += 4 + count * 2
            print(f"IDMA download: {blocks} blocks, {transfers} data writes")
        else:
            for addr in range(PM_WORDS):
                p = addr * 3
                pm[addr] = data[p] | data[p + 1] << 8 | data[p + 2] << 16
            pm[0] = 0x380000 | ((args.stack & 0xFFFF) << 4)
            pm[1] = 0x180000 | (0x0030 << 4) | 0x0F
        lib.adsp2181_coverage_clear(cpu)
        lib.adsp2181_run(cpu, args.cycles)

        covered = [(addr, lib.adsp2181_coverage_count(cpu, addr))
                   for addr in range(PM_WORDS)]
        covered = [(a, n) for a, n in covered if n]
        hot = sorted(covered, key=lambda x: x[1], reverse=True)[:8]
        print(f"pc=0x{lib.adsp2181_pc(cpu):04x} idle={lib.adsp2181_idle(cpu)} "
              f"I4=0x{lib.adsp2181_i(cpu, 4):04x}")
        print(f"executed {len(covered)} distinct PM words in {args.cycles} cycles")
        print("hot: " + " ".join(f"0x{a:04x}:{n}" for a, n in hot))
        for addr in (0x2021, 0x2022, 0x2023, 0x2AF7, 0x3FFE):
            print(f"DM[0x{addr:04x}]=0x{dm[addr]:04x}")
        callbacks = (0x31DC, 0x31EA, 0x31F8, 0x3206, 0x3214)
        print("controller callbacks: " + " ".join(
            f"DM[0x{a:04x}]=0x{dm[a]:04x}" for a in callbacks))
        if not raw_dp2 and any(lib.adsp2181_coverage_count(cpu, a)
                               for a in (0, 1)):
            print("blocked: the DSP called a zero controller callback and "
                  "re-entered the reset trampoline")
    finally:
        lib.adsp2181_destroy(cpu)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
