# eicon420 BRI hardware, 21 October 2026

Host: `eicon420.net.cryan.nz`. Hardware tests, not emulator results.

## Wiring confirmed by owner

| Card | Ports | Logical controllers | Role/peer |
|---|---|---|---|
| Diva 4BRI v2 SN 1331 | 1, 2 | 1, 2 | TE to SN 12196 ports 1, 2 |
| Diva 4BRI v2 SN 1331 | 3, 4 | 3, 4 | NT to Multitech |
| Diva 4BRI v2 SN 12196 | 1, 2 | 5, 6 | NT to SN 1331 ports 1, 2 |
| Diva 4BRI v2 SN 12196 | 3, 4 | 7, 8 | NT, unplugged |

Both Divas are PCI `1133:e013`, running protocol 122-11 and DSP 117-926.
Multitech is `10b5:2053`, subsystem `2053:1000`, a two-BRI hybrid.

## Configuration changes on host

- Initially only SN 1331 was active. Added SN 12196's four NT interfaces to
  CFGLib and the embedded XML/saved shell settings in
  `/usr/lib/divas/divas_cfg.rc`. The existing boot service loads this file.
- Direct links now have matching ETSI point-to-point, fixed TEI 0 at both
  ends. TE interfaces use permanent L2 activation. Unconnected NT ports
  retain P2MP. SN 1331 ports 3/4 settings were preserved.
- Reloaded Divatty after activating the second card: before reload it knew
  only four controllers and rejected `AT+iQ=a5`; afterward it knows eight.
- Briefly stopped/restarted both cards to apply P2P; answerer service restored.
- Tried runtime synchronising clock mode on SN 1331: no improvement;
  restored `L1SyncOpMode=0`.
- Backups: `divas_cfg.rc.before-12196-nt-20261021` and
  `divas_cfg.rc.before-p2p-20261021`, beside the live file.
- Standalone `divactrl load -c 5 -f ETSI -x ...` segfaulted in userspace.
  Working path: update CFGLib, then `divactrl load -CfgLib -c 5`.

## Results: not working calls yet

All connected Diva interfaces show L1 Activated; controllers 7/8 correctly
show Down. L1 activation is **not** proof of usable signalling.

`tools/eicon_bri_link_test.py` binds two otherwise unused TTYs to specified
logical controllers, selects X.75 (profile 1 by default), sets a temporary
accepted number, calls, and only after both CONNECT responses checks binary
SHA-256 payloads in both directions. It closes both ports on failure. It is
call-disruptive; do not use occupied TTYs. Example on host:

```
sudo python3 /var/tmp/eicon_bri_link_test.py 2 6
```

Tested calls 5->1, 1->5, 2->6 and 6->2 failed with NO ANSWER/NO DIALTONE.
No binary transfer was reached; there is no data-integrity pass claim.

Before matching P2P, link 2 failed TEI negotiation. After matching P2P, the
NT receives SABME `00 01 7f` and sends UA `00 01 73`; TE remains connecting,
frequently receiving SABME rather than the expected UA. Invalid frames,
aborts and CRC failures also appear. NT-side L2 UP alone is misleading.
D-channel traces were saved under `/var/tmp/dchannel-{1,2,5,6}.txt` (latest
capture); primary-card XLOG snapshots include `/var/tmp/post-p2p-{1,5}.xlog`.

Next physical check: verify BRI crossover pinout, pair integrity and proper
S/T termination, preferably isolate/test one span at a time. Asked owner
whether patch leads are crossover (3/6 <-> 4/5) or straight-through. Cable,
clocking, and hardware cause are not established by these observations.

## Multitech

At session start no `isicom` module, firmware file or `/dev/ttyM*` existed.
Previous substantial driver/firmware investigation is on the host in
`/home/scottcryan/isicom-port/README.md` and `isicom-diag/`; read these before
repeating tests. Persistent installation has not been done.

Temporarily loaded the previously tested `isicom-diag/isicom-baseline.ko`
with `probe_only=0` and staged its `firmware/isi4608.bin`. Ran the existing
read-only `line_status.py`, then unloaded module and removed staged firmware.
No persistent driver deployment or AT configuration writes were made.

- ttyM0/1 eventually answered; identified ISIHP-TA / ISIHP-2S.
- NET3 (A-law), automatic TEI, PPP with protocol autodetection.
- Reported Physical Layer LINE ACTIVE, Data-Link Layer LINE NOT READY.
- ttyM2/3 did not answer during this bounded pass.
- No Multitech calls or data-transfer success yet. Driver startup/endpoint
  reliability and D-channel operation remain separate open issues.

