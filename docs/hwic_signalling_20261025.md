# HWIC signalling investigation, 25 October 2026

Signalling remains unusable. Physical activation is present on all four
HWIC-to-Diva spans, but no successful bidirectional Q.921 establishment was
verified. Do not infer success from the Diva's Layer2 UP label alone.

Management access: SSH as scottcryan to eicon420.net.cryan.nz; privileged
operations use sudo. Cisco management is Telnet to toprouter.iot.cryan.nz.
Credentials are intentionally omitted.

## Tests and evidence

- Baseline Cisco 0/0/0 and 0/0/1 have established Layer 2. The HWIC
  0/2/0–3 interfaces have physical activation but Layer 2 is not activated.
- Baseline Eicon controllers 3, 4, 7 and 8 have zero valid received
  D-channel frames and nonzero receive framing/CRC errors.
- Tested Cisco 0/2/0 automatic negotiation without point-to-point setup,
  then fixed TEI 0 with point-to-point setup. Matched controller 3 runtime
  settings: automatic TEI false, one L2 link, permanent L2. No clean
  establishment resulted; good frames and corrupted frames both appeared.
- To rule out runtime settings requiring a firmware restart, staged
  controller 7 fixed TEI 0 and one L2 link in CFGLib, preserving its NT
  role. Set Cisco 0/2/3 to fixed TEI 0. Verified every B channel on the
  second card idle, then stopped/reset/reloaded only card SN 12196
  (controller 5 is its physical-card selector). SN 1331 stayed active.
- Controller 7 trace repeatedly receives SABME `00 01 7F` and sends UA
  `00 01 73`, with MDL-ERROR(F). Cisco remains AWAITING_ESTABLISHMENT,
  repeatedly transmits SABME and reports `User RX <- BAD FRAME(0x00017F)`.
  This resembles a returned SABME rather than the expected UA, but does
  not by itself prove the location or cause of the corruption/echo.
- Controller 7 eventually reports Layer2 UP while Cisco still fails.
  Its measured totals at one inspection were 20 received good frames,
  8 receive errors, and 28 transmitted frames. Cisco later showed
  29 received packets, 20 input errors (18 CRC), and 160 carrier transitions.
  These cumulative counters include deliberate test restarts.

The failed fixed-TEI exchange rules out automatic TEI negotiation as the
sole cause. Cable/termination, physical clocking, and NT interoperability
remain candidates; none is individually proved faulty.

## Restoration

Restored the original CFGLib XML and reloaded the idle second card again;
controller 7 reads automatic TEI true and eight L2 links. Restored controller
3 runtime values (automatic TEI true, eight links, L2 policy 1) and removed
the temporary Cisco static-TEI/negotiation settings. Saved divas_cfg.rc was
never changed, and Cisco startup-config was not overwritten. Disabled
Q.921 debugging. Both Eicon answer/gateway services remained active.

Host artifacts:

- `/var/tmp/hwic-signalling-before-20261025.xml`
- `/var/tmp/hwic-signalling-test7-20261025.xml`
- `/var/tmp/hwic-signalling-ctl7-20261025.txt`

## Next physical test

Swap just Cisco 0/2/3 to SN 12196 port 3 (controller 7) onto another
correctly wired ISDN crossover lead. Then repeat a bounded establishment
test with matching settings at both ends and compare frame-error deltas.
Leave the working 7900 and MultiTech cables alone. If the cable swap has
no effect, inspect S/T termination and isolate clocking/NT compatibility.

## Tool details

Use `/usr/lib/divas/divactrl mantool` directly for management variable
names containing spaces: the installed mantool shell wrapper uses `$*`
without quoting and splits those names. The same wrapper issue applies
to mlog arguments containing whitespace. Keep mlog stdin open during a
capture; EOF causes an unhelpful loop of `unknown command <>` messages.
The vendor CFGLib parser rejects ElementTree's self-closing empty tags;
preserve the original explicit `<bs></bs>` representation when staging XML.

## Follow-up: isolated multipoint and NT2 tests

