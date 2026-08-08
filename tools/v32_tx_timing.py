#!/usr/bin/env python3
"""Measure the *timing* of a transmitted line signal, not its spectrum.

Session 204 closed the spectral lead on V.32 transmit: sampled where both ends
are in data, both are proper V.32 QAM at 14-bin resolution. A coarse spectrum
cannot see a sample-clock defect, though, and a sample-clock defect is what
would produce every recorded symptom of the V.32 blocker at once -- one
directional, data independent (`--tx-prbs` reproduces it), level independent
(+12 dB changes nothing), and with a correct encoder.

The shim reads one line sample per 8 kHz frame out of `DM[DM(0x3FB4)]`
(`frame_fast()`'s fallback arm) with no check that the page published exactly
one new sample that frame. Page 8 had precisely this defect -- it published
9-12 times per sample and the transmitter was decimated by ten (Session 149) --
which is why the V.34 arm has `V34_PUBLISH_LATCH`/`V34_PUBLISH_PACED` and a
published/unpublished counter. The V.32 arm has none of that and has never been
instrumented.

Three measurements, each with the control the other lacks:

`repeat`   the fraction of adjacent sample pairs that are bit-identical. A
           sample-and-hold on a frame where the page published nothing leaves an
           exact duplicate. On modulated data at speech level, genuine
           duplicates are rare, so this is close to a direct read of the hold
           rate. Compare the two directions of one capture: the peer's stream
           arrives over the same G.711 path, so anything the path itself does
           shows up in both.

`baud`     the cyclostationary symbol-rate line. For a QAM signal at symbol
           rate B, the squared envelope has a spectral line at exactly B. V.32
           is 2400 baud. A line that is absent, smeared or off-frequency says
           the symbol clock reaching the line is not 2400 Hz, whatever the
           passband looks like. Reported as the peak in 2200-2600 Hz with the
           prominence over that band's median.

`band`     the -20 dB passband edges at fine resolution, so centre frequency
           and width are read rather than eyeballed off 14 bins. V.32 is
           600-3000 Hz about an 1800 Hz carrier.

Every window also reports RMS level and non-silence, because §4 of the handoff
records three separate leads that died to reading a window in which the line
was not actually carrying data. A window under `--min-rms` is skipped and said
to be skipped rather than averaged in.

Usage:
    tools/v32_tx_timing.py TX.ulaw [RX.ulaw] [--from S] [--to S] [--window MS]
"""

from __future__ import annotations

import argparse
import cmath
import math
import statistics
from pathlib import Path

RATE = 8000


def mulaw_decode_table() -> list[int]:
    """G.711 mu-law byte -> signed linear, built rather than tabulated."""
    table = []
    for byte in range(256):
        value = ~byte & 0xFF
        sign = value & 0x80
        exponent = (value >> 4) & 0x07
        mantissa = value & 0x0F
        magnitude = ((mantissa << 1) + 33) << exponent
        magnitude -= 33
        magnitude <<= 2
        table.append(-magnitude if sign else magnitude)
    return table


MULAW = mulaw_decode_table()


def load(path: Path) -> list[int]:
    return [MULAW[byte] for byte in path.read_bytes()]


def goertzel(samples: list[float], freq: float, rate: int = RATE) -> float:
    """Magnitude of one DFT bin at an arbitrary frequency."""
    omega = 2.0 * math.pi * freq / rate
    coeff = 2.0 * math.cos(omega)
    s1 = s2 = 0.0
    for sample in samples:
        s0 = sample + coeff * s1 - s2
        s2, s1 = s1, s0
    real = s1 - s2 * math.cos(omega)
    imag = s2 * math.sin(omega)
    return math.hypot(real, imag)


def hann(length: int) -> list[float]:
    if length < 2:
        return [1.0] * length
    return [0.5 - 0.5 * math.cos(2.0 * math.pi * i / (length - 1))
            for i in range(length)]


def spectrum(samples: list[int], lo: float, hi: float,
             step: float) -> list[tuple[float, float]]:
    window = hann(len(samples))
    shaped = [s * w for s, w in zip(samples, window)]
    out = []
    freq = lo
    while freq <= hi:
        out.append((freq, goertzel(shaped, freq)))
        freq += step
    return out


def band_edges(points: list[tuple[float, float]],
               down_db: float = 20.0) -> tuple[float, float, float] | None:
    """-down_db edges of the occupied band, and its centre."""
    if not points:
        return None
    peak = max(magnitude for _, magnitude in points)
    if peak <= 0.0:
        return None
    floor = peak * (10.0 ** (-down_db / 20.0))
    above = [freq for freq, magnitude in points if magnitude >= floor]
    if not above:
        return None
    lo, hi = min(above), max(above)
    return lo, hi, (lo + hi) / 2.0


def baud_line(samples: list[int], lo: float = 2200.0, hi: float = 2600.0,
              step: float = 2.0) -> tuple[float, float] | None:
    """Peak of the squared-envelope spectrum, and its prominence in dB.

    A QAM signal at symbol rate B puts a line at B in |x|^2. Squaring is the
    whole trick: it is what makes the symbol clock observable without knowing
    the carrier phase or the constellation.
    """
    power = [float(s) * float(s) for s in samples]
    mean = sum(power) / len(power)
    centred = [p - mean for p in power]
    points = spectrum_of(centred, lo, hi, step)
    if not points:
        return None
    peak_freq, peak_magnitude = max(points, key=lambda item: item[1])
    median = statistics.median([magnitude for _, magnitude in points])
    if median <= 0.0 or peak_magnitude <= 0.0:
        return None
    return peak_freq, 20.0 * math.log10(peak_magnitude / median)


