#!/usr/bin/env python3
"""SIP to ISDN voice gateway for a Diva card, over divacapi's /dev/capi20.

Runs on eicon420. It registers to Asterisk as one extension and bridges each
call to a transparent 64 kbit/s B channel on one CAPI controller, so a SIP
caller reaches whatever sits on that BRI (the MultiTech, on SN 1331 port 3,
logical controller 3) and an ISDN caller there reaches any Asterisk extension
by dialling its number.

The B channel carries the line law octet for octet. RTP PCMA payload passes
straight through apart from CAPI's bit order, which is LSB first, so audio is
never transcoded here; keep the Asterisk endpoint A-law only for the same
reason. Transmit to the line is clocked by receive from it: every octet the
card delivers is answered with one octet, which locks the ISDN side to the
line clock and leaves Asterisk's clock to the jitter buffer.

No libcapi20 is needed. divacapi's character device takes one CAPI message
per write(), DATA_B3_REQ followed by its payload, and returns one per read(),
DATA_B3_IND followed by its payload at the offset its length field gives
(`kernel/capimain.c`, `kernel/capifunc.c`).
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import heapq
import itertools
import logging
import os
import random
import re
import selectors
import socket
import struct
import time
from dataclasses import dataclass, field

LOG = logging.getLogger('diva-sip')

# ---------------------------------------------------------------------------
# CAPI 2.0
# ---------------------------------------------------------------------------

CAPI_REGISTER = 0x400C4301      # _IOW('C', 1, 3 x u32)

ALERT, CONNECT, CONNECT_ACTIVE, DISCONNECT = 0x01, 0x02, 0x03, 0x04
LISTEN, INFO, FACILITY, MANUFACTURER = 0x05, 0x08, 0x80, 0xFF
CONNECT_B3, CONNECT_B3_ACTIVE, DISCONNECT_B3 = 0x82, 0x83, 0x84
DATA_B3, RESET_B3 = 0x86, 0x87
REQ, CONF, IND, RESP = 0x80, 0x81, 0x82, 0x83

DATA_B3_REQ_LEN = 22
# InfoMask: cause, display, call progression (ALERTING arrives as 0x8001).
INFO_MASK = 0x0001 | 0x0004 | 0x0010
# CIPMask: speech, 3.1 kHz audio, telephony.
CIP_MASK = (1 << 1) | (1 << 4) | (1 << 16)
INFO_ALERTING = 0x8001

# Reject codes for CONNECT_RESP.
REJECT_NORMAL, REJECT_BUSY, REJECT_OUT_OF_ORDER = 2, 3, 8

BIT_REVERSE = bytes(int(f'{value:08b}'[::-1], 2) for value in range(256))
ALAW_SILENCE = 0xD5


def cstruct(data: bytes = b'') -> bytes:
    if len(data) < 255:
        return bytes([len(data)]) + data
    return b'\xff' + struct.pack('<H', len(data)) + data


def capi_message(appl: int, command: int, sub: int, number: int, body: bytes) -> bytes:
    # DATA_B3_REQ appends its audio after the CAPI message, not inside it.
    # divacapi copies Data_Length bytes starting at this header length and
    # rejects the write if they do not fit after it (capifunc.c).
    length = DATA_B3_REQ_LEN if (command, sub) == (DATA_B3, REQ) else 8 + len(body)
    return struct.pack('<HHBBH', length, appl, command, sub, number) + body


def b_protocol_transparent() -> bytes:
    """B1 64 kbit/s transparent, B2 transparent, B3 transparent."""
    return cstruct(struct.pack('<HHH', 1, 1, 0) + cstruct() + cstruct() + cstruct())


def listen_req(controller: int) -> bytes:
    return struct.pack('<IIII', controller, INFO_MASK, CIP_MASK, 0) + cstruct() + cstruct()


def connect_req(controller: int, cip: int, called: str, calling: str) -> bytes:
    called_ie = cstruct(b'\x80' + called.encode()) if called else cstruct()
    calling_ie = cstruct(b'\x00\x80' + calling.encode()) if calling else cstruct()
    return (struct.pack('<IH', controller, cip) + called_ie + calling_ie
            + cstruct() + cstruct() + b_protocol_transparent()
            + cstruct() + cstruct() + cstruct() + cstruct())


def connect_resp(plci: int, reject: int) -> bytes:
    bprot = b_protocol_transparent() if reject == 0 else cstruct()
    return struct.pack('<IH', plci, reject) + bprot + cstruct() * 4


def data_b3_req(ncci: int, data: bytes, handle: int) -> bytes:
    return struct.pack('<IIHHH', ncci, 0, len(data), handle, 0) + data


class Reader:
    def __init__(self, data: bytes, pos: int = 8):
        self.data, self.pos = data, pos

    def take(self, fmt: str):
        value = struct.unpack_from(fmt, self.data, self.pos)
        self.pos += struct.calcsize(fmt)
        return value[0] if len(value) == 1 else value

    def struct(self) -> bytes:
        if self.pos >= len(self.data):
            return b''
        size = self.data[self.pos]
        self.pos += 1
        if size == 0xFF:
            size = self.take('<H')
        value = self.data[self.pos:self.pos + size]
        self.pos += size
        return value


def called_digits(ie: bytes) -> str:
    return ie[1:].decode(errors='replace') if ie else ''


def calling_digits(ie: bytes) -> str:
    return ie[2:].decode(errors='replace') if len(ie) > 2 else ''


@dataclass
class CapiMessage:
    command: int
    sub: int
    number: int
    raw: bytes

    @classmethod
    def parse(cls, raw: bytes) -> 'CapiMessage':
        _, _, command, sub, number = struct.unpack_from('<HHBBH', raw)
        return cls(command, sub, number, raw)

    def reader(self) -> Reader:
        return Reader(self.raw)

    def data_b3(self) -> tuple[int, int, bytes]:
        """(NCCI, DataHandle, payload) of a DATA_B3_IND."""
        length = struct.unpack_from('<H', self.raw)[0]
        ncci, _, size, handle = struct.unpack_from('<IIHH', self.raw, 8)
        return ncci, handle, self.raw[length:length + size]


class Capi:
    """One registered CAPI application on a divacapi-style character device."""

    def __init__(self, path: str = '/dev/capi20', *, fd: int | None = None,
                 level3cnt: int = 2, datablkcnt: int = 7, datablklen: int = 160):
        if fd is None:
            fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
            # The application id is the ioctl's return value, which Python's
            # fcntl does not expose for a buffer argument. The driver stamps
            # it into every message written, so it is not needed here.
            fcntl.ioctl(fd, CAPI_REGISTER, struct.pack('<III', level3cnt, datablkcnt, datablklen))
        self.fd = fd
        self.appl = 0
        self.numbers = itertools.count(1)

    def fileno(self) -> int:
        return self.fd

    def send(self, command: int, sub: int, body: bytes, number: int | None = None) -> int:
        if number is None:
            number = next(self.numbers) & 0x7FFF
        os.write(self.fd, capi_message(self.appl, command, sub, number, body))
        return number

    def receive(self) -> CapiMessage | None:
        try:
            raw = os.read(self.fd, 4096)
        except BlockingIOError:
            return None
        if len(raw) < 8:
            return None
        message = CapiMessage.parse(raw)
        self.appl = struct.unpack_from('<H', raw, 2)[0]
        return message


# ---------------------------------------------------------------------------
# SIP
# ---------------------------------------------------------------------------

COMPACT = {'v': 'via', 'f': 'from', 't': 'to', 'i': 'call-id', 'm': 'contact',
           'l': 'content-length', 'c': 'content-type'}


@dataclass
class SipMessage:
    first: str
    headers: list[tuple[str, str]]
    body: bytes = b''

    @classmethod
    def parse(cls, data: bytes) -> 'SipMessage':
        head, _, body = data.partition(b'\r\n\r\n')
        lines = head.decode(errors='replace').split('\r\n')
        headers: list[tuple[str, str]] = []
        for line in lines[1:]:
            if line[:1] in (' ', '\t') and headers:
                name, value = headers[-1]
                headers[-1] = (name, value + ' ' + line.strip())
                continue
            name, _, value = line.partition(':')
            name = name.strip().lower()
            headers.append((COMPACT.get(name, name), value.strip()))
        message = cls(lines[0], headers)
        length = message.get('content-length')
        message.body = body[:int(length)] if length and length.isdigit() else body
        return message

    def get(self, name: str) -> str | None:
        name = name.lower()
        return next((v for n, v in self.headers if n == name), None)

    def all(self, name: str) -> list[str]:
        name = name.lower()
        return [v for n, v in self.headers if n == name]

    @property
    def is_response(self) -> bool:
        return self.first.startswith('SIP/2.0 ')

    @property
    def status(self) -> int:
        return int(self.first.split()[1])

    @property
    def method(self) -> str:
        if self.is_response:
            return (self.get('cseq') or ' ').split()[-1].upper()
        return self.first.split()[0].upper()

    @property
    def uri(self) -> str:
        return self.first.split()[1]

    @property
    def cseq(self) -> int:
        return int((self.get('cseq') or '0').split()[0])


def header_param(value: str, name: str) -> str | None:
    match = re.search(r';\s*' + re.escape(name) + r'=([^;>\s,]+)', value or '')
    return match.group(1) if match else None


def uri_of(value: str) -> str:
    match = re.search(r'<([^>]*)>', value)
    return match.group(1) if match else value.split(';')[0].strip()


def uri_user(uri: str) -> str:
    match = re.match(r'sips?:([^@;>]+)@', uri)
    return match.group(1) if match else ''


def digits(text: str) -> str:
    return ''.join(c for c in text if c.isdigit() or c in '*#')


def parse_challenge(value: str) -> dict[str, str]:
    _, _, params = value.partition(' ')
    return {k.lower(): v for k, v in re.findall(r'(\w+)="?([^",]*)"?', params)}


def digest_authorization(challenge: dict[str, str], method: str, uri: str,
                         username: str, password: str, cnonce: str | None = None,
                         nc: int = 1) -> str:
    realm, nonce = challenge.get('realm', ''), challenge.get('nonce', '')
    ha1 = hashlib.md5(f'{username}:{realm}:{password}'.encode()).hexdigest()
    ha2 = hashlib.md5(f'{method}:{uri}'.encode()).hexdigest()
    parts = [f'username="{username}"', f'realm="{realm}"', f'nonce="{nonce}"',
             f'uri="{uri}"', 'algorithm=MD5']
    qops = [q.strip() for q in challenge.get('qop', '').split(',') if q.strip()]
    if 'auth' in qops:
        cnonce = cnonce or f'{random.getrandbits(64):016x}'
        count = f'{nc:08x}'
        response = hashlib.md5(
            f'{ha1}:{nonce}:{count}:{cnonce}:auth:{ha2}'.encode()).hexdigest()
        parts += [f'response="{response}"', 'qop=auth', f'nc={count}',
                  f'cnonce="{cnonce}"']
    else:
        response = hashlib.md5(f'{ha1}:{nonce}:{ha2}'.encode()).hexdigest()
        parts.append(f'response="{response}"')
    if 'opaque' in challenge:
        parts.append(f'opaque="{challenge["opaque"]}"')
    return 'Digest ' + ', '.join(parts)


def parse_sdp(body: bytes) -> tuple[str, int, list[int]] | None:
    """(address, port, payload types) of the first audio stream."""
    address, port, formats = None, None, []
    for line in body.decode(errors='replace').splitlines():
        if line.startswith('c=IN IP4 ') and (address is None or port is not None):
            address = line.split()[2]
        elif line.startswith('m=audio ') and port is None:
            fields = line.split()
            port = int(fields[1])
            formats = [int(f) for f in fields[3:] if f.isdigit()]
    if address is None or port is None:
        return None
    return address, port, formats


def build_sdp(address: str, port: int, session: int, payload_type: int = 8) -> bytes:
    codec = 'PCMU' if payload_type == 0 else 'PCMA'
    return (f'v=0\r\no=diva {session} {session} IN IP4 {address}\r\ns=diva\r\n'
            f'c=IN IP4 {address}\r\nt=0 0\r\nm=audio {port} RTP/AVP {payload_type}\r\n'
            f'a=rtpmap:{payload_type} {codec}/8000\r\na=ptime:20\r\na=sendrecv\r\n').encode()


def isdn_reason_to_sip(reason: int) -> int:
    if reason >> 8 != 0x34:
        return 503
    cause = reason & 0x7F
    return {1: 404, 3: 404, 17: 486, 18: 480, 19: 480, 21: 603, 22: 410,
            27: 502, 28: 484, 31: 480, 34: 503, 38: 503, 41: 503, 42: 503,
            44: 503, 58: 503, 65: 488, 88: 488}.get(cause, 480)


def sip_status_to_reject(status: int) -> int:
    if status in (486, 600):
        return REJECT_BUSY
    if status >= 500:
        return REJECT_OUT_OF_ORDER
    return REJECT_NORMAL


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------

RTP_BLOCK = 160      # 20 ms of G.711


class JitterBuffer:
    """Byte FIFO between RTP arrival and the line's clock."""

    def __init__(self, prefill: int = 320, limit: int = 1600, silence: int = ALAW_SILENCE):
        self.prefill, self.limit, self.silence = prefill, limit, silence
        self.data = bytearray()
        self.playing = False
        self.underruns = self.overflows = 0

    def put(self, payload: bytes) -> None:
        self.data += payload
        if len(self.data) > self.limit:
            del self.data[:len(self.data) - self.prefill]
            self.overflows += 1
        if not self.playing and len(self.data) >= self.prefill:
            self.playing = True

    def take(self, size: int) -> bytes:
        if not self.playing:
            return bytes([self.silence]) * size
        chunk = bytes(self.data[:size])
        del self.data[:size]
        if len(chunk) < size:
            self.playing = False
            self.underruns += 1
            chunk += bytes([self.silence]) * (size - len(chunk))
        return chunk


