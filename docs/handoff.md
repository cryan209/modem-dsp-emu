# Handoff: current state, live blockers, and what has been disproved

Written at Session 93 and updated through Session 188. The running log in `eicon_adsp_firmware_analysis.md` is
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

**Session 188n: V.32 reaches bootpage 2 with one word held.** Arm
`EICON_PIN_PM=0x3805=0x38ab00` on the answerer and the page stops abandoning:
`TrnProgress 0x0009 -> 0x0040`, `bootpage 6 V.8 -> 2 V.32` — the V.32 page is
the resident bootpage for the first time in this project — `DM(0x0571)` is never
set, and the DSP runs 108,131 cycles further. **Arm it for any V.32 work**;
several earlier V.32 experiments were measuring a page that abandoned before it
could answer the question. The stop that remains is a different and much later
one: `TrnProgress 0x0040`, `BaudInfo=0xac99`, `DI_control` asserting
`codec_clocking|sync`, then `bootpage 2 V.32 -> 11 AT offline` with
`Rstatus=0x9d28[online|ring_valid|core|boot_request|test|ring]`. See §188n for
what the pin does and does not establish — it proves causation, not that the
firmware's `PM 0x3805` rewrite is a defect.

**Session 188, read before touching V.32 or V.34.** The per-frame continuation
can now be delivered to a *non-idle* core (`EICON_CONTINUE_NON_IDLE=<overlay
id>`, off by default, armed per resident page). It is not the common fix
Session 187 predicted:

* **V.32 wants it.** With it armed for `0x0266` the page leaves the dead
  `TrnProgress 0x0000` stall and walks to `0x0009`, and the partial's frame
  handler `PM 0x3536` is entered for the first time. It still does not train,
  and **Session 188b has the reason, which is not a V.32 defect**: the LEC loop
  at `PM 0x1d8f` is entered once with the page image's shipped template tap
  count `DM(0x3754) = 0xfff4` (16,372 iterations) before the firmware's own init
  writes 9 into it. That one loop needs ~13.9 M cycles, so it spans hundreds of
  8 kHz frames; hundreds of SPORT ISRs nest inside it; the 4-deep counter and
  loop stacks saturate; `cntr_stack_push()` then drops pushes silently and the
  matching pops return stale counts, so `CNTR` freezes (observed sticking at
  `0x0010`) and **the loop can never expire**. Eight entries, zero exits, 20.6 M
  body iterations — which is the 224-distinct-PC shape against the V.22
  control's 3,619, not a runaway filter. Forcing a sane tap count proves the
  chain (224 → 1,045 PCs, loop enters and exits 18/18) but is not a fix: the
  firmware writes 6 before it writes 9, and pinning 9 makes the page fall back
  to DIAL without ever requesting the partial. Only the first link is
  V.32-specific; **any page whose loop outlives a frame is exposed to the rest.**
  **188c refines this**: all four SSTAT overflow bits now warn once per stack
  per card, and the order says the **PC stack saturates first** (`PM 0x2f58`,
  `pcsp=16`), with the loop and counter stacks following within 80 cycles at
  `PM 0x2e18`/`0x2e19`. None of that is the LEC, so the LEC loop is the victim
  and something nesting 16 deep through `0x2e18..0x2f58` is the cause. The
  warning is a clean discriminator — **0 on the working V.22 path, 4 on V.32,
  1 on V.34** — so `grep '\[STACK\]'` is now worth doing on every capture.
  **188d dumps the stack and settles it: there is no deep chain through
  `0x2e18`.** The 16 slots are `0773 1d0e 1d19 1d90 1d96` — one LEC suspended
  mid-loop, `0x1d90` being the outer `DO`'s own loop-top — plus `0773 …` again,
  the next frame's dispatch doing its ordinary 11-deep work on top of it.
  **5 + 11 = 16, and 2 + 2 = 4 on the loop stack.** Two frames sharing one stack
  because the first never returned; nothing recurses and nothing leaks. It
  happens **identically with CNI off** (same chain, ~800 cycles earlier), so the
  re-entry is the SPORT interrupt, not this harness. Only the tap-count window
  is V.32's — every step after it would follow for any page whose routine
  outlives a frame.
* **V.34 corrupts a stack too**, once, PC stack only, at `PM 0x2dc4`. Whether
  that relates to the `0x00b0` wall is **not established**; it is a lead.
* **188e closes the window and V.32 now reaches `TrnProgress 0x00d0`.** The
  damage all happened inside the page-load resume run: the page is resumed,
  posts bootpage 19 twenty-nine cycles later, and then runs on into the LEC for
  the rest of its allowance. Hardware's kernel completes a partial transfer
  inside the frame that asks for it, so the resume run now stops at the request
  and serves it there (`EICON_PARTIAL_STOP=0` restores the old behaviour). Stop
  on **`DM(0x3132)`, not the bootpage word** — the id is written last, and
  stopping earlier serves the resident page onto itself. Result: every LEC entry
  bounded at 9, **no stack overflows on either end**, and both ends walk to
  `0x00d0` (caller 7.20 s, answerer 5.66 s). **It still carries no data**, for
  the reason Session 184 already named: the pump attaches with the V.22bis
  width (`TX 4 bits/datagram`), so LAPM never establishes. The physical layer is
  no longer the blocker on this page — the datagram width is. Regressions
  checked: V.22 unchanged (IPCP, 3/3 pings), V.34 identical to its control
  (`0x00b0`, 49/40 transitions).
* **188f gave the pump a V.32 width, and it was not the blocker.** The width
  selection keyed on `resident == V22_OVERLAY`, and `0x0266` is resident for
  *both* modulations, so V.32 got V.22bis's 4 bits, its 2400 bit/s and its name
  in the log. `DM(0x3FB0)` discriminates them now (`EICON_V32_DATAGRAM_BITS`,
  default 6). Note the two pages do **not** share a symbol rate — V.22bis is 600
  baud × 4 bits, V.32/V.32bis are 2400 baud — so the rate is a per-page constant,
  not width × baud. **Sweeping every legal width 6..2 produced zero received
  frames and not one bad FCS**, and a wrong width garbles frames rather than
  removing them. The real gap: over a 40 s call reaching `0x00d0` the answerer
  writes `DI_control` **five times** and RXD0/RXD1 once each, where 2400 baud
  needs 2400 datagrams a second. **The V.32 page reaches the data state and then
  does not run its data interface.** Session 184's "modulation-agnostic apart
  from the width constant" is withdrawn.
