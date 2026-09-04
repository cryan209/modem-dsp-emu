# V.90 failure correlation — 2026-09-05

The archived `meas` call provides evidence of receive starvation preceding a
V.90 fallback. This is stronger than a packet-size hypothesis, but does not
establish the source of the arrival stalls or prove that every failure shares
this cause. No new hardware call was made.

## Failed call versus positive control

Inputs are `artifacts/interop/v90d-reliable/{meas,good1}` with `.adsp.csv`,
`.stdout.log`, `.rtp.pcap` and `.rx.ulaw` suffixes. `good1` is a control for
sustained firmware data state, not proof of application payload delivery:
its log explicitly reports mark fill and zero host payload.

| Measurement | meas | good1 |
|---|---|---|
| First V90D data state | 27.02 s | 27.00 s |
| First ratechange status after data | 54.18 s | none observed |
| Failure marker / controller | 0x5678 / 7 at 56.18 s | none observed |
| Leave V90D data state | 56.20 s, INFO overlay | remains in data until call end (~56.46 s) |
| RX substitutions | 0 at 50 s; 160 by 53 s | 0 throughout |
| RX drops / TX underruns | 0 / 0 | 0 / 0 |
| RTP sequence discontinuities | 0 in both directions | 0 in both directions |
| Maximum inbound arrival gap | 107.307 ms | 29.630 ms |
| Inbound p99 arrival gap | 25 ms | 28 ms |

The near-identical p99 is misleading: the failed call has a small number of
much larger gaps. Its first inbound gap above 60 ms starts at 52.310841 seconds
relative to the first inbound RTP packet and lasts 64.575 ms. Further gaps
of 89–107 ms recur from 52.61 seconds onward. These are PCAP wall-clock offsets;
the table's firmware times use the DSP sample clock. They must not be treated
as sample-exact equivalents.

At DSP time 50 s, meas reports SNR 39.5 dB and slicer error 0x002e. By 54.18 s,
SNR is 33 dB and error 0x005a, with ratechange asserted; at 56.18 s, SNR is
31.5 dB and error 0x0072, CTS clears and flow_blocked is asserted. RxLevel
remains 0x001e. Good1 remains stable near SNR 21.5 dB and error 0x0135 at a
lower upstream ceiling. Therefore absolute SNR across different negotiated
rates is not a universal failure threshold; the within-call deterioration and
preceding substitution are the useful evidence.

The observed chain is: large inbound arrival gaps and 160 substituted samples,
then deteriorating receiver metrics, then ratechange, then fallback. This
supports receive starvation as a contributor. Zero packet loss did not mean
unchanged DSP input. Zero transmit underruns also rules out that particular
local failure mechanism for this call. The PCAP measures host-observed arrival:
it cannot separate a gateway/network stall from delayed local socket service.

## Limits and archive integrity

These two calls do not have matching `.fed.ulaw` recordings, so the exact
substituted sample positions cannot be recovered from their logs. We can bound
when the counter changed, not claim byte-exact reconstruction. The available
`artifacts/loopback/caller.fed.ulaw` is dated August 22 and has 258,240 bytes,
whereas its current `.rx.ulaw` is dated August 28 and has 5,099,520 bytes. They
are not a safe same-call comparison. Some older run37/run38 CSVs also reset
the sample clock within a file; those require call segmentation before analysis.

A separate recovery failure is already documented in analysis volume 06,
Sessions 241–243: after a successful recovery to 4800, a second MP omits 4800
and offers a higher rate range following an estimator reset. Recorded input
replays the failure, and an opt-in policy rewrites the offer; a live second
recovery under that policy remains unqualified. It must not be conflated with
this starvation-associated meas call.

## Reproduction and diagnostic changes

Timing reports are saved in `artifacts/v90-failure-audit-20260905/`:

```sh
python tools/rtp_pcap_timing.py artifacts/interop/v90d-reliable/meas.rtp.pcap --buffer --local 10.69.70.103
python tools/rtp_pcap_timing.py artifacts/interop/v90d-reliable/good1.rtp.pcap --buffer --local 10.69.70.103
```

The analyser formerly guessed the gateway as the local endpoint when both had
one RTP port. It then analysed our outbound stream as inbound and reported a
healthy buffer. It now requires `--local` when its inference is ambiguous.
The existing buffer simulation is a fixed-quantum approximation, not a replay
of the endpoint's hold/producer logic. Its 62 ms deficit for meas is not a
validated recommended jitter setting; variable packet sizes also require a
sample-counted model rather than counting every packet as one 20 ms quantum.

`--trace-retrain` now includes contemporaneous receive substitution/drop/hold
counts, receive queue depth, TX underruns, and RX/TX consecutive repeat counts
in the one-second pre-event history. The event header includes Unix wall time
for correlation with the PCAP. History values after sample remain hexadecimal,
as with the existing receiver-state columns. Repetition counts are consecutive,
not lifetime totals. Tests verify both pre-event zeros and event counter values.

For the next live comparison use a unique capture prefix and both
`EICON_DUMP_FED_RX=1` and `--trace-retrain --trace-file PATH`. Preserve packet
size, gain and modem options while comparing the existing 40 ms RX prefill
against 200 ms. The PPP profile already uses 200 ms; this is not a new default
change. Require a same-call fed/wire comparison, no new substitutions or
repeats around the event, and sustained valid payload before calling it fixed.
