#!/usr/bin/env python3
"""Recover the PM3's modulation selection path out of a ComOS `pmexe`.

`pmexe` is a headerless carve of ComOS's text, so nothing declares its load
address.  It is recoverable from the two adjacent display strings
``K56Flex Modulation`` and ``V.90 Modulation``: they sit 19 bytes apart, so a
``push $imm32`` whose immediate is 19 less than another push's immediate pins
the base.  Exactly one candidate is consistent, and it is 0x103000 in every
build from 3.8b15 on.

With the base known this recovers, for each image:

  * the modulation bitmask renderer and the bit -> display string map;
  * the ``set <port> modulation`` keyword table (abbrev, keyword, code);
  * the clear-then-set-one handler that turns a keyword code into a bit;
  * the ``mdp2_init_modem`` TLV emitted for each bit -- the actual modulation
    code sent to the data pump, ``E5 02 6D 03`` for K56flex and
    ``E5 02 6D 02`` for V.90.

Usage: pm3_modulation_code.py <pmexe> [more pmexe ...]
"""
import os
import re
import struct
import sys

SEP = ", "
K56 = b"K56Flex Modulation\x00"
V90 = b"V.90 Modulation\x00"


def load_base(d):
    """Pin the text load address from the adjacent K56flex/V.90 strings."""
    k = d.find(K56)
    if k < 0:
        return None
    pushes = {}
    for o in range(len(d) - 5):
        if d[o] == 0x68:
            pushes.setdefault(struct.unpack("<I", d[o + 1:o + 5])[0], []).append(o)
    # Candidate bases: every push immediate that could be the K56flex string,
    # given that the V.90 string 19 bytes later is also pushed.
    cands = [v - k for v in pushes if v + len(K56) in pushes and 0 < v - k]
    # Disambiguate with the ", " separator the renderer pushes once per bit:
    # under the true base its address is a heavily repeated push immediate.
    sep = d.find(b", \x00V.23 (B2)\x00")
    if sep < 0:
        return cands[0] if len(cands) == 1 else None
    best = [(len(pushes.get(sep + b, [])), b) for b in cands]
    best.sort(reverse=True)
    return best[0][1] if best and best[0][0] >= 5 else None


def cstr(d, addr, base):
    o = addr - base
    if not 0 <= o < len(d):
        return None
    e = d.find(b"\x00", o)
    if e < 0 or e - o > 24:
        return None
    t = d[o:e]
    return t.decode("latin1") if t and all(32 <= c < 127 for c in t) else None


def bit_strings(d, base):
    """Every ``test $imm32,%edi`` in the renderer and the string it guards."""
    sep = None
    k = d.find(b", \x00V.23 (B2)\x00")
    if k >= 0:
        sep = k + base
    out = []
    for m in re.finditer(b"\xf7\xc7", d):
        o = m.start()
        imm = struct.unpack("<I", d[o + 2:o + 6])[0]
        ptr = None
        for j in range(o + 6, min(o + 0x60, len(d) - 5)):
            if d[j] == 0x68:
                v = struct.unpack("<I", d[j + 1:j + 5])[0]
                if v != sep:          # skip the ", " separator push
                    ptr = v
                    break
        s = cstr(d, ptr, base) if ptr else None
        if s and ("Modulation" in s or s.startswith(("V.23", "MSE="))):
            out.append((o + base, imm, s))
    return out


def keyword_table(d, base):
    """The 12-byte {abbrev*, full*, code} table, ended by a 0xffff sentinel."""
    anchor = d.find(b"flex\x00fl\x00")
    if anchor < 0:
        return []
    want = struct.pack("<I", anchor + base)
    for m in re.finditer(re.escape(want), d):
        start = m.start() - 4           # step back onto the abbrev pointer
        while start - 12 >= 0:          # rewind to the head of the table
            a, b, c = struct.unpack("<III", d[start - 12:start])
            if c == 0xFFFF or cstr(d, a, base) is None or cstr(d, b, base) is None:
                break
            start -= 12
        rows, p = [], start
        while p + 12 <= len(d):
            a, b, c = struct.unpack("<III", d[p:p + 12])
            if c == 0xFFFF:
                break
            sa, sb = cstr(d, a, base), cstr(d, b, base)
            if sa is None or sb is None:
                break
            rows.append((sa, sb, c))
            p += 12
        if any(r[1] == "flex" for r in rows):
            return rows
    return []


def tlv_sites(d, base):
    """``testb $bit,0x68(%esi)`` in mdp2_init_modem and the TLV it writes."""
    out = []
    for bit, label in ((0x04, "v34"), (0x08, "flex"), (0x10, "v90")):
        for m in re.finditer(re.escape(bytes([0xF6, 0x46, 0x68, bit])), d):
            o = m.start()
            body, tlv, i = d[o + 6:o + 30], [], 0
            while i < len(body) - 3 and body[i] == 0xC6 and body[i + 1] == 0x45:
                tlv.append((body[i + 2] - 0x100, body[i + 3]))
                i += 4
            if len(tlv) >= 3:
                out.append((o + base, label, tlv))
    return out


def report(path):
    d = open(path, "rb").read()
    label = os.path.basename(os.path.dirname(os.path.abspath(path)))
    base = load_base(d)
    print("== %s  (%s)" % (label, os.path.basename(path)))
    if base is None:
        print("   no modulation display table -- pre-3.8b15 build")
        return
    print("   load base 0x%06x" % base)

    bits = bit_strings(d, base)
    if bits:
        print("   modulation bitmask at port+0x68:")
        for addr, imm, s in bits:
            mark = "  <==" if "K56Flex" in s or "V.90" in s else ""
            print("     0x%08x  bit 0x%08x  %-20s%s" % (addr, imm, s, mark))

    rows = keyword_table(d, base)
    if rows:
        print("   `set <port> modulation` keywords:")
        for ab, full, code in rows:
            print("     %-4s %-6s code=%d" % (ab, full, code))

    sites = tlv_sites(d, base)
    if sites:
        print("   mdp2_init_modem default-config TLVs:")
        for addr, lab, tlv in sites:
            body = [v for _, v in tlv][1:]
            frame = "00 06 01 " + " ".join("%02X" % v for v in body) + " FF"
            print("     0x%08x  %-5s -> %s" % (addr, lab, frame))


def main():
    if len(sys.argv) < 2:
        return print(__doc__.rstrip()) or 2
    for p in sys.argv[1:]:
        report(p)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
