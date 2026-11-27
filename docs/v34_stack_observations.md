# External owned-caller stack observations (macOS)

This adds **opt-in observation**, not a signal/pacing fix or CPU-time attribution.
The endpoint, DSP code, audio, buffers, deadlines and RX recovery policy are
unchanged. Only the qualifier launches an external observer against its own
caller process. No physical device, registrar, deployment or production service
was used.

## Run

```sh
make qualify-v34 V34_ARGS='--calls 1 --producer-detail --caller-stack-sample-seconds 2 --sample-after-bytes 1024 --max-tx-underruns 0 --max-tx-gap-ms 40 --output artifacts/qualification/NEW'
```

`--caller-stack-sample-seconds` defaults off; accepted requests are 1–5 seconds
at a fixed **10 ms** external sampling interval. It implies producer profile and
media trace; `--producer-detail` additionally provides existing GC/emulated-work
observations. The trigger uses unique verified upstream bytes, must be a positive
multiple of 64, and must leave at least 128 bytes. Deliberate process/producer
pauses are mutually exclusive with sampling in this interface.

An explicit request on systems without macOS `/usr/bin/sample` returns argument
error 2, not a hidden skip or silently unsampled success. Parser/ownership tests
are portable; active external sampling is not a Linux feature.

`CallerStackSample` receives the qualifier's owned caller `Popen`, not a user
PID. It checks target liveness before launch and after observer completion,
records PID/tool command/binary SHA-256, and requires the parsed report PID to
match. Both report completion and verified payload progress after observed
completion are required for `application_passed`. Zero/missing samples,
unsupported or malformed reports, early target exits, incomplete/failed tools,
or no post-observation progress fail that gate.

No endpoint timers are added. The external sampler naturally interrupts/inspects
threads and consumes resources: **it may perturb scheduling and pacing**. The
10 ms interval is also commensurate with the 20 ms media period; phase aliasing
is possible. This is not a zero-overhead or unbiased CPU profiler.

## Cleanup and bounds

Evidence under each `call-NNN/`:

- `caller-stack-sample.txt`: raw macOS report.
- `caller-stack-sampler.log`: tool output.
- `caller-stack-sample.json`: ownership, lifecycle and verified-progress receipt.
- `caller-stack-analysis.json`: count-conserving parsed paths/categories.

Existing evidence paths are refused. Completion is polled during the existing
payload wait; it does not extend that deadline. If the call finishes/fails
before the tool completes, cleanup terminates **the sampler only**, escalates to
kill after five seconds if needed, and marks observation incomplete. The owned
caller is stopped by the qualifier's normal `finally` cleanup. There is no wait
for an extra successful sampling window or retry-until-green. Normal exception,
interrupt and sampler-spawn failure cleanup is tested; controller SIGKILL/crash
cleanup and PID-reuse races are not magically eliminated by PID checks.

The parser supports macOS report version 7, requires one matching process header,
a complete delimited call graph, and exactly one explicitly named main queue.
It rejects unsupported/malformed/ambiguous structures, negative/inconsistent
counts, reports over 8 MiB, over 20,000 nodes or over 256 frame levels. Unknown
symbols remain `other_leaf`; they are not discarded. The binary-image table is
retained raw; the parser validates the complete call graph, not every image entry.

## Count semantics: neither CPU percentages nor precise wait durations

Call-graph nodes contain **inclusive** sample counts. The parser subtracts
immediate-child counts to recover exclusive PC/path weights, including samples
at internal nodes. Exclusive counts must conserve each thread's root count.
Main and wire thread counts are reported **separately**, never summed into a
single wall-time or CPU percentage.

Category priority is explicit:

- Named GIL-acquisition path; named Python lock-acquisition path.
- Selector, generic wait/sleep, or I/O-syscall **leaf PC**.
- ADSP-library, Python-runtime, ctypes/FFI, allocator or other leaf PC.

A Python callback nested under an ADSP ancestor is not automatically native DSP
CPU. A native-library PC can belong to a **descheduled** thread. A write PC does
not prove blocking disk I/O. Python-runtime symbols include C runtime helpers,
not just bytecode. Named GIL/lock paths do not supply wait duration or establish
which thread caused contention. Unknown/inlined symbols can hide other paths.
None of these labels waives capture, source-clock, gap or underrun budgets.

The reports aggregate stacks without per-snapshot timestamps. Receipts record
**sampler process-lifetime envelopes**, including startup, symbol processing and
polling delay—not exact collection intervals. Controller hashing/parsing work
can occur outside those envelopes. An incident inside that envelope is not proof
that a corresponding stack snapshot captured it.

