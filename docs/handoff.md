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
| **V.90 loopback: the `0x0060` park is fixed; the answerer now matches `run48` everywhere the control reaches** | `EICON_EXPAND_SPORT=1` on the direct PRI backend. The answerer walks `0060 → 0062 → 0064 → 0066 → 0068 → 006a → 0070 → 0072 → 0074 → 0076 → 0078 → 007a → 007b → 0080 → 00b0` — run48's own walk — with no `0x5678` and no fallback, and its transmit on the wire (peak 988, 3 codepoints) matches run48's (peak 924, 2 codepoints) for the same phase. `run48`'s capture ends at `0x00b0`, so the answering side is done as far as any control can show. **The remaining blocker is the caller at `0x0092`**, waiting on bit 11 of `DM(0x20EF)`, which takes exactly one write per call — `0000`, from the record unpacker. Now fully characterised: bit 11 is the twelfth entry of an action-vector table (`DM(0x0088)` → `PM 0x30B2`, `RXD0`/`RXD1` = `0xFFFF`), no image in the analog109 set can write the word and no record in the 55-record table sets it, and a state-gated pin that skips the park makes the **answerer** fall back to INFO on 3/3 runs — so the park is real and the bit has to come from the host. **Generalised and acted on (251): bit 11 is not one missing bit, it is the first of a status *vocabulary*.** Every wait state on the V.90A page polls a bit of the same two words, through the handler table at `DM(0x064B)`: `0x0092` → `PM 0x3492`, bit 11 of `DM(0x20EF)`; `0x0095` → `PM 0x348F`, bit 14 of `DM(0x20EB)`; `0x00b3` → `PM 0x34C6`, bit 12 of `DM(0x20EF)`; `0x00c0` → `PM 0x3495`, bit 15 of `DM(0x20EB)` **and** `DM(0x10D9)` non-zero; `0x00c3` → `PM 0x349D`, bit 12 of `DM(0x20EB)`. None of those bits is set by any of the 55 records (the table is the only one — a full scan of `DM 0x1000..0x2000` finds exactly one chain, and the 23-entry next-address table at `DM(0x06B0)` never leaves it), and an absolute-store scan of every `pm.bin` in the analog109 set finds no writer for either word other than V29FC's unrelated reuse. So the whole vocabulary comes from outside the DSP, and supplying it walks the pairing a very long way: with `EICON_ANALOG_PIN_DM=0x20ef=0x0800@0x20f9:0x0092>16,0x20eb=0x4000@0x20f9:0x0095,0x20ef=0x1000@0x20f9:0x00b3,0x20eb=0xc000@0x20f9:0x00c0>25` the caller walks `0092 → 0094 → 00b0 → 00b2 → 00b6 → 00c0 → 00c1`, **the answerer's `0x00b0` hold breaks by itself** — `00b0 → 00b1 (DSR) → 00b2 → 00b3 → 00b6 → 00c0 → 00c2` — and after V.90A gives up at 26.0 s both ends fall back to page 8 and **reach `TrnProgress 0x00d0` with `CTS|DSR|DCD` on both ends, `DATASTATESpeed=0x1110`**. A same-session 60 s control with no pins has the caller at `0x0092` and the answerer at `0x00b0` for the whole call, so all of that is the stand-in's doing. ⚠ It is a stand-in: it makes the DSP see status it did not compute. **What is left is `0x00c1`, and it is a signal question, not a status one.** `0x00c0`'s exit needs `DM(0x10D9)` too, which is real and toggles once per 875 samples; forcing bit 15 lands on `0x00c1`, a 3,200-tick timeout state whose expiry raises `ratechange|flow_blocked` and asks for INFO — pinning `0x00c3`'s bit 12 as well changes nothing, because the abort is at `0x00c1`'s expiry and not at `0x00c3`. **And the exit that looked missing there is a *failure* branch, so do not force it.** `0x00c1`'s `test1` is `PM 0x33F8`, `DM(0x21E6) ≥ 1200`, and its `next[1]` — like `test2`'s `PM 0x3442` — is record `0x1938`, whose only exit is a 20-tick dwell to `0x1956`, a record with both handler slots set to the never-handler `PM 0x340A`: a terminal. Pinning `DM(0x21E6) = 0x04B0` at `0x00c1` aborts at three release times (25.3/25.6/25.9 s) for that reason, which also explains Session 250's ungated result. `0x00c1`'s success exit is the sequential one, `00c2 → 00c3`, and `0x00c3` wants bit 12 of `DM(0x20EB)` **and** `DM(0x10D9)`, which reads 1 for about two samples in every 875. **⚠ And satisfying that pair does not help, which is now a hard result rather than an instrument artefact.** `EICON_ANALOG_PIN_DM` grew a hard form, `ADDR=!VALUE`, that engages the core's own store hook while the gate matches instead of writing the word once a frame — needed because `0x00c2`'s record stores `0x0040` over `DM(0x20EB)` and `0x00c3` reads bit 12 of it a few instructions later, inside the same sample, where a soft pin never holds. Holding **both** `DM(0x20EB) = 0xD000` and `DM(0x10D9) = 1` against every store from `0x00c1` onward leaves the walk byte-identical: `00c1 → 00c3 → INFO`, with the cursor going `0x1938 → 0x1956` — the terminal — inside one sample. So `0x00c3`'s condition is not what routes the machine to the failure record — **`0x00c1`'s persisted `test2` does, and it is now pinned to one word.** `test2` is `PM 0x3442`: bit 9 of `DM(0x3EE6)` clear (it reads `0x0105`, so it is) and then `DM(0x2544) AND DM(0x254B)`, zero meaning branch — to `0x1938`, the terminal. Sampled across the call the pair is `0x0001/0x0001` for the whole page and `DM(0x254B)` drops to `0x0000` at the exact sample the attempt ends. Its writer is `PM 0x2CF1..0x2CF6`, and it is one comparison: `DM(0x254B) = (DM(0x2478) > 26)`, evaluated **once per call**, off the first word of the message the builder at `PM 0x2C8D` assembles at `I7 = 0x2478` from `DM(0x3F8B)`, `DM(0x3F0A/0x3F0B)`, `DM(0x103C)` and `DM(0x1AF8)`. It reads **20** against the threshold 26. **So the V.90A attempt does not fail on a missing status bit at all — it computes a number off the channel, finds it under a floor, and declines, which is the shape of a rate decision and is why the fallback that follows takes V.34 and completes.** Next: name `DM(0x2478)` — `PM 0x2C8D`'s inputs are all database words, so this is answerable without a live call — and then ask whether 20 is the loopback's channel or our own transmit. **Holding `DM(0x254B)` past that decline opens the rest of the ladder, and the whole of it is now walked.** With `0x254b=!0x0001@0x20f9:0x00c1` the page stops asking for INFO and the pairing climbs, one condition at a time, each one the same shape (`bit of DM(0x20EB)` plus `DM(0x10D9)`): `0x00c3` → `PM 0x349D` bit 12, `0x00c6` → `PM 0x349F` bit 10. Released at `>30` so the two ends stay in step, **the answerer reaches `TrnProgress 0x00d0` with `CTS｜DSR｜DCD` at 28.4 s on its own V.90D page** — not the V.34 fallback — and the caller walks `00c6 → 00ca → 00cc → 00cd`. **`0x00cd` is the last condition and it is a real one:** `PM 0x34A6` waits for `(DM(0x2104) & 0xFF) ≥ 0xD0`, the *peer's* state, and `DM(0x2104)` reads `0x0028` for the entire call — never written, so the caller never decodes the answerer's phase-4 control channel even though the answerer is sitting in data mode. Fabricating that word (`0x2104=!0x00d0@0x20f9:0x00cd`) does put **both ends at `0x00d0` on their own V.90 pages for the rest of the call** — which bounds what is left to exactly this one decode — but the caller then raises `CTS` only, against the answerer's `CTS｜DSR｜DCD`, so the fabrication is not equivalent to hearing the peer and **this is not a V.90A connection**. ⚠ **And `DM(0x2104)` is almost certainly not the peer's state — it is this page's own *inner* machine's.** No image in the analog109 set writes it absolutely (six absolute readers in V90.ANA, no writer), and the inner scheduler at `PM 0x33BB` copies its record's next-addresses from `DM(0x2105..0x2108)` and its tests from `DM(0x2109..0x210D)` — the same one-before-next[0] relationship the outer machine has between its state word `DM(0x20F9)` and `DM(0x20FA)`. So `0x00cd` waits for the **inner** machine to reach `0xD0`, and the inner machine — cursor `DM(0x2127)`, the `iptr` the trace already prints — is what to walk next. **⚠⚠ Walked, and it overturns this entire row's premise: the status vocabulary is not host-supplied, it is the inner machine's, written out of the same table.** `PM 0x33D2` is `PM 0x33DD` with the shifts moved — index is `A & 0xFF` instead of `A >> 8`, the value's low byte `B & 0xFF` instead of `B >> 8`, both taking the same `C & 0xFF00` — so **every three-word entry carries one assignment for each machine and the table is two programs**. `tools/record_table_decode.py --inner` reads the second one: 50 records, states `0x0000, 0x0010, 0x0020, 0x0028, 0x002c, …`, and the addresses it lists are exactly the `iptr` values the live trace prints. Its records write the words the outer machine waits on — **`0x1752` (inner state `0x5a`) sets index 6 to `0x0800`, which is bit 11 of `DM(0x20EF)`, `0x0092`'s bit**, and `0x18d2` (inner state `0xD0`) sets it to `0x1312`, carrying `0x00b3`'s bit 12. So "no image in the analog109 set writes the word" was true and irrelevant: the writer is a *record*, and the outer decode could not see it. Every pin in this row is therefore a stand-in for the inner machine not running, not for a missing host. **And why it does not run is a detector question.** The inner machine sits on record `0x16b6`, state `0x0028`, for the whole call — `DM(0x2104)` reads `0x0028` from the first sample to the last — and that record's only condition is index 36 = handler `0x0A` = `PM 0x3470`: `DM(0x10F3) XOR 1`, advance when the flag is set, and clear it. `DM(0x10F3)` is the tone/event flag `--trace-v90a-state` already prints as `event`. **And the last hop closes the chain, onto a mechanism this project has already met.** Tracked live on an unpinned control, the inner machine walks `0x0000 → 0x0010 → 0x0020` and stops there, on record `0x16a4`, at the same moment the outer machine enters its `0x0092` park. `0x16a4`'s primary is a dwell countdown (`PM 0x33EE`, on `DM(0x2103)`, 40 ticks) and its `test0` is the event flag `PM 0x3470` with **`next0` pointing at `0x16a4` — itself**, so the inner machine is in a **detector-driven self-loop that reloads its own dwell** — the same shape as the V.34 answerer's correlator-latch self-loop on state `0x0090` in the row below. The chain is: detector latches → inner state `0x0020` re-enters itself → inner records `0x1752`/`0x18d2` never run → `DM(0x20EF)` bit 11 never set → outer parks at `0x0092`. **⚠ The instrument for all of this is `EICON_WATCH_OVERLAY=0x026b` on the caller, and without it a watch on this backend spends its whole budget on V.8 and INFO before the page ever loads** — the arming-hold exists for exactly that and `eicon_adsp_sip.py` says so at the arming site. ⚠ **And do not convert cycles to samples here**: 33 MHz / 8 kHz = 4,125 assumes a continuously running DSP on an 8 kHz codec, and this backend clocks a 9,600 Hz codec in frames, so the two counters are not proportional. Session 251 withdrew a correct result on that bad conversion and then had to withdraw the withdrawal. **Re-measured under the gate, the detector findings stand and one dies.** `PM 0x0D01` runs on the V.90A page (4,000 hits, budget spent) and `DM(0x10F3)` takes 6,950 stores there — 5,439 sets from `PM 0x0D04` against 1,510 clears from `PM 0x3473`. `ABS MR1` is **2,329..5,736, median 4,537, latching on 100% of evaluations** against a threshold of 700. The taps are strictly positive — 3,000 of them, **no sign change**, absolute mean 4,553 — against a constant frequency word of `0x11C7` = 4,551, which is what an arctangent returns when the vector it rotates is constant. And the loss is bracketed to two biquad sections at `PM 0x0CB2`: `MX0` goes in at **RMS 904** and reaches the mixer at `PM 0x0CE8` as **2..6**, about 200x. **What dies is the explanation:** `I4` at both call sites reads **`0x2448` on all 800 gated hits**, the correct coefficient base, so the "coefficient pointer left in code" story is disproved — and with it the `PM 0x3538` op/dump contradiction, which was the ungated watch reading INFO. **And one hop further, gated, the fault is not in the biquads at all — it is their input.** `SE` reads `0x0000` on 600 gated passes, so no shift is throwing the signal away, and `MR1` is *already* 2..6 coming out of the MAC. The reason is visible in the input itself: `MX0` at `PM 0x3538` runs **min 0, max 1,535, RMS 904 — never negative**. That is a DC-offset, single-sided stream, and two recursive sections that cancel it to a few counts of ripple are **working correctly**: a bandpass rejecting DC is what a bandpass is for. The chain reads, end to end: the caller's receive path hands the demodulator an unsigned/DC-offset stream → the biquads strip the DC and leave ~4 counts → the CORDIC on a near-zero vector returns the NCO's own increment (`0x11C7` = 4,551; tap mean 4,553) → the six-tap boxcar sits at 6.5× a 700 threshold → the event flag latches on every pass → the inner machine self-loops on `0x0020` → the status records never run → the outer machine parks at `0x0092`. **Every link in that is measured under `EICON_WATCH_OVERLAY=0x026b`.** Note the wire itself is fine and signed — the received `.rx.ulaw` is RMS 403 broadband — so the single-sided stream is made somewhere between the codec boundary and `PM 0x3538`. **Walked, and it adds a second gated data point rather than an answer.** `PM 0x23FA..0x2425` is a 19-tap FIR (`CNTR = 0x13`, MAC at `PM 0x2419`, result saturated and stored to `DM(I1,M1)`), and gated to `0x026B` its input samples read **min 0, max 1** — essentially nothing — while its output reads 0..1 too. That is *not* the same stream as the biquads' RMS-904 input, so the two feed different arms and the FIR's attribution is open: do not assume it is the demodulator's front end without establishing it. The codec boundary itself is clean — `adsp2181_sport1_frame()` loads `sport_rx[1]` with the 16-bit word unchanged, so two's complement survives, and `line_codec_rx_word()` returns the signed linear sample for `analog109`. **Located, and the RMS-904 red herring with it.** `MX0` at `PM 0x3538` is FIR *leftover*, not the demodulator's input: the input is read from **program memory**, `SR1 = PM(I7,M5)` at `PM 0x3535` and `0x353A`, with `I7` = **`0x2686`/`0x2687`**. Gated, those two samples read **I: 2..6, never negative, mean 4.1** and **Q: −3..2, mean 0, 24% negative** — a properly signed quadrature pair, except that it is about **100× too small** against a wire at RMS 403, and the I arm's DC offset is the size of the whole signal. A PM write watch names one producer, 53,717 stores: `PM 0x27B8..0x27C9`, an analytic-signal generator — `PM(I7,M5) = SR0` takes a raw tap straight off a circular DM delay line (`I1`, `L1 = 0x24`, stride `M3 = 2`) and `PM(I7,M4) = MR1` takes the 17-tap Hilbert FIR (`CNTR = 0x11`, coefficients from `PM 0x2064`). **So the demodulator, its CORDIC, its boxcar and its biquads are all innocent, and the fault is that the DM delay line feeding `PM 0x27B8` already holds ~4-count, DC-offset samples.** **Taken, and it is the root: the V.90A page's received sample is the constant 4.** The delay line is `DM(0x0E80)`, `L1 = 0x24`, filled at `PM 0x27BA` with `AR` from `PM 0x27B1..0x27B2`: `AR = DM(0x2131) − MR1`, where `MR1` is a 179-tap FIR (`CNTR = 0xB3`, coefficients from `PM 0x1E00`). Gated over 800 passes, **`DM(0x2131)` reads min 4, max 4, mean 4 — a constant — the FIR output is 0, and the residual entering the delay line is 4.** Nothing is being cancelled and nothing is being attenuated: **there is no signal at the top of the chain**, and every downstream finding above follows from that one fact rather than being a defect in its own right. `DM(0x2131)` is written at `PM 0x29A7` from `AX1`, which comes out of `PM 0x2A17` two instructions earlier. **So the whole of this row reduces to: the V.90A page is fed a constant instead of the line, and the next question — why `PM 0x2A17` returns 4 — is a sample-delivery question about this backend, not a modem one.** The wire is fine (`caller.rx.ulaw` RMS 403, broadband) and the codec boundary is fine (`sport_rx[1]` unchanged, `line_codec_rx_word()` signed), so the break is between the SPORT and `PM 0x2A17` — **and it is now located exactly.** `PM 0x2A17` reads the page's receive sample out of *program* memory: `I7 = DM(0x21A6)`, `AR = DM(I7,M5)`, and gated the pointer cycles **`PM 0x3F30 → 0x3F31 → 0x3F32`**, a three-word ring. The sample then runs through `PM 0x26D9`/`0x270C`/`0x2A3F` and a gain multiply by `DM(0x3FC8)` — which is **healthy at `0x12D0` = 4,816** — but the value entering that multiply is the **constant 1**, and 1 × 4,816 shifted is the 4 that `DM(0x2131)` carries. **A PM write watch over `0x3F30:0x3F32` catches six stores in the whole call, all from the loader's block clear at `PM 0x064D` at cycle 13,080. Nothing ever puts a line sample there.** So the V.90A page reads a receive ring that no one fills, which is the single fault behind this entire row. The next question is whose job that is: the analog kernel's SPORT1 path fills `RXSAMPLE` in *data* memory, so either `DM(0x21A6)` should point into that ring and is left pointing at `PM 0x3F30`, or the page expects an initialisation step this harness never runs. **⚠ Correction, and then the plumbing measured properly. `DM(I7,M5)` is a *data* memory read, not PM** — the ring is `DM(0x3F30..0x3F32)`, which is `DM_RXSAMPLE` in `analog_kernel_dispatch.py`, so "a PM ring nothing fills" was the wrong memory space and the PM write watch that found nothing was watching the wrong thing. **What is actually true, all gated:** `DM(0x3F30..0x3F35)` — the whole RXSAMPLE ring — reads **0 for the entire call**, 8,639 samples, one distinct value; `DM(0x21A6)` cycles `0x3F30 → 0x3F33`, reset each frame by `PM 0x2946`; the gain word `DM(0x3FC8)` is a healthy `0x12D0`; and **`ShellInptr`, `DM(0x3F0F)`, holds `0x3763`** — so the kernel is depositing the line sample at `DM(0x3763)` while the page reads `DM(0x3F30)`, and **no firmware writes `DM(0x3F0F)` at all while V.90A is resident**. The one nearby word carrying anything is `DM(0x3F36)`, ±4,580 with two distinct values. ⚠ Pointing `ShellInptr` at the ring as a probe (`0x3f0f=!0x3f30@>9.5`) changes nothing — the caller still parks at `0x0092` — so the fix is not one word, and the harness reads that pointer rather than writing through it. **Established, and the break is one measurement deep: the kernel's receive ring is always empty.** This file's own `analog_kernel_dispatch.py` header already answers who fills RXSAMPLE — the kernel does, and that is why the kernel-dispatch backend exists. Gated, the front of that path is **alive**: the SPORT1 ISR runs (`PM 0x0077`, `0x02B6`, `0x0713`, `0x0715` all hit), and `DM(0x2E22)`, the word the ISR stores `RX1` into, carries a real signal — **RMS 3,839, 3,282 distinct values, ±10k**. But the 32-word ring at `DM(0x2DE0)` never holds anything: its write pointer `DM(0x2E00)` and read pointer `DM(0x2E01)` are **equal on 8,479 of 8,479 sampled frames, difference 0 mod 32, 100%**. So the foreground pops a slot the producer has not filled, `SR1` reaches TIKRNL's continuation at `PM 0x0715` as **`0x0000`**, `RXSAMPLE` stays zero, and the demodulator gets its constant. **That is the whole V.90A blocker, and it is a producer/consumer ordering question in this harness — the sample arrives, the ISR stores it, and the ring it is queued through is drained to empty every frame.** Whether the ISR is raised at the wrong point in the frame or the foreground runs too many times per sample is the next thing to establish, and it is upstream of every modem question in this row | 249, 250, **251** |
| **the answering page stops publishing transmit data at `0x00b0`** | the live V.34 blocker. Both ends reach `0x00b0` through twenty states, then the answerer's transmit chain halts completely — no further `DM(0x224C)` requests, line frozen on one sample. Sessions 137–148 describe a regime that no longer exists; do not carry their wait-block, threshold or role-word findings forward. **Re-diagnosed (250), and the long-standing reading is withdrawn: this is a retrain loop, not a halt.** `DM(0x2147)`, the state word, takes `0x0090` **53+ times** after `0x00b0` is reached, while the logged `TrnProgress` — sampled once per media tick against a word that changes about once per 8 kHz sample — shows nothing past `0x00b0`. State `0x00b0`'s own `next[0]` resolves to record `0x1BA5`, which *is* state `0x0090`, so the machine is taking the table's retrain branch over and over. The symptom list follows from that: no further `DM(0x224C)` requests from a *new* record, a line frozen because the same states repeat. **And that question is answered too, from the watch's own registers.** `PM 0x2DC2..0x2DD6` is the branch selector: `DM(0x21EE..0x21F1)` hold the four resolved next-record addresses, `DM(0x21F2..0x21F5)` their four test handlers, `DM(0x21F6)` the primary condition; each test is `CALL (I4)` then `IF LE JUMP $2DD6`, and `0x2DD6` applies the chosen record. The `i4` left in the watch lines at `0x2DD6` names the handler that just fired, and across passes it is **`0x2EF1`** and **`0x2EF3`** — test indices `0x1E` and `0x0A` in the table at `DM(0x064B)`: `2ef1: AR = DM($3F89)`, and `2ef3: AR = DM($13BF); AR = AR XOR $0001; DM($13BF) = M0; DM($137C) = M0` — which **consumes the latch**. `DM(0x13BF)` is the **correlator latch** this project already watched by the hundred thousand in the page-8 work, and state `0x0090`'s own `next[0]` (index `0x13`) resolves to record `0x1BA5` — *itself*. **So the answerer is in a correlator-latch-driven self-loop on state `0x0090`:** every time `DM(0x13BF)` latches, `0x0090` re-enters itself and consumes it. That is the whole of this row's "stall", and why the dwell never expires and no new record is ever applied. **✅ Producer found, and it closes the chain.** `PM 0x0E31..0x0E3B` is a **6-tap correlation** (`CNTR = 6`, `MY0 = $1554`), `AR = ABS MR1`, compared against **`DM(0x2145)`** — and `0x2145` is `0x2137 + 14`, i.e. **block index 14, a record field**. `IF GT AR = 0 + 1` latches `DM(0x13BF)`. The records set it per state: `0x1BA5` (state `0x0090`) carries `(14, 02bc)` = **threshold 700**, `0x1BB7` (state `0x0092`) carries `(14, 0578)` = 1400, and `0x1C95` (`0x00b0`) does not set it at all. Put together with state `0x0090`'s own fields — `dwell 0x0032` = 50 ticks, primary = the dwell countdown, `test[0]` = the latch, `next[0]` = itself — **the loop is the design working, on a signal that will not stop**: `0x0090` counts down 50 ticks, and every time the correlation magnitude exceeds 700 it re-enters itself and reloads the dwell. As long as the correlator fires more often than once per 50 ticks, the state can never age out. **So this is now a signal question, not a state-machine one: why does the answerer's 6-tap correlator keep exceeding 700 in state `0x0090`** — and note the caller driving it is a stand-in-pinned originate side, so its transmit is the first suspect, not the answerer's detector. ⚠ One dead end recorded so it is not re-walked: `PM 0x2EF9/0x2EFC` tests bit 1 of config word 0 `DM(0x2137)`, and `0x00b0` does set word 0 to `0x9600` with that bit clear — which looks like an immediate-branch explanation and **is not the one that fires**. The `i4` evidence says `0x2EF1`/`0x2EF3`; neighbouring handlers are not evidence. Everything below this line is the older reading and is kept only for its measurements.**Superseded: one fault rather than a transmit one: the scheduler stops.** `0x00b0` is a *timed* state — its only condition is the dwell countdown `PM 0x2E32` on `DM(0x2146)`, dwell `0x0080` = 128 ticks = **16 ms** — so it waits on nothing external and should self-advance. In all three archived runs the answerer re-applies records repeatedly at `0x00ac`, applies `0x00b0`'s once, and never applies another for ~50 s. `DM(0x224C)` is written by the record-apply tail at `PM 0x2E30`, so the missing transmit requests are that same stop seen from downstream. `0x00b0`'s record swaps config word 0 `DM(0x2137)` `0xA700 → 0x9600`, whose handler `PM 0x249B` re-selects the per-sample routine set from four 2-bit fields into `DM(0x217D..0x2180)`. **Next: dump that block either side of the transition** | 149, 164, **250** |
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
| **the V.34 loopback rig is restored: the caller reaches page 8 and the answerer `0x00b0`** | **`EICON_PIN_DM=0x3811=0x0000` on the caller, with `EICON_V8_TIMER_SENTINELS=0` on both ends.** The caller starts at **0.020 s** instead of 13.6 s and walks `12 AT online → 6 V.8 → 7 INFO → 8 V.34`; the answerer reaches `0x00ac → 0x00b0`, which is §2's V.34 blocker, live and instrumented for the first time this session. One store undone, so the pin is a stand-in and not a fix — but it is the *same* stand-in class as `EICON_ORIGINATE_LINE_READY`, and for the same reason: `DM(0x3811)` is the V.22FC page's frame gate and on the originate arm it only counts down while bit 5 of the detector word `DM(0x3883)` is set, which a PRI with no analogue line never sets | **250** |
| **the loopback connects end to end again — `EICON_V8_TIMER_SENTINELS=0` on *both* ends, and the caller's dial start is what still picks the modulation** | **the `--native-mips` loopback reaches `TrnProgress 0x00d0` on both ends, 2/2, against a same-session control on the default that reaches nothing** (caller `0x0000`, answerer `0x0026`). The sentinel commit (`6a79993`) said in terms that the seed "was introduced for the originate/V.32 partial-overlay path and nothing here tested that" — this is that test, and on the originate path it is fatal. **It lands on bootpage 1, V.22**, not V.34: with sentinels off on both ends the caller starts its dial script at 13.6 s instead of 30.0 s, still late enough that V.8 has moved on. So the rig completes a call again, and the remaining V.34 gap is the caller's dial start alone | **250** |
| ~~the V.34 loopback's caller starts its dial script 31 s late~~ | **improved twice and no longer blocking a connection, but still the reason the call is V.22.** 8,192 of the samples were the answer-side page-settling loop running on the caller, where it cannot reach V.8 by construction — the originate path only gets V.8 from `ORIGINATE_V8`, which needs media frames that have not started; it is now role-conditional and the dial-park exit moves 250,259 → 242,067. The rest was the peer's sentinel silence: with both ends' sentinels off the start falls to 13.6 s. Below is the original characterisation, which still describes what is left | **250** |
| **(original, for the remaining 13.6 s)** | it gates V.34 selection rather than being part of the V.34 fault. `DM(0x03EF)`, the dial script cursor, takes **its first write of the entire call at cycle 244,061,165 — sample ~250,000, 31.3 s**. It is not stuck at a value: the script has not run at all before then. From there the originate sequence is identical to the archive — first gate `0x35d7`, second gate `0x35ed`, park exit to `0x0051`, `NORM_L` `0x3004 → 0xa13f`, the `EICON_ORIGINATE_V8` write — just 31 s late, by which time the answerer has cycled INFO and fallen back to V.22. The archived runs did all of it at sample 1,678 (0.21 s). **Not** instrumentation (reproduces with every watch removed) and **not** the tone detector (`EICON_PIN_DM=0x0554=0x0020` from the start stops the script running at all). `--ring-seconds 32` to realign the ends does not help: the answerer then reaches V.22 instead. The combifile has moved too — 64 downloads/848,580 bytes archived against 65/905,920 now, same card type and file set | **250** |
| **the Analog MIPS tower originates, and its V.8 task never returns to the kernel** | new, and it is the only remaining route to a V.90 caller with a real host. `--caller-native-mips` on `analog109` boots build-109, and after the dial-ordering fix it queues `CALL_REQ`, assigns the DSP and serves its own TIKRNL. Then `V8.ANA` runs its service loop — `PM 0x1FF7..0x2016`, 4.3 M executions, dispatching through `DM(0x38D0)` on `GEN_setup1` bit 3, which is correctly set — and never returns, so every frame truncates whatever the budget. An execution-model question: see `harness-execution-plan.md` | **250** |
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
  - **⚠ WITHDRAWN — the entry below reads the wrong memory, and so does
    everything built on it.** `DM(0x120F)` is a cursor into a record stream in
    **data** memory, not program memory: the unpacker's fetch is a DAG2 *data*
    read, proved on the calling side by `--watch-dm` catching
    `dm r 1746=101a pc=33e0`. Dumped live with `EICON_DUMP_DM` at page-14
    residency (7.55 s), the answerer's `DM 0x1800-0x1bff` is **774 of 1,024
    words non-zero**, and every cursor value in the walk holds a well-formed
    record. The stream is triples, with `0x070e` opening a state:

        180f  070e d050 | 1848  070e d052 | 1854  070e d053 | 1869  070e d054
        188a  070e d056 | 18ba  070e d060 | 18cc  070e d062 | 18d8  070e d064
        18e7  070e d066 | 1902  070e d06a

    **`DM 0x18cc` is not zeros — it is state `0x62`**, and the two records the
    `0x0060` loop runs between read in full:

        state 0x60 @18ba  070c=00bc(2)  070d=0032  070f=0002  0713=0008  0717=0001
        state 0x62 @18cc  070c=0078(5)  0713=0000  0717=0008

    So the loop is not degenerate, the machine is not unpacking zeros, and the
    action vector is not empty because a record was missing. This also explains
    the two negatives that closed the PM strand from the other end — `bb3dd63`
    (the tower has the same PM zeros and connects) and `1631192` — without
    needing either: `PM 0x18cb-0x18ff` being empty was never relevant, because
    nothing reads records from PM. Sessions spent on page-14 PM staging,
    `EICON_OVERLAY_INIT`, `EICON_RELAY_BASE` and the seven-word PM diff were
    all searching the wrong address space. **Do not re-open any of it.**

    What survives, and is now readable as firmware data rather than inferred:
    the `0x0060` ↔ `0x0062` round trip inside one frame (`PM 0x2f9a`), the
    dwell timeout being its only exit, and the transmitter publishing nothing
    while it runs. The question is why the exit condition in state `0x60`'s
    record is never satisfied — and the record above says exactly which fields
    to read.
  - **The record format, decoded from the unpacker.** `PM 0x2fe3` reads triples
    through `I4`: word 0 `AND 0xFF` is an offset, word 1 `AND 0xFF` a value,
    word 2 an extra, and the destination is `MR0 + offset` with `MR0 = 0x1FE9`
    for the outer machine (`PM 0x2fb7`) and `0x2001` for the inner
    (`PM 0x2fce`). So `070e d060` is `DM(0x1FE9+0x0E) = DM(0x1FF7) = 0x60`,
    which `PM 0x2fba` reads back as the state — the trace's own `state` column.
    `PM 0x2fc0..0x2fc9` then translates two runs of slots through tables:
    `DM(0x1FF8..0x1FFB)` are indices into the destination table at
    `DM(0x0613)`, `DM(0x1FFC..0x2000)` indices into the condition table at
    `DM(0x05E0)`. Both dumped live at page-14 residency:

        DM(0x0613)  180f 188a 18ba 1965 19f5 1a28 1a8e 1aee
                    1b51 1bcf 1c74 1c80 1c9e 1ce0 1cef 1cb9
        DM(0x05E0)  3038 2ffb 2ffd 2fff 3015 303d 3060 3064
                    30a7 3036 309c 303a 300b 300d 300f 308e

    Sixteen entries each, so nothing indexes off the end. Condition index 8 is
    `PM 0x30a7`, the tone-detect test this file already names.
  - **⚑ And against `run48` the divergence is now one transition wide.** The
    connecting call and this loopback reach state `0x0060` in the **same
    record with the same slots**: `optr=0x18cc, state=0x0060, dwell=0x0031,
    next=0002/000c, test=0008/0003`, identical in both. What happens next:

    | | run48 (connects) | loopback |
    |---|---|---|
    | | `0x18cc` `0x0060` dwell `0x31` | `0x18cc` `0x0060` dwell `0x31` |
    | | `0x18cc` `0x0060` dwell `0x2f` | |
    | | **`0x18d8` `0x0062`** test0 → `0000` | **`0x1cb9` `0x0060`** test → `0000/0000` |
    | | `0x18e7` `0x0064` → `0x1902` `0x0068` → `0x1929` `0x0070` … `0x19fb` `0x00b0` | `0x1d25` `0x0060` → `0x1d2b` `0x0060` dwell `ffff`, park |

    `0x18d8` is not in the destination table, so run48's advance is
    fall-through — the record simply ends and the next one begins. `0x1cb9` is
    destination-table **index 15**, and the record's own slots are 2 and
    `0x0c` (`0x18ba` and `0x1c9e`), so the loopback is not taking either of
    the destinations its record offers. It is being sent somewhere else, into
    the `0x1cxx/0x1dxx` stream the seeders at `PM 0x2f43/0x2f4b` own
    (`outer 0x1D0A`), and it parks there at `dwell=ffff`.

  - **✅ Named, and it is neither: the answerer takes its own declared failure
    path.** Exec-watching the seeders gated to `0x026a` gives exactly two
    seeds in the whole page-14 residency:

    | | | |
    |---|---|---|
    | `pc=2f50 from=2457` | outer `0x180F`, inner `0x1BEA` | the correct initial seed |
    | `pc=2f4b from=2f4a` | outer **`0x1D0A`**, inner `0x1D6D` | the re-seed |

    and the second is entered by fall-through from `PM 0x2f49`, which
    exec-watches as `from=2457` — the same indirect dispatch. What
    `PM 0x2f49..0x2f4a` does before falling in is the tell:

        2f49  AR = $5678
        2f4a  DM($3F8A) = AR      ; the failed-recovery marker
        2f4b  MR0 = $1D0A ; MR1 = $1D6D ; DM($2111) = 7 ; JUMP $2F53

    `0x5678` is the failure marker this file already names in the V.34 entry.
    Confirmed independently in the capture's own DM stream, which is not the
    watch: `DM(0x3F8A)` is `0x0000` for the whole call, becomes **`0x5678` at
    12.460 s** — the instant the cursor goes to `0x1cb9` — and clears at
    12.520 s when the page falls back to 7.

    So there is no mis-indexed branch and nothing is corrupt. State `0x0060`
    waits on condition index 8, `PM 0x30a7`, the tone detect; `run48` satisfies
    it 80 ms after entering the state; ours never does, the `dwell=0x0031`
    counts out, and the firmware declares the failure and re-seeds itself into
    the recovery stream, where it parks with `dwell=ffff` and transmits
    nothing. **The `0x0060` "park" is the aftermath, not the fault.**

    **That moves the last blocker off the state machine and onto the line.**
    Both ends are now understood and neither is defective: the V90A caller
    walks its stream correctly and waits, one status bit from state `0x0094`,
    for something the answerer never sends; the V90D answerer waits for a tone
    its detector never sees and then fails correctly. The question for data
    mode is what `PM 0x30a7` is measuring at state `0x0060`, and why the V90A
    caller's transmit does not satisfy it where a real analogue modem's does —
    a signal question, with `run48` as the working control and both ends
    instrumented.
  - **The transition machinery, decoded, and one measurement that does not fit
    yet.** `PM 0x2f86..0x2f99` is the transition dispatcher: it calls the
    translated condition routines `DM(0x20A8..0x20AC)` in order and, on the
    first that returns `LE`, loads the matching translated destination
    `DM(0x20A4..0x20A7)` and `PM 0x2f9a` writes it to `DM(0x120F)`. No
    condition firing means `RTS` and no branch — which is what a fall-through
    advance like `run48`'s `0x18cc → 0x18d8` looks like. Condition index 8 is

        30a7  AR = DM($120A)      ; the event flag
        30a8  AY0 = $0001
        30a9  AR = AR XOR AY0     ; LE when the flag is 1
        30aa  DM($120A) = M0      ; and it clears the flag

    so slot 0 firing would both read *and clear* `DM(0x120A)` every poll.
    **It never writes it.** Write-watching `DM(0x120A)` on the answerer gated
    to `0x026a` — armed, and the arming line is in the log at 7.553 s as its
    control — gives **zero writes** across the whole page-14 residency, while
    `PM 0x2f9a` runs 8 times in the same configuration. So the branch is being
    taken by some other slot, and the tone-detect condition is not merely
    failing, it is not being evaluated at all.

    That contradicts the earlier session's "the detector sets the flag, the
    poll reads 1, twenty times". **Resolved in the older measurement's favour,
    and the zero-write run is the outlier.** Exec-watching the three addresses
    together, gated to `0x026a`:

    | address | called from | executions |
    |---|---|---|
    | `PM 0x30a7` | `PM 0x2f87` — condition slot 0 | 6 |
    | `PM 0x2fff` | `PM 0x2f8b` — condition slot 1 | 6 |
    | `PM 0x2f9a` | **`PM 0x2f89`** — the branch after slot 0 | 12 |

    Every branch is taken on **slot 0**, the tone detect, so the event flag is
    being set repeatedly and the machine is sent back each time until the dwell
    runs out. Treat these runs as host-bound and variable — the media loop
    warns about it, and that is the likeliest reading of the zero-write run;
    re-run anything measured once.

  - **✅ Last hop taken, and the chain now runs from the record's own threshold
    to the line.** Write-watching `DM(0x120A)` with the branch counter as its
    control in the same run: 24 writes, `PM 0x2f9a` 12 executions. The clears
    are `PM 0x30aa`, the condition routine itself; **every set is `PM 0x0e30`**,
    with `I0` walking `0x0e3a, 0x0e3b, 0x0e3c, 0x0e3d…`. It is V90D's own code
    (`0x026a` owns `PM 0x0b40-0x0fd9`), and it is a six-tap correlator against
    a threshold:

        0e25  DM($212F) = I0
        0e26  MX0 = DM(I0,M1)
        0e27  MY0 = $1554
        0e28  CNTR = 6
        0e29  DO $0E2A UNTIL NOT CE
        0e2a  MR = MR + MX0*MY0 (SS), MX0 = DM(I0,M1)
        0e2b  AR = ABS MR1
        0e2c  AY0 = DM($1FF5)        ; the record's own field, offset 0x0c
        0e2d  AF = AR - AY0, AX1 = AR
        0e2e  AR = DM($120A)
        0e2f  IF GT AR = 0 + 1       ; over threshold -> post the event
        0e30  DM($120A) = AR

    `DM(0x1FF5)` is offset `0x0c`, and state `0x60`'s record sets it to
    **`0x00BC`** (state `0x62`'s to `0x0078`) — the `070c=00bc` field printed
    above. So the whole loop is one comparison: the correlator magnitude
    `|MR1|` exceeds `0xBC`, the event posts, condition slot 0 branches the
    machine back into `0x60`, and it does that until the dwell expires and
    `PM 0x2f49` writes the `0x5678` failure marker.

    **`run48` does not branch here at all**, so on a connecting call this
    correlator stays under `0xBC` through state `0x60`. Ours exceeds it
    repeatedly. That is the whole remaining difference between a V.90 call that
    connects and this one, and it is now a *signal* measurement with an exact
    probe: `AX1` at `PM 0x0e2d` is the magnitude, `DM(0x1FF5)` the bar. Read
    the magnitude distribution across state `0x60` and compare it with what the
    V90A caller is transmitting in the same window — the caller's transmit is
    99.8% continuous there, and a correlator that keeps tripping is the
    expected consequence if it is sending the wrong thing, or the right thing
    too loud.
  - **Measured, and it is not marginal: the detector is 20–100× over its bar.**
    Exec-watching `PM 0x0e2e` (the instruction after the compare, so `ax1`
    carries `|MR1|`) gated to `0x026a` gives, in order:

        0e20 1c40 2a5f 387f 469f 54be … 54be 3eac 2b39 27c2 1705 08e7 …

    against `DM(0x1FF5) = 0x00BC`. `MR` is cleared at entry
    (`0e24  MR = MX0 * 0`), so each value is a fresh six-tap sum: the shape is
    an envelope rising to `0x54be` (21,694), holding, then decaying — a real
    signal, not a stuck accumulator, and never within two orders of magnitude
    of the threshold.

    **And it is not a DC level.** Decoded from the same run's captures, the
    caller's transmit and the answerer's receive are bit-identical to each
    other and are proper AC: mean `−0.1` to `−2.1`, `±2,620` peak, 132–139
    distinct codepoints, right through the answerer's whole `0x0060` window.
    So this is not §2's `0x00b3` DC-sample defect reappearing.

    Which leaves the shape of the answer: at this state the answerer's detector
    is seeing a loud, continuous, well-formed signal, and on `run48` — the same
    firmware, the same state, the same threshold — it sees nothing above 188 at
    all. **The next step is the Recommendation, not the emulator** (§0.1): find
    what V.90 phase outer state `0x0060` is, and what a calling modem is
    supposed to be doing during it. Our V90A caller transmits continuously
    there. If the answer is that it should be silent, or sending something
    else, the defect is the caller's transmit schedule and both parks follow
    from it.
  - **And the Recommendation says silence is mandated, which makes both parks
    one deadlock.** V.90 §9.3.2.4: after transmitting TRN the analogue modem
    "shall send sequence Ja and condition its receiver to detect signal Sd and
    the Sd - to - Sd transition. After detecting the Sd - to - Sd transition,
    the modem shall **terminate Ja and transmit silence**" — and §9.3.2.7 has
    it wait, still silent, for Jd. The digital modem's Phase 3 is the mirror:
    §9.2.1.1.8 and §9.2.1.1.x have it "transmit silence and condition its
    receiver" at each handover. So there is a mandated quiet window on the
    analogue side in the middle of Phase 3, and a digital-side detector that
    expects to see nothing during it is behaving exactly as specified.

    Our V90A caller transmits 99.8% continuously through that window — the
    figure is already in this file, from the send/receive table — and the
    answerer's correlator reads 3,616–21,694 against a bar of 188 the whole
    time. **Hypothesis, for the next session to test rather than assume: the
    caller never terminates Ja / never goes silent, so the answerer never sees
    the quiet its state `0x0060` is waiting for, times out, and declares
    failure; and the caller's own park at state `0x0092`, waiting on a
    receive-derived status bit with no timeout, is waiting for what the
    answerer would have sent next.** One deadlock, two parks, and the caller is
    the end that moves first under the Recommendation.

    The test is on the caller: find which V90A state should terminate its
    transmission — its record stream is now fully readable in
    `DM 0x1689-0x17d9`, states `0x50`-`0x94` — and watch what it publishes to
    the transmit slot across states `0x0073`/`0x0075`/`0x0092`. Do not start
    from the answerer again; everything on that side is understood down to the
    comparison.
  - **✗ Tested the obvious version of it, and it is a negative — with a
    positive control, and it hands back something better.**
    `EICON_ANALOG_TX_MUTE_OVERLAY=<id>` (new) holds the Analog caller's
    transmit at silence for exactly as long as one overlay is resident; the
    gate is the page rather than the clock because these runs are host-bound
    and a wall-clock window lands in a different phase every time — the first
    attempt, `EICON_ANALOG_TX_MUTE_FROM_S=7.7`, landed inside INFO and the
    answerer never reached page 14 at all (that variable exists too, and this
    is what it is for). With `0x026b`, the caller's line is measurably silent —
    peak 6,140 at 9.0–9.3 s, then **0, one distinct codepoint**, from 9.5 s to
    the end — and the answerer's walk is unchanged to the record:
    `18cc:0060 → 1cb9 → 1d25 → 1d2b`, park.

    **Because the two ends are 1.8 s out of phase.** The answerer enters page
    14 at **7.553 s** and starts its `0x0060` detector there; the caller does
    not reach page 13 until **9.33 s**. The muted window begins nearly two
    seconds after the detector has already tripped, and through all of it the
    caller was transmitting INFO-phase signal. §2's own warning — do not
    compare two ends without checking both are in the same phase — turns out to
    apply to the ends themselves, not only to the measurements.

    So the silence hypothesis is **untested, not disproved**, and the question
    in front of it is now: **why does the answering end enter V.90 Phase 3
    1.8 s before the calling end does?** On a real call the analogue modem
    drives that timing, and `run48`, whose peer is a real modem, is the control
    for what the gap should be.
  - **⚑ And then the phase gap turned out not to matter, because the detector
    is not reading the line at all.** Three measurements, in order:

    1. **Muted, the detector reads the same numbers.** Re-running the page-13
       mute with the correlator probe armed gives `ax1` values *bit-identical*
       to the unmuted run for the first 40 events — `0e20 1c40 2a5f 387f 469f
       54be … 07c0` — and the tail, which falls inside the silent window,
       still reads `0x00cd`–`0x1770` (205–5,997), all above the 188 bar.
    2. **It is not the receive backend.** `--answerer-kernel-dispatch` on the
       PRI end — the fix that repaired the *Analog* answerer's per-symbol
       receive array — produces the identical walk and the identical failure:
       `18cc:0060 → 1cb9 → 1d25 → 1d2b`, page 7 at 12.475 s. The loopback's own
       banner confirms `answerer=kernel-dispatch`, so the arm was live.
    3. **The buffer is filled with a constant, by the routine itself.**
       Write-watching the correlator's six-word circular buffer
       (`L0 = 6`, `B0 = 0x0e38`) gated to `0x026a`: after the block zero at
       `PM 0x0d91`, **every write is `PM 0x0e24` storing `0xab3d`** — the same
       value, every pass, `DM(0x0e38)` and `DM(0x0e39)` alternating as the
       cursor wraps.

    `0xab3d` is −21,699, and six taps of it through `MY0 = 0x1554` is 21,694 —
    the exact plateau the magnitude reaches. **So the answerer's Phase 3
    detector integrates a constant that the page writes itself.** That is why
    the threshold is exceeded by 20–100×, why silence on the line changes
    nothing, and why neither receive backend matters. It is the same defect
    *class* as the RXSAMPLE freeze in this section — a detector fed a
    manufactured input rather than the line — arrived at from the opposite
    direction.

    **Next, and this is one hop:** `AR` at `PM 0x0e24` is whatever
    `PM 0x0e33` left. That routine is a normaliser — `MY0 = 0x4000`,
    `AY0 = 0x051E`, halve `MX0`/`MX1` until `|MX0| < 0x051E` — fed from the
    complex multiply at `PM 0x0e1d..0x0e22`, itself fed by `CALL $2DC4`, a
    table lookup indexed by a phase word (`AY0 = 0x01FF` mask, `0x0201` base).
    Watch `MX0`/`MX1` at `PM 0x0e1d` across the state: if they are constant,
    the phase word feeding `PM 0x2dc4` is stuck and that is the defect; if they
    vary and the output still does not, the normaliser is where it is lost.
  - **✅ Watched, and it is neither: the quadrature arm is zero.** Exec-watching
    `PM 0x0e1d` gated to `0x026a`, over 40 passes:

        mx0=2782 7654 71c4 1d2f b41f 8071 a683 0ba4 6887 7bd0 37fd ccbc …
        mx1=0000 0000 0000 0000 0000 0000 0000 0000 0000 0000 0000 0000 …

    **`MX0` is a live, varying signal and `MX1` is `0x0000` on every single
    pass.** `PM 0x0e1d` is `MY0 = MX1`, so `MY0` is zero too, and the complex
    multiply that follows —

        0e1e  MR = MX0 * MY1 (SS), MX1 = MR1
        0e1f  MR = MR + MX1 * MY0 (RND)
        0e20  MR = MX0 * MY0 (SS), AR = MR1
        0e21  MR = MR - MX1 * MY1 (RND), MX1 = AR

    — runs with one of its two arms identically zero. A quadrature correlator
    with a dead arm has no phase information left, which is how a varying input
    produces the fixed `0xab3d` that `PM 0x0e24` then stores six times per
    pass.

    **Correction, and a trap worth naming: `PM 0x2dc4` runs on every pass.**
    The "9 times against 40" reading above was a *spent watch limit* — the
    watch was armed with a budget of 8 and stopped logging, which is exactly
    what the core's `[EXEC] limit spent` line exists to prevent being misread.
    Every one of the 40 passes reports `from=2dd6`, the return from the lookup,
    and the prior-pc history is `0e19 0e1a 0e1b 0e1c 2dc4 … 2dd6 0e1d` every
    time. There are no entries that skip the call.
  - **✅ And the dead registers are `MY0` and `MY1`, in a loop that latches
    itself at zero.** Exec-watching `PM 0x0e1e`, `0x0e20` and `0x0e22` in one
    run:

    | at | `MX0` | `MX1` | `MY0` | `MY1` | `MR1` |
    |---|---|---|---|---|---|
    | `0x0e1e` | `2782` live | `0000` | **`0000`** | **`0000`** | `79a4` live |
    | `0x0e20` | `2782` | `79a4` | **`0000`** | **`0000`** | `0000` |
    | `0x0e22` | `2782` | `0000` | `0000` | `0000` | `0000` |

    The lookup is healthy — `MR1` returns `79a4, 30c7, c587, 8385`, a real
    varying sinusoid, and `0x0e1e` does load `MX1` from it. What is dead is the
    other operand. `MY1` is set at `PM 0x0e17` (`MY1 = MX0`) from `MX0` **as it
    stands on entry**, before the lookup overwrites it, and `MY0` at `0x0e1d`
    from `MX1`. So the block closes a cycle:

        0e22  MX0 = MR1  ->  0  (MR1 is zero because MY0/MY1 were zero)
        0e17  MY1 = MX0  ->  0  (next pass, entry MX0 is that zero)
        0e1e  MR = MX0 * MY1 -> 0
        0e20  MR = MX0 * MY0 -> 0, AR = 0
        0e21  MX1 = AR   ->  0

    Once zero, it stays zero: **a recursive quadrature structure latched at
    zero**, which is why a live input and a live sine table still produce the
    fixed `0xab3d`.

    So the question is what should be in `MX0` when `PM 0x0e14` is entered. It
    should be the received sample this detector is meant to mix; it is the
    previous pass's zeroed value.
  - **✅ Walked to the source, and the chain is complete: the page's own
    receive filter is publishing zeros.** Five hops, each measured:

    1. `PM 0x2a8f/0x2a90` load the detector's input pair from
       **`DM(0x0FDB)`/`DM(0x0FDC)`**, then call the chain. Both words are
       written **`0000` every pass**, by `PM 0x33fd` and `PM 0x3400`.
    2. Those two are `(DM(0x207E), DM(0x207F)) × DM(0x3F8E) >> DM(0x3F8F)`.
       **The scale pair is not the problem**: `DM(0x3F8E)/(0x3F8F)` are alive
       and in the same range as `run48`'s at the same phase — ours
       `0x446c/2, 0x1f64/2, 0x3ffd/1 …`, run48's `0x1b92/2, 0x5243/4,
       0x310f/2 …`.
    3. `DM(0x207E)/(0x207F)` are written from `SR1` at `PM 0x3391/0x3399`,
       and `SR1` comes from the biquad at `PM 0x0DAB`. **Its coefficients are
       present** — live `PM 0x1E5C-0x1E67` reads `0001 26ed b3d9 26ed 1b92
       c191 / 0000 4153 576c 4153 3724 22ef`, not zeros — even though V90D
       ships no block there and inherits them from V.8/V.OWN.
    4. The biquad's input is `SR1 = PM(I7,M5)` at `PM 0x338c` with
       **`I7 = 0x267D`** on every pass (the exec log now prints `i6`/`i7` —
       it printed `i0/i1/i4/i5` only, so "which address did that sample come
       from" was unanswerable; `adsp2181_core.c` logs both now). The word read
       is **`0x0000`**.
    5. And `PM 0x267D` is a ring the page fills itself. Write-watching
       `PM 0x267d-0x2690`: **46,962 writes, all zero, all from `PM 0x314c`** —
       the store at `PM 0x314b`, `PM(I7,M5) = SR0`, inside a 17-tap FIR:

           3140  L1 = $0024                  ; 36-word DM circular buffer
           3143  SR0 = DM(I1,M3)             ; <- the input sample
           3146  I4 = $2012                  ; PM coefficients
           3148  CNTR = $0011                ; 17 taps
           3149  DO $314A UNTIL NOT CE
           314a  MR = MR + MX0*MY0, MX0 = DM(I1,M3), MY0 = PM(I4,M5)
           314b  PM(I7,M5) = SR0, MR = MR + MX0*MY0 (RND)
           314d  PM(I7,M6) = MR1

    **So the whole failure is one starved buffer.** `DM(I1)`, 36 words, feeds
    a FIR whose output ring feeds the biquad, which feeds the quadrature
    structure, which latches at zero, which makes the correlator return a
    constant `0xab3d`, which trips the tone test, which loops state `0x0060`
    until the dwell expires, which writes `0x5678` and re-seeds — and the
    caller, whose own record stream is byte-perfect, waits at `0x0092` for
    the transmission that never happens.

    **⚠ Correction to the entry above, and it changes the diagnosis: the
    buffer is not starved, it is tiny.** "46,962 writes, all zero" was read off
    the first few lines of the log; tallied properly the ring carries plenty of
    non-zero values (`002400`, `093500`, `002100`, `002800` …). The buffer
    itself is `B1 = 0x0EC0`, `L1 = 0x24` — **`DM 0x0EC0-0x0EE3`**, 36 words,
    exec-watched at `PM 0x3143` — and it is written by `PM 0x3141` with live
    samples: `0000, 002c, 000b, 0014, fff7 …`.

    Sampled properly, the biquad's input at `PM 0x338d` over 61 passes is

        0000 (25x)  000a (4x)  0003 (4x)  0029 (2x)  000d (2x)  0002 (2x) …

    **live, and never larger than about 41.** That is the finding: not a dead
    path but one running orders of magnitude low — after the biquad, the
    `× DM(0x3F8E) >> DM(0x3F8F)` scaling rounds 40-odd counts to the `0000`
    that `DM(0x0FDB)/(0x0FDC)` show, and everything downstream follows.
    Meanwhile the answerer's own wire capture peaks at ±6,140 in the same
    window, so the page is seeing roughly 1/150 of what arrived.

    **So this joins the receive-scale theme in §2, not the RXSAMPLE-freeze
    one.** The ×4 / right-justified-14-bit corrections in that section are the
    precedent, and the question is where between the wire and `DM 0x0EC0` the
    level is lost.
  - **✅ FIXED. The direct PRI backend was handing the DSP raw µ-law octets.**
    `line_codec_rx_word()` returns `code & 0xFF` for `pri117` — the timeslot
    octet. The 2185N's SPORT delivers a *right-justified, sign-extended
    expanded* value instead, and `sport_rx_word()` **in the same file** is that
    expansion: the tower (`eicon_mips_shim`) and `dial_kernel_dispatch` both
    use it, and this backend never has. A companded octet read as an amplitude
    is not a quieter signal, it is a different one, which is why the level
    ratio was ~150 and not the ×4 §2 would have predicted.

    `EICON_EXPAND_SPORT=1` expands it, and the answerer walks straight out of
    the state this file has called a park for five sessions:

    | | walk |
    |---|---|
    | before | `18cc:0060 → 1cb9 → 1d25 → 1d2b`, `dwell=ffff`, `0x5678`, page 7 at 12.5 s |
    | **after** | `18cc:0060 → 18d8:0062 → 18e7:0064 → 18f6:0066 → 1902:0068 → 190b:006a → 1929:0070 → 1938:0072 → 1944:0074 → 1950:0076 → … → 0x0080 → **0x00b0** at 14.94 s` |

    That is `run48`'s walk — the connecting call used as the control
    throughout this section — with no failure marker, no re-seed and no
    fallback. **Everything in this entry above it is the trail to that
    fix and can be read as history**; the measurements stand, the
    interpretations were revised twice on the way, and §0.5 is the lesson.

    Left off by default. Every result recorded on this backend was taken in
    the old domain, so switching it on silently would invalidate comparisons
    rather than fix them — but it is the physically correct behaviour, and
    the case for making it the default is now a re-measurement exercise, not
    an open question.
  - **Where it now stands, and the next blocker.** With the fix on, over a 72 s
    call the answerer is silent 11–13 s (Phase 3's own quiet window) and
    transmits from 13.5 s at peak 988 with 3 distinct codepoints, while the
    caller transmits normally throughout (peak 2,620, 132–138 codepoints) and
    waits at `0x0092`.
  - **✗ "The answerer's transmitter stalls at `0x00b0`" — withdrawn, on its
    own control.** Three codepoints looks like the `0x00b0` transmit halt §2
    documents, and the first check made it worse by comparing our wire against
    `run48`'s *database* word `DM(0x3FB4)`, which the capture samples at 50 Hz
    and therefore aliases. Compared like with like — `run48.ulaw`, the card's
    own transmit on a call that connects:

    | | 12–14 s | tail |
    |---|---|---|
    | `run48` transmit | silent | **peak 924, 2 distinct codepoints**, 14.5 s → end |
    | ours | silent 11–13 s | peak 988, 3 distinct codepoints, 13.5 s → end |

    So that waveform is what this phase looks like on a connecting call, and
    our answerer is emitting it. **`run48`'s own capture ends at `0x00b0`**
    (20.9 s, its last row), so across everything the control can show, the
    V.90D answerer in this loopback is now indistinguishable from it: same
    walk, same states, same wire. The `0x00b0` entry in §2 is a V.34 finding
    and should not be carried onto V.90 on the strength of a codepoint count.
  - **⚑ Which leaves exactly one thing between this pairing and data mode: the
    caller's `0x0092`, and the bit nothing sets.** Re-measured with the
    answerer fixed and transmitting for 40+ s, `DM(0x20EF)` still takes
    **exactly one write in the whole call** — `0000`, from `PM 0x33e7`, the
    record unpacker. The caller receives the answerer's signal (its `.rx` reads
    peak 988, 3 codepoints, 15 s → 55 s), its record stream is byte-perfect,
    and its state `0x0092` has `dwell=ffff` and one test slot: bit 11 of that
    word.

    **And it is not the unpacker clearing something an earlier page set.** That
    was the obvious reading — the one write being `0000` from the record
    unpacker looks like a clear — so it was tested ungated, across the whole
    call rather than only page-13 residency: **two writes in the entire call,
    both `0000`**, `PM 0x0c94` (a block zero) and `PM 0x33e7` (the unpacker).
    Bit 11 is never set on any page at any time. Withdrawn.

  - **✅ And the reason nothing writes it: `DM(0x20EF)` is a host word, not a
    DSP one.** Scanning every image in the analog109 set for instructions that
    touch it — `((word >> 4) & 0xFFFF) == 0x20EF` with a `8`/`9` opcode nibble,
    which is how the direct DM forms encode — V90.ANA **reads it in five
    places and writes it nowhere**:

        PM 0x0bcc  AX0 = DM($20EF)   then AND $4000   (bit 14)
        PM 0x24bc  AX1 = DM($20EF)
        PM 0x29e6  AX0 = DM($20EF)
        PM 0x3492  AR  = DM($20EF)   then AND $0800   (bit 11 — the park)
        PM 0x34c6  AR  = DM($20EF)   then AND $1000   (bit 12)

    The only writer anywhere in the set is V29FC (`PM 0x22b6`), a fax page this
    call never loads, and neither the Analog kernel nor TIKRNL81.ANA touches
    it. So it is a bit-mapped **status word the host supplies**, exactly like
    the `Norm_L`/`Norm_H` case this section already fixed by deriving them from
    the CAI — and this harness supplies nothing.
  - **⚑ Pinned, and the caller moves: `0x0092` is the only thing that word was
    holding.** `EICON_ANALOG_PIN_DM=0x20ef=0x0800` (a stand-in by construction
    — it makes the DSP see a value it did not compute):

    | | caller |
    |---|---|
    | before | `17c7:0076 → 17cd:0092`, `dwell=ffff`, forever |
    | **pinned** | `17c7:0076 → 17e8:**0094** → 17f4:**0095**` at 15.56 s |

    It skips `0x0092` entirely and advances two states, then waits at `0x0095`
    with `dwell=ffff` on something else — `0x5800` (bits 11+12+14 together)
    gives exactly the same walk, so `0x0095` is not waiting on this word.
  - **⚠ And the two ends are coupled: pinning the caller moves the answerer's
    failure point.** In the same runs the answerer no longer reaches `0x00b0`;
    it walks to **state `0x007a`** and then re-seeds into the `0x1cb9/0x1d25/
    0x1d2b` recovery stream. Unpinned it reaches `0x00b0` and matches `run48`.
    So neither configuration is strictly better, and each end's progress
    changes what the other transmits — which is what a handshake is, and why
    single-ended A/Bs on this pairing need both walks reported every time.

  - **What `0x0095` then waits for, decoded the same way: a counter that never
    counts.** The V90A transition tables are `PM 0x33b0..0x33b9` —
    destinations through `DM(0x06B0)`, conditions through `DM(0x064B)`. The
    trace at `0x0095` reports `test=0000/0006`, and condition index 6 is

        33f8  AY0 = $04B0          ; 1200
        33f9  AR  = DM($21E6)
        33fa  AR  = AY0 - AR       ; exits when DM(0x21E6) >= 1200

    Sampled per frame for the whole call, `DM(0x21E6)` is **`0` from page-13
    entry to the end** — zeroed at entry and never incremented once. The
    record after this one is state **`0xB0`**, so the caller is one satisfied
    condition away from the state the answerer is already sitting in.

    So both caller parks have the same shape as the answerer's did before the
    SPORT fix: a state waiting on a value the page's own receive side never
    produces. Bit 11 of `DM(0x20EF)` and this counter are both downstream of
    the V90A page not decoding what it is being handed.
  - **⚑ The hypothesis that follows, and it is structural rather than another
    address: `--analog-codec-rate 9600` cannot carry V.90 downstream.** V.90's
    downstream *is* the network's PCM codewords — the analogue modem recovers
    the digital modem's chosen codepoints, which is why the pairing exists at
    all. This rig resamples the caller's line 8000 → 9600 at the RTP boundary
    (`e513a0d`, and V.8 genuinely needs 9600 — that is what made V.8 complete).
    A resampler is exactly the operation that destroys codeword identity: after
    it, no sample the page sees is a codepoint the answerer transmitted.

  - **✗ Wrong, and the page says so itself — do not build a per-page codec
    rate.** The premise was checked before anything was built, by sampling the
    rate triple across the call:

    | | `Samplerate` `DM(0x3F66)` | `Samplebuffersize` `DM(0x3F67)` | ratio `0x3754/0x3755` |
    |---|---|---|---|
    | boot (DIAL) | 8 → 8000 Hz | 4 | 16/36 |
    | V.8 at 0.00 s | **4 → 9600 Hz** | 4 | 15/15 |
    | **V.90 APCM at 9.35 s** | **4 → 9600 Hz** | **3** | 15/15 |

    V90.ANA keeps `Samplerate` 4 and changes only the buffer size, 4 → 3 —
    a 9600/3 = **3200 symbol/s** rate, which is the V.34-family upstream symbol
    rate the analogue side transmits at. So the page asks for the same 9600 Hz
    codec V.8 asked for, `--analog-codec-rate 9600` is what it wants, and the
    resampler is not destroying anything the page expected to receive. The
    hypothesis is withdrawn before it cost a session; `DM(0x21E6)` not counting
    is something else.

    **Where that leaves it**: the answerer is finished against the only
    available control; both of the caller's parks are located and decoded —
    `0x0092` on a host status word no DSP code writes, `0x0095` on a counter
    that never leaves zero — and the codec rate is now excluded as the reason.
    The next question is what increments `DM(0x21E6)`.
  - **✅ Answered, and it is the same answer as `DM(0x20EF)`: another page owns
    it.** Write-watching `DM(0x21E6)` gated to `0x026b`, with `DM(0x120E)` as
    the positive control (6 writes) in the same run: **two writes, both
    `0000`, both from `PM 0x2622`** — which is a block clear,
    `DM($21E5..0x21F7) = M0`. Scanning V90.ANA for every instruction touching
    the word: it is **cleared there and read once, at `PM 0x33f9`, the
    condition routine itself**. Nothing increments it.

    Across the whole analog109 set the only writer is **V34.ANA `PM 0x2468`**
    — and the only writer of `DM(0x20EF)` is V29FC `PM 0x22b6`. Neither page
    runs in this call.
  - **✗ Layering V.34 under V.90 does not run it either.**
    `EICON_RELAY_UNDER=0x0261:0x026b` with the answerer's SPORT fix on, and
    again with the `0x20EF` pin so the caller reaches the second park: the
    relay applies (`[relay-under] laid 0x0261 under 0x026b`), and
    `DM(0x21E6)` is still `0` for the whole of page-13 residency. The V.34
    writer is resident and never called — V90.ANA's own dispatch does not
    invoke it. So "V.90 APCM is a partial overlay layered over V.34" is not
    sufficient on its own, whatever else is true of it.
  - **✗ And satisfying both words by hand does not produce a connection.**
    Pinning `0x20ef=0x0800,0x21e6=0x04b0` takes the caller's cursor down the
    branch — `17e8:0094 → 1956:0095 → 19f2 → 19f5`, into the `0x19xx` stream —
    where it parks again at `dwell=ffff`, and **both ends then fall back to
    page 7** (`TrnProgress 0x002c`/`0x002e`, INFO_RX running). Which is what
    this file says about pins: a stand-in establishes what a path *would* do,
    never what the firmware does on its own. Two faked status words send the
    machine down a path whose preconditions did not actually happen.

    **So the caller's blocker is now fully characterised and is not a bug in
    any page.** V90.ANA consumes two words that no code in this configuration
    produces, and their producers live in pages this call never runs. On real
    hardware the MIPS firmware maintains that status; this harness writes the
    data pump's database itself and maintains none of it — the same gap that
    `Norm_L`/`Norm_H` had before they were derived from the CAI, and the same
    one §7 names as where fidelity is lost. **That is the next piece of work,
    and it is a modelling decision rather than another measurement**: either
    derive these words from the driver the way `eicon_idi.norm_l_from_cai()`
    does, or give the calling side a backend that runs the MIPS firmware at a
    usable speed. Do not ship the pins.
  - **Two more excluded, so the next session does not spend runs on them.**

    * **Receive level is not it.** `EICON_ANALOG_RX_GAIN_DB=18` on the caller,
      with the answerer's SPORT fix on: identical walk, same `0x0092` park.
      The answering side's blocker *was* a level/domain error, so this was
      worth one run; it is not the same defect twice.
    * **`V8_setup` is already correct, and the control is surprising.** The
      guide's write-database offset 0x04, `DM(0x3EE4)`, carries bit F
      `V90_APCM` — "if 1, analogue side V.90 modulation is enabled" — and bit
      E `V90_DPCM`. Measured: our caller writes **`0x8000`** (APCM enabled,
      correct for the analogue side) and our answerer `0x6000`. **`run48`, the
      call that connects, writes `0x0000`** — neither bit. So these bits are
      not what admits V.90 in practice, our caller is not missing them, and
      `V8_setup` is off the list.

  - **✗ "The V.90 APCM page expects V.34 to have run first" — disproved by the
    guide's own boot diagram, before it cost a run.** `addspv90guide.pdf`
    Figure 2 (p.13) draws the boot graph, and **INFO has two children, V34 and
    V90D, side by side**: the V.90 phase 3-4 page is entered *from INFO
    directly*, with a `retrain` arrow back to INFO from each. There is no
    V34 → V90 edge. Our caller's V.8 → INFO → page 13 is exactly the flow the
    guide draws, and §2's "V.90A is queued behind V.34 phase 2" is about the
    *file set* the PRI task admits, not about page sequence. The entry below is
    kept for its PM measurements; its heading is withdrawn.
  - **✅ And the missing writer is now a measured negative, not an inferred
    one: no code and no record can raise bit 11 of `DM(0x20EF)`.** The earlier
    scan looked for absolute stores, which is the exact trap §3 records for
    RXSAMPLE — a base address that arrives as an immediate into an I register
    is invisible to it. Redone across **every image in the analog109 set**,
    looking for indirect setups as well (`I0..I7 = $20Ex`, `MR0 = $20Ex`) as
    well as `DM($20EF) = `:

    | image | what it has | runs in this call |
    |---|---|---|
    | V29FC | the only absolute store to `DM(0x20EF)` | no |
    | V34.ANA | `I5/I7 = $20E1/$20E2/$20E5` (its own state) | no |
    | G.729AB | `I0 = $20EF` (codec scratch) | no |
    | **V90.ANA** | reads only, plus the record unpacker | **yes** |

    And the record table, which was never read at all, now is:
    `tools/record_table_decode.py`. The unpacker at `PM 0x33DD` is a three-word
    entry format — `index = A >> 8`, `value = (C & 0xFF00) | (B >> 8)`, record
    ends at index 25 — verified against three live records word for word. The
    V.90A table is **55 records** from `0x1689` (`PM 0x3348` sets both cursors
    there) and walks `0050 0052 0053 0054 0056 0058 005a 0060 … 0092 0094 0095
    00b0 … 00d0`, which is the walk `--trace-v90a-state` prints. Of those 55
    records, **exactly one writes `DM(0x20EF)`: the first, with `0x0000`**.

        tools/record_table_decode.py <dm.bin> --start 0x1689 --index 6

    So the word can only be written from outside the DSP. Asserted in
    `tests/test_record_table_decode.py`, because the negative is what the rest
    of this rests on.
  - **✅ And what the bit *means* is now named, from the page's own dispatch.**
    `DM(0x20E9..0x20F0)` is not a per-state parameter list: it is a running
    configuration block, and `PM 0x2494` compares each word against a shadow
    after every record and calls a reconfiguration routine for the ones that
    changed. For `DM(0x20EF)` that routine is `PM 0x2559`, which walks a
    **12-entry action-vector table at `DM(0x007D)`** and calls the vector for
    every bit set. Bit 11 is the last entry, `DM(0x0088)` = **`PM 0x30B2`**:

        30b2: AX0 = $FFFF ; DM($3FAE) = AX0 ; DM($3FAF) = AX0 ; DM($3FB1) = AX0

    `DM(0x3FAE)`/`DM(0x3FAF)` are `RXD0`/`RXD1` in the guide's database — the
    received-data words — so bit 11 is the *receive-data-idle* action. V90D
    carries the identical construct one word lower (`PM 0x2385` → `PM 0x2443`,
    table at `DM(0x007E)`), which is what makes the reading a design rather
    than a coincidence: **11 bits on the digital page, 12 on the analogue one,
    and the analogue page's extra bit is the one our caller waits for.**
  - **⚑ And the park is real: leaving `0x0092` early breaks the answerer.**
    `EICON_ANALOG_PIN_DM` takes a gate, so the bit can be held *only* while the
    machine is on the state under test — a far cleaner counterfactual than
    Session 249's permanent pin, and it releases by itself:

        EICON_ANALOG_PIN_DM=0x20ef=0x0800@0x20f9:0x0092

    | | caller | answerer |
    |---|---|---|
    | baseline (×1) | `0076 → 0092`, parks | `0080 → 00b0` at 14.94 s |
    | gated pin (×3) | `0092 → 0094 → 0095` at 15.4–15.6 s | **falls back to INFO**, `0024 → 002c` at 12.4–12.5 s, every run |

    Three for three. So `0x0092` is not a spurious wait the harness can skip:
    the caller is *supposed* to be there, and the answerer needs whatever it
    does while it is. Do not read the pinned run as progress — it reaches
    `0x0095` by breaking the other end. What it does establish is the next
    condition: `0x0095`'s test slot is `PM 0x33F8`, `AR = 0x04B0 − DM(0x21E6)`,
    i.e. **wait for that counter to reach 1,200**.
  - **✅ And the reason it broke the answerer is timing — which gives this
    pairing its furthest joint state yet.** The caller enters `0x0092` at
    12.55 s and the answerer only reaches `0x00b0` at 14.94 s, so releasing on
    entry walks the caller off a signal the other end is still working on. The
    pin syntax now takes a trailing `>SECONDS` for exactly this; "which end is
    ahead of the other" is a question about time, and a state gate cannot say
    it:

        EICON_ANALOG_PIN_DM=0x20ef=0x0800@0x20f9:0x0092>16

    | release | caller | answerer |
    |---|---|---|
    | on entry (12.55 s) | `0092 → 0094 → 0095` | **INFO fallback**, 3/3 |
    | **after 16 s** | `0092 → 0094 → 0095` at 19.0 s | **holds `0x00b0`** |
    | after 20 s | same, at 23.0 s | **holds `0x00b0`** |

    So with one timed stand-in the answerer sits at `0x00b0` — the last state
    `run48` itself reaches — and the caller at `0x0095`. Both ends are further
    along together than in any previous session. It remains a stand-in: it
    establishes the path, not the firmware.
  - **✗ And the counter cannot be faked the same way.** Adding
    `0x21e6=0x04b0@0x20f9:0x0095>16` takes the caller out of `0x0095` and
    **straight into an INFO fallback** (`0x002c → 0x002e`, 19.1 s) — the same
    ending Session 249's ungated dual pin produced, so that result was about
    the counter and not about the gating. `DM(0x21E6)` has to actually count.
  - **↺ And `DM(0x21E6)` does have a writer after all — it just never runs.**
    Session 249's "nothing increments it" was the same absolute-store grep.
    `PM 0x2632` sets `I0 = $21E5` and walks the block with `DM(I0,M1) = MR1`,
    and the block-clear at `PM 0x2621` clears exactly the words it uses
    (`0x21E5..0x21E8`, `0x21F5..0x21F7`), which is what a state block looks
    like. Its three entry points are vectors in another action table:
    `DM(0x00E6) = 0x262F`, `DM(0x00E7) = 0x2629`, `DM(0x00E8) = 0x262C`.
    Exec-watched over a full call gated to `0x026b`, **`PM 0x2632` executes
    zero times** while `PM 0x2621` executes (the positive control). So the
    counter's writer is dispatched by an action bit that is never set either —
    the same shape of gap as `0x20EF`, one table along.
  - **⚑ And that table is the next thing to explain.** `DM(0x00E6..0x00E8)` is
    **outside every dispatcher base this page indexes from records**: the
    twelve are `0x0014, 0x0021, 0x0024, 0x0025, 0x0027, 0x0034, 0x003E,
    0x004E, 0x005E, 0x006E, 0x0075, 0x007D`, and none spans `0x00E6`. The one
    construct that reaches that high is the **mode dispatch at `PM 0x24DC`**:
    when `DM(0x20F0)` ≥ 4 it walks a table based at `DM(0x00B7)` with the
    stride in `DM(0x21BE)`. `DM(0x20F0)` is that mode word — and across all 55
    records it is only ever set to **0 or 1**:

        tools/record_table_decode.py <dm.bin> --start 0x1689 --index 7

    **Answered in the same sitting, and it closes the loop.** `PM 0x3526` is
    the mode-4 setup — `DM(0x21BE) = DM(0x2129)` (the stride) then
    `DM(0x20F0) = 4` — and it is vector `DM(0x004F)`, which is **bit 1 of
    `DM(0x20F5)`**, the word whose dispatcher is based at `DM(0x004E)`. One
    record in the table sets that bit: **state `0x00CA`**. So the routines that
    maintain `DM(0x21E6)` are enabled far *later* in the walk than `0x0095`,
    which is the state waiting on the counter — the counter arm of `0x0095`
    cannot be its first-pass exit.
  - **↺ Correction, and it matters: `0x0095`'s primary condition is not the
    counter.** The record's handler slots are
    `1734 1938 16a4 16a4 340a 33f8 340a 340a 348f`, and the last slot is the
    one the machine advances on — `PM 0x348F`, **bit 14 of `DM(0x20EB)`**.
    `PM 0x33F8`'s count-to-1,200 sits in test slot 1, paired with `next[1]`, so
    it is the alternate arm, and the mode-4 finding above says it is not the
    one a first pass takes. Session 249's "the second park is a counter that
    never counts" named the wrong half of the state.
  - **⚑ Which makes the whole blocker one sentence.** `DM(0x20EB)` bit 14, like
    `DM(0x20EF)` bit 11, is tested by a state handler, is written by nothing in
    the analog109 set, and is set by **no record in the table** — 13 records
    write `DM(0x20EB)` and none of them sets `0x4000`:

        tools/record_table_decode.py <dm.bin> --start 0x1689 --index 2

    So this is not two missing words, or three; it is **one missing producer**.
    The V.90A page's whole exit vocabulary is a status block that something
    outside the DSP maintains, and this harness's calling side maintains none
    of it. §7's "derive them from the driver, or give the calling side a
    backend that runs the real MIPS firmware" is now the *only* remaining item
    on this path, and the second option is a profiling job first (`e8b0e82`:
    the calling tower costs ~2,127 ms per 20 ms tick; `mips_interval` first).
  - **✗ And "derive them from the driver" is empty — checked, not assumed.**
    `divas4linux-master`'s only DSP memory access anywhere is
    `dsp_check_presence()` (`kernel/io.c:1229`), which writes `0x5a5a` and its
    complement to DM `0x4000` through the address/data port pair **with the
    RISC held in reset**, at adapter start. There is no runtime DSP write path
    in the driver at all: during a call the data pump's host is the card's own
    MIPS protocol code. So of §7's two options only one is real, and it is the
    MIPS backend.
  - **✅ And the MIPS calling backend already exists, boots, and now
    originates — §5's "never originated a call" is closed.**
    `--caller-native-mips` on `analog109` selects `analog_mips_modem.py`, which
    runs the build-109 image (`docs/firmware/build-109/te_dmlt.am`). It failed
    with `native CALL_REQ rejected: no exchange battery` — **on a rig whose
    next log line is `battery=48.0 V`**. That was ordering, not the line:
    `begin_dial()` runs at the INVITE and `attach_physical_line()` only at the
    200 OK, so `line_in_service()` was reading `analog_line is None`. The
    originating path now attaches the DAA before it dials, and the tower
    answers `native CALL_REQ queued for 6001`, then `native dsp_assign selected
    0xbf804800` and serves its own `0x0258` TIKRNL download.
  - **⚑ Which puts the next blocker on that path in the execution model, not
    in any page.** With the tower originating, `V8.ANA` is served at sample 17
    and at sample 18 the harness reports `the task did not return within 65536
    cycles`; raising `EICON_FRAME_BUDGET` to 524,288 moves the number and
    nothing else. The PC histogram says why — one loop, 4,334,971 executions
    of every instruction in it:

        1ff8: AR = DM($3F08)            ; read database
        1ff9: DM($38F2) = AR
        1ffa: AY0 = $0060
        1ffc: IF EQ JUMP $2002
        2002: AY0 = DM($3EE1)           ; GEN_setup1
        2003: AR = $0008                ; bit 3 = CH, "1 = call or originate"
        2005: IF NE JUMP $2015
        2015: I4 = DM($38D0)
        2016: CALL (I4)                 ; and round again

    It is **dispatching**, not stalling: `configure_modem('calling')` writes
    `GEN_setup1 = 0x048C` and bit 3 is set, so the `NE` arm is the one being
    taken. This is V.8's own service loop running continuously and being
    interrupted by SPORT on real silicon, against a harness that expects a task
    to return once per frame. That is `harness-execution-plan.md`'s subject
    exactly, and §0 says to take it there rather than add another page-specific
    workaround. **It is also the last thing between this project and a V.90
    caller with a real host**, which is the only remaining route to data mode.
  - **↔ How to read a state's exit, for any of these pages.** Verified against
    the live V.90A trace on two states, so it can be used without a run:

    | | V.34 | V.90A |
    |---|---|---|
    | block base | `DM(0x2137)` | `DM(0x20E9)` |
    | dwell / state | index 15 / 16 | index 15 / 16 |
    | branch-target slots | 17–20 → record at `DM(0x0676 + slot)` | 17–20 → `DM(0x06B0 + slot)` |
    | condition slots | 21–25 → handler `PM DM(0x064B + slot)` | 21–25 → `PM DM(0x064B + slot)` |

    The **index-25 entry is the state's primary condition** — the one the
    machine advances on. Checks: V.90A `0x0092` has index 25 = `0x0F` and
    `DM(0x064B+0x0F)` = `0x3492`; `0x0095` has `0x25` → `0x348F`. Both are what
    `--trace-v90a-state` prints in its last handler slot.
  - **⚑ And that is what the V.34 blocker looks like through it.** V.34's
    `0x00b0` (record `0x1C95`) has index 25 = **1** → `PM 0x2E32`, which is
    `I0 = $2146` into the shared decrement — the **plain dwell countdown**, on
    the block's own dwell word, with dwell `0x0080`. Its structural twin is
    V.90A's `0x33EC`. So the V.34 answerer's `0x00b0` **waits on nothing
    external**: this session's V.90A answer does *not* transfer to it, and the
    two blockers are different in kind. What does transfer is the machine:
    the record-apply tail at `PM 0x2E2F/0x2E30` writes `DM(0x21D6)` and
    **`DM(0x224C)`** — the very word §2 records as going quiet — so "no further
    `DM(0x224C)` requests" is a *consequence* of the record machine stopping,
    not a separate transmit fault. **The first measurement for a V.34 session
    is therefore whether `DM(0x2146)` is still counting down at `0x00b0`**, and
    if it is not, why the state machine is not being serviced.
  - **✅ Measured after all — out of the archive, which had it all along.**
    `artifacts/loopback-v34/{wall-gap-off,wall-gap-on,wall-gap-noguard}` are
    three preserved runs of the stall, and the answerer log answers the
    question without any rig:

    | | |
    |---|---|
    | `0x00ac` | `0x00ac → 0x00ac` **eight times in 0.5 s**, every 60–80 ms |
    | `0x00b0` | entered 10.360 s, one line at 10.380 s, **then nothing for the remaining ~50 s** |

    The tracer keys on the record block, so the repeats at `0x00ac` are the
    machine *actively re-applying records*. It applies `0x00b0`'s once and
    never applies another. And `0x00b0`'s exit is the dwell countdown with
    dwell `0x0080` — **128 ticks, 16 ms at 8 kHz** — so it should have expired
    three thousand times over. **The condition is not unmet; the scheduler
    stops running.** That also explains §2's symptom without a second cause:
    `DM(0x224C)` is written by the record-apply tail, so "no further
    `DM(0x224C)` requests" *is* the scheduler stopping, seen from downstream.
  - **⚑ And the state that stalls is the state that swaps the per-sample
    routine set.** `0x00b0`'s record changes config word 0, `DM(0x2137)`, from
    `0xA700` to `0x9600`, and V.34's dispatcher (`PM 0x23AD`, the twin of
    V.90A's `PM 0x2494` and V.90D's `PM 0x2385`) calls `PM 0x249B` for it.
    That handler is *not* a bit-dispatch like the `0x20EF` one — it is a
    packed-selector decoder: `SE = 2`, `CNTR = 4`, and it walks word 0 **two
    bits at a time**, using each field as an index into the table whose base is
    `DM(0x002D + k)` (`PM 0x00E9, 0x001C, 0x0018, 0x00B5`), writing the four
    results into `DM(0x217D..0x2180)`. So word 0 is four 2-bit selectors over
    the per-sample routine set:

        0xA700 = 10 10 01 11 ...    fields 2,2,1,3   (state 0x00ac)
        0x9600 = 10 01 01 10 ...    fields 2,1,1,2   (state 0x00b0)

    Two of the four change — and because the handler is deterministic, the
    swap can be computed off the image rather than measured:

    | slot | at `0x00ac` | at `0x00b0` |
    |---|---|---|
    | `DM(0x217D)` | `PM 0x2CE7` | `PM 0x2CE7` |
    | **`DM(0x217E)`** | **`PM 0x283A`** | **`PM 0x27FE`** |
    | `DM(0x217F)` | `PM 0x2770` | `PM 0x2770` |
    | **`DM(0x2180)`** | **`PM 0x2761`** | **`PM 0x252D`** |

    `PM 0x283A` — the one that was running — writes `0x0000` three times from
    `I4 = $3FA7`, i.e. it **zeroes `TXSAMPLE_0..2`**, then joins the common
    tail at `PM 0x2804`. That is the quiet path, and `0x00ac` is a quiet
    state. `PM 0x27FE` is the full path, and it begins:

        27fe: I4 = DM($2181)
        27ff: CALL (I4)

  - **⚑ `DM(0x2181)` is a slot this page's configuration never fills.** No
    absolute store writes it anywhere in the image; it is read at three places
    (`PM 0x27FE`, `0x2840`, `0x31AD`); and the two walks that populate the
    routine block **provably skip it** — `PM 0x249D` fills `0x217D..0x2180`
    (`CNTR = 4`, from config word 0) and `PM 0x24A2` fills `0x2182`
    (`CNTR = 1`, from config word 5). So the routine `0x00b0` switches to
    begins by calling through the one slot between them.

    **✗ MEASURED, AND DISPROVED.** With the rig restored (§2) the answerer
    reaches `0x00b0` live, and one watch settles it. `DM(0x2181)` **is
    written**, at `PM 0x24B0/0x24B1`, `I0 = 0x2181`, `I4 = 0x0031`,
    `MR0 = 0x2137` — the *second* config walker, whose stores land on `0x2181`
    **and** `0x2182`, not `0x2182` alone; the off-by-one in the static reading
    below was mine. Values `0x252D`, `0x2761`, `0x2544`, all real routine
    addresses. And `PM 0x27FF`, the `CALL (I4)` through it, **executes 2,295
    times**. The slot is filled and the call lands somewhere.

    **↺ The reading it rested on is withdrawn too: the scheduler does not stop
    at `0x00b0`.** Exec-watched in the same run, `PM 0x2E32` — the dwell
    countdown — runs **6,516 times and is still running at the last cycle of
    the call** (`cyc=264,300,925`; `0x00b0` entered at 12.52 s, run ended at
    60 s). The archive's "applies one record and never another" was the tracer
    printing only on key change — a state that re-enters itself with the same
    record prints nothing. **That is the no-writes-means-nothing trap a fourth
    time in this file, and this time it was mine.** The live picture is the
    opposite of the archived one: the machine runs, the countdown ticks, and
    the state still does not advance. **So the question is what re-applies the
    record**, because a dwell of `0x0080` that is reloaded never expires.

    **✅ And that is measured too: it re-applies *one* record, forever.**
    Write-watching the cursor `DM(0x14A5)` on the answerer at `0x00b0` gives a
    two-write cycle repeating every ~5,900 cycles for the rest of the call:

        dm w 14a5=1ba5 ppc=2dd6      ; DM($14A5) = MR0  -- record to apply
        dm w 14a5=1bb7 ppc=2ddb      ; DM($14A5) = I4   -- cursor past its end

    `PM 0x2DD6..0x2DDD` is the apply path — set cursor, unpack (`0x2DA0`,
    `0x2E17`), advance, then `AX0 = DM(0x2147); DM(0x3FC2) = AX0` to publish
    the state — so those two values are the **start and end of a single
    record**, `0x1BA5`, applied over and over. The dwell is therefore reloaded
    on every pass and can never reach zero, which is exactly the symptom.

    **↺ "Forever" is wrong, and the corrected ordering is more interesting.**
    Watching the cursor and the record region in *one* run, with cycle stamps
    that reproduce across runs to the digit:

    | | cycle |
    |---|---|
    | `0x00b0` entered | 12.52 s |
    | cursor loop on `0x1BA5`, first pass | **202,201,720** |
    | cursor loop, last pass | **203,723,061** |
    | record region `0x1BA5..0x1BB8` **zeroed** by `PM 0x0D94` | **264,192,950** |

    So the loop is **bounded** — about 1.5 M cycles — and then stops on its
    own; after that nothing happens at all, and 61 M cycles later a block-clear
    walks over the record table itself. That zeroing is a real finding in its
    own right and the same class as the two overwrite bugs §2 already records
    (the native bulk worker over `DM(0x00A8..0x00A9)`, `PortableBulkDelay` over
    `DM(0x3fb8)`) — **but it is not the cause of the loop, because it happens
    60 M cycles after the loop has ended.**

    **And the contradiction above survives this, sharper.** During the loop the
    record was still intact — the zeroing is later — so applying `0x1BA5`
    should have published `0x0090` into `DM(0x2147)` and thence `TrnProgress`,
    and it did not. `DM(0x3FC2)` is confirmed as the logged source
    (`eicon_adsp_sip.py:2148`), so that half is closed too.

    **And the leading explanation is the sampler, not the firmware.** The watch
    registers confirm the apply path exactly — at `PM 0x2DD6`, `mr0=1ba5`, so
    `DM($14A5) = MR0` writes the record address; at `PM 0x2DDB`, `i4=1bb7`,
    `mr0=2137` (block base) and `mr1=0019` (terminator), with `i0=2151` =
    `0x2137 + 26`, so the unpacker walked the **full 26-word block** including
    index 25. Index 16 was therefore stored to `DM(0x2147)` and published.
    Meanwhile the loop's period is **5,966 cycles** (202,201,720 → 202,207,686)
    — roughly *one record application per 8 kHz sample* — while
    `eicon_adsp_sip.py` samples `DM(0x3FC2)` **once per media tick**. A state
    word that changes faster than the sampler reads it is not a state word that
    did not change. `TrnProgress` reaching `0x00b0` and appearing to stop is
    consistent with the machine cycling `0x00b0 → 0x0090 → …` underneath it —
    and note state `0x00b0`'s own `next[0]` resolves to record `0x1BA5`, which
    *is* state `0x0090`, so that cycle is the table's own retrain branch.
    **✅ CONFIRMED, and it changes what §2's V.34 row means.** Write-watching
    the state word `DM(0x2147)` directly instead of the per-tick `TrnProgress`:

        53 x  dm w 2147=0090          <- capped by the watch budget
         1 x  dm w 2147=0076 / 0074 / 0072 / 0071 / 0070 / 0064 / 0062 ...

    The early states are written once each — the normal walk — and then
    `0x0090` is written **over and over**, while the log still shows nothing
    past `0x00b0`. **The answerer at `0x00b0` is not a stopped machine: it is
    re-entering state `0x0090` repeatedly**, which is exactly what state
    `0x00b0`'s own `next[0]` selects (record `0x1BA5` = state `0x0090`) — the
    table's retrain branch. The disagreement was the instrument: `TrnProgress`
    is sampled once per media tick and the machine changes it about once per
    8 kHz sample.

    **So "the answering page stops publishing transmit data at `0x00b0`" —
    §2's V.34 row, standing since Session 149 — is describing a retrain loop,
    not a halt.** Everything in that row's symptom list follows from a machine
    that keeps going back to `0x0090`: no further `DM(0x224C)` requests from a
    *new* record, a line frozen on one sample because the same states repeat.
    **The question is not "why did it stop" but "why does `0x00b0` keep taking
    its retrain branch"** — and that is a different investigation, with the
    condition and branch machinery already decoded above.

    **Next, and it is four words:** `PM 0x2DCC..0x2DD5` chooses the target with
    three `IF LE JUMP $2DD6` tests over `DM(0x21F0)`, `DM(0x21F1)` and the
    handlers in `DM(0x21F4)`/`DM(0x21F5)`; every one falls through with the
    same `MR0`. Read those four and the loop is named.

    **⚠ One loose end, now halved and still open — do not build on the state
    label.** The decode is exact, so "the table walk is off by one" is
    eliminated: record `0x1BA5` ends at **exactly** `0x1BB7`, the two cursor
    values measured, and carries `index 16 = 0x0090`, `dwell 0x0032`:

        0x1ba5  [(16,0090) (14,02bc) (15,0032) (17,0013) (21,000a) (25,0001)]
        0x1bb7  [(16,0092) (14,0578) (21,0000) (22,0000) (25,000a)]

    But the apply path publishes `DM(0x2147)` — index 16 — as `TrnProgress` at
    `PM 0x2DDC`, so re-applying `0x1BA5` should republish `0x0090`, and the
    answerer's log shows `TrnProgress` reaching `0x00b0` at 12.52 s and **never
    changing again** while the cursor cycles for another 30 s. Those cannot
    both be true of the same record. What is left: either the live DM at
    `0x1BA5` differs from the overlay image (a partial overwrote it), or the
    logged `TrnProgress` is not `DM(0x3FC2)` at that moment. **Settle that
    first** — the cursor *addresses* are measured and solid; the state *name*
    attached to them is not.

    The original reasoning is kept below because its structure was right even
    though its conclusion was not. It was a mechanism that
    would produce exactly the observed stop, but it was not measured, and the
    indirect-writer scan is only half done: `PM 0x0ED2`'s `I4 = $217D` reads
    `PM(I4,M5)`, program memory, so it is a coefficient walk and not a writer,
    but `PM 0x0AEB` sets `I5 = $2180` and stores `0x2180` into `DM(0x217A)`
    and `DM(0x217C)` as a **buffer base**, which would mean this region is
    reused across phases. Settle that before believing the slot is empty at
    the moment `0x00b0` reads it. The measurement is a write-watch on
    `DM(0x2181)` plus an exec-watch on `PM 0x27FF` — and it needs the V.34
    loopback rig, which is where this stopped (below).
  - **⚠ The V.34 loopback rig does not currently reach page 8, and it is a
    separate defect with its own §2 row.** Located rather than guessed at:
    `DM(0x03EF)`, the dial script cursor, takes **its first write of the whole
    call at cycle 244,061,165**, sample ~250,000, 31.3 s. The script has not
    run at all before that — it is not parked on a value. Everything after is
    the archive's sequence unchanged, just 31 s late, and by then the answerer
    has cycled INFO and dropped to V.22. Ruled out on the way: instrumentation
    (reproduces with every watch removed), the tone detector
    (`EICON_PIN_DM=0x0554=0x0020` from the start stops the script running at
    all, which is the documented unconditional-pin failure), and realigning
    the ends with `--ring-seconds 32`. Nothing in the entries above depends on
    this rig — the stall came out of the archive and the routine swap off the
    image — but the `DM(0x2181)` measurement does.
  - **✅ And the rig is most of the way back: the loopback connects again.**
    Two causes, both found and one fixed:

    1. **The answer-side page-settling loop was running on the caller.** It
       waits up to 8,192 frames for V.8 to become resident before media
       starts, and prints a warning whose own text says "answering on a page
       other than V.8". On the originate side V.8 cannot arrive there at all —
       the dial page never calls the kernel page-request routine, so V.8 comes
       from `ORIGINATE_V8`, which needs the dial page at `TrnProgress 0x0051`,
       which needs media frames that have not started. It therefore always
       burned all 8,192. Now role-conditional; dial-park exit 250,259 →
       242,067 samples.
    2. **`EICON_V8_TIMER_SENTINELS` is fatal on the originate path.** The
       commit that added it (`6a79993`) says the seed "was introduced for the
       originate/V.32 partial-overlay path and nothing here tested that". This
       is that test:

       | | caller | answerer |
       |---|---|---|
       | default (sentinels on) | `0x0000` — never starts | `0x0026` |
       | **off, both ends (×2)** | **`0x00d0`** | **`0x00d0`** |

       Both ends in data mode, twice, against a same-session control that
       reaches nothing. Left off by default is **not** the right call yet: it
       is one variable in a rig with a known ordering problem, and §2's row
       records it as a switch rather than a default.

    **But it connects as V.22, on bootpage 1.** With both sentinels off the
    caller starts its dial script at 13.6 s rather than 30.0 s — still far
    later than the archive's 0.21 s, and by then V.8 has moved on. So the
    remaining work for a V.34 connection is that 13.6 s, and nothing else in
    this rig is now known to be wrong.
  - **↷ And two things are already known about that 13.6 s, which is where the
    next session should start rather than re-deriving them.**

    * **It is not cycle starvation.** Between media start (`cyc≈33 M`) and the
      V.8 load (`cyc=125,968,676`) the caller spends 93 M cycles over 13.6 s
      of media — about **6,800 cycles per sample against the 20,000 the
      `EICON_ADSP_MEDIA_CYCLES` allowance gives it**. The page is going idle
      inside its budget, so it is *waiting*, not being cut off. Raising the
      budget is therefore the wrong lever, and `EICON_ADSP_BUDGET` should not
      be the first thing tried.
    * **It tracks the peer, so it is line input rather than a local timer.**
      The same caller starts at **30.0 s when the answerer's sentinels are on**
      and **13.6 s when they are off** — nothing about the caller changed
      between those two runs. A local timeout cannot do that. The caller's
      dial page is responding to something on the line, which is also why
      `--ring-seconds` cannot fix it: the caller's media clock starts at the
      200 OK, so delaying the answer moves both ends together.

    * **✗ Giving the caller a head start makes it worse, not better.**
      `--setup-gap-ms` delays *only* the answerer's media, which is exactly the
      head start the argument above asks for. It does not work, and the
      direction of the failure is the point:

      | setup gap | caller's dial start | result |
      |---|---|---|
      | 0 | sample 108,960 (13.6 s) | both `0x00d0`, **V.22** |
      | 14,000 ms | sample **206,997** (25.9 s) | both `0x00d0`, **V.22** |
      | 20,000 ms | later still | both `0x00d0`, **V.22** |

      A longer silence pushes the dial start out by roughly the silence, which
      is the peer-coupling above measured a third way. **And the modulation
      does not move**: the caller goes V.8 → V.22 directly and never requests
      INFO, at every gap. So V.22 here is *not* the timing race it looks like,
      and "align the two ends" is off the list — the next question is about the
      V.8 exchange itself, with `NORM_L` already corrected to `0xa13f`.

    * **✅ And here is what it actually is, on a control that cannot be
      argued with: the caller's resident page never runs a frame for 13.6 s.**
      `DM(0x0663)` is a **free-running modulo-24 frame counter** — `PM 0x3CD6`
      reads it, adds 1, wraps at 24 and stores it back, unconditionally, once
      per frame — and the arm dispatch at `PM 0x3CFF..0x3D08` indexes a jump
      table at `PM 0x32FE` with `DM(0x0663) >> 2`, so the dial-script arm
      (`PM 0x3D14`, table index 4) is entered on 2 frames out of every 24. A
      counter like that is the positive control §0.4 always asks for: if the
      page runs, it increments. Write-watched:

          [WATCH] dm w 0663=0001 ppc=3cdb cyc=124,919,075
          [WATCH] dm w 0663=0002 ppc=3cdb cyc=124,919,665
          [WATCH] dm w 0663=0003 ppc=3cdb cyc=124,920,156

      **The first increment in the whole call is from zero, at 13.6 s.** So the
      page's per-frame code is never entered before that, and every earlier
      reading here is downstream of it: the dial script is not slow, the script
      pointer is not the problem, the peer coupling is not a listener, and
      `DM(0x03EF)`'s silence was "the code that writes it never ran" — the same
      no-writes-means-nothing trap as `DM(0x21E6)`, for the third time in this
      file.
    * **↺ Refined, and the refinement is the answer: the page runs, but only
      its 8 kHz half. The symbol-rate half is never dispatched.** "Never runs a
      frame" was too coarse — a PC histogram over a 10 s run, resident
      `0x0271`, separates the two halves cleanly:

      | vector | value | executions in 10 s |
      |---|---|---|
      | `Core8kRoutine` `DM(0x3FB3)` | `0x15DD` | **24,960** |
      | `CoreRoutine` `DM(0x3FB8)` | `0x3CBA` | **0** |

      `DM(0x0663)` sits behind `PM 0x3CBA`, which is why the frame counter
      never moved. Both vectors are correct and stable from sample 160 — read
      out of the capture's own database window, no extra run needed — so this
      is not a stale vector. The page is spending its frames in its 8 kHz
      filter chain (`PM 0x16FC..0x1705`, `PM 0x17AA/0x17B5`, 15.9 M
      executions) and its symbol-rate routine is simply never called.
    * **✗ And it is not the page's symbol-rate configuration.** From the same
      capture: `Samplerate DM(0x3F66) = 7` and `Samplebuffersize DM(0x3F67) =
      6` are published from sample 160 — code 7 is a 14,400 Hz codec and 6
      buffers give 2,400 baud, which is V.22's symbol rate and correct.
      `Symbolrate DM(0x3F65)` is **`0x0000` for the entire call**, including
      after the page starts working at 13.68 s and through data mode at
      17.6 s, so it is not the gate either. The configuration is right from the
      start; the dispatch is missing.
    * **✗ `EICON_CONTINUE_NON_IDLE=0x0271` does not help** — the caller still
      starts at 13.620 s. The page is not being suspended mid-frame and
      abandoned, which is what that knob is for; its symbol-rate entry is not
      being made at all.
    * **⚑ Which makes all three of this session's blockers one shape.** The
      caller's page is never dispatched; V.34's `0x00b0` scheduler stops being
      dispatched; V.90A's `V8.ANA` never returns so its frames truncate. Those
      are one subject — the harness's frame dispatch — and it is
      `harness-execution-plan.md`'s.
    * **✅ And the call site is found, with the clock block dead around it.**
      `CoreRoutine` is invoked from **`PM 0x1D27/0x1D28`**, in the page's own
      image, not the kernel's — neither the PRI kernel nor TIKRNL references
      `DM(0x3FB8)` at all:

          1d0e: AR  = DM($3760)          ; sample-buffer accumulator
          1d0f: AY0 = DM($3F67)          ; Samplebuffersize
          1d10: AR  = AR - AY0
          1d11: IF GE CALL $1D25         ; -> 1d27: I4 = DM($3FB8); CALL (I4)

      So the symbol clock is an accumulator: `DM(0x3760)` gathers
      `DM(0x3754)` = 15 per tick at `PM 0x1D1B` and fires `CoreRoutine` when it
      reaches `Samplebuffersize`. **`PM 0x1D08..0x1D16` has zero executions in
      the 10 s histogram** — so the accumulator is never even *evaluated*. The
      clock is not failing to fire; the block that would fire it is not run.
      (`PM 0x1D5E..0x1D68`, the register-init routine below it, runs exactly
      once, which is the control that says the histogram does see this region.)
    * **✅ ANSWERED, at instruction level with execution counts as the
      evidence. The caller's page parks itself on a frozen countdown, and
      emits silence while it does.** `Core8kRoutine` `PM 0x15DD` gates the
      whole frame, and the originate arm gates the gate. All 24,960 passes in
      the 10 s window take one path — the counts are identical at every step,
      so there is no other:

          15e1: AY0 = DM($3EE1)     ; GEN_setup1              24,960
          15e2: AR  = $0008         ; bit 3 = CH, originate   24,960
          15e4: IF NE JUMP $15EC    ; taken -> originate arm  24,960
          15ec: AY0 = DM($3811)     ; the frame gate          24,960
          15ee: IF NOT AC JUMP $15F7; NOT taken -- 0x15F7 = 0 executions
          15ef: AX0 = DM($3883)
          15f1: AF  = AX0 AND $0020 ; bit 5
          15f2: IF EQ JUMP $15F4    ; taken -> skips the store 24,960
          15f4: I5  = DM($3FB4)     ; ShellOutptr
          15f5: DM(I5,M4) = $0000   ; publish silence         24,960
          15f6: JUMP $16A2          ; abandon the frame       24,960

      Read it off: while `DM(0x3811)` is non-zero the page **publishes a zero
      line sample and abandons the frame**, and on the originate arm
      `DM(0x3811)` is only decremented when **bit 5 of `DM(0x3883)`** is set —
      which it never is. **So the countdown is frozen and the page is parked**,
      transmitting digital silence, which is also why the answerer hears
      nothing and falls back. The answering arm at `PM 0x15E5` decrements
      unconditionally, which is why only the caller shows this.
    * **⚑ And that is the same shape as `DM(0x20EF)` bit 11, one page over.**
      A status bit that gates everything, that no code in the running
      configuration sets. `DM(0x3811)` is loaded as `0x0000` by the `0x0271`
      overlay — which by this code means "run immediately" — so something sets
      it non-zero at runtime and then nothing clears it. **Next: who writes
      `DM(0x3811)`, and who is supposed to set bit 5 of `DM(0x3883)`.** Both
      are ordinary write-watch questions now, and `PM 0x15F7`'s execution count
      is the control: it is 0 while parked and must become non-zero.

    Note the watch itself perturbs this: a 20 s run with
    `--watch-dm-writes 0x03ef:4` never started the script at all, where
    unwatched 50 s runs start it at 13.6 s. Whatever instrument goes on this
    next has to be one that does not move the timeline — §0.4's control
    problem, in its most literal form.
  - **↔ So the answer to "does the V.90A work apply to V.34" is yes, at the
    mechanism and not at the fix.** The two blockers are different faults —
    V.90A waits on a status bit no host supplies, V.34's scheduler stops — but
    all three pages carry the *same* config-block-with-shadow-compare
    construct, and both blockers were found the same way: decode the record
    table, resolve the state's condition through `DM(0x064B)`, then read the
    handler the changed config word dispatches. That method is
    `tools/record_table_decode.py` plus the table above, and it is what
    transfers.
  - **The V.34 page runs the same machine, and the decoder reads it.** The
    unpacker is byte-identical in V.34 (`PM 0x2E24`), V.90D (`PM 0x2FE4`) and
    V.90A (`PM 0x33DD`) — same six shifts, same `MR1 = 0x0019` terminator — so
    the same tool decodes all three. V.34's main chain is 60 records from
    `0x1A2E` and covers `0050 … 0090 0092 0094 0096 … 00b0 00b2 00b4 00b6 00b8
    00c0 … 00d0 00ea 00bb 00bd`, which is the TrnProgress vocabulary this file
    has been quoting for fifty sessions. **§2's V.34 blocker — the answerer's
    transmit halting at `0x00b0` — now has a record to read**: `0x1C95`, nine
    entries, with `0x00b2` at `0x1CB0` behind it. That is where a V.34 session
    should start rather than in the emulator.
  - **⚑ (Superseded heading, kept for its measurements) the V.90 APCM page
    expects V.34 to have run first.** V90.ANA and
    V34.ANA are **alternative pages, not layers** — 24 of their PM blocks
    overlap — and at the very address that writes the blocking counter they
    hold different code:

        PM 0x2468   V34.ANA   DM($21E6) = AX1      ; the writer
                    V90.ANA   CALL $244E           ; something else entirely

    So V90.ANA does not merely fail to increment `DM(0x21E6)`; it has replaced
    the code that would, which is only sensible if the count is **inherited**,
    already standing when the page is entered. `DM(0x21E6)` is cleared by
    V90.ANA's own init at `PM 0x2622` — so what it wants is a value produced
    *during its own residency* by something else, or a page entry that does not
    run that init. Either way the same reading follows for `DM(0x20EF)`.

    On a real V.90 analogue call the modem trains its **upstream** with V.34
    Phase 3/4 and uses V.90 only for the downstream — this file's own §2 says
    V.90A is "queued behind V.34 phase 2". Our caller goes V.8 → INFO →
    **page 13 V.90 APCM directly**, never loading V34.ANA at all, and it is
    the firmware that asks for page 13, at 9.33 s.

    **That is the thing to test next, and it is a page-sequence question, not
    a missing-writer one**: find what the caller's page request is based on
    after INFO, and whether a real analogue V.90 client takes the V.34 page
    first. `EICON_RELAY_UNDER` cannot answer it — laying V.34 under V.90 is
    provably useless here, because V.90 owns `PM 0x2468` and overwrites the
    writer. The evidence for it is structural rather than measured, so treat it
    as §0.5 says: a thing to establish, not a thing expected to be true.
  - Nothing in `addsp_database.md` or the ADDSP V.90 guide names `DM(0x20EF)`
    or `DM(0x21E6)`: they are internal state-block words, not database
    offsets, so "derive them from the guide" has no source to derive from.
    That leaves the driver, or a calling-side backend that runs the real MIPS
    firmware — and the second is a profiling job first (`e8b0e82`'s note: the
    calling tower costs ~2,127 ms per 20 ms tick, against the answering
    tower's ~0.04x, and `mips_interval` is the first thing to look at). That is now the whole blocker, and it is the one
    place this project has no ground truth for — §5's "never originated a
    call" applies precisely here. Next: find what owns `DM(0x20EF)`. It is not
    V90.ANA, so ask which image in the analog109 set writes it at all, the way
    the `0x19D7` question was asked — and this time the answer will be about
    data memory, which is the space these machines actually use.
  - **⚠ One reading here was void and is withdrawn.** The filter state
    `DM 0x211E-0x212B` "is all zero" was dumped by `EICON_DUMP_DM`, which
    fires on the *first* frame of residency — before the page had run. Zero
    there proves nothing. `EICON_DUMP_PM`/`EICON_DUMP_DM` both trigger at
    first residency by design (they exist to compare two backends at the same
    instant); anything about steady state needs a watch, not a dump.
  - **⚑ What the loop is: the record cursor has run off the end of loaded PM.**
    *(Withdrawn — see the entry immediately above. Kept for the measurements
    it records, not for its conclusion.)*
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

    *The attribution in that last sentence is withdrawn* — the page-14 staging
    hypothesis was disproved (bb3dd63) and its last open word spent (1631192).
    The park itself, and the `PM 0x3492` derivation above, still stand. What
    replaces the attribution is the next entry.
  - **⚑ V90A has a record-stream staging hole of its own, and it is the V.34
    defect's shape.** The V.34 blocker was traced to script-block field `0x00`
    being the transmit mode — "the transmit mode is a constant in the script
    block the sequencer is currently executing", so a page that goes silent is
    a page executing a block that *says* silence
    (`analysis/05-data-path-and-modulation-selection.md`, Session 163). That
    makes "which block is the sequencer actually reading" the question for any
    page that stalls, V90A included.

    Tracing the caller with `--trace-v90a-state`, the record cursor
    `DM(0x120E)` walks:

    ```
    0x19f2 (0050) 0x16c8 0x16d4 0x16e3 0x16fe 0x1746 0x1752 0x1767
    0x177c 0x1788 0x1794 0x17a0 0x17ac 0x17c7 0x17cd (0092, parks)
    ```

    Against `026b-v90.ana-apcm-overlay`'s own PM blocks —
    `0x1661-0x16dc`, `0x1706-0x1763`, `0x1764-0x17b5`, `0x17c4-0x17d9` —
    **the records for states `0x0053` (`0x16e3`) and `0x0054` (`0x16fe`) are
    both inside the 41-word gap `0x16dd-0x1705`, which V90.ANA does not
    load.**

    And the live content there belongs to nothing in the firmware. Dumping the
    caller's `PM 0x1661-0x17d9` at page-13 residency (9.346 s) gives 376 of 377
    words non-zero, so it is not an unloaded hole — but comparing those 41
    words against **every** image in the analog109 set that covers the range
    (`0x0266`, `0x026d` V.OWN, `0x0273`, `0x02c0`, `0x02c7`) gives **0/41
    matching any of them**. It is runtime-written data from some other page:
    the words read `fdad10 faa010 f78a10 f48410 …` and `4f0410 55ee10 5c1f10
    617a10 …`, smooth monotonic 16-bit values with a constant `0x10` low byte,
    i.e. a computed coefficient table, not a packed record stream.

    So at states `0x0053` and `0x0054` the V90A sequencer is unpacking records
    out of another page's coefficient scratch. Contrast the other gap it
    crosses, `0x17b6-0x17c3`: there the live words are **13/13 identical** to
    V8.ANA, INFO.ANA, V34.ANA, INFOH and HV34, i.e. a genuinely shared block
    that the previous page is *supposed* to leave behind. The `0x16dd-0x1705`
    gap has no such owner.

    Two cautions before this is chased:

    * The parked record itself (`0x17cd`) is **not** in a gap — it is inside
      the loaded `0x17c4-0x17d9` block, and its `PM 0x3492` test slot is
      genuine firmware. The park is a real instruction to wait; the question
      this finding raises is whether the machine should have been in `0x0092`
      at all, having passed through two states it mis-read.
    * The entry cursor `0x19f2` is outside every V90.ANA block (only V34.ANA
      and SIG own that address, and this call loads neither), but it appears
      once, at the first traced sample, and is replaced by `0x16c8` two samples
      later. Treat it as a stale pre-init read, not as a record the machine
      executed, unless a write-watch says otherwise.

    **The measurement that decides it**: watch writes to `PM 0x16dd-0x1705`
    across the whole call and name the page that fills it. If it is INFO.ANA
    scratch, the fix is staging order; if V90.ANA itself computes it, the
    region is not a record stream at all and this is a coincidence of
    addresses.
  - **✅ Measured, and it is the second answer: V90.ANA fills those words
    itself, at its own page entry.** `EICON_WATCH_PM_WRITES=<lo>:<hi>[,...]`
    (new, `dial_tikrnl_drive`) arms the core's PM write watch on the direct
    backend, which had never armed it — so "nothing writes that PM word" had
    never been measurable on this side. Watching `PM 0x1600-0x1800` for a whole
    V90A call gives four fills, each **165 words, `PM 0x1661-0x1705`
    contiguous**, all from the same store, plus two boot zero-fills of
    `0x1600-0x17ff` from TIKRNL `PM 0x0649`:

    | cyc | after sample | what was entering |
    |---|---|---|
    | 29,855 / 43,298 | boot | DIAL, then V.8 |
    | 80,498,203 | 46,400 (5.80 s) | INFO.ANA |
    | 123,396,534 | 76,320 (9.54 s) | **V90.ANA** |

    The writer is `PM 0x17CE`, a DM→PM block copy called from the page-entry
    chain at `PM 0x17C8`, and it is byte-identical in V8.ANA, INFO.ANA,
    V34.ANA, DIAL, V90.ANA, INFOH and HV34:

        17ce  I0 = $3675              ; DM source
        17cf  I7 = $1661              ; PM destination
        17d0  DM($3756) = I7          ; published cursor
        17d1  CNTR = DM($3757)        ; length
        17d2  DO $17D4 UNTIL NOT CE
        17d3  AR = DM(I0,M1)
        17d4  PM(I7,M5) = AR          ; <- all 165 writes

    So the 41 words are not another page's scratch and there is nothing to fix
    in staging order. **The constant `0x10` low byte that read as a coefficient
    tag is the `PX` latch**: a 16-bit DM word stored into a 24-bit PM location
    leaves `PX` in the low eight bits, so a constant low byte is the signature
    of this copy. `acce9cb`'s attribution is withdrawn; its walk stands.
  - **And the length is the firmware's own, not the image's.** V90.ANA ships
    `DM(0x3757) = 0x014E` (334) — which would have reached `PM 0x17AE`, one
    past the last record the cursor visits, and made a short copy the obvious
    culprit. Write-watched, the word is **`0x00A5` (165) on every page entry**,
    written at `PM 0x1687/0x1688` — three writes, one per page, always the same
    value. That block is the rate loader this file already documents:
    `PM 0x167A` copies ten words from the bank `Samplerate` `DM(0x3F66)`
    selects into `DM(0x3754..0x375D)`, so `0x3756`/`0x3757` are two words of
    the rate block, and `PM 0x1689..0x168D` then publishes
    `DM(0x376E) = 0x1661 + DM(0x3757) - 1 = 0x1705`, the buffer's end pointer.
    So there is no *loader* defect — but note what the shape of it is, because
    it is this project's most-repeated defect class: **the copy's length lives
    in a word the rate loader overwrites**, and the value the image ships for
    it, 334, is exactly the length that would carry the buffer to `PM 0x17AE`
    — one word past `0x17ac`, the last cursor value before the walk reaches
    code. That is either a coincidence or the bug. It is one A/B:
    `EICON_ANALOG_PIN_DM=0x3757=0x014e@0x3fb0:0x000d` holds the image's value
    across page 13, and `EICON_WATCH_PM_WRITES=0x1700:0x17b5` is the control
    that says whether the copy actually got longer.
  - **⚑ Which moves the question, and sharpens it: the cursor leaves the buffer
    after state `0x0054` and reads instruction words for the rest of the call.**
    Only `0x16c8`, `0x16d4`, `0x16e3` and `0x16fe` — states `0x0050`-`0x0054` —
    are inside `0x1661-0x1705`. From `0x1746` (state `0x0060`) on, every cursor
    value is in statically loaded PM that the copy never touches, and a live
    `EICON_DUMP_PM` of `0x1661-0x17d9` at page-13 residency (9.35 s) shows that
    memory holding **code**: `0x17c4-0x17d9` is the copy routine above, word
    for word, and `0x17b6-0x17c3` — which V90.ANA does not load at all — is a
    code tail left by the previous page. The park's own record, `0x17cd`, is
    `0a000f`, an `RTS`.

    Nothing executes there, so this is the sequencer reading code as data
    rather than a mis-taken branch. Exec-watching the caller gated to overlay
    `0x026b`, with three positive controls in the same run (§0.4):

    | address | | executions |
    |---|---|---|
    | `PM 0x337b` | outer scheduler | 3 (control) |
    | `PM 0x33e7` | record unpacker | 6 (control) |
    | `PM 0x3492` | the park's test slot | 6 (control) |
    | **`PM 0x17c7`** | cursor, state `0x0076` | **0** |
    | **`PM 0x17cd`** | cursor, state `0x0092` | **0** |

    That reads as the calling-side twin of `2d75ca5` — the record cursor
    running off the end of loaded PM — **and it is wrong. See two entries
    below: the cursor addresses data memory, and none of this paragraph's PM
    is what the sequencer reads.** The exec counts stand as measured; the
    conclusion drawn from them does not.

  - **✅ Walked back, and the walk is fall-through: the cursor is never
    branched, it is advanced.** Write-watching `DM(0x120E)` gated to `0x026b`
    (17 writes, all `ov=0`) gives one initialisation and then the unpacker's
    own pointer, every time:

        33a9  I4 = DM($120E)
        33ac  CALL $33DD          ; the unpacker; I4 walks the record
        33ad  DM($120E) = I4      ; <- 15 of 17 writes

    The first write is `0x1689` from `PM 0x334e`, and it also catches two
    cursor values the sampled trace misses — `0x1719` and `0x1734` (the latter
    from `PM 0x338f`, the one genuine branch). So nothing mis-branches: the
    machine is handed a base and walks forward from it.
  - **⚑ And the base is a hard-coded immediate that points at memory V90.ANA
    does not stage.** `PM 0x3330..0x3348` is a rank of entry stubs, each
    loading a base pair into `MR0`/`MR1` before the common tail at `PM 0x334b`
    stores them to `DM(0x120E)` (record) and `DM(0x2127)` (iptr):

        3330  MR0 = $0050 ; DM($20F9) = MR0   ; state 0x0050
        3332  MR0 = $19D7 ; MR1 = $1689 ; JUMP $334B
        333c  MR0 = $19D7 ; MR1 = $1938 ; DM($21DC) = 0
        3341  DM($3F8A) = $5678 ; MR0 = $19D7 ; MR1 = $1938 ; DM($21DC) = 7
        3348  MR0 = $1689 ; MR1 = $1689 ; JUMP $334B

    Four of the five name **`PM 0x19D7`** as the record base — and the first
    cursor value the `--trace-v90a-state` trace ever printed is `0x19f2`,
    which is `0x19D7` plus one record. `acce9cb`'s caution that `0x19f2` is
    "a stale pre-init read" is therefore withdrawn: it is the intended base,
    one record in. What the live machine then runs on is the `0x3348` arm,
    which sets the record cursor to the *iptr* base `0x1689` and puts it inside
    the resampler buffer.

    **V90.ANA loads no PM block between `0x1850` and `0x1a80` at all.** The
    only images in the analog109 set that own `0x19D7` are **V34.ANA**
    (`0x19c9-0x19f5`), HV34, and the two SIG overlays. Dumped live at page-13
    residency, `PM 0x19d7-0x19f5` is **31/31 identical to SIG.A96** — download
    `0x0274`, laid at sample 0 and never displaced, because this call goes
    V.8 → INFO → V.90 APCM and never loads V34.ANA. Its first words read
    `0010 ffff 0001 0001 0021 0218 …` and include `0050` twice, so it has the
    shape of a record stream; it is simply the wrong page's.

    So the V.90 APCM page names a record base that only the V.34 page stages.
    **The hypothesis that follows — V90.ANA is a partial overlay meant to be
    layered over V34.ANA** — is the same shape as the PRI side needing
    `EICON_DSP_EXTRA_DOWNLOADS=0x026b` alongside V.34, and it is now testable:
    `EICON_RELAY_UNDER=<base>:<overlay>` (new, `dial_tikrnl_drive`) lays one
    base under one page, which is the per-overlay relay this file said did not
    exist. `EICON_RELAY_UNDER=0x0261:0x026b` is the experiment.
  - **✗ Run, and it is a negative — which the finding above predicts.**
    `EICON_RELAY_UNDER=0x0261:0x026b` on the caller lays V34.ANA under V90.ANA
    (`[relay-under] laid 0x0261 under 0x026b` confirms it applied) and the call
    is unchanged to the sample: same walk `19f2 16c8 16d4 16e3 16fe 1746 1752
    1767 177c 1788 1794 17a0 17ac 17c7 17cd`, same `0x0092` park at 12.56 s.
    Correct records at `PM 0x19D7` cannot help a machine that is not reading
    them: the live cursor comes from the `PM 0x3348` arm, which never uses that
    base. The knob stays — it is the per-overlay relay, and it is the right
    instrument for the next such question — but layering is not this bug.

    **So the question is now the arm, not the memory**: the first traced sample
    has `optr=0x19f2` *and* `state=0x0050`, so `PM 0x3330` ran first and
    consumed one record from `0x19D7`; something then routed to `PM 0x3348`,
    which reset the record cursor to `0x1689`. Exec-watch `PM 0x3330` and
    `PM 0x3348` gated to `0x026b` and read the `from=`/`ret=` fields to name
    what calls the second one.
  - **Done, and the arm is selected by a bit in `GEN_SETUP1`.** One execution
    of `PM 0x3348` in the whole page-13 residency, `from=256d ret=256e`, with
    `ax1=048c` — the calling `GEN_SETUP1` — and `sr0=0080`. `PM 0x2555..0x2576`
    is a generic bit-mask dispatcher: it builds a mask from `AX1 OR AY0`, walks
    a handler table with `AX0 = DM(I4,M5)`, and calls each handler whose bit is
    set (`I4 = AX0; CALL (I4)`). Bit 7 is the NORM/training bit that
    `DIAL_NORM_ENTRY` sets, and its handler is `PM 0x3348`. So the arm is not
    mis-taken: a calling data call in training selects it by design.
  - **⚠ Two A/Bs that were meant to test the PM story, and the second one
    breaks it.**

    1. **Extending the copy is a clean negative, with a live control.**
       `EICON_ANALOG_PIN_DM=0x3757=0x014e@0x3fb0:0x000d` holds the image's 334
       across page 13. It applied — `[analog-kernel] PINNED FIRMWARE STATE:
       DM(0x3757) = 0x014e first applied at sample 74765`, the page-13 entry
       sample, a report added for exactly this (§0.4) — and the copy really did
       get longer: `EICON_WATCH_PM_WRITES=0x1700:0x17b5` catches **182 distinct
       addresses** written where the unpinned run writes six. So `PM
       0x1706-0x17ae` was overwritten with the DM ring, under the cursor, and
       the walk did not move by one state.
    2. **Because the cursor is not a PM address.** `--watch-dm 0x1746:6` gated
       to `0x026b` catches `[WATCH] dm r 1746=101a pc=33e0 ov=0` — the
       unpacker's own `SR0 = DM(I4,M5)` at `PM 0x33e0` reading **data memory**.
       The disassembler is right and DAG2 is doing a data read, not a PM read.

    **So `DM(0x120E)` walks a record stream in DM, and every PM comparison
    made against those addresses — in `acce9cb` and above — is against the
    wrong memory.** Withdrawn on that basis: "the records for states `0x0053`
    and `0x0054` sit in a 41-word PM gap", "the cursor reads instruction words
    from `0x1746` on", and the whole `PM 0x19D7` line of attack, including its
    `EICON_RELAY_UNDER` negative — that experiment could not have worked. What
    survives is measurement, and it is worth keeping: `PM 0x16dd-0x1705` is
    filled by the page's own `PM 0x17CE` copy (so nothing was ever missing
    there), the copy's length comes from the rate block, the arm is chosen by
    `GEN_SETUP1` bit 7, and the walk is fall-through from `PM 0x33ad`.

  - **✅ Done, in DM, and the strand closes: the stream is perfect and the
    caller is walking it correctly.** `EICON_DUMP_DM=<lo>:<hi>:<path>` (new,
    the data-memory twin of `EICON_DUMP_PM`, same `EICON_WATCH_OVERLAY`
    trigger) dumps `DM 0x1600-0x17ff` at page-13 residency. Against V90.ANA's
    own `dm.words` over the 337 words `0x1689-0x17d9`: **0 differ.**

    And the stream reads exactly as the walk does. Every record starts with a
    mask word and carries its state in the high byte of the second:

    | record | | state | | record | | state |
    |---|---|---|---|---|---|---|
    | `0x1689` | `101b 5000` | `0x50` | | `0x1752` | `101b 645a` | `0x64` |
    | `0x16c8` | `101a 5208` | `0x52` | | `0x1767` | `1024 7002` | `0x70` |
    | `0x16d4` | `1006 5300` | `0x53` | | `0x177c` | `101a 71ba` | `0x71` |
    | `0x16e3` | `101a 540a` | `0x54` | | `0x1788` | `1004 7210` | `0x72` |
    | `0x16fe` | `100d 5600` | `0x56` | | `0x1794` | `1024 7302` | `0x73` |
    | `0x1719` | `100d 5800` | `0x58` | | `0x17a0` | `101a 75d5` | `0x75` |
    | `0x1734` | `1006 6000` | `0x60` | | `0x17ac` | `101a 7649` | `0x76` |
    | `0x1746` | `101a 6201` | `0x62` | | `0x17c7` | `1006 9220` | **`0x92`** |
    | | | | | `0x17cd` | `101a 9404` | `0x94` |

    That is the traced walk, record for record, in order, with the cursor one
    record ahead of the state as `PM 0x33ad` writes it back. **There is no
    staging hole, no corruption and no run-off.** `acce9cb`'s finding and every
    conclusion drawn from it here are withdrawn in full, and so is the reading
    that the sequencer runs on garbage: the V90A caller executes its own record
    stream faithfully and stops where that stream tells it to.

    **Which puts the blocker back where `c887fad` left it, and confirms it.**
    The record at `0x17c7` is state `0x0092` with `dwell=ffff` — no timeout —
    and its one test slot resolves to `PM 0x3492`, bit 11 of `DM(0x20EF)`,
    written once, as `0000`, by the record unpacker and by nothing else. The
    next record, `0x17cd`, is state `0x0094`, and it is one status bit away.
    So the calling side is not the defect. It is waiting, correctly, for a
    receive-derived bit, on a line where the digital end transmits nothing
    because **the V90D answerer is parked in its own `0x0060`**. One blocker,
    on the answering side, seen from both ends — and the answerer is where the
    next session's effort belongs.
  - **The current walk, for comparison with anything measured later.** Caller:
    V.8 at 0.001 s, INFO at 5.83 s, page 13 at 9.33 s, then `TrnProgress`
    `0x0050 0x0052 0x0053 0x0054 0x0060 0x0062 0x0064 0x0070 0x0071 0x0072
    0x0073 0x0075` and the `0x0092` park at 12.56 s. Answerer: `0x0060` at
    7.58 s, the park this file already records. Note `--seconds` on the
    loopback is wall clock, not audio: 20 s of it buys about 12.5 s of call on
    this machine, and 11 s does not reach page 13 at all.
  - **Fixes tried for the page-14 PM gap, both negative, both recorded so they
    are not retried:**

    1. **Call V90D's own entry point right after the download.**
       `EICON_OVERLAY_INIT=0x026a`. It does real work — non-zero words in
       `PM 0x1800-0x1bff` go 201 → 623 — but `0x18cc` stays zero and the call
       is unchanged.
    2. **Call it after the download handshake instead**, queued and run once
       `_run_and_serve` has resumed the task at `DM(0x31BB)`, so it executes in
       the frame context the card would give it. Identical result: same walk,
       same loop, same fallback at 12.38 s.
    3. **Re-lay the base image under every page switch** (`EICON_RELAY_BASE=0x026d`).
       No change.

  - **Why none of them could have worked, and where the content actually comes
    from.** No download in the extracted set writes `PM 0x18cb-0x18ff` at all —
    scanning all 82 extracted images, the only ones with any word in that range
    are the RTP/G.7xx voice overlays. So the bytes `run48` has there do not
    come from layering `0x026a`. The tower reaches them because it stages the
    DSP through `build_dsp_code_image(dspdload.bin, …)` and lets the **MIPS
    firmware perform the downloads**, which is a different and fuller path than
    `load_adsp_module`'s per-directory layering that both backends otherwise
    share. **That is the thing to reproduce**: dump the tower's `PM
    0x1800-0x1bff` at the moment page 14 becomes resident and diff it against
    the direct backend's, then find which driver-side step produces the
    difference.
  - **⚠ The diff is in, and it disproves the PM-gap hypothesis.**
    `EICON_DUMP_PM=<lo>:<hi>:<path>` (new) writes PM once, the first time the
    overlay named by `EICON_WATCH_OVERLAY` becomes resident, so the same range
    can be taken from both backends at the same moment. Direct backend at
    7.501 s, tower at 23.549 s, both on overlay `0x026a`:

    ```
    direct non-zero 966   tower non-zero 966   differing words 7
    ```

    **`PM 0x18cc` is zeros on the tower too**, byte for byte the same as the
    direct backend. So "the direct backend fails to stage `PM 0x18cb-0x1bff`"
    is **wrong** — neither backend stages it, and the tower connects anyway.
    Withdrawn. Whatever lets `run48` walk `0x18cc → 0x18d8 → 0x18e7 → 0x1902`
    must write those records *during* page-14 operation, not at staging time.
  - **What the two backends actually differ by: seven words.** Six are operand
    differences within otherwise identical instructions, and one is control
    flow:

    | PM | direct | tower |
    |---|---|---|
    | `0x1808` | `371801` | `371081` |
    | `0x18b4` | `832f2a` | `82f82a` |
    | `0x18b9`, `0x18bd`, `0x18c1`, `0x18c5` | `8f2f10` | `8ef810` |
    | **`0x19c8`** | **`19900f` (`JUMP $1990`)** | **`0a000f` (`RTS`)** |

    `0x19c8` is the last word of V90D's 201-word `attributes=7` block
    (`0x1900-0x19c8`), and the five `8f2f10`/`8ef810` pairs are the same
    instruction pointing at `DM(0x2F1x)` versus `DM(0x2F8x)`. **That is the
    next thing to chase**: a one-word control-flow difference at the end of the
    block, on the backend that does not connect, sitting in the middle of the
    record-stream region the machine walks.
  - **✗ Chased, and `0x19c8` is a negative.** `EICON_PATCH_PM=<addr>:<word>:<overlay>`
    now exists on the direct backend too (`dial_tikrnl_drive.PATCH_PM`, applied
    in `download_overlay` right after the image is laid), so the tower's word
    can be staged on the backend that does not connect. Giving the direct
    answerer the tower's `0a000f` (`RTS`):

    ```
    EICON_PATCH_PM=0x19c8:0x0a000f:0x026a
    ```

    changes **nothing**. The patch is confirmed applied at download time
    (`[patch-pm] PM 0x19c8 0x19900f -> 0x0a000f`), and the answerer's whole
    `TrnProgress` transition list is byte-identical to the unpatched run: same
    page 14 at 7.560 s, same `0x004f → 0x0060 → 0x0060 → 0x0024`, same fall
    back to page 7 at 12.480 s. The one control-flow word the two backends
    disagree on is not the blocker, and the remaining six are operand
    differences on the same instructions. **The seven-word diff is now fully
    spent** — do not re-open it without a new reason.

    Positive control: the same run without the variable produces the same
    timeline, and the `[patch-pm]` line is absent, so the "no change" is not a
    patch that failed to apply.
  - **Re-derived independently, and two corrections to the range above.**
    Dumping the direct answerer's `PM 0x18cb-0x1bff` at page-14 residency
    (7.553 s) gives **765 of 821 words non-zero**. The hole is not
    `0x18cb-0x1bff`; it is exactly **`0x18cb-0x18ff` (53 words)** plus three
    words at `0x1b7d-0x1b7f`. Everything from `0x1900` up is populated without
    any `EICON_OVERLAY_INIT`, so the "201 → 623 words" figure that motivated
    that variable is stale.

    And the reason `0x18cb-0x18ff` is empty is structural, not a loader bug:
    **overlay `0x026a`'s own PM blocks jump from `0x1300+0x279 = 0x1579`
    straight to `0x1900`**, and scanning every extracted image — all the modem
    overlays, `0x026d` V.OWN, `0x0270` SIG, the kernel and the TIKRNL task —
    the only two that contain any word in that range are the RTP voice
    overlays `0x02c0`/`0x02c1`, which a modem call never loads. This agrees
    with the withdrawn-hypothesis entry above and gives it a mechanism.

    (`EICON_RELAY_BASE=0x02c0` is *not* the experiment it looks like:
    `RELAY_BASE` re-lays its bases before **every** download, so it lands under
    V.8 as well and the call drops to V.22 at 5.16 s. Testing that idea needs a
    per-overlay relay, which does not exist.)
  - **⚠ And the tower cannot currently arbitrate anything on page 14.** In the
    mixed rig (`--answerer-native-mips` + analogue kernel-dispatch caller) the
    tower answerer never leaves bootpage 6: `TrnProgress 0x0000`, `Rstatus
    0x0500[core|boot_request]` at 8.20 s, no overlay past `0x025f`. So "dump
    the tower's PM at the moment page 14 becomes resident and diff it" is not
    an instruction that can be followed inside the loopback — the tower diff
    already in this file came from a tower run that reached `0x026a` at
    23.549 s under a different configuration, and reproducing that is a
    prerequisite for any further tower/direct comparison.
  - **⚑ For V90A specifically, the blocker is that no caller backend does both
    halves.** `tools/v90_dpcm_replay.py`'s own docstring has said since Session
    50 that **the kernel-dispatch harness parks in `TrnProgress 0x0060` on page
    14 while the native tower walks `0x0060 -> 0x0062 -> …` exactly as the live
    card does** — harness choice, not firmware, decides V.90-page behaviour.
    Our caller runs kernel dispatch, i.e. the class already known to park on
    these pages. Measured on the calling side today:

    | caller backend | V.8 | V.90 page 13 |
    |---|---|---|
    | direct | ✗ no CM — codec 8000, ANSam detector never trips | — |
    | **kernel dispatch** | ✓ reaches page 13 at 7.4 s | **parks at `0x0092`** |
    | native tower | ✗ V.8 task loops, 142 truncated frames, 2.1 s per tick | — |

    A both-native run (420 s wall) confirmed the third row: the answerer got to
    17.46 s of audio, the caller never left sample 160. So the configuration
    the V.90 pages are known to need is exactly the one the calling side cannot
    boot.
  - **Two ways forward for V90A, in order of cost.**
    1. **Make the caller bootable on the native tower** — the V.8 task loop
       there is the same `PM 0x2024` `Norm_H & 0x0060` loop found at the top of
       this session, and `EICON_NORM_H_CALLING=0x0041` clears it on the direct
       backend. Try that combination first; it is one environment variable.
    2. **Take the loopback out of it entirely.** `run48` is our card answering
       a *real* analogue modem, so `run48.ulaw` is a genuine V90D transmission
       — precisely the signal a V90A caller must respond to — and
       `run48.rx.ulaw` is a real analogue modem's reply, the reference for what
       V90A should emit. Replaying the first into our caller tests V90A against
       a real peer with no synthetic answerer in the path, and diffing our
       caller's transmit against the second says whether it emits the right
       thing at all. That is the only V90A measurement available that does not
       depend on fixing the answerer first.
  - **Tried option 1: `EICON_NORM_H_CALLING=0x0041` on the caller's tower.
    Half a result.**

    **It fixes the loop.** Truncated frames on the calling tower go **142 → 0**
    — the `PM 0x2024` `Norm_H & 0x0060` loop is gone, exactly as it is on the
    direct backend. So the calling side *can* be booted on the tower after all,
    and that was the stated blocker.

    **But the analogue tower is computationally out of reach.** Steady
    **2,127 ms per 20 ms tick** — not a boot cost, a per-tick cost — which is
    0.16 s of audio for 300 s of wall clock. Page 13 arrives at 7.4 s of audio
    on kernel dispatch and later on the tower; at this rate that is days. For
    comparison the *answering* tower runs the same rig at about 0.04x.

    So the configuration V.90 needs is now reachable in principle and unusable
    in practice, and the next question on that path is a profiling one: why the
    calling tower costs a hundred times the answering tower per tick. `mips_interval`
    (default 160) is the first thing to look at.
  - **Which leaves option 2 as the only practical V90A measurement**, and it
    needs no tower: replay `run48.ulaw` — a genuine V90D transmission recorded
    while our card answered a real analogue modem — into the kernel-dispatch
    caller, and diff its reply against `run48.rx.ulaw`, a real analogue modem's
    reply to that same signal. That tests V90A against a real peer with no
    synthetic answerer anywhere in the path.
  - **⚑ You were right to look upstream: at the page-14 handoff we differ from
    a connecting call in exactly two negotiated words.** Diffing the CSV row
    where each side first shows bootpage `0x000e` — `run48` (connects) at
    12.02 s against this session's loopback answerer at 7.64 s, across all 33
    INFO/V.8/rate columns:

    | column | run48 | loopback | |
    |---|---|---|---|
    | `v8_line_result` `DM(0x3FC4)` | **`0xa100`** | **`0x8100`** | bit 13 |
    | `baud_info` | **`0x3064`** | **`0x306c`** | bits 3:2 |
    | `rtdelay` | `0x0030` | `0x0023` | measured, expected to differ |
    | `dil_measure` | `0x5243` | `0x711d` | measured, expected to differ |

    **29 of 33 identical.** The two that matter are both *negotiated*, not
    measured. `DM(0x3FC4)` is the V.8 classifier's input (`addsp_database.md`:
    "the V.8 classifier's input", read offset 0xE4, reserved in the guide) — so
    page 14 is being entered with a V.8 result a real call does not produce.
    The page-selection mask `& 0x0016` is 0 for both, which is why the same
    page still loads; everything else the word carries differs.

    Note the family resemblance to the `Norm_L` finding already in this file:
    live calls supply `0xA13F` where the shim defaulted to `0xB13F`. Here the
    live V.8 result is `0xA100` and ours is `0x8100`. **Chase these two words
    back to what our caller advertises.** That is upstream of the page-14 state
    machine, upstream of the `0x0060` loop, and upstream of everything else in
    this section.
  - **✅ And one database word moves it most of the way.** The loopback already
    has `--answerer-db-word`/`--caller-db-word`, and its own help names the
    case: `0x3f09:0xa13f`, "the NORM_L a live call gets from the card's own
    answer WDB instead of the shim's `0xB13F` default". Setting it on both ends
    and re-diffing at the page-14 handoff:

    | column | run48 | before | with `0x3f09:0xa13f` |
    |---|---|---|---|
    | `v8_line_result` | `0xa100` | `0x8100` | **`0xa10f`** |
    | `baud_info` | `0x3064` | `0x306c` | `0x306c` |
    | `dil_measure` | `0x5243` | `0x711d` | **`0x532d`** |

    The V.8 classifier input's top byte now matches a connecting call, and
    `dil_measure` lands near run48's. Page behaviour is unchanged — still
    page 14 at 7.78 s, still back to 7 at 12.64 s — but this is the first
    thing all session to move a *negotiated* word toward the live value.

    Two deltas remain: `v8_line_result` low byte `0x0f` against run48's `0x00`
    (we are carrying `0xa13f`'s low nibble into the classifier word), and
    `baud_info` bit 3, where we advertise a symbol rate run48 does not.
  - **Where the correct values should come from, rather than being poked in.**
    `docs/divas4linux-master/tty_module/mdm_msg.h` is the Linux driver's own
    header and carries the complete `DSP_CAI_MODEM_*` definitions — negotiation
    mode (`DSP_CAI_MODEM_NEGOTIATE_V8 = 0x04`), the per-modulation disable
    bits, guard tone, split speed, and the rest — and `tools/eicon_idi.py`
    already models that side. On a real card the MIPS firmware turns the CAI
    into the data-pump words; our direct and kernel-dispatch backends write the
    database themselves and that is exactly where the fidelity is lost.
    **Deriving `Norm_L`/`Norm_H` from the driver's CAI instead of from
    constants is the principled fix**, and `0x3f09:0xa13f` above is evidence
    that theit changes is the right one.
  - **✅ Done properly: `Norm_L`/`Norm_H` are now derived from the CAI.**
    `eicon_idi.norm_l_from_cai()` / `norm_h_from_cai()` build both words from
    the guide's write-database bit maps (offsets 0x29 and 0x28) and the
    driver's own `DSP_CAI_MODEM_DISABLE_*` mask, and
    `dial_tikrnl_drive.configure_modem` uses them instead of the constants it
    carried. Two checks that the rule is the right one, not a curve fit:

    - `norm_l_from_cai()` with nothing disabled returns **`0xA13F`** — exactly
      run48's live `Norm_L`. The constant it replaces was `0x8100`, V.90 and
      V.34 only. Note the set deliberately excludes V32ext: `0xB13F` is this
      menu plus that bit and is what the shim defaulted to.
    - `norm_h_from_cai('answer')` returns **`0x0021`**, the hardware-traced
      answering value.

    Re-diffed at the page-14 handoff, with no `--db-word` anywhere:

    | column | run48 | before | CAI-derived |
    |---|---|---|---|
    | `v8_line_result` | `0xa100` | `0x8100` | **`0xa10f`** |
    | `dil_measure` | `0x5243` | `0x711d` | **`0x532d`** |
    | `baud_info` | `0x3064` | `0x306c` | `0x306c` |

  - **⚠ And the derivation caught one of my own mistakes on the way in.** The
    first version treated `Norm_H` bits 5-6 as a *role* field and returned
    `0x0041` for calling. Measured: the caller then goes bootpage 6 → 10 →
    17 DIAL and the answerer never leaves INFO — which is 40418ef's own
    `0x0041` row. They are not a role field, they are the V.8 **call function**:
    bit 5 → CM `0x0103` (fax), bit 6 → `0x010B`, neither → `0x0107`, the data
    call that reaches V.90. So a calling data call sets neither, answering
    keeps `0x20` because it is what makes the pump transmit ANSam, and
    `norm_h_from_cai` now takes `call_function` rather than inferring it.
  - **What is left of the handoff difference**, and it may not be ours to fix:
    `v8_line_result` low nibble `0x0f` against run48's `0x00`, and `baud_info`
    bit 3. Both are *negotiated* results rather than advertised menus — the low
    nibble is the low-speed family, which a real peer agrees away and our
    synthetic peer keeps offering. They are the right things to look at only
    after a peer that negotiates properly is in place.
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

**The V.90A caller's status words (251)**

- **V.34 running first does not supply them.** Session 250's structural reading —
  V90.ANA replaced the code that writes `DM(0x21E6)`, so the page must expect
  V.34 to have run — is now measured, on a run where the analogue caller falls
  back to page 8 and takes V.34 all the way to `0x00d0` twice. Across both
  complete V.34 handshakes `DM(0x20EB)` never leaves `0x0004` and `DM(0x20EF)`
  never leaves `0x1000`: **neither of the bits the V.90A page waits on is ever
  set by V.34**. What V.34 *does* leave is `DM(0x21E6)`, which it writes as
  one-hot status (`0x0010`, `0x1000`, `0xA000`, `0x4800`, …) rather than as a
  count — several of those exceed 1,200, so `PM 0x33F8`'s test is a threshold on
  a status word, not a counter reaching a target. Do not plan a V.34-then-V.90A
  page sequence on the strength of the status words; that half of the argument
  is disproved.

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
