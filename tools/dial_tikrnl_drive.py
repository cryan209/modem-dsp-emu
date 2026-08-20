#!/usr/bin/env python3
"""Run DIAL under TIKRNL, the way the card actually dispatches it.

`tools/dial_standalone_drive.py` proves DIAL processes audio, but it calls
DIAL's internals (PM 0x1B9C / 0x1BBD) as subroutines, with the DIAL overlay
layered straight onto the PRI kernel.  That is not how the card runs it.

The real chain is kernel -> task -> overlay:

  * The PRI 30M kernel (download 0x0009) owns PM 0x0000-0x05EB and the
    interrupt/service vector table.  Slots 0x0001-0x0003, 0x000A-0x000B,
    0x000E, 0x0015, 0x0017, 0x0019, 0x001E are kernel service entries; the
    genuine interrupt vectors in between are `RTI`.
  * TIKRNL81.F34 (download 0x0258) is the modem *task*.  Its entry is
    download symbol 0 = PM 0x0672.  Running it initialises the task, then
    registers a per-frame continuation through kernel service 0x0017, which
    patches PM 0x000A to `CALL 0x06FC` and PM 0x0000 to `CALL 0x08F6`.
  * DIAL/FSK/FAX.F34 (download 0x0262) is an *overlay* on the task.  Its
    download replaces the two stub words TIKRNL keeps at PM 0x08F0/0x08F1
    (`RTS`, `RTS`) with `JUMP 0x1B9C` / `JUMP 0x1BBD`.

TIKRNL's per-frame loop (PM 0x06BB-0x06EE) is what calls the overlay:

    06bb  CALL $0002        ; kernel queue service, host command dispatch
    06c0  CALL (I4)         ; dispatch the queued command
    06c2  CALL $064A        ; frame housekeeping / status publish
    06c7  AR = DM($3F08)    ; the line/RX register
    06d0  DM($3F05) = $FFFF
    06d1  CALL $08F1        ; -> DIAL line handler (0x1BBD)
    06e2  CALL $08F0        ; -> DIAL state dispatcher (0x1B9C)
    06e7  I4 = DM($3FB2)    ; -> DIAL's own action vector
    06e8  CALL (I4)

Two things this harness gets right that the standalone one does not:

1.  **Download order.**  TIKRNL's init (PM 0x0637) clears PM 0x0900-0x1DFF —
    the whole overlay region.  Layering DIAL before the task init silently
    erases most of DIAL.  The overlay has to be downloaded *after* the task
    has initialised, which is also the order the host driver uses.
2.  **Entry point.**  DIAL is entered by TIKRNL through the PM 0x08F0/0x08F1
    stubs, not called directly, so the overlay interface (and anything the
    task does to the data-pump database before and after the call) is
    exercised.

Also recovered here: the bootpage table at DM 0x31D5.  TIKRNL indexes it with
bootpage_nr (DM 0x3FB0) to get the overlay download id to ask the host for,
and it matches the ADDSP V.90 guide's Table 1 page numbering exactly
(0 = DIAL 0x0262, 6 = V.8 0x025F, 7 = INFO 0x0260, 8 = V.34 0x0261,
14 = V.90D 0x026A).  `--bootpage-table` prints it.

**Serving the page switch.**  When TIKRNL wants an overlay it publishes the
type in DM 0x31A9 and the download id in DM 0x31AA, then yields by jumping to
the kernel service slot PM 0x000A with AR = 2.  AR is an index into the task
entry table the init registered at DM 0x31BA:

    DM 31ba = 0x06BB   AR = 1: ordinary per-frame entry
    DM 31bb = 0x06D8   AR = 2: "the overlay you asked for is loaded"

`Card.frame()` plays the host side of that handshake: it downloads the
requested image and re-enters the task at DM(0x31BB), repeating until the task
stops asking.  0x06D8 is what runs the half of the frame loop the request path
skips -- the SIG stub at PM 0x1900, then the strobe clear, the DIAL state
dispatcher at PM 0x08F0 and the action vector at DM 0x3FB2.  Without a host
serving the download the task never gets there.

Overlay images come from the card-type 56 (PRI 30M / .F34) download set:

    python3 tools/eicon_dsp_extract.py docs/firmware/dspdload.bin \
        --card-type 56 --match Overlay -o artifacts/eicon-dsp/overlays

The harness can also activate calling or answering operation by writing the
ADDSP §5.4.1 database directly.  It invokes both halves of the real sample
schedule: the page/RX entry and TIKRNL's registered PM 0x06FC continuation.
This produces genuine modem TX without MIPS, IDI, call objects or timeslots:

Usage:
    python3 tools/dial_tikrnl_drive.py --freq 2100 --frames 200
    python3 tools/dial_tikrnl_drive.py --role answer --freq 0 --frames 12000 \\
        --tx-out /tmp/eicon-answer.s16 --g711-out /tmp/eicon-answer.alaw
"""
from __future__ import annotations

import argparse
import collections
import ctypes
import json
import math
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eicon_idi

REPO = Path(__file__).resolve().parent.parent
ADSP = ctypes.CDLL(str(REPO / 'tools/adsp2181emu/libadsp2181.dylib'))
ADSP.adsp2181_create.restype = ctypes.c_void_p
for _name, _args in [('reset', [ctypes.c_void_p]), ('pm', [ctypes.c_void_p]),
                     ('dm', [ctypes.c_void_p]),
                     ('run', [ctypes.c_void_p, ctypes.c_int]),
                     ('pc', [ctypes.c_void_p]), ('idle', [ctypes.c_void_p]),
                     ('set_pc', [ctypes.c_void_p, ctypes.c_uint16]),
                     ('call', [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16]),
                     ('set_ar', [ctypes.c_void_p, ctypes.c_uint16]),
                     ('set_sr1', [ctypes.c_void_p, ctypes.c_uint16]),
                     ('watch_dm', [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_int]),
                     ('watch_pm', [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_int]),
                     ('watch_exec', [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_int]),
                     ('watch_exec_limited',
                      [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint32]),
                     ('watch_dm_limited',
                      [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint32]),
                     ('watch_dm_writes',
                      [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint32]),
                     ('coverage_clear', [ctypes.c_void_p]),
                     ('dm_census', [ctypes.c_void_p, ctypes.c_int]),
                     ('dm_census_clear', [ctypes.c_void_p]),
                     ('dm_census_count', [ctypes.c_void_p, ctypes.c_uint16])]:
    getattr(ADSP, 'adsp2181_' + _name).argtypes = _args
ADSP.adsp2181_dm_census_count.restype = ctypes.c_uint64
ADSP.adsp2181_coverage_count.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
ADSP.adsp2181_coverage_count.restype = ctypes.c_uint64
ADSP.adsp2181_pm.restype = ctypes.POINTER(ctypes.c_uint32)
ADSP.adsp2181_dm.restype = ctypes.POINTER(ctypes.c_uint16)
ADSP.adsp2181_pc.restype = ctypes.c_uint16
ADSP.adsp2181_idle.restype = ctypes.c_int
ADSP.adsp2181_pmovlay.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_pmovlay.restype = ctypes.c_uint16
ADSP.adsp2181_dmovlay.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_dmovlay.restype = ctypes.c_uint16
ADSP.adsp2181_read_pm.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
ADSP.adsp2181_read_pm.restype = ctypes.c_uint32
ADSP.adsp2181_sr0.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_sr0.restype = ctypes.c_uint16
ADSP.adsp2181_sr1.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_sr1.restype = ctypes.c_uint16
ADSP.adsp2181_g711_encode_block.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16),
    ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
    ctypes.c_uint16, ctypes.c_uint16, ctypes.c_int]
ADSP.adsp2181_g711_encode_block.restype = ctypes.c_int

KERNEL = 'artifacts/eicon-dsp/build-117-926/kernel/0009-diva-server-pri-30m-kernel'
TIKRNL = 'artifacts/eicon-dsp/build-117-926/tikrnl/0258-tikrnl81.f34-task'
OVERLAYS = 'artifacts/eicon-dsp/overlays'   # card-type 56 (PRI 30M / .F34) set
FIRMWARE_SET = 'pri117'
MODEM_V8_SETUP = 0x6000                     # V90_DPCM + digital network
DIAL_ID = 0x0262                            # the bootpage the card starts on
V_OWN_ID = 0x026D                           # base routines under partial pages
RELAY_BASE = tuple(int(f, 0) for f in os.environ.get("EICON_RELAY_BASE", "").split(",") if f.strip())
# EICON_OVERLAY_INIT=<id>[,<id>]: call the overlay's declared entry point after
# downloading it.  Most overlays declare no symbols at all -- V.8 and INFO have
# none -- but V.90 DPCM declares exactly one, symbol 0 at PM 0x3602, and that
# routine writes packed data into PM through I7.  This harness has never called
# it: download_overlay writes memory and resumes TIKRNL at DM(0x31BB),
# deliberately skipping the WSTATUS.BOOTFINISHED acknowledgement that would
# normally complete a download.
# EICON_RELAY_UNDER=<base>:<overlay>[,...]: lay <base> immediately before
# <overlay> and only before it.  RELAY_BASE above re-lays its bases before
# *every* download, which puts them under V.8 as well and drops the call to
# V.22 at 5.16 s, so the experiment this file has wanted since the page-14
# work -- one base under one page -- has had nowhere to stand.  The case it
# exists for: V90.ANA's own entry stubs (`PM 0x3330`, `PM 0x3348`) name
# `PM 0x19D7` as the record base, V90.ANA loads no PM block anywhere near it,
# and the only modem images that own that address are V34.ANA and the SIG
# pair -- so `0x0261:0x026b` asks whether V.90 APCM is meant to be layered
# over V.34.
RELAY_UNDER = tuple(
    (int(parts[0], 0), int(parts[1], 0))
    for parts in (field.split(":") for field in
                  os.environ.get("EICON_RELAY_UNDER", "").split(",")
                  if field.strip()))
OVERLAY_INIT = tuple(int(f, 0)
                     for f in os.environ.get("EICON_OVERLAY_INIT", "").split(",")
                     if f.strip())
# EICON_PATCH_PM=<addr>:<word>:<overlay>[,...]: overwrite a PM word immediately
# after the named overlay is downloaded.  The tower backend has carried the same
# variable for a while (eicon_mips_shim.PATCH_PM); this is the direct backend's
# half, added so the two can be A/B'd on the words where they disagree.  The
# case it exists for is PM 0x19c8, the last word of V90D's 201-word
# attributes=7 block, where the tower stages `0a000f` (RTS) and this backend
# stages `19900f` (JUMP $1990) -- the only control-flow difference in the whole
# 0x18cb-0x1bff range.  Applied at download time rather than per sample, because
# the difference being reproduced is a staging difference; a page that rewrites
# the word at run time would need the per-sample form instead.
PATCH_PM = tuple(
    (int(parts[0], 0), int(parts[1], 0) & 0xFFFFFF, int(parts[2], 0))
    for parts in (field.split(":") for field in
                  os.environ.get("EICON_PATCH_PM", "").split(",") if field.strip()))
# EICON_WATCH_PM_WRITES=<lo>:<hi>[,<lo>:<hi>]: arm the core's PM write watch
# over an inclusive address range for the whole call.  The core has had
# `watch_pm` since it was imported and this backend never armed it, so "nothing
# writes that PM word" has never been measurable here.  The case it exists for
# is the 41-word gap `PM 0x16dd-0x1705`: V90.ANA does not load it, the V90A
# sequencer unpacks the records for states 0x0053 and 0x0054 out of it, and the
# live content matches no image in the file set -- so the question is which page
# fills it.  The watch reports ppc/pc and pmovlay per store (core
# `WWORD_PGM`), which is what names the writer.
def _parse_ranges(text: str):
    out = []
    for field in text.split(","):
        if not field.strip():
            continue
        lo, _, hi = field.partition(":")
        low = int(lo, 0) & 0x3FFF
        high = (int(hi, 0) & 0x3FFF) if hi else low
        out.append((low, high))
    return tuple(out)


