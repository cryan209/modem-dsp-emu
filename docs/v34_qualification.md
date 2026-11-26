# Local V.34/LAPM/PPP qualification

`tools/qualify_v34.py` is an **opt-in, localhost-only** qualification command.
Unlike the general loopback tracer, it returns success only after checking
training, negotiation, exact independent payloads in both directions, repeated
calls against **one persistent answering process**, and teardown.

This is real firmware emulation over local SIP/RTP, V.34, LAPM and PPP. It does
not dial a physical modem, contact a registrar, use TUN/root, or change services.

## Setup and firmware inputs

Follow [testing.md](testing.md) to create `.venv`, install requirements and build.
Extract the matching tracked combifiles; these commands also work from a fresh
checkout without any private captures:

```sh
.venv/bin/python tools/eicon_dsp_extract.py docs/firmware/build-109/dspdload.bin \
  --card-type 77 --match '^DIVA Server Analog Kernel' \
  -o artifacts/eicon-dsp/build-109-789-analog/kernel
.venv/bin/python tools/eicon_dsp_extract.py docs/firmware/build-109/dspdload.bin \
  --card-type 77 --match '^TIKRNL81\.ANA Task' \
  -o artifacts/eicon-dsp/build-109-789-analog/tikrnl
.venv/bin/python tools/eicon_dsp_extract.py docs/firmware/build-109/dspdload.bin \
  --card-type 77 --match Overlay \
  -o artifacts/eicon-dsp/build-109-789-analog/overlays

.venv/bin/python tools/eicon_dsp_extract.py docs/firmware/dspdload.bin \
  --card-type 56 --match '^DIVA Server PRI 30M Kernel' \
  -o artifacts/eicon-dsp/build-117-926/kernel
.venv/bin/python tools/eicon_dsp_extract.py docs/firmware/dspdload.bin \
  --card-type 56 --match '^TIKRNL81\.F34 Task' \
  -o artifacts/eicon-dsp/build-117-926/tikrnl
.venv/bin/python tools/eicon_dsp_extract.py docs/firmware/dspdload.bin \
  --card-type 56 --match Overlay -o artifacts/eicon-dsp/overlays
```

Do not substitute Analog and PRI overlay sets. The Analog caller uses its
native SPORT1 kernel dispatcher; the PRI answerer uses the direct backend
and hardware-correct SPORT PCM expansion. This is the topology of the
[earlier ping smoke](v34_smoke_20261125.md).

## Run

```sh
make qualify-v34
# Equivalent, after make build:
.venv/bin/python tools/qualify_v34.py

# A longer exact-payload/reconnect experiment, not a previously qualified soak:
make qualify-v34 V34_ARGS='--calls 5 --bytes 65536 --call-timeout 1200'

# Opt-in bounded event traces, flushed only at teardown:
make qualify-v34 V34_ARGS='--media-trace'

# Strict final counters plus an explicit source-side gap budget:
make qualify-v34 V34_ARGS='--max-tx-underruns 0 --max-tx-gap-ms 40'

# Larger payload case (180s deadline accommodates the extra stop-and-wait data):
make qualify-v34 V34_ARGS='--calls 3 --bytes 16384 --call-timeout 180 --max-tx-underruns 0 --max-tx-gap-ms 40'
```

Defaults are **3 calls**, **4,096 bytes per direction per call**, and **120 wall
seconds per call**, including training. Modulation is fixed to V.34 19200.
Complete source TX captures must also meet **±1,000 ppm** of 8,000 Hz by
default (`--max-tx-clock-ppm`). This is an explicit short-run regression budget,
not oscillator-accuracy or physical-wire qualification. `--max-tx-gap-ms` adds
an optional maximum inter-packet spacing budget; no maximum gap is imposed
unless requested.
A new Analog calling process is launched for each call; the same PRI answering
process must survive all calls. Its PPP address pool and userspace NAT are
therefore exercised across real SIP BYE/teardown/new-INVITE boundaries. The
existing endpoint architecture builds a fresh per-call DSP card; this does
**not** qualify reuse of one live DSP task instance across calls.

