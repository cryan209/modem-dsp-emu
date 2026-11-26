"""Bounded opt-in main-thread CPU/wall profiling, never a pacing policy.

thread_time_ns includes native code on this thread, not work on other threads.
Wall minus CPU includes descheduling, GIL/lock waits and blocking; it does not
identify which of those occurred. All intervals retain raw signed differences.
"""
from collections import deque
import argparse
import json
from pathlib import Path
import threading
import time

PHASES = ('sample_pump', 'status_reactive', 'diagnostics', 'tx_encode_enqueue', 'link', 'bookkeeping')
BUDGET_NS = 20_000_000


def accumulate(stats, measurement):
    for key in ('wall_ns', 'cpu_ns', 'non_cpu_ns'):
        stats[key] += measurement[key]
    stats['max_wall_ns'] = max(stats['max_wall_ns'], measurement['wall_ns'])
    value = measurement['non_cpu_ns']
    stats['max_non_cpu_ns'] = value if stats['max_non_cpu_ns'] is None else max(value, stats['max_non_cpu_ns'])
    stats['negative_gap_samples'] += value < 0


def empty_stats():
    return dict(wall_ns=0, cpu_ns=0, non_cpu_ns=0, max_wall_ns=0,
                max_non_cpu_ns=None, negative_gap_samples=0)


def snapshot(call):
    return dict(samples=call.samples, trn_progress=call.trn_progress,
                rx_samples=len(call.rx), tx_packets=len(call.tx_queue),
                next_send=call.next_send, next_tick=call.next_tick,
                rx_hold_until=call.rx_hold_until, rx_retry_at=call.rx_retry_at)


class ProducerProfile:
    def __init__(self, path, capacity=2048, slow_capacity=128):
        self.path = Path(path)
        self.records = deque(maxlen=capacity)
        self.slow = deque(maxlen=slow_capacity)
        self.groups = {}
        self.between_groups = {}
        self.long_between = deque(maxlen=slow_capacity)
        self.long_between_count = 0
        self.previous = None
        self.count = self.slow_count = 0
        self.active = None
        self.owner = None
        self.closed = False
        self.expected_quanta = None

    def _stamp(self):
        ident = threading.get_ident()
        if self.owner is not None and ident != self.owner:
            raise RuntimeError('producer profile crossed threads')
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError('producer profile must start on main producer thread')
        self.owner = ident
        return time.monotonic_ns(), time.thread_time_ns()

    def begin(self, call):
        if self.closed or self.active is not None:
            raise RuntimeError('overlapping or closed producer profile')
        state = snapshot(call)
        wall, cpu = self._stamp()
        between = None
        if self.previous is not None:
            pw, pc, group, end_state = self.previous
            if wall < pw or cpu < pc:
                raise RuntimeError('producer profile clock regressed between quanta')
            between = dict(begin_ns=pw, end_ns=wall, wall_ns=wall - pw,
                           cpu_ns=cpu - pc, non_cpu_ns=(wall - pw) - (cpu - pc),
                           previous_group=group, begin=end_state, end=state)
        self.active = dict(sequence=self.count, begin_ns=wall, cpu_begin_ns=cpu,
                           begin=state, phases={}, between=between)
        self.last = wall, cpu

    def mark(self, phase):
        if self.active is None or phase != PHASES[len(self.active['phases'])]:
            raise RuntimeError('out-of-order producer profile phase')
        wall, cpu = self._stamp()
        dw, dc = wall - self.last[0], cpu - self.last[1]
        if dw < 0 or dc < 0:
            raise RuntimeError('producer profile clock regressed')
        self.active['phases'][phase] = dict(wall_ns=dw, cpu_ns=dc, non_cpu_ns=dw - dc)
        self.last = wall, cpu

    def end(self, call):
        self.mark('bookkeeping')
        record = self.active
        self.active = None
        wall, cpu = self.last
        record.update(end_ns=wall, end=snapshot(call),
                      wall_ns=wall - record['begin_ns'], cpu_ns=cpu - record.pop('cpu_begin_ns'))
        record['non_cpu_ns'] = record['wall_ns'] - record['cpu_ns']
        record['group'] = ('data' if record['begin']['trn_progress'] == record['end']['trn_progress'] == 0x00d0
                           else 'training_or_transition')
        group = self.groups.setdefault(record['group'], dict(count=0, slow_count=0, phases={}))
        group['count'] += 1
        slow = record['wall_ns'] > BUDGET_NS
        group['slow_count'] += slow
        for name, measurement in [('total', record), *record['phases'].items()]:
            stats = group['phases'].setdefault(name, empty_stats())
            accumulate(stats, measurement)
        between = record['between']
        if between is not None:
            between['group'] = ('data' if between['previous_group'] == record['group'] == 'data'
                                else 'training_or_transition')
            stats = self.between_groups.setdefault(between['group'], dict(empty_stats(), count=0))
            stats['count'] += 1
            accumulate(stats, between)
            if between['wall_ns'] > BUDGET_NS:
                self.long_between_count += 1
                self.long_between.append(dict(between, following_sequence=record['sequence']))
        self.previous = wall, cpu, record['group'], record['end']
        self.count += 1
        self.records.append(record)
        if slow:
            self.slow_count += 1
            self.slow.append(record)

    def close(self, call):
        self.expected_quanta = call.tick_count
        self.closed = True

    def result(self):
        return dict(schema_version=1, closed=self.closed, owner_thread=self.owner,
                    main_thread=self.owner == threading.main_thread().ident,
                    clock='monotonic_ns + main thread_time_ns', budget_ns=BUDGET_NS,
                    completed_quanta=self.count, expected_quanta=self.expected_quanta,
                    unfinished_quantum=self.active, groups=self.groups,
                    between_groups=self.between_groups,
                    long_between_records=list(self.long_between),
                    dropped_long_between=self.long_between_count - len(self.long_between),
                    record_capacity=self.records.maxlen, slow_capacity=self.slow.maxlen,
                    dropped_records=self.count - len(self.records),
                    dropped_slow_records=self.slow_count - len(self.slow),
                    records=list(self.records), slow_records=list(self.slow))

    def dump(self):
        from v34_payload import write_json
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.path, self.result())


