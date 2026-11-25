# SPORT0 routing and SPORT1 transmit/pattern tests, 23 October 2026

Real hardware: spare adapter 5, serial 12196, PCI `0000:02:02.0`, on
`eicon420.net.cryan.nz`. The owner confirmed remote access only.

**SPORT1 now has nonconstant transmit/receive-back evidence, calibrated
framing and measured TX autobuffer overhead. SPORT0 remains unqualified:**
528 configurations produced no receive activity, with a live SPORT1 control
in every configuration. This is no-activity evidence, not a pass for SPORT0
or multichannel operation. No internal DSP clock/frame outputs were enabled.

## Local IOM fixture

The stock MIPS accessors at `0x8016fd28` (read) and `0x8016fd7c` (write)
establish the IPAC register mapping: byte accesses to
`0xbf80n000 + 4 * register`, n=1..4. Configuration snapshots on all four
IPACs matched their expected IOM slot/control layouts. The stock setup uses
SDS1 timeslot 0 (`SDS1_CR=0x80`), and transceiver B1/B2 output slots
`0x80/0x81` on DU.

The runner now offers two spare-only `--ipac-fixture` modes after halting
MIPS with the existing stock-interface takeover:

- `inject`: stop the stock DSP transmitters first, disable transceiver
  receive output for B1/B2, and use controller-data-access (CDA) outputs to
  drive DD slots 0/1 with `3c/c3` and DU slots 0/1 with `5a/a5`.
  CDA input sampling stays disabled. Static DD injection is restricted to
  the known RX-only program suites so it cannot contend with the TX probe.
- `loop`: disable the same transceiver B1/B2 output, leave CDA DD output
  disabled, and configure CDA20/21 to sample DD and return it on DU in the
  same two slots. The DSP supplies the transmitted pattern. CDA2_CR is
  `0x1e`; the relevant output slots are `0x80/0x81`.

Both modes preserve D-channel control, clock configuration and TE/NT mode.
The IPACs remain released when DSP reset is asserted. They are test settings,
not persistent CFGLib changes. Every session ends by stopping, resetting and
reloading the stock firmware.

