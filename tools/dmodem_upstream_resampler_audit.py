#!/usr/bin/env python3
"""What d-modem's 6:5 linear interpolation costs the upstream (Session 249).

`d-modem.c:558` asserts that "simple linear interpolation is adequate" for the
`DSP 9600 -> net 8000` direction. It is not: it is the whole of the ~20 dB that
bounded the upstream rate at 14,400 from Session 244c onward.

Run it against the taps d-modem writes during a call, copied off the tower:

    ssh root@tower.net.cryan.nz 'docker exec d-modem cat /tmp/dm_from_dsp_9600.raw' > dm9600.raw
    ssh root@tower.net.cryan.nz 'docker exec d-modem cat /tmp/dm_from_dsp.raw'      > dm8000.raw
    /tmp/eicon-venv/bin/python tools/dmodem_upstream_resampler_audit.py dm9600.raw dm8000.raw

The control comes first and must pass, or nothing below means anything: this
file's reimplementation of `d-modem.c:583-590` has to reproduce d-modem's own
post-interpolation tap from its pre-interpolation one. It does, to integer
truncation (`max |diff| 0.8`).

Then three numbers. The broadband error (14.3 dB) overstates the damage, and
the in-band error (14.4 dB) still does, because a fixed droop is linear and
time-invariant and the card's 54-tap LMS equalizer removes exactly that
(Session 244). The honest figure is what survives the best LTI fit -- 19.5 dB --
because a 6:5 interpolator is *not* time-invariant: its response depends on
which of five fractional phases each output sample lands on.

`--rebuild` writes `rebuilt_linear.rx.ulaw` and `rebuilt_sinc.rx.ulaw` from the
same 9600 Hz source for `tools/v90_rx_reference_demod.py`. That is the direct
test, and it is what closed the question: 19.7 dB against 37.6 dB, same samples,
same receiver, same window, one resampler apart.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

DSP_RATE = 9600.0
NET_RATE = 8000.0
# The V.34 upstream here is 3200 baud on a 1829 Hz carrier, so this is the band
# the card's down-mixer keeps. Error outside it would be filtered before the
# slicer; 96% of this error is inside it.
BAND = (229.0, 3429.0)


def dmodem_linear(raw: np.ndarray, out_n: int) -> np.ndarray:
    """`d-modem.c:583-590`, exactly: two-point linear interpolation at 6:5."""
    k = np.arange(out_n)
    num = k * 6
    i = num // 5
    frac = num % 5
    nxt = np.minimum(i + 1, len(raw) - 1)
    return (raw[i] * (5 - frac) + raw[nxt] * frac) / 5.0


def sinc_resample(raw: np.ndarray, out_n: int, taps: int = 513,
                  cutoff: float = 3700.0) -> np.ndarray:
    """The same output instants, taken with a windowed sinc instead.

    Only five distinct fractional phases exist at 6:5, so this is five kernels
    rather than a filter design problem.
    """
    pos = np.arange(out_n) * (DSP_RATE / NET_RATE)
    base = np.floor(pos).astype(int)
    frac = pos - base
    half = taps // 2
    offsets = np.arange(-half, half + 1)
    out = np.zeros(out_n)
    fc = cutoff / DSP_RATE
    for phase in np.unique(frac):
        sel = frac == phase
        h = 2 * fc * np.sinc(2 * fc * (offsets - phase)) * np.kaiser(taps, 8.0)
        h /= h.sum()
        idx = np.clip(base[sel][:, None] + offsets[None, :], 0, len(raw) - 1)
        out[sel] = raw[idx] @ h
    return out


def band_power(x: np.ndarray, lo: float, hi: float) -> tuple[float, float]:
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    freqs = np.fft.rfftfreq(len(x), 1.0 / NET_RATE)
    mask = (freqs >= lo) & (freqs <= hi)
    return spectrum[mask].sum(), spectrum.sum()


def lti_residual_snr(linear: np.ndarray, reference: np.ndarray,
                     taps: int = 129) -> float:
    """SNR after the best FIR fit -- what an equalizer cannot take out."""
    half = taps // 2
    idx = np.clip(np.arange(len(linear))[:, None]
                  + np.arange(-half, half + 1)[None, :], 0, len(linear) - 1)
    design = linear[idx]
    coefficients, *_ = np.linalg.lstsq(design, reference, rcond=None)
    residual = design @ coefficients - reference
    return 10 * np.log10((reference ** 2).sum() / (residual ** 2).sum())


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('pre', type=Path, help='/tmp/dm_from_dsp_9600.raw, int16')
    ap.add_argument('post', type=Path, help='/tmp/dm_from_dsp.raw, int16')
    ap.add_argument('--windows', default='60:70,100:110,150:160',
                    help='comma-separated FROM:TO seconds on the 8 kHz stream')
    ap.add_argument('--rebuild', action='store_true',
                    help='write rebuilt_{linear,sinc}.rx.ulaw for the '
                         'reference demodulator')
    args = ap.parse_args()

    dsp = np.fromfile(args.pre, dtype='<i2').astype(np.float64)
    net = np.fromfile(args.post, dtype='<i2').astype(np.float64)
    print(f'[audit] pre {len(dsp) / DSP_RATE:.1f}s at {DSP_RATE:.0f} Hz, '
          f'post {len(net) / NET_RATE:.1f}s at {NET_RATE:.0f} Hz')

    linear = dmodem_linear(dsp, len(net))
    span = min(len(linear), len(net))
    drift = linear[:span] - net[:span]
    print(f'[control] reimplementation vs d-modem\'s own post tap: '
          f'max |diff| {np.abs(drift).max():.1f}, rms {drift.std():.3f} '
          f'-- integer truncation only, so the model is the shipped code')
    if np.abs(drift).max() > 2.0:
        print('[control] FAILED: this is not the interpolation d-modem ran; '
              'nothing below is about the shipped code')
        return 1

    reference = sinc_resample(dsp, len(net))
    for window in args.windows.split(','):
        start, end = (float(value) for value in window.split(':'))
        lo, hi = int(start * NET_RATE), int(end * NET_RATE)
        if hi > len(net):
            print(f'{start:6.0f}..{end:.0f}s  beyond the capture, skipped')
            continue
        ref, lin = reference[lo:hi], linear[lo:hi]
        error = lin - ref
        broadband = 10 * np.log10((ref ** 2).sum() / (error ** 2).sum())
        sig_band, _ = band_power(ref, *BAND)
        err_band, err_all = band_power(error, *BAND)
        print(f'{start:6.0f}..{end:.0f}s  broadband {broadband:5.1f} dB   '
              f'in band {10 * np.log10(sig_band / err_band):5.1f} dB '
              f'({100 * err_band / err_all:4.1f}% of the error is in band)   '
              f'after best LTI fit {lti_residual_snr(lin, ref):5.1f} dB')

    if args.rebuild:
        from v90_rx_reference_demod import mulaw_encode
        for name, signal in (('linear', linear), ('sinc', reference)):
            path = Path(f'rebuilt_{name}.rx.ulaw')
            clipped = np.clip(signal, -32768, 32767).astype(np.int16)
            path.write_bytes(mulaw_encode(clipped))
            print(f'[rebuild] {path} ({len(clipped) / NET_RATE:.1f}s)')
        print('[rebuild] now demodulate both with v90_rx_reference_demod.py '
              'over one window: 19.7 dB against 37.6 dB')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
