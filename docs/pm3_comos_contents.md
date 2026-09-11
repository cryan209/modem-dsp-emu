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
- **The config keywords are `auto` `v34` `flex` `v90`** (plus the separate
  `ccitt` `v23b2` `v23b3` set), with abbreviations `au` `v3` `fl` `v9`. `v90`
  and `v34` *are* keywords, from 3.8b15 on. K56flex and V.90 are separately
  selectable, mutually exclusive settings -- not two outcomes of one `flex`
  setting. See "The modulation code" below for the byte that carries the
  choice to the data pump.

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
config keywords — alongside the `modulation` keyword set `auto`, `v34`, `flex`,
`v90`. That display table is decoded in "The modulation code" below: it renders
a bitmask, and `flex` and `v90` set different bits in it.

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
the `RESERV_n` slots in the Rockwell client. It is present in **3.7**, in the
very first `m2d` ever shipped (`m2d_1.0`, file `0x26a0`), and its shape never
changes through 3.9.1 -- so the 2000 Hz grid predates the 56K data-pump code of
3.7.2c3 rather than arriving with it. Every one of the 28 `m2d` images carries
exactly one copy; only its file offset moves.

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
a V.90 flag. Offsets 594 and 595 go from zero and two uses to nineteen each --
but see the next section: they are **not** new state. They are the old 588/589
pair after the whole upper context shifted by six words.

Record 5 is not the framing module: neither build loads a loop counter anywhere
in it, so the 4-versus-6 symbol mapping frame is not expressed here.

## The per-channel context was relaid, not extended

The "new V.90 state at 594/595" reading above is wrong, and the way it is wrong
is the most useful K56flex-to-V.90 difference found so far.

The data pump addresses its per-channel context with one idiom: load a literal
field offset into `M0`, copy the context base into `I2`, `modify(I2, M0)`, then
load or store. Every `M0 = <literal>` in the assembled image is therefore a
field selector, and the census is directly comparable across releases:

| offset | 3.7 | 3.7.2c3 | 3.8b13 | 3.8b15 | 3.8b19 | 3.8.2 | 3.9.1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 567 / 573 | 23 | 3 | 3 | **16** | **16** | 16 | 16 |
| 568 / 574 | 1 | 1 | 1 | **22** | **22** | 23 | 23 |
| 574 / 580 | 2 | 5 | 1 | **42** | **42** | 41 | 41 |
| 579 / 585 | 0 | 1 | 2 | **33** | **33** | 35 | 35 |
| 588 / 594 | 0 | 2 | 2 | **17** | **19** | 19 | 19 |
| 589 / 595 | 0 | 1 | 2 | **17** | **19** | 19 | 19 |
| 602 / 608 | 0 | 4 | 2 | **7** | **7** | 8 | 8 |
| 603 / 609 | 0 | 0 | 0 | **6** | **6** | 6 | 6 |

Each row is one field, listed at its 3.8b15 offset and its 3.8.2 offset. The
counts carry across unchanged; only the offset moves, and it moves by exactly
**+6** for every field from 565 up. Fields 561-564 do not move. So 3.8.2 does
not add scattered new state: it **inserts six words at context offset 565** and
pushes the entire upper half of the context up by six.

The same conclusion falls out of the field initializers. Extracting every
`(offset, stored literal)` pair from the two images gives value multisets that
are equal under the +6 shift -- for example the scheduler-state field:

```
3.8b19  off 568: 0000 x5  0001 x2  0002 x1  0003 x1  0004 x1  0007 x2  000f x1
                 0010 x1  003c x1  0046 x1  0050 x1  005a x4  005b x1  0064 x1
3.9.1   off 574: 0000 x5  0001 x2  0002 x1  0003 x1  0004 x1  0007 x2  000f x1
                 0010 x1  003c x1  0046 x1  0050 x1  005a x4  005b x1  0064 x1
```

and the 588/589 pair that the earlier reading mistook for new V.90 state:

```
3.8b19  off 588: 0001 x5  0002 x5  0003 x7        (a 1..3 selector)
3.9.1   off 594: 0001 x5  0002 x5  0003 x7
3.8b19  off 589: 19 distinct address-shaped values
3.9.1   off 595: 19 distinct address-shaped values, individually shifted
```

