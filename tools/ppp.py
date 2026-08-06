#!/usr/bin/env python3
"""PPP over the emulated modem's V.42 data link: framing, LCP, auth, IPCP.

The V.42 link delivers an ordered, error-free byte stream in both directions,
which is exactly what RFC 1662 asynchronous framing expects underneath it.  So
this module starts at the flag byte and implements the dial-in server a client
would meet on the far end of a real modem: HDLC-like framing with a 16-bit FCS,
LCP option negotiation, PAP or CHAP authentication, and IPCP address
assignment.

It is deliberately a peer rather than a server only.  RFC 1661 negotiation is
symmetric -- both ends send Configure-Requests and both must Ack the other's --
so a client costs almost nothing on top of a server and makes the whole thing
testable end to end without hardware or root.  The roles differ in only two
places: who demands authentication, and who assigns addresses.

What this does *not* do is put a route on the host.  IP terminates in
userspace: received datagrams land in ``rx_ip`` and ``send_ip()`` puts them
back on the wire.  ``IcmpEchoResponder`` is attached by default so that a
dialled-in client can ping the server address and get a reply, which is the
cheapest end-to-end proof that framing, negotiation and the data path all work.
Bridging that to a real network is a tun device's job and is not done here.

No dependencies, no emulator, no I/O: ``feed()`` takes bytes off the link,
``take()`` returns bytes to put on it, ``tick()`` runs the restart timers.
"""
from __future__ import annotations

import hashlib
import ipaddress
import os
import struct
from dataclasses import dataclass, field

# RFC 6598 shared address space. This is what a carrier hands to subscribers
# behind a NAT it controls, which is exactly the relationship here: callers are
# not on the host's network and never route off this process. It beats RFC 1918
# for the purpose because 10/8 and 192.168/16 are what the *client* is most
# likely already using -- a dialled-in host that gets an address colliding with
# its own LAN loses its LAN, and the failure looks like a modem fault.
CGNAT_PREFIX = '100.64.0.0/10'

# Protocol numbers (RFC 1700 / the PPP DLL registry).
PROTO_IP = 0x0021
PROTO_IPCP = 0x8021
PROTO_LCP = 0xC021
PROTO_PAP = 0xC023
PROTO_CHAP = 0xC223

# RFC 1661 section 5, packet codes.
CONF_REQ, CONF_ACK, CONF_NAK, CONF_REJ = 1, 2, 3, 4
TERM_REQ, TERM_ACK, CODE_REJ, PROTO_REJ = 5, 6, 7, 8
ECHO_REQ, ECHO_REP, DISCARD_REQ = 9, 10, 11

# LCP configuration option types (RFC 1661 section 6).
OPT_MRU = 1
OPT_ACCM = 2
OPT_AUTH = 3
OPT_QUALITY = 4
OPT_MAGIC = 5
OPT_PFC = 7
OPT_ACFC = 8

# IPCP configuration option types (RFC 1332, RFC 1877).
OPT_IP_COMPRESSION = 2
OPT_IP_ADDRESS = 3
OPT_DNS1 = 129
OPT_NBNS1 = 130
OPT_DNS2 = 131
OPT_NBNS2 = 132

FLAG = 0x7E
ESCAPE = 0x7D
XOR = 0x20

DEFAULT_MRU = 1500
# RFC 1662 section 7.2: a good frame leaves this residue in the FCS register.
GOOD_FCS = 0xF0B8


def _fcs16_table() -> list[int]:
    table = []
    for byte in range(256):
        value = byte
        for _ in range(8):
            value = (value >> 1) ^ 0x8408 if value & 1 else value >> 1
        table.append(value)
    return table


_FCS_TABLE = _fcs16_table()


def fcs16(data: bytes, fcs: int = 0xFFFF) -> int:
    """The CRC-16 of RFC 1662 appendix C, without the final complement."""
    for byte in data:
        fcs = (fcs >> 8) ^ _FCS_TABLE[(fcs ^ byte) & 0xFF]
    return fcs


PROTOCOL_NAMES = {PROTO_IP: 'IP', PROTO_IPCP: 'IPCP', PROTO_LCP: 'LCP',
                  PROTO_PAP: 'PAP', PROTO_CHAP: 'CHAP'}
CODE_NAMES = {CONF_REQ: 'Configure-Request', CONF_ACK: 'Configure-Ack',
              CONF_NAK: 'Configure-Nak', CONF_REJ: 'Configure-Reject',
              TERM_REQ: 'Terminate-Request', TERM_ACK: 'Terminate-Ack',
              CODE_REJ: 'Code-Reject', PROTO_REJ: 'Protocol-Reject',
              ECHO_REQ: 'Echo-Request', ECHO_REP: 'Echo-Reply',
              DISCARD_REQ: 'Discard-Request'}
OPTION_NAMES = {OPT_MRU: 'MRU', OPT_ACCM: 'ACCM', OPT_AUTH: 'Auth',
                OPT_QUALITY: 'Quality', OPT_MAGIC: 'Magic', OPT_PFC: 'PFC',
                OPT_ACFC: 'ACFC', 13: 'Callback', 17: 'MRRU',
                18: 'ShortSeq', 19: 'EndpointDiscriminator'}
IPCP_OPTION_NAMES = {OPT_IP_COMPRESSION: 'VJ', OPT_IP_ADDRESS: 'IP',
                     OPT_DNS1: 'DNS1', OPT_NBNS1: 'NBNS1', OPT_DNS2: 'DNS2',
                     OPT_NBNS2: 'NBNS2'}


def describe(protocol: int, payload: bytes) -> str:
    """One line naming a packet and its options, for --ppp-trace.

    Worth the code: a dial-in failure against a client that cannot be
    instrumented is diagnosed from this line or from nothing, and every
    round trip to find out costs a real phone call.
    """
    name = PROTOCOL_NAMES.get(protocol, f'0x{protocol:04x}')
    if protocol not in (PROTO_LCP, PROTO_IPCP) or len(payload) < 4:
        return f'{name} {len(payload)} bytes'
    code, identifier, length = struct.unpack('>BBH', payload[:4])
    text = f'{name} {CODE_NAMES.get(code, f"code {code}")} id={identifier}'
    if code not in (CONF_REQ, CONF_ACK, CONF_NAK, CONF_REJ):
        return text
    names = OPTION_NAMES if protocol == PROTO_LCP else IPCP_OPTION_NAMES
    try:
        options = parse_options(payload[4:length])
    except PppError:
        return f'{text} (unparseable options)'
    described = []
    for otype, value in options:
        label = names.get(otype, str(otype))
        if otype == OPT_AUTH and len(value) >= 2:
            auth = struct.unpack('>H', value[:2])[0]
            label += f'={PROTOCOL_NAMES.get(auth, hex(auth))}'
            if auth == PROTO_CHAP and len(value) > 2:
                label += f'/alg{value[2]}'
        elif protocol == PROTO_IPCP and len(value) == 4:
            label += '=' + bytes_to_ip(value)
        elif value:
            label += '=' + value.hex()
        described.append(label)
    return f'{text} [{", ".join(described) or "no options"}]'


class PppError(ValueError):
    """A frame the peer sent cannot be parsed as PPP."""


