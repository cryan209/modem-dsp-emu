# Local setup and regression testing

## Supported setup

Use Python **3.12+** (CI uses 3.13), Make, and a C/C++ toolchain on macOS 14+
or Linux. On macOS install the Xcode Command Line Tools; on Debian/Ubuntu install
`build-essential`, `python3-venv`, and a supported Python interpreter.

From the repository root:

```sh
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
make test
```

Use a recent Python rather than the old system Python shipped by macOS.
Activation is optional: all commands use `.venv/bin/python` explicitly. Set
`PYTHON=/path/to/python` when invoking Make if using a different environment.
The dependency manifest pins the emulator/test dependencies; deployment-only
packages are described separately in `deploy/*/README.md`.

`make build` builds the C runner and ctypes library. The library is called
`tools/adsp2181emu/libadsp2181.dylib` on both platforms; Linux builds an ELF
shared object with `-shared`, macOS uses `-dynamiclib`. Generated libraries,
objects, `.venv/`, and pytest caches are ignored. Never trust a replay against
an old library after changing the C sources.

`./run` prefers the project environment for the launcher itself, so it does
not accidentally import `tomllib` using macOS's older system interpreter.
`profiles.toml` defaults to `.venv/bin/python` for the launched harness too.
A gitignored
`profiles.local.toml` can override this and other profile variables, so check
that file if `./run` still launches an interpreter in a sibling project.
Do not commit registrar credentials or machine-specific interpreter paths.

## Test scopes

| Command | Scope | Additional requirements |
| --- | --- | --- |
| `make test` | C self-test and curated portable Python gate | Tracked files and the environment above |
| `make test-all` | All tests under `tests/`, with skip reasons, including long training tests | Native Unicorn MIPS JIT access; optional extracted firmware/captures enable extra tests |
| `make test-local` | Broader local baseline excluding two long modem-training tests | Native JIT access; same optional inputs as the full suite |
| `make qualify-v34` | Opt-in V.34 exact-payload/reconnect/teardown qualification, **not CI** | Native JIT access; matching extracted Analog109/PRI117 firmware; localhost SIP/RTP/UDP |
| `.venv/bin/python -m pytest tests/test_v42_lapm.py` | One selected Python module | Build first if that module loads the C library |
| `.venv/bin/python -m pytest --collect-only -q` | Enumerate tests without executing them | Environment and built library |

The `portable` marker is an explicit allowlist in `tests/conftest.py`. It
covers DSP core contracts, IDI/AT, IPAC, framing/protocols, resampling, media
clock behavior, capture parsing, preflight handling, and qualification protocol/
verdict checks (including controlled localhost UDP sockets). It excludes native
MIPS execution, private captures, external network/TUN services and hardware.
Unlisted tests still run in the complete suite: exclusion from the portable
gate is **not** an expected-failure designation.

`make test-local` and `make test-all` run `tools/check_test_environment.py` first. It probes native
MIPS memory mapping and instruction execution in a child process because
Unicorn can import successfully and still die with SIGILL on a restricted
host. A failed probe stops the full-suite command with an error; it does not
silently turn integration failures into passes. `make test` remains usable
without that JIT capability.

The two `slow` tests perform stock-firmware V.21 eight-bearer training and
V.22bis payload transfer. They remain enabled in `make test-all`; explicitly
selecting `-m 'not slow'` is useful for a shorter baseline but is **not** a full
qualification. To run just those tests after the native preflight:

```sh
.venv/bin/python tools/check_test_environment.py
.venv/bin/python -m pytest -m slow -vv --durations=10
```

The complete suite includes local firmware execution and checks against saved
physical-card/QEMU fixtures. It does **not** automatically run a QEMU guest,
dial SIP endpoints, install deployment services, or take over a physical card.
For live QEMU qualification, use [qemu/README.md](../qemu/README.md); for card
work, follow the relevant spare-only safety and restoration procedure in the
hardware documents. Neither belongs in the ordinary CI gate.

## Opt-in end-to-end V.34 qualification

