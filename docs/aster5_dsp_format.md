# The Aster 5 DSP image is the same ADDSP V.90 package

`docs/firmware/Aster 5 DSP/T8660014.00` is a **Telindus IDMA/BDMA boot file**,
the format published as section 6.1 of `docs/addspv90guide.pdf` — the same
Analog Devices "V.90 modem software on ADSP 218x family" package whose database
names this project already uses for the Eicon DSP (`docs/addsp_database.md`).

The guide is explicit that Telindus owns this format ("the **Telindus**
BDMA/IDMA binary file format", §6.2) and it carries platform-specific notes for
an earlier Telindus product throughout: "Text in blue italics concerns
**aster4 flash** data-pump interface locations and bits", "if 1, freesia / if 0,
**aster4**", "the **Aster4** strapping for the parameter 'retrain on signal
quality'". Aster 4 is a host platform variant of this data pump, not merely an
older PBX model — which is why an Aster 5 DSP image is in this format at all.

`tools/aster_dsp_extract.py` parses it.

## Container

All fields are big-endian, unlike the little-endian Eicon combifile.

```
0x0000  outer preamble: magic 0x8002, header offset 0x000c,
        total bytes of image 0 (header+body), checksum
0x000c  image 0 header (§6.1.1, 138 bytes) + body (§6.1.2)
0x440ce image 1 header + body        <- follows directly, no preamble of its own
```

The §6.1.1 header decodes exactly, and the declared checksum is a **plain byte
sum of the body**, which verifies for both images:

| image | config | rcs | built | body bytes | checksum | verifies |
|---|---|---|---|---:|---|---|
| 0 | `ASTDTP1` | `JAN0904_11` | 2005-09-29 18:07 | 278,584 | `H#010990EA` | yes |
| 1 | `ASTDTP2` | `JAN0904_11` | 2005-09-29 18:13 | 63,746 | `H#003AB844` | yes |

Both declare file format `0x0001`: the IDMA overlay word is present in every
pageblock, and program code is in the standard 4-bytes-per-instruction form.
`headerlength + body bytes` accounts for every byte of the 342,618-byte file.

Two images, two data pumps — matching the 68000 control image's own
`MAIN DSP WATCHDOG ERROR` / `AUX DSP WATCHDOG ERROR` strings. ASTDTP2 loads into
the lower half of memory (DM `0x0000-0x1fb9`, PM `0x2030-0x3fb1`) where ASTDTP1
uses the full range, so the two are not the same binary relocated.

## Page table (image 0, ASTDTP1)

Page indices are the guide's Table 1 numbering — the same numbering the Eicon
DSP reaches through `bootpage_nr` at DM `0x3FB0` (`docs/dial_v8_call.md`).
Every index present decodes to a named page, with one exception.

| page | name | blocks | DM words | PM instrs |
|---:|---|---:|---:|---:|
| S | STARTUP | 38 | 295 | 1,336 |
| 0 | DIAL (idle) | 73 | 190 | 3,451 |
| 1 | V.22 | 117 | 581 | 6,741 |
| 2 | V.32 | 180 | 6,349 | 8,182 |
| 3 | FSK | 68 | 375 | 3,313 |
| 6 | V.8 | 119 | 1,997 | 6,220 |
| 7 | INFO | 125 | 2,274 | 6,389 |
| 8 | V.34 | 249 | 9,365 | 11,117 |
| 20 | V.29 fast-connect fax (identified below) | 164 | 5,209 | 7,853 |

Image 1 (ASTDTP2) carries only STARTUP, DIAL, V.22 and V.8.

## Diff against `dspdload.bin`

Eicon overlay sizes are from `tools/eicon_dsp_extract.py --list` (build 117-926,
the `.ANA` single-codec variants, which are the comparable ones). PM counts are
instructions on both sides.

| page | Aster 5 DM / PM | Eicon overlay | Eicon DM / PM | ΔDM | ΔPM |
|---|---:|---|---:|---:|---:|
| 8 V.34 | 9,365 / 11,117 | `0x0261` V34.ANA | 9,328 / 10,619 | **+0.4%** | +4.7% |
| 7 INFO | 2,274 / 6,389 | `0x0260` INFO.ANA | 2,251 / 5,676 | **+1.0%** | +12.6% |
| 2 V.32 | 6,349 / 8,182 | `0x0266` V22V32.ANA LEC | 6,754 / 10,902 | −6.0% | −25% |
| 1 V.22 | 581 / 6,741 | `0x0275` V22bisFC.ANA | 507 / 6,037 | +14.6% | +11.7% |
| 6 V.8 | 1,997 / 6,220 | `0x025f` V8.ANA | 1,568 / 4,179 | +27% | +49% |
| 0 DIAL | 190 / 3,451 | `0x0262` DIAL/FSK/FAX.ANA | 6,984 / 5,183 | — | — |
| 3 FSK | 375 / 3,313 | (folded into `0x0262` above) | — | — | — |
| 20 ? | 5,209 / 7,853 | `0x026a` V.90 DPCM | 8,098 / 10,443 | −36% | −25% |
| — | — | `0x026b` V90.ANA APCM | 8,700 / 9,985 | — | — |

What this says:

- **V.34 and INFO are the same code.** DM word counts are dominated by
  coefficient and state tables, and they agree to within 0.4% and 1.0% across
  a 1999 Eicon build and a 2005 Telindus build. That is not convergent design;
  it is one package with six years of divergence.
- **The pages that differ are the ones you would expect to differ.** V.8 is
  where each vendor's own negotiation policy lives, and DIAL is packaged
  differently: Eicon folds DIAL, FSK and FAX into a single overlay `0x0262`,
  while the Aster file keeps DIAL (0) and FSK (3) as separate pages, exactly as
  Table 1 numbers them.
- **Page 20 is not in the guide's Table 1** and does not match either Eicon
  V.90 overlay by size. It is identified by content below, and it is not V.90.

## This image does no V.90 at all

The index table has an explicit slot per page number, and the guide says a zero
offset means "the page is not contained in the file". Image 0 declares 21 slots
and fills 8:

```
present: 0 1 2 3 6 7 8 20
absent : 4 5 9 10 11 12 13 14 15 16 17 18 19
```

**V.90A is page 13 and V.90D is page 14. Both slots exist and both are zero.**
This build knows the page numbers and ships neither. The modulation ladder tops
out at V.34, with V.8 and INFO present to negotiate it — which is what an
ISDN PBX needs for its analog ports, and it settles the question of whether a
2005 build might have carried V.92 instead: it carries neither.

## Identifying page 20 by content

Size alone was not going to answer this, so `tools/aster_page_fingerprint.py`
matches DM *word values* at shared DM addresses. Two builds of the same package
implementing the same modulation share long runs of identical coefficient and
state tables at identical addresses, even where the surrounding code was laid
out differently. Every page whose identity is already fixed by its index
calibrates the method, and each one's top match is its correct counterpart:

| Aster page | best Eicon match | score | shared addrs |
|---|---|---:|---:|
| 3 FSK | `0x0264` FSKFAX.ANA partial | 98.3% | 286 |
| 0 DIAL | `0x0263` DIAL.ANA partial | 86.7% | 30 |
| 8 V.34 | `0x0261` V34.ANA | 83.7% | 9,142 |
| 2 V.32 | `0x0266` V22V32.ANA LEC | 61.9% | 4,994 |
| 7 INFO | `0x0260` INFO.ANA | 61.7% | 2,063 |
| 1 V.22 | `0x0266` V22V32.ANA LEC | 53.6% | 373 |
| 6 V.8 | `0x025f` V8.ANA | 35.4% | 1,063 |
| **20 ?** | **`0x0273` V29FC.ANA** | **29.7%** | 4,639 |

The noise floor is 0-6%: page 20's next-best candidate is V34.ANA at 5.3%, and
its scores against both Eicon V.90 overlays are 6.5% (DPCM) and 1.4% (APCM) —
nothing. Against V29FC.ANA it scores 29.7%, the same order as the confirmed
V.8 pairing, and no other Aster page scores above 4.2% against V29FC.

The matching words are not spread evenly, which is the signature of shared
tables rather than coincidence:

```
0x0000-0x03ff   810/986   82.2%
0x0400-0x0bff     0/1902   0.0%
0x1800-0x1bff   376/895   42.0%
0x1c00-0x1fff   145/638   22.7%
0x2000-0x23ff    46/46   100.0%
```

**Page 20 is a V.29 fast-connect fax modulation page** — a fax data pump for the
PBX's analog ports, added past the guide's 1999 page numbering. That accounts
for the whole index: V.22, V.32, V.34 data, FSK and V.29 fax, V.8 and INFO to
negotiate, DIAL idling between calls.

## Cross-confirmation in the DM footprint

Loaded DM addresses at or above `0x3E00`, per page (image 0):

```
STARTUP  3edb
DIAL     3e0f-3e1d 3e24 3e4d-3e59 3e5e-3e69 3eda-3edc 3fa7-3fac 3fb8-3fb9
V.22     3e0f-3e1d 3e24 3e51-3e67 3eda-3edc 3f0f 3f47 3fb8-3fb9
V.8      3e0f-3e1d 3e24 3e4d-3e66 3eda-3edc 3f0f 3f47 3fb8-3fb9
V.34     3e0f-3e1d 3e24 3e4d-3e69 3eda-3edc 3f0f 3f47 3fb8-3fb9
```

The Aster DIAL page initialises **`0x3FA7-0x3FAC`, exactly six words**. That is
the same six-word block the Eicon DIAL overlay clears from PM `0x13D2`
(`AR = $0006` / `CALL $1279`, `docs/dial_v8_call.md`) and the same block the
resident kernel clears in `docs/harness-intervention-inventory.md`. Two
independent builds treating the same arbitrary six words at the same address as
one unit is a strong structural match.

`0x3FB8-0x3FB9` is loaded by every Aster page and by nearly every Eicon overlay
(`V8.ANA`, `INFO.ANA`, `V34.ANA`, `V.90 DPCM`); Eicon additionally loads
`0x3FB2-0x3FB3`, which the Aster pages leave alone. Both sit in the data-pump
database window that `docs/addsp_database.md` maps.

## What this is good for

The Aster image is a **second, independently built instance of the package the
emulator already runs**. Where the two agree, the behaviour belongs to the ADDSP
package; where they differ, it is Eicon's customisation. Concretely:

- V.34 and INFO can be diffed instruction-by-instruction to separate package
  code from Eicon glue.
- The Aster pages are addressed by the guide's page numbers directly, with no
  Eicon overlay-ID indirection and no `TIKRNL` mapping table (DM `0x31D5`,
  `docs/dial_under_tikrnl.md`) in between — so they show what the package
  expects a host to load, unmediated.

The limit is that **no V.90 page is present**, so this image cannot serve as a
second opinion on the V.90 work itself — only on everything underneath it (V.34,
INFO, V.8, DIAL) that V.90 is reached through.

Open questions:

- Page 20's 29.7% against V29FC.ANA is a solid identification but a much weaker
  one than V.34's 83.7%, so it is the V.29 fast-connect *family*, not
  necessarily the same modulation set. Disassembly would pin down whether it
  also carries V.27ter or V.17.
- The `Aster 5 Control` image (`T8261018.00`) is **68000**, not MIPS, so nothing
  in `tools/eicon_mips_shim.py` carries over to the host side. It is the host
  that boots these DSP images, and it is the more promising target for the
  host-side V.8 decision — see `docs/aster5_control_image.md`.

## Usage

```bash
./tools/aster_dsp_extract.py "docs/firmware/Aster 5 DSP/T8660014.00"
```

```bash
./tools/aster_dsp_extract.py "docs/firmware/Aster 5 DSP/T8660014.00" --blocks --image 0 --page 8
```

```bash
./tools/aster_dsp_extract.py "docs/firmware/Aster 5 DSP/T8660014.00" -o /tmp/aster
```

To fingerprint pages against the Eicon overlays, extract the Eicon side first:

```bash
./tools/eicon_dsp_extract.py docs/firmware/dspdload.bin --match '\.ANA' -o /tmp/eicon
```

```bash
./tools/aster_page_fingerprint.py "docs/firmware/Aster 5 DSP/T8660014.00" /tmp/eicon
```

`-o` writes `dm.bin` and `pm.bin` per page. Note these are the file's raw loads
concatenated, not sparse address-space images: unlike
`tools/eicon_dsp_extract.py`, this format's blocks are already addressed
individually, and a sparse image would need the same `dm.words`/`pm.words`
treatment to distinguish a loaded zero from a gap. Use `--blocks` for the
per-block addresses in the meantime.