## Calibration and fixed call set

A single owned throwaway Python process alternated busy Python work and a
GIL-releasing libc sleep. A one-second request yielded 92 main-thread snapshots:
48 sleep/wait leaves, 26 Python-runtime leaves and 18 other leaves (including
clock helpers). It validated report shape/counting, not modem performance or
an unbiased expected percentage.

The live plan was saved before execution: **control 001, observed 001, control
002, observed 002**. Each used one call, 4096 independent bytes/direction,
`--producer-detail`, zero TX-underrun budget, ±1000 ppm and 40 ms maximum source
gap. Observed calls requested two seconds after 1024 verified bytes. No deliberate
fault, intentional concurrent test workload or individual retry was used.

| Case | Caller/answerer underruns | Caller/answerer max gap ms | Caller/answerer source Hz | Verdict |
| --- | --- | --- | --- | --- |
| Control 001 | 0 / 0 | 26.473 / 31.838 | 8000.082 / 8000.092 | PASS |
| Observed 001 | 1 / 1 | 47.553 / 53.240 | 7994.742 / 7994.980 | FAIL |
| Control 002 | 1 / 1 | 111.098 / 124.429 | 7979.598 / 7978.397 | FAIL |
| Observed 002 | 6 / 4 | 135.805 / 105.126 | 7966.110 / 7965.271 | FAIL |

All four verified **32,768 total independent payload bytes**, clean lifecycle,
zero final RX loss/FCS errors/resets, and complete continuous captures without
sequence/timestamp discontinuities. Both observer applications passed their
ownership/report/progress gates. Both target processes remained alive at observer
completion, and exact transfers progressed to 4096 bytes. All eight profile/
detail receipts closed without primary-record or GC-event drops. Ordinary media
rings dropped 367–963 old events; all incident snapshots were retained, but
ordinary timelines are not complete. All four matched **411 final runtime input
fingerprints**.

### Main-thread stack counts

| Category | Observed 001 (183 samples) | Observed 002 (182 samples) |
| --- | --- | --- |
| Selector leaf | 90 | 57 |
| ADSP native leaf | 60 | 81 |
| Python-runtime leaf | 30 | 41 |
| Named GIL path | 0 | 1 |
| I/O syscall leaf | 2 | 0 |
| Allocator leaf | 0 | 1 |
| Other leaf | 1 | 1 |

The single main GIL path was
`select_kqueue_control_impl → _PyThreadState_Attach → take_gil → pthread condition wait`.
It locates a sampled selector-return reacquisition path, **not a DSP-call-return
wait**, and is not proof that GIL contention explains a particular media gap.
The wire threads had 170/179 Python-lock-path snapshots and 4/1 named GIL-path
snapshots respectively; these are also stack observations, not timing attribution.

Observed requests began at 1024 bytes; completion was observed at **1920/1792**
bytes, with final 4096. Sampler lifetimes were **4.266/4.034 seconds** for requested
two-second collection. No retained incidents fell in the first process envelope;
**5 caller/3 answerer** incidents fell in the second. Those are envelope overlaps,
not exact stack/incident matches. The first window cannot explain its later gap;
the second report cannot identify the stack at any individual incident time.

### Tails still unresolved

An unsampled control's data quanta measured **110.824/18.869 ms** caller and
**124.756/12.558 ms** answerer wall/thread CPU. The second observed caller had a
**135.772/17.066 ms** quantum with 541041 emulated cycles and no observed GC overlap;
four of its six incidents matched that same quantum, not four separate stalls.
The answerer had **127.178/14.538 ms**, also without observed GC overlap.

The control failure prevents calling every observed failure sampler-caused. The
observer's potential interference prevents treating sampled runs as overhead-free
natural behavior. The averaged stack counts do not uniquely distinguish native
execution cost, Python work, GIL/locks, I/O or descheduling during these tails.
Current mean data CPU remained about 10.4–11.0 ms caller/8.8–9.3 ms answerer;
mean headroom alone still does not qualify pacing. Default policy stays unchanged.

Evidence: `artifacts/stack-sampling-calibration/`,
`artifacts/qualification/v34-stack-sample-{control-001,observed-001,control-002,observed-002}/`,
`v34-stack-sample-analysis/summary.json`, per-profile `.summary.json` incident
correlations and `artifacts/test-results/v34-stack-sample-status.json`.
