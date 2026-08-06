#!/usr/bin/env python3
"""A userspace NAT, so PPP clients reach the network without root or a tun.

The tun device asks the kernel to route for the client, which is why it needs
root.  This does the opposite: it never hands a packet to the kernel at all.
Client flows are *terminated* here and re-originated as ordinary sockets the
host already permits, which is why nothing privileged is involved and nothing
system-wide is touched.

This is what `slirp` was written for in the early nineties -- giving dial-up
users real IP over a plain shell account, with no root and no kernel support --
and it is the same shape as QEMU's user-mode networking today.  For a modem
emulator it is arguably the more faithful design, not a fallback.

What works: TCP to anywhere, UDP (so DNS), and ICMP echo through the
unprivileged datagram socket.  What cannot, because there is no kernel path
carrying it: traceroute's TTL behaviour, GRE, IPsec, and raw sockets on the
client.  Connections are outbound only -- nothing on the host network can
initiate one *to* a client, which a tun plus a route would allow.

The TCP here is deliberately modest: no SACK, no timestamps, no window scaling
and no congestion control.  It can afford to be. Underneath is a V.42 link,
which is already reliable and in-order, so the segments this sends a client
never arrive out of order and are lost only if the client itself drops them.
Congestion control on a 33.6k link whose bottleneck is the modem would be
measuring something that is not in question.  Flow control is real, though, and
is driven from the socket buffers at both ends.

SECURITY: a client reaches whatever this host can reach, including its LAN and
its loopback services. The link is authenticated (CHAP by default) and this is
a lab tool; do not point it at untrusted callers and expect a boundary.
"""
from __future__ import annotations

import errno
import os
import selectors
import socket
import struct
import time

PROTO_ICMP = 1
PROTO_TCP = 6
PROTO_UDP = 17

FIN, SYN, RST, PSH, ACK, URG = 1, 2, 4, 8, 16, 32

# What we advertise to the client, and therefore how much unacknowledged data
# it may have in flight toward us. It is also the cap on how much client data
# we buffer per flow while the remote socket is not draining.
RECEIVE_BUFFER = 65535
# Read no more than this from a remote socket in one poll, so one busy flow
# cannot starve the others.
READ_CHUNK = 8192
IDLE_TIMEOUT = 300.0            # TCP flows with nothing happening
UDP_TIMEOUT = 60.0              # the usual NAT mapping lifetime
ICMP_TIMEOUT = 30.0
RETRANSMIT_TIMEOUT = 1.0
MAX_RETRANSMITS = 6


