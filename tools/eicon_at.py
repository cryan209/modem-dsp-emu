#!/usr/bin/env python3
"""The AT command set /dev/ttyds* presents, modelled on divas4linux's atp.c.

The card has no AT parser: AT is a host-side layer that the tty driver
implements on top of IDI, and this project has been driving IDI directly with
command-line flags.  This module puts the documented command set back in
front, so a stock terminal, `pppd` or `minicom` can drive the emulated card
the way it would drive the real one.

The parser knows nothing about IDI or about the emulator.  Commands that need
the outside world become `AtAction`s for the caller to perform, and the caller
reports back through `connected()`, `ring()` and `no_carrier()`.  The one
piece of shared vocabulary is `eicon_idi.ModemOptions`: `options()` returns
the modem configuration the current register and `+IE` state describe, ready
to hand to `eicon_idi.build_cai()`.

Fax class selection is implemented so a T.30 endpoint can identify itself as
Class 1 before call setup. The Class 1 modulation and HDLC commands remain
unimplemented: accepting those would falsely claim that terminal data has
been connected to the DSP fax pages. BTX, PIAFS, the SDLC registers beyond
storing them, and `AT&V1`'s profile dump are also unimplemented. Unknown
commands return ERROR rather than being
silently accepted, which is what atp.c does and what makes a misconfigured
dial script fail loudly.

Reference: `docs/divas4linux-master/AT.txt` for the documented set,
`tty_module/atp.c` for the behaviour where the two disagree.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

import eicon_idi

# Result codes, in atp.c's numbering (atp.c:144).  The numeric form is what
# ATV0 sends; the text is ATV1.
RESULT_CODES: "dict[str, tuple[int, str]]" = {
    "OK": (0, "OK"),
    "CONNECT": (1, "CONNECT"),
    "RING": (2, "RING"),
    "NO CARRIER": (3, "NO CARRIER"),
    "ERROR": (4, "ERROR"),
    "NO DIALTONE": (6, "NO DIALTONE"),
    "BUSY": (7, "BUSY"),
    "NO ANSWER": (8, "NO ANSWER"),
}

# CONNECT <speed> numbering: atRspConn() indexes SpeedMap and adds R_CONNECT,
# falling back to code 10 for anything the map does not hold.
SPEED_MAP = (300, 1200, 2400, 4800, 9600, 14400, 19200, 38400, 57600, 115200)


class Mode(Enum):
    COMMAND = "command"
    DATA = "data"
    FAX_DATA = "fax-data"


class ActionKind(Enum):
    DIAL = "dial"
    ANSWER = "answer"
    HANGUP = "hangup"
    ONLINE = "online"       # ATO: return to data mode without redialling
    RESET = "reset"         # ATZ: drop the call and reload a profile
    FAX_SEND = "fax-send"   # Class 1 DTE frame ready for the DSP
    FAX_CONFIG = "fax-config" # Class 1 modulation request for the DSP


@dataclass
class AtAction:
    """Something the parser cannot do itself."""
    kind: ActionKind
    number: str = ""
    profile: "int | None" = None
    options: "eicon_idi.ModemOptions | None" = None
    fax_operation: str = ""
    fax_payload: bytes = b""
    fax_modulation: int = 0

    def __str__(self) -> str:
        if self.kind is ActionKind.DIAL:
            return f"DIAL {self.number}"
        if self.profile is not None:
            return f"{self.kind.value.upper()} profile {self.profile}"
        return self.kind.value.upper()


@dataclass
class Registers:
    """The S-register bank.  Only the ones with behaviour are named."""
    values: "dict[int, int]" = field(default_factory=dict)

    DEFAULTS = {
        0: 255,    # auto-answer: 255 ignores incoming calls entirely
        2: 43,     # escape character, 127 disables the escape process
        3: 13,     # carriage return
        4: 10,     # line feed
        5: 8,      # backspace
        6: 2,      # wait before blind dial
        7: 0,      # carrier wait; 0 means "protocol default"
        8: 2,      # comma pause
        9: 6,      # carrier detect response time (accepted, no effect)
        10: 14,    # delay between carrier loss and hangup, tenths
        12: 50,    # escape guard time, fiftieths of a second
        27: 0,     # bit 3 disables the 2100 Hz answer tone
        51: 0,     # per-speed error-control disables
        91: 0,     # SDLC L2 options
        92: 0x30,  # SDLC address A
        172: 0,    # reserved modulation options
        253: 0,    # Q.931 disconnect cause
        254: 0,    # bit 0: ATH may reject; bit 1: TIES
    }

    def __post_init__(self) -> None:
        if not self.values:
            self.reset()

    def reset(self) -> None:
        self.values = dict(self.DEFAULTS)

    def __getitem__(self, index: int) -> int:
        return self.values.get(index, 0)

    def __setitem__(self, index: int, value: int) -> None:
        if not 0 <= index <= 65535:
            raise ValueError(f"S{index} out of range")
        if not 0 <= value <= 255 and index < 1000:
            raise ValueError(f"S{index}={value} out of range")
        self.values[index] = value


# Profiles (AT.txt "Supported TTY Profiles").  Only the modem ones carry a
# modulation here; the rest are recorded so ATZ<n>/AT&F<n> round-trips and the
# caller can see which stack was asked for.
PROFILES: "dict[int, tuple[str, str | None]]" = {
    1: ("X.75/Transparent", None),
    2: ("V.110 synchronous", None),
    3: ("V.110 asynchronous", None),
    4: ("Synchronous modem with V.42/V.42bis", "v90"),
    5: ("Asynchronous modem up to V.90, V.42/V.42bis/MNP", "v90"),
    6: ("V.120 64k", None),
    7: ("V.120 56k", None),
    8: ("Bit-transparent B-channel", None),
    9: ("HDLC/Transparent (PPP)", None),
    10: ("HDLC/Transparent 56000", None),
    11: ("BTX", None),
    12: ("BTX", None),
    14: ("Autodetect B-channel protocol", "v90"),
    15: ("X.75 with V.42bis", None),
    16: ("PIAFS 1.0 32k", None),
    17: ("PIAFS 2.0 64k", None),
    18: ("PIAFS 2.1 32/64k", None),
}
DEFAULT_PROFILE = 5

# ATI<n> identification strings.  The real card answers from its own firmware;
# these name the emulator, because pretending to be a specific Diva build
# would make a capture ambiguous about what produced it.
INFO_STRINGS = {
    0: "Diva Server PRI (emulated)",
    1: "eicon-adsp-emu",
    2: "OK",
    3: "ADSP-2181 + MIPS under emulation",
    6: "",   # filled in from the last connection
}


class AtParser:
    """Line-oriented AT command parser with an escape-sequence data mode.

    Feed it terminal input with `feed()`; it returns the bytes to write back
    (echo and result codes) and appends anything it cannot do itself to
    `actions`.  In data mode `feed()` returns the payload to pass to the link
    instead, unless the escape sequence fires.
    """

    def __init__(self, clock=time.monotonic) -> None:
        self.clock = clock
        self.registers = Registers()
        self.mode = Mode.COMMAND
        self.actions: "list[AtAction]" = []

        self.echo = True
        self.quiet = False
        self.verbose = True
        self.progress = 4          # ATX
        self.connect_format = 1    # long CONNECT text when progress allows
        self.profile = DEFAULT_PROFILE

        self.guard_tone = 0
        self.fast_connect = 0
        self.flow_control = 3
        self.dtr_option = 2
        self.com_option = 0
        self.dcd_option = 1

        self.accepted_address = ""
        self.origination_address = ""
        self.stay_in_command_mode = False
        self.fax_class = 0
        self._fax_operation = ""
        self._fax_payload = bytearray()

        self.last_dialled = ""
        self.last_caller = ""
        self.last_connect_text = ""

        self._line = bytearray()
        self._modulation: "eicon_idi.ModemOptions | None" = None
        self._escape_count = 0
        self._escape_last = 0.0
        self._last_data = 0.0
        self.load_profile(DEFAULT_PROFILE)

    # -- configuration ----------------------------------------------------
    def load_profile(self, profile: "int | None" = None) -> None:
        """AT&F<n> / ATZ<n>: reset and select a predefined profile."""
        if profile is None:
            profile = self.profile
        if profile not in PROFILES:
            raise ValueError(f"no profile {profile}")
        self.profile = profile
        self.registers.reset()
        self.echo = True
        self.quiet = False
        self.verbose = True
        self.progress = 4
        self.guard_tone = 0
        self.fast_connect = 0
        self.fax_class = 0
        self._modulation = None
        _, modulation = PROFILES[profile]
        if modulation:
            self._modulation = eicon_idi.select_modulation(modulation,
                                                           automode=1)
        if profile == 9:
            # AT.txt note [b]: the PPP profile comes up numeric and echo-off.
            self.verbose = False
            self.echo = False

    def options(self) -> eicon_idi.ModemOptions:
        """The modem configuration the current AT state describes.

        Hand the result to `eicon_idi.build_cai()`.  Everything the parser
        tracks that has a CAI field lands here; everything else (flow control,
        DTR handling) belongs to the terminal side and is the caller's
        business.
        """
        if self._modulation is not None:
            opts = eicon_idi.ModemOptions(**vars(self._modulation))
        else:
            opts = eicon_idi.legacy_modem_options()

        regs = self.registers
        opts.s7 = regs[7]
        opts.s10 = regs[10]
        opts.guard_tone = {0: eicon_idi.DSP_CAI_MODEM_GUARD_TONE_NONE,
                           1: eicon_idi.DSP_CAI_MODEM_GUARD_TONE_550HZ,
                           2: eicon_idi.DSP_CAI_MODEM_GUARD_TONE_1800HZ,
                           }[self.guard_tone]
        opts.fast_connect_mode = self.fast_connect
        if regs[27] & 0x08:
            opts.line_taking |= eicon_idi.DSP_CAI_MODEM_DISABLE_ANSWER_TONE
        opts.reserved_modulation_options |= regs[172]
        opts.sdlc_prot_options = regs[91]
        opts.sdlc_address_a = regs[92]
        opts.valid = True
        return opts

    # -- terminal input ---------------------------------------------------
    def feed(self, data: bytes) -> "tuple[bytes, bytes]":
        """Consume terminal input.

        Returns `(to_terminal, to_link)`.  In command mode everything is
        parsed and `to_link` is empty; in data mode the reverse, except for
        the bytes the escape sequence swallows.
        """
        if self.mode is Mode.FAX_DATA:
            return self._feed_fax_data(data)
        if self.mode is Mode.DATA:
            return self._feed_data(data)
        return self._feed_command(data), b""

    def _feed_fax_data(self, data: bytes) -> "tuple[bytes, bytes]":
        """Accept a Class 1 DTE stream, delimited by DLE ETX.

        The terminal representation doubles literal DLE octets.  Once a
        complete transmit frame arrives it becomes an action for the media
        bridge; receive phases never accept terminal payload.
        """
        if not self._fax_operation.startswith("tx-"):
            return b"", b""
        pos = 0
        while pos < len(data):
            byte = data[pos]
            if byte != 0x10:
                self._fax_payload.append(byte)
                pos += 1
                continue
            if pos + 1 == len(data):
                # Keep a split DLE until the next serial read.
                self._fax_payload.append(byte)
                break
            escaped = data[pos + 1]
            if escaped == 0x10:
                self._fax_payload.append(0x10)
                pos += 2
                continue
            if escaped != 0x03:
                # Class 1 has no in-band commands beyond DLE DLE/DLE ETX.
                self._fax_payload.extend((byte, escaped))
                pos += 2
                continue
            self.actions.append(AtAction(ActionKind.FAX_SEND,
                                         fax_operation=self._fax_operation,
                                         fax_payload=bytes(self._fax_payload)))
            self._fax_payload.clear()
            self._fax_operation = ""
            self.mode = Mode.COMMAND
            return self.respond("OK"), b""
        return b"", b""

    def fax_receive(self, payload: bytes = b"", complete: bool = False,
                    success: bool = True, result: str | None = None) -> bytes:
        """Deliver one Class 1 receive fragment from the DSP media bridge."""
        if not self._fax_operation.startswith("rx-"):
            return b""
        escaped = payload.replace(b"\x10", b"\x10\x10")
        if not complete:
            return escaped
        self._fax_operation = ""
        self.mode = Mode.COMMAND
        if result is None:
            result = "OK" if success else "ERROR"
        return escaped + b"\x10\x03" + self.respond(result)

    def _feed_command(self, data: bytes) -> bytes:
        out = bytearray()
        cr = self.registers[3]
        lf = self.registers[4]
        bs = self.registers[5]
        for byte in data:
            if byte == bs:
                if self._line:
                    self._line.pop()
                    if self.echo:
                        out += bytes((bs, 0x20, bs))
                continue
            if byte == cr:
                if self.echo:
                    out += bytes((cr, lf))
                line = bytes(self._line)
                self._line.clear()
                out += self.execute(line)
                continue
            if byte == lf:
                continue
            if self.echo:
                out.append(byte)
            if len(self._line) < 512:
                self._line.append(byte)
        return bytes(out)

    def _feed_data(self, data: bytes) -> "tuple[bytes, bytes]":
        """Watch for +++ with its guard times; pass everything else through.

        The guard time is S12 in fiftieths of a second, before the first plus
        and after the last.  A run interrupted by any other byte is abandoned,
        which is what stops a file transfer containing "+++" from dropping to
        command mode.
        """
        escape = self.registers[2]
        if escape > 127:
            self._last_data = self.clock()
            return b"", data

        guard = self.registers[12] / 50.0
        now = self.clock()
        to_link = bytearray()
        for byte in data:
            if byte == escape:
                if self._escape_count == 0:
                    # The first plus needs a quiet line in front of it.
                    if guard and now - self._last_data < guard:
                        to_link.append(byte)
                        self._last_data = now
                        continue
                    self._escape_count = 1
                elif self._escape_count < 3:
                    self._escape_count += 1
                else:
                    to_link.append(byte)
                self._escape_last = now
                continue
            if self._escape_count:
                # A non-escape byte cancels the run; the held pluses were
                # data after all.
                to_link += bytes((escape,)) * self._escape_count
                self._escape_count = 0
            to_link.append(byte)
            self._last_data = now
        return b"", bytes(to_link)

    def poll(self) -> bytes:
        """Call periodically: completes the escape sequence's trailing guard.

        `+++` only takes effect after the line has been quiet for S12 again,
        so the transition cannot happen inside `feed()`.
        """
        if self.mode is not Mode.DATA or self._escape_count < 3:
            return b""
        guard = self.registers[12] / 50.0
        if guard and self.clock() - self._escape_last < guard:
            return b""
        self._escape_count = 0
        self.mode = Mode.COMMAND
        return self.respond("OK")

    # -- result codes -----------------------------------------------------
    def respond(self, code: str) -> bytes:
        """Format one result code, honouring ATQ, ATV and ATX."""
        if self.quiet:
            return b""
        numeric, text = RESULT_CODES[code]
        if self.progress == 0 and code in ("BUSY", "NO DIALTONE", "NO ANSWER"):
            # ATX0 reports only the minimum: a call that failed is NO CARRIER.
            numeric, text = RESULT_CODES["NO CARRIER"]
        return self._format(str(numeric) if not self.verbose else text)

    def _format(self, text: str) -> bytes:
        cr = bytes((self.registers[3],))
        lf = bytes((self.registers[4],))
        if self.verbose:
            return cr + lf + text.encode("ascii", "replace") + cr + lf
        return text.encode("ascii", "replace") + cr

    def ring(self, caller: str = "") -> bytes:
        """An incoming call.  Auto-answers when S0 is between 1 and 254."""
        self.last_caller = caller
        out = bytearray(self.respond("RING"))
        s0 = self.registers[0]
        if 1 <= s0 <= 254:
            self.actions.append(AtAction(ActionKind.ANSWER,
                                         options=self.options()))
        return bytes(out)

    def connected(self, tx_speed: int = 0, rx_speed: int = 0,
                  carrier: str = "", protocol: str = "",
                  compression: str = "") -> bytes:
        """Report a connection.  ATX and AT\\V pick how much detail."""
        self.mode = Mode.COMMAND if self.stay_in_command_mode else Mode.DATA
        self._last_data = self.clock()
        self._escape_count = 0
        if self.quiet:
            return b""
        if not self.verbose or self.progress == 0:
            self.last_connect_text = "CONNECT"
            return self.respond("CONNECT")
        if self.connect_format == 2:
            text = (f"CONNECT TX/RX {tx_speed}/{rx_speed}\r\n"
                    f"CARRIER {carrier}\r\nPROTOCOL {protocol}\r\n"
                    f"COMPRESSION {compression}")
        elif self.connect_format == 1 and carrier:
            text = f"CONNECT {carrier}/{protocol}/{tx_speed}:TX/{rx_speed}:RX"
        else:
            text = f"CONNECT {tx_speed}" if tx_speed else "CONNECT"
        self.last_connect_text = text
        INFO_STRINGS[6] = (f"Protocol {protocol or 'NONE'} "
                           f"TX {tx_speed} RX {rx_speed}")
        return self._format(text)

    def no_carrier(self) -> bytes:
        self.mode = Mode.COMMAND
        self._escape_count = 0
        return self.respond("NO CARRIER")

    # -- command execution ------------------------------------------------
    def execute(self, line: bytes) -> bytes:
        """Parse and run one command line, returning the terminal response."""
        text = line.decode("ascii", "replace").strip()
        if not text:
            return b""
        if text.upper() == "A/":
            # Repeat the last line, without re-echoing or re-storing it.
            text = getattr(self, "_previous", None)
            if text is None:
                return self.respond("ERROR")
        else:
            if text[:2].upper() != "AT":
                return self.respond("ERROR")
            text = text[2:]
            self._previous = text

        try:
            self._suppress_ok = False
            extra = self._run(text)
        except _AtError:
            return self.respond("ERROR")
        return (extra or b"") + (b"" if self._suppress_ok else self.respond("OK"))

    def _run(self, text: str) -> bytes:
        """Walk one command line.  Commands concatenate without separators."""
        out = bytearray()
        pos = 0
        while pos < len(text):
            char = text[pos]
            if char in " \t":
                pos += 1
                continue
            if char == ";":
                pos += 1
                continue
            pos = self._command(text, pos, out)
        return bytes(out)

    def _command(self, text: str, pos: int, out: bytearray) -> int:
        char = text[pos].upper()
        rest = text[pos + 1:]

        if char == "D":
            number, consumed = _dial_string(rest)
            self.last_dialled = number
            self.actions.append(AtAction(ActionKind.DIAL, number=number,
                                         options=self.options()))
            return pos + 1 + consumed

        if char == "A":
            self.actions.append(AtAction(ActionKind.ANSWER,
                                         options=self.options()))
            return pos + 1

        if char == "H":
            value, consumed = _number(rest, default=0)
            if value == 0:
                self.actions.append(AtAction(ActionKind.HANGUP))
                self.mode = Mode.COMMAND
            return pos + 1 + consumed

        if char == "O":
            value, consumed = _number(rest, default=0)
            if value in (0, 1):
                self.actions.append(AtAction(ActionKind.ONLINE))
                self.mode = Mode.DATA
                self._last_data = self.clock()
            return pos + 1 + consumed

        if char == "Z":
            value, consumed = _number(rest, default=self.profile)
            if value not in PROFILES:
                raise _AtError
            self.actions.append(AtAction(ActionKind.RESET, profile=value))
            self.load_profile(value)
            return pos + 1 + consumed

        if char == "E":
            value, consumed = _number(rest, default=0)
            self.echo = bool(value)
            return pos + 1 + consumed

        if char == "Q":
            value, consumed = _number(rest, default=0)
            self.quiet = bool(value)
            return pos + 1 + consumed

        if char == "V":
            value, consumed = _number(rest, default=0)
            self.verbose = bool(value)
            return pos + 1 + consumed

        if char == "X":
            value, consumed = _number(rest, default=4)
            if value not in (0, 1, 2, 3, 4):
                raise _AtError
            self.progress = value
            return pos + 1 + consumed

        if char == "I":
            value, consumed = _number(rest, default=0)
            text_out = INFO_STRINGS.get(value)
            if text_out is None:
                raise _AtError
            if text_out:
                out += self._format(text_out)
            return pos + 1 + consumed

        if char in "LMNY":
            # "Command accepted for compatibility reasons." (AT.txt)
            _, consumed = _number(rest, default=0)
            return pos + 1 + consumed

        if char == "S":
            return self._s_register(text, pos, out)

        if char == "&":
            return self._ampersand(text, pos, out)

        if char == "$":
            return self._dollar(text, pos, out)

        if char == "+":
            return self._plus(text, pos, out)

        raise _AtError

    def _s_register(self, text: str, pos: int, out: bytearray) -> int:
        index, consumed = _number(text[pos + 1:], default=None)
        if index is None:
            raise _AtError
        pos += 1 + consumed
        if pos < len(text) and text[pos] == "?":
            out += self._format(f"{self.registers[index]:03d}")
            return pos + 1
        if pos < len(text) and text[pos] == "=":
            pos += 1
            if pos < len(text) and text[pos] == "?":
                out += self._format(f"{self.registers[index]:03d}")
                return pos + 1
            value, consumed = _number(text[pos:], default=None)
            if value is None:
                raise _AtError
            try:
                self.registers[index] = value
            except ValueError as exc:
                raise _AtError from exc
            return pos + consumed
        raise _AtError

    def _ampersand(self, text: str, pos: int, out: bytearray) -> int:
        if pos + 1 >= len(text):
            raise _AtError
        char = text[pos + 1].upper()
        rest = text[pos + 2:]

        if char == "F":
            value, consumed = _number(rest, default=DEFAULT_PROFILE)
            if value not in PROFILES:
                raise _AtError
            self.load_profile(value)
            return pos + 2 + consumed
        if char == "G":
            value, consumed = _number(rest, default=0)
            if value not in (0, 1, 2):
                raise _AtError
            self.guard_tone = value
            return pos + 2 + consumed
        if char == "K":
            value, consumed = _number(rest, default=3)
            if value not in range(7):
                raise _AtError
            self.flow_control = value
            return pos + 2 + consumed
        if char == "D":
            value, consumed = _number(rest, default=2)
            if value not in range(4):
                raise _AtError
            self.dtr_option = value
            return pos + 2 + consumed
        if char == "Q":
            value, consumed = _number(rest, default=0)
            if value not in range(4):
                raise _AtError
            self.com_option = value
            return pos + 2 + consumed
        if char == "C":
            value, consumed = _number(rest, default=1)
            self.dcd_option = value
            return pos + 2 + consumed
        if char == "V":
            value, consumed = _number(rest, default=0)
            out += self._format(self._settings_dump())
            return pos + 2 + consumed
        raise _AtError

    def _dollar(self, text: str, pos: int, out: bytearray) -> int:
        if pos + 1 >= len(text) or text[pos + 1].upper() != "F":
            raise _AtError
        rest = text[pos + 2:]
        if rest.startswith("?"):
            out += self._format(str(self.fast_connect))
            return pos + 3
        value, consumed = _number(rest, default=0)
        if value not in range(5):
            raise _AtError
        self.fast_connect = value
        return pos + 2 + consumed

    def _plus(self, text: str, pos: int, out: bytearray) -> int:
        """The Diva-specific +I family, fax class selection and +MS.

        AT.txt spells the modulation command `+IE`; `+MS` is the name the same
        parameters go by on every other modem, and atp.c's handler is shared,
        so both are accepted here.
        """
        rest = text[pos + 1:]
        upper = rest.upper()

        if upper.startswith("MS"):
            return self._modulation_command(text, pos + 3, out)
        if upper.startswith("FCLASS"):
            return self._fax_class_command(text, pos + 7, out)
        for command, operation in (("FTH", "tx-hdlc"), ("FRH", "rx-hdlc"),
                                   ("FTM", "tx-data"), ("FRM", "rx-data")):
            if upper.startswith(command):
                return self._fax_media_command(text, pos + 1 + len(command),
                                               out, operation)
        if not upper.startswith("I"):
            raise _AtError
        if len(rest) < 2:
            raise _AtError
        sub = rest[1].upper()
        after = pos + 3   # past '+', the 'I' and the sub-command letter

        if sub == "E":
            return self._modulation_command(text, after, out)
        if sub == "A":
            value, consumed = _string(text[after:])
            self.accepted_address = value
            return after + consumed
        if sub == "O":
            value, consumed = _string(text[after:])
            self.origination_address = value
            return after + consumed
        if sub == "C":
            value, consumed = _number(text[after:], default=1)
            if value not in (0, 1):
                raise _AtError
            self.stay_in_command_mode = value == 0
            return after + consumed
        if sub in "BD":
            _, consumed = _number(text[after:], default=0)
            return after + consumed
        raise _AtError

    def _fax_class_command(self, text: str, pos: int, out: bytearray) -> int:
        """Handle the safe, pre-call portion of the EIA fax interface.

        `+FCLASS` is a terminal-side setting.  It must not itself configure a
        DSP running an active call, but it tells the next call setup that the
        host expects Class 1 rather than ordinary modem data.  The Class 1
        page commands deliberately remain errors until their media bridge is
        implemented.
        """
        if pos == len(text):
            raise _AtError
        if text[pos] == "?":
            out += self._format(str(self.fax_class))
            return pos + 1
        if text[pos] != "=":
            raise _AtError
        pos += 1
        if pos < len(text) and text[pos] == "?":
            out += self._format("0,1,2")
            return pos + 1
        value, consumed = _number(text[pos:], default=None)
        if value not in (0, 1, 2):
            raise _AtError
        self.fax_class = value
        return pos + consumed

    def _fax_media_command(self, text: str, pos: int, out: bytearray,
                           operation: str) -> int:
        """Enter a Class 1 modulation or HDLC phase.

        Modulation value 3 is V.21 channel 2 and is the universally required
        control-channel value.  The Eicon firmware decides the later V.34
        training rate through its T.30/V.8 fax path, so the DTE must not try
        to select it by a data-modem speed command.
        """
        if self.fax_class != 1:
            raise _AtError
        if pos >= len(text) or text[pos] != "=":
            raise _AtError
        value, consumed = _number(text[pos + 1:], default=None)
        if value is None or value < 0 or value > 146:
            raise _AtError
        self._fax_operation = operation
        self._fax_payload.clear()
        self.mode = Mode.FAX_DATA
        self.actions.append(AtAction(ActionKind.FAX_CONFIG,
                                     fax_operation=operation,
                                     fax_modulation=value))
        self._suppress_ok = True
        out += self._format("CONNECT")
        return pos + 1 + consumed

    def _modulation_command(self, text: str, pos: int, out: bytearray) -> int:
        rest = text[pos:]
        if rest.startswith("?"):
            opts = self._modulation
            if opts is not None and opts.index >= 0:
                name = eicon_idi.MOD2NORM[opts.index].name
                out += self._format(
                    f"{name},{opts.automode},{opts.min_rx},{opts.max_rx},"
                    f"{opts.min_tx},{opts.max_tx}")
            else:
                out += self._format("V90,1,300,56000,300,56000")
            return pos + 1
        if not rest.startswith("="):
            raise _AtError
        rest = rest[1:]
        pos += 1
        if rest.startswith("?"):
            names = ",".join(m.name for m in eicon_idi.MOD2NORM)
            out += self._format(names)
            return pos + 1

        end = len(rest)
        for i, char in enumerate(rest):
            if char == ";":
                end = i
                break
        spec = rest[:end]
        fields = [f.strip() for f in spec.split(",")]
        try:
            nums = [int(f) if f else 0 for f in fields[1:]]
        except ValueError as exc:
            raise _AtError from exc
        nums += [0] * (5 - len(nums))
        automode = nums[0] if len(fields) > 1 else 1
        try:
            self._modulation = eicon_idi.select_modulation(
                fields[0], automode=automode, min_rx=nums[1], max_rx=nums[2],
                min_tx=nums[3], max_tx=nums[4])
        except ValueError as exc:
            raise _AtError from exc
        return pos + end

    def _settings_dump(self) -> str:
        """AT&V: the current configuration, one line."""
        regs = " ".join(f"S{i}={self.registers[i]}"
                        for i in sorted(self.registers.values))
        modulation = "none"
        if self._modulation is not None and self._modulation.index >= 0:
            modulation = eicon_idi.MOD2NORM[self._modulation.index].name
        return (f"PROFILE {self.profile} E{int(self.echo)} Q{int(self.quiet)} "
                f"V{int(self.verbose)} X{self.progress} &G{self.guard_tone} "
                f"&K{self.flow_control} &D{self.dtr_option} "
                f"MODULATION {modulation}\r\n{regs}\r\n"
                f"LAST DIAL TO {self.last_dialled or '-'}\r\n"
                f"LAST RING FROM {self.last_caller or '-'}")


class _AtError(Exception):
    """Any parse or range failure; the caller turns it into ERROR."""


def _number(text: str, default: "int | None" = 0) -> "tuple[int | None, int]":
    """Read a decimal argument, returning (value, characters consumed)."""
    i = 0
    while i < len(text) and text[i].isdigit():
        i += 1
    if i == 0:
        return default, 0
    return int(text[:i]), i


def _string(text: str) -> "tuple[str, int]":
    """Read a bare argument up to the next semicolon or end of line."""
    end = text.find(";")
    if end < 0:
        end = len(text)
    return text[:end].strip(), end


def _dial_string(text: str) -> "tuple[str, int]":
    """Parse ATD's argument (AT.txt:15).

    `<number>[|<subaddress>][^56k][+i<y>|+p=btx]`, with T and P accepted and
    ignored -- there is no dial tone or pulse on a digital span.  Dial
    modifiers that only pace an analogue line (`,`, `W`, `@`, `!`) are
    dropped, and the string ends at a `;` so `ATD123;S0=1` still works.
    """
    number = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == ";":
            break
        if char in "TPtp" and not number:
            i += 1
            continue
        if char in " -()\t,W@!wRr":
            i += 1
            continue
        number.append(char)
        i += 1
    return "".join(number), i
