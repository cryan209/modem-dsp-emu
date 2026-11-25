# Modulation continuation — 14 October 2026

The high-rate V.34 limitation recorded below was subsequently fixed by
correcting ADSP add-with-carry overflow. See [the later arithmetic fix and
data results](v34_adc_20261014.md); preserve the earlier captures as the
pre-fix baseline.

## Direct V.32 mailbox

`tools/v34_mailbox.py` now services the shared LEC overlay `0x0266` when
bootpage `DM(0x3FB0)` is 2 (V.32). Other bootpages in that image remain
excluded. Widths use the ADDSP DATASTATE speed table, accepting only two
through six bits per 2400-Hz datagram. Missing TX rate falls back to the
published symmetric RX rate, as the native backend does.

The LEC interface acknowledges TX request F on the host, rather than waiting
for a DSP clear that never occurs. Its readiness follows a TX request and
published rate, not the V.34 progress ladder. These contracts come from
`NativeMipsModem._lec_page_datagram_bits()`, `_next_tx_words()` and
`_service_tx_request()`, whose measurements are recorded in the existing
analysis. Tests cover rate publication, 9600-to-7200 renegotiation, RX bit
order, host acknowledgement and exclusion of V.22 in the shared overlay.
This adds host handling; it does **not** establish a new V.32 payload pass.

The 60-second strict `v32,0` mixed Analog/PRI baseline stalls with the caller
on V.8 and the answerer at `0x0044`. The 45-second `v32b` automode run falls
back to bootpage 1 (V.22), ends at `0x00EA` and publishes zero speed words.
Neither exercises a negotiated V.32 stream. Captures are
`artifacts/v32-20261014-baseline` and `artifacts/v32-20261014-automode`.

## Global modulation prerequisite fix

The loopback previously returned early from `end_environment()` whenever
the per-end modulation option was absent. Thus `--modulation v34,...` set
the requested menu but skipped the PRI SPORT expansion prerequisite that
`--answerer-modulation v34,...` applied. Global V.90A also missed its extra
PRI overlay staging. The helper now resolves the effective modulation from
the assembled endpoint environment before applying prerequisites. Explicit
endpoint modulation and explicit SPORT overrides retain precedence.

Regression tests compare global and per-end V.34, V.90 and V.90A setup and
check overrides and Analog exclusion. The helper was moved to module scope
for direct testing.

The original 21600 and 19200 runs in `artifacts/v34-20261014-21600` and
`artifacts/v34-20261014-19200-control` used the faulty global-option path
and retrained before DATA. They are **not** evidence of a rate boundary.

A 45-second 9600 control using per-end modulation passed CHAP/IPCP and
10/10 peer ping replies with zero PPP FCS errors and zero data-link resets.
See `artifacts/v34-20261014-9600-control`.

The corrected 55-second global-option 21600 repeat reaches DATA with nine
bits per datagram in both directions and establishes LCP/CHAP, but retrains
twice and never opens IPCP. The answerer initiates both excursions when
compared by wall time (first marker 1791925087.246772 versus caller DATA
exit 1791925087.487082). PPP reports zero FCS errors and zero link resets;
that does not make this an IP pass. See
`artifacts/v34-20261014-21600-fixed`.

The repaired global-option 19200 run passes 20/20 peer pings in 45 seconds,
with zero FCS errors, zero data-link resets and no retrain on either end.
This reproduces the existing working rate through the formerly broken
option path. Capture: `artifacts/v34-20261014-19200-fixed`.

Reproduce the global-option pass:

```sh
/Users/scottcryan/v90modem/.venv/bin/python tools/eicon_loopback.py \
  --python /Users/scottcryan/v90modem/.venv/bin/python --seconds 45 \
  --caller-firmware-set analog109 --answerer-firmware-set pri117 \
  --modulation v34,0,19200,19200,19200,19200 \
  --caller-kernel-dispatch --ppp --ppp-ping peer --ppp-ping-count 20 \
  --trace-retrain --capture-dir artifacts/v34-19200-repeat
```

Validation: 165 tests passed across mailbox, loopback environment, direct
modulation, LAPM, NL data bridge and firmware-backed Analog kernel suites.
Python compilation and `git diff --check` passed.
