# TopRouter VIC2 BRI provisioning, 3 November 2026

TopRouter (Cisco 2911, IOS 15.7(3)M8) now detects three VIC2-2BRI-NT/TE
cards in subslots 0, 1 and 2, plus VIC3-4FXS/DID and PVDM3-32.

| Router port | Number | Far end / evidence |
|---|---|---|
| BRI0/0/0 | 7900 | Eicon controller 2, existing |
| BRI0/0/1 | 7910 | MultiTech first BRI, existing PPP listeners ttyM4/5 |
| BRI0/1/0 | 7920 | Eicon controller 8, SN 12196 port 4 |
| BRI0/1/1 | 7930 | Eicon controller 7, SN 12196 port 3 |
| BRI0/2/0 | 7940 | Unconfirmed; owner suspects MultiTech; unanswered tests |
| BRI0/2/1 | 7950 | Eicon controller 4, SN 1331 port 4 |

New router interfaces accept incoming voice, send sending-complete, and
skip send-ID verification; their existing user-side roles were preserved.
Voice ports use A-law, NZ tones, no echo cancellation and no VAD.
New POTS dial peers have exact number patterns, DID, all digits forwarded,
and huntstop to prevent failed calls looping back to Asterisk. Saved with
write memory; startup-config was checked.

Asterisk root SSH access uses asterisk.net.cryan.nz. Explicit routes for
7900–7950 are in /etc/asterisk/extensions.conf, default context. New numbers
7920–7950 use the new top router endpoint named `toprouter-bri`, an A-law-only
outgoing PJSIP endpoint with aors=6501 and direct_media=false. Existing Cisco
trunk identity remains unchanged. Existing 7900 and 7910 use endpoint 6501.
Dialplan and PJSIP were reloaded. Backups:

- /etc/asterisk/extensions.conf.before-bri-lines-20261103-213529
- pjsip.conf.before-bri-lines-<epoch> on Asterisk

Eicon420 now runs an enabled, separate bri-new-spans.service using the
a copy of /usr/local/sbin/bri_test_answerer.py named
/usr/local/sbin/bri_new_spans_answerer.py and
/etc/bri-test/new-spans.json. Six unused tty devices were allocated:
ttyds10/11 controller 4 number 7950, ttyds12/13 controller 7 number 7930,
and ttyds14/15 controller 8 number 7920. Existing bri-test-answerer,
diva-sip-gateway and MultiTech PPP services were not restarted.

Initial signalling was established on all six router spans, but absent
Eicon listeners caused call rejections. After listeners were enabled,
modem calls still produced corrupted data with the router on its internal
backplane clock. With explicit owner approval, configured
`network-clock-select 1 BRI 0/1/0`, reset that interface as IOS required,
and verified BRI0/1/0 GOOD and selected. This is saved in startup-config.

After clock selection:

- 7950 direct router call returned readable menu and echo data.
- Through Asterisk, caller controller 2 dialled 17920 and 17930 (Cisco
  sends the leading-1 pattern to Asterisk, which strips the leading 1 and
  routes back to the new line). Both returned readable menu/echo lines,
  completed at about 24 seconds, and hung up normally. Listener logs show
  V34/LAPM/V42BIS 31200 TX / 28800 RX.
- The equivalent 17950 call from controller 2 failed NO CARRIER at 60 s.
  Thus 7950 is not yet verified through Asterisk; direct success alone is
  insufficient to claim it ready. A follow-up from controller 8 connected at 21600 and returned a readable
  menu, but did not complete echo or clean hangup. A separate listener copy
  now allows modulation configuration; only controller 4 is being tested
  with AT+MS=V34,1, while controllers 7/8 keep the original V90,1 default.
- Existing user calls continued on 7910 and 7930 during work. Do not clear
  all calls or restart shared services to clean up an individual test.

Test logs on Eicon420:
/var/tmp/new-bri-7950-clock.log,
/var/tmp/new-bri-79{20,30,50}-asterisk-clock.log,
/var/tmp/new-bri-7950-asterisk-from8.log.

