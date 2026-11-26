# Media underrun diagnostics

These changes instrument and reproduce starvation. They **do not change the
buffering, RX hold, sender scheduling or sample-generation policy**, and do not
claim that underruns are fixed.

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
- Characterization: during a hold, an overdue wire-thread deadline can make
  the main selector return zero despite a future RX retry time. This exposes
  the busy-poll mechanism; it is not a desired scheduling contract.

A separate production-sender characterization starts with an empty queue 5 ms
past its deadline. The producer publishes fresh audio at +10 ms, but the
sender's unconditional 20 ms backoff sends only at +25 ms, **without increasing
the underrun counter**. Only the stop Event, not producer enqueue, wakes this
wait. This is a second scheduling mechanism, independent of RX hysteresis.

Other cases check bounded retention, no file creation before dump, closed
receipt validation, elapsed wait accounting and distinct incident states.

## Findings and next fix boundary

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

The next narrow fixes should (1) let overdue senders wake on new producer
output instead of blindly waiting a full packet period, and (2) separate the
producer selector's RX retry from expired deadlines owned by the independent
wire thread. Preserve absolute deadlines, bounded idle waits, owed media and
immediate wakeup on readable RTP. Then measure whether RX rebuffer hysteresis
still stalls usable audio long enough to exhaust the TX cushion. Any prefill/recovery change needs training/data-mode controls and
must stay within V.34 latency limits. Do not inject repeated symbols, relax
the qualification budget or increase buffers blindly to make counters green.

Local initial evidence is `artifacts/qualification/v34-underrun-trace/`, with
`trace-summary.json`; the final traced repeat is
`artifacts/qualification/v34-underrun-trace-final/`. Neither is a tracked
fixture, physical-card run or hard-real-time qualification.
