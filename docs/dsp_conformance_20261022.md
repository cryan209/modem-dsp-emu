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

## Results

Each suite ran in about 2 seconds. **All eight chips returned identical
results**, so these are the chip's behaviour, not noise.

| Suite | Emulator vs chip |
|---|---|
| `mac` | 1,446 / 1,446 cases match |
| `alu` | 1,088 / 1,166; every mismatch is a flag under AR_SAT |
| `shift` | 628 / 648; every mismatch is NORM by an immediate |

### Bug 1: AZ and AN under AR_SAT come from the saturated result

When AR saturates, the chip computes AZ and AN from the value it actually
writes to AR. The emulator computes them from the overflowed sum. AR, AV and
AC already match.

| Case | Emulator ASTAT | Chip ASTAT |
|---|---|---|
| `0x7FFF + 0x0001` -> AR `0x7FFF` | `0x06` (AN, AV) | `0x04` (AV) |
| `0x8000 + 0x8000` -> AR `0x8000` | `0x0D` (AZ, AV, AC) | `0x0E` (AN, AV, AC) |
| `ABS 0x8000` -> AR `0x7FFF` | `0x16` | `0x14` |

The fix is in `alu_op_ar` in `tools/adsp2181emu/2100ops.inc`, after the
`/* saturate */` step. The `_const` and AF variants need checking for the
same issue.

### Bug 2: NORM by an immediate shifts left for a positive count

On the chip, `SR = NORM SI (HI) BY n` shifts by `+n`, the same direction as
LSHIFT. For example, `0x0001 BY 3` gives SR1 = `0x0008`. The emulator's
`shift_op_imm` negates the count, as the register form correctly does with
SE, so it shifts the other way. The NORM-by-SE cases all match.

This logic came in from MAME (`3ef6db7`) and had never been checked until
now. Only the HI form was tested by immediate. HI-OR, LO and LO-OR need a
run before their sign is changed, and so does AC injection on a right shift
(every test ran with AC clear).

### How much the firmware uses these

A scan of the 68 extracted `pm.bin` images under `artifacts/` (which counts
duplicates across builds) finds:

- about 2,450 NORM-by-immediate instructions;
- about 480 mode-control instructions that switch AR_SAT on or off.

Both bugs are therefore live in the code the modem work emulates. Neither
has been fixed yet.

## Files

- `tools/dsp_conformance.py`: encoder, suites, direct emulation, `compare`.
- `tools/eicon_dsp_bench.py`: the MIPS stub, the bundle builder, and the
  emulated card.
- `tools/eicon_dsp_bench_card.py`: the runner for eicon420.
- `tests/data/dsp_conformance_card12196.json`: the chip's results (one DSP;
  all eight were identical). `tests/test_dsp_conformance.py` checks against
  it, with the two bugs marked as expected failures.
- `artifacts/dsp-conformance-20261022/`: the raw results from all eight
  DSPs (untracked).

## Next

- Fix the two bugs. Then flip the expected failures in the test, and re-run
  the loopback to see whether anything that depended on them changes.
- Widen the suites: NORM HI-OR/LO/LO-OR by immediate, AC injection, the
  `_const` ALU forms, DAG modulo and bit-reverse addressing, loops and
  stacks, and the MSTAT/ASTAT write latencies.
- Read the DSP part number off a board. These chips run 16K-word PM programs
  and behave as ADSP-2181s here, but a 2185 or 2186 would differ elsewhere.
