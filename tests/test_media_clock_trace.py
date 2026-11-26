"""Deterministic diagnostics and two-peer clock coupling; no firmware/JIT."""
import json
import sys
import threading
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from eicon_adsp_sip import Call, EiconSipEndpoint
from media_clock_trace import MediaClockTrace, summarize, valid_receipt


def new_call():
    return Call(sip_peer=('127.0.0.1', 1), rtp_peer=('127.0.0.1', 2),
                call_id='synthetic', local_tag='test', card=None,
                samples=80000, packets=5, rx_started=True,
                next_tick=100.0, next_send=100.0)


def test_trace_is_bounded_and_does_not_write_until_dump(tmp_path):
    c = new_call()
    trace = MediaClockTrace(tmp_path / 'trace.json', capacity=3, incident_capacity=2)
    for index in range(10):
        c.tx_underruns += 1
        trace.record('underrun', c, now=100 + index)
    assert not trace.path.exists()
    result = trace.result(now=110)
    assert len(result['events']) == 3
    assert result['dropped_events'] == 7
    assert len(result['underruns']) == 2
    assert result['dropped_incidents'] == 8
    assert not valid_receipt(result, 10)  # Not torn down yet.
    trace.record('teardown', c, now=110)
    trace.dump()
    saved = json.loads(trace.path.read_text())
    assert saved['total_events'] == 11
    assert valid_receipt(saved, 10)
    assert not valid_receipt(saved, 9)
    assert not valid_receipt({}, 0)
    assert summarize(saved)['dropped_incidents'] == 8


def test_wait_measures_elapsed_time_not_retry_count(tmp_path):
    c = new_call()
    trace = MediaClockTrace(tmp_path / 'trace.json')
    trace.record('rx_wait_begin', c, now=100)
    for _ in range(20):
        trace.record('rx_wait_begin', c, now=100.1)
    assert trace.result(now=100.25)['actual_rx_wait_seconds'] == .25
    trace.record('rx_wait_end', c, now=100.5)
    trace.record('rx_wait_end', c, now=101)
    assert trace.result(now=102)['actual_rx_wait_seconds'] == .5
    assert trace.total_events == 2


def test_incident_distinguishes_active_producer_from_rx_wait(tmp_path):
    c = new_call()
    trace = MediaClockTrace(tmp_path / 'trace.json', settings=dict(
        producer_quantum_samples=160, rx_prefill_samples=320))
    trace.record('producer_begin', c, now=100)
    trace.record('underrun', c, now=100.03)
    trace.record('producer_end', c, now=100.04)
    c.rx.extend([0] * 160)
    trace.record('rx_wait_begin', c, now=100.05)
    trace.record('underrun', c, now=100.08)
    a, b = trace.result(now=100.09)['underruns']
    assert a['producer_active'] and a['rx_wait_started'] is None
    assert not b['producer_active'] and b['rx_wait_started'] == 100.05
    assert abs(a['send_lateness_seconds'] - .03) < 1e-10
    summary = summarize(trace.result(now=100.09))
    assert summary['retained_incident_states'] == dict(rx_waiting=1, producer_active=1, neither=0)
    assert summary['rx_wait_with_full_quantum_below_prefill'] == 1


