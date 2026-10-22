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
instruction can change. The last five came out of the random differential
programs described under *The fuzzer* below.

| Suite | Cases | PM words | Covers |
|---|---|---|---|
| `alu` | 1,166 | 7,139 | all 16 ALU ops; carry-in for the `+C` forms; AR_SAT for the overflowing forms; six signed DIVS + 15 x DIVQ divides |
| `mac` | 1,446 | 15,941 | RND/SS/SU/US/UU multiplies and MR+/MR- accumulates from three starting MR values, in fractional and integer mode; SAT MR after overflow |
| `shift` | 648 | 7,679 | all 16 shifter ops by SE (-17..16); LSHIFT/ASHIFT/NORM by immediate |
| `edges` | 1,240 | 12,269 | all four NORM forms by immediate and SE, AC clear/set; other immediate shifts with AC; constant-operand ALU with and without AR_SAT |
| `norm_flags` | 576 | 6,359 | NORM by SE and immediate under eight ASTAT values |
| `satflags` | 1,344 | 8,769 | seven ALU ops to AR and to AF, overflowing and not, under all four combinations of AR_SAT and AV_LATCH and four incoming ASTAT values |
| `multiwrite` | 125 | 1,445 | multifunction instructions whose ALU/MAC/shifter result register is also the destination of the transfer beside it, and memory writes from a register the same instruction changes |
| `dagmod` | 976 | 7,341 | circular DM reads and MODIFY over both DAGs, legal and illegal buffers, L written before and after I; bit-reversed DAG1 reads |
| `dagmod2` | 2,095 | 10,498 | the circular wrap swept over L = 1..8, 12, 16, 34, 64, every offset in the buffer, and M from -20 to 20 |
| `shiftxfer` | 720 | 10,105 | every shifter form against a transfer into the register it writes, from a register and from memory, with AV set and clear and SE at -15 |
| `loops` | 49 | 1,026 | DO UNTIL CE at counts 0 to 8,191 with one- and two-word bodies; every other loop condition; CE loops nested two, three and four deep; CE on a conditional compute |
| `stacks` | 94 | 4,298 | CALL/RTS one to three deep and through a DAG; every condition code on a CALL; RTI; PUSH STS / POP STS one to fourteen deep; the counter stack through CNTR, OWRCNTR and POP CNTR; CE on a CALL and a JUMP; pops of empty stacks |
| `seqprobe` | 37 | 749 | *not asserted*: the questions the loops run left open -- CE outside a loop, the counter's width, the status stack's depth, TOPPC |
| `seqprobe2` | 31 | 1,921 | *not asserted*: the same from a drained start, plus a read of TOPPC inside a called subroutine |

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


## The fuzzer, and the five bugs it found

`tools/dsp_fuzz.py` writes the same kind of program at random. Each case
randomises every register its instructions read -- data registers, AF, ASTAT,
MSTAT and the DAG I/M/L registers -- runs one to three instructions, and stores
every register they can write; a scratch window in DM and another in PM are
preset with random data and dumped at the end. The forms are the ones the
firmware leans on: ALU/MAC with a DM or PM transfer or a register move, dual
DM+PM reads, shifts with a transfer, immediate DM writes, MODIFY, and
conditional computes. The output is an ordinary `dsp_conformance` spec, so
`emulate`, `compare` and the card bench take it unchanged.

```bash
python3 tools/dsp_fuzz.py build --seed 1 --out fuzz1.json
python3 tools/dsp_fuzz.py check fuzzA.card.json --spec fuzz1.json fuzz2.json ...
```

Eight seeds, one per DSP, give about 5,400 cases a run. The first run against
the card matched 91% of them. Every one of the rest came from five emulator
bugs, each then pinned down by a suite of its own and fixed:

### AR_SAT looked at a latched AV, not at this operation's overflow

