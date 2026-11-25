# Scoping a host-serviced media ring over the card memory map, 23 October 2026

**Status update, 24 October:** the continuous DSP program and host service
loop already exist, using direct BAR3 IDMA rather than a MIPS copy loop.
Three saved 60-second runs have zero overruns/underruns but 25–41 corrupted
words each. The implementation now requires zero anomalies for a clean
result. See [current status and remaining work](sport_ring_status_20261024.md).
The architecture and missing-work sections below preserve the earlier plan.

**Measured on the card host against stopped spare SN12196.** The headline is
that the PCI memory map is nowhere near being the constraint: a 20 ms media
block costs **97 us of MMIO per 20 ms period, a 0.5% duty cycle**, and even
word-at-a-time host access would sustain 830,000 words/s against the 8,000
words/s a voice-band channel needs. The hard part is not the ring. It is the
control plane, and that belongs on the MIPS.

This corrects a caution recorded earlier the same day, that "a PCI-mediated
per-sample loop is a much harder timing problem". At 8 kHz mono it is not:
the margin is two orders of magnitude or better on every access pattern
measured (104x word-at-a-time reads, 204x on the 20 ms block duty cycle). The measurement, not the
intuition, should be relied on.

## What already runs on hardware

From [modem tone output](modem_tone_output_20261023.md) and the SPORT work:

- SPORT1 to the IPAC runs at nominally **8000 words/s**, with control `3C0F`
  selecting **external** clocks and frames and alternate sixteen-bit framing.
  No DSP clock or frame output is enabled, so **the card is the clock master**.
- The DSP transmits from a **640-word TX autobuffer** cycle and receives into
  a separate **1024-word RX ring** — 128 ms of buffer at 8000 words/s.
- **TX and RX wrap interrupts** are counted through separate DAG registers
  without disturbing arithmetic state.
- All **8,192 returned samples** matched the transmitted cycle exactly through
  the IPAC's CDA DD-to-DU loop, on all eight DSPs.
- **IDMA works while the DSP runs.** The bench's own comment records that it
  steals a DSP cycle while the timer keeps counting.
- **The host already polls card RAM while the MIPS runs our stub**, every
  bench run, for about seven seconds. The 2026-08-12 hard lock was BAR2 access
  concurrent with the *driver and stock firmware*, not with our stub.

So the DSP half of a ring, the clock, the wrap signalling and concurrent host
visibility are all proven. What does not exist is anything continuous.

## New measurements

`tools/eicon_card_mmap_rate.py`, run on the card host against a stopped card.
BAR2 is 16 MiB. Each figure is the best of five timings, with Python loop
overhead measured separately and subtracted for the single-word cases.

| Access | Cost | Rate |
|---|---:|---:|
| single 32-bit read | **1209 ns** | 830 k words/s |
| single 32-bit write (posted) | **155 ns** | 6.4 M words/s |
| 320 B block read (20 ms of 8 kHz) | 85.4 us | 3.7 MB/s |
| 320 B block write | 12.2 us | 26.3 MB/s |
| 1024 B block read / write | 264 / 38 us | 3.9 / 27.2 MB/s |

Reads cost about eight times writes, because writes are posted and reads are a
full round trip. Block throughput **falls** above 1 KiB — 4 KiB and 64 KiB both
measure 0.8 MB/s read and 4.5 MB/s write — which is unexplained and a reason
not to design around large transfers. It does not matter at 320 bytes.

Host scheduling on that machine is better than needed. A sleep-then-spin loop
holds its period with a median overshoot of **~50 us and a maximum of 60 us**,
equally at 20 ms, 5 ms and 1 ms. The ~50 us floor is loop overhead.

The MIPS side was measured by running the same probe with 600 and then 4,800
result words per DSP, twice each, reproducibly: **7.01 s versus 7.11 s**. The
0.10 s difference covers 33,600 extra IDMA word copies *and* the extra DSP
compute, giving a **lower bound of 336,000 words/s** for the stub's IDMA copy
loop. A bidirectional 8 kHz channel needs 16,000 words/s, about 5% of that
bound. The figure is coarse — 0.1 s resolved on a 7 s run dominated by the
1,000,000-iteration boot settle — and should be measured directly before being
relied on.

## The architecture the numbers imply

Two hops, with the MIPS as the real-time servicer and the host working in
blocks. This is how the stock driver is arranged, for the same reasons.

