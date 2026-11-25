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
| `seqprobe` | 37 | 749 | CE outside a loop, the counter's width, the status stack's depth, TOPPC |
| `seqprobe2` | 31 | 1,921 | the same from a drained start, plus a read of TOPPC inside a called subroutine |
| `seqprobe3` | 24 | 1,218 | TOPPC read 0 to 4 instructions after a push from an immediate, a register and a CALL; SSTAT sampled after every step |
| `seqprobe4` | 6 | 336 | which instruction form reads TOPPC, whether the store form pops, and how late PC_EMPTY is after a read, a POP PC and an RTS |

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

### TOPPC: the instruction decides what a read returns

The earlier probes read TOPPC as zero and the first guess was a latency, since
each of those reads was the instruction straight after the push. Two more
suites tested that directly, with gaps of 0 to 4 instructions and with the
push coming from an immediate, a register and a CALL. The gap makes no
difference at all. The *instruction* does:

| Read | Returns |
|---|---|
| `AR = TOPPCSTACK` | the top of the PC stack |
| `DM(0x3731) = TOPPCSTACK` | zero |

Both pop. With a CALL two instructions earlier the register form hands back
the exact return address; with two writes and two reads it hands back both
values, newest first. The store form writes zero however far it is from the
push, and after one write and one store-read the stack is empty, so it popped.

The store form is the one the firmware uses, hundreds of times, which makes
those sites a pop whose value is thrown away rather than a way of saving a
return address -- and the V.90 loopback agrees: it still reaches data mode on
both ends at 22.74 s and 21.12 s with 4 of 4 pings once the emulator stores
zero there. A read that does not pop at all, which is what the emulator did
for one earlier revision of this file, stops the caller at TrnProgress
`0x00c8`.

### PC_EMPTY is one instruction behind a TOPPC read, and nothing else

The bit that fitted no model turns out to be late, and late only there. After
a read of TOPPC empties the stack, the first SSTAT store still shows PC_EMPTY
clear and the next one has it set. After `POP PC`, and after an RTS, SSTAT has
it set immediately. `PUSH STS` clears STATUS_EMPTY immediately as well, and a
CNTR load that does not push -- the first one after an empty pop -- leaves
COUNT_EMPTY alone, which is the counter-valid gate confirming itself from
another direction.

That is modelled: a TOPPC read schedules the bit and the next instruction
publishes it.

One sample refuses to settle. Taken inside a subroutine, in the instruction
right after the read that empties the stack, the eight DSPs disagree with each
other: in one run five read 0x54 and three read 0x55, and after the suite's
addresses shifted the disagreement moved to a different gap. It is the only
disagreement between chips this bench has seen in sixteen runs. The suites
keep the TOPPC value from that case, which is solid, and leave the sample out.

### Results of the seventh to eighteenth runs

| Suite | Before | After |
|---|---|---|
| `loops` | 33 / 49 | 49 / 49 |
| `stacks` | hung the DSP | 94 / 94 |
| six control-flow fuzz seeds | hung the DSP | 3,191 / 3,191 |
| `seqprobe` | 256 / 296 | 37 / 37 |
| `seqprobe2` | 0 / 248 | 31 / 31 |
| `seqprobe3` | -- | 24 / 24 |
| `seqprobe4` | -- | 6 / 6 |

All sixteen suites now match the chip, the four probe suites included, and so
do the 3,191 cases of the six control-flow fuzz seeds. The V.90 loopback still
reaches data mode on both ends.


## Interrupts and the timer (23 October 2026)

Two suites went after the parts of the chip the emulator had taken from MAME
and never checked: the interrupt latches and vectors, and the timer, which
was not modelled at all. Every case forces its interrupt from software, so
nothing on the card is involved.

### The card asserts three interrupt pins, and they cannot be tested

The first run opened IMASK to `0x3ff` and the chip stopped dead, with SSTAT
showing the status stack overflowed and IMASK whittled down by nested entries.
One mask bit at a time said why: bits 0 to 6 are quiet, and bits 7, 8 and 9 --
IRQL2, IRQL1 and IRQ2, the external pins -- each take the DSP into its handler
and never let it back out. They are level-sensitive by default and something
out on the card is holding them asserted, so the handler cannot clear them.
Everything else here stays inside the seven quiet bits.

### The IFC bit layout, and the two bits the emulator did not have

Each of the eleven vectors was given a handler that logs its own number, so
forcing one IFC bit at a time says which interrupt that bit is:

| IFC bit | forces | IFC bit | clears |
|---|---|---|---|
| 8 | timer | 0 | timer |
| 9 | IRQ0 | 1 | IRQ0 |
| 10 | IRQ1 | 2 | IRQ1 |
| 11 | BDMA | 3 | BDMA |
| 12 | IRQE | 4 | IRQE |
| 13 | SPORT0 RX | 5 | SPORT0 RX |
| 14 | SPORT0 TX | 6 | SPORT0 TX |
| 15 | IRQ2 | 7 | IRQ2 |

The emulator had the middle six of each but neither the timer at bit 8 and 0
nor BDMA at 11 and 3, and it had no BDMA interrupt at all. Both are in now.

It also only looked at the *pin* for IRQ0 and IRQ1 unless ICNTL made them
edge-sensitive, so a forced latch did nothing with ICNTL at `0x10`. The card
takes a forced latch whatever ICNTL says.

### The priority order and the masking on entry are right

Forcing every bit at once and reading the order the handlers logged gives
SPORT0 TX, SPORT0 RX, IRQE, BDMA, IRQ1, IRQ0, timer -- the emulator's order,
and the same whatever ICNTL holds. The IMASK each handler ran under confirms
the rest: without ICNTL's nesting bit, entry clears IMASK outright; with it,
entry clears the bits at and below the interrupt's own priority, which for the
seven-bit mask gives `0x00`, `0x40`, `0x60`, `0x70`, `0x78` for priorities 3 to
7, exactly as modelled. ASTAT comes back as the case left it in every one of
these cases, so entry pushes the status stack and RTI pops it.

### The timer counts one step per TSCALE + 1 cycles

TSCALE, TCOUNT and TPERIOD at DM `0x3ffb`, `0x3ffc` and `0x3ffd` take a value
and read it back. With TSCALE at 0 and the timer enabled for a window of 17
instructions, TCOUNT came back 17 lower, and 49 lower for a window of 49: one
step per cycle. TSCALE 1, 3 and 7 divide that by 2, 4 and 8. The emulator had
no timer; it has one now, counting in data memory where the registers live,
and it agrees with the card at TSCALE 0 and in three of the six TSCALE cases.
The three it misses are a phase question -- whether writing TSCALE, or
enabling the timer, reloads the prescaler -- which the `irq2` probe asks and
this session did not get to run.

The chip's control registers at reset, read before this program writes one:

```
3fe0=0001 3fe1=0000 3fe2=0000 3fe3=0008 3fe4=0000 3fe5=0005 3fe6=fb00
3fe7..3ff1=0000  3ff2=8000  3ff3..3ffb=0000  3ffd=ffff 3ffe=ffff 3fff=0407
```

TCOUNT is left out of that list and out the suite: it holds whatever the chip
was last doing and differs from DSP to DSP.

### Follow-up: isolate the cases before interpreting the timer (22 October 2026)

The follow-up ran `irq` and `irq2`, then four new, isolated suites, and finally
confirmed all four together on the eight DSPs. Every card run finished and
SN 12196 was restored to its stock firmware afterwards; SN 1331 stayed active.
The earlier apparent stuck timer latch was a probe artifact, not chip behaviour.

Three bench effects had to be removed:

- The old suites left masked IFC requests pending between cases. Unmasking
  one could take an interrupt before the case explicitly forced another. The
  new cases mask interrupts, disable the timer, clear IFC's low eight bits,
  and leave settling instructions before beginning their measurement.
- The deliberately short-period timer case wrote past its log window and
  into the following case's log. The new timer suite uses separate 64-word
  windows and periods the handler can service.
- IDMA polling perturbed the running timer probe. With immediate polling,
  all four copies of `timerphase` gained one extra timer tick in the same
  case (TSCALE 1, gap 3). Adding a quiet interval after PM0 boot removed it
  without changing the DSP program. The MIPS stub now waits 200,000 delay-loop
  iterations before polling a newly booted DSP; all confirmation jobs finished
  on their first poll. This is evidence of an IDMA stall while the timer keeps
  counting, rather than a prescaler irregularity.

The reset-register sweep also showed `DM(0x3ff2)` at zero on one chip and
`0x8000` on others. Its earlier uniform value is not a portable reset assertion.
Neither old exploratory suite is promoted to the regression fixture.

### The timer timing now matches the chip

The isolated probes establish these rules:

- ENA/DIS TIMER takes effect after the instruction's timer cycle. A TCOUNT
  read immediately after ENA sees the initial count; DIS still spends the
  current cycle. The core previously used the new MSTAT value for that cycle.
- Disabling the timer freezes the prescaler, and re-enabling preserves its
  phase. Writing TSCALE reloads the prescaler, even when the written value is
  unchanged. A tick already due during the writing instruction still happens,
  then the divider reloads. These are distinguished by eight starting phases
  at TSCALE 1, 3 and 7, with eight consecutive TCOUNT reads per case.
