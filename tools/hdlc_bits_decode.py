import sys
data = open(sys.argv[1], 'rb').read()
s = data.find(b'CONNECT')
if s >= 0:
    data = data[data.find(b'\n', s) + 1:]

# DS0 bit order: first bit of the stream is the octet's MSB.
bits = [(b >> k) & 1 for b in data for k in range(7, -1, -1)]

FLAG = [0,1,1,1,1,1,1,0]
pos, flags = 0, []
n = len(bits)
while pos <= n - 8:
    if bits[pos:pos+8] == FLAG:
        flags.append(pos); pos += 8
    else:
        pos += 1

def fcs16(frame):
    fcs = 0xffff
    for b in frame:
        fcs ^= b
        for _ in range(8):
            fcs = (fcs >> 1) ^ 0x8408 if fcs & 1 else fcs >> 1
    return fcs

good, bad = [], 0
for a, b in zip(flags, flags[1:]):
    seg = bits[a+8:b]
    if len(seg) < 32:
        continue
    out, ones = [], 0            # destuff
    for bit in seg:
        if ones == 5 and bit == 0:
            ones = 0; continue
        out.append(bit)
        ones = ones + 1 if bit else 0
    if len(out) % 8:
        out = out[:len(out) - (len(out) % 8)]
    # HDLC transmits each octet least-significant bit first.
    octets = bytes(sum(out[i+k] << k for k in range(8)) for i in range(0, len(out), 8))
    if len(octets) >= 4 and fcs16(octets) == 0xf0b8:
        good.append(octets[:-2])
    else:
        bad += 1

print("flags found:", len(flags), " good frames:", len(good), " bad FCS:", bad)
for f in good[:5]:
    print("  frame:", f[:14].hex(), "len", len(f))
