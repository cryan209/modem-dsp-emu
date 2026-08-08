# Handoff: read this first

Current to **Session 209**. `eicon_adsp_firmware_analysis.md` is the index to the
running log — 209 sessions in six volumes under `analysis/`, the record of *how*
things were established. This is the current picture. Where they disagree, this
one is newer.

The value here is §3 and §4: what has already been ruled out, and the traps that
have each cost at least one session. Several §3 entries cost several.

---

## 0. How to work here

Sessions 190–194 spent four sessions inside the emulator to reach a fact that
`tools/v34_info.py` prints in one command from a table in
`docs/ITU Docs/T-REC-V.90`. That is the failure mode this section exists to
stop.

0. **Search the log's subject index first.** Sessions 190–194 re-derived the
   V.90 page decision — same PM addresses, same Recommendation citation — from a
   volume-02 entry written in the unnumbered 23–30 stretch (`6788c56`; the old
   "around Session 25" dating does not survive the commit order). The record was
   complete; nobody
   could find it. `docs/eicon_adsp_firmware_analysis.md` is now an index.
1. **Order of attack: spec → driver → existing tools → capture → emulator.**
   The firmware implements published Recommendations, so a field usually has a
   name in `docs/ITU Docs/` before it has an address here. `divas4linux-master/`
   is the driver's own source. Both are ground truth in a way our
   reverse-engineering is not. The emulator is the instrument of last resort.
2. **Walk causality backwards from the effect; do not diff state and guess.**
   The DM write watch reports the *writer's* PC plus the 24 preceding PCs, so
   "who wrote this word" is one run. Chain it: Session 193 went from the overlay
   request to the received line bits in five hops, each one pinned. Session 191
   diffed 3,021 DM words, picked 20 that looked like control, and all 20 died at
   once (192). Diff state only when there is no effect to anchor on.
3. **A heading may only assert what an A/B or a spec citation establishes.**
   Session 191's heading named a branch it had inferred from a set-diff; it was
   two arithmetic helpers. Headings are what a skim reads, and this one is in the
   git log too.
4. **Every negative needs a positive control.** A watch that fires zero times
   and a watch that was never armed look identical. Name a control that must
   fire.
5. **Treat the ranked list in §7 as things to establish, not things expected to
   be true.** Between Sessions 88 and 93 four hypotheses were advanced and
   disproved; between 190 and 192, four more. The *measurements* have almost all
   held and reproduce. The *interpretations* repeatedly have not.

---

## 1. Where things stand

The card connects. It has reached full V.90 data mode against two different
analogue modems; one call in Session 87 walked to `TrnProgress 0x00d0` at
38666/24000 with DCD, CTS and both speed flags. Session 190 carried a live PPP
dial-in over it — LCP, CHAP, IPCP, 182 s, four NAT flows.

**The current question is why the CX93001 never gets V.90, and Sessions 194–195
answered it: it does not ask for it, and that is not our doing.** The chain, all
established by watchpoint and confirmed against the Recommendation:

```text
received bits → deserialise + bit-reverse (PM 0x358e..0x3599) → DM(0x060B)
  → bits 9..11        (PM 0x3e14..0x3e18)  → DM(0x170B)   6 or 4
  → packed bits 4..6  (PM 0x3de9..0x3df1)  → DM(0x3FBB)   0x3064 / 0x3044
  → == 0x60 ? 14 : 13/8 (PM 0x3304..0x3310)→ DM(0x16B6)   bootpage 14 or 8
  → copied            (PM 0x217d/0x217e)   → DM(0x3FB0)
  → kernel posts      (PM 0x069a/0x069b)   → DM(0x3131)/DM(0x3132)
  → the host serves overlay 0x026a (V.90) or 0x0261 (V.34)
```

Those three bits are **INFO1a bits 37:39**. Table 10/V.90: "Bits 37:39
represent the integer 6, indicating that V.90 operation is desired"; Table 11,
0–5 means V.34 is desired. The Courier sends 6, the Conexant sends 4 (cx02) and
5 (cx01). The card's INFO0d is **bit-identical on both calls** and is the 30-bit
V.90 form, so the offer is not the variable. `PM 0x3304..0x330f` implements
Table 10 correctly. The decision is the modem's, and the card is spec-correct —
confirmed in Session 195 by an independent spandsp V.90 server reading the same
4 from the same modem on the same line. It *offers* V.90 in V.8
(`mods=V90|V34|V22`, `pcm=0x1`) and declines PCM in INFO1a, which is sent after
Phase 2 line probing, so what it is doing is **measuring**, not applying a
policy.

`EICON_FORCE_DM=0x16b6=0x000e@0x0260` moves the Conexant capture onto overlay
`0x026a` — the only thing that has ever moved it — but that is a patch over the
card's reading of the peer, and the replay is open loop, so it says nothing
about whether a live handshake would complete (193).

Other modulations: V.22bis completes and carries PPP end to end at 2400 bit/s
between two emulated ends (183–184). V.32 reaches `TrnProgress 0x00d0` after
Session 188e's partial-overlay fix but carries no data. V.34 reaches `0x00b0`
and the answerer's transmit chain halts there. V.90A is admitted by the PRI
firmware with `EICON_DSP_EXTRA_DOWNLOADS=0x026b` (134–135) and is queued behind
V.34 phase 2, not behind a file-set problem.

