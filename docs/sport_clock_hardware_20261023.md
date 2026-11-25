# SPORT clock investigation on SN 12196, 23 October 2026

Later follow-up: [SPORT0 routing, transmit and known-pattern results](sport0_tx_patterns_20261023.md).
SPORT1 transmit and nonconstant receive are now measured, including a
framing correction that all-one idle data hid. SPORT0/multichannel remain
unqualified after the broader routing scan.

Ran real-card probes on `eicon420.net.cryan.nz`, adapter 5, PCI
`0000:02:02.0`, serial 12196. **SPORT1 physical receive, circular
autobuffering, wrap interrupts and one-cycle transfer overhead are now
measured.** Preserving the stock IPAC/FPGA configuration supplies a working
receive clock/frame source. SPORT0 remains silent. The later follow-up
above adds transmit and nonconstant-data evidence; multichannel operation
still lacks a working SPORT0 source.

The first reset-release-only experiments below saw no transfers. A later
stock-interface takeover resolved that blocker; see the final results below.

`tools/eicon_sport_clock_probe.py` builds a 1,741-word program with 16
receive configurations: both SPORTs, 8/16-bit words, required/unframed RX,
and autobuffering off/on. Each configuration waits for 131,072 NOPs,
reseeds the receive ring, counts RX interrupts through a separate DAG,
and reads back the SPORT control, autobuffer control and enabled system
control registers. RX is seeded with `0x1357` to detect actual reception
even without autobuffering. No TX register is written and no internal
clock/frame generator is enabled.

The same program ran on all eight chips in two independent boot sessions:

| Reset release | Receive configurations | RX interrupts | RX register | Ring / I0 |
|---|---:|---:|---|---|
| `0x01`: DSPs only | 128 | 0 | retains `0x1357` | all `0xbeef` / `0x2800` |
| `0x1f`: DSPs and four ISACs | 128 | 0 | retains `0x1357` | all `0xbeef` / `0x2800` |

All sixteen chip runs completed with `0x600d`, and every chip's software
positive control counted exactly two forced interrupts. System enable and
autobuffer settings read back as written. SPORT control bits 0..14 match;
SPORT1 bit 15 reads high on seven chips and low on `bf802800` in both
runs. That readback difference is recorded without interpreting its cause.

The probe's interrupt control validates the counter and mask path only;
it supplies no evidence for a physical transfer. With autobuffering off,
no receive IRQ and an unchanged RX latch also exclude ring alignment or
pointer wrap as the sole explanation for the missing observed transfers.

## Reproducing the probe

```sh
python3 tools/eicon_sport_clock_probe.py --out clock.json
python3 tools/dsp_conformance.py emulate clock.json --out clock.emu.json
python3 tools/eicon_dsp_bench.py bundle clock.json --out dsp-only.bundle
python3 tools/eicon_dsp_bench.py bundle clock.json --reset-release 0x1f --out isac-release.bundle
```

Copy each bundle and its `.meta.json` plus `eicon_dsp_bench_card.py` to the
host. Use the existing stopped-card runner with PCI/adapter/serial checks.
Restore the stock firmware in an EXIT trap, including on probe failure.
`--reset-release` defaults to the previous DSP-only setting; only `1` and
`0x1f` are accepted. The reset option releases interfaces; it does not
configure their serial clock, frame sync, or routing.

Raw specs, bundles, emulator and card results, and `summary.json` with the
program hash are in `artifacts/dsp-conformance-20261023/sport/`.
The generated program finishes in the emulator, its forced counter control
returns two, all three new probe tests pass, and both existing conformance
tests pass (the optional Unicorn boot-path test is skipped: dependency
unavailable in the test interpreter). These exploratory results were not
added to the asserted emulator fixture.

## Prerequisite identified after the first runs

The next useful measurement needs either stock-equivalent IPAC/FPGA serial
clock/frame configuration under the bare-metal supervisor, or a fixture
with independently verified SCLK and RFS at the DSP. The stock MIPS code
releases each ISAC at `0x801716a8..0x801716d4`; this experiment reproduces
release only, not its full interface initialization. Do not substitute an
unverified internal-clock setting on potentially shared clock nets.

Once RX interrupts and known data are observable, use nonconstant patterns
to qualify wire formatting, then vary DAG index/modify selection and buffer
length to measure wrap interrupts and transfer overhead. TX start/framing
and multichannel slot selection still require separate physical evidence.

After testing, adapter 5 was reset and restored through CFGLib; its state
was checked as active. SN 1331 was not stopped. Both
`bri-test-answerer.service` and `diva-sip-gateway.service` were active after
the run.

## Working stock-interface takeover

The driver's `stop_qBri_hardware` resets all IPACs/DSPs and clears
`MQ2_IPACX_CONFIG` (`kernel/s_4bri.c`). The previous StopCard-based tests
therefore discarded the running interface setup before starting the DSPs.
Simply releasing resets does not reconstruct it.