def rtp_payload(packet: bytes) -> tuple[int, bytes] | None:
    if len(packet) < 12 or packet[0] >> 6 != 2:
        return None
    offset = 12 + 4 * (packet[0] & 0x0F)
    if packet[0] & 0x10:
        if len(packet) < offset + 4:
            return None
        offset += 4 + 4 * struct.unpack_from('!H', packet, offset + 2)[0]
    end = len(packet)
    if packet[0] & 0x20:
        end -= packet[-1]
    return packet[1] & 0x7F, packet[offset:end]


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------

@dataclass
class Call:
    call_id: str
    direction: str                      # 'to-isdn' (SIP caller) or 'to-sip'
    local_tag: str
    rtp: socket.socket
    rtp_port: int
    state: str = 'setup'
    plci: int | None = None
    ncci: int | None = None
    remote_rtp: tuple[str, int] | None = None
    invite: SipMessage | None = None    # the INVITE we received
    invite_source: tuple[str, int] | None = None
    local_hdr: str = ''
    remote_hdr: str = ''
    remote_target: str = ''
    route: list[str] = field(default_factory=list)
    local_cseq: int = 1
    invite_branch: str = ''
    invite_text: bytes = b''
    invite_cseq: int = 1
    auth_tried: bool = False
    provisional: bool = False
    alerted: bool = False
    answered: bool = False
    acked: bool = False
    cancel_pending: bool = False
    b3_requested: bool = False
    connect_ind_number: int = 0
    final_response: bytes = b''
    jitter: JitterBuffer = field(default_factory=JitterBuffer)
    to_rtp: bytearray = field(default_factory=bytearray)
    owed: int = 0
    outstanding: int = 0
    handle: int = 0
    seq: int = field(default_factory=lambda: random.getrandbits(16))
    timestamp: int = field(default_factory=lambda: random.getrandbits(32))
    ssrc: int = field(default_factory=lambda: random.getrandbits(32))
    marker: bool = True
    rx_blocks: dict[int, int] = field(default_factory=dict)
    started: float = field(default_factory=time.monotonic)


