#!/usr/bin/env python3
"""MIPS-side driver shim for the Eicon ADSP emulator.

Runs the real te_dmlt.pm firmware routines (host-port helpers, script
sender, database record builder/commit, request parser, and the modem
service-assign entry) under Unicorn and connects their host-port
transactions to the ADSP-2181 emulator (ctypes), so the firmware's own code
drives the DSP exactly as on real hardware.

Scope:
  * default: request_parser -> script_sender command-script commit
    (code 66 reproduces the script-66 PM-ring commit).
  * --assign: the service-assign entry 0x80096980 (service-driver table
    slot 1) with a synthesized TIKRNL download/task struct, performing the
    switch-on database commit through the real firmware path.
"""

from __future__ import annotations

import argparse
import atexit
import collections
import ctypes
import json
import math
import os
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_LITTLE_ENDIAN, UC_MODE_32
from unicorn import UC_HOOK_BLOCK, UC_HOOK_CODE
# Resolved once at import. These were previously imported inside the
# per-instruction hook, where the repeated `from ... import` cost more than the
# work the hook did.
from unicorn.mips_const import (UC_MIPS_REG_0, UC_MIPS_REG_A0, UC_MIPS_REG_A1,
                                UC_MIPS_REG_A2, UC_MIPS_REG_A3, UC_MIPS_REG_GP,
                                UC_MIPS_REG_PC, UC_MIPS_REG_RA,
                                UC_MIPS_REG_SP, UC_MIPS_REG_V0)

sys.path.insert(0, str(Path(__file__).resolve().parent))

import eicon_idi
import eicon_mips_image
from dial_tikrnl_drive import sport_rx_word
from eicon_dsp_stage import (CARDTYPE_DIVASRV_P_30M_PCI,
                             OFFS_DSP_CODE_BASE_ADDR, build_dsp_code_image,
                             protocol_end_addr)
from v42_lapm import LapmEndpoint

BIAS = 0x80011000
# Unicorn's MIPS translates kseg0 (0x8xxxxxxx) to physical by clearing the
# top three bits, so all mappings use the physical equivalents.
PHYS_BIAS = 0x00011000
IMAGE_SIZE = 0x100000  # covers code (0x80011000) and data (0x8010xxxx)
RAM_VIRT = 0x80800000
RAM_BASE = 0x00800000
RAM_SIZE = 0x100000
STUB_VIRT = 0x80900000
STUB_BASE = 0x00900000

# See the page-14 overlay-load site below. V90D holds the shared worker until
# its rate block is coherent, then restores the firmware tail call. Set
# EICON_V90D_BULK_ADAPTER=0 to retain the old diagnostic bypass for A/Bs.
V90D_BULK_ADAPTER_DISABLED = os.environ.get("EICON_V90D_BULK_ADAPTER", "1") != "1"
# Diagnostic: hold the same shared worker on the V.34 page.  PM 0x19c8 is
# `JUMP $1900` on 0x0261 exactly as it is on 0x026A, so the V90D hold applies
# unchanged.  Sessions 115h-i showed the freeze is insensitive to every
# host-side echo-canceller and symbol-rate control, which leaves one question
# the bulk worker can still answer: whether it is cause or symptom.  With this
# set, PM 0x1930/0x1934 never run and DM(0x00A8..0x00A9) cannot be overwritten
# by them; if V.34 still freezes at 0x0064 the worker is not the cause.
V34_BULK_HOLD = os.environ.get("EICON_V34_BULK_HOLD", "0") == "1"
# The repair the hold points at.  V90D already solves this shape of problem:
# hold the native tail jump and serve the documented near/far delay-line ABI
# from PortableBulkDelay instead.  PortableBulkDelay.service() is page-agnostic
# -- it reads the lengths and inputs at DM(0x3FBC..0x3FBF) and publishes the
# output pairs at DM(0x3F36..0x3F39), all of which the ADDSP guide defines for
# both pages -- so the same adapter applies to V.34 unchanged.  Implies the
# hold, since running both would put the firmware worker back on the loose.
# Enabled by default since Session 115l: three calls each, plain against
# portable.  Plain froze at TrnProgress 0x0064 in both valid calls with a
# ~928 M-iteration runaway and writes from PM 0x1930/0x1934/0x2e21;
# portable froze in none of three, kept the per-sample dispatch alive and
# put zero writes in 0x0061..0x0241 from any of those PCs.
# EICON_V34_PORTABLE_BULK=0 restores the native worker for A/Bs.
V34_PORTABLE_BULK = os.environ.get("EICON_V34_PORTABLE_BULK", "1") == "1"
# PM 0x1917/0x1921 read descriptor offset 5 as the lower limit for the
# zero-based near/far bulk delay line.  The comparison is followed by an add
# of BulkLength on unsigned underflow, so the word immediately below DM zero
# is the 16-bit -1 sentinel.  V.34 and V90D deliberately leave descriptor
# words 5..7 sparse while INFO clears low DM; the selected-channel common
# layer must therefore publish this retained word before either page resumes.
BULK_DESCRIPTOR_LOWER_LIMIT = 0xFFFF
V90_SPEED_FORMAT_MASK = 0x2020
# The native V90D worker is not safe merely because the datagram width is
# legal.  Width 31 made PM 0x1930 escape the delay area, and a later width-32
# mailbox-instrumented call reproduced the same overwrite after a nominally
# coherent release.  No width is therefore qualified: keep the worker held
# fail-closed until its remaining descriptor/phase precondition is recovered.
# The environment override is diagnostic-only: it permits deterministic
# archived-capture instruction traces of a suspect width without weakening the
# default hardware policy.
V90D_QUALIFIED_BULK_WIDTHS = frozenset(
    int(field, 0)
    for field in os.environ.get("EICON_V90D_QUALIFIED_BULK_WIDTHS", "").split(",")
    if field.strip())
# The shipped page-14 worker has now escaped its zero-based delay area at both
# width 31 and width 32.  Keep that code held and provide the ADDSP database
# contract with a bounded host-side ring instead.  The guide defines offsets
# 0x56..0x59 as the near and oldest X/Y pairs, offsets 0xdc/0xdd as lengths in
# X/Y couples, and offsets 0xde/0xdf as the pair inserted by the modem core.
# The database base is DM 0x3ee0 for every one of them -- see
# PortableBulkDelay.service() for why the 0x56 group is not at 0x3fb6.
# EICON_V90D_PORTABLE_BULK=0 retains the held/native diagnostic path.
V90D_PORTABLE_BULK = os.environ.get("EICON_V90D_PORTABLE_BULK", "1") != "0"
V34_SPEEDS_BY_INDEX = (0, 75, 110, 150, 300, 600, 1200, 2400,
                       4800, 7200, 9600, 12000, 14400, 16800,
                       19200, 21600, 24000, 26400, 28800, 31200,
                       33600)
V90D_PRESERVE_EXACT_UPSTREAM = (
    os.environ.get("EICON_V90D_PRESERVE_EXACT_UPSTREAM", "1") != "0")
# Page 1's V.22 overlay, and the one datagram width that is not negotiated.
# V.22bis is 2400 bit/s symmetric -- 600 baud carrying four bits -- so there is
# no DATASTATE word to read a width out of, unlike V.34 and V90D. Measured on a
# loopback V.22 call (Session 183): the page publishes every receive word as
# 0xf000, which is four bits left aligned in RXD0 under the existing
# oldest-bit-at-15 convention, and it never touches RXD1. 9,886 of them in one
# call, all identical, because nothing was feeding the transmit side.
V22_OVERLAY = 0x0266
V22_DATAGRAM_BITS = 4
V22_BIT_RATE = 2400
# 0x0266 is the "V.22/V.32 LEC" image and is resident for *both* modulations --
# the classifier picks page 1 or page 2 and the same overlay serves each
# (Session 184). So the overlay does not identify the modulation and the
# bootpage word does: every datagram-width test that keyed on `resident ==
# V22_OVERLAY` silently gave V.32 the V.22bis width of 4 bits, which is why the
# page reached TrnProgress 0x00d0 in Session 188e and still established no LAPM.
V22_BOOTPAGE = 0x0001
V32_BOOTPAGE = 0x0002
# V.32 and V.32bis are all 2400 baud -- unlike V.22bis, which is 600 baud with
# four bits a symbol, which is why the two pages need separate rate constants
# and the rate cannot be derived from the width. One V.32 datagram is
# rate/2400 bits: 4800->2, 7200->3, 9600->4, 12000->5, 14400->6. The card publishes no rate word
# this harness can find on page 2 -- DM(0x3F61)/DM(0x3F62) are the V.34 DATASTATE
# words and the V.32 page never writes them (watched over a whole call) -- so
# the width is set here rather than read, and is overridable so it can be swept
# against the only test that settles it, which is whether LAPM establishes.
V32_DATAGRAM_BITS = int(os.environ.get("EICON_V32_DATAGRAM_BITS", "6"), 0)
V32_BIT_RATE = V32_DATAGRAM_BITS * 2400
# Per-sample instruction allowance for pages that are not page 8, which has its
# own (V34_CYCLES_PER_SAMPLE). A run-to-idle page finishes its frame well inside
# the default; a page that does not is either wedged or under-served, and
# telling those apart needs the budget to be movable. Session 186.
ADSP_BUDGET = int(os.environ.get("EICON_ADSP_BUDGET", "20000"), 0)
# Deliver the per-frame continuation even when the budget expired with the core
# still in the page's foreground. adsp2181_modem_sample() otherwise injects it
# only out of IDLE, so a page whose frame does not fit is never dispatched
# again and stays in whatever routine it was suspended in -- Session 165's
# blocker, which is what V.34 does at 0x00b0 and V.32 does in its echo
# canceller. Off by default until it has been shown not to disturb the pages
# that do reach idle. Session 188.
# Value is a comma-separated list of overlay ids, or "1" for every page. Per
# page matters: V.8 spans budgets deliberately -- its FFT work is designed to be
# continued on the next exact SPORT frame with its context preserved -- and
# injecting the continuation underneath it drops the call to DIAL at 0.54 s.
_CONTINUE_ENV = os.environ.get("EICON_CONTINUE_NON_IDLE", "")
CONTINUE_NON_IDLE_ALL = _CONTINUE_ENV.strip() == "1"
CONTINUE_NON_IDLE_PAGES = frozenset(
    int(field, 0) for field in _CONTINUE_ENV.split(",")
    if field.strip() and field.strip() != "1")
# Serve a partial overlay at the instruction that requests it rather than at
# the end of the sample, by stopping the frame on the bootpage write. Hardware
# completes the transfer inside the frame; serving late let the V.32 page run
# on into its echo canceller with an unseeded workspace. Session 188e.
PARTIAL_STOP = os.environ.get("EICON_PARTIAL_STOP", "1") != "0"
# Bootpage 19 is the kernel's marker for a partial overlay rather than a page:
# the download named at DM(0x315D + 19) is loaded on top of the resident page,
# which keeps running. See _service_partial_overlay().
PARTIAL_BOOTPAGE = 0x0013
# Hold the six-word mapping-frame block across the resident kernel's per-frame
# clear; see the page-14 continuation site below. EICON_V90D_TX_BLOCK_HOLD=0
# restores the old behaviour (one downstream sample in six).
V90D_HOLD_TX_BLOCK = os.environ.get("EICON_V90D_TX_BLOCK_HOLD", "1") != "0"
# The same clear also runs on the V.34 page, where DM(0x3fa7..) is the source
# the resident copy at PM 0x1742 feeds into the transmit history, and PM 0x06cd
# zeroes it on roughly three frames in four against the producer at PM 0x374e
# filling it on the fourth. That looks like it should leave an impulse train in
# the history and it does not: suppressing the clear on 0x0261 leaves the
# transmitted signal byte-identical, so the copy must run ahead of the clear.
# Off by default, kept only so the A/B does not have to be rebuilt. Session 152
# -- do not re-derive.
V34_HOLD_TX_BLOCK = os.environ.get("EICON_V34_TX_BLOCK_HOLD", "0") != "0"
# Page 8 is a continuously-running foreground, so unlike run-to-idle pages its
# instruction allowance is a clock model. Keep it tunable while the fitted
# ADSP-2185N cadence is established against live Phase 3.
V34_CYCLES_PER_SAMPLE = int(os.environ.get("EICON_V34_CYCLES_PER_SAMPLE", "20000"), 0)
# Pace page 8 by its own transmit publish instead of by that budget. A fixed
# budget runs the transmit chain 9-12 times per 8 kHz sample (Session 149), and
# the line gets whichever of those DM(0x3764) happens to hold at the cut, so a
# real waveform reaches the peer decimated by ten and looks like white noise:
# 0.10 spectral concentration against 0.82 for a live modem on the same metric.
# Stopping the run at the publish makes it exactly one sample per tick, which
# is what a run-to-idle page gets from IDLE and what the SPORT interrupt gives
# the hardware. EICON_V34_PUBLISH_PACED=0 restores the fixed budget for A/Bs.
V34_PUBLISH_PACED = os.environ.get("EICON_V34_PUBLISH_PACED", "1") != "0"
# The same pacing, done by taking the first published value of the tick instead
# of halting the core at it. Stopping keeps the core out of IDLE, so
# adsp2181_modem_sample() skips its continuation and the kernel foreground
# starves -- measured at PM 0x02a9 344,933 unpaced against 39,910 stop-paced,
# with 45 M cycles going into a background wait task that never runs otherwise
# (Session 165). Latching gives the same one-sample-per-tick without touching
# execution flow. EICON_V34_PUBLISH_LATCH=0 disables, and the stop-based
# EICON_V34_PUBLISH_PACED=1 is kept for A/Bs against Session 149.
V34_PUBLISH_LATCH = os.environ.get("EICON_V34_PUBLISH_LATCH", "0") != "0"
# Run the kernel foreground continuation on a paced tick as well, then resume
# the page where the publish stopped it. EICON_V34_PUBLISH_YIELD=0 restores the
# Session 149 behaviour, where the continuation is skipped whenever the stop
# fires.
V34_PUBLISH_YIELD = os.environ.get("EICON_V34_PUBLISH_YIELD", "0") != "0"
# Publishes to let through before the stop fires. Session 169 predicted 3 would
# beat 1: the generator arms CNTR = 3, one symbol's polyphase set, and stopping
# at the first store abandons two phases of every symbol. Measured, 3 is worse
# -- it does raise output to 0.950 per sample against 0.624 and all but removes
# the quiet stretches, but the carrier goes 0.130 -> 0.073 and neither end
# advances past 0x0060/0x0064. Consuming one of three completed phases is worse
# than producing one. Default stays 1; the knob is kept for the A/B (170).
V34_PUBLISH_GROUP = int(os.environ.get("EICON_V34_PUBLISH_GROUP", "1"), 0)
# Ceiling on one publish-paced run, so a page that stops publishing cannot hang
# the media thread; it falls back to the fixed budget's behaviour for that tick.
V34_PUBLISH_MAX_CYCLES = int(
    os.environ.get("EICON_V34_PUBLISH_MAX_CYCLES", "20000"), 0)
# Per-address DM write census over page 8, written to this path as CSV at exit.
# The point is rate, not ownership: 3429 baud against 8 kHz is 3 symbols per 7
# samples, so a software symbol clock -- the only kind left after Session 173
# ruled out the hardware timer -- writes some word 0.4286 times per page-8
# sample. Nothing else in the page has a reason to run at that rate.
DM_CENSUS = os.environ.get("EICON_DM_CENSUS", "")
# Stop counting after this many page-8 samples, so two ends that hold page 8
# for very different spans can be compared on the same denominator. The two
# loopback ends differ by 5.5x on a 40 s run, and a rate averaged over a long
# inactive stretch is not the same measurement as one taken while the page
# works. 0 = no cap.
DM_CENSUS_SAMPLES = int(os.environ.get("EICON_DM_CENSUS_SAMPLES", "0"), 0)
# PM addresses whose execution count is printed alongside the census, as a
# comma-separated list.
#
# Counted on the same page-8 gate as the census, which is what makes the number
# mean anything: pages are downloaded into the same PM rather than selected by
# PMOVLAY, so an ungated count at PM 0x3768 sums the V.34 page's
# `DO $3792 UNTIL NOT CE`, the INFO page's `NOP, AY0 = DM(I1,M0)` and whatever
# V.8 keeps there. Session 169's generator loop rate was an ungated count.
# "LO:HI:PATH" -- snapshot live PM over [LO, HI) when the census is written.
# The overlay images on disk are not what is at a given PM address at run time:
# the boot sequence downloads several overlays over each other, so a region the
# V.34 image happens to fill may hold another page's content by the time page 8
# runs. Reading coefficients off the file is how Session 177's first attempt
# ended up disassembling a coefficient bank as instructions.
PM_DUMP = os.environ.get("EICON_PM_DUMP", "")
# The same thing for data memory, which had no equivalent at all. Session
# 188i needed to know whether DM(0x1a22..0x1aff) holds a record or an
# earlier page's leftovers, and the only DM snapshot in the harness is the
# end-of-call capture -- taken after the page has fallen back and the next
# overlay has loaded over the evidence. "LO:HI:PATH@OVERLAY", dumped when
# that overlay becomes resident.
DM_DUMP = os.environ.get("EICON_DM_DUMP", "")
# PM addresses to watch for *writes*, comma-separated. The core has had a
# watch_pm flag since it was imported and nothing ever read it, so every
# "nothing writes that PM address" in this log before Session 188 was an
# untested assumption rather than a measurement. Firing on value changes only,
# because a page that rewrites a word with what it already held is not the
# self-modification this is looking for.
WATCH_PM = tuple(int(field, 0)
                 for field in os.environ.get("EICON_WATCH_PM", "").split(",")
                 if field.strip())
# Hold PM words against the firmware's own stores: "ADDR=VALUE[,ADDR=VALUE]",
# VALUE a full 24-bit opcode. EICON_FORCE_DM writes at overlay-load time and so
# cannot reach a word the page rewrites afterwards -- Session 188l's PM 0x3805
# is patched 14,000 cycles after 0x0267 lands, by a fragment trampolined in from
# resident PM. The pin re-imposes the value after every store, which makes the
# counterfactual "that patch did not stick" rather than "the image shipped
# differently". Overlay loads and host writes bypass it, as they bypass the
# write watch (Session 186).
# Per-frame PC-stack depth trace to PATH, as CSV. An overflow warning says the
# stack reached 16 but not how: depth that climbs and stays up is frames pushed
# and never popped, depth that spikes and recovers is genuine interrupt nesting,
# and Session 188o cannot tell V.32's stack failure apart without seeing which.
PCSP_TRACE = os.environ.get("EICON_PCSP_TRACE", "")
# Full instruction trace, armed for whole 8 kHz frames by sample number. The
# core has had a trace budget since it was imported, but only as a count from
# wherever the run happened to be, which is useless for "what is different about
# frame 24412" -- Session 188p's stall onset. Naming frames lets the failing one
# be diffed against its own neighbours. ~4,000 lines per frame, so name few.
TRACE_FRAMES = frozenset(
    int(field, 0)
    for field in os.environ.get("EICON_TRACE_FRAMES", "").split(",")
    if field.strip())
TRACE_BUDGET = int(os.environ.get("EICON_TRACE_BUDGET", "8000"), 0)
# Only let the watches fire while this overlay is resident. A PM address is a
# different instruction on each page, so an ungated --watch-exec on a low
# address is spent by the boot and V.8 pages long before the page under test:
# Session 188l read "0x378e never executes in the V.32 window" and "0x3805 never
# executes in the V.32 window" off exactly that, and both were wrong. Unset
# means armed throughout, which is the old behaviour.
# Comma-separated, because a page and its partial are one page to the firmware
# but two residency values here: gating on 0x0266 alone disarms the moment the
# 0x0267 partial lands, 5,441 cycles in, and every later zero then means "not
# looking" rather than "did not happen". Name both.
WATCH_OVERLAY = tuple(int(field, 0)
                      for field in os.environ.get("EICON_WATCH_OVERLAY", "").split(",")
                      if field.strip())
PIN_PM = tuple(
    (int(field.split("=")[0], 0) & 0x3FFF, int(field.split("=")[1], 0) & 0xFFFFFF)
    for field in os.environ.get("EICON_PIN_PM", "").split(",")
    if field.strip())
PM_COVERAGE = tuple(int(field, 0)
                    for field in os.environ.get("EICON_PM_COVERAGE", "").split(",")
                    if field.strip())
FORCE_V34 = os.environ.get("EICON_FORCE_V34", "0") != "0"
# AT +IE-style modulation selection, run through the driver's own algorithm:
# "<mod>[,<automode>[,<min_rx>,<max_rx>,<min_tx>,<max_tx>]]". Overrides
# EICON_FORCE_V34. See modem_options().
MODULATION = os.environ.get("EICON_MODULATION", "")
# Downloads to stage on top of the card type's own file set, as a
# comma-separated list of ids. The protocol image decides a channel's
# capabilities by searching the staged table, so this is how a capability the
# PRI file set omits is offered at all: EICON_DSP_EXTRA_DOWNLOADS=0x026b
# stages the V.90 APCM overlay, which is what te_dmlt.pm looks for at
# 0x80091f9c before it will admit V.90A. See eicon_dsp_stage.py.
# --hook-call for the harnesses that build their own shim (SIP, replay,
# loopback): a comma-separated list of MIPS addresses to log entries to.
HOOK_CALL = os.environ.get("EICON_HOOK_CALL", "")


def _parse_force_dm(spec: str) -> "tuple[tuple[int, int, int | None], ...]":
    """`ADDR=VALUE[@OVERLAY]`, comma-separated.

    This is a firmware patch, not a diagnostic, and the difference matters:
    it overwrites a word the firmware owns, once per sample, for as long as
    the named overlay is resident. Nothing it produces is evidence about an
    unpatched card. It exists so that "X is zero and the code gated on X never
    runs" can be turned into "and here is what happens when it is not zero",
    which is the step from correlation to cause -- Session 138 could not take
    it because there was no way to write the word.

    Restricting to an overlay is the normal case: a DM address means different
    things on different pages, so an unrestricted force is almost always wider
    than the question being asked.
    """
    out = []
    for field in spec.split(","):
        field = field.strip()
        if not field:
            continue
        body, _, page = field.partition("@")
        address, _, value = body.partition("=")
        if not value:
            raise ValueError(f"EICON_FORCE_DM: {field!r} has no '=VALUE'")
        out.append((int(address, 0) & 0x3FFF, int(value, 0) & 0xFFFF,
                    int(page, 0) if page.strip() else None))
    return tuple(out)


# Overwrite DM words the firmware owns, once per sample. See _parse_force_dm.
FORCE_DM = _parse_force_dm(os.environ.get("EICON_FORCE_DM", ""))
DSP_EXTRA_DOWNLOADS = tuple(
    int(field, 0)
    for field in os.environ.get("EICON_DSP_EXTRA_DOWNLOADS", "").split(",")
    if field.strip())
# Let the card run its own V.42 instead of v42_lapm.LapmEndpoint: sends
# B2_V42 in the NL LLC and drops the DLC that disables it. Untried against
# hardware — handoff.md ranked step 4.
CARD_V42 = os.environ.get("EICON_CARD_V42", "0") != "0"
# EICON_V42_NL_DATA=1 carries the LAPM stream over the NL entity as N_DATA
# instead of the DSP's synchronous mailbox -- but only once an N_DATA
# indication has proved the bearer carries traffic, the same evidence the
# receive direction requires. EICON_V42_NL_DATA=force skips that check.
# LAPM keeps producing at line rate
# while a request is outstanding, so the bridge needs an elastic store between
# the two clocks; 64 kbit is about two seconds at V.34 rates, comfortably more
# than one request round trip and small enough that a stalled entity is
# reported rather than hidden.
NL_TX_ELASTIC_BITS = 64 * 1024
# Pack the V90D transmit datagram with bit 15 of each TXD word oldest, matching
# the receive mailbox, instead of the ADDSP guide's bit-0-oldest. See
# _next_tx_words(): the receive order is proven against live frames and the
# transmit order never has been.
V90D_TX_MSB_FIRST = os.environ.get("EICON_V90D_TX_MSB_FIRST", "0") != "0"
# EICON_TX_PATTERN=<text> replaces the LAPM stream with repeating 8N1
# start-stop characters at the negotiated datagram width.  A peer with error
# control off (a CX's AT\N0) still has a V.14 asynchronous-to-synchronous
# converter between the line bits and its DTE: feeding bare octets makes that
# converter discard apparent start/stop bits and cannot reproduce the text.
# The explicit framing makes the peer's raw DTE a direct end-to-end readout of
# the bit path. --tx-prbs cannot answer this -- random bits look like garbage
# either way.
TX_PATTERN = os.environ.get("EICON_TX_PATTERN", "").encode() or None


def _start_stop_pattern_bits(pattern: bytes) -> tuple[int, ...]:
    """Encode repeating-test text at the raw peer's V.14 8N1 boundary."""
    return tuple(bit
                 for value in pattern
                 for bit in (0, *(value >> index & 1 for index in range(8)), 1))


TX_PATTERN_BITS = (_start_stop_pattern_bits(TX_PATTERN)
                   if TX_PATTERN is not None else None)

# The SPORT vector at PM 0x00B5 is repointed at TIKRNL's selected-channel ISR
# for the duration of each sample and restored afterwards, so the private
# descriptor is modelled without permanently replacing a global kernel dispatch
# slot.  It is the only PM patch that runs on every sample of every call, and
# until Session 131 nothing had ever been measured without it -- which makes it
# a background every other result here was taken against rather than a
# controlled variable.  EICON_ISR_VECTOR_PATCH=0 leaves PM 0x00B5 alone so the
# firmware's own vector dispatches, which is what the card does.
ISR_VECTOR_PATCH = os.environ.get("EICON_ISR_VECTOR_PATCH", "1") != "0"
# JUMP 0x0586, the opcode the patch installs.
_ISR_VECTOR_OPCODE = 0x1C000F | (0x0586 << 4)


def install_isr_vector(pm):
    """Point the SPORT vector at the selected-channel ISR, unless disabled.

    Returns the word to hand back to `restore_isr_vector()`, or None when the
    patch is off, so the restore is a no-op rather than writing a value that
    was never displaced.
    """
    if not ISR_VECTOR_PATCH:
        return None
    saved = pm[0x00B5]
    pm[0x00B5] = _ISR_VECTOR_OPCODE
    return saved


def restore_isr_vector(pm, saved) -> None:
    # `is not None` rather than a truth test: a displaced vector of 0x000000 is
    # a legitimate word and must still be put back.
    if saved is not None:
        pm[0x00B5] = saved


# The resident 0258 TIKRNL task normally owns the data-pump TX mailbox.  When
# DI_control bit 15 is set it first puts mark fill in TXD0, then drains its own
# bearer bit store and writes TXD0, TXD1 and TXD2 immediately before dispatching
# the selected modem page.  The host-driven PRBS/V.42 diagnostics deliberately
# own that mailbox instead, so all five stores must be suppressed in that mode.
#
# The task is relocated when MIPS assigns a core: the stores are at PM
# 06d0/0732/0734/0738/0740 in the extracted task and
# 06d7/0739/073b/073f/0747 in the live build-117-926 core.  Match the exact
# relative instruction signature, not either absolute address.  Silently
# patching a different sequence would turn a diagnosed ownership conflict into
# firmware damage.
TIKRNL_TXD_STORE_SIGNATURE = (
    (0x00, 0x93F05A),  # DM(0x3F05) = AR, mark-fill path
    (0x62, 0x93F05F),  # DM(0x3F05) = SR1, short TXD0 path
    (0x64, 0x93F05F),  # DM(0x3F05) = SR1, long TXD0 path
    (0x68, 0x93F06F),  # DM(0x3F06) = SR1
    (0x70, 0x93F07F),  # DM(0x3F07) = SR1
)
# GEN_SETUP1 (write database +0x01) bit 3 picks the modulation role, ADDSP
# Table 15: 0x0484 answers, 0x048c calls. Selectable per instance so a
# loopback can put one emulated card on each side. EICON_MODEM_ROLE=calling.
GEN_SETUP1_ROLE = {"answer": 0x0484, "calling": 0x048C}
MODEM_ROLE = os.environ.get("EICON_MODEM_ROLE", "answer")
# When this instance takes the calling (originate) side of the handshake, the
# dial page parks at TrnProgress 0x0002 waiting for DM(0x0554) >= 0x10, the
# supervisory tone-detector result Sessions 95-96 traced: GEN_SETUP1=0x048c
# routes the dial page through PM 0x35d7, which proceeds only when a twelve-
# channel tone detector reports the line established. A PRI product never
# arms that detector -- there is no analogue line, so no dial tone or DTMF
# to listen for -- and the correlator state bank at DM(0x2fc0..0x2fd7) is
# never written, so the calling side stays inert and transmits nothing.
# On a real PRI the line is "connected" when Q.931 CONNECT arrives, not by
# listening for dial tone, so this harness publishes that signal directly:
# pin DM(0x0554) to 0x20 while the calling side is still parked at the dial
# page. It is the same class of intervention as the injected SETUP already
# in the tree, and like that one it is a harness "line connected" signal,
# not a fix: it starts transmission (TrnProgress -> 0x0051) but does not by
# itself request the V.8 overlay (Session 95). EICON_ORIGINATE_LINE_READY=0
# disables it for A/B against the inert caller.
ORIGINATE_LINE_READY = os.environ.get("EICON_ORIGINATE_LINE_READY", "1") != "0"
# Diagnostic: when the originate side reaches TrnProgress 0x0051 (training
# start) on the SIG overlay without the firmware requesting V.8, request V.8
# (0x025f) ourselves by writing the page-request words DM(0x3131)/DM(0x3132).
# The originate dial page never calls the kernel page-request routine
# (PM 0x0680) that the answerer's SIG overlay uses to load V.8 -- the
# legitimate path is an AT dial script that this loopback bypasses, so this
# stands in for it the same way the dial-tone pin stands in for the line. On
# by default for the calling role; EICON_ORIGINATE_V8=0 disables it.
ORIGINATE_V8 = os.environ.get("EICON_ORIGINATE_V8", "1") != "0"
# Publish the request instead of forging its result. PM 0x0680 is a scheduled
# kernel task, not a subroutine an overlay calls: it opens with CALL $0002 and
# ends JUMP $000A, and no overlay in the image references it. What an overlay
# does is publish -- the V.8 page's own request for INFO is PM 0x3760..0x3762,
# `AR = DM(0x0491); DM(0x3FB0) = AR` -- and the kernel picks it up and writes
# DM(0x3131)/DM(0x3132) from its page table at DM(0x315D + bootpage), which is
# PM 0x069a/0x069b and is where every one of the answerer's three page requests
# comes from.
#
# So writing DM(0x3131)/DM(0x3132) directly sets the kernel's *outputs* and
# skips everything else PM 0x0680 does -- which is why DM(0x3FB0) and NORM_L
# had to be added by hand afterwards, each after it had already cost a call its
# modulation (Session 180). This publishes DM(0x0491)/DM(0x3FB0) and lets the
# kernel post the request, so whatever else that task sets gets set too.
ORIGINATE_V8_KERNEL = os.environ.get("EICON_ORIGINATE_V8_KERNEL", "0") != "0"
# The modulation mask (write database NORM_L, +0x29 -> DM 0x3F09) the forced V.8
# entry above installs on the calling side. The dial page's own init leaves
# 0x3004 there, which is V.22 only, so without this the caller's V.8 offers a
# menu the answerer's 0xb13f cannot meet.
#
# Unset means "restore whatever the native driver's own WDB transaction carried"
# -- the CAI translation is where the modulation actually comes from, so a
# constant would make EICON_MODULATION invisible to the calling side's V.8. A
# hex value pins one instead. Empty disables the write and restores the pre-fix
# behaviour for A/B: the old code wrote 0x3F0D, a word nothing reads, so "not
# forced at all" is exactly what every session before this one measured.
# Hold the forced V.8 entry until this media sample. The V.8 page's answer-tone
# deadline is a timer counted from its own entry, so moving the entry moves the
# deadline: it is the one knob that can be pushed against the answerer's ANSam
# phase reversal, which is what the deadline is racing (Session 182). 0 keeps
# the entry at the earliest sample the gates allow, which is what every session
# before 182 measured.
ORIGINATE_V8_AFTER = int(os.environ.get("EICON_ORIGINATE_V8_AFTER", "0"), 0)
_ORIGINATE_NORM_L_ENV = os.environ.get("EICON_ORIGINATE_NORM_L")
ORIGINATE_NORM_L: "str | int | None" = "native"
if _ORIGINATE_NORM_L_ENV is not None:
    ORIGINATE_NORM_L = (int(_ORIGINATE_NORM_L_ENV, 0) & 0xFFFF
                        if _ORIGINATE_NORM_L_ENV.strip() else None)
# Diagnostic: the INFO page publishes DM(0x3F89) = 0 because word 0 of the
# received message packs to 0x2000 (Sessions 102-104), and PM 0x2ef1 turns that
# zero into a branch that parks the V.34 originate script at state 0x0060 for
# the life of the page. Supplying the field says whether that branch is the
# only thing in the way. Off by default -- this is a probe, not a fix, and
# nothing here reproduces what the field should actually contain.
# EICON_ORIGINATE_V34_INFO=derived takes it from the payload word DM(0x060B);
# a number pins that literal 7-bit value instead.
ORIGINATE_V34_INFO = os.environ.get("EICON_ORIGINATE_V34_INFO", "")
# V.42 7.2.1 detection phase. On by default: without it the answerer starts on
# HDLC flags, the originator never receives an ADP, and it falls back to
# non-error-correcting mode (Courier "Protocol NONE", Session 86).
# EICON_V42_DETECT=0 restores the flags-immediately behaviour.
V42_DETECT = os.environ.get("EICON_V42_DETECT", "1") != "0"

# LAPM's poll and retransmit counters advance once per take(), and take() runs
# once per *payload datagram* -- 8000/6 Hz on V.90, about 1,333 a second. The
# LapmEndpoint defaults of 24 and 48 are therefore 18 ms and 36 ms on this
# path, which is far inside its own round trip: a 20 ms media quantum at each
# end, an RTP jitter buffer, the modem pair, and the peer's turnaround. Every
# stalled window was probed and then gone-back-N long before an acknowledgement
# could physically arrive, so the first lost frame on a call started a
# retransmit storm that was itself the main source of further loss -- 40,363
# retransmissions for 100 frames sent on one live call, against 63 on a clean
# one in the same run. That is the difference between "perfect" and "really
# glitchy". V.42 puts T401 at about a second; these are that, in datagrams.
V90_DATAGRAMS_PER_SECOND = 8000 / 6


def _v42_ticks(name: str, seconds: float) -> int:
    return max(1, round(V90_DATAGRAMS_PER_SECOND
                        * float(os.environ.get(name, seconds))))


# EICON_V42_POLL_S / EICON_V42_T401_S take seconds, so an A/B does not have to
# do the datagram arithmetic.
V42_POLL_AFTER = _v42_ticks("EICON_V42_POLL_S", 0.5)
V42_RETRANSMIT_AFTER = _v42_ticks("EICON_V42_T401_S", 1.0)
# N400 consecutive failed recoveries drops the link. Three is the V.42 default
# and, with T401 finally at a second, it means three seconds of no progress
# whatsoever -- a link that really is gone. Raise it if a lossy call that would
# have recovered is being cut off instead.
V42_N400 = max(1, int(os.environ.get("EICON_V42_N400", "3")))
# The experimental V.42 path historically used PRBS while the DSP was still
# training (before it published a negotiated datagram size).  That is useful
# for diagnostics, but sounds like random payload on a real modem.  Disable it
# with EICON_V42_TRAINING_PRBS=0; V.42 then transmits mark fill until the rate
# is available. Set it to 1 to restore PRBS for testing.
V42_TRAINING_PRBS = os.environ.get("EICON_V42_TRAINING_PRBS", "0") != "0"
# Supervisor passes run at attachment purely to make Unicorn translate the
# media-phase mainloop path before the sample clock starts; see
# NativeMipsModem.warm_up. EICON_MIPS_WARMUP=0 restores the old behaviour, which
# pays about 93 ms on the first media tick instead. Non-zero values shift the
# replay timeline by one sample, so A/B against a capture with this pinned.
MIPS_WARMUP_PASSES = int(os.environ.get("EICON_MIPS_WARMUP", "3"), 0)
# Extra echo bulk delay, in 8 kHz sample pairs, added to the firmware's own
# seed.  The card measures its own round trip into DM(0x3fc9), but that
# measurement is made over whatever path the media actually takes, and a SIP
# leg adds packetisation and jitter-buffer delay the card never sees as a
# separate term.  8 pairs is 1 ms.  Default 0: the measurement is believed to
# already include the path, and this exists to tune that belief against
# hardware rather than to assume a correction.
BULK_DELAY_EXTRA_PAIRS = int(
    os.environ.get("EICON_BULK_DELAY_EXTRA_PAIRS", "0"), 0)
# EICON_BULK_DELAY_SEED=0 restores the unseeded behaviour for A/B.
BULK_DELAY_SEED = os.environ.get("EICON_BULK_DELAY_SEED", "1") != "0"
# Whether to add DM(0x3fcb) to the seed the way PM 0x323a does.  Off by
# default, because on this harness it is not an echo delay.  Cross-correlating
# the captured TX against the captured RX puts the real echo at 41-100 sample
# pairs (5.1-12.5 ms) on every capture measured, against a noise floor 35x
# below it, while DM(0x3fcb) reaches 490-540 pairs (61-68 ms).  The replay
# tool's own note explains why: DM(0x3fc9), which DM(0x3fcb) is 10/3 of, is an
# elapsed-time counter the INFO page maintains at PM 0x3caf/0x3cb4, and
# whatever it has reached at the handoff lands in everything page 14 derives
# from it.  The bare floor, 0x25 + delaycorrection = 49 pairs = 6.1 ms, matches
# the measurement.  Set EICON_BULK_DELAY_MEASURED=1 on a path with a genuine
# long echo tail, and check it with tools/echo_delay.py first.
BULK_DELAY_MEASURED = os.environ.get("EICON_BULK_DELAY_MEASURED", "0") != "0"
# PM 0x3232..0x3243 and PM 0x1085/0x1086, the firmware's own seeder, verbatim.
BULK_SEED_BASE = 0x0025          # PM 0x3233
BULK_SEED_SPAN = 0x0050          # PM 0x323c, Nearbulklength -> BulkLength
BULK_SEED_CEILING = 0x0B00       # PM 0x323f/0x3241
# PM 0x1a11/0x1a16 subtract this from each length every frame, and PM
# 0x19e2/0x19e4 restore the pre-decrement value from the saved context at
# DM(0x3608)/DM(0x3609).  A held length must therefore tolerate being observed
# one decrement low.
BULK_LENGTH_DECREMENT = 0x0020
# Consecutive frames a coherent firmware pair must survive before the host
# seed stands down for it.  Two mapping frames.
BULK_SEED_YIELD_FRAMES = 12
# Diagnostic: never stand down, so the host value governs the data phase as
# well as training.  The firmware's own steady-state choice is 439/519 pairs
# (55/65 ms) while tools/echo_delay.py measures the echo at 5-12 ms, so this
# is how to ask whether the delay setting moves the quality metric at all.
BULK_DELAY_HOLD_ALWAYS = os.environ.get(
    "EICON_BULK_DELAY_HOLD_ALWAYS", "0") != "0"


