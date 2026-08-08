#!/usr/bin/env python3
"""IDI payload construction, ported from the divas4linux driver.

The shim used to hand-build its CAI and DLC blocks from `add_b1()` and
`add_modem_b23()` in `kernel/message.c`, which is the CAPI path.  That path
can only express a maximum bit rate: everything else -- which modulations are
offered, the negotiation mode, the guard tone, the per-direction speed limits
-- is reachable only through the private V.18/VOWN extension, so the shim
wrote those bytes by hand and mostly left them zero.

The tty driver reaches the same firmware fields directly.  `putcai()`
(`tty_module/isdn.c:1209`) is the complete builder and `atPlusMS()`
(`tty_module/atp.c:1879`) is where a modulation name becomes a disabled mask,
an enabled mask and a pair of speed windows.  Both are ported here, so the
payloads this project sends are derived from a shipped implementation rather
than guessed field by field.

Nothing here imports Unicorn or touches the emulator: it is bytes in, bytes
out, so it can be unit-tested without a card or a firmware image.

Two conventions are worth stating because they have caused mistakes before:

* A payload is a list of `{code, length, data}` triples with a single zero
  code byte terminating it, as `add_ie()` builds it.  `idi_parameters()` is
  the only thing that should ever produce that framing.
* The CAI is quoted throughout in the *driver's* indexing, where `cai[0]` is
  the length byte.  `build_cai()` returns `cai[1:]` and lets
  `idi_parameters()` supply the length, so a field the driver calls `cai[n]`
  is at index `n - 1` in the returned bytes.
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from enum import Enum

# ---------------------------------------------------------------------------
# Information element codes (kernel/pc.h)
# ---------------------------------------------------------------------------
IDI_SIN = 0x01    # service indicator (codeset 6)
IDI_BC = 0x04     # bearer capability
IDI_CAI = 0x10    # call identity: the B1/DSP configuration
IDI_CHI = 0x18    # channel identification
IDI_LLI = 0x19    # logical link id
IDI_DLC = 0x20    # data link layer configuration
IDI_NLC = 0x21    # network layer configuration (carries T30_INFO on a fax)
IDI_UID = 0x2D    # user id
IDI_OAD = 0x6C    # originating address
IDI_OSA = 0x6D    # originating sub-address
IDI_CPN = 0x70    # called party number (the IDI spec calls this DAD)
IDI_DSA = 0x71    # destination sub-address
IDI_LLC = 0x7C    # low layer compatibility
IDI_ESC = 0x7F    # escape extension
SHIFT = 0x90      # codeset shift

# B1/B2/B3 protocol ids (tty_module/isdn.h)
B1_MODEM_ASYNC = 0x11
B1_MODEM_SYNC = 0x12
B2_XPARENT_OUT = 0x02
B2_XPARENT_IN = 0x03
B2_V42_IN = 0x09
B2_V42_OUT = 0x0A
B3_XPARENT = 0x04
# The fax row of the protocol map (isdn.c:273): B1_T30 pairs with B2_T30_in/out
# and B3_T30. B2_T30_in/out share their values with B2_XPARENT_in/out -- the
# same two numbers mean different things depending on the B1 resource, which
# is why they are spelled separately here rather than aliased.
B1_T30 = 0x10
B2_T30_OUT = 0x02
B2_T30_IN = 0x03
B3_T30 = 0x06

# LLI flags (tty_module/isdn.c:1495).  The driver sends all three on every
# modem NL ASSIGN; the shim used to send OK_FC alone.
LLI_OK_FC = 0x01        # don't block on flow control
LLI_CMA = 0x10
LLI_NO_CANCEL = 0x20
LLI_RX_DMA = 0x40
LLI_DEFAULT = LLI_OK_FC | LLI_CMA | LLI_NO_CANCEL

ISDN_MAX_FRAME = 2048   # tty_module/isdn_if.h
DEFAULT_MAX_DATA_LENGTH = 2138  # dlc_def in tty_module/isdn.c:1429

# ---------------------------------------------------------------------------
# DSP CAI constants (tty_module/mdm_msg.h)
# ---------------------------------------------------------------------------
DSP_CAI_HARDWARE_MODEM_ASYNC = 0x11
# Same CAI byte as the symbol-rate disables (mdm_msg.h).
DSP_CAI_MODEM_EXTENDED_LEC = 0x80
# Low six bits of the same byte (mdm_msg.h), by symbol rate in baud.
DSP_CAI_MODEM_DISABLE_SYMBOLS = {2400: 0x01, 2743: 0x02, 2800: 0x04,
                                 3000: 0x08, 3200: 0x10, 3429: 0x20}
DSP_CAI_HARDWARE_MODEM_SYNC = 0x12
DSP_CAI_HARDWARE_MASK = 0x3F
DSP_CAI_ENABLE_INFO_INDICATIONS = 0x80

DSP_CAI_RATE_ADAPTATION_19200 = 0x06

# cai[3]: async framing
DSP_CAI_ASYNC_PARITY_ENABLE = 0x01
DSP_CAI_ASYNC_PARITY_SPACE = 0x00
DSP_CAI_ASYNC_PARITY_ODD = 0x02
DSP_CAI_ASYNC_PARITY_EVEN = 0x04
DSP_CAI_ASYNC_PARITY_MARK = 0x06
DSP_CAI_ASYNC_ONE_STOP_BIT = 0x00
DSP_CAI_ASYNC_TWO_STOP_BITS = 0x20
DSP_CAI_ASYNC_CHAR_LENGTH_8 = 0x00
DSP_CAI_ASYNC_CHAR_LENGTH_7 = 0x40
DSP_CAI_ASYNC_CHAR_LENGTH_6 = 0x80
DSP_CAI_ASYNC_CHAR_LENGTH_5 = 0xC0

# cai[7]: line taking options
DSP_CAI_MODEM_LEASED_LINE_MODE = 0x01
DSP_CAI_MODEM_4_WIRE_OPERATION = 0x02
DSP_CAI_MODEM_DISABLE_BUSY_DETECT = 0x04
DSP_CAI_MODEM_DISABLE_CALLING_TONE = 0x08
DSP_CAI_MODEM_DISABLE_ANSWER_TONE = 0x10
DSP_CAI_MODEM_ENABLE_DIAL_TONE_DET = 0x20
DSP_CAI_MODEM_USE_POTS_INTERFACE = 0x40
DSP_CAI_MODEM_FORCE_RAY_TAYLOR_FAX = 0x80

# cai[8]: negotiation (low 3 bits) and guard tone (top 2)
DSP_CAI_MODEM_NEGOTIATE_HIGHEST = 0x00
DSP_CAI_MODEM_NEGOTIATE_DISABLED = 0x01
DSP_CAI_MODEM_NEGOTIATE_IN_CLASS = 0x02
DSP_CAI_MODEM_NEGOTIATE_V100 = 0x03
DSP_CAI_MODEM_NEGOTIATE_V8 = 0x04
DSP_CAI_MODEM_NEGOTIATE_V8BIS = 0x05
DSP_CAI_MODEM_NEGOTIATE_MASK = 0x07
DSP_CAI_MODEM_GUARD_TONE_NONE = 0x00
DSP_CAI_MODEM_GUARD_TONE_550HZ = 0x40
DSP_CAI_MODEM_GUARD_TONE_1800HZ = 0x80

# cai[9]: modulation options
DSP_CAI_MODEM_DISABLE_RETRAIN = 0x01
DSP_CAI_MODEM_DISABLE_STEPDOWN = 0x02
DSP_CAI_MODEM_DISABLE_SPLIT_SPEED = 0x04
DSP_CAI_MODEM_SHORT_ANSWER_TONE = 0x08
DSP_CAI_MODEM_ALLOW_RDL_TEST_LOOP = 0x10
DSP_CAI_MODEM_DISABLE_STEPUP = 0x20
DSP_CAI_MODEM_DISABLE_FLUSH_TIMER = 0x40
DSP_CAI_MODEM_REVERSE_DIRECTION = 0x80

# cai[10]: disabled modulations, low byte
DSP_CAI_MODEM_DISABLE_V21 = 0x01
DSP_CAI_MODEM_DISABLE_V23 = 0x02
DSP_CAI_MODEM_DISABLE_V22 = 0x04
DSP_CAI_MODEM_DISABLE_V22BIS = 0x08
DSP_CAI_MODEM_DISABLE_V32 = 0x10
DSP_CAI_MODEM_DISABLE_V32BIS = 0x20
DSP_CAI_MODEM_DISABLE_V34 = 0x40
DSP_CAI_MODEM_DISABLE_V90 = 0x80
# cai[11]: disabled modulations, high byte
DSP_CAI_MODEM_DISABLE_BELL103 = 0x01
DSP_CAI_MODEM_DISABLE_BELL212A = 0x02
DSP_CAI_MODEM_DISABLE_VFC = 0x04
DSP_CAI_MODEM_DISABLE_K56FLEX = 0x08
DSP_CAI_MODEM_DISABLE_X2 = 0x10

# cai[12]: enabled modulations
DSP_CAI_MODEM_ENABLE_V29FDX = 0x01
DSP_CAI_MODEM_ENABLE_V33 = 0x02
DSP_CAI_MODEM_ENABLE_V90A = 0x04
DSP_CAI_MODEM_ENABLE_V22FC = 0x08
DSP_CAI_MODEM_ENABLE_V22BISFC = 0x10
DSP_CAI_MODEM_ENABLE_V29FC = 0x20
DSP_CAI_MODEM_ENABLE_V27FC = 0x40
DSP_CAI_MODEM_ENABLE_V34FC = 0x80

# cai[24]: speaker
DSP_CAI_MODEM_SPEAKER_OFF = 0x00
DSP_CAI_MODEM_SPEAKER_DURING_TRAIN = 0x01
DSP_CAI_MODEM_SPEAKER_TIL_CONNECT = 0x02
DSP_CAI_MODEM_SPEAKER_ALWAYS_ON = 0x03
DSP_CAI_MODEM_SPEAKER_VOLUME_MIN = 0x00
DSP_CAI_MODEM_SPEAKER_VOLUME_LOW = 0x04
DSP_CAI_MODEM_SPEAKER_VOLUME_HIGH = 0x08
DSP_CAI_MODEM_SPEAKER_VOLUME_MAX = 0x0C

# Reserved modulation options and modulations (atp.c:1925)
DIVA_MDM_RESERVED_MOD_OPTION_EMPTY_FRAMES = 0x0001
DIVA_MDM_RESERVED_MOD_OPTION_MULTIMODING = 0x0002
DIVA_MDM_RESERVED_MOD_OPTION_SHIELD_EMPTY_FRM = 0x0004
DIVA_MDM_RESERVED_MODULATION_V23_OFF_HOOK = 0x00000002
DIVA_MDM_RESERVED_MODULATION_V23_ON_HOOK = 0x00000004
DIVA_MDM_RESERVED_MODULATION_V23_AUTO_FRAMER = 0x00000008
DIVA_MDM_RESERVED_MODULATION_BELL202_CID = 0x00000010
DIVA_MDM_RESERVED_MODULATION_TELENOT = 0x00000020
DIVA_MDM_RESERVED_MODULATION_BELL202_POS = 0x00000040
DIVA_MDM_RESERVED_MODULATION_BELL103_SIA = 0x00000080
DIVA_MDM_RESERVED_MODULATION_V23_REVERSE = 0x00000100

# ---------------------------------------------------------------------------
# DLC modem protocol negotiation options (tty_module/mdm_msg.h)
# ---------------------------------------------------------------------------
DLC_MODEMPROT_DISABLE_V42_V42BIS = 0x01
DLC_MODEMPROT_DISABLE_MNP_MNP5 = 0x02
DLC_MODEMPROT_REQUIRE_PROTOCOL = 0x04
DLC_MODEMPROT_DISABLE_V42_DETECT = 0x08
DLC_MODEMPROT_DISABLE_COMPRESSION = 0x10
DLC_MODEMPROT_DISABLE_SDLC = 0x40

SDLC_L2_OPTION_REVERSE_ESTABLISHEMENT = 0x01
SDLC_L2_OPTION_SINGLE_DATA_PACKETS = 0x02


def idi_parameters(*params: "tuple[int, bytes]") -> bytes:
    """Encode an IDI request payload the way add_ie() (message.c) does.

    Each parameter is a {code, length, data} triple and the list ends with a
    single zero code byte -- add_ie() writes a terminating 0 after every
    parameter and backs over it when the next one is appended.
    """
    out = bytearray()
    for code, data in params:
        if len(data) > 255:
            raise ValueError(f"IE 0x{code:02x} is {len(data)} bytes, max 255")
        out += bytes((code, len(data)))
        out += data
    out.append(0)
    return bytes(out)


def parse_idi_parameters(payload: bytes) -> "list[tuple[int, bytes]]":
    """Inverse of idi_parameters(), for decoding what the card sends back."""
    out: "list[tuple[int, bytes]]" = []
    i = 0
    while i < len(payload):
        code = payload[i]
        if code == 0:
            break
        if code & 0xF0 == SHIFT:
            # A codeset shift ends the part of the message that uses this
            # framing; what follows it (the CALL_REQ service pair, for one) is
            # codeset-6 and is not {code, length, data}.
            break
        if i + 1 >= len(payload):
            raise ValueError("truncated IE header")
        length = payload[i + 1]
        data = payload[i + 2:i + 2 + length]
        if len(data) != length:
            raise ValueError(f"IE 0x{code:02x} truncated: "
                             f"want {length}, have {len(data)}")
        out.append((code, bytes(data)))
        i += 2 + length
    return out


# ---------------------------------------------------------------------------
# Modem configuration
# ---------------------------------------------------------------------------
@dataclass
class ModemOptions:
    """The subset of diva_modem_options_t that putcai() and add_b23() read.

    `valid` is the driver's "the application configured a modulation" flag.
    putcai() keys almost every optional CAI field off it, so a default-
    constructed ModemOptions produces the short six-byte CAI and an explicit
    one produces the full descriptor.
    """
    valid: bool = False
    index: int = -1
    automode: int = 1

    disabled: int = 0            # cai[10..11], 16-bit
    enabled: int = 0             # cai[12]
    min_tx: int = 0              # cai[13..14]
    max_tx: int = 0              # cai[15..16]
    min_rx: int = 0              # cai[17..18]
    max_rx: int = 0              # cai[19..20]

    negotiation: int = 0         # cai[8], low 3 bits
    guard_tone: int = 0          # cai[8], top 2 bits
    line_taking: int = 0         # cai[7]
    modulation_options: int = 0  # cai[9]
    retrain: int = 0             # cai[9], OR'd with modulation_options
    speaker: int = 0             # cai[24]
    s7: int = 0                  # cai[25], carrier wait seconds
    s10: int = 0                 # cai[26], carrier loss hangup, 0.1s units

    bell: int = 0
    bell_selected: int = 0
    fast_connect_mode: int = 0
    fast_connect_selected: int = 0

    reserved_modulation_options: int = 0
    reserved_modulation: int = 0

    framing_valid: bool = False
    framing_cai: int = 0         # cai[3]

    # B2 configuration, consumed by nl_assign_payload() rather than the CAI.
    disable_error_control: int = 0
    protocol_options: int = 0
    sdlc_prot_options: int = 0
    sdlc_address_a: int = 0x30

    def configured(self) -> bool:
        """putcai()'s recurring "did the application set anything" test."""
        return bool(self.valid or self.guard_tone or self.modulation_options
                    or self.retrain or self.reserved_modulation_options
                    or self.reserved_modulation or self.s7 or self.s10
                    or self.bell or self.fast_connect_mode)


