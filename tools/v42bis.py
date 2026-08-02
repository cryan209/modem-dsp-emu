#!/usr/bin/env python3
"""Streaming V.42bis data compression for a V.42 information stream.

The codec starts in transparent mode as required by V.42bis.  The encoder
uses a deliberately simple compressibility policy: after a short transparent
prefix it enters compressed mode and remains there until reinitialization.
The mode-selection policy is intentionally separate from the normative wire
format, dictionary, escape, codeword-size, and flush procedures implemented
here.
"""
from __future__ import annotations

from dataclasses import dataclass


ETM = 0
FLUSH = 1
STEPUP = 2
ECM = 0
EID = 1
RESET = 2
FIRST_STRING = 259


class V42bisError(ValueError):
    """The peer's compressed stream cannot be decoded synchronously."""


@dataclass(frozen=True)
class V42bisParameters:
    """The negotiated P0/P1/P2 values from Annex A of V.42bis."""

    directions: int = 3
    codewords: int = 512
    max_string: int = 32

    def __post_init__(self) -> None:
        if not 0 <= self.directions <= 3:
            raise ValueError('V.42bis directions must be in the range 0..3')
        if not 512 <= self.codewords <= 0xFFFF:
            raise ValueError('V.42bis requires at least 512 codewords')
        if not 6 <= self.max_string <= 250:
            raise ValueError('V.42bis maximum string length must be 6..250')


class _Dictionary:
    """V.42bis tree dictionary with deterministic leaf-node recovery."""

    def __init__(self, codewords: int, max_string: int) -> None:
        self.codewords = codewords
        self.max_string = max_string
        self.by_code: dict[int, bytes] = {
            3 + value: bytes((value,)) for value in range(256)
        }
        self.by_string: dict[bytes, int] = {
            value: code for code, value in self.by_code.items()
        }
        self.children: dict[int, int] = {}
        self.parent: dict[int, int] = {}
        self.next_code = FIRST_STRING
        self.last_added: int | None = None

    def code(self, value: bytes, *, for_match: bool = False) -> int | None:
        code = self.by_string.get(value)
        if for_match and code == self.last_added:
            return None
        return code

    def value(self, code: int) -> bytes | None:
        return self.by_code.get(code)

    def add(self, prefix: bytes, suffix: int) -> int | None:
        value = prefix + bytes((suffix,))
        if len(value) > self.max_string or value in self.by_string:
            self.last_added = None
            return None
        code = self.next_code
        if code in self.by_code:
            raise AssertionError('V.42bis C1 does not identify an empty entry')
        parent = self.by_string.get(prefix)
        if parent is None:
            raise AssertionError('V.42bis new string has no dictionary parent')
        self.by_code[code] = value
        self.by_string[value] = code
        self.parent[code] = parent
        self.children[parent] = self.children.get(parent, 0) + 1
        self.last_added = code
        self._recover_next()
        return code

    def _recover_next(self) -> None:
        while True:
            self.next_code += 1
            if self.next_code >= self.codewords:
                self.next_code = FIRST_STRING
            if self.next_code not in self.by_code:
                return
            if self.children.get(self.next_code, 0):
                continue
            code = self.next_code
            value = self.by_code.pop(code)
            self.by_string.pop(value)
            parent = self.parent.pop(code)
            remaining = self.children[parent] - 1
            if remaining:
                self.children[parent] = remaining
            else:
                self.children.pop(parent)
            return


