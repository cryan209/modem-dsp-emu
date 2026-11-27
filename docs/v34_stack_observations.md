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

The original implementation (`99bccc0`) live plan was saved before execution:
**control 001, observed 001, control
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
fingerprints at that implementation**.

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

## Tighter collection-end bounds (receipt version 2)

The controller now polls the sampler's existing log, with an 8 KiB read bound;
there is still no endpoint instrumentation or extra audio-sample timer. It
records when complete newline-terminated start/completion messages are first
**received**, not when they were emitted. Byte regression, oversized logs,
duplicate/malformed/out-of-order markers, wrong PID/duration/interval or missing
completion fail the observation gate, even if the call graph is valid.

The conservative possible-collection envelope is:

```
[monotonic time before launching sample,
 first monotonic read-completion time observing "Sampling completed, processing symbols..."]
```

The start-marker **reception** is not a valid lower bound on collection start:
the tool may already be collecting before the controller reads its message.
Likewise, marker delivery can lag collection end because of buffering, polling
or scheduling. The receipt retains all-poll count, largest observed poll gap and
first marker reception times; the largest poll gap is **not** a bound on message
delivery latency. A nominal 50 ms controller polling period does not promise
±50 ms alignment. No requested-duration arithmetic supplies missing timestamps.

`caller-stack-alignment.json` records, separately for both roles, every retained
incident relative to the **caller** collection window (not answerer stack data) as `before_launch`, `possible_collection_overlap` or
`after_collection_marker`, with dropped-incident accounting. Only the latter is
positive exclusion from collection on the upper side. Inside the envelope is
**possible overlap**, not a stack/incident match. Classification does not infer
CPU time, wait duration, causation or a pacing exemption. Invalid/missing bounds
or inconsistent/unclosed traces yield explicitly unavailable alignment; there
is no fallback pretending the broader process lifetime is collection.

Version-1 receipts cannot be retrospectively tightened: old logs have no marker
reception timestamps. Those original four observations remain as reported.
The parser still retains aggregate stack paths, not timestamped individual
snapshots.

A new owned-process calibration saved its exact program and explicitly made
**no PPP/payload qualification claim**. Its one-second request produced 91 main
snapshots; the completion-marker envelope was **2.770 s**, versus **2.878 s**
process lifetime. The first start-marker receipt was 51.346 ms after launch;
maximum observed polling gap was 32.790 ms. This only removed about **108 ms**
from the old envelope. It demonstrates why neither a one-second request nor a
marker should be sold as an exact collection interval. Tool work/message delays
can substantially precede the observed completion marker.

Calibration evidence: `artifacts/stack-sampling-marker-calibration/`.

### Fixed marker-aware calls

A separate plan saved before execution requested one control followed by two
observed **one-call** runs. Each used 4096 bytes/direction, producer detail,
zero underruns, ±1000 ppm and 40 ms gaps, unchanged default RX recovery, and
no deliberate fault or intentional concurrent test load. No individual retry.

| Case | Underruns C/A | Max gap ms C/A | Source Hz C/A | Verdict |
| --- | --- | --- | --- | --- |
| Control | 0 / 0 | 24.586 / 26.773 | 8000.030 / 7999.737 | PASS |
| Observed 001 | 0 / 0 | 25.474 / 29.653 | 7999.308 / 7999.764 | PASS |
| Observed 002 | 0 / 0 | 26.285 / 29.943 | 7999.720 / 8000.074 | PASS |

Both marker-aware applications passed, with sampler completion observed at
1472/1408 verified bytes and subsequent progress to 4096. Possible collection
bounds were **2.230/2.113 s**, versus **2.285/2.165 s** process envelopes: only
**55.246/52.201 ms** were excluded at the end. Maximum observed polling gaps
were **60.081/60.167 ms**, not a guaranteed 50 ms delivery latency.

There were **no incidents** in these calls. The real receipts validate marker
capture, but do not demonstrate exclusion of a naturally occurring bad quantum.
Boundary/coalesced-message/late-delivery cases and retained-vs-dropped incident
accounting are tested synthetically, not presented as live fault evidence.
All 24,576 independent bytes, six source streams and lifecycles passed with
zero RX loss/FCS errors/resets. All six CPU/detail receipts closed with no
primary/GC-event drops; ordinary traces dropped 361–947 events, no incidents
were dropped. All three runs matched **411 runtime input fingerprints**.

Each main-thread graph conserved 170 observations. Observed 001 had **two**
named GIL paths despite passing every timing gate: one selector-return
condition-wait path, and one native SPORT1-frame → ctypes callback →
`PyGILState_Ensure → take_gil → pthread_mutex_unlock` path. The latter is an
acquisition-path observation, **not evidence of a blocking wait**. It identifies
a callback boundary worth replay-based cost investigation, not a root cause or
justification to change GIL policy. Observed 002 had no named GIL paths.

These three short passes do not erase the original fixed failures or establish
repeatable pacing, long-soak/reconnect/fault coverage, or a causal speedup from
controller log polling. No DSP/audio/timing policy changed; tail cause remains
unresolved, and aggregate stacks still cannot identify individual-incident PCs.

Evidence: `artifacts/qualification/v34-stack-markers-{control,observed-001,observed-002}/`,
`v34-stack-markers-analysis/summary.json` and
`artifacts/test-results/v34-stack-markers-status.json`.
