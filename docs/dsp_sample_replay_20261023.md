# Feeding samples to the physical DSP without BRI

Follow-up: [original modem routines](modem_routine_replay_20261023.md) now
have exact physical/emulator comparisons for the V8.ANA biquad, Hilbert
magnitude and complete detector paths, using this IDMA transport.

**Verified on spare SN12196, 23 October 2026:** preloading PCM16 through
IDMA works without a BRI line or SPORT clock. Across two runs, the eight
physical DSPs processed **12,288 samples with zero differences** from both
the emulator and an independent integer reference. The spare was restored
to stock firmware; primary SN1331 and its answerer/SIP services stayed active.

## What runs on the chip

`tools/eicon_dsp_sample_replay.py` builds a 114-word standalone DSP program.
The existing bench MIPS stub loads its program, input samples, coefficients
and zeroed history through IDMA, then writes PM0 to start execution. The
physical bundles release only DSP reset (`reset_release=1`), leaving all
four BRI interfaces in reset. This uses the stopped-card path, without
stock-interface takeover or an IPAC fixture.

The DSP disables both SPORTs, both autobuffers and all interrupts. It reads
one sample at a time from data memory and calls the same eight-tap FIR
subroutine for each sample. History uses circular DAG addressing. Signed
fractional MAC instructions form a 40-bit accumulator; `SAT MR` clips its
Q15 output before truncation to PCM16. There is no rounding step.

Default coefficients, newest sample first:

```text
-2048, 0, 10240, 16384, 10240, 0, -2048, 0
```

Each sample produces five captured words: the actual input echo, raw MR0,
raw MR1, sign-extended MR2, and clipped PCM16 output. A sixteen-word footer
captures pointer state, the complete history, and disabled SPORT/autobuffer
configuration. An independent Python integer convolution checks all words,
including state, rather than trusting agreement between two executions of
the same DSP program alone.

## Physical results

| Run | Workload | Result |
|---|---|---|
| Initial proof | Same 512-sample composite on all eight chips | 4,096 samples, exact match |
| Extended matrix | Eight 1,024-sample programs | 8,192 samples, exact match |

The composite includes silence, an impulse, positive/negative steps, a ramp,
alternating rails, deterministic noise and a periodic tone. The matrix adds
four active, unmodified PCM16 windows from captured modem audio:

| Source under `artifacts/retrain-20261013-fixed/` | Offset in samples | Rate |
|---|---:|---:|
| `caller.wav` | 80,000 | 8,000 Hz |
| `answerer.wav` | 64,000 | 8,000 Hz |
| `caller.wav` | 176,000 | 8,000 Hz |
| `answerer.wav` | 192,000 | 8,000 Hz |

Every window contains more than 100 distinct sample values and peaks above
2,000; silence was excluded. Additional matrix controls use an eight-point
average, a single-tap coefficient of 32767, and a single-tap coefficient of
−32768. Together the captures include 244 positive and 40 negative clipping
events, all matching the reference.

The initial stopped-card restore attempted a redundant StopCard after the
bench had left the card ready. That command failed and interrupted the
first restore handler; reset/reload was performed immediately and verified.
The matrix handler tolerated that already-stopped condition and completed
reset/reload. For this stopped-card workflow, restore directly with
ResetCard followed by CFGLib load; do not make a redundant StopCard success
a prerequisite for restoration.

## Replaying another input

The tool accepts mono, uncompressed 16-bit WAV or raw signed little-endian
PCM16. WAV supplies the rate; `--rate` supplies metadata for raw PCM. No
resampling, gain conversion or companding is performed. Offset and count
are in samples, with a maximum of 1,024 samples per batch.

```sh
python3 tools/eicon_dsp_sample_replay.py build \
  --input input.wav --offset 80000 --count 1024 --out replay.json
python3 tools/eicon_dsp_sample_replay.py emulate replay.json --out replay.emu.json
python3 tools/eicon_dsp_bench.py bundle replay.json --out replay.bundle
```

Omit `--input` for the deterministic composite. Set alternate signed Q15
coefficients with, for example, `--coefficients=4096,4096,4096,4096,4096,4096,4096,4096`.
The recorded physical runs use `build_bundle(..., boot_settle=1000000)`.

Copy the bundle, its `.meta.json` and `eicon_dsp_bench_card.py` to the card
host. The tested spare-only procedure is:

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
  /tmp/replay.bundle --out /tmp/replay.card.json
```

Run that procedure as root, retrieve the resulting card JSON, then compare:

```sh
python3 tools/eicon_dsp_sample_replay.py compare replay.json replay.card.json \
  --emulator replay.emu.json --out replay.summary.json
python3 -m unittest discover -s tests -p test_eicon_dsp_sample_replay.py -v
```

Comparison exits unsuccessfully on incomplete execution or any mismatch and
identifies the first differing sample/field. A JSON list of eight specs can
also be bundled and compared, one input per chip. Seven tests cover import,
bounds, partial history wraps, saturation, mismatch detection, full boot
payload hashes and all recorded physical/emulator/reference comparisons.
`tests/data/sample_replay_card12196.json` preserves the actual input vectors,
coefficients, boot hashes and hardware output. Raw specs, bundles, emulator
results and summaries are in
`artifacts/dsp-conformance-20261023/sample-replay/`.

## Scope and next step

This proves a reusable route for testing DSP computation on actual hardware
without BRI. It is an offline batch replay: the sample order is preserved,
but the rate does not impose real-time cadence, and history resets for each
batch. It does not qualify physical SPORT timing or the stock modem's sample
dispatcher, initialization or processing routines. The filter is the small
standalone routine described above, not extracted modem firmware.

The next firmware comparison can keep this IDMA input/capture transport and
replace the filter call with a selected modem routine, supplying its required
state and native sample units explicitly. Continuous streaming and state
handoff between batches remain to be implemented.
