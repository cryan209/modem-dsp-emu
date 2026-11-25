#!/usr/bin/env python3
"""Original V8.ANA modulation tone generators on the physical ADSP-2181s.

Two probe kinds call original firmware at its original PM addresses:

carrier  the oscillator at PM 3F2F, driven by the firmware's own carrier
         phase-accumulate words copied from PM 3A19..3A1E. Only the
         increment immediate changes, selecting ANSam 2100 Hz or the
         V.8bis 1080/1750 Hz carriers.
fsk      the complete V.21 FSK generator at PM 3A3C with its original
         channel initializer at PM 3A29, its 61-tap shaping filter and
         both original coefficient tables. A per-sample data bit drives
         DM(03B3), so a held bit gives one tone and a toggling bit gives
         real FSK.

The oscillator reads phase bits 9..14 of a 64-entry cosine table, so one
period spans 32768 phase units and a tone is increment*rate/32768.
"""
import argparse
import json
import math
from pathlib import Path

import dsp_conformance as d
import eicon_modem_routine_replay as modem

RESULT = 0x0800
INPUT = 0x2000
RATE = 9600                 # V8.ANA's own sample cadence
OSCILLATOR = 0x3F2F
CARRIER_SITE = (0x3A19, 0x3A1F)   # AY0=DM(03AF) .. DM(03AF)=AR
FSK_INIT, FSK_GENERATOR = 0x3A29, 0x3A3C
CARRIER_PHASE, FSK_PHASE, FSK_BIT = 0x03AF, 0x03B5, 0x03B3
FSK_RING, FSK_TAPS = 0x03C0, 61
FSK_DELAY = FSK_TAPS//2    # symmetric linear-phase shaping filter
GAIN = 0x0768
CHANNEL_SELECT = 0x3EE1     # bit 3: set selects V.21 channel 1

# Increment words as they appear in the original instructions, with the
# tone each one produces at the overlay's 9600 samples/s.
CARRIERS = {
    'ansam': (0x1C00, 2100.0, 'V.8 ANSam / V.25 answer-tone carrier, PM 3A1A'),
    'v8bis-initiating': (0x0E66, 1080.0, 'V.8bis initiating carrier, PM 3A67'),
    'v8bis-responding': (0x1755, 1750.0, 'V.8bis responding carrier, PM 3A69'),
}
# Channel 2 is the initializer's bit-clear branch at PM 3A2F, channel 1 its
# bit-set branch at PM 3A33. Each branch stores the space tone in DM(03B6)
# and the mark tone in DM(03B7); DM(03B3) is the data bit.
FSK_CHANNELS = {2: (0x18AB, 0x1600, 1850.0, 1650.0),
                1: (0x0FBC, 0x0D11, 1180.0, 980.0)}
MAX_SAMPLES = 6000


def tone_hz(increment, rate=RATE):
    return increment*rate/32768


def firmware_subset(pm, kind):
    ranges = [(0x3C80, 0x3D00), (OSCILLATOR, 0x3F42)]
    if kind == 'carrier':
        ranges.append(CARRIER_SITE)
    else:
        ranges += [(FSK_INIT, 0x3A3A), (FSK_GENERATOR, 0x3A59), (0x3D42, 0x3DBC)]
    return {a: pm[a] for lo, hi in ranges for a in range(lo, hi)}


def _common(p, result_base):
    d.preamble(p)
    p.emit(d.load_dreg('AX0', 0x0407), d.store_dreg(0x3FFF, 'AX0'),
           d.load_dreg('AX0', 0), d.store_dreg(0x3FF3, 'AX0'), d.store_dreg(0x3FEF, 'AX0'))
    for reg, value in {'M4': 0, 'M5': 1, 'M6': 1, 'I6': result_base,
                       'L4': 0, 'L6': 0}.items():
        p.emit(d.load_dag(reg, value))


def _footer(p, addresses):
    for addr in addresses:
        p.emit(d.dm_read_abs(addr, 'AX0'),
               d.compute_dm(0, 'AX0', 2, 2, dag2=True, write=True))
    group, index = d.dag_index('I6')
    p.emit(d.reg_move(0, d.DREG['AX0'], group, index),
           d.compute_dm(0, 'AX0', 2, 2, dag2=True, write=True))


