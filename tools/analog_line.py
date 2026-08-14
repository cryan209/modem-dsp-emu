#!/usr/bin/env python3
"""Minimal analogue codec/DAA boundary for the direct ADSP harness.

The POTS firmware's DSP is behind a signed-linear SPORT1 codec and a two-wire
DAA, not directly on an RTP/G.711 timeslot.  This model deliberately stops at
that physical boundary: hook state, codec gains, clipping, and optional local
hybrid echo. Ringing, caller ID and country-specific impedance remain MIPS/POTS
policy and are not fabricated here.
"""
from __future__ import annotations

import collections
import math
import os


DTMF_ROWS = (697, 770, 852, 941)
DTMF_COLUMNS = (1209, 1336, 1477, 1633)
# Silicon Labs line-side status fields shared by the Si3014/18/19 family.
# The Courier model recovered these from the Si3038 sibling: frame detect plus
# loop-current sense in 6 mA steps. Build-109 additionally asks its serial DAA
# for a signed line-voltage sample.
DAA_STATUS_FRAME_DETECT = 1 << 6
DAA_STATUS_LOOP_CURRENT_SHIFT = 2
DAA_LOOP_CURRENT_STEP_MA = 6
DAA_LOOP_CURRENT_MAX = 0x0F

DTMF_DIGITS = (
    ('1', '2', '3', 'A'),
    ('4', '5', '6', 'B'),
    ('7', '8', '9', 'C'),
    ('*', '0', '#', 'D'),
)


def _goertzel_power(samples: list[int], frequency: int,
                    sample_rate: int = 8000) -> float:
    """Energy at one DTMF frequency without requiring numpy."""
    coefficient = 2.0 * math.cos(2.0 * math.pi * frequency / sample_rate)
    first = second = 0.0
    for sample in samples:
        value = sample + coefficient * first - second
        second, first = first, value
    return first * first + second * second - coefficient * first * second


class DtmfDetector:
    """Streaming detector for digits emitted onto an Analog two-wire line.

    Decisions use 40 ms windows and require two matching windows, preventing
    an answer tone or modem carrier from becoming a dial digit. ``finished``
    becomes true after a post-digit quiet interval; that is the point at which
    an FXO bridge may turn the collected number into a SIP INVITE.
    """

    def __init__(self, *, sample_rate: int = 8000, window_ms: int = 40,
                 finish_gap_ms: int = 300):
        self.sample_rate = sample_rate
        self.window = sample_rate * window_ms // 1000
        self.finish_gap = sample_rate * finish_gap_ms // 1000
        self._buffer: list[int] = []
        self._candidate: str | None = None
        self._candidate_windows = 0
        self._active: str | None = None
        self._quiet_samples = 0
        self.digits = ''
        self.finished = False

    def feed(self, samples) -> list[str]:
        """Consume signed-linear DAC samples and return newly detected digits."""
        emitted: list[str] = []
        self._buffer.extend(int(sample) for sample in samples)
        while len(self._buffer) >= self.window:
            block = self._buffer[:self.window]
            del self._buffer[:self.window]
            digit = self._classify(block)
            if digit is None:
                self._candidate = None
                self._candidate_windows = 0
                self._active = None
                if self.digits:
                    self._quiet_samples += self.window
                    if self._quiet_samples >= self.finish_gap:
                        self.finished = True
                continue
            self._quiet_samples = 0
            self.finished = False
            if digit == self._active:
                continue
            if digit != self._candidate:
                self._candidate = digit
                self._candidate_windows = 1
                continue
            self._candidate_windows += 1
            if self._candidate_windows >= 2:
                self._active = digit
                self.digits += digit
                emitted.append(digit)
        return emitted

    @staticmethod
    def _classify(samples: list[int]) -> str | None:
        total = sum(float(sample) * sample for sample in samples)
        if total < len(samples) * 100.0 * 100.0:
            return None
        rows = [_goertzel_power(samples, frequency) for frequency in DTMF_ROWS]
        columns = [_goertzel_power(samples, frequency)
                   for frequency in DTMF_COLUMNS]
        row = max(range(4), key=rows.__getitem__)
        column = max(range(4), key=columns.__getitem__)
        row_sorted = sorted(rows, reverse=True)
        column_sorted = sorted(columns, reverse=True)
        # Each selected component must dominate its own group and represent a
        # material fraction of block energy. The broad bounds admit normal
        # DTMF twist while rejecting single modem tones such as 820/2100 Hz.
        if (row_sorted[0] < row_sorted[1] * 4.0
                or column_sorted[0] < column_sorted[1] * 4.0):
            return None
        ratio = rows[row] / columns[column] if columns[column] else 1e9
        if not 0.1 <= ratio <= 10.0:
            return None
        scale = len(samples) * total
        if rows[row] < scale * 0.08 or columns[column] < scale * 0.08:
            return None
        return DTMF_DIGITS[row][column]


