#!/usr/bin/env python3
"""Split a Diva `divactrl mlog -A3` trace into per-direction G.711 files.

Each `AudioN (520)` record carries 260 16-bit words for B-channel N.  The high
byte of a word is what that channel received and the low byte is what it
transmitted, both G.711 at 8 kHz; on a card-to-card call through a digital
PBX one end's low byte reappears bit-exact as the other end's high byte.

    python3 tools/mlog_audio_split.py mlog1.txt OUTDIR --caller 1 --answerer 2

writes caller.tx.ulaw, caller.rx.ulaw, answerer.tx.ulaw, answerer.rx.ulaw.
Use --law pcma for A-law captures: this changes the suffix to .alaw without
transcoding. The historical default is pcmu; verify the actual bearer law.
"""
import argparse
import pathlib
import re

RECORD = re.compile(r' - Audio([12]) \(\d+\)')
SAMPLES = re.compile(r'SAMPLE\[\]((?: [0-9A-F]{4})+)')


def read_channels(path):
    channels = {'1': bytearray(), '2': bytearray()}
    current = None
    for line in open(path, errors='replace'):
        m = RECORD.search(line)
        if m:
            current = m.group(1)
            continue
        m = SAMPLES.search(line)
        if m and current is not None:
            for word in m.group(1).split():
                channels[current] += int(word, 16).to_bytes(2, 'big')
    return channels


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('mlog')
    ap.add_argument('outdir')
    ap.add_argument('--caller', default='1', choices=('1', '2'))
    ap.add_argument('--answerer', default='2', choices=('1', '2'))
    ap.add_argument('--law', default='pcmu', choices=('pcmu', 'pcma'),
                    help='actual trace encoding; labels files without transcoding')
    args = ap.parse_args()
    out = pathlib.Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    channels = read_channels(args.mlog)
    suffix = 'alaw' if args.law == 'pcma' else 'ulaw'
    for role, ch in (('caller', args.caller), ('answerer', args.answerer)):
        words = channels[ch]
        rx, tx = bytes(words[0::2]), bytes(words[1::2])
        (out / f'{role}.rx.{suffix}').write_bytes(rx)
        (out / f'{role}.tx.{suffix}').write_bytes(tx)
        print(f'{role}: B{ch}, {len(tx)} samples ({len(tx) / 8000:.2f} s)')


if __name__ == '__main__':
    main()