def checksum(data: bytes) -> int:
    if len(data) & 1:
        data += b'\x00'
    total = sum(struct.unpack('>%dH' % (len(data) // 2), data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def _pseudo_header(source: bytes, destination: bytes, protocol: int,
                   length: int) -> bytes:
    return source + destination + struct.pack('>BBH', 0, protocol, length)


def parse_ipv4(packet: bytes):
    """Return (source, destination, protocol, payload) or None if unusable.

    Fragments are rejected rather than reassembled: reassembly is a whole
    subsystem, and nothing this NAT originates is large enough to provoke one
    from a correct peer.
    """
    if len(packet) < 20 or packet[0] >> 4 != 4:
        return None
    header_length = (packet[0] & 0x0F) * 4
    if header_length < 20 or len(packet) < header_length:
        return None
    total_length = struct.unpack('>H', packet[2:4])[0]
    if total_length < header_length or total_length > len(packet):
        return None
    flags_fragment = struct.unpack('>H', packet[6:8])[0]
    if flags_fragment & 0x1FFF or flags_fragment & 0x2000:
        return None
    return (packet[12:16], packet[16:20], packet[9],
            packet[header_length:total_length])


class _Identifiers:
    """The IP identification field, which only has to not repeat quickly."""

    def __init__(self) -> None:
        self.value = 0

    def next(self) -> int:
        self.value = (self.value + 1) & 0xFFFF
        return self.value


def build_ipv4(source: bytes, destination: bytes, protocol: int,
               payload: bytes, identifier: int = 0, ttl: int = 64) -> bytes:
    header = bytearray(struct.pack('>BBHHHBBH', 0x45, 0, 20 + len(payload),
                                   identifier, 0x4000, ttl, protocol, 0)
                       + source + destination)
    header[10:12] = struct.pack('>H', checksum(bytes(header)))
    return bytes(header) + payload


def build_tcp(source: bytes, destination: bytes, source_port: int,
              destination_port: int, seq: int, ack: int, flags: int,
              window: int, payload: bytes = b'', options: bytes = b'') -> bytes:
    if len(options) % 4:
        options += b'\x00' * (4 - len(options) % 4)
    offset = (20 + len(options)) // 4
    segment = bytearray(struct.pack('>HHIIBBHHH', source_port, destination_port,
                                    seq & 0xFFFFFFFF, ack & 0xFFFFFFFF,
                                    offset << 4, flags, window, 0, 0)
                        + options + payload)
    total = _pseudo_header(source, destination, PROTO_TCP, len(segment))
    segment[16:18] = struct.pack('>H', checksum(total + bytes(segment)))
    return bytes(segment)


def parse_tcp(segment: bytes):
    """Return a dict of the fields this NAT acts on, or None if malformed."""
    if len(segment) < 20:
        return None
    (source_port, destination_port, seq, ack, offset_byte, flags, window,
     _checksum, _urgent) = struct.unpack('>HHIIBBHHH', segment[:20])
    offset = (offset_byte >> 4) * 4
    if offset < 20 or len(segment) < offset:
        return None
    return {'sport': source_port, 'dport': destination_port, 'seq': seq,
            'ack': ack, 'flags': flags, 'window': window,
            'options': segment[20:offset], 'payload': segment[offset:]}


def tcp_mss_option(options: bytes) -> int | None:
    """The peer's MSS, which bounds every segment sent to it."""
    index = 0
    while index < len(options):
        kind = options[index]
        if kind == 0:                       # End of option list
            return None
        if kind == 1:                       # No-op
            index += 1
            continue
        if index + 1 >= len(options):
            return None
        length = options[index + 1]
        if length < 2 or index + length > len(options):
            return None
        if kind == 2 and length == 4:
            return struct.unpack('>H', options[index + 2:index + 4])[0]
        index += length
    return None


def build_udp(source: bytes, destination: bytes, source_port: int,
              destination_port: int, payload: bytes) -> bytes:
    datagram = bytearray(struct.pack('>HHHH', source_port, destination_port,
                                     8 + len(payload), 0) + payload)
    total = _pseudo_header(source, destination, PROTO_UDP, len(datagram))
    value = checksum(total + bytes(datagram))
    # A zero checksum means "not computed" in UDP, so the all-ones form is
    # used for the value that would otherwise encode as zero.
    datagram[6:8] = struct.pack('>H', value or 0xFFFF)
    return bytes(datagram)


def _serial_lt(a: int, b: int) -> bool:
    """RFC 1982 comparison, so the sequence space wrapping is not a special
    case scattered through the flow logic."""
    return ((a - b) & 0xFFFFFFFF) > 0x80000000


class TcpFlow:
    """One client TCP connection, terminated here and spliced to a socket."""

    def __init__(self, network, key, client_isn: int, mss: int,
                 window: int, local_mss: int = 1460) -> None:
        self.network = network
        self.key = key                      # (client, cport, remote, rport)
        self.client, self.client_port, self.remote, self.remote_port = key
        self.state = 'connecting'
        self.irs = client_isn
        self.rcv_nxt = (client_isn + 1) & 0xFFFFFFFF
        # A random ISS is the correct thing even here: a client that reuses a
        # four-tuple quickly must not see stale sequence numbers accepted.
        self.iss = struct.unpack('>I', os.urandom(4))[0]
        self.snd_una = self.iss
        self.snd_nxt = self.iss
        self.snd_wnd = window
        # Two different numbers that are easy to conflate. `mss` bounds what we
        # send, and is the smaller of what the client offered and what the link
        # carries. `local_mss` is what we announce we can receive, which is the
        # link's alone -- announcing the client's own number back at it would
        # tell it nothing and could understate what we accept.
        self.mss = mss
        self.local_mss = local_mss
        self.to_remote = bytearray()        # client data awaiting the socket
        self.from_remote = bytearray()      # socket data awaiting the client
        self.unacked = bytearray()          # sent to the client, not yet acked
        self.client_closed = False          # client sent FIN
        self.remote_closed = False          # socket reached EOF
        self.fin_sent = False
        self.fin_seq = None
        self.retransmit_at = None
        self.retransmits = 0
        self.last_activity = time.monotonic()
        # The window last advertised, so a window that reopens can be
        # announced. Without that, a client that stopped on a zero window has
        # nothing to prompt it to resume and the flow deadlocks.
        self.advertised = RECEIVE_BUFFER
        self.socket = socket.socket()
        self.socket.setblocking(False)
        self.connected = False
        address = ('.'.join(str(b) for b in self.remote), self.remote_port)
        try:
            self.socket.connect(address)
            self.connected = True
        except BlockingIOError:
            pass
        except OSError:
            # Refused outright (a bad address family, say). Behave the way the
            # network would: reset, rather than leaving the client to time out.
            self.state = 'failed'

    # -- helpers -------------------------------------------------------------

    @property
    def receive_window(self) -> int:
        """Shrink as client data backs up, so the client stops sending.

        This is the only backpressure toward the client: without it, a slow
        remote end would make this process buffer the whole transfer.
        """
        return max(0, RECEIVE_BUFFER - len(self.to_remote))

    def emit(self, flags: int, payload: bytes = b'', options: bytes = b'',
             seq: int | None = None) -> None:
        self.advertised = self.receive_window
        segment = build_tcp(self.remote, self.client, self.remote_port,
                            self.client_port,
                            self.snd_nxt if seq is None else seq,
                            self.rcv_nxt, flags, self.receive_window,
                            payload, options)
        self.network.send_ip(self.remote, self.client, PROTO_TCP, segment)

    def reset(self) -> None:
        self.emit(RST | ACK)
        self.state = 'closed'
        self.close_socket()

    def close_socket(self) -> None:
        if self.socket is not None:
            self.network.unregister(self.socket)
            self.socket.close()
            self.socket = None

    # -- from the client -----------------------------------------------------

    def on_segment(self, tcp) -> None:
        self.last_activity = time.monotonic()
        flags = tcp['flags']
        if flags & RST:
            self.state = 'closed'
            self.close_socket()
            return
        if flags & SYN and self.state == 'connecting':
            # A retransmitted SYN while the socket is still connecting. The
            # handshake completes when the socket does, not before.
            return
        self.snd_wnd = tcp['window']
        if flags & ACK:
            self._process_ack(tcp['ack'])
        payload = tcp['payload']
        consumed = 0
        if payload:
            if tcp['seq'] == self.rcv_nxt:
                # Never take more than the window just advertised, or the
                # backpressure toward the client is advisory only and this
                # process buffers the whole transfer.
                consumed = min(len(payload), self.receive_window)
                if consumed:
                    self.to_remote += payload[:consumed]
                    self.rcv_nxt = (self.rcv_nxt + consumed) & 0xFFFFFFFF
                self.emit(ACK)
            else:
                # Out of order. Underneath is V.42, which does not reorder, so
                # this is a retransmission of something already taken: re-ack
                # what we have and let the client move on.
                self.emit(ACK)
        # The FIN sits after the segment's payload, and only counts once
        # everything before it has been accepted -- a FIN riding on data that
        # the window forced us to truncate is not yet in sequence.
        if flags & FIN and not self.client_closed:
            fin_seq = (tcp['seq'] + len(payload)) & 0xFFFFFFFF
            if fin_seq == self.rcv_nxt and consumed == len(payload):
                self.client_closed = True
                self.rcv_nxt = (self.rcv_nxt + 1) & 0xFFFFFFFF
                self.emit(ACK)
                # Half close: the remote may still have data to send back, and
                # closing outright here would truncate it.
                if self.socket is not None and self.connected:
                    try:
                        self.socket.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
        self._maybe_finish()

    def _process_ack(self, ack: int) -> None:
        if _serial_lt(ack, self.snd_una) or ack == self.snd_una:
            return
        if _serial_lt(self.snd_nxt, ack):
            return                          # acks data never sent
        acked = (ack - self.snd_una) & 0xFFFFFFFF
        consumed = min(acked, len(self.unacked))
        del self.unacked[:consumed]
        self.snd_una = ack
        self.retransmits = 0
        self.retransmit_at = (time.monotonic() + RETRANSMIT_TIMEOUT
                              if self.unacked else None)

    # -- toward the client ---------------------------------------------------

    def service(self, now: float) -> None:
        if self.state in ('closed', 'failed'):
            return
        if self.state == 'connecting':
            self._check_connect()
            return
        self._write_to_remote()
        if self.advertised == 0 and self.receive_window > 0:
            # The socket drained and the window reopened. The client is
            # waiting to be told.
            self.emit(ACK)
        self._send_to_client()
        self._retransmit(now)
        self._maybe_finish()

    def _check_connect(self) -> None:
        error = self.socket.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        if error == 0:
            self.state = 'established'
            self.connected = True
            # SYN occupies one sequence number, so snd_nxt advances past it.
            self.emit(SYN | ACK,
                      options=struct.pack('>BBH', 2, 4, self.local_mss))
            self.snd_nxt = (self.snd_nxt + 1) & 0xFFFFFFFF
        elif error not in (errno.EINPROGRESS, errno.EALREADY):
            # Refused, unreachable, timed out: a RST is what a client expects
            # here, and it fails fast instead of hanging.
            self.state = 'failed'

    def _write_to_remote(self) -> None:
        while self.to_remote and self.socket is not None:
            try:
                sent = self.socket.send(bytes(self.to_remote[:READ_CHUNK]))
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                self.state = 'closed'
                self.close_socket()
                return
            if not sent:
                return
            del self.to_remote[:sent]

    def on_readable(self) -> None:
        """The socket has data or has reached EOF."""
        if self.socket is None or self.state != 'established':
            return
        try:
            data = self.socket.recv(READ_CHUNK)
        except (BlockingIOError, InterruptedError):
            return
        except ConnectionResetError:
            # The remote reset. Pass that through rather than a clean FIN, so
            # the client learns the transfer did not complete.
            self.reset()
            return
        except OSError:
            self.reset()
            return
        if not data:
            self.remote_closed = True
            self.network.unregister(self.socket)
            return
        self.from_remote += data
        self.last_activity = time.monotonic()

    def _send_to_client(self) -> None:
        while self.from_remote:
            in_flight = (self.snd_nxt - self.snd_una) & 0xFFFFFFFF
            allowed = self.snd_wnd - in_flight
            if allowed <= 0:
                return                      # the client's window is closed
            count = min(len(self.from_remote), self.mss, allowed)
            if count <= 0:
                return
            payload = bytes(self.from_remote[:count])
            del self.from_remote[:count]
            self.emit(PSH | ACK, payload)
            self.unacked += payload
            self.snd_nxt = (self.snd_nxt + count) & 0xFFFFFFFF
            if self.retransmit_at is None:
                self.retransmit_at = time.monotonic() + RETRANSMIT_TIMEOUT

    def _retransmit(self, now: float) -> None:
        if self.retransmit_at is None or now < self.retransmit_at:
            return
        if not self.unacked:
            self.retransmit_at = None
            return
        self.retransmits += 1
        if self.retransmits > MAX_RETRANSMITS:
            # The client has stopped acknowledging anything. Nothing below is
            # going to recover it.
            self.reset()
            return
        chunk = bytes(self.unacked[:self.mss])
        self.emit(PSH | ACK, chunk, seq=self.snd_una)
        # Back off, so a client that has gone away is not hammered.
        self.retransmit_at = now + RETRANSMIT_TIMEOUT * (2 ** self.retransmits)

    def _maybe_finish(self) -> None:
        if (self.remote_closed and not self.from_remote and not self.fin_sent
                and self.state == 'established'):
            self.emit(FIN | ACK)
            self.fin_seq = self.snd_nxt
            self.snd_nxt = (self.snd_nxt + 1) & 0xFFFFFFFF
            self.fin_sent = True
        if (self.fin_sent and self.client_closed
                and not _serial_lt(self.snd_una, self.snd_nxt)):
            # Both directions closed and our FIN acknowledged.
            self.state = 'closed'
            self.close_socket()

    def expired(self, now: float) -> bool:
        return now - self.last_activity > IDLE_TIMEOUT


class UdpFlow:
    """One client UDP mapping, which is all DNS needs."""

    def __init__(self, network, key) -> None:
        self.network = network
        self.key = key
        self.client, self.client_port, self.remote, self.remote_port = key
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setblocking(False)
        self.last_activity = time.monotonic()

    def send(self, payload: bytes) -> None:
        self.last_activity = time.monotonic()
        address = ('.'.join(str(b) for b in self.remote), self.remote_port)
        try:
            self.socket.sendto(payload, address)
        except OSError:
            pass                            # unreachable: drop, as UDP does

    def on_readable(self) -> None:
        while True:
            try:
                data, _address = self.socket.recvfrom(65535)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            if not data:
                return
            self.last_activity = time.monotonic()
            datagram = build_udp(self.remote, self.client, self.remote_port,
                                 self.client_port, data)
            self.network.send_ip(self.remote, self.client, PROTO_UDP, datagram)

    def expired(self, now: float) -> bool:
        return now - self.last_activity > UDP_TIMEOUT

    def close(self) -> None:
        self.network.unregister(self.socket)
        self.socket.close()


class IcmpFlow:
    """Client pings, through the unprivileged ICMP datagram socket.

    One socket per (client, echo id) rather than one shared socket, because the
    two platforms disagree about the id: Darwin preserves what we send and
    Linux rewrites it to the socket's port.  Keyed on the socket, both work
    without caring which is which.
    """

    def __init__(self, network, key) -> None:
        self.network = network
        self.key = key
        self.client, self.identifier = key
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                                    socket.IPPROTO_ICMP)
        self.socket.setblocking(False)
        self.last_activity = time.monotonic()
        self.remote = None

    def send(self, remote: bytes, payload: bytes) -> None:
        self.last_activity = time.monotonic()
        self.remote = remote
        try:
            self.socket.sendto(payload,
                               ('.'.join(str(b) for b in remote), 0))
        except OSError:
            pass

    def on_readable(self) -> None:
        while True:
            try:
                data, address = self.socket.recvfrom(65535)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            if not data:
                return
            self.last_activity = time.monotonic()
            source = self.remote or bytes(int(p) for p in address[0].split('.'))
            # Darwin hands back the whole IP packet; Linux hands back only the
            # ICMP message. Detect rather than assume, so one code path serves
            # both.
            if data[0] >> 4 == 4 and len(data) > 20:
                header_length = (data[0] & 0x0F) * 4
                source = data[12:16]
                data = data[header_length:]
            if len(data) < 8:
                return
            reply = bytearray(data)
            # Restore the identifier the client used, in case the kernel
            # rewrote it to the socket's port on the way out.
            reply[4:6] = struct.pack('>H', self.identifier)
            reply[2:4] = b'\x00\x00'
            reply[2:4] = struct.pack('>H', checksum(bytes(reply)))
            self.network.send_ip(source, self.client, PROTO_ICMP, bytes(reply))

    def expired(self, now: float) -> bool:
        return now - self.last_activity > ICMP_TIMEOUT

    def close(self) -> None:
        self.network.unregister(self.socket)
        self.socket.close()


class UserNetwork:
    """The network handler: terminate client flows, re-originate as sockets.

    Same seam as ``TunBridge`` -- ``deliver()`` for what the client sends,
    ``poll()`` for what comes back -- so a ``PppPeer`` cannot tell which it has.
    """

    def __init__(self, mtu: int = 1500, log=print) -> None:
        self.mtu = mtu
        self.log = log
        self.selector = selectors.DefaultSelector()
        self.tcp: dict = {}
        self.udp: dict = {}
        self.icmp: dict = {}
        self.outbound: list[bytes] = []
        self.identifiers = _Identifiers()
        self.to_network = 0
        self.from_network = 0
        self.refused = 0
        self.flows_opened = 0
        self._owner: dict = {}

    # -- the PppPeer interface ----------------------------------------------

    def deliver(self, packet: bytes) -> None:
        parsed = parse_ipv4(packet)
        if parsed is None:
            self.refused += 1
            return
        source, destination, protocol, payload = parsed
        self.to_network += 1
        if protocol == PROTO_TCP:
            self._deliver_tcp(source, destination, payload)
        elif protocol == PROTO_UDP:
            self._deliver_udp(source, destination, payload)
        elif protocol == PROTO_ICMP:
            self._deliver_icmp(source, destination, payload)
        else:
            # No kernel path carries this, and pretending otherwise would make
            # a client wait for a reply that cannot come.
            self.refused += 1

    def poll(self) -> list[bytes]:
        now = time.monotonic()
        for key, _mask in self.selector.select(0):
            flow = self._owner.get(key.fd)
            if flow is not None:
                flow.on_readable()
        for flow in list(self.tcp.values()):
            flow.service(now)
            if flow.state == 'failed':
                flow.emit(RST | ACK)
                flow.state = 'closed'
                flow.close_socket()
        self._reap(now)
        packets, self.outbound = self.outbound, []
        self.from_network += len(packets)
        return packets

    def summary(self) -> str:
        return (f'usernet tcp={len(self.tcp)} udp={len(self.udp)} '
                f'icmp={len(self.icmp)} opened={self.flows_opened} '
                f'in={self.to_network} out={self.from_network} '
                f'unsupported={self.refused}')

    def close(self) -> None:
        for table in (self.tcp, self.udp, self.icmp):
            for flow in list(table.values()):
                if hasattr(flow, 'close'):
                    flow.close()
                else:
                    flow.close_socket()
            table.clear()
        self.selector.close()

    # -- socket bookkeeping --------------------------------------------------

    def unregister(self, sock) -> None:
        try:
            self.selector.unregister(sock)
        except (KeyError, ValueError):
            pass
        self._owner.pop(sock.fileno(), None)

    def send_ip(self, source: bytes, destination: bytes, protocol: int,
                payload: bytes) -> None:
        packet = build_ipv4(source, destination, protocol, payload,
                            self.identifiers.next())
        if len(packet) > self.mtu:
            # Nothing here should build one this large; if it does, dropping
            # beats handing PPP a frame the peer will discard.
            self.refused += 1
            return
        self.outbound.append(packet)

    # -- per protocol --------------------------------------------------------

    def _deliver_tcp(self, source: bytes, destination: bytes,
                     payload: bytes) -> None:
        tcp = parse_tcp(payload)
        if tcp is None:
            self.refused += 1
            return
        key = (source, tcp['sport'], destination, tcp['dport'])
        flow = self.tcp.get(key)
        if flow is None:
            if not tcp['flags'] & SYN:
                # No such connection. A RST is what the network would say, and
                # it stops the client retrying a flow that cannot exist.
                self._reset(source, destination, tcp)
                return
            local_mss = self.mtu - 40
            mss = min(tcp_mss_option(tcp['options']) or 536, local_mss)
            flow = TcpFlow(self, key, tcp['seq'], mss, tcp['window'],
                           local_mss)
            self.tcp[key] = flow
            self.flows_opened += 1
            if flow.state == 'failed':
                flow.emit(RST | ACK)
                del self.tcp[key]
                return
            self._adopt(flow, flow.socket)
            # Connect may already have completed on loopback.
            flow.service(time.monotonic())
            return
        flow.on_segment(tcp)

    def _reset(self, source: bytes, destination: bytes, tcp) -> None:
        if tcp['flags'] & RST:
            return                          # never reset a reset
        sequence = (tcp['seq'] + len(tcp['payload'])
                    + (1 if tcp['flags'] & (SYN | FIN) else 0))
        segment = build_tcp(destination, source, tcp['dport'], tcp['sport'],
                            tcp['ack'] if tcp['flags'] & ACK else 0,
                            sequence, RST | ACK, 0)
        self.send_ip(destination, source, PROTO_TCP, segment)

    def _deliver_udp(self, source: bytes, destination: bytes,
                     payload: bytes) -> None:
        if len(payload) < 8:
            self.refused += 1
            return
        source_port, destination_port, length, _checksum = struct.unpack(
            '>HHHH', payload[:8])
        if length < 8 or length > len(payload):
            self.refused += 1
            return
        key = (source, source_port, destination, destination_port)
        flow = self.udp.get(key)
        if flow is None:
            flow = UdpFlow(self, key)
            self.udp[key] = flow
            self.flows_opened += 1
            self._adopt(flow, flow.socket)
        flow.send(payload[8:length])

    def _deliver_icmp(self, source: bytes, destination: bytes,
                      payload: bytes) -> None:
        if len(payload) < 8 or payload[0] != 8:
            # Only echo requests. Everything else needs a raw socket, which is
            # the privilege this whole module exists to avoid.
            self.refused += 1
            return
        identifier = struct.unpack('>H', payload[4:6])[0]
        key = (source, identifier)
        flow = self.icmp.get(key)
        if flow is None:
            try:
                flow = IcmpFlow(self, key)
            except OSError as exc:
                self.log(f'[usernet] no ICMP socket: {exc}')
                self.refused += 1
                return
            self.icmp[key] = flow
            self.flows_opened += 1
            self._adopt(flow, flow.socket)
        flow.send(destination, payload)

    def _adopt(self, flow, sock) -> None:
        """Record which flow owns a descriptor, for the selector callback."""
        if sock is None:
            return
        try:
            self.selector.register(sock, selectors.EVENT_READ)
        except KeyError:
            pass
        self._owner[sock.fileno()] = flow

    def _reap(self, now: float) -> None:
        for key, flow in list(self.tcp.items()):
            if flow.state == 'closed' or flow.expired(now):
                flow.close_socket()
                del self.tcp[key]
        for table in (self.udp, self.icmp):
            for key, flow in list(table.items()):
                if flow.expired(now):
                    flow.close()
                    del table[key]
