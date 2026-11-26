"""Bounded, opt-in media diagnostics. No file I/O until call teardown.

Queue lengths are observational snapshots, not a transactional view of the
producer/consumer. Event times use one host's monotonic clock, never RTP time.
"""
from collections import deque
import json
from pathlib import Path
import threading
import time


class MediaClockTrace:
    def __init__(self, path: Path, capacity=8192, incident_capacity=128, settings=None):
        if capacity < 1 or incident_capacity < 1:
            raise ValueError('trace capacities must be positive')
        self.path = Path(path)
        self.initial_settings = dict(settings or {})
        self.events = deque(maxlen=capacity)
        self.incidents = deque(maxlen=incident_capacity)
        self.lock = threading.Lock()
        self.total_events = self.total_incidents = 0
        self.producer_started = self.producer_finished = None
        self.producer_active = False
        self.last_rx = None
        self.wait_started = None
        self.wait_seconds = 0.0

    def record(self, kind, call, now=None):
        with self.lock:
            now = time.monotonic() if now is None else now
            if kind == 'producer_begin':
                self.producer_active = True
                self.producer_started = now
            elif kind == 'producer_end':
                self.producer_active = False
                self.producer_finished = now
            elif kind in ('rx_arrival', 'rx_drop'):
                self.last_rx = now
            elif kind == 'rx_wait_begin':
                if self.wait_started is not None:
                    return
                self.wait_started = now
            elif kind == 'rx_wait_end':
                if self.wait_started is None:
                    return
                self.wait_seconds += now - self.wait_started
                self.wait_started = None
            event = dict(event=kind, time=now, rx_samples=len(call.rx),
                         tx_packets=len(call.tx_queue), samples=call.samples,
                         next_send=call.next_send, next_tick=call.next_tick,
                         send_lateness_seconds=(now - call.next_send if call.next_send else None),
                         last_rx=self.last_rx, rx_wait_started=self.wait_started,
                         producer_active=self.producer_active,
                         producer_started=self.producer_started,
                         producer_finished=self.producer_finished,
                         underruns=call.tx_underruns, rx_hold_until=call.rx_hold_until,
                         rx_retry_at=call.rx_retry_at, trn_progress=call.trn_progress,
                         rx_hold_attempts=call.rx_holds, nominal_rx_hold_seconds=call.hold_time,
                         tx_sequence=call.tx_seq, tx_timestamp=call.tx_timestamp,
                         tx_ssrc=call.ssrc)
            if kind == 'underrun':
                self.incidents.append(event)
                self.total_incidents += 1
            self.events.append(event)
            self.total_events += 1

    def result(self, now=None):
        with self.lock:
            now = time.monotonic() if now is None else now
            waiting = now - self.wait_started if self.wait_started is not None else 0
            return dict(schema_version=1, initial_settings=self.initial_settings,
                        event_capacity=self.events.maxlen,
                        incident_capacity=self.incidents.maxlen,
                        total_events=self.total_events,
                        dropped_events=self.total_events - len(self.events),
                        total_incidents=self.total_incidents,
                        dropped_incidents=self.total_incidents - len(self.incidents),
                        actual_rx_wait_seconds=self.wait_seconds + waiting,
                        events=list(self.events), underruns=list(self.incidents))

    def dump(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.result(), separators=(',', ':')) + '\n')
        temporary.replace(self.path)


def valid_receipt(trace, expected_underruns):
    """A requested trace must close cleanly and account for every counter hit."""
    events = trace.get('events', [])
    return (type(expected_underruns) is int and expected_underruns >= 0
            and trace.get('schema_version') == 1
            and trace.get('total_incidents') == expected_underruns
            and bool(events) and events[-1].get('event') == 'teardown'
            and events[-1].get('underruns') == expected_underruns)


def summarize(trace):
    """Describe observed states, NOT inferred root causes; disclose truncation."""
    states = {'rx_waiting': 0, 'producer_active': 0, 'neither': 0}
    enough_audio = 0
    settings = trace.get('initial_settings', {})
    quantum = settings.get('producer_quantum_samples')
    prefill = settings.get('rx_prefill_samples')
    for event in trace['underruns']:
        state = ('rx_waiting' if event['rx_wait_started'] is not None else
                 'producer_active' if event['producer_active'] else 'neither')
        states[state] += 1
        if (state == 'rx_waiting' and quantum is not None and prefill is not None
                and quantum <= event['rx_samples'] < prefill):
            enough_audio += 1
    send_lateness = [event['send_lateness_seconds'] for event in trace['events']
                     if event['event'] == 'tx_send' and event['send_lateness_seconds'] is not None]
    return dict(retained_sends=len(send_lateness),
                max_send_schedule_debt_ms=(max(send_lateness) * 1000 if send_lateness else None),
                total_events=trace['total_events'], dropped_events=trace['dropped_events'],
                total_incidents=trace['total_incidents'],
                dropped_incidents=trace['dropped_incidents'],
                actual_rx_wait_seconds=trace['actual_rx_wait_seconds'],
                retained_incident_states=states,
                rx_wait_with_full_quantum_below_prefill=enough_audio,
                nominal_rx_hold_seconds_at_end=(trace['events'][-1].get('nominal_rx_hold_seconds')
                                                if trace['events'] else None))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Summarize bounded media traces; states are not causal verdicts.')
    parser.add_argument('paths', nargs='+', type=Path)
    args = parser.parse_args()
    print(json.dumps({str(path): summarize(json.loads(path.read_text()))
                      for path in args.paths}, indent=2))
