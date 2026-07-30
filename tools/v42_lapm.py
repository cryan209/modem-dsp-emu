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


def _pattern(text: str) -> tuple[int, ...]:
    return tuple(int(c) for c in text if c in '01')


# V.42 (03/2002) detection phase, 7.2.1. The Recommendation lists these left to
# right in order of transmission, low-order bit first, and each is one
# start-stop character over the synchronous link: start bit, seven data bits,
# parity, stop bit. Taken verbatim rather than derived, because the parity
# convention in the printed patterns is not worth reverse-engineering.
ODP_EVEN = _pattern('0 1000 1000 1')   # DC1, even parity
ODP_ODD = _pattern('0 1000 1001 1')    # DC1, odd parity
ADP_E = _pattern('0 1010 0010 1')      # (E)
ADP_C = _pattern('0 1100 0010 1')      # (C)
# Table 3/V.42: (E) and (C) separated by 8 to 16 ones means "V.42 supported".
ADP_SEPARATOR = (1,) * 12
ADP_V42_SUPPORTED = ADP_E + ADP_SEPARATOR + ADP_C + ADP_SEPARATOR
ADP_REPETITIONS = 10                   # 7.2.1.3: "at least ten times"


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
    i_tx: int = 0
    i_retx: int = 0
    rej_rx: int = 0
    rnr_rx: int = 0
    poll_tx: int = 0
    out_of_seq: int = 0
    odp_rx: int = 0
    adp_tx: int = 0


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
    RNR = 0x05
    REJ = 0x09
    SREJ = 0x0D

    def __init__(self, log=print, window: int = 15, n401: int = 128,
                 poll_after: int = 24, retransmit_after: int = 48,
                 detect: bool = True, detect_timeout: int = 600) -> None:
        self.decoder = HdlcDecoder()
        # 7.2.1.3: the answerer transmits mark until it sees the ODP. Starting
        # on flags instead tells the originator the protocol phase has already
        # begun, and it never gets the ADP it is waiting for. Detection may be
        # disabled by the user (7.2.1.2), which goes straight to flags.
        self.detection = 'mark' if detect else 'protocol'
        self.detect_timeout = detect_timeout
        self._detect_ticks = 0
        self._detect_reported = False
        self._odp_window: "deque[int]" = deque(maxlen=len(ODP_EVEN))
        self._odp_count = 0
        self._odp_parity: int | None = None
        self.tx: "deque[int]" = deque() if detect else deque(FLAG_BITS)
        self.log = log
        self.stats = LapmStats()
        self.connected = False
        self.vr = 0
        self.rx_data = bytearray()
        self.address = 0x03
        # Transmit side. V.42 numbers I frames modulo 128, matching the
        # two-octet control field the receive path above already decodes.
        self.vs = 0          # next N(S) to assign
        self.va = 0          # lowest unacknowledged N(S); window lower edge
        self.window = window  # k
        self.n401 = n401     # maximum information field, octets
        self.tx_stream = bytearray()
        self.unacked: "dict[int, bytes]" = {}
        self.peer_busy = False
        # Recovery is counted in take() calls rather than seconds. This link has
        # no wall clock: the bit pipe is clocked by the data pump, and the
        # harness can run far from real time, so a T401 in seconds would fire
        # at meaningless points during a replay. These are service-call counts
        # from the last acknowledgement.
        self.poll_after = poll_after
        self.retransmit_after = retransmit_after
        self._since_ack = 0

    # -- transmit ---------------------------------------------------------
    def send(self, data: bytes) -> None:
        """Queue application bytes for transmission as I frames."""
        self.tx_stream.extend(data)

    @property
    def outstanding(self) -> int:
        return (self.vs - self.va) & 0x7F

    def _fill_window(self) -> None:
        """Emit as many I frames as the window and pending data allow."""
        if not self.connected or self.peer_busy:
            return
        while self.tx_stream and self.outstanding < self.window:
            payload = bytes(self.tx_stream[:self.n401])
            del self.tx_stream[:len(payload)]
            body = bytes((self.address, (self.vs << 1) & 0xFE,
                          (self.vr << 1) & 0xFE)) + payload
            self.unacked[self.vs] = body
            self._queue(body, f'I N(S)={self.vs} N(R)={self.vr} '
                              f'{len(payload)}B')
            self.stats.i_tx += 1
            self.vs = (self.vs + 1) & 0x7F

    def _ack(self, nr: int) -> None:
        """Release acknowledged I frames. N(R) acknowledges everything below it."""
        released = 0
        while self.va != nr:
            if self.unacked.pop(self.va, None) is not None:
                released += 1
            self.va = (self.va + 1) & 0x7F
            if released > 128:  # malformed N(R); do not spin
                break
        if released:
            self._since_ack = 0

    def _retransmit_from(self, nr: int) -> None:
        """Go-back-N: requeue every unacknowledged frame from N(R) onward."""
        self.va = nr
        self.vs = nr
        pending = []
        index = nr
        while index in self.unacked:
            pending.append(self.unacked[index])
            index = (index + 1) & 0x7F
        # Rebuild rather than replay: N(R) has to carry our current receive
        # state, not the value it had when the frame was first sent.
        self.unacked.clear()
        for body in pending:
            body = bytes((body[0], (self.vs << 1) & 0xFE,
                          (self.vr << 1) & 0xFE)) + body[3:]
            self.unacked[self.vs] = body
            self._queue(body, f'I retransmit N(S)={self.vs}')
            self.stats.i_retx += 1
            self.vs = (self.vs + 1) & 0x7F
        self._since_ack = 0

    def _queue(self, body: bytes, name: str) -> None:
        # A leading idle flag ensures separation if the previous queue ended in
        # fill; encode_frame supplies both delimiters and bit transparency.
        self.tx.extend(encode_frame(body))
        self.log(f'[v42] TX {name}: {body.hex()}')

    # -- detection phase (7.2.1) -----------------------------------------
    def _scan_odp(self, bits: list[int]) -> None:
        """Look for the ODP: four DC1s of alternating parity (7.2.1.3)."""
        for bit in bits:
            self._odp_window.append(bit)
            if len(self._odp_window) < self._odp_window.maxlen:
                continue
            found = tuple(self._odp_window)
            if found == ODP_EVEN:
                parity = 0
            elif found == ODP_ODD:
                parity = 1
            else:
                continue
            self._odp_window.clear()
            if parity == self._odp_parity:
                continue           # not alternating; the spec asks for both
            self._odp_parity = parity
            self._odp_count += 1
            self.stats.odp_rx += 1
            if self._odp_count >= 4:
                self._begin_adp()
                return

    def _begin_adp(self) -> None:
        """Answer the ODP with the "V.42 supported" ADP, ten times."""
        self.detection = 'adp'
        for _ in range(ADP_REPETITIONS):
            self.tx.extend(ADP_V42_SUPPORTED)
        self.stats.adp_tx += 1
        self.log(f'[v42] ODP detected ({self._odp_count} DC1s); sending the '
                 f'"V.42 supported" ADP {ADP_REPETITIONS} times')

    def _enter_protocol(self, reason: str) -> None:
        if self.detection == 'protocol':
            return
        self.detection = 'protocol'
        self.log(f'[v42] protocol phase ({reason})')

    def feed(self, bits: list[int]) -> None:
        if self.detection == 'mark':
            self._scan_odp(bits)
        frames = self.decoder.feed(bits)
        if frames and self.detection != 'protocol':
            # 7.2.1.3: receipt of an LAPM frame is itself the start of the
            # protocol phase, so an originator with detection disabled still
            # works without us having seen an ODP.
            self._enter_protocol('LAPM frame received')
        for frame in frames:
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
            self.address = address
            # SABME resets both directions. Anything already queued belongs to
            # the previous link and its sequence numbers are now invalid;
            # unsent application bytes are kept, since they were never on the
            # wire and the terminal above does not know a reset happened.
            self.vr = self.vs = self.va = 0
            self.unacked.clear()
            self.peer_busy = False
            self._since_ack = 0
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
            self.address = address
            self._ack((frame[2] >> 1) & 0x7F)
            if ns == self.vr:
                self.rx_data.extend(frame[3:])
                self.vr = (self.vr + 1) & 0x7F
                self.stats.i_rx += 1
            else:
                # Out of sequence. Our RR carries the unchanged V(R), which is
                # what asks the peer to go back; REJ would be the sharper
                # response but duplicates that request on every subsequent
                # frame already in flight.
                self.stats.out_of_seq += 1
            poll = frame[2] & 1
            rr_control_2 = (self.vr << 1) | poll
            self._queue(bytes((address, self.RR, rr_control_2)), 'RR')
            self.stats.rr_tx += 1
        elif control & 0x03 == 0x01 and len(frame) >= 3:
            supervisory = control & 0x0F
            nr = (frame[2] >> 1) & 0x7F
            self.address = address
            self._ack(nr)
            if supervisory == self.RNR:
                # Peer's receiver is busy: stop filling the window, but keep
                # answering polls so the link does not look dead.
                self.peer_busy = True
                self.stats.rnr_rx += 1
            else:
                self.peer_busy = False
            if supervisory in (self.REJ, self.SREJ):
                self.stats.rej_rx += 1
                self._retransmit_from(nr)
            if frame[2] & 1:
                # A polled supervisory command requires a final response.
                self._queue(bytes((address, self.RR, (self.vr << 1) | 1)),
                            'RR(F)')
                self.stats.rr_tx += 1

    def _service(self) -> None:
        """Per-service-call transmit work: fill the window, then recover.

        Recovery is deliberately conservative. An unacknowledged window that
        stops moving is first probed with RR(P), because the likeliest cause is
        a lost acknowledgement rather than a lost I frame, and the peer's
        response carries the N(R) that resolves it without resending anything.
        Only if that does not move the window does it go back N.
        """
        self._fill_window()
        if not self.outstanding:
            self._since_ack = 0
            return
        self._since_ack += 1
        if self._since_ack == self.poll_after:
            self._queue(bytes((self.address, self.RR, (self.vr << 1) | 1)),
                        'RR(P) window probe')
            self.stats.poll_tx += 1
        elif self._since_ack >= self.retransmit_after:
            self._retransmit_from(self.va)

    def take(self, count: int) -> list[int]:
        """Hand `count` bits to the data pump, idling on HDLC flags.

        Idle fill is queued a whole flag at a time rather than synthesised per
        bit. Generating it per bit meant that when a frame was queued partway
        through a flag, the next take switched to frame bits and abandoned the
        flag half-emitted; the receiver then had to resync and consumed the
        frame doing it, so the first frame after any idle gap was lost. Keeping
        the flag in the same queue as the frames makes a partial flag finish
        before frame bits follow it, even across calls.
        """
        if self.detection == 'protocol':
            self._service()
        elif self.detection == 'mark':
            self._detect_ticks += 1
            if (self._detect_ticks > self.detect_timeout
                    and not self._detect_reported):
                # T400 (9.1.1). 7.2.1.3 has the answerer fall back to
                # non-error-correcting operation here; there is no asynchronous
                # mode on this side to fall back to, so stay on mark and say so
                # rather than starting flags the originator will not expect.
                self._detect_reported = True
                self.log('[v42] no ODP within the detection timeout; the peer '
                         'is not offering V.42, staying on mark')
        bits: list[int] = []
        while len(bits) < count:
            if self.tx:
                bits.append(self.tx.popleft())
                continue
            if self.detection == 'mark':
                bits.append(1)              # 7.2.1.3: mark until the ODP
                continue
            if self.detection == 'adp':
                # Every repetition has been handed over; the originator stops
                # its ODP on seeing two adjacent ADPs (7.2.1.2).
                self._enter_protocol('ADP sent')
                self._service()
            if not self.tx:
                self.tx.extend(FLAG_BITS)
        return bits