def simulate(tmp_path, stall_seconds):
    """Two real pacing/gating state machines, 5ms delivery, zero DSP cost.

    Only A's producer is paused; both wire clocks and the network keep running.
    No silence/repeat policy or real wall-clock sleeps are involved.
    """
    calls = [new_call(), new_call()]
    endpoints = []
    deliveries = []
    now = [100.0]
    for index, c in enumerate(calls):
        c.rx = deque([0] * 480)
        c.tx_queue.append(bytes(160))
        c.media_trace = MediaClockTrace(tmp_path / f'{index}.json')
        ep = object.__new__(EiconSipEndpoint)
        ep.call = c
        ep.realtime = False
        ep.rx_drain_samples = 8000
        c.synthetic_busy_polls = 0
        ep.tx_target_quanta = 1
        ep.rtp_packet_seconds = .020
        ep.repeat_tx_underrun = False
        ep.repeat_rx_underrun = False
        ep.rx_guard_samples = 0
        ep.rx_prefill_samples = 320
        ep.rx_hold_seconds = .5

        def send(call, payload=None, index=index):
            assert payload is None
            call.tx_queue.popleft()
            call.media_trace.record('tx_send', call)
            deliveries.append((now[0] + .005, 1 - index))
            call.tx_sends += 1

        ep.transmit_one = send
        endpoints.append(ep)
    with patch('time.monotonic', side_effect=lambda: now[0]):
        for step in range(350):
            now[0] = 100 + step / 1000
            arrived = [item for item in deliveries if item[0] <= now[0]]
            deliveries[:] = [item for item in deliveries if item[0] > now[0]]
            for _, index in arrived:
                calls[index].rx.extend([0] * 160)
                calls[index].media_trace.record('rx_arrival', calls[index])
            for ep, c in zip(endpoints, calls):
                ep.service_transmit(c, now[0])
            for index, (ep, c) in enumerate(zip(endpoints, calls)):
                if index == 0 and step / 1000 < stall_seconds:
                    continue
                if (c.rx_hold_until is not None and c.rx_retry_at > now[0]
                        and ep.next_wakeup(now[0]) == 0):
                    c.synthetic_busy_polls += 1
                if ep.wants_quantum(c, now[0]) and ep.rx_ready(c, now[0]):
                    c.media_trace.record('producer_begin', c)
                    assert len(c.rx) >= 160  # No fabricated received samples.
                    for _ in range(160):
                        c.rx.popleft()
                    c.samples += 160
                    c.next_tick += .020
                    c.tx_queue.append(bytes(160))
                    c.media_trace.record('tx_enqueue', c)
                    c.media_trace.record('producer_end', c)
    return calls


def test_unstalled_two_peer_control_has_no_underruns(tmp_path):
    a, b = simulate(tmp_path, 0)
    assert a.tx_underruns == b.tx_underruns == 0
    assert a.tx_sends > 10 and b.tx_sends > 10


def test_overdue_sender_wakes_when_fresh_audio_arrives():
    # Regression: producer publication wakes an overdue sender at +10ms,
    # rather than leaving real audio queued until a fixed backoff expires.
    wall = [100.005]
    sent = []
    c = new_call()
    ep = object.__new__(EiconSipEndpoint)
    ep.tx_target_quanta = 1
    ep.rtp_packet_seconds = .020
    ep.repeat_tx_underrun = False

    class Stop:
        waits = 0
        stopped = False

        def is_set(self):
            return self.stopped

        def clear(self):
            pass

        def wait(self, delay):
            self.waits += 1
            if self.waits == 1:
                assert wall[0] < 100.010 < wall[0] + delay
                c.tx_queue.append(b'fresh')
                wall[0] = 100.010  # enqueue notification interrupts the wait
            else:
                wall[0] += delay
                self.stopped = True
            return True

    def send(call):
        assert call.tx_queue.popleft() == b'fresh'
        sent.append(wall[0])

    c.tx_stop = c.tx_wake = Stop()
    ep.transmit_one = send
    with patch('time.monotonic', side_effect=lambda: wall[0]):
        ep._transmit_loop(c)
    assert len(sent) == 1
    assert abs(sent[0] - 100.010) < 1e-9
    assert c.tx_underruns == 0  # A late send is not necessarily counted.


