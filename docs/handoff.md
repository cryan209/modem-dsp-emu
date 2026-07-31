# Handoff: current state, live blockers, and what has been disproved

Written at Session 93. The running log in `eicon_adsp_firmware_analysis.md` is
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

Three blockers are live:

| blocker | status | where |
|---|---|---|
| **V.34 does not connect at all** | open, uninvestigated since the tree changed | Sessions 72–79 |
| **the calling side never trains** | open; `GEN_SETUP1=0x048c` is not supported on this product | Sessions 95–96, §2b |
| **V.90 needs `--native-bearer-activation`** | open, cause unknown | Session 67, 87 |
| **DIL is a lottery**; if it passes, the call works | open; echo canceller is the leading hypothesis | Sessions 88–93 |

Reported behaviour, which matches the captures: calls reach DIL, then either
continue (and work well), attempt a retrain, or stall. `0x00b3` is the stall
state; `0x00c6`/`0x00d0` are success; `0x00c0` is a partial.

### V.42 / terminal

A LAPM transmitter and a PTY terminal exist (`--tx-v42 --v42-pty`, Sessions 84
and 86). The framing, window, go-back-N and the V.42 §7.2.1 answerer detection
phase are unit-tested (21 tests in `tests/test_v42_lapm.py`).

**None of it has ever been exercised against hardware.** No SABME has ever
arrived. In Session 87 the data path did open for the first time
(`_lapm_active` true, TX 29 bits/datagram, RX 13) and the decoder ran for 44 s
producing `HDLC good/bad/abort = 0/2/15` — 17 framing attempts, no valid frame.
Two candidates, unresolved:

- the peer connected `Protocol NONE` (a Courier reported exactly that in
  Session 86, and `ati6` was not captured in Session 87); or
- `_service_rx_data()` misframes. It takes 13 bits per datagram MSB-first from
  RXD; a wrong order or count produces precisely this signature.

**Next run must capture `ati6` immediately after the call.** `Protocol` plus the
octet/block counters separate those two in one line. Without it this is guesswork.

Note also that `modem_nl_assign_payload()` sets
`DLC_MODEMPROT_DISABLE_V42_V42BIS`, so the **card's own V.42 is switched off** and
this Python is the V.42 entity. Using the firmware's implementation instead has
never been tried and may well be less work than making ours interoperate;
Session 86 sketches it, and `EICON_CARD_V42=1` now sends the payload for it.

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

## 2. The echo canceller chain (Sessions 58 → 93)

The near/far echo bulk-delay adapter at PM `0x1900..0x19c8` is the card's echo
canceller. **This harness disables it** by RTSing out its tail on every page-14
load (`EICON_V90D_BULK_ADAPTER=1` re-enables). That is a real functional gap:
the test path is SIP/RTP → ATA → two-wire → modem, so there is a hybrid producing
exactly the echo it exists to cancel, and the card must recover the analogue
upstream from it. It is the leading hypothesis for the DIL lottery.

It cannot simply be enabled. With it live:

| configuration | outer state walk | outcome |
|---|---|---|
| disabled (default) | `…0068 006a 0070…007a 007b 007c 0080 00a6 00b0 00b1 00b2` | clean |
| enabled | `…0068` then `0fc2`, `78f8` | state word garbage |
| enabled + `--prime-v90d-bulk-cursor` | `…0068` | stalls |
| enabled + far-bulk forced (PM `0x19c4` NOPed) | `…0060 0000 0062 0001 0050` | restarts |

### The established mechanism

1. PM `0x1930` is the adapter's store. Its destination `I0` sweeps `0x0049` to
   `0x1b41`, 1556 distinct addresses (exec watch, Session 90).
2. The V90D outer record table is inside that range: the record pointer
   `DM(0x120f)` walks `0x18ba → 0x18cc → 0x18d8 → 0x18e7 → 0x18f6 → 0x1902`
   then jumps to `0x1b51`.
3. The fill flattens the records. The sequencer reads a zeroed record and
   publishes an impossible next state — confirmed by DM watch: `DM(0x1ff7)` is
   written `0x0fc2` by **PM `0x2fea`, the sequencer's own state store**, so
   nothing overwrote the word; it read garbage.
4. This is Session 65's collision with a new victim. There it reached
   `DM(0x3fad)`/`DM(0x3fb3)` and killed `Core8kRoutine`; that no longer
   reproduces (Session 88), plausibly because of Session 79's PC-stack fix and
   Session 83's PM `0x06cd` restore.

### PM 0x1982, fully traced (Session 90)

Writes, each confirmed by DM write watch naming the writer PC — not inferred:

```text
1987 → DM7 = 0001      199b → DM0 = 03cd      199d → DM2 = 0000
199e → DM3 = 0001      19a3 → DM6 = 0000      19a5 → DM4 = 0000
```

`DM4 = 0` is the **intended** output. `DM4 = (AX0 OR AY0) AND NOT AY1`; `AX0` is
`0` from PM `0x1991` unless PM `0x1999` sets it to `4`, and `0x1999` is only
reached by falling through `IF GE JUMP $199A` at PM `0x1997`. With
`Nearbulklength = 0x03cd` (positive), the branch is taken. PM `0x1935` then
advances the cursor from `0` normally — observed 640 times with `0, 1, 2, …`.

