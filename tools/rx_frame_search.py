#!/usr/bin/env python3
"""Score receive-framing hypotheses against a recorded RXD datagram stream.

`_service_rx_data()` makes three unverified assumptions about the data pump's
receive mailbox: how many bits of each RXD word belong to the datagram, which
end of the word the oldest bit is at, and -- when RXD0 and RXD1 are both valid
in one pass -- which of the two is older.  A live call can only test one guess
at a time, and Session 87 left the question open because a misframed stream and
a stream that was never demodulated look identical from the LAPM statistics.

`EICON_RX_TRACE=<path>` records the raw `(sample, count, mask, word)` of every
datagram the mailbox published.  This replays that capture under every
combination and scores each by how many HDLC frames pass FCS.

A hypothesis that frames real LAPM will stand out by orders of magnitude: valid
FCS is a 1-in-65536 accident, so even a handful of good frames is decisive.  If
*no* combination produces any, the stream is not framed data at all and the
fault is upstream of framing, in the receiver.

    tools/rx_frame_search.py capture.rxd
    tools/rx_frame_search.py capture.rxd --bits 3,4,13 --window 200000
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v42_lapm import HdlcDecoder

MAGIC = b'ERXD0001'
RECORD = struct.Struct('<IHHH')


def load(path: Path) -> list[tuple[int, int, int, int]]:
    raw = path.read_bytes()
    if not raw.startswith(MAGIC):
        raise SystemExit(f'{path}: not an RXD trace (bad magic)')
    body = raw[len(MAGIC):]
    count, extra = divmod(len(body), RECORD.size)
    if extra:
        # A capture from a killed run can end mid-record; the tail is dropped
        # rather than refused, because a truncated call is still worth scoring.
        print(f'note: {extra} trailing bytes ignored (capture truncated)',
              file=sys.stderr)
        body = body[:count * RECORD.size]
    return [RECORD.unpack_from(body, i * RECORD.size) for i in range(count)]


def bitstream(records, *, bits: int, msb_first: bool,
              swap_pairs: bool) -> list[int]:
    """Rebuild the receive bit stream under one framing hypothesis.

    ``swap_pairs`` reverses RXD0/RXD1 within a single service pass, which is
    the case that matters if the DSP fills the second register first.
    """
    out: list[int] = []
    pending: list[tuple[int, int]] = []      # (mask, word) for one sample
    sample = None

    def flush():
        if not pending:
            return
        group = pending[::-1] if swap_pairs and len(pending) > 1 else pending
        for _mask, word in group:
            if msb_first:
                out.extend((word >> (15 - bit)) & 1 for bit in range(bits))
            else:
                out.extend((word >> bit) & 1 for bit in range(bits))
        pending.clear()

    for rec_sample, _count, mask, word in records:
        if rec_sample != sample:
            flush()
            sample = rec_sample
        pending.append((mask, word))
    flush()
    return out


def score(bits_stream: list[int]) -> tuple[int, int, int]:
    decoder = HdlcDecoder()
    # Feed in chunks so a very long stream does not build one huge list.
    for start in range(0, len(bits_stream), 8192):
        decoder.feed(bits_stream[start:start + 8192])
    return decoder.good, decoder.bad_fcs, decoder.aborts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--bits', default='',
                        help='comma-separated bit counts to try; default is '
                             'the counts present in the capture plus 1..16')
    parser.add_argument('--window', type=int, default=0,
                        help='score only the first N datagrams')
    args = parser.parse_args()

    records = load(args.capture)
    if not records:
        raise SystemExit('capture is empty: the mailbox published no datagram')
    if args.window:
        records = records[:args.window]

    observed = sorted({rec[1] for rec in records})
    masks = sorted({rec[2] for rec in records})
    words = {rec[3] for rec in records}
    print(f'{len(records)} datagrams, '
          f'samples {records[0][0]}..{records[-1][0]}')
    print(f'bit counts published by the pump: {observed}')
    print(f'RXD masks seen: {[hex(m) for m in masks]}')
    print(f'distinct RXD words: {len(words)}')
    if len(words) <= 2:
        print('  -- the mailbox is publishing a constant; the receiver is not '
              'producing a demodulated stream')

    if args.bits:
        candidates = [int(value) for value in args.bits.split(',')]
    else:
        candidates = sorted(set(observed) | set(range(1, 17)))

    print()
    print(f'{"bits":>4} {"order":>10} {"pairs":>9} '
          f'{"good":>6} {"bad_fcs":>8} {"aborts":>7}')
    best = None
    for bits in candidates:
        for msb_first in (True, False):
            for swap_pairs in (False, True):
                stream = bitstream(records, bits=bits, msb_first=msb_first,
                                   swap_pairs=swap_pairs)
                good, bad, aborts = score(stream)
                order = 'MSB-first' if msb_first else 'LSB-first'
                pairs = 'swapped' if swap_pairs else 'in order'
                print(f'{bits:>4} {order:>10} {pairs:>9} '
                      f'{good:>6} {bad:>8} {aborts:>7}')
                if best is None or good > best[0]:
                    best = (good, bits, order, pairs)

    print()
    if best and best[0]:
        print(f'best: {best[0]} good frames at {best[1]} bits, {best[2]}, '
              f'RXD pairs {best[3]}')
    else:
        print('no hypothesis produced a single valid FCS. The receive stream '
              'is not framed data, so the fault is upstream of framing.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
