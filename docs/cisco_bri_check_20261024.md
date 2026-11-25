# BRI inspection after Cisco connection, 24 October 2026

Read-only inspection of `eicon420.net.cryan.nz` at approximately 10:22–10:26
NZDT. Physical Cisco-to-Diva port mapping and Cisco management address have
not yet been supplied. No configuration changes, restarts, counter clears,
or calls were made.

| Controller | Card serial / port | Role | L1 | L2 |
|---|---|---|---|---|
| 1 | 1331 / 1 | TE | Activated | Connecting |
| 2 | 1331 / 2 | TE | Activated | UP |
| 3 | 1331 / 3 | NT per existing setup | Activated | Idle |
| 4 | 1331 / 4 | NT per existing setup | Down | Idle |
| 5 | 12196 / 1 | NT | Activated | UP, with repeated establishment/errors |
| 6 | 12196 / 2 | NT per existing setup | Down | Connecting |
| 7, 8 | 12196 / 3, 4 | NT per existing setup | Down | Idle |

Controller 2 is exchanging RR frames (`00 01 01 01`, `02 01 01 01`).
Across the inspection, its transmitted and received totals both increased
from 25 to 31 frames, with all transmit and receive error counters remaining
zero. This establishes functioning idle Q.921 signalling on that span;
call routing and bearer transfer remain untested. It is the likely Cisco
connection, pending physical mapping confirmation.

Controller 1 continues sending SABME (`00 01 7F`) and reporting
`MDL-ERROR(G)`. Receive errors increased from 15187 to 15198 across the
inspection. Controller 5 sees SABME, sends UA (`00 01 73`), and repeatedly
reports `MDL-ERROR(F)`; receive errors rose from 83 to 89. Its L2 UP label
therefore does not establish a healthy span. This resembles the previously
documented failing controller 1–5 link, but current cabling is unconfirmed.

Concurrent 25-second passive captures are on the host:
`/var/tmp/cisco-check-dch-{1,2,5}.txt`.

`divas4linux`, `bri-test-answerer`, and `diva-sip-gateway` are active.
The answerer listens on controller 2 for 7900 and controller 1 for 7910.
No new answered calls appeared in the inspected service journal.

Next: establish Cisco management access and physical port mapping, inspect
its BRI roles/Q.921 state and dial peers, then test a call on the confirmed
healthy span.

## Cisco inspection and successful call, approximately 10:29 NZDT

Accessed `toprouter.iot.cryan.nz` (`10.69.68.117`) over Telnet using
owner-supplied credentials. No router configuration changes made.

- BRI0/0/0: NET3 network role at L1 and protocol level, TEI 0,
  MULTIPLE_FRAME_ESTABLISHED. Initially 76 packets each way, zero errors.
- BRI0/0/1: NET3 network role, point-to-point-setup enabled, L1 ACTIVE,
  L2 NOT Activated, zero packets input/output. Physical activation alone
  does not establish the peer or prove this span usable.
- BRI0/2/0 through BRI0/2/3: administratively shut down.
- POTS peer 7900 routes 7900 to 0/0/0, forwarding all digits; peer 7910
  routes 7910 to 0/0/1 in the same way.

Staged the existing `tools/eicon420_v90a_dial.py` as
`/var/tmp/cisco-check-v90a-dial.py`. Verified ttyds6 unused and originated
from controller 2 to 7900, holding the echo test for five seconds.
Cisco showed incoming and outgoing call legs on 0/0/0 B1/B2, establishing
the controller 2 to BRI0/0/0 mapping. CONNECT appeared at 18.571 seconds.
Answerer journal: `CONNECT V90/LAPM/V42BIS/54666:TX/31200:RX`.
The caller received echo responses for lines 0 through 33, then exited
the menu and hung up normally. This is a successful text transfer, not
a binary integrity or sustained-throughput test.

Final controller 2 counts: 101 TX, 102 RX, every error counter zero.
Cisco confirmed no active calls afterward; ttyds6 was released.
Call log: `/var/tmp/cisco-check-v90a-dial.log` on eicon420.

