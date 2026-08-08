# The data path, V.42/V.44, and how a modulation is actually selected

Sessions 151-189. V.42/V.42bis/V.44 and PPP, the loopback rig, DM(0x3FC4) as the real modulation selector, and the V.22/V.32 pages.

Part of the running log; the index is [`../eicon_adsp_firmware_analysis.md`](../eicon_adsp_firmware_analysis.md). The current picture is [`../handoff.md`](../handoff.md).

---

## Session 151: the page-8 transmit chain, mapped end to end

150 dissolved the question 149 posed. There is no caller-specific defect to
chase: in the passband the caller is 0.081 and the answerer 0.071, against
hardware's 0.818. Both ends fail the same way, so the question is the one 145
asked — what the transmitter is being fed. The chain is now mapped, from the
line back to the overlay, off the PC histogram of a paced run.

### The publisher, PM 0x1746

```text
1746: AR = DM($3761)          ; transmit credit          39,263 executions
1747: AR = AR + 0
1748: IF EQ JUMP $1750        ; no credit -> publish AR, which is 0
1749: AR = AR - $0001         ; spend one                 39,262
174a: DM($3761) = AR
174b: I0 = DM($3768)          ; read cursor
174c: L0 = $0014              ; a 20-word circular buffer
174d: AR = DM(I0,M1)          ; take one sample
174e: DM($3768) = I0
1750: I4 = $3764
1751: DM(I4,M5) = AR          ; publish to the line word
1752: RTS
```

So `DM(0x3764)` is the tail of a 20-word ring gated by a credit at
`DM(0x3761)`. The starve arm at `0x1748` published on 1 of 39,263 ticks, so the
consumer is not outrunning the producer.

### The credit is balanced, not leaking

Writers of `DM(0x3761)`, with the values:

```text
ppc=1723   668 writes (answerer)   tops the credit up by 5
ppc=174a  3331 writes              spends one per publish
```

It oscillates 0 -> 5 -> 9 -> 0 and never grows, so the ring is neither
overflowing nor being read stale. The caller is the same, 2809:562, plus a
second pair at `0x1d4a`/`0x1d23` that is the V.90A overlay's copy of the same
code.

### The producer, PM 0x1769 — an interpolating filter

```text
1769: I1 = DM($3766)   L1 = $004A     ; 74-word history
176b: I0 = DM($3765)   L0 = $0014     ; the same 20-word ring, write side
176e: MX1 = DM($375D)  MY1 = DM($3759)
1770: AX0 = DM($376E)
1771: CNTR = DM($3755)                ; 7 samples per call
1772: DO $1780 UNTIL NOT CE
1773:   I6 = DM($376F)                ; index table, reset to 0x3788 each call
1775:   AR = AX0 + AY0                ; phase accumulate
1778:   CALL $17A6                    ; the filter proper; returns MR1
1779:   DM(I0,M1) = MR1               ; one output sample into the ring
1781: DM($3765) = I0
```

Executions: the routine runs 5,609 times and its body 39,263 — exactly the
number of publishes, so **production and consumption match one for one**. A
second, structurally identical producer at `0x1787` runs its body 50,481 times
into a different ring at `DM(0x376C)`; nothing on the transmit path consumes
those, and it is most likely the receive or echo chain rather than a second
transmitter, which is stated here as unconfirmed.

### What that leaves

Every stage from the ring to the line is now accounted for and balanced. The
content enters at the 74-word history the filter reads through `DM(0x3766)`, and
that is filled by the V.34 overlay's symbol mapper. So:

- the answerer's twelve seconds of one unchanging sample (150) means the
  overlay handed the filter a constant, not that the filter stopped;
- the broadband stretches mean the overlay handed it values with no symbol
  structure.

**Next: watch `DM(0x3766)`'s buffer, not the line.** The transmit history is a
74-word window at a known pointer, so the symbols themselves are readable
directly, one hop above everything 143-150 measured, and they can be compared
against what V.34 phase 3 is supposed to carry. That is a much smaller question
than "why is the line broadband", and it is now the only one left on this path.

## Session 152: the transmit history is fed from the V.90 mapping-frame block

151 said to watch `DM(0x3766)` rather than the line. Done, and the buffer and its
filler are now both identified.

### The buffer, and its one writer

Watching the cursor gives the extent directly: `DM(0x3766)` walks
**`0x3680..0x36c9`**, 74 words, matching the `L1 = $004A` the filter sets. An
ownership survey over that range (`--assert-dm-clean 0x3680:0x36c9:400@0x0261`,
now forwarded by `eicon_loopback.py`) finds **exactly one writer on both ends**:
PM `0x1742`, 29,600 writes each. Nothing else touches the transmit history.

`0x1742` is not the mapper. It is a resident block copy:

```text
173c: CNTR = DM($3F67)          ; 3 words
173d: I0 = $3FA7                ; source: the mapping-frame block
173e: I4 = DM($3767)            ; destination cursor
173f: L4 = $004A                ; the 74-word history
1740: DO $1742 UNTIL NOT CE
1741:   AR = DM(I0,M1)
1742:   DM(I4,M5) = AR
1744: DM($3767) = I4
```

So the transmit history is fed, three words per frame, from
**`DM(0x3FA7..)` — the V.90 mapping-frame block**, the same block
`EICON_V90D_TX_BLOCK_HOLD` exists to protect on page 14. The per-frame sequence
that drives it is at PM `0x1725`: copy the receive block, `CALL (DM(0x3FB8))` —
the page's own frame routine, the vector Session 113 found `PortableBulkDelay`
overwriting — then `CALL 0x173C` to move its output into the history.

### The clear is not the cause — disproved, do not re-derive

Two writers fill `DM(0x3fa7..0x3fa9)`: PM **`0x06cd`** 3,078 times and PM
`0x374e` 922 times in the first 4,000 writes. `0x06cd` is the resident kernel's
per-frame *clear*, and the shim suppresses it **only for page 14**. Three frames
in four being zeroed under a producer that fills the fourth is an impulse train,
which is white noise, which is the symptom.

It is not the cause. `EICON_V34_TX_BLOCK_HOLD` extends the same suppression to
`0x0261`, and with it on the transmitted signal is **byte-identical**: caller
RMS 776.6 and 0.081 at 1953 Hz, answerer 0.071 at 3094 Hz, deepest `0x00b0` on
both — every figure unchanged. The copy at `0x173C` must therefore run ahead of
the clear within the frame, so the zeroes never reach the history. The flag is
**off by default** and kept only so the A/B does not have to be rebuilt.

### Where this leaves it

The path is now complete and every stage in it is accounted for:

```text
page frame routine (DM 0x3FB8) -> DM(0x3FA7..) 3 words/frame
  -> PM 0x1742 copy -> history DM(0x3680..0x36c9), 74 words
    -> PM 0x17A6 interpolating FIR, ~38 taps, coefficients in PM
      -> 20-word ring, credit DM(0x3761)
        -> PM 0x1746 publisher -> DM(0x3764) -> the line
```

Everything from the block onwards is balanced and demonstrably correct, and the
only remaining upstream is `DM(0x3FA7..)` itself and the routine at PM `0x374e`
that fills it. **That is the next probe and it is a narrow one**: log the three
words `0x374e` writes per frame and ask whether they carry V.34 symbol structure
or noise. If they carry structure, the defect is between there and the line and
this map says there is nowhere left for it to hide; if they are noise, the
question moves into the page's own frame routine at `DM(0x3FB8)` and out of the
resident kernel entirely.

## Session 153: the noise is the overlay's own output, not anything the harness does to it

152 said to log what fills `DM(0x3FA7..)` and ask whether it carries symbol
structure. Run, with one correction to method: a plain `--watch-dm-writes` limit
is spent before page 8 even loads (92,310 clears plus 27,690 fills reached the
120,000-line cap during V.8 and INFO, and the page-8 window then logged nothing
at all). The `@OVERLAY` form of `--assert-dm-clean` arms on residency and is the
right instrument here.

### The page-8 writers of the frame block

```text
answerer   24,462 x PM 2ced      8,672 x each of PM 283c/283d/283e
caller     29,655 x PM 2ced      6,956 x each of PM 283c/283d/283e
```

`0x283c..0x283e` write **nothing but zeros** — they are the overlay's own
per-frame clear of the block, `DM(I4,M5) = $0000` three times. The data comes
from `0x2ced`, and that is not a mapper either:

```text
2ce7: CNTR = $0003
2ce8: DO $2CED UNTIL NOT CE
2ce9:   AR = DM(I0,M1)          ; from DM(0x0B92..)
2cea:   MR = AR * MY0 (SU)      ; scale
2ceb:   SR = LSHIFT MR0 (LO) BY 1
2cec:   SR = ASHIFT MR1 (HI, OR) BY 1
2ced:   DM(I7,M5) = SR1         ; into DM(0x3FA7..)
```

A gain-and-shift copy. `DM(0x0B92..0x0B94)` has exactly one writer on both ends,
PM **`0x3a57`**, which is another copy — three words per frame out of a 60-word
ring at cursor `DM(0x0F67)`.

### Every hop is a copy, and the noise is present at all of them

```text
                                   n       distinct    conc    peak bin
PM 3a57 -> DM(0x0b92)           16,827       5,570     0.074      135
PM 2ced -> DM(0x3fa7)           16,826       3,376     0.074      135
              the line (152)         -           -     0.071        -
```

Same concentration, same peak bin, at every stage. Nothing between the overlay
and the line changes the signal's character, which is the strongest possible
statement that the transport this project has spent Sessions 143-152 on is
**not** where the defect is.

And the stream is noise on its own terms, without reference to any spectrum.
Autocorrelation of the `DM(0x0b92)` sequence:

```text
lags 1..16   -0.013 -0.013 +0.012 +0.037 +0.048 +0.042 +0.043 +0.018 ...
largest |r| over lags 2..400:  +0.048 at lag 5
white-noise floor at n=16,827: +/-0.008
```

A modulated carrier would put |r| near 1 at its symbol period. This peaks at six
times the noise floor over lags 4-10 — a faint lowpass colouring on what is
otherwise a white sequence. **The V.34 overlay is generating noise at its own
output.** Its amplitude is right (RMS 2,052, range +/-8,494), so the arithmetic
is running; it is running on the wrong contents.

### What this closes and what it opens

**Closed:** the transmit path. From the overlay's own store at `0x2ced` through
`DM(0x0B92)`, `DM(0x3FA7)`, the 74-word history, the interpolating FIR, the
20-word ring and the publisher, every stage is a copy or a filter, all are
balanced one-for-one, and the signal's character is unchanged end to end. Do not
look here again.

**Open, and it is a different kind of question:** why the overlay computes noise.
Two readings, not yet separated:

1. the modulator is fed unseeded or wrong state — the natural continuation of
   Sessions 138-142, which were looking at exactly this (the `DM(0x2140)` gate,
   the role word, the script tables) before the pacing defect masked everything
   and should be re-derived now that page 8 runs at the right rate;
2. or the emulator's arithmetic diverges from an ADSP-2181 somewhere the
   modulator depends on — its MAC modes, the `(SU)`/`(RND)` variants or the
   shifter, which this chain uses heavily and which nothing has ever validated
   against hardware.

(2) is testable without another call and has never been tried: the same overlay
runs offline, and its output for a fixed input is deterministic.

## Session 154: the first arithmetic oracle, and a real 218x gap that is not the bug

153 left two readings for why the overlay computes noise, the second being that
the emulator's arithmetic diverges from the part. The part is settled and has
been since Session 61 — it is an **ADSP-2185N**, instruction-compatible with the
ADSP-2181 the emulator is named and configured for
(`chip_type = CHIP_TYPE_ADSP2181`, `mstat_mask = 0x7f`). What is not settled is
whether the emulation is faithful, and nothing in the tree had ever tested it:
`adsp_opcode_audit.py` says of itself that it is coverage, not a correctness
oracle.

### The datasheet's warning, checked

`docs/ADSP-218XN_SERIES.pdf` describes the part as "ADSP-2100 family code
compatible ... **with instruction set extensions**", and its instruction-set
section as "a superset of ADSP-2100 Family assembly language". An emulator
written to the 2100 baseline can therefore decode a 218x instruction as
something else without ever saying so.

At the top level it does not: every one of the **256** top-byte opcode classes
has a case in the dispatch, so nothing is silently swallowed wholesale.

### `tools/adsp_arith_oracle.py`, and what it establishes

The card's own G.711 encoder, TIKRNL PM `0x1810`, is shipped firmware — so it is
authoritative about what the hardware does — and G.711 is an ITU specification,
so its output is externally known for every input. Sweeping all 65,536 signed
inputs through it exercises the ALU, the shifter and the sequencer.

Exact code equality is the wrong bar and reporting it alone would mislead:
encoders differ legitimately in how a 16-bit input is folded onto a 13-bit
magnitude, and only 4,456 of 65,536 codes match a straightforward ITU reference.
The convention-free test is the reconstruction error, which no correct encoder
can get wrong by more than a quantisation step:

```text
worst reconstruction error          519, at input -31737
samples off by more than two steps    0
```

Every code lands in the correct segment. **The ALU, shifter and sequencer paths
that routine uses are faithful.** It does *not* exercise the MAC modes the
page-8 transmit filter is built from — `(SU)`, `(RND)`, `saturate MR` — which
remain unvalidated and are the natural extension of this tool.

(Recorded because it cost a false start: `Card()` does not load the kernel.
Without `card.boot()` PM `0x1810` is zero and the sweep reports a 99.6% mismatch
that is entirely the harness. A near-total mismatch against shipped firmware
means the harness, not the firmware.)

### A genuine 218x gap, with its bound

`docs/3110043388x_hardware/8xcompu.pdf` §2 documents an ADSP-218x extension the
emulator does not implement at all: **`BIASRND`**, a bit in the SPORT0
autobuffer control register that switches `RND` from unbiased to biased
rounding. `mac_round_unbiased()` is called unconditionally at all nine `RND`
sites, and neither the bit nor the register that holds it appears anywhere in
the core.

**This is a real fidelity defect and it is almost certainly not the page-8 bug.**
The manual is explicit about its scope: "This mode only has an effect when the
MR0 register contains 0x8000; all other rounding operations work normally." That
is one MAC result in 65,536 differing by one LSB. It cannot turn a carrier into
white noise, and saying otherwise would be Session 149's mistake again. Worth
fixing for correctness, not worth expecting anything from.

### Where the transmit question actually stands

Unchanged by any of this, and the honest ranking is unchanged with it: the
untraced hop is the one 153 named and did not take — the 60-word ring at cursor
`DM(0x0F67)` that PM `0x3a52..0x3a57` copies from, which is the first place in
the chain whose filler has not been identified. Sessions 138-142's work on what
feeds the modulator (the `DM(0x2140)` gate, the role word, the script tables)
also needs re-deriving now that page 8 runs at one publish per sample, because
every measurement in them was taken through the pacing defect.

## Session 155: the 0x0F67 ring, and an audit of the emulator against the 218x manuals

### The ring

`DM(0x0F67)` is the cursor of a 60-word buffer at **`DM(0x09C0..0x09FB)`**
(`L7 = $003C`, base `0x09C0`, which satisfies the DAG base rule). PM
`0x3a52..0x3a58` copies three words a frame out of it, and its sole cursor
writer is `0x3a58`, 16,827 times, `dmovlay=0`.

An ownership survey over the buffer gives two writers and no others:

```text
PM 3753   26,016 writes   every one 0x0000        (the CNTR=3 clear at 0x3750)
PM 3792   24,465 writes   9,920 distinct, RMS 3,080, range [-8494, +8381]
```

So `0x3792` is the generator, and **the noise is already fully present there** —
same distinct-value spread and amplitude as everything downstream. The chain
still has not reached a stage that holds a signal.

**A caveat that has to be recorded, because it invalidates a method used above.**
Both writers run at `pmovlay=0`, i.e. out of the base program image, yet the
end-of-call PC histogram disassembles `0x3792` as `AR = AX0 + AY0`, which stores
nothing. The image at that address is therefore **not** what ran — resident PM
above `0x2000` is rewritten during the call. Disassembly taken from an
end-of-call histogram is not evidence about what executed at a given address,
and Sessions 151-153 leaned on exactly that for `0x1746`, `0x1769` and `0x2ced`.
Those three are below `0x2000` and are not affected; anything above it needs a
live PM dump at the moment of interest. That is the first thing to do next.

The watch line now carries `pmov=` alongside `ov=` so the question can be asked
at all.

### The audit: what else the emulator gets wrong

Checked against `docs/3110043388x_hardware/` and the 218xN datasheet:

| area | verdict |
|---|---|
| opcode coverage | all **256** top-byte classes have a case; nothing swallowed wholesale |
| DAG modulo addressing | **correct**. `mask_table` implements the manual's rule exactly — base = I masked to a multiple of 2^n with 2^(n-1) < L <= 2^n — and `modified_address()` is `(I + M - B) mod L + B`. Verified for the L values this chain actually uses (0x14, 0x28, 0x3C, 0x40, 0x4A) |
| MAC fractional/integer placement | **correct**; `MSTAT_INTEGER` selects the shift at every MAC site |
| MAC unbiased rounding | **correct** per 8xcompu.pdf §2 |
| PM data read | **correct**; the upper 16 bits go to the register |
| ALU / shifter / sequencer | **validated** by `adsp_arith_oracle.py`, 65,536 inputs, no code outside its segment (Session 154) |
| **PX register width** | **was wrong — fixed here** |
| **BIASRND** | **missing**; bounded to MR0 == 0x8000 (Session 154) |
| MAC `(SU)`, `(RND)`, `saturate MR` | still unvalidated; the oracle does not reach them |

### The PX defect

`8xmemory.pdf` §8: "The PX register still latches the **lower eight bits** of the
program memory word." The emulator stored the whole 24-bit word in `px` on every
PM read, and `pgm_write_dag2()` then wrote `(val << 8) | px` **unmasked**, so a
PM write following a PM read ORed the high bits of the previously read word into
the word it stored. `wr_px()` was equally unmasked.

This is in the transmit path — PM `0x3758..0x375f`, reached from `0x3744`, does
a PM read at `0x375a` and a PM write at `0x375b` in consecutive instructions.

Fixed: `px` is eight bits at the read, at the write and at `wr_px()`.

**And it changes nothing measurable.** Same run, after the fix: answerer 0.071
at 3094 Hz, caller 0.081 at 1953 Hz, both ceilings `0x00b0`, caller RMS 776.6 —
identical to before. The G.711 oracle is unchanged and 388 tests pass. It is a
correctness fix and it is not the page-8 defect. Recorded that way so the next
session does not expect anything from it.

## Session 156: the live instruction at PM 0x3792, and structured inputs to a MAC

155 said resident PM above `0x2000` is rewritten during the call and that
end-of-call disassembly is not evidence. Confirmed, and the mechanism is now
visible: `--watch-exec` already prints the live opcode in its `op=` field, so
the instruction that actually ran at a PC is obtainable without new tooling.

**PM `0x3792` holds a different instruction in each phase**, all at `pmovlay=0`:

```text
before page 8   op 12faa5   DM(I1,M1) = AR, SR = EXPADJ     48,384 executions
during page 8   op 6800c5   DM(I1,M1) = MR1, NOP (MAC)      24,465 executions
```

24,465 is exactly the ring-write count 155 attributed to that PC, so the page-8
instruction is the one that fills the ring, and **it stores `MR1` — a MAC
result**. The `AR`-differencing loop 155 was reading is the *other* phase's code.
(The end-of-call histogram showed a third thing again, `AR = AX0 + AY0`. Three
different instructions at one address is the clearest possible statement that
static disassembly of this region is worthless.)

The same watch-limit trap caught this twice and is worth stating as a rule: a
limit of 300 was consumed entirely before page 8 loaded, and reported every
operand as zero. **Any `--watch-exec` or `--watch-dm-writes` limit on this rig
must be large enough to survive V.8 and INFO, or segmented by `TrnProgress`
after the fact.** Both of Sessions 153 and 156 lost a run to it.

### The inputs are structured; the output is not

Operands over the 24,465 page-8 executions, reached from PM `0x3791`:

```text
ax0   1 distinct value      constant 1698
ax1   4 distinct values     0 .. 8192
ay0   9 distinct values     -6476 .. +6476
--
mr0   4,071 distinct        RMS 18,567
mr1   9,920 distinct        RMS  3,080, range [-8494, +8381]   <- stored
```

A constant, a four-point set and a nine-point set are **symbol-like**: that is
what the input side of a modulator should look like. The MAC turns them into
9,920 distinct values.

That is not automatically wrong — modulating a small constellation onto a
carrier legitimately produces many values — but it locates the transformation
precisely, and it is the first stage in this whole chain whose input holds
structure.

### A correction to Session 153

153 concluded "the overlay is generating noise at its own output" and that the
transport was closed. That needs qualifying in both halves.

The generator's output is **not white**. Autocorrelation of the stored `MR1`
sequence over page 8:

```text
lags 1-10:  +0.22  -0.37  +0.06  +0.00  -0.17  +0.03  -0.05  -0.11  +0.02  -0.03
```

against the ±0.008 white-noise floor, and against the `DM(0x0b92)` stream
downstream which peaked at +0.048. So there is real short-range structure at the
generator that is **not present two hops later**. 153 measured the ring-copied
streams and read their whiteness back onto the generator; the generator is
broadband by the concentration metric (0.101, against 0.074 downstream and 0.071
at the line) but it is measurably not the same signal.

Something between the generator and `DM(0x0b92)` is destroying what structure
there is. The ring receives **26,016 zeros** from PM `0x3753` and **24,465 data
words** from `0x3792` — very nearly one zero per sample — and the reader takes
exactly 50,481, the sum. Zero-stuffing ahead of an interpolating filter is how
interpolation is supposed to work, so this may be correct; but whether the zeros
and the data land in the slots they are meant to, in the order they are meant
to, has not been checked, and a cursor that is not reset between the clear and
the fill would interleave them instead of overwriting.

**Next, and now well-posed:**

1. dump the ring's 60 words in slot order across a few frames and see whether
   data and zeros land where an interpolator would want them — this is the
   direct test of the paragraph above, and it is a `--watch-dm` on
   `0x09C0..0x09FB` with the cursor logged alongside;
2. extend `adsp_arith_oracle.py` to the MAC modes. This was ranked third before
   and moves to second: the stage that turns structure into broadband is a MAC,
   `MR1` is the high word of its result, and `(SU)`, `(RND)`, `saturate MR` and
   the fractional shift that decides which 16 bits `MR1` holds are precisely
   what has never been validated.

## Session 157: the ring is not interleaved — the generator stops halfway through page 8

156 proposed that the ring's zeros and data might be interleaved by a cursor
that is not reset between the clear and the fill. **Disproved.** Dumping every
write to `DM(0x09C0..0x09FB)` in log order shows both writers walking all 60
slots contiguously and ascending, and the two never alternate. They run in three
long phases, timed on the `cyc=` counter:

```text
PM 3792 (data)    24,246 writes    0.00 .. 44.74 Mcyc
PM 3753 (zeros)   26,016 writes   44.75 .. 95.51 Mcyc
PM 3792 (data)       219 writes   95.52 .. 95.81 Mcyc
```

**For the whole second half of page-8 residency the ring receives nothing but
zeros**, at 512 writes/Mcyc against the data phase's 542 — the same loop running
at the same rate down a different branch. This is what Session 150 was looking
at from the other end: the answerer goes silent and then sits on a constant,
which is the copy chain draining a ring that has stopped being refreshed.

So the transmitter does not emit noise for the whole page. It emits something
for half of it and then deliberately emits zeros.

### The page-8 code, live

`0x3790` swaps per phase exactly as `0x3792` does, all at `pmovlay=0`:

```text
op 20410f  x48,384   MR = MR + MX1 * MY0 (RND)     before page 8
op 20a40f  x24,594   MR = MR1 * MY0 (SU)           page 8, data phase
op 1b7a40  x 7,261   IF EQ JUMP $37A4
```

So the page-8 transmit sample is `MR1` of **`MR = MR1 * MY0 (SU)`**, stored by
`DM(I1,M1) = MR1` at `0x3792` — a self-multiplying accumulation with an unsigned
Y operand, and one of the three MAC modes Session 154 flagged as never
validated.

### `(SU)` is correct, checked against the manual

That looked like the answer and it is not. `8xcompu.pdf` Table 2-8 gives the
convention explicitly:

```text
X Input    Y Input    Code Example
Signed  x  Signed     MR=MX0*MY0(SS)
Unsigned x Signed     MR=MX0*MY0(US)
Signed  x  Unsigned   MR=MX0*MY0(SU)
Unsigned x Unsigned   MR=MX0*MY0(UU)
```

The emulator's `case 0x05<<13` takes X signed and Y unsigned, and `0x06<<13`
takes X unsigned and Y signed. Both match, and opcode `0x20a40f` does decode to
case 5. `(SU)` is faithful. It joins the DAG, the fractional placement, unbiased
rounding and the PM read in the "checked and correct" column.

### A method correction

Session 156's timings, and the first version of this session's, were wrong:
they stamped events with the last `TrnProgress` marker, which stops updating
once the state machine parks at its ceiling, so everything afterwards collapses
onto one timestamp and the generator looked like it stopped at 10.1 s. **Use
`cyc=` for any timing on this rig.** It is monotonic and independent of the
state machine. The table above is on the cycle clock; the earlier sample-based
version of it was an artifact.

### What is now the question

Not "why does the transmitter emit noise" — it emits zeros for half the window,
and something with structured MAC inputs for the other half. The question is
**what branch takes the loop into the zero path around 44.7 Mcyc into page 8 and
keeps it there.** `IF EQ JUMP $37A4` at `0x3790` is a candidate for that branch,
and the loop head at `0x378e..0x378f` reads `DM(0x076F)` in the pre-page-8
variant of this code, so the live page-8 form of `0x378c..0x3791` and whatever
DM word it tests is the next thing to read.

The MAC-mode oracle stays on the list but drops back: the one mode this stage
actually uses has now been checked by hand and is right.

## Session 158: the gate is a vector word, `DM(0x0B72)`

157 asked what branch takes the transmit loop into the zero path halfway through
page 8. It is not a branch, and 157's "the same loop taking a different branch"
— inferred from the two writers having nearly equal write rates — is withdrawn.

Climbing the call chain with `--watch-exec`, live ops only, each stage segmented
by the ring's writer phases on the `cyc=` clock:

```text
3739: I4 = DM($0B72)          ; a vector word
373a: JUMP (I4)               ; indirect
```

`0x373a` is reached from `0x3739` in both phases and jumps to **`0x373b`** (the
modulator) or **`0x3746`** (the zero path) according to what `DM(0x0B72)` holds.
`0x3746` is entered from `0x373a` 8,672 times, which is the zero phase's whole
count. The two paths are separate routines, not two arms of one loop: the data
path is `0x373b..0x3745` ending in `RTS`, and the zero path is
`0x3746..0x3750`, pointer bookkeeping on `DM(0x0EF1)`/`DM(0x0EF2)` with
`M7 = +3/-3` followed by `CNTR = $0003` and three zero stores. Both are called
with `ret=3624`.

### The whole gate is six writes

`DM(0x0B72)` has exactly one writer during page 8, PM **`0x36b0`**, and it runs
six times in the entire residency:

```text
value    Mcyc into page 8
0x373b     0.00      modulate
0x373b     1.04
0x373b    40.52
0x373b    42.95
0x3746    44.37      <- silence
0x373b    95.14      <- modulate again
```

Those match the ring's writer phases (44.75 and 95.52 Mcyc) to within the
sampling. **One word, written six times, decides whether the card transmits.**

### The modulator, and what it is fed

The data path is a double-precision polyphase FIR, `CNTR = $0003` outputs per
call, coefficients from program memory:

```text
3760..3767  prologue: cursors from DM(0x0EF1)/DM(0x0EF2), lengths from DM(0x2136)
3768        DO $3792 UNTIL NOT CE
378c          MR = MR + MX0 * MY0 (SS), MX0 = DM(I0,M1), MY0 = PM(I7,M6)
378d          MR = MR + MX0 * MY0 (SU), MX0 = DM(I0,M1), MY0 = PM(I7,M6)
378e          MR = MR + MX0 * MY0 (SS), MX0 = DM(I0,M0), MY0 = PM(I7,M6)
378f          MR = MR + MX0 * MY0 (RND), MY0 = PM(I7,M4)
3790          MR = MR1 * MY0 (SU)
3792          DM(I1,M1) = MR1
```

The `(SS)`/`(SU)` pairing on consecutive taps is the textbook 32x16
multiprecision product from 8xcompu.pdf's own description of those modes, so the
mode selection reads as correct firmware rather than anything anomalous.

And the symbol source is clean. At `0x373c` the operands are **two-valued**:
`ax0` is `0x11e4` (+4580) 3,768 times and `0xee1c` (-4580) 3,706 times — an
antipodal pair, near enough evenly split. That is what a V.34 training sequence
looks like at the input to a shaping filter, and it is the strongest evidence yet
that the *modulator's* input is not the problem.

### What this does and does not establish

It establishes the mechanism completely: the transmitter goes quiet because a
vector word is repointed, by one routine, at one moment, and the copy chain then
drains a ring nobody is refreshing — which is Session 150's constant DC tail.

It does **not** establish that this is a defect. V.34 has defined quiet periods,
and an answerer that stops transmitting partway through a phase may be doing
exactly what the recommendation says. Nothing here should be read as "the
firmware wrongly silences the transmitter" until what PM `0x36b0` is responding
to has been read.

**Next:** `--watch-exec` on PM `0x36b0` and its caller, segmented the same way,
to see what it tests before writing `0x3746`. That is one hop, and it is the
first hop in this whole chain where the answer could legitimately be "the
firmware is right and the peer never gave it what it was waiting for".

## Session 159: PM 0x36b0 tests nothing — it is a table-driven vector load

158 ended expecting `0x36b0` to test something before silencing the
transmitter, and flagged that the answer might be "the firmware is right". It is
neither. `0x36b0` is the last store of a **vector-table loader**, and there is no
test anywhere in it. Live ops, called from `0x367c` with `ret=367d`:

```text
36a6: DM($0A42) = SR0                          ; save the selector
36a7: I4 = $20D3                               ; a PM vector table
36a8: I0 = $0B70                               ; destination DM(0x0B70..0x0B72)
36a9: SE = $0002
36aa: CNTR = $0002
36ab: DO $36AF UNTIL NOT CE
36ac:   SR = LSHIFT SR0 (LO), AY1 = PM(I4,M5)  ; next field of SR0, next table entry
36ad:   DM(I0,M1) = AR, AR = SR1 + AY1         ; store previous vector, index the table
36ae:   I5 = AR
36af:   NOP (MAC), AR = PM(I5,M4)              ; dereference to a routine address
36b0: DM(I0,M1) = AR                           ; the third vector -> DM(0x0B72)
```

It walks successive fields of `SR0` (shifted by `SE = 2` each pass), uses each to
index the PM table at `0x20D3`, dereferences, and writes **three** consecutive
vectors into `DM(0x0B70..0x0B72)`. `DM(0x0B72)` — 158's gate — is simply the
third of them. The routine runs six times in page-8 residency, which is exactly
the six writes 158 counted.

So the transmitter is not being silenced by a decision taken here. It is being
reconfigured: the firmware loads a different set of three vectors, and one of
them happens to be a routine that emits zeros.

### The selector, and the moment it changes

`SR0` on entry, against the ring's writer phases on the same clock:

```text
  0.38 Mcyc   sr0=9601    modulate
  1.42 Mcyc   sr0=b700    modulate
 40.90 Mcyc   sr0=9b00    modulate
 43.33 Mcyc   sr0=9400    modulate
 44.75 Mcyc   sr0=a700    <- silence, and the ring's zero phase starts at 44.75
 95.52 Mcyc   sr0=9600    modulate, and the ring's data phase resumes at 95.52
```

The phase boundaries coincide with the loader's calls to the sample. **`SR0 =
0xa700` is the mode that silences the transmitter**, and the five other values
all select it.

`0x36a6` stores that selector to **`DM(0x0A42)`**, so the mode word is named,
addressable and watchable — which is what makes the next hop cheap.

(The exact field extraction — which bits of `SR0` index which of the three
vectors — is not pinned down here. The shift-and-dereference structure is
measured; the bit positions are arithmetic that has not been checked against a
second run, and nothing below depends on them.)

### What this does and does not settle

It settles 158's open question in the narrow sense: nothing at `0x36b0` waits on
the peer, so the silence is not this routine deciding the far end failed to
respond. The decision is upstream, in whatever computes `SR0`.

It does **not** yet say the behaviour is wrong. A modem that reconfigures its
transmit vectors partway through a training phase and goes quiet is doing
something V.34 explicitly provides for, and the run resumes modulating at 95.52
Mcyc, which is what a timed quiet period would look like. The question is
whether 50 Mcyc of silence is the intended length and whether `0xa700` is the
mode the state machine should be in at that point.

**Next:** `--watch-dm-writes 0x0a42` armed on `0x0261`, plus the caller at
`0x367c`, to find what computes the mode. `DM(0x0A42)` is one word with six
writes a call, so this is a small trace, and it lands directly in the state
machine Sessions 138-147 were working in — which now needs re-deriving anyway,
since every measurement in those was taken through the pacing defect.

## Session 160: the mode word is `DM(0x0F59)`, written by PM `0x3669`

159 named `DM(0x0A42)` as the selector the vector loader saves. It is not the
source — it is the **cached copy**. The code above the loader is a change
detector:

```text
3675: AY1 = DM($0F5B)
3676: AX1 = DM($0A43)
3677: AR = AX1 XOR AY1
3678: IF NE CALL $3692        ; reload the other vector set only if it changed
3679: SR0 = DM($0F59)         ; the requested mode
367a: AY1 = DM($0A42)         ; the mode currently loaded
367b: AR = SR0 XOR AY1
367c: IF NE CALL $36A6        ; reload the transmit vectors only if it changed
```

It runs 362 times in page-8 residency and calls the loader six times, which is
why 158 saw six writes to `DM(0x0B72)`: the vectors are reloaded on change, not
per frame. **`DM(0x0F59)` is the mode.**

`DM(0x0F59)` has exactly one writer, PM **`0x3669`**, 362 writes:

```text
0x9b00  x343      0xa700  x7      0x9400  x5
0xb700  x4        0x9600  x2      0x9601  x1
```

and in order against the ring's phases:

```text
 0.00 Mcyc  9600 / 9601      modulate
 1.10 .. 37.61  b700         modulate
40.58 .. 42.70  9b00         modulate  (343 writes -- a busy poll of the same value)
43.xx           9400         modulate
44.75           a700         SILENCE, held to ~85 Mcyc
95.52           9600         modulate again
```

### The complete chain, mode word to line

```text
PM 0x3669  ->  DM(0x0F59)                    the transmit mode
  PM 0x3675..0x367c   XOR against DM(0x0A42), reload on change
    PM 0x36a6..0x36b0 vector loader, PM table 0x20D3 -> DM(0x0B70..0x0B72)
      PM 0x373a       JUMP (DM(0x0B72))
        0x373b   modulator: double-precision polyphase FIR, 3 outputs/call
        0x3746   zero writer: 3 zeros/call
          -> ring DM(0x09C0..0x09FB), 60 words, cursor DM(0x0F67)
            -> PM 0x3a52 copy, 3 words/frame -> DM(0x0B92..0x0B94)
              -> PM 0x2ced gain/shift -> DM(0x3FA7..) mapping-frame block
                -> PM 0x1742 copy -> history DM(0x3680..0x36C9), 74 words
                  -> PM 0x17A6 interpolating FIR
                    -> 20-word ring, credit DM(0x3761)
                      -> PM 0x1746 publisher -> DM(0x3764) -> the line
```

Every stage is now identified, and each one is a copy, a filter or a table
lookup. Nothing in it decides anything except `0x3669`.

### Still not established: whether this is wrong

Repeating 158's and 159's caution because it still holds and it is the thing most
likely to be forgotten. `0xa700` silencing the transmitter for ~40 Mcyc mid-
training is exactly what a V.34 quiet period looks like from the inside, and the
run resumes afterwards. **Nothing measured so far shows the firmware
misbehaving.** What is shown is where the decision lives.

**Next:** the live op at PM `0x3669` and what it reads. That is one
`--watch-exec` and it is the last hop before this lands in the V.34 state
machine proper. Two things to carry into it:

- the mode is written 343 times with the same value in a 2 Mcyc window at 40.58
  Mcyc, so `0x3669` is inside a polled loop rather than a state transition, and
  the interesting writes are the six that *change* the value;
- Sessions 138-147 mapped that state machine through the pacing defect and need
  re-deriving before any of their block and script findings are relied on.

## Session 161: PM 0x3669 is a 13-word block copy, and the mode's real source is `DM(0x0B59)`

`0x3669` computes nothing. It is the store half of a guarded block copy that runs
once per frame:

```text
3661: I4 = $0B52                ; source block
3662: NOP (MAC), AR = DM(I4,M4) ; read the first word without advancing
3663: AR = AR + 0
3664: IF EQ RTS                 ; nothing pending -- 16,826 executions, most return here
3665: I7 = $0F52                ; destination block
3666: CNTR = $000D              ; 13 words
3667: DO $3669 UNTIL NOT CE
3668:   NOP (MAC), AR = DM(I4,M5)
3669:   DM(I7,M5) = AR
```

The guard at `0x3662..0x3664` runs **16,826** times in page-8 residency — once
per frame — and returns immediately unless `DM(0x0B52)` is non-zero. When it is,
thirteen words are copied from `DM(0x0B52..0x0B5E)` to `DM(0x0F52..0x0F5E)`. The
body executed **4,706** times, which is exactly 362 x 13, and 362 is the number
of `DM(0x0F59)` writes Session 160 counted. The arithmetic closes.

So `DM(0x0F59)` is the **eighth word of a copied parameter block**, and the mode
the transmitter obeys originates at **`DM(0x0B59)`**, latched by a flag in
`DM(0x0B52)`. This is a command-block handoff: something fills a staging block
and raises a flag, and the per-frame service copies it into the live parameter
area.

### The chain, updated

```text
??? -> DM(0x0B52..0x0B5E)                    a 13-word staging block + flag
  PM 0x3661..0x3669  per-frame copy          -> DM(0x0F52..0x0F5E)
    DM(0x0F59) is the transmit mode
      PM 0x3675..0x367c  XOR against DM(0x0A42), reload on change
        PM 0x36a6..0x36b0  vector loader     -> DM(0x0B70..0x0B72)
          PM 0x373a  JUMP (DM(0x0B72))       -> modulator 0x373b | zeros 0x3746
            ... ring, copies, interpolating FIR, publisher, line (Session 160)