### The bulk length inputs (Session 93)

No host ever writes them. Two alternating on-chip writers each, per frame:
`PM 0x1a13`/`0x19e2` → `DM(0x3fbc)`, `PM 0x1a18`/`0x19e4` → `DM(0x3fbd)`.

They derive from `delaycorrection`, write-DB `+0x24` (`DM 0x3f04`), supplied by
the card's own 256-word DATABASE transfer as `0x000c`:

```text
Nearbulklength = 0x03c1 + delaycorrection
BulkLength     = Nearbulklength + 0x50
```

Verified at `0x0000`, `0x000c`, `0x0040`. This is a span-delay calibration — the
T1/E1-shaped host input Sessions 58–67 were looking for. **It does not change the
failure**: those three values give an identical workspace apart from `DM0`, and
the same stall.

### The one unverified assumption

`AY0`, the modulo bound that reads zero at PM `0x1922` and `0x1926` (so the
`IF NOT AC` wrap corrections fire **0 times against 597 skips**), was attributed
to `DM5`/`DM6` **by inference from the workspace contents**, never by tracing
`I1` at the two read sites, PM `0x1917` and PM `0x1921`.

That attribution is load-bearing for the whole "zero modulo bound" reading and it
has never been checked. **Trace `I1` at those two instructions first.** The
`[EXEC]` line carries `i1`; it is one run, same technique as Session 90.

- If `AY0` comes from a word that is legitimately non-zero in a working
  configuration, the fault moves and the zero-bound reading was wrong.
- If it really is `DM6`, then near-bulk genuinely configures no bound, and the
  question becomes what else was meant to limit PM `0x1930`.

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

Offline replay of the echo-canceller failure, which is where all the tracing above
was done:

```bash
EICON_V90D_BULK_ADAPTER=1 EICON_MIPS_WARMUP=0 /tmp/eicon-venv/bin/python tools/v90_dpcm_replay.py artifacts/interop/usr-v92-21240/call1.rx.ulaw --to 20.0 --tx-prbs --native-bearer-activation
```

A live Courier call. **Port 5060, extension 6001**, and check nothing else holds
the port first:

```bash
/tmp/eicon-venv/bin/python -u tools/eicon_adsp_sip.py --native-mips --force-info-after-v8 --native-bearer-activation --tx-v42 --v42-pty --law pcmu --sip-port 5060 --rtp-port 4000 --capture-prefix artifacts/interop/courier-v42/callNN --mips-kernel artifacts/eicon-dsp/build-117-926/kernel/0009-diva-server-pri-30m-kernel --mips-tikrnl artifacts/eicon-dsp/build-117-926/tikrnl/0258-tikrnl81.f34-task --registrar asterisk.net.cryan.nz --username 6001 --password 6001
```

Dial from the `v90modem` checkout. `S48=0` forces LAPM and skips the detection
phase; `S48=7` exercises the Session 86 detection work:

```bash
./.venv/bin/python tools/cx_at.py --dev /dev/cu.usbserial-21210 --setup 'AT&M4&K0S48=0' dial 6001 --wait 80
```

Then, immediately, the readout this investigation keeps needing — verified
working. `ATI6` gives `Protocol` and the octet/block counters, `ATI11` the
modulation and rate:

```bash
./.venv/bin/python tools/cx_at.py --dev /dev/cu.usbserial-21210 cmd 'ATI6' 'ATI11'
```

`usrdiag` is the purpose-built superset if you want everything:

```bash
./.venv/bin/python tools/cx_at.py --dev /dev/cu.usbserial-21210 usrdiag
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

   **This is now implemented** (`EICON_ORIGINATE_LINE_READY`, on by default for
   the calling role; `--originate-line-ready`/`--no-originate-line-ready` on
   `eicon_adsp_sip.py` and `eicon_loopback.py`). `NativeMipsModem._frame_core`
   pins `DM(0x0554)=0x20` while the calling side is parked at the dial page
   (`TrnProgress 0x0002`), logs the pin and the advance, and stops once it
   leaves the park. So the caller no longer waits on the dial-tone/DTMF tone
   detector a PRI never arms — it skips to transmission. What remains, per
   Session 95, is that the V.8 overlay is still not requested from this path,
   so the next thing to follow is what moves the caller off page 12 onto V.8
   once the dial page has reported the line connected.
1. **Trace `I1` at PM `0x1917` and PM `0x1921`** to establish which workspace
   offset `AY0` is actually read from. One run. It either confirms or dismantles
   the zero-bound reading that Sessions 91–93 rest on, and everything else in the
   echo-canceller chain waits on it.
2. **Capture `ati6` on the next live call.** Settles whether the V.42 silence is
   the peer refusing error control or our RX misframing. One command.
3. **Re-run a raw-mode call on port 5060** to confirm the known-good path still
   reaches `0x00c6`/`0x00d0` on the current tree. This regression check is still
   owed: both attempts in Session 85 failed to route and it has not been redone.
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
