"""Bounded observations, not GC-policy changes or scheduling benchmarks."""
import ctypes
import gc
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import weakref

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import producer_detail as module
from producer_detail import ProducerDetail, native_reader, overlap_events, valid_receipt
from producer_profile import PHASES, ProducerProfile, summarize, valid_receipt as valid_profile
import qualify_v34


@pytest.fixture
def clock(monkeypatch):
    value = [0, 0]
    monkeypatch.setattr(module.time, 'monotonic_ns', lambda: value[0])
    monkeypatch.setattr(module.time, 'thread_time_ns', lambda: value[1])
    return value


@pytest.fixture
def observers(monkeypatch):
    # Replace the Python-visible list for deterministic manual callbacks.
    # No observer is attached to CPython's actual internal callback list here.
    callbacks = []
    monkeypatch.setattr(module.gc, 'callbacks', callbacks)
    opened = []
    def create(values=(100, 400), capacity=512):
        reader = iter(values)
        detail = ProducerDetail(lambda: next(reader), 'fake-card', capacity)
        opened.append(detail)
        return detail
    yield create
    for detail in opened:
        detail.close()
    assert not callbacks


def emit(detail, phase, generation=0):
    detail._observe_gc(phase, dict(generation=generation, collected=4, uncollectable=0))


def make_call():
    return SimpleNamespace(samples=80000, trn_progress=0x00d0, rx=[1] * 320,
        tx_queue=[b'output'], next_send=1., next_tick=1., rx_hold_until=None,
        rx_retry_at=0., tick_count=0)


def finish(profile, call):
    for phase in PHASES[:-1]:
        profile.mark(phase)
    call.samples += 160
    call.tick_count += 1
    profile.end(call)


def test_native_reader_uses_full_width_pointer_and_unsigned_counter():
    class Function:
        def __call__(self, cpu):
            assert cpu == 0x123456789abcdef0
            return 2**40 + 7
    function = Function()
    reader = native_reader(SimpleNamespace(adsp2181_cycles=function),
                           SimpleNamespace(cpu=0x123456789abcdef0))
    assert function.argtypes == [ctypes.c_void_p] and function.restype is ctypes.c_uint64
    assert reader() == 2**40 + 7


@pytest.mark.parametrize('card', [SimpleNamespace(), SimpleNamespace(cpu=None), SimpleNamespace(cpu=0)])
def test_missing_live_cpu_is_not_fabricated_as_zero_work(card):
    with pytest.raises(ValueError, match='live card.cpu'):
        native_reader(SimpleNamespace(), card)


def test_two_counter_reads_per_quantum_and_gc_not_prorated_or_subtracted(tmp_path, clock, observers):
    detail = observers()  # exactly two counter values; no per-sample reads
    profile = ProducerProfile(tmp_path / 'profile.json', detail=detail)
    call = make_call()
    profile.begin(call)
    clock[:] = [5_000_000, 4_000_000]
    emit(detail, 'start')
    clock[:] = [35_000_000, 24_000_000]
    emit(detail, 'stop')
    clock[:] = [40_000_000, 29_000_000]
    finish(profile, call)
    profile.close(call)
    value = profile.result()
    assert valid_profile(value) and valid_receipt(value['detail'], 1)
    record, = value['records']
    assert record['native_cycles'] == 300 and record['cpu_ns'] == 29_000_000
    assert record['native_begin_cycles'] == 100 and record['native_end_cycles'] == 400
    assert detail.result()['native_groups']['data'] == dict(count=1, cycles=300, min_cycles=300, max_cycles=300)
    summary = summarize(value)
    slow, = summary['detail']['slow_quanta']
    event, = slow['gc_overlaps']
    assert event['cpu_ns'] == 20_000_000 and event['main_thread']
    assert slow['cpu_ns'] == 29_000_000  # never subtract GC to waive a budget


def test_native_counter_regression_invalidates_receipt(tmp_path, clock, observers):
    detail = observers(values=(400, 100))
    profile = ProducerProfile(tmp_path / 'profile.json', detail=detail)
    call = make_call()
    profile.begin(call)
    clock[:] = [30_000_000, 20_000_000]
    finish(profile, call)
    profile.close(call)
    assert profile.result()['records'][0]['native_cycles'] == -300
    assert not valid_profile(profile.result())


