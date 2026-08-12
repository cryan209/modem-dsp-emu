# Older DIVA Server firmware comparison

This compares the newly recovered OS/2 package in `docs/firmware/divase/` with
the firmware already used by the emulator in `docs/firmware/`.

Only the package's card firmware is tracked there -- the MIPS protocol images,
`DS4BRI.BIT` and `DSPDLOAD.BIN` -- plus `CONTENT.TXT`, which is the source for
the identity and dates below. The OS/2 drivers, CAPI DLLs, utilities and the
servod PDF that shipped alongside are host-side software that nothing here
loads; they are kept locally but not committed.

## Identity and provenance

The package identifies itself in `CONTENT.TXT` as **DIVA Server for OS/2 6.03e**, a
final release. Its principal dates are October 1999.

| Component | Recovered package | Existing emulator input |
|---|---:|---:|
| PRI MIPS image | `TE_DMLT.PM`, 396,428 bytes | `te_dmlt.pm`, 979,504 bytes |
| MIPS banner | build **99-45**, protocol 6.03(V3) 99-6, `F#001F` | build **107-79**, protocol 6.03(V11) 104-102, `F#00FF` |
| DSP combifile | 392,245 bytes, build **99-220** | 2,784,299 bytes, build **117-926** |
| DSP downloads | 36 | 164 |
| DSP usage-mask/file sets | 1 byte / 8 sets | 3 bytes / 22 sets |

This is a credible, internally consistent older family rather than a renamed
copy. As an independent provenance check, recovered `DS4BRI.BIT` is byte-for-byte
identical to existing `docs/firmware/ds4bri.bit` (SHA-256
`6e7193042398d89f52d6bc0e095eacf26cf1d767e7966e7667a45768b0d3ea01`). The DSP
combifile is not identical to either existing `dspdload.bin` (117-926) or
`dspdload.bin.old` (107-708).

## DSP architecture changed substantially

The old build already contains the complete basic modem chain: PRI kernel,
TIKRNL, V.8, INFO, V.34, DIAL/FSK/FAX, V.22/V.32, and V.90 DPCM. It is therefore
old enough to be architecturally interesting but not pre-V.90.

For historical card type 23 (PRI 30M), the later build adds or changes:

- separate `.F34` task/overlay variants rather than the old generic TIKRNL/V.8/
  DIAL chain;
- a SIG overlay and FSK-own overlay;
- `INFOH.F34` and `HV34.F34` high-V.34 stages;
- a real V.32 partial overlay (the old `0x0267` entry is an empty placeholder);
- V.22 fast-connect and V.OWN/V.18 support;
- much larger DIAL/FSK/FAX partial overlays, which now include DM as well as PM;
- many analog, SoftIP, RTP codec, echo-canceller, measurement, and secure voice
  variants in the full combifile.

The old combifile has V.90 **DPCM** (`0x026a`) but no V.90 **APCM** (`0x026b`)
anywhere. The current full combifile contains APCM, although the PRI file set
still does not select it. Neither combifile exposes a V.92-labelled download.

## Pre-standard 56K PCM: interface remnants, but no implementation found

There is a potentially misleading clue in the later open-source host interface.
`kernel/mdm_msg.h` assigns connected-norm values 19 and 20 to **K56flex** and
**x2**, and defines independent CAI disable bits `0x0800` and `0x1000` for them.
The TTY and old isdn4linux presentation layers can consequently print
`K56FLEX` or `X2`. This proves that Eicon's common host/DSP API reserved slots
for the two pre-standard protocols; by itself it does not prove that this DSP
image implements them.

The recovered build supplies stronger negative evidence:

- its only 56K modem download is `0x026a V.90 DPCM`; there is no x2, K56flex,
  V.PCM, or generic proprietary-PCM download or identifying string;
- the contemporaneous **V.90 modem software on ADSP-218x, User's Guide 5.3
  (6 February 1999)** explicitly lists the modulation core as V.90, V.34bis,
  V.34 and lower ITU/Bell modes, with no x2 or K56flex;
- that guide's bootpage table labels page 7 as “ITU-T V.34 and V.90 phase 2”
  and page 14 as “ITU-T V.90, phase 3-4, DPCM”;
- the guide's complete modulation-selection table has V.90 as mode 16 but no
  x2 or K56flex mode;
- a suspicious old-V.8 instruction at PM `0x3879` really does test
  `V8_setup & 0x1000`, but the same guide defines that bit as **H.324
  capability**. The adjacent `0x0800` V.8 tests are the **CI tone** option.
  They are not the CAI K56flex/x2 disable-mask bits despite sharing values.

