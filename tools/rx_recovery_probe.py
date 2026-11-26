"""Deterministic RX recovery controls; no DSP, audio or timing qualification.

Production RX gating, queue gating and wire scheduling operate on identity
packets. The optional reference changes prefill only inside this model, only
for an already-started data call. It is NOT an endpoint recovery option.
"""
import argparse
from collections import deque
from dataclasses import dataclass
import json
from pathlib import Path
from unittest.mock import patch

from eicon_adsp_sip import Call, EiconSipEndpoint
from media_clock_trace import MediaClockTrace

QUANTUM = 160
PERIOD_MS = 20
ORIGIN = 100.0


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


@dataclass(frozen=True)
class Packet:
    peer: int
    sequence: int

    def samples(self):
        return [(self.peer, self.sequence, i) for i in range(QUANTUM)]


def endpoint(index, phase, directory):
    call = Call(sip_peer=('127.0.0.1', 1), rtp_peer=('127.0.0.1', 2),
                call_id=f'model-{index}', local_tag='model', card=None,
                samples=80000, packets=5, rx_started=True,
                next_tick=ORIGIN, next_send=ORIGIN,
                trn_progress=0x00d0 if phase == 'data' else 0x0080)
    # Explicit prehistory, not fabricated recovery packets or modem audio.
    for seq in (-2, -1):
        call.rx.extend(Packet(1 - index, seq).samples())
    call.tx_queue.append(Packet(index, 0))
    call.media_trace = MediaClockTrace(directory / f'peer-{index}.json', settings=dict(
        simulation_only=True, phase=phase, rx_prefill_samples=320,
        producer_quantum_samples=QUANTUM, packet_seconds=.020))
    ep = object.__new__(EiconSipEndpoint)
    ep.call = call
    ep.realtime = True
    ep.pty = None
    ep.rx_drain_samples = 8000
    ep.rx_guard_samples = 0
    ep.rx_prefill_samples = 320
    ep.rx_hold_seconds = .5
    ep.tx_target_quanta = 1
    ep.rtp_packet_seconds = .020
    ep.repeat_tx_underrun = ep.repeat_rx_underrun = False
    ep.hold_rx_starvation = False
    # Actual qualifier data calls do not promote data_mode with a zero
    # data-buffer setting. Use observed training progress, not that flag.
    ep.data_mode = False
    return ep, call


