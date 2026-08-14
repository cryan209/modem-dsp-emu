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
