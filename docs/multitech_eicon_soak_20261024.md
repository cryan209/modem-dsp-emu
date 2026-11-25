# Ten-minute Eicon–MultiTech duplex soak, 24 October 2026

Result: **PASS for data integrity and carrier stability**. Sustained
application throughput, especially MultiTech→Eicon, remains below the
negotiated carrier rate and needs separate investigation.

## Setup and method

Same hardware path as the short call tests: Diva SN 1331 port 2/controller 2
(`/dev/ttyds6`) → Cisco BRI0/0/0 → Cisco BRI0/0/1 → MultiTech first BRI
(`/dev/ttyM4`). Eicon originated to 7910 using AT+MS=V90a,1; MultiTech
autoanswered with its existing S0=1. Cisco switched the call locally.

Negotiated V.90: Eicon TX 31,200 / RX 56,000 bit/s. Both DCD signals were
present at start and checked throughout the streams. Two concurrent workers
ran `/usr/local/sbin/stream_test.py` for 600 seconds of sending, allowing
queued data to drain afterward. Every 1,024-byte payload is deterministic
SHAKE256 data, framed with sequence number and CRC32. Receivers check all
three; final assertions also reconcile transmitted and received byte totals
across endpoints. Progress was logged every 30 seconds.

## Results

| Direction | Verified payload bytes | Frames | Errors | Receiver elapsed | Mean received payload rate |
|---|---:|---:|---:|---:|---:|
| Eicon → MultiTech | 1,973,248 | 1,927 | 0 | 620.447 s | 3,180.4 bytes/s |
| MultiTech → Eicon | 542,720 | 530 | 0 | 604.520 s | 897.8 bytes/s |

Both endpoints returned ok=true; cross-end totals matched exactly.
The run completed after 600 seconds of generation plus approximately
20.4 seconds to drain the slower outstanding queue. No unexpected carrier
loss or disconnect occurred.

Largest observed gap between complete received frames:

- At Eicon: 5.46 seconds.
- At MultiTech: 1.78 seconds.

Diva modem diagnostics at intermediate and final inspection:

- TX/RX remained 31,200/56,000 bit/s.
- SNR 52 dB, receive level -15 dBm, echo level -57 dBm, round trip 10 ms.
- Local/remote retrains: zero; local/remote resyncs: zero.
- D-channel errors remained zero, from 359 TX / 363 RX frames near start
  to 455 TX / 459 RX at final inspection.
- Both Cisco voice BRI interfaces reported zero CRC, framing, overrun,
  abort, output error and collision counts during the soak.

The integrity pass is not a claim of full-rate sustained throughput.
In particular, the nominally 56 kbit/s MultiTech→Eicon direction delivered
about 0.9 kB/s during concurrent traffic, with multi-second gaps. This run
does not identify the bottleneck; stable carrier rates and zero retrains
alone cannot establish whether it is the host/driver, flow control, modem
buffering, ARQ behavior, or another part of the data path.

## Artifacts and cleanup

On eicon420:

- Script: `/var/tmp/multitech_eicon_soak_600.py`
- Full progress/results log: `/var/tmp/multitech-eicon-soak-600s-20261024.log`

No persistent configuration changes were made for this test. Both TTYs
were released and DTR dropped. Final Cisco inspection confirmed no active
calls, zero allocated CCBs, and both bearer channels free on both voice
BRIs. Both Eicon services remained active. The unrelated HWIC links still
have physical activation without established signalling; they were not
part of this soak.