```

Every stage from the staging block to the line is now identified and is a copy,
a filter, a table lookup or a flag test. The first thing in the chain that makes
a decision is whatever writes `DM(0x0B52..0x0B5E)`, and that has not been read.

### Note on the polling counts

Session 160 read 343 writes of `0x9b00` in a 2 Mcyc window as "a busy poll".
That is now explained precisely and was not quite the right description: the
staging flag stays raised across many frames, so the same block is re-copied
every frame, and `DM(0x0F59)` is rewritten with the value it already holds. The
change detector at `0x367c` is what stops that from reloading vectors 362 times.
The interesting events remain the six value changes.

**Next, and it is the last unknown in this chain:** `--watch-dm-writes` on
`0x0b52` and `0x0b59`, armed on `0x0261`. Those two words are where the V.34
state machine reaches the transmitter, so their writer is the state machine
itself — the code Sessions 138-147 were mapping, and which needs re-deriving
now that page 8 runs at one publish per sample.

## Session 162: the staging block is published by PM 0x2a75/0x2a7a

The last unknown in the transmit chain is read. `DM(0x0B52..0x0B5E)` is written
entirely from one place, the V.34 page's own code (`pmovlay=0`, PM above
`0x2000`):

```text
DM(0b52)  PM 2a75   16,826 writes   the request flag
DM(0b59)  PM 2a7a      362 writes   the transmit mode
DM(0b5a)  PM 2a7c      362
DM(0b5b)  PM 2a7e      362
DM(0b5e)  PM 2a80      362
DM(0b53..0b57)  PM 2a86  362 each
```

`DM(0x0B52)` is a **request flag**, not data: `0x0001` on 362 frames and
`0x0000` on the other 16,464. The 362 raises match the 362 block copies at PM
`0x3661` (Session 161) and the 362 writes to `DM(0x0F59)` (Session 160) exactly.
It is a one-shot handshake — the page publishes a transmit configuration, raises
the flag, and the per-frame service latches it.

`DM(0x0B59)` takes seven values in the whole of page-8 residency:

```text
 0.32 Mcyc  0x9600
 0.38 Mcyc  0x9601
 1.42 Mcyc  0xb700
40.90 Mcyc  0x9b00
43.33 Mcyc  0x9400
44.74 Mcyc  0xa700   <- the transmitter goes quiet
95.52 Mcyc  0x9600   <- and resumes
```

**PM `0x2a7a` writing `0xa700` at 44.74 Mcyc is the origin of the silence**, and
44.74 is the same cycle at which the ring's zero phase begins (Session 157).
The whole chain is now closed, from that store to the line.

### The complete transmit chain

```text
PM 0x2a75/0x2a7a  publish  -> DM(0x0B52) flag + DM(0x0B52..0x0B5E) block
  PM 0x3661..0x3669  per-frame latch     -> DM(0x0F52..0x0F5E)
    DM(0x0F59) = the transmit mode
      PM 0x3675..0x367c  XOR vs DM(0x0A42), reload on change
        PM 0x36a6..0x36b0  vector loader, PM table 0x20D3 -> DM(0x0B70..0x0B72)
          PM 0x373a  JUMP (DM(0x0B72))
            0x373b  modulator (double-precision polyphase FIR, 3 out/call)
            0x3746  zero writer (3 zeros/call)
              -> ring DM(0x09C0..0x09FB), cursor DM(0x0F67)
                -> PM 0x3a52 copy -> DM(0x0B92..0x0B94)
                  -> PM 0x2ced gain/shift -> DM(0x3FA7..)
                    -> PM 0x1742 copy -> history DM(0x3680..0x36C9)
                      -> PM 0x17A6 interpolating FIR
                        -> 20-word ring, credit DM(0x3761)
                          -> PM 0x1746 publisher -> DM(0x3764) -> the line
```

Ten stages, every one identified, and exactly one of them decides anything.

### What is established, and what is still not

**Established:** the mechanism, completely. The card stops transmitting because
its own V.34 page asks it to, through a documented-looking parameter handshake,
and everything downstream faithfully carries out that request.

**Not established, and this is now the whole question:** whether `0xa700` at
44.74 Mcyc is correct. Sessions 158-161 each ended with this caveat and it has
survived every hop. A V.34 answerer has defined quiet periods, the run resumes
modulating at 95.52 Mcyc, and nothing measured anywhere in this chain shows the
firmware doing something it was not asked to do.

**Next:** PM `0x2a70..0x2a7a` — what computes the value `0x2a7a` stores. That is
inside the V.34 state machine, so it is also the point where this work rejoins
Sessions 138-147, and their block and script findings need re-deriving first:
every one of them was measured while page 8 ran at 9-12 publishes per sample.

## Session 163: the transmit mode is script block field 0x00

The publisher reads, in full:

```text
2a74: AR = DM($224C)          ; a pending-request word
2a75: DM($0B52) = AR          ; the flag handed to the per-frame latch
2a76: AR = AR + 0
2a77: IF EQ JUMP $2A88        ; nothing pending, skip the block
2a78: DM($224C) = M0          ; consume the request
2a79: AR = DM($2137)          ; the mode
2a7a: DM($0B59) = AR          ; publish it
```

Two words, and both are already named in this log.

`DM(0x224C)` is a request flag internal to the page: `0x2a75` copies it straight
into `DM(0x0B52)` and `0x2a78` clears it, which is why the staging flag reads
`0x0001` on exactly 362 frames (Session 162).

**`DM(0x2137)` is field `0x00` of the current V.34 script block.** The
field-to-DM rule established in Sessions 114g-147 is `DM(0x2137 + field)`, so
`0x2137` is field zero, and the decoded blocks in this log carry it:

```text
block 0x1afa  state 0x0064    field 0x00 = 0x9601      (Session 114l)
block 0x1b36  state 0x0070    field 0x00 = 0x2700      (Session 114j)
```

`0x9601` is one of the seven mode values measured at `DM(0x0B59)` — the second
one, at 0.38 Mcyc. **Session 114l's unidentified "field 0x00" is the transmit
mode**, and that has been an open annotation in this log since 114j.

### What this means

The chain does not end in a computation. It ends in **firmware data**: the
transmit mode is a constant in the script block the sequencer is currently
executing, published to the transmit chain when the block arms itself.

So `0xa700` at 44.74 Mcyc is not a decision the page made about the peer. It is
what some block's field `0x00` says, and the card is doing what its script tells
it. The silence is *designed* — for whichever block that is.

That answers the question five sessions have been carrying, and it does so in
the direction the caveat kept allowing for: **nothing in the transmit path is
misbehaving.** The transmitter emits zeros because the script block it is in
says to emit zeros.

### Where the question goes

Straight back to Sessions 143-147, and to their exact subject: *which block the
sequencer is in, and why it does not advance.* The two are now connected — a
wait block that never exits is also a transmit mode that never changes, and
143's `0x1ae5`/`0x1ba5` self-branching wait blocks are the mechanism for both.

**Before any of that is used, it has to be re-derived.** Every block, script and
gate finding in 138-147 was measured while page 8 ran at 9-12 publishes per
sample (Session 149), and 147's own conclusion — that the wait block's test
passes because the correlator latches on broadband noise — was reasoning about a
signal the harness was mangling. The pacing fix changes the input to every one of
those measurements.

The concrete first step is small: identify which script block is current at 44.74
Mcyc, by reading the state field `DM(0x2147)` and the block cursor at the moment
`0x2a7a` publishes `0xa700`. That names the block whose field `0x00` is `0xa700`,
and from there the existing script decoders apply directly.

## Session 164: the ceilings are gone — both ends reach 0x00b0, and the blocker moves there

Chasing the mode word through instead of one hop at a time. The result changes
the status of the whole V.34 blocker.

### The answerer's full state/block trail (post-pacing-fix)

`DM(0x2147)` state, `DM(0x14A5)` block cursor, `DM(0x2137)` field 0 = transmit
mode, on the cycle clock from page-8 entry:

```text
 0.32  0x0062  1afa            0.37  0x0064  1b0f  mode 9601
 1.41  0x0070  1b24  mode b700 2.12  0x0071  1b30
 3.49  0x0072  1b39           37.93  0x0074  1b42
40.89  0x0076  1b6c -> 1ba5 -> 0x0090 1bb7  mode 9b00
       [40.89-42.69  1ba5 <-> 1bb7, the wait block of Session 143]
42.95  0x0092  1bc6           43.33  0x0094  1be4  mode 9400
43.40  0x0096  1bf3           43.46  0x0097  1bfc
43.57  0x0098  1c08           44.65  0x009a  1c14
44.74  0x00a0  1c32  mode a700  <- the quiet sequence begins
44.80  0x00a2  1c44           45.10  0x00a4  1c50
45.97  0x00a6  1c5c           47.40  0x00a8  1c74
47.59  0x00aa  1c80           85.47  0x00ac  1c95
95.51  0x00b0  1cb0  mode 9600  <- transmit resumes
```

**Twenty states.** `0x0090` is passed through in 2 Mcyc. The `0x0060`/`0x0090`
ceilings that Sessions 137-148 were built on **no longer exist** — they were an
artefact of the transmitter being decimated by ten (Session 149).

The silence is settled with it: `0xa700` is state `0x00a0`, and `0x00a0..0x00ac`
is a **designed quiet sequence** of six states which the script exits at `0x00b0`
by restoring mode `0x9600`. Nothing there is a fault, exactly as Sessions 158-163
kept allowing for.

### The new blocker: the page stops servicing the transmitter at 0x00b0

At 90 seconds both ends reach `0x00b0` — the answerer at 10.10 s, the caller at
9.54 s. Then:

- the **caller** waits 0.76 s, falls back to `0x0024` -> `0x002c` and restarts
  V.8/INFO, where it stays for the remaining 80 s;
- the **answerer** parks at `0x00b0` and its transmit chain **halts entirely**:
  last ring write at 95.81 Mcyc of a 60 s run, no further `DM(0x224C)` requests,
  and the line freezes on one sample value (RMS 1052, 100% below 300 Hz) for
  36 s.

So `0x00b0` sets mode `0x9600` (modulate), the modulator runs for 0.3 Mcyc, and
then the page stops publishing at all. That is the whole remaining gap on this
path, and it is a state the project has never previously reached in loopback.

### The 5.8 G instructions at 0x00b0 are ours, and the ceiling must stay at 20000

`--pc-histogram-state 0x00b0` reports 5,814,128,838 instructions over 290,400
samples, spinning in the kernel foreground at PM `0x051b..0x0520` (134 M) and
`0x00ff..0x0109` (83 M). That is not a firmware runaway: 290,400 x 20,000 is
5,814,128,838 exactly. It is `EICON_V34_PUBLISH_MAX_CYCLES` being spent in full
on every tick where the page publishes nothing — the fallback arm of the Session
149 pacing fix.

Lowering it is not the answer. At `EICON_V34_PUBLISH_MAX_CYCLES=4125` both ends
regress hard, caller to `0x0060` and answerer to `0x0071`, so the headroom is
load-bearing during the phases that do publish. Default stays 20000. What it does
cost is wall time whenever the page is quiet, which is worth knowing when a run
seems slow.

### Where this leaves the queue

The V.34 blocker is not "phase 2 never completes" any more. It is: **the
answering page stops publishing transmit data on entry to `0x00b0`, and the
calling end times out 0.76 s later and restarts.** Everything in Sessions 137-148
about ceilings, wait blocks, correlator thresholds and role words describes a
regime that no longer exists and should not be carried forward.

## Session 165: why the page stops publishing at 0x00b0 — it is the pacing fix starving the foreground

The answer is ours, not the firmware's.

`--pc-histogram-state 0x00b0` against a whole-residency histogram, and against
an `EICON_V34_PUBLISH_PACED=0` control:

```text
                        unpaced      paced (whole)   paced, at 0x00b0 only
PM 02a9  kernel fg      344,933          39,910               56
PM 02b7  selected fg    243,576          39,260                -
PM 1746  publisher      243,232          39,263               60
PM 051b  spin loop            0      45,024,810      134,022,497
PM 03dc  wait task            0      27,872,502       82,966,308
```

The V.34 page's own per-frame code runs **25-27 times in 290,400 samples** at
`0x00b0`, and PM `0x2e2d`/`0x2ddb` (state and block cursor) not at all. The page
is not stalling on anything — **it is not being dispatched.**

The cause is the Session 149 mechanism. `adsp2181_modem_sample()` runs its
continuation only `if (a->idle)`. Stopping the core at the transmit publish
leaves it mid-frame, never idle, so the continuation is skipped on every paced
tick, and the leftover budget goes into a background wait task (PM
`0x03dc`/`0x03e9` polling `CALL $01B2`) that never runs at all unpaced. The
kernel foreground is starved 8.6x across the call and effectively to zero at
`0x00b0`.

### Two fixes tried, both worse — recorded so they are not retried

**Latch instead of stop.** `adsp2181_latch_dm_write()` (new, in the core) takes
the *first* value a frame writes to the transmit word and lets the frame run to
completion, which should give one sample per tick without touching execution
flow. It restores the foreground exactly as predicted — PM `0x02a9` 922,329, PM
`0x051b` **0** — and the state machine **regresses to `0x0060`/`0x0072`**, back to
the pre-149 ceilings, with concentration back at 0.094.

That is the important negative: **Session 149's gain was not sample selection, it
was bounding the page to one pass per tick.** Latching keeps ten passes and picks
the first sample; the page still runs ten times too fast and fails exactly as it
did before. The mechanism is a clock, not a multiplexer.

**Stop, then drive the skipped continuation.** Calling the continuation
explicitly after the stop fires is worse still: both ends stop at `0x0052` and the
V.34 overlay never becomes resident at all.

Reverted; `EICON_V34_PUBLISH_PACED=1` remains the default and both ends reach
`0x00b0` again on re-verification. `EICON_V34_PUBLISH_LATCH` is kept, defaulting
**off**, because the A/B above is worth being able to reproduce cheaply.

### What this leaves

The blocker is now precisely stated and it is a harness problem with two
requirements that the current mechanism cannot satisfy at once:

1. the page must execute about **one pass per 8 kHz sample** — stopping at the
   publish achieves this and nothing else tried does;
2. the kernel foreground continuation must still run every sample — the stop
   prevents this, and neither driving it manually nor letting the frame run to
   completion works.

The right shape is probably to make the *core* honour both: let
`adsp2181_modem_sample()` treat a stop-on-publish as a yield rather than as a
mid-frame halt, resuming the frame after the continuation instead of restarting
it. That is a change to the C entry point rather than to the shim, and it is the
one thing on this path that has not been tried.

## Session 166: the yield works mechanically and still loses — and that casts doubt on 0x00b0

`adsp2181_yield_on_stop()` makes `adsp2181_modem_sample()` treat a
stop-on-publish as a yield: run the continuation, then put the core back where
the frame stopped so the next sample's SPORT interrupt lands on top of the
page's own foreground, as hardware does.

**First attempt failed for a reason worth keeping.** A plain
"push return_pc, jump to the continuation, restore PC" leaves both ends at
`0x0052` with the V.34 overlay never resident. The continuation is an ordinary
call, not an interrupt: it runs with the page's registers live and destroys the
computation the publish interrupted. The core saves nothing for it.

**With a full context save it works mechanically.** Saving and restoring both
register banks, `i`/`m`/`l`/`lmask`/`base`, the loop, counter, PC and status
stacks and all the status words around the continuation gives:

```text
                     stop only     stop + yield     latch      unpaced
PM 02a9  kernel fg      39,910          665,543   922,329      344,933
PM 051b  spin        45,024,810              0          0            0
PM 1746  publisher       39,263          457,005   651,141      243,232
deepest (answerer)       0x00b0           0x0090    0x0072       0x0090
deepest (caller)         0x00b0           0x0041    0x0060       0x0060
```

The starvation is completely fixed — foreground healthy, spin gone — **and the
state machine is worse.** The answerer reaches `0x0074`/`0x0090`, falls back to
`0x0024` and cycles in V.8/INFO for the rest of the run.

### The uncomfortable conclusion

Three independent ways of restoring the kernel foreground — latching, the naive
yield, the context-saving yield — all cost state progress, and the only
configuration that reaches `0x00b0` is the one where the foreground is starved
8.6x and the page is barely serviced.

That is not what a real fix looks like. **The `0x00b0` result of Sessions 164-165
should be treated as suspect**: if the states advance furthest precisely when the
foreground that would gate them is not running, the likeliest reading is that
they are advancing on timers with nothing checking them — the same pattern
Session 102 named on the answering side and Session 150 found behind the deep
states there. It is not established that `0x00b0` under stop-pacing is closer to
a connection than `0x0090` under a healthy foreground.

Defaults are unchanged and re-verified: `EICON_V34_PUBLISH_PACED=1`,
`EICON_V34_PUBLISH_LATCH=0`, `EICON_V34_PUBLISH_YIELD=0`, both ends at `0x00b0`.
All three mechanisms are kept behind their flags because the comparison above is
the most informative measurement on this path and should stay one command away.

**What to do next is a decision, not a probe:** either establish that the
stop-paced `0x00b0` trail is real by finding something in it that depends on
received signal, or accept the yield's healthier execution profile as the
correct base and re-attack from `0x0090` with the foreground running. The second
is the more honest starting point; the first is cheaper to test and should go
first — the answerer's `0x00a0..0x00ac` quiet sequence is timed, so a run with
the *caller* silenced deliberately would show whether the answerer's trail
changes at all. If it does not, the trail is timers.

## Session 167: the 0x00b0 trail is signal-driven — 166's caution withdrawn

166 suspected the stop-paced `0x00b0` trail of being timers, on the grounds that
it appears only when the kernel foreground is starved. The control settles it.

`EICON_FORCE_DM=0x3fb4=0x0000@0x0261` on the calling end zeroes the word the
harness reads the line sample through, but only once page 8 is resident, so V.8
and INFO proceed normally and the caller goes silent exactly when training
starts (caller page-8 TX RMS 776.6 -> 19.5). The answerer, unmodified:

```text
caller transmitting   0x0060 0x0064 0x0070 0x0071 0x0072 0x0074 0x0090
                      0x0092 0x0097 0x0098 0x00a0 0x00a4 0x00a6 0x00a8
                      0x00aa 0x00ac 0x00b0
caller silent         0x0060 0x0064 0x0070 0x0071 0x0072 0x0074 0x0090
                      0x0060 0x0064 0x0070 0x0071 0x0072 0x0074 0x0090   (cycles)
```

**Everything past `0x0090` requires the peer's signal.** With the caller silent
the answerer cycles back to `0x0060` indefinitely and never publishes `0x0092`.
So the trail is real, it is driven by received signal, and Session 166's
suspicion is **withdrawn**: `0x00b0` under stop-pacing is genuine progress, not a
state machine running on timers.

### And that explains why the yield and the latch lose

They do not lose because the foreground matters. They lose because they silence
the **caller**:

```text
                caller page-8 TX RMS    answerer deepest
stop (default)               776.6              0x00b0
stop + yield                   6.8              0x0090
latch                         19.5              0x0072
caller forced silent          19.5              0x0090  (the control above)
```

Under the yield the caller transmits nothing, and the answerer then behaves
exactly as it does in the deliberately-silenced control — stalling and cycling
around `0x0090`. The same for the latch. The foreground being healthy is
irrelevant to the outcome; what decides it is whether the calling end puts a
signal on the line at all.

So the causal chain, end to end:

```text
stop-pacing bounds the page to one pass per tick
  -> the calling end transmits at all (Session 149: RMS 5.0 -> 776.6)
    -> the answering end advances past 0x0090 on that signal
      -> 0x0092 .. 0x00b0, including the designed quiet sequence at 0x00a0
```

### The open problem, stated exactly

Two things are needed together and no mechanism yet achieves both:

1. **the calling end must transmit** — only stopping the core at the publish has
   ever produced this, and both alternatives silence it;
2. **the kernel foreground must run every sample** — the stop prevents it, which
   is why the page stops being serviced at `0x00b0` (Session 165).

Since (1) is now proven to be what drives the answerer forward, it takes
priority, and the default stays as it is. The question to answer next is
**why the caller only transmits under the stop** — that is a property of the
calling side's page, it has never been examined directly, and it is the one link
in the chain that is still unexplained rather than merely unfixed.

## Session 168: the caller only transmits under the stop because only the stop produces a carrier

167 left one link unexplained. It is the signal itself.

Comparing the **answerer's** transmit in the first 0.30 s of page 8 — before the
caller has transmitted anything, so the starting conditions are identical and
this is the only input the calling end has:

```text
config    rms      conc    peak      top bins
stop     1031.6   0.130   1953 Hz   1938:1.0%  1953:2.2%  1969:1.5%  2672:1.2%  3672:1.3%
yield    1034.5   0.095   2812 Hz   1953:0.8%  2109:0.9%  2500:0.9%  2797:1.0%  2812:1.1%
latch    1068.7   0.094   2953 Hz    188:0.8%  1719:0.8%  2938:0.8%  2953:0.8%  3859:0.8%
```

Under the stop the answerer emits a **carrier**: a coherent three-bin cluster at
1938/1953/1969 Hz carrying 4.7% of the band between them. Under the yield and the
latch the spectrum is flat — every top bin around 0.8-1.1%, no cluster, and the
nominal "peak" wanders to wherever the noise happens to be highest.

**1953 Hz is the V.34 carrier.** The recommendation puts it at 1959 Hz for the
3429 baud symbol rate, and the analysis bins here are 15.6 Hz wide, so
1953 +/- 8 Hz is that carrier and not a coincidence. The caller's own page-8
transmit peaks at the same 1953 Hz once it starts (Session 157).

### Why the stop is the only mechanism that produces one

Under the latch the page still runs about ten passes per 8 kHz sample and the
harness takes the first output of each group. That decimates cleanly enough as a
*sampling* strategy, which is why Session 165 expected it to work — but the
modulator's own state advances ten times per sample either way, so its carrier
phase rotates ten times too fast and nothing coherent survives at 8 kHz. The
stop is the only mechanism that throttles the page's *internal* rate rather than
just choosing among its outputs, which is the same point Session 165 made about
it being a clock and not a multiplexer, now visible in the spectrum.

### The chain, complete

```text
stop-pacing bounds the page to one modulator output per 8 kHz sample
  -> the answering end emits a coherent carrier at 1953 Hz             (168)
    -> the calling end's 0x0060 wait block detects it and exits
      -> the calling end transmits, page-8 RMS 5.0 -> 776.6            (149)
        -> the answering end advances past 0x0090 on that signal       (167)
          -> 0x0092 .. 0x00b0, including the designed quiet sequence   (164)
```

Every link is now measured, and the two ends are mutually dependent: neither
advances unless the other is emitting something detectable, which is why every
attempt to fix the foreground starvation collapsed the whole trail at once.

### What remains

The carrier is real but weak: 0.130 concentration against **0.818** for a live
modem on the same metric, with 4.7% of the band in the carrier cluster where
hardware puts 81% in 2400-3000 Hz. So the answering end is emitting a detectable
carrier buried in a great deal of broadband energy, and the caller detects it
anyway — which says the remaining gap to a connection is signal *quality*, not
signal *presence*.

That reframes the next question usefully. It is no longer "why is there no
signal" but "what is the broadband floor made of, given the modulator's symbol
input is a clean antipodal pair (157) and its arithmetic is faithful (154, 157)".
The most likely remaining candidate is the one structural thing still known to be
wrong: the page runs one pass per *sample* under the stop, but a V.34 modulator
at 3429 baud needs its interpolating filter run at the sample rate against a
symbol clock 2.33 times slower, and nothing in this harness establishes that the
page's internal symbol/sample ratio survives being throttled that way.

## Session 169: the stop truncates the modulator's polyphase loop

168 ended by asking whether the page's internal symbol/sample ratio survives
being throttled. It does not, and the numbers are unambiguous. Measured over the
modulating span — page-8 entry to the start of the quiet sequence at `0x00a0`,
18,880 samples — on a default stop-paced run:

```text
modulator outputs into the ring   24,246   = 1.284 per sample
generator loop entries (PM 3768)  44,081   = 2.335 per sample
outputs per loop entry                       0.550
line samples consumed             18,880   = 1.000 per sample
```

The generator's loop arms **`CNTR = $0003`** — three outputs per entry, which is
the interpolating filter's polyphase set for one symbol. Under the stop it
averages **0.550**. The publish stop fires on the first store, the frame is
abandoned there, and the next frame re-enters the loop **from the top** rather
than resuming it, so the second and third phases of almost every symbol are
never computed.

That is the mechanism behind the weak carrier of Session 168. The transmitter
emits phase 0 of each symbol, repeatedly, with the intervening phases missing —
which is a real carrier at the right frequency (which is why the caller detects
it at 1953 Hz) buried in the broadband splatter that dropping two of every three
interpolation phases produces. 0.130 concentration against hardware's 0.818 is
what that should look like.

Two further consequences fall out of the same table:

- the loop is **entered** 2.335 times per sample where a 3429-baud modulator
  needs one entry per 2.333 samples — off by a factor of 5.4 — and it is only
  the truncation that keeps the output rate anywhere near sane;
- supply still exceeds demand by **28%** (1.284 produced against 1.000
  consumed), so the ring drifts even in the phase where everything is working
  as well as it ever does.

### What the fix has to do

Not "stop at the first publish". The requirement is a symbol clock: **let the
`CNTR = 3` loop complete, and enter it once per 2.333 samples** rather than
2.335 times per sample, with the 60-word ring absorbing the 3:1 burst — which is
what that ring is evidently for, and why the consumer takes three words a frame.

Concretely that means pacing on **ring occupancy** rather than on publish count:
run the page when the ring needs refilling and let it finish its loop, instead of
halting it mid-symbol every tick. Both the credit word `DM(0x3761)` and the
cursor `DM(0x0F67)` are already identified (Sessions 151, 155) and either is
readable per sample, so the control input exists.

That is a different mechanism from all four tried so far (stop, latch, naive
yield, context-saving yield), and unlike them it does not have to choose between
the transmit rate and the foreground: a page allowed to finish its loop reaches
IDLE on its own, which is the condition `adsp2181_modem_sample()` already wants.

## Session 170: completing the polyphase group is worse — 169's proposed fix is disproved

169 predicted that letting the generator's `CNTR = 3` loop finish would fix the
weak carrier, on the reasoning that the 60-word ring is drained three words a
frame so one completed group per frame is what the consumer asks for.
`adsp2181_stop_on_dm_write_n()` (new) makes the stop count publishes, and
`EICON_V34_PUBLISH_GROUP` selects the count. **Measured, the prediction is
wrong.**

```text
                        group = 1        group = 3
outputs into the ring   0.624/sample     0.950/sample
quiet stretches         26,016 zeros        930 zeros
answerer carrier        0.130 @ 1953 Hz  0.073 @ 2594 Hz
caller page-8 TX RMS    776.6                9.8
answerer deepest        0x00b0            0x0064
caller deepest          0x00b0            0x0060
```

Group 3 does everything 169 said it would to the *rate* — output up by half, the
long quiet stretches essentially gone, the generator running near-continuously —
and the signal is worse on the only measure that has ever predicted anything.
The carrier drops from 0.130 to 0.073, which is the noise floor, the caller never
trains, and both ends sit at the pre-149 ceilings.

So **consuming one of three completed phases is worse than producing one**, and
169's model — that the missing phases are what buries the carrier — does not
survive contact. What the group-1 stop actually does is not "emit phase 0 of a
truncated symbol"; whatever it is, it is the only configuration in six that puts
a detectable carrier on the line.

Default returns to `EICON_V34_PUBLISH_GROUP=1` and is re-verified: both ends at
`0x00b0`, caller TX 776.6 at 1953 Hz. The knob stays for the A/B.

### The mechanisms tried, all of them

```text
mechanism                    caller TX    carrier   answerer   caller
stop at first publish (dflt)     776.6      0.130     0x00b0    0x00b0
stop after 3 publishes             9.8      0.073     0x0064    0x0060
latch first, run to completion    19.5      0.094     0x0072    0x0060
stop + naive yield                   -          -     0x0052    0x0052
stop + context-saving yield         6.8      0.095     0x0090    0x0041
no pacing, fitted budgets         ~19.5      0.096     0x0090    0x0060
```

Six mechanisms; one works and it is the crudest. That pattern — every principled
refinement losing to the accidental original — says the model of *why* it works
is still wrong, and Sessions 165, 169 and this one are three failed predictions
from three different models. The measurements are all reproducible; the
explanations have not been.

**The honest next step is not another mechanism.** It is to find out what the
group-1 stop does to the signal that the others do not, by capturing the
generator's output sequence directly under group 1 and group 3 — the values, in
order, at PM `0x3792` — and comparing them as waveforms rather than inferring
from spectra of the line. That is one run per configuration with a watch already
written, and it would settle what the carrier is actually made of before anything
else is changed.

## Session 171: the generator is broadband in every configuration; the carrier comes from the filter

Capturing the generator's output values in order at PM `0x3792`, page 8 only,
under both group settings, rather than inferring from the line:

```text
                                  n        rms   distinct   autocorr lags 1-3
group 1                       24,465      3,080     9,920    +0.22 -0.37 +0.06
group 3                       77,481      3,144    12,308    +0.14 -0.37 +0.07
```

**The two streams are the same signal.** Same amplitude, same autocorrelation
shape to two decimals, same character. Group 3 does not produce a worse
waveform; it produces three times as much of the same one, and the harness then
takes one sample in three.

Spectra confirm what that costs:

```text
group 1  generator stream as-is        conc 0.081   peak 2406 Hz
group 3  generator stream as-is        conc 0.076   peak 2562 Hz
group 3  the same stream at 1:3        conc 0.066   peak  344 Hz   <- what the line gets
```

So Session 170's result has a plain cause after all, and it is the Session 149
mechanism restated: **decimation destroys the signal, and group 3 reintroduces
it.** 169's polyphase story was wrong about which stage matters, but 149's
model — that this is a rate problem — survives everything.

### The finding that matters more

The generator's own output is **broadband in both configurations**: 0.081 and
0.076, against 0.05 for white noise. Yet the line under group 1 measures **0.130
at 1953 Hz** — better than the stream feeding it. The carrier is therefore not
coming from the modulator at all. It is being *extracted* by the downstream
interpolating FIR at PM `0x17A6`, which is doing what a pulse-shaping filter
does to a broadband input: passing the band it is tuned to.

That relocates the remaining quality gap cleanly and for the first time
unambiguously:

- the modulator's **inputs** are clean symbols — a constant, a four-point set and
  a nine-point set (Session 157);
- the modulator's **output** is broadband in every configuration ever run;
- the filter downstream recovers a weak carrier from it, which is enough for the
  peer to detect and train on but nowhere near hardware's 0.818.

**So the defect is in the modulator's own arithmetic**, between clean symbol
inputs and broadband output — not in the pacing, not in the transport, and not
in the script.

### What to do

Extend `adsp_arith_oracle.py` to the MAC modes. It has been on the list since
Session 154 and it now has a direct target: the generator is
`MR = MR + MX0 * MY0` in `(SS)`, `(SU)` and `(RND)` variants across four
consecutive taps with a final `MR = MR1 * MY0 (SU)`, and `MR1` is the high word
of a 40-bit accumulation whose fractional placement depends on `MSTAT_INTEGER`.
`(SU)` was checked by hand against Table 2-8 (Session 157) and the G.711 oracle
covers the ALU, the shifter and the sequencer (Session 154) — but nothing has
ever tested the multiplier numerically, and a multi-precision accumulation is
exactly where a wrong shift or a wrong sign extension turns a clean constellation
into broadband noise while preserving amplitude, which is precisely the observed
symptom.

## Session 172: the multiplier is faithful too — the arithmetic hypothesis is closed

`adsp_arith_oracle.py --mac` executes synthetic instructions on a bare core,
because no firmware routine exercises the multiplier against anything externally
known. Two references, one of them real ground truth:

```text
MAC modes, against Table 2-8 signedness and the fractional shift:
   (SS ) 100/100    (SU ) 100/100    (US ) 100/100    (UU ) 100/100    (RND) 100/100

Unbiased rounding, against the six vectors of 8xcompu.pdf Figure 2-11:
   00:0000:8000 -> 00:0000:0000   ok      00:0001:8000 -> 00:0002:0000   ok
   00:0000:8001 -> 00:0001:0001   ok      00:0001:8001 -> 00:0002:0001   ok
   00:0000:7fff -> 00:0000:ffff   ok      00:0001:7fff -> 00:0001:ffff   ok
```

The rounding vectors are the manual's own table, values and all, so those six
are not a re-derivation — they are the part specified from outside. The 500
signedness cases are a re-derivation of Table 2-8 and the fractional shift, which
is weaker but independent of the emulator's own reading.

(One trap, recorded because it cost a segfault: `adsp2181_set_pc` has no
`argtypes` declared in the shim, so ctypes truncates the 64-bit cpu pointer to
`int`. Any new caller of the C API from a fresh script has to declare argtypes
for everything it touches, not just the functions the shim happens to use.)

### What this closes

The emulator's arithmetic is now validated across everything the transmit chain
uses:

```text
ALU, shifter, sequencer      65,536 inputs through the card's G.711 encoder,
                             every code in the correct segment          (154)
DAG modulo addressing        mask table matches the 2^n base rule for every
                             buffer length this chain uses              (155)
MAC (SS)/(SU)/(US)/(UU)      500 cases against Table 2-8                (172)
MAC (RND)                    the manual's own six vectors               (172)
PM read/write, PX            upper-16 correct; PX width fixed           (155)
```

Two known gaps remain and both are bounded and irrelevant here: `BIASRND` is
unimplemented and affects only `MR0 == 0x8000` (154), and `saturate MR` is still
untested.

**So the modulator computes broadband output from clean symbol inputs using
arithmetic that is correct.** That combination has one remaining reading, and it
is the one this chase has not yet considered: that a broadband intermediate at PM
`0x3792` is *what a symbol mapper is supposed to produce*, with the pulse shaping
done downstream by the interpolating FIR at PM `0x17A6` — which is exactly what
Session 171 measured, the line (0.130 at 1953 Hz) being cleaner than the stream
feeding it (0.081).

If that reading is right then nothing in the modulator is wrong at all, the
carrier is weak because the *filter* is being run wrong — starved of two thirds
of its input by the group-1 stop, which is the one thing every configuration
tried so far has either done or replaced with something worse — and the fix is
the one thing not yet built: a mechanism that gives the filter its full input
rate while keeping the page at one symbol per 2.333 samples.

That is where this should resume. What it should not do is look for more
arithmetic defects; there are none left in the units that matter.

## Session 173: the hardware timer is not used, and the symbol clock is already correct

172 left the transmit chain with clean inputs, faithful arithmetic and broadband
output, and named the pacing as the last suspect. Two checks, both negative for
the hypotheses they tested, and the second one closes 168's and 169's models.

### The hardware timer is not the symbol clock

If page 8 clocked its 3429-baud symbol rate off the ADSP timer it would have had
no clock here at all: the emulator carries `ADSP2181_TIMER` as a vector but
nothing latches it, and TPERIOD/TCOUNT/TSCALE are unmodelled. Three signatures,
all negative:

```text
live PM after boot (kernel + TIKRNL, 15,367 words)
    TIMER mode-control ops        6, and all six are DIS TIMER
0261-v.34-overlay pm.bin (10,584 words)
    TIMER mode-control ops        0
    TPERIOD/TCOUNT/TSCALE refs    0
loopback, both ends, watch armed at reset, 40 s to 0x00b0
    writes to DM 0x3FFB/FFFC/FFFD 0
```

There is no `ENA TIMER` anywhere in the resident code, and no instruction ever
writes a timer register on either end across V.8, INFO and all of page 8. The
V.8 and INFO overlays each carry their own `DIS TIMER`; the firmware's habit is
to switch it off and leave it off. The watch is not silently broken — a control
on `DM(0x3FB4)` in the same rig reports normally. (`DM(0x3FFD)` reads `FFFF`
after boot and `0x3FFF` reads `1400`, but no instruction stores them, so they
arrive by IDMA and are inert with TSCALE and TCOUNT at zero.)

**So the emulator's missing timer is a real gap and not this bug.** Whatever
clocks the symbol rate is software.

### The instrument

`adsp2181_dm_census()` counts writes per DM address — coverage[] for data. The
watches say who wrote one word; identifying a *rate* needs every word counted at
once, because the candidate set is the whole page. `EICON_DM_CENSUS=<path>`
enables it over page 8 only, and `EICON_DM_CENSUS_SAMPLES` caps the sample count
so two ends holding page 8 for different spans share a denominator — they differ
by 5.5x on a 40 s run, which is enough to make raw rates incomparable.

### The symbol clock exists, and it is right

3429 baud against 8 kHz is 3 symbols per 7 samples, so a symbol clock has to
show up as sevenths and nothing else has a reason to. It does. Caller, 40,000
page-8 samples, 367 addresses written more than 0.1 times per sample:

```text
  1/7  0.1429   x2      8/7  1.1429   x4
  3/7  0.4286   x60     9/7  1.2857   x17
  4/7  0.5714   x2     10/7  1.4286   x2
  6/7  0.8571   x3     12/7, 13/7, 18/7  x1 each
```

93 of 367 land on an exact seventh within 0.4%; 27 land on some other ratio. The
60 addresses at exactly 3/7 include the ring cursor `DM(0x0F67)` (Session 155)
and the generator's own working set — `0x375E..0x3772` is seventh-denominated
end to end.

The answerer at first appeared to have none of this (3 of 346). It was an
artefact of the denominator. The census divides by page-8 ticks, and the
answerer spends 622 of 40,000 ticks publishing nothing at all, so every rate
comes out 1.6% low and falls outside the tolerance. Normalised to published
samples — `DM(0x3764)`, written exactly once per published sample — the
answerer's family is the caller's, address for address:

```text
                        denominator   on n/7   at 3/7
