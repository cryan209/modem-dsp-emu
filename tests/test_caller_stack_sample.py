"""Portable parser/ownership tests; never attach to a real process in CI."""
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import caller_stack_sample as stacks
from qualify_v34 import parser

REPORT = '''Process: Python [123]
Report Version: 7
Call graph:
    10 Thread_1 DispatchQueue_1: com.apple.main-thread (serial)
      10 Py_BytesMain  (in Python) + 4
        4 execute  (in libadsp2181.dylib) + 8
        3 take_gil  (in Python) + 1
        + 3 __psynch_cvwait  (in libsystem_kernel.dylib) + 2
        2 _PyEval_EvalFrameDefault  (in Python) + 6
    6 Thread_2 wire
      6 _select  (in libsystem_kernel.dylib) + 4

Total number in stack (recursive counted multiple, when >=5):
Binary Images:
'''


def test_exclusive_counts_preserve_parent_pc_samples_without_double_counting():
    r = stacks.parse_report(REPORT, 123)
    a, b = r['threads']
    assert a['main'] and not b['main']
    assert a['categories'] == {'adsp_native_leaf': 4, 'gil_path': 3, 'python_runtime_leaf': 3}
    assert b['categories'] == {'selector_leaf': 6}
    assert sum(p['count'] for p in a['exclusive_paths']) == a['samples'] == 10
    assert 'not CPU time' in r['scope']


@pytest.mark.parametrize('old,new', [
    ('[123]', '[124]'), ('Report Version: 7', 'Report Version: 8'),
    ('Binary Images:', ''), ('Total number in stack', 'Missing footer'),
    ('com.apple.main-thread', 'unknown'), ('4 execute', '40 execute'),
    ('      10 Py_BytesMain', '       10 Py_BytesMain'),
    ('        4 execute', 'garbled 4 execute'), ('10 Thread_1', '0 Thread_1'),
])
def test_incomplete_malformed_or_wrong_target_reports_fail_closed(old, new):
    with pytest.raises(ValueError):
        stacks.parse_report(REPORT.replace(old, new), 123)


def test_duplicate_pid_and_ambiguous_main_are_rejected():
    for text in ['Process: Python [123]\n' + REPORT, REPORT.replace('Thread_2 wire', 'Thread_2 com.apple.main-thread')]:
        with pytest.raises(ValueError): stacks.parse_report(text, 123)


def test_bounds_are_enforced_without_silent_truncation(monkeypatch):
    monkeypatch.setattr(stacks, 'MAX_BYTES', 40)
    with pytest.raises(ValueError, match='bound'): stacks.parse_report(REPORT, 123)
    monkeypatch.setattr(stacks, 'MAX_BYTES', 10000)
    monkeypatch.setattr(stacks, 'MAX_NODES', 2)
    with pytest.raises(ValueError, match='nodes'): stacks.parse_report(REPORT, 123)


@pytest.mark.parametrize('path,expected', [
    (['execute  (in libadsp2181.dylib)', '_PyEval_EvalFrameDefault  (in Python)'], 'python_runtime_leaf'),
    (['Py_BytesMain  (in Python)', 'execute  (in libadsp2181.dylib)'], 'adsp_native_leaf'),
    (['take_gil  (in Python)', '__psynch_cvwait  (in libsystem_kernel.dylib)'], 'gil_path'),
    (['PyThread_acquire_lock_timed  (in Python)', '__ulock_wait  (in libsystem_kernel.dylib)'], 'python_lock_path'),
    (['__semwait_signal  (in libsystem_kernel.dylib)'], 'wait_or_sleep_leaf'),
    (['write  (in libsystem_kernel.dylib)'], 'io_syscall_leaf'),
    (['ffi_call  (in libffi.dylib)'], 'ctypes_ffi_leaf'),
    (['malloc  (in libsystem_malloc.dylib)'], 'allocator_leaf'),
    (['unknown (in unknown)'], 'other_leaf'),
])
def test_categories_describe_pc_or_path_not_ancestor_native_cpu(path, expected):
    assert stacks.category(path) == expected


def setup_sampler(tmp_path, monkeypatch):
    target = Mock(pid=123); target.poll.return_value = None
    child = Mock(); child.poll.return_value = None; child.returncode = None
    spawn = Mock(return_value=child)
    monkeypatch.setattr(stacks.subprocess, 'Popen', spawn)
    sample = stacks.CallerStackSample(target, tmp_path, 2, 1024)
    receiver = SimpleNamespace(verified_bytes=1024, size=4096)
    return sample, receiver, target, child, spawn