def _gain(db: float) -> float:
    return 10.0 ** (db / 20.0)


def _clip16(value: float) -> int:
    return max(-32768, min(32767, int(round(value))))


class AnalogLineInterface:
    """One two-wire analogue port viewed from its modem codec and DAA."""

    def __init__(self, *, rx_gain_db: float = 0.0, tx_gain_db: float = 0.0,
                 echo_db: float | None = None, echo_delay: int = 8,
                 line_voltage: float = 48.0, loop_current_ma: float = 24.0,
                 tone_hz: float = 0.0, tone_amplitude: int = 3900,
                 tone_rate: int = 8000, tone_on_s: float = 0.0,
                 tone_off_s: float = 0.0):
        self.tone_hz = float(tone_hz)
        self.tone_amplitude = int(tone_amplitude)
        self.tone_rate = int(tone_rate)
        self.tone_on_s = float(tone_on_s)
        self.tone_off_s = float(tone_off_s)
        self._tone_index = 0
        self.rx_gain = _gain(rx_gain_db)
        self.tx_gain = _gain(tx_gain_db)
        self.echo_gain = 0.0 if echo_db is None else _gain(-abs(echo_db))
        self.echo_delay = max(0, echo_delay)
        self.seized = False
        self.connected = True
        self.line_voltage = float(line_voltage)
        self.loop_current_ma = float(loop_current_ma)
        self.dtmf = DtmfDetector()
        self.detected_digits: list[str] = []
        # receive() precedes transmit() on a tick, so even delay zero appears
        # on the following ADC sample; larger values add explicit line delay.
        history = max(1, self.echo_delay)
        self._tx_history = collections.deque([0] * history, maxlen=history)

    @classmethod
    def from_environment(cls) -> "AnalogLineInterface":
        echo = os.environ.get("EICON_ANALOG_HYBRID_ECHO_DB", "").strip()
        return cls(
            rx_gain_db=float(os.environ.get("EICON_ANALOG_RX_GAIN_DB", "0")),
            tx_gain_db=float(os.environ.get("EICON_ANALOG_TX_GAIN_DB", "0")),
            echo_db=float(echo) if echo else None,
            echo_delay=int(os.environ.get("EICON_ANALOG_HYBRID_DELAY", "8"), 0),
            line_voltage=float(os.environ.get("EICON_ANALOG_LINE_VOLTAGE", "48")),
            loop_current_ma=float(os.environ.get("EICON_ANALOG_LOOP_CURRENT_MA", "24")),
            tone_hz=float(os.environ.get("EICON_ANALOG_TX_TONE_HZ", "0")),
            tone_amplitude=int(os.environ.get("EICON_ANALOG_TX_TONE_AMPLITUDE",
                                              "3900"), 0),
            tone_rate=int(os.environ.get("EICON_ANALOG_CODEC_RATE", "8000"), 0),
            tone_on_s=float(os.environ.get("EICON_ANALOG_TX_TONE_ON_S", "0")),
            tone_off_s=float(os.environ.get("EICON_ANALOG_TX_TONE_OFF_S", "0")),
        )

    @property
    def sensed_voltage(self) -> float:
        """DAA line voltage; a seized loop normally sags from battery voltage."""
        if not self.connected:
            return 0.0
        return min(self.line_voltage, 9.0) if self.seized else self.line_voltage

    @property
    def sensed_current_ma(self) -> float:
        return self.loop_current_ma if self.connected and self.seized else 0.0

    @property
    def in_service(self) -> bool:
        """An FXO line is usable when exchange battery is present."""
        return self.connected and self.line_voltage >= 18.0

    @property
    def frame_detect(self) -> bool:
        """The isolation link is clocked whenever this DAA is connected."""
        return self.connected

    @property
    def loop_current_sense(self) -> int:
        """Si301x LCS field, in the family's recovered 6 mA steps."""
        if not self.frame_detect:
            return 0
        return min(DAA_LOOP_CURRENT_MAX,
                   int(self.sensed_current_ma) // DAA_LOOP_CURRENT_STEP_MA)

    @property
    def daa_line_status(self) -> int:
        value = DAA_STATUS_FRAME_DETECT if self.frame_detect else 0
        return value | (self.loop_current_sense
                        << DAA_STATUS_LOOP_CURRENT_SHIFT)

    @property
    def line_voltage_sense(self) -> int:
        """Signed whole-volt sample returned by the Si3019 LVS request."""
        return max(-128, min(127, int(round(self.sensed_voltage))))

    def set_connected(self, connected: bool) -> None:
        self.connected = bool(connected)
        if not connected:
            self.set_hook(False)

    def set_hook(self, off_hook: bool) -> None:
        """Seize or release the line; release also drains residual echo."""
        self.seized = bool(off_hook and self.connected)
        if not off_hook:
            history = max(1, self.echo_delay)
            self._tx_history = collections.deque([0] * history,
                                                  maxlen=history)

    def receive(self, far_sample: int) -> int:
        """ADC sample delivered to SPORT1, including local hybrid leakage."""
        if not self.seized:
            return 0
        echo = self._tx_history[0] if self._tx_history else 0
        return _clip16(far_sample * self.rx_gain + echo * self.echo_gain)

    def transmit(self, modem_sample: int) -> int:
        """DAC sample sent to the two-wire line and retained for hybrid echo."""
        if self.tone_hz:
            # Bench mode: put a known tone on the line instead of the modem's
            # own transmit, so the far end's detectors can be swept in line Hz
            # rather than in units of some internal pass rate. The codec rate
            # is the line rate here by construction, which is the whole point
            # -- pinning an internal word cannot tell you what frequency the
            # line was carrying.
            # A steady tone is not what a calling modem puts on the line, and
            # the difference turned out to matter: the cadence is part of the
            # signal, so the bench can reproduce it.
            phase = self._tone_index / self.tone_rate
            if self.tone_on_s and self.tone_off_s:
                phase %= (self.tone_on_s + self.tone_off_s)
            gated = not self.tone_on_s or phase < self.tone_on_s
            modem_sample = int(self.tone_amplitude
                               * math.sin(2 * math.pi * self.tone_hz
                                          * self._tone_index
                                          / self.tone_rate)) if gated else 0
            self._tone_index += 1
        sample = _clip16(modem_sample * self.tx_gain) if self.seized else 0
        self._tx_history.append(sample)
        self.detected_digits.extend(self.dtmf.feed((sample,)))
        return sample

    def describe(self) -> str:
        echo = ("off" if self.echo_gain == 0 else
                f"{(-20.0 * math.log10(self.echo_gain)):.1f} dB/"
                f"{self.echo_delay} samples")
        return f"linear16 battery={self.line_voltage:.1f} V " \
               f"loop={self.loop_current_ma:.1f} mA " \
               f"rx-gain={20*math.log10(self.rx_gain):.1f} dB " \
               f"tx-gain={20*math.log10(self.tx_gain):.1f} dB echo={echo}" \
               + (f" TONE={self.tone_hz:.1f} Hz @{self.tone_amplitude} "
                  f"rate={self.tone_rate}" if self.tone_hz else "")
