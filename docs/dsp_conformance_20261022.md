# Running our own code on the 4BRI v2's ADSP-2181s (22 October 2026)

The eicon420 4BRI-8 PCI v2 cards have eight ADSP-2181s each. We can now boot
programs of our choosing on them and read the results back, which gives
`tools/adsp2181emu` a hardware reference at the level of single instructions.
The first conformance run found two emulator bugs, both in instructions the
firmware uses heavily.

## How the code gets onto the chip

The host driver cannot reach the DSPs on this card. In divas4linux the only
direct DSP access is `dsp_check_presence()` (`kernel/io.c:1229`), and only
the PRI code calls it. On the 4BRI the DSPs are reached through the card's
MIPS. So `tools/eicon_dsp_bench.py` builds a bare-metal MIPS program (the
stub, 576 bytes) that copies what the stock protocol image (122-11,
`te_dmlt.2qm`) does to its DSPs:

| Step | Stock image | Stub |
|---|---|---|
| Board register window | `0xbf800000`, host BAR3 (`MQ2_*` in `mi_pc.h`) | same |
| DSP reset | byte at `0xbf800000`; bit 0 ORed in at `0x80171634`, bits 1..4 are the ISAC resets | write 0, wait, write 1 |
| DSP ports | table at `0x8026f0b4`: `0xbf80n800`, `0xbf80n810`, n = 1..4 | all eight |
| Port protocol | IDMA address to `+8`, data at `+0`, bit 14 selects DM; a PM word is two writes, upper 16 then low 8 (`0x8016fe50`) | same |
| Presence check | `0x5a5a`, `0xa5a5` echo at DM 0 (`0x80194c20`) | same, recorded |
| Boot | IDMA boot: PM from 1 upwards, PM 0 last releases the core | same |

The stub then polls DM(`0x0010`) for `0x600D`, copies the result block into
card RAM and writes a completion word.

`tools/eicon_dsp_bench_card.py` (standard library only) runs on eicon420 and
does the following:

- writes the bundle to BAR2 at the reset vector;
- starts the MIPS the way `start_qBri_hardware` does: cold reset, then
  warm|cold on `MQ2_BREG_RISC`;
- polls the status block;
- puts the MIPS and the DSPs back into reset.

It refuses to run if the MIPS is not in reset, or if the adapter number, the
serial number and the PCI function don't all match.

## Running it

```bash
python3 tools/dsp_conformance.py build --suite alu --out alu.json
python3 tools/dsp_conformance.py emulate alu.json --out alu.emu.json
../v90modem/.venv/bin/python tools/eicon_dsp_bench.py bundle alu.json --out alu.bundle
../v90modem/.venv/bin/python tools/eicon_dsp_bench.py emulate alu.bundle --out alu.emucard.json
# copy eicon_dsp_bench_card.py, alu.bundle and alu.meta.json to eicon420, then there:
sudo /usr/lib/divas/divactrl load -c 5 -StopCard
sudo python3 eicon_dsp_bench_card.py --pci 0000:02:02.0 --adapter 5 --serial 12196 alu.bundle --out alu.card.json
sudo /usr/lib/divas/divactrl load -c 5 -ResetCard
sudo /usr/lib/divas/divactrl load -CfgLib -c 5 -UseCardMap "2048,0"
# back here:
python3 tools/dsp_conformance.py compare alu.json alu.emu.json alu.card.json
```

SN 12196 is adapter 5 at `0000:02:02.0`. Nothing on eicon420 uses it: the
answerer is on controllers 1-2 and the SIP gateway is on 3, all on SN 1331.
Both services stayed up throughout the run. After the restart all four
controllers report the 117-926 kernel again.

`emulate` on a bundle runs the identical card image under Unicorn, with
emulated cores behind the port registers. That is how the stub was proved
before it went near the card: on all three suites it agreed word for word
with a direct emulator run (26,080 of 26,080 comparisons).

## Suites

The suites are straight-line code. Each case loads its operands from
immediates, executes one instruction, and stores the registers that
instruction can change.

| Suite | Cases | PM words | Covers |
|---|---|---|---|
| `alu` | 1,166 | 7,139 | all 16 ALU ops; carry-in for the `+C` forms; AR_SAT for the overflowing forms; six signed DIVS + 15 x DIVQ divides |
| `mac` | 1,446 | 15,941 | RND/SS/SU/US/UU multiplies and MR+/MR- accumulates from three starting MR values, in fractional and integer mode; SAT MR after overflow |
| `shift` | 648 | 7,679 | all 16 shifter ops by SE (-17..16); LSHIFT/ASHIFT/NORM by immediate |
| `edges` | 1,240 | 12,269 | all four NORM forms by immediate and SE, AC clear/set; other immediate shifts with AC; constant-operand ALU with and without AR_SAT |
| `norm_flags` | 576 | 6,359 | NORM by SE and immediate under eight ASTAT values |

## Results

Each run took about 2 seconds. **All eight chips returned identical results
in every run**, so these are the chip's behaviour, not noise.

The first run (`alu`, `mac`, `shift`) found two bugs. Two follow-up runs
pinned them down before anything was changed:

- `edges`: 1,240 cases. NORM in all four forms, by immediate and by SE, with
  AC clear and set; the other immediate shifts with AC set; and the
  constant-operand ALU form, with and without AR_SAT.
- `norm_flags`: 576 cases. NORM by SE and by immediate under eight ASTAT
  values, to find which bits feed the right-shift fill.