Run `make qualify-v34` only when you intend to start local emulated SIP/RTP
endpoints. Its default is three calls against one persistent answerer, each
requiring 4,096 exact independent bytes in **both** directions, single LAPM/
CHAP/IPCP establishment, zero final FCS errors/resets/RX loss and clean teardown.
It preserves structured evidence and exits nonzero on failed criteria or timeout.
The [qualification guide](v34_qualification.md) includes exact firmware
extraction commands, longer-run options, timing budgets and proof boundaries.
The general `eicon_loopback.py` trace runner still has no equivalent verdict;
its successful exit alone must not be used to qualify a call.

## Optional inputs and interpreting results

`docs/firmware/` and `tests/data/` are tracked inputs. `artifacts/` is not:
it contains local extracted overlays, recordings, and experiment outputs.
Some replay tests skip if their extracted inputs are absent; pytest's `-ra`
summary reports why. Extract matching firmware before comparing those tests
with a research machine's results. Do not manufacture placeholder captures,
copy unrelated overlays, or mark an assertion failure as skipped.

A passing portable gate means only that gate passed. A skipped replay is not
hardware validation. A fixture comparison is not a new physical-card run.
SIP startup/data-mode evidence is not sustained PPP transfer or BER evidence.

For a saved full-suite report:

```sh
mkdir -p artifacts/test-results
make test-all > artifacts/test-results/full.log 2>&1
# Inspect the exit status and the final failure/skip summary in full.log.
```

CI runs `make test` on Ubuntu and macOS with Python 3.13. Live hardware and
native firmware integration are intentionally not claimed by that workflow.

## Regression baseline

### Initial setup baseline (`8d1c1f2`)

See the results below for the local setup validation and complete-suite
limitations. The portable gate was also tested in a temporary tree populated
from tracked files plus the setup changes, with a **new virtualenv, no
profiles.local.toml, no artifacts, and newly compiled C outputs**. This is
stronger than running against the developer checkout's existing library.

Validation on macOS arm64, Python 3.13.6, Unicorn 2.1.4, NumPy 2.5.5 and
pytest 8.4.2, starting from `98af1c9` plus these setup/test changes:

| Run | Result |
| --- | --- |
| Clean-tree `make test` | C self-test PASS; **405 passed**, 710 deselected, about 5 seconds for pytest |
| Developer-tree `pytest -m 'not slow'` | **1,111 passed, 2 failed**, 2 deselected, about 5 minutes |
| Clean-tree suite excluding the entire BRI machine module | **1,044 passed, 1 failed, 55 skipped**, about 68 seconds; 15 BRI machine tests not collected |
| Media-clock, launcher and preflight tests with `-W error::ResourceWarning` | **38 passed** |
| `pip check` | No broken requirements |

The unrestricted full run was stopped after about nine minutes while still in
the V.21 eight-bearer training test. Neither `slow` test is claimed as validated
here. Both remain enabled in `make test-all`, without xfail or skip markers.
Ubuntu/macOS CI jobs have been configured; their remote results were not
available during this local validation.

### Failures found in the initial baseline (resolved below)

1. `tests/test_analog_mips_modem.py::test_si3056_audio_word_is_clocked_through_native_sport1`
   expects native DSPDAA `DM(0x2E22)` to contain `-1234 & 0xffff` after a
   SPORT1 frame, but reads zero. This reproduces with the local extracted
   Analog images; without them the native replay correctly skips. Diagnose
   ISR delivery/receive consumption before changing core semantics or
   weakening the assertion.
2. `tests/test_eicon_bri_fl0_probe.py::Fl0ProbeTest::test_probe_actually_drives_flags_instead_of_only_echoing_commands`
   stops on an unmapped instruction fetch at physical `0x1fc01000`, just
   beyond the modeled 4 KiB reset alias. It reproduces with tracked inputs
   alone. Check probe entry/boot placement against the measured board model;
   do not simply enlarge the alias to make the test green.

The focused reproduction command, after building and passing the native preflight:

```sh
.venv/bin/python -m pytest tests/test_analog_mips_modem.py tests/test_eicon_bri_fl0_probe.py -q
```