WATCH_PM_WRITES = _parse_ranges(os.environ.get("EICON_WATCH_PM_WRITES", ""))
V90D_ID = 0x026A                            # V.90 DPCM: publishes its line
V90A_ID = 0x026B                            # V.90 APCM: analogue-side page
# The native 2185 host supplies V.90D's polling TX mailbox.  Keep this
# diagnostic opt-in until the direct card has been compared with the native
# owner; ordinary direct calls historically let the firmware's mark-fill path
# own TXD0..TXD2.
V90D_TX_PRBS = os.environ.get("EICON_V90D_TX_PRBS", "0") != "0"
# Diagnostic only: replay the actual V90D TX mailbox datagrams captured from a
# native 2185 run.  This tests mailbox contents separately from ownership and
# PRBS packing; normal direct calls remain firmware-owned.
V90D_TX_DM_REPLAY = os.environ.get("EICON_V90D_TX_DM_REPLAY", "")
# Diagnostic only: V.90A's analogue transmit path also consumes the host
# TXD0 mailbox (PM 0x3d84 copies DM(0x3f05) into its symbol ring), but the
# normal TIKRNL mark-fill leaves that word at 0xffff.  Keep this opt-in until
# the correct host training source is recovered; it lets the mailbox
# ownership hypothesis be tested without pinning the modem state or wire.
V90A_TX_PRBS = os.environ.get("EICON_V90A_TX_PRBS", "0") != "0"
V90A_TX_PATTERN = tuple(
    int(field, 0) & 0xFFFF
    for field in os.environ.get('EICON_V90A_TX_PATTERN', '').split(',')
    if field.strip())
# Diagnostic only: a raw Ja source for the analogue V.90 page.  Ja starts with
# 24 ones and then repeats the N=0 DIL descriptor (276 bits); the DSP's
# modulator applies the line coding, so the host mailbox receives the source
# bits rather than a PRBS or already-modulated waveform.  Keep this separate
# from V90A_TX_PATTERN because requests are 16 bits while Ja is 12-bit aligned.
V90A_TX_JA = os.environ.get('EICON_V90A_TX_JA', '0') != '0'
V90A_TX_JA_BITS = tuple([1] * 24 + [0] * 276) if V90A_TX_JA else ()
# Diagnostic only: Ja after the V.34 GPA self-synchronizing scrambler and
# modulo-2 differential encoder.  The raw Ja probe is useful for ownership,
# but it is not a wire-level Ja sequence.  Keep this opt-in until a live pair
# proves the exact host/DSP boundary and the preceding-TRN1u differential
# seed.
V90A_TX_JA_SCRAMBLED = os.environ.get('EICON_V90A_TX_JA_SCRAMBLED', '0') != '0'


def _v90a_scrambled_differential_ja_bits():
    """Return the first N=0 Ja source block in transmitted bit order.

    V.92 8.5.4 specifies Ja as 24 ones plus a 276-bit DIL descriptor,
    scrambled with GPA from V.34 clause 7 and differentially encoded.  The
    descriptor is deliberately the existing zero placeholder here; this
    probe isolates source coding from descriptor capability/CRC details.
    GPA is represented in the usual self-synchronizing form y[n] = x[n]
    xor y[n-18] xor y[n-23].
    """
    raw = [1] * 24 + [0] * 276
    scrambled = []
    for index, bit in enumerate(raw):
        value = bit
        if index >= 18:
            value ^= scrambled[index - 18]
        if index >= 23:
            value ^= scrambled[index - 23]
        scrambled.append(value)
    previous = 0
    encoded = []
    for bit in scrambled:
        transmitted = bit ^ previous
        encoded.append(transmitted)
        previous = transmitted
    return tuple(encoded)


V90A_TX_JA_SCRAMBLED_BITS = (
    _v90a_scrambled_differential_ja_bits() if V90A_TX_JA_SCRAMBLED else ())
V90D_TX_LFSR_SEED = 0x1
# Diagnostic only: rewrite the V.90D rate-quality accumulator at frame
# boundaries. The native 2185 c2 path rises above this value while the live
# loopback remains near zero; this is a bootstrap test, never a production
# correction because it fabricates the receiver's measurement.
V90D_RATE_PIN = os.getenv('EICON_V90D_RATE_PIN', '').strip()
try:
    _rate_pin_parts = V90D_RATE_PIN.split(':', 1)
    V90D_RATE_PIN_STATE = int(_rate_pin_parts[0], 0) if len(_rate_pin_parts) == 2 else 0x00C2
    V90D_RATE_PIN_VALUE = int(_rate_pin_parts[-1], 0) & 0xffff
except ValueError:
    V90D_RATE_PIN_STATE = 0x00C2
    V90D_RATE_PIN_VALUE = 0
                                            # sample in DM(0x3FB4), not a
                                            # pointer to it -- see frame_fast
FSK_OWN_ID = 0x025C                         # base routines under DIAL/FSK/FAX

# TIKRNL entry points (download 0x0258).
TASK_ENTRY = 0x0672      # download symbol 0: init + register with the kernel
FRAME_ENTRY = 0x06BB     # per-frame loop head: command dispatch -> overlay
FRAME_ENTRY_NO_HOST = 0x06C1  # same loop, past the host-command fetch/dispatch
SAMPLE_CONTINUATION = 0x06FC  # registered kernel callback: RX/TX second half
G711_ENCODE_ENTRY = 0x1810    # TIKRNL's resident signed-linear -> G.711 routine
KERNEL_IDLE = 0x02A8     # return address for a task call
PAGE_REQUEST_ENTRY = 0x0686   # bootpage -> download id, publish, yield

# Instruction budget for one half-frame.  This is a runaway stop, not a model
# of the sample clock -- 8 kHz on a 33 MHz part is about 4,125 cycles, and this
# has always been several times that.  It has to be generous, because a run
# that ends on the budget rather than on the task's return leaves the PC stack
# *mid-call*: adsp2181_call() only discards its synthetic return when the core
# idled, so the next frame pushes its entry on top of the unwound remains and
# the 16-deep hardware stack is gone in three frames.  That is not a graceful
# degradation, it is silent control-flow corruption, and `_run_and_serve`
# reports it below rather than continuing quietly.
#
# 20,000 was under the requirement and nothing said so.  Measured over 80,000
# frames of a V.90A loopback, the worst single frame is 22,717 cycles, on
# INFOH.F34 (0x026E) at TrnProgress 0x0041 -- V.8 by comparison peaks at 4,736.
# So the old default truncated exactly one frame of the INFO handshake, and
# every frame after it ran on a corrupted stack.  65,536 is ~2.9x the measured
# worst case and still stops a genuine runaway inside one media tick.
#
# EICON_FRAME_BUDGET raises or lowers it for one run. The question it exists to
# answer is the one the truncation report cannot: a task that is over the
# budget and a task that never returns at all look identical from here, and
# only the first is fixed by a larger number.
FRAME_BUDGET = int(os.environ.get("EICON_FRAME_BUDGET", "65536"), 0)

# Task entry table registered with the kernel at init (PM 0x069C, SR0=0x31BA).
# The task selects one by loading AR and jumping to the kernel service slot
# PM 0x000A: AR = 1 -> DM 0x31BA = 0x06BB, AR = 2 -> DM 0x31BB = 0x06D8.
RESUME_ENTRIES = 0x31BA
RESUME_DOWNLOAD = 0x31BB # AR = 2: resume after the host served a download

# Overlay interface (segments shared by TIKRNL and every bootpage overlay).
OVL_STATE_STUB = 0x08F0  # -> DIAL 0x1B9C, the state dispatcher
OVL_LINE_STUB = 0x08F1   # -> DIAL 0x1BBD, the line/RX handler
SIG_STUBS = (0x1900, 0x1901, 0x1902)  # the SIG overlay's three stubs

# PM 0x069E is the `JUMP $000A` the request path takes after publishing the
# download in DM 0x31A9/0x31AA -- the marker that the task yielded for an
# overlay rather than finishing the frame.
PM_DOWNLOAD_YIELD = 0x069E
# See line_codec_rx_word(): expand the PRI timeslot octet the way the 2185N
# SPORT does, instead of handing the raw code to the DSP.
EXPAND_SPORT = os.environ.get("EICON_EXPAND_SPORT", "0") != "0"

def line_codec_rx_word(firmware_set: str, code: int, linear: int) -> int:
    """Translate the external bearer into the card's physical line format.

    PRI firmware is attached to an 8-bit companded T1/E1 timeslot. Analog
    firmware is attached to the single-channel SPORT1 codec configured for
    16-bit linear PCM. RTP remains G.711 in the harness, but that is transport,
    not the Analog DSP/DAA boundary.

    **`EICON_EXPAND_SPORT=1` expands the PRI octet the way the part does.**
    The 2185N's SPORT delivers a *right-justified, sign-extended* expanded
    value, not the octet -- `sport_rx_word()` below is that expansion, and the
    tower (`eicon_mips_shim`) and `dial_kernel_dispatch` both use it, while
    this backend has always handed the raw code over. On page 14 the
    consequence is measurable: the V.90 receive chain's own buffer at
    `DM 0x0EC0` carries samples of +/-44 and the biquad ahead of the Phase 3
    tone detector never sees more than about 41, against a wire that peaks at
    +/-6,140. Off by default because every result recorded on this backend was
    taken in the old domain, and a companded octet read as an amplitude is a
    different signal, not just a quieter one.
    """
    if firmware_set == 'analog109':
        return linear
    if EXPAND_SPORT:
        return sport_rx_word(code) & 0xFFFF
    return code & 0xFF


def sport_rx_word(code: int, law: str = 'pcmu') -> int:
    """Expand a DS0 octet exactly as the ADSP-2185N SPORT does.

    The ADSP-218x Hardware Reference §5, "Companding and Data Format", says
    RXn receives the *right-justified, sign-extended* expanded value and calls
    out a 13-bit A-law / 14-bit µ-law maximum. Conventional PCM16 G.711 helper
    formulas return those values left-shifted by three/two bits respectively;
    feeding that larger representation was a gain error at the DSP boundary.
    """
    code &= 0xFF
    if law == 'pcma':
        value = code ^ 0x55
        sample = (value & 0x0F) << 4
        segment = (value & 0x70) >> 4
        if segment == 0:
            sample += 8
        elif segment == 1:
            sample += 0x108
        else:
            sample = (sample + 0x108) << (segment - 1)
        sample >>= 3                 # right-justified 13-bit SPORT result
        return (sample if value & 0x80 else -sample) & 0xFFFF
    value = (~code) & 0xFF
    sample = (((value & 0x0F) << 3) + 0x84) << ((value >> 4) & 7)
    sample = (sample - 0x84) >> 2   # right-justified 14-bit SPORT result
    return (-sample if value & 0x80 else sample) & 0xFFFF


# Data-pump database (ADDSP V.90 guide §5.3).
DM_NORM_H = 0x3F08       # write DB +0x28: Norm_H, NOT a line register
DM_LINE_RX = DM_NORM_H   # legacy name, pre-V.8 line stand-in only
DM_LINE_TX = 0x3F09
DM_BOOTPAGE = 0x3FB0     # bootpage_nr / DIAL state selector
DM_TRNPROGRESS = 0x3FC2  # training progress, for the truncated-frame report
DM_VEC_A = 0x3FB2        # DIAL primary action vector
DM_VEC_B = 0x3FB3        # DIAL secondary action vector
DM_TX_POINTER = 0x3FB4   # pointer to current signed-linear TX sample
# EICON_V90D_TX_CENSUS=1: count DM writes per address on page 14 and report
# them per frame at exit.  Measurement only -- it answers "how many words does
# the page publish per serializer pass", which is what tells a decimated
# transmit (one word per pass, read once per tick) apart from a block the host
# is failing to drain.  Off by default; it costs a census hook in the core.
V90D_TX_CENSUS = os.getenv('EICON_V90D_TX_CENSUS', '') not in ('', '0')
V90D_TX_CENSUS_BLOCK = 0x3764    # the generic transmit word DM(0x3FB4) points at
V90D_TX_CENSUS_FRAME = 0x3FA7    # the six-word V.90 mapping-frame block
# The resident kernel's frame path zeroes that six-word block every 8 kHz
# sample at PM 0x06C6, while page 14's generator refills it once per 1333 Hz
# mapping frame and its serializer walks one slot per sample -- so five of
# every six slots are read after the clear and the line carries one sample in
# six.  Censused in data mode: DM(0x3FA8..0x3FAC) take 1.167 writes/frame
# (1.000 clear + 0.167 refill) and 16.7% of published samples are nonzero.
# Hold the clear while page 14 is resident, the way the native tower has since
# Session 62 (EICON_V90D_TX_BLOCK_HOLD, same name and default there).
V90D_HOLD_TX_BLOCK = os.getenv('EICON_V90D_TX_BLOCK_HOLD', '1') != '0'
V90D_TX_BLOCK_CLEAR = 0x06C6     # DM(I0,M1) = 0x0000, CNTR = 6 from PM 0x06C3
# Page 13 may share this block, but its refill cadence is not yet qualified.
# Keep the experiment opt-in until the caller's wire statistics prove it.
V90A_HOLD_TX_BLOCK = os.getenv('EICON_V90A_TX_BLOCK_HOLD', '0') != '0'
# Diagnostic only: page 14 publishes the line word from both halves of the
# harness frame.  The normal path reads after the continuation, matching the
# recovered direct-card convention; selecting ``frame`` tests whether the
# host is sampling the serializer one half too late.
V90D_TX_READ_PHASE = os.getenv('EICON_V90D_TX_READ_PHASE', 'continuation')
# The V.90D initializer copies the resident-law table over the staged V.90
# Table-1 values.  On PCMU hardware the selected-channel setup restores the
# staged values before Phase 3; keep the same correction in the direct card
# backend.  Set to 0 for the old A/B boundary.
V90D_PCMU_UCODE_TABLE = os.getenv('EICON_V90D_PCMU_UCODE_TABLE', '0') != '0'
# Match the native 2185 path: hold the shared bulk worker until the V.90D
# descriptor context is coherent, then service its near/far delay ABI from the
# host.  The direct card previously ran the worker with zero lengths.
V90D_BULK_ADAPTER = os.getenv('EICON_V90D_BULK_ADAPTER', '1') != '0'
V90D_PORTABLE_BULK = os.getenv('EICON_V90D_PORTABLE_BULK', '1') != '0'
V90D_BULK_SEED_BASE = 0x0025
V90D_BULK_SEED_SPAN = 0x0050
V90D_BULK_SEED_CEILING = 0x0B00
# Diagnostic override for comparing the direct-card delay ABI with captured
# 2185 writer values.  The normal path remains derived from DM(0x3f04).
def _optional_u16(name: str) -> int | None:
    value = os.getenv(name, '').strip()
    return (int(value, 0) & 0xFFFF) if value else None


