#!/usr/bin/env python3
"""Known IPAC-injected pattern: qualify RX framing, lengths and companding."""
import argparse
import json
from pathlib import Path

import dsp_conformance as d


def build():
    p = d.Program('sport_pattern_rx')
    d.preamble(p)
    for alternate in (False, True):
        for bits, dtype in ([(n, 0) for n in range(3, 17)] + [(8, x) for x in (1, 2, 3)]):
            setup = [d.load_group3('IMASK', 0),
                     d.load_dreg('AX0', 0x0407), d.store_dreg(0x3FFF, 'AX0'),
                     d.load_dreg('AX0', 0), d.store_dreg(0x3FEF, 'AX0'),
                     d.load_dreg('AX0', (0x3000 if alternate else 0x2000) | dtype<<4 | bits-1),
                     d.store_dreg(0x3FF2, 'AX0'),
                     d.load_group3('CNTR', 4096),
                     d.load_dreg('AX0', 0x0C07), d.store_dreg(0x3FFF, 'AX0')]
            start = len(p.pm)+len(setup)+1
            body = [d.do_until(start+15, 0xE)]+[d.nop()]*16
            body += [d.reg_move(0, d.DREG['AX1'], 3, 10),
                     d.dm_read_abs(0x3FF2, 'MX0'),
                     d.load_dreg('AX0', 0x0407), d.store_dreg(0x3FFF, 'AX0')]
            body += [d.nop()]*16+[d.reg_move(0, d.DREG['AX0'], 3, 10)]
            p.case('physical pattern RX', dict(alternate=alternate, bits=bits, dtype=dtype),
                   setup, body, ['AX1', 'MX0', 'AX0'])
    p.finish()
    d.check_encoding(p)
    return p


def build_sport0():
    """RX-only MC scan with stock flags and simultaneous SPORT1 control."""
    p = d.Program('sport0_multichannel_route')
    p.emit(d.jump(0), *[d.nop()]*3)
    for v in range(d.IRQ_VECTORS):
        counter = 2 if v == 4 else 3
        p.emit(d.modify(counter, counter), d.rts(rti=True), d.nop(), d.nop())
    p.pm[0] = d.jump(len(p.pm))
    d.preamble(p)
    p.emit(d.load_dreg('AX0', 0xFF), d.store_dreg(0x3FE5, 'AX0'),
           d.load_dreg('AX0', 0x70FF), d.store_dreg(0x3FE6, 'AX0'),
           0x0200CF, 0x02030F, 0x020C0F)
    settings = [(7, 0), (15, 0)]
    for channels in (24, 32):
        for delay in range(16):
            for mask in (0, (1<<channels)-1):
                settings.append((0x8007 | (0x200 if channels == 32 else 0) | delay<<10, mask))
    for control, mask in settings:
        setup = [d.load_group3('IMASK', 0), d.load_dreg('AX0', 0x0407),
                 d.store_dreg(0x3FFF, 'AX0')]
        for addr in list(range(0x2800, 0x2808))+list(range(0x2A00, 0x2A08)):
            setup += [d.load_dreg('AX0', 0xBEEF), d.store_dreg(addr, 'AX0')]
        for i in (0, 4):
            setup += [d.load_dag(f'L{i}', 8), d.load_dag(f'I{i}', 0x2800 if i == 0 else 0x2A00),
                      d.load_dag(f'M{i}', 1)]
        for i in (2, 3):
            setup += [d.load_dag(f'L{i}', 0), d.load_dag(f'I{i}', 0), d.load_dag(f'M{i}', 1)]
        for addr, value in ((0x3FF3, 1), (0x3FEF, 0x8041), (0x3FF2, 0x300F),
                            (0x3FF6, control), (0x3FF7, 0), (0x3FF8, 0),
                            (0x3FF9, mask&65535), (0x3FFA, mask>>16)):
            setup += [d.load_dreg('AX0', value), d.store_dreg(addr, 'AX0')]
        setup += [d.load_group3('ICNTL', 3), d.load_group3('IFC', 0xFF),
                  d.load_group3('CNTR', 4096),
                  d.load_dreg('AX0', 0x1C07), d.store_dreg(0x3FFF, 'AX0'),
                  d.load_group3('IMASK', 0x22)]
        start = len(p.pm)+len(setup)+1
        body = [d.do_until(start+15, 0xE)]+[d.nop()]*16
        body += [d.load_group3('IMASK', 0), d.load_dreg('AX0', 0x0407),
                 d.store_dreg(0x3FFF, 'AX0'), d.nop(), d.nop(),
                 d.reg_move(0, d.DREG['AX1'], 3, 8), d.dm_read_abs(0x3FF6, 'MX0')]
        p.case('SPORT0 route and MC clock', dict(control=control, mask=mask),
               setup, body, ['I0', 'I4', 'I2', 'I3', 'AX1', 'MX0'])
        regs = ['AX0', 'AX1', 'MX0', 'MX1', 'AY0', 'AY1', 'MY0', 'MY1']
        for base in (0x2800, 0x2A00):
            p.case('SPORT ring', dict(base=base), [],
                   [d.dm_read_abs(base+n, r) for n, r in enumerate(regs)], regs)
    p.finish()
    d.check_encoding(p)
    return p


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--sport0', action='store_true')
    args = parser.parse_args()
    args.out.write_text(json.dumps((build_sport0() if args.sport0 else build()).as_json(), indent=1)+'\n')
