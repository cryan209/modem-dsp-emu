"""Phase-controlled scheduling model, never real-host latency assertions."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from eicon_adsp_sip import EiconSipEndpoint
import rx_recovery_probe as model


@pytest.mark.parametrize('phase', ['training', 'data'])
def test_production_holds_one_real_packet_until_full_prefill(phase):
    ep, c = model.endpoint(0, phase, Path('not-written'))
    c.rx.clear()
    before = c.next_tick, c.next_send
    assert not ep.rx_ready(c, 100)
    c.rx.extend(model.Packet(1, 0).samples())
    assert len(c.rx) == 160
    assert not ep.rx_ready(c, 100.010)
    assert not ep.data_mode  # Actual qualifier does not promote the data buffer.
    c.rx.extend(model.Packet(1, 1).samples())
    assert ep.rx_ready(c, 100.030)
    assert c.rx_hold_until is None
    assert (c.next_tick, c.next_send) == before
    assert len(c.rx) == 320 and c.rx_substituted == 0


@pytest.mark.parametrize('phase', ['training', 'data'])
def test_startup_prefill_is_distinct_from_continuing_on_one_packet(phase):
    ep, c = model.endpoint(0, phase, Path('not-written'))
    c.rx = c.rx.__class__(model.Packet(1, 0).samples())
    assert ep.rx_ready(c, 100)  # Already-started, not held.
    c.packets = 0
    assert not ep.rx_ready(c, 100)  # Startup still requires 320.
    assert c.rx_substituted == 0


@pytest.mark.parametrize('available', [0, 1, 159])
def test_even_one_packet_reference_cannot_consume_partial_input(available):
    ep, c = model.endpoint(0, 'data', Path('not-written'))
    c.rx.clear(); c.rx.extend(range(available))
    ep.rx_prefill_samples = 160
    assert not ep.rx_ready(c, 100)
    assert len(c.rx) == available and c.samples == 80000


@pytest.mark.parametrize('costs', [(0, 0), (12, 9), (17, 17)])
@pytest.mark.parametrize('pause', [0, 20, 100])
def test_reference_never_changes_training_results(costs, pause):
    actual = model.run_case(phase='training', costs=costs, pause_ms=pause)
    reference = model.run_case(phase='training', costs=costs, pause_ms=pause, reference=True)
    assert actual['peers'] == reference['peers']
    assert actual['pause'] == reference['pause']
    assert actual['invariants_passed'] and actual['observation_complete']


@pytest.mark.parametrize('phase', ['training', 'data'])
@pytest.mark.parametrize('reference', [False, True])
def test_unstalled_and_jitter_controls_meet_model_budgets(phase, reference):
    row = model.run_case(phase=phase, reference=reference, delays=(2, 10, 4, 8))
    assert row['invariants_passed'] and row['observation_complete']
    assert all(p['model_budget_met'] for p in row['peers'])
    assert all(p['max_gap_ms'] <= 21 for p in row['peers'])
    assert sum(p['sends'] for p in row['peers']) == sum(p['arrived_packets'] for p in row['peers']) + len(row['in_transit_packets'])
    for peer in row['peers']:
        assert peer['initial_rx_samples'] + peer['arrived_packets'] * 160 == peer['consumed_identity_samples'] + peer['pending_rx_samples']
        assert peer['completed_quanta'] + 1 == peer['sends'] + peer['queued_tx_packets']


def test_data_reference_reduces_peer_delay_but_does_not_hide_injected_failure():
    actual = model.run_case(pause_ms=100)
    reference = model.run_case(pause_ms=100, reference=True)
    assert actual['pause'] == reference['pause'] == dict(begin_ms=112, end_ms=212)
    a, b = actual['peers']; ra, rb = reference['peers']
    assert (a['tx_underruns'], a['max_gap_ms']) == (ra['tx_underruns'], ra['max_gap_ms']) == (4, 104)
    assert (b['tx_underruns'], b['max_gap_ms']) == (2, 74)
    assert (rb['tx_underruns'], rb['max_gap_ms']) == (1, 58)
    assert b['holds'][0]['full_quantum_ms'] == rb['holds'][0]['full_quantum_ms'] == 229
    assert b['holds'][0]['resumed_ms'] == 245
    assert rb['holds'][0]['resumed_ms'] == 229
    assert b['holds'][0]['full_quantum_to_output_ms'] == 25
    assert rb['holds'][0]['full_quantum_to_output_ms'] == 9
    # Earlier resume is not a timing qualification or a free jitter margin.
    assert len(rb['holds']) > len(b['holds'])
    assert not b['model_budget_met'] and not rb['model_budget_met']
    assert actual['invariants_passed'] and reference['invariants_passed']


def test_right_censored_holds_and_unsent_tail_are_not_lost():
    row = model.run_case(pause_ms=100)
    peer = row['peers'][1]
    last = peer['holds'][-1]
    assert last['right_censored'] and last['resumed_ms'] is None
    assert last['output_right_censored'] and last['full_quantum_to_output_ms'] is None
    assert last['full_quantum_wait_ms'] == row['duration_ms'] - last['full_quantum_ms']
    assert peer['tail_without_send_ms'] == row['duration_ms'] - peer['send_times_ms'][-1]


@pytest.mark.parametrize('pause', [20, 100])
def test_two_ms_eligibility_boundary_does_not_force_a_pause_or_edit_deadlines(pause):
    row = model.run_case(costs=(18, 18), pause_ms=pause)
    assert row['pause'] is None and not row['observation_complete']
    assert row['invariants_passed']
    assert all(p['tx_underruns'] == 0 for p in row['peers'])


def test_unapplied_or_unfinished_pause_cannot_claim_complete_observation():
    row = model.run_case(pause_ms=100, request_ms=390)
    assert not row['observation_complete']
    assert row['invariants_passed']  # Separate from fault-application evidence.


def test_arrival_reordering_is_recorded_not_sorted_away():
    row = model.run_case(costs=(0, 0), pause_ms=100, delays=(19, 0))
    assert not row['invariants_passed']
    assert any(f['kind'] == 'arrival_reorder' for f in row['failures'])


def test_timeout_fallback_is_recorded_without_fabricating_samples():
    row = model.run_case(pause_ms=1000, duration_ms=900)
    assert not row['invariants_passed']
    assert any(f['kind'] == 'would_substitute_rx' for f in row['failures'])
    assert not row['observation_complete']


def test_reference_restores_prefill_even_when_production_gate_raises(monkeypatch):
    endpoints = []
    original = model.endpoint
    def create(*args):
        pair = original(*args); endpoints.append(pair[0]); return pair
    monkeypatch.setattr(model, 'endpoint', create)
    def fail(*args):
        raise RuntimeError('injected gate error')
    monkeypatch.setattr(EiconSipEndpoint, 'rx_ready', fail)
    with pytest.raises(RuntimeError, match='injected gate error'):
        model.run_case(reference=True)
    assert all(ep.rx_prefill_samples == 320 for ep in endpoints)


@pytest.mark.parametrize('kwargs', [dict(phase='unknown'), dict(costs=(1,)),
    dict(costs=(-1, 0)), dict(costs=(1.5, 0)), dict(delays=()),
    dict(delays=(-1,)), dict(delays=(0, 20)), dict(duration_ms=0),
    dict(duration_ms=2001), dict(pause_ms=-1)])
def test_invalid_model_parameters_are_rejected(kwargs):
    with pytest.raises(ValueError):
        model.run_case(**kwargs)


def test_fixed_suite_retains_negative_cases_without_claiming_qualification():
    result = model.suite()
    assert len(result['cases']) == 40
    assert result['observations_complete'] and result['invariants_passed']
    assert 'passed' not in result and 'simulation only' in result['scope']
    assert any(not p['model_budget_met'] for r in result['cases'] for p in r['peers'])


def test_cli_is_reproducible_under_optimization_and_refuses_overwrite(tmp_path):
    output = tmp_path / 'new.json'
    command = [sys.executable, '-O', str(Path(model.__file__)), '--output', str(output)]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0, completed.stderr
    value = json.loads(output.read_text())
    assert value['observations_complete'] and value['invariants_passed']
    original = output.read_bytes()
    repeated = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert repeated.returncode == 2 and output.read_bytes() == original
