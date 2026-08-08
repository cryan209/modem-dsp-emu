#!/usr/bin/env python3
"""Test how DIAL calls V.8.

Per addspv90guide §5.4.1: after the host runs the calling/answer-mode
training script (GEN_SETUP1=0x048C calling / 0x0484 answer, GEN_SETUP2,
WSTATUS=0x2000) while DIAL is active, "the dial page requests the host to
boot the V.8 page." DIAL signals this by writing bootpage_nr (DM 0x3FB0)
and setting the bootrequestbit/Boot in RSTATUS_dbs (DM 0x3FA5).

This harness runs DIAL standalone, programs the GEN_SETUP init sequence,
feeds the line, and watches 0x3FB0 (bootpage_nr) and 0x3FA5 (RSTATUS) to
catch the V.8 (page 6) request.
"""
import argparse, ctypes, math
from pathlib import Path

ADSP = ctypes.CDLL(str(Path(__file__).resolve().parent /
                       "adsp2181emu" / "libadsp2181.dylib"))
ADSP.adsp2181_create.restype = ctypes.c_void_p
for n, a in [('reset', [ctypes.c_void_p]), ('pm', [ctypes.c_void_p]),
             ('dm', [ctypes.c_void_p]), ('run', [ctypes.c_void_p, ctypes.c_int]),
             ('pc', [ctypes.c_void_p]), ('idle', [ctypes.c_void_p]),
             ('set_pc', [ctypes.c_void_p, ctypes.c_uint16]),
             ('call', [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16]),
             ('host_write', [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16])]:
    getattr(ADSP, 'adsp2181_' + n).argtypes = a
ADSP.adsp2181_pm.restype = ctypes.POINTER(ctypes.c_uint32)
ADSP.adsp2181_dm.restype = ctypes.POINTER(ctypes.c_uint16)
ADSP.adsp2181_pc.restype = ctypes.c_uint16
ADSP.adsp2181_idle.restype = ctypes.c_int

# data-pump write database base
DB = 0x3EE0
# read database registers of interest
BOOTPAGE_NR = 0x3FB0   # offset 0xD0
RSTATUS_DBS = 0x3FA5   # offset 0xC5
TRNPROG = 0x3FAD       # offset 0xCD
LINE_RX = 0x3F09       # offset 0x29 (line RX sample)


def rw(p):
    w = {}
    for line in Path(p).read_text().splitlines():
        f = line.split()
        if len(f) == 2:
            w[int(f[0], 16)] = int(f[1], 16)
    return w


