#!/usr/bin/env python3
"""Bounded SPORT1 full-duplex DMA cost probe; external clocks/frames only.

Run on spare SN12196 with stock interfaces retained. No internal clock or
frame outputs, no line activation, no IPAC fixture changes. RX and TX use
separate linear DAGs so pointer movement counts actual transfers.
"""
import argparse
import json
from pathlib import Path

import dsp_conformance as d

SNAPSHOT = 0x2F00


def build():
    p = d.Program('sport1_duplex_overhead')
    d.preamble(p)
    for repeat in range(8):
        for bits in (8, 16):
            for mask in range(4):
                setup = [d.load_group3('IMASK', 0), d.mode_control(0x20, False),
                         d.load_dreg('AR', 0x0407), d.store_dreg(0x3FFF, 'AR')]
                for i, address in ((0, 0x2400), (4, 0x2800)):
                    setup += [d.load_dag(f'L{i}', 0), d.load_dag(f'M{i}', 1),
                              d.load_dag(f'I{i}', address)]
                setup += [d.load_dreg('AR', 4 << 9 | mask), d.store_dreg(0x3FEF, 'AR'),
                          d.load_dreg('AR', 0x3C00 | (bits-1)), d.store_dreg(0x3FF2, 'AR'),
                          d.load_dreg('AR', 0), d.store_dreg(d.TIMER_TSCALE, 'AR'),
                          d.load_dreg('AR', 0xFFFF),
                          d.store_dreg(d.TIMER_TCOUNT, 'AR'),
                          d.store_dreg(d.TIMER_TPERIOD, 'AR'),
                          d.load_group3('CNTR', 1024), d.mode_control(0x20, True),
                          d.load_dreg('AR', 0x0C07), d.store_dreg(0x3FFF, 'AR')]
                start = len(p.pm) + len(setup) + 6
                body = [d.store_dag(SNAPSHOT, 'I0'), d.store_dag(SNAPSHOT+1, 'I4'),
                        d.dm_read_abs(d.TIMER_TCOUNT, 'MX0'),
                        d.store_dag(SNAPSHOT+2, 'I0'), d.store_dag(SNAPSHOT+3, 'I4'),
                        d.do_until(start+31, 0xE)] + [d.nop()]*32
                body += [d.store_dag(SNAPSHOT+4, 'I0'), d.store_dag(SNAPSHOT+5, 'I4'),
                         d.dm_read_abs(d.TIMER_TCOUNT, 'MX1'),
                         d.store_dag(SNAPSHOT+6, 'I0'), d.store_dag(SNAPSHOT+7, 'I4'),
                         d.mode_control(0x20, False),
                         d.load_dreg('AR', 0x0407), d.store_dreg(0x3FFF, 'AR')]
                p.case('full duplex DMA overhead', dict(repeat=repeat, bits=bits,
                       rx=bool(mask & 1), tx=bool(mask & 2), baseline=32774),
                       setup, body, ['MX0', 'MX1'])
                regs = ['AX0', 'AX1', 'MX0', 'MX1', 'AY0', 'AY1', 'MY0', 'MY1']
                p.case('pointer brackets', {}, [],
                       [d.dm_read_abs(SNAPSHOT+n, r) for n, r in enumerate(regs)], regs)
    p.finish()
    d.check_encoding(p)
    return p


def measurements(spec, words):
    """Bound transfers using pointer samples on both sides of timer reads.

    Equal brackets give an exact count. Moving brackets provide lower/upper
    bounds, so an asynchronous sampling boundary is never a false mismatch.
    """
    for case in spec['cases']:
        if case['op'] != 'full duplex DMA overhead':
            continue
        offset = case['addr'] - spec['result_base']
        before, after, r0, t0, r0b, t0b, r1, t1, r1b, t1b = words[offset:offset+10]
        rx_min, tx_min = (r1-r0b) & 65535, (t1-t0b) & 65535
        rx_max, tx_max = (r1b-r0) & 65535, (t1b-t0) & 65535
        elapsed = (before-after) & 65535
        overhead = elapsed-case['inputs']['baseline']
        yield dict(case['inputs'], rx_min=rx_min, rx_max=rx_max,
                   tx_min=tx_min, tx_max=tx_max, elapsed=elapsed,
                   overhead=overhead, exact=(rx_min == rx_max and tx_min == tx_max),
                   matches=(rx_min+tx_min <= overhead <= rx_max+tx_max))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    p = build()
    args.out.write_text(json.dumps(p.as_json(), indent=1)+'\n')
    print(f'{p.suite}: {len(p.pm)} PM words')
