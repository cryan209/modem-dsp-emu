# V.34 GC observations and emulated DSP work

This opt-in follow-up to [CPU versus wall profiling](v34_producer_cpu.md)
asks whether measured tails coincide with Python garbage collection or changes
in emulated DSP work. It does **not** directly time pure native execution versus
Python, identify OS descheduling/GIL/blocking causes, or change recovery policy.

## Commands

```sh
make qualify-v34 V34_ARGS='--calls 1 --producer-detail --max-tx-underruns 0 --max-tx-gap-ms 40 --output artifacts/qualification/my-gc-work'

.venv/bin/python tools/producer_profile.py \
  artifacts/qualification/my-gc-work/call-001/caller-media-traces/producer-profiles/call-001.json \
  --trace artifacts/qualification/my-gc-work/call-001/caller-media-traces/call-001.json
```

`--producer-detail` implies `--producer-profile` and media tracing. The endpoint
requires a trace directory; the qualifier supplies it. Both endpoint receipts
must include closed, error-free detail with complete native-work accounting.
Missing requested detail fails qualification. All existing payload, lifecycle,
source-clock/gap, underrun and loss checks remain unchanged.

## Emulated work, not native CPU time

The observer reads the **existing `adsp2181_cycles(card.cpu)` uint64 counter**
only twice per quantum: before and after the sample pump. There are no
per-sample timing calls, instruction hooks, counter resets or native C changes.
The FFI signature explicitly preserves full-width pointers and 64-bit results.
A missing CPU is rejected; a regressing counter invalidates the receipt.

Per-quantum raw begin/end/delta values are retained, with complete training/data
aggregates (count, sum, minimum and maximum), even if detailed records truncate.
This is the core's existing emulated-cycle accounting, **not** retired host
instructions, hardware performance counters or a measurement of native CPU time.
Fast-path costs need not be linear in this counter. Scope is **`card.cpu` only**,
not other DSP cores, MIPS work or Python resampling/dispatch. Backend identity
is recorded. Compare workloads within a backend; caller and answerer are not
interchangeable (the qualification caller uses a 9,600-Hz codec resampler).

## GC callback scope and limits

A `gc.callbacks` observer records start/stop monotonic wall time and CPU time of
**the collecting thread**, generation, thread identity, main-thread membership,
and collected/uncollectable object counts. It never disables/enables GC,
changes thresholds or requests a collection in the endpoint.

The callback window is explicitly recorded from attachment to detachment. It
can include setup and trace teardown/serialization, not just measured quanta.
Use interval overlap to distinguish production-time events from those outside
the measured producer span; whole-window GC totals are not data-quantum totals.
The profile's own JSON serialization happens after detachment.

Other-thread GC can overlap main-thread native work that released the GIL;
such overlap is **not necessarily a stop of that native work**. Its collector
CPU is not main-thread CPU. Even a main-thread event's observed interval can
include descheduling/blocking. Previously installed callback count is recorded;
callback ordering can affect the observed start/stop span.

The analysis reports raw overlapping events, including partial overlaps. It
never prorates collector CPU, subtracts GC to forgive a deadline, or treats a
coincident event as proof of the cause. A missing event with truncated storage
cannot exclude GC. A missing event in a fully retained observed window is
stronger, but still does not attribute native versus Python execution.

There are at most **512 retained GC events** per call and six generation/thread
category aggregates covering all completed observed collections. Callback
exceptions, unmatched/nested phases, thread/generation mismatch, regressing
clocks, unfinished GC and missing callback at close invalidate detail receipts.
There are no added hot-path file writes. Close removes only this observer,
preserves other callbacks, is idempotent, and breaks its own bound-method cycle.
Observer overhead and extra retained objects can affect the run; a small fixed
set is not a controlled measurement of that overhead.

## Initial fixed set, before changing SPORT history

Five commands ran in predefined order without retries or concurrent deliberate
test/build load: one profile-only control, three detailed nominal calls, then
one detailed 100 ms producer/RX pause. Each used one call, 4,096 independent bytes
per direction, zero allowed underruns, a 40 ms maximum source gap and ±1,000 ppm.

| Case | Whole-call underruns caller / answerer | Maximum source gap ms caller / answerer | Exit |
| --- | --- | --- | --- |
| Profile-only control | 4 / 0 | 53.352 / 34.523 | **1** |
| Detail nominal 001 | 0 / 0 | 36.819 / 26.870 | **0** |
| Detail nominal 002 | 2 / 2 | 98.237 / 92.563 | **1** |
| Detail nominal 003 | 0 / 0 | 26.688 / 27.226 | **0** |
| Detail producer pause | 47 / 77 | 762.186 / 803.221 | **1** |

All five verified exact payloads, one logged data-mode entry/no departure,
zero final RX loss/FCS errors/resets, complete continuous captures and clean
teardown/child exits. All eight detail receipts were complete/error-free, with
no GC-event or primary profile-record drops. The longer profile-only control
lost 182/88 older primary profile records; its aggregates still cover all quanta.
All five sets of **409 runtime input fingerprints** matched the measured source.

### GC contributes some CPU tails, not all timing failures

- Caller nominal 001: **23.030 ms wall / 21.537 ms CPU**, overlapping a main-thread
  generation-2 event of **9.631 ms collector CPU**. Its 543,309 emulated cycles
  were only **1.003×** the call's data mean. Strict timing nevertheless passed.
- Caller nominal 003: **20.579 ms wall / 20.149 ms CPU**, overlapping **8.300 ms**
  main-thread generation-2 CPU, also about **1.003×** mean emulated work. It passed.
- Failed nominal 002 answerer: **92.596 ms wall / 15.390 ms CPU**, **538,741**
  emulated cycles (about the **538,817** data mean), and **no GC overlap**.
  Roughly **77.206 ms** was not charged to the main thread. The caller also had
  **68.217 ms wall / 17.745 ms CPU**, ordinary emulated work and no GC overlap.
- Nominal 002 caller's maximum data CPU was **20.469 ms**, without GC overlap,
  at **1.027×** mean emulated work. GC cannot explain every CPU-cost tail either.

Callers had 192 observed collections each, including one generation-2 event;
answerers had 14 and no generation-2 events. This is evidence to investigate
caller allocation/retention, not permission to disable GC or claim all remaining
CPU is native DSP execution.

The producer pause was actually **101.370 ms**, with a real packet inside the
hold. Crucially, it began with **44 caller underruns already counted**; its
large whole-call gap is not an estimate of the 100 ms fault's effect. A training
quantum took **761.624 ms wall / 9.680 ms CPU**, without GC overlap. This is not
a clean fault-only comparison, and differing independent calls need not negotiate
identical modem modes/workloads.

Evidence: `artifacts/qualification/v34-gc-work-{profile-control,nominal-001,
nominal-002,nominal-003,producer-100ms}/`, combined `v34-gc-work-analysis/summary.json`,
per-profile `.summary.json` files and
`artifacts/test-results/v34-gc-work-status.json`. All failures remain retained.

Source inspection identified an unbounded duplicate history in
`AnalogKernelDispatch._tx`: every SPORT callback appends a tuple to `tx_all`
and SPORT1 also appends to `tx`. These long-lived per-sample objects are separate
from source captures and the output latch. They are the next controlled
allocation/retention target; bit-exact replay must precede any live comparison.
