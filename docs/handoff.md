# Handoff: current state, live blockers, and what has been disproved

Written at Session 93 and updated through Session 113. The running log in `eicon_adsp_firmware_analysis.md` is
chronological and is the record of *how* things were established; this document is
the current picture and is meant to be read first. Where the two disagree, this
one is newer.

Read this, then read the sessions it cites. Do not re-derive anything in the
"already disproved" lists — most of the value in this repo is knowing what has
already been ruled out, and several of those entries cost multiple sessions.

---

## 1. Where things actually stand

The card connects. It has reached full V.90 data mode against two different
analogue modems, and one call in Session 87 walked the whole state machine to
`0x00d0` at 38666/24000 with DCD, CTS and both speed flags asserted.

These blockers are live:

| blocker | status | where |
|---|---|---|
| **the INFO message's first 16 symbols decode to `0x2000`** | **retired as a receive fault**; `tools/v34_info.py` decodes the wire independently and the peer really does send zeros in bits 6..12, so `DM(0x3F89) = 0` is a correct decode. The V.34 originate stall is real but is not a demodulator, framer or length defect | Sessions 102–104, **114** |
| **the V.34 page freezes** | **root cause found (114z): the "fixed" V90D bulk worker at PM `0x1930`/`0x1934` writes `0xee1c`/`0x11e4` over the read-database dispatch table at `DM(0x00A8..0x00A9)`.** The walker at PM `0x2722` reads that table correctly at cyc 112,697,738 and corrupt at cyc 113,563,110, with the two writes in between; `CALL (I7)` with `I7 = 0xee1c` masks to PM **`0x2e1c`**, two instructions inside a scan loop, so `AY0 = $00FF` never runs and it spins on leftover registers for 941 M iterations — 99.7% of the call, while the 8 kHz ISR keeps perfect time and PM `0x0771` never runs. See the bulk-worker row: the 114k–l bound is `0x0061..0x0241`, and `0x00A8` is inside it. Retracted: 114r/114v/114w (those tables are intact and their scans succeed) | Sessions 76–79, 114b–p, **114u, 114x–z** |
| **neither loopback endpoint holds real time once page 8 is resident** | open; 0.65x, so post-5.2 s timing in loopback captures means nothing | Session 100 |
| **V.34 has never been tried against hardware since the tree changed** | **closed.** Two live forced-V.34 calls placed in Session 114c; both loaded overlay `0x0261` and both froze. `tools/cx_at.py` is restored, and forcing V.34 at *both* ends reaches the page deterministically instead of via the DIL lottery | Sessions 72–79, **114c** |
| **V.90 needs `--native-bearer-activation`** | open, cause unknown | Session 67, 87 |
| **DIL is a lottery** | open; attempts can fail before either rate is published | Sessions 88–93, 105–107 |
| **exact upstream rate falls outside the final quality ceiling** | guarded and live-selected at 12,000; bilateral data proof still pending | Sessions 107, 109–110 |
| **the native V90D bulk worker corrupts DM** | **REOPENED (114z); still no safe reseed target (115e).** Confirmed and stable: the bulk delay rings **belong in low DM** (base `AY0` at PM `0x190e` reads `0x0062`, then `0x01dc`), the index `AX1` steps by 2 (per the ADDSP guide's (X,Y) couples), the reset at PM `0x1938` **never fires**, and the dispatch table at `0x00A8` is real (114y watched PM `0x2722` read all 13 entries correctly on pass 1) and is later overwritten by PM `0x1930`/`0x1934` — which freezes V.34. **Not confirmed:** the allocation map. Watching one word from each supposed structure (`0x009b`/`0x00a8`/`0x00c0`/`0x01dc`) returns **identical writer and reader profiles** — same PCs, same proportions, across 308 words — so they are samples of one swept region, not four owners, and 115d's extents rest on nothing measured. Sampling cannot settle this. Next: give `--assert-dm-clean` a per-address budget (`LO:HI:BUDGET@OVERLAY`) and group `dm w` by address+writer to get the region's true partition in one call, then reseed to the measured gap | Sessions 106–108, 110–111, 114k–l, 114z, 115–115e |
| **the echo bulk delay had no length at all** | **fixed and hardware verified**; the firmware's seeder runs ~1.5 s before its input exists, so both lengths were zero for every call. `_service_bulk_lengths()` seeds from the floor and holds | Sessions 112–113 |
| **V.34 upstream stays at 7,200** | open; **not** the echo canceller — quality `DM(0x0fcf)` is flat at `0x02d0..0x02e2` across a 10× range of bulk delay, and matches Session 109's archived `0x02cf`. A receiver/line question | Session 113 |
| **nothing gets past `TrnProgress 0x0050` live any more** | **fixed**; `PortableBulkDelay` was writing over the per-frame dispatch vector at DM `0x3fb8`. Four of ten calls now reach `0x00d0` with bilateral payload | Session 113 |

**"The calling side never trains" is closed.** Session 100 got the loopback
caller through V.8 to a V.34 page load. The three faults were all in this
harness — a media clock that started at SIP setup instead of at the first
tick, a NORM_L write that used 0x3EE0 as the write-DB base when it is 0x3EE4,
and a page request for the already-resident page that re-entered V.8 in the
middle of ANSam detection. Section 2b below is the record of how the caller was
got moving at all and is still accurate; its "the caller never transmits"
conclusion is not.

Reported behaviour, which matches the captures: calls reach DIL, then either
continue (and work well), attempt a retrain, or stall. `0x00b3` is the stall
state; `0x00c6`/`0x00d0` are success; `0x00c0` is a partial.

### V.42 / terminal

A LAPM transmitter and PTY terminal exist (`--tx-v42 --v42-pty`), and **basic
V.42 is now established and bidirectional against live hardware**. Framing,
XID, windowing, go-back-N, fallback recovery and the §7.2.1 detection phase are
covered by 42 tests in `tests/test_v42_lapm.py`. V.42bis adds 13 focused tests,
V.44 adds 12, and the bulk/rate work adds 33; the full Python suite is 242.

V.42bis is now implemented behind `--tx-v42bis` (which requires `--tx-v42`).
The opt-in endpoint emits and parses the Annex A private XID group (`GI=f0`,
PSI `V42`, P0/P1/P2), negotiates the smaller dictionary and string limits, and
installs codecs independently in the two P0 directions. The streaming codec
starts in transparent mode, handles the cycling escape value, switches to
LSB-first packed codewords, steps codeword width, aligns C-FLUSH transfers,
and recycles leaf dictionary entries. Tests cover byte-at-a-time fragmented
decode, multiple flushed transfers that retain dictionary context, dictionary
rollover, and compressed LAPM I frames in both directions. The default remains
uncompressed and byte-for-byte compatible with the established V.42 path.

Live CX93001 interop now confirms the implementation. The peer was configured
with `S46=138`, `AT+DS44=0`, `AT+DS=3,0,2048,32` and `AT+DR=1`; it reported
`+DR: V42B` followed by `CONNECT 42667`. Its XID proposed both directions,
512 codewords and a 32-byte maximum string, and the endpoint returned the same
private parameter group. A 524-byte CX payload was recovered exactly from the
endpoint PTY after arriving in 118 compressed information octets. In reverse,
the endpoint encoded 527 application octets into one 79-octet information
field, and the CX DTE recovered all 527 octets exactly. The frame was eventually
acknowledged (`unacked=0`). The evidence is in
`artifacts/interop/nldata-cx/v42bis-mailbox1`; the two preceding attempts failed
below LAPM with zero HDLC frames, consistent with the known physical-training
lottery rather than a compression failure.

V.44 is independently implemented behind `--tx-v44` (also requiring
`--tx-v42`, and mutually exclusive with `--tx-v42bis`). Its unlengthened
`GI=ff` user-data TLVs carry the `V44` identifier and C0/P0/P1/P2/P3 values.
P0 directions are complemented in the response because they are relative to
each XID sender; asymmetric limits are paired local-TX/peer-RX and
local-RX/peer-TX and reduced to the smaller proposal. The stream codec handles
compressed and transparent modes, LSB-first code packing, ordinal and codeword
STEPUP, FLUSH alignment, REINIT, C1, history limits, and overlapping string
extensions. The encoder deliberately uses the conforming append-only subset
(one-character string segments plus complete codeword matches); the decoder
accepts the peer's full string-extension form.

Live CX93001 interop confirms both directions. With `AT+DS=0`, `AT+DS44=3`
and `AT+DR=1`, the peer reported `+DR: V44` and `CONNECT 42667`, proposed the
default 512-codeword/32-character/1024-history values in both directions, and
accepted the matching response. Its 521-byte payload (`cx-v44-`, 512 `A`
octets, CR/LF) occupied 36 I-frame information octets and was recovered exactly
on the endpoint PTY. This exposed and fixed a decoder bug: the peer extended
the C1 string `AA` by 30 characters using an overlapping history copy, which
must make each newly copied character available to the rest of the extension.
In reverse, the endpoint encoded the 524-byte `eicon-v44-` payload into 53
compressed octets and the CX DTE recovered it exactly. Final state was
`unacked=0`, with 55 good frames, one bad FCS, six aborts, six received I
frames, and one transmitted I frame plus three retransmissions. Evidence is in
`artifacts/interop/nldata-cx/v44-mailbox2`; the immediately preceding redial
ended below XID with `NO CARRIER` and is not compression evidence.

The final establishment bug was in XID parsing. The CX93001-EIS V0.2013 V92,
forced to LAPM with `S48=0 S36=4 S46=136`, sends this 59-octet command:

```text
03af 8280 0013 0303 8a8900 0502 0400 0602 0400 0701 0f 0801 0f
ff 4003 563434 4101 00 4201 03 4302 0200 4402 0200 4501 20
   4601 20 4702 0400 4802 0400
```

The V.42 group is complete after the two window parameters. `GI=ff` then starts
the ISO/IEC 8885 user-data subfield; unlike a parameter group, it has **no group
length** and runs to the FCS. Its contents are V.44 TLVs, with PI `0x41` value
zero declining compression. `parse_xid_parameters()` incorrectly read the next
two bytes, `40 03`, as a 16-bit group length, rejected the otherwise valid V.42
group and answered with a different four-octet optional-functions encoding.
It now stops structured group parsing at `GI=ff`, preserving the negotiated
V.42 parameters. The live response is the peer's 25-octet core byte-for-byte:

```text
03af8280001303038a8900050204000602040007010f08010f
```

The plain-mailbox `v42-mailbox8` call proves the complete path. The CX reported
`CONNECT 42667`, accepted that XID response, sent SABME, and received UA. It
then sent `cx-to-eicon-v42\r\n`; the endpoint accepted it in sequence and the
PTY recovered those 18 bytes. After that proof of establishment the PTY sent
`eicon-to-cx-v42\r\n`; the endpoint emitted an answerer command-addressed I
frame (`01 00 02 ...`), the CX DTE recovered all 17 bytes exactly, and its RR
released the frame (`unacked=0`). End totals were 46 good frames, zero bad FCS,
one SABME, three received I frames, and one transmitted I frame (three T401
retransmissions before acknowledgement). The media loop had zero over-budget
ticks and zero catch-up deferrals.

With `S48=0` the answerer still reaches T400 raw fallback before the CX's first
XID, then correctly re-enters the protocol phase on that valid frame. A raw PTY
therefore sees temporary fallback octets before LAPM; for deterministic tests,
do not inject PTY data until establishment is proven by the first received I
frame. This is why the successful helper waited for the CX payload before
sending the reverse payload.

The earlier fixes are now live-confirmed as a set: raw-fallback recovery,
`0x898A` optional functions with the peer's three-octet width, command/response
addressing, I-frame-only N401 enforcement, continuous post-sync mailbox data,
and exclusive host ownership of the TX mailbox. Compression is disabled by
default; large-window throughput has not had a live soak test. The opt-in
V.42bis and V.44 paths are live-confirmed in both directions.

Note also that `modem_nl_assign_payload()` sets
`DLC_MODEMPROT_DISABLE_V42_V42BIS`, so the **card's own V.42 is switched off** and
this Python is the V.42 entity. Using the firmware's implementation instead has
never been tried; Session 86 sketches it, and `EICON_CARD_V42=1` now sends the
payload for it. It is an optional investigation, not a workaround for the
Python endpoint, which now interoperates.

(An earlier version of this paragraph said the NL ASSIGN used the plain
`B2_TRANSPARENT` branch. It does not: `isdn.c:1533` overwrites the protocol
map's B2 unconditionally on the modem branch, and the payload has always
carried `B2_V42_in`. The DLC, not the LLC, is what disables error control.)

---

## 2a. AT and IDI, ported from divas4linux

`tools/eicon_idi.py` and `tools/eicon_at.py` are ports of the driver's own
payload construction and command parser. Nothing in them imports Unicorn, and
`tests/test_eicon_idi.py` + `tests/test_eicon_at.py` are 89 tests over them.

**The defaults changed nothing.** `modem_sig_assign_payload()` and
`modem_nl_assign_payload()` still emit byte-for-byte what they emitted before,
and there is a test pinning that. Everything below is opt-in.

`eicon_idi.build_cai()` is `putcai()` (`tty_module/isdn.c:1209`) and
`select_modulation()` is `atPlusMS()` (`tty_module/atp.c:1879`). What that
buys, concretely:

| | old `EICON_FORCE_V34` | `v34,1,,33600,,33600` | `v34,0,,33600,,33600` |
|---|---|---|---|
| disabled mask | `0x0080` | `0xfc80` | `0xffbf` |

### What the A/B actually showed (run34, `--to 17.0 --tx-prbs --native-bearer-activation`)

Measured, not inferred. Three things, and the first one retires a hypothesis:

- **`unused_modulations` does nothing.** `v34,1,,33600,,33600` produces host
  writes **byte-identical to the old one-bit `EICON_FORCE_V34`** — all 51,965
  of them. The `0xfc00` bits covering V.FC, K56flex and X2 never reach the
  card. An earlier draft of this section called that mask "the first thing to
  try on the V.34 blocker"; it is not, and it is not worth a live call.
- **The CAI's disabled byte *does* reach the DSP**, through the assignment
  stream at host data port `0x6802`, not through the write database. One
  capability word tracks the mask exactly:

  | CAI disabled | stream triple at `0x6802` |
  |---|---|
  | `0x0000` (default) | `3f00` **`1fb1`** `d200` |
  | `0x0080` / `0xfc80` (V.90 off) | `3f00` **`1f31`** `d200` |
  | `0xffbf` (strict V.34) | `0000` **`1f01`** `8000` |

  Bit 7 of `0x1fb1` is the V.90 bit. Strict mode clears the fallbacks too and
  changes both companion words. The descriptor also shortens: the length word
  at `0x6800` goes 97 → 89 and four words drop out of the stream.
- **The write database is untouched.** All 160 words are identical in every
  configuration — `NORM_L` stays `0xa13f`, `SPEED_SEL_L` `0xfffe`,
  `INFO0_SETUP` `0xf1fd`. The modulation restriction does not reach the page-14
  capability words on this path. That is consistent with Session 89: the card
  authors those itself.

So `automode=0` is the variant with any prospect, and it is a live-call
question. The replay cannot answer it — it is open loop against a V.90
recording, so the page-14 trace and the 9610 TX datagrams come out identical
in all four configurations and mean nothing about negotiation.

---

## 2b. The loopback rig, and the calling side's gate (Session 95)

`tools/eicon_loopback.py` runs two `eicon_adsp_sip.py` instances on loopback and
captures both, which is the first closed-loop test here that does not need the
Courier on a real line:

```bash
tools/eicon_loopback.py --native-mips --seconds 45 --modulation v34,0,,33600,,33600
```

Both instances go through the *incoming*-call signalling path. Which side of the
modem handshake an instance takes is GEN_SETUP1 bit 3 (`--modem-role`,
`EICON_MODEM_ROLE`), not who sent the SETUP, so no outgoing Q.931 state machine
is needed — and the one that was attempted does not work: `CALL_REQ` is accepted
and allocates a call object, but injecting the connected event leaves
`call_state` at `0x00` and the firmware hangs up. It is parked behind
`--simulate-outgoing-call`.

**What the rig found immediately.** The answerer reaches TrnProgress `0x0026`;
the caller parks at `0x0002` on page 12 and transmits *nothing*. The chain is
traced in Session 95: GEN_SETUP1 bit 3 → bit 11 of `DM(0x046A)` (PM `0x38ac`) →
PM `0x357a` routes to the `0x35d7` continuation instead of the training start →
that continuation needs `DM(0x046C) < 0` or `DM(0x0554) >= 0x10`, and neither
ever happens. Forcing `DM(0x0554)` starts transmission; forcing `DM(0x046C)`
does nothing.

Session 96 finishes it: `DM(0x0554)` comes from a **twelve-channel tone
detector** whose correlator state bank at `DM(0x2fc0..0x2fd7)` is never written
by anything, and whose configuration block — write database `+0x30..+0x4F` — is
zero in the **card's own firmware WDB** as well as ours. A PRI product has no
analogue line to listen to, so it never programs a supervisory tone detector,
and `GEN_SETUP1 = 0x048c` is simply not a supported configuration here. Do not
spend another session on the tone bits.

On a PRI, dialling is the Q.931 SETUP. That path stops early: CALL_REQ is
accepted and the called number is parsed and stored (found in one run with
`--scan-ram`), but **no SETUP is ever assembled**, and no lower-PRI event in
`0x01..0x20` moves the call. The D channel's framing layer is DSP work
(`0x0209 SIGPRTX`, `0x020a SIGPRRX`, `0x000b`/`0x000c` SIG kernels) which this
emulation stages but never runs — so the leading hypothesis is that Q.921 never
establishes and Q.931 will not originate over a down datalink. The HLE boundary
that would fix both directions is the MIPS-to-SIG-DSP D-channel queue, where the
payload is standard Q.921/Q.931; see Session 96 for the mapping to SIP.

Already ruled out, do not re-derive: **ADET, Dasen and TonedetEnable change
nothing**, in any combination, against silence *and* against a real answering
pump emitting ANSam. Bit 3 is the only bit that matters, and the calling side is
not waiting for audio — it never transmits or listens.

Two cautions. The `DM(0x0554)` poke is a diagnosis, not a fix: page 12 stays
resident and V.8 is never requested. And **wall-clock timings in loopback
captures are meaningless** — both endpoints drain a backlogged receive queue
without sleeping, so pointed at each other they mutually accelerate (130 s of
media in ~35 s of wall time). The DSP is sample-clocked, so state observations
still hold.

---

Two further differences between our payloads and the driver's, both left alone
because they are on the known-good path and neither has been tested:

- `cai[2]` is 0 here (`add_b1()`: `B1_resource >> 8`) and
  `DSP_CAI_RATE_ADAPTATION_19200` in the tty driver; `cai[5..6]` is 0 here,
  `MaxDataLength` in `add_b1()` and `ISDN_MAX_FRAME` in the tty driver.
- The NL `LLI` is `OK_FC` alone here; `isdn.c:1495` sends
  `OK_FC | CMA | NO_CANCEL`, and `max_data_length` is 1024 here against the
  driver's 2138.

Also worth knowing: **the 56000 Rx ceiling we send is not a legal driver
selection.** The `v90` row's `rx_map` is the V.34 speed map — the digital side
receives at V.34 rates — so `AT+IE=v90,1,,56000` is an error in the driver,
while `legacy_modem_options()` asks for 56000 in both directions. Whether the
firmware minds is untested.

The AT layer (`--at`, requires `--v42-pty`) puts command mode in front of the
terminal: echo, result codes, S-registers, `+++` with S12 guard timing,
profiles, and `AT+IE`, whose selection reaches the CAI of the next call
through `eicon_mips_shim.set_modem_options()`. Because this endpoint answers
the INVITE synchronously, the terminal sees RING immediately followed by
CONNECT; `ATA` has nothing to answer and says so. `ATH` drops the call. `ATD`
is refused — the endpoint answers calls, it does not place them.

---

## 2. The echo canceller chain (Sessions 58 → 93, 101, 105–106)

The near/far echo bulk-delay worker is PM `0x1900..0x19c8`. V.34 owns the
complete native invocation chain:

```text
19d5  CALL (Core8kRoutine)
19d7  CALL 19a7
19a7  test DM(3fc1) bit 0400, load lengths, CALL 1982
19c8  JUMP 1900
```

V90D calls the same setup at PM `0x1a24`. The emulator had generalized a V90D
diagnostic to both pages and replaced V.34 PM `0x19c8` with `RTS`, permanently
bypassing the worker. `EICON_V90D_BULK_ADAPTER` is now page-14-only; V.34 keeps
the shipped tail jump and its native bit/length gates.

### The missing retained descriptor word is now established

Execution traces at both ambiguous reads settle the Session 93 open question:

```text
PM 1917: I1=0005 before AY0 = DM(I1,M2)
PM 1921: I1=0005 before AY0 = DM(I1,M2)
```

The arithmetic bound is descriptor offset 5 exactly. PM `0x1982` writes offsets
`0,2,3,4,6,7` but never offset 5. V.34 and V90D likewise download `DM0..4` and
`DM8..12` while leaving words `5..7` sparse. INFO PM `0x3734..0x3738`, however,
deliberately clears `DM0..0x03ff`, so the reconstructed INFO-to-V.34 handoff
arrived with the retained common-layer word missing.

For the selected zero-based bulk descriptor, offset 5 is the word immediately
below the first valid address: `0xffff`. PM `0x1922/0x1923` compares a candidate
against it and adds `BulkLength` on unsigned underflow. With zero there, the
correction never fires and PM `0x1930` sweeps linearly into unrelated V.34 or
V90D state. Session 101's `DM(0x2165)=0x2859` abort is that exact failure.

The native page handoff now publishes `0xffff` at
`(DM(0x32f7) + 5) & 0x3fff` immediately after loading V.34 or V90D and before
resuming PM `0x06df`. It does not prime a cursor or call the worker from Python.

### Verification and remaining boundary

An immediate-release instruction trace with the original PM `0x19c8` opcode
and the missing limit published kept PM `0x1930` inside the zero-based delay
area and reproduced the clean outer-state walk through `0x007a`; with offset 5
zero, the same trace produced the known broad destination sweep.

A default 15-second native V.34 loopback then loaded page `0x0261` on both
ends, published `DM5=ffff`, and remained in page-8 training instead of taking
the former 40 ms caller abort. It does not yet connect: the caller still
oscillates `0x0060 ↔ 0x0062` because INFO word 0 decodes as `0x2000`, the
independent Sessions 102–104 blocker, and page 8 still runs at about 0.65x wall
time. Hardware V.34 validation is therefore still required.

V90D now keeps PM `0x19c8` held as `RTS` for every width. Width 31 corrupted
DM at PM `0x1930`, and a later exact-12,000 call disproved the apparent width-32
qualification: PM `0x1b69/0x1b6a` swept through the rate and state blocks after
a coherent release. The default allowlist is empty.

`PortableBulkDelay` instead supplies the ADDSP database contract at 8 kHz. It
stores `BulkInputX/Y` in a bounded pair ring and publishes the near and oldest
X/Y pairs, uses the firmware's existing enable and length words, flushes on a
length change, and fails closed on invalid descriptors.

**The database base is DM `0x3ee0` for every offset**, so read-DB `0x56..0x59`
are DM `0x3f36..0x3f39`. Session 111 used base `0x3f60` for that group alone and
landed on DM `0x3fb6..0x3fb9`. DM `0x3fb8` is not an output — PM `0x19f3/0x19f4`
do `I4 = DM(0x3FB8); CALL (I4)` every frame, and the firmware holds `0x3cea`
there, code that sets the `DM(0x3fc1)` `0x0400` enable bit and jumps to the
generator dispatch at `0x2a56`. Writing a sample over it is what parked every
call at `0x0050`. Session 113.

**Until Session 112 it had never run at all.** The length words were `0x0000`
for every page-14 frame of every capture. The 973/1053-pair figure quoted here
through Session 111 came from Session 93's trace and is not what the harness
produces: the seeder at PM `0x3232` fires twice on page `0x0260`, both times
about 1.5 s before its input becomes positive, so it takes the `IF LE` branch
and PM `0x1085/0x1086` — the only writer of `BulkLength` — never executes.

`_service_bulk_lengths()` seeds and holds for both `0x0261` and `0x026a`, from
the **floor** (`0x25 + delaycorrection` = 49/129 pairs, 6.1/16.1 ms).
`tools/echo_delay.py` measures this path's real echo at 41–100 pairs
(5.1–12.5 ms) by cross-correlating captured TX against captured RX, so the
floor is right and `DM(0x3fcb)` (490–540 pairs) is not an echo delay —
`DM(0x3fc9)` is an INFO-page elapsed-time counter. `EICON_BULK_DELAY_MEASURED=1`
restores the addend for a path with a genuine long tail; measure it first.
`EICON_BULK_DELAY_SEED=0` for A/B; `EICON_BULK_DELAY_HOLD_ALWAYS=1` keeps the
host value through the data phase instead of yielding to the firmware's own
439/519.

Hardware verified in Session 113: four `CONNECT 42667` calls at `0x00d0` with
exact bilateral payload. **It does not raise the upstream rate** — see the
V.34-upstream blocker above.

The historical width-32 call remains important transport evidence: it
negotiated 42,667/7,200, stayed up 67.24 seconds, and carried exact LAPM payload
in both directions. It was not a general width-32 safety proof. No native width
is currently released; the bounded replacement is the candidate for the next
hardware call.

### Negotiated-rate measurement and the upstream boundary (Sessions 107, 109–111)

The ADDSP read database is authoritative for both directions. With its base at
DM `0x3f60`, offset `0x81` (`DM(0x3f61)`) is the digital V90D transmitter,
therefore PCM downstream; offset `0x82` (`DM(0x3f62)`) is its V.34 receiver,
therefore upstream. The emulator now latches and reports both values at V.42
entry, AT `CONNECT`, and teardown. The successful Session 106 call's words
`202b/11e9` decode to **42,667 downstream / 7,200 upstream**.

A bulk-bypassed call forced the Conexant's upstream range to exactly 9,600.
The firmware repeatedly selected it: PM `0x3180` wrote `DM(0x3f62)=11ea`.
Just before synchronous data state, PM `0x31d5` replaced that with `11e0`, an
index-zero/no-rate value, and the call ended `NO CARRIER`. Session 109
disassembly shows this is a deliberate no-common-rate fallback: the final
quality ceiling is 3, which admits rates only through 7,200 and excludes the
exact peer bit for 9,600. It is not an accidental write and it is independent
of the bulk worker.

The shim raises the final native mask length only when the peer offers one
locally-supported exact rate above the transient quality limit, then retains
the full three-word setup if the firmware later falls back. A live exact-12,000
call selected and preserved `11eb/4/12` through `0x00d0` at 42,667/12,000, so
negotiation above 9,600 is established. The remaining proof is Courier
`CONNECT`, sustained LAPM, and exact payload both ways using the bounded bulk
replacement. By contrast, the already captured 42,667 / 7,200 bypassed
call completed XID and SABME and received two upstream LAPM I frames, proving
the 7,200 upstream data path.

---

## 3. Already disproved — do not re-derive

### About the echo canceller chain

- **`DM(0x32f7)`, the descriptor selector, is not the discriminator.** Setting it
  to `8` at page-14 entry (before state `0x60`, so PM `0x1982` reads it, and it is
  still `8` at the end of the run) gives byte-identical behaviour to `0`, even
  though the page-entry workspace really does hold a second descriptor at
  `DM8..DM11` (`2ac7 2ad2 2ae0 2b1b` against `2aca 2ad2 2ae5 2b1b` at `DM0`).
  Session 88.
- **`L0` is not missing.** PM `0x19ac..0x19b1` explicitly sets `L0`, `L1` and
  `L4..L7` to `$0000`. The firmware deliberately disables circular addressing, so
  the observed zero `L0` is intended and the linear sweep is not a DAG problem.
  Session 92.
- **The emulator's flags are correct.** `shift_op` touches `ASTAT` only to read
  `CFLAG` for the rotate forms and to set/clear `SS`; it never writes
  `AZ/AN/AV/AC`, which matches the ADSP-2100 family shifter. `CALC_C_SUB` sets
  `AC` as the complement of bit 16 of the raw difference and checks out against
  traced values. With `AY0 = 0`, an always-set `AC` is the right answer. Unlike
  Sessions 46 and 52, this is not an emulator defect. Session 92.
- **PM `0x1982` is not the fault** and `--prime-v90d-bulk-cursor` overwrites a
  correct value. Sessions 90, 92.
- **The far-bulk branch is probably not what should have been taken.** It needs
  `Nearbulklength` negative, i.e. `delaycorrection >= 0x7c3f`, which is not a
  delay calibration; the one negative value tried (`0x8000`) zeroes both lengths
  and scrambles the workspace. Near-bulk is almost certainly correct, which means
  `AX0 = 0` and `DM6 = 0` may both be correct too. Session 93.
- **Call ingress is not the missing owner.** Service driver `0x80098310` →
  `0x80097f60` → poll `0x80095318` all run once; the KERNEL transfer lands `0x10`
  words at DM `0x2e58` (which becomes `0x0277`) and the DATABASE transfer lands
  **all 256 words** at DM `0x3ee0`. `DM(0x2f27..0x2f29)` come up `2f21/2f00/2f0e`.
  Nothing is missing. Session 89 — and note it corrects an earlier probe in the
  same session that reported zero database writes, which was an instrumentation
  error: the bulk-write interception writes `dm[]` directly and never calls
  `adsp2181_host_write`.

### About V.90 capability words

- **`V8_SETUP = 0x0000` is the firmware's own value**, written by the card's
  connected-task DATABASE transfer, not dropped by this harness and not a Session
  75 regression. Session 82 chased this and was wrong; Session 86 retracted it on
  hardware evidence (V.90 connects with it at zero); Session 89 proved the
  provenance. `EICON_WDB_OVERRIDE=0x04:0x6000` therefore forces a value the
  firmware deliberately does not publish — it is a **deviation** from the card, not
  a restoration. Do not read an A/B with it as "restoring correct behaviour".
- The firmware's transfer also writes `INFO0_SETUP=f3fd`, `NORM_H=0001`,
  `NORM_L=b13f`, `SPEED_SEL_L=fffe`, `INFO0D_SETUP=0337`, against handbook
  `f0fd/0001/8100/ff00/03b7`. `NORM_H` matching means the modulation masks are
  undisturbed. `INFO0D_SETUP` differing in bit 7 on a µ-law call is still
  unexplained.
- **The DSP co-authors the write database.** With only one transfer in between,
  `+0x06` is written `0105` but holds `2105`, and `+0x07` written `f3fd` holds
  `f1fd`, by the time `complete_native_answer()` snapshots it. Session 75's model
  of snapshotting the driver's transaction and republishing it verbatim treats it
  as host-authored state; a single snapshot cannot capture it. Session 89.

### About the DIL lottery

- **`DM(0x3f8b)` does not predict the outcome.** It split perfectly over nine
  archived captures — `1` for every call that published a rate, `0` for every call
  stuck at `0x00b3` — and the very next live call had it clear and reached
  `0x00d0`. It is logged as instrumentation only. Nine samples was not enough and
  a perfect split over them made it look stronger than it was. Session 87.

  Session 102 found what the word actually is: **one of the six values the INFO
  overlay publishes at PM `0x3df1..0x3e01`**, copied there from `DM(0x0609)`.
  It is a phase-2 result field, not an independent measurement, which is a
  better account of the failed prediction than the sample size. Its companions
  are `DM(0x3F88)`, `DM(0x3F89)`, `DM(0x3F8C)` and BaudInfo `DM(0x3FBB)`, and
  on the loopback every one of them except BaudInfo is zero. Session 103 traced
  why: they are bit-fields of one packed word, `DM(0x060A)`, which reads
  `0x2000`. (Session 103 attributed that to a sparse receive array and a slot
  cadence mismatch; **both were misreadings** — see Session 104. The array is
  fully written, the cadence is the 16x oversampling, and the slicer is not
  marginal.)

  **Session 114 closes the receive-path reading entirely.** `tools/v34_info.py`
  demodulates the captured audio in Python and accepts a message only on the
  transmitter's own CRC, so it never touches the firmware or its emulation. The
  peer's payload bits 6..12 — the whole of `DM(0x1705)`/`DM(0x3F89)` — are zero
  on the wire on both the call that took V.34 and the call that took V.90. The
  decode agrees with the card: `DM(0x3F88)` is `0x000f` where the payload begins
  `1111` and `0x0000` where it begins `0000`, which also fixes PM `0x358E`'s
  packing as LSB-first. **Do not spend another session recovering a value the
  peer never sent.**

  Session 114 also claimed the message lengths match. **They do not** — the
  peer's later message decodes at 36 bits against the framer's `DM(0x1651)` of
  `0x0260` = 38, and it validates at several adjacent lengths in a way a
  synthetic ones-filled frame does not. Session 104's length question is still
  open. It is not on the critical path while the page emits nothing.

- **The two directions do not share a control-channel carrier.** The card
  transmits at 1200 Hz and the peer at 2400 Hz, both 600 bit/s, and between them
  sits the V.34 line probe (energy on every multiple of 150 Hz). Decoding a
  `.rx.ulaw` at 1200 Hz alone recovers nothing but our own transmissions echoed
  back 5–10 ms later, and makes the peer look silent. Session 114.

### Operational

- **Asterisk routes extension 6001 to port 5060 specifically.** Registering
  successfully on another port is not enough: five calls in Sessions 85–86 never
  produced an INVITE purely because the endpoint was bound to 5062. Those sessions
  blamed the telephony path; that was wrong. Session 87.
- `_set_load_result` in `eicon_mips_shim.py` is dead code. It decoded zero loads
  out of 100627 data-port reads, before and after the Session 81 hook rework. IDMA
  read-back works through the `mem_write` page patch beside it. Session 81.

---

## 4. Reproduction

Build first — `libadsp2181.dylib` is gitignored:

```bash
make -C tools/adsp2181emu
```

**A live forced-V.34 call.** This reaches overlay `0x0261` deterministically —
both ends deny V.90 — instead of waiting for the DIL lottery to fall that way.
Identify the modems first; the device paths move between reboots:

```bash
/tmp/eicon-venv/bin/python tools/cx_at.py ident /dev/cu.usbserial-* /dev/cu.usbmodem*
```

```bash
EICON_MODULATION=v34,0,,33600,,33600 /tmp/eicon-venv/bin/python -u tools/eicon_adsp_sip.py --native-mips --force-info-after-v8 --native-bearer-activation --tx-prbs --law pcmu --sip-port 5060 --rtp-port 4000 --capture-prefix artifacts/interop/v34-live/callNN --mips-kernel artifacts/eicon-dsp/build-117-926/kernel/0009-diva-server-pri-30m-kernel --mips-tikrnl artifacts/eicon-dsp/build-117-926/tikrnl/0258-tikrnl81.f34-task --registrar asterisk.net.cryan.nz --username 6001 --password 6001
```

Then dial in from the analogue modem, which must also be denied V.90:

```bash
/tmp/eicon-venv/bin/python -u tools/cx_at.py --dev /dev/cu.usbmodem123456781 --setup 'AT&F' --setup 'AT+MS=V34,0,2400,33600' dial 6001 --wait 45
```

Read the INFO control channel straight off any capture, without the card. Both
directions are decoded, each at its own carrier, and only CRC-valid frames are
reported — over 24.5 s of non-control-channel signal plus 10 s of noise it
reports none, so a frame it prints was on the wire:

```bash
/tmp/eicon-venv/bin/python tools/v34_info.py artifacts/interop/nldata-cx/abifix-2.rx.ulaw --from 3 --to 6
```

Offline replay of the echo-canceller failure, which is where all the tracing above
was done:

```bash
EICON_V90D_BULK_ADAPTER=1 EICON_MIPS_WARMUP=0 /tmp/eicon-venv/bin/python tools/v90_dpcm_replay.py artifacts/interop/usr-v92-21240/call1.rx.ulaw --to 20.0 --tx-prbs --native-bearer-activation
```

A live Courier call. **Port 5060, extension 6001**, and check nothing else holds
the port first:

```bash
EICON_RX_TRACE=artifacts/interop/courier-v42/callNN.rxd /tmp/eicon-venv/bin/python -u tools/eicon_adsp_sip.py --native-mips --force-info-after-v8 --native-bearer-activation --tx-v42 --v42-pty --law pcmu --sip-port 5060 --rtp-port 4000 --capture-prefix artifacts/interop/courier-v42/callNN --mips-kernel artifacts/eicon-dsp/build-117-926/kernel/0009-diva-server-pri-30m-kernel --mips-tikrnl artifacts/eicon-dsp/build-117-926/tikrnl/0258-tikrnl81.f34-task --registrar asterisk.net.cryan.nz --username 6001 --password 6001
```

Keep the endpoint log; the `[v42] RX`/`[v42] TX` lines carry the frame bytes in
both directions and are what settles an establishment question afterwards.
Score the `.rxd` trace with `tools/rx_frame_search.py` if framing is in doubt.

Dial from the `v90modem` checkout. **Check which modem is on which port first** —
they have moved. In the live V.42 closure the CX93001-EIS V0.2013 V92 was on
`/dev/cu.usbmodem123456781`; the **Courier V.Everything (ROM 5607A) was on
`/dev/cu.usbserial-21240`**.

Courier: `&M4` asks for error control (`&M0` is the raw comparison), `&K0`
disables compression, `&A3` makes it report the negotiated protocol on CONNECT.
`ATW2`, which an earlier version of this section recommended, is a Conexant
command and the Courier answers `ERROR`.

```bash
./.venv/bin/python tools/cx_at.py --dev /dev/cu.usbserial-21240 --setup 'AT&M4&K0X4&A3' dial 6001 --wait 75
```

On a CX, `S48=0` forces LAPM and skips the detection phase, `S48=7` exercises
the Session 86 detection work, and `X4W2` is what makes it report the protocol
at all — it defaults to `W0 X3`.

**Leave about 20 s after the endpoint registers before dialling.** Dial sooner
and the Courier reports `NO CARRIER` with no INVITE reaching the endpoint at
all; two calls in the twelve-call run were lost that way before it was noticed.

`usrdiag` is the purpose-built superset if you want everything, and `ATI6`/
`ATI11` are the two-line version — Courier commands both, which a CX answers
`OK`/`ERROR`:

```bash
./.venv/bin/python tools/cx_at.py --dev /dev/cu.usbserial-21240 cmd 'ATI6' 'ATI11'
```

Hold the serial port for nothing else while a call is running — a second reader
splits the AT responses and the results become uninterpretable.

### Reading a capture

- `.endpoint.log` — `[dil]`, `[adsp]`, `[v42]`, `[media]` lines. `[capture] wrote`
  with **no** `[call] ended` above it means the endpoint died mid-call; since
  Session 83 that is reported as `[call] media fault` with the page instead.
- `.adsp.csv` — one row per RTP packet, including `dil_flag`/`dil_count`/
  `dil_measure`.
- `.adsp-dm.bin` — `EADSPDM2` header then `uint64 sample + 256 uint16 LE` per
  record, covering DM `0x3ee0..0x3fdf`. Read-database word `+0xNN` is index
  `128 + 0xNN`. `+0x01` is the rate word: bit 5 set means V.90, and
  `21 + (value & 0x1f)` is bits per datagram. `0x2028` → 29 bits → 38666 bit/s.

---

## 5. Technique

What actually produced results here, in order of usefulness:

0. **On the MIPS side**, `--scan-ram` (where did a value we chose end up),
   `--watch-mem ADDR[:LEN]` (who wrote it) and `--hook-call ADDR` (is this
   routine reached, with what). Two traps, both paid for in Session 97: keep
   `--watch-mem` ranges narrow, because a wide one changes Unicorn's block
   boundaries enough to break the run; and give `--hook-call` *virtual*
   addresses, because the PC stays in kseg0 while write hooks report physical.
   Always hook a known-executed address as a positive control -- a masked
   address silently reports zero entries for everything.

1. **DM write watches** (`adsp2181_watch_dm`). The output line carries `ppc`, the
   *writer's* PC. This settles "who wrote this word" in one run and is how
   PM `0x1982`'s stores, the `DM(0x1ff7)` corruption and the bulk-length writers
   were all attributed. Prefer it over inferring from an `I` register walk — two
   inferences in this chain turned out to need correcting.
2. **Exec watches** (`adsp2181_watch_exec`). Full register dump per execution:
   `i0 i1 i4 i5 m1 m3 l0 b0 ax0 ax1 ay0 af ar mr0 mr1 sr0 sr1 si se rx0`, plus
   `astat` and the three stack pointers. Note `m0`, `m2` and `m4..m7` are **not**
   printed; infer them from consecutive `i` values.
3. **`tools/adsp2181_dis.py <pm.bin> <start> <end>`**, on a PM image dumped from a
   live core at the page you care about. It shares the emulator's dispatch tables,
   so it decodes what actually executes. `pm.bin` is 3 bytes per word, little-end
   first, `0x4000` words. The standalone `dasm/` binary mis-decodes some overlay
   pages — the README says so and it is true.
4. **Offline replay is open loop.** The recorded RX already contains the peer's
   responses, so it cannot answer any question about what the card *advertises* or
   how a peer *reacts*. Session 82 forgot this and drew a wrong conclusion from it.
   Questions of that shape need a call.

---

## 6. Next steps, ranked

0. **Read `dsp30_assign` around `0x800a8c20` for the SIG mailbox layout**
   (Sessions 96–97). It is the boundary worth high-level emulating: the payload
   crossing it is standard Q.921 framing around standard Q.931, so standing in
   for the far side makes both call directions work and maps mechanically onto
   SIP.

   Session 97 established the ground truth it rests on. `dsp30_assign` is
   **never called** in either mode, so the D-channel framing tasks never run and
   there is no transport a SETUP could use; and the outgoing path never reaches
   DSP assignment at all, where the answering path enters `dsp_assign` 32 times.
   The card never emits any Q.931, so standing in as the network cannot be done
   at that level — it has to be done underneath. Also worth finding: what would
   call `dsp30_assign`, since on a real card something brings the span up at
   start of day and that trigger is absent here.

   Do not look for a transmit queue by scanning for message content: no message
   is ever assembled. The dialled number reaches only the call record, as an
   isolated length-prefixed field at `0x80100875`.

   **`dsp30_assign` is registered in the service table at `0x8012227c` and then
   released during boot** (Session 98), by the guard at `0x800822e8`: the
   per-DSP scan runs indices `0x00..0x1c` and only index `0x1c` yields
   `v0 = 0`, which releases the 30-channel service.

   Session 98 guessed the failing DSP was the one `report_dsp_boot()` called
   "1 still held". **Session 99 disproved that**: the held core was a phantom
   the emulation invented for a card control register at `0xbc000020`, it is
   gone now (`30 cores: 30 answered, 0 still held`), and the release fires
   exactly as before. Do not re-try that.

   Open: what sets `v0 = 0` for index `0x1c`. The download/ack routines at
   `0x80082250`/`0x80082260` are never entered on this path, so Session 98's
   reading of what the guard tests is unproven. Hook the scan's *function
   entry* — `ra` on a mid-function hook is stale and will mislead.

   In the meantime the loopback rig can be unblocked without any of that, by
   forcing `DM(0x0554) >= 0x10` as an explicit harness "line connected" signal.
   That is the same class of intervention as the injected SETUP already in the
   tree. It starts transmission but has not yet been followed through to a V.8
   request.

   **This now works on the native MIPS path** (`EICON_ORIGINATE_LINE_READY`,
   on by default for the calling role; `--originate-line-ready`/
   `--no-originate-line-ready` on `eicon_adsp_sip.py` and `eicon_loopback.py`).
   The caller reaches `TrnProgress 0x0051` (training start, DSR raised) and
   transmits non-silence on the line. The missing thing was that the dial page
   has **two gates in sequence**, found by disassembling the resident PM:

   ```
   35d7: AY0=DM(046C); IF LT JUMP 35DD         ; first gate
   35da: AR=DM(0554); AR=AR-0x10; IF LT RTS     ; need 0554 >= 0x10
   35dd: AX0=$35ED; DM(03EF)=AX0; ... JUMP 36CC ; proceed; set next cont.
   35ed: CALL 3851; IF GT RTS                   ; second gate
   35ef: AR=DM(046C); IF LT RTS                 ; need 046C >= 0
   35f2: AR=DM(0554); AR+0; IF NE RTS           ; need 0554 == 0  (!)
   35fa: ... AR=0x51; DM(3FC2)=AR               ; TrnProgress = 0x0051
   ```

   The first gate needs `DM(0x0554) >= 0x10` ("line connected"); the second
   gate -- reached the next frame via `DM(0x03EF)=0x35ed` -- needs
   `DM(0x0554) == 0` (`IF NE RTS`). The earlier pin held `0x20` forever, which
   passed the first gate but made the second gate's `IF NE RTS` fire, so
   `TrnProgress` never reached `0x0051`. The fix gates the pin on
   `DM(0x03EF)`: pin `0x20` and NOP the scan tail `PM 0x3a36` (the sole writer
   of `0554`, which zeroes it every frame) while `DM(03EF)==0x35d7`; then
   un-pin and restore `0x3a36` so the scan zeroes `0554` for the second gate.
   The NOP is reapplied each frame because the dial-page overlay reloads into
   `a->program` on page entry.

   What remains: the caller parks at `0x0051` and the V.8 overlay is not
   requested from this path -- the next thing to follow is what moves the
   caller off page 12 onto V.8 once the dial page has reported the line
   connected. `--watch-exec`/`--watch-dm` on both harnesses are the tooling that
   established the two-gate structure.

   **Follow-up: the V.8 request, and why the originate firmware never makes
   it.** DM write + exec watches show the originate side **never writes the
   page-request words** `DM(0x3131)`/`DM(0x3132)` at all (zero writes over the
   whole run), and never reaches the kernel page-request routine `PM 0x0680`\   (0 exec hits, vs 4 on the answerer). That routine indexes a DM table at
   `0x315d` by the current bootpage and stores the next overlay in
   `DM(0x3132)`; the answerer's SIG overlay dispatches it through the kernel
   foreground (`0x02a8` loop: poll `DM(0x2E44)!=DM(0x2E45)`, then `I4=SR0;
   IF EQ CALL (I4)`), the caller's never does. The dial page's `0x0051`
   training-start sets the next continuation and returns; it does not call
   `0x0680`. The legitimate path is an **AT dial script** (ATD -> dial ->
   remote answers -> request V.8), which this SIP loopback bypasses by injecting
   the SETUP.

   `EICON_ORIGINATE_V8` (on by default for the calling role; CLI
   `--originate-v8`/`--no-originate-v8` on both harnesses) stands in for that
   script: once the caller reaches `TrnProgress 0x0051` on the SIG overlay
   (`0x0271`) with no firmware page request pending, it writes
   `DM(0x3131)=1, DM(0x3132)=0x025f` -- the same class of harness intervention
   as the dial-tone pin and `force_info_after_v8`. Confirmed in loopback: both
   ends then load V.8 (`0x025f`), the caller walks `0x0001->0x0002->0x0003->
   0x0004` and the answerer cycles `0x0022<->0x0026<->0x0024<->0x0028` -- they
   are negotiating through V.8, not yet locked. The next stall is the V.8
   handshake itself, not call setup.
0.5. ~~**Find why successive transmit datagrams do not reach the peer
   distinctly.**~~ **Done.** TIKRNL and the host were both answering the same
   request; explicit host modes now suppress the resident task's five mailbox
   stores. The corrected V.14-framed raw harness recovers `ABCDEFGH` unchanged
   for 46268 consecutive octets. V.42 is no longer blocked on the transmit bit
   path.
0. **Find what gates state `0x0064`.** Session 114l fixed the DM corruption and
   V.34 still does not complete — the page now freezes at `TrnProgress 0x0064`
   instead of `0x0071`. Its block is `0x1afa`, whose test4 is index `0x0001`,
   the plain countdown at PM `0x2e32` decrementing the block's own field `0x0f`
   (`DM(0x2146)`, loaded with 288). With the table clean that should resolve
   correctly, so **`--watch-dm 0x2146` on a forced-V.34 call** is the direct
   probe: if it never decrements, the evaluator is not reaching test4 for this
   block. The block also arms `field 0x00 = 0x9601` and `field 0x0d = 0x4000`,
   neither yet identified.

   Settled, do not re-derive: the rate ceiling is not a variable; the frozen
   `TrnProgress` is not meaningful on its own; `DM(0x3F89) == 0` is **correct**
   and is the intended route (PM `0x2ef1` is state `0x0076`'s test0, branching
   to `0x0090`, which runs into `0x0096`, the only state that opens the gate at
   PM `0x285e`). Use the **answering** decoder for script work — the CX dials
   in, so the card reads high bytes: `tools/v34_script.py --role answer --base
   0x1a2e --terminator 0x19`.

1. ~~**Trace `I1` at PM `0x1917` and PM `0x1921`**~~ **Superseded.** The echo
   chain was retired by Session 113: quality `DM(0x0fcf)` is flat across a 10×
   range of bulk delay, so the zero-bound reading no longer gates anything. The
   remaining rate question is a receiver/line one.
2. ~~**Re-run the V.42 call on the plain mailbox path.**~~ **Done.** The CX's
   59-octet XID exposed the unlengthened `GI=ff` user-data parser bug. After the
   fix, the CX advanced through SABME/UA and exact payloads crossed in both
   directions. A future V.42 session should be a larger throughput/window soak
   or an `S48=7` detection-phase test, not another basic-establishment call.
3. ~~Re-run a raw-mode call on port 5060 to confirm the known-good path still
   reaches `0x00c6`/`0x00d0`.~~ **Done, and it does not.** Three `--tx-prbs`
   calls landed on `0x00b3`, `0x00b0` and `0x00c0`, the same three states as the
   nine `--tx-v42` calls in the same run. Read it as the lottery losing every
   draw rather than as a regression — those are the outcomes Sessions 88–93
   describe, and Session 87's success was one call — but it does mean **the
   data source makes no difference to how far a call gets**, and nothing above
   the physical layer can be tested until DIL passes. That makes the echo
   canceller (item 1) the only thing worth a session.
4. **Consider the card's own V.42** instead of the Python LAPM — stop setting
   `DLC_MODEMPROT_DISABLE_V42_V42BIS` and supply the B2 error-correcting
   negotiation block. Bigger change, moves the data path off the synchronous pump
   onto the protocol page, but it uses the shipped implementation, which is this
   project's premise.
5. **V.34 has not been looked at since the tree changed.** Session 79's PC-stack
   fix and Session 83's PM `0x06cd` restore both altered the per-frame kernel path,
   and a page-8 replay no longer raises where it used to. Worth re-baselining
   before treating any of Sessions 72–78 as current. When it is, the one CAI
   variant worth a call is the strict form, which disables the fallbacks as
   well as V.90 and is the only setting that changes the DSP assignment
   descriptor (§2a):

   ```bash
   EICON_MODULATION=v34,0,,33600,,33600 /tmp/eicon-venv/bin/python -u tools/eicon_adsp_sip.py --native-mips --force-info-after-v8 --native-bearer-activation --tx-prbs --law pcmu --sip-port 5060 --capture-prefix artifacts/interop/v34-strict/callNN --mips-kernel artifacts/eicon-dsp/build-117-926/kernel/0009-diva-server-pri-30m-kernel --mips-tikrnl artifacts/eicon-dsp/build-117-926/tikrnl/0258-tikrnl81.f34-task --registrar asterisk.net.cryan.nz --username 6001 --password 6001
   ```

   Do **not** spend a call on `v34,1,...`: §2a shows it is byte-identical to
   the old `EICON_FORCE_V34`, which has already been tried.

## 7. A caution about this chain

Between Sessions 88 and 93, four hypotheses were advanced and disproved: `L0`
being unset, an emulator flag defect, the PM `0x19c4` near/far fork, and far-bulk
being the branch that should have been taken. The *measurements* have all held —
every table above reproduces. The *interpretations* have repeatedly not.

Treat the ranked list as things to establish, not as things expected to be true.
