"""IPAC-X register boundary for 4BRI v2, with C/I events and independent D/B-channel frame FIFOs.

Addresses follow upstream Linux drivers/isdn/hisax/ipacx.h. Reset status
values were measured on all four interfaces of spare SN12196 on 2026-10-26.
Read/write aliases are separate; unimplemented commands fail explicitly.
"""
from ppp import fcs16


class _BearerFifo:
    """One B-channel HDLC FIFO with transparent and non-auto address modes."""
    def __init__(self, owner, index):
        self.owner, self.index = owner, index
        self.base = 0x70 + 0x10 * index
        self.tx_fifo, self.tx_packet = bytearray(), bytearray()
        self.rx_fifo, self.rx_packet = bytearray(), bytearray()
        self.rx_busy = False
        self.rx_length, self.rx_status = 0, 0x0f
        self.transmitted = []

    @property
    def extension(self):
        return self.owner.config[self.base + 3]

    def signal(self, flags):
        self.owner.pending_b[self.index] |= flags

    def _next_chunk(self):
        size = 64 >> ((self.extension >> 5) & 3)
        self.rx_fifo[:] = self.rx_packet[:size]
        del self.rx_packet[:size]
        self.signal(0x40 if self.rx_packet else 0x80)

    def _address(self, frame, mode):
        """Return acceptance, raw status bits and data with compared addresses removed."""
        first = frame[0] if frame else 0
        second = frame[1] if len(frame) > 1 else 0
        if mode == 0xc0:
            high = 8 if frame and first & 0xfc == 0xfc else 12
            return True, high | (first & 2) | int(len(frame) > 1 and second == 0xff), frame
        rah1 = self.owner.config.get(self.base+5, 0)
        rah2 = self.owner.config.get(self.base+6, 0)
        ral1 = self.owner.config.get(self.base+7, 0)
        ral2 = self.owner.config.get(self.base+8, 0)
        if rah1 & 1 or rah2 & 1:
            raise NotImplementedError('IPAC B masked address comparison is unqualified')
        if mode == 0x40:
            accepted = bool(frame) and first in (ral1, ral2)
            return accepted, (first & 2) | int(first == ral1), frame[1:]
        if len(frame) < 2:
            raise NotImplementedError('IPAC B frame shorter than two-byte address')
        high = (8 if first & 0xfc == rah1 & 0xfc else
                0 if first & 0xfc == rah2 & 0xfc else
                4 if first & 0xfc == 0xfc else 12)
        low1, low2, group = second == ral1, second == ral2, second == 0xff
        status = high | (first & 2) | int((low1 or low2) and not group)
        if mode == 0xe0:
            return high != 12, status, frame[1:]
        if mode == 0xa0:
            return low1 or low2 or group, status, frame[:1] + frame[2:]
        if mode == 0x60:
            accepted = ((high == 8 and (low1 or group)) or
                        (high == 0 and (low2 or group)) or
                        (high == 4 and (low1 or low2 or group)))
            return accepted, status, frame[2:]
        raise NotImplementedError('IPAC B receive mode requires framing support')

    def receive(self, frame):
        frame = bytes(frame)
        return self.receive_with_fcs(frame + (fcs16(frame) ^ 0xffff).to_bytes(2, 'little'))

    def receive_with_fcs(self, octets):
        mode = self.owner.config[self.base + 2]
        if not mode & 8:
            return False
        mode &= 0xe0
        if mode not in (0x40, 0x60, 0xa0, 0xc0, 0xe0):
            raise NotImplementedError('IPAC B receive mode requires framing support')
        octets = bytes(octets)
        if len(octets) < 2:
            raise NotImplementedError('IPAC B frame shorter than its FCS')
        frame, crc = octets[:-2], octets[-2:]
        crc_good = crc == (fcs16(frame) ^ 0xffff).to_bytes(2, 'little')
        accepted, address_status, data = self._address(frame, mode)
        if not accepted:
            return False
        if self.rx_busy:
            self.signal(0x20)
            return False
        self.rx_busy = True
        self.rx_status = 0x80 | (0x20 if crc_good else 0) | address_status
        if self.extension & 0x10:
            data = frame
        self.rx_packet[:] = data + (crc if self.extension & 4 else b'') + bytes((self.rx_status,))
        self.rx_length = len(self.rx_packet)
        self._next_chunk()
        return True

    def command(self, value):
        if value & ~0xcb:
            raise NotImplementedError(f'IPAC B command {value:#x}')
        if value & 0x40:
            self.rx_fifo.clear()
            self.rx_packet.clear()
            self.rx_busy = False
            self.rx_status, self.rx_length = 0x0f, 0
            self.owner.pending_b[self.index] &= 0x1f
        elif value & 0x80:
            self.rx_fifo.clear()
            self.owner.pending_b[self.index] &= 0x1f
            if self.rx_packet:
                self._next_chunk()
            else:
                self.rx_busy = False
                self.rx_length = 0
        if value & 1:
            self.tx_fifo.clear()
            self.tx_packet.clear()
            self.signal(0x10)
        if value & 0x0a:
            if self.owner.config[self.base + 2] & 0xe0 not in (0x40, 0x60, 0xa0, 0xc0, 0xe0):
                raise NotImplementedError('IPAC B transmit mode requires stream framing support')
            self.tx_packet.extend(self.tx_fifo)
            self.tx_fifo.clear()
            if value & 2:
                frame = bytes(self.tx_packet)
                self.transmitted.append(frame)
                octets = (frame if self.extension & 8 else
                          frame + (fcs16(frame) ^ 0xffff).to_bytes(2, 'little'))
                if self.owner.config[self.base + 9] & 1:
                    self.receive_with_fcs(octets)
                elif self.owner.peer is not None and self.owner.active and self.owner.peer.active:
                    self.owner.peer.receive_b_frame_with_fcs(self.index, octets)
                self.tx_packet.clear()
            self.signal(0x10)

    def write_fifo(self, value):
        if len(self.tx_fifo) >= (32 if self.extension & 0x80 else 64):
            raise BufferError('IPAC B transmit FIFO overflow')
        self.tx_fifo.append(value)