| Suite | Before the fix | After the fix |
|---|---|---|
| `mac` | 1,446 / 1,446 | 1,446 / 1,446 |
| `alu` | 1,088 / 1,166 | 1,166 / 1,166 |
| `shift` | 628 / 648 | 648 / 648 |
| `edges` | 845 / 1,240 | 1,240 / 1,240 |
| `norm_flags` | 270 / 576 | 576 / 576 |

The working tree also carried an uncommitted X+Y+C carry change (AV taken
from the original operand signs). Every non-saturating X+Y+C case matches
the chip with it in place, so the hardware supports that change as well.

### Fix 1: AZ and AN under AR_SAT describe the saturated value

When AR saturates, the chip computes AZ and AN from the value it actually
writes to AR. The emulator computed them from the overflowed sum. AR, AV
and AC already matched.

| Case | Old emulator ASTAT | Chip ASTAT |
|---|---|---|
| `0x7FFF + 0x0001` -> AR `0x7FFF` | `0x06` (AN, AV) | `0x04` (AV) |
| `0x8000 + 0x8000` -> AR `0x8000` | `0x0D` (AZ, AV, AC) | `0x0E` (AN, AV, AC) |
| `ABS 0x8000` -> AR `0x7FFF` | `0x16` | `0x14` |

The fix covers both saturating paths in `2100ops.inc`: `alu_op_ar` and
`alu_op_ar_const`, the constant-operand form, which showed the same
mismatch.

### Fix 2: NORM by an immediate, and the AC fill by SE

The chip's rule, which matches all 1,536 NORM cases across the three runs:

- **By an immediate**, in all four forms, NORM shifts by `+n`: left for a
  positive count, right for a negative one, filling with zeros in both
  directions. That is the same as LSHIFT. The emulator negated the count,
  as for SE, and filled from AC.
- **By SE**, NORM shifts by `−SE`, as the emulator already did. The fill
  for a right shift (positive SE) is different, though. In the HI forms,
  every vacated bit takes AC, but only when AV and AC are both set (that is,
  after an ALU overflow). In every other case, and in the LO forms, the fill
  is zero. The emulator filled one bit from AC whenever AC was set, then
  zeros.

| Case (`SI = 0x8000`) | Old emulator | Chip |
|---|---|---|
| `NORM (HI) BY 3` | `0x10000000` | `0x00000000` (left 3) |
| `NORM (HI)`, SE = 1, AC only | `0xC0000000` | `0x40000000` |
| `NORM (HI)`, SE = 3, AV and AC | `0x30000000` | `0xF0000000` |

The NORM logic came in from MAME (`3ef6db7`). One C core test, from the
earlier uncommitted carry work, asserted the old AR_SAT flags; its
expectation is updated to the chip's. Its HIX/NORM test already expected the
AV|AC fill the chip shows.

### How much the firmware uses these

A scan of the 68 extracted `pm.bin` images under `artifacts/` (which counts
duplicates across builds) finds:

- about 2,450 NORM-by-immediate instructions;
- about 480 mode-control instructions that switch AR_SAT on or off.

### Effect on the V.90 loopback: none observable

The standard loopback was run with the current recipe from
`docs/v90_retrain_20261013.md`: the analog109 V.90A caller with the PLL
patch, the pri117 V.90 answerer, PPP and ping, 75 s. There were two runs on
the pre-fix core and two on the fixed core.

- Every run reaches DATA on both ends and holds it to the end of the call,
  with no retrains, 4 of 4 pings answered and zero FCS errors. The answerer
  transmits at `speedTx=0x2034`.
- In three of the four runs, the caller enters DATA at 22.46 s and the
  answerer at 20.82 s.
- One fixed-core run started about 140 ms late from its very first states.
  It took a different phase-4 path (`c6→c8→ca→d0`) and gave pings about
  20 ms slower. The repeat on the same build matched the baseline exactly.
- The DSP traces (`*.adsp.csv`) of the fixed and pre-fix cores are
  bit-identical up to 20.78 s on the answerer and 22.88 s on the caller.
  After that they diverge, but two runs on the *pre-fix* core diverge from
  each other at exactly the same points. That divergence is the loopback's
  own wall-clock pacing, not the fix.

So the fix changes nothing this loopback can see. Either the affected
instructions don't run on its path to DATA, or they run with operands on
which the old and new behaviour agree, for example a NORM right shift
without AV set. Captures are in `artifacts/conformance-loopback-{before,before2,after,after2}/`
(untracked).


## Files

- `tools/dsp_conformance.py`: encoder, suites, direct emulation, `compare`.
- `tools/eicon_dsp_bench.py`: the MIPS stub, the bundle builder, and the
  emulated card.
- `tools/eicon_dsp_bench_card.py`: the runner for eicon420.
- `tests/data/dsp_conformance_card12196.json`: the chip's results (one DSP;
  all eight were identical). `tests/test_dsp_conformance.py` checks against
  it for all five suites.
- `artifacts/dsp-conformance-20261022/`: the raw results from all eight
  DSPs (untracked).

## Next

- Widen the suites: DAG modulo and bit-reverse addressing, loops and
  stacks, the MSTAT/ASTAT write latencies, the AF-destination and
  flags-only ALU forms under AR_SAT, and EXP/EXPADJ with AV set.
- Read the DSP part number off a board. These chips run 16K-word PM programs
  and behave as ADSP-2181s here, but a 2185 or 2186 would differ elsewhere.