7940 needs physical cable confirmation. MultiTech diagnostic status showed
first span LINE ACTIVE / LINE READY, second span LINE NOT ACTIVE / LINE NOT
READY. None of the other Eicon controllers' inspected B1 records identified
7940; controller 3 retained an older local address 2905. Do not infer the
7940 peer from signalling alone.

## Final follow-up

The controller-4-only V.34 listener setting returned a complete menu and
readable ECHO responses for probe lines 0 and 1 through Asterisk. Listener
reported V34/LAPM/V42BIS 24000 TX / 9600 RX. The stream stalled between
fragments (about 16 seconds), then disconnected at 32 seconds, so this is
partial functional evidence, not a reliable sustained data qualification.
Log: /var/tmp/new-bri-7950-v34answer.log. The V.34-only setting remains in
new-spans.json for controller 4. Existing shared answerer code was not edited.

Clock BRI0/1/0 remained GOOD and selected; startup-config includes the source.
All three Eicon services (existing test answerer, SIP gateway and new-span
listeners) remained active. No router reboot or Eicon card reload occurred.
Outstanding: physically confirm 7940 cable, and investigate 7950's asymmetric
rate/stalls before considering all new lines reliable.

## 7940 targeted follow-up

Q.931 trace on BRI0/2/0 showed SETUP retransmission without a peer reply,
then timer-expiry clearing. Debugging was disabled afterward. A brief
network-side role test on only 7940 did not activate the MultiTech second
span, and was reverted. Controller 3 D-channel trace during an isolated
7940 reset captured TEI assignment `FE FF 03 0F 2D 1E 02 DB`; the Cisco
then reported TEI 109 (DB >> 1). This correlates 7940 with Eicon controller
3. Its Layer 2 remained Connecting with MDL-ERROR(I), despite the Cisco
reporting MULTIPLE_FRAME_ESTABLISHED. No Q.931 call reached the gateway.

A temporary controller-3-only fixed TEI 0 / NT2 off / one-link test did
not establish Layer 2. These settings were restored to Automatic TEI true,
NT2 true and eight links; Cisco static TEI was removed and its original
user role and point-to-point-setup retained. No persistent test changes
were saved. The active 6010 -> 7910 call was not cleared.

Next proposed test: controller 3 Config\\Layer2\\L2 from 0 (On demand) to 2
(Permanent establishment), then clear only BRI0/2/0 and verify Q.921/Q.931.
Automatic approval rejected this controller-wide change because controller
3 also hosts diva-sip-gateway (2910). Explicit user approval pending. This
is establishment policy; durability across card reload has not been assumed.
Trace: /var/tmp/7940-controller3-trace.txt on Eicon420.

## Owner cable move

Owner moved an uncertain router port to MultiTech. Subsequent inspection:
controller 2 L1 Down; Cisco 7900 now L1 Active but old fixed-TEI-0 peer state;
MultiTech second span changed from inactive to active. Reset only idle
BRI0/0/0. A targeted call from Eicon controller 8 to 7900 caused Cisco
BRI0/0/0 to establish TEI 64, consistent with MultiTech's automatic TEI.
This strongly identifies the moved cable as 7900 -> MultiTech second span.
The first MultiTech span remains on 7910 with the existing 6010 call intact.

Unused ttyM7 responded AT at 115200; ttyM6 did not respond at 115200/38400/9600.
The ttyM7 autoanswer test to 7900 timed out without CONNECT; its original
S0=2 was restored successfully. No sustained data call on the moved 7900
span has been demonstrated. Log: /var/tmp/multitech-7900-modem7-test.log.
Test calls cleared; final compact router call table contained only 6010 ->
7910. No card reload or staged 7940 TE fallback was applied. Staged files
/var/tmp/7940-before.rc, 7940-before.xml, 7940-te3.rc, 7940-te3.xml remain
unapplied and must be checked against current configuration before use.
