#!/usr/bin/env python3
"""A continuous SPORT1 media ring on the physical DSP, serviced by the host.

The DSP program never terminates: it enables SPORT1 with external clocks and
frames, takes a per-word receive interrupt into a circular DM ring, and feeds
TX1 from a second ring on every transmit interrupt. Both rings carry a sample
counter in a fixed DM cell, so the host knows exactly how far the DSP has got
and never reads a word the DSP had not finished writing.

Autobuffering is deliberately off. The proven SPORT1 work used autobuffer wrap
interrupts, which signal only once per buffer and cannot say where inside it
the DAG is; per-word interrupts on the same vectors and the same IMASK bits
give exact occupancy instead. The interrupt path is the part that was already
proven -- SPORT1 TX is vector 0x20 and RX is 0x24, both enabled by IMASK 6.

The host drives the DSP's IDMA port directly through PCI BAR3, so there is no
MIPS stub in the media path at all. Measured on the card: host IDMA reads run
at 747 kword/s and writes at up to 21 Mword/s, against the 8,000 words/s a
voice-band channel needs.

Counters live in I registers, which are 14 bits, so they wrap every 16,384
samples -- 2.048 s at 8 kHz. 16,384 is a whole number of ring lengths, so a
ring offset stays `count % ring` across the wrap, and the host extends the
counters to full width itself.
"""
import argparse
import json
from pathlib import Path

import dsp_conformance as d

RX_RING, TX_RING, RING = 0x1000, 0x1800, 1024
RX_COUNT, TX_COUNT, STOP, ALIVE = 0x0800, 0x0801, 0x0802, 0x0803
SYSTEM, SPORT1_CONTROL, SPORT1_AUTOBUF, SPORT0_AUTOBUF = 0x3FFF, 0x3FF2, 0x3FEF, 0x3FF3
SPORTS_OFF, SPORT1_ON = 0x0407, 0x0C07
SPORT1_EXTERNAL = 0x3C0F     # external clocks/frames, alternate 16-bit framing
IMASK_SPORT1 = 6             # bit 1 SPORT1 RX, bit 2 SPORT1 TX
VECTORS = 12                 # 0x00 reset then 11 interrupt vectors, 4 words each
TX_VECTOR, RX_VECTOR = 0x20, 0x24