def bulk_delay_seed(dm) -> tuple[int, int] | None:
    """Recompute the firmware's own echo bulk-delay seed.

    PM 0x3232 is the only site that turns the measured round-trip delay into
    delay-line lengths:

        3232: AX0 = DM(0x3F04)      delaycorrection, write-DB +0x24
        3233: AY0 = 0x0025
        3234: AR  = AX0 + AY0
        3235: DM(0x3FBC) = AR       Nearbulklength
        3236: AR = DM(0x3FCB)
        3238: IF LE JUMP 0x323C     skip when no delay has been measured yet
        323a: AR = AR + DM(0x3FBC)
        323b: DM(0x3FBC) = AR
        323c: DM(0x0A5D) = min(Nearbulklength + 0x50, 0x0B00)
        3243 -> 1086: DM(0x3FBD) = DM(0x0A5D)   BulkLength

    It runs twice, both times on page 0x0260, and on this harness both firings
    land about 1.5 s before DM(0x3FCB) first becomes positive -- so the `IF LE`
    branch is taken, PM 0x1085/0x1086 never executes at all, and page 14 runs
    its whole residency with both lengths at zero.

    The addend is deliberately not applied by default; see
    BULK_DELAY_MEASURED.  The floor alone is 49 near / 129 far pairs, 6.1 and
    16.1 ms, which brackets every echo delay measured on this path.
    """
    near = BULK_SEED_BASE + (int(dm[0x3F04]) & 0xFFFF) + BULK_DELAY_EXTRA_PAIRS
    if BULK_DELAY_MEASURED:
        addend = int(dm[0x3FCB]) & 0xFFFF
        if not 0 < addend < 0x8000:        # PM 0x3237/0x3238 test AR > 0
            return None
        near += addend
    if near < 1:
        return None
    far = min(near + BULK_SEED_SPAN, BULK_SEED_CEILING)
    near = min(near, BULK_SEED_CEILING)
    return near, far


def publish_bulk_lower_limit(dm) -> int:
    """Publish the common-layer lower limit for the selected bulk descriptor."""
    base = int(dm[0x32F7]) & 0x3FFF
    address = (base + 5) & 0x3FFF
    dm[address] = BULK_DESCRIPTOR_LOWER_LIMIT
    return address


class PortableBulkDelay:
    """Bounded 8 kHz implementation of the ADDSP near/far bulk contract."""

    def __init__(self) -> None:
        self._lengths: tuple[int, int] | None = None
        self._pairs: collections.deque[tuple[int, int]] = collections.deque()

    def reset(self) -> None:
        self._lengths = None
        self._pairs.clear()

    def service(self, dm) -> bool:
        """Insert one X/Y pair and publish the two delayed output pairs.

        The database base is DM 0x3ee0 and an offset maps straight onto it.
        That is the only base consistent with every mapping already proved
        against the firmware: write-DB 0x24 is delaycorrection at DM(0x3f04),
        read-DB 0x81/0x82 are the rate words at DM(0x3f61)/DM(0x3f62), and
        0xdc..0xdf are the lengths and inputs at DM(0x3fbc..0x3fbf).  So the
        near and far output pairs at 0x56..0x59 are DM(0x3f36..0x3f39).

        Session 111 used base 0x3f60 for this group alone and landed on
        DM(0x3fb6..0x3fb9).  DM(0x3fb8) is not an output: PM 0x19f3/0x19f4 do
        `I4 = DM(0x3FB8); CALL (I4)` every frame, and the firmware holds 0x3cea
        there -- code that sets the DM(0x3fc1) 0x0400 worker-enable bit and
        jumps to the generator dispatch at 0x2a56.  Writing a sample over it
        called the page into garbage, which stopped the generator, left TX
        datagrams at 0/0 and parked the outer state at 0x0050 for the whole
        call.  PM 0x19e7/0x19e8 (`DM(0x3F36) = DM(0x3F38)`) context-switch the
        real pair, exactly as PM 0x19e2/0x19e4 do for the lengths.

        A length is a count of sample *pairs*, not words.
        """
        near = int(dm[0x3FBC]) & 0xFFFF
        bulk = int(dm[0x3FBD]) & 0xFFFF
        # The physical delay RAM is in the ADSP's 14-bit DM domain.  Refuse
        # zero, signed/negative, reversed, or impossibly large descriptors.
        if not (0 < near <= bulk <= 0x2000):
            self.reset()
            dm[0x3F36] = dm[0x3F37] = 0
            dm[0x3F38] = dm[0x3F39] = 0
            return False

        lengths = (near, bulk)
        if lengths != self._lengths:
            self._lengths = lengths
            self._pairs = collections.deque(
                ((0, 0) for _ in range(bulk)), maxlen=bulk)

        # Read before append: index -near is exactly the pair inserted `near`
        # clocks ago, while index 0 is the oldest (`bulk` clocks ago).
        near_pair = self._pairs[-near]
        far_pair = self._pairs[0]
        self._pairs.append((int(dm[0x3FBE]) & 0xFFFF,
                            int(dm[0x3FBF]) & 0xFFFF))
        dm[0x3F36], dm[0x3F37] = near_pair
        dm[0x3F38], dm[0x3F39] = far_pair
        return True


def v90d_bulk_adapter_parameters(dm) -> tuple[int, int] | None:
    """Return a coherent, hardware-qualified V90D rate/count pair."""
    # Read-DB 0x81 is the V90D transmitter (PCM downstream); 0x82 is the
    # unrelated V.34 upstream rate and must not release the downstream worker.
    rate = int(dm[0x3F61])
    count = int(dm[0x1E4F])
    encoded_count = 21 + (rate & 0x001F)
    if ((rate & V90_SPEED_FORMAT_MASK) != V90_SPEED_FORMAT_MASK
            or count != encoded_count
            or count not in V90D_QUALIFIED_BULK_WIDTHS):
        return None
    return rate, count


def v90_downstream_rate(speed_word: int) -> int | None:
    """Decode ADDSP read-DB 0x81 for a digital-side V.90 transmitter."""
    if ((speed_word & V90_SPEED_FORMAT_MASK)
            != V90_SPEED_FORMAT_MASK):
        return None
    bits_per_datagram = 21 + (speed_word & 0x001F)
    # V90D transfers 8000/6 datagrams/s. Round the repeating-third rates to
    # the integer convention used by modem CONNECT reports.
    return (bits_per_datagram * 8000 + 3) // 6


def v34_rate(speed_word: int, format_mask: int = 0x2000) -> int | None:
    """Decode a V.34 DATASTATE speed-number field from ADDSP read DB 0x82."""
    if speed_word & format_mask:
        return None
    index = speed_word & 0x001F
    if index >= len(V34_SPEEDS_BY_INDEX):
        return None
    rate = V34_SPEEDS_BY_INDEX[index]
    return rate if rate >= 2400 and rate % 2400 == 0 else None


def v90d_negotiated_rates(dm) -> tuple[int | None, int | None]:
    """Return (downstream, upstream) rates from ADDSP read DB 0x81/0x82.

    In the digital V90D role the modem transmitter is the PCM downstream and
    the modem receiver is the analogue V.34 upstream.
    """
    return (v90_downstream_rate(int(dm[0x3F61])),
            v34_rate(int(dm[0x3F62])))


def v90d_upstream_rate_bit(speed_word: int) -> int | None:
    """Return the V.34 capability bit represented by a V90D rate word."""
    if ((speed_word & 0xFFE0) != 0x11E0
            or v34_rate(speed_word) is None):
        return None
    bit = (speed_word & 0x001F) - 7
    return 1 << bit if bit >= 0 else None


def v90d_upstream_handoff(dm, speed_word: int) -> tuple[int, int, int] | None:
    """Capture the complete firmware setup for a genuinely selected rate."""
    rate_bit = v90d_upstream_rate_bit(speed_word)
    bit_number = (speed_word & 0x001F) - 7
    if (rate_bit is None
            or not (int(dm[0x1E3F]) & rate_bit)
            or not (int(dm[0x210B]) & rate_bit)
            or int(dm[0x3F9B]) != bit_number
            or int(dm[0x204E]) != 3 * bit_number):
        return None
    return speed_word, int(dm[0x3F9B]), int(dm[0x204E])


def v90d_exact_upstream_fallback(dm, speed_word: int,
                                 handoff: tuple[int, int, int] | None
                                 ) -> tuple[int, int, int] | None:
    """Recognize the final no-common-rate fallback after an exact selection.

    The firmware can publish the exact peer rate early, reload V90D, then let
    its transient quality ceiling exclude the sole allowed bit at the final
    handoff.  Retaining all three rate-derived words bridges that handoff; the
    subsequent data phase remains the arbiter of whether the rate is usable.
    """
    if speed_word != 0x11E0 or handoff is None:
        return None
    selected_word, speed_number, datagram_parameter = handoff
    rate_bit = v90d_upstream_rate_bit(selected_word)
    if rate_bit is None or int(dm[0x1E3F]) != rate_bit:
        return None
    bit_number = (selected_word & 0x001F) - 7
    if (not (int(dm[0x210B]) & rate_bit)
            or int(dm[0x20BA]) > bit_number):
        return None
    return selected_word, speed_number, datagram_parameter


def v90d_exact_upstream_ceiling_floor(dm) -> int | None:
    """Return the inclusive-mask length needed by a sole exact peer rate.

    PM 0x316a builds a low-bits mask from DM(0x20ba).  When the peer offers
    exactly one locally supported rate above that transient limit, lifting the
    mask length lets the native firmware perform its complete receiver setup.
    """
    try:
        peer_mask = int(dm[0x1E3F])
        local_mask = int(dm[0x210B])
        ceiling = int(dm[0x20BA])
    except (IndexError, KeyError):
        return None
    if peer_mask == 0 or peer_mask & (peer_mask - 1):
        return None
    bit_number = peer_mask.bit_length() - 1
    speed_word = 0x11E0 | (bit_number + 7)
    if (v90d_upstream_rate_bit(speed_word) != peer_mask
            or not (local_mask & peer_mask)
            or ceiling > bit_number):
        return None
    return bit_number + 1


def _parse_wdb_override(text: str) -> dict[int, int]:
    """Parse EICON_WDB_OVERRIDE, e.g. "0x04:0x6000,0x7b:0x03b7"."""
    result: dict[int, int] = {}
    for pair in text.split(","):
        if not pair.strip():
            continue
        offset, _, value = pair.partition(":")
        result[int(offset, 0) & 0x7F] = int(value, 0) & 0xFFFF
    return result


# Words forced into the answer-cycle write database on top of the driver's
# native transaction. Empty by default: Session 75 established that the native
# CAI-to-WDB translation, not the ADDSP handbook table, is what the real driver
# produces, and overriding it reintroduces a hand-built confounder.
#
# It exists because the native WDB and the handbook disagree on documented
# capability fields, and `V8_SETUP +0x04` is the sharpest case: the handbook
# value `0x6000` is the V90_DPCM and digital-network enable, and the native WDB
# leaves it `0x0000` for the whole call (Session 82). An open-loop replay cannot
# settle whether that matters, because the recorded peer audio already contains
# a V.90-accepting response no matter what the card offered. Only a live call
# can. Suggested A/B:
#
#     EICON_WDB_OVERRIDE=0x04:0x6000
#
# Other documented-vs-native disagreements, for reference: INFO0_SETUP +0x07
# f0fd/f1fd, NORM_L +0x29 8100/a13f, SPEED_SEL_L +0x2b ff00/fffe,
# INFO0D_SETUP +0x7b 03b7/0377.
WDB_OVERRIDE = _parse_wdb_override(os.environ.get("EICON_WDB_OVERRIDE", ""))

HOST_WRITE = BIAS + 0x71950  # 0x80082950
HOST_READ = BIAS + 0x71920   # 0x80082920
HOST_WRITE_DM_BLOCK = BIAS + 0x71A38  # 0x80082a38
HOST_WRITE_PM_BLOCK = BIAS + 0x71B8C  # 0x80082b8c
SCRIPT_SENDER = BIAS + 0x786A4  # 0x800896a4
REQUEST_PARSER = BIAS + 0x78138  # 0x80089138
# Service-driver table slot 1: the modem service assign entry (file 0x85980).
# Performs the switch-on database commit for task 0x0258 (TIKRNL81.F34),
# reached through the table at file 0xeaec4 rather than by a direct jal.
SERVICE_ASSIGN = BIAS + 0x85980  # 0x80096980
SWITCH_ON = BIAS + 0x7FE58       # 0x80090e58, publish initial task command
DSP_DOWNLOAD = BIAS + 0x75AF8    # 0x80086af8, native block/relocation loader
# 0x800951d4 publishes the connected driver's control toggle; PRI clocks before
# this call cannot service a command that is not yet live.
CONNECTED_DRIVER = BIAS + 0x841D4  # 0x800951d4

# Every MIPS address the shim intercepts or counts. Each gets its own
# single-address UC_HOOK_CODE rather than one hook over all code: Unicorn only
# instruments translation blocks that overlap a hook's range, so a global code
# hook makes every instruction a Python call. Measured on a 20 s replay that was
# 8.5 ms of the 20 ms media budget for ~12k instructions -- about 700 ns per
# instruction, against roughly 20 ns uninstrumented.
INTERCEPT_ADDRESSES = (HOST_WRITE, HOST_READ, HOST_WRITE_DM_BLOCK,
                       HOST_WRITE_PM_BLOCK, SERVICE_ASSIGN, SWITCH_ON,
                       CONNECTED_DRIVER, STUB_VIRT)
# Recent block addresses kept for fault diagnostics. The trace was previously
# every executed instruction, unbounded: 17.5 million entries and 800 MB of RSS
# over a 20 s call, which is memory pressure on the real-time media thread.
TRACE_RING = 256

# The MIPS image's .data/.bss for the protocol task lives at 0x80200000
# (physical 0x200000).  The te_dmlt.pm file only covers 0x11000..0x100230,
# so this segment is zero-initialized; map it writable and auto-map any
# neighbouring .bss page the firmware touches during assign.
DATA_VIRT = 0x80200000
DATA_BASE = 0x200000
DATA_SIZE = 0x100000
# The protocol image's real runtime stack/heap (sp = 0x80338700, set at the
# image entry) and the database-record buffers (e.g. 0x80331c12) live in the
# 0x80300000 segment.  Map it writable; the auto-map hook covers neighbours.
STACK_VIRT = 0x80300000
STACK_BASE = 0x300000
STACK_SIZE = 0x100000
STACK_TOP = 0x80338700  # sp value set at the image entry (file 0x477c..0x4788)
AUTO_PAGE = 0x10000

# Shared RAM (PR_RAM) lives at physical 0x1000 in DRAM (MP_SHARED_RAM_OFFSET).
# The MIPS accesses it via the uncached segment 0xa0001000.  The boot/config
# area (struct mp_load) is at physical 0x0.  Map the full 0x0..0x11000 range
# (shared RAM + boot area); the protocol image at 0x11000 is mapped separately.
SHARED_RAM_BASE = 0x00000
SHARED_RAM_SIZE = 0x11000
PR_RAM_PHYS = 0x1000       # physical base of PR_RAM
PR_RAM_VIRT = 0xa0001000   # MIPS uncached address (stored at gp+0x5e93)

# MIPS firmware entry points for the request-queue path.
MIPS_INIT = BIAS + 0x72130     # 0x80083130: store PR_RAM base, clear 0x4d20 bytes
MIPS_MAINLOOP = BIAS + 0x16970  # 0x80027970: poll PR_RAM, dispatch requests
# The real firmware entry point (boot loader jumps here after copying config
# and clearing .bss).  Reads card config from shared RAM, initialises DSP
# resources, waits for boot->cmd==3, then enters the main loop.
MIPS_ENTRY = BIAS + 0x71f90     # 0x80082f90

# PR_RAM structure (from kernel/pr_pc.h).  Offsets within the PR_RAM region.
PR_NextReq = 0x00   # word: host write pointer (offset into B[])
PR_NextRc = 0x02    # word: MIPS response write pointer
PR_NextInd = 0x04   # word: MIPS indication write pointer
PR_ReqInput = 0x06  # byte: count of requests submitted by host
PR_ReqOutput = 0x07 # byte: count of requests processed by MIPS
PR_Int = 0x09       # byte: interrupt flag
PR_ReqReserved = 0x08 # byte: Req buffers reserved
PR_XLock = 0x0a     # byte: arbitration lock
PR_RcOutput = 0x0b  # byte: count of RC buffers the MIPS has returned
PR_IndOutput = 0x0c # byte: count of IND buffers the MIPS has returned
PR_IMask = 0x0d     # byte: interrupt mask flag
PR_ReadyInt = 0x10  # byte: host pokes this to request a ready interrupt
PR_Signature = 0x1e # word: MIPS writes 0x5858 (not ready) or valid sig
PR_B = 0x20         # start of the REQ/RC/IND buffer area

# RC structure (kernel/pr_pc.h): next(2) Rc(1) RcId(1) RcCh(1) Res(1) Ref(2)
RC_RC = 0x02
RC_RCID = 0x03
RC_RCCH = 0x04
RC_REFERENCE = 0x06
IND_IND = 0x02
IND_ID = 0x03
IND_CH = 0x04
IND_REFERENCE = 0x08
IND_RBUFFER = 0x10
IND_RDATA = 0x12
# Return codes (kernel/pc.h), from eicon_idi so there is one copy.
ASSIGN_RC = eicon_idi.ASSIGN_RC   # ASSIGN acknowledgement class
ASSIGN_OK = eicon_idi.ASSIGN_OK   # ASSIGN succeeded
RC_OK = eicon_idi.RC_OK           # command accepted

# REQ structure (from kernel/pr_pc.h).  Each REQ buffer in B[].
REQ_SIZE = 0x120    # next(2)+Req(1)+ReqId(1)+ReqCh(1)+Res1(1)+Ref(2)+Res[8]+XBuffer(2+270)
REQ_NEXT = 0x00     # word: offset of next free REQ in B[]
REQ_REQ = 0x02      # byte: request code (ASSIGN=0x01, etc.)
REQ_REQID = 0x03    # byte: global entity id (DSIG_ID, NL_ID, ...)
REQ_REQCH = 0x04    # byte: channel number
REQ_REFERENCE = 0x06  # word: host cookie (0 = signalling, 1 = network)
REQ_XBUFFER = 0x10  # PBUFFER: word length + byte[270] data
REQ_XDATA = 0x12    # start of the 270-byte data payload

# IDI request codes and global entity ids (kernel/pc.h), from eicon_idi.
ASSIGN = eicon_idi.ASSIGN
LISTEN_REQ = eicon_idi.LISTEN_REQ
N_CONNECT = eicon_idi.N_CONNECT
INDICATE_REQ = eicon_idi.INDICATE_REQ
CALL_RES = eicon_idi.CALL_RES
DSIG_ID = eicon_idi.DSIG_ID   # D-channel signalling
NL_ID = eicon_idi.NL_ID       # network-layer access (B or D channel)
BLLC_ID = eicon_idi.BLLC_ID   # B-channel link level access
TASK_ID = eicon_idi.TASK_ID   # dynamic user tasks
MAN_ID = eicon_idi.MAN_ID     # management
REMOVE = eicon_idi.REMOVE

# Signalling-controller object vector used by the common IDI dispatcher.
# gp+0x5eb9 holds the count; entries are firmware runtime pointers and must
# be accessed through Unicorn's physical mirror.
ENTITY_TABLE = 0x80299928
# Complete incoming signalling-message parser.  Do not confuse this with
# 0x800172a8: that address is only the delay slot of a jal to the IE-copy
# helper at the end of the preceding function.
CALL_INGRESS_PARSER = 0x800172c0
SYNTH_CALL_OBJECT = RAM_VIRT + 0x7000
SYNTH_INGRESS_MESSAGE = RAM_VIRT + 0x7800

# DSP CAI modem hardware types (kernel/mdm_msg.h).  add_b1()'s resource[]
# table maps B1 protocol 7/8 (MODEM_ALL_NEGOTIATE / MODEM_ASYNC) to 17 and
# protocol 9 (MODEM_SYNC_HDLC) to 18, so a modem call's B1 resource is one
# of these.  These and the IE codes below now live in eicon_idi; the aliases
# stay so the rest of this file and the replay harnesses read unchanged.
DSP_CAI_HARDWARE_MODEM_ASYNC = eicon_idi.DSP_CAI_HARDWARE_MODEM_ASYNC
DSP_CAI_HARDWARE_MODEM_SYNC = eicon_idi.DSP_CAI_HARDWARE_MODEM_SYNC

# IDI parameter codes (kernel/pc.h).
IDI_BC = 0x04     # bearer capability
IDI_CAI = eicon_idi.IDI_CAI
IDI_LLI = eicon_idi.IDI_LLI
IDI_DLC = eicon_idi.IDI_DLC
IDI_UID = eicon_idi.IDI_UID
IDI_LLC = eicon_idi.IDI_LLC

# DLC modem protocol negotiation flags (kernel/mdm_msg.h).
DLC_MODEMPROT_DISABLE_V42_V42BIS = eicon_idi.DLC_MODEMPROT_DISABLE_V42_V42BIS
DLC_MODEMPROT_DISABLE_MNP_MNP5 = eicon_idi.DLC_MODEMPROT_DISABLE_MNP_MNP5
DLC_MODEMPROT_REQUIRE_PROTOCOL = eicon_idi.DLC_MODEMPROT_REQUIRE_PROTOCOL
DLC_MODEMPROT_DISABLE_V42_DETECT = eicon_idi.DLC_MODEMPROT_DISABLE_V42_DETECT
DLC_MODEMPROT_DISABLE_COMPRESSION = eicon_idi.DLC_MODEMPROT_DISABLE_COMPRESSION
DLC_MODEMPROT_DISABLE_SDLC = eicon_idi.DLC_MODEMPROT_DISABLE_SDLC

# The protocol image sets $gp = 0x8010.0000 - 0x5c4b = 0x800fa3b5 at its entry
# (file 0x4774/0x4764c).  All gp-relative globals live off this value: the
# trace-printf pointer is at gp+0x1a7b (0x800fbe30, file-backed = 0x80083180)
# and the service-driver table at gp+0x1b0f (0x800fbec4).  Using any other gp
# leaves the printf pointer NULL and jalr faults on the first trace call.
GP = 0x800fa3b5

# ---------------------------------------------------------------- ADSP side

# ADSP2181_LIB overrides the emulator library, e.g. to load the
# AddressSanitizer build (make -C tools/adsp2181emu libadsp2181_asan.dylib).
ADSP = ctypes.CDLL(os.environ.get(
    "ADSP2181_LIB",
    str(Path(__file__).resolve().parent / "adsp2181emu" / "libadsp2181.dylib")))
ADSP.adsp2181_create.restype = ctypes.c_void_p
ADSP.adsp2181_destroy.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_reset.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_pm.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_pm.restype = ctypes.POINTER(ctypes.c_uint32)
ADSP.adsp2181_dm.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_dm.restype = ctypes.POINTER(ctypes.c_uint16)
ADSP.adsp2181_idle.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_pc.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_pc.restype = ctypes.c_uint16
ADSP.adsp2181_call.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16]
ADSP.adsp2181_run.argtypes = [ctypes.c_void_p, ctypes.c_int]
ADSP.adsp2181_set_irq.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
ADSP.adsp2181_host_write.argtypes = [ctypes.c_void_p, ctypes.c_uint16,
                                     ctypes.c_uint16]
ADSP.adsp2181_host_read.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
ADSP.adsp2181_host_read.restype = ctypes.c_uint16
ADSP.adsp2181_idle.restype = ctypes.c_int
ADSP.adsp2181_idma_addr_write.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
ADSP.adsp2181_idma_data_write.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
ADSP.adsp2181_idma_data_read.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_idma_data_read.restype = ctypes.c_uint16
ADSP.adsp2181_set_idma_boot_hold.argtypes = [ctypes.c_void_p, ctypes.c_int]
ADSP.adsp2181_idma_boot_held.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_idma_boot_held.restype = ctypes.c_int
ADSP.adsp2181_watch_dm.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_int]
ADSP.adsp2181_watch_pm.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_int]
ADSP.adsp2181_pin_pm.argtypes = [ctypes.c_void_p, ctypes.c_uint16,
                                 ctypes.c_uint32, ctypes.c_int]
ADSP.adsp2181_pin_pm_hits.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
ADSP.adsp2181_pin_pm_hits.restype = ctypes.c_uint32
ADSP.adsp2181_pcsp_window.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_pcsp_window.restype = ctypes.c_uint32
ADSP.adsp2181_watch_gate.argtypes = [ctypes.c_void_p, ctypes.c_int]
ADSP.adsp2181_cycles.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_cycles.restype = ctypes.c_uint64
ADSP.adsp2181_watch_exec.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_int]
ADSP.adsp2181_watch_exec_limited.argtypes = [ctypes.c_void_p, ctypes.c_uint16,
                                             ctypes.c_uint32]
ADSP.adsp2181_watch_dm_limited.argtypes = [ctypes.c_void_p, ctypes.c_uint16,
                                           ctypes.c_uint32]
ADSP.adsp2181_watch_dm_writes.argtypes = [ctypes.c_void_p, ctypes.c_uint16,
                                          ctypes.c_uint32]
ADSP.adsp2181_sport0_tx_written.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_sport0_tx_written.restype = ctypes.c_int
ADSP.adsp2181_dm_census.argtypes = [ctypes.c_void_p, ctypes.c_int]
ADSP.adsp2181_dm_census_clear.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_dm_census_count.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
ADSP.adsp2181_dm_census_count.restype = ctypes.c_uint64
ADSP.adsp2181_stop_on_dm_write.argtypes = [ctypes.c_void_p, ctypes.c_uint16,
                                           ctypes.c_int]
ADSP.adsp2181_stop_dm_hit.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_stop_dm_hit.restype = ctypes.c_int
ADSP.adsp2181_stop_on_dm_write_n.argtypes = [ctypes.c_void_p, ctypes.c_uint16,
                                             ctypes.c_int, ctypes.c_int]
ADSP.adsp2181_yield_on_stop.argtypes = [ctypes.c_void_p, ctypes.c_int]
ADSP.adsp2181_continue_non_idle.argtypes = [ctypes.c_void_p, ctypes.c_int]
ADSP.adsp2181_latch_dm_write.argtypes = [ctypes.c_void_p, ctypes.c_uint16,
                                         ctypes.c_int]
ADSP.adsp2181_latched_dm_write.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_latched_dm_write.restype = ctypes.c_int32
ADSP.adsp2181_pmovlay.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_pmovlay.restype = ctypes.c_uint16
ADSP.adsp2181_dmovlay.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_dmovlay.restype = ctypes.c_uint16
ADSP.adsp2181_read_pm.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
ADSP.adsp2181_read_pm.restype = ctypes.c_uint32
ADSP.adsp2181_trace_budget.argtypes = [ctypes.c_void_p, ctypes.c_int64]
ADSP.adsp2181_coverage_clear.argtypes = [ctypes.c_void_p]
ADSP.adsp2181_coverage_count.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
ADSP.adsp2181_coverage_count.restype = ctypes.c_uint64
ADSP.adsp2181_coverage_gate.argtypes = [ctypes.c_void_p, ctypes.c_int]
ADSP.adsp2181_set_callbacks.argtypes = [ctypes.c_void_p] * 4
ADSP.adsp2181_sport0_tdm_frame.argtypes = [
    ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint16,
    ctypes.c_uint16, ctypes.c_int]
ADSP.adsp2181_sport0_tdm_frame.restype = ctypes.c_uint16
ADSP.adsp2181_modem_sample.argtypes = [
    ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16, ctypes.c_int,
    ctypes.c_uint16, ctypes.c_uint16]
ADSP.adsp2181_modem_sample.restype = ctypes.c_uint16

RX_CB = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_int)
TX_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, ctypes.c_int32)
TIM_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int)

SAMPLE_RATE = 8000
DM_COUPLED_BUFFER_MODE = 0x32F0
DM_RX_BUFFER_POINTER = 0x3F0F
DM_TX_BUFFER_POINTER = 0x3FB4
DM_RX_BUFFER = 0x2B00
DM_TX_BUFFER = 0x2B01
DM_TDM_OUTPUT_LATCH = 0x2E52

# V.22FC's common 8 kHz line-side TX adapter (overlay 0x0271).  The overlay
# publishes DM_PAGE_TX_SAMPLE through DM_TX_BUFFER_POINTER.  PM 0x1d06 fills
# the 20-word circular queue and PM 0x1d46 consumes one sample per frame.
DM_PAGE_TX_COUNT = 0x3761
DM_PAGE_TX_SAMPLE = 0x3764
DM_PAGE_TX_WRITE_POINTER = 0x3765
DM_PAGE_TX_READ_POINTER = 0x3768
DM_PAGE_TX_RING = 0x36E0
DM_PAGE_TX_RING_WORDS = 0x14


def load_pm_words(cpu, path: Path) -> None:
    data = path.read_bytes()
    pm = ADSP.adsp2181_pm(cpu)
    for a in range(0x4000):
        pm[a] = data[a * 3] | (data[a * 3 + 1] << 8) | (data[a * 3 + 2] << 16)


def load_dm_words(cpu, path: Path) -> None:
    data = path.read_bytes()
    dm = ADSP.adsp2181_dm(cpu)
    for a in range(0x4000):
        dm[a] = data[a * 2] | (data[a * 2 + 1] << 8)


def load_sparse_pm_words(cpu, image: Path) -> None:
    """Apply only the PM blocks actually present in an extracted image."""
    import json
    metadata = json.loads((image / "metadata.json").read_text())
    data = (image / "pm.bin").read_bytes()
    pm = ADSP.adsp2181_pm(cpu)
    for block in metadata["pm_blocks"]:
        start = block["address"]
        for address in range(start, start + block["words"]):
            off = address * 3
            pm[address] = (data[off] | (data[off + 1] << 8) |
                           (data[off + 2] << 16))


def apply_word_map(cpu, path: Path) -> None:
    for line in path.read_text().splitlines():
        addr_s, value_s = line.split()
        ADSP.adsp2181_host_write(cpu, int(addr_s, 16), int(value_s, 16))


# ---------------------------------------------------------------- MIPS side

