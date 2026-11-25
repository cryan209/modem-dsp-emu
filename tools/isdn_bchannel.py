#!/usr/bin/env python3
"""Host ISDN B-channel adapters. Wire octets contain oldest bit in bit zero.

This backend models a clear 8,000-octet/s bearer; it never invokes an audio
codec or DSP. It is separate from original MIPS-firmware execution. The
implemented subset is fixed-profile, one logical link, NRZ, 16-bit HDLC FCS,
V.110 9.6/19.2/38.4k (8N1 asynchronous), and acknowledged V.120/X.75.
"""
from collections import deque

from eicon_idi import IsdnOptions
from v42_lapm import (FLAG_BITS, HdlcDecoder, encode_frame, octets_to_bits,
                      bits_to_octets, XidParameters, encode_xid_parameters,
                      parse_xid_parameters, HDLC_OPTIONAL_FUNCTIONS)
from v42bis import V42bisEncoder, V42bisDecoder, V42bisParameters


class IsdnLink:
    """Single-link LAPB (X.75) / Q.922 (V.120) acknowledged transfer."""
    def __init__(self, profile, calling, emit, deliver, max_frame=2048):
        self.profile, self.calling = profile, calling
        self.emit, self.deliver = emit, deliver
        self.v120 = profile in (6, 7)
        self.modulus = 128 if self.v120 else 8
        self.max_frame = min(max_frame, 256)
        self.state = 'idle'
        self.pending = deque()
        self.unacked = deque()
        self.original_unacked = {}
        self.va = self.vs = self.vr = 0
        self.now = self.deadline = 0.0
        self.retries = self.retransmits = self.invalid = 0
        self.peer_busy = False
        self.compression = False
        self.encoder = self.decoder = None
        self.failed = None

    @property
    def connected(self):
        return self.state == 'up'

    def address(self, command):
        if self.v120:
            # V.120 Figure 6 and Table 4: LLI=256; C/R is symmetric,
            # zero for commands and one for responses in both directions.
            return bytes((0x08 | (0 if command else 2), 0x01))
        return bytes((0x01 if command == self.calling else 0x03,))

    def send(self, control, information=b'', command=True):
        self.emit(self.address(command) + bytes(control) + information)

    def xid(self, compression):
        # ISO 8885 XID parameter format, common to the HDLC family.
        params = XidParameters(n401_tx=self.max_frame, n401_rx=self.max_frame,
                               k_tx=7, k_rx=7,
                               optional_functions=HDLC_OPTIONAL_FUNCTIONS & ~(1 << 8),
                               v42bis=V42bisParameters() if compression else None)
        return encode_xid_parameters(params)

    def start(self):
        if not self.calling or self.state != 'idle':
            return
        self.state = 'xid' if self.profile == 15 else 'establish'
        if self.state == 'xid':
            self.send((0xBF,), self.xid(True))
        else:
            self.establish()
        self.deadline = self.now + 1.0

    def establish(self):
        self.state = 'establish'
        self.send((0x7F if self.v120 else 0x3F,))  # SABME(P) / SABM(P)
        self.deadline = self.now + 1.0

    def enable_compression(self, peer):
        params = peer.v42bis if peer is not None else None
        self.compression = params is not None and params.directions == 3
        if self.compression:
            words, size = min(512, params.codewords), min(32, params.max_string)
            self.encoder, self.decoder = V42bisEncoder(words, size), V42bisDecoder(words, size)

    def queue(self, data):
        if self.state in ('failed', 'closed'):
            raise ConnectionError('ISDN link is closed')
        if len(data) > 65536 or sum(map(len, self.pending)) + len(data) > 65536:
            raise BufferError('ISDN transmit queue full')
        # Leave space for TA header or worst-case transparent-mode escape
        # expansion before compression becomes profitable.
        chunk_size = ((self.max_frame - 16) // 2 if self.profile == 15
                      else self.max_frame - int(self.v120))
        for offset in range(0, len(data), chunk_size):
            self.pending.append(data[offset:offset + chunk_size])
        self.pump()

    def iframe(self, ns, data):
        control = ((ns << 1), (self.vr << 1)) if self.modulus == 128 else (
            (ns << 1) | (self.vr << 5),)
        self.send(control, data)

    def pump(self):
        while self.connected and not self.peer_busy and self.pending and len(self.unacked) < 7:
            data = self.pending.popleft()
            original = data
            if self.compression:
                data = self.encoder.feed(data) + self.encoder.flush()
            if self.v120:
                data = b'\x83' + data  # asynchronous TA header: E,B,F=1
            self.iframe(self.vs, data)
            self.unacked.append((self.vs, data))
            self.original_unacked[self.vs] = original
            self.vs = (self.vs + 1) % self.modulus
            if len(self.unacked) == 1:
                self.deadline = self.now + 1.0

    def ack(self, nr):
        count = (nr - self.va) % self.modulus
        if count > len(self.unacked):
            self.invalid += 1
            return False
        for _ in range(count):
            ns, _ = self.unacked.popleft()
            self.original_unacked.pop(ns, None)
        self.va = nr
        if count:
            self.retries = 0
            self.deadline = self.now + 1.0 if self.unacked else 0.0
        return True

    def supervisory(self, kind=1, final=False, command=False):
        control = (kind, (self.vr << 1) | int(final)) if self.modulus == 128 else (
            kind | (self.vr << 5) | (0x10 if final else 0),)
        self.send(control, command=command)

    def retransmit(self):
        if not self.unacked:
            return
        for ns, data in self.unacked:
            self.iframe(ns, data)
            self.retransmits += 1
        self.deadline = self.now + 1.0

    def feed(self, frame):
        width = 2 if self.v120 else 1
        if len(frame) < width + 1:
            self.invalid += 1
            return
        address, control = frame[:width], frame[width]
        # Peer commands carry our response address; peer responses our command.
        command = address == self.address(True if self.v120 else False)
        if address not in (self.address(True), self.address(False)):
            self.invalid += 1
            return
        if control & 3 == 3:
            operation = control & ~0x10
            information = frame[width + 1:]
            if operation == 0xAF:  # XID
                peer = parse_xid_parameters(information)
                if command:
                    if peer is None:
                        # Unsupported identification formats (including
                        # CAPI20/X75) are not an ISO V.42bis negotiation.
                        # Do not fabricate a response in a different dialect;
                        # the subsequent SABM can establish plain X.75.
                        return
                    self.enable_compression(peer)
                    self.send((control,), self.xid(self.compression), command=False)
                elif self.state == 'xid':
                    self.enable_compression(peer)
                    self.establish()
            elif operation == (0x6F if self.v120 else 0x2F) and command:
                # A lost UA can cause establishment to repeat after this end
                # has already transmitted. Requeue the original octets, not
                # compressed fragments from the old dictionary/sequence.
                retry_data = [self.original_unacked[ns] for ns, _ in self.unacked]
                self.pending.extendleft(reversed(retry_data))
                if self.compression:
                    self.encoder.reset()
                    self.decoder.reset()
                self.va = self.vs = self.vr = 0
                self.unacked.clear()
                self.original_unacked.clear()
                self.state = 'up'
                self.send((0x63 | (control & 0x10),), command=False)
                self.deadline = 0.0
                self.pump()
            elif operation == 0x63 and not command and self.state == 'establish':
                self.state = 'up'
                self.deadline = 0.0
                self.retries = 0
                self.pump()
            elif operation == 0x43 and command:  # DISC
                self.send((0x63 | (control & 0x10),), command=False)
                self.state = 'closed'
            elif operation in (0x0F, 0x87):  # DM / FRMR
                self.state = 'failed'
                self.failed = f'peer rejected link: 0x{operation:02x}'
            return
        if not self.connected:
            return
        if self.modulus == 128:
            if len(frame) < width + 2:
                self.invalid += 1
                return
            nr, poll = frame[width + 1] >> 1, frame[width + 1] & 1
            information = frame[width + 2:]
        else:
            nr, poll = control >> 5, bool(control & 0x10)
            information = frame[width + 1:]
        if not self.ack(nr):
            return
        if not control & 1:
            ns = (control >> 1) & (self.modulus - 1)
            if ns == self.vr:
                if self.v120:
                    # The supported asynchronous mode requires B=F=1.
                    if not information or information[0] & 0x3F != 3:
                        self.invalid += 1
                        return
                    if information[0] & 0x80:
                        information = information[1:]
                    elif len(information) >= 2 and information[1] & 0x80:
                        information = information[2:]
                    else:
                        self.invalid += 1
                        return
                if self.compression:
                    information = self.decoder.feed(information)
                self.deliver(information)
                self.vr = (self.vr + 1) % self.modulus
                self.supervisory(final=bool(poll))
            else:
                # Duplicate or missing frame: reject from the next expected NS.
                self.supervisory(9, final=bool(poll))
        else:
            kind = control & 0x0F
            self.peer_busy = kind == 5  # RNR
            if kind == 9:
                self.retransmit()
            if command and poll:
                self.supervisory(final=True)
        self.pump()

    def tick(self, now):
        self.now = now
        if not self.deadline or now < self.deadline:
            self.pump()
            return
        if self.retries >= 5:
            self.state, self.failed = 'failed', 'ISDN link retry limit'
            self.deadline = 0.0
            return
        self.retries += 1
        if self.state == 'xid':
            # Compression negotiation is optional; retain queued raw data.
            self.enable_compression(None)
            self.establish()
        elif self.state == 'establish':
            self.establish()
        elif self.peer_busy:
            self.supervisory(final=True, command=True)
            self.deadline = now + 1.0
        elif self.unacked:
            self.retransmit()

    def close(self):
        if self.connected:
            self.send((0x53,))  # DISC(P)
        self.state = 'closed'
        self.pending.clear()
        self.unacked.clear()
        self.original_unacked.clear()
        self.deadline = 0.0


