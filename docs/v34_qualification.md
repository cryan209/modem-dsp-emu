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

# Optional strict final per-end TX-underrun budget (may legitimately fail):
make qualify-v34 V34_ARGS='--max-tx-underruns 0'
```

Defaults are **3 calls**, **4,096 bytes per direction per call**, and **120 wall
seconds per call**, including training. Modulation is fixed to V.34 19200.
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

## Pass/fail contract

**Exit 0** requires all requested calls and the answering process to pass.
**Exit 1** means qualification failure, including missing firmware, native
preflight failure, timeouts or crashed children. Invalid arguments return 2;
Ctrl-C returns 130 after cleanup. Read `result.json` and endpoint logs, not
just the parent stdout.

Each call must provide:

- Both ends reach `TrnProgress 0x00d0`, establish LAPM exactly once,
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
  `--max-tx-underruns` budget is exceeded. No default timing guarantee is implied.
- Every caller exits 0 without forced kill; the answerer exits 0 without forced
  kill after the final call. Early endpoint exits and tracebacks fail the run.

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
- `preflight.log`: out-of-process native Unicorn mapping/execution check.
- `answerer.endpoint.log` plus combined answering-side audio/RTP/DSP captures.
- `call-NNN/`: caller captures/log, answering-side log slice, probe configuration,
  client/server payload receipts and per-call result. Failure retains partial
  receipts as well as the overall error.

Each cycle's client reports elapsed **active transfer** wall time, excluding
training, and retry count. This stop-and-wait elapsed time is not negotiated
line rate or a TCP throughput measurement. Captures and reports remain ignored
local artifacts; do not commit large recordings or machine-specific paths.

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
