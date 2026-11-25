# Eicon–MultiTech call tests, 24 October 2026

## Sustained follow-up, 25 October 2026

Eicon controller 2/ttyds6 called MultiTech 7910/ttyM4 using temporary
`AT+MS=V90a,1`. This call reported **CONNECT 31200** at Eicon and
**CONNECT 31200 V42bis** at MultiTech, so it does **not** reproduce the
previous 56000 connection. The following checks establish reliability
at this run's negotiated rate, not sustained V.90/56K performance.

| Phase | Traffic generation | Verified bytes received | Result |
|---|---|---|---|
| MultiTech to Eicon | 120 seconds | 438272 | PASS, zero errors |
| Eicon to MultiTech | 120 seconds | 502784 | PASS, zero errors |
| Simultaneous duplex | 360 seconds | 1362944 at MultiTech; 1301504 at Eicon | PASS, zero errors |

All transfers stayed on one continuous modem call. Queued bytes were
allowed to drain, giving about 10m44s for the traffic phases including
drain and inter-phase pauses. Receive throughput was approximately
3584–3589 bytes/s per active direction. Total verified payload: 3605504
bytes. Each 1024-byte incompressible SHAKE256 payload was checked for
exact content, sequence and CRC32; opposite endpoint byte totals matched.
No application integrity errors, exceptions or call drops occurred.
This does not measure modem-level retransmission/retrain counts.

The idle `multitech-ppp.service` was paused with automatic restart in a
shell trap. Test cleanup dropped DTR, restored host termios, and closed
both ports. PPP answerer, bri-test-answerer and diva-sip-gateway were
active afterward; ttyds6 had no owner, ttyM4 was held by restored pppd/chat.
After cleanup an independent call from 2900 to 7910 appeared; it was not
terminated by this test. No PPP interface was established at that check.

Harness: `/var/tmp/multitech_long.py` on eicon420.
Evidence: `/var/tmp/multitech-eicon-long-20261025.jsonl` on eicon420.
The answerer service had transient chat initialization failure after
restart and remained running in its existing persist/retry loop.

## Original short tests

Live hardware tests on eicon420, approximately 11:35–11:39 NZDT.

Path: Diva SN 1331 port 2 (controller 2) → Cisco BRI0/0/0 →
Cisco BRI0/0/1 → MultiTech first BRI, modem `/dev/ttyM4`.
Cisco switches these calls locally; they do not traverse Asterisk/RTP.
Existing answerer 7900 is on Eicon controller 2. Number 7910 routes to
MultiTech through Cisco 0/0/1.

| Origin / destination | Modem selection | Negotiated rates | Data result |
|---|---|---|---|
| Eicon ttyds6 → MultiTech 7910 | Eicon `AT+MS=V34,0` | 31,200 bit/s each way | 32,768 bytes each way, exact matches |
| MultiTech ttyM4 → Eicon 7900 | Existing modem/answerer selection | V.34, 31,200 bit/s each way | 44,032 bytes MT→Eicon and 106,496 bytes Eicon→MT; zero stream errors at both ends |
| Eicon ttyds6 → MultiTech 7910 | Eicon `AT+MS=V90a,1` | Eicon TX 31,200 / RX 56,000 bit/s | 32,768 bytes each way, exact matches |

The V.90 call produced CONNECT 56000 at Eicon and CONNECT 31200 V42bis
at MultiTech. Diva status independently retained `Modem ASYNC 31200/56000`.
Thus MultiTech→Eicon is the 56,000 direction, and Eicon→MultiTech is 31,200.
These are negotiated carrier rates, not measured application throughput.
The exact-payload test took 9.210 seconds Eicon→MT and 17.123 seconds
MT→Eicon on the V.90 call; sustained throughput was not characterized.

Exact transfers use deterministic SHAKE256 payloads and byte-for-byte
comparison. The stream test also verifies sequence, CRC32 and expected
payload, with matching opposite byte totals reported by the two ends.

Remote scripts and logs on eicon420:

- `/var/tmp/multitech_eicon_incoming_32k.py` and
  `/var/tmp/multitech-eicon-incoming-32k.log`
- `/var/tmp/multitech_eicon_outgoing.py` and
  `/var/tmp/multitech-eicon-outgoing.log`
- `/var/tmp/multitech_eicon_incoming_auto.py` and
  `/var/tmp/multitech-eicon-auto.log`

All three final test runs returned PASS/ok. An initial staging edit placed
the dial command inside a setup loop; that run failed and was corrected
before the successful runs above. No persistent router or card changes
were needed for these tests. MultiTech S0 was already 1 and remained so.
The V.90 selection was temporary on the unused Eicon test TTY.

Cleanup verified: no active Cisco ISDN calls, zero allocated CCBs, both
bearers free on each voice BRI; no owners of ttyM4/ttyds6; bri-test-answerer
and diva-sip-gateway active. Other MultiTech endpoints and its disconnected
second BRI were not exercised.