`--python` selects the endpoint interpreter (default: the invoking interpreter,
including its virtualenv). `--output` selects a **new** evidence directory;
existing directories are rejected, preventing stale reports from passing a
later run. Otherwise a unique directory under `artifacts/qualification/` is
created. Diagnostic `EICON_*`/`ADSP_*` environment overrides are removed and their
key names recorded; only the standard explicit V.34/SPORT settings are applied.
No credentials are read from `profiles.local.toml`.

## Controlled data-phase stalls

`--caller-pause-ms 100` opts into one whole-caller-process pause per call after
`--pause-after-bytes 1024` exact upstream bytes (the default trigger). It is
**disabled by default** and targets only this qualifier's owned local child.
The original call deadline and every strict timing check remain in force.
A recovered payload is not permission to pass a missed clock/gap budget.
See [controlled stall recovery](v34_stall_recovery.md) for commands, measured
holds, interrupt safety, receipts and retained negative results—including a
zero-underrun transfer that still failed the 40 ms source-gap budget.

`--producer-pause-ms 100` instead pauses only the main producer/RX-dispatch
thread after real TX publication, leaving the wire thread runnable. It is
also disabled by default and mutually exclusive with the whole-process fault.
A private inherited pipe arms the one-shot fault; independent source capture
must prove a real TX packet inside the measured pause window. See
[producer/RX-dispatch stall isolation](v34_producer_stall.md) for its placement,
receipts and negative control/fault results. Neither option changes normal
buffering, fabricates samples, extends the deadline or waives timing failures.

`--producer-profile` opts into bounded main-thread CPU/wall measurements and
implies `--media-trace`. Both endpoint profiles must close and account for every
completed quantum; missing/incomplete receipts fail qualification. It does not
waive any existing budget or add a CPU-duration pass criterion. See
[CPU versus wall profiling](v34_producer_cpu.md) for phase definitions,
between-quanta waiting, retention, observer effects and the fixed observation set.

`--producer-detail` additionally observes bounded GC events and reads the
existing emulated ADSP-cycle counter twice per quantum; it implies profiling
and tracing, and requires complete detail receipts. It is not pure native CPU
measurement and changes no GC policy. Live Analog kernel dispatch now omits
duplicate in-memory SPORT history while preserving the real TX latch and all
required captures. `--analog-sport-history` explicitly restores that unbounded
caller diagnostic history for controls. See [GC/work observations and the
bit-exact retention change](v34_gc_work.md), including retained timing failures.

## Pass/fail contract

**Exit 0** requires all requested calls and the answering process to pass.
**Exit 1** means qualification failure, including missing firmware, native
preflight failure, timeouts or crashed children. Invalid arguments return 2;
Ctrl-C returns 130 after cleanup. Read `result.json` and endpoint logs, not
just the parent stdout.

Each call must provide:

- Both ends log exactly one entry to `TrnProgress 0x00d0` and no departure,
  establish LAPM exactly once,
  authenticate CHAP as the local test user, and bring IPCP up exactly once.
- The host receiver verifies **every upstream byte**, with exact length and
  SHA-256. The caller independently verifies **every downstream byte**,
  with exact length and SHA-256. Missing blocks cannot be counted as complete.
- A fresh 128-bit per-call nonce matches. Payload direction and block sequence
  select deterministic independent byte streams: the downstream is **not** an
  echo of the upstream. Corruption, wrong nonce, wrong sequence or wrong length
  latches failure even if a later retransmission is correct.
- Caller shutdown sends an outgoing-dialog SIP BYE. The persistent answerer
  receives it and completes call/PPP/NAT teardown before the next caller starts.
  The answerer must report closing the probe's one NAT flow. Both ends report
  IPCP/LCP down and final zero PPP FCS errors/data-link resets.
- Final media counters exist and show zero RX substituted/dropped samples.
  TX underruns are always recorded; they fail only if the optional
  `--max-tx-underruns` budget is exceeded. Clock and optional gap checks below
  are independent: zero counted underruns alone cannot pass a drifting stream.
