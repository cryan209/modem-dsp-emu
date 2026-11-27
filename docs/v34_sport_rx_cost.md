# Explicit SPORT1 RX experiment: exact replay, no promotion

**Outcome:** the redundant callback can be removed without changing the replayed
firmware signal/state, but these observations did **not** establish a repeatable
CPU win. The predeclared CPU gate failed. No live control/after set was run,
no SIP/qualifier flag was added, and the live/standalone defaults still use the
legacy callback path. This is an **offline opt-in alternative**, not a pacing fix.

## Candidate and compatibility contract

The [stack observations](v34_stack_observations.md) identified:

```
adsp2181_sport1_frame → ctypes closure → PyGILState_Ensure → take_gil
```

That snapshot occurred on a passing call and ended in mutex unlock, not a proven
blocking wait. Static inspection then established an avoidable round trip:
`AnalogKernelDispatch.frame()` masks/latches its word and passes it to C;
`adsp2181_set_irq()` invokes Python `_rx()`, which returns that same latched word.
A sampled boundary identifies work to investigate; it does not quantify its cost
or explain a 110–136 ms wall-time tail.

The alternative is deliberately separate:

- Existing `adsp2181_set_irq()` and `adsp2181_sport1_frame()` retain their callback
  contract, including custom callbacks that **replace** the supplied frame word.
- New `adsp2181_sport1_frame_explicit_rx()` makes its argument authoritative for
  that frame's injected SPORT1 edge. It omits only that edge's RX callback.
- A private IRQ helper shares the original latch/state/vector logic with an
  explicit receive-fetch parameter. Callback pointers are never nulled/swapped;
  subsequent generic IRQs and legacy frames retain their installed callbacks.
- TX callbacks, actual SPORT latches, frame write flags, cycle budgets, IRQ
  checks and the run-to-IDLE pass are unchanged. Null/nonpositive-budget calls
  retain the no-op/zero-return contract.
- `AnalogKernelDispatch` and `AnalogKernelModem` accept
  `explicit_sport_rx=True` for offline experiments. Both default **False**.
  `eicon_adsp_sip.py` continues constructing the default callback mode.

There is no GIL-policy change, GC-policy change, per-audio-sample timer, fake or
repeated input, new buffer growth, relaxed deadline, capture removal, DSP
arithmetic/reset-map change or physical-hardware claim. The generic IRQ code is
refactored, not byte-identical to the old binary; broad native regression remains
necessary even though defaults still choose the same semantics.

## Replay evidence

Portable native synthetic ISR tests exercise both APIs with signed-word extrema,
RX/previous-TX distinction, omitted TX writes/retained latches and custom RX
callbacks deliberately returning a word different from the argument. They verify
that explicit frames do not invoke that callback and that a subsequent legacy
frame still invokes it and uses its replacement word. Invalid core/budgets do
not invoke callbacks.

The firmware replay compares 4096 deterministic input words, including
0/0x7fff/0x8000/0xffff, for **8000 and 9600 Hz codec rates × DM and emulated
physical-SPORT TX selection**. Both modes boot/configure the calling Analog
firmware independently, with duplicate SPORT history off. Every output, actual
TX latch, frame result and TX callback count agrees. Final converter counters
and exact binary64 histories, pending bearer output, last output, full DM/PM,
emulated cycles, codec-frame counts and frame-write counts agree.

A test-only counting RX callback confirms **one invocation per legacy codec
frame → zero** on the explicit side, including boot frames. No such per-frame
counter/instrumentation is added to the live implementation. This proves removal
of the redundant boundary, not host CPU savings or trained-duplex equivalence.

## Whole-batch CPU protocol

```sh
.venv/bin/python tools/benchmark_sport_rx.py \
  --samples 32000 --pairs 4 --codec-rate 9600 \
  --output artifacts/qualification/NEW.json
# --sport-tx selects the other emulated TX source; it is not physical hardware.
```

The tool refuses existing outputs, sanitizes inherited `EICON_`/`ADSP_` tuning,
sets V.34, independently boots/configures each model, and warms 1024 equal words
outside timing. It alternates callback/explicit then explicit/callback order.
Only whole measured batches have wall/thread-CPU timers; output and final-state
hashes are computed afterward. Native cores are destroyed outside timing.
Thread CPU includes native work on that thread. GC policy is left unchanged.

This is a full Analog sample-pump **offline synthetic replay without a trained
peer**, not native-only CPU, trained DATA, throughput or source-clock proof.
The corrected report fingerprints its script, dispatcher/driver, C/header/shared
library and every file in the selected firmware directory (168 inputs total).
Platform/Python version, input hash and final state/output hashes are retained.

### First fixed batch: retained provenance limitation

| Pair | Callback / explicit CPU ms per 160 | Callback/explicit CPU ratio | Exact |
| --- | --- | --- | --- |
| 0 | 4.2651 / 4.2701 | 0.99884 | yes |
| 1 | 4.3490 / 4.2644 | 1.01983 | yes |
| 2 | 4.3728 / 4.3324 | 1.00933 | yes |
| 3 | 4.4180 / 4.3507 | 1.01545 | yes |

All four output/state comparisons were exact. Ratios above one favor explicit;
one pair was flat/slightly slower, the others about 1–2% faster. The initial
manifest glob expected `.bin` files but these images use extensionless names:
only six source/library hashes were recorded, **not firmware hashes**. This
omission was fixed; the entire original tool source is preserved as
`benchmark-v1.py` and matches that report's script hash. The initial report is
not retrospectively presented as having a complete firmware manifest.

### Corrected-provenance batch and failed gate

Before the corrected batch, `fixed-plan.json` specified four alternating pairs,
all bit-exact and **all four CPU ratios strictly greater than one** as the gate
for a proposed one-control/three-opt-in live set. No default promotion was planned.

| Pair | Callback / explicit CPU ms per 160 | Callback/explicit CPU ratio | Exact |
| --- | --- | --- | --- |
| 0 | 4.4042 / 4.3817 | 1.00512 | yes |
| 1 | 4.4130 / 4.4463 | **0.99251** | yes |
| 2 | 4.3591 / 4.3336 | 1.00590 | yes |
| 3 | 4.3738 / 4.3561 | 1.00407 | yes |

**Protocol deviation:** this batch was inadvertently launched concurrently with
the 2.93 s portable/native preflight command, potentially affecting the first
two pairs. It is retained and explicitly **not an uncontaminated CPU benchmark**.
That concurrency is not subtracted from thread CPU or dismissed because the
host has multiple cores. It makes fine cost attribution weaker, not stronger.

The numeric gate also failed (pair 1 slower). No further timing batches were
run to seek a green gate. No live control/after trials or default promotion
followed. Both complete sets and their limitations remain visible. The data do
**not** prove the callback is free or cannot save CPU; they do not earn a claimed
repeatable speedup or a live default change either. A future clean benchmark
would be a separately declared experiment, not a replacement for these results.

Evidence:

- `artifacts/qualification/sport-rx-batch/{paired.json,benchmark-v1.py,fixed-plan.json,fixed-paired.json}`
- `artifacts/qualification/sport-rx-analysis/decision.json`
- `artifacts/test-results/{preflight-explicit-rx,preflight-explicit-rx-fixed,sport-rx-batch,sport-rx-batch-fixed}.log`

The remaining ordinary-work/no-GC wall tails are still unresolved. This experiment
must not be cited as saved GIL-wait duration, a pacing qualification, or proof
that native callback overhead caused those tails.