class Gateway:
    MAX_OUTSTANDING = 6

    def __init__(self, args, capi: Capi, sip: socket.socket, *, registrar: tuple[str, int],
                 advertise: str):
        self.args, self.capi, self.sip = args, capi, sip
        self.payload_type = 0 if getattr(args, 'law', 'pcma') == 'pcmu' else 8
        self.registrar = registrar
        self.advertise = advertise
        self.sip_port = sip.getsockname()[1]
        self.selector = selectors.DefaultSelector()
        self.selector.register(capi, selectors.EVENT_READ, self.on_capi)
        self.selector.register(sip, selectors.EVENT_READ, self.on_sip)
        self.calls: dict[str, Call] = {}
        self.by_plci: dict[int, Call] = {}
        self.pending_connect: dict[int, Call] = {}
        self.timers: list[tuple[float, int, object]] = []
        self.timer_ids = itertools.count()
        self.register_call_id = f'diva-reg-{random.getrandbits(64):016x}'
        self.register_cseq = 0
        self.register_tag = self.tag()
        self.registered = False
        self.register_auth_tried = False
        self.rtp_ports = iter(range(args.rtp_first, args.rtp_last + 1, 2))
        self.running = True

    # -- utilities --------------------------------------------------------

    @staticmethod
    def tag() -> str:
        return f'{random.getrandbits(48):012x}'

    @staticmethod
    def branch() -> str:
        return f'z9hG4bK{random.getrandbits(64):016x}'

    def later(self, delay: float, fn) -> None:
        heapq.heappush(self.timers, (time.monotonic() + delay, next(self.timer_ids), fn))

    @property
    def domain(self) -> str:
        host, port = self.registrar
        return host if port == 5060 else f'{host}:{port}'

    @property
    def contact(self) -> str:
        return f'<sip:{self.args.username}@{self.advertise}:{self.sip_port}>'

    def via(self, branch: str) -> str:
        return f'SIP/2.0/UDP {self.advertise}:{self.sip_port};branch={branch};rport'

    def send_sip(self, data: bytes, address: tuple[str, int] | None = None) -> None:
        self.sip.sendto(data, address or self.registrar)

    def compose(self, first: str, headers: list[str], body: bytes = b'',
                content_type: str | None = None) -> bytes:
        lines = [first] + headers
        if body:
            lines.append(f'Content-Type: {content_type or "application/sdp"}')
        lines += ['User-Agent: diva-sip-gateway', f'Content-Length: {len(body)}', '', '']
        return '\r\n'.join(lines).encode() + body

    def open_rtp(self) -> tuple[socket.socket, int]:
        for port in self.rtp_ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.bind((self.args.bind, port))
            except OSError:
                sock.close()
                continue
            sock.setblocking(False)
            return sock, port
        self.rtp_ports = iter(range(self.args.rtp_first, self.args.rtp_last + 1, 2))
        raise RuntimeError('no free RTP port')

    def new_call(self, call_id: str, direction: str) -> Call:
        sock, port = self.open_rtp()
        call = Call(call_id, direction, self.tag(), sock, port)
        call.jitter.silence = 0xFF if self.payload_type == 0 else ALAW_SILENCE
        self.selector.register(sock, selectors.EVENT_READ, lambda c=call: self.on_rtp(c))
        self.calls[call_id] = call
        return call

    def finish(self, call: Call, why: str) -> None:
        if call.state == 'gone':
            return
        call.state = 'gone'
        held = time.monotonic() - call.started
        LOG.info('call %s ended (%s) after %.1f s; underruns %d overflows %d; '
                 'B-channel block sizes %s', call.call_id[:12], why, held,
                 call.jitter.underruns, call.jitter.overflows,
                 dict(sorted(call.rx_blocks.items())) or '-')
        try:
            self.selector.unregister(call.rtp)
        except (KeyError, ValueError):
            pass
        call.rtp.close()
        if call.plci is not None:
            self.by_plci.pop(call.plci, None)
        # Keep the record briefly so retransmissions are absorbed.
        self.later(32, lambda: self.calls.pop(call.call_id, None))

    # -- registration -----------------------------------------------------

    def send_register(self, challenge: SipMessage | None = None) -> None:
        self.register_cseq += 1
        uri = f'sip:{self.domain}'
        headers = [
            f'Via: {self.via(self.branch())}', 'Max-Forwards: 70',
            f'From: <sip:{self.args.username}@{self.domain}>;tag={self.register_tag}',
            f'To: <sip:{self.args.username}@{self.domain}>',
            f'Call-ID: {self.register_call_id}', f'CSeq: {self.register_cseq} REGISTER',
            f'Contact: {self.contact}', f'Expires: {self.args.expires}']
        if challenge is not None:
            name = 'WWW-Authenticate' if challenge.status == 401 else 'Proxy-Authenticate'
            value = challenge.get(name.lower()) or ''
            header = 'Authorization' if challenge.status == 401 else 'Proxy-Authorization'
            headers.append(f'{header}: ' + digest_authorization(
                parse_challenge(value), 'REGISTER', uri, self.args.username,
                self.args.password))
        self.send_sip(self.compose(f'REGISTER {uri} SIP/2.0', headers))

    def on_register_response(self, message: SipMessage) -> None:
        if message.status in (401, 407) and not self.register_auth_tried:
            self.register_auth_tried = True
            self.send_register(message)
        elif 200 <= message.status < 300:
            expires = header_param(message.get('contact') or '', 'expires')
            seconds = int(expires) if expires and expires.isdigit() else self.args.expires
            if not self.registered:
                LOG.info('registered %s@%s for %d s', self.args.username, self.domain, seconds)
            self.registered = True
            self.register_auth_tried = False
            self.later(max(30, seconds * 0.8), self.send_register)
        elif message.status >= 300:
            LOG.warning('registration refused: %s', message.first)
            self.registered = False
            self.register_auth_tried = False
            self.later(30, self.send_register)

    # -- SIP dispatch -----------------------------------------------------

    def on_sip(self) -> None:
        try:
            data, source = self.sip.recvfrom(65535)
        except BlockingIOError:
            return
        if not data.strip():
            return
        try:
            message = SipMessage.parse(data)
        except (ValueError, IndexError):
            LOG.debug('unparseable SIP from %s', source)
            return
        if message.is_response:
            if message.get('call-id') == self.register_call_id:
                self.on_register_response(message)
                return
            call = self.calls.get(message.get('call-id') or '')
            if call is not None:
                self.on_response(call, message)
            return
        method = message.method
        call = self.calls.get(message.get('call-id') or '')
        if method == 'OPTIONS':
            self.respond(message, source, 200, 'OK', extra=['Allow: INVITE, ACK, CANCEL, BYE, OPTIONS'])
        elif method == 'INVITE' and call is None:
            self.on_invite(message, source)
        elif method == 'INVITE':
            if call.final_response:
                self.send_sip(call.final_response, source)
            elif call.direction == 'to-isdn':
                self.respond(message, source, 100, 'Trying')
            else:
                self.respond(message, source, 488, 'Not Acceptable Here', to_tag=call.local_tag)
        elif method == 'ACK':
            if call is not None:
                call.acked = True
        elif method == 'CANCEL':
            self.on_cancel(call, message, source)
        elif method == 'BYE':
            if call is None:
                self.respond(message, source, 481, 'Call/Transaction Does Not Exist')
                return
            self.respond(message, source, 200, 'OK')
            self.hangup_isdn(call)
            self.finish(call, 'SIP BYE')
        else:
            self.respond(message, source, 405 if call is None else 200,
                         'Method Not Allowed' if call is None else 'OK')

    def respond(self, request: SipMessage, source, code: int, reason: str, *,
                to_tag: str | None = None, extra: list[str] = (), body: bytes = b'') -> bytes:
        headers = [f'Via: {v}' for v in request.all('via')]
        to = request.get('to') or ''
        if to_tag and 'tag=' not in to:
            to += f';tag={to_tag}'
        headers += [f'From: {request.get("from")}', f'To: {to}',
                    f'Call-ID: {request.get("call-id")}', f'CSeq: {request.get("cseq")}']
        if request.method == 'INVITE' and 100 < code < 300:
            headers += [f'Record-Route: {r}' for r in request.all('record-route')]
            headers.append(f'Contact: {self.contact}')
        headers += list(extra)
        data = self.compose(f'SIP/2.0 {code} {reason}', headers, body)
        self.send_sip(data, source)
        return data

    # -- SIP caller -> ISDN ---------------------------------------------

    def on_invite(self, invite: SipMessage, source) -> None:
        call_id = invite.get('call-id') or ''
        sdp = parse_sdp(invite.body)
        if sdp is None or self.payload_type not in sdp[2]:
            self.respond(invite, source, 488, 'Not Acceptable Here', to_tag=self.tag())
            return
        if sum(c.state != 'gone' for c in self.calls.values()) >= self.args.max_calls:
            self.respond(invite, source, 486, 'Busy Here', to_tag=self.tag())
            return
        try:
            call = self.new_call(call_id, 'to-isdn')
        except RuntimeError:
            self.respond(invite, source, 503, 'Service Unavailable', to_tag=self.tag())
            return
        call.invite, call.invite_source = invite, source
        call.remote_rtp = (sdp[0], sdp[1])
        call.remote_hdr = invite.get('from') or ''
        call.local_hdr = f'{invite.get("to")};tag={call.local_tag}'
        call.remote_target = uri_of(invite.get('contact') or invite.get('from') or '')
        call.route = invite.all('record-route')
        self.respond(invite, source, 100, 'Trying')

        user = digits(uri_user(invite.uri))
        called = self.args.isdn_number if user in ('', self.args.username) else user
        calling = digits(uri_user(uri_of(call.remote_hdr)))
        number = self.capi.send(CONNECT, REQ, connect_req(
            self.args.controller, self.args.cip, called, calling))
        self.pending_connect[number] = call
        LOG.info('SIP %s -> ISDN %s on controller %d (CIP %d)', calling or '?',
                 called or '(no number)', self.args.controller, self.args.cip)

    def final(self, call: Call, code: int, reason: str, body: bytes = b'') -> None:
        call.final_response = self.respond(call.invite, call.invite_source, code, reason,
                                           to_tag=call.local_tag, body=body)
        if code < 300:
            self.retransmit_ok(call, 0.5, 0.0)

    def retransmit_ok(self, call: Call, interval: float, elapsed: float) -> None:
        """Resend a 2xx until ACKed: T1 doubling to T2, for 64*T1 (RFC 3261 13.3.1.4)."""
        def again():
            if call.acked or call.state == 'gone' or elapsed + interval >= 32:
                return
            self.send_sip(call.final_response, call.invite_source)
            self.retransmit_ok(call, min(interval * 2, 4.0), elapsed + interval)
        self.later(interval, again)

    def on_cancel(self, call: Call | None, message: SipMessage, source) -> None:
        if call is None:
            self.respond(message, source, 481, 'Call/Transaction Does Not Exist')
            return
        self.respond(message, source, 200, 'OK')
        if call.direction == 'to-isdn' and not call.answered and call.state != 'gone':
            self.final(call, 487, 'Request Terminated')
            self.hangup_isdn(call)
            self.finish(call, 'SIP CANCEL')

    def bye(self, call: Call) -> None:
        call.local_cseq += 1
        headers = [f'Via: {self.via(self.branch())}', 'Max-Forwards: 70']
        headers += [f'Route: {r}' for r in call.route]
        headers += [f'From: {call.local_hdr}', f'To: {call.remote_hdr}',
                    f'Call-ID: {call.call_id}', f'CSeq: {call.local_cseq} BYE']
        # An incoming dialog can be direct, rather than through our registrar.
        peer = call.invite_source if call.direction == 'to-isdn' else None
        self.send_sip(self.compose(f'BYE {call.remote_target} SIP/2.0', headers), peer)

    # -- ISDN caller -> SIP ---------------------------------------------

    def on_connect_ind(self, message: CapiMessage) -> None:
        r = message.reader()
        plci, cip = r.take('<I'), r.take('<H')
        called, calling = called_digits(r.struct()), calling_digits(r.struct())
        target = called or self.args.default_extension
        busy = sum(c.state != 'gone' for c in self.calls.values()) >= self.args.max_calls
        LOG.info('ISDN %s -> SIP %s (CIP %d, PLCI %#x)', calling or '?', target or '?', cip, plci)
        if not target or busy or not self.registered:
            reject = REJECT_BUSY if busy else REJECT_NORMAL
            self.capi.send(CONNECT, RESP, connect_resp(plci, reject), message.number)
            return
        call = self.new_call(f'diva-{random.getrandbits(64):016x}', 'to-sip')
        call.plci = plci
        call.connect_ind_number = message.number
        self.by_plci[plci] = call
        # Alert at once: the caller's T303 is 4 s and SIP setup can outlast it.
        call.alerted = True
        self.capi.send(ALERT, REQ, struct.pack('<I', plci) + cstruct())
        user = calling or self.args.username
        call.local_hdr = f'<sip:{user}@{self.domain}>;tag={call.local_tag}'
        call.remote_hdr = f'<sip:{target}@{self.domain}>'
        call.remote_target = f'sip:{target}@{self.domain}'
        self.send_invite(call)

    def send_invite(self, call: Call, challenge: SipMessage | None = None) -> None:
        call.invite_branch = self.branch()
        sdp = build_sdp(self.advertise, call.rtp_port, random.getrandbits(31), self.payload_type)
        headers = [f'Via: {self.via(call.invite_branch)}', 'Max-Forwards: 70',
                   f'From: {call.local_hdr}', f'To: {call.remote_hdr}',
                   f'Call-ID: {call.call_id}', f'CSeq: {call.invite_cseq} INVITE',
                   f'Contact: {self.contact}']
        if challenge is not None:
            name = 'WWW-Authenticate' if challenge.status == 401 else 'Proxy-Authenticate'
            header = 'Authorization' if challenge.status == 401 else 'Proxy-Authorization'
            headers.append(f'{header}: ' + digest_authorization(
                parse_challenge(challenge.get(name.lower()) or ''), 'INVITE',
                call.remote_target, self.args.username, self.args.password))
        call.invite_text = self.compose(f'INVITE {call.remote_target} SIP/2.0', headers, sdp)
        self.send_sip(call.invite_text)
        self.retransmit_invite(call, call.invite_text, 0.5)

    def retransmit_invite(self, call: Call, text: bytes, interval: float) -> None:
        def again():
            if call.provisional or call.state == 'gone' or call.invite_text is not text:
                return
            if interval > 16:
                LOG.warning('INVITE for %s timed out', call.call_id[:12])
                self.reject_isdn(call, REJECT_OUT_OF_ORDER)
                self.finish(call, 'INVITE timeout')
                return
            self.send_sip(text)
            self.retransmit_invite(call, text, interval * 2)
        self.later(interval, again)

    def ack(self, call: Call, response: SipMessage) -> None:
        if response.status < 300:
            target = call.remote_target
            headers = [f'Via: {self.via(self.branch())}', 'Max-Forwards: 70']
            headers += [f'Route: {r}' for r in call.route]
        else:
            target = uri_of(call.remote_hdr)
            headers = [f'Via: {self.via(call.invite_branch)}', 'Max-Forwards: 70']
        headers += [f'From: {call.local_hdr}', f'To: {response.get("to")}',
                    f'Call-ID: {call.call_id}', f'CSeq: {response.cseq} ACK']
        self.send_sip(self.compose(f'ACK {target} SIP/2.0', headers))

    def cancel(self, call: Call) -> None:
        headers = [f'Via: {self.via(call.invite_branch)}', 'Max-Forwards: 70',
                   f'From: {call.local_hdr}', f'To: {call.remote_hdr}',
                   f'Call-ID: {call.call_id}', f'CSeq: {call.invite_cseq} CANCEL']
        self.send_sip(self.compose(f'CANCEL {call.remote_target} SIP/2.0', headers))

    def on_response(self, call: Call, message: SipMessage) -> None:
        if call.direction != 'to-sip':
            return
        if message.method == 'BYE' or message.method == 'CANCEL':
            return
        if message.method != 'INVITE' or message.cseq != call.invite_cseq:
            return
        status = message.status
        if status < 200:
            call.provisional = True
            if call.cancel_pending:
                self.cancel(call)
            return
        if status in (401, 407) and not call.auth_tried and call.state != 'gone':
            call.auth_tried = True
            self.ack(call, message)
            call.invite_cseq += 1
            call.local_cseq = call.invite_cseq
            self.send_invite(call, message)
            return
        if status >= 300:
            self.ack(call, message)
            if call.state != 'gone':
                LOG.info('SIP refused %s: %s', call.call_id[:12], message.first)
                self.reject_isdn(call, sip_status_to_reject(status))
                self.finish(call, message.first)
            return
        # 2xx, possibly a retransmission.
        first = not call.answered
        if first:
            call.answered = True
            call.remote_hdr = message.get('to') or call.remote_hdr
            call.remote_target = uri_of(message.get('contact') or '') or call.remote_target
            call.route = list(reversed(message.all('record-route')))
            call.local_cseq = call.invite_cseq
        self.ack(call, message)
        if not first:
            return
        if call.state == 'gone' or call.cancel_pending:
            self.bye(call)
            return
        sdp = parse_sdp(message.body)
        if sdp is None or self.payload_type not in sdp[2]:
            LOG.warning('answer without configured G.711 law: %r', message.body[:200])
            self.bye(call)
            self.reject_isdn(call, REJECT_NORMAL)
            self.finish(call, 'no PCMA in answer')
            return
        call.remote_rtp = (sdp[0], sdp[1])
        self.capi.send(CONNECT, RESP, connect_resp(call.plci, 0), call.connect_ind_number)
        call.state = 'connecting'

    def reject_isdn(self, call: Call, reject: int) -> None:
        if call.plci is None or call.state == 'gone':
            return
        if call.direction == 'to-sip' and not call.answered:
            self.capi.send(CONNECT, RESP, connect_resp(call.plci, reject), call.connect_ind_number)
        else:
            self.hangup_isdn(call)

    # -- CAPI dispatch ----------------------------------------------------

    def hangup_isdn(self, call: Call) -> None:
        if call.plci is not None and call.state != 'gone':
            self.capi.send(DISCONNECT, REQ, struct.pack('<I', call.plci) + cstruct())

    def on_capi(self) -> None:
        while True:
            message = self.capi.receive()
            if message is None:
                return
            try:
                self.dispatch_capi(message)
            except Exception:
                LOG.exception('CAPI message %02x/%02x', message.command, message.sub)

    def dispatch_capi(self, m: CapiMessage) -> None:
        kind = (m.command, m.sub)
        r = m.reader()
        if kind == (DATA_B3, IND):
            ncci, handle, payload = m.data_b3()
            self.capi.send(DATA_B3, RESP, struct.pack('<IH', ncci, handle), m.number)
            call = self.by_plci.get(ncci & 0xFFFF)
            if call is not None and call.state == 'active':
                self.line_to_rtp(call, payload)
            return
        if kind == (DATA_B3, CONF):
            ncci, _, info = r.take('<IHH')
            call = self.by_plci.get(ncci & 0xFFFF)
            if call is not None:
                call.outstanding = max(0, call.outstanding - 1)
                if info:
                    LOG.debug('DATA_B3_CONF info %#x', info)
                self.pump(call)
            return
        if kind == (LISTEN, CONF):
            controller, info = r.take('<IH')
            if info:
                LOG.error('LISTEN on controller %d refused: info %#x', controller, info)
            else:
                LOG.info('listening on controller %d', controller)
            return
        if kind == (CONNECT, CONF):
            plci, info = r.take('<IH')
            call = self.pending_connect.pop(m.number, None)
            if call is None:
                return
            if info:
                LOG.warning('CONNECT_REQ refused: info %#x', info)
                if call.state != 'gone':
                    self.final(call, 503, 'Service Unavailable')
                    self.finish(call, f'CONNECT_CONF {info:#x}')
                return
            call.plci = plci
            self.by_plci[plci] = call
            if call.state == 'gone':
                self.capi.send(DISCONNECT, REQ, struct.pack('<I', plci) + cstruct())
            return
        if kind == (CONNECT, IND):
            self.on_connect_ind(m)
            return
        if kind == (INFO, IND):
            ident, number = r.take('<IH')
            element = r.struct()
            self.capi.send(INFO, RESP, struct.pack('<I', ident), m.number)
            call = self.by_plci.get(ident & 0xFFFF)
            LOG.debug('INFO_IND %#x number %#06x %s', ident, number, element.hex())
            if (call is not None and call.direction == 'to-isdn' and number == INFO_ALERTING
                    and not call.alerted and call.state != 'gone'):
                call.alerted = True
                self.respond(call.invite, call.invite_source, 180, 'Ringing', to_tag=call.local_tag)
            return
        if kind == (CONNECT_ACTIVE, IND):
            plci = r.take('<I')
            self.capi.send(CONNECT_ACTIVE, RESP, struct.pack('<I', plci), m.number)
            call = self.by_plci.get(plci)
            if call is None:
                return
            if call.direction == 'to-isdn':
                self.request_b3(call)
            else:
                # The passive side of a transparent bearer may or may not be
                # offered CONNECT_B3_IND; ask for B3 ourselves if it is not.
                self.later(0.3, lambda: self.request_b3(call))
            return
        if kind == (CONNECT_B3, IND):
            ncci = r.take('<I')
            call = self.by_plci.get(ncci & 0xFFFF)
            self.capi.send(CONNECT_B3, RESP, struct.pack('<IH', ncci, 0 if call else 2) + cstruct(),
                           m.number)
            if call is not None:
                call.b3_requested = True
            return
        if kind == (CONNECT_B3, CONF):
            ncci, info = r.take('<IH')
            if info:
                LOG.warning('CONNECT_B3_REQ refused: info %#x', info)
            return
        if kind == (CONNECT_B3_ACTIVE, IND):
            ncci = r.take('<I')
            self.capi.send(CONNECT_B3_ACTIVE, RESP, struct.pack('<I', ncci), m.number)
            call = self.by_plci.get(ncci & 0xFFFF)
            if call is not None and call.state != 'gone':
                self.media_up(call, ncci)
            return
        if kind == (DISCONNECT_B3, IND):
            ncci = r.take('<I')
            self.capi.send(DISCONNECT_B3, RESP, struct.pack('<I', ncci), m.number)
            call = self.by_plci.get(ncci & 0xFFFF)
            if call is not None:
                call.ncci = None
            return
        if kind == (DISCONNECT, IND):
            plci, reason = r.take('<IH')
            self.capi.send(DISCONNECT, RESP, struct.pack('<I', plci), m.number)
            call = self.by_plci.pop(plci, None)
            if call is not None and call.state != 'gone':
                self.isdn_gone(call, reason)
            return
        if kind == (FACILITY, IND):
            plci, selector = r.take('<IH')
            self.capi.send(FACILITY, RESP, struct.pack('<IH', plci, selector) + cstruct(),
                           m.number)
            return
        if kind == (MANUFACTURER, IND):
            controller, manu = r.take('<II')
            self.capi.send(MANUFACTURER, RESP, struct.pack('<II', controller, manu), m.number)
            return
        if m.sub == CONF:
            ident, info = r.take('<IH') if len(m.raw) >= 14 else (0, 0)
            if info:
                LOG.warning('CAPI %02x confirmation info %#x', m.command, info)
            return
        LOG.debug('unhandled CAPI %02x/%02x', m.command, m.sub)

    def request_b3(self, call: Call) -> None:
        if call.b3_requested or call.state == 'gone' or call.plci is None:
            return
        call.b3_requested = True
        self.capi.send(CONNECT_B3, REQ, struct.pack('<I', call.plci) + cstruct())

    def isdn_gone(self, call: Call, reason: int) -> None:
        call.plci = None
        LOG.info('ISDN cleared %s: reason %#06x', call.call_id[:12], reason)
        if call.direction == 'to-isdn':
            if call.final_response and call.answered:
                self.bye(call)
            elif not call.final_response:
                code = isdn_reason_to_sip(reason)
                reasons = {404: 'Not Found', 410: 'Gone', 480: 'Temporarily Unavailable',
                           484: 'Address Incomplete', 486: 'Busy Here', 488: 'Not Acceptable Here',
                           502: 'Bad Gateway', 503: 'Service Unavailable', 603: 'Decline'}
                self.final(call, code, reasons.get(code, 'Unavailable'))
        else:
            if call.answered:
                self.bye(call)
            elif call.provisional:
                self.cancel(call)
            else:
                call.cancel_pending = True
        self.finish(call, f'ISDN reason {reason:#06x}')

    # -- media ------------------------------------------------------------

    def media_up(self, call: Call, ncci: int) -> None:
        call.ncci = ncci
        call.state = 'active'
        call.owed = 2 * RTP_BLOCK
        LOG.info('B channel up for %s (NCCI %#x), RTP %s <-> :%d', call.call_id[:12], ncci,
                 call.remote_rtp, call.rtp_port)
        if call.direction == 'to-isdn' and not call.answered:
            call.answered = True
            self.final(call, 200, 'OK', build_sdp(self.advertise, call.rtp_port,
                                                  random.getrandbits(31), self.payload_type))
        self.pump(call)

    def line_to_rtp(self, call: Call, payload: bytes) -> None:
        call.rx_blocks[len(payload)] = call.rx_blocks.get(len(payload), 0) + 1
        audio = payload.translate(BIT_REVERSE) if self.args.bit_reverse else payload
        call.to_rtp += audio
        while len(call.to_rtp) >= RTP_BLOCK and call.remote_rtp:
            chunk = bytes(call.to_rtp[:RTP_BLOCK])
            del call.to_rtp[:RTP_BLOCK]
            header = struct.pack('!BBHII', 0x80, (0x80 if call.marker else 0) | self.payload_type,
                                 call.seq, call.timestamp, call.ssrc)
            call.marker = False
            call.seq = (call.seq + 1) & 0xFFFF
            call.timestamp = (call.timestamp + RTP_BLOCK) & 0xFFFFFFFF
            try:
                call.rtp.sendto(header + chunk, call.remote_rtp)
            except OSError as error:
                LOG.debug('RTP send: %s', error)
        call.owed += len(payload)
        self.pump(call)

    def pump(self, call: Call) -> None:
        if call.state != 'active' or call.ncci is None:
            return
        while call.owed >= RTP_BLOCK and call.outstanding < self.MAX_OUTSTANDING:
            audio = call.jitter.take(RTP_BLOCK)
            line = audio.translate(BIT_REVERSE) if self.args.bit_reverse else audio
            call.handle = (call.handle + 1) & 0xFFFF
            try:
                self.capi.send(DATA_B3, REQ, data_b3_req(call.ncci, line, call.handle))
            except OSError as error:
                LOG.debug('DATA_B3_REQ: %s', error)
                return
            call.owed -= RTP_BLOCK
            call.outstanding += 1
        if call.owed > 8 * RTP_BLOCK:
            call.owed = 2 * RTP_BLOCK

    def on_rtp(self, call: Call) -> None:
        try:
            packet, _ = call.rtp.recvfrom(4096)
        except (BlockingIOError, OSError):
            return
        parsed = rtp_payload(packet)
        if parsed is None or parsed[0] != self.payload_type:
            return
        call.jitter.put(parsed[1])

    # -- main loop --------------------------------------------------------

    def run(self) -> None:
        self.capi.send(LISTEN, REQ, listen_req(self.args.controller))
        self.send_register()
        while self.running:
            timeout = 1.0
            if self.timers:
                timeout = max(0.0, min(timeout, self.timers[0][0] - time.monotonic()))
            for key, _ in self.selector.select(timeout):
                key.data()
            now = time.monotonic()
            while self.timers and self.timers[0][0] <= now:
                _, _, fn = heapq.heappop(self.timers)
                fn()