def test_sampling_uses_owned_popen_pid_and_progress_then_requires_post_sample_progress(tmp_path, monkeypatch):
    sample, rx, target, child, spawn = setup_sampler(tmp_path, monkeypatch)
    with sample:
        rx.verified_bytes = 960; sample.on_progress(rx); spawn.assert_not_called()
        rx.verified_bytes = 1024; sample.on_progress(rx)
        assert spawn.call_args.args[0] == ['/usr/bin/sample', '123', '2', '10', '-mayDie', '-file', str(sample.report_path)]
        sample.report_path.write_text(REPORT)
        child.poll.return_value = child.returncode = 0
        rx.verified_bytes = 1536; sample.on_progress(rx)
        rx.verified_bytes = 4096
        assert sample.finish(rx)['application_passed']
    target.terminate.assert_not_called(); target.kill.assert_not_called()
    child.terminate.assert_not_called()


@pytest.mark.parametrize('failure', ['tool_exit', 'partial', 'target_exit', 'no_progress', 'unfinished', 'never_started'])
def test_missing_application_evidence_fails_and_cleanup_never_signals_target(tmp_path, monkeypatch, failure):
    sample, rx, target, child, spawn = setup_sampler(tmp_path, monkeypatch)
    with sample:
        if failure != 'never_started':
            sample.on_progress(rx)
            sample.report_path.write_text(REPORT if failure != 'partial' else REPORT[:50])
            if failure != 'unfinished':
                child.poll.return_value = child.returncode = 1 if failure == 'tool_exit' else 0
            else:
                child.returncode = -15
            if failure == 'target_exit': target.poll.return_value = 0
            if failure != 'unfinished':
                rx.verified_bytes = 1536; sample.on_progress(rx)
        rx.verified_bytes = 1536 if failure == 'no_progress' else 4096
        assert not sample.finish(rx)['application_passed']
    target.terminate.assert_not_called(); target.kill.assert_not_called()
    if failure == 'unfinished': child.terminate.assert_called_once()


@pytest.mark.parametrize('exception', [RuntimeError, KeyboardInterrupt])
def test_exception_during_observation_cleans_up_sampler_only(tmp_path, monkeypatch, exception):
    sample, rx, target, child, spawn = setup_sampler(tmp_path, monkeypatch)
    child.returncode = -15
    with pytest.raises(exception, match='controller'):
        with sample:
            sample.on_progress(rx)
            raise exception('controller error')
    child.terminate.assert_called_once()
    target.terminate.assert_not_called()
    assert sample.handle is None


def test_stuck_sampler_is_killed_without_signaling_caller(tmp_path, monkeypatch):
    sample, rx, target, child, spawn = setup_sampler(tmp_path, monkeypatch)
    child.returncode = -9
    child.wait.side_effect = [stacks.subprocess.TimeoutExpired('sample', 5), 0]
    with sample:
        sample.on_progress(rx)
        assert not sample.finish(rx)['application_passed']
    child.terminate.assert_called_once(); child.kill.assert_called_once()
    target.terminate.assert_not_called(); target.kill.assert_not_called()


def test_sampler_spawn_error_closes_log_and_never_signals_caller(tmp_path, monkeypatch):
    sample, rx, target, child, spawn = setup_sampler(tmp_path, monkeypatch)
    spawn.side_effect = OSError('spawn failed')
    with pytest.raises(OSError, match='spawn failed'):
        with sample:
            sample.on_progress(rx)
    assert sample.handle is None
    target.terminate.assert_not_called(); target.kill.assert_not_called()


def test_existing_evidence_is_not_reused(tmp_path):
    (tmp_path / 'caller-stack-sample.txt').write_text('keep')
    with pytest.raises(ValueError): stacks.CallerStackSample(Mock(pid=123), tmp_path, 2, 1024)


@pytest.mark.parametrize('arguments', [
    ['--caller-stack-sample-seconds', '-1'], ['--caller-stack-sample-seconds', '6'],
    ['--caller-stack-sample-seconds', '2', '--sample-after-bytes', '1'],
    ['--caller-stack-sample-seconds', '2', '--sample-after-bytes', '4096'],
    ['--caller-stack-sample-seconds', '2', '--producer-pause-ms', '100'],
])
def test_invalid_sampling_flags_reject_before_start(monkeypatch, arguments):
    monkeypatch.setattr(stacks, 'available', lambda: True)
    monkeypatch.setattr(sys, 'argv', ['qualify_v34.py', *arguments])
    with pytest.raises(SystemExit) as exc: parser()
    assert exc.value.code == 2


def test_explicit_unsupported_platform_errors_not_hidden_skip(monkeypatch):
    monkeypatch.setattr(stacks, 'available', lambda: False)
    monkeypatch.setattr(sys, 'argv', ['qualify_v34.py', '--caller-stack-sample-seconds', '2'])
    with pytest.raises(SystemExit) as exc: parser()
    assert exc.value.code == 2


def test_sampler_flag_implies_profile_and_trace(monkeypatch):
    monkeypatch.setattr(stacks, 'available', lambda: True)
    monkeypatch.setattr(sys, 'argv', ['qualify_v34.py', '--caller-stack-sample-seconds', '2'])
    args = parser()
    assert args.producer_profile and args.media_trace and args.sample_after_bytes == 1024
