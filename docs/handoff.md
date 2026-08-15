# Handoff: read this first

Current to **Session 249**. The agreed execution-model work programme is
[`harness-execution-plan.md`](harness-execution-plan.md); use its phase gates
rather than adding another page-specific workaround. `eicon_adsp_firmware_analysis.md`
is the index to the running log — 244 sessions in six volumes under `analysis/`, the record of *how*
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

**The CX93001 now has a confirmed end-to-end V.90 `CONNECT`.** Session 237
corrects the selected-channel receive scale to the fitted ADSP-2185N's
right-justified 14-bit µ-law representation. The first live call connected at
45,333/7,200 bit/s and held for 75 seconds; the batch was 1/3, so DIL variability
remains but the absolute CX blocker is closed.

The earlier page-selection question was localized to the original VG224 path.
Sessions 194–195 established the protocol decision chain, all by watchpoint and
confirmed against the Recommendation:
watchpoint and confirmed against the Recommendation:

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
Table 10 correctly. The card's decoding and page choice are spec-correct. An independent spandsp
server reads the same 4, proving this implementation did not invent it. But that
server shared Asterisk's anchored RTP bridge and VG224 2/3, so it did not
exonerate the bearer. The modem *offers* V.90 in V.8
(`mods=V90|V34|V22`, `pcm=0x1`) and declines PCM after Phase 2 probing. Session
216 found no negotiated NSE/modem-passthrough payload and no direct media; the
shared path, not modem policy, is now the primary target. Session 217 confirms
`+GCI=B5` is already active and catches the VG224 live call still in ordinary
G.711 with EC/NLP/adaptive playout enabled: configured NSE passthrough never
activates through Asterisk.

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

**The Diva 4BRI-v1 hardware strand ended, on the hardware.** The card on
`eicon420` has 2,383 hard stuck bits in two of its four megabytes of SDRAM,
including everything it allocates from. Its firmware never had a defect; the
question of which protocol image to run on it does not have an answer worth
finding. See §3, and `docs/4bri_v1_firmware_replay.md` for the map. The
emulator work under `tools/eicon_4bri_boot.py` is unaffected and stands — it was
the *disagreement* between it and the card that led here.

---

## 2. Live blockers

| blocker | status | where |
|---|---|---|
| **the CX93001 requests V.34, not V.90** | **absolute blocker closed.** Selection was localized to original VG224 2/3/8403; 7802 selects V.90. The fitted 2185N expands SPORT PCMU to a right-justified 14-bit value (max 8031), but the shim supplied PCM16 scale (max 32124). Correcting the ×4 receive gain error preserves the healthy replay, advances the canonical Courier stall, and produced `+MCR: V90`, `+MRR: 7200,45333`, `CONNECT 115200` for 75 s. Batch 1/3; 7802 now 1/21, so DIL variability remains. `S202=0`, `+MR=0`, no endpoint | 190–195, 215–236, **237** |
| **the answering page stops publishing transmit data at `0x00b0`** | the live V.34 blocker. Both ends reach `0x00b0` through twenty states, then the answerer's transmit chain halts completely — no further `DM(0x224C)` requests, line frozen on one sample. Sessions 137–148 describe a regime that no longer exists; do not carry their wait-block, threshold or role-word findings forward | 149, **164** |
| **V.32: slmodemd measures our transmit at 8 dB and retrains every ~10.5 s** | the live blocker. `V32STC - SNR drop observed, SNR = 8 < threshold = 13`, eight consecutive drops, then `local retrain`, stepping 9600 → 7200 → 4800. **Not** the width, LAPM, level or the G.711 path: `--tx-prbs` reproduces it, a call that never writes the transmit mailbox reproduces it, +12 dB via `TD` changes nothing, and the encoder matches the ITU reference. **Nor the sample clock or the companding (205):** the page publishes exactly one line sample per 8 kHz tick, mean 1.000 over 147,625 ticks, and the card's own `PM 0x1810` encoder is 65157/65536 exact against ITU-T µ-law with no gross error. Our own receiver reports a pristine line the other way — `RXLevel` −10 dBm, MAE **0** at the slicer, `PeakPhasErr` 0, `FreqOffset` 0, `TimOffset` ±1, SNR 30–39.5 dB — so the impairment is one-directional and in our transmit. The mechanism is **not** established | 204, 205 |
| **and do not compare two ends without checking both are in the same phase** | three leads died to this in Session 204. The loopback SNR split was read on a silent line. A spectral comparison of the live call at 12.7 s showed our transmit broadband and slmodemd's a narrow 1800 Hz spike, which looks damning until you sample across the call: both ends alternate between broadband data (10-13 of 14 bins within 10 dB of peak) and a narrow training tone (1-3 bins), and they are seldom in data at the same instant. Sampled where both are, both are proper V.32 QAM. Align on slmodemd's own connect/retrain timestamps before comparing anything | 204 |
| **do not use the loopback SNR asymmetry as evidence** | a 22 dB / 16 dB split between the two roles, which did follow `GEN_SETUP1` bit 3 across a role swap, was measured at `TrnProgress 0x00ea` — where, with no data source configured, **both ends have stopped transmitting entirely** (0% non-silence after ~13 s of a 70 s call). There is nothing on the line to measure there, so those are stale or role-constant reads, not transmit quality. In the phase where audio is actually present both roles read 38.5–39.5 dB and there is no asymmetry. Any repeat must confirm the line is active in the window it measures | 204 |
| **V.32's earlier width story** | superseded. The width is now derived from `DATASTATESpeed` and tracks a peer through 9600 → 7200 → 4800; the 6..2 sweep that "produced zero frames" ran with a width the card never published and against a loopback, and its premise — that page 2 never writes `DM(0x3F61)`/`DM(0x3F62)` — is withdrawn. `tests/test_nl_data_bridge.py` still asserted the old constant width and had been failing since; it now encodes the derivation instead — the three measured `DATASTATESpeed` words, the 9600 → 7200 step, and the `None`-until-published gate | 184, 188e–188g, 204, 206 |
| **V.32's page-2 chain is under a counterfactual** | everything from 188n onward runs with `EICON_PIN_PM=0x3805=0x38ab00`. Every stage of the chain is shipped firmware (188m), so "the harness has the page in a state the real card would not be in" is still the likelier reading than a firmware defect | 188m–188y |
| **DIL is a lottery** | improved but still open. The shim's PCMU receive samples were ×4 too large for the 2185N SPORT's right-justified 14-bit output. Correct scaling eliminates the canonical stalled Courier runaway and produced the first CX V.90 `CONNECT`; live batch 1/3. One failure reached `00c0`, one remained `00b3`. Keep the earlier signed-filter trace as explanation of the consequence, but do not ship its guards. Next quantify residual calls with correct scale and inspect PM `3d00..3d22` only on failures | 88–93, 105–107, 212, 214–215, 229–236, **237** |
| **`EcLevel` cannot publish a non-zero value for any echo — closed** | **understood, and it is a dead instrument (209, 210).** The level routines run every publisher pass and compute a dB through the shared conversion at `PM 0x0eb9`/`PM 0x0e67`; `PM 0x0ede` floors negatives at 0. Calibrated by pinning the accumulator: **6.0206 dB per binary exponent**, `raw = 6.0206·log2(MR) − 116.3`, so raw = 0 needs `MR ≈ 2^19.3`. The largest `MR` a positive 16-bit high word can produce is 8,240 — **38 dB under the floor** — and `0x7fff` down to `0x0001` all publish `0x0000`, 69 passes each. Nothing upstream can fix this; an echo number has to come from the audio, and `tools/echo_delay.py` already does that | 207–**210** |
| **the whole echo-level block is closed** | **nothing to recover (207–211).** `EcLevel`'s accumulator is fed but its conversion floors every reachable value (210). `FarEcLevel`'s pair `DM(0x10EF)/(0x10F0)` has four writers across five captures and none accumulates: `PM 0x37b4` once per call with the constant `0x1306:0x111e` (identical on every capture, including the V.34-only one), the page-14 entry block-zero loops, and `PM 0x2a69` — **V.34-page code**, which stores oscillating signed values into `0x10EF..0x10F1` as scratch. `NearEcLevel` is total − far, so it inherits both. Only `DM(0x10F1:0x10F2)` is genuinely accumulated, by the leaky integrator at `PM 0x2dc0`. An echo number comes from the audio: `tools/echo_delay.py` | 207–**211** |
| **the x4 transmit scaling (245) is disproved** | **withdrawn, and off by default.** The peer publishes its own timing estimate and two matched tower calls settle it: scaling on gives `Timing Offset [ppm] = +8493` and `vpcm: Link Error` at `0x00b0`; scaling off gives `+0.328` — run76's own figure — and 189 s of data mode at 29,333 bit/s. 245's evidence could not have decided it: a right-justified 14-bit mu-law expansion **is** a quarter of a PCM16 codepoint by construction, so "100% codepoints at x4" is predicted by both readings, and run48 reports 100.0% with the scaling off. `encode_g711()` is the card's own PM 0x1810 routine, already in the DSP's domain. The `-36.4` vs `-22.6` dBFS wire asymmetry is unexplained but is not a defect with a mechanism | 245, **248** |
| **in state `0x00b3` we transmit a DC level, not a signal** | open, and separate from the companding. `DM(0x3FB4)` holds the generic pointer `0x3764` that PM 0x19ee re-primes whenever PM 0x1a1e's serializer did not run, and this path emits it as a sample: `14180`, a `-7 dBFS` DC level, on every one of the 15,875 archived page-14 frames in `0x00b3` and half of `0x00c2`. That is the state §7.10's six non-LAPM calls stopped in. The generic path would *dereference* it; page 14 deliberately does not (the comment at `frame_fast`), and which of the two the firmware intends in this state is the open question. Counted in the end-of-call census, behaviour unchanged | **245** |
| **V.90 needs `--native-bearer-activation`** | open, cause unknown | 67, 87 |
| **V.34 upstream stays at 7,200 / local retrain** | open, and **not** the echo canceller — quality `DM(0x0fcf)` is flat across a 10× range of bulk delay. **Not a missing SPORT receive ring:** TIKRNL stores the selected sample through `ShellInptr` immediately before each Core8kRoutine call; runtime tracing proved ordered 8 kHz delivery. In a connected/failed/failed CX comparison, `DM3763` had zero mismatches, filter producer/consumer rates matched exactly at 1.199976/sample, and both internal rings advanced with identical cadence. Courier `Requested 0 / Granted 1` proves the Eicon initiates the retrain. A live CX trace catches two `0xd0 -> 0xbd -> 0xc2` exits, then `0x5678` 7.325 s after the second: it is the failed-recovery marker, not the initial trigger. Immediately before it SNR falls 15.5→13.5 dB, `DM0fcf` rises `~02a9→03b7`, and status asserts `ratechange|flow_blocked`; RTP is clean. Runtime writer is PM `2f4a`, controller `DM2111=7`. `DM0fcf` is a slow average of complex slicer decision error and controls the upstream rate ceiling, but pinning it at a good value does not repair recovery. Both recoveries reach inner `006a`; outer `c2` needs an ordered decoded control-result sequence. The successful pass produces `DM206d/206e=400f/fff9`; the failed pass never sees CP because its second Type-1 MP changes from drn=3/mask `0xffd` to drn=7/mask `0xffe` after the reset quality average spuriously improves, despite just publishing 4800. The CX continues TRN2u with no common low-rate response. `EICON_V90D_RECOVERY_HOLD=1` preserves the first successful recovery's limit/mask for later attempts; fixed diagnostics are LIMIT=3/MASK=1ffa. One fixed-policy call held `d0` for 37.8 s, but no live second-recovery event has yet qualified the fix | 113, 61, 238–242, **243** |
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

**The Analog V.8 stall and RXSAMPLE**

- **RXSAMPLE is filled, by the V.8 page itself, and the whole receive chain is
  alive.** `V8.ANA PM 0x173A` is the writer — `CNTR = DM($3F67)` /
  `I0 = $3F30` / `DM(I0,M1) = AX1` from the 20-word ring at `DM(0x376C)` — and
  it runs **6,000 times per 24,000 line samples: 2 kHz, four words each,
  exactly the 8 kHz stream regrouped**. Driving a 2100 Hz tone, every stage
  carries it: `DM(0x3763)` (r = 0.9999 vs input ×0.25), the 64-word input ring
  at `0x3700` (64 distinct, peak 2031), the 20-word resampled ring at `0x3740`
  (20 distinct, peak 2104), and `RXSAMPLE_0..3` = −1505, 1585, 1255, −1783.
  **`RXSAMPLE_4` and `_5` are zero because `DM(0x3F67)` is 4**, not because
  nothing writes them. Commit 16f09aa's founding observation and the
  correction to it in the first version of this entry are **both withdrawn**:
  a grep for `DM($3F30)` misses this writer, because the address arrives as an
  immediate into I0. Do not spend another session on RXSAMPLE.
