# New RASFinder on Cisco FXS, 25 October 2026

## Remaining S108 options: 1 and 2 passed; 3 comparison invalidated

Requested follow-up compared S108 **1 -> 2 -> 3 -> 7** against7900
in one raw dial-out session with automatic V.90 and exact echo checks.

- S108=1: V90 TX31200/RX46666, 816 exact echoed characters, zero frame
  errors/retrains; dial27.56s, clean remote Q hangup.
- S108=2: same rates and integrity results; dial26.67s.
- S108=3: no carrier within80s; the subsequent S108=7 control also failed.
  **Do not attribute this to S108=3.** Answerer service logs show an
  independent stop/restart at22:49:04 NZDT during this trial. Its config
  changed to only controller1/ttyds3+4 number7910, removing controller2
  7900 channels. `faxgetty` independently took ttyds1+2 (PIDs28682,28684).
  This chat did not change or restart that service for these comparisons.

S108=7 restoration was acknowledged OK after all attempts. Additional
fresh-profile baseline call was started before discovering the external
change; it cannot establish a valid comparator while7900 is repurposed.
No change to the independently configured faxgetty/answerer was attempted.

Logs: `/tmp/rasfinder-7900-loss-123-20261025.jsonl` and
`/tmp/rasfinder-7900-loss-final-control-20261025.jsonl` locally.

## 7900 comparison: PBX digital-loss setting made no difference

At owner's request, RASFinder WAN1/7800 dialled the normal Eicon test
answerer **7900**. Kept automatic V.90 profile unchanged:
`+MS: V90,1,0,31200,0,56000`, S38=1. Compared S108 in order
**7 -> 0 -> 6 -> 7**, keeping one dial-out management connection open.

All four calls: ATI11 **V90**, initial/final TX **31200**, RX **46666**,
LAPM/V42bis, measured digital loss0dB, zero TX/RX frame errors and zero
local/remote retrains. Each passed six deterministic text echo checks
(816 exact characters per call). Dial times26.69,26.80,26.64,26.80 seconds.
Returned to answerer's menu and requested Q; all four received NO CARRIER
before reading ATI11. Each restoration to S108=7 was acknowledged OK.

S108=7 is 0dB with robbed-bit compensation; S108=0 is 0dB without it;
S108=6 is 6dB compensation. **No observed carrier-rate/data-integrity
benefit from changing S108 to0 on this path.** This is a short-call
comparison, not a throughput or long-soak qualification.

Log: `/tmp/rasfinder-7900-loss-single-session-20261025.jsonl` locally.
Harness: `/tmp/rasfinder_7900_single_compare.py`.

Important recovery finding: initial separate-socket baseline passed, but
closing raw dial-out left Ethernet Telnet unavailable again even after
only816 echoed characters. Thus the earlier stall is not proven to be
bulk-size-specific. Recovered via modem-shell self-Telnet and management
reset after checking all other lines idle. Nested menu CRs must be paced:
send digit+CR, read echo, then separate CR; back-to-back CRCR did not work.
Recovery helper on eicon420: `/var/tmp/rasfinder_recover_reset.py`.
Final comparison used one raw dial-out session for all cases and sent
Telnet Interrupt Process (IAC IP) before socket close. **Ethernet Telnet
remained healthy afterward**, with correct MAC and uptime3m40s, so no
second restart was needed. Use this explicit IAC IP cleanup for future
raw dial-out tests rather than simply closing the socket.

## Recovery completed after faster-rate test

Owner reported power-cycle, but Ethernet Telnet remained unavailable.
Direct VLAN70 capture showed SYN retries to correct MAC with no reply;
ICMP worked and HTTP accepted TCP without returning data.

Reached WAN1 via Eicon call to7800 at33600, shell option2 Telnet to the
unit's own10.69.70.33. Internal management worked. Menu inputs need
**two carriage returns** after a choice through this nested Telnet client
(send digit+CR, then another CR); a single CR only echoes the digit.
ESC navigates the remote menu normally.

System Information confirmed MAC0008005128d0 and **uptime4h32m50s**,
so the reported power-cycle had not reset this identified unit. Cisco
showed only our WAN1 recovery call active, all other RASFinder lines idle.
Used management option3 Reset RASFinder (resets immediately, no prompt).
Our call dropped and caller was released cleanly. Ethernet Telnet returned.
Fresh System Information showed same MAC and **uptime19 seconds**;
saved address10.69.70.33 retained. Management recovery is now verified.
No further bulk traffic was sent after recovery. Faster negotiated rate
53333 remains a short-call result; sustained throughput remains unverified.