The most defensible conclusion is therefore: **the API retained generic
K56flex/x2 identifiers, but build 99-220's shipped DSP set implements
standardised V.90 only.** It remains theoretically possible that proprietary
training is folded invisibly into the V.90 overlay, but the module inventory,
published software guide, bootpage map, and modulation table all argue against
it. Proving the absolute negative would require stimulating the old V.8/DIAL
chain with recorded x2 and K56flex handshakes and observing that no proprietary
path is selected.

## The shared modem modules are not minor revisions

The extractor was used on identically named generic modules. “Same” means the
same value at the same loaded ADSP address; it is not a fuzzy binary match.

| Module | old DM | new DM | same DM | old PM | new PM | same PM |
|---|---:|---:|---:|---:|---:|---:|
| V.8 | 1,086 | 1,264 | 505 | 2,699 | 3,121 | 1,064 |
| INFO | 1,922 | 2,243 | 200 | 5,130 | 5,662 | 226 |
| V.34 | 9,248 | 9,320 | 7,404 | 10,339 | 10,605 | 1,052 |
| DIAL/FSK/FAX | 7,773 | 6,959 | 194 | 6,069 | 5,291 | 113 |
| V.22/V.32 | 7,592 | 8,499 | 3,194 | 12,219 | 12,341 | 604 |
| V.90 DPCM | 8,109 | 8,098 | 918 | 10,211 | 10,443 | 904 |

The deployed PRI comparison is even less source-compatible in several places:
old TIKRNL versus new `TIKRNL81.F34` retains only 4 identical PM words at the
same addresses; old V.8 versus `V8.F34` retains 609; old DIAL versus
`DIAL/FSK/FAX.F34` retains 27. Address conventions and interfaces cannot be
assumed to carry across builds.

V.34 is the exception in data memory: 7,404 same-address DM words survive, which
probably reflects large coefficient/table regions. Its PM code is still mostly
changed. V.90 DPCM is a major rewrite despite nearly identical total size.

## Additional recovered 109-era firmware directory

The recovered 109-era directory is not simply a duplicate, but it is mostly one:
of its 45 files, 34 are byte-for-byte identical to files already in
`docs/firmware/` and eleven genuinely differ. Only those eleven are tracked, in
`docs/firmware/build-109/`; the unpacked original stays local and untracked, so
the repository does not carry 26 MB of second copies. Among the identical ones
are its basic `te_dmlt.pm`, which matches the existing 107-79/V11 image, and its
`dspdload.bin.old`, which is the already known 107-708 file -- neither is
tracked again.

The important coherent family among the eleven is:

- DSP combifile **109-789** (`build-109/dspdload.bin`): 151 downloads / 102
  unique IDs, between existing 107-708 (141 / 97) and 117-926 (164 / 112);
- PRI/large-card MIPS images `build-109/te_dmlt.pm3` and `build-109/te_dmlt.qpm`:
  build **109-76**, protocol 6.03(V19) 108-1, versus the root build 122-11/V24;
- `build-109/te_dmlt.2qm` and `build-109/te_dmlt.am`: build 109-76/V19 versus
  root 122-11/V24. The `.am` is the paired Analog image used by
  `docs/analog_v8_oracle.md`;
- `build-109/te_dmlt.qm` is a third distinct `.qm`, different from both the
  tracked 108-130 `te_dmlt.qm` and the 107-136 image the 4BRI card actually
  runs (`docs/firmware/te_dmlt.qm.107-136`).

The 109-789 combifile is an especially useful bisect point, but module-level
comparison gives an important qualification. After extraction and relocation,
its PRI `TIKRNL81.F34`, INFO, INFOH, DIAL/FSK/FAX partial, FSK/FAX partial,
V.32 partial, and **V.90 DPCM images are identical to 117-926** (container build
labels still make the raw records hash differently). V.90 DPCM differs from
107-708 only in two download-interface words and two corresponding relocated
PM operands; its modulation body is effectively the same across all three
107/109/117 combifiles. Therefore the current page-14 V.90 behaviour was not
introduced between 109 and 117, and 109-789 is not an independent V.90
implementation oracle.

Other PRI modules really do change across 109 -> 117: the PRI kernel (17 DM and
887 PM loaded-word differences), V8.F34 (183/1,344), HV34.F34 (41/3,032),
DIAL/FSK/FAX.F34 (916/4,231), V.22/V.32 LEC (242/6,120), and V.22FC
(88/4,336). This makes 109-789 useful for V.8/DIAL, V.32, V.34, and kernel
regression/bisection. The later 117 combifile additionally introduces VHook
LEC/SIG families and more secure/small voice-kernel variants.

