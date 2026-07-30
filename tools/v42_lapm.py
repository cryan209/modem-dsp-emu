#!/usr/bin/env python3
"""Small V.42 LAPM endpoint for the emulated modem's synchronous bit pipe.

This is deliberately not a V.42bis compressor.  It implements HDLC framing,
XID response, link establishment, receive acknowledgements, and a bounded test
payload.  Bytes at this boundary are synchronous and bits are oldest-first;
the ADSP mailbox adapter is responsible for packing them into data-pump words.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

FLAG_BITS = (0, 1, 1, 1, 1, 1, 1, 0)  # 0x7e, least-significant bit first


def fcs16(data: bytes) -> int:
    """V.42/HDLC 16-bit FCS (x^16+x^12+x^5+1, reflected form)."""
    crc = 0xFFFF
    for octet in data:
        crc ^= octet
        for _ in range(8):
            crc = (crc >> 1) ^ (0x8408 if crc & 1 else 0)
    return crc ^ 0xFFFF


def octets_to_bits(data: bytes) -> list[int]:
    return [(octet >> bit) & 1 for octet in data for bit in range(8)]


def bits_to_octets(bits: list[int]) -> bytes | None:
    if len(bits) % 8:
        return None
    return bytes(sum(bits[i + bit] << bit for bit in range(8))
                 for i in range(0, len(bits), 8))


def encode_frame(body: bytes) -> list[int]:
    """Return one flag-delimited, bit-stuffed HDLC frame."""
    protected = body + fcs16(body).to_bytes(2, 'little')
    stuffed: list[int] = []
    ones = 0
    for bit in octets_to_bits(protected):
        stuffed.append(bit)
        if bit:
            ones += 1
            if ones == 5:
                stuffed.append(0)
                ones = 0
        else:
            ones = 0
    return list(FLAG_BITS) + stuffed + list(FLAG_BITS)


class HdlcDecoder:
    """Streaming flag detector, unstuffing and FCS verifier."""

    def __init__(self) -> None:
        self.raw: list[int] = []
        self.in_frame = False
        self.good = 0
        self.bad_fcs = 0
        self.aborts = 0

    @staticmethod
    def _unstuff(raw: list[int]) -> list[int] | None:
        out: list[int] = []
        ones = 0
        i = 0
        while i < len(raw):
            bit = raw[i]
            out.append(bit)
            i += 1
            if bit:
                ones += 1
                if ones > 6:
                    return None
                if ones == 5:
                    if i >= len(raw):
                        return None
                    if raw[i] != 0:
                        return None
                    i += 1
                    ones = 0
            else:
                ones = 0
        return out

    def feed(self, bits: list[int]) -> list[bytes]:
        frames: list[bytes] = []
        for bit in bits:
            self.raw.append(bit & 1)
            if len(self.raw) < 8 or tuple(self.raw[-8:]) != FLAG_BITS:
                continue
            content = self.raw[:-8]
            self.raw = []
            if self.in_frame and content:
                unstuffed = self._unstuff(content)
                octets = bits_to_octets(unstuffed) if unstuffed is not None else None
                if octets is None or len(octets) < 4:
                    self.aborts += 1
                elif fcs16(octets[:-2]) == int.from_bytes(octets[-2:], 'little'):
                    self.good += 1
                    frames.append(octets[:-2])
                else:
                    self.bad_fcs += 1
            self.in_frame = True
        # Keep only enough pre-flag noise to recognize a flag crossing calls.
        if not self.in_frame and len(self.raw) > 7:
            self.raw = self.raw[-7:]
        return frames


@dataclass
class LapmStats:
    xid_rx: int = 0
    xid_tx: int = 0
    sabme_rx: int = 0
    ua_tx: int = 0
    i_rx: int = 0
    rr_tx: int = 0
    disc_rx: int = 0


class LapmEndpoint:
    """Answer-side minimum LAPM state machine.

    The originator leads initial XID and SABME exchange.  We conservatively
    echo its valid XID information as the response; this accepts its proposed
    N401/window values without claiming V.42bis parameters it did not offer.
    """

    XID = 0xAF
    SABME_MASKED = 0x6F
    DISC_MASKED = 0x43
    UA = 0x63
    RR = 0x01

    def __init__(self, log=print) -> None:
        self.decoder = HdlcDecoder()
        self.tx = deque(FLAG_BITS)
        self.log = log
        self.stats = LapmStats()
        self.connected = False
        self.vr = 0
        self.rx_data = bytearray()
        self._idle_index = 0

    def _queue(self, body: bytes, name: str) -> None:
        # A leading idle flag ensures separation if the previous queue ended in
        # fill; encode_frame supplies both delimiters and bit transparency.
        self.tx.extend(encode_frame(body))
        self.log(f'[v42] TX {name}: {body.hex()}')

    def feed(self, bits: list[int]) -> None:
        for frame in self.decoder.feed(bits):
            self._handle(frame)

    def _handle(self, frame: bytes) -> None:
        if len(frame) < 2:
            return
        address, control = frame[0], frame[1]
        self.log(f'[v42] RX control=0x{control:02x} address=0x{address:02x} '
                 f'length={len(frame)}')
        # P/F is bit 4 for U frames; mask it while identifying the function.
        ucontrol = control & 0xEF
        if ucontrol == self.XID:
            self.stats.xid_rx += 1
            self._queue(bytes((address, self.XID)) + frame[2:], 'XID response')
            self.stats.xid_tx += 1
        elif ucontrol == self.SABME_MASKED:
            self.stats.sabme_rx += 1
            self.connected = True
            self.vr = 0
            self._queue(bytes((address, self.UA | (control & 0x10))), 'UA')
            self.stats.ua_tx += 1
        elif ucontrol == self.DISC_MASKED:
            self.stats.disc_rx += 1
            self.connected = False
            self._queue(bytes((address, self.UA | (control & 0x10))), 'UA(DISC)')
            self.stats.ua_tx += 1
        elif control & 0x01 == 0 and len(frame) >= 3:
            # Extended (modulo-128) I frame: N(S) in octet 2, N(R)/P in 3.
            ns = (control >> 1) & 0x7F
            if ns == self.vr:
                self.rx_data.extend(frame[3:])
                self.vr = (self.vr + 1) & 0x7F
                self.stats.i_rx += 1
            poll = frame[2] & 1
            rr_control_2 = (self.vr << 1) | poll
            self._queue(bytes((address, self.RR, rr_control_2)), 'RR')
            self.stats.rr_tx += 1
        elif control & 0x03 == 0x01 and len(frame) >= 3:
            # RR/RNR/REJ/SREJ. No outbound I-frame window exists yet; a polled
            # supervisory command still requires an RR final response.
            if frame[2] & 1:
                self._queue(bytes((address, self.RR, (self.vr << 1) | 1)), 'RR(F)')
                self.stats.rr_tx += 1

    def take(self, count: int) -> list[int]:
        bits: list[int] = []
        while len(bits) < count:
            if self.tx:
                bits.append(self.tx.popleft())
            else:
                bits.append(FLAG_BITS[self._idle_index])
                self._idle_index = (self._idle_index + 1) % len(FLAG_BITS)
        return bits