On the renewed request to fix signalling, rechecked the current baseline:
the saved configuration XML still matches the earlier backup. Temporarily
shut Cisco 0/2/0–2, leaving only 0/2/3 active on the HWIC. Set controller
7 NT-2 false at runtime and Cisco 0/2/3 to multipoint with TEI negotiation
on power-up. Cisco transmitted TEI Identity Requests; during the Eicon
capture its good receive count stayed at 2 and its receive errors increased
from 7 to 8 (including one additional wrong-CRC event). No TEI assignment
or Layer 2 establishment occurred.

Also staged fixed TEI 0, one L2 link and NT2 disabled in firmware XML,
then reloaded only the verified-idle second card. The firmware normalises
NT2 back to true for this configuration, so explicitly disabled NT2 at
runtime after checking readback. The isolated fixed-TEI span continued
to show CRC/framing errors, Cisco BAD FRAME(0x00017F), Layer 1 inactive
events and AWAITING_ESTABLISHMENT. These tests do not establish a working
configuration. Restored the original CFGLib/card configuration and all
four Cisco HWIC interfaces afterward; no startup configuration was saved.

Additional host artifacts:

- `/var/tmp/hwic-multipoint-ctl7-20261025.txt`
- `/var/tmp/hwic-signalling-test7-nt2off-20261025.xml`

Dialogic's installation guide, printed page 27, explicitly requires the
crossover wiring and termination resistors for BRI NT/back-to-back use:
https://www.dialogic.com/~/media/manuals/diva_hw/203-195-17.pdf
The diagram specifies 100-ohm, 5% resistors. Correct termination has not
been physically verified on these spans. This is a concrete installation
requirement to check, not proof that termination alone explains the fault.

## Fresh retest after unchanged-cabling confirmation

Initially all four HWIC ports were physically active but Layer 2 was not
activated; a passive 20-second observation showed no frame/counter changes.
Confirmed all HWIC spans idle, then issued `clear interface` on 0/2/0–3
without changing configuration. Over the 35-second measurement window:

| Cisco | Eicon controller | New valid RX frames | New RX errors / CRC |
|---|---|---:|---:|
| 0/2/0 | 3 | 0 | 4 / 4 |
| 0/2/1 | 4 | 0 | 6 / 6 |
| 0/2/2 | 8 | 0 | 3 / 3 |
| 0/2/3 | 7 | 0 | 0 / 0 |

During the post-clear Cisco inspection, 0/2/0–2 awaited TEI assignment;
0/2/3 reported Layer 1 DEACTIVATED and Layer 2 not activated. Thus the
zero errors on controller 7 do not establish a pass: it exchanged no frames.
The working 0/0/0 and 0/0/1 spans remained established. Found the earlier
interface-scoped Q.921 diagnostic still enabled despite the global
`no debug isdn q921`; removed it with
`no debug isdn q921 interface BRI0/2/3`. `show debugging` then showed none.

## Cisco option matrix on request

Tested idle Cisco 0/2/1 against Eicon controller 4, leaving Eicon settings
unchanged. Each case included a local interface restart and approximately
12 seconds of observation; counters are deltas including that restart.

| Cisco setting | Good RX at Eicon | RX errors / CRC | Cisco result |
|---|---:|---:|---|
| Automatic TEI, multipoint, negotiation at power-up | 0 | 2 / 2 | ESTABLISH_AWAITING_TEI |
| Static TEI 0, point-to-point SETUP | 3 | 3 / 1 | AWAITING_ESTABLISHMENT |
| Automatic TEI, attempted 15000 ms T-Activate | 2 | 1 / 1 | Layer 2 not activated; timer setting rejected |
| Protocol-emulate network | 0 | 0 / 0 | Layer 1 deactivated, Layer 2 not activated |

The timer command is deprecated in favour of `isdn timer T-activate`,
but more importantly IOS reports `%T-activate is only supported for Net5
user side.` The attempted longer timer was therefore not an applied NET3
test. Network protocol role was accepted syntactically but did not create
a functioning physical NT interface; the peer remained NT, so this was
not a matched reversal of both endpoint roles.

Briefly enabled the supported `loopback` mode on 0/2/3. The interface
reported loopback set and spoofed up, while physical Layer 1 remained
deactivated and Layer 2 not activated. No bearer payload was generated,
so this is not a hardware loopback integrity pass. Removed loopback.

