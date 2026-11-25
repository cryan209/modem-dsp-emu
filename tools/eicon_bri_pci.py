"""PCI configuration header for Diva 4BRI v2 (1133:e013, 8001:0014).

Identity/subsystem values measured on SN12196. BAR aperture sizes follow
kernel/os_4bri.c. A bus frontend may assign BAR bases and dispatch accesses
to BriCard; this module does not attach a kernel-visible PCI device.
"""
import struct

# Feature EEPROM bytes read through PCI VPD on spare SN12196.
VPD_IMAGE = bytes.fromhex(
    "331113e001008002000100000000000000000000000000ff010000000c000102"
    "0005300000000000000000004301438a00000000000000000000000000000000"
    "00000000018014000000ffff0180801f43020000064c0000ffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffa42f0000"
)


class BriPci:
    BAR_SIZES = (0x100, 0x100, 0x1000000, 0x10000)

    def __init__(self, serial=12196):
        if not 0 < serial < 0xffffffff:
            raise ValueError('invalid board serial number')
        self.data = bytearray(256)
        struct.pack_into('<I', self.data, 0, 0xe0131133)
        struct.pack_into('<I', self.data, 8, 0x02800001)
        struct.pack_into('<I', self.data, 0x2c, 0x00148001)
        self.data[0x3d] = 1
        struct.pack_into('<H', self.data, 6, 0x0290)
        self.data[0x34] = 0x40
        # Measured PM -> Hot Swap -> VPD capability chain. The PMC supports
        # D0/D3hot only; D1/D2 requests are ignored.
        self.data[0x40:0x4e] = bytes.fromhex('0148010000000000064c80000300')
        self.vpd = bytearray(VPD_IMAGE)
        struct.pack_into('<I', self.vpd, 252, serial)
        self.bars = [0, 1, 0, 0]
        self.probing = [False] * 4
        self.power_transitions = []
        self.power_control = None

    @property
    def power_state(self):
        return self.data[0x44] & 3

    @staticmethod
    def _validate(offset, size):
        if size not in (1, 2, 4) or offset < 0 or offset % size or offset + size > 256:
            raise ValueError('PCI configuration access must be aligned within 256 bytes')

    def read(self, offset, size):
        self._validate(offset, size)
        if 0x10 <= offset < 0x20:
            index = (offset - 0x10) // 4
            if self.probing[index]:
                value = (~(self.BAR_SIZES[index] - 1) & 0xffffffff) | (index == 1)
            else:
                value = self.bars[index]
            shift = (offset % 4) * 8
            return (value >> shift) & ((1 << (size * 8)) - 1)
        return int.from_bytes(self.data[offset:offset + size], 'little')

    def write(self, offset, size, value):
        self._validate(offset, size)
        if not 0 <= value < 1 << (size * 8):
            raise ValueError('PCI value exceeds access width')
        if offset == 0x44:
            state = value & 3
            if state in (0, 3):
                previous = self.power_state
                self.data[0x44] = state
                if state != previous:
                    self.power_transitions.append(state)
                    if self.power_control is not None:
                        self.power_control(previous, state)
            return
        if offset == 0x4e and size == 2:
            if value & 0x8000:
                raise NotImplementedError('VPD EEPROM writes are not supported')
            if value & 3 or value + 4 > len(self.vpd):
                raise NotImplementedError('VPD read outside aligned feature EEPROM')
            self.data[0x50:0x54] = self.vpd[value:value + 4]
            struct.pack_into('<H', self.data, 0x4e, value | 0x8000)
            return
        if offset < 0x54 and offset + size > 0x50:
            raise NotImplementedError('VPD data writes are not supported')
        if 0x10 <= offset < 0x20:
            if size != 4:
                raise NotImplementedError('PCI BAR programming requires dword access')
            index = (offset - 0x10) // 4
            self.probing[index] = value == 0xffffffff
            if not self.probing[index]:
                self.bars[index] = (value & ~(self.BAR_SIZES[index] - 1)) | (index == 1)
            return
        # The supported endpoint controls: PCI command and interrupt line.
        # Identity, class, subsystem, absent BAR4/5 and ROM are read-only.
        if offset == 4 and size in (2, 4):
            struct.pack_into('<H', self.data, 4, value & 0x147)
        elif offset == 0x3c and size == 1:
            self.data[offset] = value
