# What is inside a PortMaster ComOS upgrade

`docs/firmware/portmaster/` holds six Livingston/Lucent PortMaster ComOS
upgrade files. They matter here because the PM3's digital modems are
K56flex/V.90 **server** engines running on ADSP-2181s — the same core the
emulator in `tools/adsp2181emu/` already executes for the Eicon card. Where
the Eicon firmware is a client-side V.90 digital modem, these are the other
end of the same call.

`tools/pm3_comos_extract.py <image> <outdir> --pack` unpacks one.

## Container

The upgrade file is a text archive, not a binary: `#` comment lines, a
`file <name>` header per section, and a uuencoded body ending on a short
line. Sections named `/6/…` and `/7/…` are paths in the unit's flash.

Each section body is then a run of **gzip members**, and each member carries
its component's original filename in the FNAME field — which is how the
pieces below are named. `pmexe` is the ComOS executable itself.

## Release timeline

`docs/firmware/portmaster/` now holds 32 PM3 releases, 3.5 through 3.9.1, which
turns the 56K transition into a series rather than two snapshots.

| era | releases | modem controller (`m2d`) | data pump (`m2c`) | 2181 overlays |
|---|---|---|---|---|
| V.34 | 3.5 … 3.5.1b20 | — | — | per-modulation: `v22` `v32` `v34`, plus `bot` `cmn` `ph1` `ph2` `omc` `mnp` (`ans`/`org` from 3.5.1b20) |
| `m2d`/`m2c` appears | 3.7 … 3.7.2 | unversioned, then `i930729` | 56-59 KB | `v34` gone; `v22` `v32` remain |
| 56K in the DSP | 3.7.2c3, 3.8b13 | `i930729`, `i930738` | 75-76 KB | `v22` gone at 3.8b15 |
| 56K in the host | 3.8b15 … 3.8.3 | `i416800` … `iA01809` | 71-74 KB | `v32` last seen 3.8.2 |
| frozen | 3.8.2c2 … 3.9.1 | `iA01809` … `i12600e` | 74070 B, **identical** | none |

Three corrections to what an inspection of 3.5/3.8/3.9 alone suggested:

- **The 56K server arrives in 3.7, not 3.8.** 3.7 is the first release with the
  `m2d`/`m2c` pair at all. 3.5.x has no controller and no data pump.
- **DSP support precedes host support.** The data pump grows an eighth record and
  +16 KB at **3.7.2c3**, while `pm3OS` still has no 56K strings. The host gains
  `K56Flex Modulation` / `V.90 Modulation` and the `flex` keyword only at
  **3.8b15**.
- **The config keywords are `v23b3` `v23b2` `ccitt` `flex` `auto`.** There is no
  `v90` and no `v34` keyword. `set <port> modulation flex` selects the 56K
  engine; whether a call lands on K56flex or V.90 is reported by the display
  table, not selected. Both display strings appear together at 3.8b15, so no
  shipped PM3 build is K56flex-only on the host side.

`wanctl.0` (`wanctl.bin`) and, from 3.9 on, `/7/mipsboot` (the VPN co-processor)
ride along in every PM3 image.

### The data pump froze before the controller did

There are only **11 distinct `dp2.bin` images** across the 32 releases, and the
last one covers 15 consecutive releases:

```
3.8.2c2 3.8.2c4 3.8.2 3.8.3 3.9b8 3.9b9 3.9b22 3.9b24
3.9b26 3.9b27 3.9b28 3.9 3.9.1b1 3.9.1c1 3.9.1     -> byte-identical
```

Over that same span the Z180 controller changes five times (`iA01809`,
`ic07819`, `i518902`, `i120899`, `i12600e`). So all late-3.9 modem work is
controller-side, and there is exactly one final data pump to analyse.
(`3.9b12` is a one-off outlier, 74125 B.)

### Where the DSP changed

Record lengths per release, from `pm3_dp2_unpack.py --map`:

| rec | 3.7 | 3.7.2c3 | 3.8b13 | 3.8b15 | 3.8b19 | 3.8.2+ |
|---|---:|---:|---:|---:|---:|---:|
| 0 | 377 | 377 | 377 | 247 | 247 | 247 |
| 1 | 3333 | 4777 | 4615 | 3980 | 3975 | 4019 |
| 2 | 2912 | 3937 | 4046 | 3960 | 3956 | 3514 |
| 3 | 142 | 142 | 142 | 142 | 142 | 142 |
| 4 | 1946 | 1946 | 1946 | 1946 | 1946 | 1946 |
| 5 | 1740 | 1740 | 1740 | 1740 | **2183** | **2976** |
| 6 | 1994 | 1620 | 1944 | 1610 | 1611 | 1645 |
| 7 | — | 1624 | 1624 | 1616 | 1616 | 1611 |

