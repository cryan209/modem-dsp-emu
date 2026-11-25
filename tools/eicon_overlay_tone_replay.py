#!/usr/bin/env python3
"""Tone generators in the six other overlays that carry the cosine table.

The 128-word interpolated cosine table of V8.ANA PM 3C80 is relocated,
word-identical, into six further analog overlays, each with its own copy of
the oscillator that reads it. Two oscillator families exist:

A  phase in AR, M7 = 0x61, table base+1, returns MX0 and MR1.
   V8.ANA 3F2F, info.ana 3BFF, infoh.ana 3BA3, v29fc.ana 3CDA.
B  phase in AX0, M7 = 0x5F, table base+0, returns MX0 and MX1 exactly 48
   entries — 90 degrees — apart, so it is a quadrature oscillator.
   v22fc.ana 2EBA, v22bisfc.ana 2BC8, v22v32.ana-lec 2BCA.

Three probe kinds run original code at its original PM addresses:

oscillator   the overlay's own oscillator, driven by a plain 16-bit phase
             accumulator. The oscillator masks phase bits 0..14, so a
             16-bit accumulator and the firmware's own 15-bit wrap give
             identical samples.
native       the overlay's own complete accumulate-and-call sequence,
             copied verbatim with only its increment immediates patched.
quadrature   the family-B generator wrapper, which turns its phase cell
             into a rotated I/Q pair, driven through its own phase cell
             with the rotation set to unity.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

import dsp_conformance as d
import eicon_modulation_tone_replay as v8

ROOT = Path(__file__).resolve().parents[1]
OVERLAYS = ROOT/'artifacts/eicon-dsp/build-109-789-analog/overlays'
RESULT = 0x0800
LIMIT = 0x1F00              # capture must stay below this
PHASE_SCRATCH = 0x07F0      # driver's own cell, for the oscillator probe
RING = 0x0700               # zeroed correction ring for info.ana's sequence

# rate is the overlay's own sample cadence, established by the increments
# its generators use: 1200/2400 Hz at 9600 for the INFO overlays, and
# 1700 Hz (V.29) plus 1800 Hz (V.27ter) at 7200 for v29fc.
OVERLAY = {
    'info.ana': dict(directory='0260-info.ana-overlay', table=0x2C80,
                     oscillator=0x3BFF, family='A', rate=9600,
                     native=(0x34ED, 0x34FB, (0, 1), 0x1676, RING, 0x16BB, 16),
                     tones={'v34-info-calling': (0x1000, 1200.0),
                            'v34-info-answering': (0x2000, 2400.0)}),
    'infoh.ana': dict(directory='026e-infoh.ana-overlay', table=0x2F00,
                      oscillator=0x3BA3, family='A', rate=9600,
                      native=(0x3597, 0x35A2, (0, 1), 0x1635, None, None, 0),
                      tones={'v34-info-calling': (0x1000, 1200.0),
                             'v34-info-answering': (0x2000, 2400.0)}),
    'v29fc.ana': dict(directory='0273-v29fc.ana-overlay', table=0x3200,
                      oscillator=0x3CDA, family='A', rate=7200,
                      native=(0x3059, 0x3062, (0,), 0x1620, None, None, 0),
                      tones={'v29-carrier': (0x1E39, 1700.02),
                             'v27ter-carrier': (0x2000, 1800.0),
                             'low-tone': (0x1800, 1350.0)}),
    'v22fc.ana': dict(directory='0271-v22fc.ana-overlay', table=0x3100,
                      oscillator=0x2EBA, family='B', rate=9600, native=None,
                      wrapper=dict(entry=0x2ED1, phase=0x065B,
                                   rotation=(0x04DC, 0x04DD),
                                   outputs=(0x0658, 0x0659)),
                      tones={'v22-originate': (0x1000, 1200.0),
                             'v22-answer': (0x2000, 2400.0),
                             'v22bis-guard': (0x1800, 1800.0)}),
    'v22bisfc.ana': dict(directory='0275-v22bisfc.ana-overlay', table=0x2E00,
                         oscillator=0x2BC8, family='B', rate=9600, native=None,
                         wrapper=dict(entry=0x2BDF, phase=0x06E9,
                                      rotation=(0x056C, 0x056D),
                                      outputs=(0x06E6, 0x06E7)),
                         tones={'v22-originate': (0x1000, 1200.0),
                                'v22-answer': (0x2000, 2400.0),
                                'v22bis-guard': (0x1800, 1800.0)}),
    'v22v32.ana-lec': dict(directory='0266-v22v32.ana-lec-overlay', table=0x2E00,
                           oscillator=0x2BCA, family='B', rate=9600, native=None,
                           wrapper=dict(entry=0x2BE1, phase=0x06E1,
                                        rotation=(0x05BD, 0x05BE),
                                        outputs=(0x06DE, 0x06DF)),
                           tones={'v22-originate': (0x1000, 1200.0),
                                  'v22-answer': (0x2000, 2400.0),
                                  'v32-carrier': (0x1800, 1800.0)}),
}
FAMILY_A_SPAN, FAMILY_B_SPAN = 0x13, 0x17
UNITY = 0x7FFF


def overlay_path(name):
    return OVERLAYS/OVERLAY[name]['directory']/'pm.words'


def load_overlay(name):
    text = overlay_path(name).read_text()
    return {int(a, 16): int(w, 16) for a, w in
            (line.split() for line in text.splitlines() if line.strip())}


def overlay_digest(name):
    return hashlib.sha256(overlay_path(name).read_bytes()).hexdigest()


def firmware_subset(name, pm, kind='oscillator'):
    info = OVERLAY[name]
    span = FAMILY_A_SPAN if info['family'] == 'A' else FAMILY_B_SPAN
    ranges = [(info['table'], info['table']+0x80),
              (info['oscillator'], info['oscillator']+span)]
    if kind == 'native':
        lo, hi = info['native'][0], info['native'][1]
        ranges.append((lo, hi))
    if kind == 'quadrature':
        entry = info['wrapper']['entry']
        ranges.append((entry, entry+0x0D))
    return {a: pm[a] for lo, hi in ranges for a in range(lo, hi)}


def words_per_sample(name, kind):
    if kind == 'native':
        return 1
    return 1 if OVERLAY[name]['family'] == 'A' else 2


def _common(p, result_base):
    d.preamble(p)
    p.emit(d.load_dreg('AX0', 0x0407), d.store_dreg(0x3FFF, 'AX0'),
           d.load_dreg('AX0', 0), d.store_dreg(0x3FF3, 'AX0'),
           d.store_dreg(0x3FEF, 'AX0'))
    for reg, value in {'M0': 0, 'M1': 1, 'M4': 0, 'M5': 1, 'M6': 1,
                       'I6': result_base, 'L0': 0, 'L4': 0, 'L6': 0}.items():
        p.emit(d.load_dag(reg, value))


def _footer(p, addresses):
    for addr in addresses:
        p.emit(d.dm_read_abs(addr, 'AX0'),
               d.compute_dm(0, 'AX0', 2, 2, dag2=True, write=True))
    group, index = d.dag_index('I6')
    p.emit(d.reg_move(0, d.DREG['AX0'], group, index),
           d.compute_dm(0, 'AX0', 2, 2, dag2=True, write=True))


def _check(p, count, per_sample, footer):
    p.next_result = RESULT+count*per_sample+footer
    if p.next_result > LIMIT:
        raise ValueError('capture exceeds the result window')
    d.check_encoding(p)


def _patch_immediate(word, value):
    """Replace the 16-bit immediate field, bits 4..19, of a load."""
    return (word & ~(0xFFFF << 4)) | (value & 0xFFFF) << 4


def build_oscillator(name, tone, count=2400, firmware=None):
    if name not in OVERLAY:
        raise ValueError(f'overlay must be one of {sorted(OVERLAY)}')
    info = OVERLAY[name]
    if tone not in info['tones']:
        raise ValueError(f'{name} tone must be one of {sorted(info["tones"])}')
    per_sample = words_per_sample(name, 'oscillator')
    if not 1 <= count <= (LIMIT-RESULT-2)//per_sample:
        raise ValueError('count is outside the result window')
    pm = load_overlay(name) if firmware is None else firmware
    increment, expected = info['tones'][tone]
    p = d.Program(f'{name}_oscillator_{tone}'.replace('.', '_'), next_result=RESULT)
    _common(p, RESULT)
    loop = len(p.pm)
    p.emit(d.nop(),
           d.load_dreg('AY0', increment),
           d.dm_read_abs(PHASE_SCRATCH, 'AR'),
           d.alu(d.ALU_OPS['X+Y'], x='AR', y='AY0'),
           d.store_dreg(PHASE_SCRATCH, 'AR'))
    if info['family'] == 'B':
        p.emit(d.compute_move(0, 'AX0', 'AR'))
    p.emit(d.call(info['oscillator']))
    if info['family'] == 'A':
        p.emit(d.compute_dm(0, 'MR1', 2, 2, dag2=True, write=True))
    else:
        p.emit(d.compute_dm(0, 'MX0', 2, 2, dag2=True, write=True),
               d.compute_dm(0, 'MX1', 2, 2, dag2=True, write=True))
    p.emit(d.nop())
    p.pm[loop] = d.do_until(len(p.pm)-1, 0xE)
    p.pm.insert(loop, d.load_group3('CNTR', count))
    p.pm[loop+1] = d.do_until(len(p.pm)-1, 0xE)
    _footer(p, (PHASE_SCRATCH,))
    p.finish()
    p.dm_init = [[PHASE_SCRATCH, 0]]
    _check(p, count, per_sample, 2)
    return _spec(p, name, pm, 'oscillator', dict(
        tone=tone, increment=increment, expected_hz=expected, count=count,
        rate=info['rate'], family=info['family'], oscillator=info['oscillator'],
        table=info['table'], per_sample=per_sample,
        fields=['output'] if per_sample == 1 else ['mx0', 'mx1']))


def build_native(name, tone, count=2400, firmware=None):
    info = OVERLAY[name]
    if not info.get('native'):
        raise ValueError(f'{name} has no traced fixed-increment sequence')
    if tone not in info['tones']:
        raise ValueError(f'{name} tone must be one of {sorted(info["tones"])}')
    if not 1 <= count <= LIMIT-RESULT-3:
        raise ValueError('count is outside the result window')
    pm = load_overlay(name) if firmware is None else firmware
    lo, hi, slots, phase, ring, ring_cell, ring_len = info['native']
    increment, expected = info['tones'][tone]
    site = [pm[a] for a in range(lo, hi)]
    # Both immediates are patched where the sequence picks between two
    # increments with a condition, so the choice cannot change the tone.
    for slot in slots:
        site[slot] = _patch_immediate(site[slot], increment)
    p = d.Program(f'{name}_native_{tone}'.replace('.', '_'), next_result=RESULT)
    _common(p, RESULT)
    loop = len(p.pm)
    p.emit(d.load_group3('CNTR', count), d.nop(), *site,
           d.compute_dm(0, 'MR1', 2, 2, dag2=True, write=True), d.nop())
    p.pm[loop+1] = d.do_until(len(p.pm)-1, 0xE)
    _footer(p, (phase,))
    p.finish()
    p.dm_init = [[phase, 0]]
    if ring is not None:
        p.dm_init += [[ring_cell, ring]]+[[ring+n, 0] for n in range(ring_len)]
    _check(p, count, 1, 2)
    return _spec(p, name, pm, 'native', dict(
        tone=tone, increment=increment, expected_hz=expected, count=count,
        rate=info['rate'], family=info['family'], oscillator=info['oscillator'],
        table=info['table'], per_sample=1, sequence=[lo, hi], patched=list(slots),
        phase=phase, fields=['output']))


def build_quadrature(name, tone, count=2400, firmware=None):
    info = OVERLAY[name]
    if not info.get('wrapper'):
        raise ValueError(f'{name} has no quadrature generator wrapper')
    if tone not in info['tones']:
        raise ValueError(f'{name} tone must be one of {sorted(info["tones"])}')
    if not 1 <= count <= (LIMIT-RESULT-3)//2:
        raise ValueError('count is outside the result window')
    pm = load_overlay(name) if firmware is None else firmware
    wrapper = info['wrapper']
    increment, expected = info['tones'][tone]
    p = d.Program(f'{name}_quadrature_{tone}'.replace('.', '_'), next_result=RESULT)
    _common(p, RESULT)
    loop = len(p.pm)
    p.emit(d.load_group3('CNTR', count), d.nop(),
           d.load_dreg('AY0', increment),
           d.dm_read_abs(wrapper['phase'], 'AR'),
           d.alu(d.ALU_OPS['X+Y'], x='AR', y='AY0'),
           d.store_dreg(wrapper['phase'], 'AR'),
           d.call(wrapper['entry']))
    for addr in wrapper['outputs']:
        p.emit(d.dm_read_abs(addr, 'AX0'),
               d.compute_dm(0, 'AX0', 2, 2, dag2=True, write=True))
    p.emit(d.nop())
    p.pm[loop+1] = d.do_until(len(p.pm)-1, 0xE)
    _footer(p, (wrapper['phase'],))
    p.finish()
    # Unity rotation, so the wrapper's outputs are its own oscillator's I/Q.
    p.dm_init = [[wrapper['phase'], 0], [wrapper['rotation'][0], UNITY],
                 [wrapper['rotation'][1], 0]]
    _check(p, count, 2, 2)
    return _spec(p, name, pm, 'quadrature', dict(
        tone=tone, increment=increment, expected_hz=expected, count=count,
        rate=info['rate'], family=info['family'], oscillator=info['oscillator'],
        table=info['table'], per_sample=2, wrapper=wrapper['entry'],
        rotation=UNITY, fields=['i', 'q']))


def _spec(p, name, pm, kind, tone):
    spec = p.as_json()
    spec['result_base'] = RESULT
    spec['pm_init'] = [[a, w] for a, w in
                       sorted(firmware_subset(name, pm, kind).items())]
    tone.update(kind=kind, overlay=name, firmware='build 109-789 analog')
    spec['tone'] = tone
    return spec


BUILDERS = {'oscillator': build_oscillator, 'native': build_native,
            'quadrature': build_quadrature}


def build(name, kind, tone, count=2400, firmware=None):
    return BUILDERS[kind](name, tone, count, firmware)


def channels(spec, words):
    """Split a capture into one list per captured field."""
    tone = spec['tone']
    n, per = tone['count'], tone['per_sample']
    raw = v8.signed([w & 65535 for w in words[:n*per]])
    return [raw[k::per] for k in range(per)]


def dft(samples, hz, rate):
    w = 2*math.pi*hz/rate
    re = sum(x*math.cos(w*n) for n, x in enumerate(samples))
    im = -sum(x*math.sin(w*n) for n, x in enumerate(samples))
    return complex(re, im)


def measure(spec, words):
    tone = spec['tone']
    rate, expected = tone['rate'], tone['expected_hz']
    parts = channels(spec, words)
    scans = [v8.spectrum(part, rate, 300, 3000) for part in parts]
    result = dict(kind=tone['kind'], overlay=tone['overlay'], tone=tone['tone'],
                  expected_hz=expected, fields=tone['fields'],
                  peaks=[s['peak_hz'] for s in scans],
                  second_ratios=[s['second_ratio'] for s in scans],
                  peak_amplitudes=[s['peak_amplitude'] for s in scans],
                  rms=[s['rms'] for s in scans])
    result['error_hz'] = max(abs(s['peak_hz']-expected) for s in scans)
    if len(parts) == 2:
        a, b = (dft(part, result['peaks'][0], rate) for part in parts)
        separation = math.degrees(abs(((math.atan2(b.imag, b.real)
                                        - math.atan2(a.imag, a.real))
                                       + math.pi) % (2*math.pi)-math.pi))
        result['quadrature_degrees'] = separation
        result['quadrature_error'] = abs(separation-90.0)
    return result


def compare(specs, card, emulated, tolerance_hz=6.0, quadrature_tolerance=1.0):
    specs = specs if isinstance(specs, list) else [specs]
    expected_runs = len(specs)
    if not card.get('dsps') or len(specs) not in (1, len(card['dsps'])):
        raise ValueError('specifications do not match captured DSPs')
    if len(specs) == 1:
        specs = specs*len(card['dsps'])
    if len(emulated['runs']) != expected_runs:
        raise ValueError('emulator result count does not match specifications')
    rows = []
    for i, (spec, chip) in enumerate(zip(specs, card['dsps'])):
        e = emulated['runs'][0 if len(emulated['runs']) == 1 else i]
        expected = [int(v, 16) for v in e['dm']]
        raw = [int(v, 16) for v in chip['dm']]
        actual = raw[:len(expected)]
        diffs = [j for j, (a, b) in enumerate(zip(actual, expected)) if a != b]
        mismatch_count = len(diffs)+abs(len(actual)-len(expected))+sum(
            v != spec['preset'] for v in raw[len(expected):])
        tone = spec['tone']
        row = dict(port=chip['port'], overlay=tone['overlay'], kind=tone['kind'],
                   tone=tone['tone'],
                   done=chip['done'] == d.DONE_VALUE and e['done'] == d.DONE_VALUE,
                   mismatch_count=mismatch_count,
                   card=measure(spec, actual), emulator=measure(spec, expected))
        row['tone_ok'] = row['card']['error_hz'] <= tolerance_hz
        if 'quadrature_error' in row['card']:
            row['quadrature_ok'] = \
                row['card']['quadrature_error'] <= quadrature_tolerance
            row['tone_ok'] = row['tone_ok'] and row['quadrature_ok']
        if diffs:
            row['first_mismatch'] = dict(word=diffs[0],
                                         actual=f'{actual[diffs[0]]:04x}',
                                         expected=f'{expected[diffs[0]]:04x}')
        rows.append(row)
    return dict(finished=card['finished'], tolerance_hz=tolerance_hz,
                quadrature_tolerance=quadrature_tolerance, dsps=rows,
                matches=bool(card['finished']) and all(
                    r['done'] and not r['mismatch_count'] and r['tone_ok']
                    for r in rows))


# Two eight-program runs cover every overlay, both families and all three
# probe kinds.
RUNS = {
    'a': [('info.ana', 'native', 'v34-info-calling'),
          ('info.ana', 'native', 'v34-info-answering'),
          ('infoh.ana', 'native', 'v34-info-calling'),
          ('infoh.ana', 'native', 'v34-info-answering'),
          ('v29fc.ana', 'native', 'v29-carrier'),
          ('v29fc.ana', 'native', 'v27ter-carrier'),
          ('v29fc.ana', 'native', 'low-tone'),
          ('v29fc.ana', 'oscillator', 'v29-carrier')],
    'b': [('v22fc.ana', 'oscillator', 'v22-originate'),
          ('v22fc.ana', 'quadrature', 'v22-answer'),
          ('v22bisfc.ana', 'oscillator', 'v22-answer'),
          ('v22bisfc.ana', 'quadrature', 'v22bis-guard'),
          ('v22v32.ana-lec', 'oscillator', 'v32-carrier'),
          ('v22v32.ana-lec', 'quadrature', 'v22-originate'),
          ('info.ana', 'oscillator', 'v34-info-calling'),
          ('infoh.ana', 'oscillator', 'v34-info-answering')],
}


def matrix(run, count=2400):
    cache = {}
    specs = []
    for name, kind, tone in RUNS[run]:
        pm = cache.setdefault(name, load_overlay(name))
        specs.append(build(name, kind, tone, count, pm))
    return specs


def main():
    p = argparse.ArgumentParser(description=__doc__,
                               formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build')
    b.add_argument('--overlay', choices=sorted(OVERLAY))
    b.add_argument('--kind', choices=sorted(BUILDERS), default='oscillator')
    b.add_argument('--tone')
    b.add_argument('--run', choices=sorted(RUNS), help='build a whole eight-DSP run')
    b.add_argument('--count', type=int, default=2400)
    b.add_argument('--out', type=Path, required=True)
    b.add_argument('--bundle', type=Path)
    e = sub.add_parser('emulate')
    e.add_argument('spec', type=Path)
    e.add_argument('--out', type=Path, required=True)
    c = sub.add_parser('compare')
    c.add_argument('spec', type=Path)
    c.add_argument('card', type=Path)
    c.add_argument('--emulator', type=Path, required=True)
    c.add_argument('--tolerance-hz', type=float, default=6.0)
    c.add_argument('--out', type=Path, required=True)
    sub.add_parser('list')
    args = p.parse_args()
    if args.command == 'list':
        for name, info in sorted(OVERLAY.items()):
            kinds = ['oscillator']+(['native'] if info.get('native') else [])+(
                ['quadrature'] if info.get('wrapper') else [])
            print(f"{name:16s} family {info['family']} table {info['table']:04x} "
                  f"oscillator {info['oscillator']:04x} {info['rate']} Hz "
                  f"kinds={','.join(kinds)}")
            for tone, (increment, hz) in sorted(info['tones'].items()):
                print(f"    {tone:20s} increment {increment:04x} -> {hz:8.2f} Hz")
        return 0
    if args.command == 'build':
        if args.run:
            result = matrix(args.run, args.count)
        else:
            if not args.overlay or not args.tone:
                p.error('--overlay and --tone are required without --run')
            result = build(args.overlay, args.kind, args.tone, args.count)
        args.out.write_text(json.dumps(result, indent=1)+'\n')
        if args.bundle:
            import eicon_dsp_bench as bench
            image, meta = bench.build_bundle(result, reset_release=1,
                                             boot_settle=1000000)
            args.bundle.write_bytes(image)
            args.bundle.with_suffix('.meta.json').write_text(
                json.dumps(meta, indent=1)+'\n')
        return 0
    if args.command == 'emulate':
        specs = json.loads(args.spec.read_text())
        result = {'runs': [d.emulate(s) for s in
                           (specs if isinstance(specs, list) else [specs])]}
        args.out.write_text(json.dumps(result, indent=1)+'\n')
        return 0
    result = compare(json.loads(args.spec.read_text()),
                     json.loads(args.card.read_text()),
                     json.loads(args.emulator.read_text()), args.tolerance_hz)
    args.out.write_text(json.dumps(result, indent=1)+'\n')
    print(json.dumps(result, indent=1))
    return 0 if result['matches'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