The answerer `bri-test-answerer.service` is active again. It listens on
controllers 1/2 for modem calls to 7910/7900; it does not provide routing
between spans or answer on controllers 3..8.

## 22 October 2026 follow-up

Live 15 s D-channel captures (`/var/tmp/live-dch-{1,2,5,6}.txt` on host),
taken simultaneously on both ends of each Diva-to-Diva link:

- NT ctl 5 sends RR `02 01 01 01` (T203 poll), then its own SABME
  `02 01 7F`, then UA `00 01 73` to each TE SABME it does receive.
- TE ctl 1 sees **none** of these. It logs only its own SABME `00 01 7F`
  repeated every second (T200), and in XLOG it receives a copy of its own
  SABME about 4 ms after sending it, which is one frame's duration.
- Ctl 2/6 behave the same. Only one NT frame (`02 01 01 01`) ever reached
  ctl 2 across all captures.
- Ctl 1's XLOG also shows `IPACX: ERROR D-TX xmit message repeat` (D-channel
  collision detection on the E echo), `invalid frame`, and `MDL-ERROR(G)`.
  Ctl 5 shows `invalid frame` and `MDL-ERROR(F)`.

So L1 is up, and TE-to-NT works with errors, but the NT-to-TE D channel does
not work in either link. The TE receives its own D bits back, which is what
the NT's E (echo) channel carries. Layer 2 and Layer 3 configuration is not
the cause: NT/TE, P2P, TEI 0 and permanent L2 all match in `divas_cfg.rc`.
Ctl 1 worked against the CORNET PBX on 9 October (`/var/tmp/dch1.txt` shows
clean RR exchange, with no self-echo), so ctl 1's TE receiver was good then.

Multitech: the temporarily loaded `isicom-baseline.ko` was used again, then
unloaded with the firmware unstaged. Meanwhile ctl 3 (SN 1331, NT) logged a
TEI Identity Request `FC FF 03 0F 72 6A 01 FF` and its own assignment of
TEI 107, `FE FF 03 0F 72 6A 02 D7`. The Multitech did not retry the request,
so it received the assignment. **SN 1331's NT mode passes NT-to-TE D frames
to the Multitech**; its L2 then stays idle because P2MP L2 is set up when a
call is made. Ctl 4 logged nothing, and ttyM2/3 again did not answer. The
Multitech cannot take part in anything unless its driver is loaded; nothing
loads it persistently. Also, no service routes calls between spans.

Suspects, narrowed down: either SN 12196's NT-mode transmit, which is common
to both failing links, or the two Diva-to-Diva patch leads. The next
discriminating test is physical. Put the Multitech's known-good lead between
SN 1331 port 1 and SN 12196 port 1. Alternatively, move the Multitech to
SN 12196 port 3 (ctl 7, already NT P2MP) and look for the TEI exchange.

### Role swap on link 1 (22 October 2026)

NT mode is the StableL2 RAM byte at offset 0x0e: `a` = NT, `2` = TE with
permanent L2. I left `divas_cfg.rc` alone. Instead, I loaded a copy of its
embedded XML into CFGLib with only that byte swapped, so SN 1331 port 1
(ctl 1) became NT and SN 12196 port 1 (ctl 5) became TE. I then stopped and
restarted both cards. The answerer was stopped for the test. Files on host:
`/var/tmp/cfgxml-{now,swap}.xml` and `/var/tmp/swap-dch-*.txt`.

- Both ends briefly showed Layer2 UP, then ctl 1 fell back to Connecting.
- Ctl 1 (now NT) logged only its own SABME `02 01 7F`. None of ctl 5's
  frames arrived.
- Ctl 5 (now TE) received ctl 1's SABME and answered UA `02 01 73`. It then
  polled with RR `00 01 01 01` and retried SABME `00 01 7F`. Ctl 1 heard
  none of these.
- Link test 5->1 and 1->5: NO DIALTONE on the caller.

The failure therefore **follows the direction SN 12196 -> SN 1331** and does
not depend on which end is NT. Before the swap, 12196->1331 carried NT
frames; after it, it carried TE frames, and both failed. The 1331->12196
direction passes frames (with errors) either way. Ctl 1's receiver worked
against the PBX on 9 October, and SN 1331's NT transmit works to the
Multitech. That leaves SN 12196's port transmitters, or the pair that
carries 12196's transmit in each patch lead. Link 2 fails in the same
direction, which fits a fault common to SN 12196 or to how both leads were
made.

Restored: CFGLib was reloaded from the unmodified XML and both cards were
restarted. Ctl 1 is TE again and ctl 5 is NT. The status table matches the
pre-test state, and `bri-test-answerer` is active.
