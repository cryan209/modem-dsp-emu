#!/usr/bin/env python3
"""Random differential programs for ADSP-2181 multifunction and DAG instructions.

Each case randomises the registers its instructions read (data registers,
AF, ASTAT, MSTAT and the DAG I/M/L registers it uses), runs one to three
random instructions, and stores every register those instructions can
write.  Memory is covered by a scratch window in DM and another in PM, both
preset with random data and dumped at the end.  A mismatch therefore names
a case, and the case names its instructions.

The forms are the ones the firmware leans on: ALU/MAC with a DM read or
write through either DAG, with a PM read or write, with a dual DM+PM read,
with a register move; shifts with a memory transfer or register move; DM
write of an immediate through a DAG; MODIFY; and conditional ALU/MAC.  DAG
setups cover linear, circular (power-of-two and odd lengths, written in
either order relative to I) and bit-reversed DAG1 addressing.

    dsp_fuzz.py build --seed 1 --out fuzz1.json
    dsp_conformance.py emulate fuzz1.json --out fuzz1.emu.json

The output is an ordinary dsp_conformance spec, so emulate, compare and
the card bench all take it unchanged.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dsp_conformance as dc  # noqa: E402
from dsp_conformance import (  # noqa: E402
    ALU_OPS, DREG, MAC_OPS, Program, load_dag, load_dreg, load_group3, nop,
    store_dreg, store_group3)
import adsp2181_dis  # noqa: E402

DM_SCRATCH = (0x2000, 0x3000)
PM_SCRATCH = (0x3F00, 0x3F40)
PM_DUMP = 0x3000            # DM copy of the PM scratch: (upper 16, PX) pairs
RESULT_END = 0x2000          # results must stop below the DM scratch
PM_LIMIT = PM_SCRATCH[0] - 3 * (PM_SCRATCH[1] - PM_SCRATCH[0]) - 16

ALU_X = ("AX0", "AX1", "AR", "MR0", "MR1", "MR2", "SR0", "SR1")
ALU_Y = ("AY0", "AY1", "AF", None)          # 3 reads as zero
MAC_X = ("MX0", "MX1", "AR", "MR0", "MR1", "MR2", "SR0", "SR1")
MAC_Y = ("MY0", "MY1", None, None)          # MF (2) is left out: not loadable
SHIFT_X = ("SI", "SI", "AR", "MR0", "MR1", "MR2", "SR0", "SR1")
DUAL_DM = ("AX0", "AX1", "MX0", "MX1")
DUAL_PM = ("AY0", "AY1", "MY0", "MY1")
MAC_AMF = [a for a in MAC_OPS.values()] + [0x09, 0x0A, 0x0D, 0x0E, 0x0F]
ACCUMULATING = {0x02, 0x03} | set(range(0x08, 0x10))
EDGES = (0x0000, 0x0001, 0x7FFF, 0x8000, 0x8001, 0xFFFF, 0x4000, 0xC000)


def reverse14(value: int) -> int:
    return int(f"{value & 0x3FFF:014b}"[::-1], 2)


class Case:
    """One case under construction: what it reads, runs and writes."""

    def __init__(self, rng: random.Random, bit_reverse: bool) -> None:
        self.rng = rng
        self.reads: set[str] = {"ASTAT"}
        self.writes: set[str] = {"ASTAT"}
        self.body: list[int] = []
        self.text: list[str] = []
        self.dags: dict[int, str] = {}     # I register -> "dm" / "pm"
        self.bit_reverse = bit_reverse
        self.af = False

    # -- operands --------------------------------------------------------
    def compute(self, alu: bool, allow_af: bool = True) -> tuple[int, int, int, bool]:
        """Random (amf, xop, yop, to_f) for an ALU or MAC op; records reads/writes."""
        r = self.rng
        if alu:
            amf = r.choice(list(ALU_OPS.values()))
            xop, yop = r.randrange(8), r.randrange(4)
            self.reads.add(ALU_X[xop])
            if ALU_Y[yop]:
                self.reads.add(ALU_Y[yop])
            to_f = allow_af and r.random() < 0.3
            if yop == 2 or to_f:
                self.af = True
            self.writes.add("AF" if to_f else "AR")
        else:
            amf = r.choice(MAC_AMF + [0x00])      # 0: no compute, transfer only
            xop, yop = r.randrange(8), r.choice((0, 1, 3))
            self.reads.add(MAC_X[xop])
            if MAC_Y[yop]:
                self.reads.add(MAC_Y[yop])
            if amf in ACCUMULATING:
                self.reads |= {"MR0", "MR1", "MR2"}
            if amf:
                self.writes |= {"MR0", "MR1", "MR2"}
            to_f = False
        return amf, xop, yop, to_f

    def dag(self, space: str, dag2: bool) -> tuple[int, int]:
        """Pick an I/M pair in one DAG, consistent with this case's other uses."""
        r = self.rng
        candidates = [i for i in (range(4, 8) if dag2 else range(4))
                      if self.dags.get(i, space) == space]
        i = r.choice(candidates)
        self.dags[i] = space
        return i % 4, r.randrange(4)

    # -- instruction forms -----------------------------------------------
    def memory_compute(self) -> None:
        """ALU/MAC with a DM read/write (either DAG) or a PM read/write."""
        r = self.rng
        alu = r.random() < 0.6
        pm = r.random() < 0.25
        write = r.random() < 0.4
        amf, xop, yop, to_f = self.compute(alu)
        dag2 = True if pm else r.random() < 0.5
        i, m = self.dag("pm" if pm else "dm", dag2)
        dreg = r.choice(list(DREG))
        if write:
            self.reads.add(dreg)
        else:
            self.writes.add(dreg)
        if pm:
            self.writes.add("PX")
            if write:
                self.reads.add("PX")
            base = 0x500000
        else:
            base = 0x600000 | (int(dag2) << 20)
        op = base | (int(write) << 19) | (int(to_f) << 18) | (amf << 13) \
            | (yop << 11) | (xop << 8) | (DREG[dreg] << 4) | (i << 2) | m
        self.body.append(op)

    def dual_read(self) -> None:
        r = self.rng
        alu = r.random() < 0.5
        amf, xop, yop, _ = self.compute(alu, allow_af=False)
        dd, pd = r.randrange(4), r.randrange(4)
        di, dm_ = self.dag("dm", False)
        pi, pm_ = self.dag("pm", True)
        self.writes |= {DUAL_DM[dd], DUAL_PM[pd], "PX"}
        self.body.append(0xC00000 | (pd << 20) | (dd << 18) | (amf << 13) | (yop << 11)
                         | (xop << 8) | (pi << 6) | (pm_ << 4) | (di << 2) | dm_)

    def register_move(self) -> None:
        r = self.rng
        alu = r.random() < 0.6
        amf, xop, yop, to_f = self.compute(alu)
        src, dst = r.choice(list(DREG)), r.choice(list(DREG))
        self.reads.add(src)
        self.writes.add(dst)
        op = 0x280000 | (int(to_f) << 18) | (amf << 13) | (yop << 11) | (xop << 8) | (DREG[dst] << 4) | DREG[src]
        self.body.append(op)

    def shift_transfer(self) -> None:
        r = self.rng
        sf = r.randrange(16)
        xop = r.randrange(8)
        self.reads |= {SHIFT_X[xop], "SE"}
        if sf in (1, 3, 5, 7, 9, 11):
            self.reads |= {"SR0", "SR1"}
        if sf == 15:
            self.reads.add("SB")
        self.writes |= {"SE"} if sf in (12, 13, 14) else {"SB"} if sf == 15 else {"SR0", "SR1"}
        kind = r.randrange(4)               # 0 move, 1 PM DAG2, 2 DM DAG1, 3 DM DAG2
        if kind == 0:
            src, dst = r.choice(list(DREG)), r.choice(list(DREG))
            self.reads.add(src)
            self.writes.add(dst)
            self.body.append(0x100000 | (sf << 11) | (xop << 8) | (DREG[dst] << 4) | DREG[src])
            return
        write = r.random() < 0.4
        i, m = self.dag("pm" if kind == 1 else "dm", kind != 2)
        dreg = r.choice(list(DREG))
        (self.reads if write else self.writes).add(dreg)
        if kind == 1:
            self.writes.add("PX")
            if write:
                self.reads.add("PX")
        self.body.append(0x100000 | (kind << 16) | (int(write) << 15) | (sf << 11)
                         | (xop << 8) | (DREG[dreg] << 4) | (i << 2) | m)

    def write_immediate(self) -> None:
        dag2 = self.rng.random() < 0.5
        i, m = self.dag("dm", dag2)
        data = self.rng.randrange(0x10000)
        self.body.append(0xA00000 | (int(dag2) << 20) | (data << 4) | (i << 2) | m)

    def modify(self) -> None:
        dag2 = self.rng.random() < 0.5
        space = "pm" if dag2 and self.rng.random() < 0.3 else "dm"
        i, m = self.dag(space, dag2)
        self.body.append(0x090000 | (int(dag2) << 4) | (i << 2) | m)

    def conditional(self) -> None:
        r = self.rng
        alu = r.random() < 0.6
        amf, xop, yop, to_f = self.compute(alu)
        if not alu and not amf:
            amf = 0x04
            self.writes |= {"MR0", "MR1", "MR2"}
        # The result register keeps its old value when the condition fails.
        self.reads |= {"AF" if to_f else "AR"} if alu else {"MR0", "MR1", "MR2"}
        if to_f:
            self.af = True
        cond = r.choice([c for c in range(16) if c != 14])     # CE needs CNTR
        self.body.append((0x2 << 20) | (int(to_f) << 18) | (amf << 13) | (yop << 11)
                         | (xop << 8) | cond)


