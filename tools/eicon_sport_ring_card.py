#!/usr/bin/env python3
"""Run the continuous SPORT1 media ring on a stopped Diva 4BRI-8 PCI v2.

Standard library only: this runs on the card host. There is no MIPS stub in
the media path. The host halts the stock MIPS, keeps the IPACs and their
clocks, selects the existing CDA DD-to-DU loop, then drives the DSP's IDMA
port directly through PCI BAR3 -- loading the program, draining the receive
ring and filling the transmit ring for the whole run.

    sudo python3 eicon_sport_ring_card.py --pci 0000:02:02.0 --adapter 5 \
        --serial 12196 --seconds 60 ring.json --out ring.card.json

Restricted to spare adapter 5 / SN 12196, like every stock-interface takeover.
The caller must StopCard, ResetCard and reload stock firmware in a trap.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import mmap
import math
import os
import re
import sys
import time

RISC, ISAC_DSP_RESET, IPACX_CONFIG, IRQ_TEST = 0x0200, 0x0000, 0x0300, 0x0400
VENDOR, DEVICE = 0x1133, 0xE013
COLD, WARM = 1, 2
PORTS = [0x1000*n + off for n in (1, 2, 3, 4) for off in (0x800, 0x810)]
DM_SELECT = 0x4000


def validate_service(ring, seconds, period, margin):
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('seconds must be finite and positive')
    if not math.isfinite(period) or period < 0:
        raise ValueError('period must be finite and nonnegative')
    if not period*9000 < margin < ring['ring']:
        raise ValueError('margin must exceed one poll period of samples '
                         'and stay inside the ring')


def classify_result(result):
    """Flow continuity and sample integrity are separate requirements."""
    flow = (result.get('overruns') == 0 and result.get('underruns') == 0
            and result.get('loop_delay') is not None
            and result.get('verified_samples', 0) > 0)
    anomaly_free = result.get('anomalies') == 0
    return dict(flow_continuous=bool(flow), anomaly_free=anomaly_free,
                clean=bool(flow and anomaly_free))


def unexpected_zero_indices(entries, counter_modulus, pattern_modulus, delay=1):
    """Decode a non-overflowed cyclic-ramp zero log; omit startup samples.

    A genuine ramp zero occurs every pattern_modulus samples. That interval
    must be shorter than the counter modulus to observe counter wraps.
    """
    absolute = 0
    unexpected = []
    for raw in entries:
        absolute += (raw-absolute) % counter_modulus
        if absolute >= 2*pattern_modulus and absolute % pattern_modulus != delay:
            unexpected.append(absolute)
    return unexpected


def map_bar(pci: str, index: int) -> mmap.mmap:
    path = f'/sys/bus/pci/devices/{pci}/resource{index}'
    fd = os.open(path, os.O_RDWR | getattr(os, 'O_SYNC', 0))
    try:
        return mmap.mmap(fd, os.path.getsize(path), mmap.MAP_SHARED,
                         mmap.PROT_READ | mmap.PROT_WRITE)
    finally:
        os.close(fd)


def check_adapter(pci: str, adapter: int, serial: int) -> None:
    """The same identity gate as eicon_dsp_bench_card.py: vendor, serial, devfn."""
    device = f'/sys/bus/pci/devices/{pci}'
    if int(open(f'{device}/vendor').read(), 16) != VENDOR or \
            int(open(f'{device}/device').read(), 16) != DEVICE:
        raise SystemExit(f'{pci} is not a 1133:e013 4BRI v2')
    info = open(f'/proc/net/eicon/adapter{adapter}/info').read()
    if not re.search(rf'^Serial\s*:\s*{serial}\s*$', info, re.M):
        raise SystemExit(f'adapter {adapter} is not serial {serial}')
    devfn = int(pci.split('.')[0].split(':')[-1], 16) << 3 | int(pci.split('.')[1])
    funcs = re.findall(r'^PCI-func\s*:\s*(\d+)', info, re.M)
    if not funcs or int(funcs[0]) != devfn:
        raise SystemExit(f'adapter {adapter} is PCI-func {funcs}, not {pci} (devfn {devfn})')


class Idma:
    """One DSP's IDMA port, mapped straight into this process."""

    def __init__(self, ctl: mmap.mmap, port: int):
        self.data = ctypes.c_uint16.from_buffer(ctl, port)
        self.addr = ctypes.c_uint16.from_buffer(ctl, port+8)

    def echo(self) -> bool:
        got = []
        for probe in (0x5A5A, 0xA5A5):
            self.addr.value = DM_SELECT
            self.data.value = probe
            self.addr.value = DM_SELECT
            got.append(self.data.value)
        return got == [0x5A5A, 0xA5A5]

    def read_dm(self, addr: int) -> int:
        self.addr.value = DM_SELECT | addr
        return self.data.value

    def write_dm(self, addr: int, value: int) -> None:
        self.addr.value = DM_SELECT | addr
        self.data.value = value & 0xFFFF

    def read_block(self, addr: int, count: int) -> list[int]:
        self.addr.value = DM_SELECT | addr
        data = self.data
        return [data.value for _ in range(count)]

    def write_block(self, addr: int, words) -> None:
        self.addr.value = DM_SELECT | addr
        data = self.data
        for word in words:
            data.value = word & 0xFFFF

    def boot(self, writes) -> None:
        """DM presets then PM words; writing PM 0 last starts execution."""
        for addr, value, is_pm in writes:
            self.addr.value = addr if is_pm else DM_SELECT | addr
            if is_pm:
                self.data.value = (value >> 8) & 0xFFFF
                self.data.value = value & 0xFF
            else:
                self.data.value = value & 0xFFFF