@dataclass
class Modulation:
    """One row of atp.c's mod2norm[]."""
    numeric: int
    name: str
    max_tx: int
    disabled: int
    enabled: int
    tx_map: str
    rx_map: str
    modulation_options: int = 0
    reserved_modulation_options: int = 0
    reserved_modulation: int = 0
    group: int = 0


# Speed maps (atp.c:1887).  `lower` chains the fallbacks that automode allows;
# v90 is the one map validated by a rule rather than a list.
_SPEED_MAPS: "dict[str, tuple[tuple[int, ...], str | None]]" = {
    "v34": ((33600, 31200, 28800, 26400, 24000, 21600, 19200, 16800, 14400,
             12000, 9600, 7200, 4800, 2400), "v32b"),
    "v32b": ((14400, 12000, 9600, 7200, 4800), "v32"),
    "v32": ((9600, 4800), "v22b"),
    "v22b": ((2400, 1200), "v22"),
    "v22": ((1200,), "v21"),
    "v21": ((300,), None),
    "v23": ((1200,), None),
    "b103": ((300,), None),
    "b212": ((1200,), None),
    "v22f": ((1200,), None),
    "v22bf": ((2400, 1200), "v22f"),
    "v29f": ((9600, 7200, 4800, 2400), "v22bf"),
    "telenot": ((10,), None),
    "user": ((64000,), None),
    "v90": ((), "v34"),   # empty list: validated by _check_v90_speed instead
}