- Every caller exits 0 without forced kill; the answerer exits 0 without forced
  kill after the final call. Early endpoint exits and tracebacks fail the run.
- Closed PCAPs must provide a complete source TX stream on each end. Packet
  counts must match final `tx_sends`, preventing aligned prefix/tail loss from
  disguising a partial capture. RTP sequence advances by one, timestamp advances
  by the preceding 160-sample payload, and capture times increase in record order.
  The average timestamp clock must meet its budget; an optional maximum gap
  budget also rejects isolated stalls/bursts hidden by a correct average.

Source timing uses **each emitter's own PCAP**, not peer receive timestamps.
The caller's incoming SSRC identifies the matching answerer TX stream in the
persistent capture. Every answerer TX stream must be accounted for exactly
once. Truncated/malformed records, unknown/ambiguous streams, absent final send
counts, or missing captures fail closed. The strict reader intentionally accepts
only the complete RAW IPv4/UDP/RTP format generated by this harness. Sequence
and timestamp wrap are supported. Records are never sorted to hide reordering
or wall-clock regressions. Host wall-clock adjustment can invalidate a timing
measurement; it is not silently interpreted as successful clock qualification.

Analysis runs **after endpoints exit and captures close**. Console protocol
passes are provisional. In result schema **2**, `protocol_passed` records that
stage; `passed` stays false and `source_timing_pending` true until capture
analysis finishes. Both per-call and overall final verdicts include timing.

The application protocol uses 64-byte stop-and-wait UDP blocks with bounded
retries driven by the overall call deadline. IPv4 + UDP + probe metadata +
payload + PPP protocol field fits within LAPM's N401=128 boundary. Lost replies
may be retried; verified duplicates are counted but never inflate the byte
count. The protocol travels through the calling PPP peer, modem link and
answerer usernet/NAT to a host UDP receiver bound only to `127.0.0.1`.

## Evidence

The output directory contains:

- `result.json`: overall verdict, explicit per-call checks, counters, commands,
  persistent answerer PID and caller/answerer exit/forced-kill status.
- `inputs.json`: Python/package versions, Git revision when available, and
  SHA-256 fingerprints of selected firmware extractions, native library and
  relevant harness source. Source archives without Git metadata are supported.
- `source-timing.json`: source stream identities, completeness/continuity/clock
  checks, measured rates/offsets, min/max spacing and optional gap-budget checks.
- `preflight.log`: out-of-process native Unicorn mapping/execution check.
- `answerer.endpoint.log` plus combined answering-side audio/RTP/DSP captures.
- `call-NNN/`: caller captures/log, answering-side log slice, probe configuration,
  client/server payload receipts and per-call result. Failure retains partial
  receipts as well as the overall error. With a caller fault enabled,
  `caller-pause.json` records application/progress evidence, not a timing waiver.

Each cycle's client reports elapsed **active transfer** wall time, excluding
training, and retry count. This stop-and-wait elapsed time is not negotiated
line rate or a TCP throughput measurement. Captures and reports remain ignored
local artifacts; do not commit large recordings or machine-specific paths.

For trace fields, deterministic coupled-starvation reproduction, live findings
and sender/selector wakeup repairs, see [media underrun diagnostics](media_underrun_diagnostics.md).
Tracing adds overhead; it is not a real-time guarantee or a buffering fix.

## Recorded local results (25 November 2026)

The default three-call/4,096-byte-per-direction case passed twice: once in the
developer tree, then from a **fresh source archive with these changes applied**,
a new virtualenv, a freshly built C library and all six extractions above made
from tracked combifiles. The archive had no private captures, local profile or
Git metadata. Its manifest fingerprinted 403 firmware/library/source files.

Fresh-tree result, with the native regression gate running concurrently:

| Call | Active payload wall time | Application retries | Final TX underruns (caller / answerer) |
| --- | --- | --- | --- |
| 1 | 22.930 s | 0 | **17 / 7** |
| 2 | 22.545 s | 0 | **16 / 6** |
| 3 | 23.346 s | 0 | **17 / 16** |