class MipsShim:
    def __init__(self, image: Path, cpu, log: bool = False):
        self.cpu = cpu
        self.log = log
        # Every anchor above is `BIAS + <file offset>`, so an image that does
        # not load at BIAS turns all of them into unrelated bytes -- silently,
        # because Unicorn will happily execute whatever is there. Derive the
        # image's own layout and refuse the mismatch instead. te_dmlt.2q0 (the
        # 4BRI-8 v2 image, and the only one whose file set carries the V.90
        # APCM overlay) loads at 0x80000000, 0x11000 below this one; see
        # docs/bri_target.md for what re-targeting it actually needs.
        self.layout = eicon_mips_image.derive_layout(image)
        expected = (("load base", self.layout.base, BIAS),
                    ("$gp", self.layout.gp, GP),
                    ("initial $sp", self.layout.stack_top, STACK_TOP),
                    ("entry", self.layout.entry, MIPS_ENTRY))
        wrong = [f"{name} 0x{got:08x} != 0x{want:08x}"
                 for name, got, want in expected if got != want]
        if wrong:
            raise RuntimeError(
                f"{image.name} does not match the te_dmlt.pm layout this shim's "
                f"anchor addresses are written against: " + "; ".join(wrong))
        self.uc = Uc(UC_ARCH_MIPS, UC_MODE_LITTLE_ENDIAN | UC_MODE_32)
        self.uc.mem_map(PHYS_BIAS, IMAGE_SIZE)
        self.uc.mem_write(PHYS_BIAS, image.read_bytes())
        self.uc.mem_map(RAM_BASE, RAM_SIZE)
        self.uc.mem_map(STUB_BASE, 0x1000)
        # Zero-initialized .data/.bss segment the assign routine reads
        # (lookup tables at 0x80272c90 etc.).  Auto-map neighbours on touch.
        self.uc.mem_map(DATA_BASE, DATA_SIZE)
        self.uc.mem_map(STACK_BASE, STACK_SIZE)
        # Shared RAM + boot/config area (physical 0x0..0x11000).  The MIPS
        # accesses this via uncached 0xa0001000; Unicorn translates kseg1 to
        # physical by clearing the top 3 bits.
        self.uc.mem_map(SHARED_RAM_BASE, SHARED_RAM_SIZE)
        self.mapped_pages = {DATA_BASE, STACK_BASE, SHARED_RAM_BASE}  # phys pages
        # stub function: jr ra; nop (two copies: entry and terminator)
        stub = struct.pack("<II", 0x03E00008, 0)
        self.uc.mem_write(STUB_BASE, stub)
        self.uc.mem_write(STUB_BASE + 0x20, stub)
        self.stub_returns = 0
        # trace printf pointer lives at gp + 0x1a7b; gp is set per-run
        self.host_writes: list[tuple[int, int]] = []
        self.host_reads: list[tuple[int, int, int]] = []
        self.trace_host_reads = False
        self.preserve_host_writes = False
        # MIPS instructions between ADSP time slices (0 disables the pump).
        # A DSP that runs while the MIPS is streaming an IDMA download will
        # execute the half-replaced image and clobber it, so the download
        # paths hold the core instead (see adsp2181_set_idma_boot_hold).
        self.pump_every = 256
        # One emulated ADSP per DSP register block (see core_for).  Off by
        # default so the single-DSP harness paths keep using self.cpu.
        self.multi_dsp = False
        self.cores: dict[int, object] = {}
        self._idma_addrs: dict[int, int] = {}
        # MIPS instruction count of the last IDMA *write* to each core.  A
        # core is not run while a download is streaming into it: the real
        # card holds the DSP in reset for the transfer, and a core executing
        # its own half-replaced image corrupts it (and then runs wild).
        # Reads do not defer execution, so the boot-handshake poll still
        # lets the DSP it is polling make progress.
        self._core_last_write: dict[int, int] = {}
        self.dsp_write_quiet = 512
        self._active_block = -1
        self._idma_addr = 0
        # Hook memory-mapped writes to the host register block.  The single
        # host_write helper (0x80082950) is intercepted at the function level
        # (PC hook), but the bulk-write helper (0x80082a38) writes directly to
        # the register block: +0x80 = IDMA address port, +0x00 = IDMA data
        # port (auto-incrementing).  Route both to the ADSP IDMA interface.
        from unicorn import UC_HOOK_MEM_WRITE, UC_HOOK_MEM_READ
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self._hostreg_write,
                         begin=RAM_BASE + 0x5000, end=RAM_BASE + 0x5084)
        # The card init (0x80081de0) builds the DSP register bases itself:
        # 0xbc000800 + row_offset + dsp_index*8 for the 30 module DSPs, plus
        # 0xbc000008 / 0xbc000020 for the two on-board ones.  kseg1 maps
        # 0xbc000000 to physical 0x1c000000.  Within each block, +0x80 is the
        # IDMA address port and +0x00 the (auto-incrementing) data port.
        #
        # All 30 blocks alias onto the one emulated ADSP: the port decode
        # only looks at the low byte, so whichever DSP the firmware talks to
        # reaches the same core.  That is what makes the 30-DSP card init
        # work against a single emulated DSP.
        #
        # +0x20 is *not* the second on-board DSP; see CARD_CONTROL_REGISTER.
        # The range must stop after the last DSP block: the two on-board DSPs
        # are at +0x08/+0x20, the module rows at +0x800..+0x870 and
        # +0x1000..+0x1070, and each block's address port is 0x80 above it,
        # so the highest byte in use is 0x10f8.  The card's own control
        # registers live just past that (the card object's +0x80 holds
        # 0xbc001800); hooking them too would route a plain register write
        # into the IDMA path and spawn phantom DSPs.
        self._dsp_base = 0x1c000000
        self._dsp_limit = self._dsp_base + 0x1100
        self.uc.hook_add(UC_HOOK_MEM_READ, self._dsp_read,
                         begin=self._dsp_base, end=self._dsp_limit)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self._dsp_write,
                         begin=self._dsp_base, end=self._dsp_limit)
        # One hook per intercepted address, plus a block hook for the periodic
        # ADSP pump and the fault trace. See INTERCEPT_ADDRESSES.
        for address in INTERCEPT_ADDRESSES:
            self.uc.hook_add(UC_HOOK_CODE, self._hook_intercept,
                             begin=address, end=address)
        self.uc.hook_add(UC_HOOK_BLOCK, self._hook_block)
        # Set when a caller enables trace_calls: that diagnostic genuinely needs
        # to see every instruction, and pays for it.
        self._call_trace_hook = None
        self._service_assign_return_hook = None
        self._pending_hook_dels: list[int] = []
        # Advanced per translation block by the block hook; the ADSP pump
        # cadence and the per-core write-quiet test are both measured in it.
        self._insn_count = 0
        from unicorn import (UC_HOOK_MEM_FETCH_UNMAPPED,
                             UC_HOOK_MEM_READ_UNMAPPED,
                             UC_HOOK_MEM_WRITE_UNMAPPED)
        self.uc.hook_add(UC_HOOK_MEM_FETCH_UNMAPPED, self._unmapped)
        self.uc.hook_add(UC_HOOK_MEM_READ_UNMAPPED, self._unmapped)
        self.uc.hook_add(UC_HOOK_MEM_WRITE_UNMAPPED, self._unmapped)
        self.trace_log: collections.deque[str] = collections.deque(
            maxlen=TRACE_RING)
        # Executions of each intercepted address. Replaces counting occurrences
        # in an unbounded instruction trace.
        self.exec_counts: dict[int, int] = {}
        self.call_trace: list[tuple[str, int, int]] = []
        self._trace_calls = False
        self.phase = "boot"
        self.service_assign_pending = False
        self.intercept_bulk_writes = False
        self.bulk_write_calls: list[tuple[int, int, int]] = []
        self.service_assign_block: int | None = None
        self.native_task_started: set[int] = set()
        self.native_bearer_activation = False
        self.native_kernel: Path | None = None
        self.native_tikrnl: Path | None = None
        self.native_service_assign_return: int | None = None
        self.native_setup_frames = 0
        self.native_connected_driver = False
        # --hook-call is a --mainloop option, but the interesting firmware
        # decisions are made on the native call path the SIP and replay
        # harnesses take, and those construct the shim themselves. The env
        # var is the same instrument reachable from any of them.
        if HOOK_CALL:
            install_call_hooks(self, HOOK_CALL)

    @property
    def trace_calls(self) -> bool:
        return self._trace_calls

    @trace_calls.setter
    def trace_calls(self, enabled: bool) -> None:
        """Attach or drop the per-instruction jal/jalr trace.

        Recording every call site means inspecting every instruction, so this
        is the one caller that needs a code hook over all code. It is a
        diagnostic, not part of a live call: keep it off the media path rather
        than making every run pay for it.
        """
        enabled = bool(enabled)
        if enabled == self._trace_calls:
            return
        self._trace_calls = enabled
        if enabled:
            self._call_trace_hook = self.uc.hook_add(UC_HOOK_CODE,
                                                     self._hook_call_trace)
        elif self._call_trace_hook is not None:
            self._drop_hook(self._call_trace_hook)
            self._call_trace_hook = None

    def _hook_call_trace(self, uc, address, size, user):
        try:
            insn = struct.unpack("<I", uc.mem_read(address & 0x1FFFFFFF, 4))[0]
        except Exception:
            return
        opcode = (insn >> 26) & 0x3F
        if opcode == 0x03:  # jal
            target = ((address + 4) & 0xF0000000) | ((insn & 0x03FFFFFF) << 2)
        elif opcode == 0x00 and (insn & 0x3F) == 0x09:  # jalr
            target = uc.reg_read(UC_MIPS_REG_0 + ((insn >> 21) & 0x1F))
        else:
            return
        self.call_trace.append((self.phase, address, target))

    def _set_load_result(self, uc, val, size):
        """Decode the MIPS load instruction at current PC and write val
        into the destination register.  Needed because the Python Unicorn
        bindings do not allow a UC_HOOK_MEM_READ callback to override the
        value the emulated instruction sees for the current access."""
        from unicorn.mips_const import UC_MIPS_REG_PC, UC_MIPS_REG_0
        pc = uc.reg_read(UC_MIPS_REG_PC)
        try:
            insn = struct.unpack("<I", uc.mem_read(pc, 4))[0]
        except Exception:
            return
        opcode = (insn >> 26) & 0x3F
        # MIPS I-type load opcodes: lb/lh/lwl/lw/lbu/lhu/lwr
        if 0x20 <= opcode <= 0x27:
            rt = (insn >> 16) & 0x1F
            if size == 1:
                if opcode == 0x20:   # lb  – sign-extend byte
                    val = val if (val & 0x80) == 0 else val - 0x100
                else:                # lbu – zero-extend byte
                    val = val & 0xFF
            elif size == 2:
                if opcode == 0x21:   # lh  – sign-extend halfword
                    val = val if (val & 0x8000) == 0 else val - 0x10000
                else:                # lhu/lw/lwl/lwr – zero-extend halfword
                    val = val & 0xFFFF
            uc.reg_write(UC_MIPS_REG_0 + rt, val)

    def core_for(self, block: int):
        """The emulated DSP behind one host register block.

        Each of the card's DSPs has its own 8-byte register block and its own
        ADSP-2181, so they need separate cores: with a single shared core the
        second DSP's download lands in the first DSP's running image.  Cores
        are created on demand, held in IDMA boot mode until their own
        download writes PM 0.  `block` may be a kseg address.
        """
        if not self.multi_dsp:
            return self.cpu
        block &= 0x1fffffff
        self._active_block = block
        core = self.cores.get(block)
        if core is None:
            core = ADSP.adsp2181_create()
            ADSP.adsp2181_reset(core)
            ADSP.adsp2181_set_idma_boot_hold(core, 1)
            self.cores[block] = core
            self._idma_addrs[block] = 0
            if self.log:
                print(f"[mips] new DSP core for register block 0x{block:08x}")
        return core

    def _start_native_selected_task(self, block: int, core) -> None:
        block &= 0x1fffffff
        if (not self.native_bearer_activation or
                block != self.service_assign_block or
                block in self.native_task_started):
            return
        # SERVICE_ASSIGN has now finished the firmware's genuine segmented
        # download. It already contains the resident kernel plus the relocated
        # TIKRNL task (source PM 06fc is runtime PM 0703). Do not overlay the
        # extracted source-address image here: that destroys those relocations.
        ADSP.adsp2181_set_idma_boot_hold(core, 0)
        ADSP.adsp2181_call(core, 0x0679, 0x02A8)
        ADSP.adsp2181_run(core, 2_000_000)
        if not ADSP.adsp2181_idle(core):
            raise RuntimeError(
                f"native selected-task initializer stopped at "
                f"PM 0x{ADSP.adsp2181_pc(core):04x}")
        self.native_task_started.add(block)

    def _dsp_ports(self, address):
        """Split a DSP register access into (block base, port)."""
        port = address & 0xFF
        if port >= 0x80:
            return address - 0x80, port
        return address, port

    def _dsp_read(self, uc, access, address, size, value, user):
        # DSP register block: +0x80 = IDMA address port, +0x00 = data port.
        block, port = self._dsp_ports(address)
        if (block & 0x1FFFFFFF) == CARD_CONTROL_REGISTER:
            return True
        if port >= 0x80:
            # Address port reads are rare; let the auto-mapped zero page
            # return 0.
            return True
        # Data port read — return IDMA data and force it into the
        # destination register so the read-back verify sees the value.
        val = ADSP.adsp2181_idma_data_read(self.core_for(block))
        if self.log:
            idma = self._idma_addrs.get(block & 0x1fffffff, self._idma_addr)
            tag = "DM" if idma & 0x4000 else "PM"
            print(f"[mips] dsp_read {tag} 0x{idma & 0x3fff:04x} -> 0x{val:04x}")
        # Patch the mapped page (fallback if Unicorn re-reads), and
        # directly set the destination register.
        uc.mem_write(address, struct.pack("<H", val))
        self._set_load_result(uc, val, size)
        return True

    def _dsp_write(self, uc, access, address, size, value, user):
        block, port = self._dsp_ports(address)
        if (block & 0x1FFFFFFF) == CARD_CONTROL_REGISTER:
            return True
        core = self.core_for(block)
        if self.service_assign_pending and self.service_assign_block is None:
            self.service_assign_block = block & 0x1fffffff
        value &= 0xFFFF
        if port >= 0x80:
            self._idma_addr = value
            self._idma_addrs[block & 0x1fffffff] = value
            ADSP.adsp2181_idma_addr_write(core, value)
            if self.log:
                print(f"[mips] dsp_addr 0x{value:04x}")
        else:
            ADSP.adsp2181_idma_data_write(core, value)
            self._core_last_write[block & 0x1fffffff] = self._insn_count
            idma = self._idma_addrs.get(block & 0x1fffffff, self._idma_addr)
            if self.log:
                tag = "DM" if idma & 0x4000 else "PM"
                print(f"[mips] dsp_write {tag} 0x{idma & 0x3fff:04x} = 0x{value:04x}")
            self.host_writes.append((idma, value))
        return True

    def _hostreg_write(self, uc, access, address, size, value, user):
        # Memory-mapped write to the host register block (hostreg_v region).
        # +0x80 = IDMA address port, +0x00 = IDMA data port (auto-increment).
        off = address - (RAM_BASE + 0x5000)
        if off == 0x80:
            self._idma_addr = value & 0xFFFF
            ADSP.adsp2181_idma_addr_write(self.cpu, value & 0xFFFF)
        elif off == 0x00:
            ADSP.adsp2181_idma_data_write(self.cpu, value & 0xFFFF)
            if self.log:
                tag = "DM" if self._idma_addr & 0x4000 else "PM"
                print(f"[mips] idma_write {tag} 0x{self._idma_addr & 0x7fff:04x} = 0x{value:04x}")
            self.host_writes.append((self._idma_addr, value))
        return True

    def _unmapped(self, uc, access, address, size, value, user):
        from unicorn import UC_MEM_READ_UNMAPPED, UC_MEM_WRITE_UNMAPPED
        from unicorn.mips_const import UC_MIPS_REG_PC, UC_MIPS_REG_RA
        pc = uc.reg_read(UC_MIPS_REG_PC)
        # Translate kseg0 (0x8xxx) / kseg1 (0xaxxx) to physical by clearing
        # the top 3 bits, then auto-map a zero page for data accesses.
        phys = address & 0x1fffffff
        page = phys & ~(AUTO_PAGE - 1)
        if access in (UC_MEM_READ_UNMAPPED, UC_MEM_WRITE_UNMAPPED):
            # Catch-all: auto-map any unmapped physical page.  The firmware
            # init accesses hardware registers (PCI config, interrupt
            # controller) at various addresses; mapping them as zero lets
            # the init proceed past hardware probes.
            if page not in self.mapped_pages:
                if self.log:
                    print(f"[mips] auto-map page phys=0x{page:06x} "
                          f"(touch @0x{address:08x} pc=0x{pc:08x})")
                # ensure_mapped consults the live region list: a bare
                # mem_map here throws when the page overlaps one of the
                # larger fixed mappings, which surfaces as an unmapped-access
                # error rather than the auto-map it is meant to perform.
                self.ensure_mapped(page, AUTO_PAGE)
            return True
        print(f"[mips] unmapped access {access} {address:08x} (phys 0x{phys:08x}) "
              f"sz={size} pc=0x{pc:08x} ra=0x{uc.reg_read(UC_MIPS_REG_RA):08x}")
        for entry in self.recent_trace(12):
            print(f"   {entry}")
        return False

    def recent_trace(self, count: int) -> list[str]:
        """The most recent block addresses, oldest first, for fault reports."""
        return list(self.trace_log)[-count:]

    def _hook_block(self, uc, address, size, user):
        """Per-translation-block accounting and the periodic ADSP time slice.

        The pump used to run on an exact instruction count, which required a
        hook on every instruction. Block granularity keeps the same average
        cadence -- ``_insn_count`` still advances by real instruction counts --
        for a fraction of the callbacks. The pump is a "let the DSP make
        progress" clock, not a cycle-accurate relationship, so its phase within
        a basic block carries no meaning.
        """
        self.trace_log.append(f"{address:08x}")
        # Pump the ADSP in lockstep with the MIPS: about every pump_every MIPS
        # instructions, run the ADSP a few cycles so the DSP can boot,
        # acknowledge, and process commands the MIPS downloads. Without this
        # the MIPS init hangs polling for a DSP boot-acknowledge that never
        # comes (the DSP never runs).
        previous = self._insn_count
        self._insn_count = previous + max(1, size >> 2)
        if not self.pump_every:
            return
        if previous // self.pump_every == self._insn_count // self.pump_every:
            return
        self._pump_adsp()

    def _pump_adsp(self) -> None:
        # Strobe IRQE (irq 6) to wake the DSP foreground from IDLE so it
        # runs code the MIPS just downloaded and writes the boot-ack.
        # Cores still in IDMA boot hold ignore the run and stay stopped.
        if self.multi_dsp:
            # Only the DSP the firmware is currently talking to runs: the
            # card brings its DSPs up one at a time, and every other core
            # is either still being downloaded into or waiting its turn.
            block = self._active_block
            core = self.cores.get(block)
            if core is None:
                return
            quiet = (self._insn_count -
                     self._core_last_write.get(block, -1 << 30))
            if quiet <= self.dsp_write_quiet:
                return
        else:
            core = self.cpu
        if (self.native_bearer_activation and
                self.native_connected_driver and
                self.native_setup_frames < 4 and
                self.service_assign_block in self.native_task_started and
                core is self.cores.get(self.service_assign_block)):
            # PRI SPORT never stops while the MIPS handles call setup.
            # The selected task consumes its connected command on that
            # clock; IRQE alone is masked after TIKRNL initialization.
            pm = ADSP.adsp2181_pm(core)
            saved_isr = install_isr_vector(pm)
            try:
                ADSP.adsp2181_modem_sample(
                    core, 0x00FF, 0x00FF, 3000, 0x02A9, 0x02A8)
                if ADSP.adsp2181_idle(core):
                    ADSP.adsp2181_call(core, 0x06C8, 0x02A8)
                    ADSP.adsp2181_run(core, 3000)
            finally:
                restore_isr_vector(pm, saved_isr)
            self.native_setup_frames += 1
        else:
            ADSP.adsp2181_set_irq(core, 6, 1)
            ADSP.adsp2181_run(core, 2000)
            ADSP.adsp2181_set_irq(core, 6, 0)
            ADSP.adsp2181_run(core, 1000)

    def _drop_hook(self, handle: int) -> None:
        """Queue a hook for removal.

        ``Uc.hook_del`` drops the binding's reference to the ctypes trampoline
        for the callback, so calling it from inside that callback frees the
        function being executed. Every removal here can happen mid-emulation,
        so none of them are immediate: they are applied in _flush_hook_dels
        before the next emu_start.
        """
        self._pending_hook_dels.append(handle)

    def _flush_hook_dels(self) -> None:
        while self._pending_hook_dels:
            self.uc.hook_del(self._pending_hook_dels.pop())

    def _set_service_assign_return(self, address: int | None) -> None:
        """Watch SERVICE_ASSIGN's return address, which is only known at call
        time. One hook, added when the caller is captured and dropped when it
        fires, rather than testing every instruction against it."""
        self.native_service_assign_return = address
        if self._service_assign_return_hook is not None:
            self._drop_hook(self._service_assign_return_hook)
            self._service_assign_return_hook = None
        if address is not None:
            self._service_assign_return_hook = self.uc.hook_add(
                UC_HOOK_CODE, self._hook_intercept, begin=address, end=address)

    def _hook_intercept(self, uc, address, size, user):
        """Function-level interception, one hook per address.

        Registered individually over INTERCEPT_ADDRESSES so Unicorn instruments
        only the blocks that contain them. Each branch below replaces a firmware
        routine: the host-port helpers are answered from the emulated DSP and
        returned from by writing PC = RA, so the real body never executes.
        """
        self.exec_counts[address] = self.exec_counts.get(address, 0) + 1
        if address == self.native_service_assign_return:
            if (self.native_bearer_activation
                    and self.service_assign_block is not None):
                block = self.service_assign_block
                self._start_native_selected_task(block, self.core_for(block))
            self._set_service_assign_return(None)
            return
        if address == CONNECTED_DRIVER and self.native_bearer_activation:
            # The connected driver is publishing its control toggle now. PRI
            # clocks before this call cannot service that not-yet-live command.
            self.native_connected_driver = True
            self.native_setup_frames = 0
        if address == SERVICE_ASSIGN:
            self.service_assign_pending = True
            if self.native_bearer_activation:
                # The service object points at the task object at +0; the
                # task owns its DSP host-register base at +0x10. Capture the
                # caller now, then initialize only after SERVICE_ASSIGN has
                # completed its sparse download and before the next DSP pump.
                service = uc.reg_read(UC_MIPS_REG_A0)
                task = struct.unpack(
                    "<I", bytes(uc.mem_read(service & 0x1fffffff, 4)))[0]
                block = struct.unpack(
                    "<I", bytes(uc.mem_read((task + 0x10) & 0x1fffffff, 4)))[0]
                self.service_assign_block = block & 0x1fffffff
                self._set_service_assign_return(uc.reg_read(UC_MIPS_REG_RA))
        if (self.intercept_bulk_writes
                and address in (HOST_WRITE_DM_BLOCK, HOST_WRITE_PM_BLOCK)):
            a0 = uc.reg_read(UC_MIPS_REG_A0)
            dest = uc.reg_read(UC_MIPS_REG_A1) & 0x3FFF
            source = uc.reg_read(UC_MIPS_REG_A2)
            count = uc.reg_read(UC_MIPS_REG_A3) & 0xFFFF
            core = self.core_for(a0)
            self.bulk_write_calls.append((address, dest, count))
            raw = bytes(uc.mem_read(source & 0x1FFFFFFF,
                                    count * (2 if address == HOST_WRITE_DM_BLOCK else 4)))
            if address == HOST_WRITE_DM_BLOCK:
                dm = ADSP.adsp2181_dm(core)
                for index in range(count):
                    value = struct.unpack_from("<H", raw, index * 2)[0]
                    dm[(dest + index) & 0x3FFF] = value
                    self.host_writes.append((0x4000 | ((dest + index) & 0x3FFF),
                                             value))
            else:
                pm = ADSP.adsp2181_pm(core)
                for index in range(count):
                    high, low = struct.unpack_from("<HH", raw, index * 4)
                    value = ((high << 8) | (low & 0xFF)) & 0xFFFFFF
                    pm[(dest + index) & 0x3FFF] = value
                    self.host_writes.append(((dest + index) & 0x3FFF, value))
            uc.reg_write(UC_MIPS_REG_V0, 1)
            uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))
        elif address == HOST_WRITE:
            a0 = uc.reg_read(UC_MIPS_REG_A0)
            if self.service_assign_pending and self.service_assign_block is None:
                self.service_assign_block = a0 & 0x1fffffff
            a1 = uc.reg_read(UC_MIPS_REG_A1) & 0xFFFF
            a2 = uc.reg_read(UC_MIPS_REG_A2) & 0xFFFF
            if self.log:
                print(f"[mips] host_write [0x{a0:08x}] {a1:04x} = {a2:04x}")
            self.host_writes.append((a1, a2))
            ADSP.adsp2181_host_write(self.core_for(a0), a1, a2)
            self._core_last_write[a0 & 0x1fffffff] = self._insn_count
            uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))
        elif address == HOST_READ:
            a0 = uc.reg_read(UC_MIPS_REG_A0)
            a1 = uc.reg_read(UC_MIPS_REG_A1) & 0xFFFF
            value = ADSP.adsp2181_host_read(self.core_for(a0), a1)
            if self.trace_host_reads:
                self.host_reads.append((a0 & 0x1FFFFFFF, a1, value))
            if self.log:
                print(f"[mips] host_read [0x{a0:08x}] {a1:04x} -> {value:04x}")
            uc.reg_write(UC_MIPS_REG_V0, value)
            uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))
        elif address == STUB_VIRT:
            self.stub_returns += 1
            uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    def call(self, entry: int, args: list[int], gp: int, sp: int,
             max_insns: int = 200000,
             extra_regs: "dict[int, int] | None" = None) -> int:
        uc = self.uc
        self._flush_hook_dels()
        uc.reg_write(UC_MIPS_REG_SP, sp)
        uc.reg_write(UC_MIPS_REG_GP, gp)
        uc.reg_write(UC_MIPS_REG_RA, STUB_VIRT + 0x20)
        for i, value in enumerate(args[:4]):
            uc.reg_write([UC_MIPS_REG_A0, UC_MIPS_REG_A1,
                          UC_MIPS_REG_A2, UC_MIPS_REG_A3][i], value)
        if extra_regs:
            for reg, value in extra_regs.items():
                uc.reg_write(reg, value)
        if not self.preserve_host_writes:
            self.host_writes = []
        uc.emu_start(entry, STUB_VIRT + 0x20, count=max_insns)
        return uc.reg_read(UC_MIPS_REG_V0)

    def write32(self, virt: int, value: int) -> None:
        self.uc.mem_write(virt & 0x1fffffff, struct.pack("<I", value & 0xffffffff))

    def write16(self, virt: int, value: int) -> None:
        self.uc.mem_write(virt & 0x1fffffff, struct.pack("<H", value & 0xffff))

    def write8(self, virt: int, value: int) -> None:
        self.uc.mem_write(virt & 0x1fffffff, bytes([value & 0xff]))

    def ensure_mapped(self, virt: int, size: int) -> None:
        """Map every auto-page the range [virt, virt+size) falls in.

        The fixed mappings are larger than AUTO_PAGE, so membership in
        `mapped_pages` is not enough — check the live region list.
        """
        # Map at Unicorn's 4K granularity: a 64K unit can straddle the edge
        # of an existing region, and mem_map rejects any overlap.
        page_size = 0x1000
        phys = virt & 0x1fffffff
        regions = [(begin, end) for begin, end, _perms in self.uc.mem_regions()]
        first = phys & ~(page_size - 1)
        last = (phys + size - 1) & ~(page_size - 1)
        for page in range(first, last + page_size, page_size):
            if any(begin <= page <= end for begin, end in regions):
                continue
            self.uc.mem_map(page, page_size)
            self.mapped_pages.add(page)
            regions.append((page, page + page_size - 1))

    def write_bytes(self, virt: int, data: bytes) -> None:
        self.ensure_mapped(virt, len(data))
        self.uc.mem_write(virt & 0x1fffffff, data)

    def alloc(self, virt_base: int, words: int) -> int:
        """Reserve a zeroed guest block; returns the guest-visible pointer."""
        self.uc.mem_write(virt_base & 0x1fffffff, bytes(words * 4))
        return virt_base


def symbol_address(metadata: dict, index: int) -> int:
    """Resolve a download symbol to its bare DSP address (no IDMA type bit)."""
    symbol = metadata["symbols"][index]
    segment = symbol["segment"]
    if segment < 4:
        return symbol["offset"]
    seg = next(s for s in metadata["segments"] if s["number"] == segment)
    return seg["base"] + symbol["offset"]


def symbol_host_address(metadata: dict, index: int) -> int:
    """Resolve a download symbol to a host-port address, as 0x800a6204 does.

    The firmware ORs in 0x4000 for data memory and leaves it clear for
    program memory, which is the ADSP-2181 IDMA "destination type" bit.  For
    the fixed segments 0-3 that split is by segment number (0/2 = DM, 1/3 =
    PM, matching memory blocks 0-3); for relocatable segments it comes from
    the target memory block's `memory_type & 1` (1 = PM).
    """
    symbol = metadata["symbols"][index]
    segment = symbol["segment"]
    if segment < 4:
        is_pm = segment in (1, 3)
        return symbol["offset"] | (0 if is_pm else 0x4000)
    seg = next(s for s in metadata["segments"] if s["number"] == segment)
    block = next(b for b in metadata["memory_blocks"]
                 if b["number"] == seg["memory_block"])
    is_pm = bool(block["type"] & 1)
    return (seg["base"] + symbol["offset"]) | (0 if is_pm else 0x4000)


def mips_runtime_addr(addr: int) -> int:
    """Normalize Unicorn's physical MIPS PCs/targets to firmware kseg0 addrs."""
    phys = addr & 0x1fffffff
    if PHYS_BIAS <= phys < PHYS_BIAS + IMAGE_SIZE:
        return phys | 0x80000000
    return addr


def run_assign(shim: "MipsShim", args) -> None:
    """Synthesize the TIKRNL download/task struct and run the service-assign
    entry (0x80096980) so the real firmware performs the switch-on database
    commit through the hooked host port.

    The struct layout follows the field accesses in the 0x80096980 prologue:
      a0 (s3): assign request
        +0x00 -> base (s2); per-channel state is s1 = s2 + 0x200
        +0x04 -> resource struct (s0); NULL here returns immediately
        +0x08 -> existing mailbox; 0 for a fresh assign
        +0x18 -> channel/timeslot byte
      s0 (resource):
        +0x04 -> download/task descriptor; *(desc+0) = download id (0x0258)
        +0x40 -> task id halfword (0x0258)
        +0x140 -> mailbox state (0 to take the fresh-assign branch)
      desc (download descriptor):
        +0x00 -> download id (0x0258)
        +0x24 -> relocation/symbol table pointer
    The descriptor and per-channel state are large; zero-initialize them and
    let the auto-map hook supply .bss lookups.  Firmware host_write calls
    during assign are the switch-on database commit.
    """
    import json
    metadata = json.loads((args.tikrnl / "metadata.json").read_text())
    write13 = symbol_host_address(metadata, 13)  # 0x3310 host->TIKRNL mailbox
    write14 = symbol_host_address(metadata, 14)  # 0x3338 TIKRNL->host mailbox

    # Guest RAM layout (RAM_VIRT + offset; API uses physical = RAM_BASE + off):
    #   0x4000 assign request (s3)        0x60 bytes
    #   0x4100 resource struct (s0)       0x200 bytes
    #   0x4400 base block (s2) / s1=+0x200 0x400 bytes
    #   0x4900 download descriptor         0x100 bytes
    #   0x4a00 relocation/symbol table     0x200 bytes
    #   0x4d00 channel context (chctx)     0x100 bytes
    #   0x4e00 channel descriptor (desc2)  0x200 bytes
    #   0x5000 host register block         0x40 bytes
    #   0x5100 scratch words               0x100 bytes
    gp = GP
    sp = STACK_TOP
    base_v = RAM_VIRT + 0x4400
    res_v = RAM_VIRT + 0x4100
    desc_v = RAM_VIRT + 0x4900
    reloc_v = RAM_VIRT + 0x4a00
    chctx_v = RAM_VIRT + 0x4d00
    desc2_v = RAM_VIRT + 0x4e00
    hostreg_v = RAM_VIRT + 0x5000
    scratch_v = RAM_VIRT + 0x5100
    shim.alloc(RAM_VIRT + 0x4000, 0x2000)

    # Download descriptor: id 0x0258, relocation table at reloc_v.
    shim.write16(desc_v + 0x00, 0x0258)        # download id (low half)
    shim.write32(desc_v + 0x24, reloc_v)        # relocation/symbol table

    # Populate the relocation/symbol table from metadata.json.  Each entry is
    # 8 bytes; db_record_append reads the resolved DM address as a halfword at
    # entry+4 (reloc_table + symbol_index*8 + 4).  Only DM-resident symbols
    # (segments in memory_block 2 = DM) are meaningful here.
    for sym in metadata["symbols"]:
        seg = next((s for s in metadata["segments"]
                    if s["number"] == sym["segment"]), None)
        if sym["segment"] < 4:
            dm_addr = sym["offset"]
        elif seg and seg["memory_block"] == 2:  # DM
            dm_addr = seg["base"] + sym["offset"]
        else:
            continue  # PM symbol — not a DM database target
        shim.write16(reloc_v + sym["id"] * 8 + 4, dm_addr)

    # Resource struct: +4 -> descriptor, +0x40 = task id, +0x140 = 0 (fresh).
    shim.write32(res_v + 0x04, desc_v)
    shim.write16(res_v + 0x40, 0x0258)

    # Base (s2): the card/channel struct the firmware assumes already exists.
    #   +0x0c -> channel context (chctx)
    #   +0x10 -> host register block (also written to s1+0 and the mailbox)
    # chctx+0x24 -> the download symbol table (db_record_append a2!=0 path
    # resolves symbol index -> DM address at entry+4).  Point this at the
    # populated TIKRNL symbol table (reloc_v) so records resolve correctly.
    shim.write32(base_v + 0x0c, chctx_v)
    shim.write32(base_v + 0x10, hostreg_v)
    shim.write32(chctx_v + 0x24, reloc_v)
    shim.write16(desc2_v + 0x106, 0x0000)

    # Per-channel state s1 = base + 0x200.  s1+0x6c must point to a writable
    # word (the routine does `sh zero, ($v1)` through it).
    shim.write32(base_v + 0x200 + 0x6c, scratch_v)
    shim.write32(base_v + 0x200 + 0x70, scratch_v + 4)
    # s1+0x04 -> resource struct, so db_record_append's a2==0 path resolves
    # *(s1+4) -> res -> +4 -> desc -> +0x24 -> relocation/symbol table.
    shim.write32(base_v + 0x200 + 0x04, res_v)

    # Per-channel command mailbox at s1+0x24 (= base+0x224).  0x80093d14 calls
    # 0x80086af8 (DSP handshake wait) on it before the db commit; for a fresh
    # assign the active flag (+0x10) makes it return nonzero immediately so
    # the switch-on database commit (0x80090e58) actually runs.
    #   +0x08 -> host register block; +0x0c -> symbol-13 mailbox descriptor;
    #   +0x10 (byte) active = 1; +0x12 halfword = 0; +0x14 -> ring descriptor.
    mbox_v = base_v + 0x224
    shim.write32(mbox_v + 0x08, hostreg_v)
    shim.write32(mbox_v + 0x0c, reloc_v)   # any valid descriptor; type 0 path
    shim.write8(mbox_v + 0x10, 1)           # active -> 0x80086af8 returns nonzero
    shim.write16(mbox_v + 0x12, 0x0000)
    shim.write32(mbox_v + 0x14, reloc_v)

    # Database ring descriptor in the channel state (s6/s3 = s1 = base+0x200).
    # db_ring_commit (0x8008dd14) reads the DSP producer via host_read at
    # *(s3+0xc)+1, then validates (producer - base) < length before writing.
    #   s3+0x0c = producer address - 1, already carrying the IDMA type bit
    #             (db_ring_commit host_reads s3+0xc+1 with no further masking)
    #   s3+0x10 (byte) = ring memory select: zero makes 0x8008dda8 OR in
    #             0x4000, i.e. a DM ring; nonzero leaves the address in PM
    #   s3+0x12 = bare ring base address;  s3+0x14 = ring length
    # The TIKRNL symbol-13 command ring is in DM at 0x3327 (16 words) with its
    # producer pointer at DM 0x3315 (initialised to 0x3327 by the TIKRNL
    # initializer at PM 0x672).  The switch-on database commit targets this
    # ring: the DSP's TIKRNL consumer polls DM 0x3315/0x3316 and processes
    # records from DM 0x3327.
    ring_v = base_v + 0x200
    shim.write16(ring_v + 0x0c, write13 + 0x05 - 1)  # producer addr - 1 = 0x7314
    shim.write8(ring_v + 0x10, 0x00)                  # zero -> DM ring (+0x4000)
    shim.write16(ring_v + 0x12, symbol_address(metadata, 13) + 0x17)  # bare 0x3327
    # DM 0x3327..0x3337 is 17 words.  The switch-on record occupies exactly
    # 16 words; declaring a 16-word ring wraps the producer to the consumer
    # and makes the full ring indistinguishable from empty.
    shim.write16(ring_v + 0x14, 0x0011)               # ring length (17)

    # Assign request (s3): +0 -> base, +4 -> resource, +8 = 0, +0x18 = channel.
    shim.write32(RAM_VIRT + 0x4000 + 0x00, base_v)
    shim.write32(RAM_VIRT + 0x4000 + 0x04, res_v)
    shim.write32(RAM_VIRT + 0x4000 + 0x08, 0)
    shim.write8(RAM_VIRT + 0x4000 + 0x18, args.channel)

    print(f"[assign] calling 0x{SERVICE_ASSIGN:08x} req=0x{RAM_VIRT+0x4000:08x} "
          f"base=0x{base_v:08x} res=0x{res_v:08x} desc=0x{desc_v:08x} "
          f"ch={args.channel} mb13=0x{write13:04x} mb14=0x{write14:04x}")
    try:
        v0 = shim.call(SERVICE_ASSIGN, [RAM_VIRT + 0x4000], gp=gp, sp=sp,
                       max_insns=4000000)
    except Exception as exc:
        from unicorn.mips_const import UC_MIPS_REG_PC, UC_MIPS_REG_RA
        pc = shim.uc.reg_read(UC_MIPS_REG_PC)
        ra = shim.uc.reg_read(UC_MIPS_REG_RA)
        print(f"[assign] fault: {exc} pc=0x{pc:08x} ra=0x{ra:08x}")
        print("  recent:")
        for e in shim.recent_trace(16):
            print(f"    {e}")
        return
    print(f"[assign] returned v0=0x{v0:08x} host_writes={len(shim.host_writes)}")
    adsp_dm = ADSP.adsp2181_dm(shim.cpu)
    print("[assign] TIKRNL command ring DM3327..3336: "
          + " ".join(f"{adsp_dm[a]:04x}"
                     for a in range(0x3327, 0x3337)))
    print("[assign] TIKRNL control DM3310..3316: "
          + " ".join(f"{adsp_dm[a]:04x}" for a in range(0x3310, 0x3317)))
    print("[assign] host writes: "
          + " ".join(f"{addr:04x}={val:04x}"
                     for addr, val in shim.host_writes))
    if args.log:
        # Show the last PCs in the assign call to see which return path fired.
        print("  assign trace tail:")
        for e in shim.recent_trace(24):
            print(f"    {e}")
    # Read back the database record buffer (0x80331c12) to see what
    # db_record_append produced.
    buf = shim.uc.mem_read(0x00331c12, 0x80)
    nonzero = next((i for i, b in enumerate(buf) if b), None)
    if nonzero is not None:
        print(f"  db buffer @0x80331c12 has data from offset {nonzero}: "
              + " ".join(f"{b:02x}" for b in buf[:64]))
    else:
        print("  db buffer @0x80331c12 is empty (db_record_append produced nothing)")
    if shim.log and shim.host_writes:
        for addr, val in shim.host_writes[:64]:
            tag = "DM" if addr & 0x4000 else "PM"
            print(f"  host_write {tag} 0x{addr & 0x7fff:04x} = 0x{val:04x}")


def stage_dsp_code(shim: "MipsShim", args) -> int:
    """Write the DSP download image to card RAM and point the header at it.

    Reproduces pri_telindus_load (kernel/s_pri.c): the image goes at
    DspCodeBaseAddr — the protocol image's own OFFS_PROTOCOL_END_ADDR,
    dword-aligned — and that address is written back into the image header
    at OFFS_DSP_CODE_BASE_ADDR, which is where the MIPS entry reads it from
    (`lw $s1, 0x106c($s1)` with the image based at 0xa0011000).
    """
    base = args.dsp_code_base
    if base is None:
        base = protocol_end_addr(args.image)
    image = build_dsp_code_image(args.dsp_combifile, args.card_type, base,
                                 extra_download_ids=DSP_EXTRA_DOWNLOADS)
    shim.write_bytes(base, image.data)
    # The header field is read through the uncached image alias; write it via
    # the physical address the image was loaded at.
    shim.write32(BIAS + OFFS_DSP_CODE_BASE_ADDR, base)
    print(f"[mainloop] DSP code staged at 0x{base:08x}..0x{image.end_addr:08x} "
          f"({len(image.data)} bytes, {len(image.downloads)} downloads, "
          f"card type {image.card_type} -> file set {image.file_set})")
    interesting_downloads = {
        0x0007,  # DIVA Server PRI 2M TX Kernel
        0x0008,  # DIVA Server PRI 2M RX Kernel
        0x000B,  # DIVA Server PRI 2M TX SIG Kernel
        0x000C,  # DIVA Server PRI 2M RX SIG Kernel
        0x0208,  # SIG.MDM Task
        0x0209,  # SIGPRTX Task
        0x020A,  # SIGPRRX Task
        0x0258,  # TIKRNL81.F34 Task
        0x025F,  # V.8 overlay
        0x0261,  # V.34 overlay
        0x026A,  # V.90 DPCM overlay
        0x026B,  # V.90 APCM overlay (staged only via EICON_DSP_EXTRA_DOWNLOADS)
        0x0270,  # SIG overlay loaded before negative TIKRNL pages
    }
    for entry in image.downloads:
        if entry.download_id in interesting_downloads:
            print(f"           id=0x{entry.download_id:04x} @0x{entry.address:08x} "
                  f"{entry.description}")
    return base


# 0xbc000020 is a card control register, not the second on-board DSP the
# comment in MipsShim.__init__ used to claim. The firmware never sets its IDMA
# address port and never streams to it: it writes single bytes 0x00 and 0x12,
# seven times, through the helper at 0x80082eb0, which stores the byte to
# *(card+0x84) with card = 0x802728c8 -- the same card object the DSP scan uses
# as $s4. Treating it as a DSP block spawned a phantom 31st core that could
# never be downloaded, which is exactly the hazard the range comment warns
# about, and made report_dsp_boot() claim "1 still held" on every run.
# Session 99.
CARD_CONTROL_REGISTER = 0x1C000020

DSP_BOOT_MAILBOX = 0x3FFF   # kernel symbol 0: PM 0x3fff
DSP_BOOT_PROBE = 0x5A5A     # written by 0x800a77e0 before the download
DSP_BOOT_ACK = 0xA5A5       # polled for by 0x800a78d0 afterwards


def report_dsp_boot(shim: "MipsShim", cycles: int = 200000) -> int:
    """Run each downloaded DSP and report the boot handshake result.

    The validator (0x80082130 -> 0x800a77e0) writes 0x5a5a to the download's
    symbol 0, streams the kernel in, releases the core, then polls that word
    for 0xa5a5.  This runs the cores the firmware has finished downloading
    and reports how many produce the acknowledgement, which is what the
    handshake is waiting on.
    """
    if not shim.cores:
        return 0
    held = acked = 0
    held_blocks: "list[int]" = []
    for block, core in sorted(shim.cores.items()):
        if ADSP.adsp2181_idma_boot_held(core):
            held += 1
            held_blocks.append(block)
            continue
        ADSP.adsp2181_run(core, cycles)
        if ADSP.adsp2181_host_read(core, DSP_BOOT_MAILBOX) == DSP_BOOT_ACK:
            acked += 1
        elif shim.log:
            pm = ADSP.adsp2181_pm(core)
            print(f"[dsp] block 0x{block:08x} no ack: "
                  f"pm[0x{DSP_BOOT_MAILBOX:04x}]=0x{pm[DSP_BOOT_MAILBOX]:06x}")
    print(f"[dsp] {len(shim.cores)} cores: {acked} answered the boot handshake "
          f"with 0x{DSP_BOOT_ACK:04x}, {held} still held (no download)")
    if held_blocks:
        # Session 98: a core that never gets a download fails the firmware's
        # own DSP test, and the failure releases a service entry from the
        # dispatch table at 0x8012227c. Name the blocks, because which one is
        # held decides which service goes away.
        print("[dsp] held blocks: "
              + ", ".join(f"0x{block:08x}" for block in held_blocks))
    return acked


_MODEM_OPTIONS_OVERRIDE: "eicon_idi.ModemOptions | None" = None


def set_modem_options(options: "eicon_idi.ModemOptions | None") -> None:
    """Override the modem configuration for every CAI built from now on.

    This is how the AT layer applies `+IE`: the selection is made before the
    call exists, and the CAI is built during firmware entry, so the two need
    somewhere to meet.  Setting None restores the environment-driven default.
    """
    global _MODEM_OPTIONS_OVERRIDE
    _MODEM_OPTIONS_OVERRIDE = options


def modem_options() -> eicon_idi.ModemOptions:
    """The modem configuration every CAI in this run is built from.

    ``EICON_MODULATION`` takes an AT ``+IE`` argument --
    ``<mod>[,<automode>[,<min_rx>,<max_rx>,<min_tx>,<max_tx>]]`` -- and runs
    it through the driver's own selection algorithm, so
    ``EICON_MODULATION=v34,1,,33600,,33600`` produces the disabled mask the
    tty driver would send rather than the single V.90 bit the old
    ``EICON_FORCE_V34`` set.  That difference is the point: the driver also
    disables every modulation its table does not name, V.FC, K56flex and X2
    among them.

    With neither set, the payload is byte-for-byte what this project has been
    sending on the known-good V.90 path.
    """
    if _MODEM_OPTIONS_OVERRIDE is not None:
        return _MODEM_OPTIONS_OVERRIDE
    if MODULATION:
        fields = [f.strip() for f in MODULATION.split(",")]
        name = fields[0]
        nums = [int(f) if f else 0 for f in fields[1:]]
        nums += [0] * (5 - len(nums))
        automode = nums[0] if len(fields) > 1 else 1
        return eicon_idi.select_modulation(name, automode=automode,
                                           min_rx=nums[1], max_rx=nums[2],
                                           min_tx=nums[3], max_tx=nums[4])
    if FORCE_V34:
        # Historic behaviour: the V.90 disable bit and a 33600 ceiling, with
        # nothing else touched.  EICON_MODULATION=v34,1,,33600,,33600 is the
        # driver-faithful version of the same intent.
        opts = eicon_idi.legacy_modem_options(33600)
        opts.disabled = eicon_idi.DSP_CAI_MODEM_DISABLE_V90
        return opts
    return eicon_idi.legacy_modem_options()


# Re-exported so the existing call sites and any external harness keep
# working; the construction now lives in eicon_idi.
idi_parameters = eicon_idi.idi_parameters


def modem_cai(max_bit_rate: int = 56000,
              b1_resource: int = DSP_CAI_HARDWARE_MODEM_ASYNC,
              b1_options: int = 0) -> bytes:
    """The CAI add_b1()/putcai() build for a modem B1 protocol.

    Offsets follow the driver's cai[] array, whose [0] is the length byte
    add_p() strips off, so data[i] here is the driver's cai[i+1].
    """
    opts = modem_options()
    if max_bit_rate != 56000:
        # A caller-supplied ceiling clamps the configuration without editing
        # it: modem_options() may be returning the shared override the AT
        # layer installed, and mutating that would leak into the next call.
        opts = eicon_idi.ModemOptions(**vars(opts))
        opts.max_tx = min(opts.max_tx or max_bit_rate, max_bit_rate)
        opts.max_rx = min(opts.max_rx or max_bit_rate, max_bit_rate)
    cai = bytearray(eicon_idi.build_cai(opts, b1_resource=b1_resource))
    cai[3] = b1_options & 0xFF   # cai[4]: B1 options
    return bytes(cai)


