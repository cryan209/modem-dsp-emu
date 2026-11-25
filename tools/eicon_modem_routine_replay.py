#!/usr/bin/env python3
"""IDMA replay of original V8.ANA biquad, magnitude and detector firmware.

Routine and coefficient words stay at their original PM addresses. Only a
small batch driver is new; no BRI, SPORT or interrupts are used.
"""
import argparse
import hashlib
import json
from pathlib import Path

import dsp_conformance as d
import eicon_dsp_sample_replay as transport

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT/'artifacts/eicon-dsp/build-109-789-analog/overlays/025f-v8.ana-overlay'
RESULT = 0x1000
INPUT = 0x2800
TABLES = (0x3D04, 0x3D10, 0x3D16, 0x3D1C, 0x3D22)
HISTORY = 0x04A0  # From the original initializer at PM 39BA, not DM 073F.


def load_firmware(path=OVERLAY/'pm.words'):
    return {int(a,16):int(w,16) for a,w in
            (line.split() for line in Path(path).read_text().splitlines() if line.strip())}


def firmware_subset(pm):
    addresses = set()
    for lo, hi in ((0x39BA,0x39D5), (0x3EDE,0x3F2F),
                   (0x3C32,0x3C38), (0x3C64,0x3C72)):
        addresses.update(range(lo, hi))
    for table in TABLES:
        addresses.update(range(table, table+6))
    return {a:pm[a] for a in sorted(addresses)}


def layout(kind):
    if kind == 'detector':
        return layout('magnitude')+[(f'biquad_state{n}',0x07AC+n) for n in range(4)]+[
            ('level',0x0777), ('counter',0x0778), ('phase',0x07BF), ('queue_pointer',0x3FCC)]+[
            (f'queue{n}',0x0600+n) for n in range(4)]
    if kind == 'biquad':
        return [('input',None), ('output',0x0776), ('MR0','MR0'), ('MR1','MR1'),
                ('MR2','MR2'), ('SR0','SR0'), ('SR1','SR1'), ('MX0','MX0'), ('MX1','MX1')]+[
                    (f'state{n}',0x07AC+n) for n in range(4)]+[
                ('ASTAT','ASTAT'), ('I0','I0'), ('I4','I4')]
    if kind == 'magnitude':
        return [('input',None), ('magnitude',0x0776), ('MX0','MX0'), ('MX1','MX1'),
                ('SR0','SR0'), ('SR1','SR1'), ('MR0','MR0'), ('MR1','MR1'), ('MR2','MR2'),
                ('history_pointer',0x073E), ('ASTAT','ASTAT'), ('I0','I0'), ('I4','I4'),
                ('countdown',0x07BE), ('gate',0x3EDA)]+[
                    (f'history{n}',HISTORY+n) for n in range(29)]
    raise ValueError('routine must be biquad, magnitude or detector')