def _check_v90_speed(speed: int) -> bool:
    """diva_check_v90_speed(): the V.90 downstream rate ladder."""
    if not 28000 <= speed <= 56000:
        return False
    rem = speed % 4000
    return rem == 0 or 1300 <= rem <= 1333 or 2600 <= rem <= 2667


def check_speed(map_name: str, speed: int, automode: int) -> bool:
    """diva_check_speed(): is `speed` reachable under this modulation?

    Zero means "unspecified" and is always accepted.  With automode the
    fallback maps are searched too, which is what makes `AT+IE=v90,1,,33600`
    legal while `AT+IE=v90,0,,33600` is not.
    """
    if not speed:
        return True
    if map_name == "v90":
        ok = _check_v90_speed(speed)
    else:
        ok = speed in _SPEED_MAPS[map_name][0]
    if not ok and automode:
        lower = _SPEED_MAPS[map_name][1]
        if lower:
            return check_speed(lower, speed, automode)
    return ok


# mod2norm[] (atp.c:1948), in table order.  Order is load-bearing: automode
# disables everything *above* the selected row, so the table must stay sorted
# fastest-first.
MOD2NORM: "tuple[Modulation, ...]" = (
    Modulation(12, "v90a", 56000, DSP_CAI_MODEM_DISABLE_V90,
               DSP_CAI_MODEM_ENABLE_V90A, "v34", "v90"),
    Modulation(12, "v90d", 56000, DSP_CAI_MODEM_DISABLE_V90, 0, "v90", "v34"),
    Modulation(12, "v90", 56000, DSP_CAI_MODEM_DISABLE_V90, 0, "v90", "v34"),
    Modulation(11, "v34", 33600, DSP_CAI_MODEM_DISABLE_V34, 0, "v34", "v34"),
    Modulation(10, "v32b", 14400, DSP_CAI_MODEM_DISABLE_V32BIS, 0,
               "v32b", "v32b"),
    Modulation(9, "v32", 9600, DSP_CAI_MODEM_DISABLE_V32, 0, "v32", "v32"),
    Modulation(2, "v22b", 2400, DSP_CAI_MODEM_DISABLE_V22BIS, 0,
               "v22b", "v22b"),
    Modulation(1, "v22", 1200, DSP_CAI_MODEM_DISABLE_V22, 0, "v22", "v22"),
    Modulation(3, "v23c", 1200, DSP_CAI_MODEM_DISABLE_V23, 0, "v23", "v23"),
    Modulation(3, "v23", 1200, DSP_CAI_MODEM_DISABLE_V23, 0, "v23", "v23"),
    Modulation(0, "v21", 300, DSP_CAI_MODEM_DISABLE_V21, 0, "v21", "v21"),
    Modulation(69, "b212a", 1200, DSP_CAI_MODEM_DISABLE_BELL212A << 8, 0,
               "b212", "b212"),
    Modulation(64, "b103", 300, DSP_CAI_MODEM_DISABLE_BELL103 << 8, 0,
               "b103", "b103"),
    Modulation(9, "v29f", 9600, 0, DSP_CAI_MODEM_ENABLE_V29FC,
               "v29f", "v29f", group=2),
    Modulation(2, "v22bf", 2400, 0, DSP_CAI_MODEM_ENABLE_V22BISFC,
               "v22bf", "v22bf", group=2),
    Modulation(1, "v22f", 1200, 0, DSP_CAI_MODEM_ENABLE_V22FC,
               "v22f", "v22f", group=2),
    Modulation(14, "v23hdx", 1200, 0, 0, "b212", "b212",
               modulation_options=DSP_CAI_MODEM_DISABLE_FLUSH_TIMER,
               reserved_modulation=DIVA_MDM_RESERVED_MODULATION_V23_OFF_HOOK,
               group=1),
    Modulation(15, "v23hdxon", 1200, 0, 0, "b212", "b212",
               modulation_options=DSP_CAI_MODEM_DISABLE_FLUSH_TIMER,
               reserved_modulation=DIVA_MDM_RESERVED_MODULATION_V23_ON_HOOK,
               group=1),
    Modulation(19, "v23s", 1200, 0, 0, "b212", "b212",
               reserved_modulation=DIVA_MDM_RESERVED_MODULATION_V23_AUTO_FRAMER,
               group=1),
    Modulation(204, "b202cid", 1200, 0, 0, "b212", "b212",
               modulation_options=DSP_CAI_MODEM_DISABLE_FLUSH_TIMER,
               reserved_modulation=DIVA_MDM_RESERVED_MODULATION_BELL202_CID,
               group=1),
    Modulation(205, "telenot", 1200, 0, 0, "telenot", "telenot",
               reserved_modulation=DIVA_MDM_RESERVED_MODULATION_TELENOT,
               group=1),
    Modulation(206, "b202pos", 1200, 0, 0, "b212", "b212",
               modulation_options=DSP_CAI_MODEM_DISABLE_FLUSH_TIMER,
               reserved_modulation=DIVA_MDM_RESERVED_MODULATION_BELL202_POS,
               group=1),
    Modulation(207, "b103sia", 300, 0, 0, "b103", "b103",
               modulation_options=DSP_CAI_MODEM_DISABLE_FLUSH_TIMER,
               reserved_modulation_options=DIVA_MDM_RESERVED_MOD_OPTION_MULTIMODING,
               reserved_modulation=DIVA_MDM_RESERVED_MODULATION_BELL103_SIA,
               group=1),
    Modulation(208, "v23r", 1200, 0, 0, "b212", "b212",
               reserved_modulation=DIVA_MDM_RESERVED_MODULATION_V23_REVERSE,
               group=1),
) + tuple(
    Modulation(216 + n, f"user{16 + n}", 64000, 0, 0, "user", "user",
               reserved_modulation=0x00010000 << n, group=3 + n)
    for n in range(8)
)

def _or_disabled(table: "tuple[Modulation, ...]") -> int:
    used = 0
    for mod in table:
        used |= mod.disabled
    return used


# ~(union of every disable bit the table uses).  atp.c ORs this into any
# non-empty disabled mask, so selecting a modulation also switches off the
# modulations no table row names -- V.FC, K56flex and X2 among them.  That is
# a real behavioural difference from the shim's hand-built CAI, which left
# them enabled.
UNUSED_MODULATIONS = ~_or_disabled(MOD2NORM) & 0xFFFF


def find_modulation(spec: "str | int") -> int:
    """Index into MOD2NORM by name or by AT +IE numeric id.

    Name matching is longest-prefix, as atp.c does it, so "v90a" wins over
    "v90".  Raises ValueError if nothing matches.
    """
    if isinstance(spec, int):
        for i, mod in enumerate(MOD2NORM):
            if mod.numeric == spec:
                return i
        raise ValueError(f"no modulation with numeric id {spec}")
    text = spec.strip().lower()
    best = -1
    best_len = 0
    for i, mod in enumerate(MOD2NORM):
        if text.startswith(mod.name) and len(mod.name) > best_len:
            best, best_len = i, len(mod.name)
    if best < 0:
        raise ValueError(f"unknown modulation {spec!r}")
    return best


