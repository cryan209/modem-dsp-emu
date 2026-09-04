# RTP frame-size investigation — 2026-09-05

15 ms is a valid experimental G.711 packet size, but an optimal live size has
not been established. Keep the live 20 ms default pending a working packetized
positive control and real-path A/B calls.

## Packet size and DSP processing are separate

At 8 kHz, 10/15/20/30 ms contain 80/120/160/240 G.711 bytes and require
100/66.67/50/33.33 packets per second. With 40 bytes of IPv4/UDP/RTP headers,
the corresponding rates are 96/85.33/80/74.67 kbit/s, excluding link overhead.
15 ms reduces the maximum packet accumulation interval by 5 ms versus 20 ms,
but increases packet count by one third.

RFC 3551 sections 4.2, 4.3 and 4.5.14 permit sample-based G.711 packet sizes
such as 120 bytes; 20 ms is the default, not a required framing boundary:
https://www.rfc-editor.org/rfc/rfc3551.html

At the time of these experiments, the live endpoint used SAMPLES_PER_PACKET=160 for DSP work and RTP,
and advertises a=ptime:20. The reactive process adapter also requires 160-byte
frames. Changing the global constant to 120 would change DSP/service timing
and violate that adapter contract. A live experiment needs a separate RTP
packetizer and wire clock, retaining 160-sample DSP exchanges, sample-counted
buffer targets, matching SDP, and timestamps advancing by actual payload size.
Changing only SDP cannot perform this experiment.

## Fresh experiments

Results and logs: artifacts/rtp-frame-size-20260905/.
Each experiment runs 90 seconds of simulated audio with sideband enabled and
--arm-samples 12000. These are frame-driven localhost experiments, not real-time
network timing measurements or hardware modem calls.

The direct control connects at 10.38 seconds, stays connected at both ends,
and transfers 1343 downstream and 1185 upstream bytes with zero bad FCS.
The 10, 15, 20 and 30 ms packetized controls never connect and transfer zero bytes.
All have the same 320-sample startup cushion in each direction. Their loop also
exchanges both receivers before putting either transmitter output, unlike the
direct sequential coupling. This changes feedback delay/topology as well as
packetization. Therefore these failures cannot rank packet sizes, or show that
RTP headers themselves cause failure.

Inserting 120 silent samples downstream at 40 seconds in the direct rig leaves
both ends connected with the same valid byte totals as the clean control.
This is a sample insertion experiment, not a lost-packet or live 15 ms test.
Fresh 160/162-sample controls reproduce the archived alignment sensitivity:
160 leaves the caller disconnected (T401 SABME retry limit), with only 493/615
bytes transferred, while 162 survives with the clean control's 1343/1185 bytes. Both 120 and 162 are multiples of six; 160 is
not. This is a hypothesis about this bridge, not a reason to insert silence or
claim a universal V.90 packet size. Correct reassembly preserves the sample
stream regardless of packet boundaries.

## Existing live capture measurements

Recomputed using tools/rtp_pcap_timing.py CAPTURE --buffer:

- run34: 160-byte packets, no sequence gaps or timestamp jumps; inbound p99
  gap 23 ms, maximum 37 ms; simulated receive buffer never starved.
- run48: 160-byte packets, no sequence gaps or timestamp jumps; inbound p99
  gap 30 ms, maximum 115 ms; simulated buffer starved, requiring 71 ms prefill.

These are historical captures, not before/after tests of current settings.
They show why smaller packets alone cannot establish a cure for the observed
arrival stalls. The PPP profile already configures 200 ms receive prefill.

## Next qualification

First obtain a connected packetized 20 ms baseline with the same feedback delay
as the direct positive control. Then compare 10/15/20/30 ms with identical
buffer latency and DSP scheduling. For the actual SIP path, verify observed
payload lengths and pacing in both directions: SDP preferences do not prove
that a gateway changed its transmit size. Rank repeated calls by handshake
success, time to connect, negotiated rate, sustained valid data, retrains,
receive substitutions and transmit underruns. No live default was changed.

The probe now accepts 10 ms and reports payload validation false when no data
was received; previously an empty byte stream misleadingly validated as true.
Python compilation and the 10 ms plus 160/162-sample runs passed after this
reporting change. Earlier outputs retain their original empty-stream flag;
connection state and byte counts above are the qualification criteria.

## Configurable endpoint follow-up

The endpoint and loopback runner now accept `--rtp-packet-ms` (1–200, default
20). RTP packetization and pacing are separate from the fixed DSP quantum;
SDP and payload-based timestamps follow the selected duration. See README
for usage and buffer rounding. This enables live qualification; no new live
connection success claim is implied by the implementation.