Controller 1 briefly reported L2 UP during this pass, while its accumulated
receive error count continued to rise (15275 at final inspection). Its
physical peer and the peer of Cisco BRI0/0/1 remain unconfirmed. Do not
infer their mapping solely from matching dial numbers.

## Owner-confirmed mapping and second-span cause

Owner confirms SN 1331 port 2 -> Cisco 0/0/0 and SN 1331 port 3 -> Cisco
0/0/1. Controller 3 is still configured as NT, with automatic TEI and
eight L2 links, from its earlier MultiTech role. Cisco 0/0/1 is also NT.
This is a configuration mismatch; fix this before drawing conclusions
about that span's cable or termination.

Prepared `/var/tmp/cisco-divas-te3.rc` and `/var/tmp/cisco-divas-te3.xml`
on eicon420: controller 3 StableL2 RAM byte a -> 2, TEI RAM byte 0 -> 1
(fixed TEI 0), BRI link count 0 -> 1, plus matching saved shell settings
CCardNT[3]=0, CCardL2[3]=3, CCardP2P[3]=1. XML parsed successfully locally.
The live saved config SHA-256 before any application is
`cdcfef3a56181715329ff57c7c6efacb635088016b05ec34cab38eab77c48484`.

Automatic approval review rejected applying the change because the user
authorized investigation rather than a disruptive persistent role change
and card reload. No live configuration changes or service stops occurred.
Applying it awaits explicit user approval. Planned application includes
backup, idle-channel verification, CFGLib initialization, card 1331 reload,
and restoration of bri-test-answerer/diva-sip-gateway, with rollback on error.

The 7910 modem answerer remains bound to controller 1, not the newly wired
controller 3. The existing SIP gateway also listens on controller 3 as
extension 2910. Decide the intended incoming-call handling before changing
those service mappings. A first isolated test can originate on controller 3
to 7900, which already has an answerer on controller 2.

## Approved application and cross-span data test

Owner explicitly approved applying the change. Saved backup:
`/usr/lib/divas/divas_cfg.rc.before-cisco-te3-20261024` on eicon420.
Applied the staged CFGLib configuration and reloaded card 1331 only;
card 12196 remained active. Stopped and restored bri-test-answerer and
diva-sip-gateway around the reload. Controller 3 now reports NT FALSE,
and both Cisco BRI0/0/0 and BRI0/0/1 report TEI 0,
MULTIPLE_FRAME_ESTABLISHED.

First controller 3 -> 7900 cross-span call established at ISDN level but
failed modem training after 43.6 seconds. Cisco inspection found 0/0/0
uses A-law and 0/0/1 uses u-law; controller 3 was still A-law. Changed
controller 3 runtime `Config\B-Layer1\u-Law` to 1 and repeated.

The repeat connected after 38.156 seconds and echoed probe lines 0–16.
Caller reported CONNECT 28800; the answerer reported
`CONNECT V34/LAPM/V42BIS/28800:TX/26400:RX`. Thus the V.90A-configured
caller fell back to V.34; cross-span V.90 is not demonstrated. Cisco's
first failed cross-span call history showed g711ulaw media on both legs,
whereas the earlier same-span call had a direct TDM path (codec None).
The reason for fallback has not been established.

Saved the matching port 3 u-law setting: ForceLaw RAM offset 0x10 = 2,
and CCardLAW[3]=2 in the embedded shell settings. Initialized CFGLib from
that XML without another card restart, since the runtime setting already
matches. It has not been verified across reboot. Snapshot before this
last addition: `/var/tmp/divas_cfg.rc.before-cisco-ulaw-20261024`.
The extracted XML parses and the executable shell prefix passes bash -n.
Whole-file bash -n also examines raw XML after the vendor's `exit 0` and
fails on both the untouched backup and the edited file; that is not a new
script error.

Final controller 2: 35 TX / 37 RX; controller 3: 37 TX / 37 RX;
all signalling error counters zero on both. Both remain L2 UP, both
services active, ttyds6 released, Cisco has no active calls.
Logs: `/var/tmp/cisco-check-v90a-port3.log` (failed A-law trial),
`/var/tmp/cisco-check-v90a-port3-ulaw.log` (successful V.34 fallback).