caller, by tick              40,000       93       60
answerer, by tick            40,000        3        2
answerer, by published       39,378       92       60
```

**Both ends run a correct 3-symbols-per-7-samples clock.** The caller publishes
on 40,000 ticks out of 40,000; the answerer on 39,378.

### What that disproves

Session 168 closed by suspecting that the page's internal symbol/sample ratio
does not survive being throttled by the stop. It survives exactly.

Session 169 went further and said the `CNTR = 3` loop is truncated — phase 0 of
each symbol emitted and the other two abandoned — on the strength of 0.550
outputs per loop entry. Divide by symbols instead of by loop entries and that
inverts: outputs run at 9/7 per sample against symbols at 3/7, which is
**3.000 outputs per symbol**, the whole polyphase group, every symbol. 169's
0.550 is real but it is measuring re-entry overhead — the loop is entered 7/3
times per sample, i.e. 49/9 = 5.44 times per symbol, and most entries produce
nothing. That is also why Session 170's group-3 experiment lost: the group was
already complete, so demanding three publishes a tick produced three times the
needed output and the harness decimated it, which is precisely what 171 then
measured in the spectrum.

### What it opens

The same numbers state the remaining defect more sharply than anything so far.
A 3429-baud modulator needs **7 line samples per 3 symbols**. This generator
produces **9 outputs per 3 symbols** and the line consumes 7:

```text
generator outputs   9/7 = 1.2857 per sample
line consumption    1.0000 per sample
surplus             2/7 = 0.2857 per sample into a 60-word ring
```

That is Session 169's "supply exceeds demand by 28%" restated exactly, and it is
now the only rate in the chain that is wrong. A fractional 7:3 interpolator does
not emit a constant 3 outputs per symbol — it emits 3, 2, 2 across three
consecutive symbols. A constant `CNTR = 3` is the signature of something that
should be varying the count and is not.

So the next question is narrow and mechanical: **is `CNTR` at the generator's
loop entry always 3, or does the firmware vary it 3/2/2 and the harness is
observing only the first of each triple?** That is a watch on the loop entry PM
`0x3768` reading CNTR, one run, and it decides between "the firmware's symbol
scheduler is being cut off" and "the firmware is being asked for a rate it was
never given the input to compute". Nothing else should be changed until it is
answered — this chase has now lost three predictions in a row to acting before
measuring.

```bash
tools/eicon_loopback.py --native-mips --native-bearer-activation \
    --force-info-after-v8 --seconds 40 --no-realtime \
    --capture-dir artifacts/loopback-v34/census \
    --caller-env EICON_DM_CENSUS=census.caller.csv \
    --caller-env EICON_DM_CENSUS_SAMPLES=40000 \
    --answerer-env EICON_DM_CENSUS=census.answerer.csv \
    --answerer-env EICON_DM_CENSUS_SAMPLES=40000
```

## Session 174: the sevenths are V.34's own, and the symbol generator runs at half the symbol rate

173 asked whether the firmware varies the generator's `CNTR` between 3 and 2 to
make a fractional 7:3 interpolator. It does not, and answering that properly
required fixing the instrument first — and then the real defect fell out of the
corrected numbers.

### Where the sevenths come from — Recommendation V.34, not inference

Table 1/V.34 gives the symbol rate as `S = (a/c) * 2400`, and for 3429 it is
**a = 10, c = 7**. Table 2/V.34 gives the carrier as `(d/e) * S`, and for 3429
both the low and high carrier are **d = 4, e = 7**, i.e. 1959 Hz. So seven is
this symbol rate's own denominator twice over, and Session 173's family —
1/7, 3/7, 4/7, 6/7, 8/7, 9/7, 10/7, 12/7, 13/7, 18/7 — is the page keeping the
recommendation's parameters, with 10/7 the symbol-rate ratio and 4/7 the carrier
ratio appearing literally. It also settles 168's carrier identification: the
spec value is 1959 Hz, the measurement was 1953 Hz in 15.6 Hz bins.

### The instrument was wrong, and PMOVLAY was not the fix

`coverage[]` sums every page ever resident at an address, which is why 173's
loop counts were incoherent (PM 0x3768 executing 4.3x more often than the
`CNTR = 3` two instructions before it). The obvious fix — key coverage by
PMOVLAY — was built and is **disproved**: every address of interest reports
`ov0` and nothing else, because the pages are *downloaded into* the same PM
rather than selected by an overlay register. Only the caller knows which page
is loaded. Replaced with `adsp2181_coverage_gate()`, driven off the same page-8
residency gate as the DM census, so an execution count and a write count now
share a page and a denominator. `EICON_PM_COVERAGE` prints it.

Session 169's "generator loop entries (PM 3768) 44,081 = 2.335 per sample" is an
ungated count. It is withdrawn.

### CNTR = 3 is an immediate, and the loop completes every time

```text
3763: 3c0035  CNTR = $0003
3768: 17792e  DO $3792 UNTIL NOT CE
3783: 0b000f  JUMP (I4)          <- vector, taken every iteration
3792: 6800c5  DM(I1,M1) = MR1    <- the store, and the loop end
```

Gated to page 8, caller:

```text
PM 0x3758  routine entry     10,181
PM 0x3763  CNTR = 3          10,181
PM 0x3768  DO                10,181
PM 0x3792  the store         30,543   = 3.0000x
```

Three stores per entry, exactly, every entry. **Nothing is truncated** — 169's
model is refuted a second time and from the instruction side this time. The
`JUMP (I4)` at 0x3783 runs once per iteration and every vector converges back on
0x3792, so the tails are symbol-type variants, not exits.

### The defect: the generator is not on a clock

Everything downstream is locked to the sample clock. The interpolating FIR at
PM 0x17A6 runs at **16/7 = 2.2856 per sample on both ends**, agreeing to four
decimals. The generator does not:

```text
                        caller     answerer     required
generator entries       0.2545       0.2082       0.4286   (3/7, the symbol rate)
  as % of required         59%          49%
stores into the ring    0.7636       0.6246       1.2857   (9/7)
FIR calls               2.2856       2.2856       -
```

The symbol rate is 3/7 per sample and the page has machinery running at exactly
that — Session 173 found 60 addresses there, including the ring cursor
`DM(0x0F67)`. The generator that is supposed to feed them runs at **half** it,
and unlike every other rate in the page it is **not the same on the two ends**:
0.2545 against 0.2082, a 22% spread where the FIR agrees to 0.01%.

A rate that varies with load is not a clock. It is what is left over, which is
exactly what the publish stop makes of the page's foreground (Session 165) —
and it now has a name and a number. The generator is being called roughly every
other symbol, irregularly, and the FIR downstream is filtering a symbol stream
with half its symbols missing at uneven intervals.

That is a complete account of the symptom 172 could not explain: clean symbol
inputs (157), faithful arithmetic (154, 172), and broadband output (171).
Dropping four symbols in ten at irregular intervals produces exactly that, and
it also explains a carrier that is present at the right frequency but at 0.130
concentration against hardware's 0.818 (168).

### What to do

The requirement is now specific enough to state as a number rather than a
mechanism: **the generator at PM 0x3758 must be entered 3 times per 7 line
samples**, and no configuration tried in Sessions 149-171 has ever put it there,
because all of them paced the *publish* and left the generator to whatever
foreground time survived.

The next step is to measure before building again. `EICON_PM_COVERAGE` on
PM 0x3758 is now a direct read of the thing that has to be fixed, so every
mechanism in the table in Session 170 can be re-scored against it in one run
each, and the question "does this mechanism give the generator its clock" gets a
number instead of an argument. That ranking should come before a seventh
mechanism is written.

```bash
tools/eicon_loopback.py --native-mips --native-bearer-activation \
    --force-info-after-v8 --seconds 40 --no-realtime \
    --capture-dir artifacts/loopback-v34/gen-rate \
    --caller-env EICON_DM_CENSUS=gen.caller.csv \
    --caller-env EICON_DM_CENSUS_SAMPLES=40000 \
    --caller-env EICON_PM_COVERAGE=0x3758,0x3763,0x3768,0x3792,0x17a6
```

## Session 175: the generator rate is anti-correlated with success — 174's requirement is wrong

174 ended by saying the generator at PM `0x3758` must be entered 3 times per 7
line samples, and that scoring the six mechanisms against that number would rank
them. It does rank them. It ranks them backwards.

`EICON_PM_COVERAGE` on the generator, gated to page 8, rates per published
sample (`DM(0x3764)` writes). The 3/7 target is V.34's, not measured here:
Table 1/V.34 puts the symbol rate at (10/7) x 2400 against an 8 kHz line.

```text
mechanism      end        published  gen/sample  % of 3/7   stores      FIR  deepest
stop-group1    caller         40000      0.2545       59%   0.7636   2.2856  0x00b0
stop-group1    answerer       39378      0.2082       49%   0.6246   2.2855  0x00b0
stop-group3    caller        120194      0.3486       81%   1.0459   2.0498  0x0060
stop-group3    answerer      120184      0.3424       80%   1.0272   2.0320  0x0090
latch          caller        262282      0.3214       75%   0.9641   1.9684  0x0060
latch          answerer      234118      0.3209       75%   0.9627   1.9675  0x0090
yield          caller         56068      0.3918       91%   1.1752   2.2087  0x0060
yield          answerer       56032      0.3787       88%   1.1340   2.1628  0x0090
budget-4125    caller         78992      0.3746       87%   1.1238   2.1281  0x0060
budget-4125    answerer       69978      0.3740       87%   1.1221   2.1269  0x0090
budget-20000   caller        262282      0.3214       75%   0.9641   1.9684  0x0060
budget-20000   answerer       234082      0.3209       75%   0.9628   1.9675  0x0090
```

**The only mechanism that trains has the worst generator rate of the six.**
`stop-group1` reaches `0x00b0` on both ends at 49-59% of the symbol rate; the
yield reaches 88-91% and stalls at `0x0060`/`0x0090`. Every mechanism that feeds
the generator better fails. 174's requirement is **withdrawn** — the generator
entry rate is not what decides this, and a mechanism built to hit 3/7 would have
been the seventh failure for the same reason as the previous four.

(Session 170's naive yield has no surviving knob, having been replaced by the
context-saving one, so the six here are the five that remain plus both budget
variants. `budget-20000` and `latch` are numerically identical throughout, which
is itself worth recording: the latch does not alter execution flow, exactly as
its comment claims.)

### What survives, and what it is worth

`stores/entry` is 3.000 in every configuration and on both ends, so the
`CNTR = 3` loop always completes. That part of 174 holds and 169 stays refuted.

The one quantity that separates the working configuration from the five failures
is downstream of the generator:

```text
mechanism        FIR calls per generator store
stop-group1              2.99  (caller)   3.66  (answerer)
stop-group3              1.96              1.98
latch / budget-20000     2.04              2.04
yield                    1.88              1.91
budget-4125              1.89              1.90
```

The working configuration runs the interpolating FIR at PM `0x17A6` about three
times per generator store; all five failures sit near two.

**This is not yet evidence of anything.** The 16/7 FIR rate quoted in 174 was
measured from `stop-group1` in the first place, so "only the working mechanism
hits 16/7" is circular, and the answerer's 3.66 already breaks the neatness of
three. What is real is a 3-versus-2 split across six independent runs; what is
absent is any reason from outside these measurements why three is right.

So the position after this session is that the one quantity derived from the
recommendation is anti-correlated with success, and the one quantity that
correlates has no independent justification. That combination says the model of
what happens between the generator and the line is still wrong. Session 171
assumed PM `0x17A6` is a pulse-shaping filter fed by the generator; these ratios
do not behave like one.

### What to look at, and what not to build

PM `0x17A6` takes its tap count from a register — `CNTR = MX1` — and its
coefficient base from `AR`, then runs a second pass from `DM(0x376D) - AY0` with
the stride negated:

```text
17a6: 0d080a  I4 = AR
17a7: 0d0c53  CNTR = MX1
17a9: 157aae  DO $17AA UNTIL NOT CE
17ac: 8376da  AR = DM($376D)
17ad: 22e20f  AR = AR - AY0
17ae: 0d080a  I4 = AR
17b1: 0d087a  M7 = AR            (M7 negated at 17b0)
17b4: 157b5e  DO $17B5 UNTIL NOT CE
```

Two passes over the coefficients from different bases in opposite directions,
with a runtime tap count and a runtime offset. A fixed pulse-shaping filter does
not need either. An interpolator whose fractional delay is carried in that
offset needs both, and that would put the rate conversion here rather than in
the generator — which would explain why the generator's own rate does not
predict anything.

The next step is to read the sequence of `CNTR` and `I4` at PM `0x17A8` under
`stop-group1`, where those values are live, and see whether the offset walks a
repeating pattern. If it does, its period is the resampler's, and it can be
compared against 7:3 directly. That is one exec watch and no new mechanism.

```bash
tools/eicon_loopback.py --native-mips --native-bearer-activation \
    --force-info-after-v8 --seconds 40 --no-realtime \
    --capture-dir artifacts/loopback-v34/fir \
    --caller-env EICON_PM_COVERAGE=0x3758,0x3792,0x17a6 \
    --watch-exec 0x17a8:60
```

## Session 176: PM 0x17A6 is a polyphase kernel, and the 9/7 "surplus" is a resampler ratio

171 called PM `0x17A6` the downstream interpolating FIR and reasoned about it as
a fixed pulse-shaping filter. It is not one. Reading its two call sites settles
what it is, and retires the last live piece of Sessions 169 and 174.

### Two resamplers over one kernel

PM `0x1769` and PM `0x1787` are structurally identical routines that both
`CALL $17A6`. Every parameter is a DM word, re-read on each call:

```text
                        routine 1 (PM 1769)   routine 2 (PM 1787)
outputs per call  \     DM(0x3755) = 5        DM(0x3754) = 9
PM coeff stride   /     same word, PM 1777    same word, PM 1794
tap count               DM(0x375D)            DM(0x375C)
output gain             DM(0x3759) = 0x470A   DM(0x3758) = 0x7FFF
coefficient base        DM(0x376E)            DM(0x3773)
history buffer          L1 = 0x4A, 74 words   L1 = 0x40, 64 words
output buffer           L0 = 0x14, 20 words   L0 = 0x14, 20 words
```

**The output count and the coefficient stride are the same word.** That is a
polyphase bank with interleaved coefficients, where the number of phases is by
construction the number of outputs per input. A fixed pulse-shaping filter needs
neither a runtime stride nor a runtime tap count; this needs both.

The phase index is table-driven rather than computed — `I6` walks a table
re-primed at the end of each call — and over 3000 consecutive calls both
sequences are exactly stable:

```text
routine 1   0 1 2 3 4  0 1 2 3 4        ascending, period 5
routine 2   0 5 4 3 2 1  0 5 4 3 2 1    descending, period 6
```

Routine 1's 74-word history is the `0x3680..0x36C9` block the symbol generator
writes (Session 152's block, Session 174's 9/7 rate), so routine 1 is the stage
the generator feeds. Its 20-word output block `0x36E0..0x36F3` is written at
exactly 1.0000 per sample — the line rate.

### The 9/7 ratio is the design, not a defect

Three generator outputs per symbol at 3429 baud is 3 x (10/7) x 2400 =
10285.714 Hz, and

```text
10285.714 x 7/9 = 8000.000
```

exactly. So **9** — the value of `DM(0x3754)`, routine 2's phase count and
stride — is precisely the phase count a 7:9 conversion from the generator's
output rate to the 8 kHz line requires. The identity comes from Table 1/V.34,
not from anything measured here.

Which means the 9/7 that Session 169 read as a 28% oversupply, and that 174
inherited as "the one rate still wrong", is **the resampler's designed ratio**.
There was never a surplus. 169 is now refuted in all three of its parts and 174
in both of its claims.

### Two things this session could not close

The tap counts do not reconcile. The disassembly has routine 1 loading
`MX1 = DM(0x375D)` and routine 2 `MX1 = DM(0x375C)`; the watch caught those
words holding `0x20` and `0x11`, while `CNTR` at the kernel entry is a stable 18
and 15 across 3000 calls. `CNTR` is loaded from `MX1` two instructions earlier,
so those should be equal. Either the routine-to-word mapping is inverted or the
words are reprogrammed between the reads sampled and the calls sampled. Recorded
unresolved rather than settled by choosing the convenient reading.

The split between the two routines cannot be turned into a rate, because it
comes from a watch limited to the first 3000 calls. Only the census figure —
routine 1's output block at exactly 1.0000 per sample — is a rate.

### The test this makes possible

Everything in Sessions 165-175 compared mechanisms on quantities defined by the
mechanisms themselves, which is why the one surviving correlation in 175 was
circular. The resampler gives an external one: it has a designed ratio, stated
by the recommendation, and either it runs at that ratio or it does not.

Gated coverage on the two call sites and on their inner stores, plus the
parameter words watched over the same window, across all six mechanisms. If
`stop-group1` is the only configuration in which the resamplers run at their
programmed output counts, that is the first non-circular account of why the
crudest mechanism is the only one that works — and if it is not, the mechanism
question is still open but at least it is being asked about something the
firmware defines rather than something the harness does.

```bash
tools/eicon_loopback.py --native-mips --native-bearer-activation \
    --force-info-after-v8 --seconds 40 --no-realtime \
    --capture-dir artifacts/loopback-v34/resampler \
    --caller-env EICON_PM_COVERAGE=0x1769,0x1779,0x1787,0x1796,0x17a6,0x3758,0x3792 \
    --watch-dm 0x3754:2000,0x3755:2000,0x375c:2000,0x375d:2000
```

## Session 177: only the working mechanism completes the resampler loops — and pacing is now finished

176 proposed the first non-circular test available in this chase: the resamplers
have a designed output count, so either they deliver it or they do not. Six
mechanisms, gated coverage on the two call sites and their inner stores.

```text
mechanism      end        routine 1        routine 2      deepest
stop-group1    caller     7.000000         9.000000       0x00b0
stop-group1    answerer   7.000000         9.000000       0x00b0
stop-group3    caller     7.617535         7.999303       0x0060
stop-group3    answerer   7.838346         8.091312       0x0090
latch          caller     8.746339         8.473472       0x0060
latch          answerer   8.759226         8.478393       0x0090
yield          caller     6.029604         7.326651       0x0060
yield          answerer   6.264596         7.461394       0x0090
budget-4125    caller     6.775547         7.645651       0x0060
budget-4125    answerer   6.784405         7.648051       0x0090
budget-20000   caller     8.746339         8.473472       0x0060
budget-20000   answerer   8.759087         8.478315       0x0090
```

Exactly, not approximately: 39998/5714 = 7 and 51426/5714 = 9, and 39375/5625
and 50625/5625 on the answerer. **`stop-group1` is the only configuration in
which either resampler delivers a whole number of outputs per call**, and it
delivers 7 and 9 on both ends. All five failures deliver fractions, which is
what a loop cut mid-pass produces.

Integrality is a property of the firmware's loop rather than of the harness, and
7:9 is the recommendation's ratio, so unlike every comparison since Session 165
this one is not circular.

### The chain closes arithmetically

Both routines are called at exactly **1/7 per line sample** — 5714 calls per
40,000 published samples:

```text
routine 2   1/7 calls/sample x 9 outputs  =  9/7 per sample
routine 1   1/7 calls/sample x 7 outputs  =  1.000 per sample  -> the line
```

Routine 2 into routine 1 *is* the 9:7 conversion, run once per seven line
samples. The 1/7 and 9/7 members of Session 173's family of sevenths are these
two call rates, and the transmit rate structure under the default mechanism is
correct end to end.

It also closes 176's open item. `DM(0x375C)` is seen holding both `0x11` and
`0x0F`, `DM(0x375D)` both `0x20` and `0x12`: the tap words are reprogrammed
during training, the `CNTR` values of 15 and 18 are genuine, and 176's
routine-to-word mapping was right. The earlier watch had a limit of 6 and caught
only the initial values.

### What the requirement actually was

**Loop completion, not rate.** That is why every rate-targeted mechanism failed,
including Session 174's: each optimised some rate while cutting a resampler
mid-pass. The crude stop wins because stopping at the publish happens to leave
both loops intact, which no refinement preserved.

### Pacing is finished as a line of inquiry

If the transmit rate structure is exactly right under the default, then the
remaining gap — carrier concentration 0.130 against hardware's 0.818 (168) — is
**not a timing problem**. Sessions 149 through 176 were all pacing, and pacing
is now demonstrably correct in the configuration that matters.

The arithmetic was validated in 154, 155 and 172. The symbol inputs are clean
(157). The rates are right (177). What has never been examined is the data: the
polyphase coefficient banks the kernel multiplies by, at PM `0x1664` and the
`0x1C61`/`0x1C64` pair the parameter words point at before they are
reprogrammed. That is where this goes next.

Two things stay open and should not be quietly dropped: the symbol generator at
PM `0x3758` runs at 0.2545 per sample against a nominal 3/7 and nothing accounts
for it; and rates being right does not prove the output is right, only that this
explanation is spent.

## Session 178: round-trip delay does not move the V.34 wall — but V.8's modulation selection depends on it

The loopback presents two emulated ends to each other over real UDP, and the
media loop is built to give latency back ("Draining a backlog is how accumulated
latency is given back... rather than leaving it as permanent one-way delay").
V.34 Phase 2 is ranging — `RTDEa`/`RTDEc` are defined as a measured interval
minus 40 ms (11.2.1.1.4, 11.2.1.2.4) and the Phase 3 recovery timers are all
"plus a round trip delay" — so a line with no length looked like a candidate for
a wall that nothing else explains.

`EICON_RX_LAG_MS` pads the receive stream once, before the first real sample.
Padding rather than a deeper queue: holding the queue only changes *when* a tick
happens in wall time, since the consumer still takes the peer's sample n on its
own tick n. What ranging needs is a shift in the sample correspondence.

### The answer is no

```text
one-way pad   caller                         answerer
     0 ms     V.8 -> INFO -> V.34, 0x00b0    V.8 -> INFO -> V.34, 0x00b0
    25 ms     V.8 -> V.22 (page 1)           V.8 -> V.22 (page 1)
    50 ms     V.8 -> V.22 (page 1)           V.8 -> V.22 + FSK (pages 1, 3)
```

No configuration moves the V.34 wall. The hypothesis is **not supported** and
the pacing-adjacent explanations are now exhausted along with it.

### Three retractions, all the same mistake

Recorded because the pattern matters more than the results.

1. "The round trip is 0.00 ms" was measured by correlating `caller.ulaw` against
   `answerer.rx.ulaw`. Both are written in the capture's `write()`, at packet
   **arrival** — wire-side, before the jitter queue. That measures the loopback
   network, which is trivially zero, and says nothing about what the modem
   consumes. Withdrawn as evidence about modem-visible delay.
2. The 25 ms failure was attributed to `--force-info-after-v8` firing at its
   hard-coded 12,000-sample threshold and desynchronising the ends by 3.9 s.
   That does happen, but it is not the cause: without the flag V.8 still
   completes at 25 ms and simply selects a different modulation.
3. "0x00d0 at 25 ms — past the wall for the first time" is a **V.22** page
   state. `TrnProgress` is page-specific and that trail
   (`0x0043 0x0047 0x0051 0x0055 0x0058 0x00d0`) shares no value with the V.34
   trail. Likewise the 50 ms answerer's `0x00b0` is a V.22/FSK state — its page
   list has no page 8. Nothing got past `0x00b0`.

All three are the same error: reading a number without checking which page,
gate or signal path it belongs to. It is Session 169's error exactly, and that
one stood for seven sessions. Any figure in this log not tied to all three
should be treated as unverified.

### V.8 has always worked, and that is the new finding

Against the suspicion that V.8 only ever completed because the harness forced
it: of the 22 live tower endpoint logs, **20 reach page 7 INFO with no fallback
at all** and one (`run36`) fired it. V.8 completes on its own, on real calls,
against a real modem, over a path with real network delay.

Which makes the table above a defect in its own right. Real modems negotiate
V.34 across links with far more than 25 ms of round trip; this one drops to
V.22, on both ends, reproducibly, and at 50 ms the two ends stop agreeing with
each other at all. **V.8's modulation selection depends on round-trip delay**,
and it should not. The CM/JM exchange carries the modulation menu, so either the
menu is being built differently or it is being received differently once the
path has length.

That is a bounded question with a clean A/B, in a phase assumed solved because
it normally reaches INFO, and it is where this goes next. The capture already
carries `v8_result`, `v8_line_result` and `v8_pending_page` per sample, so the
first step is to read what the negotiation actually concluded at each delay
before touching anything.

```bash
tools/eicon_loopback.py --native-mips --native-bearer-activation \
    --seconds 45 --capture-dir artifacts/loopback-v34/v8-lag \
    --caller-env EICON_RX_LAG_MS=25 --answerer-env EICON_RX_LAG_MS=25
```

## Session 179: V.8 picks V.22 because its result word is never written — PM 0x3982 does not run

178 left V.8's modulation selection moving with round-trip delay. It is traced
to the deciding instruction, and the cause is upstream of the decision.

### The classifier decides on one word

PM `0x3ba1..0x3bfb` (Session 15's classifier) selects the pending page purely
from `DM(0x3FC4)`:

```text
3bb3: AY0 = $0016
3bb4: AR  = AX1 AND AY0          AX1 = DM(0x3FC4)
3bb5: AR  = $0001
3bb6: IF NE JUMP $3BFB           -> DM(0x0491) = 1, page 1, V.22
      ... no other table entry matches ...
3bc8: AR  = $0007                fall-through default
3bfb: DM($0491) = AR             -> page 7, V.34
```

Watched at the branch, answerer:

```text
 0 ms   pc=3ba7 ax1=1000  ->  pc=3bc8  ->  pc=3bfb ar=0007    page 7, V.34
25 ms   pc=3bfb ax1=b13f                    ar=0001           page 1, V.22
```

`0x1000 & 0x0016 = 0`, so nothing in the table matches and control falls through
to the `AR = 7` default. `0xb13f & 0x0016 = 0x0016`, so it matches the **first**
entry and takes V.22 immediately. V.34 is not chosen by the classifier; it is
what is left when nothing else matches.

### The word is never written

`0xb13f` is the idle value, stored at sample 160 by PM `0x366d` in both runs.
What differs is what happens after:

```text
 0 ms answerer   0xb13f@PM366d ... 0x1000@PM3982 at sample 24160
25 ms answerer   0xb13f@PM366d ... 0xa03f@PM3bb2 at sample 41120
```

At 0 ms **PM `0x3982`** publishes `0x1000` before the classifier decides. At
25 ms PM `0x3982` **never executes at all**, and the classifier reads the reset
value. Nothing is corrupted and nothing is mis-set: V.8 classifies a result that
was never produced.

The PM images at the two delays are **byte-identical** over `0x3900..0x3d00`
(0 words differ), so this is control flow, not overwritten code.

Session 15's own decision procedure said to inspect `0x0491`/`0x3fc4` against
`0x075b`/`0x06b3`, and it discriminates exactly as written:

```text
run              0x3fc4 final  0491==7  075b max
0ms  caller            0x1000      yes         7
0ms  answerer          0x1000      yes         7
25ms caller            0x0004       NO         0
25ms answerer          0xa03f       NO         0
```

The delayed completion callback is not the problem — it never gets the chance.

### Two tooling corrections

`EICON_PM_DUMP` snapshotted at exit, which is whichever page the call ended on:
`0x0266` when V.8 selects V.22, `0x0260`/`0x0261` when it works. Never the page
being read. It now takes `@OVERLAY` and snapshots when that page becomes
resident. Reading the classifier off the on-disk overlay image would have been
worse still (Session 178).

And `v8_pending_page` is **not** mislabelled, contrary to 178's closing note:
`DM(0x0491)` is the pending-page word and has been since Session 15. Collecting
every distinct value a word takes across a whole run is what produced garbage,
not the label. Withdrawn.

### Next

What PM `0x3982` is, what reaches it, and which precondition fails once the path
has length. One exec watch on its entry and its caller. Note that it is the only
producer of the value that selects V.34, so anything that stops it reaching that
store costs the call its modulation -- which makes it worth knowing whether the
live tower calls reach it by the same route.

## Session 180: the answerer's V.8 receiver is starved, not broken — the calling end leaves V.8 at 1 s

179 ended pointing at the answerer's V.21 FSK receive path. Walking the chain
back from `DM(0x3FC4)` reaches it, and then goes straight through it: the
receiver is fine and there is nothing on the line to receive. **That closing
claim is withdrawn.**

### The chain, one measured link at a time

Every address below is in the V.8 overlay `0x025f`, and every figure is taken
inside its residency window.

```text
PM 0x3901    per-bit entry: AX0 = DM(0x04C4), shift into DM(0x0495)/SR1,
             dispatch through the state vector DM(0x0497)
PM 0x3950    the 10-bit framer's octet store: AR = SR1 AND 0x03FF -> DM(0x05AA..)
PM 0x3859    composer entry: I0 = 0x06EC, builds the reply into DM(0x06EC..)
             from the received list at DM(0x05AA) and our own at DM(0x3F08)