FORMS = (("memory_compute", 5), ("dual_read", 3), ("register_move", 2),
         ("shift_transfer", 2), ("write_immediate", 1), ("modify", 1),
         ("conditional", 2))


def value(rng: random.Random) -> int:
    return rng.choice(EDGES) if rng.random() < 0.3 else rng.randrange(0x10000)


def dag_setup(rng: random.Random, i: int, space: str, bit_reverse: bool) -> tuple[list[int], dict, int, bool]:
    """Loads for I and L of DAG register i, with the bound its accesses put on M.

    An instruction names any M register of the DAG it uses, not necessarily
    the one that goes with its I register, so the M registers are loaded per
    DAG by `dag_modifiers` once every I register in it is known.  The bound
    is the largest |M| that keeps this I register's accesses inside its
    window; `step4` is set when they must also be multiples of four.
    """
    lo, hi = PM_SCRATCH if space == "pm" else DM_SCRATCH
    if i < 4 and bit_reverse and space == "dm":
        # reverse14() of an I that is 1 mod 4 lands in 0x2000..0x2fff, and an
        # M that is 0 mod 4 keeps it there.
        address = rng.randrange(0x2000, 0x3000)
        ival, lval, bound, step4 = reverse14(address), 0, 16, True
    else:
        span = hi - lo
        lval = rng.choice((0, 0, 0, rng.randint(1, 64), rng.choice((4, 8, 16, 32, 64))))
        if space == "pm":
            lval = min(lval, span)
        step4 = False
        if lval:
            ival = rng.randrange(lo + 64, hi - 64) if space == "dm" else rng.randrange(lo, hi)
            bound = lval - 1
        else:
            margin = 16 if space == "dm" else 8
            ival = rng.randrange(lo + margin, hi - margin)
            bound = 16 if space == "dm" else 4
    regs = {f"I{i}": ival, f"L{i}": lval}
    order = ["L", "I"] if rng.random() < 0.5 else ["I", "L"]
    words = [load_dag(f"{k}{i}", regs[f"{k}{i}"]) for k in order]
    return words, {**regs, f"order{i}": "".join(order)}, bound, step4


