#!/usr/bin/env python3
"""Let the Analog kernel dispatch TIKRNL.ANA itself, off SPORT1.

`tools/dial_kernel_dispatch.py` does this for the PRI card: it boots kernel
0x0009, hands it one host command, and from then on the kernel's own foreground
calls the task on every SPORT0 timeslot.  That harness is PRI-only -- SPORT0
multichannel TDM, 8-bit companded -- and the Analog card's codec is SPORT1,
16-bit linear, on a different kernel.  This is the Analog half.

## The Analog kernel is the same design, relocated

Disassembling kernel 0x000d against 0x0009 gives a one-to-one correspondence
for every part of the dispatch chain:

| role                                   | PRI 0x0009 | Analog 0x000d |
|----------------------------------------|------------|---------------|
| foreground loop head                   | PM 0x02A9  | PM 0x02A6     |
| IDLE                                   | PM 0x02A8  | PM 0x02A5     |
| command dispatch (read ring, call)     | PM 0x02A1  | PM 0x029E     |
| foreground slot a task claims          | PM 0x02B9  | PM 0x02B6     |
| ISR slot a task claims                 | PM 0x00B5  | PM 0x0079     |
| the five ring-descriptor pointers      | DM 0x2F27  | DM 0x2E7A     |
| task registration block                | DM 0x2F21  | DM 0x2E74     |
| host -> DSP command ring descriptor    | DM 0x2F00  | DM 0x2E47     |
| DSP -> host descriptor (+9 = doorbell) | DM 0x2F0E  | DM 0x2E55     |
| sample queue head / tail               | DM 0x2E44/45 | DM 0x2E00/01 |

Both ISR slots are literally the same instruction, `AR = SR0 + 0, SR0 = AR`,
and the Analog registration block ships the pair 0x02B6 / 0x0079 in its image
at DM 0x2E77/0x2E78 -- so nothing here is inferred from the correspondence
alone; the kernel names its own two words.

## What the interrupt vectors settle

The Analog kernel leaves SPORT0 unused -- PM 0x0010 and PM 0x0014 are both
`RTI`, where the PRI kernel jumps to its TDM ISR at PM 0x0072 -- and services
SPORT1 at PM 0x0047, reached from the shared SPORT1 vector at PM 0x0020.  That
ISR reads `SR0 = RX1`, queues the sample into the 32-word ring at DM 0x2DE0
(PM 0x0077-0x0082, `I4 = DM($2E00)` / `DM(I4,M5) = AR` / `DM($2E00) = I4`) and
returns.  The foreground then pops it (`I5 = DM($2E01)`, `SR1 = DM(I5,M4)`) and
calls the slot at PM 0x02B6, which TIKRNL.ANA has patched to its sample
continuation PM 0x0713 -- with the sample in SR1, which is where PM 0x0715
takes it from.

So the whole receive path is the firmware's own, and this harness never calls
the task's frame entry by hand.  That is the point: RXSAMPLE_0..5
(DM 0x3F30..0x3F35) is filled by the kernel on a real card, and the direct
backend in `dial_tikrnl_drive.py` -- which hands the page one sample per frame
and calls the entries itself -- leaves it frozen (commit 16f09aa).

Usage:
    python3 tools/analog_kernel_dispatch.py --samples 16000 --stimulus ansam
"""
from __future__ import annotations

import argparse
import collections
import ctypes
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dial_tikrnl_drive as drive
from dial_tikrnl_drive import ADSP, Card, linear_to_mulaw

ADSP.adsp2181_set_callbacks.argtypes = [ctypes.c_void_p] * 4
ADSP.adsp2181_set_irq.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
ADSP.adsp2181_sport1_frame.argtypes = [ctypes.c_void_p, ctypes.c_uint16,
                                       ctypes.c_int]
ADSP.adsp2181_sport1_frame.restype = ctypes.c_uint32

RX_CB = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_int)
TX_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, ctypes.c_int32)
TIM_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int)

# Analog kernel 0x000d.  Every one of these is read off the disassembly or the
# kernel's own DM image; see the table in the module docstring.
PM_DISPATCH = 0x029E
PM_FOREGROUND_SLOT = 0x02B6
PM_ISR_SLOT = 0x0079
KERNEL_IDLE = 0x02A5
DM_RING_PTRS = 0x2E7A
DM_REGISTRATION = 0x2E74
DM_CMD_DESC_PTR = 0x2E7B       # -> the host -> DSP command ring descriptor
DM_DOORBELL_DESC_PTR = 0x2E7C  # -> the DSP -> host descriptor; doorbell at +9
DM_QUEUE_HEAD = 0x2E00
DM_QUEUE_TAIL = 0x2E01