def select_modulation(spec: "str | int", automode: int = 1,
                      min_rx: int = 0, max_rx: int = 0,
                      min_tx: int = 0, max_tx: int = 0,
                      base: "ModemOptions | None" = None) -> ModemOptions:
    """atPlusMS(): turn a modulation choice into a ModemOptions.

    This is the whole of `AT+IE=<mod>[,<automode>[,<min_rx>,<max_rx>,<min_tx>,
    <max_tx>]]`.  With `automode` the card may fall back to slower modulations
    and only the ones *faster* than the selection are disabled; without it,
    every other modulation is disabled and the card must connect at the named
    one or not at all.

    Raises ValueError on a speed the modulation cannot reach or on an
    inverted min/max, which is where atp.c returns R_ERROR.
    """
    index = find_modulation(spec)
    mod = MOD2NORM[index]
    if automode not in (0, 1):
        raise ValueError("automode must be 0 or 1")

    for speed, map_name in ((min_rx, mod.rx_map), (max_rx, mod.rx_map),
                            (min_tx, mod.tx_map), (max_tx, mod.tx_map)):
        if not check_speed(map_name, speed, automode):
            raise ValueError(f"{speed} is not a valid {mod.name} speed")
    if (min_rx > max_rx and min_rx and max_rx) or \
       (min_tx > max_tx and min_tx and max_tx):
        raise ValueError("minimum speed above maximum")

    opts = base or ModemOptions()
    opts.disabled = 0
    opts.enabled = 0
    opts.min_rx = opts.max_rx = opts.min_tx = opts.max_tx = 0
    opts.bell_selected = 0
    opts.fast_connect_selected = 0
    # atPlusMS preserves the flush-timer bit that ATx set and replaces the
    # rest of the modulation options from the table.
    opts.retrain = ((opts.retrain & ~DSP_CAI_MODEM_DISABLE_FLUSH_TIMER)
                    | mod.modulation_options)
    opts.reserved_modulation_options = 0
    opts.reserved_modulation = 0

    if automode:
        # Everything in the same group is offered.
        for other in MOD2NORM:
            if other.group == mod.group:
                opts.enabled |= other.enabled
                opts.reserved_modulation_options |= other.reserved_modulation_options
                opts.reserved_modulation |= other.reserved_modulation
    else:
        opts.enabled = mod.enabled
        opts.reserved_modulation_options = mod.reserved_modulation_options
        opts.reserved_modulation = mod.reserved_modulation

    others = MOD2NORM[:index] if automode else MOD2NORM
    for other in others:
        if mod.disabled != other.disabled:
            opts.disabled |= other.disabled
        if mod.enabled != other.enabled:
            opts.enabled &= ~other.enabled
    opts.enabled &= 0xFF

    if opts.disabled:
        opts.disabled |= UNUSED_MODULATIONS
    opts.disabled &= 0xFFFF

    opts.min_rx, opts.max_rx = min_rx, max_rx
    opts.min_tx, opts.max_tx = min_tx, max_tx
    opts.automode = automode
    opts.index = index
    opts.valid = True
    return opts


def legacy_modem_options(max_bit_rate: int = 56000) -> ModemOptions:
    """The configuration this project has been sending since Session 89.

    A bare speed ceiling applied to both directions and no modulation
    selection at all.  It is kept as a named function because it is *not*
    expressible through select_modulation(): the driver's v90 row limits the
    receive direction to the V.34 speed map, so `AT+IE=v90,1,,56000` is an
    error, while this asks the card for 56000 in both directions.

    Whether the firmware minds an impossible Rx ceiling is untested.  It is
    the default only so that adopting this module changes no bytes on the
    known-good V.90 path.
    """
    return ModemOptions(valid=True, max_tx=max_bit_rate, max_rx=max_bit_rate)


def framing_cai(data_bits: int = 8, parity: str = "N",
                stop_bits: int = 1) -> int:
    """atPlusMF(): the async framing byte, cai[3]."""
    value = 0
    value |= {8: DSP_CAI_ASYNC_CHAR_LENGTH_8,
              7: DSP_CAI_ASYNC_CHAR_LENGTH_7,
              6: DSP_CAI_ASYNC_CHAR_LENGTH_6,
              5: DSP_CAI_ASYNC_CHAR_LENGTH_5}[data_bits]
    parity_bits = {"N": 0,
                   "S": DSP_CAI_ASYNC_PARITY_ENABLE | DSP_CAI_ASYNC_PARITY_SPACE,
                   "O": DSP_CAI_ASYNC_PARITY_ENABLE | DSP_CAI_ASYNC_PARITY_ODD,
                   "E": DSP_CAI_ASYNC_PARITY_ENABLE | DSP_CAI_ASYNC_PARITY_EVEN,
                   "M": DSP_CAI_ASYNC_PARITY_ENABLE | DSP_CAI_ASYNC_PARITY_MARK}
    value |= parity_bits[parity.upper()]
    if stop_bits == 2:
        value |= DSP_CAI_ASYNC_TWO_STOP_BITS
    elif stop_bits != 1:
        raise ValueError("stop_bits must be 1 or 2")
    return value