* **188g: it never starts, and the page abandons.** The two modulations service
  `DI_control` from *disjoint* code — V.22 from `PM 0x3fc8..0x3ff2`, the
  per-datagram servicer (167,538 writes a call); page 2 from
  `PM 0x34d1..0x34f9`, and **`0x3fc8` is never entered while page 2 is
  resident**. All five of page 2's writes land within 40 cycles of each other:
  one initialisation burst. Then 6,235 cycles later the page writes bootpage 0
  from **`PM 0x36bc`** and falls back to DIAL. **`PM 0x36bc` is the abandon path
  and is the blocker**, ahead of anything in the data path. Caveat: the abandon
  is evidenced on a call that ended at `0x0009` rather than reaching `0x00d0`,
  so whether a `0x00d0` call abandons the same way is not established.
  **Run-to-run variance on this page is large — never compare V.32 measurements
  across runs; put them on one cycle axis in one call.**
* **188h: the condition is `DM(0x0571) != 0`.** `PM 0x36bb` is unconditional —
  set the page-ready bit in `DM(0x3FC1)`, write bootpage 0, return. The decision
  is at the top of the per-frame handler `PM 0x3536` (the word the partial puts
  in `DM(0x3fb8)`): `AX0 = DM($0571); AR = AX0 + 0; IF NE JUMP $36B7`. **Any
  non-zero value there makes the page skip its whole frame and ask for DIAL**,
  which is exactly why the data interface at `CALL $34C4` is initialised once
  and never serviced. `PM 0x2cfb` writes `0x18f3` into it 16,532 cycles before
  the abandon.
* **188i: `0x18f3` is not a status code.** `PM 0x2cfa` (not `0x2cfb`) is the last
  instruction of a sparse-record unpacker looping over `0x2cf1..0x2cfb`: it
  walks `(offset, value)` pairs from a source pointed at by `I4` and scatters
  them across a parameter block based at **`DM(0x054C)`**, stopping when the
  offset equals `MR1`. At the write, `MR0=0x054C` and the offset `AF=0x25`, so
  `DM(0x0571)` is simply **field 0x25 of that block** and `0x18f3` is the value
  out of the record. **The record source is `I4 = 0x1ae0`, inside the 222-word
  gap `0x1a22..0x1aff` that no DM block of `0x0266` or `0x0267` covers, and
  nothing writes there all call.** So the page abandons because a stale-record
  unpack scribbled on its parameter block — it is not deciding to give up.
