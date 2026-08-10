# ADSP modem harness fidelity plan

## Objective

Reproduce the ADSP-2185N SPORT, interrupt, foreground and overlay execution
model closely enough that the unmodified V90D (`0x026a`) and V.34 (`0x0261`)
firmware trains at rates consistent with the physical path.

The physical acceptance reference is the modem-to-modem V.34 call which held
3429 symbols/s and 26,400 bit/s in both directions with zero carrier losses,
rate renegotiations or retrains. `CONNECT 115200` was its DTE rate; `AT#UD`
reported the 26,400 bit/s line rate.

## Non-negotiable rules

1. Preserve exact ADSP behaviour; do not turn a diagnostic pin into a default.
2. Keep measured facts, causal results and hypotheses labelled separately.
3. Change one execution-layer variable per A/B.
4. Use buffered live tracing and dump only after firmware decisions.
5. Do not treat V90D page `0x026a` as evidence about V.34 page `0x0261`.
6. Do not implement automatic V.90-to-V.34 fallback until true V.34 connects.

## Established baseline

- RTP continuity, G.711 expansion and the scalar `DM(0x3763)` ingress are exact
  for the captures tested.
- TIKRNL reaches `Core8kRoutine` approximately once per 8 kHz sample.
- V90D sees a large smoothed slicer residual in `DM(0x0fcf)` and consequently
  limits upstream operation to 7,200 and then 4,800 bit/s.
- Echo bulk delay over a 10x range did not materially change that residual or
  rate.
- True V.34 does not connect. The current harness artificially paces page
  `0x0261`, which still emits DC/broadband instead of valid training carriers.
- The current execution path uses synthetic continuation calls, explicit
  PC/stack manipulation, context save/restore injection and a V90D mapping-block
  clear suppression. Correct sample cadence does not validate that chronology.
- INFO1d has a separate standards/configuration issue: it suppresses 3000 baud
  despite both ends advertising 3000 support. Keep this visible, but do not use
  INFO forcing to hide an execution-model defect.

## Phase 0 — Freeze reproducible oracles

**Status:** in progress. Session 244 added the bounded C-core SPORT entry/return
snapshot ring and its fixed ABI test. The harness now records source-image
SHA-256 values (`EICON_IMAGE_HASHES`) and, when execution history is enabled,
per-frame mapping-block and `DM(0x3763)` write counts, SPORT TX publication
counts, PM ownership-call counts, and the native bulk descriptor/input/output
ABI with its publisher/worker/slicer coverage. A true replay oracle and
state-sequence A/B are still required.

### Work

- Preserve the current V90D second-call replay and its state/DM traces.
- Preserve a true `0x0261` failure capture for both calling and answering roles.
- Record hashes of all kernel, TIKRNL and overlay images used by each oracle.
- Add a bounded per-sample execution-history ring containing:
  - sample index and resident overlay;
  - PC/PPC, idle state and cycle count at SPORT assertion and return;
  - interrupt latch/mask/nesting and PC/status stack depths;
  - MSTAT/ASTAT and active register bank;
  - natural calls to PM `0x02b7`, `0x0703` and `0x06c8`;
  - writes to `DM(0x3763)`, the TX SPORT word, and the mapping block;
  - `DM(0x0efb/0x0efc)`, `DM(0x0fce/0x0fcf)` and `DM(0x20ba)`.

### Exit gate

A replay produces a deterministic machine-readable baseline, and enabling the
bounded history does not change its state sequence or generated samples. The
CSV can be compared with `tools/compare_execution_history.py`, which reports
the first differing sample and field.

The first oracle A/B used the same `run29.rx.ulaw` capture and identical image
hashes. Legacy completed 4,001 frames; SPORT stopped during native TIKRNL
activation before media history began (`DM(0x3131)=0x000d`, `DM(0x3137)=1`).
This is the first deterministic divergence to investigate, not a rate result.

## Phase 1 — Inventory every harness intervention

**Status:** initial inventory is recorded in
`docs/harness-intervention-inventory.md`. The remaining gap is a
resident-qualified runtime trace proving the natural caller/owner of PM
`0x06c8`; the explicit legacy call is now listed separately from firmware
execution.

### Work

Create one table with, for every intervention:

- source file and line;
- pages affected;
- default state;
- firmware behaviour replaced;
- original evidence that required it;
- current causal evidence that it is still necessary.

The first entries must include:

- synthetic continuation PM `0x06c8`;
- selected foreground PM `0x02b7`;
- synthetic return PC `0x02a8`;
- non-idle context save/restore injection;
- suppression of PM `0x06ca..0x06cd` mapping-block clears;
- portable bulk-delay servicing;
- V.34 publish stop/yield/pacing;
- direct dispatch and bearer-activation paths.

Statically reconstruct the intended chain from the actual images:

```text
SPORT interrupt -> kernel/TIKRNL owner -> selected task -> PM 0x0703
                 -> RTI -> interrupted foreground
```

Locate every natural caller of PM `0x06c8` and determine which owner is
supposed to clear and refill `DM(0x3fa7..0x3fac)`.

### Exit gate

Every non-firmware action is accounted for, and the intended interrupt/call/
return ownership is documented without relying on current harness comments.

## Phase 2 — Add a pure SPORT execution model

**Status:** opt-in frame boundary is now wired. `EICON_EXECUTION_MODEL=sport`
uses the C-core SPORT0 frame entry and does not inject a continuation, pace a
V.34 publish, latch a page publish, or suppress the mapping-block clear. It is
an execution probe, not yet a qualified modem path; the first deterministic
ownership divergence is the next measurement.

### Work

Add a selectable execution model, initially opt-in:

```text
EICON_EXECUTION_MODEL=legacy
EICON_EXECUTION_MODEL=sport
```

The `sport` model must:

1. publish exactly one selected line sample at the real SPORT boundary;
2. assert the real SPORT interrupt rather than assign a continuation PC;
3. let the kernel and TIKRNL reach the selected task naturally;
4. return through the firmware's RTI path;
5. resume the interrupted foreground with hardware interrupt semantics;
6. use a cycle budget derived from the ADSP clock and SPORT timing;
7. make no synthetic call to `0x06c8`;
8. make no mapping-block clear suppression;
9. make no page-specific publish stop/yield.

Do not repair a failure by adding another page-specific patch. Capture the first
divergence from the intended ownership chain and fix that owner.

### Exit gates

- Exactly one `DM(0x3763)` publication and one `Core8kRoutine` call per sample.
- Exactly one line-side TX sample selected per sample without decimating multiple
  firmware publications.
- No stack leakage, interrupt nesting growth or register-bank drift over 60 s.
- Mapping frames survive because firmware ownership is correct, not because a
  store is suppressed.

## Phase 3 — Qualify ADSP arithmetic and interrupt semantics

### Work

Build focused instruction tests from the hot paths before the slicer and
quality average, especially:

- fractional signed multiply/MAC and MR rounding;
- `SAT MR` and overflow stickiness;
- `ASHIFT`/`LSHIFT` with OR forms;
- MSTAT mode and bank-switch latency;
- ASTAT condition behaviour after multifunction instructions;
- DAG modify, circular wrap and zero-length behaviour;
- interrupt entry/RTI preservation of PC, status and loop stacks.

Use small firmware kernels with independently calculated fixed-point results.
Prioritize PM `0x32f7..0x335d` and the equalizer, carrier and timing producers
feeding it.

### Exit gate

Every opcode and mode used by the slicer path has a boundary-value conformance
test, including positive/negative saturation and rounding ties.

## Phase 4 — Resolve the V90D low-rate decision

### Work

Run the same recorded receive stream under `legacy` and `sport` modes and find
the first divergence in:

1. carrier/timing phase;
2. equalizer coefficients;
3. equalized `(I,Q)` point;
4. selected constellation point;
5. `DM(0x0efb/0x0efc)` residual;
6. `DM(0x0fcf)` average and `DM(0x20ba)` rate class.

Add a bounded slicer scatter dump. Interpret it as:

- tight points with wrong decisions: constellation/negotiation mismatch;
- rotating points: carrier/phase recovery;
- diffuse points: equalizer/arithmetic/timing;
- clean points and inflated residual: residual arithmetic defect.

Only after the execution model is qualified, run the narrow INFO experiment:
enable 3000 alone, verify the CRC-valid INFO1d on the wire, and observe the
peer's selected symbol rate. Treat 3429 as a separate optional-capability test.

### Exit gate