- TCOUNT at zero reloads TPERIOD and requests an interrupt on the next timer
  step. Taking the timer interrupt clears its latch: the clean handler does
  not write IFC and logs exactly the expected number of entries.
- Interrupt entry spends one additional cycle with the timer still running.
  Without that cycle the emulator's TCOUNT was one high per logged entry;
  accounting for it makes every clean timer-interrupt result agree. This
  cycle accounting is enabled with the opt-in hardware timer model.

The BDMA addition also needed `irq_state`, as well as `irq_latch`, sized to
`ADSP2181_IRQ_COUNT`: the public `set_irq` API accepts BDMA's new index 9.

| Suite | Cases | Result |
|---|---:|---|
| `irqclean` | 112 | all match; IFC routing at four ICNTL values and four mask-to-force gaps |
| `timerphase` | 40 | all match; enable/read/disable at TSCALE 0, 1, 3 and 7 |
| `timerphase2` | 48 | all match; re-enable versus rewriting TSCALE at eight phases |
| `timerirq` | 10 | all match; initial TCOUNT 0, 1, 2, 4 and 64, periods 32 and 64 |

The final combined run gives **420/420 case comparisons**, two copies of every
suite, with identical results in each pair. Earlier isolated runs gave four
identical copies of each suite. All 210 new cases are now in
`tests/data/dsp_conformance_card12196.json`, alongside hashes of their PM words
so a changed program cannot silently reuse the old hardware reference.
The raw bundles, specs and results are under
`artifacts/dsp-conformance-20261022/irq-followup/`.

The C core tests and all twenty asserted hardware suites pass. The Python
conformance run passes with the system Python, skipping the optional Unicorn
card model because Unicorn is absent there. The sibling venv's Unicorn crashes
with SIGILL in `mem_map` before executing the MIPS stub; that path was not
validated in this follow-up. Hardware did validate the updated stub.

IDLE wakeup remains open: `irq2` demonstrates it on hardware, while this
emulator still uses IDLE to end a host run slice. The hardware timer remains
opt-in (`EICON_ADSP_TIMER=1`); its firmware-loopback integration remains a
separate issue from instruction conformance.


## Register-write visibility, flags-only ALU and IDLE (22 October 2026)

The next pass added four suites and ran each twice: first with four or eight
identical DSP copies, then all four suites together, two copies per suite.
The final run matched **3,736/3,736 case comparisons**. All eight DSP jobs
finished on their first poll; SN 12196 was restored after every run and
SN 1331 stayed active. These are 1,868 additional hardware-backed cases,
bringing the asserted suites to twenty-four.

### ASTAT and the tested MSTAT modes are visible immediately

`astat_write` tests immediate, register-MOVE and absolute-DM writes, followed
by zero, one, two or four NOPs before a consumer. Its 660 cases cover:

- direct ASTAT readback, incoming carry for X+Y+C, and AV/AC feeding NORM;
- all fourteen flag condition codes, under zero and all-set ASTAT;
- individual flag bits and all-set flags, with the previous value inverted.

`mstat_write` adds the mode-control instruction as a fourth write form. Its
160 cases switch modes in both directions and measure AR_SAT, AV_LATCH,
integer/fractional MAC placement, DAG1 bit reversal, and register-bank
selection. The two banks have distinct operands so an early/late bank switch
cannot accidentally give the same sum.

**All 820 cases matched the existing core**, including every zero-gap case.
No extra ASTAT/MSTAT write delay should be introduced for these forms and
consumers. This does not establish the timing of every control register or
special register read: the previously measured TOPPC/SSTAT exception remains.

### Flags-only ALU operations obey saturation flags too

`flags_only` covers all sixteen ALU operations at four operand pairs, four
incoming ASTAT values, and all four combinations of AR_SAT/AV_LATCH. It also
checks that neither AR nor AF changes. The old core matched 968 of 1,024 cases.
All eight DSPs agreed on the 56 mismatches.

The flags-only form reports AZ and AN for the saturated value, just like AR
and AF destinations, while discarding the result. For example:

| Operation under AR_SAT | Old ASTAT | Chip ASTAT |
|---|---:|---:|
| `0x7fff + 1` | `0x06` | `0x04` |
| `0x8000 + 0x8000` | `0x0d` | `0x0e` |
| `ABS 0x8000` | `0x16` | `0x14` |