---

## 2. Live blockers

| blocker | status | where |
|---|---|---|
| **the CX93001 requests V.34, not V.90** | **understood, not fixed, and not ours.** Its INFO1a bits 37:39 are 4/5, never 6 — read identically by an independent spandsp V.90 server on the same line, so the rig is exonerated. It *offers* V.90 in V.8 and declines PCM in INFO1a, after Phase 2 probing, so it is measuring something — **and what it measures is not known.** No candidate impairment has evidence behind it | 190–195 |
| **the answering page stops publishing transmit data at `0x00b0`** | the live V.34 blocker. Both ends reach `0x00b0` through twenty states, then the answerer's transmit chain halts completely — no further `DM(0x224C)` requests, line frozen on one sample. Sessions 137–148 describe a regime that no longer exists; do not carry their wait-block, threshold or role-word findings forward | 149, **164** |
| **V.32: slmodemd measures our transmit at 8 dB and retrains every ~10.5 s** | the live blocker. `V32STC - SNR drop observed, SNR = 8 < threshold = 13`, eight consecutive drops, then `local retrain`, stepping 9600 → 7200 → 4800. **Not** the width, LAPM, level or the G.711 path: `--tx-prbs` reproduces it, a call that never writes the transmit mailbox reproduces it, +12 dB via `TD` changes nothing, and the encoder matches the ITU reference. **Nor the sample clock or the companding (205):** the page publishes exactly one line sample per 8 kHz tick, mean 1.000 over 147,625 ticks, and the card's own `PM 0x1810` encoder is 65157/65536 exact against ITU-T µ-law with no gross error. Our own receiver reports a pristine line the other way — `RXLevel` −10 dBm, MAE **0** at the slicer, `PeakPhasErr` 0, `FreqOffset` 0, `TimOffset` ±1, SNR 30–39.5 dB — so the impairment is one-directional and in our transmit. The mechanism is **not** established | 204, 205 |
| **and do not compare two ends without checking both are in the same phase** | three leads died to this in Session 204. The loopback SNR split was read on a silent line. A spectral comparison of the live call at 12.7 s showed our transmit broadband and slmodemd's a narrow 1800 Hz spike, which looks damning until you sample across the call: both ends alternate between broadband data (10-13 of 14 bins within 10 dB of peak) and a narrow training tone (1-3 bins), and they are seldom in data at the same instant. Sampled where both are, both are proper V.32 QAM. Align on slmodemd's own connect/retrain timestamps before comparing anything | 204 |
| **do not use the loopback SNR asymmetry as evidence** | a 22 dB / 16 dB split between the two roles, which did follow `GEN_SETUP1` bit 3 across a role swap, was measured at `TrnProgress 0x00ea` — where, with no data source configured, **both ends have stopped transmitting entirely** (0% non-silence after ~13 s of a 70 s call). There is nothing on the line to measure there, so those are stale or role-constant reads, not transmit quality. In the phase where audio is actually present both roles read 38.5–39.5 dB and there is no asymmetry. Any repeat must confirm the line is active in the window it measures | 204 |
| **V.32's earlier width story** | superseded. The width is now derived from `DATASTATESpeed` and tracks a peer through 9600 → 7200 → 4800; the 6..2 sweep that "produced zero frames" ran with a width the card never published and against a loopback, and its premise — that page 2 never writes `DM(0x3F61)`/`DM(0x3F62)` — is withdrawn. `tests/test_nl_data_bridge.py` still asserted the old constant width and had been failing since; it now encodes the derivation instead — the three measured `DATASTATESpeed` words, the 9600 → 7200 step, and the `None`-until-published gate | 184, 188e–188g, 204, 206 |
| **V.32's page-2 chain is under a counterfactual** | everything from 188n onward runs with `EICON_PIN_PM=0x3805=0x38ab00`. Every stage of the chain is shipped firmware (188m), so "the harness has the page in a state the real card would not be in" is still the likelier reading than a firmware defect | 188m–188y |
| **DIL is a lottery** | open; attempts can fail before either rate is published. **The archive cannot settle it:** of 28 non-empty DM captures only five ever load page 14, and every other `0x00d0` is page 2 (V.32) — mostly one call replayed. Use `tools/dil_database_scan.py --v90-only` before treating the archive as a sample | 88–93, 105–107, **207** |
| **the echo levels are published as zero because the conversion floors a negative** | **mechanism established (209).** All four level routines run every publisher pass and compute a negative dB — `mr1=0xff50`, −176, read off `[EXEC]` either side of the clamp — and `PM 0x0ede` (`IF LT MR = MX0 * 0`) floors negatives at zero, capping at `0x3F`. So `EcLevel = 0` means *below the bottom of the guide's scale*, not unmeasured. Pinning the accumulator across five magnitudes to full scale moves the raw result only to −116, still floored, so the scaling between accumulator units and the conversion is the defect and the input is not the lever. `RXLevel`'s kernel entry is the same; its live 49/50 comes from `PM 0x2200` in the overlay. Remaining: what `PM 0x0e67` interpolates against (`I4 = $0115`) — a table read | 207, 208, **209** |
| **the far echo accumulator is never accumulated** | open, and now the only live part of the echo question. `DM(0x10EF)/(0x10F0)` takes **three writes in 21 s** — one initialisation — while the total pair is written 68,500 times. The map around it is settled: the quality block is published by one kernel loop, `PM 0x29c1..0x29d3`, thirteen routines against `DM(0x3F78..0x3F84)`, with near = total − far at `PM 0x0eb0`. Separately, `SNRPROB` is written **once**, value 0, by `PM 0x3e57` — beside the `PM 0x3e63..0x3e7d` conversion §7.1 is about | 207, 208 |
| **V.90 needs `--native-bearer-activation`** | open, cause unknown | 67, 87 |
| **V.34 upstream stays at 7,200** | open, and **not** the echo canceller — quality `DM(0x0fcf)` is flat across a 10× range of bulk delay. A receiver/line question | 113 |
| **exact upstream rate falls outside the final quality ceiling** | guarded, live-selected at 12,000; bilateral proof pending | 107, 109–110 |
| **the card's own V.42 has never been tried** | `modem_nl_assign_payload()` sets `DLC_MODEMPROT_DISABLE_V42_V42BIS`, so the Python LAPM is the entity. `EICON_CARD_V42=1` sends the payload for the alternative. Optional investigation, not a workaround | 86 |