## Faster-rate follow-up and recovery history

Rate tests with BRI S109=1 and the original RASFinder automatic profile:

- S38=23 (56000 selection): V.34 fallback at 31200/31200.
- **S38=21 (53333 selection): ATI11 V90, initial/final TX31200,
  RX53333**, LAPM/SREJ, V42bis; marker/ack passed, zero frame errors
  or retrains; one local rate renegotiation. Approximately 14.3% higher
  downstream carrier than 46666. This is a short negotiated-rate/data
  check, not proof of sustained throughput or reliable repeated calls.
- S38=22 (54666 selection): two calls fell back to V.34, one RX4800
  and one RX31200. No faster carrier established.

A subsequent deterministic 32768-byte SHAKE256 payload test, base64
framed over the RASFinder dial-out Telnet session, **did not complete**:
the 60-second sender limit left 12486 encoded bytes pending and the
65-second client read collected only 28276 bytes. Decode failed with
incorrect padding because the transfer was incomplete; no data-integrity
pass is claimed. Serial/Telnet flow control is an observed bottleneck,
not proven modem-level throughput. A longer retry then received no Telnet
banner; stopped local Python PID94806 and the remote receiver.

**Outstanding: .33 still pings (exact MAC confirmed), but Telnet times
out; HTTP accepts TCP but sends no HTTP response. SNMP public did not
respond (SNMP enablement unknown).** Could not verify modem settings
restoration because the cleanup AT commands were not acknowledged.
No AT&W or other nonvolatile RASFinder write was issued. User asked to
power-cycle only the new .33 unit to recover management; not yet confirmed.
Saved LAN IP is 10.69.70.33/24. Existing .32 was not touched.

Cleanup verified Cisco has **zero calls**, all 7800/1/2 on-hook/idle.
Normal multitech-ppp, bri-test-answerer and diva-sip-gateway services are
active; ttyM4 owned only by restored PPP/chat, ttyds6 unused.
No temporary Cisco changes were made in this faster-rate follow-up.
Do not report sustained 53333 performance or a recovered Telnet service
until verified after the requested restart.

## Current result: V.90 works; BRI answerer now accepts both PCM modes

Two RASFinder WAN1 -> 7910 calls conclusively reported **V90** in ATI11,
initial/final TX **31200**, RX **46666**, LAPM/SREJ, V42bis, zero frame
errors, retrains or rate renegotiations. Each passed the exact test
marker to the BRI receiver and received its acknowledgement. Digital
loss reported 0 dB, robbed-bit signalling 00, Last PCM S PTR 8BB1.

Working client profile is the RASFinder dial-out default initialization
`AT&FW1X4&D2&C1S0=1`: readback `+MS: V90,1,0,31200,0,56000`, S109=2,
S108=7, S42=1. Do not replace this with the earlier forced modulation,
fixed loss compensation or fallback experiments; those gave V.34/failure.
This proves the existing card -> BRI -> local TDM -> FXS -> RASFinder
path can carry PCM modem data and does not require a law change.

First success used BRI receiver S109=2; repeat success used S109=1.
Persisted `/etc/chatscripts/multitech-answer` initialization changed from
S109=0 (Flex only, excludes V.90) to **S109=1 (V.90 and Flex)**.
Original backup: `/etc/chatscripts/multitech-answer.before-dual-20261025`.
Repository `deploy/ppp/multitech-answer` matches. Normal PPP service
restored after the bounded raw receiver tests. PAP/IP session was not
tested in these raw modem calls.

Automatic Flex-only comparison, client S109=0 with original S108=7 and
`+MS: K56,1,0,31200,0,56000`, BRI S109=0, still reported V34 31200/31200.
**K56flex remains unverified**; working V.90 is not a claim of Flex success.