Three live calls against the same modem/path reach data above 20,000 bit/s and
remain there for at least 60 s without a diagnostic DM/PM pin or recovery clamp.

## Phase 5 — Make true V.34 work

### Work

- Force V.34 through driver-faithful modulation options and verify resident
  page `0x0261`; a 33,600 ceiling is not proof of V.34 selection.
- Run page `0x0261` only under the qualified SPORT model.
- Remove artificial publish pacing and identify the natural sample owner.
- Compare every Phase 2/3 transmitted segment with a physical V.34 capture by
  RMS, passband energy, carrier, symbol-rate line and state transition cause.
- Require detector-driven transitions; timer progression without the expected
  received signal is a failure.

### Exit gate

Three true `0x0261` calls connect above 20,000 bit/s, exchange bilateral data,
and remain stable for 60 s with no page-specific execution patch.

## Phase 6 — Implement and qualify fallback

Only after Phase 5 passes:

1. detect the firmware's full-retrain/fallback decision;
2. restart V.8 with V.90 disabled through normal modem options;
3. verify the next resident data page is `0x0261`;
4. preserve the call and data interface across the transition;
5. test V90D -> V.34 fallback repeatedly under induced V90D failure.

### Exit gate

Five induced V.90 failures fall back to true V.34, retain the call, exchange
bilateral payload and produce no host-side fabricated state or rate word.

## Immediate work order

1. Phase 0 bounded execution-history ring.
2. Phase 1 intervention inventory and natural PM `0x06c8` ownership.
3. Implement opt-in `sport` mode with no synthetic continuation.
4. Stop at and explain the first deterministic divergence.
5. Add arithmetic tests for only the path reached before that divergence.
6. Reassess INFO1d and rate selection after execution fidelity is established.

The first success criterion is not a higher reported rate. It is removal of a
harness intervention while preserving correct firmware-owned execution. Rate
qualification follows only after that foundation is measurable.

The activation failure found by the first oracle A/B is explicitly in scope:
pre-media native TIKRNL setup must consume its pending work request through the
same SPORT/interrupt ownership chain before media history can be compared. A
SPORT run that reaches media only by restoring a setup-time synthetic
continuation has not passed Phase 2; capture the setup divergence instead.
The setup trace now shows the first frame reaches PM `0x06c8` four times and
PM `0x0703` once under SPORT, while legacy reaches `0x06c8` five times and
`0x0703` four times; legacy's extra `0x06c8` is the host continuation. The
pending `DM(0x3eee)=0x2000` / `DM(0x3131)=0x000d` request therefore remains
unconsumed in SPORT. The first lifecycle A/B fix is now known: SPORT must keep
`native_bearer_activation` enabled while the initial WDB is consumed. Disabling
it selected the compatibility path and removed the firmware-owned private
descriptor. With that correction, SPORT completes activation and a 4,001-frame
replay. The old legacy-vs-SPORT comparison diverges at sample 1653, where
the resident overlays differ (`0x025f` versus `0x0271`), but that is not yet a
failure: legacy disables native ownership during WDB setup and therefore skips
native intermediate page `0x0263`. Do not force SPORT to match that altered
oracle; compare the native SPORT sequence against the physical page/state
trace instead. SPORT now clocks the pending `0x025f` handoff for three
bounded pre-media frames, matching the physical capture's `0x025f` resident
page at media start. Those setup clocks are part of activation timing, not a
page-specific media patch.
The 2185N data sheet specifies that an interrupt from IDLE resumes at the
instruction following IDLE. The core's pre-instruction PC advance already
models that behavior; the focused SPORT0 RX/RTI test now guards it.
The core regression suite now covers the SPORT0 priority-4 entry with the
line held asserted through an unconditional RTI, alongside the existing
2185N BIASRND midpoint test. This validates the modeled baseline; it does not
yet establish that every 2185N SPORT control-register bit is implemented.

