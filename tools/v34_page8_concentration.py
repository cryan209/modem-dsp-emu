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


def spectrum(signal: np.ndarray) -> np.ndarray:
    window = np.hanning(512)
    total = np.zeros(257)
    for offset in range(0, signal.size - 512, 256):
        total += np.abs(np.fft.rfft(signal[offset:offset + 512] * window)) ** 2
    return total


def concentration(signal: np.ndarray, frac: float = 0.05,
                  passband: bool = True) -> "tuple[float, float]":
    """Energy share of the top `frac` of bins, and the peak frequency.

    `passband` restricts the whole measurement to 300-3400 Hz, and it must
    normally stay on.  The raw metric is band-agnostic, so a DC blob scores as
    well as a carrier: Session 149's first reading of 0.813 for the answerer was
    80.3% of the energy sitting at 0-300 Hz, which is not a V.34 signal at all.
    Judge a transmitter on the band a phone line carries.
    """
    if signal.size < 512:
        return float("nan"), float("nan")
    total = spectrum(signal)
    if passband:
        lo, hi = int(300 * 512 / 8000), int(3400 * 512 / 8000) + 1
        total = total[lo:hi]
    else:
        lo = 0
    if total.sum() == 0:
        # A silent end, which was the caller's normal state on page 8 before
        # Session 149. There is no spectrum to rank, so say so.
        return float("nan"), float("nan")
    peak = (int(np.argmax(total)) + lo) * 8000 / 512
    ranked = np.sort(total)[::-1]
    top = max(1, int(round(frac * ranked.size)))
    return float(ranked[:top].sum() / ranked.sum()), peak


for prefix in sys.argv[1:]:
    start, stop = page8_window(f"{prefix}.endpoint.log")
    pcm = ulaw_decode(open(f"{prefix}.ulaw", "rb").read())
    if start is None:
        print(f"{prefix}: no page-8 window in the log")
        continue
    stop = min(stop or pcm.size, pcm.size)
    segment = pcm[start:stop]
    band, peak = concentration(segment)
    raw, _ = concentration(segment, passband=False)
    total = spectrum(segment)
    dc = total[:int(300 * 512 / 8000)].sum() / max(total.sum(), 1e-30)
    print(f"{prefix}  window {start}..{stop} "
          f"({(stop - start) / 8000:.2f}s)  rms {np.sqrt((segment ** 2).mean()):7.1f}"
          f"  passband conc {band:.3f} peak {peak:6.0f} Hz"
          f"  (raw {raw:.3f}, {dc * 100:.0f}% below 300 Hz)")
