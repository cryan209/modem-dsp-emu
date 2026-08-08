#!/usr/bin/env python3
"""Decode an `AT#UD` last-call report against Microsoft's Unimodem spec.

`AT#UD` is Microsoft's Unimodem Diagnostic Command, not a Conexant extension,
so the key numbering is documented rather than guessed:

    https://download.microsoft.com/download/1/6/1/161ba512-40e2-4cc9-843a-923143f3456c/umud10.rtf

The modem answers with one line per key:

    DIAG <2A4D3263 20=0C>

Only the keys a given modem implements appear. Most of the fields that would
bear on a V.90 decision are `Rec10` (recommended, not required) and the
CX93001-EIS V0.2013 reports none of them — see the note in `--missing`. Worse,
specification note 5 says v1.0 was written while V.90 was still in development
and carries no V.90 parameters at all, so this command cannot report why a
modem declined PCM. Recorded here so the next person does not go looking twice.

    unimodem_ud.py CAPTURE.txt        # a file of DIAG lines
    tools/cx_at.py --dev DEV cmd 'AT#UD' | unimodem_ud.py -
"""
from __future__ import annotations

import argparse
import re
import sys

# Table 1. (name, decoder) keyed by the hex key number.
DEC_HZ = lambda v: f'{v} Hz'
DEC_SYM = lambda v: f'{v} symbol/s'
DEC_BPS = lambda v: f'{v} bit/s'
DEC_NDBM = lambda v: f'-{v} dBm'
DEC_INT = lambda v: str(v)

TABLE2 = {0: 'no previous call', 1: 'no dial tone', 2: 'reorder, network busy',
          3: 'busy', 4: 'no recognized signal', 5: 'voice detected',
          6: 'text telephone (V.18)',
          7: 'data answering signal detected (V.25 ANS / V.8 ANSam)',
          8: 'data calling signal detected (V.25 CT / V.8 CI)',
          9: 'fax answering signal (T.30 CED/DIS)', 0xA: 'fax calling (T.30 CNG)',
          0xB: 'V.8bis signal detected'}
TABLE6 = {0: 'V.17', 1: 'V.21', 2: 'V.22', 3: 'V.22bis', 4: 'V.23 constant carrier',
          5: 'V.23 switched carrier', 6: 'V.26bis', 7: 'V.26ter', 8: 'V.27ter',
          9: 'V.29 HD', 0xA: 'V.32', 0xB: 'V.32bis', 0xC: 'V.34', 0xD: 'V.34 HD',
          0xE: 'V.90 issue 1 (asymmetric)', 0xF: 'V.90 issue 2 (symmetric)'}
TABLE9 = {0x00: 'cause unidentified', 0x01: 'no previous call',
          0x02: 'call still in progress', 0x2A: 'call attempts limit exceeded',
          0x2B: 'extension phone off hook',
          0x2C: 'call setup fail timer expired (e.g. S7 timeout)',
          0x2E: 'loop current interrupted', 0x2F: 'no dial tone',
          0x3C: 'carrier lost'}