def build_cai(options: "ModemOptions | None" = None,
              b1_resource: int = DSP_CAI_HARDWARE_MODEM_ASYNC,
              rate_adaptation: int = 0,
              max_frame: int = 0,
              min_length: int = 26) -> bytes:
    """putcai(), modem branch.  Returns the driver's cai[1:].

    The driver grows the descriptor field by field and stops at the last one
    the application actually set, so an unconfigured modem gets six bytes and
    a fully configured one gets thirty-three.  `min_length` pads back up to
    the twenty-six bytes `add_b1()` always sends on the CAPI path, which is
    what the firmware has been accepting from this project so far -- lowering
    it changes the descriptor the card sees.

    Two fields differ between the references and both defaults here follow
    the CAPI path, so that a call with no ModemOptions produces exactly the
    bytes this project has been sending since the CAI was first corrected:

    * `rate_adaptation` (cai[2]) is `B1_resource >> 8`, i.e. zero, in
      `add_b1()`; the tty driver's MODEM template holds
      DSP_CAI_RATE_ADAPTATION_19200.
    * `max_frame` (cai[5..6]) is the application's MaxDataLength in
      `add_b1()` and ISDN_MAX_FRAME in the tty driver.  The shim has been
      sending zero.

    Neither has been tested against the card with a non-zero value.  Change
    them deliberately and one at a time.

    The one deliberate divergence from putcai() is that the padding is
    explicit zeroes.  The driver copies a seven-byte template into a stack
    buffer and then writes past it, so bytes it skips are whatever the stack
    held; every field it skips is one it also declines to count in the
    length, and the firmware reads only up to the length, so the difference is
    unobservable -- but it would not be reproducible here.
    """
    opts = options or ModemOptions()
    cai = bytearray(34)          # cai[0..33]; cai[0] is the length
    cai[1] = b1_resource & 0xFF
    cai[2] = rate_adaptation & 0xFF
    cai[3] = 0
    cai[4] = 0
    struct.pack_into("<H", cai, 5, max_frame & 0xFFFF)
    length = 6

    if opts.framing_valid:
        cai[3] = opts.framing_cai & 0xFF

    if opts.configured() or opts.line_taking:
        line_taking = opts.line_taking
        if opts.fast_connect_mode == 1:
            line_taking |= (DSP_CAI_MODEM_DISABLE_ANSWER_TONE
                            | DSP_CAI_MODEM_DISABLE_CALLING_TONE)
        cai[7] = line_taking & 0xFF
        length += 1

    if opts.configured():
        cai[8] = (opts.negotiation | opts.guard_tone) & 0xFF
        length += 1

    if opts.configured():
        disabled = opts.disabled
        enabled = opts.enabled
        if opts.bell and not opts.bell_selected:
            disabled |= ((DSP_CAI_MODEM_DISABLE_BELL212A << 8)
                         | (DSP_CAI_MODEM_DISABLE_BELL103 << 8))
        if opts.fast_connect_mode and not opts.fast_connect_selected:
            enabled = _apply_fast_connect(opts.fast_connect_mode,
                                          disabled, enabled)

        cai[9] = (opts.modulation_options | opts.retrain) & 0xFF
        length += 1
        cai[10] = disabled & 0xFF
        cai[11] = (disabled >> 8) & 0xFF
        length += 2

        if (opts.min_tx or opts.max_tx or opts.min_rx or opts.max_rx
                or enabled or opts.s7 or opts.s10
                or opts.reserved_modulation_options or opts.reserved_modulation):
            cai[12] = enabled & 0xFF
            length += 1

            if (opts.min_tx or opts.max_tx or opts.min_rx or opts.max_rx
                    or opts.s7 or opts.s10 or opts.reserved_modulation_options
                    or opts.reserved_modulation):
                struct.pack_into("<H", cai, 13, opts.min_tx & 0xFFFF)
                length += 2

                if (opts.max_tx or opts.min_rx or opts.max_rx or opts.s7
                        or opts.s10 or opts.reserved_modulation_options
                        or opts.reserved_modulation):
                    struct.pack_into("<H", cai, 15, opts.max_tx & 0xFFFF)
                    length += 2

                    if (opts.min_rx or opts.max_rx or opts.s7 or opts.s10
                            or opts.reserved_modulation_options
                            or opts.reserved_modulation):
                        struct.pack_into("<H", cai, 17, opts.min_rx & 0xFFFF)
                        length += 2

                        if (opts.max_rx or opts.s7 or opts.s10
                                or opts.reserved_modulation_options
                                or opts.reserved_modulation):
                            struct.pack_into("<H", cai, 19,
                                             opts.max_rx & 0xFFFF)
                            length += 2

                            if (opts.s7 or opts.s10
                                    or opts.reserved_modulation_options
                                    or opts.reserved_modulation):
                                cai[21] = 0   # disabled symbol rates
                                cai[22] = 0   # modem info options
                                cai[23] = 0   # transmit level adjust
                                cai[24] = opts.speaker & 0xFF
                                cai[25] = opts.s7 & 0xFF
                                length += 5

                                if (opts.s10 or opts.reserved_modulation_options
                                        or opts.reserved_modulation):
                                    cai[26] = opts.s10 & 0xFF
                                    length += 1

                                    if (opts.reserved_modulation_options
                                            or opts.reserved_modulation):
                                        cai[27] = 6
                                        struct.pack_into(
                                            "<H", cai, 28,
                                            opts.reserved_modulation_options & 0xFFFF)
                                        struct.pack_into(
                                            "<I", cai, 30,
                                            opts.reserved_modulation & 0xFFFFFFFF)
                                        length += 7

    length = max(length, min_length)

    # cai[21] carries DSP_CAI_MODEM_EXTENDED_LEC (0x80) as well as the
    # symbol-rate disables (0x01..0x20); the branch above that names it only
    # runs when s7/s10 are set, so on this project's calls the byte is
    # transmitted as padding and has always been zero.  LEC is the line echo
    # canceller, which the bulk delay line serves, and V.34 needs far-echo
    # cancellation where V.90 downstream does not -- so this is the one
    # host-side control over the echo canceller the driver defines and this
    # project has never exercised.  Opt-in and untested against the card, like
    # the other divergences noted above; set it deliberately, on its own.
    if length > 21:
        if os.environ.get('EICON_EXTENDED_LEC') == '1':
            cai[21] |= DSP_CAI_MODEM_EXTENDED_LEC
        # The low six bits of the same byte disable individual V.34 symbol
        # rates, and are equally never set on this project's calls.  Naming
        # rates to disable is clearer at the call site than a raw mask, and
        # the rate set is small and fixed.
        for rate in os.environ.get('EICON_DISABLE_SYMBOLS', '').split(','):
            rate = rate.strip()
            if rate:
                cai[21] |= DSP_CAI_MODEM_DISABLE_SYMBOLS[int(rate)]

    return bytes(cai[1:1 + length])


def _apply_fast_connect(mode: int, disabled: int, enabled: int) -> int:
    """putcai()'s fast-connect fixups (isdn.c:1263)."""
    if mode in (1, 3):
        enabled |= DSP_CAI_MODEM_ENABLE_V22FC | DSP_CAI_MODEM_ENABLE_V22BISFC
        if disabled & DSP_CAI_MODEM_DISABLE_V22BIS:
            if not disabled & DSP_CAI_MODEM_DISABLE_V22:
                enabled &= ~DSP_CAI_MODEM_ENABLE_V22BISFC
        elif disabled & DSP_CAI_MODEM_DISABLE_V22:
            enabled &= ~DSP_CAI_MODEM_ENABLE_V22FC
    elif mode == 2:
        enabled |= DSP_CAI_MODEM_ENABLE_V22FC
    elif mode == 4:
        enabled |= (DSP_CAI_MODEM_ENABLE_V22FC | DSP_CAI_MODEM_ENABLE_V22BISFC
                    | DSP_CAI_MODEM_ENABLE_V29FC)
        if disabled & DSP_CAI_MODEM_DISABLE_V22BIS:
            enabled &= ~DSP_CAI_MODEM_ENABLE_V22BISFC
        if disabled & DSP_CAI_MODEM_DISABLE_V22:
            enabled &= ~DSP_CAI_MODEM_ENABLE_V22FC
    return enabled & 0xFF


# ---------------------------------------------------------------------------
# Request payloads
# ---------------------------------------------------------------------------
def sig_assign_payload(options: "ModemOptions | None" = None,
                       user_id: bytes = b"Capi20", **cai_kwargs) -> bytes:
    """Signalling-entity ASSIGN: the CAI, as add_b1() attaches it."""
    return idi_parameters((IDI_CAI, build_cai(options, **cai_kwargs)),
                          (IDI_UID, user_id))


def call_res_payload(options: "ModemOptions | None" = None,
                     **cai_kwargs) -> bytes:
    """CAPI20 connect_res(): the same complete modem descriptor.

    The old i4l compatibility path used a six-byte CAI here; Eicon's CAPI20
    hardware path attaches the full one, and that transaction has private DSP
    effects the native ingress experiment depends on.
    """
    return idi_parameters((IDI_CAI, build_cai(options, **cai_kwargs)))


def address_ie(code: int, number: str, plan: int = 0x81,
               octet_3a: "int | None" = None) -> "tuple[int, bytes]":
    """putaddr() (isdn.c:1209's neighbour): a Q.931 address element.

    `plan` is the numbering-plan octet -- 0x81 is "unknown type, ISDN/E.164",
    which is what a bare extension wants.  `octet_3a` carries presentation and
    screening and is omitted entirely when None, which is the difference
    between an address the network passes through and one it rewrites.
    """
    data = bytearray((plan & 0xFF,))
    if octet_3a is not None:
        data.append(octet_3a & 0xFF)
    data += number.encode("ascii")
    return (code, bytes(data))


def call_req_payload(destination: str,
                     origination: str = "",
                     options: "ModemOptions | None" = None,
                     user_id: bytes = b"Capi20",
                     service: int = 2, service_add: int = 3,
                     plan_dest: int = 0x81, plan_orig: int = 0x81,
                     presentation: "int | None" = None,
                     destination_subaddress: str = "",
                     **cai_kwargs) -> bytes:
    """CALL_REQ, as isdnDial() assembles it (tty_module/isdn.c:1952).

    The order is the driver's: user id, the same modem CAI the ASSIGN carried,
    originating address, called party number, then the service pair.  A modem
    call is service 2 / additional 3 -- "data over modem connection" in the
    manual's table -- carried in codeset 6 behind a non-locking shift, which is
    what the driver sends whenever no explicit bearer capability was
    configured.

    `origination` may be empty: putaddr() emits nothing for a zero-length
    address, and an outgoing call with no calling-party number is legal.
    """
    parameters: "list[tuple[int, bytes]]" = [
        (IDI_UID, user_id),
        (IDI_CAI, build_cai(options, **cai_kwargs)),
    ]
    if origination:
        parameters.append(address_ie(IDI_OAD, origination, plan_orig,
                                     presentation))
    parameters.append(address_ie(IDI_CPN, destination, plan_dest))
    if destination_subaddress:
        parameters.append(address_ie(IDI_DSA, destination_subaddress, 0xFF))

    payload = bytearray(idi_parameters(*parameters))
    # The service pair is not an ordinary IE: it is a non-locking shift to
    # codeset 6 followed by SIN there, so it is appended past the terminator
    # rather than through idi_parameters().
    payload = payload[:-1]
    payload += bytes((SHIFT | 0x08 | 6, IDI_SIN, 2,
                      service & 0xFF, service_add & 0xFF))
    payload.append(0)
    return bytes(payload)


