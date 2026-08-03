#!/usr/bin/env python3
"""Decode V.34 phase-2 INFO messages straight off a capture, without the card.

Sessions 102--104 read the INFO result out of the firmware's own published
words and concluded that word 0 of the received message is ``0x2000``.  Every
statement in that chain passes through the DSP's demodulator, its framer and
this project's emulation of both, so a defect in any of them looks exactly like
a defect in the peer's message.  This tool removes all three: it demodulates
the captured audio in Python and validates each recovered message against the
CRC the transmitter computed, so a frame that reports here is a frame that was
genuinely on the wire.

The control channel is differentially encoded binary PSK.  Framing is the one
``tools/info_cc_framer_probe.py`` documents from PM 0x3520 onwards -- fill ones,
the 10-bit synchronisation code 0x372, the payload, then a CRC-16 (reflected
0x8408, preset 0xffff, transmitted LSB first and uncomplemented).  Nothing else
about the firmware is assumed: the payload length is searched rather than taken
from DM(0x1651), and the CRC is what accepts or rejects a candidate.

The packing report reproduces PM 0x358E, which takes the payload MSB first into
consecutive 16-bit words, and then the field split at PM 0x3d6f, so the decoded
message can be compared directly against DM(0x0608..0x060E) and the published
DM(0x3F88..0x3F8C).

Usage:
    python3 tools/v34_info.py CAPTURE.rx.ulaw
    python3 tools/v34_info.py CAPTURE.rx.ulaw --from 3.0 --to 6.0
    python3 tools/v34_info.py CAPTURE.ulaw --carrier 1200 --bit-rate 600
"""
from __future__ import annotations

import argparse
import cmath
import math
import sys
from dataclasses import dataclass
from pathlib import Path

SYNC_CODE = 0x372
SYNC_BITS = 10
CRC_BITS = 16
SAMPLE_RATE = 8000

# The two directions do not share a carrier.  Measured on the live captures:
# the card transmits its control channel on 1200 Hz and the analogue peer on
# 2400 Hz, both at 600 bit/s.  A capture holds one of each, so decoding a
# <prefix>.rx.ulaw at 1200 Hz alone recovers only the echo of our own
# transmissions and reports the peer as silent.
#
# Labelled by which end transmits, not by V.34 call/answer role: on these
# captures the endpoint's SIP role is `answer` (the analogue modem dials in),
# so mapping a carrier onto a V.34 role would be an inference the captures do
# not support.
MODES = ((1200.0, 600.0, 'card'), (2400.0, 600.0, 'peer'))


def decode_ulaw(code: int) -> int:
    """G.711 mu-law, matching RtpCapture.decode_ulaw in eicon_adsp_sip.py."""
    value = (~code) & 0xFF
    sample = (((value & 0x0F) << 3) + 0x84) << ((value & 0x70) >> 4)
    sample -= 0x84
    return -sample if value & 0x80 else sample


def decode_alaw(code: int) -> int:
    value = code ^ 0x55
    sample = (value & 0x0F) << 4
    segment = (value & 0x70) >> 4
    if segment == 0:
        sample += 8
    elif segment == 1:
        sample += 0x108
    else:
        sample = (sample + 0x108) << (segment - 1)
    return sample if value & 0x80 else -sample


def load_capture(path: Path, law: str | None) -> list[float]:
    data = path.read_bytes()
    if law is None:
        law = 'alaw' if path.suffix == '.alaw' else 'ulaw'
    table = [decode_alaw(c) if law == 'alaw' else decode_ulaw(c) for c in range(256)]
    return [float(table[byte]) for byte in data]


def crc_bit(crc: int, bit: int) -> int:
    """One bit of the framers' CRC-16 at PM 0x354e / 0x25cf."""
    if (crc ^ bit) & 1:
        return ((crc >> 1) ^ 0x8408) & 0xFFFF
    return (crc >> 1) & 0xFFFF


def crc_bits(payload: list[int]) -> int:
    crc = 0xFFFF
    for bit in payload:
        crc = crc_bit(crc, bit)
    return crc


