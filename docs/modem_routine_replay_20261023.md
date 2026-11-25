# Original modem routines on hardware and emulator, 23 October 2026

Replayed three original V8.ANA processing paths on spare SN12196 without BRI:
the envelope biquad, Hilbert magnitude path and complete detector routine.
**All 4,096 routine calls
match the emulator exactly**, including outputs, accumulators, flags,
pointers and feedback/history state. No core arithmetic change was needed.
The investigation did find and fix an incorrect history-buffer setup in the
older emulator magnitude bench.

Hardware: adapter 5, PCI `0000:02:02.0`, on `eicon420.net.cryan.nz`. Each
session stopped only the spare, loaded samples/program through the MIPS
IDMA bench, held the BRI interfaces in reset, then reset/reloaded stock
firmware. Primary SN1331 and its answerer/SIP services remained active.

## Original instructions and defined initial state

The new `tools/eicon_modem_routine_replay.py` imports the routines and
coefficient words from the extracted **V8.ANA 1.00 build 109-789** overlay.
They retain their original PM addresses and instruction words. A small new
driver supplies samples and captures state; it does not implement a replacement
filter. Both SPORTs, autobuffers, interrupts and timer are disabled. All
processing is offline, with zeroed initial history and fractional MAC mode.

| Path | Original PM | Initialization and capture |
|---|---|---|
| Envelope biquad | `3f1d..3f29`, called through `3ef5..3ef9` | Four state words at `07ac..07af`; original six-word coefficient table; 16 trace words per sample |
| Hilbert magnitude | `3ede..3ee8`, `39bd..39d4`, `3f15..3f1c` | Original initializer `39ba..39bc`; 29-word history at `04a0`; 44 trace words per sample |
| Complete detector routine | `3ede..3f14` plus the same helpers, biquad and queue packer `3f2a..3f2e` / `3c32..3c37` | Natural 15-call countdown, threshold 200, defined level/counter/phase state, four-word queue at `0600`; 56 trace words per sample |

The biquad driver copies the five original call-site instructions. Only its
I4 address field changes when selecting another original coefficient table.
It captures the input, output, MR0/MR1/MR2, SR0/SR1, MX0/MX1, all four state
words, ASTAT and I0/I4 after every call.

The magnitude driver holds `DM(3eda)=0` so the input gate accepts the sample.
It sets `DM(07be)=7fff` before each call, making the original conditional RTS
at `3ee8` return after magnitude calculation. Thus this test exercises the
input gate, delayed sample, stride-two Hilbert FIR and squared-magnitude
calculation, while excluding the downstream decimated biquad/integrator.
Every sample records input, magnitude, MX0/MX1, SR0/SR1, the complete MR,
history pointer, ASTAT, I0/I4, gate/countdown and all 29 history words.

The complete detector mode lets the original countdown run, rather than
resetting it each sample. Every fifteenth input reaches the original phase
update, queue write, envelope biquad and leaky integrator/threshold counter.
It captures the full magnitude trace plus biquad state, level, counter,
phase, queue pointer and all four queue words. The queue pointer is explicitly
initialized to a valid four-word buffer at `0600`; no host consumer runs.

Five footer words record input/output pointers and disabled serial control.
The physical captures total **135,288 words**, with no mismatch. Program,
coefficient and sample boot writes are hashed together, so a changed input
or routine cannot silently reuse the physical evidence.

## Physical cases

Eight DSPs each execute one 256-sample biquad program:

- Table `3d10`: impulse 7648, steady 7648, a 15 Hz ±20% envelope at 640
  evaluations/s, and an active captured caller PCM window.
- Tables `3d04`, `3d16`, `3d1c`, `3d22`: the deterministic composite input.

That gives 2,048 biquad calls. Hardware reproduces the previously observed
steady output of 44 for input 7648 with table `3d10`. The envelope control
reaches a peak magnitude of 1568 over the latter half of its 256 samples.
This checks arithmetic under a defined evaluation rate; it does not establish
which rate the live modem dispatcher supplies.