def takeover(ctl: mmap.mmap, rx_constant=None) -> dict:
    """Halt the stock MIPS, keep the IPACs, select the CDA DD-to-DU loop."""
    risc = ctypes.c_uint32.from_buffer(ctl, RISC)
    if risc.value & (COLD | WARM) != COLD | WARM:
        raise SystemExit(f'takeover requires the stock MIPS running (RISC=0x{risc.value:x})')
    reset = ctypes.c_uint32.from_buffer(ctl, ISAC_DSP_RESET)
    if reset.value & 0x1F != 0x1F:
        raise SystemExit('stock interfaces are not all released from reset')
    stock = {'risc': risc.value, 'reset': reset.value,
             'ipacx_config': ctypes.c_uint32.from_buffer(ctl, IPACX_CONFIG).value}
    risc.value = 0
    ctypes.c_uint32.from_buffer(ctl, IRQ_TEST).value = 0
    time.sleep(0.01)
    # Stop the stock DSP transmitters before enabling any CDA output; leave
    # every IPAC released so its clock setup survives. Same register writes
    # as eicon_dsp_bench_card.py's 'loop' fixture.
    reset.value = 0x1E
    stock['ipacs'] = []
    for n in range(1, 5):
        def byte(r):
            return ctypes.c_uint8.from_buffer(ctl, n*0x1000+4*r)
        before = {f'{r:02x}': byte(r).value for r in range(0x40, 0x58)}
        if before['57'] & 1:
            raise SystemExit('IOM_CR.CI_CS must be zero for TR_CR access')
        byte(0x50).value = before['50'] & ~0x60    # TR_CR: RX B1/B2 off
        byte(0x4E).value = 0
        byte(0x4F).value = 0
        for r, value in ((0x44, 0), (0x45, 1), (0x46, 0x80), (0x47, 0x81)):
            byte(r).value = value
        if rx_constant is None:
            byte(0x4F).value = 0x1E                # sample DD, output DU
        else:
            # Drive DU only; DD remains disabled, avoiding DSP TX contention.
            byte(0x42).value = rx_constant >> 8
            byte(0x43).value = rx_constant & 0xFF
            byte(0x4F).value = 6                   # static DU, no sampling
        stock['rx_constant'] = rx_constant
        stock['ipacs'].append({'base': f'bf80{n}000', 'registers': before})
    return stock