- **The Analog caller's V.8 burst is unmodulated because its two FSK phase
  increments are the same word.** `PM 0x3A3C` is the modulator: it reads the
  data bit `DM(0x03B3)` and selects `DM(0x03B6)` or `DM(0x03B7)` into the
  phase accumulator `DM(0x03B5)`. `PM 0x3A36` installs a proper V.21 pair —
  `0x0FBC/0x0D11` for channel 1, `0x18AB/0x1600` for channel 2, whose ratios
  are 1180/980 and 1850/1650 to four figures — and **38 cycles later the CI
  builder at `PM 0x3817` overwrites `DM(0x03B6)`**, leaving both words
  `0x1156`. One increment for both bit values is a carrier, not FSK.
  Hold the pair and the 1083.5 Hz carrier collapses by four orders of
  magnitude, the level roughly triples and the burst becomes two-tone —
  asserted in `tests/test_analog_kernel_dispatch.py`. **This is the blocker
  for the pairing**: the PRI answerer never responds to a carrier, so it
  cycles V.8 and falls to V.22 at 21.8 s.
  - The bit clock is **not** the fault, so do not go looking for an analogue
    of INFO's `DM(0x16af)`. `DM(0x03B3)` holds for 30–354 samples across a
    burst against the 26.67 a 300 baud bit needs; it is the frequency path,
    not the data path, that is dead.
  - **Settled, and it makes the overwrite correct: the tone constants are
    calibrated for 9600 Hz and the chain clocks them at 8000, so everything
    transmits at 5/6 of nominal.** Forcing both increments to one value makes
    the output a pure tone, so increment → frequency reads off directly:
    `0x0A00` → 625.0, `0x0D11` → 816.7, `0x0FBC` → 983.4, `0x1156` → 1083.5,
    every one giving **4.0960 counts/Hz = 32768/8000 exactly**. At 9600 Hz the
    same five constants land on standard tones to within 0.015%:

    | constant | at 8000 Hz | at 9600 Hz | is |
    |---|---|---|---|
    | `0x0D11` | 816.7 | 980.0 | V.21 ch1 mark |
    | `0x0FBC` | 983.4 | 1180.1 | V.21 ch1 space |
    | `0x1600` | 1375.0 | 1650.0 | V.21 ch2 mark |
    | `0x18AB` | 1541.7 | 1850.1 | V.21 ch2 space |
    | `0x1156` | 1083.5 | 1300.2 | V.25 calling tone |

    Five exact hits is not a coincidence. So **`0x1156` is the calling tone,
    and the CI builder writing it over both increments is correct** — an
    unmodulated tone is what a calling modem emits — which withdraws the
    previous entry's "the builder corrupts the pair" headline. There is one
    defect, not two: the 5/6 rate error. The answerer hears 1083.5 Hz where
    V.25 says 1300, and would hear 816.7/983.4 where V.21 says 980/1180, so it
    never responds.
  - **The mechanism is the polyphase step table, and it is directly
    demonstrated.** `PM 0x1771` produces `DM(0x3755)` = 15 outputs per call,
    advancing its input pointer by a step from the table at `DM(0x3790)` —
    which reads **all ones, a 1:1 resample**. 9600 → 8000 needs 15 outputs per
    18 inputs, an average step of 1.2. Forcing the table to `1,1,1,1,2` moves
    the calling tone from 1083.5 Hz to **1308.5 Hz**, against a 1:1 control
    that reproduces 1083.5 exactly. (A 2:1 control gives 1425 Hz, not double:
    the generator only produces 15 new samples per call, so steps above ~1.2
    starve it. Read the 6:5 result as confirming the mechanism and the
    direction, not as a finished fix.)
  - **The tables are generated, correctly, from a rate the page selects.**
    `PM 0x16C4` builds them by Bresenham with ratio `DM(0x3754)/DM(0x3755)`;
    `PM 0x167A` fills those two by copying a 10-word block chosen by
    `DM(0x3F66)` from the pointer table at `0x37C3`. The banks at
    `0x377D/0x3787/…` are those parameter blocks, **not** polyphase phases —
    the generator later writes its tables over the same memory. Transmit
    frequency is `1083.5 Hz × DM(0x3754)/DM(0x3755)`, measured exact at
    1160.9 (15/14) and 1354.4 (15/12). `DM(0x3F67)` does not affect frequency.
  - **`DM(0x3F66) = 4` is the V.8 page's own hardcoded choice**, written at
    `PM 0x3655/0x3656` over the `8` DIAL had selected — the 15/15 identity.
  - **Settled, from the database doc: `DM(0x3F66)` is `Samplerate`, and V.8
    asks for 9600.** `addsp_database.md` names it, with `DM(0x3F67)` =
    `Samplebuffersize` ("the ratio of sample- and symbolrate") and `DM(0x3F65)`
    = `Symbolrate`. Its one surviving code → rate pair is **code 8 → 8000**,
    and code 8 selects `DM(0x3755)` = 18, so

        rate = 144000 / DM(0x3755)

    reproduces the documented value exactly. Every block ships `DM(0x3754)` =
    15 = 144000/9600 — the DSP's fixed internal rate — so the resampler ratio
    `DM(0x3754)/DM(0x3755)` is `rate/9600`, a converter between the codec and
    a constant 9600 Hz core:

    | code | `DM(0x3755)` | codec rate |
    |---|---|---|
    | 0,1,2 | 20 | 7200 |
    | 3 | 16 | 9000 |
    | **4** | **15** | **9600** ← V.8's own choice |
    | 5 | 14 | 10286 |
    | 6 | 12 | 12000 |
    | 7 | 10 | 14400 |
    | 8 | 18 | 8000 ← documented, and what DIAL selects |

    **So there is no firmware defect.** V.8 asks for a 9600 Hz codec and gets
    an identity resampler because core and codec then agree; its tone
    constants are 9600 Hz constants because that is the rate it asked for.
    `DM(0x3F67)` = 4 makes the symbol rate 9600/4 = **2400**, which is why
    `RXSAMPLE_0..3` is four samples per symbol and why the fill runs at 2400 Hz
    (measured 2000 Hz only because the codec is being clocked at 8000).
    **The harness clocks SPORT1 at 8000, and that is the whole bug.** The
    previous entry's "no block reaches 1.2, so 9600 is unreachable" had the
    ratio inverted and is withdrawn: 9600 needs no resampling at all.
  - **Clocking the codec at 9600 puts the calling tone at exactly 1300.2 Hz**
    against the V.25 nominal 1300. The receive detector does **not** improve
    — peak `DM(0x07BC)` 52 at 9600 against 46 at 8000, `DM(0x07BD)` 0 either
    way — so the V.8 escape is a separate question from the rate. Note the
    earlier "9600 makes receive worse" (peak 24) was **linear interpolation**,
    the exact defect Session 249 measured at ~20 dB in this direction; a
    windowed sinc recovers it, and run65 had already qualified one for
    `net 8000 → DSP 9600`. Resample properly or the measurement is about the
    interpolator.
  - **Done, and it completes V.8.** `--analog-codec-rate 9600` clocks SPORT1
    at the rate the page asks for, with a polyphase windowed-sinc resampler at
    the RTP boundary (6:5 in, 5:6 out; frequencies exact and amplitude within
    0.2% over 400–3000 Hz). Against the native PRI V.90d answerer:

    | | codec 8000 | **codec 9600** |
    |---|---|---|
    | caller V.25 calling tone | 1083.5 Hz | **1300.2 Hz** |
    | caller TrnProgress | `0x0001`, whole call | `0x0001→02→0b→24→26→28→2a` |
    | caller pages | V.8 only | V.8 → **page 7 INFO** at 20.3 s |
    | answerer TrnProgress | `0x22/26/24/28` cycling | `0x00→01→04→09→44` |
    | answerer fallback | V.22 at 21.8 s | **V.32** at 19.2 s |

    The Analog caller leaves the CI retransmit loop it had never left, V.8
    completes, and it requests the V.34/V.90 Phase 2 page by itself. That is
    the strand from `f695909` onward closed: it was the codec rate.
  - **Do not conclude "the detector still does not fire" from a replay.**
    Driven with *recorded* peer audio the escape counter `DM(0x07BD)` stays 0
    even at 9600 and the level only reaches 72 of 2000 — because that
    recording came from a call in which the peer never answered. Against a
    live peer that does answer, the escape fires. A replay cannot test a
    detector whose input depends on what the detector's own output causes.
  - **They do negotiate INFO — against another Analog end.** With
    `--answerer-kernel-dispatch --answerer-firmware-set analog109` and both at
    9600, V.8 completes in **3.7 s** on the caller and **5.2 s** on the
    answerer, both load page 7 INFO, and both reach `TrnProgress 0x002a` with
    the INFO receive parser running (`INFO_RX event=0x0001 parser=0x3520`).
    Caller walks `01→02→0b→24→26→2a`, answerer `00→04→0b→24→26→2a`.
  - **Against the PRI answerer they do not, and the caller is not the reason.**
    The PRI end is **silent for 19 s** — probed on its own capture, nothing on
    the wire from 5 s to 18 s — and its first transmission is 2100 Hz ANSam at
    ~19 s, the same instant it gives up and takes V.32 (19.16 s). The Analog
    caller detects that ANSam and asks for INFO 1.1 s later, at 20.3 s, which
    is correct behaviour arriving after the peer has left.
    `--native-bearer-activation` does not change it (19.18 s).
  - The Analog caller's transmit is now well-formed and worth stating exactly,
    because it is **not** CI: it emits **only 1300 Hz**, in 0.6 s bursts every
    2.0 s, with ~0% energy at 980/1180 in every active window of a 40 s call.
    That is the **V.25 calling tone** and its cadence, which is legal V.8 for a
    calling terminal — but it means the handshake depends entirely on the
    answering end returning ANSam. Bit 7 of the `PM 0x3DF6` group ("send CI")
    is the only action bit ever set, and modulated CI at 980/1180 is still
    never transmitted; that is now a question about the answering end's
    behaviour rather than a caller defect.
  - **(a) is localized: the PRI V.8 answer script parks in a zero-action wait
    state.** Watching `DM(0x049F)` (script cursor) and `DM(0x0740)` (action
    mask) on the live answerer:

    | cycle | cursor | mask | |
    |---|---|---|---|
    | 66,156,612 | — | `0x0000` | V.8 loads, block zeroed at PM 0x36C9 |
    | 66,157,769 | `0x0341` | `0x0000` | entry record loaded, **no actions** |
    | 198,887,232 | `0x0050` | `0x0000` | ~132.7 M cycles later it finally moves |
    | 207,062,919 | `0x00E3` | `0x0006` | first non-zero actions — ANSam, at 19 s |

    So it is not that ANSam is mis-generated: for the whole silent window the
    script asks for **nothing at all**, and the wire agrees. The Analog end on
    the same rig has a non-zero mask by cycle 98,379 and transmits from
    0.00 s. The V.8 script machinery is byte-identical between the two images
    (PM 0x378B–0x37B8 word for word), so this is a script-record/condition
    difference, not different code. `--native-bearer-activation` does not
    change it.
  - **(a) is solved, and it is a countdown underflow.** The chain, measured end
    to end with a PC histogram and write watches on one 22 s pairing:

    | | answerer | caller |
    |---|---|---|
    | V.8 frame head `PM 0x170E` | **1** | 165,513 |
    | symbol block `PM 0x1728` | 1 | 102,048 |
    | script dispatcher `PM 0x378E` | **0** | 41,371 |
    | executions in `PM 0x3000-0x3FFF` | 2,085 (all init) | 53.8 M |

    The V.8 page is resident, `DM(0x3FB8)` correctly holds `0x371E`, the rate
    triple is identical on both ends (`Samplerate` 4, `Samplebuffersize` 4),
    and the page's line handler *does* run per frame. It simply never reaches
    the page. `PM 0x2055..0x205B`:

        2055  AY0 = DM($3999)
        2056  AR  = AY0 - 1
        2057  IF NOT AC JUMP $205C     ; taken 0 times in 54,894 passes
        2058  DM($3999) = AR           ; decrement
        2059  I4 = DM($3FB4)
        205a  DM(I4,M5) = $0000        ; zero the transmit word
        205b  JUMP $207F               ; skip the page

    `DM(0x3999)` is **counting down from 0xFFFF**: observed writes are
    `0xFFFE, 0xFFFD, 0xFFFC…`. V8.F34 *ships* it as `0x1900` (6400) and the
    page's own init zeroes it at `PM 0x2022`. At ~2.6 kHz, 65,534 decrements
    is ~20 s — which is the silence, to the second. Every one of those passes
    zeroes the transmit word, which is why the wire is empty.
  - **What the loopback answerer is missing, against a live call that works.**
    A real modem calling in over SIP reaches V.90 data mode, so the working
    case is the control. Diffing the DM capture at 0.02 s:

    | | live run48 (connects) | loopback answerer |
    |---|---|---|
    | overlay on bootpage 6 | `0x025f` V.8 | **`0x0271` V.22FC** |
    | `Norm_H` `DM(0x3F08)` | `0x0021` | `0x00FF`, then `0x0021` at 0.22 s |
    | `Norm_L` `DM(0x3F09)` | **`0xA13F`** | **`0xB13F`** |
    | first transmit | **ANSam at 0.5 s** | silent 19 s |

    `0xB13F` is `0xA13F` plus **bit 12, V32ext**. And live calls **never load
    `0x0271` at all** — run34 and run48 go `0x025f` → `0x0260` → `0x026a`
    and nothing else. So the loopback answerer comes up on V.22FC with a
    wider modulation menu, V.8 is downloaded over it at 0.22 s, and the
    V.22FC per-frame code keeps running (`PM 0x1DAA`/`0x1DB5`, 15.9 M
    executions each) while V.8's own frame head runs **once**.
  - `0xB13F` is the shim's *documented default* — `norm_l = native if native
    else 0xB13F` at `eicon_mips_shim.py:4921`. A live call supplies `0xA13F`
    from the card's own answer WDB. The shim already documents this exact
    failure shape for the **originate** side at `eicon_mips_shim.py:4893`:
    bootpage `0x000c` "AT online" instead of `0x0006` restricts NORM_L and
    "the V.8 negotiation falls back to V.22/FSK". The answer side walks into
    the same trap and has no equivalent fix.
  - **`DM(0x3999)` is shared scratch, not a dedicated V.8 word.** DIAL ships
    `0x0017` there and the pre-V.8 page reads it at `PM 0x1D9A/0x1DAA/0x1DB5`
    as ordinary working data. That is why V.8's countdown is never sane when
    a stale page is still running.
  - **Not established: how it reaches 0xFFFF.** The only *store* to
    `DM(0x3999)` between the page's init and the first decrement is the init's
    own `0x0000` — so nothing the ADSP executed set it to 0xFFFF. A bulk DM
    load from the host is invisible to a store watch, and the native shim
    writes DM directly on every overlay load, so that is the first place to
    look. Note V8.ANA ships `0x0B40` where V8.F34 ships `0x1900`, so the two
    images do not agree on this word to begin with.
  - Superseded: the earlier "parks in a zero-action wait state waiting on a
    condition" reading. The mask is `0x0000` because the script machine never
    runs at all, not because a condition fails; and `PM 0x0341` is not a code
    pointer, so do not go looking for a record there.
  - **Answered, and the writer is ours.** `eicon_mips_shim.py` seeds
    `DM(0x3995)`/`DM(0x3999)` with `0xFFFF` on every V.8 (`0x025F`) overlay
    serve, as disabled-timer sentinels, added in `c788ad7` for the
    originate/V.32 partial path. `EICON_V8_TIMER_SENTINELS=0` gates it. The
    answerer then walks `0x0000 → 0x0004` at **0.54 s** instead of 16.92 s —
    the same walk 16.4 s earlier, and 0.54 s is live run48's own "ANSam at
    0.5 s". Only the `--answerer-native-mips` answerer takes this path; an A/B
    on the direct backend shows nothing, which cost a run.
- **The answerer's V.8 exit is not a timeout — it is a detector count, and the
  caller's calling tone is what feeds it.** With the answerer transmitting on
  time it still leaves V.8 1.94 s later for V.32, and the script trace names
  the exit exactly. Watching `DM(0x049F)`/`DM(0x0740)` gated to overlay
  `0x025F`, the answer script walks
  `0050→006e→007a→008f→00a4→00bc→00da`, takes mask `0x0006` (ANSam) at the
  record on cursor `0x00E3`, and leaves from cursor `0x00F5` through condition
  slot 3 (`PM 0x37AD..0x37AE`) into `DM(0x0791) = 0x0149`, which then walks on
  to V.32. That slot's routine is `PM 0x37F1`:

      37f1  AX0 = DM($3F09)      ; NORM_L
      37f2  AY0 = $2000          ; bit 13
      37f3  AF  = AX0 AND AY0
      37f4  IF EQ JUMP $37D5     ; bit clear -> constant true, never exits
      37f5  AY0 = $12C0          ; 4800
      37f6  JUMP $37F8
      37f7  AY0 = $0780          ; 1920 -- the caller's own entry
      37f8  AX0 = DM($07BD)
      37f9  AR  = AY0 - AX0      ; IF LE at 37ae -> exit

  So both ends run the same comparison on the same counter; NORM_L bit 13 is
  set on the answerer, so its bar is **4800** where the caller's is 1920.
  `DM(0x07BD)` is `0x0000` for the first 1.4 s of ANSam, climbs from `0x0001`
  to `0x12C0` over ~0.5 s, and the state exits on the crossing.
  - **It is signal-driven, and the control proves it.** With the caller's
    transmit muted (`EICON_ANALOG_TX_GAIN_DB=-100` on the caller) the answerer
    sends ANSam at 0.54 s and **holds it for the whole 12 s call** — no
    `0x0009`, no V.32, counter never leaves zero. The caller's receive is
    untouched in that run and it still reaches `0x002a`. So the exit is caused
    by what the caller transmits, and not by a clock.
  - **Withdrawn: "the 1300 Hz calling tone is what feeds it."** The mute
    control shows the caller's transmit is *necessary*; it does not show which
    part of it, and a tone bench says it is not the tone. `analog_line.py`
    grows a bench tone source — `EICON_ANALOG_TX_TONE_HZ`, with
    `_AMPLITUDE`/`_ON_S`/`_OFF_S` — which replaces the caller's transmit with
    a known signal on a real call, so a sweep is in line Hz by construction
    rather than in units of an internal pass rate. **No tone reproduces the
    escape**: 400–3000 Hz in 100–300 Hz steps, at the measured level (rms 537,
    `--amplitude 760`) and 14 dB hot, steady and in the caller's own 0.6 s /
    1.4 s cadence. In every one the answerer reaches `0x0004` at 0.54 s and
    stays there for the call.
  - **What arms `PM 0x3EAD`: `DM(0x077D)`, a script-record field.** The
    per-symbol loop is `PM 0x373D..0x3750`, `CNTR = DM(0x3F67)`, and the
    detector is called indirectly at `PM 0x3749..0x374A`
    (`I4 = DM($077D); CALL (I4)`). `DM(0x077D)` is offset 0x3E of the block
    `PM 0x37B7` loads wholesale from the state's record, exactly like the
    condition routines and `DM(0x0740)`. Live, it is written twice by
    `PM 0x3C0F`: `0x3ED4` (the `RTS`, no detector) on the state before ANSam,
    then **`0x3EAD`** on the ANSam state itself. There is no runtime decision
    and no gating signal — arming is a field in the record, and it is set.
  - **Withdrawn: "under a tone the detector does not run at all."** It runs.
    The zero-write reading came from watches that never armed, and a
    positive control in the same run is what caught it: at `--seconds 6` the
    control word `DM(0x077D)` also reports zero writes, and at `--seconds 8`
    the same command reports 2 writes ending `0x3EAD` and 4,133 writes to
    `DM(0x07BC)`. Every `--seconds 6` sweep point is void. §0.4 exists for
    this; watch a word you know fires, in the same run, every time.
  - **The bench axis needs ×1.2, and that is measured.** A tone injected at
    the caller's codec boundary with `EICON_ANALOG_CODEC_RATE=9600` arrives on
    the wire at `HZ × 8000/9600`: generating 1300 puts **1080 Hz** on the
    line, confirmed by `modem_tone_probe.py` on both `caller.ulaw` and
    `answerer.rx.ulaw`. To place *f* on the wire, generate `f × 1.2`. The
    first sweep therefore covered 333–2500 Hz, not 400–3000.
  - **The two waveforms are the same signal, measured side by side.** Over the
    same 0.3 s window of `answerer.rx.ulaw`, the live caller's burst and the
    bench tone are 1300.2 Hz and 1300.0 Hz (2 Hz Goertzel scan), rms 537.5 and
    539.5, peak 748 both, DC −1.2 and −1.8, and neither has a second bin
    within 20 dB of the first. The answerer's script state entering ANSam is
    bit-identical too: the same two `DM(0x077D)` writes at the same cycles,
    73269610 and 73296993, in both runs. The live run's third write is at
    105592052, after the escape, so it is a consequence.
  - **Amplitude is inert, confirming 995a2d9 on this image.** The same burst
    at amplitude 3900 gives peak 118 and at 12000 gives 71 — lower, and
    non-monotonic. So the 5× difference in input level below is not the
    mechanism either.
  - **Where the two runs actually diverge is the answerer's own front end.**
    Binning the live `DM(0x0772)` writes by time — `73325420` cycles is the
    ANSam entry at 0.54 s and `105421060` the escape at 2.44 s, which fixes
    the axis:

    | window | passes | peak `\|DM(0x0772)\|` |
    |---|---|---|
    | 0.6–1.5 s, wire silent | 8,604 | **0** |
    | 1.5–2.03 s, the 1300 Hz burst | 5,067 | **32,768** (full scale) |
    | 2.03–2.44 s, wire silent, counter still climbing | 3,941 | **10,610** |
    | after the escape | 404 | 10,165 |

    The front end is clean before the burst, saturates during it, and then
    keeps delivering ~10,000 for the 0.4 s of *silence* in which the counter
    finishes its climb to 4,800. The bench, fed the same wire signal, peaks at
    6,303 and never saturates, and its level never exceeds 129. So the
    difference is not in the caller's signal, not in the discriminator, and
    not in the script: it is in what `PM 0x3764` makes of the line, and the
    leaky integrator's 0.95 per pass decays in ~10 ms, so a level above 2,000
    at 2.44 s cannot be the burst ringing out. **That is the next thing to
    walk backwards from** — `PM 0x3764`, `DM(0x06BE)` and the RXSAMPLE window
    it steps through, against the same window of the bench run.
  - **`PM 0x3764` is a high-pass with an AGC and a hard clip, and its cursor
    is RXSAMPLE.** Disassembled end to end:

        3764  I4 = $3C3D                  ; PM coefficient pair
        3765  I1 = $3FCD                  ; DM filter state pair
        376b  I4 = DM($06BE)              ; the RXSAMPLE cursor
        376c  MR = MR + MX0*MY1, SR1 = DM(I4,M5)   ; read RXSAMPLE[n], step
        376d  DM($06BE) = I4              ; cursor back
        3770  DM(I1,M1) = MR0, AR = AY0 - MR1      ; sample - lowpass
        3771  MY0 = DM($3FC8)             ; AGC gain
        3772  DM(I1,M0) = MR1, MR = AR * MY0
        3774  AF = MR1 - $0400 ; IF GE -> AR = $7FFF
        3776  AF = MR1 + $0400 ; IF LE -> AR = $8000
        3778  SR = ASHIFT MR1 (HI) BY 5   ; else << 5
        377b  RTS                         ; AR -> DM(0x0772)

    So `DM(0x0772)` is `clip(((RXSAMPLE[n] − lowpass) × DM(0x3FC8)) << 5)`,
    and the ±32768 seen live is the clip constant at `0x377E`, not a sample.
    This also names three of the "reserved and very much in use" words in
    `addsp_database.md`: `DM(0x3FC8)` is this AGC gain and
    `DM(0x3FCD)`/`DM(0x3FCE)` are its filter state — which is why `0x3FCD`
    moves 3,967 times in a call.
  - **The AGC is at maximum on both runs**, `0x7FFF` from 0.58 s. Live it is
    pulled down from 1.92 s (`0x5510 → 0x23bd → 0x1f62 …`); on the bench it
    never moves at all. With the gain at full scale and a `<< 5` after it,
    almost any input clips — which is the first sign that the input, not the
    line, is the variable.
  - **RXSAMPLE freezes, and the frozen window is the signal.** `Samplerate`
    `DM(0x3F67)` is 4, so the per-symbol loop makes four passes and the cursor
    steps `RXSAMPLE_0..3`. Read out of the database capture:

        live   1.90 s [0, 0, 0, 0, 0, 0]
               2.00 s [-298, 697, 324, -259, 0, 0]   and identical at
               2.10 / 2.20 / 2.40 s
        bench  2.40 s [0, 0, 0, 0, 0, 0]
               2.50 s [-182, -155, -24, 123, 0, 0]   and identical at
               2.60 / 2.70 / 2.80 / 2.90 s

    The array takes a value shortly after the burst arrives and then **never
    changes again**. A cursor cycling four fixed words at 9,600 passes per
    second is a manufactured periodic waveform at **2,400 Hz**, and it does
    not stop when the line goes quiet. That is what the escape detector
    integrates: it is why `DM(0x0772)` reads 10,610 through 0.4 s of silence,
    why the level stays over the 2,000 threshold, and why the counter reaches
    4,800 and takes V.8 out. The bench differs only in the *amplitude* of the
    frozen window — peak 182 against 697 — which after the high-pass, the
    full-scale AGC and the `<< 5` is the difference between clipping and
    reading 129. Same defect, one side of the threshold each.
  - **Fixed, and the answerer already had a kernel-driven receive path —
    `--answerer-kernel-dispatch`, blocked by a stale assertion.** It refused to
    start: `_validate_v90d_configuration` demanded equality on `Info0_setup`
    and `Norm_H`, which are host inputs the firmware adds bits to, so it
    rejected a card that had got *further* (V.8 loaded) rather than one
    misconfigured. Measured, `Info0_setup` is `0xF1FD` on the native tower and
    `0xF8FD` here against the guide's `0xF0FD`, and `Norm_H` is `0x0021` —
    which `addsp_database.md` already records as constant across every live
    capture and `dial_tikrnl_drive.py` names `NORM_H_V8_MEDIA`. The check now
    requires the host's bits to survive and lets the firmware own the rest.
  - **The A/B on the receive array, same writer and same cursor walk:**

    | answerer backend | `RXSAMPLE_0` writes | distinct values |
    |---|---|---|
    | `--answerer-native-mips` | 2,352 | **1** |
    | `--answerer-kernel-dispatch` | 7,344 | **109** |

    `PM 0x1738` is the writer in both and `DM(0x06BE)` walks `0x3F30..0x3F34`
    in both, so `27b3629` is right that the page fills the array itself — it
    writes what it is handed, and the native tower hands it one constant.
    Downstream, `DM(0x0772)` goes from 1,672 distinct values clipping at
    ±32,768 to **794 distinct, peak 7,296, no clipping**, and the escape
    level `DM(0x07BC)` from a peak of 6,558 to **2,710**.
  - **And the handshake completes.** `--answerer-kernel-dispatch` against the
    Analog caller, no `EICON_V8_TIMER_SENTINELS` needed because the sentinel
    is the native shim's:

        answerer  0x0000 → 0004 (ANSam, 0.54 s) → 000b → 0020 → 0024
                  → 0026 → 0028 → 002a  (5.30 s)
        caller    0x0001 → 0002 (2.90 s) → 000b → 0024 → 0026 → 002a (3.80 s)

    No V.32 fallback on either end. **This is the first time the PRI answerer
    and the Analog caller have negotiated V.8 and both loaded page 7 INFO.**
    They then stall at `0x002a` — the same place two Analog ends stall, so it
    is now one blocker for both pairings rather than two different ones.
  - **So the escape was a harness artifact, not V.8 behaviour**, and
    it was `16f09aa`'s finding — the per-symbol receive array is not
    being maintained — on the PRI answerer's native-MIPS path. `27b3629`
    withdrew that reading for the Analog caller; it was never tested here.
    **The fix is upstream of everything above**: give the native-MIPS answerer
    a receive path that writes `RXSAMPLE_0..3` every symbol. Until it has one,
    no measurement of this detector means anything — including the sweep
    numbers earlier in this section.
  - **Superseded: the frequency framing.** With the waveforms measured
    identical, "whether 1300 Hz is in the passband" is no longer the question,
    and neither is the caller's missing CM as an explanation for *this*
    symptom. Both remain open on their own terms.
  - **Where it now stands, and it is not a level problem.** With `1560`
    generated — 1300 Hz on the wire, rms 537, the real caller's own tone and
    level — the input reaches the page intact (`DM(0x0772)` takes 167 distinct
    values, peak ±6,303 over 28,416 writes) and the discriminator still
    publishes `DM(0x07BC) = 0x0000` across 4,133 passes. The live pairing,
    same frequency and same level on the wire, publishes 55+ and counts to
    4,800. So a clean sine at the calling tone's frequency is *not* what
    drives the counter, and the difference between it and the caller's actual
    transmit is the open question. The caller's transmit is a modem DAC
    output, not a sine, so its edges and harmonic content are the first place
    to look — compare the two waveforms directly before sweeping anything
    further.
  - **The detector's pass rate is measured: 9,600 Hz.** Write-watching
    `DM(0x0772)` on the live answerer gated to overlay `0x025F` gives 9,792
    writes over exactly 1.0 s of residency (`answerer.adsp.csv`, `overlay ==
    0x025f`). That is the rate `DM(0x3F66)` asks for (e513a0d), on a PRI end
    whose line delivers 8,000 — the same mismatch `--analog-codec-rate 9600`
    fixed for the caller, for which the PRI/T1 answerer has no equivalent.
    **Not established** that this is a defect: nothing here measures what the
    answerer's front end does with the difference.
  - Two dead ends recorded so they are not repeated. A bare
    `pm.words`/`dm.words` load of the overlay answers 11 at every frequency —
    the filter state, the coefficient pointers and `DM(0x0748)` all come from
    the page's init. And the direct `Card` harness in `dial_tikrnl_drive.py`
    loads V.8 but never runs its script: after 200 frames the cursor is
    `0xFFF1`, `DM(0x0748)` is 0 and shellinptr is unpublished, so it cannot
    host this bench. The live loopback is the only rig that reaches the state.
  - **`PM 0x3A67` is not the routine that feeds the counter, and the state's
    own detector pointers are a trap the log already warned about.** That
    state carries `DM(0x077B) = 0x3ED4` (the `RTS`, i.e. no detector) and
    `DM(0x077C) = 0x3A67` — a quadrature down-converter, phase accumulator
    `DM(0x03B9) += 0x0E66` per pass, sibling at `PM 0x3A69` using `0x1755` —
    but every one of the 18,305 writes to `DM(0x07BD)` comes from
    **`PM 0x3ECC`**, the shared `0x3EBC` tail. Exec-watching the entries names
    it: `PM 0x3EAD`, called from `PM 0x374A`, first instruction
    `AX0 = DM($0772)`. So both ends run the *same* discriminator on the same
    input word, and 995a2d9's note — that a state's `0x077B`/`0x077C` pair is
    a different chain from the one feeding `DM(0x07BD)` — holds on the
    answering side too. Read the writer, not the pointer.
  - **(b)** both Analog ends stall at `TrnProgress 0x002a` inside INFO without
    reaching data mode — and since `01e92e1` the PRI/Analog pairing stalls in
    the same place, so it is one blocker.
- **What `0x002a` is: page 7 skips the INFO0 exchange and both ends transmit
  the answer-side Phase 2 tone.** V.34 §11.2.1 is explicit — the call modem
  sends INFO0c followed by **Tone B, 1200 Hz** (§11.2.1.1.1), the answer modem
  sends INFO0a followed by **Tone A, 2400 Hz** (§11.2.1.2.1), and §11.1 gives
  Tone A a 1800 Hz guard tone. Measured on the wire in the PRI/Analog pairing:

    | | caller | answerer |
    |---|---|---|
    | 1200 Hz peak, whole call | **12.0** | **7.4** |
    | 1800 Hz | 1241.6 | 1226.5 |
    | 2100 Hz (ANSam) | 73.5 | 1453.0 |
    | 2400 Hz | 1460.0 | 1429.3 |

    INFO sequences are DPSK on a 1200 Hz carrier and Tone B is 1200 Hz, so a
    peak of 12 across the whole call means **neither end ever sends INFO0, and
    the calling end never sends Tone B**. Both send Tone A plus its guard tone
    — the answer modem's signal — the caller from 4.0 s and the answerer from
    7.5 s. Each then waits for something the other never transmits, which is
    exactly what `info_rx_complete = 0x0000` on both ends says. Both ends are
    otherwise in identical INFO state: `bootpage 7`, overlay `0x0260`,
    `info_internal_progress 0x002a`, `info_state_vector 0x0b42`,
    `info_timer_lo 0x0063`.
  - **The role reaches the page and is correct.** `GEN_SETUP1` is `0x048C`
    (calling) on the caller and `0x0484` (answer) on the answerer, and page 7
    reads it in exactly three places — `PM 0x1663`, `PM 0x3EFD`, and the
    copies `DM(0x167E)`/`DM(0x168C)` it makes there, read only at
    `PM 0x32DF`/`0x32E0`. Watched live, the caller stores `DM(0x167E) = 0x048C`
    from `PM 0x3EFE`. So this is not a lost role word, and `PM 0x1661..0x166A`
    is not the branch either: it turns on `GEN_SETUP1` bit 9, which is clear
    in both roles.
  - **Page 7's transmit is role-blind, and that is measured, not inferred.**
    Two Analog ends, one in each role, peak over the whole call:

    | | 1200 Hz | 1800 Hz | 2100 Hz | 2400 Hz |
    |---|---|---|---|---|
    | caller (**calling**) | 12.0 | 1241.6 | 74.6 | 1460.0 |
    | answerer (**answer**) | 9.9 | 1241.6 | 1400.3 | 1460.0 |

    The two roles produce **identical** Phase 2 output — 1460.0 and 1241.6
    each, not merely similar — and the caller's figures are bit-identical to
    the caller of the PRI/Analog pairing as well. The only role-dependent
    signal in either pairing is the 2100 Hz ANSam, which V.8 produces, not
    page 7. So whatever emits Tone A does not consult the role at all, in
    either firmware set, in either pairing.
  - **This is one blocker across both pairings**, confirmed rather than
    assumed: the two-Analog pairing fails the same way on the wire, not just
    at the same `TrnProgress`.
  - **The generator, traced from the transmit sample back.** `TXSAMPLE_0`
    (`DM(0x3FA7)`, guide read offset 0xC7) is written from **`PM 0x3416`**,
    and the code above it is a vectored dispatch:

        3400  AF = AX0 - AY0, MX0 = PM(I4,M5)   ; I4 = DM($16A0), L4 = 8
        3405  MR = MX0 * MF, MY1 = PM(I4,M5)    ; I4 = DM($16A1), L4 = 0x10
        3408  MY1 = DM($1674)                   ; output gain, 0x3895
        3409  I4 = DM($166C)
        340a  JUMP (I4)                         ; <- the transmit selector
        340b  MR1 = 0 ; JUMP $3416              ; silence
        340d  MY0 = $6FC0 ; JUMP $3410          ; table, full scale
        340f  MY0 = $37E0                       ; table, 6 dB down
        3410  I4 = DM($16A3), L4 = $0040        ; 64-entry PM waveform table
        3412  AR = PM(I4,M5)
        3414  MR = AR * MY0
        3415  MR = MR1 * MY1                    ; <- selected: skips the table
        3416  DM(I7,M5) = MR1                   ; -> TXSAMPLE_0

    Live, `DM(0x166C)` goes `0x0000 → 0x340B` (silence) `→ 0x3415`, written by
    `PM 0x34B0` — **the same values in the same order on both roles**. And
    `0x3415` is not one of the three entry points: it lands past the table
    walk, so the transmitted sample is whatever the chain at
    `PM 0x33F8..0x3409` left in `MR1`, scaled by `DM(0x1674)`. The 1800/2400
    pair therefore comes from the 8-entry and 16-entry PM tables walked
    through `DM(0x16A0)`/`DM(0x16A1)` — at 9,600 samples/s those have 1200 Hz
    and 600 Hz fundamentals, and 1800 and 2400 are exact harmonics of both.
  - **`DM(0x166C)` is loaded by a generic table walker, indexed by state, and
    the role is not the index.** `PM 0x34AE..0x34B4` walks a PM table
    (`I4 = 0x2BD5` for the `0x166A` block) at an index derived from `SR0`, and
    page 7's init calls it at `PM 0x3F04` with **`SR0 = 0x0000`** — set
    literally at `PM 0x3F03`. `GEN_SETUP1` is read two instructions earlier
    (`PM 0x3EFD`) and stored to `DM(0x167E)`, and then never consulted by this
    path. That is the mechanism behind the role-blindness measured above: the
    transmit vector is selected by a state index that starts at zero on both
    ends, not by the role.
  - **Found in passing, and worth more than the bug: the firmware's V.34 line
    probing signal is present and correct, and never transmitted.** The
    64-entry table at `PM 0x2C40` — the one the unused `0x340D`/`0x340F`
    entries would play — DFTs to equal-amplitude tones at every multiple of
    150 Hz **except 900, 1200, 1800 and 2400**, which is exactly V.34's L1/L2
    omission set. Meanwhile the wire carries *only* 1800 and 2400, every other
    150 Hz bin reading 0.0. So the two signals are complementary: the card is
    sending the two tones the probe omits and omitting the twenty-one it
    sends. Any fix should end with `DM(0x166C)` reaching `0x340D`/`0x340F` at
    the right moment, and that table is how to recognise it working.
  - **The blocker is `Norm_L`, `DM(0x3F09)` — bisected to one bit.** Diffing
    our answerer's write database against live run48 at the state they share
    (`TrnProgress 0x0028`, the last common state before ours goes to `0x002a`
    and run48 goes to `0x002e`) gives thirteen differing words. Aligning all
    thirteen takes the answerer to `0x0026 → 0x0030 → 0x0034`. Halving twice
    and then testing singly:

    | `Norm_L` | answerer reaches | on which page |
    |---|---|---|
    | `0x9100` (ours) | `0x0028 → 0x002a`, stuck | bootpage 7, `0x0260` |
    | `0x9101` (+V21) | `0x0040 → 0x0044` | V.32 |
    | **`0x9102` (+V22)** | `0x0030 → 0x0034` | **bootpage 1, `0x0266`** |
    | `0x9103`, `0x913F`, `0xA13F` (live) | `0x0030 → 0x0034` | bootpage 1/2 |

    Guide §5.3.1 gives `Norm_L` as the modulation menu — `0x0001` V21,
    `0x0002` V22, `0x0100` V34, `0x1000` V32ext, `0x2000` V32bis, `0x8000`
    V90. We advertise V90+V34+V32ext and nothing below; the live card
    advertises `0xA13F`, which adds V32bis and the whole low group `0x003F`.
    **One bit, V22 `0x0002`, is the difference between stalling at `0x002a`
    and walking on.**
  - **But it buys a fallback, not V.90, so it is not a fix yet.** With
    `0xA13F` on both ends the pairing goes much further — the answerer walks
    `0x0009→0040→0044→0048→004a→004c→004e→0050→0052→0054→0058→005a→005c→005e→0060`
    and the caller likewise to `0x0060` — but both land on **bootpage 2,
    overlay `0x0267`: V.32**, not V.90. With the minimal `0x9102`/`0x8102`
    they settle lower still, on bootpage 1 / `0x0266`. So populating the menu
    lets the negotiation complete and train, downwards, where before it had
    no common lower modulation and simply stopped.
  - **Defaults deliberately unchanged.** The current default reaches V.8 and
    INFO; every menu tried here trades that for a completed handshake at V.32
    or V.22. Which of those is "further" depends on whether the goal is a
    working data path or V.90 specifically, so it is a call to make rather
    than a change to slip in. Reproduce either with
    `--answerer-db-word 0x3f09:0xa13f --caller-db-word 0x3f09:0xa13f`.
  - **Answered, and it is a correction: V.22 does not unblock Phase 2 — it
    lets V.8 avoid it.** Page 7 reads neither `Norm_L` (`DM(0x3F09)`) nor the
    V.8 classifier result (`DM(0x3FC4)`); an opcode scan of both `0x0260`
    images finds zero references to either. So `Norm_L` acts entirely inside
    V.8, and what it changes is the **handoff page**:

    | `Norm_L` | `v8_pending_page` | final residency |
    |---|---|---|
    | `0x9100` (ours) | `0x0007` | bootpage 7, `0x0260`, trn `0x002a` |
    | `0x9102` (+V22) | `0x0001` | bootpage **1**, `0x0266`, trn `0x0034` |
    | `0xA13F` both ends | — | bootpage **2**, `0x0267`, trn `0x0060` |

    `v8_line_result` (`DM(0x3FC4)`) moves with it: `0x8100 → 0x8000` when V.8
    hands off to page 7, `0x8100 → 0xA002 → 0xA802` when it hands off to
    page 1. With only V90+V34+V32ext advertised, V.8 selects page 7 and page 7
    stalls; add V22 and V.8 selects a page that trains. **Page 7 is exactly as
    broken as before** — the TrnProgress numbers in the bisect above are
    page-1 and page-2 progress, not page-7 progress, which is what the earlier
    "walks on" reading missed.
  - **The other twelve words are inert, tested with the page pinned.** Holding
    `Norm_L` at ours — which is what keeps V.8 handing off to page 7 — and
    applying all twelve remaining run48 values at once
    (`GEN_setup0 0x0040`, `DISP_setup 0x0008`, `V8_setup 0x0000`,
    `Info0_setup 0xf1fd`, `TD`/`TA 0x000c`, `TX_LEVEL_TUNE 0x00b8`,
    `DCD_HYST 0x0003`, `P2SD 0xabcd`, `speed_sel_l 0xfffe`,
    `Mintimer 0x0014`, `Info0D_setup 0x0377`) changes **nothing**: page 7 for
    43.5% of the call against a baseline 43.4%, the same state walk
    `0x0020→0024→0026→0028→002a`, nothing beyond `0x002a`, same final
    residency. No bisect is needed on a group whose whole is inert. So the
    write database is not where we differ from run48 at Phase 2, and the
    thirteen-word result in `84c101a` was `Norm_L` and nothing else.
  - **Where we actually differ is our transmit, and run48 is the control.**
    Same firmware, same role, measured on each card's own transmit:

    | | at its `0x0028` transition | top bins |
    |---|---|---|
    | run48 (works) | 5.8–6.2 s | **1200 Hz: 366 → 686**, 1800/2400 ≈ 0 |
    | ours (stalls) | 5.3 s | **2100 Hz: 1451** (still ANSam) |
    | ours, later | 8.0 s | 2400: 1429, 1800: 1226 (Tone A + guard) |

    run48 puts a 1200 Hz carrier on the line exactly at `0x0028 → 0x002e` —
    INFO0a, DPSK on 1200 Hz per V.34 §11.2.1.2.1. Ours is still transmitting
    ANSam at the corresponding moment, then goes to the Tone A pair, and emits
    1200 Hz at no point in the call. So `0x002a` is our answerer waiting in a
    state it should have left by transmitting INFO0a, and the defect is the
    transmit path, not reception, not the database, and not the peer.
  - **The structural reason, and it reframes the whole strand: this project
    has never originated a call.** Scanning every archived DM capture for
    `GEN_SETUP1` bit 3 (guide §5.3.1: "Channel selection, 1 = call or
    originate channel, 0 = answering"), taking the dominant value per capture:

    | | answer | calling |
    |---|---|---|
    | live/tower captures | **110** (+4 at `0x0486`, also answering) | **0** |
    | loopback captures | 269 | 297 |

    **Not one live capture has the card as the caller.** Every control this
    repo has ever validated against — run34, run48, the CX and Courier
    sessions, the V.90 connect — is the card answering a real modem. So a
    `DM(0x166C)` diff against run48 can only ever describe answer-side
    behaviour, and there is no ground truth at all for the calling side.
  - **And our caller is our answerer with one bit flipped.** Two analog109
    ends of the same loopback, same firmware, one in each role: the write
    database differs in **exactly one word**, `GEN_SETUP1` `0x048C` against
    `0x0484`. Per the guide's own worked examples that is *correct* — Table 14
    (calling mode training) and Table 15 (answer mode training) differ in
    `Gen_setup1` alone, with `GEN_setup2 0x0030` and `Wstatus 0x2000`
    identical. So the database is not where the caller-ness is missing.
  - **It is missing from the path.** The guide's Table 14 note is explicit
    that the training script runs "when the dial page is active", and that
    "after the script is executed, the dial page requests the host to boot the
    V.8 page". Our caller does none of that: `_maybe_request_v8` writes
    `DM(0x0491)`, `DM(0x3FB0)` and the status strobe directly to fake the
    request, and says so — *"the legitimate path is an AT dial script this
    harness bypasses"* (`dial_tikrnl_drive.py:602`). `eicon_mips_shim`'s
    `ORIGINATE_V8` is the same bypass on the native path. So DIAL's
    calling-mode script never runs on either backend, and every page
    downstream inherits state that was never set up as a call-side call.
    That, not a page-7 branch, is why both ends transmit the answer-side
    Phase 2 signal.
  - **Withdrawn: "the caller reaches V.8 through a forced request."** That
    citation — `dial_tikrnl_drive.py:602`, *"the legitimate path is an AT dial
    script this harness bypasses"* — is the **direct** backend's code, and the
    pairing under test runs `--caller-kernel-dispatch`. Control: with
    `--no-originate-v8` *and* no DIAL entry run, the caller is still on
    bootpage 6 / overlay `0x025F` at the first captured sample and still walks
    to page 7 at 3.68 s. So the kernel-dispatch caller already originates
    through the firmware's own download-request loop — the same reason
    RXSAMPLE is maintained on this backend and frozen on the direct one. The
    bypass is real, but not in the path we are measuring.
  - **Built and measured inert: the caller now runs DIAL's own NORM entry.**
    `EICON_ANALOG_DIAL_ORIGINATE=1` runs
    it in `analog_kernel_dispatch.configure_modem`, the way
    `dial_kernel_dispatch.py:639` and `eicon_mips_shim.py:4021` already do for
    the answering side. **The address is not `0x13CC`**: that is a TrnProgress
    store in the Analog DIAL image. The documented sequence — `MODE_CTL(2e80)`,
    `GEN_SETUP1 AND 0xFFBF OR 0x0080` written back, the six-word clear at
    `0x3FA7` — is at **`0x13E3`**, behind the same M4/M5/M6 preamble F34 has at
    `0x13C9..0x13CB`. It runs, completes, and changes nothing: `GEN_SETUP1`
    `0x048C → 0x048C` (a fixed point — the routine *sets* bit 7 rather than
    consuming it, so `dial_v8_call.md`'s "test+clear NORM bit" is wrong),
    TrnProgress still `0x0000`, and both ends still stall at `0x002a`.
  - **The DSP code is all there. The transmit mode word asks for the probing
    signal and the vector row never follows.** Page 7 is a vector machine: a
    mode word's **top two bits** index a row of routine pointers loaded from
    PM tables by `PM 0x34AE`, and `PM 0x343D..0x3454` is a change detector —
    for each mode word, compare against its shadow, and on any difference call
    that block's loader with `SR0` = the new value:

        343d  SR0 = DM($1642)      ; transmit mode word
        343e  AY1 = DM($168B)      ; its shadow
        343f  AR  = SR0 XOR AY1
        3440  IF NE CALL $349E     ; reload DM(0x166A..0x166D)

    The transmit column, `PM(0x2BD6) = 0x2C32`, holds all four modes:

    | index | vector | what it does |
    |---|---|---|
    | 0 | `0x340B` | silence |
    | 1 | `0x3415` | bypass — the chain output, our 1800+2400 |
    | **2** | **`0x340D`** | the 64-entry L1/L2 probing table, full scale |
    | **3** | **`0x340F`** | the same table, 6 dB down |

    Measured live, on both ends of the two-Analog pairing:

    | | distinct values |
    |---|---|
    | `DM(0x1642)` mode word | `0x0000`, `0x0400`, **`0x9400`**, `0x5400` |
    | `DM(0x166C)` vector | `0x0000`, `0x340B`, `0x3415` — 5 writes, **never `0x340D`** |

    **Withdrawn: "the mode word asks for the probing transmit."** That read
    `0x9400`'s *top* two bits. The index for this column is bits **13:12**,
    because `PM 0x34AF` shifts `SR0` and writes the result back to `SR0`, so
    the loop's three iterations extract successive 2-bit fields from the top
    down, and `DM(0x166C)` is written on the third pass with the second
    field's vector. Watching all three words in one run settles it — the
    detector fires every time, the shadow follows, and the vector is always
    exactly what the firmware asks for:

    | `DM(0x1642)` | bits 15:14 | bits 13:12 | predicted | observed |
    |---|---|---|---|---|
    | `0x0000` | 0 | 0 | `0x340B` | `0x340B` |
    | `0x0400` | 0 | 0 | `0x340B` | `0x340B` |
    | `0x9400` | 2 | **1** | `0x3415` | `0x3415` |
    | `0x5400` | 1 | **1** | `0x3415` | `0x3415` |

    Four of four. There is no emulator defect and no missed reload: the
    machinery is correct, and the mode word's bits 13:12 are simply never
    `2` or `3`. So the probing transmit is **never requested**, which is the
    opposite of what the previous commit concluded.
  - **Where the mode word comes from: a script record, exactly like V.8's.**
    `PM 0x3376..0x3383` walks a PM record through `I4` as (offset, lo, hi)
    triples and writes `DM(0x1642 + offset)` wholesale — the same machinery as
    the V.8 script-record loader at `PM 0x37B7` writing `DM(0x073F + offset)`
    (`analog_v8_oracle.md`). `DM(0x1642)` is offset 0 of that block. So page 7
    is a script machine like V.8, the transmit mode is a *field in the record*,
    and we never walk to a record whose bits 13:12 select the probe.
  - **Found, and it parks exactly like V.8 did. The cursor is `DM(0x1679)`.**
    `PM 0x331E` is the state-entry routine and names everything: it stores the
    record address to `DM(0x1679)`, loads it into `I4`, calls the record loader
    through `DM(0x169F)`, stores the advanced cursor back, and publishes
    `DM(0x1652) & 0x00FF` as **TrnProgress** (`DM(0x3FC2)`). Watched live:

        0x0B87 -> 0x0B8D -> 0x0AC4 -> 0x0AF1 -> 0x0B00 (0x0022)
        -> 0x0B0F (0x0024) -> 0x0B21 (0x0026) -> 0x0B30 (0x0028)
        -> 0x0B42 (0x002a) -> 0x0B30 -> 0x0B42 -> 0x0B30 ...

    A **two-record loop between `0x0B30` (state `0x0028`) and `0x0B42`
    (state `0x002a`)**, forever, driven by `PM 0x334D` and `PM 0x3352`. Not a
    stall — a wait state cycling, the same shape as V.8's CI-retransmit loop.
  - **What it is waiting for: INFO reception.** `PM 0x3335..0x333F` is the
    condition evaluator — countdown `DM(0x1647)`, then the routine in
    `DM(0x169A)` (`IF LE JUMP 0x334E`), then the routine in `DM(0x1696)` with
    `MR0 = DM(0x1692)` (`IF LE JUMP 0x334D`, the arm we take). Live, those
    vectors are `{0x33C2, 0x3391, 0x339E}` and `{0x33C2, 0x33C4}`:

    | routine | what it tests |
    |---|---|
    | `0x33C2` | `AR = 0 + 1` — the constant true, never taken; V.8's `0x37D5` idiom |
    | `0x3391` | decrements `DM(0x1650)`, a countdown |
    | `0x339E` | reads and clears a flag at `DM(0x063C)` |
    | `0x339B` | reads and clears **`DM(0x0685)`**, the CSV's `info_rx_event` |
    | `0x33A3`/`0x33C4` | **`DM(0x0686)`**, `info_rx_complete`, then the parser `DM(0x16BD) == 0x3520` |

    `info_rx_complete` is `0x0000` for the whole call on both ends, in every
    capture in this section. So the escape condition is "an INFO sequence was
    received", and it can never fire, because neither end ever transmits one —
    which is the same fact the 150 Hz comb and the 1200 Hz measurements
    already showed from the wire.
  - **So the two pairings are deadlocked, symmetrically**, each parked in
    `0x0028 ↔ 0x002a` waiting to *receive* an INFO0 that the other will only
    send from a later state. The asymmetry that should break it is V.34
    §11.2.1.2.1: the answer modem sends INFO0a **unprompted**, after the
    75 ± 5 ms silence ending Phase 1. Ours does not — it waits.
  - **The two records, decoded.** They are DM, not PM, and the INFO overlay
    supplies them — its own block is `DM 0x07A0..0x0BB0`, and the image's
    `0x0B30` is `0x100E`, matching the live read exactly. The loader
    `PM 0x3376` reads (w0, w1, w2) triples, `offset = w0 >> 8`,
    `value = (w1 >> 8) | ((w2 >> 8) << 8)`, terminating on offset `0x19`:

    | | `0x0B30` — walked forever | `0x0B42` — never walked |
    |---|---|---|
    | `DM(0x1652)` progress | **`0x002a`** | **`0x002c`** |
    | `DM(0x1650)` countdown | **`0x0064`** (100) | `0x0001` |
    | `DM(0x1654)` | `0x0022` | `0x001a` |
    | `DM(0x1658)` | `0x0006` | `0x0007` |
    | `DM(0x164F)` / `DM(0x164A)` | `0x02BC` | `0x0000` |
    | `DM(0x1642)` transmit mode | **absent** | **absent** |

  - **Three things fall out of that.** *First*, neither record writes offset 0,
    so neither state touches the transmit mode — the probing transmit is
    selected by some other state's record, one the walk never reaches, which
    is why `DM(0x1642)`'s bits 13:12 stay at 0 or 1.
    *Second*, **the wait cannot time out.** `DM(0x1650)` is the countdown that
    routine `0x3391` decrements, and record `0x0B30` reloads it to 100 every
    time it is walked — and the loop re-walks `0x0B30` on every cycle. So the
    timer resets faster than it counts, and the state waits forever instead of
    failing over. That is why the pairing hangs at `0x002a` rather than
    falling back.
    *Third*, the next state would be **`0x002c`**, where live run48 goes
    `0x0028 → 0x002e`. So even with the condition satisfied our walk is on a
    different branch from the working call's, and `0x002e`'s record is
    somewhere we have never been.
  - **Something does select the probe, and it is one record.** Decoding all
    **50** records in `DM 0x07A0..0x0BB0` offline, every value any record
    writes to `DM(0x1642)`:

    | value | bits 13:12 | records |
    |---|---|---|
    | `0x0000` | 0 | `0x07A0`, `0x0AC4`, `0x0B69`, `0x0B93` |
    | `0x0400` | 0 | `0x07E2`, `0x0B00` |
    | `0x1400` | 1 | `0x0986`, `0x0A5E`, `0x0AA0` |
    | `0x5400` | 1 | `0x0830`, `0x095F`, `0x0A3A`, `0x0B21` |
    | **`0x6400`** | **2** | **`0x08AE`, progress `0x0036`** |
    | **`0x7400`** | **3** | **`0x0905`** |
    | `0x9400` | 1 | `0x07F7`, `0x0A49`, `0x0A79`, `0x0B0F`, `0x0B54` |

    The four values we see live are exactly the four in the chain we walk. The
    probe is selected by `0x08AE` at **progress `0x0036`** — a state live
    run48 reaches and we never do.
  - **Both roles walk the identical chain**, cursor for cursor:
    `0x0B87 → 0x0B8D → 0x0AC4 → 0x0AF1 → 0x0B00 → 0x0B0F → 0x0B21 → 0x0B30 ↔
    0x0B42`. Calling and answering, byte for byte. That is the measured form
    of "two answer modems".
  - **The chain is chosen at `PM 0x34B5..0x34CA`, by three inputs:**

        34b5  AR = $07A0 ; AX1 = $07A0        ; default
        34b7  AX0 = DM($3F8A) ; AY0 = $5678
        34ba  IF EQ JUMP $34C3                ; marker -> keep 0x07A0
        34bb  AX0 = DM($3EE0) ; AF = AX0 AND $0040
        34be  IF EQ JUMP $34C1                ; GEN_SETUP0 bit 6 clear -> keep 0x07A0
        34bf  AR = $0AE8 ; AX1 = $0AC4        ; bit 6 set
        34c3  CALL $34CB                      ; AF = DM($3F94) AND $0008
        34c4  IF NE JUMP $34C8
        34c5  MR0 = AX1 ; I4 = $3376          ; bit 3 clear -> 0x0AC4, loader 0x3376
        34c8  MR0 = AR  ; I4 = $336A          ; bit 3 set   -> 0x0AE8, loader 0x336A

    Measured against run48: `DM(0x3F8A)` is `0x0000` in both, `GEN_SETUP0`
    bit 6 is set in both (`0x0040` live, `0x00C4` ours). **The one that
    differs is `DM(0x3F94)`: it reaches `0x0009` — bit 3 set — on the live
    card, and stays `0x0000` on both our ends.** So run48 enters at `0x0AE8`
    through loader `0x336A`, and we enter at `0x0AC4` through `0x3376`.
  - **Nothing host-side writes it — the DSP's own V.8 page does, and ours
    never reaches the state that would.** `0x3F94` is read-database (offset
    `0xB4`, in the `0x80..0xFF` half the guide defines as DSP→host), so it is
    published, not written by the MIPS or the driver. In run48 it goes
    `0x0000 → 0x0009` at **4.48 s, with overlay `0x025F` still resident** —
    V.8 publishes it more than a second before page 7 loads at 5.72 s. And the
    V.8 state walks diverge before that:

    | | states while `0x025F` is resident |
    |---|---|
    | run48 (works) | `0x0000 → 0x0004 → `**`0x0003`**` (4.48 s) → 0x0009` |
    | our answerer | `0x0000 → 0x0004 → `**`0x000b`** |
    | our caller | `0x0001 → 0x0002 → `**`0x000b`** |

    Neither of our ends ever enters V.8 state `0x0003`, which is exactly where
    the live card publishes `DM(0x3F94)`. Both leave through `0x000b` instead.
  - **The CM state exists, is unique, and we never enter it.** Decoding all
    **43** records in V8.ANA's `DM 0x0000..0x036D` (loader `PM 0x37B7`:
    `offset = w0 & 0xFF`, `value = (w1 & 0xFF) | ((w2 & 0xFF) << 8)`,
    terminator offset `0x11`; validated against the live CI-wait state at
    cursor `0x01DC`, whose offset 1 decodes to `0x0086` exactly as measured),
    **exactly one record sets action-mask bit 4 — the CM builder at
    `PM 0x3828`: record `0x021B`, mask `0x0016`** (bits 1, 2, 4).
  - The caller's actual walk, with the codec at 9600 so ANSam *is* detected:
    `0x0341 → 0x0194 → 0x01BB → 0x01C7 → 0x01DC ↔ 0x01EE ↔ 0x0200` (the CI
    retransmit loop, mask `0x0086`) `→ 0x0281 → 0x028D → 0x029F → 0x02AB →
    0x031D` (mask `0x0100`) `→ 0x033B` (mask `0x0001`). It escapes the CI loop
    correctly and then walks a branch that never reaches `0x021B`, so bit 4 is
    never set and no CM is ever built.
  - **Destinations are not record fields.** No record carries offsets
    `0x51`/`0x52` (`DM(0x0790)`/`DM(0x0791)`). They are written at runtime by
    `PM 0x37B5`, the indirect table loader at `PM 0x37B0..0x37B6`: read an
    index from `DM(I0)`, add a base in `AY0`, use the sum as an address, and
    store what it points at. Live on the caller the destinations written are
    `0x0341`, `0x02B7`, `0x0200`, `0x0281`, `0x01DC`, `0x031D` — **never
    `0x021B`**.
  - **The tables are named, and the path to CM is fully mapped.**
    `PM 0x379A..0x37A3` sets them up: destinations are
    `DM(0x035B) + index` with the indices in record offsets `0x0D`/`0x0E`,
    conditions are `DM(0x034A) + index` from offsets `0x0F`/`0x10`. The
    destination table has 19 entries; **index 17 is `0x021B`, the CM state**,
    and the only record carrying index 17 is **`0x02D5`**. Nothing in the
    table points at `0x02D5` itself — it is reached by *fall-through*, since
    records are contiguous and `PM 0x3795` advances the cursor naturally:

        0x02AB -> 0x02B7 -> 0x02C9 -> 0x02D5 -> (index 17) -> 0x021B = CM

    **`0x02AB` is a state we do walk.** Four records of *table* distance
    separate the caller from building a CM — but see the next two bullets:
    that distance is not fall-through the cursor takes on its own.
  - **The fork, as it was reasoned.** `0x02AB`'s own condition is index 0 →
    `PM 0x37D5`, the constant true, which an `IF LE` never takes — so the
    inference was that by itself `0x02AB` falls through toward CM, and that
    what diverts us is *persistence*: a record only writes the fields it
    carries, so the condition/destination pair from `0x029F` (condition
    index 1 → `PM 0x37D7`, the `DM(0x0749)` countdown; destination index 10 →
    `0x031D`) is still loaded when `0x02AB` runs. The countdown expires and
    branches to `0x031D`, and the walk continues `0x031D → 0x033B` into the
    mask `0x0100`/`0x0001` states that never build a CM. Live timings agree —
    `0x029F` at cycle 47,927,968, `0x02AB` at 47,948,763, `0x031D` at
    47,965,396.
  - **Tested, and the fall-through half is wrong: `0x02AB` has no other exit.**
    `EICON_ANALOG_PIN_DM` now takes an `@GATE:VALUE` suffix so a word can be
    held only while `DM(0x049F)`, the script cursor, is on the state under
    test, and `EICON_ANALOG_TRACE_CURSOR` prints the walk. Against the V90D
    answerer (`--answerer-firmware-set pri117 --answerer-modulation v90
    --caller-firmware-set analog109 --caller-modulation v90a
    --caller-kernel-dispatch --analog-codec-rate 9600`) the trace reproduces
    the archived walk exactly, and puts `0x02AB` at just **12 codec samples**
    (29119 → 29131), so a per-frame pin gets ~15 applications inside it.

    | arm | pin | result |
    |---|---|---|
    | baseline | none | `0x029F → 0x02AB → 0x031D → 0x033B` |
    | destination | `0x0791=0x02b7@0x049f:0x02ab` | 15 applications, **still `0x031D`** |
    | countdown | `0x0749=0x7fff@0x049f:0x02ab` | **parks on `0x02AB` for the remaining 268,000 samples**, `TrnProgress` `0x000b` |

    Holding the countdown off does not produce fall-through to `0x02B7`. It
    produces no advance at all. **So the branch to `0x031D` is not a diversion
    from a fall-through — it is the only exit `0x02AB` has**, and the length
    of `DM(0x0749)` is exonerated: it is not the bug, and lengthening it is
    not a fix. This fits the advance mechanism already recorded above — the
    cursor moves only when `DM(0x076F)` is set (`PM 0x378B..0x378C`), so
    record contiguity makes fall-through *available*, never automatic.
  - **The dispatcher explains it, and fall-through is conditional.**
    `PM 0x37A4..0x37AF` runs three condition slots, and the first one is the
    fall-through gate:

        37a4: I4 = DM($0794)   ; slot 0
        37a6: IF LE JUMP $378C ; -> set DM(0x076F) only, cursor unchanged = FALL-THROUGH
        37a7: MR0 = DM($0790)  ; slot 1's destination
        37aa: IF LE JUMP $378B ; -> DM(0x049F) = MR0, flag set
        37ab: MR0 = DM($0791)  ; slot 2's destination
        37ae: IF LE JUMP $378B
        37af: RTS              ; nothing taken -> flag clear -> the state repeats

    `0x378C` sets the advance flag *without* moving the cursor, so the next
    pass through `0x378E..0x3795` loads the contiguously next record — that,
    and only that, is fall-through. It requires slot 0 to return `<= 0`, and
    at `0x02AB` slot 0 is `PM 0x37D5`, which is `AR = 0 + 1` and never `LE`.
    **So `0x02AB` structurally cannot fall through**, which is exactly what
    the countdown arm measured.
  - **The tables are not in the static DM; an initialiser list writes them.**
    Reading `DM(0x034A)`/`DM(0x035B)` straight out of the overlay's `dm.bin`
    gives nonsense (index 17 reads `0x01B6`, not `0x021B`). They are filled at
    load from an (address, value) pair list at `DM(0x0688..)` in the same
    image. Walking it reproduces every value measured live, and adds the one
    that mattered:

        conditions, base 0x034A:  0 -> 0x37D5 (never LE)   1 -> 0x37D7 (countdown)
        destinations, base 0x035B: 10 -> 0x031D   14 -> 0x02B7   17 -> 0x021B (CM)

    **`0x02B7` is destination index 14 — a real branch target.** The "four
    records of fall-through" framing was never needed: nothing has to fall
    through to reach either `0x02B7` or the CM state.
  - **The exit from `0x02AB` is slot 1, through `DM(0x0790)`.** Not `DM(0x0791)`
    as assumed above. Only that fits both arms — pinning `DM(0x0791)` did
    nothing across 15 applications, while pinning the countdown parked the
    machine, so the condition that fires is slot 1 and its destination word is
    `DM(0x0790)`. (Slots are re-resolved from the index words at
    `PM 0x379A..0x37A3` only inside the advance block, so while a state is
    parked `DM(0x0790..0x0794)` persist and a per-frame pin does reach them.)
  - **Pinned to the CM state, the builder runs and the downstream stall
    clears.** `EICON_ANALOG_PIN_DM=0x0790=0x021b@0x049f:0x02ab`:

    | | baseline | pinned |
    |---|---|---|
    | cursor | `0x02AB → 0x031D → 0x033B` | `0x02AB → `**`0x021B`**` → 0x0236 → 0x024E → 0x0254 → 0x0269 → 0x0275 → 0x0281` |
    | action mask | `0x0086 → …` | `0x0086 → `**`0x0016`**` → 0x0040` |
    | TrnProgress | `0x0001 0x0002 0x000b 0x0024 0x0026 `**`0x002a`** | `0x0001 0x0002 0x000b `**`0x0005 0x0006 0x0009 0x001f`** |

    Mask `0x0016` is bits 1, 2 and **4** — bit 4 being the CM builder at
    `PM 0x3828`, so the builder dispatched. The caller then walks six records
    no run in this repo has entered, and the progress ladder changes wholesale:
    it reaches `0x0009`, the value the live run48 card publishes, and `0x001f`,
    **bypassing the `0x002a` park the whole page-7 strand sits behind**. That
    is the strongest evidence yet that the CM and the page-7 stall are one
    fault and not two.
  - **The CM builder is what writes `DM(0x3F94)`** — the question left open
    above ("find what writes it on a live call... that is the whole remaining
    question for `0x002a`"). `PM 0x3828`, the bit-4 routine, has it in plain
    sight two instructions in:

        3828: I0 = $05A8          ; the CI/CM message buffer
        3829: DM($0492) = I0
        382a: DM($037F) = I0
        382d: AR = $0008
        382e: DM($3F94) = AR      ; <- bit 3, the INFO chain selector

    The JM builder at `PM 0x385E` clears it (`DM($3F94) = M0`). So building a
    CM sets bit 3, INFO's `PM 0x34C3` then reads bit 3 and takes the `0x0AE8`
    chain through loader `0x336A`, which is the chain containing record
    `0x08AE` that transmits the probe. run48 measures `0x0009`; the builder
    supplies the `0x0008`. **The causal chain from "no CM" to "page 7 parks at
    `0x002a`" is now closed end to end**, and it is one fault, not two.
  - **Independently confirmed from the record data: `0x02AB` cannot fall
    through.** Decoding the records straight out of the DM initialiser list
    reproduces every mask measured live — `0x01DC` = `0x0086` (CI), `0x021B` =
    `0x0016` (CM), `0x031D` = `0x0100`, `0x033B` = `0x0001` — so the decode is
    sound. Offset `0x11`, the slot-0 (fall-through) condition index, reads
    **0 for `0x02AB`** (→ `PM 0x37D5`, never `LE`), 9 for `0x029F` and 5 for
    `0x021B`. That is why `0x029F` fell through to `0x02AB`, why `0x02AB`
    cannot fall through at all, and why `0x021B` fell through to `0x0236`.
  - **Two corrections to the measurements above.**
    - The "zero V.21 channel 1 in either run" scan was a broken measurement,
      not a finding: the threshold was applied to a length-normalised Goertzel
      and meant nothing. An energy profile is unambiguous. The caller's
      transmit is **silent until 3.5 s**, then in baseline sits at a flat
      rms 674 for the remaining 33 s — and the spectrum there is 1800 Hz +
      2400 Hz, *bit-identical at t=4 s and t=10 s*. That is a frozen buffer
      being replayed, not a modulated CI. So the long-standing "the Analog
      caller sends CI and never CM" is a statement about the script, and on
      the wire it never sent a real CI either.
    - **Under the pin the caller transmits nothing at all**: rms 18 over the
      call against baseline's 639, and flat zero from 4.0 s to the end. Taking
      the CM branch does not merely fail to modulate a CM, it leaves the loop
      that was producing any output. So "the CM is built and never
      transmitted" is right, but the reason is that the pinned path never
      arms a transmit, not that a CM specifically failed to modulate.
  - **Where the arming lives, and the thread that is still loose.** The action
    dispatcher is `PM 0x3B81..0x3B94`, and there are five call sites, each with
    its own mask word, routine table and count:

        3b6b: I4 = $3DF6  CNTR = 9    <- bit 4 = CM, 5 = JM, 7 = CI
        3b70: I4 = $3E0F  CNTR = 13   mask DM(0x078A)
        3b74: I4 = $3E1C  CNTR = 16
        3b79: I4 = $3E2C  CNTR = 8    mask DM(0x0787)
        3b7e: I4 = $3E34  CNTR = 4    mask DM(0x0770)

    `CNTR = 9` settles it: the `0x3DF6` table really is 9 entries, so an
    earlier guess here that mask bit 10 arms the transmit was wrong — no
    record sets bit 10, and bit 10 of that mask does not exist. The arming
    routines are a **separate** table at `PM 0x3E00`: `0x38E8` arms the
    transmit buffer at `0x05A8` (the CI/CM buffer), `0x38EB` the one at
    `0x06EC` (JM). `PM 0x38D9` is the bit feeder that walks `DM(0x037F)` and
    hands the modulator `DM(0x03A1)`.

    **`PM 0x3E00` has no call site among the five.** Finding what indexes that
    table is the next step, and it is the V.8 analogue of page 7's `DM(0x166C)`
    transmit selector.
  - Standing caveat: this is a pin, so it establishes what the path *would* do
    and not what the firmware does unaided. What makes a card resolve slot 1 to
    index 17 at `0x02AB` on its own is still open, and with no caller capture
    the only comparison available is the V.8 `0x0003` divergence below.
  - **Superseded by measurement: unaided, the caller does build a CM — offline.**
    Since the codec-rate default moved to 9600 (`c934385`), replaying a
    loopback's own `caller.rx.ulaw` through `AnalogKernelModem` walks
    `0x01DC ↔ 0x01EE ↔ 0x0200 → 0x0281 → 0x028D → `**`0x0200 → 0x021B`**` →
    0x0236`, with no pin of any kind. The branch is taken at the exact sample
    `DM(0x0778)` reaches 240, and `DM(0x3F94)` goes `0x0000 → 0x0008` — the
    INFO chain selector the whole page-7 strand was waiting on. The same holds
    on a synthetic normative ANSam, and at every replay alignment tried (rx
    offsets 0–20000 samples, with and without the 1 s guard prefix and the 2 s
    setup gap): eight for eight.
  - **But the live loopback caller, on the same run's rx stream, does not** — it
    walks `… 0x0200 → 0x01DC → 0x01EE → 0x0281 → 0x028D → 0x029F → 0x02AB →
    0x031D` and never revisits `0x0200`. Watched live, `DM(0x0778)` is reset to
    zero over and over by `PM 0x3ED3` and never reaches 240, while the replay of
    that run's own recording reaches 813. Detector A is not the difference:
    `DM(0x07BC)` peaks at 7053 live against 8077 in replay, both well over the
    2000 threshold, and the media loop reports 0 substituted and 0 dropped
    samples.
  - **So the next question is sharp and small: what does the live path deliver
    to `frame_fast` that a replay of its own `caller.rx.ulaw` does not?**
    `--no-originate-v8 --no-originate-line-ready` changes nothing, and
    `analog_line` is identity with echo off, so the candidates are the guard/gap
    boundaries and the jitter buffer's ordering. The measurement that settles it
    is a byte-for-byte dump of the samples handed to `frame_fast`, compared
    against the recorded stream — the harness has no such dump today, and adding
    one is the next step. Until then, treat "the caller never builds a CM" as
    **false in the firmware and true in the live harness**, which inverts where
    to look.
  - **Done, and the audio is exonerated: the live path delivers the recording
    verbatim.** `EICON_DUMP_FED_RX=1` writes `<prefix>.fed.ulaw` (one codeword
    per sample, at the `frame_fast()` call) and `<prefix>.fed.word.bin` (the
    int16 `line_rx_word()` actually passed); `tools/fed_rx_diff.py <prefix>`
    aligns it against the wire. On the caller: **216,000/216,000 codewords
    equal, 100.0000%**, at a single offset of 1120 samples of leading silence
    (the rx guard), and every one of the 217,120 line words equals
    `decode_mulaw()` of its codeword — so `analog_line`, the jitter queue and
    the guard are all identity in the only sense that matters. The candidates
    that section named are all dead. (The answerer's own dump is 91%, which is
    just its 2 s setup gap and guard, and is the control showing the tool
    reports a real difference when there is one.)
  - **Alignment is exonerated too.** Prefixing the replay with 0, 160 or 1120
    silence codewords, with and without `set_line_hook(True)`, and with the
    loopback's own `EICON_ORIGINATE_*` environment, all reach the CM: peak
    `DM(0x0778)` 813, `DM(0x3F94) = 0x0008`, five for five.
  - **So the divergence is one decision, at one state, on identical input.**
    With the 1120-sample prefix the two walks agree *sample for sample* to
    `0x0281@23101 → 0x028D@23106`, and then:

        replay:  DM(0x0778) climbs 0 → 240 (one count per 12.5 bearer samples)
                 → 0x0200@26169 → 0x021B@26171 = CM
        live:    DM(0x0778) never passes 1 → dwell expires
                 → 0x029F@29106 → 0x02AB → 0x031D = the no-CM walk

  - **And the writer census names the routine.** Write-watching `DM(0x0778)`:
    **`PM 0x3F0E` is the incrementer**, `PM 0x3ED3` and `PM 0x3F13` the
    resetters. Over comparable windows the replay takes 666 increments against
    8,742 resets and gets its unbroken run of 240; the live caller takes **27
    increments against 9,163 resets**, every one of them writing `0x0001` —
    incremented from zero, then cleared before the next. **Next: what gates
    `PM 0x3F0E` and what fires `PM 0x3ED3`/`0x3F13`,** on a second input that
    is not the line samples, because those are now proven identical. The
    reported peak `DM(0x07BC)` (30,840 replayed here against the 7,053 live of
    the previous session) is the first thing to re-measure on both sides with
    the same window, since a detector level cannot legitimately differ on
    identical audio.
  - **⚑ FOUND, AND FIXED. The live caller was being handed −32768 for every
    negative sample.** `EICON_ANALOG_DM_CSV`/`EICON_ANALOG_DM_LIST` sample DM
    on the bearer frame boundary in *both* the live endpoint and an offline
    replay, so the two line up by sample index and diff. The first word to
    differ is `DM(0x03A3)` at sample 21407 — live `0xFF80`, replay `0x0080`:
    same magnitude, opposite sign. Every threshold (`DM(0x0747)`,
    `DM(0x0748)`) is identical throughout, and nothing differs before 21407.

    `PM 0x3FC4` writes `DM(0x03A3)`, and `PM 0x3F15` reads it back as the
    sample it pushes into the 15-tap correlator at `PM 0x39BD`, whose energy
    feeds the leaky integrator `DM(0x0777)` and hence the `PM 0x3F0E` /
    `PM 0x3F13` up-down counter. So `DM(0x03A3)` is the detector's *input*.

    The cause is in this repo, not the firmware. `eicon_adsp_sip.py` hands
    `line_rx_word()` straight to `frame_fast()`, and on analog109 that is a
    **signed Python int**, not a 16-bit word. `AnalogKernelModem.frame_fast`
    sign-extended it with `word - 0x10000 if word & 0x8000`, and in Python
    `-128 & 0x8000` is `0x8000` — so every negative sample was decremented by
    another 65536 and the clamp below turned it into full-scale −32768.
    `Card._present_line` masks, which is why the direct backend never saw it;
    and at `--analog-codec-rate 8000` there is no resampler and the word goes
    to `_codec_frame`, which masks too. **The defect was reachable only in the
    resampling path — that is, only in the V.90A configuration.**

    Proved before it was fixed, by A/B on one recording: feeding the replay
    the *masked* word builds the CM, feeding it the *raw signed* value
    reproduces the live no-CM walk state for state and sample for sample
    (`0x028D@23106 → 0x029F@29106 → 0x02AB@29119 → 0x031D@29131 →
    0x033B@29134 → 0x0341@29221`). `tests/test_analog_kernel_dispatch.py`
    `SignedLineWordTest` covers it, and was checked to fail without the fix —
    note every older test in that file masks the word itself, which is how
    this survived.
  - **With the mask, the loopback caller sends a CM and the answerer answers
    it.** The caller reaches `0x021B` at 26171 — the same sample as the replay
    — then walks on past where any replay has gone: `0x0236 → 0x024E → 0x0254
    → 0x0269 → 0x0275`. The answering end takes **`0x0004 → 0x0003`**, which
    §3 already identified as the signature of a peer that sent a CM, and boots
    **page 10 with `0x026E` INFOH.F34** instead of falling back to page 2
    V.32. Both ends then climb about twenty states to `0x0041`. **So "the
    Analog caller sends CI and never CM" is now closed, and the V.90A blocker
    has moved downstream into V.34 phase 2** — which is where §2 always said
    the queue was.
  - **⚑ And the blocker behind that was a 20,000-cycle frame budget silently
    truncating the INFO handshake.** Reaching page 10 for the first time
    exposed it immediately: at 4.40 s the answerer's core reported PC, loop
    *and* counter stack overflows, and four seconds later it dropped the call.
    The overflow trail ends `… 0662 001e 0272 0273` — an interrupt vectoring
    through `PM 0x001C` onto a stack already 14 deep — and the stack dump shows
    the chain `076c 15dc 1712 1729 3362 3798` present **twice**.

    `adsp2181_pcsp_window()` names the mechanism exactly. Until sample 49855
    the PC stack unwinds to depth **0** every frame; at 49856 its per-frame
    *minimum* jumps to 6, then 12, then pins at 16. Six frames leak at a time,
    which is that chain's depth. The cause: `adsp2181_run()` stops when the
    budget runs out, `adsp2181_call()` only discards its synthetic return when
    the core actually idled, so a truncated frame leaves the stack mid-call and
    the next frame's entry is pushed on top of it. The 16-deep hardware stack
    is gone in three frames, and control flow after that is not the firmware's.

    Measured rather than guessed: over 80,000 frames the worst single frame is
    **22,717 cycles**, on INFOH.F34 at `TrnProgress 0x0041` — against a 20,000
    budget. V.8 peaks at 4,736, which is why nothing had ever hit it. So the
    old default truncated exactly one frame of the INFO handshake and every
    frame after it ran on a corrupted stack. `FRAME_BUDGET` is now 65,536
    (~2.9x the measured worst case, still a runaway stop inside one media
    tick), and `_run_and_serve` reports a non-idle return once per call
    instead of continuing quietly — the silence was the actual defect.
  - **Result: both ends now run the V.34 phase 2/3 handshake.** V.8 → page 10
    INFOH → page 5 HV.34 on both sides, the answerer walking to `TrnProgress
    0x00a8` and the caller to `0x00a3`, then both restarting the ladder about
    every 6 s. No stack overflow anywhere in a 60 s call. **The V.90A caller
    has arrived at the V.34 phase 2/3 region §2's `0x00b0` entry describes** —
    the first time this configuration has been level with the live V.34
    blocker rather than upstream of it. The retry cycle is the next question.
  - **⚑ Correction, and the one that matters: that ladder was V.34 *fax*.**
    `am_firmware_contents.md` names `0x026E` INFOH "half-duplex V.34 phase-2
    negotiation (V.34 fax)" and `0x026F` HV34 "half-duplex V.34 modulation
    (V.34 fax)", and volume 02's *INFOH is not it* (`996ccee`) had already
    ruled INFOH out. So "both ends now run the V.34 phase 2/3 handshake" above
    was wrong: they were negotiating fax, and the 6 s retry loop is what that
    looks like when neither end is a fax terminal.

    **The selector is `Norm_H` bits 5–6 and it was ours.** `V8.ANA
    PM 0x3834..0x383D` picks the V.8 CM call-function octet from them —
    bit 5 → `0x0103`, bit 6 → `0x010B`, neither → `0x0107`. The harness applied
    one constant, `NORM_H_V8_MEDIA = 0x0021`, to *both* roles. That value is
    hardware-traced (`38cd94e`) and correct for the **answering** role, where
    the `0x20` bit is load-bearing — without it the answerer does not transmit
    ANSam at all — but on the **calling** role bit 5 is what declares the call
    a fax. Now split: `NORM_H_V8_ANSWER = 0x0021`, `NORM_H_V8_CALLING = 0x0001`
    (`EICON_NORM_H_CALLING` overrides).

    Chosen by A/B over a 40 s loopback, not by reading the octet encoding:

    | calling Norm_H | answerer pages | caller pages |
    |---|---|---|
    | `0x0021` (old) | 6 → **10 INFOH → 5 HV.34**, looping | same, looping |
    | `0x0041` | 6 → 7 INFO, stalls `0x002b` | 6 → 10 → 17 DIAL |
    | **`0x0001`** | 6 → 7 INFO → **14 V.90 DPCM** | 6 → 7 INFO → **13 V.90 APCM** |
  - **⚑ Both ends now load their V.90 pages.** The answering PRI card boots
    **bootpage 14, `0x026A` V.90 DPCM** at 7.30 s, and the Analog caller boots
    **bootpage 13, `0x026B` V90.ANA APCM** at 9.10 s — the digital side and the
    analogue side of a V.90 link, in the configuration this whole strand was
    for. The caller walks to `TrnProgress 0x0092`; the answerer reaches
    `0x0060` and then falls back to page 7 at 12.22 s. **That fallback is the
    next question**, and it is the first one this project has been able to ask
    with both V.90 pages actually resident.
  - **What it is stuck on, located but not fixed: V90D outer state `0x0060`.**
    `--trace-v90d-state` puts the answerer into page 14 at 7.2895 s and through
    `0x0050 → 0x0052 → 0x0053 → 0x0060` in three milliseconds, entering
    `0x0060` with `dwell=0x0031` and `next=0002/000c`, `test=0008/0003`. It
    then sits there for **4.9 s** while the dwell counts down, reaches
    `dwell=0000` at 12.183 s, goes to `dwell=ffff` and drops to page 7. The
    tests read `0000/0000` by then, so neither transition is ever satisfied.

    **Confirmed off the wire, which is what makes it a fact and not a reading
    of an instrument.** Per-second non-silence on the captures: the answerer
    transmits **0.0% for the whole 9–14 s window**, exactly its page-14
    residency, while the caller transmits 99.8% throughout and the answerer's
    own `.rx.ulaw` shows it is receiving that signal. So the answering V.90
    page is receiving fine and putting nothing on the line — the §2 "answering
    page stops publishing transmit data" shape, now on page 14.

    The immediate cause of the silence is one level down and is *not* a
    plumbing fault: write-watching `DM(0x3FB4)` on page 14 shows `PM 0x19ee`
    re-priming the generic pointer `0x3764` every frame and `PM 0x1a1e`
    immediately overwriting it with the serializer's word, which is **`0x0000`
    on all 39,302 page-14 frames**. The serializer is emitting zeros because
    the outer machine never leaves `0x0060`. So the transmit silence is a
    symptom of the parked state, and **what satisfies `0x0060`'s two
    transitions is the open question.** Do not chase the transmit path for it.
  - **Fixed on the way, because it would have corrupted that measurement:**
    the direct backend applied the generic `DM[DM(0x3FB4)]` indirection on
    page 14. Page 14 publishes the *sample itself* there, which is why
    `eicon_mips_shim.py` has taken the value directly since the native tower
    first reached this page; this backend had never been here before. It reads
    the same `0` either way while the serializer is idle, so it changes nothing
    today — and everything on the first frame that does publish, which is
    precisely the frame the next session will be looking at.
  - **The native tower is not a way round it.** `--answerer-native-mips` also
    reaches page 14 (23.56 s, and it does *not* fall back to page 7), and also
    goes silent — 0.0% non-silence from 26 s. Both backends, same wall.
  - **⚠⚠ Read the correction at the end of this run of bullets before using
    any of the next three.** They were all measured with the analog caller on
    the **direct** backend, which is not a configuration the analog caller
    works in, and two of their conclusions are wrong because of it.
  - **The direct backend collapses to 0.00x with `NORM_H_V8_CALLING = 0x0001`.**
    The rig paces at 1.01x through `f1d653e` and collapses at `40418ef`;
    `EICON_NORM_H_CALLING=0x0021` restores 1.01x and zero truncated frames, so
    the pacing figure reads that one constant. With `0x0001` the calling end's
    V8 overlay `0x025f` loops from the first frame — `EICON_FRAME_BUDGET`
    (new) at `0x400000`, 64x the shipped budget, still never reaches the task's
    return — and the core reports **PC stack overflow at `pc=0x2017`, depth 16,
    cycle 48395**. The PC histogram names the loop: `PM 0x2024..0x2048`, 18.7 M
    executions in 40 s, and it turns on `Norm_H` itself:

        2025  AR = DM($3F08)      ; Norm_H
        2027  AY0 = $0060
        2029  IF EQ JUMP $202F
        202f  AY0 = DM($3EE1)     ; GEN_setup1
        2032  IF NE JUMP $2047
        2047  I4 = DM($3972) / CALL (I4)

    So bits 5–6 are not only the CM call-function selector `V8.ANA PM 0x3834`
    reads. V.8 *also* branches on `Norm_H & 0x0060`, and `0x0001` clears both.
  - **There is no calling `Norm_H` that both avoids that loop and gets through
    V.8.** Swept `0x0021`, `0x0041`, `0x0061`, `0x0043`, `0x0045` over the
    mixed pri117/analog109 V.90 rig, 60 s each: every one of them is clean —
    zero truncated frames, 1.00x — and every one of them **parks the caller at
    `TrnProgress 0x0001` on bootpage 6 for the whole call**. The wire says what
    that is: the answerer transmits 100% from 5 s and the caller receives all
    of it, while the caller transmits at 2 s, then **nothing from 5 s to 20 s**.
    That is CI sent, ANSam heard, and no CM — the failure `839894e` closed,
    reappearing for every value that does not overflow the stack. The CM only
    appears under `0x0001`, i.e. only when V.8 is running inside the loop that
    destroys the PC stack, so **"both ends now load V.90" should be treated as
    a product of corrupted control flow until it is reproduced by a caller that
    returns.**
  - **⚠ Neither A/B table in `40418ef` or `f1d653e` reproduces here** — on the
    direct backend. Corrected below: both reproduce exactly under
    `--caller-kernel-dispatch`.
  - **✅ THE CORRECTION, and the finding worth keeping from the three bullets
    above: the analog caller has to run `--caller-kernel-dispatch`.** The
    direct backend clocks its codec at 8000, and `ansam_envelope_loss.md`
    already established what that costs — the whole envelope-detector chain,
    biquad included, runs 5/6 slow, its 14.4 Hz passband sits at 12 Hz, and
    ANSam's 15 Hz falls outside it. `DM(0x0778)` needs 240 and never leaves 0.
    So on the direct backend the caller sends CI, hears ANSam, and **cannot**
    answer it, which is every "parks at `TrnProgress 0x0001`, no CM" result
    above. The PC histogram is unambiguous: `PM 0x3817`, the **CI** builder
    (`0x03FF, 0x0001, 0x0109, 0xFFFF`), runs 24 times, and `PM 0x3828`, the
    **CM** builder that writes the call-function octet `PM 0x3834` selects,
    runs **zero**.

    Add `--caller-kernel-dispatch` to the same mixed rig and every row of
    `40418ef`'s table comes back, to the second:

    | calling Norm_H | answerer | caller |
    |---|---|---|
    | `0x0021`, `0x0061` | 6 → 10 INFOH → 5 HV.34, looping ~6 s | same |
    | `0x0041` | 6 → 7 INFO | 6 → 10 → 17 DIAL |
    | **`0x0001`** | 6 → 7 (3.78 s) → **14 V.90 DPCM (7.30 s)** → 7 (12.22 s) | 6 → 7 (5.58 s) → **13 V.90 APCM (9.10 s)** |

    Those are the previous session's own numbers — 7.30, 9.10, 12.22 — with
    **zero `[STACK]` overflows, zero truncated frames and 1.00x pacing**. So
    the page-14 work stands, `0x0001` stands, and the retraction two bullets up
    is itself retracted: what was corrupt was the backend the caller was run
    on, not the result.
  - **The rule this leaves.** The analog caller is only valid under kernel
    dispatch; `eicon_loopback.py`'s own docstring shows the mixed command
    without it, which is how this session lost a run of measurements to it.
    On the direct backend the caller cannot complete V.8 at all, and with
    `0x0001` it additionally spins in `PM 0x2024` until the PC stack is gone —
    a real defect in that backend, but not one that has ever been on the path
    to V.90.
  - **What `0x0060` is waiting for, measured on the corrected rig.** The
    scheduler at `PM 0x2f86` calls each test handler and transitions when it
    returns AR ≤ 0 (DM reads leave ASTAT alone, so the `IF LE` at `0x2f89`
    reads the handler's own last ALU result). The two tests resolve through the
    table at `DM(0x05E0)`: test 3 → `PM 0x2fff`, the dwell counter
    `DM(0x20E0)` — the timeout that fires at 12.183 s — and test 8 → `PM
    0x30a7`, `AR = DM(0x120A) XOR 1`. `DM(0x120A)` is set by the tone detector
    at `PM 0x0e2e` when the six-tap mean at `DM(0x0E38..0x0E3D)` exceeds
    `DM(0x1FF5)`, and only when bit 1 of `DM(0x1FF2)` armed it. `0x0060` is a
    wait-for-tone state.

    `--trace-v90d-state` now prints all of that, and the answer is not the one
    the shape suggested: **the detector is armed and it is tripping.**
    `arm=0002`, `thresh=02bc`, and the reconstructed magnitude peaks at
    `0x2a62` on entry and `0x54c3` across the 4.9 s park — thirty times the
    threshold. The state record is not decaying either: write-watching
    `DM(0x1FFC)` shows `PM 0x2fea` re-arming it with test index **8** fifty-six
    times over the park. Armed, tripping, re-armed, and still no transition.
  - **⚠⚠ WITHDRAWN, along with everything built on it: `DM(0x120A)` is not
    clobbered, and the watches that said it was were not measuring page 14.**
    `EICON_WATCH_OVERLAY` was only ever implemented in `eicon_mips_shim.py`.
    On the direct backend `--watch-*` arms at call setup, so a budget of sixty
    is spent within the first frames — on **V.8 and INFO**, pages 6 and 7 —
    and the log then reads as evidence about a page that has not loaded yet.
    That is where the "FFT trampling the flag" came from, and why `PM 0x3792`
    matched `V8.F34` word-for-word: it *was* V.8, running at the time.

    Fixed: with `EICON_WATCH_OVERLAY` set, this backend now holds arming until
    one of the named overlays is resident and logs `[watch] armed at sample …`
    when it does. Re-run gated, armed at 7.621 s on overlay `0x026a`, the
    writers of `DM(0x120A)` **on page 14** are:

    | writer | count | value |
    |---|---|---|
    | `PM 0x0e30` detector | 36 | `0001` |
    | `PM 0x30aa` consumer clear | 23 | `0000` |
    | `PM 0x0d91` | 1 | `0000` |

    Set by the detector, cleared by the consumer, nothing else — the same clean
    pattern as V90A's `DM(0x10F3)`. **So the flag is fine, the detector fires,
    the test is polled, and the outer machine still does not leave `0x0060`.**
    Withdrawn with it: the "second control word" `DM(0x0DE0)`, the wrong-DM-base
    hypothesis, and "V.8 leftovers execute on page 14" — all rest on the same
    ungated watches. Re-measure anything from them before reusing it.
  - **✅ ANSWERED: it is not waiting for anything. It is looping between two
    `0x0060` records.** Exec-watching the poll and the branch, gated to page 14:

    | PM | what it is | hits | value |
    |---|---|---|---|
    | `0x30a8` | just after `AR = DM(0x120A)` | 24 | **`ar=0001` 12x**, `0000` 12x |
    | `0x30a9` | just before the `XOR 1` | 20 | `ar=0001` 10x, `0000` 10x |
    | **`0x2f9a`** | **the transition being taken** | **20** | **`mr0=18ba` every time** |

    So the chain works end to end: the detector sets the flag, the poll reads
    **1**, `AR XOR 1 = 0`, `IF LE` at `0x2f89` is taken, and `PM 0x2f9a` writes
    `DM(0x120F) = 0x18ba` — the machine *does* leave record `0x18cc`. Twenty
    times in the sampled window.

    The reason four sessions of tracing never saw it move: `0x2f9b` calls the
    state entry and `0x2f9c` jumps straight back to `0x2f86` to poll again, so
    the round trip `0x18cc → 0x18ba → 0x18cc` completes **inside one frame**.
    `--trace-v90d-state` samples once per sample, after the frame, and always
    finds it back at rest on `0x18cc`.

    **So `0x0060` is a two-record closed loop, not a park**, and the dwell
    timeout at 12.18 s is the only way out of it because the loop has no other
    exit. The tone, the flag, the detector, the peer and the timing are all
    doing their jobs.
  - **⚑ What the loop is: the record cursor has run off the end of loaded PM.**
    `DM(0x120F)` is a *cursor* into a packed record stream in PM — `PM 0x2fb4`
    does `I4 = DM(0x120F)`, unpacks 0x17 words through `PM 0x2fe3`, and writes
    the advanced cursor back. Follow the cursor values the trace reports:

    | cursor | what is loaded there |
    |---|---|
    | `0x1d25`, `0x1cb9` | V.OWN (`0x026d`) — PM `0x1c00-0x1dff` is fully populated |
    | `0x1848`, `0x1854`, `0x1869` | TIKRNL's tail — PM `0x1800-0x18ca`, 201 words |
    | **`0x18cc`** | **nothing. Zeros.** |

    The highest non-zero word anywhere in `0x1800-0x18ff` is `0x18ca`, and
    `0x18cb` through `0x1bff` is an unloaded gap — no overlay in the set writes
    it: not V90D, not V.OWN, not INFO, not V.8, not DIAL, not SIG. The machine
    walks `0x1848 → 0x1854 → 0x1869 → 0x18cc` and **steps off the end of the
    image**, and from there it is unpacking zeros.

    That is one cause for every symptom: a record of zeros gives the degenerate
    two-record loop (`mr0=18ba` forever), the test slots that read `0000`, and
    a zero action vector — which is why **the page-14 transmitter emits nothing
    at all**. It also explains why the tone, the flag, the detector, the peer
    and the timing all measured healthy: none of them was ever the problem.
  - **Send/receive, measured on the corrected rig** (`gap0`, one clock):

    | s | ans TX | ans RX | cal TX | cal RX |
    |---|---|---|---|---|
    | 8–11 | **0.0%** | 99.8% | 99.8% | **0.0%** |
    | 12 | 42.0% | 99.8% | 99.8% | 42.0% |
    | 13–15 | 100.0% | 99.8% | 99.8% | 100.0% |

    **V90A sends correctly and continuously** (99.8% throughout page 13) and
    the answerer receives all of it. **V90D receives correctly and transmits
    nothing** for its entire page-14 residency, and starts transmitting again
    the moment it falls back to page 7 at 12.5 s. The caller hears silence
    only because there is nothing to hear.
  - **Where the record streams are supposed to be, and who seeds the cursor.**
    `PM 0x2f38..0x2f52` is a set of seeders, each loading an outer and an inner
    stream address and jumping to the common tail at `0x2f53`, which writes
    `DM(0x120F)` and `DM(0x204A)`:

    | seeder | outer | inner |
    |---|---|---|
    | `0x2f38` | `0x1D0A` | `0x1BEA` |
    | `0x2f43`, `0x2f4b` | `0x1D0A` | `0x1D6D` |
    | `0x2f50` | **`0x180F`** | **`0x1BEA`** |

    And the "next record" table at `DM(0x0613)` holds `180f 188a 18ba 1965 19f5
    1a28 1a8e` — streams at `0x18xx`, `0x19xx` and `0x1axx`. V90D's own PM
    blocks cover `0x1900-0x1a25` but stop at `0x1578` below that, so `0x180f`,
    `0x188a` and `0x18ba` fall in a gap its download never writes, and the only
    thing in `0x1800-0x18ca` is 201 words of TIKRNL's tail. That is what the
    machine has been walking.
  - **Tried and did not fix it: calling the overlay's own entry point.** V90D
    is unusual — `0x0260` INFO and `0x025f` V.8 declare **no symbols at all**,
    while V90D declares exactly one, symbol 0 at `PM 0x3602`, and that routine
    writes packed data into PM through `I7` (`PM(I7,M4) = SI`,
    `PM(I7,M5) = MR0`). This harness has never called it: `download_overlay`
    writes memory and resumes TIKRNL at `DM(0x31BB)`, deliberately skipping the
    `WSTATUS.BOOTFINISHED` acknowledgement that would complete a download.

    `EICON_OVERLAY_INIT=0x026a` (new) calls it. It does real work — non-zero
    words in `PM 0x1800-0x1bff` go from **201 to 623**, now reaching `0x1bff` —
    so the routine really is a stream builder and this is part of the picture.
    But `PM 0x18cc` stays zero, and the live call is unchanged: same walk
    `0x1d25 → 0x1848 → 0x1854 → 0x1869 → 0x18cc`, same loop, same fallback at
    12.38 s. Either the entry needs a context this call does not give it, or
    `0x18cc` is genuinely past the end of the `0x180f` stream and the record
    that points there is the defect.
  - **✅ SETTLED by the repo's own control: V90D is fine, the harness is not.**
    `run48` is a live SIP call in which the card answers a real modem and
    connects. Its CSV carries the outer-machine columns, and at **12.02 s** it
    enters **exactly the record this loopback gets stuck in** — `optr=0x18cc`,
    `state=0x0060`, `dwell=0x0031`, `test=0008/0003`. Eighty milliseconds later
    it leaves:

    | s | optr | state |
    |---|---|---|
    | 12.02 | `0x18cc` | `0x0060` |
    | 12.10 | `0x18d8` | `0x0062` |
    | 12.12 | `0x18e7` | `0x0064` |
    | 12.14 | `0x1902` | `0x0068` |
    | 12.22–12.38 | `0x1929` → `0x1974` | `0x0070` → `0x007a` |
    | 14.10–14.36 | `0x198c`, `0x19c8` | `0x007b`, `0x0080` |
    | 18.18 | `0x19fb` | `0x00b0` |

    **`PM 0x18cc` and everything after it is populated on a working call.** The
    cursor walks `0x18d8`, `0x18e7`, `0x1902`, `0x1929` … — all inside the
    `0x18cb-0x1bff` range that is *zeros* in this loopback's answerer. So the
    "cursor runs off the end of loaded PM" finding is real and it is a
    **staging defect in the direct backend**, not a firmware fault, not V90A,
    not timing, and not the tone. run48 ran on the native tower, which loads
    the whole MIPS image rather than the extracted overlay set.
  - **Two independent reasons the loopback cannot be trusted here**, both
    already in this file and both worth re-reading before the next attempt:
    §"this project has never originated a call" — no live capture has the card
    as the caller, so the calling side has no ground truth at all — and
    `dial_tikrnl_drive.py:602`, where `_maybe_request_v8` fakes the V.8 request
    by writing `DM(0x0491)`, `DM(0x3FB0)` and the strobe directly, because
    "the legitimate path is an AT dial script this harness bypasses".
  - **So the work is: make the direct backend stage page 14 the way the tower
    does.** The gap is `PM 0x18cb-0x1bff`; `EICON_OVERLAY_INIT=0x026a` fills
    part of it (201 → 623 words) but not `0x18cc`. The fastest check is to diff
    the tower's PM over that range against the direct backend's at the moment
    page 14 becomes resident — the tower is a working reference for exactly the
    bytes that are missing.
  - **The calling side's blocker, located: V90A state `0x0092` waits forever on
    one status bit.** Its record is `dwell=ffff` — no timeout — and its only
    non-common test slot is index **`0x000f`**, resolving through `DM(0x064B)`
    to handler **`PM 0x3492`**:

        3492  AR = DM($20EF)
        3493  AY0 = $0800
        3494  JUMP $348C
        348c  AR = AR AND AY0
        348d  AR = AR XOR AY0
        348e  RTS

    Zero — and so a transition — only when **bit 11 of `DM(0x20EF)`** is set.
    The record loads that word as `0000`, and write-watching it gated to page
    13 for the whole residency catches **exactly one write**: `PM 0x33e7`, the
    record unpacker, storing `0000`. Nothing else ever touches it.

    So the analogue end is not stuck on a decision of its own. It is parked on
    a receive-derived status bit, with no timer to rescue it, while its own
    `.rx` is 0.0% because the digital end is transmitting nothing. **Both ends'
    blockers are the same defect seen from opposite sides**, and the defect is
    the direct backend's page-14 PM staging, which `run48` shows populated on a
    call that connects.
  - **Not connected.** The blocker is located and its mechanism is fully
    observed; the call still ends with the answerer back on page 7 and the
    caller parked on page 13.
  - **⚠ Anything read out of `DM` below `0x2000` is bank 0 only.**
    `adsp2181_core.c:358` banks that whole range on `DMOVLAY`, and
    `adsp2181_dm()` returns the base bank — so `--trace-v90d-state`, which
    lives at `DM(0x120F)`, `DM(0x1FF6..0x1FFF)` and `DM(0x120A)`, is reading
    one bank of three. Every write observed in the park above reports `ov=0`
    (240 of 240), so these particular readings stand, but nothing in the
    tooling checks that for you.
  - **⚠ "no valid overlay page" was a log defect, not a finding.** Pages 5, 9
    and 17 were missing from `PAGE_NAMES` in `eicon_adsp_sip.py` while their
    overlays loaded perfectly — page 5 served `0x026F` HV34 on every pass and
    still printed as unsupported. Fixed from the bootpage table. It cost one
    detour here; do not spend a second one on it.
  - **So the whole page-7 strand is downstream of a V.8 divergence**, and it
    joins up with what this section already knew: the Analog caller "sends CI
    and never CM". run48's peer is a real modem that sends CM; its answerer
    takes `0x0004 → 0x0003`, publishes the selector, and INFO then walks the
    chain that transmits the probe. Ours never gets a CM, takes `0x000b`,
    publishes nothing, and INFO walks the chain that parks. **Chase V.8 state
    `0x0003` — what admits it, and why `0x000b` is taken instead — not page 7.**
    Everything measured below `0x002a` is a consequence.
  - **So the originate hypothesis is tested and does not explain the stall.**
    What survives from it is the structural point, which is still the most
    important fact in this section: there is no live caller capture anywhere,
    so the calling side has no ground truth, and page 7's transmit is
    role-blind by measurement. The next thing that would actually discriminate
    is a caller-side control — either a real originated call, or deriving the
    calling-side Phase 2 sequence from V.34 §11.2.1.1 and checking our
    transmit against the spec directly rather than against a capture.
  - So the `0x002a` blocker is untouched and stands where `c521cc4` and
    `b4f232d` left it: page 7 never sends INFO0, both ends emit the answer-side
    tone, and the transmit vector `DM(0x166C)` is chosen by a state index that
    starts at zero regardless of role. `Norm_L` only decides whether the
    firmware enters that broken page at all.
  - **The concrete asymmetry to pull next: the two ends enter page 7 by
    different paths.** `PM 0x3EFD` — the init that copies `GEN_SETUP1` and
    calls `PM 0x32DA`, `0x349E`, `0x34A9` — executes **61 times on the caller
    and 0 times on the answerer**, and the answerer never writes `DM(0x167E)`
    or `DM(0x168C)` at all. Both still reach `0x002a` and both still emit
    Tone A, so neither path is producing the calling-side Phase 2 behaviour.
    Establish which entry the firmware intends for each role before treating
    the tone choice as the defect.
- **The escape detector reads live data and still does not fire.** With the
  chain above proven alive, `DM(0x0772)` varies, `DM(0x07BC)` reads 55 against
  threshold `DM(0x0748)` = 2000, and `DM(0x07BD)` stays 0. That is consistent
  with 995a2d9's finding that the chain is a pure frequency discriminator
  peaking at 0.19–0.20 of its input rate; its input is the 8 kHz stream, so it
  peaks near **1560 Hz** and reads ~35–55 at ANSam's 2100 Hz. Whether this
  escape is watching for ANSam at all is still the open question — the state's
  own detector pointers are `DM(0x077B)` = 0x3EDE and `DM(0x077C)` = 0x3A67.
- **The direct backend silently omits a firmware stage.** PM 0x0582, the ISR
  word TIKRNL.ANA claims through kernel service 0x0017, is a DC-removal high
  pass with a `1-2^-5` pole and state at DM `0x31F1/0x31F2`. The direct
  backend plants SR1 by hand and never runs the ISR, so it does not exist
  there. This is why the two backends disagree on a near-DC input (the
  kernel one decays at exactly 0.96875 per sample) and agree at 2100 Hz
  (r = 0.9999). Do not read that decay as a delivery fault; the positive
  controls are in `tests/test_analog_kernel_dispatch.py`.
- **Do not use `download_flag = 0x31AD` for the ANA task.** The request path
  writes AX0/AR/M0 to DM `0x31AB/0x31AC/0x31AD`, exactly as the PRI task
  writes `0x31A9/0x31AA/0x31AB`, so the flag is `0x31AB` (it reads `0x0015`
  with `0x0274` pending out of task init) and `0x31AD` is M0 and always zero.
  `dial_tikrnl_drive.FIRMWARE_SETS` names `0x31AD`; that word is only read by
  a kernel-dispatch service loop, so the direct Analog path never reached it.

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
- **RTDelay does not predict the DIL outcome.** 151 distinct archived page-14
  calls had medians of 70 ms for both outcomes, rank-sum p=0.12 pooled and
  0.22–0.77 within every peer group. The remaining four-call >130 ms tail is
  closed too: a deliberate live lag sweep reached `0x00d0` five times at
  measured 180–190 ms. Session 207's candidate and Session 212's weak cliff
  shape are both withdrawn. 212, 214.
- **⚠ The loopback rig cannot test anything about DIL.** `--answerer-modulation
  v90 --caller-modulation v90a` never loads page 14: it halts at `0x00b0` in V.34
  phase 2, the §2 blocker, which is upstream of the page decision. It is also
  host-bound on this machine in every run, which §4 says makes a run not
  self-consistent. Page 14 needs a real analogue modem. 213.
- **⚠ `artifacts/*/*.adsp-dm.bin` misses most of the archive.** The interop
  captures are one level deeper, under `artifacts/interop/<dir>/`. The real
  corpus is **292 captures, 151 distinct page-14 live calls** once deduplicated
  by the sibling `.rx.ulaw` digest — replays of one call are not separate trials.
  Session 207's "only five load page 14" was this glob bug, and the caveat built
  on it is withdrawn: the archive *is* a sample. Use `rglob`. 212.
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
- **A zero out of the quality-block publisher is a *floored* value, and for the
  echo levels it is unconditional.** `PM 0x0edc` clamps to `[0, 0x3F]` and
  `PM 0x0ede` turns any negative into zero; the conversion runs at 6.0206 dB per
  binary exponent with its zero 116 dB above `MR = 1`, so the accumulator's whole
  reachable range is 38–176 dB under the floor. Every "never measured" reading in
  Sessions 207–208 is superseded, and so is any plan to fix it upstream. 209, 210.
- **⚠ `DM(0x10EF..)` is shared scratch, not an echo accumulator.** The V.34 page
  (`0x0261`) writes its own array over it from `PM 0x2a69` — oscillating signed
  values, `I0 = 0x10EF`, ~750 passes — on every capture that reaches that page.
  Page 14 only clears the pair. Same location-reuse pattern as `DM(0x3F7C)`. 211.
- **⚠ The level routines read the accumulator pair as *signed*.** `0xffff:0xffff`
  is −1, the smallest magnitude, not full scale — Session 209 called it full
  scale and was wrong about that word. The honest maximum is `0x7fff`, and it
  publishes zero too. 210.
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

**The Diva 4BRI-v1 card on `eicon420` — the whole line is closed**

- **The card's SDRAM is faulty. Stop looking for a firmware answer.** A full
  write/read map of the 4 MB BAR2 window finds **2,383 hard stuck bits**, every
  one a single bit in a 32-bit word, every one failing on the immediate
  readback: `0x100000..0x1fffff` has word bits 1 and 14 **stuck at 0** at offset
  `+0x084` of every kilobyte, and `0x300000..0x3fffff` has bits 8 and 11 **stuck
  at 1** at `+0x3e4` plus bit 4 at `+0x11c`. The even megabytes are clean. Not
  an address-line fault: writing every word its own address returns **zero**
  aliased reads, and the 512 corruptions it does show are the exact subset the
  stuck-cell map predicts. `artifacts/diva-4bri-v1-cellmap.txt`.
- **What that ruins.** Shared RAM (`0x1000..0x45000`) is clean, which is why the
  card boots, publishes `0x4447`, answers the driver and writes a coherent
  XLOG — but the protocol image's top 200 KB holds 200 bad words, the DSP
  download 592, and **the heap every instance and pool is allocated from,
  1,575** — about one word in 256. The card is executing corrupted code and
  dereferencing corrupted pointers by design.
- **So every firmware-pairing result on that card is void.** "Only 107-136
  starts this card", "108-130 does not start", "107-234 is worse" were all
  measured on a machine that silently drops bits. Do not rank protocol images,
  do not hunt for a 107-725-or-later `.qm`, and do not read the trap frames'
  odd registers as firmware behaviour.
- **The null-pointer trap probably is this too.** 107-136's instance 3 sits at
  `0x801cc7c0`, the only one of its four with bit 14 set, in the megabyte where
  bit 14 is stuck at 0; `0x801c87c0` is a zeroed hole whose `+12` is null, which
  is exactly the statistics pointer the trap dereferences. The long search for
  "who was supposed to assign `+12`" has no answer because nothing was.
- `docs/4bri_v1_firmware_replay.md` has the derivation, the exception-vector
  patch that stops the card destroying its own image (one word at file offset
  `0x442ec`), and the operational notes — `dd` on `resource2` returns EIO, read
  BAR2 through `mmap`; BAR2 stays mapped and writable after `divas_stop.rc`,
  which is the safest state for memory tests.

---

## 4. Traps that have cost sessions

- **⚠ Nothing measured in the 4BRI card's odd megabytes is evidence.** Two of
  its four megabytes have hard stuck bits (§3). Anything read out of
  `0x100000..0x1fffff` or `0x300000..0x3fffff` — a pointer, an instruction, a
  register in a trap frame that came from the stack, a string in the XLOG — may
  differ from what was written, always in the same bits and always silently. If
  a finding on that card rests on card memory above `0x100000`, it needs the
  cell map applied before it means anything.
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

0. ~~**Re-measure the received upstream**~~ — **done (248), and then explained
   (249).** run48 is 189 s of V.90 data mode against the tower peer at 29,333
   downstream. The independent receiver reads 19.7–19.9 dB against 244c's 19.6,
   the card 21.5 against 20.5, upstream ceiling 14,400 — unchanged, exactly as
   predicted. **The ~20 dB is `d-modem`'s own 6:5 linear interpolation**, in the
   `DSP 9600 -> net 8000` direction its source comment calls "adequate"
   (`d-modem.c:558`) — the mirror of the direction run65 fixed with sinc plus
   Lagrange-8. Rebuilding the same 9600 Hz tap two ways and running both through
   the reference receiver gives **19.7 dB with d-modem's linear interpolation
   and 37.6 dB with a windowed sinc**, against a 37.1 dB codec ceiling; the
   non-equalizable residual after the best 129-tap LTI fit is 19.5 dB, which is
   the measured line. The card's `MP Rate14400` is the correct response to it.

   **So the upstream ceiling on this path is a property of the test peer, and
   the fix is on the tower, not in this repo** — patch `dmodem_get_frame()` the
   way `tools/dmodem_v90_bridge.patch` did the other direction, rebuild
   `/src/d-modem`, and the same call should train far above 14,400 (the peer
   already advertises `upStream max rate : 33600  Rate mask :1fff`). Nothing
   here explains the analogue modems' `0x00b3` stalls, which have no resampler
   of ours in the path.
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
5. ~~**The echo level chain**~~ — **closed (208, 209, 210).** Computed, floored,
   and floored for every input the accumulator can hold. Do not reopen it from
   the DM side; an echo number comes from the audio (`tools/echo_delay.py`). The
   far pair is settled too (211): four writers, none of them an accumulation, and
   the region is shared scratch with the V.34 page. What remains is Session 93's
   far-bulk branch — a question about the *canceller*, not about these locations.
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
10. **Why a live PPP call goes one-way after two minutes.** `artifacts/eicon-ppp/
    live.log`: LCP, CHAP and IPCP all up at 45 s, real web traffic to 130 s, then
    the peer acknowledges nothing further — it polls RR(P) every 660 ms for the
    rest of the call while our I frame N(S)=27 goes unacknowledged through three
    retransmissions. Our receive direction is *fine* throughout (607 good frames,
    0 bad FCS), so this is our transmit path, and the LAPM timers are only
    reporting it.

    **The measurement to start from is the pacing, not the protocol.**
    `tools/rtp_pcap_timing.py live.rtp.pcap` puts the peer's stream at −12 ppm
    and ours at **−441 ppm**, with 1,278 of 16,925 packets sent off the 20 ms
    grid; V.34 allows ±100 ppm. The `[media]` counters agree and date it: the
    receive queue steps to 640 samples (80 ms) at 133 s and never drains again,
    and the run declares itself host-bound at 10 s with 0 clock holds — the
    emulated timeline is being set by how fast the Mac runs, not by the 8 kHz
    clock. A far receiver fed 80 ms of accumulated slip is a sufficient
    explanation for one-way loss and does not need a protocol bug behind it.

    **Answered, and fixed: a quantum costs ~17 ms of the 20 it produces.**
    Measured off the wire rather than inferred — when the loop is behind it runs
    quanta back to back, so the RTP gaps straight after a stall are what one
    quantum costs: median 17.3 ms across 67 recoveries in the 18:20 capture,
    16.5 ms across 102 in the 17:51 one. Three milliseconds of headroom means a
    73 ms stall takes half a second to repay and the next lands first; the worst
    stream's gap histogram lost 533 ms to long gaps, clawed back 449, and left
    the 84 ms that *is* its −1240 ppm. The wire clock now runs on its own thread
    behind a `--tx-buffer-ms` cushion, so the far modem no longer demodulates
    the emulator's stalls. A queue alone was not enough and simulating it said
    so before a call had to: the pump holds the interpreter for the whole stall,
    so a single-threaded sender cannot send during one. A thread can, because
    the pump is `ctypes.CDLL` and releases the GIL for every `adsp2181_run`;
    the measured bound on sender lateness is the 5 ms interpreter switch
    interval, against 70–120 ms stalls.

    **Log volume is not the cause, and was measured rather than assumed.** A
    print to a redirected file costs 2.3 µs unbuffered on this rig (0.4 µs
    buffered), so the call's 877 lines a second came to 0.2% of a core, and even
    the 1,333/s disconnect storm to 0.3%. The three runaway sites are gone
    because a 19 MB log in which 8,831 lines are the ones you want is its own
    problem — not because they moved the clock. What is left to explain −441 ppm
    is the per-tick work itself: Unicorn's MIPS mainloop and the ADSP step,
    against a 20 ms budget, with `[media]` reporting only 7 ticks over 18 ms in
    338 s. That combination — almost no over-budget ticks, no clock holds, and a
    steady 0.04% deficit — is the shape to explain next, and `rtp_pcap_timing.py`
    on a fresh capture is the one-line test of any change to it.

    **The disconnections had a different cause, now fixed: retrains.**
    `rerun.log` (17:51) put eleven calls through the rig. Five reached IPCP, and
    all five died identically — T401 with `TrnProgress` on the handshake ladder
    (0x0040–0x0080) or mid rate change (`upstream rate word 11f2->11f1`). The
    LAPM timers advance per datagram because that is the only clock the endpoint
    has, and a retrain does not stop the datagrams: the pump keeps asking for one
    every 6 samples and puts training signal on the line instead. Three seconds
    of V.42 recovery therefore expired inside a retrain the peer was also in.
    `line_disturbed()` now holds the timers, driven from `DM(0x3FC2)` crossing
    below `EICON_V42_RETRAIN_FLOOR` and from a change in the published datagram
    width. Two of the five then died a second death — FRMR — because the peer
    polled with its pre-reset `N(R)=59` against our freshly zeroed `V(S)`; I and
    S frames are discarded while a SABME is outstanding, per 8.4.1.

    **A retrain is 2–3 s away plus a tail of up to 14 s, against a 3 s budget,
    which is why roughly one in ten used to survive.** Page transitions time it:
    page 14 → page 7 → page 14 takes 2.2–3.2 s on every observed retrain, and
    `T401 × N400` is 3.0 s exactly — so survival was a coin toss on where in the
    budget the retrain started. The tail is worse: on the 18:20 call `TrnProgress`
    left page 7 at 0x0060 and did not reach 0x00c4 for a further fourteen
    seconds. A hold measured in T401s cannot cover that, so the hold is ended by
    the pump stating synchronous state (0xC6, the same threshold that starts
    LAPM), not by a clock; `EICON_V42_RETRAIN_HOLD_S` is only the cap that stops
    a pump which never returns from holding a dead link open.

    Left open by that run: the six calls that never reached LAPM at all
    (`HDLC good/bad/abort=0/0/8`, `XID rx/tx=0/0`) trained to V.90, reached
    `TrnProgress 0x00b3`, and stopped — which is item 3 above, not a V.42
    problem. **A retrain every 60–250 s is itself worth a question**: the rate
    words show the far modem asking to renegotiate downward (`quality=0x0042`,
    `allowed-mask` narrowing from 0x1ffe to 0x17fe), so the line the emulator
    presents is marginal in a way a real modem's would not be — and that lands
    back on the pacing above.
