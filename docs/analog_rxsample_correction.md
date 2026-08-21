# RXSAMPLE is written, the detector fires, and the CM branch loses a race

This corrects the closing section of `docs/analog_v8_oracle.md`
("Why the Analog side does not progress: RXSAMPLE is never written") and the
plan that followed from it. The premise is false under
`--caller-kernel-dispatch`, so the "give analog109 a real SPORT1 kernel-driven
receive path" work was **not** started — it would have fixed something that
already works.

All measurements below: `--answerer-firmware-set pri117 --answerer-modulation
v90 --caller-firmware-set analog109 --caller-modulation v90a
--caller-kernel-dispatch --analog-codec-rate 9600 --seconds 20`.

## RXSAMPLE_0..5 is filled by the page, not the kernel

`PM 0x173A` in `V8.ANA` — and at the same address in `INFO.ANA`, `V34.ANA`,
`DIAL/FSK/FAX.ANA` and `HV34.ANA`, so it is shared library code linked into
every overlay:

```text
173a: CNTR = DM($3F67)      ; 3, 4 or 5 -- the guide's "the kernel writes N samples in"
173b: I0 = $3F30            ; RXSAMPLE_0
173c: L7 = $0014            ; a 20-word circular receive ring
173d: I7 = DM($376C)        ; ring read pointer
173e: DO $1740 UNTIL NOT CE
173f:   AX1 = DM(I7,M5)
1740:   DM(I0,M1) = AX1     ; -> RXSAMPLE_n
1741: DM($376C) = I7
```

The ring is produced at `PM 0x178F` from an upstream 0x40-word buffer through an
interpolator at `PM 0x17AE`. **Neither the Analog kernel (`0x000d`) nor
`TIKRNL81.ANA` references `0x3F30..0x3F35` at all** — a scan of every image in
the build-109 set for that immediate finds hits only in overlays. The guide's
wording is about who feeds the ring, not about who fills the array.

Exec watches confirm the whole chain runs live: `PM 0x178F` (ring producer),
`PM 0x1728` (the `DM(0x36F0)` gate, passed), `PM 0x172E` and `PM 0x173A`.

`--watch-dm-writes 0x3F30:200000` over a whole call: **40,985 writes, 2,630
distinct values.** The array is live, not frozen. `RXSAMPLE_4`/`_5` staying zero
is correct — `DM(0x3F67)` is 4, so only `_0.._3` are written.

> A caution about how the old measurement went wrong, because it nearly caught
> this one too: the first run here used `--watch-dm-writes 0x3F30:400` and saw
> 400 writes of `0x0000`, which looks exactly like a dead array. Those 400
> writes span cycles 39,597–2,227,371 — the first 5% of a call that runs to 47
> million, before the answerer has even joined the bearer. A write-watch limit
> silently samples the *beginning* of a call.

## The ANSam detector fires, well past its threshold

`DM(0x07BD)` is the hysteresis counter, `0x0780` (1920) the threshold that
condition 3 tests. Over one call, 34,557 writes:

| | |
|---|---|
| max | **0x21D6 (8662)** — 4.5× the threshold |
| samples at or above threshold | 7,237 of 34,557 (20.9%) |
| longest consecutive run above | **7,237** — one contiguous block, against the 240 needed |

So the detector is not weak, not frozen and not mistuned. Everything the oracle
inferred from "MR1 falls 10× short" belongs to a configuration that is no longer
the one under test.

## What actually blocks CM: slot 2 always wins

Record `0x01C7` carries both exits, and its fields persist through the CI
retransmit loop (`0x01DC ↔ 0x01EE` carry no destination indices of their own),
so **both are re-evaluated on every pass of the loop**:

| slot | destination | condition | requires |
|---|---|---|---|
| 1 | index 6 → `0x0200` → fall-through → **`0x021B` (CM)** | 3 (`PM 0x37DC`) | `DM(0x07BD) >= 0x0780` **and** `DM(0x0778) >= 0xF0` |
| 2 | index 5 → `0x0281` → … → `0x031D` (no CM) | 2 (`PM 0x37F7`) | `DM(0x07BD) >= 0x0780` |

The dispatcher runs slot 1 first, so slot 1 gets the first look — but it needs a
second counter, `DM(0x0778)`, at 240, and condition 3 *clears that counter
itself* whenever the detector is below threshold (`PM 0x37DD` → `PM 0x37D4` →
`PM 0x3ED1..0x3ED3` writes `M0` to `DM(0x0776..0x0778)`). Slot 2 needs only the
threshold. So on the first pass where the detector crosses, slot 1 fails on a
counter that has just been released from zero and slot 2 fires immediately.

Measured: `DM(0x0778)` takes only the values 0 and 1 across 9,190 writes, and
never approaches 240. The watch's own PC history at the loop exit shows exactly
this — condition 3 evaluated, first gate passed (`37dd → 37de`, not the reset
path), second gate failed, then condition 2 taken:

```text
37a6 37a7 37a8 37a9  37dc 37f7..37fa 37dd 37de 37df 37e0 37e1  37aa   <- slot 1, not taken
37ab 37ac 37ad       37f7..37fa                                37ae   <- slot 2, taken
378b  DM($049F) = 0x0281
```

## Correction: the detector is fast. A second detector is the blocker

An earlier revision of this file said the detector took ~14 s to respond,
converting cycles to seconds linearly. **That conversion is invalid** -- the
emulator runs the DSP only for the cycles each frame consumes, so cycles are not
proportional to call time. Against the log's own `[adsp] sample N (T s)` stamps:

- The answerer joins at 2.0 s and ANSam is on the line **2.5-7.0 s** (measured by
  Goertzel on `caller.rx.wav`: 2100 Hz present, bouncing with the 15 Hz envelope
  and the 450 ms reversals). After 7.0 s the line carries a constant rms 2554
  with no tone at 2100/1800/2225 Hz.
- `DM(0x07BD)` first crosses its threshold at caller sample 22,240 = **2.78 s**,
  0.28 s after ANSam starts.

So detector A responds promptly and correctly. There is no latency anomaly.

**Condition 3 gates on two independent detectors, and the second one fails.**

| | level | threshold | counter | condition 3 needs |
|---|---|---|---|---|
| A, the ANSam discriminator | `DM(0x07BC)` | `DM(0x0748)` = 2000 | `DM(0x07BD)` | >= 0x0780 (1920) |
| B | `DM(0x0777)` | `DM(0x0747)` = 200 | `DM(0x0778)` | >= 0xF0 (240) |

Both thresholds are *record fields* -- offsets 0x09 and 0x08 -- and record
`0x0194`, which is on the caller's walk, sets `DM(0x0747) = 0x00C8` and
`DM(0x0748) = 0x07D0`.

Detector A: max 8662, 4.5x its threshold. Detector B, measured over one call:

```text
DM(0x0777) smoothed level : n=9,128   median 0   mean 3   max 1778
                            above the 200 threshold in 26 samples -- 0.3%
DM(0x0776) instantaneous  : n=43,680  median 32  mean 1752  max 28128
```

So B's input is present but violently bursty, and its leaky integrator
(`new = 4*DM(0x0776)^2 + 0.95*DM(0x0777)`, fractional MACs, `PM 0x3EFE..0x3F08`)
never sustains. `DM(0x0778)` consequently only ever holds 0 or 1.

`DM(0x0778)` has two maintainers and only one is live: `PM 0x3804..0x380A`
**clamps it at 0x30 (48)**, so that path could never satisfy a `>= 240` test at
all, and an exec watch confirms it never runs. The live path is the unclamped
`PM 0x3F0C..0x3F14`, driven by detector B.

Detector B is a coherent complex-magnitude detector: `PM 0x39BD` runs a 14-tap
FIR with stride 2 over a 29-word circular history at `DM(0x073E)` to form the
quadrature component in `MX1` against the in-phase `MX0`, and `PM 0x3EE0..0x3EE4`
publishes `(MX0^2 + MX1^2) << 5` as `DM(0x0776)`. A Hilbert pair needs a
*contiguous* sample history; a burst-filled one produces exactly the spiky
magnitude measured.

## Localised to one biquad, and to a reset that outruns it

Splitting the `DM(0x0776)` writes by storing PC, **during the ANSam window only**
(2.5–7.0 s), settles where the signal dies:

| storing PC | what it is | n | median | max |
|---|---|---:|---:|---:|
| `PM 0x3EE4` | raw `(MX0² + MX1²) << 5` | 10,000 | **7,648** | 28,128 |
| `PM 0x3EF9` | after the biquad at `PM 0x3F1D`, table `PM 0x3D10` | 667 | **0** | 3,816 |
| `PM 0x3ED1` | the reset, writing zero | 691 | 0 | 0 |

The integrator squares the *filtered* value, not the raw magnitude — `PM 0x3EF5`
overwrites `DM(0x0776)` with the filter output before `PM 0x3EFE` runs. For the
integrator to clear its 200 threshold the filter output needs `|x| >= 905`
(`4 · 2 · (x/32768)² · 32768 >= 200`). Its median is 0.

So B's front end is healthy — the Hilbert magnitude is strong and steady — and
**one biquad takes 7,648 to 0**. Table `PM 0x3D10` is `SE=0x0001`, `b = -464, 0,
+464`, `a = +15460, -31500`: a bandpass with `b0 = -b2`, running not on the line
signal but on the *magnitude envelope*, at the decimated rate (667 evaluations
in 4.5 s ≈ 148 Hz). On that reading it is looking for ANSam's 15 Hz amplitude
modulation, which is what distinguishes ANSam from plain ANS.

Second, independent, finding: **the reset runs as often as the filter.** 691
resets against 667 filter evaluations *inside* the ANSam window. `PM 0x3ED1`
clears `DM(0x0776..0x0778)` together and is reached from condition 3's
`PM 0x37D4` whenever detector A is below threshold — and A, though healthy, is
below threshold 27.6% of the time during ANSam, because ANSam reverses phase
every 450 ms and a narrowband detector collapses at each reversal. B is required
to hold 240 *consecutive* evaluations. It never gets a clear run.

### The bench, and what it found

`tools/v8_envelope_filter_bench.py` drives `PM 0x3F1D` in the real emulator with
a chosen coefficient table, measuring the impulse and frequency response rather
than relying on a reading of the coefficients. `tests/test_v8_envelope_filter.py`
pins it.

**A warning about the instrument, because it nearly produced a false finding.**
The first version reported *"the filter is dead"* for every table, including the
ones already exonerated — which is the only reason it was caught. Two faults,
both in the bench's own hand-encoded driver stub: `SR1 = AR` had source and
destination reversed, and the DAG setup wrote `M4`/`L4` where it meant
`M0`/`M1`/`L0`, on the wrong register bank. The stub now lifts the real call
site `PM 0x3EF5..0x3EF9` verbatim and substitutes only address fields. Always
run a known-good table through this bench before believing a null result.

With that fixed:

- **The emulator's arithmetic on this path is sound.** Predicting the sine
  response from the impulse response holds across the band (ratios cluster
  0.85–1.2, integer peak-picking accounting for the spread). A bad shift or a
  misplaced saturation would break linearity, and it does not. **This retires
  the `ASHIFT`/`SE` suspicion `docs/analog_v8_oracle.md` raised, for table
  `0x3D10`** — the one its own sweep never covered.
- **The bench agrees with the live card.** Live, filter input median 7,648 gives
  output median 0. On the bench a steady 7,648 gives a median of 44. Both are
  effectively nothing, from the same input, so the bench is measuring the same
  thing the call is.