def baseband(samples: list[float], carrier: float, symbol_len: float) -> list[complex]:
    """Downconvert to complex baseband and integrate, as a prefix sum.

    Returning the running integral lets a symbol be taken over any fractional
    window by differencing two linearly interpolated points, which is what the
    timing search needs.
    """
    step = -2.0 * math.pi * carrier / SAMPLE_RATE
    total = 0.0 + 0.0j
    prefix = [total]
    for index, value in enumerate(samples):
        total += value * cmath.exp(1j * step * index)
        prefix.append(total)
    return prefix


def integrate(prefix: list[complex], start: float, end: float) -> complex:
    def at(position: float) -> complex:
        if position <= 0.0:
            return prefix[0]
        if position >= len(prefix) - 1:
            return prefix[-1]
        low = int(position)
        frac = position - low
        return prefix[low] * (1.0 - frac) + prefix[low + 1] * frac

    return at(end) - at(start)


def symbols(prefix: list[complex], offset: float, symbol_len: float) -> list[complex]:
    out = []
    position = offset
    limit = len(prefix) - 1
    while position + symbol_len <= limit:
        out.append(integrate(prefix, position, position + symbol_len))
        position += symbol_len
    return out


def differential_bits(values: list[complex], invert: bool) -> list[int]:
    """DBPSK: a bit is the sign of the phase step between adjacent symbols."""
    bits = []
    for index in range(1, len(values)):
        product = values[index] * values[index - 1].conjugate()
        bit = 1 if product.real < 0.0 else 0
        bits.append(bit ^ 1 if invert else bit)
    return bits


@dataclass(frozen=True)
class Frame:
    sample: int          # capture sample of the first payload bit
    seconds: float
    payload: tuple[int, ...]
    carrier: float
    bit_rate: float
    offset: float
    inverted: bool

    def pack(self, msb_first: bool) -> tuple[int, ...]:
        """PM 0x358E's five 16-bit words, under either bit order.

        Which order the packer uses is not settled by static reading, so both
        are reported and the capture decides: DM(0x3F88) tracks payload bits
        0..3, which is the low nibble under LSB-first packing.
        """
        out = []
        for base in range(0, len(self.payload), 16):
            chunk = self.payload[base:base + 16]
            word = 0
            for index, bit in enumerate(chunk):
                word |= bit << (15 - index) if msb_first else bit << index
            out.append(word)
        return tuple(out)

    def fields(self, msb_first: bool) -> tuple[int, int, int]:
        """PM 0x3d6f's split of word 0 into DM(0x1703), (0x1704), (0x1705)."""
        words = self.pack(msb_first)
        word = words[0] if words else 0
        return word & 0x0007, (word >> 3) & 0x0007, (word >> 6) & 0x007F


def find_frames(bits: list[int], lengths: range) -> list[tuple[int, tuple[int, ...]]]:
    """Every position where the sync code is followed by a CRC-valid payload."""
    found = []
    window = 0
    mask = (1 << SYNC_BITS) - 1
    for index, bit in enumerate(bits):
        window = ((window << 1) | bit) & mask
        if window != SYNC_CODE or index + 1 < SYNC_BITS:
            continue
        start = index + 1
        for length in lengths:
            end = start + length
            if end + CRC_BITS > len(bits):
                break
            payload = bits[start:end]
            received = 0
            for offset in range(CRC_BITS):
                received |= bits[end + offset] << offset
            if received == crc_bits(payload):
                found.append((start, tuple(payload)))
    return found