Restored 0/2/1 to its original running configuration and confirmed all BRI
configuration sections match baseline. No Eicon settings or Cisco startup
configuration were changed in this option matrix. Local structured results:
`/tmp/hwic-options-results.json` (on the workstation running the test).

## Further clock, transceiver and activation investigation

On the request to keep investigating, read board-control and configuration
registers without halting MIPS. PCI 02:00.0 has NT mask 0x0c and PCI 02:02.0
has 0x0f at BAR3+0x300: these match the intended physical roles. All
transceivers have TR_CONF0=0 and TR_CONF2=0x40, so the documented remote
line-loop bit TR_CONF2.RLP (bit 4) is clear. NT TR_MODE=2, TE TR_MODE=0.
Test\Loopback on controller 8 also reads zero.

The supported hidden management fields on controller 1 were tested with
L1SyncPortNo=1 (zero-based port 2, the working Cisco-connected TE) and
L1SyncOpMode=1, then restored to 0/0. During this bounded test neither
controller 3 nor 4 recorded new D frames; controller 4 remained physically
down. Cisco 0/0/0 and 0/0/1 stayed established. This is not a clock-sync pass.

Using the manufacturer's documented configuration bits, tested only idle
controller 8 on SN 12196, checking both B channels idle and the PCI serial
before each test. Each byte was restored in a finally block, and readback
confirmed the changed value during observation and the original afterward:

| Controller 8 configuration | Valid RX delta | RX errors / CRC delta |
|---|---:|---:|
| TR_CONF0.BUS=1 (fixed short-bus timing) | 0 | 1 / 1 |
| TR_CONF1.RPLL_ADJ=1, adaptive timing | 0 | 1 / 1 |
| RPLL_ADJ=1 plus fixed short-bus timing | 0 | 4 / 4 |

Original bytes are TR_CONF0=0x00 and TR_CONF1=0x03; RPLL_ADJ tests used
TR_CONF1=0x43. None established signalling. No undocumented/reserved bits
were changed. Manufacturer register documentation:
https://www.hkjdwchip.com/image/files/infineontechnologies-peb3086fv14-datasheets-6592.pdf
and the corresponding IPAC-X documentation:
https://media.digikey.com/pdf/Data%20Sheets/Infineon%20PDFs/IPAC-X.pdf

Also briefly disabled controller 8's documented transceiver D data path
TR_CR.EN_D (0xf8 -> 0x78), preserving B-channel enables and clock settings,
to look for returned Cisco frames independently of Eicon D-channel data.
The register remained 0x78 throughout the bounded test and was restored
to 0xf8. Cisco Layer 1 deactivated and produced no Q.921 traffic, so this
experiment does **not** resolve whether the earlier BAD FRAME is an echo.

Registered a temporary CAPI application listening on controller 8; LISTEN
CONF returned info 0. This did not activate signalling. Released it, then
originated one unrestricted digital CAPI call from controller 8 toward its
directly attached HWIC (called 8899, calling 7980). CONNECT CONF returned
PLCI 0x0508/info 0, followed by DISCONNECT IND reason 0x3301 (Layer 1
protocol error). No valid RX frames were recorded and Cisco Layer 1 stayed
deactivated. Closed the application to release the diagnostic call.

Cisco help exposes no `clear controller` command and `hw-module` offers
only ISM/PVDM, with no targeted HWIC reload. `test interfaces BRI...` is
available, but Cisco documents it as a factory IP/ICMP-loop test that does
not give reliable results on a live network. It was not run or represented
as a D-channel hardware qualification.

Finally checked all eight B channels on the second card idle and reloaded
only that card from unchanged CFGLib configuration to clear transient PHY
state. Verified controller 8 register bytes 0x30=0, 0x31=3, 0x32=0x40,
0x3a=2, 0x50=0xf8, the original NT masks, and first-card clock fields 0/0.
Both answerer/gateway services remained active. Restored temporary Cisco
TEI/negotiation options and removed the interface-scoped Q.921 debug.

No working HWIC signalling configuration has been found. The next decisive
comparison still needs a known-good, properly terminated physical span or
a port/cable substitution with the working VIC BRI hardware. Wiring,
termination, clocking and physical NT interoperability are not individually
proved faulty by the tests above.