def spectrum_of(values: list[float], lo: float, hi: float,
                step: float) -> list[tuple[float, float]]:
    window = hann(len(values))
    shaped = [v * w for v, w in zip(values, window)]
    out = []
    freq = lo
    while freq <= hi:
        out.append((freq, goertzel(shaped, freq)))
        freq += step
    return out


def repeat_fraction(samples: list[int]) -> float:
    if len(samples) < 2:
        return 0.0
    repeats = sum(1 for a, b in zip(samples, samples[1:]) if a == b)
    return repeats / (len(samples) - 1)


def run_lengths(samples: list[int]) -> dict[int, int]:
    """Histogram of identical-value run lengths, capped at 6+.

    A hold defect and mu-law quantisation of a quiet passage look the same in
    the repeat fraction alone. They separate here: quantisation makes runs of
    two, a page that publishes nothing for k frames makes a run of k+1.
    """
    histogram: dict[int, int] = {}
    length = 1
    for previous, current in zip(samples, samples[1:]):
        if current == previous:
            length += 1
            continue
        if length > 1:
            key = min(length, 6)
            histogram[key] = histogram.get(key, 0) + 1
        length = 1
    if length > 1:
        histogram[min(length, 6)] = histogram.get(min(length, 6), 0) + 1
    return histogram


def rms_dbm0(samples: list[int]) -> float:
    if not samples:
        return -99.0
    mean_square = sum(float(s) * float(s) for s in samples) / len(samples)
    if mean_square <= 0.0:
        return -99.0
    # Full scale 32768 is 3.14 dBm0 for mu-law; report relative to full scale
    # and leave the offset out, because every use here is a comparison.
    return 20.0 * math.log10(math.sqrt(mean_square) / 32768.0)


def analyse(name: str, samples: list[int], start: float, stop: float,
            window_ms: int, min_rms: float) -> None:
    width = int(RATE * window_ms / 1000)
    first = int(start * RATE)
    last = min(len(samples), int(stop * RATE))
    print(f"\n=== {name}: {len(samples)/RATE:.1f} s, "
          f"analysing {start:.1f}-{min(stop, len(samples)/RATE):.1f} s "
          f"in {window_ms} ms windows ===")
    print(f"{'t(s)':>6} {'rms(dBfs)':>10} {'repeat%':>8} {'runs2/3/4+':>12} "
          f"{'baud(Hz)':>9} {'prom(dB)':>9} {'band(Hz)':>13} {'centre':>7}")
    active = []
    for offset in range(first, last - width, width):
        block = samples[offset:offset + width]
        level = rms_dbm0(block)
        seconds = offset / RATE
        if level < min_rms:
            print(f"{seconds:6.1f} {level:10.1f} {'':>8} "
                  f"{'-- below --min-rms, skipped --':>12}")
            continue
        repeats = repeat_fraction(block) * 100.0
        histogram = run_lengths(block)
        runs = (f"{histogram.get(2, 0)}/{histogram.get(3, 0)}/"
                f"{sum(v for k, v in histogram.items() if k >= 4)}")
        baud = baud_line(block)
        edges = band_edges(spectrum(block, 300.0, 3500.0, 10.0))
        baud_text = f"{baud[0]:9.1f} {baud[1]:9.1f}" if baud else f"{'--':>9} {'--':>9}"
        if edges:
            band_text = f"{edges[0]:.0f}-{edges[1]:.0f}"
            centre_text = f"{edges[2]:.0f}"
        else:
            band_text = centre_text = "--"
        print(f"{seconds:6.1f} {level:10.1f} {repeats:8.2f} {runs:>12} "
              f"{baud_text} {band_text:>13} {centre_text:>7}")
        active.append((repeats, baud, edges))
    if not active:
        print("no window passed --min-rms; nothing measured")
        return
    print(f"active windows: {len(active)}, "
          f"median repeat {statistics.median(r for r, _, _ in active):.2f}%")
    bauds = [b[0] for _, b, _ in active if b]
    proms = [b[1] for _, b, _ in active if b]
    if bauds:
        print(f"baud line: median {statistics.median(bauds):.1f} Hz, "
              f"median prominence {statistics.median(proms):.1f} dB")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tx", type=Path, help="our transmit, mu-law")
    parser.add_argument("rx", type=Path, nargs="?",
                        help="the peer's transmit, mu-law -- the control")
    parser.add_argument("--from", dest="start", type=float, default=0.0)
    parser.add_argument("--to", dest="stop", type=float, default=1e9)
    parser.add_argument("--window", type=int, default=500,
                        help="window in ms (default 500)")
    parser.add_argument("--min-rms", type=float, default=-45.0,
                        help="skip windows quieter than this, dB rel. full "
                             "scale (default -45)")
    args = parser.parse_args()

    analyse(f"TX {args.tx.name}", load(args.tx), args.start, args.stop,
            args.window, args.min_rms)
    if args.rx:
        analyse(f"RX {args.rx.name} (control)", load(args.rx), args.start,
                args.stop, args.window, args.min_rms)


if __name__ == "__main__":
    main()