def build_carrier(tone='ansam', count=4800, firmware=None):
    if tone not in CARRIERS:
        raise ValueError(f'tone must be one of {sorted(CARRIERS)}')
    if not 1 <= count <= MAX_SAMPLES:
        raise ValueError(f'count must be 1..{MAX_SAMPLES}')
    pm = modem.load_firmware() if firmware is None else firmware
    increment, expected, origin = CARRIERS[tone]
    p = d.Program(f'v8_carrier_{tone}', next_result=RESULT)
    _common(p, RESULT)
    p.emit(d.load_group3('CNTR', count))
    site = [pm[a] for a in range(*CARRIER_SITE)]
    # Only the increment immediate changes; AX0 = $<increment> is 0x4<imm>0.
    site[1] = (site[1] & ~(0xFFFF << 4)) | increment << 4
    loop = len(p.pm)
    p.emit(d.nop(), *site, d.call(OSCILLATOR),
           d.compute_dm(0, 'MR1', 2, 2, dag2=True, write=True), d.nop())
    p.pm[loop] = d.do_until(len(p.pm)-1, 0xE)
    _footer(p, (CARRIER_PHASE,))
    p.finish()
    p.dm_init = [[CARRIER_PHASE, 0]]
    p.next_result = RESULT+count+2
    d.check_encoding(p)
    spec = p.as_json()
    spec['result_base'] = RESULT
    spec['pm_init'] = [[a, w] for a, w in sorted(firmware_subset(pm, 'carrier').items())]
    spec['tone'] = dict(kind='carrier', tone=tone, increment=increment, count=count,
                        rate=RATE, expected_hz=expected, origin=origin,
                        fields=['output'], firmware='V8.ANA 1.00 build 109-789')
    return spec