The directory contains no new evidence of pre-standard PCM modem support. Its
combifile has the same V.90 DPCM/APCM architecture and no x2- or
K56flex-labelled DSP download.

### Do not conflate analog modem service with the Analog card family

The `.ANA` downloads really are for the later physical **Diva Server Analog
(POTS-line) cards**, not merely another name for modem calls arriving through
an ISDN B channel. In 109-789, card types 77/78 (Analog 4/8-port, file set 18)
select the `DIVA Server Analog Kernel`, `TIKRNL81.ANA`, the `.ANA` overlay
family, and `V90.ANA APCM`; the paired MIPS image is `te_dmlt.am` build 109-76.

The recovered 1999 OS/2 package is not that firmware set. Its manifest says
4BRI, BRI and PRI; its combifile directory ends at card type 38; and it has no
Analog kernel, `.ANA` overlays or APCM overlay. Its “analog modem” feature is
modem termination on an ISDN server card, using the DPCM side toward the
digital network. A separate contemporary Analog-board package could have
existed, but it is not present in `divase/` (and card types 77/78 are absent).

This also sharpens the HV34 naming: both `HV34.F34` (PRI/ISDN family) and
`HV34.ANA` (physical Analog-card family) exist, alongside ordinary `V.34` and
`V34.ANA`. Therefore the leading `H` does not mean “analog hardware”; it marks
the half-duplex V.34/V.34-fax function. The suffix chooses the card/DSP
integration variant.

### Using the Analog family to investigate the V.8/INFO failure

This is useful primarily as a **configuration-path oracle**, not as an
independent V.8 algorithm. In build 117 the F34/ANA loaded-word differences are
438 DM + 417 PM for V.8 and 338 DM + 319 PM for INFO. More importantly, every
INFO region implicated by the current missing-framer problem is instruction-for-
instruction identical between `INFO` and `INFO.ANA`: classifier/actions
`0x2410..0x24ff`, installer `0x25f0..0x260f`, action table
`0x2ee0..0x2eff`, entry selection `0x32d0..0x331f`, record handlers
`0x3435..0x34bf`, framers `0x3510..0x35ff`, and initializer
`0x3f40..0x3f8f`. PM `0x2602`, `0x32dd`, `0x3317`, and `0x3f4c` are exact.

Consequently, if a paired Analog-card execution takes the `0x07xx` INFO state
chain and installs framer B at state `0x24`, the cause is outside that shared
INFO code: MIPS supervisor setup, TIKRNL/kernel integration, or the V.8 -> INFO
published database. The decisive A/B trace is the last V.8 frame through the
first INFO `0x37`, comparing write-database values and writers plus
`DM(0x164c)`, `DM(0x19cf)`, `DM(0x198e)`, the selected INFO entry/vector, and
bootpage requests. A same-input direct-DSP F34/ANA run can first separate DSP
variant effects; a fully authoritative run needs paired `te_dmlt.am` build
109-76 + card type 77/78 (file set 18), rather than mixing the Analog overlays
with the PRI MIPS image.

## Practical implications

1. **Do not drop the old files into the current MIPS+DSP harness as a mixed set.**
   The MIPS protocol image, TIKRNL, overlay variants, partial-overlay mechanism,
   and card directory all differ. Existing hard-coded addresses and current
   analysis findings are build-specific.
2. **The old package is useful as a clean second implementation oracle.** It can
   distinguish emulator defects from behaviour introduced by the 107/117-era
   firmware, particularly around V.32 partial loading, high-V.34, and V.90.
3. **The best first experiment is a fully paired old PRI run:** old
   `TE_DMLT.PM` + old card-type-23 selection from old `DSPDLOAD.BIN`. This needs
   a separate profile and fresh address discovery rather than current shim
   patches.
4. **A lower-risk first step is direct ADSP execution** of the old PRI kernel,
   TIKRNL, and one old overlay with existing extraction/emulation tools. This can
   check instruction compatibility before adapting the MIPS shim.

Reproduce the inventory with:

```sh
python3 tools/eicon_dsp_extract.py \
  docs/firmware/divase/DSPDLOAD.BIN --list --card-type 23
python3 tools/eicon_dsp_extract.py \
  docs/firmware/dspdload.bin --list --card-type 23
```
