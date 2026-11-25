"""Clear PRI bearer clocks and IDI data for the original firmware.

Firmware performs framing, rate adaptation, FCS, and link protocols. This
boundary only clocks raw SPORT words and moves host IDI requests/indications.
"""
from collections import deque
import eicon_mips_shim as fw

SERIAL_TO_WIRE = bytes(int(f'{value:08b}'[::-1], 2) for value in range(256))


class FirmwareBChannel:
    def __init__(self, shim, options, *, channel=2):
        self.shim, self.options, self.profile = shim, options, options.profile
        self.channel = channel
        self.sr, self.gp, self.sp = shim.idi_context
        self.entity = shim.nl_entity_id
        self.core = shim.cores[shim.service_assign_block]
        self.pending = deque()
        self.rx_wire = deque()
        self.rx_frames = deque()
        self.partial = bytearray()
        self.rx_data = bytearray()
        self.reference = 32
        self.busy = None
        self.flow_controlled = False
        self.tx_octets = self.rx_octets = self.accepted_octets = 0
        self.rejected = self.disconnects = self.unwritten = 0
        self.closed = False
        self.idle_octet = 0xff
        self.now = 0
        self.stream_credit = 0

    @property
    def connected(self):
        return self.shim.nl_connected and not self.closed

    def queue_data(self, data):
        if not self.connected:
            raise RuntimeError('firmware bearer is disconnected')
        if len(data) > self.options.max_frame:
            raise ValueError('IDI data exceeds configured frame limit')
        if sum(len(payload) for request, payload in self.pending) + len(data) > 65536:
            raise BufferError('firmware bearer queue full')
        if self.profile in (6, 7):
            # tty_module/isdn.c isdnXmit: the IDI interface includes the
            # terminal-adaptation header; the firmware does not invent it.
            data = b'\x83' + data
        if self.profile == 8:
            self.stream_credit += len(data)
        # PR_RAM nodes contain 270 octets. N_MDATA continues the same frame;
        # only the final N_DATA fragment closes it (kernel/di.c pr_out).
        for offset in range(0, len(data), 270):
            payload = bytes(data[offset:offset + 270])
            request = 1 if offset + len(payload) < len(data) else fw.eicon_idi.N_DATA
            self.pending.append((request, payload))

    def _pump(self):
        self.shim.phase = 'isdn-clear-bearer'
        self.shim.call(fw.MIPS_MAINLOOP, [], gp=self.gp, sp=self.sp,
                       max_insns=500000)
        for ind, entity, channel, ref, payload in fw.drain_indications(self.shim, self.sr):
            fw.observe_nl_bearer(self.shim, self.entity, ind, entity)
            if entity != self.entity:
                continue
            if ind == fw.eicon_idi.N_DISC:
                self.disconnects += 1
            elif ind in (1, fw.eicon_idi.N_DATA):
                if self.profile in (6, 7, 9, 10):
                    self.partial.extend(payload)
                    if len(self.partial) > self.options.max_frame:
                        raise BufferError('firmware receive frame too large')
                    if ind == fw.eicon_idi.N_DATA:
                        if self.profile in (6, 7):
                            # Extension bit terminates the one/two-octet TA
                            # header returned on IDI, just as isdnNetDataInd.
                            end = next((i + 1 for i, v in enumerate(self.partial) if v & 0x80), None)
                            if end is None or end > 2:
                                raise RuntimeError('invalid V.120 terminal-adaptation header')
                            self.rx_data.extend(self.partial[end:])
                        else:
                            self.rx_frames.append(bytes(self.partial))
                        self.partial.clear()
                else:
                    self.rx_data.extend(payload)
        for rc, entity, channel, reference in fw.drain_return_codes(self.shim, self.sr):
            if entity == self.entity and rc == fw.eicon_idi.READY_INT:
                self.flow_controlled = False
            elif entity == self.entity and self.busy and reference == self.busy[0]:
                if rc in (fw.eicon_idi.RC_OK, fw.eicon_idi.OK_FC):
                    self.accepted_octets += self.busy[1]
                    self.flow_controlled = rc == fw.eicon_idi.OK_FC
                else:
                    self.rejected += 1
                    raise RuntimeError(f'firmware N_DATA rejected: 0x{rc:02x}')
                self.busy = None
        if self.connected and self.pending and self.busy is None and not self.flow_controlled:
            request, data = self.pending.popleft()
            self.reference = (self.reference + 1) & 0xffff
            self.busy = (self.reference, len(data))
            fw.post_request(self.shim, self.sr, request,
                            self.entity, self.channel, data, self.reference)

    def take_wire(self, count=160):
        if count < 0:
            raise ValueError('negative bearer clock count')
        if self.profile == 8 and self.idle_octet == 0x7e:
            # Transparent PPP needs explicit flag fill. Otherwise the native
            # idle mark (FF) becomes garbage between two PPP flag boundaries.
            fill = max(0, count - self.stream_credit)
            if fill:
                self.queue_data(bytes((self.idle_octet,)) * fill)
        self.stream_credit = max(0, self.stream_credit - count)
        self._pump()
        output = bytearray()
        for _ in range(count):
            octet = self.rx_wire.popleft() if self.rx_wire else self.idle_octet
            # SPORT shifts MSB first. The clear-bearer capture/API convention
            # places the oldest serial bit in bit 0; reversal is its inverse.
            word = SERIAL_TO_WIRE[octet]
            packed = fw.ADSP.adsp2181_sport0_frame(self.core, word, 3000)
            self.unwritten += not bool(packed & 0x10000)
            output.append(SERIAL_TO_WIRE[packed & 0xff])
            self.tx_octets += 1
            if self.tx_octets % 8 == 0:
                self._pump()
        return bytes(output)

    def feed_wire(self, data):
        if len(self.rx_wire) + len(data) > 65536:
            raise BufferError('firmware receive wire queue full')
        self.rx_wire.extend(data)
        self.rx_octets += len(data)

    def take_frames(self):
        frames = list(self.rx_frames)
        self.rx_frames.clear()
        return frames

    def take_data(self):
        data = bytes(self.rx_data)
        self.rx_data.clear()
        return data

    def tick(self, now):
        self.now = now

    def close(self):
        self.closed = True
