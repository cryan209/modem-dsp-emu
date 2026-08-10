#!/usr/bin/env python3
"""Replay a capture through the V.90 data pump and watch its global countdown.

**Use this, not `eicon_info_replay.py`, for anything on overlay `0x026a`.**
The two drive different harnesses over the same firmware:

    eicon_info_replay.py   LiveKernelModem      (dial_kernel_dispatch)
    this tool              create_native_mips_modem()  (eicon_mips_shim)

Live captures come from `eicon_adsp_sip.py --native-mips`, i.e. the second one.
On the INFO page the two agree closely enough to be confused for each other; on
page 14 they do not, and the kernel-dispatch harness parks in TrnProgress
`0x0060` where the native one walks `0x0060 -> 0x0062 -> ...` exactly as the
live card does.  Session 50 lost an afternoon to that.

What it prints: the data pump's two record layers (as decoded by
`tools/v90_dpcm_state_records.py`) on every change, plus each seed and expiry
of the global countdown `DM(0x20e0)` that condition index `0x02` tests.

    PM 0x2c7d   MY0 = PM(0x200c + DM(0x20e3)) -- the symbol-rate scale table
                0x200c..0x2011 = 0.2400 0.2743 0.2800 0.3000 0.3200 0.3429,
                the V.34 baud family over 10000.  Index 4 is 3200 baud, and
                the countdown is measured ticking at 3200 Hz.
    PM 0x2c65   AR = 0x7530 ; scale -> 9600 ticks = 3.000 s
    PM 0x2c6b   AR = 0x4e20 ; scale ; double -> 12800 ticks = 4.000 s
    PM 0x2c78   AR = MR1 + DM(0x3fcb) ; DM(0x20e0) = AR
    PM 0x2cb4   DM(0x3fcb) ~= DM(0x3fc9) * 10/3    -- RTDelay, 2400 Hz -> 8 kHz
    PM 0x2f7d   AY0 = DM(0x20e0) ; AR = AY0 - 1 ; DM(0x20e0) = AR, clamped at 0

The 0x2c65 path falls through PM 0x2c78 into PM 0x2c68 (`AR = AR + AY0`) and
stores again, so it adds `DM(0x3fcb)` *twice*. `DM(0x3fc9)` is not the data
pump's at all: from INFO state 0x0032 to 0x0036, PM 0x3cac..0x3cae increments
it at 2400 Hz while DM(0x1649) bit 0 is set, after PM 0x3cb0..0x3cb3 installs
a compensated negative preload. PM 0x3300..0x3303 divides the result by about
24 to publish the vendor's `RTDelay` at DM(0x3f87), in 10 ms units. Page 14
instead converts the same high-resolution measurement from 2400 Hz to 8 kHz
with fixed-point constant 0xd555 and inherits it in DM(0x3fcb).

Needs the MIPS emulator, so run it under the venv that has `unicorn`, and
build the ADSP core first -- `libadsp2181.dylib` is gitignored and the
top-level makefile does not build it:

    make -C tools/adsp2181emu
    /tmp/eicon-venv/bin/python tools/v90_dpcm_replay.py CAPTURE.rx.ulaw
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eicon_mips_shim import ADSP, create_native_mips_modem

SAMPLE_RATE = 8000
KERNEL = Path('artifacts/eicon-dsp/build-117-926/kernel/'
              '0009-diva-server-pri-30m-kernel')
TIKRNL = Path('artifacts/eicon-dsp/build-117-926/tikrnl/'
              '0258-tikrnl81.f34-task')

# The data pump's working set.  Layers and offsets from
# tools/v90_dpcm_state_records.py; DM(0x20e0..0x20e3) from the seeders above.
WORDS = {
    'trn': 0x3FC2,      # published TrnProgress = outer state AND 0x00ff
    'count': 0x20E0,    # global countdown, condition index 0x02
    'rate': 0x20E3,     # index into the PM 0x200c symbol-rate scale table
    'addend': 0x3FCB,   # high-resolution RTDelay in 8 kHz units
    'exit': 0x20B8,     # data-state record condition input
    'recover': 0x2113,  # inner state 0x006a recovery gate
    'optr': 0x120F, 'ostate': 0x1FF7, 'odwell': 0x1FF6,
    'iptr': 0x204A, 'istate': 0x2008, 'idwell': 0x2007,
}
# The countdown moves every tick, so key on the record pointers and states
# only -- otherwise this prints one line per sample for six seconds.
KEY = ('trn', 'optr', 'ostate', 'iptr', 'istate', 'exit', 'recover')
DM_COUNT = 0x20E0
DM_ELAPSED = 0x3FC9
DM_ADDEND = 0x3FCB


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('capture', type=Path, help='a .rx.ulaw capture (PCMU)')
    ap.add_argument('--from', dest='start', type=float, default=0.0)
    ap.add_argument('--to', dest='end', type=float, default=25.0,
                    help='stop here; the replay runs far slower than real time')
    ap.add_argument('--tx-prbs', action='store_true',
                    help='answer V90D TX requests with deterministic PRBS data')
    ap.add_argument('--native-bearer-activation', action='store_true',
                    help='use lower-PRI event 03 task attachment before ADDSP answer setup')
    ap.add_argument('--seed-v90-speed', action='store_true',
                    help='seed observed CX V.90 TX=32/RX=3 speed words')
    ap.add_argument('--foreground', action='store_true',
                    help='report consecutive frames that exhaust their run '
                         'without returning the ADSP foreground to IDLE')
    ap.add_argument('--rx-path', action='store_true',
                    help='audit SPORT publication and V90D internal receive-ring '
                         'progression over the replay')
    ap.add_argument('--patch-pm', action='append', default=[], metavar='ADDR=WORD',
                    help='replay-only PM A/B, applied after overlay loads; repeatable')
    ap.add_argument('--pin-dm', action='append', default=[], metavar='ADDR=VALUE',
                    help='hold a DM word against firmware stores during a timed '
                         'replay window; repeatable')
    ap.add_argument('--pin-from', type=float, default=0.0, metavar='SECONDS')
    ap.add_argument('--pin-to', type=float, default=float('inf'), metavar='SECONDS')
    ap.add_argument('--poke-dm', action='append', default=[], metavar='ADDR=VALUE',
                    help='write a DM word once at --poke-at; repeatable')
    ap.add_argument('--poke-at', type=float, default=float('inf'), metavar='SECONDS')
    ap.add_argument('--watch-dm-write', action='append', default=[],
                    metavar='ADDR[:LIMIT]',
                    help='log page-14 firmware stores and their PCs; repeatable')
    ap.add_argument('--watch-exec', action='append', default=[], metavar='ADDR',
                    help='log execution of a selected-overlay PM address; repeatable')
    ap.add_argument('--watch-overlay', type=lambda value: int(value, 0),
                    default=0x026A, metavar='OVERLAY',
                    help='resident overlay that gates watches (default: 0x026a)')
    ap.add_argument('--watch-from', type=float, default=0.0, metavar='SECONDS',
                    help='arm --watch-dm-write/--watch-exec at this replay time')
    args = ap.parse_args()

    patches = []
    for field in args.patch_pm:
        address, value = field.split('=', 1)
        patches.append((int(address, 0) & 0x3FFF, int(value, 0) & 0xFFFFFF))
    pins = []
    for field in args.pin_dm:
        address, value = field.split('=', 1)
        pins.append((int(address, 0) & 0x3FFF, int(value, 0) & 0xFFFF))
    pokes = []
    for field in args.poke_dm:
        address, value = field.split('=', 1)
        pokes.append((int(address, 0) & 0x3FFF, int(value, 0) & 0xFFFF))
    watches = []
    for field in args.watch_dm_write:
        address, separator, limit = field.partition(':')
        watches.append((int(address, 0) & 0x3FFF,
                        int(limit, 0) if separator else 32))
    exec_watches = [int(field, 0) & 0x3FFF for field in args.watch_exec]

    data = args.capture.read_bytes()
    card = create_native_mips_modem(KERNEL, TIKRNL, 'pcmu',
                                    force_info_after_v8=True,
                                    tx_prbs=args.tx_prbs,
                                    native_bearer_activation=args.native_bearer_activation)
    dm = card.dm
    print('[replay] native-MIPS harness ready', flush=True)
    watches_armed = False

    previous = None
    seeded = None
    live = total = page14_live = page14_total = 0
    first_page14_tx = None
    nonidle_start = None
    nonidle_state = None
    nonidle_pcs: Counter[int] = Counter()
    nonidle_max_cycles = 0
    patch_reported = False
    pins_armed = False
    pokes_applied = False
    rx_page_samples = 0
    rx_publish_mismatches = 0
    rx_pointers = {
        'filter-read DM25b9': [],
        'filter-write DM25ba': [],
        'alignment DM2062': [],
    }
    rx_coverage_addresses = {
        'selected continuation PM0703': 0x0703,
        'Core8k wrapper PM19e1': 0x19E1,
        'filter-ring store PM3d22': 0x3D22,
        'filter-ring drain PM2b4d': 0x2B4D,
        'alignment-ring store PM3141': 0x3141,
        'downstream mapping generator PM2a52': 0x2A52,
        'data-exit predicate PM30b4': 0x30B4,
        'data-exit counter increment PM23d7': 0x23D7,
    }
    rx_coverage_start = None

    def report_nonidle(end_index: int) -> None:
        nonlocal nonidle_start, nonidle_state, nonidle_max_cycles
        if nonidle_start is None:
            return
        frames = end_index - nonidle_start
        top = ' '.join(f'{pc:04x}:{count}'
                       for pc, count in nonidle_pcs.most_common(5))
        shim_sample = card._media_samples - frames
        print(f'{nonidle_start / SAMPLE_RATE:8.4f}  foreground non-IDLE for '
              f'{frames} frame(s), shim samples {shim_sample}..'
              f'{card._media_samples - 1}, TrnProgress=0x{nonidle_state:04x}, '
              f'max cycles/frame={nonidle_max_cycles}, ending PCs {top}',
              flush=True)
        nonidle_start = None
        nonidle_state = None
        nonidle_pcs.clear()
        nonidle_max_cycles = 0

    for index, code in enumerate(data):
        seconds = index / SAMPLE_RATE
        if seconds > args.end:
            break
        if patches and card.resident == 0x026A:
            for address, value in patches:
                card.pm[address] = value
            if not patch_reported:
                print('[replay] PATCHED page-14 PM: ' + ' '.join(
                    f'0x{address:04x}=0x{value:06x}'
                    for address, value in patches), flush=True)
                patch_reported = True
        if args.seed_v90_speed and card.resident == 0x026A:
            # CX handoff observed live: TX=32 (V90 index 11), RX=3
            # (7200/2400, index 7).
            card.dm[0x3F61] = 0x202B
            card.dm[0x3F62] = 0x2007
        if (pokes and not pokes_applied and seconds >= args.poke_at
                and card.resident == 0x026A):
            for address, value in pokes:
                dm[address] = value
            print(f'[replay] applied timed DM poke at {seconds:.4f}s: ' +
                  ' '.join(f'0x{address:04x}=0x{value:04x}'
                           for address, value in pokes), flush=True)
            pokes_applied = True
        pin_window = (pins and args.pin_from <= seconds < args.pin_to
                      and card.resident == 0x026A)
        if pin_window and not pins_armed:
            for address, value in pins:
                dm[address] = value
                ADSP.adsp2181_pin_dm(card.cpu, address, value, 1)
            print(f'[replay] armed timed DM pins at {seconds:.4f}s: ' +
                  ' '.join(f'0x{address:04x}=0x{value:04x}'
                           for address, value in pins), flush=True)
            pins_armed = True
        elif pins_armed and not pin_window:
            for address, _ in pins:
                ADSP.adsp2181_pin_dm(card.cpu, address, 0, 0)
            print(f'[replay] released timed DM pins at {seconds:.4f}s', flush=True)
            pins_armed = False
        if (watches or exec_watches) and not watches_armed and seconds >= args.watch_from:
            for address, limit in watches:
                ADSP.adsp2181_watch_dm_writes(card.cpu, address, limit)
                print(f'[replay] watching overlay 0x{args.watch_overlay:04x} '
                      f'DM(0x{address:04x}) stores, limit {limit}', flush=True)
            for address in exec_watches:
                ADSP.adsp2181_watch_exec(card.cpu, address, 1)
                print(f'[replay] watching overlay 0x{args.watch_overlay:04x} '
                      f'PM(0x{address:04x}) execution', flush=True)
            watches_armed = True
        if watches_armed:
            ADSP.adsp2181_watch_gate(
                card.cpu, card.resident == (args.watch_overlay & 0xFFFF))
        before_cycles = ADSP.adsp2181_cycles(card.cpu)
        sample = card.frame_fast(code, index)
        frame_cycles = ADSP.adsp2181_cycles(card.cpu) - before_cycles
        if args.rx_path and card.resident == 0x026A:
            if rx_coverage_start is None:
                rx_coverage_start = {
                    name: ADSP.adsp2181_coverage_count(card.cpu, address)
                    for name, address in rx_coverage_addresses.items()
                }
            rx_page_samples += 1
            expected = card._sport_rx_word(code)
            if int(dm[0x3763]) != expected:
                rx_publish_mismatches += 1
            for name, address in (('filter-read DM25b9', 0x25B9),
                                  ('filter-write DM25ba', 0x25BA),
                                  ('alignment DM2062', 0x2062)):
                rx_pointers[name].append(int(dm[address]))
        if args.foreground:
            idle = bool(ADSP.adsp2181_idle(card.cpu))
            state = int(dm[WORDS['trn']])
            if not idle:
                if nonidle_start is None:
                    nonidle_start = index
                    nonidle_state = state
                elif state != nonidle_state:
                    report_nonidle(index)
                    nonidle_start = index
                    nonidle_state = state
                nonidle_pcs[int(ADSP.adsp2181_pc(card.cpu))] += 1
                nonidle_max_cycles = max(nonidle_max_cycles, frame_cycles)
            else:
                report_nonidle(index)
        if seconds < args.start:
            continue

        count = dm[DM_COUNT]
        if seeded is None or count > seeded[1]:
            # The only way the countdown rises is a seed: PM 0x2f7d only ever
            # decrements it, and clamps at zero.
            seeded = (seconds, count)
            if count:
                print(f'{seconds:8.4f}  countdown seeded {count:#06x} = {count}'
                      f' ticks, addend DM(0x3fcb)={dm[DM_ADDEND]} from'
                      f' DM(0x3fc9)={dm[DM_ELAPSED]}', flush=True)
        elif count == 0 and seeded[1]:
            held = seconds - seeded[0]
            print(f'{seconds:8.4f}  countdown expired after {held:.4f} s '
                  f'({seeded[1] / held:.0f} ticks/s)', flush=True)
            seeded = (seconds, 0)

        key = tuple(dm[WORDS[name]] for name in KEY)
        if key != previous:
            fields = ' '.join(f'{n}={dm[a]:04x}' for n, a in WORDS.items())
            print(f'{seconds:8.4f}  shim={card._media_samples:06d} {fields}',
                  flush=True)
            previous = key

        total += 1
        live += 1 if sample else 0
        if card.resident == 0x026A:
            page14_total += 1
            page14_live += 1 if sample else 0
            if sample and first_page14_tx is None:
                first_page14_tx = (seconds, sample, dm[WORDS['ostate']])
                print(f'{seconds:8.4f}  first V90D TX sample={sample} '
                      f'outer_state={first_page14_tx[2]:04x}', flush=True)

    if args.foreground:
        report_nonidle(min(len(data), int(args.end * SAMPLE_RATE) + 1))
    print(f'TX over the replayed window: {100.0 * live / max(1, total):.1f}% '
          f'non-zero of {total} samples; page 14: '
          f'{100.0 * page14_live / max(1, page14_total):.1f}% non-zero of '
          f'{page14_total}; TX datagrams '
          f'{card.tx_accepted}/{card.tx_requests} accepted/requested')
    # The transmit-companding census, which a replay can qualify without a
    # call: the words the page publishes are the same words either way, and an
    # archived capture says whether the right-justified reading holds on more
    # than the two calls it was derived from.
    census = getattr(card, 'tx_scale_census', None)
    if census is not None:
        line = census()
        if line:
            print(line)
    if args.rx_path:
        print(f'RX path over {rx_page_samples} V90D samples: DM3763 mismatches '
              f'{rx_publish_mismatches}')
        for name, values in rx_pointers.items():
            transitions = sum(left != right
                              for left, right in zip(values, values[1:]))
            longest = run = 0
            previous_value = None
            for value in values:
                if value == previous_value:
                    run += 1
                else:
                    run = 1
                    previous_value = value
                longest = max(longest, run)
            common = ','.join(f'{value:04x}:{count}' for value, count in
                              Counter(values).most_common(4))
            print(f'  {name}: {transitions} transitions, '
                  f'{len(set(values))} values, longest hold {longest} samples, '
                  f'top {common or "n/a"}')
        if rx_coverage_start is not None:
            for name, address in rx_coverage_addresses.items():
                count = (ADSP.adsp2181_coverage_count(card.cpu, address)
                         - rx_coverage_start[name])
                print(f'  {name}: {count} executions '
                      f'({count / max(1, rx_page_samples):.6f}/sample)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
