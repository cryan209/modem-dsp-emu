"""Owned-child faults and data-phase receipts; no firmware or external peers."""
import json
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import qualification_faults as faults
import qualify_v34
from v34_payload import HEADER, MAGIC, Receiver, payload


class FakeProcess:
    pid = 12345
    returncode = None

    def __init__(self):
        self.signals = []

    def poll(self):
        return self.returncode

    def send_signal(self, sig):
        self.signals.append(sig)


@pytest.fixture
def clock(monkeypatch):
    now = [0]
    monkeypatch.setattr(time, 'monotonic_ns', lambda: now[0])
    monkeypatch.setattr(time, 'monotonic', lambda: now[0] / 1e9)
    monkeypatch.setattr(time, 'time_ns', lambda: 1_700_000_000_000_000_000 + now[0])
    monkeypatch.setattr(time, 'sleep', lambda seconds: now.__setitem__(0, now[0] + round(seconds * 1e9)))
    return now


def stopped_status():
    return (signal.SIGSTOP << 8) | 0x7f


def receipt():
    return dict(requested_ms=100, trigger_after_bytes=1024, attempted=True,
                verified_bytes_at_stop=1024, verified_bytes_at_continue=1088)


def test_observed_stop_and_continue_have_exact_monotonic_receipt(clock, monkeypatch):
    child = FakeProcess()
    monkeypatch.setattr(faults.os, 'waitpid', lambda pid, flags: (pid, stopped_status()))
    value = receipt()
    previous = signal.getsignal(signal.SIGTERM)
    faults.pause_owned_process(child, 100, value)
    assert child.signals == [signal.SIGSTOP, signal.SIGCONT]
    assert signal.getsignal(signal.SIGTERM) == previous
    assert value['observed_hold_ns'] == 100_000_000
    assert faults.pause_result(value, 4096)


@pytest.mark.parametrize('status,expected', [(17 << 8, 17), (signal.SIGKILL, -signal.SIGKILL)])
def test_exited_child_status_is_not_reaped_and_fabricated_as_zero(clock, monkeypatch, status, expected):
    child = FakeProcess()
    monkeypatch.setattr(faults.os, 'waitpid', lambda pid, flags: (pid, status))
    with pytest.raises(RuntimeError, match='exited during injection'):
        faults.pause_owned_process(child, 100, {})
    assert child.returncode == expected
    assert child.signals == [signal.SIGSTOP]


def test_unobserved_stop_times_out_but_still_continues_child(clock, monkeypatch):
    child = FakeProcess()
    monkeypatch.setattr(faults.os, 'waitpid', lambda pid, flags: (0, 0))
    value = receipt()
    with pytest.raises(TimeoutError, match='SIGSTOP was not observed'):
        faults.pause_owned_process(child, 100, value)
    assert child.signals == [signal.SIGSTOP, signal.SIGCONT]
    assert not faults.pause_result(value, 4096)


def test_interrupt_during_stop_publication_cannot_lose_continuation(clock, monkeypatch):
    child = FakeProcess()

    def send(sig):
        child.signals.append(sig)
        if sig == signal.SIGSTOP:
            raise KeyboardInterrupt

    monkeypatch.setattr(child, 'send_signal', send)
    with pytest.raises(KeyboardInterrupt):
        faults.pause_owned_process(child, 100, {})
    assert child.signals == [signal.SIGSTOP, signal.SIGCONT]


@pytest.mark.parametrize('interrupt', [None, 'keyboard', 'term_handler'])
def test_real_owned_child_is_resumed_before_cleanup_even_on_interrupt(monkeypatch, interrupt):
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    value = dict(requested_ms=5)
    previous = signal.getsignal(signal.SIGTERM)
    real_sleep = time.sleep

    def sleep(seconds):
        if seconds == .005 and interrupt:
            if interrupt == 'term_handler':
                handler = signal.getsignal(signal.SIGTERM)
                assert callable(handler)
                handler(signal.SIGTERM, None)  # no real SIGTERM to pytest itself
            raise KeyboardInterrupt
        real_sleep(seconds)

    monkeypatch.setattr(time, 'sleep', sleep)
    try:
        if interrupt:
            with pytest.raises(KeyboardInterrupt):
                faults.pause_owned_process(child, 5, value)
        else:
            faults.pause_owned_process(child, 5, value)
            assert value['hold_completed']
            assert value['observed_hold_ns'] >= 5_000_000
        assert value['stop_observed'] and value['continue_sent']
        assert signal.getsignal(signal.SIGTERM) == previous
        child.terminate()
        # A still-stopped child cannot act on SIGTERM. This bounds cleanup,
        # not a millisecond host-scheduling performance assertion.
        assert child.wait(timeout=5) == -signal.SIGTERM
    finally:
        child.send_signal(signal.SIGCONT)
        child.kill()
        child.wait(timeout=5)


