"""Guarded native-facing RX decision and bounded receipt regressions."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from data_rx_resume import DataRxResume, valid_receipt, configuration_supported
from rx_recovery_probe import endpoint
from qualify_v34 import endpoint_result, parser


def setup():
    ep, c = endpoint(0, 'data', Path('not-written'))
    ep.call = c
    ep.experimental_data_rx_resume = True
    ep.ppp = SimpleNamespace(peer=SimpleNamespace(up=True))
    c.card = SimpleNamespace(dm=[0] * 0x4000, lapm=SimpleNamespace(data_ready=True))
    c.card.dm[0x3fc2] = 0xd0
    c.rx.clear(); c.rx.extend(range(160)); c.rx_hold_until = 100.5
    c.data_rx_resume = DataRxResume()
    return ep, c


def test_current_state_not_cached_progress_or_buffer_promotion_controls_resume():
    ep, c = setup()
    ep.data_mode = False; c.trn_progress = 0x80
    before = c.next_send, c.next_tick, list(c.rx), c.samples, c.hold_time
    assert ep.rx_ready(c, 100)
    assert (c.next_send, c.next_tick, list(c.rx), c.samples, c.hold_time) == before
    assert c.rx_hold_until is None and c.data_rx_resume.resumes == 1
    assert c.data_rx_resume.events[0]['current_trn'] == 0xd0


@pytest.mark.parametrize('state', [0, 0x80, 0xc0, 0xcc, 0xd1, 0xffff])
def test_cached_data_state_never_overrides_current_training_or_departure(state):
    ep, c = setup(); c.trn_progress = 0xd0; ep.data_mode = True
    c.card.dm[0x3fc2] = state
    assert not ep.rx_ready(c, 100)
    assert c.data_rx_resume.resumes == 0
    assert c.data_rx_resume.reasons == {'not_data': 1}
    assert len(c.rx) == 160 and c.next_send == c.next_tick == 100


@pytest.mark.parametrize('link', ['ppp', 'lapm', 'missing_ppp', 'missing_card'])
def test_current_link_must_be_established(link):
    ep, c = setup()
    if link == 'ppp': ep.ppp.peer.up = False
    if link == 'lapm': c.card.lapm.data_ready = False
    if link == 'missing_ppp': ep.ppp = None
    if link == 'missing_card': c.card = None
    assert not ep.rx_ready(c, 100)
    assert c.data_rx_resume.resumes == 0 and len(c.rx) == 160


@pytest.mark.parametrize('size', [0, 1, 159, 320])
def test_partial_or_already_prefilled_input_never_counts_as_experimental_use(size):
    ep, c = setup(); c.rx.clear(); c.rx.extend(range(size))
    assert ep.rx_ready(c, 100) == (size == 320)
    assert c.data_rx_resume.attempts == 0 and len(c.rx) == size


@pytest.mark.parametrize('guard', ['disabled', 'startup', 'not_held', 'other_call', 'non_realtime'])
def test_no_default_startup_or_unheld_policy_change(guard):
    ep, c = setup()
    if guard == 'disabled': ep.experimental_data_rx_resume = False
    if guard == 'startup': c.packets = 0
    if guard == 'not_held': c.rx_hold_until = None
    if guard == 'other_call': ep.call = SimpleNamespace()
    if guard == 'non_realtime': ep.realtime = False
    assert ep.rx_ready(c, 100) == (guard == 'not_held')
    assert c.data_rx_resume.attempts == 0


def test_data_training_data_transition_rechecks_every_candidate_and_new_call_is_clean():
    ep, c = setup()
    assert ep.rx_ready(c, 100)
    c.rx_hold_until = 100.6; c.card.dm[0x3fc2] = 0x80
    assert not ep.rx_ready(c, 100.1)
    c.card.dm[0x3fc2] = 0xd0; ep.ppp.peer.up = False
    assert not ep.rx_ready(c, 100.2)
    ep.ppp.peer.up = True
    assert ep.rx_ready(c, 100.3)
    assert c.data_rx_resume.resumes == 2
    _, fresh = setup()
    assert fresh.data_rx_resume.attempts == 0


def test_bounded_receipt_closes_and_counts_all_decisions(tmp_path):
    ep, c = setup(); c.data_rx_resume = DataRxResume(capacity=2)
    for i in range(10):
        c.rx_hold_until = 100.5
        ep.ppp.peer.up = i % 2 == 0
        ep.rx_ready(c, 100)
    path = tmp_path / 'missing' / 'nested' / 'receipt.json'; c.data_rx_resume.dump(path)
    receipt = json.loads(path.read_text())
    assert valid_receipt(receipt, 5)
    assert receipt['attempts'] == 10
    assert receipt['dropped_events'] == receipt['dropped_denials'] == 3
    with pytest.raises(RuntimeError, match='closed'):
        c.data_rx_resume.decide(c, 100, current_trn=0xd0, ppp_up=True, lapm_ready=True)


@pytest.mark.parametrize('field,value', [('closed', False), ('attempts', 99),
    ('resumes', 0), ('dropped_events', 9), ('reasons', {'resumed': 2}), ('events', []),
    ('capacity', 0)])
def test_missing_or_inconsistent_receipts_fail_closed(tmp_path, field, value):
    ep, c = setup(); ep.rx_ready(c, 100)
    c.data_rx_resume.dump(tmp_path / 'closed.json')
    receipt = deepcopy(c.data_rx_resume.result()); receipt[field] = value
    assert not valid_receipt(receipt, 1)
    assert not valid_receipt(c.data_rx_resume.result(), None)
    assert not valid_receipt({}, 0)


def test_corrupted_allowed_guard_cannot_validate(tmp_path):
    ep, c = setup(); ep.rx_ready(c, 100); c.data_rx_resume.dump(tmp_path / 'closed.json')
    receipt = c.data_rx_resume.result(); receipt['events'][0]['current_trn'] = 0xcc
    assert not valid_receipt(receipt, 1)


def test_zero_use_receipt_is_complete_but_not_evidence_policy_was_exercised(tmp_path):
    ep, c = setup(); c.data_rx_resume.dump(tmp_path / 'closed.json')
    assert valid_receipt(c.data_rx_resume.result(), 0)
    assert endpoint_result('[media] experimental data RX resume: 0 resumes')['data_rx_resumes'] == 0
    assert endpoint_result('')['data_rx_resumes'] is None
    assert endpoint_result('[media] experimental data RX resume: 0 resumes\n' * 2)['data_rx_resumes'] is None


def test_qualifier_flag_implies_trace_and_records_experiment(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'argv', ['qualify_v34.py', '--experimental-data-rx-resume', '--output', str(tmp_path / 'new')])
    args = parser()
    assert args.experimental_data_rx_resume and args.media_trace


@pytest.mark.parametrize('setting', ['valid', 'ppp', 'media_trace_dir', 'bind', 'realtime', 'native_mips', 'bri_kernel_dispatch', 'mode'])
def test_full_endpoint_configuration_uses_environment_modulation_not_nonexistent_argument(setting):
    args = SimpleNamespace(ppp=True, ppp_client=False, media_trace_dir=Path('new'),
        bind='127.0.0.1', realtime=True, native_mips=False, bri_kernel_dispatch=False)
    env = {'EICON_MODULATION': 'v34,0,19200,19200,19200,19200'}
    changes = dict(ppp=False, media_trace_dir=None, bind='0.0.0.0', realtime=False,
                   native_mips=True, bri_kernel_dispatch=True)
    if setting in changes: setattr(args, setting, changes[setting])
    if setting == 'mode': env['EICON_MODULATION'] = 'v90'
    assert configuration_supported(args, env) == (setting == 'valid')


def test_endpoint_refuses_experiment_without_local_realtime_v34_ppp_trace():
    command = [sys.executable, 'tools/eicon_adsp_sip.py', '--experimental-data-rx-resume']
    result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert result.returncode == 2
    assert 'requires localhost real-time V.34 PPP' in result.stderr