The setup work also fixed a stale Analog test stub, ensured the diagnostic
capture test closes its files, switched FPGA tests from an ignored duplicate
firmware directory to the tracked bitstream, made parser-only milestone tests
independent of a private recording, and added explicit missing-input skips to
optional extracted-firmware tests. Those skips do not suppress either of the
assertion failures above when its required input is present.

Local logs and installed package versions are saved under
`artifacts/test-results/` (intentionally untracked). The table records the
results without requiring those local logs to exist in a fresh clone.

### Follow-up at `78299e8`: correct observables, unchanged emulator semantics

Both initial assertion failures came from test assumptions. No DSP arithmetic,
IRQ routing, firmware bytes, or board memory mappings were changed to resolve
them. Neither test was skipped or marked xfail.

**Analog SPORT1:** execution tracing confirms native PM `0x004f` reads RX1
into SR0; PM `0x0078` reads the previous ring/TX word into AR; PM `0x0079`
(`0x2a7eea`) performs the parallel swap `AR = SR0 + 0, SR0 = AR`; and PM
`0x007a` stores the **TX** word to `DM(0x2e22)`. PM `0x0081` queues RX. The
IDLE task subsequently recycles the same ring slot for TX, so reading that
slot after the foreground finishes is not a receive observation either.

The corrected test latches the **first executed write** to the queue slot,
checks both ISR/foreground writes occurred, and verifies a distinct seeded TX
word in `DM(0x2e22)` and the codec return. Thirty-six frames cover signed and
unsigned representations, zero, extrema, and all 32 slots plus wraparound.
A new portable synthetic-ISR test independently covers the parallel RX/TX swap
and the frame API's write indicator, including retaining TX without claiming
a new write. The native firmware remains unchanged.

**BRI FL0 matrix:** the unchanged physical stimulus is 15,344 bytes, with no
reset-to-SDRAM trampoline. The board model's default PC now correctly starts
at `0xbfc00000`, but its qualified reset alias maps only the first 4 KiB.
The stimulus test now explicitly starts the body at uncached SDRAM
`0xa0000000`, preserving the original image/hash, all 19 command masks,
completion checks, and exact FL0 falling-edge counts `[3,3,3,3,3,3,3,1]`.
Stock reset entry and firmware boot remain covered by separate machine tests.
This does **not** qualify execution of the raw 15 KiB matrix from reset on the
model, or infer the hardware's complete reset-aperture extent from historical
matrix completion. That remains a board-mapping qualification question, not
a reason to enlarge the mapping just to pass a flag-stimulus test.

Follow-up validation uses the same Python/dependency versions as above:

| Run | Result |
| --- | --- |
| Clean-tree portable gate | C self-test PASS; **406 passed**, 710 deselected |
| Developer-tree `make test-local` | C self-test and native preflight PASS; **1,114 passed**, 2 deselected, 379.29 s |
| Clean-tree suite excluding the entire BRI machine module | **1,046 passed, 55 skipped**; 15 machine tests not collected |
| Focused SPORT/native Analog/FL0 regressions | **13 passed** |
| V.21 eight-bearer stock-firmware training/transfer/teardown | **1 passed**, 296.89 s |
| V.22bis stock-firmware binary transfer/teardown | **1 passed**, 270.73 s |

The two slow tests were run separately from the broader native gate. Together
these disjoint runs cover **all 1,116 collected tests**, with no failures or
skips in the developer environment. This is complete inventory coverage, not
a claim that one uninterrupted `make test-all` invocation was run. These are
local firmware-emulation results, not new physical-card measurements.
Logs are in `artifacts/test-results/*followup.log` and
`{v21,v22bis}-training.{log,xml}`.

The original Ubuntu CI job passed all 405 tests plus the C self-test. The
original macOS job did not execute: GitHub reported
`The configuration 'macos-14-arm64-us-default' is not supported`. The workflow
now uses `macos-latest` rather than that retired runner configuration.

### Shared-runner timing correction