def build(samples, kind='biquad', table=None, firmware=None, rate=8000,
          source='demo', offset=0, initial_counter=0):
    samples = list(samples)
    fields = layout(kind)
    result_base = 0x0800 if kind == 'detector' else RESULT
    if not samples or len(samples) > (256 if kind == 'biquad' else 128):
        raise ValueError('count must be 1..256 for biquad or 1..128 for magnitude/detector')
    if any(not isinstance(v,int) or not -32768 <= v <= 32767 for v in samples):
        raise ValueError('samples must be signed PCM16 integers')
    if table is None:
        table = 0x3C64 if kind == 'magnitude' else 0x3D10
    allowed_tables = TABLES if kind == 'biquad' else (0x3D10,) if kind == 'detector' else (0x3C64,)
    if table not in allowed_tables:
        raise ValueError('unsupported firmware coefficient table')
    if rate <= 0 or offset < 0:
        raise ValueError('rate must be positive and offset must be nonnegative')
    if not 0 <= initial_counter <= 32767 or (kind != 'detector' and initial_counter):
        raise ValueError('initial counter is a nonnegative detector-only value')
    pm = load_firmware() if firmware is None else firmware
    p = d.Program(f'v8_{kind}_replay', next_result=result_base)
    d.preamble(p)
    p.emit(d.load_dreg('AX0',0x0407), d.store_dreg(0x3FFF,'AX0'),
           d.load_dreg('AX0',0), d.store_dreg(0x3FF3,'AX0'), d.store_dreg(0x3FEF,'AX0'))
    for reg,value in {'M0':0,'M1':1,'M5':1,'I6':INPUT,'M6':1,'I7':result_base,'M7':1,
                      'L0':0,'L4':0,'L6':0,'L7':0}.items():
        p.emit(d.load_dag(reg,value))
    imports = {}
    def include(lo, hi):
        imports.update({a:pm[a] for a in range(lo,hi)})
    if kind == 'biquad':
        include(0x3F1D,0x3F2A)
        include(table,table+6)
        p.dm_init = [[0x07AC+n,0] for n in range(4)]
    else:
        include(0x39BA,0x39D5)
        include(0x3EDE,0x3EE9)
        include(0x3F15,0x3F1D)
        include(0x3C64,0x3C72)
        # Run the actual history initializer once. The original emulator
        # magnitude bench used 073F, which is not the firmware's ring base.
        p.emit(d.call(0x39BA))
        p.dm_init = [[HISTORY+n,0] for n in range(29)]+[[0x073E,0],[0x3EDA,0],[0x0776,0]]
        if kind == 'detector':
            include(0x3EDE,0x3F2F)
            include(0x3C32,0x3C38)
            include(0x3D10,0x3D16)
            p.dm_init += [[0x07AC+n,0] for n in range(4)]+[
                [0x0777,0],[0x0778,initial_counter],[0x07BE,15],[0x07BF,0],[0x0747,200],[0x3FCC,0x0600]]+[
                [0x0600+n,0] for n in range(4)]
    p.emit(d.load_group3('CNTR',len(samples)))
    loop = len(p.pm)
    p.emit(d.nop(), d.compute_dm(0,'AX0',2,2,dag2=True),
           d.compute_dm(0,'AX0',3,3,dag2=True,write=True))
    if kind == 'biquad':
        p.emit(d.store_dreg(0x0776,'AX0'))
        # Original five-word call site. Only the coefficient-table address
        # changes when explicitly selecting another original table.
        site = [pm[a] for a in range(0x3EF5,0x3EFA)]
        site[2] = (site[2] & ~(0x3FFF << 4)) | table << 4
        p.emit(*site)
    else:
        p.emit(d.store_dreg(0x03A3,'AX0'))
        if kind == 'magnitude':
            p.emit(d.load_dreg('AX0',0x7FFF), d.store_dreg(0x07BE,'AX0'))
        p.emit(d.call(0x3EDE))
    for _, value in fields[1:]:
        if isinstance(value,int):
            p.emit(d.dm_read_abs(value,'AX0'))
            reg = 'AX0'
        elif value in d.GROUP3:
            p.emit(d.reg_move(0,d.DREG['AX0'],3,d.GROUP3[value]))
            reg = 'AX0'
        elif value.startswith('I'):
            group,index = d.dag_index(value)
            p.emit(d.reg_move(0,d.DREG['AX0'],group,index))
            reg = 'AX0'
        else:
            reg = value
        p.emit(d.compute_dm(0,reg,3,3,dag2=True,write=True))
    p.emit(d.nop())
    p.pm[loop] = d.do_until(len(p.pm)-1,0xE)
    for reg in ('I6','I7'):
        group,index = d.dag_index(reg)
        p.emit(d.reg_move(0,d.DREG['AX0'],group,index),
               d.compute_dm(0,'AX0',3,3,dag2=True,write=True))
    for addr in (0x3FFF,0x3FF3,0x3FEF):
        p.emit(d.dm_read_abs(addr,'AX0'), d.compute_dm(0,'AX0',3,3,dag2=True,write=True))
    p.finish()
    p.dm_init += [[INPUT+n,v & 65535] for n,v in enumerate(samples)]
    p.next_result = result_base+len(fields)*len(samples)+5
    if p.next_result > INPUT:
        raise ValueError('capture overlaps input buffer')
    d.check_encoding(p)
    spec = p.as_json()
    spec['result_base'] = result_base
    spec['pm_init'] = [[a,w] for a,w in sorted(imports.items())]
    spec['modem_replay'] = dict(kind=kind,table=table,samples=samples,rate=rate,
                               source=source,offset=offset,fields=[f for f,_ in fields],
                               firmware='V8.ANA 1.00 build 109-789',
                               history_base=0x07AC if kind == 'biquad' else HISTORY)
    if kind == 'detector':
        spec['modem_replay']['initial_counter'] = initial_counter
    return spec