class AddressPool:
    """Hand each caller its own address out of a prefix, and take it back.

    The pool belongs to whatever outlives a single call -- the SIP endpoint,
    the listening socket -- and not to a peer: a per-peer pool would issue the
    same first address to every caller and could never detect a collision.

    A /10 holds four million addresses, so nothing is materialised.  The cursor
    walks forward and wraps, which means a released address is not reissued
    immediately; a client that reconnects during the same run usually gets a
    fresh address rather than one still lingering in some ARP or route cache at
    its end.
    """

    def __init__(self, prefix: str = CGNAT_PREFIX, *, reserve=()) -> None:
        self.network = ipaddress.IPv4Network(prefix, strict=False)
        # A /31 or /32 has no usable host range to hand out at all, and the
        # host-address exclusions below would empty it.
        if self.network.prefixlen > 30:
            raise ValueError(f'{prefix} is too small to allocate from')
        self.reserved = {ipaddress.IPv4Address(address) for address in reserve}
        self.allocated: dict[str, ipaddress.IPv4Address] = {}
        self._first = int(self.network.network_address) + 1
        self._last = int(self.network.broadcast_address) - 1
        self._cursor = self._first
        self.issued = 0
        self.collisions = 0

    def __contains__(self, address: str) -> bool:
        try:
            return ipaddress.IPv4Address(address) in self.network
        except ValueError:
            return False

    def allocate(self) -> str:
        """The next free address, as a dotted quad. Raises when exhausted."""
        taken = set(self.allocated.values()) | self.reserved
        for _ in range(self._last - self._first + 1):
            candidate = ipaddress.IPv4Address(self._cursor)
            self._cursor += 1
            if self._cursor > self._last:
                self._cursor = self._first
            if candidate not in taken:
                address = str(candidate)
                self.allocated[address] = candidate
                self.issued += 1
                return address
        raise PppError(f'no free address left in {self.network}')

    def preview(self) -> str:
        """The first address the pool could issue, without consuming it.

        A tun interface needs a point-to-point peer address before any client
        exists, and that address is cosmetic -- the route covering the pool is
        what actually carries traffic -- so it must not eat a pool entry.
        """
        taken = set(self.allocated.values()) | self.reserved
        for value in range(self._first, self._last + 1):
            candidate = ipaddress.IPv4Address(value)
            if candidate not in taken:
                return str(candidate)
        raise PppError(f'no free address left in {self.network}')

    def release(self, address: str) -> None:
        """Give an address back. Releasing an unknown one is not an error:
        teardown paths run more than once and must stay idempotent."""
        self.allocated.pop(address, None)

    def reserve(self, address: str) -> None:
        self.reserved.add(ipaddress.IPv4Address(address))

    def __len__(self) -> int:
        return len(self.allocated)


# ---------------------------------------------------------------------------
# Framing


class HdlcFramer:
    """RFC 1662 asynchronous framing: byte stuffing, FCS, flag delimiting.

    The receiver is deliberately permissive about the things a real peer gets
    away with -- shared flags between frames, leading garbage, and either
    address/control compression -- because the point is to accept what clients
    actually send, not to police them.  It is strict about the FCS: a frame
    that fails it is dropped silently, which is what RFC 1662 requires and what
    keeps a corrupt frame from being mistaken for a negotiation failure.
    """

    def __init__(self) -> None:
        self.rx_accm = 0xFFFFFFFF          # what *we* need escaped inbound
        self.tx_accm = 0xFFFFFFFF          # what the peer asked us to escape
        self.tx_acfc = False
        self.tx_pfc = False
        self.mru = DEFAULT_MRU
        self._buffer = bytearray()
        self._escaped = False
        self._in_frame = False
        # Undecoded bytes. They are held rather than decoded eagerly because
        # the ACCM can change *between* two frames in one read -- the peer's
        # Configure-Ack is routinely followed immediately by a packet sent
        # under the terms it just agreed. Decoding the whole buffer up front
        # applies the old ACCM to that second frame and discards it.
        self._input = bytearray()
        self._cursor = 0
        self.fcs_errors = 0
        self.overruns = 0

    def reset(self) -> None:
        """Forget a partial frame. Used when the link restarts."""
        self._buffer.clear()
        self._escaped = False
        self._in_frame = False
        self._input.clear()
        self._cursor = 0

    def restore_defaults(self) -> None:
        """Un-negotiate: escape everything, compress nothing."""
        self.reset()
        self.rx_accm = 0xFFFFFFFF
        self.tx_accm = 0xFFFFFFFF
        self.tx_acfc = False
        self.tx_pfc = False
        self.mru = DEFAULT_MRU

    def push(self, data: bytes) -> None:
        """Accept bytes off the link without decoding them yet."""
        self._input += data

    def next_frame(self) -> bytes | None:
        """Decode up to the next complete frame, or None if there is not one.

        Decoding one frame at a time is what lets the caller act on a frame --
        including changing the ACCM -- before the next one is interpreted.
        """
        while self._cursor < len(self._input):
            byte = self._input[self._cursor]
            self._cursor += 1
            if byte == FLAG:
                frame = None
                if self._in_frame and self._buffer:
                    frame = self._close_frame()
                self._buffer.clear()
                self._escaped = False
                self._in_frame = True
                self._compact()
                if frame is not None:
                    return frame
                continue
            if not self._in_frame:
                # Bytes before the first flag are not part of any frame. A
                # client that sends a banner or stray AT chatter before PPP
                # starts is normal, so drop them rather than erroring.
                continue
            if byte == ESCAPE:
                self._escaped = True
                continue
            if self._escaped:
                byte ^= XOR
                self._escaped = False
            elif byte < 0x20 and (self.rx_accm >> byte) & 1:
                # An unescaped control character the peer agreed to escape.
                # RFC 1662 section 4.2: discard it, do not treat it as data.
                continue
            if len(self._buffer) > self.mru + 8:
                self.overruns += 1
                self._in_frame = False
                self._buffer.clear()
                continue
            self._buffer.append(byte)
        self._compact()
        return None

    def _compact(self) -> None:
        """Drop consumed input, so a long session does not grow a buffer."""
        if self._cursor:
            del self._input[:self._cursor]
            self._cursor = 0

    def feed(self, data: bytes) -> list[bytes]:
        """Every complete, FCS-checked frame available, headers stripped.

        Convenience for callers with nothing to change mid-buffer; the peer
        itself uses push()/next_frame() so that it can.
        """
        self.push(data)
        frames = []
        while True:
            frame = self.next_frame()
            if frame is None:
                return frames
            frames.append(frame)

    def _close_frame(self) -> bytes | None:
        raw = bytes(self._buffer)
        if len(raw) < 4 or fcs16(raw) != GOOD_FCS:
            self.fcs_errors += 1
            return None
        return self._strip_header(raw[:-2])

    @staticmethod
    def _strip_header(body: bytes) -> bytes | None:
        """Drop an uncompressed address/control pair, leaving protocol+data."""
        if body[:2] == b'\xff\x03':
            body = body[2:]
        return body or None

    def encode(self, protocol: int, payload: bytes) -> bytes:
        """Frame one PPP packet, applying whatever the peer asked us to compress.

        LCP is never sent with a compressed protocol field: RFC 1661 section
        6.5 forbids it, and a peer that has not finished negotiating PFC would
        be entitled to discard it.
        """
        header = bytearray()
        if not (self.tx_acfc and protocol != PROTO_LCP):
            header += b'\xff\x03'
        if self.tx_pfc and protocol < 0x100 and protocol != PROTO_LCP:
            header.append(protocol)
        else:
            header += struct.pack('>H', protocol)
        body = bytes(header) + payload
        body += struct.pack('<H', fcs16(body) ^ 0xFFFF)
        out = bytearray([FLAG])
        for byte in body:
            if (byte in (FLAG, ESCAPE)
                    or (byte < 0x20 and (self.tx_accm >> byte) & 1)):
                out.append(ESCAPE)
                out.append(byte ^ XOR)
            else:
                out.append(byte)
        out.append(FLAG)
        return bytes(out)


