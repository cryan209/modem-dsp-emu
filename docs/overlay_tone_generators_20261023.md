# Tone generators in the six other analog overlays, 23 October 2026

**Verified on all eight DSPs of spare SN12196, in two runs:** every one of the
six analog overlays that carries the relocated V8.ANA cosine table generates
tones on the real hardware, each landing on its expected frequency, and the
quadrature oscillator family's two outputs are **exactly 90.000° apart**. All
**57,632 captured words** from **38,400 oscillator evaluations** match the
emulator. Stock firmware was restored after both runs; primary SN1331 and its
answerer/SIP services stayed active. No BRI line or SPORT clock is involved.

Follow-on from [the V8.ANA modulation tone generators](modulation_tone_generators_20261023.md),
which established the increment-to-frequency law and the V.21/V.8bis tones in
one overlay. This extends the harness to the other six.

## Two oscillator families

The 128-word interpolated cosine table at V8.ANA PM `3C80` appears,
**word-identical**, in six further overlays, each with its own copy of the
oscillator that reads it. A test asserts all 128 words of each copy against
the V8.ANA original. The copies are not all the same routine:

| Overlay | Table | Oscillator | Family | Native rate |
|---|---:|---:|:--:|---:|
| `0260-info.ana` | `2C80` | `3BFF` | A | 9600 |
| `026e-infoh.ana` | `2F00` | `3BA3` | A | 9600 |
| `0273-v29fc.ana` | `3200` | `3CDA` | A | 7200 |
| `0271-v22fc.ana` | `3100` | `2EBA` | B | 9600 |
| `0275-v22bisfc.ana` | `2E00` | `2BC8` | B | 9600 |
| `0266-v22v32.ana-lec` | `2E00` | `2BCA` | B | 9600 |

**Family A** is the V8.ANA oscillator: phase arrives in `AR`, `M7 = 0x61`, it
indexes from table base **+1** and returns `MX0` and `MR1`.

**Family B** is a different routine: phase arrives in `AX0`, `M7 = 0x5F`, it
indexes from table base **+0** and returns `MX0` and `MX1`. Because it
advances `1 + 0x5F = 96` words — 48 of the 64 entries — between its two
interpolations, those outputs are a quarter period apart. That is the
structural reason the measured separation is exactly 90°, and a test asserts
the `L4`, `M7` and table-base words of each oscillator against the family the
descriptor claims.

Rates are not assumed: they are fixed by which increments make standard
tones. `v29fc`'s `1E39` and `2000` give **1700.02 Hz and 1800.00 Hz at
7200** — the V.29 and V.27ter carriers, both exact, where 9600 would make
neither standard. The INFO overlays' `1000` and `2000` give **1200 and
2400 Hz at 9600**, the carriers V.34's INFO sequences use, and 9600 is
16 samples per 600 baud symbol.

## Three probe kinds

`tools/eicon_overlay_tone_replay.py` runs original code at its original PM
addresses, with both SPORTs, autobuffers, interrupts and the timer disabled
and all four BRI interfaces in reset. `--run a` and `--run b` each build one
eight-DSP matrix; `list` prints the whole descriptor table.

**oscillator** calls the overlay's own oscillator from a plain 16-bit phase
accumulator. The oscillator masks phase bits 0..14, so a 16-bit accumulator
and the firmware's own 15-bit wrap produce identical samples — a test
asserts the two probe kinds agree word for word on all 1,200 samples
wherever both exist.

**native** copies the overlay's **own complete accumulate-and-call sequence**
verbatim, patching only its increment immediates, as the V8.ANA carrier probe
does. These sequences were traced structurally, from the increment load to
the oscillator call:

| Overlay | Original PM | Phase cell | Increments |
|---|---|---:|---|
| `info.ana` | `34ED..34FA` | `DM(1676)` | `1000` / `2000` |
| `infoh.ana` | `3597..35A1` | `DM(1635)` | `1000` / `2000` |
| `v29fc.ana` | `3059..3061` | `DM(1620)` | `1E39`, `2000`, `1800` |

Each picks between its increments with a condition, so **both** immediates
are patched and the condition cannot change the tone; a test re-derives every
patch and asserts each remaining word is untouched. `info.ana`'s sequence
also reads a 16-word frequency-correction ring through `DM(16BB)`, which the
probe supplies zeroed, so the correction term is zero.

**quadrature** calls the family-B overlays' own generator wrapper — PM `2ED1`
in `v22fc`, `2BDF` in `v22bisfc`, `2BE1` in `v22v32-lec` — which reads its
phase cell, calls its oscillator and writes a rotated I/Q pair. The probe
drives the wrapper's own phase cell and sets its rotation pair to unity
(`7FFF`, `0`), so the captured outputs are the wrapper's own I and Q.

## Physical results

Two runs of eight programs, 2,400 samples each, 7.06 s and 7.11 s. Every
probe has its own boot payload hash, so a changed routine or probe cannot
reuse this evidence.