`alu_op_none` now isolates the incoming latched AV while doing the operation,
updates AZ/AN on a new saturating overflow, then restores the incoming AV when
AV_LATCH requires it. It still writes no result. All 1,024 cases now match.
The C arithmetic regression's old flags-only exception is removed too.

### IDLE sleeps across host slices, and waking takes two extra cycles

`idle_timer` uses timer-only interrupts and isolated state/log windows. It
runs one or two consecutive IDLEs at TSCALE 0, 1, 3 and 7, starting TCOUNT at
8, 16 or 32 and reloading 128. The handler samples TCOUNT and logs its entry;
the continuation samples it again and disables the timer.

Merely stopping instruction fetches until an interrupt was insufficient: at
TSCALE 0 the emulator's handler sample was 127 while the chip's was 125.
Compared with ordinary interrupt entry, waking IDLE takes two extra cycles
with the timer running. Modelling three entry cycles for a sleeping core,
instead of one for a running core, matches all 24 cases at every scale,
including the second wait through a timer reload.

With `EICON_ADSP_TIMER=1`, IDLE now sleeps across calls to `adsp2181_run` as
well as within one call. While asleep, it executes no instructions and spends
no loop counts; its timer and interrupt-entry cycles still advance. The
existing default host run-to-IDLE contract is preserved when the timer model
is off.

`tests/test_adsp_idle.py` additionally checks sleeping with no enabled timer,
external IRQ wakeup through the public API, a masked timer remaining asleep
until unmasked, identical output across short run slices, and the legacy
contract. These host API tests supplement the hardware timer-wakeup oracle;
they are not measurements of the card's external pins.

All twenty-four asserted suites, four IDLE host-contract tests, and the C
core tests pass. The optional Unicorn card-model test remains skipped in the
system Python; the sibling venv's earlier native `mem_map` SIGILL remains
unresolved. A modem firmware loopback was not rerun in this pass. The timer
model remains opt-in; validating it with full modem firmware is still open.

Raw specs, pre-fix emulator results, bundles and chip results are in
`artifacts/dsp-conformance-20261022/register-idle/`. All four suites are in the
checked-in card fixture with PM hashes.


## SPORT internal companding (22 October 2026)

SPORT's compander can be exercised by CPU writes to RXn/TXn while the serial
ports are disabled. The local hardware reference documents this in
[Companding Internal Data, section 5-31](3110043388x_hardware/8xsports.pdf).
This made a substantial hardware comparison possible without driving serial
clocks or frame-sync signals.

### What was measured

| Suite | Cases | Coverage |
|---|---:|---|
| `sport_probe` | 512 | both ports, RX/TX, all four DTYPE values, immediate through four-NOP readback, register and DM-store read forms |
| `sport_rx` | 3,072 | every 8-bit receive code for both laws on both ports, with upper bytes 0x00, 0x7f and 0xff |
| eight `sport_tx{port}_{dtype}_{half}` suites | 65,536 | every input -8192..8191, both ports and both laws; also covers A-law clipping outside its narrower range |
| `sport_timing` | 336 | SLEN 3/8/16 bits, extreme 16-bit inputs, pending conversion versus CPU rewrite or DTYPE change |

The TX sweeps run a 41-word program per DSP: a hardware DO loop generates
8,192 consecutive inputs and writes the converted words through I0. This
covers the whole native range in one eight-DSP run instead of requiring huge
straight-line programs. The cases in the spec describe the individual loop
outputs, so ordinary `emulate` and the checked-in oracle still apply.

The first `sport_probe` run had 256 mismatches out of 512: every companding
case differed because the emulator just retained the raw RX/TX write. All
eight chips returned identical results. Both full TX sweeps were then run a
second time; every word matched the first run. The RX and timing suites were
also repeated with identical copies. The final sweeps plus final mixed suite
run give **76,960/76,960 case comparisons**.

### Hardware rules now modelled

- CPU writes to RXn expand the low eight bits; the upper byte is ignored.
  CPU writes to TXn compress the signed 16-bit value and sign-extend the
  resulting eight-bit code back into the register.
- The native linear scale is signed 14-bit for mu-law and signed 13-bit for
  A-law. For example mu-law code 0 expands to -8031, rather than the host
  PCM16 decoder's -32124. Out-of-range TX magnitudes clip.
- Compression uses the magnitude of negative inputs for both laws. The
  measured negative A-law boundaries require `abs(sample)`, not the
  `-sample-1` convention used by some host G.711 routines.
- The instruction immediately after a CPU write still reads the raw value.
  After one intervening instruction, reads return the converted value.
- A new CPU write beats a conversion completing in the same cycle. A second
  write therefore reads raw immediately and converts one instruction later.