def parse_packet(frame: bytes) -> tuple[int, bytes]:
    """Split a de-framed body into (protocol, payload).

    An odd low bit in the first byte means the protocol field was compressed to
    one octet -- PPP protocol numbers are always odd in their least significant
    byte, which is what makes single-byte protocols unambiguous.
    """
    if not frame:
        raise PppError('empty PPP frame')
    if frame[0] & 1:
        return frame[0], frame[1:]
    if len(frame) < 2:
        raise PppError('truncated PPP protocol field')
    return struct.unpack('>H', frame[:2])[0], frame[2:]


def parse_options(data: bytes) -> list[tuple[int, bytes]]:
    """Parse a type/length/value option list, rejecting malformed lengths."""
    options = []
    index = 0
    while index < len(data):
        if index + 2 > len(data):
            raise PppError('truncated option header')
        otype, length = data[index], data[index + 1]
        if length < 2 or index + length > len(data):
            raise PppError(f'bad length {length} for option {otype}')
        options.append((otype, data[index + 2:index + length]))
        index += length
    return options


def encode_options(options) -> bytes:
    return b''.join(bytes((otype, len(value) + 2)) + value
                    for otype, value in options)


def ip_to_bytes(address: str) -> bytes:
    parts = address.split('.')
    if len(parts) != 4:
        raise ValueError(f'not a dotted-quad IPv4 address: {address!r}')
    return bytes(int(part) for part in parts)


def bytes_to_ip(raw: bytes) -> str:
    return '.'.join(str(byte) for byte in raw)


# ---------------------------------------------------------------------------
# The RFC 1661 option negotiation automaton


class ControlProtocol:
    """The shared Configure-Request/Ack/Nak/Reject machinery of LCP and IPCP.

    This is the practical subset of the RFC 1661 automaton, not all ten states:
    the two ends of a modem link either converge in a few round trips or the
    call is not worth keeping.  Ack-Rcvd and Ack-Sent are tracked separately
    because "opened" means both directions agreed, and collapsing them is the
    classic way to declare a link up that the peer is still negotiating.
    """

    protocol = 0
    name = 'ctrl'

    def __init__(self, peer, *, max_restarts: int = 10,
                 restart_timeout: float = 3.0) -> None:
        self.peer = peer
        self.state = 'closed'
        self.identifier = 0
        self.last_request_id = None
        self.last_request = b''
        self.restarts = max_restarts
        self.max_restarts = max_restarts
        self.restart_timeout = restart_timeout
        self.deadline = None
        self.naks_sent = 0
        self.failed = None

    # -- transmission helpers ------------------------------------------------

    def _next_id(self) -> int:
        self.identifier = (self.identifier + 1) & 0xFF
        return self.identifier

    def send(self, code: int, identifier: int, payload: bytes = b'') -> None:
        packet = struct.pack('>BBH', code, identifier, len(payload) + 4) + payload
        self.peer.transmit(self.protocol, packet)

    def send_configure_request(self, now: float) -> None:
        self.last_request = encode_options(self.local_options())
        self.last_request_id = self._next_id()
        self.send(CONF_REQ, self.last_request_id, self.last_request)
        self.deadline = now + self.restart_timeout

    def open(self, now: float) -> None:
        if self.state != 'closed':
            return
        self.restarts = self.max_restarts
        self.naks_sent = 0
        self.failed = None
        self.state = 'req-sent'
        self.send_configure_request(now)

    def close(self, now: float) -> None:
        if self.state in ('closed', 'closing'):
            return
        was_open = self.state == 'opened'
        self.state = 'closing'
        self.restarts = self.max_restarts
        self.send(TERM_REQ, self._next_id(), b'goodbye')
        self.deadline = now + self.restart_timeout
        if was_open:
            self.this_layer_down()

    def tick(self, now: float) -> None:
        if self.deadline is None or now < self.deadline:
            return
        self.restarts -= 1
        if self.restarts <= 0:
            self.deadline = None
            if self.state == 'closing':
                self.state = 'closed'
            else:
                self.fail('no response to Configure-Request')
            return
        if self.state == 'closing':
            self.send(TERM_REQ, self._next_id(), b'goodbye')
            self.deadline = now + self.restart_timeout
        elif self.state in ('req-sent', 'ack-rcvd', 'ack-sent'):
            self.send_configure_request(now)

    def fail(self, reason: str) -> None:
        was_open = self.state == 'opened'
        self.state = 'closed'
        self.deadline = None
        self.failed = reason
        self.peer.log(f'[ppp] {self.name} failed: {reason}')
        if was_open:
            self.this_layer_down()

    # -- reception -----------------------------------------------------------

    def feed(self, payload: bytes, now: float) -> None:
        if len(payload) < 4:
            raise PppError(f'{self.name} packet shorter than a header')
        code, identifier, length = struct.unpack('>BBH', payload[:4])
        if length < 4 or length > len(payload):
            raise PppError(f'{self.name} length {length} does not fit the frame')
        data = payload[4:length]
        handler = getattr(self, f'_recv_{code}', None)
        if handler is None:
            # RFC 1661 section 5.7: an unknown code is Code-Reject, not a
            # reason to drop the link.
            self.send(CODE_REJ, self._next_id(), payload[:length])
            return
        handler(identifier, data, now)

    def _recv_1(self, identifier: int, data: bytes, now: float) -> None:
        """Configure-Request."""
        if self.state == 'opened':
            # The peer restarted negotiation under us. Tear the layer down
            # before looking at the options, so the down transition cannot
            # discard the very values this request is establishing.
            self.this_layer_down()
            self.state = 'req-sent'
            self.send_configure_request(now)
        acked, nakked, rejected = [], [], []
        for otype, value in parse_options(data):
            verdict, replacement = self.review_peer_option(otype, value)
            if verdict == 'ack':
                acked.append((otype, value))
            elif verdict == 'nak':
                nakked.append((otype, replacement))
            else:
                rejected.append((otype, value))
        if rejected:
            self.send(CONF_REJ, identifier, encode_options(rejected))
            self._not_acknowledged()
            return
        if nakked:
            self.naks_sent += 1
            if self.naks_sent > self.max_restarts:
                # A peer that will not take our Naks is not going to converge.
                self.fail(f'{self.name} peer rejected {self.naks_sent} Naks')
                return
            self.send(CONF_NAK, identifier, encode_options(nakked))
            self._not_acknowledged()
            return
        for otype, value in acked:
            self.apply_peer_option(otype, value)
        self.send(CONF_ACK, identifier, data)
        if self.state == 'ack-rcvd':
            self._enter_opened()
        else:
            self.state = 'ack-sent'

    def _not_acknowledged(self) -> None:
        if self.state == 'ack-sent':
            self.state = 'req-sent'
        elif self.state == 'opened':
            self.this_layer_down()
            self.state = 'req-sent'

    def _recv_2(self, identifier: int, data: bytes, now: float) -> None:
        """Configure-Ack."""
        if identifier != self.last_request_id or data != self.last_request:
            # Stale or garbled: RFC 1661 says silently discard. Acting on it
            # would open the layer on terms we never proposed.
            return
        for otype, value in parse_options(data):
            self.apply_local_option(otype, value)
        self.restarts = self.max_restarts
        if self.state == 'ack-sent':
            self._enter_opened()
        elif self.state == 'req-sent':
            self.state = 'ack-rcvd'
            self.deadline = None

    def _recv_3(self, identifier: int, data: bytes, now: float) -> None:
        """Configure-Nak: the peer wants different values, so take them."""
        if identifier != self.last_request_id:
            return
        for otype, value in parse_options(data):
            self.accept_nak(otype, value)
        self._resend_after_negotiation(now)

    def _recv_4(self, identifier: int, data: bytes, now: float) -> None:
        """Configure-Reject: the peer will not discuss these options."""
        if identifier != self.last_request_id:
            return
        for otype, _ in parse_options(data):
            self.reject_local_option(otype)
        self._resend_after_negotiation(now)

    def _resend_after_negotiation(self, now: float) -> None:
        self.restarts = self.max_restarts
        if self.state == 'ack-rcvd':
            self.state = 'req-sent'
        self.send_configure_request(now)

    def _recv_5(self, identifier: int, data: bytes, now: float) -> None:
        """Terminate-Request."""
        self.send(TERM_ACK, identifier)
        if self.state == 'opened':
            self.this_layer_down()
        self.state = 'closed'
        self.deadline = None

    def _recv_6(self, identifier: int, data: bytes, now: float) -> None:
        """Terminate-Ack."""
        if self.state == 'closing':
            self.state = 'closed'
            self.deadline = None

    def _recv_7(self, identifier: int, data: bytes, now: float) -> None:
        """Code-Reject. Rejecting a code we consider basic is fatal."""
        self.fail(f'{self.name} peer rejected code {data[0] if data else "?"}')

    def _enter_opened(self) -> None:
        self.state = 'opened'
        self.deadline = None
        self.this_layer_up()

    # -- hooks subclasses fill in -------------------------------------------

    def local_options(self):
        return []

    def review_peer_option(self, otype: int, value: bytes):
        return 'rej', value

    def apply_peer_option(self, otype: int, value: bytes) -> None:
        pass

    def apply_local_option(self, otype: int, value: bytes) -> None:
        pass

    def accept_nak(self, otype: int, value: bytes) -> None:
        pass

    def reject_local_option(self, otype: int) -> None:
        pass

    def this_layer_up(self) -> None:
        pass

    def this_layer_down(self) -> None:
        pass