Fixed, recorded so the symptom is recognisable if it returns: the page-8
transmitter decimated by ten (149), the V.34 page freeze from the native bulk
worker overwriting `DM(0x00A8..0x00A9)` (115j–l, `EICON_V34_PORTABLE_BULK` now
default), the zero-length echo bulk delay (112–113), `PortableBulkDelay` writing
over the per-frame dispatch vector at `DM(0x3fb8)` (113), the V.32 partial
overlay never being served (185, 188e), and the runaway log line that cost the
rig 15% of its wall clock (190).

---

## 3. Already disproved — do not re-derive

**The V.90/Conexant question (190–195)**

- **Input gain and receive level are not the mechanism.** The successful PPP
  call is on the *hot* port and the failures on the quiet one; modem AGC covers
  the range. 190.
- **Port 2/3 vs 2/5 cannot explain it either.** The only difference is
  `input gain 6`, which is upstream; the V.90 request is a downstream judgement.
  Both ports carry the same `output attenuation -6`. 194.
- **Not the missing APCM download.** `EICON_DSP_EXTRA_DOWNLOADS=0x026b` changes
  nothing here and `0x026b` is never requested. 191.
- **Not our transmit gaps.** A Conexant call with `substituted=0` and zero clock
  holds still took V.34; a Windows call with 27 holds and 54 ms of gaps reached
  `0x00d0`. 191.
- **Not the modem's offer.** `AT+MS=V34` and `AT+MS=V90,1,,56000,,33600` are both
  accepted by the CX93001 and neither changes which page the card picks. 190–191.
- **Not the V.8 menu.** `DM(0x3F09)` is `0xb13f` on both calls — the card offers
  the same menu to the peer that gets V.90 and the one that does not. 191.
- **Not the `PM 0x1cb9` loop.** It runs 39 times on one call and once on the
  other, inside a single frame, and converges to the same two DM values either
  way. 191's "strongest candidate" withdrawn.
- **Not the DM control block.** All twenty differing words around the
  measurement, forced *jointly* to the V.90 call's values, leave the outcome
  identical to the sample — while changing 105 PM addresses of coverage, so the
  patch was live. 192.
- **`PM 0x2bc1` and `PM 0x2b9a` are not the V.90 branch.** They are the
  1/√2 correction arm of a `sqrt()` and the negate arm of an `abs()`, in two
  library subroutines called four times each on both captures. Session 191's
  headline is withdrawn. 192.
- **`DM(0x3F9C..0x3F9F)` is a log, not control** — `PM 0x2b6a..0x2b6f` is a
  four-deep ring buffer (`I0 = DM($3FCC)`, `L0 = 4`, store, write back). 193.
- **Not our downstream, and not a disabled capability in the modem.** An
  independent PJSIP+spandsp V.90 server (`../v90modem`) on the same port 2/3
  reads `downstream code=4, not 6` from the same modem, and its V.8 result shows
  the Conexant *offering* `V90|V34|V22` with `pcm=0x1`. So the modem is willing
  until Phase 2, and nothing this project transmits is what it rejects. 195.

**The V.32 transmit asymmetry (205)**

- **The transmit is not decimated, and the sample clock is not the mechanism.**
  The LEC page publishes **exactly one line sample per 8 kHz tick** — mean
  1.000 over 147,625 ticks on the calling end and 147,551 on the answering one,
  with both ends in the data state. So Session 149's page-8 defect (9–12
  publishes a tick, transmitter decimated by ten) has **no analogue here**. The
  census is free and on by default; a run where it reports 0 ticks means the
  page was never resident, which is not the same statement. 205.
- **The live transmit companding is correct.** It is the card's own encoder at
  `PM 0x1810` on an independent core, not ours: swept against ITU-T over all
  65536 inputs *with the law configured as the live path configures it*, µ-law
  is 65157/65536 exact, worst reconstruction error 644, **zero gross errors**.
  This closes the G.711 path with a positive control instead of by assertion.
  205.

**Modulation selection**