A full 18-second `run29.rx.ulaw` A/B reached `0x025f`, `0x0260`, and `0x026a`
with identical images. SPORT published exactly one `DM(0x3763)` word per frame
and one TX word per frame until V90D naturally stopped publishing. Its first
functional divergence was V90D remaining at `TrnProgress 0x0060`, while legacy
continued through `0x007b`. The cause was another unlisted legacy intervention:
`_service_bulk_lengths()` injected nonzero delay lengths in SPORT mode, which
activated the native PM `0x1900..0x19c8` worker before a firmware rate/length
ABI existed. Both independently controlled A/Bs—holding PM `0x19c8` at RTS, or
disabling the synthetic length seed—restored the legacy state sequence; holding
the mapping-block clear did not. SPORT now excludes the synthetic seed rather
than patching the worker, preserving firmware's zero-length gate. Bounded frame
tracing is also armed on the native SPORT branch now, not only the compatibility
branch.

The next divergence is now bounded rather than hidden: with the host length
seed correctly absent, SPORT's V90D slicer words `DM(0x0efb/0x0efc)` and
quality average `DM(0x0fce/0x0fcf)` remain zero through the complete 23.3-second
oracle. A diagnostic arm combining the nonzero seed with PM `0x19c8 = RTS`
reaches PM `0x3303/0x3305`, but that is not a valid owner model.

The expanded history settles the publication question but not the native
worker's missing precondition. Firmware PM `0x3235` and PM `0x1086` naturally
publish the lengths once on V90D; PM `0x19fd` publishes `DM(0x3fa7)` as
BulkInputX; and PM `0x19c8` enters the worker. No selected-channel host length
writer is missing. On the open-loop oracle BulkInputX/Y stay zero and the
failure is hidden.

Closed-loop SPORT run39 against tower `d-modem` supplies the decisive positive
control. The page naturally published near/far `0x0e89/0x0b00` and entered the
worker at sample 94554. At sample 99651 the worker overwrote RTT
`DM(0x3fcb)=0xfa64`; by 99669 it had replaced the lengths with
`0x2ec6/0x6510`; boot pages and state records then became arbitrary, with
unserved random page requests until the call ended. The peer reported
`NO CARRIER`. This reproduces on SPORT the hardware escape that made portable
bulk delay the default: coherent lengths and live inputs are not the worker's
complete safe ABI. SPORT must therefore hold PM `0x19c8` and service the bounded
portable database ABI too. Native release remains diagnostic-only; neither the
synthetic length seed nor a native release is part of the SPORT default.

Closed-loop run40 validates that policy against the same tower `d-modem`. SPORT
held the worker, used the naturally published lengths (no synthetic seed), and
walked cleanly through `0x0060 -> 0x007a -> 0x007b -> 0x0080 -> 0x00b0` with
6,830 natural PM `0x3303/0x3305` slicer publications and no random page request
or bulk-state corruption.

Matched legacy run41 reaches the same `0x00b0` ceiling and the peer reports the
same failure measurements to rounding: timing offset `+6620 -> +7289 ppm`, then
`VPcmFloModem (V90): drop to V34 requested` and `NO CARRIER`. Run40 and run41
therefore separate the remaining connection failure from SPORT execution.

`tools/v90_tx_validate.py` now checks raw PCMU against Table 1/V.90 and
§8.4.4/§8.4.5 rather than judging the waveform. Run40 contains the exact
384T Sd plus 48T S-bar-d sequence and zero errors over the first 2040T of GPC
TRN1d signs, but its TRN1d magnitude is Ucode 49 while received UINFO is 48.
The runtime table at `DM(0x1f14..0x1f93)` is the A-law linear table even though
the staged page and selected descriptor are PCMU. A bounded diagnostic installs
the staged PCMU magnitudes. Run42 showed that copying literal Ucode 0 loses
negative-zero polarity and worsens the peer estimate to `+11864 ppm`. Run43
retained the page's +/-2 zero sentinel while selecting PCMU magnitudes and is
fully exact: Sd, S-bar-d, TRN1d Ucode 48, and 2040/2040 GPC signs. The peer still
reports the original `+6620 -> +7289 ppm` and drops at the same instant.

That A/B rejects downstream Phase-3 codeword content as the cause of tower
`d-modem`'s estimate, but run43 also establishes the correct PCMU table
boundary. The staged PCMU magnitudes plus the signed-zero sentinel are now the
default for PCMU calls; `EICON_V90D_PCMU_UCODE_TABLE=0` retains the old A-law
resident-table regression. The next boundary is tower `d-modem`'s 8 kHz
network-to-9.6 kHz SmartLink
resampler. Run43's `/tmp/dm_to_dsp.raw` supplies the first check: expanding the
captured GPC signs at exactly 6/5 aligns at raw sample 137386, and the best
offset remains **zero** in every 2,000-symbol window through 38,000 input
symbols. There is no accumulated clock drift corresponding to `+7289 ppm`.
The resampled polarity agrees on 87-91% of samples, with disagreement confined
to interpolation around transitions. Thus SmartLink's number is a filter/shape
estimate rather than the actual symbol cadence.