def build(rx_ring=RX_RING, tx_ring=TX_RING, ring=RING, tx_fill=None,
          tx_autobuffer=False, rx_shadow=False, log_zeros=False):
    if ring & (ring-1) or not 16 <= ring <= 4096:
        raise ValueError('ring must be a power of two between 16 and 4096')
    for base in (rx_ring, tx_ring):
        if base % ring:
            raise ValueError('a circular buffer must be aligned to its length')
    if rx_ring+ring > tx_ring and tx_ring+ring > rx_ring:
        raise ValueError('the rings overlap')
    shadow = 0x2000
    if rx_shadow and any(base < shadow+ring and shadow < base+ring
                         for base in (rx_ring, tx_ring)):
        raise ValueError('the shadow ring overlaps a media ring')
    p = d.Program('sport1_ring')

    # Vector table: reset, then one four-word slot per interrupt. Only the two
    # SPORT1 vectors do anything; the rest return at once.
    p.emit(d.jump(0), *[d.nop()]*3)
    for _ in range(VECTORS-1):
        p.emit(d.rts(rti=True), *[d.nop()]*3)
    if len(p.pm) != VECTORS*4:
        raise AssertionError('vector table is the wrong length')

    main = len(p.pm)
    p.pm[0] = d.jump(main)
    d.preamble(p)
    # Quiet first: both SPORTs and both autobuffers off while this configures.
    p.emit(d.load_dreg('AX0', SPORTS_OFF), d.store_dreg(SYSTEM, 'AX0'),
           d.load_dreg('AX0', 0), d.store_dreg(SPORT0_AUTOBUF, 'AX0'),
           d.store_dreg(SPORT1_AUTOBUF, 'AX0'),
           d.load_dreg('AX0', SPORT1_EXTERNAL), d.store_dreg(SPORT1_CONTROL, 'AX0'))
    for reg, value in {'I0': 0, 'L0': 0, 'M0': ring if tx_autobuffer else 1,
                       'I1': rx_ring, 'L1': ring, 'M1': 1,
                       'I2': tx_ring, 'L2': ring, 'M2': 1,
                       'I3': 0, 'L3': 0, 'M3': 1,
                       'I4': 0, 'L4': 0, 'M4': 1}.items():
        p.emit(d.load_dag(reg, value))
    p.emit(d.load_group3('ICNTL', 3), d.load_group3('IFC', 0xFF),
           d.store_dag(RX_COUNT, 'I3'), d.store_dag(TX_COUNT, 'I0'))
    if rx_shadow:
        p.emit(d.load_dag('I5', shadow), d.load_dag('L5', ring), d.load_dag('M5', 1))
    if log_zeros:
        p.emit(d.load_dag('I6', 0x3000), d.load_dag('L6', 512), d.load_dag('M6', 1),
               d.load_dag('I7', 0), d.load_dag('L7', 0), d.load_dag('M7', 1),
               d.store_dag(0x0804, 'I7'))
    # Prime TX1, or the transmit interrupt that refills it never fires. These
    # are the transmit handler's own four instructions, so the counter and the
    # ring pointer stay consistent with it.
    p.emit(d.compute_dm(0, 'AY1', 2, 2), d.reg_move(3, d.GROUP3['TX1'], 0, d.DREG['AY1']),
           d.load_dag('I0', 1) if tx_autobuffer else d.modify(0, 0),
           d.store_dag(TX_COUNT, 'I0'))
    if tx_autobuffer:
        # I2/M2 supplies TX automatically; only the wrap interrupt runs.
        p.emit(d.load_dreg('AX0', (2 << 9) | (2 << 7) | 2),
               d.store_dreg(SPORT1_AUTOBUF, 'AX0'))
    p.emit(d.load_dreg('AX0', SPORT1_ON), d.store_dreg(SYSTEM, 'AX0'),
           d.load_group3('IMASK', IMASK_SPORT1))

    # Foreground: publish a liveness counter and watch the host's stop cell.
    # Nothing here touches ASTAT between the test and the branch except the
    # test itself, and neither handler writes ASTAT at all.
    loop = len(p.pm)
    p.emit(d.modify(0, 0, dag2=True), d.store_dag(ALIVE, 'I4'),
           d.dm_read_abs(STOP, 'AY0'), d.alu(d.ALU_OPS['Y'], y='AY0'),
           d.jump(loop, cond=0))

    p.emit(d.load_group3('IMASK', 0),
           d.load_dreg('AX0', SPORTS_OFF), d.store_dreg(SYSTEM, 'AX0'),
           d.load_dreg('AX0', 0), d.store_dreg(SPORT1_AUTOBUF, 'AX0'))
    p.finish()

    rx_isr = len(p.pm)
    p.emit(d.reg_move(0, d.DREG['AX1'], 3, d.GROUP3['RX1']),
           d.compute_dm(0, 'AX1', 1, 1, write=True))
    if rx_shadow:
        p.emit(d.compute_dm(0, 'AX1', 1, 1, write=True, dag2=True))
    if log_zeros:
        p.emit(d.alu(d.ALU_OPS['ABS X'], x='AX1'))
        skip = len(p.pm)
        p.emit(d.nop())
        group, reg = d.dag_index('I3')
        p.emit(d.reg_move(0, d.DREG['AX0'], group, reg),
               d.compute_dm(0, 'AX0', 2, 2, write=True, dag2=True),
               d.modify(3, 3, dag2=True), d.store_dag(0x0804, 'I7'))
        p.pm[skip] = d.jump(len(p.pm), cond=1)
    p.emit(d.modify(3, 3), d.store_dag(RX_COUNT, 'I3'), d.rts(rti=True))
    tx_isr = len(p.pm)
    if not tx_autobuffer:
        p.emit(d.compute_dm(0, 'AY1', 2, 2),
               d.reg_move(3, d.GROUP3['TX1'], 0, d.DREG['AY1']))
    p.emit(d.modify(0, 0), d.store_dag(TX_COUNT, 'I0'), d.rts(rti=True))
    p.pm[TX_VECTOR] = d.jump(tx_isr)
    p.pm[RX_VECTOR] = d.jump(rx_isr)

    fill = list(tx_fill) if tx_fill is not None else [0]*ring
    if len(fill) != ring or any(not 0 <= v <= 0xFFFF for v in fill):
        raise ValueError('tx_fill must be `ring` unsigned 16-bit words')
    p.dm_init = ([[STOP, 0], [ALIVE, 0], [RX_COUNT, 0], [TX_COUNT, 0]]
                 + [[rx_ring+n, 0] for n in range(ring)]
                 + [[tx_ring+n, v] for n, v in enumerate(fill)])
    p.next_result = RX_COUNT
    if rx_shadow:
        p.dm_init += [[shadow+n, 0xFFFF] for n in range(ring)]
    if log_zeros:
        p.dm_init += [[0x0804, 0]]+[[0x3000+n, 0xFFFF] for n in range(512)]
    d.check_encoding(p)
    spec = p.as_json()
    spec['ring'] = dict(rx_ring=rx_ring, tx_ring=tx_ring, ring=ring,
                        rx_count=RX_COUNT, tx_count=TX_COUNT, stop=STOP, alive=ALIVE,
                        counter_modulus=1 << 14, rx_isr=rx_isr, tx_isr=tx_isr,
                        sport1_control=SPORT1_EXTERNAL)
    if tx_autobuffer:
        spec['ring'].update(tx_autobuffer=True, tx_count_granularity=ring)
    if rx_shadow:
        spec['ring']['rx_shadow'] = shadow
    if log_zeros:
        spec['ring'].update(zero_log=0x3000, zero_log_size=512, zero_count=0x0804)
    return spec