def local_address_for(peer: tuple[str, int]) -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(peer)
        return probe.getsockname()[0]
    finally:
        probe.close()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--registrar', required=True, help='Asterisk host[:port]')
    ap.add_argument('--username', required=True, help='SIP extension to register')
    ap.add_argument('--password-file', required=True)
    ap.add_argument('--controller', type=int, default=3, help='CAPI controller (default 3)')
    ap.add_argument('--cip', type=int, default=4,
                    help='CIP for calls to ISDN: 1 speech, 4 3.1 kHz audio (default)')
    ap.add_argument('--law', choices=('pcma', 'pcmu'), default='pcma',
                    help='actual B-channel law; no transcoding is performed (default pcma)')
    ap.add_argument('--isdn-number', default='',
                    help='called number when the SIP request names only the gateway')
    ap.add_argument('--default-extension', default='',
                    help='SIP target for ISDN calls that carry no called number')
    ap.add_argument('--bind', default='0.0.0.0')
    ap.add_argument('--sip-port', type=int, default=5060)
    ap.add_argument('--advertise', help='address placed in Contact and SDP')
    ap.add_argument('--rtp-first', type=int, default=40000)
    ap.add_argument('--rtp-last', type=int, default=40198)
    ap.add_argument('--expires', type=int, default=300)
    ap.add_argument('--max-calls', type=int, default=2)
    ap.add_argument('--no-bit-reverse', dest='bit_reverse', action='store_false',
                    help='pass B-channel octets without reversing their bit order')
    ap.add_argument('--capi', default='/dev/capi20')
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    with open(args.password_file) as handle:
        args.password = handle.read().strip()
    host, _, port = args.registrar.partition(':')
    registrar = (socket.gethostbyname(host), int(port or 5060))
    advertise = args.advertise or local_address_for(registrar)
    sip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sip.bind((args.bind, args.sip_port))
    sip.setblocking(False)
    capi = Capi(args.capi)
    LOG.info('gateway %s on controller %d, SIP %s:%d -> %s:%d', args.username,
             args.controller, advertise, args.sip_port, *registrar)
    Gateway(args, capi, sip, registrar=registrar, advertise=advertise).run()


if __name__ == '__main__':
    main()