# TIKRNL.ANA task 0x0258.
TASK_ENTRY = 0x0679
SAMPLE_CONTINUATION = 0x0713
DM_ENTRIES = 0x31BA            # slot i answers doorbell bit i
DM_DOWNLOAD_REQ = 0x31AC       # the download id the task is asking for
# The task's request path writes AX0/AR/M0 to DM 0x31AB/0x31AC/0x31AD, exactly
# as the PRI task writes DM 0x31A9/0x31AA/0x31AB.  So the *flag* is 0x31AB --
# it reads 0x0015 with 0x0274 pending straight out of task init -- and not
# 0x31AD, which is M0 and is always zero.  `dial_tikrnl_drive.FIRMWARE_SETS`
# names 0x31AD; that word is only consulted by the PRI kernel-dispatch service
# loop, so the mistake has never been reachable from the direct Analog path.
DM_DOWNLOAD_FLAG = 0x31AB

# Data-pump database (ADDSP V.90 guide §5.3).
DM_DB = 0x3EE0
DM_GEN_SETUP1 = 0x3EE1
DM_TRNPROGRESS = 0x3FC2
DM_STATUS = 0x3FC1
DM_BOOTPAGE = 0x3FB0
DM_TX_POINTER = 0x3FB4
DM_RXSAMPLE = 0x3F30
DM_SHELLINPTR = 0x3F0F
V8_DOWNLOAD = 0x025F
SAMPLE_RATE = 8000

# Overlays the Analog boot order needs, as `Card.boot()` establishes them.
V_OWN_ID = 0x026D
FSK_OWN_ID = 0x025C
DIAL_ID = 0x0262

# DIAL's NORM-operation entry: the documented V.8 trigger.  The host writes
# GEN_SETUP1 with the NORM bit and activates it through WSTATUS, and this
# routine is what consumes it and runs the training init
# (`docs/dial_v8_call.md`, guide Tables 14/15).  Both other backends already
# run it -- `dial_kernel_dispatch.py:639` and `eicon_mips_shim.py:4021`, both
# at the F34 address 0x13CC -- and this backend never has, which is why the
# Analog caller reached V.8 only through the forced request.
#
# **The address is not 0x13CC here.** A PM address is different code in every
# image, and in the Analog DIAL overlay 0x13CC is a TrnProgress store. The
# same instruction sequence -- MODE_CTL(2e80), then GEN_SETUP1 AND 0xFFBF OR
# 0x0080 written back, then the six-word clear at 0x3FA7 -- sits at 0x13E3,
# behind the identical M4/M5/M6 preamble that F34 has at 0x13C9..0x13CB.
DIAL_NORM_ENTRY = 0x13E3
# Off by default, and **measured inert** -- kept because it is what the other
# two backends do and because the negative is worth being able to reproduce.
# GEN_SETUP1 0x048C is a fixed point for the routine (it clears bit 6, already
# clear, and sets bit 7, already set), so it returns with the word unchanged
# and TrnProgress still 0x0000, and the pairing still stalls at 0x002a.
#
# It also disproves the reason it was added. This backend does **not** reach
# V.8 through the forced request: with `--no-originate-v8` and this flag off,
# the caller is still on bootpage 6 / overlay 0x025F at the first captured
# sample. `_maybe_request_v8` and `ORIGINATE_V8` are the *direct* backend's
# bypass; the kernel-dispatch path gets V.8 from the firmware's own download
# request loop, which is the same reason RXSAMPLE is maintained here.
DIAL_ORIGINATE = os.environ.get('EICON_ANALOG_DIAL_ORIGINATE', '0') != '0'

