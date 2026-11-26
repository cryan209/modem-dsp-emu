# Controlled V.34 data-phase process stalls

These are **localhost emulated endpoints**, not a physical modem or board.
The fault is a POSIX `SIGSTOP` of the whole newly created Analog caller:
producer, wire sender and receive dispatch all stop. It is **not** a
producer-only stall, packet-loss injector, training fault or CPU-overload model.

The purpose is to separate functional payload recovery from real-time pacing.
A successful transfer after a stall must not erase its timing violations.

## Reproduce

After the firmware extraction/setup in [the qualification guide](v34_qualification.md):

```sh
# Matched traced control, no fault:
make qualify-v34 V34_ARGS='--calls 1 --media-trace --max-tx-underruns 0 --max-tx-gap-ms 40 --output artifacts/qualification/my-pause-control'

# Data-phase whole-caller pause (may recover data but still return 1):
make qualify-v34 V34_ARGS='--calls 1 --media-trace --max-tx-underruns 0 --max-tx-gap-ms 40 --caller-pause-ms 100 --output artifacts/qualification/my-pause-100ms'
```

`--caller-pause-ms` defaults to **0 (disabled)**. Nonzero requests must be
finite and **1..1000 ms**. `--pause-after-bytes` defaults to **1,024 verified
upstream bytes**, must be a positive multiple of 64, and must leave at least
128 bytes to transfer. Small payloads remain supported when faults are disabled.

The host receiver advances its O(1) progress counter only for unique, exact,
nonce-matching upstream blocks. Duplicates and corrupt blocks do not advance it.
This proves a working PPP data path before the fault; training time is not used
as a guessed trigger. The controller injects once per call. A missed trigger or
failure to observe/apply the fault fails the run, rather than silently
claiming fault coverage.

The process target is **only the caller Popen created by this qualifier**.
There is no arbitrary-PID option and no signals to pre-existing services,
registrars, hardware or production endpoints.

## Timing and safety

The controller observes the child's actual stopped state with nonblocking
`waitpid(WUNTRACED)`, with a 0.5 s acknowledgement deadline. It then requests the
specified hold. Receipt durations use monotonic nanoseconds; wall-clock
nanoseconds are also recorded for comparison with PCAPs. The measured hold runs
from observed stop to continuation request, not to the child's first resumed
instruction. It can exceed the requested duration due to host scheduling. The requested bounds
are not hard-real-time upper bounds on actual scheduling delay.

`SIGCONT` is requested in `finally` before diagnostic file writes or ordinary
endpoint termination. Interrupts during STOP publication and the hold have
regression coverage. During suspension a temporary SIGTERM handler raises an
interrupt so that continuation runs before outer cleanup; the prior handler is
restored. A child that exits instead of stopping retains its actual exit status.
There are no filesystem writes by the injector while the caller is stopped.

The fault consumes the **original call deadline**. No time is added for it.
Ctrl-C returns 130 after cleanup; SIGTERM during the guarded suspension follows
that same interrupt path. SIGKILL, controller crashes and repeated interruption
of cleanup cannot be made cleanup-safe by Python; do not treat those as
qualified recovery cases.

## Receipts and verdicts

`call-NNN/caller-pause.json`, also embedded as `caller_pause` in the result,
records the request, stopped-state observation, continuation request, measured
hold and verified-byte counts at stop/continuation/completion. Its `passed`
means only **fault applied and verified upstream progress after continuation**.
It is not an overall qualification pass or permission to relax any budget.

Exact independent payload hashes, one LAPM/CHAP/IPCP session, zero final FCS
errors/resets/RX loss, teardown and clean child exits remain required. The
qualifier also requires one **logged** data-mode entry and no logged departure;
a modem retraining behind an unchanged LAPM session cannot count as continuous
data mode. Mailbox annotations are not mistaken for additional transitions.
This is firmware transition-log evidence, not instruction-by-instruction proof
that no unobserved intermediate DSP state existed.

All existing source-capture checks and the requested underrun/gap budgets stay
in force. Read overall `result.json` and the actual CLI exit code. A fault
receipt and payloads can pass while the qualification still correctly fails.
No xfails, sample repetition, buffer growth, deadline extension or relaxed
budgets are used to turn a fault case green.

## Recorded local probes

Each scenario was run **once**, with tracing enabled, 4,096 independent bytes
per direction, the default ±1,000 ppm source-clock budget, zero-underrun budget
and **40 ms** maximum source-spacing budget. The control preceded the 100 ms
probe. The 20 ms probe followed; post-run transition-log review also confirmed
the stronger mode checks for the earlier control/100 ms logs.

| Scenario | Observed stopped time | TX underruns caller / answerer | Maximum source gap ms caller / answerer | Overall verdict |
| --- | --- | --- | --- | --- |
| No pause | — | 0 / 0 | 25.768 / 26.921 | **PASS** |
| 100 ms requested | 101.167 ms | 1 / 4 | 120.257 / 103.210 | **FAIL** |
| 20 ms requested | 24.394 ms | 0 / 0 | 41.883 / 29.806 | **FAIL** |

All three delivered exact bytes in both directions, closed cleanly, and had
zero final RX loss/FCS errors/data-link resets. There was one logged data-mode
entry and no departure on each end. In both fault cases verified upstream bytes
were **1,024 at stop, 1,024 at continuation and 4,096 at completion**. Thus data
progress demonstrably continued after resumption, not just from already-buffered
complete-transfer receipts. Endpoint processes were verified exited.

- **100 ms:** source rates **7,988.697 / 7,981.480 Hz** (caller / answerer),
  outside the clock budget; underrun and gap budgets also failed. The unpaused
  answerer's four underruns show coupled starvation, but this does not isolate
  producer-only causation.
- **20 ms:** source rates **7,999.548 / 7,998.945 Hz**, within the clock budget,
  and **zero counted underruns**. Nevertheless the caller's **41.883 ms** gap
  failed the unchanged 40 ms budget. This is a real, retained example of a
  counter-only pass missing a source-pacing violation. It was not retried or
  reclassified as a full pass.

Evidence is under `artifacts/qualification/v34-data-pause-{control,100ms,20ms}/`:
`result.json`, `source-timing.json`, per-call fault/payload receipts, logs,
closed traces and source captures. The 20 ms run's **406 input fingerprints**
matched current runtime files; earlier logs were independently rechecked with
the strengthened transition checks. Analysis is separate from fresh calls.

## Remaining boundaries

These probes establish recovery of the selected local stop-and-wait payloads,
not full-bandwidth/TCP performance, long soaks, physical-peer recovery or
universal tolerance of these requested stall durations. Both endpoints can
backpressure their emulated DSP advancement on received audio. A continuously
clocked physical peer need not recover the same way.

The source clock/gap checks correctly remain negative where the data path
recovers. No buffering or media/DSP scheduling policy was changed. Further
work should isolate producer/RX-dispatch stalls from wire-thread suspension,
exercise training and data modes separately, and control fault phase/duration
before proposing RX recovery/prefill policy changes.