* **188j settles it: the "record" is a cosine table, and the pointer is wrong.**
  `EICON_DM_DUMP` (new — DM had no dump facility) shows `DM(0x1ac3)` onward
  reading, every other word in Q15, as `+1.0000 +0.9951 +0.9807 … +0.0980
  +0.0000` — **cos(kπ/32), a 32-point quarter-cosine table**, continuing into
  the negative quadrant. The arithmetic closes exactly: sources `0x1add..0x1adf`
  are `2528 f3d1 18f9`, giving offset `0x2528>>8 = 0x25`, value
  `hi(18f9)<<8 | hi(f3d1) = 0x18f3`, destination `0x054C+0x25 = 0x0571` — all
  three as observed. So `0x18f3` is two high bytes of adjacent cosine
  coefficients glued together. **Not a missing download** (the memory is fully
  populated and regular, so Session 134's V.90A lever does not apply) and **not
  corruption** (a corrupt table would not give cos to four decimals across
  seventeen points). **`AX0` at `PM 0x2cee` is wrong, and it is the only thing
  wrong** — the scattered parameter block, the non-zero `DM(0x0571)`, the
  per-frame abandon, the five `DI_control` writes and the DIAL fallback all
  follow from that one register. Next: exec-watch `0x2cee`, read `ax0`, walk the
  trail back.
* **188k sources the pointer: `AX0` is `DM(0x05B7)`, and `PM 0x28C0` writes the
  wrong database into it.** `PM 0x2CEE` is a shared, resumable record-stream
  unpacker: `PM 0x2931` loads `AX0 = DM(0x05B7)` and walks with terminator
  `MR1 = 0x1F`; `PM 0x2CB9` loads `AX0 = DM(0x05B8)` and walks with `0x1A`. Both
  cursors are installed at **`PM 0x2CB0`** (`DM(0x05B7) = MR1`,
  `DM(0x05B8) = MR0`). The correct `MR1 = 0x0FCA` **is** installed, at
  cyc 78,801,956 from `PM 0x2C68` — and 8,701 cycles later `PM 0x28C0`'s
  `MR1 = $1081` overwrites it. `0x28BF` is reached by `CALL (I4)` at `PM 0x382A`
  through the handler table at `DM(0x0AE6..)`, and bits 2/7/10 resolve to
  `2c72`/`28bf`/`3888` on one base, so **the table is exactly aligned — this is
  not Session 115's overwritten-dispatch shape.** Ten of the eleven arms that
  reach `0x2CB0` set `MR1 = $0FCA`; `0x1081` appears three times elsewhere and
  always as `MR0`. Walked over a live DM dump, all eight `MR0` constants
  terminate at `0x1A` in 5–21 records and `0x0FCA` terminates at `0x1F` in 3,
  but **`0x1081` walked with `0x1F` never terminates** — 895 records and still
  running at `0x1AFE`, which is how it reaches the cosine table. **Still open:**
  `0x28BF` is well-formed firmware doing its declared job, so the defect is one
  step up — either bit 7 should not be set in the `DM(0x0550) XOR DM(0x0644)`
  mask built at `PM 0x378B..0x378E`, or the table at `DM(0x0AE6..)` belongs to a
  different overlay. Next: write-watch `0x0550` with an exec watch on `0x378E`,
  and check whether any `0x0266`/`0x0267` DM block covers `0x0AE6`.
* **188l closes the chain: `PM 0x3805` is self-modified, and the swap sends bit 7
  to the wrong handler.** `DM(0x0AE6)` **is** downloaded — `0x0266`'s DM block
  `0x0780..0x0f62` covers it, live matches shipped 16/16, and all nine install
  constants are in `0x0266`'s own blocks — so 188k's "different overlay" reading
  is dead. The dispatcher's mask is **GEN_SETUP1**: `PM 0x2C69` computes
  `DM(0x05C8) XOR DM(0x0647)` = `0x0484 XOR 0` = `0x0484` (bits 2, 7, 10), and
  `DM(0x05C8) = 0x0484` is the modulation-role word. Bit 7 is set in both roles,
  so nothing there is corrupt. What *is* wrong is the table base: the shipped
  `PM 0x3805` is `38ab00` (`I4 = $0AB0`) and the word that executes is `38ae60`
  (`I4 = $0AE6`), rewritten at cyc 78,801,924 by a five-instruction quine at
  **`PM 0x2909..0x290D`** that copies its own opcode over `PM 0x3805`. Confirmed
  three ways (`pm w 3805=38ae60 was=38ab00 ppc=290d`, `[EXEC] pc=3805
  op=38ae60`, `i4=0ae6` at `0x3821`). **This is the first confirmed
  self-modification in the project and does not revive Session 186's withdrawn
  `PM 0x1d8e` claim, which still stands withdrawn.** The two tables differ only
  at bits 6 and 7; **bit 7 ships as `PM 0x2C79`**, which sets the correct
  `MR1 = $0FCA` and *selects* `MR0` by probing `DM(0x05C0)`/`DM(0x05BE)`, and the
  patched table sends it to `PM 0x28BF`, which hardcodes the bad pair instead.
  Also measured: `DM(0x0550)` takes 46 of its 178 writes from the runaway
  unpacker itself (`PM 0x2CFA`, field `0x04`), and `DM(0x0644)` two more, so the
  runaway would keep re-triggering dispatches — but it begins 289 cycles *after*
  the bit-7 dispatch and is not the trigger. **Open, and now the whole blocker:**
  the live `PM 0x2909` is `38ae60` while `0x0266` ships `403008` there, so the
  patch instruction is itself not the shipped word. Next: `EICON_WATCH_PM=0x2909`
  for the writer, then A/B suppressing the patch.
* **188m answers that, and it is not the page writing its own code.**
  `EICON_WATCH_PM` on `0x2909..0x290D` shows all five words written at
  cyc 78,785,250 by a two-instruction loop at **`PM 0x1FBA/0x1FBB`**, `I5`
  walking the destination and `I4` the source — and the source is *program*
  memory: `[WATCH] pm r 0985=3b8053` is exactly the word that lands at
  `PM 0x290A`. So a five-word fragment ships at **`PM 0x0984..0x0988`**, below
  `0x2000` in the always-resident region, and `0x1FBA` is a PM→PM **trampoline
  installer** that stages it into the overlay window at page activation.
  `PM 0x3B83` zeroes all five words again at cyc 78,827,863. Every stage of the
  chain is shipped firmware or shipped data. **This weakens the "swapped
  constant" reading a lot** — a fragment parked in resident PM, trampolined in,
  used once and torn down is deliberate, and we are walking it as designed; the
  likelier reading is that the harness puts the page in a state the real card
  would not be in. Next: find which download supplies `PM 0x0984..0x0988`, then
  the one-run A/B — suppress the copy (or restore `PM 0x3805` to `I4 = $0AB0`)
  and see whether bit 7 reaches `PM 0x2C79`, `DM(0x05B7)` stays `0x0FCA`, and the
  abandon at `PM 0x353A` stops.
* **The host driver never touches the DSP, so it is not a suspect in any of
  this.** `pri_telindus_load()` (`kernel/s_pri.c:451`) opens `dspdload.bin`,
  `dsp_read_file()` (`divactrl/load/common/dsp_file.c:144`) picks the file set by
  card type and streams each download's DM/PM blocks into *card RAM*, and the
  driver then writes a dword count plus `t_dsp_portable_desc download_table[128]`
  (0x30 bytes each) at `DspCodeBaseAddr`. That is all: no IDMA path, no PM or DM
  write anywhere in the kernel tree. The card's MIPS image reads the table and
  drives the ADSP, which is exactly what `build_dsp_code_image()` plus
  `descriptors = {id: base + 4 + index*0x30}` already models. `kernel/dsp_defs.h`
  is the authoritative format reference and matches the extractor; note the file
  set is chosen **per card type**, so `EICON_DSP_EXTRA_DOWNLOADS` stands in for
  another card's file set rather than being a free-form lever.
* **188n runs the A/B, and it is positive on every prediction.** New lever
  `EICON_PIN_PM=ADDR=VALUE` holds a PM word against DSP stores inside
  `WWORD_PGM()`, which `EICON_FORCE_DM` cannot do because it writes at
  overlay-load time — 14,000 cycles too early here. It reports its hit count at
  exit (`[pin-pm] … N stores undone`), because a pin that never fires makes the
  run identical to the control and an unchanged result would mean nothing. It
  fired once, at `ppc=290d cyc=78801924`. With `PM 0x3805` held at `I4 = $0AB0`:
  the dispatcher table is `0x0AB0`, bit 7 reaches **`PM 0x2C79`**, which sets
  `MR1 = $0FCA` *and selects* `MR0 = 0x1081` (so `0x1081` was always a good base
  in the wrong slot); both unpacks are correctly paired and terminate, the
  `0x0FCA` walk ending at `0x0FD3` exactly where 188k's static walk said;
  `DM(0x0571)` is never written in the V.32 window; and the page takes bootpage
  2. **This proves causation, not that the rewrite is a defect** — every stage of
  the chain is shipped firmware (188m), so "the harness has the page in a state
  the real card would not be in" is still the likelier reading.
* **⚠ Two measurement traps, both hit in 188l.** A `--watch-exec` limit is spent
  by *earlier pages at the same PM address*: `0x3805:400` reported zero hits in
  the V.32 window while actually executing there, because 400 earlier-page hits
  used the budget. Always check hit count against the limit before reading a
  silent watch as "never runs". And an **exit-time `EICON_PM_DUMP` can be wrong
  even when correctly gated** — it prints `(resident overlay 0x0267)` and still
  reads the shipped `38ab00` at `0x3805`, because the page falls back and
  `0x0262` loads over PM before exit. The `op=` field of an `[EXEC]` line is the
  only ground truth for a PM word.
* **⚠ Session 185's "the partial `0x0267` is seven DM blocks and no PM at all"
  is WRONG.** `0x0267` rewrites program memory: `PM 0x36bb` is `804dd0` in a
  dump gated on `@0x0266` and `93fb0a` — the opcode actually executed — in one
  gated on `@0x0267`. **Any page-2 disassembly taken at `0x0266`-load time is of
  the pre-partial image and is wrong.** Use `EICON_PM_DUMP=...@0x0267`, and
  check it against the `op=` field of an `[EXEC]` line, which is ground truth.
* **V.34 does not.** Armed for `0x0261` it is a regression: the control reaches
  `0x00b0` on both ends, and with the flag the caller stops at `0x0060` and the
  answerer at `0x0090`, cycling 3–4× as much. V.34 already has its own
  per-sample discipline (`V34_PUBLISH_PACED` + `yield_on_stop`) and this
  competes with it. **Keep the flag page-scoped and off for `0x0261`.**
* Arming it for *every* page (`=1`) drops the call to DIAL at 0.54 s: V.8 spans
  budgets deliberately and its context must survive.

So `0x00b0` and V.32's silence are **not** one blocker, and 187's claim that
they were is withdrawn.

These blockers are live:

| blocker | status | where |
|---|---|---|
| **the INFO message's first 16 symbols decode to `0x2000`** | **retired as a receive fault**; `tools/v34_info.py` decodes the wire independently and the peer really does send zeros in bits 6..12, so `DM(0x3F89) = 0` is a correct decode. The V.34 originate stall is real but is not a demodulator, framer or length defect | Sessions 102–104, **114** |
| **the answering page stops publishing transmit data at `0x00b0`** | **the live V.34 blocker (164).** Post-pacing-fix both ends reach `0x00b0` -- answerer 10.10 s, caller 9.54 s -- through twenty states. There the answerer's transmit chain halts completely: last ring write 95.81 Mcyc of a 60 s run, no further `DM(0x224C)` requests, line frozen on one sample for 36 s. The caller waits 0.76 s, falls back to `0x0024`/`0x002c` and restarts V.8/INFO for the rest of the call. **The `0x0060`/`0x0090` ceilings are gone and Sessions 137-148 describe a regime that no longer exists** -- do not carry their wait-block, threshold or role-word findings forward. The quiet stretch at `0x00a0..0x00ac` is a designed six-state sequence (script field 0x00 = `0xa700`), not a fault | Sessions 149, **164** |
| **the page-8 transmitter is decimated by ten** | **FIXED (149).** A fixed instruction budget on a page that never idles let it publish `DM(0x3764)` 9-12 times per 8 kHz tick against the one the harness consumed, so the line carried an aliased tenth of a real waveform: spectral concentration 0.097 against 0.818 for a live modem. `EICON_V34_PUBLISH_PACED` (default on) stops the run at the publish -- 1.00/sample. Both ends leave the `0x0060`/`0x0090` ceilings for `0x00b0`, page 8 stops cycling, and the caller transmits for the first time (RMS 5.0 -> 776.6), which retires 137's "the calling side transmits nothing". **The publish rate is fixed; the signal is not** -- 150 withdraws 149's claim that the paced output matches hardware, which was the metric scoring a stuck DC level. In-passband both ends are 0.071/0.081 against hardware's 0.818 | Sessions 138, 145-148, 149, **150** |
| **the V.34 page freezes** | **FIXED (115j–l).** Cause: the native bulk worker at PM `0x1930`/`0x1934` overwrote the read-database dispatch table at `DM(0x00A8..0x00A9)`, so `CALL (I7)` at PM `0x2725` entered a scan loop at `0x2e1c` instead of `0x2e1a`, skipping `AY0 = $00FF`, and spun for 99.7% of the call. `EICON_V34_PORTABLE_BULK` (now **default on**) holds the worker and serves the guide's delay-line ABI instead. Freeze gone in 3/3 calls; PCs 59 → ~7,470; PM `0x0771` 0 → ~700 k. **New state: cycling, not freezing.** `0x0090` is *not* a stall — it is reached 11–12 times per call and always falls back to `0x0020`/`0x0024`. The trail `0x004f → 0x0070 → 0x0072 → 0x0074 → 0x0090` **skips `0x0076` and `0x0080..0x0086`**, which Session 102 identified as the answerer advancing **on timers, not on received signal**. Captures show both ends transmitting throughout (RX 120–1280 RMS, cycling with the restarts), so **the peer sends phase-3 training and the card does not detect it** — a receiver question, posable for the first time. **115n:** there is **no `0x0076` or `0x0074` block** — the answering script (base `0x1e81`, 16 blocks) publishes only `0x0000/0x0020/0x0050/0x0060/0x0070/0x0080/0x0090/0x00a0/0x00d0` plus `0x0020/0x0030/0x0040/0x00df/0x00e0`, so `0x0071/0x0072/0x0074` are **sub-states inside block `0x0070`** (`0x1ed5`). State field is `0x1b`; answering fields sit `0x0b` above the calling ones. The live trail also skips the `0x0080` block (`0x1eed`) entirely. Next: resolve `0x1ed5`'s tests/branches (`0x20/0x21/0x22 = 0x001c/0x0012/0x0000`, `0x1c = 0x001e`) through the `0x064B`/`0x0676` tables per 114j, then `--watch-exec` which fires | Sessions 76–79, 114b–z, **115j–m** |
| **neither loopback endpoint holds real time once page 8 is resident** | **largely retired (135).** True in absolute terms — 0.82-0.93x over 90 s, not 0.65x — but the two ends stay within 0.10 s of each other, end on the same sample, and drop nothing, so loopback timing between them is sound. Only comparisons against a real-time third party are affected | Sessions 100, **135** |
| **V.34 has never been tried against hardware since the tree changed** | **closed.** Two live forced-V.34 calls placed in Session 114c; both loaded overlay `0x0261` and both froze. `tools/cx_at.py` is restored, and forcing V.34 at *both* ends reaches the page deterministically instead of via the DIL lottery | Sessions 72–79, **114c** |
| **V.90 needs `--native-bearer-activation`** | open, cause unknown | Session 67, 87 |
| **DIL is a lottery** | open; attempts can fail before either rate is published | Sessions 88–93, 105–107 |
| **exact upstream rate falls outside the final quality ceiling** | guarded and live-selected at 12,000; bilateral data proof still pending | Sessions 107, 109–110 |
| **the native V90D bulk worker corrupts DM** | **FIXED for V.34 by working around it (115j–l); root defect still in the firmware worker.** `EICON_V34_PORTABLE_BULK` is **now the default**: it RTSes PM `0x19c8` on `0x0261` and serves the ADDSP guide's near/far delay-line ABI from `PortableBulkDelay` (page-agnostic, no changes needed). Three calls each: **plain froze at `0x0064` in both valid calls** (~928 M-iteration runaway, writes from `0x1930`/`0x1934`/`0x2e21`); **portable froze in none of three**, distinct PCs 59–1857 → ~7,470, PM `0x0771` 0 → 95 k–706 k, `TrnProgress` → `0x0090`, and `--assert-dm-clean` shows **zero writes from those PCs in every call**. `=0` restores the native worker for A/Bs. **Caveats:** no call connects (this fixes the freeze, not the connection), and `ab-portable-2` looked untidy — dispatch 95 k, 916 M executions of PM `0x3b1e..0x3b23`, `TrnProgress` oscillating `0x1408`/`0x2804`. **Explained (136):** `0x3b1e` is the INFO overlay's 16-bit bit reversal and `0x3b24` is a bit-reversing block copy whose destination is the status block itself, so that call's state machine had simply stopped and the FFT was running in the DM the reading came from. Not an anomaly and not a state | Sessions 106–108, 110–111, 114k–l, 114z, 115–115l, **136** |
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
V.44 adds 12, and the bulk/rate work adds 33; the full Python suite is 393.

**Session 183: it also runs between two emulated ends now**, over V.22bis at
2400 bit/s — LAPM connected, then PPP through CHAP to IPCP, with no bad FCS and
no retransmissions. That is the whole stack on the card's own firmware at both
ends rather than on two `LapmEndpoint`s wired together in `tests/test_ppp.py`.
See §6; the caveat is that two emulated ends share their bugs, so it tests the
pump, not the protocol.

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

### V.90A, the analogue side (Session 134)

**The PRI firmware admits V.90A.** This was thought to need a `.2q0` re-target,
on the grounds that the PRI's combifile file set carries no V.90 APCM overlay.
It carries none, and that turns out not to be the same thing: `te_dmlt.pm`
gates V.90A at `0x80091f78` on two conditions the harness owns — CAI bit `0x04`
(`DSP_CAI_MODEM_ENABLE_V90A`, which `EICON_MODULATION=v90a` already sets) and
finding download `0x026b` in the DSP table this harness stages. Miss either and
it traces "V.90A not supported".

`EICON_DSP_EXTRA_DOWNLOADS=0x026b` stages the overlay on top of the card type's
file set, and the firmware then takes the supported branch and sets its
capability bit. `EICON_HOOK_CALL` is `--hook-call` for the harnesses that build
their own shim, which is how that was measured on the native call path.
`docs/bri_target.md` is corrected in place.

**Session 135: it reaches the DSP too.** Exactly one host write of 51,967
changes — word 39 of the `0x6802` assignment stream, `4760` -> `47e4` — and
only when the overlay is staged; the CAI bit alone is invisible below the MIPS.

`eicon_loopback.py` now takes `--answerer-modulation` / `--caller-modulation`
and stages the APCM overlay on the V.90A end automatically, so both sides of a
V.90 link can be the card's own firmware for the first time. (Those flags stage
the overlay and build the CAI; **they do not steer V.8's selection** — Session
183, §2.) **It does not get
there.** Both ends walk V.8 -> INFO -> V.34 and cycle between pages 7 and 8;
neither ever requests page 13/14, deepest `TrnProgress` is `0x0090` (answerer,
parking at `0x002e`) and `0x0060` (caller, stopping at `0x0041` on the INFO
page). That is the standing V.34 blocker, reached before V.90 selection
happens. **Both ends park in INFO with neither advancing** — see 136.

