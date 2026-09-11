#!/usr/bin/env python3
"""Recover the PM3 data pump's own log strings and parameter-block geometry.

The data pump has a printf.  Its format strings live in the download image's DM
records, stored **one ASCII character per 16-bit word**, which is why a normal
`strings` pass over dp2.bin finds nothing.  Recovering them gives the firmware's
own vocabulary -- `venus_m2h_cmd`, `DP_STATE_*`, `LINE PROBE`, `MP_WORDS` -- and
with it the names of the modem's parameter blocks.

Those blocks are dumped by a routine that, per block, pushes the label, then an
items-per-line count, then an element count, calls a byte or word dumper, and
advances a cursor.  The element count and the advance are code immediates, not
table data, so block geometry can be read straight out of any build and
compared across releases.  That is where the K56flex/V.90 difference actually
lives: the blocks are not templates in DM, they are generated.

Usage:
  pm3_dsp_strings.py --strings <dp2.bin> [...]        list the log strings
  pm3_dsp_strings.py --diff <a.dp2.bin> <b.dp2.bin>   strings added/removed
  pm3_dsp_strings.py --blocks <rel>=<dp2.bin> [...]   parameter-block geometry
"""
import importlib.util
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "pm3_dp2_unpack", os.path.join(HERE, "pm3_dp2_unpack.py"))
_u = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_u)

RE_PUSH = re.compile(r"^([0-9a-f]{4}): \w+  DM\(I4,M[57]\) = \$([0-9A-F]{4})$")
RE_CALL = re.compile(r"^([0-9a-f]{4}): \w+  CALL \$([0-9A-F]{4})$")
RE_AY1 = re.compile(r"^([0-9a-f]{4}): \w+  AY1 = \$([0-9A-F]{4})$")


def dm_image(path):
    data = open(path, "rb").read()
    dm = {}
    for addr, _count, words in _u.records(data):
        if addr == 0:
            break
        if addr & 0x4000:
            base = addr & 0x3FFF
            for i, w in enumerate(words):
                dm[base + i] = w
    return dm


def log_strings(path, minlen=4):
    """{DM address: text} for every run of printable characters."""
    dm = dm_image(path)
    if not dm:
        return {}
    out, a, top = {}, min(dm), max(dm)
    while a <= top:
        if a in dm and 32 <= dm[a] < 127:
            run, start = [], a
            while a in dm and 32 <= dm[a] < 127:
                run.append(chr(dm[a]))
                a += 1
            if len(run) >= minlen:
                out[start] = "".join(run)
        else:
            a += 1
    return out


def disassemble(image, tmpdir):
    pm = os.path.join(tmpdir, os.path.basename(image) + ".pm")
    subprocess.run([sys.executable, os.path.join(HERE, "pm3_dp2_unpack.py"),
                    image, pm], check=True, stdout=subprocess.DEVNULL)
    out = subprocess.run([sys.executable, os.path.join(HERE, "adsp2181_dis.py"),
                          pm, "0x30", "0x3f13"],
                         check=True, capture_output=True, text=True)
    return out.stdout.splitlines()


def blocks(image, tmpdir):
    """{block name: (per_line, count, element_bytes)} from the dump routine."""
    strs = log_strings(image)
    lines = disassemble(image, tmpdir)
    found = {}
    for i, line in enumerate(lines):
        m = RE_PUSH.match(line.rstrip())
        if not m:
            continue
        label = strs.get(int(m.group(2), 16))
        if not label or not label.startswith("M%d: ") or "%" in label[5:]:
            continue
        imms, called, advance = [], None, None
        for j in range(i + 1, min(i + 16, len(lines))):
            nxt = lines[j].rstrip()
            mm = RE_PUSH.match(nxt)
            if mm:
                imms.append(int(mm.group(2), 16))
                continue
            if RE_CALL.match(nxt) and imms:
                called = True
            ma = RE_AY1.match(nxt)
            if ma and called:
                advance = int(ma.group(2), 16)
                break
        # A real block dump advances a cursor by a whole multiple of its count.
        if len(imms) >= 2 and advance and imms[1] and advance % imms[1] == 0:
            found[label[5:]] = (imms[0], imms[1], advance // imms[1])
    return found


def main():
    argv = sys.argv[1:]
    if not argv:
        return print(__doc__.rstrip()) or 2

    if argv[0] == "--strings":
        for p in argv[1:]:
            print("==", p)
            s = log_strings(p)
            for a in sorted(s):
                print("  %04x  %r" % (a, s[a]))
        return 0

    if argv[0] == "--diff" and len(argv) == 3:
        a, b = log_strings(argv[1]), log_strings(argv[2])
        sa, sb = set(a.values()), set(b.values())
        print("%s: %d strings   %s: %d strings"
              % (os.path.basename(argv[1]), len(a),
                 os.path.basename(argv[2]), len(b)))
        for title, items in (("only in the second", sorted(sb - sa)),
                             ("only in the first", sorted(sa - sb))):
            print("\n=== %s ===" % title)
            for s in items:
                print("   %r" % s)
        return 0

    if argv[0] == "--blocks":
        cols = []
        with tempfile.TemporaryDirectory() as tmp:
            for spec in argv[1:]:
                rel, _, path = spec.partition("=")
                cols.append((rel or os.path.basename(path), blocks(path, tmp)))
        names = []
        for _rel, b in cols:
            for n in b:
                if n not in names:
                    names.append(n)
        print("block         elem" + "".join(r.rjust(10) for r, _ in cols))
        for n in names:
            elem = next((b[n][2] for _r, b in cols if n in b), 0)
            row = "".join((str(b[n][1]) if n in b else "-").rjust(10)
                          for _r, b in cols)
            print("%-13s %dB  " % (n, elem) + row)
        return 0

    return print(__doc__.rstrip()) or 2


if __name__ == "__main__":
    sys.exit(main())
