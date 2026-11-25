"""Measured 4BRI-v2 serial configuration port and fixed Diva FPGA personality.

The GPIO state machine consumes real clocked bits. The qualified body is
identified before enabling the existing Diva register model; unknown FPGA
personalities are explicit, not treated as interchangeable logic. This is
not a generic Xilinx fabric/CRC emulator. GPIO/DONE timing is functional;
the stock image's four startup edges were measured on spare SN12196.
"""
import hashlib

PROG, DONE, CS, CCLK, DATA = 1, 2, 0xc, 0x100, 0x400
OUTPUTS = PROG | CS | CCLK | DATA
READ_ONLY = 0x1802
BODY_BITS = 29201 * 8
BODY_SHA256 = '8aafe701d0e93b345624f130d7c60e16a248c591e8fbd1b57476287d24529756'
PERSONALITY = 'diva-4bri-v2-20071015'


class BriFpgaSerial:
    def __init__(self):
        # Existing board diagnostics model a stock-configured, stopped card.
        self.control = CS | PROG | DATA
        self.done = True
        self.personality = PERSONALITY
        self.clocks = 0
        self.length_count = None
        self.error = None
        self.state = 'configured'
        self._ones = self._value = self._remaining = self._body_count = 0
        self._body_byte = self._body_byte_bits = 0
        self._hash = hashlib.sha256()

    def read(self):
        # Measured EEPROM-present and bidirectional data readback.
        return 0x1000 | self.control | (0x800 if self.control & DATA else 0) | (DONE if self.done else 0)

    def write(self, value):
        if not 0 <= value <= 0xffff:
            raise ValueError('FPGA control must fit a word')
        if value & ~(OUTPUTS | READ_ONLY):
            raise NotImplementedError(f'unqualified PLX control bits {value:#x}')
        previous = self.control
        self.control = value & OUTPUTS
        if self.control & CS != CS:
            raise NotImplementedError('unqualified programming-pin direction mode')
        if not self.control & PROG:
            self.done = False
            self.personality = None
            self.clocks = 0
            self.length_count = None
            self.error = None
            self.state = 'reset'
            self._ones = self._value = self._remaining = self._body_count = 0
            self._body_byte = self._body_byte_bits = 0
            self._hash = hashlib.sha256()
            return
        if not previous & PROG:
            self.state = 'fill'
        if self.control & CCLK and not previous & CCLK and not self.done:
            self._clock(int(bool(self.control & DATA)))

    def _clock(self, bit):
        self.clocks += 1
        if self.error:
            return
        if self.state == 'fill':
            if bit:
                self._ones += 1
            elif self._ones >= 8:
                self._value, self._remaining, self.state = 0, 3, 'preamble'
            else:
                self._ones = 0
        elif self.state in ('preamble', 'length', 'separator'):
            self._value = (self._value << 1) | bit
            self._remaining -= 1
            if self._remaining:
                return
            if self.state == 'preamble':
                if self._value != 2:
                    self.error = 'unsupported configuration preamble'
                    return
                self.state, self._remaining, self._value = 'length', 24, 0
            elif self.state == 'length':
                self.length_count = self._value
                self.state, self._remaining, self._value = 'separator', 4, 0
            else:
                if self._value != 15:
                    self.error = 'invalid configuration separator'
                elif self.length_count != self.clocks + BODY_BITS + 1:
                    self.error = 'unsupported FPGA configuration geometry'
                else:
                    self.state = 'body'
        elif self.state == 'body':
            self._body_byte = (self._body_byte << 1) | bit
            self._body_byte_bits += 1
            self._body_count += 1
            if self._body_byte_bits == 8:
                self._hash.update(bytes((self._body_byte,)))
                self._body_byte = self._body_byte_bits = 0
            if self._body_count == BODY_BITS:
                if self._hash.hexdigest() != BODY_SHA256:
                    self.error = 'unsupported FPGA configuration personality'
                else:
                    self.state = 'startup'
        elif self.state == 'startup':
            if self.clocks == self.length_count + 3:
                self.done = True
                self.personality = PERSONALITY
                self.state = 'configured'

    def snapshot(self):
        return dict(control=self.control, done=self.done, clocks=self.clocks,
                    length_count=self.length_count, state=self.state,
                    personality=self.personality, error=self.error)