@pytest.mark.parametrize('publication', ['before_clear', 'during_wait'])
def test_sender_does_not_lose_publication_or_send_ahead_of_deadline(publication):
    wall = [100.0]
    sent = []
    c = new_call()
    c.next_send = 100.02
    ep = object.__new__(EiconSipEndpoint)
    ep.tx_target_quanta = 1
    ep.rtp_packet_seconds = .020
    ep.repeat_tx_underrun = False
    ep.law = 'pcmu'
    ep.rtp_packet_samples = 160
    ep.codec = SimpleNamespace(encode_g711=lambda samples: bytes(samples))
    stop = threading.Event()
    c.tx_stop = stop

    class Wake(threading.Event):
        clears = 0
        waits = 0

        def clear(self):
            self.clears += 1
            if publication == 'before_clear' and self.clears == 1:
                ep._queue_rtp(c, [1] * 160)
            super().clear()

        def wait(self, delay):
            self.waits += 1
            if publication == 'during_wait' and self.waits == 1:
                ep._queue_rtp(c, [1] * 160)
                assert super().wait(0)  # publication is not lost before wait
                return True
            if sent:
                stop.set()
                return True
            assert delay > 0
            wall[0] += delay
            return False

    def send(call):
        assert call.tx_queue.popleft() == bytes([1] * 160)
        sent.append(wall[0])

    ep.transmit_one = send
    c.tx_wake = Wake()
    with patch('time.monotonic', side_effect=lambda: wall[0]):
        ep._transmit_loop(c)
    assert len(sent) == 1
    assert abs(sent[0] - 100.02) < 1e-9


def test_producer_notifies_only_when_a_whole_rtp_packet_is_ready():
    ep = object.__new__(EiconSipEndpoint)
    ep.law = 'pcmu'
    ep.rtp_packet_samples = 160
    ep.tx_target_quanta = 1
    ep.codec = SimpleNamespace(encode_g711=lambda samples: bytes(samples))
    c = new_call()
    ep._queue_rtp(c, [1] * 80)
    assert not c.tx_wake.is_set() and not c.tx_queue
    ep._queue_rtp(c, [2] * 80)
    assert c.tx_wake.is_set()
    assert list(c.tx_queue) == [bytes([1] * 80 + [2] * 80)]


def test_stop_interrupts_a_wire_wait_far_in_the_future():
    c = new_call()
    c.next_send = time.monotonic() + 3600
    c.tx_queue.append(b'queued')
    entered = threading.Event()

    class Wake(threading.Event):
        def wait(self, delay):
            entered.set()
            return super().wait(delay)

    c.tx_wake = Wake()
    ep = object.__new__(EiconSipEndpoint)
    ep.tx_target_quanta = 1
    ep.rtp_packet_seconds = .020
    ep.transmit_one = lambda call: pytest.fail('sent before the deadline')
    ep.start_transmit_clock(c)
    thread = c.tx_thread
    try:
        assert entered.wait(5), 'sender never entered its deadline wait'
        ep.stop_transmit_clock(c)
        assert not thread.is_alive()
    finally:
        c.tx_stop.set()
        c.tx_wake.set()
        thread.join(timeout=5)


def test_buffered_rx_hold_uses_retry_without_moving_media_or_wire_schedule():
    ep = object.__new__(EiconSipEndpoint)
    ep.realtime = False
    ep.rx_drain_samples = 8000
    ep.tx_target_quanta = 1
    c = new_call()
    c.next_send = 99.9
    c.next_tick = 99.8
    c.rx_hold_until = 100.5
    c.rx_retry_at = 100.002
    ep.call = c
    assert abs(ep.next_wakeup(100) - .002) < 1e-9
    assert (c.next_send, c.next_tick) == (99.9, 99.8)
    assert ep.next_wakeup(100.002) == 0  # retry is now due


def test_one_producer_pause_propagates_into_peer_rx_wait_and_tx_starvation(tmp_path):
    a, b = simulate(tmp_path, .120)
    assert a.tx_underruns > 0
    assert b.tx_underruns > 0
    incidents = b.media_trace.result(now=100.35)['underruns']
    assert any(e['rx_wait_started'] is not None and not e['producer_active']
               and e['tx_packets'] == 0 and e['rx_samples'] < 320 for e in incidents)
    # This identifies a mechanism, not a claim that it caused every live gap.
    assert b.media_trace.result(now=100.35)['actual_rx_wait_seconds'] > 0
    # Waiting on RX must not spin the producer on wire-thread deadlines.
    assert b.synthetic_busy_polls == 0
    assert a.rx_substituted == b.rx_substituted == 0