Other controls: forced V.90/loss=0 and loss=6 gave V.34; disabling FXS
nonlinear processing/comfort noise/VAD did not restore V.90; matched
mu-law across FXS0, BRI1 and BRI modem S108=0 failed with NO CARRIER.
All Cisco experiments reverted, temporary dial-peer 17902 removed, and
startup/running comparison showed no changes. Both relevant Cisco ports
are A-law, BRI modem S108=1; `voice-card 0 local-bypass` is enabled.
An Eicon receiver control on temporary 7902 also passed V.34 data but
did not establish V.90 under the forced client profile.

Owner connected another RASFinder's three analogue lines to Cisco numbers
7800, 7801, 7802 (voice ports 0/3/0, 0/3/1, 0/3/2 respectively). Its
Ethernet is intended to be on LAN 10.69.70.0/24, and its command port is
cabled to eicon420. Owner supplied the new unit's MAC as
**00:08:00:51:28:D0**. Owner confirmed model **MTASR3-200**, Ethernet
LINK on and FAIL off. The custom command cable worked with another RASFinder.

Later capture on gateway physical trunk **igc0**, filtered for the exact
source MAC, found the unit on **VLAN 88**, sending RIP v2 from
**192.168.0.1** and IPX SAP advertisements named RASFIND. Earlier VLAN-70
discovery therefore targeted the wrong broadcast domain. No network
configuration was changed.

A bounded call from Eicon controller 2 to **7800** connected at
**33600 bit/s** and presented the RASFinder shell. Shell option 2 (Telnet)
to its own **192.168.0.1** reached **RASFinder Version 3.17** management.
127.0.0.1 failed. Menu input after connecting needs further line-ending
testing; no configuration was written. The diagnostic call was hung up
and its caller released. 7801 and 7802 have not yet been call-tested.

**10.69.70.32 is the owner's existing RASFinder, not this new unit.**
Its web/Telnet banners identify RASFinder firmware 4.12, boot 3.00.16,
Ethernet MAC 00:08:00:51:80:83. Read its menus only; no settings were
changed. Do not configure or reset .32 to set up the new unit.

## Saved LAN configuration

## Eicon modem tests

### Resolved: Cisco companding mismatch

Further diagnosis found working 0/3/0 used A-law, while failing 0/3/1
and 0/3/2 used u-law. WAN1/2/3 RASFinder settings matched: enabled,
Async, Modem Connect, Answering, terminal server enabled, 115200 baud,
Multi-Tech 56K Modem Module profile, blank dial string.

Changed **only** `compand-type a-law` on Cisco 0/3/1 and 0/3/2.
With gain, delay and all other settings unchanged, 7801 connected at
**33600 bps in 16.7 seconds** and 7802 at **33600 bps in 16.4 seconds**.
Both presented the shell, accepted Telnet to 10.69.70.33 and returned
the RASFinder 3.17 management banner, proving bidirectional data.
All three FXS ports were on-hook/idle after cleanup.

Startup/running comparison showed only these two companding changes,
so the verified fix was saved to startup configuration. Successful raw
results: `/var/tmp/rasfinder-eicon-alaw-success-20261025.json` on eicon420.
No gain/delay tuning or RASFinder modem configuration changes required.

### Initial tests before correction

Subsequent V.90 automatic-negotiation probe on 7800 used temporary
`AT+MS=V90a,1` instead of forced V.34. It connected at **33600** in
15.9 seconds and passed shell/management-banner data. This does not
demonstrate a V.90 connection; no 56K connection has been established
to this analogue RASFinder. Result on eicon420:
`/var/tmp/rasfinder-eicon-v90-20261025.json`. Call hung up and TTY released.

Tested from eicon420 `/dev/ttyds6`, Eicon controller 2, caller 7980,
with temporary `AT&F5`, `AT+iQ=o2`, `AT+iO7980`, `AT+MS=V34,0`.
7800 connected at **21600 bps** in 26.4 seconds, presented the shell,
and accepted Telnet-to-host 10.69.70.33, returning firmware 3.17's
management menu. This proves bidirectional application data, not bulk
throughput or PPP. Earlier 7800 calls negotiated 33600 bps.

7801 timed out at 50 seconds in both attempts. 7802 first returned
NO CARRIER in 31.9 seconds; that attempt overlapped accidental entry to
WAN2 WANTalk, whose exit warns of a reboot, so it is confounded.
An isolated repeat of 7802 timed out at 50 seconds. Cisco showed
0/3/2 off-hook during that repeat: the third unit answered, but no
carrier was established. No modem settings were written through WANTalk.
Do not use main option 4 for read-only configuration: it enters raw
modem passthrough and warns of reboot on close. Use main 3 -> WAN 5.

