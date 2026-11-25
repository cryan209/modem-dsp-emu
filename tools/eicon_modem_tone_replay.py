#!/usr/bin/env python3
"""Original V8.ANA ANSam generation and physical SPORT1 tone loop probes."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import wave

import dsp_conformance as d
import eicon_modem_routine_replay as modem

TX, RX, RESULT = 0x2800, 0x3000, 0x1000
WIRE_RATE = 8000


def build_generator(count=7680, gain=0x4000, firmware=None):
    if not 1 <= count <= 8191 or not 0 <= gain <= 32767:
        raise ValueError('count must be 1..8191 and gain 0..32767')
    pm = modem.load_firmware() if firmware is None else firmware
    p = d.Program('v8_ansam_generator')
    d.preamble(p)
    p.emit(d.load_dreg('AX0',0x0407),d.store_dreg(0x3FFF,'AX0'),
           d.load_dreg('AX0',0),d.store_dreg(0x3FF3,'AX0'),d.store_dreg(0x3FEF,'AX0'))
    for reg,value in {'M4':0,'M5':1,'M6':1,'I6':0x0800,'L4':0,'L6':0}.items():
        p.emit(d.load_dag(reg,value))
    p.emit(d.load_group3('CNTR',count))
    loop = len(p.pm)
    p.emit(d.do_until(loop+3,0xE),d.call(0x3A02),
           d.compute_dm(0,'MR1',2,2,dag2=True,write=True),d.nop())
    for addr in (0x03AF,0x03B0,0x03B1,0x0773,0x0769):
        p.emit(d.dm_read_abs(addr,'AX0'),d.compute_dm(0,'AX0',2,2,dag2=True,write=True))
    p.finish()
    p.next_result = 0x0800+count+5
    p.dm_init = [[0x03AF,0],[0x03B0,0],[0x03B1,0],[0x0773,4321],[0x0769,gain]]
    spec = p.as_json()
    spec['result_base'] = 0x0800
    spec['pm_init'] = [[a,pm[a]] for lo,hi in ((0x3A02,0x3A29),(0x3F2F,0x3F42),(0x3C80,0x3D00))
                       for a in range(lo,hi)]
    spec['tone'] = dict(count=count,gain=gain,rate=9600,kind='generator',
                        routine=0x3A02,carrier_hz=2100,modulation_hz=51*9600/32768)
    d.check_encoding(p)
    return spec


def build_wire(samples):
    samples = list(samples)
    if len(samples) != 640 or any(not -32768 <= x <= 32767 for x in samples):
        raise ValueError('wire probe requires exactly 640 signed PCM16 samples')
    p = d.Program('v8_tone_sport1_loop',next_result=RESULT)
    p.emit(d.jump(0),*[d.nop()]*3)
    for v in range(d.IRQ_VECTORS):
        index = 2 if v == 7 else 3
        p.emit(d.modify(index,index),d.rts(rti=True),d.nop(),d.nop())
    p.pm[0] = d.jump(len(p.pm))
    d.preamble(p)
    p.emit(d.load_dreg('AX0',0x0407),d.store_dreg(0x3FFF,'AX0'))
    for reg,value in {'I0':TX,'L0':640,'M0':1,'I4':RX,'L4':1024,'M4':1,
                      'I2':0,'L2':0,'M2':1,'I3':0,'L3':0,'M3':1,
                      'I6':RESULT,'L6':0,'M6':1}.items():
        p.emit(d.load_dag(reg,value))
    p.emit(d.load_dreg('AX0',0x0043),d.store_dreg(0x3FEF,'AX0'),
           d.load_dreg('AX0',0x3C0F),d.store_dreg(0x3FF2,'AX0'),
           d.load_group3('ICNTL',3),d.load_group3('IFC',0xFF),
           d.load_group3('CNTR',8191),d.load_dreg('AX0',0x0C07),
           d.store_dreg(0x3FFF,'AX0'),d.load_group3('IMASK',6))
    loop = len(p.pm)
    p.emit(d.do_until(loop+2048,0xE),*[d.nop()]*2048)
    p.emit(d.load_group3('IMASK',0),d.load_dreg('AX0',0x0407),d.store_dreg(0x3FFF,'AX0'),
           d.nop(),d.nop(),d.load_group3('CNTR',1024))
    loop = len(p.pm)
    p.emit(d.do_until(loop+2,0xE),d.compute_dm(0,'AX0',0,0,dag2=True),
           d.compute_dm(0,'AX0',2,2,dag2=True,write=True))
    for reg in ('I0','I4','I2','I3'):
        group,index = d.dag_index(reg)
        p.emit(d.reg_move(0,d.DREG['AX0'],group,index),
               d.compute_dm(0,'AX0',2,2,dag2=True,write=True))
    p.finish()
    p.next_result = RESULT+1024+4
    p.dm_init = [[TX+n,v & 65535] for n,v in enumerate(samples)]+[[RX+n,0xBEEF] for n in range(1024)]
    spec = p.as_json()
    spec['result_base'] = RESULT
    spec['tone'] = dict(kind='wire',rate=WIRE_RATE,samples=samples,count=1024,wait_nops=8191*2048)
    d.check_encoding(p)
    return spec


def signed(words):
    return [v-65536 if v & 32768 else v for v in words]


def spectrum(samples,rate,low=1600,high=2300):
    """Windowed frequency scan for a short captured carrier, in 1 Hz steps."""
    if len(samples) < 2 or rate <= 0:
        raise ValueError('spectrum requires at least two samples and a positive rate')
    mean = sum(samples)/len(samples)
    windowed = [(x-mean)*(0.5-0.5*math.cos(2*math.pi*n/(len(samples)-1)))
                for n,x in enumerate(samples)]
    def power(hz):
        coeff = 2*math.cos(2*math.pi*hz/rate)
        a = b = 0.0
        for x in windowed:
            z = x+coeff*a-b
            b,a = a,z
        return max(0,a*a+b*b-coeff*a*b)
    powers = {hz:power(hz) for hz in range(low,high+1)}
    peak = max(powers,key=powers.get)
    return dict(peak_hz=peak,peak_amplitude=max(abs(x) for x in samples),
                rms=math.sqrt(sum(x*x for x in samples)/len(samples)))


def wire_phase(spec,words):
    if len(words) < 1024:
        return None
    cycle = [v & 65535 for v in spec['tone']['samples']]
    return next((phase for phase in range(len(cycle))
                 if all(v == cycle[(phase+n) % len(cycle)] for n,v in enumerate(words[:1024]))),None)


def wire_from_generator(card):
    """Build an 8 kHz playback cycle from actual 9.6 kHz DSP output."""
    from analog_kernel_dispatch import RationalResampler
    if not card.get('finished') or not card.get('dsps'):
        raise ValueError('generator capture is incomplete')
    first = card['dsps'][0]['dm']
    if any(c['done'] != d.DONE_VALUE or c['dm'] != first for c in card['dsps']):
        raise ValueError('generator DSPs did not complete with identical output')
    samples = signed([int(w,16) for w in first[:-5]])
    converter = RationalResampler(5,6)
    converted = [max(-32768,min(32767,round(y))) for x in samples for y in converter.push(x)]
    spec = build_wire(converted[400:1040])
    spec['tone']['source'] = dict(generator_pcm_sha256=hashlib.sha256(
        struct.pack(f'<{len(samples)}h',*samples)).hexdigest(),
        conversion='RationalResampler 5/6, default 64 taps',offset=400)
    return spec


def write_wav(path,samples,rate):
    with wave.open(str(path),'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(struct.pack(f'<{len(samples)}h',*samples))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--count',type=int,default=7680)
    parser.add_argument('--wire-from-card',type=Path,help='build SPORT playback from a completed generator capture')
    parser.add_argument('--bundle',type=Path,help='also build the matching spare-card MIPS bundle and metadata')
    args = parser.parse_args()
    spec = wire_from_generator(json.loads(args.wire_from_card.read_text())) if args.wire_from_card else build_generator(args.count)
    args.out.write_text(json.dumps(spec,indent=1)+'\n')
    if args.bundle:
        import eicon_dsp_bench as bench
        wire = args.wire_from_card is not None
        image,meta = bench.build_bundle(spec,reset_hold=0x1E if wire else 0,
                                       reset_release=0x1F if wire else 1,
                                       boot_settle=5000000 if wire else 1000000)
        args.bundle.write_bytes(image)
        args.bundle.with_suffix('.meta.json').write_text(json.dumps(meta,indent=1)+'\n')


if __name__ == '__main__':
    main()