def modem_sig_assign_payload(max_bit_rate: int = 56000) -> bytes:
    """Signalling-entity ASSIGN payload: the CAI, as add_b1() attaches it."""
    return idi_parameters((IDI_CAI, modem_cai(max_bit_rate)),
                          (IDI_UID, b"Capi20"))


def modem_call_res_payload(max_bit_rate: int = 56000) -> bytes:
    """CAPI20 ``connect_res()`` modem payload produced by ``add_b1()``.

    The old i4l IDI compatibility path used a six-byte CAI, but Eicon's
    CAPI20 hardware path attaches the complete 26-byte modem descriptor to
    CALL_RES.  This is the transaction whose private DSP effects the native
    ingress experiment needs to preserve.
    """
    return idi_parameters((IDI_CAI, modem_cai(max_bit_rate)))


def modem_nl_assign_payload(max_data_length: int = 1024,
                            answering: bool = True,
                            signaling_id: "int | None" = None,
                            error_control: "bool | None" = None) -> bytes:
    """Network-layer ASSIGN payload, as add_modem_b23()/send_req() build it.

    ``error_control`` (or ``EICON_CARD_V42=1``) switches the LLC from
    B2_TRANSPARENT to B2_V42 and drops the DLC that disables V.42/V.42bis, so
    the card runs its own error control instead of ``v42_lapm.LapmEndpoint``.
    That moves the data path off the synchronous pump onto the protocol page
    and has never been tried against hardware; the default is unchanged.
    """
    if error_control is None:
        error_control = CARD_V42
    return eicon_idi.nl_assign_payload(max_data_length=max_data_length,
                                       answering=answering,
                                       signaling_id=signaling_id,
                                       error_control=error_control,
                                       options=modem_options())


# The decode moved to eicon_idi, which also names the error return codes the
# old version reported as "?" -- WRONG_IE and OUT_OF_RESOURCES in particular,
# both of which have shown up on rejected ASSIGNs here.
rc_name = eicon_idi.rc_name


def clear_host_doorbell(shim: "MipsShim") -> None:
    """Acknowledge the card->host notification, as a host ISR would.

    The main loop only calls the RC/IND flush (0x80029774) when RcOutput and
    IndOutput are both zero *and* the byte pointed at by gp+0x5eaf is zero
    (0x80027d84).  The flush sets that byte to 1 on its way out
    (0x8002989c), so leaving it set stops the firmware publishing any further
    return codes: the RC sits queued in card RAM with gp+0x5e9d stuck at 1.
    """
    ptr = struct.unpack_from("<I",
        shim.uc.mem_read((GP + 0x5eaf) & 0x1fffffff, 4))[0]
    if ptr:
        shim.write8(ptr, 0)


def drain_return_codes(shim: "MipsShim", sr: int) -> "list[tuple[int, int, int, int]]":
    """Consume the queued return codes the way pr_rc() (kernel/di.c) does.

    Walk `RcOutput` entries from `B[NextRc]` along the chain, zero each Rc
    field as it is taken, then clear `RcOutput`.  Returns
    (Rc, RcId, RcCh, Reference) tuples.
    """
    rc_out = shim.uc.mem_read(sr + PR_RcOutput, 1)[0]
    if not rc_out:
        return []
    clear_host_doorbell(shim)
    off = struct.unpack_from("<H", shim.uc.mem_read(sr + PR_NextRc, 2))[0]
    out = []
    for _ in range(rc_out):
        rb = sr + PR_B + off
        rc = shim.uc.mem_read(rb + RC_RC, 1)[0]
        if rc:
            out.append((rc,
                        shim.uc.mem_read(rb + RC_RCID, 1)[0],
                        shim.uc.mem_read(rb + RC_RCCH, 1)[0],
                        struct.unpack_from("<H",
                            shim.uc.mem_read(rb + RC_REFERENCE, 2))[0]))
            shim.write8(rb + RC_RC, 0)
        off = struct.unpack_from("<H", shim.uc.mem_read(rb, 2))[0]
    shim.write8(sr + PR_RcOutput, 0)
    return out


def drain_indications(shim: "MipsShim", sr: int) -> list[tuple[int, int, int, int, bytes]]:
    """Consume card indications using the PR_RAM IND chain."""
    count = shim.uc.mem_read(sr + PR_IndOutput, 1)[0]
    if not count:
        return []
    clear_host_doorbell(shim)
    off = struct.unpack_from("<H", shim.uc.mem_read(sr + PR_NextInd, 2))[0]
    out = []
    for _ in range(count):
        rb = sr + PR_B + off
        length = struct.unpack_from("<H",
            shim.uc.mem_read(rb + IND_RBUFFER, 2))[0]
        out.append((
            shim.uc.mem_read(rb + IND_IND, 1)[0],
            shim.uc.mem_read(rb + IND_ID, 1)[0],
            shim.uc.mem_read(rb + IND_CH, 1)[0],
            struct.unpack_from("<H",
                shim.uc.mem_read(rb + IND_REFERENCE, 2))[0],
            bytes(shim.uc.mem_read(rb + IND_RDATA, length)),
        ))
        shim.write8(rb + IND_IND, 0)
        off = struct.unpack_from("<H", shim.uc.mem_read(rb, 2))[0]
    shim.write8(sr + PR_IndOutput, 0)
    return out


def post_request(shim: "MipsShim", sr: int, req: int, req_id: int,
                 req_ch: int, payload: bytes, reference: int = 0) -> int:
    """Put one IDI request in the queue, as pr_out() (kernel/di.c) does.

    Fill the REQ at B[NextReq], advance NextReq to REQ->next, and increment
    the host-owned ReqInput counter.  Returns the buffer offset used.
    """
    off = struct.unpack_from("<H", shim.uc.mem_read(sr + PR_NextReq, 2))[0]
    rb = sr + PR_B + off
    req_next = struct.unpack_from("<H", shim.uc.mem_read(rb + REQ_NEXT, 2))[0]
    shim.write8(rb + REQ_REQ, req)
    shim.write8(rb + REQ_REQID, req_id)
    shim.write8(rb + REQ_REQCH, req_ch)
    shim.write16(rb + REQ_REFERENCE, reference)
    shim.write16(rb + REQ_XBUFFER, len(payload))
    shim.uc.mem_write(rb + REQ_XDATA, payload)
    shim.write16(sr + PR_NextReq, req_next)
    req_in = shim.uc.mem_read(sr + PR_ReqInput, 1)[0]
    shim.write8(sr + PR_ReqInput, (req_in + 1) & 0xFF)
    return off


def run_until_rc(shim: "MipsShim", sr: int, gp: int, sp: int,
                 iterations: int = 32, phase: str = "mainloop") -> "list[tuple[int, int, int, int]]":
    """Spin the main loop until the firmware queues a return code."""
    for _ in range(iterations):
        try:
            shim.phase = phase
            shim.call(MIPS_MAINLOOP, [], gp=gp, sp=sp, max_insns=500000)
        except Exception as exc:
            print(f"[mainloop] fault: {exc}")
            break
        if shim.uc.mem_read(sr + PR_RcOutput, 1)[0]:
            break
    return drain_return_codes(shim, sr)


def assign_entity(shim: "MipsShim", sr: int, gp: int, sp: int, label: str,
                  req_id: int, req_ch: int, payload: bytes) -> "int | None":
    """Post one ASSIGN and report its return code.

    Returns the local entity id the card assigned on ASSIGN_OK, else None.
    """
    off = post_request(shim, sr, ASSIGN, req_id, req_ch, payload,
                       reference=1 if label == "nl" else 0)
    print(f"[{label}] ASSIGN Id=0x{req_id:02x} Ch=0x{req_ch:02x} "
          f"@B[0x{off:04x}] payload={payload.hex()}")
    codes = run_until_rc(shim, sr, gp, sp, phase=f"{label}-assign")
    if not codes:
        print(f"[{label}] no return code")
        return None
    assigned = None
    for rc, rc_id, rc_ch, ref in codes:
        print(f"[{label}] RC 0x{rc:02x} ({rc_name(rc)}) Id=0x{rc_id:02x} "
              f"Ch=0x{rc_ch:02x} Ref=0x{ref:04x}")
        if rc == ASSIGN_OK and assigned is None:
            assigned = rc_id
    return assigned


def install_call_hooks(shim: "MipsShim", spec: str) -> None:
    """Log entries to the named MIPS addresses with their arguments.

    One single-address UC_HOOK_CODE each, for the reason INTERCEPT_ADDRESSES
    gives: a hook spanning all code makes every instruction a Python call.
    """
    from unicorn import UC_HOOK_CODE
    from unicorn.mips_const import (UC_MIPS_REG_A0, UC_MIPS_REG_A1,
                                    UC_MIPS_REG_A2, UC_MIPS_REG_A3,
                                    UC_MIPS_REG_RA, UC_MIPS_REG_V0,
                                    UC_MIPS_REG_S4)

    counts: "dict[int, int]" = {}

    def on_call(uc, address, size, user_data):
        counts[address] = counts.get(address, 0) + 1
        a0, a1, a2, a3, ra, v0, s4 = (uc.reg_read(r) for r in
                                      (UC_MIPS_REG_A0, UC_MIPS_REG_A1,
                                       UC_MIPS_REG_A2, UC_MIPS_REG_A3,
                                       UC_MIPS_REG_RA, UC_MIPS_REG_V0,
                                       UC_MIPS_REG_S4))
        print(f"[hookcall] 0x{address:08x} #{counts[address]} "
              f"a0=0x{a0:08x} a1=0x{a1:08x} a2=0x{a2:08x} a3=0x{a3:08x} "
              f"v0=0x{v0:08x} s4=0x{s4:08x} ra=0x{ra:08x} ({shim.phase})")

    for text in spec.split(","):
        if not text.strip():
            continue
        address = int(text, 0)
        # Unmasked, like INTERCEPT_ADDRESSES: Unicorn's PC stays in kseg0 even
        # though memory is mapped at the physical equivalents, so a code hook
        # wants the virtual address while a write hook reports physical ones.
        shim.uc.hook_add(UC_HOOK_CODE, on_call, begin=address, end=address)
        print(f"[hookcall] hooking 0x{address:08x}")


def install_mem_watch(shim: "MipsShim", spec: str) -> None:
    """Log firmware writes into an address range, with the writing PC.

    `spec` is ADDR[:LEN], virtual (kseg0 is translated the way the rest of
    this file does it). --scan-ram says where a value ended up; this says
    which instruction put it there, which is the half that names the code.
    """
    from unicorn import UC_HOOK_MEM_WRITE
    from unicorn.mips_const import UC_MIPS_REG_PC, UC_MIPS_REG_RA

    text, _, length = spec.partition(":")
    begin = int(text, 0) & 0x1FFFFFFF
    size = int(length, 0) if length else 4
    end = begin + size - 1

    def on_write(uc, access, address, size, value, user_data):
        pc = uc.reg_read(UC_MIPS_REG_PC)
        ra = uc.reg_read(UC_MIPS_REG_RA)
        print(f"[memwatch] write {size}B = 0x{value:08x} to phys "
              f"0x{address:08x} from PC 0x{pc:08x} ra=0x{ra:08x} "
              f"({shim.phase})")

    shim.uc.hook_add(UC_HOOK_MEM_WRITE, on_write, begin=begin, end=end)
    print(f"[memwatch] watching phys 0x{begin:08x}..0x{end:08x}")


def scan_ram(shim: "MipsShim", needle: bytes) -> None:
    """Report every emulated-RAM address holding `needle`.

    Used to find where the firmware assembled something whose contents we
    chose -- a dialled number in a Q.931 SETUP, for instance. Skips the
    request buffers, since the host wrote the digits there itself and finding
    our own copy proves nothing.
    """
    regions = ((PHYS_BIAS, IMAGE_SIZE, "image"),
               (RAM_BASE, RAM_SIZE, "ram"),
               (STUB_BASE, 0x10000, "stub"))
    total = 0
    for base, size, label in regions:
        try:
            blob = bytes(shim.uc.mem_read(base, size))
        except Exception as exc:
            print(f"[scan] {label}: unreadable ({exc})")
            continue
        start = 0
        while True:
            hit = blob.find(needle, start)
            if hit < 0:
                break
            start = hit + 1
            total += 1
            context = blob[max(0, hit - 48):hit + len(needle) + 48]
            print(f"[scan] {needle!r} at {label}+0x{hit:06x} "
                  f"(phys 0x{base + hit:08x}) context={context.hex()}")
    print(f"[scan] {total} occurrence(s) of {needle!r}")


def make_call_control(shim: "MipsShim", sr: int, gp: int, sp: int,
                      phase: str = "idi") -> eicon_idi.IdiCallControl:
    """Wrap this shim's PR_RAM queues as an eicon_idi transport.

    The requests and their ordering are unchanged -- this is the same
    post_request/run_until_rc/drain_indications underneath.  What it adds is
    that the codes get named, the CALL_IND channel and the calling number are
    recorded rather than re-derived at each use, and the call state is
    somewhere the AT layer can read it.
    """
    def post(req, entity_id, channel, payload, reference):
        off = post_request(shim, sr, req, entity_id, channel, payload,
                           reference=reference)
        print(f"[idi] REQ {eicon_idi.code_name(req, 'sig')} "
              f"Id=0x{entity_id:02x} Ch=0x{channel:02x} @B[0x{off:04x}] "
              f"payload={payload.hex()}")

    def pump():
        codes = run_until_rc(shim, sr, gp, sp, phase=phase)
        indications = [
            eicon_idi.Indication(ind, ind_id, ind_ch, ref, payload)
            for ind, ind_id, ind_ch, ref, payload
            in drain_indications(shim, sr)]
        return codes, indications

    return eicon_idi.IdiCallControl(post, pump)


def issue_listen_request(shim: "MipsShim", sr: int, gp: int, sp: int,
                         sig_id: int, legacy_req_id: bool = False,
                         control: "eicon_idi.IdiCallControl | None" = None
                         ) -> "eicon_idi.IdiCallControl":
    """Put the assigned signalling entity into incoming-call listening state.

    The old i4l driver names this host operation INDICATE_REQ even though the
    firmware-side CAPI state machine talks about LISTEN_REQ.  Its payload is a
    one-byte zero parameter block (idi_put_req()), and it must happen before a
    CALL_IND can exist for CALL_RES to answer.
    """
    if control is None:
        control = make_call_control(shim, sr, gp, sp, phase="listen-req")
    control.entities["sig"] = sig_id
    codes, indications = control.listen(legacy_req_id=legacy_req_id)
    for rc, rc_id, rc_ch, ref in codes:
        print(f"[listen] RC 0x{rc:02x} ({rc_name(rc)}) Id=0x{rc_id:02x} "
              f"Ch=0x{rc_ch:02x} Ref=0x{ref:04x}")
    for indication in indications:
        print(f"[listen] {indication}")
    return control


def read_runtime32(shim: "MipsShim", addr: int) -> int:
    return struct.unpack_from("<I",
        shim.uc.mem_read(addr & 0x1fffffff, 4))[0]


def dump_entities(shim: "MipsShim", gp: int, limit: int = 16) -> None:
    count = struct.unpack_from("<H",
        shim.uc.mem_read((gp + 0x5eb9) & 0x1fffffff, 2))[0]
    print(f"[entities] count={count} table=0x{ENTITY_TABLE:08x}")
    for idx in range(min(count, limit)):
        ptr = read_runtime32(shim, ENTITY_TABLE + idx * 4)
        if ptr == 0:
            continue
        words = []
        for off in range(0, 0x30, 4):
            words.append(read_runtime32(shim, ptr + off))
        formatted = " ".join(f"+{i * 4:02x}={word:08x}"
                             for i, word in enumerate(words))
        print(f"[entities] {idx:02x}: ptr=0x{ptr:08x} {formatted}")
        call = read_runtime32(shim, ptr + 0x1c)
        if call:
            sig_fields = bytes(shim.uc.mem_read((ptr + 0x340) & 0x1fffffff, 0x1f0))
            call_fields = bytes(shim.uc.mem_read(call & 0x1fffffff, 0x240))
            print(f"[entities] {idx:02x}: sig+340..52f={sig_fields.hex()}")
            print(f"[entities] {idx:02x}: call[0..23f]={call_fields.hex()}")


def inject_call_ingress(shim: "MipsShim", gp: int, sp: int,
                        slot: int = 0) -> None:
    """Inject a network-originated SETUP into the real signalling parser.

    ``0x800172c0`` obtains the current message type from ``gp+0x5e87`` and,
    when ``gp+0x5e88`` is zero, parses the length/data block selected by
    ``gp+0x5ecf``.  This is the same interface the lower PRI/SIG dispatcher
    establishes before calling the controller object's handler.  Event
    ``0x17`` is the no-call-state jump-table entry that allocates the incoming
    call object; event 2 only updates bearer-status flags.
    """
    sig_obj = read_runtime32(shim, ENTITY_TABLE + slot * 4)
    if sig_obj == 0:
        print(f"[ingress] no signalling object in slot {slot}")
        return

    # IDI/Q.931 information elements consumed by the parser: 3.1-kHz audio
    # bearer capability, V.42 low-layer compatibility, and a minimal channel
    # identification.  The parser uses the ordinary code,length,data form.
    payload = idi_parameters(
        (IDI_BC, bytes((0x90, 0x90, 0xa3))),
        (IDI_LLC, bytes((0x88, 0x90, 0x21))),
        (0x18, bytes((0xa1, 0x83))),
    )
    message = SYNTH_INGRESS_MESSAGE + slot * 0x100
    shim.alloc(message, 0x100)
    for off in range(0, 0x100, 4):
        shim.write32(message + off, 0)
    shim.write16(message + 0x10, len(payload))
    shim.write_bytes(message + 0x12, payload)

    entity_id = shim.uc.mem_read((sig_obj + 0x14) & 0x1fffffff, 1)[0]
    shim.write32(gp + 0x5ecf, message)
    shim.write8(gp + 0x5e88, 0x00)  # use the gp+0x5ecf message block
    shim.write8(gp + 0x5eab, entity_id)

    # The PRI dispatcher reports a new call first (0x17), causing state 0 to
    # allocate the call object, then delivers SETUP indication 0x0b to the new
    # call-state handler.  Calling only the first event leaves a correctly
    # allocated but never indicated call.
    for event, label in ((0x17, "allocate"), (0x0b, "SETUP")):
        shim.write8(gp + 0x5e87, event)
        before = read_runtime32(shim, sig_obj + 0x1c)
        print(f"[ingress] {label} event 0x{event:02x} on controller slot "
              f"{slot} obj=0x{sig_obj:08x} entity=0x{entity_id:02x} "
              f"message=0x{message:08x} payload={payload.hex()} "
              f"before +1c=0x{before:08x}")
        try:
            shim.phase = f"call-ingress-{label.lower()}"
            shim.call(CALL_INGRESS_PARSER, [sig_obj], gp=gp, sp=sp,
                      max_insns=2000000)
        except Exception as exc:
            print(f"[ingress] firmware {label} parser stopped: {exc}")
        after = read_runtime32(shim, sig_obj + 0x1c)
        state = (shim.uc.mem_read((after + 0x2c) & 0x1fffffff, 1)[0]
                 if after else 0)
        print(f"[ingress] controller slot {slot} after {label}: "
              f"+1c=0x{after:08x} call_state=0x{state:02x}")
        if event == 0x17 and after:
            # The lower D-channel dispatcher advances the newly allocated
            # controller from allocation state 1 to pending-incoming state 2
            # before delivering the decoded SETUP.  0x8002a89c treats state 2
            # as an already network-owned call; leaving state 1 makes it try
            # to allocate an outgoing B-channel and reject the SETUP.
            shim.write16(sig_obj + 0x24, 2)
            shim.write16(sig_obj + 0x26, 1)
            flags = read_runtime32(shim, sig_obj + 0x20)
            shim.write32(sig_obj + 0x20, flags | 0x00400000)


def inject_call_connect(shim: "MipsShim", gp: int, sp: int,
                        slot: int = 0, event: int = 0x03) -> None:
    """Deliver the network's CONNECT for a call this side originated.

    The answering path injects SETUP (0x17 then 0x0b) because no network
    sends one; an outgoing call has the mirror-image problem, in that nothing
    will ever answer the SETUP the card just put on the wire.  Event 0x03 is
    the connected event the answering path already delivers after CALL_RES,
    and it is the same lower-PRI interface: the object is the one CALL_REQ
    allocated, so no allocation event is needed first.

    The call object is reached through the signalling entity's +0x1c, which
    CALL_REQ fills in; if it is still null the request never allocated one and
    delivering CONNECT would be meaningless, so this says so and returns.
    """
    sig_obj = read_runtime32(shim, ENTITY_TABLE + slot * 4)
    if sig_obj == 0:
        print(f"[connect] no signalling object in slot {slot}")
        return
    call_obj = read_runtime32(shim, sig_obj + 0x1c)
    if call_obj == 0:
        print(f"[connect] slot {slot} has no call object at +0x1c; CALL_REQ "
              "did not allocate one, so there is nothing to connect")
        return

    entity_id = shim.uc.mem_read((sig_obj + 0x14) & 0x1fffffff, 1)[0]
    message = SYNTH_INGRESS_MESSAGE + slot * 0x100
    shim.alloc(message, 0x100)
    for off in range(0, 0x100, 4):
        shim.write32(message + off, 0)
    # A CONNECT carries no information elements this parser needs; the
    # channel identification the network would return is already fixed by the
    # outgoing request.
    shim.write16(message + 0x10, 1)
    shim.write_bytes(message + 0x12, b"\x00")
    shim.write32(gp + 0x5ecf, message)
    shim.write8(gp + 0x5e88, 0x00)
    shim.write8(gp + 0x5eab, entity_id)
    shim.write8(gp + 0x5e87, event & 0xFF)

    state_before = shim.uc.mem_read((call_obj + 0x2c) & 0x1fffffff, 1)[0]
    print(f"[connect] event 0x{event:02x} on slot {slot} obj=0x{sig_obj:08x} "
          f"call=0x{call_obj:08x} entity=0x{entity_id:02x} "
          f"call_state=0x{state_before:02x}")
    try:
        shim.phase = "call-connect"
        shim.call(CALL_INGRESS_PARSER, [sig_obj], gp=gp, sp=sp,
                  max_insns=2_000_000)
    except Exception as exc:
        print(f"[connect] firmware CONNECT parser stopped: {exc}")
    state_after = shim.uc.mem_read((call_obj + 0x2c) & 0x1fffffff, 1)[0]
    print(f"[connect] call_state 0x{state_before:02x} -> 0x{state_after:02x}")


def synthesize_call_ingress(shim: "MipsShim", slot: int = 0) -> None:
    """Fabricate the minimum incoming-call object needed before CALL_RES.

    This mirrors the field writes in the 0x800172a8 allocation branch without
    depending on the surrounding Q.931 dispatcher frame: the listening SIG
    entity gains a call object at +0x1c, enters pending-call state, and the
    call object points back at its owning SIG entity.
    """
    sig_obj = read_runtime32(shim, ENTITY_TABLE + slot * 4)
    if sig_obj == 0:
        print(f"[ingress] no entity object in slot {slot}")
        return
    call_obj = SYNTH_CALL_OBJECT + slot * 0x100
    shim.alloc(call_obj, 0x100)
    for off in range(0, 0x100, 4):
        shim.write32(call_obj + off, 0)
    flags = read_runtime32(shim, sig_obj + 0x20) & 0xfffeffff
    shim.write8(call_obj + 0x2f, 1)
    shim.write32(call_obj + 0x28, sig_obj)
    # The allocation branch stores state 1 first; the real SETUP parser then
    # progresses the PLCI into the pending incoming-call state before CALL_RES.
    shim.write16(sig_obj + 0x24, 2)
    shim.write8(sig_obj + 0x12a, 1)
    shim.write32(sig_obj + 0x1c, call_obj)
    shim.write32(sig_obj + 0x20, flags)
    # Parsed SETUP fields used by the incoming answer path.  These are
    # length-prefixed internal copies of the Q.931 BC/LLC/HLC/channel IEs.
    # BC 90 90 a3 = 3.1 kHz audio, 64 kbit/s, G.711 A-law; LLC 88 90 21
    # selects V.42/modem-style low-layer handling in the IDI firmware.
    shim.write_bytes(sig_obj + 0x365, bytes((4, 0x90, 0x90, 0xa3, 0x00)))
    shim.write_bytes(sig_obj + 0x37d, bytes((4, 0x88, 0x90, 0x21, 0x00)))
    shim.write_bytes(sig_obj + 0x395, bytes((1, 0x80)))
    shim.write8(sig_obj + 0x51f, 0xff)
    shim.write8(sig_obj + 0x520, 0x11)
    print(f"[ingress] synthetic call object 0x{call_obj:08x} "
          f"linked to entity slot {slot} obj=0x{sig_obj:08x}")


def run_mainloop(shim: "MipsShim", args) -> None:
    """Drive the MIPS via its native PR_RAM request queue (the real host
    interface).  Maps shared RAM, runs the MIPS init, sets up the PR_RAM
    buffer chain, writes a modem ASSIGN request, and calls the main loop.

    This is the path the Linux driver uses: the host writes IDI requests
    to PR_RAM, the MIPS polls and dispatches them, calling dsp_assign
    and downloading DSP overlays internally.
    """
    import json
    metadata = json.loads((args.tikrnl / "metadata.json").read_text())
    write13 = symbol_host_address(metadata, 13)
    write14 = symbol_host_address(metadata, 14)

    gp = GP
    sp = STACK_TOP

    # 0. Stage the DSP code image, as the host driver's pri_telindus_load
    #    does, and publish its address in the protocol image header.  The
    #    firmware entry reads the count at DspCodeBaseAddr and the descriptor
    #    table right after it; with no image the count is 0, every DSP object
    #    is built with an empty code table and no overlay can be assigned.
    # Each DSP register block gets its own emulated ADSP, all held in IDMA
    # boot mode: the firmware downloads a kernel into every one of them and
    # a shared core would see each download land in the previous DSP's
    # running image.
    shim.multi_dsp = True
    # The DSPs have to run in line with the MIPS: the validator writes 0x5a5a
    # to a DSP, downloads its kernel, releases it, then polls for 0xa5a5
    # within one call, and without that acknowledgement no DSP resources are
    # registered.  IDMA boot hold keeps each core stopped for its own
    # download, so interleaving is safe.
    shim.pump_every = args.dsp_pump

    if args.dsp_combifile is not None:
        stage_dsp_code(shim, args)

    # 1. Write the card config and boot command the firmware entry reads
    #    during init.  The request queue is NOT set up here: the firmware
    #    initialises PR_RAM itself as it boots and publishes its signature
    #    when ready, so a request written beforehand is overwritten.
    sr = PR_RAM_PHYS
    shim.write8(sr + 0x08, 0)      # TEI (0 = auto)
    shim.write8(sr + 0x10, args.force_law)  # 1=A-law, 2=mu-law
    shim.write8(sr + 0x16, 0x80)    # DSPInfo = DSP code loaded
    # The protocol image and staged combifile must agree on card identity.
    # Hardcoding legacy value 12 while selecting the PRI-30M file set (23)
    # lets basic signalling run but bypasses the matching DSP resource path.
    shim.write8(sr + 0x1a, args.card_type)
    shim.write8(sr + 0xe0, 0)      # PCINIT_END_OF_LIST
    shim.write32(0x00, 3)          # boot->cmd = 3 (start)
    shim.write32(0x04, 0xa0011000) # boot->addr
    print("[mainloop] card config + boot command written")

    # 2. Call the firmware entry (0x80082f90) to store the PR_RAM pointer
    #    and run basic init.  It reaches a self-loop waiting for a hardware
    #    interrupt; the instruction count stops it there.  The PR_RAM
    #    pointer (gp+0x5e93) is now set.
    print("[mainloop] running firmware entry (basic init)...")
    try:
        shim.phase = "entry"
        shim.call(MIPS_ENTRY, [], gp=gp, sp=sp, max_insns=5000000)
    except Exception as exc:
        print(f"[mainloop] entry stopped at self-loop: {exc}")

    # 3. The self-loop at 0x800830ec waits for a hardware interrupt.  In real
    #    hardware the host triggers this after writing config + boot command.
    #    Skip it and call the post-wait init + main loop directly.
    #    0x80083100 calls 0x80083d10 (init), 0x8002a534 (init), then loops
    #    on 0x80027970 (main loop).  Call the two init functions, then the
    #    main loop separately.
    MIPS_POST_INIT1 = BIAS + 0x72d10   # 0x80083d10
    MIPS_POST_INIT2 = BIAS + 0x1a534   # 0x8002a534
    print("[mainloop] running post-wait init functions...")
    try:
        shim.phase = "post-init1"
        shim.call(MIPS_POST_INIT1, [], gp=gp, sp=sp, max_insns=2000000)
    except Exception as exc:
        print(f"[mainloop] init1 fault: {exc}")
    try:
        shim.phase = "post-init2"
        shim.call(MIPS_POST_INIT2, [], gp=gp, sp=sp, max_insns=2000000)
    except Exception as exc:
        print(f"[mainloop] init2 fault: {exc}")

    # Check if DSP resources were registered
    dsp_table = struct.unpack_from("<H",
        shim.uc.mem_read((gp + 0x5eb9) & 0x1fffffff, 2))[0]
    init_state = struct.unpack_from("<H",
        shim.uc.mem_read((gp + 0x5e81) & 0x1fffffff, 2))[0]
    print(f"[mainloop] after init: gp+0x5e81={init_state:#06x} "
          f"gp+0x5eb9={dsp_table:#06x}")
    report_dsp_boot(shim)

    # 3b. Assign the entities now that the firmware has initialised PR_RAM.
    #     The driver's order is signalling first (sig_req(plci, ASSIGN,
    #     DSIG_ID), carrying the CAI from add_b1()), then the network layer
    #     (nl_req_ncci(plci, ASSIGN, 0), carrying LLI/LLC/DLC from
    #     add_modem_b23()).  Each ASSIGN is answered with an ASSIGN_RC
    #     carrying the local entity id the card allocated.
    sig = struct.unpack_from("<H", shim.uc.mem_read(sr + PR_Signature, 2))[0]
    print(f"[mainloop] card ready: Sig=0x{sig:04x}")
    # From this point onward report the whole call lifecycle. MipsShim.call()
    # normally resets this diagnostic per helper invocation, which made a
    # connected call incorrectly finish with "host_writes=0" whenever its
    # final main-loop iteration happened to be idle.
    shim.host_writes = []
    shim.preserve_host_writes = True
    shim.nl_connected = False

    assigned = {}
    if args.entity in ("sig", "both"):
        steps = (("sig", DSIG_ID, modem_sig_assign_payload()),)
    else:
        steps = ()
    for label, req_id, payload in steps:
        entity_id = assign_entity(shim, sr, gp, sp, label, req_id,
                                  args.channel, payload)
        if entity_id is None:
            print(f"[{label}] assign did not succeed; stopping the sequence")
            break
        assigned[label] = entity_id
        print(f"[{label}] entity id 0x{entity_id:02x} assigned "
              f"(host_writes={len(shim.host_writes)})")

    defer_nl_assign = (
        args.fake_call_ingress
        and args.call_direction == "answering"
        and args.entity in ("nl", "both")
    )

    if args.entity in ("nl", "both") and not defer_nl_assign:
        signaling_id = assigned.get("sig")
        payload = modem_nl_assign_payload(
            answering=args.call_direction == "answering",
            signaling_id=signaling_id)
        entity_id = assign_entity(shim, sr, gp, sp, "nl", NL_ID,
                                  args.channel, payload)
        if entity_id is None:
            print("[nl] assign did not succeed; stopping the sequence")
        else:
            assigned["nl"] = entity_id
            print(f"[nl] entity id 0x{entity_id:02x} assigned "
                  f"(host_writes={len(shim.host_writes)})")

    call_channel = 0
    control = make_call_control(shim, sr, gp, sp)
    control.entities.update(assigned)
    if args.fake_call_ingress and "sig" in assigned:
        issue_listen_request(shim, sr, gp, sp, assigned["sig"],
                             legacy_req_id=args.legacy_sig_req_id,
                             control=control)
        if args.inject_call_ingress:
            inject_call_ingress(shim, gp, sp, args.ingress_entity_slot)
            # The real SETUP parser emits CALL_IND through PR_RAM.  Its Ch is
            # the per-call selector that must be echoed by CALL_RES; Ch=0
            # answers the listener and bypasses the allocated call object.
            for ind, ind_id, ind_ch, ref, payload in drain_indications(shim, sr):
                indication = eicon_idi.Indication(ind, ind_id, ind_ch, ref,
                                                  payload)
                control.indications.append(indication)
                control.observe(indication)
                print(f"[ingress] {indication}")
                if ind == eicon_idi.CALL_IND:
                    call_channel = ind_ch
        if args.synthesize_call_ingress:
            synthesize_call_ingress(shim, args.ingress_entity_slot)
        if args.dump_entities:
            dump_entities(shim, gp, args.dump_entity_limit)

    if defer_nl_assign:
        signaling_id = assigned.get("sig")
        payload = modem_nl_assign_payload(
            answering=True,
            signaling_id=signaling_id)
        entity_id = assign_entity(shim, sr, gp, sp, "nl", NL_ID,
                                  args.channel, payload)
        if entity_id is None:
            print("[nl] assign did not succeed after fake ingress")
        else:
            assigned["nl"] = entity_id
            print(f"[nl] entity id 0x{entity_id:02x} assigned after ingress "
                  f"(host_writes={len(shim.host_writes)})")
            if args.dump_entities:
                dump_entities(shim, gp, args.dump_entity_limit)

    if defer_nl_assign:
        # NL ASSIGN gives the asynchronous lower SETUP path enough main-loop
        # turns to publish CALL_IND.  Consume it before CALL_RES, matching the
        # real host ordering and releasing PR_RAM indication flow control.
        for ind, ind_id, ind_ch, ref, payload in drain_indications(shim, sr):
            print(f"[ingress] IND 0x{ind:02x} Id=0x{ind_id:02x} "
                  f"Ch=0x{ind_ch:02x} Ref=0x{ref:04x} "
                  f"payload={payload.hex()}")
            if ind == 0x02:
                call_channel = ind_ch

    if args.connect and "nl" in assigned:
        bearer_disconnected = False
        if args.call_direction == "calling" and "sig" in assigned:
            # isdnDial() (tty_module/isdn.c:1952): CALL_REQ carries the same
            # modem CAI the ASSIGN did, plus the addresses and the codeset-6
            # service pair.  The outgoing call object is allocated by this
            # request, so unlike the answering path there is no event 0x17
            # network-originated allocation to inject first.
            call_payload = eicon_idi.call_req_payload(
                args.dial_number, origination=args.dial_origination,
                options=modem_options())
            off = post_request(shim, sr, eicon_idi.CALL_REQ, assigned["sig"],
                               call_channel, call_payload, reference=0)
            print(f"[call] CALL_REQ Id=0x{assigned['sig']:02x} "
                  f"to {args.dial_number!r} @B[0x{off:04x}] "
                  f"payload={call_payload.hex()}")
            for rc, rc_id, rc_ch, ref in run_until_rc(shim, sr, gp, sp,
                                                      phase="call-req"):
                print(f"[call] RC 0x{rc:02x} ({rc_name(rc)}) "
                      f"Id=0x{rc_id:02x} Ch=0x{rc_ch:02x} Ref=0x{ref:04x}")
            for indication in [
                    eicon_idi.Indication(ind, ind_id, ind_ch, ref, payload)
                    for ind, ind_id, ind_ch, ref, payload
                    in drain_indications(shim, sr)]:
                control.indications.append(indication)
                control.observe(indication)
                print(f"[call] {indication}")
            if args.inject_call_ingress:
                # There is no network to answer the SETUP this just sent, so
                # the far end's CONNECT has to be delivered the same way the
                # answering path delivers its post-CALL_RES event: straight
                # into the lower-PRI signalling parser against the call
                # object CALL_REQ allocated.
                inject_call_connect(shim, gp, sp, args.ingress_entity_slot,
                                    event=args.connect_event)
        if args.call_direction == "answering" and "sig" in assigned:
            # message.c connect_res(): add_b1() appends the modem CAI to the
            # CALL_RES itself. The initial SIG ASSIGN only creates the PLCI;
            # an empty CALL_RES answers signalling but never allocates the
            # 0x0258 modem DSP service.
            call_payload = modem_call_res_payload()
            off = post_request(shim, sr, CALL_RES, assigned["sig"],
                               call_channel, call_payload, reference=0)
            print(f"[call] CALL_RES Id=0x{assigned['sig']:02x} "
                  f"Ch=0x{call_channel:02x} @B[0x{off:04x}]")
            for rc, rc_id, rc_ch, ref in run_until_rc(shim, sr, gp, sp,
                                                      phase="call-res"):
                print(f"[call] RC 0x{rc:02x} ({rc_name(rc)}) "
                      f"Id=0x{rc_id:02x} Ch=0x{rc_ch:02x} Ref=0x{ref:04x}")
            if (args.inject_call_ingress and
                    getattr(args, "native_bearer_activation", False)):
                # SETUP allocation is followed by a distinct lower-PRI
                # connected event after an answering CALL_RES. It publishes
                # CONNECT_ACTIVE and installs the selected bearer state.
                sig_obj = read_runtime32(shim, ENTITY_TABLE +
                                          args.ingress_entity_slot * 4)
                shim.write8(gp + 0x5e87, 0x03)
                shim.phase = "call-ingress-connected"
                shim.call(CALL_INGRESS_PARSER, [sig_obj], gp=gp, sp=sp,
                          max_insns=2_000_000)
                print("[ingress] delivered post-CALL_RES event 0x03")
            if args.dump_entities:
                dump_entities(shim, gp, args.dump_entity_limit)
        off = post_request(shim, sr, N_CONNECT, assigned["nl"], 0,
                           b"\x00", reference=1)
        print(f"[call] N_CONNECT Id=0x{assigned['nl']:02x} Ch=0x00 "
              f"@B[0x{off:04x}]")
        for rc, rc_id, rc_ch, ref in run_until_rc(shim, sr, gp, sp,
                                                  phase="n-connect"):
            print(f"[call] RC 0x{rc:02x} ({rc_name(rc)}) "
                  f"Id=0x{rc_id:02x} Ch=0x{rc_ch:02x} Ref=0x{ref:04x}")
            # The media-plane N_DATA bridge may not submit bearer data before
            # the bearer exists, and this is the only place the N_CONNECT
            # return code is consumed -- NativeMipsModem never sees it.
            if (rc_id == assigned["nl"]
                    and rc in (eicon_idi.RC_OK, eicon_idi.OK_FC)):
                shim.nl_connected = True
        if not getattr(shim, "nl_connected", False):
            print("[call] N_CONNECT was not accepted; the N_DATA bridge stays "
                  "closed for this call")
        if args.dump_entities:
            dump_entities(shim, gp, args.dump_entity_limit)
        if args.force_modem_dsp_assign:
            force_modem_dsp_assign(shim, args)
        for _ in range(args.call_steps):
            shim.phase = "call-pump"
            shim.call(MIPS_MAINLOOP, [], gp=gp, sp=sp, max_insns=500000)
            for ind, ind_id, ind_ch, ref, payload in drain_indications(shim, sr):
                print(f"[call] IND 0x{ind:02x} Id=0x{ind_id:02x} "
                      f"Ch=0x{ind_ch:02x} Ref=0x{ref:04x} "
                      f"payload={payload.hex()}")
                if ind == 0x04:
                    bearer_disconnected = True
        dsp_assigned = shim.exec_counts.get(SERVICE_ASSIGN, 0) > 0
        if bearer_disconnected:
            bearer_state = "DISCONNECTED"
        elif dsp_assigned:
            bearer_state = "ACTIVE (modem DSP assigned)"
        else:
            bearer_state = "SIGNALLING ACTIVE, DSP UNASSIGNED"
        print(f"[call] simulated B-channel: {bearer_state}")

    if assigned:
        print("[mainloop] assigned: " +
              ", ".join(f"{k}=0x{v:02x}" for k, v in assigned.items()))
    # Preserve the live IDI context for the media-plane N_DATA bridge.
    shim.idi_context = (sr, gp, sp)
    shim.nl_entity_id = assigned.get("nl")

    if getattr(args, "scan_ram", None):
        scan_ram(shim, args.scan_ram.encode())
    print(f"[mainloop] done: host_writes={len(shim.host_writes)}")
    print("[mainloop] modem DSP path: service_assign=%d switch_on=%d"
          % (shim.exec_counts.get(SERVICE_ASSIGN, 0),
             shim.exec_counts.get(SWITCH_ON, 0)))
    if shim.service_assign_block is not None:
        block = shim.service_assign_block
        core = shim.cores.get(block)
        if core is not None:
            dm = ADSP.adsp2181_dm(core)
            print(f"[mainloop] native modem core block=0x{block:08x} "
                  f"ring=DM{dm[0x3316]:04x}..DM{dm[0x3315]:04x} "
                  f"page=0x{dm[0x3fb0]:04x} "
                  f"mode=0x{dm[0x3f94]:04x}")
            if args.native_dm_out is not None:
                args.native_dm_out.parent.mkdir(parents=True, exist_ok=True)
                args.native_dm_out.write_bytes(struct.pack(
                    "<16384H", *(dm[i] for i in range(0x4000))))
                print(f"[mainloop] native modem DM snapshot: "
                      f"{args.native_dm_out}")
    if args.trace_calls:
        from collections import Counter, defaultdict
        phases: dict[str, Counter[int]] = defaultdict(Counter)
        for phase, _src, target in shim.call_trace:
            target = mips_runtime_addr(target)
            if BIAS <= target < BIAS + len(args.image.read_bytes()):
                phases[phase][target] += 1
        print("[trace] firmware call targets by phase:")
        marked = {SERVICE_ASSIGN, BIAS + 0x7FE58}
        for index, (phase, src, target) in enumerate(shim.call_trace):
            runtime_target = mips_runtime_addr(target)
            if runtime_target not in marked:
                continue
            lo = max(0, index - 8)
            hi = min(len(shim.call_trace), index + 9)
            print(f"[trace] ordered window around 0x{runtime_target:08x} "
                  f"in {phase}:")
            for item_phase, item_src, item_target in shim.call_trace[lo:hi]:
                print(f"    [{item_phase}] 0x{mips_runtime_addr(item_src):08x} "
                      f"-> 0x{mips_runtime_addr(item_target):08x}")
        printed = False
        for phase in sorted(phases):
            top = phases[phase].most_common(args.trace_call_limit)
            if not top:
                continue
            printed = True
            print(f"  [{phase}]")
            for target, count in top:
                mark = ""
                if target == SERVICE_ASSIGN:
                    mark = " SERVICE_ASSIGN"
                elif target == BIAS + 0x7fe58:
                    mark = " SWITCH_ON"
                print(f"    0x{target:08x} count={count}{mark}")
        if not printed:
            raw = Counter((phase, src, target) for phase, src, target in shim.call_trace)
            print(f"  no file-backed targets decoded; raw_calls={sum(raw.values())}")
            for (phase, src, target), count in raw.most_common(args.trace_call_limit):
                print(f"    [{phase}] src=0x{src:08x} target=0x{target:08x} count={count}")
    if shim.log and shim.host_writes:
        for addr, val in shim.host_writes[:32]:
            tag = "DM" if addr & 0x4000 else "PM"
            print(f"  host_write {tag} 0x{addr & 0x7fff:04x} = 0x{val:04x}")