Incoming-call service mappings were preserved: 7910's modem answerer is
still on controller 1, while controller 3 has the SIP gateway LISTEN.
Incoming modem calls to 7910 therefore still need a deliberate service
mapping decision; the successful test originated on port 3 and answered
on port 2 at 7900.

## Further wiring: HWIC and MultiTech

Owner subsequently connected the HWIC-4B-S/T and moved one 0/0/x connection
to the MultiTech. Exact new mapping is pending. Inspection after this:

- Cisco inventory confirms HWIC-4B-S/T V02 in slot 0/2. All four
  BRI0/2/0–3 remain administratively shutdown, configured HDLC, NET3,
  point-to-point. Their physical connections cannot yet be assessed.
- Cisco 0/0/0 retains MULTIPLE_FRAME_ESTABLISHED. 0/0/1 has L1 ACTIVE
  but only TEI_ASSIGNED (TEI 0), not MULTIPLE_FRAME_ESTABLISHED.
  It retains `isdn point-to-point-setup` from the earlier Diva connection.
- Eicon controller 2 remains L1/L2 UP. Controller 3 is now physically Down.
  Controller 1 remains Activated/Connecting; controller 5 Activated/UP.
  Controllers 4 and 6–8 are Down.
- MultiTech isicom is loaded; no ttyM ports were occupied. Ran the existing
  `isicom-diag/line_status.py` without reload. ttyM1 answers and reports
  ISIHP-2S, NET3 A-law, automatic data and voice TEI, PPP with autodetect.
  Its first BRI reports LINE ACTIVE / LINE NOT READY. ttyM2/3 answer but
  their second BRI reports LINE NOT ACTIVE / LINE NOT READY.
- ttyM0 mostly emits D-channel analyzer Identity Request records rather
  than normal command responses; ttyM1 supplies a clean status reading.

No configuration changes made during this inspection. Asked owner for
the HWIC-to-Eicon mapping, confirmation of MultiTech on 0/0/1, and
permission to enable the connected HWIC interfaces. If MultiTech is on
0/0/1, its automatic TEI versus that interface's point-to-point setup is
a configuration mismatch to resolve before another call trial.

## HWIC enabled and cabling mapped by link transitions

Owner instructed: "work it out enable all the ports". Issued `no shutdown`
on BRI0/2/0–3. Brief shutdown/no-shutdown tests on idle ports identified
peers from the corresponding Eicon L1 drop, rather than inferred numbering:

| Cisco | Peer | Evidence |
|---|---|---|
| 0/0/0 | SN 1331 port 2 / ctl 2 | Earlier successful calls and owner mapping |
| 0/0/1 | MultiTech first BRI / ttyM0,1,4,5 group | ttyM1 ATL8 changed from LINE ACTIVE to LINE NOT ACTIVE on Cisco shutdown |
| 0/2/0 | Unidentified | L1 DEACTIVATED, zero carrier transitions |
| 0/2/1 | SN 1331 port 4 / ctl 4 | Only ctl 4 dropped when Cisco shut down |
| 0/2/2 | SN 12196 port 4 / ctl 8 | Only ctl 8 dropped when Cisco shut down |
| 0/2/3 | SN 12196 port 3 / ctl 7 | Only ctl 7 dropped when Cisco shut down |

All ports were restored enabled, including 0/0/1, after mapping. Final
Cisco state: 0/0/0 L2 established, 0/0/1 L1 active/L2 not activated,
0/2/0 L1 deactivated, 0/2/1–3 L1 active/L2 not activated.
No active ISDN calls. Only administrative shutdown states were changed;
no TEI, companding, role, or dial-peer changes in this pass. Changes are
in running-config; startup-config was not overwritten.