def bit_vector(count, bit, bit_samples):
    if bit not in (0, 1):
        raise ValueError('bit must be 0 (space) or 1 (mark)')
    if bit_samples < 0:
        raise ValueError('bit_samples must be nonnegative')
    if not bit_samples:
        return [bit]*count
    return [bit ^ (n//bit_samples & 1) for n in range(count)]


def build_fsk(channel=1, bit=1, count=4800, bit_samples=0, gain=0x4000, firmware=None):
    if channel not in FSK_CHANNELS:
        raise ValueError('channel must be 1 or 2')
    if not 1 <= count <= MAX_SAMPLES:
        raise ValueError(f'count must be 1..{MAX_SAMPLES}')
    if not 0 <= gain <= 32767:
        raise ValueError('gain must be 0..32767')
    pm = modem.load_firmware() if firmware is None else firmware
    bits = bit_vector(count, bit, bit_samples)
    space_word, mark_word, space_hz, mark_hz = FSK_CHANNELS[channel]
    p = d.Program(f'v8_fsk_ch{channel}', next_result=RESULT)
    _common(p, RESULT)
    for reg, value in {'M1': 1, 'M2': 1, 'I2': INPUT, 'L0': 0, 'L2': 0}.items():
        p.emit(d.load_dag(reg, value))
    # The original initializer picks the channel, its two increments and its
    # shaping-coefficient table from bit 3 of DM(3EE1).
    p.emit(d.call(FSK_INIT))
    p.emit(d.load_group3('CNTR', count))
    loop = len(p.pm)
    p.emit(d.nop(), d.compute_dm(0, 'AX0', 2, 2), d.store_dreg(FSK_BIT, 'AX0'),
           d.call(FSK_GENERATOR),
           d.compute_dm(0, 'MR1', 2, 2, dag2=True, write=True), d.nop())
    p.pm[loop] = d.do_until(len(p.pm)-1, 0xE)
    _footer(p, (FSK_PHASE, 0x03B6, 0x03B7, 0x03B8, 0x03B2, FSK_BIT))
    p.finish()
    p.dm_init = [[CHANNEL_SELECT, 8 if channel == 1 else 0], [GAIN, gain],
                 [FSK_PHASE, 0], [FSK_BIT, 0]]
    p.dm_init += [[FSK_RING+n, 0] for n in range(FSK_TAPS)]
    p.dm_init += [[INPUT+n, v] for n, v in enumerate(bits)]
    p.next_result = RESULT+count+7
    if p.next_result > INPUT:
        raise ValueError('capture overlaps the data-bit vector')
    d.check_encoding(p)
    spec = p.as_json()
    spec['result_base'] = RESULT
    spec['pm_init'] = [[a, w] for a, w in sorted(firmware_subset(pm, 'fsk').items())]
    held = None if bit_samples else (mark_hz if bit else space_hz)
    spec['tone'] = dict(kind='fsk', channel=channel, bit=bit, bit_samples=bit_samples,
                        count=count, rate=RATE, gain=gain, taps=FSK_TAPS,
                        space_word=space_word, mark_word=mark_word,
                        space_hz=space_hz, mark_hz=mark_hz, expected_hz=held,
                        baud=None if not bit_samples else RATE/bit_samples,
                        fields=['output'], firmware='V8.ANA 1.00 build 109-789')
    return spec


def signed(words):
    return [v-65536 if v & 32768 else v for v in words]


def goertzel(samples, hz, rate):
    """Energy at one frequency, the matched filter an FSK receiver uses."""
    coeff = 2*math.cos(2*math.pi*hz/rate)
    a = b = 0.0
    for x in samples:
        a, b = x+coeff*a-b, a
    return max(0.0, a*a+b*b-coeff*a*b)


def discriminate(output, mark_hz, space_hz, bit_samples, rate=RATE,
                 delay=FSK_DELAY, guard=4, skip_bits=1):
    """Recover data bits by comparing mark and space energy in each interval.

    The shaping filter is a symmetric 61-tap linear-phase FIR, so its output
    lags the data by exactly its 30-sample group delay.
    """
    if bit_samples <= 2*guard:
        raise ValueError('bit interval is too short for the guard band')
    bits = []
    total = max(0, (len(output)-delay)//bit_samples)
    for k in range(skip_bits, total):
        lo = delay+k*bit_samples+guard
        segment = output[lo:delay+(k+1)*bit_samples-guard]
        bits.append(1 if goertzel(segment, mark_hz, rate)
                    > goertzel(segment, space_hz, rate) else 0)
    return bits, skip_bits


def spectrum(samples, rate=RATE, low=300, high=3000, step=1):
    """Hann-windowed Goertzel scan over a captured tone."""
    if len(samples) < 2 or rate <= 0 or step <= 0 or high <= low:
        raise ValueError('spectrum needs two samples, a positive rate and low < high')
    mean = sum(samples)/len(samples)
    windowed = [(x-mean)*(0.5-0.5*math.cos(2*math.pi*n/(len(samples)-1)))
                for n, x in enumerate(samples)]

    def power(hz):
        coeff = 2*math.cos(2*math.pi*hz/rate)
        a = b = 0.0
        for x in windowed:
            a, b = x+coeff*a-b, a
        return max(0.0, a*a+b*b-coeff*a*b)

    powers = {hz: power(hz) for hz in range(low, high+1, step)}
    peak = max(powers, key=powers.get)
    ordered = sorted(powers, key=powers.get, reverse=True)
    second = next((hz for hz in ordered if abs(hz-peak) > 40), peak)
    return dict(peak_hz=peak, second_hz=second,
                second_ratio=powers[second]/powers[peak] if powers[peak] else 0.0,
                peak_amplitude=max(abs(x) for x in samples),
                rms=math.sqrt(sum(x*x for x in samples)/len(samples)))


def measure(spec, words):
    """Frequency content of one captured run, against the expected tone."""
    tone = spec['tone']
    output = signed([w & 65535 for w in words[:tone['count']]])
    scan = spectrum(output, tone['rate'])
    result = dict(kind=tone['kind'], count=tone['count'], **scan)
    if tone['expected_hz'] is not None:
        result['expected_hz'] = tone['expected_hz']
        result['error_hz'] = abs(scan['peak_hz']-tone['expected_hz'])
        return result
    # Toggling FSK: demodulate instead of scanning, then require both tones.
    bits, skipped = discriminate(output, tone['mark_hz'], tone['space_hz'],
                                 tone['bit_samples'], tone['rate'])
    sent = bit_vector(tone['count'], tone['bit'], tone['bit_samples'])
    sent = [sent[(skipped+k)*tone['bit_samples']] for k in range(len(bits))]
    result.update(mark_hz=tone['mark_hz'], space_hz=tone['space_hz'],
                  baud=tone['baud'], bits=len(bits), skipped_bits=skipped,
                  bit_errors=sum(a != b for a, b in zip(bits, sent)),
                  mark_energy=goertzel(output, tone['mark_hz'], tone['rate']),
                  space_energy=goertzel(output, tone['space_hz'], tone['rate']))
    result['error_hz'] = 0.0 if not result['bit_errors'] else float('inf')
    return result


def compare(specs, card, emulated, tolerance_hz=4.0):
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
        row = dict(port=chip['port'], kind=tone['kind'],
                   label=tone.get('tone') or f"ch{tone['channel']}",
                   done=chip['done'] == d.DONE_VALUE and e['done'] == d.DONE_VALUE,
                   mismatch_count=mismatch_count,
                   card=measure(spec, actual), emulator=measure(spec, expected))
        row['tone_ok'] = row['card']['error_hz'] <= tolerance_hz
        if diffs:
            row['first_mismatch'] = dict(word=diffs[0], sample=diffs[0],
                                         actual=f'{actual[diffs[0]]:04x}',
                                         expected=f'{expected[diffs[0]]:04x}')
        rows.append(row)
    return dict(finished=card['finished'], tolerance_hz=tolerance_hz, dsps=rows,
                matches=bool(card['finished']) and all(
                    r['done'] and not r['mismatch_count'] and r['tone_ok'] for r in rows))


def matrix(count=4800, firmware=None):
    """One probe per DSP: three carriers, four held FSK tones, one real FSK."""
    pm = modem.load_firmware() if firmware is None else firmware
    specs = [build_carrier(t, count, pm) for t in ('ansam', 'v8bis-initiating',
                                                   'v8bis-responding')]
    for channel in (1, 2):
        for bit in (1, 0):
            specs.append(build_fsk(channel, bit, count, 0, firmware=pm))
    # 32 samples per bit at 9600 is V.21's own 300 bit/s.
    specs.append(build_fsk(1, 1, count, 32, firmware=pm))
    return specs


def main():
    p = argparse.ArgumentParser(description=__doc__,
                               formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build')
    b.add_argument('--kind', choices=('carrier', 'fsk', 'matrix'), default='carrier')
    b.add_argument('--tone', choices=sorted(CARRIERS), default='ansam')
    b.add_argument('--channel', type=int, choices=(1, 2), default=1)
    b.add_argument('--bit', type=int, choices=(0, 1), default=1)
    b.add_argument('--bit-samples', type=int, default=0,
                   help='samples per data bit; 0 holds one tone')
    b.add_argument('--count', type=int, default=4800)
    b.add_argument('--gain', type=lambda x: int(x, 0), default=0x4000)
    b.add_argument('--out', type=Path, required=True)
    b.add_argument('--bundle', type=Path)
    e = sub.add_parser('emulate')
    e.add_argument('spec', type=Path)
    e.add_argument('--out', type=Path, required=True)
    c = sub.add_parser('compare')
    c.add_argument('spec', type=Path)
    c.add_argument('card', type=Path)
    c.add_argument('--emulator', type=Path, required=True)
    c.add_argument('--tolerance-hz', type=float, default=4.0)
    c.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    if args.command == 'build':
        if args.kind == 'matrix':
            result = matrix(args.count)
        elif args.kind == 'carrier':
            result = build_carrier(args.tone, args.count)
        else:
            result = build_fsk(args.channel, args.bit, args.count,
                               args.bit_samples, args.gain)
        args.out.write_text(json.dumps(result, indent=1)+'\n')
        if args.bundle:
            import eicon_dsp_bench as bench
            image, meta = bench.build_bundle(result, reset_release=1,
                                             boot_settle=1000000)
            args.bundle.write_bytes(image)
            args.bundle.with_suffix('.meta.json').write_text(json.dumps(meta, indent=1)+'\n')
        return 0
    if args.command == 'emulate':
        specs = json.loads(args.spec.read_text())
        result = {'runs': [d.emulate(s) for s in (specs if isinstance(specs, list) else [specs])]}
        args.out.write_text(json.dumps(result, indent=1)+'\n')
        return 0
    result = compare(json.loads(args.spec.read_text()), json.loads(args.card.read_text()),
                     json.loads(args.emulator.read_text()), args.tolerance_hz)
    args.out.write_text(json.dumps(result, indent=1)+'\n')
    print(json.dumps(result, indent=1))
    return 0 if result['matches'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
