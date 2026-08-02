#!/usr/bin/env python3
"""Streaming ITU-T V.44 stream-method data compression.

The encoder uses the conforming append-only subset of the V.44 dictionary
algorithm: it creates one-character string segments and greedily emits the
longest complete string already represented by a codeword.  The decoder also
implements string-extension lengths, so it accepts peers that use the full
string-extension procedure.
"""
from __future__ import annotations

from dataclasses import dataclass


ETM = 0
FLUSH = 1
STEPUP = 2
REINIT = 3
ECM = 0
EID = 1
EPM = 2
FIRST_CODEWORD = 4


class V44Error(ValueError):
    """The peer's V.44 stream cannot be decoded synchronously."""


@dataclass(frozen=True)
class V44Parameters:
    """V.44 C0 and P0..P3 values, relative to the XID sender."""

    capability: int = 0
    directions: int = 3
    tx_codewords: int = 512
    rx_codewords: int = 512
    tx_max_string: int = 32
    rx_max_string: int = 32
    tx_history: int = 1024
    rx_history: int = 1024

    def __post_init__(self) -> None:
        if not 0 <= self.capability <= 0xFF:
            raise ValueError('V.44 capability must be one octet')
        if not 0 <= self.directions <= 3:
            raise ValueError('V.44 directions must be in the range 0..3')
        for name, value in (('tx_codewords', self.tx_codewords),
                            ('rx_codewords', self.rx_codewords)):
            if not 256 <= value <= 0xFFFF:
                raise ValueError(f'V.44 {name} must be 256..65535')
        for name, value in (('tx_max_string', self.tx_max_string),
                            ('rx_max_string', self.rx_max_string)):
            if not 32 <= value <= 255:
                raise ValueError(f'V.44 {name} must be 32..255')
        for name, value in (('tx_history', self.tx_history),
                            ('rx_history', self.rx_history)):
            if not 512 <= value <= 0xFFFF:
                raise ValueError(f'V.44 {name} must be 512..65535')


class _BitWriter:
    def __init__(self) -> None:
        self.bits = 0
        self.count = 0
        self.output = bytearray()

    def put(self, value: int, width: int) -> None:
        self.bits |= value << self.count
        self.count += width
        while self.count >= 8:
            self.output.append(self.bits & 0xFF)
            self.bits >>= 8
            self.count -= 8

    def take(self) -> bytes:
        result = bytes(self.output)
        self.output.clear()
        return result

    def align(self) -> None:
        if self.count:
            self.output.append(self.bits & 0xFF)
            self.bits = 0
            self.count = 0


class V44Encoder:
    """Incremental V.44 encoder for the modem stream method."""

    def __init__(self, codewords: int = 512, max_string: int = 32,
                 history: int = 1024) -> None:
        V44Parameters(tx_codewords=codewords, rx_codewords=codewords,
                      tx_max_string=max_string, rx_max_string=max_string,
                      tx_history=history, rx_history=history)
        self.codewords = codewords
        self.max_string = max_string
        self.history_limit = history
        self.max_width = (codewords - 1).bit_length()
        self.writer = _BitWriter()
        self.compressed = True
        self.escape = 0
        self._reset_dictionary()

    def _reset_dictionary(self) -> None:
        self.by_string: dict[bytes, int] = {}
        self.by_code: dict[int, bytes] = {}
        self.next_code = FIRST_CODEWORD
        self.code_width = 6
        self.threshold = 64
        self.ordinal_width = 7
        self.history_count = 0
        self.previous_kind: str | None = None
        self.previous = b''
        self.wire_previous_codeword = False

    def reset(self) -> None:
        self.writer = _BitWriter()
        self.compressed = True
        self.escape = 0
        self._reset_dictionary()

    def _control(self, value: int) -> None:
        self.writer.put(1, 1)
        self.writer.put(value, self.code_width)
        self.wire_previous_codeword = False

    def _reinitialize(self) -> None:
        self._control(REINIT)
        self._reset_dictionary()

    def _add(self, value: bytes) -> None:
        if (len(value) > self.max_string
                or self.next_code >= self.codewords):
            return
        self.by_string[value] = self.next_code
        self.by_code[self.next_code] = value
        self.next_code += 1

    def _record(self, kind: str, value: bytes) -> None:
        if self.previous_kind in ('ordinal', 'codeword'):
            self._add(self.previous + value[:1])
        self.previous_kind = kind
        self.previous = value

    def _ordinal(self, value: int) -> None:
        if value >= 128 and self.ordinal_width == 7:
            self._control(STEPUP)
            self.ordinal_width = 8
        # An ordinal immediately after a codeword has the two-bit prefix 00;
        # otherwise its prefix is the single zero bit.
        self.writer.put(0, 1)
        if self.wire_previous_codeword:
            self.writer.put(0, 1)
        self.writer.put(value, self.ordinal_width)
        self.wire_previous_codeword = False
        self._record('ordinal', bytes((value,)))

    def _codeword(self, code: int, value: bytes) -> None:
        while code >= self.threshold:
            if self.code_width >= self.max_width:
                raise AssertionError('V.44 codeword exceeds negotiated P1')
            self._control(STEPUP)
            self.code_width += 1
            self.threshold *= 2
        self.writer.put(1, 1)
        self.writer.put(code, self.code_width)
        self.wire_previous_codeword = True
        self._record('codeword', value)

    def _longest(self, data: bytes, pos: int) -> tuple[int, bytes] | None:
        limit = min(self.max_string, len(data) - pos,
                    self.history_limit - self.history_count)
        for length in range(limit, 1, -1):
            value = data[pos:pos + length]
            code = self.by_string.get(value)
            if code is not None:
                return code, value
        return None

    def feed(self, data: bytes) -> bytes:
        data = bytes(data)
        if not self.compressed:
            out = bytearray()
            for value in data:
                if value == self.escape:
                    out.extend((self.escape, EID))
                    self.escape = (self.escape + 51) & 0xFF
                else:
                    out.append(value)
            return bytes(out)

        pos = 0
        while pos < len(data):
            if (self.next_code >= self.codewords
                    or self.history_count >= self.history_limit):
                self._reinitialize()
            match = self._longest(data, pos)
            if match is None:
                self._ordinal(data[pos])
                consumed = 1
            else:
                code, value = match
                self._codeword(code, value)
                consumed = len(value)
            pos += consumed
            self.history_count += consumed
        return self.writer.take()

    def flush(self) -> bytes:
        if not self.compressed:
            return b''
        self._control(FLUSH)
        self.writer.align()
        return self.writer.take()