KEYS = {
    0x00: ('diagnostic spec revision', lambda v: f'{v >> 4}.{v & 0xF}'),
    0x01: ('call setup result', lambda v: TABLE2.get(v, f'unknown {v:#x}')),
    0x02: ('multimedia mode', lambda v: 'data only' if v == 0 else f'{v:#x}'),
    0x03: ('DTE-DCE interface mode', DEC_INT),
    0x04: ('V.8 CM octet string', str),
    0x05: ('V.8 JM octet string', str),
    0x10: ('received signal power', DEC_NDBM),
    0x11: ('transmit signal power', DEC_NDBM),
    0x12: ('estimated noise level', DEC_NDBM),
    0x13: ('normalized mean squared error', DEC_INT),
    0x14: ('near echo loss', lambda v: f'{v} dB'),
    0x15: ('far echo loss', lambda v: f'{v} dB'),
    0x16: ('far echo delay', lambda v: f'{v} ms'),
    0x17: ('round trip delay', lambda v: f'{v} ms'),
    0x18: ('V.34 INFO bit map', lambda v: f'{v:#x}'),
    0x20: ('transmit carrier', lambda v: TABLE6.get(v, f'unknown {v:#x}')),
    0x21: ('receive carrier', lambda v: TABLE6.get(v, f'unknown {v:#x}')),
    0x22: ('transmit symbol rate', DEC_SYM),
    0x23: ('receive symbol rate', DEC_SYM),
    0x24: ('transmit carrier frequency', DEC_HZ),
    0x25: ('receive carrier frequency', DEC_HZ),
    0x26: ('initial transmit rate', DEC_BPS),
    0x27: ('initial receive rate', DEC_BPS),
    0x30: ('temporary carrier loss events', DEC_INT),
    0x31: ('rate renegotiation events', DEC_INT),
    0x32: ('retrains requested', DEC_INT),
    0x33: ('retrains granted', DEC_INT),
    0x34: ('final transmit rate', DEC_BPS),
    0x35: ('final receive rate', DEC_BPS),
    0x40: ('protocol negotiation result', DEC_INT),
    0x41: ('error control frame size', lambda v: f'{v} bytes'),
    0x42: ('error control timeouts', DEC_INT),
    0x43: ('error control NAKs received', DEC_INT),
    0x44: ('compression negotiation result', DEC_INT),
    0x45: ('compression dictionary size', lambda v: f'{v} bytes'),
    0x50: ('transmit flow control', DEC_INT),
    0x51: ('receive flow control', DEC_INT),
    0x52: ('characters sent from DTE', DEC_INT),
    0x53: ('characters sent to DTE', DEC_INT),
    0x54: ('transmit characters lost', DEC_INT),
    0x55: ('receive characters lost', DEC_INT),
    0x56: ('transmit I-frames', DEC_INT),
    0x57: ('received I-frames', DEC_INT),
    0x58: ('transmit I-frame errors', DEC_INT),
    0x59: ('received I-frame errors', DEC_INT),
    0x60: ('termination cause', lambda v: TABLE9.get(v, f'unknown {v:#x}')),
    0x61: ('call waiting events', DEC_INT),
}

# Spec ranges, for flagging a value the modem reports outside its field.
RANGES = {0x10: 0x2F, 0x11: 0x1F, 0x12: 0x64, 0x13: 0xFF, 0x14: 0x3F, 0x15: 0x3F,
          0x16: 0x3F, 0x17: 0xFFF, 0x22: 0x1F40, 0x23: 0x1F40, 0x24: 0xFA0,
          0x25: 0xFA0, 0x26: 0xFA00, 0x27: 0xFA00, 0x34: 0xFA00, 0x35: 0xFA00,
          0x50: 2, 0x51: 2}

# The keys that would bear on a V.90/PCM decision, and are Rec10 rather than
# required. Reported when absent, because "the modem did not say" and "we did
# not ask" look identical otherwise.
V90_RELEVANT = (0x04, 0x05, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('source', help="file of DIAG lines, or - for stdin")
    args = ap.parse_args()
    text = sys.stdin.read() if args.source == '-' else open(args.source).read()

    seen = {}
    for key, value in re.findall(r'DIAG\s*<\s*\w+\s+([0-9A-Fa-f]+)=([0-9A-Fa-f]+)>', text):
        seen[int(key, 16)] = value
    if not seen:
        print('no DIAG lines found', file=sys.stderr)
        return 1

    for key in sorted(seen):
        raw = seen[key]
        name, decode = KEYS.get(key, (f'reserved/proprietary key {key:#04x}', str))
        try:
            shown = decode(int(raw, 16))
        except Exception:
            shown = raw
        limit = RANGES.get(key)
        flag = ''
        if limit is not None and int(raw, 16) > limit:
            flag = f'   [!] outside the spec range 0..{limit:#x}'
        print(f'  {key:#04x}  {name:<34s} {raw:>8s}  {shown}{flag}')

    absent = [k for k in V90_RELEVANT if k not in seen]
    if absent:
        print('\n  Not reported by this modem (all Rec10, not required):')
        for key in absent:
            print(f'    {key:#04x}  {KEYS[key][0]}')
        print('  Specification note 5: v1.0 predates V.90 and defines no V.90\n'
              '  parameters, so #UD cannot report why a modem declined PCM.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
