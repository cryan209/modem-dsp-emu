# The Aster 5 68000 control image

`docs/firmware/Aster 5 Control/T8261018.00` (1,124,464 bytes, part `HT8261/01800`,
stamped `29/03/07 11:52`) is the Telindus Aster 5 host processor image. It is
**Motorola 68000**, not MIPS — `4E75` RTS, `4E56` LINK A6, `4E5E` UNLK are
everywhere — so nothing in `tools/eicon_mips_shim.py` applies to it.

Why it is worth reading at all: `docs/dial_v8_call.md` establishes that the V.8
page load is a **host-side decision**, not a DIAL-side one, driven by the host's
training script and line follow-up. On the Eicon card that host is the MIPS
protocol firmware. Here it is this image — and this image is the host for
exactly the DSP boot format parsed in `docs/aster5_dsp_format.md`, as its own
error strings show: `FLASH IDMA ERROR`, `FLASH BAD DSP CODE`, `MAIN BOOT ERROR`,
`AUX BOOT ERROR`, `MAIN DSP WATCHDOG ERROR`, `AUX DSP WATCHDOG ERROR`.

## What is legible without any disassembly

**A task table** at file `0x2A67`, names tagged with a leading `H`:

```
Root  Supervisor  Modem  Line  Layer2_main  Layer2_aux  Dialler  CmdItpr  DTE  NMS  Encryption
```

`Modem` and `Dialler` are the two tasks that matter for the DIAL/V.8/V.34
question. There is no IP or ISDN task in the list, which is the first hint at
what the product is — see below.

**A management database**, SNMP-shaped and self-describing, in a nested
tag/length/value encoding. `tools/aster_control_db.py` recovers two record
shapes, each validated by its own internal length fields:

- **372 attribute records** (286 distinct), some carrying an inline default
  symbol: `modulation default v34 = 11`, `maxSpeed default 33600bps = 26`,
  `minSpeed default 19200bps = 15`, `compression default v42bis = 4`. Names
  include `modulation`, `maxSpeed`, `minSpeed`, `speedRange`, `fallback`,
  `training`, `retrain`, `autoSpeed`, `amModulation`, `diallingMode`,
  `blindDialDelay`, `dialString`, `autoAnswer`, `ringsToAnswer`, `answerTone`,
  alongside standard MIB-II (`sysDescr`, `sysObjectID`, `sysUpTime`).
- **163 enum runs**, each one enumerated type. The modem state machine:

```
offline=0  dialling=1  incomingCall=2  training=3  retraining=4  data=5  disconnecting=6  notInData=7
retrain reason:  no=0  retrain=1  forced=2  unknownReason=3
```

**There are two different modulation enums, and the difference matters.** The
*status* enum reports what the line is doing:

```
v21=0 … v32b=13  v33=14  auto=15  bell212a=16  bell103=17  tfast=18  v34=19  faxV21=20  autoV8=21  v90=22  faxV34=23
```

The *configuration* enum selects what to run, and is the one the `modulation`
attribute's default refers to:

```
bell103=0  bell212a=1  v21=2  videotex=3  v22=4  v22b=5  v32b=8  v34=11  auto=12  autoV8=13
```

`v90=22` exists **only in the status enum, and the configuration enum has no
V.90 value at all** — consistent with `docs/aster5_dsp_format.md`, where the DSP
index table's V.90A (13) and V.90D (14) slots are both explicitly zero. The
status enum is a shared Telindus product-line MIB; do not read capability off
it. The index table is the authority.

## What the product actually is

Not a PBX. Earlier notes in this repo called it one; the management database
says it is a **managed leased-line modem with dial backup and remote
maintenance**, and the evidence is not ambiguous:

```
line:      wires default 2wire=0   lineType / lineMode / savedLineType / forceLine
levels:    txLevel  rxLevel  nearEchoLevel  farEchoLevel
loopbacks: al=1  dl=2  rl=4  et=24  al+et=25  rl+et=28        (V.54)
dialling:  v25bis=3  ll=4  rem=5   dialString / dialNumber / dialStoredNumber
DTE:       108/1 ext=0   108/2 int=1   rtsCts   dceFlowCtrl   dtePABXCtrl
line type: leasedLine=0  pstn=1  internalDbu=17  management=19
state:     windowDeviceState1 default ll = 4
backup:    setDbu  controlDBU  dbuAllow  dbuFail  dbuSecurity
```

DBU is a Dial Backup Unit. That explains the two data pumps: `ASTDTP1` carries
the full ladder (V.22, V.32, V.34, FSK, V.29 fax) for the main line, and
`ASTDTP2` carries only STARTUP/DIAL/V.22/V.8 — which is what a backup dial
channel needs. The pairing with `Layer2_main` / `Layer2_aux` and the
`auxiliaryChannel` attribute is consistent, but it is inference from the page
sets, not something confirmed in code.

## Everything else in the image

