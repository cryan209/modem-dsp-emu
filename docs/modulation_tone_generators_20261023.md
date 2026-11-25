# Modulation tone generators on the physical DSPs, 23 October 2026

**Verified on all eight DSPs of spare SN12196:** the original V8.ANA
generators produce **seven distinct modulation tones**, each landing on its
published frequency with no error, and the complete V.21 FSK generator
carries 300 bit/s data that demodulates with **zero bit errors**. All
**38,400 physical samples** match the emulator exactly. Stock firmware was
restored; primary SN1331 and its answerer/SIP services stayed active. No
BRI line, SPORT clock or analog connection is involved.

Follow-up: the six other overlays that carry this cosine table now have
their own generators measured on the same eight DSPs in
[overlay tone generators](overlay_tone_generators_20261023.md).

Follow-on from [ANSam generation and SPORT output](modem_tone_output_20261023.md),
which covered the 2100 Hz ANSam routine alone. This adds the other
generators in the same overlay and measures what each one emits.

## What the overlay actually generates

The oscillator at PM `3F2F` reads phase bits 9..14 of a 64-entry cosine
table at PM `3C80` with interleaved interpolation deltas, so one period
spans 32768 phase units and a tone is `increment * rate / 32768`. At the
overlay's own 9600 samples/s the original increment immediates are:

| Generator | Original PM | Increment | Tone |
|---|---|---:|---:|
| V.8 ANSam carrier | `3A1A` | `1C00` | 2100 Hz |
| V.8bis initiating carrier | `3A67` | `0E66` | 1080 Hz |
| V.8bis responding carrier | `3A69` | `1755` | 1750 Hz |
| V.21 channel 1 mark | `3A33` → `DM(03B7)` | `0D11` | 980 Hz |
| V.21 channel 1 space | `3A33` → `DM(03B6)` | `0FBC` | 1180 Hz |
| V.21 channel 2 mark | `3A2F` → `DM(03B7)` | `1600` | 1650 Hz |
| V.21 channel 2 space | `3A2F` → `DM(03B6)` | `18AB` | 1850 Hz |

