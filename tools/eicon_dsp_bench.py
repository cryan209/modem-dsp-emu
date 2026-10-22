#!/usr/bin/env python3
"""Run DSP conformance programs on a Diva 4BRI-8 PCI v2's own ADSP-2181s.

The host cannot reach this card's DSPs: they sit behind the card's MIPS, which
drives each one through a host-port pair in the board register window.  So
this tool builds a small bare-metal MIPS program (the stub) that does exactly
what the card's protocol image does to its DSPs, and nothing else:

  - the reset byte at 0xbf800000: write 0 (everything held), then bit 0
    (the image ORs 1 into it at 0x80171634 before it downloads its kernel;
    bits 1..4 are the four ISAC resets, which stay asserted);
  - per DSP port (0xbf80n800 and 0xbf80n810, table at 0x8026f0b4): write the
    IDMA address to +8, then data at +0.  Bit 14 selects DM.  A PM word is two
    data writes, the upper 16 bits then the low 8 (0x8016fe50);
  - the presence echo of 0x5a5a and 0xa5a5 at DM 0 (0x80194c20);
  - every PM word from 1 upwards, then PM 0, which releases a core held for
    IDMA boot -- the same order the card's own kernel download uses.

The stub then polls DM(DONE_ADDR) over IDMA, copies the result block into
card RAM and writes a completion word.  The host reads that back.

    eicon_dsp_bench.py bundle alu.json --out alu.bundle
    eicon_dsp_bench.py emulate alu.bundle --out alu.emucard.json
    # on eicon420, card stopped:  eicon_dsp_bench_card.py alu.bundle ...

`emulate` runs the bundle under Unicorn with tools/adsp2181emu cores behind
the port registers, so the stub is proved before it goes near the card.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import dsp_conformance  # noqa: E402

KSEG1 = 0xA0000000
STUB = 0x0000
PARAMS = 0x1000
STATUS = 0x1100
LISTS = 0x4000
RESULTS = 0x100000

MAGIC = 0x42505344          # "DSPB"
FINISHED = 0x600DCAFE
RESET_REG = 0xBF800000
PORTS = tuple(0xBF800000 + 0x1000 * n + off for n in (1, 2, 3, 4) for off in (0x800, 0x810))
RESULT_STRIDE = 0x8000      # bytes of card RAM per DSP's result block

# Status block layout (32-bit words from STATUS).
ST_STAGE, ST_HEARTBEAT, ST_FINISHED, ST_JOB = 0x00, 0x04, 0x08, 0x10
STAGES = {1: "reset asserted", 2: "reset released", 3: "probe", 4: "DM preset",
          5: "PM load", 6: "running", 7: "readback", 8: "finished"}


# ------------------------------------------------------------ MIPS-I words --

REG = {name: i for i, name in enumerate(
    ("zero", "at", "v0", "v1", "a0", "a1", "a2", "a3",
     "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
     "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7",
     "t8", "t9", "k0", "k1", "gp", "sp", "fp", "ra"))}


class Assembler:
    """Just enough MIPS-I for the stub.  Branch and load delay slots are the
    caller's to fill: every branch and every load here is followed by a NOP
    unless the next instruction is independent of it."""

    def __init__(self) -> None:
        self.words: list[int | tuple] = []
        self.labels: dict[str, int] = {}

    def label(self, name: str) -> None:
        self.labels[name] = len(self.words)

    def _r(self, funct: int, rd: str = "zero", rs: str = "zero", rt: str = "zero", sa: int = 0):
        self.words.append((REG[rs] << 21) | (REG[rt] << 16) | (REG[rd] << 11) | (sa << 6) | funct)

    def _i(self, opcode: int, rt: str, rs: str, imm: int):
        self.words.append((opcode << 26) | (REG[rs] << 21) | (REG[rt] << 16) | (imm & 0xFFFF))

    def nop(self): self.words.append(0)
    def addu(self, rd, rs, rt): self._r(0x21, rd, rs, rt)
    def subu(self, rd, rs, rt): self._r(0x23, rd, rs, rt)
    def or_(self, rd, rs, rt): self._r(0x25, rd, rs, rt)
    def sll(self, rd, rt, sa): self._r(0x00, rd, rt=rt, sa=sa)
    def srl(self, rd, rt, sa): self._r(0x02, rd, rt=rt, sa=sa)
    def jr(self, rs): self._r(0x08, rs=rs)
    def addiu(self, rt, rs, imm): self._i(0x09, rt, rs, imm)
    def andi(self, rt, rs, imm): self._i(0x0C, rt, rs, imm)
    def ori(self, rt, rs, imm): self._i(0x0D, rt, rs, imm)
    def lui(self, rt, imm): self._i(0x0F, rt, "zero", imm)
    def lw(self, rt, off, base): self._i(0x23, rt, base, off)
    def lhu(self, rt, off, base): self._i(0x25, rt, base, off)
    def sw(self, rt, off, base): self._i(0x2B, rt, base, off)
    def sh(self, rt, off, base): self._i(0x29, rt, base, off)

    def li(self, rt: str, value: int) -> None:
        self.lui(rt, (value >> 16) & 0xFFFF)
        self.ori(rt, rt, value & 0xFFFF)

    def beq(self, rs, rt, target): self.words.append(("b", 0x04, rs, rt, target))
    def bne(self, rs, rt, target): self.words.append(("b", 0x05, rs, rt, target))

    def assemble(self) -> bytes:
        out = []
        for index, word in enumerate(self.words):
            if isinstance(word, tuple):
                _, opcode, rs, rt, target = word
                offset = self.labels[target] - (index + 1)
                word = (opcode << 26) | (REG[rs] << 21) | (REG[rt] << 16) | (offset & 0xFFFF)
            out.append(word)
        return b"".join(struct.pack("<I", w) for w in out)


def build_stub() -> bytes:
    a = Assembler()
    # Reset fetches this from wherever the board aliases card RAM 0; run the
    # rest uncached from kseg1, as the protocol image's own first block does.
    a.li("t0", KSEG1 | (STUB + 0x20))
    a.jr("t0")
    a.nop()
    while len(a.words) < 8:
        a.nop()

    a.li("s0", KSEG1 | PARAMS)
    a.li("s1", KSEG1 | STATUS)

    def stage(n: int) -> None:
        a.addiu("t0", "zero", n)
        a.sw("t0", ST_STAGE, "s1")

    def delay(param_offset: int, name: str) -> None:
        a.lw("t0", param_offset, "s0")
        a.nop()
        a.label(name)
        a.addiu("t0", "t0", -1)
        a.bne("t0", "zero", name)
        a.nop()

    # Hold everything in reset, then release the DSPs only.
    a.lw("s2", 0x04, "s0")               # reset register
    a.nop()
    a.sh("zero", 0, "s2")
    stage(1)
    delay(0x08, "hold")
    a.addiu("t0", "zero", 1)
    a.sh("t0", 0, "s2")
    stage(2)
    delay(0x0C, "settle")

    a.addiu("s3", "zero", 0)             # job index
    a.label("job")
    a.lw("t0", 0x10, "s0")               # job count
    a.nop()
    a.beq("s3", "t0", "all_done")
    a.nop()
    a.sll("t1", "s3", 2)
    a.addu("t1", "t1", "s0")
    a.lw("s4", 0x40, "t1")               # port base
    a.lw("s5", 0x60, "t1")               # result destination
    a.sll("t1", "s3", 4)
    a.addu("s6", "t1", "s1")             # this job's status words (+ST_JOB)

    # Presence echo at DM 0, as the card's own probe does.
    stage(3)
    a.addiu("t2", "zero", 0x4000)
    a.sh("t2", 8, "s4")
    a.addiu("t3", "zero", 0x5A5A)
    a.sh("t3", 0, "s4")
    a.sh("t2", 8, "s4")
    a.lhu("t4", 0, "s4")
    a.nop()
    a.sw("t4", ST_JOB + 0, "s6")
    a.ori("t3", "zero", 0xA5A5)
    a.sh("t2", 8, "s4")
    a.sh("t3", 0, "s4")
    a.sh("t2", 8, "s4")
    a.lhu("t5", 0, "s4")
    a.nop()
    a.sw("t5", ST_JOB + 4, "s6")
    a.addiu("t3", "zero", 0x5A5A)
    a.bne("t4", "t3", "skip")
    a.nop()
    a.ori("t3", "zero", 0xA5A5)
    a.bne("t5", "t3", "skip")
    a.nop()

    # DM preset: one word per entry, (idma address << 16) | value.
    stage(4)
    a.lw("t6", 0x14, "s0")
    a.lw("t7", 0x18, "s0")
    a.nop()
    a.label("dm_loop")
    a.beq("t7", "zero", "dm_end")
    a.nop()
    a.lw("t0", 0, "t6")
    a.nop()
    a.srl("t1", "t0", 16)
    a.sh("t1", 8, "s4")
    a.sh("t0", 0, "s4")
    a.addiu("t6", "t6", 4)
    a.addiu("t7", "t7", -1)
    a.beq("zero", "zero", "dm_loop")
    a.nop()
    a.label("dm_end")

    # PM: (address, 24-bit word) pairs, PM 0 last.
    stage(5)
    a.lw("t6", 0x1C, "s0")
    a.lw("t7", 0x20, "s0")
    a.nop()
    a.label("pm_loop")
    a.beq("t7", "zero", "pm_end")
    a.nop()
    a.lw("t0", 0, "t6")
    a.lw("t1", 4, "t6")
    a.nop()
    a.sh("t0", 8, "s4")
    a.srl("t2", "t1", 8)
    a.sh("t2", 0, "s4")
    a.andi("t2", "t1", 0xFF)
    a.sh("t2", 0, "s4")
    a.addiu("t6", "t6", 8)
    a.addiu("t7", "t7", -1)
    a.beq("zero", "zero", "pm_loop")
    a.nop()
    a.label("pm_end")

    # Wait for the program's completion word.
    stage(6)
    a.lw("t2", 0x24, "s0")               # IDMA address of DONE
    a.lw("t3", 0x28, "s0")               # DONE value
    a.lw("t7", 0x2C, "s0")               # poll limit
    a.addiu("t6", "zero", 0)
    a.label("poll")
    a.sh("t2", 8, "s4")
    a.lhu("t4", 0, "s4")
    a.addiu("t6", "t6", 1)
    a.sw("t6", ST_HEARTBEAT, "s1")
    a.beq("t4", "t3", "ran")
    a.nop()
    a.bne("t6", "t7", "poll")
    a.nop()
    a.label("ran")
    a.sw("t6", ST_JOB + 8, "s6")
    a.sw("t4", ST_JOB + 12, "s6")

    # Copy the result block: DM, auto-incrementing.
    stage(7)
    a.lw("t2", 0x30, "s0")
    a.lw("t7", 0x34, "s0")
    a.nop()
    a.sh("t2", 8, "s4")
    a.addu("t6", "s5", "zero")
    a.label("read_loop")
    a.beq("t7", "zero", "next")
    a.nop()
    a.lhu("t0", 0, "s4")
    a.nop()
    a.sh("t0", 0, "t6")
    a.addiu("t6", "t6", 2)
    a.addiu("t7", "t7", -1)
    a.beq("zero", "zero", "read_loop")
    a.nop()

    a.label("skip")
    a.addiu("t0", "zero", -1)
    a.sw("t0", ST_JOB + 8, "s6")
    a.label("next")
    a.addiu("s3", "s3", 1)
    a.beq("zero", "zero", "job")
    a.nop()

    a.label("all_done")
    stage(8)
    a.li("t0", FINISHED)
    a.sw("t0", ST_FINISHED, "s1")
    a.label("park")
    a.beq("zero", "zero", "park")
    a.nop()

    code = a.assemble()
    if len(code) > PARAMS - STUB:
        raise ValueError("stub overruns its params block")
    return code


# ----------------------------------------------------------------- bundle --

def build_bundle(spec: dict, ports: tuple[int, ...] = PORTS,
                 hold: int = 200_000, settle: int = 2_000_000,
                 polls: int = 2_000_000) -> tuple[bytes, dict]:
    writes = dsp_conformance.boot_writes(spec)
    dm = [(0x4000 | a) << 16 | v for a, v, is_pm in writes if not is_pm]
    pm = [(a, v) for a, v, is_pm in writes if is_pm]
    count = spec["result_end"] - spec["result_base"]
    if count * 2 > RESULT_STRIDE:
        raise ValueError("result block larger than its stride")

    image = bytearray(LISTS)
    stub = build_stub()
    image[STUB:STUB + len(stub)] = stub
    dm_off = LISTS
    pm_off = dm_off + 4 * len(dm)
    lists = b"".join(struct.pack("<I", w) for w in dm) + \
        b"".join(struct.pack("<II", a, v) for a, v in pm)
    image += lists

    params = struct.pack(
        "<IIIII" "II" "II" "III" "II",
        MAGIC, RESET_REG, hold, settle, len(ports),
        KSEG1 | dm_off, len(dm), KSEG1 | pm_off, len(pm),
        0x4000 | spec["done_addr"], spec["done_value"], polls,
        0x4000 | spec["result_base"], count)
    image[PARAMS:PARAMS + len(params)] = params
    for j, port in enumerate(ports):
        struct.pack_into("<I", image, PARAMS + 0x40 + 4 * j, port)
        struct.pack_into("<I", image, PARAMS + 0x60 + 4 * j,
                         KSEG1 | (RESULTS + RESULT_STRIDE * j))
    meta = {"suite": spec["suite"], "ports": [f"{p:08x}" for p in ports],
            "results": RESULTS, "stride": RESULT_STRIDE, "count": count,
            "status": STATUS, "finished": FINISHED, "size": len(image)}
    return bytes(image), meta


def read_results(ram, meta: dict) -> dict:
    """Decode status and result blocks from card RAM (bytes-like)."""
    def word(off): return struct.unpack_from("<I", ram, off)[0]
    status = meta["status"]
    out = {"suite": meta["suite"], "stage": word(status + ST_STAGE),
           "heartbeat": word(status + ST_HEARTBEAT),
           "finished": word(status + ST_FINISHED) == meta["finished"], "dsps": []}
    for j, port in enumerate(meta["ports"]):
        base = status + ST_JOB + 16 * j
        probe = (word(base), word(base + 4))
        polls, done = word(base + 8), word(base + 12)
        start = meta["results"] + meta["stride"] * j
        dm = struct.unpack_from(f"<{meta['count']}H", ram, start)
        out["dsps"].append({
            "port": port, "probe": [f"{v:04x}" for v in probe],
            "polls": polls if polls != 0xFFFFFFFF else None, "done": done & 0xFFFF,
            "dm": [f"{v:04x}" for v in dm]})
    return out


# --------------------------------------------------------------- emulation --

class EmulatedCard:
    """The 4BRI v2 as the stub sees it: card RAM at physical 0, and the board
    register window at 0x1f800000 with an ADSP-2181 behind each port."""

    RAM = 16 * 1024 * 1024
    WINDOW = 0x1F800000

    def __init__(self, bundle: bytes, cycles_per_access: int = 200) -> None:
        from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN
        self.lib = dsp_conformance.load_library()
        self.uc = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 + UC_MODE_LITTLE_ENDIAN)
        self.uc.mem_map(0, self.RAM)
        self.uc.mem_write(0, bundle)
        self.uc.mmio_map(self.WINDOW, 0x10000, self._read, None, self._write, None)
        self.reset = 0
        self.cores: dict[int, int] = {}
        self.cycles = cycles_per_access
        self.log: list[str] = []

    def _core(self, port: int):
        core = self.cores.get(port)
        if core is None:
            core = self.lib.adsp2181_create()
            self.lib.adsp2181_reset(core)
            self.lib.adsp2181_set_idma_boot_hold(core, 1)
            self.cores[port] = core
        return core

    def _port(self, offset: int):
        sub, low = offset & 0xF000, offset & 0xFFF
        if sub and low in (0x800, 0x808, 0x810, 0x818):
            return (sub | (low & 0x810)), low & 0x8
        return None, None

    def _write(self, uc, offset, size, value, user):
        if offset == 0:
            if (value & 1) and not (self.reset & 1):
                for core in self.cores.values():
                    self.lib.adsp2181_reset(core)
                    self.lib.adsp2181_set_idma_boot_hold(core, 1)
                self.log.append("DSP reset released")
            self.reset = value & 0xFFFF
            return
        port, reg = self._port(offset)
        if port is None:
            self.log.append(f"write 0x{offset:04x} = 0x{value:x}")
            return
        if not self.reset & 1:
            return
        core = self._core(port)
        if reg == 8:
            self.lib.adsp2181_idma_addr_write(core, value & 0xFFFF)
        else:
            self.lib.adsp2181_idma_data_write(core, value & 0xFFFF)
        self.lib.adsp2181_run(core, self.cycles)

    def _read(self, uc, offset, size, user):
        if offset == 0:
            return self.reset
        port, reg = self._port(offset)
        if port is None or not self.reset & 1:
            return 0xFFFF
        core = self._core(port)
        self.lib.adsp2181_run(core, self.cycles)
        if reg == 8:
            return 0
        return self.lib.adsp2181_idma_data_read(core)

    def run(self, meta: dict, budget: int = 400_000_000) -> dict:
        pc, step = KSEG1 | STUB, 2_000_000
        spent = 0
        while spent < budget:
            self.uc.emu_start(pc, KSEG1 | (PARAMS - 4), count=step)
            pc = self.uc.reg_read(__import__("unicorn").mips_const.UC_MIPS_REG_PC)
            spent += step
            status = self.uc.mem_read(STATUS, 16)
            if struct.unpack_from("<I", status, ST_FINISHED)[0] == FINISHED:
                break
        ram = self.uc.mem_read(0, RESULTS + RESULT_STRIDE * len(meta["ports"]))
        result = read_results(ram, meta)
        result["source"] = "eicon_dsp_bench emulated card"
        result["mips_instructions"] = spent
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("bundle", help="build the card RAM image for a suite")
    b.add_argument("spec", type=Path)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--ports", type=lambda s: [int(p, 16) for p in s.split(",")],
                   default=list(PORTS), help="comma-separated port bases (hex)")
    e = sub.add_parser("emulate", help="run a bundle on the emulated card")
    e.add_argument("bundle", type=Path)
    e.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "bundle":
        spec = json.loads(args.spec.read_text())
        image, meta = build_bundle(spec, tuple(args.ports))
        args.out.write_bytes(image)
        args.out.with_suffix(".meta.json").write_text(json.dumps(meta, indent=1))
        print(f"{spec['suite']}: {len(image)} bytes, {len(meta['ports'])} DSPs, "
              f"stub {len(build_stub())} bytes")
        return 0

    meta = json.loads(args.bundle.with_suffix(".meta.json").read_text())
    card = EmulatedCard(args.bundle.read_bytes())
    result = card.run(meta)
    args.out.write_text(json.dumps(result, indent=1))
    print(f"{meta['suite']}: stage {result['stage']} ({STAGES.get(result['stage'])}), "
          f"finished={result['finished']}, {result['mips_instructions']} MIPS instructions")
    for d in result["dsps"]:
        print(f"  {d['port']}: probe {'/'.join(d['probe'])}  polls {d['polls']}  done {d['done']:04x}")
    return 0 if result["finished"] else 1


if __name__ == "__main__":
    sys.exit(main())