def nl_assign_payload(max_data_length: int = 1024,
                      answering: bool = True,
                      signaling_id: "int | None" = None,
                      error_control: bool = False,
                      options: "ModemOptions | None" = None,
                      lli: int = LLI_OK_FC) -> bytes:
    """Network-layer ASSIGN, as add_modem_b23()/assign_nl() build it.

    The `lli` and `max_data_length` defaults are the ones this project has
    been sending, not the driver's.  The tty driver sends LLI_DEFAULT
    (OK_FC | CMA | NO_CANCEL) and a 2138-byte maximum; the shim has sent
    OK_FC alone and 1024.  Both are on the known-good V.90 path, so they are
    left alone here and changed by the caller.

    Two shapes, and the difference is the whole of whether the card runs its
    own V.42:

    * `error_control=False` (the default, and what this project has been
      sending) adds a DLC that sets DISABLE_V42_V42BIS, so the card frames
      nothing and the Python LapmEndpoint owns error control.
    * `error_control=True` omits the DLC entirely, which is how the tty driver
      enables the shipped V.42.  isdn.c only ever emits a DLC here to *disable*
      things.

    The LLC is B2_V42_in/out either way.  The protocol map's B2 for a modem is
    B2_XPARENT, but isdn.c:1533 overwrites it unconditionally on the modem
    branch, so B2_TRANSPARENT never reaches the card on this path -- the DLC,
    not the LLC, is what turns error control off.
    """
    parameters: "list[tuple[int, bytes]]" = []
    if signaling_id is not None:
        # send_req(): the first NL request for a PLCI is global (Id=NL_ID) and
        # carries a one-byte CAI naming the parent signalling entity.  Omitting
        # it makes the firmware reject the otherwise valid ASSIGN with 0xe6.
        parameters.append((IDI_CAI, bytes((signaling_id & 0xFF,))))
    parameters.append((IDI_LLI, bytes((lli & 0xFF,))))
    b2 = B2_V42_IN if answering else B2_V42_OUT
    parameters.append((IDI_LLC, bytes((b2, B3_XPARENT))))

    if error_control:
        opts = options or ModemOptions()
        if opts.disable_error_control or opts.protocol_options:
            parameters.append(
                (IDI_DLC, _dlc_no_error_control(opts, max_data_length)))
    else:
        dlc = bytearray(struct.pack("<H", max_data_length))
        dlc += bytes((3,      # Addr A
                      1,      # Addr B
                      7,      # modulo
                      7,      # window size
                      0, 0,   # XID length
                      DLC_MODEMPROT_DISABLE_V42_V42BIS
                      | DLC_MODEMPROT_DISABLE_MNP_MNP5
                      | DLC_MODEMPROT_DISABLE_SDLC))
        parameters.append((IDI_DLC, bytes(dlc)))
    return idi_parameters(*parameters)


def _dlc_no_error_control(opts: ModemOptions, max_data_length: int) -> bytes:
    """dlc_mdm_no_ec / dlc_mdm_no_ec_sdlc (isdn.c:1443).

    Which template the driver picks turns on whether SDLC is among the things
    being disabled: if it is, the SDLC block is left off entirely rather than
    sent with dummy values.
    """
    head = bytearray(struct.pack("<H", max_data_length))
    head += bytes((3, 1, 7, 7, 0, 0))
    if opts.disable_error_control & DLC_MODEMPROT_DISABLE_SDLC:
        return bytes(head + bytes((opts.disable_error_control & 0xFF,
                                   opts.protocol_options & 0xFF)))

    sdlc_options = 0x03  # wait CTS/DCD on, indicate CTS/DCD on
    if opts.sdlc_prot_options & SDLC_L2_OPTION_REVERSE_ESTABLISHEMENT:
        sdlc_options |= 0x10
    if opts.sdlc_prot_options & SDLC_L2_OPTION_SINGLE_DATA_PACKETS:
        sdlc_options |= 0x08

    body = bytearray(head)
    body += bytes((opts.disable_error_control & 0xFF,
                   opts.protocol_options & 0xFF,
                   0,      # modem protocol break configuration
                   0,      # modem protocol application options
                   0,      # modem reserved struct length
                   9))     # SDLC config struct length
    body += struct.pack("<H", max_data_length)
    body += bytes((opts.sdlc_address_a & 0xFF,
                   0x00,   # Address B
                   0x07,   # modulo
                   0x07,   # window size
                   0x00, 0x00,  # XID length
                   sdlc_options))
    return bytes(body)


# ---------------------------------------------------------------------------
# T.30 / Group 3 fax
# ---------------------------------------------------------------------------
# The card's own firmware runs T.30, not the host: the protocol map's fax row
# selects B1_T30 in the CAI and B2_T30/B3_T30 in the NL ASSIGN, and from there
# the card drives phases A-E itself and reports where it is with the EDATA
# messages below.  The host supplies the parameters once, in a T30_INFO
# attached to the ASSIGN as an NLC, and after that exchanges page data.
#
# Field layout and constants are divacapi.h:788, not tty_module/t30.h -- the
# copy there is inside an `#if 0` and is missing `resolution_high`.
T30_MAX_STATION_ID_LENGTH = 20

T30_RESOLUTION_R8_0385 = 0x0000          # standard, 98 lpi
T30_RESOLUTION_R8_0770_OR_200 = 0x0001   # fine, 196 lpi
T30_RESOLUTION_R8_1540 = 0x0002
T30_RESOLUTION_R16_1540_OR_400 = 0x0004

T30_DATA_FORMAT_SFF = 0
T30_DATA_FORMAT_PLAIN_MH = 1
T30_DATA_FORMAT_PCX = 2
T30_DATA_FORMAT_DCX = 3
T30_DATA_FORMAT_TIFF = 4
T30_DATA_FORMAT_ASCII = 5

T30_OPERATING_MODE_STANDARD = 0
T30_OPERATING_MODE_CLASS2 = 1
T30_OPERATING_MODE_CLASS1 = 2
T30_OPERATING_MODE_CAPI = 3
T30_OPERATING_MODE_CAPI_NEG = 4
T30_OPERATING_MODE_MONITOR = 5
T30_OPERATING_MODE_BIT_INFO_EX = 0x80

T30_CONTROL_BIT_DISABLE_FINE = 0x0001
T30_CONTROL_BIT_ENABLE_ECM = 0x0002
T30_CONTROL_BIT_ECM_64_BYTES = 0x0004
T30_CONTROL_BIT_ENABLE_2D_CODING = 0x0008
T30_CONTROL_BIT_ENABLE_T6_CODING = 0x0010
T30_CONTROL_BIT_ENABLE_UNCOMPR = 0x0020
T30_CONTROL_BIT_ACCEPT_POLLING = 0x0040
T30_CONTROL_BIT_REQUEST_POLLING = 0x0080
T30_CONTROL_BIT_MORE_DOCUMENTS = 0x0100
T30_CONTROL_BIT_ENABLE_V34FAX = 0x1000
T30_CONTROL_BIT_EARLY_CONNECT = 0x2000
T30_CONTROL_BIT_ENABLE_T85_CODING = 0x8000

T30_RECORDING_WIDTH_ISO_A4 = 0
T30_RECORDING_LENGTH_ISO_A4 = 0

# EDATA transmit messages -- what the host tells the card to send.
EDATA_T30_DIS = 0x01
EDATA_T30_FTT = 0x02
EDATA_T30_MCF = 0x03
EDATA_T30_PROGRESS = 0x04
# EDATA receive messages -- what the card reports it saw.
EDATA_T30_DCS = 0x81
EDATA_T30_TRAIN_OK = 0x82
EDATA_T30_EOP = 0x83
EDATA_T30_MPS = 0x84
EDATA_T30_EOM = 0x85
EDATA_T30_DTC = 0x86
EDATA_T30_PAGE_END = 0x87
EDATA_T30_EOP_CAPI = 0x88