- DTYPE is captured by the original write: changing or disabling companding
  in the intervening cycle does not change that pending conversion.
- SLEN does not truncate these internal register conversions, even at a
  three-bit serial word length. DTYPE 0/1 CPU writes retain the full raw
  register value; this does not establish receive-wire sign/zero-fill rules.

The core has a per-register conversion pipeline, clocked alongside the timer,
including host slice boundaries and idle cycles. Reset discards pending jobs.
Native internal companding is **opt-in: `EICON_ADSP_SPORT=1`**. The existing
modem frame adapters already convert some samples outside the core and have
not been qualified with the native SPORT representation. Their default
behaviour is preserved. This switch models register conversions; it does not
add serial clock generation, serial shifting, frame sync, or autobuffering.
Existing TX write callbacks remain CPU-write notifications, not timed wire
transmission events, and conversion completion does not duplicate them.

`tests/test_adsp_sport.py` checks conversion latency across one-instruction
host slices, discarding a pending job on reset, one callback per CPU write,
and the legacy unconverted-latch mode. All 35 asserted hardware suites, the C
core tests, and the four SPORT and four IDLE host tests pass. Optional Unicorn
validation has the same environment limitation described above. No full modem
loopback with native SPORT conversion was claimed or run.

### External receive/autobuffer probe: no transfer observed

Follow-up: [23 October hardware clock investigation](sport_clock_hardware_20261023.md)
resolved the SPORT1 clock prerequisite by preserving stock IPAC/FPGA setup.
Physical receive, circular autobuffering, wrap IRQs and one-cycle transfer
overhead are measured. Reset release alone was insufficient; SPORT0 and TX
remain unqualified. The results below describe the original reset-only probe.

`sport_external` is an exploratory probe, not an asserted oracle. It enables
one SPORT at a time with external SCLK and external frame-sync inputs, RX-only
autobuffering through I0/M0/L0, and a bounded 32,768-NOP wait. It tries both
required and unframed receive modes on each port. It never loads TX data and
never enables internal clock or frame generators.

On all eight chips, I0 remained 0x2800 and every word in the eight-word receive
ring remained at its 0xbeef sentinel. RX0 read 0x00ff and RX1 read 0xffff.
There was **no observed serial receive/autobuffer transfer**. This does not
prove the board lacks a clock: clock/frame generation or routing may require
further board/FPGA setup. It supplies no evidence for autobuffer timing.

The next physical SPORT work therefore needs a verified clock/frame source
on the stopped card, or a connected fixture. Then measure receive formatting,
autobuffer DAG selection/wrap interrupts and overhead, transmit start/frame
behaviour, and multichannel slot selection. Internal companding success is
not a pass for those wire-level behaviours.

SN 12196 was restored to its stock firmware after every hardware session;
SN 1331 stayed active. Raw specs, bundles and both initial/final runs are in
`artifacts/dsp-conformance-20261022/sport/`. The eleven settled suites and
program hashes are in the checked-in fixture.


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
  against it for all thirty-five asserted suites.
- `artifacts/dsp-conformance-20261022/`: the raw results from all eight
  DSPs (untracked).

## Next

- MSTAT/ASTAT write visibility and flags-only ALU saturation now have asserted
  hardware suites. SPORT internal companding now has eleven asserted suites.
  The 23 October follow-ups measured SPORT1 physical RX/TX, framing and
  autobuffering, including one-cycle RX/TX transfer overhead. The opt-in
  [external-clock emulator](sport_emulator_20261023.md) now reproduces serial
  framing, data, DAG results and per-transfer cost. Internal clock generation,
  multichannel and full firmware integration remain open. SPORT0 needs an active-line
  control: the owner confirmed the line was not up during the routing scan.
- Interrupts, timer counting and IDLE timer wakeup now have isolated, asserted
  suites. Full firmware use of the opt-in timer model remains open. Broader
  interrupt nesting across loops, calls and stack operations needs checking.
  Keep `irq` and `irq2` as exploratory probes; their shared state and log
  overflow make them unsuitable as exact regression oracles.
- One sample is a race between the chips themselves: SSTAT read inside a
  subroutine in the instruction after a TOPPC read empties the stack.  Nothing
  else in sixteen runs has differed between the eight DSPs.
- Overflowing the loop stack, and ending a loop on a branch, are things the
  chip does not come back from, so neither can be asked here.
- The fuzzer does not generate loops, calls or interrupts, so its programs
  stay straight-line. Extending it there is the obvious next widening.
- Read the DSP part number off a board. These chips run 16K-word PM programs
  and behave as ADSP-2181s here, but a 2185 or 2186 would differ elsewhere.
