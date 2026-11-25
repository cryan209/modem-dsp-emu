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
| `.venv/bin/python -m pytest tests/test_v42_lapm.py` | One selected Python module | Build first if that module loads the C library |
| `.venv/bin/python -m pytest --collect-only -q` | Enumerate tests without executing them | Environment and built library |

The `portable` marker is an explicit allowlist in `tests/conftest.py`. It
covers DSP core contracts, IDI/AT, IPAC, framing/protocols, resampling, media
clock behavior, capture parsing, and preflight handling. It excludes native
MIPS execution, private captures, real network/TUN services and hardware.
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

### Remaining assertion failures

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

Reproduce after building and passing the native preflight:

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
