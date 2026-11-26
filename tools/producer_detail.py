"""Opt-in bounded GC observations and emulated ADSP work counters.

No per-sample timers, GC policy changes, or claims of pure native CPU time.
GC runs on the collecting thread; other-thread GC can overlap native execution
that released the GIL and is not necessarily a stop of the main thread.
"""
from collections import deque
import ctypes
import gc
import threading
import time


def native_reader(library, card):
    """Read only card.cpu's existing uint64 emulated-cycle counter."""
    cpu = getattr(card, 'cpu', None)
    if not cpu:
        raise ValueError('producer detail requires a live card.cpu')
    function = library.adsp2181_cycles
    function.argtypes = [ctypes.c_void_p]
    function.restype = ctypes.c_uint64
    return lambda: int(function(cpu))


class ProducerDetail:
    def __init__(self, reader, backend, capacity=512):
        self.reader = reader
        self.backend = backend
        self.events = deque(maxlen=capacity)
        self.groups = {}
        self.native_groups = {}
        self.count = self.errors = self.native_count = self.native_total = 0
        self.pending = None
        self.closed = False
        self.attached_ns = time.monotonic_ns()
        self.detached_ns = None
        self._callback = self._observe_gc
        self.previous_callbacks = len(gc.callbacks)
        gc.callbacks.append(self._callback)

    def _observe_gc(self, phase, info):
        # CPython ignores callback exceptions. Convert instrumentation errors
        # into an invalid receipt instead of letting that silently pass.
        try:
            wall, cpu = time.monotonic_ns(), time.thread_time_ns()
            ident = threading.get_ident()
            if info['generation'] not in (0, 1, 2):
                self.errors += 1
                return
            if phase == 'start':
                if self.pending is not None:
                    self.errors += 1
                    return
                self.pending = dict(begin_ns=wall, cpu_begin_ns=cpu,
                                    thread=ident, generation=info['generation'],
                                    main_thread=ident == threading.main_thread().ident)
            elif phase == 'stop':
                event, self.pending = self.pending, None
                if (event is None or event['thread'] != ident
                        or event['generation'] != info['generation']):
                    self.errors += 1
                    return
                event.update(end_ns=wall, wall_ns=wall - event['begin_ns'],
                             cpu_ns=cpu - event.pop('cpu_begin_ns'),
                             collected=info['collected'], uncollectable=info['uncollectable'])
                if event['wall_ns'] < 0 or event['cpu_ns'] < 0:
                    self.errors += 1
                    return
                key = ('main' if event['main_thread'] else 'other') + f":g{event['generation']}"
                stats = self.groups.setdefault(key, dict(count=0, wall_ns=0, collector_cpu_ns=0,
                                                        max_wall_ns=0, max_collector_cpu_ns=0))
                stats['count'] += 1
                stats['wall_ns'] += event['wall_ns']
                stats['collector_cpu_ns'] += event['cpu_ns']
                stats['max_wall_ns'] = max(stats['max_wall_ns'], event['wall_ns'])
                stats['max_collector_cpu_ns'] = max(stats['max_collector_cpu_ns'], event['cpu_ns'])
                self.count += 1
                self.events.append(event)
            else:
                self.errors += 1
        except Exception:
            self.errors += 1

    def begin(self, record):
        record['native_begin_cycles'] = self.reader()

    def pump_end(self, record):
        end = self.reader()
        record['native_end_cycles'] = end
        record['native_cycles'] = end - record['native_begin_cycles']
        if record['native_cycles'] < 0:
            self.errors += 1
        else:
            self.native_count += 1
            self.native_total += record['native_cycles']

    def end(self, record):
        value = record['native_cycles']
        stats = self.native_groups.setdefault(record['group'], dict(count=0, cycles=0, min_cycles=None, max_cycles=0))
        stats['count'] += 1
        stats['cycles'] += value
        stats['min_cycles'] = value if stats['min_cycles'] is None else min(value, stats['min_cycles'])
        stats['max_cycles'] = max(stats['max_cycles'], value)

    def close(self):
        if self.closed:
            return
        # Remove only our own callback; never clear another observer's hooks.
        try:
            gc.callbacks.remove(self._callback)
        except ValueError:
            self.errors += 1
        self._callback = None  # Break our own bound-method reference cycle.
        self.detached_ns = time.monotonic_ns()
        self.closed = True

    def result(self):
        return dict(schema_version=1, closed=self.closed, errors=self.errors,
                    attached_ns=self.attached_ns, detached_ns=self.detached_ns,
                    backend=self.backend, counter_scope='emulated ADSP cycles of card.cpu only; not host CPU time',
                    native_quanta=self.native_count, native_cycles=self.native_total,
                    native_groups=self.native_groups,
                    gc_count=self.count, gc_capacity=self.events.maxlen,
                    gc_dropped=self.count - len(self.events), gc_groups=self.groups,
                    gc_unfinished=self.pending, gc_events=list(self.events),
                    previous_gc_callbacks=self.previous_callbacks)


def valid_receipt(value, expected_quanta):
    return (value.get('schema_version') == 1 and value.get('closed') is True
            and value.get('errors') == 0 and value.get('gc_unfinished') is None
            and value.get('native_quanta') == expected_quanta
            and type(value.get('native_cycles')) is int and value['native_cycles'] >= 0
            and sum(g['count'] for g in value.get('native_groups', {}).values()) == expected_quanta
            and sum(g['cycles'] for g in value.get('native_groups', {}).values()) == value['native_cycles']
            and value.get('gc_count') == sum(g['count'] for g in value.get('gc_groups', {}).values())
            and value.get('gc_count') == len(value.get('gc_events', [])) + value.get('gc_dropped', -1))


def overlap_events(record, detail):
    """Return raw overlapping events, never prorate or subtract collector CPU."""
    return [e for e in detail.get('gc_events', [])
            if e['begin_ns'] < record['end_ns'] and record['begin_ns'] < e['end_ns']]
