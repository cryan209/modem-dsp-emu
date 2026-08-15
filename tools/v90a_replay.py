#!/usr/bin/env python3
"""Replay a real digital modem's transmission into the V.90 APCM caller.

Every V.90 question this project has asked lately has been asked in the
loopback, where the analogue caller's peer is our own answerer -- and that
answerer parks on page 14 and transmits nothing (handoff.md, this session).  A
caller waiting on a silent peer tells you nothing about the caller.

`artifacts/eicon-native-tower/run48.*` removes the loopback from the question.
run48 is our card **answering a real analogue modem** over SIP, and it
connects, so:

    run48.ulaw      what our V90D transmitted -- a genuine digital-side
                    signal, which is exactly what a V90A caller must answer
    run48.rx.ulaw   what the real analogue modem transmitted back -- the
                    reference for what V90A itself should be emitting

This feeds the first into an analog109 kernel-dispatch caller and reports what
its APCM machine does with it.  The replay is open loop: the recording cannot
react to what we transmit, so a divergence in our transmit path shows what the
firmware does next, not what the call would have done.  That is enough to
answer the only question that matters here -- whether V90A responds at all to
a real peer, or whether it walks the same fixed 3.2 s script it walks into
silence.

    tools/v90a_replay.py artifacts/eicon-native-tower/run48.ulaw \\
        --reference artifacts/eicon-native-tower/run48.rx.ulaw

**Measured limit, found by running it.** Fed run48.ulaw, the caller walks
DIAL -> V.8 -> INFO at 4.05 s and stays on INFO for the rest of the 20.7 s
recording; it never reaches page 13, so the APCM machine never loads.  That is
not a defect it has found -- INFO is a two-way negotiation and an open-loop
recording cannot answer us, so the replay cannot carry the caller past it.
What it does establish is that the front half works against real audio rather
than against our own answerer.

Validating V90A itself needs a peer that reacts, which means either a real
digital modem on the other end of the SIP leg or an answerer that transmits.
The same tool run with `--role answer` against `run48.rx.ulaw` is the case that
*is* closed: that recording is what our card actually heard while connecting,
so replaying it reproduces run48's own state path.

The APCM machine's addresses are V90D's with every base moved, derived in this
session and recorded in handoff.md: outer scheduler PM 0x337b, record cursor
DM(0x120E), state DM(0x20F9) with the record laid out at the same offsets from
it that V90D uses from DM(0x1FF7), tone flag DM(0x10F3).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dial_tikrnl_drive as drive

V90A_ID = 0x026B
SAMPLE_RATE = 8000

# The APCM outer machine, as decoded this session.  Offsets from the state word
# DM(0x20F9) mirror V90D's from DM(0x1FF7), which is how the threshold lands on
# DM(0x20F7) -- the word the detector at PM 0x0d01 actually compares against.
DM_CURSOR = 0x120E
DM_STATE = 0x20F9
DM_DWELL = 0x20F8
DM_ARM = 0x20F4
DM_THRESH = 0x20F7
DM_EVENT = 0x10F3
DM_TEST0 = 0x20FE
DM_NEXT0 = 0x20FA
DM_TRNPROGRESS = 0x3FC2

SILENT = (0xFF, 0x7F)   # u-law and A-law idle codes


def decode_ulaw(code: int) -> int:
    """G.711 u-law codeword to signed linear, as RtpCapture decodes it."""
    value = (~code) & 0xFF
    sample = (((value & 0x0F) << 3) + 0x84) << ((value & 0x70) >> 4)
    sample -= 0x84
    return -sample if value & 0x80 else sample


def non_silent(data: bytes, second: int) -> float | None:
    chunk = data[second * SAMPLE_RATE:(second + 1) * SAMPLE_RATE]
    if not chunk:
        return None
    return sum(1 for code in chunk if code not in SILENT) / len(chunk) * 100


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('capture', type=Path,
                    help='u-law of what the peer transmitted, i.e. what this '
                         'modem receives -- run48.ulaw for a real V90D')
    ap.add_argument('--reference', type=Path,
                    help='u-law of what a real analogue modem replied with, '
                         'for a side-by-side transmit comparison')
    ap.add_argument('--role', default='calling', choices=('calling', 'answer'))
    ap.add_argument('--codec-rate', type=int, default=9600,
                    help='SPORT1 codec rate; 9600 is what V.8 asks for and '
                         '8000 is the setting that stops it hearing ANSam')
    ap.add_argument('--seconds', type=float, default=1e9)
    ap.add_argument('--tx-out', type=Path,
                    help='write the replayed transmission here, so it can be '
                         'compared with the reference sample by sample')
    args = ap.parse_args()

    drive.select_firmware_set('analog109')
    from analog_kernel_dispatch import AnalogKernelModem

    data = args.capture.read_bytes()
    limit = min(len(data), int(args.seconds * SAMPLE_RATE))
    card = AnalogKernelModem(modem_role=args.role, law='pcmu',
                             codec_rate=args.codec_rate)
    card.boot()
    # The Analog kernel-dispatch backend takes a role: its dial page asks for
    # V.8 by itself in either direction (eicon_adsp_sip.py's own comment).
    card.configure_modem(args.role, 'pcmu')
    dm = card.dm
    print(f'[replay] {args.capture} -> analog109 {args.role} '
          f'(codec {args.codec_rate}), {limit / SAMPLE_RATE:.1f} s')

    transmitted = bytearray()
    key = None
    pages = 0
    for index in range(limit):
        # The same conversion the live path does before frame_fast: the codec
        # boundary wants the line word, not the raw codeword.
        code = data[index]
        word = card.line_rx_word(code, decode_ulaw(code))
        sample = card.frame_fast(word, index)
        transmitted.append(sample & 0xFF if isinstance(sample, int) else 0)

        if len(card.switches) > pages:
            for at, page, wanted in card.switches[pages:]:
                print(f'{at / SAMPLE_RATE:9.3f}s  bootpage -> {page}, '
                      f'download 0x{wanted:04x}')
            pages = len(card.switches)

        if card.resident != V90A_ID:
            continue
        current = (dm[DM_CURSOR], dm[DM_STATE], dm[DM_TEST0], dm[DM_NEXT0])
        if current != key:
            key = current
            print(f'{index / SAMPLE_RATE:9.3f}s  [v90a] '
                  f'optr={dm[DM_CURSOR]:04x} state={dm[DM_STATE]:04x} '
                  f'dwell={dm[DM_DWELL]:04x} next0={dm[DM_NEXT0]:04x} '
                  f'test0={dm[DM_TEST0]:04x} arm={dm[DM_ARM]:04x} '
                  f'thresh={dm[DM_THRESH]:04x} event={dm[DM_EVENT]:04x} '
                  f'trn={dm[DM_TRNPROGRESS]:04x}')

    if args.tx_out:
        args.tx_out.write_bytes(bytes(transmitted))
        print(f'[replay] wrote {args.tx_out}')

    reference = args.reference.read_bytes() if args.reference else None
    print()
    print('  s      fed%   ours%' + ('   real%' if reference else ''))
    for second in range(int(limit / SAMPLE_RATE)):
        fed = non_silent(data, second)
        ours = non_silent(bytes(transmitted), second)
        row = f'{second:4d}  {fed:6.1f}  {ours:6.1f}'
        if reference is not None:
            real = non_silent(reference, second)
            row += f'  {real:6.1f}' if real is not None else '     -  '
        print(row)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
