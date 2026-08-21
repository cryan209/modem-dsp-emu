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
import atexit
import collections
import ctypes
import math
import os
import struct
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
ADSP.adsp2181_pin_dm.argtypes = [ctypes.c_void_p, ctypes.c_uint16,
                                 ctypes.c_uint16, ctypes.c_int]

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
#
# A trailing `>SECONDS` holds the pin back until that point in the call, which
# a state gate alone cannot express.  Session 250's V.90A caller enters its
# `0x0092` park at 12.55 s and the answering end only reaches `0x00b0` at
# 14.94 s: released on entry the caller walks on and the *answerer* falls back
# to INFO, 3 runs out of 3.  "Which end is ahead of the other" is a question
# about time, so the instrument needs a clock as well as a state.
#
# **`=!VALUE` makes it a hard pin, held inside the sample as well as between
# them.** A plain pin is re-applied once per codec frame, which is fine for a
# word the firmware reads on a later frame than it writes and useless for one
# it writes and reads inside the same one. Session 251's V.90A caller is the
# second case: `0x00c2`'s record stores `0x0040` over `DM(0x20EB)` and
# `0x00c3`'s condition reads bit 12 of it a handful of instructions later, so a
# per-frame pin reads back as "no effect" when it simply never held. The hard
# form engages the core's own store hook (`adsp2181_pin_dm`, the same one
# `EICON_PIN_DM` uses on the tower) while the gate matches and releases it when
# the gate stops matching -- so it overrides the firmware's own stores, which a
# soft pin does not, and it is that much more of a stand-in for it.
def _parse_pin(field: str):
    body, _, gate = field.partition('@')
    address, _, value = body.partition('=')
    hard = value.startswith('!')
    if hard:
        value = value[1:]
    gate_pair = None
    after = 0.0
    if gate:
        gate, _, delay = gate.partition('>')
        if delay:
            after = float(delay)
        if gate:
            gate_address, _, gate_value = gate.partition(':')
            gate_pair = (int(gate_address, 0) & 0x3FFF,
                         int(gate_value, 0) & 0xFFFF)
    return (int(address, 0) & 0x3FFF, int(value, 0) & 0xFFFF, gate_pair, after,
            hard)


PIN_DM = tuple(_parse_pin(f) for f
               in os.environ.get('EICON_ANALOG_PIN_DM', '').split(',')
               if f.strip())
# The V.8 script cursor (`docs/analog_v8_oracle.md`): print each state the walk
# enters.  Without this a pinned run that never reached the gated state and a
# pinned run that reached it and did nothing look identical from the wire.
TRACE_CURSOR = int(os.environ.get('EICON_ANALOG_TRACE_CURSOR', '0'), 0)
# A DM sampler on the *bearer* frame boundary, so a live endpoint and an
# offline replay of that endpoint's own capture produce rows that line up by
# sample index and can simply be diffed. `EICON_ANALOG_DM_CSV=<path>` with
# `EICON_ANALOG_DM_LIST=0x776,0x777,...`; the cursor DM(0x049F) is always
# included as the first column so a row can be attributed to a script state.
# This exists because the ADSP disassembler mislabels direct DM reads and
# writes (README), so which word feeds a detector is a question to measure
# rather than to read off a decode.
DM_CSV_PATH = os.environ.get('EICON_ANALOG_DM_CSV', '')
DM_CSV_LIST = tuple(int(field, 0) & 0x3FFF for field
                    in os.environ.get('EICON_ANALOG_DM_LIST', '').split(',')
                    if field.strip())