That tower-bridge A/B was run next, with run43's exact Eicon stream held fixed:

| run | tower 8k->9.6k bridge | peer result |
|---|---|---|
| 44 | 257-tap sinc, gain 0.25 | Sd arrival falls below the usable boundary; two errors then `energy drop` |
| 45 | 257-tap sinc, gain 0.50 | `+5891 -> +7279 -> +7255 ppm`, Phase-3 drop |
| 46 | 321-tap sinc, gain 0.50 | `+6055 -> +6833 ppm`, Phase-3 drop |
| 47 | 321 taps, +1/12-sample kernel phase | `+6616 -> +7407 ppm`, Phase-3 drop |
| 48 | 321 taps, -1/12-sample kernel phase | `+5903 -> +7261 -> +7236 ppm`, Phase-3 drop |

The 321-tap kernel is the best filter result, but only moves the estimate by
about 450 ppm and does not change the protocol outcome. The old 0.25 bridge is
not a valid Eicon setting: the historical direct-C reference emits about four
times the pre-bridge level, so its 0.25 output has TRN magnitude 943; applying
the same scalar to Eicon reduces its already-correct PCMU magnitude to about
231. Gain 0.5 and unity remain equivalent at the failure boundary, while 0.25
causes an earlier energy-drop decision. Constant kernel phase is also rejected.

The known-good direct-C capture and Eicon capture both have exact 6/5 cadence,
similar Phase-3 RMS at SmartLink (943 versus 925), and the same near-Nyquist
periodic onset. Filter length, scalar gain, and constant fractional phase do not
explain the failure.

The simple missing boundary was found in runs 49-65. A three-input-sample delay
moves the sinc estimate from `+7289` to `+5136 ppm`, proving strong polyphase
sensitivity, but delays 0/1/2/4 and 321 taps do not lock. Pure ZOH does lock:
run54 converges to `+2.25 ppm` and zero error, but corrupts Sd enough that
SmartLink remains in `WaitForSd`. The two training regions therefore cannot use
the same reconstruction:

- preserve the 257-tap 4 kHz sinc through the exact Sd and S-bar-d detector;
- at the first TRN1d symbol, change to an ordinary interpolator for the random
  polarity stream and later mapped symbols.

The bridge's existing `loop_find_trn1d_start()` provides that exact content
boundary. Switching there gives these matched results:

| interpolation after S-bar-d | final timing/error before decision | result |
|---|---|---|
| 3-tap/exact ZOH | `+71 ppm`, error about 900 | Phase-3 timeout |
| linear | `-1.3 ppm`, error about 350 | Phase-3 timeout |
| six-point (degree-5) Lagrange | `-0.25 ppm`, error about 65 | **Phase 4 and physical CONNECT** |

Run65 is the first complete physical V.90 link against tower `d-modem`. The card
naturally traverses `0x00b0 -> 0x00b1 -> 0x00b2 -> 0x00b3 -> 0x00b6 -> 0x00c0
-> 0x00c2 -> 0x00c4 -> 0x00c6 -> 0x00c8 -> 0x00cc -> 0x00d0`, asserts
DSR/CTS/DCD, and negotiates **30,667 bit/s downstream and 14,400 bit/s
upstream**. SmartLink enters Phase 4, receives MP/MPnot/Ed, and reaches data
status. This rejects every Eicon scheduler/timing hypothesis: the blocker was
tower's use of a near-Nyquist Sd interpolator for TRN1d and mapped data.

Run66 repeats the physical connection with the harness V.42 endpoint. The peer's
default data mode sends mark/idle, so T400 correctly chooses the
non-error-corrected fallback (`0` XID/SABME); this is now above, not below, the
physical-layer result.

