#!/bin/sh
# Build the PM3 controller probe against BinaryMelodies/x80-emulator.
set -eu

if [ "$#" -ne 1 ]; then
    echo "usage: $0 /path/to/x80-emulator" >&2
    exit 2
fi
x80=$1
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
out="$repo/artifacts/pm3-z180"
mkdir -p "$out"

python3 "$x80/src/cpu/generate.py" emu "$x80/src/cpu/isa.dat" \
    "$out/emulator.gen.c"
cc -O2 -Wall -DCPU_Z180=1 -I"$x80/src/cpu" -I"$out" \
    -c "$x80/src/cpu/cpu.c" -o "$out/z180.o"
cc -O2 -Wall -I"$x80/src/cpu" "$repo/tools/pm3_z180_harness.c" \
    "$out/z180.o" -o "$out/pm3-z180"
echo "$out/pm3-z180"
