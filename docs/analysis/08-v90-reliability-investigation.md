# V.90A/V.90D connection and recovery investigation — 2026-09-05

## Findings

The paired reactive bridges have two reproduced failures: premature training
event acceptance, and persistent loss of downstream six-symbol alignment after
a sample slip. LAPM re-establishment does not repair the latter. There is also
a separate upstream data-path failure when the experimental sideband is disabled.

These are fresh experiments, not conclusions inferred solely from old logs.
No production DSP or protocol behaviour was changed during this investigation.

## Reproduction environment

- Harness checkout: `d864764c5d3f4ed2922b7b8dfacacc639d4c1ce1`.
- Clean sibling checkout: `2aaea90f01ec1168af2367c01fd127c6e3c4d006`.
- Both bridge executables freshly compiled from C sources against the sibling's
  installed static SpanDSP library. No old `/tmp` bridge executable was reused.
- `tools/v90_pair_investigate.py` connects the real analogue and digital child
  processes through 160-byte PCMU exchanges and the actual Python LAPM endpoints.
  It uses explicit media time, a 1 ms yield per exchange for the asynchronous CP
  worker, and no SIP, network, Eicon observer, or realtime scheduling requirement.
- The default working control uses the experimental upstream PCMU sideband and
  real V.90 downstream mapping. This is not a native V.34 upstream interop test.
- A temporary digital diagnostic build added segment logging only. Its source
  is retained as `artifacts/v90-reliability-investigation/bridge-d-diag.c`.

## Initial connection: false Ja starts the digital transmitter too early

With the event-arm gate at zero, the segmenter classifies a segment beginning
at sample 596 as J. The digital bridge accepts it by sample 1920, while the
analogue transmitter is still in its pre-Ja training sequence. The digital
transmitter starts Sd/TRN1d early. It later times out waiting for S; the analogue
side reaches `Phase 4 (unarmed)`, and the two do not converge.

Evidence: `segments.log`, particularly the segment classifications around
samples 1280 and 1920, and their ordering relative to `tx=TRN` and `tx=Ja`.

| Configuration | Result |
| --- | --- |
| Default event gate 0 | 0/3 connections; runs of 90, 30 and 30 media seconds |
| Event gate 12000 | 3/3 connections at 10.24–10.26 media seconds |
| Historical short TRN1d, 2496 symbols, gate 0 | Still no connection in 60 seconds |

The gated repeat runs each receive 323 bytes at the analogue end and 285 bytes
at the digital end, exactly matching the repeated test payloads, with zero HDLC
FCS failures. The 60-second gated control has 100 good frames at each end.

This isolates the event timing gate as the effective difference in this setup;
changing the newer TRN1d duration alone does not repair it. A fixed 12000-sample
gate is a demonstrated diagnostic control, not a universal timing constant for
every SIP or firmware topology. A durable fix should validate Ja from the actual
training sequence before accepting a generic segmenter's J classification.

## Recovery: a 20 ms discontinuity permanently shifts the mapper grid

Start from the gated working connection. At media time 40 seconds, insert 160
PCMU silence samples into the downstream only, then resume the exact original
sample stream. Upstream continues unchanged. The input remains byte-exact apart
from the explicitly injected samples.

Observed sequence in `insert160.log`:

1. Both LAPM endpoints connect at 10.26 seconds.
2. The 160-sample insertion occurs at 40.00 seconds.
3. At 52.00 seconds, LAPM exhausts data retries and starts re-establishment.
4. The digital end receives the caller's SABME and sends UA repeatedly, recording
   generations 2 through 5. The analogue end never records a second connection.
5. At 64.00 seconds, the analogue endpoint fails with `T401 SABME retry limit`.
6. At 90 seconds, the analogue decoder still reports `stage=data`, but has
   66,667 out-of-constellation failures. The digital transmitter still reports
   training complete and continues producing data frames.

Control: inserting **162** silence samples at the same point produces only 28
transient demap failures. Both links remain on generation 1 through 90 seconds,
with 160 good HDLC frames each and no FCS failures (`insert162.log`).

