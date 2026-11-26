"""Deterministic CPU versus wall timing; no host scheduling thresholds."""
import copy
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import producer_profile as module
from producer_profile import PHASES, ProducerProfile, summarize, valid_receipt
import qualify_v34


@pytest.fixture
def clock(monkeypatch):
    now = [0, 0]
    monkeypatch.setattr(module.time, 'monotonic_ns', lambda: now[0])
    monkeypatch.setattr(module.time, 'thread_time_ns', lambda: now[1])
    return now


def call():
    return SimpleNamespace(samples=80000, trn_progress=0x00d0, rx=[1] * 320,
                           tx_queue=[b'output'], next_send=1, next_tick=1,
                           rx_hold_until=None, rx_retry_at=0, tick_count=0)


def quantum(profile, c, clock, wall=30_000_000, cpu=4_000_000, phase='sample_pump', end_state=None):
    profile.begin(c)
    for name in PHASES[:-1]:
        if name == phase:
            clock[0] += wall
            clock[1] += cpu
        profile.mark(name)
    if phase == 'bookkeeping':
        clock[0] += wall
        clock[1] += cpu
    c.samples += 160
    c.tick_count += 1
    if end_state is not None:
        c.trn_progress = end_state
    profile.end(c)


@pytest.mark.parametrize('wall,cpu,gap', [(20_000_000, 20_000_000, 0),
                                         (50_000_000, 4_000_000, 46_000_000),
                                         (10, 12, -2)])
def test_raw_cpu_wall_and_signed_gap_are_not_confused_or_clamped(tmp_path, clock, wall, cpu, gap):
    p = ProducerProfile(tmp_path / 'profile.json')
    c = call()
    quantum(p, c, clock, wall, cpu)
    p.close(c)
    result = p.result()
    assert valid_receipt(result, 1)
    record = result['records'][0]
    assert (record['wall_ns'], record['cpu_ns'], record['non_cpu_ns']) == (wall, cpu, gap)
    assert record['phases']['sample_pump']['non_cpu_ns'] == gap
    assert result['groups']['data']['phases']['total']['negative_gap_samples'] == (gap < 0)


@pytest.mark.parametrize('phase', PHASES)
def test_phase_partition_covers_total_without_double_counting(tmp_path, clock, phase):
    p = ProducerProfile(tmp_path / 'profile.json')
    c = call()
    quantum(p, c, clock, phase=phase)
    p.close(c)
    record = p.result()['records'][0]
    assert record['phases'][phase]['wall_ns'] == record['wall_ns'] == 30_000_000
    assert sum(v['cpu_ns'] for v in record['phases'].values()) == record['cpu_ns']
    assert valid_receipt(p.result())


def test_retention_is_bounded_but_group_aggregates_cover_all_quanta(tmp_path, clock):
    p = ProducerProfile(tmp_path / 'profile.json', capacity=2, slow_capacity=1)
    c = call()
    c.trn_progress = 0x00cc
    quantum(p, c, clock, end_state=0x00d0)  # entering data is a transition
    for _ in range(4):
        quantum(p, c, clock)
    assert not p.path.exists()
    assert not valid_receipt(p.result())
    p.close(c)
    p.dump()
    value = json.loads(p.path.read_text())
    assert valid_receipt(value, 5)
    assert len(value['records']) == 2 and value['dropped_records'] == 3
    assert len(value['slow_records']) == 1 and value['dropped_slow_records'] == 4
    assert value['groups']['training_or_transition']['count'] == 1
    assert value['groups']['data']['count'] == 4
    assert value['groups']['data']['phases']['total']['wall_ns'] == 120_000_000


def test_incomplete_quantum_cannot_pass_closed_receipt(tmp_path, clock):
    p = ProducerProfile(tmp_path / 'profile.json')
    c = call()
    quantum(p, c, clock)
    p.begin(c)
    p.close(c)
    assert not valid_receipt(p.result())
    assert p.result()['unfinished_quantum'] is not None


def test_thread_clock_cannot_accidentally_measure_the_wire_thread(tmp_path, clock):
    p = ProducerProfile(tmp_path / 'profile.json')
    p.begin(call())
    errors = []
    def worker():
        try:
            p.mark('sample_pump')
        except RuntimeError as exc:
            errors.append(str(exc))
    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert errors == ['producer profile crossed threads']


def test_first_measurement_cannot_start_on_wire_thread(tmp_path, clock):
    p = ProducerProfile(tmp_path / 'profile.json')
    errors = []
    def worker():
        try:
            p.begin(call())
        except RuntimeError as exc:
            errors.append(str(exc))
    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=5)
    assert errors == ['producer profile must start on main producer thread']
    assert not thread.is_alive() and p.active is None


@pytest.mark.parametrize('which', [0, 1])
def test_regressing_clock_does_not_produce_plausible_metrics(tmp_path, clock, which):
    p = ProducerProfile(tmp_path / 'profile.json')
    p.begin(call())
    clock[which] -= 1
    with pytest.raises(RuntimeError, match='clock regressed'):
        p.mark('sample_pump')