Mapped HWIC peers are all Diva NT, so their basic TE/NT roles match the
HWIC user-side configuration. During initial negotiation, ctl 4 had zero
valid RX and 11 RX errors (11 wrong CRC), ctl 8 zero valid RX and 7 errors,
ctl 7 zero valid RX and 1 error. Cisco 0/2/1 also showed 3 CRC errors and
81 carrier transitions. Error samples include deliberate link toggles;
these totals are not an undisturbed steady-state error-rate measurement.
Nonetheless there are no successful D-channel exchanges on these spans
yet, so do not claim that enabling ports established usable signalling.

MultiTech's automatic TEI remains different from 0/0/1's inherited
point-to-point setting; its A-law also differs from that voice port's
u-law setting. Neither was changed during this enable-and-map pass.

## HWIC port 0 mapping and controller 3 restored to NT

Owner identified 0/2/0 as SN 1331 port 3 and explicitly requested changing
that Diva port back to NT. Initially deferred the card reload because
controller 2 had an active 6010 -> 7900 call; proceeded once all lines
reported Idle. Backup on host:
`/usr/lib/divas/divas_cfg.rc.before-hwic-nt3-20261024`.

Changed controller 3 StableL2 2 -> a, TEI 1 -> 0, BRI link count 1 -> 0,
with matching saved CCardNT=1, CCardL2=1, CCardP2P=0. Preserved other
settings, including the saved u-law setting. Loaded CFGLib, reloaded only
card 1331, and restored both services; card 12196 stayed active.
Saved XML parses and the executable shell prefix passes bash -n.

Both ends now report L1 ACTIVE, confirming the role mismatch caused the
previous physical-down state. Cisco 0/2/0 is ESTABLISH_AWAITING_TEI;
Diva ctl 3 has zero valid received frames and one invalid/CRC/aborted
frame event. Cisco likewise shows one CRC error and 72 carrier transitions
at inspection. Thus physical activation is restored, but usable signalling
is still not established, matching the other HWIC-to-Diva spans.
All four HWIC ports remain enabled and physically active. Cisco 0/0/0
has L2 established; 0/0/1 remains L1 active/L2 not activated.
No active Cisco calls; bri-test-answerer and diva-sip-gateway active.

## MultiTech current-status and call check

On the owner's question whether MultiTech now works, checked unused ports
without reloading or changing profiles. ttyM1 responds AT OK, and ATL8
reports NET3 A-law, LINE ACTIVE, LINE NOT READY. ttyM4 responds AT OK and
identifies Lucent ISI56128_MDM firmware 2.10V1 (29 November 2000).
ATD7900 from ttyM4 produced only command echo within a 45-second bound;
no CONNECT or call reached the Eicon answerer during inspection.
Sent escape and ATH afterward, but no response was received; closed the
TTY and restored its terminal settings. This is not a successful call
or a confirmed AT hangup acknowledgment. No data-transfer pass.
Driver/AT access and L1 work; usable signalling and calls remain unproven.

## MultiTech fixed: modem calls and verified data in both directions

Owner requested getting the MultiTech working. On Cisco BRI0/0/1 removed
`isdn point-to-point-setup`; on voice-port 0/0/1 set `compand-type a-law`.
The MultiTech is configured NET3 with automatic TEI and A-law. Removing
the inherited point-to-point setting immediately established Q.921 with
TEI 64. Cisco interface receive/error counters stayed clean.

Outgoing test from ttyM4 to 7900:

- Caller CONNECT 31200 V42bis; Eicon answerer V34/LAPM/V42BIS,
  31200 TX / 31200 RX.
- Unique text echo passed.
- Shared stream_test.py duplex test: MultiTech sent 45,056 bytes and
  received 106,496 bytes. The peer reported the matching opposite totals.
  Both reported zero errors and ok=true. Checks include sequence, CRC32,
  and deterministic SHAKE256 payload equality.
- Script timed out awaiting NO CARRIER after menu Q, but the answerer
  logged session ended and the Cisco active-call list was empty. This
  cleanup response timeout does not negate the independently recorded
  transfer pass. DTR was dropped and the descriptor closed.
- Log: `/var/tmp/multitech-cisco-verify-ttyM4.log` on eicon420.