def linear_to_mulaw(s) -> int:
    """ITU-T G.711 mu-law, conventional octet order.

    The segment search this replaced shifted by 5 where the 16-bit input needs
    8 -- `exponent = (magnitude).bit_length() - 8` -- so every magnitude at or
    above 3964 (-18.3 dBfs) ran the loop past segment 7 and took the
    saturation arm. A 20000-amplitude sine, which is what `make_g711_stimulus`
    and every standalone drive here asks for, came out clipped on 87.5% of its
    samples using 7 of the 33 codes it should span: a square wave whose third
    harmonic aliases to about 1700 Hz at 8 kHz. Verified exhaustively against
    the reference over all 65536 inputs.
    """
    sign = 0x80 if s < 0 else 0x00
    magnitude = min(abs(s), 32635) + 132     # bias 0x84 on 14 bits << 2
    exponent = magnitude.bit_length() - 8         # 0..7
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa)) & 0xFF

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--frames', type=int, default=800)
    ap.add_argument('--mode', choices=['calling', 'answer', 'none'], default='calling')
    ap.add_argument('--freq', type=int, default=0)
    args = ap.parse_args()

    repo = Path(__file__).resolve().parent.parent
    K = str(repo / 'artifacts/eicon-dsp/build-117-926/kernel/0009-diva-server-pri-30m-kernel')
    D = str(repo / 'artifacts/eicon-dsp/dial/0262-dial-fsk-fax.f34-overlay')

    cpu = ADSP.adsp2181_create()
    ADSP.adsp2181_reset(cpu)
    pm = ADSP.adsp2181_pm(cpu)
    dm = ADSP.adsp2181_dm(cpu)
    for a, v in rw(K + '/pm.words').items():
        pm[a] = v
    for a, v in rw(K + '/dm.words').items():
        dm[a] = v
    ADSP.adsp2181_run(cpu, 2000)
    for a, v in rw(D + '/pm.words').items():
        pm[a] = v
    for a, v in rw(D + '/dm.words').items():
        dm[a] = v

    # Program the data-pump init (guide §5.4.1 Table 12/14/15)
    ADSP.adsp2181_host_write(cpu, DB + 0x00, 0x00C4)  # GEN_SETUP0
    if args.mode == 'calling':
        ADSP.adsp2181_host_write(cpu, DB + 0x01, 0x048C)  # GEN_SETUP1 calling mode
    elif args.mode == 'answer':
        ADSP.adsp2181_host_write(cpu, DB + 0x01, 0x0484)  # GEN_SETUP1 answer mode
    else:
        ADSP.adsp2181_host_write(cpu, DB + 0x01, 0x0040)  # default
    ADSP.adsp2181_host_write(cpu, DB + 0x02, 0x0030)  # GEN_SETUP2
    ADSP.adsp2181_host_write(cpu, DB + 0x03, 0xF0FD)  # INFO0_SETUP
    ADSP.adsp2181_host_write(cpu, DB + 0x0E, 0x2000)  # WSTATUS activate (bit D)
    # Norm selection: V.34+V8 (guide Table 13)
    ADSP.adsp2181_host_write(cpu, DB + 0x0F, 0x0001)  # Norm_H (V8)
    ADSP.adsp2181_host_write(cpu, DB + 0x10, 0x0100)  # Norm_L (V34+V8)
    print(f'[v8] mode={args.mode} GEN_SETUP1 written; frames={args.frames}')

    samples = ([linear_to_mulaw(int(20000 * math.sin(
        2 * math.pi * args.freq * i / 8000))) for i in range(8000)]
        if args.freq > 0 else [0x80] * 8000)

    print(f'[v8] init: bootpage_nr={dm[BOOTPAGE_NR]:04x} '
          f'RSTATUS={dm[RSTATUS_DBS]:04x} trnprog={dm[TRNPROG]:04x}')
    prev = None
    for f in range(args.frames):
        dm[LINE_RX] = samples[f % len(samples)]
        dm[0x3F08] = samples[f % len(samples)]
        ADSP.adsp2181_call(cpu, 0x1BBD, 0x02A8)   # line handler
        ADSP.adsp2181_run(cpu, 5000)
        ADSP.adsp2181_call(cpu, 0x1B9C, 0x02A8)   # state dispatcher
        ADSP.adsp2181_run(cpu, 5000)
        now = (dm[BOOTPAGE_NR], dm[RSTATUS_DBS], dm[TRNPROG])
        if now != prev:
            bp = now[0]
            tag = ''
            if bp == 6:
                tag = '  <<< V.8 PAGE REQUEST'
            elif bp == 7:
                tag = '  <<< INFO page'
            elif bp == 8:
                tag = '  <<< V.34 page'
            print(f'  frame {f:4d}: bootpage_nr={bp:04x} RSTATUS={now[1]:04x} '
                  f'trnprog={now[2]:04x}{tag}')
            prev = now
            if bp == 6:
                print('[v8] *** DIAL requested the V.8 page (bootpage_nr=6) ***')
                print('[v8] host would now load V.8 overlay (0x025f) via IDMA '
                      'and set BOOTFINISHED in WSTATUS.')
                return
    print(f'[v8] final: bootpage_nr={dm[BOOTPAGE_NR]:04x} '
          f'RSTATUS={dm[RSTATUS_DBS]:04x} (no V.8 request seen)')


if __name__ == '__main__':
    main()
