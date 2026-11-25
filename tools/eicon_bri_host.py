"""Host IDI transaction queues, following kernel/di.c pr_out/pr_dpc.

This consumes firmware-created queues. It never creates queue descriptors,
entity IDs, CONNECT events or firmware return codes.
"""
import struct


class BriHost:
    def __init__(self, card, controller):
        self.card = card
        self.controller = controller
        self.last_reference = None
        self.ram = card.controller_memory(controller, shared=True)

    @property
    def ready(self):
        return self._half(0x1e) == 0x4447

    def _half(self, offset):
        return struct.unpack_from('<H', self.ram, offset)[0]

    def _buffer(self, pointer, header):
        offset = 0x20 + pointer
        if offset + header > len(self.ram):
            raise RuntimeError('firmware queue pointer outside shared RAM')
        return offset

    def request(self, entity, code, channel=0, parameters=b''):
        if not self.ready:
            raise RuntimeError('firmware has not published ready signature')
        if not all(0 <= value <= 255 for value in (entity, code, channel)):
            raise ValueError('IDI entity, request and channel must fit a byte')
        if len(parameters) > 270:
            raise ValueError('IDI parameters exceed one request buffer')
        if not (self.ram[7] - self.ram[6]) & 255:
            return False
        offset = self._buffer(self._half(0), 288)
        next_pointer = self._half(offset)
        self._buffer(next_pointer, 288)
        struct.pack_into('<H', self.ram, offset + 16, len(parameters))
        self.ram[offset + 18:offset + 18 + len(parameters)] = parameters
        self.ram[offset + 2:offset + 5] = bytes((code, entity, channel))
        struct.pack_into('<H', self.ram, 0, next_pointer)
        self.last_reference = self._half(offset + 6)
        self.ram[6] = (self.ram[6] + 1) & 255
        return True

    def _collect(self, indication):
        count_offset, head = (12, 4) if indication else (11, 2)
        count = self.ram[count_offset]
        pointer = self._half(head)
        entries = []
        consumed = []
        for _ in range(count):
            offset = self._buffer(pointer, 288 if indication else 16)
            pointer = self._half(offset)
            if self.ram[offset + 2]:
                entry = dict(code=self.ram[offset + 2], entity=self.ram[offset + 3],
                             channel=self.ram[offset + 4],
                             reference=self._half(offset + (8 if indication else 6)))
                if indication:
                    length = self._half(offset + 16)
                    if length > 270:
                        raise RuntimeError('firmware indication exceeds buffer')
                    entry.update(more=self.ram[offset + 5],
                                 total_length=self._half(offset + 6),
                                 payload=bytes(self.ram[offset + 18:offset + 18 + length]))
                entries.append(entry)
                consumed.append(offset)
        for offset in consumed:
            self.ram[offset + 2] = 0
        self.ram[count_offset] = 0
        # scom_clear_int and the board ISR acknowledge are separate from
        # clearing RcOutput/IndOutput in pr_dpc.
        self.ram[0x3fe] = 0
        self.card.write(3, 0x400, 4, 0)
        return entries

    def collect_returns(self):
        return self._collect(False)

    def collect_indications(self):
        return self._collect(True)


class BriModemSignals:
    """Real N_UDATA DCD/CTS state, as kernel/message.c gates B3 activation.

    The caller must dispatch only indications for this modem's NL entity.
    N_CONNECT_ACK alone does not establish carrier or permission to send.
    """
    def __init__(self):
        self.dcd = self.cts = False
        self.tx_bps = self.rx_bps = None

    @property
    def ready(self):
        return self.dcd and self.cts

    def observe(self, payload):
        if not payload or payload[0] not in (1, 2, 3, 4):
            return False
        event = payload[0]
        if event in (1, 2):
            self.dcd = event == 2
        else:
            self.cts = event == 4
        if event in (2, 4) and len(payload) >= 20:
            self.tx_bps, self.rx_bps = struct.unpack_from('<II', payload, 12)
        return True

    def report(self):
        return dict(dcd=self.dcd, cts=self.cts, ready=self.ready,
                    tx_bps=self.tx_bps, rx_bps=self.rx_bps)


class BriDataSender:
    """Fragment one NL frame across PR_RAM buffers, observing firmware flow control.

    N_MDATA continues a frame; only its last N_DATA fragment terminates it.
    The caller drains real return queues and passes entries to observe_returns.
    No request is posted while its predecessor awaits acknowledgment.
    """
    def __init__(self, host, entity, channel=1, max_frame=2048):
        from collections import deque
        if not 0 <= entity <= 255 or not 0 <= channel <= 255:
            raise ValueError('entity and channel must fit a byte')
        if not 1 <= max_frame <= 65535:
            raise ValueError('invalid maximum frame length')
        self.host, self.entity, self.channel = host, entity, channel
        self.max_frame = max_frame
        self.pending = deque()
        self.busy = None
        self.reference = None
        self.flow_controlled = False
        self.accepted_bytes = self.posted_fragments = 0

    @property
    def idle(self):
        return not self.pending and self.busy is None

    def queue(self, frame):
        frame = bytes(frame)
        if not 1 <= len(frame) <= self.max_frame:
            raise ValueError('frame length outside configured limit')
        if sum(len(data) for code, data in self.pending) + (self.busy or 0) + len(frame) > 65536:
            raise BufferError('bearer transmit queue full')
        for offset in range(0, len(frame), 270):
            data = frame[offset:offset + 270]
            self.pending.append((1 if offset + len(data) < len(frame) else 8, data))

    def pump(self):
        if not self.pending or self.busy is not None or self.flow_controlled:
            return False
        code, data = self.pending[0]
        if not self.host.request(self.entity, code, channel=self.channel, parameters=data):
            return False
        self.pending.popleft()
        self.busy = len(data)
        self.reference = self.host.last_reference
        self.posted_fragments += 1
        return True

    def observe_returns(self, entries):
        for entry in entries:
            if entry['entity'] != self.entity:
                continue
            code = entry['code']
            if code == 0xfd:  # READY_INT releases a flow-controlled sender
                self.flow_controlled = False
            elif (entry['channel'] == self.channel and self.busy is not None
                  and entry.get('reference') == self.reference):
                if code not in (0xff, 0xfc):
                    raise RuntimeError(f'firmware rejected bearer fragment: {code:#x}')
                self.accepted_bytes += self.busy
                self.busy = None
                self.reference = None
                self.flow_controlled = code == 0xfc