EDATA_NAMES = {
    EDATA_T30_DIS: "DIS", EDATA_T30_FTT: "FTT", EDATA_T30_MCF: "MCF",
    EDATA_T30_PROGRESS: "PROGRESS", EDATA_T30_DCS: "DCS",
    EDATA_T30_TRAIN_OK: "TRAIN_OK", EDATA_T30_EOP: "EOP",
    EDATA_T30_MPS: "MPS", EDATA_T30_EOM: "EOM", EDATA_T30_DTC: "DTC",
    EDATA_T30_PAGE_END: "PAGE_END", EDATA_T30_EOP_CAPI: "EOP_CAPI",
}


def build_t30_info(station_id: str = "",
                   head_line: str = "",
                   rate_div_2400: int = 6,
                   resolution: int = T30_RESOLUTION_R8_0770_OR_200,
                   data_format: int = T30_DATA_FORMAT_SFF,
                   operating_mode: int = T30_OPERATING_MODE_CAPI,
                   control_bits: int = 0,
                   recording_properties: int = 0,
                   resolution_high: int = 0,
                   code: int = 0,
                   outgoing: bool = False) -> bytes:
    """The T30_INFO the fax NL ASSIGN carries as its NLC (divacapi.h:789).

    Sixteen fixed bytes and then a fixed twenty-byte station id field.  The
    struct is copied whole -- `sizeof(*T30Info)` at isdn.c:1575 -- so the
    station id is padded rather than truncated to its length, and
    `station_id_len` says how much of it is real.

    `rate_div_2400` defaults to 6, i.e. 14400: the ceiling for the V.17
    modulations the fax page carries.  `outgoing` reproduces the driver's
    "HACK HACK HACK" at isdn.c:1577, which zeroes `station_id_len` on an
    outgoing assign while leaving the field itself populated.

    Fields marked `/*ind*/` in the struct are the card's to fill in on the
    way back and are sent as zero: `code`, `pages_low`, `pages_high`,
    `feature_bits_low`, `feature_bits_high`.  `code` is exposed anyway
    because the same structure comes back as an indication.
    """
    station = station_id.encode("ascii", "replace")[:T30_MAX_STATION_ID_LENGTH]
    head = head_line.encode("ascii", "replace")

    info = bytearray(16)
    info[0] = code & 0xFF                    # ind: code
    info[1] = rate_div_2400 & 0xFF
    info[2] = resolution & 0xFF
    info[3] = data_format & 0xFF
    info[4] = 0                              # ind: pages_low
    info[5] = 0                              # ind: pages_high
    info[6] = operating_mode & 0xFF
    info[7] = control_bits & 0xFF
    info[8] = (control_bits >> 8) & 0xFF
    info[9] = 0                              # ind: feature_bits_low
    info[10] = 0                             # ind: feature_bits_high
    info[11] = recording_properties & 0xFF
    info[12] = resolution_high & 0xFF
    info[13] = 0                             # universal_7
    info[14] = 0 if outgoing else len(station)
    info[15] = len(head)

    body = bytes(info) + station.ljust(T30_MAX_STATION_ID_LENGTH, b"\0")
    return body + head


def fax_nl_assign_payload(max_data_length: int = 2138,
                          answering: bool = True,
                          signaling_id: "int | None" = None,
                          t30_info: "bytes | None" = None,
                          lli: int = LLI_OK_FC | LLI_CMA | LLI_NO_CANCEL,
                          **t30_kwargs) -> bytes:
    """Network-layer ASSIGN for a Group 3 fax (isdn.c:1567, ISDN_PROT_FAX).

    Four parameters and no branches, which is the whole difference from the
    modem path: CAI naming the signalling entity, LLI, an LLC carrying
    B2_T30_in/out and B3_T30, `dlc_def` -- a bare two-byte maximum info size,
    with none of the modem template's error-control fields -- and then the
    NLC holding the T30_INFO.

    `max_data_length` defaults to the driver's 2138 rather than the 1024 the
    modem path in this file has been sending, because unlike that default
    this one has no known-good capture behind it to preserve.
    """
    if t30_info is None:
        t30_info = build_t30_info(outgoing=not answering, **t30_kwargs)
    elif t30_kwargs:
        raise TypeError("pass either t30_info or its fields, not both")

    parameters: "list[tuple[int, bytes]]" = []
    if signaling_id is not None:
        parameters.append((IDI_CAI, bytes((signaling_id & 0xFF,))))
    parameters.append((IDI_LLI, bytes((lli & 0xFF,))))
    b2 = B2_T30_IN if answering else B2_T30_OUT
    parameters.append((IDI_LLC, bytes((b2, B3_T30))))
    parameters.append((IDI_DLC, struct.pack("<H", max_data_length)))
    parameters.append((IDI_NLC, t30_info))
    return idi_parameters(*parameters)


# ---------------------------------------------------------------------------
# Entities, requests and indications
# ---------------------------------------------------------------------------
# Global entity ids (kernel/pc.h).
DSIG_ID = 0x00    # D-channel signalling
NL_ID = 0x20      # network-layer access
BLLC_ID = 0x60    # B-channel link level access
TASK_ID = 0x80    # dynamic user tasks
MAN_ID = 0xE0     # management

# Request/indication codes.  Signalling and network-layer entities have
# separate number spaces that overlap, which is why these are two tables and
# why decoding needs to know which entity a message belongs to.
ASSIGN = 0x01
REMOVE = 0xFF

SIG_CODES = {
    1: "CALL_REQ/CALL_CON", 2: "CALL_IND/LISTEN_REQ", 3: "HANGUP",
    4: "SUSPEND", 5: "RESUME", 6: "SUSPEND_REJ", 8: "USER_DATA",
    9: "CONGESTION", 10: "INDICATE", 11: "CALL_RES", 12: "CALL_ALERT",
    13: "INFO", 14: "REJECT", 15: "RESOURCES", 16: "HW_CTRL/TEL_CTRL",
    17: "STATUS_REQ", 21: "CALL_COMPLETE", 22: "SW_CTRL", 29: "SIG_CTRL",
    30: "DSP_CTRL", 31: "LAW_REQ", 33: "NCR_FACILITY", 34: "CALL_HOLD",
    35: "CALL_RETRIEVE", 40: "GCR_RESTART", 41: "S_SERVICE",
    44: "STATUS_ENQ",
}
NL_CODES = {
    2: "N_CONNECT", 3: "N_CONNECT_ACK", 4: "N_DISC", 5: "N_DISC_ACK",
    8: "N_DATA",
}

CALL_REQ = 1
CALL_CON = 1
CALL_IND = 2
LISTEN_REQ = 2
HANGUP = 3
INDICATE_REQ = 10
INDICATE_IND = 10
CALL_RES = 11
CALL_ALERT = 12
INFO_IND = 13
REJECT = 14

N_CONNECT = 2
N_CONNECT_ACK = 3
N_DISC = 4
N_DISC_ACK = 5
N_DATA = 8

# Return codes.
UNKNOWN_COMMAND = 0x01
WRONG_COMMAND = 0x02
WRONG_ID = 0x03
WRONG_CH = 0x04
UNKNOWN_IE = 0x05
WRONG_IE = 0x06
OUT_OF_RESOURCES = 0x07
ASSIGN_RC = 0xE0
ASSIGN_OK = 0xEF
OK_FC = 0xFC
READY_INT = 0xFD
TIMER_INT = 0xFE
RC_OK = 0xFF

_RC_NAMES = {
    UNKNOWN_COMMAND: "UNKNOWN_COMMAND", WRONG_COMMAND: "WRONG_COMMAND",
    WRONG_ID: "WRONG_ID", WRONG_CH: "WRONG_CH", UNKNOWN_IE: "UNKNOWN_IE",
    WRONG_IE: "WRONG_IE", OUT_OF_RESOURCES: "OUT_OF_RESOURCES",
    ASSIGN_OK: "ASSIGN_OK", OK_FC: "OK_FC", READY_INT: "READY_INT",
    TIMER_INT: "TIMER_INT", RC_OK: "OK",
}