# Hold DM words against the firmware's own stores, re-applied before each codec
# frame: "ADDR=VALUE[@GATE:VALUE][,...]".  The shim has PIN_DM for the native
# tower and this backend had no equivalent, so a V.8 script A/B had nowhere to
# stand.  It is a stand-in by construction -- it makes the DSP see a value it
# did not compute -- so a result under it establishes what a path *would* do,
# never what the firmware does on its own.
#
# **The gate is not optional in practice, for the V.8 walk.** The two words the
# CM experiment wants to hold are both shared with the CI retransmit loop:
# `DM(0x0749)` is the countdown PM 0x37D7 decrements for *every* state, and
# `DM(0x0791)` is the destination slot the table loader rewrites at each one.
# Pinned unconditionally, either one holds the caller in 0x01DC <-> 0x0200 and
# it never reaches 0x02AB at all.  `@0x049f:0x02ab` applies the pin only while
# the script cursor is on the state under test.
def _parse_pin(field: str):
    body, _, gate = field.partition('@')
    address, _, value = body.partition('=')
    if gate:
        gate_address, _, gate_value = gate.partition(':')
        gate_pair = (int(gate_address, 0) & 0x3FFF, int(gate_value, 0) & 0xFFFF)
    else:
        gate_pair = None
    return (int(address, 0) & 0x3FFF, int(value, 0) & 0xFFFF, gate_pair)


PIN_DM = tuple(_parse_pin(f) for f
               in os.environ.get('EICON_ANALOG_PIN_DM', '').split(',')
               if f.strip())
# The V.8 script cursor (`docs/analog_v8_oracle.md`): print each state the walk
# enters.  Without this a pinned run that never reached the gated state and a
# pinned run that reached it and did nothing look identical from the wire.
TRACE_CURSOR = int(os.environ.get('EICON_ANALOG_TRACE_CURSOR', '0'), 0)


