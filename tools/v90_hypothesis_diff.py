#!/usr/bin/env python3
"""Compare sibling V.90 baud/carrier hypotheses for two G.711 captures.

The sibling demodulator is loaded from the temporary ABI bridge built by the
analysis notes.  This is a diagnostic selector, not a live wire decoder.
"""
from __future__ import annotations

import argparse
import ctypes
from pathlib import Path



def mulaw_decode(data: bytes) -> list[int]:
    """Decode G.711 u-law at the card's signed-16 scale."""
    result = []
    for code in data:
        value = (~code) & 0xff
        magnitude = ((value & 0x0f) << 3) + 132
        magnitude <<= (value >> 4) & 0x07
        result.append(-magnitude if value & 0x80 else magnitude)
    return result


class Hypothesis(ctypes.Structure):
    _fields_ = [
        ("baud_code", ctypes.c_int),
        ("carrier_sel", ctypes.c_int),
        ("carrier_hz", ctypes.c_float),
        ("baud_rate", ctypes.c_float),
        ("symbol_count", ctypes.c_int),
        ("segment_count", ctypes.c_int),
        ("score", ctypes.c_float),
        ("has_s", ctypes.c_bool),
        ("has_trn", ctypes.c_bool),
        ("has_j", ctypes.c_bool),
        ("has_ru", ctypes.c_bool),
    ]


def scan(lib, path: Path, start: float, seconds: float) -> list[Hypothesis]:
    data = path.read_bytes()
    lo = int(start * 8000)
    hi = min(len(data), lo + int(seconds * 8000))
    decoded = mulaw_decode(data[lo:hi])
    samples = (ctypes.c_int16 * len(decoded))(*decoded)
    result = (Hypothesis * 12)()
    lib.p3_scan_all_hypotheses(
        samples,
        len(samples), lo, 8000, result, 12)
    return sorted(result, key=lambda item: item.score, reverse=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("native", type=Path)
    parser.add_argument("failed", type=Path)
    parser.add_argument("--library", type=Path,
                        default=Path("/private/tmp/libv90_stream_event_bridge.dylib"))
    parser.add_argument("--start", type=float, default=12.0)
    parser.add_argument("--seconds", type=float, default=8.0)
    args = parser.parse_args()

    lib = ctypes.CDLL(str(args.library))
    lib.p3_scan_all_hypotheses.argtypes = [
        ctypes.POINTER(ctypes.c_int16), ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.POINTER(Hypothesis), ctypes.c_int]
    lib.p3_scan_all_hypotheses.restype = ctypes.c_int

    for label, path in (("native", args.native), ("failed", args.failed)):
        print(label)
        for item in scan(lib, path, args.start, args.seconds):
            print("  baud=%d carrier=%d score=%.3f symbols=%d segments=%d "
                  "S=%d TRN=%d J=%d Ru=%d" % (
                      item.baud_code, item.carrier_sel, item.score,
                      item.symbol_count, item.segment_count, item.has_s,
                      item.has_trn, item.has_j, item.has_ru))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
