# The receive path flattens ANSam's envelope, and that is why there is no CM

One number, measured with a control:

| where | 15 Hz envelope depth |
|---|---:|
| on the wire, `caller.rx.wav` | **0.182** |
| `DM(0x0772)` — the first per-sample value the V.8 page sees | **0.007** |
| `DM(0x077F)` — the detector chain's input | 0.010 |

V.8 §7.2 requires ANSam's amplitude to vary between 0.8 and 1.2 of average — a
depth of 0.20. The answerer emits that correctly. **By the time the signal
reaches the V.8 page it is gone, flattened by a factor of about 18**, and every
downstream failure in `docs/analog_rxsample_correction.md` follows from it.

## Why this is the whole chain

The V.8 page's CM branch (`docs/v8_script_records.md`) is gated by condition 3,
which needs two detectors. Detector A is amplitude-blind by construction — the
sign slice at `PM 0x3EA4`/`0x3EB4` discards magnitude — so it fires on the
*presence* of the tone and reads 4.5× its threshold. Detector B is the one that
measures the **modulation**, and it is what distinguishes ANSam from a plain
2100 Hz ANS. No modulation, no detector B, no CM.

The chain is: line → `DM(0x0772)` → … → `DM(0x077F)` → AGC → `DM(0x03A3)` →
Hilbert magnitude `DM(0x0776)` → biquad `PM 0x3F1D` → level `DM(0x0777)` →
counter `DM(0x0778)` ≥ 240 → condition 3 slot 1 → `0x0200` → `0x021B` = CM.

## The firmware is correct — proved, not assumed

Driving the firmware's own magnitude chain (`PM 0x3EDE`, input `DM(0x03A3)`)
from `tools/v8_envelope_filter_bench.py` with a clean 2100 Hz tone carrying 20%
AM at 15 Hz, and decimating by the `DM(0x07BE)` reload of 15 exactly as the
firmware does:

```text
magnitude decimated to 640 Hz: strongest component 15 Hz at depth 0.506
```

The envelope dominates, which is precisely what the detector is built to see.
Fed the *live* sequence instead, the same code gives 15 Hz at depth 0.027 and a
240 Hz alias at 0.299 — and replaying that live sequence through the bench
reproduces the live filter output exactly (median 0, max 3816, against live
median 0, max 3816). **The firmware behaves identically to the real card; only
its input differs.**

## Rates, settled by ratio rather than by clock

Earlier passes derived rates from the sparse `[adsp] sample N (T s)` log lines
and got them wrong twice. Ratios need no time axis:

```text
inner loop (PM 0x373F) / outer block (PM 0x3732)  = 4.00   -- CNTR = DM(0x3F67)
outer block / RXSAMPLE_0 fill (PM 0x1740)         = 1.00   -- one symbol each
detector B (PM 0x3EE4) / inner loop               = 0.997  -- once per codec sample
biquad (PM 0x3EF9) / detector B                   = 1/15   -- DM(0x07BE) reload
```

So the loop at `PM 0x373E..0x3750` runs four times per symbol, once per codec
sample; the detector runs in it; and the biquad runs at 9600/15 = **640 Hz**,
where its measured passband of 0.0225 cycles/sample sits at **14.4 Hz** —
on ANSam's 15 Hz. The firmware's design is coherent and its rates are right.

## Where the loss is, and is not

`DM(0x0772)` is written at `PM 0x373F` from `CALL 0x3764`, the front end that
walks `RXSAMPLE_0..3` through `I4 = DM(0x06BE)`. Its depth is already 0.007, so
the envelope is lost **at or before the RXSAMPLE array** — upstream of every
line of V.8 page code. Not the AGC (`DM(0x077F)` is already flat at 0.010, and
it precedes the AGC), not the Hilbert pair, not the biquad, not the integrator,
not the record layer.

That puts it in this project's receive path: RTP → `analog_line` → the 8000→9600
`RationalResampler` → SPORT1 → the kernel's RXSAMPLE fill. **That is where to
look, and the measurement above is the test for any change to it.**

## Method notes, because two of them nearly produced false findings

- **A heavy write-watch can kill the call.** One run watching three addresses at
  300,000 each went host-bound and the caller received *silence* — `rx.wav`
  rms 0, 1.9 s long. Its `DM(0x0776)` was 32 everywhere, which looks exactly
  like a dead detector. Always check `rx.wav` rms before interpreting a run.
- **The envelope estimator was validated against a known answer.** The
  moving-RMS method reports 0.182 on the wire where complex demodulation reports
  0.179. Without that control, "depth 0.007" would have been unfalsifiable.
- Rates from log timestamps are unreliable; use ratios of write counts.