# ---------------------------------------------------------------------------
# LCP


class Lcp(ControlProtocol):
    protocol = PROTO_LCP
    name = 'lcp'

    def __init__(self, peer, **kwargs) -> None:
        super().__init__(peer, **kwargs)
        self.want_mru = DEFAULT_MRU
        self.peer_mru = DEFAULT_MRU
        self.want_accm = 0x00000000
        self.magic = struct.unpack('>I', os.urandom(4))[0]
        self.peer_magic = None
        self.want_pfc = True
        self.want_acfc = True
        self.want_magic = True
        # Auth is only ever demanded of the peer, never offered: this end is
        # the one that owns the secrets database, and a dial-in server that
        # authenticated *to* its callers would have them backwards.
        self.require_auth = None           # None, 'pap' or 'chap'
        self.peer_auth = None              # what the peer demands of us
        # Negotiated framing is staged here and only pushed into the framer
        # when LCP opens.  Applying it the moment an option is Acked looks
        # right and is not: the Configure-Ack itself would then go out under
        # terms the peer does not know about until it reads that very frame,
        # and it discards the frame carrying the news.
        self._pending_tx = {}
        self._pending_rx = {}

    def local_options(self):
        options = [(OPT_MRU, struct.pack('>H', self.want_mru)),
                   (OPT_ACCM, struct.pack('>I', self.want_accm))]
        if self.require_auth == 'pap':
            options.append((OPT_AUTH, struct.pack('>H', PROTO_PAP)))
        elif self.require_auth == 'chap':
            # The trailing 0x05 is the CHAP algorithm: MD5.
            options.append((OPT_AUTH, struct.pack('>HB', PROTO_CHAP, 5)))
        if self.want_magic:
            options.append((OPT_MAGIC, struct.pack('>I', self.magic)))
        if self.want_pfc:
            options.append((OPT_PFC, b''))
        if self.want_acfc:
            options.append((OPT_ACFC, b''))
        return options

    def review_peer_option(self, otype: int, value: bytes):
        if otype == OPT_MRU:
            if len(value) != 2:
                return 'nak', struct.pack('>H', DEFAULT_MRU)
            mru = struct.unpack('>H', value)[0]
            # Below 128 the per-frame overhead dominates and IP needs
            # fragmenting we cannot do; propose the default instead.
            if mru < 128:
                return 'nak', struct.pack('>H', DEFAULT_MRU)
            return 'ack', value
        if otype == OPT_ACCM:
            return ('ack', value) if len(value) == 4 else (
                'nak', struct.pack('>I', 0xFFFFFFFF))
        if otype == OPT_MAGIC:
            if len(value) != 4:
                return 'nak', struct.pack('>I', self.magic)
            if struct.unpack('>I', value)[0] == self.magic:
                # Identical magic numbers mean the link is looped back to us.
                # Naking with a fresh one is how RFC 1661 tells the two apart.
                self.magic = struct.unpack('>I', os.urandom(4))[0]
                return 'nak', struct.pack('>I', self.magic)
            return 'ack', value
        if otype in (OPT_PFC, OPT_ACFC):
            return 'ack', value
        if otype == OPT_AUTH:
            if self.peer.role != 'client':
                # A caller that wants to authenticate *us* gets refused rather
                # than Nakked: there is nothing to negotiate, the server has no
                # identity to present to its callers.
                return 'rej', value
            if len(value) < 2:
                return 'rej', value
            protocol = struct.unpack('>H', value[:2])[0]
            if protocol == PROTO_PAP:
                return 'ack', value
            if protocol == PROTO_CHAP and value[2:3] == b'\x05':
                return 'ack', value         # MD5 is the only algorithm here
            if protocol == PROTO_CHAP:
                # Some other CHAP algorithm (MS-CHAP, say). Nak with the one
                # we can actually compute rather than refusing auth outright.
                return 'nak', struct.pack('>HB', PROTO_CHAP, 5)
            return 'nak', struct.pack('>HB', PROTO_CHAP, 5)
        return 'rej', value

    def apply_peer_option(self, otype: int, value: bytes) -> None:
        # These describe what the peer will receive, so they configure our
        # transmitter -- once the layer is up.
        if otype == OPT_ACCM:
            self._pending_tx['accm'] = struct.unpack('>I', value)[0]
        elif otype == OPT_PFC:
            self._pending_tx['pfc'] = True
        elif otype == OPT_ACFC:
            self._pending_tx['acfc'] = True
        elif otype == OPT_MAGIC:
            self.peer_magic = struct.unpack('>I', value)[0]
        elif otype == OPT_MRU:
            # What the *peer* can receive, so it bounds what we may send. The
            # tun's MTU is set from this, which is what makes the kernel
            # fragment or signal PMTU instead of handing down packets the
            # client would have to drop.
            self.peer_mru = struct.unpack('>H', value)[0]
        elif otype == OPT_AUTH:
            # We Acked the peer's demand, so we are the one who authenticates.
            self.peer_auth = ('pap' if struct.unpack('>H', value[:2])[0]
                              == PROTO_PAP else 'chap')

    def apply_local_option(self, otype: int, value: bytes) -> None:
        # Acked by the peer, so these will govern what we receive.
        if otype == OPT_ACCM:
            self._pending_rx['accm'] = struct.unpack('>I', value)[0]
        elif otype == OPT_MRU:
            self._pending_rx['mru'] = struct.unpack('>H', value)[0]

    def accept_nak(self, otype: int, value: bytes) -> None:
        if otype == OPT_MRU and len(value) == 2:
            self.want_mru = struct.unpack('>H', value)[0]
        elif otype == OPT_ACCM and len(value) == 4:
            self.want_accm = struct.unpack('>I', value)[0]
        elif otype == OPT_MAGIC and len(value) == 4:
            # The peer saw our magic as its own. Pick a different one rather
            # than the one it suggested, so a genuine loop keeps colliding
            # instead of quietly resolving.
            self.magic = struct.unpack('>I', os.urandom(4))[0]

    def reject_local_option(self, otype: int) -> None:
        if otype == OPT_PFC:
            self.want_pfc = False
        elif otype == OPT_ACFC:
            self.want_acfc = False
        elif otype == OPT_MAGIC:
            self.want_magic = False
        elif otype == OPT_AUTH:
            # A client that will not do CHAP-MD5 is common rather than
            # exceptional -- modern Windows RAS offers MS-CHAPv2 and has
            # CHAP switched off by default -- so try PAP before giving up.
            # The Configure-Request is resent by the caller of this hook, so
            # changing what we ask for is all that is needed here.
            if self.require_auth == 'chap':
                self.require_auth = 'pap'
                self.peer.log('[ppp] peer rejected CHAP; offering PAP')
                return
            # Refusing every password is refusing the service. Say so rather
            # than letting IPCP come up for an unidentified caller.
            self.fail('peer rejected every authentication protocol offered; '
                      'run with --ppp-auth none to allow unauthenticated '
                      'callers')

    def _recv_9(self, identifier: int, data: bytes, now: float) -> None:
        """Echo-Request: reply with our own magic number, per RFC 1661."""
        if self.state != 'opened':
            return
        self.send(ECHO_REP, identifier, struct.pack('>I', self.magic) + data[4:])

    def _recv_10(self, identifier: int, data: bytes, now: float) -> None:
        """Echo-Reply: proof of life, used by the keepalive."""
        self.peer.echo_replies += 1
        self.peer.echo_outstanding = 0

    def _recv_11(self, identifier: int, data: bytes, now: float) -> None:
        """Discard-Request. Defined to be thrown away."""

    def _recv_8(self, identifier: int, data: bytes, now: float) -> None:
        """Protocol-Reject: the peer does not implement something we sent."""
        if len(data) >= 2:
            rejected = struct.unpack('>H', data[:2])[0]
            self.peer.log(f'[ppp] peer rejected protocol 0x{rejected:04x}')
            self.peer.rejected_protocols.add(rejected)

    def this_layer_up(self) -> None:
        framer = self.peer.framer
        framer.tx_accm = self._pending_tx.get('accm', 0xFFFFFFFF)
        framer.tx_pfc = self._pending_tx.get('pfc', False)
        framer.tx_acfc = self._pending_tx.get('acfc', False)
        framer.rx_accm = self._pending_rx.get('accm', 0xFFFFFFFF)
        framer.mru = self._pending_rx.get('mru', DEFAULT_MRU)
        self.peer.on_lcp_up()

    def open(self, now: float) -> None:
        # A fresh negotiation starts from the conservative defaults -- escape
        # everything, compress nothing -- because nothing has been agreed yet.
        # This is the only place that resets them.  Doing it on the way *down*
        # is the obvious alternative and is wrong: the Terminate-Ack closing
        # the link is still in flight under the old ACCM, and a receiver that
        # has already reverted discards the frame it is waiting for.
        if self.state == 'closed':
            self.peer.framer.restore_defaults()
        super().open(now)

    def this_layer_down(self) -> None:
        self._pending_tx.clear()
        self._pending_rx.clear()
        self.peer.on_lcp_down()