Both cases were repeated after rebuilding the original, uninstrumented bridge
sources. `final-slip160.json` again ends with caller T401 failure and answerer
generation 5. `final-slip162.json` remains connected at both ends, with 1343
downstream and 1185 upstream application bytes, all matching the test payloads.
The repeats connect at 10.38 seconds; asynchronous CP-worker scheduling accounts
for small differences in the connection time between these frame-level runs.

160 modulo 6 is 4; 162 modulo 6 is zero. A V.90 mapping frame spans six samples.
The disturbance is therefore a permanent frame-boundary shift, not merely a
short burst of damaged bytes that HDLC retries can correct.

An independent Phase-4 generator/receiver probe confirms the deletion case:

| Deleted samples | Subsequent demap failures | Receiver state |
| --- | --- | --- |
| 0 | 0 | DATA |
| 1 | 994 | DATA |
| 6 | 0 | DATA |
| 160 | 973; zero output bits | DATA |
| 162 | 0 | DATA |

See `slip-probe.c` and `slip-results.txt` in the artifact directory. The probe
derives its valid training fixture from the sibling's `test_phase4_receive`.
Its output bit queue is bounded; the clean controls' reported bit counts are
capped and must not be read as total delivered throughput.

Source explanation: `v90_analogue_phase4.c` accumulates six received samples,
attempts demapping, counts failures and returns. Persistent failures do not
automatically move DATA back to acquisition or realign the grid. The streaming
analogue wrapper latches `data_connected`; the digital wrapper stops training
classification after `connected_reported`. Although the sibling exposes rate
renegotiation APIs, these wrappers do not coordinate that recovery in response
to the observed loss. LAPM's SABME/UA exchange cannot make the broken downstream
decoder read UA.

This reproduces the asymmetry in the historical five-minute `5mb-window8`
capture. It establishes why recovery fails once the stream slips. It does not
identify which host task caused each historical scheduling stall.

## DATA is insufficient to establish a usable connection

With the working 12000-sample gate but sideband disabled, both engines complete
training and report synchronous streams ready. Neither LAPM endpoint receives a
good HDLC frame in 60 seconds. The caller exhausts SABME retries at 22.18 seconds.
No application bytes arrive (`native-upstream.json` / `.log`).

Thus training completion does not validate the V.34 upstream path. The sideband
currently makes the paired diagnostic setup usable; the native upstream needs
its own bit-level handover/decoder investigation. This test bounds the failure
but does not distinguish all possible upstream transmitter, receiver, or
handover defects.

## Fix boundaries established by this investigation

1. Validate training events against actual peer progress; do not permit the
   early false-J classification to initiate Sd.
2. Detect persistent downstream demap failure, withdraw stale DATA readiness,
   and coordinate actual modem retraining/reacquisition before restarting LAPM.
   More T401 retries or a larger jitter buffer alone cannot restore alignment.
3. Validate bidirectional payload delivery before calling a connection usable.
   Native upstream and experimental sideband results must stay separate.

The exact host-stall source, a production retraining implementation, native
upstream repair, and repeated live SIP/PPP soak tests remain unverified.

## Commands

Run from the repository root on this Mac:

```sh
python3 tools/build_v90_pair_probe.py
python3 tools/v90_pair_investigate.py --arm-samples 0 --seconds 90 --output artifacts/v90-reliability-investigation/default.json
python3 tools/v90_pair_investigate.py --arm-samples 12000 --seconds 90 --output artifacts/v90-reliability-investigation/gated.json
python3 tools/v90_pair_investigate.py --arm-samples 12000 --seconds 90 --slip 160 --output artifacts/v90-reliability-investigation/slip160.json
python3 tools/v90_pair_investigate.py --arm-samples 12000 --seconds 90 --slip 162 --output artifacts/v90-reliability-investigation/slip162.json
python3 tools/v90_pair_investigate.py --arm-samples 12000 --sideband 0 --seconds 60 --output artifacts/v90-reliability-investigation/upstream.json
```

Redirect stdout and stderr to a log to retain training and LAPM diagnostics.
These are frame-level probes, not hardware or live SIP acceptance tests.
The existing four frame-adapter unit tests also pass; both added Python tools
compile, and `git diff --check` is clean.