class NativeMipsModem:
    """SIP-facing view of the modem core assigned by the real MIPS firmware.

    The MIPS remains live as the host supervisor.  RTP's 8 kHz clock drives
    the selected ADSP core one PRI frame at a time, while one MIPS main-loop
    pass per RTP packet handles database commands and overlay downloads.
    """

    def __init__(self, shim: MipsShim, core, law: str, dsp_block: int,
                 download_descriptors: dict[int, int],
                 dm_blocks: "dict[int, dict[int, tuple[int, ...]]] | None" = None,
                 force_info_after_v8: bool = False,
                 tx_prbs: bool = False,
                 tx_v42: bool = False,
                 tx_v42bis: bool = False,
                 tx_v44: bool = False,
                 prime_v90d_bulk_cursor: bool = False,
                 native_bearer_activation: bool = False,
                 mips_interval: int = 160, adsp_budget: int = ADSP_BUDGET,
                 originate_line_ready: bool | None = None,
                 originate_v8: bool | None = None,
                 modem_role: str = "answer"):
        if modem_role not in GEN_SETUP1_ROLE:
            raise ValueError(f"modem_role must be one of "
                             f"{sorted(GEN_SETUP1_ROLE)}, not {modem_role!r}")
        self.modem_role = modem_role
        self.shim = shim
        self.cpu = core
        self.dm = ADSP.adsp2181_dm(core)
        # Both are fixed members of the core struct, so the pointers are stable
        # for its lifetime. _frame_core swaps a PM word on every one of the 8000
        # samples per second; re-crossing the FFI to re-fetch the same pointer
        # each time is pure overhead.
        self.pm = ADSP.adsp2181_pm(core)
        if CONTINUE_NON_IDLE_ALL:
            ADSP.adsp2181_continue_non_idle(core, 1)
            print("[native-mips] per-frame continuation will be delivered to a "
                  "non-idle core on every page (EICON_CONTINUE_NON_IDLE=1)")
        for address in WATCH_PM:
            ADSP.adsp2181_watch_pm(core, address, 1)
        if WATCH_PM:
            print("[native-mips] PM write watch on "
                  + ",".join(f"0x{a:04x}" for a in WATCH_PM))
        if WATCH_OVERLAY:
            # Disarmed until one of them loads, so nothing is spent before it.
            ADSP.adsp2181_watch_gate(core, 0)
            print("[native-mips] watches gated to overlay(s) "
                  + ",".join(f"0x{o:04x}" for o in WATCH_OVERLAY)
                  + "; disarmed until one is resident")
        for address, value in PIN_PM:
            ADSP.adsp2181_pin_pm(core, address, value, 1)
        if PIN_PM:
            print("[native-mips] PM pinned: "
                  + ", ".join(f"0x{a:04x}=0x{v:06x}" for a, v in PIN_PM))
        self.law = law
        self.dsp_block = dsp_block
        self.download_descriptors = download_descriptors
        # Per-download DM block contents, used to tell a partial overlay's new
        # content from the blocks it merely repeats. See
        # _duplicate_partial_blocks().
        self.dm_blocks = dm_blocks or {}
        self.force_info_after_v8 = force_info_after_v8
        self._media_samples = 0
        self.silence = 0xD5 if law == "pcma" else 0xFF
        self.mips_interval = max(1, mips_interval)
        self.adsp_budget = adsp_budget
        # DM(0x0554) pin for the originate-side dial-page gate (Sessions
        # 95-96); see ORIGINATE_LINE_READY. Defaults to the env var so a
        # loopback caller skips the dial-tone/DTMF wait without any extra
        # flag, and so EICON_ORIGINATE_LINE_READY=0 is a single A/B switch.
        self.originate_line_ready = (ORIGINATE_LINE_READY
                                     if originate_line_ready is None
                                     else originate_line_ready)
        self.originate_v8 = (ORIGINATE_V8
                             if originate_v8 is None
                             else originate_v8)
        self._originate_parked_logged = False
        self._originate_advanced_logged = False
        self._originate_saved_3a36 = None
        self._originate_v8_requested = False
        self._same_page_request_logged = False
        self._unserved_page_requests: set[int] = set()
        # Frames cut short at a partial-overlay request, so a run can say
        # whether the Session 188e path fired at all rather than leaving it to
        # be inferred from the absence of a stack warning.
        self._partial_stops = 0
        self._partial_overlay_served: int | None = None
        self.originate_v34_info = ORIGINATE_V34_INFO
        self._originate_v34_info_logged = False
        self.switches: list[tuple[int, int, int]] = []
        self.overlays: dict[int, tuple[object, str]] = {}
        self.forced_info_samples: list[int] = []
        self.l1l2_forced_samples: list[int] = []
        self.resident = 0x0258
        self._mips_fault_reported = False
        self._forced_dm_writes = 0
        if FORCE_DM:
            # Loud, and once: every capture taken with this on is a patched
            # card, and Session 131's inventory exists because that is easy to
            # forget between a run and its writeup.
            for address, value, page in FORCE_DM:
                scope = (f"while overlay 0x{page:04x} is resident"
                         if page is not None else "on every page")
                print(f"[force-dm] PATCHED FIRMWARE: DM(0x{address:04x}) held "
                      f"at 0x{value:04x} {scope}")
        self._private_line_active = False
        self.tx_prbs = tx_prbs
        self.tx_v42 = tx_v42
        if tx_prbs or tx_v42:
            self._claim_tx_mailbox()
        self.lapm = (LapmEndpoint(
            detect=V42_DETECT,
            role='originator' if modem_role == 'calling' else 'answerer',
            poll_after=V42_POLL_AFTER,
            retransmit_after=V42_RETRANSMIT_AFTER, n400=V42_N400,
            compression=tx_v42bis, v44=tx_v44)
                     if tx_v42 else None)
        self._lapm_active = False
        self.prime_v90d_bulk_cursor = prime_v90d_bulk_cursor
        self._v90d_bulk_cursor_primed = False
        self._v90d_saved_clear = None
        self._v90d_generated = -1
        self._v90d_generator_idle = 0
        self._direct_selected_dispatch = False
        self.native_bearer_activation = native_bearer_activation
        self._native_answer_wdb: list[int] | None = None
        self.tx_requests = 0
        # Page-8 pacing: ticks where the page published a transmit sample, and
        # ticks where it ran the whole ceiling without publishing one.
        self._v34_published_samples = 0
        self._v34_unpublished_samples = 0
        self._v34_last_line_sample = 0
        self._dm_census_on = False
        self._dm_census_started = False
        self._dm_census_samples = 0
        if DM_CENSUS:
            atexit.register(self._write_dm_census)
        self._pm_dumped = False
        # "LO:HI:PATH[@OVERLAY]". Without an overlay the snapshot is taken at
        # exit, which is only ever the last page the call happened to end on --
        # a V.8 that selects V.22 ends with 0x0266 resident and its classifier
        # long overwritten. With one, the snapshot is taken as that page is
        # replaced, so it is the code that actually ran.
        self._pm_dump_overlay = (int(PM_DUMP.split("@", 1)[1], 0)
                                 if PM_DUMP and "@" in PM_DUMP else None)
        if PM_DUMP:
            atexit.register(self._write_pm_dump)
        self._dm_dump_overlay = (int(DM_DUMP.split("@", 1)[1], 0)
                                 if DM_DUMP and "@" in DM_DUMP else None)
        self._dm_dumped = False
        if DM_DUMP:
            atexit.register(self._write_dm_dump)
        if PIN_PM:
            atexit.register(self._report_pin_pm)
        self._pcsp_rows: list[tuple[int, int, int, int, int]] = []
        if PCSP_TRACE:
            atexit.register(self._write_pcsp_trace)
        # Datagrams that carried the LAPM/pattern stream, against those that
        # went out as mark fill because the in_sync gate was shut. A live data
        # connection transmits every datagram whatever this harness thinks, so
        # mark fill here is silence injected into a working link.
        self.tx_payload_datagrams = 0
        self.tx_fill_datagrams = 0
        # Last datagram width published by the pump, held so a transiently
        # unreadable rate word cannot punch a hole in an established stream.
        self._tx_datagram_bits: int | None = None
        self.negotiated_downstream_bps: int | None = None
        self.negotiated_upstream_bps: int | None = None
        self._v90d_upstream_word: int | None = None
        self._v90d_upstream_handoff: tuple[int, int, int] | None = None
        self._v90d_preserved_handoff_logged = False
        self.tx_accepted = 0
        self.tx_first_sample: int | None = None
        self._tx_pending = False
        self._tx_words_pending: tuple[int, int, int] | None = None
        self._tx_lfsr = 0x6D2B79F5
        self.idi_context = getattr(shim, "idi_context", None)
        self.nl_entity_id = getattr(shim, "nl_entity_id", None)
        self.nl_data_queue = collections.deque()
        nl_data = os.environ.get('EICON_V42_NL_DATA', '')
        self.nl_data_mode = nl_data in ('1', 'force')
        # 'force' diverts the transmit direction to NL without waiting for an
        # N_DATA indication to show the bearer carries anything.  See
        # _next_tx_words(): the plain '1' form leaves LAPM on the mailbox until
        # then, so a dead bearer cannot silently replace it with mark fill.
        self.nl_data_forced = nl_data == 'force'
        # NL request state, mirroring isdn.c's per-channel net_busy/NetFC.  A
        # request stays outstanding until its return code arrives; the next one
        # is not posted before then.
        self._nl_busy = False
        self._nl_fc = False
        self._nl_reference = 0
        self._nl_posted = 0
        self._nl_accepted = 0
        self._nl_rejected = 0
        self._nl_tx_octets = 0
        self._nl_rx_octets = 0
        self._nl_rx_seen = False
        self._tx_pattern_pos = 0
        self._bulk_adapter_held = False
        self._v34_bulk_opcode = None
        self._bulk_adapter_opcode: int | None = None
        self._bulk_adapter_waiting_on: tuple[int, int] | None = None
        self._portable_bulk_delay = PortableBulkDelay()
        self._portable_bulk_active = False
        self._bulk_seed_published: tuple[int, int] | None = None
        self._bulk_seed_yielded_to: tuple[int, int] | None = None
        self._bulk_seed_candidate: tuple[int, int] | None = None
        self._bulk_seed_candidate_frames = 0
        # EICON_RX_TRACE=<path> records every RXD datagram the mailbox
        # publishes, so receive-framing hypotheses can be scored offline.
        rx_trace = os.environ.get('EICON_RX_TRACE', '')
        self._rx_trace = open(rx_trace, 'wb') if rx_trace else None
        if self._rx_trace is not None:
            self._rx_trace.write(b'ERXD0001')
            print(f"[rx-trace] recording RXD datagrams to {rx_trace}")
        self._nl_gate_reported = False
        # LAPM produces at line rate whether or not NL is accepting, so the
        # bridge needs elasticity between the two.  This is the transmit
        # elastic store, in bits, filled by the media path.
        self._nl_tx_bits: list[int] = []
        self._v90_tx_source_trace = None

    def _claim_tx_mailbox(self) -> None:
        """Give the explicit host test source ownership of TXD0..TXD2.

        TIKRNL's resident adapter and ``_service_tx_request()`` otherwise both
        answer the same data-pump request.  TIKRNL runs later in the ADSP frame
        and overwrites the host words before the selected modem page consumes
        them.  The request bit still clears, which made ``tx_accepted`` report
        success even though none of the host payload reached the modulator.

        Normal firmware operation is untouched: this is called only for the
        explicit ``--tx-prbs``/``--tx-v42`` host-driven diagnostics.
        """
        span = TIKRNL_TXD_STORE_SIGNATURE[-1][0]
        matches = [
            base for base in range(0x4000 - span)
            if all((self.pm[base + offset] & 0xFFFFFF) == opcode
                   for offset, opcode in TIKRNL_TXD_STORE_SIGNATURE)
        ]
        if len(matches) != 1:
            raise RuntimeError(
                "cannot claim the synchronous TX mailbox: TIKRNL TXD store "
                f"signature matched {len(matches)} times, expected once")
        addresses = [matches[0] + offset
                     for offset, _ in TIKRNL_TXD_STORE_SIGNATURE]
        for address in addresses:
            self.pm[address] = 0x000000
        print("[native-mips] host owns synchronous TX mailbox; suppressed "
              "TIKRNL stores at PM " + "/".join(
                  f"{address:04x}" for address in addresses))

    @property
    def nl_connected(self) -> bool:
        """Whether N_CONNECT has been accepted for the assigned NL entity."""
        return bool(getattr(self.shim, "nl_connected", False))

    def queue_n_data(self, payload: bytes) -> bool:
        """Queue one NL N_DATA payload for the firmware data entity."""
        if not payload or self.idi_context is None or self.nl_entity_id is None:
            return False
        self.nl_data_queue.append(bytes(payload))
        return True

    def _nl_data_gate(self) -> bool:
        """Whether the bearer may carry N_DATA yet.

        Two conditions, both required.  N_CONNECT must have been accepted, or
        the entity has no bearer to put data on; and the data pump must have
        reached synchronous state 0xC6, or the payload is being submitted
        during training, ahead of anything that could carry it.
        """
        if self.idi_context is None or self.nl_entity_id is None:
            return False
        if not self.nl_connected:
            return False
        # _lapm_active is the data pump's own statement that it reached the
        # synchronous state, and it is set for whichever modulation page is
        # resident.  The DATASTATE test alone was not enough: the first CX call
        # opened the gate at DATASTATE=0x0000 because the check only covered
        # the V.34/V.90 pages and 0x0258 was still resident.  Nothing was
        # submitted then -- LAPM had produced nothing to submit -- but a
        # caller using queue_n_data() directly would not have been so lucky.
        if self.tx_v42 and not self._lapm_active:
            return False
        if self.resident in (0x0261, 0x026A) and self.dm[0x3FC2] < 0x00C6:
            return False
        if not self._nl_gate_reported:
            self._nl_gate_reported = True
            print(f"[nl] bearer open for N_DATA: Id=0x{self.nl_entity_id:02x} "
                  f"DATASTATE=0x{self.dm[0x3FC2]:04x}")
        return True

    def _nl_take_tx(self, count: int) -> bytes:
        """Remove up to ``count`` whole octets from the transmit elastic store."""
        available = len(self._nl_tx_bits) // 8
        if not available:
            return b''
        take = min(count, available)
        bits = self._nl_tx_bits[:take * 8]
        del self._nl_tx_bits[:take * 8]
        # HDLC transmits the low-order bit of each octet first, which is the
        # order LAPM lays its stream out in.
        return bytes(sum(bits[i + bit] << bit for bit in range(8))
                     for i in range(0, len(bits), 8))

    def _service_n_data(self) -> None:
        """Post at most one outstanding N_DATA request, driver-fashion.

        isdn.c:3290 sets net_busy before RequestFunc() and only clears it when
        the matching return code arrives (isdn.c:4184/4194); OK_FC additionally
        latches NetFC, which blocks the queue until the next return code.  The
        same rule applies here: without it the bridge posts on every main-loop
        pass and walks the PR ring, which is what the ring-offset progression
        in the 6.4 s run was.
        """
        if self._nl_busy or self._nl_fc:
            return
        if not self._nl_data_gate():
            return
        if self.nl_data_mode and self.lapm is not None and not self.nl_data_queue:
            payload = self._nl_take_tx(270)
            if payload:
                self.nl_data_queue.append(payload)
        if not self.nl_data_queue:
            return
        sr, _gp, _sp = self.idi_context
        payload = self.nl_data_queue.popleft()
        if len(payload) > 270:
            self.nl_data_queue.appendleft(payload[270:])
            payload = payload[:270]
        # Every request after ASSIGN goes out on the Id the adapter returned in
        # ASSIGN_OK (isdn.c:3282 sends C->Net.Req on C->Net.Id).  NL_ID is only
        # the pre-assignment "assign me" Id; there is no separate bearer-data
        # Id.  Posting N_DATA on a hardcoded Id=1 addressed an entity that was
        # never assigned, which is why no return code ever came back.
        self._nl_reference = (self._nl_reference + 1) & 0xFFFF or 1
        off = post_request(self.shim, sr, eicon_idi.N_DATA, self.nl_entity_id,
                           0, payload, reference=self._nl_reference)
        self._nl_busy = True
        self._nl_posted += 1
        self._nl_tx_octets += len(payload)
        print(f"[nl] N_DATA submitted off=0x{off:04x} len={len(payload)} "
              f"Id=0x{self.nl_entity_id:02x} ref={self._nl_reference}")

    def _nl_return_code(self, rc: int, rc_id: int, rc_ch: int,
                        rc_ref: int) -> None:
        """Apply one NL return code to the outstanding-request state."""
        if self.nl_entity_id is None or rc_id != self.nl_entity_id:
            return
        if rc == eicon_idi.OK_FC:
            # Flow control: the request was taken, but nothing further may be
            # posted until the entity reports ready again.
            self._nl_busy = False
            self._nl_fc = True
            self._nl_accepted += 1
        elif rc == eicon_idi.RC_OK:
            self._nl_busy = False
            self._nl_fc = False
            self._nl_accepted += 1
        elif rc in (eicon_idi.READY_INT, eicon_idi.TIMER_INT):
            # Not a response to a request; READY_INT is the entity announcing
            # it can take one again, which is what clears flow control.
            if rc == eicon_idi.READY_INT:
                self._nl_fc = False
        else:
            self._nl_busy = False
            self._nl_fc = False
            self._nl_rejected += 1
            print(f"[nl] N_DATA rejected: RC=0x{rc:02x} ({rc_name(rc)}) "
                  f"Id=0x{rc_id:02x} Ch=0x{rc_ch:02x} ref={rc_ref}")

    def _sport_rx_word(self, code: int) -> int:
        """Expand a DS0 octet as the T1/E1 SPORT compander does."""
        return sport_rx_word(code, self.law)

    def start_native_task(self) -> None:
        """Release the assigned core and run TIKRNL's relocated initializer."""
        if self.dsp_block in self.shim.native_task_started:
            print("[native-mips] TIKRNL initialized before native SWITCH_ON")
            return
        if ADSP.adsp2181_idma_boot_held(self.cpu):
            ADSP.adsp2181_set_idma_boot_hold(self.cpu, 0)
        ADSP.adsp2181_call(self.cpu, 0x0679, 0x02A8)
        ADSP.adsp2181_run(self.cpu, 2_000_000)
        if not ADSP.adsp2181_idle(self.cpu):
            raise RuntimeError(
                f"native TIKRNL initializer stopped at PM "
                f"0x{ADSP.adsp2181_pc(self.cpu):04x}")
        self.shim.native_task_started.add(self.dsp_block)
        print("[native-mips] released assigned DSP and initialized TIKRNL")

    def load_native_overlay(self, download_id: int) -> None:
        """Run the firmware's real segmented/relocating ADSP loader."""
        descriptor = self.download_descriptors.get(download_id)
        if descriptor is None:
            raise RuntimeError(f"download 0x{download_id:04x} is not staged")
        # 0x80086af8 consumes this 0x1c-byte transfer state.  Its segment-base
        # pointer is biased by eight bytes: relocation segment N is read at
        # table + N*2 - 8.  Native TIKRNL allocated modem DM segment 4 at
        # 0x32f0 and movable PM export segment 5 at 0x0580.
        state = RAM_VIRT + 0xA000
        bases = RAM_VIRT + 0xA100
        self.shim.alloc(state, 0x200)
        self.shim.write_bytes(state, bytes(0x40))
        self.shim.write_bytes(bases, bytes(0x40))
        self.shim.write32(state + 0x00, self.dsp_block)
        self.shim.write32(state + 0x08, descriptor)
        self.shim.write32(state + 0x0C, bases + 8)
        dm_blocks = struct.unpack(
            "<I", self.shim.uc.mem_read((descriptor + 0x28) & 0x1FFFFFFF, 4))[0]
        self.shim.write32(state + 0x14, dm_blocks)
        self.shim.write16(bases + 4 * 2, 0x32F0)
        self.shim.write16(bases + 5 * 2, 0x0580)
        before = len(self.shim.host_writes)
        self.shim.intercept_bulk_writes = True
        try:
            result = self.shim.call(DSP_DOWNLOAD, [state, 0xFFFF, 0],
                                    gp=GP, sp=STACK_TOP, max_insns=8_000_000)
        finally:
            self.shim.intercept_bulk_writes = False
        active = self.shim.uc.mem_read((state + 0x10) & 0x1FFFFFFF, 1)[0]
        block_index = struct.unpack(
            "<H", self.shim.uc.mem_read((state + 0x12) & 0x1FFFFFFF, 2))[0]
        if result != 1 or not active:
            raise RuntimeError(
                f"native loader did not complete 0x{download_id:04x}: "
                f"result={result} active={active} block={block_index} "
                f"bulk={self.shim.bulk_write_calls[-4:]}")
        # Eicon's PRI kernel publishes the relocated selected-channel state
        # at DM2f86. Generic modem overlays dereference the compatibility word
        # DM32f6 instead; portable overlay DM blocks reset it to zero. Bridge
        # the private descriptor after every download before page init runs.
        if self.dm[0x2F86]:
            self.dm[0x32F6] = self.dm[0x2F86]
        self.resident = download_id
        if WATCH_OVERLAY:
            armed = self.resident in WATCH_OVERLAY
            ADSP.adsp2181_watch_gate(self.cpu, 1 if armed else 0)
            print(f"[watch-gate] {'armed' if armed else 'disarmed'}: resident "
                  f"0x{self.resident:04x}, watching "
                  + ",".join(f"0x{o:04x}" for o in WATCH_OVERLAY)
                  + f" [cyc={ADSP.adsp2181_cycles(self.cpu)}]")
        self._apply_continue_non_idle()
        if (self._dm_dump_overlay is not None
                and self.resident == self._dm_dump_overlay):
            self._write_dm_dump()
        if (self._pm_dump_overlay is not None
                and self.resident == self._pm_dump_overlay):
            # Snapshot as soon as the page is resident. Waiting for it to be
            # replaced would be better -- runtime patching would be visible --
            # but a page that is never replaced would then never be dumped,
            # and the failing case is exactly the one that stalls.
            self._write_pm_dump()
        print(f"[native-mips] loaded 0x{download_id:04x} through MIPS "
              f"({len(self.shim.host_writes) - before} host writes) "
              f"[cyc={ADSP.adsp2181_cycles(self.cpu)}]")

    def _apply_continue_non_idle(self) -> None:
        """Arm the non-idle continuation for the pages it is selected for.

        Follows the resident page rather than being set once, because the
        pages that need it and the pages it breaks are both in the same call.
        """
        if CONTINUE_NON_IDLE_ALL or not CONTINUE_NON_IDLE_PAGES:
            return
        on = self.resident in CONTINUE_NON_IDLE_PAGES
        ADSP.adsp2181_continue_non_idle(self.cpu, 1 if on else 0)
        if on:
            print(f"[native-mips] non-idle continuation armed for page "
                  f"0x{self.resident:04x}")

    def _duplicate_partial_blocks(self, partial_id: int,
                                  base_id: int) -> tuple[tuple[int, int], ...]:
        """(address, words) of `partial_id`'s DM blocks that `base_id` already has.

        Identical content at the same address, so applying them can only undo
        whatever the running page has since computed there. Blocks the partial
        actually contributes -- new addresses, or the same address with
        different content -- are not in this list and do get applied.
        """
        partial = self.dm_blocks.get(partial_id) or {}
        base = self.dm_blocks.get(base_id) or {}
        return tuple((address, len(values))
                     for address, values in partial.items()
                     if base.get(address) == values)

    def _service_partial_overlay(self) -> bool:
        """Load an overlay the resident page asks for *on top of* itself.

        Bootpage 19 is not a page. It is the marker the kernel's page-request
        service uses for a partial overlay -- a download that adds segments to
        the page already resident and returns to it, rather than replacing it.
        The V.32 page uses one (`0x0267`, "V.32 Partial Overlay"); so does DIAL
        (`0x0263`), which is why `docs/dial_kernel_dispatch.md` shows a chain
        with a partial in the middle of it.

        The request *flag* is no use here, which is why the whole-page path
        never saw this request. DM(0x3131) is posted at PM 0x069a and cleared
        again at PM 0x06e4 inside the same 8 kHz frame -- on hardware the
        kernel completes the transfer itself, so the flag's whole life is
        invisible to a host that samples once per frame. What does stand still
        is the pair the kernel leaves behind: bootpage 19 and DM(0x3132)
        naming the download. Measured at sample 24343 of a V.32 call --
        `bootpage=0x0013 req=0 want=0x0267` -- with bootpage holding 19 for
        ~640 samples afterwards. Session 185.

        The resident page is deliberately *not* changed, and no continuation is
        run. Every page test in this file keys on `self.resident`, and after a
        partial the page that is running is still the underlying one -- calling
        it `0x0267` would take V.32 out of its own transmit and receive paths
        the moment it got them.
        """
        if self.dm[0x3FB0] != PARTIAL_BOOTPAGE:
            self._partial_overlay_served = None
            return False
        download_id = self.dm[0x3132] & 0xFFFF
        if download_id == self.resident:
            # DM(0x3132) still names the whole-page request that brought this
            # page in, because the page writes the bootpage marker before it
            # writes the new id. A "partial" of the resident page onto itself
            # is not a partial: it reloads the image and re-resumes the page
            # for nothing. Wait for the id to be updated.
            return False
        if download_id == self._partial_overlay_served:
            # Bootpage stays 19 until the page puts it back, so without this
            # the same partial would be reloaded on every frame in between.
            return False
        if download_id not in self.download_descriptors:
            if download_id not in self._unserved_page_requests:
                self._unserved_page_requests.add(download_id)
                print(f"[native-mips] partial overlay 0x{download_id:04x} is "
                      f"not staged; page 0x{self.resident:04x} will wait for "
                      "it forever")
            return False
        underlying = self.resident
        # A partial repeats whole DM blocks of the page it extends, byte for
        # byte -- 0x0267 carries three of 0x0266's, 335 words including the LEC
        # workspace at 0x3680..0x37cb. The page has already run its init and
        # computed live values in there by the time it asks for the partial:
        # DM(0x3754), the LEC tap count at PM 0x1d8e, is 9 at sample 22560 and
        # the shipped template puts 0xfff4 back, which as a 14-bit CNTR is
        # 16,372 iterations and is exactly the runaway Session 185 recorded.
        # So apply what the partial adds and keep what it merely repeats.
        # Session 186.
        duplicated = self._duplicate_partial_blocks(download_id, underlying)
        # Printed rather than assumed: whether a block is held back depends on
        # the partial and the base agreeing byte for byte, and if the base's
        # blocks are not recorded the comparison silently holds nothing back
        # and the template lands on top of the running page's own values.
        partial_blocks = self.dm_blocks.get(download_id) or {}
        base_blocks = self.dm_blocks.get(underlying) or {}
        print(f"[native-mips] partial 0x{download_id:04x}: "
              f"{len(partial_blocks)} DM blocks, base 0x{underlying:04x} has "
              f"{len(base_blocks)} recorded; holding back "
              + (",".join(f"0x{a:04x}({w})" for a, w in duplicated)
                 or "nothing")
              + f" [cyc={ADSP.adsp2181_cycles(self.cpu)}]")
        saved = {address: [self.dm[address + i] for i in range(words)]
                 for address, words in duplicated}
        self.load_native_overlay(download_id)
        for address, words in saved.items():
            for index, value in enumerate(words):
                self.dm[address + index] = value
        self.resident = underlying
        # load_native_overlay() set self.resident to the partial's id on the way
        # through, which disarmed anything keyed to the underlying page. Put it
        # back now that the resident page is the truth again.
        self._apply_continue_non_idle()
        self._partial_overlay_served = download_id
        # The page is parked in the kernel's page-request service waiting to be
        # resumed, exactly as it is after a whole-page load, and DM(0x3143)
        # holds where. Without this the load lands and nothing runs it: the
        # V.32 page took the partial, timed out, and fell all the way back to
        # DIAL a third of a second later.
        resume = self.dm[0x3143] & 0x3FFF
        if resume:
            ADSP.adsp2181_call(self.cpu, resume, 0x02A8)
            ADSP.adsp2181_run(self.cpu, self.adsp_budget)
        self.dm[0x3EEE] &= ~0x1000
        self.dm[0x3131] = 0x0000
        print(f"[native-mips] partial overlay 0x{download_id:04x} applied to "
              f"0x{underlying:04x} at sample {self._media_samples}, resumed at "
              f"PM 0x{resume:04x}")
        return True

    def attach_connected_bearer(self) -> None:
        # This is the exact result of SIG.MDM's private bearer-connected
        # notification; use the card loader rather than copying extracted
        # fixed-address word maps.
        for download_id in (0x026D, 0x025C, 0x0262):
            self.load_native_overlay(download_id)
        self.dm[0x2F22] = 0x3C27 if self.law == "pcmu" else 0x3C07
        self.dm[0x32F0] = 0x0004
        self.dm[0x3F0F] = 0x2B00
        self.dm[0x3FB4] = 0x2B01
        # Native CALL_RES has already translated the Linux driver's 26-byte
        # modem CAI into a complete pending WDB. Preserve that transaction:
        # it contains firmware-selected capabilities, INFO0 masks and timing
        # values that the generic ADDSP example does not. The table below is
        # retained only for the standalone/non-native compatibility path.
        if self._native_answer_wdb is not None:
            initial = dict(enumerate(self._native_answer_wdb))
        else:
            initial = {
                0x00: 0x00C4, 0x01: 0x0040, 0x02: 0x0000, 0x03: 0x0000,
                0x07: 0xF0FD, 0x08: 0x0006, 0x09: 0x0006, 0x0A: 0x00FF,
                0x0B: 0x0030, 0x0C: 0x0000, 0x24: 0x000C,
                0x2C: 0x0003, 0x2D: 0x0003,
            }
        for offset, value in initial.items():
            self.dm[0x3EE0 + offset] = value
        self.dm[0x3EEE] = 0x2000
        initial_frames = 0
        for initial_frames in range(1, 4097):
            self._frame_core(self.silence)
            if not (self.dm[0x3EEE] & 0x2000):
                break
        if self.dm[0x3EEE] & 0x2000:
            raise RuntimeError(
                "native TIKRNL did not consume initial WDB: "
                f"3131={self.dm[0x3131]:04x} 3137={self.dm[0x3137]:04x} "
                f"3138={self.dm[0x3138]:04x} 3141={self.dm[0x3141]:04x}")
        if self._native_answer_wdb is not None:
            # DIAL's CAI import runs between the two communication cycles.
            # Republish the driver's exact transaction, changing only the
            # operation words documented by ADDSP Table 15 to start
            # training. In particular, do not replace native Norm_L=a13f,
            # speed_sel_l=fffe or INFO0D_setup=0377 with example-table values.
            #
            # GEN_SETUP1 bit 3 picks the modulation role: 0x0484 answer,
            # 0x048c calling. Session 74 found that forcing 0x048c broke V.8,
            # but that was an open-loop replay against a recording of a peer
            # that had itself called in and expected an answerer -- the one
            # configuration where flipping roles unilaterally cannot work. In
            # a loopback the two ends take opposite roles, which is the first
            # time this word has been meaningfully selectable.
            final = dict(enumerate(self._native_answer_wdb))
            final.update({0x01: GEN_SETUP1_ROLE[self.modem_role],
                          0x02: 0x0030})
            if WDB_OVERRIDE:
                # Diagnostic only; empty by default. See WDB_OVERRIDE.
                final.update(WDB_OVERRIDE)
                print("[native-mips] WDB override on native transaction: " +
                      " ".join(f"+0x{offset:02x}=0x{value:04x}"
                               for offset, value in sorted(
                                   WDB_OVERRIDE.items())))
        else:
            final = {
                0x01: 0x0484, 0x02: 0x0030, 0x04: 0x6000,
                0x0F: 0x0001, 0x10: 0x0100, 0x28: 0x0001,
                0x29: 0x8100, 0x2A: 0x001F, 0x2B: 0xFF00,
                0x79: 0x003F, 0x7A: 0xFFFF, 0x7B: 0x03B7,
                0x7C: 0x000E, 0x7D: 0x0015, 0x7E: 0x000E, 0x7F: 0x0015,
            }
        for offset, value in final.items():
            self.dm[0x3EE0 + offset] = value
        self.dm[0x3EEE] = 0x2000
        if self.resident != 0x0262:
            self.load_native_overlay(0x0262)
        for entry, budget in ((0x0581, 200000), (0x13CC, 1000000)):
            ADSP.adsp2181_call(self.cpu, entry, 0x02A8)
            ADSP.adsp2181_run(self.cpu, budget)
            if not ADSP.adsp2181_idle(self.cpu):
                raise RuntimeError(f"native DIAL setup PM {entry:04x} did not return")
        # PM 0581 imports the native CAI defaults, including NORM_H=0x00ff.
        # The documented answer-mode WDB is the following communication cycle;
        # publish it after that import so V.8 sees NORM_H=1 (negotiate).
        for offset, value in final.items():
            self.dm[0x3EE0 + offset] = value
        self.dm[0x3EEE] = 0x2000
        answer_frames = 0
        for answer_frames in range(1, 4097):
            self._frame_core(self.silence)
            if not (self.dm[0x3EEE] & 0x2000):
                break
        if self.dm[0x3EEE] & 0x2000:
            raise RuntimeError("native TIKRNL did not consume answer WDB")
        print("[native-mips] connected bearer activated through DIAL "
              f"(WDB frames {initial_frames}+{answer_frames})")

    def complete_native_answer(self) -> None:
        """Finish ADDSP answer setup after native task attachment.

        Event 0x03 remains the sole TIKRNL attachment owner. The existing
        compatibility routine is used only for its documented DIAL pages and
        two WDB communication cycles; the exact one-call selected-channel
        media adapter is restored before media starts.
        """
        # Snapshot before loading DIAL: this is the WDB produced inside the
        # closed firmware's native CALL_RES/SERVICE_ASSIGN/SWITCH_ON path from
        # the CAI built by divas4linux kernel/message.c:add_b1().
        self._native_answer_wdb = list(self.dm[0x3EE0:0x3F80])
        native = self.native_bearer_activation
        self.native_bearer_activation = False
        try:
            self.attach_connected_bearer()
        finally:
            self.native_bearer_activation = native
        # Task attachment and retained DM are native. Media uses the existing
        # one-call selected descriptor adapter because page downloads replace
        # the kernel's private dispatch records; it invokes relocated PM06c8
        # exactly once per line sample.
        self._direct_selected_dispatch = True

    def _prbs_bits(self, count: int) -> list[int]:
        bits = []
        for _ in range(count):
            # x^32 + x^22 + x^2 + x + 1, non-zero deterministic seed.
            lsb = self._tx_lfsr & 1
            self._tx_lfsr = ((self._tx_lfsr >> 1) ^
                             (0x80200003 if lsb else 0)) & 0xFFFFFFFF
            bits.append(lsb)
        return bits

    def _v90d_tx_bits(self) -> int | None:
        """Bits in one 8000/6-Hz V.90 downstream datagram.

        DATASTATEspeedTx bit 5 selects the V.90 table; its speed index is also
        the number added to the minimum 21-bit (28000 bit/s) datagram.
        """
        value = self.dm[0x3F61]
        return 21 + (value & 0x1F) if value & 0x20 else None

    @staticmethod
    def _v34_datagram_bits(value: int, format_mask: int) -> int | None:
        """Bits in one 2400-Hz V.34 datagram from a DATASTATE speed word."""
        rate = v34_rate(value, format_mask)
        return rate // 2400 if rate is not None else None

    def _v34_rx_bits(self) -> int | None:
        # DATASTATESpeed has its format selector at bit 13; the asymmetric
        # transmitter word uses bit 5 instead (ADDSP guide offsets 0x81/0x82).
        return self._v34_datagram_bits(self.dm[0x3F62], 0x2000)

    def _lec_page_datagram_bits(self) -> int:
        """Datagram width for the shared V.22/V.32 image, by bootpage.

        Overlay 0x0266 serves both modulations, so `self.resident` cannot tell
        them apart and DM(0x3FB0) has to. Anything other than page 2 keeps the
        V.22bis width, which is what this code did for both before.
        """
        if (self.dm[0x3FB0] & 0xFFFF) == V32_BOOTPAGE:
            return V32_DATAGRAM_BITS
        return V22_DATAGRAM_BITS

    def _rx_datagram_bits(self) -> int | None:
        """Bits in one receive datagram, whichever page is resident.

        Neither V.22 nor V.32 has a rate word to read: the width comes from the
        bootpage. Every other page still resolves through the V.34 DATASTATE
        words, which is what `_service_rx_data()` has always used.
        """
        if self.resident == V22_OVERLAY:
            return self._lec_page_datagram_bits()
        return self._v34_rx_bits()

    def _next_tx_words(self) -> tuple[int, int, int]:
        """Generate one synchronous data-pump mailbox datagram.

        The synchronous-state test is a *latch*, not a live comparison. Getting
        that wrong is what kept V.42 from ever completing: `DM(0x3FC2)` does not
        sit still at or above 0xC6 once the link is up -- it moves through the
        0xC0..0xC4 neighbourhood constantly -- but the DSP transmits a datagram
        every time it asks for one regardless. Re-testing it per datagram meant
        27% of a live call's downstream bits (22587 of 82715, measured) went out
        as mark fill *inside* the LAPM stream, shredding every HDLC frame. Our
        framing, our FCS, our XID content and our transmit path were all proven
        correct against a real CX; nothing survived the holes.

        `_lapm_active` is already the pump's own statement that it reached the
        synchronous state, so once it is set the stream is continuous and the
        datagram width is whatever was last published -- the rate word also
        reads back transiently as zero, which would reopen the same hole.
        """
        latched = self._lapm_active
        if self.resident == 0x026A:
            # DATASTATE speed words can appear transiently during training.
            # Do not start LAPM/T400 until the data pump has actually reached
            # synchronous state 0xC6; otherwise T400 expires before CONNECT.
            in_sync = latched or self.dm[0x3FC2] >= 0x00C6
            count = (self._v90d_tx_bits()
                     if self.tx_v42 and in_sync else None)
        elif self.resident == 0x0261:
            in_sync = latched or self.dm[0x3FC2] >= 0x00C6
            count = (self._v34_datagram_bits(self.dm[0x3F61], 0x0020)
                     if self.tx_v42 and in_sync else None)
            if self.tx_v42 and in_sync and count is None:
                # Symmetric V.34 publishes the common rate in DATASTATESpeed.
                count = self._v34_datagram_bits(self.dm[0x3F62], 0x2000)
        elif self.resident == V22_OVERLAY:
            # No DATASTATE test here, and none is needed: DM(0x3FC2) is a V.34
            # /V90D word and means nothing on page 1. What stands in for it is
            # the request itself -- this function is only reached from
            # _service_tx_request(), and the V.22 page raises DI_control bit F
            # for the first time as the link completes (measured at 6.82 s and
            # 6.22 s on the two ends of a call whose handshake finished at 7.08
            # and 7.00), not during its training. Being asked is the evidence.
            count = self._lec_page_datagram_bits() if self.tx_v42 else None
        else:
            count = None
        if self.tx_v42 and count is None and latched:
            # The rate word went transiently unreadable on an established link.
            # Keep the stream continuous at the width already negotiated.
            count = self._tx_datagram_bits
        if count is not None:
            self._tx_datagram_bits = count
        if self.tx_v42 and count is not None:
            if not self._lapm_active:
                self._lapm_active = True
                if self.resident == 0x026A:
                    self._service_negotiated_rates()
                elif self.resident == V22_OVERLAY:
                    # v34_rate() would read the V.34 DATASTATE words, which on
                    # this page hold whatever the previous page left there.
                    # The two modulations do not share a symbol rate -- V.22bis
                    # is 600 baud and 4 bits a symbol, V.32/V.32bis 2400 baud --
                    # so the rate cannot be derived from the width alone and
                    # each page carries its own constant.
                    bps = (V32_BIT_RATE
                           if (self.dm[0x3FB0] & 0xFFFF) == V32_BOOTPAGE
                           else V22_BIT_RATE)
                    self.negotiated_downstream_bps = bps
                    self.negotiated_upstream_bps = bps
                else:
                    rate = v34_rate(self.dm[0x3F62])
                    self.negotiated_downstream_bps = rate
                    self.negotiated_upstream_bps = rate
                # RX valid may contain a training-era word which predates the
                # synchronous LAPM stream. Acknowledge it without decoding it.
                self.dm[0x3FAD] &= ~0x6000
                modulation = (
                    'V.90/V.34' if self.resident == 0x026A else
                    ('V.32' if (self.dm[0x3FB0] & 0xFFFF) == V32_BOOTPAGE
                     else 'V.22bis') if self.resident == V22_OVERLAY
                    else 'V.34')
                print(f"[v42] {modulation} synchronous data state: TX {count} "
                      f"bits/datagram, RX {self._rx_datagram_bits() or '?'} "
                      "bits/datagram")
                if self.resident == 0x026A:
                    print("[v90] negotiated rates: downstream "
                          f"{self.negotiated_downstream_bps or '?'} bit/s, "
                          f"upstream {self.negotiated_upstream_bps or '?'} "
                          "bit/s (ADDSP read DB 0x81/0x82)")
            if self.nl_data_mode and (self._nl_rx_seen or self.nl_data_forced):
                # The NL bridge carries the LAPM stream instead of the
                # synchronous mailbox, so the mailbox gets mark fill.
                #
                # This is gated on the same evidence the receive direction is
                # gated on -- an N_DATA *indication* having actually arrived --
                # and for the same reason.  Diverting unconditionally puts mark
                # fill in the one transmit path that is known to reach the line
                # and hands the LAPM stream to an entity that has never been
                # shown to carry it.  That is what the 73-XID call did: the CX
                # was answered 73 times over NL and heard mark on the line, so
                # it retransmitted XID for the whole call and never sent SABME.
                # EICON_V42_NL_DATA=force restores the unconditional diversion
                # for anyone testing the bearer in isolation.
                #
                # LAPM is still clocked here rather than from _service_n_data(): its
                # still clocked here rather than from _service_n_data(): its
                # T401/T403/poll counters advance per take() call, so driving
                # them from the main loop instead of the datagram rate would
                # run every LAPM timer on the wrong clock.  The bits go to the
                # transmit elastic store for _service_n_data() to drain.
                self._nl_tx_bits.extend(self.lapm.take(count))
                if len(self._nl_tx_bits) > NL_TX_ELASTIC_BITS:
                    dropped = len(self._nl_tx_bits) - NL_TX_ELASTIC_BITS
                    del self._nl_tx_bits[:dropped]
                    print(f"[nl] transmit elastic store overflowed; dropped "
                          f"{dropped // 8} octets (NL is not draining)")
                bits = [1] * count
            elif TX_PATTERN_BITS is not None:
                bits = [TX_PATTERN_BITS[(self._tx_pattern_pos + i)
                                        % len(TX_PATTERN_BITS)]
                        for i in range(count)]
                self._tx_pattern_pos += count
            else:
                bits = self.lapm.take(count)
            self.tx_payload_datagrams += 1
        else:
            self.tx_fill_datagrams += 1
            # Training consumes arbitrary payload before DATASTATE publishes a
            # line rate. Keep the old PRBS diagnostic available, but allow a
            # real modem session to use mark fill instead of apparent random
            # data until the negotiated synchronous rate is known.
            count = 48 if self.resident == 0x026A else 16
            bits = (self._prbs_bits(count) if
                    (self.tx_prbs or V42_TRAINING_PRBS) else [1] * count)
        if self.resident == 0x026A:
            # V90D is the exception: TXD0 bit 0 is oldest and the datagram
            # continues through TXD1/TXD2.
            #
            # That is the ADDSP guide's reading and it has never been checked
            # against a receiver that cares. It is also the *opposite* of the
            # receive mailbox, where _service_rx_data() takes bit 15 as oldest
            # -- and the receive convention is proven, because it decodes the
            # CX's XID with a valid FCS 60 times a call. PRBS cannot test a bit
            # order, so a live call with --tx-prbs proves only that the samples
            # reach the peer, which it does (CONNECT 42667, garbage on the CX's
            # terminal). If our HDLC goes out bit-reversed within each word the
            # peer never sees a flag, which is exactly the observed behaviour:
            # the CX retransmits XID on a metronomic 700 ms T401, completely
            # unaffected by the 60 responses we send it.
            #
            # EICON_V90D_TX_MSB_FIRST=1 packs the datagram the way the receive
            # mailbox unpacks it, so the two can be compared on a live call.
            bits.extend([1] * (48 - len(bits)))
            if V90D_TX_MSB_FIRST:
                words = [sum(bits[word * 16 + bit] << (15 - bit)
                             for bit in range(16)) for word in range(3)]
            else:
                words = [sum(bits[word * 16 + bit] << bit for bit in range(16))
                         for word in range(3)]
            return words[0], words[1], words[2]
        # All other modulations use TXD0 with the oldest bit at bit 15 and the
        # negotiated datagram left aligned.
        bits.extend([1] * (16 - len(bits)))
        return sum(bits[bit] << (15 - bit) for bit in range(16)), 0, 0

    def _service_bulk_lengths(self) -> None:
        """Seed and hold the echo bulk-delay lengths the firmware left at zero.

        The firmware's seeder (see bulk_delay_seed) fires only on page 0x0260
        and only before its input exists, so DM(0x3fbc)/DM(0x3fbd) are zero for
        the whole of V.34 and V90D.  A zero-length delay line gives the echo
        canceller no reference at all: PortableBulkDelay rejects the descriptor
        every frame, and the native V.34 worker's modulo bound at PM 0x1930 is
        zero for the same reason -- which is the unbounded fill Sessions 90-93
        and 101 chased.  The retained 0xffff lower limit bounds that pointer but
        does not give it a length.

        Holding rather than writing once: PM 0x19e2/0x19e4 restore both words
        from the saved context at DM(0x3608)/DM(0x3609) at the top of every
        frame, and PM 0x1a13/0x1a18 write them back one BULK_LENGTH_DECREMENT
        low at the bottom.  A single write would survive exactly one frame, and
        a value that alternates by 0x20 would make PortableBulkDelay flush its
        ring every frame.  Publishing the same pair into both the live words and
        the saved context each frame keeps the firmware's own ping-pong intact
        and stable.

        This defers to the firmware, but only to a real publication. The
        candidate has to be a coherent descriptor -- `0 < near <= far` -- and
        has to survive BULK_SEED_YIELD_FRAMES consecutive frames. Neither
        condition is pedantic: the ping-pong routinely shows a half-updated
        pair, and an earlier version of this stood down on a transient
        `near=17 far=0` in the second frame of every call, which handed the
        delay line straight back to the value the seed exists to replace.
        """
        if not BULK_DELAY_SEED or self.resident not in (0x0261, 0x026A):
            return
        seed = bulk_delay_seed(self.dm)
        if seed is None:
            return
        near, far = seed
        published = (int(self.dm[0x3FBC]), int(self.dm[0x3FBD]))
        if self._bulk_seed_yielded_to:
            return
        accepted = {(0, 0), (near, far),
                    (near - BULK_LENGTH_DECREMENT, far - BULK_LENGTH_DECREMENT)}
        if self._bulk_seed_published is not None:
            accepted.add(self._bulk_seed_published)
            accepted.add((self._bulk_seed_published[0] - BULK_LENGTH_DECREMENT,
                          self._bulk_seed_published[1] - BULK_LENGTH_DECREMENT))
        coherent = 0 < published[0] <= published[1] <= 0x2000
        # Never stand down on the V.34 page.  Session 114k measured what the
        # firmware's own pair does there: the worker's ring pointer at PM
        # 0x192e stops wrapping and marches 0x0061..0x0769, straight through
        # the V.34 script's test-routine table at DM(0x064B..0x066A) -- 16
        # writes into it in one call, after which the state machine resolves
        # its exit test to INFO residue and the page freezes for good.  Holding
        # the floor pair instead bounds the sweep to 0x0061..0x0241 and puts
        # zero writes in the table.  V90D (0x026A) keeps the existing yield.
        yielding = not BULK_DELAY_HOLD_ALWAYS and self.resident != 0x0261
        if published not in accepted and coherent and yielding:
            if published == self._bulk_seed_candidate:
                self._bulk_seed_candidate_frames += 1
            else:
                self._bulk_seed_candidate = published
                self._bulk_seed_candidate_frames = 1
            if self._bulk_seed_candidate_frames >= BULK_SEED_YIELD_FRAMES:
                self._bulk_seed_yielded_to = published
                print("[native-mips] firmware published its own bulk delay "
                      f"lengths near={published[0]} far={published[1]}; "
                      "the host seed stands down")
            return
        self._bulk_seed_candidate = None
        self._bulk_seed_candidate_frames = 0
        self.dm[0x3FBC] = self.dm[0x3608] = near
        self.dm[0x3FBD] = self.dm[0x3609] = far
        if self._bulk_seed_published != (near, far):
            self._bulk_seed_published = (near, far)
            source = (f"floor + DM(0x3fcb)={int(self.dm[0x3FCB])}"
                      if BULK_DELAY_MEASURED else "floor only")
            extra = (f", {BULK_DELAY_EXTRA_PAIRS:+d} pairs tuning"
                     if BULK_DELAY_EXTRA_PAIRS else "")
            print(f"[native-mips] seeded echo bulk delay for "
                  f"0x{self.resident:04x}: near={near} far={far} sample pairs "
                  f"({near / 8:.1f}/{far / 8:.1f} ms), {source}, "
                  f"delaycorrection={int(self.dm[0x3F04])}{extra}")

    def _service_bulk_adapter(self) -> None:
        """Service the bounded delay or diagnose a qualified native release.

        Session 88 recorded that enabling the 0x1900..0x19c8 adapter is worse
        than leaving it off: the outer state word goes 0x00c4 -> 0x78f8 within
        a few hundred samples of the page load. The cause is a sequencing one.

        The adapter's frame loop at PM 0x26b7..0x26d7 stores through I0 from
        PM 0x26b1's `I0 = 0x1DD0`, and I0 has no L register -- it is linear by
        design, bounded only by the loop count the routine reads from
        DM(0x1E4F) at PM 0x26b5. That word's only writer is PM 0x3dee, in the
        rate-publication routine that also writes DATASTATESpeed at PM 0x3ded,
        and it stores the datagram bit count: DATASTATESpeed is assembled as
        0x2000 | 0x0020 | (AX0 - 0x15) and the host reads bits per datagram
        back as 21 + (value & 0x1f), so the stored AX0 *is* that bit count.

        At page load that routine has not run, so DM(0x1E4F) holds whatever
        the previous page left -- 0x6613, or 26131 iterations, in the capture
        this was traced on. I0 walks 0x1DD0 upward across DM(0x1FF7), which is
        the outer state word, and writes 0x78f8 over it from PM 0x26d4. With
        the adapter RTSed out the routine is never reached and the word is
        never touched, which is exactly what the archived comparison showed.

        A later exact-12,000 hardware call disproved the remaining native
        width-32 qualification: after release, PM 0x1b69/0x1b6a swept through
        unrelated DM.  The default therefore keeps PM 0x19c8 held and services
        the documented delay-line database ABI with PortableBulkDelay.  The
        rate/count checks below remain only for explicit native diagnostics.
        """
        # The V.34 hold is tracked separately from the V90D one, so this
        # runs ahead of the page-14 held-state guard below.
        # V.34 runs the portable delay with no rate gate: the page has no
        # equivalent publication to wait on, and PM 0x19a7's 0x0400 enable bit
        # in DM(0x3FC1) is the same worker gate on both pages.
        if self.resident == 0x0261 and V34_PORTABLE_BULK:
            enabled = bool(int(self.dm[0x3FC1]) & 0x0400)
            active = enabled and self._portable_bulk_delay.service(self.dm)
            if active and not self._portable_bulk_active:
                print("[native-mips] portable V.34 bulk delay active: "
                      f"near={int(self.dm[0x3FBC])} "
                      f"far={int(self.dm[0x3FBD])} sample pairs")
            self._portable_bulk_active = active
            if not enabled:
                self._portable_bulk_delay.reset()
            return

        if not self._bulk_adapter_held:
            return
        # Only V90D is held behind the data-rate publication.  Another overlay
        # replaces PM 0x19c8, so a stale page-14 hold must never restore its
        # saved opcode into that page.
        if self.resident != 0x026A:
            return
        if V90D_PORTABLE_BULK:
            # PM 0x19a7 uses bit 0x0400 as the worker-enable gate.  Service
            # the same database interface once per frame, but never restore
            # the unsafe native tail jump.  This can start during training;
            # unlike the former datagram-width gate it does not need a data
            # rate because delay length and input samples are its full ABI.
            enabled = bool(int(self.dm[0x3FC1]) & 0x0400)
            active = enabled and self._portable_bulk_delay.service(self.dm)
            if active and not self._portable_bulk_active:
                print("[native-mips] portable V90D bulk delay active: "
                      f"near={int(self.dm[0x3FBC])} "
                      f"far={int(self.dm[0x3FBD])} sample pairs")
            self._portable_bulk_active = active
            if not enabled:
                self._portable_bulk_delay.reset()
            return
        parameters = v90d_bulk_adapter_parameters(self.dm)
        if parameters is None:
            rate = int(self.dm[0x3F61])
            count = int(self.dm[0x1E4F])
            waiting_on = (rate, count)
            if waiting_on != self._bulk_adapter_waiting_on:
                self._bulk_adapter_waiting_on = waiting_on
                encoded_count = 21 + (rate & 0x001F)
                if ((rate & V90_SPEED_FORMAT_MASK) == V90_SPEED_FORMAT_MASK
                        and count == encoded_count
                        and 21 <= count <= 42
                        and count not in V90D_QUALIFIED_BULK_WIDTHS):
                    print("[native-mips] bulk adapter remains held: V90D "
                          f"width {count} is not hardware-qualified")
            return
        rate, count = parameters
        ADSP.adsp2181_pm(self.cpu)[0x19C8] = self._bulk_adapter_opcode
        self._bulk_adapter_held = False
        self._bulk_adapter_waiting_on = None
        print(f"[native-mips] bulk adapter released: DATASTATEspeedTx="
              f"0x{rate:04x}, DM(0x1E4F)={count} bits/datagram")

    def _service_negotiated_rates(self) -> None:
        """Latch valid ADDSP rate words before DATASTATE makes them transient."""
        if self.resident != 0x026A:
            return
        upstream_word = int(self.dm[0x3F62])
        previous = getattr(self, "_v90d_upstream_word", None)
        if upstream_word != previous:
            handoff = v90d_upstream_handoff(self.dm, upstream_word)
            if handoff is not None:
                if handoff != getattr(self, "_v90d_upstream_handoff", None):
                    self._v90d_preserved_handoff_logged = False
                self._v90d_upstream_handoff = handoff
            preserved = None
            if V90D_PRESERVE_EXACT_UPSTREAM:
                preserved = v90d_exact_upstream_fallback(
                    self.dm, upstream_word,
                    getattr(self, "_v90d_upstream_handoff", None))
            if preserved is not None:
                upstream_word, speed_number, datagram_parameter = preserved
                self.dm[0x3F62] = upstream_word
                self.dm[0x3F9B] = speed_number
                self.dm[0x204E] = datagram_parameter
                if not getattr(self, "_v90d_preserved_handoff_logged", False):
                    print("[v90] preserved exact upstream selection through "
                          f"final quality fallback: 0x{upstream_word:04x} "
                          f"({v34_rate(upstream_word)} bit/s), "
                          f"speed-number={speed_number}, "
                          f"parameter={datagram_parameter}")
                    self._v90d_preserved_handoff_logged = True
            self._v90d_upstream_word = upstream_word
            previous_rate = (v34_rate(previous)
                             if previous is not None else None)
            upstream_rate = v34_rate(upstream_word)
            previous_is_encoded = (previous is not None
                                   and previous & 0xFFE0 == 0x11E0)
            upstream_is_encoded = upstream_word & 0xFFE0 == 0x11E0
            # Invalid words can churn rapidly after unrelated DM damage.  A
            # transition into or out of an encoded V90D upstream rate is the
            # useful diagnostic; checking the speed index alone would mistake
            # arbitrary damaged words for valid V.34 rates.
            if (upstream_word != previous
                    and ((upstream_rate is not None and upstream_is_encoded)
                         or (previous_rate is not None
                             and previous_is_encoded))):
                def diagnostic_word(address: int) -> int:
                    try:
                        return int(self.dm[address])
                    except (IndexError, KeyError):
                        return 0

                print("[v90] upstream rate word "
                      f"{previous if previous is not None else 0:04x}->"
                      f"{upstream_word:04x} at sample "
                      f"{getattr(self, '_media_samples', 0)}: "
                      f"rate-mask=0x{diagnostic_word(0x1E3F):04x}, "
                      f"allowed-mask=0x{diagnostic_word(0x210B):04x}, "
                      f"limit={diagnostic_word(0x20BA)}, "
                      f"quality=0x{diagnostic_word(0x0FCF):04x}, "
                      f"mode-mask=0x{diagnostic_word(0x1FD6):04x}, "
                      f"result-mask=0x{diagnostic_word(0x3F8D):04x}")
        if (upstream_word == 0x11E0
                and v90_downstream_rate(int(self.dm[0x3F61])) is not None
                and not getattr(self, "_v90d_no_common_rate_logged", False)):
            print("[v90] no-common upstream handoff: "
                  f"rate-mask=0x{int(self.dm[0x1E3F]):04x}, "
                  f"allowed-mask=0x{int(self.dm[0x210B]):04x}, "
                  f"limit={int(self.dm[0x20BA])}, "
                  f"quality=0x{int(self.dm[0x0FCF]):04x}, "
                  f"speed-number={int(self.dm[0x3F9B])}, "
                  f"parameter={int(self.dm[0x204E])}, "
                  f"result-mask=0x{int(self.dm[0x3F8D]):04x}")
            self._v90d_no_common_rate_logged = True
        if V90D_PRESERVE_EXACT_UPSTREAM:
            ceiling_floor = v90d_exact_upstream_ceiling_floor(self.dm)
            if ceiling_floor is not None:
                old_ceiling = int(self.dm[0x20BA])
                self.dm[0x20BA] = ceiling_floor
                if not getattr(self, "_v90d_ceiling_floor_logged", False):
                    print("[v90] raised transient quality ceiling for exact "
                          f"upstream offer: {old_ceiling}->{ceiling_floor}, "
                          f"rate-mask=0x{int(self.dm[0x1E3F]):04x}")
                    self._v90d_ceiling_floor_logged = True
        downstream, upstream = v90d_negotiated_rates(self.dm)
        if downstream is not None:
            self.negotiated_downstream_bps = downstream
        if upstream is not None:
            self.negotiated_upstream_bps = upstream

    def _service_rx_data(self) -> None:
        if not self._lapm_active:
            return
        count = self._rx_datagram_bits()
        if count is None:
            return
        control = self.dm[0x3FAD]
        # V.22 needs no arm of its own: it publishes into RXD0 with bit 13 and
        # never writes RXD1, so the second pair simply never fires there, and
        # the oldest-bit-at-15 unpacking below is the convention page 1 uses
        # too (Session 183). Do not "fix" the loop to skip it.
        for mask, address in ((0x2000, 0x3FAE), (0x4000, 0x3FAF)):
            if control & mask:
                word = self.dm[address]
                # Two possible receive sources feed one HdlcDecoder, and only
                # one of them may be live: interleaving them desynchronises the
                # flag search and fails the FCS on everything.  The mailbox is
                # the source until an N_DATA indication actually arrives.
                #
                # The first live CX call settled which one that is.  With the
                # NL entity assigned B2_TRANSPARENT the firmware accepted all
                # 857 N_DATA requests and returned an N_DATA indication for
                # none of them, so the receive direction stays on the mailbox
                # even while the transmit direction rides the NL entity.
                # Suppressing the mailbox on the assumption that indications
                # would replace it left LAPM with no source at all: no frame
                # was decoded for the whole call and V.42 fell back at T400.
                #
                # The acknowledgement below is unconditional either way, or the
                # DSP stalls waiting for the host to consume the datagram.
                if self._rx_trace is not None:
                    # Raw record of what the mailbox published, before any
                    # framing assumption is applied. The bit count, the bit
                    # order and the RXD0/RXD1 ordering are all unverified, and
                    # a live call cannot test more than one guess at a time;
                    # this lets every combination be scored offline against the
                    # same stream. See tools/rx_frame_search.py.
                    self._rx_trace.write(
                        struct.pack("<IHHH", self._media_samples, count,
                                    mask, word))
                if not (self.nl_data_mode and self._nl_rx_seen):
                    # RXD b15 is the first/oldest bit; only the negotiated
                    # number of left-aligned bits belongs to this datagram.
                    self.lapm.feed([(word >> (15 - bit)) & 1
                                    for bit in range(count)])
                self.dm[0x3FAD] &= ~mask

    def _service_tx_request(self) -> None:
        """Supply the polling data interface described by ADDSP guide §5.3.1.

        In V90D, TXD0 bit 0 is oldest and a datagram spans TXD0..TXD2. The
        negotiated packet uses only 21..42 of these bits. The DSP owns and
        clears DI_control bit F after consuming the packet.
        """
        if (not (self.tx_prbs or self.tx_v42)
                or self.resident not in (0x0261, 0x026A, V22_OVERLAY)
                or self._tx_pending or not (self.dm[0x3FAD] & 0x8000)):
            return
        words = self._next_tx_words()
        self.dm[0x3F05], self.dm[0x3F06], self.dm[0x3F07] = words
        self._tx_words_pending = words
        self.tx_requests += 1
        self._tx_pending = True
        if self.tx_first_sample is None:
            self.tx_first_sample = self._media_samples
            print(f"[native-mips] supplied first synchronous TX datagram at sample "
                  f"{self._media_samples}: "
                  f"{words[0]:04x}/{words[1]:04x}/{words[2]:04x}")

    def _frame_core(self, code: int) -> None:
        # A request raised by the preceding sample is answered before the DSP
        # receives the next SPORT clock, matching an IDMA host polling cycle.
        self._service_tx_request()
        self._media_samples += 1
        # Originate-side "line connected" signal (Sessions 95-96, solved on
        # the native MIPS path). The calling branch of the dial page has TWO
        # gates in sequence, found by disassembling the resident PM:
        #
        #   35d7: AY0 = DM(046C); IF LT JUMP 35DD      ; first gate
        #   35da: AR = DM(0554); AR = AR - 0x10; IF LT RTS   ; need 0554 >= 0x10
        #   35dd: AX0 = $35ED; DM(03EF) = AX0; ... JUMP 36CC  ; proceed; set
        #                                                        next continuation
        #   35ed: CALL 3851; IF GT RTS                  ; second gate
        #   35ef: AR = DM(046C); IF LT RTS              ; need 046C >= 0
        #   35f2: AR = DM(0554); AR + 0; IF NE RTS       ; need 0554 == 0  (!)
        #   35fa: SR1 = 0x0200; CALL 385B; AR = 0x51; DM(3FC2) = AR  ; TrnProg 0x51
        #
        # The first gate needs DM(0x0554) >= 0x10 ("line connected"), the
        # supervisory tone-detector result a PRI never arms (Session 96). The
        # second gate -- reached the next frame via DM(03EF)=0x35ed -- needs
        # DM(0x0554) == 0. So the pin must be 0x20 while DM(03EF)==0x35d7
        # (first gate pending) and then 0 while DM(03EF)==0x35ed (second gate
        # pending). Holding 0x20 forever (the earlier attempt) passes the first
        # gate but the second gate's IF NE RTS fires and TrnProgress never
        # reaches 0x0051.
        #
        # PM 0x3a36 is the sole writer of DM(0x0554); it writes 0 every frame
        # (the scan tail), so while the first gate is pending the scan must be
        # NOPed (PM 0x3a36 -> 0x000000) to keep the pin alive to the gate's
        # single read. The NOP has to be reapplied each frame because the
        # dial-page overlay reloads into a->program on page entry. For the
        # second gate, restore 0x3a36 and let the scan zero 0554 naturally.
        #
        # Confirmed: the caller now reaches TrnProgress 0x0051 (training
        # start, DSR raised) and transmits non-silence on the line. The next
        # stall is downstream -- V.8 is not requested from this path yet
        # (handoff.md ranked next step). EICON_ORIGINATE_LINE_READY=0 /
        # --no-originate-line-ready reproduce the inert caller for A/B.
        if (self.modem_role == "calling" and self.originate_line_ready
                and self.dm[0x03EF] == 0x35D7):
            # First gate (PM 0x35d7) needs DM(0x0554) >= 0x10 to proceed.
            # Pin it high and NOP the scan tail (PM 0x3a36) so it cannot zero
            # the pin before the gate's single read.
            if not self._originate_parked_logged:
                print(f"[native-mips] originate first gate (DM(03EF)=0x35d7) "
                      f"pending; pinning DM(0x0554)=0x20 and NOPing PM 0x3a36 "
                      f"from sample {self._media_samples} "
                      f"(EICON_ORIGINATE_LINE_READY)")
                self._originate_parked_logged = True
                self._originate_saved_3a36 = self.pm[0x3A36]
            self.pm[0x3A36] = 0x000000
            self.dm[0x0554] = 0x20
        elif (self.modem_role == "calling" and self.originate_line_ready
                and self.dm[0x03EF] == 0x35ED):
            # Second gate (PM 0x35ed) needs DM(0x0554) == 0 to proceed to
            # TrnProgress 0x0051 (IF NE RTS at 0x35f4). The first gate set
            # DM(03EF)=0x35ed, so stop pinning, restore the scan, and let it
            # zero 0554 so the second gate reads 0 and continues to 0x35fa.
            if self._originate_parked_logged and self._originate_saved_3a36 is not None:
                print(f"[native-mips] originate second gate (DM(03EF)=0x35ed) "
                      f"pending at sample {self._media_samples}; "
                      f"un-pinning 0554 and restoring PM 0x3a36 so the scan "
                      f"zeroes 0554 (the second gate needs 0554 == 0)")
                self.pm[0x3A36] = self._originate_saved_3a36
                self._originate_saved_3a36 = None
            # Do not pin 0554; let the scan write 0.
        elif (self.modem_role == "calling" and self.originate_line_ready
                and self._originate_parked_logged
                and not self._originate_advanced_logged
                and self.dm[0x3FC2] > 0x0002):
            print(f"[native-mips] originate side left the dial-page park -> "
                  f"TrnProgress 0x{self.dm[0x3FC2]:04x} at sample "
                  f"{self._media_samples}")
            if self._originate_saved_3a36 is not None:
                self.pm[0x3A36] = self._originate_saved_3a36
                self._originate_saved_3a36 = None
            self._originate_advanced_logged = True
        # Originate-side V.8 request (see ORIGINATE_V8). The dial page reaches
        # TrnProgress 0x0051 (training start) but never calls the kernel
        # page-request routine (PM 0x0680) the answerer uses to load V.8 -- the
        # legitimate path is an AT dial script this loopback bypasses. Stand in
        # for it by writing the page-request words once the caller has left the
        # dial-page park, the same class of intervention as the pin above.
        if (self.modem_role == "calling" and self.originate_v8
                and self.originate_line_ready
                and not self._originate_v8_requested
                and self._media_samples >= ORIGINATE_V8_AFTER
                and self.dm[0x3FC2] >= 0x0051
                and self.resident == 0x0271
                and not self.dm[0x3131]):
            # The kernel page-request routine (PM 0x0680) sets DM(0x3FB0)=
            # bootpage before writing DM(0x3131)/DM(0x3132). Forcing the
            # page request without the bootpage makes V.8 init see bootpage
            # 0x000c (AT online) instead of 0x0006 (V.8), which restricts
            # NORM_L to V.22-only (0x3004 instead of 0xb13f) and the V.8
            # negotiation falls back to V.22/FSK. Set the bootpage first.
            # Also force NORM_L to the full modulation mask, since the DIAL
            # init (PM 0x0581) wrote 0x3004 (V.22-only) on the originate side
            # while the answerer's NORM_L is 0xb13f (V.8/V.90/V.34/V.32bis/
            # V.22 all enabled).
            #
            # The write database base is DM 0x3EE0, so NORM_L +0x29 is
            # DM 0x3F09. An earlier form of this line used 0x3EE4 as the base
            # and wrote 0xb13f into DM 0x3F0D, which is not a database word at
            # all: the V.8 overlay makes 0 direct accesses to 0x3F0D and 15 to
            # 0x3F09, and in a live capture the *answerer's* untouched native
            # database holds 0xb13f at 0x3F09 and 0x0014 at 0x3F0D. The same
            # capture pins the base three more ways -- GEN_SETUP1 0x048c/0x0484
            # at 0x3EE1, INFO0_SETUP f1fd at +0x07, SPEED_SEL_L fffe at +0x2b --
            # and every other write database site in this file already uses
            # 0x3EE0. The 0x3EE4 reading came from PM 0x37c3/0x37c8 reading
            # DM(0x3EE4) and being taken for a GEN_SETUP1 role test; +0x04 is
            # V8_SETUP, and it is zero on both ends. So the caller's NORM_L was
            # never actually forced and V.8 has been offering the dial page's
            # V.22-only mask on every originate call.
            self.dm[0x3FB0] = 6
            was_norm_l = self.dm[0x3EE0 + 0x29]
            norm_l = ORIGINATE_NORM_L
            source = "EICON_ORIGINATE_NORM_L"
            if norm_l == "native":
                native = (self._native_answer_wdb[0x29]
                          if self._native_answer_wdb is not None else None)
                norm_l = native if native else 0xB13F
                source = ("native WDB" if native
                          else "no native WDB; documented default")
            if norm_l is None:
                print(f"[native-mips] originate NORM_L DM(0x3F09) left at "
                      f"0x{was_norm_l:04x} (EICON_ORIGINATE_NORM_L empty)")
            else:
                self.dm[0x3EE0 + 0x29] = norm_l
                print(f"[native-mips] originate NORM_L DM(0x3F09) "
                      f"0x{was_norm_l:04x} -> 0x{norm_l:04x} ({source})")
            self._originate_v8_requested = True
            if ORIGINATE_V8_KERNEL:
                # Publish the way PM 0x375d..0x3761 does: the page number in
                # DM(0x0491)/DM(0x3FB0), then bit 0x0100 of DM(0x3FC1), which
                # is the doorbell PM 0x06dc tests before jumping to the
                # page-request entry at PM 0x068d. Publishing without the bit
                # leaves the status block claiming page 6 while the old
                # overlay is still resident, so the bit is the request and the
                # rest is only its argument.
                self.dm[0x0491] = 6
                self.dm[0x3FC1] |= 0x0100
                print(f"[native-mips] originate side at TrnProgress "
                      f"0x{self.dm[0x3FC2]:04x} on SIG overlay without a V.8 "
                      f"request; publishing DM(0491)=6 DM(3FB0)=6 and leaving "
                      f"the request to the kernel at sample "
                      f"{self._media_samples} (EICON_ORIGINATE_V8_KERNEL)")
            else:
                self.dm[0x3131] = 0x0001
                self.dm[0x3132] = 0x025F
                print(f"[native-mips] originate side at TrnProgress "
                      f"0x{self.dm[0x3FC2]:04x} on SIG overlay without a V.8 "
                      f"request; writing DM(3131)=1 DM(3132)=0x025f to load "
                      f"V.8 at sample {self._media_samples} "
                      f"(EICON_ORIGINATE_V8)")
        # Probe: supply the INFO result the V.34 originate script branches on.
        # PM 0x2ef1 reads DM(0x3F89) and a zero sends block 0x1a91 (state
        # 0x0054) to 0x1ae5, the "wait for the line to go quiet" park at state
        # 0x0060, which the answerer's transmission never satisfies. Set it
        # while the INFO page is still resident so the value is in place before
        # the V.34 page's first sequencer pass. See ORIGINATE_V34_INFO.
        if (self.originate_v34_info and self.modem_role == "calling"
                and self.resident in (0x0260, 0x0261) and not self.dm[0x3F89]):
            if self.originate_v34_info == "derived":
                # Word 0 of the received message is the same 0x2000 on both
                # ends, i.e. not direction-specific content; word 1 is where
                # the two directions first differ. Cut the field from there
                # instead, on the same bit positions PM 0x3d79 uses.
                value = (self.dm[0x060B] >> 6) & 0x7F
            else:
                value = int(self.originate_v34_info, 0) & 0x7F
            if value:
                self.dm[0x3F89] = value
                if not self._originate_v34_info_logged:
                    print(f"[native-mips] originate V.34: INFO published "
                          f"DM(3F89)=0; pinning it to 0x{value:02x} at sample "
                          f"{self._media_samples} (EICON_ORIGINATE_V34_INFO)")
                    self._originate_v34_info_logged = True
        sport_word = code & 0xFF
        # The hardware PRI descriptor calls TIKRNL's registered continuation
        # only for this selected channel.  The generic SPORT frame walks the
        # kernel queue but cannot reconstruct that private callback.
        # DIAL activation still consumes this reconstructed line word. Once
        # V.8 is resident, however, DM3f08 is processed status owned by the
        # page; raw G.711 reaches it through the selected SPORT descriptor.
        if not self._private_line_active:
            self.dm[0x3F08] = code & 0xFF
        else:
            # The private descriptor supplies a processed line-status word
            # separately from the raw G.711 ring. Fixed dispatch holds 0x21
            # here during normal V.8 media; storing the octet itself corrupts
            # the result bits, while leaving it at 1 stalls the RX action.
            self.dm[0x3F08] = 0x0021
            # ADDSP V.90 User's Guide §3.3 specifies SPORT companding for the
            # T1/E1 interface. The private descriptor publishes the expanded
            # signed sample, not the compressed DS0 octet, to the page RX word.
            sport_word = self._sport_rx_word(code)
            self.dm[0x3763] = sport_word
        # Forced words go in before the page runs, so the code gated on them
        # sees them this sample rather than the next one.
        if FORCE_DM:
            self._apply_force_dm()
        # Native TIKRNL registers PM 0x0586 as the selected-channel ISR and
        # PM 0x0703 as its continuation. Model the private descriptor without
        # permanently replacing either global kernel dispatch slot.
        pm = self.pm
        saved_isr = install_isr_vector(pm)
        try:
            # The returned SPORT0 latch is deliberately discarded.  It is not a
            # transmit source: it carries the kernel's TDM slot mirror, i.e. the
            # received word delayed one frame.  Measured on run13 after the
            # bootpage 14 handoff, TX[t+1] == RX[t] for 16000/16000 samples, so
            # publishing it would echo the peer to itself.  The modem's own
            # transmit sample reaches the line only through DM(0x3fb4).
            # MIPS has already consumed the private command mailbox. Run the
            # relocated no-host continuation (source 06c1+7) if the selected
            # channel ISR yields, in the same C call to avoid per-sample FFI.
            if (self.native_bearer_activation and
                    not self._direct_selected_dispatch):
                # Keep the resident kernel foreground at PM 0x02a9 live. It
                # observes DM2f08 != DM2f09 and calls PM 0x01c1 to install the
                # selected task vectors. The compatibility path skips that
                # owner and resumes TIKRNL directly at PM 0x06c8.
                ADSP.adsp2181_modem_sample(
                    self.cpu, sport_word, self.silence, self.adsp_budget,
                    0x02A9, 0x02A8)
                if ADSP.adsp2181_idle(self.cpu):
                    # The tail of this continuation zeroes the six-word V.90
                    # mapping-frame block DM(0x3fa7..0x3fac) at PM
                    # 0x06ca..0x06cd (6 writes every frame, reached through the
                    # 0x04f8 call and its RTS). The page-14 generator refills
                    # that block once per 1333 Hz mapping frame at PM 0x2a52
                    # (`CALL (I4)`, AX0 = 0x3fa7) while the serializer at PM
                    # 0x2eed..0x2ef2 walks cursor DM(0x20de) across it one slot
                    # per 8 kHz frame, so the block has to survive six frames.
                    # Without the block surviving, five of every six downstream
                    # samples read zero and the line carries an impulse train
                    # instead of Sd. The clear runs inside the ISR above rather
                    # than in this continuation, so it is suppressed at the
                    # store itself when page 14 loads, not snapshotted here.
                    ADSP.adsp2181_call(self.cpu, 0x06C8, 0x02A8)
                    ADSP.adsp2181_run(self.cpu, self.adsp_budget)
            else:
                # The selected-channel foreground at PM 02b7 loads the sample
                # from TIKRNL's ring and calls the registered continuation at
                # PM 0703. V90D happens to keep the SPORT interrupt/foreground
                # path live and historically used the relocated 06c8 tail
                # directly. The V.34 page masks that interrupt during Phase 3;
                # resuming at 06c8 then runs only the kernel tail and never
                # invokes Core8kRoutine, leaving the answer modem silent at
                # TrnProgress 0x52. Drive the real selected foreground for V.34.
                continuation = 0x02B7 if self.resident == 0x0261 else 0x06C8
                # Count DM writes per address for page 8 only, so the census is
                # divided by page-8 samples and a rate means something. The
                # page cycles between 7 and 8, and writes made on page 7 would
                # otherwise be attributed to a page-8 sample count.
                if DM_CENSUS:
                    on_page8 = self.resident == 0x0261
                    if (DM_CENSUS_SAMPLES
                            and self._dm_census_samples >= DM_CENSUS_SAMPLES):
                        on_page8 = False
                    if on_page8 != self._dm_census_on:
                        if on_page8 and not self._dm_census_started:
                            ADSP.adsp2181_dm_census_clear(self.cpu)
                            ADSP.adsp2181_coverage_clear(self.cpu)
                            self._dm_census_started = True
                        ADSP.adsp2181_dm_census(self.cpu, 1 if on_page8 else 0)
                        # PM coverage on the same gate, so an execution count
                        # and a write count share a denominator and a page.
                        ADSP.adsp2181_coverage_gate(self.cpu,
                                                    1 if on_page8 else 0)
                        self._dm_census_on = on_page8
                    if on_page8:
                        self._dm_census_samples += 1
                # Unlike the mostly run-to-idle pages, V.34 leaves a continuous
                # foreground live between SPORT samples. Its effective budget
                # is still under investigation; keep it independently tunable
                # while checking the page-8 symbol scheduler.
                publish_latched = (V34_PUBLISH_LATCH
                                   and self.resident == 0x0261)
                if publish_latched:
                    # Arm before the frame; the value is read out in
                    # _line_sample() after it, so the tick carries the first
                    # sample the page produced rather than whichever one the
                    # budget happened to end on.
                    ADSP.adsp2181_latch_dm_write(
                        self.cpu, self.dm[0x3FB4] & 0x3FFF, 1)
                publish_paced = (V34_PUBLISH_PACED
                                 and self.resident == 0x0261)
                if publish_paced:
                    # The transmit word is reached through the pointer at
                    # DM(0x3fb4), which page 8 writes twelve times a call and
                    # always with 0x3764. Read it rather than assuming it, so
                    # a page that moves its transmit word still paces.
                    ADSP.adsp2181_stop_on_dm_write_n(
                        self.cpu, self.dm[0x3FB4] & 0x3FFF,
                        V34_PUBLISH_GROUP, 1)
                    ADSP.adsp2181_yield_on_stop(
                        self.cpu, 1 if V34_PUBLISH_YIELD else 0)
                    budget = V34_PUBLISH_MAX_CYCLES
                else:
                    budget = (V34_CYCLES_PER_SAMPLE
                              if self.resident == 0x0261 else self.adsp_budget)
                tracing = self._media_samples in TRACE_FRAMES
                if tracing:
                    # Armed here and cleared below, so the budget cannot bleed
                    # into the next frame and mislabel which frame a line
                    # belongs to -- the whole point is comparing one frame
                    # against its neighbours.
                    ADSP.adsp2181_trace_budget(self.cpu, TRACE_BUDGET)
                    print(f"[trace] frame {self._media_samples} armed for "
                          f"{TRACE_BUDGET} instructions "
                          f"[cyc={ADSP.adsp2181_cycles(self.cpu)}]")
                try:
                    ADSP.adsp2181_modem_sample(
                        self.cpu, sport_word, self.silence, budget,
                        continuation, 0x02A8)
                finally:
                    if tracing:
                        ADSP.adsp2181_trace_budget(self.cpu, 0)
                        print(f"[trace] frame {self._media_samples} ended "
                              f"[cyc={ADSP.adsp2181_cycles(self.cpu)}]")
                    if publish_paced:
                        if not ADSP.adsp2181_stop_dm_hit(self.cpu):
                            self._v34_unpublished_samples += 1
                        else:
                            self._v34_published_samples += 1
                        ADSP.adsp2181_stop_on_dm_write(self.cpu, 0, 0)
        finally:
            restore_isr_vector(pm, saved_isr)
        if PCSP_TRACE:
            packed = ADSP.adsp2181_pcsp_window(self.cpu)
            self._pcsp_rows.append(
                (self._media_samples, ADSP.adsp2181_cycles(self.cpu),
                 self.resident, (packed >> 8) & 0xFF, packed & 0xFF))
        wanted = self.dm[0x3132] & 0xFFFF
        if (self.force_info_after_v8 and self.resident == 0x025F
                and wanted != 0x0260 and self.dm[0x3FB0] not in (6, 7)):
            if self._media_samples < 12000:
                return
            wanted = 0x0260
            self.dm[0x3FB0] = 7
            self.dm[0x3132] = wanted
            self.forced_info_samples.append(self._media_samples)
            print(f"[native-mips] diagnostic post-V.8 fallback -> INFO "
                  f"at sample {self._media_samples}")
        page_ready = bool(self.dm[0x3FC1] & 0x0100)
        if (self.native_bearer_activation and self.dm[0x3137]
                and wanted != self.resident):
            page_ready = True
        if self._service_partial_overlay():
            return
        if (page_ready and self.dm[0x3131] and wanted == self.resident):
            # A request naming the page that is already resident is not a
            # page change, and re-running the entry path for one is
            # destructive: for V.8 it zeroes the TX word and both timer
            # sentinels, which on the originate side landed in the middle of
            # ANSam detection. Acknowledge it and leave the page alone.
            if not self._same_page_request_logged:
                print(f"[native-mips] page request for the resident page "
                      f"0x{wanted:04x} at sample {self._media_samples}; "
                      f"acknowledged without re-entering it")
                self._same_page_request_logged = True
            self.dm[0x3131] = 0x0000
        elif (page_ready and self.dm[0x3131]
                and wanted in self.download_descriptors):
            previous = self.resident
            if wanted != self.resident:
                self.load_native_overlay(wanted)
                if previous == 0x026A and wanted != 0x026A:
                    self._bulk_adapter_held = False
                    self._bulk_adapter_opcode = None
                    self._bulk_adapter_waiting_on = None
                    self._portable_bulk_delay.reset()
                    self._portable_bulk_active = False
                if wanted in (0x0261, 0x026A):
                    # V.34 PM 0x19d7 and V90D PM 0x1a24 call the native setup
                    # at 0x19a7, which tail-jumps through PM 0x19c8 to the
                    # shared worker at 0x1900. Publish the selected
                    # descriptor's sparse common-layer lower limit before the
                    # page resumes. V.34 then uses the firmware's bit/length
                    # gates; V90D additionally stays held until its rate block
                    # is coherent below.
                    bulk_limit = publish_bulk_lower_limit(self.dm)
                    print("[native-mips] published bulk descriptor lower "
                          f"limit DM(0x{bulk_limit:04x})=0xffff for "
                          f"0x{wanted:04x}")
                    # Descriptor words 0..7 as the shared worker reads them.
                    # PM 0x1900-0x1906 loads AX0 from word 0, SR1 from word 7,
                    # AY1 -- BulkLength -- from word 1 and AY0 from word 2;
                    # AY1 is what PM 0x1919/0x1923/0x1927 add on underflow, so
                    # a zero there is a ring pointer that cannot wrap.
                    _base = int(self.dm[0x32F7]) & 0x3FFF
                    print(f"[native-mips] bulk descriptor @DM(0x{_base:04x}): "
                          + ' '.join(f'[{k}]={int(self.dm[(_base + k) & 0x3FFF]):04x}'
                                     for k in range(8)))
                    # The seed and any stand-down decision belong to one
                    # page's delay line; both overlays reload the workspace.
                    self._bulk_seed_published = None
                    self._bulk_seed_yielded_to = None
                    self._bulk_seed_candidate = None
                    self._bulk_seed_candidate_frames = 0
                if wanted == 0x026A:
                    self._v90d_upstream_word = None
                    self._v90d_ceiling_floor_logged = False
                    self._v90d_no_common_rate_logged = False
                hold_tx_block = ((wanted == 0x026A and V90D_HOLD_TX_BLOCK)
                                 or (wanted == 0x0261 and V34_HOLD_TX_BLOCK))
                if hold_tx_block:
                    # PM 0x06cd is the six-count store that zeroes the V.90
                    # mapping-frame block DM(0x3fa7..0x3fac) every frame in the
                    # resident kernel's frame tail. The page-14 generator
                    # refills the block once per 1333 Hz mapping frame while
                    # the serializer walks it one slot per 8 kHz frame, so the
                    # clear has to stop for the block to survive its six reads.
                    pm_words = ADSP.adsp2181_pm(self.cpu)
                    self._v90d_saved_clear = pm_words[0x06CD]
                    pm_words[0x06CD] = 0x000000
                    print("[native-mips] diagnostic: suppressed per-frame "
                          "clear of the V90D mapping-frame block")
                elif (self._v90d_saved_clear is not None
                      and previous in (0x026A, 0x0261)):
                    # Leaving page 14, so put the clear back. This has to test
                    # `previous`: load_native_overlay has already set
                    # self.resident to `wanted`, so the old condition
                    # (self.resident == 0x026A) could never hold on the way out
                    # and the store stayed NOP'd for the rest of the call. PM
                    # 0x06cd is in the resident kernel rather than the overlay
                    # region, so a later page load does not replace it, and
                    # V.34 after a V.90 fallback inherited a kernel whose
                    # per-frame clear of DM(0x3fa7..0x3fac) never ran.
                    self.pm[0x06CD] = self._v90d_saved_clear
                    self._v90d_saved_clear = None
                    print("[native-mips] restored the per-frame clear of the "
                          f"V90D mapping-frame block leaving 0x{previous:04x}")
                if wanted == 0x0261 and (V34_BULK_HOLD or V34_PORTABLE_BULK):
                    pm = ADSP.adsp2181_pm(self.cpu)
                    if self._v34_bulk_opcode is None:
                        self._v34_bulk_opcode = pm[0x19C8]
                    pm[0x19C8] = 0x0A000F        # RTS
                    self._portable_bulk_delay.reset()
                    self._portable_bulk_active = False
                    print("[native-mips] V.34 bulk worker held: PM 0x19c8 "
                          f"RTSed (was 0x{self._v34_bulk_opcode:06x}) "
                          f"for 0x{wanted:04x}"
                          + ("; portable bounded delay selected"
                             if V34_PORTABLE_BULK else ""))
                if wanted == 0x026A and not V90D_BULK_ADAPTER_DISABLED:
                    # Enabled by default; EICON_V90D_BULK_ADAPTER=0 restores
                    # the old diagnostic bypass. Hold
                    # the same RTS in place and let _service_bulk_adapter()
                    # lift it once the adapter's parameters exist. Running it
                    # at page load is what destroys the state word, and the
                    # cause is a sequencing one -- see _service_bulk_adapter().
                    pm = ADSP.adsp2181_pm(self.cpu)
                    if self._bulk_adapter_opcode is None:
                        self._bulk_adapter_opcode = pm[0x19C8]
                    pm[0x19C8] = 0x0A000F
                    self._bulk_adapter_held = True
                    self._bulk_adapter_waiting_on = None
                    self._portable_bulk_delay.reset()
                    self._portable_bulk_active = False
                    if V90D_PORTABLE_BULK:
                        print("[native-mips] native bulk adapter held; portable "
                              f"bounded delay selected for 0x{wanted:04x}")
                    else:
                        print("[native-mips] bulk adapter held until the rate is "
                              f"published for 0x{wanted:04x}")
                if wanted == 0x026A and V90D_BULK_ADAPTER_DISABLED:
                    # Diagnostic: RTS out the tail of the 0x1900..0x19c8
                    # near/far echo bulk-delay adapter. With the adapter live
                    # the outer state machine stalls before 0x0080 (session
                    # 65's delayed bulk-cursor collision); with it disabled the
                    # machine reaches 0x0080 and transmits. Set
                    # EICON_V90D_BULK_ADAPTER=1 keeps the adapter running.
                    #
                    ADSP.adsp2181_pm(self.cpu)[0x19C8] = 0x0A000F
                    print("[native-mips] diagnostic: disabled the bulk "
                          f"adapter for 0x{wanted:04x}")
                self.switches.append(
                    (self._media_samples, self.dm[0x3FB0], wanted))
            if wanted == 0x025F:
                self._private_line_active = True
                # PM 2025 snapshots DIAL's processed line status before the
                # first callback. Raw PCMU idle 0xff has result bits 5-6 set;
                # hardware/direct dispatch presents idle status 1 here.
                self.dm[0x3F08] = 0x0001
            self.dm[0x3EEE] = 0x1000
            resume = self.dm[0x3143] & 0x3FFF
            if resume:
                ADSP.adsp2181_call(self.cpu, resume, 0x02A8)
                # A page whose init asks for a partial overlay must not run on
                # past the request. Hardware's kernel completes the transfer
                # inside the frame -- DM(0x3131) is posted at PM 0x069a and
                # cleared at PM 0x06e4 between two host samples (Session 185)
                # -- but this harness served it at the end of the *next*
                # sample, and V.32 spent the gap in its echo canceller with the
                # image's placeholder tap count still in place. Measured on the
                # answerer: the image lands at cyc 78,782,332, the page posts
                # bootpage 19 twenty-nine cycles later, and 5,571 cycles after
                # that it enters the LEC with DM(0x3754) = 0xfff4, which is a
                # 16,372-iteration loop that cannot finish inside a frame; the
                # next frame then stacks on top of the unfinished one and every
                # stack saturates (Sessions 188b-d). All of it happens inside
                # this one resume run, which is why it has to be stopped here
                # and not in the per-frame path -- stopping there fires on every
                # ordinary page transition, costs those frames their
                # continuation, and moved the V.8 classifier off V.32 outright.
                if not PARTIAL_STOP:
                    ADSP.adsp2181_run(self.cpu, self.adsp_budget)
                else:
                    # Stop on DM(0x3132), the download id, and not on the
                    # bootpage word: the page writes them in the order
                    # 0x3FB0=19 (PM 0x1f8c), 0x3131=1 (PM 0x069a), 0x3132=id
                    # (PM 0x069b), so at the bootpage write the id still holds
                    # the *previous* request. Stopping there served 0x0266 on
                    # top of itself -- a 19-block no-op that then consumed the
                    # served-once guard, so the real 0x0267 never arrived.
                    ADSP.adsp2181_stop_on_dm_write_n(self.cpu, 0x3132, 1, 1)
                    before = ADSP.adsp2181_cycles(self.cpu)
                    try:
                        ADSP.adsp2181_run(self.cpu, self.adsp_budget)
                        hit = ADSP.adsp2181_stop_dm_hit(self.cpu)
                    finally:
                        ADSP.adsp2181_stop_on_dm_write(self.cpu, 0, 0)
                    if hit and self.dm[0x3FB0] == PARTIAL_BOOTPAGE:
                        self._partial_stops += 1
                        # Serves, restores the blocks the partial merely
                        # repeats, and resumes the page at DM(0x3143) itself.
                        self._service_partial_overlay()
                    elif hit:
                        # An ordinary bootpage write during init. Give back
                        # only what the run had left, not a fresh allowance.
                        left = self.adsp_budget - int(
                            ADSP.adsp2181_cycles(self.cpu) - before)
                        if left > 0:
                            ADSP.adsp2181_run(self.cpu, left)
            if wanted == 0x025F:
                # The movable V.8 init leaves its temporary DM image in the
                # runtime TX word and zeroes the two disabled-timer sentinels.
                # Fixed-layout dispatch has completed this shared-state seam
                # with -1 timers and an empty adapter output before frame 1.
                self.dm[0x3995] = 0xFFFF
                self.dm[0x3999] = 0xFFFF
                self.dm[0x3764] = 0x0000
            self.dm[0x3EEE] &= ~0x1000
            # Acknowledge the request. The kernel's page-request service
            # clears DM(0x3131) when it has served one; leaving it set means
            # the next time page_ready comes round the same request is served
            # again. On the originate side that is not hypothetical: the
            # forced V.8 request (ORIGINATE_V8) writes DM(0x3131) from
            # outside and nothing on the DSP clears it, so V.8 was re-entered
            # mid-handshake -- zeroing its TX word and its two timer
            # sentinels while the state machine was in the middle of ANSam
            # detection.
            self.dm[0x3131] = 0x0000
            print(f"[native-mips] page request 0x{wanted:04x} "
                  f"(from 0x{previous:04x}) resumed at PM 0x{resume:04x}")
        elif self.dm[0x3131]:
            # A request nobody serves used to be silent, and a page waiting on
            # one looks exactly like a page that has stalled by itself: V.32
            # sat at TrnProgress 0x0000 cycling boot_request for a whole call
            # before --watch-dm-writes showed it was asking for something
            # (Session 184). Report the first one of each kind.
            reason = ("not staged" if wanted not in self.download_descriptors
                      else "page not ready" if not page_ready else "?")
            if wanted not in self._unserved_page_requests:
                self._unserved_page_requests.add(wanted)
                print(f"[native-mips] page request 0x{wanted:04x} unserved "
                      f"({reason}) at sample {self._media_samples}, resident "
                      f"0x{self.resident:04x} bootpage 0x{self.dm[0x3FB0]:04x}")
        # Diagnostic seam: PM 0x1982 preserves the far-bulk cursor in DM4,
        # but the portable V90D image initializes it to zero. A real selected
        # channel is expected to publish the first valid delay-line address.
        # Prime it once at activation to distinguish that missing publication
        # from a DSP state-machine or codec failure.
        if (self.prime_v90d_bulk_cursor and not self._v90d_bulk_cursor_primed
                and self.resident == 0x026A and self.dm[0x1FF7] == 0x0060):
            self.dm[4] = self.dm[0]
            self._v90d_bulk_cursor_primed = True
            print(f"[native-mips] diagnostic V90D bulk cursor DM4 "
                  f"primed to DM0=0x{self.dm[0]:04x}")
        self._service_negotiated_rates()
        self._service_bulk_lengths()
        self._service_bulk_adapter()
        self._service_rx_data()
        if self._tx_pending and not (self.dm[0x3FAD] & 0x8000):
            self.tx_accepted += 1
            self._tx_pending = False
            self._tx_words_pending = None
        # V.8 FFT work can span more than one execution budget. Preserve a
        # live page context and continue it on the next exact SPORT frame.

    def boot(self) -> None:
        """Compatibility with ``Card``/``LiveKernelModem``; already booted."""

    def configure_modem(self, role: str, law: str = "pcmu") -> None:
        # `role` here is the *signalling* role, which is always answer: the
        # card is driven through its incoming-call path. The modulation role
        # is self.modem_role and was fixed when the core was built, because
        # GEN_SETUP1 is published during the answer WDB cycle.
        if role != "answer":
            raise ValueError("native MIPS SIP backend answers calls only; "
                             "for the calling side of a handshake set "
                             "modem_role=calling, which is a data-pump role "
                             "and not a signalling direction")
        if law != self.law:
            raise ValueError(f"native core booted for {self.law}, not {law}")

    def _step_mips(self) -> None:
        try:
            self._service_n_data()
            # TIKRNL's V.90 TX-length mapper uses this private lookup table.
            # The overlay handoff clears the private DM image, although the
            # 0258 V.90 task's DM image defines these entries.  Without them
            # PM 0x05B5 maps the TX length to zero and PM 0x06B3 emits fill.
            if (self.resident == 0x026A and self.dm[0x31EE] == 0):
                for address, value in {
                    0x31EE: 0x0006, 0x31EF: 0x0200,
                    0x31F0: 0x0040, 0x31F1: 0x02E0,
                    0x31F2: 0xFD00, 0x31F3: 0x0060,
                    0x31F4: 0x0260,
                }.items():
                    self.dm[address] = value
                print("[native-mips] restored V.90 TX-length table "
                      "DM31EE..31F4")
            self.shim.phase = "native-sip"
            self.shim.call(MIPS_MAINLOOP, [], gp=GP, sp=STACK_TOP,
                           max_insns=500000)
            if self.resident in (0x0261, 0x026A):
                table = tuple(self.dm[address] for address in range(0x31C2, 0x31D6))
                trace = (self.resident, self.dm[0x31B2], table,
                         self.dm[0x3F09], self.dm[0x3F0A], self.dm[0x3F0B],
                         self.dm[0x3F61], self.dm[0x3F62], self.dm[0x31B3],
                         self.dm[0x31ED], self.dm[0x31EE], self.dm[0x31EF],
                         self.dm[0x31F0], self.dm[0x31F1], self.dm[0x31F2],
                         self.dm[0x31F3], self.dm[0x31F4],
                         self.dm[0x3FC0], self.dm[0x3FC1], self.dm[0x31A7],
                         self.dm[0x31AD], self.dm[0x31AE], self.dm[0x31AC],
                         self.dm[0x3F05], self.dm[0x3F06], self.dm[0x3F07],
                         self.dm[0x3FAD])
                previous_trace = self._v90_tx_source_trace
                # DI_control's request/ack bit changes every datagram.  It is
                # useful in the printed snapshot but must not turn this state
                # diagnostic into a per-datagram trace.
                if (previous_trace is None
                        or trace[:-1] != previous_trace[:-1]):
                    self._v90_tx_source_trace = trace
                    print("[native-mips] data TX source: "
                          f"page={trace[0]:04x} 31B2={trace[1]:04x} "
                          f"3F09..0B={trace[3]:04x}/{trace[4]:04x}/{trace[5]:04x} "
                          f"3F61/62={trace[6]:04x}/{trace[7]:04x} 31B3={trace[8]:04x} "
                          f"31ED..F4={'/'.join(f'{word:04x}' for word in trace[9:17])} "
                          f"31C2..31D5={'/'.join(f'{word:04x}' for word in table)} "
                          f"state={trace[17]:04x}/{trace[18]:04x} "
                          f"A={trace[19]:04x}/{trace[20]:04x}/{trace[21]:04x}/"
                          f"{trace[22]:04x} "
                          f"TXD={trace[23]:04x}/{trace[24]:04x}/{trace[25]:04x} "
                          f"DI={trace[26]:04x}")
            # Act as the host consumer so a long call cannot fill PR_RAM with
            # status indications.  Data-plane delivery will be attached to
            # the NL entity separately; signalling diagnostics are printed.
            for ind, ind_id, ind_ch, ref, payload in drain_indications(
                    self.shim, PR_RAM_PHYS):
                if ind == eicon_idi.N_DATA and self.nl_data_mode:
                    if self.lapm is not None:
                        if not self._nl_rx_seen:
                            # Takes the receive direction off the DSP mailbox
                            # so the decoder only ever sees one stream.  Not
                            # observed on hardware yet -- the CX call returned
                            # no N_DATA indication at all -- so this switch is
                            # reported when it first happens.
                            self._nl_rx_seen = True
                            print("[nl] first N_DATA indication: receive "
                                  "switched from the DSP mailbox to NL")
                        # Low-order bit first, matching the transmit packing.
                        self.lapm.feed([((value >> bit) & 1)
                                        for value in payload
                                        for bit in range(8)])
                        self._nl_rx_octets += len(payload)
                    continue
                if ind not in (N_CONNECT, 3):
                    print(f"[native-mips] IND 0x{ind:02x} "
                          f"Id=0x{ind_id:02x} Ch=0x{ind_ch:02x} "
                          f"Ref=0x{ref:04x} payload={payload.hex()}")
            for rc, rc_id, rc_ch, rc_ref in drain_return_codes(
                    self.shim, PR_RAM_PHYS):
                self._nl_return_code(rc, rc_id, rc_ch, rc_ref)
                if self.nl_data_mode or rc_id == self.nl_entity_id:
                    print(f"[nl] RC=0x{rc:02x} ({rc_name(rc)}) "
                          f"id=0x{rc_id:02x} ch=0x{rc_ch:02x} ref={rc_ref}")
        except Exception as exc:
            if not self._mips_fault_reported:
                print(f"[native-mips] runtime supervisor stopped: {exc}")
                self._mips_fault_reported = True

    def _apply_force_dm(self) -> None:
        """Write the EICON_FORCE_DM words, once per sample.

        Per sample rather than once, because the words worth forcing are the
        ones the firmware republishes -- DM(0x2140), the reason this exists, is
        rewritten by the script block loader on every page entry, so a single
        write would be undone before it changed anything.
        """
        resident = self.resident
        for address, value, page in FORCE_DM:
            if page is None or page == resident:
                if self.dm[address] != value:
                    self._forced_dm_writes += 1
                    if self._forced_dm_writes == 1:
                        # Proof the patch is live. A force that never
                        # overwrites anything is a null experiment that reads
                        # exactly like a negative result.
                        print(f"[force-dm] first overwrite: DM(0x{address:04x}) "
                              f"0x{self.dm[address]:04x} -> 0x{value:04x} at "
                              f"sample {self._media_samples}")
                self.dm[address] = value

    def _write_pm_dump(self) -> None:
        """Snapshot live PM at exit, independently of the census.

        Independently, because the census only runs on page 8 and the calls
        worth dumping include ones that never reach it -- a V.8 that selects
        V.22 has no page 8 at all, and its classifier is exactly the code that
        needs reading.
        """
        if self._pm_dumped:
            return
        self._pm_dumped = True
        lo, hi, target = PM_DUMP.split(":", 2)
        target = target.split("@", 1)[0]
        lo, hi = int(lo, 0), int(hi, 0)
        pm = ADSP.adsp2181_pm(self.cpu)
        with open(target, "w") as handle:
            handle.write("address,word,upper16\n")
            for address in range(lo, hi):
                word = pm[address] & 0xFFFFFF
                handle.write(f"0x{address:04x},0x{word:06x},"
                             f"0x{(word >> 8) & 0xFFFF:04x}\n")
        print(f"[pm-dump] PM 0x{lo:04x}..0x{hi:04x} (resident overlay "
              f"0x{self.resident:04x}) -> {target}")

    def _write_dm_dump(self) -> None:
        """Snapshot live DM, the data-memory twin of _write_pm_dump()."""
        if self._dm_dumped:
            return
        self._dm_dumped = True
        lo, hi, target = DM_DUMP.split(":", 2)
        target = target.split("@", 1)[0]
        lo, hi = int(lo, 0), int(hi, 0)
        with open(target, "w") as handle:
            handle.write("address,word\n")
            for address in range(lo, hi):
                handle.write(f"0x{address:04x},0x{self.dm[address] & 0xFFFF:04x}\n")
        print(f"[dm-dump] DM 0x{lo:04x}..0x{hi:04x} (resident overlay "
              f"0x{self.resident:04x}) -> {target}")

    def _write_pcsp_trace(self) -> None:
        """Per-frame PC-stack depth as CSV, plus the shape of it in one line.

        The summary is what the question needs: if the per-frame minimum ever
        returns to where it started, the stack unwinds and the depth is
        nesting; if the minimum only ever climbs, frames are leaking.
        """
        with open(PCSP_TRACE, "w") as handle:
            handle.write("sample,cycle,resident,pcsp_min,pcsp_max\n")
            for sample, cycle, resident, lo, hi in self._pcsp_rows:
                handle.write(f"{sample},{cycle},0x{resident:04x},{lo},{hi}\n")
        floors = [lo for _, _, _, lo, _ in self._pcsp_rows]
        print(f"[pcsp] {len(self._pcsp_rows)} frames -> {PCSP_TRACE}"
              + (f"; per-frame floor {min(floors)}..{max(floors)}, "
                 f"peak {max(hi for *_, hi in self._pcsp_rows)}"
                 if floors else ""))

    def _report_pin_pm(self) -> None:
        """Say how often each PM pin actually undid a store.

        A pin that never fires makes the run identical to the control, so an
        unchanged result would mean nothing. Printing the count is what tells
        "the patch was suppressed and it changed nothing" apart from "the
        suppression never happened".
        """
        for address, value in PIN_PM:
            hits = ADSP.adsp2181_pin_pm_hits(self.cpu, address)
            note = "" if hits else "  <-- never fired, this A/B tested nothing"
            print(f"[pin-pm] PM 0x{address:04x} held at 0x{value:06x}: "
                  f"{hits} stores undone{note}")

    def _write_dm_census(self) -> None:
        """Dump the page-8 DM write census as CSV: address, writes, per sample.

        Written at exit rather than at a fixed sample, because the two loopback
        endpoints reach page 8 at different times and neither knows when the
        other has finished.
        """
        if not self._dm_census_started or not self._dm_census_samples:
            return
        rows = []
        for address in range(0x4000):
            count = ADSP.adsp2181_dm_census_count(self.cpu, address)
            if count:
                rows.append((address, count, count / self._dm_census_samples))
        path = Path(DM_CENSUS)
        with path.open("w") as handle:
            handle.write(f"# page-8 samples: {self._dm_census_samples}\n")
            handle.write("address,writes,per_sample\n")
            for address, count, rate in rows:
                handle.write(f"0x{address:04x},{count},{rate:.6f}\n")
        print(f"[dm-census] {len(rows)} written addresses over "
              f"{self._dm_census_samples} page-8 samples -> {path}")
        for address in PM_COVERAGE:
            count = ADSP.adsp2181_coverage_count(self.cpu, address)
            print(f"[pm-coverage] PM 0x{address:04x}: {count} executions "
                  f"({count / self._dm_census_samples:.4f} per page-8 sample)")

    def warm_up(self, passes: int | None = None) -> None:
        """Translate the supervisor's media-phase path before media starts.

        Unicorn JITs on first execution, and the mainloop path taken once the
        bearer is up is not the one boot took: the first in-call pass costs
        about 93 ms against a 20 ms budget, which is five media ticks lost at
        the instant the call goes live. That is the worst possible moment --
        DIAL is selecting a modulation and the peer is measuring what comes
        back -- and it shows up as RTP backlog rather than as anything visibly
        wrong with the firmware.

        The passes themselves are ordinary idle supervisor polls, which is what
        a real card does between bearer activation and its first DS0 sample --
        this harness ran none there, because polls are driven by the sample
        clock. They are not free of consequence: three of them move the whole
        replay timeline one sample earlier. Pin EICON_MIPS_WARMUP when comparing
        against a recorded capture.
        """
        if passes is None:
            passes = MIPS_WARMUP_PASSES
        for _ in range(passes):
            self._step_mips()

    def frame_fast(self, code: int, sample_index: int) -> int:
        self._frame_core(code)
        if (sample_index + 1) % self.mips_interval == 0:
            self._step_mips()
            # The V.90 task has an initialization/fill path that can write
            # TXD0..TXD2 while the DSP request remains asserted.  Re-assert
            # the host-owned datagram after that task pass; the DSP will clear
            # DI_control on the following data-pump cycle.
            if (self._tx_pending and self._tx_words_pending is not None
                    and (self.dm[0x3FAD] & 0x8000)):
                self.dm[0x3F05], self.dm[0x3F06], self.dm[0x3F07] = (
                    self._tx_words_pending)
        if self.resident == 0x026A:
            # The block is held across the resident kernel's per-frame clear so
            # the serializer can walk all six slots, which means a generator
            # that stops leaves its last mapping frame on the line for ever.
            # It must not be cleared on content, though: Phase 4 opens with Ri,
            # "the single PCM codeword whose Ucode is UINFO for all data frame
            # intervals" (§9.4.1.1), so a constant block is a legitimate signal
            # and indistinguishable from a stale one by inspection. Ask the core
            # whether the generator dispatch at PM 0x2a52 is still running
            # instead, and only clear when it has genuinely gone quiet.
            generated = ADSP.adsp2181_coverage_count(self.cpu, 0x2A52)
            if generated != self._v90d_generated:
                self._v90d_generated = generated
                self._v90d_generator_idle = 0
            else:
                self._v90d_generator_idle += 1
                if (V90D_HOLD_TX_BLOCK
                        and self._v90d_generator_idle == 12):
                    for address in range(0x3FA7, 0x3FAD):
                        self.dm[address] = 0
                    print("[native-mips] V90D generator idle for two mapping "
                          f"frames at sample {self._media_samples}; cleared the "
                          "held block (state "
                          f"0x{self.dm[0x1FF7]:04x})")
            # Page 14 publishes the sample itself in DM(0x3fb4): PM 0x19ee
            # re-primes the generic pointer 0x3764 every frame and PM 0x1a1e
            # then overwrites it with the word the V90D serializer left in
            # DM(0x3fa7). Nothing writes DM(0x3764) at all while V90D
            # transmits, so there is nothing for the generic indirection to
            # dereference here; applying it turns each sample into whatever
            # unrelated word lives at that address.
            value = self.dm[0x3FB4]
        elif V34_PUBLISH_LATCH and self.resident == 0x0261:
            # The first value the page published this tick. -1 means it
            # published nothing, which is a real state on a page that is
            # deliberately quiet: hold the last sample rather than inventing
            # one, which is what the pointer read below would do.
            latched = ADSP.adsp2181_latched_dm_write(self.cpu)
            if latched < 0:
                self._v34_unpublished_samples += 1
                value = self._v34_last_line_sample
            else:
                self._v34_published_samples += 1
                value = latched
                self._v34_last_line_sample = latched
        else:
            pointer = self.dm[0x3FB4] & 0x3FFF
            value = self.dm[pointer] if pointer else 0
        return value - 0x10000 if value & 0x8000 else value