V90D_BULK_NEAR_OVERRIDE = _optional_u16('EICON_V90D_BULK_NEAR')
V90D_BULK_FAR_OVERRIDE = _optional_u16('EICON_V90D_BULK_FAR')
V90D_BULK_SELECTOR_OVERRIDE = _optional_u16('EICON_V90D_BULK_SELECTOR')
V90D_BULK_DESCRIPTOR_LOWER_LIMIT = 0xFFFF
# Diagnostic final line-level trim. This is separate from the native-MIPS
# SPORT x4 experiment: it scales the signed-linear sample after the DSP has
# published it, allowing the measured V.90D 0xc2 level mismatch to be tested
# without changing the DSP's right-justified representation.
try:
    V90D_TX_GAIN = float(os.getenv('EICON_V90D_TX_GAIN', '1'))
except ValueError:
    V90D_TX_GAIN = 1.0
DM_STATUS = 0x3FC1
DM_SHELLINPTR = 0x3F0F   # write DB +0x2f: where the kernel stores the sample
DM_RXSAMPLE = 0x3F30     # write DB +0x50..0x55: RXSAMPLE_0..5
DM_RXSAMPLE_COUNT = 0x3F67  # how many of them the page expects per symbol
# Norm_H while a V.8 page is resident. This is per-role, and it has to be:
# V8.ANA PM 0x3834..0x383D reads bits 5 and 6 of this word to choose which V.8
# CM call-function octet to transmit -- bit 5 -> 0x0103, bit 6 -> 0x010B,
# neither -> 0x0107 -- so the same constant means "how to drive ANSam" on the
# answering side and "what kind of call this is" on the calling side.
#
# 0x0021 is hardware-traced from the native backend (38cd94e) and is correct
# for the *answering* role, where the 0x20 bit is load-bearing: without it the
# answerer does not transmit ANSam at all. Applying the same word to the
# calling role was the mistake. Measured: with bit 5 set the caller's CM sends
# the answerer to bootpage 10 INFOH and then bootpage 5 HV.34 -- which
# `am_firmware_contents.md` names half-duplex V.34 phase-2 negotiation and
# half-duplex V.34 modulation, i.e. **V.34 fax**. Clearing bit 5 sends it to
# bootpage 7 INFO instead, which is the V.34/V.90 data path and the only one
# that can reach page 8 or page 14.
# Derived from the driver's CAI rather than written out, so the two roles come
# from one rule instead of two constants: eicon_idi.norm_h_from_cai() returns
# 0x0021 answering -- the hardware-traced value -- and 0x0001 for a calling
# *data* call, which sets neither bit 5 nor bit 6 and so selects CM 0x0107, the
# only call function that reaches V.90.  0x0041 was the earlier reading of these
# as a role field and is the mistake the signature now exists to prevent; it is
# still reachable through EICON_NORM_H_CALLING for A/B.
NORM_H_V8_ANSWER = eicon_idi.norm_h_from_cai('answer')
NORM_H_V8_CALLING = int(os.environ.get(
    'EICON_NORM_H_CALLING', str(eicon_idi.norm_h_from_cai('calling'))), 0)
DM_DB = 0x3EE0          # ADDSP data-pump database base (§5.4.1)

# TIKRNL private state.
DM_BOOTPAGE_TABLE = 0x31D5   # bootpage number -> overlay download id
DM_DOWNLOAD_REQ = 0x31AA     # download id TIKRNL is asking the host for
DM_DOWNLOAD_FLAG = 0x31A9

# Direct-drive layouts. The task's shared data-pump database and bootpage table
# stay fixed, but the ANA task has 23 extra resident instructions and moves its
# private entry points/request words. Keep these differences explicit: using
# the PRI addresses appeared to run but read alternating 0/2/4 as download IDs.
FIRMWARE_SETS = {
    'pri117': {
        'kernel': KERNEL, 'tikrnl': TIKRNL, 'overlays': OVERLAYS,
        'task_entry': 0x0672, 'frame_entry': 0x06BB,
        'frame_no_host': 0x06C1, 'sample_continuation': 0x06FC,
        'kernel_idle': 0x02A8, 'download_yield': 0x069E,
        'page_request': 0x0686,
        'download_req': 0x31AA, 'download_flag': 0x31A9,
        'v8_setup': 0x6000,
    },
    'analog109': {
        'kernel': ('artifacts/eicon-dsp/build-109-789-analog/kernel/'
                   '000d-diva-server-analog-kernel'),
        'tikrnl': ('artifacts/eicon-dsp/build-109-789-analog/tikrnl/'
                   '0258-tikrnl81.ana-task'),
        'overlays': 'artifacts/eicon-dsp/build-109-789-analog/overlays',
        'task_entry': 0x0679, 'frame_entry': 0x06D2,
        'frame_no_host': 0x06D8, 'sample_continuation': 0x0713,
        'kernel_idle': 0x02A6, 'download_yield': 0x06B5,
        'page_request': 0x068D,
        # The ANA request path writes AX0/AR/M0 to DM 0x31AB/0x31AC/0x31AD,
        # exactly as the PRI one writes 0x31A9/0x31AA/0x31AB, so the flag is
        # 0x31AB -- it reads 0x0015 with 0x0274 pending straight out of task
        # init. 0x31AD is M0 and is always zero. Only the request-type
        # diagnostic below reads this word on the direct path, which is why
        # the old value never showed up as a behaviour difference.
        'download_req': 0x31AC, 'download_flag': 0x31AB,
        'v8_setup': 0x8000,  # V90_APCM + analogue network
    },
}


def select_firmware_set(name: str) -> None:
    """Select one coherent kernel/task/overlay ABI for this process."""
    global FIRMWARE_SET, KERNEL, TIKRNL, OVERLAYS, MODEM_V8_SETUP
    global TASK_ENTRY, FRAME_ENTRY, FRAME_ENTRY_NO_HOST, SAMPLE_CONTINUATION
    global KERNEL_IDLE, PM_DOWNLOAD_YIELD, DM_DOWNLOAD_REQ, DM_DOWNLOAD_FLAG
    global PAGE_REQUEST_ENTRY
    cfg = FIRMWARE_SETS[name]
    FIRMWARE_SET = name
    KERNEL, TIKRNL, OVERLAYS = cfg['kernel'], cfg['tikrnl'], cfg['overlays']
    TASK_ENTRY, FRAME_ENTRY = cfg['task_entry'], cfg['frame_entry']
    FRAME_ENTRY_NO_HOST = cfg['frame_no_host']
    SAMPLE_CONTINUATION = cfg['sample_continuation']
    KERNEL_IDLE, PM_DOWNLOAD_YIELD = cfg['kernel_idle'], cfg['download_yield']
    PAGE_REQUEST_ENTRY = cfg['page_request']
    DM_DOWNLOAD_REQ, DM_DOWNLOAD_FLAG = cfg['download_req'], cfg['download_flag']
    MODEM_V8_SETUP = cfg['v8_setup']

# The kernel's five ring-descriptor pointers.  Its foreground writes them at
# PM 0x02AD-0x02B2 the first time it wakes, but this harness calls the task
# directly and never lets the foreground run, so they have to be planted --
# see tools/dial_kernel_dispatch.py, which lets the kernel write them itself.
# They matter here because the task's registration (kernel service 0x0017)
# reads the block at DM 0x2F21 to find the two words it patches; with the
# pointers zero it indexes DM 0x0002 instead and patches PM 0x0000/0x000A.
RING_POINTERS = {0x2F27: 0x2F21,   # task registration block
                 0x2F28: 0x2F00,   # host -> DSP command ring descriptor
                 0x2F29: 0x2F0E,   # DSP -> host descriptor + doorbell
                 0x2F2A: 0x2F42,
                 0x2F2B: 0x2F4E}
PM_FOREGROUND_SLOT = 0x02B9   # kernel foreground: CALL $02A1 -> the task
PM_ISR_SLOT = 0x00B5          # SPORT0 ISR: an inline op the task may claim

# PM 0x00D8 walks the command ring as `I0 = DM(0x2F28)`, and PM 0x06C0 calls
# whatever handler address the walk produced.  DM 0x2F03 (the byte count) stays
# zero until a host command is queued, and an unwritten DM 0x2F28 walks DM
# 0x0000 instead -- harmless while that is unpopulated, which is why the
# pre-serving harness never noticed, but the V.8, V.22FC and DIAL-partial
# overlays all load DM from 0x0000, so the first page switch turns overlay
# coefficients into dispatch addresses.  See FRAME_ENTRY_NO_HOST.
DM_QUEUE_HEAD = 0x2F28


def read_words(path: Path) -> dict[int, int]:
    out = {}
    for line in path.read_text().splitlines():
        fields = line.split()
        if len(fields) == 2:
            out[int(fields[0], 16)] = int(fields[1], 16)
    return out


def reverse_octet(value: int) -> int:
    value &= 0xFF
    value = ((value & 0x55) << 1) | ((value >> 1) & 0x55)
    value = ((value & 0x33) << 2) | ((value >> 2) & 0x33)
    return ((value << 4) | (value >> 4)) & 0xFF


def linear_to_mulaw(sample: int) -> int:
    """ITU-T G.711 mu-law, conventional octet order.

    The segment search this replaced shifted by 5 where the 16-bit input needs
    8 -- `exponent = (magnitude).bit_length() - 8` -- so every magnitude at or
    above 3964 (-18.3 dBfs) ran the loop past segment 7 and took the
    saturation arm. A 20000-amplitude sine, which is what `make_g711_stimulus`
    and every standalone drive here asks for, came out clipped on 87.5% of its
    samples using 7 of the 33 codes it should span: a square wave whose third
    harmonic aliases to about 1700 Hz at 8 kHz. Verified exhaustively against
    the reference over all 65536 inputs.
    """
    sign = 0x80 if sample < 0 else 0x00
    magnitude = min(abs(sample), 32635) + 132     # bias 0x84 on 14 bits << 2
    exponent = magnitude.bit_length() - 8         # 0..7
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa)) & 0xFF

