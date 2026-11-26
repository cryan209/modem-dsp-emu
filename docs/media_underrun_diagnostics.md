# Media underrun diagnostics and wakeup repairs

Bounded traces first exposed starvation and scheduling defects. The follow-up
repairs wake an overdue sender on producer publication and stop the producer
selector busy-polling on wire-owned deadlines. **Buffer sizes, RX recovery
thresholds, sample generation and DSP arithmetic are unchanged.** This does
not imply immunity to arbitrary host stalls or hard-real-time qualification.

## Capture and summarize

After the [V.34 qualification setup](v34_qualification.md):

```sh
make qualify-v34 V34_ARGS='--media-trace --output artifacts/qualification/v34-clock-probe'

.venv/bin/python tools/media_clock_trace.py \
  artifacts/qualification/v34-clock-probe/answerer-media-traces/*.json \
  artifacts/qualification/v34-clock-probe/call-*/caller-media-traces/*.json

# Independent userspace RTP capture timing, not physical-wire timestamps:
.venv/bin/python tools/rtp_pcap_timing.py \
  artifacts/qualification/v34-clock-probe/call-001/caller.rtp.pcap
```

The output directory must be new. `--media-trace` is opt-in; the normal
qualification and portable gate do not enable live tracing. For a standalone
SIP endpoint use `--media-trace-dir DIR`. A persistent answerer writes a new
`call-NNN.json` at each call's teardown.

The qualifier requires a valid, closed trace on both ends when tracing is
requested. Its total recorded underrun count and final snapshot must match
the endpoint's final media counter. A missing or incomplete trace fails that
run even if payload verification passed.

## What is recorded

No per-event file writes or console prints occur. Each call has a locked,
bounded ring of **8,192 events** plus **128 underrun snapshots**, dumped only
after stopping its transmit clock. Total/discarded counts disclose truncation;
older incidents can outlive their surrounding event history. This is bounded
history, not a complete packet archive.

Events cover received RTP (including drops), RX wait begin/end, producer
begin/end, TX enqueue/send, underrun and teardown. Snapshots include:

- Actual host monotonic event time, RX sample count and TX packet count.
- Scheduled next send/tick and observed send-deadline lateness.
- Last RTP arrival, RX hold deadline/retry and active wait start.
- Producer active state and most recent producer begin/end times.
- Underrun/hold-attempt counters and the legacy nominal hold-time counter.
- Training state and outgoing RTP SSRC/sequence/timestamp for capture matching.

Initial settings retain the TX target, RX prefill, producer quantum, RTP period
and hold limit. Queue lengths are observational snapshots, not an atomic
transaction across the sender and producer. Tracing adds overhead and can
perturb scheduling; repeat timing qualification with tracing off.

`actual_rx_wait_seconds` measures wall time between RX-wait transitions,
including an open wait at teardown. The old `hold_time` counter is left
unchanged: it adds a nominal 2 ms **per retry**, which is not a measurement of
elapsed waiting. A busy poll can inflate it enormously.

The summary reports maximum retained **send schedule debt** as well as
incident states. Debt is lateness relative to the absolute send schedule, not
an individual inter-packet gap: it can accumulate over many mildly late sends.
The summary labels incident **states**, not proven causes:

- `rx_waiting`: producer gated on RX refill.
- `producer_active`: incident during the whole producer quantum, which includes
  DSP, capture and protocol work; this alone does not prove CPU overload.
- `neither`: neither of those states was active.

It also counts waiting snapshots with a complete producer quantum available
but less than RX prefill. Late `tx_send` events with queued data identify a
different symptom from an empty-queue underrun; inspect their deadlines and
RTP capture gaps/bursts as well. A zero underrun counter alone is not a strict
packet-cadence guarantee: the existing counter trips only after an empty queue
is a full RTP period past its send deadline.

## Deterministic reproduction

```sh
.venv/bin/python -m pytest tests/test_media_clock_trace.py -q
```

The two-peer simulation uses the **production** `rx_ready`, `wants_quantum`,
`service_transmit` and `next_wakeup` state machines. Its DSP is a zero-cost
synthetic producer, with deterministic 5 ms packet delivery, one TX packet of
cushion and a two-quantum RX recovery prefill. It uses no native firmware,
real sleeps, packet loss, repeated symbols or fabricated RX samples.

- Control: both peers progress without underruns.
- Pause only producer A for 120 ms: A underruns, then B also underruns while
  waiting for RX recovery. B's producer is not doing expensive DSP work.
- RX-hold regression: the buffered producer must honor its future RX retry,
  without moving either media or wire schedule. An overdue wire-thread
  deadline no longer forces immediate selector polls during that hold.

The sender regression starts with an empty queue 5 ms past its deadline. The
producer publishes fresh audio at +10 ms. Before the repair, a fixed backoff
sent only at +25 ms **without increasing the underrun counter**. The test now
requires sending at **+10 ms**, with no fabricated audio or counter changes.

Additional regressions cover publication just before notification clear and
just before wait, no early send on a future deadline, notification only for a
complete RTP packet, and shutdown interrupting a one-hour deadline wait.

Other cases check bounded retention, no file creation before dump, closed
receipt validation, elapsed wait accounting and distinct incident states.

## Pre-repair findings

An initial three-call live trace passed exact payload/reconnect/teardown
qualification but recorded **72 underruns**: **48 during RX waits**, **24 while
the producer was active**, none in the third category. Several waiting
snapshots already had 160 received samples, but the recovery threshold was
320. This supports the deterministic coupled-starvation mechanism; it does
not prove that all live incidents have one root cause.