Runs 69-70 exposed one final simple bridge bug: the S-bar-d detector discarded a
valid boundary when its 48-symbol tail crossed a 160-sample media frame. It now
carries a pending TRN1d offset into the next frame. Extending the post-S-bar-d
interpolator from six to eight Lagrange points removes the remaining polyphase
sensitivity. Run73 then completes the whole stack with peer `AT\\N3`:

- SmartLink reports `CONNECT 32000` and protocol `LAPM V.42`;
- the harness receives XID and SABME, returns XID and UA, and reports
  `LAPM connected (SABME received), link 1`;
- `PEER_TO_EICON_73_PAYLOAD\\r\\n` arrives byte-exact at the harness PTY;
- `EICON_TO_PEER_73_PAYLOAD\\r\\n` arrives byte-exact at the peer DTE;
- PTY accounting records 26 bytes sent and 6,347 received (6,321 pre-link mark
  bytes plus the exact 26-byte peer payload), with no full-window stalls.

This is the first complete V.90 + LAPM + bidirectional application-data call.
`tools/dmodem_v90_bridge.patch` records the qualified tower change: 257-tap
sinc through Sd/S-bar-d, deterministic cross-frame boundary detection, then
eight-point Lagrange interpolation from TRN1d onward. It is installed in tower
`d-modem` as `/src/d-modem` (SHA-256 `8ea8a1c1...`) with `dm-wrap.sh` defaulting
to `DM_RESAMPLER=hybrid`, `DM_RS_HEADROOM=1.0`, and `DM_LOOP_TAPS=3`; the prior
source and wrapper have `bak-pre-v90-lagrange8` backups. Keep the Eicon firmware
state unchanged.

### Run76 and replay arithmetic audit: the 14,400 upstream ceiling is before the slicer residual

A matched live legacy call through the qualified tower bridge reaches the same
32,000/14,400 data state as SPORT run73. SmartLink decodes the same Eicon MP
`Rate14400`, and the card publishes the same `DATASTATEspeedTx=0x2023` and
`DATASTATESpeed=0x11ec`. The execution model therefore does not select the low
upstream rate.

Two timing A/Bs are exact negatives. Delaying or advancing the recorded SPORT
input by one 8 kHz sample changes the settled slicer residual by less than one
unit (`345.9` control versus `346.2/345.5` mean L1/2). Reducing the per-frame
execution allowance from 20,000 to 5,000 and 4,000 cycles produces byte-identical
128,001-row histories (SHA-256 `822959...`). Neither sample/ISR phase nor excess
foreground allowance explains the rate.

A bounded PM `0x32b3` scatter trace captures coherent points before the residual
routine. The firmware's chosen TRN constellation is exact and stable at the four
corners `(+/-4578,+/-4578)`; the rotated/equalized observations scatter around
those corners with mean L1/2 residual about 316 in the sampled window. PM
`0x32fe..0x3305` then stores the literal component subtraction, with no hidden
scale. PM `0x3348..0x335d` publishes the expected leaky average. A new C-core
kernel test independently verifies that exact ABS, divide-by-two, parallel MR
move, fractional unsigned MAC and saturation sequence; the observed
`0x012d:0x3456 -> 0x012d:0x345f` update is bit exact. The complex rotation at PM
`0x0d5e..0x0d61` is also independently reproducible from trace operands; for the
first captured vector it yields the firmware's exact `0x1273/0xee87` output.
The rate-limiting error is therefore present in the signal/equalizer values
feeding that rotation, not introduced by residual or averaging arithmetic.

The SPORT helper does contain a separate fidelity gap: its compatibility path
ignores `active_slot`, `dispatch_slot`, and `idle_word`, invokes only one IRQ,
and duplicates the selected sample through all 64 TDM history words. An opt-in
`EICON_SPORT_FULL_TDM=1` now exercises 31 idle slots then the selected slot.
That diagnostic reaches V90D but stops at `0x0060` before executing the slicer,
because the private per-slot descriptor owner is not reconstructed; it cannot
be used as a rate oracle and remains off by default. It does establish the next
SPORT task precisely: recover the natural per-slot descriptor/dispatch instead
of treating the current scalar publication as proof of complete TDM semantics.
The next receive arithmetic boundary is upstream of PM `0x0d5e`, in the filter
and equalizer producers of `DM(0x0ef9/0x0efa)` and the carrier rotation
coefficients, not `DM(0x0efb/0x0efc -> 0x0fcf)`.

