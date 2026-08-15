#!/usr/bin/env python3
"""Compare the samples the media loop handed the modem against the wire.

`<prefix>.rx.ulaw` is written by the RTP reader, so it is the stream as it
arrived.  `<prefix>.fed.ulaw` (with `EICON_DUMP_FED_RX=1`) is written at the
`frame_fast()` call, so it is the stream the firmware actually demodulated.
Between them sit the jitter queue, the rx guard's silence substitution and the
setup gap, and Session 249 ended needing to know which of those a live V.90A
caller sees that a replay of its own `.rx.ulaw` does not:

    tools/fed_rx_diff.py artifacts/loopback/caller

The two are not expected to be equal -- the guard prefix alone makes the fed
stream longer -- so what this reports is the *alignment*: the offset at which
they agree best, how much of the wire stream survives, and where the first run
of real divergence starts.  A clean result is "fed = N silence codewords, then
the wire stream verbatim"; anything else is a difference the replay never had.
"""
from __future__ import annotations

import argparse
from pathlib import Path

SILENCE = {0xFF, 0x7F}  # mu-law zero, both signs


def read(path: Path) -> bytes:
    if not path.exists():
        raise SystemExit(f"{path} does not exist"
                         + ("; run the endpoint with EICON_DUMP_FED_RX=1"
                            if path.suffix == ".ulaw" and ".fed" in path.name
                            else ""))
    return path.read_bytes()


def leading_silence(data: bytes) -> int:
    for index, code in enumerate(data):
        if code not in SILENCE:
            return index
    return len(data)


def match_at(fed: bytes, wire: bytes, offset: int) -> tuple[int, int]:
    """(matching samples, first mismatch index into wire) at this offset."""
    window = min(len(wire), len(fed) - offset)
    matched = 0
    first = -1
    for index in range(window):
        if fed[offset + index] == wire[index]:
            matched += 1
        elif first < 0:
            first = index
    return matched, first


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prefix", type=Path,
                    help="capture prefix, e.g. artifacts/loopback/caller")
    ap.add_argument("--law", choices=("ulaw", "alaw"), default="ulaw")
    ap.add_argument("--search", type=int, default=20000,
                    help="how many sample offsets to try when aligning the "
                         "fed stream onto the wire stream (default 20000, "
                         "2.5 s at 8 kHz)")
    args = ap.parse_args()

    fed = read(args.prefix.with_suffix(f".fed.{args.law}"))
    wire = read(args.prefix.with_suffix(f".rx.{args.law}"))
    print(f"fed  {len(fed):>9} samples ({len(fed)/8000:.3f}s), "
          f"{leading_silence(fed)} leading silence codewords")
    print(f"wire {len(wire):>9} samples ({len(wire)/8000:.3f}s), "
          f"{leading_silence(wire)} leading silence codewords")

    best_offset, best_matched, best_first = 0, -1, -1
    for offset in range(0, min(args.search, max(1, len(fed) - 1))):
        matched, first = match_at(fed, wire, offset)
        if matched > best_matched:
            best_offset, best_matched, best_first = offset, matched, first
    window = min(len(wire), len(fed) - best_offset)
    print(f"best alignment: fed[{best_offset}:] vs wire[0:], "
          f"{best_matched}/{window} samples equal "
          f"({100.0 * best_matched / max(1, window):.4f}%)")
    if best_matched == window:
        print("the fed stream is the wire stream verbatim after the offset; "
              "the media path is not altering what the modem demodulates")
        return 0

    print(f"first divergence at wire sample {best_first} "
          f"({best_first / 8000:.3f}s into the received stream)")
    # A handful of scattered mismatches and a wholesale desynchronisation are
    # different faults, and the run structure is what separates them.
    runs, run_start, in_run = [], 0, False
    for index in range(window):
        differs = fed[best_offset + index] != wire[index]
        if differs and not in_run:
            run_start, in_run = index, True
        elif not differs and in_run:
            runs.append((run_start, index - run_start))
            in_run = False
    if in_run:
        runs.append((run_start, window - run_start))
    print(f"{len(runs)} divergent runs, longest "
          f"{max((length for _, length in runs), default=0)} samples")
    for start, length in runs[:10]:
        print(f"  wire {start:>8} +{length:<6} "
              f"fed={fed[best_offset + start]:#04x} wire={wire[start]:#04x}")
    if len(runs) > 10:
        print(f"  ... {len(runs) - 10} more")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