The register definitions and local switching mechanism come from the
[manufacturer's IPAC-X data sheet, sections 3.7.1.1 and 4.4](https://media.digikey.com/pdf/Data%20Sheets/Infineon%20PDFs/IPAC-X.pdf).
The DSP framing definitions are in the local hardware reference,
`docs/3110043388x_hardware/8xsports.pdf`, pages 5-17 through 5-19.

## Nonconstant receive and the framing correction

With the injection fixture, SPORT1 hears the DU pattern `0x5aa5` on every
chip. SPORT0 still has no transfers. This identifies a working data source
in addition to the clock/frame source established in the preceding session.

The all-one idle word had hidden a framing mismatch. With normal receive
framing (`RFSW=0`), the eight-bit sample is `0xb5` and the sixteen-bit
sample is `0xb54b`: the intended pattern has shifted left one bit and picks
up an idle one at the end. With alternate framing (`RFSW=1`, control
`0x300f` for sixteen bits), the exact `0x5aa5` is received. The stock BRI
kernel also selects alternate receive/transmit framing (`0x3c0f`).

`tools/eicon_sport_pattern_probe.py` sweeps 3..16-bit unsigned receive and
the three other eight-bit formats in both framing modes: 34 cases on each
of eight chips. Active and settled reads agree. For lengths 8..16, all
chips return the corresponding leading bits of `0x5aa5` in alternate mode
and of `0xb54b` in normal mode. Shorter words can retrigger during the
SDS pulse; their raw results are retained without claiming a single
unambiguous frame alignment.

The nonconstant eight-bit formats also agree across all chips:

| Framing / received byte | Signed RX | mu-law RX | A-law RX |
|---|---|---|---|
| Alternate / `0x5a` | `0x005a` | `0xff75` | `0xffe1` |
| Normal / `0xb5` | `0xffb5` | `0x032f` | `0x0420` |

The earlier idle formatting test is still valid for its all-one input, but
it did not establish correct bit alignment. Use alternate framing with
this IPAC fixture for correctly aligned nonconstant data.

## Transmit and receive-back

`tools/eicon_sport_tx_probe.py` runs one program for each of the eight TX
index-register selections. RX uses an index in the other DAG. The TX ring
contains eight words; both normal/alternate framing and 8/16-bit words are
tested with no CPU TX write, a seed before enabling SPORT1, or a seed
after enabling it: 96 cases total.

The calibrated sixteen-bit alternate-framing cases return this exact
cyclic sequence through the physical DD-to-DU loop:

```text
11ee 22dd 44bb 8877 55aa aa55 3cc3 c33c
```

All 24 calibrated cases (eight TX indices, three seed choices) have at
least 27 recorded steady words matching the sequence after omitting four
startup words. Each records four TX buffer-wrap interrupts. The program
using each index runs on one chip; this is coverage of every index, not a
full eight-index-by-eight-chip cross-product.

The first eight-bit case on each freshly reset DSP starts transmitting
without a CPU TX write when TX autobuffering is enabled. Its returned
pattern proves that an interrupt or pointer advance was not mistaken for
data transmission. Later cases demonstrate continued operation with the
two explicit seed choices. Initial FIFO contents, exact first-edge timing
and the number of startup words vary with enable phase; the test does not
claim a deterministic first transmitted word at a specified cycle.

Normal sixteen-bit framing corrupts some low bits in this loop. The exact
alternate-framing comparison above is the settled data-integrity evidence.
The initial exploratory `tx-start.json` calls the framing field `inverted`;
that name was wrong. The bit difference is normal versus alternate framing,
not polarity inversion. The settled `tx-i*.json` specs use `alternate`.

## TX transfer overhead

A separate TX-only autobuffer probe leaves IMASK=0 and RX autobuffering
disabled. It samples a linear TX pointer around a fixed 32,768-NOP loop
and samples TCOUNT at TSCALE=0. Framed/unframed and buffered/unbuffered
cases repeat four times on every chip: 128 measurements.

Every measurement satisfies:

```text
elapsed DSP cycles = 32770 + (TX index after - TX index before)
```

All unbuffered baselines are 32,770 cycles. Each counted TX autobuffer
transfer therefore adds exactly one cycle, matching the independently
measured receive cost. No interrupt service cost is included.

## SPORT0 and multichannel result

Restored the BRI kernel's exact flag initialization before retrying the
original receive matrix: PFDATA `0x00ff`, PFTYPE `0x70ff`, and its three
FL output instructions from PM `0x05e6..0x05e8`. SPORT0 still has no
receive transfers on any chip.

Then ran `build_sport0()` with both SPORTs enabled, the stock flags, and a
simultaneous SPORT1 receive ring. Each chip tests:

- Two unframed single-channel word lengths.
- 24- and 32-word multichannel frames, every MFD value 0..15, and all
  receive slots enabled versus none enabled. Transmit slot masks are zero.

That is 66 configurations per chip, 528 total. In every case, SPORT0's
ring stays `0xbeef`, its receive index stays `0x2800` and its wrap counter
stays zero. SPORT1's ring receives data in all 528 cases. The card is
executing and the retained IPAC clock source is alive; changing the
SPORT0 framing mode or restoring these flags is insufficient to obtain
an observable receive path.

The owner subsequently confirmed that the line was not up. These probes
did not establish whether SPORT0 clock/frame routing depends on line
activation. The next control is therefore a repeat with verified L1 up,
before concluding that a physical fixture is required. No USB logic
analyser/serial fixture is attached to the host, and access is remote only.
The results do not distinguish an unconnected SPORT0 from gated
clock/frame routing. They do not establish a SPORT0 fault or qualify
SPORT0 transmit or multichannel slot selection.

## Evidence and verification

Raw specs, bundles and results are in
`artifacts/dsp-conformance-20261023/sport-rest/`. The checked-in
`tests/data/sport_rest_card12196.json` contains program hashes and actual
card results for the settled tests. Eight new tests preserve those hashes,
validate returned patterns/overhead and the live SPORT1 controls, check
that programs terminate without a clock, and check the static-injection
TX exclusion before card access. The preceding eight SPORT tests also
pass. These validate physical evidence; they do not add an emulator serial
clock or autobuffer implementation.

Example builds:

```sh
python3 tools/eicon_sport_pattern_probe.py --out pattern-rx.json
python3 tools/eicon_sport_pattern_probe.py --sport0 --out sport0-mc.json
python3 tools/eicon_sport_tx_probe.py --index 0 --out tx-i0.json
python3 tools/eicon_sport_tx_probe.py --overhead --out tx-overhead.json
python3 tools/eicon_dsp_bench.py bundle tx-i0.json --reset-release 0x1f --preserve-isacs --out tx-i0.bundle
```

The recorded bundles use `boot_settle=1000000`. Use the card runner with
`--takeover-stock-interfaces --ipac-fixture inject` for the pattern RX test,
`--takeover-stock-interfaces --ipac-fixture loop` for TX tests, and plain
takeover for the SPORT0 routing scan. All use the exact spare-card identity
checks. Do not call StopCard before takeover; always stop/reset/reload stock
firmware in an EXIT/finally handler afterwards.
