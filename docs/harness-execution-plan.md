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
per-frame mapping-block write counts and PM ownership-call counts. A true
replay oracle and state-sequence A/B are still required.

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
bounded history does not change its state sequence or generated samples.

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
