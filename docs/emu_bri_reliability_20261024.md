# BRI emulator reliability, 24 October 2026

26 October follow-up: corrected loss of the firmware's PCMU negative-zero
wire code at the live BRI adapter boundary. Exhaustive G.711 round-trip
checks, three physical-card reconnects and a60-second duplex generation
test pass. This does not establish that signed zero caused the historical
startup failures or complete the full qualification below. See
[correction, exact results and limitations](bri_wire_zero_20261026.md).

The physical test path is Eicon controller 2, `/dev/ttyds6`, through Cisco
and Asterisk extension 6001 to the BRI firmware emulator. The negotiated
initial rate is CONNECT 38666. Both endpoints generate different deterministic
SHAKE256 streams and compare every received byte. The local PTY verifier is
armed before dialing, and the physical modem hangs up only after both ends
confirm receipt. No persistent router or modem profiles are changed.

## Failures and recovery change

`artifacts/bri-reliability-20261024-run1` did not finish the 1 MiB duplex
transfer: upstream throughput collapsed, then the modem retrained repeatedly.
The run was stopped; checked prefixes had no byte mismatches. Run 2 reproduced
the failure with a deeper receive jitter buffer. Its new LAPM telemetry showed
13,137 transmitted REJs by modem time 170 seconds, with only 1,965 accepted
I frames. Neither incomplete run is a pass.

The receiver sent REJ for every out-of-order I frame. Each REJ can restart a
go-back-N peer's retransmission of the same window, causing a recovery storm.
The receiver now keeps a reject-exception state: one REJ per missing-frame
gap, cleared when the expected frame arrives or SABME resets the link. Polls
during that gap still receive an RR final response. This fixes the observed
protocol recovery defect; the full live reliability qualification is separate.

The regression explicitly checks repeated out-of-order frames, a poll during
recovery, delivery after filling the gap, a later independent gap, and link
reset. All 83 LAPM tests, 80 PPP tests, 13 V.42bis tests, and three integrity
probe tests passed. Socket and PTY tests require access outside the desktop
filesystem sandbox.

## Qualification run

`artifacts/bri-reliability-20261024-run3` runs the corrected receiver with
200 ms receive jitter, 1,000 ms depth, and a 500 ms hold budget. The sequence
is 1 MiB in each direction, 20 reconnects with 8 KiB in each direction, and
a 1,800-second duplex soak. Consult the run and phase `summary.json` files
for completed results; a started phase is not a pass. Run 3 completed the
1 MiB duplex phase in 285.4 seconds of physical transfer, with exact matches
and four receive-gap recoveries. Its first eleven reconnects passed.

Reconnect 12 failed during a startup V.90-to-V.34 retrain: T400 expired and
raw fallback delivered mark bytes before the physical modem reported CONNECT.
The mailbox updated the datagram widths but retained V.90's 1,333.3 Hz
detection clock when the V.34 stream used 2,400 Hz. The mailbox now updates
the clock when the new modulation reaches DATA readiness, and the LAPM timer
rescales its consumed ticks to preserve elapsed seconds. Two new regressions
cover the transition and elapsed-time accounting (84 LAPM and 21 mailbox tests
pass). Run 4 restarts all 20 reconnects, followed by the full soak. The first
run's eleven successful calls are not combined into a 20-call pass.

Run 4 stopped after nine successful reconnects. Call nine recovered through
substantial retransmission (40.7 seconds for the payload); call ten returned
NO CARRIER after stalling at V.90 progress `0x00b0`, before LAPM activation.
That call reported no dropped/substituted RX samples and no TX underrun.

Inspection found an omitted host contract in the new BRI adapter: overlay
download replaces V.90D's shared bulk delay worker with an RTS, delegating its
near/far delay lines to `Card._service_v90d_bulk()`. The direct frame path called
that service, while the BRI SPORT1 path did not. BRI host completion now serves
the same delay ABI at every frame boundary. Its regression checks both saved
length copies and distinct near/far delayed values through startup and wrap.
All four BRI tests pass. Run 5 repeats the complete reconnect/soak sequence
with RTP, codec audio and per-packet DSP captures enabled.

Run 5 passed thirteen reconnects and failed on call fourteen at the same
V.90 `0x00b0` state. The delay ABI omission was real, but correcting it did
not fully resolve startup reliability. The physical caller waited at `0x0095`
and retrained on its eight-second timeout. Emulator capture shows outer
record `0x19fb` / state `0x00b0`, inner record `0x1c4a` / state `0x0040`.

The captured failure also exposed a transport gap: inbound RTP packets were
delayed as much as 440.4 ms without a sequence-number gap; outgoing RTP had
a 266.4 ms gap and nine transmit underruns. The old 160 ms transmit cushion
could not bridge that stall. Timing measurements are saved in run 5's
`rtp-timing.json`. The hardware trace reader initially spun on stdin EOF;
it was stopped, repeated `unknown command <>` noise was removed while real
records were retained, and the replacement reader keeps its input pipe open.
No bearer audio was enabled in the hardware diagnostic reader.

The BRI profile and reliability harness now use a 600 ms real-audio transmit
cushion, 600 ms receive prefill, 2,000 ms receive depth and 1,000 ms hold.
A regression replays a 440 ms producer stall: the 160 ms cushion underruns,
while the 600 ms cushion sends the original quanta in order on schedule.
All 33 media-clock tests pass.

The first single-call trial with that cushion (`buffer600`) exposed premature
V.42 startup: V.90D started T400 at `0x00c6`, and its training tail lasted
longer than the detection timeout. The mailbox now starts detection only at
the actual `0x00d0` DATA state for both V.90 roles and keeps timers held through
V.90 retraining tails. All 22 mailbox tests pass. `buffer600-data` tests that
correction on the physical line before another complete qualification run.

`tools/eicon_bri_reliability.py` owns the test processes, stops at the first
failure, and tears down its SIP endpoint and physical call. Timed transfers
freeze both transmit counts at the deadline and drain those exact byte counts
before reporting success, so queued data is included in verification.

The next checks are real Linux PPP negotiation, ten pings, and 256 KiB HTTP
files in each direction, followed by an independent MultiTech peer. The PPP
probe creates a dedicated network namespace so host routes cannot bypass
the modem; its namespace, temporary peer/chat files, and owned pppd process
are removed on exit. These checks are not yet claimed as passes.
