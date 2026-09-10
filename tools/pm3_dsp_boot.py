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
        "io": [ctypes.c_void_p],
        "run": [ctypes.c_void_p, ctypes.c_int],
        "pc": [ctypes.c_void_p],
        "idle": [ctypes.c_void_p],
        "i": [ctypes.c_void_p, ctypes.c_int],
        "coverage_clear": [ctypes.c_void_p],
        "coverage_count": [ctypes.c_void_p, ctypes.c_uint16],
        "imask": [ctypes.c_void_p],
        "icntl": [ctypes.c_void_p],
        "set_irq": [ctypes.c_void_p, ctypes.c_int, ctypes.c_int],
        "sport1_frame": [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_int],
        "sport0_tdm_frame": [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                             ctypes.c_uint16, ctypes.c_uint16, ctypes.c_int],
        "dm_census": [ctypes.c_void_p, ctypes.c_int],
        "dm_census_clear": [ctypes.c_void_p],
        "dm_census_count": [ctypes.c_void_p, ctypes.c_uint16],
    }.items():
        getattr(lib, "adsp2181_" + name).argtypes = args
    lib.adsp2181_pm.restype = ctypes.POINTER(ctypes.c_uint32)
    lib.adsp2181_dm.restype = ctypes.POINTER(ctypes.c_uint16)
    lib.adsp2181_io.restype = ctypes.POINTER(ctypes.c_uint16)
    lib.adsp2181_pc.restype = ctypes.c_uint16
    lib.adsp2181_idle.restype = ctypes.c_int
    lib.adsp2181_i.restype = ctypes.c_uint16
    lib.adsp2181_coverage_count.restype = ctypes.c_uint64
    lib.adsp2181_imask.restype = ctypes.c_uint16
    lib.adsp2181_icntl.restype = ctypes.c_uint16
    lib.adsp2181_sport1_frame.restype = ctypes.c_uint32
    lib.adsp2181_sport0_tdm_frame.restype = ctypes.c_uint16
    lib.adsp2181_dm_census_count.restype = ctypes.c_uint64
    lib.adsp2181_set_idma_boot_hold.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.adsp2181_idma_addr_write.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
    lib.adsp2181_idma_data_write.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
    return lib


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("image", type=Path, help="raw dp2.bin or flat 16K-word .pm")
    ap.add_argument("--cycles", type=int, default=1_000_000)
    ap.add_argument("--frames", type=int, default=0,
                    help="inject this many 8 kHz SPORT frames after boot")
    ap.add_argument("--sport", type=int, choices=(0, 1), default=1)
    ap.add_argument("--sample", type=lambda x: int(x, 0), default=0)
    ap.add_argument("--frame-cycles", type=int, default=4000)
    ap.add_argument("--poke", action="append", default=[], metavar="ADDR=VALUE",
                    help="write a DM host field after cold boot (repeatable)")
    ap.add_argument("--dump", action="append", default=[], metavar="ADDR",
                    help="print a DM word after the run (repeatable)")
    ap.add_argument("--channel-state", type=lambda x: int(x, 0),
                    help="initialize channel 0, then force its scheduler state")
    ap.add_argument("--census", type=int, default=0, metavar="N",
                    help="show the N most-written DM words after activation")
    ap.add_argument("--scan-outputs", type=int, default=0, metavar="N",
                    help="rank varying channel DM words over N scheduler runs")
    ap.add_argument("--log", action="store_true",
                    help="decode the DSP-to-ComOS byte ring at DM 0x10")
    ap.add_argument("--comos-config", metavar="HEXBYTES",
                    help="send default-config TLVs, e.g. 04,01,02")
    ap.add_argument("--irq", type=int, choices=range(9),
                    help="pulse a raw ADSP interrupt once after boot")
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
        io = lib.adsp2181_io(cpu)
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
        for spec in args.poke:
            try:
                address, value = (int(part, 0) for part in spec.split("=", 1))
            except (ValueError, TypeError):
                ap.error(f"invalid --poke {spec!r}; expected ADDR=VALUE")
            lib.adsp2181_idma_addr_write(cpu, 0x4000 | (address & 0x3fff))
            lib.adsp2181_idma_data_write(cpu, value)
        if args.poke:
            lib.adsp2181_run(cpu, args.frame_cycles)
        if args.comos_config is not None:
            try:
                tlv = bytes(int(x, 16) for x in args.comos_config.split(",")
                            if x)
            except ValueError:
                ap.error("--comos-config expects comma-separated hex bytes")
            # mdp2_init_modem (ComOS 3.9.1 file 0x4e719..0x4ebe6):
            # command 6, TLV count, TLVs; byte count; -1 terminator; commit 2c.
            count = 0
            p = 0
            while p < len(tlv):
                if p + 2 > len(tlv) or p + 2 + tlv[p + 1] > len(tlv):
                    ap.error("malformed TLV list in --comos-config")
                count += 1
                p += 2 + tlv[p + 1]
            base = dm[0x2021]
            packet = bytes((6, count)) + tlv
            for offset, value in enumerate(packet):
                lib.adsp2181_idma_addr_write(cpu, 0x4000 | (base + 0x50 + offset))
                lib.adsp2181_idma_data_write(cpu, value)
            for offset, value in ((0x4f, len(packet)), (0x4e, 0xffff),
                                  (0x23f, 0x2c)):
                lib.adsp2181_idma_addr_write(cpu, 0x4000 | (base + offset))
                lib.adsp2181_idma_data_write(cpu, value)
            lib.adsp2181_run(cpu, args.frame_cycles)
        if args.channel_state is not None:
            # DM[0x2021] is channel 0's context base.  The K56flex-era 2.1
            # layout and V.90-era 2.2 layout use different host event/state
            # fields; this split is recovered from their scheduler dispatch.
            base = dm[0x2021]
            is_v90_layout = any(a == 0x26bb for a, _n, _words in
                                pm3_dp2_unpack.records(data))
            event_field, state_field = ((575, 574) if is_v90_layout
                                        else (406, 568))
            lib.adsp2181_idma_addr_write(cpu, 0x4000 | (base + event_field))
            lib.adsp2181_idma_data_write(cpu, 1)
            lib.adsp2181_run(cpu, args.frame_cycles)
            if args.census:
                lib.adsp2181_dm_census_clear(cpu)
                lib.adsp2181_dm_census(cpu, 1)
            lib.adsp2181_idma_addr_write(cpu, 0x4000 | (base + state_field))
            lib.adsp2181_idma_data_write(cpu, args.channel_state)
            lib.adsp2181_run(cpu, args.frame_cycles)
            scans = None
            if args.scan_outputs:
                scans = {a: [] for a in range(PM_WORDS)}
                for _ in range(args.scan_outputs):
                    lib.adsp2181_run(cpu, args.frame_cycles)
                    for a, values in scans.items():
                        values.append(dm[a])
            if args.census:
                lib.adsp2181_dm_census(cpu, 0)
        if args.irq is not None:
            lib.adsp2181_set_irq(cpu, args.irq, 1)
            lib.adsp2181_set_irq(cpu, args.irq, 0)
            lib.adsp2181_run(cpu, args.frame_cycles)
        tx_written = 0
        for _ in range(args.frames):
            if args.sport == 1:
                tx_written += bool(lib.adsp2181_sport1_frame(
                    cpu, args.sample, args.frame_cycles) & 0x10000)
            else:
                lib.adsp2181_sport0_tdm_frame(
                    cpu, 0, 0, args.sample, 0, args.frame_cycles)

        covered = [(addr, lib.adsp2181_coverage_count(cpu, addr))
                   for addr in range(PM_WORDS)]
        covered = [(a, n) for a, n in covered if n]
        hot = sorted(covered, key=lambda x: x[1], reverse=True)[:8]
        print(f"pc=0x{lib.adsp2181_pc(cpu):04x} idle={lib.adsp2181_idle(cpu)} "
              f"I4=0x{lib.adsp2181_i(cpu, 4):04x} "
              f"IMASK=0x{lib.adsp2181_imask(cpu):04x} "
              f"ICNTL=0x{lib.adsp2181_icntl(cpu):04x}")
        if args.frames:
            print(f"SPORT{args.sport}: {args.frames} frames, "
                  f"{tx_written} explicit TX writes")
        print(f"executed {len(covered)} distinct PM words after cold boot")
        print("hot: " + " ".join(f"0x{a:04x}:{n}" for a, n in hot))
        print(f"peripheral IO: [0x0c0]=0x{io[0x0c0]:04x} "
              f"[0x0d0]=0x{io[0x0d0]:04x} "
              f"PM[0x00e8] executions="
              f"{lib.adsp2181_coverage_count(cpu, 0x00e8)}")
        if raw_dp2:
            major = [(a, len(words)) for a, _n, words in
                     pm3_dp2_unpack.records(data)
                     if not a & 0x4000 and len(words) >= 100]
            print("major-block coverage: " + " ".join(
                f"0x{a:04x}={sum(1 for pc, _ in covered if a <= pc < a + n)}"
                for a, n in major))
        for addr in (0x2021, 0x2022, 0x2023, 0x2AF7, 0x3FFE):
            print(f"DM[0x{addr:04x}]=0x{dm[addr]:04x}")
        for spec in args.dump:
            address = int(spec, 0) & 0x3fff
            print(f"DM[0x{address:04x}]=0x{dm[address]:04x}")
        if args.log:
            pos, end = dm[0x10] & 0xfff, dm[0x11] & 0xfff
            payload = bytearray()
            while pos != end:
                word = dm[0x12 + (pos >> 1)]
                payload.append((word >> (8 if pos & 1 else 0)) & 0xff)
                pos = (pos + 1) & 0xfff
            print("DSP log:\n" + payload.decode("latin1", "replace"))
        if args.census:
            census = [(lib.adsp2181_dm_census_count(cpu, a), a, dm[a])
                      for a in range(PM_WORDS)]
            census = sorted((row for row in census if row[0]), reverse=True)
            print("DM write census: " + " ".join(
                f"0x{a:04x}:{count}=0x{value:04x}"
                for count, a, value in census[:args.census]))
        if args.scan_outputs and scans is not None:
            varying = []
            for address, values in scans.items():
                unique = len(set(values))
                if unique > 1:
                    signed = [v if v < 0x8000 else v - 0x10000 for v in values]
                    span = max(signed) - min(signed)
                    varying.append((unique, span, address, values[-1]))
            varying.sort(reverse=True)
            print("varying DM words: " + " ".join(
                f"0x{a:04x}:uniq={unique},span={span},last=0x{last:04x}"
                for unique, span, a, last in varying[:20]))
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
