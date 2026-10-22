#!/usr/bin/env python3
"""ADSP-2181 conformance programs: the same code on the emulator and the chip.

Each suite is one straight-line DSP program.  Every case loads its operands
from immediates, runs one computation, and stores the registers that
computation may change to its own slot in data memory.  Nothing in a case
depends on the previous one except where the case says so (the accumulate
forms set MR first), so a mismatch names one instruction and one operand set.

The program is booted the way the card's MIPS boots its own kernel: IDMA
writes of every PM word from 1 upwards, then PM 0 last to release the core.
It ends by writing DONE_VALUE to DM(DONE_ADDR) and spinning.

    dsp_conformance.py build --suite alu --out alu.json
    dsp_conformance.py emulate alu.json --out alu.emu.json
    dsp_conformance.py compare alu.json alu.emu.json alu.card.json

`emulate` runs the program directly on tools/adsp2181emu with no MIPS in the
path; `eicon_dsp_bench.py` runs it through the card's own boot path, emulated
or real.  The opcode layouts are the ones tools/adsp2181emu decodes, and every
emitted word is checked against tools/adsp2181_dis.py when the suite is built.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LIBRARY = ROOT / "adsp2181emu" / "libadsp2181.dylib"

DONE_ADDR = 0x0010
DONE_VALUE = 0x600D
RESULT_BASE = 0x0100
RESULT_LIMIT = 0x3800   # DM 0x3fe0..0x3fff are the chip's control registers
PRESET = 0xDEAD         # what every result word holds before the program runs

# Register group 0, and the ALU/MAC/shifter operand selects.
DREG = {name: i for i, name in enumerate(
    ("AX0", "AX1", "MX0", "MX1", "AY0", "AY1", "MY0", "MY1",
     "SI", "SE", "AR", "MR0", "MR1", "MR2", "SR0", "SR1"))}
GROUP3 = {"ASTAT": 0, "MSTAT": 1, "SSTAT": 2, "IMASK": 3, "ICNTL": 4,
          "CNTR": 5, "SB": 6, "PX": 7}
ALU_X = {"AX0": 0, "AX1": 1, "AR": 2, "MR0": 3, "MR1": 4, "MR2": 5, "SR0": 6, "SR1": 7}
ALU_Y = {"AY0": 0, "AY1": 1, "AF": 2}
MAC_X = {"MX0": 0, "MX1": 1, "AR": 2, "MR0": 3, "MR1": 4, "MR2": 5, "SR0": 6, "SR1": 7}
MAC_Y = {"MY0": 0, "MY1": 1, "MF": 2}
SHIFT_X = {"SI": 0, "AR": 2, "MR0": 3, "MR1": 4, "MR2": 5, "SR0": 6, "SR1": 7}
TRUE = 0xF

ALU_OPS = {
    "Y": 0x10, "Y+1": 0x11, "X+Y+C": 0x12, "X+Y": 0x13, "NOT Y": 0x14,
    "-Y": 0x15, "X-Y+C-1": 0x16, "X-Y": 0x17, "Y-1": 0x18, "Y-X": 0x19,
    "Y-X+C-1": 0x1A, "NOT X": 0x1B, "X AND Y": 0x1C, "X OR Y": 0x1D,
    "X XOR Y": 0x1E, "ABS X": 0x1F,
}
MAC_OPS = {
    "X*Y(RND)": 0x01, "MR+X*Y(RND)": 0x02, "MR-X*Y(RND)": 0x03,
    "X*Y(SS)": 0x04, "X*Y(SU)": 0x05, "X*Y(US)": 0x06, "X*Y(UU)": 0x07,
    "MR+X*Y(SS)": 0x08, "MR+X*Y(UU)": 0x0B, "MR-X*Y(SS)": 0x0C,
}
SHIFT_OPS = {
    "LSHIFT(HI)": 0, "LSHIFT(HI,OR)": 1, "LSHIFT(LO)": 2, "LSHIFT(LO,OR)": 3,
    "ASHIFT(HI)": 4, "ASHIFT(HI,OR)": 5, "ASHIFT(LO)": 6, "ASHIFT(LO,OR)": 7,
    "NORM(HI)": 8, "NORM(HI,OR)": 9, "NORM(LO)": 10, "NORM(LO,OR)": 11,
    "EXP(HI)": 12, "EXP(HIX)": 13, "EXP(LO)": 14, "EXPADJ": 15,
}


# ---------------------------------------------------------------- encoders --

def nop() -> int:
    return 0x000000


def load_dreg(reg: str, value: int) -> int:
    """reg = <16-bit data>"""
    return 0x400000 | ((value & 0xFFFF) << 4) | DREG[reg]


def load_group3(reg: str, value: int) -> int:
    """ASTAT/MSTAT/SB/... = <14-bit signed data>"""
    if not -0x2000 <= value < 0x2000:
        raise ValueError(f"{reg} immediate {value} does not fit 14 bits")
    return 0x3C0000 | ((value & 0x3FFF) << 4) | GROUP3[reg]


def store_dreg(addr: int, reg: str) -> int:
    """DM(<addr>) = reg (group 0)"""
    return 0x900000 | ((addr & 0x3FFF) << 4) | DREG[reg]


def store_group3(addr: int, reg: str) -> int:
    return 0x9C0000 | ((addr & 0x3FFF) << 4) | GROUP3[reg]


def alu(amf: int, x: str = "AX0", y: str = "AY0", to_af: bool = False) -> int:
    return (0x2 << 20) | (int(to_af) << 18) | (amf << 13) | (ALU_Y[y] << 11) \
        | (ALU_X[x] << 8) | TRUE


def mac(amf: int, x: str = "MX0", y: str = "MY0") -> int:
    return (0x2 << 20) | (amf << 13) | (MAC_Y[y] << 11) | (MAC_X[x] << 8) | TRUE


def shift(sf: int, x: str = "SI") -> int:
    return 0x0E0000 | (sf << 11) | (SHIFT_X[x] << 8) | TRUE


def shift_imm(sf: int, exponent: int, x: str = "SI") -> int:
    return 0x0F0000 | (sf << 11) | (SHIFT_X[x] << 8) | (exponent & 0xFF)


def sat_mr() -> int:
    return 0x050000


def divs(x: str = "AX0", y: str = "AF") -> int:
    return 0x060000 | (ALU_Y[y] << 11) | (ALU_X[x] << 8)


def divq(x: str = "AX0") -> int:
    return 0x071000 | (ALU_X[x] << 8)


def jump(addr: int) -> int:
    return 0x180000 | ((addr & 0x3FFF) << 4) | TRUE


# ----------------------------------------------------------------- program --

@dataclass
class Case:
    suite: str
    op: str
    inputs: dict
    outputs: list[str]
    addr: int


@dataclass
class Program:
    suite: str
    pm: list[int] = field(default_factory=list)
    cases: list[Case] = field(default_factory=list)
    next_result: int = RESULT_BASE

    def emit(self, *words: int) -> None:
        self.pm.extend(words)

    def case(self, op: str, inputs: dict, setup: list[int], body: list[int],
             outputs: list[str]) -> None:
        addr = self.next_result
        self.next_result += len(outputs)
        if self.next_result > RESULT_LIMIT:
            raise ValueError(f"{self.suite}: results overflow DM")
        self.emit(*setup, *body)
        for i, reg in enumerate(outputs):
            if reg in GROUP3:
                self.emit(store_group3(addr + i, reg))
            else:
                self.emit(store_dreg(addr + i, reg))
        self.cases.append(Case(self.suite, op, inputs, outputs, addr))

    def finish(self) -> None:
        here = len(self.pm)
        self.emit(load_dreg("AX0", DONE_VALUE), store_dreg(DONE_ADDR, "AX0"),
                  jump(here + 2))
        if len(self.pm) > 0x4000:
            raise ValueError(f"{self.suite}: {len(self.pm)} PM words")

    def as_json(self) -> dict:
        return {
            "suite": self.suite,
            "pm": [f"{w:06x}" for w in self.pm],
            "done_addr": DONE_ADDR, "done_value": DONE_VALUE,
            "result_base": RESULT_BASE, "result_end": self.next_result,
            "preset": PRESET,
            "cases": [c.__dict__ for c in self.cases],
        }


VALUES = (0x0000, 0x0001, 0x7FFF, 0x8000, 0x8001, 0xFFFF, 0x5555, 0x1234)
MAC_VALUES = (0x0000, 0x0001, 0x7FFF, 0x8000, 0xFFFF, 0x4000)
SHIFT_VALUES = (0x0000, 0x0001, 0x7FFF, 0x8000, 0xFFFF, 0x0F0F)
SHIFT_COUNTS = (-17, -16, -1, 0, 1, 15, 16)
AC = 0x08                      # ASTAT carry


def preamble(p: Program) -> None:
    """Defined values in every register a case reads but does not set."""
    p.emit(load_group3("MSTAT", 0), load_group3("ASTAT", 0),
           load_group3("SB", 0), load_group3("IMASK", 0))
    for reg in DREG:
        p.emit(load_dreg(reg, 0))


def suite_alu() -> Program:
    p = Program("alu")
    preamble(p)
    carry = {"X+Y+C", "X-Y+C-1", "Y-X+C-1"}
    unary_x = {"NOT X", "ABS X"}
    unary_y = {"Y", "Y+1", "NOT Y", "-Y", "Y-1"}
    for saturate in (False, True):
        p.emit(load_group3("MSTAT", 0x08 if saturate else 0), nop())
        for name, amf in ALU_OPS.items():
            if saturate and name not in {"X+Y", "X-Y", "X+Y+C", "Y-X", "-Y", "ABS X"}:
                continue
            if name in unary_x:
                pairs = [(x, 0) for x in VALUES]
            elif name in unary_y:
                pairs = [(0, y) for y in VALUES]
            else:
                pairs = [(x, y) for x in VALUES for y in VALUES]
            for x, y in pairs:
                for c in ((0, AC) if name in carry else (0,)):
                    op = f"AR = {name}" + (" (AR_SAT)" if saturate else "")
                    p.case(op, {"x": x, "y": y, "astat": c},
                           [load_group3("ASTAT", c), load_dreg("AX0", x),
                            load_dreg("AY0", y)],
                           [alu(amf)], ["AR", "ASTAT"])
    p.emit(load_group3("MSTAT", 0), nop())

    # One signed 32/16 divide: DIVS once, DIVQ fifteen times.
    for hi, lo, d in ((0x0000, 0x0064, 0x0007), (0xFFFF, 0xFF9C, 0x0007),
                      (0x0000, 0x0064, 0xFFF9), (0x0012, 0x3456, 0x0100),
                      (0x3FFF, 0xFFFF, 0x7FFF), (0xC000, 0x0000, 0x7FFF)):
        p.case("DIVS/DIVQ", {"af": hi, "ay0": lo, "ax0": d},
               [load_group3("ASTAT", 0), load_dreg("AY1", hi),
                alu(ALU_OPS["Y"], y="AY1", to_af=True),
                load_dreg("AY0", lo), load_dreg("AX0", d)],
               [divs()] + [divq()] * 15 + [alu(ALU_OPS["Y"], y="AF")],
               ["AY0", "AR", "ASTAT"])
    p.finish()
    return p


def suite_mac() -> Program:
    p = Program("mac")
    preamble(p)
    accumulators = ((0x00, 0x0000, 0x0000), (0x00, 0x7FFF, 0xFFFF),
                    (0xFF, 0x8000, 0x0000))
    for integer in (False, True):
        p.emit(load_group3("MSTAT", 0x10 if integer else 0), nop())
        mode = " (integer)" if integer else ""
        for name, amf in MAC_OPS.items():
            accumulate = name.startswith("MR")
            for mr2, mr1, mr0 in (accumulators if accumulate else accumulators[:1]):
                for x in MAC_VALUES:
                    for y in MAC_VALUES:
                        setup = [load_group3("ASTAT", 0), load_dreg("MX0", x),
                                 load_dreg("MY0", y), load_dreg("MR2", mr2),
                                 load_dreg("MR1", mr1), load_dreg("MR0", mr0)]
                        p.case(f"MR = {name}{mode}",
                               {"x": x, "y": y, "mr": [mr2, mr1, mr0]},
                               setup, [mac(amf)], ["MR0", "MR1", "MR2", "ASTAT"])
        # Overflow into MR2, then SAT MR.
        for x, y in ((0x7FFF, 0x7FFF), (0x8000, 0x8000), (0x8000, 0x7FFF)):
            p.case(f"MR = MR+X*Y(SS); SAT MR{mode}",
                   {"x": x, "y": y, "mr": [0x00, 0x7FFF, 0xFFFF]},
                   [load_group3("ASTAT", 0), load_dreg("MX0", x),
                    load_dreg("MY0", y), load_dreg("MR2", 0),
                    load_dreg("MR1", 0x7FFF), load_dreg("MR0", 0xFFFF)],
                   [mac(MAC_OPS["MR+X*Y(SS)"]), sat_mr()],
                   ["MR0", "MR1", "MR2", "ASTAT"])
    p.emit(load_group3("MSTAT", 0), nop())
    p.finish()
    return p


def suite_shift() -> Program:
    p = Program("shift")
    preamble(p)
    outputs = ["SR0", "SR1", "SE", "SB", "ASTAT"]
    for name, sf in SHIFT_OPS.items():
        counts = SHIFT_COUNTS if sf < 12 else (0,)
        for x in SHIFT_VALUES:
            for se in counts:
                p.case(f"SR = {name} SI", {"si": x, "se": se},
                       [load_group3("ASTAT", 0), load_group3("SB", -16),
                        load_dreg("SI", x), load_dreg("SE", se),
                        load_dreg("SR1", 0x00F0), load_dreg("SR0", 0x0F00)],
                       [shift(sf)], outputs)
    for name in ("LSHIFT(HI)", "LSHIFT(LO)", "ASHIFT(HI)", "ASHIFT(LO)", "NORM(HI)"):
        for x in SHIFT_VALUES:
            for exponent in (-16, -3, 3, 16):
                p.case(f"SR = {name} SI BY {exponent}", {"si": x},
                       [load_group3("ASTAT", 0), load_dreg("SI", x),
                        load_dreg("SE", 0x55), load_dreg("SR1", 0x00F0),
                        load_dreg("SR0", 0x0F00)],
                       [shift_imm(SHIFT_OPS[name], exponent)], outputs)
    p.finish()
    return p


SUITES = {"alu": suite_alu, "mac": suite_mac, "shift": suite_shift}


def check_encoding(program: Program) -> None:
    """Every word must decode as the instruction it was built to be."""
    sys.path.insert(0, str(ROOT))
    import adsp2181_dis
    bad = [(i, w, adsp2181_dis.disas(w)) for i, w in enumerate(program.pm)
           if "?" in adsp2181_dis.disas(w) or "unknown" in adsp2181_dis.disas(w).lower()]
    if bad:
        for i, w, text in bad[:10]:
            print(f"  PM {i:04x}: {w:06x}  {text}", file=sys.stderr)
        raise SystemExit(f"{program.suite}: {len(bad)} words do not decode")


# --------------------------------------------------------------- emulator --

def load_library(path: Path = LIBRARY):
    lib = ctypes.CDLL(str(path))
    lib.adsp2181_create.restype = ctypes.c_void_p
    for name in ("adsp2181_reset", "adsp2181_destroy"):
        getattr(lib, name).argtypes = [ctypes.c_void_p]
    lib.adsp2181_set_idma_boot_hold.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.adsp2181_idma_boot_held.argtypes = [ctypes.c_void_p]
    lib.adsp2181_idma_addr_write.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
    lib.adsp2181_idma_data_write.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
    lib.adsp2181_idma_data_read.argtypes = [ctypes.c_void_p]
    lib.adsp2181_idma_data_read.restype = ctypes.c_uint16
    lib.adsp2181_run.argtypes = [ctypes.c_void_p, ctypes.c_int]
    return lib


def boot_writes(spec: dict) -> list[tuple[int, int, bool]]:
    """(address, value, is_pm) in the order the host must issue them."""
    writes = [(a, spec["preset"], False)
              for a in range(spec["result_base"], spec["result_end"])]
    writes.append((spec["done_addr"], 0, False))
    pm = [int(w, 16) for w in spec["pm"]]
    writes += [(a, w, True) for a, w in enumerate(pm) if a]
    writes.append((0, pm[0], True))
    return writes


def emulate(spec: dict, cycles: int = 5_000_000) -> dict:
    lib = load_library()
    core = lib.adsp2181_create()
    lib.adsp2181_reset(core)
    lib.adsp2181_set_idma_boot_hold(core, 1)
    for addr, value, is_pm in boot_writes(spec):
        if is_pm:
            lib.adsp2181_idma_addr_write(core, addr)
            lib.adsp2181_idma_data_write(core, value >> 8)
            lib.adsp2181_idma_data_write(core, value & 0xFF)
        else:
            lib.adsp2181_idma_addr_write(core, 0x4000 | addr)
            lib.adsp2181_idma_data_write(core, value)

    def read(addr: int) -> int:
        lib.adsp2181_idma_addr_write(core, 0x4000 | addr)
        return lib.adsp2181_idma_data_read(core)

    ran = 0
    while read(spec["done_addr"]) != spec["done_value"] and ran < cycles:
        lib.adsp2181_run(core, 10_000)
        ran += 10_000
    done = read(spec["done_addr"])
    dm = [read(a) for a in range(spec["result_base"], spec["result_end"])]
    lib.adsp2181_destroy(core)
    return {"source": "adsp2181emu direct", "done": done, "cycles": ran,
            "dm": [f"{v:04x}" for v in dm]}


# ---------------------------------------------------------------- compare --

def mismatches(spec: dict, reference: list[int], dm: list[int]) -> list[tuple[dict, str]]:
    """The cases whose outputs differ, each with a 'REG want/got' summary."""
    out = []
    for case in spec["cases"]:
        offset = case["addr"] - spec["result_base"]
        want = reference[offset:offset + len(case["outputs"])]
        got = dm[offset:offset + len(case["outputs"])]
        if want != got:
            out.append((case, ", ".join(f"{reg} {w:04x}/{g:04x}" for reg, w, g
                                         in zip(case["outputs"], want, got) if w != g)))
    return out


def compare(spec: dict, runs: list[tuple[str, list[int]]]) -> int:
    reference_name, reference = runs[0]
    count = 0
    for name, dm in runs[1:]:
        for case, diffs in mismatches(spec, reference, dm):
            count += 1
            inputs = " ".join(f"{k}={v:04x}" if isinstance(v, int) else f"{k}={v}"
                              for k, v in case["inputs"].items())
            print(f"{case['op']:<34} {inputs:<34} {reference_name}/{name}: {diffs}")
    total = len(spec["cases"]) * (len(runs) - 1)
    print(f"{spec['suite']}: {total - count}/{total} cases match "
          f"({len(spec['cases'])} cases, {len(runs) - 1} comparison(s))")
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--suite", choices=sorted(SUITES), required=True)
    b.add_argument("--out", type=Path, required=True)
    e = sub.add_parser("emulate")
    e.add_argument("spec", type=Path)
    e.add_argument("--out", type=Path, required=True)
    c = sub.add_parser("compare")
    c.add_argument("spec", type=Path)
    c.add_argument("results", type=Path, nargs="+",
                   help="result files; the first is the reference. A card "
                        "result file contributes one run per DSP.")
    args = parser.parse_args()

    if args.command == "build":
        program = SUITES[args.suite]()
        check_encoding(program)
        args.out.write_text(json.dumps(program.as_json(), indent=1))
        print(f"{args.suite}: {len(program.cases)} cases, {len(program.pm)} PM words, "
              f"DM 0x{RESULT_BASE:04x}..0x{program.next_result:04x}")
        return 0
    spec = json.loads(args.spec.read_text())
    if args.command == "emulate":
        result = emulate(spec)
        args.out.write_text(json.dumps(result, indent=1))
        ok = result["done"] == spec["done_value"]
        print(f"{spec['suite']}: done={result['done']:04x} after {result['cycles']} cycles"
              + ("" if ok else "  ** did not finish **"))
        return 0 if ok else 1
    runs = []
    for path in args.results:
        data = json.loads(path.read_text())
        for run in data.get("dsps", [data]):
            if run.get("done") != spec["done_value"]:
                print(f"{path.name} {run.get('port', '')}: did not finish "
                      f"(done={run.get('done')})")
                continue
            runs.append((f"{path.stem}{run.get('port', '')}",
                         [int(v, 16) for v in run["dm"]]))
    if len(runs) < 2:
        print("need at least two finished runs to compare", file=sys.stderr)
        return 2
    return 1 if compare(spec, runs) else 0


if __name__ == "__main__":
    sys.exit(main())