@dataclass
class _Entry:
    end: int
    length: int


class V44Decoder:
    """Incremental V.44 decoder, including peer string extensions."""

    def __init__(self, codewords: int = 512, max_string: int = 32,
                 history: int = 1024) -> None:
        V44Parameters(tx_codewords=codewords, rx_codewords=codewords,
                      tx_max_string=max_string, rx_max_string=max_string,
                      tx_history=history, rx_history=history)
        self.codewords = codewords
        self.max_string = max_string
        self.history_limit = history
        self.max_width = (codewords - 1).bit_length()
        self.bits = 0
        self.bit_count = 0
        self.compressed = True
        self.escape = 0
        self.escaped = False
        self._reset_dictionary()

    def _reset_dictionary(self) -> None:
        self.entries: dict[int, _Entry] = {}
        self.history = bytearray()
        self.next_code = FIRST_CODEWORD
        self.code_width = 6
        self.ordinal_width = 7
        self.previous_kind: str | None = None
        self.previous = b''
        self.previous_code: int | None = None
        self.wire_previous_codeword = False
        self.pending_stepup = False

    def reset(self) -> None:
        self.bits = 0
        self.bit_count = 0
        self.compressed = True
        self.escape = 0
        self.escaped = False
        self._reset_dictionary()

    def _peek(self, offset: int, width: int) -> int:
        return (self.bits >> offset) & ((1 << width) - 1)

    def _consume(self, width: int) -> None:
        self.bits >>= width
        self.bit_count -= width

    def _entry_value(self, code: int) -> bytes:
        entry = self.entries.get(code)
        if entry is None:
            raise V44Error(f'empty V.44 codeword {code}')
        start = entry.end - entry.length + 1
        return bytes(self.history[start:entry.end + 1])

    def _add(self, value: bytes, end: int) -> None:
        if len(value) > self.max_string:
            return
        if self.next_code >= self.codewords:
            raise V44Error('peer filled V.44 node tree without REINIT')
        self.entries[self.next_code] = _Entry(end, len(value))
        self.next_code += 1

    def _append_history(self, value: bytes) -> int:
        start = len(self.history)
        if start + len(value) > self.history_limit:
            raise V44Error('peer exceeded negotiated V.44 history')
        self.history.extend(value)
        return start

    def _ordinal(self, value: int, out: bytearray) -> None:
        current = bytes((value,))
        start = self._append_history(current)
        out.extend(current)
        if self.previous_kind in ('ordinal', 'codeword'):
            self._add(self.previous + current, start)
        self.previous_kind = 'ordinal'
        self.previous = current
        self.previous_code = None
        self.wire_previous_codeword = False

    def _codeword(self, code: int, out: bytearray) -> None:
        if code > self.next_code:
            raise V44Error(f'codeword {code} exceeds C1={self.next_code}')
        if code == self.next_code:
            if self.previous_kind not in ('ordinal', 'codeword'):
                raise V44Error('C1 codeword has no preceding string')
            current = self.previous + self.previous[:1]
        else:
            current = self._entry_value(code)
        start = self._append_history(current)
        out.extend(current)
        if self.previous_kind in ('ordinal', 'codeword'):
            self._add(self.previous + current[:1], start)
        self.previous_kind = 'codeword'
        self.previous = current
        self.previous_code = code
        self.wire_previous_codeword = True

    def _extension(self, length: int, out: bytearray) -> None:
        if self.previous_kind != 'codeword' or self.previous_code is None:
            raise V44Error('string extension does not follow a codeword')
        entry = self.entries.get(self.previous_code)
        if entry is None:
            raise V44Error('string extension references no dictionary entry')
        start = entry.end + 1
        if start >= len(self.history):
            raise V44Error('string extension exceeds available history')
        if len(self.history) + length > self.history_limit:
            raise V44Error('peer exceeded negotiated V.44 history')
        # Source and destination may overlap. This is essential for runs: C1
        # can decode "AA", whose second A immediately follows the represented
        # string and seeds an extension of up to N7-2 more A characters.
        output_start = len(self.history)
        extension = bytearray()
        for offset in range(length):
            source = start + offset
            if source >= len(self.history):
                raise V44Error('string extension exceeds available history')
            value = self.history[source]
            self.history.append(value)
            extension.append(value)
        out.extend(extension)
        self._add(self.previous + extension,
                  output_start + len(extension) - 1)
        self.previous_kind = 'extension'
        self.previous = b''
        self.previous_code = None
        self.wire_previous_codeword = False

    def _parse_extension(self, offset: int) -> tuple[int, int] | None:
        if self.bit_count < offset + 1:
            return None
        if self._peek(offset, 1):
            return 1, 1
        if self.bit_count < offset + 3:
            return None
        short = self._peek(offset + 1, 2)
        if short:
            return short + 1, 3
        if self.bit_count < offset + 4:
            return None
        if not self._peek(offset + 3, 1):
            if self.bit_count < offset + 7:
                return None
            return 5 + self._peek(offset + 4, 3), 7
        width = (5 if self.max_string <= 46 else
                 6 if self.max_string <= 78 else
                 7 if self.max_string <= 142 else 8)
        if self.bit_count < offset + 4 + width:
            return None
        return 13 + self._peek(offset + 4, width), 4 + width

    def _control(self, value: int) -> None:
        self.wire_previous_codeword = False
        if value == STEPUP:
            if self.pending_stepup:
                raise V44Error('consecutive V.44 STEPUP controls')
            self.pending_stepup = True
        elif value == FLUSH:
            self.bits = 0
            self.bit_count = 0
        elif value == REINIT:
            self._reset_dictionary()
        elif value == ETM:
            self.bits = 0
            self.bit_count = 0
            self.compressed = False

    def _drain(self, out: bytearray) -> None:
        while self.compressed and self.bit_count:
            first = self._peek(0, 1)
            if self.pending_stepup:
                if first:
                    if self.code_width >= self.max_width:
                        raise V44Error('STEPUP exceeds negotiated codeword size')
                    width = self.code_width + 1
                    if self.bit_count < 1 + width:
                        return
                    self.code_width = width
                    value = self._peek(1, width)
                    self._consume(1 + width)
                    self.pending_stepup = False
                    if value < FIRST_CODEWORD:
                        raise V44Error('STEPUP followed by a control code')
                    self._codeword(value, out)
                else:
                    if self.ordinal_width >= 8:
                        raise V44Error('STEPUP exceeds 8-bit ordinal size')
                    width = self.ordinal_width + 1
                    if self.bit_count < 1 + width:
                        return
                    self.ordinal_width = width
                    value = self._peek(1, width)
                    self._consume(1 + width)
                    self.pending_stepup = False
                    self._ordinal(value, out)
                continue

            if first:
                if self.bit_count < 1 + self.code_width:
                    return
                value = self._peek(1, self.code_width)
                self._consume(1 + self.code_width)
                if value < FIRST_CODEWORD:
                    self._control(value)
                else:
                    self._codeword(value, out)
                continue

            if self.wire_previous_codeword:
                if self.bit_count < 2:
                    return
                if self._peek(1, 1):
                    parsed = self._parse_extension(2)
                    if parsed is None:
                        return
                    length, width = parsed
                    if length > min(253, self.max_string - 2):
                        raise V44Error('string extension exceeds negotiated P2')
                    self._consume(2 + width)
                    self._extension(length, out)
                    continue
                prefix = 2
            else:
                prefix = 1
            if self.bit_count < prefix + self.ordinal_width:
                return
            value = self._peek(prefix, self.ordinal_width)
            self._consume(prefix + self.ordinal_width)
            self._ordinal(value, out)

    def _transparent(self, value: int, out: bytearray) -> None:
        if self.escaped:
            self.escaped = False
            if value == ECM:
                self._reset_dictionary()
                self.compressed = True
            elif value == EID:
                out.append(self.escape)
                self.escape = (self.escape + 51) & 0xFF
            elif value == EPM:
                raise V44Error('post-link V.44 parameter mode is unsupported')
            else:
                raise V44Error(f'reserved V.44 command 0x{value:02x}')
        elif value == self.escape:
            self.escaped = True
        else:
            out.append(value)

    def feed(self, data: bytes) -> bytes:
        out = bytearray()
        for value in data:
            if not self.compressed:
                self._transparent(value, out)
                continue
            self.bits |= value << self.bit_count
            self.bit_count += 8
            self._drain(out)
        return bytes(out)
