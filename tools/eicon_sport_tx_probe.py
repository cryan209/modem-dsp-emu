#!/usr/bin/env python3
"""External-clock SPORT1 TX start and local IOM loop capture on spare card.

No DSP clock/frame generators. Run with the runner's --ipac-fixture loop,
which returns DD B1/B2 to DU through IPAC CDA registers. An interrupt or
autobuffer advance alone does not prove that a transmitted word was received.
"""
import argparse
import json
from pathlib import Path

import dsp_conformance as d

RX = 0x2800
TX = 0x2900
PATTERN = (0x11, 0x22, 0x44, 0x88, 0x55, 0xAA, 0x3C, 0xC3)


def build(index=1):
    if index not in range(8):
        raise ValueError('index must be 0..7')
    rx_i = (index+4) % 8
    tx_count, rx_count = [n for n in range(8) if n not in (index, rx_i)][:2]
    p = d.Program(f'sport1_tx_i{index}')
    p.emit(d.jump(0), *[d.nop()] * 3)
    for v in range(d.IRQ_VECTORS):
        count = tx_count if v == 7 else rx_count
        p.emit(d.modify(count % 4, count % 4, dag2=count >= 4),
               d.rts(rti=True), d.nop(), d.nop())
    p.pm[0] = d.jump(len(p.pm))
    d.preamble(p)
    for bits in (8, 16):
        for alternate in (False, True):
            for kick in ('none', 'before-enable', 'after-enable'):
                setup = [d.load_group3('IMASK', 0),
                         d.load_dreg('AX0', 0x0407), d.store_dreg(0x3FFF, 'AX0')]
                for n in range(64):
                    setup += [d.load_dreg('AX0', 0xBEEF), d.store_dreg(RX+n, 'AX0')]
                for n, value in enumerate(PATTERN):
                    if bits == 16:
                        value = value << 8 | (value ^ 0xFF)
                    setup += [d.load_dreg('AX0', value), d.store_dreg(TX+n, 'AX0')]
                for n in range(8):
                    setup += [d.load_dag(f'L{n}', 64 if n == rx_i else 8 if n == index else 0),
                              d.load_dag(f'M{n}', 1),
                              d.load_dag(f'I{n}', RX if n == rx_i else TX if n == index else 0)]
                autobuffer = index<<9 | (index%4)<<7 | rx_i<<4 | (rx_i%4)<<2 | 3
                setup += [d.load_dreg('AX0', autobuffer), d.store_dreg(0x3FEF, 'AX0'),
                          d.load_dreg('AX0', (0x3C00 if alternate else 0x2800) | (bits-1)),
                          d.store_dreg(0x3FF2, 'AX0'),
                          d.load_group3('ICNTL', 3), d.load_group3('IFC', 0xFF),
                          d.load_group3('CNTR', 4096)]
                seed = [d.compute_dm(0, 'AX0', index%4, index%4, dag2=index>=4),
                        d.reg_move(3, 11, 0, d.DREG['AX0'])]
                if kick == 'before-enable':
                    setup += seed
                setup += [d.load_dreg('AX0', 0x0C07), d.store_dreg(0x3FFF, 'AX0'),
                          d.load_group3('IMASK', 6)]
                if kick == 'after-enable':
                    setup += seed
                start = len(p.pm) + len(setup) + 1
                body = [d.do_until(start+63, 0xE)] + [d.nop()] * 64
                body += [d.load_group3('IMASK', 0),
                         d.load_dreg('AX0', 0x0407), d.store_dreg(0x3FFF, 'AX0'),
                         d.nop(), d.nop()]
                p.case('SPORT1 TX initiation', dict(bits=bits, alternate=alternate, kick=kick),
                       setup, body, [f'I{rx_i}', f'I{index}', f'I{tx_count}', f'I{rx_count}'])
                regs = ['AX0', 'AX1', 'MX0', 'MX1', 'AY0', 'AY1', 'MY0', 'MY1']
                for n in range(0, 32, 8):
                    p.case('loop RX', dict(base=RX+n), [],
                           [d.dm_read_abs(RX+n+j, r) for j, r in enumerate(regs)], regs)
    p.finish()
    d.check_encoding(p)
    return p


def build_overhead():
    p = d.Program('sport1_tx_overhead')
    d.preamble(p)
    for n in range(64):
        p.emit(d.load_dreg('AX0', PATTERN[n%8]), d.store_dreg(TX+n, 'AX0'))
    for repeat in range(4):
        for framed in (False, True):
            for buffered in (False, True):
                setup = [d.load_group3('IMASK', 0), d.mode_control(0x20, False),
                         d.load_dreg('AX0', 0x0407), d.store_dreg(0x3FFF, 'AX0'),
                         d.load_dag('L0', 0), d.load_dag('M0', 1), d.load_dag('I0', TX),
                         d.load_dreg('AX0', 2 if buffered else 0), d.store_dreg(0x3FEF, 'AX0'),
                         d.load_dreg('AX0', 7 | (0x0C00 if framed else 0)),
                         d.store_dreg(0x3FF2, 'AX0'),
                         d.load_dreg('AX0', 0), d.store_dreg(d.TIMER_TSCALE, 'AX0'),
                         d.load_dreg('AX0', 0xFFFF),
                         d.store_dreg(d.TIMER_TCOUNT, 'AX0'),
                         d.store_dreg(d.TIMER_TPERIOD, 'AX0'),
                         d.load_group3('CNTR', 1024), d.mode_control(0x20, True),
                         d.load_dreg('AX0', 0x0C07), d.store_dreg(0x3FFF, 'AX0')]
                start = len(p.pm)+len(setup)+3
                body = [d.reg_move(0, d.DREG['AX0'], 1, 0),
                        d.dm_read_abs(d.TIMER_TCOUNT, 'MX0'),
                        d.do_until(start+31, 0xE)]+[d.nop()]*32
                body += [d.dm_read_abs(d.TIMER_TCOUNT, 'MX1'),
                         d.reg_move(0, d.DREG['AX1'], 1, 0),
                         d.mode_control(0x20, False),
                         d.load_dreg('AY0', 0x0407), d.store_dreg(0x3FFF, 'AY0')]
                p.case('TX transfer CPU overhead', dict(repeat=repeat, framed=framed,
                       buffered=buffered), setup, body, ['AX0', 'AX1', 'MX0', 'MX1'])
    p.finish()
    d.check_encoding(p)
    return p


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--index', type=int, choices=range(8), default=1)
    parser.add_argument('--overhead', action='store_true')
    args = parser.parse_args()
    program = build_overhead() if args.overhead else build(args.index)
    args.out.write_text(json.dumps(program.as_json(), indent=1) + '\n')
    print(f'{program.suite}: {len(program.pm)} PM words')