def create_native_mips_modem(kernel: Path, tikrnl: Path, law: str = "pcmu",
                             image: Path = Path("docs/firmware/te_dmlt.pm"),
                             dsp_combifile: Path = Path("docs/firmware/dspdload.bin"),
                             channel: int = 1, call_steps: int = 2,
                             dsp_pump: int = 256,
                             force_info_after_v8: bool = False,
                             tx_prbs: bool = False,
                             tx_v42: bool = False,
                             tx_v42bis: bool = False,
                             tx_v44: bool = False,
                             prime_v90d_bulk_cursor: bool = False,
                             native_bearer_activation: bool = False,
                             mips_interval: int = 160,
                             originate_line_ready: bool | None = None,
                             originate_v8: bool | None = None,
                             modem_role: "str | None" = None) -> NativeMipsModem:
    """Boot the real card firmware and return its naturally assigned modem.

    ``modem_role`` selects the modulation role written to GEN_SETUP1 and
    defaults to EICON_MODEM_ROLE. The signalling path is the answering one
    either way: the card is told an incoming call arrived, and the role only
    decides which side of the modem handshake its data pump takes. Those are
    separate things, and keeping them separate is what lets two instances
    train against each other without an outgoing Q.931 state machine.
    """
    if law not in ("pcmu", "pcma"):
        raise ValueError("native MIPS backend supports only pcmu or pcma")
    if not ISR_VECTOR_PATCH:
        # Loud, because it is off the path every archived capture was taken on.
        print("[native-mips] EICON_ISR_VECTOR_PATCH=0: PM 0x00B5 left alone; "
              "the firmware's own SPORT vector dispatches")
    cpu = ADSP.adsp2181_create()
    ADSP.adsp2181_reset(cpu)
    ADSP.adsp2181_set_idma_boot_hold(cpu, 1)
    shim = MipsShim(image, cpu)
    shim.native_bearer_activation = native_bearer_activation
    shim.native_kernel = kernel
    shim.native_tikrnl = tikrnl
    shim.write32(0x800fbe30, STUB_VIRT)
    base = protocol_end_addr(image)
    staged = build_dsp_code_image(
        dsp_combifile, CARDTYPE_DIVASRV_P_30M_PCI, base,
        extra_download_ids=DSP_EXTRA_DOWNLOADS)
    descriptors = {entry.download_id: base + 4 + index * 0x30
                   for index, entry in enumerate(staged.downloads)}
    staged_dm_blocks = staged.dm_blocks
    args = SimpleNamespace(
        image=image, tikrnl=tikrnl, dsp_combifile=dsp_combifile,
        dsp_code_base=None, card_type=CARDTYPE_DIVASRV_P_30M_PCI,
        force_law=2 if law == "pcmu" else 1,
        dsp_pump=dsp_pump, entity="both", channel=channel,
        call_direction="answering", fake_call_ingress=True,
        inject_call_ingress=True, synthesize_call_ingress=False,
        ingress_entity_slot=0, legacy_sig_req_id=False,
        connect=True, force_modem_dsp_assign=False, call_steps=call_steps,
        dump_entities=False, dump_entity_limit=0, native_dm_out=None,
        trace_calls=False, trace_call_limit=0,
        native_bearer_activation=native_bearer_activation)
    run_mainloop(shim, args)
    block = shim.service_assign_block
    core = shim.cores.get(block) if block is not None else None
    if core is None:
        raise RuntimeError("native incoming call did not assign a modem DSP core")
    print(f"[native-mips] SIP media attached to DSP block 0x{block:08x} "
          f"using {law}")
    modem = NativeMipsModem(
        shim, core, law, block, descriptors, staged_dm_blocks,
        force_info_after_v8=force_info_after_v8, tx_prbs=tx_prbs,
        tx_v42=tx_v42, tx_v42bis=tx_v42bis,
        tx_v44=tx_v44,
        prime_v90d_bulk_cursor=prime_v90d_bulk_cursor,
        native_bearer_activation=native_bearer_activation,
        mips_interval=mips_interval,
        originate_line_ready=originate_line_ready,
        originate_v8=originate_v8,
        modem_role=modem_role or MODEM_ROLE)
    print(f"[native-mips] modulation role: {modem.modem_role} "
          f"(GEN_SETUP1=0x{GEN_SETUP1_ROLE[modem.modem_role]:04x})")
    if (modem.modem_role == "calling"
            and not modem.originate_line_ready):
        print("[native-mips] originate-side line-ready pin is OFF "
              "(EICON_ORIGINATE_LINE_READY=0): the calling side will park "
              "at the dial page and transmit nothing (Sessions 95-96)")
    # Before the bearer is attached, so the supervisor polls this costs are
    # indistinguishable from the boot-time ones and the sample clock has not
    # started. Warming after attachment works equally well but shifts the whole
    # replay timeline by a sample.
    modem.warm_up()
    if not native_bearer_activation:
        modem.start_native_task()
    if native_bearer_activation:
        # SERVICE_ASSIGN already installed the resident kernel and relocated
        # task. Reapplying extracted source-address PM here would undo that
        # relocation after the connected-task command has run.
        ADSP.adsp2181_set_idma_boot_hold(core, 0)
        modem.complete_native_answer()
        print("[native-mips] using native lower-PRI task attachment with "
              "ADDSP answer WDB completion")
    else:
        modem.attach_connected_bearer()
    return modem


