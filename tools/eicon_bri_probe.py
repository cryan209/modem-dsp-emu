"""Build bounded bare-metal board probes for eicon_dsp_bench_card.py.

Run only with that tool's identity/stopped-card guards and a stock-firmware
restoration handler. These probes change IPAC reset/C/I state on the spare.
"""
import argparse
import json
from pathlib import Path
from eicon_dsp_bench import Assembler, FINISHED, STATUS, RESULTS


def build(kind):
    a = Assembler()
    a.li('t0', 0xa0000000 + STATUS)
    for offset in range(0, 0x100, 4):
        a.sw('zero', offset, 't0')
    if kind == 'ipac-ci-irq':
        return build_ci_irq(a)
    if kind == 'boot-high-alias':
        image, meta = build_boot_alias(a, 0xb0000000)
        meta['suite'] = 'boot-high-alias'
        meta['ports'] = ['sdram-and-high-aperture']
        return image, meta
    if kind == 'boot-alias':
        return build_boot_alias(a)
    if kind == 'boot-high-sweep':
        return build_boot_high_sweep(a)
    if kind == 'boot-entry':
        return build_boot_entry(a)
    if kind == 'cpu':
        a.li('t1', 0xa0000000 + RESULTS)
        registers = [15, 16, 12]
        for i, reg in enumerate(registers):
            a.words.append(0x40000000 | (10 << 16) | (reg << 11))
            a.nop()
            a.sw('t2', i * 4, 't1')
        ports, count = ['cp0-prid/config/status'], 6
    elif kind == 'memory-controller':
        a.li('t0', 0xffffe200)
        a.li('t1', 0xa0000000 + RESULTS)
        for i in range(2):
            a.lw('t2', i * 4, 't0')
            a.nop()
            a.sw('t2', i * 4, 't1')
        ports, count = ['ffffe200/ffffe204'], 4
        registers = [0xffffe200, 0xffffe204]
    else:
        a.li('t0', 0xbf800000)
        if kind == 'ipac-b-reset':
            a.sh('zero', 0, 't0')
            a.li('t2', 100000)
            a.label('assert_reset')
            a.addiu('t2', 't2', -1)
            a.bne('t2', 'zero', 'assert_reset')
            a.nop()
        a.li('t1', 0x1e)
        a.sh('t1', 0, 't0')
        a.li('t2', 100000)
        a.label('settle')
        a.addiu('t2', 't2', -1)
        a.bne('t2', 'zero', 'settle')
        a.nop()
        a.li('t1', 0xa0000000 + RESULTS)
        registers = [0x20, 0x21, 0x2e, 0x33, 0x60, 0x62, 0x63, 0x64]
        if kind == 'ipac-b-reset':
            registers = [base + offset for base in (0x70, 0x80)
                         for offset in (0, 0, 1, 2, 3, 6, 7, 8, 9)]
        count = len(registers) if kind in ('ipac-reset', 'ipac-b-reset') else 8
        if kind == 'ipac-b-reset':
            count += 4
        for port in range(4):
            a.li('t0', 0xbf801000 + port * 0x1000)
            if kind in ('ipac-reset', 'ipac-b-reset'):
                for index, reg in enumerate(registers):
                    a._i(0x24, 't2', 't0', reg * 4)
                    a.nop()
                    a.sh('t2', (port * count + index) * 2, 't1')
                if kind == 'ipac-b-reset':
                    for index, base in enumerate((0x70, 0x80)):
                        a._i(0x28, 'zero', 't0', base * 4)
                        for repeat in range(2):
                            a._i(0x24, 't2', 't0', base * 4)
                            a.nop()
                            a.sh('t2', (port * count + len(registers) + index * 2 + repeat) * 2, 't1')
            else:
                # Read reset indication twice, then TIM, RES and DI, reading
                # CIR0 twice after each command to distinguish status/change.
                for index, command in enumerate((None, 0x0f, 0x1f, 0xff)):
                    if command is not None:
                        a.li('t2', command)
                        a._i(0x28, 't2', 't0', 0x2e * 4)
                        a.li('t2', 100000)
                        a.label(f'wait{port}_{index}')
                        a.addiu('t2', 't2', -1)
                        a.bne('t2', 'zero', f'wait{port}_{index}')
                        a.nop()
                    for repeat in range(2):
                        a._i(0x24, 't2', 't0', 0x2e * 4)
                        a.nop()
                        a.sh('t2', (port * count + index * 2 + repeat) * 2, 't1')
                registers = ['reset/first', 'reset/second', 'TIM/first', 'TIM/second',
                             'RES/first', 'RES/second', 'DI/first', 'DI/second']
        if kind == 'ipac-b-reset':
            registers += ['70/unmasked/first', '70/unmasked/second',
                          '80/unmasked/first', '80/unmasked/second']
        ports = [f'ipac{i+1}' for i in range(4)]
    a.li('t0', 0xa0000000 + STATUS)
    a.li('t1', 8)
    a.sw('t1', 0, 't0')
    a.li('t1', FINISHED)
    a.sw('t1', 8, 't0')
    a.label('done')
    a.beq('zero', 'zero', 'done')
    a.nop()
    meta = dict(suite=kind, status=STATUS, results=RESULTS, stride=count * 2,
                count=count, finished=FINISHED, ports=ports, registers=registers)
    return a.assemble(), meta