# ---------------------------------------------------------------------------
# Authentication


class PapServer:
    """RFC 1334 PAP. The password crosses the link in clear, which is why the
    server defaults to CHAP and this exists only for clients that cannot."""

    protocol = PROTO_PAP

    def __init__(self, peer) -> None:
        self.peer = peer
        self.user = None
        self.done = False

    def start(self, now: float) -> None:
        """The authenticator does not speak first in PAP; the client does."""

    def tick(self, now: float) -> None:
        pass

    def feed(self, payload: bytes, now: float) -> None:
        if len(payload) < 4:
            raise PppError('PAP packet shorter than a header')
        code, identifier, length = struct.unpack('>BBH', payload[:4])
        if code != 1:
            return                          # Ack/Nak are for the client side
        data = payload[4:length]
        if not data:
            raise PppError('PAP Authenticate-Request with no peer-id')
        user_length = data[0]
        user = data[1:1 + user_length]
        rest = data[1 + user_length:]
        password = rest[1:1 + rest[0]] if rest else b''
        expected = self.peer.secrets.get(user.decode('latin-1'))
        if expected is not None and password.decode('latin-1') == expected:
            self.user = user.decode('latin-1')
            message = b'access granted'
            self.peer.transmit(PROTO_PAP, struct.pack(
                '>BBHB', 2, identifier, 5 + len(message), len(message)) + message)
            self.done = True
            self.peer.on_auth_result(True, self.user)
        else:
            message = b'access denied'
            self.peer.transmit(PROTO_PAP, struct.pack(
                '>BBHB', 3, identifier, 5 + len(message), len(message)) + message)
            self.peer.on_auth_result(False, user.decode('latin-1'))


class ChapServer:
    """RFC 1994 CHAP with MD5. The secret is never transmitted."""

    protocol = PROTO_CHAP

    def __init__(self, peer, *, restart_timeout: float = 3.0,
                 max_restarts: int = 5) -> None:
        self.peer = peer
        self.identifier = 0
        self.challenge = b''
        self.user = None
        self.done = False
        self.deadline = None
        self.restart_timeout = restart_timeout
        self.restarts = max_restarts

    def start(self, now: float) -> None:
        self.identifier = (self.identifier + 1) & 0xFF
        self.challenge = os.urandom(16)
        self._send_challenge(now)

    def _send_challenge(self, now: float) -> None:
        name = self.peer.hostname.encode('latin-1')
        body = bytes((len(self.challenge),)) + self.challenge + name
        self.peer.transmit(PROTO_CHAP, struct.pack(
            '>BBH', 1, self.identifier, len(body) + 4) + body)
        self.deadline = now + self.restart_timeout

    def tick(self, now: float) -> None:
        if self.done or self.deadline is None or now < self.deadline:
            return
        self.restarts -= 1
        if self.restarts <= 0:
            self.deadline = None
            self.peer.on_auth_result(False, None)
            return
        self._send_challenge(now)

    def feed(self, payload: bytes, now: float) -> None:
        if len(payload) < 4:
            raise PppError('CHAP packet shorter than a header')
        code, identifier, length = struct.unpack('>BBH', payload[:4])
        if code != 2:
            return                          # Success/Failure are the client's
        data = payload[4:length]
        if not data:
            raise PppError('CHAP Response with no value')
        value = data[1:1 + data[0]]
        user = data[1 + data[0]:].decode('latin-1')
        secret = self.peer.secrets.get(user)
        ok = False
        if secret is not None and identifier == self.identifier:
            expect = hashlib.md5(bytes((identifier,))
                                 + secret.encode('latin-1')
                                 + self.challenge).digest()
            # Constant time is not security theatre here even though the link
            # is emulated: the comparison is cheap and the habit is correct.
            ok = _constant_time_eq(expect, value)
        self.deadline = None
        code = 3 if ok else 4
        message = b'welcome' if ok else b'authentication failed'
        self.peer.transmit(PROTO_CHAP, struct.pack(
            '>BBH', code, identifier, len(message) + 4) + message)
        self.done = ok
        self.user = user if ok else None
        self.peer.on_auth_result(ok, user)