@pytest.mark.parametrize('generation', [0, 1, 2])
def test_gc_generation_cpu_wall_and_collection_counts(clock, observers, generation):
    detail = observers()
    emit(detail, 'start', generation)
    clock[:] = [50_000_000, 20_000_000]
    emit(detail, 'stop', generation)
    detail.close()
    value = detail.result()
    assert valid_receipt(value, 0)
    event, = value['gc_events']
    assert event['wall_ns'] == 50_000_000 and event['cpu_ns'] == 20_000_000
    assert event['collected'] == 4 and event['uncollectable'] == 0
    assert value['gc_groups'][f'main:g{generation}']['count'] == 1


def test_gc_retention_is_bounded_but_aggregates_cover_every_collection(clock, observers):
    detail = observers(capacity=2)
    for _ in range(5):
        emit(detail, 'start')
        clock[0] += 10
        clock[1] += 8
        emit(detail, 'stop')
    detail.close()
    value = detail.result()
    assert valid_receipt(value, 0)
    assert value['gc_count'] == 5 and value['gc_dropped'] == 3 and len(value['gc_events']) == 2
    assert value['gc_groups']['main:g0']['collector_cpu_ns'] == 40


@pytest.mark.parametrize('failure', ['orphan_stop', 'nested_start', 'wrong_generation', 'malformed', 'unknown_phase', 'clock_regression'])
def test_callback_faults_are_receipt_failures_not_silently_ignored(clock, observers, failure):
    detail = observers()
    if failure == 'orphan_stop':
        emit(detail, 'stop')
    elif failure == 'nested_start':
        emit(detail, 'start'); emit(detail, 'start')
    elif failure == 'wrong_generation':
        emit(detail, 'start'); emit(detail, 'stop', 1)
    elif failure == 'malformed':
        detail._observe_gc('start', {})
    elif failure == 'unknown_phase':
        emit(detail, 'unknown')
    else:
        emit(detail, 'start'); clock[0] -= 1; emit(detail, 'stop')
    detail.close()
    assert detail.result()['errors'] > 0
    assert not valid_receipt(detail.result(), 0)


def test_unfinished_gc_invalidates_closed_receipt(clock, observers):
    detail = observers()
    emit(detail, 'start')
    detail.close()
    assert not valid_receipt(detail.result(), 0)


def test_close_preserves_other_callbacks_and_breaks_own_reference_cycle(clock, observers):
    other = lambda *args: None
    module.gc.callbacks.append(other)
    detail = observers()
    reference = weakref.ref(detail)
    detail.close(); detail.close()
    assert module.gc.callbacks == [other] and detail._callback is None
    module.gc.callbacks.remove(other)
    # The factory intentionally holds the observer; no additional callback cycle.
    assert reference() is detail


def test_removed_observer_cannot_claim_complete_coverage(clock, observers):
    detail = observers()
    module.gc.callbacks.remove(detail._callback)
    detail.close()
    assert detail.result()['errors'] == 1
    assert not valid_receipt(detail.result(), 0)


def test_partial_overlap_reports_raw_event_without_inventing_main_cpu_share(clock, observers):
    detail = observers()
    emit(detail, 'start')
    clock[:] = [30, 20]
    emit(detail, 'stop')
    detail.close()
    event, = overlap_events(dict(begin_ns=10, end_ns=20), detail.result())
    assert event['cpu_ns'] == 20 and event['wall_ns'] == 30
    assert not overlap_events(dict(begin_ns=30, end_ns=40), detail.result())


def test_real_other_thread_collection_preserves_policy_and_detaches():
    enabled, threshold, callbacks = gc.isenabled(), gc.get_threshold(), list(gc.callbacks)
    detail = ProducerDetail(lambda: 0, 'test-real-gc')
    try:
        thread = threading.Thread(target=gc.collect)
        thread.start(); thread.join(timeout=5)
        assert not thread.is_alive()
    finally:
        detail.close()
    assert any(not e['main_thread'] for e in detail.result()['gc_events'])
    assert gc.isenabled() == enabled and gc.get_threshold() == threshold
    assert gc.callbacks == callbacks
    assert valid_receipt(detail.result(), 0)


@pytest.mark.parametrize('full_history', [False, True])
def test_qualifier_full_sport_history_is_explicit_opt_in(tmp_path, monkeypatch, full_history):
    argv = ['qualify_v34.py', '--output', str(tmp_path / 'new')]
    if full_history:
        argv.append('--analog-sport-history')
    monkeypatch.setattr(sys, 'argv', argv)
    assert qualify_v34.parser().analog_sport_history is full_history


def test_qualifier_detail_implies_profile_and_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['qualify_v34.py', '--producer-detail', '--output', str(tmp_path / 'new')])
    args = qualify_v34.parser()
    assert args.producer_detail and args.producer_profile and args.media_trace
    assert not valid_receipt({}, 1)
