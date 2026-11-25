# SPORT1 duplex cost and extended DAG investigation, 23 October 2026

Ran new physical probes on spare adapter 5, serial 12196, PCI
`0000:02:02.0`, at `eicon420.net.cryan.nz`. Each session preserved stock
IPAC/FPGA setup, used external SPORT1 clock/frame inputs, and restored stock
firmware in an EXIT handler. No fixture settings or line activation were
changed. Primary SN1331 remained active; its answerer and SIP services were
verified running.

## Full-duplex DMA cost

`tools/eicon_sport_duplex_probe.py` selects independent linear RX I0/M0 and
TX I4/M4 pointers. It measures a fixed 32,768-NOP loop with TSCALE=0 and
IMASK=0, for four enable combinations: neither autobuffer, RX only, TX only,
and both. Eight- and sixteen-bit alternate framing repeat eight times on
every chip, giving **512 measurements, including 128 duplex cases**.

The first exploratory run sampled pointers only outside the timer reads.
One of 512 samples counted a boundary transfer outside the timer interval.
The settled probe samples each pointer on both sides of each timer read.
If a bracket moves, it supplies a lower/upper transfer bound rather than
pretending to have an exact count. All brackets in the settled physical run
were equal, so every count is exact. All 512 measurements satisfy:

```text
elapsed cycles = 32774 + RX words + TX words
```

The baseline includes the additional bracket-store instructions. Disabled
directions have no pointer movement; each enabled direction moves in every
case. In duplex cases RX transfers range from three to four, TX from four
to five, and measured overhead ranges from seven to nine cycles.
This qualifies additive one-cycle RX/TX costs under simultaneous operation.
It does not resolve the ordering of transfers requested on the same cycle:
the probe deliberately uses different indices and memory regions.

## Non-power-of-two buffers and larger strides

`eicon_sport_wire_probe.build_strides(index)` extends the original receive
probe to lengths 3, 5, 7 and 8 with M values 0, +2 and −2. Every I0–I7 is
covered by a separate program, one per chip. Each case logs the receive
ring and up to sixteen timestamp/pointer/word wrap records.

The clean repeated matrix contains **96 cases**. A second control runs the
I3 program on all eight chips, adding **96 cases**. Both runs match the
emulator's ring contents and post-wrap pointer sequences. In particular:

- M=0 keeps the pointer fixed, writes `00ff` at that address, and generates
  no wrap interrupt. Unchanged pointers do not imply no DMA occurred.
- Odd lengths with strides ±2 visit every ring element. Wraps need not land
  at the buffer base: length 5, +2 alternates wrap pointers `2801/2800`;
  length 5, −2 alternates `2803/2804`.
- Length 8, ±2 visits only the even offsets. Positive wrap lands at `2800`;
  negative wrap lands at `2806`.

Tests compare ring values and wrap sequences, without asserting an unknown
absolute oscillator phase or an identical count at the end of a CPU wait.

## An unresolved initial capture

In the initial mixed-index matrix, port `bf802810` running the I3 program
left both length-3 ±2 result blocks at the `dead` preset. Its next case,
length-5 M=0, instead contained a length-3 +2-like ring and IRQ log. The
program still reported completion. Other chips in that run behaved as
expected.

The identical I3 program then passed on every chip, including that port;
the complete mixed-index matrix also passed on repeat. The initial capture
is retained as `runs.strides_initial`, together with an explicit anomaly
record, in `tests/data/sport_duplex_card12196.json`. It is not accepted as
an emulator behavior oracle. Its cause remains unresolved: no PM readback
was performed, so program-load corruption, execution/interrupt behavior,
and capture problems have not been distinguished. If it recurs, add PM
readback verification and case-entry progress records before changing core
semantics.

## Regression coverage and reproduction

Five new evidence/probe tests preserve program hashes, validate physical
costs and wrap behavior, exercise boundary bracketing, and check bounded
completion without clocks. Three additional wire-model tests execute the
duplex/extended-stride probe programs against explicit host events and
check that two pending DMA transfers consume two processor cycles. No core
change was needed: these reproducible measurements agree with the current
implementation. All 21 SPORT probe/evidence tests and 22 ADSP tests pass.

```sh
python3 tools/eicon_sport_duplex_probe.py --out duplex.json
python3 tools/eicon_sport_wire_probe.py --kind strides --index 3 --out stride-i3.json
python3 -m unittest discover -s tests -p 'test_eicon_sport*.py' -v
python3 -m unittest discover -s tests -p 'test_adsp*.py' -v
```

Build physical bundles with `reset_hold=0x1e`, `reset_release=0x1f` and
`boot_settle=1000000`. Use the existing spare-only runner's
`--takeover-stock-interfaces`, without an IPAC fixture, and restore using
StopCard, ResetCard and CFGLib load in an EXIT handler. Raw specs, bundles
and captures are under `artifacts/dsp-conformance-20261023/sport-duplex/`.

Still open: exact simultaneous DMA priority/interrupt latency, internal
clock generation, SPORT0 with verified L1 active, multichannel operation,
and full modem integration with the wire model.
