#!/usr/bin/env python3
"""Locate and audit the producers of the V90D receive observation.

Companion to `tools/v90_dpcm_vector_trace.py` -- same harness
(`create_native_mips_modem()` over a recorded `.rx.ulaw`) -- but pointed at the
*receive* chain instead of the transmit vector.  Session 243 proved the residual
`DM(0x0efb/0x0efc)`, its `(|I|+|Q|)/2` average `DM(0x0fce/0x0fcf)` and the final
complex rotation at PM `0x0d5e` are all bit-exact, so the 14,400 upstream
ceiling has to be built earlier: in whatever writes `DM(0x0ef9/0x0efa)`.

What it found, working outwards from that pair:

    PM 0x0b4e..0x0b7e   the receive equalizer.  54-tap complex FIR:
                        DM(0x0ef9) = sum(x_r*h_r - x_i*h_i)
                        DM(0x0efa) = sum(x_r*h_i + x_i*h_r)
                        L0/L1 = 0x90 data lines based at DM(0x201f) (real) and
                        DM(0x2020) (imag, always +0x100), tap stride M3 = 2;
                        L5/L6 = 0x36 coefficient rings based at DM(0x2023) ->
                        PM 0x1f80 (real) and DM(0x2024) -> PM 0x1fc0 (imag).
                        Both accumulators are realigned by 2 bits and rounded
                        (PM 0x0b67..0x0b6b) before the store.
    PM 0x0bab..0x0bbd   the LMS tap update, driven by exactly the residual pair
                        DM(0x0efb/0x0efc).  Taps are 32-bit: high words in the
                        PM 0x1f80/0x1fc0 rings, low words in the rings based at
                        DM(0x2025) -> PM 0x25c0 and DM(0x2026) -> PM 0x2600.
                        DM(0x2074) iterations (0x12) update 19 complex taps per
                        symbol, from a base that walks with the data pointer.
    PM 0x0bbe..0x0bcf   a leak/decay applied every DM(0x0e04) symbols.
    PM 0x0fc1..0x0fcb   pushes one complex sample into the data lines: MX0 to
                        the real line, SR1 to the imaginary one, DM(0x201f) and
                        DM(0x2020) decrementing by one each call.
    PM 0x0f93..0x0fa3   the gain/interpolation stage that runs immediately
                        before each push, over the 0x20-word ring at DM(0x2130)
                        with DM(0x0a29) as the multiplier.
    PM 0x2a93..0x2a97   the per-sample dispatch: `I4 = DM(0x201b)`, fetch the
                        next handler address, `CALL (I4)`.  DM(0x201b) walks the
                        handler table at DM(0x0008..0x000b) =
                        {0x2ac7, 0x2ad2, 0x2ae0, 0x2b1b}; PM 0x2ada rewinds it
                        by three, which is what makes the equalizer run at three
                        samples per 3,200-baud symbol (9,600 Hz).

The audit conclusion is a negative for the equalizer: it adapts every symbol,
and its taps converge to a sane profile (a single main lobe with the period-3
side structure a T/3-spaced filter should have).  The receive error floor is
therefore in the samples arriving at DM(0x201f/0x2020), not in the filter or
its adaptation.

Runs against the emulator, so use the venv with `unicorn` and build the core
first (`libadsp2181.dylib` is gitignored):

    make -C tools/adsp2181emu
    /tmp/eicon-venv/bin/python tools/v90_rx_equalizer_probe.py \\
        artifacts/eicon-native-tower/run76.rx.ulaw --to 14.2 --taps

Windows must be short.  `--watch`/`--watch-rw`/`--watch-pm`/`--watch-exec` all
write to stderr from inside the core's hot path, and a window wider than a few
tens of milliseconds of audio buries the answer.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import eicon_mips_shim as SHIM
from eicon_mips_shim import ADSP, create_native_mips_modem

SAMPLE_RATE = 8000
KERNEL = Path('artifacts/eicon-dsp/build-117-926/kernel/'
              '0009-diva-server-pri-30m-kernel')
TIKRNL = Path('artifacts/eicon-dsp/build-117-926/tikrnl/'
              '0258-tikrnl81.f34-task')

DM_OSTATE = 0x1FF7
DM_ISTATE = 0x2008
# Observation, residual, smoothed residual, published rate class.
TRACED = (0x0EF9, 0x0EFA, 0x0EFB, 0x0EFC, 0x0FCE, 0x0FCF, 0x20BA)
# Equalizer geometry, as PM 0x0b4e..0x0b7e and PM 0x0b90..0x0bbd read it.
TAPS = 0x36
COEFF_HI = (0x1F80, 0x1FC0)   # real, imaginary; the words the FIR multiplies
COEFF_LO = (0x25C0, 0x2600)   # their low halves, carried by the LMS update


def signed(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def pm_hi(cpu, addr: int) -> int:
    """A 16-bit coefficient as PM data access sees it: the top of the word."""
    return signed((ADSP.adsp2181_read_pm(cpu, addr) >> 8) & 0xFFFF)


def report_taps(cpu) -> None:
    print(f'{"tap":>4} {"re":>7} {"im":>7} {"re_lo":>7} {"im_lo":>7} '
          f'{"|h|":>9}')
    energy = 0.0
    for n in range(TAPS):
        re, im = pm_hi(cpu, COEFF_HI[0] + n), pm_hi(cpu, COEFF_HI[1] + n)
        rl, il = pm_hi(cpu, COEFF_LO[0] + n), pm_hi(cpu, COEFF_LO[1] + n)
        mag = (re * re + im * im) ** 0.5
        energy += re * re + im * im
        print(f'{n:4d} {re:7d} {im:7d} {rl:7d} {il:7d} {mag:9.1f}  '
              + '#' * min(60, int(mag / 200)))
    print(f'tap energy {energy:.4e}, rms {(energy / TAPS) ** 0.5:.1f}')


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('capture', type=Path, help='a .rx.ulaw capture (PCMU)')
    ap.add_argument('--to', dest='end', type=float, default=25.0)
    ap.add_argument('--progress', type=float, default=2.0,
                    help='seconds between state lines; 0 disables')
    ap.add_argument('--taps', action='store_true',
                    help='print the equalizer tap profile at --to')
    ap.add_argument('--dump-pm', type=Path, default=None,
                    help='dump the 24-bit PM image at --to')
    ap.add_argument('--show', type=lambda t: int(t, 0), action='append',
                    default=[], help='DM words to print at --to')
    ap.add_argument('--show-range', default=None,
                    help='LO:HI DM range to print at --to')
    ap.add_argument('--track', type=lambda t: int(t, 0), action='append',
                    default=[], help='DM words to print every frame in-window')
    ap.add_argument('--watch', type=lambda t: int(t, 0), action='append',
                    default=[], help='DM words whose writers to log')
    ap.add_argument('--watch-rw', type=lambda t: int(t, 0), action='append',
                    default=[], help='DM words whose reads and writes to log')
    ap.add_argument('--watch-pm', type=lambda t: int(t, 0), action='append',
                    default=[], help='PM words whose writers to log')
    ap.add_argument('--watch-exec', type=lambda t: int(t, 0), action='append',
                    default=[], help='PM addresses whose execution to log')
    ap.add_argument('--watch-limit', type=int, default=8,
                    help='per-address event budget; 0 = unlimited')
    ap.add_argument('--watch-from', type=float, default=None)
    ap.add_argument('--watch-to', type=float, default=None)
    args = ap.parse_args()

    data = args.capture.read_bytes()
    card = create_native_mips_modem(KERNEL, TIKRNL, 'pcmu',
                                    force_info_after_v8=True, tx_prbs=True)
    dm = card.dm
    print(f'[probe] harness ready; bulk adapter '
          f'{"disabled" if SHIM.V90D_BULK_ADAPTER_DISABLED else "LIVE"}',
          flush=True)

    watched = list(args.watch) + list(args.watch_rw)
    first_nonzero: dict[int, float] = {}
    first_ostate: dict[int, float] = {}
    armed = False
    next_progress = args.progress

    for index, code in enumerate(data):
        seconds = index / SAMPLE_RATE
        if seconds > args.end:
            break

        if (watched or args.watch_pm or args.watch_exec) and not armed \
                and args.watch_from is not None \
                and seconds >= args.watch_from:
            for addr in args.watch:
                ADSP.adsp2181_watch_dm_writes(card.cpu, addr, args.watch_limit)
            for addr in args.watch_rw:
                ADSP.adsp2181_watch_dm_limited(card.cpu, addr,
                                               args.watch_limit)
            for addr in args.watch_pm:
                ADSP.adsp2181_watch_pm(card.cpu, addr, 1)
            for addr in args.watch_exec:
                ADSP.adsp2181_watch_exec_limited(card.cpu, addr,
                                                 args.watch_limit or 4)
            print(f'{seconds:8.4f}  watch armed: '
                  f'dm={[f"{a:#06x}" for a in watched]} '
                  f'pm={[f"{a:#06x}" for a in args.watch_pm]} '
                  f'exec={[f"{a:#06x}" for a in args.watch_exec]}', flush=True)
            armed = True
        if armed and args.watch_to is not None and seconds > args.watch_to:
            for addr in watched:
                ADSP.adsp2181_watch_dm(card.cpu, addr, 0)
            for addr in args.watch_pm:
                ADSP.adsp2181_watch_pm(card.cpu, addr, 0)
            for addr in args.watch_exec:
                ADSP.adsp2181_watch_exec(card.cpu, addr, 0)
            print(f'{seconds:8.4f}  watch disarmed', flush=True)
            armed = False
            args.watch_from = None

        card.frame_fast(code, index)
        if card.resident != 0x026A:
            continue

        ostate = dm[DM_OSTATE]
        if ostate not in first_ostate:
            first_ostate[ostate] = seconds
            print(f'{seconds:8.4f}  ostate {ostate:04x}', flush=True)
        for addr in TRACED:
            if dm[addr] and addr not in first_nonzero:
                first_nonzero[addr] = seconds
                print(f'{seconds:8.4f}  DM({addr:04x}) first nonzero = '
                      f'{dm[addr]:04x} ({signed(dm[addr])})', flush=True)
        if args.track and armed:
            print(f'{seconds:8.4f}  '
                  + ' '.join(f'{a:04x}={dm[a]:04x}' for a in args.track),
                  flush=True)
        if args.progress and seconds >= next_progress:
            next_progress += args.progress
            print(f'{seconds:8.4f}  ostate={ostate:04x} '
                  f'istate={dm[DM_ISTATE]:04x} '
                  + ' '.join(f'{a:04x}={dm[a]:04x}' for a in TRACED),
                  flush=True)

    if args.dump_pm is not None:
        words = bytearray()
        for addr in range(0x4000):
            word = ADSP.adsp2181_read_pm(card.cpu, addr) & 0xFFFFFF
            words += bytes((word & 0xFF, (word >> 8) & 0xFF, word >> 16))
        args.dump_pm.write_bytes(bytes(words))
        print(f'wrote {args.dump_pm} '
              f'(pmovlay={ADSP.adsp2181_pmovlay(card.cpu)})', flush=True)
    for addr in args.show:
        print(f'DM({addr:04x}) = {dm[addr]:04x} ({signed(dm[addr])})')
    if args.show_range:
        lo, hi = (int(t, 0) for t in args.show_range.split(':'))
        for base in range(lo, hi, 8):
            row = ' '.join(f'{dm[a]:04x}' for a in range(base,
                                                         min(base + 8, hi)))
            print(f'DM {base:04x}: {row}')
    if args.taps:
        report_taps(card.cpu)

    print('--- first nonzero ---')
    for addr in TRACED:
        when = first_nonzero.get(addr)
        print(f'  DM({addr:04x}) {"never" if when is None else f"{when:.4f}"}')
    print('--- outer states ---')
    for state, when in sorted(first_ostate.items(), key=lambda kv: kv[1]):
        print(f'  {state:04x} at {when:.4f}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