class V110Adapter:
    """V.110 80-bit frame at 9.6/19.2/38.4k; RA2 fill, async 8N1."""
    DATA_ROWS = (1, 2, 3, 4, 6, 7, 8, 9)

    def __init__(self, rate=38400, asynchronous=False):
        self.width = {9600: 2, 19200: 4, 38400: 8}[rate]
        self.asynchronous = asynchronous
        self.tx = deque()
        self.rx = deque()
        self.stream = deque()
        self.characters = deque()
        self.framing_errors = self.frames = 0
        self.synced = False
        self.idle_bits = (1,)
        self.idle_phase = 0

    def queue(self, data):
        for octet in data:
            if self.asynchronous:
                self.tx.append(0)
            self.tx.extend((octet >> bit) & 1 for bit in range(8))
            if self.asynchronous:
                self.tx.append(1)

    def frame(self):
        # Table 6e: six D bits per data row, E1..E3=011, E4..E7=1111.
        # Frame rows are described in transmission order (MSB first), not
        # by the software bearer's oldest-bit-in-bit-zero representation.
        bits = [0] * 8
        for row in range(1, 10):
            if row == 5:
                bits.extend((1, 0, 1, 1, 1, 1, 1, 1))
            else:
                bits.append(1)
                for _ in range(6):
                    if self.tx:
                        bits.append(self.tx.popleft())
                        self.idle_phase = 0
                    else:
                        bits.append(self.idle_bits[self.idle_phase])
                        self.idle_phase = (self.idle_phase + 1) % len(self.idle_bits)
                bits.append(0)  # S/X ON, V.110 §2.1.2.3
        out = []
        for start in range(0, 80, self.width):
            slot = bits[start:start + self.width] + [1] * (8 - self.width)
            out.append(sum(bit << n for n, bit in enumerate(slot)))
        return bytes(out)

    def feed(self, data):
        for octet in data:
            self.stream.extend((octet >> bit) & 1 for bit in range(self.width))
        result = bytearray()
        while len(self.stream) >= 80:
            window = list(self.stream)[:80]
            if any(window[:8]) or any(window[row * 8] != 1 for row in range(1, 10)):
                self.stream.popleft()
                self.synced = False
                self.framing_errors += 1
                continue
            for _ in range(80):
                self.stream.popleft()
            self.frames += 1
            self.synced = True
            if window[40:48] != [1, 0, 1, 1, 1, 1, 1, 1]:
                self.framing_errors += 1
                continue
            data_bits = [window[row * 8 + bit] for row in self.DATA_ROWS for bit in range(1, 7)]
            if self.asynchronous:
                self.characters.extend(data_bits)
                while self.characters:
                    if self.characters[0]:
                        self.characters.popleft()
                    elif len(self.characters) < 10:
                        break
                    else:
                        char = [self.characters.popleft() for _ in range(10)]
                        if char[-1] != 1:
                            self.framing_errors += 1
                        else:
                            result.append(sum(char[n + 1] << n for n in range(8)))
            else:
                result.extend(bits_to_octets(data_bits))
        return bytes(result)