588/589 is a dispatch pair -- a small 1..3 code beside an address-shaped word --
written 19 times in both builds. Only the addresses differ, which is what code
relocation does. Nothing about it is V.90-specific.

### The context has been relaid four times, and 3.8.2 is the largest move

That 588/589 pair is a unique fingerprint: in every release it is the only
adjacent field pair in 555-612 whose two selector counts are both >= 10 and
within 2 of each other. Following it dates each relayout:

| release | pair at | shift | context grows by |
|---|---:|---:|---:|
| 3.7 | 581/582 | — | — |
| 3.7.2c3 | 585/586 | +4 | 4 |
| 3.8b13 | 586/587 | +1 | 1 |
| 3.8b15 | 588/589 | +2 | 2 |
| 3.8b19 | 588/589 | 0 | 0 |
| 3.8.2 … 3.9.1 | 594/595 | +6 | 6 |

The per-channel context grew **13 words** across the 56K era, in four steps, and
the +6 at 3.8.2 is the biggest. 3.8.2 is also where record 5 grows by 793 words
and where the Z180 controller goes from `m2d` generation 2.1 to 2.2 — three
independent measurements landing on the same release boundary. 3.8b19, by
contrast, changes no offsets at all: it adds code against the existing layout
(275/277/278 selector counts rise, offsets do not move).

### What the six inserted words are

Nothing in the data pump selects them. Offsets 565-570 have a selector count of
**zero** in 3.8.2 and 3.9.1, and no initializer writes them. The +6 is a hole
the DSP never touches — so the six words are host- or controller-owned, written
across the IDMA boundary by `m2d`, and the data pump only pays for the layout.
That is consistent with the `--channel-state` finding above: the V.90-era
host/channel ABI is wider than the K56flex one.

Two fields below the insertion do change behaviour. Offsets 559 and 560 go from
1 and 0 selectors to 3 and 3, and they are always written as a triple with
597 (= the old 591), from a value computed by `CALL $1CB3`:

```
1752: CALL $1CB3
1754: M0 = 560
1755: AX1 = AR
1756: modify(I2, M0)
1758: M0 = 597
175a: DM(I2,M2) = AX1
```

So the one genuinely new per-channel behaviour at 3.8.2 is a third write site
into an existing 559/560/597 measurement triple — not the 594/595 pair.

### A ruled-out candidate for the mapping frame

The only unrolled straight-line repeats anywhere in the data pump are two runs
of 16 identical `071200` words and one of 15, and they sit in **every** release
from 3.7 onward, in the same three places, at the same lengths:

| release | run A | run B | run C |
|---|---|---|---|
| 3.7 | `1d2c` x15 | `2da3` x16 | `2e03` x16 |
| 3.8b15 | `2349` x15 | `3890` x16 | `38f0` x16 |
| 3.8.2 / 3.9.1 | `21b2` x15 | `3beb` x16 | `3c4b` x16 |

A 16-deep unroll that predates all 56K code and never changes length is not a
4-versus-6 symbol mapping frame. Combined with "record 5 loads no loop counter",
the mapping frame is now ruled out of both the obvious hiding places.

## Not yet established

- Whether record 5 is V.90-specific at all. It is where the post-V.90 growth
  landed and it gained new state, but nothing yet ties the inserted code to V.90
  rather than to unrelated 3.8.2 work.
- What the DM block at `M3` is. (Fields 594/595 are answered below: they are
  the relocated 588/589 dispatch pair, not new V.90 state.)
- Where the mapping frame lives. It is in none of record 5, and the constants
  that would identify it have not been found in any record.
- Whether the V.90 rate grid is computed, and where.
- What the six words inserted at context offsets 565-570 at 3.8.2 hold. The
  data pump never selects them.

## Boot probe

`tools/pm3_dsp_boot.py <dp2.bin>` now replays that native IDMA stream. Both the
K56flex-era 3.8b15 image and the V.90-era 3.9.1 image release at PM[0], execute
1,582 distinct PM words in a ten-million-cycle probe, clear the full internal
DM range, initialize shared state, and settle into the same resident scheduler
loop. This is a real cold boot of each data pump, not the earlier synthetic
reset trampoline. The remaining work is to drive SPORT samples and host/board
interrupts into the resident scheduler so negotiation reaches the divergent
record-5 code.