The new `--takeover-stock-interfaces` runner option halts only the MIPS
and clears the host interrupt request on spare adapter 5. It preserves the
IPAC/FPGA setup. The bundle asserts reset mask `0x1e`, then releases
`0x1f`, resetting the DSPs while leaving all four IPACs released throughout.
Before each takeover, the board reported MIPS control `3`, reset mask `31`,
and IPACX TE/NT configuration `15`. No DSP drives an internal SCLK, RFS,
or TFS, and none writes TX. This is a working retained setup, not a
standalone reconstruction of IPAC initialization.

The first takeover repeated the original 16-case receive matrix on all
eight chips. SPORT0 still had no transfers. SPORT1 received the all-one
idle word, filled its ring and generated interrupts. The raw result is
`sport-clock-stock-interfaces.card.json` in the artifact directory.
Its exploratory `stock_interfaces.dsps` IDMA reads are **not valid control
register snapshots**: the ADSP hardware reference excludes memory-mapped
control registers from IDMA access. The runner no longer attempts them.
All actual SPORT configuration readbacks used in the evidence are DSP
instructions reading their own control registers.

## Physical SPORT1 measurements

`tools/eicon_sport_wire_probe.py` builds bounded RX-only programs. Each
program was first checked to finish in the emulator even with no clock.
The hardware runs then preserved the stock interfaces as above.

- **DAG and wrap:** tested all eight index registers, paired modify
  registers within their DAG, lengths 2/4/8, and strides +1/-1: 48
  configurations. Only the selected ring words change from `0xbeef` to
  `0x00ff`. At every recorded wrap interrupt the pointer is the ring base
  for +1 and base + length - 1 for -1. Both a short and a longer capture
  agree on data and pointer behavior.
- **Relative frame timing:** the longer capture records timer stamps at
  wrap IRQ entry with TSCALE=1 (two DSP cycles per tick). Settled interval
  ranges are 8,191..8,192 ticks for a two-word ring,
  16,383..16,384 for four words, and 32,766..32,767 for eight words.
  That is approximately 8,192 DSP cycles per framed received word.
  One first interval in the longer run is 32,681 ticks; later intervals
  settle. Startup phase/first-edge timing is not qualified. These are
  cycle-relative measurements, not a scope measurement of absolute Hz
  or pin-edge setup/hold margins.
- **Transfer overhead:** four chips each run sixteen cases with IMASK=0,
  framed/unframed receive and autobuffering off/on. A linear I0 counts
  transfers; timer stamps surround a fixed 32,768-NOP loop. In all 64
  cases, elapsed DSP cycles are exactly `32770 + transfers`. The matched
  unbuffered baselines are all 32,770 cycles. **Each observed receive
  autobuffer transfer costs exactly one extra DSP cycle.**
- **Physical idle-word formatting:** a final all-eight-chip run checks
  two active RX reads and a settled read after disabling SPORT1. All
  agree: unsigned 8-bit `0x00ff`; signed 8-bit `0xffff`; either 16-bit
  format `0xffff`; mu-law `0x0000`; A-law `0x006a`. Control readbacks
  match the chosen format in bits 0..14. An earlier immediate-after-stop
  probe had one inconsistent companding sample; it is retained as
  exploratory evidence and is not used as the settled formatting oracle.

All hardware sessions completed on all eight chips, and all stopped/reloaded
the spare card through an EXIT trap. The final adapter state was active;
both primary-card services were active. SN 1331 was never stopped.

The checked-in `tests/data/sport_wire_card12196.json` preserves the settled
card outputs and program hashes. Eight probe/evidence tests pass, and the
existing conformance tests still pass (optional Unicorn test skipped).
The new tests validate captured physical evidence and byte-identical probe
programs; they do not claim the emulator implements serial clocks or
autobuffering. The original 35-suite emulator oracle is unchanged.

Raw bundles/specs/results remain under
`artifacts/dsp-conformance-20261023/sport/`: `wire-dag.*` is the first
capture, `wire-long.*` the longer capture, `wire-timing.*` the overhead
run, and `wire-format-settled.*` the final formatting check.

To build a DAG program:

```sh
python3 tools/eicon_sport_wire_probe.py --kind dag --index 0 --out wire.json
python3 tools/eicon_dsp_bench.py bundle wire.json --reset-release 0x1f --preserve-isacs --out wire.bundle
```

Use `--kind overhead` or `--kind format` for the other programs. For timing
captures the recorded bundles use `build_bundle(..., boot_settle=5000000)`
to keep IDMA polling away from the executing probes. On the fully started
spare card, use the runner's `--takeover-stock-interfaces` option **without
calling StopCard first**. Always stop/reset/reload stock firmware afterwards
in a finally/EXIT handler. The mode is hard-restricted to
`0000:02:02.0`, adapter 5, serial 12196 and the matching reset masks.

The receive clock/fixture prerequisite is closed for SPORT1 on this board.
Remaining physical work is nonconstant receive patterns, other strides
and alignments, precise first-frame timing, transmit start/framing, SPORT0
clock routing and multichannel slot selection. These measurements are now
enough to guide a receive-autobuffer emulator implementation, without
inventing its per-transfer overhead or wrap-IRQ behavior.