class NativeBChannel:
    """One fixed-profile native terminal adapter over clear B-channel octets."""
    def __init__(self, options, *, calling=True):
        self.options, self.calling = options, calling
        self.profile = options.profile
        self.tx_bits = deque()
        self.rx_data = bytearray()
        self.rx_frames = deque()
        self.hdlc = HdlcDecoder(minimum_octets=1 if self.profile in (9, 10) else 2)
        self.oversize_frames = 0
        self.closed = False
        self.idle_phase = 0
        self.tx_octets = self.rx_octets = 0
        self.tx_payload = self.rx_payload = 0
        self.link = None
        self.v110 = None
        self.raw = deque()
        self.idle_octet = 0xFF
        if self.profile in (2, 3):
            self.v110 = V110Adapter(options.user_rate, self.profile == 3)
            if self.profile == 2:
                self.v110.idle_bits = FLAG_BITS
        elif self.profile in (1, 6, 7, 15):
            self.link = IsdnLink(self.profile, calling, self.emit, self.deliver, options.max_frame)
            self.link.start()

    @property
    def connected(self):
        if self.closed:
            return False
        if self.v110 is not None:
            return self.v110.synced
        return self.link.connected if self.link is not None else True

    def emit(self, frame):
        # Finish an idle flag before opening a new frame. At phase seven,
        # its last zero would otherwise consume the opening flag's first
        # zero and make a short control frame disappear.
        if not self.tx_bits and self.idle_phase:
            self.tx_bits.extend(FLAG_BITS[self.idle_phase:])
            self.idle_phase = 0
        self.tx_bits.extend(encode_frame(frame))

    def deliver(self, data):
        self.rx_data.extend(data)
        self.rx_payload += len(data)

    def queue_data(self, data):
        if self.closed:
            raise ConnectionError('B-channel is closed')
        if not data:
            return
        queued = (len(self.v110.tx) // 8 if self.v110 is not None
                  else len(self.tx_bits) // 8 if self.profile in (9, 10) else len(self.raw))
        if len(data) > 65536 or queued + len(data) > 65536:
            raise BufferError('ISDN transmit queue full')
        if self.link is not None:
            self.link.queue(data)
        elif self.v110 is not None:
            if self.profile == 2:
                # B1_V110_s carries synchronous HDLC on the adapted D-bit
                # stream; V110Adapter itself remains the unframed RA1/RA2
                # primitive used by asynchronous mode and wire fixtures.
                if not self.v110.tx and self.v110.idle_phase:
                    self.v110.tx.extend(FLAG_BITS[self.v110.idle_phase:])
                    self.v110.idle_phase = 0
                for offset in range(0, len(data), self.options.max_frame):
                    self.v110.tx.extend(encode_frame(data[offset:offset + self.options.max_frame]))
            else:
                self.v110.queue(data)
        elif self.profile == 8:
            self.raw.extend(data)
        else:
            if len(data) > self.options.max_frame:
                raise ValueError('HDLC frame exceeds configured maximum')
            self.emit(data)
        self.tx_payload += len(data)

    def take_wire(self, count=160):
        if count < 0:
            raise ValueError('negative B-channel slot count')
        self.tx_octets += count
        if self.closed and self.link is None:
            return bytes((0xFF,)) * count
        if self.profile == 8:
            return bytes(self.raw.popleft() if self.raw else self.idle_octet for _ in range(count))
        if self.v110 is not None:
            while len(self.raw) < count:
                self.raw.extend(self.v110.frame())
            return bytes(self.raw.popleft() for _ in range(count))
        width = 7 if self.options.bearer_rate == 56000 else 8
        result = bytearray()
        for _ in range(count):
            value = 0
            for bit in range(width):
                if self.tx_bits:
                    b = self.tx_bits.popleft()
                    self.idle_phase = 0
                else:
                    b = FLAG_BITS[self.idle_phase]
                    self.idle_phase = (self.idle_phase + 1) % 8
                value |= b << bit
            result.append(value | (0x80 if width == 7 else 0))
        return bytes(result)

    def feed_wire(self, data):
        self.rx_octets += len(data)
        if self.closed:
            return
        if self.profile == 8:
            self.deliver(data)
            return
        if self.v110 is not None:
            adapted = self.v110.feed(data)
            if self.profile == 2:
                for frame in self.hdlc.feed(octets_to_bits(adapted)):
                    self.deliver(frame)
            else:
                self.deliver(adapted)
            return
        width = 7 if self.options.bearer_rate == 56000 else 8
        bits = [(octet >> bit) & 1 for octet in data for bit in range(width)]
        for frame in self.hdlc.feed(bits):
            if len(frame) > self.options.max_frame + 4:
                self.oversize_frames += 1
                continue
            if self.link is not None:
                self.link.feed(frame)
            else:
                self.rx_frames.append(frame)
                self.rx_payload += len(frame)
        if len(self.hdlc.raw) > (self.options.max_frame + 8) * 10:
            self.oversize_frames += 1
            self.hdlc.raw.clear()
            self.hdlc.in_frame = False

    def take_data(self):
        data = bytes(self.rx_data)
        self.rx_data.clear()
        return data

    def take_frames(self):
        frames = list(self.rx_frames)
        self.rx_frames.clear()
        return frames

    def tick(self, now):
        if self.link is not None:
            self.link.tick(now)

    def close(self):
        self.closed = True
        self.tx_bits.clear()
        if self.link is not None:
            self.link.close()
        self.raw.clear()
        if self.v110 is not None:
            self.v110.tx.clear()
            self.v110.stream.clear()
        self.rx_data.clear()
        self.rx_frames.clear()


class PppIsdnAdapter:
    """Connect existing PPP to native HDLC packets or a TA octet stream."""
    def __init__(self, channel, peer):
        from ppp import HdlcFramer
        self.channel, self.peer = channel, peer
        self.framed = channel.profile in (9, 10)
        self.outgoing = HdlcFramer()
        self.outgoing.rx_accm = 0  # sender may have negotiated unescaped controls
        self.incoming = HdlcFramer()
        if channel.profile == 8:
            channel.idle_octet = 0x7E
        if channel.profile == 2 and getattr(channel, 'v110', None) is not None:
            channel.v110.idle_bits = FLAG_BITS

    def transmit(self, now):
        self.channel.tick(now)
        if not self.channel.connected:
            return
        if not self.peer._started:
            self.peer.start(now)
        self.peer.tick(now)
        data = self.peer.take()
        if self.framed:
            # Remove terminal octet escapes/FCS before bit-synchronous HDLC.
            for frame in self.outgoing.feed(data):
                self.channel.queue_data(b'\xff\x03' + frame)
        else:
            self.channel.queue_data(data)

    def receive(self, now):
        from ppp import parse_packet
        if self.framed:
            for frame in self.channel.take_frames():
                packet = self.incoming._strip_header(frame)
                protocol, payload = parse_packet(packet)
                self.peer.feed(self.incoming.encode(protocol, payload), now)
        else:
            self.peer.feed(self.channel.take_data(), now)