class Card:
    """Kernel + TIKRNL task + DIAL overlay on one emulated ADSP-2181."""

    def __init__(self, log: bool = False, serve: bool = True,
                 max_downloads: int = 8, host_dispatch: bool = False,
                 force_info_after_v8: bool = False):
        self.log = log
        self.serve = serve
        self.firmware_set = FIRMWARE_SET
        self.max_downloads = max_downloads
        self.force_info_after_v8 = force_info_after_v8
        self.truncated_frames = 0
        self.pending_overlay_init = None
        self._census: dict | None = None
        self._v90d_saved_clear: int | None = None
        self._v90a_saved_clear: int | None = None
        self._v90a_tx_claimed = False
        self._v90a_tx_pending: int | None = None
        self._v90a_tx_pattern_index = 0
        self._v90a_tx_ja_bitpos = 0
        self._v90d_staged_ucode: tuple[int, ...] | None = None
        self._v90d_ucode_restored = False
        self._v90d_saved_bulk_opcode: int | None = None
        self._v90d_bulk_lengths: tuple[int, int] | None = None
        self._v90d_bulk_pairs: collections.deque[tuple[int, int]] = collections.deque()
        self._v90d_tx_lfsr = V90D_TX_LFSR_SEED
        self._v90d_tx_pending: tuple[int, int, int] | None = None
        self._v90d_tx_replay = self._load_v90d_tx_replay(V90D_TX_DM_REPLAY)
        self._v90d_tx_replay_index = 0
        self._v90d_tx_claimed = False
        self._v90d_rate_pinned = False
        self.v90d_tx_requests = 0
        # PM 0x06BB-0x06C0 fetches and dispatches a host command.  With no
        # channel assigned there is nothing to fetch, and the walk aliases an
        # overlay's DM 0x0000, so the default entry starts just past it.
        self.entry = FRAME_ENTRY if host_dispatch else FRAME_ENTRY_NO_HOST
        self.cpu = ADSP.adsp2181_create()
        ADSP.adsp2181_reset(self.cpu)
        for low, high in WATCH_PM_WRITES:
            for address in range(low, high + 1):
                ADSP.adsp2181_watch_pm(self.cpu, address, 1)
        if WATCH_PM_WRITES:
            print('[watch-pm] armed ' + ",".join(f'0x{a:04x}-0x{b:04x}'
                                                 for a, b in WATCH_PM_WRITES),
                  file=sys.stderr)
        self.pm = ADSP.adsp2181_pm(self.cpu)
        self.dm = ADSP.adsp2181_dm(self.cpu)
        self.pm_loaded: set[int] = set()
        self.overlays = self._index_overlays()
        self.resident = 0                                  # download id on the card
        # Set when V.8 is served, at which point DM(0x3F08) stops being a
        # stand-in for the line and goes back to being what the ADDSP database
        # says it is; see _present_line().
        self.private_line_active = False
        self.norm_h = 0x0001
        self.modem_role = 'idle'
        self.modem_law = 'pcmu'
        # Frame at which the calling side asks for the V.8 page, or None. The
        # request has to come after DIAL has run: V.8's entry stub saves the
        # action vector it displaces (DM(0x38D0)) and chains to it at
        # PM 0x2016, and that vector -- DIAL's PM 0x17C4 -- is what publishes
        # shellinptr and the transmit slot. Load V.8 over a DIAL that has never
        # run a frame and there is no vector to save, so the page never gets
        # its sample pointers. See Endpoint.build_card().
        self.originate_v8_at: int | None = None
        self._originate_v8_done = False
        self._shellinptr_warned = False
        self.line_sample = 0
        # Diagnostic stand-in for the kernel's per-symbol receive array; see
        # _present_rxsample(). Off unless EICON_RXSAMPLE is set.
        self.rxsample_history: collections.deque = collections.deque(maxlen=6)
        self.rxsample_stand_in = os.getenv('EICON_RXSAMPLE', '0') != '0'
        self.served: collections.Counter = collections.Counter()
        self.unserved: collections.Counter = collections.Counter()
        self.switches: list[tuple[int, int, int]] = []     # frame, bootpage, id
        self.forced_info_samples: list[int] = []

    @staticmethod
    def _index_overlays() -> dict[int, tuple[Path, str]]:
        """Map download id -> extracted image, from the card's overlay set."""
        root = REPO / OVERLAYS
        if not root.is_dir():
            if FIRMWARE_SET == 'analog109':
                command = (
                    '  python3 tools/eicon_dsp_extract.py '
                    'docs/firmware/firmware/dspdload.bin \\\n'
                    '      --card-type 77 --match Overlay -o ' + OVERLAYS)
            else:
                command = (
                    '  python3 tools/eicon_dsp_extract.py docs/firmware/dspdload.bin \\\n'
                    '      --card-type 56 --match Overlay -o ' + OVERLAYS)
            raise SystemExit(f'{OVERLAYS} is missing. Extract its overlay set:\n'
                             + command)
        index: dict[int, tuple[Path, str]] = {}
        for entry in sorted(root.iterdir()):
            meta = entry / 'metadata.json'
            if meta.is_file():
                data = json.loads(meta.read_text())
                index.setdefault(data['download_id'],
                                 (entry, data['description']))
        return index

    def _download(self, directory: Path | str) -> None:
        base = REPO / directory
        for addr, value in read_words(base / 'pm.words').items():
            self.pm[addr] = value
            self.pm_loaded.add(addr)
        for addr, value in read_words(base / 'dm.words').items():
            self.dm[addr] = value

    def _restore_v90d_pcmu_ucode_table(self) -> None:
        """Restore the firmware-staged PCMU V.90 Table-1 magnitudes.

        The page initializer reuses DM(0x1f14..0x1f93) for the resident law.
        The selected PCMU channel on the native path restores the V.90D
        overlay's staged table, including the value-8 zero sentinel that PM
        0x2ef1 turns into distinct +/-2 wire values.  Without this, the
        direct backend runs the V.90D receiver against the A-law magnitudes.
        """
        if (not V90D_PCMU_UCODE_TABLE
                or self.modem_law != 'pcmu'
                or self._v90d_staged_ucode is None):
            return
        changed = (self.dm[0x1F14] != 8
                   or any(self.dm[address] != value
                          for address, value in zip(
                              range(0x1F15, 0x1F94),
                              self._v90d_staged_ucode[1:])))
        if not changed:
            return
        for address, value in zip(range(0x1F14, 0x1F94),
                                  self._v90d_staged_ucode):
            self.dm[address] = value
        self.dm[0x1F14] = 8
        if not self._v90d_ucode_restored:
            print('[v90d] restored selected PCMU Ucode table '
                  'at DM(0x1f14..0x1f93)')
            self._v90d_ucode_restored = True

    def download_overlay(self, download_id: int) -> str | None:
        """Serve one overlay download the way the host driver does."""
        entry = self.overlays.get(download_id)
        if entry is None:
            self.unserved[download_id] += 1
            return None
        path, description = entry
        # EICON_RELAY_BASE=<id>[,<id>] re-lays a base image immediately before
        # the requested overlay. The images are layered partials: each download
        # writes only its own blocks, so PM keeps whatever the previous page
        # left in the gaps. Measured on page 14: with V.8 and INFO having run
        # first, the live words at PM 0x3785 and 0x3792 are V8.F34's, 0x378a is
        # INFO's and 0x3749 is V90D's -- a patchwork, and the per-frame chain
        # walks V.8's state machine, whose variables cover the word V90D's
        # 0x0060 test polls. V.OWN is the "base routines under partial pages"
        # image and this harness lays it only once, at boot, before either of
        # those pages existed.
        for base_id in RELAY_BASE:
            base = self.overlays.get(base_id)
            if base is not None and base_id != download_id:
                self._download(base[0])
        for base_id, over_id in RELAY_UNDER:
            if over_id != download_id or base_id == download_id:
                continue
            base = self.overlays.get(base_id)
            if base is None:
                print(f'[relay-under] 0x{base_id:04x} is not in this '
                      f'overlay set; nothing laid')
                continue
            self._download(base[0])
            print(f'[relay-under] laid 0x{base_id:04x} under '
                  f'0x{download_id:04x}')
        self._download(path)
        if download_id == V90D_ID:
            staged = tuple(self.dm[address] for address in range(0x1F14, 0x1F94))
            if staged[0] == 0 and staged[1] == 8:
                self._v90d_staged_ucode = staged
                self._v90d_ucode_restored = False
        for address, value, overlay in PATCH_PM:
            if overlay != download_id:
                continue
            old = int(self.pm[address]) & 0xFFFFFF
            self.pm[address] = value
            print(f'[patch-pm] PATCHED FIRMWARE: PM 0x{address:04x} '
                  f'0x{old:06x} -> 0x{value:06x} on download '
                  f'0x{overlay:04x}')
        # Do not set WSTATUS.BOOTFINISHED here.  This direct harness resumes
        # TIKRNL explicitly through DM(0x31BB); adding the ordinary host/kernel
        # acknowledgement as well completes the download twice.  In particular
        # FAX/partial pages then leave the page-change strobe asserted and are
        # destructively reloaded several times per sample.
        if download_id in OVERLAY_INIT:
            # Queue it rather than calling it here: entering the overlay's own
            # entry point before the task has been resumed at DM(0x31BB) runs
            # it outside the frame context the card would give it, and the
            # measured result was a partial PM fill (201 -> 623 words in
            # 0x1800-0x1bff, with 0x18cc still empty).
            self.pending_overlay_init = (download_id, path)
        if V90D_HOLD_TX_BLOCK or V90A_HOLD_TX_BLOCK:
            self._hold_tx_block(download_id)
        if V90D_BULK_ADAPTER:
            self._hold_v90d_bulk(download_id)
        if download_id == V90A_ID and (V90A_TX_PRBS or V90A_TX_PATTERN
                                       or V90A_TX_JA):
            self._claim_v90a_tx_mailbox()
        if download_id == V90D_ID and (V90D_TX_PRBS
                                       or self._v90d_tx_replay):
            self._claim_v90d_tx_mailbox()
        self.resident = download_id
        self.served[download_id] += 1
        if download_id == 0x025F:
            # V.8 publishes shellinptr on entry and owns Norm_H from here on;
            # see _present_line().
            self.private_line_active = True
            self.dm[DM_NORM_H] = self.norm_h | (
                NORM_H_V8_CALLING if self.modem_role == 'calling'
                else NORM_H_V8_ANSWER)
        return description

    def _claim_v90a_tx_mailbox(self) -> None:
        """Let the opt-in V.90A TXD0 probe survive TIKRNL mark fill."""
        if self._v90a_tx_claimed:
            return
        matches = [address for address in range(0x4000)
                   if (int(self.pm[address]) & 0xFFFFFF) == 0x93F05A]
        if len(matches) != 1:
            raise RuntimeError(
                'cannot claim V.90A TXD0 mailbox: mark-fill opcode matched '
                f'{len(matches)} times, expected once')
        self.pm[matches[0]] = 0
        self._v90a_tx_claimed = True
        print(f'[v90a] host-owned TXD0 probe suppressed TIKRNL mark fill '
              f'at PM 0x{matches[0]:04x}')

    def _claim_v90d_tx_mailbox(self) -> None:
        """Let the opt-in V90D host source survive TIKRNL's TX stores."""
        if self._v90d_tx_claimed:
            return
        signatures = {
            0x93F05A: 1,   # mark-fill TXD0
            0x93F05F: 2,   # internal short/long TXD0 paths
            0x93F06F: 1,   # internal TXD1
            0x93F07F: 1,   # internal TXD2
        }
        matches = {
            opcode: [address for address in range(0x4000)
                     if (int(self.pm[address]) & 0xFFFFFF) == opcode]
            for opcode in signatures
        }
        if any(len(matches[opcode]) != count for opcode, count
               in signatures.items()):
            detail = ', '.join(f'0x{opcode:06x}={len(matches[opcode])}'
                               for opcode in signatures)
            raise RuntimeError(
                'cannot claim V90D TX mailbox: unexpected store signature '
                f'counts ({detail})')
        for addresses in matches.values():
            for address in addresses:
                self.pm[address] = 0
        self._v90d_tx_claimed = True
        print('[v90d] host-owned TX mailbox: suppressed five TIKRNL stores')

    def _hold_tx_block(self, download_id: int) -> None:
        """Stop the per-frame clear of the V.90 mapping-frame block on page 14.

        PM 0x06C6 is the six-count store the resident kernel's frame path runs
        every sample, zeroing DM(0x3FA7..0x3FAC).  Page 14's generator refills
        that block once per mapping frame (0.167/frame) and its serializer
        reads one slot per sample from cursor DM(0x20DE), so the block has to
        survive six samples; with the clear live, five of six slots read zero
        and the published transmit is 83% zeros.  The store lives in the
        resident kernel rather than the overlay, so a later page load does not
        put it back -- restore it by hand on the way out, testing the page we
        are leaving rather than the one being loaded.
        """
        v90a_saved_clear = getattr(self, '_v90a_saved_clear', None)
        if (download_id == V90D_ID and v90a_saved_clear is not None
                and self.resident == V90A_ID):
            self.pm[V90D_TX_BLOCK_CLEAR] = v90a_saved_clear
            self._v90a_saved_clear = None
            print('[v90a] restored the per-frame clear before entering '
                  'page 14')
        if download_id == V90D_ID:
            if self._v90d_saved_clear is None:
                self._v90d_saved_clear = int(self.pm[V90D_TX_BLOCK_CLEAR])
                self.pm[V90D_TX_BLOCK_CLEAR] = 0x000000
                print('[v90d] held the resident kernel\'s per-frame clear of '
                      'the mapping-frame block DM(0x3fa7..0x3fac) '
                      '(EICON_V90D_TX_BLOCK_HOLD=0 restores it)')
        elif self._v90d_saved_clear is not None and self.resident == V90D_ID:
            self.pm[V90D_TX_BLOCK_CLEAR] = self._v90d_saved_clear
            self._v90d_saved_clear = None
            print(f'[v90d] restored the per-frame clear leaving page 14 for '
                  f'0x{download_id:04x}')
        if download_id == V90A_ID and V90A_HOLD_TX_BLOCK:
            if self._v90a_saved_clear is None:
                self._v90a_saved_clear = int(self.pm[V90D_TX_BLOCK_CLEAR])
                self.pm[V90D_TX_BLOCK_CLEAR] = 0x000000
                print('[v90a] held the resident kernel\'s per-frame clear of '
                      'the mapping-frame block DM(0x3fa7..0x3fac) '
                      '(EICON_V90A_TX_BLOCK_HOLD=0 restores it)')
        elif (getattr(self, '_v90a_saved_clear', None) is not None
              and self.resident == V90A_ID):
            self.pm[V90D_TX_BLOCK_CLEAR] = self._v90a_saved_clear
            self._v90a_saved_clear = None
            print(f'[v90a] restored the per-frame clear leaving page 13 for '
                  f'0x{download_id:04x}')

    def _hold_v90d_bulk(self, download_id: int) -> None:
        """Hold the unsafe native bulk worker while V.90D is resident.

        PM 0x19c8 is a shared tail jump whose loop width comes from
        DM(0x1e4f).  At direct-card V.90D entry that width is stale and the
        near/far lengths are still zero, so letting it run can overwrite the
        page-14 context before the first coherent rate publication.  The
        native 2185 path replaces this jump with an RTS and services the
        documented delay-line words from the host.
        """
        if download_id == V90D_ID:
            if self._v90d_saved_bulk_opcode is None:
                self._v90d_saved_bulk_opcode = int(self.pm[0x19C8])
                self.pm[0x19C8] = 0x0A000F
                print('[v90d] held shared bulk-delay worker PM 0x19c8')
            self._v90d_bulk_lengths = None
            self._v90d_bulk_pairs.clear()
        elif (self._v90d_saved_bulk_opcode is not None
              and self.resident == V90D_ID):
            self.pm[0x19C8] = self._v90d_saved_bulk_opcode
            self._v90d_saved_bulk_opcode = None
            self._v90d_bulk_lengths = None
            self._v90d_bulk_pairs.clear()
            print(f'[v90d] restored shared bulk-delay worker leaving page 14 '
                  f'for 0x{download_id:04x}')

    def _service_v90d_bulk(self) -> None:
        """Serve the native V.90D near/far delay-line database contract."""
        if (not V90D_BULK_ADAPTER or not V90D_PORTABLE_BULK
                or self.resident != V90D_ID):
            return
        if V90D_BULK_SELECTOR_OVERRIDE is not None:
            selector = V90D_BULK_SELECTOR_OVERRIDE & 0x3FFF
            self.dm[0x32F7] = selector
            self.dm[(selector + 5) & 0x3FFF] = (
                V90D_BULK_DESCRIPTOR_LOWER_LIMIT)
        near = V90D_BULK_NEAR_OVERRIDE
        far = V90D_BULK_FAR_OVERRIDE
        if near is None:
            near = V90D_BULK_SEED_BASE + (int(self.dm[0x3F04]) & 0xFFFF)
        near = max(1, min(near, V90D_BULK_SEED_CEILING))
        if far is None:
            far = near + V90D_BULK_SEED_SPAN
        far = max(near, min(far, V90D_BULK_SEED_CEILING))
        lengths = (near, far)
        if lengths != self._v90d_bulk_lengths:
            self._v90d_bulk_lengths = lengths
            self._v90d_bulk_pairs = collections.deque(
                ((0, 0) for _ in range(far)), maxlen=far)
            source = ('override' if V90D_BULK_NEAR_OVERRIDE is not None
                      else 'DM(0x3f04)')
            if V90D_BULK_SELECTOR_OVERRIDE is not None:
                source += f', selector=0x{V90D_BULK_SELECTOR_OVERRIDE:04x}'
            print(f'[v90d] portable bulk delay near={near} far={far} '
                  f'source={source}')
        # PM 0x19e2/0x19e4 restore these saved-context words every frame;
        # publish both copies just as the native host path does.
        self.dm[0x3FBC] = self.dm[0x3608] = near
        self.dm[0x3FBD] = self.dm[0x3609] = far
        if not (self.dm[DM_STATUS] & 0x0400):
            return
        near_pair = self._v90d_bulk_pairs[-near]
        far_pair = self._v90d_bulk_pairs[0]
        self._v90d_bulk_pairs.append((self.dm[0x3FBE], self.dm[0x3FBF]))
        self.dm[0x3F36], self.dm[0x3F37] = near_pair
        self.dm[0x3F38], self.dm[0x3F39] = far_pair

    @staticmethod
    def _load_v90d_tx_replay(path: str) -> list[tuple[int, int, int]]:
        if not path:
            return []
        data = Path(path).read_bytes()
        record = struct.Struct('<Q256H')
        if not data.startswith(b'EADSPDM2'):
            raise ValueError(f'{path}: not an EADSPDM2 DM capture')
        words = []
        for offset in range(8, len(data) - record.size + 1, record.size):
            row = record.unpack_from(data, offset)
            dm = row[1:]
            trn = dm[0x3FC2 - 0x3EE0]
            if not (0x00B0 <= trn <= 0x00D0):
                continue
            packet = tuple(dm[address - 0x3EE0]
                           for address in (0x3F05, 0x3F06, 0x3F07))
            if packet != (0xFFFF, 0xFFFF, 0xFFFF):
                words.append(packet)
        if not words:
            raise ValueError(f'{path}: no V90D page TX mailbox records')
        print(f'[v90d] loaded {len(words)} native TX mailbox datagrams from '
              f'{path}')
        return words

    def _next_v90d_tx_words(self) -> tuple[int, int, int]:
        """Make the native host's 48-bit V.90D training datagram.

        V.90D is the exception to the other ADDSP mailbox layouts: TXD0 bit
        zero is the oldest bit and the packet continues through TXD1/TXD2.
        This is deliberately a PRBS diagnostic, matching the native MIPS
        ``--tx-prbs`` source, rather than pretending to provide a V.42 stream.
        """
        bits: list[int] = []
        for _ in range(48):
            lsb = self._v90d_tx_lfsr & 1
            self._v90d_tx_lfsr = ((self._v90d_tx_lfsr >> 1) ^
                                  (0x80200003 if lsb else 0)) & 0xFFFFFFFF
            bits.append(lsb)
        return tuple(sum(bits[word * 16 + bit] << bit for bit in range(16))
                     for word in range(3))

    def _service_v90d_tx_request(self) -> None:
        """Supply a direct-card V.90D TX mailbox request, when enabled."""
        if (self.resident != V90D_ID
                or (not V90D_TX_PRBS and not self._v90d_tx_replay)):
            return
        requested = bool(self.dm[0x3FAD] & 0x8000)
        if not requested:
            self._v90d_tx_pending = None
            return
        if self._v90d_tx_pending is None:
            if self._v90d_tx_replay:
                self._v90d_tx_pending = self._v90d_tx_replay[
                    self._v90d_tx_replay_index % len(self._v90d_tx_replay)]
                self._v90d_tx_replay_index += 1
            else:
                self._v90d_tx_pending = self._next_v90d_tx_words()
            self.v90d_tx_requests += 1
            if self.v90d_tx_requests == 1:
                source = ('native replay' if self._v90d_tx_replay
                          else 'PRBS')
                print(f"[v90d] supplied first direct TX mailbox datagram "
                      f"({source}) "
                      f"{self._v90d_tx_pending[0]:04x}/"
                      f"{self._v90d_tx_pending[1]:04x}/"
                      f"{self._v90d_tx_pending[2]:04x}")
        self.dm[0x3F05], self.dm[0x3F06], self.dm[0x3F07] = self._v90d_tx_pending

    def _service_v90a_tx_request(self) -> None:
        """Diagnostic V.90A TXD0 source for the analogue page.

        Unlike page 14, the APCM page consumes only TXD0, with the oldest bit
        at bit 15.  TIKRNL's per-frame mark fill is therefore visible as an
        all-one symbol source.  A PRBS source is useful only as an ownership
        probe: V.90A training still requires the protocol's real source.
        """
        if ((not V90A_TX_PRBS and not V90A_TX_PATTERN and not V90A_TX_JA
             and not V90A_TX_JA_SCRAMBLED)
                or self.resident != V90A_ID):
            return
        requested = bool(self.dm[0x3FAD] & 0x8000)
        if not requested:
            self._v90a_tx_pending = None
            return
        if self._v90a_tx_pending is not None:
            self.dm[0x3F05] = self._v90a_tx_pending
            return
        if V90A_TX_JA or V90A_TX_JA_SCRAMBLED:
            source_bits = (V90A_TX_JA_SCRAMBLED_BITS
                           if V90A_TX_JA_SCRAMBLED else V90A_TX_JA_BITS)
            bits = [source_bits[(self._v90a_tx_ja_bitpos + index)
                                % len(source_bits)]
                    for index in range(16)]
            self._v90a_tx_ja_bitpos = ((self._v90a_tx_ja_bitpos + 16)
                                       % len(source_bits))
            self._v90a_tx_pending = sum(bit << (15 - index)
                                        for index, bit in enumerate(bits))
        elif V90A_TX_PATTERN:
            self._v90a_tx_pending = V90A_TX_PATTERN[
                self._v90a_tx_pattern_index % len(V90A_TX_PATTERN)]
            self._v90a_tx_pattern_index += 1
        else:
            bits = []
            for _ in range(16):
                lsb = self._v90d_tx_lfsr & 1
                self._v90d_tx_lfsr = ((self._v90d_tx_lfsr >> 1) ^
                                      (0x80200003 if lsb else 0)) & 0xFFFFFFFF
                bits.append(lsb)
            self._v90a_tx_pending = sum(bit << (15 - index)
                                        for index, bit in enumerate(bits))
        self.dm[0x3F05] = self._v90a_tx_pending

    def _call_overlay_entry(self, download_id: int, directory) -> None:
        """Run the overlay's symbol-0 entry, the way the task entry is run."""
        meta = REPO / directory / 'metadata.json'
        if not meta.exists():
            return
        symbols = json.loads(meta.read_text()).get('symbols') or []
        if not symbols:
            print(f'[overlay-init] 0x{download_id:04x} declares no symbols')
            return
        entry = symbols[0]['offset']
        ADSP.adsp2181_call(self.cpu, entry, KERNEL_IDLE)
        for _ in range(1_000_000):
            ADSP.adsp2181_run(self.cpu, 1)
            if ADSP.adsp2181_idle(self.cpu):
                break
        else:
            print(f'[overlay-init] 0x{download_id:04x} entry PM 0x{entry:04x} '
                  f'did not return')
            return
        print(f'[overlay-init] called 0x{download_id:04x} symbol 0 at '
              f'PM 0x{entry:04x}')

    def boot(self) -> None:
        """Kernel download + reset, then the task, then the overlay."""
        self._download(KERNEL)
        ADSP.adsp2181_run(self.cpu, 5000)   # reset vector -> kernel init -> IDLE
        if not ADSP.adsp2181_idle(self.cpu):
            raise RuntimeError('kernel did not reach its idle loop')

        # Stand in for the foreground pass this harness never lets happen.
        for addr, value in RING_POINTERS.items():
            self.dm[addr] = value

        self._download(TIKRNL)
        ADSP.adsp2181_call(self.cpu, TASK_ENTRY, KERNEL_IDLE)
        for _ in range(1_000_000):
            ADSP.adsp2181_run(self.cpu, 1)
            if ADSP.adsp2181_idle(self.cpu):
                break
        else:
            raise RuntimeError('TIKRNL task entry did not return to the kernel')

        # The task registered itself: the kernel's foreground dispatch now
        # calls TIKRNL's per-sample continuation, and the SPORT0 ISR word the
        # task claimed calls its own copy of the instruction it displaced.
        self.foreground_slot = self.pm[PM_FOREGROUND_SLOT]
        self.isr_slot = self.pm[PM_ISR_SLOT]

        # Only now is it safe to download overlays: task init cleared PM
        # 0x0900-0x1DFF. The .F34 images are layered partial overlays. DIAL
        # calls shared routines beyond its own image (notably PM 0x244c and
        # 0x2c4f), supplied by V.OWN and FSK OWN in the real host flow. Loading
        # DIAL alone leaves those calls unpopulated and causes unstable V.8
        # classification/INFO transitions.
        for base_id in (V_OWN_ID, FSK_OWN_ID):
            entry = self.overlays.get(base_id)
            if entry is None:
                raise SystemExit(f'no extracted base image 0x{base_id:04x}')
            self._download(entry[0])
        if self.download_overlay(DIAL_ID) is None:
            raise SystemExit(f'no extracted image for download 0x{DIAL_ID:04x}')
        self.served.clear()   # the boot page is not a page *switch*

    def configure_g711_law(self, law: str) -> None:
        """Select TIKRNL's resident encoder parameter table."""
        self.modem_law = law
        self.dm[0x3309] = 0x35BE if law == 'pcmu' else 0x35B7

    def encode_g711(self, samples: list[int]) -> bytes:
        """Call TIKRNL's resident G.711 encoder at PM 0x1810.

        The PRI/E1 kernel selects the A-law parameter table at DM 0x3309.
        PM 0x1810 accepts signed linear PCM in AR and returns the serial-wire
        bit order in SR1.  Reverse each returned octet to conventional G.711
        file/RTP bit order.  Run this after modem framing: the subroutine uses
        core DAG registers that hardware SPORT companding would not disturb.
        """
        count = len(samples)
        if not count:
            return b''
        source = (ctypes.c_int16 * count)(*samples)
        encoded = (ctypes.c_uint8 * count)()
        result = ADSP.adsp2181_g711_encode_block(
            self.cpu, source, encoded, count,
            G711_ENCODE_ENTRY, KERNEL_IDLE, 1000)
        if result:
            raise RuntimeError(f'firmware G.711 block encoder failed: {result}')
        return bytes(encoded)

    def configure_modem(self, role: str, law: str = 'pcmu') -> None:
        """Activate the data pump directly, without MIPS/IDI call control.

        These are the ADDSP §5.4.1 database writes also made by the Linux
        driver's modem B1 assignment path.  GEN_SETUP1 bit 3 distinguishes
        calling (0x048c) from answering (0x0484) operation.
        """
        self.modem_role = role
        # The DSP's resident G.711 helper and the modem's selected SPORT law
        # share DM(0x3309).  The SIP endpoint has a separate codec Card for
        # wire encoding, so configuring that helper alone does not configure
        # this modem Card; without this write a PCMU call leaves the direct
        # PRI path on the boot-time A-law table.
        self.configure_g711_law(law)
        if role == 'idle':
            return
        # Tables 12-15 plus V.90-specific §5.3.1 fields. These values are all
        # resident before the final change_wdb strobe is consumed by DIAL.
        writes = {
            DM_DB + 0x00: 0x00C4,
            DM_DB + 0x01: 0x048C if role == 'calling' else 0x0484,
            DM_DB + 0x02: 0x0030,
            DM_DB + 0x04: MODEM_V8_SETUP,             # DPCM/digital or APCM/analog
            DM_DB + 0x07: 0xF0FD,
            DM_DB + 0x08: 0x0006, DM_DB + 0x09: 0x0006,
            DM_DB + 0x0A: 0x00FF, DM_DB + 0x0B: 0x0030,
            DM_DB + 0x0C: 0x0000,
            DM_DB + 0x28: 0x0001,                     # V.8
            # Norm_L, the modulation menu. This was 0x8100 -- V.90 and V.34
            # only -- and a live call advertises 0xA13F, which is what
            # norm_l_from_cai() returns for a CAI with nothing disabled. The
            # difference showed up as v8_line_result 0x8100 against run48's
            # 0xa100 at the page-14 handoff.
            DM_DB + 0x29: eicon_idi.norm_l_from_cai(),
            DM_DB + 0x2A: 0x001F, DM_DB + 0x2B: 0xFF00,
            DM_DB + 0x2C: 0x0003, DM_DB + 0x2D: 0x0003,
            DM_DB + 0x79: 0x003F, DM_DB + 0x7A: 0xFFFF,
            DM_DB + 0x7B: 0x03B7 | (0x0040 if law == 'pcma' else 0),
            DM_DB + 0x7C: 0x000E, DM_DB + 0x7D: 0x0015,
            DM_DB + 0x7E: 0x000E, DM_DB + 0x7F: 0x0015,
            DM_DB + 0x0E: 0x2000,
        }
        for address, value in writes.items():
            self.dm[address] = value
        # Norm_H is restored from here whenever a page takes the word back;
        # see _present_line().
        self.norm_h = writes[DM_DB + 0x28]

    def _run(self, entry: int, budget: int) -> collections.Counter:
        """Run the task from one entry point until it yields to the kernel."""
        hist: collections.Counter = collections.Counter()
        ADSP.adsp2181_call(self.cpu, entry, KERNEL_IDLE)
        for _ in range(budget):
            pc = ADSP.adsp2181_pc(self.cpu)
            hist[pc] += 1
            if pc not in self.pm_loaded:
                hint = ''
                if self.entry == FRAME_ENTRY and self.served:
                    hint = (' -- with an overlay resident and DM 0x2F28 still '
                            'unassigned, the host-command walk dispatches '
                            'overlay DM as code; drop --host-dispatch')
                raise RuntimeError(f'ran into unpopulated PM at 0x{pc:04x}{hint}')
            ADSP.adsp2181_run(self.cpu, 1)
            if ADSP.adsp2181_idle(self.cpu) or ADSP.adsp2181_pc(self.cpu) == KERNEL_IDLE:
                break
        return hist

    def _maybe_force_info(self, wanted: int, index: int) -> int:
        """Diagnostic host policy: replace a post-V.8 fallback with INFO.

        Shipping V.8 normally writes pending page 7 itself. This option tests
        whether a peer that goes quiet after V.8 is waiting for the host side
        to start V.34/V.90 Phase 2 rather than accepting the DSP's low-level
        fallback. It is intentionally opt-in and does not alter natural page-7
        requests.
        """
        page = self.dm[DM_BOOTPAGE]
        if (self.force_info_after_v8 and index >= 12000
                and self.resident == 0x025F and page not in (6, 7)
                and wanted != 0x0260):
            self.dm[DM_BOOTPAGE] = 7
            self.dm[DM_DOWNLOAD_REQ] = 0x0260
            if not self.forced_info_samples or self.forced_info_samples[-1] != index:
                self.forced_info_samples.append(index)
            return 0x0260
        return wanted

    def line_rx_word(self, code: int, linear: int) -> int:
        """Sample representation presented by this card family's line codec.

        PRI/T1 firmware receives an 8-bit companded timeslot. The Analog
        kernel instead services its single-channel codec on SPORT1 and its
        modem task consumes signed-linear samples; feeding it a G.711 octet
        made V8.ANA classify byte values as waveform amplitudes.
        """
        return line_codec_rx_word(self.firmware_set, code, linear)

    def _maybe_request_v8(self, index: int) -> bool:
        """Ask for the calling side's V.8 page, once DIAL has run.

        Returns True when this frame should enter TIKRNL at the page-request
        routine (PM 0x068D on ANA, 0x0686 on F34) instead of the frame head.
        Setting the bootpage and ringing the DM(0x3FC1) doorbell is not enough
        on its own: the frame path calls the resident page's line handler
        first, and DIAL rewrites DM(0x3FB0) back to its own page before the
        request routine ever reads it. Entering at the request routine is what
        the kernel does when a page asks, and it takes the bootpage as given.

        This is the direct-backend counterpart of eicon_mips_shim's
        ORIGINATE_V8: the legitimate path is an AT dial script this harness
        bypasses.
        """
        if (self.originate_v8_at is None or self._originate_v8_done
                or index < self.originate_v8_at
                or self.resident == 0x025F):
            return False
        self._originate_v8_done = True
        self.dm[0x0491] = 6
        self.dm[DM_BOOTPAGE] = 6
        # The request routine publishes the download id but does not raise the
        # strobe -- the page normally has. Raise it so the serve loop below
        # sees the yield it is about to produce.
        self.dm[DM_STATUS] |= 0x0100
        return True

    def _run_and_serve(self, entry: int, index: int, budget: int) -> None:
        """Run one half of the frame, serving whatever downloads it asks for.

        Both halves ask. The host side of the handshake is the same either
        way: read the published download id, load it, and re-enter at
        DM(0x31BB) until the task stops asking.
        """
        for _ in range(self.max_downloads + 1):
            ADSP.adsp2181_call(self.cpu, entry, KERNEL_IDLE)
            ADSP.adsp2181_run(self.cpu, budget)
            if not ADSP.adsp2181_idle(self.cpu):
                # The run ended on the budget, not on the task's return, so
                # the synthetic return address is still on the PC stack and
                # the next frame will stack on top of it. Everything after
                # this point is running on state the firmware did not build.
                self.truncated_frames += 1
                if self.truncated_frames == 1:
                    print(f'[frame] TRUNCATED at sample {index}: the task did '
                          f'not return within {budget} cycles '
                          f'(overlay 0x{self.resident:04x}, bootpage '
                          f'{self.dm[DM_BOOTPAGE]}, TrnProgress '
                          f'0x{self.dm[DM_TRNPROGRESS]:04x}). The PC stack is '
                          f'left mid-call and will overflow within a few '
                          f'frames; raise the frame budget above '
                          f'{FRAME_BUDGET}. Reported once.')
            if not self.serve or not (self.dm[DM_STATUS] & 0x0100):
                break
            wanted = self._maybe_force_info(self.dm[DM_DOWNLOAD_REQ], index)
            if wanted == self.resident:
                # Complete a still-asserted request without resetting the
                # state held in the already-resident partial overlay.
                entry = self.dm[RESUME_DOWNLOAD]
                continue
            description = self.download_overlay(wanted)
            if description is None:
                break
            if self.pending_overlay_init is not None:
                queued, queued_path = self.pending_overlay_init
                self.pending_overlay_init = None
                self._call_overlay_entry(queued, queued_path)
            # Match the selected-channel/native handoff: the staged PCMU
            # table must be restored after the page initializer but before
            # the resumed page executes its first media pass.
            self._restore_v90d_pcmu_ucode_table()
            self.switches.append((index, self.dm[DM_BOOTPAGE], wanted))
            entry = self.dm[RESUME_DOWNLOAD]

    def _present_rxsample(self, rx_code: int) -> None:
        """Diagnostic: fill RXSAMPLE_0..5 the way the kernel is said to.

        `addsp_database.md` on write offsets 0x50..0x55: "at symbol rate the
        kernel writes 3, 4 or 5 samples in", the count being DM(0x3F67). The
        V.8 page's detector front end reads that array -- PM 0x3764 walks
        DM(I4,M5) from DM(0x06BE), which sits inside it -- and this backend
        never wrote it, so the discriminator integrated a frozen buffer.

        This is a stand-in for the kernel, not the kernel, and it is off by
        default. It exists to show that filling the array is what the page is
        missing; the durable fix is a SPORT1 kernel-driven receive path.
        """
        self.rxsample_history.append(rx_code & 0xFFFF)
        if len(self.rxsample_history) < 6:
            return
        # Fill all six slots, not DM(0x3F67) of them: the page's own read
        # pointer DM(0x06BE) sits at 0x3F34 -- slot 4 -- for 99.9% of samples,
        # so a fill of slots 0..3 writes what nothing reads.
        for slot, word in enumerate(self.rxsample_history):   # oldest first
            self.dm[DM_RXSAMPLE + slot] = word

    def _present_line(self, rx_code: int) -> None:
        """Hand one line sample to the page the way the kernel hands it over.

        `DM(0x3F08)` is not a line register. The ADDSP database (§5.3, write
        offset 0x28) calls it **Norm_H** -- the companion to Norm_L at
        `DM(0x3F09)`, carrying the V8/V110/V18/speakerphone/low-level bits that
        `configure_modem` sets. The page reads it as configuration: TIKRNL
        PM 0x06DE ORs 0x1000 into Norm_L when bits 5-6 are set, and V8.ANA
        PM 0x3834..0x383D picks which V.8 CM call-function octet to transmit
        from the same bits. Writing a line sample there rewrote the call's
        configuration 8000 times a second.

        The sample itself is handed over in SR1 at the sample-continuation
        entry -- see frame_fast(), where it is set. TIKRNL is what stores it
        through `shellinptr` (write offset 0x2f, `DM(0x3F0F)`), after a
        one-sample delay and a x0.25 scale, so this must not write there
        itself. What is left here is the pre-V.8 line stand-in and the
        report that a resident page has published no pointer at all.
        """
        self.line_sample = rx_code & 0xFFFF
        if self.rxsample_stand_in and self.private_line_active:
            self._present_rxsample(rx_code)
        if not self.private_line_active:
            self.dm[DM_NORM_H] = rx_code & 0xFFFF
            return
        target = self.dm[DM_SHELLINPTR] & 0x3FFF
        if not target and not self._shellinptr_warned:
            # The page has not said where it wants the sample, so there is
            # nowhere honest to put it. Only the boot-time V.8 pre-load reaches
            # here: it loads the page over a DIAL that never ran, so DIAL's
            # action vector -- which publishes the pointer -- never runs
            # either. EICON_ORIGINATE_V8_AFTER asks for the page instead.
            self._shellinptr_warned = True
            print('[card] V.8 is resident but shellinptr DM(0x3F0F) is '
                  'unpublished; the page is receiving nothing')

    def frame(self, rx_code: int, index: int = 0,
              budget: int = FRAME_BUDGET) -> collections.Counter:
        """One 8 kHz frame: present a line sample, run TIKRNL's frame loop.

        The frame is not one pass through the task.  Whenever DIAL raises the
        page-change strobe (DM 0x3FC1 bit 8) the request path at PM 0x0686
        publishes an overlay download and yields; the host serves it and
        re-enters at DM(0x31BB) = PM 0x06D8.  Playing that host role here is
        what carries the frame past the request into the state dispatcher.
        """
        self._present_line(rx_code)
        # The host publishes TXD0 before the task's frame pass consumes it.
        # This is only active for the opt-in V.90A mailbox probe; ordinary
        # calls retain TIKRNL ownership of the synchronous TX words.
        self._service_v90a_tx_request()
        hist: collections.Counter = collections.Counter()
        entry = self.entry
        for _ in range(self.max_downloads + 1):
            this_pass = self._run(entry, budget)
            hist.update(this_pass)
            if not self.serve or not this_pass.get(PM_DOWNLOAD_YIELD):
                break
            wanted = self._maybe_force_info(self.dm[DM_DOWNLOAD_REQ], index)
            if wanted == self.resident:
                # The direct completion path can leave the request strobe set
                # briefly. Resume it, but do not destructively reload the
                # already-resident partial image or report a new page switch.
                entry = self.dm[RESUME_DOWNLOAD]
                continue
            description = self.download_overlay(wanted)
            if description is None:
                if self.log:
                    print(f'  frame {index}: no image for download '
                          f'0x{wanted:04x}, cannot serve')
                break
            self.switches.append((index, self.dm[DM_BOOTPAGE], wanted))
            if self.log:
                print(f'  frame {index}: bootpage {self.dm[DM_BOOTPAGE]:04x} '
                      f'-> served 0x{wanted:04x} {description}')
            entry = self.dm[RESUME_DOWNLOAD]

        # The real kernel invokes the continuation TIKRNL registered at init
        # once per SPORT sample.  It calls DM(3FB3), consumes DM(3FB4), and
        # runs the task's TX post-processing.  Calling only the 0x06c1 half
        # drives page/RX state but silently leaves every transmitter idle.
        hist.update(self._run(SAMPLE_CONTINUATION, budget))
        return hist

    def frame_fast(self, rx_code: int, index: int = 0,
                   budget: int = FRAME_BUDGET) -> int:
        """Production version of :meth:`frame` without instruction tracing.

        ``adsp2181_run`` executes the whole pass in C.  The page-change strobe
        remains set while TIKRNL is yielded to the host, so it is sufficient
        to detect downloads without the Python per-instruction PC histogram.
        Returns the current signed-linear transmit sample.
        """
        self._present_line(rx_code)
        if (self.resident == V90D_ID and V90D_RATE_PIN
                and self.dm[0x3fc2] == V90D_RATE_PIN_STATE):
            if not self._v90d_rate_pinned:
                self._v90d_rate_pinned = True
                print(f'[v90d] diagnostic rate-quality force in state '
                      f'0x{V90D_RATE_PIN_STATE:04x}: '
                      f'DM(0x2117)=0x{V90D_RATE_PIN_VALUE:04x}')
            self.dm[0x2117] = V90D_RATE_PIN_VALUE
        # The native host answers a request raised by the preceding SPORT
        # sample before the next page pass.  Keep this at the frame boundary so
        # the firmware sees the same mailbox ownership timing.
        self._service_v90d_tx_request()
        self._service_v90a_tx_request()
        entry = (PAGE_REQUEST_ENTRY if self._maybe_request_v8(index)
                 else self.entry)
        self._run_and_serve(entry, index, budget)
        # Some direct TIKRNL paths write their mark-fill word while the
        # request remains asserted.  The native host re-publishes its pending
        # datagram after that task pass; do the same before the continuation.
        self._service_v90d_tx_request()
        self._service_v90a_tx_request()
        # The continuation asks for pages too, and its requests used to be
        # dropped: TIKRNL reaches the request routine two ways, by tail jump
        # from the frame path (PM 0x06EC) and by CALL from the continuation
        # path (PM 0x078C), and only the first was served here. On the Analog
        # dial page every V.8 request comes the second way -- DM(0x31AC) held
        # 0x025F with bootpage 6 and the strobe set at the end of the frame,
        # and the next frame's DIAL pass overwrote it with 0x0263 before
        # anyone looked. Serving both halves is what lets the card leave DIAL
        # for V.8 on its own.
        # The continuation reads the line sample out of SR1 -- PM 0x0715 on
        # the Analog task, PM 0x0700 on the PRI one -- delays it a sample
        # through DM(0x31B8), scales it by 0x2000 (x0.25, the right-justified
        # SPORT representation) and only then stores it through ShellInptr.
        # Writing DM(ShellInptr) from here instead was overwritten by that
        # store every sample, so the page's detectors ran on whatever SR1
        # happened to hold: DM(0x399A), which PM 0x2119 writes on every pass
        # of the V.8 filter bank, never changed once in a 90,000-sample call.
        if V90D_TX_CENSUS and self.resident == V90D_ID:
            self._census_split('frame')
        frame_tx_value = (self.dm[DM_TX_POINTER]
                          if self.resident == V90D_ID else None)
        ADSP.adsp2181_set_sr1(self.cpu, self.line_sample)
        self._run_and_serve(SAMPLE_CONTINUATION, index, budget)
        if (self.resident == V90D_ID and V90D_RATE_PIN
                and self.dm[0x3fc2] == V90D_RATE_PIN_STATE):
            self.dm[0x2117] = V90D_RATE_PIN_VALUE
        if V90D_TX_CENSUS and self.resident == V90D_ID:
            self._census_split('continuation')
        # The page initializer may have rewritten the shared U-code table
        # during either half above; restore the selected PCMU table before the
        # next media sample can enter Phase 3.
        self._restore_v90d_pcmu_ucode_table()
        # The native 2185 host holds PM 0x19c8 and services the delay ABI at
        # the frame boundary. Do the same before exposing this frame to the
        # media loop.
        self._service_v90d_bulk()
        if self.resident == V90D_ID:
            # Page 14 publishes the *sample itself* in DM(0x3FB4), not a
            # pointer to it, and Session 267 counted rather than inferred it:
            # DM(0x3764) takes no writes at all while this page is resident, so
            # there is no block there to dereference or drain. PM 0x19ee is a
            # context *restore* -- it reloads the page's own saved copy from
            # DM(0x3607), which is why the stale generic pointer 0x3764 shows
            # up in the word at all -- and PM 0x1a1e then publishes the
            # serializer's output port DM(0x3FA7), one word per sample.
            # eicon_mips_shim.py has taken the value directly since the native
            # tower first reached this page; this backend kept dereferencing
            # until the serializer first published, which is when it mattered.
            value = (frame_tx_value
                     if (V90D_TX_READ_PHASE == 'frame'
                         and frame_tx_value is not None)
                     else self.dm[DM_TX_POINTER])
        else:
            pointer = self.dm[DM_TX_POINTER] & 0x3FFF
            value = self.dm[pointer] if pointer else 0
        if V90D_TX_CENSUS and self.resident == V90D_ID:
            self._census_frame(value)
        if os.getenv('EICON_FEDEBUG'):
            st = getattr(self, '_fe2', None)
            if st is None:
                st = self._fe2 = {'buf': set(), 'lvl': 0, 'cnt': 0, 'in': set()}
            st['buf'].add(tuple(self.dm[a] for a in range(0x3F30, 0x3F36)))
            st['in'].add(self.dm[0x0772])
            st.setdefault('ptr', {})
            pv = self.dm[0x06BE]
            st['ptr'][pv] = st['ptr'].get(pv, 0) + 1
            st['lvl'] = max(st['lvl'], self.dm[0x07BC])
            st['cnt'] = max(st['cnt'], self.dm[0x07BD])
            if index == 60000:
                tot = sum(st['ptr'].values())
                print('[fe] DM(06BE) pointer: ' + ', '.join(
                    '%04x=%.1f%%' % (k, 100.0*v/tot)
                    for k, v in sorted(st['ptr'].items(), key=lambda kv: -kv[1])[:4]))
                print('[fe] stand-in=%s RXSAMPLE distinct=%d  DM(0772) '
                      'distinct=%d  peak level DM(07BC)=%d  peak count '
                      'DM(07BD)=%d  (threshold %d, escape 1920)'
                      % (self.rxsample_stand_in, len(st['buf']), len(st['in']),
                         st['lvl'], st['cnt'], self.dm[0x0748]))
        sample = value - 0x10000 if value & 0x8000 else value
        if self.resident == V90D_ID and V90D_TX_GAIN != 1.0:
            sample = max(-32768, min(32767, round(sample * V90D_TX_GAIN)))
        return sample

    def _census_frame(self, published: int) -> None:
        """Count what page 14 writes per frame; report it at exit.

        The two readings of an 83%-zero transmit differ in one observable.  If
        DM(0x3764) is a live block base, it is written N words per serializer
        pass and the host's single read per tick decimates it by N.  If it is
        not written at all, the sample really does arrive in DM(0x3FB4) and the
        zeros come from further up -- from the six-word mapping frame at
        DM(0x3FA7..0x3FAC), which the resident kernel tail clears every frame
        and the generator refills only once per pass.  Counting the writes to
        both, per frame and per TrnProgress state, separates them.
        """
        state = self._census
        if state is None:
            addresses = (list(range(V90D_TX_CENSUS_BLOCK,
                                    V90D_TX_CENSUS_BLOCK + 8))
                         + list(range(V90D_TX_CENSUS_FRAME,
                                      V90D_TX_CENSUS_FRAME + 6))
                         + [DM_TX_POINTER, 0x20DE])
            ADSP.adsp2181_dm_census_clear(self.cpu)
            ADSP.adsp2181_dm_census(self.cpu, 1)
            state = self._census = {
                'addresses': addresses,
                'previous': {a: 0 for a in addresses},
                'frames': collections.Counter(),
                'writes': collections.defaultdict(collections.Counter),
                'per_frame': collections.defaultdict(collections.Counter),
                'published': collections.Counter(),
            }
            import atexit
            atexit.register(self._census_report)
            print('[tx-census] counting page-14 DM writes '
                  '(EICON_V90D_TX_CENSUS)')
        trn = self.dm[DM_TRNPROGRESS]
        state['frames'][trn] += 1
        if published:
            state['published'][trn] += 1
        for address in state['addresses']:
            now = ADSP.adsp2181_dm_census_count(self.cpu, address)
            delta = now - state['previous'][address]
            state['previous'][address] = now
            if delta:
                state['writes'][trn][address] += delta
                state['per_frame'][address][delta] += 1

    def _census_split(self, half: str) -> None:
        """Attribute the clear and the serializer to a half of our frame.

        The harness calls two kernel entries per 8 kHz sample: the frame path
        and the registered sample continuation.  Which half runs the six-word
        clear and which runs the page's serializer decides whether the block's
        one-frame lifetime is the firmware's own ordering or an artefact of
        calling one of those entries at the wrong rate.
        """
        state = self._census
        if state is None:
            return
        split = state.setdefault('split', {})
        counters = state.setdefault('split_counters', {})
        for name, pc in (('clear', 0x06C6), ('serializer', 0x2EEF),
                         ('generator', 0x2A4F), ('publish', 0x1A1E)):
            now = ADSP.adsp2181_coverage_count(self.cpu, pc)
            delta = now - counters.get(name, 0)
            counters[name] = now
            if delta:
                split[(name, half)] = split.get((name, half), 0) + delta

    def _census_report(self) -> None:
        state = self._census
        if not state:
            return
        total = sum(state['frames'].values())
        print(f'[tx-census] {total} page-14 frames')
        for trn, frames in sorted(state['frames'].items()):
            live = state['published'][trn]
            print(f'[tx-census] TrnProgress {trn:04x}: {frames} frames, '
                  f'{live} published a nonzero sample '
                  f'({100.0 * live / max(1, frames):.1f}%)')
            for address in sorted(state['writes'][trn]):
                count = state['writes'][trn][address]
                print(f'    DM {address:04x}: {count:9d} writes '
                      f'= {count / frames:7.3f}/frame')
        split = state.get('split') or {}
        if split:
            print('[tx-census] executions by half of the harness frame:')
            for key in sorted(split):
                print(f'    {key[0]:10s} in the {key[1]:12s} half: {split[key]}')
        print('[tx-census] writes per frame, distribution:')
        for address in sorted(state['per_frame']):
            dist = ' '.join(f'{k}x{v}' for k, v in
                            sorted(state['per_frame'][address].items()))
            print(f'    DM {address:04x}: {dist}')


