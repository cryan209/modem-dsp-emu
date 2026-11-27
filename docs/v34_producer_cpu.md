# V.34 producer CPU versus wall time

This is opt-in instrumentation of the **main producer/RX-dispatch thread** in
localhost firmware emulation. It is not a CPU-overload verdict, a physical-peer
run, or a change to buffering, samples, RX recovery or clock policy.

A later [scalar FIR optimization](v34_resampler_cpu.md) removes repeated
per-tap bookkeeping without changing binary64 outputs or native firmware state.
Alternating FIR-only batches measured about 3× lower filter CPU, not a 3× modem
speedup. One of three post-change live calls passed strict timing; two failures
and CPU/non-CPU tails remain. Existing instrumentation/budgets are unchanged.

## Run and inspect

```sh
# Implies --media-trace; all normal qualification budgets still apply:
make qualify-v34 V34_ARGS='--calls 1 --producer-profile --max-tx-underruns 0 --max-tx-gap-ms 40 --output artifacts/qualification/my-cpu-profile'

.venv/bin/python tools/producer_profile.py \
  artifacts/qualification/my-cpu-profile/call-001/caller-media-traces/producer-profiles/call-001.json \
  --trace artifacts/qualification/my-cpu-profile/call-001/caller-media-traces/call-001.json
```

The endpoint's `--producer-profile` requires `--media-trace-dir`. The qualifier
supplies it automatically and requires closed, complete profile receipts from
both endpoints. Profiles live in a nested `producer-profiles/` directory so
existing trace-only `*.json` commands do not accidentally parse them as traces.

## What is measured

`time.monotonic_ns()` measures elapsed wall time; `time.thread_time_ns()` measures
CPU consumed by this main OS thread, including native library code running on
it, but excluding the wire thread and any library work on other threads.
Thread ownership is checked; accidentally measuring a different thread fails.

Each completed quantum is partitioned into:

| Phase | Coverage |
| --- | --- |
| `sample_pump` | Per-sample DSP/native execution, RX code lookup, Python dispatch and sample-level diagnostics; **not pure DSP-only time** |
| `status_reactive` | State/overlay handling and optional reactive-engine work after the sample loop |
| `diagnostics` | Existing DSP state capture (`write_diag`), when enabled |
| `tx_encode_enqueue` | G.711 encoding and publication of real output, not wire-thread UDP/capture work |
| `link` | Terminal/V.42/PPP application servicing |
| `bookkeeping` | Remaining measured quantum bookkeeping/trace work |

Phase wall/CPU sums must equal measured quantum totals. Quanta with observed
begin/end training progress both `0x00d0` are grouped as data; others as training
or transition. This is observed state, not instruction-by-instruction tracing.

**Wall minus CPU is not synonymous with descheduling.** It includes GIL/lock
waits, blocking operations and other elapsed time not charged to this thread.
CPU time alone does not separate native DSP, Python work, allocation or GC.
Clock reads are sequential rather than atomic; raw negative differences are
retained and counted, not clamped to fabricate perfect measurements. The fixed
set below had small negative quantum/between differences down to -209 ns.

Between-quanta wall/CPU intervals are measured separately. They include selector
waiting, RX gating/dispatch, reporting, injected producer sleeps and profiling
aggregation overhead. A long interval can be intentional waiting for audio,
not a computation overrun. Initial setup before the first measured quantum and
final teardown after the last are outside this span. A 20 ms reference labels
long quanta/intervals for retention; it is **not** a new qualification criterion.

Profiling itself has overhead. Measurements include marker/trace overhead;
end-of-quantum profile aggregation falls into the following between-quanta
interval. The instrumentation does not claim zero observer effect.

## Bounded storage and correlation

Per call, the profiler retains 2,048 completed quantum records, 128 slow-quantum
records and 128 long between-quanta intervals. Online training/data aggregates
cover **all completed quanta**, even when detailed records are dropped. The
profiler adds no hot-path file writes; JSON is dumped at teardown after the wire stops.
The receipt must close without an unfinished quantum, match the independent
final `Call.tick_count`, retain consistent phase sums and account for records.

The analysis command correlates retained underrun snapshots with retained
quantum/between intervals by monotonic time. A matching quantum's CPU value is
for its **whole completed interval**, not CPU consumed at the incident instant.
A missing match is not evidence of idle CPU: details may be truncated or the
incident may be outside the measured span. Trace truncation and profile
retention are disclosed independently. Queue/deadline context is a lightweight,
non-atomic snapshot: the independent wire thread remains runnable while fields
are read, so these fields are observations, not an atomic scheduling proof.

## Fixed observation set

