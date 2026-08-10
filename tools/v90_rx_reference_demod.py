#!/usr/bin/env python3
"""Independently demodulate a recorded .rx.ulaw and measure its usable SNR.

Session 244 traced the whole card-side receive chain -- kernel publication,
phase splitter, AGC, down-mixer, 8 kHz -> 9.6 kHz resampler, and the 54-tap
adaptive equalizer at PM 0x0b4e -- and found no broken stage: the ring never
underruns, levels keep headroom, and the equalizer converges.  Yet the settled
slicer residual sits around 21 dB below the constellation, which is exactly the
SNR that selects 14,400 upstream.

That leaves one question the firmware trace cannot answer from inside: is 21 dB
what the recording actually contains?  This tool answers it by demodulating the
same capture with a completely separate float64 receiver -- its own carrier
estimate, its own resampler, its own fractionally spaced equalizer and carrier
tracking -- and reporting the SNR it can reach.  If the independent receiver
also floors around 21 dB, the wire is the limit and the card is doing as well as
the recording allows.  If it reaches far more, the loss is inside the emulated
receive path.

The answer on run76 is the first: over the TRN window 12.7..14.6 s this receiver
reaches 19.6 dB where the card's own slicer reaches 20.5 dB, against a mu-law
quantisation ceiling of 37.1 dB.  The card is extracting everything the
recording holds, and about a dB more than an independent receiver does.  The
4,578-corner-scaled residuals are 388 here and 336 on the card.  The upstream
rate ceiling is a property of the captured signal, not of the receive chain.

The receiver is deliberately batch and deterministic: matched filter, best of
the three sampling phases, then repeated closed-form least-squares solves
against the current decisions with a smoothed carrier-phase estimate between
them.  An online CMA/decision-directed loop was tried first and could not
demodulate its own clean control signal; nothing here depends on a step size.

`--synthetic` is that control, and is not optional in practice: it replaces the
capture with a clean 4-point signal at the same symbol rate, carrier and level,
passed through the same mu-law quantiser, and runs the identical receiver over
it.  A reference receiver that cannot demodulate its own clean signal proves
nothing about a real one.  It currently reaches 36.3 dB against a 35.9 dB
quantisation ceiling, and `--synthetic-noise-db` tracks injected noise to within
a dB (30 -> 28.9, 25 -> 25.5, 20 -> 21.0), which is what makes a measured 19.6
dB on the capture believable.

Two bugs found by that control are worth remembering, because both looked
exactly like a bad channel: shaping the synthetic symbols onto the 2.5-sample
8 kHz grid instead of the 3-sample 9.6 kHz one, and a mu-law encoder using
floor(log2(magnitude / 33)) for the segment exponent instead of the position of
the top set bit less five.  The second cost 15 dB on its own.

    /tmp/eicon-venv/bin/python tools/v90_rx_reference_demod.py --synthetic
    /tmp/eicon-venv/bin/python tools/v90_rx_reference_demod.py \\
        artifacts/eicon-native-tower/run76.rx.ulaw --from 12.7 --to 14.6

The default window is the V.34 TRN era of run76, where the card's own slicer is
working against the four corners (+/-4578, +/-4578) and where its residual was
measured.  Both receivers therefore see the same seconds of the same signal.

Needs numpy only; there is no scipy in the venv, so the resampler, the filters
and the analytic transform are all built here from FFTs and windowed sincs.
"""
from __future__ import annotations

import argparse
import math

import numpy as np

SAMPLE_RATE = 8000.0
SYMBOL_RATE = 3200.0
SAMPLES_PER_SYMBOL = 3            # the rate the card resamples to, 9,600 Hz
EXCESS_BANDWIDTH = 0.12           # V.34 roll-off at 3,200 baud
CARD_CORNER = 4578.0              # the firmware's own TRN constellation corner


def mulaw_decode(octets: bytes) -> np.ndarray:
    """G.711 mu-law to linear, in the 16-bit domain the card's kernel sees."""
    u = np.frombuffer(octets, dtype=np.uint8).astype(np.int32) ^ 0xFF
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    magnitude = ((mantissa << 1) + 33) << exponent
    magnitude -= 33
    value = np.where(sign, -magnitude, magnitude).astype(np.float64)
    return value * 4.0