def rc_name(rc: int) -> str:
    """Name a return code.

    isdn_rc() (kernel/di.c) treats any Rc with the top nibble ASSIGN_RC as an
    assign acknowledgement carrying the assigned Id, but only ASSIGN_OK means
    the assign actually succeeded -- 0xe6, for instance, is an acknowledged
    rejection and reads as success to anything that only masks the nibble.
    """
    if rc in _RC_NAMES:
        return _RC_NAMES[rc]
    if rc & 0xF0 == ASSIGN_RC:
        return f"ASSIGN_RC(0x{rc:02x}, rejected)"
    return f"0x{rc:02x}"


def code_name(code: int, entity: str) -> str:
    """Name a request or indication code within its entity's number space."""
    table = NL_CODES if entity == "nl" else SIG_CODES
    return table.get(code, f"0x{code:02x}")


class CallState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    INCOMING = "incoming"      # CALL_IND seen, not yet answered
    ANSWERING = "answering"    # CALL_RES posted, no confirmation yet
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"


@dataclass
class Indication:
    """One decoded IND buffer."""
    code: int
    entity_id: int
    channel: int
    reference: int
    payload: bytes
    entity: str = "sig"

    @property
    def name(self) -> str:
        return code_name(self.code, self.entity)

    def parameters(self) -> "list[tuple[int, bytes]]":
        try:
            return parse_idi_parameters(self.payload)
        except ValueError:
            return []

    def __str__(self) -> str:
        return (f"IND {self.name} Id=0x{self.entity_id:02x} "
                f"Ch=0x{self.channel:02x} Ref=0x{self.reference:04x} "
                f"payload={self.payload.hex()}")


class IdiCallControl:
    """Entity bookkeeping and a call state machine over an IDI transport.

    The transport is two callables, so this stays testable and the emulator
    stays out of it:

    * `post(req, entity_id, channel, payload, reference)` queues one request;
    * `pump()` runs the card far enough to produce return codes and
      indications, and returns `(return_codes, indications)` where a return
      code is `(rc, id, ch, reference)` and an indication is an `Indication`.

    What this does *not* do is decide the request sequence for a call from
    scratch.  The shim's hand-built ASSIGN/LISTEN/CALL_RES ordering is on the
    known-good V.90 path and stays where it is; this tracks what that
    sequence produces, names it, and gives the AT layer something to ask
    about.
    """

    def __init__(self, post, pump, log=print) -> None:
        self._post = post
        self._pump = pump
        self.log = log
        self.entities: "dict[str, int]" = {}
        self.state = CallState.IDLE
        self.call_channel = 0
        self.last_cause = 0
        self.indications: "list[Indication]" = []

    # -- primitives -------------------------------------------------------
    def request(self, req: int, entity: str, payload: bytes = b"",
                channel: int = 0, reference: int = 0,
                entity_id: "int | None" = None) -> "tuple[list, list]":
        """Post one request and drain whatever it produces."""
        if entity_id is None:
            entity_id = self.entities.get(entity)
            if entity_id is None:
                raise KeyError(f"no {entity} entity assigned")
        self._post(req, entity_id, channel, payload, reference)
        codes, indications = self._pump()
        for indication in indications:
            indication.entity = entity
            self.indications.append(indication)
            self.observe(indication)
        return codes, indications

    def assign(self, entity: str, payload: bytes, global_id: int,
               channel: int = 0, reference: int = 0) -> "int | None":
        """ASSIGN one entity, recording the local id the card allocates."""
        self._post(ASSIGN, global_id, channel, payload, reference)
        codes, indications = self._pump()
        for indication in indications:
            indication.entity = entity
            self.indications.append(indication)
        assigned = None
        for rc, rc_id, rc_ch, ref in codes:
            self.log(f"[{entity}] RC {rc_name(rc)} Id=0x{rc_id:02x} "
                     f"Ch=0x{rc_ch:02x} Ref=0x{ref:04x}")
            if rc == ASSIGN_OK and assigned is None:
                assigned = rc_id
        if assigned is not None:
            self.entities[entity] = assigned
        return assigned

    def remove(self, entity: str) -> None:
        """REMOVE an entity and forget it."""
        if entity not in self.entities:
            return
        self.request(REMOVE, entity)
        self.entities.pop(entity, None)

    # -- call verbs -------------------------------------------------------
    def listen(self, legacy_req_id: bool = False):
        """Put the signalling entity into the listening state.

        The old i4l driver calls this INDICATE_REQ even though the firmware's
        CAPI state machine talks about LISTEN_REQ; the payload is a one-byte
        zero parameter block, and without it no CALL_IND can exist for
        CALL_RES to answer.
        """
        entity_id = 1 if legacy_req_id else self.entities.get("sig", 0)
        if self.state is CallState.IDLE:
            self.state = CallState.LISTENING
        result = self.request(INDICATE_REQ, "sig", b"\x00",
                              entity_id=entity_id)
        # An incoming call can arrive in the same pump that acknowledges the
        # listen, so do not stamp LISTENING over the state the indications
        # just established.
        return result

    def answer(self, payload: bytes):
        """CALL_RES on the channel the CALL_IND named."""
        self.state = CallState.ANSWERING
        codes, indications = self.request(CALL_RES, "sig", payload,
                                          channel=self.call_channel)
        if any(rc == RC_OK for rc, _, _, _ in codes):
            self.state = CallState.CONNECTED
        return codes, indications

    def reject(self, payload: bytes = b""):
        self.state = CallState.DISCONNECTING
        return self.request(REJECT, "sig", payload,
                            channel=self.call_channel)

    def hangup(self, payload: bytes = b""):
        self.state = CallState.DISCONNECTING
        return self.request(HANGUP, "sig", payload,
                            channel=self.call_channel)

    # -- state tracking ---------------------------------------------------
    def observe(self, indication: Indication) -> None:
        """Advance the call state from what the card reports."""
        if indication.entity == "nl":
            if indication.code == N_DISC:
                self.state = CallState.DISCONNECTING
            elif indication.code == N_CONNECT_ACK:
                self.state = CallState.CONNECTED
            return
        if indication.code in (CALL_IND, INDICATE_IND):
            # CALL_IND's Ch is the per-call selector CALL_RES must echo.
            self.call_channel = indication.channel
            self.state = CallState.INCOMING
        elif indication.code == HANGUP:
            payload = indication.payload
            self.last_cause = payload[0] if payload else 0
            self.state = CallState.IDLE

    def calling_number(self) -> str:
        """The calling party number from the most recent CALL_IND, if any.

        IE 0x70 is the called-party number and 0x6c the calling-party number
        in the Q.931 numbering the signalling entity passes through; the
        first octet is the numbering plan, which is dropped here.
        """
        for indication in reversed(self.indications):
            if indication.code != CALL_IND:
                continue
            for code, data in indication.parameters():
                if code == 0x6C and len(data) > 1:
                    return data[1:].decode("ascii", "replace")
        return ""


def describe_cai(cai: bytes) -> str:
    """One-line summary of a CAI, for trace output."""
    if len(cai) < 6:
        return f"CAI[{len(cai)}] {cai.hex()} (short)"
    parts = [f"res=0x{cai[0]:02x}", f"rate=0x{cai[1]:02x}",
             f"framing=0x{cai[2]:02x}",
             f"maxframe={struct.unpack_from('<H', cai, 4)[0]}"]
    if len(cai) >= 7:
        parts.append(f"linetaking=0x{cai[6]:02x}")
    if len(cai) >= 8:
        neg = cai[7] & DSP_CAI_MODEM_NEGOTIATE_MASK
        parts.append(f"neg={neg} guard=0x{cai[7] & 0xc0:02x}")
    if len(cai) >= 11:
        parts.append(f"modopt=0x{cai[8]:02x}")
        parts.append(f"disabled=0x{struct.unpack_from('<H', cai, 9)[0]:04x}")
    if len(cai) >= 12:
        parts.append(f"enabled=0x{cai[11]:02x}")
    if len(cai) >= 20:
        parts.append("tx=%d..%d" % struct.unpack_from("<HH", cai, 12))
        parts.append("rx=%d..%d" % struct.unpack_from("<HH", cai, 16))
    return "CAI[%d] %s" % (len(cai), " ".join(parts))
