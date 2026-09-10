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

    <IDMA address:16> <transfer count:16> <count 16-bit writes> ...

This is the exact format read by ComOS `mdp2_card_dload` at `pmexe` file offset
`0x4ed54`. It writes the address to the card's `+0xa8` port, then bulk-writes
`count` words to `+0xaa`. PM records (address bit `0x4000` clear) use two
transfers per 24-bit word: `(first << 8) | (second & 0xff)`. DM records (bit
`0x4000` set) use one transfer per 16-bit word. The final address-zero record
writes PM[0], which releases the processor from IDMA boot hold; a checksum word
follows the stream.

`tools/pm3_dp2_unpack.py <image> <out.pm> [--map]` writes the assembled flat PM
that `tools/adsp2181_dis.py` and the emulator core load.

### `dp2.bin` 3.9.1 record map

| load addr | transfers | words | end |
|---|---|---:|---|
| `0030` | `01b0` | 216 | `0108` |
| `0108` | `003c` | 30 | `0126` |
| `0126` | `1f66` | 4019 | `10d9` |
| `10d9` | `1b74` | 3514 | `1e93` |
| `1e93` | `011c` | 142 | `1f21` |
| `1f21` | `0f34` | 1946 | `26bb` |
| `26bb` | `1740` | 2976 | `325b` |
| `325b` | `0cda` | 1645 | `38c8` |
| `38c8` | `02b4` | 346 | `3a22` |

Later PM records fill the remaining ranges, including `0x0001`-`0x002f`, and
the final record supplies PM[0]. Thus `dp2.bin` is a complete IDMA boot image;
the controller does not synthesize its vector table.

Confirmation that the decode is right: record 0 disassembles as a register-save
prologue (`M5 = -1`, then a run of `DM(I4,M5) = <reg>` pushes), 1329 of the 1336
direct `JUMP`/`CALL` targets in the assembled image land inside the loaded
range, and `CALL $1f21` at `0x004d` targets a record load address exactly. The
same walk decodes every 3.5 and 3.8 overlay (`2181_ph1`, `_v32`, `_v34`, `_mnp`,
`_omc`, `_ans`, `_boot`, ...), which base at `0x2000` and end on `f000`.

## The rate tables in the controller

The Z180 controller carries two contiguous 16-bit rate tables, adjacent in the
image (`i12600e`, 3.9.1, at `0x28b9`):

```
0028b9:   600  1200  2400  4800  7200  9600 12000 14400
0028c9: 16800 19200 21600 24000 26400 28800 31200 33600   <- V.34 and below
0028d9: 32000 34000 36000 38000 40000 42000 44000 46000
0028e9: 48000 50000 52000 54000 56000 58000 60000 65535   <- 56K, 2000 Hz steps
0028f9: 65535 65535 ...                                   <- reserved padding
```

The 56K table is 15 entries on a **2000 Hz grid** running past the usable
maximum to 60000, followed by a long run of `0xffff` -- the PM3's equivalent of
the `RESERV_n` slots in the Rockwell client. It appears from 3.7.2c3 (the first
build with 56K code in the data pump) and its shape never changes through 3.9.1.

**No V.90 rate grid is present anywhere.** The V.90 downstream rates are
k*8000/6 -- 28000, 29333, 30667, ... -- and none of the fourteen non-multiples of
2000 appears as ASCII in `m2d`, `pmexe` or `dp2`, nor as a contiguous 16-bit run
in any of them. The scattered individual 16-bit hits are at chance rates for
files of this size.

This is the opposite of what the Rockwell analogue client does, where V.90's
22-entry 1333 1/3 grid was grafted onto the K56flex index space as indices 30-51
(`docs/rockwell_v1456_firmware.md`). On the PM3 the only tabulated 56K grid is
the K56flex one, in builds that certainly do V.90.

Caveat: this shows the rates are not *tabulated*, not that the server cannot
reach them. The 1333 1/3 grid is arithmetically derivable and the tables here may
serve only configuration limits and display -- the `modulation` keyword region
carries `31200`/`28800` alongside them, which suggests exactly that role.

## Where V.90 landed in the data pump

Record 5 is the only record to change size after the host learns about V.90, so
3.8b15 (1740 words, `0x2852`) against 3.8.2 (2976 words, `0x26bb`) is the closest
thing to a controlled before/after the PM3 side offers.