**Session 137 locates it.** INFO is not the problem: both ends receive, CRC-
validate and act on each other's INFO messages every cycle (`DM(0x0686) = 1`
25 and 36 times; `DM(0x1651)` reconfigured to exactly the 38- and 77-bit
payloads `tools/v34_info.py` reads off the wire). They then enter the V.34 page
nine times each and abandon it after 180–280 ms, every time publishing the
firmware's own reason code `DM(0x3F8A) = 0x5678` (PM `0x2d63`, which sets
`DM(0x2252) = 7` and requests the page through PM `0x290c`). The cause is
asymmetric: **the calling side transmits nothing on page 8** — exactly zero
output from 160 ms before the page loads until it returns to INFO, against the
answerer's steady 250 RMS — so the answerer trains against silence.

**Session 138 names the gate: `DM(0x2140)`.** Diffing the two ends' PC
histograms over the same overlay, 399 words of `0x0261` run on the answerer and
never on the caller; the hottest by far (60.4 M executions) is the complex MAC
filter body at PM `0x2f8b..0x2f9c`, whose entry tests
`DM(0x2140) AND DM(0x12FD)` and returns immediately when it is zero. The caller
writes that word 20 times and every one is `0x0000`; the answerer also writes
`0x0044`, `0x004c` and `0x02cc`. It is published by the script block loader,
and the two ends come out of *different* record formats — the caller's writes
from PM `0x2e21`, the answerer's from `0x2e2d`, selected by the indirect jump
at `0x2e18` through `DM(0x14A6)`, the same loader Sessions 114y–115l worked in.

