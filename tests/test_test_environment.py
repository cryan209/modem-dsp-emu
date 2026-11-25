"""The full-suite preflight must fail safely when native JIT access fails."""
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import check_test_environment as environment


def test_native_probe_success(capsys):
    result = subprocess.CompletedProcess([], 0, '', '')
    with patch.object(environment.subprocess, 'run', return_value=result) as run:
        assert environment.main() == 0
    assert run.call_args.args[0][0] == sys.executable
    assert run.call_args.kwargs['timeout'] == 15
    assert 'PASS' in capsys.readouterr().out


def test_native_probe_signal_is_reported_without_crashing_suite(capsys):
    result = subprocess.CompletedProcess([], -4, '', '')
    with patch.object(environment.subprocess, 'run', return_value=result):
        assert environment.main() == 1
    assert 'signal 4' in capsys.readouterr().err


def test_native_probe_import_failure_is_reported(capsys):
    result = subprocess.CompletedProcess([], 1, '', 'ModuleNotFoundError: unicorn')
    with patch.object(environment.subprocess, 'run', return_value=result):
        assert environment.main() == 1
    assert 'ModuleNotFoundError: unicorn' in capsys.readouterr().err


def test_native_probe_timeout_is_bounded(capsys):
    with patch.object(environment.subprocess, 'run',
                      side_effect=subprocess.TimeoutExpired('probe', 15)):
        assert environment.main() == 1
    assert 'timed out' in capsys.readouterr().err