Records 3 and 4 hold their length exactly (142, 1946) in every release from 3.7
on while their contents change -- fixed-size slots, not invariant modules.
Record 5 holds 1740 words from 3.7 through 3.8b15, then grows twice, +443 and
+793. It is the only record that changes size *after* the host learns about
K56flex and V.90, which makes it the prime suspect for where V.90 landed. That
is a lead from record geometry, not a decode.

## The K56flex engine

Two halves, both in the `m2d`/`m2c` pair:

- **`i12600e.bin`** (3.9.1) — 256 KiB **Z180/HD64180** modem-controller code.
  Its header string at offset 17 is `VERSION: 12600E`, matching
  `docs/firmware/portmaster/release391.txt:23` ("ComOS 3.9.1 contains modem
  code version i12600e"). Entry: `di / ld a,0deh / out0 (0c0h),a /
  ld sp,0fffdh / jp 0302h`. Banked code runs 0–0x18000, tables and the DSP
  payload 0x20000–0x36000. 3.8 ships `i623810.bin`, 3.9b9 `ic07819.bin`, all
  256 KiB.
- **`dp2.bin`** — the ADSP-2181 data pump, a record-structured download image
  (see below) assembling to PM `0x0030`–`0x3f13` in 3.8.2 and later. `--pack`
  writes the flat `.pm` that `tools/adsp2181_dis.py` and the emulator core load.

The ComOS side confirms what the pair implements. In `pmexe` from 3.8b15 onward
(and in no earlier build) there is a modulation display table —
`V.23 (B2)`, `V.23 (B3)`, `V.34 Modulation`, `K56Flex Modulation`,
`V.90 Modulation`, then `MSE=0x2500`…`MSE=0x4000`, the thresholds also being
config keywords — alongside the `modulation` keyword set `v23b3`, `v23b2`,
`ccitt`, `flex`, `auto`. So `set <port> modulation flex` selects the 56K engine,
and the display table reports which of K56flex or V.90 a call actually reached.

## The download image format

`dp2.bin` and the `2181_*` overlays are not flat PM images, which is why an
interrupt-vector scan over the raw file finds nothing at any offset. They are
record-structured, and the unit is **not** a 32-bit slot.

The file is a stream of 16-bit little-endian values read as pairs, each pair
holding one 24-bit ADSP-2181 word:

    word = (first << 8) | (second & 0x00ff)

so the high byte of the second half is normally zero. A non-zero high byte
there marks a record header, whose first half is the record's PM load address:

    <addr:16> <tag:16>  then <word:24 as a pair> ...

The record runs to the next header. Lengths are not stored -- consecutive
records are contiguous in PM, so a record's length is the gap to the next
header. Address `0xf000` terminates the stream; in `dp2.bin` the PM records are
followed by a 16-bit DM data region (from pair 16108 in 3.9.x).

`tools/pm3_dp2_unpack.py <image> <out.pm> [--map]` writes the assembled flat PM
that `tools/adsp2181_dis.py` and the emulator core load.

### `dp2.bin` 3.9.1 record map

| load addr | tag | words | end |
|---|---|---:|---|
| `0030` | `01b0` | 247 | `0127` |
| `0126` | `1f66` | 4019 | `10d9` |
| `10d9` | `1b74` | 3514 | `1e93` |
| `1e93` | `011c` | 142 | `1f21` |
| `1f21` | `0f34` | 1946 | `26bb` |
| `26bb` | `1740` | 2976 | `325b` |
| `325b` | `0cda` | 1645 | `38c8` |
| `38c8` | `02b4` | 1611 | `3f13` |

The records tile `0x0030`-`0x3f13`, inside the 2181's 16K program memory, and
leave `0x0000`-`0x002f` -- exactly the 48-word interrupt vector table -- absent.
The controller supplies the vectors; the image never carries them.

Confirmation that the decode is right: record 0 disassembles as a register-save
prologue (`M5 = -1`, then a run of `DM(I4,M5) = <reg>` pushes), 1329 of the 1336
direct `JUMP`/`CALL` targets in the assembled image land inside the loaded
range, and `CALL $1f21` at `0x004d` targets a record load address exactly. The
same walk decodes every 3.5 and 3.8 overlay (`2181_ph1`, `_v32`, `_v34`, `_mnp`,
`_omc`, `_ans`, `_boot`, ...), which base at `0x2000` and end on `f000`.

## Not yet established

- The 16-bit header `tag` is not decoded. It is not a length (lengths come from
  the address deltas) and not an obvious checksum.
- Record 0 carries one word more than the gap to record 1's address, so its
  final word (`0x0a001f` in 3.9.1) is overwritten when record 1 loads. Every
  other record fits its gap exactly.
- The DM data region at the tail of `dp2.bin` is copied out but not mapped to a
  DM load address; only the PM side is assembled.
- The signal processing itself. The image is now disassemblable, but nothing
  here yet identifies the K56flex transmit path inside it.