```
host  <--PCI mmap, 5-20 ms blocks-->  card SDRAM rings
                                          ^
                                          | MIPS stub service loop, IDMA
                                          v
                                      DSP DM rings
                                          ^
                                          | SPORT1 autobuffer + wrap IRQs
                                          v
                                      IPAC (CDA loop today)
```

The host does **not** need to be real-time. At a 20 ms period it spends 0.5% of
the period in MMIO and has 128 ms of RX ring before overrun.

## What is missing

**A. DSP: a non-terminating SPORT1 service program.** Today's wire program
enables SPORT1, waits out a bounded 8191 x 2048 NOP loop, disables SPORT1 and
dumps the ring. A ring needs continuous operation and host-visible read/write
indices in fixed DM cells. The wrap handlers already exist and are already
proven harmless to arithmetic state. Small.

**B. MIPS stub: a steady-state service loop.** Today the stub is strictly
linear — stages 1 through 8, poll for the completion word, copy results, exit.
It needs a loop after boot that reads the DSP's RX index over IDMA, moves new
RX words to an SDRAM ring, moves TX words from an SDRAM ring into DSP DM,
publishes both indices in the status block, and exits on a host-set flag.
Perhaps 60 to 100 more instructions in the existing in-repo assembler. Small,
and the riskiest part to get wrong, because a stub that never exits is a stub
that needs a watchdog.

**C. Host: a service loop** in place of poll-to-completion. Jitter is proven
adequate. Small.

**D. Flow control and drift.** The card is the clock master, so the host cannot
pace it and must never assume a fixed sample count per period: the published
indices have to be authoritative, with overrun and underrun counters. Over a
long run the IPAC's 8 kHz and the host's clock will drift apart and the ring
occupancy will walk; that is a measurement to make, not a thing to assume away.

**E. The line.** Stage one is necessarily the existing CDA DD-to-DU loop. A
real call needs the IPAC routed to a B-channel, which needs D-channel
signalling, which needs the stock MIPS — the conflict set out in the previous
answer. Nothing here resolves it.

## The hard part: the control plane, not the media

The media ring is cheap. The reason this does not immediately yield a
hardware-in-the-loop modem is latency of a different kind.

The `direct` backend reaches data mode by reading and writing DSP data memory
**at frame rate** — the V.34 mailbox is serviced once per datagram, about 2,400
times a second at 19200 bit/s. Routed through a 5-20 ms host service loop, each
such access would cost 12 to 48 datagram periods. That is fatal, and no amount
of PCI bandwidth fixes it, because the cost is the service period, not the
transfer.

The stock firmware solves this by putting that logic on the MIPS, which is what
tikrnl is. So hardware-in-the-loop has two honest options:

1. **Port the frame-rate control logic onto the MIPS stub.** Large, and it
   converges on reimplementing part of tikrnl.
2. **Restrict to DSP code that is autonomous per block** and needs only
   block-rate control. The routines already replayed — biquad, Hilbert
   magnitude, the complete detector, the tone generators — are all in this
   class. A full V.34 negotiation is not.

Deciding between those is the actual scoping question. The ring is a
prerequisite for either, and is worth building regardless because it is small
and it makes the choice measurable.

## Suggested order

1. **Continuous SPORT1 loopback ring, no modem.** Host writes a tone into the
   TX ring and reads it back from the RX ring through the CDA loop, for 60 s,
   with overrun and underrun counters at zero. This establishes the sustainable
   service period and the real drift, and it is a short step from the proven
   8,192-sample result. Everything else depends on these numbers.
2. **A real DSP routine on live streamed data** — the V8.ANA detector is the
   obvious candidate, since its hardware conformance is already established on
   batch input. First hardware-in-the-loop signal processing, still loopback.
3. **Decide the control-plane question** using the period measured in step 1.
4. **Only then** a real line, which needs the signalling conflict resolved.

## Caveats

Every figure here is from one host and one stopped spare. The IDMA bound is
coarse and conflated with DSP compute. The large-block throughput anomaly is
unexplained. Continuous IDMA means continuous cycle stealing from a DSP that,
unlike today's probes, would get no quiet window — the tone work deliberately
leaves one before reading completion, and a streaming modem cannot. That is
the most likely place for this design to surprise. Stock-interface takeover
remains restricted to spare adapter 5 / SN 12196 with a DSP-only-reset bundle,
and any streaming work inherits that restriction.

Reproduce the host measurements with the card stopped:

```sh
sudo python3 tools/eicon_card_mmap_rate.py 0000:02:02.0
```