class AnalogKernelDispatch:
    """Analog kernel + TIKRNL.ANA task, driven only through SPORT1."""

    def __init__(self, log: bool = False):
        if drive.FIRMWARE_SET != 'analog109':
            raise RuntimeError('analog_kernel_dispatch needs the analog109 '
                               'firmware set; call select_firmware_set first')
        self.card = Card(serve=False)
        self.log = log
        self.sample = 0
        self.tx: list[int] = []
        self.tx_written = 0
        self.doorbell: collections.Counter = collections.Counter()
        self.commands: list[int] = []
        self.frames = 0
        self._cbs = (RX_CB(self._rx), TX_CB(self._tx), TIM_CB(self._timer))

    # --- SPORT1 -----------------------------------------------------------
    def _rx(self, cpu, port):
        return self.sample if port == 1 else 0

    def _tx(self, cpu, port, value):
        if port == 1:
            self.tx.append(value & 0xFFFF)

    def _timer(self, cpu, enabled):
        pass

    @property
    def doorbell_word(self) -> int:
        """DM(DM(0x2E7C) + 9), the way the kernel's own foreground names it."""
        descriptor = self.card.dm[DM_DOORBELL_DESC_PTR]
        return (descriptor + 9) if descriptor else 0

    # --- the host side of the command ring --------------------------------
    def push(self, vector: int) -> bool:
        """Queue one command: a PM address for the foreground to call."""
        dm = self.card.dm
        desc = dm[DM_CMD_DESC_PTR]
        if not desc or dm[desc + 3] != 0:
            return False           # not drained; one command at a time
        dm[dm[desc + 4]] = vector & 0xFFFF
        dm[desc + 3] = 2           # two bytes, low half of the word first
        self.commands.append(vector)
        return True

    def frame(self, receive_word: int = 0, budget: int = 2_000_000) -> int:
        """One SPORT1 codec frame: latch the sample, run the kernel to IDLE.

        The Si3056 exchanges one 16-bit word each way per 8 kHz frame, and
        SPORT1's receive and transmit interrupts share a vector on the 2181,
        which is what `adsp2181_sport1_frame` models.
        """
        self.sample = receive_word & 0xFFFF
        result = ADSP.adsp2181_sport1_frame(self.card.cpu, self.sample, budget)
        self.frames += 1
        if not ADSP.adsp2181_idle(self.card.cpu):
            raise RuntimeError('Analog kernel SPORT1 dispatch did not return '
                               f'to IDLE (pc={ADSP.adsp2181_pc(self.card.cpu):#06x})')
        if result & 0x10000:
            self.tx_written += 1
        return result & 0xFFFF

    @staticmethod
    def _call_word(target: int) -> int:
        """Encode ADSP-2181 CALL target, as kernel PM 0x0291-0x0295 does."""
        return 0x1C000F | ((target & 0x3FFF) << 4)

    def resume(self, entry: int, index: int, budget: int = 2_000_000) -> None:
        """Hand one dispatch to a registered TIKRNL entry.

        TIKRNL has patched PM 0x02B6 to CALL its continuation, so a completion
        queued in the byte ring would never reach PM 0x029E.  The host can
        write program memory over IDMA; lend that call site to the registered
        completion for exactly one frame and restore it immediately.  This is
        the same encoding kernel service 0x0017 used to claim the slot.
        """
        if not entry:
            return
        saved = self.card.pm[PM_FOREGROUND_SLOT]
        self.card.pm[PM_FOREGROUND_SLOT] = self._call_word(entry)
        try:
            ADSP.adsp2181_call(self.card.cpu, entry, KERNEL_IDLE)
            ADSP.adsp2181_run(self.card.cpu, budget)
            if not ADSP.adsp2181_idle(self.card.cpu):
                raise RuntimeError('TIKRNL completion did not return to IDLE')
        finally:
            self.card.pm[PM_FOREGROUND_SLOT] = saved
        if self.log:
            print(f'  sample {index}: resumed entry {entry:04x}')

    def service(self, index: int) -> None:
        """The host half: answer doorbells and serve overlay downloads."""
        dm = self.card.dm
        bits = dm[self.doorbell_word] if self.doorbell_word else 0
        if bits:
            dm[self.doorbell_word] = 0
            for bit in range(16):
                if bits & (1 << bit):
                    self.doorbell[bit] += 1
        # The task asks two ways: the doorbell bit above, and the page-change
        # strobe DM(0x3FC1) bit 8 that the direct backend serves.  Take either,
        # because on the Analog dial page the V.8 request arrives on the
        # continuation path (PM 0x078C) rather than the frame tail.
        wanted = dm[DM_DOWNLOAD_REQ]
        asking = bool(bits & 0x0002) or bool(dm[DM_STATUS] & 0x0100)
        if not asking or not wanted or wanted == self.card.resident:
            return
        # Serve whatever the task asks for, including the SIG image (0x0274 on
        # this build) it requests out of init.  Withholding one is not a
        # neutral experiment: the task is yielded until the host completes the
        # request, so a skipped download leaves it parked forever with
        # shellinptr unpublished and no page ever resident.
        description = self.card.download_overlay(wanted)
        if description is None:
            return
        self.card.switches.append((index, dm[DM_BOOTPAGE], wanted))
        if self.log:
            print(f'  sample {index}: served 0x{wanted:04x} {description}')
        self.resume(dm[drive.RESUME_DOWNLOAD], index)

    def boot(self) -> None:
        card = self.card
        card._download(drive.KERNEL)
        ADSP.adsp2181_run(card.cpu, 5000)
        if not ADSP.adsp2181_idle(card.cpu):
            raise RuntimeError('Analog kernel did not reach its idle loop')
        ADSP.adsp2181_set_callbacks(card.cpu, *self._cbs)
        # Let the foreground write its own five ring-descriptor pointers.  It
        # does that the first time the SPORT1 ISR queues a sample, so one
        # frame is enough; the direct backend has to plant them by hand.
        for _ in range(32):
            self.frame()
            if card.dm[DM_CMD_DESC_PTR]:
                break
        else:
            raise RuntimeError('Analog foreground did not initialise the '
                               'command ring')
        card._download(drive.TIKRNL)
        if not self.push(TASK_ENTRY):
            raise RuntimeError('could not queue the TIKRNL.ANA task entry')
        empty_slot = self._call_word(PM_DISPATCH)
        for _ in range(32):
            self.frame()
            if card.dm[DM_ENTRIES] and card.pm[PM_FOREGROUND_SLOT] != empty_slot:
                break
        else:
            raise RuntimeError('Analog kernel did not register TIKRNL.ANA')
        if card.pm[PM_FOREGROUND_SLOT] != self._call_word(SAMPLE_CONTINUATION):
            raise RuntimeError(
                'TIKRNL.ANA claimed PM 0x02B6 with an unexpected vector: '
                f'{card.pm[PM_FOREGROUND_SLOT]:#08x}')
        # Task init cleared PM 0x0900 upward, so only now is it safe to
        # download overlays.  DIAL calls shared routines beyond its own image;
        # V.OWN and FSK OWN supply them, exactly as in `Card.boot()`.
        for base_id in (V_OWN_ID, FSK_OWN_ID):
            entry = card.overlays.get(base_id)
            if entry is None:
                raise RuntimeError(f'missing base overlay 0x{base_id:04x}')
            card._download(entry[0])
        if card.download_overlay(DIAL_ID) is None:
            raise RuntimeError(f'no extracted image for 0x{DIAL_ID:04x}')
        card.served.clear()        # the boot page is not a page *switch*