@pytest.mark.parametrize('field,value', [
    ('stop_observed', False), ('hold_completed', False), ('continue_sent', False),
    ('observed_hold_ns', 99_999_999), ('verified_bytes_at_stop', 0),
    ('verified_bytes_at_stop', 4096), ('verified_bytes_at_continue', 4096),
])
def test_unapplied_or_unrecovered_fault_cannot_pass_receipt(field, value):
    record = dict(receipt(), stop_observed=True, hold_completed=True,
                  continue_sent=True, observed_hold_ns=100_000_000)
    record[field] = value
    assert not faults.pause_result(record, 4096)


def test_data_hook_is_one_shot_and_not_triggered_before_verified_progress(tmp_path, monkeypatch):
    record = dict(requested_ms=100, trigger_after_bytes=1024, attempted=False)
    receiver = SimpleNamespace(verified_bytes=960, size=4096)
    calls = []

    def inject(process, ms, value):
        calls.append((process, ms))
        receiver.verified_bytes = 1088

    monkeypatch.setattr(faults, 'pause_owned_process', inject)
    path = tmp_path / 'pause.json'
    faults.pause_on_progress('owned-child', receiver, record, path)
    assert not path.exists() and not calls
    receiver.verified_bytes = 1024
    faults.pause_on_progress('owned-child', receiver, record, path)
    receiver.verified_bytes = 2048
    faults.pause_on_progress('owned-child', receiver, record, path)
    assert calls == [('owned-child', 100)]
    saved = json.loads(path.read_text())
    assert saved['verified_bytes_at_stop'] == 1024
    assert saved['verified_bytes_at_continue'] == 1088


def test_missed_data_window_is_not_silently_treated_as_injected(tmp_path, monkeypatch):
    monkeypatch.setattr(faults, 'pause_owned_process', lambda *_: pytest.fail('must not pause after data'))
    record = dict(requested_ms=100, trigger_after_bytes=1024, attempted=False)
    with pytest.raises(RuntimeError, match='missed the active data window'):
        faults.pause_on_progress(None, SimpleNamespace(verified_bytes=4096, size=4096),
                                 record, tmp_path / 'pause.json')
    assert not faults.pause_result(record, 4096)


def test_receiver_progress_excludes_duplicates_and_corrupt_bytes():
    receiver = Receiver(bytes(16), 65)
    def packet(seq):
        return HEADER.pack(MAGIC, bytes(16), seq) + payload(bytes(16), b'up', seq, 65)
    assert receiver.verified_bytes == 0
    receiver.accept(packet(0))
    assert receiver.verified_bytes == 64
    receiver.accept(packet(0))
    receiver.accept(packet(1)[:-1] + b'bad')
    assert receiver.verified_bytes == 64
    receiver.accept(packet(1))
    assert receiver.verified_bytes == receiver.result()['bytes'] == 65
    assert receiver.errors  # corruption remains sticky even after valid retry


def test_pause_hook_consumes_original_deadline_without_forgiveness(clock):
    predicates = []
    def predicate():
        predicates.append(True)
        return len(predicates) > 1
    def hook():
        clock[0] += 2_000_000_000
    with pytest.raises(TimeoutError):
        qualify_v34.wait_for(predicate, [FakeProcess()], 1, 'test', on_poll=hook)
    assert len(predicates) == 1


@pytest.mark.parametrize('options', [
    ['--caller-pause-ms', 'nan'], ['--caller-pause-ms', 'inf'],
    ['--caller-pause-ms', '-1'], ['--caller-pause-ms', '.5'],
    ['--caller-pause-ms', '1001'],
    ['--caller-pause-ms', '100', '--pause-after-bytes', '0'],
    ['--caller-pause-ms', '100', '--pause-after-bytes', '65'],
    ['--caller-pause-ms', '100', '--pause-after-bytes', '4032'],
    ['--caller-pause-ms', '100', '--bytes', '128'],
])
def test_invalid_pause_arguments_fail_before_creating_children(monkeypatch, options):
    monkeypatch.setattr(sys, 'argv', ['qualify_v34.py', *options])
    with pytest.raises(SystemExit) as exc:
        qualify_v34.parser()
    assert exc.value.code == 2
