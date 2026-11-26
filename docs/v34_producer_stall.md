# V.34 producer/RX-dispatch stall with an independent wire clock

This opt-in localhost fault separates the main producer/RX-dispatch thread
from the RTP sender. Unlike [the whole-process SIGSTOP probes](v34_stall_recovery.md),
it leaves the caller process and its wire thread running. Both endpoints are
emulated; this is not physical-peer recovery or a CPU-overload benchmark.

## Reproduce

Use the setup/extractions in [the qualification guide](v34_qualification.md):

```sh
# No injected fault; a timing failure must still be retained:
make qualify-v34 V34_ARGS='--calls 1 --media-trace --max-tx-underruns 0 --max-tx-gap-ms 40 --output artifacts/qualification/my-producer-control'

# Main producer/RX-dispatch pause, while the wire thread remains runnable:
make qualify-v34 V34_ARGS='--calls 1 --media-trace --producer-pause-ms 100 --max-tx-underruns 0 --max-tx-gap-ms 40 --output artifacts/qualification/my-producer-stall'
```

`--producer-pause-ms` defaults to **0 (disabled)**; nonzero requests must be
finite and **1..1000 ms**. It is mutually exclusive with `--caller-pause-ms`.
The shared `--pause-after-bytes` trigger defaults to 1,024 unique, exact upstream
bytes, requires a multiple of 64 and leaves at least 128 bytes for recovery.

## Fault placement and ownership

The qualifier creates a private pipe and passes only its read descriptor to
its own newly launched caller. After verified data progress it writes one
command byte. There is no signal, arbitrary-PID option, listening control port,
production-service access or hardware operation.

The endpoint consumes the command nonblockingly after a completed quantum.
It arms one pause and waits until **real produced audio is already queued**
with a future send deadline and a live sender thread. A 2 ms minimum deadline
lead is only an eligibility condition for observing a send; no deadline is
moved, packet manufactured, queue enlarged or audio repeated. Arming can wait
for a suitable natural publication. Request time is not pause-begin time.

The sleep runs on the **main producer/RX-dispatch thread**, between quanta and
after real TX publication. It releases the GIL and holds no sender or trace
lock. Socket RX dispatch on this thread also stops; the kernel may still queue
incoming packets. This is not DSP-only work with a separately running RX
handler, nor an injected delay inside DSP arithmetic. Ordinary per-quantum
compute statistics exclude this explicitly recorded between-quantum hold.

The measured pause uses monotonic nanoseconds, with wall-clock boundaries for
independent source-PCAP correlation. Requested durations are bounded, but
actual wall holds can overshoot under host scheduling. The original call
deadline is unchanged. Receipts are written after the sleep, never inside it.
No SIGSTOP/SIGCONT or temporary signal handler is used in this mode. An ordinary
endpoint stop can wait for the at-most-one-second requested sleep to return;
this is not a hard scheduling bound or SIGKILL/crash cleanup guarantee.

Descriptors close on teardown, launch failure and fired/interrupted fault paths.
The ordinary endpoint path has no private pipe and no enabled pause object.

## Required evidence

- `producer-pause.config.json`: the private inherited FD configuration.
- `producer-pause.json`: controller request time/verified-byte counts,
  post-resume observation, endpoint receipt and application checks.
- `producer-pause.endpoint.json`: measured hold, main/wire thread identities,
  sample/RX queue depths, TX queue depth, send/underrun counters and SSRC.
- `result.json` / `source-timing.json`: independent
  `producer_pause_wire_window`, matched to the caller's actual source flow/SSRC.
- Optional closed traces include `producer_pause_begin/end` markers.

The configuration's FD number is valid only inside its owned launch. Reproduce
with the qualifier, not by reusing a saved child command/config after FDs close.

Application evidence requires unchanged DSP sample count and RX queue depth,
a distinct live wire thread, real output queued at the start, send-count
progress during the pause, a completed measured hold, and verified upstream
progress after the endpoint's resume receipt is observed.