class RationalResampler:
    """Streaming polyphase windowed-sinc resampler, up/down.

    The Analog codec runs at 9600 Hz (`Samplerate` code 4) and the RTP bearer
    at 8000, so the boundary needs 6:5 one way and 5:6 the other.  Linear
    interpolation is not good enough here and the measurement to prove it is
    already in the record: Session 249 put d-modem's two-point interpolation
    in this exact direction at ~20 dB, of which 19.5 dB survives an LTI fit,
    and run65 qualified a windowed sinc instead.
    """

    def __init__(self, up: int, down: int, taps_per_phase: int = 16):
        self.up, self.down = up, down
        self.taps = taps_per_phase
        length = taps_per_phase * up
        cutoff = 1.0 / max(up, down)
        centre = length // 2
        self.h = []
        for index in range(length):
            t = index - centre
            x = math.pi * cutoff * t
            sinc = 1.0 if t == 0 else math.sin(x) / x
            window = 0.54 + 0.46 * math.cos(2 * math.pi * t / length)
            self.h.append(up * cutoff * sinc * window)
        self.history: list[float] = [0.0] * (taps_per_phase + 2)
        self.consumed = 0          # inputs dropped off the front of history
        self.produced = 0          # outputs emitted so far

    def push(self, sample: float) -> list[float]:
        """Feed one input sample; return however many outputs it completes."""
        self.history.append(float(sample))
        available = self.consumed + len(self.history) - 1   # newest index
        out: list[float] = []
        lead = self.taps // 2
        while True:
            position = self.produced * self.down
            base, phase = divmod(position, self.up)
            if base + lead > available:
                break
            total = 0.0
            for k in range(self.taps):
                tap = phase + k * self.up
                if tap >= len(self.h):
                    break
                index = base + lead - k - self.consumed
                if 0 <= index < len(self.history):
                    total += self.h[tap] * self.history[index]
            out.append(total)
            self.produced += 1
        # trim history we can no longer need
        keep = self.taps + 2
        oldest_needed = (self.produced * self.down) // self.up - self.taps
        drop = max(0, oldest_needed - self.consumed - keep)
        if drop > 0:
            del self.history[:drop]
            self.consumed += drop
        return out


