#!/usr/bin/env python3
"""Measure externally framed SPORT1 RX, DAG wrap and idle-word formatting.

Use only with the spare-card stock-interface takeover. No TX writes or
internally driven clocks/frame syncs. Foreground execution is bounded even
if the clock disappears. Timer stamps count TSCALE=1 ticks (two DSP cycles).
"""
import argparse
import json
from pathlib import Path

import dsp_conformance as d

LOG = 0x2400
RING = 0x2800


def build(high_dag=False):
    p = d.Program('sport_wire_high' if high_dag else 'sport_wire_low')
    log_i, count_i = (0, 1) if high_dag else (6, 7)
    p.emit(d.jump(0), *[d.nop()] * 3)
    # Common handler: timestamp, post-wrap buffer pointer, and wire word.
    for v in range(d.IRQ_VECTORS):
        p.emit(d.jump(0), *[d.nop()] * 3)
    handler = len(p.pm)
    p.emit(d.dm_read_abs(d.TIMER_TCOUNT, 'AX0'),
           d.compute_dm(0, 'AX0', log_i % 4, log_i % 4, write=True, dag2=log_i >= 4))
    # The selected index is copied by a case-specific instruction, patched
    # by splitting the builder into one program per selected DAG index.
    p.emit(d.nop())
    pointer_move = len(p.pm) - 1
    p.emit(d.compute_dm(0, 'AX0', log_i % 4, log_i % 4, write=True, dag2=log_i >= 4),
           d.reg_move(0, d.DREG['AX0'], 3, 10),
           d.compute_dm(0, 'AX0', log_i % 4, log_i % 4, write=True, dag2=log_i >= 4),
           d.modify(count_i % 4, count_i % 4, dag2=count_i >= 4), d.rts(rti=True))
    for v in range(d.IRQ_VECTORS):
        p.pm[4 + 4 * v] = d.jump(handler)
    p.pm[0] = d.jump(len(p.pm))
    d.preamble(p)
    return p, pointer_move, log_i, count_i


def build_index(index, lengths=(2, 4, 8), strides=(1, -1)):
    p, pointer_move, log_i, count_i = build(index >= 6)
    group, reg = d.dag_index(f'I{index}')
    p.pm[pointer_move] = d.reg_move(0, d.DREG['AX0'], group, reg)
    p.suite = f'sport_wire_i{index}'
    # Index selection and nonmatching modify selection within the same DAG.
    m = (index + 1) % 4 + (4 if index >= 4 else 0)
    if m in (log_i, count_i):
        m = index
    for length in lengths:
        for stride in strides:
            setup = [d.load_group3('IMASK', 0), d.mode_control(0x20, False),
                     d.load_dreg('AX0', 0x0407), d.store_dreg(0x3FFF, 'AX0')]
            for n in range(8):
                setup += [d.load_dreg('AX0', 0xBEEF), d.store_dreg(RING+n, 'AX0')]
            for n in range(64):
                setup += [d.load_dreg('AX0', 0xDEAD), d.store_dreg(LOG+n, 'AX0')]
            for i in range(8):
                setup.append(d.load_dag(f'L{i}', 0))
            setup += [d.load_dag(f'L{index}', length),
                      d.load_dag(f'I{index}', RING), d.load_dag(f'M{m}', stride),
                      d.load_dag(f'I{log_i}', LOG), d.load_dag(f'M{log_i}', 1),
                      d.load_dag(f'I{count_i}', 0), d.load_dag(f'M{count_i}', 1),
                      d.load_dreg('AX0', (index << 4) | ((m % 4) << 2) | 1),
                      d.store_dreg(0x3FEF, 'AX0'),
                      d.load_dreg('AX0', 0x2007), d.store_dreg(0x3FF2, 'AX0'),
                      d.load_dreg('AX0', 1), d.store_dreg(d.TIMER_TSCALE, 'AX0'),
                      d.load_dreg('AX0', 0xFFFF),
                      d.store_dreg(d.TIMER_TCOUNT, 'AX0'),
                      d.store_dreg(d.TIMER_TPERIOD, 'AX0'),
                      d.load_group3('ICNTL', 3), d.load_group3('IFC', 0xFF),
                      d.load_group3('CNTR', 4096), d.mode_control(0x20, True),
                      d.load_dreg('AX0', 0x0C07), d.store_dreg(0x3FFF, 'AX0'),
                      d.load_group3('IMASK', 2)]
            start = len(p.pm) + len(setup) + 1
            body = [d.do_until(start+63, 0xE)] + [d.nop()] * 64
            body += [d.load_group3('IMASK', 0), d.mode_control(0x20, False),
                     d.load_dreg('AX0', 0x0407), d.store_dreg(0x3FFF, 'AX0'),
                     d.nop(), d.nop()]
            p.case('SPORT1 RX circular DAG',
                   dict(index=index, modify=m, length=length, stride=stride,
                        timer_tick_cycles=2, wait_nops=262144), setup, body,
                   [f'I{index}', f'I{count_i}'])
            regs = ['AX0', 'AX1', 'MX0', 'MX1', 'AY0', 'AY1', 'MY0', 'MY1']
            for offset, size in ((RING, 8), (LOG, 48)):
                for n in range(0, size, 8):
                    p.case('ring' if offset == RING else 'irq log',
                           dict(base=offset+n), [],
                           [d.dm_read_abs(offset+n+j, r) for j, r in enumerate(regs)], regs)
    p.finish()
    d.check_encoding(p)
    return p