class V42bisEncoder:
    """Incremental encoder producing octet-aligned C-TRANSFER blocks."""

    def __init__(self, codewords: int = 512, max_string: int = 32,
                 compress_after: int = 32) -> None:
        V42bisParameters(3, codewords, max_string)
        if compress_after < 1:
            raise ValueError('compress_after must be positive')
        self.codewords = codewords
        self.max_string = max_string
        self.max_width = (codewords - 1).bit_length()
        self.compress_after = compress_after
        self.reset()

    def reset(self) -> None:
        self.dictionary = _Dictionary(self.codewords, self.max_string)
        self.compressed = False
        self.escape = 0
        self.current = b''
        self.code_width = 9
        self.threshold = 512
        self._transparent_count = 0
        self._bits = 0
        self._bit_count = 0
        self._exception = False

    @staticmethod
    def _cycle_escape(value: int, escape: int) -> int:
        return (escape + 51) & 0xFF if value == escape else escape

    def _put_code(self, code: int, out: bytearray) -> None:
        while code >= self.threshold:
            if self.code_width >= self.max_width:
                raise AssertionError('codeword exceeds negotiated V.42bis P1')
            self._put_bits(STEPUP, self.code_width, out)
            self.code_width += 1
            self.threshold *= 2
        self._put_bits(code, self.code_width, out)

    def _put_bits(self, value: int, width: int, out: bytearray) -> None:
        self._bits |= value << self._bit_count
        self._bit_count += width
        while self._bit_count >= 8:
            out.append(self._bits & 0xFF)
            self._bits >>= 8
            self._bit_count -= 8

    def _enter_compressed(self, first: int, out: bytearray) -> None:
        if self.current:
            self.dictionary.add(self.current, first)
        out.extend((self.escape, ECM))
        self.compressed = True
        self.current = bytes((first,))
        self.escape = self._cycle_escape(first, self.escape)

    def feed(self, data: bytes) -> bytes:
        out = bytearray()
        for value in data:
            if not self.compressed:
                if self._transparent_count >= self.compress_after:
                    self._enter_compressed(value, out)
                    continue
                candidate = self.current + bytes((value,))
                if self.current and self.dictionary.code(
                        candidate, for_match=True) is None:
                    self.dictionary.add(self.current, value)
                    self.current = bytes((value,))
                else:
                    self.current = candidate
                if value == self.escape:
                    out.extend((self.escape, EID))
                else:
                    out.append(value)
                self.escape = self._cycle_escape(value, self.escape)
                self._transparent_count += 1
                continue

            if self._exception:
                if self.current:
                    self.dictionary.add(self.current, value)
                self.current = bytes((value,))
                self._exception = False
            else:
                candidate = self.current + bytes((value,))
                if self.dictionary.code(candidate, for_match=True) is not None:
                    self.current = candidate
                else:
                    code = self.dictionary.code(self.current)
                    if code is None:
                        raise AssertionError('encoder string missing from dictionary')
                    self._put_code(code, out)
                    self.dictionary.add(self.current, value)
                    self.current = bytes((value,))
            self.escape = self._cycle_escape(value, self.escape)
        return bytes(out)

    def flush(self) -> bytes:
        """Transfer pending compressed data and recover octet alignment."""
        if not self.compressed or not self.current:
            return b''
        out = bytearray()
        code = self.dictionary.code(self.current)
        if code is None:
            raise AssertionError('encoder flush string missing from dictionary')
        self._put_code(code, out)
        if self._bit_count:
            self._put_code(FLUSH, out)
            if self._bit_count:
                out.append(self._bits & 0xFF)
                self._bits = 0
                self._bit_count = 0
        self._exception = True
        return bytes(out)


class V42bisDecoder:
    """Incremental decoder accepting arbitrary LAPM information fragments."""

    def __init__(self, codewords: int = 512, max_string: int = 32) -> None:
        V42bisParameters(3, codewords, max_string)
        self.codewords = codewords
        self.max_string = max_string
        self.max_width = (codewords - 1).bit_length()
        self.reset()

    def reset(self) -> None:
        self.dictionary = _Dictionary(self.codewords, self.max_string)
        self.compressed = False
        self.escape = 0
        self.current = b''
        self.code_width = 9
        self._bits = 0
        self._bit_count = 0
        self._escaped = False

    def _accept_data(self, value: bytes, out: bytearray) -> None:
        if self.current:
            self.dictionary.add(self.current, value[0])
        self.current = value
        out.extend(value)
        for octet in value:
            if octet == self.escape:
                self.escape = (self.escape + 51) & 0xFF

    def _transparent(self, value: int, out: bytearray) -> None:
        candidate = self.current + bytes((value,))
        if self.current and self.dictionary.code(
                candidate, for_match=True) is None:
            self.dictionary.add(self.current, value)
            self.current = bytes((value,))
        else:
            self.current = candidate
        out.append(value)
        if value == self.escape:
            self.escape = (self.escape + 51) & 0xFF

    def _control(self, code: int) -> None:
        if code == STEPUP:
            if self.code_width >= self.max_width:
                raise V42bisError('STEPUP exceeds negotiated codeword size')
            self.code_width += 1
            return
        if code == FLUSH:
            self._bits = 0
            self._bit_count = 0
            return
        if code == ETM:
            self._bits = 0
            self._bit_count = 0
            self.compressed = False

    def feed(self, data: bytes) -> bytes:
        out = bytearray()
        for value in data:
            if not self.compressed:
                if self._escaped:
                    self._escaped = False
                    if value == ECM:
                        self.compressed = True
                    elif value == EID:
                        self._transparent(self.escape, out)
                    elif value == RESET:
                        self.reset()
                    else:
                        raise V42bisError(
                            f'reserved transparent command 0x{value:02x}')
                elif value == self.escape:
                    self._escaped = True
                else:
                    self._transparent(value, out)
                continue

            self._bits |= value << self._bit_count
            self._bit_count += 8
            while self.compressed and self._bit_count >= self.code_width:
                mask = (1 << self.code_width) - 1
                code = self._bits & mask
                self._bits >>= self.code_width
                self._bit_count -= self.code_width
                if code <= STEPUP:
                    self._control(code)
                    continue
                if code == self.dictionary.next_code:
                    raise V42bisError('received codeword equal to C1')
                decoded = self.dictionary.value(code)
                if decoded is None:
                    raise V42bisError(f'empty dictionary codeword {code}')
                self._accept_data(decoded, out)
        return bytes(out)