**Structurally there is nothing hidden.** Entropy is flat at 6.0-6.5 bits across
all 35 32K blocks — no compressed region, no encrypted region, no padding gap.
Code and rodata are interleaved throughout, which is consistent with the
multi-segment link described below. **The DSP image is not embedded here**:
neither `config:AST` nor `ROM CHECKSUM` appears anywhere in the file, so the
data pumps are a separate flash object that this image loads.

It is also a sparse image — only 4,671 printable runs of 6+ characters in
1.1 MB. Subsystems present, by string and attribute evidence:

| area | evidence |
|---|---|
| V.42 / V.42bis | `V42 RX slot`, `V42 TX slot`, `COMPRESSION: NONE/CLASS5/V42BIS`, enums `mnp2=112 mnp3=113 mnp4=114`, `disabled=2 mnp5=3 v42bis=4 v44=5` |
| dialler | `dialString`, `dialNumber`, `dialStoredNumber`, `blindDialDelay`, `ringsToAnswer`, `answerTone`, `TELEPHONE NUMBERS:`, `NO CARRIER` |
| management | MIB-II (`sysDescr`, `sysObjectID`, `sysUpTime`), `alarmHistoryList`, `alarmDiscriminator`, seven `alarmLevel` attributes, `cms2SessionList`, `debugMessages`, `memDump` |
| config / flash | `flash1Version`, `flash2Version`, `activeFlash`, `bootFromFlash`, `presentConfiguration`, `loadDefConfig`, `loadSavedConfig`, `activateConfig`, `coldBoot` |
| local console | the `windowDevice*` attribute family, `<ACTION> <ARG>`, `SELECT `, `RECONNECTING LOCALLY ..` |
| encryption | the `Encryption` task, `windowDeviceEncryption`, `encryption` — three references, no algorithm names anywhere |

**Absent:** no IP, PPP or Ethernet stack; no Q.931/DSS1 or any ISDN layer-3; no
T.30 fax protocol. The fax capability is the DSP's V.29 page (page 20) — this
image appears not to terminate fax at all.

One trap worth naming: the compression enum contains **`v44=5`**, and V.44 is
V.92-era. It is the same shared-MIB artefact as `v90=22` in the status
modulation enum. Neither is evidence of capability; the DSP index table is.

## The load address is not recovered

This is the blocker for going further, and it is a real finding rather than a
missing step.

The image is an **AST5LDR multi-segment loader image**. Its header carries named
segments — `aster5tt`, `aster5CVTwinA`, `aster5CVTwinB`, `line`, `v24`,
`dialler` — in 0x40-byte records from file `0x5C`, each with a small field
group before an `H`-tagged name. Code begins around file `0x20A`.

Absolute call targets cluster in `0x00480000-0x00580000`, a span about the size
of the image, which looks like a single load base. It is not one. Disassembling
339,404 instructions in a resynchronising linear sweep yields 2,223 distinct
absolute `jsr`/`jmp` targets, and under **any** single candidate base almost
none of them land on a function prologue:

| candidate base | targets landing on `link a6` / `movem.l` |
|---|---:|
| `0x480000` | 0.4% |
| `0x47F000` | 0.8% |
| `0x4698D2` | 1.7% |

A correct base would put nearly all of them on prologues. Three independent
methods — prologue matching, pointer-to-string-start correlation, and
correlating PC-relative `BSR` targets against absolute `JSR` targets — all
failed to produce a sharp peak. The consistent explanation is that **different
segments load at different addresses**, so no single base exists, and the
absolute targets are runtime addresses in a layout the loader builds.

The next step is therefore to decode the AST5LDR segment table into (file
offset, load address, length) triples, not to keep searching for one base.
Until that is done, treat every absolute address in a disassembly as
unresolved.

Two smaller cautions found the same way:

- The `0x3FB0` (`bootpage_nr`) byte patterns in the image occur at **odd file
  offsets** (`0x37EE5`, `0x380DD`, `0x677FD`), so they cannot be instruction
  words. They are data or coincidence, not evidence that this code writes
  `bootpage_nr`.
- Searching for the guide's training-script constants was inconclusive:
  `GEN_SETUP1 = 0x048C` (calling) appears **zero** times, and `0x0484` (answer)
  appears 9 times, which is indistinguishable from noise in a 1.1 MB image. The
  Aster host may drive the guide's *modem software* interface rather than the
  raw *data-pump* interface; those use different locations entirely.

## Tools

```bash
./tools/aster_control_db.py "docs/firmware/Aster 5 Control/T8261018.00" --grep 'modul|train|speed'
```

```bash
./tools/aster_control_db.py "docs/firmware/Aster 5 Control/T8261018.00" --enums
```

Disassembly needs capstone with M68K, which is not in the system Python
(PEP 668 blocks installing there); it is installed in the project venv:

```bash
/tmp/eicon-venv/bin/pip install capstone
```

```bash
/tmp/eicon-venv/bin/python tools/aster68k_dis.py "docs/firmware/Aster 5 Control/T8261018.00" 0x20a --count 24
```

Addresses printed are **file offsets**. PC-relative flow is correct as printed;
absolute references are runtime addresses and do not index the file until the
segment table is decoded.