With the supported macOS runner, CI executed the gate and exposed a flaky
wall-clock assertion: `test_the_wire_keeps_its_schedule_through_a_stall`
required every observed packet gap to be below 45 ms. The shared VM paused
for 87.64 ms. An interpreter switch interval is not an upper bound on OS/VM
scheduling latency, so that test could not distinguish a scheduling pause
from broken sender logic.

The replacement separates two proof obligations, without relaxing the old
45 ms threshold to another arbitrary performance bound:

- A deterministic clock drives the **actual sender loop**, with producer
  refills only every 80 ms. It requires all 16 sends on their exact 20 ms
  deadlines, an unchanged absolute schedule, and zero underruns.
- A real thread must consume queued payloads in FIFO order while the producer
  waits for its progress event. It verifies a different thread performs all
  sends and no underruns occur. A five-second timeout bounds a broken test;
  it is a liveness check, not a real-time latency claim.

Both cases passed 25 repetitions each. The complete media-clock module also
passes with resource warnings treated as errors. This adds one net test:
**1,117 collected**, of which **407** are in the portable gate. The production
clock implementation is unchanged; only its overstrong scheduling-bound
docstring was corrected. Physical/on-wire latency still requires measurement
on the intended host, not a CI timing assertion.

Validation at `5dc006f` after that split:

| Run | Result |
| --- | --- |
| Clean-tree `make test`, no captures/local overrides | C self-test PASS; **407 passed**, 710 deselected |
| Developer-tree `make test-local` | C self-test and native preflight PASS; **1,115 passed**, 2 deselected, 316.70 s |
| Media-clock module with resource warnings as errors | **34 passed** |
| Repeated deterministic-clock and thread-progress cases | **50 passed** (25 repetitions each) |

The two previously completed slow-test runs above cover the two deselections;
their code and emulator implementation did not change. Together these runs
cover that **1,117-test inventory**. A fresh local V.34/LAPM/PPP smoke
also answered 20/20 pings; its [separate report](v34_smoke_20261125.md) records
media underruns and explicitly limits what that result qualifies.

### Executable V.34 qualification follow-up

- Final clean-tree portable gate: C self-test PASS, **449 passed**, 710 deselected.
- Qualification unit module: **42 passed**, including resource warnings as errors.
- Broader native run: **1,156 passed**, 2 slow deselections, 300.00 s. One final
  loopback CLI-validation regression was added afterward and passed in the
  portable gate; the then-current inventory was **1,159 tests**. The two long BRI
  tests were previously qualified separately and their execution paths did not
  change. These are combined runs, not a new uninterrupted `make test-all`.
- Default V.34 qualifier: **3/3 calls passed**, 4,096 exact independent bytes
  in each direction per call, one persistent answering process, clean BYE/PPP
  teardown. Reproduced from a new source tree/venv/build using the documented
  six extractions from tracked combifiles, without private captures/profiles.
- Deliberate 10-second deadline: **exit 1**, explicit timeout and incomplete
  receipts, both endpoint processes reaped cleanly with no forced kill.

The new [qualification guide](v34_qualification.md) records payload timing,
visible nonzero TX underruns, NAT cleanup, evidence layout and proof limits.
Neither a green portable gate nor these bounded transfers qualifies a physical
line, full-bandwidth TCP, or a long soak.

### Bounded media diagnostics follow-up

`--media-trace` adds opt-in bounded event/incident rings and closed, counter-
matching receipts. It does not change pacing, buffers or sample generation.
[Media underrun diagnostics](media_underrun_diagnostics.md) documents capture,
summarization, deterministic two-peer/overdue-sender reproductions, and live
findings. Zero counted underruns is not sufficient proof of correct RTP cadence.

- Portable gate: **455 passed**, 710 deselected; C self-test PASS.
- Diagnostic/clock/qualifier/BRI-codec modules: **87 passed**, with resource
  warnings treated as errors. The six initial diagnostic cases are portable.
- Final traced qualification: **3/3 calls passed** with exact independent
  payloads and closed traces. It still recorded 19 underruns; the findings are
  explicit, not a claim of corrected timing.

