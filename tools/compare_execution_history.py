#!/usr/bin/env python3
"""Find the first state divergence between two SPORT execution histories."""
from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from pathlib import Path


def read_history(path: Path):
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "sample" not in reader.fieldnames:
            raise ValueError(f"{path}: not an execution-history CSV")
        return reader.fieldnames, list(reader)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare(left: Path, right: Path) -> int:
    left_fields, left_rows = read_history(left)
    right_fields, right_rows = read_history(right)
    if left_fields != right_fields:
        print("schema mismatch")
        for name, fields in ((left, left_fields), (right, right_fields)):
            print(f"  {name}: {','.join(fields)}")
        return 1

    limit = min(len(left_rows), len(right_rows))
    for index in range(limit):
        a, b = left_rows[index], right_rows[index]
        if a.get("sample") != b.get("sample"):
            print(f"first divergence: row {index}, sample "
                  f"{a.get('sample')} != {b.get('sample')}")
            return 1
        for field in left_fields:
            if a.get(field) != b.get(field):
                print(f"first divergence: row {index}, sample {a['sample']}, "
                      f"field {field}: {a.get(field)} != {b.get(field)}")
                return 1

    if len(left_rows) != len(right_rows):
        print(f"length divergence after {limit} rows: "
              f"{len(left_rows)} != {len(right_rows)}")
        return 1

    print(f"identical: {limit} rows")
    print(f"  {left}: sha256:{digest(left)}")
    print(f"  {right}: sha256:{digest(right)}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    args = parser.parse_args(argv)
    try:
        return compare(args.left, args.right)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