- **Nothing in the CAI path selects a modulation.** `EICON_MODULATION` /
  `--modulation` build the CAI correctly and it reaches the DSP, but it is not
  what V.8 reads; pinning NORM_L does not force one either. `AT+MS` on the
  *calling* modem does reach V.8. 183, 190.
- **`unused_modulations` does nothing.** `v34,1,,33600,,33600` produces host
  writes byte-identical to the old one-bit `EICON_FORCE_V34` — all 51,965. Not
  worth a live call. Measured on the `run34` A/B.
- **The write database is untouched by modulation config.** All 160 words
  identical in every configuration; the card authors those itself. 89.
- **`DM(0x3FC4)` *is* what selects the page** (the one positive result here):
  `0x0016`→V.22, `0x6000`→V.32, `0x0029`→FSK, `0x0040`→page 17, `0x0E00`→FAX,
  `0x0080`→page 20, default→INFO/V.34. 184.
- **"V.8's modulation selection depends on round-trip delay" is withdrawn** —
  it was the 1 s off-hook guard landing inside V.8 because loopback started both
  ends on the same instant. `--setup-gap-ms` (default 2000) fixes it, and 179–180
  are consequences rather than defects in their own right. 182.

**V.34 page 8 (Sessions 137–150 — a chain that mostly withdrew itself)**

- **`DM(0x2140)` is not the transmitter's enable.** Forcing it demonstrably
  opens the gate (a filter that never ran executes 880–70,464 times) and the
  caller stays silent. 139.
- **The role word `DM(0x2198)` is not a fix** — holding it at zero makes the
  calling end a second answerer. Script *content* is not the dependence either
  (two bits, both forced, still silent), and three of its four readers have zero
  executions on both ends. 140–142.
- **The correlator threshold is not the problem.** The latch sets ~2,400 times a
  run at the real threshold and forcing it to `0x0001` changes nothing. 143's
  reading is withdrawn; 147 has the correct one (the self-branch is the "stay
  here" arm, so a passing test keeps the card in the block). 146–147.
- **`V34_CYCLES_PER_SAMPLE` is eliminated as the cause.** The fraction of
  correlator invocations that latch is flat at 51–60% across a 13× sweep. It is
  still wrong, but it is tidiness. 148.
- **The paced transmit signal does not match hardware.** Session 149's 0.813
  was the concentration metric scoring a *stuck DC level*; in-passband both ends
  are 0.071/0.081 against hardware's 0.818. Never read a concentration number
  without its peak frequency. 150.
- **Transmit level does not predict outcome.** 144.

**The echo canceller chain**

- **`DM(0x32f7)`, the descriptor selector, is not the discriminator** — setting
  it to `8` at page-14 entry is byte-identical to `0`. 88.
- **`L0` is not missing.** `PM 0x19ac..0x19b1` sets `L0`, `L1` and `L4..L7` to
  zero deliberately; the firmware disables circular addressing. 92.
- **The emulator's flags are correct.** `shift_op` touches `ASTAT` only for
  `CFLAG`/`SS`, matching the ADSP-2100 shifter; `CALC_C_SUB` checks out against
  traced values. Unlike Sessions 46 and 52, not an emulator defect. 92.
- **`PM 0x1982` is not the fault**, and `--prime-v90d-bulk-cursor` overwrites a
  correct value. 90, 92.
- **The far-bulk branch is probably not the one that should have been taken.**
  It needs `Nearbulklength` negative, which is not a delay calibration. 93.
- **Call ingress is not the missing owner** — all transfers land, nothing is
  missing. Note it corrects an in-session probe that reported zero database
  writes, which was an instrumentation error. 89.

**V.90 capability words and the DIL lottery**

- **`V8_SETUP = 0x0000` is the firmware's own value**, not dropped by this
  harness. `EICON_WDB_OVERRIDE=0x04:0x6000` forces a value the firmware
  deliberately does not publish — a deviation, not a restoration. 82 was wrong,
  86 retracted it, 89 proved provenance.
- **The DSP co-authors the write database**, so a single snapshot cannot capture
  it. 89.
- **`DM(0x3f8b)` does not predict the DIL outcome.** It split perfectly over
  nine archived captures and the next live call broke it. It is one of six
  values the INFO overlay publishes, copied from `DM(0x0609)` — a phase-2 result
  field, not an independent measurement. 87, 102.
- **The peer really does send zeros in payload bits 6..12.** `DM(0x3F89) = 0` is
  a *correct* decode, on both the call that took V.34 and the one that took
  V.90, confirmed by CRC off the wire. **Do not spend another session recovering
  a value the peer never sent.** 114.
- **`DM(0x3F87)` is `RTDelay`, not a DIL count.** The `[dil]` line printed it as
  `count` from Session 87 to 207. It reads 6..0x1d plus the `0xffff` sentinel and
  changes 2–10 times a call: 60–290 ms of round trip, varying run to run on one
  rig, on a path whose V.8 classifier is already known to be delay-sensitive.
  Relabelled in the log line and in `.adsp.csv`. 207.
- **`DM(0x3F7C)` is not `FarEchoPhaseRoll` on page 14, and nothing measures phase
  roll at all.** The kernel's routine for it is the two-word stub `MR1 = 0; RTS`
  at `PM 0x0e8b`; the non-zero values are the V90D **inner state record**, which
  `PM 0x2fbf` stores there from `DM(0x2008)` 19,922 times against the stub's 69.
  So the values `0x0001` and `0x0040` are state numbers, Session 207's "measures
  phase roll but never level" is withdrawn, and it measures neither. 208.
- **A zero out of the quality-block publisher is a *floored* value.** `PM 0x0edc`
  clamps to `[0, 0x3F]` and `PM 0x0ede` turns any negative into zero. Every
  "never measured" reading of `EcLevel`, `NearEcLevel`, `FarEcLevel` and the
  kernel's `RXLevel` in Sessions 207–208 is superseded by this: the routines run
  69 times a call and compute −176, which publishes as `0x0000`. 209.
- **⚠ `[pin-dm]`'s hit count saturates at 8** (`pin_dm_hits[x] < 8` in
  `adsp2181_core.c` gates the increment). The pin still substitutes on every
  store; only "0 hits" carries information. Do not read "8 store(s) undone" as a
  count, and do not conclude a pin failed to hold because the number is small.
  209.