Each call used a bounded helper with hang-up and TTY close/restore.
Raw results on eicon420:
`/var/tmp/rasfinder-eicon-test-20261025.json` and
`/var/tmp/rasfinder-eicon-retest-20261025.json`.

## LAN change details

## RASFinder-originated call to BRI 7910

### Further controlled testing

WAN1 `AT+MS=?` lists BELL103,V21,BELL212A,V22,V22B,V23C,V32,V32B,
V34,V90,V92 but omits K56. However `AT+MS=K56,0` returns OK and
`AT+MS?` reports K56, so omission does not prove missing Flex support.
Numeric selector `AT+MS=56,0` returns ERROR.

WAN1 explicit `AT+MS=K56,0,0,28800,0,56000`, S108=0 connected and
passed the test marker/ack, but ATI11 reported **V34 28800/28800**,
zero errors/retrains, Last PCM S PTR 71A1. This attempt inherited
S109=2 until the +MS command's effects; do not infer Flex from accepted AT.

An attempted receive-rate floor 32000 was rejected with ERROR; the trial
therefore did not apply that floor. With S109=0 verified and S42=0,
the subsequent call failed with NO CARRIER, ATI11 failure/cause 3.

WAN2 comparison: original S109=2; `AT+MS?` default V90,1,0,31200,0,56000.
Applied S109=0, then accepted `AT+MS=K56,0,0,28800,0,56000`, S42=0,
S108=0. Readback: S109=1 (the +MS command changes the legacy register),
K56,0,0,28800,0,56000, S42=0. It nevertheless connected with ATI11
**V34 28800/28800**, LAPM/SREJ, V42bis, no frame errors or retrains;
marker/ack passed; Last PCM S PTR 71A1. Hence S42=0/+MS auto=0 cannot
be assumed to prohibit V.34 fallback on this installed firmware, despite
older manual wording. Both WAN1 and WAN2 show the same limitation.
Restored S109=2,S42=1,S108=7 with OK responses and restored PPP answerer.
No firmware changes, AT&W, or router changes were made.

Used main menu 1 (Dial-out), WAN1, default 115200 8N1. This grants raw
modem control without entering WANTalk/reboot mode. Initialization sends
`AT&FW1X4&D2&C1S0=1`. ATI3 identifies **LT V.90 1.0 MT5634SMI Data/Fax
Modem Version 4.25f**. Read original registers: S38=1, S42=1, S108=7,
S109=2 (V.90 only).

Temporarily set S109=0; dialled `ATDT7910`. The BRI raw receiver on
eicon420 ttyM4 had S109=0 verified, autoanswer enabled, PPP service paused
with automatic restoration. Call connected. RASFinder's CONNECT 115200
was DTE speed, not carrier rate. It sent RASFINDER_FLEX_TEST_7800;
the BRI received it exactly and returned BRI_FLEX_ACK_7910, received
at the RASFinder. Subsequent ATI11 conclusively reported **V34**, initial
and final TX/RX **31200/31200**, LAPM/SREJ, V42bis, zero frame errors,
zero retrains and rate renegotiations. This is not a Flex success.

Second controlled attempt selected S109=0, S38=1, S42=0 (disable 56K
autorate/fallback), S108=0 (zero digital-loss/no robbed-bit compensation).
The BRI rang but the caller returned **NO CARRIER**. ATI11 reported
Failure, termination cause 3, no carrier rates or transmitted data.
Original S109=2, S42=1, S108=7 restored with OK responses; no AT&W used.
Raw BRI receiver closed and PPP answerer restored automatically.

Modem-specific reference: Multi-Tech MT5634SMI ModemModule Developer's
Guide, hosted by IBM:
https://www.ibm.com/support/pages/system/files/support/nas/nastech.nsf/0/2ff519529264679385257bc10063c090/%24FILE/MT5634SMI%282771%29.pdf
It documents S109=0 disables V.90, 1 dual Flex/V.90, 2 V.90 only;
S42=0 disables 56K autorate and V.34 fallback. Advertised model-family
support is not proof this installed firmware establishes Flex.

