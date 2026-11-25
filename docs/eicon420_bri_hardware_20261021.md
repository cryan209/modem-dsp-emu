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
Ctl 1 worked against the Cisco 2900 BRI NT port on 9 October (`/var/tmp/dch1.txt` shows
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

### Multitech persistent driver and call tests (22 October 2026)

Installed persistently with the staged `~/isicom-port/install.sh`. The
module is the hardware-tested build (sha256 `223e9268…`, the same as
`isicom-diag/isicom-baseline.ko`). It went to `/lib/modules/6.12.101+deb13-amd64/extra/isicom.ko`
with `isi4608.bin` in `/lib/firmware`, `options isicom probe_only=0` in
`/etc/modprobe.d/isicom.conf`, and boot loading through
`/etc/modules-load.d/isicom.conf`. This is tied to this kernel; rebuild
after kernel updates. It has not been checked across a reboot.

Endpoint start-up is still unreliable. On each load only one TA pair
answers AT: ttyM2/3 on the first load, ttyM0/1 after a `modprobe -r`/`modprobe`.
ttyM4-7 answered.

The Multitech's own D-channel analyzer works: `AT>D99` on, `AT>D98` off,
`AT>D1` dump. The test script is `/var/tmp/mt_call.py`. The Diva answers
on ttyds6 bound to ctl 3 with number 7950. Results:

- At firmware load, ctl 3 receives a TEI Identity Request and assigns a TEI
  (107 on the first load, 82 on the reload). Ctl 4 has never received
  anything from the Multitech.
- On `ATD7950` from ttyM2 (PPP), ttyM0 (V.120) or ttyM1, the Multitech sends
  a fresh TEI Identity Request three times, 5-10 ms apart, then reports
  NO CARRIER at once. Neither ctl 3 nor ctl 4 receives any of these frames.
  Retransmissions 5-10 ms apart are not Q.921 retries (T202 is seconds).
  They are the transmitter aborting and restarting, which matches the
  Diva TE's `IPACX: ERROR D-TX xmit message repeat`.

Revised reading across all three spans: what SN 1331 *transmits* is
received, by SN 12196 (TE or NT) and by the Multitech (TEI assignment).
What is transmitted *towards* SN 1331 mostly does not arrive: from SN 12196
in either role, and from the Multitech after its first frame. SN 1331 as TE
also received its own SABME. That makes **SN 1331's receive path**
(own-transmit leakage, termination or line interface) a stronger suspect
than SN 12196's transmitters. It doesn't fit the 9 October PBX result,
where ctl 1 received cleanly, so it is not established. The test that
separates the two: move the Multitech to SN 12196 port 3 (ctl 7, NT P2MP)
and dial. If calls set up there, the fault is on SN 1331.

### Cabling confirmed by owner (22 October 2026)

Every BRI lead is an RJ45 crossover (pins 3-4 and 5-6 swapped), with **no
S/T termination anywhere**. The owner first described them as
straight-through, then corrected that. The crossover matches Dialogic's
back-to-back wiring: TE pins 3/6 (TX) go to NT pins 4/5 (RX), and TE pins
4/5 (RX) to NT pins 3/6 (TX). A Diva in NT mode keeps the TE pinout, which
is why the crossover is needed.

The missing termination fits everything above better than a faulty card. S0
drivers expect a terminated (about 50 ohm) load. Without it, INFO signals are
still detected and L1 reports Activated, but pulse shapes and levels are
wrong. D bits and the E echo then get corrupted, which causes the collision
aborts, the 5-10 ms transmit repeats, invalid frames, and the odd frame that
gets through. The 9 October result fits too. The peer then was a Cisco 2900
BRI port set to NT, and these leads worked with it. A network-side port
normally terminates its own end, though Cisco's documentation found online
doesn't confirm it for that card. Every failing link has a Diva or the
Multitech at both ends, and none of those terminate. The asymmetry towards
SN 1331 may just be its receiver being less tolerant of an unterminated line.

Termination is the cable's job. Dialogic's
[Diva Media Board Installation Guide 203-195-18](https://ftp.sangoma.com/diva/documentation/203-195-18.pdf)
("Connecting Diva BRI Media Boards in NT mode", page 25) says that for
back-to-back use the crossover cable must carry the termination resistors:
100 ohm, 5%, across each pair. It describes no jumpers on the boards. No
MultiTech documentation on termination turned up, so look at the card.

Next step: add a 100 ohm, 5% resistor across pins 3-6 and across 4-5 on each
lead. Check the Multitech leads use the same crossover. Then repeat the
D-channel captures and call tests before suspecting either card.

### Multitech endpoint silence and the card start-up window (22 October 2026)

The card's processor is busy for 3848 ms after the driver transfers control
to `isi4608.bin`: bit 0 of status register `0xe` (card free) stays low, then
rises once and stays up. This was the same on three traced loads. The old
driver registered `/dev/ttyM*` immediately, so an open inside that window
hit `isicom: Failed to lock Card (0xd280)`, and the driver silently dropped
that port's DTR command. One test opened ttyM6 1.2 s after load: it returned
garbage, then echoed `AT\r` without `OK`, then went silent until the next
reload. A 15 s wait before opening gave a clean ID and five good reopens.

`~/isicom-port/isicom.c` now waits for the free bit, sleeping for up to 10 s,
after the control transfer and before marking the firmware loaded and
registering the ports (`ready.patch`). It is installed in
`/lib/modules/6.12.101+deb13-amd64/extra/isicom.ko` (see below for the current
hash). The previous module is kept as `isicom.ko.pre-ready-installed`, and the previous
source as `isicom.c.pre-ready`. With the patch, three loads opening ttyM6 as
soon as `insmod` returned logged no lock failures, and ttyM6 answered every
time.

**This does not fix the silent ports.** Endpoints that are dead after a load
stay dead until the next reload, with the old module too. Tests at 13:15-13:41
pinned down how:

- **Fixed for the load once it happens, but set off by the first open.** With
  one port at a time and 10 s gaps, a dead port stays dead on later passes,
  and holding DTR across closes (HUPCL cleared) changed nothing. Reversing the
  open order (7 down to 0) moves the failures towards the ports opened first:
  ttyM7 died for the first time in about 30 loads, and ttyM4/5 died in 3 of 5.
  Waiting 60 s after load before the first open did not help. (An earlier
  version of this note said the failure was decided at reset; the order test
  disproves that.)
- **Varies from one reset to the next.** Dead sets over successive loads, opening 0 to 7:
  `0,1,6`; `6`; none; `0,2,3,6`; `0,1`; `0`; `2,3,6`; `0,1,2,3,6`; none;
  `0,1,4,5`; `2,3`; none. About 70% of loads lose at least one port. TA
  ports (ttyM0-3) fail most often, often as pairs 0/1 and 2/3. ttyM6 is the
  modem that fails most; ttyM4/5 failed twice, as a pair, and ttyM7 never.
  The pairs suggest two endpoints sharing a chip.
- **Not the line.** `divactrl dchannel -c 3` captured nothing across any of
  these loads, so the card sent no frames to the Diva NT. The SIP gateway's
  last call was at 11:46.
- **Not the reset delay.** A test build with 5 s instead of 1 s after the
  reset pulse still had dead ports on 3 of 5 loads.
- For a dead port, `TIOCOUTQ` drains within 250 ms of writing `AT\r` and the
  modem signals match a working port. The bytes reach the card, and the
  endpoint inside it never answers.

The vendor reset ioctl doesn't recover a dead port; only a reload does.
`/var/tmp/mt_clean.py` reloads until all eight answer, which usually takes 1-7
tries. The loads are in `/var/tmp/mt-seq-*.log`, and the scripts are
`/var/tmp/mt_seq.py` and `/var/tmp/mt_dist.py`. Next steps: inspect the board
for a shared component behind the failing pairs, or read MultiTech's own
3.05 loader (Fedora 7 `isicom-3.05` SRPM, not on the host) for a start-up step
the kernel loader omits.

#### What a dead port does (14:00-14:30)

The user mapped the ports: ttyM0 and ttyM4 share a B channel (likewise 1/5,
2/6, 3/7), and ttyM0, 1, 4, 5 are on one BRI port (A), with 2, 3, 6, 7 on the
other (B). Sharing a B channel does not predict failures. The BRI ports do:
on A the failures nest (ttyM0, then 0+1, then all four), and on B ttyM2+3 fail
together while ttyM6 fails on its own.

- **Not silent.** An interrupt trace showed a "dead" ttyM0 echoing its first
  `AT\r` (one 3-byte packet) and then sending nothing more, and a dead ttyM6
  sending fragments of 3, 1 and 3 bytes. A working port sends one 9-byte
  `AT\r\r\nOK\r\n` packet. The endpoint takes the first command and then its
  output stalls.
- **The card sends no interrupts at all during its 3.85 s start-up** (5
  loads), so ignored or half-read start-up packets are not the cause. The
  firmware upload checks every record by reading it back. The only difference
  from upstream is the byte after the image's last, odd-length record at
  `0x96e0`, which upstream filled with the next byte in memory and we fill
  with zero.
- **Toggling RTS sometimes releases a stall.** On one load, toggling RTS on a
  stalled ttyM0 brought back ttyM0 and also ttyM1, 4 and 5, its whole BRI port.
  Across 6 more loads it brought back only 2 of 9 stalled ports, and never
  ttyM6. Toggling RTS straight after open, 0.2 s or 2 s before the first
  command, does not prevent stalls.
- **Not the doubled DTR on open.** `config_port` raises DTR (`0x0504`) and the
  tty layer then sends DTR+RTS (`0x0f04`). A build that sends only RTS
  (`0x0a04`) in the second command was no better: one clean load in eight.
- XON, break, CRTSCTS, a baud change, a DTR toggle and the vendor reset ioctl
  did not revive the dead ttyM6.

Whether this is the driver or the card firmware is still open. The first open
sets it off, which points at the open sequence: kill tx/rx, baud, flow
control, DTR, DTR+RTS, sent back to back once the card reports free. Upstream
sends the same sequence. MultiTech's own 3.05 driver is compared in the next
section. Scripts: `/var/tmp/mt_irq.py`, `mt_order.py`, `mt_recover.py`,
`mt_stall.py`.

#### Comparison with MultiTech's own 3.05 driver (14:30-14:45)

Source: the Fedora 7 SRPM
[`isicom-3.05-18.2.2.src.rpm`](https://archives.fedoraproject.org/pub/archive/fedora/linux/releases/7/Everything/source/SRPMS/isicom-3.05-18.2.2.src.rpm),
which contains MultiTech's `L305_22X_24X.tar.bz2`: `isicom.c`/`isicom.h` dated
6 June 2001, `firmld.c`, `ISIResetModem.c` and the same five firmware files.
It was extracted and read only, never run.

| Step | Vendor 3.05 | Ours (upstream base) |
|---|---|---|
| Card reset | read `0x8`, 10 ms, write `0x8`, 1 s, read signature and port count | same |
| Firmware upload and readback | same commands (`0xf0`, `0xf1`, `0xf2` to `0x800:0`) | same, plus the wait for the card to be free |
| Lock and wait | `lock_card` 100 x 1 ms; `WaitTillCardIsFree` up to 6,000,000 polls | 100 ms in both |
| Open: `config_port` | DTR **and RTS** (`0x0f04`) before baud and flow ("anil.s on 06-06-01") | DTR only (`0x0504`) |
| Open: after config | `set_signal_mask(0xff)`, command `0x07`: report EIA changes | **never sent** |
| Close | drop DTR+RTS only if HUPCL; `set_signal_mask(0x00)` | drop DTR+RTS if HUPCL; no mask |
| `isicom_tx` | locks the card **before each port's packet** | upstream did not; our fix now does |
| Interrupts | same, including not draining packets for unopened ports | same |
| Modem reset | `0x3004`, 200 ms, `0x2004` (ioctl on `/dev/isictl`) | same timing (`0x4d14` on the tty) |

The only loader difference is the byte after the last, odd-length record. The
vendor sends whole words from a reused buffer, so it writes `0xef` (left over
from record `0x96d0`) at `0x96e1`, where we write `0x00`. Byte `0x96e0` is
`0xcf`, an x86 `IRET` and the last instruction of the 80186 code, so `0x96e1`
is past the end of the program.

A trial build with the vendor's open and close (`0x0f04` in `config_port`,
mask `0xff` on open and `0x00` on close; source kept as
`~/isicom-port/isicom.c.vendor-open-trial`) did **not** reduce dead ports:
one clean load in eight, the same as before. With the mask on, the card
reports EIA lines. A dead ttyM6 showed CTS and DSR up like the working
modems, so the endpoint signals ready and still does not answer.

So the remaining host-side differences are the two in the open and close path,
and adopting them does not stop the dead endpoints. That makes a host-driver cause unlikely. What remains is inside
the card: its firmware or hardware, set off by the first open. The vendor's
`ISIResetModem` utility, which says it resets an individual internal
modem/ISDN port without rebooting, suggests endpoints hung in normal use on
their driver too.

Both vendor steps are now in the installed driver anyway (14:45), because
without the signal mask the card never reports DCD, so on modem calls the
driver cannot see carrier come up or drop. Checked on all eight ports: on
open each reports CTS (and DSR, except ttyM1 before its first command). `AT&C0`
raises CD and `AT&C1` drops it again; neither was saved with `AT&W`. A carrier
change during a real call, and hangup on carrier loss with CLOCAL off, have
not been tested.

#### Concurrent transmit lost packets (fixed)

A separate bug, in upstream's `isicom_tx` too. With all eight ports alive and
`AT\r` written to each in the same 10 ms tick, only one port got anything
back, not even an echo. The tick writes every pending port's packet straight
after the previous doorbell, but the card is busy for 20-40 us taking each
packet (measured: bit 0 of `0xe` drops at once and returns after about 9 polls
of 2 us), and a packet written in that time is lost. `isicom_tx` now waits up
to 500 us for the card after each doorbell and leaves the remaining ports to
the next tick if it does not come back. With this, all eight ports answered
every simultaneous round. The earlier "ports answer one at a time, 5-20 s
after opening" was this bug, not slow endpoints. The earlier attempt at this
(wait before each packet) was tested on a load with three dead endpoints,
which hid the result.

All changes are in `~/isicom-port/ready.patch` against `isicom.c.pre-ready`:
the start-up wait, this transmit fix, and the vendor's DTR+RTS and signal mask
on open and close. The installed module is sha256 `339ea72a...`. It is loaded
now with all eight ports answering, including simultaneous writes on all of
them.

### New lead on ctl 3, and the first clean error-counter measurement (23 October 2026)

The owner made a second lead and fitted it between SN 1331 ctl 3 (NT) and the
Multitech. Host had been up 25 min; all eight `/dev/ttyM*` answered `ATE1V1`
on the first probe, so no `isicom` reload was needed.

Method, non-disruptive first: `divactrl mantool -c N -b -r"Statistics\D-Layer1"`
exposes per-controller D-channel framing counters, and
`-e"Statistics\D-Layer1\Clear Values"` zeroes them. Cleared ctl 3 and ctl 4,
then ran `mt_call.py --controller 3 --mt-tty /dev/ttyM0 --number 7950`.

Multitech side before dialling: `ATL8` reported `Physical Layer: LINE ACTIVE`,
`Data-Link Layer: LINE NOT READY`. On `ATD7950` it returned NO CARRIER, and its
own analyzer (`AT>D99`) showed it transmitting three
`Q.921 SAPI=63, TEI=127, UI - Identity Request` frames and receiving nothing.

Diva counters afterwards, ctl 3:

```
R-Frames = 0      R-Errors = 2
R-ErrInvalidFrame = 2   R-ErrAbortedFrame = 2   R-ErrWrongCRCFrame = 2
X-Frames = 0
```

ctl 4 stayed all-zero, which also identifies the new lead as the ctl 3 span.

This is the measurement that was missing. **Not one valid D-channel frame has
ever been received on this span** — every frame the Multitech sends arrives
invalid, aborted and CRC-bad at once, and the Diva therefore transmits nothing
(`X-Frames = 0`). The earlier reading in this document, that the Diva's TEI
assignment fails to reach the Multitech, is the wrong way round: the Identity
Request never arrives intact, so no assignment is ever generated.

L1 activating while every frame fails CRC means the line synchronises but the
bits are corrupted, which narrows the cause to signal integrity rather than
pinout — a crossed or straight-through error gives no L1 or no received frames
at all, not framing errors. That leaves, in order: missing termination (the
span is terminated at the Multitech end only, if the resistors the owner can
see on the ISIHP-2S are the S/T terminators), a split pair in the new lead
(continuity correct, pairs not kept twisted together), and level or pulse-shape
mismatch.

Next: fit 100 ohm 5% across pins 3-6 and across 4-5 at the **Diva end only** of
this lead, confirm with a meter that the Multitech jack already reads ~100 ohm
across each pair, clear the counters and repeat. The counters now give a pass
or fail in one call attempt, so the resistors are a one-variable test.

Roles verified live on 23 October, after the reboot, from
`divactrl mantool -c N -b -r"Config"`: the `NT` flag is TRUE on ctl 3-8 and
FALSE on ctl 1-2, so ctl 3 is still NT as the table says and the NT setting
survived the restart. Note the `Build` string reads `TE_DMLT` on every
controller — that is the firmware image name, not the port role, so do not read
it as TE. `Config\Layer1\Clock Config` is 0x0000 on ctl 3.

Since ctl 3 is NT and the Multitech TA is a TE, this span wants a
**straight-through** lead; the 3-4/5-6 crossover is only for the Diva-to-Diva
spans, where both ends are the same role. Worth confirming which of the two the
new lead is before buying parts, though L1 activating argues the orientation is
already right.

With no resistors to hand, the free tests in preference order: confirm the lead
is straight-through for this NT-TE span; swap in a factory-made straight patch
lead, which rules out a split pair without any crimping; and make the span as
short as possible, since reflections on a 30 cm lead land inside the bit period
and matter far less than on a 2-3 m one. If termination is still wanted
afterwards, 2 x 220 ohm in parallel gives 110 ohm and 2 x 47 ohm in series
gives 94 ohm, both close enough, or harvest 100 ohm parts (marked 1000 or 101)
off a dead NIC.

Correction, same day: the owner confirms the new ctl 3 lead is a crossover, and
L1 still activates and corrupt frames still arrive. That settles the pinout
question in the opposite direction to the paragraph above. The 4BRI's RJ45 is
wired TE-style in hardware and the NT role is firmware and config only, so the
card keeps transmitting on 4/5 while acting as NT, and an NT-to-TE span on this
board needs the crossover after all. Do not swap this span to a straight lead;
the measurement already proves the orientation is right, since a genuine
transmitter-to-transmitter mismatch would leave L1 Down with nothing received.

Pinout is therefore eliminated by measurement rather than by argument, and the
remaining causes are a split pair in the lead, missing termination, and levels.
Split pair is the one to check first because it costs nothing: at **both**
plugs, pins 3 and 6 must be the two conductors of one twisted pair and pins 4
and 5 the two conductors of the other. In T568B that is 3/6 = white-green and
green with 4/5 = blue and white-blue at the straight end, and at the crossed end
3/6 = blue and white-blue with 4/5 = white-green and green. Wiring 3 to 4 and 6
to 5 from conductors that belong to different pairs gives correct continuity on
a tester, a split pair on the line, and exactly the framing errors measured
here.

### 282 frames, none good, and the direction that works (23 October 2026)

Owner confirms both plugs match the pin-by-pin colouring, so pairs are intact
and the split-pair hypothesis is out. Three further call attempts from
/dev/ttyM0 with the counters cleared first:

```
ctl 3   R-Frames = 0        R-Bytes = 0
        R-Errors = 282      R-ErrInvalidFrame = 282
        R-ErrAbortedFrame = 282   R-ErrWrongCRCFrame = 282
        X-Frames = 0
```

A 282-frame sample with not one good frame changes the character of the
diagnosis. Marginal signal integrity gives a non-zero pass rate; this is
categorical. The three error counters moving in lockstep means one bad-frame
event raises all three, so it is 282 events, not 846.

The asymmetry is the useful part. The Multitech reports
`Physical Layer: LINE ACTIVE`, which it can only do by synchronising to the
Diva's INFO2/INFO4, so the **Diva-to-Multitech pair carries L1 cleanly**. The
Diva, meanwhile, corrupts every frame it receives. One direction is fine and
the other fails totally, on two separate pairs of the same lead.

That maps directly onto which end holds a terminator. If the resistors the
owner can see on the ISIHP-2S are its S/T terminators, then this span is matched
at the Multitech end and open at the Diva end — and the receiver sitting at the
open, unterminated end is precisely the one failing. The Multitech receives well
because it sits at the matched end.

Correction to the previous entry: shortening the lead was listed as a free test,
and that reasoning does not hold here. The S/T line rate is 192 kbit/s, a bit
period of about 5.2 us, while a 2 m lead has a round trip near 20 ns — four
hundredths of one percent of a bit. Reflection *timing* cannot be the mechanism
at this rate. The 100 ohm terminators are there to present the correct load
impedance so the transmitter develops its specified voltage into a matched line;
an open end leaves the receiver looking at roughly double amplitude. That is an
overdrive problem, and a shorter cable does not help it.

So termination at the Diva end is the live hypothesis and the test needs a
resistor. Routes that need no purchase: harvest 100 ohm parts from scrap (SMD
marked `1000` or `101`, through-hole brown-black-brown); or exploit the fact
that an S/T bus is a parallel bus and the Cisco 2900's BRI port terminates its
own end, so paralleling that port onto the Diva end of this span through an
RJ45 splitter supplies the missing 100 ohm without any soldering. Outstanding
free measurement that decides the whole question: meter the Multitech jack
across pins 3-6 and 4-5 with the card powered off and see whether it reads
~100 ohm.

### Diva to Diva measured the same way, and it is a different failure (23 October 2026)

Span states before testing: ctl 1 (TE) and ctl 5 (NT) both L1 Activated; ctl 2
and ctl 6 both L1 **Down**, so the ports 2 lead is not passing anything and
only the 1-5 span is live. Counters cleared on ctl 1 and ctl 5, then left idle
for 120 s while the two ends exchanged Q.921 establishment attempts:

```
ctl 1 (TE)   X-Frames 78  X-Bytes 240   R-Frames 26  R-Bytes 82
             R-Errors 41  Invalid 39  Aborted 41  WrongCRC 39
ctl 5 (NT)   X-Frames 64  X-Bytes 199   R-Frames 24  R-Bytes 74
             R-Errors 16  Invalid 12  Aborted 16  WrongCRC 12
```

**Good frames do get through here.** ctl 1 received 26 good out of 67 arrivals,
ctl 5 24 good out of 40. Two further things fall out of the transmit totals: of
the 78 frames ctl 1 sent, ctl 5 accounted for only 40, so 38 vanished without
even registering as errors — corrupted past the point where L1 could frame
them at all. And the error counters are not locked together here (Invalid 39
against Aborted 41), which corrects the lockstep reading from the smaller
Multitech sample.

With 40 to 60 percent of frames lost, Q.921 cannot complete SABME/UA reliably,
which is exactly the stuck asymmetry seen all along: ctl 5 sits at Layer2 UP
while ctl 1 sits at Layer2Connecting. `eicon_bri_link_test.py 1 5` then fails
with NO DIALTONE from the caller, because the originating TE's L2 never
establishes.

The comparison is the result:

| Span | Good frames received | Character |
|---|---|---|
| Diva ctl 1 to Diva ctl 5 | 26/67 and 24/40 | marginal, 40-60% pass |
| Diva ctl 3 to Multitech | 0/282 | total, never a single frame |

Missing termination fits the Diva-to-Diva spans well: the line is degraded but
functional, which is what an unmatched load does. It does not fit the Multitech
span, where nothing has ever got through. So there are probably two faults
here, not one, and fitting 100 ohm is likely to bring the Diva-to-Diva spans up
while leaving the Multitech span dead. Test the Diva-to-Diva span first when
resistors appear — it is the one close to working, and it needs no Multitech
driver in the loop.

The ctl 2 to ctl 6 lead, L1 Down at both ends, is a separate and simpler
problem worth checking while the plugs are out.

### Why ctl 2 and ctl 6 are L1 Down: an open path (23 October 2026)

Config first, to rule it out: ctl 2 matches ctl 1 exactly (`NT` FALSE, `NT-2`
TRUE, `L2LinkCount` 1, `BChannelCount` 2) and ctl 6 matches ctl 5. So the dead
span is configured identically to the span that does carry frames.
`/proc/net/eicon/adapter{2,6}/dynamic_l1_down` is 0, so nothing has
administratively deactivated L1.

Cleared the counters on both and forced an activation attempt with
`eicon_bri_link_test.py 2 6`. It failed NO DIALTONE, and afterwards:

```
ctl 2   X-Frames 0  R-Frames 0  R-Errors 0  Aborted 0
ctl 6   X-Frames 0  R-Frames 0  R-Errors 0  Aborted 0
L1 Down / L2 Idle at both ends (L2 dropped from Connecting to Idle)
```

Nothing at all, in either direction — not one received frame and not one error.
`Statistics\Layer1\PeakProc. Load` is 0 on ctl 2 and ctl 6 against 3 on ctl 1
and ctl 5, so those L1 processors have never had anything to do.

This is a different signature again, and the cleanest of the three. A signal
that arrives degraded raises error counters; the ctl 1-5 span does that and the
Multitech span does it 282 times. An error count of exactly zero with L1 Down
means **no signal is arriving at all** — an open path, not a corrupted one.
The known-unplugged ctl 7 and ctl 8 read identically (Down, Idle, zeroes), so
the ctl 2-6 span is electrically indistinguishable from having no cable in it.

X-Frames 0 is expected here and is not evidence of a dead transmitter: a TE
emits INFO1 at L1 and sends no D-channel frames until L1 activates, so there is
nothing for the frame counter to count.

Software cannot take this further; the remaining candidates are a plug not
seated, a broken conductor, or a dead port on either card. The decisive move is
one swap: put the ctl 2-6 lead onto the ctl 1-5 ports. If that span goes Down,
the lead is at fault; if it still passes its usual 40-60 percent, the lead is
good and a port is at fault. Reseating both plugs is worth doing first since
the leads have been handled today.

`Test\Loopback` is writable per controller in mantool and would separate port
from cable without touching the hardware, but ctl 2 shares the SN 1331 card
with the live ctl 1 span and the Multitech span on ctl 3, and the value
semantics are undocumented here, so it has not been tried.

Three distinct signatures now exist on this box, which is worth keeping
straight:

| Span | L1 | Received | Reading |
|---|---|---|---|
| ctl 1 to ctl 5 | Activated | 26/67 good, errors | degraded line |
| ctl 3 to Multitech | Activated | 0/282 good, 282 errors | signal arrives, never decodes |
| ctl 2 to ctl 6 | Down | nothing, no errors | open path |

### Why there are so many errors (23 October 2026)

Owner confirms ctl 2-6 is simply disconnected, so the open-path reading was
right and that span is closed out.

Three things sharpen the error question on the two live spans.

**It is not ambient noise.** Cleared ctl 3 and left it 120 s with the Multitech
idle and nothing dialling: every counter stayed at 0, including R-Errors. The
receiver is quiet when the line is quiet, so a ground loop, mains hum or
crosstalk from elsewhere in the case is not manufacturing frames. Errors appear
only when the peer actually transmits, which means they are corrupted real
frames, not noise framed as frames. A crosstalk-swamping theory was tested here
and failed.

**The raw totals overstate the number of events by roughly three.** On ctl 1 the
counts were Invalid 39, Aborted 41, WrongCRC 39, and on ctl 5 12, 16, 12. One
corruption trips all three at once, and for a good reason: once the receiver
loses level or bit sync part-way through a frame, the closing flag is not where
it should be (invalid), the resulting bit soup contains a run of seven or more
ones (abort sequence), and the FCS cannot match (CRC). So ctl 1 saw about 41
bad-frame events in 120 s, not 119. Similarly, ctl 1 logged 26 good plus 41 bad
= 67 received events while ctl 5 transmitted only 64 frames; the excess is one
frame fragmenting into more than one error event after a mid-frame resync, not
phantom frames.

**The severity difference between the two spans is the transmitter, not the
cable.** The same Diva receiver on SN 1331 manages 40-60 percent good frames
when a Diva is transmitting, and 0 of 282 when the Multitech is transmitting,
on leads that are wired alike and unterminated alike. That reframes the earlier
guess in this document that SN 1331's receiver is simply less tolerant: the
receiver is the constant in both measurements, so the Multitech's transmit
level or pulse shape is the extra variable on that span. It is consistent with
the Multitech being the one device here that may already have its own
termination fitted, and therefore the one whose driver is working into a
different load than the Divas are.

So the error mechanism stands as this document already had it — every span is
run outside I.430's load conditions, the S0 driver's level is specified into
about 50 ohm and sees 100 ohm or an open line, and a pseudo-ternary three-level
code loses its framing logic when the levels are wrong. What is new is that the
Multitech span has a second problem on top of that, and that the Diva-to-Diva
span is the one to fix first.

One nuance for later: at 50 percent independent per-frame loss, a two-frame
SABME/UA exchange should still succeed about a quarter of the time and L2 would
establish within a few retries. It does not — ctl 5 sits at Layer2 UP while
ctl 1 sits at Layer2Connecting — so the loss is probably bursty or correlated
rather than independent, or one direction stays broken for long stretches.

### Is the Multitech configured correctly, and is termination or level settable? (23 October 2026)

Probed from /dev/ttyM1 with `/var/tmp/mt_probe.py` (reads only; takes the port
from `MTPORT`). Note /dev/ttyM0 was unusable for this: it still had the D-channel
analyzer enabled from the earlier call test and floods every command with a
replay of its stored buffer, all stamped 00:27:30, so `AT>D98` and even `ATZ`
never got through. Use another endpoint rather than fighting it.

Card: `ATI2` = ISIHP-TA, `ATI1` = firmware 4.22, `ATI` = 247, `ATI3` lists all
eight ports. `AT&V`/`AT&V1` dump the S-registers per port. There are no help
screens — `AT$`, `AT!$`, `AT&$`, `AT*$`, `AT%$` all return ERROR.

The complete ISDN parameter set this TA exposes, from `AT!L`:

```
Switch Type:  NET3 (A-law)
Data DN/MSN 1,2 and Modem DN/MSN 1,2:  [empty]
Data TEI:  AUTO_TEI        Voice TEI:  AUTO_TEI
Data Protocol:  PPP (V.120 on ttyM0)
Auto Protocol Detection:  Detect
```

Eight items, and **not one of them is a termination, transmit level or
bus-length setting**. So there is nothing to set in software on this card: no
short/long passive bus option, no level adjustment, no termination toggle. If
termination is switchable on the ISIHP-2S it is a hardware jumper or DIP block,
which is where to look — typically a two-position block silkscreened TERM or
100R beside each RJ45. A pair of fixed resistors with no jumper beside them
means it is permanent.

The signalling configuration does pair up correctly against the Diva, checked
on ctl 3:

| Setting | Multitech | Diva ctl 3 |
|---|---|---|
| Protocol | NET3 (A-law) | ETSI-DSS1(01), E-DSS1 (EURO) |
| TEI | AUTO_TEI | AutomaticTEI TRUE, TEI 0 |
| Role | TE | NT TRUE |

ETSI both ends, dynamic TEI both ends, NT facing TE. No mismatch.

And configuration cannot be the cause of what we measured, for a reason worth
recording: **a config mismatch produces clean frames that get rejected, so the
counters would read R-Frames climbing with R-Errors at zero.** The measurement
is the exact inverse — 0 good frames and 282 errors. HDLC framing sits below
the protocol layer, so the card cannot even assemble a valid frame, let alone
disagree about its contents. Switch type, TEI mode and B-channel protocol are
all ruled out by the shape of the counters alone.

One thing to fix later, not now: all four DN/MSN fields are empty, which will
matter for call acceptance once L2 establishes, but nothing reaches that stage
while the D channel is failing.

### What the MultiTech documentation says about termination (23 October 2026)

Searched for the ISIHP hardware documentation. MultiTech's current site keeps
nothing for this generation: a Wayback index query over multitech.com,
www.multitech.com and ftp.multitech.com for any URL containing ISIHP, ISIHI or
hybrid returns no hits at all, and web.archive.org is not reachable through
WebFetch here, so the CDX API has to be queried with curl.

What does exist are third-party copies of the *MultiModemISI Hybrid Series User
Guide* (models ISIHP-2S/2U/4S/4U/4SD, 155 pages) and the matching Quick Start
Guide, plus the ISIHI-2S/2U guide for the ISA sibling. Chapter 2, the hardware
chapter, runs to computer requirements, shipping contents, telecom safety
warnings, the installation procedure and LED indicators, and ends there.

Two statements settle the question as far as documentation can:

- The ISIHI guide says explicitly, of the PCI card: "Unlike the ISIHI, the
  ISIHP has no DIP switch for I/O addresses and no jumper to determine the IRQ
  code." The ISA ISIHI has a base-address DIP switch on the endplate and an IRQ
  jumper block on the component side; the ISIHP has neither.
- **Termination is not mentioned anywhere** in either guide — no terminating
  resistors, no 100 ohm, no bus length, no short-passive-bus option, no
  termination jumper or switch, and no RJ-45 pinout table. The documented
  topology is simply a TE connected to the S interface of an NT1 with the
  supplied RJ-45 cord, the arrangement in which the network side terminates.

So there is no switchable termination to find, and no documentation that any is
fitted either. Whatever resistors are visible on the card are undocumented, and
the meter across pins 3-6 and 4-5 remains the only way to establish whether the
Multitech end is terminated. Note also that a TE does not normally terminate an
S bus — termination belongs to the NT1 and to the far end of the bus — so the
prior assumption that the ISIHP-2S supplies termination should be treated as
unproven rather than likely.

Sources: the Hybrid Series user guide and quick start on ManualsLib
(manual ids 108438 and 297069) and ManualsDir (114694), and the ISIHI-2S guide
on ManualZilla (doc 7319183), which is where the no-DIP-switch, no-jumper
statement appears.

### Schematic and FCC search, and a correction to the meter test (23 October 2026)

No board-level documentation exists to find, and there is a structural reason:
an internal PCI card with no intentional radiator needs only a Part 15
declaration of conformity, which is never filed publicly, and its telecom
approval is a Part 68 registration, which carries no schematics and no internal
photos. So there is no FCC filing to retrieve. Searches of fccid.io and
fcc.report for the ISIHP turn up nothing; the only Multi-Tech grant that
surfaces (AU792U01E04510) is a 2001 wireless router. The TH99 hardware database
entry that search engines offer for Multi-Tech ISDN is the MultiMobile
MT128NTZLX, a different, U-interface PCMCIA card, with no chip or jumper data.

On-host routes are exhausted too. `lspci -vv` gives only the bus interface:
`10b5:2053` is a PLX Technology bridge, subsystem `2053:1000`, two I/O regions
and a 128-byte memory window — nothing about the line side. `/lib/firmware/
isi4608.bin` is 7325 bytes and contains no readable strings, being the serial
boot image rather than the ISDN stack, which lives in the card's own flash.

**Correction, and it matters: the DC meter test recommended in the two entries
above will not answer the question.** An S/T port is transformer-coupled, so
the two pins of each pair at the jack go to a transformer winding whose DC
resistance is typically one to five ohms. A 100 ohm terminator sits in parallel
with that winding, so the meter reads the winding either way and the terminator
barely shifts the result. A few ohms across pins 3-6 proves only that a
transformer is present. Reading ~100 ohm would in fact suggest the opposite of
what was assumed earlier.

What does settle it:

- Visual tracing, not resistance: is there a resistor bridging the two
  conductors of one pair *at the jack*? Be careful here, because a TE front end
  normally has other resistors in the same area that are not termination —
  series protection parts, and the transmitter's source-impedance setting pair.
  Resistors near the jack are not evidence of termination by themselves.
- Or lift one end of a suspect resistor and measure it out of circuit.

The pragmatic alternative is to stop trying to establish it and let the
counters answer instead: **fit 100 ohm at the Diva end and re-measure.** If the
Multitech is already terminated, the span becomes correctly matched; if it is
not, one terminator still beats none. Either way the D-Layer1 counters report
the result in a single call attempt, which is a better experiment than any
measurement of the Multitech jack.

If board-level certainty is still wanted, the one remaining lead is the S/T
transceiver's own part number, read off the card next to the RJ45s — a Siemens
or Infineon PEB/PSB 2081 or 2186 class part, or an AMD, Level One or Lucent
equivalent. Those datasheets publish a reference TE front end, which states
whether termination belongs on the card or in the installation.