- **`PM 0x29d2` publishes zero for `RXLevel` too.** The live 49/50 at `DM(0x3F78)`
  comes from `PM 0x2200` in the overlay, 512 writes. On page 14 the kernel
  publisher is not the source of any level; the overlay writes the real ones over
  the top of it. Do not read a zero from the kernel publisher as "not measured
  anywhere". 208.
- **`Signalquality` is the constant 7.** `PM 0x336e` writes it 6,829 times and
  never anything else, which is why DIL-entry `0x0007` split the archive and
  means nothing. The withheld predictor is now dead rather than merely suspect.
  208.
- **`RTDelay` is written once per call**, by `PM 0x3303` — in the `PM 0x3304..0x3310`
  Table-10 neighbourhood, i.e. at V.8 handoff — value 14. A word written once at
  handoff is not a DIL counter, which settles trap 2 beyond the value ranges. 208.
- **`MinReduction_dbs` and DIL-entry `Signalquality` are not offered as
  predictors**, though both split the archive. `MinReduction_dbs` takes `0xff5d`
  and `0xf5dc`, which no transmission level can be, so the word is reused; and
  the `Signalquality` split is `DM(0x3f8b)`'s exact shape over a corpus whose
  successes are one page-2 call, already broken by `local01`. 207.
- **The two rate decodes in this file are one decode.** `DATASTATESpeed` bit D
  picks the speed mask: page-2 calls publish `0x11aa` (V.34 format, norm 13,
  9600) and page-14 calls `0x2029` (V.90 format, `speed_sel_V90_L` bit 9 =
  40000). §6's `21 + (value & 0x1f)` gives identical rates — `0x2028` → 38666 =
  `speed_sel_V90_L` bit 8. `rate_of()` in `tools/dil_database_scan.py`. 207.
- **The two directions do not share a control-channel carrier** — card 1200 Hz,
  peer 2400 Hz, both 600 bit/s, with the V.34 line probe between them. Decoding
  a `.rx.ulaw` at 1200 Hz alone recovers only our own echo and makes the peer
  look silent. 114.

**MIPS side and operational**

- **The host driver never touches the DSP.** `pri_telindus_load()` streams
  DM/PM blocks into card RAM and writes the descriptor table; no IDMA path, no
  PM or DM write anywhere in the kernel tree. It is not a suspect in anything.
  188m.
- **The "held DSP core" was a phantom** the emulation invented for a card
  control register at `0xbc000020`. Gone now; the service release fires exactly
  as before. Session 98 guessed it was the cause; 99 disproved it. **Do not
  re-try.**
- **`dsp30_assign` is never called** in either mode, so no Q.931 is ever
  emitted and standing in as the network cannot be done at that level. Do not
  look for a transmit queue by scanning for message content — no message is ever
  assembled. 96–98.
- **Asterisk routes extension 6001 to port 5060 specifically.** Registering on
  another port is not enough; five calls in Sessions 85–86 produced no INVITE
  for this reason alone, and those sessions blamed the telephony path. 87.
- **`_set_load_result` is dead code** — zero loads decoded out of 100,627 data
  port reads. 81.

---

## 4. Traps that have cost sessions

- **⚠ The core's `coverage_on` and `watch_gate` both default to ON.** A tool
  that opens with `gated = False` and only calls the gate on a *transition*
  never pushes it down, so every page before the one under test is counted as
  if it were on it. Fixed in `info_page_diff.py`, `loop_dm_writes.py`,
  `branch_frame.py`, `page_request_writer.py`; any gated count taken before
  Session 192 wants a re-run. 192, 194.
- **⚠ Gate watches to the page under test.** A PM address is a different
  instruction on each resident page. `EICON_WATCH_OVERLAY` disarms every watch
  until one of the named overlays is resident, and a disarmed limited watch does
  not decrement. Name the *composite* page — gating on `0x0266` alone disarms
  when the `0x0267` partial lands 5,441 cycles later, and every later zero then
  means "not looking". Three wrong readings in Session 188 alone. 188q.
- **⚠ Host writes are invisible to every DM watch.** The shim writes through
  `self.dm[...] = ...`, which does not go through `WWORD_DATA`, so no write
  watch in this log has ever shown one. "The harness does not touch this word"
  can only be established by reading the shim — its DM addresses are listed in
  Session 201.