The magnitude matrix adds eight 128-sample programs: impulse, silence,
2100/2400 Hz tones labelled at 9600 samples/s, captured caller and answerer
PCM, noise/tone, and full-scale rail steps. That gives 1,024 calls. The
coefficient table is the original fourteen PM words at `3c64..3c71`.

The complete detector matrix uses those same eight 128-sample input vectors,
adding 1,024 calls and **64 decimated processing updates**. It uses threshold
200, zero initial level and initial countdown 15. The silence control starts
its threshold counter at 3; the other cases start at zero. Physical traces
verify all three counter actions: increment above threshold, decrement below
threshold and clamp at zero. Level/counter state changes only at decimated
updates, and the four-word queue pointer advances on those same updates.

Each program runs on one chip. This covers all eight chips and all five
biquad tables, rather than a complete table-by-chip cross-product.

## Correcting the older magnitude bench

`MagnitudeBench.reset_chain()` previously assigned `DM(073e)=073f` and
cleared the following memory. `073e` is a pointer cell, not an inline ring
header. The firmware explicitly initializes it with:

```text
39ba: AR = 04a0
39bb: DM(073e) = AR
39bc: RTS
```

The corrected bench clears the actual 29-word buffer at `04a0`, executes
this initializer, and verifies the resulting pointer. It now matches every
physical magnitude output, pointer and history word in the new matrix.
The existing biquad bench also matches all physical outputs/state words.
The fix affects the standalone instrument, not the modem dispatcher or
emulator arithmetic. The earlier descriptions of the ring as being *at*
`073e` should be read as *pointed to by* that cell. Earlier standalone
magnitude measurements made with the incorrect reset require remeasurement;
live traces and the separate biquad bench are unaffected by that setup fix.

## Reuse and regression evidence

```sh
python3 tools/eicon_modem_routine_replay.py build \
  --kind biquad --table 0x3d10 --input input.wav --count 256 --out biquad.json
python3 tools/eicon_modem_routine_replay.py build \
  --kind magnitude --input input.wav --count 128 --out magnitude.json
python3 tools/eicon_modem_routine_replay.py build \
  --kind detector --input input.wav --count 128 --out detector.json
python3 tools/eicon_modem_routine_replay.py emulate biquad.json --out biquad.emu.json
python3 tools/eicon_dsp_bench.py bundle biquad.json --out biquad.bundle
```

The existing stopped-spare procedure is documented in
[sample replay](dsp_sample_replay_20261023.md). These runs used default
DSP-only reset release and `boot_settle=1000000`. After retrieving card JSON:

```sh
python3 tools/eicon_modem_routine_replay.py compare biquad.json biquad.card.json \
  --emulator biquad.emu.json --out biquad.summary.json
python3 -m unittest discover -s tests -p test_eicon_modem_routine_replay.py -v
python3 -m unittest discover -s tests -p test_v8_envelope_filter.py -v
```

Input is unchanged signed PCM16, without automatic resampling or native-unit
conversion. Rates are metadata for these offline calls. Limits are 256
samples per biquad batch and 128 per magnitude/detector batch. Detector mode
also accepts `--initial-counter` for explicit counter-state controls. The default demo
begins with silence; use the saved matrices for the impulse/tone controls.

`tests/data/modem_routines_card12196.json` preserves the required original
PM words, extraction hash, input vectors, boot payload hashes and actual
hardware results. Eight new tests validate code provenance, every trace
word, partial batches, mismatch reporting, decimation/counter behavior and
both older bench interfaces. These eight tests, the seven existing envelope
filter tests and seven earlier sample-replay tests pass.
The physical evidence tests can rebuild from their saved firmware subset;
local-extraction/older-bench tests skip if that overlay is unavailable.
Raw specs, bundles, emulator results and summaries remain under
`artifacts/dsp-conformance-20261023/modem-routines/`.

Still open: live sample scheduling, state selection/reset by the modem task,
long-duration detector behavior and other modem processing paths. These
isolated results qualify the three tested paths under the stated initial state and inputs; they do not
establish full modem conformance or diagnose a call failure.
