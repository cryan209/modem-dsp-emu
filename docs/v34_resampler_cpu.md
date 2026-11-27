# Remove repeated scalar FIR bookkeeping without changing samples

`RationalResampler.push()` now computes the valid tap interval once per output
rather than repeating coefficient/history lengths, bounds checks and index
products/subtractions for every tap. This is an execution-cost change, **not a
filter, timing, buffering or RX recovery policy change**.

## Exact operation contract

The original algorithm iterates ascending `k`, skips invalid history entries,
then performs one binary64 multiplication and addition. Valid history indices
form a contiguous interval because `index = base - k - consumed` decreases by
one. The optimized loop computes that interval, steps coefficient indices by
`up`, and decrements the history index. The **same multiplications and additions
occur in the same order**.

There is no NumPy dot product, reassociation, compensated sum, coefficient
cache, new history slice, altered tap count/window/cutoff/phase, shortened delay,
extra packet, repeated sample or larger buffer. Coefficient edits between pushes
remain visible. History trimming, output cadence, rounding/clamping around the
converter and native DSP arithmetic are unchanged. The separate Lagrange
converter is untouched.

### Proof scopes

- **108 configurations**: six ratios × three tap counts × three phase offsets
  × two cutoffs, including negative phases/startup boundaries. Every emitted
  binary64 value and every streaming state is bit-exact against the reference.
- Additional tests cover signed zero, tiny/nonfinite values and edits/truncation
  of the public coefficient list. No epsilon comparison hides altered samples.
- Existing sample-cadence, DC gain, upper-band gain and >70 dB residual-SNR tests
  remain enabled.
- Native replay: 4096 deterministic signed-word inputs per configuration,
  8000/9600 Hz codec × DM/physical SPORT TX. Every bearer output, SPORT latch,
  callback count, pending resampler/bearer state, final complete DM/PM images,
  emulated-cycle count and frame TX-write count match the old loop.

These are within-platform comparisons using the same filter design/math
library. They do not assert identical filter coefficient bits across different
operating systems, or constitute physical-peer qualification.

## Paired FIR-only CPU replay

```sh
make build
.venv/bin/python tools/benchmark_resampler.py \
  --samples 32000 --pairs 4 \
  --output artifacts/qualification/NEW-resampler-batch.json
```

`ReferenceResampler.push()` freezes the pre-optimization loop from `f6a1e0d`.
Both implementations use the unchanged constructor. Each pass sends deterministic
floating-point inputs through 6:5 and 5:6 filters with an identity codec boundary:
**no DSP, modem waveform or PPP execution**. Constructor and output hashing are
outside the timed span. Wall/thread-CPU reads surround a whole batch, never
individual samples/taps. Orders alternate reference-first/optimized-first; all
observations are retained, with no performance pass/fail threshold in tests.

| Pair | Reference CPU ms / 160 inputs | Optimized CPU ms / 160 inputs | CPU ratio |
| --- | --- | --- | --- |
| 0 | 2.208 | 0.738 | 2.992 |
| 1 | 2.234 | 0.743 | 3.005 |
| 2 | 2.196 | 0.736 | 2.985 |
| 3 | 2.221 | 0.737 | 3.013 |

All output counts, SHA-256 hashes of unrounded binary64 output and final
streaming states match. This is approximately **3× faster for this isolated
FIR workload**, not a modem-wide speedup. The roughly 1.47 ms batch difference
must not be subtracted mechanically from live quantum CPU: native interleaving,
caches, input workload and observation windows differ.

Evidence: `artifacts/qualification/resampler-batch/paired.json`; source hashes,
Python version, order, full CPU/wall values and state hashes are included.
Existing output paths are refused. Exit 0 establishes replay equality, not
latency or real-time qualification.

## Fixed live emulation observations

The plan was saved before the baseline: one unmodified call, then three
post-change calls. Each used one call, 4096 independent bytes each direction,
`--producer-detail`, zero TX underruns, ±1000 ppm and a **40 ms** maximum source
gap. RX recovery defaults stayed at 320; the experimental data-resume flag was
**not enabled**. No fault, intentional concurrent test workload or individual
retry-until-green was used.

| Case | Caller/answerer underruns | Caller/answerer max gap ms | Caller/answerer source Hz | Verdict |
| --- | --- | --- | --- | --- |
| Before | 9 / 9 | 67.779 / 84.553 | 7954.266 / 7951.495 | FAIL |
| After 001 | 2 / 1 | 69.026 / 43.298 | 7990.269 / 7993.417 | FAIL |
| After 002 | 0 / 0 | 27.324 / 28.921 | 7999.224 / 7999.642 | PASS |
| After 003 | 1 / 1 | **41.026** / 36.628 | 7994.900 / 7994.452 | FAIL |

After 003 remains a failure: 41.026 exceeds 40, and one underrun is not zero.
Only **one of three post-change calls passed strict timing**; this is not a
reproducible pacing qualification or a long-soak/reconnect result.

All four verified **32,768 total independent payload bytes**, single data entry
and no logged departure, LAPM/CHAP/IPCP, zero FCS errors/resets/RX loss, clean
exits/teardown and complete source captures without sequence/timestamp
continuity failures. All eight profile/detail receipts closed without primary
profile or GC-event drops. Ordinary trace rings dropped 384–955 old events;
all incident snapshots were retained. They are not complete ordinary-event
timelines. The three post-change sets matched all **410 runtime input fingerprints**; the baseline
differs from current runtime only in `tools/analog_kernel_dispatch.py`, as
expected. Full results and every failed budget remain intact.

Caller mean data-quantum CPU was **14.682 ms before**, and **10.135, 10.229,
10.169 ms after**. However, the untouched PRI answerer's CPU also fell from
**10.743 to 8.597–8.679 ms**; emulated work/host execution context were not
perfectly matched. Therefore the entire live CPU/underrun difference cannot be
assigned to this FIR change. The alternating isolated replay is the narrower
cost evidence. CPU/wall/cycle counts do not by themselves identify host
scheduling, CPU frequency, native instruction cost or all Python overhead.

Retained failures still show both tail types. After 001's three underruns
matched training/transition quanta of **63.471/10.883**, **31.968/9.124** and
**48.587/9.695 ms wall/thread CPU**. After 003's data incidents matched
**40.529/18.016 ms** caller and **35.536/15.257 ms** answerer quanta, with
541119/544102 emulated cycles (near their respective data means) and no observed
GC overlap. The latter are larger CPU tails plus substantial wall-minus-CPU,
not proof of uniquely host descheduling or a particular GIL/lock/I/O wait.
There is no GC subtraction or waived budget. Per-profile `.summary.json`
files retain incident correlations and raw phase/detail observations.

Evidence: `artifacts/qualification/v34-resampler-{before,after-001,after-002,after-003}/`,
`v34-resampler-analysis/summary.json`, and
`artifacts/test-results/v34-resampler-status.json`.

## Remaining boundary

The optimization removes demonstrated Python bookkeeping while preserving
values/state, but does not resolve residual CPU/non-CPU tails or prove stable
wire pacing. Required captures, GC policy, real samples, absolute deadlines,
receiver thresholds and TX cushion remain unchanged. Further sample-pump cost
isolation and fixed controls are still needed; physical SPORT, hardware timing,
full-bandwidth/TCP, transition faults and long soaks remain separately scoped.