The broader gate initially caught a codec regression's minimal `SimpleNamespace`
call stub missing the new optional `media_trace=None` field. The stub was
updated without changing any assertion about the 256 G.711 octets; the whole
codec module then passed. No genuine firmware assertion was skipped or weakened.

### Sender/selector wakeup repair follow-up

The sender now wakes on complete-packet publication, rechecks absolute deadlines
without lost notifications, and wakes promptly on cancellation. Buffered RX holds
honor their retry rather than busy-polling on wire-owned deadlines. Buffer sizes,
RX recovery thresholds, underrun accounting and sample generation are unchanged.

- Portable gate: **460 passed**, 710 deselected; C self-test PASS.
- Clock/diagnostic/qualification/BRI-codec modules: **92 passed**, resource
  warnings treated as errors. The diagnostic module now has 11 cases, including
  publication races, no early sends, partial packets and long-wait shutdown.
- Broader native run: **1,163 passed**, 2 slow deselections, 302.03 s; five
  additional wakeup cases added afterward passed in the final portable gate.
- Loaded traced qualifier: **3/3 calls passed**, with five total TX underruns.
  Independent per-direction RTP capture rates were **7,993.61–8,003.41 Hz**.
- Single strict follow-up after the native gate, tracing off:
  **3/3 calls passed `--max-tx-underruns 0`**, zero underruns on both ends,
  exact payloads and clean teardown. Source-side TX capture rates were
  **7,999.604–8,000.153 Hz**, with no sequence/timestamp gaps. Maximum captured
  source-side spacing was 29.901 ms; per-packet jitter is not eliminated.

The [wakeup repair report](media_underrun_diagnostics.md) preserves pre-repair
findings and follow-up evidence. The loaded run is not a zero-underrun verdict,
and neither it nor a short idle run establishes hard-real-time or soak safety.

### Complete source-clock gate and larger payload follow-up

`tools/rtp_qualification.py` adds deterministic, portable tests of the actual
source-capture gate, not a host-scheduling threshold in CI. It streams the
harness's RAW PCAP records without sorting, checks complete source packet counts
against final send counters, and preserves sequence/timestamp wrap semantics.
The qualifier now requires continuous source TX streams within its explicit
clock budget; an optional gap budget detects stalls/bursts that average rates
alone miss. Protocol-only passes remain pending until closed-capture analysis.

- Portable gate: **499 passed**, 710 deselected; C self-test PASS.
- Fresh source archive with the changes applied, a new virtualenv/build and no
  extracted firmware/private captures supplied: **499 passed**, 710 deselected.
- Source timing/qualifier/clock/trace modules: **126 passed**, with resource
  warnings as errors. The new capture module has **39 deterministic cases**,
  including slow sources masked by good receive clocks, aligned capture loss,
  truncation, duplicates/reordering, wrap, wrong/extra SSRCs, clock regressions,
  gap/burst pairs, invalid budgets and top-level failure propagation.
- Broader native gate: **1,207 passed**, 2 slow deselections, **303.85 s**.
  Current combined inventory: **1,209**; no uninterrupted all-including-slow
  run is claimed.
- Prior repaired strict captures pass the new gate. Historical pre-repair
  protocol successes fail on their ~7,550–7,594 Hz source streams. These are
  explicitly offline control replays, not fresh modem calls.
- Fresh strict **3 × 16,384 bytes per direction**: **3/3 passed**, zero TX
  underruns on both ends, exact independent payloads and clean reconnects/
  teardown. All source packet counts match final counters, with zero sequence
  gaps/timestamp jumps. Observed source rates **7,999.915–8,000.059 Hz**;
  requested maximum-gap budget **40 ms**, observed maximum **29.814 ms**.

See the [qualification guide](v34_qualification.md) for commands, schema 2,
receipts and evidence paths. This post-call analysis adds no media hot-path
work and changes no buffering, RX recovery, sample generation or DSP scheduling.
The longer case is still three ~98-second streams, not a long soak, physical
clock-accuracy proof, full-bandwidth duplex or TCP qualification.