def build_ci_irq(a):
    """Isolate each IPAC C/I interrupt with all other sources masked."""
    a.li('s0', 0xbf800000)
    a.li('s1', 0xa0000000 + RESULTS)
    # Reset DSPs only, retaining the stock IPAC clock/interface setup.
    a.li('t0', 0x1e)
    a.sh('t0', 0, 's0')

    def delay(label):
        a.li('t0', 100000)
        a.label(label)
        a.addiu('t0', 't0', -1)
        a.bne('t0', 'zero', label)
        a.nop()

    delay('dsp_reset')
    a.li('t0', 0x1f)
    a.sh('t0', 0, 's0')
    for port in range(4):
        a.li('t0', 0xff)
        for reg in (0x60, 0x61, 0x20, 0x70, 0x80):
            a._i(0x28, 't0', 's0', (port + 1) * 0x1000 + reg * 4)
    for port in range(4):
        base = (port + 1) * 0x1000
        a.li('t0', 0xef)
        a._i(0x28, 't0', 's0', base + 0x60 * 4)
        for step, command in enumerate((0x1f, 0x0f)):
            result = (port * 2 + step) * 16
            a.li('t0', command)
            a._i(0x28, 't0', 's0', base + 0x2e * 4)
            delay(f'ci_{port}_{step}')
            a.words.append(0x40000000 | (10 << 16) | (13 << 11))
            a.nop()
            a.sw('t2', result, 's1')
            for index, reg in enumerate((0x60, 0x2e, 0x2e)):
                a._i(0x24, 't2', 's0', base + reg * 4)
                a.nop()
                a.sh('t2', result + 4 + index * 2, 's1')
            a.words.append(0x40000000 | (10 << 16) | (13 << 11))
            a.nop()
            a.sw('t2', result + 12, 's1')
        a.li('t0', 0xff)
        a._i(0x28, 't0', 's0', base + 0x60 * 4)
    a.li('t0', 0xa0000000 + STATUS)
    a.li('t1', 8)
    a.sw('t1', 0, 't0')
    a.li('t1', FINISHED)
    a.sw('t1', 8, 't0')
    a.label('done')
    a.beq('zero', 'zero', 'done')
    a.nop()
    image = a.assemble()
    if len(image) >= STATUS:
        raise RuntimeError('C/I interrupt probe overlaps status block')
    meta = dict(suite='ipac-ci-irq', status=STATUS, results=RESULTS,
                stride=16, count=8, finished=FINISHED,
                ports=[f'ipac{i+1}-{command}' for i in range(4) for command in ('RES', 'TIM')],
                registers=['cause-low', 'cause-high', 'ista', 'cir-first', 'cir-second',
                           'reserved', 'cause-after-low', 'cause-after-high'],
                reset_hold=0x1e, reset_release=0x1f)
    return image, meta


def build_boot_alias(a, aperture=0xbfc00000):
    """Compare the reset-vector aperture with low SDRAM, then restore a word."""
    a.li('s0', 0xa0000000)
    a.li('s1', aperture)
    a.li('s2', 0xa0000000 + RESULTS)
    a.li('t0', 1024)
    a.label('compare_boot_aperture')
    a.lw('t1', 0, 's0')
    a.lw('t2', 0, 's1')
    a.nop()
    a.sw('t1', 0, 's2')
    a.sw('t2', 4096, 's2')
    for register in ('s0', 's1', 's2'):
        a.addiu(register, register, 4)
    a.addiu('t0', 't0', -1)
    a.bne('t0', 'zero', 'compare_boot_aperture')
    a.nop()
    a.li('s0', 0xa0000024)
    a.li('s1', aperture + 0x24)
    a.li('s2', 0xa0000000 + RESULTS + 8192)
    a.lw('s3', 0, 's0')
    a.lw('s4', 0, 's1')
    a.nop()
    a.sw('s3', 0, 's2')
    a.sw('s4', 4, 's2')
    a.addiu('t1', 's3', 1)
    a.sw('t1', 0, 's0')
    a.lw('t2', 0, 's1')
    a.nop()
    a.sw('t2', 8, 's2')
    a.addiu('t1', 's3', 2)
    a.sw('t1', 0, 's1')
    a.lw('t2', 0, 's0')
    a.nop()
    a.sw('t2', 12, 's2')
    a.sw('s4', 0, 's1')
    a.sw('s3', 0, 's0')
    a.lw('t1', 0, 's0')
    a.lw('t2', 0, 's1')
    a.nop()
    a.sw('t1', 16, 's2')
    a.sw('t2', 20, 's2')
    a.li('t0', 0xa0000000 + STATUS)
    a.li('t1', 8)
    a.sw('t1', 0, 't0')
    a.li('t1', FINISHED)
    a.sw('t1', 8, 't0')
    a.label('done')
    a.beq('zero', 'zero', 'done')
    a.nop()
    return a.assemble(), dict(suite='boot-alias', status=STATUS, results=RESULTS,
        stride=8216, count=4108, finished=FINISHED, ports=['sdram-and-reset-aperture'],
        slices=dict(sdram=[0, 2048], reset_aperture=[2048, 4096], word_tests=[4096, 4108]))


