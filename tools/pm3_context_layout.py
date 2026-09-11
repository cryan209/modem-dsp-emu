#!/usr/bin/env python3
"""Census the ADSP-2181 data pump's per-channel context field layout.

The PM3 data pump addresses its per-channel context with exactly one idiom:

    M0 = <literal field offset>
    I2 = <context base>
    modify(I2, M0)
    DM(I2,M2) = <value>        # or a load

So every ``M0 = <literal>`` in the assembled image is a field selector, and the
selector histogram is directly comparable between releases.  Counting them
across the 56K era shows that 3.8.2 does not add scattered new V.90 state: it
inserts six words at context offset 565 and shifts every field above it up by
six.  ``--init`` additionally recovers the ``(offset, stored literal)`` pairs,
whose value multisets are equal under that shift.

Usage:
  pm3_context_layout.py <dp2.bin|dp2.pm> [more images ...] [--init] [--min N]

Images are labelled by their parent directory (the ComOS release).
"""
import collections
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PM_START, PM_END = 0x30, 0x3f13

RE_M0 = re.compile(r"^([0-9a-f]{4}): \w+  M0 = (-?\d+)$")
RE_MODIFY = re.compile(r"modify address register")
RE_STORE = re.compile(r"^([0-9a-f]{4}): \w+  DM\(I2,M2\) = \$([0-9A-F]{4})$")


def disassemble(image, tmpdir):
    """Return the disassembly lines for a dp2 image (packed or already flat)."""
    pm = image
    if not image.endswith(".pm"):
        pm = os.path.join(tmpdir, os.path.basename(image) + ".pm")
        subprocess.run([sys.executable, os.path.join(HERE, "pm3_dp2_unpack.py"),
                        image, pm], check=True, stdout=subprocess.DEVNULL)
    out = subprocess.run([sys.executable, os.path.join(HERE, "adsp2181_dis.py"),
                          pm, hex(PM_START), hex(PM_END)],
                         check=True, capture_output=True, text=True)
    return out.stdout.splitlines()


def selectors(lines):
    return collections.Counter(int(m.group(2))
                               for m in map(RE_M0.match, lines) if m)


def initializers(lines):
    """Recover (field offset, stored literal) pairs.

    The offset belongs to the ``M0`` load feeding the nearest preceding
    ``modify``, not to the ``M0`` load textually adjacent to the store -- the
    assembler interleaves the next field's setup ahead of the current store.
    """
    m0 = pending = None
    out = collections.defaultdict(collections.Counter)
    for ln in lines:
        m = RE_M0.match(ln)
        if m:
            m0 = int(m.group(2))
            continue
        if RE_MODIFY.search(ln):
            pending = m0
            continue
        m = RE_STORE.match(ln)
        if m and pending is not None:
            out[pending][int(m.group(2), 16)] += 1
            pending = None
    return out


def anchor(counts):
    """The 588/589-style dispatch pair: the unique adjacent high-count pair."""
    return [f for f in range(555, 612)
            if counts[f] >= 10 and counts[f + 1] >= 10
            and abs(counts[f] - counts[f + 1]) <= 2]


def main():
    argv = sys.argv[1:]
    want_init = False
    minimum = 2
    args = []
    i = 0
    while i < len(argv):
        if argv[i] == "--init":
            want_init = True
        elif argv[i] == "--min":
            i += 1
            minimum = int(argv[i])
        elif argv[i].startswith("--"):
            return print(__doc__.rstrip()) or 2
        else:
            args.append(argv[i])
        i += 1
    if not args:
        return print(__doc__.rstrip()) or 2

    with tempfile.TemporaryDirectory() as tmp:
        labels, sels, inits = [], {}, {}
        for image in args:
            label = os.path.basename(os.path.dirname(os.path.abspath(image)))
            lines = disassemble(image, tmp)
            labels.append(label)
            sels[label] = selectors(lines)
            if want_init:
                inits[label] = initializers(lines)

    print("field selector census (M0 = <literal>)")
    print("off  " + "".join(l.rjust(9) for l in labels))
    fields = sorted(set().union(*(set(s) for s in sels.values())))
    for f in fields:
        row = [sels[l][f] for l in labels]
        if f >= 270 and max(row) >= minimum:
            print("%4d " % f + "".join(str(v).rjust(9) for v in row))

    print("\ndispatch-pair anchor (unique adjacent high-count pair in 555-612)")
    for l in labels:
        print("  %-9s %s" % (l, anchor(sels[l]) or "none"))

    if want_init:
        print("\nfield initializers (offset -> stored literals)")
        allf = sorted(set().union(*(set(d) for d in inits.values())))
        for f in allf:
            cells = []
            for l in labels:
                d = inits[l].get(f, {})
                cells.append(" ".join("%04x x%d" % (v, c)
                                      for v, c in sorted(d.items())) or "-")
            print("%5d  %s" % (f, "  |  ".join(cells)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