def compare(specs, card, emulated):
    expected_runs = len(specs) if isinstance(specs,list) else 1
    specs = specs if isinstance(specs,list) else [specs]*len(card['dsps'])
    if not card['dsps'] or len(specs) != len(card['dsps']):
        raise ValueError('specifications do not match captured DSPs')
    if len(emulated['runs']) != expected_runs:
        raise ValueError('emulator result count does not match DSPs')
    rows = []
    for i,(spec,chip) in enumerate(zip(specs,card['dsps'])):
        e = emulated['runs'][0 if len(emulated['runs']) == 1 else i]
        expected = [int(v,16) for v in e['dm']]
        raw = [int(v,16) for v in chip['dm']]
        actual = raw[:len(expected)]
        diffs = [j for j,(a,b) in enumerate(zip(actual,expected)) if a != b]
        mismatch_count = len(diffs)+abs(len(actual)-len(expected))+sum(
            v != spec['preset'] for v in raw[len(expected):])
        a = spec['modem_replay']
        row = dict(port=chip['port'],kind=a['kind'],table=a['table'],samples=len(a['samples']),
                   done=chip['done'] == d.DONE_VALUE and e['done'] == d.DONE_VALUE,
                   mismatch_count=mismatch_count)
        if diffs:
            j = diffs[0]
            row['first_mismatch'] = dict(word=j,actual=f'{actual[j]:04x}',expected=f'{expected[j]:04x}')
            if j < len(a['samples'])*len(a['fields']):
                row['first_mismatch'].update(sample=j//len(a['fields']),field=a['fields'][j % len(a['fields'])])
        rows.append(row)
    return dict(finished=card['finished'],dsps=rows,
                matches=card['finished'] and all(r['done'] and not r['mismatch_count'] for r in rows))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command',required=True)
    b = sub.add_parser('build')
    b.add_argument('--kind',choices=('biquad','magnitude','detector'),default='biquad')
    b.add_argument('--table',type=lambda x:int(x,0),help='biquad table; magnitude uses fixed PM 3C64')
    b.add_argument('--input',type=Path)
    b.add_argument('--offset',type=int,default=0)
    b.add_argument('--count',type=int,default=128)
    b.add_argument('--rate',type=int,default=8000)
    b.add_argument('--initial-counter',type=int,default=0,help='detector threshold-counter initial state')
    b.add_argument('--out',type=Path,required=True)
    e = sub.add_parser('emulate')
    e.add_argument('spec',type=Path)
    e.add_argument('--out',type=Path,required=True)
    c = sub.add_parser('compare')
    c.add_argument('spec',type=Path)
    c.add_argument('card',type=Path)
    c.add_argument('--emulator',type=Path,required=True)
    c.add_argument('--out',type=Path,required=True)
    args = p.parse_args()
    if args.command == 'build':
        if args.input:
            samples,rate = transport.read_samples(args.input,args.offset,args.count,args.rate)
        else:
            if args.offset != 0 or not 1 <= args.count <= (256 if args.kind == 'biquad' else 128):
                p.error('demo requires offset=0 and a count within the routine limit')
            samples,rate = transport.demo_samples(args.count),args.rate
        result = build(samples,args.kind,args.table,rate=rate,
                       source=str(args.input) if args.input else 'demo',offset=args.offset,
                       initial_counter=args.initial_counter)
    elif args.command == 'emulate':
        specs = json.loads(args.spec.read_text())
        result = {'runs':[d.emulate(s) for s in (specs if isinstance(specs,list) else [specs])]}
    else:
        result = compare(json.loads(args.spec.read_text()),json.loads(args.card.read_text()),
                         json.loads(args.emulator.read_text()))
    args.out.write_text(json.dumps(result,indent=1)+'\n')
    if args.command == 'compare':
        print(json.dumps(result,indent=1))
        return 0 if result['matches'] else 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