- **⚠ `EICON_FORCE_DM` cannot test a word written and read inside one frame.**
  It writes once per sample before the page runs, so the firmware overwrites it
  before the consumer looks — and the end-of-run snapshot still shows the forced
  value, so it reads exactly like a clean negative. `DM(0x3FBB)`, `DM(0x170B)`
  (193) and `DM(0x0f6d..0x0f72)` (199–200) are all this shape. Use
  **`EICON_PIN_DM=ADDR=VALUE`**, which re-imposes after every store and reports
  its hit count; a pin with zero hits tested nothing. 200.
- **⚠ `EICON_TRACE_FRAMES` is armed against the shim's `_media_samples`, not a
  replay loop index.** They differ by 1653 at the point Session 192 needed, not
  by one. Aim it wrong and you trace a 407-instruction kernel frame that looks
  exactly like "the address does not execute here". `branch_frame.py` reports
  the right number. 192.
- **⚠ A watch limit is a ceiling on the log, not a measurement.** Session 146's
  "~2,400 latches" was `0x13bf:4000` hitting its limit exactly; uncapped it is
  142,734. 148.
- **⚠ `EICON_PM_DUMP` snapshots the *loaded* image, not the executing one.** It
  is written when the overlay becomes resident, so it predates run-time
  patching — which is why it reads `38ab00` at `PM 0x3805` while the core
  executes `38ae60`. **The `op=` field of a `[TRACE]`/`[EXEC]` line is the only
  ground truth for a PM word**, and reading opcodes out of a trace also
  sidesteps the disassembler's overlay mis-decode entirely. 188l, 188o, 192.
- **⚠ Session 185's "the partial `0x0267` is seven DM blocks and no PM at all"
  is WRONG.** `0x0267` rewrites program memory, so any page-2 disassembly taken
  at `0x0266`-load time is of the pre-partial image. 188.
- **⚠⚠ Log volume changes the answer.** The rig paces both endpoints to the wall
  clock, so host speed is an input to the emulation. A watch on a hot address
  moved the V.32 stall by 1.8 M cycles; clean runs reproduce exactly, host-bound
  runs are not even self-consistent. **Check the `[media]` line before quoting
  any number.** `tools/logcap.py` caps runaway sites and reports what it
  dropped. 188s, 190.
- **⚠ Run-to-run variance on V.32 is large.** Never compare V.32 measurements
  across runs; put them on one cycle axis in one call. 188g.
- **⚠ Offline replay is open loop.** The recorded RX already contains the peer's
  responses, so it can answer nothing about what the card advertises or how a
  peer reacts. Session 82 forgot this and drew a wrong conclusion. Questions of
  that shape need a call. Session 193's forced-`0x16B6` A/B is bounded by this.