PM 0x3874    DM(I0,M1) = AY0 -- first menu word
PM 0x388d    DM(I0,M1) = AR  -- table-decoded words after it
PM 0x3965    decoder: scans DM(0x06EE..) via 0x39AE, folds the PM 0x3E44 table
PM 0x397a    DM(0x3FC4) = AR   (first store)
PM 0x3982    DM(0x3FC4) = AR   (masked store -- 179's missing write)
PM 0x3ba1    179's classifier, reads DM(0x3FC4)
```

Answerer, write-watched at both delays:

```text
                        0 ms                          25 ms
DM(0x05AA)   0xffff@PM38ef  0x0103@PM3950     0xffff@PM38ef   (nothing)
DM(0x05AB)   0xffff@PM38ef  0x0143@PM3950     0xffff@PM38ef   (nothing)
DM(0x06EE)   0x0103@PM3874                    (nothing)
DM(0x06EF)   0x0143@PM388d                    (nothing)
DM(0x3FC4)   0x3006@PM397a  0x1000@PM3982     0xa03f@PM3bb2 -- the reset value
```

At 25 ms the received-menu buffer is written once, with the `0xFFFF` fill at
PM `0x38ef`, and never again until the page is torn down. Nothing downstream of
it runs. 179's "PM 0x3982 never executes" is the fifth link in this chain, not
the first.

### The receiver is being fed a dead line

`DM(0x04C4)` is the V.21 bit decision, stored by PM `0x3ae9` once per bit slot
as `0x3FFF` or `0`. Counted over the whole V.8 window:

```text
        slots   0x3FFF   mark fraction
 0 ms     746      125           17%
25 ms    1382        2          0.1%
```

That is not a demodulator failing to lock. It is silence.

### The calling end never transmits

The wire-side captures say the same thing from the other direction. The line
into the answerer carries V.21 channel 1 (980/1180 Hz) from sample ~16,800 at
0 ms; at 25 ms it is silent from sample ~800 to ~47,200 -- 5.8 s spanning the
answerer's entire V.8 window -- and then comes up broadband as V.22.

The caller is not being starved in turn. Its own receive capture is
**byte-identical to the 0 ms run for the first 14,000 samples**, with ANSam at
2100 Hz present from sample ~4000 in both. The delay line is faithful over the
same window: `rx queue 200, substituted 0, dropped 0, clock holds 0`.

It quits anyway:

```text
                     0 ms                          25 ms
sample  640    TrnProgress 0x0001, energy     TrnProgress 0x0001, energy
sample 8160    0x0001 -> 0x0002, energy       0x0001 -> 0x0002, no energy
sample 9754                                   overlay request page 1 V.22
sample 25873   overlay request page 7 INFO
```

Both ends evaluate at the same sample. At 25 ms the caller's energy bit is
clear at that instant, and 1,600 samples later its **own DSP** requests the
V.22 page -- the request comes from the firmware, not from the shim.

So the answerer's fallback is a consequence three seconds downstream of a
decision the other end already made. Whatever is wrong is on the **calling**
side, at V.8 `TrnProgress 0x0001`, about one second into the call, and it is
upstream of everything 178 and 179 examined.

### It is the calling end that is fragile, and its V.8 entry is synthetic

Padding one end at a time separates "25 ms breaks V.8" from "25 ms breaks the
calling end":

```text
receive pad             caller        answerer
none                    V.34          V.34
answerer only, 25 ms    V.34          V.34
caller only, 25 ms      V.22          V.22 -> FSK
both, 25 ms             V.22          V.22
```

The answerer is untroubled by 25 ms on its own receive -- and it is the end
that has to demodulate V.21 CM. The delay sensitivity belongs to the calling
end alone.

Which matters, because the calling end never enters V.8 the way the answerer
does. The answerer is loaded by the kernel's own page-request routine PM
`0x0680`. The caller is pushed there by the harness: `ORIGINATE_LINE_READY`
pins `DM(0x0554)=0x20` and NOPs PM `0x3a36` to clear the dial page's two gates,
then `ORIGINATE_V8` forges the page request the firmware never makes --
`DM(0x3131)=1`, `DM(0x3132)=0x025F`, plus `DM(0x3FB0)=6` and `NORM_L`
(`DM 0x3F0D`) `= 0xB13F`. (**Session 181:** NORM_L is `DM 0x3F09`; `0x3F0D` is
read by nothing, so that last patch was inert and the caller in every run below
entered V.8 with the dial page's V.22-only `0x3004`. It does not change any A/B
in this session — both sides of each were unforced — but the caller was worse
off than described.) The shim's own comment is explicit that "the
legitimate path is an AT dial script this loopback bypasses", and `--at` does
not change it: the caller still parks at the dial gate and is still forced in
at sample 2183.

Those last two patches are the tell. Both were added because forcing the page
request left V.8 init reading state the dial page would have set -- bootpage
`0x000c` instead of `0x0006`, `NORM_L` `0x3004` instead of `0xb13f` -- and each
was found only after it had caused a wrong modulation. There is no reason to
think that list is complete, and a detector that is armed by the dial page
would fail exactly like this: fine with no delay, and out of margin with any.

So this is **not yet established as a firmware defect**. The fragile end is the
end whose V.8 entry is fabricated, and separating the two requires a caller
that reaches V.8 on its own.

### Exec watches are overlay-blind

`--watch-exec 0x3982` fired dozens of times at 25 ms with `op=22670f` -- but
the V.8 overlay's word at `0x3982` is `93fc4a`. Those hits were the V.22
overlay `0x0266` executing its own code at the same address. Same for
`0x397a`. An address-only exec watch says nothing about which page ran unless
the hits are bounded by the overlay's residency window, which is what every
table above does. 179's `EICON_PM_DUMP@OVERLAY` fixed this for PM dumps; the
watches still have it.

### The transport is not the problem

Checked before any of the above was believed, because the whole result rests on
two processes exchanging faithful audio.

* Every sender's TX capture is **byte-identical** to the peer's wire-side RX
  capture, both directions, both delays: 0 diffs, 0 alignment offset.
* All four media loops report `substituted 0, dropped 0`. A queue that runs dry
  holds the modem's clock rather than feeding it invented silence (99 holds /
  198 ms at 0 ms, none at 25 ms).
* The pad is genuine silence: `LAW_INFO['pcmu']` gives `0xFF`, linear 0. Had it
  been `0x00` it would have been a full-scale -32124 DC step at the head of the
  stream, which is the one way the pad could have killed a detector by itself.
* The frequencies are the right ones -- 2100 Hz ANSam, 980/1180 Hz V.21
  channel 1 -- and the same path at 0 ms completes V.8 to V.34 on both ends.

One check paid for itself. Comparing the two runs' received streams against
*each other*: the caller's transmit is bit-identical between them for the first
**8101 bytes (1.013 s)**, which is modem sample 8160 -- the exact instant it
leaves V.8. Up to its own decision point the caller had an identical input
stream in both runs, differing only in the 200-sample offset at which it was
consumed. That is the audio corroborating the watches.

Not verified: that the offset stays constant for the whole call rather than
drifting. It cannot affect this result, which is settled at 1 s, but anything
using `EICON_RX_LAG_MS` over a longer horizon should nail it down first.

### A real dial is not available on this product

Chased, because if the caller dialled for itself the pair above would separate.
It cannot. The dial page's two gates are driven by `DM(0x0554)`, which PM
`0x3a2b` produces by scanning twelve channel words at `DM(0x056E)` for ones
that have filled with `0xFFFF`; PM `0x3a05..0x3a22` fills them, one bit per
frame, from a correlator compared against the threshold in
`DM(0x057B:0x057C)`. Run unaided, the caller writes `DM(0x0554)` **117 times
and every one is zero**, from PM `0x3a36`, the "no channel matched" tail.

The reason is not that the line is quiet. The correlator's inputs are the state
bank at `DM(0x2fc0..0x2fd7)`, and **nothing writes it** -- 0 writes across the
park, confirming Session 96 at 84 sessions' distance. The threshold is
`0000:0000` too. The detector is not starved, it is unarmed, so no audio fed to
the caller could move it.

Session 96 already named the cause and it stands: supervisory tone detection is
configured by write database `+0x30..+0x4F`, which is all zero in every source
including the firmware's own, because this is a **PRI** card. There is no
analogue line to listen to. On a PRI, dialling is the Q.931 SETUP.
`GEN_SETUP1 = 0x048c` waits on a detector this product never arms.

### The forged page request is not the cause either

What could be fixed was the other half: the shim wrote the kernel's *outputs*.
Reading the kernel out of a live PM dump gives the real protocol.

```text
06dc: AR = DM($3FC1) AND $0100 ; IF NE JUMP $068D    the doorbell
068d: SR0 = DM($3FB0)                                the argument
068e: I4 = $315D + SR0 ; AX0 = DM(I4,M4)             page table lookup
069a: DM($3131) = AX0
069b: DM($3132) = AR
06e4: DM($3131) = M0 ; DM($3FC1) AND $FEFF           the acknowledgement
```

PM `0x0680` never executes -- `0x068d` is the entry, reached from `0x06dc` and
`0x077b`, and it is where **all three** of the answerer's page requests come
from. The V.8 overlay's own request for INFO is the matching publisher, PM
`0x375d..0x3761`: raise bit `0x0100` of `DM(0x3FC1)`, put the page in
`DM(0x0491)`/`DM(0x3FB0)`, and let the kernel do the rest.

`EICON_ORIGINATE_V8_KERNEL=1` makes the shim publish that way instead. Setting
the argument without the doorbell does nothing useful -- the status block
starts claiming page 6 while `0x0271` is still resident -- so the bit is the
request and the rest is only its argument. With it, the caller's request is
posted by PM `0x069a/0x069b` exactly like the answerer's, and 0 ms is
unchanged: V.8 -> INFO -> V.34.

It makes no difference at 25 ms:

```text
                     forged entry      kernel-posted entry
TrnProgress 0x0002   sample 8160       sample 8160
page 1 V.22 request  sample 9754       sample 9754
result               V.22              V.22
```

Identical to the sample. So the forged page request is **eliminated** as the
explanation for the caller's delay sensitivity, and the remaining synthetic
element is `ORIGINATE_LINE_READY`'s dial-gate bypass -- which, per the section
above, no amount of work on this image can remove.

### Next

The caller's delay fragility now survives a faithful V.8 entry, which is a
point in favour of it being real firmware behaviour rather than an artefact.
Not proof: the dial-gate bypass is still there and cannot be lifted.

So the question goes back to where 180 opened it, with one fewer confound: what
the caller's V.8 evaluates at `TrnProgress 0x0001`, and why a 25 ms shift in
the sample correspondence leaves its energy bit clear at sample 8160 when the
same ANSam is in its queue either way. The transition is at a fixed sample in
both runs, so it is a timer expiring against a detector that has not asserted
-- which makes the detector's integration window, not the timer, the thing to
read.

Open question for the rig: `EICON_ORIGINATE_V8_KERNEL` is off by default
because it changes the baseline every earlier session was measured against.
It is strictly the more faithful of the two and agrees with the old path at
both delays, so it is a candidate for the default.

```bash
tools/eicon_loopback.py --native-mips --native-bearer-activation \
    --seconds 22 --capture-dir artifacts/loopback-v34/v8-caller \
    --caller-env EICON_ORIGINATE_V8_KERNEL=1 \
    --caller-env EICON_RX_LAG_MS=25 --answerer-env EICON_RX_LAG_MS=25 \
    --watch-dm-writes 0x04c4:9000
```

## Session 181: the caller's V.8 has been negotiating with the dial page's V.22-only mask — NORM_L was never actually forced

180 ended with the calling end's V.8 entry as the last synthetic element and no
way to remove it. This is the part of it that was simply wrong.

### The write database base is 0x3EE0, and 0x3F0D is not a database word

Session 100 §2 moved the write-DB base from 0x3EE0 to **0x3EE4** and moved the
originate NORM_L force from DM `0x3F09` to DM `0x3F0D` with it. The base is
0x3EE0 and always was. Five independent checks, four of them off a live
capture's own `.adsp-dm.bin` (which dumps `dm[0x3EE0 + i]`, so it can be read
without assuming anything):

```text
+0x01 GEN_SETUP1   0x3EE1   caller 0x048c   answerer 0x0484   (the role word)
+0x07 INFO0_SETUP  0x3EE7   f1fd            f1fd              (Session 75 native)
+0x29 NORM_L       0x3F09   0x3004          0xb13f
+0x2b SPEED_SEL_L  0x3F0B   0x00c0          0xfffe            (Session 75 native)
```

Under base 0x3EE4, GEN_SETUP1 would be 0x3EE5, which is `0x0000` on both ends
for the whole call. The `0x00c0`/`0xfffe` asymmetry is the one Session 100 §2
itself reported one paragraph later — it read it at the 0x3EE0-based address
while arguing for the other base.

The fifth check is the V.8 overlay image. Counting direct DM accesses in
`0x025f`'s PM words (`8aaaaR` read, `9aaaaR` write, address = `(word >> 4) &
0xFFFF`):

```text
DM 0x3EE1   21 accesses      DM 0x3F09   15 accesses
DM 0x3EE5    0 accesses      DM 0x3F0D    0 accesses
```

The page never touches 0x3F0D. Every session since 100 has therefore measured a
caller whose NORM_L was **not** forced: the write landed in a scratch word, and
the caller entered V.8 with the `0x3004` the dial page's own init (PM `0x0581`)
left there — V.22 only — while the answerer offered `0xb13f`.

PM `0x37c3`/`0x37c8` do read `DM(0x3EE4)`, which is what 100 took for a
GEN_SETUP1 role test. `+0x04` is **V8_SETUP** (Session 82's word), and it is
`0x0000` on both ends of every capture, so whatever those two branches decide,
they do not decide it from the role.

### NORM_L seeds the classifier word 179 traced

`DM(0x3FC4)`, whose value picks the pending page (179), is not written from
nowhere at V.8 entry — it takes NORM_L. Caller, 0 ms, `v8_line_result` column:

```text
NORM_L left at 0x3004     0x3FC4: b13f(idle) -> 3004 @640 -> 2004 -> 1000
NORM_L set to 0xa13f      0x3FC4: b13f(idle) -> a13f @10240      -> 1000
```

`@640` is the sample the forced V.8 entry fires. So the chain from the write
database to 179's classifier is one hop, and the caller has been feeding it a
V.22-only menu.

### The fix, and what it is worth

The shim now writes `DM(0x3EE0 + 0x29)`, and it restores **the caller's own
native WDB value** rather than the constant 0xb13f that was copied off the
answerer: the CAI translation is where the modulation comes from, so a constant
makes `EICON_MODULATION` invisible to the calling side's V.8. That value is
`0xa13f` — Session 75's documented native NORM_L. `EICON_ORIGINATE_NORM_L=0xNNNN`
pins one instead; `EICON_ORIGINATE_NORM_L=` (empty) disables the write, which is
exactly what every earlier session measured. 0xb13f and 0xa13f are
indistinguishable in the event: V.8 masks the former down to the latter within
10 k samples and the two runs are identical sample for sample.

Seven runs of the V.90A rig (`--answerer-modulation v90 --caller-modulation
v90a`, 40 s, no lag):

```text
                     caller pages                        answerer pages          deepest
unforced (x3)  1x  0271 025f 0266                        025f 0266 025c          0051 / 00b0
               2x  0271 025f 0260 0261 0260              025f 0260 0261          00b0 / 00b0
forced   (x4)  4x  0271 025f 0260 0261 0260              025f 0260 0261          00b0 / 00b0
```

One of three unforced runs collapsed to V.22 with no lag at all; none of four
forced runs did, and the four are identical to each other. That is three runs
against four of a known lottery, so it is a direction, not a rate.

**It does not move the V.90A blocker.** Neither end ever requests page 13 or 14
in any of the seven runs; both stop at the standing V.34 `0x00b0` wall. V.90A
remains queued behind V.34 phase 2 exactly as `handoff.md` says.

**And it does not explain 178-180's delay fragility.** At 25 ms on both ends the
caller still leaves V.8 at sample 8160 and still takes V.22, forced or not:

```text
25 ms unforced   caller 0x3FC4 -> 0x0004 @8160   pages 0271 025f 0266
25 ms forced     caller 0x3FC4 -> 0x0016 @8160   pages 0271 025f 0266 025c
```

`0x0016 & 0x0016` matches the classifier's first table entry, so the mask is
being read and the answer is still V.22. Whatever leaves the caller's energy bit
clear at sample 8160 is upstream of the modulation menu, and 180's closing
question stands unchanged.

### What this costs the earlier record

Nothing in 178-180 is retracted — those runs were all unforced on both sides of
each A/B, so the comparisons hold. What changes is the description: "the caller
is pushed into V.8 with NORM_L forced to the full mask" was never true, and
Session 100 §2 is corrected in place. Anything that read a write-DB offset
through the 0x3EE4 base since Session 100 is off by four.

```bash
tools/eicon_loopback.py --native-mips --answerer-modulation v90 \
    --caller-modulation v90a --seconds 40 \
    --capture-dir artifacts/loopback-v90a/norml-native \
    --caller-env EICON_ORIGINATE_NORM_L=      # empty = pre-fix control
```

## Session 182: the caller's V.22 fallback is this harness's off-hook guard, and the rig starts both modems at once

181 left the delay fragility open: at 25 ms the caller still leaves V.8 at
sample 8160 and takes V.22. It is not a firmware timer and not a detector
margin. It is two harness artefacts stacked, and both are now fixed.

### The threshold is sharp, and the evaluation is at a fixed sample

Padding the caller's receive alone, V.90A rig, 12 s calls:

```text
pad        caller
 5 ms      energy at 8160, TrnProgress 0x0002 at 9920   -> INFO -> V.34
10 ms      energy at 8160, TrnProgress 0x0002 at 9920   -> INFO -> V.34
15 ms      no energy at 8160                            -> V.22 at 9733
20 ms      no energy at 8160                            -> V.22 at 9748
25 ms      no energy at 8160                            -> V.22 at 9754
```

The evaluation is at sample 8160 (1.020 s) in every run at every pad, so it is
not signal-driven. Between 80 and 120 samples of pad decides the call.

### A coincidence that cost an hour, recorded because it was convincing

The answerer's ANSam, measured off its own transmit capture: onset sample 4400
(0.550 s), a deep envelope notch at **8000 (1.000 s)** and the next at 11600 --
450 ms apart, which is V.8's phase reversal period. It is identical in every
run. The caller's energy bit, meanwhile, stays clear while the tone is present
from 0.55 s and sets in the window ending 8160.

That reads as "the bit tracks the phase reversal, and the reversal at 1.000 s
beats the deadline at 1.020 s by 20 ms". **It is wrong.** Two unrelated
one-second constants landed on the same sample. The retraction is below, and
the general lesson is Session 178's: a number that lines up is not a mechanism.

### The deadline is `--rx-guard-ms`

The endpoint substitutes silence for the modem's first `rx_guard_ms` of receive
audio -- the FXS off-hook transient guard, written for a real ATA, default
**1000 ms**. ANSam starts at 0.533 s, so the caller is deaf through the first
467 ms of the tone; the guard lifts at sample 8000 and V.8 evaluates at 8160,
one RTP packet later. What lands in that single 160-sample window is what
decides the modulation, which is why 200 samples of pad flips it.

Turning the guard off, no setup gap:

```text
guard    pad      caller
    0     0 ms    energy at 4640 (0.580s) -> 0x0002 at 6240 -> INFO at 3.034s
    0    25 ms    energy at 4800 (0.600s) -> 0x0002 at 6400 -> INFO at 3.092s
 1000    25 ms    no energy at 8160 -> V.22 at 9754
```

Detection now follows ANSam onset by ~47 ms, and 25 ms of one-way delay moves
it by exactly 25 ms. The delay is passed through, as it should be.

### The rig started both modems on the same instant

The deeper fault, and the reason the guard could collide with the handshake at
all: both endpoints' media clocks start when the bearer opens, so the answering
modem's ANSam is already 0.47 s old the moment the calling modem is allowed to
hear anything. A real call does not do that -- the calling modem is running
through dialling and call setup for a couple of seconds before the answering
modem is connected to anything, and the one-way delay is on top of that.

`--setup-gap-ms` (default **2000**, on the answering end) holds that end off the
line for the first N ms of bearer time: idle PCM goes out so the caller's clock
stays fed, arriving audio is dropped because this modem was not listening to it,
and the card is not clocked at all, so its own timers start when it answers. The
guard then expires long before the tone exists:

```text
setup gap 2000 ms, pad  0 ms   energy at 20640 (2.580s) -> 0x0002 -> INFO -> V.34
setup gap 2000 ms, pad 25 ms   energy at 20800 (2.600s) -> 0x0002 -> INFO -> V.34
```

Indistinguishable bar the 160 samples of pad. `--rx-guard-ms` is forwarded from
the loopback now, and a guard that is not shorter than the gap is warned about
loudly, because that combination is exactly this session's failure.

`EICON_ORIGINATE_V8_AFTER=<sample>` holds the forced V.8 entry, which is how the
two halves were separated before the gap existed (entry at 4000 rescued a 25 ms
call that failed at entry 640).

### What this retires

* **Session 178's headline, "V.8's modulation selection depends on round-trip
  delay", is withdrawn as a firmware finding.** The dependence is real and
  reproducible, and it is this rig's guard against this rig's simultaneous
  start. Nothing about the caller's V.8 has been shown defective by it.
* **179 and 180 are consequences, not causes.** PM `0x3982` never running, the
  classifier reading its reset value, the answerer's V.21 receiver starved --
  all of it follows from the caller abandoning V.8 at 1.02 s, which is now
  explained. Their measurements stand; their framing as firmware defects does
  not.
* Session 181's NORM_L correction is unaffected: that was a wrong address, and
  it stays wrong whatever the pacing.

Every loopback session before this one ran with both ends starting together and
a 1 s guard, so any earlier claim about *when* something happened in V.8 is
worth re-reading with that in mind.

### What it does not move: the V.34 wall, checked directly

Three 60 s runs of the V.34 rig (`--native-mips --native-bearer-activation`),
each end's own sample clock:

```text
                          answerer            caller           answerer TX
setup gap 0 (old rig)     0x00b0 at 10.38s    0x00b0, falls    frozen on one
                                              back to INFO     sample 35.9 s
                                                               from 10.37 s
setup gap 2000            0x00b0 at  9.90s    same             33.9 s from 11.91 s
setup gap 2000, guard 0   0x00b0 at  9.90s    same             37.0 s from 11.91 s
```

The last two are identical to the sample -- with a 2 s gap the off-hook guard
has expired before either end hears anything, so it is no longer in the
handshake at all. All three reproduce Session 164 exactly: the answerer walks
the twenty states to `0x00b0`, its transmit chain halts, the line freezes on one
sample value, and the caller falls back to INFO and stays there. No end ever
requests page 13/14, so V.90A stays queued behind it exactly as 181 left it.

All three report `substituted 0, dropped 0`, so the freeze is the page, not the
transport. **The wall is not of the same class as the V.8 fallback**: it does
not care when the two ends start relative to each other, and Session 165's
diagnosis -- the paced publish leaving the core non-idle, so the continuation is
skipped and the page is never dispatched -- stands as the thing to attack.

```bash
tools/eicon_loopback.py --native-mips --answerer-modulation v90 \
    --caller-modulation v90a --seconds 25 \
    --capture-dir artifacts/loopback-v90a/gap-l25 \
    --caller-env EICON_RX_LAG_MS=25          # --setup-gap-ms 0 for the old rig
```

## Session 183: V.22bis is the only modulation that has ever connected here — and `--modulation` does not select a modulation

The question was narrow: V.34 and V.90 do not connect on loopback, so does
anything below them? The answer is yes, once, by accident, and it carries no
data — and getting to it disproved the two levers that were supposed to force a
modulation in the first place.

### `EICON_MODULATION` does not reach V.8

Both ends forced to one modulation with automode off, 30 s calls, default rig:

```text
                          caller                              answerer
v22b,0 both ends          V.8 -> INFO 4.840s -> V.34 7.000s   INFO 2.840s -> V.34 5.000s -> 0x00b0 9.900s
v32b,0 both ends          identical, same samples             identical, same samples
unforced (control)        identical, same samples             identical, same samples
```

Not "similar" — the page transitions land on the same samples in all three, and
`v8_line_result` is `0x1000` in every row of all six captures. The CAI is built
correctly and does differ (`select_modulation('v22b', automode=0)` gives
`disabled=0xfff7`, `v32b` `0xffdf`, `v90` `0xff7f`), and the loopback does put
it in the child's environment. It simply has no effect on the handshake.

The reason is 181's: the V.8 menu is **NORM_L at DM `0x3F09`**, written from the
native WDB transaction, and the CAI mask is not what puts it there. So
`--modulation` / `--answerer-modulation` / `--caller-modulation` configure what
the driver asks the card for and **not what V.8 offers**, which is the opposite
of how they read. Every "forced V.34" run in this log was forced by other means
(`EICON_FORCE_V34`, the page request, the NORM_L write); none of them was forced
by this flag, and no session before this one checked that it did anything.

### Pinning NORM_L does not force V.22 either

`EICON_ORIGINATE_NORM_L=0x3004` — the dial page's own V.22-only mask — on the
caller, current pacing:

```text
[native-mips] originate NORM_L DM(0x3F09) 0x3004 -> 0x3004
caller   6 V.8 -> 7 INFO 4.580s -> 8 V.34 6.740s -> 0x00b0 -> falls back 11.820s
```

A V.22-only offer, and V.8 concluded V.34 anyway. This is worth stating plainly
because 181 left the impression that the `0x3004` mask was what chose V.22 in
the old runs: **it was not**. It was the off-hook guard, exactly as 182 says,
and with the guard out of the handshake the mask does not move the outcome.
Nothing in this harness currently selects a modulation.

### V.22bis connects, both ends, and reproduces exactly

The old rig (`--setup-gap-ms 0`, guard 1000, 25 ms pad both ends, NORM_L
unforced) reproduces `norml-lag-ctl` sample for sample on today's tree:

```text
             page            trail                                                 flags at end
caller       1 / 0x0266      0x0002 0x0043 0x0047 0x0051 0x0055 0x0058 -> 0x00d0    speed_tx|speed_rx|CTS|DSR|DCD  7.080s
             @1.020s
answerer     1 / 0x0266      0x0000 0x0006 0x0022 0x0046 0x0050 0x0054 -> 0x00d0    speed_tx|speed_rx|CTS|DSR|DCD  7.000s
             @5.160s
```

Both hold `0x00d0` for the remaining 23 s with no fallback, `substituted 0,
dropped 0, clock holds 0`. That is a completed V.22bis handshake between two
emulated ends — the only completed handshake of any modulation this project has
produced on loopback.

Session 178 saw this `0x00d0` and retracted it, correctly, as evidence about
V.34: the trail is page-specific and shares no value with the V.34 one, so
nothing had got past `0x00b0`. That retraction is not a statement that the V.22
link failed, and it should not be read as one. The modem status flags say it
did not.

### It carries nothing, and the reason is a two-page whitelist

The same configuration with `--ppp --ppp-auth chap`, 45 s: both ends reach
`0x00d0` on schedule and then produce **no HDLC, no XID, no LAPM, no PPP**.
`[ppp] usernet tcp=0 udp=0 icmp=0 opened=0 dns=0 in=0 out=0`. The `[nl] bearer
open for N_DATA` line never prints.

The gate is explicit, and it is ours:

* `_service_tx_request()` returns immediately unless
  `self.resident in (0x0261, 0x026A)` — the V.34 and V.90 DPCM overlays. On the
  V.22 page (`0x0266`) the ADDSP §5.3.1 polling data interface is never
  serviced, so no datagram is ever supplied.
* `_next_tx_words()` has the same shape: `0x026A` reads
  `_v90d_tx_bits()`, `0x0261` reads the V.34 DATASTATE words, and the `else`
  branch is `count = None`. `_lapm_active` is set only when a width is
  published, so on any other page it stays false for the whole call.
* `_nl_data_gate()` then refuses on `if self.tx_v42 and not self._lapm_active`,
  so N_DATA is never posted even if LAPM had something to post.

Three interlocking conditions, all keyed to two overlay ids. The V.42 stack
above them is modulation-agnostic and unit-tested; it is the pump attachment
that is V.34/V.90-only. Nothing about the V.22 page has been shown incapable of
carrying data — it has never been asked.

### The V.22 page drives the same data interface, and we ignore it

`--watch-dm-writes 0x3FAD,0x3FAE,0x3FAF` across the V.22 call, writes grouped by
the resident page:

```text
page                   DI_control 0x8000   DI_control 0x2000   RXD0 0x3FAE   RXD1 0x3FAF
1 V.22 / 0x0266                   19,458              19,772         9,886             0   (caller)
1 V.22 / 0x0266                   19,684              19,543         9,772             0   (answerer)
12 AT online / 0x0271                 56                   0             0             0
6 V.8 / 0x025f                         0                   0             0             0
```

So page 1 raises **bit F, the transmit-datagram request**, ~19,500 times a call,
and **bit 13, receive-datagram available**, ~19,700 times, and publishes a
receive word at `DM(0x3FAE)` ~9,900 times. It is the ADDSP §5.3.1 polling
interface at the same addresses `0x0261`/`0x026A` use — no new mechanism to
reverse-engineer. The request PCs are `0x3ff1`/`0x3fcb` (transmit) and
`0x3fdb`/`0x3fe1` (receive), inside the V.22 overlay.

Two details worth having before writing the fix:

* **`0x3FAF` is never written.** V.34/V.90 spread a datagram across RXD0 and
  RXD1; V.22 uses RXD0 alone, so `_service_rx_data()`'s two-address loop needs
  only its first arm here.
* **Every receive word is `f000`**, all 9,886 of them, one distinct value.
  Left-aligned per the existing convention that RXD b15 is the oldest bit, that
  is **four bits of mark fill** — an idle synchronous link, which is exactly
  what it should be when the host end has never sent anything. Four bits per
  datagram is also the arithmetic V.22bis wants: 4 bits × 600 baud = 2400 bit/s.
  The first receive word lands at 6.28 s (caller) / 6.88 s (answerer), as the
  link completes, and they continue to the end of the call.

The datagram width, in other words, is very likely the constant 4, and the page
announces its own readiness through the same `DI_control` bits already handled.
Both halves of the "unknown" this session started with are answered.

**The transmit side matches, and it is TXD0 alone.** `--watch-dm 0x3F05,0x3F06,
0x3F07` over a 15 s call, reads included:

```text
page                    r 0x3F05   r 0x3F06   r 0x3F07   from PM   first read
1 V.22 / 0x0266 caller       681          0          0    0x3fc4    6.82 s
1 V.22 / 0x0266 answerer     794          0          0    0x3fc4    6.22 s
12 AT online / 0x0271         28          0          0    0x3db0    -
```

The page reads `DM(0x3F05)` and never `0x3F06`/`0x3F07`, mirroring RXD0-only on
receive, and every read returns `ffff` — mark, because nothing writes it. The
first read lands on the same sample as the page's first bit-F request (6.82 s
caller, 6.22 s answerer, against handshakes completing at 7.08/7.00), which is
the second useful thing here: **the V.22 page does not ask during training.**
Being asked is therefore sufficient evidence of the data state on this page,
and no `DM(0x3FC2)` analogue has to be found — that word is V.34/V90D's and
holds whatever the previous page left in it.

One number is not yet explained and should be re-measured once the host
actually answers: transmit reads run at ~85/s where the receive side publishes
~430/s. With nothing consuming the receive word and nothing supplying the
transmit one, both sides are free-running against a host that never replies, so
neither rate means much yet. It is noted so that a later mismatch is not read as
new.

### The width is implemented

`_next_tx_words()` now has a `0x0266` arm returning the constant, `V22_OVERLAY`
/ `V22_DATAGRAM_BITS` / `V22_BIT_RATE` are module constants carrying the
measurement above, the rate publication sets 2400 symmetric rather than reading
`v34_rate()` off a stale DATASTATE word, and `_rx_datagram_bits()` is the new
page-aware receive width (`_service_rx_data()` calls it instead of
`_v34_rx_bits()`; behaviour off page 1 is unchanged). Six tests in
`tests/test_nl_data_bridge.py` cover the width, the TXD0-alone placement, the
rate, the receive width, and that the constant does not leak onto other pages.
Full suite 393 tests, green.

On its own it is inert: `_service_tx_request()` still returns on its
two-overlay test, so `_next_tx_words()` is never reached on page 1. That is
item 2, below.

### Item 2, and the loopback carries PPP

The change is one line — `V22_OVERLAY` added to `_service_tx_request()`'s page
test. `_service_rx_data()` needed nothing: the second arm of its
`(0x2000, 0x3FAE), (0x4000, 0x3FAF)` loop never fires on a page that does not
write RXD1, and the unpacking convention is already page 1's. A comment says so,
because the natural instinct on reading it is to "fix" it.

With that in place, the V.22 call from the reproduction below, `--ppp
--ppp-auth chap`:

```text
                                              caller            answerer
first synchronous TX datagram                 7.27 s            7.08 s
[v42] V.22bis synchronous data state          TX 4 bits/datagram, RX 4 both ends
V.42 detection (7.2.1)                        ODP sent          ADP x10
XID                                           command           response
SABME / UA                                    TX SABME(P)       TX UA
LAPM                                          connected         connected
PPP LCP                                       up, mru=1500, auth=chap
CHAP                                          authenticated 'ppp'
IPCP                                          up, 100.64.0.2 <-> 100.64.0.1
```

17 frames received at each end, **zero bad FCS, zero aborts, zero
retransmissions, no REJ and no T401/T403 expiry**, media `substituted 0,
dropped 0, ratio 1.00x` on both. The whole stack — synchronous mailbox, HDLC,
LAPM, XID, PPP, CHAP, IPCP — came up over two emulated cards at 2400 bit/s, at
the first attempt, with nothing else changed.

**This is the first data path this project has completed end to end**, and it
is worth being precise about what it is and is not. It is: the card's own
firmware on both sides of a modem link, carrying framed, FCS-checked,
acknowledged payload that both ends acted on. It is not interop evidence: two
emulated ends share their bugs, and a V.42 implementation talking to itself
proves the pump beneath it, not the protocol.

### And it carries user traffic

Nothing in the harness originated IP, so the first run was all control plane.
`--ppp-ping ADDRESS` (client end only; `peer` means the address the server
settled on) sends one echo request a second once IPCP is up and matches the
replies by sequence number. `ppp.icmp_echo_request()` and
`parse_icmp_echo_reply()` are the two halves, tested against the existing
`IcmpEchoResponder` so the instrument is known good before a link is blamed.

```text
[ping] 100.64.0.1 seq=1 sent      [ping] reply seq=1 in 500 ms
[ping] 100.64.0.1 seq=2 sent      [ping] reply seq=2 in 500 ms
[ping] 100.64.0.1 seq=3 sent      [ping] reply seq=3 in 500 ms
[ping] 100.64.0.1 seq=4 sent      [ping] reply seq=4 in 501 ms
[ppp] usernet tcp=0 udp=0 icmp=0 opened=0 dns=0 in=4 out=4
```

4/4 answered, 29 frames each way, no bad FCS and no retransmissions. `icmp=0`
with `in=4 out=4` is correct rather than a miss: a ping to the gateway address
is answered inside the NAT and never becomes a host socket, which is what
`usernet` documents.

**500 ms is the right answer, and that is the useful part.** A 32-byte echo
request plus PPP/HDLC and LAPM framing is ~40 octets, 320 bits, 133 ms each way
at 2400 bit/s; two crossings plus the acknowledgement and the 20 ms media
quantum land squarely on half a second. The link is not merely passing traffic,
it is passing it at V.22bis speed — which is also the first end-to-end
confirmation that the constant-4 width is right, since a wrong width would not
produce a plausible rate *and* a valid FCS.

The `at_watch()` rate word (item 3) is still unmeasured on page 1 and did not
stand in the way here, because PPP rides the V.42 link directly and never
consults the AT layer. It will matter to `--v42-pty`.

### What was fixed, and what is left

1. ~~The datagram width for page `0x0266`.~~ **Done.**
2. ~~`0x0266` into `_service_tx_request()`'s page test, and the receive arm.~~
   **Done**, and it carries PPP.
3. **`at_watch()`'s rate word** is still open. It reads WDB `+0x01` and rejects
   anything with the GEN_SETUP1 bits set; what page 1 publishes there is
   unmeasured. Without a `CONNECT` the AT parser stays in command mode and
   silently eats terminal text, which is the failure already documented in that
   function's docstring — so this is what stands between here and `--v42-pty`
   on a V.22 call.

Next, in rough order of what each would buy:

* **Traffic beyond the gateway.** The ping above is answered inside the NAT.
  Nothing has yet been re-originated as a host socket from a V.22 client, so
  `usernet`'s TCP/UDP/DNS paths are still exercised only by their own tests
  and by the V.90 rig.
* **A rig that reaches V.22 deliberately.** All of this rides the old pacing
  (`--setup-gap-ms 0`, guard 1000, 25 ms pad) because nothing in this harness
  selects a modulation — see the top of this session. A supported way to ask for
  a modulation would make the V.22 data path a fixture rather than a trick.
* **V.42bis and V.44 over it.** Both are implemented and both are exercised only
  against hardware or in unit tests; a 2400 bit/s emulated link is a cheap place
  to run them, and compression bugs show up faster on a slow link.

### Reproduction

```bash
# the connection (old-rig pacing is load-bearing here, not incidental)
tools/eicon_loopback.py --native-mips --seconds 30 --setup-gap-ms 0 \
    --caller-env EICON_RX_LAG_MS=25 --answerer-env EICON_RX_LAG_MS=25 \
    --caller-env EICON_ORIGINATE_NORM_L= \
    --capture-dir artifacts/loopback-lowspeed/v22-repro

# the same call with the data path attached. Before item 2 this reached
# 0x00d0 and produced nothing (artifacts/loopback-lowspeed/v22-ppp); after it,
# LAPM connects and PPP reaches IPCP (.../v22-ppp2)
tools/eicon_loopback.py --native-mips --seconds 45 --setup-gap-ms 0 --ppp \
    --ppp-auth chap --caller-env EICON_RX_LAG_MS=25 \
    --answerer-env EICON_RX_LAG_MS=25 --caller-env EICON_ORIGINATE_NORM_L= \
    --capture-dir artifacts/loopback-lowspeed/v22-ppp2

# the ping across it: 4/4 at ~500 ms, which is 2400 bit/s with framing
tools/eicon_loopback.py --native-mips --seconds 60 --setup-gap-ms 0 --ppp \
    --ppp-auth chap --ppp-ping peer --ppp-ping-count 4 \
    --caller-env EICON_RX_LAG_MS=25 --answerer-env EICON_RX_LAG_MS=25 \
    --caller-env EICON_ORIGINATE_NORM_L= \
    --capture-dir artifacts/loopback-lowspeed/v22-ping

# the transmit side of it: reads of TXD0..TXD2, so --watch-dm, not -writes
tools/eicon_loopback.py --native-mips --seconds 15 --setup-gap-ms 0 \
    --caller-env EICON_RX_LAG_MS=25 --answerer-env EICON_RX_LAG_MS=25 \
    --caller-env EICON_ORIGINATE_NORM_L= \
    --watch-dm 0x3F05,0x3F06,0x3F07 \
    --capture-dir artifacts/loopback-lowspeed/v22-txd

# the data interface the V.22 page drives and we ignore
tools/eicon_loopback.py --native-mips --seconds 30 --setup-gap-ms 0 \
    --caller-env EICON_RX_LAG_MS=25 --answerer-env EICON_RX_LAG_MS=25 \
    --caller-env EICON_ORIGINATE_NORM_L= \
    --watch-dm-writes 0x3FAD,0x3FAE,0x3FAF \
    --capture-dir artifacts/loopback-lowspeed/v22-watch

# the flag that does nothing (v32b for the other half of the A/B)
tools/eicon_loopback.py --native-mips --answerer-modulation v22b,0 \
    --caller-modulation v22b,0 --seconds 30 \
    --capture-dir artifacts/loopback-lowspeed/v22b
```

## Session 184: the classifier is the modulation selector, and V.32 stalls on an unserved partial overlay

183 left two things open: nothing in this harness could ask for a modulation on
purpose, and V.22 was only reachable by breaking V.8 with the old pacing. Both
are answered by reading the classifier properly, and the answer generalises to
every page.

### The whole table, not two branches of it

Session 179 read PM `0x3ba1..0x3bfb` and recorded the V.22 entry and the
default. Dumped from the resident V.8 overlay (`EICON_PM_DUMP=...@0x025f`, which
is the only trustworthy source -- 178) and disassembled, it is a seven-entry
table over one word:

```text
3baf: AX1 = DM($3F09)            NORM_L
3bb0: AY0 = $EEFF
3bb1: AR  = AX1 AND AY0          the seed: NORM_L with bits 12 and 8 cleared
3bb2: DM($3FC4) = AR
3bb3: AY0 = $0016  AR = AX1 AND AY0  AR = $0001  IF NE JUMP $3BFB   page 1  V.22
3bb7: AY0 = $6000  AF = AX1 AND AY0             IF NE JUMP $3BCA   page 2  V.32
3bba: AY0 = $0029  AF = AX1 AND AY0  AR = $0003 IF NE JUMP $3BFB   page 3  FSK
3bbe: AF = AX1 AND $0040          AR = $0011    IF NE JUMP $3BFB   page 17
3bc1: AY0 = $0E00  AF = AX1 AND AY0  AR = $0004 IF NE JUMP $3BFB   page 4  FAX
3bc5: AF = AX1 AND $0080          AR = $0014    IF NE JUMP $3BFB   page 20
3bc8: AR = $0007                                                   page 7  INFO
3bfb: DM($0491) = AR
```

Entered at `0x3ba5` there is one further test ahead of these -- `AX1 & 0x0100`
goes straight to the `AR = 7` default -- which is why V.8's own result word
`0x1000` selects V.34: not by matching anything, but by matching nothing.

**`DM(0x3FC4)` is therefore the modulation selector, and it is writable.**
`EICON_FORCE_DM=0x3FC4=<word>@0x025f` (Session 138's knob, restricted to the V.8
overlay so it cannot follow the value onto a page where that address means
something else) picks the page:

```text
0x0004  ->  page 1   V.22       reached, trains, carries data
0x6000  ->  page 2   V.32       reached, stalls -- see below
0x0001  ->  page 3   FSK        untried
0x0800  ->  page 4   FAX        untried
none    ->  page 7   INFO/V.34  the standing 0x00b0 wall
```

This is the first thing in this project that selects a modulation on purpose.
`--modulation` never did (183), and neither does NORM_L: `0x6000` there does not
even reach the classifier, because it makes V.8 *complete* -- `DM(0x3FC4)` walks
`0xb13f -> 0x7000 -> 0x6000 -> 0x1000` and the call goes to V.34 by the default
branch. The seed only decides the fallback, and only when there is one.

### V.22 without the trick

`EICON_FORCE_DM=0x3FC4=0x0004@0x025f` on both ends, default pacing -- setup gap
2000, guard 1000, no lag, none of 183's old-rig configuration:

```text
answerer   6 V.8 -> 1 V.22 at 2.840 s        caller   6 V.8 -> 1 V.22 at 4.840 s
[v42] V.22bis synchronous data state: TX 4 bits/datagram, RX 4  (both ends)
LAPM connected, IPCP up, ping 3/3 at 440 ms
```

So the V.22 data path is now a fixture rather than a trick, which is what 183
asked for. (440 ms against 183's 500 ms: the two rigs put the ends in a
different phase relative to each other, and the round trip is quantised by the
20 ms media tick.)

### V.32 selects, loads, and then asks for something we do not serve

`0x3FC4=0x6000` puts **both** ends on page 2 and loads overlay `0x0266` -- the
same image as V.22, which is why it is called "V.22/V.32 LEC". Then both stop
dead at `TrnProgress 0x0000`, cycling `Rstatus 0x0100 boot_request` for the rest
of the call, with no state ever published.

`--watch-dm-writes 0x3131,0x3132,0x0491,0x3FB0` says exactly what it wants:

```text
3fb0=0013 from PM 1f8c        bootpage 19 -- the "partial overlay" pseudo-page
3131=0001 from PM 069a        request posted
3132=0267 from PM 069b        V.32 Partial Overlay
```

and the endpoint answers `shared boot word 6 V.8 -> 0x0013 (19); no valid
overlay page`. **The harness's page-request server does not implement partial
overlays.** It serves whole pages out of `download_descriptors` and has no
notion of page 19, so the request is dropped and the page waits for it forever.

Ruled out while getting there: `0x0267` is *not* a missing download. It is in
file set 5 of both `dspdload.bin` and `dspdvmdm.bin` -- staging it explicitly
fails with "already in file set 5" -- so this is Session 134's V.90A situation
in reverse. The image is there; the load mechanism is not.

The DIAL chain documented in `docs/dial_kernel_dispatch.md` is the same shape --
`SIG (0x0270) -> DIAL (0x0262) -> DIAL partial (0x0263) -> V.8 (0x025f)` -- so
whatever serves a partial there is the model for serving this one. Note that the
partial is an *overlay onto the resident page*, not a page change: `0x0266` is
already loaded and stays loaded, which is presumably why bootpage 19 is a
pseudo-page rather than a real one.

### Next

1. **Serve the partial overlay.** One mechanism unblocks V.32 and probably
   FSK and FAX with it, since all three are pages this harness has never had a
   reason to load. Start from how `0x0263` reaches the DIAL page.
2. **Then run the data path over V.32.** The pump attachment from 183 is
   modulation-agnostic apart from the width constant: V.32bis at 14400 is
   6 bits per datagram at 2400 baud, and the same measurement (`0x3FAE`
   contents, `0x3F05` reads) will confirm or refute that in one call.
3. **FSK and FAX are one flag away each** (`0x3FC4=0x0001`, `0x3FC4=0x0800`)
   and have never been tried. Cheap, and each one either loads or names its own
   missing mechanism.

```bash
# the selector, and the deliberate V.22 rig it buys
tools/eicon_loopback.py --native-mips --seconds 45 --ppp --ppp-auth chap \
    --ppp-ping peer --ppp-ping-count 3 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x0004@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x0004@0x025f \
    --capture-dir artifacts/loopback-lowspeed/v22-deliberate

# V.32: selected on both ends, stalls asking for 0x0267
tools/eicon_loopback.py --native-mips --seconds 20 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --watch-dm-writes 0x3131,0x3132,0x0491,0x3FB0 \
    --capture-dir artifacts/loopback-lowspeed/v32-req

# the classifier itself, off the resident overlay
EICON_PM_DUMP=0x3b90:0x3c10:/tmp/cls.csv@0x025f
```

## Session 185: partial overlays are served now, and V.32 runs away in the LEC instead of training

184 left V.32 selectable and stalled, waiting on download `0x0267` that nothing
loaded. The loader exists now. **V.32 still does not train**, but it fails
somewhere new and specific.

### The request flag is invisible, so do not wait for it

The whole-page path serves on `DM(0x3131)`, and that is why it never saw this
request. Instrumented at the decision point:

```text
sample 24342  bootpage=0x0002 req=1 want=0x0266   <- served, flag cleared by us
sample 24343  bootpage=0x0013 req=0 want=0x0267   <- the partial, flag already gone
```

`DM(0x3131)` is posted at PM `0x069a` and cleared again at PM `0x06e4` **inside
one 8 kHz frame**: on hardware the kernel completes the transfer itself, so the
flag's whole life happens between two host samples. Sampling once per frame can
only ever see it by luck.

What does stand still is the pair the kernel leaves behind — **bootpage 19 and
`DM(0x3132)`**. Bootpage 19 is not a page; it is the marker for "overlay onto
the resident page and come back", and it holds for ~640 samples. `0x0267` sat
in `DM(0x3132)` untouched for all of it. `_service_partial_overlay()` triggers
on that pair, loads through the same native loader, and then does two things the
whole-page path does not:

* **leaves `self.resident` alone.** After a partial the running page is still
  the underlying one. Setting it to `0x0267` would have taken V.32 straight out
  of the transmit and receive paths Session 183 had just given it.
* **runs the continuation at `DM(0x3143)`.** Missing this was worth a debugging
  round: the partial landed, nothing ran it, and 0.4 s later the page gave up
  and fell all the way back to DIAL (`bootpage 0`, overlay `0x0262`).

A dropped page request also prints now. It used to be silent, which is why a
page waiting forever and a page that had stalled on its own looked identical.

### What the partial actually is

Worth knowing before blaming the loader: the staged `0x0267` is **seven DM
blocks and no PM at all** — `0x0485` (156 words), `0x0680` (16), `0x3676` (3),
`0x3680` (332), `0x3fb2` (2), `0x3fb8` (2), and three words of segment 4 at
`0x32f0`. It is pure data, including the per-frame dispatch vector at `DM(0x3fb8)`
that Session 113 already knows is load-bearing. Nothing needs relocating: no
block carries a relocation.

Three variants of `0x0267` ship under the same id (usage masks `900100`,
`6efe13`, `00000c`) and **the `900100` one is empty — zero blocks**. File set 5
selects `6efe13`, the seven-block one, so this is not the empty-variant trap;
but a future "the partial did nothing" should check which variant got staged
before anything else.

### V.32 gets further and then eats the core

With the partial served, both ends take it and keep going:

```text
[native-mips] partial overlay 0x0267 applied to 0x0266 at sample 24343, resumed at PM 0x06df
DI_control=0xa000[tx_request|rx0_valid]
```

So the page reaches the data interface and starts asking for datagrams, which
is further than any V.32 attempt has got. Then:

* **the line goes silent.** TX RMS over the back 60% of the call is **0.0** on
  both ends, against 252/261 for the working V.22 rig.
* **the core runs away.** `--pc-histogram --pc-histogram-from 0x0266`:
  **93 PCs, 310,964,000 instructions**, with six instructions of one MAC loop
  taking 6.2% each:

```text
1db5  19385673  6.2%  MR = MR + MX0 * MY0 (SS), MX0 = DM(I1,M2), MY0 = PM(I4,M7)
1db6  19385672  6.2%  MR = MR + MX0 * MY0 (RND)
1db7  19385673  6.2%  saturate MR
1db8  19385673  6.2%  MR = MR1 * MY1 (SU)
1db9  19385673  6.2%  saturate MR
1dba  19385673  6.2%  RTS
1daa   9692837  3.1%  ... AR = DM($376D); AR = AR - AY0; I4 = AR; M7 = -M7
```

* the media clock collapses to **0.52x** and the answerer's status block is
  taken over as scratch, so `TrnProgress` stops meaning anything (Session 136).

93 distinct PCs against the caller's 7,955 is the shape of a page doing one
thing forever. `0x1900..0x1dff` is the overlay's own code — this page is the
"V.22/V.32 **LEC**" image — and `DM(0x376D)` is read every iteration to compute
a PM index with a negated stride, which is a delay-line walk.

**This is Session 115's failure again, one page over.** There the native bulk
worker at PM `0x1930` spun ~928 M iterations because its modulo bound was zero,
and the fix was to find the length the firmware never seeded. The next move is
the same: find what bounds `0x1daa..0x1dba`, starting from `DM(0x376D)` and the
`I4`/`M7` pair, and check whether the partial was supposed to seed it — its
`0x3680` block is 332 words, which is the right size for exactly this kind of
coefficient/length workspace.

### Not affected

The V.22 path is unchanged by all of this: page 1 never sets bootpage 19, and a
re-run of the deliberate V.22 rig still reaches LAPM, IPCP and 2/2 pings at
440/445 ms. Suite 395.

```bash
# V.32: partial served, page continues, core runs away
tools/eicon_loopback.py --native-mips --seconds 20 \
    --pc-histogram --pc-histogram-from 0x0266 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --capture-dir artifacts/loopback-lowspeed/v32-pc
```

## Session 186: the LEC bound was fine — the partial was overwriting it — and V.32 still does not train

185 called the V.32 failure "a delay-line walk with no bound" and pointed at the
MAC loop at PM `0x1daa..0x1dba`. **That reading is withdrawn.** The bound is
there, the loop is normal, and the damage was this harness's.

### The bound, and what it actually holds

Disassembled from the resident overlay, the loop is a subroutine with two short
`DO` loops, and the outer walk above it is:

```text
1d8e: CNTR = DM($3754)          <- the tap count
1d8f: DO $1D9D UNTIL NOT CE
1d90: I6 = DM($3771) ... 1d95: CALL $1DA6 ... 1d9d: DM($3769) = I1
```

`--watch-dm-writes 0x3754,0x3755,0x375C` says the page computes **9** into it
(PM `0x1c88`, sample 22560), with `0x3755` = 5 and `0x375C` = 17. Sane filter
lengths, seeded by the page itself, long before it asks for the partial. Nothing
in the firmware is unbounded.

### What broke it was Session 185's own loader

The staged `0x0267` has seven DM blocks. Compared against the base `0x0266`
block by block:

```text
0x0485  156 words   new
0x0680   16 words   new
0x3676    3 words   byte-identical to 0x0266's
0x3680  332 words   byte-identical to 0x0266's     <- the LEC workspace
0x3fb2    2 words   byte-identical to 0x0266's
0x3fb8    2 words   differs                        <- per-frame dispatch vector
0x32f0    3 words   differs
```

A partial repeats whole blocks of the page it extends. `0x3680..0x37cb` covers
`0x3754`, and the shipped template there is `0xfff4`. The page had already put 9
in it; re-applying the block put `0xfff4` back, and as a 14-bit `CNTR` that is
**16,372 iterations** — precisely the runaway 185 measured and misattributed to
the firmware.

So the rule: **apply what a partial adds, keep what it merely repeats.**
`DspCodeImage` now carries each staged download's DM block contents, and
`_duplicate_partial_blocks()` compares the partial against the resident base;
those ranges are saved before the native loader runs and written back after it.
The blocks the partial actually contributes are untouched by this.

### It changed the outcome, and not enough

Both ends now **transmit** after taking the partial -- TX RMS over the back half
of the call goes from **0.0 to 106.3 / 107.0** -- and both reach
`DI_control=0xa000[tx_request|rx0_valid]`. V.32 still does not train.

The control that should have been run in 185 reframes what is left. Same
overlay, same histogram window, page 1 instead of page 2:

```text
                 distinct PCs   instructions   media   1db5 executions
V.22 (works)            3,619    144,059,222   1.00x         4,176,672
V.32                       93    310,964,000   0.42x        19,385,673
```

**The LEC loop is not the anomaly** -- the working page runs the same
instruction 4.2 million times without trouble. The anomaly is 93 PCs against
3,619: the V.32 page runs the echo canceller and the kernel's SPORT ISR and
*nothing else*. It never advances into the demodulator or the state machine, so
it saturates its per-sample allowance forever and the media clock collapses.

Budget starvation is ruled out. `EICON_ADSP_BUDGET` is new (the general
equivalent of `V34_CYCLES_PER_SAMPLE`, which only ever applied to page 8), and
at 200,000 -- ten times the default -- the behaviour is identical to the
instruction.

### Next

The question is now well posed: **what does the V.32 page dispatch into after
the LEC, and why does page 2 never get there when page 1 does?** The two blocks
the partial genuinely contributes are the place to start, and one of them is
already known to matter:

* `DM(0x3fb8)`, the per-frame dispatch vector Session 113 found `PortableBulkDelay`
  writing over. The partial sets it, and its value differs from the base's.
* `DM(0x32f0)`, three words of segment 4 -- the segment `load_native_overlay()`
  relocates to `0x32F0` by hand, which makes it worth checking that our fixed
  base is right for a partial.

Also worth an hour: `0x0485` (156 words) and `0x0680` (16), the partial's actual
new content, are unexamined.

### Not affected

V.22 is unchanged -- page 1 asks for no partial -- and still reaches LAPM, IPCP
and 2/2 pings at 440 ms. Suite 395 (one known-flaky usernet TCP FIN test, which
passes in isolation and touches none of this).

```bash
# V.32 with the workspace kept: transmits, still 93 PCs
tools/eicon_loopback.py --native-mips --seconds 20 \
    --pc-histogram --pc-histogram-from 0x0266 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --capture-dir artifacts/loopback-lowspeed/v32-pc2

# the control: same overlay, page 1, 3619 PCs at 1.00x
tools/eicon_loopback.py --native-mips --seconds 20 \
    --pc-histogram --pc-histogram-from 0x0266 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x0004@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x0004@0x025f \
    --capture-dir artifacts/loopback-lowspeed/v22-pc
```

## Session 187: DM(0x3fb8) is the dispatch vector and the partial sets it correctly — V.32 is Session 165's blocker

Both of 186's leads check out clean, and eliminating them lands V.32 on the
mechanism that already blocks V.34.

### DM(0x3fb8): the vector is right, and it is never called

It is the per-frame dispatch vector, confirmed by execution rather than by
reading:

```text
DM(0x3fb8)   base 0x0266: 3e4c 2c55        partial 0x0267: 3536 2400
PM 0x3e4c    V.22: 20,016 executions       V.32: never
PM 0x3536    V.22: 1                       V.32: never (4 at a 400k budget)
```

20,016 executions across a 20 s histogram window is once per 8 kHz sample: on
the working page, `DM(0x3fb8)` word 0 *is* the frame handler. The partial
replaces `3e4c 2c55` with `3536 2400`, which is exactly what a V.32 partial
should do, and our loader applies it. **The vector is installed and then never
called.**

### DM(0x32f0): the segment base is right too

The hypothesis was that `load_native_overlay()`'s hardcoded segment-4 base
could be wrong for a partial. It is not: `0x0267` declares segment 4 with base
13040 = **0x32F0**, precisely the constant the loader writes, and its three
words `0004 0001 0001` are the first three of the base's five
(`0004 0001 0001 0000 0000`) with identical values. Nothing is misplaced and
nothing is missing. Dead end, closed.

### What is actually wrong is not V.32's

`adsp2181_modem_sample()` injects the per-frame continuation **only when the
core is idle** — the core's own comment says so and cites Session 165. The
`yield_on_stop`/`stop_dm_hit` path is the one exception, and it exists because
the same thing bit page 8: it saves every register, DAG, loop and stack around
the continuation and puts the core back where it was.

The V.32 page does not reach idle inside 20,000 instructions, so it never gets
the continuation, so `0x3536` is never entered, so it never advances — and what
keeps executing is the LEC foreground it was suspended in the middle of. That is
why the histogram showed 93 PCs and a DO body repeating without its own entry:
not a loop that cannot end, but a frame that never finishes and is never
re-dispatched.

Raising the allowance confirms it end to end:

```text
budget     distinct PCs   instructions   0x1d87 entries   0x3536
 20,000              93     310,964,000                0   never
400,000           5,850   2,199,626,094        9,644,767   4
```

**So V.32 is not a V.32 problem.** It is the standing Session 165 blocker — the
one the handoff already names behind V.34's `0x00b0` wall — reached from a
different page. Two of the three modulations this project cannot connect now
fail for one reason.

`EICON_ADSP_BUDGET` is not the fix and should not be read as one. A 33 MHz
ADSP-2181 has ~4,125 cycles per 8 kHz sample and a 2185N at 75 MHz has ~9,375;
the default 20,000 is already generous and 400,000 is roughly a hundred times
the hardware's. A page that needs it is being served wrongly, not slowly, and
the 400k run still ended back on DIAL.

### Next, and it is a good one

**Deliver the continuation to a non-idle core.** The machinery already exists
and is proven: the `yield_on_stop` path does exactly this, with a full
save/restore around it, for the V.34 publish. Generalising it — inject the
continuation whenever the budget expires with the core non-idle, rather than
only on a publish stop — is the direct attack on Session 165, and it now has two
independent test cases: V.34's `0x00b0` and V.32's silent page. The V.22 path is
a control that must stay working, since it reaches idle every frame and would
not be affected if the change is right.

## Session 188: the non-idle continuation is built — it unblocks V.32's state machine and regresses V.34, so they are not one blocker

187 proposed delivering the per-frame continuation to a non-idle core and
predicted it would fix V.34's `0x00b0` and V.32's silent page together, because
both were Session 165's blocker. **The mechanism works and the prediction is
half wrong.** It moves V.32 and it makes V.34 worse, so the two failures do not
share a cause.

### The mechanism

`adsp2181_modem_sample()` injected the continuation only out of IDLE, plus the
one `yield_on_stop`/`stop_dm_hit` exception built for the V.34 publish. That
exception already did the hard part — a full save/restore of core, alt, DAGs,
loop/counter/PC/status stacks and `px` around an injected call — so generalising
it was mostly widening the condition:

```c
} else if ((a->yield_on_stop && a->stop_dm_hit) || a->continue_non_idle) {
```

`adsp2181_continue_non_idle()` is the new C entry point;
`EICON_CONTINUE_NON_IDLE` selects it **per resident overlay** (a comma-separated
list of ids, or `1` for every page), and `_apply_continue_non_idle()` re-arms on
every page load and after a partial is served, because the resident page changes
several times in one call. It is **off by default** and should stay off until a
page it helps actually connects. `EICON_CNI_TRACE=1` prints one injection line
per 8,000 for checking that it is firing at all.

Per page rather than globally, and this is not tidiness: arming it everywhere
drops the call to DIAL at 0.54 s, because V.8 spans budgets deliberately — its
FFT work is designed to be continued on the next SPORT frame with its context
intact, and injecting the kernel continuation underneath it destroys that.

### V.32: from a dead stall to a walking state machine

187 left V.32 at `TrnProgress 0x0000` for the whole call, with the partial's
frame handler `PM 0x3536` never entered. With `EICON_CONTINUE_NON_IDLE=0x0266`
on both ends:

```text
caller     0002 0001 0002 0003 0004 0005 0006 0009      0x0009 at 4.80 s
answerer   0000 0004 0003 0009                          0x0009 at 2.82 s
PM 0x3536  caller 23 executions, answerer 1             (187: never)
```

So the state machine runs, and the vector the partial installs is reached. Then
both ends stop at `0x0009` for the remaining 25 s of a 30 s call. Transmit dies
at 4.83 s on both ends — **0.2 s before the partial is applied at 5.03 s** — so
`0x0009` is the state that requests the partial, and nothing resumes afterwards.

### Where the V.32 page actually is, which is not where 185/186 thought

`--pc-histogram-from 0x0266` on the answerer, against the V.22 control taken the
same way on the same overlay image:

```text
                frames(PM 0x0014)   total instr   per frame   LEC 0x1d00-0x1dff   distinct PCs
V.22 (works)              149,600   316,863,206       2,118     296/frame (14%)          3,619
V.32                        4,480    90,430,024      20,185   19,986/frame (99%)           224
```

**The V.32 page's entire execution footprint is the kernel ISR plus the LEC
fragment `0x1d90..0x1dba`.** Nothing between `0x06dd` and `0x1d90`, and nothing
above `0x1dba` — 3,402 PCs that the working page reaches are never executed at
all, including the whole demodulator at `0x3dec..0x3f76` and `0x2963..0x2a78`.
It is not "running away in the LEC" (185) and not a bad tap count (186, which
was a real harness bug and is correctly fixed): it is a page whose echo
canceller consumes 100% of a 20,000-cycle allowance every frame, so nothing
after it in the frame ever runs. That is also why the media clock collapses —
the emulator cannot make real time when every sample burns the full budget.

The cost difference is in the iteration count, not the loop:

```text
             0x1d90 body/frame   0x1da6 calls/frame   taps per call
V.22                       1.8                  2.8            22.4
V.32                     312.3                312.3             2.0
```

The setup at `0x1d8e`/`0x1d8f` (`CNTR = DM($3754)`; `DO $1D9D`) and the epilogue
at `0x1d9e..0x1da5` **never execute inside the V.32 window** while the body
`0x1d90..0x1d9d` runs 1.4–20.6 M times, against 29,920 setups, 29,920 epilogues
and nine body iterations each on the V.22 control. That looked like a jump into
the middle of a loop body. **It is not — see the next section, which is the
answer and withdraws this framing.** Nothing enters `0x1d90` without the `DO`;
one entry simply lasts the whole window.

**A PM self-modification theory was raised and disproved in the same session,
and the disproof cost a new tool.** `PM 0x1d8e` was seen executing as `8f7545`
on the caller and `66e002` on the answerer, which looked like code being
overwritten. It is not:

* `EICON_PM_DUMP=0x1d00:0x1e00:...@0x0266` on both ends is **byte-identical**,
  and holds the correct `8f7545`.
* the new PM write watch fires **zero** times on `0x1d8e/0x1d8f/0x1da6`.
* the answerer never executes `0x1d8e` *while `0x0266` is resident* at all, so
  the `66e002` sighting was a different page occupying the same address.

That last point is the hazard the core's own coverage comment already warns
about — pages are swapped into the same PM by download, not selected by
`PMOVLAY` — and an exec watch is not gated by residency, so **an `[EXEC]` line
does not tell you which page you are looking at.** Read it with the overlay
timeline beside it.

### A watch that never watched anything

`adsp2181_watch_pm()` has existed since the core was imported and **nothing ever
read the flag it set**. Every "nothing writes that PM address" in this log
before now was an untested assumption. It fires in `WWORD_PGM()` now, on value
changes only, and `EICON_WATCH_PM=<addr>[,<addr>...]` arms it.

One caveat that matters for how far a negative result goes: the shim writes PM
through the raw pointer from `adsp2181_pm()`, so **overlay loads and any other
host write bypass this watch entirely**. It sees DSP stores and nothing else,
which is what "does the firmware modify its own code" needs and is not the same
question as "did this word change".

### V.34: the same change is a regression

Same rig, `EICON_CONTINUE_NON_IDLE=0x0261`, 45 s, against a control run
immediately before it:

```text
             deepest    transitions
control      caller 0x00b0 / answerer 0x00b0      49 / 40
CNI armed    caller 0x0060 / answerer 0x0090     150 / 186
```

The control reproduces Session 164 exactly — twenty states to `0x00b0`, then the
caller falls back to `0x0024`/`0x002c`. With the continuation forced the caller
never gets past `0x0060` and the answerer never past `0x0090`, and both cycle
three to four times as much: this is the pre-149 regime the handoff says is
gone. V.34 already has a per-sample discipline (`V34_PUBLISH_PACED` +
`yield_on_stop`), and injecting a second continuation on every sample on top of
it is not a generalisation of that mechanism but a competitor to it.

**So 187's "two of the three modulations fail for one reason" is withdrawn.**
V.32 wanted the continuation; V.34 does not. Whatever holds V.34 at `0x00b0` is
still unidentified.

### The control

V.22 with the flag armed for the same overlay `0x0266`: LAPM connected, CHAP,
IPCP up, 3/3 pings, `usernet in=3 out=3`. Note what this does and does not show
— the page reaches idle every frame (2,118 instructions against a 20,000
allowance), so the branch never fires and the flag is *inert* rather than
tolerated. That is the right control for "does arming it cost anything", not for
"is injection safe on a busy page". Suite 395, OK.

### Next

1. **Find what enters `0x1d90` without `0x1d8f`.** This is the whole V.32
   blocker now: the body cannot be bounded by a `DO` that never runs, and until
   the LEC returns, no code after it in the frame will ever execute. Exec-watch
   `0x1d90` unlimited on the answerer *with the overlay timeline beside it* and
   read `from=`; it was `0x1d9d` (the normal loop-back) and `0x1d99` in two
   different runs, and neither was residency-gated, so start by re-taking that
   measurement properly.
2. **Then ask whether the loop stack is being restored across the injected
   continuation in a way the hardware would not do.** The CNI save/restore
   copies `loop_stack`, `cntr_stack` and both stack pointers around the
   continuation every sample. That is intended, and V.22 does not exercise it
   (it idles), so the V.32 page is the only thing that has ever run it — which
   makes it unexamined rather than proven.
3. **Do not carry the non-idle continuation to V.34.** Keep it page-scoped; the
   `0x00b0` wall needs its own explanation.

```bash
# V.32: state machine walks to 0x0009, then stops
tools/eicon_loopback.py --native-mips --seconds 30 --ppp --ppp-auth chap \
    --ppp-ping peer --ppp-ping-count 3 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --caller-env EICON_CONTINUE_NON_IDLE=0x0266 \
    --answerer-env EICON_CONTINUE_NON_IDLE=0x0266 \
    --capture-dir artifacts/loopback-lowspeed/s188-v32-cni-a

# the histogram that shows 224 PCs against V.22's 3,619
tools/eicon_loopback.py --native-mips --seconds 14 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --caller-env EICON_CONTINUE_NON_IDLE=0x0266 \
    --answerer-env EICON_CONTINUE_NON_IDLE=0x0266 \
    --watch-exec 0x3536,0x3e4c,0x2400,0x2c55 \
    --pc-histogram --pc-histogram-from 0x0266 \
    --capture-dir artifacts/loopback-lowspeed/s188-v32-watch

# V.34 A/B -- the regression
tools/eicon_loopback.py --native-mips --seconds 45 \
    --capture-dir artifacts/loopback-lowspeed/s188-v34-ctl
tools/eicon_loopback.py --native-mips --seconds 45 \
    --caller-env EICON_CONTINUE_NON_IDLE=0x0261 \
    --answerer-env EICON_CONTINUE_NON_IDLE=0x0261 \
    --capture-dir artifacts/loopback-lowspeed/s188-v34-cni
```

```bash
# the PM write watch, and the load-time dump that disproved self-modification
tools/eicon_loopback.py --native-mips --seconds 14 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --caller-env EICON_CONTINUE_NON_IDLE=0x0266 \
    --answerer-env EICON_CONTINUE_NON_IDLE=0x0266 \
    --caller-env EICON_WATCH_PM=0x1d8e,0x1d8f,0x1da6 \
    --answerer-env EICON_WATCH_PM=0x1d8e,0x1d8f,0x1da6 \
    --capture-dir artifacts/loopback-lowspeed/s188-v32-pmwatch

# EICON_PM_DUMP=0x1d00:0x1e00:<path>@0x0266 on both ends; the two files diff clean
```

## Session 188b: nothing enters `0x1d90` without the `DO` — the LEC loop goes immortal when the 4-deep counter stack saturates

The question was "what enters `0x1d90` without the setup at `0x1d8f`". **Nothing
does.** The premise came from reading a histogram whose whole window was inside
a single loop entry, and the real mechanism is better than the guess.

### The measurement

The exec watch reported one prior PC and no loop state, which cannot tell a jump
into a loop body from the loop's own back-edge — the back-edge is
`pc = pc_stack_top()` and shows the last body instruction either way. It now
prints a 24-PC trail and the sequencer state (`loop`, `loop_condition`,
`loop_sp`, `cntr`, `cntr_valid`, PC-stack top, and the loop stack), the same way
the DM watch already did.

With that, one run carrying `--watch-dm-writes 0x3754`, `--watch-exec 0x1d8f`
and the histogram together — one call, one cycle axis, no cross-run inference:

```text
cyc 33,058,531   DM(0x3754) = 6            PM 0x1688, an earlier page
cyc ~61M..72.9M  0x1d8e/0x1d8f execute with cntr=0000, loop=ffff, lsp=0
                                           -- not this page; other overlays at
                                              the same addresses
[native-mips] loaded 0x0266                the LEC image lands
cyc 78,787,932   0x1d8f  CNTR = 3ff4       <- 16,372 iterations
cyc 78,834,891   DM(0x3754) = 9            PM 0x1c88, the page's own init
cyc 78,844,213   0x1d8f  CNTR = 0009       every later entry is bounded at 9
```

Eight executions of `0x1d8f` in the whole call, and **zero** of the epilogue
`0x1d9e`: the loop is entered eight times and never once exits.

### Why one entry never ends

`0x3754` is the tap count, and the freshly-loaded page image still holds the
shipped template `0xfff4` in it — masked to a 14-bit `CNTR`, `0x3ff4` = 16,372.
The firmware's own init writes 9 about 47,000 cycles later, so there is a window
in which the LEC is entered with the template still in place. At ~849 cycles an
iteration that one loop needs ~13.9 M cycles: **hundreds of 8 kHz frames**, so
the SPORT ISR lands inside it hundreds of times, each nesting its own loop and
counter state on top of the page's.

`CNTR_STACK_DEPTH` and `LOOP_STACK_DEPTH` are 4. `cntr_stack_push()` silently
drops the push once `cntr_sp == 4` (it just sets `COUNT_OVER`), and the LEC
subroutine writes `CNTR = MX1` twice per body iteration. Once the stack is full
that write destroys the outer count with nothing saved, and the matching expiry
pops a stale `cntr_stack[3]` back instead. Watched directly at `0x1d90`:

```text
cyc 78844281  cntr=0008 psp=10 csp=2 lsp=3     counting down normally
cyc 78844348  cntr=0007 psp=10 csp=2 lsp=3
...           cntr=0001 psp=10 csp=2 lsp=3     about to exit correctly
cyc 78875975  cntr=0009 psp=10 csp=4 lsp=4     <- both stacks saturate
cyc 78876039  cntr=0010 psp=10 csp=4 lsp=4
cyc 78876103  cntr=0010 psp=10 csp=4 lsp=4     frozen; never decrements again
```

`CNTR` sticks at `0x0010` for the rest of the call. **The loop is immortal**, so
the page runs its echo canceller and its ISR and nothing else — which is the
224-PCs-against-3,619 shape, not a page "running away" (185) and not an
unbounded filter (186, correctly withdrawn there for a different reason).

So the chain is: template tap count → one 16,372-iteration loop → the loop spans
frames → hundreds of ISRs nest inside it → 4-deep stacks saturate → pops return
stale counts → the loop can never expire. Only the first link is V.32-specific;
the rest would bite any page whose loop outlives a frame.

### The obvious fix is not a fix

Forcing a sane tap count, `EICON_FORCE_DM=0x3754=0x0009@0x0266` on both ends,
confirms the mechanism and is not usable:

```text
                   distinct PCs   0x1d8f setups   0x1d9e exits   body
default                     224               0              0   20,634,448
tap count forced          1,045              18             18          183
```

The loop enters and exits balanced, the stacks stop saturating, and the page
executes 4.7× more of itself. But the page then **falls back to DIAL at 4.64 s
without ever requesting the partial** — `bootpage 2 V.32 -> 11 AT offline,
overlay=0x0262`. `DM(0x3754)` is written twice by the firmware, 6 first and 9
later, and pinning it at 9 breaks whatever the 6 is for. So this is a
demonstration, not a repair, and the "both ends leave `0x0009`" it produces is a
fallback rather than progress.

### Next

1. **Stop the LEC being entered before the page seeds its own tap count**, which
   is the actual defect. The firmware writes 6 then 9 to `DM(0x3754)`; the loop
   is entered between the image landing and either write. Whether hardware has
   the same window and survives it is the question — it plausibly does not,
   because on hardware the page load and the init are not paced by this
   harness's page-request service.
2. **Consider whether saturating a 4-deep stack should be visible.** The core
   sets `COUNT_OVER`/`LOOP_OVER` and carries on silently, which is faithful to
   the hardware but meant this took three sessions to find. A one-shot warning
   when either flag is first set would have named it immediately, and is worth
   the two lines.
3. The V.34 `0x00b0` wall is untouched by any of this.

```bash
# the one run that puts the DM write, the loop entry and the histogram on one
# cycle axis -- do not compare these across runs, the variance is large
tools/eicon_loopback.py --native-mips --seconds 20 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --caller-env EICON_CONTINUE_NON_IDLE=0x0266 \
    --answerer-env EICON_CONTINUE_NON_IDLE=0x0266 \
    --watch-dm-writes 0x3754 --watch-exec 0x1d8f:400 \
    --pc-histogram --pc-histogram-from 0x0266 \
    --capture-dir artifacts/loopback-lowspeed/s188-v32-final

# the stack-saturation trace
    --watch-exec 0x1d90:60,0x1d8f:60,0x1d8e:60   # read csp/lsp, not just cntr

# the demonstration that is not a fix
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f,0x3754=0x0009@0x0266
```

## Session 188c: the stack-overflow warning, and it says the PC stack goes first

188b's second "next step" is done, and it immediately corrected part of 188b.

All four SSTAT overflow bits now warn once per stack per card, from
`warn_stack_over()` in `2100ops.inc`, wired into `pc_stack_push`,
`pc_stack_push_val`, `cntr_stack_push`, `stat_stack_push` and
`loop_stack_push`. All four rather than just the counter and loop stacks 188b
named: they are one defect class, and a warning that covered half of them would
make "no warning" mean nothing. It is not behind a flag — it can fire at most
four times in a card's life.

### It discriminates

```text
V.22, the working path, 30 s, LAPM + IPCP + 3/3 pings   0 warnings
V.32, 16 s                                              4 warnings
V.34, 45 s                                              1 warning
```

Silent on the path that works and loud on both that do not, which is the only
property that makes a warning worth having.

### And it moves the V.32 diagnosis

188b blamed the counter stack and implied the LEC filled it. The warning says
otherwise, and the order is the point:

```text
answerer, V.32
cyc 78,868,131  PC stack overflow      pc=2f58  pcsp=16 cntrsp=3 loopsp=4
cyc 78,868,173  loop stack overflow    pc=2e19  pcsp=15 cntrsp=4 loopsp=4
cyc 78,868,211  counter stack overflow pc=2e18  pcsp=14 cntrsp=4 loopsp=4
```

**The PC stack saturates first**, at `PM 0x2f58`, and the loop and counter
stacks follow within 80 cycles at `PM 0x2e18`/`0x2e19` — none of which is the
LEC at `0x1d9x`. So the LEC loop is the *victim*, not the cause: something
nesting 16 deep through `0x2e18..0x2f58` exhausts every stack, and the LEC's
outer count is destroyed as collateral. (**"Nesting through `0x2e18`" is the
wrong reading — 188d dumps the stack and there is no deep chain through that
address at all.** `0x2e18` is just where the second of two frames happens to be
standing when the loop and counter stacks give out.)

The caller in the same call overflows only the PC stack (`pc=210c`, `cntrsp=1
loopsp=2`), which is why the two ends fail differently.

### V.34 does it too

```text
answerer, V.34, no CNI
cyc 200,288,777  PC stack overflow  pc=2dc4  pcsp=16 cntrsp=1 loopsp=0
```

One warning, PC stack only, at `PM 0x2dc4`. **Whether this has anything to do
with the `0x00b0` wall is not established** — the cycle was not correlated to a
state transition and nothing here shows causation. It is a lead, recorded as
one, and it is the first time the V.34 page has been caught corrupting a stack
at all.

### Next

1. **Find what nests 16 deep through `0x2e18..0x2f58`.** The exec watch now
   prints a 24-PC trail; watch `0x2f58` and read it. This is upstream of
   everything 185–188b chased.
2. **Correlate the V.34 warning at `0x2dc4` with `TrnProgress`.** Cheap, and it
   either promotes a lead or kills it.
3. The tap-count window from 188b is still real, but it is now the second thing
   wrong on this page rather than the first.

```bash
# the discriminator: 0 on V.22, 4 on V.32, 1 on V.34
tools/eicon_loopback.py --native-mips --seconds 30 --ppp --ppp-auth chap \
    --ppp-ping peer --ppp-ping-count 3 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x0004@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x0004@0x025f \
    --capture-dir artifacts/loopback-lowspeed/s188-stackwarn-v22
tools/eicon_loopback.py --native-mips --seconds 45 \
    --capture-dir artifacts/loopback-lowspeed/s188-stackwarn-v34
grep '\[STACK\]' <capture-dir>/*.endpoint.log
```

## Session 188d: nothing nests 16 deep — it is two frames sharing one stack, and the LEC never gets off it

188c said "something nesting 16 deep through `0x2e18..0x2f58`". **There is no
such thing.** The warning now dumps the PC stack, the loop stack and the
instruction trail at the moment of saturation, and the PC stack at overflow *is*
the call chain, so the question answers itself:

```text
pc stack (oldest first): 0773 1d0e 1d19 1d90 1d96 │ 0773 1e7f 1d12 1d29 3541 3783 3822 382b 3b2a 2e09 2f55
                         └─── suspended LEC (5) ──┘ └──────── the next frame's own chain (11) ───────────┘
loop stack: end=1d9d,cond=14  end=1db5,cond=14 │ end=382e,cond=14  end=2f58,cond=14
            └──── the suspended LEC's (2) ─────┘ └──── the second frame's (2) ────┘
```

Read it as two halves:

* **Slots 0–4 are one interrupted LEC.** Kernel dispatch `0x0773` → `0x1d0e` →
  `0x1d19` → **`0x1d90`**, which is the outer `DO`'s loop-top pushed by the `DO`
  itself, → `0x1d96`, the return address for `CALL $1DA6` at `0x1d95`. This is
  the frame that never finished, frozen exactly where 188b left it.
* **Slot 5 is `0x0773` a second time.** The kernel's per-frame dispatch,
  re-entered while the first one is still on the stack.
* **Slots 6–15 are that second frame doing its ordinary work**, ten more deep.

So the depth is not one pathological chain, it is **5 + 11**, and 5 + 11 = 16
exactly. Nothing is recursing and nothing is leaking; two frames are simply
sharing a 16-deep stack because the first one never returned. The loop stack
tells the same story in miniature: two entries belong to the suspended LEC and
two to the live frame, and `LOOP_STACK_DEPTH` is 4.

That also explains the working control without needing a new measurement: the
second frame's chain is 11 deep on its own and fits in 16 with room to spare.
V.22 overflows nothing because its LEC finishes inside a frame and leaves no
suspended half behind.

### It is not the harness's continuation

The obvious suspect was `EICON_CONTINUE_NON_IDLE` — it injects a continuation on
a core that is deliberately *not* idle, which is exactly "start a second frame
on top of an unfinished one". **Wrong.** The same call with CNI off:

```text
CNI armed   PC/loop/counter overflow at cyc 78,868,131 / 78,868,173 / 78,868,211
CNI off     PC/loop/counter overflow at cyc 78,867,343 / 78,867,385 / 78,867,423
            identical pc stack, identical loop stack, ~800 cycles earlier
```

Byte-identical chains. The re-entry at slot 5 is the SPORT interrupt doing what
a SPORT interrupt does, not this harness injecting anything. CNI is exonerated
as the cause of the corruption — it runs on a page that is already broken this
way, which is a different charge and is still the reason it must stay page-scoped.

### So the causal chain closes on 188b

1. the page image's shipped template leaves `DM(0x3754) = 0xfff4` when the LEC
   is first entered, so the loop is set up for 16,372 iterations;
2. at ~849 cycles an iteration that cannot finish in one 8 kHz frame;
3. the next frame's dispatch stacks on top of the unfinished one — 5 + 11 = 16,
   and 2 + 2 = 4;
4. `pc_stack_push`, `loop_stack_push` and `cntr_stack_push` all start dropping;
5. the LEC subroutine's `CNTR = MX1` then destroys the outer count with nothing
   saved and the expiry pops a stale value, so the outer loop can never expire;
6. the page runs its echo canceller and its ISR forever — the 224-PC shape.

**Only step 1 is V.32's.** Steps 2–6 would follow for any page whose routine
outlives a frame, which is worth remembering the next time a page "runs away".

### Next

1. **Step 1 is the only thing worth fixing**, and it is narrow: stop the LEC
   being entered between the image landing and the firmware's own write of the
   tap count. 188b showed that pinning the value is not it (the firmware writes
   6 before 9 and pinning 9 drops the page to DIAL).
2. **The V.34 warning at `PM 0x2dc4` is still uncorrelated** and now more
   interesting: it is PC-stack-only with `loopsp=0`, so it is *not* this shape.
3. The trail line prints `(none -- arm any --watch-exec ...)` unless some watch
   has turned the history ring on, because the ring is only written then. Do not
   read an absent trail as an empty one.

```bash
# the stack dump that answers it, and the control that clears CNI
tools/eicon_loopback.py --native-mips --seconds 16 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --caller-env EICON_CONTINUE_NON_IDLE=0x0266 \
    --answerer-env EICON_CONTINUE_NON_IDLE=0x0266 \
    --watch-exec 0x2f58:1 \
    --capture-dir artifacts/loopback-lowspeed/s188-nest
# same without the two CONTINUE_NON_IDLE lines -> artifacts/.../s188-nest-noCNI
grep -A3 '\[STACK\].*overflow' <capture-dir>/answerer.endpoint.log
```

## Session 188e: serve the partial at the request, not at the end of the sample — V.32 reaches `TrnProgress 0x00d0`

The tap-count window is closed, and closing it took V.32 from a page stuck
forever in its echo canceller to **`TrnProgress 0x00d0` on both ends**, which
Sessions 87 and 93 record as a success state.

### Where the window actually was

188d left this as "stop the LEC being entered before the page seeds its tap
count". The measurement that located it is the ordering against the working
control, one cycle axis each:

```text
V.22 (works)   image lands -> init writes 9 in 4,556 cycles, LEC entered after:  CNTR=0009
V.32 (broken)  image lands -> bootpage 19 posted 29 cycles later
                           -> LEC entered 5,571 cycles after that:               CNTR=3ff4
                           -> partial served ~25,000 cycles later
```

V.22 loads the same image with the same placeholder and simply wins the race.
All of V.32's damage happens **inside the page-load resume run** at
`_frame_core`'s `adsp2181_call(DM(0x3143))` — not in a per-frame dispatch. The
page is resumed, posts the partial request, and then runs on for the rest of its
allowance straight into the LEC.

### The fix, and the two wrong versions of it first

Hardware's kernel completes the transfer inside the frame that asks for it
(Session 185). So stop the resume run at the request, serve, and let the page
continue. `EICON_PARTIAL_STOP=0` restores the old behaviour.

Two attempts failed first, and both failures are worth keeping:

* **Stopping in the per-frame path** (`adsp2181_modem_sample`) is the wrong
  site: `DM(0x3FB0)` is the bootpage register, so the stop fires on every
  ordinary page transition, and a frame cut short there loses its continuation.
  That alone moved the V.8 classifier off V.32 — one end chose V.22 and the
  other FSK. A first version also handed back a *full* fresh budget instead of
  the remainder, doubling the cycles of every page-changing frame.
* **Stopping on the bootpage write** is the wrong word. The page writes
  `0x3FB0=19` (PM 0x1f8c), then `0x3131=1` (PM 0x069a), then `0x3132=id`
  (PM 0x069b), so at the bootpage write the id still names the *previous*
  whole-page request. Stopping there served `0x0266` on top of itself — a
  19-block no-op that consumed the served-once guard so the real `0x0267` never
  arrived. Stop on `DM(0x3132)`, which is written last.

`_service_partial_overlay()` also now refuses a partial whose id equals the
resident page, for the same reason: that is never a partial, and it reloaded the
image and re-resumed the page for nothing.

### Result

```text
                       before 188e                    after
LEC entries            one at CNTR=3ff4, then 9       every entry CNTR=0009
stack overflows        PC + loop + counter            none, either end
partial 0x0267         served ~25,000 cycles late     served before the LEC runs
V.32 caller            stuck at 0x0009                0009 0025 0051 0055 00d0 at 7.20 s
V.32 answerer          stuck at 0x0009                0009 0004 0006 0022 0046 0052 0054 00d0 at 5.66 s
```

**It does not carry data yet, and the reason is already known.** The pump
attaches with the V.22bis width — `[v42] V.22bis synchronous data state: TX 4
bits/datagram, RX 4` — so LAPM never establishes and PPP reports `in=0 out=0`.
That is Session 184's own next step 2, untouched since: V.32bis at 14,400 is 6
bits per datagram at 2400 baud, and the width constant is chosen from the
modulation. **The physical layer is no longer the blocker on this page; the
datagram width is.**

### Regressions

```text
V.22   IPCP up, 3/3 pings, 0 stack warnings          unchanged
V.34   deepest 0x00b0, 49/40 transitions, 1 warning  identical to the control
```

V.34 matching the control transition-for-transition is the one that matters,
because this change is in a path every page load takes.

### Next

1. **Give the pump the V.32 datagram width.** One constant, and it is the only
   thing between this page and a data call.
2. `0x00d0` here is reached by *two emulated ends that share their bugs*. It is
   not interop evidence, and V.32 against the Courier has never been tried.
3. The V.34 `0x00b0` wall is untouched and its `PM 0x2dc4` stack warning is
   still uncorrelated.

```bash
tools/eicon_loopback.py --native-mips --seconds 45 --ppp --ppp-auth chap \
    --ppp-ping peer --ppp-ping-count 3 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --capture-dir artifacts/loopback-lowspeed/s188-v32-data
```

## Session 188f: the pump has a V.32 width now — and the width was never the blocker

188e ended by saying the datagram width was "one constant away" from a V.32 data
call, quoting Session 184. **The constant is fixed and it did not produce data**,
which retires that expectation rather than fulfilling it.

### The bug was real: page 1 and page 2 share an overlay

`_next_tx_words()` and `_rx_datagram_bits()` both selected the width with
`self.resident == V22_OVERLAY`, and `V22_OVERLAY` is `0x0266` — the
"V.22/V.32 LEC" image the classifier serves to **both** modulations (Session
184). So V.32 was handed V.22bis's 4-bit datagram, V.22bis's 2400 bit/s rate,
and even announced itself as `V.22bis` in the log. The overlay does not identify
the modulation; `DM(0x3FB0)` does. `_lec_page_datagram_bits()` now reads the
bootpage, and page 2 gets `V32_DATAGRAM_BITS`.

The rate is a *separate* constant per page and cannot be derived from the width
— the unit test caught this immediately, which is what it is for. **V.22bis is
600 baud with four bits a symbol (2400 bit/s); V.32/V.32bis are 2400 baud.**
Deriving `bps = width * 2400` published V.22bis at 9600.

The card publishes no rate word on page 2 that this harness can find:
`DM(0x3F61)`/`DM(0x3F62)` are the V.34 DATASTATE words and the V.32 page **never
writes them** — watched across a whole call, zero writes. So the width is set
rather than read, and `EICON_V32_DATAGRAM_BITS` makes it sweepable.

> **Withdrawn (Session 204).** The V.32 page does write `DM(0x3F62)`, and it
> carries the negotiated rate. Live against slmodemd the word read `0x11aa`,
> `0x11a9` and `0x01a8` as the peer moved 9600 → 7200 → 4800, decoding through
> the same `v34_rate()` speed-number table the V.34 path already uses; bit C is
> a trellis bit the guide marks "Only for V32bis", so the word identifies
> itself. `Rstatus_ch` bits D and B (SPEEDTX/SPEED) are the card's own statement
> that the speed is available to be read, and they were asserted in the traces
> this section was written from. Whatever the original watch did, it was not
> watching this. The width is derived rather than pinned as of `be91b26`, and
> the sweep below therefore measured six wrong widths against a peer whose rate
> it never knew.

### The sweep, which is the actual result

Every legal V.32/V.32bis width, 40 s loopback with PPP, both ends on page 2:

```text
width  bits/s   RX frames  bad FCS  SABME  IPCP  ping
  6    14400        0         0       0      0   in=0 out=0
  5    12000        0         0       0      0   in=0 out=0
  4     9600        0         0       0      0   in=0 out=0
  3     7200        0         0       0      0   in=0 out=0
  2     4800        0         0       0      0   in=0 out=0
```

**Not one received frame at any width** — not even a bad FCS, which is what a
wrong width would produce. A width error garbles frames; it does not remove
them. So the width was necessary and is nowhere near sufficient.

### What is actually missing

The data interface barely runs on this page. Over a 40 s call that reaches
`TrnProgress 0x00d0`, watching the interface words on the answerer:

```text
DM(0x3FAD) DI_control   5 writes   0x8000, 0x8000, 0xe000, 0xa000, 0x0000
DM(0x3FAE) RXD0         1 write
DM(0x3FAF) RXD1         1 write
```

Five. A 2400-baud link needs 2400 datagrams a second in each direction; the DSP
asks for a datagram and publishes a receive word a handful of times and then
stops. **The V.32 page reaches the data state and then does not run its data
interface**, and no framing parameter can fix that.

So the standing claim from Session 184 — "the pump attachment is
modulation-agnostic apart from the width constant" — is **withdrawn**. It is
modulation-agnostic apart from the width constant *and* whatever makes the page
service `DI_control` continuously, which V.22 does and V.32 does not.

### Regressions

```text
V.22   V.22bis, TX 4 bits, 2400 bit/s, IPCP up, 3/3 pings   unchanged
suite  398 tests, OK                                        was 395
```

Three new tests cover the page-2 width, the page-2 rate, and an unknown bootpage
on the shared overlay keeping the V.22 width, so a page nobody has characterised
cannot silently acquire a six-bit datagram. The existing V.22 tests now state
their bootpage instead of relying on it not mattering.

### Next

1. **Find why `DI_control` stops.** Compare the V.22 page, which services it
   continuously, against V.32 on the same image: what does page 1 do per frame
   that page 2 does not? This is the last thing between V.32 and data.
2. `EICON_V32_DATAGRAM_BITS` defaults to 6 (V.32bis 14,400) and is **not
   measured**. Once frames flow, the sweep above becomes meaningful and should
   be re-run to pick the width by FCS rather than by assumption.

```bash
for W in 6 5 4 3 2; do
  tools/eicon_loopback.py --native-mips --seconds 40 --ppp --ppp-auth chap \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --caller-env EICON_V32_DATAGRAM_BITS=$W \
    --answerer-env EICON_V32_DATAGRAM_BITS=$W \
    --capture-dir artifacts/loopback-lowspeed/s188-w$W
done
# the interface probe
    --watch-dm-writes 0x3FAD,0x3FAE,0x3FAF
```

## Session 188g: `DI_control` does not stop on page 2 — it never starts, and the page abandons

The question was why the data interface stops. It does not stop. **Page 2 has no
per-datagram data-interface loop at all**, and the five writes are one
initialisation burst before the page gives up.

### The two pages use different code

`--watch-dm-writes 0x3FAD` over a 40 s call, both modulations, answerer:

```text
V.22   167,538 writes   from PM 0x3fcb, 0x3fe1, 0x3ff1, 0x3fdb
V.32         5 writes   from PM 0x34d1, 0x34dc, 0x34e6, 0x34ec, 0x34f9
```

Two disjoint routines. V.22's is at `PM 0x3fc8..0x3ff2` and is the per-datagram
servicer: `0x3fc8..0x3fde` publishes a receive word (clear bits 13/14, compare
the producer `DM(0x06EB)` against the consumer `DM(0x0337)`, `IF EQ RTS` when
there is nothing, else set `rx0_valid` and fill `DM(0x3FAE)`), and
`0x3fdf..0x3ff2` raises `tx_request`. It runs 67,824 times a call.

**On page 2 `PM 0x3fc8` is never entered.** An exec watch on it fires only
around cyc 33 M, which is the V.8 era before `0x0266` is even resident.

### The five writes are one burst, and then the page quits

Bootpage and `DI_control` on one cycle axis:

```text
cyc 78,780,553  bootpage = 2   (V.32)                PM 0x3762
cyc 78,782,361  bootpage = 19  (partial requested)   PM 0x1f8d
cyc 78,787,789  bootpage = 2   (partial served)      PM 0x1deb
cyc 78,830,857  DI_control = 0x0000                  PM 0x34ed
cyc 78,830,870  DI_control = 0x8000  tx_request      PM 0x34fa
cyc 78,830,876  DI_control = 0x8000                  PM 0x34d2
cyc 78,830,887  DI_control = 0xa000  +rx0_valid      PM 0x34dd
cyc 78,830,897  DI_control = 0xe000  +rx1_valid      PM 0x34e7
cyc 78,837,132  bootpage = 0   (DIAL)                PM 0x36bc
cyc 78,839,025  bootpage = 11  (AT offline)          PM 0x1dbd
```

All five land **within 40 cycles of each other** — a single pass through
`0x34d1..0x34f9`, which is data-interface *setup*, not servicing. Then 6,235
cycles later the page writes bootpage 0 from **`PM 0x36bc`** and falls back to
DIAL.

So the sequence is: page 2 loads, takes its partial, initialises the data
interface once, and abandons the connection about a third of a frame later. No
framing parameter, and no datagram width, was ever going to matter — 188f's
sweep was measuring a link that had already been given up on.

**`PM 0x36bc` is the abandon path and is the thing to chase.**

### The caveat, and it is a real one

This run and its probe both ended at `TrnProgress 0x0009 -> 0x0000` at 2.94 s
and never reached `0x00d0`, where 188e's runs of the same rig walked to `0x00d0`
on both ends. So **the burst-then-abandon above is characterised on a call that
failed early**, and whether a call that reaches `0x00d0` abandons at the same
`PM 0x36bc` is *not* established. Both readings are consistent with "page 2
never services the interface", because 188f's `0x00d0` run also produced only
five writes over 40 s — but the abandon site is evidenced only in the early
failure.

Run-to-run variance on this page is large and has now cost time twice in this
session. **Do not compare V.32 measurements across runs**; put everything on one
cycle axis in one call, as here.

### Next

1. **Disassemble `PM 0x36bc` and find its condition.** What makes page 2 decide
   to write bootpage 0? That is the blocker now, ahead of anything in the data
   path.
2. **Establish whether a `0x00d0` call abandons the same way**, by re-running
   the 188e rig with `--watch-dm-writes 0x3FAD,0x3FB0` until one reaches
   `0x00d0`. Same watches, same axis.
3. `PM 0x34d1..0x34f9` is page 2's data-interface setup. If the page is ever
   made to stay, the question becomes what it expects to drive the interface
   afterwards, since it has no equivalent of `0x3fc8..0x3ff2`.

```bash
tools/eicon_loopback.py --native-mips --seconds 40 --ppp --ppp-auth chap \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --watch-dm-writes 0x3FAD,0x3FB0 \
    --capture-dir artifacts/loopback-lowspeed/s188g-clean
# the V.22 control, same watch: 167,538 writes from 0x3fcb/0x3fe1/0x3ff1/0x3fdb
    --caller-env EICON_FORCE_DM=0x3FC4=0x0004@0x025f
```

## Session 188h: the condition is `DM(0x0571) != 0`, tested at the top of every V.32 frame — and the partial *does* carry PM

`PM 0x36bc` is not the decision. It is the second instruction of a five-word
routine that has no condition in it at all. The decision is one branch earlier,
in the per-frame handler, and finding it required correcting Session 185.

### First: every page-2 disassembly taken so far has been of the wrong image

Session 185 recorded that the V.32 partial `0x0267` is "**seven DM blocks and no
PM at all** ... It is pure data". **That is wrong**, and it invalidated two
attempts at this before it was caught. Ground truth is the `op=` field of an
`[EXEC]` line, which is the word actually fetched:

```text
PM 0x36bb executed at the abandon            op=93fb0a
EICON_PM_DUMP=...@0x0266  (page load)           804dd0   MISMATCH
EICON_PM_DUMP=...@0x0267  (after the partial)   93fb0a   MATCH
```

So `0x0267` rewrites program memory, and **a dump gated on `0x0266` is the
pre-partial image**. `93fb0a` is `DM($3FB0) = AR`; `804dd0` decodes as a read of
`DM(0x04DD)` and is simply a different page's instruction at that address. Any
page-2 disassembly in this log taken at `0x0266`-load time should be re-taken
with `@0x0267`.

### The routine, and the branch that reaches it

```text
36b7  AX0 = DM($3FC1)
36b8  AR  = AX0 OR $0100      ; the page-request "ready" flag the host polls
36b9  DM($3FC1) = AR
36ba  AR  = $0000
36bb  DM($3FB0) = AR          ; bootpage 0 = DIAL
36bc  RTS
```

Unconditional: set page-ready, ask for DIAL, return. The condition is in the
per-frame handler `PM 0x3536` — the word the partial installs into `DM(0x3fb8)`
(Session 187):

```text
3536  CALL $3528          ; init: clear L0..L7, set M0..M6, MODE_CTL(2e80)
3537  DM($3FB5) = M0
3538  AX0 = DM($0571)     ; <-- the condition
3539  AR  = AX0 + 0
353a  IF NE JUMP $36B7    ; <-- non-zero: request DIAL and do nothing else
353b  CALL $34C4          ; zero: the frame's real work, including the data
353c  CALL $36BF          ;      interface at 0x34d1..0x34f9 that 188g saw
353d  CALL $2B76
353e  CALL $2C69
353f  CALL $2CB5
3540  CALL $376B
3541  AR = DM($3FC1) ; AR = AR OR $0400 ; DM($3FC1) = AR
```

**`DM(0x0571)` is tested at the top of every V.32 frame, and any non-zero value
makes the page skip its entire frame and ask for DIAL.** That is 188g's five
`DI_control` writes explained exactly: `CALL $34C4` is reached only while
`DM(0x0571)` is zero, so the interface is initialised once and never serviced
again.

### What sets it, and when

`--watch-dm-writes 0x0571,0x3FB0`, one cycle axis:

```text
cyc 78,780,553  bootpage = 2      V.32 selected
cyc 78,782,361  bootpage = 19     partial requested
cyc 78,787,789  bootpage = 2      partial served, page resumed
cyc 78,801,594  DM(0x0571) = 0    PM 0x3b89
cyc 78,801,882  DM(0x0571) = 0    PM 0x241e
cyc 78,820,636  DM(0x0571) = 0x18f3   PM 0x2cfb     <-- set non-zero
cyc 78,837,168  bootpage = 0      PM 0x36bb         <-- 16,532 cycles later
```

The page zeroes it twice on entry and then **`PM 0x2cfb` writes `0x18f3`**,
after which the next frame abandons. Across the whole call the same address is
written 134 times with a wide spread of values (`0x0de0`, `0xee40`, `0xf100`,
`0xff20` …) from `PM 0x3a95`, which is a signal-domain quantity on an *earlier*
page — the pages reuse DM addresses, so do not read those as V.32 status.

### Next

1. **`PM 0x2cfb` and the value `0x18f3`.** Disassemble it **with an `@0x0267`
   dump**, and find what it is testing. That is the whole blocker: the page is
   not failing to run its data interface, it is being told to give up.
2. `DM(0x0571)` is worth a read watch too — the handler reads it every frame, so
   a read watch will be loud, but `--watch-dm-writes` on `0x0571` plus an exec
   watch on `0x2cfb` gives the setter's registers without the noise.
3. `DM(0x3FC1)` bit 8 is the page-request ready flag (`page_ready` in the shim)
   and bit 10 is set at the end of a *successful* frame (`0x3541..0x3543`), so
   the two bits distinguish "asked for a page" from "completed a frame".

```bash
tools/eicon_loopback.py --native-mips --seconds 20 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --watch-dm-writes 0x0571,0x3FB0 \
    --capture-dir artifacts/loopback-lowspeed/s188h6
# and the only trustworthy disassembly source for page 2:
EICON_PM_DUMP=0x3510:0x3560:/tmp/pm.csv@0x0267
```

## Session 188i: `0x18f3` is not a status code — it is a record field scattered into a parameter block from DM the page never loads

`DM(0x0571)` is not a dedicated abort word, and `0x18f3` is not a failure code.
Both are artefacts of a sparse-record unpacker writing into a parameter block,
from a source the V.32 page image does not initialise.

### The writer is a loop, and `0x0571` is one of its destinations

The storing instruction is `PM 0x2cfa` (`ppc`, not `pc`), and the trail shows a
tight loop `0x2cf1..0x2cfb` rather than a one-off store. Disassembled from an
`@0x0267` dump — the only correct image for page 2 (188h):

```text
2cee  I4 = AX0                          ; source record pointer
2cef  AY0 = $FF00
2cf0  MR0 = $054C                       ; destination base
2cf1  AR = DM(I4,M5)                    ; next source word
2cf2  SR = LSHIFT AR (LO) BY -8         ; SR0 = word >> 8
2cf3  AF = SR0 + 0, AR = DM(I4,M5)      ; AF = the offset
2cf4  AR = AR AND AY0, SR1 = DM(I4,M5)
2cf5  SR = LSHIFT SR1 (HI) BY -8
2cf6  SR = LSHIFT SR1 (HI) BY 8
2cf7  SR = LSHIFT AR (HI, OR) BY -8     ; SR1 = the assembled value
2cf8  AR = MR0 + AF                     ; 0x054C + offset
2cf9  I0 = AR
2cfa  DM(I0,M1) = SR1, AR = MR1 XOR AF  ; store; compare offset against MR1
2cfb  IF NE JUMP $2CF1                  ; loop until offset == MR1
```

So it walks `(offset, value)` pairs and scatters them across a block based at
`DM(0x054C)`, stopping when the offset equals the terminator in `MR1`. At the
write that matters:

```text
MR0 = 054c   AF = 0025   -> I0 = 0x0571      SR1 = 18f3   MR1 = 001f
```

`DM(0x0571)` is therefore **`DM(0x054C + 0x25)` — field 0x25 of a parameter
block** — and `0x18f3` is that field's value out of the record. Session 188h's
"`PM 0x2cfb` writes `0x18f3`" was reading the loop's last instruction as if it
were a purpose-built store; it is neither purpose-built nor at that address.

### The record is read from DM nothing loads

`I4 = 0x1ae0` at the store. Against `0x0266`'s own nineteen DM blocks (printed
by the partial service):

```text
0x17f1(188)  -> 0x17f1..0x18ac
0x1900(74)   -> 0x1900..0x1949
0x194b(215)  -> 0x194b..0x1a21
0x1b00(1280) -> 0x1b00..0x1fff
                0x1a22..0x1aff is a 222-word gap, and I4 is inside it
```

`0x0267` adds `0x0485(156)` and `0x0680(16)` and covers none of it either. And a
write watch on `0x1ad8`, `0x1ae0`, `0x1ae8` fires **zero times in the whole
call**. So the source record is neither loaded by this page nor written during
it: it is whatever an earlier overlay left at those addresses.

That also explains the terminator. The loop ends only when a source offset byte
happens to equal `MR1 = 0x1f`; walking uninitialised memory it will scatter an
arbitrary number of arbitrary values across `0x054C + offset` before it stops.
`0x0571` getting `0x18f3` is one of those, and the frame handler at `PM 0x3538`
reads that same word every frame and abandons on any non-zero (188h).

**So the V.32 page is not deciding to give up. It is reading a parameter block
that a stale-record unpack has scribbled on.**

### Caveat

"Stale" is inferred from two facts — no DM block of `0x0266`/`0x0267` covers
`0x1a22..0x1aff`, and nothing writes there during the call — not from watching
the region get its contents from some earlier page. It is possible the page
expects a companion download that this harness never stages, which is Session
134's V.90A situation again and would be a much better outcome than corruption.
**Establish which it is before designing a fix.**

### Next

1. **Find where `AX0` comes from at `PM 0x2cee`**, i.e. who chooses the record
   pointer. That names the table, and the table names whether a download is
   missing or a pointer is wrong.
2. **Dump `DM(0x1a22..0x1aff)` at the moment of the unpack** and see whether it
   looks like a record (plausible offset bytes, terminator `0x1f` present) or
   like leftover signal data. That answers the caveat directly.
3. If it is a missing download, `EICON_DSP_EXTRA_DOWNLOADS` is the lever, as it
   was for V.90A in Session 134.

```bash
tools/eicon_loopback.py --native-mips --seconds 20 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_PM_DUMP=0x2cc0:0x2d20:/tmp/pm2c.csv@0x0267 \
    --watch-dm-writes 0x1ad8,0x1ae0,0x1ae8,0x0571 \
    --capture-dir artifacts/loopback-lowspeed/s188i2
```

## Session 188j: the "record" is a cosine table — the pointer is wrong, and nothing is missing or corrupt

188i left one question: is `DM(0x1a22..0x1aff)` a record this harness fails to
stage, or an earlier page's leftovers? **It is neither.** It is a live
trigonometric table belonging to the signal path, and the unpacker at
`PM 0x2cee` is simply pointed at it.

### A DM dump, because there was not one

`EICON_PM_DUMP` had no data-memory twin, and the only DM snapshot in the harness
is the end-of-call capture — taken after the page has fallen back and the next
overlay has loaded over the evidence. `EICON_DM_DUMP=LO:HI:PATH@OVERLAY` now
mirrors it, dumping when that overlay becomes resident.

### What is actually there

`@0x0267`, so the image is complete and the unpack has not yet run:

```text
1ac0: 4600 0040 0010 7fff ff62 7f61 fe28 7d89
1ac8: fcf3 7a7c fbc5 7641 faa1 70e2 f98b 6a6d
1ad0: f884 62f1 f791 5a82 f6b1 5133 f5e9 471c
1ad8: f53a 3c56 f4a5 30fb f42c 2528 f3d1 18f9
1ae0: f393 0c8c f374 0000 f374 f374 f393 e707
```

Every other word, read as Q15 from `0x1ac3`:

```text
+1.0000 +0.9951 +0.9807 +0.9569 +0.9239 +0.8819 +0.8315 +0.7730
+0.7071 +0.6344 +0.5555 +0.4714 +0.3827 +0.2903 +0.1951 +0.0980 +0.0000
```

That is cos(kπ/32) to four decimals — **a 32-point quarter-cosine table**,
continuing past `0x0000` into `e707 dad8 cf05 …` for the negative quadrant. The
interleaved column is a second waveform. This memory is fully populated,
perfectly regular, and obviously in use by the modulator or demodulator.

### The arithmetic closes exactly

`I4 = 0x1ae0` at the store, after three post-increment reads, so the sources
were `0x1add..0x1adf`:

```text
DM(0x1add)=2528  DM(0x1ade)=f3d1  DM(0x1adf)=18f9

offset = 0x2528 >> 8                  = 0x25    observed AF  = 0x0025
value  = hi(0x18f9)<<8 | hi(0xf3d1)   = 0x18f3  observed SR1 = 0x18f3
dest   = 0x054C + 0x25                = 0x0571  observed I0  = 0x0571
```

So `0x18f3` is two high bytes of adjacent cosine coefficients glued together,
and `0x25` is the high byte of a third. There is no record, no status, and no
code. **The unpacker is reading the trig table as if it were a configuration
record**, scattering coefficient bytes across the parameter block at
`DM(0x054C)` until an offset byte happens to equal the terminator `0x1f`.

### What this rules out

188i's caveat is settled, and both alternatives are dead:

* **Not a missing download.** The memory is not empty or uninitialised; it holds
  a complete, structured table. Session 134's V.90A situation does not apply and
  `EICON_DSP_EXTRA_DOWNLOADS` is not the lever here.
* **Not corruption.** The table is numerically perfect — a corrupted table would
  not produce cos to four decimals across seventeen points.

What is left is the pointer: **`AX0` at `PM 0x2cee` is wrong**, and it is the
only thing wrong. Everything downstream — the scattered parameter block, the
non-zero `DM(0x0571)`, the per-frame abandon at `PM 0x353a`, the five
`DI_control` writes, the fallback to DIAL — follows from that one value.

### Next

1. **Find who loads `AX0` before `PM 0x2cee`.** That is now the entire V.32
   blocker, and it is one register. Exec-watch `0x2cee` and read `ax0`, then
   walk back through the trail the watch prints.
2. Whatever selects it is likely a table lookup, and this project has been here
   before: Session 115's `CALL (I7)` entered a scan at `0x2e1c` instead of
   `0x2e1a` because a dispatch table at `DM(0x00A8..0x00A9)` had been overwritten.
   `0x2e18`/`0x2e19` also turned up in 188c's stack saturation. Check whether the
   record pointer comes through the same read-database machinery.

```bash
tools/eicon_loopback.py --native-mips --seconds 20 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_DM_DUMP=0x1a10:0x1b10:/tmp/dm1a.csv@0x0267 \
    --capture-dir artifacts/loopback-lowspeed/s188j
```

## Session 188k: `AX0` comes from `DM(0x05B7)`, and `PM 0x28c0` puts a terminator-`0x1A` database into the terminator-`0x1F` slot

188j's "`AX0` at `PM 0x2cee` is wrong" is confirmed and now sourced. The value is
not corrupted, not stale and not a mis-stepped dispatch: it is written by a
well-formed firmware handler that assigns the wrong one of two record
databases.

### The register trail

Exec-watch on `0x2cee` fires exactly twice on the answerer in a whole call, both
inside the resident `0x0266`/`0x0267` page, and the run is bit-identical across
repeats:

```text
cyc 78,810,706  pc=2cee from=2cbb  ax0=1174  mr1=001a   <- DM(0x05B8) stream
cyc 78,810,900  pc=2cee from=2933  ax0=1081  mr1=001f   <- DM(0x05B7) stream
```

Disassembled from an `@0x0267` dump, the two callers are the same routine
entered at two points, and `0x2CEE` is a shared subroutine, not page-2 code:

```text
2cb5  AX0 = DM($05B4)          ; pending flag for the 0x1A stream
2cb8  DM($05B4) = M0           ; clear it
2cb9  AX0 = DM($05B8)          ; <- record cursor
2cba  MR1 = $001A              ; <- terminator
2cbb  CALL $2CEE
2cbc  DM($05B8) = AR           ; save the advanced cursor back

292d  AX0 = DM($05B6)          ; pending flag for the 0x1F stream
2930  DM($05B6) = M0
2931  AX0 = DM($05B7)          ; <- record cursor       ** this is the AX0 **
2932  MR1 = $001F              ; <- terminator
2933  CALL $2CEE
2934  DM($05B7) = AR
```

`PM 0x2CFC` is `AR = I4`, so the unpacker returns its source pointer and the
caller stores it back: these are *resumable stream cursors*, one record consumed
per call, terminated when a record's offset byte equals `MR1`.

### Both cursors are installed at one place, from `MR0` and `MR1`

```text
2cb0  DM($05B7) = MR1          ; the 0x1F stream
2cb1  DM($05B6) = M1
2cb2  DM($05B8) = MR0          ; the 0x1A stream
2cb3  DM($05B4) = M1
2cb4  RTS
```

Eleven sites jump to `0x2CB0`. An exec watch on it shows two installs in the
call, and the second wins:

```text
cyc 78,801,956  pc=2cb0 from=2c68  mr0=1060 mr1=0fca
cyc 78,810,657  pc=2cb0 from=28c1  mr0=1174 mr1=1081   <- 8,701 cycles later
```

So the page installs `DM(0x05B7) = 0x0FCA` correctly and then overwrites it with
`0x1081`.

### The overwrite is a dispatch-table handler, and the table is *not* misaligned

`ret=382b`, `i4=ax0=sr1=0x28bf`: `PM 0x382A` is `CALL (I4)` inside a
walking-bit handler dispatcher at `0x3821..0x382E`, which fetches a handler
address per set bit of a changed-bits mask. Three handlers ran from that mask:

```text
cyc 78,810,626  bit 0x0004 (2)  -> 2c72
cyc 78,810,653  bit 0x0080 (7)  -> 28bf
cyc 78,810,680  bit 0x0400 (10) -> 3888
```

The table holding them is in data memory at base `0x0AE6`:

```text
0ae6: 2c6e 2c70 2c72 2c74 2c76 2c76 2c76 28bf 3839 2c9c 3888 ...
       +0   +1   +2   +3   +4   +5   +6   +7   +8   +9   +10
```

Bit 2 → `0x0AE8` = `2c72`, bit 7 → `0x0AED` = `28bf`, bit 10 → `0x0AF0` =
`3888`. All three line up on one base, so **this is not Session 115's
overwritten-dispatch-table shape**: the lookup is exact and `0x28BF` really is
bit 7's handler. The lead was the right machinery and the wrong table —
`CALL (I4)` at `0x382A` through `DM(0x0AE6..)`, not `CALL (I7)` at `0x2E1A`
through `DM(0x00A8..0x00A9)`.

### `PM 0x28BF` is the only install site in the image that does not set `MR1 = $0FCA`

Every arm that reaches `0x2CB0`, disassembled from the same dump:

```text
28bf  MR0 = $1174   MR1 = $1081   JUMP $2CB0     <-- the odd one out
2c66  MR0 = $1060   MR1 = $0FCA   JUMP $2CB0
2c76  MR0 = $0F70   MR1 = $0FCA   JUMP $2CB0
2c84  MR0 = $1081                 JUMP $2CB0     (MR1 = $0FCA at 2c79)
2c86  MR0 = $1720                 JUMP $2CB0
2c88  MR0 = $16AB                 JUMP $2CB0
2c91  MR0 = $1081                 JUMP $2CB0     (MR1 = $0FCA at 2c79/2c97)
2c93  MR0 = $16BA                 JUMP $2CB0
2c95  MR0 = $1645                 JUMP $2CB0
2ca8  MR0 = $1081   MR1 = $0FCA   JUMP $2CB0
2cad  MR0 = $0F70   MR1 = $0FCA   JUMP $2CB0
```

`MR1` is the constant `0x0FCA` at ten of the eleven. `0x1081` appears three
times — always as `MR0`. `PM 0x28C0` is the sole place in the image that puts
`0x1081` in `MR1`.

### The two databases are structurally distinct, and the swap cannot terminate

Walking every install constant over a live `@0x0267` DM dump, each against the
terminator its slot is walked with:

```text
DM(0x05B8), terminator 0x1A       DM(0x05B7), terminator 0x1F
  0x1060  terminates,  5 records    0x0FCA  terminates,   3 records
  0x0F70  terminates, 18 records    0x1081  NEVER TERMINATES
  0x1081  terminates, 21 records            (895 records and still running
  0x1720  terminates, 19 records             at 0x1AFE, the dump's end)
  0x16AB  terminates, 20 records
  0x16BA  terminates, 15 records
  0x1645  terminates,  5 records
  0x1174  terminates,  7 records
```

The `0x1A` database holds records over fields `0x00..0x1A`; the `0x1F` database
holds records over fields `0x1D..0x1F` and `0x0FCA` is three records long. They
are different tables with different field ranges, and the terminator is what
tells them apart. `0x1081` is an `0x1A`-family base — walked with `0x1F` its
offset byte never matches, so the unpacker runs 885 records from `0x1081`
straight into the quarter-cosine table at `0x1ADD` (188j), where offset `0x25`
and value `0x18f3` land on `DM(0x054C+0x25) = DM(0x0571)` and the frame handler
at `PM 0x3538` abandons every frame.

### What this settles, and what it does not

**Settled.** `AX0` at `PM 0x2cee` is `DM(0x05B7)`; `DM(0x05B7)` is installed at
`PM 0x2CB0` from `MR1`; the correct value `0x0FCA` *is* installed at
cyc 78,801,956 and is then overwritten by `PM 0x28C0`'s `MR1 = $1081`, reached
through an exactly-aligned handler table. Nothing here is stale, corrupt or
mis-stepped.

**Not settled.** `PM 0x28BF` is well-formed firmware doing its declared job, so
the defect is one step further up, and there are two readings left:

1. **Bit 7 should not be set** in the changed-bits mask at this point in a V.32
   call, so `0x28BF` should never run. The mask is built at `PM 0x378B..0x378E`
   from `DM(0x0550) XOR DM(0x0644)` — a mode word against its shadow — so this
   is a question about who writes `DM(0x0550)`.
2. **`0x28BF` is bit 7's handler for a different page**, and the table at
   `DM(0x0AE6..)` belongs to an overlay whose bit 7 means something else. The
   table is in DM, and 188i already established that this page loads very little
   of low DM.

Do not read this as "the firmware has a swapped-constant bug" without settling
which. A shipping V.32 implementation that never trains is the less likely of
the two.

### Next

1. **Find who writes `DM(0x0550)` and when bit 7 goes in.** A write watch on
   `0x0550` plus an exec watch on `0x378E` gives the setter and the mask in one
   run. That discriminates reading 1 from reading 2 directly.
2. **Check whether `DM(0x0AE6..0x0AFF)` is downloaded by `0x0266`/`0x0267`.** The
   partial service prints each page's DM blocks; if no block covers `0x0AE6` the
   table is an earlier overlay's and reading 2 is live.
3. The cheap A/B, once either is answered: force `DM(0x05B7) = 0x0FCA` after the
   install and see whether the per-frame abandon stops. `EICON_FORCE_DM` writes
   at overlay-load time, which is too early here, so this needs a write hook
   rather than the existing lever.

```bash
tools/eicon_loopback.py --native-mips --seconds 20 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --watch-exec 0x2cee:40,0x2cb0:20,0x382a:12 \
    --watch-dm-writes 0x0550,0x05b7,0x05b8 \
    --capture-dir artifacts/loopback-lowspeed/s188k
# the disassembly source, and the DM twin that made the walk above possible:
EICON_PM_DUMP=0x2000:0x4000:/tmp/pm.csv@0x0267
EICON_DM_DUMP=0x0f00:0x1b00:/tmp/dm.csv@0x0267
```

## Session 188l: the handler table is swapped by self-modifying code — bit 7 should reach `PM 0x2C79`, not `PM 0x28BF`

188k left two readings. Both questions it posed are now answered, and the second
one closes the chain: **`PM 0x3805` is rewritten at run time**, and the rewrite
is what sends bit 7 to the wrong handler.

### `DM(0x0AE6)` *is* downloaded, so reading 2 is dead

`0x0266`'s DM block `0x0780..0x0f62` covers it. The live sixteen words at
`0x0AE6..0x0AF5` are identical to the shipped block, 0 of 16 differing, and all
nine install constants (`0x0FCA`, `0x0F70`, `0x1060`, `0x1081`, `0x1174`,
`0x1645`, `0x16AB`, `0x16BA`, `0x1720`) are inside `0x0266`'s own DM blocks too.
The handler table and both record databases are the page's own. 188k's
"the table belongs to a different overlay" is disproved.

### The write watch on `DM(0x0550)`: the runaway feeds itself, but it is not the trigger

`DM(0x0550)` takes 178 writes in a call: 130 from `PM 0x3a95` and one from
`PM 0x36c9` (an earlier page's signal domain — the same decoy this log already
records for `DM(0x0571)`), one page-init zero from `PM 0x3b89`, and **46 from
`PM 0x2CFA`, the runaway unpacker itself** (field `0x04`, `0x054C + 4`). It hits
`DM(0x0644)`, the shadow `DM(0x0550)` is compared against, twice more
(field `0xF8`). So the runaway scribbles on both halves of a mode comparison and
would keep re-triggering handler dispatches.

**But it is not what started this one.** The ordering is unambiguous:

```text
cyc 78,801,681  DM(0x05C8) = 0        PM 0x3b89   page-init zero
cyc 78,801,924  PM 0x3805 rewritten   PM 0x290d   <-- self-modification
cyc 78,801,956  DM(0x05B7) = 0x0FCA   PM 0x2cb0   the correct install
cyc 78,801,962  DM(0x05C8) = 0x0484   PM 0x2425
cyc 78,810,611  dispatcher entered    PM 0x3821   mask 0x0484, table 0x0AE6
cyc 78,810,653  bit 7 -> PM 0x28bf                DM(0x05B7) = 0x1081
cyc 78,810,900  runaway unpack begins PM 0x2cee
cyc 78,810,956  first of 46 writes to DM(0x0550)  PM 0x2cfa
```

The dispatch precedes the runaway by 289 cycles.

### The mask is GEN_SETUP1

The dispatcher's `ret=353f` names `PM 0x353E: CALL $2C69`, and the trail is
`353e 2c69 2c6a 2c6b 2c6c 2c6d 3804 3805 3806 3807 3808 381c … 3821`:

```text
2c69  AX1 = DM($05C8)          ; 0x0484
2c6a  AY1 = DM($0647)          ; 0x0000
2c6b  AR = AX1 XOR AY1         ; 0x0484 -- the mask
2c6c  IF EQ RTS
2c6d  JUMP $3804
3804  DM($0647) = AX1          ; update the shadow
3805  I4 = $0AE6               ; table base
3806  CNTR = $000D
```

`DM(0x05C8) = 0x0484` is written at cyc 78,801,962 by `PM 0x2425` — that is
**GEN_SETUP1**, the modulation-role word (`GEN_SETUP1_ROLE = {"answer": 0x0484,
"calling": 0x048C}`). `0x0484` is bits 2, 7 and 10, which is exactly the three
handlers observed. Bit 7 is set in *both* roles, so nothing here is
answer-specific and nothing is corrupt: the mask is the role word doing its job.

### `PM 0x3805` is self-modified, and that is what redirects bit 7

The shipped `0x0267` word at `PM 0x3805` is `38ab00` = `I4 = $0AB0`. The word
that executes is `38ae60` = `I4 = $0AE6`. Three independent measurements agree:

```text
[WATCH] pm w 3805=38ae60 was=38ab00 ppc=290d pc=290e cyc=78801924
[EXEC]  pc=3805 op=38ae60 cyc=78810602
[EXEC]  pc=3821 i4=0ae6 cntr=000d ret=353f cyc=78810611
```

The writer is a five-instruction quine at `PM 0x2909`, which copies **its own
opcode word** over `PM 0x3805` (`AX0 = PM(I6,M4)` loads the upper 16 bits and
latches the low 8 in `PX`, and `PM(I7,M4) = AX0` writes both back, so the
24-bit word is reproduced exactly):

```text
2909  I4 = $0AE6              ; the word that gets copied
290a  I7 = $3805              ; destination: the instruction at 0x3805
290b  I6 = $2909              ; source: this instruction
290c  AX0 = PM(I6,M4)
290d  PM(I7,M4) = AX0
```

This is the first confirmed self-modifying code in this project. It does **not**
revive the claim Session 186 withdrew: that one was `PM 0x1d8e`, the write watch
still fires zero times there, and the withdrawal stands.

### The two tables differ in exactly one place that matters

```text
bit    DM(0x0AB0) shipped default   DM(0x0AE6) patched-in
 0..5  2c6e 2c70 2c72 2c74 2c76 2c76   identical
 6     2c97                            2c76
 7     2c79                            28bf     <-- the swap
 8..12 3839 2c9c 3888 388d 3893         identical
```

And `PM 0x2C79` is the handler that does the right thing:

```text
2c79  MR1 = $0FCA              ; the correct terminator-0x1F base
2c7a  AX0 = DM($05C0)
2c7c  IF EQ JUMP $2C8A
2c7d  CALL $3883               ; probe
2c7f  CALL $3885               ; probe
2c84  MR0 = $1081  JUMP $2CB0  ; MR0 selected from a family of 0x1A bases
2c86  MR0 = $1720  JUMP $2CB0
2c88  MR0 = $16AB  JUMP $2CB0
```

So bit 7's proper handler **selects** `MR0` by probing `DM(0x05C0)`,
`DM(0x05BE)` and two tests, and always pairs it with `MR1 = $0FCA`.
`PM 0x28BF` does neither: it hardcodes `MR0 = $1174, MR1 = $1081`, and `0x1081`
is a terminator-`0x1A` base in the terminator-`0x1F` slot (188k). Everything
downstream follows.

### What is left

The chain is closed from `PM 0x290D` to `DM(0x0571)`. The one open link is the
patch's own provenance: the live word at `PM 0x2909` is `38ae60`, but
`0x0266`'s shipped PM block holds `403008` at that address, so **`PM 0x2909` is
itself not the shipped word** and something wrote it before it ran. That is now
the whole V.32 blocker, and it is one instruction.

### Two measurement traps this session walked into

* **A watch limit is spent by earlier pages at the same PM address.** `0x378e`
  with `:12` and `0x3805` with `:400` both reported zero executions in the V.32
  window; `0x3805` actually runs 1,372 times before it and executes at
  cyc 78,810,602. Always check the hit count against the limit before reading a
  silent watch as "never runs".
* **`EICON_PM_DUMP` snapshots the *loaded* image, not the executing one.** The
  dump reads `38ab00` at `0x3805` while the core executes `38ae60`. **The reason
  given here first — "the page falls back and `0x0262` loads over PM before an
  exit-time dump" — is wrong**, and Session 188o corrected it: the dump is taken
  when the overlay becomes resident (`eicon_mips_shim.py:3452`), so it is a
  snapshot from *before* any run-time patching. The `atexit` registration is
  only a fallback for a page that is never replaced. Same practical rule, sounder
  reason: a PM dump shows what was downloaded, and the `op=` field of an
  `[EXEC]` line is the only ground truth for what runs.

### Next

1. **Find who writes `PM 0x2909`.** `EICON_WATCH_PM=0x2909` gives the writer PC
   and cycle directly; `pgm_write_dag2()` logs every DAG2 store to a watched
   address, and `WWORD_PGM()` logs value changes with `was=`.
2. If nothing writes it, the live `0x2909` came in with an overlay load, and the
   question becomes which download supplies `PM 0x2909..0x290D` — the write watch
   does not see overlay loads or host writes (Session 186's recorded caveat).
3. The A/B, once the writer is known: suppress the patch so `PM 0x3805` keeps
   `I4 = $0AB0`, and see whether bit 7 reaches `PM 0x2C79`, `DM(0x05B7)` stays
   `0x0FCA`, and the per-frame abandon at `PM 0x353A` stops.

```bash
tools/eicon_loopback.py --native-mips --seconds 20 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_WATCH_PM=0x2909,0x290a,0x290b,0x3805 \
    --watch-exec 0x290d:8 \
    --capture-dir artifacts/loopback-lowspeed/s188m
```

## Session 188m: what the host driver does — and `PM 0x2909` is a trampoline copied in from resident PM, not a page writing its own code

Two things, because the first answers 188l's open link and the second bounds
where the answer could ever have come from.

### The Linux driver never touches the DSP

For DSP code the driver's whole job is three steps, and none of them writes
ADSP memory:

1. `pri_telindus_load()` (`kernel/s_pri.c:451`) opens `dspdload.bin` and calls
   `dsp_read_file()` (`divactrl/load/common/dsp_file.c:144`) with the card type
   number. That selects the file set from the combifile directory via a usage
   mask, then streams each download's DM and PM blocks into **card RAM** through
   the `pri_download_buffer` callback, bumping `IoAdapter->downloadAddr`.
2. It writes a header at `DspCodeBaseAddr`: a dword download count followed by
   `t_dsp_portable_desc download_table[128]`, 0x30 bytes per descriptor, each
   carrying `p_data_blocks_pm` / `p_data_blocks_dm` pointers into that RAM.
3. It stops. There is no IDMA path, no PM write, no DM write anywhere in the
   driver — `t_dsp_desc` and `p_data_blocks_pm` appear in `kernel/dsp_defs.h`
   and nowhere else in the kernel tree.

The card's own MIPS protocol image reads the table and drives the ADSP. So the
driver could never have been the source of any patch, and the shim already
models it exactly: `build_dsp_code_image()` plus
`descriptors = {download_id: base + 4 + index * 0x30}`.

`kernel/dsp_defs.h` is the authoritative format reference and matches the
extractor: `DSP_RELOC_TYPE_0..3`, `DSP_SEGMENT_FIRST_RELOCATABLE = 4`, and the
DWORD PM container. One practical consequence: the file set is chosen **per card
type**, so `EICON_DSP_EXTRA_DOWNLOADS` is the harness's stand-in for another
card's file set, not an arbitrary lever.

### `PM 0x2909` is copied in from `PM 0x0984`, by a loader below the overlay window

`EICON_WATCH_PM` on `0x2909..0x290D` answers 188l's open question outright:

```text
[WATCH] pm w 2909=38ae60 was=403008 ppc=1fbb pc=1fba cyc=78785250 i5=2909 ar=38ae
[WATCH] pm w 290a=3b8053 was=9030b8 ppc=1fbb ...              i5=290a ar=3b80
[WATCH] pm w 290b=3a9092 was=47fff0 ppc=1fbb ...              i5=290b ar=3a90
[WATCH] pm w 290c=500008 was=903090 ppc=1fbb ...              i5=290c ar=5000
[WATCH] pm w 290d=58000c was=404000 ppc=1fbb ...              i5=290d ar=5800
```

A two-instruction loop at `PM 0x1FBA/0x1FBB` writes all five words, `I5` walking
the destination and `I4` the source. The source is program memory, not data
memory:

```text
[WATCH] pm r 0985=3b8053 pc=1fbb cyc=78785251
[WATCH] pm r 0986=3a9092 pc=1fbb cyc=78785253
```

`PM 0x0985` holds exactly the word that lands at `PM 0x290A`, so the fragment
lives at **`PM 0x0984..0x0988`** — below `0x2000`, in the always-resident region
the overlay window never covers — and `0x1FBA` is a **PM→PM trampoline
installer** that stages it into the overlay window when the page activates. The
page tears it down again at cyc 78,827,863: `PM 0x3B83` zeroes all five words.

So the timeline for the whole chain is:

```text
cyc 78,782,332  0x0266 loaded
cyc 78,785,250  PM 0x1FBA copies PM 0x0984..0x0988 -> PM 0x2909..0x290D
cyc 78,787,773  partial 0x0267 loaded
cyc 78,801,924  the installed fragment rewrites PM 0x3805: I4 = $0AB0 -> $0AE6
cyc 78,801,962  DM(0x05C8) = 0x0484  (GEN_SETUP1)
cyc 78,810,611  dispatcher: mask 0x0484, table 0x0AE6
cyc 78,810,653  bit 7 -> PM 0x28BF instead of the shipped PM 0x2C79
cyc 78,810,900  the unpack runs away from 0x1081
cyc 78,827,863  PM 0x3B83 zeroes PM 0x2909..0x290D
```

### What this does to the reading

188l called `PM 0x2909` self-modifying code and left its provenance open. It is
still a DSP store into program memory, but it is **not the page writing its own
code**: the fragment is shipped, parked in resident PM, and installed by a
generic loader. Every stage of the chain — the resident fragment, the
trampoline, the table at `DM(0x0AE6)`, both record databases, `PM 0x28BF`'s
constants — is shipped firmware or shipped data, and the driver contributes
nothing.

That makes "the firmware has a swapped constant" a weak reading. A five-word
fragment parked in resident memory, trampolined in, used once, and zeroed again
is a deliberate mechanism, and we are walking through it as designed. **The
likelier reading is now that the harness puts the page in a state the real card
would not be in**, and the concrete question is which download last wrote
`PM 0x0984..0x0988` — because that fragment is what chooses the table.

### Correction

188m first looked for the copy's source in **data** memory at `DM(0x0985)`,
where `0x0266` ships `00d0 0008 00f0 0008 00d0` — no match. The source is
program memory; the `pm r` lines above are the measurement. Nothing downstream
depended on the wrong guess.

### Next

1. **Which download supplies `PM 0x0984..0x0988`?** It is below `0x2000`, so it
   is not in the overlay window and not in `0x0266`/`0x0267`'s PM blocks. Search
   the staged downloads for a PM block covering `0x0984`, and watch
   `EICON_WATCH_PM=0x0984` for a writer — the watch does not see overlay loads
   (Session 186's caveat), so a silent watch means it arrived with a download.
2. **The A/B is now well posed and cheap**: suppress the five-word copy at
   `PM 0x1FBA` (or restore `PM 0x3805` to `I4 = $0AB0` after it runs) and see
   whether bit 7 reaches `PM 0x2C79`, `DM(0x05B7)` stays `0x0FCA`, and the
   per-frame abandon at `PM 0x353A` stops. That tests the whole chain in one run.

```bash
tools/eicon_loopback.py --native-mips --seconds 20 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_WATCH_PM=0x0984,0x0985,0x0986,0x0987,0x0988 \
    --capture-dir artifacts/loopback-lowspeed/s188m3
```

## Session 188n: the A/B — pin `PM 0x3805` to `I4 = $0AB0` and V.32 stops abandoning

188m left the chain established and untested. It is now tested, and the result
is positive on every prediction.

### The lever: `EICON_PIN_PM`

`EICON_FORCE_DM` writes at overlay-load time, which cannot reach `PM 0x3805`:
`0x0267` lands at cyc 78,787,773 and the trampolined fragment rewrites the word
at cyc 78,801,924. `EICON_PIN_PM=ADDR=VALUE` (24-bit opcode) re-imposes the
value inside `WWORD_PGM()` after every DSP store, so the counterfactual is
"that patch did not stick" rather than "the image shipped differently". Overlay
loads and host writes bypass it, exactly as they bypass the write watch
(Session 186's caveat).

The pin reports its own hit count at exit, because **a pin that never fires
makes the run identical to the control and an unchanged result would mean
nothing**. Here it fired once, at the predicted instruction and cycle:

```text
[PIN] pm 3805 store=38ae60 held at 38ab00 ppc=290d pc=290e cyc=78801924
[pin-pm] PM 0x3805 held at 0x38ab00: 1 stores undone
```

### Every link behaved as the chain predicted

```text
                          control                pinned
dispatcher table (0x3821) i4 = 0x0AE6            i4 = 0x0AB0
bit 7 handler             PM 0x28BF              PM 0x2C79
DM(0x05B7) install        0x1081 (mr1=1081)      0x0FCA (mr1=0fca, mr0=1081)
unpack #1                 ax0=0x1174 mr1=0x1A    ax0=0x1081 mr1=0x1A
unpack #2                 ax0=0x1081 mr1=0x1F    ax0=0x0FCA mr1=0x1F
                          -- runs away --        terminates at 0x0FD3
DM(0x0571)                = 0x18f3 @78,820,636   never written in the window
```

Three details worth keeping. `PM 0x2C79` did exactly what 188l predicted from
its disassembly: it set `MR1 = $0FCA` *and selected* `MR0 = 0x1081` by probing
`DM(0x05C0)`/`DM(0x05BE)` — so `0x1081` is a perfectly good base, it was simply
in the wrong slot. Both unpacks are now correctly paired with their terminators
and both terminate. And the `0x0FCA` walk ends at `0x0FD3`, which is where
188k's static walk over the DM dump said it would, after three records.

In the control the runaway also scribbles its own cursor —
`dm w 05b7=6e6c ppc=2cfa` — which is how it kept going; that write is gone too.

### The call gets materially further

```text
control                                pinned
TrnProgress 0x0009 -> 0x0000           TrnProgress 0x0009 -> 0x0040
bootpage 6 V.8 -> 0 DIAL               bootpage 6 V.8 -> 2 V.32
status block hijacked by PM 0x3b25     (no hijack)
DI_control=0xe000                      DI_control=0xa000[tx_request|rx0_valid]
                                       INFO_variant=0x0089
0x0262 reloaded at cyc 78,839,037      0x0262 reloaded at cyc 78,947,168
```

**The V.32 page becomes the resident bootpage for the first time in this
project.** It is no longer abandoning per frame: the `PM 0x353A` test reads a
zero `DM(0x0571)` and falls through, and the page runs 108,131 cycles further
before it gives up. Session 136's status-block hijack message does not appear,
which fits — that was the page scribbling over `DM(0x3fb0..0x3fca)` on its way
out.

### The new blocker, which is a different one

160 samples later the page still stops:

```text
sample 22880: TrnProgress 0x0040 -> 0x0000
sample 22880: bootpage 2 V.32 -> 11 AT offline, overlay=0x0262
sample 22880: Rstatus=0x9d28[online|ring_valid|core|boot_request|test|ring]
sample 22880: DI_control=0xcb71[tx_request|rx1_valid|codec_clocking|sync]
              BaudInfo=0xac99
```

That is a *later and different* failure: it reaches `TrnProgress 0x0040`, drives
`DI_control` with `codec_clocking|sync` and a non-zero `BaudInfo`, and then
drops to **AT offline** rather than to DIAL. `Rstatus 0x9d28` carries `test` and
`ring`, which no V.32 state should.

### What this does and does not establish

**Established.** The `PM 0x3805` rewrite is the sole cause of the immediate V.32
abandon. One held word removes the runaway, the scattered parameter block, the
non-zero `DM(0x0571)`, the per-frame abandon and the fallback to DIAL, and lets
the page take bootpage 2.

**Not established.** That the rewrite is a *defect*. Everything in the chain is
shipped firmware (188m), so the more likely reading remains that the harness has
the page in a state the real card would not be in and the patch is correct
behaviour for some configuration this rig is not in. The pin proves causation,
not intent. The `AT offline` stop above is consistent with either.

### Next

1. **Which download supplies `PM 0x0984..0x0988`**, the fragment the trampoline
   installs. That is still the open provenance question from 188m and it is what
   would say whether the patch belongs to this configuration.
2. **Chase the new stop.** `TrnProgress 0x0040` with `BaudInfo=0xac99` and
   `DI_control` asserting `codec_clocking|sync` is a much later seam than
   anything this project has had on V.32; `Rstatus 0x9d28`'s `test|ring` bits
   are the thread to pull.
3. Re-run the earlier V.32 experiments with the pin armed — several of them were
   measuring a page that abandoned before it could answer the question.

```bash
tools/eicon_loopback.py --native-mips --seconds 20 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_PIN_PM=0x3805=0x38ab00 \
    --capture-dir artifacts/loopback-lowspeed/s188n-pinned
```

## Session 188o: nineteen V.32 frames complete, then the PC stack fills and a non-reentrant kernel scribbles the status block

With `EICON_PIN_PM=0x3805=0x38ab00` armed, the stop 188n reported as
"`TrnProgress 0x0040` → AT offline" is not a state the modem reached. The
failure is real; the state is misread scratch.

### What actually happens, in order

```text
cyc 80,942,824 .. 81,040,090   19 frames complete at PM 0x3543   <-- new
cyc 81,055,943                 PC stack overflow, depth 16, pc=0274
cyc 81,056,277                 loop stack overflow at 0x1c24
cyc 81,056,288                 counter stack overflow at 0x1c23
cyc 81,067,635                 DM(0x3FB0) written by PM 0x1c29   <-- scribble
cyc 81,067,734                 DM(0x3FC1) = 0x9928 by PM 0x1c2c
cyc 81,078,615                 0x0262 loaded
```

**Nineteen successful V.32 frames.** `PM 0x3543` is the frame-complete store
(188h: `0x3541..0x3543` sets bit 10 of `DM(0x3FC1)` at the end of a *successful*
frame), and it fires 19 times. V.32 has never processed a frame in this project
before. Nothing breaks until 15,853 cycles after the last one.

*(Late cycle numbers are not comparable between runs. The rig is real-time
paced, so once the page runs continuously the sample count depends on host
speed; 188n's copy of this sequence sits ~2.1 M cycles earlier. Order and
spacing are stable, absolute cycles are not.)*

### The status block is overwritten, so the reported state is scratch

The kernel at `PM 0x1C1F..0x1C3C` writes through `I0` with post-increment at
`0x1c29`, `0x1c2c`, `0x1c2e`, `0x1c2f` and `0x1c39`. After the stacks saturate,
`I0` has walked past the end of its buffer:

```text
dm w 3fb0=0000 ppc=1c29 i0=3fb0 ar=b266 af=2181 mr0=92fc mr1=e20c sr0=72a2
dm w 3fc1=9928 ppc=1c2c i0=3fc1 ar=9928 af=2181 mr0=13e0 mr1=bb06 sr0=09f0
```

Those are signal-domain values landing on the status block. Only *after* that
does the shim read `bootpage 11 AT offline`, `Rstatus=0x9d28[…|test|ring]`,
`DI_control=0xcb71`, `BaudInfo=0xac99` and serve a page request — all of it out
of overwritten memory. This is Session 136's hijack at a different address; 136's
detector did not fire because it keys on `TrnProgress` reading `0x0100`. The 488
later writes of `0x9d28` from `PM 0x1DF6` are the DIAL page's own use of the
word, after the fallback, and are not V.32 status either.

### The loop is bounded — this is not Session 188b's shape

```text
1fe0  CNTR = $000B          ; eleven, hardcoded
1fe1  CALL $1C1F
1fe2  RTS

1c21  DO $1C3C UNTIL NOT CE ; outer, 11
1c22  CNTR = $0002
1c23  DO $1C2D UNTIL NOT CE ; inner, 2
1c24..1c2d  MAC chain, DM(I0,M1) stores, PM(I4,M5) coefficients
```

The count is a constant `11`, not a word read from memory, so there is no
runaway iteration count here and `DM(0x3754)` is not involved. What the loop
stack shows instead is **re-entrancy**: `end=1c2d` appears three times nested.
The kernel keeps its state in `I0`/`I4`/`MR` and is not re-entrant, so each
nested entry resumes advancing `I0` from wherever the interrupted one left it —
which is how a bounded loop still walks off the end of its buffer.

### Where the nesting comes from

The PC stack at overflow is four passes through one chain:

```text
0773 1e7f 1d12 1d29   0773 1e7f 1d12 1d29   0773 1e7f 1d12 1d29   3540 02a8 06ca 066a
```

and each return address names an indirect dispatch:

```text
0770  ENA INTS               ; interrupts re-enabled *before* dispatching
0771  I4 = DM($3FB3)
0772  CALL (I4)              ; -> 0773
1e7e  CALL (I4)              ; I4 = DM($37F6)   -> 1e7f
1d11  IF GE CALL $1D25       ; -> 1d12
1d28  CALL (I4)              ; I4 = DM($3FB8)   -> 1d29
1fe1  CALL $1C1F             ; -> 1fe2
```

`ENA INTS` at `PM 0x0770` means the dispatched handler is interruptible by
design, and each nested interrupt costs four PC-stack frames, so four of them
fill the 16-deep stack exactly.

**What is not explained is why it fills at all.** The 19 frames span 113,119
cycles, averaging 5,953 cycles each (spacings cluster at ~2,350, ~6,390 and
~7,030), against a harness budget of 20,000 cycles per 8 kHz sample. The work
fits the budget roughly three times over, so this is not an overrun, and the
depth builds gradually across ~19 frames rather than spiking. That pattern looks
more like a **leak** than like momentary nesting. Two candidates, and they are
distinguishable:

1. **Genuine nesting**, with the harness delivering SPORT interrupts in a
   pattern the hardware would not.
2. **Frames that are pushed and never popped.** `PM 0x0774` is
   `AR = DM($313A); TOPPCSTACK = AR` — the firmware *rewrites the top of the PC
   stack* instead of returning normally. If the core's `TOPPCSTACK` write or its
   `RTI` pop does not match hardware, depth ratchets up. This project has been
   bitten by this class before (188c's silent `cntr_stack_push()` drops).

### Next

1. **Instrument PC-stack depth over time** — a high-water mark per frame is
   enough. Monotonic growth that never recovers means a leak and points at the
   `TOPPCSTACK` rewrite at `PM 0x0774`; spike-and-recover means real nesting and
   points at interrupt delivery. That single measurement separates the two, and
   nothing else should be attempted before it.
2. If it is the `TOPPCSTACK` path, check the core's implementation of writing
   `TOPPCSTACK` against the ADSP-2181 manual: rewriting the top of stack must
   *replace* the return address, not push a second one.
3. Only then re-open "does V.32 train" — 19 frames is the deepest this has gone,
   and the answer is no longer blocked on the parameter block.

```bash
tools/eicon_loopback.py --native-mips --seconds 20 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_PIN_PM=0x3805=0x38ab00 \
    --watch-dm-writes 0x3fb0,0x3fc1 \
    --capture-dir artifacts/loopback-lowspeed/s188o
```

## Session 188p: the stack does not leak and does not merely nest — it unwinds cleanly for 69 frames and then stalls in three

188o offered two candidates for the PC-stack failure and said one measurement
would separate them. The measurement says **neither**.

### The instrument

`adsp2181_pcsp_window()` keeps the minimum and maximum PC-stack depth since the
last call and resets to the current depth, with `note_pcsp()` on every push and
pop so extremes *inside* a frame survive. `EICON_PCSP_TRACE=PATH` samples it
once per 8 kHz frame and writes `sample,cycle,resident,pcsp_min,pcsp_max`.

It does not perturb the run: the PC-stack overflow lands at cyc 78,924,523 with
the trace armed and at cyc 78,924,523 without it.

### The shape

75 frames run with `0x0266` resident. For the first 69 the **floor is 0** — the
stack unwinds completely, every frame — with peaks of 5 to 11. Then, in three
frames:

```text
sample     cycle    Δcyc  floor peak
 24411   78914028   1165     0    5
 24412   78917964   3936     0   13
 24413   78919481   1517     4   13   <-- first frame that does not unwind
 24414   78922490   3009     8   13
 24415   78924628   2138     8   16   <-- overflow
 24416   78944628  20000    12   16   <-- full budget, never reached IDLE
 24417   78946548   1920    12   16
```

**A slow leak is dead**: 69 consecutive frames return to depth 0, so nothing is
being stranded per dispatch. **Simple nesting is dead too**: after 24413 the
floor never returns to 0. What actually happens is a *stall with an abrupt
onset* — the floor climbs 0 → 4 → 8 → 12 in unit steps of exactly four, which is
one complete dispatch chain (`0773 1e7f 1d12 1d29`, 188o) stranded per frame,
until the 16-deep stack is full three frames later.

The Δcycle column carries the same story. A normal frame is 1,138 cycles, with a
periodic 2,400–3,000 one. Sample 24397 is an early outlier at 5,118 cycles and
peak 11; 24412 is 3,936 with peak 13; and by 24416 the frame consumes the entire
20,000-cycle budget without reaching IDLE.

### What this rules out

188o's second candidate was `PM 0x0774`'s `AR = DM($313A); TOPPCSTACK = AR`
against `wr_topstack()` in `2100ops.inc:563`, which calls `pc_stack_push_val()`
— a **push**, where `set_pc_stack_top()` (the replace primitive) sits unused ten
lines above. That would strand one frame on *every* execution, and 69 clean
frames say it is not what is happening here.

It is still worth checking on its own account: if the ADSP-2181 replaces the top
of stack on a `TOPPCSTACK` write rather than pushing, the core is wrong
independently of this bug. An attempt to see whether `0x0774` even executes in
the V.32 window was **inconclusive** — `--watch-exec 0x0774:12` spent its whole
limit by cyc 33,076,899 on an earlier page, which is 188l's trap again.

### Next

1. **What happens in sample 24412.** That is the frame where the peak first
   exceeds anything seen before (13 against a running maximum of 11) and the
   frame after which returns stop happening. It spans cyc 78,914,028..78,917,964,
   which is a small enough window to trace exhaustively.
2. The precursor at sample 24397 (5,118 cycles, peak 11) is the same shape
   1.5 ms earlier and recovered. Whatever 24412 is, 24397 nearly was.
3. Separately and independently: check `TOPPCSTACK` write semantics against the
   ADSP-2181 manual and fix `wr_topstack()` if it should replace. Note that any
   `--watch-exec` on a low PM address needs a limit in the tens of thousands, or
   the earlier pages eat it.

```bash
tools/eicon_loopback.py --native-mips --seconds 20 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_PIN_PM=0x3805=0x38ab00 \
    --answerer-env EICON_PCSP_TRACE=/tmp/pcsp.csv \
    --capture-dir artifacts/loopback-lowspeed/s188p
```

## Session 188q: gate the watches to the page under test — and `PM 0x0774` is cleared, with a positive control

Three readings in this session were wrong because output drowned rather than
because the firmware surprised us:

* 188l reported "the PM write watch printed only reads". The `pm w` line was
  there, past a `head -10`.
* 188l reported `PM 0x378e` and `PM 0x3805` as "never executing in the V.32
  window". Both execute there; the `--watch-exec` limits had been spent by
  earlier pages at the same addresses.
* 188p's check of `PM 0x0774` was abandoned as inconclusive for the same reason.

The common cause is structural, not carelessness: **a PM address is a different
instruction on each resident page**, so a watch armed for one page fires on all
of them and spends its budget long before the page in question.

### Two changes

**`EICON_WATCH_OVERLAY=<id>[,<id>…]`** gates every watch — exec, DM, PM — on
residency (`adsp2181_watch_gate()`), disarmed until one of the named overlays is
resident. A limited watch does not decrement while disarmed, so the limit is
spent where the question is. Every transition prints, which matters: see below.

**A spent limit now says so**, in the log, at the cycle it happened:

```text
[EXEC] limit spent for pc=0774 at cyc=78822940 -- no further executions of this
       address will be logged
```

Silence after that line means "stopped looking"; silence without it means "did
not happen". Those were indistinguishable before.

Neither perturbs the run: the PC-stack overflow lands at cyc 78,924,523 gated
and ungated.

### The gate needs the *composite* page, and a zero still needs a control

First attempt gated on `0x0266` alone and `PM 0x0774` came back with zero hits —
which looked like an answer and was not. The gate history showed why:

```text
[watch-gate] armed:    resident 0x0266 [cyc=78782332]
[watch-gate] disarmed: resident 0x0267 [cyc=78787773]   <-- 5,441 cycles later
```

The page and its partial are one page to the firmware and two residency values
here, so the gate closed almost immediately. `EICON_WATCH_OVERLAY` takes a list
for exactly this reason. **A zero from a gated watch is only worth anything
beside a positive control**: `PM 0x3543` gated to `0x0266,0x0267` fires 19
times, matching the 19 frame-completes 188o measured independently.

### `PM 0x0774` is cleared, and now on evidence

With the gate right, the complete count (limit 5000, never spent) is **73
executions in the V.32 window**:

```text
69 before the stall onset at cyc 78,917,964      psp = 0 at every one
 4 after                                         psp = 8, 8, 8, 12
```

Sixty-nine is exactly 188p's count of clean frames, so `PM 0x0774` runs **once
per frame**, and the PC stack is **empty at every execution** before the stall.
If `wr_topstack()`'s push (`2100ops.inc:563`) leaked a frame per execution the
depth would climb from the first frame; it does not move at all for 69 of them.

**`wr_topstack()` is not the mechanism, and this is now measured rather than
inferred.** Whether a `TOPPCSTACK` write should replace the top of stack instead
of pushing is still an open correctness question about the core — the replace
primitive `set_pc_stack_top()` sits unused ten lines above — but it has nothing
to do with the V.32 stall.

### Next

Unchanged from 188p, and now much cheaper to run: **trace sample 24412
exhaustively** (cyc 78,914,028..78,917,964), with
`EICON_WATCH_OVERLAY=0x0266,0x0267` so the budget goes where the question is.
Sample 24397 is the same shape 1.5 ms earlier and recovered, so it is the
control.

```bash
tools/eicon_loopback.py --native-mips --seconds 20 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_PIN_PM=0x3805=0x38ab00 \
    --answerer-env EICON_WATCH_OVERLAY=0x0266,0x0267 \
    --watch-exec 0x0774:5000 \
    --capture-dir artifacts/loopback-lowspeed/s188q3
```

## Session 188r: `PM 0x3536` is entered once per frame and never returns

Sample 24412 traced instruction by instruction. It is not a fault — it is the
frame in which the V.32 page **activates**, and the stall is a direct
consequence of how its frame handler exits.

### The lever

`EICON_TRACE_FRAMES=<sample>[,<sample>…]` arms the core's instruction trace for
whole 8 kHz frames by sample number, `EICON_TRACE_BUDGET` instructions each
(default 8000, ~4,000 needed). The budget is cleared when the frame ends so it
cannot bleed into the next and mislabel which frame a line belongs to. Frames
24411 (normal), 24412 (the anomaly) and 24413 (the first that does not unwind)
were captured together, and the boundaries match `EICON_PCSP_TRACE` exactly.

### 24412 is an activation frame

```text
frame 24411:  1,165 instructions,   601 distinct PCs
frame 24412:  3,936 instructions, 1,333 distinct PCs
frame 24413:  1,516 instructions,   937 distinct PCs
```

908 PCs run in 24412 that 24411 never touches, in coherent blocks: the parameter
unpacker (`0x2cb5..0x2cfe`), the mode dispatcher (`0x376b..0x379a`,
`0x3809..0x382f`), the frame-handler prologue (`0x3528..0x353f`), and the LEC
setup (`0x1d87..0x1dba`).

Two things worth recording from that:

* **The LEC is fine.** `PM 0x1d90` executes **9** times, so the tap count is the
  firmware's own 9 — Session 188b's `DM(0x3754) = 0xfff4` runaway is *not*
  happening here.
* **The frame stages a code overlay into program memory.** `PM 0x3b7b` and
  `PM 0x3b83` are both `op=580005`, a PM store through `I5`, and between them
  they write **1,744 words**: `I5` walks `0x2400..0x27ff` at `0x3b7b` and
  `0x2800..0x2acf` at `0x3b83`. That is the same machinery that zeroed
  `PM 0x2909..0x290D` in 188m, and `0x2909` is inside the second span.

### The mechanism: a call that never returns

`PM 0x1d28` is `CALL (I4)` with `I4 = DM($3FB8)`. Its target and its return
point, counted per frame:

```text
frame     0x1d28 (the CALL)   0x3536 (target)   0x1d29 (the RETURN point)
24411            0                  0                   0
24412            1                  1                   0
24413            1                  1                   0
```

**`PM 0x3536` — the V.32 frame handler — is entered once per frame from 24412
onward and `PM 0x1d29` never executes.** The handler does not return; it runs
its body (`0x3536` calls `0x3528`, tests `DM(0x0571)` at `0x3538..0x353a`, then
`0x34C4`, `0x36BF`, `0x2B76`, `0x2C69`, `0x2CB5`, `0x376B`, and sets the
frame-complete bit at `0x3541..0x3543`) and tail-transfers to the frame-end path
instead.

That is the whole stall, and it explains the shape 188p measured exactly:

* the chain that reaches `0x1d28` is four deep — `0773 1e7f 1d12 1d29` — so
  **each frame strands exactly four PC-stack entries**, which is the step size
  188p saw;
* the onset is abrupt because it begins the moment `0x3536` first runs, in 24412;
* four such frames fill the 16-deep stack, which is 24413, 24414, 24415;
* and the core still reaches the idle point every frame — 24411 and 24413 both
  end identically through `0x06d6..0x06dd → 0x02a8` — which is why the failure
  looked like a stall rather than a hang.

### What this makes the open question

Not "what corrupts the stack" but **"what is supposed to unwind it"**. The
handler tail-transfers to the frame-end path rather than returning through
`0x1d29`, so on real hardware something must discard those frames. Candidates,
in order of cheapness:

1. The frame-end path itself pops or resets the PC stack by a route the core does
   not model — the `TOPPCSTACK` question 188q parked is now relevant again, from
   a different direction.
2. The harness's own frame delivery. `adsp2181_modem_sample(..., continuation,
   0x02A8)` injects the continuation at `0x02A8`; if the hardware flow re-enters
   through a path that unwinds and ours enters below it, the difference is the
   harness's, not the firmware's.

### Caveat: the stall cycle is not stable across runs

Four runs stall at cyc 78,924,523 (`s188n-pinned`, `s188p`, `s188q3`, `s188r`)
and one at 81,055,943 (`s188o`). The odd one out is the run that carried
`--watch-dm-writes`, and it is also the run 188o counted **19 frame-completes**
from. **That 19 is therefore run-specific and should not be treated as a
constant**, and whether a DM watch perturbs execution needs settling before any
frame count is quoted again. The mechanism above is identical in both: the same
overflow PC, the same four-deep chain.

### Next

1. **Settle whether `--watch-dm-writes` perturbs the run.** One A/B, same seed,
   with and without. Everything quantitative in 188o rests on it.
2. **Find the intended unwind.** Trace a *V.22* or *V.90* frame the same way
   (`EICON_TRACE_FRAMES`) and see whether its frame handler returns through its
   caller or tail-transfers like `0x3536`. A page that works is the control this
   question needs, and this project has two.

```bash
tools/eicon_loopback.py --native-mips --seconds 20 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_PIN_PM=0x3805=0x38ab00 \
    --answerer-env EICON_TRACE_FRAMES=24411,24412,24413 \
    --capture-dir artifacts/loopback-lowspeed/s188r
```

## Session 188s: log volume changes the answer — the rig is wall-clock paced, and a hot watch moves the V.32 stall by 1.8 M cycles

188r flagged one run that disagreed with four others and blamed
`--watch-dm-writes`. That was half right. **The feature is not the problem; the
volume is.**

### The A/B

Same command throughout, `EICON_PIN_PM=0x3805=0x38ab00`, varying one thing:

```text
condition                          log lines   clock holds   ratio   stall cycle
baseline                    x2             0          1611   0.66x    78,924,523
+ EICON_PM_DUMP             x2             0          1633   0.66x    78,924,523
+ --watch-dm-writes 0x3fb0                  5          1656   0.65x    78,924,523
+ --watch-dm-writes 0x3fc1             44,482            52   0.99x    79,819,831
+ --watch-dm-writes both    x2         45,297            33   0.99x    80,724,401
```

`0x3FB0` and `0x3FC1` are the same feature, one watch each. `0x3FB0` takes five
writes and reproduces the baseline **exactly**. `0x3FC1` takes tens of thousands
and moves the stall by 0.9 M cycles; both together move it by 1.8 M. A one-shot
`EICON_PM_DUMP` of 8,192 words does not perturb at all, so it is sustained
output that matters, not work as such.

### The mechanism is the pacing feedback

The loopback paces both endpoints to the wall clock so the V.8 handshake stays
synchronised (`--realtime`, on by default; `--no-realtime` is documented as
making V.8 fail). An unloaded run therefore spends most of its time *waiting*:
~1,600 clock holds in seven seconds, ratio 0.66x. A run that cannot keep up
never waits — 20 to 50 holds, ratio 0.99x — and the DSP sees a different sample
timeline. **Host speed is an input to the emulation.**

Two consequences worth having in mind:

* **Clean runs are reproducible.** Six of them stall at cyc 78,924,523.
* **Host-bound runs are not, even against themselves.** The same
  `--watch-dm-writes 0x3fc1` command gave 79,819,831 once and 80,724,401
  another time.

### The guard

The media report now warns once, when the clock holds collapse:

```text
[media] WARNING: host-bound -- only 20 clock holds in 10 s, so the emulated
        timeline is being set by how fast this machine runs, not by the 8 kHz
        clock. Cycle counts and frame counts from this run are not comparable
        with an unloaded one. Usually log volume: gate watches with
        EICON_WATCH_OVERLAY and avoid watching hot addresses.
```

Silent on a clean run, once on a host-bound one. It is the same theme as 188q's
watch gate: make the trap say its own name instead of leaving a number that
looks fine.

### Which past runs are affected

```text
run       log lines   holds   ratio   stall         verdict
s188o        45,601       9   1.00x   81,055,943    host-bound
s188p             0    1578   0.66x   78,924,523    clean
s188q2           94    1612   0.66x   78,924,523    clean
s188q3          219    1519   0.65x   78,924,523    clean
s188r         6,618    1693   0.65x   78,924,523    clean
```

Only `s188o` is affected, and `EICON_WATCH_OVERLAY` is why the later runs are
not — gating cut the volume by two orders of magnitude. A bounded frame trace
(6,618 lines) is comfortably safe.

### Correction to 188r's caveat

188r said 188o's **19 frame-completes** was "run-specific and should not be
treated as a constant". That was too strong. `s188q2` is a clean run with a
gated watch on `PM 0x3543`, and it counts **19** independently. **The 19 stands.**

What does *not* survive is 188o's cycle arithmetic around it — the
80,942,824..81,040,090 timeline, the 113,119-cycle span and the 5,953
cycles-per-frame average were all measured host-bound. 188p's per-frame cadence
(1,138 cycles, the 2,400–3,000 outliers) came from a clean run and stands.

### Next

Unchanged: **find the intended unwind for `PM 0x3536`** (188r). Trace a V.22 or
V.90 frame with `EICON_TRACE_FRAMES` and see whether a working page's frame
handler returns through its caller or tail-transfers the same way. Keep watches
gated and off hot addresses, and check the media line before quoting a number.

```bash
tools/eicon_loopback.py --native-mips --seconds 20 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_PIN_PM=0x3805=0x38ab00 \
    --watch-dm-writes 0x3fb0        # five writes: safe
#   --watch-dm-writes 0x3fc1        # tens of thousands: changes the answer
```

## Session 188t: V.22 returns through the same dispatcher — and V.32 calls a routine its own frame erased

188r asked whether a working page's frame handler returns through its caller or
tail-transfers like `PM 0x3536`. V.22 is the control this project has, and it
answers both that question and the one behind it.

### V.22 uses the same page image and the same dispatcher

V.22bis connects on the old-rig recipe (Session 183) as `bootpage 1 V.22,
overlay 0x0266` — **the same overlay id as V.32**, the shared "V.22/V.32 LEC"
image. So the comparison is not across two firmwares; it is the same program
memory in two modes, reached through the same resident dispatcher at
`PM 0x1d28` (`I4 = DM($3FB8); CALL (I4)`).

Across a connected 30 s call:

```text
frames with 0x0266 resident   144,170
per-frame stack floor         0 in 144,170 of 144,170
peaks                         5 (100,919), 8 (42,704), 9 (4), 10 (543)
stack overflows               0
```

The stack unwinds **completely, every frame, for the whole call**.

### The handler returns

Two steady-state frames traced with `EICON_TRACE_FRAMES`:

```text
frame 56000  1,113 instructions   0x1d28 x0   0x1d29 x0            (idle frame)
frame 56002  5,438 instructions   0x1d28 x1 -> 0x3e4c   0x1d29 x1  <-- returns
```

V.22's frame handler is `PM 0x3e4c`, and `PM 0x1d29` — the return point that
never executes on V.32 — **executes once, in the same frame as the call**. It is
also the *longer* handler: 5,438 instructions against V.32's 1,516. So a
tail-transfer is not the house style and length is not the issue. **The
dispatcher expects a return, working pages give it one, and `PM 0x3536` is the
anomaly.**

That also weakens 188r's second candidate: V.22 runs through the same
`0x02A8` continuation and the same harness frame delivery, and unwinds fine, so
the harness's injection is not what strands V.32's frames.

### Why `PM 0x3536` does not return: it calls into memory the same frame erased

The V.32 handler's exit path, followed through the trace of frame 24413:

```text
3536 -> 3528 -> 3537 -> 353b -> 34c4 -> 353c -> 36bf -> 353d -> 2b76
     -> 353e -> 2c69 -> 353f -> 2929 ......... 2adb -> 06c8 -> ... -> 02a8 IDLE
```

`PM 0x353F` executes as `1e929f` = **`CALL $2929`**, and `PM 0x2929` reads:

```text
frame 24412   1ecb5f   = CALL $2CB5     <-- real code
frame 24413   000000   = NOP            <-- erased between the two frames
```

188r recorded that frame 24412 writes 1,744 words of program memory at
`PM 0x3b7b`/`PM 0x3b83`, `I5` walking `0x2400..0x27ff` and `0x2800..0x2acf`. It
is a **fill, not a copy** — `ar`, `sr0`, `sr1` and `i4` are all constant across
every one of the 1,744 stores — and what it fills with is zero:

```text
executed in 0x2929..0x2acf (inside the fill)   423 addresses, all 000000
executed in 0x2ad0..0x2adb (past its end)       12 addresses of leftover words
```

So the frame handler calls `0x2929`, sleds through 423 zero words, runs twelve
words of whatever lay beyond the fill, falls into the resident kernel at
`0x06c8` and ends at `0x02a8` IDLE. The four-deep chain that reached it is never
unwound. **That is the stall, exactly, and it is one event: the page's own
staging fill erased the routine its frame handler calls.**

Note the call target moved too — `PM 0x353F` is `1ecb5f` (`CALL $2CB5`) in the
loaded image and `1e929f` (`CALL $2929`) as executed. The page re-points its
handler at a routine that is supposed to be staged into `0x2400..0x2ACF`, clears
the region, and then calls into it. **What is missing is whatever should write
the routine in between.**

### Caveats

* The V.22 run is **host-bound** (0 clock holds, ratio 1.00x) and the new 188s
  warning fired on it, so its cycle counts are not comparable with the V.32
  ones. Nothing above rests on them: "the floor is 0 in every one of 144,170
  frames" and "`0x1d29` executes in the same frame as `0x1d28`" are structural.
* All the V.32 measurements are **under `EICON_PIN_PM=0x3805=0x38ab00`**, which
  changed which mode the page selected (188n). It is entirely possible the pin
  put the page into a mode that clears the staging area and never re-stages it,
  in which case the erased routine is a consequence of the counterfactual rather
  than a defect. This must be checked before the fill is called a bug.

### Next

1. **Does the fill happen without the pin?** One run, `EICON_WATCH_OVERLAY=0x0266,0x0267`
   and a low-volume watch, pin off. If `PM 0x3b7b`/`0x3b83` never runs unpinned,
   the pin is implicated and the V.32 blocker moves back to mode selection.
2. **What is supposed to write `0x2400..0x2ACF`?** The fill's source register is
   not in the trace columns; disassemble `PM 0x3b7b`/`0x3b83` from the *live*
   image (`op=` ground truth, not a dump) and find whether a load follows the
   clear on some path this run does not take.
3. `PM 0x353F` differing between the loaded image (`1ecb5f`) and execution
   (`1e929f`) is a third PM rewrite, after `0x3805` and `0x2909..0x290D`. Worth
   a `EICON_WATCH_PM=0x353f` to see who writes it and when.

```bash
# the V.22 control
tools/eicon_loopback.py --native-mips --seconds 30 --setup-gap-ms 0 \
    --caller-env EICON_RX_LAG_MS=25 --answerer-env EICON_RX_LAG_MS=25 \
    --caller-env EICON_ORIGINATE_NORM_L= \
    --answerer-env EICON_TRACE_FRAMES=56000,56002 \
    --capture-dir artifacts/loopback-lowspeed/v22-trace
```

## Session 188u: the fill is not the pin's doing — the window is staged once, cleared once, and never re-staged

188t's caveat was that every V.32 measurement sat under
`EICON_PIN_PM=0x3805=0x38ab00`, so the zero fill at `PM 0x3b7b`/`0x3b83` might be
a consequence of the counterfactual. **It is not.**

### The fill runs identically with the pin off

Gated to `0x0266,0x0267`, low-volume watches, neither run host-bound:

```text
                    pinned                    unpinned
PM 0x3b7b           4 (limit)                 4 (limit)
PM 0x3b83           4 (limit)                 4 (limit)
first execution     cyc 78,915,832            cyc 78,827,151
cntr / ar at entry  01b6 / 01b6               01b6 / 01b6
trail into it       3825..382a 3b20..3b28     identical
                    3b73..3b7b
```

Same instruction, same trail, same counts, same values. The only difference is
**when**: unpinned it runs 88,681 cycles earlier, because the page gives up
sooner. And the trail names what it is — `PM 0x382a` is the walking-bit
`CALL (I4)` dispatcher, so **the fill is a bit handler**, `0x3b20`, in the same
machinery as `0x28BF` (188l).

### Staged once, erased once, never restored

`EICON_WATCH_PM` on `0x2929` and `0x2500`, one word from each half of the filled
span. Every write in the whole call, in both runs:

```text
cyc 78,785,314   PM 0x2929: 0x510971 -> 0x1ecb5f   by PM 0x1fbb   <-- staged
cyc 78,9xx,xxx   PM 0x2500: 0x1a5060 -> 0x000000   by PM 0x3b7b   <-- cleared
cyc 78,9xx,xxx   PM 0x2929: 0x1ecb5f -> 0x000000   by PM 0x3b83   <-- cleared
```

Three events, and that is all of them. `PM 0x1fbb` is **the same trampoline
installer 188m found** copying `PM 0x0984..0x0988` into `PM 0x2909..0x290D`; it
stages `0x2929` 64 cycles later, in the same pass. So the window is filled with
real code once at page activation, cleared later, and **nothing ever re-stages
it** — with or without the pin.

### What the pin actually changed

Only whether the page lives long enough to call into the hole:

```text
unpinned   78,810,115  handler 0x3536 entered
           78,810,697  PM 0x353f -> CALL $2929      (real code, returns)
           78,827,895  0x2929 erased
           78,837,145  handler entered again -- abandons at 0x353a, never
                       reaches 0x353f; page falls back to DIAL

pinned     78,810,115  handler entered
           78,810,697  PM 0x353f -> CALL $2929      (real code, returns)
           ...         handler entered every ~7,000 cycles, 0x353f each time
           78,916,576  0x2929 erased
           thereafter  every 0x353f call sleds into the zeros -- the stall
```

Unpinned, `DM(0x0571)` is non-zero so the abandon at `PM 0x353a` fires and the
page never reaches `0x353f` again. Pinned, the abandon is gone, the handler keeps
running, and after the clear it walks into 423 zero words. **The clear is real
firmware behaviour in both; the pin only removed the thing that was stopping the
page before it mattered.**

### Correction to 188t

188t said the page "re-points its handler" from `CALL $2CB5` to `CALL $2929` as
part of the staging sequence. **Wrong.** `PM 0x353f` executes as `1e929f`
(`CALL $2929`) at the *first* handler entry, cyc 78,810,697, in both runs — long
before any fill. The `1ecb5f` it was compared against came from an
`EICON_PM_DUMP`, which is the loaded image and not what executes (188q). There
is no re-pointing; there is one live call target that the dump never showed.

### Next

The question is now specific: **`PM 0x3b20` is a dispatcher bit handler that
clears `PM 0x2400..0x2ACF` and nothing re-stages it.** Either it is a teardown
handler that should only run when the page is finished — in which case what
dispatches it while the page is still running is the bug — or a re-stage is
supposed to follow it and does not.

1. **Which mask bit dispatches `0x3b20`?** 188o's `0x382a` trail puts this call
   at `ret=3783`, i.e. the prologue at `PM 0x3813` with table `DM(0x0ADD)` and
   `CNTR = 4`, whose mask is built at `PM 0x3780..0x3782` from `DM(0x064A)`.
   Write-watch `DM(0x064A)` and exec-watch `0x3782`, gated.
2. **Is `0x3b20` teardown?** It runs 88,681 cycles earlier unpinned, right as the
   page falls back — which is what a teardown handler would do. If so, the pinned
   run is watching the page tear itself down while we hold it open, and the real
   blocker is whatever makes it decide to finish.

```bash
tools/eicon_loopback.py --native-mips --seconds 20 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_WATCH_OVERLAY=0x0266,0x0267 \
    --answerer-env EICON_WATCH_PM=0x2929,0x2500 \
    --capture-dir artifacts/loopback-lowspeed/fill-w-unpinned
```

## Session 188v: bit 0 of `DM(0x0554)` dispatches the clear — and the two runs reach it for opposite reasons

### Which bit

`PM 0x3782`'s prologue is `PM 0x3813`: table `DM(0x0ADD)`, `CNTR = 4`. Its four
entries, from `0x0266`'s own shipped DM block:

```text
bit 0   DM(0x0ADD) = 0x3b20    <-- the clear
bit 1   DM(0x0ADE) = 0x3b28
bit 2   DM(0x0ADF) = 0x3b6e
bit 3   DM(0x0AE0) = 0x3b29
```

`0x3b20` is bit 0, and it appears in **no other dispatcher table** — the other
six (`0x0A95`, `0x0AA1`, `0x0AB0`, `0x0ABD`, `0x0ACD`, `0x0AE1`) do not contain
it. So there is exactly one way to reach the clear.

### Which word

```text
377f  AX1 = DM($0554)
3780  AY1 = DM($064A)
3781  AR = AX1 XOR AY1
3782  IF NE CALL $3813
```

**`DM(0x0554)` is the state word and `DM(0x064A)` its shadow.** Both are inside
the parameter block based at `DM(0x054C)`: `0x0554` is field `0x08` and `0x064A`
is field `0xFE`, so the record unpacker at `PM 0x2CEE` can write either of them.

### The two runs reach the same handler for opposite reasons

Write-watch on `DM(0x064A)`, gated, neither run host-bound:

```text
unpinned
  cyc 78,801,811   = 0x0000   by PM 0x3b89   page init
  cyc 78,818,623   = 0xffff   by PM 0x2cfa   <-- the runaway unpacker
  cyc 78,819,580   = 0xff80   by PM 0x2cfa   <-- the runaway unpacker
  cyc 78,827,114   = 0x1a03   by PM 0x3813   the shadow update itself
  at the compare:  ax1=0x1a03  ar = 0x1a03 XOR 0xff80 = 0xe583
                   mask = ar AND ax1 = 0x0003 -> bits 0 and 1 dispatched

pinned
  cyc 78,801,811   = 0x0000   by PM 0x3b89   page init
  cyc 78,915,795   = 0x0001   by PM 0x3813   the shadow update itself
  at the compare:  ax1=0x0001  ar = 0x0001 XOR 0x0000 = 0x0001
                   mask = 0x0001 -> bit 0 dispatched
```

**Unpinned, the clear is dispatched off a corrupted shadow** — `PM 0x2cfa` is the
runaway record unpack (188k), and `DM(0x064A)` is field `0xFE`, one more address
it scribbles alongside `DM(0x0571)`. The mask `0xe583` is nonsense and bit 0 is
in it by accident.

**Pinned, the clear is dispatched by a clean single transition** — the shadow is
only ever `0x0000` then the update, and `DM(0x0554)` reads `0x0001`. Bit 0 went
from clear to set and its handler ran, exactly as designed.

So 188u's "the fill is not the pin's doing" is right about *occurrence* and
incomplete about *cause*: the same handler runs in both, for entirely different
reasons. Only the pinned run's dispatch is legitimate, which makes the pinned
run the one worth reasoning from.

### `0x3b20` is initialisation, not teardown

```text
3b20  AR = $FFB2
3b21  AX1 = $FFB0
3b22  AY0 = DM($05C0)
3b23  AF = AY0
      ...falls through to
3b28  CALL $3B73        <-- the 1,744-word clear of PM 0x2400..0x2ACF
3b29  CALL $2E08
3b2a  CALL $2238
3b2b  AX0 = $01FF
3b2c  DM($038F) = AX0
```

It loads constants, clears the scratch code window, and then calls two more
setup routines. That is a **mode (re)initialisation**, and it corrects 188u's
guess that this might be teardown. The clear is a legitimate part of it.

**What is missing is the re-stage.** `PM 0x1fbb` — the trampoline installer —
filled that window with real code once, at page activation (188u), and the PM
watch on `0x2929` shows it is never written again. So the handler clears the
window as part of re-initialising, and nothing puts the code back.

### Next

1. **What re-stages `PM 0x2400..0x2ACF`, and why does it not run here?**
   `PM 0x1fbb` is the installer; find its caller and what gates it. If a mode
   re-init is supposed to re-run it, the gate is the bug. `EICON_WATCH_PM` on a
   word inside the window plus an exec watch on `0x1fba`, gated, in a call that
   reaches the re-init.
2. **`PM 0x3b29`'s `CALL $2E08` and `PM 0x3b2a`'s `CALL $2238`** run *after* the
   clear and are the obvious candidates for the re-stage. Neither writes
   `0x2929` (the watch would have caught it), so check what they do write.
3. The unpinned path needs no further work: its dispatch is downstream of the
   runaway unpack, which is 188k's blocker and already understood.

```bash
tools/eicon_loopback.py --native-mips --seconds 20 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_PIN_PM=0x3805=0x38ab00 \
    --answerer-env EICON_WATCH_OVERLAY=0x0266,0x0267 \
    --watch-dm-writes 0x064a:200 --watch-exec 0x3782:200,0x382a:60 \
    --capture-dir artifacts/loopback-lowspeed/bit0-pinned
```

## Session 188w: `PM 0x1fbb` is the page's overlay unpack, gated on `DM(0x3FB0) == 2` — and the gate is not what stops it re-running

### It is not a bespoke trampoline

188m called `PM 0x1fbb` a trampoline installer because it was seen copying five
words into `PM 0x2909`. It is a **generic descriptor-driven unpacker** with two
engines:

```text
1f90  I4 = $1B00              ; descriptor table in DATA memory
1f92  AR = AR AND AY1, AX1 = DM(I4,M5)
1f93  IF EQ JUMP $1FAB        ; terminator
1f96  I5 = AX0                ; destination
1f98  CNTR = AX1              ; length
1f9b/1f9c   DM -> DM
1fa0/1fa1   DM -> PM
1fa6..1fa9  DM -> PM, two words per entry with PX carrying the low byte

1fb0  I4 = $0900              ; descriptor table in PROGRAM memory
1fb2  AR = AR AND AY1, AX1 = PM(I4,M5)
1fb6  I5 = AX0
1fb7  CNTR = AX1
1fba/1fbb   PM -> PM          ; <-- what staged PM 0x2909 and 0x2929
1fbe/1fbf   PM -> DM
1fc5  IF NE JUMP $1FB2        ; next descriptor
1fc6  RTS
```

The tables are `DM 0x1B00` — one of `0x0266`'s own shipped DM blocks, 1,280
words — and `PM 0x0900`, in the resident region. So the five words at `0x2909`
are one descriptor among many; the first copy observed in the V.32 window is
`i4=0x0903 i5=0x1c61 cntr=0x76`, 118 words to `0x1c61`.

### The gate, and what the routine actually is

```text
1f82  AY0 = DM($3FC1)
1f83  AR = $0100
1f85  DM($3FC1) = AR          ; set bit 8 -- the page-request ready flag
1f86  AY0 = DM($3FB0)
1f87  AR = $0002
1f88  AR = AR - AY0
1f89  IF NE RTS               ; <-- THE GATE: only when DM(0x3FB0) == 2
1f8b  AX1 = $0013
1f8c  DM($3FB0) = AX1         ; request page 19
1f8e  DM($3F04) = 3
1f90..1fc6                    ; both unpacks, then RTS
```

So this is **the partial-overlay request**: it raises the page-request flag, and
if the current bootpage is 2 (V.32) it asks for page 19 and unpacks the
descriptor tables. That is exactly the sequence 188h recorded from the outside
(`bootpage = 2` → `bootpage = 19 partial requested` → `bootpage = 2 partial
served`), now with the code behind it.

Its two callers are both gated on the same word:

```text
1e90..1e93   AY0 = DM($3FB0); AR = 2 - AY0; IF EQ JUMP $1F82
1de5..1de9   AX0 = $0002; IF EQ JUMP $1DEA; AX0 = $0001;
             AR = AX0 - AY0; IF NE JUMP $1F82
```

### Measured: entered once, and the gate is open afterwards

Gated to `0x0266,0x0267`, not host-bound:

```text
PM 0x1f82 (routine entry)   1 execution in the whole V.32 window
PM 0x1f8c (past the gate)   1

DM(0x3FB0) writes
  cyc 78,782,361  = 0x0013  by PM 0x1f8c   request page 19
  cyc 78,787,789  = 0x0002  by PM 0x1dea   restored once the partial is served
  cyc 78,936,215  = 0x0000  by PM 0x1c29   the post-stall scribble (188o)
```

**This is the answer, and it is not the one the question expected.** From
cyc 78,787,789 onward `DM(0x3FB0)` is back to `2`, so `PM 0x1F89`'s gate *would*
pass on a second entry. The unpack does not re-run because **nothing ever calls
`PM 0x1F82` again** — one entry in 165,000 cycles of page-2 residency.

So the missing re-stage after `0x3b20`'s clear is not blocked by a condition
that has gone false. The re-init path simply does not route back to the unpack,
and the unpack has no other trigger.

### Next

1. **`PM 0x2E08` and `PM 0x2238`** — called at `0x3b29`/`0x3b2a`, immediately
   after the clear, and the only remaining candidates for the restore. Neither
   writes `0x2929` (188u's watch would have caught it). Trace the re-init frame
   with `EICON_TRACE_FRAMES` and see what they do write.
2. **Does the re-init have any path to `PM 0x1F82`?** A gated exec watch on
   `0x1de9`, `0x1e93` and `0x1f82` over a pinned call answers it directly; 188w
   only counted `0x1f82`.
3. If neither, the reading becomes that `0x3b20` should not be dispatched in this
   state at all — which puts the weight back on `DM(0x0554)` bit 0 (188v) and on
   what sets it.

```bash
tools/eicon_loopback.py --native-mips --seconds 20 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_PIN_PM=0x3805=0x38ab00 \
    --answerer-env EICON_WATCH_OVERLAY=0x0266,0x0267 \
    --watch-exec 0x1f82:40,0x1f8c:20 --watch-dm-writes 0x3fb0:60 \
    --capture-dir artifacts/loopback-lowspeed/gate
```

## Session 188x: neither post-clear routine touches program memory — there is no restore

### What they write

Both disassemblies check out against the opcodes actually executed in frame
24412 (0 of 27 addresses differ for `0x2E08`, 0 of 7 for `0x2238`), so the dump
is trustworthy for these two.

```text
2e08  CALL $2F40                     ; PM 0x2E08, called at 0x3b29
2e0c  AX0 = DM($3FC4)                ; reads the card capability word
2e14  I0 = DM($05C9)
2e17  CNTR = $0006
2e18  DO $2E1B UNTIL NOT CE          ; the six-entry scan
2e19    CALL $2FB8
2e1d  AX0 = DM($3EE2)
2e22  DM($05C4) = AR                 ; <-- writes
2e23  DM($05C3) = AR                 ; <-- writes
2e24  RTS

2238  I4 = $2B4F                     ; PM 0x2238, called at 0x3b2a
2239  DM($0619) = I4                 ; <-- writes
223a  I4 = $2B48
223b  DM($0618) = I4                 ; <-- writes
223c  AX0 = $7FFF
223d  DM($0153) = AX0                ; <-- writes
223e  RTS
```

**Five data words between them and not one program-memory store.** `0x2E08`
computes a status word out of `DM(0x3FC4)`, `DM(0x05C9)` and `DM(0x3EE2)`;
`0x2238` installs three constants.

Two things worth noting. `0x2E18..0x2E1B` with `I7 = $17F3` is the
**read-database scan** — Session 115's `CALL (I7)` seam and 188c's stack
saturation both live here, and it turns out to be called from this re-init.
And `0x2238`'s two pointers, `0x2B48` and `0x2B4F`, are read back as indirect
call targets (`I4 = DM($0618)` at `PM 0x30A7`, `I4 = DM($0619)` at `PM 0x22C2`)
and lie **outside** the cleared spans — so the re-init *does* re-point one
dispatch at code that survives.

### There is no restore, measured across the window

`EICON_WATCH_PM` on six words spread across the staged region, whole call:

```text
cyc 78,785,250  PM 0x2909: 0x403008 -> 0x38ae60  by PM 0x1fbb   staged
cyc 78,785,314  PM 0x2929: 0x510971 -> 0x1ecb5f  by PM 0x1fbb   staged
cyc 78,915,832  PM 0x2400: 0x1f528f -> 0x000000  by PM 0x3b7b   cleared
cyc 78,916,279  PM 0x2800: 0x400300 -> 0x000000  by PM 0x3b83   cleared
cyc 78,916,544  PM 0x2909: 0x38ae60 -> 0x000000  by PM 0x3b83   cleared
cyc 78,916,576  PM 0x2929: 0x1ecb5f -> 0x000000  by PM 0x3b83   cleared
cyc 78,916,998  PM 0x2acf: 0x845b81 -> 0x000000  by PM 0x3b83   cleared
```

Staged once, cleared once, **never written again by anything**. Combined with
the two disassemblies, the re-init has no restore step at all.

### Correction: the fill is 1,158 words in two spans, not 1,744

`PM 0x2600` was watched and **never written**, which exposed an error. Measured
from the trace rather than assumed:

```text
PM 0x3b7b   438 stores   I5 0x2400..0x25B5
PM 0x3b83   720 stores   I5 0x2800..0x2ACF
gap         0x25B6..0x27FF, 586 words, untouched
```

188r, 188u and 188v all say "1,744 words" and describe the span as
`0x2400..0x2ACF`. That was `0x2ACF - 0x2400 + 1` computed from the two endpoints
instead of measured — the two fills are **disjoint** and total **1,158** words.
Nothing else changes: `0x2929` is inside the second span, so the erased routine
and everything downstream stand.

### Where this leaves it

`PM 0x353F` is a **direct** `CALL $2929`, not an indirect through a DM pointer,
so unlike `DM(0x0618)`/`DM(0x0619)` it cannot be re-pointed at run time without
a program-memory write. The re-init clears the code it calls, re-points a
*different* dispatch at surviving code, and never restores the window. So either

* the frame handler is not supposed to run after this re-init — which puts the
  weight back on `DM(0x0554)` bit 0 (188v) and what sets it; or
* the re-init is incomplete in this configuration and a restore step is being
  skipped, in which case the thing to find is what would have called `PM 0x1F82`
  a second time (188w: the gate is open, nothing calls it).

### Next

1. **Who reads `DM(0x05C3)`/`DM(0x05C4)`**, the two words `0x2E08` computes? If
   they gate the frame handler, the first reading is testable directly.
2. **`PM 0x2F40` and `PM 0x2FB8`**, the two routines `0x2E08` calls, are the last
   unexamined part of the re-init.
3. Everything about the fill's *extent* in 188r/188u/188v should be re-read with
   the correction above.

```bash
tools/eicon_loopback.py --native-mips --seconds 20 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_PIN_PM=0x3805=0x38ab00 \
    --answerer-env EICON_WATCH_OVERLAY=0x0266,0x0267 \
    --answerer-env EICON_WATCH_PM=0x2400,0x2600,0x2800,0x2909,0x2929,0x2acf \
    --capture-dir artifacts/loopback-lowspeed/restore
```

## Session 188y: nothing on this page reads them — and the clear is the *optional* half of the configure step

### No readers

Gated to `0x0266,0x0267`, `--watch-dm 0x05c3:80,0x05c4:80` (reads **and**
writes), not host-bound. Six lines fired against a budget of 160, so this is the
complete picture and not a spent limit:

```text
DM(0x05C3)   w PM 0x3b89 = 0x0000   (page init)
             w PM 0x2e23 = 0x0ff9   x2
DM(0x05C4)   w PM 0x3b89 = 0x0000   (page init)
             w PM 0x2e22 = 0x0ff9   x2
```

**Zero reads.** The status word `PM 0x2E08` computes is written and never
consulted while page 2 is resident, so it does not gate the frame handler and
188x's first reading is not testable this way.

Readers do exist — a static scan finds them in the resident image at
`PM 0x0c44..0x0da5` and mirrored in the overlay at `PM 0x2d6b..0x2ecc` — but
none of them execute on this page. So `0x2E08`'s output is a value computed
**for whatever runs next**, not for page 2's own use. That is a hand-off, and it
softens 188v's confident "initialisation, not teardown": the configure step's
product is for someone else.

### The clear is optional, and the same configure code has two entries

Counting the pieces, gated:

```text
PM 0x3b20  1 execution   ret=0x382b   cyc 78,915,815   (bit 0 handler)
PM 0x3b73  1 execution   ret=0x3b29   cyc 78,915,824   (the clear)
PM 0x2e08  2 executions  ret=0x3b2a   cyc 78,802,740 and 78,917,001
PM 0x2238  2 executions  ret=0x3b2b   cyc 78,802,925 and 78,917,186
```

`0x2E08` and `0x2238` run **twice**, both times from `0x3b29`/`0x3b2a` — but the
clear runs **once**. So the first pass reached `0x3b29` without going through
`0x3b28`, and the table from 188v says exactly how:

```text
bit 0  -> 0x3b20   loads constants, falls into 0x3b28: CALL $3B73 (the clear),
                   then 0x3b29 CALL $2E08, 0x3b2a CALL $2238
bit 3  -> 0x3b29   CALL $2E08, CALL $2238 -- the same configure, no clear
```

**Bit 3 is the non-destructive entry and bit 0 is the destructive one.** At page
activation (cyc 78,802,740) bit 3 ran and the window was left alone; at
cyc 78,915,815 bit 0 ran and wiped it. Both produce the same `0x0ff9`, so the
configure half is idempotent — the only difference between the two paths is
whether `PM 0x2400..0x25B5` and `PM 0x2800..0x2ACF` get cleared first.

### Where this leaves it

The blocker is now a single, well-posed question: **why is bit 0 dispatched the
second time rather than bit 3?** The firmware has a path that does the same
configuration without destroying the code window, and it took it once already.

That puts everything back on `DM(0x0554)` (188v) — specifically on what sets bit
0 versus bit 3, and whether bit 0 is meant to be set at all in a call that is
still training. Recall the two dispatches differ in their masks:

```text
cyc 78,802,7xx   bit 3 -- non-destructive
cyc 78,915,815   bit 0 -- destructive, mask 0x0001 from DM(0x0554)=0x0001
```

### Next

1. **Write-watch `DM(0x0554)`**, gated, for the whole page-2 window. 188v only
   watched the shadow `DM(0x064A)`; the state word itself has not been watched,
   and `0x0554` is field `0x08` of the parameter block, so the unpacker is one
   candidate writer among others.
2. **What set bit 3 the first time?** The same watch answers both, and the two
   together say whether bit 0 replaces bit 3 or is added to it.
3. `PM 0x2F40` and `PM 0x2FB8` remain unexamined, but they are inside the
   configure half, which is idempotent and runs on both paths — so they are
   unlikely to matter to the clear.

```bash
tools/eicon_loopback.py --native-mips --seconds 20 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x6000@0x025f \
    --answerer-env EICON_PIN_PM=0x3805=0x38ab00 \
    --answerer-env EICON_WATCH_OVERLAY=0x0266,0x0267 \
    --watch-dm 0x05c3:80,0x05c4:80 \
    --watch-exec 0x3b20:10,0x2e08:10,0x2238:10,0x3b73:10 \
    --capture-dir artifacts/loopback-lowspeed/reinit
```

## Session 189: the card's own firmware runs T.30 — the fax protocol row reaches it, and the DSP side does not follow

Target: send a fax. The lab Asterisk has `ReceiveFAX` on 3000 and `SendFAX`
on 3099, both G.711 audio with no T.38, so the passthrough media path here is
adequate and 3099 is a known-good T.30 sender to point at the card.

### The FAX page selects, and asks for a partial nobody served

`EICON_FORCE_DM=0x3FC4=0x0800@0x025f` — the Session 184 selector, listed there
as untried — writes `DM(0x0491)=0x0004` at the classifier (PM `0x3bfb`) and
loads overlay `0x0262`. Page 4 is reached for the first time.

The page then posts bootpage **16** with `DM(0x3132)=0x0265`, the FAX.F34
Partial, and waits. `_service_partial_overlay()` recognised only bootpage 19,
so the request fell through to the whole-page path, which looked page 16 up in
the shared boot word and read `0x0a2f`:

```text
3fb0=0010 from PM 1dbc        bootpage 16
0491=0a2f from PM 1dc9
3131=0001, 3132=0265          the FAX partial
[adsp] shared boot word 16 low-level/FAX partial -> 0x0a2f (2607);
       no valid overlay page
```

Page 16 is a pseudo-page exactly like 19. `0x0265` was never missing — it is
in file set 5 already. With 16 treated as a marker the partial lands on both
ends, and the answerer runs to 24.5 s instead of dying at 3.4:

```text
partial 0x0265: 10 DM blocks, base 0x0262 has 17 recorded;
                holding back 0x2276(3),0x2280(332),0x3fb2(2)
partial overlay 0x0265 applied to 0x0262 at sample 24767
```

It still does not train. After the partial the page hands straight back:
`bootpage 6 V.8 -> 12 AT online`, `TrnProgress 0x0009 -> 0x0000`, then DIAL.
It loads and has nothing to do — which the rest of this session explains.

### T.30 is in the firmware, not missing from this project

The first read of this was that T.30 would have to be written. It does not.
The protocol map's fax row (`tty_module/isdn.c:273`) is

```c
{"FAX", "", ISDN_PROT_FAX, 0, DI_FAX3,
 B2_T30_i, B2_T30_o, B3_T30,
 {6, B1_T30, 0, 0, 0, MAX_PACK_LO, MAX_PACK_HI} },
```

and `B1_T30` is `0x10` in the same list the DSP CAI hardware ids come from —
`MODEM_a` is `B1_MODEM_a` (0x11), `MODEM_s` is `B1_MODEM_s` (0x12). `T30_INFO`
is a host↔card message and the EDATA set (`DIS`/`FTT`/`MCF` out,
`DCS`/`TRAIN_OK`/`EOP`/`MPS`/`EOM` in) is the card reporting its phase. The
card drives phases A–E; the host supplies parameters once and then exchanges
page data. divas4linux carries the whole host side in `fax.c`/`fax1.c`/
`fax2.c`.

Use `divacapi.h:789` for the layout, **not** `tty_module/t30.h`: the copy
there is inside an `#if 0` and is missing `resolution_high`, which would put
every field after it one byte out.

### The CAI alone does not select it

`EICON_B1_RESOURCE=0x10`, confirmed on the wire as `res=0x10`:

```text
bootpage 6 V.8 -> 7 INFO -> 8 V.34
DM(0x3FC4): b13f -> 310f -> 1000
```

V.8 completed and took its default branch, exactly as for a modem call. A
clean negative, and cheap: it says the fax setup is an NL/B3 thing.

### The whole row reaches the T.30 engine

`EICON_FAX=1` sends `B1_T30` in the CAI *and* the fax NL ASSIGN
(`isdn.c:1567`) — LLC `03 06`, `dlc_def` `5a 08`, and an NLC holding the
`T30_INFO`. The ASSIGN is accepted, `N_CONNECT` is accepted, and the card
answers:

```text
IND 0x04 Id=0x03 payload=0106000000000000000000000000 14 00 ...
```

`0x04` is `N_DISC`, and the payload is a `T30_INFO` coming back, where `code`
is the T.30 result. `code = 0x01` is `T30_ERR_NO_ENERGY_DETECTED`;
`rate_div_2400 = 6` is 14400. **The firmware accepted the fax protocol row,
brought its T.30 engine up, listened for a fax, heard nothing, and tore the
call down in T.30 terms.** Nothing in this project had previously got the card
to answer as a fax at all.

The silence is not mysterious. The ADSP was off running its usual tower —
`TrnProgress 0x00b0`, and `service_assign=1 switch_on=1`, which is the
*modem* DSP path — while the T.30 engine waited on a bearer that was not
carrying fax. Signalling is fax; the DSP side is not.

### Next

1. **Find what the fax ASSIGN should have changed on the DSP side and did
   not.** `--watch-exec` on the service-assign path, a fax assign against a
   modem one. This is the join with the page-4 work above: once the card asks
   for page 4 itself, the partial loader is already in place.
2. **Do not chase `0x3FC4` for this.** It is the V.8 classifier's lever and a
   fax call never reaches that classifier; page 4 has to arrive through the
   assign.
3. **Then 3099 → the card**, not the card → 3000. `SendFAX` is a known-good
   T.30 sender, so a failure is unambiguously ours; originating is the harder
   direction and is worth second.

```bash
# the page-4 partial
tools/eicon_loopback.py --native-mips --seconds 40 \
    --caller-env EICON_FORCE_DM=0x3FC4=0x0800@0x025f \
    --answerer-env EICON_FORCE_DM=0x3FC4=0x0800@0x025f \
    --watch-dm-writes 0x3131,0x3132,0x0491,0x3FB0 \
    --capture-dir artifacts/loopback-fax/page4-partial

# the fax protocol row
tools/eicon_loopback.py --native-mips --seconds 30 \
    --caller-env EICON_FAX=1 --caller-env EICON_FAX_STATION_ID=5551000 \
    --answerer-env EICON_FAX=1 --answerer-env EICON_FAX_STATION_ID=5552000 \
    --watch-dm-writes 0x3131,0x3132,0x0491,0x3FB0,0x3FC4 \
    --capture-dir artifacts/loopback-fax/nl-t30
```