def stage_direct_tikrnl_core(args):
    """Create a standalone PRI-kernel+TIKRNL core for forced call assignment.

    The PR_RAM mainloop emulates every card DSP as a separate core while the
    MIPS firmware boots the card.  The direct service-assign helper, however,
    talks through the synthetic host register block at RAM+0x5000, which is
    wired to shim.cpu.  Use a deliberately staged TIKRNL core there so the
    switch-on command ring lands in a modem task that can consume it.
    """
    cpu = ADSP.adsp2181_create()
    ADSP.adsp2181_reset(cpu)
    load_pm_words(cpu, args.kernel / "pm.bin")
    load_dm_words(cpu, args.kernel / "dm.bin")
    ADSP.adsp2181_run(cpu, 1000)
    pm = ADSP.adsp2181_pm(cpu)
    dm = ADSP.adsp2181_dm(cpu)
    for line in (args.tikrnl / "pm.words").read_text().splitlines():
        a, v = line.split()
        pm[int(a, 16)] = int(v, 16)
    for line in (args.tikrnl / "dm.words").read_text().splitlines():
        a, v = line.split()
        dm[int(a, 16)] = int(v, 16)
    ADSP.adsp2181_call(cpu, 0x0672, 0x02A8)
    ADSP.adsp2181_run(cpu, 1000000)
    return cpu