| Run | DSP | Overlay | Probe | Expected | Measured |
|---|---|---|---|---:|---|
| a | `bf801800` | `info.ana` | native, calling | 1200 Hz | **1200 Hz** |
| a | `bf801810` | `info.ana` | native, answering | 2400 Hz | **2400 Hz** |
| a | `bf802800` | `infoh.ana` | native, calling | 1200 Hz | **1200 Hz** |
| a | `bf802810` | `infoh.ana` | native, answering | 2400 Hz | **2400 Hz** |
| a | `bf803800` | `v29fc.ana` | native, V.29 | 1700.02 Hz | **1700 Hz** |
| a | `bf803810` | `v29fc.ana` | native, V.27ter | 1800 Hz | **1800 Hz** |
| a | `bf804800` | `v29fc.ana` | native, low tone | 1350 Hz | **1350 Hz** |
| a | `bf804810` | `v29fc.ana` | oscillator, V.29 | 1700.02 Hz | **1700 Hz** |
| b | `bf801800` | `v22fc.ana` | oscillator, 1200 | 1200 Hz | **1200/1200 Hz, 90.000°** |
| b | `bf801810` | `v22fc.ana` | quadrature, 2400 | 2400 Hz | **2400/2400 Hz, 90.000°** |
| b | `bf802800` | `v22bisfc.ana` | oscillator, 2400 | 2400 Hz | **2400/2400 Hz, 90.000°** |
| b | `bf802810` | `v22bisfc.ana` | quadrature, 1800 | 1800 Hz | **1800/1800 Hz, 90.000°** |
| b | `bf803800` | `v22v32.ana-lec` | oscillator, 1800 | 1800 Hz | **1800/1800 Hz, 90.000°** |
| b | `bf803810` | `v22v32.ana-lec` | quadrature, 1200 | 1200 Hz | **1200/1200 Hz, 90.000°** |
| b | `bf804800` | `info.ana` | oscillator, calling | 1200 Hz | **1200 Hz** |
| b | `bf804810` | `infoh.ana` | oscillator, answering | 2400 Hz | **2400 Hz** |

Frequencies come from a Hann-windowed Goertzel scan in 1 Hz steps, so the
V.29 carrier's 1700.02 Hz reads back as 1700: that 0.02 Hz is the scan's own
step, not a generator error. Hardware and emulator agree word for word on
every captured word of both runs, footers included. The quadrature separation
is measured from the complex DFT of each channel at the detected peak and is
90.000° on all six two-channel probes, on the card and in the emulator alike.

Recordings of the actual hardware output are under
`artifacts/dsp-conformance-20261023/overlay-tone/` — 22 WAVs, each at its
overlay's native rate, with the family-B probes split into `-i` and `-q`
files.

## What is not established

The V.22 overlays' probes are driven at the V.22 originate/answer carrier and
guard-tone increments (`1000`, `2000`, `1800`), but **those increments were
not traced to a generator constant**. In these overlays the oscillator's
phase cell is written by a second-order timing loop at PM `2F00`, which
combines a feedforward term passed in `AR` with gain words from PM tables at
`32E0` and `32E3` — selected by bit 12 of `DM(0468)` — and an integrator at
`DM(0652)`. Its nominal carrier term comes from the caller, which this work
did not trace. Note also that the `2000` sitting beside the `v22fc` wrapper
at PM `3BEA`/`3BEC` is an **amplitude** scaler used at `3BEF`, not a phase
increment; a frequency-shaped scan of immediates finds it and is not by
itself evidence. So for the three V.22 overlays the frequencies above are the
ones the probe drives, and the hardware result is that each overlay's own
oscillator and generator wrapper turn that phase rate into a clean quadrature
tone — not that the live modem emits those exact frequencies.

`v29fc`'s third increment, `1800`, gives 1350 Hz at 7200 and is recorded as
`low-tone` because it matches no standard carrier the way the other two do.

This remains offline batch generation: sample order is preserved, but the
batch rate does not impose the overlay's cadence, and these programs do not
drive SPORT. The 32-bit-phase sweep generators found in both INFO overlays
(`info.ana` PM `32BA` with its increment in `DM(0633):DM(0634)`, `infoh.ana`
PM `3308` with `DM(0680):DM(0681)`) are mapped but not run; they would give
arbitrary-frequency probe tones. How the live modem tasks schedule any of
these generators is still open.

## Reproduction

```sh
python3 tools/eicon_overlay_tone_replay.py list
python3 tools/eicon_overlay_tone_replay.py build --run a --count 2400 \
  --out ov_a.json --bundle ov_a.bundle
python3 tools/eicon_overlay_tone_replay.py emulate ov_a.json --out ov_a.emu.json
```

Copy the bundle, its `.meta.json` and `tools/eicon_dsp_bench_card.py` to the
card host and use the stopped-spare procedure from
[sample replay](dsp_sample_replay_20261023.md), with default DSP-only reset
release and `boot_settle=1000000`. Both runs can go in one stopped-card
window. Then:

```sh
python3 tools/eicon_overlay_tone_replay.py compare ov_a.json ov_a.card.json \
  --emulator ov_a.emu.json --out ov_a.summary.json
python3 -m unittest discover -s tests -p test_eicon_overlay_tone_replay.py -v
```

`compare` exits unsuccessfully on incomplete execution, any differing word, a
tone further than `--tolerance-hz` (default 6 Hz) from the expected one, or a
quadrature separation more than 1° from 90°. Single probes build with
`--overlay <name> --kind oscillator|native|quadrature --tone <name>`.

`tests/data/overlay_tone_card12196.json` preserves each overlay's extraction
hash, the 156–164 imported firmware words per overlay, every probe's
parameters and boot hash, and both eight-DSP captures. Nineteen tests cover
table identity across all six overlays, the family classification from the
oscillators' own words, the increment-to-frequency law, bounds, the verbatim
native patches, the unity rotation, oscillator/native equivalence, every
emulated and physical word, the measured tones, the 90° separation,
corrupted-word rejection and mismatched specification counts.
