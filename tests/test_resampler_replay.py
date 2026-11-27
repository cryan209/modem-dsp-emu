"""Scalar FIR changes must preserve binary64 operations, not just SNR."""
import json
from pathlib import Path
import struct
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from analog_kernel_dispatch import RationalResampler
from benchmark_resampler import ReferenceResampler, batch, stimulus


def bits(values):
    return b''.join(struct.pack('>d', value) for value in values)


@pytest.mark.parametrize('ratio', [(6, 5), (5, 6), (1, 1), (2, 7), (7, 2), (3, 4)])
@pytest.mark.parametrize('taps', [4, 16, 64])
@pytest.mark.parametrize('offset', [-97, 0, 23])
@pytest.mark.parametrize('cutoff', [0.8, 1.0])
def test_every_output_and_stream_state_bit_exact(ratio, taps, offset, cutoff):
    config = dict(taps_per_phase=taps, phase_offset=offset, cutoff_multiplier=cutoff)
    reference = ReferenceResampler(*ratio, **config)
    candidate = RationalResampler(*ratio, **config)
    values = [0.0, -0.0, 32767.0, -32768.0, 1e-200, -1e-200] + stimulus(257)
    for sample in values:
        assert bits(candidate.push(sample)) == bits(reference.push(sample))
        assert candidate.consumed == reference.consumed
        assert candidate.produced == reference.produced
        assert bits(candidate.history) == bits(reference.history)
    assert bits(candidate.h) == bits(reference.h)


def test_public_coefficient_edits_are_not_hidden_by_a_stale_phase_cache():
    a, b = ReferenceResampler(6, 5), RationalResampler(6, 5)
    for index, sample in enumerate(stimulus(1000)):
        if index == 200:
            a.h[0] = b.h[0] = -0.125
        if index == 500:
            del a.h[-7:]; del b.h[-7:]
        assert bits(a.push(sample)) == bits(b.push(sample))


def test_nonfinite_input_keeps_same_ieee_operation_order():
    for ratio in ((6, 5), (5, 6)):
        a, b = ReferenceResampler(*ratio), RationalResampler(*ratio)
        for value in [float('inf'), float('-inf'), float('nan'), -0.0] + stimulus(200):
            assert bits(a.push(value)) == bits(b.push(value))


def test_round_trip_batch_hash_and_state_match_without_per_sample_timers():
    a, b = batch(ReferenceResampler, stimulus(1000)), batch(RationalResampler, stimulus(1000))
    assert all(a[key] == b[key] for key in ('outputs', 'sha256', 'state'))
    assert a['cpu_ns'] > 0 and b['wall_ns'] > 0


def test_benchmark_cli_preserves_results_and_refuses_reused_output(tmp_path):
    path = tmp_path / 'new' / 'batch.json'
    cmd = [sys.executable, 'tools/benchmark_resampler.py', '--samples', '160', '--pairs', '2', '--output', str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(path.read_text())
    assert receipt['bit_exact'] and len(receipt['observations']) == 2
    assert receipt['observations'][0]['order'] == ['reference', 'optimized']
    assert receipt['observations'][1]['order'] == ['optimized', 'reference']
    original = path.read_bytes()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    assert result.returncode == 2 and path.read_bytes() == original
