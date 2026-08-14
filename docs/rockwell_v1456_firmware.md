# Rockwell V1456VQE-R firmware (Dynalink/Askey V.90 upgrade kit)

Findings from `~/Downloads/auv90eall`, the Askey Australia field-upgrade kit for
the external **Rockwell V1456VQE-R** "VoiceDesk 56 Pro" analogue modem, 1998.
Nothing here is Eicon, ADSP-2181, or digital-side. It is the *analogue client*
end of V.90 -- the peer of what this project transmits -- and it is useful for
exactly that reason: it is a shipped, third-party implementation of the side we
only ever see across the line, and it exists in two builds that differ only by
protocol.

Everything below was established from the images themselves. Where a claim rests
on inference rather than a decode, it says so.

## The kit

Three Motorola S3 records (32-bit addresses), converted to flat binaries by
merging on load address:

| File | Load range | Size | Contents |
|---|---|---:|---|
| `LOAD4IN1.S37` | 0x8000-0x9797 | 6,039 | "Flash loader - Rev 17", runs from RAM |
| `kewsast3.s37` | 0x0-0x1FFA4 | 130,107 | K56flex v2.010, full flash image |
| `vewsast4.s37` | 0x0-0x20000 | 130,101 | V.90 v2.200, full flash image |

`readme.txt` gives the field procedure: `AT**` puts the modem in download mode,
the loader is sent as ASCII, then the payload image. Reverting to K56flex is the
same procedure with the other image, so the two are drop-in equivalents for the
same hardware -- which is what makes the diff meaningful.

The loader is a flash-part encyclopedia: ~40 device strings with manufacturer and
device IDs (AMD, Atmel, ISSI, SST, Winbond, Macronix, SGS-Thomson), 1/2/4 Mbit
detection for both the device and the code, and `Code doesn't match hardware`,
so the payload carries a size/hardware token the loader checks.

## Two processors, two download images

Both images contain `DRAM`-tagged section tables. They are not one table. There
are two, describing different address spaces with **different unit sizes**, and
the distinction is load-bearing.

### Table 1 (V.90 image at 0x7780): host 6502 code

Record layout is an 8-byte header then five 32-bit LE fields -- `start`, `end`,
`length`, `payload offset`, `x` -- then a NUL-terminated name. `end - start`
equals `length` exactly: **one byte per unit**. Payload offsets are relative to
the table start.

| Section | Load range | Length |
|---|---|---:|
| `RAM_Intercept_Vectors` | 1000-1400 | 0x400 |
| `V8X_RAM_code` | 1400-2335 | 0xF35 |
| `SPK_RAM_code` | 1400-2FB0 | 0x1BB0 |
| `VPCM_RAM_code` | 31F0-3B20 | 0x930 |
| `PCM_Common_RAM` | 1400-31E2 | 0x1DE2 |
| `PORInit_RAM_code` | 3F00-3FD6 | 0xD6 |
| `Common_RAM_code` | 3B20-3ECE | 0x3AE |
| `DownDefVec_RAM_code` | 3FDE-3FFF | 0x21 |

`RAM_Intercept_Vectors` decides it. Its payload at 0x7966 is

```
4C 08 D1  4C 20 3B  4C A2 D6  4C AC D6  4C 3C 3B  4C 48 3C ...
```

a table of 6502 `JMP abs` instructions. So `$1000-$3FFF` is **host RAM**, these
sections are 6502 overlays, and the controller runs from RAM loaded out of flash
at power-on (`PORInit_RAM_code`), patched through the `$1000` jump table. Several
overlays share `$1400`, so they are alternates, not a single resident image.

Consequence for reading the image: code that looks resident is not necessarily
resident. `PORInit`'s payload offset 0x57DD lands at 0xCF5D, inside what looks
like ordinary flash code -- that whole span is overlay payload.

### Table 2 (V.90 image at 0x1CAC0): the DSP, 32-bit words

Same record layout, but the payload advances **four bytes per unit**. The offset
chain closes exactly, six times:

| Section | Load range | Units | Offset | Offset + 4*units |
|---|---|---:|---:|---:|
| `B0_RAM_CODE_VECTORS` | 7000-7013 | 0x13 | 0x188 | 0x1D4 |
| `PCMRX_RAMCODE` | 7013-79E2 | 0x9CF | 0x1D4 | 0x2910 |
| `SPKPHONE_RAMCODE` | 7013-7144 | 0x131 | 0x2910 | 0x2DD4 |
| `BAUD_RAMCODE` | 7AA6-7BDE | 0x138 | 0x2DD4 | 0x32B4 |
| `PCMROM_RAM_SECTION` | 79F6-7AA6 | 0xB0 | 0x32B4 | 0x3574 |
| `ENCODER_RAMCODE` | 7BDE-7BEC | 0xE | 0x3574 | 0x35AC |
| `GENERAL_DOWNLOADCODE` | 7BF0-7BFA | 0xA | 0x35AC | -- |

Each row's `offset + 4*units` is the next row's offset. Byte-lane entropy over
the payload confirms the period independently -- stride 4 is the only one that
separates:

```
stride 2  lanes 5.36 5.34                 spread 0.03
stride 3  lanes 5.59 5.64 5.67            spread 0.08
stride 4  lanes 4.85 5.51 4.52 4.21       spread 1.30
stride 5  lanes 5.60 5.61 5.56 5.61 5.61  spread 0.05
```

It is not 16-bit words zero-padded to 32 (no lane is near-zero entropy) and not
address/data pairs (the high halves `001A, 1A07, 1807, 7C21` are not monotonic).
1698 of 2511 words are distinct, so it is code, not tables.

So the DSP is a **32-bit-instruction machine with a 4K-word space at
`$7000-$7FFF`**, about 3 K words occupied, with `SPKPHONE` overlaying `PCMRX`.

That width rules out the usual suspects: ADSP-21xx and Motorola 56000 are 24-bit
program words, TI C5x and Lucent DSP16 are 16-bit. A 32-bit word with
functional-block section names reads as a wide-microword data-pump engine rather
than a general-purpose DSP running assembly, which is what Rockwell's data pumps
were. **The core is not identified.** There is no public ISA for it and none is
recoverable from these images without an oracle. Treat the DSP payload as opaque.

K56flex has the same two-table layout and the same `$7000` space with
`B0_RAM_CODE_VECTORS` at the same `7000-7013`; it names only that one section and
ships the rest unnamed.

## The host CPU is a 6502 superset, not an R65C02

The controller decodes cleanly as 65C02 -- `4C` `JMP`, `4A` `LSR A`, `60` `RTS`,
Rockwell's `RMB`/`SMB`/`BBR`/`BBS` zero-page bit ops all present and sensible --
but four opcodes in the `x2` column are re-purposed, including two the 65C02
itself defines. Any disassembly that assumes a stock R65C02 desynchronises.

| Opcode | Length | Encoding | Meaning |
|---|---:|---|---|
| `E2` | 5 | `E2 lo hi mask rel8` | branch if bit **reset** (absolute) |
| `F2` | 5 | `F2 lo hi mask rel8` | branch if bit **set** (absolute) |
| `C2` | 4 | `C2 mask lo hi` | clear bit (absolute) -- `RMB` abs |
| `D2` | 4 | `D2 mask lo hi` | set bit (absolute) -- `SMB` abs |

`rel8` is relative to the byte after the instruction, as for `BBR`/`BBS`. Note
the operand order differs between the two families: address-first for the
branches, mask-first for the set/clear.

**Evidence for the shapes.** Counting bytes that fit each template (plausible RAM
page, power-of-two mask) separates these four from every other `x2` opcode:

```
        mask-first (4B)   addr-first (5B)     (vewsast4)
 C2        370  44%           18   2%
 D2        373  57%           16   2%
 E2         48   7%          342  50%
 F2         24   4%          310  51%
 all others  <=8  <=2%       <=9  <=3%
```

Structurally, `E2` self-loops exist with `rel = $FB`, exactly -5 -- only a 5-byte
instruction makes that a self-loop. `E2 0A 06 02 FB` at `$6F65` spins on `$060A`
bit 1; `E2 0D 06 02 FB` at `$7DDC` spins then writes a byte to the data port at
`$0610`. Branch targets land on instruction boundaries throughout (`$F4C0` ->
`$F4D0` = `SEC`; `$11C7A` -> an `RTS`).

**Evidence for the polarity**, from `kewsast3`. One routine clears the gate bits
and zeroes the data they gate:

```
6DE6  C2 02 5B 01   RMB  #$02,$015B
6DEA  C2 02 5E 01   RMB  #$02,$015E
6DEE  AD 5C 01      LDA  $015C
6DF1  29 08         AND  #$08          ; clears bit 2 of $015C
6DF3  8D 5C 01      STA  $015C
6DF6  A9 00         LDA  #$00
6DF8  8D 7E 86      STA  $867E         ; zero the DSP table's count
6DFB  60            RTS
```

