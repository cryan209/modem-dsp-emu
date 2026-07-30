#!/usr/bin/env python3
"""Compare complete ADDSP database snapshots immediately before page handoff."""

from __future__ import annotations

import argparse
import csv
import struct
from pathlib import Path

RECORD = struct.Struct("<Q256H")
MAGIC = b"EADSPDM2"


def handoff_sample(csv_path: Path, overlay: int) -> tuple[int, dict[str, str]]:
    rows = list(csv.DictReader(csv_path.open()))
    index = next(i for i, row in enumerate(rows)
                 if int(row["overlay"], 0) == overlay)
    return int(rows[index - 1]["sample"]), rows[index - 1]


def snapshot(path: Path, wanted: int) -> tuple[int, tuple[int, ...]]:
    data = path.read_bytes()
    if not data.startswith(MAGIC):
        raise ValueError(f"{path}: not an EADSPDM2 capture")
    records = [RECORD.unpack_from(data, offset)
               for offset in range(len(MAGIC), len(data) - RECORD.size + 1,
                                   RECORD.size)]
    record = min(records, key=lambda item: abs(item[0] - wanted))
    return record[0], record[1:]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("left", type=Path, help="capture prefix")
    parser.add_argument("right", type=Path, help="capture prefix")
    parser.add_argument("--left-overlay", type=lambda x: int(x, 0), required=True)
    parser.add_argument("--right-overlay", type=lambda x: int(x, 0), required=True)
    args = parser.parse_args()

    samples = []
    rows = []
    words = []
    for prefix, overlay in ((args.left, args.left_overlay),
                            (args.right, args.right_overlay)):
        sample, row = handoff_sample(prefix.with_suffix(".adsp.csv"), overlay)
        actual, data = snapshot(prefix.with_suffix(".adsp-dm.bin"), sample)
        samples.append(actual)
        rows.append(row)
        words.append(data)

    fields = ("bootpage", "overlay", "trnprogress", "baud_info",
              "info_mode_selector", "info_variant", "info_internal_progress",
              "info_state_vector", "gen_control", "di_control")
    print(f"pre-handoff samples: {samples[0]} {samples[1]}")
    for field in fields:
        print(f"{field:24s} {rows[0][field]:>8s} {rows[1][field]:>8s}")
    print("\ncomplete interface differences:")
    for offset, (left, right) in enumerate(zip(*words)):
        if left != right:
            print(f"DM{0x3EE0 + offset:04x} {left:04x} {right:04x} "
                  f"xor={left ^ right:04x}")


if __name__ == "__main__":
    main()