def mulaw_encode(x: np.ndarray) -> bytes:
    """The inverse, for the synthetic control: quantise exactly as a codec."""
    v = np.clip(np.round(x / 4.0), -8159, 8159).astype(np.int32)
    sign = (v < 0).astype(np.int32)
    magnitude = np.abs(v) + 33
    # The exponent is the position of the top set bit less five -- NOT
    # log2(magnitude / 33).  The two differ whenever log2(magnitude) lands just
    # above an integer, which is about 4% of samples, and there the mantissa
    # overflows its four bits and wraps to a wildly wrong code.  That bug cost
    # the codec 15 dB and made the synthetic control look like a bad channel.
    exponent = np.clip(
        np.floor(np.log2(np.maximum(magnitude, 1))).astype(np.int32) - 5, 0, 7)
    mantissa = (magnitude >> (exponent + 1)) & 0x0F
    u = (sign << 7) | (exponent << 4) | mantissa
    return bytes(((u ^ 0xFF) & 0xFF).astype(np.uint8))


def quantisation_ceiling_db(x: np.ndarray) -> float:
    """The best SNR any receiver could get from this mu-law recording.

    Each decoded sample sits in a mu-law interval whose width is fixed by its
    own segment, so the quantisation noise power is the mean of step^2/12 over
    the actual samples -- no assumption about the signal is needed beyond the
    codec itself.
    """
    levels = np.sort(np.unique(mulaw_decode(bytes(range(256)))))
    steps = np.diff(levels)
    index = np.clip(np.searchsorted(levels, x) - 1, 0, len(steps) - 1)
    noise = np.mean(steps[index] ** 2) / 12.0
    power = float(np.mean(x ** 2))
    if power <= 0.0 or noise <= 0.0:
        return float('-inf')     # a silent window has no ceiling to report
    return 10.0 * math.log10(power / noise)