That host boundary is now partially reproduced with `--channel-state`. The
resident image enables only the timer (`IMASK=1`); SPORT0, SPORT1, and raw board
interrupt injections are intentionally ignored at cold boot. The timer walks
ten 612-word channel contexts starting at the pointer in `DM[0x2021]`.

The host-visible layout itself changed at V.90:

| image generation | init/event field | scheduler-state field |
|---|---:|---:|
| `m2c_2.1` (3.8b15 K56flex) | context `+406` | context `+568` |
| `m2c_2.2` (3.9.1 V.90) | context `+575` | context `+574` |

This was measured, not inferred: sweeping every field in one context finds only
three values that expand cold-boot coverage, and staged writes to the fields
above make states `0x06`, `0x46`, and `0x5a` enter the generation-specific
large modem block (`PM 0x2852` in 2.1, `PM 0x26bb` in 2.2). State `0x06`
executes 113 words in either version of that block; states `0x46` and `0x5a`
execute 105. Thus the two images can now be driven through homologous live
scheduler paths for an instruction/state comparison. For example:

```
tools/pm3_dsp_boot.py artifacts/pm3-comos/3.8b15/6_m2c_2.1.dp2.bin \
  --cycles 200000 --channel-state 0x46 --frame-cycles 300000
tools/pm3_dsp_boot.py artifacts/pm3-comos/3.9.1/7_m2c_2.2.dp2.bin \
  --cycles 200000 --channel-state 0x46 --frame-cycles 300000
```

The first concrete K56flex-to-V.90 architectural difference is therefore not a
SPORT format: it is the host/channel ABI. The old layout's compact event field
at `+406` moves into a new control cluster around `+574`, alongside the new
fields `+594/+595` identified in the static diff. SPORT samples become relevant
only after this timer-driven host state activates the modem path.

An attempted direct PCM capture also closed off a tempting false lead. The
resident image reads/writes ADSP I/O locations `0x0c0/0x0d0`, but the write at
PM `0x00e8` executes only once during initialization. Repeated external IRQ2
edges and all three activated scheduler states leave `0x0d0` at zero. These
locations are peripheral setup, not the recurring line-sample interface. The
actual tone path therefore requires the ComOS `mdp_cntl` `CIO_*` binding that
installs the channel's sample buffers; forcing a DSP state alone executes modem
control code but does not attach a bearer or create audio.

## The modulation code

`pmexe` is a headerless text carve, but its load base is recoverable: the two
adjacent strings `K56Flex Modulation` and `V.90 Modulation` are 19 bytes apart,
so a `push $imm32` whose immediate is 19 less than another push's immediate
pins the base. Exactly one candidate pair in each image gives a consistent
answer, and it is the same in both: **base `0x103000`**, runtime = file +
`0x103000`. The two pushes are `0x28` bytes apart -- the same function.

### It is a bitmask, at port-struct offset `0x68`

That function (`0x0014edb0` in 3.9.1) is a renderer: it walks a 32-bit word in
`%edi` and prints a `, `-separated list. Each bit has one string:

| bit | string |
|---:|---|
| `0x00000001` | `V.23 (B2)` |
| `0x00000002` | `V.23 (B3)` |
| `0x00000004` | `V.34 Modulation` |
| `0x00000008` | **`K56Flex Modulation`** |
| `0x00000010` | **`V.90 Modulation`** |
| `0x00010000` … `0x40000000` | `MSE=0x2500` … `MSE=0x4000` (bits 16-30) |

The MSE ladder is absent in 3.8b15 -- that build's renderer stops after the
V.90 bit -- so the MSE threshold bits are a later addition to the same word.

All three of its callers push `0x68(%esi)`, so the word lives at **offset
`0x68` of the port structure**. One caller is in `mdp2_init_modem`, logging
`M%d: Default Modem Configuration: %s` (the `%d` is the modem index at
`0xd1(%edi)`, which is what the earlier `movzbl 0xd1(%eax)` turned out to be).

So K56flex and V.90 are **independent bits**, not two outcomes of one setting.

### The keyword table sets exactly one bit

`set <port> modulation <kw>` is parsed from a `{abbrev*, full*, code}` table of
12-byte entries at file `0x1325c0`, terminated by a `0xffff` sentinel:

| abbrev | keyword | code |
|---|---|---:|
| `au` | `auto` | 0 |
| `v3` | `v34` | 1 |
| `fl` | `flex` | 2 |
| `v9` | `v90` | 3 |

The handler at `0x0019b27b` clears all three bits, then sets one from the code:

```
83 66 68 fb    and  $0xfb,0x68(%esi)   ; clear V.34
83 66 68 f7    and  $0xf7,0x68(%esi)   ; clear K56flex
83 66 68 ef    and  $0xef,0x68(%esi)   ; clear V.90
83 fb 01       cmp  $1,%ebx            ; v34  -> orb $0x04,0x68(%esi)
83 fb 02       cmp  $2,%ebx            ; flex -> orb $0x08,0x68(%esi)
83 fb 03       cmp  $3,%ebx            ; v90  -> orb $0x10,0x68(%esi)
```

Clear-then-set-one makes the three mutually exclusive. `auto` (code 0) sets
none.

### The byte that reaches the data pump

`mdp2_init_modem` turns that bitmask into the default-config packet described
in the next section. The three tests are consecutive and each writes a complete
TLV, then jumps past the others:

```
c6 45 c0 06    packet[0] = 0x06          ; DEFAULT MODEM CONFIG
c6 45 c1 00    packet[1] = 0             ; TLV count
bb 02          len = 2

f6 46 68 04    testb $0x04,0x68(%esi)    ; V.34
   count=1, TLV = 03 01 08,     len = 5
f6 46 68 08    testb $0x08,0x68(%esi)    ; K56flex
   count=1, TLV = E5 02 6D 03,  len = 6
f6 46 68 10    testb $0x10,0x68(%esi)    ; V.90
   count=1, TLV = E5 02 6D 02,  len = 6
```

So the modulation code is the last byte of an **extended `E5` record with
sub-option `0x6D`**:

| setting | command frame on the wire to `m2d` |
|---|---|
| `auto` | *(TLV count 0 -> `(config) No default configuration`)* |
| `v34` | `00 06 01 03 01 08 FF` |
| `flex` | `00 06 01 E5 02 6D 03 FF` |
| `v90` | `00 06 01 E5 02 6D 02 FF` |

`E5 02 6D 03` = K56flex and `E5 02 6D 02` = V.90. This extends the option set
recovered earlier (`04 01 …`, `0e 01 …`, `10 01 …`, `0b 01 …`, `e5 02 61 …`)
with sub-option `0x6D`; the `0x61` family in the same function is driven by the
flag bytes at `0x6a`/`0x6b` and carries values `0x19`-`0x27`.

`auto` emitting no TLV at all is why a zero TLV count is a legal thing for
ComOS to produce: it means "no host-imposed ceiling, let the data pump
negotiate", and the data pump's `(config) No default configuration` is the
acknowledgement of that, not an error.

### The host could ask for V.90 a full release before the DSP could do it

These four sites are **byte-identical in 3.8b15, 3.8, 3.8.2 and 3.9.1**:

| release | V.34 | K56flex | V.90 |
|---|---|---|---|
| 3.8b15 | `03 01 08` | `E5 02 6D 03` | `E5 02 6D 02` |
| 3.8 | `03 01 08` | `E5 02 6D 03` | `E5 02 6D 02` |
| 3.8.2 | `03 01 08` | `E5 02 6D 03` | `E5 02 6D 02` |
| 3.9.1 | `03 01 08` | `E5 02 6D 03` | `E5 02 6D 02` |

The complete host-side V.90 selection path -- keyword, bit, TLV and display
string -- shipped at **3.8b15**, and never changed again. But 3.8b15's data
pump is the one whose record 5 has not yet grown and whose channel context has
not yet been relaid; both of those happen at **3.8.2**. The host learned to ask
for V.90 one release before the DSP could deliver it, which is the mirror image
of the 3.7.2c3 situation, where the DSP gained 56K code before the host had any
way to ask for it.

## Looking for the V.8 / INFO difference: there isn't one in the data

The download image carries DM records as well as PM records, and they are
initialized constant tables -- the place any V.8 CM/JM template or INFO message
layout would have to live. The first **58** DM records, covering `DM 0x2000`
through `0x2667` (1639 words), keep the same address and the same length in
every build from 3.8b15 on, so they can be compared with no relocation to undo.
`tools/pm3_dsp_states.py --data` does it:

```
aligned prefix: 58 DM records, DM 2000..2667 (1639 words)
changed: 2
   DM 21a4  101 words, 91 differ
   DM 242e   15 words, 15 differ
```

`DM 0x242e` is not a semantic change: all 15 of its words are PM addresses and
every one moves by exactly +34, which is code relocation. So across the whole
K56flex-to-V.90 boundary **exactly one initialized table changes meaning**, and
1624 of the 1639 comparable words are byte-identical.

That is a strong negative answer to "which V.8 / INFO fields does flex use that
V.90 does not": in the data pump's initialized data, **none**. Whatever V.8 and
INFO tables the engine carries, K56flex and V.90 share them byte-for-byte. The
two modulations are not distinguished by their signalling data here.

(The alignment matters. The full record lists diverge at `DM 0x2668`, so any
comparison above that address is comparing unrelated objects; an earlier naive
pass "found" a third changed record at `DM 0x3217` that is simply a different
table in the two builds.)

## The one table that did change is the scheduler's state dispatch

`DM 0x21a4` is 101 words of PM addresses with one address repeated in every
unused slot -- a jump table with a default. The default is the common return
point: the state handlers end `JUMP $045F` in 3.9.1, and `0x045f` is exactly
the value filling the table. Indexing it by the channel's scheduler-state field
(574 in the V.90 layout, 568 in the K56flex layout) reproduces the three states
the `--channel-state` probe already found activating -- `0x06`, `0x46`, `0x5a`
are populated entries 6, 70 and 90 -- which is what identifies the table.

`tools/pm3_dsp_states.py --states` dates every state the engine implements:

| state | 3.8b15 | 3.8b19 | 3.8.2 | 3.9.1 |
|---|---|---|---|---|
| *(default)* | `042b` | `042f` | `045f` | `045f` |
| 0 | `01d2` | `01d2` | `01d2` | `01d2` |
| 1 | `025e` | `025e` | `025e` | `025e` |
| 2 | `0270` | `0270` | `0270` | `0270` |
| 5 | `0328` | `0328` | `0328` | `0328` |
| 6 | `0427` | `042b` | `045b` | `045b` |
| 14 | `032b` | `032b` | `032b` | `032b` |
| 15 | `0381` | `0381` | `0381` | `0381` |
| 16 | `0405` | `0405` | `0435` | `0435` |
| **18** | — | — | **`0409`** | **`0409`** |
| 60 | `01bd` | `01bd` | `01bd` | `01bd` |
| 70 | `02e0` | `02e0` | `02e0` | `02e0` |
| 80 | `0325` | `0325` | `0325` | `0325` |
| 90 | `0180` | `0180` | `0180` | `0180` |

Twelve states are stable across the boundary. **V.90 adds exactly one: state
`0x12` (18), at 3.8.2.** Every other entry either does not move or moves by the
relocation delta.

This is confirmed independently, from a measurement taken a different way. The
field-initializer census of the scheduler-state field lists the literals each
build ever stores into it, and the V.90 build's list contains one value the
K56flex build's does not:

```
3.8b19  field 568: 0000 x5 0001 x2 0002 0003 0004 0007 x2 000f 0010      003c ...
3.9.1   field 574: 0000 x5 0001 x2 0002 0003 0004 0007 x2 000f 0010 0012 003c ...
```

`0012` appears in exactly the build where the dispatch table gains index 18.

### What state 0x12 does

It is a timed wait, and it waits on the deadline that the host-command receiver
arms:

```
0409: AY1 = $0008
040b: M0 = 585                    ; flags
0410: AR = AX1 AND $0008
0412: IF NE JUMP $045F            ; same gate as command reception
0414: AR = AX1 AND $2000
0416: IF NE JUMP $0423
0418: M0 = 607                    ; the deadline field
041b: CALL $0E34                  ; expired?
041e: IF EQ JUMP $045F            ; no -> stay in state 0x12
...
042e: M0 = 574
0433: DM(I2,M2) = $0010           ; yes -> state 0x10
```

Field 607 is the word the receiver's caller loads from `DM[0x26C1]` -- the
`0x0BB8`/`0x3A98` command-completion timeout recovered above. So state `0x12`
is "wait for the host command to complete, then fall into state `0x10`", and
`0x10` is a state both builds already had.