A final three-call repeat passed with **19 underruns**: 14 waiting for RX
(all 14 had a full quantum available below prefill) and 5 producer-active.
Caller/answerer totals per call were **0/0, 6/3, 3/7**. For answerer call 3,
measured RX wait was 0.200 s while the nominal retry sum claimed 24.088 s.
All requested trace receipts closed with counts matching the final counters;
all 4,096-byte-per-direction payloads, reconnects and teardown passed.

Independent caller-PCAP analysis of call 3 measured about **7,550/7,560 RTP
timestamp units per wall second**, not 8,000, despite no sequence gaps or
timestamp jumps. Retained caller send events had up to **1.574 s of accumulated
schedule debt**; that does not mean a single 1.574-second packet gap. These
observations explain why zero counted underruns alone is insufficient.

## Repairs and measured follow-up

The sender now clears a per-call notification **before** checking its queue
and absolute deadline. A producer appends complete packets and signals it.
Publication before clear remains visible in the queue; publication afterward
wakes the wait. Enqueue cannot force an early send. Empty/late senders wait
for publication, stop, or the next accounting boundary instead of blindly
sleeping a full packet period after work. Stop signals both cancellation and
wake notification. These notifications are hints, never packet counts.

The buffered producer selector honors a pending RX retry independently of
expired wire-thread deadlines. Readable RTP still interrupts the selector
immediately; neither `next_tick` nor `next_send` is forgiven or moved by this
change. Genuine underruns retain their existing accounting/recovery policy.

With the native regression gate running concurrently, the repaired three-call
traced qualification passed exact payloads, reconnects, trace receipts and
teardown. Caller/answerer underruns were **1/1, 1/2, 0/0**: five total, not a
claim of zero underruns under load. Captured rates across all six directions
were **7,993.61–8,003.41 timestamp units/s**, versus the earlier ~7,550–7,560
case. Host load was not identical across runs; these are observations, not a
controlled performance benchmark or hard latency guarantee.

The third repaired call's maximum retained send-schedule debt was **3.188 ms
caller / 4.142 ms answerer**, rather than seconds of accumulated debt. The
first call still captured a ~109 ms peak, showing that host stalls remain
possible. Nominal retry sums no longer ballooned into tens of seconds during
short holds; they remain estimates, not measured waiting.

A separate **single strict attempt**, after the native gate finished and with
tracing disabled, ran `--max-tx-underruns 0`. All **3/3 calls passed with zero
TX underruns on both ends**, zero RX loss/FCS errors/resets, exact independent
payloads and clean persistent-answerer reconnect/teardown. No budget was
relaxed and no failed attempt was retried until green.

Source-side TX captures measured **7,999.604–8,000.153 Hz** across the six
streams, with no RTP sequence gaps or timestamp discontinuities. Use each
endpoint's own TX capture for this measurement: receive-side userspace capture
times also include the receiver's processing/queueing delays. Packet jitter
remains: source-side maximum spacing was **29.901 ms**, so this is not a
perfect 20 ms physical-wire or hard-real-time claim. These short spans are not
long-soak or oscillator-accuracy qualification.

Strict evidence is `artifacts/qualification/v34-wakeup-fix-strict/`, including
`source-tx-summary.json`, `capture-summary.json` and per-call `rtp-timing.txt`.
Loaded evidence is `artifacts/qualification/v34-wakeup-fix/`, including per-call
`rtp-timing.txt` and `trace-summary.json`. RX rebuffer hysteresis can still
propagate a sufficiently long producer stall, as the two-peer test preserves.
Any further prefill/recovery change needs training/data-mode controls and must
stay within V.34 latency limits. Do not inject repeated symbols, relax the
qualification budget or increase buffers blindly to make counters green.

The qualifier now independently gates complete source TX captures, continuity
and average clock offset, with an optional maximum-gap budget. Historical
slow-clock transfers fail that gate even when their protocol checks passed.
A larger strict 3 × 16 KiB-per-direction follow-up passed all six source
streams with zero underruns; see the [qualification guide](v34_qualification.md)
for budgets, measured jitter and proof limits. This adds no hot-path tracing
or changes to buffering, RX recovery or DSP scheduling.

The [controlled data-phase process-stall probes](v34_stall_recovery.md) now
exercise an owned caller's SIGSTOP/SIGCONT separately from the earlier
producer-only synthetic test. Both 20/100 ms requested pauses recovered exact
data with no logged mode exits or data-link resets, yet retained negative
strict timing verdicts. In particular, the 20 ms case counted zero underruns
but exceeded the unchanged 40 ms gap budget. Do not infer producer-only or
physical-peer recovery from this whole-process experiment.

The [producer/RX-dispatch isolation follow-up](v34_producer_stall.md) now leaves
that wire thread running. During a 108.607 ms observed main-thread hold, DSP
samples and RX queue were frozen while one already-produced packet was sent
(source-PCAP verified), followed by three underrun events. The no-fault control
also failed timing, so the pair cannot establish an injection-only performance
delta. Retained fault markers/incident snapshots are available despite older
trace-ring events being dropped; they are not complete call timelines.

Local initial evidence is `artifacts/qualification/v34-underrun-trace/`, with
`trace-summary.json`; the final traced repeat is
`artifacts/qualification/v34-underrun-trace-final/`. Neither is a tracked
fixture, physical-card run or hard-real-time qualification.