DM_CSV_EVERY = max(1, int(os.environ.get('EICON_ANALOG_DM_EVERY', '1'), 0))


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
        # Keep all SPORT callback writes for boundary diagnostics.  The
        # physical Analog path uses SPORT1, but retaining port 0 too makes it
        # possible to distinguish an idle SPORT1 TX from a callback-selection
        # mistake without changing the codec source.
        self.tx_all: list[tuple[int, int, int]] = []
        self.tx_written = 0
        self.doorbell: collections.Counter = collections.Counter()
        self.commands: list[int] = []
        self.frames = 0
        self._cbs = (RX_CB(self._rx), TX_CB(self._tx), TIM_CB(self._timer))

    # --- SPORT1 -----------------------------------------------------------
    def _rx(self, cpu, port):
        return self.sample if port == 1 else 0

    def _tx(self, cpu, port, value):
        self.tx_all.append((self.frames, port, value & 0xFFFF))
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
        self.last_frame_result = result
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
        download_entry = 0
        if not asking or not wanted or wanted == self.card.resident:
            pass
        else:
            # Serve whatever the task asks for, including the SIG image
            # (0x0274 on this build) it requests out of init.  Withholding one
            # is not neutral: the task is yielded until the host completes
            # the request, so a skipped download leaves it parked forever.
            description = self.card.download_overlay(wanted)
            if description is None:
                return
            self.card.switches.append((index, dm[DM_BOOTPAGE], wanted))
            download_entry = dm[drive.RESUME_DOWNLOAD]
            if self.log:
                print(f'  sample {index}: served 0x{wanted:04x} {description}')
        # The descriptor is a table of host-service entries, not merely a
        # diagnostic counter.  The PRI kernel service dispatches every
        # asserted slot; Analog needs the same handoff.  In particular, the
        # V.90 task raises bit 10 later in the call and leaves PM 0x08f5 in
        # DM(0x31c4).  Dropping that request makes the task wait forever even
        # though the page-change request itself was served.
        for bit in range(16):
            if not (bits & (1 << bit)):
                continue
            entry = dm[DM_ENTRIES + bit]
            if entry:
                if self.log:
                    print(f'  sample {index}: doorbell bit {bit} -> '
                          f'entry {entry:04x}')
                self.resume(entry, index)
        if download_entry and not (bits & 0x0002):
            # Some Analog firmware revisions publish the page-change strobe
            # without retaining doorbell bit 1.  Keep the legacy completion
            # path for that form of the request.
            self.resume(download_entry, index)

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

    def __init__(self, up: int, down: int, taps_per_phase: int = 16,
                 phase_offset: int = 0, cutoff_multiplier: float = 1.0):
        self.up, self.down = up, down
        self.taps = taps_per_phase
        self.phase_offset = int(phase_offset)
        length = taps_per_phase * up
        cutoff = (1.0 / max(up, down)) * float(cutoff_multiplier)
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
            position = self.produced * self.down + self.phase_offset
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


class LagrangeResampler:
    """Streaming six-point Lagrange/Farrow converter.

    The analogue firmware's 8 kHz -> 9.6 kHz receive path rebuilds six
    interpolation coefficients for each fractional phase.  This is kept as
    an opt-in diagnostic because the windowed-sinc converter remains the
    qualified default for the host media path; it lets the live loopback test
    the firmware-shaped boundary without silently changing the baseline.
    """

    def __init__(self, up: int, down: int, phase_offset: float = 0.0):
        self.up, self.down = up, down
        self.phase_offset = float(phase_offset)
        self.history: list[float] = [0.0] * 6
        self.consumed = -6
        self.produced = 0

    @staticmethod
    def _coefficients(frac: float) -> list[float]:
        # Six samples at integer offsets -2..+3 around floor(position).
        result = []
        for j in range(6):
            offset = j - 2
            value = 1.0
            for k in range(6):
                if k == j:
                    continue
                value *= (frac - (k - 2)) / (offset - (k - 2))
            result.append(value)
        return result

    def push(self, sample: float) -> list[float]:
        self.history.append(float(sample))
        available = self.consumed + len(self.history) - 1
        out: list[float] = []
        while True:
            position = (self.produced * self.down / self.up
                        + self.phase_offset)
            base = math.floor(position)
            if base + 3 > available:
                break
            frac = position - base
            coefficients = self._coefficients(frac)
            total = 0.0
            for j, coefficient in enumerate(coefficients):
                index = base + j - 2 - self.consumed
                if 0 <= index < len(self.history):
                    total += coefficient * self.history[index]
            out.append(total)
            self.produced += 1
        oldest_needed = math.floor(
            self.produced * self.down / self.up) - 2
        drop = max(0, oldest_needed - self.consumed - 8)
        if drop > 0:
            del self.history[:drop]
            self.consumed += drop
        return out


ANALOG_RESAMPLER_KIND = os.environ.get(
    'EICON_ANALOG_RESAMPLER_KIND', 'sinc').strip().lower()
ANALOG_RESAMPLER_IN_KIND = os.environ.get(
    'EICON_ANALOG_RESAMPLER_IN_KIND', ANALOG_RESAMPLER_KIND).strip().lower()
ANALOG_RESAMPLER_OUT_KIND = os.environ.get(
    'EICON_ANALOG_RESAMPLER_OUT_KIND', ANALOG_RESAMPLER_KIND).strip().lower()