At the owner's request, changed the new unit to **10.69.70.33/24**,
with default route **10.69.70.1**. The unit moved from VLAN 88 to
**VLAN 70** during configuration (observed exact-source-MAC capture;
no switch change was made by this session). Configuration was saved
through the menu's required save-and-reboot operation. IP and gateway
needed separate saves because gateway validation used the active subnet.
Confirmed 4/4 ping replies and Telnet firmware 3.17 management at .33;
gateway ARP maps .33 to **00:08:00:51:28:d0 on vlan0.70**.

Address .33 is outside gateway DHCP pool .100–254; no reservation or
ARP response existed in pre-change checks. Temporary gateway aliases
192.168.0.230/32 and 10.69.70.34/32 and host routes used for recovery
were removed by bounded helpers. No existing RASFinder settings changed.

Cisco's three FXS ports are enabled and initially reported on-hook/idle.
7800 has since passed the modem connection and shell test described above.

## Access and discovery

- eicon420: SSH as scottcryan using existing key; sudo available.
- LAN gateway 10.69.70.1: existing SSH key access works as root; hostname
  fw.net.cryan.nz, OPNsense/FreeBSD. Shell is csh; use `sh -s` for scripts
  with POSIX redirection. No gateway configuration changed.
- VLAN interface for the target LAN is vlan0.70. Gateway dnsmasq DHCP
  range is 10.69.70.100–254. Lease file `/var/db/dnsmasq.leases`.
- A TCP scan of Telnet and HTTP on 10.69.70.1–254 identified only one
  RASFinder (.32); .6 is MikroTik and .221 AudioCodes MP-114, not the unit.
- Gateway ARP and DHCP lease inspection did not identify another
  Multi-Tech. A 40-second passive capture filtered for source OUI
  00:08:00, excluding existing .32's MAC, captured no matching packets.
  This does not prove the new unit is disconnected or lacks DHCP: its
  MAC/model is not confirmed, it may be silent or statically addressed.

## Command serial connection

eicon420 has 16550A UARTs ttyS0 and ttyS1; ttyS2/3 are unknown UARTs.
No process/getty owned ttyS0/1 during inspection. Initial inference from
ttyS1's CTS/DSR/CD was incorrect: sysfs/PCI inspection identifies ttyS1 as
Intel 8086:1e3d KT management/SOL, while ttyS0 is the onboard PNP physical
COM port. Use **/dev/ttyS0** for physical command-cable work.
Sent only one carriage return to each at 19200 8N1 and got no received
bytes. Restored host termios after each probe. No persistent serial or
RASFinder changes were made.

The manufacturer-authored MTASR3-200 manual describes a 19200-baud command
port used by its Windows configuration utility, so no text prompt is not
proof that the link is faulty. Owner subsequently confirmed MTASR3-200.

## Next step

With exact MAC 00:08:00:51:28:D0 supplied, searched all gateway ARP entries,
current dnsmasq leases, legacy DHCP leases and dnsmasq static mappings;
no match. A 40-second exact-source-MAC capture on vlan0.70 saw no traffic.
Then sent 768 low-rate discovery frames directly to that MAC via libpcap
on vlan0.70: ARP requests covering 10.69.70/24 and the previously observed
legacy ranges 192.168.2/24 and 192.168.88/24, plus ICMP echo, address-mask
and timestamp requests to limited-broadcast/all-hosts destinations.
A concurrent 25-second exact-MAC capture saw no replies. No configuration
or IP addresses were changed, and no reset was performed.

This does not identify an IP or prove DHCP capability. The device could
have a static address outside the probed ranges, be on another VLAN,
have no Ethernet link, or not have booted successfully. Asked owner to
check that Ethernet LINK is on and FAIL is off, and provide exact model.
Avoid further interaction with the known existing RASFinder at .32.

Discovery artifacts on eicon420:

- `/var/tmp/rasfinder-lan-discovery-20261025.json`
- `/var/tmp/rasfinder-lan-discovery-extra-20261025.json`
- `/var/tmp/rasfinder-106970-discovery.json`

The initial ARP probes from eicon420 are not authoritative for VLAN 70:
eicon420 is 10.150.150.157/24 and Cisco proxy-ARPs for VLAN 68, yielding
many replies with the router MAC. Use the actual gateway's vlan0.70
interface and its ARP table for further layer-2 discovery.
