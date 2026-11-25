"""Bounded DSP FL0 level matrix for the stopped, identity-checked spare.

Observe CP0 Cause and IPAC AUX while commanding all eight DSP flag pins.
Do not infer electrical wiring merely from a pulse or a single active pin.
The caller must use the spare-only runner and restore stock firmware.
"""
import argparse
import json
from pathlib import Path
import dsp_conformance as d
from eicon_dsp_bench import Assembler, RESULTS, FINISHED, PORTS

STATUS = 0x10000


def build(status_ack=False):
    a = Assembler()
    a.li('s0', 0xbf800000)
    a.li('s1', 0xa0000000 + RESULTS)
    a.li('t0', 0xa0000000 + STATUS)
    for offset in range(0, 0x200, 4):
        a.sw('zero', offset, 't0')

    def delay(label):
        a.li('t0', 100000)
        a.label(label)
        a.addiu('t0', 't0', -1)
        a.bne('t0', 'zero', label)
        a.nop()

    a.li('t0', 0x1e)
    a.sh('t0', 0, 's0')
    delay('reset_held')
    a.li('t0', 0x1f)
    a.sh('t0', 0, 's0')
    delay('reset_released')
    # DM100 controls FL0; DM101 acknowledges execution of that command.
    # This uses neither SPORT nor a software-forced processor interrupt.
    program = [0x0200cf, d.dm_read_abs(0x100, 'AR'),
               d.store_dreg(0x101, 'AR'), d.load_dreg('AY0', 0),
               d.alu(d.ALU_OPS['X-Y'], x='AR'), d.jump(9, 0),
               0x02008f, d.jump(1), d.nop(), 0x0200cf, d.jump(1)]
    for port in PORTS:
        a.li('t1', port)
        a.li('t0', 0x4100)
        a.sh('t0', 8, 't1')
        a.sh('zero', 0, 't1')
        a.li('t0', 0xffff)
        a.sh('t0', 0, 't1')
        for address in list(range(1, len(program))) + [0]:
            a.li('t0', address)
            a.sh('t0', 8, 't1')
            a.li('t0', program[address] >> 8)
            a.sh('t0', 0, 't1')
            a.li('t0', program[address] & 255)
            a.sh('t0', 0, 't1')

    masks = [0] + [1 << i for i in range(8)] + [255] + [255 ^ (1 << i) for i in range(8)] + [0]
    stride = 72 if status_ack else 28
    for phase, mask in enumerate(masks):
        for index, port in enumerate(PORTS):
            a.li('t1', port)
            a.li('t0', 0x4100)
            a.sh('t0', 8, 't1')
            a.li('t0', (mask >> index) & 1)
            a.sh('t0', 0, 't1')
        delay(f'phase_{phase}')
        a.words.append(0x40000000 | (10 << 16) | (13 << 11))  # mfc0 t2,Cause
        a.nop()
        a.sw('t2', phase * stride, 's1')
        status_registers = (0x60, 0x61, 0x20, 0x2e, 0x70, 0x80) if status_ack else (0x61,)
        for reg_index, register in enumerate(status_registers):
            for index in range(4):
                a._i(0x24, 't2', 's0', (index + 1) * 0x1000 + 4 * register)
                a.nop()
                a.sh('t2', phase * stride + 4 + reg_index * 8 + index * 2, 's1')
        ack_base = 4 + len(status_registers) * 8
        for index, port in enumerate(PORTS):
            a.li('t1', port)
            a.li('t0', 0x4101)
            a.sh('t0', 8, 't1')
            a.lhu('t2', 0, 't1')
            a.nop()
            a.sh('t2', phase * stride + ack_base + index * 2, 's1')
        if status_ack:
            a.words.append(0x40000000 | (10 << 16) | (13 << 11))
            a.nop()
            a.sw('t2', phase * stride + 68, 's1')
    a.li('t0', 0xa0000000 + STATUS)
    a.li('t1', 8)
    a.sw('t1', 0, 't0')
    a.li('t1', FINISHED)
    a.sw('t1', 8, 't0')
    a.label('done')
    a.beq('zero', 'zero', 'done')
    a.nop()
    image = a.assemble()
    # This bare probe has no generic bench job tables. Its status block is
    # above the larger matrix program, with room for decoder job fields.
    if len(image) > STATUS:
        raise RuntimeError('FL0 matrix overlaps status block')
    registers = (['cause-low', 'cause-high']
                 + [f'ipac{i}-{register:02x}' for register in status_registers for i in range(4)]
                 + [f'dsp{i}-command-ack' for i in range(8)])
    if status_ack:
        registers += ['cause-after-low', 'cause-after-high']
    # Preserve the original fixture's register labels and byte image.
    else:
        registers[2:6] = [f'ipac{i}-aux' for i in range(4)]
    meta = dict(suite='dsp-fl0-status-ack' if status_ack else 'dsp-fl0-matrix', status=STATUS, results=RESULTS,
                stride=stride, count=stride // 2, finished=FINISHED,
                ports=[f'low-mask-{mask:02x}' for mask in masks], low_masks=masks,
                registers=registers,
                reset_hold=0x1e, reset_release=0x1f)
    return image, meta


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--status-ack', action='store_true',
                        help='read all IPAC IRQ sources, then resample Cause')
    args = parser.parse_args()
    image, meta = build(args.status_ack)
    args.out.write_bytes(image)
    args.out.with_suffix('.meta.json').write_text(json.dumps(meta, indent=2) + '\n')
    print(f'{len(image)} bytes, {len(meta["low_masks"])} phases')