try:
    ANALOG_RESAMPLER_IN_LAGRANGE_PHASE = float(os.environ.get(
        'EICON_ANALOG_RESAMPLER_IN_LAGRANGE_PHASE', '0'))
except ValueError:
    ANALOG_RESAMPLER_IN_LAGRANGE_PHASE = 0.0


ANALOG_RESAMPLER_TAPS = int(
    os.environ.get('EICON_ANALOG_RESAMPLER_TAPS', '16'), 0)
ANALOG_RESAMPLER_IN_TAPS = int(
    os.environ.get('EICON_ANALOG_RESAMPLER_IN_TAPS', str(ANALOG_RESAMPLER_TAPS)), 0)
ANALOG_RESAMPLER_OUT_TAPS = int(
    os.environ.get('EICON_ANALOG_RESAMPLER_OUT_TAPS', str(ANALOG_RESAMPLER_TAPS)), 0)
ANALOG_RESAMPLER_CUTOFF_MULT = float(
    os.environ.get('EICON_ANALOG_RESAMPLER_CUTOFF_MULT', '1.0'))
ANALOG_RESAMPLER_IN_CUTOFF_MULT = float(
    os.environ.get('EICON_ANALOG_RESAMPLER_IN_CUTOFF_MULT',
                   str(ANALOG_RESAMPLER_CUTOFF_MULT)))
ANALOG_RESAMPLER_IN_PHASE = int(
    os.environ.get('EICON_ANALOG_RESAMPLER_IN_PHASE', '0'), 0)
ANALOG_RESAMPLER_OUT_CUTOFF_MULT = float(
    os.environ.get('EICON_ANALOG_RESAMPLER_OUT_CUTOFF_MULT',
                   str(ANALOG_RESAMPLER_CUTOFF_MULT)))
ANALOG_RESAMPLER_OUT_PHASE = int(
    os.environ.get('EICON_ANALOG_RESAMPLER_OUT_PHASE', '0'), 0)
ANALOG_USE_SPORT_TX = os.environ.get('EICON_ANALOG_USE_SPORT_TX', '0') != '0'
# Diagnostic bridge: preserve the generic DM pointer through V.8/INFO, then
# use the physical SPORT1 TX latch once the analogue V.90 page owns the line.
# This isolates the late page-13 handoff without changing early negotiation.
ANALOG_USE_SPORT_TX_AFTER_V90A = (
    os.environ.get('EICON_ANALOG_USE_SPORT_TX_AFTER_V90A', '0') != '0')
# Diagnostic only: hold the V.90A transmit selector at the symbol reader or
# silence writer after the requested bearer-time offset.  The resident V.90A
# code normally selects this through its record table at PM 0x258a; holding
# it lets the live-pair experiment separate that decision from the codec and
# line path without changing the default emulation.
V90A_TX_SHAPER = os.environ.get('EICON_V90A_TX_SHAPER', '').strip().lower()
V90A_TX_SHAPER_AFTER = float(os.environ.get(
    'EICON_V90A_TX_SHAPER_AFTER', '0'))
# Optional state gate for the selector probe.  The wall-clock form above is
# useful for quick A/Bs, but it also overrides the preceding 0x0095 record and
# can hide the transition being measured.  A state gate lets us test the
# native-looking Phase-3 reader only in the caller's 0x00b0/0x00b3 records.
V90A_TX_SHAPER_STATES = frozenset(
    int(field, 0) & 0xFFFF
    for field in os.environ.get('EICON_V90A_TX_SHAPER_STATES', '').split(',')
    if field.strip())
# Optional remote-state gate for the selector probe.  When the answerer's
# TrnProgress is exported through EICON_V90D_STATE_EXPORT and imported by the
# caller's EICON_V90A_TX_PEER_STATE, this selects the reader only while the
# peer is in the named states.  This is a live control experiment: it avoids
# making the caller's selector depend on wall-clock alignment with the peer.
V90A_TX_SHAPER_PEER_STATES = frozenset(
    int(field, 0) & 0xFFFF
    for field in os.environ.get('EICON_V90A_TX_SHAPER_PEER_STATES', '').split(',')
    if field.strip())