### Session 244: the receive equalizer is located, adapting, and converged

`tools/v90_rx_equalizer_probe.py` walks the receive chain outwards from
`DM(0x0ef9/0x0efa)` with the core's DM/PM write and execution watches. The
observation pair the residual subtracts from is written by a **54-tap complex
FIR at PM `0x0b4e..0x0b7e`**:

```text
DM(0x0ef9) = sum(x_r*h_r - x_i*h_i)     stored at PM 0x0b6c
DM(0x0efa) = sum(x_r*h_i + x_i*h_r)     stored at PM 0x0b78
```

Its geometry is fully recovered. `L0`/`L1` are `0x90` data lines based at
`DM(0x201f)` (real) and `DM(0x2020)` (imaginary, always `+0x100`), walked with
tap stride `M3 = 2`. `L5`/`L6` are `0x36` coefficient rings based at
`DM(0x2023) -> PM 0x1f80` and `DM(0x2024) -> PM 0x1fc0`. Both accumulators are
realigned two bits left through the shifter's HI/LO pair and rounded at PM
`0x0b67..0x0b6b` before the store.

The loop around it is closed and live, which is a causal negative for three
hypotheses at once:

- **Adaptation runs.** PM `0x0bab..0x0bbd` is a 32-bit LMS update driven by
  exactly the residual pair `DM(0x0efb/0x0efc)`. A PM write watch catches it
  updating `PM 0x1f80` and `PM 0x1fc0` on every 3,200-baud symbol. Tap low
  halves live in the rings based at `DM(0x2025) -> PM 0x25c0` and
  `DM(0x2026) -> PM 0x2600`; `DM(0x2074)` (`0x12`) updates 19 complex taps per
  symbol from a base that walks with the data pointer, so all 54 are covered.
  PM `0x0bbe..0x0bcf` applies a leak every `DM(0x0e04)` symbols.
- **The taps converge to a sane filter.** At 14.2 s of run76 the profile is a
  single main lobe at tap 33 (`|h| = 7955`) with a decaying tail and the
  period-3 side structure a T/3-spaced equalizer should have; total rms 1384.
  It is neither diverged nor stuck at its initial values.
- **The realignment arithmetic is exact.** A new C-core kernel test replays PM
  `0x0b67..0x0b6c` from seeded `MR1`/`MR0` and reproduces the hand-derived
  `0x1413`, pinning the OR-ed HI/LO shifter halves, the parallel `MR1`/`MR0`
  moves, and `(RND)` adding `0x8000` to `MR0` without disturbing `MR2`.

The chain feeding the filter is also now named. PM `0x0fc1..0x0fcb` pushes one
complex sample into the data lines (`MX0` real, `SR1` imaginary), decrementing
`DM(0x201f)` and `DM(0x2020)` by one per call, and PM `0x0f93..0x0fa3` is the
gain/interpolation stage that runs immediately before each push, over the
`0x20`-word ring at `DM(0x2130)` with `DM(0x0a29)` as multiplier. Both are
driven from the per-sample dispatch at PM `0x2a93..0x2a97`
(`I4 = DM(0x201b)`, fetch handler, `CALL (I4)`), whose handler table is
`DM(0x0008..0x000b) = {0x2ac7, 0x2ad2, 0x2ae0, 0x2b1b}`; PM `0x2ada` rewinds
`DM(0x201b)` by three, which is what makes the equalizer run at three samples
per symbol, i.e. 9,600 Hz.

One harness intervention was checked in passing and cleared.
`publish_bulk_lower_limit()` computes its base from `DM(0x32f7)`, which is
**zero** on entry to `0x026a`, so its `0xffff` lands on `DM(0x0005)` -- inside
the page-zero block that holds receive handler addresses at page-entry time. A
read watch over the whole training window records no firmware read of
`DM(0x0005)`, so the write is inert here; it is still a host write to an
address the harness has misidentified, and is logged as such.

The receive error floor is therefore **not** in the equalizer, its adaptation,
its arithmetic, or the residual and averaging downstream of it. It is in the
samples arriving at `DM(0x201f)/DM(0x2020)`. The next boundary to audit is the
front end that produces them: PM `0x0f93..0x0fa3` and its input ring at
`DM(0x2130)`, and whatever fills that ring from the SPORT word.