def run_case(*, phase='data', reference=False, costs=(12, 9), pause_ms=0,
             delays=(5,), duration_ms=400, request_ms=100, gap_budget_ms=40):
    """1-ms-grid observation with always-active RX dispatch and wire clocks.

    Pause A's producer between completed quanta after real publication with
    >=2ms future send deadline. RX dispatch stays active: unlike the live
    producer/RX-dispatch fault, this isolates supply/gating in a synthetic model.
    """
    if phase not in ('training', 'data') or len(costs) != 2 or any(type(c) is not int or c < 0 for c in costs):
        raise ValueError('phase and two nonnegative integer costs required')
    if not delays or any(type(d) is not int or d < 0 for d in delays) or max(delays) - min(delays) >= PERIOD_MS:
        raise ValueError('nonnegative integer delays with <20ms spread required')
    if (any(type(t) is not int for t in (pause_ms, request_ms, duration_ms))
            or pause_ms < 0 or request_ms < 0 or duration_ms <= 0 or duration_ms > 2000):
        raise ValueError('invalid integer pause/request/duration (maximum duration 2000ms)')
    peers = [endpoint(i, phase, Path('model-not-dumped')) for i in range(2)]
    arrivals = []
    sent = [[], []]
    received = [deque(c.rx) for _, c in peers]
    consumed = [0, 0]
    next_received = [0, 0]
    arrival_count = [0, 0]
    produced = [0, 0]
    busy = [None, None]
    hold = [None, None]
    episodes = [[], []]
    resumed_outputs = [{}, {}]
    failures = []
    pause = None
    now = [ORIGIN]
    step = [0]

    def publication(index):
        nonlocal pause
        ep, call = peers[index]
        produced[index] += 1
        call.samples += QUANTUM
        call.next_tick += .020  # Never forgive a deficit on RX hold.
        call.tx_queue.append(Packet(index, produced[index]))
        call.media_trace.record('tx_enqueue', call)
        call.media_trace.record('producer_end', call)
        if (index == 0 and pause_ms and pause is None and step[0] >= request_ms
                and call.next_send - now[0] >= .002):
            pause = dict(begin_ms=step[0], end_ms=step[0] + pause_ms)

    for index, (ep, call) in enumerate(peers):
        def send(c, payload=None, index=index):
            require(payload is None, 'stale output requested')
            packet = c.tx_queue.popleft()
            require(packet == Packet(index, len(sent[index])), 'output duplicated/reordered')
            require(now[0] >= c.next_send, 'early send')
            sent[index].append(step[0])
            episode = resumed_outputs[index].pop(packet.sequence, None)
            if episode is not None:
                episode['output_sent_ms'] = step[0]
            delay = delays[packet.sequence % len(delays)]
            arrivals.append((step[0] + delay, 1 - index, packet))
            c.tx_sends += 1
            c.media_trace.record('tx_send', c)
        ep.transmit_one = send

    with patch('time.monotonic', side_effect=lambda: now[0]):
        for tick in range(duration_ms + 1):
            step[0] = tick
            now[0] = ORIGIN + tick / 1000
            due = [item for item in arrivals if item[0] <= tick]
            arrivals[:] = [item for item in arrivals if item[0] > tick]
            for _, index, packet in sorted(due, key=lambda item: item[0]):
                call = peers[index][1]
                if packet.sequence != next_received[index]:
                    failures.append(dict(peer=index, time_ms=tick, kind='arrival_reorder'))
                next_received[index] = packet.sequence + 1
                arrival_count[index] += 1
                samples = packet.samples()
                call.rx.extend(samples)
                call.packets += 1
                received[index].extend(samples)
                call.media_trace.record('rx_arrival', call)
            for i, (ep, call) in enumerate(peers):
                if busy[i] is not None and busy[i] <= tick:
                    busy[i] = None
                    publication(i)
                ep.service_transmit(call, now[0])
            for i, (ep, call) in enumerate(peers):
                if busy[i] is not None or (i == 0 and pause is not None and pause['begin_ms'] <= tick < pause['end_ms']):
                    continue
                if not ep.wants_quantum(call, now[0]):
                    continue
                old_tick = call.next_tick
                old_send = call.next_send
                # Test-only one-packet data recovery reference. Production
                # prefill is restored even on failure; training stays at 320.
                if reference and call.trn_progress == 0x00d0 and call.packets > 0:
                    ep.rx_prefill_samples = QUANTUM
                try:
                    ready = ep.rx_ready(call, now[0])
                finally:
                    ep.rx_prefill_samples = 320
                require((call.next_tick, call.next_send) == (old_tick, old_send), 'RX gate forgave a deadline')
                if not ready:
                    if hold[i] is None:
                        hold[i] = dict(begin_ms=tick, full_quantum_ms=None, resumed_ms=None)
                    if len(call.rx) >= QUANTUM and hold[i]['full_quantum_ms'] is None:
                        hold[i]['full_quantum_ms'] = tick
                    continue
                if len(call.rx) < QUANTUM:
                    # Production's timeout fallback would supply missing input.
                    # Record it, but do not execute it or invent model samples.
                    failures.append(dict(peer=i, time_ms=tick, kind='would_substitute_rx'))
                    continue
                if hold[i] is not None:
                    if hold[i]['full_quantum_ms'] is None:
                        hold[i]['full_quantum_ms'] = tick
                    hold[i]['resumed_ms'] = tick
                    resumed_outputs[i][produced[i] + 1] = hold[i]
                    episodes[i].append(hold[i])
                    hold[i] = None
                call.media_trace.record('producer_begin', call)
                for _ in range(QUANTUM):
                    require(call.rx.popleft() == received[i].popleft(), 'RX identity changed')
                    consumed[i] += 1
                busy[i] = tick + costs[i]
                if costs[i] == 0:
                    busy[i] = None
                    publication(i)
            require(all(len(c.tx_queue) <= 1 for _, c in peers), 'TX cushion grew')

    require(sum(map(len, sent)) == sum(arrival_count) + len(arrivals), 'network packet count mismatch')
    results = []
    for i, (ep, call) in enumerate(peers):
        require(consumed[i] == (produced[i] + (busy[i] is not None)) * QUANTUM, 'quantum count mismatch')
        require(list(call.rx) == list(received[i]), 'RX samples lost/duplicated')
        require(call.rx_substituted == call.rx_dropped == 0, 'RX substitution/loss')
        require(ep.rx_prefill_samples == 320, 'reference prefill leaked')
        require(produced[i] + 1 == len(sent[i]) + len(call.tx_queue), 'output packet count mismatch')
        require(320 + arrival_count[i] * QUANTUM == consumed[i] + len(call.rx), 'input count mismatch')
        if hold[i] is not None:
            episodes[i].append(hold[i])  # Preserve right-censored holds.
        gaps = [b - a for a, b in zip(sent[i], sent[i][1:])]
        tail = duration_ms - sent[i][-1] if sent[i] else duration_ms
        for episode in episodes[i]:
            start = episode['full_quantum_ms']
            end = episode['resumed_ms']
            episode['full_quantum_wait_ms'] = None if start is None else (duration_ms if end is None else end) - start
            episode['right_censored'] = end is None
            output = episode.get('output_sent_ms')
            episode['full_quantum_to_output_ms'] = None if start is None or output is None else output - start
            episode['output_right_censored'] = output is None
        results.append(dict(sends=len(sent[i]), send_times_ms=sent[i],
            max_gap_ms=max(gaps, default=None), tail_without_send_ms=tail,
            max_observed_silence_ms=max([tail, *gaps]), tx_underruns=call.tx_underruns,
            completed_quanta=produced[i], consumed_identity_samples=consumed[i],
            in_flight=busy[i] is not None, pending_rx_samples=len(call.rx),
            arrived_packets=arrival_count[i], initial_rx_samples=320,
            queued_tx_packets=len(call.tx_queue),
            holds=episodes[i], legacy_nominal_hold_ms=call.hold_time * 1000,
            actual_hold_ms=call.media_trace.result(now=now[0])['actual_rx_wait_seconds'] * 1000,
            model_budget_met=bool(gaps) and max([tail, *gaps]) <= gap_budget_ms and call.tx_underruns == 0))
    return dict(scope='synthetic identity model; not DSP/audio/PCAP qualification',
                phase=phase, policy='data-one-packet-reference' if reference else 'production',
                grid_ms=1, costs_ms=list(costs), delays_ms=list(delays), pause=pause,
                pause_requested_ms=pause_ms,
                observation_complete=(not pause_ms or pause is not None and pause['end_ms'] <= duration_ms),
                duration_ms=duration_ms, gap_budget_ms=gap_budget_ms,
                invariants_passed=not failures, failures=failures, peers=results,
                in_transit_packets=[dict(arrival_ms=t, receiver=i, sequence=p.sequence) for t, i, p in arrivals])


def suite():
    rows = []
    for phase in ('training', 'data'):
        for costs in ((0, 0), (12, 9), (17, 17)):
            for pause in (0, 20, 100):
                for reference in (False, True):
                    rows.append(run_case(phase=phase, costs=costs, pause_ms=pause, reference=reference))
        for reference in (False, True):
            rows.append(run_case(phase=phase, costs=(12, 9), delays=(2, 10, 4, 8), reference=reference))
    return dict(scope='simulation only; no timing qualification verdict',
                observations_complete=all(r['observation_complete'] for r in rows),
                invariants_passed=all(r['invariants_passed'] for r in rows), cases=rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True, help='new JSON file, never overwritten')
    args = ap.parse_args()
    if args.output.exists():
        ap.error('output already exists')
    result = suite()
    import hashlib
    import platform
    result['python_version'] = platform.python_version()
    result['source_sha256'] = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                               for name in ('rx_recovery_probe.py', 'eicon_adsp_sip.py', 'media_clock_trace.py')}
    from v34_payload import write_json
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, result)
    return 0 if result['invariants_passed'] and result['observations_complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