def boot_words(spec):
    """(idma_address, value, is_pm) triples, in load order, PM 0 last."""
    return [[addr, value, bool(is_pm)]
            for addr, value, is_pm in d.boot_writes(spec)]


def build_rx_capture():
    """Autonomous RX-only control: one second of DM, no ISR or TX writes."""
    p = d.Program('sport1_rx_capture')
    d.preamble(p)
    p.emit(d.load_group3('IMASK', 0),
           d.load_dreg('AX0', SPORTS_OFF), d.store_dreg(SYSTEM, 'AX0'),
           d.load_dreg('AX0', 0), d.store_dreg(SPORT0_AUTOBUF, 'AX0'),
           d.load_dag('I1', 0), d.load_dag('L1', 8192), d.load_dag('M1', 1),
           d.load_dreg('AX0', SPORT1_EXTERNAL), d.store_dreg(SPORT1_CONTROL, 'AX0'),
           d.load_dreg('AX0', (1 << 4) | (1 << 2) | 1),
           d.store_dreg(SPORT1_AUTOBUF, 'AX0'),
           d.load_dreg('AX0', SPORT1_ON), d.store_dreg(SYSTEM, 'AX0'))
    loop = len(p.pm)
    p.emit(d.store_dag(0x3E00, 'I1'), d.jump(loop))
    p.dm_init = [[n, 0xFFFF] for n in range(8192)]
    p.next_result = 0x3E00
    d.check_encoding(p)
    spec = p.as_json()
    spec['capture'] = dict(base=0, size=8192, pointer=0x3E00,
                           system=SYSTEM, sports_off=SPORTS_OFF)
    return spec


def ramp(n, ring=RING, start=0):
    """The host's test pattern: word for absolute sample index n."""
    return (start+n) & 0xFFFF


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ring', type=int, default=RING)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--tx-autobuffer', action='store_true',
                    help='diagnostic: autonomous cyclic TX, requires runner --cyclic-tx')
    ap.add_argument('--rx-shadow', action='store_true', help='diagnostic: duplicate every received word')
    ap.add_argument('--log-zeros', action='store_true', help='diagnostic: log RX zero sample positions in DSP DM')
    ap.add_argument('--rx-capture', action='store_true', help='diagnostic: autonomous RX-only capture')
    args = ap.parse_args()
    spec = (build_rx_capture() if args.rx_capture else
            build(ring=args.ring, tx_autobuffer=args.tx_autobuffer, rx_shadow=args.rx_shadow,
                  log_zeros=args.log_zeros,
                  tx_fill=[ramp(n, args.ring) for n in range(args.ring)]))
    spec['boot'] = boot_words(spec)
    args.out.write_text(json.dumps(spec, indent=1)+'\n')
    print(f"{len(spec['pm'])} PM words, {len(spec['dm_init'])} DM presets, "
          f"{len(spec['boot'])} IDMA writes")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