- **The shortfall is about 8×, and it is not the reset.** Fed by hand:

  | input to the filter | median \|output\| | clears 905? |
  |---|---:|---|
  | steady 7,648 | 44 | no |
  | 7,648 with 50% zeros (the reset's effect) | 44 | no |
  | 15 Hz AM, ±20%, on 7,648 | **108** | no |
  | 15 Hz AM with 50% zeros | 50 | no |

  So the `PM 0x3ED1` reset punching holes in the input stream is **not** what
  starves the integrator — an unbroken input does no better. And a textbook
  ANSam envelope, ±20% at 15 Hz, yields 108 against the 905 needed.

  This also corrects a mid-analysis error: a DFT of the impulse response
  suggested a large DC gain, but the response has not decayed within 512
  samples, so that figure was a truncation artefact. The steady-state numbers
  above are what the filter actually does.

### What has not been established

**No defect in this project's code has been identified.** The emulator is
exonerated on this path, the reset is exonerated as the starving mechanism, and
the filter is doing something consistent and linear. What is left is a
quantitative gap of about 8x between what the filter yields from a nominal ANSam
envelope (108) and what the integrator needs (905).

Two readings remain, and they are distinguishable by measurement:

1. **The input's AC content is too small.** The filter keys on envelope
   *variation*, and the bench says a +-20% 15 Hz AM is not enough. If the real
   ANSam the answerer emits is shallower than V.8 SS7.2 requires, or if the
   receive chain has already smoothed it, that is a harness fault with a clear
   target. Measure the actual modulation depth of `DM(0x0776)` inside the ANSam
   window and compare against +-20%.
2. **This detector is not meant to fire on ANSam at all**, and condition 3's
   second gate is waiting for something else entirely -- in which case the
   caller is right not to build a CM here, and the question moves back up to
   which V.8 state the caller should be in. Note `DM(0x0747)` is a *record
   field*, so a state that wants this detector to fire can set a lower
   threshold; record `0x0194` chose 200.

The bench makes either one cheap to test, which it was not before.

## Measured: the input is correct, so reading (1) is disconfirmed

**On the wire the answerer's ANSam is right.** Complex-demodulating
`caller.rx.wav` at 2100 Hz over the 2.6–6.9 s window and taking the envelope:

```text
envelope mean 1448   stdev/mean 0.146
15 Hz component: amplitude 259, depth 0.179   <- V.8 S7.2 requires 0.20
strongest component in the whole band: 15.0 Hz
```

Depth 0.179 against a required 0.20, the shortfall being my crude
moving-average envelope detector rather than the signal. The 15 Hz modulation is
the strongest thing in the envelope. The answerer is emitting a proper ANSam.

**And the modulation survives into the detector.** `DM(0x0776)` during the same
window has mean 8,174, stdev/mean **0.235** and peak-to-peak/mean 2.17 — a
signal carrying roughly the ±20% variation the wire has.

> A caveat on one number that did not survive scrutiny: resolving `DM(0x0776)`'s
> 15 Hz *component* gave a depth of 0.009, which would have been a 20× loss and
> a tidy harness fault. It is not trustworthy. Per-write timestamps for a DM
> watch come from the sparse `[adsp] sample N (T s)` lines, so the time axis is
> stepwise and the frequency axis derived from it is not reliable — which is
> also why a spurious "strongest component at 87.5 Hz" appeared. The
> stdev/mean figure above needs no time axis and is the one to trust.

So the detector's input is not starved of modulation. **Reading (1) is
disconfirmed**, and with it the last hypothesis that pointed at this project's
code.

## What that leaves: reading (2)

Every link in the chain is now measured and correct on its own terms:

| stage | measured | verdict |
|---|---|---|
| answerer's ANSam on the wire | 15 Hz at depth 0.179 | correct |
| Hilbert magnitude `DM(0x0776)` | mean 8,174, stdev/mean 0.235 | correct |
| biquad `PM 0x3F1D` / table `0x3D10` | LTI; ±20% AM at 15 Hz → 108 | consistent |
| integrator threshold | needs ≥ 905 | never reached |

A correct ANSam, correctly received, produces about an eighth of what this
detector's threshold requires. Since the emulator, the reset, the resampler, the
receive path and the input signal are each now excluded, the remaining reading
is that **detector B is not an ANSam detector at all** — condition 3's second
gate is waiting for something else, and the caller is right not to build a CM in
this state.

That moves the question back up a level, to the one `docs/v8_script_records.md`
left open: which V.8 state the caller should be in. `DM(0x0747)` is a *record
field*, so a state that intends this detector to fire sets its own threshold;
record `0x0194` chose 200 and the caller has been sitting on that choice since
the start of the call. The next thing to establish is what `DM(0x0776)`'s chain
responds to strongly — sweep it on the bench the way
`docs/analog_v8_oracle.md` swept the other detector — and then find which record
sets a threshold that signal would cross.

## Found it: the detector chain runs 4x too slow

Reading (2) said detector B might not be an ANSam detector. It is one — swept
properly, everything lines up, and the fault is a rate.

**The magnitude stage is a plain envelope detector.** Sweeping a tone through
`PM 0x3EDE` (input `DM(0x03A3)`, gated by `DM(0x3EDA)`) gives a flat, saturated
`(MX0² + MX1²) << 5` from 300 Hz to 3300 Hz. No selectivity there; all of it is
in the biquad, which runs on the *envelope*.

**The biquad's passband is at 0.0225 cycles/sample** — measured with a `dc +
ac·sin` input, which is what it actually sees, rather than a bare sinusoid that
swings through zero:

```text
0.0100  ->  520      0.0225  -> 1524   <- peak
0.0150  -> 1018      0.0300  ->  868
0.0200  -> 1462      0.0500  ->  368      0.0938 (ANSam, live rate) -> 172
```

**At its passband it produces 1524 — comfortably over the 905 the integrator
needs.** So the detector is not under-gained, mistuned or broken. It is being
evaluated at the wrong rate.

The rate arithmetic closes exactly. `DM(0x07BE)`'s reload is 15:

| `PM 0x3EDE` runs at | biquad rate | 0.0225 cyc/sample sits at | |
|---|---:|---:|---|
| **9,600 Hz** — the codec rate V.8 asks for (`DM(0x3F66) = 4`) | 640 Hz | **14.4 Hz** | ANSam's envelope is **15 Hz** |
| 2,400 Hz — the symbol rate (`DM(0x3F67) = 4`) | 160 Hz | 3.6 Hz | matches nothing |

Live, the chain runs at ~2,326 Hz and the biquad at ~155 Hz, so ANSam's 15 Hz
lands at 0.0938 cycles/sample — deep in the stopband, output 172 against 905.

A 4% match on the first row, against nothing on the second, is the design: the
`÷15` exists to turn 9,600 into 640, at which this filter sits on ANSam. **The
chain is being driven once per symbol where the firmware expects once per codec
sample — a factor of `DM(0x3F67)` = 4.**

That is a defect on this side of the line, it is the first one this
investigation has found, and it predicts the observed shortfall
(172 vs 1524 ≈ the 8× measured earlier) rather than merely being consistent
with it.

### Disconfirmed by direct measurement

**There is no missing factor of 4.** Watching both counters in one run, inside
the ANSam window:

```text
RXSAMPLE_0 writes (once per symbol)         10,221   -> 2,377 Hz
DM(0x0776) writes from PM 0x3EE4 (detector)  9,040   -> 2,102 Hz
ratio 0.88
```

The detector runs in lockstep with the RXSAMPLE fill, once per symbol. That is
the firmware's own cadence, not something the harness imposes, so the 14.4 Hz
coincidence is just that — a coincidence. The rate hypothesis is dead.

**And the CM branch is never taken against an Analog answerer either.** With
`--answerer-firmware-set analog109 --answerer-kernel-dispatch`, the configuration
`docs/handoff.md` records as completing V.8, the caller's walk is identical --
`0x0341 -> 0x0194 -> 0x01BB -> 0x01C7 -> 0x01DC <-> 0x01EE -> 0x0281 -> 0x028D
-> 0x029F -> 0x031D` -- and `DM(0x0778)` still peaks at 1.

That is worth taking seriously as a reframing: **no configuration in this repo
has ever taken the CM branch**, including ones considered working. Either the
branch is genuinely not part of the normal path and V.8 completes through
`0x0281`, or every configuration shares one upstream cause. The pin experiment
argues it matters -- forcing `0x021B` reached `TrnProgress 0x0009`/`0x001f` and
bypassed the `0x002a` park -- but that is the only evidence that it should be
taken, and it is indirect.

### Before changing anything

The remaining question is *where* the factor of 4 is lost — whether the harness
presents one sample per symbol to `DM(0x03A3)`, or the page's own frame code
should iterate `RXSAMPLE_0..3` and is not being given the chance. `PM 0x3EE4`
writes are the counter to watch: they should arrive at 9,600 Hz and currently
arrive at 2,326 Hz. Fixing the wrong one would be a fifth stand-in.

## Where this leaves things

- Do not build the SPORT1 kernel receive path for `RXSAMPLE`. It is written.
- Do not touch the V.8 record layer. `0x01C7` offers the CM branch on every pass
  of the CI loop; the script is behaving correctly given its inputs.
- `docs/analog_v8_oracle.md`'s final section and its "two ways out" are
  superseded by this file. Its earlier sections — the detector variants, the
  level law, the amplitude-blindness result — still stand.
- The open question is narrow and quantitative: **why is `DM(0x0776)` bursty?**
  Its median is 32 against a mean of 1752 — a signal that is right most of the
  time and near-zero the rest, which is the signature of a Hilbert transformer
  reading a history with gaps in it, not of a weak or mistuned detector.

## How to fix it

In preference order, most durable first.

1. **Make the receive history at `DM(0x073E)` contiguous.** `PM 0x39BD` walks 29
   words with stride 2 and 14 taps, so it needs 29 consecutive line samples in
   the right order. The producer chain feeding it is `PM 0x178F` → the 20-word
   ring → `RXSAMPLE`, and `PM 0x178F` runs under `CNTR = DM(0x3754)` from a
   0x40-word buffer at `DM(0x376A)`. The measurement that decides this is a
   write-watch on the `DM(0x073E)` buffer, checking whether successive words are
   successive line samples or whether the pointer jumps. If it jumps, the fault
   is in how the harness paces samples into the ring, and fixing that fixes the
   detector without touching firmware state.
2. ~~**The 9600/8000 resampler is defective.**~~ **Retracted — 9600 is correct
   and the resampler is not implicated.** `docs/handoff.md` already settled the
   rate: the V.8 page writes `DM(0x3F66) = 4` itself at `PM 0x3655`, which the
   database names `Samplerate` code 4 = **9600 Hz**, and `DM(0x3754) = 15 =
   144000/9600` is the DSP's fixed internal core rate, so at 9600 the page's own
   resampler ratio is 15/15 — *identity*. `--analog-codec-rate 9600` was the fix
   that completed V.8 and got the caller to page 7; clocking SPORT1 at 8000 puts
   every tone constant at 5/6 of nominal (the calling tone at 1083.5 Hz instead
   of 1300.2). The harness resampler at the RTP boundary is already a streaming
   polyphase windowed sinc (`RationalResampler`, `analog_kernel_dispatch.py:349`,
   qualified in run65) — not the linear interpolator whose ~20 dB penalty an
   earlier session measured. My "duplicates or drops a sample" was asserted
   without reading it.

   So the 8000 measurement below is **not** an improvement. At 8000 the page
   still believes it is at 9600, so a 2100 Hz ANSam presents to the Hilbert FIR
   as 2520 Hz; a larger magnitude there is a mis-scaled input landing nearer the
   filter's passband by accident, not a better one.

   | | `DM(0x0776)` median | mean | max |
   |---|---:|---:|---:|
   | codec 9600 (correct) | 32 | 1,752 | 28,128 |
   | codec 8000 (detuned) | 3,413 | 4,507 | 32,640 |

   And it does not move the gate either way: at 8000 `DM(0x0777)` is above its
   threshold in 0.5% of 43,400 writes and `DM(0x0778)` still never exceeds 1.
   The number to explain remains the one at 9600.

3. **The reset coupling, which is the remaining gap.** `PM 0x3ED1..0x3ED3`
   clears `DM(0x0776)`, `DM(0x0777)` and `DM(0x0778)` together, and it is reached
   from `PM 0x37D4` — inside condition 3 — whenever detector A is *below* its
   threshold. Detector A is below threshold 79% of the time (7,237 of 34,557
   samples above). So every dip in A wipes B's integrator, and B is required to
   hold for 240 consecutive evaluations. Whether that is correct firmware
   behaviour starved of a good signal, or whether A should be dipping far less
   than 79% of the time, is the next question, and it is one measurement:
   correlate `DM(0x07BD)`'s dips against the ANSam envelope's own 15 Hz
   modulation and 450 ms phase reversals.
4. **Only if both are clean**, suspect the emulator's `ASHIFT`/fractional MAC in
   `PM 0x3EE0..0x3EE4` — `(MX0² + MX1²) << 5` with saturation. The `Y - 1` carry
   defect is precedent, and `tools/adsp_arith_oracle.py` exists for this.

What **not** to do: pin `DM(0x0747)` down from 200, or pin `DM(0x0778)` to 240.
Either makes the CM branch fire and would look like success — the walk would
reach `0x021B` and build a CM — while leaving a broken quadrature path that
every later modulation depends on. That is the fifth stand-in the oracle warned
about.

## Resolved: the codec was clocked at 8000

The open questions above — why detector B's integrator never reaches its
threshold, and whether detector A's dips are wiping it — were all downstream of
one setting. The Analog codec-rate default was 8000 where V.8 asks for 9600
(`Samplerate` code 4), so the entire V.8 page, the envelope biquad included,
ran 5/6 slow and its passband missed ANSam's 15 Hz. With the default at 9600:
`DM(0x0778)` climbs past its 240 threshold to 813 on a normative ANSam, and a
fresh loopback takes the caller from page 6 (V.8) to page 7 (INFO) in 3.67 s.

No stand-in was needed: `DM(0x0747)` and `DM(0x0778)` are untouched. See
`docs/ansam_envelope_loss.md`, which also withdraws its own claim that this
project's receive path was flattening the envelope.

---

# The V.90A "RXSAMPLE is dead" trail is withdrawn in full: it photographed a silent line

Sessions 249–251 built six commits on the V.90A page's receive path — "RXSAMPLE
is zero all call", "RXSAMPLE is filled and then zeroed once per frame", "the
page copies from a ring nothing fills", "the ring has two producers and the
cursor follows the wrong one", and upstream of those the whole downstream chain
from `DM(0x2131)` = the constant 4 to the outer park at `0x0092`. **Every one of
those measurements was taken in the first five seconds of V.90A residency, and
in that window the answerer is transmitting nothing.** Re-measured after the
wire comes up, the entire chain is alive and this row has no defect in it.

## The instrument fault

`EICON_WATCH_OVERLAY` holds watch arming until the page is resident, which is
what Session 251 added to stop watches spending their budget on V.8 and INFO.
It is necessary and it is not sufficient: *residency is not the same thing as
the page having something to look at*. On this rig

| | |
|---|---|
| V.90A (`0x026B`) becomes resident | **9.36 s** |
| the answerer's transmit on `caller.rx.wav` | **0 from 9 s to 14 s**, RMS 2048 from 14.6 s |

so a budgeted watch armed at 9.36 s spends the whole budget on 5.25 s of
silence. The same is true of the per-frame DM CSV and of `adsp.csv`: a summary
over the *whole* residency is dominated by the dead window, which is how "60%
non-zero" and "zero, every one" were read off the same buffer in one survey.

`EICON_WATCH_AFTER=<seconds>` is the companion gate. It holds overlay-gated
arming — watches and `--assert-dm-clean` alike — until the call has also run
that long. It is the same idea as `EICON_DUMP_PM`'s trailing `:<seconds>`, one
level up.

## The same instrument, the same address, the arming time the only change

`--assert-dm-clean 0x3763:0x3763:40000@0x026b`, `EICON_WATCH_OVERLAY=0x026b`:

| arming | writes logged | non-zero | RMS |
|---|---:|---:|---:|
| on residency (9.36 s) | 40,000 | **0** | 0 |
| `EICON_WATCH_AFTER=16` | 40,000 | **40,000 (100%)** | **520** |

Nothing else differs between the two runs.

## The receive chain, gated and settled, end to end

All figures from the live window of a 30–40 s loopback
(`--answerer-firmware-set pri117 --answerer-modulation v90
--caller-firmware-set analog109 --caller-modulation v90a
--caller-kernel-dispatch --analog-codec-rate 9600`):

| stage | measured | verdict |
|---|---|---|
| SPORT1 ISR store, `DM(0x2E22)` | 99.9% non-zero, RMS 3,850, 4,674 distinct | alive |
| TIKRNL publishes through `ShellInptr`, `DM(0x3763)` | 100% non-zero, RMS 520 — one writer, `PM 0x0721` | alive |
| the page's ring `DM(0x3740..0x3753)` | **one** producer, `PM 0x1752`, the page's own scalar fill | alive |
| `RXSAMPLE_0..2`, `DM(0x3F30..0x3F32)` | **one** writer, `PM 0x1733`, 100% non-zero, RMS 520 | alive |
| the demodulator's input `DM(0x2131)` | 100% non-zero, RMS 2,295, −3,286..3,209 | **not a constant** |
| the analytic delay line `DM(0x0E80)` | RMS 3,111, ±3,288 | alive |

`DM(0x3763)` × 0.25 is the published sample by design — TIKRNL `PM 0x0717..0x0719`
multiplies by `0x2000` — so RMS 520 against a wire at RMS 2,048 is the ratio the
firmware asks for, not a loss.

## What was misread, specifically

- **"Two producers, `PM 0x179E` and `PM 0x1752`."** There is one. `PM 0x179E`
  is the *V.8* overlay's receive-resampler store; in **V.90A's** copy of the
  same library, which is what is resident, `PM 0x179E` is `L0 = $0000` and is
  not a store at all. Dumped live with `EICON_DUMP_PM=0x1700:0x17c0:…:1.0`
  while `0x026B` is resident, **176 of 176 covered words match V90.ANA and 16 of
  187 match V8.ANA** — the resident library is unambiguously V.90A's.
- **V.90A's Core8kRoutine is a different shape from V.8's, and correctly so.**
  V.8's `PM 0x1706` runs scalar-in → the 64-word buffer at `DM(0x3700)` →
  the resampler at `PM 0x178F` → the 20-word ring at `DM(0x3740)`. V.90A's
  `PM 0x1706` (confirmed live: `Core8kRoutine` = `0x1706`) puts the sample
  straight into the ring at `PM 0x174E` and never calls its resampler, because
  at `Samplerate` code 4 the ratio `DM(0x3754)`:`DM(0x3755)` is 15:15 —
  identity. Its `PM 0x1710`/`0x1782` resample entry is unreferenced by design.
- **`DM(0x3762)` is `0x3763`**, measured, so `PM 0x174E` reads exactly the word
  `ShellInptr` publishes. The `0x3764` "receive audio" of commit `3d2f81f` is
  the transmit word `ShellOutptr` names, as `c9dbdc3` already suspected.
- **`RXSAMPLE_3..5` staying zero is correct**: `DM(0x3F67)` = 3 on this page, so
  the copy loop fills `_0.._2` only.

## What this leaves

The V.90A caller still parks at `DM(0x20F9)` = `0x0092` for the whole live
window, with the inner machine on cursor `DM(0x2127)` = `0x16C2`,
`DM(0x2104)` = `0x0028` and the event flag `DM(0x10F3)` never set. That is a
real blocker and it is **not** downstream of the receive path — the page is
being handed the line correctly and is declining for some other reason. Note
the inner cursor in the live window is `0x16C2`, not the `0x16A4` self-loop the
handoff row describes; that reading also belongs to the silent window and needs
re-taking before it is used.

**Before reusing anything from the V.90A row of `docs/handoff.md`, check
whether it was measured under `EICON_WATCH_AFTER`.** If it was not, it is
probably a photograph of the same five seconds.

---

# Correction to the correction: the settled window above still photographed the wrong answerer, and the park is an inner self-loop, not a stuck `0x28`

Everything in "What this leaves" was measured **without
`EICON_EXPAND_SPORT=1` on the answerer**. Without it the PRI answerer loads the
V.90 DPCM page at 7.55 s, walks to `TrnProgress 0x0060`, and **falls back to
INFO (page 7) at 12.47 s** — so from ~14.6 s the caller's line carries the
answerer's *INFO* signal, a nearly pure **1200 Hz tone at RMS 2048**, not a
V.90 Phase-3 probe. Every reading in the section above (`DM(0x2131)` RMS 2295,
inner cursor `0x16C2`, `DM(0x10F3)` "never set") is a photograph of the caller's
detector chewing on that fallback tone. This is the same class of instrument
fault as the silent-window one, one level out: **the answerer has to be held on
its V.90 page, or the caller is not receiving V.90.**

`EICON_EXPAND_SPORT=1` (documented in `dial_tikrnl_drive.py`) keeps the answerer
on V.90: it then walks `0060 → … → 0x00b0` (reached 14.94 s) and transmits a
**broadband probe at RMS 403**, no dominant tone — the real Phase-3 signal. All
figures below are from
`--answerer-firmware-set pri117 --answerer-modulation v90 --caller-firmware-set
analog109 --caller-modulation v90a --caller-kernel-dispatch
--analog-codec-rate 9600 --answerer-env EICON_EXPAND_SPORT=1`, caller watches
gated `EICON_WATCH_OVERLAY=0x026b EICON_WATCH_AFTER=17`.

## The blocker is the inner machine's state-`0x20` self-loop — the Session-251 reading was right

Under the faithful answerer, the caller's inner machine does **not** sit at
`0x28`. It self-loops on state `0x20` (record `0x16a4`), cursor bouncing
`0x16a4 ↔ 0x16b6`, `DM(0x2104)` = `0x0020` re-applied 8,320× in the settled
window. This is exactly the `docs/handoff.md` Session-251 shape — "a
detector-driven self-loop that reloads its own dwell, the same shape as the
V.34 answerer's correlator-latch self-loop on state `0x0090`" — and it is
**confirmed, not withdrawn**. The session-252 "stuck at `0x28`, event never set"
reading is what the INFO-fallback tone produces and is superseded.

## Why it self-loops: the Phase-3 detector never goes quiet

The condition is the detector at `PM 0x0cf0`: a front-end interpolator
(`CALL $2C7A`), a complex NCO mix, a CORDIC normalise (`CALL $0D07`), a 6-tap
correlator (coeff `0x1554`), then

```text
0cff: AR = ABS MR1        ; correlation magnitude
0d00: AY0 = DM($20F7)     ; threshold (record block index 14)
0d01: AF = AR - AY0
0d03: IF GT AR = 0 + 1    ; latch the event flag when magnitude > threshold
0d04: DM($10F3) = AR
```

State `0x20` is a V.34-style correlator-latch: primary condition is a dwell
countdown (`0x28` = 40 ticks), test slot `[32]` = handler `0x0A` (`PM 0x3470`)
consumes the event flag and **re-enters `0x20`, reloading the dwell, every time
the flag is set**. So it ages out only after 40 *consecutive* detector
evaluations with the flag clear.

Measured over the settled window against the RMS-403 broadband probe:

| | |
|---|---|
| detector evaluations (`PM 0x0d04` stores) | 36,096 |
| flag set (magnitude > threshold) | **34,616 — 95%** |
| longest consecutive **quiet** run | **1 evaluation** |
| consecutive quiet the dwell needs | **40** |
| magnitude `ABS MR1` | ~5,000 (`0xebc8` etc.) against threshold `0x02bc` = 700 |

The magnitude is ~7× the threshold and the flag is essentially never clear for
two evaluations running, so the dwell can never expire and the machine can
never leave `0x20`. Every downstream wait — inner `0x28`…`0x5a`, and through
them the outer park at `0x0092` on bit 11 of `DM(0x20EF)` — is starved by this
one loop.

**Direct confirmation.** Hard-pinning the threshold impossibly high
(`EICON_ANALOG_PIN_DM=0x20f7=!0x7fff@0x20f9:0x0092>17`, forcing the detector
quiet) breaks the loop at once: the inner cursor advances `0x16b6 → 0x16c2` at
17.03 s. Conversely, in the INFO-fallback case the magnitude is only ~444, the
flag never latches, the loop *does* age out to `0x28`, and pinning the threshold
*low* (`0x64`) drives the outer machine `0x0092 → 0x0094 → 0x0095` before it
re-parks — the mirror image. Neither a permanently-firing nor a
permanently-quiet detector completes the walk: the states alternate between
"advance when the flag fires" and "advance when it goes quiet", so the walk
needs the detector's firing pattern to **track the probe's segment structure**,
and against this continuous 95%-firing probe it cannot.

## What this leaves, corrected

The V.90A caller parks at `0x0092` because its Phase-3 inner machine self-loops
on state `0x20`: the tone/frequency detector at `PM 0x0cf0` fires on 95% of
evaluations of the answerer's continuous broadband probe and never stays quiet
the 40 consecutive ticks the dwell needs. The receive path is healthy and the
detector is healthy; what is wrong is that the received probe has **no segment
boundary the detector reads as a gap**. The open question is whether that is:

1. **the answerer's probe** — under the `EICON_EXPAND_SPORT` stand-in the PRI
   answerer may transmit a structurally-flat probe where a real V.90 digital
   modem inserts the silence/segment transitions the caller keys on; or
2. **the caller's threshold** — `DM(0x20F7)` = 700 for this state may be too low,
   so the detector fires on probe energy a correctly-tuned client would score as
   quiet.

Distinguish them by measuring the answerer's probe against V.90 §Phase 3 segment
timing (does it have the expected gaps?), and by sweeping `DM(0x20F7)` for a
threshold at which the flag goes quiet for 40 ticks between segments. **Do not
pin either word as a "fix"** — both are stand-ins for the real question, which
is why the detector's firing pattern does not match the state machine's expected
segment sequence.

**Instrument rule added: any V.90A caller measurement must set
`EICON_EXPAND_SPORT=1` on the answerer**, or the answerer falls back to INFO and
the caller is receiving a 1200 Hz tone instead of a V.90 probe. Combined with
`EICON_WATCH_AFTER`, that is two gates a valid V.90A reading now needs.

## v90d against v90a, side by side: the difference is detector discrimination, and it is a peer-signal asymmetry

The V.90D answerer (`pri117`, page `0x026a`) and the V.90A caller (`analog109`,
page `0x026b`) run the same two-level machine with the same
correlator-latch self-loop shape, so the answerer must get *past* its equivalent
of the caller's state-`0x20` loop. It does; the caller does not. Watching each
end's detector event flag over the same faithful loopback
(`--answerer-env EICON_EXPAND_SPORT=1`):

| | detector event | evaluations | fired | longest quiet run | magnitude (min / median / max) | threshold |
|---|---|---:|---:|---:|---|---:|
| **v90d answerer** | `DM(0x120A)` | 100,143 | **50%** | **499** | 0 / **863** / 21,694 | `DM(0x1FF5)` = 1400 |
| **v90a caller** | `DM(0x10F3)` | 36,096 | **95%** | **1** | 0 / **3,079** / 5,522 | `DM(0x20F7)` = 700 |

The answerer's correlator magnitude sits **below** its threshold most of the
time (median 863 < 1400), so it goes quiet for long stretches (up to 499
evaluations) between the probe segments it detects, and its self-loops age out
and walk. The caller's magnitude sits **4.4× above** its threshold (median
3,079 vs 700) and drops below it only 8.6% of the time, so it fires almost
continuously and its state-`0x20` dwell never sees the 40 consecutive quiet
ticks it needs.

**This is a peer-signal asymmetry, not a broken mechanism on either side.** The
two ends receive structurally different signals:

- the signal the **answerer** receives (the caller's transmit) is properly
  segmented — long quiet gaps, up to 499 evaluations, between bursts;
- the signal the **caller** receives (the answerer's transmit) is near-
  continuous — the magnitude reaches 0 but the longest quiet run is a single
  evaluation.

And the reason the answerer's transmit has no gaps is the row two down in
`docs/handoff.md`: **the answerer reaches `TrnProgress 0x00b0` at 14.94 s and
stalls there for the rest of the call**, emitting a continuous holding probe
rather than advancing through its Phase-3 segments. So the V.90A caller's
`0x0092` park and the V.90 answerer's `0x00b0` stall are **one mutual Phase-3
deadlock**: the answerer holds a featureless probe because it is waiting on the
caller; the caller cannot advance (or transmit its own next segment) because
that probe never shows the segment boundary its state `0x20` waits for.

## The answer to "what is V.90A missing that V.90D has"

Not a mechanism — the caller's receive path, detector, CORDIC, correlator and
state machine are all healthy and would walk on a properly-segmented probe
(proven: forcing the detector quiet advances the inner cursor immediately, and
in the INFO-fallback case a low threshold drove the outer machine
`0x0092 → 0x0094 → 0x0095`). What the caller is missing is **a received peer
signal with Phase-3 segment gaps**, and it is missing it because its peer — our
own V.90 answerer — is stalled at `0x00b0`. Against a real external analogue
modem (the goal's working case) the digital answerer's peer drives Phase-3 to
completion and the segments arrive; in the all-emulated loopback the answerer's
`0x00b0` stall starves the caller.

**So the V.90A caller blocker is not independent — it is the receive-side face
of the answerer's `0x00b0` transmit stall, and the two should be worked as one.**
The next step is the answerer's `0x00b0` row, not another V.90A pin: establish
why the emulated V.90 answerer holds a continuous probe at `0x00b0` instead of
emitting the Phase-3 segment sequence, since that is the signal the caller is
waiting to detect. Only if a faithfully-segmented answerer probe *still* leaves
the caller's detector above threshold (median 3,079 vs 700) is there a second,
independent caller-side receive-scale defect to chase — the ~4× magnitude
inflation is suggestive of the same receive-scale family as the DIL row, but it
cannot be separated from the continuous-probe input until the answerer sends
gaps.

## Proof the loopback answerer's `0x00b0` probe is not a faithful v90d downstream

`artifacts/eicon-native-tower/run48.ulaw` is our card's own transmit **as the
digital side, connecting to a real analogue modem** (`tools/v90a_replay.py`
header) — a gold reference for what a working v90d sends downstream. Its
short-term energy envelope has real segment structure through the handshake:

| phase | quiet% | longest silent gap |
|---|---:|---:|
| run48 0–3 s | 18% | 530 ms |
| run48 6–9 s | 36% | 700 ms |
| run48 9–12 s | 40% | 615 ms |
| run48 12–15 s | 79% | **2,355 ms** |
| **loopback answerer at `0x00b0`, 15–25 s** | **0%** | **0 ms** |

The loopback answerer's `0x00b0` transmit is flat and gapless where a real v90d
downstream has seconds of segment silence. So the caller is not merely receiving
a different-but-valid signal — it is receiving a **degraded** one, and the
degradation is the answerer's `0x00b0` stall, confirmed independently of the
caller.

This settles the direction: **the V.90A caller cannot be validated against our
own answerer, because our answerer does not transmit a faithful v90d downstream
once it stalls at `0x00b0`.** `tools/v90a_replay.py` cannot substitute — an
open-loop replay of run48 stalls the caller at INFO (a two-way negotiation the
recording cannot answer), so it never reaches Phase 3. A real reacting digital
peer over the SIP leg is the only way to test whether the caller's ~4× detector
inflation is a genuine second defect or an artefact of the stalled probe. Absent
that, the actionable blocker is unambiguously the answerer's `0x00b0` row.

## Corrected once more, and this time it is a caller-side defect: V.90A does not transmit the 2400 Hz Phase-3 tone

The premise that "the answerer's `0x00b0` stall is the root" gets the causality
backwards, and the goal statement says so: *our v90d connects to a real external
analogue modem*, so our v90d does **not** inherently stall — it stalls in the
loopback because **our v90a caller does not drive it the way a real analogue
client does.** The answerer is a fixed, known-good analyser here: fed the gold
analogue-client transmit it walks to a connection (`v90a_replay --role answer`
against `run48.rx.ulaw` "reproduces run48's own state path"); fed our caller's
transmit it stalls at `0x00b0`. **The difference between the two transmits is
what V.90A is missing, and it is measurable.**

`run48.rx.ulaw` is what the real analogue modem transmitted upstream while
connecting — the reference for what V.90A should emit. Comparing it to our
caller's actual transmit (`answerer.rx.ulaw`, i.e. what the answerer receives):

| window | GOLD run48.rx (connects) | OURS (stalls) |
|---|---|---|
| 3–6 s | **1197 Hz pure tone**, spectral flatness 0.001 | 1073 Hz |
| 6–9 s | **2400 Hz pure tone**, flatness 0.001 | multi-tone → broadband |
| 9–12 s | **2400 Hz**, flatness 0.013 | flatness **0.17**, broadband |
| 12–27 s | clean 2400 Hz tones | flatness **~0.17**, broadband noise, held all call |

By Goertzel, a **2400 Hz tone is dominant in 46% of the gold transmit's active
frames (155/334); in ours it is dominant in 6% (36/567).** The real analogue
client spends about half its transmit on a clean 2400 Hz tone; ours almost never
produces it, emitting a broadband, noise-like signal (spectral flatness ~0.17
vs the gold's ~0.001) from ~9 s — the moment the V.90A page loads — for the rest
of the call.

**2400 Hz is the tone the digital side is waiting for.** That is why the
answerer advances part-way (it keys on the caller transmit's *amplitude* segment
structure, which is present — the answerer's detector sees quiet runs of 499)
and then stalls at `0x00b0`: the answerer's next transition needs the caller's
sustained 2400 Hz *tone*, which the caller does not emit. The mutual deadlock is
real but its root is here — the caller's transmit — not the answerer's stall,
which is the *consequence* of the caller never sending 2400 Hz.

### So the answer to the goal

**V.90A is missing its Phase-3 transmit tone.** A working analogue client emits
a sustained clean 2400 Hz (46% of frames); ours emits broadband noise (2400 Hz
only 6%, spectral flatness ~0.17 against the reference's ~0.001). The digital
server — ours in the loopback, or the real one the goal describes — cannot find
the 2400 Hz it must detect to leave `0x00b0`, so it never advances, and with no
advancing peer the caller's own state machine never leaves its `0x20` self-loop.

The next step is on the caller transmit path, and it is now specific: find where
the V.90A page's per-sample transmit routine (`Core8kRoutine`, the transmit half
of `PM 0x1706`, and the tone/segment generator the outer states select) produces
broadband where it should produce a 2400 Hz carrier — the same family as the
V.8-burst tone-generator defects in `docs/analog_v8_oracle.md` (the FSK pair
collapse and the 5/6 rate error), on the V.90A page rather than the V.8 page.
Measure it by watching the caller's transmit-sample producer against a Goertzel
on 2400 Hz, gated to the V.90A states, and compare to what the record's transmit
selection intends for each state. **This is a caller-side defect with a wire
signature, not a peer-signal artefact.**

### Done: `tools/v90a_tx_tone_probe.py`, gated per state

The probe reads the `[v90a] ... state=XXXX` trace to build a sample→state
timeline and Goertzels 2400 Hz on the caller's transmit (`answerer.rx.ulaw`)
frame by frame, bucketed by the outer state in force. Across the whole faithful
loopback:

| state | frames | 2400 Hz dominant | flatness |
|---|---:|---:|---:|
| 0x0054–0x0072 (the walk) | 1–18 each | **0%** | 0.22–0.43 |
| 0x0073 | 45 | 2% | 0.31 |
| **0x0092 (the park)** | **363** | **1%** | 0.29 |
| **run48.rx.ulaw (gold, connects)** | 334 | **53%** | **0.099** |

So the caller emits the 2400 Hz tone in **no** state it reaches — not the walked
states, not the `0x0092` park — while a connecting analogue client spends 53% of
its transmit on it. The producer is running its **data modulator**: `PM 0x1a1e`,
the serializer that fills `DM(0x3FB4)`, disassembles as a MAC-heavy QAM builder
(`MR = AR * MY0`, norm/shift, a 32-tap `DO` loop at `PM 0x1a15`), not a tone NCO
— broadband by construction (flatness 0.22–0.43 vs the tone's 0.099). So the
caller has entered a data-modulating transmit state before the digital side is
ready, where a real client is still holding the 2400 Hz Phase-3 carrier. The
next question is which record selects `PM 0x1a1e` versus a tone generator for
these states, and whether the caller reaches the modulator because a record
field is wrong or because the state walk is (again) ahead of the peer — the same
"which end is ahead" timing theme the V.34 rows carry. `tools/v90a_tx_tone_probe.py`
and `tests/test_v90a_tx_tone_probe.py` make the per-state measurement one
command.

## The transmit dispatch, mapped -- and `PM 0x1a1e` was the wrong address

`PM 0x1a1e` does **not** run on the V.90A caller -- an exec-watch on it fires
zero times while `0x026b` is resident. It is the **V.90D answerer's** serializer
(page 14), and the earlier "MAC-heavy QAM builder" reading disassembled it out of
the caller's image by address, not by what the caller executes. The caller's
real transmit dispatch, traced live:

```text
Core8kRoutine (PM 0x1706)
  PM 0x1723: CALL (I4), I4 = DM(0x3FB8) = 0x292d   -- per-frame generator
    PM 0x292d
      PM 0x2948: JUMP (I4), I4 = DM(0x211A)         -- the variant selector
        DM(0x211A) in {0x2996, 0x29f2, 0x29fe}
  PM 0x1741..0x174c: drain the ring, DM(0x3764) = sample  -- the tx word the
                                                             wire carries
```

`DM(0x211A)` is the word that "selects the transmit routine", and it is written
by `PM 0x258a` -- a table-unpack loop (`PM 0x2588`, source table based at
`DM(0x21)`, called from `PM 0x104c`/`PM 0x24af`) that fills the vector
`DM(0x2118..0x211A)` from a **state-derived index**, the transmit-side twin of
the record unpacker. The three variants:

| variant | what it is |
|---|---|
| `0x29f2` | **silence** -- zeros the transmit ring (`DM(I7,M5)=0` ×3) |
| `0x29fe` | the **full modulator** -- `CALL 0x2459/0x3303/0x2BA9/0x32BF/0x27CA/0x2750`, MAC loop at `0x2A17` |
| `0x2996` | **conditional modulator** -- branches on `DM(0x20EE)` bit 10; both arms modulate |

The parked state (`0x0092`) selects `0x2996`, i.e. it is **modulating** -- which
is why the wire is broadband, not a 2400 Hz tone. So the answer to "which record
selects the transmit routine" is: the state-indexed unpack at `PM 0x2588` writes
`DM(0x211A)`, and for the stuck states it writes the modulator (`0x2996`), never
the silence/tone variant a real client holds during Phase 3.

## Data mode reached (both ends, `0x00d0`, `CTS｜DSR｜DCD`) -- via the status vocabulary, a stand-in

Driving the caller's status vocabulary forward with the Session-251 pin set,
extended with the `0x00c3`/`0x00c6` rungs, takes the pairing all the way to data
mode in the faithful config (`EICON_EXPAND_SPORT=1`):

```text
EICON_ANALOG_PIN_DM=\
 0x20ef=0x0800@0x20f9:0x0092>16,0x20eb=0x4000@0x20f9:0x0095,\
 0x20ef=0x1000@0x20f9:0x00b3,0x20eb=0xc000@0x20f9:0x00c0>25,\
 0x254b=!0x0001@0x20f9:0x00c1>30,0x20eb=0x1000@0x20f9:0x00c3>30,\
 0x20eb=0x0400@0x20f9:0x00c6>30,0x2104=!0x00d0@0x20f9:0x00cd>30
```

The caller walks `0092 → 0094 → 00b0 → 00b2 → 00b6 → 00c0 → 00c1 → 00c3 → 00c6 →
00ca → 00cc → 00d0` and holds `TrnProgress 0x00d0` with **`CTS｜DSR｜DCD`** and
`DATASTATESpeed=0x1113`; the answerer reaches `0x00d0` with `CTS｜DSR｜DCD` and
`DATASTATESpeed=0x1111`. This is a fuller result than the "caller raises CTS
only" of the earlier row -- both ends now assert `CTS｜DSR｜DCD`.

**⚠ It is a stand-in, not a fix.** Every pinned word is a status the caller
could not compute for itself because of the Phase-3 deadlock, so this
demonstrates data mode is *reachable* once the status vocabulary is present -- it
does not make the caller compute it. The real fix is still upstream: make the
caller transmit the 2400 Hz Phase-3 tone (select the tone/silence variant, or
feed the modulator the training pattern) so the answerer advances and supplies
the vocabulary on its own. The pin string is the ladder for regression-checking
that everything downstream of the vocabulary still reaches `0x00d0`.

## The state-index behind the transmit-variant unpack: `DM(0x20E9)`, record index 0

`DM(0x211A)` (the variant word) is re-unpacked by `PM 0x2578` whenever its
driving index changes, and the driver is pinned at `PM 0x249c..0x249f`:

```text
249c: SR0 = DM(0x20E9)          ; record block index 0
249e: AR  = SR0 XOR DM(0x215F)  ; changed since last unpack?
249f: IF NE CALL 0x2578         ; re-select the transmit variant (DM 0x2118..0x211A)
```

So **the state-index that drives the transmit-variant unpack is `DM(0x20E9)`,
the record block's index-0 word** — the same word the outer/inner unpackers
write per state. `PM 0x249c..0x24cb` is a per-frame "config word changed → re-run
its handler" dispatcher; index 0 → the transmit-variant unpack, arm `DM(0x20F4)`
→ the `0x2140` unpack, and so on. Each state's record sets index 0, which
selects silence / modulator / full-modulator for that state.

## Native data mode: the caller is the sole root, and the block is a detector that will not go quiet

Three results, in order, localise what a *native* (unpinned) data mode needs:

1. **The answerer follows the caller.** In the pinned run that reaches `0x00d0`,
   the answerer is *not* pinned, yet the moment the caller is driven past
   `0x0092` the answerer leaves its `0x00b0` stall on its own —
   `00b0 → 00b1 → 00b2 → 00b3 → 00b5 → 00b6 → 00b8 → 00ba → 00c0 → … → 00d0`.
   So the answerer's `0x00b0` stall is *caused by the caller not advancing*; the
   caller is the sole root, and nothing native has to be done to the answerer.

2. **The caller's detector never goes quiet, even before the answerer stalls.**
   Gated to 11–15 s — while the answerer is still walking `0x0060→0x00b0` and has
   not yet begun its holding probe — the caller's event flag `DM(0x10F3)` still
   fires on **96%** of detector evaluations, longest quiet run **1**, and the
   inner state is `0x0020` the whole time (6,143 re-applies). The answerer's
   twin detector fires **50%** with quiet runs to 499. So the caller's detector
   over-fires against *everything* the answerer sends, not just the stalled
   probe, and its inner `0x20` self-loop can never time out.

3. **It is not receive scale, and not a single threshold.** Pinning the receive
   gain `DM(0x3FC8)` down by 3.6× leaves the firing rate at 96% — the CORDIC is
   amplitude-normalised, so the magnitude is a *frequency* reading, not a level.
   And pinning the state-`0x20` threshold up to a value that discriminates
   (`0x0d00`–`0x1000`) does **not** cascade the walk: the inner states alternate
   between "advance when the detector fires" (`0x28`) and "advance when it goes
   quiet" (`0x20`), so no single threshold serves the sequence.

**What native data mode needs, and the one thing not yet isolated.** For the
caller to walk natively its detector has to *discriminate* — fire on the awaited
signal, go quiet the 40 ticks its `0x20` dwell needs — instead of firing 96% on
everything. Two readings remain open and they are not separable in an
all-emulated loopback:

- **the caller's detector over-fires** — a frequency-domain defect in its own
  `PM 0x0cf0` chain (the NCO mix ahead of the CORDIC at `PM 0x0cf2`, or the rate
  it is clocked at — the 5/6-family suspicion the V.8 tone constants carried),
  so that even a correct peer signal reads above the record thresholds; or
- **the answerer never sends a narrowband signal to go quiet on** — its
  broadband probe (and its earlier walk output) genuinely has the high-frequency
  content the CORDIC reads high, so the caller is right to keep waiting and the
  fault is the peer, entangled with the `EICON_EXPAND_SPORT` stand-in.

The 96%-vs-50% firing split *looks* like the first, but the two ends receive
different signals (the caller hears the answerer's broadband probe; the answerer
hears the caller's transmit), so the split alone does not prove a caller defect —
that is the honest limit of what the loopback can show. Deciding between them
needs the caller's `PM 0x0cf0` detector driven by a **known-good digital-side
downstream that reaches Phase 3** — which no replay in this repo provides
(`v90a_replay` stalls the caller at INFO). A real digital modem on the SIP leg
is the instrument that would settle it. Until then, native `0x00d0` is blocked
on this one undecided question, and the status-vocabulary pins are the only
route — a stand-in for exactly the status the caller cannot compute while its
detector will not go quiet.

### Native levers tried, all negative

Every native intervention available in the repo was tested and none breaks the
deadlock:

| lever | result |
|---|---|
| discriminating threshold pin (`DM(0x20F7)` = `0x0d00`–`0x1000`) at the park | caller stays at `0x0092` — the inner states alternate needing fire vs quiet, no one value serves them |
| receive gain `DM(0x3FC8)` down ×3.6 | detector still fires 96% — CORDIC is amplitude-normalised |
| relative timing `--setup-gap-ms` 500 / 4000 / 7000 | caller never advances past `0x0092` |
| force caller transmit variant to silence (`0x29f2`) or full-modulator (`0x29fe`) at the park | answerer stays at `0x00b0` — it waits for a *specific* Phase-3 response, not merely a change in the caller's energy |

The deadlock is symmetric and specific: each end waits for the other's exact
Phase-3 signal, and neither can synthesise it without the other advancing first.
The answerer only reaches `0x00b0` at all through the `EICON_EXPAND_SPORT`
stand-in (no real bearer), so the loopback cannot present the caller a faithful
digital peer that would break the symmetry. **A native V.90A `0x00d0` is not
reachable in the all-emulated loopback with any lever found here; it needs a real
digital modem on the SIP leg — or the caller's transmit fixed to emit the
Phase-3 training pattern (the modulator's symbol source, deeper than the variant
selection), which is the one caller-only path that could drive the answerer
without a pin.** The status-vocabulary pins remain the demonstration that
everything downstream of the deadlock reaches data mode.

---

# Re-measured from the wire: the deadlock stands, two claims above are wrong, and the one caller-side emu bug is in the tone generator, not the modulator

A fresh faithful capture (`--answerer-firmware-set pri117 --answerer-modulation
v90 --caller-firmware-set analog109 --caller-modulation v90a
--caller-kernel-dispatch --analog-codec-rate 9600
--answerer-env EICON_EXPAND_SPORT=1 --trace-v90a-state --seconds 28`,
`artifacts/loopback-v90a/probe`) reproduces the deadlock exactly — caller
`TrnProgress 0x0073 -> 0x0092` at 12.56 s, answerer `0x0080 -> 0x00b0` at
14.94 s. Re-reading it against the wire settles three things the sections above
got wrong or left open.

## The harness resampler is exonerated, with a runnable proof

The recurring "5/6 rate family" suspicion kept pointing at the codec-boundary
resampler. It is clean. A pure 2400 Hz tone sampled at 9600 Hz, pushed through
this repo's own `RationalResampler(5, 6)` (`analog_kernel_dispatch.py:392`) to
8000 Hz, comes out **2400 Hz with 0.00 % at 1800 Hz** — no image. Whatever is
wrong with the transmit spectrum is generated *inside* the DSP, upstream of the
bearer boundary. (`tools/analog_kernel_dispatch.py` `RationalResampler`, driven
by hand; reproduce in ten lines.)

## Correction: the caller *does* emit 2400 Hz — in the V.8/INFO phase, not the V.90A page

`docs/analog_rxsample_correction.md` above and `tools/v90a_tx_tone_probe.py`
concluded "the caller emits the 2400 Hz tone in **no** state it reaches." That is
a scoping artifact of the probe, not a fact about the caller. The `[v90a]` state
trace only begins logging once the V.90A page is **resident** (~9.35 s), so the
probe's state timeline covers only `0x0060..0x0092` — the broadband tail. The
strong 2400 Hz the wire actually carries is at **6.0–9.0 s**, during
`TrnProgress 0x0024..0x0044` — the **V.8/INFO** phase, before V.90A loads. The
tone probe never sees it because no `[v90a]` line exists yet to bin it.

So the accurate statement is narrower: the **V.90A page itself** (states
`0x0060..0x0092`) transmits only broadband — its data modulator, flatness ~0.17,
RMS ~960 — and never a tone. The tones belong to the earlier page. This does not
change the deadlock, but it retires "V.90A is missing its Phase-3 transmit tone"
as stated: the tone is emitted; the question is why the *parked* page runs the
modulator instead, and that is the self-loop, already mapped.

## The answerer's `0x00b0` probe is white noise, measured

The caller's `0x0092` self-loop needs its detector quiet for 40 consecutive
ticks. It never gets one because the signal it receives — the answerer's
transmit at `0x00b0` — is **near-white noise**: RMS 403 constant, spectral
flatness **0.45–0.64**, energy spread uniformly (~20 % per octave) across
0–4000 Hz for the whole park. A narrowband correlator fires on white noise by
construction, which is exactly the 96 %/longest-quiet-run-1 the earlier section
measured. The same detector mechanism on the answerer side fires only 50 % with
quiet runs to 499, because the answerer hears the caller's *structured*
transmit. **The detector is not defective; its input is featureless.** This
confirms — from the wire, not the state machine — that the block is the peer
signal, and the "caller receive-scale defect" reading is not supported: an
amplitude-normalised correlator reading white noise as "always present" is
correct behaviour.

## The one genuine caller-side emu bug found: the V.8/INFO tone is a comb, not a tone

Where a real analogue client (`run48.rx.ulaw`) transmits a **pure single tone**
during its line-probe/Tone-A phase (2400 Hz, 0.0 % at every other probe
frequency), our caller transmits a **comb of discrete tones** at roughly equal
amplitude:

| component | 600 | 1800 | 2100 | 2400 | 3000 Hz |
|---|---:|---:|---:|---:|---:|
| level (of peak) | 90 % | 100 % | 85 % | 85 % | 86 % |

Ruled out as the cause of the comb:

- **Not µ-law companding.** A pure 2400 Hz tone at the same amplitude through
  encode→decode is clean (0.0 % at every comb frequency). Gold, through the same
  codec, shows a pure tone. The comb is ours alone.
- **Not the resampler** (above).

So the comb is generated in the caller's DSP transmit — a tone generator
producing images/harmonics (600 Hz spacing; `{600, 1800, 3000}` are the odd
multiples of 600, the fingerprint of a square/ZOH source; `{2100, 2400}` ride on
top). This is the first defect localised to the caller's own transmit
arithmetic rather than to a state or a pin, and it is the shape the whole
investigation kept predicting ("the same family as the V.8-burst tone-generator
defects — the FSK pair collapse and the 5/6 rate error").

## Write-watch on `DM(0x3764)`, and the honest correction it forces

Write-watching the caller's transmit word directly at the DSP settles what the
comb is and tempers the "tone generator is broken" claim above. Run:
`--caller-env EICON_WATCH_OVERLAY=0x0260 --watch-dm-writes 0x3764:40000`
(`artifacts/loopback-v90a/txwatch`); the target window is bootpage **0x0007**
(INFO), overlay **0x0260**, `TrnProgress 0x0024..0x0044`.

- **The comb is DSP-internal, airtight.** 30,402 writes to `DM(0x3764)` at one
  per codec sample (9600 Hz). FFT of the value stream is the same comb the wire
  carries — so it is present *before* the SPORT drain, the kernel, the resampler
  and the RTP path. The entire downstream transmit chain is exonerated, not just
  the resampler.
- **It is an exactly period-16 waveform.** The 16 samples
  `[376,-1040,313,-279,-2084,288,3897,1014,-4692,-2870,4006,4192,-2237,-4201,
  422,2895]` repeat verbatim (std across repetitions = 0). At 9600 Hz that is a
  **600 Hz fundamental**, and the comb is simply its harmonics (strongest the
  3rd/4th, 1800/2400). So the INFO page emits a fixed synthesized waveform, not a
  tone with an accidental image.

**The correction.** The claim above that gold emits "a pure tone" where we emit a
comb is *false as a general statement*. Scanned for discrete-tone combs, the gold
analogue client emits them too: a **150 Hz-spaced** dense comb (13+ tones,
150..3750 Hz) at 9.0–9.4 s — the textbook **V.34 line-probe L1/L2** — and a
~165 Hz comb around 2400 Hz. Gold's pure 2400 Hz is only its **Tone A** segment.
So the real difference is **600 Hz-spaced comb (ours) vs 150 Hz-spaced line probe
(gold)**, and it is not established that ours is malformed rather than a
different, legitimate INFO-page signal — especially since **the INFO exchange
completes**: both ends advance out of it (caller to the V.90A page, answerer to
`0x00b0`). This signal is therefore **not** the data-mode blocker, and the
earlier "if the same generator feeds the Phase-3 tone…" was speculation — the
Phase-3 park runs on the V.90A overlay with the broadband *modulator*, a
different producer, not this 16-sample INFO waveform.

What survives: the comb is real and DSP-generated (useful — it exonerates the
whole transmit path and localises any future question to the INFO page's own
waveform table), but it is not the thing standing between us and data mode.

## Where this leaves the goal

No single missing bit takes the all-emulated loopback to V.90A data mode; the
mutual Phase-3 deadlock is real and reproduced. The honest summary:

- the caller reaches the Phase-3 park and its detector is healthy but starved of
  a segmented peer signal;
- the answerer holds a featureless white-noise probe because the parked caller
  never sends it the Phase-3 tone;
- breaking it natively needs a real reacting digital modem on the SIP leg, **or**
  the caller's transmit driven to emit the Phase-3 signal the answerer waits for
  — which the parked V.90A overlay, running its data modulator, does not.

---

# The V.90A Phase-3 transmit producer, traced end to end — and it is not the defect

Disassembling `026b-v90.ana-apcm-overlay/pm.bin` and confirming with exec-watches
against the live park settles what the parked caller actually transmits and why,
and it closes the "caller-only Phase-3 transmit fix" lead: the producer is a
correctly-functioning data modulator, not a broken tone generator.

## The chain

```
Core8kRoutine  PM 0x1706
  0x1722  I4 = DM(0x3FB8)                     ; = 0x292d, the per-frame generator
  0x1723  CALL (I4)
    per-frame PM 0x292d
      0x2946  DM(0x21A6) = 0x3F30             ; RXSAMPLE_0 -> the *receive* demod (0x2A17)
      0x2947  I4 = DM(0x211A)                 ; the transmit variant word
      0x2948  JUMP (I4)                        ; -> 0x2996 / 0x29f2 / 0x29fe
  0x1724  CALL 0x1737                          ; copy the 3-word tx ring DM(0x3FA7)
                                               ;   into the 32-word ring DM(0x3766)
  0x170f  JUMP 0x1741                          ; drain one word/tick -> DM(0x3764) -> wire
```

The park (`TrnProgress 0x0092`) selects **`DM(0x211A) = 0x2996`**, the conditional
modulator (branch on `DM(0x20EE)` bit 10; both arms modulate). Its producer stage
is **`0x32BF`**, the only call in the chain that writes the transmit ring:

```
0x32BF  I7 = 0x3FA7                            ; the 3-word tx ring
        MY0 = DM(0x211F)                       ; output scale
        I0  = 0x0A92                           ; the pulse-shaped symbol buffer
        I4  = DM(0x2119) ; JUMP (I4)           ; sub-shaper select:
          0x32C4  DM(I7,M5)=0 x3               ;   -- silence
          0x32CA  DO x3: AR=DM(I0,M1);         ;   -- read symbol buffer,
                  MR=AR*MY0; DM(I7,M5)=SR1     ;      scale, emit to ring
```

The symbol buffer `DM(0x0A92)` is filled per symbol at **`0x39A0`** (called from
`0x3854`), the QAM pulse-shaper — a FIR walk over a coefficient table driven by
the current constellation point. The upstream data/scrambler source is the
circular buffer at `DM(0x3FCA)` (read at `0x2479`, `L4=4`), gated by `DM(0x20F0)`.
There is a distinct **zero-symbol** generator at `0x3886` (`DM(I1,M1)=0 x3`) that a
silence/tone state would select instead.

## What runs at the park, measured

Exec-watch gated to overlay `0x026b` and `EICON_WATCH_AFTER=17` (i.e. inside the
`0x0092` park), `--watch-exec 0x32ca:8,0x32c4:8,0x39a0:8,0x3886:8`:

| address | what it is | hits at park |
|---|---|---:|
| `0x32CA` | symbol-buffer reader → tx ring | **runs** (`from=32c3 ret=29a3`) |
| `0x39A0` | QAM pulse-shaper filling `DM(0x0A92)` | **runs** (`from=3854 ret=3855`) |
| `0x32C4` | silence writer (zeros the ring) | never |
| `0x3886` | zero-symbol generator | never |

And the symbol values vary per call — `0x39A0`'s `MY0` steps `522b, 5265, 523e,
527b, …` and its `I0` coefficient pointer walks `136f→1351` — i.e. genuine
data-modulated symbols, not a repeated constant. So the parked caller runs the
**full data modulator**, correctly, by the state's own vector: `DM(0x211A)=0x2996`
→ `DM(0x2119)` picks the reader → `0x39A0` shapes real symbols into `DM(0x0A92)`.

## Consequence for the goal

**There is no wrong tone or wrong variant to fix at the V.90A park.** The producer
is a working QAM modulator emitting Phase-3 training, which is broadband *by
construction* — the same shape gold shows in its own `12–14.6 s` TRN segment. The
"caller should emit a 2400 Hz tone" reading belonged to the earlier V.8/INFO
phase (Tone A), a different page and a different producer; at the V.90A park,
broadband training is the expected signal. So the last caller-only lever — "drive
the Phase-3 transmit producer to emit the awaited signal" — is not a code fix
here: the producer already emits the awaited *class* of signal. What it cannot do
is advance to the *next* Phase-3 segment, because that transition is gated by the
receive detector going quiet, and the peer (our stalled answerer) never gives it
the gap. This is the same mutual deadlock, now confirmed from the transmit side:
the modulator is healthy, the state machine is simply held in the training state
by the receive path. A native `0x00d0` needs a real reacting digital peer; no
edit to the transmit producer changes that.

The INFO-page comb is a genuine DSP-internal signal but a **dead end for the goal**:
it sits in a phase that completes. The live blocker remains the Phase-3 deadlock,
and the one caller-only lever that could break it without a pin is still the
V.90A overlay's Phase-3 transmit (the modulator's symbol source), not the INFO
waveform table.

---

# The quiet-gate on `0x0092`, verified on a real probe — and the exact reason native `0x00d0` is out of reach

This resolves the receive side the way the section above resolved the transmit
side: the caller's Phase-3 detector and its quiet-gate are **healthy**, and the
only thing missing is a peer that leads the segment sequence. Two instruments
settle it — a threshold pin that forces the detector quiet, and a new
`EICON_RX_PRIME` that feeds a *real* digital downstream into the caller's receive.

## The detector latches; the handler clears; the gate needs a real gap

`PM 0x0cf0` computes a correlation magnitude (`ABS MR1`) after a CORDIC normalise
and a 6-tap boxcar, and at `PM 0x0d02..0x0d04`:

```
0d02: AR = DM($10F3)       ; the CURRENT flag
0d03: IF GT AR = 0 + 1     ; set to 1 only when magnitude > DM($20F7)
0d04: DM($10F3) = AR       ; ... otherwise leave it as it was -- a LATCH
```

The detector never clears `DM(0x10F3)`; it only sets it. The clear is `handler
0x0A` at `PM 0x3470` (`DM($10F3) = M0`, i.e. 0), invoked by state `0x20`'s test
slot, which also reloads the dwell. So the quiet-gate fires only when the
magnitude stays **below threshold for the whole dwell** — one below-threshold
evaluation is not enough, because any single crossing between two handler-clears
re-latches the flag. Against a gapless probe the flag is always set when checked.

## Forcing the detector quiet advances the inner cursor — the gate works

`EICON_ANALOG_PIN_DM=0x20f7=!0x7fff@0x20f9:0x0092>17` (threshold impossibly high
inside the park, so the magnitude can never exceed it):

```
12.554s  outer 0092  iptr=16b6  thresh=02bc  event=0001
17.030s  outer 0092  iptr=16c2  thresh=7fff  event=0000   <- pin engages, inner advances
```

The inner cursor steps `0x16b6 → 0x16c2` the instant the detector goes quiet. The
gate is verified. But it stalls at `0x16c2`: the *next* inner state needs the
detector to **fire** on a segment, and a permanently-quiet detector cannot supply
that. No single threshold serves the sequence — it alternates quiet/fire.

## A real segmented probe cascades the whole outer machine — `0x0092 → 0x00b3`

`EICON_RX_PRIME=<ulaw>:<start_s>:<end_s>:<offset_s>` substitutes the caller's
received codeword with a file's during a window (added to `eicon_adsp_sip.py`,
inert unless set). Feeding `run48.ulaw` — a **real V.90D digital downstream that
connected to a real analogue modem**, so it has genuine Phase-3 segment gaps —
into the caller at the park, the outer machine walks:

```
0092 (thresh 02bc, event 1)  ->  0094 (thresh 0578, event 0)  ->  0095
   ->  00b0 -> 00b1 -> 00b2 -> 00b3
```

The detector discriminates (`event` alternates 0/1 as the gaps arrive), the
quiet-gate cascades, and the caller leaves the park under its own state machine.
**The caller's Phase-3 receive path is healthy and would reach data mode given a
peer that leads the segments.** Determinism check: replaying the loopback's own
`caller.rx.ulaw` into a standalone caller (`v90a_replay.py`) reproduces the walk
to the park exactly, which is what makes the splice valid.

## In the loopback, priming the caller makes the answerer follow — a native first

With the same prime applied to the **loopback** caller (its transmit still
reaching the live answerer), the answerer leaves its `0x00b0` stall **on its own,
without any status pin** — `0x00b0 → 0x00b1 → 0x00b2` — and its transmit develops
real gaps (0% quiet at `0x00b1`, then **20–26% quiet with 340–440 ms silent runs**
at `0x00b2`). This is the strongest native progress recorded: caller past the park
to `0x00b3`, answerer off its stall to `0x00b2` emitting a segmented downstream.

## Why it still does not reach `0x00d0`, exactly

Both ends then re-deadlock — caller `0x00b3`, answerer `0x00b2` — and stay there
for the rest of the call after the prime ends, across every release time swept
(`19, 19.5, 20, 20.5 s`) and under a dual-prime of both directions. **Phase 3 is
a *sequence* of mutual gates, not one.** At each gate the analogue side needs the
digital side to lead the next segment and vice-versa; the prime supplies that lead
for the *first* gate (`0x0092`) from a recording, but past it the recording is not
reactive to our caller's actual transmit, so it stops matching, and the two live
ends — one of them the `EICON_EXPAND_SPORT` stand-in, not a faithful V.90D — cannot
lead each other through the remaining gates. Priming the answerer's own receive
instead makes it *worse* (it stops hearing the caller and never follows), which
confirms the answerer advances only off the caller's real transmit.

## Verdict for the goal

- **Quiet-gate on `0x0092`: verified.** Forcing the detector quiet advances the
  inner cursor `0x16b6 → 0x16c2`; a real segmented probe cascades the outer machine
  `0x0092 → 0x00b3`. The detector, CORDIC, latch, handler-clear and dwell are all
  healthy.
- **Real (native) `0x00d0`: not reachable in the all-emulated loopback, and now
  proven from the receive side too.** The caller reaches `0x00b3` on real audio and
  the answerer follows to `0x00b2` with real gaps — no pins — but Phase 3 re-locks
  at every subsequent mutual gate. Closing it needs a real reacting digital modem
  on the SIP leg (or a faithful V.90D in place of the `EICON_EXPAND_SPORT` stand-in);
  the status-vocabulary pins remain the only way to *display* `0x00d0` here, and
  they are a stand-in for exactly the peer-led segments this section shows are
  missing. `EICON_RX_PRIME` is the instrument that isolates it: it drives the caller
  as far as a non-reactive real downstream can, which is `0x00b3`, and no further.

---

# What the caller's 0x0092 detector actually keys on: real v90d structure, not level or tone

A session spent testing every *signal* the digital side could send at the
`0x0092` park, driving the answerer's transmit directly rather than only its
state. New instruments in `eicon_adsp_sip.py`, all inert unless their env var is
set: `EICON_TX_MUTE=<start_s>:<end_s>` (zero this end's produced transmit),
`EICON_TX_TONE=<hz>:<start_s>:<end_s>[:<amp>]` (replace it with a pure sine),
`EICON_TX_FILE=<ulaw>:<start_s>:<end_s>:<offset_s>` (replace it with a decoded
u-law file over the real wire), and `EICON_EVENT_LOG=<period>[:<overlay>]` on the
caller (log the detector flag `DM(0x10F3)` per sample, or on change with
`period=0`, so a flag that moves while the outer state does not is visible).

## The detector at 0x0092, disassembled

Live-dumped (`EICON_DUMP_PM=0x0ce0:0x0d40:...`, gated `EICON_WATCH_OVERLAY=0x026b
EICON_WATCH_AFTER=13`) and decoded with `tools/disasm_dump.py`:

- `PM 0x0cf0` CALL `$2C7A` — a 9-bit-phase sine-table NCO (the mixing carrier),
  phase accumulator `DM(0x2200)`, increment from `PM(0x2115)`.
- `PM 0x0cf1..0x0cf6` — a complex multiply (NCO mix of the receive sample).
- `PM 0x0cf7` CALL `$0D07` — a CORDIC that **normalises to unit magnitude**, so
  it returns a near-constant coherent angle whose size does not track input
  level.
- `PM 0x0cfa..0x0cfe` — a 6-tap accumulate of the ring the CORDIC wrote.
- `PM 0x0cff..0x0d04` — `AR = ABS MR1; AF = AR - DM(0x20F7); AR = DM(0x10F3);
  IF GT AR = 1; DM(0x10F3) = AR`. So the flag is **set-only** here (never
  self-clears); the clear is handler `0x0A` at `PM 0x3470`. Its natural period is
  the ~3-sample on/off seen below.

## Silence and pure tones do NOT advance the park — measured

With the answerer's transmit forced to silence (`EICON_TX_MUTE`), and separately
to pure tones at 400 / 1000 / 1800 / 2400 / 3200 Hz (`EICON_TX_TONE`, amp 8000),
the caller's detector flag is **identical in every case**: it fires ~82% with a
longest consecutive-quiet run of **exactly 3 samples**, against the ~40 the inner
`0x20` self-loop needs. Confirmed on a truly silent wire (both ends `EICON_TX_MUTE`,
`rxbuf=0`, `rxsamp=0/0/0/0/0/0` — the receive really is zero) the run is still 3.
A strong tone reaches `RXSAMPLE` (`DM 0x3F30`) only ~30x attenuated (amp 8000 ->
about ±238) and is swamped by the CORDIC's constant-angle output. **Mid-session
this read as "the detector is input-independent"; the next section shows that is
wrong** — the trap was testing only silence and single tones, neither of which is
what a V.90D sends.

## A real v90d downstream DOES advance the park — over the wire, not just spliced

Feeding the gold `run48.ulaw` (a real V.90D downstream) advances the caller off
the park two independent ways:

- `EICON_RX_PRIME=...:12.4:22:9.0` (spliced into the caller's receive slot):
  `0x0092 -> 0x0094 (13.66s) -> 0x0095 (16.66s)` — reproduces the documented
  cascade.
- `EICON_TX_FILE=artifacts/eicon-native-tower/run48.ulaw:12.4:23:9.0` on the
  **answerer** (so the same samples cross the *real* wire and the caller's own
  receive chain, attenuation and all): `0x0092 -> 0x0094 (15.80s) -> 0x0095
  (18.80s)`.

So the `0x0092` detector is not input-independent: it discriminates on the real
segmented broadband V.90D signal and stays firing on silence, white noise, or any
single tone. **What an improved v90d must emit to advance the caller is that
segment structure — not a tone, and not merely "energy".** The reason the live
loopback still parks is the answerer's `0x00b0` probe is gapless white noise, not
this structure. Neither `run48` feed reaches data mode (`run48` itself parked at
`0x00b0`, so it has no post-park content) and the answerer's own state stays at
`0x00b0` when its transmit is overridden, so this sharpens the target without
closing it: the missing piece is a **reactive** v90d that leads every Phase-3
gate, of which a fixed recording leads only the first — exactly the deadlock the
sections above describe, now pinned to the specific signal property the caller's
detector requires.

## The caller detector is level-insensitive: attenuation is not the block

To test whether the ~30x receive attenuation (a strong tone reaches RXSAMPLE at
about ±238) is why the caller ignores the answerer, `EICON_TX_FILE` grew an
optional 5th field, a linear scale: `...:<offset_s>:<scale>`. Feeding `run48.ulaw`
as the answerer's transmit at scale **1.0, 0.1, and 0.03** (i.e. down to ~1/33,
past the measured attenuation) advances the caller to `0x0095` in **every** case.
So the caller's `0x0092` detector discriminates the real V.90D segment structure
independent of level — consistent with the CORDIC's amplitude normalisation, and
with the earlier finding that pinning receive gain down 3.6x did not change the
firing rate. **The receive attenuation is real but not the blocker; there is no
level-threshold to raise and no gain fix that helps.** The detector responds to
*structure*, not level: it correctly ignores any tone, noise and silence and
correctly follows a real downstream. The block is entirely that the loopback
answerer never transmits that structure — an unstalled, reacting v90d is the only
thing that supplies it.

## It is the V.90D content, not merely the gaps: gapped white noise fails too

The stalled answerer emits gapless near-white noise; `run48` is broadband bursts
*with* real segment gaps. To separate the two, `EICON_TX_GAP=<on_ms>:<off_ms>:
<start_s>:<end_s>` cyclically zeros the answerer's own produced transmit, inserting
segment gaps into whatever its modulator emits without substituting a recording.
Swept `370/245`, `300/400`, `150/500` ms (on/off) over the park: the caller stays
at `0x0092` in every case. So gap *structure* alone does not advance it -- the
caller's detector keys on the genuine V.90D modulation content between the gaps,
which white noise does not have. This narrows "improve v90d" precisely: inserting
Phase-3 segment gaps into the stalled probe is not enough; v90d has to emit the
actual Phase-3 segment *modulation*. That is the reacting-modem build, and the
repo has no recording of the post-park segments to copy (run48 itself parks at
`0x00b0`), so the late-segment waveform is unknown reference data -- the real,
data-shaped reason native `0x00d0` cannot be closed in the all-emulated loopback
from anything now in the tree.

---

# BREAKTHROUGH: v90a reaches data mode 0x00d0, driven by a real V.90D downstream (run65)

The earlier claim that "no recording in the repo reaches data mode" was wrong: it
looked only at run48. **`run65` reached data mode** -- its slmodemd log reads
`V90Demodulator: enter Data Phase, Rate = 30667 [bps]` and our card (the answerer
in that capture) walks `TrnProgress ... 0x00b0(17.96s) 0x00c0(23.14s)
0x00d0(27.5s)`. So `run65.ulaw` is a **gold V.90D downstream that reaches data
mode**, and `run65.rx.ulaw` is the analog upstream that drove it.

## The signal-based Phase 3 is fully driveable from a real v90d downstream

Feeding `run65.ulaw` into the loopback caller's receive
(`EICON_RX_PRIME=artifacts/eicon-native-tower/run65.ulaw:12.4:50:14.0`) walks the
caller **with no pins** straight through every signal gate that used to be the
wall:

```
0092 -> 0094 -> 0095 -> 00b0 -> 00b1 -> 00b3 -> 00b6 -> 00c0
```

`0x00c0` is six gates past the old `0x00b3` ceiling. The caller's detector was
healthy all along -- it discriminates a genuine V.90D signal and simply never got
one from the loopback answerer's white-noise probe. This is the direct proof of
the "improve v90d" thesis: give v90a a real V.90D downstream and it walks the
signal phase itself.

## Reaching and holding 0x00d0

The terminal gates `0x00c0 -> 0x00d0` are a *bidirectional* status handshake; a
one-directional recording cannot answer the caller's own transmit there, so they
are supplied by the five terminal status pins from the Session-251 set
(`0x20eb=0xc000@...:0x00c0>25` through `0x2104=!0x00d0@...:0x00cd>30`). With
run65 driving the signal phase and only those terminal pins:

```
... 00c0 -> 00c1 -> 00c3 -> 00c6 -> 00ca -> 00d0   (CTS|DSR, 30.18s)
```

**v90a reaches `TrnProgress 0x00d0` and holds it ~20 s (30.18–50.14 s), with zero
retrains, until the driving recording's window ends.** Captures in
`artifacts/loopback-v90a-datamode/`.

## The answerer's own firmware also reaches 0x00d0 from the reference upstream

Symmetrically, priming the **answerer's** receive with `run65.rx.ulaw`
(`--answerer-env EICON_RX_PRIME=...run65.rx.ulaw:12:44:13.0`) walks the answerer's
real V.90D firmware all the way to `0x00d0` on its own -- so both firmwares reach
data mode given real reference audio. A dual prime (caller downstream + answerer
upstream) is two independent replays and does not couple them, so it does not beat
the single caller-side result.

## What remains for *fully* native 0x00d0

The only piece still stood in is the terminal `0x00c0 -> 0x00d0` handshake, which
needs a peer that reacts to the caller's transmit -- exactly the reactive V.90D a
recording cannot be. run65 closes the entire signal phase; a reacting digital peer
(or driving the answerer to respond to the caller rather than to run65's upstream)
would close the last five gates without pins. New instrument for aligning the
recording per gate: `EICON_RX_PRIME_SYNC=<ulaw>:<start>:<end>:<init_off>:<map>`,
which re-anchors the read cursor as the caller enters mapped milestones (cursor
jumps disturb demod lock, so it matches but does not beat a well-chosen fixed
offset for the signal phase).

---

# The root cause, correctly located: v90d generation is HEALTHY; the caller's TRANSMIT is the blocker

Two symmetric experiments settle where the fault actually is, and it is not v90d.

## v90d generates the full structure to 0x00d0 natively, given a valid upstream

Feed the loopback **caller's transmit** a valid analog upstream -- the real
`run65.rx.ulaw` -- during Phase 3 (`--caller-env EICON_TX_FILE=artifacts/eicon-
native-tower/run65.rx.ulaw:12.4:44:13.0`), and the loopback **answerer's own
firmware**, with **no pins and no downstream recording**, generates the complete
V.90D segment structure and walks to data mode:

```
00b1 -> 00b2 -> 00b3 -> 00b6 -> 00c0 -> 00c2 -> 00c4 -> 00c6 -> 00c8 -> 00cc -> 00d0  (24.9s, held)
```

(Capture: `artifacts/loopback-v90a-datamode/answerer-native-generation.endpoint.log`.)
So the answerer's "white-noise probe" is not a generation defect: the firmware
emits the real segments the moment it receives a valid upstream. **v90d does not
need improving.** It stalls at `0x00b0` and idles only because the upstream it
receives from the loopback caller is invalid.

## The caller's transmit is the invalid signal

Comparing the caller's actual Phase-3 transmit (`answerer.rx.ulaw`, what the
answerer receives) with run65's valid upstream (`run65.rx.ulaw`):

| | caller transmit (13-16 s) | run65 valid upstream (14-26 s) |
|---|---|---|
| RMS | ~960, constant, then 0 at 17 s | varies 720-1090, with a gap (0) at 16 s |
| zero-cross rate | ~0.49 (near-random / white) | 0.20-0.42 (structured) |
| segment gaps | none (gapless), then silence at park | real gaps (RMS -> 0 mid-handshake) |

The caller's upstream is gapless, white-noise-like, and then goes silent when the
caller parks at `0x0092` -- it never presents the structured, gapped V.90A
Phase-3 upstream a digital peer needs to lock. That is why the answerer cannot
advance, and the whole "mutual deadlock" reduces to this one side.

## Conclusion

- **v90a receive/detector: healthy** -- reaches `0x00d0` on a valid downstream
  (run65.ulaw) through the whole signal phase, plus terminal status pins.
- **v90d transmit/generation: healthy** -- reaches `0x00d0` generating the real
  segments itself on a valid upstream (run65.rx.ulaw), no pins, no recording.
- **The blocker is the caller's (v90a) TRANSMIT modulator**, which emits a
  gapless white-noise-like upstream instead of the structured, gapped V.90A
  Phase-3 signal. Fixing that -- the modulator's symbol source / segment gating,
  the same lever flagged earlier -- is what makes the answerer generate natively
  and the caller ride it, closing data mode with neither recording nor pins. The
  goal as posed ("improve v90d") is aimed at the healthy end; the defect is the
  caller's transmit.

---

# Experiment 3: driving the caller's transmit with gold upstream — the answerer reaches data mode but the caller still parks

The previous section concluded "the blocker is the caller's transmit modulator,
fix that and the answerer generates natively and the caller rides it." Experiment
3 tests the second half of that claim directly and **partially refutes it**: fixing
the caller's transmit is *necessary but not sufficient*, because the answerer's own
downstream transmit is also unfaithful and blocks the caller independently.

## Setup

Same baseline, but the caller's produced transmit is replaced over the *real wire*
with the gold upstream (`run65.rx.ulaw` — the analog upstream from the real session
that reached data mode), so the answerer receives a faithful, data-mode-reaching
upstream while everything else stays live and reactive:

```
tools/eicon_loopback.py \
  --answerer-firmware-set pri117 --answerer-modulation v90 \
  --caller-firmware-set analog109 --caller-modulation v90a \
  --caller-kernel-dispatch --analog-codec-rate 9600 \
  --answerer-env EICON_EXPAND_SPORT=1 \
  --caller-env EICON_TX_FILE=artifacts/eicon-native-tower/run65.rx.ulaw:12.4:44:13.0 \
  --trace-v90a-state --seconds 50
```

## Result: the two ends split

| end | state walk | outcome |
|---|---|---|
| **answerer** | `0x0080→0x00b0`(15.36s)`→0x00b2→0x00c0`(20.54s)`→0x00d0`(24.90s) | **reaches data mode, no pins**, holds ~5 s, then retrains to `0x0024` at 29.74 s when the recording's post-data-mode content runs out |
| **caller** | `…→0x0073→0x0092`(12.55s), inner cursor `16b6→16c2` once, then held | **stays parked at `0x0092`** — one quiet-gate step, no cascade |

So a faithful recorded upstream walks the answerer's entire *receive-driven state
machine* to data mode — but the caller does **not** follow, even though the
answerer is now live and "in data mode."

## Why: the answerer's downstream transmit is weak and low-coherence

The reason is on the answerer's *transmit* side. Measuring the answerer's live
downstream (`caller.rx.ulaw`) during **its own Phase 3** (`0x00b0→0x00c0`,
15.9–20.5 s) against the gold downstream (`run65.ulaw`) at *its* equivalent phase
(17.96–23.14 s in the real session):

| metric | answerer live downstream | gold `run65.ulaw` |
|---|---|---|
| ZCR | ~0.17 | ~0.50 |
| quiet fraction (`\|x\|<0.15·RMS`) | ~0.83 | ~0.00–0.13 |
| RMS | ~290–400 | ~665–1700 |
| peak DFT magnitude (100–3600 Hz) | ~12–53 | ~100–240 |
| spectral centroid | ~1900 Hz | ~1900 Hz |

The band is right (centroid ~1900 Hz both), but the answerer's downstream is
**~3–5× weaker and lacks the strong 1800/2400 Hz V.90 carrier structure** — a
low-energy, low-coherence signal. The caller's Phase-3 phase-coherence detector
(healthy; it walks `0x0092→0x00c0` on the gold `run65.ulaw` via `EICON_RX_PRIME`)
never advances past the first quiet-gate on it. `EICON_EXPAND_SPORT` only fixes the
answerer's *receive* word scaling — it does not touch the transmit — so this is the
answerer's real pri117/v90 modulator output.

## What this proves — the deadlock is genuine and a recording can't break it

The crucial control: the caller's transmit here is the **gold** upstream, i.e. the
answerer *is* receiving a faithful, reactive-equivalent partner — the exact signal
that in the real session drove a real answerer to data mode. It still emits an
unfaithful downstream. Therefore:

- A faithful one-way recording advances the *receiving* end's **state** but not its
  **transmit**. The transmit modulator is phase-locked to a *reactive* peer — one
  that responds to *this* end's output in real time — which a recording cannot be.
- So the earlier "fix the caller's transmit → answerer generates → caller rides it"
  is too optimistic: fixing the caller's transmit gets the answerer's *state* to
  data mode, but the answerer's *downstream* stays weak/low-coherence, so the caller
  cannot ride it. Both transmit sides need a reactive counterpart at once.

This closes the last ambiguity in the symmetric-deadlock model. Native, pin-free
V.90A data mode in the all-emulated loopback is **unreachable** by any combination
of fixed recordings or caller-only / answerer-only edits: it requires a genuinely
reactive V.90D digital peer on the SIP leg (the role `slmodemd` played when it
produced the gold `run65` pair). That is the single actionable path to native data
mode. Capture: `artifacts/loopback-v90a-datamode/exp3/`.

## 2026-08-20 timing boundary: sample shift, not jitter buffering

The current no-recording baseline remains a caller `0x00c0` / answerer `0x00c2`
deadlock. An opt-in `EICON_RX_LAG_MS=10` on **both** endpoints changes the walk to
caller `0x00b3` / answerer `0x00b2`; 9 ms and below return to the baseline. Applying
the same delay to only one endpoint has no effect, and increasing the ordinary
`--rx-jitter-ms` prefill from 40 to 50 ms has no effect. The result therefore comes
from shifting the sample correspondence in both directions, not from giving the
modems more startup audio. It is useful evidence of a shared media-clock boundary,
but it is not a data-mode fix and remains diagnostic/opt-in.

The codec A/Bs do not support a simple gain correction. Caller DAA receive gain
of `-3` or `-6 dB` makes the caller stall at `0x0095`, and caller transmit gain
of `+12 dB` clips the V.8 exchange and leaves the caller at INFO; neither reaches
Phase 3. The native build-109/2185-compatible wrapper was then corrected to use
the same 9600-Hz kernel-dispatch media core while retaining native DSPDAA/MIPS
supervision. It now reaches V.90 without truncation, but stalls at caller
`0x0095` / answerer `0x00b0`; the kernel-dispatch control reaches
`0x00c0` / `0x00c2`. This isolates the remaining native-path difference to
DSPDAA/MIPS supervision rather than the analog modem's 9600-Hz media core.

The unpinned kernel-dispatch A/B now isolates the caller terminal waveform. The
default 9600-Hz path reaches caller `0x00c0` and answerer `0x00c2`, but the
answerer's upstream-quality peak is only `0x0036`, versus `0x0156` in the real
run65 caller capture. The emulated caller output around the terminal exchange
has RMS about 962 and 136 code values, while the real caller capture is about
1094 RMS and 140 code values. A corrected +1.5 dB transmit-level experiment
raised the emulated RMS to about 1144 but reduced answerer quality to `0x0034`
and did not change either state. This rules out a simple DAA/codec level error.

An 8000-Hz codec A/B is also invalid: the caller remains around `0x0001` and
the answerer falls back from INFO. The 9600-Hz codec and its 6:5/5:6 bearer
resampler are therefore required; the remaining mismatch is in the waveform
shape/timing through that path, not removable by bypassing the resampler.

The resampler filter sweep reinforces that boundary. The default 16 taps per
phase is the only tested setting that reaches the `0x00c0`/`0x00c2` terminal
pair: 8 and 32 taps both regress to caller `0x0095` / answerer `0x00b0`.
Changing the output fractional phase by one filter-grid unit preserves the
terminal pair but leaves upstream quality unchanged at about `0x0036`. A
terminal-only +1 dB caller gain (leaving V.8 untouched) instead regresses to
`0x00b3` / `0x00b2`. These are diagnostic controls, not production settings;
they show that neither FIR length, a one-step phase shift, nor level alone
closes the reactive Phase-3 exchange.

Two further path checks narrow the firmware boundary. On the analog kernel
dispatch path, replacing the DM-published TX word with the physical SPORT1 TX
latch regresses the caller to `0x0030` / answerer `0x0028`; the DM publication
read is therefore the correct convention for this task. Conversely, forcing
the direct V.90D result words to the real terminal values `0x000f/0xfff8`
does not change the answerer's sparse downstream output. The failure is not a
simple result-word estimate or SPORT-latch selection error.

A 20 ms receive delay on both ends returns to the ordinary `0x00c0`/`0x00c2`
baseline. The earlier 10 ms result remains a narrow sample-alignment artifact,
not evidence that adding realistic round-trip delay solves the exchange.

The strongest new result is a page-14-only receive-domain calibration on the
PRI answerer. Applying +1 dB after SPORT expansion raises `upstream_quality`
from `0x0036` to `0x01ab`; +1.5 dB reaches `0x0281`, and +2 dB reaches
`0x0358`. V.8/INFO and the `0x00c0`/`0x00c2` state timing are unchanged, but
none reaches `0x00d0`, and the quality later falls back while the peer remains
in the terminal exchange. This proves the emulated caller's upstream is
under-scaled in the V.90D receive domain, but scaling alone does not solve the
reactive handshake. The controls are opt-in diagnostics, not defaults.
### Result-register trace and calibrated loopback (2026-08-20)

Applying a page-14-only receive gain of +1.5 dB on the PRI117 side, together
with +1 dB caller transmit gain after `0x00c0`, raises the observed V.90D
quality peak from `0x0036` to `0x0439`.  This is useful evidence that the
expanded SPORT receive level is slightly low for the V.90A upstream signal,
but it does not advance the endpoints beyond caller `0x00c0` / answerer
`0x00c2`.  A simultaneous 10 ms receive-lag sweep produces the same result,
so the gain improvement is not a timing fix.

The `DM(0x206d/0x206e)` write trace shows the answerer reaching its `0x007a`
Ja receive gate and shifting a new dibit on each detector event.  The rolling
result never forms the required CP/Ja sync mask, despite the larger detector
quality.  The corresponding answerer transmit capture remains sparse in the
`0x00c2` interval (about 40% nonzero, RMS about 973), unlike the real 2185
capture's continuous lower-level stream.  The current boundary is therefore
the reactive V.90D mapping/control-frame producer or its upstream decoded
control sequence, not the basic PCMU codec rate, resampler phase, or a simple
level/timing calibration.

The serializer chronology was then checked directly. Late write watches show
PM `0x19ee` first publishing the generic pointer and PM `0x1a1e` then
publishing the actual line word. Reading the frame-half value instead of the
continuation-half value regresses the call to caller `0x0095` / answerer
`0x00b0`; the existing continuation read is therefore the correct direct-card
publication point. Disabling the 160 ms transmit cushion has the same negative
result (`0x0095` / `0x00b0` at 0 ms and 80 ms), while 160 ms is needed to reach
`0x00c0` / `0x00c2`. Buffering supplies timing margin but does not repair the
mapping exchange.

The native-side selected-PCMU U-code restoration was ported experimentally to
the direct PRI117 card. It is a real staged firmware table (not fabricated
values), but the direct unpinned call regressed to caller `0x00b3` / answerer
`0x00b2` with quality peaking at only `0x0047`. It is therefore retained as
`EICON_V90D_PCMU_UCODE_TABLE=1` for diagnosis, while the direct default remains
the previously qualified table path until the corresponding direct receive or
DAA boundary is recovered.

The direct card also now configures `DM(0x3309)` from the requested bearer law
when `configure_modem()` is called. Before this, direct PCMU calls left the
resident A-law pointer (`0x35b7`) even though the SIP-side helper codec was
configured for PCMU. This is a correctness fix at the DAA/codec boundary; the
first unpinned A/B is behaviorally unchanged (`0x00c0` / `0x00c2`), so it does
not yet explain the V.90 deadlock.

## Native 2185 bulk-delay boundary port (2026-08-21)

The native MIPS/2185 path contains another V.90D boundary operation that the
direct PRI card did not: it holds the shared PM `0x19c8` bulk-delay worker while
the V.90D descriptor is established, seeds the near/far pair at
`DM(0x3fbc/0x3fbd)` (normally `49/129`), and services the delayed-pair ABI at
`DM(0x3f36..0x3f39)`. The direct card previously ran that worker with zero
lengths, allowing a stale width to walk unrelated DM and poison Phase 3.

The direct backend now mirrors this behavior by default; setting
`EICON_V90D_BULK_ADAPTER=0` or `EICON_V90D_PORTABLE_BULK=0` restores the old A/B
path. The unpinned loopback still ends at caller `0x00c0` / answerer `0x00c2`,
so this is a confirmed fidelity correction but not the final mapping-handshake
fix. Enabling the native PCMU U-code table together with it regresses to caller
`0x00b3` / answerer `0x00b2`, so it remains diagnostic-only
(`EICON_V90D_PCMU_UCODE_TABLE=1`).

## Native V.90D TX mailbox A/B (2026-08-21)

The native MIPS path also supplies V.90D polling TX datagrams through
`DM(0x3f05..0x3f07)` when `DI_control` bit 15 is asserted. The direct card now
has an opt-in equivalent, `EICON_V90D_TX_PRBS=1`, using the native 48-bit PRBS
packing (TXD0 bit 0 oldest) and frame-boundary ownership timing.

In the combined bulk-delay loopback, the hook supplied its first datagram but
the result was byte-for-byte identical to the no-mailbox run: caller
`0x00c0`, answerer `0x00c2`, and the same exact-silence intervals on both wire
captures. This excludes the absent host TX mailbox as the cause of the current
Phase-3 deadlock. The diagnostic remains opt-in; the next boundary to isolate
is the direct V.90A control-frame/silence selection that leaves the caller's
upstream wire silent while the answerer is waiting for it.

## Reactive-peer boundary experiments (2026-08-21)

Three additional A/Bs narrowed the remaining gap without changing the default
path:

* Answerer TX gain of `+12 dB` regressed the caller to `0x0095`, so the weak
  terminal exchange is not a simple DAA/codec level error.
* Periodic answerer TX gaps (`700 ms` on / `300 ms` off) moved the caller to
  `0x00c0` but still left the answerer at `0x00c2`; gap timing alone is not the
  required Phase-3 content.
* State-held replay, including the existing peer-state feedback file, also
  stopped at or before `0x00c0`/`0x00c2`. A fixed recording cannot substitute for
  the live V.90D segment scheduler and terminal response.

The next implementation target is therefore a genuinely reactive V.90D
segment/control producer, rather than another codec scaling or mailbox tweak.

The native-MIPS comparison was also re-run with the current mixed harness. The
answerer completed bearer attachment but remained on INFO (`0x0042`), so it did
not reach V.90D and cannot yet serve as a page-14 oracle. Preboot/no-gap made
the answerer stall earlier in V.8. A 2400-Hz carrier-only bootstrap likewise
failed to reach V.90D, confirming that the missing response is structured
Phase-3 content rather than a bare carrier or level.

## Native page-14 mixed-loopback boundary (2026-08-21)

With `--force-info-after-v8`, the native 2185 answerer does reach page 14 in
the mixed harness. Synchronizing bearer start still does not connect: the
analog caller reaches APCM (`0x0054 -> 0x0072`), while the native answerer
enters DPCM with its V.90D outer and inner state at `0x0000`,
`DM(0x2004)` unset, and no page-14 line samples. Its generator then reports
two idle mapping frames and clears the held block.

This rules out a simple native/direct page-entry race. The remaining boundary
is the reactive Phase-3 mapping/control exchange: the caller is not providing
the structured response that advances the answerer's V.90D state image, while
the caller cannot leave `0x0072` without the answerer's mapping response.
Retaining a stale mapping block would only invent downstream training and is
not a valid fix.

## V.90A terminal selector A/B (2026-08-21)

The live caller's selector watch found a concrete transition at the terminal
exchange: `DM(0x20e9)` changes from `0x1340` to `0x0340`, and PM `0x258a`
accordingly changes `DM(0x2119)` from the symbol reader `0x32ca` to the
silence writer `0x32c4`. The latter then writes zeros into the V.90A output
block while the caller waits in `0x00c0`.

An opt-in hard selector hold (`EICON_V90A_TX_SHAPER=reader`) is now available
for diagnosis. Holding the reader from 15 seconds regresses the handshake to
caller `0x0095` / answerer `0x00b1`, proving that the silence transition is not
simply an emulation error to remove. Holding it only after the normal caller
entry to `0x00c0` leaves the pair at `0x00c0` / `0x00c2`. The selector is
therefore a symptom of the missing reactive control exchange, not its fix; the
override remains disabled by default.

The analogous V90A mapping-block clear was also tested with
`EICON_V90A_TX_BLOCK_HOLD=1`. It changes the caller wire timing and removes
some sparse output, but the clean loopback remains caller `0x00c0` /
answerer `0x00c2`; combining the hold with a late reader hold has the same
result. The V90A hold is therefore retained as an opt-in fidelity probe, not
promoted to the default path.

## Loopback harness boundary cleanup (2026-08-21)

The loopback driver now enables `EICON_EXPAND_SPORT=1` automatically for a
direct `pri117` V.90D answerer unless the caller explicitly supplies a value.
This is the hardware-correct 2185N SPORT receive representation and prevents
the normal harness from silently falling back during INFO; the lower-level
`line_codec_rx_word()` A/B default remains unchanged for compatibility tests.

With that correction, the ordinary unpinned analog109 V.90A to pri117 V.90D
loopback reaches the previously qualified terminal pair (`0x00c0` / `0x00c2`),
but still does not reach `0x00d0`. The direct V.90D U-code-table A/B and the
analogue SPORT-TX-latch A/B both regress earlier (`0x00b3`/`0x00b2` and
`0x0030`/`0x0028`, respectively), so neither is promoted as a fix.

Further page-14-only answerer receive-gain tests at `+3 dB` and `+6 dB` also
leave the unpinned pair at `0x00c0` / `0x00c2`. The decoder therefore has no
useful hard level threshold in the tested range; the missing progress is in the
decoded waveform/control sequence.

## Live Phase-3 bootstrap boundary (2026-08-21)

A complete caller output-resampler phase sweep (`0..5`) produced the same
short-run boundary on every setting: caller `0x00b0 -> 0x00b3`, answerer
`0x00b1 -> 0x00b2`. This makes a one-sample 5:6 codec phase error unlikely.

As a complementary A/B, replacing only the caller's upstream with the
known-good `run65.rx.ulaw` recording caused the live direct V.90D answerer to
generate its own response and reach `0x00d0` at 22.90 s. The caller remained
around `0x0095` and later fell back. Thus the answerer generator and SPORT
expansion can complete the exchange when the upstream is valid; the remaining
unpinned failure is in the caller's live Phase-3 response/receive path, not a
simple DAA gain or answerer-side V.90D entry defect.

The shaper watch adds an important state distinction. In the current clean
loopback, the caller's `0x0095` path selects `DM(0x2119)=0x32c4` and the live
`0x0a92` symbol-buffer writes are zero; its raw analogue TX capture is
therefore all-zero through the `0x0095 -> 0x00b3` window. This differs from the
earlier `0x0092` park trace, where `0x32ca` read varying QAM symbols from the
same buffer. A late `0x32ca` reader override makes the TX stream nonzero but
does not move the answerer beyond `0x00c2`, so the missing input/mapping state
precedes the final shaper selection and is not fixed by forcing the reader.

## V.90A TXD0 mailbox ownership probe (2026-08-21)

PM `0x3d84` consumes the analogue page's host-facing `DM(0x3f05)` TXD0 word.
The direct kernel-dispatch path previously left TIKRNL's `0xffff` mark-fill in
that mailbox. An opt-in probe (`EICON_V90A_TX_PRBS=1`) now suppresses the live
mark-fill store and publishes changing MSB-first PRBS words at the actual
SPORT frame boundary; the trace confirms PM `0x3d84` reads values such as
`0x4b22`, `0x31d8`, and `0x842e` rather than `0xffff`.

The answerer nevertheless remains at `0x00c2`. This closes the mailbox-
ownership hypothesis: changing TXD0 proves the host handoff reaches the
analogue modulator, but arbitrary PRBS is not the protocol-specific V.90A
Phase-3 training source required by the V.90D peer. The probe remains
diagnostic-only and disabled by default.

## SPORT1 callback source A/B (2026-08-21)

The first SPORT-TX A/B was corrected after finding that it selected the
emulator's frame-status return rather than the value written by the SPORT1 TX
callback. The callback trace shows real nonzero SPORT1 words, and they differ
from the direct `DM(0x3fb4)` publication. Consuming the actual callback word
with `EICON_ANALOG_USE_SPORT_TX=1` nevertheless regresses the clean loopback
to caller `0x0030` / answerer `0x0028` in 28 seconds. The default DM source,
which reaches `0x00c0` / `0x00c2`, remains correct for this harness; the
callback path is retained only as a diagnostic comparator.
## Dual-prime release is a warm-start diagnostic, not a loopback fix (2026-08-21)

The capture in `artifacts/loopback-v90a-dualprime-release/` primes both directions
with known-good `run65` reference streams, then releases them to live RTP. After
release, the ordinary firmware walks the remaining ladder without status pins:
the caller reaches `0x00d0` at 39.28 s and the answerer reaches `0x00d0` at about
37.10 s. This proves the resident page state and codec handoff can remain live
long enough to complete once both receive paths already contain a valid Phase-3
history.

It does not qualify as the requested fix. A fresh unprimed call still stops at
caller `0x00c0` / answerer `0x00c2`, and releasing only one side's prime does not
produce a coupled call. The release experiment therefore identifies a missing
initial/reactive signal history, not a bad DAA scale, SPORT representation, or
MSTAT arithmetic mode. Do not promote the prime path or its final DM snapshot to
the default harness; the next implementation target remains a reactive V.90D
Phase-3 producer that responds to the live V.90A symbols.

## Reactive SIP peer A/B: caller reaches 0x00b3 (2026-08-21)

The existing fast-JM build of the sibling `sip_v90_modem` was bound explicitly
to `127.0.0.1` and connected directly to the live `analog109` caller. This is a
reactive peer test, not a recording or a status pin. V.8 completed, the peer
selected V.90, and the caller advanced through the first live Phase-3 exchange:
`0x0092 -> 0x0094 -> 0x00b0 -> 0x00b2 -> 0x00b3` (the caller reached `0x00b3`
at about 13.1 s). RTP had no loss or substitution.

The call still did not reach data mode. The peer remained in its V.34/V.90
training wait for the caller's next response; the caller later retrained from
`0x00b3` at about 27.5 s. This is nevertheless a stronger boundary than the
two-firmware loopback: the V.90A receive path accepts a genuinely reactive
digital peer through `0x00b3`, so the remaining failure is the peer's missing
post-`0x00b3` response or the corresponding V.90A transmit/control exchange,
not a basic DAA, codec, SPORT, or caller detector failure. The capture is kept
at `artifacts/loopback-v90a-reactive-peer-fastjm-current/`; the peer binary is
diagnostic-only and was not promoted into the harness.

## Targeted 0x00b3 shaper A/B reaches the terminal exchange (2026-08-21)

The same reactive-peer test was repeated with the existing `reader` shaper
override restricted to `EICON_V90A_TX_SHAPER_STATES=0x00b3`. This changes only
the caller's transmit sub-shaper while it is in the `0x00b3` state. The result
changed materially: the caller advanced `0x00b3 -> 0x00b6 -> 0x00c0` at about
16.4 s instead of retraining from `0x00b3`, and the peer entered repeated Phase-4
MP generation. RTP remained lossless.

The caller then held at `0x00c0`, so this is not yet the final fix. It does,
however, make the earlier zero-output observation actionable: the normal caller
selected the silence writer (`PM 0x32c4`) throughout `0x00b3`, while selecting
the symbol reader (`PM 0x32ca`) lets the live peer receive enough response to
reach its Phase-4 exchange. The override remains diagnostic-only; the next step
is to recover the native state/record condition that should select the reader
at `0x00b3`, then solve the remaining `0x00c0` terminal mapping/status exchange.
Capture: `artifacts/loopback-v90a-reactive-peer-fastjm-b3reader/`.

## Selector-input trace for the firmware-backed A/B (2026-08-21)

A fresh firmware-backed run watched the selector inputs while the `0x00b3`
reader override was active. The caller's control word reached
`DM(0x20e9)=0x1340` at the `0x00b3` boundary; the override then changed the
`PM 0x258a` result to `DM(0x2119)=0x32ca`. Before that transition the same
record path produced `0x20e9=0x0310` and `DM(0x2119)=0x32c4`. The caller still
ended at `0x00c0` and the answerer at `0x00c2`.

This narrows the implementation target to the dynamic mapping/control words
feeding the `0x20e9=0x1340` record, rather than the DAA or codec scaling. The
reader correction is real for the early response, but it does not supply the
remaining V.90D mapping/status exchange. Capture:
`artifacts/loopback-v90a-b3reader-watch/`.

## Native mapping-word replay regresses the live exchange (2026-08-21)

The native/current `DM(0x3fa7)` phase difference was tested directly with hard,
state-gated pins on the caller: `0xfe10`, `0x02fc`, and `0x00d7` were replayed
in states `0x00b0`, `0x00b1`, and `0x00b2`, respectively. This is not a
portable correction: the firmware-backed loopback regressed to caller
`0x00b3` / answerer `0x00b0`, compared with the normal `0x00c0` / `0x00c2`
pair.

The mapping-word phase shift is therefore downstream of the live response
history, not a missing static native table value. The pins remain diagnostic
only. Capture: `artifacts/loopback-v90a-native-map-remap-20260821/`.

## Reader override across 0x00b0–0x00b3 does not clear c2 (2026-08-21)

The reader shaper was enabled in every pre-terminal caller state
(`EICON_V90A_TX_SHAPER_STATES=0x00b0,0x00b1,0x00b2,0x00b3`) against the
firmware-backed PRI117 peer. The result was unchanged from the b3-only A/B:
caller `0x00b6 -> 0x00c0`, answerer `0x00c0 -> 0x00c2`, and no data mode.

This rules out an earlier silence-selection window as the sole cause of the
remaining c0/c2 wall. The b3 reader effect remains a real early-response
diagnostic, but the terminal failure is in the subsequent reactive waveform,
mapping, or rate/status exchange. Capture:
`artifacts/loopback-v90a-reader-preterminal-20260821/`.

## V.90D c2 result-word override does not bootstrap the peer (2026-08-21)

The direct V.90D answerer was rerun with the c2-gated diagnostic
`EICON_V90D_RESULT_OVERRIDE=0x0000/0x000f`. The capture confirms that the
override is active (`v90d_result_lo/hi=0x0000/0x000f` throughout c2), but the
pair still ends at caller `0x00c0` / answerer `0x00c2`.

This rules out a simple result-word publication or downstream status handoff
as the missing transition. Combined with the negative c2 rate pin, the next
comparison belongs at the equalizer/filter input phase history and the
waveform that feeds it. Capture:
`artifacts/loopback-v90a-v90d-result-override-20260821/`.