def build_boot_high_sweep(a):
    # Compare 64 bytes on every 4 KiB page without writing into either aperture.
    a.li('s0', 0xa0000000)
    a.li('s1', 0xb0000000)
    a.li('s2', 4096)
    a.li('s3', 0)
    a.label('page')
    a.li('t3', 16)
    a.label('sweep')
    a.lw('t1', 0, 's0')
    a.lw('t2', 0, 's1')
    a.nop()
    a.beq('t1', 't2', 'same')
    a.nop()
    a.addiu('s3', 's3', 1)
    a.label('same')
    a.addiu('s0', 's0', 4)
    a.addiu('s1', 's1', 4)
    a.addiu('t3', 't3', -1)
    a.bne('t3', 'zero', 'sweep')
    a.nop()
    a.addiu('s0', 's0', 4032)
    a.addiu('s1', 's1', 4032)
    a.addiu('s2', 's2', -1)
    a.bne('s2', 'zero', 'page')
    a.nop()
    a.li('t0', 0xa0000000 + RESULTS)
    a.sw('s3', 0, 't0')
    a.sw('s0', 4, 't0')
    a.sw('s1', 8, 't0')
    a.li('t0', 0xa0000000 + STATUS)
    a.li('t1', 8)
    a.sw('t1', 0, 't0')
    a.li('t1', FINISHED)
    a.sw('t1', 8, 't0')
    a.label('done')
    a.beq('zero', 'zero', 'done')
    a.nop()
    return a.assemble(), dict(suite='boot-high-sweep', status=STATUS,
        results=RESULTS, stride=12, count=6, finished=FINISHED,
        ports=['every-page-16MiB-high-aperture'], words_compared=65536, page_stride=4096)


def build_boot_entry(a):
    entry_offset = len(a.words) * 4
    a.words.append(0x04110001)  # bgezal zero,+1: record current execution segment
    a.nop()
    a.li('s2', 0xa0000000 + RESULTS)
    a.sw('ra', 0, 's2')
    a.addiu('s2', 's2', 4)
    a.li('s0', 0xa0000000)
    a.li('s1', 0xb0000000)
    a.li('t0', 16)
    a.label('compare_high_aperture')
    a.lw('t1', 0, 's0')
    a.lw('t2', 0, 's1')
    a.nop()
    a.sw('t1', 0, 's2')
    a.sw('t2', 64, 's2')
    for register in ('s0', 's1', 's2'):
        a.addiu(register, register, 4)
    a.addiu('t0', 't0', -1)
    a.bne('t0', 'zero', 'compare_high_aperture')
    a.nop()
    a.li('t0', 0xa0000000 + STATUS)
    a.li('t1', 8)
    a.sw('t1', 0, 't0')
    a.li('t1', FINISHED)
    a.sw('t1', 8, 't0')
    a.label('done')
    a.beq('zero', 'zero', 'done')
    a.nop()
    return a.assemble(), dict(suite='boot-entry', status=STATUS, results=RESULTS,
        stride=132, count=66, finished=FINISHED, ports=['execution-entry-and-high-aperture'],
        branch_link_offset=entry_offset,
        slices=dict(return_address=[0, 2], sdram=[2, 34], high_aperture=[34, 66]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=('cpu', 'memory-controller', 'ipac-reset', 'ipac-b-reset', 'ipac-ci', 'ipac-ci-irq', 'boot-alias', 'boot-entry', 'boot-high-sweep', 'boot-high-alias'), required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    image, meta = build(args.kind)
    args.out.write_bytes(image)
    args.out.with_suffix('.meta.json').write_text(json.dumps(meta, indent=2) + '\n')


if __name__ == '__main__':
    main()