def analytic(x: np.ndarray) -> np.ndarray:
    """Hilbert analytic signal, FFT form (no scipy in the venv)."""
    n = len(x)
    spectrum = np.fft.fft(x)
    weight = np.zeros(n)
    weight[0] = 1.0
    if n % 2 == 0:
        weight[1:n // 2] = 2.0
        weight[n // 2] = 1.0
    else:
        weight[1:(n + 1) // 2] = 2.0
    return np.fft.ifft(spectrum * weight)


def lowpass(x: np.ndarray, cutoff_hz: float, rate: float,
            taps: int = 129) -> np.ndarray:
    """Windowed-sinc low pass, applied with 'same' convolution."""
    n = np.arange(taps) - (taps - 1) / 2.0
    h = 2.0 * cutoff_hz / rate * np.sinc(2.0 * cutoff_hz / rate * n)
    h *= np.blackman(taps)
    h /= h.sum()
    return np.convolve(x, h.astype(x.dtype), mode='same')


def resample_fft(x: np.ndarray, up: int, down: int) -> np.ndarray:
    """Rational resample of one finite block by spectrum resizing.

    The blocks here are seconds long and only the settled middle is measured,
    so the circular-convolution edges cost nothing and this avoids designing a
    polyphase bank by hand.
    """
    n = len(x)
    m = int(round(n * up / down))
    spectrum = np.fft.fft(x)
    if m > n:
        half = n // 2
        grown = np.zeros(m, dtype=complex)
        grown[:half] = spectrum[:half]
        grown[m - (n - half):] = spectrum[half:]
        spectrum = grown
    else:
        half = m // 2
        spectrum = np.concatenate((spectrum[:half],
                                   spectrum[n - (m - half):]))
    return np.fft.ifft(spectrum) * (m / n)


def estimate_carrier(x: np.ndarray, rate: float) -> float:
    """Band centre from the power spectrum over the voice band."""
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    freqs = np.fft.rfftfreq(len(x), 1.0 / rate)
    band = (freqs > 300.0) & (freqs < 3600.0)
    return float((freqs[band] * spectrum[band]).sum() / spectrum[band].sum())


def refine_carrier(baseband: np.ndarray, rate: float) -> float:
    """Residual offset from the fourth-power line of a 4-point constellation."""
    quartic = baseband ** 4
    spectrum = np.abs(np.fft.fft(quartic * np.hanning(len(quartic))))
    freqs = np.fft.fftfreq(len(quartic), 1.0 / rate)
    return float(freqs[int(np.argmax(spectrum))] / 4.0)


def root_raised_cosine(sps: int, span: int, beta: float) -> np.ndarray:
    """Unit-energy RRC pulse, with both removable singularities filled in."""
    t = np.arange(-span * sps, span * sps + 1, dtype=np.float64) / sps
    pulse = np.empty_like(t)
    for i, ti in enumerate(t):
        if abs(ti) < 1e-9:
            pulse[i] = 1.0 - beta + 4.0 * beta / math.pi
        elif abs(abs(4.0 * beta * ti) - 1.0) < 1e-9:
            pulse[i] = beta / math.sqrt(2.0) * (
                (1.0 + 2.0 / math.pi) * math.sin(math.pi / (4.0 * beta))
                + (1.0 - 2.0 / math.pi) * math.cos(math.pi / (4.0 * beta)))
        else:
            pulse[i] = ((math.sin(math.pi * ti * (1.0 - beta))
                         + 4.0 * beta * ti
                         * math.cos(math.pi * ti * (1.0 + beta)))
                        / (math.pi * ti * (1.0 - (4.0 * beta * ti) ** 2)))
    return pulse / math.sqrt(np.sum(pulse ** 2))


def derotate(y: np.ndarray) -> np.ndarray:
    """Remove the constant phase of a 4-point constellation.

    For corners at +/-a +/- ja the fourth power sits at angle pi, not 0, so the
    naive angle(mean(y**4))/4 rotates the constellation onto the axes and then
    a sign slicer reports nonsense.  Subtract that pi first.
    """
    return y * np.exp(-1j * (np.angle(np.mean(y ** 4)) - math.pi) / 4.0)


def smooth_phase(y: np.ndarray, decisions: np.ndarray,
                 span: int) -> np.ndarray:
    """Slowly varying carrier phase, from the decision-directed error."""
    raw = np.unwrap(np.angle(y * np.conj(decisions)))
    kernel = np.ones(span) / span
    padded = np.concatenate((np.full(span, raw[0]), raw,
                             np.full(span, raw[-1])))
    return np.convolve(padded, kernel, mode='same')[span:span + len(raw)]


def equalize(samples: np.ndarray, taps: int, iterations: int,
             phase_span: int, beta: float):
    """Batch T/3 equalizer: matched-filter start, then least-squares refinement.

    An online blind loop was tried first and could not demodulate its own clean
    control signal, so this is deliberately deterministic instead.  The matched
    filter plus the best of the three sampling phases opens the eye well enough
    to make decisions; each iteration then solves the equalizer in closed form
    against those decisions, re-estimates the slow carrier phase, and re-decides.
    Nothing here depends on a step size.
    """
    samples = samples / np.sqrt(np.mean(np.abs(samples) ** 2))

    windows = np.lib.stride_tricks.sliding_window_view(samples, taps)
    windows = windows[::SAMPLES_PER_SYMBOL]

    # Start from a matched filter, choosing the sampling phase whose fourth
    # power is most concentrated -- i.e. the one that looks most like a
    # 4-point constellation.
    matched = np.convolve(samples, root_raised_cosine(
        SAMPLES_PER_SYMBOL, 16, beta), mode='same')
    best, best_phase = None, -1.0
    for phase in range(SAMPLES_PER_SYMBOL):
        candidate = matched[phase::SAMPLES_PER_SYMBOL][:len(windows)]
        quartic = candidate ** 4
        score = abs(quartic.mean()) / np.mean(np.abs(quartic))
        # NaN compares false against everything, so a silent window would
        # otherwise leave `best` unset and fail much further down.
        if best is None or score > best_phase:
            best, best_phase = candidate, score
    y = derotate(best / np.sqrt(np.mean(np.abs(best) ** 2)))

    amplitude = float(np.mean((np.abs(y.real) + np.abs(y.imag)) / 2.0))
    decisions = amplitude * (np.sign(y.real) + 1j * np.sign(y.imag))

    for _ in range(iterations):
        phase = smooth_phase(y, decisions, phase_span)
        target = decisions * np.exp(1j * phase)
        gram = windows.conj().T @ windows
        gram += 1e-9 * np.trace(gram).real / taps * np.eye(taps)
        weights = np.linalg.solve(gram, windows.conj().T @ target)
        y = windows @ weights
        phase = smooth_phase(y, decisions, phase_span)
        y = y * np.exp(-1j * phase)
        amplitude = float(np.mean((np.abs(y.real) + np.abs(y.imag)) / 2.0))
        decisions = amplitude * (np.sign(y.real) + 1j * np.sign(y.imag))
    return y, decisions


def synthetic_capture(seconds: float, carrier: float, peak: float,
                      noise_db: float | None = None, seed: int = 244) -> bytes:
    """A clean 4-point 3,200-baud passband signal, mu-law quantised."""
    rng = np.random.default_rng(seed)
    symbols = int(seconds * SYMBOL_RATE)
    corners = np.array([1 + 1j, 1 - 1j, -1 + 1j, -1 - 1j]) / math.sqrt(2.0)
    data = corners[rng.integers(0, 4, symbols)]

    # Shape on the 9,600 Hz grid, where 3,200 baud is exactly three samples
    # per symbol, then resample down to 8 kHz.  Rounding symbol positions onto
    # the 2.5-sample 8 kHz grid instead would misplace every other symbol by
    # half a sample, which is a generator bug that looks exactly like a bad
    # channel.
    oversample = SAMPLES_PER_SYMBOL
    span, beta = 16, EXCESS_BANDWIDTH
    t = np.arange(-span * oversample, span * oversample + 1) / oversample
    with np.errstate(divide='ignore', invalid='ignore'):
        pulse = (np.sin(np.pi * t * (1 - beta))
                 + 4 * beta * t * np.cos(np.pi * t * (1 + beta)))
        pulse /= (np.pi * t * (1 - (4 * beta * t) ** 2))
    pulse[np.isnan(pulse)] = 1 - beta + 4 * beta / np.pi
    pulse /= np.sqrt(np.sum(pulse ** 2))

    grid = np.zeros(symbols * oversample + len(pulse), dtype=complex)
    grid[np.arange(symbols) * oversample] = data
    shaped = np.convolve(grid, pulse)

    rate = SYMBOL_RATE * SAMPLES_PER_SYMBOL
    n = np.arange(len(shaped))
    passband = np.real(shaped * np.exp(2j * np.pi * carrier * n / rate))
    passband = np.real(resample_fft(passband, 5, 6))
    passband *= peak / np.max(np.abs(passband))
    if noise_db is not None:
        power = np.mean(passband ** 2)
        passband = passband + rng.normal(
            0.0, math.sqrt(power / 10.0 ** (noise_db / 10.0)), len(passband))
    return mulaw_encode(passband)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('capture', nargs='?', help='a .rx.ulaw capture (PCMU)')
    ap.add_argument('--synthetic', action='store_true',
                    help='run the control: demodulate a clean generated signal')
    ap.add_argument('--synthetic-peak', type=float, default=1310.0,
                    help='control signal peak; the capture measures 1310')
    ap.add_argument('--synthetic-noise-db', type=float, default=None,
                    help='add white noise at this SNR to the control')
    ap.add_argument('--from', dest='start', type=float, default=12.7)
    ap.add_argument('--to', dest='end', type=float, default=14.6)
    ap.add_argument('--taps', type=int, default=81,
                    help='equalizer taps at T/3 spacing')
    ap.add_argument('--iterations', type=int, default=6,
                    help='least-squares refinement passes')
    ap.add_argument('--phase-span', type=int, default=128,
                    help='symbols averaged in the carrier-phase estimate')
    ap.add_argument('--carrier', type=float, default=None,
                    help='override the estimated carrier, Hz')
    args = ap.parse_args()

    if args.synthetic:
        octets = synthetic_capture(args.end + 1.0, carrier=1920.0,
                                   peak=args.synthetic_peak,
                                   noise_db=args.synthetic_noise_db)
        label = 'synthetic control'
    elif args.capture:
        octets = open(args.capture, 'rb').read()
        label = args.capture
    else:
        ap.error('give a capture, or --synthetic')

    lo = int(args.start * SAMPLE_RATE)
    hi = min(int(args.end * SAMPLE_RATE), len(octets))
    if hi - lo < 4096:
        print(f'window {args.start}..{args.end}s is too short for {label}')
        return 1
    passband = mulaw_decode(octets[lo:hi])

    peak = float(np.max(np.abs(passband)))
    rms = float(np.sqrt(np.mean(passband ** 2)))
    if rms <= 0.0:
        print(f'[reference] {label} {args.start:.2f}..{args.end:.2f}s is silent')
        return 1
    print(f'[reference] {label} {args.start:.2f}..{args.end:.2f}s '
          f'({hi - lo} samples), peak {peak:.0f}, rms {rms:.0f}')

    print(f'[reference] mu-law quantisation ceiling for this window: '
          f'{quantisation_ceiling_db(passband):.1f} dB')

    analytic_signal = analytic(passband)
    ramp = 2j * np.pi * np.arange(hi - lo) / SAMPLE_RATE

    def downmix(frequency: float) -> np.ndarray:
        return lowpass(analytic_signal * np.exp(-frequency * ramp),
                       1850.0, SAMPLE_RATE)

    if args.carrier is not None:
        carrier, offset = args.carrier, 0.0
    else:
        # The spectral centroid only has to land within the low-pass width;
        # the fourth-power line then gives the carrier itself.  Re-derive the
        # baseband from the refined value rather than shifting the first one,
        # so the low pass ends up centred on the signal instead of clipping
        # whichever band edge the centroid happened to favour.
        carrier = estimate_carrier(passband, SAMPLE_RATE)
        offset = refine_carrier(downmix(carrier), SAMPLE_RATE)
        carrier += offset
    baseband = downmix(carrier)
    print(f'[reference] carrier {carrier - offset:.1f} Hz + fourth-power '
          f'residual {offset:+.2f} Hz -> {carrier:.1f} Hz')

    samples = resample_fft(baseband, 6, 5)   # 8,000 -> 9,600 Hz, T/3
    edge = int(0.02 * len(samples))          # discard the FFT-resampler edges
    samples = samples[edge:len(samples) - edge]

    outputs, decisions = equalize(samples, args.taps, args.iterations,
                                  args.phase_span, EXCESS_BANDWIDTH)

    # Drop the first and last eighth: the batch solve has edges too.
    settled = slice(len(outputs) // 8, -len(outputs) // 8)
    y, d = outputs[settled], decisions[settled]
    error = y - d
    amplitude = float(np.sqrt(np.mean(np.abs(d) ** 2)))
    error_rms = float(np.sqrt(np.mean(np.abs(error) ** 2)))
    if amplitude == 0.0 or error_rms == 0.0:
        print('[reference] the equalizer did not converge on this window')
        return 1
    snr_db = 20.0 * math.log10(amplitude / error_rms)
    evm = 100.0 * error_rms / amplitude

    # Restate the same error the way the card publishes it, so the two numbers
    # can be read side by side: PM 0x3348 forms (|eI| + |eQ|) / 2 against a
    # constellation whose corner is CARD_CORNER.
    corner = float(np.mean((np.abs(d.real) + np.abs(d.imag)) / 2.0))
    scale = CARD_CORNER / corner if corner else 0.0
    l1_half = float(np.mean((np.abs(error.real) + np.abs(error.imag)) / 2.0))
    print(f'[reference] {len(y)} settled symbols: '
          f'SNR {snr_db:.1f} dB, EVM {evm:.1f}%')
    print(f'[reference] scaled to the card\'s +/-{CARD_CORNER:.0f} corners, '
          f'mean (|eI|+|eQ|)/2 = {l1_half * scale:.0f}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