- **⚠ On the MIPS side**, keep `--watch-mem` ranges narrow (a wide one changes
  Unicorn's block boundaries enough to break the run) and give `--hook-call`
  *virtual* addresses (the PC stays in kseg0 while write hooks report physical).
  Always hook a known-executed address as a positive control. 97.
- **⚠ `adsp_arith_oracle.py` needs the law configured, and did not configure
  it.** `boot()` leaves `DM(0x3309)` on the A-law table for this card, so
  `--law ulaw` swept the A-law encoder against the µ-law reference: **0/65536
  exact, 65472 gross errors**, which reads as a catastrophic firmware defect and
  is entirely the missing call. Fixed — it now sets the law from `--law` — but
  any µ-law result quoted from it before Session 205 is meaningless. 205.
- **⚠ Every host-side `linear_to_mulaw` saturated from −18.3 dBfs**, in all
  seven copies. The segment search shifted by 5 where a 16-bit input needs 8, so
  every magnitude at or above 3964 took the saturation arm. The standard probe
  stimulus — a 20000-amplitude 2100 Hz sine — was clipped on **87.5%** of its
  samples across 7 of its 33 codes, a square wave whose third harmonic aliases
  to ~1700 Hz at 8 kHz, 10.2 dB below the fundamental and right next to V.32's
  1800 Hz carrier. Now exact against ITU-T everywhere, guarded by
  `tests/test_g711_mulaw.py`. **This is not the live transmit path** (that is
  firmware), but every forced-G.711 probe and standalone drive in
  `docs/dial_*.md` ran on the clipped tone; whether any conclusion there
  depended on tone purity is untested. 205.
- **⚠ `AT&F` restores pulse dialling on some firmware**, and into an FXS port
  that does not decode loop disconnect every number comes back BUSY at the same
  speed — which reads as a dead route. Dial with `ATDT`. 190.
- **⚠ A withdrawn premise leaves its tests behind, and they fail in the language
  of the new code.** `be91b26` replaced page 2's constant datagram width with a
  derivation from `DATASTATESpeed` and updated the shim, this file and its own
  commit message — but not `tests/test_nl_data_bridge.py`, which still set
  `DM(0x3F62) = 0` as "the stale V.34 rate word" and asserted the constant. That
  assignment is now the input that *suppresses* the width, so two tests sat
  failing on `main` for two sessions. The second failed with
  `AttributeError: no attribute 'negotiated_downstream_bps'`, which reads as a
  dropped attribute and is nothing of the sort: it is initialised in
  `__init__()` and assigned when the pump latches, so its absence only means the
  transmit path bailed out earlier. **Making that error go away by adding the
  attribute would have preserved the withdrawn premise and buried the real
  failure one line further down.** When a session supersedes a measured premise,
  the tests encoding it are part of the supersession — and a test that fails on a
  missing attribute is describing where the code stopped, not what is missing.
  204, 206.

---

## 5. Instruments

In order of how much they have produced:

1. **DM write watches** (`adsp2181_watch_dm_writes`). The line carries `ppc`,
   the writer's PC, plus the 24 preceding PCs. This is the backward walk of §0.2
   and it settles "who wrote this word" in one run. Host writes bypass it, so
   the shim's own `self.dm[...] =` assignments do not appear — only firmware.
2. **Gated execution coverage** (`adsp2181_coverage_*`). Per-address counts with
   a residency gate. `tools/branch_frame.py` turns a count change into the
   sample *and* the `EICON_TRACE_FRAMES` value that traces it.
3. **Whole-frame instruction trace** (`EICON_TRACE_FRAMES` / `EICON_TRACE_BUDGET`).
   ~4,000 lines a frame, with `op=`, `ar`, `sr0/1` and `i4..i7` per instruction.
   Feed the `op=` words to `adsp2181_dis.disas()` directly.
4. **Forcing and pinning.** `EICON_FORCE_DM=ADDR=VALUE[@OVERLAY]` writes once
   per sample; `EICON_PIN_PM=ADDR=VALUE` holds a PM word against DSP stores.
   Both report their first hit, because a patch that never fires reads exactly
   like a negative result. **A force only works if the word is read in a later
   frame than it is written** — `DM(0x3FBB)` and `DM(0x170B)` are written and
   read ~70 cycles apart and cannot be held this way; `DM(0x16B6)` can.
5. **The LEC publish census** (`adsp2181_latched_dm_writes()`, reported as
   `[adsp] LEC transmit publishes per tick`). A counter in the store hook, not a
   watch, so unlike a write watch on a per-sample address it does not move the
   run (§4). Default on, and it cross-checks itself against the first-value
   latch so an unarmed census cannot masquerade as a silent page.
6. **`tools/v34_info.py`** — demodulates the phase-2 control channel off any
   capture in Python and accepts only CRC-valid frames, so it touches neither
   the firmware nor our emulation of it. This is the tool §0.1 is about.
7. **`tools/dil_database_scan.py`** — reads every guide-named database word out
   of the archived `.adsp-dm.bin` captures with no live call, since each holds
   all 256 words of the interface per RTP packet. `--span` gives the
   distinct-value count per word, which is what makes a zero readable as
   never-written rather than never-armed; `--v90-only` restricts to page 14.
8. **`tools/adsp2181_dis.py`** on a PM image dumped at the page you care about;
   it shares the emulator's dispatch tables. The standalone `dasm/` binary
   mis-decodes some overlay pages.

---

## 6. Reproduction

Build first — `libadsp2181.dylib` is gitignored:

```bash
make -C tools/adsp2181emu
```

Run profiles live in `profiles.toml`; `./run --list` shows them and `./run -n
<profile>` prints the resolved command without running it. That output is what
belongs in a session entry. The standard live-call harness is:

```bash
./run native-tower --run 42
```

Identify the modems before dialling — device paths move across reboots:

```bash
/tmp/eicon-venv/bin/python tools/cx_at.py ident /dev/cu.usbserial-* /dev/cu.usbmodem*
```

```bash
/tmp/eicon-venv/bin/python tools/cx_at.py --dev /dev/cu.usbmodem123456781 dial 6001# --wait 75
```

Modem notes: on the CX93001, `S48=0` forces LAPM and skips detection, `S48=7`
exercises it, and `X4W2` is what makes it report the protocol at all. On the
Courier, `&M4` asks for error control, `&K0` disables compression, `&A3` reports
the negotiated protocol; `ATW2` is a Conexant command and the Courier answers
`ERROR`. `ATDT6001#` connects immediately — `#` terminates the VG224's
interdigit timer, which `destination-pattern .T` otherwise waits out for 3–4 s.
**Leave ~20 s after the endpoint registers before dialling**, and hold the
serial port for nothing else while a call is running.

Read the INFO exchange off any capture, no card in the path:

```bash
/tmp/eicon-venv/bin/python tools/v34_info.py artifacts/eicon-ppp/cx02.rx.ulaw --from 3.0 --to 6.0
```

Bits 9:11 of the 38-bit peer frame's `lsb-first` word 1 are INFO1a bits 37:39:
6 means that call asked for V.90, 0–5 means V.34.

**Reading a capture.** `.endpoint.log` carries `[dil]`, `[adsp]`, `[v42]` and
`[media]` lines; `[capture] wrote` with no `[call] ended` above it means the
endpoint died mid-call, reported since Session 83 as `[call] media fault`.
`.adsp.csv` is one row per RTP packet. `.adsp-dm.bin` is an `EADSPDM2` header
then `uint64 sample + 256 uint16 LE` per record over `DM 0x3ee0..0x3fdf`;
read-database word `+0xNN` is index `128 + 0xNN`, and `+0x01` is the rate word —
bit 5 set means V.90, and `21 + (value & 0x1f)` is bits per datagram, so
`0x2028` → 29 bits → 38666 bit/s.

---

## 7. Next

Things to establish, not things expected to be true (§0.5).

1. **Which re-enable condition should fire for 2743, 2800 and 3000? (198–203)**
   The INFO1d projected-rate report is not a measurement failure. The per-rate
   array at `DM(0x0f71..)` is full and correct (`000c..0010`, monotonic); the
   *enable mask* at `DM(0x0f8b..0x0f90)` is what zeroes four rates, and it is
   written by straight-line firmware: `PM 0x3911` enables everything,
   `PM 0x3915..0x391a` unconditionally disables six entries by storing `M0`, and
   conditional blocks after that re-enable specific ones. Only the `DM(0x2408)`
   test at `0x391b` fired, re-enabling 3200. **Next:** trace `PM 0x3922` onward
   and record which test rejects 2743, 2800 and 3000; the visible inputs are
   `DM(0x2408)`, `DM(0x3FC9)` (`0x0159` Conexant / `0x011e` Courier, compared
   against `0x0118`) and `DM(0x16E6)`.
   Eliminated on the way: the per-frame budget (with a positive control), the
   `I0` array at `DM(0x0f6d..)` (it feeds the high-carrier bit, which works), and
   any write of our own (the shim's DM addresses are listed in Session 201).
2. **Not the Asterisk endpoints.** `8403` and `8405` are configured the same;
   Session 197's hypothesis is dead.
3. **Read the VG224's actual voice-port config for 2/3 and 2/5** — not for gain,
   but for `echo-cancel`, `vad` and `comfort-noise`, which are per-port and break
   transparency the same way. Nobody working on this has seen the config; every
   statement about these ports descends from one summary sentence in Session 190.
4. ~~**Decode `AT#UD` on the Conexant.**~~ **Done, and it is a dead end (196).**
   It is Microsoft's Unimodem command, decoded by `tools/unimodem_ud.py`, and
   specification note 5 says v1.0 predates V.90 and defines no V.90 parameters
   at all. This modem also reports none of the optional fields that would have
   helped — no V.8 CM/JM strings, no MSE, no echo loss or delay, no V.34 INFO
   bit map. It did corroborate the INFO1a decode (carrier V.34, symbol rate
   3200, matching bits 34:36 = 4), and it showed the Session 195 disconnect was
   an S7 timeout on the server's failed V.34 training.

   *Withdrawn as the lead:* the VG224's `output attenuation -6` as a digital pad
   destroying PCM transparency. Plausible as a mechanism in general, but nothing
   measured points at it, the config was never inspected, and the Courier reaches
   V.90 through the same attenuation on 2/5 — which was explained away as
   "different tolerance" rather than treated as the counter-evidence it is.
3. **The V.34 `0x00b0` transmit halt** — the live blocker for everything that is
   not V.90, and the thing V.90A is queued behind.
4. **Bound the V.32 delay-line walk** at `PM 0x1daa..0x1dba`, and check whether
   the partial's 332-word `0x3680` block was meant to seed it. Note the whole
   page-2 chain is under a pin (§2).
5. ~~**Who writes `DM(0x3F7B)`/`DM(0x3F7C)`, and why does `EcLevel` publish zero?**~~
   **Both done (208, 209).** The echo level is computed and floored, not missing.
   What is left is small and optional: **what reference does `PM 0x0e67`
   interpolate against** (`I4 = $0115`, `SI` from the normalised mantissa), i.e.
   why −176 dB-units is the right answer for this input. A table read, worth
   doing only if the echo level is wanted as a number. Separately the far pair
   `DM(0x10EF)/(0x10F0)` takes one initialisation and is never accumulated —
   the old far-bulk question, now with an address.
6. **Is `MAXTXSPEED`/`MAXRXSPEED = 0x000e` a 19200 ceiling?** Both read `0x000e`
   on every capture while `speed_sel_l`/`_h` enable everything to 33600, and
   under the speed numbering the rate decode establishes 14 is 19200. Directly on
   top of "V.34 upstream stays at 7,200". **Counter-evidence first:** Session 87
   saw 24000 upstream, so either `MAXTXSPEED_V90` (`0x0015`, no cap) governs V.90
   calls or the numbering differs for these words.
   `EICON_PIN_DM=0x3F5C=0x0014 EICON_PIN_DM=0x3F5E=0x0014`; a pin with zero hits
   tested nothing. 207.
7. **`Maxtimer`/`Mintimer` (`DM(0x3F0C)`/`DM(0x3F0D)`)** — `0x0003`/`0x0014` on
   every call, set deliberately by nothing in this repo, and per the guide the
   periods the MSE must hold above and below a threshold. The only host-writable
   training-patience knobs in the database, which is the shape a DIL-region stall
   has. Read guide 5.3.1's bitfields before the call. 207.
8. **FSK and FAX** — one `DM(0x3FC4)` value each (`0x0001`, `0x0800`), untried,
   now that the partial loader is in place.
9. **The card's own V.42** instead of the Python LAPM. Bigger change, moves the
   data path onto the protocol page, but it uses the shipped implementation,
   which is this project's premise.
