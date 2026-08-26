#!/usr/bin/env python3
"""Small V.42 LAPM endpoint for the emulated modem's synchronous bit pipe.

It implements HDLC framing, XID negotiation, link establishment, reliable
information transfer, and optional V.42bis compression.  Bytes at this
boundary are synchronous and bits are oldest-first; the ADSP mailbox adapter
is responsible for packing them into data-pump words.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from v42bis import (V42bisDecoder, V42bisEncoder, V42bisError,
                    V42bisParameters)
from v44 import V44Decoder, V44Encoder, V44Error, V44Parameters

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


# Table 11a/V.42, Note 1. The PI=3 parameter value is a 32-bit HDLC optional
# functions mask, bit 1 being the low-order bit of the first octet transmitted.
# Bit positions 2, 4, 8, 9, 12 and 16 are not negotiable: "the transmitter of an
# XID command frame shall set bit positions 2, 4, 8, 9, 12 and 16 to 1. The
# transmitter of an XID response frame shall also set these bit positions to 1,
# except bit position 16 shall be set to 0 if bit position 17 is set to 1."
# Bit 17 is the 32-bit FCS, which this endpoint does not offer, so bit 16 (the
# 16-bit FCS) stays set. The four bits that are genuinely negotiable here -- 3
# and 24 (SREJ), 14 (TEST) and 17 -- are all left clear, which is a valid "no
# request/no agreement" for every optional procedure in clause 10.
#
# Sending zero here, as this did until now, is a conformance failure and not a
# cosmetic one: bit 9 is the only statement that the sender uses extended
# (modulo 128) sequence numbering, and bit 16 the only statement that it uses a
# 16-bit FCS. A peer that reads the mask rather than ignoring it sees a
# responder that has agreed to neither, which is a live candidate for the CX
# retransmitting XID and never advancing to SABME.
HDLC_OPTIONAL_FUNCTIONS = ((1 << 1) | (1 << 3) | (1 << 7)
                           | (1 << 8) | (1 << 11) | (1 << 15))   # 0x0000898A


@dataclass
class XidParameters:
    """The V.42 12.2.2 general-purpose parameter-negotiation subset."""
    n401_tx: int = 128
    n401_rx: int = 128
    k_tx: int = 15
    k_rx: int = 15
    optional_functions: int = HDLC_OPTIONAL_FUNCTIONS
    # Table 11a Note 1 states PL = 4 for PI = 3, but a CX93001 sends the mask
    # in three octets -- ISO/IEC 8885's "smallest number of octets needed to
    # express the value", the same rule Table 11b states for its own
    # parameters. Both readings are defensible and only one interoperates with
    # any given peer, so the length is carried alongside the value and a
    # responder answers in the form the initiator used. See
    # LapmEndpoint._handle().
    optional_functions_octets: int = 4
    v42bis: V42bisParameters | None = None
    v44: V44Parameters | None = None


def encode_xid_parameters(params: XidParameters) -> bytes:
    """Encode V.42 FI=0x82/GI=0x80 parameter negotiation."""
    if params.v42bis is not None and params.v44 is not None:
        raise ValueError('an XID may offer only one compression algorithm')
    if not (1 <= params.n401_tx <= 0xFFFF
            and 1 <= params.n401_rx <= 0xFFFF
            and 1 <= params.k_tx <= 127 and 1 <= params.k_rx <= 127):
        raise ValueError('XID parameters outside V.42 ranges')
    if not 1 <= params.optional_functions_octets <= 4:
        raise ValueError('the optional-functions mask is 1 to 4 octets')
    # N401 is expressed in bits in XID, despite being octets operationally.
    values = {
        3: params.optional_functions.to_bytes(
            params.optional_functions_octets, 'little'),
        5: (params.n401_tx * 8).to_bytes(2, 'big'),
        6: (params.n401_rx * 8).to_bytes(2, 'big'),
        7: bytes((params.k_tx,)),
        8: bytes((params.k_rx,)),
    }
    body = bytearray((0x82, 0x80))
    fields = bytearray()
    for pi in (3, 5, 6, 7, 8):
        value = values[pi]
        fields += bytes((pi, len(value))) + value
    body += len(fields).to_bytes(2, 'big') + fields
    if params.v42bis is not None:
        compression = params.v42bis
        fields = bytearray()
        for pi, value in (
                (0, b'V42'),
                (1, bytes((compression.directions,))),
                (2, compression.codewords.to_bytes(2, 'big')),
                (3, bytes((compression.max_string,)))):
            fields += bytes((pi, len(value))) + value
        body += bytes((0xF0,)) + len(fields).to_bytes(2, 'big') + fields
    if params.v44 is not None:
        compression = params.v44
        body.append(0xFF)
        for pi, value in (
                (0x40, b'V44'),
                (0x41, bytes((compression.capability,))),
                (0x42, bytes((compression.directions,))),
                (0x43, compression.tx_codewords.to_bytes(2, 'big')),
                (0x44, compression.rx_codewords.to_bytes(2, 'big')),
                (0x45, bytes((compression.tx_max_string,))),
                (0x46, bytes((compression.rx_max_string,))),
                (0x47, compression.tx_history.to_bytes(2, 'big')),
                (0x48, compression.rx_history.to_bytes(2, 'big'))):
            body += bytes((pi, len(value))) + value
    return bytes(body)


def parse_xid_parameters(info: bytes) -> XidParameters | None:
    """Parse recognized V.42 XID parameters; ignore unknown fields."""
    if not info or info[0] != 0x82:
        return None
    pos = 1
    result = XidParameters()
    while pos < len(info):
        # ISO/IEC 8885 GI=0xff is the user-data subfield. Unlike parameter
        # groups it has no group-length field: its contents run to the FCS.
        # The CX puts V.44 capability TLVs here even with compression disabled
        # (its direction byte is zero). Treating the first two user bytes as a
        # length rejects the already complete V.42 group and makes our response
        # fall back to a different optional-functions width.
        if info[pos] == 0xFF:
            pos += 1
            values: dict[int, bytes] = {}
            while pos < len(info):
                if pos + 2 > len(info):
                    return None
                pi, length = info[pos], info[pos + 1]
                pos += 2
                value = info[pos:pos + length]
                if len(value) != length:
                    return None
                pos += length
                values[pi] = bytes(value)
            if values.get(0x40) == b'V44':
                expected = {0x41: 1, 0x42: 1, 0x43: 2, 0x44: 2,
                            0x45: 1, 0x46: 1, 0x47: 2, 0x48: 2}
                if any(len(values.get(pi, b'')) != length
                       for pi, length in expected.items()):
                    return None
                try:
                    result.v44 = V44Parameters(
                        values[0x41][0], values[0x42][0],
                        int.from_bytes(values[0x43], 'big'),
                        int.from_bytes(values[0x44], 'big'),
                        values[0x45][0], values[0x46][0],
                        int.from_bytes(values[0x47], 'big'),
                        int.from_bytes(values[0x48], 'big'))
                except ValueError:
                    return None
            break
        if pos + 3 > len(info):
            return None
        gi = info[pos]
        gl = int.from_bytes(info[pos + 1:pos + 3], 'big')
        pos += 3
        end = pos + gl
        if end > len(info):
            return None
        if gi == 0x80:
            while pos < end:
                if pos + 2 > end:
                    return None
                pi, length = info[pos], info[pos + 1]
                pos += 2
                value = info[pos:pos + length]
                if len(value) != length:
                    return None
                pos += length
                if pi == 3 and 1 <= length <= 4:
                    result.optional_functions = int.from_bytes(value, 'little')
                    result.optional_functions_octets = length
                elif pi in (5, 6) and length == 2:
                    bits = int.from_bytes(value, 'big')
                    if bits == 0 or bits % 8:
                        return None
                    setattr(result, 'n401_tx' if pi == 5 else 'n401_rx',
                            bits // 8)
                elif pi in (7, 8) and length == 1 and value[0]:
                    setattr(result, 'k_tx' if pi == 7 else 'k_rx', value[0])
        elif gi == 0xF0:
            identifier = None
            directions = None
            codewords = 512
            max_string = 6
            while pos < end:
                if pos + 2 > end:
                    return None
                pi, length = info[pos], info[pos + 1]
                pos += 2
                value = info[pos:pos + length]
                if len(value) != length:
                    return None
                pos += length
                if pi == 0 and length == 3:
                    identifier = bytes(value)
                elif pi == 1 and length == 1 and value[0] <= 3:
                    directions = value[0]
                elif pi == 2 and length == 2:
                    codewords = int.from_bytes(value, 'big')
                elif pi == 3 and length == 1:
                    max_string = value[0]
            if identifier == b'V42' and directions is not None:
                try:
                    result.v42bis = V42bisParameters(
                        directions, codewords, max_string)
                except ValueError:
                    return None
        else:
            pos = end
    return result


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
    rej_tx: int = 0
    frmr_tx: int = 0
    disc_tx: int = 0
    disc_rx: int = 0
    i_tx: int = 0
    i_retx: int = 0
    rej_rx: int = 0
    rnr_rx: int = 0
    poll_tx: int = 0
    out_of_seq: int = 0
    odp_rx: int = 0
    adp_tx: int = 0
    adp_rx: int = 0
    ua_rx: int = 0
    sabme_tx: int = 0
    reestablish: int = 0
    suspensions: int = 0
    discarded_in_establishment: int = 0


class LapmEndpoint:
    """Answer-side minimum LAPM state machine.

    The originator leads initial XID and SABME exchange.  We conservatively
    take the smaller of its proposal and ours for N401 and the window, which
    accepts its values without claiming V.42bis parameters it did not offer.
    The optional-functions mask is the one part of the response that is not
    negotiated: HDLC_OPTIONAL_FUNCTIONS is what Table 11a/V.42 requires of any
    XID transmitter, and clause 10's optional procedures stay unrequested.
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
                 detect: bool = True, detect_timeout: int = 600,
                 role: str = 'answerer', n400: int = 3,
                 reestablish: int = 1, trace: bool = False,
                 inactivity_after: int | None = None,
                 compression: bool = False,
                 compression_codewords: int = 512,
                 compression_string: int = 32,
                 v44: bool = False,
                 v44_codewords: int = 512,
                 v44_string: int = 32,
                 v44_history: int = 1024) -> None:
        if role not in ('answerer', 'originator'):
            raise ValueError("role must be 'answerer' or 'originator'")
        if compression and v44:
            raise ValueError('V.42bis and V.44 cannot both be requested')
        self.role = role
        # Per-frame tracing is off by default. Establishment is a handful of
        # frames and worth every line; information transfer is not the same
        # activity at all -- a PPP call moving a web page put 4,329 RX lines and
        # 800 RR lines into one log, and a megabyte transfer would be tens of
        # thousands. What the frame trace is *for* is XID and establishment
        # going wrong, and those are over before the volume starts, so the
        # counters in `stats` are what a data-phase problem is read from.
        self.trace = trace
        self.decoder = HdlcDecoder()
        # 7.2.1.3: the answerer transmits mark until it sees the ODP. Starting
        # on flags instead tells the originator the protocol phase has already
        # begun, and it never gets the ADP it is waiting for. Detection may be
        # disabled by the user (7.2.1.2), which goes straight to flags.
        if detect:
            self.detection = 'mark' if role == 'answerer' else 'odp'
        else:
            self.detection = 'protocol'
        self.detect_timeout = detect_timeout
        self._detect_ticks = 0
        self._detect_reported = False
        self._detect_bits = 0
        self._detect_ones = 0
        self._odp_window: "deque[int]" = deque(maxlen=len(ODP_EVEN))
        self._adp_window: "deque[int]" = deque(maxlen=len(ADP_V42_SUPPORTED))
        self._adp_count = 0
        self._odp_count = 0
        self._odp_parity: int | None = None
        self.tx: "deque[int]" = deque() if detect else deque(FLAG_BITS)
        self.log = log
        self.stats = LapmStats()
        self.connected = False
        self.raw_mode = False
        self._raw_rx_bits: list[int] = []
        self.vr = 0
        self.rx_data = bytearray()
        # Table 10/V.42: DLCI 0 is the DTE-to-DTE connection. It is not carried
        # in XID frames, so it is learned from whatever the peer addresses.
        self.dlci = 0
        self._awaiting_ua = False
        self._originator = role == 'originator'
        self._compression_requested = compression
        self._compression_local = (V42bisParameters(
            3, compression_codewords, compression_string)
            if compression else None)
        self._v44_requested = v44
        self._v44_local = (V44Parameters(
            directions=3,
            tx_codewords=v44_codewords, rx_codewords=v44_codewords,
            tx_max_string=v44_string, rx_max_string=v44_string,
            tx_history=v44_history, rx_history=v44_history)
            if v44 else None)
        self.xid = XidParameters(n401_tx=n401, n401_rx=n401,
                                 k_tx=window, k_rx=window,
                                 v42bis=self._compression_local,
                                 v44=self._v44_local)
        self.tx_compressor: V42bisEncoder | V44Encoder | None = None
        self.rx_decompressor: V42bisDecoder | V44Decoder | None = None
        if not detect and self._originator:
            self._begin_originator_protocol()
        # Transmit side. V.42 numbers I frames modulo 128, matching the
        # two-octet control field the receive path above already decodes.
        self.vs = 0          # next N(S) to assign
        self.va = 0          # lowest unacknowledged N(S); window lower edge
        self.window = window  # k
        self.n401 = n401     # maximum information field, octets
        self.tx_stream = bytearray()
        self._tx_transfer = bytearray()
        self.unacked: "dict[int, bytes]" = {}
        self.peer_busy = False
        # Recovery is counted in take() calls rather than seconds. This link has
        # no wall clock: the bit pipe is clocked by the data pump, and the
        # harness can run far from real time, so a T401 in seconds would fire
        # at meaningless points during a replay. These are service-call counts
        # from the last acknowledgement.
        self.poll_after = poll_after
        self.retransmit_after = retransmit_after
        self.n400 = max(1, n400)
        self.inactivity_after = inactivity_after
        self._since_ack = 0
        self._retries = 0
        self._inactivity = 0
        self._establish_ticks = 0
        # Datagrams left to sit out after a line disturbance. See
        # line_disturbed(): every one of the five live PPP calls that got this
        # far died in a modem retrain, because these counters advance per
        # datagram and the datagrams keep coming while the pump is carrying
        # training signals instead of the LAPM stream.
        self._suspended_for = 0
        self._suspend_reason = ''
        # A link that has failed is *down*, not merely disconnected: `failed`
        # holds the reason and stops the recovery machinery from running again
        # on the next datagram. Without it, an exhausted N400 left `unacked`
        # populated and `_retries` at the limit, so every subsequent take()
        # re-entered the same branch and re-announced the disconnect: 247,513
        # identical "T401 retry limit" lines on one live PPP call, 84% of the
        # log, with the call itself still up and nothing above it ever told.
        # (The cost of that is the log, not the clock: printing a line to a
        # redirected file measures 2.3 us unbuffered on the rig, so even at
        # 1,333 lines a second it is 0.3% of a core. It buried the run rather
        # than slowing it.)
        self.failed: str | None = None
        # Incremented each time the link is (re-)established, so a bridge above
        # can tell "still the same link" from "the same object, new link".
        self.generation = 0
        self.reestablish = max(0, reestablish)
        self._reestablish_left = self.reestablish

    # -- addressing (8.2.1) -----------------------------------------------
    #
    # Table 6/V.42 makes the C/R bit depend on the direction *and* on which end
    # originated the call, so a frame cannot be addressed by echoing whatever
    # arrived: an answerer that replies 0x03 to everything sends its commands
    # -- I frames, RR(P) probes, DISC -- with the C/R value that marks them as
    # responses. The DLCI stays the same in both directions; only C/R moves.
    #
    #   command   originator -> answerer   C/R = 1
    #             answerer   -> originator C/R = 0
    #   response  originator -> answerer   C/R = 0
    #             answerer   -> originator C/R = 1
    #
    # For an answerer that works out as 0x01 for commands and 0x03 for
    # responses, and the reverse for an originator.
    @property
    def command_address(self) -> int:
        return (self.dlci << 2) | (0x02 if self._originator else 0x00) | 0x01

    @property
    def response_address(self) -> int:
        return (self.dlci << 2) | (0x00 if self._originator else 0x02) | 0x01

    @property
    def address(self) -> int:
        """The address of frames this endpoint sends in reply to a command."""
        return self.response_address

    def _is_command(self, address: int) -> bool:
        """Whether a received frame is a command, from Table 6/V.42."""
        return bool(address & 0x02) != self._originator

    def _learn_dlci(self, address: int) -> None:
        dlci = (address >> 2) & 0x3F
        if dlci != self.dlci:
            self.log(f'[v42] peer addresses DLCI {dlci}, not {self.dlci}')
            self.dlci = dlci

    # -- transmit ---------------------------------------------------------
    def send(self, data: bytes) -> None:
        """Queue application bytes for transmission as I frames."""
        self.tx_stream.extend(data)

    @property
    def data_ready(self) -> bool:
        """Whether the DTE may exchange data (LAPM or raw fallback)."""
        return self.connected or self.raw_mode

    @property
    def outstanding(self) -> int:
        return (self.vs - self.va) & 0x7F

    def _fill_window(self) -> None:
        """Emit as many I frames as the window and pending data allow."""
        if not self.connected or self.peer_busy:
            return
        if self.tx_compressor is not None and self.tx_stream:
            self._tx_transfer.extend(self.tx_compressor.feed(self.tx_stream))
            self._tx_transfer.extend(self.tx_compressor.flush())
            self.tx_stream.clear()
        source = self._tx_transfer if self.tx_compressor is not None else self.tx_stream
        while source and self.outstanding < self.window:
            payload = bytes(source[:self.n401])
            del source[:len(payload)]
            body = bytes((self.command_address, (self.vs << 1) & 0xFE,
                          (self.vr << 1) & 0xFE)) + payload
            self.unacked[self.vs] = body
            self._queue(body, f'I N(S)={self.vs} N(R)={self.vr} '
                              f'{len(payload)}B')
            self.stats.i_tx += 1
            self.vs = (self.vs + 1) & 0x7F

    def _ack(self, nr: int) -> bool:
        """Release acknowledged I frames, rejecting invalid N(R) values."""
        if ((nr - self.va) & 0x7F) > self.outstanding:
            self.log(f'[v42] invalid N(R)={nr}; V(A)={self.va} '
                     f'V(S)={self.vs}')
            return False
        released = 0
        while self.va != nr:
            if self.unacked.pop(self.va, None) is not None:
                released += 1
            self.va = (self.va + 1) & 0x7F
            if released > 128:  # malformed N(R); do not spin
                break
        if released:
            self._since_ack = 0
            self._retries = 0
        return True

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
            body = bytes((self.command_address, (self.vs << 1) & 0xFE,
                          (self.vr << 1) & 0xFE)) + body[3:]
            self.unacked[self.vs] = body
            self._queue(body, f'I retransmit N(S)={self.vs}')
            self.stats.i_retx += 1
            self.vs = (self.vs + 1) & 0x7F
        self._since_ack = 0
        # _retries is deliberately *not* cleared here. It counts consecutive
        # unacknowledged recovery attempts, and clearing it inside the recovery
        # made the N400 limit unreachable from the timeout path: _service
        # cleared it here and immediately set it back to 1, so a link whose
        # peer had stopped acknowledging went back-N for ever instead of
        # disconnecting. On a lossy call that is a retransmit storm which
        # itself causes more loss -- 40,363 retransmissions for 100 frames
        # sent, against 63 on a clean call in the same run. Only a window that
        # actually moves (_ack) or an explicit REJ clears it.

    @staticmethod
    def _sequenced(control: int) -> bool:
        """Whether a control field belongs to an I or S frame.

        The U frames are the ones there are a fixed few of -- XID, SABME, UA,
        DISC, FRMR -- and they are the ones worth a line unconditionally. I and
        S frames scale with the traffic, so they are what `trace` gates.
        """
        return control & 0x03 != 0x03

    def _queue(self, body: bytes, name: str) -> None:
        # A leading idle flag ensures separation if the previous queue ended in
        # fill; encode_frame supplies both delimiters and bit transparency.
        self.tx.extend(encode_frame(body))
        if self.trace or not self._sequenced(body[1] if len(body) > 1 else 0):
            self.log(f'[v42] TX {name}: {body.hex()}')

    # -- line disturbances (retrain, rate renegotiation) -------------------
    @property
    def suspended(self) -> bool:
        return self._suspended_for > 0

    def line_disturbed(self, reason: str, ticks: int | None = None) -> None:
        """Hold every timer while the physical link is not carrying the stream.

        T401, T403 and the poll counter advance per datagram because that is
        the only clock this endpoint has. A retrain or a rate renegotiation
        does not stop the datagrams -- the pump keeps asking for one every
        6 samples and putting training signals on the line instead -- so a
        three-second V.42 recovery budget expires inside a retrain that the
        peer is also in, and the link is declared dead over a fault that never
        happened. That is what killed all five PPP calls of the 17:51 run:
        every T401 fired with TrnProgress on the handshake ladder (0x0040 to
        0x0080) or mid rate change, and the peer came back afterwards still
        numbering from where it left off.

        The pending bit queue goes too. Whatever was half-transmitted when the
        line went is not going to arrive, and the peer's HDLC has to resync
        past it either way; anything that mattered is in `unacked` and is what
        T401 exists to resend once the line is back.

        Re-arming is idempotent, so the caller may say this every datagram for
        as long as the disturbance lasts and the window extends behind it.
        """
        if ticks is None:
            # A round of T401 past the end of the disturbance: the peer has its
            # own resynchronisation to do and its first frame afterwards should
            # not land on a counter that is already most of the way to N400.
            ticks = self.retransmit_after
        if not self.suspended:
            self.stats.suspensions += 1
            self.log(f'[v42] line disturbed ({reason}); holding the LAPM '
                     f'timers')
        self._suspend_reason = reason
        self._suspended_for = max(self._suspended_for, max(1, ticks))
        self.tx.clear()

    def line_restored(self, reason: str = '') -> None:
        """The pump is carrying the stream again; stop holding the timers.

        The end of a disturbance is an event the pump knows and a timer can
        only guess at, and guessing cost the 18:20 call: the hold expired one
        T401 after the last re-arm, with DM(0x3FC2) at 0x00a6 and still
        climbing, and T401 then fired four seconds before the pump reached
        synchronous state. A retrain's tail -- 0x0060 up to 0x00c4 -- ran 14
        seconds on that call, five times the hold it was given.
        """
        if not self.suspended:
            return
        self._suspended_for = 0
        self._resume(reason or 'the pump reported synchronous state')

    def _resume(self, how: str = 'the hold expired') -> None:
        """Restart the timers from now, not from where the disturbance left."""
        self.log(f'[v42] line back after {self._suspend_reason} ({how}); '
                 f'LAPM timers resume with {self.outstanding} frame(s) '
                 f'unacknowledged')
        self._suspend_reason = ''
        self._since_ack = 0
        self._establish_ticks = 0
        self._inactivity = 0
        # A retrain is not evidence about the peer, so it must not spend the
        # recovery budget that decides whether the peer is still there.
        self._retries = 0

    def _reset_transmit(self) -> None:
        """Drop the transmit window: nothing in it belongs to a new link."""
        self.vs = self.va = 0
        self.unacked.clear()
        self.peer_busy = False
        self._since_ack = 0
        self._retries = 0
        self._tx_transfer.clear()

    def _establish(self, reason: str) -> None:
        """Enter the information-transfer state, from either direction.

        SABME resets both directions, so this is the one place that clears the
        sequence numbers, and the one place that clears `failed`: a peer that
        establishes again is the recovery, whether it does so after our DISC or
        after our own SABME.
        """
        self.connected = True
        self.raw_mode = False
        self._awaiting_ua = False
        self.vr = 0
        self._reset_transmit()
        self._inactivity = 0
        self._establish_ticks = 0
        self.failed = None
        self._reestablish_left = self.reestablish
        self.generation += 1
        if self.tx_compressor is not None:
            self.tx_compressor.reset()
        if self.rx_decompressor is not None:
            self.rx_decompressor.reset()
        self.log(f'[v42] LAPM connected ({reason}), link {self.generation}')

    def _link_failure(self, reason: str) -> None:
        """N400 recovery attempts exhausted: re-establish, or give up.

        V.42 8.4.9 makes re-establishment -- not disconnection -- the response
        to an unrecoverable error in the information-transfer state, and it is
        the difference between a transient one-way outage costing a few seconds
        and costing the call. The budget is finite because a peer that has
        genuinely gone is not going to answer a SABME either, and the failed
        establishment is what finally reports the link as down.
        """
        if self._reestablish_left <= 0 or not self.connected:
            self._disconnect(reason)
            return
        self._reestablish_left -= 1
        self.stats.reestablish += 1
        self.connected = False
        self._reset_transmit()
        # Unsent application bytes go with the window: they were queued against
        # a link that is being reset underneath them, and the peer's PPP finds
        # its own frame boundaries again from the next flag either way.
        self.tx_stream.clear()
        self._queue(bytes((self.command_address, self.SABME_MASKED | 0x10)),
                    'SABME(P) re-establish')
        self.stats.sabme_tx += 1
        self._awaiting_ua = True
        self._establish_ticks = 0
        self.log(f'[v42] {reason}; re-establishing the data link '
                 f'({self._reestablish_left} further attempt(s) allowed)')

    def _disconnect(self, reason: str) -> None:
        """Terminate locally after an unrecoverable LAPM exception."""
        if self.failed is not None:
            return
        if self.connected:
            self._queue(bytes((self.command_address, self.DISC_MASKED | 0x10)),
                        'DISC(P)')
            self.stats.disc_tx += 1
        self.connected = False
        self.raw_mode = False
        self._awaiting_ua = False
        self._reset_transmit()
        self.tx_stream.clear()
        self.failed = reason
        self.log(f'[v42] disconnected: {reason}')

    def _set_compression(self, negotiated: V42bisParameters | None) -> None:
        """Install directional codecs for the negotiated XID relationship."""
        self.tx_compressor = None
        self.rx_decompressor = None
        self._tx_transfer.clear()
        if negotiated is None or not negotiated.directions:
            return
        # P0 is expressed relative to the XID initiator. Bit 0 is the
        # initiator-to-responder direction and bit 1 is responder-to-initiator.
        tx_bit = 1 if self._originator else 2
        rx_bit = 2 if self._originator else 1
        if negotiated.directions & tx_bit:
            self.tx_compressor = V42bisEncoder(
                negotiated.codewords, negotiated.max_string)
        if negotiated.directions & rx_bit:
            self.rx_decompressor = V42bisDecoder(
                negotiated.codewords, negotiated.max_string)
        self.log('[v42bis] negotiated directions='
                 f'{negotiated.directions} P1={negotiated.codewords} '
                 f'P2={negotiated.max_string}')

    def _set_v44_compression(self, peer: V44Parameters | None,
                             negotiated: V44Parameters | None) -> None:
        """Install V.44 codecs using limits paired across XID directions."""
        self.tx_compressor = None
        self.rx_decompressor = None
        self._tx_transfer.clear()
        if peer is None or negotiated is None:
            return
        # P0 is relative to each XID sender: peer receive is our transmit,
        # and peer transmit is our receive.
        if peer.directions & 2:
            self.tx_compressor = V44Encoder(
                negotiated.tx_codewords, negotiated.tx_max_string,
                negotiated.tx_history)
        if peer.directions & 1:
            self.rx_decompressor = V44Decoder(
                negotiated.rx_codewords, negotiated.rx_max_string,
                negotiated.rx_history)
        self.log('[v44] negotiated peer directions='
                 f'{peer.directions} TX={negotiated.tx_codewords}/'
                 f'{negotiated.tx_max_string}/{negotiated.tx_history} '
                 f'RX={negotiated.rx_codewords}/'
                 f'{negotiated.rx_max_string}/{negotiated.rx_history}')

    def _send_frmr(self, frame: bytes, *, invalid_nr: bool = False,
                   too_long: bool = False) -> None:
        """Send the five-octet FRMR information field from §8.2.4.12."""
        rejected_control = frame[1] if len(frame) > 1 else 0
        flags = (0x08 if invalid_nr else 0) | (0x04 if too_long else 0)
        info = bytes((rejected_control, 0,
                      (self.vs << 1) & 0xFE,
                      (self.vr << 1) & 0xFE, flags))
        self._queue(bytes((self.response_address, 0x97)) + info, 'FRMR(F)')
        self.stats.frmr_tx += 1
        self._disconnect('FRMR')

    # -- detection phase (7.2.1) -----------------------------------------
    def _scan_odp(self, bits: list[int]) -> None:
        """Look for the ODP: four DC1s of alternating parity (7.2.1.3)."""
        # A failed detection has two completely different causes that the ODP
        # counter alone cannot tell apart: the peer never sent the pattern, or
        # it sent it and the path corrupted it. The mark ratio separates them.
        # All ones means the peer is idle and this end is waiting for something
        # that was never coming; anything near half means real data arrived and
        # the pattern match is what failed.
        self._detect_bits += len(bits)
        self._detect_ones += sum(bits)
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

    def detection_summary(self) -> str:
        """What the detection phase actually received, in one line."""
        if not self._detect_bits:
            return 'no bits reached the detector at all'
        mark = 100.0 * self._detect_ones / self._detect_bits
        shape = ('peer is sending mark/idle' if mark > 97 else
                 'peer is sending data' if 35 < mark < 65 else
                 'peer stream is neither mark nor balanced data')
        return (f'{self._detect_bits} bits scanned, {mark:.1f}% ones '
                f'({shape}), ODP matches {self._odp_count}')

    def _scan_adp(self, bits: list[int]) -> None:
        """Originator-side detection: require two adjacent ADPs."""
        for bit in bits:
            self._adp_window.append(bit)
            if (len(self._adp_window) == self._adp_window.maxlen
                    and tuple(self._adp_window) == ADP_V42_SUPPORTED):
                self._adp_count += 1
                self.stats.adp_rx += 1
                self._adp_window.clear()
                if self._adp_count >= 2:
                    self._begin_originator_protocol()
                    return

    def _begin_originator_protocol(self) -> None:
        """Stop ODP and initiate the protocol establishment phase."""
        self.detection = 'protocol'
        self._queue(bytes((self.command_address, self.XID))
                    + encode_xid_parameters(self.xid), 'XID command')
        self._queue(bytes((self.command_address, self.SABME_MASKED | 0x10)),
                    'SABME(P)')
        self._awaiting_ua = True
        self.log('[v42] ADP detected twice; starting protocol establishment')

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

    def _enter_raw(self, reason: str) -> None:
        """V.42 7.9 fallback: continue as non-error-corrected octets."""
        self.detection = 'raw'
        self.raw_mode = True
        self.connected = False
        self.tx.clear()
        self._raw_rx_bits.clear()
        self.log(f'[v42] non-error-corrected fallback ({reason})')

    def _feed_raw(self, bits: list[int]) -> None:
        self._raw_rx_bits.extend(bit & 1 for bit in bits)
        while len(self._raw_rx_bits) >= 8:
            value = sum(self._raw_rx_bits[i] << i for i in range(8))
            del self._raw_rx_bits[:8]
            self.rx_data.append(value)

    def feed(self, bits: list[int]) -> None:
        if self.detection == 'raw':
            self._feed_raw(bits)
            # 7.2.1.3 again: receipt of an LAPM frame *is* the start of the
            # protocol phase, so the fallback must not be a one-way door.
            # A peer with detection disabled (Conexant S48=0, and the Courier
            # under the S48=0 the handoff recommends) never sends an ODP at
            # all, so T400 always expires here and its SABME arrives strictly
            # afterwards. Returning early discarded every frame that followed:
            # a live CX call whose captured datagrams contain 45 frames with a
            # valid FCS reported HDLC good/bad/abort=0/0/0, because nothing
            # after the fallback ever reached the decoder.
            frames = self.decoder.feed(bits)
            if not frames:
                return
            # The octets accumulated while this looked like raw data are the
            # same bits, mis-read; keep the frames and drop that reading.
            self.rx_data.clear()
            self.raw_mode = False
            self._raw_rx_bits.clear()
            self._enter_protocol('LAPM frame received after fallback')
            for frame in frames:
                self._handle(frame)
            return
        if self.detection == 'mark':
            self._scan_odp(bits)
        elif self.detection == 'odp':
            self._scan_adp(bits)
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
        self._inactivity = 0
        self._learn_dlci(address)
        kind = 'cmd' if self._is_command(address) else 'rsp'
        if self.trace or not self._sequenced(control):
            # The sequence state the peer is reporting is the whole content of
            # a supervisory frame, and without it a stalled window is
            # undiagnosable from the trace: 4,329 RX lines of a live PPP call
            # said only that RRs kept arriving, not that their N(R) never
            # advanced past the frame we were retransmitting. I and S frames
            # carry N(R) in octet 3.
            sequence = ''
            if len(frame) >= 3 and self._sequenced(control):
                sequence = f' N(R)={(frame[2] >> 1) & 0x7F} PF={frame[2] & 1}'
                if control & 0x01 == 0:
                    sequence = f' N(S)={(control >> 1) & 0x7F}' + sequence
            self.log(f'[v42] RX control=0x{control:02x} '
                     f'address=0x{address:02x} '
                     f'({kind}) length={len(frame)}{sequence}')
        # P/F is bit 4 for U frames; mask it while identifying the function.
        ucontrol = control & 0xEF
        if ucontrol == self.XID:
            self.stats.xid_rx += 1
            # V.42 8.2.4.13 explicitly requires P/F=0 for XID command and
            # response frames. Only the answerer responds to the originator's
            # command; the originator consumes the answerer's response.
            peer = parse_xid_parameters(frame[2:])
            if peer is not None:
                # A responder may select a value between the initiator's
                # proposal and the V.42 default. This endpoint uses one
                # symmetric value for both directions.
                self.n401 = min(self.n401, peer.n401_tx, peer.n401_rx)
                self.window = min(self.window, peer.k_tx, peer.k_rx)
                # No optional procedure is advertised until its complete
                # procedure is implemented (in particular SREJ/32-bit FCS), but
                # the six bits Table 11a requires of every XID transmitter are
                # not optional and must survive this rebuild. The mask is
                # returned in however many octets the initiator used: a peer
                # that encodes it one way very likely parses it the same way,
                # and the Recommendation and ISO/IEC 8885 disagree about which
                # is right.
                negotiated_compression = None
                if self._compression_requested and peer.v42bis is not None:
                    negotiated_compression = V42bisParameters(
                        peer.v42bis.directions,
                        min(self._compression_local.codewords,
                            peer.v42bis.codewords),
                        min(self._compression_local.max_string,
                            peer.v42bis.max_string))
                negotiated_v44 = None
                if self._v44_requested and peer.v44 is not None:
                    local = self._v44_local
                    # Response directions are complementary because V.44 P0
                    # is defined relative to the sender of each XID.
                    response_directions = ((peer.v44.directions & 1) << 1
                                           | (peer.v44.directions & 2) >> 1)
                    negotiated_v44 = V44Parameters(
                        peer.v44.capability, response_directions,
                        min(local.tx_codewords, peer.v44.rx_codewords),
                        min(local.rx_codewords, peer.v44.tx_codewords),
                        min(local.tx_max_string, peer.v44.rx_max_string),
                        min(local.rx_max_string, peer.v44.tx_max_string),
                        min(local.tx_history, peer.v44.rx_history),
                        min(local.rx_history, peer.v44.tx_history))
                self.xid = XidParameters(
                    self.n401, self.n401, self.window, self.window,
                    HDLC_OPTIONAL_FUNCTIONS,
                    peer.optional_functions_octets,
                    negotiated_compression,
                    negotiated_v44)
                self._set_compression(negotiated_compression)
                if self._v44_requested:
                    self._set_v44_compression(peer.v44, negotiated_v44)
            if not self._originator:
                self._queue(bytes((self.response_address, self.XID))
                            + encode_xid_parameters(self.xid),
                            'XID response')
                self.stats.xid_tx += 1
        elif ucontrol == self.UA:
            if len(frame) != 2:
                self._send_frmr(frame)
                return
            self.stats.ua_rx += 1
            # Not gated on the role any more: an answerer that has re-issued
            # SABME after a T401 failure is establishing the link exactly as an
            # originator does, and ignoring the UA left it waiting for one it
            # had already been sent.
            if self._awaiting_ua:
                self._establish('UA(F) received')
        elif ucontrol == self.SABME_MASKED:
            if len(frame) != 2:
                self._send_frmr(frame)
                return
            self.stats.sabme_rx += 1
            # SABME resets both directions: anything already queued belongs to
            # the previous link and its sequence numbers are now invalid. It is
            # also how a link that failed comes back, so it clears `failed` and
            # restores the re-establishment budget.
            self._establish('SABME received')
            self._queue(bytes((self.response_address,
                               self.UA | (control & 0x10))), 'UA')
            self.stats.ua_tx += 1
        elif ucontrol == self.DISC_MASKED:
            if len(frame) != 2:
                self._send_frmr(frame)
                return
            self.stats.disc_rx += 1
            self.connected = False
            self._queue(bytes((self.response_address,
                               self.UA | (control & 0x10))), 'UA(DISC)')
            self.stats.ua_tx += 1
            # The peer has released the link, so the window is as dead as it is
            # after our own failure: leaving it populated left `_service`
            # retransmitting into a link that no longer exists.
            self._disconnect('DISC received')
        elif self._awaiting_ua and self._sequenced(control):
            # Establishment is pending, so the peer's sequence numbers refer to
            # a link this end has already reset and mean nothing here: 8.4.1
            # discards I and S frames in this state. Answering one instead is
            # how a re-establishment turned into a teardown twice in the 17:51
            # run -- the peer, which had not seen the SABME yet, polled with
            # N(R)=59 against our freshly zeroed V(S), `_ack` correctly called
            # that impossible, and FRMR took the call down inside the recovery
            # that was supposed to save it.
            self.stats.discarded_in_establishment += 1
            if self.trace:
                self.log(f'[v42] discarding control=0x{control:02x} while '
                         f'awaiting UA')
        elif control & 0x01 == 0 and len(frame) >= 3:
            # Extended (modulo-128) I frame: N(S) in octet 2, N(R)/P in 3.
            # N401 bounds the information field of an I frame and nothing else.
            # Applying it to every frame meant that once a peer negotiated N401
            # down, its own XID -- which is not bounded by N401, and the CX's is
            # 77 octets -- was answered with FRMR and the link torn down.
            if len(frame) > self.n401 + 3:
                self._send_frmr(frame, too_long=True)
                return
            ns = (control >> 1) & 0x7F
            if not self._ack((frame[2] >> 1) & 0x7F):
                self._send_frmr(frame, invalid_nr=True)
                return
            if ns == self.vr:
                if self.rx_decompressor is None:
                    self.rx_data.extend(frame[3:])
                else:
                    try:
                        self.rx_data.extend(
                            self.rx_decompressor.feed(frame[3:]))
                    except (V42bisError, V44Error) as exc:
                        algorithm = ('V.44' if isinstance(exc, V44Error)
                                     else 'V.42bis')
                        self._disconnect(f'{algorithm} C-ERROR: {exc}')
                        return
                self.vr = (self.vr + 1) & 0x7F
                self.stats.i_rx += 1
            else:
                # V.42 8.5.1 requires REJ for an out-of-sequence I frame
                # unless SREJ was negotiated. Do not acknowledge or advance
                # V(R) for the bad frame.
                self.stats.out_of_seq += 1
                poll = frame[2] & 1
                self._queue(bytes((self.response_address, self.REJ,
                                   (self.vr << 1) | poll)), 'REJ')
                self.stats.rej_tx += 1
                return
            poll = frame[2] & 1
            rr_control_2 = (self.vr << 1) | poll
            self._queue(bytes((self.response_address, self.RR, rr_control_2)),
                        'RR')
            self.stats.rr_tx += 1
        elif control & 0x03 == 0x01 and len(frame) >= 3:
            if len(frame) != 3:
                self._send_frmr(frame)
                return
            supervisory = control & 0x0F
            nr = (frame[2] >> 1) & 0x7F
            if not self._ack(nr):
                self._send_frmr(frame, invalid_nr=True)
                return
            if supervisory == self.RNR:
                # Peer's receiver is busy: stop filling the window, but keep
                # answering polls so the link does not look dead.
                self.peer_busy = True
                self.stats.rnr_rx += 1
                # A busy peer is a peer that is answering, and 8.4.6 makes the
                # busy condition a state to wait out, not an error to count.
                # Without this the enquiry cycle spent N400 on it: an AMR
                # softmodem sent nine RNRs into a link that then died of "T401
                # retry limit" with an empty window and three retransmissions
                # into a receiver that had said it could not take them.
                self._retries = 0
                self._since_ack = 0
            else:
                self.peer_busy = False
            if supervisory in (self.REJ, self.SREJ):
                self.stats.rej_rx += 1
                self._retransmit_from(nr)
                # An explicit REJ is the peer telling us exactly what it wants
                # and proves it is still listening, which a timeout does not.
                self._retries = 0
            if frame[2] & 1 and self._is_command(address):
                # A polled supervisory command requires a final response; the
                # F bit of a response is not a poll and must not be answered.
                self._queue(bytes((self.response_address, self.RR,
                                   (self.vr << 1) | 1)), 'RR(F)')
                self.stats.rr_tx += 1
        else:
            # V.42 8.5.5: an undefined control field is a frame-rejection
            # condition, not silently ignored traffic.
            self._send_frmr(frame)

    def _service(self) -> None:
        """Per-service-call transmit work: fill the window, then recover.

        Recovery is deliberately conservative. An unacknowledged window that
        stops moving is first probed with RR(P), because the likeliest cause is
        a lost acknowledgement rather than a lost I frame, and the peer's
        response carries the N(R) that resolves it without resending anything.
        Only if that does not move the window does it go back N.
        """
        if self.failed is not None:
            # Down and staying down until the peer establishes again. Idle
            # flags still go out (take() does that below), so a SABME from the
            # peer is still answered; nothing else here has any work to do.
            return
        if self._suspended_for:
            self._suspended_for -= 1
            if not self._suspended_for:
                self._resume()
            return
        self._fill_window()
        if self.inactivity_after is not None:
            self._inactivity += 1
            if self._inactivity >= self.inactivity_after:
                self._disconnect('T403 inactivity')
                return
        if self._awaiting_ua:
            self._establish_ticks += 1
            if self._establish_ticks >= self.retransmit_after:
                if self._retries >= self.n400:
                    self._disconnect('T401 SABME retry limit')
                    return
                self._queue(bytes((self.command_address,
                                   self.SABME_MASKED | 0x10)), 'SABME retry')
                self.stats.sabme_tx += 1
                self._retries += 1
                self._establish_ticks = 0
        if self.peer_busy:
            # 8.4.6: the busy condition is waited out with an enquiry, not
            # recovered from. Going back N into a receiver that has just said
            # it cannot take anything wastes the frames *and* the budget --
            # three retransmissions and a dead link, on a call whose window was
            # empty by then. What N400 counts here is enquiries the peer did
            # not answer, so a peer that stays busy but alive keeps the link
            # and one that goes silent still loses it.
            self._since_ack += 1
            if self._since_ack >= self.retransmit_after:
                if self._retries >= self.n400:
                    self._link_failure('peer busy and not answering enquiries')
                    return
                self._queue(bytes((self.command_address, self.RR,
                                   (self.vr << 1) | 1)), 'RR(P) busy enquiry')
                self.stats.poll_tx += 1
                self._retries += 1
                self._since_ack = 0
            return
        if not self.outstanding:
            self._since_ack = 0
            return
        self._since_ack += 1
        if self._since_ack == self.poll_after:
            self._queue(bytes((self.command_address, self.RR,
                               (self.vr << 1) | 1)), 'RR(P) window probe')
            self.stats.poll_tx += 1
        elif self._since_ack >= self.retransmit_after:
            if self._retries >= self.n400:
                self._link_failure('T401 retry limit')
                return
            self._retransmit_from(self.va)
            self._retries += 1
            self._since_ack = 0

    def take(self, count: int, *, service: bool = True,
             idle: bool = True) -> list[int]:
        """Hand `count` bits to the data pump, idling on HDLC flags.

        This is also the endpoint's clock: `_service()` runs once per call, so
        T401, T403 and the poll counter advance per datagram taken. Anything
        bridging this stream elsewhere -- the NL N_DATA path in
        eicon_mips_shim -- must still call it at the line's datagram rate and
        buffer the result, not pull large blocks on its own schedule, or every
        LAPM timer runs on the wrong clock.

        Idle fill is queued a whole flag at a time rather than synthesised per
        bit. Generating it per bit meant that when a frame was queued partway
        through a flag, the next take switched to frame bits and abandoned the
        flag half-emitted; the receiver then had to resync and consumed the
        frame doing it, so the first frame after any idle gap was lost. Keeping
        the flag in the same queue as the frames makes a partial flag finish
        before frame bits follow it, even across calls.
        """
        if service and self.detection == 'protocol':
            self._service()
        elif service and self.detection in ('mark', 'odp'):
            self._detect_ticks += 1
            if (self._detect_ticks > self.detect_timeout
                    and not self._detect_reported):
                # T400 (9.1.1). 7.2.1.3 has the answerer fall back to
                # non-error-correcting operation here; there is no asynchronous
                # mode on this side to fall back to, so stay on mark and say so
                # rather than starting flags the originator will not expect.
                self._detect_reported = True
                self._enter_raw(f'T400 expired; {self.detection_summary()}')
        bits: list[int] = []
        while len(bits) < count:
            if self.detection == 'raw':
                if self.tx_stream:
                    value = self.tx_stream.pop(0)
                    bits.extend((value >> bit) & 1 for bit in range(8))
                else:
                    bits.append(1)
                continue
            if self.tx:
                bits.append(self.tx.popleft())
                continue
            if self.detection == 'mark':
                bits.append(1)              # 7.2.1.3: mark until the ODP
                continue
            if self.detection == 'odp':
                # Repeat the ODP continuously until two adjacent ADPs arrive.
                if not self.tx:
                    self.tx.extend(ODP_EVEN + ADP_SEPARATOR + ODP_ODD
                                   + ADP_SEPARATOR)
                continue
            if self.detection == 'adp':
                # Every repetition has been handed over; the originator stops
                # its ODP on seeing two adjacent ADPs (7.2.1.2).
                self._enter_protocol('ADP sent')
                self._service()
            if not self.tx:
                if not idle:
                    break
                self.tx.extend(FLAG_BITS)
        return bits