All three calls verified exactly 4,096 bytes in each direction, with zero final
FCS errors, data-link resets, RX substitutions or RX drops. The same answerer
PID survived the two reconnects; each caller and the final answerer exited 0
without forced kill. The developer-tree repeat passed with 20.41–20.58 s active
payload time and also zero application retries. **Nonzero TX underruns mean
neither run qualifies an underrun-free wire clock.**

A negative control, `--calls 1 --bytes 512 --call-timeout 10`, deliberately
expired before training/payload completion. It returned **1**, recorded an
explicit timeout plus incomplete byte receipts and failed endpoint checks,
and left no endpoint children alive; both children still cleaned up with exit
0. Thus clean process exits alone cannot produce a qualification pass.

Local evidence: `artifacts/qualification/v34-default/`,
`artifacts/qualification/v34-timeout-final/`, and the fresh-tree run under the
temporary checkout recorded in `/tmp/modem-v34-clean-path`. These paths are
local evidence pointers, not tracked fixtures or prerequisites for rerunning.

## Source timing gate and larger strict run

The source-capture checks are mandatory in result schema 2. This makes a
successful protocol transfer insufficient when its actual timestamp clock
runs too slowly. A zero underrun counter cannot bypass these checks.

Two offline controls were checked without modifying their original evidence:

- Earlier repaired strict captures passed the new complete-source gate.
- Pre-repair captures that passed payload/reconnect qualification failed source
  timing on calls 2 and 3, at roughly **7,550–7,594 Hz**. Call 1 still passed.
  This is an offline requalification, not three fresh modem calls.

A fresh larger run used:

```sh
.venv/bin/python tools/qualify_v34.py \
  --calls 3 --bytes 16384 --call-timeout 180 \
  --max-tx-underruns 0 --max-tx-gap-ms 40 \
  --output artifacts/qualification/v34-16k-source-gate
```

All three calls passed, including the default ±1,000 ppm source-clock budget,
complete captures, exact independent data, zero underruns on both ends,
zero FCS errors/resets/RX loss and clean reconnects/teardown. That is
**49,152 verified bytes per direction**, 98,304 total—not a TCP throughput test.

| Call | Active payload time | Source TX Hz (caller / answerer) | Maximum source spacing ms (caller / answerer) |
| --- | --- | --- | --- |
| 1 | 81.959 s | 8000.059 / 8000.018 | 25.948 / 29.814 |
| 2 | 81.959 s | 8000.051 / 7999.923 | 26.197 / 27.919 |
| 3 | 81.960 s | 7999.969 / 7999.915 | 24.931 / 23.721 |

All six source streams had zero sequence/timestamp discontinuities. Observed
rates were within 11 ppm over these finite ~98-second streams; this is not
long-term clock-accuracy or hard-real-time proof. The explicit 40 ms gap budget
passed; it does not assert perfect 20 ms spacing or forbid every catch-up burst.

Evidence is in `artifacts/qualification/v34-16k-source-gate/`, including
`result.json`, `source-timing.json`, per-call receipts and raw captures.
Control replays are `v34-source-gate-replay/` and `v34-clock-negative-replay/`
under the same ignored qualification directory. Current timing checks reproduce
the saved larger-run verdicts exactly and account for all answerer TX streams.

## Qualification limits

This qualifies **bounded bidirectional application-data exchanges and repeated
call cleanup**, not simultaneous full-bandwidth duplex, arbitrary large IP
frames, TCP bulk throughput, 30-minute reliability, BER, physical-peer timing,
or the standalone physical SPORT receive ring. The direct PRI backend is not
a fresh QEMU guest run. The native core's arithmetic and board mappings are not
changed by the qualifier.

Portable unit tests cover success, corruption in either direction, stale/wrong
sessions, truncation, missing data, retries/duplicates, fail-closed log verdicts,
UDP-server cleanup, outgoing BYE, and forced-child cleanup. The firmware calls
are **not** part of `make test` or CI; run this command explicitly and preserve
its report for the exact byte count, call count and timing policy you need.
