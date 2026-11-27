"""Portable benchmark contract tests; native firmware replay is tested separately."""
from copy import deepcopy
from pathlib import Path
import struct
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from benchmark_sport_rx import digest, equivalent, stimulus


@pytest.mark.parametrize('count', [0, 1, 4, 160, 4096])
def test_stimulus_is_deterministic_unsigned_words_with_extremes(count):
    words = stimulus(count)
    assert len(words) == count and words == stimulus(count)
    assert all(0 <= w <= 65535 for w in words)
    assert words[:4] == [0, 0x7fff, 0x8000, 0xffff][:count]


def test_digest_is_binary_exact_not_float_equality():
    assert digest([0.0], '>d') != digest([-0.0], '>d')
    assert digest([1.0], '>d') == digest([struct.unpack('>d', struct.pack('>d', 1.0))[0]], '>d')


@pytest.mark.parametrize('field', ['outputs', 'output_sha256', 'state'])
def test_equivalence_requires_every_proof_field(field):
    a = dict(outputs=160, output_sha256='exact', state=dict(cycles=5), cpu_ns=100)
    b = deepcopy(a); b[field] = None
    assert not equivalent(a, b)
    b = deepcopy(a); b['cpu_ns'] = 200
    assert equivalent(a, b)


@pytest.mark.parametrize('arguments', [['--samples', '0'], ['--samples', '1000001'],
                                      ['--pairs', '0'], ['--pairs', '21'], ['--codec-rate', '1']])
def test_invalid_cli_never_starts_native_replay(tmp_path, arguments):
    output = tmp_path / 'new.json'
    result = subprocess.run([sys.executable, str(ROOT / 'tools/benchmark_sport_rx.py'),
                             '--output', str(output), *arguments], capture_output=True, text=True)
    assert result.returncode == 2 and not output.exists()


def test_existing_output_is_never_overwritten(tmp_path):
    output = tmp_path / 'old.json'; output.write_text('retain')
    result = subprocess.run([sys.executable, str(ROOT / 'tools/benchmark_sport_rx.py'),
                             '--output', str(output)], capture_output=True, text=True)
    assert result.returncode == 2 and output.read_text() == 'retain'