# Diagnostic only: replace the page-13 reader's output scale while the
# selector probe is active.  This is intentionally coupled to the shaper
# gate so it cannot alter V.8/INFO or the normal firmware path.
V90A_TX_SCALE = os.environ.get('EICON_V90A_TX_SCALE', '').strip()
# Diagnostic only: override the V.90A LMS shift used by the second receive
# training window.  The stock page uses DM(0x2121)=-4; the corresponding V.90D
# equalizer uses -6.  Keep this opt-in until a live, unprimed pair proves that
# the lower step fixes convergence rather than merely hiding divergence.
V90A_EQ_SHIFT = os.environ.get('EICON_V90A_EQ_SHIFT', '').strip()
# Diagnostic only: apply one post-training gain normalization to the V.90A
# equalizer's 216 double-precision taps when the caller enters 0x00c0.  The
# normal page disables LMS updates there, so this isolates the measured
# under-converged response from the state-machine exchange without repeatedly
# multiplying taps on every frame.
try:
    V90A_EQ_COEFF_SCALE = float(os.environ.get(
        'EICON_V90A_EQ_COEFF_SCALE', '0'))
except ValueError:
    V90A_EQ_COEFF_SCALE = 0.0


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
        self._tx_pcm = None
        tx_pcm_path = os.environ.get('EICON_ANALOG_TX_PCM', '')
        if tx_pcm_path:
            self._tx_pcm = open(tx_pcm_path, 'wb', buffering=0)
            atexit.register(self._tx_pcm.close)
            print(f'[analog-kernel] raw codec TX PCM -> {tx_pcm_path}')
        self._pins_applied = 0
        self._hard_pins: dict[int, bool] = {}
        self._v90a_shaper_active = False
        self._v90a_shaper_value = 0
        self._v90a_eq_scaled = False
        self._cursor_last = -1
        self._dm_csv = None
        if DM_CSV_PATH and DM_CSV_LIST:
            self._dm_csv = open(DM_CSV_PATH, 'w', buffering=1 << 16)
            # A live endpoint is shut down with a signal, so the buffer has to
            # be flushed by something that runs on the way out or the last
            # seconds -- usually the interesting ones -- are lost.
            atexit.register(self._dm_csv.flush)
            self._dm_csv.write('sample,cursor,'
                               + ','.join('dm%04x' % a for a in DM_CSV_LIST)
                               + '\n')
            print(f'[analog-kernel] DM sampler -> {DM_CSV_PATH}: '
                  + ' '.join('0x%04x' % a for a in DM_CSV_LIST)
                  + f' (every {DM_CSV_EVERY} bearer frames)')
        if codec_rate != bearer_rate:
            common = math.gcd(codec_rate, bearer_rate)
            up, down = codec_rate // common, bearer_rate // common
            in_taps = max(4, ANALOG_RESAMPLER_IN_TAPS)
            out_taps = max(4, ANALOG_RESAMPLER_OUT_TAPS)
            in_resampler = (LagrangeResampler
                            if ANALOG_RESAMPLER_IN_KIND == 'lagrange'
                            else RationalResampler)
            out_resampler = (LagrangeResampler
                             if ANALOG_RESAMPLER_OUT_KIND == 'lagrange'
                             else RationalResampler)
            if in_resampler is LagrangeResampler:
                self._to_codec = in_resampler(
                    up, down, ANALOG_RESAMPLER_IN_LAGRANGE_PHASE)
            else:
                self._to_codec = in_resampler(
                    up, down, taps_per_phase=in_taps,
                    phase_offset=ANALOG_RESAMPLER_IN_PHASE,
                    cutoff_multiplier=ANALOG_RESAMPLER_IN_CUTOFF_MULT)
            if out_resampler is LagrangeResampler:
                self._to_bearer = out_resampler(down, up)
            else:
                self._to_bearer = out_resampler(
                    down, up, taps_per_phase=out_taps,
                    phase_offset=ANALOG_RESAMPLER_OUT_PHASE,
                    cutoff_multiplier=ANALOG_RESAMPLER_OUT_CUTOFF_MULT)
            print(f'[analog-kernel] codec {codec_rate} Hz, bearer '
                  f'{bearer_rate} Hz: resampling {up}:{down} in, {down}:{up} '
                  f'out ({ANALOG_RESAMPLER_IN_KIND}/'
                  f'{ANALOG_RESAMPLER_OUT_KIND}, '
                  f'{in_taps}/{out_taps} taps/phase, '
                  f'cutoff x{ANALOG_RESAMPLER_IN_CUTOFF_MULT:g}/'
                  f'{ANALOG_RESAMPLER_OUT_CUTOFF_MULT:g}, '
                  f'phase {ANALOG_RESAMPLER_IN_PHASE}/'
                  f'{ANALOG_RESAMPLER_OUT_PHASE})')

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
        for index, (address, value, gate, after, hard) in enumerate(PIN_DM):
            active = not (after and sample_index < after * 8000)
            if active and gate is not None and self.dm[gate[0]] != gate[1]:
                active = False
            if hard:
                # Engage and release the core's store hook on the gate's edges
                # rather than writing the word, so it holds through the stores
                # the firmware itself makes inside this frame.
                if active != self._hard_pins.get(index, False):
                    ADSP.adsp2181_pin_dm(self.card.cpu, address, value,
                                         1 if active else 0)
                    self._hard_pins[index] = active
                if not active:
                    continue
                # The core hook only substitutes on a *store*, so a word the
                # firmware reads without rewriting keeps its old value forever
                # under the hook alone. Write it too, on every frame, exactly
                # as the soft form does.
                self.dm[address] = value
            elif not active:
                continue
            else:
                self.dm[address] = value
            self._pins_applied += 1
            # Say so the first time. Without this a gate that never matches and
            # a pin that applied and did nothing produce identical output, and
            # the run reads as a negative when it measured nothing at all
            # (handoff §0.4). The count is otherwise only printed under
            # EICON_ANALOG_TRACE_CURSOR.
            if self._pins_applied == 1:
                print(f'[analog-kernel] PINNED FIRMWARE STATE: '
                      f'DM(0x{address:04x}) = 0x{value:04x} first applied at '
                      f'sample {sample_index}')
        # The SPORT-dispatch backend does not enter Card.frame(); publish the
        # opt-in V.90A TXD0 source immediately before the kernel's SPORT frame
        # so PM 0x3d84 can consume it after TIKRNL housekeeping.
        if hasattr(self.card, '_service_v90a_tx_request'):
            self.card._service_v90a_tx_request()
        if V90A_EQ_SHIFT and self.card.resident == 0x026B:
            # This is deliberately a soft frame-boundary override: it is a
            # measurement control, not a firmware pin.  The page reads the
            # shift as its LMS update is entered, after this boundary.
            self.dm[0x2121] = int(V90A_EQ_SHIFT, 0) & 0xFFFF
        self.driver.frame(word & 0xFFFF, budget)
        self.driver.service(sample_index)
        # Keep the kernel-dispatch backend on the same live state-exchange
        # boundary as Card.frame().  This is required by opt-in peer-coupled
        # diagnostics such as the V.90A selector shaper; without it the
        # caller's imported V.90D state remains unset even though the direct
        # endpoint is exporting it.
        if hasattr(self.card, '_exchange_v90_state'):
            self.card._exchange_v90_state(sample_index)
        # The native host can see a TXD0 request raised by the just-completed
        # continuation only after that SPORT frame returns.  Mirror the
        # direct-card path's second mailbox service here so a request is
        # staged for the next frame at the same boundary.  It is inert for
        # the normal firmware-owned mailbox and only affects the opt-in
        # diagnostic TXD0 sources.
        if hasattr(self.card, '_service_v90a_tx_request'):
            self.card._service_v90a_tx_request()
        if (V90A_EQ_COEFF_SCALE > 0.0 and not self._v90a_eq_scaled
                and self.card.resident == 0x026B
                and (self.dm[0x20F9] & 0xFFFF) == 0x00C0):
            self._scale_v90a_equalizer(V90A_EQ_COEFF_SCALE)
        shaper_requested = (V90A_TX_SHAPER in ('reader', 'silence')
                            and self.card.resident == 0x026B)
        if shaper_requested and V90A_TX_SHAPER_STATES:
            shaper_requested = ((self.dm[0x20F9] & 0xFFFF)
                                in V90A_TX_SHAPER_STATES)
        elif shaper_requested and not V90A_TX_SHAPER_PEER_STATES:
            shaper_requested = sample_index >= V90A_TX_SHAPER_AFTER * 8000
        if shaper_requested and V90A_TX_SHAPER_PEER_STATES:
            shaper_requested = (
                getattr(self.card, '_v90a_peer_state', None)
                in V90A_TX_SHAPER_PEER_STATES)
        if shaper_requested:
            # PM 0x258a stores the selector during the page pass.  A hard
            # store hook is intentional here: a plain frame-boundary write
            # would be overwritten again before the next symbol pass.
            selector = 0x32CA if V90A_TX_SHAPER == 'reader' else 0x32C4
            if (not self._v90a_shaper_active
                    or self._v90a_shaper_value != selector):
                ADSP.adsp2181_pin_dm(self.card.cpu, 0x2119, selector, 1)
                self._v90a_shaper_active = True
                self._v90a_shaper_value = selector
                print(f'[analog-kernel] V90A TX shaper: DM(0x2119) = '
                      f'0x{selector:04x} from sample {sample_index}')
            self.dm[0x2119] = selector
            if V90A_TX_SCALE:
                try:
                    scale = int(V90A_TX_SCALE, 0) & 0xFFFF
                except ValueError:
                    scale = None
                if scale is not None:
                    ADSP.adsp2181_pin_dm(self.card.cpu, 0x211f, scale, 1)
                    self.dm[0x211f] = scale
        elif self._v90a_shaper_active:
            ADSP.adsp2181_pin_dm(self.card.cpu, 0x2119,
                                 self._v90a_shaper_value, 0)
            self._v90a_shaper_active = False
        if TRACE_CURSOR:
            cursor = self.dm[TRACE_CURSOR]
            if cursor != self._cursor_last:
                self._cursor_last = cursor
                print(f'[analog-kernel] cursor 0x{cursor:04x} '
                      f'(sample {sample_index}, pins {self._pins_applied})')
        if self.card.resident != before:
            self.resident = self.card.resident
        use_sport_tx = (ANALOG_USE_SPORT_TX or
                        (ANALOG_USE_SPORT_TX_AFTER_V90A
                         and self.card.resident == 0x026B))
        if use_sport_tx:
            # The SPORT latch is the physical codec-side TX word. The SPORT1
            # callback is the authoritative value here. `last_frame_result`
            # is only the emulator's frame-status return and can be zero even
            # when the SPORT callback wrote a word.
            value = self.driver.tx[-1] if self.driver.tx else 0
        else:
            pointer = self.dm[DM_TX_POINTER] & 0x3FFF
            value = self.dm[pointer] if pointer else 0
        return value - 0x10000 if value & 0x8000 else value

    def _scale_v90a_equalizer(self, scale: float) -> None:
        """Diagnostic normalization of the V.90A 216-tap LMS result."""
        for offset in range(0x00D8):
            hi = self.pm[0x1EB4 + offset] & 0xFFFF
            lo = self.pm[0x21F4 + offset] & 0xFFFF
            value = (hi << 16) | lo
            if value & 0x80000000:
                value -= 0x100000000
            value = max(-0x80000000, min(0x7FFFFFFF,
                                         int(round(value * scale))))
            self.pm[0x1EB4 + offset] = (value >> 16) & 0xFFFF
            self.pm[0x21F4 + offset] = value & 0xFFFF
        self._v90a_eq_scaled = True
        print(f'[analog-kernel] diagnostic V90A equalizer coefficient scale '
              f'{scale:g} applied at caller 0x00c0')

    def frame_fast(self, word: int, sample_index: int = 0,
                   budget: int = 2_000_000) -> int:
        """One *bearer* sample in, one bearer sample out.

        With the codec at the bearer's own rate this is one kernel frame.
        With the codec at 9600 and the bearer at 8000 it is six frames per
        five calls, and the resampling happens here rather than anywhere the
        firmware can see: to the card the line simply runs at 9600.

        `word` is a 16-bit line word and is masked as one before anything
        else. Python integers are not 16 bits: `line_rx_word()` hands the
        media loop a *signed* linear sample, and `-128 & 0x8000` is 0x8000, so
        sign-extending without masking first subtracted 0x10000 from a value
        that was already negative and the clamp below turned every negative
        sample in the call into full-scale -32768. `Card._present_line` masks;
        this path did not, which is why only the Analog kernel-dispatch
        backend saw it -- and that backend is the V.90A caller.
        """
        word &= 0xFFFF
        if self._dm_csv is not None and sample_index % DM_CSV_EVERY == 0:
            dm = self.dm
            self._dm_csv.write(
                '%d,%04x,%s\n' % (sample_index, dm[0x049F],
                                  ','.join(str(dm[a]) for a in DM_CSV_LIST)))
        if self._to_codec is None:
            return self._codec_frame(word, sample_index, budget)
        sample = word - 0x10000 if word & 0x8000 else word
        for codec_sample in self._to_codec.push(sample):
            value = max(-32768, min(32767, int(round(codec_sample))))
            out = self._codec_frame(value & 0xFFFF, sample_index, budget)
            if self._tx_pcm is not None:
                self._tx_pcm.write(struct.pack('<h', max(-32768, min(
                    32767, int(out)))))
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