class AnalogKernelModem:
    """Card-compatible Analog modem driven by the real SPORT1 kernel."""

    firmware_set = 'analog109'

    def __init__(self, modem_role: str = 'calling', law: str = 'pcmu',
                 log: bool = False, codec_rate: int = 9600,
                 bearer_rate: int = 8000):
        self.driver = AnalogKernelDispatch(log=log)
        self.card = self.driver.card
        self.dm = self.card.dm
        self.pm = self.card.pm
        self.overlays = self.card.overlays
        self.switches = self.card.switches
        self.forced_info_samples = self.card.forced_info_samples
        self.modem_role = modem_role
        self.law = law
        self.resident = 0
        # The codec boundary.  V.8 asks for `Samplerate` code 4 -- 9600 Hz --
        # in its own init at PM 0x3655, and its tone constants are 9600 Hz
        # constants: 0x0D11/0x0FBC are V.21 channel 1's 980/1180 and 0x1156 is
        # the V.25 calling tone, all to within 0.015%.  Clocking SPORT1 at the
        # bearer's 8000 instead emits every one of them at 5/6.
        self.codec_rate = codec_rate
        self.bearer_rate = bearer_rate
        self._to_codec = None
        self._to_bearer = None
        self._bearer_out: collections.deque = collections.deque()
        self._last_out = 0
        self._pins_applied = 0
        self._cursor_last = -1
        if codec_rate != bearer_rate:
            common = math.gcd(codec_rate, bearer_rate)
            up, down = codec_rate // common, bearer_rate // common
            self._to_codec = RationalResampler(up, down)
            self._to_bearer = RationalResampler(down, up)
            print(f'[analog-kernel] codec {codec_rate} Hz, bearer '
                  f'{bearer_rate} Hz: resampling {up}:{down} in, {down}:{up} out')

    def __getattr__(self, name):
        # Everything the harnesses reach for that is plain Card state -- the
        # emulator handle, the overlay index, the G.711 encoder.
        return getattr(self.card, name)

    def boot(self) -> None:
        self.driver.boot()
        self.resident = self.card.resident

    def configure_modem(self, role: str, law: str = 'pcmu') -> None:
        # The ADDSP §5.4.1 database writes, the same ones the direct backend
        # makes.  GEN_SETUP1 bit 3 selects calling (0x048C) over answer.
        self.card.configure_g711_law(law)
        role = role or self.modem_role
        self.card.configure_modem(role, law)
        if DIAL_ORIGINATE:
            # Let DIAL consume the NORM bit and run its own training init, the
            # way the answering backends do, instead of leaving the page
            # untouched and faking the V.8 request from outside.
            if self.card.resident != DIAL_ID:
                raise RuntimeError(
                    f'DIAL is not resident (0x{self.card.resident:04x}); the '
                    'NORM entry only means anything on the DIAL page')
            before = self.dm[DM_GEN_SETUP1]
            self.card._run(DIAL_NORM_ENTRY, 1_000_000)
            print(f'[analog-kernel] DIAL NORM entry PM '
                  f'0x{DIAL_NORM_ENTRY:04x} run for {role}: GEN_SETUP1 '
                  f'0x{before:04x} -> 0x{self.dm[DM_GEN_SETUP1]:04x}, '
                  f'TrnProgress 0x{self.dm[DM_TRNPROGRESS]:04x}')

    def line_rx_word(self, code: int, linear: int) -> int:
        return self.card.line_rx_word(code, linear)

    def _codec_frame(self, word: int, sample_index: int, budget: int) -> int:
        """One codec frame, dispatched entirely by the kernel."""
        before = self.card.resident
        for address, value, gate in PIN_DM:
            if gate is not None and self.dm[gate[0]] != gate[1]:
                continue
            self.dm[address] = value
            self._pins_applied += 1
        self.driver.frame(word & 0xFFFF, budget)
        self.driver.service(sample_index)
        if TRACE_CURSOR:
            cursor = self.dm[TRACE_CURSOR]
            if cursor != self._cursor_last:
                self._cursor_last = cursor
                print(f'[analog-kernel] cursor 0x{cursor:04x} '
                      f'(sample {sample_index}, pins {self._pins_applied})')
        if self.card.resident != before:
            self.resident = self.card.resident
        pointer = self.dm[DM_TX_POINTER] & 0x3FFF
        value = self.dm[pointer] if pointer else 0
        return value - 0x10000 if value & 0x8000 else value

    def frame_fast(self, word: int, sample_index: int = 0,
                   budget: int = 2_000_000) -> int:
        """One *bearer* sample in, one bearer sample out.

        With the codec at the bearer's own rate this is one kernel frame.
        With the codec at 9600 and the bearer at 8000 it is six frames per
        five calls, and the resampling happens here rather than anywhere the
        firmware can see: to the card the line simply runs at 9600.
        """
        if self._to_codec is None:
            return self._codec_frame(word, sample_index, budget)
        sample = word - 0x10000 if word & 0x8000 else word
        for codec_sample in self._to_codec.push(sample):
            value = max(-32768, min(32767, int(round(codec_sample))))
            out = self._codec_frame(value & 0xFFFF, sample_index, budget)
            self._bearer_out.extend(self._to_bearer.push(out))
        if self._bearer_out:
            self._last_out = max(-32768, min(32767,
                                             int(round(self._bearer_out.popleft()))))
        return self._last_out


def create_analog_kernel_modem(modem_role: str = 'calling',
                               law: str = 'pcmu') -> AnalogKernelModem:
    return AnalogKernelModem(modem_role=modem_role, law=law)


def decode_mulaw(code: int) -> int:
    """ITU-T G.711 mu-law expansion to conventional PCM16."""
    value = (~code) & 0xFF
    sample = (((value & 0x0F) << 3) + 0x84) << ((value >> 4) & 7)
    sample -= 0x84
    return -sample if value & 0x80 else sample