With AV_LATCH (MSTAT 0x04) the chip keeps AV set until something clears it,
and the firmware uses that. The emulator's saturation test was `MSTAT & AR_SAT
&& AV`, so once AV was latched *every* ALU operation saturated, including ones
that cannot overflow:

| Case | mstat | ASTAT in | Old emulator AR | Chip AR |
|---|---|---|---|---|
| `AR = 0x1868 + 0x8EB8` | 0x0C | AV | `0x7FFF` | `0xA720` |
| `AR = 0x1234 + 1` | 0x0C | AV | `0x7FFF` | `0x1235` |
| `AR = 0x5617 XOR 0x2397` | 0x0C | AV | `0x7FFF` | `0x7580` |

The first three runs always entered with ASTAT clear, which is why they missed
it. The fix puts the incoming AV aside for the duration of the operation, so
`GET_V` is this operation's own overflow, and restores it afterwards when
AV_LATCH is on.

### The flags follow the saturated value for an AF destination too

`AF = X + Y` under AR_SAT leaves the unsaturated sum in AF -- the chip does not
saturate AF -- but reports AZ and AN of the value AR would have been given:
`AF = 0x7FFF + 1` gives AF `0x8000` with ASTAT `0x04`, not `0x06`. The
`satflags` suite covers both destinations under all four MSTAT combinations;
all 1,344 cases match.

### A multifunction MAC or shift beats the transfer beside it

When an instruction's compute result register is also the destination of the
transfer beside it, which write survives depends on the unit:

| Instruction | Chip keeps |
|---|---|
| `AR = AX0 + AY0, AR = DM(I0,M0)` | the word read from memory |
| `AR = AX0 + AY0, AR = AX1` | AX1 |
| `MR = MX0 * MY0 (SS), MR0 = DM(I0,M0)` | the product's low word |
| `SR = LSHIFT SI (HI), SR0 = DM(I0,M0)` | the shifter's SR0 |
| `DM(I0,M0) = AR, AR = AX0 + AY0` | memory gets the *old* AR |

So the ALU's AR write loses to the transfer, while the MAC's MR and the
shifter's SR win; the transfer's own source is read before anything is
written in either case. SE is a third behaviour: when an EXP form and the
transfer both write it, the chip ends up with the bitwise OR of the two
eight-bit values, as two drivers on one bus would. For `SR = EXP SI (HI),
SE = AX1` with SI `0x0F0F` (exponent -3, `0xFD`) and AX1 `0x2222` (`0x22`) the
card returns SE `0xFF`. The `multiwrite` and `shiftxfer` suites cover the
combinations, 845 cases.

### EXPADJ lowered SB

EXPADJ keeps the largest exponent of a block in SB and never lowers it. The
emulator had the rule but compared an unsigned `res` against a signed `-SB`,
which promoted a positive SB to a huge unsigned value, so every EXPADJ wrote
SB. With SB = 7 the chip keeps 7 where the emulator wrote -5. One cast.

### The circular-buffer wrap is one conditional add, not a modulus

The manual gives `next = (I + M - base) modulo L + base` for `|M| < L` and an I
inside the buffer. The chip adds M to I and then corrects by one L, or not at
all, and it decides that from a single bit. Writing n for the number of offset
bits the buffer occupies -- L = 5 and L = 8 both use three -- and s for the
offset part of I plus M:

- `M >= 0`: subtract L when bit n of `s - L` is clear;
- `M < 0`: add L when bit n of s is set.

For a legal buffer that is the manual's rule. Where the firmware is sloppy it
is not, and the emulator's compare against `base` and `base + L` wrapped where
the chip does nothing:

| I | L | M | Old emulator | Chip |
|---|---|---|---|---|
| `0x2807` | 8 | +9 | `0x2808` | `0x2810` |
| `0x2807` | 5 | -1 | `0x2801` | `0x2806` |
| `0x2832` | 0x22 | -1 | `0x280F` | `0x2831` |
| `0x2800` | 8 | +9 | `0x2801` | `0x2801` |

The last row is the one that rules out "no wrap when `|M| >= L`": the
correction is periodic in M with period 2^(n+1). `dagmod2` sweeps L = 1..8, 12,
16, 34 and 64 over every offset and M from -20 to 20; all 2,095 cases fit the
rule above, and so do the DM-read and bit-reversed cases of `dagmod`.

## Results of the fourth to sixth runs

| Suite | Before | After |
|---|---|---|
| eight fuzz seeds | 4,933 / 5,413 | 5,413 / 5,413 |
| `satflags` | 1,146 / 1,344 | 1,344 / 1,344 |
| `multiwrite` | 41 / 125 | 125 / 125 |
| `dagmod` | 936 / 976 | 976 / 976 |
| `dagmod2` | 1,340 / 2,095 | 2,095 / 2,095 |
| `shiftxfer` | 624 / 720 | 720 / 720 |

A final confirmation run put all ten suites back on the card: 11,336 case
comparisons, none mismatched. The four-way copies in that run agreed word for
word, as every run so far has.


## Loops, the counter and the stacks (23 October 2026)

The seventh run put two new suites and six control-flow fuzz programs on the
card. The `loops` suite came back; everything containing a CALL hung its DSP,
which took three more runs and two probe suites to explain. Both causes were
in the bench, not the emulator:

- **A loop may not end on a branch.** The fuzzer wrapped a case's whole body
  in `DO UNTIL CE`, and when the last instruction of that body was the CALL it
  had generated, the chip never left the loop. The manual's restriction on the
  last instructions of a loop is not advice. The fuzzer now keeps two NOPs
  after a call inside a loop.
- **The progress marker needs its modifier too.** `Program.progress` writes the
  completion word through I7 after every case, so a case that hangs still
  leaves the earlier results readable. It post-modifies through M7, and a fuzz
  case that loaded M7 sent the marker walking across data memory and into the
  control registers at `0x3fe0`, which is a reliable way to stop a DSP. The
  fuzzer reserves both when progress marking is on.

With those out of the way, the chip answered six questions about the counter
and the stacks, and all six were emulator bugs.

### CE means "the counter holds 1", and testing it costs nothing

The emulator decremented the counter inside the condition itself, so every
conditional instruction spent a count. The chip's rule:

| Instruction | CNTR before | Runs? | CNTR after |
|---|---|---|---|
| `IF NOT CE AR = X + Y` | 0 | yes | 0 |
| `IF NOT CE AR = X + Y` | 1 | **no** | 1 |
| `IF NOT CE AR = X + Y` | 2 | yes | 2 |
| `IF NOT CE AR = X + Y` | 0x3fff | yes | 0x3fff |
| `IF NOT CE CALL leaf` | 3 | yes | **2** |
| `IF NOT CE CALL leaf` | 1 | no | 0 (the counter stack is popped) |

So the counter is expired when it holds 1, and the test leaves it alone. A
count is spent only by an instruction the sequencer owns -- a jump, a call, a
return, or the end of a DO UNTIL -- and when the count was already expired
that instruction pops the counter stack instead.

### A count of 0 is 16,384 passes

The counter is 14 bits, and `DO UNTIL CE` with CNTR = 0 wraps through 0x3fff:
the card ran the body 16,384 times, leaving `I0` at 0x0000 and the body's
accumulator at 0x4000. The emulator's signed decrement made that one pass.

### The count moves after the loop's last instruction

A loop's condition is decided before its last instruction runs -- which is
what gives a flag-driven loop its one-pass lag, and the card agrees with the
emulator there -- but the counter itself must not move until afterwards. When
the last instruction of a CE loop is itself conditional on CE, the chip tests
the count the pass started with. Three fuzz cases turned on this alone.

### An empty stack reads as zeros

`POP CNTR` with nothing pushed leaves CNTR at 0, not at its old value, and
`POP STS` with nothing pushed clears ASTAT, MSTAT and IMASK. The emulator left
all of them alone. `POP LOOP` and `POP PC` on an empty stack show nothing
either way.

### The status stack holds twelve entries

`PUSH STS` twelve times and `POP STS` twelve times brings the first ASTAT,
MSTAT and IMASK back; thirteen does not, and SSTAT's overflow bit says so. The
emulator had four. The counter and loop stacks are four, as modelled, and the
PC stack sixteen.

### TOPPC, and the one thing the card says that we do not believe

Both halves of TOPPC are firmware idiom: `TOPPCSTACK = DM(0x35b1)` sets up a
computed return, `DM(0x3731) = TOPPCSTACK` saves one, and there are hundreds of
each across the extracted images. A write pushes the value -- the card returns
to the address written -- and a read returns the top of the stack and pops it,
which is what the emulator already did and what the V.90 loopback needs: with
the read not popping, the caller stops at TrnProgress `0x00c8` and never
reaches data mode.

The card nevertheless reads TOPPC as **zero**, in both probe suites, even
inside a subroutine whose return address is certainly on the stack. In every
one of those cases the read is the instruction straight after the push, which
is the one sequence a pipelined stack would get wrong, and it is a sequence the
firmware never writes. So this is recorded as a write latency we do not model,
not as the register's value. It is the only place where the emulator is
knowingly not what the card measured, and the loopback is the reason.

The companion puzzle is SSTAT's PC_EMPTY bit: writing TOPPC clears it, as a
push should, but after one write and two reads the card reports the stack
empty, after two writes and two reads it does not, and a read inside a
subroutine leaves it clear until the subroutine has returned. Reads that pop
explain the first and third, reads that do not explain the second.

`seqprobe` and `seqprobe2` hold both sequences and are deliberately not
asserted in the tests; everything else in them matches.

### Results of the seventh to fourteenth runs

| Suite | Before | After |
|---|---|---|
| `loops` | 33 / 49 | 49 / 49 |
| `stacks` | hung the DSP | 94 / 94 |
| six control-flow fuzz seeds | hung the DSP | 3,191 / 3,191 |
| `seqprobe` | 256 / 296 | 66 / 74 (the TOPPC reads) |
| `seqprobe2` | 0 / 248 | 58 / 62 (the TOPPC reads) |

The ten suites from the earlier runs still match the chip, and the V.90
loopback still reaches data mode on both ends.


## Files

- `tools/dsp_conformance.py`: encoder, suites, direct emulation, `compare`.
- `tools/eicon_dsp_bench.py`: the MIPS stub, the bundle builder, and the
  emulated card.
- `tools/eicon_dsp_bench_card.py`: the runner for eicon420.
- `tools/dsp_fuzz.py`: the random differential programs and their `check`;
  `build --progress` reserves I7 and M7 to mark the program complete after
  every case, which is how a card run says which case did not return.
- `tests/data/dsp_conformance_card12196.json`: the chip's results (one DSP;
  the DSPs in a run were identical). `tests/test_dsp_conformance.py` checks
  against it for all ten suites.
- `artifacts/dsp-conformance-20261022/`: the raw results from all eight
  DSPs (untracked).

## Next

- Still untested: the MSTAT/ASTAT write latencies, the flags-only ALU form
  under AR_SAT, interrupts and the timer, and the serial ports.
- Open: a read of TOPPC in the instruction after a push, and the moment
  SSTAT's PC_EMPTY bit turns on.  A probe that leaves a few instructions
  between the push and the read would settle the first.
- Overflowing the loop stack, and ending a loop on a branch, are things the
  chip does not come back from, so neither can be asked here.
- The fuzzer does not generate loops, calls or interrupts, so its programs
  stay straight-line. Extending it there is the obvious next widening.
- Read the DSP part number off a board. These chips run 16K-word PM programs
  and behave as ADSP-2181s here, but a 2185 or 2186 would differ elsewhere.