Incoming test from Eicon controller 2/ttyds6 to 7910:

- ttyM4 existing S0 was 001 (autoanswer already enabled). Temporarily
  reasserted S0=1, with no persistent profile writes.
- MultiTech received RING, then both modems reported CONNECT 31200.
- Exact 8,192-byte payloads passed each direction. Existing helper compares
  received payload byte-for-byte; both transfers returned ok=true.
- Log: `/var/tmp/multitech-cisco-incoming.log`.
- Follow-up reopen confirmed ttyM4 AT OK and S0 still 001; ttyM1 ATL8
  reported LINE ACTIVE / LINE READY, both modems on-hook, all reported
  B-channel error counters zero.

Cisco startup-config was backed up to
`flash:before-multitech-working-20261024.cfg`, then write memory completed
with OK, saving the tested settings and previously requested HWIC enables.
Both Eicon services remained active and test TTYs were released.

One orphaned Cisco CCB (callid 8007) predating these successful tests remained
despite no active calls. Cleared it by shutdown/no-shutdown of idle 0/0/1;
both bearer channels then showed free (mask 0x80000003), zero allocated CCBs.
A repeat incoming call/data test after this cleanup also passed at 31200,
with exact 8,192-byte transfers each way. Log:
`/var/tmp/multitech-cisco-incoming-after-clear.log`. Final Cisco state:
0/0/1 TEI 64, MULTIPLE_FRAME_ESTABLISHED, zero active calls, zero CCBs,
both bearer channels free. No stale call-control entry recurred.

Verified scope is the first MultiTech BRI and modem ttyM4; the disconnected
second BRI and other modem/TA endpoints are not covered by these passes.

### Dial to 6312

At the owner's request, ttyM4 dialled 6312 through the Cisco and Asterisk.
Connected after 40.7 seconds with `CONNECT 19200 V42bis`. Received
`MULTITECH SOFTWARE SYSTEMS`, `USA`, and `login:`. No credentials or login
input sent. Hung up by dropping DTR after reading the initial banner.
Asterisk confirmed the bridge from Cisco endpoint 6501 to endpoint 6312
at 10.69.70.221:5060 during the test.

### 6312 rate follow-up: V.90 and asymmetric rates

Owner expected a better rate. Repeated the call while retaining the same
TTY open for ATI11 diagnostics (closing/reopening resets those statistics).
The repeat's short result was CONNECT 31200 V42bis after 31.1 seconds,
but ATI11 during the call and afterward both reported:

- Last Connection: V.90.
- Initial/final transmit carrier rate: 49,333 bit/s.
- Initial/final receive carrier rate: 31,200 bit/s.
- LAPM/SREJ, V42bis; remote retrains 1, local rate renegotiations 1.
- Receive signal power -46 dBm, transmit -14 dBm, round trip 154 ms.

Thus the short CONNECT result omitted the higher transmit rate on this
call. From the MultiTech's perspective it sent 49,333 bit/s toward 6312
and received 31,200 bit/s. Do not retroactively apply those figures to the
earlier CONNECT 19200 trial; that trial's ATI11 was not retained.

Asterisk used ulaw on both active legs, Read/WriteTranscode No, zero
reported packet loss, approximately 0–1 ms reported jitter at inspection.
Cisco used g711ulaw on its SIP side, with no reported RTP loss. The BRI
port remains A-law. That leaves a law conversion in the Cisco path as a
possible optimization target, but this sample already achieved V.90 and
does not establish that conversion caused the earlier lower result.
No settings changed during this follow-up. 6312 inherits the deliberate
ulaw/clearmode-only endpoint-modem template; do not casually enable both
laws and introduce uncontrolled transcoding.

Log: `/var/tmp/mt6312-baseline-profile.log` on eicon420. It received the
same remote login banner; no login input was sent. ATI11's noise-level
field changed from 0 live to an implausible 64882 afterward, so no SNR
claim is made from that field. ATH produced no acknowledgment, but the
call subsequently cleared: final Cisco inspection showed zero active
calls/CCBs and both bearer channels free, without a span reset.