def valid_receipt(value, expected_quanta=None):
    count = value.get('completed_quanta')
    expected = value.get('expected_quanta') if expected_quanta is None else expected_quanta
    if not (value.get('schema_version') == 1 and value.get('closed') is True
            and value.get('main_thread') is True
            and type(count) is int and count > 0 and type(expected) is int and count == expected
            and value.get('expected_quanta') == expected and value.get('unfinished_quantum') is None):
        return False
    groups = value.get('groups', {})
    if not groups or set(groups) - {'data', 'training_or_transition'}:
        return False
    for group in groups.values():
        if type(group.get('count')) is not int or group['count'] <= 0:
            return False
        phases = group.get('phases', {})
        if set(phases) != set(PHASES) | {'total'}:
            return False
        for phase in phases.values():
            if (type(phase.get('wall_ns')) is not int or phase['wall_ns'] < 0
                    or type(phase.get('cpu_ns')) is not int or phase['cpu_ns'] < 0
                    or phase.get('non_cpu_ns') != phase['wall_ns'] - phase['cpu_ns']):
                return False
        if any(phases['total'][key] != sum(phases[p][key] for p in PHASES) for key in ('wall_ns', 'cpu_ns')):
            return False
    return (sum(g['count'] for g in groups.values()) == count
            and sum(g['count'] for g in value.get('between_groups', {}).values()) == count - 1
            and len(value.get('records', [])) + value.get('dropped_records', -1) == count
            and len(value.get('slow_records', [])) + value.get('dropped_slow_records', -1)
                == sum(g.get('slow_count', -1) for g in groups.values()))


def summarize(value, trace=None):
    result = dict(valid=valid_receipt(value), completed_quanta=value.get('completed_quanta'),
                  dropped_records=value.get('dropped_records'), dropped_slow_records=value.get('dropped_slow_records'),
                  groups={}, between_groups={}, incident_quanta=[],
                  dropped_long_between=value.get('dropped_long_between'))
    for name, group in value.get('groups', {}).items():
        count = group['count']
        result['groups'][name] = dict(count=count, slow_count=group['slow_count'], phases={
            phase: dict(wall_mean_ms=s['wall_ns'] / count / 1e6,
                        cpu_mean_ms=s['cpu_ns'] / count / 1e6,
                        non_cpu_mean_ms=s['non_cpu_ns'] / count / 1e6,
                        max_wall_ms=s['max_wall_ns'] / 1e6,
                        max_non_cpu_ms=s['max_non_cpu_ns'] / 1e6,
                        negative_gap_samples=s['negative_gap_samples'])
            for phase, s in group['phases'].items()})
    for name, s in value.get('between_groups', {}).items():
        count = s['count']
        result['between_groups'][name] = dict(count=count, wall_mean_ms=s['wall_ns'] / count / 1e6,
            cpu_mean_ms=s['cpu_ns'] / count / 1e6, non_cpu_mean_ms=s['non_cpu_ns'] / count / 1e6,
            max_wall_ms=s['max_wall_ns'] / 1e6, max_non_cpu_ms=s['max_non_cpu_ns'] / 1e6,
            negative_gap_samples=s['negative_gap_samples'])
    if trace is not None:
        records = {r['sequence']: r for r in value.get('records', []) + value.get('slow_records', [])}
        result['trace_dropped_events'] = trace.get('dropped_events')
        result['trace_dropped_incidents'] = trace.get('dropped_incidents')
        intervals = {b['end_ns']: b for b in value.get('long_between_records', [])}
        intervals.update({r['between']['end_ns']: r['between'] for r in records.values() if r.get('between')})
        for incident in trace.get('underruns', []):
            when = round(incident['time'] * 1e9)
            match = next((r for r in records.values() if r['begin_ns'] <= when < r['end_ns']), None)
            between = next((b for b in intervals.values() if b['begin_ns'] <= when < b['end_ns']), None)
            result['incident_quanta'].append(dict(time=incident['time'], rx_waiting=incident['rx_wait_started'] is not None,
                producer_active=incident['producer_active'], quantum=match, between_quanta=between))
    return result


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('profile', type=Path)
    ap.add_argument('--trace', type=Path)
    args = ap.parse_args()
    print(json.dumps(summarize(json.loads(args.profile.read_text()),
                              json.loads(args.trace.read_text()) if args.trace else None), indent=2))
