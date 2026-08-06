"""Spectral concentration of the page-8 transmit signal.

The metric handoff.md §147 asks for: the fraction of total energy in the top 5%
of FFT bins, measured only inside a contiguous window where the V.34 page is
resident, so INFO and V.8 cannot contribute.  A page-8 transmitter doing its
job is spectrally concentrated; the broadband floor Session 145 measured is
what makes the 0x13BF correlator latch on noise (146, 147).

0.05 is white noise, since the top 5% of bins then hold 5% of the energy.  Read
a result near it as "this end is transmitting nothing a correlator could lock
to", not as a small deficit.

    tools/v34_page8_concentration.py artifacts/loopback-v90a/lat-1500/answerer

Each argument is a capture prefix: `<prefix>.endpoint.log` supplies the window
and `<prefix>.ulaw` the transmitted signal.
"""
import re
import sys
import numpy as np

ULAW_BIAS = 0x84


def ulaw_decode(raw: bytes) -> np.ndarray:
    u = np.frombuffer(raw, dtype=np.uint8).astype(np.int32) ^ 0xFF
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    value = ((mantissa << 3) + ULAW_BIAS) << exponent
    value -= ULAW_BIAS
    return np.where(sign, -value, value).astype(np.float64)


def page8_window(log: str) -> "tuple[int, int]":
    """First 0x0261-resident window, in samples, from the endpoint log."""
    entries = re.findall(r"sample (\d+) \([\d.]+s\): TrnProgress 0x[0-9a-f]+ "
                         r"-> 0x([0-9a-f]+)", open(log).read())
    start = None
    for sample, state in entries:
        value = int(state, 16)
        if start is None and value >= 0x60:
            start = int(sample)
        elif start is not None and value < 0x50:
            return start, int(sample)
    return (start, None) if start is not None else (None, None)


def concentration(signal: np.ndarray, frac: float = 0.05) -> float:
    if signal.size < 512:
        return float("nan")
    window = np.hanning(512)
    total = np.zeros(257)
    for offset in range(0, signal.size - 512, 256):
        spectrum = np.fft.rfft(signal[offset:offset + 512] * window)
        total += np.abs(spectrum) ** 2
    ranked = np.sort(total)[::-1]
    if ranked.sum() == 0:
        # A silent end, which is the caller's normal state on page 8 (137).
        # There is no spectrum to rank, so say so rather than dividing by zero.
        return float("nan")
    top = max(1, int(round(frac * ranked.size)))
    return float(ranked[:top].sum() / ranked.sum())


for prefix in sys.argv[1:]:
    start, stop = page8_window(f"{prefix}.endpoint.log")
    pcm = ulaw_decode(open(f"{prefix}.ulaw", "rb").read())
    if start is None:
        print(f"{prefix}: no page-8 window in the log")
        continue
    stop = min(stop or pcm.size, pcm.size)
    segment = pcm[start:stop]
    print(f"{prefix}  window {start}..{stop} "
          f"({(stop - start) / 8000:.2f}s)  rms {np.sqrt((segment ** 2).mean()):8.1f}"
          f"  concentration {concentration(segment):.3f}")