def _constant_time_eq(a: bytes, b: bytes) -> bool:
    if len(a) != len(b):
        return False
    difference = 0
    for x, y in zip(a, b):
        difference |= x ^ y
    return difference == 0


class AuthClient:
    """The caller's half of PAP and CHAP, so a client can be driven in tests."""

    def __init__(self, peer, user: str, password: str) -> None:
        self.peer = peer
        self.user = user
        self.password = password
        self.succeeded = None

    @property
    def protocol(self) -> int:
        """Whichever protocol the peer demanded in its LCP Configure-Request.

        The client does not choose this, so deriving it beats storing it: a
        stored copy would be wrong for the whole of a CHAP exchange, where
        nothing is sent until the challenge arrives and there is no natural
        moment to set it.
        """
        return PROTO_PAP if self.peer.lcp.peer_auth == 'pap' else PROTO_CHAP

    def start(self, now: float) -> None:
        """PAP speaks first; CHAP waits to be challenged."""
        if self.peer.lcp.peer_auth == 'pap':
            user = self.user.encode('latin-1')
            password = self.password.encode('latin-1')
            body = (bytes((len(user),)) + user
                    + bytes((len(password),)) + password)
            self.peer.transmit(PROTO_PAP, struct.pack(
                '>BBH', 1, 1, len(body) + 4) + body)

    def tick(self, now: float) -> None:
        pass

    def feed(self, payload: bytes, now: float) -> None:
        if len(payload) < 4:
            raise PppError('auth packet shorter than a header')
        code, identifier, length = struct.unpack('>BBH', payload[:4])
        data = payload[4:length]
        if self.peer.lcp.peer_auth == 'chap':
            if code == 1:                   # Challenge
                challenge = data[1:1 + data[0]]
                digest = hashlib.md5(bytes((identifier,))
                                     + self.password.encode('latin-1')
                                     + challenge).digest()
                user = self.user.encode('latin-1')
                body = bytes((len(digest),)) + digest + user
                self.peer.transmit(PROTO_CHAP, struct.pack(
                    '>BBH', 2, identifier, len(body) + 4) + body)
                return
            if code in (3, 4):
                self.succeeded = code == 3
                self.peer.on_auth_result(self.succeeded, self.user)
            return
        if code in (2, 3):
            self.succeeded = code == 2
            self.peer.on_auth_result(self.succeeded, self.user)


# ---------------------------------------------------------------------------
# IPCP


class Ipcp(ControlProtocol):
    protocol = PROTO_IPCP
    name = 'ipcp'

    def __init__(self, peer, **kwargs) -> None:
        super().__init__(peer, **kwargs)
        self.local_address = peer.local_address
        self.peer_address = peer.peer_address
        self.dns = peer.dns
        self.want_dns = peer.role == 'client'
        self.assigned = None                # what the peer settled on
        self.peer_dns = [None, None]

    def local_options(self):
        options = [(OPT_IP_ADDRESS, ip_to_bytes(self.local_address))]
        if self.want_dns:
            # A client asks with 0.0.0.0, is Nakked with the real address, and
            # must then ask for *that* -- re-sending the placeholder would earn
            # the same Nak forever.
            for option, learned in ((OPT_DNS1, self.peer_dns[0]),
                                    (OPT_DNS2, self.peer_dns[1])):
                options.append((option, ip_to_bytes(learned or '0.0.0.0')))
        return options

    def review_peer_option(self, otype: int, value: bytes):
        if otype == OPT_IP_ADDRESS:
            if len(value) != 4:
                return 'nak', ip_to_bytes(self.peer_address)
            if self.peer.role == 'client':
                # The server is telling us its own address; it is not ours to
                # second-guess.
                return 'ack', value
            wanted = ip_to_bytes(self.peer_address)
            # 0.0.0.0 means "assign me one", and any other address that is not
            # the one this server hands out gets the same answer.
            return ('ack', value) if value == wanted else ('nak', wanted)
        if otype in (OPT_DNS1, OPT_DNS2):
            if self.peer.role == 'client':
                return 'ack', value
            offered = ip_to_bytes(self.dns[0 if otype == OPT_DNS1 else 1])
            return ('ack', value) if value == offered else ('nak', offered)
        if otype in (OPT_NBNS1, OPT_NBNS2):
            # No WINS server exists behind this link, and Nakking with a bogus
            # one would be worse than refusing.
            return 'rej', value
        if otype == OPT_IP_COMPRESSION:
            # Van Jacobson header compression is a real feature this does not
            # implement. Rejecting it is honest; every client falls back.
            return 'rej', value
        return 'rej', value

    def apply_peer_option(self, otype: int, value: bytes) -> None:
        if otype == OPT_IP_ADDRESS:
            self.assigned = bytes_to_ip(value)

    def accept_nak(self, otype: int, value: bytes) -> None:
        if otype == OPT_IP_ADDRESS and len(value) == 4:
            self.local_address = bytes_to_ip(value)
        elif otype == OPT_DNS1 and len(value) == 4:
            self.peer_dns[0] = bytes_to_ip(value)
        elif otype == OPT_DNS2 and len(value) == 4:
            self.peer_dns[1] = bytes_to_ip(value)

    def reject_local_option(self, otype: int) -> None:
        if otype in (OPT_DNS1, OPT_DNS2):
            self.want_dns = False

    def this_layer_up(self) -> None:
        self.peer.on_ipcp_up()

    def this_layer_down(self) -> None:
        self.peer.on_ipcp_down()


# ---------------------------------------------------------------------------
# IP