and the `AT&V1` printer gates that same table on those same bits:

```
2BCB  E2 5B 01 02 16   BBR  $015B,#$02 -> $2BE6   ; skip the table
2BD0  E2 5C 01 04 11   BBR  $015C,#$04 -> $2BE6
2BD5  A2 00            LDX  #$00
2BD7  AC 7E 86         LDY  $867E                 ; the count just zeroed
2BDA  BD 7F 86         LDA  $867F,X
```

Bits cleared and data zeroed on one side, branch-away on the other: the branch is
taken when the bit is reset. The write side is confirmed at `$1CC1`, `LDA #$00 /
STA $015B / D2 01 5B 01` -- zero the byte, then set bit 0.

`B2` is also a three-byte extension, not `LDA (zp)`: it appears in sequential runs
like `B2 70 18 / B2 71 19 / B2 72 1A / B2 73 1B / RTS`, which reads as a move.
**Not decoded.** The rest of the `x2` column (`12 32 52 72 92`) is unexamined and
should be assumed suspect.

The flash loader contains no `E2` at all, so the loader alone will not reveal the
extension set.

## The rate tables

One pointer table per image maps a **rate index** to an ASCII rate string:
K56flex base `$2F28`, V.90 base `$2E76`, 16-bit LE pointers, index 0 = `"300"`.
Indices 0-29 mean the same thing in both builds:

```
0:300  1:600  2:1200 3:2400 4:4800 5:9600 6:12000 7:14400 8:7200
9:16800 ... 16:33600          (note 7200 out of order at index 8)
17..29: 32000 34000 ... 54000 56000     K56flex: 13 rates, 2000 Hz steps
```

In `kewsast3`, indices **30 and 31 are literally the strings `RESERV_1` and
`RESERV_2`**. In `vewsast4` those reserved slots are the start of a 22-entry
block:

```
30:28000 31:29333 32:30667 33:32000 34:33333 35:34667 36:36000 37:37333
38:38667 39:40000 40:41333 41:42667 42:44000 43:45333 44:46667 45:48000
46:49333 47:50667 48:52000 49:53333 50:54667 51:56000
```

22 rates on 8000/6 = 1333 1/3 steps against K56flex's 13 on 8000/4 = 2000 -- the
6-symbol versus 4-symbol frame difference, visible directly in the index space.
Rockwell did not renumber: the K56flex indices stay live in the V.90 build, which
is dual-mode, and where the grids coincide the V.90 entries **share the K56flex
string pointers** (index 33 and index 17 both point at `$2F3B`; likewise
36/40/42/45/48/51 reuse 19/21/23/25/27/29). Only the seven non-multiples of 2000
needed new strings.

### The code that indexes them

Exactly one consumer per image, a `PrintRate` subroutine:

```
2C02  C9 FF     CMP #$FF          ; $FF = not established
2C04  D0 03     BNE $2C09
2C06  4C 1F 2C  JMP $2C1F         ; -> "NONE"
2C09  0A        ASL A             ; index*2
2C0A  AA        TAX
2C0B  BD 76 2E  LDA $2E76,X       ; the pointer table
2C0E  85 A6     STA $A6
2C10  BD 77 2E  LDA $2E77,X
2C13  20 D7 31  JSR $31D7         ; print (A = hi, $A6 = lo)
2C16  A9 E3     LDA #$E3
2C18  85 A6     STA $A6
2C1A  A9 2F     LDA #$2F
2C1C  4C D7 31  JMP $31D7         ; print " BPS"
```

K56flex's is at `$2CB9`, the same nine instructions against `$2F28`, printing via
`$320B`. There is **no upper-bound check** in either -- the `#$FF` sentinel is the
only guard -- so feeding index >= 30 to the K56flex routine runs off the end of
its table into `RESERV_1`. That is presumably why the two slots exist.

Four callers each, the `AT&V1` report. A rate index is a plain byte in the status
block, and the block was re-laid-out between builds:

| Field | K56flex | V.90 |
|---|---|---|
| LAST TX rate | `$03C8` (`$2AC0`) | `$03CC` (`$29D6`) |
| HIGHEST TX rate | `$03CA` (`$2AC9`) | `$03CE` (`$29DF`) |
| LAST RX rate | `$03CB` (`$2AD2`) | `$03D0` (`$29E8`) |
| HIGHEST RX rate | `$03CD` (`$2ADB`) | `$03D2` (`$29F1`) |