def make_stimulus(kind: str, samples: int, freq: int, amp: int) -> list[int]:
    """Signed-linear input, including normative V.8 ANSam (§7.2).

    ANSam is a 2100 Hz carrier with a 15 Hz sinusoidal envelope ranging from
    0.8 to 1.2 of average amplitude and 180-degree reversals every 450 ms.
    """
    if kind == 'silence' or not freq:
        return [0] * samples
    result = []
    phase_offset = 0.0
    reversal_samples = int(0.450 * SAMPLE_RATE)
    for i in range(samples):
        if kind == 'ansam' and i and i % reversal_samples == 0:
            phase_offset += math.pi
        envelope = (1.0 + 0.2 * math.sin(2 * math.pi * 15 * i / SAMPLE_RATE)
                    if kind == 'ansam' else 1.0)
        sample = int(amp * envelope
                     * math.sin(2 * math.pi * freq * i / SAMPLE_RATE
                                + phase_offset))
        result.append(max(-32768, min(32767, sample)))
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--replay', type=Path,
                    help='drive the card with a captured G.711 mu-law stream '
                         '(e.g. a loopback run\'s caller.rx.ulaw) instead of a '
                         'synthetic stimulus, so the peer is a real one')
    ap.add_argument('--samples', type=int, default=16000)
    ap.add_argument('--stimulus', default='ansam',
                    choices=['ansam', 'tone', 'silence'])
    ap.add_argument('--freq', type=int, default=2100)
    ap.add_argument('--amp', type=int, default=3900)
    ap.add_argument('--role', default='calling', choices=['calling', 'answer'])
    ap.add_argument('--log', action='store_true')
    args = ap.parse_args()

    drive.select_firmware_set('analog109')
    modem = AnalogKernelModem(modem_role=args.role, log=args.log)
    modem.boot()
    modem.configure_modem(args.role)
    print(f'[analog-kernel] booted; resident=0x{modem.card.resident:04x} '
          f'role={args.role}')

    if args.replay:
        codes = args.replay.read_bytes()[:args.samples]
        # The bearer is G.711 on the wire; the Analog codec boundary is
        # signed linear, which is what `Card.line_rx_word` hands over.
        stimulus = [decode_mulaw(code) for code in codes]
        print(f'[analog-kernel] replaying {len(stimulus)} samples from '
              f'{args.replay}')
    else:
        stimulus = make_stimulus(args.stimulus, args.samples, args.freq,
                                 args.amp)
    rxsample_contents: set[tuple[int, ...]] = set()
    shellinptr: collections.Counter = collections.Counter()
    trn: dict[int, int] = {}
    peak_level = peak_count = 0
    for index, sample in enumerate(stimulus):
        modem.frame_fast(sample & 0xFFFF, index)
        dm = modem.dm
        rxsample_contents.add(tuple(dm[a] for a in range(DM_RXSAMPLE,
                                                         DM_RXSAMPLE + 6)))
        shellinptr[dm[DM_SHELLINPTR]] += 1
        trn.setdefault(dm[0x3FC2], index)
        peak_level = max(peak_level, dm[0x07BC])
        peak_count = max(peak_count, dm[0x07BD])

    print(f'[analog-kernel] frames={modem.driver.frames} '
          f'sport1 tx writes={modem.driver.tx_written}')
    print(f'[analog-kernel] RXSAMPLE distinct contents={len(rxsample_contents)}'
          f'  (direct backend: 24 over 60,001 samples)')
    print('[analog-kernel] shellinptr: ' + ', '.join(
        '%04x=%.1f%%' % (value, 100.0 * n / len(stimulus))
        for value, n in shellinptr.most_common(4)))
    print(f'[analog-kernel] peak DM(07BC)={peak_level} '
          f'peak DM(07BD)={peak_count} (escape needs 1920, '
          f'threshold DM(0748)={modem.dm[0x0748]})')
    print('[analog-kernel] doorbell bits: '
          + (', '.join(f'{bit}={n}' for bit, n
                       in sorted(modem.driver.doorbell.items())) or 'none'))
    print('[analog-kernel] page switches: '
          + (', '.join(f'{i}:page{p}->0x{d:04x}'
                       for i, p, d in modem.card.switches) or 'none'))
    print('[analog-kernel] TrnProgress first seen: '
          + ', '.join(f'0x{value:04x}@{i}' for value, i in sorted(trn.items())))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