def dag_modifiers(rng: random.Random, group: int, bound: int,
                  step4: bool) -> tuple[list[int], dict]:
    """Loads for all four M registers of one DAG, within `bound`."""
    words, regs = [], {}
    for n in range(4 * group, 4 * group + 4):
        if step4:
            mval = 4 * rng.randint(-(bound // 4), bound // 4)
        else:
            mval = rng.randint(-bound, bound)
        regs[f"M{n}"] = mval
        words.append(load_dag(f"M{n}", mval))
    return words, regs


def build(seed: int, max_cases: int = 10_000) -> Program:
    rng = random.Random(seed)
    p = Program(f"fuzz{seed}")
    dc.preamble(p)
    for reg in range(8):                  # every L defined, linear by default
        p.emit(load_dag(f"L{reg}", 0))
    forms = [name for name, weight in FORMS for _ in range(weight)]
    while len(p.cases) < max_cases:
        mstat = rng.choice((0, 0, 0x02, 0x04, 0x08, 0x10, 0x0C, 0x1A))
        case = Case(rng, bool(mstat & 0x02))
        for _ in range(rng.choice((1, 1, 2, 3))):
            getattr(case, rng.choice(forms))()
        if case.af:
            case.reads.add("AF")

        setup: list[int] = []
        inputs: dict = {"mstat": mstat}
        if "AF" in case.reads:
            af = value(rng)
            setup += [load_dreg("AY1", af), dc.alu(ALU_OPS["Y"], y="AY1", to_af=True)]
            inputs["AF"] = af
        setup += [load_group3("MSTAT", mstat), nop()]
        astat = rng.randrange(0x80)
        setup.append(load_group3("ASTAT", astat))
        inputs["ASTAT"] = astat
        if "PX" in case.reads:
            px = rng.randrange(0x100)
            setup.append(load_group3("PX", px))
            inputs["PX"] = px
        for reg in sorted(case.reads & set(DREG), key=lambda r: r == "MR2"):
            v = rng.randrange(-40, 41) & 0xFFFF if reg == "SE" else value(rng)
            setup.append(load_dreg(reg, v))
            inputs[reg] = v
        if "SB" in case.reads:
            sb = rng.randint(-16, 15)
            setup.append(load_group3("SB", sb))
            inputs["SB"] = sb
        bounds = {0: 16, 1: 16}
        step4 = {0: False, 1: False}
        for i, space in sorted(case.dags.items()):
            words, regs, bound, four = dag_setup(rng, i, space, case.bit_reverse)
            setup += words
            inputs.update(regs)
            group = i // 4
            bounds[group] = min(bounds[group], bound)
            step4[group] = step4[group] or four
        for group in sorted({i // 4 for i in case.dags}):
            words, regs = dag_modifiers(rng, group, bounds[group], step4[group])
            setup += words
            inputs.update(regs)

        outputs = sorted(case.writes - {"AF"}, key=lambda r: (r not in DREG, r))
        outputs += [f"I{i}" for i in sorted(case.dags)]
        if "AF" in case.writes:
            outputs.append("AF")
        words = len(setup) + len(case.body) + len(outputs) + 1
        if len(p.pm) + words > PM_LIMIT or p.next_result + len(outputs) > RESULT_END:
            break
        p.case(" ; ".join(adsp2181_dis.disas(w) for w in case.body), inputs, setup,
               case.body, outputs)
    _dump_pm_scratch(p)
    p.finish()
    return p


def _dump_pm_scratch(p: Program) -> None:
    """Copy PM scratch to DM: upper 16 bits via AX0, low 8 via PX."""
    p.emit(load_group3("MSTAT", 0), nop(), load_dag("L4", 0),
           load_dag("I4", PM_SCRATCH[0]), load_dag("M4", 1))
    for n in range(PM_SCRATCH[1] - PM_SCRATCH[0]):
        p.emit(0x500000 | (DREG["AX0"] << 4) | (0 << 2) | 0,     # AX0 = PM(I4,M4)
               store_dreg(PM_DUMP + 2 * n, "AX0"),
               store_group3(PM_DUMP + 2 * n + 1, "PX"))


def spec(seed: int) -> dict:
    p = build(seed)
    out = p.as_json()
    rng = random.Random(seed ^ 0x5EED)
    out["result_end"] = PM_DUMP + 2 * (PM_SCRATCH[1] - PM_SCRATCH[0])
    out["dm_init"] = [[a, rng.randrange(0x10000)] for a in range(*DM_SCRATCH)]
    out["pm_init"] = [[a, rng.randrange(0x1000000)] for a in range(*PM_SCRATCH)]
    out["regions"] = {"cases": [dc.RESULT_BASE, p.next_result],
                      "dm_scratch": list(DM_SCRATCH),
                      "pm_scratch_copy": [PM_DUMP, out["result_end"]]}
    return out


def check(specs: dict[str, dict], results: list[dict], limit: int = 40) -> int:
    """Compare each DSP's results with its own program on the emulator."""
    emulated: dict[str, list[int]] = {}
    total = 0
    for result in results:
        for dsp in result["dsps"]:
            name, sp = dsp.get("suite"), specs.get(dsp.get("suite"))
            label = f"{name}@{dsp['port']}"
            if sp is None:
                print(f"{label}: no spec given")
                continue
            if dsp["done"] != sp["done_value"]:
                print(f"{label}: did not finish (done={dsp['done']:04x})")
                total += 1
                continue
            if name not in emulated:
                emulated[name] = [int(v, 16) for v in dc.emulate(sp)["dm"]]
            emu, card = emulated[name], [int(v, 16) for v in dsp["dm"]]
            cases = dc.mismatches(sp, card, emu)
            base = sp["result_base"]
            memory = [(region, a) for region, (lo, hi) in sp["regions"].items()
                      if region != "cases" for a in range(lo, hi)
                      if card[a - base] != emu[a - base]]
            total += len(cases) + len(memory)
            print(f"{label}: {len(sp['cases']) - len(cases)}/{len(sp['cases'])} cases match, "
                  f"{len(memory)} memory words differ")
            for case, diffs in cases[:limit]:
                inputs = " ".join(f"{k}={v:x}" if isinstance(v, int) else f"{k}={v}"
                                  for k, v in case["inputs"].items())
                print(f"  case @{case['addr']:04x} {case['op']}\n      chip/emu: {diffs}\n      in: {inputs}")
            for region, a in memory[:limit]:
                print(f"  {region} DM {a:04x}: chip {card[a - base]:04x} emu {emu[a - base]:04x}")
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--seed", type=int, required=True)
    b.add_argument("--out", type=Path, required=True)
    c = sub.add_parser("check", help="compare card results with the emulator")
    c.add_argument("--spec", type=Path, nargs="+", required=True)
    c.add_argument("results", type=Path, nargs="+")
    args = parser.parse_args()
    if args.command == "check":
        specs = {}
        for path in args.spec:
            sp = json.loads(path.read_text())
            specs[sp["suite"]] = sp
        results = [json.loads(path.read_text()) for path in args.results]
        return 1 if check(specs, results) else 0
    s = spec(args.seed)
    args.out.write_text(json.dumps(s, indent=1))
    print(f"seed {args.seed}: {len(s['cases'])} cases, {len(s['pm'])} PM words")
    return 0


if __name__ == "__main__":
    sys.exit(main())