def build_strides(index):
    """Additional physical wrap controls, including DMA with M=0."""
    p = build_index(index, lengths=(3, 5, 7, 8), strides=(0, 2, -2))
    p.suite = f'sport_wire_stride_i{index}'
    return p


def build_overhead():
    """Cycle cost with IMASK=0; linear I0 counts actual transfers."""
    p = d.Program('sport_wire_overhead')
    d.preamble(p)
    for repeat in range(4):
        for sync in (False, True):
            for buffered in (False, True):
                setup = [d.load_group3('IMASK', 0), d.mode_control(0x20, False),
                         d.load_dreg('AX0', 0x0407), d.store_dreg(0x3FFF, 'AX0'),
                         d.load_dag('L0', 0), d.load_dag('I0', RING),
                         d.load_dag('M0', 1),
                         d.load_dreg('AX0', int(buffered)), d.store_dreg(0x3FEF, 'AX0'),
                         d.load_dreg('AX0', 7 | (0x2000 if sync else 0)),
                         d.store_dreg(0x3FF2, 'AX0'),
                         d.load_dreg('AX0', 0), d.store_dreg(d.TIMER_TSCALE, 'AX0'),
                         d.load_dreg('AX0', 0xFFFF),
                         d.store_dreg(d.TIMER_TCOUNT, 'AX0'),
                         d.store_dreg(d.TIMER_TPERIOD, 'AX0'),
                         d.load_group3('CNTR', 1024), d.mode_control(0x20, True),
                         d.load_dreg('AX0', 0x0C07), d.store_dreg(0x3FFF, 'AX0')]
                start = len(p.pm) + len(setup) + 2
                body = [d.dm_read_abs(d.TIMER_TCOUNT, 'AX1'),
                        d.do_until(start+31, 0xE)] + [d.nop()] * 32
                body += [d.dm_read_abs(d.TIMER_TCOUNT, 'AX0'),
                         d.mode_control(0x20, False),
                         d.load_dreg('MX0', 0x0407), d.store_dreg(0x3FFF, 'MX0'),
                         d.nop(), d.nop()]
                p.case('RX transfer CPU overhead', dict(repeat=repeat, sync=sync,
                       buffered=buffered, wait_nops=32768), setup, body,
                       ['AX1', 'AX0', 'I0'])
    p.finish()
    d.check_encoding(p)
    return p


def build_format():
    """Physical idle-word conversion only; this is not an all-code sweep."""
    p = d.Program('sport_wire_format')
    d.preamble(p)
    for bits, dtype in ((8, 0), (8, 1), (16, 0), (16, 1), (8, 2), (8, 3)):
        setup = [d.load_group3('IMASK', 0),
                 d.load_dreg('AX0', 0x0407), d.store_dreg(0x3FFF, 'AX0'),
                 d.load_dreg('AX0', 0), d.store_dreg(0x3FEF, 'AX0'),
                 d.load_dreg('AX0', 0x2000 | (dtype << 4) | (bits-1)),
                 d.store_dreg(0x3FF2, 'AX0'),
                 d.load_dreg('AX0', 0x1357), d.reg_move(3, 10, 0, d.DREG['AX0']),
                 d.load_group3('CNTR', 4096),
                 d.load_dreg('AX0', 0x0C07), d.store_dreg(0x3FFF, 'AX0')]
        start = len(p.pm) + len(setup) + 1
        body = [d.do_until(start+31, 0xE)] + [d.nop()] * 32
        body += [d.reg_move(0, d.DREG['AX1'], 3, 10)] + [d.nop()] * 8
        body += [d.reg_move(0, d.DREG['AY0'], 3, 10),
                 d.dm_read_abs(0x3FF2, 'MX0'),
                 d.load_dreg('AX0', 0x0407), d.store_dreg(0x3FFF, 'AX0')]
        body += [d.nop()] * 16 + [d.reg_move(0, d.DREG['AX0'], 3, 10)]
        p.case('SPORT1 wire idle formatting', dict(bits=bits, dtype=dtype), setup, body,
               ['AX1', 'AY0', 'MX0', 'AX0'])
    p.finish()
    d.check_encoding(p)
    return p


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=int, choices=range(8), default=0)
    parser.add_argument('--kind', choices=('dag', 'strides', 'overhead', 'format'), default='dag')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    program = (build_index(args.index) if args.kind == 'dag' else
               build_strides(args.index) if args.kind == 'strides' else
               build_overhead() if args.kind == 'overhead' else build_format())
    args.out.write_text(json.dumps(program.as_json(), indent=1) + '\n')
    print(f'{program.suite}: {len(program.pm)} PM words')
