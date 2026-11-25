# Externally clocked SPORT emulator, 23 October 2026

Follow-up [physical duplex/stride comparisons](sport_duplex_strides_20261023.md)
qualify additive RX/TX transfer costs and extended circular wrap behavior;
three additional emulator tests pass without changing core semantics.

The core now has an opt-in single-channel SPORT wire interface, backed by the
saved spare-card measurements. Set `EICON_ADSP_SPORT_WIRE=1` before creating
or resetting the core. This also enables native register companding; timer
counting still requires `EICON_ADSP_TIMER=1`. Existing modem frame adapters
retain their default behavior. Do not combine them with the new event APIs.

## Host interface

Declarations are in `tools/adsp2181emu/adsp2181_core.h`. Port numbers are 0
and 1. Configure the processor's system, SPORT control and autobuffer
registers through firmware as usual, selecting external clocks and frames.

`adsp2181_sport_clock_edge(cpu, port, rising, dr, rfs, tfs)` drives serial
events. Rising edges produce the next MSB-first DT bit; falling edges sample
DR and framing. The result is DT in bit 0 and DT drive validity in bit 1.
Normal framing delays the first data bit; alternate framing starts with the
frame. Frame polarity bits are honored. Port disable or a format-register
write discards partial words; the CPU transmit buffer remains available.

For a host that already schedules complete words,
`adsp2181_sport_receive_word(cpu, port, word)` delivers a completed serial
word and returns the receive register value.
`adsp2181_sport_transmit_word(cpu, port)` starts a transmit word and returns
its serial bits. These bypass edge/framing simulation. The word APIs return
`-1` for a disabled/unsupported port, or an empty transmitter. The edge API
returns `-1` for unsupported configuration and zero for a disabled port.

The host advances CPU time with `adsp2181_run()` between events; the APIs do
not generate an oscillator or advance time themselves. Supported lengths
are 3–16 bits for linear data and eight bits for companded data. Native
mu-law and A-law expand to signed 14-bit and 13-bit units respectively.

## Autobuffering and interrupts

RX stores each completed word through the selected I/M/L registers. TX
fetches a word at its start when no CPU-supplied word is pending. The active
TX shift word is independent of the CPU buffer. M selection inherits the
I register's DAG bank, and circular address updates use the core's existing
DAG implementation. Memory hooks and overlays are honored.

Each DMA transfer adds one pending processor cycle. Subsequent execution
spends that cycle without retiring an instruction, while peripherals keep
clocking. Circular wrap latches the port's corresponding RX/TX interrupt;
entry waits until pending DMA cycles finish. Unbuffered words latch the
corresponding interrupt directly. IDLE and interrupt-entry cycles persist
across short `run()` calls. Reset clears partial words and pending DMA debt.
DMA is distinct from a CPU TX-register write and does not issue its callback.

## Evidence and limits

Ten tests in `tests/test_adsp_sport_wire.py` cover serial loopback, the measured
normal-frame `5aa5` → `b54b` offset, card-backed receive formats, all eight
RX/TX I selections, circular memory/pointers, one-cycle DMA stalls, timer
progress, wrap interrupt entry and partial-word reset. The original RX DAG
and TX overhead probe programs also execute against explicit host events.
Their ring contents, pointers and per-transfer cost match saved card results;
the host schedule does not claim to reproduce an unknown absolute card
oscillator phase. Hardware evidence:
[RX and clocks](sport_clock_hardware_20261023.md),
[TX and patterns](sport0_tx_patterns_20261023.md).

Validation passed: C core tests, 19 ADSP Python tests, all 35 asserted DSP
conformance suites both with the wire flag enabled and at default settings,
15 analog dispatcher tests and five direct-modulation tests. The optional
Unicorn comparison was skipped because its dependency is unavailable.

Internal clock/frame generation and SPORT0 multichannel are explicitly
rejected. Exact simultaneous DMA arbitration, external-memory wait costs,
and full modem firmware integration remain unqualified. SPORT0 shares the
single-channel implementation, but its physical board routing remains
unverified: the line was down during the silent routing scan. Repeat that
hardware control with L1 active before attributing silence to missing pins
or requiring a physical fixture.