def service(idma: Idma, ring: dict, seconds: float, period: float,
            margin: int, verbose: bool, warmup: int = 0,
            cyclic_tx: bool = False, rx_constant=None) -> dict:
    validate_service(ring, seconds, period, margin)
    if ring.get('tx_autobuffer') and not cyclic_tx:
        raise ValueError('TX autobuffer diagnostic requires cyclic_tx')
    if rx_constant is not None and not 0 <= rx_constant <= 0xFFFF:
        raise ValueError('rx_constant must be an unsigned 16-bit word')
    size = ring['ring']
    modulus = ring['counter_modulus']
    rx_cell, tx_cell = ring['rx_count'], ring['tx_count']
    rx_base, tx_base = ring['rx_ring'], ring['tx_ring']
    pattern_modulus = size if cyclic_tx else 0x10000

    def extend(previous: int, raw: int) -> int:
        """Lift a 14-bit DSP counter back onto the absolute sample index."""
        return previous + (raw - previous) % modulus

    # The receive ring starts zeroed and the loop carries nothing until the
    # first transmitted words come back, so the opening samples are not data.
    # Locking the delay on them would fix it at a startup artefact and make
    # every later sample a mismatch.
    warmup = warmup or 2*size
    rx_abs = tx_abs = 0
    rx_drained = 0
    locked_at = None
    tx_filled = size            # the preset ring already holds words 0..size-1
    overruns = underruns = polls = 0
    delay = None
    mismatches = []
    anomalies = zero_words = other_words = 0
    reread_correct = reread_still_wrong = 0
    last_anomaly = None
    gaps = []
    occupancy_peak = 0
    alive_first = idma.read_dm(ring['alive'])
    started = time.perf_counter()
    deadline = started+seconds
    nxt = started+period
    while True:
        now = time.perf_counter()
        if now >= deadline:
            break
        if now < nxt:
            time.sleep(min(nxt-now, 0.002))
            continue
        nxt += period
        polls += 1
        rx_abs = extend(rx_abs, idma.read_dm(rx_cell))
        tx_abs = extend(tx_abs, idma.read_dm(tx_cell))

        pending = rx_abs-rx_drained
        occupancy_peak = max(occupancy_peak, pending)
        if pending > size:
            overruns += 1
            rx_drained = rx_abs-size        # keep only what the ring still holds
            pending = size
        while pending:
            offset = rx_drained % size
            run = min(pending, size-offset)
            words = idma.read_block(rx_base+offset, run)
            for i, word in enumerate(words):
                index = rx_drained+i
                # The ramp makes the loop delay readable from one sample:
                # a returned word is the absolute index that produced it.
                if index < warmup:
                    continue
                this = (index-word) % pattern_modulus if rx_constant is None else int(word != rx_constant)
                if delay is None:
                    delay, locked_at = this if rx_constant is None else 0, index
                if this != delay or (rx_constant is None and word >= pattern_modulus):
                    anomalies += 1
                    # Re-read the same ring word. The DSP will not overwrite
                    # this position for another `size` samples, so a value
                    # that is correct on the second look was never in DM: the
                    # zero came from the IDMA read, not from the wire.
                    again = idma.read_dm(rx_base+(index % size))
                    correct = (again == rx_constant if rx_constant is not None else
                               again < pattern_modulus and (index-again) % pattern_modulus == delay)
                    if correct:
                        reread_correct += 1
                    else:
                        reread_still_wrong += 1
                    if word:
                        other_words += 1
                    else:
                        zero_words += 1
                    if last_anomaly is not None:
                        gaps.append(index-last_anomaly)
                    last_anomaly = index
                    if len(mismatches) < 32:
                        mismatches.append({'index': index, 'word': word,
                                           'delay': this, 'expected_delay': delay,
                                           'poll': polls, 'elapsed': now-started,
                                           'batch_start': rx_drained,
                                           'batch_size': run,
                                           'rx_count': rx_abs, 'tx_count': tx_abs})
                        if 'rx_shadow' in ring:
                            mismatches[-1]['shadow_word'] = idma.read_dm(
                                ring['rx_shadow']+index % size)
            rx_drained += run
            pending -= run

        if cyclic_tx:
            # Reuse the boot pattern without any live TX DM writes. This
            # control isolates write contention from RX reads and the wire.
            tx_filled = tx_abs+size
        if tx_abs > tx_filled:
            underruns += 1
            tx_filled = tx_abs
        limit = tx_abs+size-margin
        while tx_filled < limit:
            offset = tx_filled % size
            run = min(limit-tx_filled, size-offset)
            idma.write_block(tx_base+offset,
                             [(tx_filled+i) & 0xFFFF for i in range(run)])
            tx_filled += run
        if verbose and polls % 250 == 0:
            print(f'  {now-started:5.1f}s rx={rx_abs} tx={tx_abs} '
                  f'peak_occupancy={occupancy_peak} overruns={overruns} '
                  f'underruns={underruns} delay={delay}', flush=True)

    elapsed = time.perf_counter()-started
    idma.write_dm(ring['stop'], 1)
    time.sleep(0.01)
    return {'seconds': round(elapsed, 3), 'polls': polls, 'period': period,
            'tx_mode': 'cyclic' if cyclic_tx else 'streamed',
            'pattern_modulus': pattern_modulus,
            'rx_constant': rx_constant,
            'tx_count_granularity': ring.get('tx_count_granularity', 1),
            'margin': margin, 'ring': size, 'warmup': warmup,
            'delay_locked_at': locked_at,
            'verified_samples': max(0, rx_drained-warmup),
            'rx_samples': rx_abs, 'tx_samples': tx_abs, 'rx_drained': rx_drained,
            'rx_rate': round(rx_abs/elapsed, 1), 'tx_rate': round(tx_abs/elapsed, 1),
            'overruns': overruns, 'underruns': underruns,
            'peak_occupancy': occupancy_peak, 'loop_delay': delay,
            'delay_mismatches': mismatches,
            'anomalies': anomalies, 'zero_words': zero_words,
            'other_words': other_words,
            'reread_correct': reread_correct,
            'reread_still_wrong': reread_still_wrong,
            'anomaly_rate': round(anomalies/max(1, rx_drained-warmup), 9),
            'anomaly_gaps': {'count': len(gaps),
                             'min': min(gaps) if gaps else None,
                             'max': max(gaps) if gaps else None,
                             'mean': round(sum(gaps)/len(gaps), 1) if gaps else None},
            'alive_first': alive_first, 'alive_last': idma.read_dm(ring['alive']),
            'final_rx_count': idma.read_dm(rx_cell),
            'final_tx_count': idma.read_dm(tx_cell)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('spec')
    ap.add_argument('--pci', required=True)
    ap.add_argument('--adapter', type=int, required=True)
    ap.add_argument('--serial', type=int, required=True)
    ap.add_argument('--port', type=int, choices=range(8), default=0, help='which DSP, 0..7')
    ap.add_argument('--seconds', type=float, default=60.0)
    ap.add_argument('--period', type=float, default=0.020)
    ap.add_argument('--margin', type=int, default=300)
    ap.add_argument('--out', required=True)
    ap.add_argument('--quiet', action='store_true')
    ap.add_argument('--cyclic-tx', action='store_true',
                    help='diagnostic: repeat the boot TX ramp without live TX writes')
    ap.add_argument('--rx-constant', type=lambda s: int(s, 0),
                    help='diagnostic: IPAC drives a constant DU word instead of looping DD')
    ap.add_argument('--no-live-idma', action='store_true',
                    help='diagnostic: wait without host accesses, then read the DSP zero log')
    args = ap.parse_args()
    if args.rx_constant is not None and not 0 <= args.rx_constant <= 0xFFFF:
        ap.error('--rx-constant must be an unsigned 16-bit word')
    if (args.pci, args.adapter, args.serial) != ('0000:02:02.0', 5, 12196):
        raise SystemExit('the ring is restricted to spare adapter 5 / SN 12196')
    spec = json.loads(open(args.spec).read())
    capture = spec.get('capture')
    ring = spec.get('ring', {'ring': 1024})
    if capture and args.rx_constant is None:
        ap.error('autonomous RX capture requires --rx-constant')
    if args.no_live_idma and ('zero_log' not in ring or not args.cyclic_tx or args.rx_constant is not None):
        ap.error('--no-live-idma requires a zero-log program, --cyclic-tx and looped RX')
    if ring.get('tx_autobuffer') and not args.cyclic_tx:
        ap.error('TX autobuffer diagnostic requires --cyclic-tx')
    try:
        validate_service(ring, args.seconds, args.period, args.margin)
    except ValueError as exc:
        ap.error(str(exc))
    check_adapter(args.pci, args.adapter, args.serial)
    ctl = map_bar(args.pci, 3)
    stock = takeover(ctl, args.rx_constant)
    print(f"stock interfaces preserved: RISC={stock['risc']:x} "
          f"reset={stock['reset']:x} IPACX={stock['ipacx_config']:x}", flush=True)
    reset = ctypes.c_uint32.from_buffer(ctl, ISAC_DSP_RESET)
    result = {'source': f'card {args.serial} ({args.pci})',
              'port': f"{PORTS[args.port]:#06x}", 'stock_interfaces': stock}
    try:
        reset.value = 0x1F                 # release DSPs, IPACs stay up
        time.sleep(0.05)
        idma = Idma(ctl, PORTS[args.port])
        result['echo'] = idma.echo()
        if not result['echo']:
            raise SystemExit('DSP IDMA port did not echo')
        idma.boot(spec['boot'])
        time.sleep(0.05)
        print(f"loaded {len(spec['boot'])} IDMA writes; servicing "
              f"{args.seconds:.0f}s at {args.period*1000:.0f} ms", flush=True)
        if capture:
            time.sleep(args.seconds)
            idma.write_dm(capture['system'], capture['sports_off'])
            time.sleep(.01)
            words = idma.read_block(capture['base'], capture['size'])
            bad = [i for i, word in enumerate(words) if word != args.rx_constant]
            result.update(autonomous_rx_capture=True, seconds=args.seconds,
                          expected_word=args.rx_constant, words=words,
                          pointer=idma.read_dm(capture['pointer']),
                          anomalies=len(bad), anomaly_offsets=bad,
                          clean=not bad, scope='RX-only static-word capture; no streamed TX')
        elif args.no_live_idma:
            time.sleep(args.seconds)
            idma.write_dm(ring['stop'], 1)
            time.sleep(.01)
            count = idma.read_dm(ring['zero_count'])
            entries = idma.read_block(ring['zero_log'], min(count, ring['zero_log_size']))
            result.update(no_live_idma=True, zero_count=count, zero_log=entries,
                          zero_log_overflow=count > ring['zero_log_size'],
                          seconds=args.seconds, clean=False,
                          scope='zero-event diagnostic; not a streamed media qualification')
            if not result['zero_log_overflow']:
                result['unexpected_zero_indices'] = unexpected_zero_indices(
                    entries, ring['counter_modulus'], ring['ring'])
        else:
            result.update(service(idma, ring, args.seconds, args.period,
                                  args.margin, not args.quiet, cyclic_tx=args.cyclic_tx,
                                  rx_constant=args.rx_constant))
    finally:
        reset.value = 0x1E                 # DSPs back in reset, IPACs preserved
    if capture:
        open(args.out, 'w').write(json.dumps(result, indent=1)+'\n')
        print(f"autonomous RX capture: {len(words)} words, {len(bad)} mismatches")
        return int(bool(bad))
    if args.no_live_idma:
        open(args.out, 'w').write(json.dumps(result, indent=1)+'\n')
        print(f"quiet IDMA control: {result['zero_count']} RX zero events; "
              f"log overflow={result['zero_log_overflow']}")
        return 0 if not result['zero_log_overflow'] else 1
    result.update(classify_result(result))
    ok = result['clean']
    open(args.out, 'w').write(json.dumps(result, indent=1)+'\n')
    print(f"rx={result['rx_samples']} ({result['rx_rate']} words/s) "
          f"tx={result['tx_samples']} ({result['tx_rate']} words/s) "
          f"overruns={result['overruns']} underruns={result['underruns']} "
          f"peak_occupancy={result['peak_occupancy']} delay={result['loop_delay']} "
          f"verified={result['verified_samples']} anomalies={result['anomalies']} "
          f"(zero={result['zero_words']} other={result['other_words']}) "
          f"clean={result['clean']}")
    print('restore the card with: divactrl load -c 5 -StopCard; '
          'divactrl load -c 5 -ResetCard; '
          'divactrl load -CfgLib -c 5 -UseCardMap "2048,0"')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