States 1, 2 and 60 are the same shape against a different timer (field 584 and
the same `CALL $0E34`), so `0x12` joins an existing family of timed waits
rather than introducing a new mechanism. For orientation, the other handlers
open as:

| state | entry | first call |
|---|---|---|
| 5 | `0328` | `CALL $0578` |
| 6 | `045b` | `CALL $26BB`, then `CALL $2FDB` |
| 80 | `0325` | `CALL $07BC` |

State 6 calling `$26BB` is the link back to record geometry: `0x26bb` is the
base of the record that grew by 793 words at 3.8.2. So the release that adds
state `0x12` is the same release that rewrites the block state 6 runs.

### What this does not answer

None of this locates the V.8 CM/JM or INFO message tables themselves. The
result is a bound, not an identification: whatever they are, they are inside
the 1624 words that did not change, so they are shared. Naming them still
needs the signalling code to be followed, which the board-event gate still
blocks from being observed running.

## Recovered ComOS-to-data-pump initialization sequence

The relocated `mdp_cntl` jump table is stored at `pmexe` file `0x472b8`
(runtime `0x14a2b8`). Its control values 4 through 17 dispatch to the basic
blocks at file `0x472f0..0x4753c`. The modem-initialization case calls
`mdp2_init_modem` at file `0x4e594`. For an otherwise default configuration it
performs these IDMA DM writes, where `base = DM[0x2021]` for channel zero:

```
base+0x50 = 0x06             command: default modem configuration
base+0x51 = TLV count
base+0x52...                 TLVs: <option, length, value...>
base+0x4f = packet byte count
base+0x4e = 0xffff           packet terminator/ownership marker
base+0x23f = 0x002c          commit event
```

`tools/pm3_dsp_boot.py --comos-config` reproduces this sequence. For example,
`--comos-config 04,01,02` produces the firmware-confirmed log:

```
M0: host_cmd state 1 DEFAULT MODEM CONFIG - SENDCMD
M0: (sendcmd) Sending 00 06 01 04 01 02 ff: Total of 7 bytes
M0: h2m_cmd: 00 06 01 04 01 02 ff
```

The leading zero is added by the data pump and `ff` terminates the command.
The recovered option encodings emitted by ComOS include `04 01 02/03`,
`0e 01 00/01/02`, `10 01 00/01/02`, `0b 01 <value>`, and extended
`e5 02 61 <value>` records. A zero TLV count is rejected explicitly as
`(config) No default configuration`.

This establishes the full first leg of bring-up. The accepted frame stops at
`h2m_cmd` because it crosses to the separately booted `m2d` Z180 controller.
The `0xc0/0xc1` activity is ASIC index/data control, not a PCM sample pair and
not the command byte stream.

### What the data pump does with the modulation code

Neither image ever compares against `0xE5` or `0x6D`. There is no `cp $E5` in
the Z180 code region and no `AX = $00E5` / `$006D` immediate anywhere in the
assembled data pump. So `E5` is not a case in a dispatch ladder: it is a
**"set extended parameter" record**, `E5 <len> <param> <value>`, and `0x6D` is
a parameter id, looked up rather than branched on. That is consistent with the
other extended records ComOS emits, `E5 02 61 <v>` for `v` in `0x19`-`0x27`,
which are the same opcode with a different parameter id.

The data pump's side of the handshake is fully decoded. The host-command
receiver is the routine at **PM `0x08EB`**, called from exactly one site,
`0x0335`, with the channel context base in `AR`:

```
032b: AY1 = $0008
032d: M0 = 585                    ; flags field
0330: AX1 = DM(I0,M2)
0331: AR = AX1 AND AY1
0333: IF NE JUMP $0364            ; bit 3 set -> do not poll for commands
0335: CALL $08EB                  ; receive host command
0338: IF EQ JUMP $0364            ; nothing was waiting
```

so command reception is gated on **bit `0x0008` of context field 585** being
clear. The receiver itself:

```
08fd: AX1 = $FFFF
08ff: M7 = 78                     ; context+0x4e, the ownership marker
0902: AY1 = DM(I6,M6)
0903: AF = AY1 - AX1
0904: IF EQ JUMP $0907            ; marker == 0xffff -> a packet is waiting
0905: AR = $0000 ; JUMP $0940     ; otherwise return 0

0907: M0 = 79                     ; context+0x4f, the byte count N
090a: MR0 = DM(I2,M2)
090b..0916:  MR1 = ceil(N / 2)    ; N + sign, >> 1, +1 if N is odd
0918: MY1 = $26C2                 ; staging buffer
091c: M0 = 80                     ; context+0x50, the packet
0923: DO $092B UNTIL NOT CE       ; copy MR1 words
0935: DM(I6,M6) = $0000           ; ack: clear the ownership marker
```

Two things follow. First, the packet is **two bytes per 16-bit DM word**: the
field at `+0x4f` is a byte count, and the receiver halves it to get the number
of words to copy. Second, the command never stays in the channel context -- it
is copied straight into a global staging buffer at **DM `0x26C2`** and the
context slot is released.

The receiver then picks a deadline constant from the second staged word:

```
092c: AY1 = DM($26C3)
092d: AX1 = $0040
092f: IF NE JUMP $0933
0930: DM($26C1) = $0BB8           ;  3000
0933: DM($26C1) = $3A98           ; 15000
```

and back in the caller that constant is added to the free-running counter at
DM `0x26B8` and compared against `$7530` (30000) before being stored into a
context field -- i.e. `DM[0x26C1]` is a per-command completion timeout, short
for the command whose second word is `0x0040` and long for everything else.
On a successful receive the caller also drives the scheduler-state field 574
through `CALL $38C8`, which is what actually routes the staged packet to a
handler.

So the answer to "how does the DSP use the modulation code" is: **it doesn't
decode it in the receiver at all.** The receiver is a transport. It moves the
whole frame into `0x26C2`, arms a timeout, and advances the channel's scheduler
state; whichever state the command selects is what later walks the TLVs.

Under the two-bytes-per-word packing the receiver requires, the frame
`06 01 E5 02 6D 03` stages as

```
DM[0x26C2] = 0x0601      command 6, one TLV
DM[0x26C3] = 0xE502      extended record, length 2
DM[0x26C4] = 0x6D03      parameter 0x6D = 3  (K56flex; 0x6D02 = V.90)
```

so the modulation code arrives as a single 16-bit word. That last step is an
inference from the packing arithmetic, not yet an observation -- see below.

#### The receiver has not been caught running

`tools/pm3_dsp_boot.py --comos-config` writes the packet **one byte per DM
word**, which does not match the `ceil(N/2)` the receiver computes. Correcting
the packing does not help, though, because the receiver is not reached at all:
with either packing, and under each of the three activatable scheduler states
(`0x06`, `0x46`, `0x5a`), DM `0x26C2` stays zero and the ownership marker is
never acked. Instead the packet area `+0x4e`..`+0x51` is cleared wholesale
somewhere between 10,000 and 14,000 cycles after the write -- the commit event
at `+575` survives that clear, so it is not the receiver's ack, which touches
only `+0x4e`.

This is the same gate as before: without the PM3 board event that builds the
receive-ring descriptors, the channel never enters a state that polls for host
commands. The modulation code's path through the data pump is therefore
established statically end to end, and still unconfirmed dynamically.

The Z180 receive transport is now identified from the interrupt handler at
`04ac`. External interrupt 0 with `FE` bits 7 and 5 set selects receive service.
The handler reads bytes from port `F0` into its software ring and tests port
`F5` bit `0x20` after each byte: clear means another FIFO byte is available;
set means the hardware FIFO is empty. It then updates the byte count at `818e`,
stores the ring pointer, acknowledges with `OUT (FE),80`, and returns. The
wire-side sequence is therefore:

1. Place the complete frame in the ASIC receive FIFO.
2. Assert external INT0 with `FE=a0`.
3. Return bytes on successive `IN (F0)` operations.
4. Keep `F5 & 20` clear until the final byte has been read, then set it.
5. Let the controller task consume its software receive ring.

The harness implements this with `--rx-hex`; `--rx-at` defers assertion until a
chosen instruction count. It also reports the receive count and ring metadata.
With the current zero-input board model, a seven-byte default-config frame is
drained correctly and the count at `818e` becomes seven, but the receive-ring
descriptor words remain zero. The concrete remaining boot dependency is the
PM3 board event that creates those descriptors. Configuration and dial/answer
frames—and therefore tone output—must follow that gate.

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
