#!/usr/bin/env python3
"""Compare the PM3 data pump's initialized DM data and its scheduler states.

The download image carries DM records as well as PM records, and the first 58
of them -- DM 0x2000..0x2667 -- keep the same address and length in every
build from 3.8b15 on.  That makes the data pump's initialized tables directly
comparable across the K56flex/V.90 boundary, with no relocation to undo.

Only two of those 58 change between 3.8b15 and 3.9.1, and one of them
(DM 0x242e) is a uniform +34 relocation of PM addresses.  The other, DM 0x21a4,
is the scheduler's dispatch table: 101 entries of PM addresses indexed by the
channel's scheduler-state field, with a "return" address filling every unused
slot.  Its populated entries are the states the data pump actually implements,
so diffing it across releases dates every state the engine ever gained.

Usage:
  pm3_dsp_states.py --states <rel>=<dp2.bin> [...]    dispatch table per release
  pm3_dsp_states.py --data <a.dp2.bin> <b.dp2.bin>    aligned DM data diff
"""
import collections
import importlib.util
import os
import sys

DISPATCH = 0x21A4

_spec = importlib.util.spec_from_file_location(
    "pm3_dp2_unpack", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "pm3_dp2_unpack.py"))
_u = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_u)


def dm_records(path):
    """[(address, words)] for every DM record, in image order."""
    data = open(path, "rb").read()
    out = []
    for addr, _count, words in _u.records(data):
        if addr == 0:
            break
        if addr & 0x4000:
            out.append((addr & 0x3FFF, words))
    return out


def aligned_prefix(a, b):
    """How many leading DM records share an address and a length."""
    n = 0
    while (n < min(len(a), len(b)) and a[n][0] == b[n][0]
           and len(a[n][1]) == len(b[n][1])):
        n += 1
    return n


def dispatch_table(path):
    for addr, words in dm_records(path):
        if addr == DISPATCH:
            return words
    return None


def states(words):
    """(default address, {state: handler}) -- the default fills unused slots."""
    default = collections.Counter(words).most_common(1)[0][0]
    return default, {i: v for i, v in enumerate(words) if v != default}


def cmd_states(args):
    cols = []
    for spec in args:
        rel, _, path = spec.partition("=")
        table = dispatch_table(path)
        if table is None:
            print("  %s: no dispatch table at DM %04x" % (rel, DISPATCH),
                  file=sys.stderr)
            continue
        cols.append((rel, states(table)))
    if not cols:
        return 1
    idx = sorted(set().union(*[set(s[1][1]) for s in cols]))
    print("state  " + "".join(r.rjust(10) for r, _ in cols))
    print("(dflt) " + "".join(("%04x" % s[0]).rjust(10) for _, s in cols))
    for i in idx:
        row = "".join((("%04x" % s[1][i]) if i in s[1] else "-").rjust(10)
                      for _, s in cols)
        print("%3d/0x%02x" % (i, i) + row)
    return 0


def cmd_data(args):
    if len(args) != 2:
        return print(__doc__.rstrip()) or 2
    a, b = dm_records(args[0]), dm_records(args[1])
    n = aligned_prefix(a, b)
    words = sum(len(w) for _, w in a[:n])
    print("aligned prefix: %d DM records, DM %04x..%04x (%d words)"
          % (n, a[0][0], a[n - 1][0] + len(a[n - 1][1]) - 1, words))
    changed = [(addr, len(wa), sum(1 for x, y in zip(wa, wb) if x != y))
               for (addr, wa), (_b, wb) in zip(a[:n], b[:n]) if wa != wb]
    print("changed: %d" % len(changed))
    for addr, length, diff in changed:
        print("   DM %04x  %3d words, %2d differ" % (addr, length, diff))
    return 0


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "--states":
        return cmd_states(argv[1:])
    if argv and argv[0] == "--data":
        return cmd_data(argv[1:])
    return print(__doc__.rstrip()) or 2


if __name__ == "__main__":
    sys.exit(main())
