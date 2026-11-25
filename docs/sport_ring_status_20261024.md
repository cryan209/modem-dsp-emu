# Continuous SPORT1 ring status, 24 October 2026

The existing ring implementation supersedes the missing-program portion of
the 23 October scoping document. The host services DSP DM directly through
BAR3 IDMA; a MIPS steady-state media-copy stub is not required for this path.
The modem's frame-rate control-plane question remains unresolved.

Saved evidence in `tests/data/sport_ring_card12196.json`:

| Run | Duration | Received samples | Overruns / underruns | Anomalies |
|---|---:|---:|---:|---:|
| ring20 | 60 s | 480287 | 0 / 0 | 41 |
| ring5 | 60 s | 480408 | 0 / 0 | 25 |
| ring_final | 60 s | 480288 | 0 / 0 | 36 |

All runs retain a one-sample loop delay. In the final run the 36 anomalies
are zero substitutions, and all remain incorrect when reread. This supports
continuity of the stream but does not establish lossless sample transport or
identify the producer of the zero values. The historical fixture's `clean`
field used the weaker flow-only criterion and is preserved as recorded.

The runner now publishes `flow_continuous` and `anomaly_free` independently.
`clean` and successful exit require both. Invalid duration, period, margin
and port arguments are rejected before hardware takeover.

Next hardware investigation: retain the existing spare-only takeover and
restoration procedure; compare anomaly positions with TX/RX counters and
IDMA access timing, using a positive control for the transmitted pattern.
Separate TX starvation inside the interrupt service path, a received zero,
and IDMA contention before changing DSP or emulator semantics. Repeat the
60-second run with zero corrupt words before moving to streamed V8.ANA
detector input. The initial validation changes used saved evidence; the physical follow-up
below subsequently tested these hypotheses.

Local validation: all 20 ring tests and the C core tests pass. The optional
Unicorn MIPS integration dependency crashes on a minimal memory-map probe
inside the restricted sandbox; the identical probe succeeds outside it. The
test now contains that probe in a child process and reports a skip on native
signal termination rather than killing test discovery. Local socket tests
also require permission to bind localhost sockets.

Earlier unrestricted validation after the V.110 idle-flag correction: all 916
Python tests pass, including the Unicorn emulated-card integration test;
the C core tests and `git diff --check` also pass.

## Physical isolation controls

Later on 24 October, the owner explicitly authorized copying diagnostics to
their `eicon420.net.cryan.nz` host and running bounded tests on spare adapter
5 / SN12196. Each takeover used a timeout and an EXIT trap to stop, reset and
reload stock firmware. Adapter 5 and the primary SIP gateway were verified
active afterward. No primary-card takeover was performed.

| Control | Duration | Unexpected zeros |
|---|---:|---:|
| Original streamed ramp, DSP 0 | 15 s | 10 |
| 5 ms host polling | 15 s | 8 |
| Adjacent DSP 1 | 15 s | 10 |
| Cyclic TX, no live TX DM writes | 15 s | 9 |
| Autonomous TX autobuffering | 15 s | 10 |
| Constant IPAC DU word `5aa5` | 15 s | 7 |
| Duplicate RX stores into separate rings | 15 s | 7, both copies zero |
| DSP zero log, no live host IDMA | 15 s | 8 unexpected events |
| RX-only DMA capture, trial 1 | 5 s | 0 in retained 8,192 words |
| RX-only DMA capture, trial 2 | 5 s | 1 in retained 8,192 words |
| RX-only DMA capture, trial 3 | 5 s | 1 in retained 8,192 words |

All streamed controls retained continuity with no buffer overruns/underruns.
The quiet-IDMA control records 126 zero events; most are legitimate cyclic
ramp zeros at sample index `1 mod 1024`. Extending the 14-bit positions and
excluding startup leaves eight unexpected zero events. The host performed
no IDMA accesses during the 15-second wait.

The final control uses an 8,192-word RX autobuffer, IMASK=0, no TX writes and
a foreground loop that only publishes the RX pointer. IPAC CDA drives a
constant `5aa5` onto DU with DD injection disabled. After five seconds the
host disables SPORT1 and reads the retained buffer. Two trials contain an
actual `0000`; every other word is `5aa5`. These are one-second snapshots of
five-second runs, not captures of every sample during those runs.

This isolates the defect to the physical receive path or its configuration;
it does not identify a particular chip, clock edge or board component. The
source fixture, IOM/FPGA clock/frame routing, SPORT reception and DMA hardware
remain candidates. It rules out live host servicing and per-sample CPU RX/TX
handlers as necessary causes. No emulator semantics were changed.

The autonomous-TX counter updates only on wrap; its reported sample count
has 1,024-sample granularity. Do not interpret its endpoint-derived rate as
precise clock frequency. Existing streamed rates also include samples
accumulated during the post-boot delay before measurement begins.

Evidence is in `tests/data/sport_ring_isolation_card12196.json`, including raw
DMA captures and DSP zero positions. Original run files and generated inputs
are under `artifacts/sport-ring-20261024/`. Regression checks pin all five
programs and their boot payload hashes, shadow zeros, quiet-IDMA events and
the DMA captures.

New diagnostic options: builder `--tx-autobuffer`, `--rx-shadow`, `--log-zeros`
and `--rx-capture`; runner `--cyclic-tx`, `--rx-constant` and `--no-live-idma`.
RX capture requires a constant source. Autonomous TX requires cyclic mode.
The no-live-IDMA log is a bounded diagnostic and never claims a clean media
stream. These controls retain the existing spare-only identity gate.

Final validation: all 932 Python tests pass with native JIT and localhost
access, including all 36 ring tests and the Unicorn integration test. C core
tests and `git diff --check` pass.