def ip_checksum(data: bytes) -> int:
    if len(data) & 1:
        data += b'\x00'
    total = sum(struct.unpack('>%dH' % (len(data) // 2), data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


class IcmpEchoResponder:
    """Answer pings to the server address, and nothing else.

    This is the end-to-end test instrument, not a network stack: `ping` from a
    dialled-in client exercises framing, ACCM escaping, MRU and IPCP in one
    command, and its round-trip time is the only latency measurement of the
    whole emulated path that needs no instrumentation at either end.
    """

    def __init__(self, address: str) -> None:
        self.address = address
        self.replied = 0

    def __call__(self, packet: bytes) -> bytes | None:
        if len(packet) < 20 or packet[0] >> 4 != 4:
            return None
        header_length = (packet[0] & 0x0F) * 4
        if len(packet) < header_length + 8 or packet[9] != 1:
            return None
        destination = packet[16:20]
        if destination != ip_to_bytes(self.address):
            return None
        icmp = packet[header_length:]
        if icmp[0] != 8:                    # Echo Request
            return None
        reply_icmp = bytearray(icmp)
        reply_icmp[0] = 0                   # Echo Reply
        reply_icmp[2:4] = b'\x00\x00'
        reply_icmp[2:4] = struct.pack('>H', ip_checksum(bytes(reply_icmp)))
        header = bytearray(packet[:header_length])
        header[16:20] = packet[12:16]       # swap source and destination
        header[12:16] = destination
        header[8] = 64                      # a fresh TTL for the return trip
        header[10:12] = b'\x00\x00'
        header[10:12] = struct.pack('>H', ip_checksum(bytes(header)))
        self.replied += 1
        return bytes(header) + bytes(reply_icmp)


# ---------------------------------------------------------------------------
# The peer


@dataclass
class PppConfig:
    """Everything the two roles differ by, in one place."""

    role: str = 'server'                    # 'server' or 'client'
    # RFC 6598 shared space, so a caller's own LAN cannot collide with what it
    # is assigned here. See CGNAT_PREFIX.
    local_address: str = '100.64.0.1'
    peer_address: str = '100.64.0.2'
    dns: tuple = ('100.64.0.1', '100.64.0.1')
    hostname: str = 'eicon'
    auth: str | None = 'chap'               # server: demand this of the caller
    secrets: dict = field(default_factory=lambda: {'ppp': 'ppp'})
    username: str = 'ppp'                   # client: present these
    password: str = 'ppp'
    echo_interval: float = 20.0             # 0 disables the keepalive
    echo_failures: int = 3
    icmp_echo: bool = True
    trace: bool = False                     # log every packet in and out


class PppPeer:
    """One end of a PPP link over a byte stream.

    Drive it with three calls and nothing else: ``feed()`` with whatever came
    off the link, ``tick()`` once per media tick so the restart timers run, and
    ``take()`` to collect what should go back out.  Nothing here touches a
    socket, a file descriptor or a clock of its own, which is what lets the
    same object run against a PTY, against the V.42 endpoint, and against
    another ``PppPeer`` in a unit test.
    """

    def __init__(self, config: PppConfig | None = None, log=print) -> None:
        self.config = config or PppConfig()
        self.role = self.config.role
        self.log = log
        self.hostname = self.config.hostname
        self.secrets = dict(self.config.secrets)
        self.local_address = self.config.local_address
        self.peer_address = self.config.peer_address
        self.dns = self.config.dns
        self.framer = HdlcFramer()
        self.lcp = Lcp(self)
        if self.role == 'server':
            self.lcp.require_auth = self.config.auth
        self.ipcp = Ipcp(self)
        self.auth = None
        self.tx = bytearray()
        self.rx_ip: list[bytes] = []
        self.rejected_protocols: set[int] = set()
        self.echo_replies = 0
        self.echo_outstanding = 0
        self.next_echo = None
        self.authenticated = False
        self.authenticated_user = None
        self.up = False                     # IPCP opened: IP may flow
        self.down_reason = None
        self.tx_packets = 0
        self.rx_packets = 0
        self.handler = (IcmpEchoResponder(self.local_address)
                        if self.config.icmp_echo else None)
        # A network -- a TunBridge, in practice -- replaces the ICMP responder
        # rather than sitting beside it: with a real interface behind the link
        # the host answers its own pings, and a responder here would reply to
        # them first and shadow whatever the kernel was going to say.
        self.network = None
        self.tx_ip = 0
        self.rx_ip_count = 0
        self._started = False
        self._clock = 0.0

    # -- link interface ------------------------------------------------------

    def start(self, now: float = 0.0) -> None:
        """Bring the link up. Idempotent, so a caller may just call it."""
        if self._started:
            return
        self._started = True
        self._clock = now
        self.log(f'[ppp] {self.role} starting, '
                 f'{self.local_address} -> {self.peer_address}')
        self.lcp.open(now)

    def stop(self, now: float = 0.0, reason: str = 'closed') -> None:
        if not self._started:
            return
        self.down_reason = reason
        if self.ipcp.state == 'opened':
            self.ipcp.close(now)
        self.lcp.close(now)

    def transmit(self, protocol: int, payload: bytes) -> None:
        if self.config.trace:
            self.log(f'[ppp] tx {describe(protocol, payload)}')
        self.tx += self.framer.encode(protocol, payload)
        self.tx_packets += 1

    def take(self) -> bytes:
        """Collect and clear everything queued for the link."""
        data = bytes(self.tx)
        del self.tx[:]
        return data

    def feed(self, data: bytes, now: float = 0.0) -> None:
        self._clock = now
        if not self._started:
            self.start(now)
        self.framer.push(data)
        while True:
            # One frame at a time: dispatching a Configure-Ack changes the
            # ACCM, and the very next frame in the same read is already sent
            # under the new terms. Decoding them all first drops it.
            frame = self.framer.next_frame()
            if frame is None:
                return
            self.rx_packets += 1
            try:
                protocol, payload = parse_packet(frame)
                self._dispatch(protocol, payload, now)
            except PppError as exc:
                # A malformed packet is the peer's problem, not a reason to
                # drop a link that may recover on the retransmission.
                self.log(f'[ppp] discarding a bad frame: {exc}')

    def _dispatch(self, protocol: int, payload: bytes, now: float) -> None:
        if self.config.trace:
            self.log(f'[ppp] rx {describe(protocol, payload)}')
        if protocol == PROTO_LCP:
            self.lcp.feed(payload, now)
            return
        if self.lcp.state != 'opened':
            # RFC 1661 section 3.4: nothing but LCP is legal before LCP opens.
            return
        if protocol in (PROTO_PAP, PROTO_CHAP):
            if self.auth is not None and protocol == getattr(
                    self.auth, 'protocol', protocol):
                self.auth.feed(payload, now)
            else:
                self._protocol_reject(protocol, payload)
            return
        if protocol == PROTO_IPCP:
            if (self.lcp.require_auth and self.role == 'server'
                    and not self.authenticated):
                # An unauthenticated caller does not get to negotiate an
                # address. Silently ignoring is deliberate: a Protocol-Reject
                # would tell it IPCP is unavailable rather than premature.
                return
            self.ipcp.feed(payload, now)
            return
        if protocol == PROTO_IP:
            if self.ipcp.state == 'opened':
                self._receive_ip(payload)
            return
        self._protocol_reject(protocol, payload)

    def _protocol_reject(self, protocol: int, payload: bytes) -> None:
        body = struct.pack('>H', protocol) + payload
        self.lcp.send(PROTO_REJ, self.lcp._next_id(), body)

    def tick(self, now: float) -> None:
        """Run the restart timers. Cheap enough to call every media tick."""
        self._clock = now
        if not self._started:
            return
        self.lcp.tick(now)
        if self.auth is not None:
            self.auth.tick(now)
        self.ipcp.tick(now)
        self._drain_network()
        self._echo_tick(now)

    def _echo_tick(self, now: float) -> None:
        interval = self.config.echo_interval
        if not interval or self.lcp.state != 'opened':
            return
        if self.next_echo is None:
            self.next_echo = now + interval
            return
        if now < self.next_echo:
            return
        self.next_echo = now + interval
        if self.echo_outstanding >= self.config.echo_failures:
            # A peer that has ignored three keepalives has hung up without
            # saying so, which on a dial-up link is the common case.
            self.log('[ppp] peer stopped answering echo requests')
            self.lcp.fail('echo timeout')
            return
        self.echo_outstanding += 1
        self.lcp.send(ECHO_REQ, self.lcp._next_id(),
                      struct.pack('>I', self.lcp.magic))

    # -- IP ------------------------------------------------------------------

    def send_ip(self, packet: bytes) -> None:
        if self.ipcp.state != 'opened':
            raise PppError('IPCP is not open; no IP may be sent')
        self.transmit(PROTO_IP, packet)

    def _receive_ip(self, packet: bytes) -> None:
        self.rx_ip_count += 1
        if self.network is not None:
            self.network.deliver(packet)
            return
        # With no network attached the datagrams are kept, because that list is
        # the only evidence a test or a trace has. Behind a tun they are not:
        # holding every packet of a real session would grow without bound.
        self.rx_ip.append(packet)
        if self.handler is not None:
            reply = self.handler(packet)
            if reply:
                self.transmit(PROTO_IP, reply)

    def attach_network(self, network) -> None:
        """Route this peer's IP through `network` instead of terminating it."""
        self.network = network
        self.handler = None

    def _drain_network(self) -> None:
        """Move anything the kernel has for the client onto the link."""
        if self.network is None or self.ipcp.state != 'opened':
            return
        for packet in self.network.poll():
            if len(packet) > self.lcp.peer_mru:
                # The peer told us what it can receive and this exceeds it.
                # The interface MTU is set from the same number, so this is
                # the kernel disagreeing with itself rather than routine.
                self.log(f'[ppp] dropping a {len(packet)}-byte datagram: '
                         f'the peer MRU is {self.lcp.peer_mru}')
                continue
            self.transmit(PROTO_IP, packet)
            self.tx_ip += 1

    # -- layer events --------------------------------------------------------

    def on_lcp_up(self) -> None:
        auth = self.lcp.require_auth if self.role == 'server' else self.lcp.peer_auth
        self.log(f'[ppp] LCP up  mru={self.framer.mru} '
                 f'accm=0x{self.framer.tx_accm:08x} auth={auth or "none"}')
        self.next_echo = None
        if self.role == 'server':
            # What was *negotiated*, not what was configured. These differ
            # whenever a client rejected our first choice, and reading the
            # configured value here sends a CHAP challenge down a link that
            # agreed on PAP.
            if self.lcp.require_auth == 'pap':
                self.auth = PapServer(self)
            elif self.lcp.require_auth == 'chap':
                self.auth = ChapServer(self)
            else:
                self.authenticated = True
                self.ipcp.open(self._now())
                return
        elif self.lcp.peer_auth:
            self.auth = AuthClient(self, self.config.username,
                                   self.config.password)
        else:
            self.authenticated = True
            self.ipcp.open(self._now())
            return
        self.auth.start(self._now())

    def on_lcp_down(self) -> None:
        self.log('[ppp] LCP down')
        self.authenticated = False
        self.auth = None
        self.up = False
        if self.ipcp.state != 'closed':
            self.ipcp.state = 'closed'
            self.ipcp.deadline = None

    def on_auth_result(self, ok: bool, user) -> None:
        if not ok:
            self.log(f'[ppp] authentication failed for {user!r}')
            if self.role == 'server':
                self.lcp.close(self._now())
            return
        self.log(f'[ppp] authenticated {user!r}')
        self.authenticated = True
        self.authenticated_user = user
        self.ipcp.open(self._now())

    def on_ipcp_up(self) -> None:
        self.up = True
        peer = self.ipcp.assigned or self.peer_address
        self.log(f'[ppp] IPCP up  local={self.ipcp.local_address} peer={peer}')
        if self.network is not None:
            device = getattr(self.network, 'device', None)
            if device is not None:
                # Only now is the peer's MRU known, and it is what the
                # interface must agree with. The device clamps to the MTU it
                # was configured with, so a small-MRU client cannot ratchet
                # the interface down for the callers after it.
                device.set_mtu(self.lcp.peer_mru)

    def on_ipcp_down(self) -> None:
        self.up = False
        self.log('[ppp] IPCP down')

    def _now(self) -> float:
        # The layer-up callbacks fire from inside feed() or tick(), both of
        # which stamp the clock on the way in.  Threading a float through every
        # hook in the automaton would be worse for the two places that need it.
        return self._clock

    # -- diagnostics ---------------------------------------------------------

    def summary(self) -> str:
        return (f'lcp={self.lcp.state} ipcp={self.ipcp.state} '
                f'auth={self.authenticated_user or "-"} '
                f'tx={self.tx_packets} rx={self.rx_packets} '
                f'fcs-errors={self.framer.fcs_errors}')


class LapmPppLink:
    """Bridge a ``PppPeer`` to a ``LapmEndpoint``, pumped once per media tick.

    The same shape as ``v42_pty.PtyLink`` and for the same reason: the caller
    owns the clock and calls ``pump()``.  LAPM's window is again the only
    backing store, so PPP output is metered into it rather than queued here --
    a PPP peer that outran the window would build a second, invisible queue and
    make every round-trip measurement meaningless.
    """

    def __init__(self, peer: PppPeer, log=print) -> None:
        self.peer = peer
        self.log = log
        self.to_link = 0
        self.from_link = 0
        self.blocked_ticks = 0
        self._backlog = bytearray()
        self._started = False

    def pump(self, lapm, now: float) -> None:
        if lapm is None or not lapm.data_ready:
            return
        if not self._started:
            self._started = True
            self.peer.start(now)
        if lapm.rx_data:
            payload = bytes(lapm.rx_data)
            del lapm.rx_data[:]
            self.from_link += len(payload)
            self.peer.feed(payload, now)
        self.peer.tick(now)
        self._backlog += self.peer.take()
        if not self._backlog:
            return
        capacity = (len(self._backlog) if lapm.raw_mode else
                    ((lapm.window - lapm.outstanding) * lapm.n401
                     - len(lapm.tx_stream)))
        if capacity <= 0:
            self.blocked_ticks += 1
            return
        count = min(capacity, len(self._backlog))
        lapm.send(bytes(self._backlog[:count]))
        del self._backlog[:count]
        self.to_link += count

    def close(self, now: float = 0.0) -> None:
        self.peer.stop(now, reason='call cleared')
        self.log(f'[ppp] {self.peer.summary()}; {self.to_link} bytes to the '
                 f'link, {self.from_link} from it '
                 f'({self.blocked_ticks} ticks with the window full)')


def make_server(**kwargs) -> PppPeer:
    """A dial-in server with the repo's defaults: CHAP, 100.64.0.1/2."""
    log = kwargs.pop('log', print)
    return PppPeer(PppConfig(role='server', **kwargs), log=log)


def make_client(**kwargs) -> PppPeer:
    """The calling half, for loopback tests and for driving the far endpoint."""
    log = kwargs.pop('log', print)
    kwargs.setdefault('local_address', '0.0.0.0')
    kwargs.setdefault('icmp_echo', False)
    kwargs.setdefault('auth', None)
    return PppPeer(PppConfig(role='client', **kwargs), log=log)