class Ipacx:
    CONFIG = {0x22, 0x23, 0x24, 0x29, 0x30, 0x31, 0x32, 0x34,
              0x39, 0x3a, 0x3c, 0x3d, 0x3e, *range(0x40, 0x58),
              0x59, 0x5a, 0x5e, 0x62, 0x63, 0x65,
              0x72, 0x73, 0x79, 0x82, 0x83, 0x89}
    WRITE_ONLY = {0x25, 0x26, 0x27, 0x28, 0x2f, 0x35, 0x36, 0x37,
                  0x3f, 0x58, 0x5c, 0x5f, 0x61, 0x75, 0x76, 0x77,
                  0x78, 0x85, 0x86, 0x87, 0x88}

    def __init__(self):
        self.reset()

    def reset(self):
        self.config = {reg: 0 for reg in self.CONFIG}
        self.mask = 0xff
        self.aux_mask = 0xff
        self.pending_aux = 0
        self.maskd = 0xff
        self.maskb = [0xff, 0xff]
        self.pending_b = [0x10, 0x10]
        self.config[0x57] = 0x08  # Measured EN_BCL reset default on all four IPACs.
        self.config[0x72] = self.config[0x82] = 0xc0
        self.bearers = [_BearerFifo(self, i) for i in range(2)]
        self.commands = []
        self.pending = 0
        self.pending_d = 0
        self.cir0 = 0x1a
        self.ci_queue = [15]  # reset completes into Deactivated/DC
        self.tx_fifo = bytearray()
        self.tx_packet = bytearray()
        self.transmitted = []
        self.rx_packet = bytearray()
        self.rx_length = 0
        self.rx_status = 0
        self.rx_fifo = bytearray()
        self.peer = getattr(self, 'peer', None)
        self.active = False
        self.iom_configured = False

    @property
    def irq(self):
        return bool(self._ista() & ~self.mask)

    def _ista(self):
        value = self.pending | (1 if self.pending_d & ~self.maskd else 0)
        for pending, mask, flag in zip(self.pending_b, self.maskb, (0x80, 0x40)):
            if pending & ~mask:
                value |= flag
        if self.pending_aux & ~self.aux_mask:
            value |= 8
        return value

    def connect(self, peer):
        if peer is self:
            raise ValueError('IPAC cannot be its own line peer')
        if self.peer is not None or peer.peer is not None:
            raise ValueError('line already connected')
        self.peer, peer.peer = peer, self

    def _indication(self, value):
        previous = self.ci_queue[-1] if self.ci_queue else self.cir0 >> 4
        if previous == value:
            return
        self.ci_queue.append(value)
        if self.cir0 & 8:
            self.pending |= 0x10
        # CIR0 is a two-event FIFO: preserve the oldest unread event and
        # the latest transition. A consumed current code occupies no slot.
        capacity = 1 if self.cir0 & 8 else 2
        if len(self.ci_queue) > capacity:
            if capacity == 1:
                self.ci_queue[:] = [value]
            else:
                self.ci_queue[:] = [self.ci_queue[0], value]

    def serial_tick(self):
        if not self.cir0 & 8 and self.ci_queue:
            value = self.ci_queue.pop(0)
            # C/I changes update CODR0/CIC0, not the independent S/G and
            # BAS signals. Physical stock-interface captures retain S/G=1
            # even for AI (0xc); deriving it from activation loses status.
            self.cir0 = (value << 4) | 0x08 | (self.cir0 & 3)
            self.pending |= 0x10

    def receive_frame(self, frame):
        if len(frame) > 2048:
            raise ValueError('D-channel frame exceeds 2048 bytes')
        if self.rx_fifo or self.rx_packet:
            self.pending_d |= 0x20  # receiver FIFO overflow
            return False
        self.rx_packet = bytearray(frame) + b'\xa0'
        self.rx_length = len(self.rx_packet)
        self.rx_status = 0xa0
        self._next_rx_chunk()
        return True

    def _next_rx_chunk(self):
        size = 32 >> ((self.config[0x23] >> 5) & 3)
        self.rx_fifo = self.rx_packet[:size]
        del self.rx_packet[:size]
        self.pending_d |= 0x40 if self.rx_packet else 0x80

    def _d_command(self, value):
        if value & ~0xcb:
            raise NotImplementedError(f'IPAC D command {value:#x}')
        if value & 0x40:
            self.rx_packet.clear()
            self.rx_fifo.clear()
            self.rx_status = 0
            self.pending_d &= 0x1f
        elif value & 0x80:
            self.rx_fifo.clear()
            self.pending_d &= 0x1f
            if self.rx_packet:
                self._next_rx_chunk()
        if value & 1:
            self.tx_fifo.clear()
            self.tx_packet.clear()
            self.pending_d |= 0x10
        if value & 0x0a:
            self.tx_packet.extend(self.tx_fifo)
            self.tx_fifo.clear()
            if value & 2:
                frame = bytes(self.tx_packet)
                self.transmitted.append(frame)
                if self.peer is not None and self.active and self.peer.active:
                    self.peer.receive_frame(frame)
                self.tx_packet.clear()
            self.pending_d |= 0x10

    def receive_b_frame(self, channel, frame):
        if type(channel) is not int or not 0 <= channel < 2:
            raise ValueError('B-channel must be 0 or 1')
        return self.bearers[channel].receive(frame)

    def receive_b_frame_with_fcs(self, channel, octets):
        """Inject aligned HDLC octets including FCS, excluding flags/bit stuffing."""
        if type(channel) is not int or not 0 <= channel < 2:
            raise ValueError('B-channel must be 0 or 1')
        return self.bearers[channel].receive_with_fcs(octets)

    def read(self, register):
        if 0 <= register < 0x20:
            return self.rx_fifo.pop(0) if self.rx_fifo else 0
        if 0x70 <= register <= 0x8a and register // 16 in (7, 8):
            bearer = self.bearers[(register >> 4) - 7]
            offset = register & 15
            if offset == 6:
                return bearer.rx_length & 255
            if offset == 7:
                return ((bearer.rx_length >> 8) & 15) | (0x10 if bearer.rx_length > 4095 else 0)
            if offset == 8:
                return bearer.rx_status
            if offset == 10:
                return bearer.rx_fifo.pop(0) if bearer.rx_fifo else 0
        if register in self.CONFIG:
            return self.config[register]
        if register == 0x61:
            value = self.pending_aux & ~self.aux_mask
            self.pending_aux &= ~value
            return value
        if register == 0x60:
            return self._ista()
        if register == 0x20:
            value, self.pending_d = self.pending_d, 0
            return value
        if register in (0x70, 0x80):
            index = int(register == 0x80)
            value = self.pending_b[index] & ~self.maskb[index]
            self.pending_b[index] &= ~value
            return value
        if register == 0x26:
            return self.rx_length & 0xff
        if register == 0x27:
            return (self.rx_length >> 8) & 0x0f
        if register == 0x28:
            return self.rx_status
        if register == 0x21:
            return 0x40
        if register in (0x71, 0x81):
            bearer = self.bearers[int(register == 0x81)]
            capacity = 32 if bearer.extension & 0x80 else 64
            return (0x40 if len(bearer.tx_fifo) < capacity else 0) | (2 if bearer.tx_packet else 0)
        if register == 0x2e:
            value = self.cir0
            self.pending &= ~0x10
            self.cir0 &= ~0x08
            return value
        if register == 0x33:
            return 0
        if register == 0x64:
            return 1
        raise NotImplementedError(f'IPAC read {register:#x}')

    def write(self, register, value):
        if 0 <= register < 0x20:
            if len(self.tx_fifo) >= (16 if self.config[0x23] & 0x80 else 32):
                raise BufferError('IPAC D transmit FIFO overflow')
            self.tx_fifo.append(value)
        elif register in (0x7a, 0x8a):
            self.bearers[(register >> 4) - 7].write_fifo(value)
        elif register == 0x61:
            self.aux_mask = value
        elif register in self.CONFIG or register in self.WRITE_ONLY:
            self.config[register] = value
            if register == 0x57:
                self.iom_configured = True
        elif register == 0x60:
            self.mask = value
        elif register == 0x20:
            self.maskd = value
        elif register in (0x70, 0x80):
            self.maskb[register == 0x80] = value
        elif register == 0x2e:
            self.commands.append((register, value))
            command = value >> 4
            if command == 0:
                if not self.active:
                    self._indication(7)  # TIM starts clocks: F3 Power Up
            elif command == 1:
                self.active = False
                self._indication(1)
            elif command == 15:
                self.active = False
                self._indication(15)
            elif command in (8, 9, 10):
                if self.peer is not None:
                    self.active = self.peer.active = True
                    self._indication(0xc)
                    self.peer._indication(0xc)
            elif command == 12 and self.config[0x3a] & 2:
                self.active = self.peer is not None
            elif command not in (2, 3):
                raise NotImplementedError(f'IPAC C/I command {value:#x}')
        elif register == 0x21:
            self.commands.append((register, value))
            self._d_command(value)
        elif register in (0x71, 0x81):
            self.commands.append((register, value))
            self.bearers[int(register == 0x81)].command(value)
        elif register == 0x64:
            # Keep commands visible; their side effects require the remaining
            # FIFO/transceiver model rather than generic register storage.
            self.commands.append((register, value))
            raise NotImplementedError(f'IPAC command {register:#x}={value:#x}')
        else:
            raise NotImplementedError(f'IPAC write {register:#x}={value:#x}')
