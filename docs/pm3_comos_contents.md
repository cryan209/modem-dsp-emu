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

## Sections by release

| release | ComOS | modem controller (`m2d`) | data pump (`m2c`) | ADSP-2181 overlays |
|---|---|---|---|---|
| 3.5 | `pmexe.18795` | — | — | `2_51_` bot, cmn, v32, v22, ph1, ph2, v34, omc, mnp |
| 3.8 | `pmexe.8285` | `i623810.bin` | `dp2.bin` | `3_18_` omc, mnp, bot, cmn, v32, ph1, ph2, ans |
| 3.9b9 | `pmexe.5409` | `ic07819.bin` | `dp2.bin` | — |
| 3.9.1 | `pmexe.22117` | `i12600e.bin` | `dp2.bin` | — |

`wanctl.0` (`wanctl.bin`, 10186 words) and, from 3.9 on, `/7/mipsboot` (the
VPN co-processor) ride along in every PM3 image.

3.5 is the V.34 generation: its overlay set is per-modulation
(`v22`, `v32`, `v34`) and there is no 56K anything. The 56K server arrives in
3.8 with the `m2d`/`m2c` pair, and by 3.9 the separate `2181_*` overlays are
gone — folded into the controller image.

## The K56flex engine

Two halves, both in the `m2d`/`m2c` pair:

- **`i12600e.bin`** (3.9.1) — 256 KiB **Z180/HD64180** modem-controller code.
  Its header string at offset 17 is `VERSION: 12600E`, matching
  `docs/firmware/portmaster/release391.txt:23` ("ComOS 3.9.1 contains modem
  code version i12600e"). Entry: `di / ld a,0deh / out0 (0c0h),a /
  ld sp,0fffdh / jp 0302h`. Banked code runs 0–0x18000, tables and the DSP
  payload 0x20000–0x36000. 3.8 ships `i623810.bin`, 3.9b9 `ic07819.bin`, all
  256 KiB.
- **`dp2.bin`** — the ADSP-2181 data pump, 18517 words in 3.9.x, 18198 in 3.8.
  Stored one 24-bit word per 32-bit little-endian slot with the top byte zero;
  `--pack` writes the `.pm` form that `tools/adsp2181_dis.py` and the emulator
  core load.

The ComOS side confirms what the pair implements. In `pmexe` from 3.8 onward
(and in no 3.5 build) there is a modulation display table —
`V.23 (B2)`, `V.23 (B3)`, `V.34 Modulation`, `K56Flex Modulation`,
`V.90 Modulation`, then `MSE=0x2500`…`MSE=0x4000` — and a config keyword table
whose `modulation` values are `ccitt`, `v90`, `flex`, `v34`, `auto`. So
`set <port> modulation flex` selects the K56flex server on the running unit.

## Not yet established

`dp2.bin` and the `2181_*` overlays are not flat PM images: word 0 of
`dp2.bin` is `0x01b00030` with a non-zero tag byte, and further tagged slots
appear at word 248, 4268, 7783, … , with what looks like a 16-bit DM data
region from about word 16108. Scanning for an ADSP-2181 interrupt vector table
(four-word slots of `JUMP`/`RTI`) finds nothing in any of them at any offset,
under either byte order. They are record-structured download images —
address/length/data — and the record header has still to be worked out before
the data pump can be disassembled or run. The Z180 controller in
`i12600e.bin` is what stages them, so it is the place to read the format off.