V.90's are a clean 2-byte stride; K56flex's are 2,1,2.

The CONNECT result-code path does **not** use this table -- these are the only
references to `$2E76`/`$2F28` in either image. Connect messages index a separate
pointer table (K56flex `$2DF8`, from `$0124` at `$2AAD`).

**Open:** who writes `$03CC`. Its `STA` sites cluster at 0xCF30-0xD110 and
0x17B85/0x17BFC and are all `LDA / AND #mask / ORA #val / STA` packed-field writes
touching `$03C8-$03D5` together, which reads as profile/status setters rather than
the negotiation result.

## Digital impairment learning

K56flex has the equivalent, and it uses the same hardware window. The `AT&V1`
impairment fields are read straight out of the DSP interface at `$86xx`, and two
of the three words are at **identical addresses in both builds**:

| Field | K56flex | V.90 |
|---|---|---|
| EQM Sum | `$03DF`/`$03DE` | `$03E5`/`$03E4` |
| Min Distance | absent | `$041F`/`$0420`, gated |
| RBS Pattern | `$867A` | `$867A` |
| Rate Drop | `$867B` | `$867B` |
| Digital Pad / Loss | `$8679` | `$0421`/`$0422` |

So the K56flex data pump already measures robbed-bit signalling, rate drop and
digital pad, and exports them through the same mailbox. That measurement phase is
K56flex's DIL.

Where it is coarser: K56flex's digital pad is a *classified byte*, not a
measurement.

```
2B88  AD 79 86  LDA $8679
2B8B  D0 04     BNE $2B91       ; 0x00 -> "N/A"
2B91  C9 40     CMP #$40        ; 0x40 -> "3dB"
2B99  C9 80     CMP #$80        ; 0x80 -> "6dB"
2BA1  C9 60     CMP #$60        ; 0x60 -> "2db"
2BA9  A2 06     LDX #$06        ; else  -> "None"
2BAB  BD D4 2D  LDA $2DD4,X
```

Five buckets. The V.90 build drops that for the pad and prints a 16-bit value from
host RAM instead -- hence the rename `Digital Pad` -> `Digital Loss` -- and adds
`Min Distance` ahead of the RBS field, behind a gate. Other label deltas: added
`Min Distance` (width 0x10), and `V8bis K56Flex` -> `Flex`. The 16 termination
reasons are identical in both.

The gates are the newly decoded bit-branches, on `$014E` bit 4 = "connection is
V.90":

- `$2A93  BBR $014E,#$10 -> "NONE"` -- not V.90, so no min distance.
- `$2ABE  BBR $0150,#$04 -> $2AD1` -- and `$2AD1` is `LDA $8679`, the K56flex pad
  classifier. The V.90 build keeps the old path for non-V.90 connections.
- `$F4C0  BBS $014E,#$10 -> SEC` -- a predicate that returns true on V.90.
- `$11C7A BBS $014E,#$10 -> RTS` -- skip the EQM-versus-table rate logic on V.90.

This is all the **host** view. The learning itself runs in the DSP; the 6502 reads
results. The images show that K56flex has impairment measurements and at what
resolution they are reported. They do not show how either probe sequence is
designed.

## What is worth taking from this

- A shipped analogue-client V.90 rate grid, index space and the K56flex grid it
  was grafted onto, with the sharing made explicit by pointer reuse.
- Which line metrics a real V.90 client computes and reports, versus a K56flex
  one, at byte-level resolution.
- A clean K56flex/V.90 delta on identical hardware.

What it is not: nothing here is digital-side, and `PCMRX` is the *downstream PCM
receiver*, the peer of this project's transmitter rather than another copy of it.
The DSP payload is an opaque 32-bit blob for an unidentified core, so the signal
processing itself is not readable.

## Reproducing

The images were flattened from S-records by merging records on load address; the
gaps listed by that pass are unwritten flash, not missing data. The 65C02
disassembler used for the listings above -- a stock 65C02 table plus the `E2`,
`F2`, `C2`, `D2` entries in the ISA table above -- is **not committed**; the
encodings are documented here so it can be rebuilt. Its PC display must handle
banking: the images are 128 K in a 64 K address space, so file offset and
processor address diverge above 0xFFFF.
