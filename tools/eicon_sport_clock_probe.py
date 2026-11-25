#!/usr/bin/env python3
"""Build bounded, receive-only SPORT clock probes for the spare 4BRI card.

No internal SCLK/RFS/TFS generators and no TX writes. Run the same program
with eicon_dsp_bench's reset-release=1 and reset-release=0x1f to distinguish
DSP-only reset release from release of the four ISAC interfaces. Neither
setting configures the ISAC serial interface or proves a clock is present.
"""
import argparse
import json
from pathlib import Path

import dsp_conformance as d


def build(stock_flags=False):
    p = d.Program('sport_clock_stock_flags' if stock_flags else 'sport_clock_probe')
    # All vectors count through a separate linear DAG pointer. This cannot
    # disturb the receive ring, the foreground loop, or ASTAT.
    p.emit(d.jump(0), *[d.nop()] * 3)
    for _ in range(d.IRQ_VECTORS):
        p.emit(d.modify(2, 2), d.rts(rti=True), d.nop(), d.nop())
    p.pm[0] = d.jump(len(p.pm))
    d.preamble(p)
    if stock_flags:
        p.emit(d.load_dreg('AX0', 0x00FF), d.store_dreg(0x3FE5, 'AX0'),
               d.load_dreg('AX0', 0x70FF), d.store_dreg(0x3FE6, 'AX0'),
               0x0200CF, 0x02030F, 0x020C0F)
    for port in (0, 1):
        control = 0x3FF6 if port == 0 else 0x3FF2
        autobuffer = 0x3FF3 if port == 0 else 0x3FEF
        mask = 0x20 if port == 0 else 0x02
        for bits in (8, 16):
            for sync in (False, True):
                for buffered in (False, True):
                    setting = bits - 1 | (0x2000 if sync else 0)
                    setup = [d.load_group3('IMASK', 0),
                             d.load_dreg('AX0', 0x0407), d.store_dreg(0x3FFF, 'AX0')]
                    for n in range(8):
                        setup += [d.load_dreg('AX0', 0xBEEF),
                                  d.store_dreg(0x2800 + n, 'AX0')]
                    setup += [d.load_dag('L0', 8), d.load_dag('I0', 0x2800),
                              d.load_dag('M0', 1), d.load_dag('L2', 0),
                              d.load_dag('I2', 0), d.load_dag('M2', 1),
                              d.load_dreg('AX0', int(buffered)),
                              d.store_dreg(autobuffer, 'AX0'),
                              d.load_dreg('AX0', setting), d.store_dreg(control, 'AX0'),
                              d.load_dreg('AX0', 0x1357),
                              d.reg_move(3, 8 + 2 * port, 0, d.DREG['AX0']),
                              d.load_group3('ICNTL', 3),
                              d.load_dreg('AX0', 0xFF),
                              d.reg_move(3, d.GROUP3['IFC'], 0, d.DREG['AX0']),
                              d.load_group3('CNTR', 4096),
                              d.load_dreg('AX0', 0x1407 if port == 0 else 0x0C07),
                              d.store_dreg(0x3FFF, 'AX0'), d.load_group3('IMASK', mask)]
                    start = len(p.pm) + len(setup) + 1
                    body = [d.do_until(start + 31, 0xE)] + [d.nop()] * 32
                    body += [d.load_group3('IMASK', 0),
                             d.dm_read_abs(0x3FFF, 'AX1'),
                             d.load_dreg('AX0', 0x0407), d.store_dreg(0x3FFF, 'AX0'),
                             d.nop(), d.nop(),
                             d.dm_read_abs(control, 'MX0'),
                             d.dm_read_abs(autobuffer, 'MX1'),
                             d.reg_move(0, d.DREG['AY1'], 3, 8 + 2 * port)]
                    p.case('external clock/frame receive',
                           dict(port=port, bits=bits, sync=sync, buffered=buffered,
                                wait_nops=131072, control=setting), setup, body,
                           ['I0', 'I2', 'AX1', 'MX0', 'MX1', 'AY1'])
                    regs = ['AX0', 'AX1', 'MX0', 'MX1', 'AY0', 'AY1', 'MY0', 'MY1']
                    p.case('receive ring', dict(port=port, bits=bits, sync=sync,
                                               buffered=buffered), [],
                           [d.dm_read_abs(0x2800 + n, r) for n, r in enumerate(regs)], regs)
    # Positive control for vector counting, with SPORTs disabled. This is
    # explicitly software-forced and supplies no wire timing evidence.
    p.case('forced interrupt counter control', {},
           [d.load_dag('I2', 0), d.load_group3('IMASK', 0x22),
            d.load_dreg('AX0', 0x2200)],
           [d.reg_move(3, d.GROUP3['IFC'], 0, d.DREG['AX0'])]
           + [d.nop()] * 32 + [d.load_group3('IMASK', 0)], ['I2'])
    p.finish()
    d.check_encoding(p)
    return p


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--stock-flags', action='store_true')
    args = parser.parse_args()
    program = build(args.stock_flags)
    args.out.write_text(json.dumps(program.as_json(), indent=1) + '\n')
    print(f'{len(program.cases)} cases, {len(program.pm)} PM words')