Thread liveness/counters alone are insufficient: the strict source-PCAP reader
must find an actual matching outgoing RTP record **inside the measured pause
window**. Missing, malformed, truncated or wrong-source evidence fails closed.
This proves a real send during the hold, not continuous supply for its full
length. A one-packet cushion can send once, then genuinely starve.

`application_passed` and the wire-window check do **not** waive the final
payload, mode-continuity, FCS/reset/RX-loss, teardown, complete-capture, clock,
underrun or gap checks. Overall qualification can correctly remain negative.

## Recorded single-attempt local pair

Both commands used tracing, 4,096 exact independent bytes each direction,
one local call per command, ±1,000 ppm source-clock budget, zero counted
underruns and a 40 ms maximum source gap. Each owned its own caller/answerer;
these one-call probes do not exercise reconnects. Neither attempt was retried.

| Scenario | TX underruns caller / answerer | Maximum source gap ms caller / answerer | Overall exit |
| --- | --- | --- | --- |
| No fault | 4 / 4 | 76.159 / 49.316 | **1** |
| 100 ms producer/RX pause | 4 / 3 | 122.626 / 112.715 | **1** |

**The no-fault control also failed.** Thus this pair is not a clean performance
comparison, and not all failures or differences in counts may be attributed to
the injection. It does not establish nominal zero-underrun safety or quantify
the extra failure rate of this fault.

Both calls nevertheless verified exact payloads, clean teardown and clean child exits. Both had one logged data-mode entry with no
departure, zero RX substituted/dropped samples, zero FCS errors/data-link resets,
complete source packet counts and zero sequence/timestamp discontinuities.

For the faulted call:

- Request after **1,024 verified upstream bytes**; eligible pause began about
  **268 ms later**. This includes command delivery, scheduling and eligibility
  delay, not an immediate precisely phased stop at byte 1,024.
- **108.607 ms** observed hold for the 100 ms request.
- DSP samples stayed **170,560 → 170,560**; dispatched RX queue stayed
  **160 → 160** samples.
- Real TX queue drained **1 → 0**, sends advanced **1,065 → 1,066**, and
  wire underruns advanced **0 → 3** inside the hold.
- The independent caller source capture contains **one matching RTP packet**
  **3.245 ms after pause begin**, before the producer resumed.
- Retained trace markers bracket one `tx_send`, three `underrun` events,
  **zero producer begins and zero RX arrivals**. Those three incidents are
  expected starvation after the existing packet drains, not proof the wire
  thread was suspended. A fourth caller incident was producer-active afterward.
- Parent observed **1,088 verified bytes after the resume receipt**, then
  **4,096 at completion**. Exact downstream verification also completed.
- Source clocks were **7,977.962 / 7,982.764 Hz**, outside budget; clock,
  underrun and gap failures remain failures.

The unpaused answerer's three retained incidents were RX-waiting; one had a
full 160-sample quantum below the 320-sample recovery prefill. The no-fault
control had four producer-active caller incidents and mixed answerer states.
These are observations, **not proof of CPU saturation** or grounds to blindly
change buffers. Incident counts are not counts of lost physical packets.

Evidence: `artifacts/qualification/v34-producer-pause-{control,100ms}/`, with
per-call receipts, full source captures, logs and `trace-summary.json`.
The trace rings dropped older events (950 caller / 1,355 answerer in the faulted
call), so they are not complete call timelines. The fault markers and all
incident snapshots above were retained; the source-PCAP evidence is independent.

## Next boundary

Wire independence is now observed with actual transmission, not just asserted
from thread existence. [Bounded CPU/wall profiling](v34_producer_cpu.md) now
records main-thread CPU versus elapsed time, including separate between-quanta
waits. Its fixed set found both CPU-cost and non-CPU tails; it does not identify
all blocking/scheduling causes. Remaining work includes finer native/Python/GC
attribution, phase-controlled training/data recovery, and evaluating RX
recovery hysteresis with explicit latency budgets. These
probes do not qualify long soaks, DSP-only stalls with active RX dispatch,
full-bandwidth/TCP transfer, or continuously clocked physical peers. No normal
buffering, RX-prefill, sample-generation or clock-scheduling policy was changed.
