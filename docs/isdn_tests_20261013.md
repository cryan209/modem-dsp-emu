# Native ISDN profile tests — 13 October 2026

**Historical baseline:** the implementation now fixes this routing and adds a host-native B-channel backend. See [current native implementation and evidence](isdn_native_20261013.md). The blocked matrix below describes the code before that change.

All nine requested native ISDN profiles are blocked at configuration, before a native bearer or payload test can begin. The AT parser accepts them, but the actual SIP endpoint configuration handoff sends modem options to the native shim. Its signalling builder selects asynchronous modem B1 `0x11`; its network-layer builder selects V.42 B2 `0x09` answering and `0x0A` calling. No profile-specific ISDN protocol selection reaches either builder.

## Measured matrix

The probe invokes `AtParser.feed(AT&F<n>)`, the actual `EiconSipEndpoint.at_apply_options()`, and the actual native shim's signalling and both-direction network ASSIGN builders. It decodes their emitted IDI descriptors and compares the minimum B1/B2/B3 selection with the shipped driver's mappings.

| Profile | Requested mode | Required B1 | Required B2 answer / caller | Result |
| --- | --- | --- | --- | --- |
| 1 | X.75 | `05` | `01 / 01` | Wrong stack |
| 2 | V.110 synchronous | `0C` | `03 / 02` | Wrong stack |
| 3 | V.110 asynchronous | `0D` | `03 / 02` | Wrong stack |
| 6 | V.120 64k | `05` | `08 / 08` | Wrong stack |
| 7 | V.120 56k | `08` | `08 / 08` | Wrong stack |
| 8 | Bit-transparent B-channel | `09` | `03 / 02` | Wrong stack |
| 9 | HDLC / transparent PPP | `05` | `03 / 02` | Wrong stack |
| 10 | HDLC / transparent 56k | `08` | `03 / 02` | Wrong stack |
| 15 | X.75 with V.42bis | `05` | `0C / 0C` | Wrong stack |
| 5 | Asynchronous modem control | `11` | `09 / 0A` | Configuration matches |

All rows require transparent B3 `04`. All nine native rows actually emit `11 / 09 / 0A / 04`. The control match means configuration only; it does not count as a new modem payload test.

The reference values come from `docs/divas4linux-master/AT.txt` (“Supported TTY Profiles”), `tty_module/isdn.h` B1/B2/B3 definitions, and `tty_module/isdn.c` `protmap`, `putcai()` and `assign_nl()`. In particular, `putcai()` changes HDLC B1 `05` to HDLC_56 `08` at the 56k baud code. V.120/X.75 also require their own DLC; matching the minimum stack identifiers alone will not establish functional support.

## Reproduction and evidence

```sh
/Users/scottcryan/v90modem/.venv/bin/python tools/eicon_isdn_profile_probe.py \
  --json artifacts/isdn-profile-probe-20261013.json

/Users/scottcryan/v90modem/.venv/bin/python -m unittest discover \
  -s tests -p test_isdn_profile_probe.py
```

The probe exits **1**, deliberately, because nine selected profiles emit the wrong stack. Its JSON records the full CAI, signalling ASSIGN and both-direction NL ASSIGN bytes for every row, as well as individual mismatches. Select a single row with `--profile 9`, for example. The modem control `--profile 5` exits 0 for its configuration match; all results explicitly keep `payload_transfer_verified=false`.

Five harness regression tests pass: valid native stack fixtures, detection of modem misrouting for every native profile, independent caller B3 checking, recording the actual endpoint/native-builder results, and the modem control. The real configuration probe's nine blocked results are separate from these five passing harness tests.

## Limits and next implementation boundary

This is real configuration-path testing, **not** execution of native ISDN firmware, an ISDN call, or a native B-channel payload test. No external card or line was contacted. Sending modem PPP through the existing G.711/DSP loopback would test a different protocol and is not an ISDN pass.

Before end-to-end payload testing, the project needs profile-specific CAI and NL descriptors connected to call setup, plus a native B-channel transport/firmware ingress that preserves its bytes or bits instead of routing through the audio modem. The initial payload control should be bit-transparent profile 8, followed by HDLC/PPP 9/10, then rate-adapted V.110 and link-layer V.120/X.75. Each requires both-direction known payload comparison and error/rate checks; HDLC/PPP also requires successful PPP negotiation and traffic. The current PCM/V.90/V.34 fixes were not changed by this probe.
