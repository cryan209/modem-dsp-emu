#!/usr/bin/env python3
"""Destructive-but-restoring SDRAM test for a stopped Diva 4BRI-8M PCI.

The protocol processor must be stopped before this program is run.  The test
backs up BAR2, writes several patterns over the complete BAR, verifies them,
and restores the backup even when interrupted normally.  The backup remains
on disk so it can also be used after an abnormal termination.
"""

from __future__ import annotations

import argparse
import json
import mmap
import os
from pathlib import Path
import struct
import sys
import time


VENDOR = 0x1133
DEVICE = 0xE012
EXPECTED_SIZE = 4 * 1024 * 1024
PATTERNS = (0x00000000, 0xFFFFFFFF, 0x55555555, 0xAAAAAAAA)


def read_hex(path: Path) -> int:
    return int(path.read_text().strip(), 16)


def find_card(address: str | None) -> Path:
    root = Path("/sys/bus/pci/devices")
    if address:
        candidates = [root / address]
    else:
        candidates = list(root.glob("*"))
    matches = [
        p for p in candidates
        if p.is_dir()
        and read_hex(p / "vendor") == VENDOR
        and read_hex(p / "device") == DEVICE
    ]
    if len(matches) != 1:
        names = ", ".join(p.name for p in matches) or "none"
        raise RuntimeError(
            f"expected exactly one 1133:e012 card, found {len(matches)}: {names}"
        )
    return matches[0]


def bar_size(device: Path, index: int) -> int:
    fields = (device / "resource").read_text().splitlines()[index].split()
    start, end, flags = (int(value, 16) for value in fields[:3])
    if not (flags & 0x200):
        raise RuntimeError(f"BAR{index} is not an I/O-memory resource")
    return end - start + 1


def mismatch(mm: mmap.mmap, expected: int, address_pattern: bool):
    counts: dict[tuple[int, int], int] = {}
    samples: list[dict[str, str]] = []
    total = 0
    for offset in range(0, len(mm), 4):
        wanted = offset if address_pattern else expected
        got = struct.unpack_from("<I", mm, offset)[0]
        if got == wanted:
            continue
        total += 1
        delta = wanted ^ got
        for bit in range(32):
            mask = 1 << bit
            if delta & mask:
                direction = 1 if got & mask else 0
                key = (bit, direction)
                counts[key] = counts.get(key, 0) + 1
        if len(samples) < 200:
            samples.append({
                "offset": f"0x{offset:06x}",
                "expected": f"0x{wanted:08x}",
                "actual": f"0x{got:08x}",
                "xor": f"0x{delta:08x}",
            })
    return {
        "mismatched_words": total,
        "bit_failures": {
            f"bit_{bit}_to_{direction}": count
            for (bit, direction), count in sorted(counts.items())
        },
        "samples": samples,
        "samples_truncated": total > len(samples),
    }


def compare_bytes(mm: mmap.mmap, expected: bytes):
    samples: list[dict[str, str]] = []
    total = 0
    for offset, (wanted, got) in enumerate(zip(expected, mm[:])):
        if got == wanted:
            continue
        total += 1
        if len(samples) < 200:
            samples.append({
                "offset": f"0x{offset:06x}",
                "expected": f"0x{wanted:02x}",
                "actual": f"0x{got:02x}",
                "xor": f"0x{wanted ^ got:02x}",
            })
    return {
        "mismatched_bytes": total,
        "samples": samples,
        "samples_truncated": total > len(samples),
    }


def fill_word(mm: mmap.mmap, value: int) -> None:
    word = struct.pack("<I", value)
    mm[:] = word * (len(mm) // 4)
    # A read forces preceding posted PCI writes to complete.
    struct.unpack_from("<I", mm, len(mm) - 4)


def fill_addresses(mm: mmap.mmap) -> None:
    chunk_words = 16384
    for first in range(0, len(mm), chunk_words * 4):
        last = min(len(mm), first + chunk_words * 4)
        data = bytearray(last - first)
        for offset in range(first, last, 4):
            struct.pack_into("<I", data, offset - first, offset)
        mm[first:last] = data
    struct.unpack_from("<I", mm, len(mm) - 4)


def write_exclusive(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", help="PCI address, e.g. 0000:02:01.0")
    parser.add_argument("--bar", type=int, default=2, help="RAM BAR index (default: 2)")
    parser.add_argument("--backup", type=Path, required=True,
                        help="new file in which to preserve the original BAR")
    parser.add_argument("--report", type=Path, required=True,
                        help="new JSON report file")
    parser.add_argument("--confirm-stopped", action="store_true",
                        help="confirm divas_stop.rc has completed")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("run as root (sudo)")
    if not args.confirm_stopped:
        raise RuntimeError(
            "refusing to write BAR RAM without --confirm-stopped; first run "
            "sudo /usr/lib/divas/divas_stop.rc"
        )
    if args.backup.exists() or args.report.exists():
        raise RuntimeError("backup and report paths must not already exist")

    device = find_card(args.device)
    size = bar_size(device, args.bar)
    if size != EXPECTED_SIZE:
        raise RuntimeError(
            f"refusing unexpected BAR{args.bar} size 0x{size:x}; expected 0x{EXPECTED_SIZE:x}"
        )
    resource = device / f"resource{args.bar}"
    print(f"card={device.name} resource={resource} size=0x{size:x}", flush=True)

    fd = os.open(resource, os.O_RDWR | os.O_SYNC)
    try:
        mm = mmap.mmap(fd, size, flags=mmap.MAP_SHARED,
                       prot=mmap.PROT_READ | mmap.PROT_WRITE)
        try:
            original = mm[:]
            write_exclusive(args.backup, original)
            print(f"backup={args.backup} sha256: run sha256sum to record it", flush=True)
            report = {
                "pci_device": device.name,
                "vendor_device": "1133:e012",
                "bar": args.bar,
                "bar_size": size,
                "started_unix": int(time.time()),
                "tests": [],
            }
            try:
                for pattern in PATTERNS:
                    print(f"write/verify 0x{pattern:08x}", flush=True)
                    fill_word(mm, pattern)
                    result = mismatch(mm, pattern, False)
                    result["pattern"] = f"0x{pattern:08x}"
                    report["tests"].append(result)
                    print(f"  mismatched_words={result['mismatched_words']}", flush=True)

                print("write/verify address-as-data", flush=True)
                fill_addresses(mm)
                result = mismatch(mm, 0, True)
                result["pattern"] = "address-as-data"
                report["tests"].append(result)
                print(f"  mismatched_words={result['mismatched_words']}", flush=True)
            finally:
                print("restoring original BAR contents", flush=True)
                mm[:] = original
                struct.unpack_from("<I", mm, len(mm) - 4)
                report["restore"] = compare_bytes(mm, original)
                restored = report["restore"]["mismatched_bytes"] == 0
                report["restore"]["verified"] = restored
                if restored:
                    print("restore verified", flush=True)
                else:
                    print(
                        "WARNING: BAR restore verification failed: "
                        f"{report['restore']['mismatched_bytes']} mismatched bytes; "
                        f"recovery image is {args.backup}",
                        file=sys.stderr,
                        flush=True,
                    )

            report["completed_unix"] = int(time.time())
            write_exclusive(args.report, (json.dumps(report, indent=2) + "\n").encode())
            print(f"report={args.report}", flush=True)
            return 0 if restored else 2
        finally:
            mm.close()
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