Five one-call commands ran in this predefined order, without deliberate
concurrent test/build load and **without retries**. Each owned fresh local
caller/answerer processes, verified 4,096 independent bytes in each direction,
and retained the same zero-underrun, 40 ms source-gap and ±1,000 ppm clock budgets.
All used media tracing; the first omitted CPU profiling as an observation control.
This is a small fixed-order set, **not** a statistically controlled overhead or
host-load benchmark.

| Case | CPU profile | Underruns caller / answerer (whole call) | Maximum source gap ms caller / answerer | Exit |
| --- | --- | --- | --- | --- |
| Trace-only control | off | 0 / 0 | 30.916 / 23.777 | **0** |
| Nominal 001 | on | 0 / 0 | 27.898 / 24.523 | **0** |
| Nominal 002 | on | 7 / 6 | 58.494 / 66.742 | **1** |
| Nominal 003 | on | 8 / 10 | 83.189 / 139.180 | **1** |
| Producer/RX pause, 100 ms requested | on | 4 / 3 | 112.828 / 92.393 | **1** |

All five completed exact payloads and clean teardown/child exits. All had one
logged data-mode entry, no departure, zero final RX loss/FCS errors/data-link
resets, complete source captures and zero sequence/timestamp discontinuities.
All eight requested profiles closed correctly; no primary profile records were
dropped in these ~35-second calls. Whole-call underrun counts include training;
the CPU means below are **data quanta only**.

### Both CPU-cost and non-CPU tails exist

| Nominal call | Caller mean wall / CPU ms | Answerer mean wall / CPU ms | Data quanta >20 ms wall caller / answerer |
| --- | --- | --- | --- |
| 001 | 11.856 / 11.796 | 8.813 / 8.742 | 1 / 0 |
| 002 | 12.620 / 12.271 | 9.168 / 8.902 | 14 / 4 |
| 003 | 12.698 / 12.244 | 9.480 / 9.113 | 15 / 6 |

Average CPU demand is below the 20 ms quantum period, but that does not establish
worst-case pacing safety. For example:

- Nominal 003 answerer: **67.088 ms wall / 13.966 ms CPU**, leaving **53.122 ms**
  not charged to its main thread. Not explainable as 67 ms of main-thread CPU.
- Nominal 003 caller: **75.418 ms wall / 21.769 ms CPU**, a mixed tail with
  **53.649 ms** not charged to that thread.
- Nominal 003 caller also had **47.108 ms wall / 36.453 ms CPU**; sample-pump
  CPU alone was **36.050 ms**. Nominal 002 had a **30.877 ms CPU** quantum.
  Genuine computation/allocation/GC tails exist alongside non-CPU delay.
- Nominal 001's sole >20 ms data quantum consumed **20.804 ms CPU** but caused
  no counted underrun; runtime above a period is not by itself a failed send.

Roughly **97–98% of measured in-quantum data CPU** was in the sample pump.
Diagnostic capture averaged approximately **0.08–0.12 ms CPU**, with TX encoding
and link work also small on average. This does not rule out occasional I/O
blocking, but does not support removing diagnostic capture as the primary
CPU-throughput fix.

Some answerer incidents occurred in short **8.95–9.90 ms CPU** quanta whose
start snapshots were already approximately **16–17 ms behind the send deadline**.
Those incidents cannot be explained by the current quantum's CPU duration alone;
preceding waiting, supply/catch-up and deadline context matter.

### The injected sleep is not mislabeled DSP cost

The producer probe held the main thread for **105.013 ms**, with an actual
source packet transmitted during it. Its containing between-quanta interval
was **105.672 ms wall / 0.670 ms CPU**. Three caller incidents correlated with
that interval, not a DSP-work quantum. The answerer had an RX-wait interval of
**95.163 ms wall / 1.087 ms CPU**. Data recovered; timing still failed.

## Evidence and next decisions

Evidence directories are `artifacts/qualification/v34-cpu-wall-{trace-control,
nominal-001,nominal-002,nominal-003,producer-100ms}/`. Per-profile `.summary.json`
files retain the analysis output; command exits are recorded in
`artifacts/test-results/v34-cpu-wall-status.json`. The combined analysis is
`artifacts/qualification/v34-cpu-wall-analysis/summary.json`. All five sets of
**408 runtime input fingerprints** matched the runtime files at `4f50b1e`. These are
ignored local artifacts, not tracked fixtures or physical measurements.

[GC and emulated-work observations](v34_gc_work.md) now correlate collections
and the core's existing cycle counter with these intervals, and investigate
duplicate Analog SPORT history without disabling required captures.

Next, further split the sample-pump CPU tails into native execution versus Python/
allocation/GC costs, while separately investigating non-CPU waits. Preserve
RX/deadline context: late-start quanta and recovery gating must not be mistaken
for expensive current computation. Use phase-controlled training/data recovery
cases before changing prefill. Do not infer CPU saturation, disable GC, enlarge
buffers, repeat samples, relax timing budgets or claim a physical recovery fix
from this observation set.