def test_phase_order_and_overlap_are_rejected(tmp_path, clock):
    p = ProducerProfile(tmp_path / 'profile.json')
    p.begin(call())
    with pytest.raises(RuntimeError, match='overlapping'):
        p.begin(call())
    with pytest.raises(RuntimeError, match='out-of-order'):
        p.mark('diagnostics')


@pytest.mark.parametrize('mutation', ['missing_phase', 'wrong_sum', 'wrong_expected', 'missing_records', 'not_closed'])
def test_missing_or_inconsistent_profile_evidence_fails_closed(tmp_path, clock, mutation):
    p = ProducerProfile(tmp_path / 'profile.json')
    c = call()
    quantum(p, c, clock)
    p.close(c)
    value = copy.deepcopy(p.result())
    if mutation == 'missing_phase':
        del value['groups']['data']['phases']['link']
    elif mutation == 'wrong_sum':
        value['groups']['data']['count'] += 1
    elif mutation == 'wrong_expected':
        value['expected_quanta'] += 1
    elif mutation == 'missing_records':
        value['records'] = []
    elif mutation == 'not_closed':
        value['closed'] = False
    assert not valid_receipt(value)
    assert not valid_receipt({})


def test_correlate_retained_quantum_cpu_not_cpu_at_incident_instant(tmp_path, clock):
    p = ProducerProfile(tmp_path / 'profile.json', capacity=1, slow_capacity=1)
    c = call()
    quantum(p, c, clock)  # slow retained separately even after main ring loses it
    quantum(p, c, clock, wall=10_000_000, cpu=5_000_000)
    p.close(c)
    trace = dict(dropped_events=3, dropped_incidents=2, underruns=[
        dict(time=.01, rx_wait_started=None, producer_active=True),
        dict(time=.1, rx_wait_started=.09, producer_active=False)])
    result = summarize(p.result(), trace)
    assert result['valid'] and result['trace_dropped_events'] == 3
    matched, outside = result['incident_quanta']
    assert matched['quantum']['sequence'] == 0
    assert matched['quantum']['cpu_ns'] == 4_000_000
    assert outside['quantum'] is None  # absence is not evidence of idle CPU


def test_between_quantum_pause_is_not_mislabelled_as_dsp_work(tmp_path, clock):
    p = ProducerProfile(tmp_path / 'profile.json')
    c = call()
    quantum(p, c, clock, wall=10_000_000, cpu=5_000_000)
    clock[0] += 100_000_000  # dispatch pause outside measured quanta
    quantum(p, c, clock, wall=10_000_000, cpu=5_000_000)
    p.close(c)
    total = p.result()['groups']['data']['phases']['total']
    assert total['wall_ns'] == 20_000_000 and total['cpu_ns'] == 10_000_000
    assert p.result()['records'][1]['begin_ns'] == 110_000_000
    interval = p.result()['records'][1]['between']
    assert interval['wall_ns'] == 100_000_000 and interval['cpu_ns'] == 0
    assert p.result()['between_groups']['data']['non_cpu_ns'] == 100_000_000
    trace = dict(underruns=[dict(time=.05, rx_wait_started=None, producer_active=False)])
    incident, = summarize(p.result(), trace)['incident_quanta']
    assert incident['quantum'] is None and incident['between_quanta']['wall_ns'] == 100_000_000


def test_long_between_intervals_are_bounded_but_aggregate_counts_are_complete(tmp_path, clock):
    p = ProducerProfile(tmp_path / 'profile.json', capacity=1, slow_capacity=1)
    c = call()
    for _ in range(4):
        clock[0] += 30_000_000
        quantum(p, c, clock, wall=10_000_000, cpu=5_000_000)
    p.close(c)
    value = p.result()
    assert valid_receipt(value)
    assert len(value['long_between_records']) == 1 and value['dropped_long_between'] == 2
    assert value['between_groups']['data']['count'] == 3
    assert value['between_groups']['data']['wall_ns'] == 90_000_000


@pytest.mark.parametrize('which', [0, 1])
def test_between_quantum_clock_regression_is_not_hidden(tmp_path, clock, which):
    p = ProducerProfile(tmp_path / 'profile.json')
    c = call()
    quantum(p, c, clock)
    clock[which] -= 1
    with pytest.raises(RuntimeError, match='regressed between quanta'):
        p.begin(c)


def test_qualifier_profile_is_opt_in_and_implies_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['qualify_v34.py', '--producer-profile', '--output', str(tmp_path / 'new')])
    args = qualify_v34.parser()
    assert args.producer_profile and args.media_trace
    monkeypatch.setattr(sys, 'argv', ['qualify_v34.py', '--output', str(tmp_path / 'new')])
    args = qualify_v34.parser()
    assert not args.producer_profile and not args.media_trace