def decode(samples: list[float], carrier: float, bit_rate: float,
           offsets: int, lengths: range, base_sample: int) -> list[Frame]:
    symbol_len = SAMPLE_RATE / bit_rate
    prefix = baseband(samples, carrier, symbol_len)
    frames: dict[tuple[int, tuple[int, ...]], Frame] = {}
    for step in range(offsets):
        offset = step * symbol_len / offsets
        values = symbols(prefix, offset, symbol_len)
        for invert in (False, True):
            bits = differential_bits(values, invert)
            for start, payload in find_frames(bits, lengths):
                # bits[i] is the step from symbol i to symbol i+1, so payload
                # bit 0 ends at symbol start+1.
                position = offset + (start + 1) * symbol_len
                key = (round(position / symbol_len), payload)
                if key in frames:
                    continue
                frames[key] = Frame(
                    sample=base_sample + int(position),
                    seconds=(base_sample + position) / SAMPLE_RATE,
                    payload=payload, carrier=carrier, bit_rate=bit_rate,
                    offset=offset, inverted=invert)
    return sorted(frames.values(), key=lambda frame: frame.sample)


def report(frames: list[Frame]) -> None:
    """Print one line per message, shortest CRC-valid length first.

    A frame whose payload is followed by fill ones often validates at several
    adjacent lengths, because the trailing fill can be absorbed into the
    payload and still leave a zero residue.  Those are one message, so they are
    grouped and the extra lengths are named rather than repeated in full.
    """
    if not frames:
        print('  no CRC-valid frames')
        return
    groups: dict[int, list[Frame]] = {}
    for frame in frames:
        key = next((k for k in groups if abs(k - frame.sample) <= 16), frame.sample)
        groups.setdefault(key, []).append(frame)
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda frame: len(frame.payload))
        frame = members[0]
        others = [len(other.payload) for other in members[1:]]
        extra = f'  (also validates at {", ".join(map(str, others))})' if others else ''
        bits = ''.join(str(bit) for bit in frame.payload)
        print(f'  {frame.seconds:8.3f}s  {len(frame.payload):3d} bits'
              f'{" inverted" if frame.inverted else ""}{extra}')
        print(f'            payload {bits}')
        for label, msb_first in (('lsb', False), ('msb', True)):
            words = ' '.join(f'{word:04x}' for word in frame.pack(msb_first))
            low, mid, high = frame.fields(msb_first)
            print(f'            {label}-first {words}   DM(0x1703)={low:#06x} '
                  f'DM(0x1704)={mid:#06x} DM(0x3F89)={high:#06x}')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('capture', type=Path, help='<prefix>.rx.ulaw or <prefix>.ulaw')
    ap.add_argument('--law', choices=('ulaw', 'alaw'), help='override the codec')
    ap.add_argument('--from', dest='start', type=float, default=0.0,
                    help='window start in seconds')
    ap.add_argument('--to', dest='end', type=float, help='window end in seconds')
    ap.add_argument('--carrier', type=float, help='carrier Hz (default 1200)')
    ap.add_argument('--bit-rate', type=float, help='bit rate (default 600)')
    ap.add_argument('--offsets', type=int, default=16,
                    help='timing phases searched per symbol (default 16)')
    ap.add_argument('--min-bits', type=int, default=8, help='shortest payload tried')
    ap.add_argument('--max-bits', type=int, default=120, help='longest payload tried')
    args = ap.parse_args()

    samples = load_capture(args.capture, args.law)
    base = int(args.start * SAMPLE_RATE)
    end = len(samples) if args.end is None else int(args.end * SAMPLE_RATE)
    window = samples[base:end]
    if not window:
        print('empty window', file=sys.stderr)
        return 2

    if args.carrier or args.bit_rate:
        modes = ((args.carrier or 1200.0, args.bit_rate or 600.0, 'custom carrier'),)
    else:
        modes = MODES

    lengths = range(args.min_bits, args.max_bits + 1)
    total = 0
    for carrier, bit_rate, role in modes:
        print(f'{args.capture.name}  {carrier:.0f} Hz  {bit_rate:.0f} bit/s  '
              f'({role} transmits)  {base / SAMPLE_RATE:.2f}..{end / SAMPLE_RATE:.2f}s')
        frames = decode(window, carrier, bit_rate, args.offsets, lengths, base)
        report(frames)
        total += len(frames)
    return 0 if total else 1


if __name__ == '__main__':
    raise SystemExit(main())
