# RXSAMPLE is written, the detector fires, and the CM branch loses a race

This corrects the closing section of `docs/analog_v8_oracle.md`
("Why the Analog side does not progress: RXSAMPLE is never written") and the
plan that followed from it. The premise is false under
`--caller-kernel-dispatch`, so the "give analog109 a real SPORT1 kernel-driven
receive path" work was **not** started — it would have fixed something that
already works.

All measurements below: `--answerer-firmware-set pri117 --answerer-modulation
v90 --caller-firmware-set analog109 --caller-modulation v90a
--caller-kernel-dispatch --analog-codec-rate 9600 --seconds 20`.

## RXSAMPLE_0..5 is filled by the page, not the kernel

`PM 0x173A` in `V8.ANA` — and at the same address in `INFO.ANA`, `V34.ANA`,
`DIAL/FSK/FAX.ANA` and `HV34.ANA`, so it is shared library code linked into
every overlay:

```text
173a: CNTR = DM($3F67)      ; 3, 4 or 5 -- the guide's "the kernel writes N samples in"
173b: I0 = $3F30            ; RXSAMPLE_0
173c: L7 = $0014            ; a 20-word circular receive ring
173d: I7 = DM($376C)        ; ring read pointer
173e: DO $1740 UNTIL NOT CE
173f:   AX1 = DM(I7,M5)
1740:   DM(I0,M1) = AX1     ; -> RXSAMPLE_n
1741: DM($376C) = I7
```

The ring is produced at `PM 0x178F` from an upstream 0x40-word buffer through an
interpolator at `PM 0x17AE`. **Neither the Analog kernel (`0x000d`) nor
`TIKRNL81.ANA` references `0x3F30..0x3F35` at all** — a scan of every image in
the build-109 set for that immediate finds hits only in overlays. The guide's
wording is about who feeds the ring, not about who fills the array.

Exec watches confirm the whole chain runs live: `PM 0x178F` (ring producer),
`PM 0x1728` (the `DM(0x36F0)` gate, passed), `PM 0x172E` and `PM 0x173A`.

`--watch-dm-writes 0x3F30:200000` over a whole call: **40,985 writes, 2,630
distinct values.** The array is live, not frozen. `RXSAMPLE_4`/`_5` staying zero
is correct — `DM(0x3F67)` is 4, so only `_0.._3` are written.

> A caution about how the old measurement went wrong, because it nearly caught
> this one too: the first run here used `--watch-dm-writes 0x3F30:400` and saw
> 400 writes of `0x0000`, which looks exactly like a dead array. Those 400
> writes span cycles 39,597–2,227,371 — the first 5% of a call that runs to 47
> million, before the answerer has even joined the bearer. A write-watch limit
> silently samples the *beginning* of a call.

## The ANSam detector fires, well past its threshold

`DM(0x07BD)` is the hysteresis counter, `0x0780` (1920) the threshold that
condition 3 tests. Over one call, 34,557 writes:

| | |
|---|---|
| max | **0x21D6 (8662)** — 4.5× the threshold |
| samples at or above threshold | 7,237 of 34,557 (20.9%) |
| longest consecutive run above | **7,237** — one contiguous block, against the 240 needed |

So the detector is not weak, not frozen and not mistuned. Everything the oracle
inferred from "MR1 falls 10× short" belongs to a configuration that is no longer
the one under test.

## What actually blocks CM: slot 2 always wins

Record `0x01C7` carries both exits, and its fields persist through the CI
retransmit loop (`0x01DC ↔ 0x01EE` carry no destination indices of their own),
so **both are re-evaluated on every pass of the loop**:

| slot | destination | condition | requires |
|---|---|---|---|
| 1 | index 6 → `0x0200` → fall-through → **`0x021B` (CM)** | 3 (`PM 0x37DC`) | `DM(0x07BD) >= 0x0780` **and** `DM(0x0778) >= 0xF0` |
| 2 | index 5 → `0x0281` → … → `0x031D` (no CM) | 2 (`PM 0x37F7`) | `DM(0x07BD) >= 0x0780` |

The dispatcher runs slot 1 first, so slot 1 gets the first look — but it needs a
second counter, `DM(0x0778)`, at 240, and condition 3 *clears that counter
itself* whenever the detector is below threshold (`PM 0x37DD` → `PM 0x37D4` →
`PM 0x3ED1..0x3ED3` writes `M0` to `DM(0x0776..0x0778)`). Slot 2 needs only the
threshold. So on the first pass where the detector crosses, slot 1 fails on a
counter that has just been released from zero and slot 2 fires immediately.

Measured: `DM(0x0778)` takes only the values 0 and 1 across 9,190 writes, and
never approaches 240. The watch's own PC history at the loop exit shows exactly
this — condition 3 evaluated, first gate passed (`37dd → 37de`, not the reset
path), second gate failed, then condition 2 taken:

```text
37a6 37a7 37a8 37a9  37dc 37f7..37fa 37dd 37de 37df 37e0 37e1  37aa   <- slot 1, not taken
37ab 37ac 37ad       37f7..37fa                                37ae   <- slot 2, taken
378b  DM($049F) = 0x0281
```

## The timing, which is the part that looks wrong

`DM(0x07BD)` first crosses the threshold at **cycle 37,705,629**, and the CI
loop exits 34,000 cycles later at 37,739,671. On a 20-second call ending at
cycle 47.4 million, that crossing is about **15.9 seconds in** — for an ANSam
the answerer begins emitting around 2 s.

So the detector takes some fourteen seconds to respond to a tone that is
present the whole time. That, not the record layer, is the thing to explain
next. It also means `DM(0x0778)` never had a chance: the branch it gates was
only ever going to be reachable during a long, *early*, stable detection.

## Where this leaves things

- Do not build the SPORT1 kernel receive path for `RXSAMPLE`. It is written.
- Do not touch the V.8 record layer. `0x01C7` offers the CM branch on every pass
  of the CI loop; the script is behaving correctly given its inputs.
- `docs/analog_v8_oracle.md`'s final section and its "two ways out" are
  superseded by this file. Its earlier sections — the detector variants, the
  level law, the amplitude-blindness result — still stand.
- The open question is now narrow and quantitative: **why does the ANSam
  discriminator take ~14 s to cross a threshold it eventually clears by 4.5×?**
  `DM(0x07BC)` (the smoothed level, 1,887 distinct values, max 7028) against
  `DM(0x0748)` (2000) is where to start, since the counter only climbs while the
  level is above that.