It is a rewrite, not an extension: normalising internal `JUMP`/`CALL` targets to
record-relative, only 856 of 3.8b15's 1740 words survive into 3.8.2 (49%), and
the common prefix is just the 16-word register-save prologue every record opens
with. Two insertions dominate the +1236 growth:

| B range | net words | note |
|---|---:|---|
| `2796`-`297e` | +487 | |
| `2da0`-`2f74` | +467 | |
| `2b08`-`2bce` | +197 | |

The two large ones are variants of the same routine. Both walk a per-connection
DM block through `M3`, both gate on the field at offset 275 compared against
`$0100`, and both use the same packed-table idiom -- read field 277, shift right
one, test bit 0, then select `$00ff`/`$ff00` and a second mask by the parity --
i.e. two values packed per word, indexed by a counter. They differ in the second
mask (`$c000`/`$00c0` versus `$7d00`).

Counting the DM field selectors across releases separates old from new:

| selector | 3.7 | 3.8b15 | 3.8b19 | 3.8.2+ |
|---|---:|---:|---:|---:|
| `M0 = 275` | 20 | 20 | 27 | 36 |
| `M0 = 277` | 13 | 13 | 16 | 22 |
| `M0 = 278` | 14 | 14 | 19 | 39 |
| `M0 = 593` | 0 | 2 | 2 | 4 |
| `M0 = 594` | 2 | 2 | 2 | 19 |
| `M0 = 595` | 0 | 0 | 0 | 19 |

Field 275 and its `$0100` test are **not** new -- they go back to 3.7, before any
56K code -- so that gate is a pre-existing discriminator the new code reuses, not
a V.90 flag. What is new at 3.8.2 is state at offsets **594 and 595**, from zero
and two uses to nineteen each. (Offsets are only comparable within the 3.8b15+
era; record 0 and the record 1 base both move at 3.8b15, so the DM layout before
that is a different frame of reference.)

Record 5 is not the framing module: neither build loads a loop counter anywhere
in it, so the 4-versus-6 symbol mapping frame is not expressed here.

## Not yet established

- Whether record 5 is V.90-specific at all. It is where the post-V.90 growth
  landed and it gained new state, but nothing yet ties the inserted code to V.90
  rather than to unrelated 3.8.2 work.
- What the DM block at `M3` is, and what fields 594/595 hold.
- Where the mapping frame lives. It is in none of record 5, and the constants
  that would identify it have not been found in any record.
- Whether the V.90 rate grid is computed, and where.

## Boot probe

`tools/pm3_dsp_boot.py <dp2.bin>` now replays that native IDMA stream. Both the
K56flex-era 3.8b15 image and the V.90-era 3.9.1 image release at PM[0], execute
1,582 distinct PM words in a ten-million-cycle probe, clear the full internal
DM range, initialize shared state, and settle into the same resident scheduler
loop. This is a real cold boot of each data pump, not the earlier synthetic
reset trampoline. The remaining work is to drive SPORT samples and host/board
interrupts into the resident scheduler so negotiation reaches the divergent
record-5 code.

The matching controller probe now lives in `tools/pm3_z180_harness.c`.  It uses
the LGPL-2.1 [BinaryMelodies x80-emulator](https://github.com/BinaryMelodies/x80-emulator),
which implements the Z180 instructions and MMU rather than silently treating
`OUT0`/`IN0` as Z80 no-ops.  Build and run it with:

```
git clone https://github.com/BinaryMelodies/x80-emulator /tmp/x80-emulator
tools/pm3_z180_build.sh /tmp/x80-emulator
artifacts/pm3-z180/pm3-z180 --trace-io \
  artifacts/pm3-comos/3.9.1/6_m2d_2.2.i12600e.bin
```

With external inputs held at zero, `i12600e` executes 419 distinct logical
addresses, configures the MMU as `CBR=30 BBR=04 CBAR=84`, installs interrupt
vectors, enables both timers (`TCR=11`), and reaches a stable scheduler loop.
The complete pre-idle board interaction is only 55 writes and three reads.  The
external `0xC0/0xC1` pair is an ASIC index/data interface: startup selects
registers `C5` and `C6` and reads both through `C1`.  No DSP download occurs
with those inputs zero and no board interrupt.  `--timer-every` can inject the
otherwise absent PRT0 clock; this advances the controller's time base but does
not by itself request a modem boot.  The next boundary is therefore the PM3
board event/command presented through the indexed ASIC, not another DSP-side
guess.
