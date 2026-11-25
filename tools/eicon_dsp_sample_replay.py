#!/usr/bin/env python3
"""Replay preloaded PCM16 through an eight-tap Q15 FIR on DSP or emulator.

No BRI, SPORT, interrupts, timer, or real-time host streaming is required.
The MIPS bench loads input/coefficients through IDMA before starting PM0.
This is a standalone processing harness, not the stock modem firmware.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import wave

import dsp_conformance as d

INPUT = 0x1800
HISTORY = 0x2800
COEFFICIENTS = 0x2810
TAPS = (-2048, 0, 10240, 16384, 10240, 0, -2048, 0)
MAX_SAMPLES = 1024
FIELDS = ('input', 'MR0', 'MR1', 'MR2', 'PCM16')


def demo_samples(count=512):
    """Deterministic silence, impulse, steps, ramp, rails, noise and tone."""
    tone = (0, 6270, 11585, 15137, 16384, 15137, 11585, 6270,
            0, -6270, -11585, -15137, -16384, -15137, -11585, -6270)
    state, out = 0x2181, []
    for n in range(count):
        block, pos = divmod(n % 512, 64)
        state = (1664525*state+1013904223) & 0xFFFFFFFF
        value = (0, 32767 if pos == 0 else 0, 32767, -32768,
                 pos*1024-32768, 32767 if pos & 1 else -32768,
                 (state >> 16)-32768, tone[pos % 16])[block]
        out.append(value)
    return out


def read_samples(path, offset=0, count=512, rate=8000):
    if offset < 0 or not 1 <= count <= MAX_SAMPLES:
        raise ValueError('offset must be nonnegative and count must be 1..1024')
    path = Path(path)
    if path.suffix.lower() == '.wav':
        with wave.open(str(path), 'rb') as f:
            if f.getnchannels() != 1 or f.getsampwidth() != 2 or f.getcomptype() != 'NONE':
                raise ValueError('WAV input must be uncompressed mono PCM16')
            rate = f.getframerate()
            if offset > f.getnframes():
                raise ValueError('offset is beyond the end of the WAV')
            f.setpos(offset)
            raw = f.readframes(count)
    else:
        with path.open('rb') as f:
            f.seek(2*offset)
            raw = f.read(2*count)
    if len(raw) != 2*count:
        raise ValueError('input does not contain the requested number of PCM16 samples')
    return list(struct.unpack(f'<{count}h', raw)), rate


def build(samples, coefficients=TAPS, rate=8000, source='demo', offset=0):
    samples, coefficients = list(samples), list(coefficients)
    if not 1 <= len(samples) <= MAX_SAMPLES:
        raise ValueError('sample count must be 1..1024')
    if len(coefficients) != 8:
        raise ValueError('exactly eight coefficients are required')
    if any(not isinstance(v, int) or not -32768 <= v <= 32767 for v in samples+coefficients):
        raise ValueError('samples and coefficients must be signed PCM16 integers')
    if rate <= 0:
        raise ValueError('sample rate must be positive')
    p = d.Program('sample_replay_fir8')
    d.preamble(p)
    # Explicitly disable both SPORTs and their DMA; no serial input is used.
    p.emit(d.load_dreg('AR', 0x0407), d.store_dreg(0x3FFF, 'AR'),
           d.load_dreg('AR', 0), d.store_dreg(0x3FF3, 'AR'), d.store_dreg(0x3FEF, 'AR'))
    for reg, value in {'I0':INPUT, 'L0':0, 'M0':1,
                       'I1':HISTORY, 'L1':8, 'M1':1,
                       'I2':HISTORY, 'L2':8, 'M2':-1,
                       'I4':COEFFICIENTS, 'L4':8, 'M4':1,
                       'I5':d.RESULT_BASE, 'L5':0, 'M5':1}.items():
        p.emit(d.load_dag(reg, value))
    p.emit(d.load_group3('CNTR', len(samples)))
    loop = len(p.pm)
    p.emit(d.do_until(loop+7, 0xE),
           d.compute_dm(0, 'MX0', 0, 0),
           d.reg_move(1, 2, 1, 1),  # I2 = current history write position
           d.compute_dm(0, 'MX0', 1, 1, write=True),
           d.compute_dm(0, 'MX0', 1, 1, write=True, dag2=True))  # input echo via I5
    call = len(p.pm)
    p.emit(d.call(0), d.nop(), d.nop())
    # Footer: pointer state, full history and disabled serial configuration.
    for reg in ('I0', 'I1', 'I2', 'I4', 'I5'):
        group, index = d.dag_index(reg)
        p.emit(d.reg_move(0, d.DREG['AX0'], group, index),
               d.compute_dm(0, 'AX0', 1, 1, write=True, dag2=True))
    for addr in list(range(HISTORY, HISTORY+8))+[0x3FFF, 0x3FF3, 0x3FEF]:
        p.emit(d.dm_read_abs(addr, 'AX0'),
               d.compute_dm(0, 'AX0', 1, 1, write=True, dag2=True))
    p.finish()
    routine = len(p.pm)
    p.pm[call] = d.call(routine)
    for tap in range(8):
        p.emit(d.compute_dm(0, 'MX0', 2, 2),
               d.compute_dm(0, 'MY0', 0, 0, dag2=True),
               d.mac(d.MAC_OPS['X*Y(SS)' if tap == 0 else 'MR+X*Y(SS)']))
    for reg in ('MR0', 'MR1', 'MR2'):
        p.emit(d.compute_dm(0, reg, 1, 1, write=True, dag2=True))
    p.emit(d.sat_mr(), d.compute_dm(0, 'MR1', 1, 1, write=True, dag2=True), d.rts())
    p.next_result = d.RESULT_BASE+5*len(samples)+16
    if p.next_result > INPUT:
        raise ValueError('result block overlaps sample input')
    p.dm_init = [[INPUT+n, value & 65535] for n, value in enumerate(samples)]
    p.dm_init += [[COEFFICIENTS+n, value & 65535] for n, value in enumerate(coefficients)]
    p.dm_init += [[HISTORY+n, 0] for n in range(8)]
    d.check_encoding(p)
    spec = p.as_json()
    spec['replay'] = dict(samples=samples, coefficients=coefficients, rate=rate,
                          source=source, offset=offset, fields=list(FIELDS),
                          input_sha256=hashlib.sha256(struct.pack(f'<{len(samples)}h', *samples)).hexdigest(),
                          timing='offline; sample order preserved, no real-time cadence')
    return spec


def reference(spec):
    """Independent integer convolution, fractional MAC, then SAT MR/truncate."""
    a = spec['replay']
    samples, coefficients = a['samples'], a['coefficients']
    words = []
    for n, sample in enumerate(samples):
        acc = 2*sum(coefficient*samples[n-k] for k, coefficient in enumerate(coefficients) if n >= k)
        extension = (acc >> 32) & 255
        if extension & 128:
            extension |= 0xFF00
        pcm = min(32767, max(-32768, acc >> 16))
        words.extend([sample & 65535, acc & 65535, (acc >> 16) & 65535,
                      extension, pcm & 65535])
    count = len(samples)
    history = [0]*8
    for n, value in enumerate(samples):
        history[n % 8] = value & 65535
    # I5 is observed after the preceding four footer stores.
    words += [INPUT+count, HISTORY+count % 8, HISTORY+(count-1) % 8,
              COEFFICIENTS, d.RESULT_BASE+5*count+4]
    return words+history+[0x0407, 0, 0]


def boot_digest(spec):
    """Hash every actual PM/DM boot write, including samples and presets."""
    payload = b''.join(struct.pack('<IIB', addr, value, int(pm))
                       for addr, value, pm in d.boot_writes(spec))
    return hashlib.sha256(payload).hexdigest()


def compare(spec, capture, emulated=None):
    if not capture['dsps']:
        raise ValueError('capture contains no DSP results')
    specs = spec if isinstance(spec, list) else [spec]*len(capture['dsps'])
    if len(specs) != len(capture['dsps']):
        raise ValueError('one spec per captured DSP is required')
    runs = emulated.get('runs', []) if emulated is not None else []
    if emulated is not None and len(runs) != (len(spec) if isinstance(spec, list) else 1):
        raise ValueError('emulator result count does not match specifications')
    summary = dict(finished=capture.get('finished', False), dsps=[])
    for i, (s, chip) in enumerate(zip(specs, capture['dsps'])):
        expected = reference(s)
        raw = [int(v,16) for v in chip['dm']]
        actual = raw[:len(expected)]
        padding_ok = all(v == s['preset'] for v in raw[len(expected):])
        diffs = [j for j, (a, b) in enumerate(zip(actual, expected)) if a != b]
        emulator_match = True
        if emulated is not None:
            run = runs[i if isinstance(spec, list) else 0]
            emulator_match = run['done'] == d.DONE_VALUE and [int(v,16) for v in run['dm']] == actual
        row = dict(port=chip['port'], samples=len(s['replay']['samples']),
                   done=chip['done'] == d.DONE_VALUE,
                   reference_match=len(actual) == len(expected) and not diffs and padding_ok,
                   emulator_match=emulator_match if emulated is not None else None,
                   mismatch_count=len(diffs)+abs(len(actual)-len(expected))+
                   sum(v != s['preset'] for v in raw[len(expected):]))
        if diffs:
            j = diffs[0]
            row['first_mismatch'] = dict(word=j, expected=f'{expected[j]:04x}', actual=f'{actual[j]:04x}')
            if j < 5*row['samples']:
                row['first_mismatch'].update(sample=j//5, field=FIELDS[j % 5])
        summary['dsps'].append(row)
    summary['matches'] = summary['finished'] and all(
        r['done'] and r['reference_match'] and r['emulator_match'] is not False for r in summary['dsps'])
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build')
    b.add_argument('--input', type=Path, help='mono PCM16 WAV or signed little-endian .pcm; default: demo')
    b.add_argument('--offset', type=int, default=0, help='input sample offset')
    b.add_argument('--count', type=int, default=512)
    b.add_argument('--rate', type=int, default=8000, help='raw PCM metadata only; WAV supplies its rate')
    b.add_argument('--coefficients', type=lambda s: [int(v) for v in s.split(',')], default=TAPS)
    b.add_argument('--out', type=Path, required=True)
    e = sub.add_parser('emulate')
    e.add_argument('spec', type=Path)
    e.add_argument('--out', type=Path, required=True)
    c = sub.add_parser('compare')
    c.add_argument('spec', type=Path)
    c.add_argument('card', type=Path)
    c.add_argument('--emulator', type=Path)
    c.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'build':
        if args.input:
            samples, rate = read_samples(args.input, args.offset, args.count, args.rate)
        else:
            if args.offset != 0 or not 1 <= args.count <= MAX_SAMPLES:
                parser.error('demo requires offset=0 and count=1..1024')
            samples, rate = demo_samples(args.count), args.rate
        result = build(samples, args.coefficients, rate, str(args.input) if args.input else 'demo', args.offset)
    elif args.command == 'emulate':
        spec = json.loads(args.spec.read_text())
        result = {'runs':[d.emulate(s) for s in (spec if isinstance(spec, list) else [spec])]}
    else:
        result = compare(json.loads(args.spec.read_text()), json.loads(args.card.read_text()),
                         json.loads(args.emulator.read_text()) if args.emulator else None)
    args.out.write_text(json.dumps(result, indent=1)+'\n')
    if args.command == 'compare':
        print(json.dumps(result, indent=1))
        return 0 if result['matches'] else 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