def load_adsp_module(cpu, module: Path) -> None:
    """Layer one extracted ADSP download directory onto an existing core."""
    pm = ADSP.adsp2181_pm(cpu)
    dm = ADSP.adsp2181_dm(cpu)
    for line in (module / "pm.words").read_text().splitlines():
        a, v = line.split()
        pm[int(a, 16)] = int(v, 16)
    for line in (module / "dm.words").read_text().splitlines():
        a, v = line.split()
        dm[int(a, 16)] = int(v, 16)


def find_extracted_download(download_id: int) -> Path | None:
    roots = (
        Path("artifacts/eicon-dsp/overlays"),
        Path("artifacts/eicon-dsp/sig-path"),
        Path("artifacts/eicon-dsp/build-117-926/tikrnl"),
    )
    for root in roots:
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            meta = entry / "metadata.json"
            if not meta.is_file():
                continue
            try:
                if json.loads(meta.read_text()).get("download_id") == download_id:
                    return entry
            except (OSError, json.JSONDecodeError):
                continue
    return None


def pump_direct_tikrnl_core(cpu, words: int) -> None:
    """Let TIKRNL consume the switch-on database ring written by run_assign."""
    for _ in range(words):
        dm = ADSP.adsp2181_dm(cpu)
        if dm[0x3315] != dm[0x3316]:
            ADSP.adsp2181_host_write(cpu, 0x7310, 0x0001)
        ADSP.adsp2181_set_irq(cpu, 3, 1)  # SPORT0_RX
        ADSP.adsp2181_set_irq(cpu, 3, 0)
        ADSP.adsp2181_set_irq(cpu, 6, 1)  # IRQE doorbell
        ADSP.adsp2181_run(cpu, 5000)
        ADSP.adsp2181_set_irq(cpu, 6, 0)
        ADSP.adsp2181_run(cpu, 5000)
        ADSP.adsp2181_call(cpu, 0x064A, 0x02A8)
        ADSP.adsp2181_run(cpu, 20000)
        service_vector = ADSP.adsp2181_dm(cpu)[0x3308]
        if service_vector:
            ADSP.adsp2181_call(cpu, service_vector, 0x02A8)
            ADSP.adsp2181_run(cpu, 20000)
        ADSP.adsp2181_call(cpu, 0x06BB, 0x02A8)
        ADSP.adsp2181_run(cpu, 20000)


def linear_to_mulaw(sample: int) -> int:
    sample = max(-32768, min(32767, sample))
    sign = 0x80 if sample < 0 else 0
    if sample < 0:
        sample = -sample - 1
    sample += 0x84
    if sample > 0x7FFF:
        sample = 0x7FFF
    segment = 0
    shifted = sample >> 5
    while shifted and segment < 8:
        shifted >>= 1
        segment += 1
    if segment >= 8:
        return (sign | 0x7F) ^ 0xFF
    return (sign | (segment << 4) | ((sample >> (segment + 3)) & 0xF)) ^ 0xFF


def make_g711_stimulus(kind: str, samples: int, code: int,
                       freq: float = 2100.0, amp: int = 20000) -> list[int]:
    """Build an 8 kHz u-law G.711 stimulus for the forced DSP RX path."""
    if samples <= 0:
        return []
    if kind == "constant":
        return [code & 0xFF] * samples
    if kind == "silence" or not freq:
        return [0xFF] * samples
    result = []
    phase_offset = 0.0
    reversal_samples = int(0.450 * SAMPLE_RATE)
    for index in range(samples):
        if kind == "ansam" and index and index % reversal_samples == 0:
            phase_offset += math.pi
        envelope = (1.0 + 0.2 * math.sin(2 * math.pi * 15 * index / SAMPLE_RATE)
                    if kind == "ansam" else 1.0)
        linear = int(amp * envelope
                     * math.sin(2 * math.pi * freq * index / SAMPLE_RATE
                                + phase_offset))
        result.append(linear_to_mulaw(linear))
    return result


def restore_direct_pcm_pointers(cpu) -> None:
    """Restore the one-line pointer-mode PCM buffers used by TIKRNL pages."""
    dm = ADSP.adsp2181_dm(cpu)
    dm[DM_COUPLED_BUFFER_MODE] = 0x0004
    dm[DM_RX_BUFFER_POINTER] = DM_RX_BUFFER
    dm[DM_TX_BUFFER_POINTER] = DM_TX_BUFFER


def probe_direct_tikrnl_g711(cpu, samples: int, code: int,
                             stimulus_kind: str = "constant",
                             stimulus_freq: float = 2100.0,
                             stimulus_amp: int = 20000,
                             bridge_tx: bool = False,
                             restore_pcm_pointers: bool = False) -> None:
    """Feed raw G.711 codewords into the assigned direct TIKRNL core.

    It writes u-law octets to the data-pump line words that DIAL/V.8 consume
    (`DM 0x3f08`/`0x3f09`) and runs TIKRNL's frame entry.  With bridge_tx set,
    it also copies the task pointer-mode TX buffer to the kernel TDM output
    latch (`DM 0x2e52`) before strobing SPORT0_RX so TX0 emits it.
    """
    dm = ADSP.adsp2181_dm(cpu)
    stimulus = make_g711_stimulus(stimulus_kind, samples, code,
                                  stimulus_freq, stimulus_amp)
    tx_words: list[int] = []
    natural_tx_words: list[int] = []
    bridge_tx_words: list[int] = []
    bridged_words: list[int] = []
    rx_index = 0

    def rx_cb(_cpu, port):
        if port != 0:
            return 0
        if not stimulus:
            return code & 0xFF
        return stimulus[min(rx_index, len(stimulus) - 1)] & 0xFF

    def tx_cb(_cpu, port, value):
        if port == 0:
            tx_words.append(value & 0xFFFF)

    def timer_cb(_cpu, _enabled):
        return None

    callbacks = (RX_CB(rx_cb), TX_CB(tx_cb), TIM_CB(timer_cb))
    ADSP.adsp2181_set_callbacks(cpu, *callbacks)
    changes = 0
    prev = None
    page_tx_seen = False
    page_tx_counts: list[int] = []
    page_tx_samples: list[int] = []
    page_tx_ring_nonzero_max = 0
    for index in range(samples):
        rx_index = index
        rx_code = stimulus[index] if stimulus else (code & 0xFF)
        dm[0x3F08] = rx_code & 0xFF
        dm[0x3F09] = rx_code & 0xFF
        # Exercise both paths: the direct frame entry makes page progress
        # visible, while SPORT0 RX/TX strobes give the kernel bridge a chance
        # to move line samples to and from the serial port callbacks.
        tx_mark = len(tx_words)
        ADSP.adsp2181_set_irq(cpu, 3, 1)  # SPORT0_RX
        ADSP.adsp2181_set_irq(cpu, 3, 0)
        ADSP.adsp2181_run(cpu, 50000)
        natural_tx_words.extend(tx_words[tx_mark:])
        ADSP.adsp2181_call(cpu, 0x06BB, 0x02A8)
        ADSP.adsp2181_run(cpu, 50000)
        ADSP.adsp2181_set_irq(cpu, 4, 1)  # SPORT0_TX
        ADSP.adsp2181_set_irq(cpu, 4, 0)
        ADSP.adsp2181_run(cpu, 50000)
        wanted = dm[0x31AA]
        if dm[0x31A9] and wanted:
            module = find_extracted_download(wanted)
            if module is not None:
                load_adsp_module(cpu, module)
                if restore_pcm_pointers:
                    restore_direct_pcm_pointers(cpu)
                dm[0x3EEE] = 0x1000  # BOOTFINISHED; mirrors host overlay ack
                resume = dm[0x31BB]
                if resume:
                    ADSP.adsp2181_call(cpu, resume, 0x02A8)
                    ADSP.adsp2181_run(cpu, 100000)
                print(f"[g711] served requested overlay 0x{wanted:04x} "
                      f"from {module.name}")
            else:
                print(f"[g711] requested overlay 0x{wanted:04x}, "
                      "but no extracted image is available")
        if bridge_tx:
            if restore_pcm_pointers:
                restore_direct_pcm_pointers(cpu)
            tx_ptr = dm[DM_TX_BUFFER_POINTER] & 0x3FFF
            tx_value = dm[tx_ptr] if tx_ptr else dm[0x3F09]
            bridged_words.append(tx_value & 0xFFFF)
            dm[DM_TDM_OUTPUT_LATCH] = tx_value & 0xFFFF
            tx_mark = len(tx_words)
            ADSP.adsp2181_set_irq(cpu, 3, 1)  # SPORT0_RX drives TDM TX0
            ADSP.adsp2181_set_irq(cpu, 3, 0)
            ADSP.adsp2181_run(cpu, 20000)
            bridge_tx_words.extend(tx_words[tx_mark:])
        tx_pointer = dm[DM_TX_BUFFER_POINTER] & 0x3FFF
        if tx_pointer == DM_PAGE_TX_SAMPLE:
            page_tx_seen = True
            page_tx_counts.append(dm[DM_PAGE_TX_COUNT])
            page_tx_samples.append(dm[DM_PAGE_TX_SAMPLE])
            page_tx_ring_nonzero_max = max(
                page_tx_ring_nonzero_max,
                sum(dm[DM_PAGE_TX_RING + n] != 0
                    for n in range(DM_PAGE_TX_RING_WORDS)))
        now = (dm[0x3F08], dm[0x3F09], dm[0x3FB0], dm[0x3FB2],
               dm[0x3FB3], dm[0x3FC1], dm[0x31A9], dm[0x31AA],
               dm[DM_TX_BUFFER_POINTER], dm[tx_pointer] if tx_pointer else 0)
        if now != prev:
            changes += 1
            if changes <= 12:
                print(f"[g711] sample {index:04d}: "
                      f"3F08={now[0]:04x} 3F09={now[1]:04x} "
                      f"3FB0={now[2]:04x} 3FB2={now[3]:04x} "
                      f"3FB3={now[4]:04x} 3FC1={now[5]:04x} "
                      f"31A9={now[6]:04x} 31AA={now[7]:04x} "
                      f"3FB4={now[8]:04x} TXPTR={now[9]:04x}")
            prev = now
    if stimulus_kind == "constant":
        source_desc = f"raw G.711 octets 0x{code & 0xff:02x}"
    elif stimulus_kind == "silence":
        source_desc = "u-law silence"
    else:
        source_desc = (f"{stimulus_kind} {stimulus_freq:g}Hz "
                       f"amp={stimulus_amp}")
    print(f"[g711] fed {samples} {source_desc}; line-state changes={changes}")
    if page_tx_seen:
        from collections import Counter
        sample_counts = Counter(page_tx_samples)
        queue_min = min(page_tx_counts) if page_tx_counts else 0
        queue_max = max(page_tx_counts) if page_tx_counts else 0
        print("[g711] V.22FC page TX adapter DM3764: "
              f"frames={len(page_tx_samples)} "
              f"nonzero={sum(value != 0 for value in page_tx_samples)} "
              f"queue-count={queue_min}..{queue_max} "
              f"write=DM{dm[DM_PAGE_TX_WRITE_POINTER] & 0x3fff:04x} "
              f"read=DM{dm[DM_PAGE_TX_READ_POINTER] & 0x3fff:04x} "
              f"ring-nonzero-max={page_tx_ring_nonzero_max}/{DM_PAGE_TX_RING_WORDS} "
              f"top={','.join(f'{value:04x}:{count}' for value, count in sample_counts.most_common(8))}")
    if bridged_words:
        from collections import Counter
        counts = Counter(bridged_words)
        non_idle = sum(value not in (0x0000, 0x00ff, 0x0400)
                       for value in bridged_words)
        print(f"[g711] bridged task TX DM[3FB4]->DM{DM_TDM_OUTPUT_LATCH:04x}: "
              f"words={len(bridged_words)} unique={len(counts)} non_idle={non_idle} "
              f"top={','.join(f'{value:04x}:{count}' for value, count in counts.most_common(8))} "
              f"first16={' '.join(f'{value:04x}' for value in bridged_words[:16])}")
        bridge_counts = Counter(bridge_tx_words)
        bridge_non_idle = sum(value not in (0x0000, 0x00ff, 0x0400)
                              for value in bridge_tx_words)
        print(f"[g711] SPORT0 TX0 bridged captures: words={len(bridge_tx_words)} "
              f"unique={len(bridge_counts)} non_idle={bridge_non_idle} "
              f"top={','.join(f'{value:04x}:{count}' for value, count in bridge_counts.most_common(8))} "
              f"first16={' '.join(f'{value:04x}' for value in bridge_tx_words[:16])}")
    if natural_tx_words:
        from collections import Counter
        counts = Counter(natural_tx_words)
        non_idle = sum(value not in (0x0000, 0x00ff, 0x0400)
                       for value in natural_tx_words)
        print(f"[g711] SPORT0 TX0 natural captures: words={len(natural_tx_words)} "
              f"unique={len(counts)} non_idle={non_idle} "
              f"top={','.join(f'{value:04x}:{count}' for value, count in counts.most_common(8))} "
              f"first16={' '.join(f'{value:04x}' for value in natural_tx_words[:16])}")
    if tx_words:
        from collections import Counter
        counts = Counter(tx_words)
        non_idle = sum(value not in (0x0000, 0x00ff, 0x0400)
                       for value in tx_words)
        print(f"[g711] SPORT0 TX0 captured: words={len(tx_words)} "
              f"unique={len(counts)} non_idle={non_idle} "
              f"top={','.join(f'{value:04x}:{count}' for value, count in counts.most_common(8))} "
              f"first16={' '.join(f'{value:04x}' for value in tx_words[:16])}")
    else:
        print("[g711] SPORT0 TX0 captured: words=0")


def scan_direct_tikrnl_tx_source(cpu, marker: int = 0x0055,
                                 start: int | None = None,
                                 end: int | None = None) -> None:
    """Poke likely TX buffers and see which one appears on SPORT0 TX0."""
    dm = ADSP.adsp2181_dm(cpu)
    hits: list[tuple[str, int, int]] = []
    tx_words: list[int] = []

    def rx_cb(_cpu, _port):
        return 0xFF

    def tx_cb(_cpu, port, value):
        if port == 0:
            tx_words.append(value & 0xFFFF)

    def timer_cb(_cpu, _enabled):
        return None

    callbacks = (RX_CB(rx_cb), TX_CB(tx_cb), TIM_CB(timer_cb))
    ADSP.adsp2181_set_callbacks(cpu, *callbacks)
    if start is not None or end is not None:
        lo = 0 if start is None else start
        hi = 0x4000 if end is None else end
        ordered_candidates = list(range(max(0, lo), min(0x4000, hi)))
    else:
        candidates = (
            list(range(0x2E00, 0x2E60))
            + list(range(0x2B00, 0x2B10))
            + [0x2E52, 0x3F08, 0x3F09, 0x3F0F, 0x3FB4]
        )
        seen = set()
        ordered_candidates = []
        for addr in candidates:
            if addr not in seen:
                seen.add(addr)
                ordered_candidates.append(addr)
    for addr in ordered_candidates:
        saved = dm[addr]
        tx_words.clear()
        dm[addr] = marker & 0xFFFF
        ADSP.adsp2181_set_irq(cpu, 3, 1)  # SPORT0_RX drives the TDM walk
        ADSP.adsp2181_set_irq(cpu, 3, 0)
        ADSP.adsp2181_run(cpu, 20000)
        if tx_words and tx_words[-1] == (marker & 0xFFFF):
            hits.append(("rx", addr, tx_words[-1]))
        tx_words.clear()
        ADSP.adsp2181_set_irq(cpu, 4, 1)  # SPORT0_TX
        ADSP.adsp2181_set_irq(cpu, 4, 0)
        ADSP.adsp2181_run(cpu, 20000)
        dm[addr] = saved
        if tx_words and tx_words[-1] == (marker & 0xFFFF):
            hits.append(("tx", addr, tx_words[-1]))
    print("[txscan] marker 0x%04x source hits: %s" % (
        marker & 0xFFFF,
        " ".join(f"{irq}:DM{addr:04x}->{value:04x}"
                 for irq, addr, value in hits)
        or "none"))


def force_direct_tikrnl_tx(cpu, samples: int, code: int,
                           source: int = 0x2E52) -> None:
    """Preload the kernel TDM output latch and capture forced SPORT0 TX0."""
    dm = ADSP.adsp2181_dm(cpu)
    tx_words: list[int] = []

    def rx_cb(_cpu, port):
        return 0xFF if port == 0 else 0

    def tx_cb(_cpu, port, value):
        if port == 0:
            tx_words.append(value & 0xFFFF)

    def timer_cb(_cpu, _enabled):
        return None

    callbacks = (RX_CB(rx_cb), TX_CB(tx_cb), TIM_CB(timer_cb))
    ADSP.adsp2181_set_callbacks(cpu, *callbacks)
    for _ in range(samples):
        dm[source & 0x3FFF] = code & 0xFFFF
        ADSP.adsp2181_set_irq(cpu, 3, 1)  # SPORT0_RX runs the TDM TX walk
        ADSP.adsp2181_set_irq(cpu, 3, 0)
        ADSP.adsp2181_run(cpu, 20000)
    if tx_words:
        from collections import Counter
        counts = Counter(tx_words)
        forced = sum(value == (code & 0xFFFF) for value in tx_words)
        print(f"[force-tx] source DM{source & 0x3fff:04x}=0x{code & 0xffff:04x}: "
              f"captured={len(tx_words)} forced={forced} "
              f"top={','.join(f'{value:04x}:{count}' for value, count in counts.most_common(8))} "
              f"first16={' '.join(f'{value:04x}' for value in tx_words[:16])}")
    else:
        print(f"[force-tx] source DM{source & 0x3fff:04x}: captured=0")


def force_modem_dsp_assign(shim: "MipsShim", args) -> None:
    """Force the recovered TIKRNL service assignment during fake call setup."""
    print("[force] staging direct TIKRNL core for modem DSP assignment")
    forced_cpu = stage_direct_tikrnl_core(args)
    old_cpu = shim.cpu
    old_multi = shim.multi_dsp
    try:
        shim.cpu = forced_cpu
        shim.multi_dsp = False
        run_assign(shim, args)
        pump_direct_tikrnl_core(forced_cpu, args.words)
        dm = ADSP.adsp2181_dm(forced_cpu)
        print("[force] after TIKRNL pump: DM3310..3316 "
              + " ".join(f"{dm[a]:04x}" for a in range(0x3310, 0x3317))
              + " DM3327..3336 "
              + " ".join(f"{dm[a]:04x}" for a in range(0x3327, 0x3337)))
        if args.g711_probe_samples:
            probe_direct_tikrnl_g711(forced_cpu, args.g711_probe_samples,
                                     args.g711_probe_code,
                                     args.g711_probe_stimulus,
                                     args.g711_probe_freq,
                                     args.g711_probe_amp,
                                     args.bridge_task_tx,
                                     args.restore_pcm_pointers)
        if args.tx_source_scan:
            scan_direct_tikrnl_tx_source(forced_cpu, args.tx_source_marker,
                                         args.tx_scan_start, args.tx_scan_end)
        if args.force_tx_samples:
            force_direct_tikrnl_tx(forced_cpu, args.force_tx_samples,
                                   args.force_tx_code, args.force_tx_source)
    finally:
        shim.cpu = old_cpu
        shim.multi_dsp = old_multi
    shim.forced_modem_cpu = forced_cpu


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--tikrnl", type=Path, required=True)
    parser.add_argument("--image", type=Path,
                        default=Path("docs/firmware/te_dmlt.pm"))
    parser.add_argument("--mode", type=int, default=0)
    parser.add_argument("--code", type=int, default=66,
                        help="command script code (66 = 0x0258 switch-on)")
    parser.add_argument("--selector", type=int, default=0x0001)
    parser.add_argument("--words", type=int, default=200,
                        help="8 kHz host words to pump after the commit")
    parser.add_argument("--assign", action="store_true",
                        help="run the service assign entry (0x80096980) to "
                             "perform the switch-on database commit before "
                             "the command-script path")
    parser.add_argument("--mainloop", action="store_true",
                        help="drive the MIPS via its native PR_RAM request "
                             "queue (the real host interface), writing a modem "
                             "ASSIGN request and running the main loop")
    parser.add_argument("--channel", type=int, default=1,
                        help="E1 timeslot / channel byte written at req+0x18")
    parser.add_argument("--dsp-combifile", type=Path,
                        default=Path("docs/firmware/dspdload.bin"),
                        help="DSP download combifile staged in card RAM for "
                             "--mainloop (pass an empty value to skip)")
    parser.add_argument("--card-type", type=lambda s: int(s, 0),
                        default=CARDTYPE_DIVASRV_P_30M_PCI,
                        help="CARDTYPE_* number selecting the combifile's "
                             "required download set (23 = PRI 30M PCI)")
    parser.add_argument("--force-law", type=int, choices=(1, 2), default=1,
                        help="native card companding: 1=A-law (default), "
                             "2=mu-law")
    parser.add_argument("--entity", choices=("sig", "nl", "both"),
                        default="both",
                        help="which entities --mainloop assigns: the signalling "
                             "entity (DSIG_ID, carries the CAI), the network "
                             "layer (NL_ID, carries LLI/LLC/DLC), or both in "
                             "the driver's order (default)")
    parser.add_argument("--connect", action="store_true",
                        help="after linked SIG+NL assignment, submit the "
                             "network N_CONNECT that activates the bearer")
    parser.add_argument("--call-direction", choices=("calling", "answering"),
                        default="calling",
                        help="select outgoing V42 or incoming V42_IN bearer "
                             "semantics for the modem NL entity")
    parser.add_argument("--dial-number", default="6001",
                        help="called party number for an outgoing CALL_REQ "
                             "(--call-direction calling)")
    parser.add_argument("--dial-origination", default="",
                        help="calling party number to present on an outgoing "
                             "CALL_REQ; omitted from the message when empty")
    parser.add_argument("--hook-call", metavar="ADDR[,ADDR...]",
                        help="log every entry to these MIPS addresses with "
                             "a0..a3 and the return address. Answers 'is this "
                             "routine reached, and with what' without a full "
                             "call trace")
    parser.add_argument("--watch-mem", metavar="ADDR[:LEN]",
                        help="log every firmware write into this address "
                             "range with the writing PC. Complements "
                             "--scan-ram: the scan says where something ended "
                             "up, this says which code put it there")
    parser.add_argument("--scan-ram", metavar="BYTES",
                        help="after the call steps, scan emulated RAM for this "
                             "literal (e.g. a dialled number) and report every "
                             "address holding it. Q.931 encodes the called "
                             "party number in IA5, so a SETUP the firmware "
                             "built for transmission contains the digits "
                             "verbatim -- which locates the message buffer "
                             "without guessing at internals")
    parser.add_argument("--connect-event", type=lambda t: int(t, 0),
                        default=0x03,
                        help="lower-PRI signalling event delivered after an "
                             "outgoing CALL_REQ to stand in for the network's "
                             "CONNECT (--simulate-outgoing-call)")
    parser.add_argument("--simulate-outgoing-call", action="store_true",
                        help="place an outgoing call through CALL_REQ and "
                             "inject the network CONNECT the emulated span "
                             "cannot send, the mirror of --simulate-b-channel")
    parser.add_argument("--simulate-b-channel", action="store_true",
                        help="simulate an answered incoming call through the "
                             "native SETUP/CALL_IND, CALL_RES, NL activation, "
                             "and TIKRNL service-assignment path")
    parser.add_argument("--force-modem-dsp-assign", action="store_true",
                        help="after N_CONNECT, run the recovered "
                             "SERVICE_ASSIGN path against a directly staged "
                             "TIKRNL core so the modem DSP receives a real "
                             "switch-on database commit")
    parser.add_argument("--g711-probe-samples", type=int, default=0,
                        help="after forced modem DSP assignment, feed this "
                             "many raw G.711 octets into the direct TIKRNL "
                             "core's line words (RX-side probe only)")
    parser.add_argument("--g711-probe-code", type=lambda s: int(s, 0),
                        default=0xff,
                        help="raw G.711 octet used by --g711-probe-samples "
                             "(default 0xff)")
    parser.add_argument("--g711-probe-stimulus",
                        choices=("constant", "tone", "ansam", "silence"),
                        default="constant",
                        help="stimulus used by --g711-probe-samples "
                             "(default constant)")
    parser.add_argument("--g711-probe-freq", type=float, default=2100.0,
                        help="tone/ANSam carrier frequency for "
                             "--g711-probe-stimulus (default 2100)")
    parser.add_argument("--g711-probe-amp", type=int, default=20000,
                        help="linear PCM amplitude before u-law encoding for "
                             "tone/ANSam stimulus (default 20000)")
    parser.add_argument("--bridge-task-tx", action="store_true",
                        help="during --g711-probe-samples, copy the "
                             "pointer-mode task TX buffer to the kernel "
                             "SPORT0 TX latch before each TDM strobe")
    parser.add_argument("--restore-pcm-pointers", action="store_true",
                        help="during --g711-probe-samples, restore the old "
                             "one-line pointer-mode PCM block "
                             "(3F0F->2B00, 3FB4->2B01) after overlays")
    parser.add_argument("--tx-source-scan", action="store_true",
                        help="after forced modem DSP assignment, poke likely "
                             "DM TX buffers with --tx-source-marker and report "
                             "whether SPORT0 TX0 emits the marker")
    parser.add_argument("--tx-source-marker", type=lambda s: int(s, 0),
                        default=0x0055,
                        help="16-bit marker used by --tx-source-scan "
                             "(default 0x0055)")
    parser.add_argument("--tx-scan-start", type=lambda s: int(s, 0),
                        default=None,
                        help="optional inclusive DM start address for a wider "
                             "--tx-source-scan")
    parser.add_argument("--tx-scan-end", type=lambda s: int(s, 0),
                        default=None,
                        help="optional exclusive DM end address for a wider "
                             "--tx-source-scan")
    parser.add_argument("--force-tx-samples", type=int, default=0,
                        help="after forced modem DSP assignment, preload the "
                             "kernel TDM TX source and capture this many "
                             "SPORT0 TX0 words")
    parser.add_argument("--force-tx-code", type=lambda s: int(s, 0),
                        default=0x0055,
                        help="G.711/codeword marker used by --force-tx-samples "
                             "(default 0x0055)")
    parser.add_argument("--force-tx-source", type=lambda s: int(s, 0),
                        default=0x2E52,
                        help="DM source address used by --force-tx-samples "
                             "(default 0x2E52, kernel TDM output latch)")
    parser.add_argument("--fake-call-ingress", action="store_true",
                        help="drive the incoming-call host sequence before "
                             "answering: put the assigned signalling entity "
                             "into LISTEN/INDICATE state, then use the normal "
                             "CALL_RES + N_CONNECT path")
    parser.add_argument("--legacy-sig-req-id", action="store_true",
                        help="use ReqId=1 for simple signalling requests, "
                             "matching the old i4l idi_put_req() helper")
    parser.add_argument("--inject-call-ingress", action="store_true",
                        help="after LISTEN/INDICATE, run the internal "
                             "firmware branch that allocates the incoming "
                             "per-call object before CALL_RES")
    parser.add_argument("--synthesize-call-ingress", action="store_true",
                        help="after LISTEN/INDICATE, fabricate the minimum "
                             "incoming call object expected by CALL_RES")
    parser.add_argument("--ingress-entity-slot", type=int, default=0,
                        help="entity table slot whose listener object receives "
                             "the fake ingress")
    parser.add_argument("--call-steps", type=int, default=64,
                        help="MIPS main-loop iterations to run after N_CONNECT")
    parser.add_argument("--dsp-pump", type=int, default=256,
                        help="MIPS instructions between DSP time slices during "
                             "--mainloop; the DSPs must run in line with the "
                             "MIPS for the boot handshake to complete.  0 holds "
                             "them for the whole run instead.")
    parser.add_argument("--dsp-code-base", type=lambda s: int(s, 0), default=None,
                        help="override DspCodeBaseAddr (default: the protocol "
                             "image's OFFS_PROTOCOL_END_ADDR)")
    parser.add_argument("--log", action="store_true")
    parser.add_argument("--trace-calls", action="store_true",
                        help="record MIPS jal/jalr call targets per harness phase")
    parser.add_argument("--trace-call-limit", type=int, default=24,
                        help="number of hot call targets to print per phase")
    parser.add_argument("--dump-entities", action="store_true",
                        help="dump the firmware entity pointer table after "
                             "incoming-call state transitions")
    parser.add_argument("--dump-entity-limit", type=int, default=16,
                        help="maximum number of entity table slots to dump")
    parser.add_argument("--native-dm-out", type=Path,
                        help="write the naturally assigned modem core's full "
                             "0x4000-word DM image after --mainloop")
    args = parser.parse_args()
    if args.simulate_b_channel:
        args.connect = True
        args.call_direction = "answering"
        args.fake_call_ingress = True
        args.inject_call_ingress = True
    if args.simulate_outgoing_call:
        args.connect = True
        args.call_direction = "calling"
        # No LISTEN: an originating call allocates its own object through
        # CALL_REQ rather than waiting for a network SETUP.
        args.fake_call_ingress = False
        args.inject_call_ingress = True
    if args.dsp_combifile is not None and not str(args.dsp_combifile):
        args.dsp_combifile = None

    cpu = ADSP.adsp2181_create()
    ADSP.adsp2181_reset(cpu)
    if args.mainloop:
        # The firmware downloads the DSP's own kernel over IDMA, so nothing is
        # pre-staged here.  Hold the core in IDMA boot mode until that
        # download writes PM 0; a DSP left running would execute its
        # half-replaced image and corrupt the transfer (the download's own
        # read-back verify catches it).
        ADSP.adsp2181_set_idma_boot_hold(cpu, 1)
        print("[adsp] held in IDMA boot mode; firmware downloads the kernel")
    else:
        load_pm_words(cpu, args.kernel / "pm.bin")
        load_dm_words(cpu, args.kernel / "dm.bin")
        ADSP.adsp2181_run(cpu, 1000)  # boot to IDLE
        # stage TIKRNL and run its initializer, as in eicon_adsp_run
        pm = ADSP.adsp2181_pm(cpu)
        dm = ADSP.adsp2181_dm(cpu)
        for line in (args.tikrnl / "pm.words").read_text().splitlines():
            a, v = line.split()
            pm[int(a, 16)] = int(v, 16)
        for line in (args.tikrnl / "dm.words").read_text().splitlines():
            a, v = line.split()
            dm[int(a, 16)] = int(v, 16)
        ADSP.adsp2181_call(cpu, 0x672, 0x02A8)
        ADSP.adsp2181_run(cpu, 1000000)
        print(f"[adsp] staged: idle={ADSP.adsp2181_idle(cpu)}")

    shim = MipsShim(args.image, cpu, log=args.log)
    shim.trace_calls = args.trace_calls
    if args.watch_mem:
        install_mem_watch(shim, args.watch_mem)
    if args.hook_call:
        install_call_hooks(shim, args.hook_call)

    # The firmware's trace-printf pointer (gp+0x1a7b = 0x800fbe30) is
    # file-backed and points at the real printf (0x80083180), which writes to
    # the hardware trace buffer at 0xa0005d20.  Redirect it to the no-op stub
    # so trace calls return immediately instead of faulting on that buffer.
    shim.write32(0x800fbe30, STUB_VIRT)

    if args.mainloop:
        run_mainloop(shim, args)
        return 0

    if args.assign:
        run_assign(shim, args)

    # Fabricate the request struct (see dsp_assign 0x0258 tail + sender).
    req = RAM_VIRT + 0x1000  # guest-visible pointer
    buf = bytearray(0x60)
    struct.pack_into("<II", buf, 0x00, 0xDEAD0000, 0xDEAD0004)  # host regs
    # symbol 13/14 mailbox addresses, in the form the firmware's own resolver
    # (0x800a6204) produces: 0x4000 selects data memory.
    struct.pack_into("<HH", buf, 0x08, 0x4000 | 0x3310, 0x4000 | 0x3338)
    buf[0x0C] = 1                     # active
    struct.pack_into("<H", buf, 0x12, args.selector)    # command selector
    struct.pack_into("<H", buf, 0x3E, 0x0020)           # control word
    shim.uc.mem_write(RAM_BASE + 0x1000, bytes(buf))  # API uses physical

    # Top-level byte request: [len, ?, form, code, mode] selects a script.
    outer = bytes([4, 0, 0, args.code, args.mode])
    shim.uc.mem_write(RAM_BASE + 0x2000, outer)
    # Context struct: +0x20 = pointer to the byte request.
    ctx = bytearray(0x40)
    struct.pack_into("<I", ctx, 0x20, RAM_VIRT + 0x2000)
    shim.uc.mem_write(RAM_BASE + 0x3000, bytes(ctx))

    # parser(a0=request, a1=context) then sender(a0=request, a1=context)
    v0 = shim.call(REQUEST_PARSER, [req, RAM_VIRT + 0x3000],
                   gp=GP, sp=STACK_TOP)
    if args.log:
        print(f"[mips] parser -> {v0:#x}")
    if v0:
        shim.call(SCRIPT_SENDER, [req, RAM_VIRT + 0x3000],
                  gp=GP, sp=STACK_TOP)

    # pump the 8 kHz host loop so the DSP consumes the command
    for addr in (0x3315, 0x3316, 0x3310, 0x3338, 0x2f28, 0x2e44, 0x2e45):
        ADSP.adsp2181_watch_dm(cpu, addr, 1)
    ADSP.adsp2181_watch_pm(cpu, 0x3327, 1)
    for _ in range(args.words):
        if ADSP.adsp2181_dm(cpu)[0x3315] != ADSP.adsp2181_dm(cpu)[0x3316]:
            ADSP.adsp2181_host_write(cpu, 0x7310, 0x0001)
        ADSP.adsp2181_set_irq(cpu, 3, 1)  # SPORT0_RX
        ADSP.adsp2181_set_irq(cpu, 3, 0)
        # IRQE (irq 6) wakes the kernel foreground from IDLE so it runs
        # the command-queue processor (analysis session 3/4: the host
        # doorbell is IRQE, vector 0x18). Without it TIKRNL's consumer
        # never advances past 0x3327.
        ADSP.adsp2181_set_irq(cpu, 6, 1)  # IRQE doorbell
        ADSP.adsp2181_run(cpu, 5000)
        ADSP.adsp2181_set_irq(cpu, 6, 0)
        ADSP.adsp2181_run(cpu, 5000)
        # Call TIKRNL's frame handler (PM 0x64A) directly so its command
        # consumer (0x1810, reads DM 0x3309 -> command ring) runs and
        # advances the consumer pointer at DM 0x3316. Without this the
        # dispatch loop is circular: TIKRNL only runs when dispatched,
        # but dispatch needs TIKRNL to have registered.
        ADSP.adsp2181_call(cpu, 0x64A, 0x02A8)
        ADSP.adsp2181_run(cpu, 20000)
        # TIKRNL init publishes 0x05B1 as its command service vector at
        # DM 0x3308.  The frame initializer above does not consume the
        # host->task database ring by itself; invoke the published service
        # vector to break the assignment/dispatch bootstrap cycle.
        service_vector = ADSP.adsp2181_dm(cpu)[0x3308]
        ADSP.adsp2181_call(cpu, service_vector, 0x02A8)
        ADSP.adsp2181_run(cpu, 20000)
        ADSP.adsp2181_call(cpu, 0x06BB, 0x02A8)
        ADSP.adsp2181_run(cpu, 20000)
    # Host-port reads need the 0x4000 data-memory select; a bare address
    # selects program memory (see symbol_host_address).
    dm_sel = 0x4000
    print(f"[adsp] selector DM3310={ADSP.adsp2181_host_read(cpu, dm_sel | 0x3310):04x}"
          f" producer DM3315={ADSP.adsp2181_host_read(cpu, dm_sel | 0x3315):04x}"
          f" consumer DM3316={ADSP.adsp2181_host_read(cpu, dm_sel | 0x3316):04x}"
          f" resp DM3338={ADSP.adsp2181_host_read(cpu, dm_sel | 0x3338):04x}")
    # dump the channel table and free-list to see if the descriptor got hooked
    dm = ADSP.adsp2181_dm(cpu)
    print("[adsp] channel table: 2E44=%04x 2E45=%04x  queue 2F08=%04x 2F09=%04x"
          % (dm[0x2E44], dm[0x2E45], dm[0x2F08], dm[0x2F09]))
    print("[adsp] free-list 2F27=%04x 2F28=%04x" % (dm[0x2F27], dm[0x2F28]))
    print("[adsp] DM 2E00-2E10: " + " ".join("%04x" % dm[a] for a in range(0x2E00, 0x2E11)))
    print("[adsp] DM 2F00-2F10: " + " ".join("%04x" % dm[a] for a in range(0x2F00, 0x2F11)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