def print_bootpage_table(card: Card) -> None:
    names = {0x025C: 'FSK OWN', 0x025F: 'V.8', 0x0260: 'INFO', 0x0261: 'V.34',
             0x0262: 'DIAL/FSK/FAX', 0x0263: 'DIAL partial', 0x0266: 'V.22/V.32 LEC',
             0x0268: '?', 0x0269: '?', 0x026A: 'V.90 DPCM', 0x026B: '?',
             0x026E: 'INFOH', 0x026F: 'HV.34', 0x0271: 'V.22FC'}
    print('bootpage table (DM 0x31D5, indexed by bootpage_nr / DM 0x3FB0):')
    for page in range(18):
        raw = card.dm[DM_BOOTPAGE_TABLE + page]
        download = raw if raw < 0x8000 else 0x10000 - raw
        sign = '+' if raw < 0x8000 else '-'
        print(f'  page {page:2d}: {raw:04x} {sign} download 0x{download:04x} '
              f'{names.get(download, "")}')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--frames', type=int, default=200)
    ap.add_argument('--firmware-set', choices=tuple(FIRMWARE_SETS),
                    default='pri117',
                    help='coherent kernel/TIKRNL/overlay family; analog109 '
                         'requires the extracted build-109-789 card-type-77 set')
    ap.add_argument('--state-out', type=Path,
                    help='write changed V.8/INFO state snapshots and WDB values as JSON')
    ap.add_argument('--force-info-at', type=int,
                    help='diagnostic: directly layer INFO (0x0260) at this frame '
                         'using the live V.8/DIAL database')
    ap.add_argument('--freq', type=int, default=2100,
                    help='line tone in Hz (0 = μ-law silence)')
    ap.add_argument('--amp', type=int, default=20000)
    ap.add_argument('--role', choices=('idle', 'answer', 'calling'),
                    default='idle',
                    help='directly activate the modem database in this role; '
                         'bypasses MIPS, IDI, signalling and bearer assignment')
    ap.add_argument('--tx-out', type=Path,
                    help='write DM[3FB4] signed-linear TX samples as s16le')
    ap.add_argument('--g711-out', type=Path,
                    help='call TIKRNL PM 0x1810 and write raw A-law octets')
    ap.add_argument('--bootpage-table', action='store_true',
                    help='dump the recovered bootpage -> overlay id table')
    ap.add_argument('--no-serve-overlays', action='store_true',
                    help='leave the download requests unanswered, as before: '
                         'the task then never gets past PM 0x0686')
    ap.add_argument('--max-downloads', type=int, default=8,
                    help='cap on overlay downloads served within one frame')
    ap.add_argument('--host-dispatch', action='store_true',
                    help='enter at PM 0x06BB and run the host-command fetch '
                         'and dispatch; needs a MIPS-assigned queue, and '
                         'without one it dispatches overlay DM as code')
    ap.add_argument('--log', action='store_true')
    args = ap.parse_args()
    select_firmware_set(args.firmware_set)

    card = Card(log=args.log, serve=not args.no_serve_overlays,
                max_downloads=args.max_downloads,
                host_dispatch=args.host_dispatch)
    card.boot()
    card.configure_modem(args.role)
    print(f'[card] firmware-set={FIRMWARE_SET} kernel={KERNEL} overlays={OVERLAYS}')
    print(f'[card] kernel + TIKRNL + DIAL up; role={args.role}; the task claimed '
          f'PM {PM_FOREGROUND_SLOT:04x}={card.foreground_slot & 0xFFFFFF:06x} '
          f'(foreground dispatch) and '
          f'PM {PM_ISR_SLOT:04x}={card.isr_slot & 0xFFFFFF:06x} (SPORT0 ISR)')
    print(f'[card] overlay stubs after the DIAL download: '
          f'PM 08f0={card.pm[OVL_STATE_STUB]:06x} '
          f'08f1={card.pm[OVL_LINE_STUB]:06x} (TIKRNL ships both as 0a000f = RTS)')
    print(f'[card] frame entry PM {card.entry:04x}; host-command queue '
          f'DM 2f28={card.dm[DM_QUEUE_HEAD]:04x} '
          + ('(assigned)' if card.dm[DM_QUEUE_HEAD] else
             '(unassigned -- the MIPS-side channel assignment)'))
    print(f'[card] task entry table DM {RESUME_ENTRIES:04x}: '
          f'AR=1 -> {card.dm[RESUME_ENTRIES]:04x}  '
          f'AR=2 -> {card.dm[RESUME_DOWNLOAD]:04x} (post-download resume)')
    print(f'[card] overlay images indexed: {len(card.overlays)} from {OVERLAYS}')

    if args.bootpage_table:
        print_bootpage_table(card)

    if args.freq:
        tone = [linear_to_mulaw(int(args.amp * math.sin(2 * math.pi * args.freq * i / 8000)))
                for i in range(8000)]
    else:
        tone = [0xFF] * 8000

    totals: collections.Counter = collections.Counter()
    states: collections.Counter = collections.Counter()
    changes = 0
    prev = None
    tx = []
    tx_linear = []
    state_trace = []
    state_trace_previous = None
    for f in range(args.frames):
        if args.force_info_at == f:
            card.dm[DM_BOOTPAGE] = 7
            description = card.download_overlay(0x0260)
            if description is None:
                raise SystemExit('the selected firmware set has no INFO overlay')
            card.switches.append((f, 7, 0x0260))
            print(f'[card] frame {f}: diagnostic direct load -> {description}')
        hist = card.frame(tone[f % len(tone)], index=f)
        totals.update(hist)
        state = card.dm[DM_BOOTPAGE]
        states[state] += 1
        tx.append(card.dm[DM_LINE_TX])
        tx_pointer = card.dm[DM_TX_POINTER] & 0x3FFF
        tx_linear.append(card.dm[tx_pointer] if tx_pointer else 0)
        trace_key = (card.resident, state, card.dm[0x3FAD],
                     card.dm[0x164C], card.dm[0x19CF], card.dm[0x198E],
                     card.dm[DM_VEC_A], card.dm[DM_VEC_B],
                     card.dm[DM_DOWNLOAD_REQ])
        if args.state_out and trace_key != state_trace_previous:
            state_trace.append({
                'frame': f, 'resident': card.resident, 'bootpage': state,
                'trnprogress': card.dm[0x3FAD], 'info_framer': card.dm[0x164C],
                'info_framer_state': card.dm[0x19CF],
                'info_event': card.dm[0x198E],
                'vector_a': card.dm[DM_VEC_A], 'vector_b': card.dm[DM_VEC_B],
                'download_request': card.dm[DM_DOWNLOAD_REQ],
                'wdb': [card.dm[address] for address in range(DM_DB, 0x3F10)],
            })
            state_trace_previous = trace_key
        now = (card.dm[DM_LINE_RX], card.dm[DM_LINE_TX], state,
               card.dm[DM_VEC_A], card.dm[DM_VEC_B], card.dm[DM_STATUS])
        if now != prev:
            changes += 1
            if args.log or changes <= 12:
                print(f'  frame {f:4d}: 3F08={now[0]:04x} 3F09={now[1]:04x} '
                      f'3FB0={now[2]:04x} 3FB2={now[3]:04x} 3FB3={now[4]:04x} '
                      f'3FC1={now[5]:04x}')
            prev = now

    print(f'[card] {args.frames} frames, {changes} data-pump register changes')
    print('[card] DIAL entered via the overlay stubs: '
          f'08f1(line)={totals.get(OVL_LINE_STUB, 0)} '
          f'08f0(state)={totals.get(OVL_STATE_STUB, 0)} '
          f'1bbd={totals.get(0x1BBD, 0)} 1b9c={totals.get(0x1B9C, 0)}')
    print('[card] DIAL DSP work: action dispatch 1da7='
          f'{totals.get(0x1DA7, 0)} line-signal handler 1bce={totals.get(0x1BCE, 0)}')
    print('[card] SIG stubs (PM 1900-1902): '
          + ' '.join(f'{a:04x}:{totals.get(a, 0)}' for a in SIG_STUBS))
    print(f'[card] action vector call (PM 06e8, via DM 3FB2): '
          f'{totals.get(0x06E8, 0)}')
    print('[card] bootpage_nr (DM 3FB0) histogram: '
          + ' '.join(f'{s:04x}:{c}' for s, c in states.most_common()))

    # PM 0x0686-0x0694: index DM 0x31D5 with bootpage_nr, then publish the
    # wanted overlay in DM 0x31AA with a type in DM 0x31A9.  A positive table
    # entry is requested directly; the negative entries (DIAL among them) fall
    # through to the fixed pair (type 0x000D, download 0x0270 = the SIG
    # overlay) until SIG is resident, and are then requested directly too.
    if card.switches:
        print(f'[card] page switches served: {len(card.switches)}')
        for wanted, count in card.served.most_common():
            name = card.overlays[wanted][1].split(' Version')[0]
            pages = sorted({p for _, p, w in card.switches if w == wanted})
            print(f'  0x{wanted:04x} {name:28s} x{count:<4d} '
                  'from page ' + ','.join(f'{p:04x}' for p in pages))
        chain = ' -> '.join(f'{p:04x}:{w:04x}' for _, p, w in card.switches[:12])
        print(f'  first switches (page:download): {chain}'
              + (' ...' if len(card.switches) > 12 else ''))
    for wanted, count in sorted(card.unserved.items()):
        print(f'[card] UNSERVED download 0x{wanted:04x} x{count}: '
              'no extracted image, the frame stops at the request')
    download = card.dm[DM_DOWNLOAD_REQ]
    if download:
        print(f'[card] last TIKRNL overlay request: DM {DM_DOWNLOAD_REQ:04x}='
              f'0x{download:04x} type DM {DM_DOWNLOAD_FLAG:04x}='
              f'0x{card.dm[DM_DOWNLOAD_FLAG]:04x}; '
              f'resident overlay 0x{card.resident:04x}')
    # DM 0x3F09 is the second line register; TIKRNL ORs 0x1000 into it at PM
    # 0x06CE before handing the frame to the overlay, so print the whole word
    # rather than pretending the low byte is a bare μ-law codeword.
    print('[card] DM 3F09 (line register, post-TIKRNL) first 16: '
          + ' '.join(f'{v:04x}' for v in tx[:16]))
    first_nonzero = next((i for i, value in enumerate(tx_linear) if value), None)
    print(f'[card] DM[3FB4] signed-linear TX: pointer={card.dm[DM_TX_POINTER]:04x} '
          f'nonzero={sum(value != 0 for value in tx_linear)}/{len(tx_linear)} '
          f'first-nonzero={first_nonzero if first_nonzero is not None else "none"} '
          'first16=' + ' '.join(f'{v:04x}' for v in tx_linear[:16]))
    if args.tx_out:
        args.tx_out.parent.mkdir(parents=True, exist_ok=True)
        args.tx_out.write_bytes(b''.join(
            int(value).to_bytes(2, 'little') for value in tx_linear))
        print(f'[card] wrote {len(tx_linear)} signed-linear samples to {args.tx_out}')
    if args.state_out:
        args.state_out.parent.mkdir(parents=True, exist_ok=True)
        args.state_out.write_text(json.dumps({
            'firmware_set': FIRMWARE_SET, 'kernel': KERNEL,
            'tikrnl': TIKRNL, 'overlays': OVERLAYS,
            'role': args.role, 'frequency': args.freq,
            'switches': card.switches, 'states': state_trace,
        }, indent=2) + '\n')
        print(f'[card] wrote {len(state_trace)} changed state snapshots to '
              f'{args.state_out}')
    if args.g711_out:
        g711 = card.encode_g711(tx_linear)
        args.g711_out.parent.mkdir(parents=True, exist_ok=True)
        args.g711_out.write_bytes(g711)
        print(f'[card] called TIKRNL PM {G711_ENCODE_ENTRY:04x}; wrote '
              f'{len(g711)} A-law octets to {args.g711_out}; '
              f'first16={" ".join(f"{value:02x}" for value in g711[:16])}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