The V.21 initializer at PM `3A29` selects the channel from **bit 3 of
`DM(3EE1)`**: set chooses channel 1 (the calling side's 980/1180), clear
chooses channel 2 (1650/1850). The same branch also selects the shaping
filter — PM `3D42` for channel 1, PM `3D7F` for channel 2. Both are
61-tap symmetric linear-phase FIRs with their peak tap at 30, so the
generator's output lags its data by exactly 30 samples.

The same 128-word cosine table appears, relocated and word-identical, in
six further overlays: `info.ana` at `2C80`, `infoh.ana` at `2F00`,
`v22v32.ana-lec` and `v22bisfc.ana` at `2E00`, `v22fc.ana` at `3100` and
`v29fc.ana` at `3200`. The `v29fc` increment `1E39` gives 1700.02 Hz at
7200 samples/s, the V.29 carrier, which is how that overlay's own rate is
identified. Those overlays are mapped but not yet run on hardware.

## The two probes

`tools/eicon_modulation_tone_replay.py` builds standalone DSP programs that
call original firmware at its original PM addresses. Both SPORTs,
autobuffers, interrupts and the timer are disabled, and all four BRI
interfaces stay in reset.

**carrier** copies the firmware's own six-word carrier phase-accumulate
sequence from PM `3A19..3A1E` and replaces **only** the increment immediate,
exactly as the biquad driver replaces only a coefficient-table address. It
then calls the unmodified oscillator and stores `MR1`, which is the word
the ANSam routine itself consumes at PM `3A21`. A test re-derives the patch
and asserts the other five words are untouched.

**fsk** calls the original initializer at PM `3A29`, then the complete
generator at PM `3A3C` once per sample — phase accumulate, oscillator, the
61-tap shaping filter over its circular buffer at `DM(03C0)`, and the two
output scalings. A host-supplied data bit vector drives `DM(03B3)` through
`I2`, so a held bit gives one pure tone and a toggling bit gives real FSK.
Gain is the original `DM(0768)`.

321 firmware words are imported in total. Nothing is reimplemented: the
only new code is the batch driver that supplies phase, data bits and
capture.

## Physical results

Eight programs, one per DSP, 4,800 samples each, in a single 7.36 s run.
Every probe has its own boot payload hash, so a changed routine or probe
cannot silently reuse this evidence.

| DSP | Probe | Expected | Measured | Peak amplitude |
|---|---|---:|---:|---:|
| `bf801800` | ANSam carrier | 2100 Hz | **2100 Hz** | 32767 |
| `bf801810` | V.8bis initiating | 1080 Hz | **1080 Hz** | −32768 |
| `bf802800` | V.8bis responding | 1750 Hz | **1750 Hz** | −32768 |
| `bf802810` | V.21 ch 1, mark held | 980 Hz | **980 Hz** | 4503 |
| `bf803800` | V.21 ch 1, space held | 1180 Hz | **1180 Hz** | 4542 |
| `bf803810` | V.21 ch 2, mark held | 1650 Hz | **1650 Hz** | 4682 |
| `bf804800` | V.21 ch 2, space held | 1850 Hz | **1850 Hz** | 4620 |
| `bf804810` | V.21 ch 1, 300 bit/s | 980/1180 Hz | **148 bits, 0 errors** | 5532 |

Frequencies come from a Hann-windowed Goertzel scan in 1 Hz steps over the
captured words; every held tone peaks on its exact published frequency.
The ANSam carrier is a clean full-scale sine: RMS 23169.7566 against the
32767/√2 = 23169.7679 of an ideal one, with the next peak more than 40 Hz
away at 1.4e-9 of its power. The V.21 tones are quieter because they pass
through the shaping filter and the `DM(0768)` gain.

The 300 bit/s probe toggles the data bit every 32 samples, which at 9600
samples/s is V.21's own rate. It is checked by demodulating rather than
scanning: mark and space energy are compared over each bit interval, after
skipping the filter's 30-sample group delay and the first interval, with a
four-sample guard band at each edge. All 148 bits are recovered correctly.
A control probe at 16 samples per bit — 600 bit/s, above what V.21 and this
shaping filter support — fails that same discriminator, so the check is not
trivially satisfied.

Hardware and emulator agree word for word on all **38,456 captured words**,
including each program's footer of final phase, both selected increments,
the selected coefficient table, the ring pointer and the last data bit.

Recordings of the actual hardware output, at the generators' native
9600 samples/s, are under
`artifacts/dsp-conformance-20261023/modulation-tone/`:

[2100 Hz ANSam](../artifacts/dsp-conformance-20261023/modulation-tone/hardware-carrier-ansam-2100hz.wav) ·
[1080 Hz V.8bis](../artifacts/dsp-conformance-20261023/modulation-tone/hardware-carrier-v8bis-initiating-1080hz.wav) ·
[1750 Hz V.8bis](../artifacts/dsp-conformance-20261023/modulation-tone/hardware-carrier-v8bis-responding-1750hz.wav) ·
[980 Hz](../artifacts/dsp-conformance-20261023/modulation-tone/hardware-v21-ch1-980hz.wav) ·
[1180 Hz](../artifacts/dsp-conformance-20261023/modulation-tone/hardware-v21-ch1-1180hz.wav) ·
[1650 Hz](../artifacts/dsp-conformance-20261023/modulation-tone/hardware-v21-ch2-1650hz.wav) ·
[1850 Hz](../artifacts/dsp-conformance-20261023/modulation-tone/hardware-v21-ch2-1850hz.wav) ·
[V.21 at 300 bit/s](../artifacts/dsp-conformance-20261023/modulation-tone/hardware-v21-ch1-300baud.wav)

## Reproduction

```sh
python3 tools/eicon_modulation_tone_replay.py build --kind matrix \
  --count 4800 --out matrix.json --bundle matrix.bundle
python3 tools/eicon_modulation_tone_replay.py emulate matrix.json \
  --out matrix.emu.json
```

Copy `matrix.bundle`, `matrix.meta.json` and `tools/eicon_dsp_bench_card.py`
to the card host and use the stopped-spare procedure from
[sample replay](dsp_sample_replay_20261023.md), with default DSP-only reset
release and `boot_settle=1000000`:

```sh
set -eu
restore() {
  /usr/lib/divas/divactrl load -c 5 -ResetCard
  /usr/lib/divas/divactrl load -CfgLib -c 5 -UseCardMap "2048,0"
}
trap restore EXIT
/usr/lib/divas/divactrl load -c 5 -StopCard
python3 /tmp/eicon_dsp_bench_card.py \
  --pci 0000:02:02.0 --adapter 5 --serial 12196 \
  /tmp/matrix.bundle --out /tmp/matrix.card.json
```

The bench script refuses any adapter that is not exactly
`0000:02:02.0` / adapter 5 / serial 12196. Then compare:

```sh
python3 tools/eicon_modulation_tone_replay.py compare matrix.json \
  matrix.card.json --emulator matrix.emu.json --out matrix.summary.json
python3 -m unittest discover -s tests -p test_eicon_modulation_tone_replay.py -v
```

`compare` exits unsuccessfully on incomplete execution, any differing word,
or a measured tone further than `--tolerance-hz` (default 4 Hz) from the
expected one. Single probes build with `--kind carrier --tone <name>` or
`--kind fsk --channel 1|2 --bit 0|1 --bit-samples <n>`; the limit is 6,000
samples per probe, so the capture cannot reach the data-bit vector.

`tests/data/modulation_tone_card12196.json` preserves the 321 original PM
words, the overlay extraction hash, every probe's parameters and boot hash,
and the actual capture from all eight DSPs. Eighteen tests cover code
provenance, the increment-to-frequency table, filter symmetry, bounds,
channel selection, every emulated and physical word, the measured
frequencies, the 300 bit/s demodulation, the 600 bit/s control, gain
scaling, corrupted-word rejection and mismatched specification counts.

## Scope

This measures what the generators emit as sample streams, computed by the
real DSPs. It is offline batch generation: sample order is preserved, but
the batch rate does not impose the 9600 samples/s cadence, and these
programs do not drive SPORT. Getting generated samples out through the
physical SPORT1/IPAC loop is covered separately for the ANSam tone in
[modem tone output](modem_tone_output_20261023.md) and is not repeated here
for the new generators. Nothing here is analog audio at a telephone
connector.

The V.8bis carriers are measured through the oscillator at the increments
its own generator uses; the full V.8bis routine at PM `3A67` is a complete
transmitter and detector with 168-word delay lines, and running it whole is
still open. Also open: the six other overlays' relocated generators, V.21
answer/guard-tone sequencing, and how the live modem task schedules any of
these.