**Session 139 settles it: no.** `EICON_FORCE_DM="ADDR=VALUE[@OVERLAY]"` now
holds DM words once per sample (loud, twice — it is a firmware patch), and
`eicon_loopback.py` takes `--caller-env`/`--answerer-env` so the patch reaches
one end and the other stays a control. Forcing `DM(0x2140)` to `0x02cc` and then
to `0xffff` leaves the caller silent (TX RMS 5.0 and 4.9 against a control of
5.5) **while demonstrably opening the gate** — PM `0x2f8b..0x2f9c`, which had
never executed on the caller, runs 880–70,464 times. So that filter is not the
transmitter's enable.

**Session 140 answers it: the role word `DM(0x2198)`.** `DM(0x14A6)` selects the
loader's record format, but only as a consequence of *which script table* is
being walked — PM `0x2d6b..0x2d74` picks base `0x1EA2` with format A when
`DM(0x2198)` is nonzero and base `0x1E81` (115n's **answering** script) with
format B when it is zero. So 139's "format B goes unread" was the caller
correctly declining the answering script, not a defect.

`DM(0x2198)` is GEN_SETUP1 bit 3 — the modem role — published by PM `0x1049`
(`0x0008` on the caller, `0x0000` on the answerer). Holding it at zero on the
calling end alone takes its page-8 transmit RMS from **5.5 to 248.8** and its
state trail from `0x0060` to `0x0060 0x0064 0x0070 0x0072 0x0074 0x0090`.

**This is not a fix** — it makes the calling end a second answerer, and both
ends then stop at `0x0090` as usual.

**Session 141 maps the calling script and eliminates it.** Both scripts have
the same thirteen states, the same tests and the same branch structure (each
role's branch indices resolving to its *own* blocks), and the entire content
difference is two bits: field `0x04` -> `DM(0x213b)` bit 11 and field `0x0b` ->
`DM(0x2142)` bit 14, both set on the answering side. Forcing each and then both
on the calling end leaves it silent (TX RMS 5.9 / 5.3 / 5.8 against a control of
5.5), though bit 14 does advance the sub-state `0x0060` -> `0x0062`.

So the transmitter's role dependence is not in the script *content*.
`DM(0x2198)` has four readers in the V.34 overlay. **Session 142 eliminates
three of them**: PM `0x2b4a` assembles a control word into `DM(0x223F)` that is
read nowhere in any loaded image, and `0x2b49`, its only call site `0x3012`, and
the other two readers `0x3034`/`0x3102` all have **zero executions on both
ends** in histograms where their neighbours run 150k–290k times. The only live
reader is the script selector at `0x2d6e`, five executions per end.

That leaves the resolution: selecting the other script changes **which blocks
are visited and in what order**, not just field values — the branch fields
resolve to blocks in whichever table is selected. Forcing two field values (141)
left the caller walking the calling script, so it tested the wrong thing.
Supporting measurement: the caller runs loader format A **289,978** times
against the answerer's 271, and does four times the total record loading — a
state machine re-entering blocks continuously.

**Session 143 traced it, and it is one block per end.** There are two
sequencers: A (terminator `0x19`, cursor `DM(0x14A5)`) publishes TrnProgress
from `DM(0x2147)`; B (terminator `0x24`, cursor `DM(0x2192)`, bases
`0x1EA2`/`0x1E81`) **never enters a block on either end** — so 141's map of
`0x1EA2` and 115n's of `0x1E81` describe a table that is not walked. Sequencer A
is the live one.

Its entire trail: the caller enters block **`0x1ae5`** 49,105 times and nothing
else; the answerer enters **`0x1ba5`** 12,201 times. Those are states `0x0060`
and `0x0090` — the two deepest states Session 137 measured. The blocks are
identical but for `branch0`, which resolves to **the block itself** on both
ends. So neither end is stuck by a fault: each is in a *designed wait state*,
re-arming a 50-tick countdown, doing what its script says.

Both wait on the same test (index `0x0a` -> PM `0x2ef3`), which reads the
self-clearing latch **`DM(0x13BF)`**, set at PM `0x0e3a` when a six-tap
correlator's magnitude exceeds `DM(0x2145)` — the block's own field `0x0e`,
`0x02bc` on both ends.

**Session 146: the detector is not the problem, and 143's reading of it is
withdrawn.** The latch sets **2,374 (caller) / 2,399 (answerer)** times a run at
the *real* threshold, and forcing the threshold down to `0x0001` changes those
counts by one and changes nothing else — same `0x0060`/`0x0090` ceilings. The
correlator clears `0x02bc` routinely and the test consumes the latch ~1,600
times. "The latch never survives to the test" was an inference from the block not
advancing, and it was wrong.

**What is actually wrong:** the block's only branch target is itself.

```text
block 0x1ae5  branch0 = 0x0002 -> DM(0x0678) = 0x1ae5   (caller,   state 0x0060)
block 0x1ba5  branch0 = 0x0013 -> DM(0x0689) = 0x1ba5   (answerer, state 0x0090)
```

The test passes, the sequencer takes branch0, and branch0 re-enters the same
block — 49,105 times on the caller. Neither end is blocked on a signal; both are
in a block with no exit. Sequencer B, which might have provided one, never
enters a block at all (143).

**Session 147 closes it: the loop is the branch being taken.** Walking all 60
blocks a lane of sequencer A's script, **only two carry a branch field at all**
— `0x1ae5` (caller, `0x0060`) and `0x1ba5` (answerer, `0x0090`) — and both point
at themselves. Every other block has none, so the script's normal advance is
*sequential* and a branch exists only to override it. The self-branch is the
"stay here" arm: **a test that passes keeps the card in the block.** 143 read
this backwards and 144–146 inherited the error.

Raising the threshold confirms it:

| `DM(0x2145)` | caller deepest | caller page-8 states | answerer deepest |
|---|---|---|---|
| `0x02bc` (script) | `0x0060` | 60 | `0x0090` |
| `0x0001` forced | `0x0060` | 60 | `0x0090` |
| `0x2000` forced | **`0x007a`** | 60 68 72 7a | `0x0090` |
| `0x7fff` forced | `0x004f` | (never reaches page 8) | **`0x0092`** |

The complete chain: the page-8 transmitter never leaves the noise floor (145) →
the correlator clears `0x02bc` on that noise ~2,400 times a run (146) → the wait
block's test always passes → its only branch is taken and points at itself → the
block is re-entered 49,105 times (143).

**Not a fix** — the correct behaviour is a transmitter whose output is not
broadband, against which `0x02bc` is the right threshold.

**Session 149 supersedes everything below: the page-8 transmitter was being
decimated by ten, and pacing it releases both ends.** `V34_CYCLES_PER_SAMPLE`
gave the page a fixed budget, so it published a transmit sample into `DM(0x3764)`
**9-12 times per 8 kHz tick** and the harness took one — a real waveform aliased
into flat noise. `EICON_V34_PUBLISH_PACED` (default on) ends the run at the
publish instead, taking that to exactly 1.00. Page-8 residency goes from 0.30 s
of cycling to one continuous 10.20 s segment, both ends leave their ceilings —
caller `0x0060` -> `0x00b0`, answerer `0x0090` -> `0x00b0` — the wait blocks of
143/147 release on their own, and the caller transmits at all for the first time
(page-8 RMS 5.0 -> 776.6), retiring 137's "the calling side transmits nothing".

**Read Session 150 before believing the signal improved: it did not.** 149 also
claimed the paced transmit signal matched hardware at 0.813 against 0.818. That
was the concentration metric scoring a **stuck DC level** — the answerer emits one
unchanging sample for twelve seconds from five seconds into the page. In the
300-3400 Hz passband both ends sit at **0.071/0.081 against hardware's 0.818**,
barely moved from the unpaced 0.096. Neither end modulates, so the deeper states
are being reached on timers, not by training. **Never read a concentration number
without its peak frequency**; the tool now prints both.

The 148 result below stands as measured and is what pointed here, but its
conclusion — "the budget is eliminated" — was wrong about the budget's role: it
does not act through the detector's latch rate, it acts on the signal.

**Session 148 ran that test, and the answer is no.** Sweeping
`V34_CYCLES_PER_SAMPLE` over 20000 / 4125 / 1500 drops the absolute latch count
about five-fold, but only because the correlator is invoked five times less
often (PM `0x0e3b`: 154,330 -> 33,619 executions in the page-8 window). The
fraction of invocations that latch is **flat at 51–60% across the whole range**,
and the answerer's transmitted spectral concentration goes 0.097 -> 0.084 ->
0.189 against 0.05 for white noise — broadband at every budget. Ceilings
unchanged at `0x0060`/`0x0090`. **`V34_CYCLES_PER_SAMPLE` is eliminated as the
cause**, and 147's re-ranking is withdrawn; it is still wrong (160 MIPS against
a 2185N's 9,375 per sample) and lowering it stretches page-8 residency from
0.30 s to 4.46 s, but it is tidiness, not the blocker.

Also from 148, **do not trust 146's "~2,400 latches"**: that was
`--watch-dm-writes 0x13bf:4000` hitting its limit (2,374 + 1,626 = 4,000
exactly). Uncapped it is 142,734. A watch limit is a ceiling on the log, not a
measurement.

`tools/v34_page8_concentration.py` is the concentration metric, run off a
capture prefix with no firmware in the path:

```bash
/tmp/eicon-venv/bin/python tools/v34_page8_concentration.py artifacts/loopback-v90a/lat-1500/answerer
```

Two results from the sessions that chased the signal stand on their own even
though their premise was wrong: transmit level does not predict outcome (144),
and `V34_CYCLES_PER_SAMPLE` is not the blocker though it is wrong — 14.06
transmit-chain executions per sample against 1.00 on a run-to-idle page, and a
page-8 output that never leaves the broadband floor at the default budget where
hardware reaches full spectral concentration (145). The concentration metric
(energy in the top 5% of bins, slices confined to one contiguous page-8 window)
needs no new tool and separates hardware from loopback cleanly.

The field-to-DM rule is `DM(0x2137 + field)`; branch indices resolve through
`DM(0x0676 + i)` to script blocks and tests through `DM(0x064B + i)` to
routines. The transmit credit chain is *not* the gate — it
runs and publishes 150,754 samples in the page-8 window, all zero — and neither
is the page-8 instruction budget, though 138 shows that is wrong too (14.06
publishes per sample against exactly 1.00 on a run-to-idle page; sweeping it
scales the rate and leaves the silence).

**Session 181: the caller's V.8 was offering a V.22-only modulation mask.** The
write database base is DM `0x3EE0`, not `0x3EE4` — GEN_SETUP1 `0x048c`/`0x0484`
sits at `0x3EE1` in every capture — so Session 100's originate NORM_L force had
been landing in `0x3F0D`, which the V.8 overlay never reads, instead of NORM_L
at `0x3F09`. The calling end therefore entered V.8 with the dial page's `0x3004`
while the answerer offered `0xb13f`, and `DM(0x3FC4)` — the word Session 179's
classifier decides the page from — is seeded straight off it. The shim now
restores the caller's own native WDB value (`0xa13f`); `EICON_ORIGINATE_NORM_L=`
(empty) is the pre-fix control. Any `+0xNN` write-DB offset resolved through the
`0x3EE4` base since Session 100 is off by four.

**Session 182: the loopback started both modems on the same instant, and the
1 s off-hook guard landed inside V.8.** The endpoint replaces the modem's first
`--rx-guard-ms` (default 1000) of receive audio with silence — an FXS transient
guard written for a real ATA. With both ends starting together the answerer's
ANSam begins at 0.533 s, so the caller is deaf through the first 467 ms of it,
the guard lifts at sample 8000 and V.8 evaluates at 8160: one RTP packet of tone
decides the modulation, and 15 ms of one-way delay is enough to lose it. With
the guard off, detection follows ANSam onset by 47 ms and 25 ms of delay moves
it by 25 ms. **`--setup-gap-ms` (default 2000) now holds the answering end off
the line** — idle PCM out, arriving audio dropped, card not clocked — which is
where a real call puts the two modems' clocks, and 0 ms and 25 ms then behave
identically. `--setup-gap-ms 0` restores the old rig; `--rx-guard-ms` is
forwarded and a guard longer than the gap is warned about.

This **withdraws Session 178's "V.8's modulation selection depends on round-trip
delay" as a firmware finding**, and makes Sessions 179–180 consequences of the
caller abandoning V.8 at 1.02 s rather than defects in their own right. Their
measurements stand; the framing does not. Any earlier claim about *when*
something happened inside V.8 was measured under the old pacing.

It buys fidelity, not a connection: 4/4 forced runs of the V.90A rig walk
V.8 → INFO → V.34 identically and 1/3 unforced runs collapsed to V.22 with no
lag, but no run of either kind ever requests page 13/14, and at 25 ms of lag the
caller still drops to V.22 with the mask correct — so Sessions 178-180's delay
fragility is untouched.

So V.90A is no longer a firmware or file-set question. It is queued behind one
thing already on this list: V.34 phase 2 completing between two emulated ends.

**Session 183: `--modulation` does not select a modulation, and V.22bis is the
only handshake that has ever completed here.** Forcing both ends to `v22b,0`,
then to `v32b,0`, produces page trails identical *to the sample* to an unforced
run — V.8 → INFO 4.840 s → V.34 7.000 s, answerer to `0x00b0` at 9.900 s — and
`v8_line_result` is `0x1000` in every row of all six captures. The CAI is built
correctly and does differ between them; it just is not what V.8 reads. That is
§2a's "the write database is untouched" seen from the other end: the mask
reaches the DSP through the assignment stream and **never reaches NORM_L at
DM `0x3F09`, which is the menu**. Pinning NORM_L instead does not force a
modulation either — `EICON_ORIGINATE_NORM_L=0x3004` (V.22-only) on the caller
still concludes V.34 — so **nothing in this harness currently selects a
modulation**, and 181 should not be read as saying the `0x3004` mask chose V.22
in the old runs. The guard did, exactly as 182 says.

What does exist is a **completed V.22bis link**: under the old pacing
(`--setup-gap-ms 0`, guard 1000, 25 ms pad) both ends load page 1 / `0x0266`
and reach `TrnProgress 0x00d0` with `speed_tx|speed_rx|CTS|DSR|DCD` at ~7 s,
holding for the rest of the call, reproduced sample-for-sample against Session
181's `norml-lag-ctl`. Session 178 retracted this `0x00d0` as evidence about
V.34, which was right — the trail is page-specific — but it is not a statement
that the V.22 link failed. **It carries no data**, and that gate is ours, not
the firmware's: see §6's V.22 data-path entry.

The two-sided V.90 loopback, which is the rig for all of this:

```bash
/tmp/eicon-venv/bin/python -u tools/eicon_loopback.py --native-mips \
    --answerer-modulation v90 --caller-modulation v90a --seconds 40 \
    --capture-dir artifacts/loopback-v90a/runNN \
    --pc-histogram --pc-histogram-from 0x0261
```

**Loopback pacing is not in the way, and Session 100's row overstates it for
this rig.** Both endpoints' captures end at the same sample (625280, 78.16 s,
3909 rows), their media clocks stay within 0.10 s of each other for a whole
90 s run, and neither substitutes or drops a sample. The absolute rate is
0.82-0.93x, not 0.65x, and a shared slow clock does not distort a handshake
between two ends that exchange samples one for one — it would only matter
against a real-time third party. (Session 135 first claimed a 2.9x spread
between the ends; that was a misread of the last TrnProgress change as the last
emulated sample, and is corrected in place.)

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

**Done during Session 183, and it changes what this rig is for: the V.22 page
now carries PPP.** Loopback completes a V.22bis handshake — both ends `0x00d0`
on page 1 / `0x0266` with `CTS|DSR|DCD` — and used to carry nothing, because
three conditions in `eicon_mips_shim.py` were keyed to two overlay ids:
`_service_tx_request()` returned unless `resident in (0x0261, 0x026A)`;
`_next_tx_words()` had readers for those two and `else: count = None`,
so `_lapm_active` never set; and `_nl_data_gate()` refuses on
`tx_v42 and not _lapm_active`. The V.42/V.42bis/V.44/PPP stack above them was
already modulation-agnostic and tested.

With a width for page 1 and that page in the request test, a
`--ppp --ppp-auth chap` call brings up the lot at 2400 bit/s: first synchronous
TX datagram at 7.27 s / 7.08 s, V.42 detection, XID, SABME/UA, **LAPM
connected**, LCP up, CHAP authenticated, **IPCP up (100.64.0.2 ↔ 100.64.0.1)**.
17 frames each way, zero bad FCS, zero aborts, zero retransmissions, no timer
expiry, media `substituted 0, dropped 0, 1.00x`. First data path this project
has completed end to end — the card's own firmware on both sides.

**And it carries user traffic.** `--ppp-ping peer` (client end; new, with
`icmp_echo_request()`/`parse_icmp_echo_reply()` in `ppp.py`) gets **4/4 replies
at ~500 ms**, which is what 2400 bit/s predicts: ~40 octets of echo request plus
framing is 133 ms each way, twice, plus the acknowledgement and a 20 ms media
quantum. A wrong datagram width could not produce both a plausible rate and a
valid FCS, so this is also the end-to-end confirmation of the constant 4. The
NAT's `icmp=0 in=4 out=4` is correct — a ping to the gateway is answered inside
`usernet` and never becomes a host socket. Still not interop evidence: two
emulated ends share their bugs.

The page's side of the interface is measured, not assumed: on `0x0266` the
firmware raises `DI_control` bit F ~19,500 times a call, bit 13 ~19,700 times,
publishes ~9,900 receive words at `DM(0x3FAE)`, and reads `DM(0x3F05)` from PM
`0x3fc4` — the same ADDSP §5.3.1 addresses the V.34 and V.90 pages use.
`0x3FAF` and `0x3F06`/`0x3F07` are never touched, so both directions are word 0
alone; every receive word is `f000`, four bits left-aligned, mark fill, which is
both an idle link and the 4 bits × 600 baud = 2400 bit/s V.22bis wants. The
page's first request and first TXD0 read land as the handshake completes, not
during training, so **being asked is sufficient evidence of the data state** —
there is no `DM(0x3FC2)` analogue to find.

The code: `V22_OVERLAY`/`V22_DATAGRAM_BITS`/`V22_BIT_RATE`, the `0x0266` arm of
`_next_tx_words()`, 2400 symmetric rates, the page-aware `_rx_datagram_bits()`,
and `V22_OVERLAY` in `_service_tx_request()`'s page test. `_service_rx_data()`
needed nothing — its RXD1 arm simply never fires on a page that does not write
RXD1. Six tests in `tests/test_nl_data_bridge.py`; suite 393 green.

**Session 184 made it a fixture rather than a trick.** The V.8 classifier at PM
`0x3ba1..0x3bfb` picks the page from `DM(0x3FC4)` alone, and that word is
writable: `EICON_FORCE_DM=0x3FC4=0x0004@0x025f` on both ends selects V.22 under
*default* pacing — LAPM, PPP and 3/3 pings at 440 ms with no lag, no
`--setup-gap-ms 0` and no NORM_L games. The full table is `0x0016`→V.22,
`0x6000`→V.32, `0x0029`→FSK, `0x0040`→page 17, `0x0E00`→FAX, `0x0080`→page 20,
default→INFO/V.34. It is the first thing here that selects a modulation on
purpose; `--modulation` does not, and neither does NORM_L (which only seeds the
fallback, and only when V.8 fails to publish a result).

**V.32 selects, and Session 185 got it past the loader and into a new failure.**
`0x3FC4=0x6000` puts both ends on page 2 and loads `0x0266` (the same
"V.22/V.32 LEC" image), after which both ask for **bootpage 19 / download
`0x0267`, the V.32 Partial Overlay**. That request is now served:
`_service_partial_overlay()` triggers on the durable pair (bootpage 19 plus
`DM(0x3132)`) rather than on `DM(0x3131)`, which the kernel posts and clears
inside one 8 kHz frame and a host sampler can only catch by luck; it leaves
`self.resident` on the underlying page, and runs the continuation at
`DM(0x3143)` — without which the page takes the partial, times out, and falls
back to DIAL. Unserved page requests are no longer silent.

**V.32 still does not train.** Both ends now reach the data interface
(`DI_control=0xa000[tx_request|rx0_valid]`, further than any previous attempt)
and then the line goes silent — TX RMS 0.0 against 252/261 for working V.22 —
while the core runs away: **93 PCs, 311 M instructions**, six instructions of
one MAC loop at PM `0x1db5..0x1dba` taking 6.2% each, media clock down to 0.52x.
`DM(0x376D)` is read every iteration to build a PM index with a negated stride:
a delay-line walk with no bound. This is Session 115's runaway one page over
(there PM `0x1930`, ~928 M iterations, modulo bound zero), and the next move is
the same — find what bounds `0x1daa..0x1dba` and whether the partial's 332-word
`0x3680` block was meant to seed it.

What is left: **bound that loop** (the whole of V.32), **FSK and FAX** (one
`0x3FC4` value each, `0x0001` and `0x0800`, untried and now with the partial
loader in place), **`at_watch()`'s rate word** (unmeasured on page 1; without a
`CONNECT` the AT parser silently eats terminal text, so `--v42-pty` on a V.22
call will not work yet), and **traffic beyond the gateway** (the ping is
answered inside the NAT, so no V.22 client flow has been re-originated as a host
socket yet).

What it buys is a loopback that carries PPP end to end at 2400 bit/s: the whole
stack above the pump exercised on emulated hardware rather than on two
`LapmEndpoint`s wired together in `tests/test_ppp.py`. What it does not buy is
interop evidence — two emulated ends share their bugs.

The one wrinkle: the V.22 connection is only reachable through the *old*
pacing (`--setup-gap-ms 0`, guard 1000, 25 ms pad), because nothing in this
harness selects a modulation on purpose — see Session 183 in §2. A rig that
reaches V.22 deliberately is worth having if this work continues past the first
frame.

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
