# Eicon/Dialogic ADSP V.34/V.90 firmware extraction

Initial extraction notes, 2026-07-26.

## Why this firmware matters

The files under `docs/firmware/` are Eicon/Dialogic DIVA DSP download
combifiles. They contain separately named, relocatable modem overlays rather
than one unidentified flat image. Two versions have been verified:

| Combifile | Build | Downloads | Relevant overlays |
|---|---:|---:|---|
| `dspdvmdm.bin` | 103-492 | 78 | V.34, V.90 DPCM, V.90 APCM |
| `dspdload.bin` | 117-926 | 164 | V.34, V.90 DPCM, V.90 APCM |

The architecture is ADSP-218x—specifically an ADSP-2185N on the Eicon card—not
the Courier's TMS320C25. Evidence:

- `docs/addspv90guide.pdf` describes the matching Telindus V.90 package on the
  ADSP-218x family and its V.34/V.90 overlay organization.
- firmware task names include `TIKRNL81`, the ADSP-2181-family software ABI
  used by the instruction-compatible ADSP-2185N;
- program-memory records contain 24-bit ADSP instructions in 32-bit download
  containers;
- the public Eicon `dsp_defs.h` identifies the exact combifile structures and
  the `DSP_DOWNLOAD_FLAG_2181` flag.

This is useful as an independent shipping V.34/V.90 implementation. It does
not directly reveal the Courier's decisions, but its Phase 3 receiver,
DIL-analysis and V.90 sequencing can be compared with our implementation and
with the Courier firmware.

## Extractor

`tools/eicon_dsp_extract.py` parses the complete combifile directory and every
nested download. By default it extracts the three generic V.34/V.90 overlays:

```bash
./tools/eicon_dsp_extract.py docs/firmware/dspdvmdm.bin \
  -o artifacts/eicon-dsp/build-103-492

./tools/eicon_dsp_extract.py docs/firmware/dspdload.bin \
  --match 'V\.90 APCM Overlay' -o artifacts/eicon-dsp/build-117-926
```

List all contained downloads without writing images:

```bash
./tools/eicon_dsp_extract.py docs/firmware/dspdload.bin --list
```

Select other variants, such as the analogue-card overlays, with `--match`:

```bash
./tools/eicon_dsp_extract.py docs/firmware/dspdload.bin \
  --match 'V34\.ANA|V90\.ANA' -o artifacts/eicon-dsp/ana
```

`--match` alone is ambiguous where a download id ships in several variants —
`0x0270` is `SIGLK`, `SIG` and `SIG.ANA`; `0x0262` is plain, `.F34` and `.ANA`.
`--card-type` resolves it the way the driver does, by the usage mask. Card type
56 is file set 12, the set the PRI 30M kernel, TIKRNL81.F34 and
DIAL/FSK/FAX.F34 all belong to, so it selects exactly the `.F34` variants:

```bash
./tools/eicon_dsp_extract.py docs/firmware/dspdload.bin \
  --card-type 56 --match Overlay -o artifacts/eicon-dsp/overlays
```

That is the overlay set `tools/dial_tikrnl_drive.py` serves page switches from.

Each selected download produces:

- `download.bin` — exact nested download record;
- `dm.bin` — complete 16-bit DM address space, little-endian;
- `pm.bin` — complete PM address space, packed little-endian 24-bit words;
- `dm.words` / `pm.words` — only populated addresses, suitable for conversion
  to another disassembler format;
- `metadata.json` — file offsets, hashes, segment relocation, block maps and
  resolved relocation records.

The binary images fill unpopulated addresses with zero. Use the `.words` maps
or block metadata when loaded zeroes must be distinguished from gaps.

## Verified extraction results

Build 103-492:

| Overlay | ID | File offset | Record bytes | DM words | PM words |
|---|---:|---:|---:|---:|---:|
| V.34 | `0x0261` | `0x5ce96` | 60,394 | 9,248 | 10,345 |
| V.90 DPCM | `0x026a` | `0x8c9f0` | 57,535 | 8,112 | 10,199 |
| V.90 APCM | `0x026b` | `0x9aab2` | 57,239 | 8,674 | 9,824 |

Build 117-926:

| Overlay | ID | File offset | Record bytes | DM words | PM words |
|---|---:|---:|---:|---:|---:|
| V.34 | `0x0261` | `0x139282` | 61,594 | 9,320 | 10,605 |
| V.90 DPCM | `0x026a` | `0x1de5ac` | 58,515 | 8,098 | 10,443 |
| V.90 APCM | `0x026b` | `0x1eca42` | 57,419 | 8,692 | 9,852 |

All declared section sizes, block counts, and the final combifile length match
exactly for both files. All four relocation forms and DWORD PM byte packing are
now recovered from the shipping MIPS protocol loader, as described below.
Across build 117-926 the extractor resolves 4,265 type-0 and 40,802 type-2
fixups; types 1 and 3 are supported although these combifiles do not use them.

## Container references

The parser follows the structures published in the historical Linux Eicon
driver (`drivers/isdn/hardware/eicon/dsp_defs.h`) and its userspace loader
(`divactrl/load/common/dsp_file.c`):

- 48-byte combifile and nested-file magic fields;
- little-endian `t_dsp_combifile_header` and `t_dsp_file_header`;
- memory-block, segment, symbol and data-block tables;
- fixed segments 0-3 and relocatable segments beginning at 4;
- DM words and DWORD-container PM words;
- Eicon relocation byte masks.

This container is distinct from the simpler IDMA/BDMA boot-page format in
section 6.1 of `addspv90guide.pdf`, although both ultimately describe ADSP-218x
PM and DM loads.

## Standalone ADSP-2181 execution prototype

`tools/adsp2181emu/` now contains a standalone ADSP-2181 interpreter adapted
from MAME's BSD-licensed ADSP-21xx core. It has separate 16K-word PM/DM spaces,
the ADSP register banks, DAGs, ALU/MAC/shifter, loops, SPORT callbacks and the
full 24-bit instruction dispatcher. It intentionally has no Eicon peripheral
model yet.

Build it with:

```bash
make -C tools/adsp2181emu
```

A first real execution test uses the bootable primary-rate kernel:

```bash
./tools/eicon_dsp_extract.py docs/firmware/dspdload.bin \
  --match '^DIVA Server PRI 30M Kernel' -o /tmp/eicon-kernel

./tools/adsp2181emu/eicon_adsp_run \
  /tmp/eicon-kernel/0009-diva-server-pri-30m-kernel/pm.bin \
  /tmp/eicon-kernel/0009-diva-server-pri-30m-kernel/dm.bin 100000
```

The corrected interpreter run executes reset PM `0x0000` (`0x18580f`), jumps
to the kernel entry at `0x0580`, initializes the ADSP and reaches its IDLE at
PM `0x02a9` after about 100 instructions. The earlier apparent `0x0f1858`
SPORT path was caused by treating DWORD PM as an ordinary little-endian
integer; it was byte-rotated and is not a valid result.

Set `ADSP_TRACE` to trace the first N instructions:

```bash
ADSP_TRACE=64 ./tools/adsp2181emu/eicon_adsp_run <pm.bin> <dm.bin> 1000
```

`ADSP_RX0`/`ADSP_RX1` and `ADSP_TX0`/`ADSP_TX1` attach little-endian signed
16-bit SPORT streams. `ADSP_TRACE_SPORT=N` logs the first N transfers, and
`ADSP_START_PC` is available for entry-point experiments.

### Recovered MIPS relocation loader

The PRI protocol image `docs/firmware/te_dmlt.pm` is little-endian MIPS. Its
routine at file offsets `0x75e04..0x75f30` reads each DWORD PM container and
performs relocation. The input layout is:

```text
host word 0: instruction[23:8]
host word 1 low byte: instruction[7:0]
host word 1 high byte: relocation type | segment number
```

In pseudocode, after reconstructing the 24-bit instruction as `v` and looking
up the allocated segment base `b`, the four cases are:

```text
type 0: PM v += b << 8; DM word += b
type 1: v += b            # low PM data part
type 2: v += b << 4       # standard ADSP command
type 3: permute(v); v += b << 2; inverse_permute(v)
```

The exact type-3 masks are implemented in `tools/eicon_dsp_extract.py` from
MIPS offsets `0x75eb0..0x75efc`. As a semantic check, V.90 DPCM PM `0x1900`
resolves from its container form to instruction `0x872f71`; its direct DM
operand is `0x32f7`, inside referenced segment 4 (`0x32f0..0x32f7`).

The extracted kernel, `TIKRNL81.F34`, and V.90 DPCM images can be composed in
load order with `tools/eicon_adsp_bundle.py`. There are 117 differing overlaps:
108 words where TIKRNL replaces the kernel task-loader window and nine where
DPCM replaces TIKRNL state/code. Preloading all three before reset is not a
valid boot sequence: the TIKRNL replacement removes the boot kernel entry.
The boot kernel must first run to IDLE, then receive task descriptors through
the Eicon host interface and perform the staged loads itself.

The emulator now includes the ADSP-2181 interrupt controller, IDMA PM/DM
transfer protocol, PMOVLAY/DMOVLAY registers and two external 8K PM/DM banks.
The corrected register map removes the false reset-time writes to invalid
registers: group-1 registers 14 and 15 are PMOVLAY and DMOVLAY.

The resident PRI kernel enables only mask bit `0x020`, the SPORT0 receive
interrupt, and idles at `0x02a9`. Pulsing it vectors through PM `0x0014`; this
is the 8 kHz PRI sample/TDM path, not a task-download mailbox. With no line
interface model it emits G.711 idle code `0x00ff` and passes words along SPORT1,
which is consistent with the physical DSP chain on a 30-channel card.

A staged-load experiment now performs the real sequence more closely:

1. boot the PRI kernel to IDLE;
2. write TIKRNL81.F34 and V.90 DPCM populated PM/DM words without resetting;
3. CALL TIKRNL's zero-sized exported label at PM `0x0672` with a valid return
   stack entry;
4. return cleanly to kernel IDLE and clock SPORT0 receive interrupts.

The TIKRNL initializer clears/configures its PM window and populates its data
structures (including DM `0x3184..` and the task tables at `0x31c8..`). It does
not activate a modem channel by itself. Calling V.90 symbol 0 as code was ruled
out: it is a one-word fixed data symbol at DM `0x3602`, not an entry point.
The MIPS-side assignment boundary is now located. In `te_dmlt.pm` build
107-79, file-backed runtime objects use address bias `0x80011000`. Resolving the
trace-format references identifies generic `dsp_assign` at file offsets
`0x79cc4..0x7b978` and PRI `dsp30_assign` at `0x9775c..0x97dcc`.
`tools/eicon_dsp_assign.py` verifies these xrefs directly from the protocol
image.

The two routines have different roles. `dsp30_assign` pairs PRI transparent or
voice tasks such as download IDs `0x0065/0x0066` and `0x01f9/0x01fa`. Generic
`dsp_assign` selects download ID `0x0258`, `TIKRNL81.F34`, for a modem service;
it is therefore the relevant path even on the 30-DSP E1 card. The routine
allocates a per-DSP resource, copies the assignment parameter blob, and sets up
an asynchronous command state machine. It does **not** synchronously write a
flat modem database before returning.

For TIKRNL, the `0x0258` branch resolves symbol-table entries 13 and 14. In
build 117-926 these are fixed DM symbols:

| Symbol | DM address | Words | Role |
|---:|---:|---:|---|
| 13 | `0x3310` | 21 | MIPS-to-TIKRNL command/database mailbox |
| 14 | `0x3338` | 27 | TIKRNL-to-MIPS mailbox |

The mailbox transport is now recovered in more detail. The request parser is
at MIPS file offsets `0x78138..0x78388` and the sender is at
`0x786a4..0x78cc0`. The symbol-13 structure starts with control fields and owns
a 16-word circular command ring:

| Symbol-13 offset | Initial value | Meaning |
|---:|---:|---|
| `+0` | `0x0000` | command selector/doorbell written on commit |
| `+5` | `0x3327` | MIPS producer pointer |
| `+6` | `0x3327` | DSP consumer/shadow pointer |
| `+7` | `0x0010` | ring length in words |
| `+8` | `0x3327` | ring start |

For each commit the sender writes command words into DM
`0x3327..0x3336`, wrapping at 16 words, then writes the new producer pointer to
DM `0x3315`. It writes the request's selector to DM `0x3310` and the request's
control word, with bit `0x0020` cleared, to DM `0x3338`. These are the exact
post-initializer writes the emulator must reproduce.

The upper request is a byte record whose byte 2 selects one of five parser
forms. Form 0 selects a command script using bytes 3 and 4; form 1 queues a raw
byte payload; forms 2 and 3 update the `0x3338` control and `0x3310` selector
respectively; form 4 updates a separate state word. Script selection is
`script[mode][code]`, with two modes and codes below 75. The pointer table is at
MIPS file offset `0xeb248`. Script records consist of a word count, a
mask/opcode word and the remaining argument words; zero terminates and a
negative word branches within the script. `tools/eicon_dsp_assign.py` now dumps
that complete pointer map.

A second path carries the initial database itself. The helper at MIPS file
offsets `0x7bbf8..0x7bd0c` appends one database record to a host buffer. It
resolves the requested download symbol/location, emits that 16-bit DM address
little-endian, and appends arguments according to a compact format string:
`b` emits one byte and `w` emits one little-endian word. The strings adjacent
to `DSP_DRV.C` include formats such as `wbbww`, `bbbbb`, `bbbbww`, `bbb`, and
`bw`.

The commit helper at `0x7cd14..0x7cf18` writes the completed buffer into a
second ADSP circular ring. Its exact sequence is:

1. read and validate the ring producer against its start and length;
2. write a header containing the payload length and resource flags;
3. write the selected database/task identifier;
4. bulk-write the generated address/value records, splitting at ring wrap;
5. publish the updated producer pointer last.

The modem-specific switch-on branch is selected when the assigned task ID is
`0x0258`. It builds the TIKRNL/F34 setup block, appends additional records with
the format helper, and commits it through this ring. For example, one recovered
call selects database location `0x16` or `0x1c` and emits a `bbb` record with
three one-byte values. This is the database-write path originally being sought;
the symbol-13 command mailbox is the later runtime control path.

Thus `dsp_assign` itself allocates and loads the resource, switch-on commits the
initial database ring, and subsequent asynchronous requests use the symbol-13
command mailbox. Reproducing only one of those stages cannot activate a modem
channel. The emulator accepts `ADSP_POST_DM_WORDS` specifically for captured or
reconstructed post-initializer writes. SPORT0/SPORT1 remain only the PCM
highway.

`--card-type` can now apply the combifile directory's usage masks. Card type 23
(the older DIVA Server PRI 30M profile) maps to file set 5 and selects the
DPCM-capable digital-side task family:

```bash
./tools/eicon_dsp_extract.py docs/firmware/dspdload.bin \
  --card-type 23 --list
```

## Next reverse-engineering step

Load `pm.bin` as little-endian packed 24-bit ADSP-218x program memory and use
`pm.words` to mark populated ranges. Load `dm.bin` separately as little-endian
16-bit data memory. Start with a build-to-build diff:

1. align the same overlay IDs (`0x0261`, `0x026a`, `0x026b`);
2. identify unchanged PM routines and DM tables;
3. locate tables shared by V.34 and both V.90 overlays;
4. search the V.90 DPCM/APCM differences for Phase 3 and DIL-specific control;
5. use the V.34 training sequences and known ITU constants as anchors before
   assigning function names.

A disassembler must explicitly support ADSP-218x 24-bit instructions. Ordinary
x86/ARM `objdump` cannot decode these images.

## Script sender and command-ring semantics (2026-07-27)

Disassembly of the build 107-79 mailbox sender (te_dmlt.pm file
`0x786a4..0x78cc0`, capstone MIPS) recovered the exact command commit:

- The script interpreter walks 16-bit records: word 0 is the record length
  (including itself and the mask word), word 1 is a bit mask, and one argument
  word follows per set mask bit. A record with bit 15 set is a relative
  branch; a zero word terminates the script. Arguments whose mask bit is 3 or
  6 are shifted right by one unless a global flag is set. A NULL script
  pointer selects the shared empty script at file `0xeaf2c`.
- Record words are written to a 16-word ring in **PM data space** at PM
  `0x3327..0x3336`: the sender forms host-port address `ring_pos + 0x4000`,
  and the host-port helper (runtime `0x80082950`) treats addresses with bit
  `0x4000` set as 24-bit PM writes (data word then a zero pad byte), lower
  addresses as single 16-bit DM writes.
- The commit then writes the producer pointer to DM `0x3315`, the command
  selector to DM `0x3310`, and the control word with bit `0x20` cleared to DM
  `0x3338`.

The emulator's IDMA model previously had the bit-`0x4000` PM/DM select
inverted; this is fixed and covered by `adsp2181_core_test`.

Dynamic probing with the staged TIKRNL image (watchpoints +
`ADSP_TRACE_HOST`) shows:

- The resident kernel's SPORT0 RX ISR at PM `0x0072` is a per-timeslot TDM
  state machine: DM `0x2e44/0x2e45` hold channel-table pointers (`0x2e00`),
  DM `0x2e50` is a per-channel substate countdown, DM `0x2e52` holds the
  current PCM code (`0x00ff` idle). A staged but unassigned TIKRNL never runs:
  no IRQ (0/1/2/6) doorbells the command mailbox, and the DSP never reads DM
  `0x3310`.
- Channel activation therefore requires the `dsp_assign` initial database
  writes (the database-ring commit path at MIPS file `0x7cd14..0x7cf18`),
  which must hook a channel-table entry to the modem task before command
  scripts mean anything.

## Next reverse-engineering step

Reconstruct the complete `dsp_assign` write sequence for a modem (task
`0x0258`) assignment: disassemble `dsp_assign` (file `0x79cc4..0x7b978`), the
database record builder (`0x7bbf8..0x7bd0c`, format chars `b`/`w`) and the
ring commit (`0x7cd14..0x7cf18`), and emit the resulting DM/PM word map for
the emulator's `ADSP_POST_DM_WORDS`/`ADSP_HOST_SCRIPT` replay. Success
criterion: the SPORT0 ISR's channel-table walk reaches TIKRNL channel state
(the `0x2e00` table entry for the assigned timeslot points into TIKRNL
structures) and DM `0x3310` command selectors start being consumed.

## Database ring commit and kernel task dispatch (2026-07-27, session 2)

The database ring commit helper (MIPS file `0x7cd14..0x7cf18`) is fully
decoded. Its ring descriptor (MIPS-side struct) has: `+0x0c` producer-pointer
DM address, `+0x10` PM flag (clear: payload words are written to PM via the
`+0x4000` host-address convention), `+0x12` ring start, `+0x14` ring length.
Each commit writes: header word `(payload_bytes + 2) | resource_flags`, then
the database/task identifier word, then the byte payload (packed two bytes
per word, wrap-split), and publishes the producer last. The record builder
(file `0x7bbf8..0x7bd0c`) emits `[DM addr lo, DM addr hi, value bytes...]`;
its address operand resolves either a download symbol (8-byte table entries,
address at `+4`) or a database location id, and its format string emits one
byte (`b`) or one little-endian word (`w`) per character from a varargs list.

The `0x0258` tail of `dsp_assign` (runtime `0x8008c6e8..0x8008c978`) only
initializes the per-channel mailbox/request struct: `+0x140/0x144` = DSP host
register block, `+0x148` = TIKRNL symbol 13 (`0x3310`), `+0x14a` = symbol 14
(`0x3338`), plus timeout defaults (`0x80`) and zeroed state. All actual DSP
writes happen later through the asynchronous request/script machinery.

On the DSP side, watchpoint+trace analysis of the resident kernel shows the
foreground idle loop at PM `0x02a8..0x02ac` reads the channel-table pointers
DM `0x2e44/0x2e45` (-> `0x2e00`) and there is an indirect `CALL (I4)` at PM
`0x02a4` — the kernel's task dispatcher (further indirect calls through I4 at
PM `0x01d9/0x01ed/0x0266`, indirect jumps at `0x02fe/0x0522..`). DM
`0x2e00..0x2e3f` is the 64-entry G.711 timeslot buffer (`0x00ff` idle);
channel descriptors start at `0x2e40`; a second pointer/link table lives at
`0x2f00..0x2f2b`. DM `0x3fe0..0x3fff` is the ADSP-2181 system-register page
(`0x3ff9 = 0x8000` and `0x3ffa = 0` are read by the ISR every frame but are
not a host command doorbell — poking them changes nothing).

A standalone ADSP-218x disassembler now exists (MAME `2100dasm.cpp`,
BSD-3-Clause, fetched from the mamedev mirror) and is wired up as
`/tmp/dasm`; it decodes ALU/MAC/control flow correctly but mislabels the
direct DM read/write opcodes (`10dd ddaa ..` form, address in bits 4..17),
so watchpoints remain the ground truth for those.

Caller-scan of the protocol image shows the modem database setup is the dense
`db_record_append`/`db_ring_commit` cluster at file `0x8892c..0x8a50c`
(roughly a dozen append+commit pairs), with `request_parser` called from file
`0x92e50` and `0x9f05c`. Next step: disassemble that cluster to recover the
exact switch-on database contents (ring target, record list, values) for a
modem answer assignment, then replay through `ADSP_HOST_SCRIPT` and verify
the kernel's `CALL (I4)` dispatcher reaches TIKRNL channel code.

## Host doorbell and kernel command queue (2026-07-27, session 3)

The host doorbell is **IRQE** (enum 6, priority 5, imask bit `0x0100`,
vector `0x18`). The vector contains a bare RTI: its only purpose is to wake
the kernel foreground from IDLE. After an IRQE wake the foreground leaves its
`0x02a8..0x02ac` idle loop and runs the queue processor at `0x02ad..`:

1. rebuilds the free-list links at DM `0x2f27..0x2f2b` (-> `0x2f21`, `0x2f00`,
   `0x2f0e`, `0x2f42`, `0x2f4e` — five message entries);
2. reads the queue head/tail DM `0x2f08/0x2f09` (equal = empty);
3. calls the service dispatcher (`0x01c1` -> `0x02a1` -> `0x01b2` ->
   `0x00d8`) which walks the per-frame descriptor at DM `0x2f00`:
   field `+0` flags/command, `+1` = `0x2800`, `+4` = DM data pointer
   (dereferenced), `+0x0c/+0x0d` state; DM `0x2e78` is cleared when a queued
   entry was present.

Harness notes: level-sensitive IRQs must be held asserted across two
`adsp2181_run` slices because `check_irqs` runs at run-entry and the SPORT0
ISR masks IRQ1/IRQE (priority 5/7) while active. The kernel restores imask
from the status stack on RTI, so `ADSP_FORCE_IMASK` is re-applied every host
word. IRQ2's vector (`0x0004`) is a parked IDLE — it is not the doorbell.

Open: the exact queue-entry semantics for task download / channel assign.
Queue pushes with head!=tail are consumed silently (pointers normalized back
to `0x2f00`), and the per-frame descriptor at `0x2f00` is processed every
8 kHz frame regardless. The next step is decoding the queue processor at PM
`0x02ad..0x02c0` and the service routine at `0x00d8..0x0109` statically, then
replaying a task-download queue entry for TIKRNL so the boot kernel performs
the staged load itself (the real boot sequence), instead of pre-staging.

## MIPS shim: real firmware routines drive the emulator (2026-07-27, session 4)

`tools/eicon_mips_shim.py` runs the actual te_dmlt.pm routines under Unicorn
(physical kseg0 mappings — this unicorn build has unreliable guest data
accesses for pages first written after execution starts, and kuseg pages) and
connects their host-port calls to the ADSP-2181 emulator via ctypes
(`libadsp2181.dylib`). host_write (`0x80082950`) and host_read (`0x80082920`)
are hooked; `adsp2181_host_write/read` implement the exact IDMA semantics.

The command-script sender (`0x800896a4`) takes `a0` = request struct:
`+0/+4` host-reg pointers, `+8` symbol-13 address (`0x3310`), `+0xa`
symbol-14 (`0x3338`), `+0xc` active flag, `+0x10` script code (<75),
`+0x11` script mode (<2), `+0x12` command selector, `+0x14` script pc,
`+0x1c` request form (0=script, 1=single word, >=2=raw byte payload at
`+0x1e`), `+0x3e` control word. Script table index is `mode*75 + code`
(earlier "79" was wrong) into the pointer table at `0x800FC248`.

Verified end-to-end: with code 66 (mode 0) the sender writes the ring records
`a001 0708 | a00d 0a28 4333 0286 | e007 004b` to PM `0x3327..`, advances the
producer to `0x3331`, writes selector `0x0001` to DM `0x3310`, and clears the
control word — matching the statically recovered script (including the
mask-bit-3 `>>1` argument rule: `0x050c -> 0x0286`).

The DSP does not consume the command yet (consumer stays `0x3327`): channel
activation/doorbell on the DSP side is still required (kernel queue vector +
IRQE, or TIKRNL's own consumer hooked into the frame loop).

Also: the kernel queue handler at `0x01c1` performs `CALL (I4)` on queued
entries — queue entries carry function vectors. The per-frame descriptor at
DM `0x2f00` (fields: `+0` flags, `+1 = 0x2800`, `+4` DM data pointer) is
serviced every 8 kHz frame by the routine at `0x00d8`.

## Session 5: parser path live, kernel scheduler model complete (2026-07-27)

- The shim now drives the full top-level path: byte request
  `[len, ?, form, code, mode]` -> request_parser (`0x80089138`) ->
  script_sender, reproducing the script-66 PM-ring commit through the real
  firmware code path (not just the sender in isolation).
- Kernel scheduler model: five service slots whose entry pointers live at
  DM `0x2f27..0x2f2b` (entries at `0x2f21/0x2f00/0x2f0e/0x2f42/0x2f4e`).
  IRQ/service handlers each own a slot (SPORT0 TX -> `2f27`, IRQ2 -> `2f28`,
  IRQL1/2 -> `2f29`, timer -> `2f2a`, `2f2b` spare) and CALL the slot's
  vector. The foreground per-frame service uses slot `2f28` (entry `0x2f00`,
  vector field `+1`, currently the inactive `0x2800`).
- TIKRNL's vector table (DM `0x31bb`) entries are wrappers that CALL the
  kernel's `0x01b2/0x00d8` service routine and then dispatch via SR0 — the
  kernel and task kernels share the scheduler.
- TIKRNL init (entry `0x672`) calls `0x0637/0x184d/0x064a`; `0x184d` exports
  service vectors `0x05ab/0x05b1` (or `0x05b7/0x05be`) into DM `0x3307+`.
- Service-driver table in te_dmlt.pm at file `0xeaec4`: {release `0x8008c978`,
  `0x80096980`, `0x80098310`, `0x80098614`, `0x80099734`, `0x800a6820`,
  `0x8009fae8`, `0x800a6874`, `0x800a687c`, `0x800a68c0`, `0x800a318c`} —
  the modem service entry points (assign is reached via this table, not by
  direct `jal`).
- Harness: `ADSP_STAGE_ENTRY2_AT` (call after word N), `ADSP_TRACE_AT_WORD`,
  hex-safe entry parsing. `tools/adsp2181_dis.py` decodes the full
  ADSP-2181 kernel+TIKRNL images reliably.

Next: run the modem service assign entry (table slot 1, `0x80096980`) in the
shim with a synthesized TIKRNL download struct (segments/symbols from
metadata.json), which performs the switch-on database commit; then feed the
E1 timeslot stream on SPORT0 (a call can also be signalled from the E1 side:
the kernel ISR walks timeslots and CAS/signalling arrives in-band).

## Session 6: service-assign entry runs live in the shim (2026-07-27)

`tools/eicon_mips_shim.py` gained an `--assign` mode that calls the real
service-assign entry `0x80096980` (file `0x85980`, service-driver table slot
1) under Unicorn with a synthesized TIKRNL download/task struct, so the
firmware's own code performs the switch-on database commit through the
hooked host port — not a hand-replayed write sequence.

Reverse-engineering required to make the routine run:

- **Correct `$gp`.** The image entry (file `0x4774`/`0x4764c`) sets
  `gp = 0x8010.0000 - 0x5c4b = 0x800fa3b5`. The shim's previous hardcoded
  `0x80108000` was wrong; it left the trace-printf pointer at `gp+0x1a7b`
  (`0x800fbe30`) NULL, so the first `jalr $v0` in the assign trace path
  faulted to address 0. The real pointer is file-backed and equals
  `0x80083180`.
- **Trace-printf redirection.** The real printf (`0x80083180`) writes to the
  hardware trace buffer at `0xa0005d20` (uncached kseg1). The shim overwrites
  the pointer at `0x800fbe30` with the no-op stub address so trace calls
  return immediately.
- **Three MIPS memory segments** the shim now maps: the code image
  (`0x11000`–`0x111000`, file-backed), the `.data`/`.bss` segment
  (`0x80200000`, physical `0x200000`, zero — the lookup tables at
  `0x80272c90` etc. are *not* in `te_dmlt.pm`, which ends at `0x100230`),
  and the runtime stack/heap segment (`0x80300000`, physical `0x300000`, zero
  — `sp = 0x80338700` and the database-record buffers at `0x80331c12` live
  here). An auto-map hook covers neighbouring pages and a low kuseg guard
  page so NULL-ish dereferences surface as zero instead of stopping the run.
- **Synthesized struct.** `0x80096980` takes `a0` = an assign request whose
  `+0` -> base (s2), `+4` -> resource (s0), `+8` -> existing mailbox (0 for
  fresh), `+0x18` = channel byte. `s0+4` -> a download descriptor with id
  `0x0258` at `+0`; `s0+0x40` = task id halfword. `s2+0xc` -> a channel
  context whose `+0x24` -> a descriptor; `s2+0x10` -> the host register
  block (data port `+0`, address port `+0x80`). The per-channel state
  `s1 = s2+0x200` owns the command mailbox (`+0x24`, active flag `+0x10`)
  and the database-ring descriptor (`+0x0c` producer DM offset, `+0x10` PM
  flag, `+0x12` ring base, `+0x14` ring length).

The dispatch for task `0x0258` (none of `0x213/0x1f5/0x1ff/0x227/0x2bd` match)
runs `0x80093d14` then `0x80090e58`. `0x80093d14` is a synchronous
command/handshake: it calls `0x80086af8` (DSP wait) and, on success,
`0x80093ba4` -> `0x8008cacc` (send a command word via `host_write`). For a
fresh assign the mailbox active flag makes `0x80086af8` return nonzero so
`0x80090e58` (the db record-append + ring-commit body) runs. `0x80090e58`
calls `db_record_append` (`0x8008cbf8`, 7x) and finishes at `0x80093b50`
with `db_ring_commit` (`0x8008dd14`).

Verified: `--assign` produces host_write transactions through the real
firmware path — the ring header to the PM ring and the producer-pointer
publish to DM — i.e. the switch-on database commit is live. The DSP does
not yet consume the command, because the synthesized database ring targets
DM `0x0001` rather than the real TIKRNL symbol-13 ring at `0x3327`; the
remaining work is feeding the correct ring descriptor (real TIKRNL
database-ring DM address and segment/symbol relocation table from
`metadata.json`) so the kernel's channel-table walk hooks the modem task.

## Session 7: Linux driver source + PR_RAM request queue (2026-07-27)

The `divas4linux` driver source (in `/tmp/divas4linux-master`) provides the
complete host-side architecture, confirming the reverse-engineered model:

- **`kernel/pr_pc.h`**: `struct pr_ram` — the shared-RAM request queue.
  `NextReq`/`NextRc`/`NextInd` are word offsets into `B[]` (the buffer area
  at +0x20).  `ReqInput`/`ReqOutput` are byte counters.  `REQ`/`RC`/`IND`
  structures form linked lists via their `next` field.
- **`kernel/di.c` `pr_out()`**: the host writes a `REQ` at `B[NextReq]`,
  advances `NextReq = REQ->next`, increments `ReqInput`.  The MIPS reads
  from `B[read_offset]` (gp+0x5e99), advances via `REQ->next`, increments
  `ReqOutput`.
- **`kernel/mi_pc.h`**: shared RAM at physical `0x1000`, protocol at
  `0x11000`, boot structure (`struct mp_load`) at `0x0`.
- **`kernel/mdm_msg.h`**: complete modem CAI byte layout (hardware type
  `0x11` = modem async, V.8 negotiation, modulation masks, speeds).
- **`kernel/message.c` `add_modem_b23()`**: CAPI→IDI modem call path.
- **`kernel/s_pri.c`**: PRI card init, DSP detect (`dsp_addr_port` at
  `+0x80`, `dsp_data_port` at `+0x00` — confirms the IDMA hook).

The shim's `--mainloop` mode now:
1. Maps shared RAM (physical `0x0`–`0x11000`).
2. Fixes the auto-map hook for kseg0/kseg1 (translates `0x8xxx`/`0xaxxx` to
   physical via `& 0x1fffffff`).
3. Calls the real firmware entry (`0x80082f90`) which stores the PR_RAM
   pointer and runs basic init.
4. Calls the post-wait init functions (`0x80083d10`, `0x8002a534`).
5. Hooks the DSP register region (physical `0x380000`+, computed from
   `DSPInfo=0x80`) with `_dsp_read`/`_dsp_write` routing to the ADSP IDMA
   interface.
6. Writes a modem `ASSIGN` request to the PR_RAM queue and runs the main
   loop (`0x80027970`).

**Result**: the MIPS main loop runs and reads the ASSIGN request from PR_RAM.
The firmware entry produces IDMA writes to the DSP (PM code download at
`0x3e8+`).  However, the init's DSP presence check (`lhu $s2, ($s1)` at
`0x80380000`) returns 0 because the ADSP's DM[0] is 0, so the firmware skips
DSP resource registration (gp+0x5eb9 stays 0).  Without registered DSPs, the
ASSIGN can't allocate a channel and produces no host_writes.

**Remaining work** (well-defined, not exploratory):
1. Model the DSP presence check: the firmware writes `0xFF` to DM[0x3f] via
   the addr/data ports and reads it back.  The `_dsp_read`/`_dsp_write` hooks
   must correctly route this through `idma_addr_write`/`idma_data_write`/
   `idma_data_read` so the write-back-read returns `0xFF`.
2. Load the combifile (`dspdload.bin`) into shared RAM at `DspCodeBaseAddr`
   (computed from the protocol image's end address) so the firmware can
   download DSP code from it.
3. Once the init detects DSPs and registers them (gp+0x5eb9 != 0), the
   main loop will process the ASSIGN request, calling `dsp_assign` and
   downloading the V.90 overlay internally.
## Session 8: linked call assignment and bearer activation

The network-layer `0xe6` rejection was a missing call-parent link, not a bad
modem LLC/DLC. In the Linux driver's `message.c`, the first
`nl_req_ncci(..., ASSIGN, 0)` is sent with global `NL_ID`; `send_req()`
prepends `CAI, 1, plci->Sig.Id` to the parameters. The shim previously sent
only LLI/LLC/DLC, leaving the firmware no signalling entity (PLCI) to attach
the network entity to.

`modem_nl_assign_payload()` now accepts the assigned signalling ID and emits
that CAI prefix. The native PR_RAM sequence consequently succeeds:

```
[sig] RC 0xef (ASSIGN_OK) Id=0x02 Ref=0x0000
[nl]  RC 0xef (ASSIGN_OK) Id=0x03 Ref=0x0001
```

The shim also writes the REQ `Reference` field explicitly, can submit the
network-layer `N_CONNECT`, and drains the PR_RAM indication chain. With
`--connect`, firmware accepts bearer activation and produces:

```
[call] RC  0xff (OK) Id=0x03 Ch=0x02 Ref=0x0001
[call] IND 0x03      Id=0x03 Ch=0x02   # N_CONNECT_ACK
[call] IND 0x04      Id=0x03 Ch=0x02   # N_DISC
```

The initial experiment disconnected because it activated NL without first
answering the parent signalling call. `--simulate-b-channel` now models the
answered incoming sequence: linked SIG+NL assignment, `CALL_RES` on SIG, then
NL activation. Both entities return `IND 0x03`, and no `N_DISC` appears after
512 main-loop iterations; the harness reports the simulated B-channel
`ACTIVE`.

RING and CID therefore belong to signalling before modem activation, as
expected. The next boundary is DSP resource startup: the held B-channel
currently produces no post-boot IDMA writes, so the switch-on database has
not yet initialized TIKRNL/DIAL before NORM/V.8.

Tracing the two modem-service entry points makes the missing state precise:
neither service assign `0x80096980` nor switch-on `0x80090e58` executes.
The `ASSIGN DSIG_ID` result (`Id=0x02`) is the global/listener signalling
entity. A real incoming call first produces `CALL_IND` and allocates a
per-call PLCI; `connect_res()` attaches `add_b1()`'s modem CAI to `CALL_RES`
on that PLCI. Sending `CALL_RES` to the listener can return `OK` and keep NL
from immediately disconnecting, but it does not allocate a modem DSP.

The simulator now reports this honestly as
`SIGNALLING ACTIVE, DSP UNASSIGNED`. The next implementation step is to inject
the network-side incoming-call event through the signalling handler (creating
the per-call entity), then issue linked NL ASSIGN and CAI-bearing CALL_RES
against that new entity.

## Session 9: native signalling trace and direct service-assign proof

`tools/eicon_mips_shim.py` now has `--trace-calls`, which records MIPS
`jal`/`jalr` targets by harness phase. The trace normalizes Unicorn's physical
PCs back to the protocol image's `0x800...` runtime addresses, so the output
can be compared directly with disassembly and earlier recovered entry points.

The native PR_RAM path is reproducible with:

```bash
.venv/bin/python tools/eicon_mips_shim.py \
  --kernel artifacts/eicon-dsp/build-117-926/kernel/0009-diva-server-pri-30m-kernel \
  --tikrnl artifacts/eicon-dsp/build-117-926/tikrnl/0258-tikrnl81.f34-task \
  --mainloop --simulate-b-channel --call-steps 2 \
  --trace-calls --trace-call-limit 120
```

Result: DSP resource registration is healthy (`gp+0x5eb9=0x0060`, 30 DSPs
answer the `0xa5a5` boot handshake), SIG and NL assignment both return
`ASSIGN_OK`, and `CALL_RES`/`N_CONNECT` both return/indicate success, but the
modem DSP path remains unentered:

```text
[call] simulated B-channel: SIGNALLING ACTIVE, DSP UNASSIGNED
[mainloop] modem DSP path: service_assign=0 switch_on=0
```

The phase trace pins down the boundary:

| Phase | Distinctive firmware calls | Meaning |
|---|---|---|
| `sig-assign` | `0x800c99e4` x4 | SIG ASSIGN copies/normalizes the listen/register parameter block. |
| `call-res` | `0x800c9470` x3 | CALL_RES runs signalling IE parsing/serialization, not modem service assignment. |
| all native phases | no `0x80096980`, no `0x80090e58` | The listener entity never becomes a per-call PLCI in the synthetic sequence. |

Disassembly around `0x800c9470` shows the IE walker/copy helpers and calls
into `0x800c99e4`; it is useful for reconstructing signalling payload format,
but it is downstream of the missing network-originated incoming-call event.
The viable clean route is therefore still to inject the incoming SETUP/CALL_IND
event before `CALL_RES`, so the firmware allocates a per-call PLCI instead of
answering the listener entity.

The direct allocator route is also live. This command:

```bash
.venv/bin/python tools/eicon_mips_shim.py \
  --kernel artifacts/eicon-dsp/build-117-926/kernel/0009-diva-server-pri-30m-kernel \
  --tikrnl artifacts/eicon-dsp/build-117-926/tikrnl/0258-tikrnl81.f34-task \
  --assign --words 40
```

calls the real service-driver entry `0x80096980` and produces the switch-on
database record through firmware host writes:

```text
[assign] returned v0=0x80804100 host_writes=17
[assign] TIKRNL command ring DM3327..3336:
001d 0000 0000 0000 00ff 0002 0000 0000
0102 0008 0000 0200 0008 0000 1e00 0000
[assign] host writes:
7327=001d ... 7315=3337
```

So the current hard fact is: **DSP switch-on works when invoked directly;
native CALL_RES is missing the per-call PLCI creation event.** The next code
step is to use the `0x800c94xx`/`0x800c99xx` signalling helpers as format
oracles while locating the upstream network ingress that emits `CALL_IND`
(`0x02`) to PR_RAM.

## Session 10: fake ingress state in the MIPS shim

`tools/eicon_mips_shim.py --simulate-b-channel` now explicitly arms incoming
signalling before answer:

1. SIG ASSIGN returns `Id=0x02`.
2. NL ASSIGN returns `Id=0x03`.
3. `INDICATE_REQ`/listen is posted to the assigned SIG id and returns `OK`.
4. A synthetic incoming-call object is linked into the listening SIG entity.

The entity table used by the firmware dispatcher is at `0x80299928`, with the
entity count in `gp+0x5eb9`. After SIG/NL assignment, the active listener is
slot 0:

```text
[entities] 00: ptr=0x801004e0 ... +14=00000002 +18=800164b8
```

With fake ingress enabled, the shim links a synthetic call object at
`SIG+0x1c` before issuing `CALL_RES`:

```text
[listen] RC 0xff (OK) Id=0x02 Ch=0x00 Ref=0x0000
[ingress] synthetic call object 0x80807000 linked to entity slot 0 obj=0x801004e0
[entities] 00: ptr=0x801004e0 ... +1c=80807000 +24=00000001
```

This proves the harness can fake the firmware-owned ingress state rather than
only pushing host PR_RAM requests. It still does not enter modem service
assignment:

```text
[mainloop] modem DSP path: service_assign=0 switch_on=0
```

So the remaining gap is no longer "how do we fake an ingress at all"; it is
which additional per-call fields the CALL_RES/resource-selection path expects
besides the minimal linkage written by the recovered `0x800172a8` allocation
branch. The branch writes `call+0x2f=1`, `call+0x28=sig`,
`sig+0x24=1`, `sig+0x12a=1`, `sig+0x1c=call`, and clears bit `0x10000` in
`sig+0x20`; later service selection likely depends on the call object's parsed
BC/LLC/CIP fields.

## Session 11: ingress field seeding

The fake ingress path now seeds the fields the recovered incoming setup parser
uses before answer:

| Offset | Seed | Meaning inferred from parser |
|---|---:|---|
| `sig+0x24` | `2` | pending incoming-call state after initial allocation |
| `sig+0x365` | `04 90 90 a3 00` | BC: 3.1 kHz audio / 64 kbit/s / A-law |
| `sig+0x37d` | `04 88 90 21 00` | LLC-style low-layer information |
| `sig+0x395` | `01 80` | channel/additional-info placeholder |
| `sig+0x51f` | `ff` | previous/invalid channel marker |
| `sig+0x520` | `11` | selected modem async resource byte |

`CALL_RES` now also uses the old IDI modem answer payload instead of the
26-byte SIG ASSIGN CAI:

```text
CAI len=6: 11 09 00 00 20 00
```

The run confirms these fields are present in the firmware object before
`CALL_RES`, but the path still stops before `service_assign`:

```text
[entities] 00 ... +1c=80807000 +24=00000002
[entities] 00: sig+340..52f=...049090a3...04889021...ff11...
[mainloop] modem DSP path: service_assign=0 switch_on=0
```

This means the blocker has moved again: the firmware is no longer missing
only obvious parsed BC/LLC/channel fields. The remaining condition is likely
ownership/allocator metadata on the per-call object that the real
`0x800785c4` allocation path creates and the synthetic `0x80807000` object
does not yet reproduce.

## Session 12: PRI/E1 signalling DSP lead

`docs/ADSP-21MOD870.PDF` and `docs/addspv90guide.pdf` are a useful correction
to the call-ingress model. The ADSP-21mod870 reference design is not just a
host plus isolated modem datapumps: its network-access diagram has
line-interface/call-control blocks for `T1,E1,PRI,xDSL,ATM`, and its modem
software guide says that T1/E1 operation programs SPORT0 in multichannel
mode, with DB setup locations for the SPORT0 control registers and `V34SLOT`
selecting the TDM slot used by modem operation.

The actual build-117-926 combifile for card type 23 matches that architecture.
The staged image contains separate PRI line/signalling downloads before the
modem task:

| ID | Download |
|---:|---|
| `0x0007` | DIVA Server PRI 2M TX Kernel |
| `0x0008` | DIVA Server PRI 2M RX Kernel |
| `0x000b` | DIVA Server PRI 2M TX SIG Kernel |
| `0x000c` | DIVA Server PRI 2M RX SIG Kernel |
| `0x0208` | SIG.MDM Task |
| `0x0209` | SIGPRTX Task |
| `0x020a` | SIGPRRX Task |
| `0x0258` | TIKRNL81.F34 Task |
| `0x0270` | SIG Overlay |
| `0x025f` | V8.F34 Overlay |
| `0x026a` | V.90 DPCM Overlay |

`tools/eicon_mips_shim.py` now prints those IDs in its DSP staging summary so
each run shows whether the line/SIG layer is present. A fresh
`--simulate-b-channel` run still answers the synthetic PLCI through SIG/NL but
never reaches DSP assignment:

```text
[mainloop] DSP code staged ... (64 downloads, card type 23 -> file set 5)
           id=0x000b ... DIVA Server PRI 2M TX SIG Kernel ...
           id=0x000c ... DIVA Server PRI 2M RX SIG Kernel ...
           id=0x0208 ... SIG.MDM Task ...
           id=0x0209 ... SIGPRTX Task ...
           id=0x020a ... SIGPRRX Task ...
           id=0x0258 ... TIKRNL81.F34 Task ...
           id=0x0270 ... SIG Overlay ...
[call] simulated B-channel: SIGNALLING ACTIVE, DSP UNASSIGNED
[mainloop] modem DSP path: service_assign=0 switch_on=0
```

That result changes the most likely next route. The fake MIPS object proves
we can satisfy visible PR_RAM request/response state, but it does not reproduce
the internal call-control ownership chain. The better target is now the
PRI/SIG DSP ingress side: either instantiate the SIG.MDM/SIGPRTX/SIGPRRX path
far enough that it emits the normal incoming-call indication into the MIPS
PLCI allocator, or recover exactly what metadata that path passes to
`0x800172a8`/`0x800785c4` and synthesize that object rather than the current
minimal shell.

## Session 13: SIG task registration recovered

`tools/eicon_sig_path_probe.py` now extracts the ADSP-side registration points
for the PRI/SIG path. The probe loads one kernel plus one SIG task, runs the
task's download entry, and diffs PM after the task calls the kernel's service
registration routine.

The task entries and registration results are:

| Task | Kernel | Entry | Registered patch |
|---|---|---:|---|
| `0x0208` SIG.MDM | PRI 30M kernel `0x0009` | `PM 0x0980` | `PM 0x02b9: CALL 0x02a1 -> CALL 0x0999` |
| `0x0209` SIGPRTX | PRI 2M TX SIG kernel `0x000b` | `PM 0x3900` | `PM 0x0032 -> CALL 0x3914` |
| `0x020a` SIGPRRX | PRI 2M RX SIG kernel `0x000c` | `PM 0x3900` | `PM 0x0032 -> CALL 0x390d` |

Probe output for `SIG.MDM`:

```text
[probe] task=0208-sig.mdm-task entry=0x0980
[probe] patch slots before: PM02b9=1c2a1f PM00b5=2a7eea
[probe] patch slots after:  PM02b9=1c999f PM00b5=2a7eea
[probe] PM changes: 1
  PM02b9: 1c2a1f -> 1c999f
```

Probe output for the PRI 2M SIG tasks:

```text
[probe] task=0209-sigprtx-task entry=0x3900
[probe] PM changes: 1
  PM0032: 0d0c7e -> 1f914f

[probe] task=020a-sigprrx-task entry=0x3900
[probe] PM changes: 1
  PM0032: 0d0c7e -> 1f90df
```

`SIG.MDM` is therefore not a vague architectural hunch any more: it installs a
foreground callback at `PM 0x0999`. That callback processes the task's private
state and eventually reaches the DSP-to-host doorbell helper at `PM 0x13a2`.
The helper saves temporary registers at `DM 0x05e2..0x05e4` and calls kernel
service `PM 0x000a` at `PM 0x13d2`, the same DSP-to-host doorbell path used by
TIKRNL. Its queue/format state is centred on:

```text
DM 05de = 0000
DM 05df = 00ab
DM 05e0 = 05f5
DM 05e1 = 0601
DM 05e5..0612 = nibble/order and format tables
DM 0660..06ef = SIG.MDM runtime state block
```

The immediate next target is to drive `SIG.MDM`'s `0x0999` foreground callback
with a populated `DM 0x05de..0x05e4` queue until it toggles the DSP-to-host
service bit at `DM 0x2f17`. Once that event shape is recovered, the MIPS shim
can either deliver the real DSP-side indication into PR_RAM or synthesize the
corresponding allocator metadata at the MIPS call-ingress boundary.

## Session 14: forced modem DSP assignment during fake call

There is now a deliberately simpler path in `tools/eicon_mips_shim.py`:
`--force-modem-dsp-assign`. `--simulate-b-channel` enables it by default.
After `CALL_RES` and `N_CONNECT`, the shim stages a direct PRI-kernel+TIKRNL
core, runs the recovered MIPS modem `SERVICE_ASSIGN` entry (`0x80096980`), and
pumps the TIKRNL command path long enough to observe the real switch-on
database commit.

This bypasses native PRI/SIG call ingress selection; it is a practical shim
affordance for "the bearer is connected, tell the modem DSP to handle it."

Successful run:

```text
[force] staging direct TIKRNL core for modem DSP assignment
[assign] calling 0x80096980 ... ch=1 mb13=0x7310 mb14=0x7338
[assign] returned v0=0x80804100 host_writes=17
[assign] TIKRNL command ring DM3327..3336:
001d 0000 0000 0000 00ff 0002 0000 0000
0102 0008 0000 0200 0008 0000 1e00 0000
[call] simulated B-channel: ACTIVE (modem DSP assigned)
[mainloop] modem DSP path: service_assign=1 switch_on=1
```

So the architectural problem is split cleanly:

1. The harness can now force genuine modem service assignment at the connected
   call boundary.
2. The still-open faithful path is to replace the forced direct TIKRNL core
   with either native PRI/SIG ingress metadata or a real firmware-selected DSP
   resource, then route the bearer PCM into that assigned task.

## Session 15: raw G.711 RX probe into forced TIKRNL

`tools/eicon_mips_shim.py` also has a first RX-side G.711 probe:
`--g711-probe-samples N --g711-probe-code BYTE`. After forced assignment it
writes the raw octet into TIKRNL's line words (`DM 0x3f08`/`0x3f09`) and runs
the task frame entry. If TIKRNL requests an overlay, the probe loads the
extracted image by download ID, sets `BOOTFINISHED`, and resumes the task's
completion entry.

This proves the assigned core can hear raw G.711 codewords and advance through
the task/page machinery:

```text
[g711] served requested overlay 0x0270 from 0270-sig-overlay
[g711] sample 0000: ... 31A9=0001 31AA=0262
[g711] served requested overlay 0x0262 from 0262-dial-fsk-fax.f34-overlay
[g711] sample 0001: ... 3FB0=000b 3FB2=17bb 3FB3=1706 ... 31AA=0263
[g711] served requested overlay 0x0271 from 0271-v.22fc-overlay
[g711] sample 0002: ... 3FB0=0001 3FB2=1582 3FB3=15dd ... 31AA=0266
[g711] served requested overlay 0x0266 from 0266-v.22-v.32-lec-overlay
[g711] fed 16 raw G.711 octets 0xff; line-state changes=5
```

So the current boundary is:

- RX into the forced modem DSP core: working enough to trigger SIG/DIAL/page
  transitions from raw codewords.
- TX back out as a B-channel G.711 stream: still open. Prior V.8 capture work
  shows the generated transmit signal is not written back to `0x3f08/0x3f09`;
  it goes through the kernel SPORT0 TX/channel-table bridge or a task TX
  buffer that still needs to be wired into this forced-call path.

## Session 16: forcing SPORT0 TX from the assigned core

`tools/eicon_mips_shim.py` now has two TX helpers for the forced-call path:

- `--tx-source-scan` pokes candidate DM words with a marker and checks whether
  SPORT0 TX0 emits it.
- `--force-tx-samples N --force-tx-code BYTE` preloads the recovered source
  and captures the resulting SPORT0 TX0 words.

The key correction was to drive the kernel's RX-side TDM interrupt, not only
the explicit SPORT0 TX interrupt. The resident ISR writes TX0 during the
SPORT0_RX timeslot walk. The source scan found the practical output latch:

```text
[txscan] marker 0x0055 source hits: rx:DM2e52->0055
```

Forcing that latch proves byte/codeword-level outbound control:

```text
[force-tx] source DM2e52=0x0055: captured=16 forced=16
top=0055:16 first16=0055 0055 0055 0055 0055 0055 0055 0055 ...
```

So we can force G.711 TX now by preloading `DM 0x2e52` before each
SPORT0_RX-driven TDM slot. This is not yet the modem page's generated TX; it
is the kernel TDM output latch. The next recovery step is to connect the
task-side TX buffer (`DM 0x3fb4` pointer mode, expected target around
`DM 0x2b01`/`0x3f09`) to this latch, or identify where the page writes its
generated sample before the ISR emits `DM 0x2e52`.

## Session 17: tone-driven RX and live TX pointer bridge

`--g711-probe-samples` can now drive the forced modem DSP with synthesized
u-law stimuli:

- `--g711-probe-stimulus constant` preserves the old raw-byte probe.
- `--g711-probe-stimulus tone --g711-probe-freq 2100` feeds a stable tone.
- `--g711-probe-stimulus ansam` feeds a V.8-style 2100 Hz ANSam carrier with
  15 Hz amplitude modulation and 450 ms phase reversals.

The probe can also test a live TX bridge:

```text
--bridge-task-tx
```

This follows the firmware's current `DM 0x3fb4` pointer, copies
`DM[DM 0x3fb4]` into the recovered kernel TDM output latch `DM 0x2e52`, and
then strobes the RX-driven TDM ISR so SPORT0 TX0 emits that value.

With ANSam, the forced core enters a different overlay chain than flat
silence/idle: after `0x0270` and `0x0262`, it requests and serves
`0x0263-dial.f34-partial-overlay`, then moves to `0x0271-v.22fc-overlay`.
The TX pointer also changes from the old pointer-mode buffer to page-owned
addresses:

```text
sample 0002: ... 31AA=0263 3FB4=2277 TXPTR=0000
sample 0004: ... 31AA=0271 3FB4=3764 TXPTR=0000
```

A 512-sample 2100 Hz tone run proves RX tone drive and separates the two TX
effects:

```text
[g711] fed 512 tone 2100Hz amp=20000; line-state changes=486
[g711] bridged task TX DM[3FB4]->DM2e52:
  words=512 unique=4 non_idle=3 top=0000:509,10cd:1,0080:1,fc58:1
[g711] SPORT0 TX0 bridged captures:
  words=512 unique=4 non_idle=3 top=0000:509,10cd:1,0080:1,fc58:1
[g711] SPORT0 TX0 natural captures:
  words=512 unique=126 non_idle=179 top=0000:300,00ff:33,...
```

Interpretation:

- RX can now be driven with real G.711 tone waveforms, not just a flat byte.
- The kernel/TDM side naturally emits varying TX0 words while a tone is being
  received, but that is separate from the explicit task-pointer bridge.
- The live task TX pointer bridge is only seeing three startup non-idle words;
  after the page settles at `DM 0x3fb4 = 0x3764`, `DM[0x3764]` stays zero in
  this forced path. The next target is therefore the page initialization or
  action vector that arms sustained transmit generation, not the SPORT0 latch.

## Session 18: DM 0x3764 TX producer recovered

Static tracing of the extracted `0x0271` V.22FC overlay corrects the earlier
interpretation of `DM 0x3764`. It is not a persistent G.711 buffer and the
`0xfc58` found there in `dm.words` is not an idle code. The overlay initially
uses `DM 0x3680..0x37cb` as boot data; its loader at `PM 0x1dc5` copies that
material into PM and clears the runtime region. The same address range is then
reused as line-adapter state.

The overlay publishes its receive and transmit sample locations during init:

```text
PM 1dc0: AR = 3763
PM 1dc1: DM(3f0f) = AR       ; RX sample pointer
PM 1dc2: AR = 3764
PM 1dc3: DM(3fb4) = AR       ; TX sample pointer
```

The kernel task dereferences the TX pointer once per frame:

```text
PM 076a: I4 = DM(3fb3)
PM 076b: CALL (I4)           ; V.22FC PM 1d06 TX action
...
PM 07bb: I0 = DM(3fb4)
PM 07bc: SR1 = DM(I0,M0)     ; fetch DM 3764
```

`PM 0x1d06` is the line-side sample-rate adapter. Its final path at `0x1d46`
produces `DM 0x3764` from a 20-word circular queue:

| DM | Role |
|---:|---|
| `0x3761` | queued TX sample count |
| `0x3764` | current signed linear TX sample, one word per 8 kHz frame |
| `0x3765` | producer/write pointer, initialized to `0x36e0` |
| `0x3768` | consumer/read pointer, initialized to `0x36e0` |
| `0x36e0..0x36f3` | 20-word circular TX queue |

At `PM 0x1d46`, a nonzero queue count is decremented, one signed sample is read
through `I0/L0=0x14`, the read pointer is saved, and the sample is written to
`DM 0x3764`. An empty queue writes zero. The producer is `PM 0x1d69`; when the
phase/count test at `PM 0x1d1e` fires it synthesizes a block into the circular
queue and increments `DM 0x3761`. The V.22FC page initializer sets
`DM 0x3f67 = 6`, which is the block size used by the adjacent line adapter.

Therefore the sustained zero has a precise meaning: the V.22FC modem engine is
idle or is feeding zero-valued source samples, rather than the TX pointer or
SPORT bridge being broken. Also, `DM 0x3764` is **linear 16-bit PCM**, not a raw
G.711 octet. Copying it directly to the TDM latch is useful as a plumbing probe
but bypasses the TIKRNL post-processing beginning at `PM 0x07db`; natural TX
capture must remain the correctness path.

The forced G.711 probe now reports the `0x3764` adapter independently: number
of nonzero output frames, queue-count range, final read/write pointers, and the
maximum number of nonzero words observed in the 20-word queue. This separates
three failure cases on the next run:

1. queue count always zero: the producer is not being scheduled;
2. queue count advances but the ring remains zero: modem TX source is idle;
3. `DM 0x3764` varies but SPORT0 TX does not: fault is after the page adapter.

The first instrumented 512-frame, 2100 Hz run reports:

```text
[g711] V.22FC page TX adapter DM3764:
  frames=508 nonzero=0 queue-count=0..8
  write=DM36ea read=DM36e6 ring-nonzero-max=0/20 top=0000:508
```

This resolves the three-way test as case 2. The producer is definitely being
scheduled: the queue count reaches eight and both circular pointers advance.
However, no nonzero word ever appears in the queue, so `PM 0x1d46` correctly
emits zero on every steady-state frame. The next target is upstream of the
line adapter: trace the V.22FC engine's source block around `DM 0x3fa7` and its
`PM 0x3cba` action while `DM 0x3fb0 = 0x000c`, and determine which control or
call-progress event transitions that engine from idle to answer-tone TX.

## Session 19: BRI experiment and a simpler direct driver

The BRI suggestion was tested against download `0x0006`, `DIVA Server BRI 2M
Kernel`, selected by card type 60/file set 9. This file set uses the exact same
`TIKRNL81.F34`, V.8, V.34 and V.90 overlays as the working PRI set, which made
the kernel look like a promising drop-in replacement.

It is not ABI-compatible at the resident-kernel boundary. Its service jump
table is shifted (for example service slots `0x0001..0x001e` target different
resident routines), its SPORT layout differs, and the current PRI task
registration/resume assumptions do not hold. In the forced probe it repeatedly
requests `0x0270` SIG and never advances to DIAL. Moving to BRI therefore means
recovering a second set of kernel vectors and interrupt plumbing; it does not
address the zero-valued modem source inside the shared F34 task.

The useful simplification is instead to remove MIPS/PRI **call control**, while
retaining the already-understood `0x0009` kernel as a small compatibility
substrate. `tools/dial_tikrnl_drive.py` now accepts:

```text
--role idle|answer|calling
```

`answer` and `calling` write the ADDSP §5.4.1 data-pump database directly:
`GEN_SETUP0`, role-specific `GEN_SETUP1` (`0x0484` answer, `0x048c` calling),
`GEN_SETUP2`, `INFO0_SETUP`, `WSTATUS`, `Norm_H` and `Norm_L`. This path has one
emulated ADSP, no Unicorn, no MIPS protocol image, no CAPI/IDI entities, no
synthetic call object and no PRI timeslot assignment. The Linux Eicon driver's
`message.c` remains the format oracle for those modem B1 parameters, while the
ADDSP guide defines their DSP database representation.

A direct answer-side smoke test now reaches V.8 immediately:

```bash
python3 tools/dial_tikrnl_drive.py --role answer --freq 0 --frames 512
```

```text
role=answer
page switches: SIG -> V.22FC -> V8.F34
bootpage_nr 0006:512
GEN_SETUP1=0484 WSTATUS=2000 Norm_H=0001 Norm_L=0100
```

The matching calling-side run remains on V.22FC (`bootpage 0x000c`), proving
that the role bit is live rather than an inert poke. This direct harness is the
preferred bring-up path. BRI kernel emulation can be deferred unless actual
BRI hardware timing becomes a goal.

The apparent remaining switch-on requirement was then disproved. The direct
harness was only running TIKRNL's `PM 0x06c1` page/RX half. On hardware the
kernel separately invokes the callback TIKRNL registered at `PM 0x06fc` once
per SPORT sample. That continuation calls the secondary page action through
`DM 0x3fb3`, consumes the signed-linear sample through `DM 0x3fb4`, and runs
the TX post-processing. Without it, the answer-side V.8 page was selected but
its transmitter never ran.

`Card.frame()` now invokes both halves. A 1.5-second direct answer run with
silence on RX produces real modem TX without any MIPS switch-on command:

```bash
python3 tools/dial_tikrnl_drive.py \
  --role answer --freq 0 --frames 12000 \
  --tx-out artifacts/eicon-dsp/direct-answer-tx.s16
```

```text
DM[3FB4] signed-linear TX:
  pointer=3764 nonzero=7733/12000 first-nonzero=4267
```

The transmitter starts at sample 4267 (533.4 ms). FFT of the first 4096 active
samples peaks at 2099.6 Hz, with signed amplitude approximately
`-1677..+1820`: this is the expected V.8 answer carrier generated by the
shipping firmware. The direct output file is raw 8 kHz signed 16-bit
little-endian PCM. This is now the uncomplicated modem-driving path originally
wanted: one ADSP core, direct documented database writes, real overlay
switching, both TIKRNL sample callbacks, and captured generated TX.

## Session 20: firmware G.711 encoder called

Download `0x02bf`, `G.711 Overlay`, is real and contains A-law/µ-law conversion
code at PM `0x0913..0x0972`. It belongs to the voice-kernel family, however,
and loads over PM `0x08f0..0x0975`; loading it beside V.8 would overwrite the
active modem overlay interface.

The important discovery is that TIKRNL already carries the same conversion
algorithm as a resident utility. Its signed-linear-to-G.711 entry is
`PM 0x1810..0x182f`. The modem overlays leave this range untouched. The
PRI/E1 kernel selects the A-law parameter table through `DM 0x3309 = 0x35b7`.
The routine takes the signed sample in AR and returns a bit-reversed serial
codeword in the low byte of SR1. Reversing that octet produces conventional
G.711/RTP byte order (`0xab` from the DSP becomes A-law silence `0xd5`).

The emulator now exposes diagnostic AR/SR accessors, and the direct harness
can call the shipping encoder after collecting the modem samples:

```bash
python3 tools/dial_tikrnl_drive.py \
  --role answer --freq 0 --frames 5000 \
  --tx-out artifacts/eicon-dsp/direct-answer-tx.s16 \
  --g711-out artifacts/eicon-dsp/direct-answer-tx.alaw
```

```text
called TIKRNL PM 1810; wrote 5000 A-law octets
first16=d5 d5 d5 d5 d5 d5 d5 d5 d5 d5 d5 d5 d5 d5 d5 d5
```

The 5000-octet result has 113 distinct codewords. Decoding it with the local
independent G.711 implementation differs from the source linear PCM by at most
39 counts (mean absolute error 8.46), confirming the firmware routine and bit
order. Thus the direct harness now emits the actual firmware-companded DS0
stream; no software approximation and no destructive `0x02bf` page load are
needed.

## Session 21: direct SIP/RTP endpoint

`tools/eicon_adsp_sip.py` turns the direct TIKRNL harness into a callable SIP
endpoint. It intentionally implements only UDP INVITE/ACK/BYE/OPTIONS and
PCMA/8000, avoiding all card signalling and host-driver call objects. Incoming
RTP A-law octets are written byte-exact to TIKRNL's `DM 0x3f08` line interface.
For every octet the harness executes one `PM 0x06c1` frame pass and one
`PM 0x06fc` continuation. The resulting signed-linear sample at the pointer
in `DM 0x3fb4` is passed through the shipping G.711 routine at `PM 0x1810` on
a second emulated ADSP core, then sent as RTP payload type 8.

The media scheduler advances in exact 160-sample/20-ms quanta. It has no
resampler, transcoder, PLC, VAD, comfort noise, echo cancellation or gain
processing. A missing inbound sample is A-law silence; late scheduler wakeups
execute every elapsed sample quantum rather than changing sample accounting.

```bash
python3 tools/eicon_adsp_sip.py \
  --bind 0.0.0.0 --advertise 192.0.2.10 \
  --sip-port 5060 --rtp-port 4000 --law pcma \
  --capture-prefix artifacts/eicon-dsp/sip-answer
```

A local loop test completed INVITE/200, received three 172-byte packets (12
bytes RTP plus 160 A-law octets), and completed BYE/200. The initial media was
firmware-generated A-law silence (`d5`). The optimized `Card.frame_fast()`
runs 5000 modem samples in approximately 0.11 seconds on the development
machine, leaving ample margin for an 8-kHz real-time call.

`--capture-prefix` records every outbound packet in a raw-IP PCAP, appends its
payload byte-exact to a raw A-law file, and independently decodes it to an
8-kHz mono WAV for listening. A 60-second local SIP call with continuous
inbound A-law silence produced:

```text
3001 RTP packets / 480160 samples / 60.02 seconds
RTP timestamp step: 160
active packets: 231, one run from packet 26 through 256
active interval: 0.520 through 5.140 seconds
active RMS: 981.4 counts, range -1696..+1824
FFT peak: 2098.6 Hz
```

The answer modem therefore waits about 520 ms, emits its approximately 2100 Hz
answer signal continuously for 4.62 seconds, then returns to A-law silence
when the caller supplies no modem signal. It remains stable and silent for the
rest of the minute; there are no extra tones, page-switch loops, RTP timestamp
discontinuities, or emulator stalls. The retained files are:

```text
artifacts/eicon-dsp/sip-answer-60s.rtp.pcap
artifacts/eicon-dsp/sip-answer-60s.alaw
artifacts/eicon-dsp/sip-answer-60s.wav
```

The WAV can be played directly with `afplay` on macOS or any ordinary audio
player. The PCAP uses `LINKTYPE_RAW` and contains synthesized IPv4/UDP headers
plus the exact RTP packets sent on the socket.

A subsequent call used this repository's `sip_v90_modem` as a genuine call
modem rather than a silence generator. `ATD1` placed a peer-to-peer SIP call;
the endpoints negotiated raw PCMU/8000 and exchanged 488 packets before the
caller's V.8 timeout. Capture now records both directions (`.ulaw`/`.wav` for
ADSP TX and `.rx.ulaw`/`.rx.wav` for peer TX) in one bidirectional PCAP.

The caller detected the ADSP's ANSam and transmitted real V.8 CM at the
expected 980/1170-Hz DPSK frequencies. On replay, the Eicon firmware remained
in bootpage 6 (V.8) until sample 41271 (5.159 s), then selected bootpage 1
(`0x0266`, V.22/V.32 LEC) and bootpage 3 (`0x025c`, FSK OWN). This proves the
receive RTP reaches and controls the genuine firmware state machine. The
remaining failure is now protocol-level: the Eicon side emits no JM response
after that page transition, so the project caller times out V.8 after about
9.7 seconds. Captures are retained as:

```text
artifacts/eicon-dsp/sip-project-caller-pcmu.rtp.pcap
artifacts/eicon-dsp/sip-project-caller-pcmu.wav
artifacts/eicon-dsp/sip-project-caller-pcmu.rx.wav
```

PCMU is now the endpoint default because TIKRNL's `DM 0x3f08` modem interface
is µ-law. `--law pcma` remains available for E1 experiments and selects the
firmware A-law encoder table; it must not be used as an implicit transcoder.

## Session 22: PDF setup correction and physical Courier call

The ADDSP V.90 User's Guide v5.3 §5.3.1 and §5.4.1 Tables 12-15 exposed a
major direct-harness setup error. `Info0_setup`, `Norm_H`, and `Norm_L` are at
write-database offsets `0x07`, `0x28`, and `0x29`; the harness had written them
to `0x03`, `0x0f`, and `0x10`. The latter two are P2SD and the low-level
dialler range, not modulation masks. The corrected initialization now writes:

```text
GEN_SETUP0    +00 = 00c4
GEN_SETUP1    +01 = 0484             answer, 2-wire, internal clock, norm
GEN_SETUP2    +02 = 0030
V8_SETUP      +04 = 6000             V90_DPCM + digital network
INFO0_SETUP   +07 = f0fd
TD / TA       +08/+09 = 0006/0006
TX tune       +0a = 00ff
DCD off/hyst  +0b/+0c = 0030/0000
WSTATUS       +0e = 2000             change_wdb
NORM_H        +28 = 0001             V.8
NORM_L        +29 = 8100             V.90 + V.34
SPEED masks   +2a/+2b = 001f/ff00
```

With those values, the repository's software call modem changed from V.8
failure to a successful V.8 result (`status=2`) and entered V.34 training.
That confirms the PDF-defined offsets were operationally significant.

The SIP endpoint now also implements SIP REGISTER with MD5 digest auth. It
registered extension 6001 directly with the test Asterisk and accepted a real
call from the physical USRobotics Courier on `/dev/cu.usbserial-21210`. The
45-second call exchanged 2232 outbound RTP packets / 357120 samples and saved
both directions:

```text
artifacts/eicon-dsp/sip-courier-pcmu.rtp.pcap
artifacts/eicon-dsp/sip-courier-pcmu.ulaw
artifacts/eicon-dsp/sip-courier-pcmu.wav
artifacts/eicon-dsp/sip-courier-pcmu.rx.ulaw
artifacts/eicon-dsp/sip-courier-pcmu.rx.wav
```

This first Courier call did not train. The Eicon changed from V.8 page 6 to
page 25 / `V.OWN` (`0x026d`) almost immediately and emitted no answer tone.
The Courier reported no carrier and `ATI6` showed a keypress abort. This is a
better reference peer than the software modem, but it reveals another startup
or capability-selection issue rather than validating the full path. The SIP
endpoint now logs live bootpage changes with sample timestamps so the next
Courier attempt can distinguish actual live timing from offline replay.

The reason the first Courier call was inaudible was then isolated in its RX
capture: the FXS path delivered a near-full-scale off-hook transient at about
100 ms (`-32124..+32124`). That made DIAL select `V.OWN` at 160 ms, well before
the normal transmitter begins ANSam at about 533 ms, leaving TX silent. The
SIP endpoint now discards the first 1000 ms of received bearer audio while
still consuming every RTP sample, modelling the bearer-seizure settling time
without shifting its clock.

A guarded Courier call was audibly active for 19.8 seconds. The audible
intervals correlate exactly with page changes: the first ANSam run is
`0.520..2.040 s`, and it cuts out when the firmware transitions from page 6
V.8 to page 16 (`0x0265`, FAX.F34 Partial). It does **not** request page 7 /
`0x0260` INFO during this call. It returns to V.8 at 6.300 s and emits several
more 2100-Hz bursts before selecting page 2 / `0x0267` V.32 Partial at
17.420 s and emitting a 3000-Hz signal. The cut-out is therefore genuine
firmware behavior, but it is a wrong low-level/fax path rather than the
expected V.34/V.90 INFO phase. The captured TX had RMS 1411, range
`-6908..+8316`, and 89956 non-zero decoded samples:

```text
artifacts/eicon-dsp/sip-courier-pcmu-guarded2.rtp.pcap
artifacts/eicon-dsp/sip-courier-pcmu-guarded2.wav
artifacts/eicon-dsp/sip-courier-pcmu-guarded2.rx.wav
```

A second PDF pass found the V.90-specific setup at write-database offsets
`0x79..0x7f`. Merely setting `V8_SETUP.V90_DPCM` and `NORM_L.V90` is not
enough: both V.90 speed masks default to zero. The harness now additionally
sets `SPEED_SEL_V90_H=0x003f`, `SPEED_SEL_V90_L=0xffff`, and explicit maximum
V.34/V.90 rates. `INFO0D_SETUP=0x03b7` advertises lookahead 3, 3429-baud
upstream support, µ-law PCM, codec-output power measurement, and -12 dBm0
maximum power. PCMA mode sets its PCM-coding bit 6 dynamically.

Two immediate physical retries registered successfully but received no SIP
INVITE from the FXS/Asterisk path, despite the Courier producing its local Y4
call-progress dump. Their unique crash-safe captures correctly contain only
headers, so they are not modem results. The settings remain in place for the
next call that reaches the endpoint.

The guide's §2.2 ordinary host flow sets `WSTATUS.BOOTFINISHED` (`0x1000`)
after a download and then lets the kernel redispatch the task. Testing that bit
in the direct harness changed post-V.8 `TrnProgress` to `0x0040/0x0044`, but
physical calls regressed into repeated low-level/FAX pages and never reached
INFO. The direct harness already resumes through TIKRNL's registered
post-download entry `DM(0x31bb)`; doing that *and* setting BOOTFINISHED signals
completion twice. The extra bit was therefore reverted. This distinction is
specific to the direct-resume harness, not a contradiction of the documented
normal kernel flow.

The DSP's own diagnostic outputs are now retained once per RTP packet in
`PREFIX.adsp.csv`: live and event-latched `TrnProgress`, `Rstatus_ch`,
`Rstatus`, change flags, bootpage/overlay, and all three eye-pattern words.
`PREFIX.adsp-dm.bin` originally snapshotted the 128 DSP-owned read-database
words every 20 ms. Format `EADSPDM2` now retains the complete 256-word
memory-mapped interface at `DM 0x3EE0..0x3FDF`: all 128 host-written setup
words followed by all 128 DSP-written status words. This preserves activation
strobes and selected setup alongside rate formats, `ErrorMessage`, detector
levels, reserved live state, and undocumented diagnostic values. Each record
is a little-endian `uint64` sample number followed by 256 `uint16` words.
The fields come from guide §5.3.2 and §6.6 (`EYESAMPLE_0` for V.8,
`EYESAMPLE_2` for INFO). This distinguishes a host timeout from a DSP state
stall on the next physical call.

The retained `sip-courier-live-20260728-181057.log` confirms the audible
"INFO then timeout" report. Three Courier calls cleanly selected page 7 /
`0x0260` INFO at 3.24-3.52 s. Their DSP-owned `TrnProgress` advanced through
`0x22/0x24`, `0x26`, and `0x28`, then stopped at `0x28` until the calls ended.
This is materially different from the later FAX/V.OWN attempts and proves the
Courier reached V.34/V.90 phase 2. A later A/B test showed that additionally
setting `WSTATUS.BOOTFINISHED` prevents this direct-resume path from reaching
INFO, so the INFO stall has a different cause.

The complete read-database snapshots exposed the larger missing flow: event
flags and their latched `RSTATUS_CH_dbs`, `RSTATUS_dbs`, and
`TRNPROGRESS_dbs` copies remain identically zero while the live E0-E2 words
change. Per §5.3.2 these mirrors are populated during the Host-Kernel RX_2400
communication cycle. The direct SIP harness calls TIKRNL entries as ordinary
subroutines and never runs that kernel/SPORT host cycle. It also differs from
the recovered MIPS flow, which sets BOOTFINISHED, restores assigned PCM buffer
pointers after non-V.8 overlays, and resumes the registered completion through
the kernel foreground slot. Therefore further setup-bit guessing cannot make
the direct-call path faithful; the SIP media loop needs to use the existing
`KernelDispatch` SPORT/doorbell path while retaining direct database call
activation.

A second concrete layering omission was found by comparing `Card.boot()` with
that kernel-driven harness. The `.F34` images are partial overlays: before
DIAL, the real flow layers V.OWN (`0x026d`) and FSK OWN (`0x025c`) underneath
it. DIAL calls shared routines beyond its own image, notably PM `0x244c` and
`0x2c4f`. The SIP direct harness loaded DIAL alone, leaving those targets as
cleared task memory or stale content and making classification vary among
INFO, FAX, and V.OWN across otherwise similar calls. `Card.boot()` now loads
V.OWN, FSK OWN, then DIAL in the recovered order. The direct-resume harness
must not also set the ordinary host/kernel BOOTFINISHED acknowledgement: doing
both completes a download twice. A fresh physical capture is required to
determine whether the remaining Host-Kernel RX_2400 cycle is still necessary
after fixing the base-image layer.

## Recovered: V.8's indirect page-7 handoff

The V.8 overlay does select INFO, but there is deliberately no immediate
`DM(0x3fb0) = 7` at the V.8 decision point.  The handoff has three stages:

1. PM `0x3ba1..0x3bfb` classifies the V.8 result.  On the normal-modem path,
   `DM(0x3fc4) & 0x0100` branches at `0x3ba7` to `0x3bc8`, which loads `AR=7`
   and stores it in the pending-page word `DM(0x0491)` at `0x3bfb`.  The
   alternate entry at `0x3c18..0x3c27` can also store 7 there.
2. A completion callback sets `DM(0x075b)`.  PM `0x372c..0x3763` then counts
   25 callback invocations in `DM(0x06b3)` before copying the pending page
   from `DM(0x0491)` to `bootpage_nr` (`DM(0x3fb0)`) at PM `0x3761` and
   setting the page-change strobe in `DM(0x3fc1)`.
3. TIKRNL sees that strobe, indexes its bootpage table at `DM(0x31d5)`, and
   requests download `0x0260`; the host only serves and acknowledges that
   request.  INFO is therefore not a direct ADSP `CALL` from V.8.

Replay of the first successful Courier capture confirms the exact path.  At
sample 27332, `DM(0x3fc4)=0xa100` selects page 7 into `DM(0x0491)`.  At sample
27487 the completion flag becomes non-zero.  At sample 27572 the counter moves
from 24 to 25, PM `0x3761` copies 7 into `DM(0x3fb0)`, and TIKRNL requests the
INFO overlay.  In a failed low-level/FAX call, `DM(0x0491)` never becomes 7;
V.8 classifies the input first and changes directly to page 16.  Thus a
missing page-7 transition is upstream of the overlay loader: either V.8 did
not set pending page 7 (inspect `0x0491`/`0x3fc4`) or its delayed completion
callback did not run (inspect `0x075b`/`0x06b3`).

## Replay result: page 7 loads; INFO receive acquisition stalls

Replaying the first successful Courier RX capture through the current layered
`Card` path proves the complete request and load path.  At sample 27573
(3.447 s) TIKRNL requested page 7, the harness served `0x0260`, and the
resident image became INFO.  `TrnProgress` then advanced
`0x20 -> 0x22 -> 0x24 -> 0x26 -> 0x28`; it remained at `0x28` through sample
80000.  The SIP endpoint now logs the served request explicitly rather than
only reporting the bootpage at the next 160-sample packet boundary.

INFO transmission is active after the switch.  The replayed DSP output is
non-zero continuously after `TrnProgress=0x28`, and the offline Phase 1/2
decoder recovers a V.90 `INFO0d` from it.  The failure is on INFO receive
acquisition: at the stall, the control-channel parser remains at its initial
PM state (`DM(0x16bd)=0x3520`) and its completion flag `DM(0x0686)` remains
zero.  PM `0x3574..0x358d` sets that flag only after the receive parser accepts
its bit sequence, while the state-`0x28` condition at PM `0x33a3..0x33cb`
continues testing it.  The peer capture contains control-channel energy and a
Tone-A candidate, but the independent decoder also fails to recover a valid
INFO0 frame from that interval.  Therefore the next investigation is the
INFO page's control-channel RX input/carrier/framing path, not page mapping,
overlay loading, or INFO transmission.

A subsequent live call exposed a separate direct-harness drive bug on page 16.
The same still-asserted request was treated as a new download up to eight times
per sample, repeatedly resetting the resident partial overlay and flooding the
synchronous log enough to make media processing appear stalled.  `Card.frame`
and `Card.frame_fast` now resume a request for the already-resident download
without reloading it or recording a duplicate page switch.  Replays retain the
single page-7/`0x0260` transition and no longer produce thousands of destructive
page-16 reloads.

The next physical calls confirmed a distinct policy gap: after exchanging V.8
signals the peer went quiet waiting for Phase 2, while the firmware selected
page 16 or V.32 rather than setting pending page 7.  An opt-in
`--force-info-after-v8` diagnostic now replaces the first post-V.8 low-level
fallback (after 1.5 s) with page 7/`0x0260`; natural page-7 requests are
untouched.  Replay of the first affected call changes the path from page 16 to
INFO and advances `TrnProgress` through `0x20/0x22/0x24/0x26/0x28` to `0x2a`.
This validates the host-policy hypothesis strongly enough for a live A/B call,
but the override remains diagnostic rather than a claim about the shipping
supervisor's exact acceptance gate.

The first forced live A/B calls show that the INFO microstate cadence itself is
not anomalously fast: `0x20 -> 0x2a` takes about 140-160 ms, comparable to the
natural page-7 captures.  The uncertain timing is the V.8-to-INFO seam.  In the
second call page 7 loaded at 2.055 s, while the peer-side recording contains a
CRC-valid INFO0c candidate beginning at 1.889 s, so waiting for the DSP's
fallback request can actually enter INFO too late and miss the peer's first
control-channel frame.

The open-source `divas4linux` driver does not schedule page 7.  Its
`kernel/message.c:add_b1()` builds a modem CAI and hands the call to the closed
MIPS protocol firmware.  That CAI includes call direction, digital-modem use,
modulation masks, exact answer-tone duration, answer-tone delay, carrier-wait
time and carrier-loss time.  The ADDSP guide §5.4.2 confirms that the MIPS
supervisor starts 40 s abort and 15 s training timers when the DSP requests
V.8, monitors published `TrnProgress`, and initiates retrain/disconnect policy;
it does not advance INFO microstates directly.  Our direct path bypasses both
the CAI-to-database call setup and the Host-Kernel RX_2400 publication cycle,
so the next faithful fix is to recover those MIPS-derived timing/setup writes,
not add sleeps between INFO states.

The natural V.8 completion gate is now pinned down.  At the protocol level it
is answer-side CJ reception after the modem has sent JM.  In the successful
firmware replay, that event sets `TrnProgress=0x0009` and dispatches PM
`0x3ba1`; with `DM(0x3eaa)&0x0060 == 0` and `DM(0x3fc4)&0x0100 != 0`, PM
`0x3bc8/0x3bfb` stores pending page 7 in `DM(0x0491)`.  A later transmitter
completion calls PM `0x3b95`, setting `DM(0x075b)=1`; PM `0x372c..0x3761`
counts 25 callbacks in `DM(0x06b3)` before publishing page 7.  The forced
calls never produce this gate naturally: they remain at `TrnProgress=0x0004`
and `DM(0x0491)=0` until the diagnostic override.  New captures retain all
five gate words and label forced page-7 requests explicitly.

## Blocker isolated: direct INFO RX is disconnected

A sample-by-sample replay of the first successful Courier call separates the
remaining failure from INFO framing.  In the direct `Card.frame_fast()` path,
page 7 loads at sample 27572 and publishes `DM(0x3f0f)=0x3763`, but
`DM(0x3763)` becomes zero and stays zero while the incoming `DM(0x3f08)`
codeword continues changing.  Consequently the INFO parser never leaves
`DM(0x16bd)=0x3520`, `DM(0x0686)` stays zero, and `TrnProgress` stops at
`0x0028`.  V.8 had its own active G.711 line adapter at the same pointer;
INFO depends on the assigned kernel/SPORT path that the direct subroutine
harness bypasses.

Replaying the identical RX bytes through `LiveKernelModem` proves the point.
The kernel path advances through `0x20/0x22/0x24/0x26/0x28` and then reaches
`0x2a/0x2b` at 3.642 s, corresponding to acquisition of the peer's Tone A.
It cannot progress further on a fixed replay because the recorded Courier
cannot react to the newly generated response.  Both paths transmit a
decodable INFO0d at 3.590 s, but the direct path incorrectly advertises A-law
in INFO0d bit 39 on a PCMU call; the kernel path advertises µ-law.  Thus the
next meaningful hardware test is `tools/eicon_adsp_sip.py --kernel-dispatch`.
A fresh call, rather than another replay, is required to determine whether the
Courier acknowledges that corrected INFO0d and advances beyond `0x2b`.

## INFO `0x37` terminal FFT corruption

Execution watchpoints on the INFO analysis sequencer (`PM 0x36ed`) show the
normal transform actions entering `PM 0x376e`, `0x3771`, and `0x3774` with
`DM(0x16c5..0x16c7)` progressing from `0x0080/0x0002/0x0004` to the terminal
`0x0001/0x0100/0x0200`.  At the `0x37` failure boundary, repeated analysis-result publication has
already advanced the linear pointer `DM(0x15f3)` beyond its 20-word buffer at
`0x0ddd..0x0df0`.  PM `0x323e..0x3244` appends two words per analysis and,
because detector completion never occurs, eventually writes through
`DM(0x0e4c)`, which holds the second `PM 0x373a` reset action in the active
analysis sequence.  It replaces that action with `0xffed`.  The sequencer
therefore reaches its second `PM 0x376e` transform without resetting the
terminal `0x0001/0x0100/0x0200` parameters.  The next stage shifts the span
to zero and doubles count/stride to `0x0200/0x0400`.  The indirect butterfly
stores at PM `0x3792/0x3794` then escape the `0x1110` work buffer and overwrite
the INFO control workspace.  The first consequential overwrite is
`DM(0x16b6)=0xffec`; PM
`0x217d..0x217f` subsequently copies that invalid variant selector to the
shared boot-page word.  The later `DI_control=0xfd00`, `BaudInfo=0x3000`, and
status values are downstream corruption, not host requests.

An independent emulator defect was also found in opcode class `0x10` (shift
with internal register move): the core executed the shift before sampling the
parallel move source.  INFO PM `0x25fc` shifts a new value into SR while moving
the preceding `SR1` accumulator to AR, so the old ordering forced AR to read
the newly cleared SR1.  The core now samples the move source first, with a
regression test using the firmware's exact `0x1013af` opcode.  The captured
candidate still fails earlier validation at PM `0x25e9..0x25f1`, so this fix
is necessary instruction semantics but is not by itself the `0x37` cure.

This is evidence of a missing or incorrectly timed emulator path rather than
a valid firmware transition.  The stale classifier/event value
`DM(0x198e)=0x06a6` is present at every watched transform entry.  Do not hide
the problem with an FFT bounds check: trace why the sequencer skips its reset
or why the detector completion fails to stop/reconfigure that sequence.

## The control-channel framer is not the `0x37` fault

The detector completion that never occurs is `DM(0x0686)`, published by the
INFO page's control-channel framer.  That framer has now been recovered and
exercised in isolation, and it works: the fault is upstream of it.

Two stages sit between the line and `DM(0x0686)`:

- **PM `0x34f0`, the demodulator.**  A 16-word circular sample history at
  `DM(0x16bb)` (`L0 = 0x10`) is correlated against the 16-tap reference at
  `DM(0x1554)`.  PM `0x350b` takes `|MR1|`; over `DM(0x164f)` it raises the
  energy flag `DM(0x0685)`, and over the immediate `0x0578` it becomes the
  one-bit decision published in `DM(0x060f)` at PM `0x3515`.  The magnitude
  itself lives only in `AR`/`AX1` and is never stored to DM.
- **PM `0x3520` and PM `0x25ab`, two framers.**  PM `0x3515` runs framer A
  through `DM(0x16bd)` and then falls into framer B (the `JUMP $25A0` at PM
  `0x351f`), once per demodulated sample.

Both framers keep 16 lanes in a circular buffer, one per sample phase of a
16x oversampled bit, advancing one lane per call:

|  | framer A | framer B |
|---|---|---|
| state | `DM(0x16bd)`, hunt `0x3520` | `DM(0x19cf)`, hunt `0x25ab` |
| lanes | `DM(0x0620..0x062f)` | `DM(0x1990..0x199f)` |
| bit planes | `DM(0x068c..)` | `DM(0x19d0..)` |
| call count | `DM(0x068a)` | `DM(0x19cd)` |
| payload | `DM(0x1651)`: `0x0110`/`0x01e0` (17/30 bits), by `DM(0x3f94)` bit 1 | fixed `0x0080` (8 bits) |
| success | `DM(0x0686) = 1` | `DM(0x198e)` event, `DM(0x198f)` octet |

A lane hunts an 11-bit window equal to `0x0772` — one fill bit followed by
the V.34 INFO synchronization code `0x372`, the same constant as
`V34_INFO_SYNC_CODE` in `v34_info_decode.h` — five times, then accumulates
CRC-16 (reflected `0x8408`, preset `0xffff`) over the payload while the bit
planes collect every lane's decision.  The received CRC is shifted in against
each lane's own register, so the lane whose residue is zero is the one that
sampled on the correct phase.  PM `0x3568`/`0x25e9` is that zero scan — the
validation the previous session saw fail — and PM `0x3574`/`0x25f5`
transposes the bit planes to recover the winning lane's payload.

`tools/info_cc_framer_probe.py` drives PM `0x3515` directly with ideal
decisions (each bit repeated across all 16 lanes) after running the
firmware's own initializers, PM `0x359a` and PM `0x3f7f`.  Framer A locks
sync, accepts its 17-bit payload, validates the CRC and sets
`DM(0x0686) = 1`.  So the framer, the 16-lane phase search and the emulated
instruction semantics along that path — including the opcode-class `0x10`
fix above — are all correct.  Framer B behaves identically when fed its own
8-bit message (it recovers `DM(0x198f) = 0x30` and publishes
`DM(0x198e) = 1`, the event the `_inject_l1l2_completion` gate fakes), but
note that the INFO page initializer at PM `0x3f4c` deliberately parks framer
B at the disabled handler `0x25f3`; PM `0x2602` is what installs it, and
nothing in the resident image references `0x2602` directly — it is reached
only through the PM action table at `0x2ee6..0x2eee`.

The remaining candidate is therefore the decision itself.  `DM(0x060f)` is a
hard threshold on a correlation magnitude against fixed constants
(`DM(0x164f)`, `0x0578`) that assume the real card's signal levels; nothing
downstream can recover if that bit is stuck or noisy.  The `[EXEC]`
watchpoint line now carries `ax1`/`ar`/`mr1` for this reason, and
`tools/eicon_adsp_sip.py --watch-exec 0x3515` logs the magnitude per sample
on a live call.  The next measurement is that magnitude against `0x0578`
over the INFO window: a magnitude that never crosses, or never stops
crossing, is a level/scaling fault in the emulated RX path, not a framing
one.  Note this repository's standing μ-law level gotcha (0 dBm0 is RMS
16017, not 4004) when interpreting it.

## Live against slmodemd: no `0x37` stall, no FFT corruption

Two calls from the tower rig's SmartLink softmodem (`slmodemd_trnref` behind
d-modem, dialling `ATD6001` into `tools/eicon_adsp_sip.py --kernel-dispatch`
registered as 6001) settle the framer question live and move the frontier.
The second call ran without `--init-info-detector-at-24`; both are
byte-identical in outcome, so that diagnostic is not load-bearing against
this peer.  Traces: `artifacts/eicon-live/run01.adsp.csv`, `run02.adsp.csv`.

What works, none of it forced:

- V.8 completes naturally (`TrnProgress 0x0004 -> 0x0003 -> 0x0009`) and the
  DSP requests page 7 on its own at ~5.57 s.  `--force-info-after-v8` is not
  needed against this peer.
- The peer decodes our INFO0a: `V34INFO, rxinfo0 0xbf,0x84,0x07,0x68,0x32`,
  logged by slmodemd as `rxinfo0a`.
- **The control-channel framer runs and validates.**  `DM(0x0686)` is set in
  749/754 of the 1028 captured 20 ms windows, and `DM(0x16bd)` cycles
  `0x3520 -> 0x3546 -> 0x3561` throughout.  The "detector completion never
  occurs" symptom is Courier-specific; the framer analysis above holds live.
- **No FFT corruption.**  `DM(0x15f3)` advances only `0x0ddd -> 0x0de9`,
  inside its 20-word buffer; `DM(0x0e4c)` holds `0x373a`/`0x376e`, the real
  reset and transform actions, never `0xffed`; `DM(0x16b6)` never leaves
  `0x0000`.  Zero anomalies in either run.
- INFO advances `0x20 -> 0x24 -> 0x26 -> 0x28 -> 0x2e -> 0x30 -> 0x32 ->
  0x34 -> 0x36 -> 0x37` in ~1.3 s.  It does not stall at `0x37`.

Where it actually fails: after ~120 ms in `0x37` the sequencer leaves for
state `0x0010` and stays there for the rest of the call.  The exit is a
normal, understood transition, not a fault in itself.  PM `0x3335` is the
INFO sequencer: it counts `DM(0x1647)` down, then calls the pre-condition
`DM(0x169a)` and up to four condition handlers `DM(0x1696..0x1699)`, taking
the first that returns LE and loading the matching next state from
`DM(0x1692..0x1695)`.  In state `0x37` those handlers are `0x33c4`,
`0x33c2`, `0x33c2` and `0x2476`; `0x33c2` is `AR = 0 + 1`, a stub that never
fires, `0x2476` tests framer B's event `DM(0x198e)` against 1, and `0x33c4`
falls through `0x33a3` to `AR = DM(0x0686) XOR 1` — it fires when framer A
completes.  The capture shows `DM(0x0686)` going to 1 in the window before
the transition, so `0x37` ends because a genuine CRC-valid control frame
arrived, exactly as designed.  `DM(0x1647)` still had `0x0a2f` left, so this
is not a timeout.

The real defect is what `0x0010` does.  `DM(0x3fb4)`'s sample is zero for the
whole `0x34..0x37` window — correct, the digital modem is listening for the
analogue modem's L1/L2 — and resumes at the `0x0010` transition.  The peer
decodes that resumed transmission as a **second INFO0a**
(`0xbf,0x84,0x87,0x68,0x29`, differing from the first in bit `0x80` of octet
2), returns to `TX_PHASE1_ANS`, and gets nothing further; it reports
`vpcm: Link Error` 13 s later.  So instead of proceeding from the INFO0
exchange to line probing and INFO1, the page drops back and repeats INFO0a.

Note that framer B never publishes: `DM(0x198e)` and `DM(0x198f)` stay zero
for the entire call while its state cycles `0x25ab -> 0x25c7 -> 0x25e2`.
That is consistent rather than alarming — framer B's payload is fixed at 8
bits where framer A takes the 17 that `DM(0x1651)` selects, so the two are
alternative message formats and this peer only sends the longer one.

Next: identify what state `0x0010` is meant to be and why `0x37`'s framer-A
exit targets it.  `DM(0x1695)`/`DM(0x1692)` hold the candidate next states at
that point, and PM `0x331e`/`0x334d` are where the winning one is committed;
capture those four words across the transition rather than inferring the
mapping from `TrnProgress` alone.

## The `0x37` exit: candidates captured, and the missing event

`tools/eicon_info_replay.py` replays a `.rx.ulaw` capture through a fresh
`LiveKernelModem`.  Our transmission cannot affect an already-recorded RX, so
the replay reproduces `run02`'s state path sample for sample and any DM word
can be instrumented without dialling the rig.

### The candidate table across the transition

`DM(0x1692..0x1695)` and `DM(0x1696..0x1699)` through the window:

| time | state | next0/test0 | next1/test1 | next2/test2 | next3/test3 |
|---|---|---|---|---|---|
| 6.1559 | `0x34` | `0a9d`/`33c4` | `0836`/`3384` | `0b69`/`33c2` | `0b69`/`33c2` |
| 6.5571 | `0x36` | `0a9d`/`33c4` | `0836`/`33c2` | `0b69`/`33c2` | `098f`/`3384` |
| 6.5671 | `0x36` | `0a9d`/`33c4` | `0836`/`33c2` | `0b69`/`33c2` | `1736`/`2476` |
| 6.6508 | `0x37` | `0a9d`/`33c4` | `0914`/`33c2` | `08d5`/`33c2` | `1736`/`2476` |
| 6.7371 | `0x10` | `0a9d`/`33c2` | `0a9d`/`339b` | `08d5`/`33c2` | `1736`/`2476` |

`0x33c2` is `AR = 0 + 1`, a stub that never fires, so state `0x37` has exactly
two live exits: `0x33c4` (framer A completed, `DM(0x0686) == 1`) to `0x0a9d`,
and `0x2476` (`DM(0x198e) == 1`) to `0x1736`.  Note `0x36` arms a timer exit
first and re-arms 10 ms later to the `DM(0x198e)` test — the sequencer is
deliberately set up to wait for that event across `0x36`/`0x37`.

`0x0a9d` is not a mis-set candidate: it is the intended framer-A successor,
and it is the state-`0x0010` script.  So the transition is the firmware taking
its documented fallback because the branch it is actually waiting for never
becomes true.  `DM(0x1647)` still held `0x0a15`, so nothing timed out.

### What is missing while we are silent

Transmit activity per state, from the same replay:

```
  6.1559s  0x0034    3210 samples    2.0% non-zero TX
  6.5571s  0x0036     534 samples    0.0% non-zero TX
  6.6239s  0x0037     906 samples    0.0% non-zero TX
  6.7371s  0x0010  109783 samples   99.4% non-zero TX
```

The 580 ms of silence is correct — the digital modem is listening.  Nothing is
missing from our transmit path.  What is missing is the input event.

The peer is presenting exactly what should raise it.  Complex demodulation of
the captured RX at 2400 Hz shows a steady Tone A from ~5.5 s at magnitude
~1738, with 180-degree phase reversals starting at 6.59 s: a burst at
6.59-6.74 s and another at 6.97-7.36 s.  The first burst coincides with our
`0x36` (6.557 s) and `0x37` (6.624 s) window almost exactly.

Injecting the event confirms the diagnosis.  With `DM(0x198e) = 1` written on
first reaching `0x37`, the sequencer takes `0x2476` instead:

```
  6.6239s  0x0037       5 samples    0.0% non-zero TX
  6.6245s  0x00a0      10 samples    0.0% non-zero TX
  6.6258s  0x00a2    6790 samples   99.3% non-zero TX
  7.4745s  0x00ab     111 samples   28.8% non-zero TX
```

State `0x00a2` transmits for 848 ms — the phase-1 response we currently never
send.  The `0xa0`/`0xa2`/`0xab` family had never been reached before.  Past
that point the replay is open loop and says nothing about what the call would
have done.

### Why the event is never published

`DM(0x198e)` has five writers in the INFO image and four of them clear it.
The only one that sets a value is PM `0x2470..0x2474`, the match arm of the
classifier PM `0x2461`: it stores `I6 - 0x1986`, the index of the matched
message code in the 8-entry table PM `0x2410` builds at `DM(0x1986)`
(`0x30`, `0x50 | DM(0x3f4b) & 0x0f`, `0x70`, `0x90`, `0xb0`, `0xd0`, `0x40`,
`0x60`).  Event 1 is therefore "the `0x50` message was received".

That classifier is reachable from framer B's success path (PM `0x2600`) and
from framer A's (PM `0x3587`) — but PM `0x357e` compares `DM(0x1651)` against
the length the INFO mode word selects, which is the value PM `0x3f7f` just
wrote there, so that test always takes the equal branch and PM `0x3583` only
lets the classify path run when `DM(0x1651) == 0x0080`.  With `DM(0x1651) =
0x0110` on this call, framer A can never publish an event.  Framer B has the
fixed `0x0080` length and is the intended publisher — and the INFO page
initializer at PM `0x3f4c` parks it at the disabled handler `0x25f3`, with
only PM `0x2602` installing it, which nothing in the resident image calls.

Installing it is necessary but not sufficient: `run01` ran with
`--init-info-detector-at-24`, which does call PM `0x2602`, and framer B cycled
`0x25ab -> 0x25c7 -> 0x25e2` all call without ever validating.  This peer is
transmitting a phase-reversed tone in that window, not an 8-bit control-channel
message, so there is nothing for framer B to decode.

Open question, and the next thing to settle: what is supposed to raise event 1
against a tone-only peer.  Either the probing/tone classifier reaches PM
`0x2470` by a path not yet found, or `DM(0x3f4b)` — tested for bits `0x10` and
`0x80` by the neighbouring condition handlers PM `0x2495`, `0x249a`, `0x24a9`
and folded into the table entry itself — is the tone-detector's output and the
gap is upstream of the classifier.  Resolve that before adding any injection
to the live path.

## What raises event 1: not the MIPS, and not a tone

The previous section left open what publishes `DM(0x198e) = 1` for a peer that
looks tone-only in the `0x36`/`0x37` window.  Three findings close it.

### The MIPS supervisor cannot be the source

`DM(0x198e)` has exactly one writer that stores a non-zero value: PM
`0x2470..0x2474`, the match arm of the classifier PM `0x2461`.  PM `0x2461` is
entered from PM `0x2600` (framer B's success path) and PM `0x3587` (framer
A's), and from nowhere else — no PM data word anywhere in the loaded image
holds `0x2461 << 8` or `0x2470 << 8`, so no script table can dispatch either.
The event is therefore raised inside the DSP, by a framer, or not at all.

That matches the host's documented role.  `DM(0x198e)` is overlay-private
scratch, outside the host-visible database window `0x3ee0..0x3fdf` that
`.adsp-dm.bin` snapshots, and the ADDSP guide's line follow-up only monitors
`TrnProgress`, runs the training/response timers and decides retrain policy.
The guide even anticipates this exact transition: "It is also possible that,
because of some recovery mechanisms in the training, the TrnProgress is
smaller than LastStatus", handled by counting `RetrainAutofallbackcount` and
forcing a fresh retrain past 10.  So `0x37 -> 0x0010` is a recovery the host
design expects to see occasionally — the defect is that we take it every time,
not that we take it.

### The peer is not tone-only there

The 2400 Hz carrier's phase reversals are its DPSK transmission, not tone
timing: slmodemd's own log shows `txstate TONE_AB=>TX_DPSK` at 667.464 and
back at 668.263, which maps to our 6.34-7.14 s — the reversal bursts measured
at 6.59-6.74 s and 6.97-7.36 s fall inside it.  Reading those bursts as Tone A
reversals in the previous section was wrong.

And the receiver decodes it.  Reconstructing framer A's bit planes
(`DM(0x068c..)`) per lane at each `DM(0x0686)` 0->1 edge gives a real message,
twice, with the same payload:

```
  5.568s trn=0028   lanes 1-13:  1 1111 1111 0000 1000
  6.710s trn=0037   lanes 2-13:  1 1111 1111 0000 1000
```

Twelve of the sixteen sample-phase lanes agree exactly, and the firmware's own
zero-scan accepted one, so the received CRC matched the computed `0x9bf1`.
The demodulator, the 16-lane phase search and the framer all work on real
signal — this is a genuine 17-bit control-channel message, not a false lock.

### Why the event branch is structurally dead

PM `0x3583` only lets framer A reach the classifier when `DM(0x1651) ==
0x0080`.  `DM(0x1651)` has a single writer, PM `0x3f84`, fed by PM `0x3f7f`,
which stores `0x0110` or `0x01e0` depending on `DM(0x3f94)` bit 1.  **It can
never hold `0x0080`**, so framer A can never publish an event in this build.

Framer B is hard-wired to the `0x0080` length (PM `0x25c5`) and is therefore
the only possible publisher — and the INFO page initializer PM `0x3f4c` parks
it at the disabled handler `0x25f3`, with only PM `0x2602` installing it and
nothing in the resident image calling that.  `run01` did call it via
`--init-info-detector-at-24`; framer B then cycled `0x25ab -> 0x25c7 ->
0x25e2` all call without validating, because the peer is sending 17-bit
messages in that window, not 8-bit ones.

So the `0x37` state offers two exits and only one of them is live for this
traffic: "17-bit message received" to `0x0a9d`/state `0x0010`, and "8-bit
message 1 received" to `0x1736`, which needs a message class the peer never
sends here.  The question is no longer what raises event 1 — it is why the
page is configured for 17-bit messages at a point in the handshake where its
own state graph expects the 8-bit class, i.e. what should have set
`DM(0x3f94)`/`DM(0x1651)` differently, or which earlier state should have run
the PM `0x2ee6..0x2eee` action block (`0x2410` table build, `0x2602` framer B
install, and the `DM(0x3f4b)` flag actions) that nothing in our run ever
enters.  `DM(0x3f4b)` stays `0x0000` for the whole call, which is consistent
with that block never running.

## The action block gates the transmitter, and running it unblocks the peer

### DM(0x3f94) and DM(0x1651) are already correct

`DM(0x3f94)` is set by the V.8 overlay, not the INFO page: PM `0x38a1`/`0x38a2`
store `0x0009` when the V.8 result has bit `0x0008`, PM `0x38a6`/`0x38a7`
store `0x0006` for bit `0x0004`, with `0x0008`/`0x0000` at PM `0x382e`/`0x385e`.
The INFO overlay's only writer, PM `0x3db5`, merely clears bit 1 when
`DM(0x3f93) & 0x0010` is zero.  Our calls get `0x0009`, so PM `0x330c` selects
INFO variant 8 (`DM(0x16b6) = 8`) and PM `0x3f7f` derives `DM(0x1651) =
0x0110`.  That is the V.90 path and the 17-bit message class that goes with
it; the `0x01e0` alternative belongs to mode `0x0006`.  Nothing is
misconfigured here, and the previous section's framing of this as a
misconfiguration was wrong — the 8-bit class simply is not this variant's.

### What the action block actually does

PM `0x2ee6..0x2eee` is a dispatch table indexed by action code, executed by the
script interpreter PM `0x2148` through the pointer `DM(0x1667)`:

| code | entry | effect |
|---|---|---|
| 0 | `0x2410` | build the message table at `DM(0x1986)`; `DM(0x16af) = 1` |
| 1 | `0x2602` | install framer B, call `0x2410`, then `DM(0x16af) = 0` |
| 2 | `0x242b` | clear `DM(0x3f4b)` bit `0x80` |
| 3 | `0x2430` | disable framer B; transmit message 0 |
| 4-8 | `0x243d`, `0x2441`, `0x243f`, `0x2434`, `0x243b` | transmit message 3, 5, 4, 1, 2 |

The transmit arms all reach PM `0x2446`, which builds an outgoing frame at
`DM(0x16a5)` — `0x0010`, then `0x0f72` (fill bits plus the sync `0x372`), the
message octet, and a CRC from PM `0x3aa4` (CRC-16-CCITT, `0x1021`, MSB first;
the framers use the reflected `0x8408` form of the same polynomial).  So this
block is the 8-bit control-channel transmitter, and action 7 sends message 1 —
the very code event 1 waits to receive.

`DM(0x16af)` is not a mute.  PM `0x3b0e..0x3b13` decrements it every sample and
reloads it with 4 on reaching zero, clocking the next bit out of the message
buffer: it is the transmit bit-clock divider.  Setting it to 0 — which is
exactly PM `0x2602`'s last instruction, PM `0x2609` — makes the countdown run
away for 65535 samples, so the modulator's carrier keeps running unmodulated
instead of going idle.

### Replay: the carrier is 1200 Hz

Dispatching action 1 at state `0x34` in replay turns the `0x34..0x37` window
from silent into a continuous, clean **1200 Hz** tone at rms 2048.  Writing
`DM(0x16af) = 0` directly does the same, which isolates the divider as the
cause.  1200 Hz is the V.34 answer-modem Tone B, and the peer is transmitting
2400 Hz Tone A across the same window.  Whether the shipping firmware really
produces Tone B by stalling this divider is not proven by that alone — the
peer is the arbiter.

### Live: the peer runs line probing for the first time

`tools/eicon_adsp_sip.py --kernel-dispatch --info-action 0x34:1`
(`artifacts/eicon-live/run03.adsp.csv`).  Our transmit envelope loses its gap:
baseline `run02` is 3%/0%/0%/0%/0%/19% active over 6.3-6.8 s, `run03` is 100%
throughout.  State `0x37` then holds for 1.44 s instead of 113 ms and does not
fall back to `0x0010`.

slmodemd's own state machine goes far past anything previously seen:

```
  357.865  microstate TX_PHASE2_ANS=>TX_L1      <- line probing L1
  358.025  microstate TX_L1=>TX_L2              <- line probing L2
  358.245  microstate TX_L2=>TX_PHASE3_ANS
  358.325  microstate TX_PHASE3_ANS=>RX_PHASE2_ANS   (goes silent, waits for us)
  359.785  rxstate RX_DPSK=>RX_L1               <- detects an L1 from us
  360.225  rxstate RX_L1=>RX_DPSK
  361.425  txstate TONE_AB=>SILENCERETRAIN
  364.585  vpcm: Link Error
```

Every earlier call ended in the `DET_INFO -> TX_PHASE1_ANS` loop with the peer
repeating INFO0 until it gave up.  This is the first time it has transmitted
L1 and L2 or detected anything from us in the probing phase, which confirms
the causal claim: the `0x34..0x37` silence was what blocked V.34 Phase 2, and
putting energy there unblocks the peer.

It still fails ~3 s later.  The peer detects our "L1" at 359.785, which maps
to our 8.03 s — the moment our own `TrnProgress` resets to `0x0000` — so what
it detected was most likely the stalled carrier or the reset transient rather
than a real L1, and it retrains when no INFO1 follows.  Two things to settle
next, in order: what the firmware's genuine Tone B and L1/L2 transmit path is
(action 1 is a diagnostic that stalls a clock, not that path), and why our
side resets to `0x0000` at 8.02 s instead of proceeding from `0x37`.

## The real Tone B path, and the reset is the FFT overrun

### Tone B is state `0x00a2`, reached only through event 1

The transmit source is a per-state field, `DM(0x166b)`, dispatched at PM
`0x3b0c` (`I4 = DM(0x166b); CALL (I4)`).  It has no writer in the overlay: the
state-script executor PM `0x336a` loads it as part of the 25-word state record,
alongside `DM(0x1667)`, `DM(0x1668)` and the rest of `DM(0x1665..0x167x)`.

Replaying `run02` with the event injected at `0x37` shows the sources and what
each transmits:

| state | `DM(0x166b)` | `DM(0x16a6)` | TX |
|---|---|---|---|
| `0x34`, `0x36`, `0x37` | `0x3b29` | `0xbbc0` | silent |
| `0x00a0` | `0x3b30` | `0x4440` | — |
| `0x00a2` | `0x3b30` | `0x4440` | **1200 Hz, rms 1998, 848 ms** |
| `0x00e0` | `0x3b29` | `0x4440` | 2100 Hz |

PM `0x3b29` is `SI = 0` — the receive states feed the modulator zeros, so their
silence is deliberate and correct, not a fault.  PM `0x3b30` is the message
buffer readout (`DM(0x16a5)` against the end marker `DM(0x16b3)`).  State
`0x00a2` selects it and puts out a clean 1200 Hz carrier for 848 ms: that is
the genuine V.34 answer-modem Tone B, produced by the state's own script with
no clock manipulation, and it is entered from `0x37` only through condition PM
`0x2476` (event 1) to candidate `DM(0x1695) = 0x1736`.

So `--info-action 0x34:1` produced 1200 Hz by a completely different mechanism
— stalling the bit-clock divider so the carrier leaks — and is not the real
path.  The real path needs event 1.

### The 8.02 s reset is the Courier's FFT overrun

Watchpoints put the corrupting stores at PM `0x3793` and `0x3795`: the
indirect butterfly this document identified in the first `0x37` investigation.
The analysis-result pointer `DM(0x15f3)` advances two words per analysis every
~26.7 ms from `0x0ddd`, and its buffer ends at `0x0df0`:

```
  run02 (baseline)     0x0ddd -> 0x0de9, still inside the buffer
  run03 (action 1)     0x0ddd -> 0x0df0 at ~6.62 s, then straight through:
                       0x0df1 ... 0x0e4b ... 0x0e4d at 7.8489 s
```

`DM(0x0e4c)` holds the sequence's second `PM 0x373a` reset action; it flips
from `373a` to `0000` at exactly 7.8489 s.  2.6 ms later the transform runs
without that reset, the butterfly escapes its `0x1110` work buffer, and PM
`0x3793`/`0x3795` overwrite `DM(0x1652)`, `DM(0x166b)` and `DM(0x1679)` — the
sequencer's own working set.  The garbage next-state and condition addresses
then walk `TrnProgress` through nonsense in a few hundred microseconds and
land on `0x0000`.  That is the "reset" at 8.02 s.

This corrects the earlier claim that slmodemd calls show no FFT corruption.
They show none only because they leave `0x37` after 113 ms and the pointer
never reaches `0x0df0`.  The corruption is not peer-specific — it is
dwell-time specific, and it bites about 1.2 s into the receive window.  Any
fix that legitimately keeps us in `0x34..0x37` long enough to do the real work
will hit it.

### One bug, two symptoms

Both open threads reduce to the same missing event.  The classifier PM `0x2461`
publishes `DM(0x198e)` and is also what completes the analysis; because it
never runs, (a) state `0x37` never advances to Tone B at `0x00a2`, and (b) the
analysis never stops appending, so the result buffer overruns into the
sequencer's action list.  Raising event 1 needs framer B — the fixed `0x0080`
length instance — to decode the 8-bit control-channel message, and framer B is
parked disabled at `0x25f3` by PM `0x3f4c` with only the unreferenced PM
`0x2602` installing it.

Next: find what dispatches the PM `0x2ee6..0x2eee` action table through the
script pointer `DM(0x1667)` in a real call.  PM `0x2148` is the interpreter and
PM `0x2169..0x2175` walk `DM(0x1667)` within 8-entry blocks based at `0x2be0`,
`0x2be8` and `0x2bf0`, so recovering who seeds that pointer for the INFO page
is the concrete remaining step — not another injection.

## The state-record format, and what actually seeds `DM(0x1667)`

### How a state record is applied

PM `0x336a` (installed as `DM(0x169f)` by PM `0x32dd`/`0x34c9`; PM `0x3376` is a
packed alternative) decodes the state script as `(offset, value-lo, value-hi)`
triples, writing each value to `DM(0x1642 + offset)` and stopping when the
offset equals `MR1 = 0x19`.  So a record can only reach `DM(0x1642..0x165a)` —
the raw fields, plus the candidate/condition indices at `DM(0x1653..0x165b)`
that PM `0x3329..0x3332` translate through the tables at `0x133e`/`0x131e` into
`DM(0x1692..0x1695)` and `DM(0x1696..0x169a)`.

PM `0x3435` then diffs each raw field against a shadow copy in
`DM(0x1688..0x1690)` and calls a per-field handler on change.  Two handler
shapes:

- PM `0x3480`, a **bitmask dispatcher**: for each set bit of the field, fetch
  an action address from a PM table and `CALL` it.  Used by PM `0x345e`
  (`0x2e9a`, 11 entries), `0x3463` (`0x2ea5`, 16), `0x3468` (`0x2eb5`, 13),
  `0x346d` (`0x2ec2`, 16), `0x3472` (`0x2ed2`, 16), `0x3477` (`0x2ee6`, 9) and
  `0x347c` (`0x2ee2`, 4).
- PM `0x34ae`, a **2-bit-slot pointer loader**: each slot indexes a PM
  sub-table and the fetched pointer is stored to consecutive DM words.  PM
  `0x349e` fills `DM(0x166a..0x166c)` (the transmit source `DM(0x166b)` among
  them) from the bases at PM `0x2bd5..0x2bd7`; PM `0x34a4` fills
  `DM(0x166e..0x166f)`; PM `0x34a9` fills `DM(0x1681..0x1683)`.

### `DM(0x1667)` is seeded, and it is not the problem

PM `0x2169`, `0x216b` and `0x216d` load `AY1` with `0x2be8`, `0x2be0` and
`0x2bf0` and fall into PM `0x216e`, which computes
`((DM(0x1667) - 0x2be0) & 7) + AY1` — switch script block, keep the phase
within it.  Those three are entries 10, 9 and 8 of the PM `0x2ea5` table, so
they are selected by bits in the state-record field **`DM(0x1644)`**.  The INFO
page initializer's block clear at PM `0x3f6a..0x3f6f` zeroes `DM(0x1667)`
first; PM `0x2149`/`0x214b` then walk it and PM `0x2165`/`0x2168` rewind it.

Watching it live confirms all of that works.  `DM(0x1644) = 0x0401` throughout
INFO selects block `0x2be8`, and `DM(0x1667)` walks `0x2be8..0x2bec` normally
across every state.  **The script pointer was never unseeded.**

### The action table is dispatched from a different field, and is downstream

PM `0x2ee6..0x2eee` is not reached through `DM(0x1667)` at all.  It is handler
PM `0x3477`'s table, dispatched as a bitmask from the state-record field
**`DM(0x164c)`** — bit N runs action N.

`DM(0x164c)` is `0x0000` for every INFO state `0x20..0x37`, and that is
correct, not a gap.  On the event-1 branch it is set by the states that follow:

```
  6.625  state 0x00a0   DM(0x164c)=0x0001   -> action 0, PM 0x2410, build the
                                               message table at DM(0x1986)
  6.626  state 0x00a2   DM(0x164c)=0x0080   -> action 7, PM 0x2434, transmit
                                               message 1, TX source 0x3b30
```

So the previous section's chicken-and-egg framing was wrong.  Installing
framer B and transmitting the 8-bit message are the **response** to event 1,
not its prerequisite: on receiving message 1 at `0x37` the page moves to
`0x00a0`, builds the message table, and at `0x00a2` sends its own message 1
back on the 1200 Hz Tone B carrier.  Nothing about the action block is
missing — the states that dispatch it are simply downstream of the transition
we never take.

That leaves exactly one thing unexplained, and it is now sharply posed: PM
`0x2470` is the only writer of a non-zero `DM(0x198e)` and it runs only from
the classifier PM `0x2461`, which only the 8-bit framer can reach.  So a real
card receiving message 1 at state `0x37` must be running framer B at that
point — yet PM `0x3f4c` parks it at `0x25f3` and the only installer, action 1,
is dispatched by a state we only reach afterwards.  Either another page or an
earlier INFO state sets `DM(0x164c)` bit 1 before `0x37` in a real call, or
framer B is installed by a path outside this overlay.  Dump `DM(0x164c)` and
`DM(0x19cf)` across every state of a call that gets further than ours — that
comparison, not more static reading, is what will settle it.

## INFOH is not it; the INFO page has two state chains and we take the wrong one

### INFOH checked and ruled out

INFOH (download `0x026e`) loads cleanly — "INFOH.F34 Overlay Version 1.00 Build
117-926" — and the bootpage table `DM(0x31d5)` maps **bootpage 10** to it
(entries are negative: `0xfd92` is `-0x026e`, `0xfda0` is `-0x0260` for page 7,
`0xfda1` is `-0x025f` for V.8).  So the firmware can request it.

It is not a variant of the INFO page, though.  Every region that matters
differs: the classifier/action block `0x2410..0x24ff`, the PM `0x2602` entry,
the framers `0x3510..0x35ff`, the PM action table `0x2ee0..0x2eef`, and the
record handlers `0x3435..0x34bf` all hash differently between the two
overlays, and INFOH brings its own DM image, so the INFO state records do not
exist in it.  `DM(0x164c)` reads `0x0006` after loading INFOH, but that is
overlay data sitting at that address, not a live state field.  INFOH is not
the missing piece.

### The missing piece is a state chain inside INFO

`tools/info_state_records.py` decodes every record and the state-vector table
at `DM(0x133e)`.  Across all 0x40 entries, exactly one record dispatches
action 1:

```
  index 03  state 0024 @07e5  DM(0x164c)=0002  ->  PM 0x2602 INSTALL FRAMER B
  index 2a  state 00a0 @1736  DM(0x164c)=0001  ->  PM 0x2410 build message table
  index 30  state 00a2 @175d  DM(0x164c)=0080  ->  PM 0x2434 transmit message 1
  index 32  state 00a7 @1796  DM(0x164c)=0101  ->  PM 0x2410, PM 0x243b (message 2)
```

Framer B is installed by the record for **state `0x0024`** at `DM 0x07e5`, which
belongs to the `0x07xx`/`0x08xx` chain (`07a0` state `0x20`, `07e5` state
`0x24`, `07f7` `0x26`, `080f` `0x28`, `082d` `0x2b`, `0836` `0x2c`, `084e`
`0x2e`, `08d5` `0x37` ...; records also simply run on into the next address,
so a chain of consecutive records is one state sequence).

Our calls never touch it.  PM `0x32dd` enters INFO with vector `0x0b87` and PM
`0x3317` with `0x0b69`, both in the `0x0bxx` chain, and the live trace walks
`0b18 -> 0b27 -> 0b36 -> 0b4b -> 0b60` before crossing into `0869 -> 087b ->
088d -> 0899 -> 08b1 -> 08e7`.  The `0x0bxx` record for state `0x24` leaves
`DM(0x164c)` at zero, so framer B is never installed — which is why the
`--init-info-detector-at-24` diagnostic worked: it was calling PM `0x2602` at
exactly the state whose real record would have done it, by luck.

### Correction: `DM(0x1651)` does reach `0x0080`

The record at `0x1736` (state `0x00a0`) sets `DM(0x1651) = 0x0080`, confirmed
live — it goes `0x0110 -> 0x0080` at the moment that state is entered.  The
earlier claim that `0x0080` was unreachable because PM `0x3f84` is the only
writer was wrong: it was a scan of direct-addressed writes only, and the
record loader writes `DM(0x1642 + offset)` through a DAG store.  So framer A
is itself reconfigured to the 8-bit message class at `0x00a0` and becomes the
event publisher there; framer B is not the only candidate after all.

### The remaining question

What selects the `0x07xx` chain over the `0x0bxx` one.  Both are complete INFO
sequences over the same states; only the `0x07xx` one arms the 8-bit control
channel at `0x24`.  Entry into it is via candidate index `0x02` or `0x14` in
the vector table, so the next step is to find which record's candidate field
carries those indices and what condition selects it — and whether the choice
keys off `DM(0x16b6)`/`DM(0x3f94)`, the INFO variant, since PM `0x3317` seeds
the `0x0bxx` entry while explicitly setting `DM(0x16b6) = 8`.

## What selects the `0x07xx` chain: `GEN_setup1` bit 7 and the reserved word `DM(0x3f8a)`

The chain entry is chosen by PM `0x34b5`, which is dispatched as bit 7 of the
PM `0x2eb5` action table:

```
  34b5: AR = 0x07a0 ; AX1 = 0x07a0
  34b7: AF = DM(0x3f8a) XOR 0x5678
  34ba: IF EQ JUMP 0x34c3                  ; magic present -> keep 0x07a0
  34bb: AF = DM(0x3ee0) AND 0x0040
  34be: IF EQ JUMP 0x34c1
  34bf:   AR = 0x0ae8 ; AX1 = 0x0ac4       ; override the entry vector
  34c1: DM(0x3f91) = DM(0x3ee7)
  34c3: CALL 0x34cb                        ; AF = DM(0x3f94) AND 0x0008
  34c4: IF NE JUMP 0x34c8
  34c5:   MR0 = AX1 ; I4 = 0x3376 ; JUMP 0x331e   ; packed record loader
  34c8:   MR0 = AR  ; I4 = 0x336a ; JUMP 0x331e   ; triple record loader
```

The field that dispatches it is `DM(0x167e)`, loaded verbatim from
`DM(0x3ee1)` at PM `0x3efe` — database offset `0x01`, **`GEN_setup1`**,
"operation mode parameters" in the ADDSP guide.  Our calls have
`GEN_setup1 = 0x0484` (bits 2, 7, 10), so bit 7 does fire PM `0x34b5`; the
`0x0bxx` alternative is bit 9 (PM `0x3315`) and is not set.

So the entry vector is decided by two words the host owns:

| `DM(0x3f8a)` | `DM(0x3ee0) & 0x0040` | `DM(0x3f94) & 8` | entry vector |
|---|---|---|---|
| `0x5678` | — | set | `0x07a0` |
| `0x5678` | — | clear | `0x07a0` |
| other | set | set | `0x0ae8` |
| other | set | clear | `0x0ac4` |

`DM(0x3f8a)` is database offset `0xaa`, inside the guide's elided
`A8..B7` reserved range.  The DSP only ever reads-and-clears it (PM `0x33bd`
is a condition handler testing for the same `0x5678`; PM `0x33be` clears it),
so it is a host-written token.  `tools/dial_kernel_dispatch.py` writes
`DM(0x3ee0) = 0x00c4` and never writes `DM(0x3f8a)`, so the override fires and
we get `0x0ae8` — the chain whose state `0x24` record leaves `DM(0x164c)` at
zero.

### Live with the token set

`--db-word 0x3f8a:0x5678` (`artifacts/eicon-live/run04`).  The page takes the
other chain — `0x24 -> 0x2c -> 0x2e -> 0x30 -> 0x32 -> 0x36 -> 0x37`, skipping
`0x22`, `0x28` and `0x34` — and **the firmware installs framer B itself**:
`DM(0x19cf)` cycles `0x25ab -> 0x25c7 -> 0x25e2` for the whole call, the first
time that has happened without the `--init-info-detector-at-24` diagnostic.
`0x37` then exits to `0x0026` rather than `0x0010`.

It is not a fix.  Framer B never validates a frame, `DM(0x198e)` stays `0`,
and event 1 still never fires.  The peer does worse than `run03`: one
`rxinfo0`, no L1/L2 probing, `vpcm: Link Error` after 14 s.  The new chain
skips state `0x34`, so the transmit window that unblocked probing in `run03`
is gone too.

Treat `0x5678` as an undocumented mode token, not a recovered setting: it is a
magic value in a reserved word, it changes the state graph, and nothing yet
shows a real MIPS supervisor writes it.  What it does establish is that the
`0x07xx` chain is reachable by configuration alone, and that framer B running
is necessary but not sufficient — this peer never sends the 8-bit message it
waits for.  The next question is what that message is on the wire, and whether
a V.90 call is supposed to see one at all, since `DM(0x3f94) = 9` selects
V.90 and the 8-bit class may belong to the `0x0006` (non-V.90) mode.

## No: the 8-bit class belongs to the V.90 decoding, not mode `0x0006`

The two record loaders read the *same* DM bytes differently.  PM `0x336a`
takes the offset from `w1 & 0xff` and the value from the low bytes of `w2`/`w3`;
PM `0x3376` takes the offset from `w1 >> 8` and the value from the high bytes.
The record blocks are dual-encoded — both decodings yield a plausible state
sequence over the same addresses — and PM `0x34c4` picks the loader from
`DM(0x3f94) & 0x0008`: set (mode `0x0009`, V.90) uses PM `0x336a`, clear (mode
`0x0006`) uses PM `0x3376`.

Decoding the token chain at `0x07a0` both ways:

```
  triple  (mode 9, V.90)   0020  0022  0024 actions=0002  0026  0028  002a  002b  002c
  packed  (mode 6)         0020  0022  0024 actions=0000  0026  0028  002a  002c  002e
```

**The framer-B install is present only in the triple decoding** — the V.90
one.  Under mode `0x0006` the same bytes at state `0x24` decode to
`actions = 0000`.  And the non-token entries never arm it under either
decoding:

```
  0x0ac4 packed (mode 6's own loader)   0020 0022 0024 0026 0028 002a 002c 002d  -- all actions=0000
  0x0ae8 triple (mode 9)                0020 0022 0024 0026 0028 002a 002b       -- all actions=0000
```

So the answer is no.  The 8-bit control channel is a **V.90** feature: it is
armed by the mode-9 decoding of the token chain and by nothing else.  That is
consistent with `run04`, where mode `0x0009` plus `DM(0x3f8a) = 0x5678` made
the firmware install framer B on its own.  Mode `0x0006`'s message class is
the 30-bit one PM `0x3f7f` selects (`DM(0x1651) = 0x01e0`), not 8-bit.

This also settles the previous section's closing speculation, which guessed the
opposite: the `0x0bxx` chain we ran originally is not "the right V.90 path" —
it is the V.90 path *without* the token, and the token chain is the V.90 path
with the 8-bit control channel armed.

What remains is why framer B, once genuinely installed and running for a whole
call (`run04`: `DM(0x19cf)` cycling `0x25ab/0x25c7/0x25e2` throughout), never
validates a frame.  Either this peer does not transmit the 8-bit message at
all, or it is transmitted in a form the magnitude-thresholded bit decision at
PM `0x3515` cannot recover.  Distinguishing those two needs the message's
on-wire form, and the transmitter is available to answer it: PM `0x2446` builds
the frame and `--info-action` can fire actions 3..8 to emit each message code.
Capturing our own transmission of message 1 and measuring it gives the exact
waveform framer B is waiting to receive, offline and without a peer.

## Stepping back: our own stack already documents this exact failure

The simple thing was in this repository the whole time.  `modem_engine.c:4856`,
written from live work against this same SmartLink peer:

> the peer gave up on Phase 3/4 and initiated a retrain: 70 ms silence then
> Tone A, waiting for our Tone B (V.90 §9.5.2.1) ... nothing ever answers Tone
> A, and the SmartLink peer declares a link error after ~3.1 s of unanswered
> Tone A (observed live 2026-07-22)

That is precisely the emulated card's symptom: slmodemd transmits Tone A,
nothing answers it, and it link-errors.  `run03` is the same statement from
the other side — putting 1200 Hz into the `0x34..0x37` window made the peer
advance to `TX_L1`/`TX_L2` immediately.  So the failure is simply **we never
answer Tone A with Tone B**, and the 8-bit control-channel work of the last
several sections was chasing the machinery around that, not the fault itself.

The earlier session's `_inject_l1l2_completion` comment — "the emulated INFO
probing classifier never publishes event 1, although the waveform is present"
— was right about the symptom.  Treating its Tone A reading as unfounded and
going after the message classifier instead was my error; both descriptions
converge on the same missing detection.

### Ruled out cheaply this turn

- **Host event acknowledgement.**  `tools/dial_kernel_dispatch.py:270-286`
  does service `DM(0x3fc1)` and the change bits; `change_flags` and `wstatus`
  stay `0x0000` across the call and `dbs_trnprogress` tracks every transition,
  so the host side of the interface is being consumed.
- **Receive level.**  Replaying `run02` with the µ-law stream scaled by 0.5x,
  2x and 4x produces a bit-identical state path and `DM(0x198e)` never leaves
  its stale `0x06a6` at any gain.  Not a threshold or scaling problem.
- **Skipping Tone A acquisition.**  States `0x2a`/`0x2b` last about two samples
  by construction, not because this peer causes an early exit: the record at
  `0x0b60` sets `DM(0x1650) = 1`, so the pre-condition PM `0x3391` fires on the
  second sample, and its only live condition targets state `0x2e` anyway.  The
  earlier note calling `0x2a`/`0x2b` "acquisition of the peer's Tone A" does
  not survive reading the record.
- **Any other writer of the event word.**  A watchpoint on `DM(0x198e)` across
  a whole call logs no write at all.  PM `0x2470` really is the only writer and
  it never runs, so the Tone A path does not reach it either.

### Where that leaves it

Two readings remain and they are worth separating before more work.  Either
this firmware answers Tone A from a detector we have not found — in which case
it is not `DM(0x198e)`, since nothing writes that word — or the INFO page
genuinely expects an 8-bit control-channel message here that slmodemd never
sends, and this build's Phase 2 is not the plain V.34 exchange the peer
implements.

The `run03` result is the practical lever either way: energy in the
`0x34..0x37` window is what the peer needs, and it advances immediately when
it gets it.  A response driven by an actual Tone A detector — rather than the
bit-clock stall `run03` used — is the next thing worth building, and
`tools/eicon_info_replay.py` can prototype it offline against the captured
peer audio before spending live calls.

## The tone detector: found, armed, counting — and two profiles are dead code

The INFO page's tone detector is FFT based.  PM `0x372d` fills the working
buffers, PM `0x36ed` runs the transform (256-point: span/count/stride walk
`0x80/2/4 -> 0x20/8/0x10 -> 8/0x20/0x40 -> 1/0x100/0x200`), and results are
appended to the 20-word buffer at `DM(0x0ddd)` through the pointer
`DM(0x15f3)`.  A detector is armed by writing a bin index to `DM(0x16f2)` and
a threshold to `DM(0x16f3)`, with `DM(0x16f0)`, `DM(0x16f1)` and `DM(0x06e7)`
as further parameters.

Four arming profiles exist in the image:

| routine | bin `DM(0x16f2)` | threshold `DM(0x16f3)` | reachable |
|---|---|---|---|
| PM `0x365c` | `0x0003` | `0x2aaa` | yes — armed on entering state `0x36` |
| PM `0x36b9` | `0x0005` | `0x1999` | yes |
| PM `0x3716` | `0x0008` | `0x1000` | **no reference anywhere** |
| PM `0x3722` | `0x0012` | `0x071c` | **no reference anywhere** |

PM `0x3716` and PM `0x3722` have no branch, no PM table slot and no DM vector
pointing at them — the same signature PM `0x2602` had before its action-table
slot was found.  PM `0x3722`'s bin 18 with the lowest threshold of the four is
the profile one would expect for acquiring a distant modem's Tone A.

The detection counter is `DM(0x06e6)`, incremented by PM `0x3702`/`0x370c`,
which are bits 2 and 1 of the PM `0x2ed2` table — handler PM `0x3472`, state
record field `DM(0x164b)`.  State `0x37` sets `DM(0x164b) = 0x1002`, so bit 1
dispatches PM `0x370c`, and live `DM(0x06e6)` counts `0,1,2,3,4` across state
`0x37`, one per ~26.7 ms analysis, before we leave at 4.  The condition
handlers PM `0x33ae..0x33bc` compare `DM(0x06e6)` against 3, 5, 6, `0x0e` and
`0x18`, so the state graph is waiting on a detection count.

So the detector is armed, the transform runs, and the count advances — what
never happens is a result that turns into `DM(0x198e)`.

Arming the dead profiles by hand does not help, and the reason is instructive:
calling PM `0x3722` or PM `0x3716` at state `0x34` or `0x36` leaves the state
path and `DM(0x198e)` bit-identical, because state `0x36`'s own record runs PM
`0x365c` on entry and immediately re-arms bin 3 over the top.  Any real use of
those profiles has to come from a state record that selects them, not from an
injection.

That points away from the MIPS/driver hypothesis rather than toward it: these
arming routines are dispatched from PM tables driven by state-record fields,
not from database words, so a different host-side database setup cannot reach
them directly.  What can is a state chain we do not run — the same conclusion
the `DM(0x164c)` work reached from the other side.  The concrete next step is
to search the record blocks for a field value whose PM `0x2ed2`/`0x2ec2` bits
resolve to `0x3716`/`0x3722`, which would name the state that arms the Tone A
profile; `tools/info_state_records.py` already decodes the records, so this is
a table lookup rather than more disassembly.

## The Tone A detector is armed at state `0x0c41`, which we never reach

Correcting the previous section: PM `0x3716` and PM `0x3722` are *not*
unreferenced.  The earlier scan looked for branches, PM table slots and DM
vectors, and missed immediate-store opcodes.  Two routines install them into
the analysis action lists:

```
  36ae: I4 = 0x0e53 ; DM(I4,M5) = 0x3716 ; DM(I4,M5) = 0x3700
  36e3: I4 = 0x0e4c ; DM(I4,M5) = 0x3722 ; DM(I4,M5) = 0x3700
```

`DM(0x0e4c)` and `DM(0x0e53)` are the analysis action slots — lists of PM
addresses terminated by `0x3700` — that the FFT sequencer executes.  (This is
also why the result-buffer overrun documented earlier is so destructive: the
20-word buffer at `DM(0x0ddd..0x0df0)` runs directly into the detector
programs.)

The installers are all bits of the PM `0x2ed2` table, dispatched from the
state-record field `DM(0x164b)`:

| bit | routine | profile |
|---|---|---|
| 4 | `0x36a7` | multi-action sequence |
| 6 | `0x36ae` | bin 8, threshold `0x1000` |
| 7 | `0x365c` | bin 3, threshold `0x2aaa` |
| 8 | `0x36dd` | — |
| 9 | `0x36e7` | — |
| **10** | **`0x36e3`** | **bin 18, threshold `0x071c`** |
| 11 | `0x36b9` | bin 5, threshold `0x1999` |

Scanning all 125 reachable records for `DM(0x164b)` gives the answer:

```
  @08b1 state=0a36  DM(0x164b)=0080  -> 365c (bin 3)      <- what our calls arm
  @08ff state=0b37  DM(0x164b)=0010  -> 36a7
  @0914 state=0c37  DM(0x164b)=0040  -> 36ae (bin 8)
  @09b3 state=0040  DM(0x164b)=0800  -> 36b9 (bin 5)
  @0a07 state=0b41  DM(0x164b)=0100  -> 36dd
  @0a28 state=0c41  DM(0x164b)=0400  -> 36e3 (bin 18)     <- the Tone A profile
  @0a3a state=0d41  DM(0x164b)=0200  -> 36e7
  @1b32/1b3e/1b59   DM(0x164b)=ff1f  -> arms every detector
```

The states form two parallel families: `0x37` with sub-states `0a37`, `0b37`,
`0c37`, `0d37`, and `0x41` with `0a41`, `0b41`, `0c41`, `0d41`.  Our calls
reach `0x37`/`0x0a37` and stop — never `0x0b37`, never `0x0c37`, and never the
`0x40`/`0x41`/`0x42` family at all.

Live exec watchpoints confirm it: PM `0x365c` runs on entering state `0x36`
and PM `0x36a7` at `0x37`, so `DM(0x0e4c)` only ever holds `0x376e`/`0x373a`
and `DM(0x0e53)` `0x3700`/`0x325c`.  **PM `0x36e3` never executes**, so
`0x3722` is never installed and the bin-18 detector never runs.  PM `0x36ae`
executes exactly once, early, during DIAL — never during INFO.

This also explains why arming the profiles by hand did nothing: state `0x36`'s
record re-runs PM `0x365c` on entry and overwrites the slot.

So the answer to "where is the Tone A detector" is: it exists, it is
configured with the lowest threshold of the four profiles as expected for
acquiring a distant tone, and it is armed by the record for state `0x0c41` —
a state in a family our INFO sequence never enters.

## The `0x41` family is the no-message path, not downstream of Tone B

Decoding complete records (the `0x19` terminator consumes a full three-word
triple) resolves the `0x41` decision at `0x09ec`:

```
  @09d7  state 0041
  @09ec  state 0a41
           default successor -> @0a07 state 0b41, pretest[12] (count == 5)
           slot 0: next[23] -> @0a28 state 0c41, test[13] -> PM 33b6 (count == 6)
           slot 1: next[24] -> @0a3a state 0d41, test[14] -> PM 33b8 (count == 24)
           slot 2: next[25] -> @09d7 state 0041, test[01] -> PM 33ca (always)
  @0a07  state 0b41
  @0a28  state 0c41                         <- arms bin 18 / Tone A
  @0a3a  state 0d41
  @0a4f  state 0042
```

Thus `0x0a41` takes its sequential `0x0b41` successor at detector-count 5,
selects `0x0c41` at 6, selects `0x0d41` at 24, and otherwise loops through
root state `0x41`.  At 6, `0x0c41`'s `DM(0x164b) = 0x0400` dispatches PM
`0x36e3` and installs the bin-18 Tone A profile.  This is a real, internally
connected detector path; the apparent absence of a vector pointing at
`0x0a41` is because the sequencer stores the address immediately after each
record as its default successor.  The `0x19` terminator's two-byte payload is
also live: it becomes `DM(0x165b)`/the default-successor pre-condition, which
is why the updated decoder prints it as `pretest` rather than discarding it.

The preceding default-successor records are:

```
  @098f  state 003c, event-1 candidate -> @1736 state 00a0
  @09a7  state 003e
  @09b3  state 0040
  @09c8  state 0a40
  @09d7  state 0041
```

State `0x3c` is the fork.  Its slot 3 is condition PM `0x2476`
(`DM(0x198e) == 1`) to vector index `0x2f`, record `0x1736`; that message
branch proceeds immediately to state `0x00a2` and transmits Tone B.  If the
message event does not fire, the inherited countdown pre-condition takes the
sequential path through `0x3e`/`0x40` into the `0x41` detector family.  No
record in the `0x00a0`/`0x00a2` message chain has a candidate back to vector
indices `0x23..0x25`, and the only candidate for root `0x41` is its own
`0x0a41` loop.

So the Tone A detector is **not after the Tone B response**.  It is on the
alternative no-message/default-successor chain.  Nor is it a direct candidate
of the `0x37` record: several intermediate receive states precede it.  In the
live call, framer A wins at `0x37` and sends the page to state `0x10` before
that chain can be reached, while the missing event-1 exit is what would send
it to the genuine Tone B path.  The `0x37` exit choice therefore remains the
single blocking question; the `0x41` lookup does not reveal a missing
post-`0x00a2` transition.

`tools/info_state_records.py --records 0x09d7:0x0a5b` now walks consecutive
records on their true boundaries and resolves each candidate and condition
index, so the result is reproducible without hand-splitting the table.

## The real V90D load path is INFO1a mode 6 -> bootpage 14

The desired destination is not selected by a magic TrnProgress value or by a
MIPS policy decision.  The DSP carries the V.90/V.34 decision all the way to
TIKRNL's ordinary overlay loader.

INFO initializer PM `0x3304..0x3310` computes the page to use when INFO
completes:

```
  AX0 = 0x000e
  AR = DM(0x3fbb) & 0x0070             ; BaudInfo, INFO1a bits 37:39
  if AR == 0x0060: DM(0x16b6) = AX0    ; value 6 -> page 14 / V90D
  else:
      AX0 = 0x000d
      if !(DM(0x3f94) & 2): AX0 = 8
      DM(0x16b6) = AX0                  ; non-6 result -> another data pump
```

That test is the firmware implementation of ITU-T V.90 §9.2.1.1.8: after
sending INFO1d and receiving INFO1a, the digital modem proceeds to V.90 Phase
3 only when INFO1a bits 37:39 encode integer 6; values 0 through 5 continue as
a V.34 call modem.

INFO's completion routine PM `0x2176..0x217f` then performs the actual DSP-side
page selection:

```
  DM(0x3fc1) |= 0x0100                  ; publish page-change status
  DM(0x3fb0) = DM(0x16b6)               ; bootpage_nr = 14 for V.90
```

From there the already-recovered normal loader path applies unchanged:

1. TIKRNL PM `0x0686..0x0694` indexes its table at `DM(0x31d5)` with
   `DM(0x3fb0) = 14`.
2. Table entry 14 is positive `0x026a`, the V.90 DPCM overlay, so TIKRNL
   publishes `DM(0x31aa) = 0x026a`, registers its post-download continuation,
   and yields through kernel service PM `0x000a`.
3. The host downloads overlay `0x026a`, sets `WSTATUS.BOOTFINISHED`, and
   dispatches the registered completion entry.
4. TIKRNL resumes into the V90D program for V.90 Phase 3.

This corrects the harness's synthetic `_load_v90d()` path, which watched for
TrnProgress `0x003b`, wrote bootpage 14 itself, and directly downloaded the
overlay.  There is no `0x003b` state in the decoded INFO record families, and
that shortcut bypassed the protocol decision we need to test.  It has been
removed: `KernelDispatch.service()` already serves the genuine page-14
request when INFO publishes one.

Therefore the path to pursue is:

```
INFO0 exchange -> Tone B/reversal -> L1/L2 -> INFO1d -> valid INFO1a
  -> INFO1a bits 37:39 == 6
  -> DM(0x3fbb) rate field 0x60
  -> DM(0x16b6) = DM(0x3fb0) = 14
  -> TIKRNL requests 0x026a V90D
```

Our call currently fails back at the `0x37` INFO0/Tone-B decision, long before
INFO1a can set that rate field.  Reaching the `0x41` Tone A detector is not the
goal by itself; the goal is to take the complete V.90 Phase 2 chain through a
CRC-valid INFO1a whose mode field is 6, then let the DSP request V90D naturally.

## Host-bit audit: V90D is enabled; the remaining selector is received INFO1a

Auditing `program_initial_setup()` and `program_v8_call()` against ADDSP V.90
User's Guide v5.3 §5.3.1 and §5.4.1 Tables 12, 13 and 15 gives:

| write DB | value | relevant meaning |
|---|---:|---|
| `GEN_SETUP0 +00` | `00c4` | extended training, PSTN, normal equalizer |
| `GEN_SETUP1 +01` | `0484` | answer, two-wire, internal clock, NORM operation |
| `GEN_SETUP2 +02` | `0030` | signal-quality step-up and fallback |
| `V8_SETUP +04` | `6000` | **V90_DPCM** and **digital network** |
| `INFO0_SETUP +07` | `f0fd` | V.34 Phase-2 capabilities and 3429 support |
| `delaycorrection +24` | `000c` | Eicon build's supplementary-buffer calibration |
| `NORM_H/L +28/+29` | `0001/8100` | V.8, V.90, and V.34 fallback |
| `SPEED_SEL_H/L +2a/+2b` | `001f/ff00` | V.34 fallback rates through 33600 |
| `SPEED_SEL_V90_H/L +79/+7a` | `003f/ffff` | every defined V.90 rate, 28000–56000 |
| `INFO0D_SETUP +7b` | `03b7` | lookahead 3, 3429 upstream, µ-law, codec measurement, −12 dBm0 |
| maxima `+7c..+7f` | `000e/0015/000e/0015` | V.34 33600 and V.90 56000, TX and RX |

These are all the documented host-controlled capability fields relevant to a
digital-side V.90 call.  The guide also defines generic line-driver fields at
`+0x70..+0x78`; the Eicon PRI kernel bypasses them as detailed below.

Two write-database words change after DIAL consumes them, but neither removes
V.90 capability:

- DIAL's line handler changes `GEN_SETUP2` from `0x0030` to `0x0070`, enabling
  its tone detector while retaining the requested `0x30` step-up/fallback
  bits.
- PM `0x0bb7..0x0bc8` masks the ordinary V.34 `SPEED_SEL_L` fallback word from
  `0xff00` to `0x0100` for the selected recommendation.  The separate V.90
  masks at `+0x79/+0x7a` remain `0x003f/0xffff`.

`LiveKernelModem.configure_modem()` now validates the surviving V90D fields
after DIAL activation and fails immediately if a later change clears one.
The fact that live calls reach INFO with `DM(0x3f94) = 0x0009` independently
confirms that V.8 selected the V.90 INFO variant, rather than rejecting our
host capability setup.

So: **yes, the documented host bits needed to negotiate V90D are set**.  They
cannot directly force page 14.  The final selector is deliberately peer-owned
by V.90 §9.2.1.1.8: the firmware must finish INFO1 and decode INFO1a bits
37:39 as 6.  Our present failure occurs before that field exists, at the
`0x37` INFO0/Tone-B branch, and is not evidence of another missing V90D enable
bit.

### Other PDF requirements, separated from the current blocker

A wider pass over the guide found four integration requirements that are not
V90D-enable bits:

1. **`delaycorrection` (`+0x24`) must be platform calibrated.**  The guide
   says to measure `roundtripcntr` in a calibrated back-to-back session and
   write the compensation when INFO is initialized.  We had not written the
   word, so it happened to retain build 117-926's DM-image value `0x000c`.
   The harness now writes and validates `0x000c` explicitly, preserving
   existing behaviour while making the assumption visible.  Replaying
   `run02` with `0x0000` shifts the state-`0x30` exit by 5 ms but leaves the
   complete path and the `0x37 -> 0x10` failure unchanged.  A real calibrated
   loop is still required before claiming exact ranging timing.
2. **SPORT0 setup words `+0x70..+0x74` and `V34SLOT +0x78` are required by
   the guide's generic T1/E1 line driver, but not consumed by Eicon's PRI
   kernel.**  The distinction is established below.
3. **`DATACONFIG +0x77` defaults to SCC1 data.**  Selecting value 1 in its
   two-bit data-pump field (word `0x4000`) chooses the documented IDMA TX/RX
   buffers.  This matters after V90D reaches DATASTATE and the PTY bridge is
   attached; it does not participate in V.8, INFO, or the page-14 decision.
   The experimental endpoint currently has no completed V90D data-interface
   cycle, so changing it during training would conceal that later work.
4. **Runtime boot service must finish within 75 ms.**  Guide §2.3 imposes the
   general page-load deadline (70 ms specifically for INFO -> V.34).  The
   emulator downloads and acknowledges a requested overlay synchronously in
   the same 8 kHz frame, so it meets the logical requirement without dropping
   or manufacturing bearer samples.

The guide also requires separate initial and training `change_wdb`
communication cycles and a `BOOTFINISHED` acknowledgement after each runtime
page load.  Both are already implemented.  The reconstructed 2400-Hz event
publication cycle is host-supervisor plumbing rather than another capability
setting.

## SPORT0 setup does not feed this Eicon PRI kernel

The suggestion to populate `Sp0CntrlReg`/the four multichannel masks was worth
testing because ADDSP guide §3.3 and §5.3.1 say they must be initialized before
the startup page for a T1/E1 interface.  That text describes the generic
Analog Devices line-driver integration.  This image instead runs Eicon's
**Diva Server PRI 30M kernel `0x0009`** around TIKRNL.

Static and runtime checks agree:

- There is no reference to database words `DM(0x3f50..0x3f54)` in either the
  PRI kernel or TIKRNL program image.
- PRI kernel PM `0x004b..0x0070` programs the real SPORT words
  `DM(0x3ff6..0x3ffa)` from its private per-channel descriptor block at
  `DM(0x2e44...)`, including dynamically recomputing RX/TX slot masks.
- After kernel startup the actual registers are `SPORT0 control = 0x8207` and
  RX-low mask `0x8000`; the generic setup database remains all zero.
- Writing zeroes, plausible `0x8207/ffff/ffff/ffff/ffff`, or marker values
  `0x1234/1/2/4/8` to `+0x70..+0x74` immediately before executing the kernel
  leaves those actual SPORT registers unchanged.
- Applying the same three configurations after modem setup and replaying all
  of `run02.rx.ulaw` produces identical state transitions and an identical
  signed-TX SHA-256:
  `8bbb79a33feb189e926edddeef657c6439b434b81319a8c064b4011c79533c61`.

The emulator's `adsp2181_sport0_tdm_frame()` is explicit about the missing
closed-MIPS layer: it models the assigned PRI descriptor by presenting one
selected 8 kHz slot, populating the channel table, and dispatching TIKRNL only
for that slot.  It intentionally does not emulate physical SPORT register
clocking or consult the generic setup words.

Therefore configuring `+0x70..+0x74` would currently be inert, and teaching
the emulator to consume guessed values would double-model the descriptor
filter rather than restore a missing input.  These words would matter in a
new generic-SPORT hardware model; they cannot alter INFO's `0x37` decision in
the present Eicon PRI path.

## V34SLOT is also bypassed; the real input is the PRI channel descriptor

The guide says `V34SLOT` at write-database offset `0x78` selects SPORT0 slot
0–31 and must be initialized before modem operation.  That is again the
interface of its generic T1/E1 driver, not Eicon's layered PRI integration:

- Disassembly finds no direct reference to `DM(0x3f58)` in the PRI kernel,
  TIKRNL, DIAL, V.8, INFO, or V90D.  Apparent matches at PM address `0x3f58`
  are code locations, not reads of the database word.
- Isolating `V34SLOT` from the emulated selected descriptor and consuming
  values 0, 1, 15, and 31 through the normal second `change_wdb` cycle gives
  identical `run02` state transitions and the same TX hash as above.
- The harness formerly wrote `V34SLOT = channel` and validated that the word
  survived.  That proved only that host-owned DM retained the value, not that
  firmware consumed it.  The write and validation have now been removed.

What Eicon requires instead is the full closed-MIPS **channel assignment**:
its private kernel descriptor links one of the `DM(0x2e00..0x2e1f)` PRI
sample slots to the TIKRNL task, supplies the companding descriptor, schedules
the task only for that 8 kHz slot, and supplies its RX/TX buffer pointers.
`V34SLOT` is not a substitute for that assignment.

The emulator currently models those effects explicitly:

- `adsp2181_sport0_tdm_frame()` presents the selected slot and dispatches the
  registered TIKRNL continuation once per 8 kHz frame;
- `DM(0x2f22)` selects the µ-law (`0x3c27`) or A-law (`0x3c07`) adapter;
- `assign_pcm_buffers()` restores the assigned one-word RX/TX pointers.

Thus there is no additional value or companion bit missing *for V34SLOT*.
Either a future emulator reproduces the complete MIPS descriptor assignment,
or the current selected-descriptor abstraction remains the boundary.  Merely
writing `DM(0x3f58)` cannot change the modem's line input, output, INFO state
chain, or V90D selection.

## What hardware still has that this harness does not

The remaining difference is now narrow enough to name, but not yet to reduce
to one magic word: **the native MIPS per-call modem assignment and its
CAI-derived private setup sequence**.

On the card, the path is:

```
PRI/SIG incoming call
  -> allocate a real per-call PLCI
  -> add_b1() 26-byte modem CAI (direction, digital modem, modulation/rate,
     INFO options, answer-tone/carrier timing)
  -> MIPS SERVICE_ASSIGN / switch_on for TIKRNL 0x0258
  -> private PRI channel descriptor + initial database-ring commit
  -> later symbol-13 command-mailbox scripts
  -> DIAL / V.8 / INFO
```

`LiveKernelModem` currently starts below that boundary.  It registers TIKRNL,
models one selected 8 kHz descriptor, supplies the companding and PCM pointers,
and writes the public ADDSP database directly.  That is sufficient to make
V.8 negotiate V.90 (`DM(0x3f94) = 9`), transmit a decodable INFO0d, receive a
CRC-valid peer control frame, and preserve exact sample accounting.  It is
not proof that every private switch-on/runtime command the MIPS normally
derives from the CAI has run.

The MIPS shim confirms both halves independently:

- Direct `SERVICE_ASSIGN` executes real firmware and commits TIKRNL's initial
  database ring (`DM(0x3327..0x3336)`), activating the kernel task.
- The native synthetic incoming-call route still reports `SIGNALLING ACTIVE,
  DSP UNASSIGNED`; its fake PLCI lacks ownership/allocator metadata from the
  real PRI/SIG ingress, so `connect_res()` never carries the full modem CAI
  into `SERVICE_ASSIGN`.  The current `--force-modem-dsp-assign` deliberately
  jumps over exactly this seam.

That missing seam is a better explanation for "hardware works" than SPORT0
or `V34SLOT`.  It can select private INFO setup before the overlay runs.  It
cannot write INFO's event word directly: PM `0x2470` remains the sole non-zero
writer of `DM(0x198e)`.  The observed consequence of wrong setup is instead
that INFO enters a family whose `0x37` branch waits for an 8-bit message while
the peer sends the valid 17-bit class.

`DM(0x3f8a) = 0x5678` is evidence that private setup can change the family,
not the answer by itself: forcing it arms the other framer but still does not
make the peer send the expected class.  The MIPS protocol image contains no
literal `0x5678`, so that token must not be promoted to a presumed hardware
write without a real trace.

### Concrete next comparison

The decisive next step is not another public database bit.  Complete the
native PRI/SIG ingress in `tools/eicon_mips_shim.py` far enough that the real
per-call PLCI reaches `connect_res()` and `SERVICE_ASSIGN` without
`--force-modem-dsp-assign`.  Then capture and replay, in order:

1. the switch-on database-ring records;
2. symbol-13 runtime command records at `DM(0x3310..0x3338)`;
3. the full interface `DM(0x3ee0..0x3fdf)` at V.8 result, INFO entry, and
   states `0x24`, `0x34`, and `0x37`;
4. effective INFO fields `DM(0x1642..0x165b)` after each state load.

Diff that against `LiveKernelModem`.  A hardware IDMA trace or DM snapshot at
the same milestones would be an even shorter oracle.  Until that comparison,
the honest answer is: we are missing the **native CAI-to-TIKRNL control
transaction**, not a known SPORT/V34SLOT value, and the exact private field
that changes the INFO message class is still unrecovered.

## Session 31: native incoming-call assignment recovered

The native transaction above is now reproducible.  The earlier ingress probe
contained one decisive disassembly error: `0x800172a8` is not an allocator.
It is the delay-slot instruction of a `jal 0x800c99e4` at the end of the
preceding IE helper.  Entering there merely executes `addiu a1,a2,0x28` and
returns through whatever stale frame the harness supplied.  The complete
signalling message parser starts at **`0x800172c0`**.

The lower PRI dispatcher interface to that parser was recovered from its
prologue and event jump tables:

- `gp+0x5e87`: current signalling event;
- `gp+0x5e88 = 0`: select the contiguous message representation;
- `gp+0x5ecf`: pointer to `{..., uint16 length @ +0x10, data @ +0x12}`;
- event `0x17`: allocate the network-originated call object while no call is
  attached;
- controller state `+0x24 = 2` and flag `0x00400000`: the pending-incoming,
  network-owned state established by the real lower dispatcher;
- event `0x0b`: deliver the parsed SETUP to that object.

The synthetic SETUP uses ordinary IDI `code,length,data` elements for 3.1-kHz
audio BC, V.42 LLC, and channel identification.  Event `0x17` now calls the
real `0x800785c4` allocator and links a firmware-owned object (observed at
`0x8028fda0`); event `0x0b` takes the incoming branch rather than trying to
allocate an outgoing B channel.  No synthetic object at `0x80807000` is
needed.

The other important correction is CALL_RES configuration.  The old i4l
compatibility path's six-byte CAI is not Eicon's CAPI20 hardware path.
`connect_res()` receives the complete 26-byte modem CAI produced by
`add_b1()`, so `modem_call_res_payload()` now sends that descriptor.

Consequently this command reaches modem service assignment without either
`synthesize_call_ingress()` or `--force-modem-dsp-assign`:

```bash
/tmp/eicon-venv/bin/python tools/eicon_mips_shim.py \
  --kernel artifacts/eicon-dsp/build-117-926/kernel/0009-diva-server-pri-30m-kernel \
  --tikrnl artifacts/eicon-dsp/build-117-926/tikrnl/0258-tikrnl81.f34-task \
  --mainloop --simulate-b-channel --call-steps 2 \
  --native-dm-out /tmp/native-modem.dm.bin
```

Observed result:

```text
[ingress] allocate event 0x17 ... after +1c=0x8028fda0
[ingress] SETUP event 0x0b ...
[call] IND 0x02 Id=0x02 Ch=0x00 ...
[call] simulated B-channel: ACTIVE (modem DSP assigned)
[mainloop] modem DSP path: service_assign=1 switch_on=1
[mainloop] native modem core block=0x1c000808 ...
```

The call trace contains exactly one `SERVICE_ASSIGN 0x80096980` and one
`SWITCH_ON 0x80090e58`; 2,274 host writes are made during the native call.
`--simulate-b-channel` now selects this route by default.  The force and
minimal-object options remain available only as independent diagnostics.

`--native-dm-out` writes all `0x4000` words from the naturally selected modem
core, identifying the core from the first host transaction after
`SERVICE_ASSIGN`.  At the end of this short pre-DIAL run, block `0x1c000808`
has consumed its initial ring (`DM3315 == DM3316 == 0x3327`) and has not yet
selected a datapump overlay (`DM3fb0 = 0`, `DM3f94 = 0`), which is expected.
This snapshot is now the clean baseline for diffing native switch-on/private
state against `LiveKernelModem` before DIAL and at each INFO milestone.

## Session 32: native MIPS supervisor attached to the SIP media clock

`eicon_adsp_sip.py` now has an experimental `--native-mips` backend.  It
preboots the actual MIPS image, runs the recovered native incoming call, and
attaches RTP to the exact ADSP selected by `SERVICE_ASSIGN` instead of making
a separate directly configured `Card`:

```bash
/tmp/eicon-venv/bin/python tools/eicon_adsp_sip.py \
  --native-mips --law pcmu \
  --registrar example.net --username 6001 --password 6001
```

`NativeMipsModem` provides the `Card`-compatible `dm`, `cpu`, `boot()`,
`configure_modem()`, and `frame_fast()` interface used by the SIP endpoint.
The RTP clock drives one native SPORT0 frame per G.711 sample, and every
160 samples the backend runs another real MIPS main-loop pass and drains host
status indications.  `ForceLaw` is now selectable (`1` A-law, `2` mu-law), so
the native call's companding agrees with the negotiated RTP payload.

The host ordering was also corrected: the asynchronous CALL_IND generated by
the lower SETUP path is consumed after NL ASSIGN and before CALL_RES.  The
native sequence now returns `OK` for both CALL_RES and N_CONNECT while still
reaching one `SERVICE_ASSIGN` and one `SWITCH_ON`.

An offline 320-sample PCMU smoke test proves the combined process remains
stable and advances both emulators without manufacturing/dropping samples.
It also exposes the next boundary honestly:

```text
[native-mips] SIP media attached to DSP block 0x1c000808 using pcmu
[native-mips] media clock attached; native SIG.MDM->TIKRNL connect callback is not yet routed
[probe] frames 320 ... page=0 mode=0 ptr=0/0
```

Thus the SIP/MIPS/core plumbing is running, but this mode does **not yet emit
ANSam or train a peer**.  Real hardware delivers a private inter-DSP bearer-
connected callback from SIG.MDM to the dynamically relocated TIKRNL task.
The current multi-core emulator has independent DSPs and no serial link for
that callback, so TIKRNL remains before DIAL with `DM3fb0=0` and no PCM
pointers.

A useful negative experiment localized this further.  Loading the extracted
fixed-address DIAL overlay and calling the direct harness's PM `0x06fc`
continuation is invalid: native TIKRNL publishes relocated service entry
`DM3308=0x35ab`, and the mixed fixed/dynamic call never returns to kernel
IDLE.  That forcing was removed from the SIP backend.  The next implementation
must recover the assigned descriptor's relocated continuation/callback (or
route SIG.MDM's actual inter-DSP message), not transplant the direct harness's
fixed PM addresses.

## Session 33: native loader relocation, not a relocated continuation

A runtime PM dump of the naturally assigned core corrected the interpretation
above. `DM3308=0x35ab` is a **DM command-descriptor pointer**, not a PM service
entry: DM `0x35ab` contains descriptor data while PM `0x35ab` is zero.  Calling
it as PM (as the old diagnostic `pump_direct_tikrnl_core()` does) is invalid.

The actual native TIKRNL image reveals the private callback construction:

- the card task loader inserts a seven-word prefix into the movable task
  segment;
- extracted source PM `0x06bb` appears at runtime PM `0x06c2`;
- source PM `0x06fc` appears at runtime PM `0x0703`;
- runtime PM `0x06a0` explicitly loads `AR=0x0703` before registering the
  callback with the private PRI descriptor.

The important new result is that DIAL is also a **movable** overlay in this
native layout.  Runtime TIKRNL calls DIAL setup at PM `0x0581`, whereas the
standalone extracted image and direct harness place the same entry at PM
`0x08f1`.  The native loader therefore assigns the overlay PM segment a base
shift of `-0x0370` and relocates its internal calls, loops, exported vectors,
and references.  Simply copying `pm.words` at its extracted addresses leaves
TIKRNL calling unrelated task code; merely shifting words without applying
the module's complete relocation semantics is also insufficient.

This explains every silence experiment consistently:

1. native assignment and switch-on consume the symbol-13 WDB ring;
2. `DM3eee=0x2000` remains pending because no native DIAL page is resident;
3. fixed-address DIAL makes `DM3fb2/3` look populated but does not match the
   native TIKRNL link layout;
4. the wrong entry either returns without consuming WDB or loops at its entry;
5. the unmodified native SIP backend remains stable because it does not apply
   this invalid mixed-layout forcing.

The next concrete step is now narrower: invoke the MIPS firmware's existing
module loader for download `0x0262` in the assigned service context (which
will apply the real segment allocator and relocations), rather than emulating
SIG.MDM by copying extracted PM/DM maps.  Once that loader publishes the
native `0x0703` descriptor callback, the existing exact 8 kHz SIP media clock
can drive it directly.

## Session 34: the real MIPS overlay loader is live

The SIP backend now invokes the protocol firmware's actual segmented ADSP
loader at MIPS `0x80086af8` for the naturally assigned modem core.  Its
transfer state is reconstructed from the already staged portable descriptor:

- `+0x00`: assigned DSP register block (`0x1c000808` in the probe);
- `+0x08`: staged `t_dsp_portable_desc`;
- `+0x0c`: allocated segment-base table;
- `+0x14`: initial DM block-list pointer from descriptor `+0x28`;
- `+0x10/+0x12/+0x18`: loader phase, block index, and block offset.

The native allocations recovered from the running task are supplied to the
loader: modem DM segment 4 at `0x32f0` and movable PM export segment 5 at
`0x0580`.  The earlier failed probe omitted state `+0x14`, causing the loader
to interpret zero-page data as block headers; initializing it was the key to
running the genuine routine.

Two hardware bulk-IDMA helpers (`0x80082a38` for DM and `0x80082b8c` for PM)
are now intercepted only while this loader runs.  They copy the firmware's
already-relocated scratch words into the selected ADSP core and return the
same success result as card hardware.  Normal card boot retains its existing
instruction-level path.  This preserves the MIPS implementation of all block
walking and all four relocation forms instead of recreating them in Python.

The connected bearer now loads the exact native module stack successfully:

```text
[native-mips] loaded 0x026d through MIPS (15028 host writes)
[native-mips] loaded 0x025c through MIPS (4108 host writes)
[native-mips] loaded 0x0262 through MIPS (12145 host writes)
[native-mips] connected bearer overlays loaded through MIPS
```

Post-load validation matches the extracted DIAL image while retaining the
native movable export: `DM3fb2=0x17bb`, `DM3fb3=0x1706`,
`PM0580=0x19b9cf`, and `DM3eee=0x2000`.  The fixed-address transplant has
therefore been eliminated.

At this point the private PRI descriptor still did not consume WDB. Session
35 below resolves that result as an IDMA boot-hold lifecycle problem rather
than another missing overlay or relocation.

## Session 35: native activation, V.8, and INFO

The last apparent activation blocker was an emulator lifecycle error.  The
core selected by `SERVICE_ASSIGN` was the one DSP still in IDMA boot hold.
Consequently every earlier `adsp2181_call()` was a no-op even though PM/DM
looked correctly loaded.  The native backend now releases that exact core and
runs TIKRNL's loader-relocated initializer at PM `0x0679` before loading the
connected-bearer overlays.

The recovered private PRI descriptor has two callbacks:

- runtime PM `0x0586`: selected-channel SPORT ISR adapter;
- runtime PM `0x0703`: RX/page/TX continuation, published by PM `0x06a0`.

They are selected only around one exact SPORT frame; the global kernel slots
are restored immediately.  The one-word buffers remain DM `0x2b00/0x2b01`,
and PCMU/PCMA selects the same `0x3c27/0x3c07` adapter as the direct kernel
harness.  DM `0x3131/0x3132` are the loader-relocated download flag/request,
not a descriptor-active flag.

Native page-download supervision is now live as well.  The relocated outbound
request is `DM3131/DM3132`, and `DM3143` publishes resume PM `0x06df`.
`NativeMipsModem` serves each request through the real MIPS loader, sets the
one-cycle BOOTFINISHED acknowledgement, and resumes TIKRNL.  A normal startup
now traverses genuine firmware requests such as:

```text
0x0270 SIG -> 0x0263 DIAL partial -> 0x0271 V.22FC
           -> 0x025f V.8
```

The connected callback's ADDSP V.90 §5.4.1 setup is delivered as two separate
WDB cycles.  Both are consumed in one native descriptor frame, after which
DIAL/V.8 produces nonzero line samples.  Long V.8 FFT passes preserve their
live ADSP context across media frames rather than being restarted when one C
execution budget expires.

The existing diagnostic `--force-info-after-v8` is now supported by the
native-MIPS backend.  It leaves V.8 resident for 12,000 exact samples; if V.8
then requests a low-level fallback instead of page 7, the host policy changes
the pending request to INFO and still loads/resumes it through the real MIPS
loader.  The offline silence proof reaches native INFO as requested:

```text
[native-mips] diagnostic post-V.8 fallback -> INFO at sample 12000
[native-mips] loaded 0x0260 through MIPS (7905 host writes)
[native-mips] page request 0x0260 (from 0x025f) resumed at PM 0x06df
FINAL resident=0x0260 bootpage=7 TrnProgress=0x002a
```

Run a SIP hardware test with:

```bash
/tmp/eicon-venv/bin/python tools/eicon_adsp_sip.py \
  --native-mips --force-info-after-v8 --law pcmu \
  --mips-kernel artifacts/eicon-dsp/build-117-926/kernel/0009-diva-server-pri-30m-kernel \
  --mips-tikrnl artifacts/eicon-dsp/build-117-926/tikrnl/0258-tikrnl81.f34-task \
  --registrar example.net --username 6001 --password 6001
```

This policy remains explicitly diagnostic.  With a valid calling-modem CM/JM,
shipping V.8 should request `0x0260` naturally and the same loader/resume path
is used without substitution.

## Session 36: live tower/slmodemd result

Four live calls were made from `root@tower.net.cryan.nz`, container `d-modem`,
with `slmodemd_trnref` registered as 6000 and dialing 6001.  Captures are under
`artifacts/eicon-native-tower/run01..run04`; peer logs were
`/tmp/slm-native{,2,3,4}.log` in the container.

The live run corrected two more native descriptor details.  TIKRNL's full
relocated no-host frame entry is PM `0x06c8` (source `0x06c1` plus the loader's
seven-word prefix); it must run before continuation PM `0x0703`.  Page requests
must also be gated by `DM3fc1 & 0x0100`: the initial `DM3131/3132 =
0x000d/0x0270` pair is task-image state, not by itself a live request.  With
those corrections, each real request is loaded only on the page-change strobe
and resumed through PM `0x06df`.

The `--force-info-after-v8` tower call reached native INFO on live RTP:

```text
sample 12000: load 0x0260, bootpage 7, TrnProgress 0x0020
sample 12160: TrnProgress 0x0026
sample 12480: TrnProgress 0x0028
sample 12640: TrnProgress 0x002a
```

This is an internal DSP milestone, not yet modem interoperability.  The peer
never left call-progress detection.  Its log ended with
`CALLPROG_WAIT_RING -> CALLPROG_END`; in the earlier native runs where V.8 was
allowed to fall through immediately it instead reported `Time Out Waiting For
ANSam`.  Received RTP in run01 was entirely PCMU `0xff`, confirming slmodemd
never began CM/JM transmission.  Our transmitted capture was a sustained
~2300 Hz tone rather than V.8's 2100 Hz ANSam.  Thus the current live frontier
is now precise: native MIPS/TIKRNL can load and execute INFO, but the answer
call enters the wrong DIAL/V.8 transmit state before the peer recognizes an
answer tone.  Fixing that TX state is required before a natural slmodemd INFO
request is meaningful.

## Session 37: the Linux driver identifies the TX-state boundary

The `divas4linux` source in `/tmp/divas4linux-master` rules out another set of
ADDSP setup-bit guesses.  `kernel/message.c:add_b1()` is the complete host
answer path.  For an incoming modem call it sets `CALL_DIR_ANSWER`, builds the
26-byte CAI already reproduced by `modem_cai()`, and sends that CAI through
CALL_RES.  `add_modem_b23()` separately sends LLC mode 9 (`V42_IN`).  The
host driver does **not** write `GEN_SETUP*`, invoke a DIAL PM export, or issue a
second ADDSP answer WDB.  Relevant CAI defaults are:

- `cai[7] = 0`: answer tone is not disabled;
- `cai[8] = 0`: negotiate the highest available class;
- `cai[9] = 0` for an ordinary incoming call: no reverse-direction override;
- `cai[24] = 0`: default answer-tone duration/speaker policy;
- resource `0x11`: asynchronous hardware modem.

The naturally assigned core confirms that the closed MIPS protocol has already
translated this call before Python touches it.  Its pending database begins
`0040 0024 0038 0008 ...`, has `WSTATUS=2000`, and contains the native
capability/rate defaults through the rest of `DM3ee0..`.  Our current
`attach_connected_bearer()` then overwrites that transaction with generic
ADDSP Tables 12/13/15 and directly calls relocated PM `0x0581`/`0x13cc`.
That sequence has no counterpart in the Linux driver and is now the leading
explanation for the wrong 2300-Hz fallback state.

A second boundary is equally important.  Python calls the low-level MIPS
loader `0x80086af8` directly and acknowledges the ADSP at PM `0x06df`.
The protocol image has thirteen callers of that loader (notably
`0x800a9d14..0x800aa158`); those callers own the per-channel transfer object,
post-download state and subsequent WDB publication.  Calling the loader body
alone bypasses exactly the supervisor transition that real hardware performs
after DIAL/V.8 downloads.

The corrective implementation should therefore remove the synthetic
answer-WDB/direct-PM activation, retain the MIPS-generated CAI transaction,
and route `DM3131/3132` requests through the owning MIPS per-channel download
caller.  Only the byte-exact SPORT line callback remains in Python.  The Linux
driver answers the policy question: answer/calling role is already represented
by native CALL_RES + LLC `V42_IN`; it should not be reconstructed as a second
host-side ADDSP transaction.

## Session 38: owner-path experiment disproves the proposed direct replacement

The Session 37 implementation was attempted end to end and then reverted after
instrumented offline runs.  The important correction is that TIKRNL81 task
`0x0258` does **not** use the newer per-channel owner at MIPS `0x800a9d14`.
`SERVICE_ASSIGN 0x80096980` takes its default/old-task branch and creates the
transfer state at `task_state+0x24`; it calls `0x80093d14`, then calls
`SWITCH_ON 0x80090e58` only when that initial task download returns one.  The
`0x800a9d14..0x800aa158` family belongs to newer task formats.

Reusing the real embedded transfer state and segment table was successful at
the IDMA level, but applying the assignment-time `SWITCH_ON` sequence to live
page requests was wrong: DIAL/V.8 was redirected to `0x0270/0x0271`, remained
silent, and never produced ANSam.  Removing the generic ADDSP cycles entirely
had the same result.  The MIPS-generated pending WDB starts
`0040/0024/0038`, is consumed, and selects the low-level online path; it is an
assignment transaction, not the later bearer-connected digital-answer
transaction.

This establishes a sharper boundary than Session 37: the Linux host driver
only supplies CAI and `V42_IN`, but the missing operation is inside the closed
SIG.MDM protocol between N_CONNECT/service assignment and its later
bearer-connected callback.  It is not any public Linux `add_b1()` operation,
not the generic loader owner, and not assignment-time SWITCH_ON.  The existing
synthetic second WDB remains necessary until that specific protocol callback
is recovered.  All unsuccessful owner-path code was removed so the last
validated native-INFO behavior is preserved.

## Session 39: the bearer callback is not missing

An ordered MIPS call trace corrects the remaining callback hypothesis.  The
modem assignment and its initial command occur synchronously inside native
CALL_RES, before N_CONNECT:

```text
CALL_RES 0x8001852c -> 0x8002aaec
         0x8002ace8 -> 0x80082794 -> table dispatch
         0x800827c0 -> 0x80096980  SERVICE_ASSIGN
         0x80097dd0 -> 0x80093d14  TIKRNL81 download owner
         0x80093e34 -> 0x80086af8  relocating loader
         0x80097dec -> 0x80090e58  SWITCH_ON
```

N_CONNECT and 32 subsequent main-loop passes contain no second
`SERVICE_ASSIGN`, no second `SWITCH_ON`, and no distinct modem-task callback.
The pending `0040/0024/0038` WDB is therefore the output of the real native
CALL_RES bearer callback, not an assignment placeholder awaiting a later
SIG.MDM event.  Sessions 37-38 were right that Linux supplies only CAI and
`V42_IN`, but wrong to infer a missing later MIPS callback.

The trace exposed one real emulator lifecycle mismatch.  Hardware releases
and initializes the newly downloaded TIKRNL task before SWITCH_ON publishes
its first command.  The shim previously left that core in IDMA boot hold until
`NativeMipsModem` was constructed, after all of CALL_RES had returned.  The
MIPS hook now releases the selected core and runs relocated initializer PM
`0x0679` at entry to native SWITCH_ON; the later SIP adapter recognizes that
it is already initialized and does not run PM `0x0679` twice.

This correction preserves the existing offline outcome: V.8 still returns to
DIAL after four line frames and the fallback remains a 2300-Hz `0x0271` tone.
A separate-RX-callback PM `0x0585` experiment produced the same page sequence
and was reverted.  Thus the remaining transmit defect is below the now-proven
CALL_RES callback boundary, in the private descriptor/V.8 line-state handoff;
it is not a missing Linux operation or delayed SIG.MDM bearer event.

## Session 40: V.8's four-frame exit fixed; TX encoding remains

A cycle trace of the first native V.8 frame found the exact page-17 writer.
V.8 initializer PM `0x2025..0x2026` copies `DM3f08` into `DM3994`.  Later PM
`0x207f..0x2092` tests `DM3994 & 0x0060`; when nonzero it writes bootpage 17
and returns to DIAL.  The native shim had written the raw PCMU idle octet
`0xff` directly to `DM3f08`, so this branch was guaranteed.  At the same seam
the working fixed-layout dispatcher presents processed idle status `0x0001`.
The native page completion now publishes that idle status before resuming V.8.

The comparison also exposed Eicon's private descriptor bridge.  The assigned
PRI descriptor pointer is `DM2f86=0x3110`, but generic modem overlays consume
compatibility word `DM32f6`; portable overlay data resets the latter to zero.
The MIPS loader path now republishes `DM2f86` into `DM32f6` after every overlay
download and before page initialization.

Offline silence no longer leaves V.8 after four frames: resident `0x025f`,
bootpage 6 and TrnProgress 4 remain stable for 16,000 samples.  A fifth tower
call (`artifacts/eicon-native-tower/run05`) confirms the timing live:

```text
sample     3: load V.8 0x025f
sample  2240: TrnProgress 0 -> 4
sample 20639: V.8 times out without peer signalling and requests V.32
```

This removes the immediate `page 17 -> DIAL -> 0x0271/2300 Hz` failure.  It
does not yet produce recognizable ANSam: slmodemd remains in
`CALLPROG_WAIT_RING`, and run05 TX is broadband/corrupted rather than a
2100-Hz carrier.  Comparing the native and direct V.8 state confirms page,
role, processed idle status, RX/TX pointers and descriptor selection are now
stable.  The remaining boundary is narrower again: conversion/accounting of
the V.8 TX word through PM `0x0703` and the private G.711 SPORT adapter, not
V.8 state selection.

## Session 41: native G.711 TX and ANSam recovered

The corrupted TX was a sample-accounting error, not a companding-table error.
A one-frame PM trace counted execution of the native continuation:

```text
PM 06c8: 1 hit
PM 0703: 2 hits
```

Calling runtime PM `0x06c8` already reaches the registered PM `0x0703`
continuation through TIKRNL's selected-channel dispatch.  `_frame_core()` then
called `0x0703` explicitly a second time.  Consequently the V.8 TX engine ran
at twice the 8-kHz line clock: its first nonzero sample appeared near 2131
instead of the direct path's 4267, and the nominal carrier was broadband when
observed at the real RTP sample rate.

The explicit second call is removed.  The movable V.8 handoff also now finishes
the same shared runtime state as fixed dispatch after its completion callback:
`DM3995=DM3999=0xffff` and the line adapter's current TX word `DM3764=0`.
Native TX is again taken from the firmware-published signed-linear pointer
`DM3fb4`; the existing SIP codec performs the selected G.711 companding once.

Offline PCMU silence now matches the direct firmware path:

```text
first nonzero TX sample: 4262 (direct: 4267)
FFT peak after startup:  2100 Hz
TX range:                -3472..+3475
```

Tower run06 (`artifacts/eicon-native-tower/run06`) validates the transmitted
G.711 stream end to end.  Its RTP capture peaks at 2101 Hz and slmodemd reports:

```text
V8_ORG_WAITING_FOR_ANSAM
V8_ORG_ANSAM_DETECTED_WAITING_TE
V8 ANSAM Detected (CM ready)
V8_ORG_SEND_CM
```

This is the first native MIPS-controlled call on which the peer recognizes
ANSam and transmits CM.  The next blocker is now RX: the Eicon remains at V.8
TrnProgress 4/11 and times out to V.32 without decoding the peer's CM, while
slmodemd eventually times out waiting for JM.  The private descriptor's G.711
to-linear SR1 input convention must be recovered without adding a second page
callback.

## Session 42: private G.711 RX publication recovered

The TX fix made the reciprocal RX boundary visible. Enhanced PM traces now
include AR/SR0/SR1. They show the selected SPORT walk carrying the prior raw
codeword in SR1 into runtime continuation PM `0x0703`, but Eicon's private
line descriptor has two additional publications which the generic walk does
not reproduce:

1. processed line status `0x0021` at `DM3f08`; and
2. the SPORT-compander's expanded signed sample at the V.PCM-family one-word
   RX location `DM3763` before the page's primary action.

Writing the RTP octet to `DM3f08` was the earlier mistake. It mixed line data
with status/result bits. Merely leaving the seam value `1` also fails: V.8's
RX action stalls. Fixed dispatch keeps `DM3f08=0x21` during ordinary media and
feeds the separately expanded sample through `DM3763`. ADDSP V.90 User's
Guide §3.3 explicitly specifies µ-law/A-law companding on the T1/E1 SPORT;
the RTP/DS0 octet remains byte-exact outside that hardware boundary. The
native adapter now models those two descriptor outputs after V.8 becomes
resident, and keeps doing so across INFO and later V.PCM overlays which share
the PM `0x1661` line adapter. DIAL startup retains its existing pre-descriptor
`DM3f08` path.

A replay of run06 proves that this is the missing RX input. Before
the change, native V.8's history at `DM3700..DM3753` remained stale boot value
`0xfce8` and timed out to V.32. The direct path filled it with the captured
PCMU octets and changed TrnProgress 4 -> 3 at sample 32297. Native now makes
the same transition at sample 32301 and remains in V.8.

Tower run07 validates natural bidirectional V.8:

```text
Eicon: TrnProgress 4 -> 3 -> 9 -> INFO page 0x0260
peer:  SEND_CM -> SEND_CJ -> V8_OK
peer:  remote V90=1, digital connection=1, pcmIndication=1
peer:  receives Eicon INFO0a and enters V.90 training
```

Run07 itself used the V.8-only form of the fix and therefore stalled in INFO at
state `0x28`. Replaying its exact RX capture with descriptor publication kept
active across INFO now advances naturally through:

```text
0x20 -> 0x22 -> 0x24 -> 0x26 -> 0x28 -> 0x2e
     -> 0x30 -> 0x32 -> 0x34
```

The original G.711 RX blocker is resolved. The next live run can now test the
existing INFO/Tone-B frontier and whether decoded INFO1a naturally requests
V90D overlay `0x026a`.

## Session 43: forcing event 1 is the wrong response

Tower run08 validates the complete native RX path and reproduces the earlier
kernel-dispatch result exactly: INFO reaches `0x37`, receives a CRC-valid
17-bit INFO0a, and takes successor `0x10`.  slmodemd then returns to
narrowband 2400-Hz Tone A.  Under V.90 §9.2.1.2.1 the digital modem must
finish its current INFO0d, detect Tone A and its phase reversal, and only then
send the correctly timed Tone B response before continuing at §9.2.1.1.3.
The native firmware does not make that response.

Tower run10 took a different timing path: slmodemd advanced to `TX_L1` and
`TX_L2` while Eicon INFO remained at `0x37`.  The Eicon still failed to finish
the corresponding receive/probing state, and after about 1.4 seconds the
known unbounded FFT result pointer overwrote the detector action list.  This
shows that transport is carrying the peer waveform, but the INFO detector
result/state transition is missing or incorrectly modelled.

A diagnostic then wrote `DM198e=1` after recognizing Tone A or a 160-ms probe.
Exact replay entered `0x00a0 -> 0x00a2`, and tower run11 produced:

```text
sample  70880: 0x37 -> 0x10 after repeated INFO0a
sample  80640: forced event 1
sample  80800: 0x10 -> 0x00a2
sample  81600: 0x00a2 -> 0x00ab -> V.8 retrain
```

This is a negative result.  Event 1 is not a generic "Tone A/L1 complete"
publication that may be asserted from either receive state.  Forcing it from
`0x10` skips the required Tone-A phase-reversal/ranging sequence and sends a
Tone B state at the wrong point; slmodemd remains in `TX_PHASE1_ANS` and the
call immediately retrains.  The `--native-phase2-gate` experiment was removed
rather than retained as a false fix.

The next task is therefore narrower: trace the firmware condition selected by
its actual Tone-A detector in recovery state `0x10`, including the phase-
reversal timestamp required by §9.2.1.2.1/§9.2.1.1.3, and determine why that
condition is never published from the SPORT receive samples.  Only that
condition may select the firmware's correctly ordered Tone-B/ranging path;
`DM198e` must not be used as a shortcut.

## Session 44: restore SPORT companding; recovery now exits naturally

The receive seam was still in the wrong numeric domain.  The native adapter
put the raw 8-bit PCMU code into both the selected SPORT word and `DM3763`.
That was sufficient for V.8 and the robust 16-lane DPSK framer, but it made
INFO's correlators operate on the logarithmic G.711 code rather than the
signed linear sample a real SPORT supplies.  It also contradicted the known
TX boundary (`DM3764` is signed linear) and ADDSP V.90 User's Guide §3.3.

`NativeMipsModem` now expands PCMU/PCMA only at the emulated SPORT boundary.
RTP and the DS0 stream remain byte-exact; no network transcoding, resampling,
gain change or sample-count change is introduced.  Exact run08 replay now
leaves recovery state `0x10` naturally rather than through `DM198e`.

Tower run12 confirms the change live without any event or state injection:

```text
0x37 -> 0x10 -> 0x28 -> 0x2e -> 0x30 -> 0x32 -> 0x34 -> 0x36 -> 0x37
```

slmodemd correspondingly advances through `TX_PHASE2_ANS -> TX_L1 -> TX_L2`
for the first time on the native path.  This is the correct response direction,
but not a completed Phase 2.

The apparent late arrival at the second `0x37` was a capture-coordinate error:
the endpoint's ADSP counter includes 5200 pre-media samples that are absent
from `.rx.ulaw`.  After removing that fixed 650-ms offset, state `0x36` begins
at RX 11.090 s, exactly as L1 begins.  Its root plus `0x0a36` timers total 160
2400-Hz symbol ticks (66.7 ms) and arm the bin-3 FFT profile immediately;
`0x37` is a continuation of that already-active receiver, not its start.
Changing those timers would violate the firmware sequence rather than improve
it.

Exact replay exposes the actual failure.  Each analysis increments
`DM06e6`; at count 5 the `0x0a37` condition correctly visits transient state
`0x0c37`, whose `DM164b=0x0040` is meant to dispatch PM `0x36ae`.  That routine
installs PM `0x3716`, and PM `0x3716` calls PM `0x3231` to reset the 20-word
result buffer before changing detector profile.  In the native replay the
pointer does not reset: it advances past `0x0df0` and overwrites the action
lists.  The next boundary is therefore why the transient `0x0c37` profile
installer/reset does not take effect, not Phase-2 line timing.

## Session 45: not overlay paging, and the installer never runs at all

Two candidate explanations for the Session 44 boundary were open: the wrong
firmware image (would a BRI build differ?), or an emulator fault in PMOVLAY
paging, since `0x36ae`, `0x3716` and `0x3231` all sit above PM `0x2000` and
therefore name a different instruction on each overlay page.

**Firmware is not a variable here.**  The `.bit` files are Xilinx bitstreams
for the board's line interface (`dsbri2m.bit` identifies itself as
`dsbri2m_fpga_chip.ncd`), with no ADSP code.  The BRI/PRI split inside
`dspdload.bin` is confined to the ~1500-word kernels, id `0x0006` (BRI 2M) and
id `0x0009` (PRI 30M); the handshake lives in the single card-independent
`V.90 DPCM Overlay` (id `0x026a`).  The only per-card V.90 variant in the file
is `V90.ANA APCM`, the analogue side.  There is no BRI V.90 firmware to try.

**PMOVLAY is not a variable either.**  `adsp2181_pmovlay`/`_dmovlay`/`_read_pm`
now export the page selectors and an overlay-resolved PM read, the `[EXEC]`
watchpoint line carries `pmovlay`/`dmovlay` and the fetched word, and
`tools/eicon_info_replay.py --overlay` traces the seam.  Across every native
tower capture, `pmovlay` and `dmovlay` are **0 at every sample and at every
installer execution**.  The Eicon loader relocates each page into the resident
image (Sessions 33-34), so the 2181's overlay pages are never selected and a
mis-paged fetch is impossible.

The replay first had to be put in the native numeric domain: `--overlay` alone
still reproduced the pre-Session-44 compressed-code path, because the SPORT
companding fix landed only in `NativeMipsModem`.  `sport_rx_word()` now lives
in `dial_tikrnl_drive.py` with both callers sharing it, and the new
`--sport-companding` flag selects it (opt-in, so recorded results in the old
domain are not silently reinterpreted).  With it, run08's replay leaves `0x10`
naturally, as tower run12 did.

With both domains correct, run09 reproduces the overrun offline and the
resident image reads back **exactly as documented**:

```text
36ae: 38e530 I4 = $0E53   b37161 DM(I4,M5) = $3716   b37001 DM(I4,M5) = $3700
3231: 40ddd0 AX0 = $0DDD  915f30 DM($15F3) = AX0     0d0400 I0 = AX0
```

So Session 44's diagnosis was wrong in an important way.  The installer chain
is intact; it simply never runs:

  - PM `0x36ae` executes **once**, at cycle 6.5 M, during DIAL - where the
    resident word is still `1b6b2f JUMP $36B2`, the DIAL image's content.  It
    never executes during INFO.
  - PM `0x3716` **never executes**.
  - PM `0x3231` executes twice, both times with `ret=3676`, i.e. called from
    PM `0x3675 CALL $3231` in the detector-init block at PM `0x366e` - never
    from PM `0x3716`.

The DM view says why.  Through the whole of state `0x37` the internal state
`DM(0x1652)` stays `0x0037` and the state record's dispatch field
`DM(0x164b)` stays `0x0000`.  The transient `0x0c37`, whose record carries
`DM(0x164b) = 0x0040`, is never entered, so nothing ever dispatches PM
`0x36ae` and the action slot `DM(0x0e53)` keeps the `0x325c`/`0x3700` pair it
was given at `0x0a36`.  Meanwhile the analysis counter runs past 5 without
stopping and the result pointer walks off the end - run09, counts 0..8:

```text
11.2457  count=0004  resultp=0de9  164b=0000  0e53=325c  buffer in
11.2721  count=0005  resultp=0deb  164b=0000  0e53=325c  buffer in
11.3257  count=0007  resultp=0def  164b=0000  0e53=325c  buffer in
11.3521  count=0008  resultp=0df1  164b=0000  0e53=325c  buffer OVERRUN
```

The overrun is therefore a *consequence* of the missed sub-state, not an
independent fault, and it corrupts the detector action lists from `0x0df1`
onwards exactly as the earlier section predicted.

The boundary moves up one level: find what loads `DM(0x164b)` from a state
record and why the `0x0a37` condition selects the plain `0x37` successor
instead of the transient `0x0c37` at count 5.  That is sequencer record
selection (PM `0x3335`, `DM(0x1692..0x169a)`), not detector programming, and
no firmware substitution can affect it.

## Session 46: the sequencer's equality test, and the emulator's ABS flags

Tracing the one writer of `DM(0x164b)` settles the Session 45 boundary and
finds the root cause, which is ours.

### What loads `DM(0x164b)`

PM `0x3335` disassembles as:

```text
3335  AY0 = DM($1647) ; AR = AY0 - 1 ; IF LT AR = 0 ; DM($1647) = AR
3339  I4 = DM($169A) ; CALL (I4) ; IF LE JUMP $334E     <- pre-condition
333c  MR0 = DM($1692) ; I4 = DM($1696) ; CALL ; IF LE JUMP $334D
3340  MR0 = DM($1693) ; I4 = DM($1697) ; CALL ; IF LE JUMP $334D
3344  MR0 = DM($1694) ; I4 = DM($1698) ; CALL ; IF LE JUMP $334D
3348  MR0 = DM($1695) ; I4 = DM($1699) ; CALL ; IF LE JUMP $334D
334c  RTS                                               <- nothing matched
334d  DM($1679) = MR0                                   <- record selected
334e  I4 = DM($1679) ; MR1 = $0019 ; I6 = DM($169F) ; CALL (I6)
3352  DM($1679) = I4
3353  DM($3FC2) = DM($1652) AND $00FF
3357  translate DM(0x1653..56) through 0x133e -> DM(0x1692..95)
335c  translate DM(0x1657..5b) through 0x131e -> DM(0x1696..9a)
3361  CALL $3435   ; 3362  JUMP $3339
```

So `DM(0x164b)` has exactly one writer: the record applier PM `0x336a` at
`DM(0x169f)`, writing offset 9 of whichever record `DM(0x1679)` names.  The
pre-condition returning LE means "fall through to the next record"; a
candidate returning LE means "jump to that record".  `tools/eicon_info_replay.py
--sequencer` traces all of it, and the `[EXEC]` line now carries `mr0` (the
candidate under test), `istate` and `analysis`.

Only record `@0914` (state `0x0c37`) carries `DM(0x164b) = 0x0040`, and the
`0x37` chain offers it twice:

```text
@08d5  state 0037  dwell DM(0x1650)=0x40, pre = 3391 (countdown), no candidates
@08e7  state 0a37  164b=0x1002, pre = 33ae (count == 3)
                   slot1 -> @0914 (0c37) when 33b0 (count == 6)
                   slot2 -> @08d5 (0037) always
@08ff  state 0b37  164b=0x0010, slot2 -> @08d5 always
@0914  state 0c37  164b=0x0040   <- the bin-8 profile installer, PM 0x36ae
```

Live, each analysis ran `0037 -> 0a37 -> 0b37 -> back to 0037`.  At `0a37` the
pre-condition returned LE with the analysis counter at **6**, falling straight
through to `@08ff` without ever testing slot 1 — the very candidate that
selects the transient at count 6.

### The pre-condition is an equality test that was behaving as `>=`

PM `0x33ae` and its siblings are all the same idiom:

```text
33ae  AY0 = $0003 ; JUMP $33B9
33b9  AX0 = DM($06E6) ; AR = AY0 - AX0 ; AR = ABS AR ; RTS
```

`ABS` then `IF LE` is how this firmware writes "count == N".  Our ALU set AZ
from the *input* being zero and touched AN only on the `0x8000` overflow, so
AN survived from the preceding subtract.  For `count > N` the subtract left AN
set and `IF LE` read true: every one of these tests fired at `count >= N`
instead of `count == N`.

`2100ops.inc` now takes AZ and AN from the result (`CALC_NZ(res)`), keeping AS
as the sign of the input and the `0x8000` overflow case, at all five ABS
sites.  `adsp2181_core_test` already exercised this idiom, but only at
`count == 3`, which passed either way; it now checks `count = 0..8` and fails
on the old core.

### Effect

Exact replay of the same captures, no injection, nothing forced:

- PM `0x36ae` now executes **during INFO**, at `istate=0c37`, `analysis=0006`,
  exactly as record `@08e7` specifies.
- PM `0x3716` executes for the first time ever, and calls PM `0x3231`, which
  rewinds `DM(0x15f3)` to `0x0ddd`.
- The result-buffer overrun is **gone in all five native captures**.  It was
  never an unbounded-FFT defect: the buffer was simply never being rewound
  because the state that rewinds it was unreachable.
- INFO passes state `0x37` for the first time.  run09, run10 and run12 reach
  `0x38` and `0x3a`; run08 and run11 still take the `0x10` recovery carousel,
  which is peer-timing dependent.

Sessions 30-45 read a long series of symptoms — the "terminal FFT
corruption", the `0x37` stall, the unreferenced `0x3716`/`0x3722` installers,
the dead detector profiles — as firmware behaviour to be reverse-engineered.
They were all one emulator flag bug.  The next boundary is real INFO state
`0x38`/`0x3a` behaviour against the peer.

## Session 47: live tower call — INFO completes, V.90 DPCM loads, TX stops

First live call after the ABS fix (`artifacts/eicon-native-tower/run13`,
endpoint registered as 6001, `slmodemd_trnref` as 6000 dialing `ATD6001`).
INFO ran to completion for the first time:

```text
12.120  0x0036 -> 0x0037
12.520  0x0037 -> 0x0038          <- past 0x37, live
13.540  0x0038 -> 0x003a -> 0x003c -> 0x003e -> 0x0040
13.640  0x0040 -> 0x0041          <- the 0x41 family, previously never reached
14.280  0x0041 -> 0x0042 -> 0x0044 -> 0x0046
16.240  0x0046 -> 0x004f   INFO_RX complete=0x0001, INFO_mode=0x0009 variant=0x000e
16.248  overlay request page 14 V.90 DPCM -> 0x026a served
16.260  bootpage 7 INFO -> 14 V.90 DPCM;  0x0060 -> 0x0062 -> 0x0064 -> 0x0066
16.460  0x0066 -> 0x00ea   Rstatus_ch=0x8800[change_h|speed_rx]
```

The peer agrees: it finishes Phase 2 (`V90Phase2Info: L2[20] = +204.332`),
constructs the Phase 3 demodulator (`Reset called`, `initial state set to
WaitForSd`) and waits.  Both sides are in V.90, on the right pages, at the
right time.  This is the furthest any call has reached.

It then fails for a new reason.  `V90Demodulator: Error Energy = -0.000` for
three seconds, then `VPcmFloModem (V90): retrain requested !!` /
`VPcmV34Main: Initiating retrain, requested DP is 90`, back to
`TX_PHASE1_ANS`.  Error Energy is exactly zero because there is no signal:

```text
TX ours->peer, RMS per 0.5 s     RX peer->ours
  14.50s  2078.8                   2479.1
  15.00s  1196.2                   2470.8
  15.50s     0.0                   1138.3   <- peer transmits throughout
  16.50s     7.7                   1096.5
  ...       0.0                    1093.8
  20.00s     0.0                   2271.1   <- peer gives up, retrain tones
```

`run13.adsp.csv` locates it exactly.  Through INFO the transmit sample pointer
`DM(0x3fb4)` holds `0x3764`, the signed-linear TX word, and `tx_value` carries
real modulator output.  At the page handoff:

```text
16.240  bootpage=0x0007 trn=0x004f  tx_ptr=0x3764  tx_value=0x0000  gen=0x3cb8
16.260  bootpage=0x000e trn=0x0060  tx_ptr=0x0000  tx_value=0x0000  gen=0x0000
```

**The V.90 DPCM page comes up with no transmit source at all** — `tx_ptr` and
`gen_control` are both zero and stay zero for the rest of the call.  The page
is resident and its state machine sequences normally (`0x60..0x66..0xea`,
`speed_rx` asserted), but nothing is wired to the SPORT transmit word, so the
digital modem never sends Sd and the peer times out in `WaitForSd`.

This is the same class of defect as the V.8 handoff fixed in `f5bfe3d` and
`0806495`: page-local transmit state that the real host re-establishes across
a boot-page change and our supervisor does not.  The next task is the bootpage
7 -> 14 handoff specifically — which database words the host writes to point
page 14's modulator at `DM(0x3764)`, the analogue of what was recovered for
V.8.  ADDSP V.90 User's Guide §5.4.1's database setup and the `DM(0x166a..
0x166c)` transmit-source group (PM `0x349e`, Session "state-record format")
are the places to look.

## Session 48: the page-14 transmit source — reproduced offline, two candidates ruled out

**Not fixed yet.**  This section records the reproduction and two negatives, so
neither is re-derived.

### Reproduction is now offline

`create_native_mips_modem()` replaying `run13.rx.ulaw` reproduces the failure
exactly — bootpage 7 -> 14 at 15.55 s, `DM(0x3fb4)` 0x3764 -> 0x0000, silence
thereafter.  No live call is needed to iterate on this; a run to 18 s takes
about two minutes.

### The page does swap the pointer, deliberately

Watching `DM(0x3fb4)` shows two writers, both inside the V.90 DPCM overlay,
alternating every frame:

```text
1a19: AR = DM($3F0F) ; DM($3606) = AR      <- save RX pointer
1a1b: AR = DM($3FB4) ; DM($3607) = AR      <- save TX pointer
1a1d: AR = DM($3FA7) ; DM($3FB4) = AR      <- swap in its own context
19eb: AR = DM($3606) ; DM($3F0F) = AR      <- restore
19ed: AR = DM($3607) ; DM($3FB4) = AR
```

`DM(0x3fa7)` is zero, so the swapped-in pointer is null.

### Negative 1: the SPORT0 transmit latch is not the transmit source

`adsp2181_sport0_tdm_frame()` returns the SPORT0 TX latch, and after the
handoff that latch varies every frame with signal-like values while the
pointer dereference reads zero — which looks exactly like the answer.  It is
not.  The core now reports whether the firmware actually drove the latch
(`adsp2181_sport0_tx_written()`), and comparing the latch against the received
stream settles it:

```text
TX[t+0] == RX[t]:   160/16000 =   1.0%
TX[t+1] == RX[t]: 15999/15999 = 100.0%
```

It is the kernel's TDM slot mirror — the received word delayed one frame.
Publishing it would echo the peer to itself.  `_frame_core` therefore
discards the return value, with the measurement recorded at the call site.

### Negative 2: the host cannot seed `DM(0x3fa7)`

`DM(0x3fa7)` is the base of a six-word block that pages *clear* during init:
DIAL at PM `0x13d3` (`docs/dial_v8_call.md`) and the V.90 page at PM
`0x2a4c..0x2a50` (`AX0 = 0x0006 ; DM(0x20df) = AX0 ; AX0 = 0x3fa7 ;
DM(0x20de) = AX0 ; CALL 0x2f9d`).  Seeding `DM(0x3fa7) = 0x3764` at the
`0x026a` load — the analogue of the V.8 handoff fixes — changes nothing:
the page clears it again and `DM(0x3fb4)` still reads 0x0000 for the rest of
the call.  A host-side seed has to land after the page's own init, if it is a
host-side value at all.

### Open, and the two things to settle next

1. **Is the page even trying to transmit?**  TrnProgress runs
   `0x60 -> 0x62 -> 0x64 -> 0x66 -> 0xea` in 200 ms and then stops, which is
   far too fast for V.90 Phase 3.  `Rstatus` never sets `online`.  If `0xea` is
   an abort, the transmit pointer is a symptom and not the fault.  The guide's
   TrnProgress table for the V.90 data pump has not been decoded yet; do that
   before more pointer archaeology.
2. **Which DM word is the page's transmit sample?**  Scanning all of DM across
   1600 post-handoff frames for words that change nearly every frame gives
   `DM(0x25b8)` (rms 9566), `DM(0x2059)` (4520), `DM(0x202d)` (3744),
   `DM(0x0f94)` (2971) and `DM(0x2056)` (2164) as candidates; `DM(0x2e00..
   0x2e3f)` at rms ~1080 is our own TDM mirror and `DM(0x2e44)/0x2e45` are the
   kernel queue indices.  If (1) shows the page is transmitting, correlating
   these against a known Sd sequence identifies the word `DM(0x3fa7)` should
   point at.

## Session 49: the V.90 data pump's TrnProgress table — `0xea` is a timeout abort

> **Superseded in part by Session 50.**  The table decoding below is static
> analysis and holds.  The replay trace is not reproducible: it was measured
> against a stale `libadsp2181.dylib`, and on a current build the page never
> reaches `0xea` — it runs its full 6.3 s budget in state `0x0060`.

`tools/v90_dpcm_state_records.py` decodes overlay `0x026a`'s state machine.
It is the INFO page's design one level deeper: **two** record layers over the
same two index tables.

```text
layer   base        terminator   state word    record pointer
outer   DM(0x1fe9)  offset 0x17  DM(0x1ff7)    DM(0x120f)
inner   DM(0x2001)  offset 0x10  DM(0x2008)    DM(0x204a)

DM(0x0613), 0x40 entries   candidate index -> record address
DM(0x05e0), 0x33 entries   condition index -> PM address
```

PM 0x2fe3 applies both — the same `(offset, lo, hi)` triple walk as INFO's
PM 0x336a, base in MR0, terminator offset in MR1.  **TrnProgress is the outer
layer's state word**: PM 0x2fba..0x2fbd is
`DM(0x3fc2) = DM(0x1ff7) AND 0x00ff`, and it is the only writer of DM(0x3fc2)
in the whole overlay.  PM 0x2f9d is the selection chain — four candidate slots
plus a fall-through, first condition returning LE wins — identical in shape to
INFO's PM 0x3335.

79 records decode to **56 distinct TrnProgress states**: `0x50..0x5c`,
`0x60..0x6a`, `0x70..0x80`, `0xa6`, `0xb0..0xbd`, `0xc0..0xd0`, `0xea`, and a
`0x0bc0/0x0cc0/0x0dc0/0x0ec0/0x0fc2` family whose low byte repeats `0xc0..0xc4`.

Two cautions the tool now encodes:

- **Records are deltas.**  The applier writes only the offsets a record
  carries; everything else keeps the previous record's value.  A record that
  sets some conditions to the never stub does not by itself prove a dead end.
- **Records cannot be found by scanning.**  A triple walk started at the wrong
  address still terminates on a byte that happens to equal the terminator
  offset, so a contiguous scan silently mis-aligns (it put state `0x50` at
  `@17b8` instead of `@180f`).  Seed from the vector table.

Conditions decoded so far, all sharing PM 0x3010 — the dwell countdown, the
same shape as INFO's PM 0x3391 (decrement through I0, return the old value, so
LE means expired):

| index | PM | meaning |
|---|---|---|
| 0x00 | 0x3038 | `AR = 0 + 1` — never |
| 0x01 | 0x2ffb | outer dwell `DM(0x1ff6)` expired |
| 0x02 | 0x2ffd | inner dwell `DM(0x2007)` expired |
| 0x03 | 0x2fff | **global countdown `DM(0x20e0)` expired** |

### `0xea` is terminal, and we reach it on a timeout

Tracing the outer pointer and the translated condition set through the run13
replay gives the whole page life — 0.21 seconds of it:

```text
15.5476 ptr=1d25 state=0050 | next=180f,180f,180f,180f test=3038,3038,3038,3038 pre=2ffb
15.5490 ptr=1869 state=0053 | next=180f,1c9e,180f,180f test=3038,2fff,3038,3038 pre=2ffb
15.5496 ptr=18cc state=0060 | next=18ba,1c9e,180f,180f test=30a7,2fff,3038,3038 pre=2ffb
15.6351 ptr=18d8 state=0062
15.6584 ptr=18e7 state=0064
15.6634 ptr=18f6 state=0066
15.7589 ptr=1ce0 state=00ea | next=1aee,1cce,180f,180f test=3038,3038,3038,3038 pre=3038
```

Two things settle it.

First, at `0x00ea` **every one of the five conditions is PM 0x3038**, the never
stub — including the two slots record `@1cce` does not set, which were already
never.  On this path the state has no exit at all.  The data pump parks there
for the rest of the call.

Second, the exit that took us there is visible from state `0x0053` onward:
slot 1 is armed with `next -> @1c9e` under `test[02]`, the global countdown
`DM(0x20e0)`.  `@1c9e` falls through `@1cb9 -> @1cc2 -> @1cce`, and `@1cce` is
state `0xea`.  PM 0x2f7d..0x2f80 decrements `DM(0x20e0)` and PM 0x2fff returns
it, so this is a plain "the page ran out of time" escape, armed for the entire
run and firing 0.21 s after the overlay loads.

### What this means for Session 48

The transmit-pointer investigation was chasing a symptom, as suspected.  The
V.90 data pump aborts on its own timeout a fifth of a second after it starts,
long before it could produce Sd; `DM(0x3fb4)` being null is what an aborted
page leaves behind, not the reason it never transmitted.

The question is now why the countdown expires immediately.  Two candidates,
both cheap to test on the offline replay: `DM(0x20e0)` is never seeded (its
writers are PM 0x2c69 and PM 0x2c7a, seeding 0x7530 and 0x1b58 plus
`DM(0x3fcb)`), or it is seeded but the routine that should restart it per
state never runs.  Watch `DM(0x20e0)` across the handoff before anything else.


## Session 50: `DM(0x20e0)` across the handoff — the deadline is inherited from INFO, and `0xea` is not a timeout

Session 49 asked for one measurement: the global countdown `DM(0x20e0)` across
the INFO → V.90 handoff.  `tools/v90_dpcm_replay.py` is that view.  A second
live call, `run14` (four dials, all four identical), is the corroboration.

### The harness trap, first

Two replay harnesses drive the same firmware:

| tool | harness |
|---|---|
| `tools/eicon_info_replay.py` | `LiveKernelModem` (`dial_kernel_dispatch`) |
| `tools/v90_dpcm_replay.py` | `create_native_mips_modem()` (`eicon_mips_shim`) |

Live captures come from `eicon_adsp_sip.py --native-mips`, i.e. the second.
On the INFO page the two track each other closely enough to be mistaken for
one another.  **On page 14 they do not.**  The kernel-dispatch harness parks in
TrnProgress `0x0060` for the whole page; the native one walks
`0x0060 -> 0x0062 -> ...` exactly as the live card does.  A first pass at this
session measured the countdown on the wrong harness and blamed a stale
`libadsp2181.dylib`; that was wrong, and Session 49's replay trace reproduces
exactly on a current build.  (Rebuilding the emulator is still necessary —
it is gitignored and the top-level makefile does not build it.)

Fidelity of the right harness, `run14` call 1, page 14:

```text
              live card          native replay
0x0060        11.000 s           10.2215 s
0x0062        11.160 s           10.3908 s
leaves        17.040 s           16.2696 s
dwell in 0x0062   5.880 s            5.879 s
```

The absolute times differ by the call-setup offset; the dwell agrees to one
millisecond.

### `0xea` is not a timeout abort

Replaying `run13` natively reproduces Session 49's trace to the sample —
`0x0050` 15.5476, `0x0053` 15.5490, `0x0060` 15.5496, `0x0062` 15.6351,
`0x0064` 15.6584, `0x0066` 15.6634, `0x00ea` 15.7589.  But with the countdown
in view, the reason is not what Session 49 concluded:

```text
15.5490  countdown seeded 0x4eb8 = 20152 ticks
15.6807  trn=0066 count=4d13 odwell=00f9      <- 249 ticks of outer dwell left
15.7589  trn=00ea count=4c19                  <- 19481 ticks still on the clock
```

`0x0066` is entered with `DM(0x1ff6) = 0xf9` (249 ticks), and `0xea` follows
249 ticks — 78 ms — later.  That is condition `0x01`, the **outer dwell**.
The global countdown still had 19481 of its 20152 ticks, six seconds, when the
page arrived at `0xea`.  It was armed as an escape from state `0x0053` onward
and simply never fired.  So the `0x60 -> 0x62 -> 0x64 -> 0x66 -> 0xea` walk is
ordinary dwell-driven sequencing that happens to end in a state with no exit —
not a page that ran out of time.

### `run14`: a second call, and there the countdown *is* the exit

Four dials, live, all four the same, and the native replay agrees:

```text
10.2209  countdown seeded 0x4b9c = 19356 ticks, addend DM(0x3fcb)=6556
                                                from DM(0x3fc9)=1967
10.2215  trn=0060
10.3908  trn=0062                <- and stays there
16.2696  trn=0050                <- 19356 / 3200 = 6.0488 s after the seed
```

Here the page reaches `0x0062` and holds until the countdown expires, then
restarts the outer chain at `0x0050`.  `0xea` is never reached.  So the two
calls take two different exits from the same page, and neither is the story
Session 49 told.

### The deadline varies per call, because it is inherited

The seed decomposes the same way in both calls, and the variable part is not
the data pump's own:

| | run13 | run14 |
|---|---|---|
| `DM(0x3fc9)` at the handoff | 2206 | 1967 |
| `DM(0x3fcb)` = `x 10/3` | 7352 (2.2975 s) | 6556 (2.0488 s) |
| budget | 12800 (4.000 s) | 12800 (4.000 s) |
| seed | 20152 (6.2975 s) | 19356 (6.0488 s) |

The budget half is exact and rate-correct: PM 0x2c6b takes `0x4e20` = 20000,
scales it through PM 0x200c + `DM(0x20e3)`, doubles, and 20000 × 0.32 × 2 =
12800 ticks at the measured 3200 ticks/s = 4.000 s on the nose.  The table
PM 0x200c..0x2011 is `0.2400 0.2743 0.2800 0.3000 0.3200 0.3429` — the V.34
baud family over 10000 — and index 4 is 3200.

The addend is another page's.  PM 0x2cb4 computes `DM(0x3fcb) = DM(0x3fc9) x
10/3` with a *hardcoded* factor, not one indexed by `DM(0x20e3)`, and
`DM(0x3fc9)` is maintained by the **resident INFO page** at PM 0x3caf/0x3cb4 —
counting at ~535 Hz and stopping seconds before the handoff.  Whatever it has
reached when INFO hands over lands in every deadline the data pump sets, so
the page's time budget varies call to call with how INFO ran.

`run14` also exercises the other seeder, and it double-counts.  Its restart at
16.2696 seeds 22712 = 9600 + 2 × 6556: PM 0x2c65 loads `0x7530` = 30000,
scales to 9600 ticks (3.000 s), calls PM 0x2c78 — which stores `MR1 +
DM(0x3fcb)` — and then falls into PM 0x2c68, `AR = AR + AY0`, storing again
with the addend applied a second time.

### Where this leaves the transmit question

`DI_control = 0x8000 [tx_request]` toggles every 20 ms across the entire
page-14 window on all four `run14` calls, and TX is 0.0 % non-idle for every
one of those samples.  The request side of the transmit path is alive; the
data side produces nothing.  That is Session 48's gap stated more sharply, and
it is upstream of anything the countdown does — both exits above are what
happens *after* six silent seconds, not why they were silent.

Next: `DM(0x3fc9)` — what INFO counts at ~535 Hz, why it stops when it does,
and whether PM 0x2cb4 is meant to read it at all.

```bash
make -C tools/adsp2181emu
/tmp/eicon-venv/bin/python tools/v90_dpcm_replay.py \
  artifacts/eicon-native-tower/run14.rx.ulaw --to 20
```

### Addendum: a call that runs the page to `0x00c2` and still never transmits

`run14` grew to five live dials.  The fourth took the page far deeper than
anything recorded before.  It entered through `0x004f` with
`INFO_variant = 0x000e`, as `run13` did, rather than the `0x0046 -> 0x0060` the
others took — but that is not what selects the deep path: a sixth dial entered
through `0x004f` with the same variant and went no further than `0x0062`.
What distinguishes the deep call is still unknown.

```text
13.140  0x0046 -> 0x004f        INFO_variant=0x000e
13.160  bootpage 7 -> 14, overlay 0x026a served
13.160  0x0060
13.240  0x0062 -> 0x0064 -> 0x0068
13.360  0x0072 -> 0x0074 -> 0x0076
13.520  0x00c2                  <- and stays, for 81.96 s
```

`0x00c2` is in the `0xc0..0xd0` family Session 49 decoded but never saw
entered.  The page held it for 82 seconds — no dwell expiry, no countdown, no
restart — until the peer hung up at 95.5 s.

TX across every page-14 state of all five calls, measured against our own
`run14.ulaw`:

| call | page-14 path | longest state | TX non-idle |
|---|---|---|---|
| 1 | `0x60 0x62` | `0x62`, 5.86 s | 0.0 % |
| 2 | `0x60 0x62` | `0x62`, 5.78 s | 0.0 % |
| 3 | `0x60` | `0x60`, 9.36 s | 0.0 % |
| 4 | `0x60 0x62 0x64 0x68 0x72 0x74 0x76 0xc2` | `0xc2`, **81.96 s** | 0.0 % |
| 5 | `0x60 0x62` | `0x62`, 5.64 s | 0.0 % |

Zero non-idle codewords in any of them, while `DI_control` toggles
`tx_request` every 20 ms throughout.

This separates the two problems for good.  The sequencer is *not* the blocker:
given the right INFO outcome it advances through eight states and parks
indefinitely in a late one, with no timeout involved.  The transmit path is
dead independently of which state the page is in, for 82 seconds at a stretch.
The countdown work above is correct but is not on the critical path — Session
48's transmit source is, and it should be the whole of the next session.

## Session 51: the host never answers the transmit request — and the rig is not real time

Two problems, found from the six `run14` dials.  The second one is the reason
page 14 has never transmitted, and it is not a firmware fault at all.

### The data interface is a host responsibility we never implemented

The ADDSP V.90 guide's host/DSP block is based at `DM(0x3ee0)` — confirmed
twice over, since guide offset `0xcd` (`DI_control`) lands on the known
`DM(0x3fad)` and offset `0xd0` (`bootpage_nr`) on the known `DM(0x3fb0)`.
That fixes the transmit-data registers:

| guide offset | address | name |
|---|---|---|
| 0x25 | `DM(0x3f05)` | TXD0 |
| 0x26 | `DM(0x3f06)` | TXD1 |
| 0x27 | `DM(0x3f07)` | TXD2 |
| 0xcd | `DM(0x3fad)` | DI_control |

The guide on `DI_control` bit F, and on TXD0 in V90D mode:

> TX request bit: if 1, the modem core asks the kernel to give a new data
> packet (in the TXD0, TXD1, TXD2-location); this bit is cleared by the DSP
> after arrival of the packet.
>
> In case of V90D operation: TXD0, b0 is the oldest bit of the Datagram,
> consecutive bit locations are filled up until b15, further bits are put in
> TXD1 and TXD2.  Datagram package size can vary from 21 to 42.

**Nothing in this repository ever writes `DM(0x3f05)`, `DM(0x3f06)` or
`DM(0x3f07)`.**  `eicon_mips_shim.py` says as much in a comment at the point
it declines to do it: "Data-plane delivery will be attached to the NL entity
separately."  It never was.

So the V.90 data pump raises the TX request every baud interval asking for a
datagram, the host does not supply one, and the page has nothing to modulate.
That explains the whole of Session 48 and the `run14` table above in one
stroke — 0.0 % non-idle TX in *every* page-14 state of every call, including
the 82-second stay in `0x00c2`, because the silence never depended on which
state the sequencer was in.  `DM(0x3fa7)` being a null transmit pointer is the
same fact seen from the DSP side, and Session 48's search for a transmit
*source* inside the overlay was looking on the wrong side of the interface.

The test is direct: on `tx_request`, write a known datagram into TXD0..TXD2
and let the DSP clear the request bit after consuming it, then measure TX.
Host-clearing bit F races the polling handshake and is not the ownership
specified by the guide.

### The rig loses real time once page 14 loads

Measured from `run14.rtp.pcap`, per call, comparing our own RTP send times
against the media they carry:

| call | media | wall | drift at end |
|---|---|---|---|
| 1 | 31.1 s | 30.7 s | constant −0.40 s offset, no drift |
| 2 | 32.5 s | 32.1 s | constant −0.39 s |
| 3 | 20.1 s | 19.7 s | constant −0.38 s |
| 4 | 95.4 s | 104.3 s | **+8.93 s (−9.4 %)** |
| 5 | 42.7 s | 45.0 s | +2.32 s (−5.4 %) |
| 6 | 27.0 s | 27.6 s | +0.60 s (−2.2 %) |

Short calls hold a fixed −0.39 s startup offset and then track real time
exactly.  The long ones start level and then fall behind monotonically, and
the crossover is at 15–20 s of media — which is when the V.90 page loads.  The
emulated data pump costs more than the 8 kHz budget, so from the handoff
onward we transmit roughly 10 % slow.

Per CLAUDE.md's third constraint, a few ppm of sample-rate error destroys the
constellation; this is ~100 000 ppm.  Everything a peer does after the page-14
handoff on this rig is a response to a signal that is not real time, so no
live conclusion about *the peer's* behaviour past that point is safe —
including the deep `0x00c2` call, whose peer had stopped sending (741 RX
packets in 104 s) long before the state walk happened.

The offline replays are unaffected: they are open loop and sample-driven, with
no wall clock in them at all.

### Session 51, tested correctly: DSP-owned acknowledgement and real-time headroom

The first direct test host-cleared `DI_control` bit F. That demonstrated that
the page notices the mailbox, but it did not implement the documented
handshake: the guide says the **DSP clears the bit after arrival**. The native
backend now writes deterministic PRBS datagrams to `TXD0..TXD2` while bit F is
set and leaves acknowledgement to the DSP. On the `run14` call-1 replay it
accepted 8337 of 8337 supplied datagrams. Use:

```bash
/tmp/eicon-venv/bin/python tools/v90_dpcm_replay.py \
  artifacts/eicon-native-tower/run14.rx.ulaw --to 20 --tx-prbs
```

The open-loop replay remains 0.0% non-zero during its 78245 page-14 samples.
That does not settle the live transmit question: its recorded Courier input
was produced in response to the old silent output and cannot react to the
changed host handshake. The supplied stream also changes the state timing
(the restart moves from 16.2696 s to 16.4721 s), confirming that the page is
consuming it. A closed-loop Courier call is the decisive next test.

The wall-clock blocker was removed independently. `Card.encode_g711()` now
runs the unchanged resident TIKRNL PM `0x1810` compander over each 160-sample
packet in one C call rather than crossing ctypes four times per sample. The
block output was checked byte-for-byte against the old scalar path. A
20-second native replay including PRBS delivery, G.711 encoding, and the full
DM/SCC diagnostic snapshots measured:

| media interval | wall time | utilization |
|---|---:|---:|
| 0–10 s | 5.45 s | 54.5% |
| 10–20 s (page 14) | 6.19 s | **61.9%** |

This preserves all 8000 samples/s and gives page 14 about 38% execution
headroom on the test machine. The live test mode is `eicon_adsp_sip.py
--native-mips --tx-prbs`; RTP timing must still be checked from the resulting
capture before interpreting the Courier's state progression.

## Session 52: executed-opcode audit finds incorrect MAC rounding

The ABS/ASTAT fault in Session 46 established that apparently coherent INFO
behaviour is not evidence that the adapted MAME CPU core is instruction
accurate. `tools/adsp_opcode_audit.py` now records resident-PM execution
coverage after INFO loads, discarding DIAL/V.8 coverage because movable pages
reuse the same addresses. On `run14` through 9.5 seconds it measured:

```text
INFO samples:          40939
unique executed PCs:    3393
total instructions: 59,832,252
ALU/MAC:            16,016,250
shifter:             1,883,855
hardware loop:         601,018
```

This turns the audit from a review of every nominally supported instruction
into a review of the exact firmware idioms reached by INFO:

```bash
make -C tools/adsp2181emu
/tmp/eicon-venv/bin/python tools/adsp_opcode_audit.py \
  artifacts/eicon-native-tower/run14.rx.ulaw --to 9.5 \
  --out /tmp/info-opcodes.tsv
```

The first conformance failure is in all four MAC implementations. For the
`MR +/- X*Y (RND)` forms, the MAME-derived code tested the low word of the
multiply product to detect the unbiased-rounding midpoint. The ADSP-2100
Family User's Manual §2.3.2.6 defines rounding on the complete unrounded MR
value. Once an existing accumulator participates, the product low word is not
MR0. INFO executes rounded MAC operations heavily, including
`MR=MR+MX0*MY0 (RND)` 143872 times in this replay.

A focused instruction test constructs `MR0=0x4000` plus the fractional
product `0x2000*1=0x4000`. The complete result is the exact `0x8000` midpoint
with an even MR1, so unbiased rounding must leave MR1 zero. The old core
produced one. The emulator now takes the midpoint from the complete result;
the regression covers MR0 and MR1.

That test exposed two more MAC defects. Every multiply was first evaluated
into a signed 32-bit `temp`. Fractional `-32768 * -32768` is positive
`0x80000000` in the MAC's 40-bit domain but became negative in `int32_t`;
unsigned products can exceed 32 bits much more broadly. All four MAC paths
now form products in signed 64-bit storage before applying the documented
fractional/integer placement. In addition, the two MF-destination paths never
updated ASTAT.MV, despite the multiply instruction's status table specifying
MV for both MR and MF destinations. They now calculate MV from bits 39..31 of
the complete result, as the MR paths do. A regression executes
`MF=-32768*-32768 (SS)` in fractional mode and verifies MV.

The run14 state path is unchanged by these corrections, so these real opcode
bugs are not claimed as that capture's protocol blocker. They demonstrate that
the opcode-audit direction is necessary.

### DAG modulo correction changes the INFO signal path

The next audit found a firmware-active error in all six DAG access/modify
paths. The User's Manual §4.2.3 defines circular modification as
`(I + M - B) modulo L + B`, with M signed. The core added signed M to an
unsigned host `I`. When a valid buffer had base zero and negative M crossed
its lower boundary, the host value underflowed before the boundary test and
the core subtracted L instead of adding it. For example, INFO executes
PM `0x329c` with `I0=0`, `M3=-14`, and `L0=0x400`; the old core produced an
effective address of `0x3bf2` instead of `0x03f2`. A trace through eight
seconds counted 1644 such firmware-reached underflows, including strides
-13, -14, and -511.

DAG modification now uses a signed intermediate and applies the manual's
single-wrap formula. When L=0 disables circular buffering, the result is
still masked back into the architectural 14-bit I register, so linear
`0 - 1` becomes `0x3fff`. Focused tests cover both that case and the base-zero
three-word circular case, where `(0 - 1) modulo 3 = 2`.

Unlike the MAC corrections, this changes run14's INFO output values beginning
before eight seconds. It also changes the later restart countdown seed at
16.4721 seconds from `0x58b8` to the saturated `0x7fff`, while reaching the
same high-level state sequence. This is therefore the first audit correction
known to alter the captured INFO/data-pump signal path materially.

### Shifter and count-stack checks

The high-volume EXP(HI/LO), EXPADJ, and NORM(HI/LO) paths were checked against
the shifter chapter. Tests now cover ordinary EXP/NORM normalization and the
HIX overflow case where SE becomes +1 and NORM(HI) fills from ASTAT.AC. The
firmware-reached semantics agree with the manual. Signed-left-shift undefined
behaviour inherited from the old C core was removed from shifter, M-register,
program-memory, MAC, and DIVQ operations. A UBSan build now replays INFO
through 9.5 seconds without a diagnostic.

The count stack had another conformance defect from the MAME adaptation:
every CNTR load pushed the previous value, including the invalid value after
reset. The User's Manual §3.2.3 explicitly excludes that first push. The core
now tracks whether current CNTR is valid, preserving all four physical stack
entries for dormant nested counts; a five-active-count test verifies that
COUNT_OVER remains clear. The run14 path is unchanged by this stack fix.

Remaining firmware-reached work includes conditional ALU/MAC flags,
multifunction ordering beyond the tested SR1 case, normal and exceptional
hardware-loop exits, and interrupt/loop stack interaction. Live Courier
testing remains paused until those idioms have conformance coverage.

### ADSP-218x global interrupt instructions were decoded as no-ops

The interrupt/stack audit found that PM `0x076b=0x040040` and
`0x0770=0x040060`, each executed 40939 times in the INFO coverage window,
are not generic stack-control padding. They are the ADSP-217x/218x
`DIS INTS` and `ENA INTS` opcodes. The Family User's Manual pp. 15-90..91
assigns reserved stack-control bit 6 to global interrupt control and bit 5 to
the enable value. The old 2100 core inspected only the original stack fields
in bits 4..0, making both instructions no-ops.

The core now has a global interrupt-enable latch, enabled after reset.
`DIS INTS` suppresses interrupt servicing without modifying IMASK or losing
pending latches; `ENA INTS` re-enables servicing and immediately recognizes a
pending unmasked interrupt. A regression disables interrupts, asserts IRQ2,
verifies that PC and IMASK do not change, then executes ENA and verifies the
pending vector to PM `0x0004`. The disassembler now names both opcodes.
This matters because the resident ISR dispatcher brackets TOPPCSTACK
manipulation with this pair; allowing an interrupt in that interval can corrupt
the shared PC stack even when ordinary replay timing happens not to inject one.

All 69 DO instructions reached in this INFO window terminate on NOT CE, and
none of their reached final instructions is JUMP, CALL, RETURN, or IDLE. The
ordinary loop comparator path now has a focused `CNTR=3` regression proving
three passes and correct fall-through. Exceptional end-of-loop control-flow
semantics remain outside this capture's executed idioms.

The MAC audit also corrected the host representation of architectural MR.
MR is 40 bits and MR2 is an 8-bit signed extension, while the inherited C union
allocated a 16-bit field for MR2 and retained unmasked host carry bits after an
accumulation. MAC writes, explicit MR1/MR2 writes, and SAT MR now normalize and
sign-extend exactly 40 bits. A maximum-positive-MR plus two regression wraps to
negative MR and verifies that MR2 reads as `0xff80`, not `0x0080`. This does not
change the run14 state path but closes a real overflow/readback error in the
firmware's heavily used SAT MR sequences.

## Session 53: live Phase 3 stall localised to the page-14 callback transition

The corrected live `run17` repeatedly reaches page 14 around 10 seconds and
advances through `0x60,0x62,0x66,0x68`; some calls also report `0x6a/0x72` and
DCD. This is not a successful Phase 3 continuation. In the final captured call,
the Eicon transmit stream becomes predominantly PCMU idle or held codewords
immediately after the transition. The Courier-to-Eicon stream later settles to
a measured 2400 Hz tone, so the audible solid tone is the Courier waiting while
the emulated Eicon has stopped emitting useful training.

Page-14-only coverage (`--page 0x026a`) exposes the CPU failure directly. Near
the later timeout/restart seam, the normal page-14 record decompressor at PM
`0x2fe3..0x2feb` rewrites callback word `DM(0x20b1)`:

```text
initial callback: 3038
cycle 219605136:  10e4
cycle 219605656:  a020
```

PM `0x2f9d` subsequently loads that word into I4 and PM `0x2f9e` executes
`CALL (I4)`. Architectural I registers retain 14 bits, so `0xa020` calls PM
`0x2020`. PM `0x2020..` is packed page data rather than a routine. Execution
walks through it until PM `0x204a`, whose data word happens to decode as
`IF EQ JUMP (I4)`. With ASTAT.AZ set and I4 still `0x2020`/later self-directed,
the core executes that accidental dispatch loop hundreds of millions of times.
This also explains the apparently meaningful logged `TrnProgress=0x204a`: the
old diagnostic labels page-private words after their role has changed.

The real MIPS driver was kept active every 160 samples during this replay.
Tracing its IDMA writes shows **no host DSP writes after the 18541 writes that
load page 14**. In particular there is no write at DCD or at the callback-table
rewrite. This agrees with ADDSP V.90 Guide §5.4.2: the host supervisor enters
SUPDATA only at `TrnProgress=DATASTATE (0xd0)`, not merely when DCD rises. The
rewrite and bad indirect call are internal DSP execution, not a driver reaction
to DCD. Host behavior can still be missing later, but it is not the cause of
this transition.

A deeper trace rules out accidental byte assembly in the decompressor. The
transition is selected by the first callback scheduler at PM `0x2f7d..0x2f9c`.
Its second callback, PM `0x2fff`, returns `DM(0x20e0)` through PM `0x3082`; when
that timer reaches zero, the scheduler selects state-image pointer `0x1c9e`.
The PM `0x2fe3` unpacker then consumes the page's **DM** record stream (not PM
instructions). The extracted firmware image contains the exact three records
which produce the bad word:

```text
DM 22a1 = 02c8   destination offset c8 -> 1fe9+c8 = 20b1
DM 22a2 = 0220   output low byte 20
DM 22a3 = 01a0   output high byte a0
```

Thus `0xa020` is byte-for-byte what build 117-926's state image requests. The
same unpack operation ends normally on its `AF=0x17` sentinel; ASTAT.EQ and the
loop termination are correct. It also changes the primary scheduler target
`DM(0x2039)` from `0x2eeb` to `0xf894`. The masked target PM `0x3894` executes
normally once before the next six-tick secondary dispatch. `DM(0x203a)` remains
`0x3db9`, `DM(0x201a)` remains zero, and the secondary-dispatch countdown
`DM(0x20df)` is not changed by the state image. Consequently PM `0x2a50` still
calls PM `0x2f9d`, which unconditionally consumes `DM(0x20b1)`.

The image is layered: after unpacking `0x1c9e..0x2475`, the primary scheduler
selects base image `0x180f` and restores part of the runtime tables. It does not
restore `DM(0x20b1)`. Source records, destination arithmetic, shifter OR
placement, sentinel handling, and the final `0xa020` all agree with the
extracted binary. The timer itself is also deterministic: PM `0x2c7a` seeds
`DM(0x20e0)=0x4b9c` and `DM(0x20e1)=0`; PM `0x2f7d..0x2f80` decrements it once
per primary scheduler dispatch until zero. The expiry is therefore the page's
roughly six-second Phase 3 watchdog, not wall-clock drift or an ADSP hardware
timer interrupt. During restart initialization the firmware reseeds it through
`0x4b9c`, `0x3f1c`, `0x58b8`, `0x671c`, and finally `0x7fff`, proving that the
state image is trying to initiate another training/recovery configuration before
the bad secondary call.

The remaining fault is therefore earlier: either the Phase 3 watchdog should
not select image `0x1c9e` in this operating state, or real hardware/host/watchdog
handling removes this scheduler before its next secondary tick. It is no longer
credible to fix this by byte-swapping `0xa020` or changing PM/DMD placement.

Trace tooling now supports page-selective coverage, watched PM execution,
richer watched-DM writes, and a rolling prior-PC history for this seam.

## Session 54: watchdog audit points back to the synthetic kernel continuation

There are three distinct mechanisms called a watchdog in the ADDSP interface,
and they must not be conflated:

1. `WSTATUS.TXdog`/`change_TXdog` and `changeBITS.RXdog` form an optional
   host↔DSP liveness handshake (ADDSP guide write offset `0x0e`, read offset
   `0xc1`).
2. `Unitimer` at read offsets `0x92..0x94` is a 1 kHz host-visible clock which
   the host line-follow-up can use for its timers.
3. Page 14's `DM(0x20e0)` is a private primary-scheduler count. It is the timer
   that actually expires immediately before state image `0x1c9e` is selected.

Supplying a correct changing TXdog nibble every 160 samples does not alter the
failure: page 14 still ends at PM `0x204a`, `DM(0x20b1)` still becomes `0xa020`,
and the firmware does not consume the synthetic TXdog request before trapping.
There are still no MIPS IDMA writes at expiry. This excludes the external
TXdog handshake as the trigger or cure for the callback transition.

A first interpretation that the synthetic boundary skipped TIKRNL's full
selected-channel continuation was wrong. Runtime coverage settles this exactly:
PM `0x06c8` executes once per sample and reaches the loader-relocated callback
at PM `0x0703` once per sample through the registered descriptor. This is the
same single-call accounting established in Session 41; explicitly selecting
`0x0703` makes it execute twice and recreates that old error. Runtime PM
`0x0703..0x07ed` already performs sample selection, calls the page through
`DM(0x3fb3)`, runs the host/data gate, and publishes the final SPORT word.
The extracted TIKRNL listing is shifted by the native loader's seven-word
prefix, so disassembling extracted PM at the same numeric addresses caused the
mistake.

The host communication subroutine at runtime PM `0x18b0` is likewise reached
once per sample. Its cadence word `DM(0x35f9)` counts `1 -> 0` and reloads from
relocated `DM(0x2f82)=1`. `DM(0x35f8)` is an ASTAT snapshot, not a Boolean:
zero makes `IF NE RTS` true because AZ is clear, while value `1` enables the
four conditional host slots. Forcing AZ there proves PM `0x18b8..0x18c8` is
otherwise executable, but does not affect the Phase 3 deadline or recovery.
Likewise, page-14 PM `0x299b..0x299f` sees `WSTATUS.change_TXdog` and clears bit
4 correctly. Driving TXdog every 160 samples leaves the low nibble consumed but
does not change `DM(0x20e0)`, state selection, `0xa020`, or the terminal trap.

Thus neither a missing PM `0x0703` call nor the optional external TXdog
handshake explains the six-second recovery. The selected SPORT descriptor is
already executing at exact 8 kHz accounting.

The MIPS side was then traced at every native `HOST_READ`. Each 20 ms main-loop
pass polls five words on every DSP. On the selected modem DSP they are:

```text
IDMA 6f18 / DM2f18 = 8000
IDMA 6f17 / DM2f17 = 8000
IDMA 6e49 / DM2e49 = SPORT/sample counter, +160 per pass
IDMA 6e46 / DM2e46 = foreground/error activity
IDMA 6f19 / DM2f19 = 0000
```

The paired `DM2f17/2f18` values are compared and acknowledged only when their
XOR changes. They remain equal across the trap. PM `0x204a` does not disable
interrupts: each SPORT interrupt preempts the bad foreground loop, updates
`DM2e49`, and returns to the loop. Consequently the MIPS liveness poll still
sees exactly 160 samples per 20 ms and has no reason to reset the DSP.
`DM2e46`, previously zero, begins increasing after the trap; the MIPS handler
at `0x800a507c` notices changes and reads/formats a diagnostic block, but does
not write or reload the DSP.

Continuing exact SPORT frames to 32 seconds (with a reduced foreground budget
after the trap) produces no MIPS IDMA writes, page reload, retrain WDB, or
cleardown. Calling 600 additional MIPS main-loop passes without SPORT time also
produces no action. `TrnProgress` remains the deliberately published `0x0050`
while the foreground remains at PM `0x204a`. There is therefore no omitted
host watchdog rescue to implement in this path.

This also corrects the phrase "earlier clean restart": Session 50 observed the
published `0x0062 -> 0x0050` state transition but never audited the following
indirect callback. It did not prove execution continued after the restart.
The evidence now supports a simpler interpretation: build 117-926's six-second
failure/restart path itself reaches an unusable secondary table. Successful
hardware must avoid this path by making Phase 3 progress before the deadline.
The callback trap is a terminal consequence of the existing Phase 3 stall, not
the cause to patch around.

The concrete missing boundary is now the V90D transmit publication. Generic
DIAL/V.8/INFO output uses the task pointer left in `DM(0x3fb4)`, which is why
the SIP adapter historically dereferenced that word after each frame. Page 14
uses the private scalar slot `DM(0x3fa7)` instead. The runtime adapter proves
the ownership and timing:

```text
PM 1a1b: DM(3607) = DM(3fb4)   save generic task context
PM 1a1d: DM(3fb4) = DM(3fa7)   publish previous V90D scalar to kernel
... page action computes the following DM(3fa7) ...
PM 19ed: DM(3fb4) = DM(3607)   restore generic task context
```

Reading `DM(0x3fb4)` after the frame does read the restored generic context,
so `DM(0x3fa7)` was tested as a signed-linear sample in live `run18`, then the
raw-G.711 alternative was checked against the captured value stream. The
experiment disproved that candidate. Across both calls, `DM(0x3fa7)` remained
zero for the whole valid page-14 interval. Treating it as signed-linear
therefore emitted PCMU silence; treating the same value as a codeword would
emit constant PCMU `0x00`, not a Phase 3 sequence. The only
nonzero values appeared after shared state was already corrupt. Both run18
calls reached `0x0060/0x0062` and left page 14 within 0.3 seconds; neither
produced downstream training. The experimental source selection was reverted.

PM `0x1a1d` is a context swap, not proof that the swapped scalar is the V90D
modulator output. The next candidate was the SPORT0 TX latch, and live `run19` disproved it.
Only PM `0x0079` writes TX0: `TX0 = DM(0x2e52)`, before the selected descriptor
continuation. PM `0x00bd` updates `DM(0x2e52)` from the generic TDM slot walk.
The apparent post-continuation waveform (`-8`, `8`, `1980`, etc.) is therefore
the delayed TDM/RX mirror already rejected in Session 41, not a V90D waveform.
Returning it on SIP made the call sound different because the Courier heard a
version of its own signal; both run19 calls then left page 14 almost
immediately after `0x0060/0x0062`. The SPORT-latch experiment was reverted.

We have now ruled out all three externally visible post-frame candidates:
restored generic `DM(0x3fb4)`, zero `DM(0x3fa7)`, and mirrored SPORT0 TX.
Following the firmware's own dataflow shows that zero output is intentional at
the state currently reached, not a missing line descriptor:

- PM `0x3db9..0x3dc6` copies the six-sample vector `DM(0x10ae..0x10b3)` into
  `DM(0x3fa7..0x3fac)` (three samples followed by their negatives).
- PM `0x2ee9` dispatches through `DM(0x2039)`. Mode zero selects PM `0x2eeb`,
  which deliberately clears the published scalar. Transmit mode selects PM
  `0x2eed..0x2ef2`, which walks the six-sample vector through `DM(0x20de)` and
  publishes one signed-linear sample at a time.
- PM `0x24de..0x24e3` derives that handler set from inner field `DM(0x2001)`.
  The initial inner state `0x0001` leaves it zero. Inner state `0x0020` sets it
  to `0x1000`, selecting the real serializer.
- The `0x0001 -> 0x0020` inner transition is gated at PM `0x30ce..0x30d2` by
  bit `0x0002` in outer field `DM(0x1fe9)`. The outer records do not set that
  bit until outer state `0x0080` (`DM(0x1fe9)=0x1402`). The best live run only
  reached `TrnProgress 0x0072`.

Consequently, V90D is not yet supposed to emit downstream training at
`0x0060/0x0062/0x0072`. The primary fault remains the receive-side outer state
machine failing to progress from `0x0072` through `0x0074`, `0x0076`, `0x0078`,
`0x007a`, and `0x007c` to `0x0080`; fabricating output earlier would violate
the firmware sequence. `tools/v90_dpcm_state_records.py` now decodes inner
records with PM `0x2fee`'s high-byte packing rather than incorrectly applying
PM `0x2fe3`'s outer low-byte format to both layers.

### Handover after Session 54

The tree is intentionally back on the validated generic `DM(0x3fb4)` transmit
path. Neither the run18 `DM(0x3fa7)` experiment nor the run19 SPORT/RX-mirror
experiment remains enabled. Do not restore either one. Page-14 silence before
outer state `0x0080` agrees with the firmware.

The next investigation should start at the receive-side outer-state seam, not
at RTP publication:

1. Replay the complete `run17.rx.ulaw`; unlike replaying only its final segment,
   it reaches page `0x026a` and outer state `0x0072`.
2. Watch `DM(0x1ff6)`, `DM(0x120f)`, `DM(0x1ff7)`, and execution of PM
   `0x2ffb..0x3014`. State `0x0072` installs dwell `0x003e`; determine why the
   expected timed records `0x0074`, `0x0076`, and `0x0078` are not published.
3. Separately watch `DM(0x204a)`, `DM(0x2008)`, and PM `0x2fee..0x2ffa` so an
   inner-record transition is not confused with the later recovery image that
   repurposes those words. Values observed after bootpage leaves V90D are not
   valid Phase-3 state.
4. Only after outer state `0x0080` is reached should line output be taken from
   the firmware serializer at PM `0x2eed..0x2ef2`/`DM(0x3fa7)`. At that point
   verify one G.711 companding operation and exact 8 kHz sample accounting in a
   new closed-loop Courier call.

The useful local captures are `run17` (best live progress), `run18` (zero
`DM(0x3fa7)` experiment), and `run19` (disproved SPORT mirror). Their large
capture files remain untracked and are not part of the source commit.

## Session 55: live slmodemd run disproves the expected `0x72 -> 0x74` dwell path

A new closed-loop call against `slmodemd_trnref` on the tower produced
`artifacts/eicon-native-tower/run20`. The SIP endpoint now has
`--trace-v90d-state`, and every capture CSV records both V90D record layers.
The exact outer sequence was:

```text
15.974625  ptr=18cc state=0060 dwell=0031
16.060125  ptr=18d8 state=0062 dwell=ffff
16.083875  ptr=18e7 state=0064 dwell=000f
16.088875  ptr=18f6 state=0066 dwell=0027
16.101375  ptr=1938 state=0072 dwell=003d
16.120750  ptr=19c8 state=0072 dwell=ffff pretest=002a
```

The state-`0x72` dwell is healthy. PM `0x2ffb..0x3014` decrements it from
`0x003e` through `0xffff` at the expected rate. The old handover's expectation
that expiry would publish the static records `0x74`, `0x76`, `0x78`, and
`0x80` was wrong for the live runtime image.

Before the dwell expires, V90D PM `0x1900..0x1939`, called from the page action
at PM `0x19a7`, writes zero through PM `0x1930` across DM
`0x1938..0x19c7`. Those addresses initially hold the extracted static records
for `0x74..0x80`. At expiry PM `0x2fb4..0x2fb9` correctly starts the outer
record applier at `DM(0x120f)=0x1938`, but the records have already been
replaced. The applier walks the zero triples until the surviving terminator at
`0x19c5`, updates only pretest to condition `0x2a`, and leaves state `0x72`.
There is no missing dwell callback and no failure to execute PM `0x2fe3`.

Condition `0x2a` is PM `0x30ea`: it succeeds only when bit 0 of
`DM(0x2004)` is set. That receive-side publication is now the concrete next
boundary. It must be traced back through the V90D demodulator before making
any transmit change.

The apparent descriptor repair `DM(0x32f7)=DM(0x2f87)` was tested and rejected.
`DM(0x32f7)` is zero at page load and becomes `4` through the normal runtime;
copying `0x3108` into it makes PM `0x1900` consume the wrong structure and
causes immediate broad DM corruption. Only the established
`DM(0x32f6)=DM(0x2f86)` compatibility bridge remains.

This call also reconfirms that G.711 is not the domain error. RTP remains raw
PCMU, while the emulated SPORT receives exactly one G.711 expansion to signed
linear PCM as required by ADDSP V.90 Guide §3.3. The same boundary was already
required to make INFO complete. `run20` naturally reached page `0x026a` and
state `0x72` with that domain.

## Session 56: `DM(0x2004)` is generated by the state-image stream

An execute/data watch on the complete run20 replay resolves the `DM(0x2004)`
boundary. It is not an independently latched demodulator flag and it is not
written by the inner record applier. PM `0x1930` writes it while the PM
`0x1900..0x1939` stream walker sweeps sequentially through the outer/inner
state-image addresses:

```text
cycle 204162466  PM 190f reads DM(0x0ad5) into SR0: 0000
cycle 204162480  PM 1930 writes SR0 to DM(0x2004): 0000
cycle 204163546  PM 30ec reads DM(0x2004):          0000
```

The destination and source pointers at the write were `I0=0x2004` and
`AX1=0x0ad5`. The preceding writes mapped `DM(0x0ad3)->DM(0x2002)` and
`DM(0x0ad4)->DM(0x2003)`; the following write maps
`DM(0x0ad6)->DM(0x2005)`. This is deliberate stream relocation, not a random
single-word corruption. It also explains why restoring the extracted static
records after state `0x72` does not make the state machine advance: those DM
addresses are active stream storage by then, and restoring them damages the
running producer.

The immediate source is initialized at PM `0x39fe..0x3a03` as:

```text
DM(0x0ad5) = DM(0x0dd7) - 3 * DM(0x0acf)
```

PM `0x3996..0x39b1` derives `DM(0x0acf)` from a table selected by
`DM(0x0dd7)`. The input is itself not opaque: PM `0x3680..0x3688` copies the
13-word decoded block `DM(0x0a52..0x0a5e)` to `DM(0x0dd3..0x0ddf)`, so this
word is the direct mapping `DM(0x0dd7)=DM(0x0a56)`. In run20 the resulting
remainder is zero, so condition `0x2a` can only remain false when that word
reaches `DM(0x2004)`. The outer scheduler is functioning correctly: it tests
the freshly written word about 1,066 DSP cycles later.

There is a second dependency that rules out forcing `DM(0x2004)=1` as a fix.
The initial inner state `0x0001` has pretest condition `0x28` at PM
`0x30ce..0x30d2`; it cannot enter inner state `0x0020` until bit 1 of outer
field `DM(0x1fe9)` is set. The extracted outer `0x0080` record supplies
`DM(0x1fe9)=0x1402`. Therefore `DM(0x2004)` is one generated member of the
coupled state image, not the root ownership boundary.

The next trace should start with the producer configuration, specifically the
runtime writers/copy path for `DM(0x0dd7)` and the table entry that becomes
`DM(0x0acf)`. New `--trace-v90d-state` captures include `DM(0x1fe9)`,
`DM(0x2004)`, `DM(0x0ad5)`, `DM(0x0dd7)`, `DM(0x0acf)`, and the decoded source
`DM(0x0a56)` so a live call can show whether the zero remainder is inherited
from the firmware configuration or differs with peer training input.

## Session 57: live run21 shows `DM(0x2004)` is not the pre-`0x80` blocker

A fresh tower `slmodemd_trnref` call using the expanded trace is captured as
`artifacts/eicon-native-tower/run21`. It reproduced run20's state path and
made the source ordering explicit:

```text
state 0050: decoded=0000 input=0000 scale=0000 source=0000
state 0052: decoded=00ff input=00ff scale=0000 source=0000
...
state 0072: decoded=00ff input=00ff scale=0000 source=0000 iflag=0000
```

An execute watch found the producer of the decoded word. PM `0x2cef..0x2cf0`
copies six words from `DM(0x20f6..0x20fb)` into
`DM(0x0a53..0x0a58)`, so `DM(0x0a56)=DM(0x20f9)`. PM
`0x23db..0x23e0` deliberately sets `DM(0x20f9)=0x00ff` whenever outer field
`DM(0x1ff1)` is below four. Every extracted normal outer record sets that
field to zero or one, never four or greater. The `0x00ff` value is therefore a
sentinel for this mode, not evidence that the SPORT decoder failed to produce
a numeric estimate.

The first sentinel transition is handled correctly:

```text
PM 2cf0  DM(0x0a56) = 00ff
PM 3688  DM(0x0dd7) = 00ff
PM 369e  00ff XOR old 7fff != 0
PM 3985  DM(0x0a34) = 00ff
PM 3988  00ff >= 00ff, branch to the sentinel/recovery initializer
```

Consequently PM `0x3996..0x3a03` intentionally does not calculate a new
remainder for this sentinel, and `DM(0x0ad5)` remains zero.

A diagnostic replay forced `DM(0x20f9)=4`. The complete derived path then
worked: `DM(0x0a56)=4`, `DM(0x0dd7)=4`, `DM(0x0acf)=1`, and
`DM(0x0ad5)=1`. It did **not** advance outer state `0x72`. The stream walker
did not reach and write `DM(0x2004)` until after the outer state had already
been cleared during recovery, at which point it still copied the recovery
image's zero. This rejects both the sentinel and the remainder calculation as
the cause of failure to reach state `0x80`.

Condition `0x2a` belongs to the record installed by outer state `0x80`; it is
a post-`0x80` condition. The stalled runtime only appears to wait on it because
the zeroed record walk skips the `0x74..0x80` publications and eventually
consumes the surviving `0x19c5` terminator/pretest bytes. Treating
`DM(0x2004)` as the prerequisite for entering state `0x80` had the dependency
backwards.

The unresolved boundary moves back one level: determine why PM
`0x1900..0x1939`'s generated state-image stream supplies zeros while its
destination overtakes `DM(0x1938..0x19c7)`. For the write to `DM(0x1938)`,
the immediate source is `DM(0x0409)`. The same mapping later uses
`DM(0x0ad5)->DM(0x2004)`: both have the exact destination offset `+0x152f`.
A write watch on `DM(0x03f0..0x042f)` from V90D page entry through recovery
saw no producer write. Session 58 below corrects the initial interpretation of
that fact: this routine deliberately substitutes zero while its cursor is
outside the configured bulk interval, so the low-DM contents are not the
ownership boundary.

## Session 58: the destructive stream is the bulk-delay adapter; its cursor is unprimed

The ADDSP V.90 Guide's write-database map identifies `DM(0x3fbc..0x3fbf)` as
`Nearbulklength`, `BulkLength`, `BulkInputX`, and `BulkInputY`. PM
`0x1900..0x19c8` is the matching near/far echo bulk-delay adapter, not a state
image decoder. The identical routine is present at the same PM addresses in
the V.34 overlay. PM `0x19a7` detects bulk-configuration changes, PM `0x1982`
reconfigures its eight-word descriptor at DM zero, and PM `0x1900` services it.

This explains the zero source without requiring a missing producer. At PM
`0x190f` the adapter reads the cursor-selected DM word, but PM `0x1910..0x1914`
replaces it with zero while the cursor is outside the configured bulk
interval. PM `0x1930` then stores that deliberate zero. The watched low-DM
source happened to retain nonzero INFO-page data at V90D entry; its contents
were suppressed by the bounds check, not absent.

The descriptor exposes the actual seam. When state `0x60` activates the
adapter, run21 has:

```text
DM3fbc/DM3fbd = 1d77 / 0ae0
DM0..DM7       = 1d77 2ad2 0001 0001 0001 0000 0000 0001
```

DM4 is the far-bulk cursor. The portable V90D DM block explicitly initializes
it to zero, PM `0x1982` preserves that relative cursor, and the first service
increments it to one. Starting from one makes the zero-fill destination reach
`DM(0x1938)` before state `0x72`'s dwell expires.

A diagnostic replay primed only `DM4=DM0` on the first state-`0x60` sample.
No codec, record, countdown, or input value was changed. The previously
missing sequence immediately appeared:

```text
0060 -> 0062 -> 0064 -> 0066 -> 0068 -> 006a -> 0070 -> 0072 -> 0074
```

Without the prime, the same run goes `0066 -> 0072`, zeroes the pending
records, and remains there. Priming DM2 as well is unnecessary. This is causal
proof that the failure is the unpublished initial far-bulk cursor, not
`DM(0x2004)`, G.711, record-applier ordering, or the dwell implementation.
The primed open-loop replay later remains at `0x74`; by then its changed output
can no longer be expected to match the captured peer response, so that is not
evidence against the cursor seam.

`--prime-v90d-bulk-cursor` is retained as an explicit native-MIPS diagnostic.
It primes DM4 from DM0 once when V90D state `0x60` activates; it is not enabled
by default.

## Session 59: closed-loop run25 proves the missing cursor publication

Tower call `artifacts/eicon-native-tower/run25` enabled only
`--prime-v90d-bulk-cursor`. At state `0x60` the native seam published
`DM4=DM0=0x1e17`. The live peer—not an open-loop replay—then followed:

```text
0060 -> 0062 -> 0064 -> 0066 -> 0068 -> 006a -> 0070
     -> 0072 -> 0074 -> 0076 -> 0078 -> 007a
```

The unprimed run21 went `0066 -> 0072` and lost its pending records. Run25
therefore confirms in closed loop that DM4's zero origin is the missing
publication. It also disproves the earlier claim that `0x68`, `0x6a`, and
`0x70` were peer-dependent branches: they were simply among the records
clobbered by the bulk adapter.

Run25 stops at state `0x7a`, whose pretest condition `0x18` at PM
`0x3019..0x3038` requires both:

```text
(DM(0x206d) & 0x000f) == 0x000f
(DM(0x206e) & 0xfffc) == 0xfff8
```

That is the next receive-result boundary. It is downstream of the now-proven
bulk-cursor seam and should be traced separately; the peer disconnects before
publishing a matching result. The cursor prime remains diagnostic until its
correct owner—the omitted selected-channel continuation versus a generic
kernel initialization—is encoded without patching overlay-private DM from the
SIP layer.

## Session 60: state `0x7a` is the Ja receive gate, so silence is still correct

The tower log for run25 confirms the peer sequence explicitly:

```text
TRNSEG4 -> JTXMIT -> JaTXMIT
V90Phase3Demodulator: initial state set to WaitForSd
```

Our RTP output is PCMU `0xff` throughout the corresponding V90D window. That
matches V.90 §9.3.1.3 up to the failed receive decision: after the first 512T
of TRN, the digital modem conditions its receiver for Ja; only **after
receiving Ja** may it wait up to 500 ms and then transmit Sd for 384T followed
by Sd-bar for 48T. Sending Sd before the state-`0x7a` condition succeeds would
hide the receive fault and violate the specified ordering.

A cursor-primed run25 replay shows why condition `0x18` never succeeds. Its
32-bit detector result becomes `DM206d/DM206e = 0x1ab2/0xa604` and then freezes,
while the required masked value is `0x000f/0xfff8`. The bit input `DM2055`
remains zero. PM `0x2eac..0x2ecb` derives that bit from the receiver history
and PM `0x0ca6..0x0caf` shifts it into `DM206d/DM206e`; the scheduler itself is
working.

PM `0x19fd` copies the page's in-place scalar `DM3fa7` into `BulkInputX` before
the page reuses that slot. As a negative test, publishing the raw expanded
SPORT sample directly at `DM3fa7` made the detector registers move, but a live
run stalled back at state `0x60`. Therefore `DM3fa7` is not another raw-linear
SPORT inlet; it is a processed scalar owned by the selected-channel/V.34
receiver continuation. That experiment was reverted.

At this point the likely boundary was the selected-channel continuation, but
the direct runtime trace in Session 61 disproved that ownership hypothesis.
The downstream serializer should still remain silent until the receive
boundary passes.

## Session 61: ADSP-2185N runtime proves the V.34 core receives the SPORT sample

The hardware part is an ADSP-2185N, not literally an ADSP-2181. `TIKRNL81` and
`DSP_DOWNLOAD_FLAG_2181` identify the compatible ADSP-2181-family firmware ABI;
they do not identify the exact fitted part. The computational instruction set
used here is compatible, but SPORT/autobuffer assumptions must be checked
against the 2185N hardware rather than inferred from the emulator's name.

A dump of the fully relocated runtime code resolves the selected-channel path:

```text
PM 02b7: SR1 = DM(I5,M4)
PM 02b9: CALL 0703
PM 0703: SE = DM(313f)
PM 0704: SR = LSHIFT SR1 (HI)
PM 0771: CALL (DM(3fb3))       ; page Core8kRoutine
```

Coverage at V90D state `0x60` was 127087 executions of PM `0x0703` for 127079
media samples—the eight-call difference is setup—so the native foreground
runs once per selected 8 kHz sample. Execution-register tracing then showed
the expected one-sample pipeline exactly. For a diagnostic PCMU octet `0x80`,
SPORT RX0 and the next PM `0x0703` invocation both held signed-linear
`0x7d7c`. Thus the Ja failure is **not** caused by an omitted callback or by
feeding V90D from the wrong raw-sample address.

The ADDSP guide's write-database table also corrects the interpretation of
`DM3fbe/DM3fbf`: `BulkInputX/Y` are samples the **V.34 modemCore offers the
kernel at symbol rate** for the echo delay line. PM `0x19fd` copies the core's
in-place result at `DM3fa7` to `BulkInputX`. They are not receiver inputs;
zeros while the digital transmitter is deliberately silent are expected.
This also explains why forcing raw SPORT PCM into `DM3fa7` damaged state
`0x60`.

Two hardware-boundary alternatives were tested and rejected:

- running the call as PCMA because slmodemd reports `pcmType = A_LAW` made the
  outer sequencer oscillate between `0x50` and `0x52`; it did not repair Ja;
- presenting 32 SPORT interrupts per media sample to model a complete PRI TDM
  frame prevented INFO completion. The native MIPS assignment has already
  selected one B-channel before this task, so this firmware instance correctly
  receives one 8 kHz scalar per call.

The investigation boundary is now inside the live V.34 upstream receiver:
trace `DM11e8/DM11e9 -> DM2055 -> DM206d/DM206e` over Ja, compare its symbol
and bit cadence with the Ja descriptor in V.90 §8.3.1, and find the first
receiver stage whose decisions differ. No SPORT, codec, or bulk-input shim
should be added at this point.

## Session 62: ADSP-2185N manuals and the first Ja decision trace

The added Analog Devices references materially tighten the hardware model:

- `docs/ADSP-218XN_SERIES.pdf` identifies the ADSP-2185N as 16K-word PM plus
  16K-word DM and confirms the ADSP-2100-family instruction compatibility;
- `docs/3110043388x_hardware/8xsports.pdf` §5 confirms that companded RX0 is a
  right-justified, sign-extended linear value before the receive interrupt;
- its interrupt table gives SPORT0 TX/RX vectors `0x0010/0x0014`, matching the
  emulator's priority-to-vector mapping;
- `docs/3110043388x_hardware/8xcompu.pdf` §2 states that computational and
  register operands are read at the beginning of a cycle and results/data
  moves are written at the end. This supports simultaneous old-value semantics
  for multifunction instructions rather than sequential C-side updates.

The SPORT chapter describes µ-law and A-law as having 14-bit and 13-bit
maximums. A diagnostic interpretation that divided conventional int16 G.711
expansion by 4/8 was tested, but live run28 stopped receiving symbol events in
state `0x74`; it did not detect Ja. The wording describes the algorithms'
effective precision and saturation limits, not sufficient evidence for adding
an extra gain change at the reconstructed boundary. The divide was reverted.

A symbol-cadence run25 replay now captures the complete live chain. At state
`0x7a`, `DM11e9` publishes quadrant/dibit values and PM `0x2eac..0x2ecb`
publishes `DM2055` in the expected top-two-bit forms `0000/4000/8000/c000`.
PM `0x0ca6..0x0caf` shifts two bits per event into `DM206d/DM206e`. The rolling
register moves for 231 samples and settles at:

```text
DM206d/DM206e = 1ab2/a604
```

It never contains the frame-sync condition selected by outer precondition
`0x18`:

```text
(DM206d & 000f) == 000f
(DM206e & fffc) == fff8
```

V.90 §8.3.1 defines the start of every repeated Ja DIL descriptor as 17 one
bits followed by a zero start bit. Therefore the remaining failure is no
longer merely “no receive publication”: the live receiver produces dibits,
but its descrambled stream never publishes the Ja sync run. The next trace
was then split across PM `0x2eac`: `DM11e9`, the raw aligned word at
`DM0ee6`, histories `DM2067/DM2068`, and output `DM2055` all advance. The
history shifts continuously and agrees with the raw decision stream; the
output remains non-sync after far more than the 23-bit self-synchronization
length. Trying the four dibit inversion combinations and both within-dibit
orders against the GPA `x^-5/x^-23` recurrence produced no 17-one run (the
longest was nine). Setting `DM1fea bit 15` to select PM `0x2e99` was also
rejected: it changed the receiver mode broadly and still produced no sync.

The first wrong boundary is therefore before GPA descrambling, in the V.34
carrier/equalizer/differential-decision path that produces `DM11e9`. The most
useful next comparison is the trained V.34 receiver state immediately before
and after the INFO-to-V90D overlay load: if the segmented loader overwrites or
fails to retain equalizer/carrier state, Ja will look exactly like the observed
random but cadence-correct dibits.

## Session 63: the native loader retains the sparse V.34 handoff state

A run25 replay snapshotted all 16K DM words immediately before and immediately
after the native MIPS loader transferred overlay `0x026a`. The page has 8,098
explicit DM words; 7,963 runtime words changed and 616 non-zero INFO values
became zero. Those headline counts initially look destructive, but matching
them against `026a`'s actual block map shows the opposite: every change is in
an explicit V90D load block, while the sparse gaps are left untouched.

In the receiver/state range `0x0d00..0x21ff`, 365 non-zero words survive
bit-exact in unloaded gaps. The largest handoff islands include:

```text
10c1..10ca   10 words
1d8e..1dcf   66 words
1df2..1e5c  107 words (with small explicit block holes)
2094..2133  160 words
```

The corresponding addresses in the portable INFO flat image are zero, proving
these are runtime/inherited values rather than constants reloaded from INFO.
V90D directly references several islands—for example PM `0x0ad5`, `0x1025`,
`0x107b`, `0x26a9`, `0x26ae` and `0x3e78` use the retained `DM1e00` region.
Thus the segmented native loader is preserving the intended cross-page V.34
state, and broad loader zeroing is not the source of the random Ja decisions.

The remaining comparison must move one stage earlier: record the V.34
carrier/equalizer outputs and phase decisions on both sides of the page seam,
then audit the exact ADSP-2185N shifter/MAC instructions on the first path that
diverges. The added computational-unit manual is now the authority for that
audit, especially old-value multifunction semantics, fractional MAC placement
and EXP/NORM behavior.

## Session 64: `1ab2/a604` is the exact differential-decoded TRN-tail signature

Run30 finally reproduced the run25 path with symbol-cadence logging in the live
call. It reached state `0x7a` and produced exactly the same terminal detector
word as the offline replay. The additional fields change the interpretation
completely.

The equalizer outputs `DM11f5/DM11f6` form four tight clusters:

```text
phase index  centroid angle
0             +44.95 degrees
1            -135.91 degrees
2             -45.93 degrees
3            +135.07 degrees
```

Residual phase error remains within about +/-11 degrees and magnitude is
stable. `DM11eb` maps the sliced phase to the V.34 clockwise rotation index,
and `DM11e9` agrees with modulo-4 differential decoding on 89 of 90 transitions
(the first value is inherited). PM `0x2eac` then agrees bit-for-bit with GPA
`1 + x^-5 + x^-23` descrambling after its 23-bit history fills. The carrier,
equalizer, slicer, differential decoder and descrambler are all operating
correctly for the symbols they receive.

The supposedly random terminal word can be reproduced independently. Generate
GPA-scrambled binary ones as V.34 TRN (§10.1.3.8), map them directly to the
four-point constellation (TRN is *not* differential encoded), then feed those
phase indices through the Ja-mode differential decoder and GPA descrambler.
The rolling 32-bit register becomes exactly:

```text
DM206d/DM206e = 1ab2/a604
```

at TRN symbol 590. No other bit-order or polarity adjustment is needed. Thus
state `0x78/0x7a` is seeing the deterministic tail of TRN, not corrupt Ja data.
It publishes 91 symbols and then all of `DM11f5/DM11f6`, `DM11e8`, `DM11e9`
and the result register freeze at the TRN-to-Ja boundary, although SPORT input
and the 8 kHz foreground continue.

This corrects the Session 62 conclusion: the first wrong boundary is not a bad
V.34 decision. It is the missing **TRN-to-Ja receive continuation**. The DSP
successfully arms Ja mode near TRN symbol 500, consumes the remaining TRN tail,
but does not resume symbol publication for Ja. That is exactly why the remote
stays in `JaTXMIT/WaitForSd` while the digital side remains silent.

The next trace target is the timing/symbol-strobe gate feeding `DM11f5/DM11f6`
at the moment the result freezes (around live sample 136017), especially the
state selected by `DM1fec=0x4060`, `DM1fed=0x331c`, `DM1ff1=1` and the callback
at `DM2035`. Do not alter differential decoding, GPA taps or the equalizer.

## Session 65: the apparent TRN-to-Ja freeze is the delayed bulk-cursor collision

Instruction coverage on the two sides of the final `DM11f5/DM11f6` update
showed that this was not a symbol-strobe branch inside the V.34 receiver. The
entire V.34 foreground disappears. Before the freeze, PM `0x19e1` and the
receiver chain run once per 8 kHz or symbol event as appropriate; afterwards
they execute only twice while the already-dispatched calls drain.

A write watch identifies the destructive instruction exactly. At replay sample
130257 PM `0x1930` zeroes `DM3fad`; one sample later it zeroes `DM3fb3`:

```text
PM 1930: DM(I0,M1) = SR0
I0 = 3fad, then 3fb3
DM3fb3: 19e1 -> 0000
```

`DM3fb3` is the ADDSP write-database `Core8kRoutine` callback. PM `0x076d`
loads it and PM `0x0771` invokes it once per selected-channel sample. Once the
bulk adapter replaces it with zero, the modem core is no longer called, so the
equalizer and Ja detector necessarily freeze. SPORT and the kernel foreground
continue exactly as observed.

This is the original Session 58 collision returning later, not yet a distinct
Ja transition defect. Priming `DM4=DM0` merely moves the far-bulk cursor from
zero to the near-bulk cursor. It delays the zero-fill sweep long enough to
reach state `0x7a`, but does not put the cursor in a valid far-bulk interval.
The descriptor workspace at page entry is:

```text
DM0..DM11 = 2aca 2ad2 2ae5 2b1b 0000 0000 0000 0000
            2ac7 2ad2 2ae0 2b1b
DM32f7    = 0000
```

A subsequent selector watch corrects an important labeling error: these are
not two independently selected descriptors. PM `0x1900` and PM `0x1982` load
I1 from `DM32f7`; that selector remains zero throughout the failure and has no
DSP writer. Consequently both routines operate on the overlapping workspace
starting at DM zero. PM `0x1900` reaches offsets zero through seven, including
both the cursor at `DM0` and the second mutable pointer/state word at `DM4`;
under the observed selector it does not consume `DM8..DM11` as “far bounds.”

When state `0x60` reconfigures the workspace, PM `0x1982` itself deliberately
writes `DM4=0`; PM `0x1900` then advances it to one. The one-shot diagnostic
replaces that firmware-generated state with the unrelated `DM0` cursor. It
subsequently advances to `0x2a85`; PM `0x1930`'s delayed destination then
reaches `DM3fb3` and kills `Core8kRoutine`. The terminal `1ab2/a604` word is
still proven to be a deterministic TRN-tail signature, but its being the final
word is explained by this memory collision rather than by a demonstrated
TRN-to-Ja receiver-mode branch.

The next target is therefore the **PM `0x1982` bulk-workspace calculation**,
not `DM2035` or the slicer. Trace how `DM3fbc/DM3fbd`
(`Nearbulklength/BulkLength`), the RX sample/database workspace, `DM32f7`, and
ADSP carry/NORM semantics produce `DM0..DM7`. The evidence no longer supports
calling `DM4` an unpublished standalone cursor: zero is generated by executed
firmware, so either an input/selected-channel handoff is missing or one of the
calculation's ADSP semantics is wrong. Keep `--prime-v90d-bulk-cursor`
diagnostic-only; it is temporarily useful but eventually destructive.

## Session 66: PM `0x1900` exposes the missing retained workspace words

The unprimed collision was captured at the exact service invocation. When PM
`0x1930` first overwrites the state record at `DM1938`, the workspace is:

```text
DM0..DM7 = 1e17 2ad2 040a 0001 040a 0000 0000 0001
```

The executed path is `0x1900..0x191c, 0x192c..0x1930`. PM `0x190b` advances
`DM2`; PM `0x190d` reads the prior `DM4`; and PM `0x1917` subtracts the upper
workspace value `DM1=0x2ad2`. The old-value multifunction at PM `0x191a`
correctly copies that negative result (`0xd938`) into AX0 while clearing AR.
Loading the 14-bit DAG register at PM `0x192e` therefore produces I0=`0x1938`,
and PM `0x1930` stores the deliberate zero there. Carry, 14-bit DAG masking
and same-cycle old-value behavior all agree with the ADSP-2185N manuals for
this invocation; no emulator arithmetic discrepancy has yet been found.

A stronger lifecycle clue is in the segmented images. Both V.34 page `0x0261`
and V90D page `0x026a` explicitly load DM `0..4` and `8..12`, but leave
`DM5..7` sparse. Those three workspace words are designed to survive from a
common/kernel initializer. In the forced native path they are zero before the
V90D load and remain zero when PM `0x1982` runs. PM `0x1982` consequently
regenerates `DM2=0` and preserves the zero second-pointer state rather than
installing a bounded bulk interval.

The next comparison should therefore be a normal V.34/common-page startup—or
the omitted selected-channel initializer—at the writers of `DM5..7`. An
emulator defect remains possible, especially in the PM `0x1900` carry paths,
but simply changing carry or old-value semantics would contradict both the
manual and the captured instruction-level data. The first unexplained input is
the absent retained workspace state, not the resulting subtraction.

## Session 67: the missing owner is above the ADSP page, in call ingress/activation

The Linux and early-MIPS paths narrow the ownership boundary further. Linux
`drivers/isdn/hardware/eicon/message.c` (`connect_res()` and `add_b1()` in the
v4.0 tree) only builds the modem CAI—resource, framing, negotiation options and
speed limits—and attaches it to `CALL_RES`. It does not calculate or publish
ADDSP near/far bulk workspace words. Those are card-firmware/task activation
state, not Linux modem parameters.

The real MIPS `SERVICE_ASSIGN`/`SWITCH_ON` path was then stopped before the
SIP adapter's `attach_connected_bearer()` synthesis. At that point the modem
DSP has consumed its genuine TIKRNL assignment command, but has no active page:

```text
resident/page = 0000
DM2f86/2f87  = 3110/3108
DM32f0..32f7 = all zero
DM0..DM11    = 0000 0000 3150 000a 0000 ...
```

The initial write database left by `SWITCH_ON` is also materially different
from the two hand-built WDBs in `attach_connected_bearer()`:

```text
DM3ee0.. = 0040 0024 0038 0008 0000 0000 2105 f1fd
            000c 000c 00b8 0033 0003 0000 2000 abcd ...
```

`attach_connected_bearer()` therefore is not yet “the exact result” of the
SIG.MDM bearer-connected notification, despite its old comment. It manually
loads DIAL-related pages, writes a generic ADDSP Table 12/13/15 database, calls
PM `0x0581/0x13cc`, and later `_frame_core()` manually resumes the selected
continuation. This is precisely the layer capable of omitting private
selected-channel/bulk initialization.

A write-PC trace corrects the initial interpretation of the synthetic call
link. Event `0x17` allocates a staging object at `0x8028fda0`; SETUP clears the
controller's `+0x1c` at MIPS `0x8001855c`, in the delay slot of the successful
transfer path after `0x8002a89c` accepts the call. That clear is intentional,
not evidence that allocation failed. CALL_IND channel zero is also valid for
the first call. The missing event is later: an answering `CALL_RES` is not the
line-side connected indication.

Injecting lower-PRI event `0x03` immediately after successful `CALL_RES`
produces the previously absent native effects:

```text
CONNECT_ACTIVE indication: IND 0x01, Id 0x02, Ch 0
network indication:        IND 0x03, Id 0x03, Ch 2
MIPS host writes:          2274 -> 3814
modem DM2e58:              0000 -> 0277
modem DM2f08:              0000 -> 8000
```

The resident kernel compares `DM2f08` with `DM2f09` at PM `0x02b3..0x02b6`
and calls PM `0x01c1` to install the selected task vectors when they differ.
This is the genuine early-firmware selected-bearer seam that the compatibility
path omitted. It cannot simply be combined with `attach_connected_bearer()`:
doing so double-activates the task and its first synthetic WDB is no longer
consumed (`DM3131=0x000d`).

`--native-bearer-activation` now exposes this path diagnostically. It delivers
event `0x03` and disables the compatibility DIAL/WDB synthesis. The resulting
core intentionally remains at TIKRNL page `0x0258`, requesting SIG overlay
`0x0270`, until the remaining native supervisor/page-service continuation is
reconstructed. This is progress rather than a working media mode: it preserves
the real activation state so the next trace can follow PM `0x01c1`, the
`0x0270` request and the native WDB owner without overwriting them with the
hand-built DIAL setup.

Driving one media frame through that preserved state exposes the next missing
owner. Event `0x03` sets `DM2f08=0x8000`, and resident PM `0x02b3..0x02b6`
correctly notices that it differs from `DM2f09`. But the kernel dispatch-list
roots are still absent:

```text
DM2f27/DM2f28/DM2f29 = 0000/0000/0000
```

PM `0x01c1` therefore follows a null task-vector list and execution reaches PM
zero. The compatibility sample path hid this by patching PM `0x00b5` and
resuming TIKRNL directly at PM `0x06c8`; native activation now deliberately
does neither, so the missing early state is visible. The next MIPS/TIKRNL
target is the post-`SWITCH_ON` command consumer that should populate
`DM2f27..DM2f29` before the first selected-channel interrupt. This is earlier
than any V.90 overlay or bulk-delay calculation.

A comparison across every emulated DSP localised why those roots were absent.
The modem assignment selects block `0x1c000808`, the same exceptional core
reported at boot as held without a resident download. Its sparse TIKRNL task
is present around PM `0x0580`, but its resident interrupt and scheduler words
are still parking instructions:

```text
selected 0x1c000808: PM0014/0072/00b5 = 18000f/18000f/18000f (JUMP 0)
other active cores:  PM0014/0072/00b5 = 18072f/3c0611/2a7eea
```

The `kernel` argument to `create_native_mips_modem()` was unused. Thus this
was not yet evidence of a missing firmware write to `DM2f27`: there was no
resident kernel capable of receiving the first SPORT/IRQE event. The TIKRNL
image is genuinely sparse (its PM blocks begin at `0x0580` and `0x1800`), so
loading it cannot supply the resident vectors.

The earlier `SWITCH_ON` hook was still one lifecycle step too late. At
`SERVICE_ASSIGN`, the service object's `+0` pointer leads to the task object,
whose `+0x10` word is the DSP register base `0xbc000808`. The return address of
`SERVICE_ASSIGN` is therefore the first safe seam where all sparse task blocks
exist but the MIPS has not yet published its task commands. Native activation
now composes and initializes the selected core there, before the next DSP pump.
Both kernel and TIKRNL are restored from their declared PM blocks only; loading
a flattened 16K PM image erased the live command ring at PM `0x3327`.

The first selected-channel pipeline then behaves normally after its two-sample
SPORT delay:

```text
frame 0: DM2f08/DM2f09 = 8000/0000
frame 2: DM2f08/DM2f09 = 8000/8000
         DM2f27..DM2f29 = 2f21/2f00/2f0e
```

Tracing the task drivers identifies the real connected task path. Lower-PRI
event `0x03` dispatches service driver `0x80098310`; it verifies task ID
`0x0258`, describes the two native transfers as:

```text
KERNEL:   DSP address 0x6e68, 0x0010 words
DATABASE: DSP address 0x7ee0, 0x0100 words
```

and then calls `0x80097f60` plus `0x80095318`. The latter polls the DSP for
completion. On the physical card PRI SPORT continues clocking while that MIPS
poll runs. The shim instead pumped masked IRQE and supplied no selected-channel
clocks, so `0x80095318` timed out and its cleanup path removed the task.

Native activation now supplies a bounded pre-media SPORT window while phase
`call-ingress-connected` is executing, using the registered PM `0x0586` ISR
and PM `0x06c8` continuation. Four setup clocks are sufficient to change
the native task marker without fabricating any RTP samples:

```text
DM3137: 0000 -> 0001
DM3131/DM3132: 000d/0270
```

That makes the initial SIG request live. The page supervisor loads `0x0270`
once and resumes PM `0x06df`; unlike the old unconditional interpretation, it
does not repeatedly reload the static `000d/0270` image pair.

The remaining task boundary is now after SIG-page attachment: PM `0x0586` and
PM `0x06c8` execute, but PM `0x0703` is not yet reached, no DIAL successor is
requested, and the native network task eventually emits IND `0x04`. The next
trace belongs in the `0x80098310 -> 0x80095318` completion state and SIG
`0x0270` handoff, not in V.90 bulk workspace or kernel free-list code.

## Session 60: relocated native task attachment and first V90D transmit

The apparent post-SIG blocker was caused by restoring the wrong task image.
`SERVICE_ASSIGN` calls `SWITCH_ON` before it returns. The old hook initialized
and marked the task at `SWITCH_ON`, so the intended deferred return hook became
a no-op. It also copied the extracted source-address TIKRNL PM over the MIPS
loader's relocated image. The naturally completed download proves that no PM
composition is needed:

```text
PM0014 = 18072f                 resident kernel vector
PM0580 = 0a000f
PM0586 = 18589f                 selected-channel ISR entry
PM06a0 = 40703a                 relocated continuation address 0703
PM0703 = 8313f9                 source PM06fc after the +7 relocation
```

The `SWITCH_ON` initializer was removed. The selected core is released and
runtime PM `0x0679` is called only after `SERVICE_ASSIGN` returns, preserving
the genuine relocated kernel/task image. The bounded PRI setup-clock window
also starts at MIPS `0x800951d4`, where the connected driver actually publishes
its control toggle, rather than at entry to the much earlier event-03 driver.
Three clocks are consumed before the MIPS sees completion:

```text
host writes: 3814 timeout -> 2281 success
DM2f08/DM2f09: 8000/8000
```

Event `0x03` remains the sole TIKRNL attachment owner. The subsequent helper
only completes the documented ADDSP answer transaction: common pages plus the
two Table 12/13/15 WDB cycles already required by Sessions 38-42. It does not
run another task initializer or publish another kernel descriptor. After page
downloads replace the private kernel records, media uses the established
one-call PM `0x06c8` selected-channel adapter, preserving one continuation per
8 kHz sample.

The final V90D corruption was then reduced to one exact retained-publication
seam. PM `0x1982` builds the overlapping `DM0..DM7` bulk descriptor. Its
same-cycle old-value sequence preserves the incoming far cursor in `DM4` and
consumes the corresponding bound in `DM6`. The INFO-to-V90D handoff left both
zero. PM `0x19c6` is the first point where the newly allocated far-buffer base
is available in `DM3fbc`; publishing it to `DM4` and `DM6` immediately before
calling PM `0x1982` produces:

```text
DM0..DM7 = 1e17/2ad2/0001/0001/1e17/0000/1e17/0001
```

This is not the destructive diagnostic `DM4=DM0` applied after setup. It
models the missing common-layer retained publication at the consumer seam.
The PM `0x1900` sweep no longer reaches `DM1938` or `Core8kRoutine`.

A native-MIPS replay of `run25.rx.ulaw` now reaches the Ja gate, detects Ja,
and transmits only after entering outer state `0x0080`:

```text
page 0x026a: sample 127059
outer state 0x007a: before sample 140000
first nonzero V90D TX: sample 145852, value 4, outer state 0x0080
final outer state: 0x00b0
TX datagrams: 10157/10157 accepted/requested
```

This preserves V.90 §9.3.1.3 silence through state `0x007a`; the first output
is after Ja, not a forced Sd. Reproduce it with:

```bash
/tmp/eicon-venv/bin/python tools/v90_dpcm_replay.py \
  artifacts/eicon-native-tower/run25.rx.ulaw \
  --native-bearer-activation --tx-prbs --to 23.5
```

The result is an offline hardware-capture replay. A fresh tower call is still
required to validate the same retained publication and Sd/S-bar-d stream
against the live analogue modem.

## Session 61: run31 hardware test disproves the DM3fbc cursor bridge

A fresh closed-loop call was run against tower `slmodemd_trnref` with native
event-03 attachment, PRBS TX service and the Session 60 PM19c6 bridge. The
capture is `artifacts/eicon-native-tower/run31.*`. Native activation itself was
successful (`host_writes=2281`, `DM2f08/DM2f09=8000/8000`), and the call
naturally completed V.8 and INFO before loading V90D at sample 86528.

The retained-workspace conclusion from offline run25 was wrong. In run25,
`DM3fbc=0x1e17` happened to be a plausible resident-DM address. On the live
call it was `0x0be9`; it is capture-dependent bulk input, not an allocated
far-delay cursor. Publishing it into `DM4` and `DM6` produced:

```text
sample 86542: DM0..DM7 = 0be9/2ad2/0001/0001/0bea/0000/0be9/0001
sample 86566: descriptor words begin changing to ab3d
sample 87360: bootpage 14 -> 0, followed by nonsensical page/status values
```

The peer reported `NO CARRIER` and hung up after about 15.7 seconds. V90D did
not reach a valid `0x007a -> 0x0080` transition. Supplying a TX datagram at
sample 86529 only serviced `DI_control`; it is not evidence that valid Sd was
transmitted.

The PM19c6/DM3fbc bridge has therefore been removed from the default path. The
native task-relocation and event-03 fixes remain valid, but the retained
ADDSP delay workspace owner is still unresolved. In particular, the correct
`DM4..DM6` values must come from the common-layer allocation lifecycle; they
must not be inferred from `DM3fbc`, copied from a V.32 snapshot, or repaired
after PM1982. Until that owner is recovered, the hardware result supersedes
Session 60's offline transmit claim.

## Session 68: the downstream samples reach the line, and Phase 4 begins

Sessions 60 and 61 left page 14 reaching outer state `0x0072` at best, with the
`0x007a -> 0x0080` transition never observed and no valid downstream transmit.
Two defects between the transmit generator and the line are now fixed, the card
transmits genuine Phase 3 and Phase 4 signals, and a live Courier call reaches
outer state `0x00d0`.

### The generator was never the problem; the block's lifetime was

`tools/v90_dpcm_vector_trace.py` (added this session) traces the six-word
mapping-frame block with the core's DM-write and PM-execution watches. Per
8 kHz frame on page 14:

```text
PM 0x19ee   DM(0x3fb4) = 0x3764              generic pointer re-published
PM 0x2a52   CALL (I4)  with AX0 = 0x3fa7     generator dispatch, 0.167/frame
PM 0x2eef   MX0 = DM(I0)                     serializer reads one slot
PM 0x2ef1   DM(0x3fa7) = <slot, scaled >>2>
PM 0x1a1e   DM(0x3fb4) = DM(0x3fa7)          the sample leaves here
PM 0x07c1   reads DM(0x3fb4)
PM 0x06cd   DM(0x3fa7..0x3fac) = 0  x6       resident kernel frame tail
```

`DM(0x3fa7..0x3fac)` is the V.90 mapping frame, six DS0 samples. The generator
fills it once per 1333 Hz mapping frame — correctly, 8000/6 — and the serializer
walks cursor `DM(0x20de)` across it one slot per 8 kHz frame, so the block must
survive six frames. The resident kernel's frame tail zeroed all six words every
frame, measured at 6.000 executions of PM `0x06cd` per frame against the
generator's 0.167. Five of every six downstream samples were lost, producing the
one-nonzero-in-six impulse train seen on the wire in run33.

PM `0x2a52` is `0x0B001F`, an unconditional `CALL (I4)`: a dispatch table, not a
gate. I4 held `0x3db9` (the Session 54 vector copy) for the first 64 mapping
frames and `0x2199` thereafter. Session 54's "the copy never runs" reading was
really "the copy is one of at least two generators".

Second, `frame_fast()` read `DM(0x3fb4)` as a pointer and returned
`DM[DM(0x3fb4)]`. That is right for the earlier pages, where PM `0x19ee`
publishes `0x3764`, but on page 14 PM `0x1a1e` overwrites it with the sample
itself, and nothing writes `DM(0x3764)` at all while V90D transmits — zero
accesses over a 40-frame watch. Each surviving sample was being turned into
whatever unrelated word lived at its own numeric value.

With both fixed, replaying run33 takes the page-14 output from 2045 to 12125
nonzero samples of 31050, and the first 48 ms carry

```text
1919, 0, 1919, -1919, 0, -1919   repeating on the six-sample mapping frame
```

which is exactly the Sd of §9.3.1.3: 64 repetitions of `{+W, +0, +W, -W, -0, -W}`.
The shape and period are per spec.

### The block clear also publishes silence

Suppressing that clear outright is wrong in the other direction. When the
generator stops, the serializer re-emits the last mapping frame for ever: run35
froze on codeword 148 (linear +13948, near full scale) for 7.02 s and codeword
200 (linear +1372) for 8.64 s, both from the instant the state machine reached
`0x00b3`.

The first correction cleared the block at every cursor wrap, which would have
been worse. Phase 4 opens with Ri, "signal R using the single PCM codeword whose
Ucode is `UINFO` for all data frame intervals" (§9.4.1.1, sent for a minimum of
192T while the receiver is conditioned for CPt) — a constant block is a
legitimate transmitted signal and is indistinguishable from a stale one by
inspection. Clearing on content would replace Ri with silence.

The core can answer the question that content cannot: read the execution count
of PM `0x2a52` each frame and clear only after the generator has produced
nothing for two mapping frames, logging sample and outer state when it does.
A live call then reports whether the generator actually stops.

### run34: the first peer-side measurement of our downstream

Tower `slmodemd`, `artifacts/eicon-native-tower/run34.*`. The card's state
timeline matched the offline replay exactly (`0x007a` at 10.22 s, Ja detected
into `0x007b` at 11.92 s, `0x0080` at 12.20 s, `0x00b0` at 16.00 s) and TX came
up at 12.18 s.

```text
                            run33            run34
Error Energy during TX      -0.000           +117 .. +162 (first +1600)
Phase 3 held after Ja       1.94 s           8.12 s
Timing Offset               -0.000           +4750 .. +4884 ppm
```

The peer's first nonzero Error Energy is at its 794.945 and our TX starts at our
12.18 s — the same instant under the 782.77 s clock offset. The 1.94 s in run33
is the §9.3.2.4 deadline (retrain if the Sd-to-S̄d transition is not seen within
1500 ms of Ja); that deadline no longer fires.

### The +4800 ppm is in the signal, not the sample accounting

`tools/rtp_pcap_timing.py` (added) decodes the capture properly. `RtpCapture`
writes **LINKTYPE_RAW** (101), bare IP with no Ethernet header, so parsing it as
Ethernet produces plausible garbage — constant 146-byte payloads and nonsense
SSRCs — rather than an error. Both directions of run34 are clean 160-byte PCMU
with zero sequence gaps and zero timestamp jumps, reading 8012.84 Hz outbound
against 8010.37 Hz inbound on our host's one wall clock. The shared ~1500 ppm is
a stamping bias; the two media clocks differ from each other by +308 ppm.

Our own accounting is exact: the cursor advances +1 per frame with a -5 wrap
every sixth (32391/6478 over the transmit era), and PM `0x2a52` executes 6479
times in 38870 frames against 6478.33 for an exact 1333 Hz mapping frame — one
single-frame anomaly in the whole era, 26 ppm.

The peer's estimator is trustworthy: on this same rig, locked to our own
sample-exact C implementation, it reports ±0.04 to ±7 ppm (`20260723T045437Z`
shows +0.037, +0.038, +0.041 sustained).

**Methodology warning.** A first version of the refill audit reported ~4% missed
refills and was wrong. It detected refills by watching the block contents change,
which cannot distinguish a skipped refill from a refill that writes the same six
values; with a small symbol alphabet, chance collisions alone produce that rate.
Execution counts are the sound measure. Do not infer generator activity from
block contents.

What remains is content. The spec defines Sd's W as the PCM *codeword* whose
Ucode is `16 + UINFO`, while the card publishes linear values that the harness
mu-law encodes, and that encoding moves them: 943 -> 924 and 1919 -> 1980 on the
wire. Ri's codeword is Ucode `UINFO`, so Ri must be quieter than Sd's W. Measured
against run35: call 3's post-DIL constant is 1372 against Sd's 1980, plausible;
call 2's is 13948, seven times too loud. Whether `DM(0x3fb4)` is a codeword
rather than a linear sample, and whether the resulting Ucode matches `UINFO`, is
open — and it subsumes the serializer's `>>2`, which as a linear scale puts the
line at -20.7 dBm0 and would be meaningless as a codeword.

### Courier: DIL, DSR, and Phase 4 to `0x00d0`

Six live Courier calls, `run35.*` (three) and `run37.*` (three). All six reach

```text
 8.7 s   0x007b -> 0x0080        Sd/TRN1d/Jd, then DIL
12.5 s   0x0080 -> 0x00b0
12.7 s   0x00b0 -> 0x00b1        Rstatus_ch = 0x8200 [change_h|DSR]
12.9 s   0x00b1 -> 0x00b2
14.6 s   0x00b2 -> 0x00b3
```

DSR asserting at `0x00b1` is new. The DIL era is audible and visible as the
stepped TX levels through the `0x0080` dwell.

Five of the six stop at `0x00b3` with the generator genuinely idle — the
coverage-based clear logs it — so run35's constant was a stall, not Ri. run37
call 1 continued:

```text
14.64 s  0x00b3 -> 0x00b6 -> 0x00c0     single level +-924 alternating at 1332 Hz
15.24 s  0x00c2                         levels 652/748/556
16.64 s  0x00c4 -> 0x00c8 -> 0x00ca -> 0x00cc   levels 844/748/556
17.06 s  0x00d0                         outer mode -> 0x0000, dwell = ffff
19.34 s  0x0024                         retrain
```

The 1332 Hz alternation is the mapping-frame rate, i.e. an audible tone, and the
multi-level stretches have the shape TRN2d and MP should have. At `0x00d0` the
transmit mode field drops to zero and the dwell goes indefinite; 2.3 s later the
card retrains from the top.

The Courier is not silent while we wait. Received level is 147 during our DIL
(listening, as expected) and then 2085, 2114, 2119, 2127 across our Phase 4
signals, rising to **2343 during the whole `0x00d0` wait**. It is answering
continuously and the card is not consuming it: `0x00d0` is a receive-side gate,
the same shape as the `0x007a` Ja gate one phase earlier.

### Open

- `0x00d0`: identify what the outer record tests there, and why the Courier's
  continuous Phase 4 response is not accepted. Receive side, not transmit.
- the `0x00b3` stall, five calls in six. The generator stops; the owner is
  unknown.
- codeword versus linear at `DM(0x3fb4)`, and the Ucode/`UINFO` relationship
  above. This is the most likely cause of the peer's +4800 ppm.
- the Session 61 `DM4..DM6` retained-workspace owner is still unresolved, and
  reaching `0x0080` at all still depends on the diagnostic that RTS-es out the
  `0x1900..0x19c8` echo bulk-delay adapter.

### Reproduce

```bash
make -C tools/adsp2181emu
/tmp/eicon-venv/bin/python tools/v90_dpcm_vector_trace.py \
  artifacts/eicon-native-tower/run34.rx.ulaw --to 17.0 --refill-audit
/tmp/eicon-venv/bin/python tools/v90_dpcm_vector_trace.py \
  artifacts/eicon-native-tower/run34.rx.ulaw --to 17.0 --count 0x2a52
tools/rtp_pcap_timing.py artifacts/eicon-native-tower/run34.rtp.pcap
```

Both page-14 diagnostics are env-gated: `EICON_V90D_TX_BLOCK_HOLD=0` restores
the per-frame clear (one downstream sample in six), and
`EICON_V90D_BULK_ADAPTER=1` keeps the echo bulk-delay adapter live, in which
case the outer state machine never reaches `0x0080` and there is no transmit era
to trace.

## Session 69: first downstream data from the card, and the retrain blocker

Session 68 was written before the first Courier connect and is superseded twice
over by what followed. Two live results, both on `artifacts/eicon-native-tower/run37.*`.

### The V.90 connect, with error control on

The Courier trained and reported a connect:

```text
Modulation               V.90/V.34+
Symbol Rate              8000/3200
Speed                    37333/31200
Protocol                 LAPM SREJ 128/15
Retrains Requested       0        Retrains Granted     0
Chars sent 0                      Chars Received 0
Disconnect Reason is XID Timeout
```

37333 bps downstream on an 8000 Hz PCM symbol rate, with **zero retrains
requested**. The blocker recorded in `docs/courier_firmware_analysis.md` and
`docs/v90_phase3_s_and_rbs_false_positive.md` — the Courier retraining after DIL
— did not occur.

`XID Timeout` is V.42 LAPM, not the physical layer: the Courier brought up LAPM,
waited for our XID parameter-negotiation frames, and timed out having moved zero
characters. `eicon_adsp_sip.py --tx-prbs` answers the card's datagram requests
with deterministic pseudorandom bytes, so there are no XID frames to send and
LAPM cannot complete however long it waits. The Eicon path has no V.42 at all.

### With error control disabled, data crosses

Dialled with `AT&M0` so no XID is required:

```text
Modulation               V.90/V.34+
Symbol Rate              8000/2400
Speed                    48000/24000
Protocol                 NONE
Recv/Xmit Level (-dBm)   24.9/17.3  then  18.9/11.3
SNR             ( dB )   42.5
Chars sent 1606                   Chars Received 39507
Retrains Requested       0        Retrains Granted     1
Disconnect Reason is Unable to Retrain
```

**39507 characters delivered downstream**, the card's first data of any kind.
The PRBS arrived as garbage on the Courier's terminal, which is exactly right
with `Protocol NONE`. Downstream rate rose from 37333 to 48000 and the received
level improved by 6 dB between the two `ATI11` reads.

Our side of the same call is `run37` call 6: 3079 RTP packets, 492640 samples
(61.58 s), 46242/46242 TX datagrams accepted. The Courier's `Last Call 00:00:43`
counts from CONNECT while our capture counts from SIP answer, and the difference
(18.6 s) matches this call's training reaching outer state `0x00d0` at 17.0 s.
That reconciles the call-duration mismatch noted while the run was in progress.

### `0x00d0` is not a dead end

Session 68 described `0x00d0` as a receive-side gate ending in a retrain, on the
strength of one call. Three later calls leave it:

```text
17.06 s  0x00cc -> 0x00d0
21.96 s  0x00d0 -> 0x00c2        (call 4 via 0x00bd)
22.56 s  0x00c2 -> 0x00c4 -> 0x00c6
22.9 s+  0x00c6   dwell = ffff, outer mode = 0x147e (transmit active)
```

The transmitted signal there is a multi-level constellation, 14 distinct levels
on the `0x00c2`/`0x00c4` passes and 31 during the `0x00c6` dwell, not a training
pattern. The `0x00b3` stall with the generator idle still happens on some calls;
it is intermittent, not a fixed ceiling.

### The blocker is now our retrain path

`Unable to Retrain`, with `Retrains Requested 0 / Granted 1`: the Courier never
asked for one. Our card restarted its own training three times inside call 6
(`0x00c4 -> 0x0024`, then the full handshake again, ending mid-handshake at
`0x0052`). The Courier granted the first and gave up when the later ones did not
converge.

Throughput matches that picture: 39507 characters in 43 s is about 7.3 kbps
against a 48000 bps line rate, so data moved in bursts between training
restarts rather than continuously. Data mode is reachable and not yet holdable.

### The timing offset is not a fixed property of our downstream

Session 68 recorded +4800 ppm from the tower peer and −4413 ppm from the
Courier as corroborating measurements of one defect. The `&M0` call complicates
that. In the 8000/3200 configuration the Courier read −4432 ppm; after the
retrain to 8000/2400 it read 0.

That second reading cannot be taken at face value: the same register block
reports `SNR 6469.5 dB`, which is impossible, so it was sampled in a disturbed
state. What survives is that the offset differed between two upstream symbol
rates on one call, so it is not a constant of the downstream stream. Settling it
needs a deliberate test — two calls forced to each upstream rate, reading
`ATI11` while `Online` and stable — not inference from post-retrain registers.

### Open

- our retrain path: why the card restarts training from `0x00c4`, and why the
  restarts do not converge. This is what ends an otherwise working connection.
- the transmit level. `Recv Level` of 24.9 dBm and the serializer's `>>2` point
  the same way, and 48000 is short of the 56000 ceiling.
- the timing offset, per the deliberate test above.
- V.42: `--tx-prbs` is why LAPM times out. A real connection needs XID and LAPM
  on the Eicon data path, or `&M0`-style raw operation on the analogue side.
- unchanged from Session 68: the `0x00b3` intermittent stall, the codeword versus
  linear question at `DM(0x3fb4)`, the `DM4..DM6` retained-workspace owner, and
  the bulk-adapter RTS diagnostic that `0x0080` still depends on.

### Reproduce

```bash
/tmp/eicon-venv/bin/python -u tools/eicon_adsp_sip.py \
  --native-mips --force-info-after-v8 --native-bearer-activation --tx-prbs \
  --trace-v90d-state --law pcmu --capture-prefix artifacts/eicon-native-tower/runNN \
  --mips-kernel artifacts/eicon-dsp/build-117-926/kernel/0009-diva-server-pri-30m-kernel \
  --mips-tikrnl artifacts/eicon-dsp/build-117-926/tikrnl/0258-tikrnl81.f34-task \
  --registrar asterisk.net.cryan.nz --username 6001 --password 6001
./.venv/bin/python tools/cx_at.py --dev /dev/cu.usbserial-21210 dial 6001 --wait 120 --pre 'AT&M0'
./.venv/bin/python tools/cx_at.py --dev /dev/cu.usbserial-21210 usrdiag
```

Take `ATI6` and `ATI11` while the call is still `Online`, not only after it
drops: the post-disconnect register block is not reliable.

## Session 70: the media budget, and why a hitch lands on DIL

The card connects, but the calls are intermittent and the symptom is a repeated
DIL. Logging was the suspect. It is not the cause: measured on run19's received
audio, the whole diagnostic apparatus costs about 0.5 ms of the 20 ms media
tick, and the media path had two structural faults that turn any lost wall time
into corruption of the sequence the Courier is measuring.

### What a 20 ms tick actually costs

`tools/eicon_adsp_sip.py` runs the pump, the SIP/RTP sockets and every
diagnostic on one thread. Per 160-sample quantum, replaying
`artifacts/eicon-native-tower/run19.rx.ulaw` (arm64, `-O3` core):

| component | per tick | note |
|---|---|---|
| `_step_mips` | **8.4 ms** | one MIPS main-loop pass per RTP packet, `max_insns=500_000` |
| `_frame_core` x160 | 2.5 ms | 9-18 us/sample; page 14 is the expensive page |
| `write_diag` (CSV + DM + SCC) | 0.06 ms | ~1400 DM reads, three unbuffered files |
| `RtpCapture.write` x2 | 0.06 ms | pcap + G.711 + WAV, both directions |
| `[v90d]` trace | 0.42 ms | format 64 lines; the tty write itself is 0.13 ms |

So 11 ms of every 20 ms, i.e. a 55% duty cycle with no elasticity, and 0.5 ms
of that is diagnostics. Mid-call overlay loads through the MIPS loader are 3-9
ms, not the hundreds of ms they look like: the one 363 ms sample is Unicorn
warming up on the first tick of the call, before any modem state exists.

The `[v90d]` trace becomes 3200 lines/s the moment page 14 reaches state
`0x0078`, because the key includes the per-symbol equaliser words `DM(0x11f5)`
and `DM(0x11f6)`. That is a DIL-era artefact of the trace, not a cost problem.

### The two faults

- **No jitter buffer.** RTP arrived on the peer's clock and was consumed on
  ours, and an empty queue substituted `self.silence` per sample. One late
  packet put 20 ms of invented silence into the middle of the DIL sequence, and
  the analogue modem answers a hole in that sequence by asking for it again.
- **Uncapped catch-up.** After any stall, `while now >= call.next_tick` ran
  every elapsed quantum back to back without servicing the RTP socket, so the
  receive queue stayed empty for the whole burst and then overflowed its 1 s cap
  and discarded arrivals wholesale.

Driven with a stub pump at the measured 11 ms/tick, an 8 ms jittered feed and
one 300 ms process stall over 6 s:

```text
before: substituted 3680 samples (460 ms of silence), dropped 640, TX 300/303
after : substituted 0, dropped 0, TX 302/302, +42 ms latency, queue drains to 0
```

The modem's clock is virtual, so the fix is to hold it rather than invent
samples: `rx_ready()` waits up to `--rx-hold-ms` for late audio, and a queue
above `--rx-jitter-ms` + one packet is drained ahead of the wall clock so the
latency is given back instead of becoming permanent one-way delay. Catch-up
is capped at `--catchup-quanta` per wake-up so RTP keeps being read. Every
substitution, discard, hold and over-budget tick is counted and reported once
per second as `[media]`, so "flaky" is now a number.

### Headroom

`--mips-interval 320` (one MIPS pass per two RTP packets) takes the pump from
0.57x to 0.34x real time -- 11 ms/tick down to 6.8 ms -- and on run19 the
per-second TrnProgress timeline is identical at both intervals, page 14 and
state `0x007a` included. It is a knob, not a default: it was verified on one
capture, not across the retrain path.

## Session 71: USRobotics V.92 interop makes V.42 the next layer

A USRobotics 56K Fax External V.92 (product `00568606`, V5.4.5 dated
2012-06-29, DSP rev 15) on `/dev/cu.usbserial-21240` was called four times.
All runs used the native MIPS path, `--tx-prbs`, the Session 70 media pacing,
and `--mips-interval 320`. Evidence is under
`artifacts/interop/usr-v92-21240/`.

Two calls with the modem's normal fallback policy connected identically:

```text
CONNECT 45333/V90/NONE
Protocol               NONE
Speed                  45333/21600
Modulation              V90/V34
Symbol Rate             8000/3200
Recv/Xmit Level (-dBm)  25/12
SNR (dB)                46
Timing Offset (ppm)     0
Retrains Requested/Granted  0/0
```

Both traversed `0x00b3 -> 0x00b6 -> 0x00c0 -> 0x00c2 -> 0x00c4 -> 0x00c6`.
The calls were deliberately ended by DTR after six online seconds. There were
no substituted or dropped RX samples; each run had only three over-budget
media ticks. Unlike the older Courier result, this peer therefore gives a
repeatable physical V.90 link without first exercising the retrain defect.

Two calls forced to ARQ-only mode with `AT&M5` failed identically. The Eicon
reached and remained at `0x00b3`; after about 38 seconds the USR returned
`NO CARRIER`, `Last Call 00:00:00`, and no physical-layer diagnostics. The
receive stream was intact (zero substitutions and drops), so this is not a
jitter artefact. `&M5` requires an error-controlled connection and refuses the
raw fallback used by the successful calls.

### Priority

Implement V.42 XID/LAPM on the Eicon data path next. Raw V.90 is now repeatable
against this modem, while the normal error-controlled service is the exact
boundary between two successful and two failed calls. This is a more useful
next interoperability layer than further tuning the successful raw training
path. The Courier retrain defect remains independently open and should still be
fixed before retrain/rate-renegotiation can be called interoperable.

## Session 72: CX93001 V.34 does not reach the V.42 boundary

A Conexant `CX93001-EIS_V0.2013-V34` on
`/dev/cu.usbmodem246802461` was tried twice with `--tx-v42` and once with raw
PRBS (`AT\\N0`). It advertises V.34 through 33600 but no V.90. All three calls
selected V.34 cleanly (`bootpage 7 -> 8`, overlay `0x0261`) and then returned
`NO CARRIER`; none reached a published DATASTATE speed or emitted an upstream
HDLC frame. The V.42 counters consequently remained zero.

The synchronous host adapter was extended to service V.34 as well as V90D:
V.34 TXD0 is packed oldest-bit at bit 15, unlike V90D's oldest-bit at bit 0,
and its negotiated packet size comes from the 2400-Hz DATASTATE rate. On the
live call the V.34 page raised one TX request immediately after loading, but
never cleared it after the host supplied TXD0 (`TX datagrams 0/1 accepted`).
The same physical failure in `AT\\N0` proves this test is not blocked at XID.
Before this peer can validate LAPM, the V.34 page-8 mailbox/bring-up path must
be recovered far enough to establish a raw carrier.

Evidence is under `artifacts/interop/cx93001-v34-246802461/`.

## Session 73: page 8 was never dispatched

The page-8 silence was not initially a V.34 detector or mailbox failure. An
execution watch after the INFO-to-V.34 handoff counted PM `0x06c8` once per
sample but zero executions of PM `0x0703`, the selected-channel foreground,
zero of `Core8kRoutine` wrapper `0x19d5`, and zero of the V.34 symbol routine
at `0x27dd`. Only TIKRNL's input ring changed. The SIP adapter resumed the
kernel tail directly after every SPORT frame; V.34 masks the SPORT interrupt
used by the compatibility shortcut, so nothing invoked the page.

For resident V.34 page `0x0261`, the native adapter now resumes at PM `0x02b7`.
That is the real selected-channel foreground: it reads the queued SPORT word,
calls PM `0x0703`, and PM `0x076a` dispatches `DM(0x3fb3)`. Offline replay then
showed live V.34 state (`DM119d`, carrier words and ring cursors) where
previously every internal word was frozen. This establishes that the page is
executing, but not yet that its Phase-3 transmitter is running.

It does not connect yet. Three post-fix calls still ended `NO CARRIER` with
published `TrnProgress=0x52`. V.34 leaves its foreground continuously live,
unlike the run-to-idle V.90 pages, so instruction cadence is now the critical
boundary. At the existing 20,000-instruction allowance the media tick is often
over budget and the peer responds but does not advance; the ADSP-2185N's
nominal 80 MIPS implies 10,000 instructions per 8-kHz sample, which restores
headroom but did not produce the expected answer-side S/PP sequence in the
first call. `EICON_V34_CYCLES_PER_SAMPLE` selects this budget (default 20000)
without another code change. The next work is to pin the fitted clock/cycle cadence and trace
`DM2147`'s `0x52` transition conditions against V.34 §11.3.1.2, not to force a
state or add synthetic tones.

Post-fix evidence is `call5-v34-foreground`, `call6-v34-10k`, and
`call7-v34-20k-m800` under the Session 72 artifact directory.

## Session 74: the apparent Phase-3 output was stale INFO

A database-level audit found the critical overclaim in Session 73. ADDSP guide
§5.3 defines `DM(0x3fa7..0x3fac)` as `TXSAMPLE_0..5`; the V.34 core must fill
three to six samples there at symbol rate and TIKRNL serializes them through
`ShellOutptr` at `DM(0x3fb4)`. From the page-8 handoff through the entire replay,
all six TXSAMPLE words remain zero, `ShellOutptr` remains `0x3764`, and
`DM(0x3764)` remains zero. Thus page 8 still emits genuine silence.

The energy in `call5-v34-foreground` was the preceding INFO waveform retained
until about 7.3 seconds because its 320-sample host stepping cadence delayed the
handoff. With the 800-sample cadence (`call7`) TX becomes zero exactly at the
5.08-second page switch and stays zero. The CX93001 bursts attributed to a V.34
response were consequently responses/retries around stale Phase-2 signaling,
not proof of S/PP/TRN interoperability.

The answer/call role was checked as the obvious deadlock explanation. The live
answer WDB retains `GEN_SETUP1=0x0484` (Table 15 answer mode); changing it to
`0x048c` calling mode prevents this peer capture from progressing through V.8.
Role selection is therefore not the missing publication. PM `0x0703`, wrapper
`0x19d5`, and V.34 symbol code `0x27dd` now execute, but the symbol scheduler
never fills TXSAMPLE at state `0x52`. The immediate target is the scheduler's
sample-buffer-empty/GEN_CONTROL condition and the page-8 initialization that
feeds it—not SIP, LAPM, or TXD0.

## Session 75: preserve the driver's native CAI-to-WDB initialization

The `divas4linux` source provides a concrete correction to the initialization
rig. `kernel/message.c:add_b1()` marks an incoming call `CALL_DIR_ANSWER`, builds
the 26-byte hardware-modem CAI, and attaches it to `CALL_RES`. The closed card
firmware translates that transaction before Python constructs
`NativeMipsModem`. Its pending write database is not the generic ADDSP example:

```text
00: 0040 0024 0038 0008 0000 0000 2105 f1fd 000c 000c 00b8 0033 0003 0000 2000 abcd
10: 00ff 0080 0060 0046 0050 0023 0041 0050 0005 0005 0000 000f 0040 000a 0029 010c
20: 0116 0000 002b 0001 000c ffff ffff ffff 0001 a13f 001f fffe 0003 0014 0000 0000
...
70: 0000 0000 0000 0000 0000 0003 0000 0000 0000 003f ffff 0377 000e 0015 000e 0015
```

The old `attach_connected_bearer()` discarded this and substituted handbook
values including `Norm_L=0x8100`, `speed_sel_l=0xff00`, and
`INFO0D_setup=0x03b7`. Native CALL_RES had selected `0xa13f`, `0xfffe`, and
`0x0377` respectively, plus CAI-derived timing and capability words. That
replacement had no analogue in the Linux driver.

`complete_native_answer()` now snapshots `DM(0x3ee0..0x3f7f)` before loading
DIAL. Native activation consumes that exact firmware-produced WDB as its first
communication cycle. After DIAL imports its defaults, the second cycle
republishes the same native transaction and changes only the two operation
words required by ADDSP Table 15: `GEN_SETUP1=0x0484` and
`GEN_SETUP2=0x0030`. The generic Tables 12/13 setup remains only as a fallback
for the non-native harness.

Offline replay still reaches page 8 and state `0x52`, so this cleanup does not
by itself start TXSAMPLE. It does remove the hand-built configuration as a
confounder. An init execution watch also confirms the page-8 chain is complete:
`Init8kRoutine 0x19d2` calls V.34 `InitRoutine 0x1000` once (about 14k emulated
instructions), followed by `Core8kRoutine 0x19d5` and symbol routine `0x27dd`.
The unresolved boundary is therefore after V.34 init entry and before its
GEN_CONTROL/TXSAMPLE publication.

## Session 76: live CX93001 test with native driver WDB

The driver-faithful initialization was tested live against the
`CX93001-EIS_V0.2013-V34` on `/dev/cu.usbmodem246802461`, forced to raw mode
with `AT\\N0`. The call progressed cleanly through V.8 and INFO, loaded page 8
at sample 43040 (5.380 s), and published `TrnProgress=0x52`. It then ended
`NO CARRIER`; no synchronous TX datagram was accepted (`0/1`).

The waveform confirms the page boundary exactly. Local TX RMS falls from 766
in the 5.3-second bin to zero at 5.4 seconds and remains zero; peer RX falls to
near-codec silence at the same point. Thus preserving the native CAI/WDB does
not by itself clear the page-8 scheduler gate, but it validates the failure
against hardware without the generic initialization overwrite. Evidence is
`artifacts/interop/cx93001-v34-246802461/call9-native-cai-wdb*`.

## Session 77: V.34-only CAI and page-8 scheduler audit

The Linux driver's modem CAI has a real V.90-disable control (`cai[10]` bit 7).
`EICON_FORCE_V34=1` now sends that control through native CALL_RES and caps the
CAI rate at 33600, instead of patching the translated DSP database. A live
CX93001 call forced to V.34 with `AT+MS=V34,1,2400,33600,2400,33600` followed
the same path: page 8 at 5.12 seconds, state `0x52`, silence, then `NO CARRIER`.
The option is retained as a diagnostic because it removes V.90 policy at the
correct driver boundary, but it is not the page-8 fix. Evidence is
`call10-force-v34-cai*`.

The scheduler audit found that V.34 `CoreRoutine` PM `0x27dd` deliberately
clears `GEN_CONTROL` at `0x27ea` on every symbol invocation; state actions must
set it again. The stop branch at `0x290c` is not being taken (`DM2165=0`). The
active action cursor cycles around `DM2166=0x10..0x12`, while the generator
actions PM `0x23a0/0x23a3/0x23a7` are present later in the low-DM action table.
`GEN_SETUP1=0x0484` is correctly imported into both `DM219c` and `DM21e5`, and
the V.34 init briefly publishes generator controls `0x0180` then `0x0080`
before the state-`0x52` actions clear them. This localizes the remaining bug to
selection/advancement of the Phase-3 action stream, rather than a skipped init,
a stopped core, or an absent generator implementation.

## Session 78: INFO handoff A/B and SPORT-format falsification

`tools/eicon_handoff_compare.py` now locates the last complete EADSPDM2 record
before an overlay transition and compares all 256 ADDSP interface words. The
working USR V90D handoff (`call4`, page `0x026a`) and failing CX93001 V.34
handoff (`call9`, page `0x0261`) both finish INFO at `TrnProgress=0x4f` and
carry `BaudInfo` high bits `0x3000`. Their expected modulation fields differ:

```text
                         V90D       V.34
BaudInfo                 3064       305d
INFO mode selector       0009       0000
INFO variant             000e       0008
INFO internal progress   004f       004f
```

The shared `0x3000` high field disproves the simple theory that V.34 alone
carries a reversed call/answer bit there. Old generic-init CX (`call3`) and
native-WDB CX (`call9`) have identical `BaudInfo=0x305d`, selector 0 and variant
8 and both fail, despite their configuration words differing substantially.
The V.34-only CAI run also arrives with `BaudInfo=0x305d`. There is no malformed
or uninitialized INFO mode value unique to the failing native setup.

A second A/B tested whether the modem's real V.34 output bypasses
`ShellOutptr/TXSAMPLE` and appears in the SPORT0 TX latch. It does not. At the
page-8 handoff the first latch word is the prior RX sample; it then freezes at
`0xf9a4` for all 23,810 observed page-8 samples. It matches neither current nor
one-sample-delayed RX and is never updated by the V.34 generator. Publishing it
would put a stale DC word on the line, exactly why the adapter already discards
that kernel latch. Thus this is not a hidden output-format path: the page truly
fails to publish samples.

The remaining format-sensitive boundary is internal, between decoded INFO
state and the V.34 action-table selection. The captures rule out G.711/SIP
output extraction and the obvious `BaudInfo` role-bit hypothesis; the next
trace must identify which writer keeps the state-`0x52` action cursor in its
receive/wait loop instead of advancing to PM `0x23a0..0x23a7`.

## Session 79: action-table trace and synthetic PC-stack overflow

Execution watches resolved the apparent action-table stall. `DM(0x2166)` is
the current action index: PM `0x28be` initializes it to `0x10`, PM
`0x2834..0x2836` advances it through `0x11`, `0x12`, and `0x13`, and PM
`0x286d..0x2870` resets it to `0x10`. The dispatcher does reach generator
action PM `0x23a0` and handlers PM `0x285c`, `0x2868`, and `0x2879`; the table
entry itself is valid. `DM(0x2291)` is instead the receive/sample queue count,
initialized to 8 at PM `0x0fbf` and consumed at PM `0x0fa3..0x0fa5` through
buffer pointers `DM(0x228f/0x2290)`.

Adding PC/count/loop stack depths to execution watches exposed the real fault.
The PC stack reached its hardware depth of 16 with this tail:

```text
...,02a8,02a8,02a8,02a8,0773,19d7,1712,1729,27fd,3617,3675,369a,36a1,3e0a,3b88
```

`0x02a8` is the resident IDLE sentinel supplied as the return address by
host-injected ADSP service calls. Some firmware paths jump to that IDLE instead
of executing `RTS`, leaving the synthetic return on the PC stack. Reinjecting
the per-sample continuation accumulated duplicate `0x02a8` entries. Once the
PC stack overflowed, a `DO` at PM `0x359d` could not push loop start `0x359e`;
its first iteration instead returned through unrelated caller PM `0x3b88`.
That recursively called PM `0x3dc1/0x3598`, filled the four-deep count and loop
stacks, left `CNTR=6`, and produced the misleading `DM2166=0x10..0x12` wait
loop.

`adsp2181_call()` and `adsp2181_modem_sample()` now discard consecutive stale
copies of their own synthetic return sentinel when re-entering from IDLE,
without disturbing the underlying firmware call frames. In the same offline
CX93001 replay, PC stack depth then remains 5 at `Core8kRoutine`, count and loop
stacks return to zero, PM `0x23a0` is reached again later in Phase 3,
`GEN_CONTROL` becomes nonzero, and the adapter publishes 77 nonzero V.34
samples between replay samples 40190 and 40272. The final zero block occurs
after the captured peer signal ends, not at the former page-8 handoff. This
clears the emulator-side action scheduler blocker; live hardware validation is
next.

## Session 80: defer firmware-side answer until SIP INVITE

The native SIP endpoint previously created a synthetic incoming call during
server startup. Consequently the MIPS firmware could finish CALL_RES and
connect its bearer before any SIP INVITE existed; accepting RTP later did not
model the ordering of a real network SETUP and answer. Native initialization
is now deferred until the first valid INVITE. The endpoint immediately sends
`100 Trying`, synchronously runs firmware entry, incoming-call assignment,
ADDSP answer completion, and bearer attachment, and sends `200 OK` with the
connected media line only after all of those steps return. This also prevents
INVITE retransmissions during the relatively long firmware setup without
exposing RTP prematurely.

## Session 81: the media budget again — the MIPS cost was the trace, not the MIPS

Session 70 measured `_step_mips` at 8.4 ms of the 20 ms media tick and treated
that as the price of running the firmware. It was not. Almost all of it was one
line of instrumentation.

`MipsShim.__init__` registered `hook_add(UC_HOOK_CODE, self._hook)` with no
address range, so every MIPS instruction became a Python callback. Unicorn only
instruments translation blocks that overlap a code hook's range; a rangeless
hook instruments all of them. Measured on `call1.rx.ulaw` with the native
harness, the supervisor executes about 12200 instructions per RTP packet and
took 8.5 ms to do it — roughly 700 ns per instruction, against about 20 ns
uninstrumented. Per callback the hook also re-ran `from unicorn.mips_const
import ...` and appended a formatted string to an unbounded list.

That list is the second fault. `trace_log` held every executed instruction
address: 17.5 million entries and 813 MB of RSS after 20 s of call, growing for
as long as the call lasted. Its only consumers were a 12-to-24-entry tail on
fault, and `.count()` of two addresses during boot.

### What replaced it

- One single-address `UC_HOOK_CODE` per intercepted address
  (`INTERCEPT_ADDRESSES`). The host-port helpers, `SERVICE_ASSIGN`, `SWITCH_ON`,
  the connected-driver publish and the stub return are all function-entry
  interceptions, so a hook per address is what the code always meant.
- `SERVICE_ASSIGN`'s return address is only known at call time and gets a hook
  added when captured. Removals are deferred to `_flush_hook_dels` before the
  next `emu_start`: `Uc.hook_del` drops the binding's reference to the ctypes
  trampoline, so calling it from inside its own callback frees the executing
  callback.
- A `UC_HOOK_BLOCK` hook carries the ADSP pump and instruction accounting.
  `_insn_count` still advances by real instruction counts (`size >> 2`); only
  the pump's phase within a basic block changes, and that phase never meant
  anything. The pump is load-bearing, not vestigial: with the block hook
  replaced by a no-op, boot fails at `native incoming call did not assign a
  modem DSP core`.
- `trace_log` is a 256-entry deque, and `exec_counts` replaces counting strings
  in it.
- `trace_calls` became a property that attaches a rangeless code hook only while
  the diagnostic is on. It is the one caller that genuinely needs every
  instruction.
- `NativeMipsModem` caches the `pm` pointer alongside `dm`. Both are fixed
  members of the core struct; `_frame_core` was re-crossing the FFI for it 8000
  times a second.

### Measured, on `usr-v92-21240/call1.rx.ulaw` to 20 s

| | before | after |
|---|---|---|
| per media tick | 11.04 ms | 3.91 ms |
| `_step_mips` | 8.91 ms | 1.88 ms |
| `_frame_core` x160 | 2.12 ms | 2.03 ms |
| real-time factor | 0.55x | 0.20x |
| peak RSS | 813 MB | 93 MB |
| boot to answer | 4.9 s | 1.7 s |

The overlay-switch list, the full TrnProgress timeline, the V90D outer-state
timeline and a SHA-256 of every transmitted sample are all byte-identical
across the change, with `EICON_MIPS_WARMUP=0`. The `--mainloop --connect
--simulate-b-channel` CLI path is byte-identical too.

### The first tick, and the one behavioural change

Unicorn JITs on first execution, and the mainloop path taken once the bearer is
up is not the path boot took. The first in-call `_step_mips` cost 93 ms and the
next 1.8 ms — five media ticks lost at the instant the call goes live, while
DIAL is choosing a modulation. On a live loopback call it measured worse still,
390 ms, because it contends with the socket loop.

`NativeMipsModem.warm_up()` runs three idle supervisor passes at attachment, so
the translation happens before the sample clock starts. This is the only change
here that is not behaviour-preserving: three extra polls move the whole replay
timeline one sample earlier (`0x0260` at 29438 becomes 29437), and on this
capture the run then also reaches outer state `0x007b`, which the baseline never
did. Arguably it is the more faithful model — a real card's supervisor runs
continuously during the SIP answer gap, where this harness ran zero polls,
because polls are driven by the sample clock. It is not proven better.
`EICON_MIPS_WARMUP=0` restores the old behaviour; pin it when diffing against a
recorded capture.

### Live loopback A/B, 22 s of `call1.rx.ulaw` at real-time pace

| | before | after |
|---|---|---|
| SIP answer latency | 4.79 s | 1.78 s |
| ticks over 18 ms | 2 | 0 |
| worst tick | 390.2 ms | 13.3 ms |
| catch-up deferrals | 20 | 0 |

The 390 ms tick and its 20 catch-up deferrals are the Session 70 corruption
mechanism firing on every call, at the worst possible moment. Substituted RX
samples were 160 before and 320 after, which is loopback jitter from a naive
same-machine sender, not signal.

### Not done, and why

`_set_load_result` is dead code. It reads PC inside a memory hook to decode the
load instruction, but decoded zero loads out of 100627 data-port reads — both
before and after this change. IDMA read-back works through the `mem_write` page
patch beside it. Removing the global code hook did not regress it; it never
worked.

The remaining `_step_mips` cost is about 3600 block callbacks per RTP packet,
roughly 1.4 ms. Chunking `emu_start` on exact instruction counts would remove
them, but resuming mid-stream risks splitting a MIPS branch-delay slot, and at
3.9 ms of a 20 ms budget the headroom is no longer worth that. For the same
reason the single-core question is closed: the pump does not need a second
thread, it needed a hook range.

At 3.9 ms/tick the Session 70 pacing defaults are conservative —
`--catchup-quanta 2` was chosen against an 11 ms tick. Untested on hardware.

## Session 82: Session 75 dropped the V90_DPCM enable, and replay cannot see it

Reported symptom: the V.34 work regressed V.90, with a suspicion of missing
nuance between the DSP and the MIPS regarding modulations. The suspicion is
correct and the word is `V8_SETUP`.

### The bisect that found nothing, and why

Replaying `usr-v92-21240/call1.rx.ulaw` to 20 s at `bb8dd03` (pre-V.34),
`16a7428`, `c50f43f`, `bb1adb5` and `4e71e5d` gives byte-identical output at
every commit, on both bearer paths. Without `--native-bearer-activation`,
page 14 transmits 0.0% non-zero at all five; with it, 63.7% at all five.

That is not evidence of no regression. `v90_dpcm_replay.py` is open loop: the
recorded RX stream already contains the peer's responses, so it contains a
V.90-accepting answer no matter what the card offered. A capability the card
fails to advertise costs nothing in replay and everything on a live call, and it
will look intermittent rather than broken, because the peer's decision is
marginal rather than forced. No offline harness in this repo can settle a
question of this shape.

### What actually changed, and where

Probing `DM(0x3ee0 + n)` immediately after bearer attachment on the
`--native-bearer-activation` path:

| write DB | bb8dd03 / 16a7428 | c50f43f onwards | documented meaning |
|---|---|---|---|
| `V8_SETUP +04` | `6000` | **`0000`** | **V90_DPCM + digital network** |
| `INFO0_SETUP +07` | `f0fd` | `f1fd` | V.34 Phase-2 capabilities, 3429 |
| `NORM_L +29` | `9100` | `b13f` | V.90 + V.34 |
| `SPEED_SEL_L +2b` | `ff00` | `fffe` | V.34 fallback rates through 33600 |
| `INFO0D_SETUP +7b` | `03b7` | `0337` | 3429 upstream, µ-law, −12 dBm0 |

The transition is exactly `c50f43f`, Session 75. That session replaced the
hardcoded ADDSP table with the driver's native CAI-to-WDB transaction, which was
the right correction — the handbook table had no analogue in the Linux driver.
But the native transaction leaves `V8_SETUP` at zero, and the second
communication cycle now overrides only `GEN_SETUP1` and `GEN_SETUP2`, so nothing
supplies it. Tracked across a whole call, `DM(0x3ee4)` is `0000` from
attachment to page 14 and never written. The session's own audit at
"Host-bit audit: V90D is enabled" documents `6000` as the V90_DPCM and
digital-network enable, so the card is now completing V.8 without having
requested V.90 DPCM in its setup word.

`NORM_L` is not the problem: `b13f` is a superset of `9100` in the bits the
handbook assigns, so V.90 and V.34 remain enabled there. `INFO0D_SETUP` is worth
a second look — `03b7` against `0337` differs in bit 7 only, inside the field the
audit reads as µ-law upstream and codec measurement, and every call here is
µ-law.

### Where the missing nuance probably is

`V8_SETUP` bit 13 is "digital network". That is a property of the bearer, not of
the modem CAI: on a PRI card the DSP has to be told it sits on a digital DS0.
This harness fabricates parts of call ingress (`fake_call_ingress`,
`inject_call_ingress`), so the likeliest explanation is that the real
CALL_RES/bearer path sets that bit and the synthetic ingress does not — i.e. the
MIPS never learns it is a digital call, so its CAI translation cannot enable
V90_DPCM. Bit 14 would then follow from the CAI, whose `cai[10]` bit 7 the
driver uses to *disable* V.90. `modem_cai()` also only ever offers
`DSP_CAI_HARDWARE_MODEM_ASYNC` (0x11), never `_SYNC` (0x12), which is unexamined.

Forcing `V8_SETUP` is a workaround, not the fix; the fix is to find who should
have set it.

### The lever

`EICON_WDB_OVERRIDE` applies words to the answer-cycle WDB on top of the native
transaction. Empty by default, because defaulting it on would silently
reintroduce the confounder Session 75 removed.

```text
EICON_WDB_OVERRIDE=0x04:0x6000
```

Confirmed inert offline, as predicted: with the override on, the 20 s replay is
identical except for its own log line. It has not been tested on hardware. That
test is the next step, and it is the only thing that can decide this.

## Session 83: correcting Session 82, and the page-14 exit that breaks fallback

### Session 82's conclusion was wrong

Session 82 found `V8_SETUP +0x04` at `0x0000` since Session 75 and framed the
missing V90_DPCM/digital-network enable as the V.90 regression. Hardware says
otherwise: V.90 **does** connect on the `--native-bearer-activation` path, with
`V8_SETUP=0x0000` throughout, and does not connect without that flag. So the
word is not a V.90 blocker and the handbook value is not required to reach a V.90
connection. The `EICON_WDB_OVERRIDE` lever stays, because the documented-vs-native
disagreement is still real and `INFO0D_SETUP` bit 7 is still unexplained on a
µ-law call, but it is no longer a suspect for "V.90 does not connect".

The reasoning error was treating an offline replay's ability to reach page 14 as
weak evidence and the capability table as strong evidence, when the only real
evidence available was a live call. Session 82 said no offline harness could
settle the question and then leaned on offline reasoning anyway.

The actual open items, from hardware:

- V.34 does not connect at all.
- V.90 connects only with `--native-bearer-activation`.
- A V.90 attempt that falls back to V.34 **drops the call**.

### The dropped call was the endpoint exiting

`EiconSipEndpoint.run()` had no guard around `media_tick`. Any exception from the
emulated pump propagated out of the loop, ran the `finally` that closes the
capture, and terminated the process — so the far modem saw the call vanish, and
the endpoint log ended mid-sentence.

`call10-force-v34-cai.endpoint.log` is exactly this, and it was in the artifacts
the whole time: it stops after `TrnProgress 0x0037` with only `[capture] wrote`,
and no `[call] ended` or `[media] call totals`, where `call9` has all three. A
firmware fault and a peer hanging up produced indistinguishable logs.

`run()` now calls `fail_call()`, which prints the traceback plus the overlay,
bootpage, TrnProgress and Rstatus at the fault, drops the call, and keeps the
endpoint listening. Verified by injecting a fault into a live loopback call: the
endpoint survives, reports `overlay=0x025f bootpage=6 TrnProgress=0x0004`, and
answers a second INVITE with a fresh firmware boot.

Replaying `call10-force-v34-cai.rx.ulaw` under the current tree no longer raises;
page `0x0261` loads at 5.037 s and reaches `TrnProgress 0x0071`. Session 79's PC
stack fix accounts for that, so the fault that killed call10 is likely already
gone — but the containment is what makes the next one legible.

### PM 0x06cd was never restored on the way out of page 14

The real fallback defect. The overlay switch reads:

```python
self.load_native_overlay(wanted)          # sets self.resident = wanted
...
elif self._v90d_saved_clear is not None and self.resident == 0x026A:
```

`load_native_overlay` assigns `self.resident = download_id` before returning, so
on the way out of page 14 `self.resident` is already the new page and the
condition can never hold. `previous` is captured immediately above for precisely
this test and was unused. The restore was dead code from the moment it was
written.

PM `0x06cd` is the six-count store that clears the V.90 mapping-frame block
`DM(0x3fa7..0x3fac)` in the resident kernel's frame tail. Page 14 needs it
suppressed. It is in the **resident kernel, not the overlay region**, so no later
page load replaces it. Measured on `usr-v92-21240/call1.rx.ulaw`:

```text
before:  6.6730s page=0x026a PM[0x06cd]=0x000000
        10.9977s page=0x0270 PM[0x06cd]=0x000000   <-- still NOP
        10.9979s page=0x0260 PM[0x06cd]=0x000000   <-- still NOP
after :  6.6730s page=0x026a PM[0x06cd]=0x000000
        10.9977s page=0x0270 PM[0x06cd]=0xa00001   <-- restored
```

So every page following a V.90 attempt ran on a kernel whose per-frame clear of
the mapping-frame block never executed. That is the V.90-to-V.34 fallback path,
and it is also the retrain path — the card restarts training from `0x00c4` by
leaving page 14, which makes this a candidate for the Courier retrain blocker in
Sessions 69 and 71 as well. Fixed by testing `previous`.

The sibling diagnostic at PM `0x19c8` (the bulk-adapter RTS) is deliberately not
restored: unlike `0x06cd` it sits inside the page-14 overlay region, so the next
overlay download overwrites it, and writing a saved page-14 word back over V.34
code would corrupt it.

### Still open

None of this explains why V.34 does not connect, or why V.90 needs
`--native-bearer-activation`. Both are now testable against a card that no longer
silently inherits a broken kernel, and against a log that distinguishes a fault
from a hang-up.

## Session 84: a terminal on the V.42 link

The V.42 endpoint could establish a link and acknowledge I frames but could not
send one. Every `_queue()` call site was XID, UA or RR: there was no V(S), no
window, no segmentation and no retransmission, and `rx_data` was a sink whose
only consumer was `len()` in the BYE handler, so payload the peer sent was
counted at hangup and discarded. There was no terminal device of any kind.

### Transmit side

`LapmEndpoint` now has the other half. `send()` appends to a byte stream;
`_fill_window()` segments it into N401-sized I frames while
`(V(S) - V(A)) mod 128 < k`, stamping each with the current V(R) so I frames
carry the receive acknowledgement. Incoming N(R) on both I and S frames releases
the window. RNR stops it and RR resumes it. REJ and SREJ go back N, and the
retransmitted frames are rebuilt rather than replayed, because N(R) has to carry
the receive state as of the retransmission and not as of the original. SABME
resets V(S)/V(A) and drops the unacknowledged set, but keeps unsent application
bytes, which were never on the wire.

Recovery is counted in `take()` calls, not seconds. The bit pipe is clocked by
the data pump and the harness can run at 0.2x real time, so a T401 in seconds
would fire at meaningless points during a replay. A stalled window is first
probed with RR(P): the likeliest cause is a lost acknowledgement rather than a
lost I frame, and the response carries the N(R) that resolves it without
resending anything. Only if that fails does it go back N.

Not done: V.42bis, and XID parsing for the peer's k and N401. Those remain at
the V.42 defaults as constructor arguments, which is the same conservatism the
existing XID echo already had.

### A pre-existing framing bug in `take()`

Found by the window test, not by inspection. Idle fill was synthesised one bit at
a time from `_idle_index`, so when a frame was queued partway through a flag, the
next `take()` switched to frame bits and abandoned the flag half-emitted. The
receiver then had to resync on the malformed delimiter and consumed the following
frame doing it, so **the first frame after any idle gap was lost**. With the
endpoint unable to transmit I frames this could only ever have corrupted an XID,
UA or RR, which is probably why it went unnoticed.

Idle flags are now queued whole into the same deque as the frames, so a partial
flag finishes before frame bits follow it even across calls.

### The terminal

`tools/v42_pty.py` allocates a PTY, prints the slave path, and is pumped once per
20 ms media quantum from `media_tick`. `--v42-pty` enables it, and it is created
at startup rather than on answer so a session can already be attached when the
call lands.

The PTY carries no line speed, parity or flow control: those belong to the
asynchronous side of a real modem's UART, and this link starts at the synchronous
V.42 boundary where the data pump has already framed the bits. `stty` on the
slave appears to work and changes nothing. Flow control is real, though — the
LAPM window is the only buffer, so the pump only reads from the PTY while the
window can take frames, and the terminal blocks when it cannot. Link-to-terminal
writes are dropped when nothing has the slave open, rather than buffered:
replaying a previous session's output at whoever attaches next is worse than
losing it.

Verified with two endpoints back to back through their own bit pipes and a PTY on
one side: `hello from the terminal` and `second line` arrive in the peer's
`rx_data`, `welcome to the card` arrives at the terminal, 5 I frames out, no
retransmissions. Eight new unit tests cover segmentation, the window limit,
piggybacked N(R), REJ go-back-N, RNR, the probe-then-retransmit sequence, SABME
reset and modulo-128 wrap.

### What this does not fix

The terminal is exactly as reliable as the carrier under it, and the carrier is
the open problem: V.34 does not connect, V.90 needs `--native-bearer-activation`,
and a live LAPM link has never been established through the emulated modem at
all — the loopback test above supplies its own SABME. The `[v42] totals` line now
reports the transmit counters, so the first real attempt will say which side
stopped.

## Session 85: live Courier V.42 test — the data path never switches on

First hardware test of the Session 84 transmitter. Five calls from the Courier
(`5607`/`1A11`, `/dev/cu.usbserial-21210`) to extension 6001, endpoint on port
5062 with `--native-bearer-activation --tx-v42 --v42-pty`. Answer: V.42 is not
reachable yet, and none of these calls put it under test.

| call | Courier setup | result | maxTrn | rate word `+0x01` |
|---|---|---|---|---|
| call1 | `&M4&K0S48=7` | 60 s call, clean stall | `0x00b3` | never |
| call2 | `&M4&K0S48=7` | no INVITE routed | -- | -- |
| call3 | `&M4&K0S48=7` | collapsed after `0x00b2` | garbage | never |
| raw1 | `&M0` | no INVITE routed | -- | -- |
| raw2 | `&M0` | no INVITE routed | -- | -- |

All calls that reached the card reported `SABME rx=0` and
`HDLC good/bad/abort=0/0/0`. The LAPM decoder was never fed a single bit.

### Why, exactly

`_v90d_tx_bits()` reads DATASTATEspeedTx at read-database `+0x01`
(`DM(0x3f61)`) and returns `None` unless bit 5 is set. That word is `0x0000` for
every sample of every failing call. `None` means `_lapm_active` never becomes
true, so `frame_fast` falls back to `_prbs_bits(48)` for transmit and
`_service_rx_data()` returns at its first line. The whole V.42 path is gated on a
word the card never publishes.

The gate itself is correct. Surveying every `.adsp-dm.bin` in `artifacts/interop`:
the two Session 71 raw successes published `+0x01 = 0x202d`, which is bit 5 set
with index 13, so `21 + 13 = 34` bits per datagram, and 34 x 8000/6 =
45333 bit/s -- exactly the rate that run reported. The address and the decode are
right end to end. The card simply does not get there.

### Error control has never once succeeded

Cross-referencing the modem logs for the AT setup actually used:

| setup | calls | reached a rate |
|---|---|---|
| `&M5` / `&M4` (error control) | usr call2, call3, call5-v42; courier call1 | **0 of 4** |
| `&M0` (raw) | usr call1, call4, call6-v42, call7-v42 | 2 of 4 |

So `0x00b3` is not exclusively an error-control state -- raw `call6-v42` reached
it too -- but no call that asked for error control has ever published a rate. At
n=4 each that is suggestive, not conclusive: under raw's own 50% failure rate,
four consecutive error-control failures has probability about 0.06. Worth more
samples before treating "error control breaks it" as established.

### The failure has a consistent position

`call1` walked `0x0078 -> 0x007a -> 0x007b -> 0x0080 -> 0x00b0 -> 0x00b1 ->
0x00b2 -> 0x00b3` and held `0x00b3` for the remaining 45 seconds. DSR asserts at
`0x00b1` (`Rstatus_ch=0x8200`), DCD never does. At the stall `DM(0x3fb4)` has
reverted from a real sample to the generic pointer `0x3764`, and
`DM(0x3fb2/0x3fb3)` moved from the page-14 routines `19d2/19e1` to `17bb/1706`,
so the V90D generator has stopped; `DI_control` stops requesting transmit.

`call3` reached `0x00b2` at essentially the same point and then the shared boot
word went to `0x8001`/`0xbfb2` and thrashed -- the modem task collapsing rather
than stalling. Same moment in the call (14.62 s vs 14.72 s), two manifestations.
Neither produced a media fault, so the Session 83 containment was not needed, and
neither printed the PM `0x06cd` restore, because the card never left page 14 by a
proper overlay request.

Media pacing was clean throughout: `call1` had 0 substituted RX samples, 0
dropped, 4 ticks over 18 ms, worst 26.4 ms. The Session 81 budget work holds on
live hardware and lost wall time is not implicated in any of this.

### Caveats on this run

- The raw-mode regression check is **inconclusive**. Both `&M0` attempts failed
  to route an INVITE at all, so this tree has not been shown to still reach
  `0x00c6`/`0x00d0` on a known-good raw call. That check is still owed.
- Three of five calls produced no INVITE. Cause not established; all 32 historical
  dials in the artifacts used the same `ATDT6001`, and the endpoint had registered
  in each case. Asterisk-side routing was not inspected.
- The test harness printed one misleading `SABME seen` line on `call3`. It matched
  the substring in `SABME rx=0` from the end-of-call totals, not a real frame.

### What this means for the terminal

The Session 84 transmitter and PTY are still unexercised against hardware. The
loopback test in that session supplies its own SABME, so what is proven is the
framing, the window and the state machine -- not that a Courier's XID and SABME
survive the carrier. Nothing above is evidence against the V.42 work; it is
evidence that the blocker is two layers below it.

## Session 86: the garbage is a missing V.42 detection phase

A Courier call finally connected: `V.90/V.34+`, 38666/24000, symbol rate
8000/3429, DCD asserted, 84 seconds, 86055 chars received. The terminal filled
with garbage, and the Courier's `ati6` explains it in one line:

```text
Protocol               NONE
Data Compression       NONE
Octets sent 0   Octets Received 0   Blocks sent 0   Blocks Received 0
Chars sent 1364  Chars Received 86055
Disconnect Reason is Unable to Retrain
```

`Protocol NONE` with zero octets and zero blocks: the Courier connected with **no
error control at all**. Our side was sending HDLC flags into a raw character
pipe, and the Courier was sending raw characters into our HDLC decoder, which is
why `[v42] totals` read `HDLC good/bad/abort=0/22/270` -- 292 framing attempts,
none of them real. Both directions were garbage by construction.

### Why the Courier gave up on error control

V.42 (03/2002) 7.2.1.3, answerer actions:

> the control function of the answerer shall transmit 1-bits (mark) until
> termination of the detection phase, receipt of the ODP, or detection of the
> start of the protocol phase (the start of the protocol phase is indicated by
> receipt of continuous flags, or of an LAPM or alternative procedure protocol
> frame).

And 7.2.1.2, originator actions:

> If the ADP is not observed within the period of T400 [...] the originator shall
> decide that the answerer does not possess V.42 error-correcting capability. In
> this case, the originator may fall back to non-error-correcting mode.

`LapmEndpoint` started on continuous flags the moment the data state opened. To
the Courier that reads as "the protocol phase has already begun", so it stopped
looking, never received an ADP, T400 expired, and it fell back exactly as
specified. The bug was that we skipped the detection phase entirely, and the
symptom was 86 KB of garbage.

### Implemented

The answerer role of 7.2.1, patterns taken verbatim from the Recommendation
rather than derived, since the parity convention in the printed patterns does not
match a naive reading:

```text
ODP  0 1000 1000 1 11...11  0 1000 1001 1 11...11   DC1, alternating parity
ADP  0 1010 0010 1 11...11  0 1100 0010 1 11...11   (E) and (C) = V.42 supported
```

Each is one start-stop character over the synchronous link: start bit, seven data
bits low-order first, parity, stop bit. The endpoint now sends mark until it sees
four DC1s of alternating parity, answers with the "V.42 supported" ADP from
Table 3 ten times, then enters the protocol phase and starts flags. Receipt of an
LAPM frame also enters the protocol phase directly, so an originator with
detection disabled (USR `S48=0`) still works. T400 is counted in service calls
for the reasons in Session 84; on expiry it stays on mark and says so, because
there is no asynchronous mode on this side to fall back to.

`EICON_V42_DETECT=0` restores the flags-immediately behaviour. Eight new tests
cover mark-not-flags, the four-DC1 threshold, the ADP contents and repetition
count, flags following the ADP, same-parity DC1s not counting, the LAPM-frame
shortcut, the timeout, and the opt-out. 21 tests total.

### The larger question this exposes

`modem_nl_assign_payload()` sets `DLC_MODEMPROT_DISABLE_V42_V42BIS`, so the
harness explicitly disables the **card's own** V.42 and runs the plain
B2_TRANSPARENT branch. That is why `v42_lapm.py` exists at all: with the
firmware's error control switched off, the Python is the V.42 entity and owes the
detection phase.

There is a second route that has never been tried. The card ships a real V.42
implementation, and `DLC_MODEMPROT_DISABLE_V42_DETECT` (0x08) exists as a
separate bit, so the firmware clearly has its own detection phase. Not setting
`DISABLE_V42_V42BIS`, and supplying the B2 error-correcting negotiation block
instead of the transparent one, would use the shipped implementation rather than
a from-scratch LAPM -- which is this project's whole premise. It moves the data
path off the synchronous pump and onto the protocol page, so it is not a small
change, but it is very likely less work than making our LAPM interoperate, and it
would exercise firmware nobody has run yet.

### Not validated on hardware

The fix is unit-tested and unvalidated. Five further calls were attempted
(`&M4&K0S48=7` and `&M4&K0S48=0`, the latter forcing LAPM without a detection
phase) and none routed: no INVITE reached the endpoint, which had registered
each time. Four of the last seven dials behaved this way, where earlier calls in
the same session routed normally, so something in the telephony path changed
rather than in the endpoint. `Disconnect Reason is Unable to Retrain` on the one
successful call also leaves the Session 69/71 retrain blocker untouched: the log
shows the card reaching page 8 for a retrain at 87.44 s and the shared boot word
going to `0xf770` immediately after.

## Session 87: a Courier call completes, and the DIL predictor is falsified

### Routing, and a retraction

Session 85 and 86 blamed "something in the telephony path" for five calls that
never produced an INVITE. That was wrong: Asterisk routes extension 6001 to port
**5060** specifically, and those runs bound 5062 because the user's own endpoint
held 5060 at the time. Registering successfully on another port is not enough.
Every "no route" result in Sessions 85 and 86 is explained by that and says
nothing about the card or the line.

### A complete connection

On port 5060, `AT&M4&K0S48=0`:

```text
[dil]  6.200s: flag DM(0x3f8b)=0x0000 count DM(0x3f87)=0x000d measure DM(0x3f8e)=0x2388
      0x00b3 -> 0x00b6 -> 0x00c0 -> 0x00c2 -> 0x00c4 -> 0x00c8 -> 0x00ca -> 0x00cc -> 0x00d0
[v42] V.90/V.34 synchronous data state: TX 29 bits/datagram, RX 13 bits/datagram
Rstatus_ch=0x8783[change_h|CTS|DSR|DCD|change_l|sec_rx_present|sec_rx_data]
```

`maxTrn=0x00d0`, rate word `+0x01 = 0x2028`: bit 5 set, index 8, so 21 + 8 = 29
bits per datagram and 29 x 8000/6 = 38666 bit/s -- the same rate the user's
successful call reported. DCD, CTS, speed_tx and speed_rx all assert.

**This is the first time the V.42 data path has activated on hardware.**
`_lapm_active` became true and the LAPM decoder was fed for the whole call. So
the Session 85 blocker is not permanent; it is the lottery the user describes.

### The predictor was wrong

Nine archived captures gave a clean split: `DM(0x3f8b)` was `0x0001` for every
call that published a rate and `0x0000` for every call that stalled at `0x00b3`,
0 for 6 against 2 for 3. A `[dil]` line was added to print it at TrnProgress
`0x007a`, where the outcome is set, along with `DM(0x3f87)` and `DM(0x3f8e)`,
which looked like a count and a channel measurement on the same split.

The very next live call printed `flag DM(0x3f8b)=0x0000` and then reached
`0x00d0`. The correlation does not hold and the wording that claimed it has been
removed from the code. The three words are kept as instrumentation for the phase
where the call is actually decided -- none of them appears anywhere else in 86
sessions -- but they do not predict anything. Nine samples was not enough, and
the split being perfect over those nine made it look stronger than it was.

### Still no LAPM

`HDLC good/bad/abort=0/2/15` and `SABME rx=0` over 44 seconds of data state. The
decoder ran and found no valid frame in 17 framing attempts. Two possibilities
remain open and this run cannot separate them:

- the Courier again connected with `Protocol NONE`, so there is no LAPM to
  decode. `S48=0` should force LAPM, but the successful call in Session 86 also
  reported `Protocol NONE` under `&M4`, and no `ati6` was captured this time;
- the receive side is misframed. `_service_rx_data()` takes 13 bits per datagram
  MSB-first from RXD; if the order or the count is wrong, a perfectly good LAPM
  stream would produce exactly this -- a handful of accidental flags and no valid
  FCS.

The next run must capture `ati6` immediately after the call. `Protocol` and the
octet/block counters separate those two cases in one line, and without them this
is guesswork. The V.42 detection phase added in Session 86 is still unvalidated:
with `S48=0` the Courier skips detection, so this call did not exercise it.

## Session 88: the echo canceller is still off, and still cannot be turned on

The near/far echo bulk-delay adapter at PM `0x1900..0x19c8` is the card's echo
canceller, and this harness RTSes out its tail on every page-14 load. That has
been the default since Session 65. It is a real functional gap and a plausible
contributor to the DIL lottery: our path runs SIP/RTP to an ATA to two-wire to
the Courier, so there is a hybrid generating exactly the echo this adapter
exists to remove, and the card's receiver has to pull the analogue modem's
upstream V.34 out of it.

Re-tested on `usr-v92-21240/call1.rx.ulaw` to 16 s, native-bearer path:

| configuration | V90D outer state walk | result |
|---|---|---|
| adapter disabled (default) | `0050 .. 0068 006a 0070 .. 007a 007b 007c 0080 00a6 00b0 00b1 00b2` | clean |
| adapter enabled, `DM(0x32f7)=0` | `0050 .. 0068` then `0fc2`, `00c4`, `78f8` | state word corrupted |
| adapter enabled, `DM(0x32f7)=8` | identical to the above, word for word | state word corrupted |
| adapter enabled + `--prime-v90d-bulk-cursor` | `0050 .. 0068` and stops | stalls |

So enabling it is still worse than leaving it off: the machine cannot get past
`0x0068`, where with the adapter off it walks to `0x00b2`. Turning the echo
canceller on is not a switch that is being left unflipped.

### Two things this narrows

**The Session 65 symptom no longer reproduces.** That session pinned the
destruction precisely: PM `0x1930` zeroing `DM(0x3fb3)`, the `Core8kRoutine`
callback, after which the equalizer and Ja detector necessarily freeze. Watching
`DM(0x3fb3)` across all four runs above, it is never zeroed. The failure has
moved -- plausibly because of Session 79's PC-stack fix or Session 83's PM
`0x06cd` restore, both of which changed what the resident kernel does per frame.
Instead the outer state word `DM(0x1ff7)` itself takes impossible values
(`0x0fc2`, `0x78f8`), so the corruption is now landing on the state image rather
than on the write database. Anyone resuming Session 65's trace should re-derive
the destructive store rather than trusting the `0x3fb3` finding.

**`DM(0x32f7)` can be dropped as a candidate.** Session 65 left "trace how
`DM(0x3fbc/0x3fbd)`, the RX workspace, `DM32f7` and ADSP carry/NORM semantics
produce `DM0..DM7`" as the next target, on the grounds that the selector stays
zero with no DSP writer. Setting it to `8` at page-14 entry -- before state
`0x60`, so PM `0x1982` reads the new value, and it is still `8` at the end of the
run -- produces byte-identical behaviour to leaving it zero. The page-entry
workspace does hold a second descriptor at `DM8..DM11`
(`2ac7 2ad2 2ae0 2b1b` against `2aca 2ad2 2ae5 2b1b` at `DM0`), so selecting it
is meaningful, and it changes nothing. Whichever descriptor is selected, its
cursor word is zero, which is Session 65's real point: PM `0x1982` deliberately
writes zero there, so the missing thing is an input to that calculation, not the
selector.

Cursor priming plus a live adapter now stalls at `0x0068` rather than corrupting,
which is a third distinct failure mode and consistent with Session 65's warning
that the prime is "temporarily useful but eventually destructive".

### What is not established

Whether the missing echo canceller is what makes DIL a lottery. It cannot be
tested by comparison while enabling it fails earlier than disabling it, so the
causal claim stays open. It is a good hypothesis with a mechanism, not a result.
The blocker remains where Session 67 left it: the owner of the bulk workspace
handoff is above the ADSP page, in call ingress/activation.

## Session 89: the ingress handoff is healthy, and V8_SETUP=0 is the firmware's

Session 67 left "the missing owner is above the ADSP page, in call ingress/
activation" as the blocker, and Sessions 82, 86 and 88 all pointed back at it.
Instrumenting the path says it is not missing anything.

Lower-PRI event `0x03` dispatches service driver `0x80098310`, which calls
`0x80097f60` and polls `0x80095318`. All three execute exactly once on the
`--native-bearer-activation` path. Two native transfers result:

- **KERNEL**, DSP `0x6e58` for `0x10` words. IDMA `0x6e58` is DM `0x2e58`, and it
  lands: `DM(0x2e58)` becomes `0x0277`, matching Session 67. (Session 67 records
  this address as `0x6e68`; the transfer object here says `0x6e58`.)
- **DATABASE**, 256 words to DSP `0x7ee0`, which is DM `0x3ee0` -- the entire
  write database. **It happens.** Exactly one transfer, 256 words, and
  `DM(0x2f27..0x2f29)` come up `2f21/2f00/2f0e`, so the dispatch roots are
  populated too.

An earlier attempt at this measurement reported zero database writes. That was an
instrumentation error: the bulk-write interception in `_hook_intercept` writes
`dm[]` directly and never calls `adsp2181_host_write`, which is what was being
counted. `shim.host_writes` does record them, as 256 successive writes to the
`0x7ee0` IDMA port.

### V8_SETUP is zero because the firmware writes zero

The values the card's own connected-task driver puts in that transfer:

| write DB | firmware writes | handbook (Session 22 audit) |
|---|---|---|
| `V8_SETUP +0x04` | **`0000`** | `6000` (V90_DPCM + digital network) |
| `INFO0_SETUP +0x07` | `f3fd` | `f0fd` |
| `NORM_H +0x28` | `0001` | `0001` |
| `NORM_L +0x29` | `b13f` | `8100` |
| `SPEED_SEL_L +0x2b` | `fffe` | `ff00` |
| `INFO0D_SETUP +0x7b` | `0337` | `03b7` |

So `V8_SETUP=0x0000` is authored by the shipped firmware, not dropped by this
harness and not a Session 75 regression. Session 82 asked whether something
should have set it and answered "probably the bearer, via the digital-network
bit"; the answer is that the card's own driver writes zero there on this build.
That closes the question. `EICON_WDB_OVERRIDE=0x04:0x6000` therefore forces a
value the firmware deliberately does not publish, which is worth knowing before
anyone reads an A/B result from it -- it is a deviation from the card, not a
restoration of it.

`NORM_H` also settles at the handbook's `0x0001`, so the `0x00ff` that Session 82
listed against it was a different word (`+0x10`) and the modulation masks are not
disturbed.

### The database is co-authored, which undercuts "preserve the exact transaction"

Comparing the transferred values against DM at the moment
`complete_native_answer()` snapshots it, with only one transfer in between:

```text
+0x06   written 0105   DM holds 2105
+0x07   written f3fd   DM holds f1fd
```

The DSP modifies the database after the host publishes it. Session 75's model --
snapshot the driver's transaction and republish it verbatim as the answer cycle --
treats it as host-authored state. It is not; the DSP is a co-author, and
republishing a snapshot taken at one instant overwrites whatever the DSP had
contributed by the next. That does not make Session 75 wrong to stop substituting
handbook values, but "the exact result of the notification" is not a thing a
single snapshot can capture.

### The bulk workspace is not an ingress problem

`Nearbulklength`, `BulkLength`, `BulkInputX` and `BulkInputY` are at
`DM(0x3fbc..0x3fbf)` -- the **read** database, which the DSP writes. They are zero
after activation because no page has computed them yet; Session 58 observed
`1d77/0ae0` at V90D state `0x60`, which is when PM `0x1982` derives them. Ingress
is not expected to publish them and its not doing so is not the fault.

Combined with Session 88 ruling out `DM(0x32f7)`, the echo-canceller blocker is
now firmly inside the DSP: the PM `0x1982` bulk-workspace calculation, with a
complete write database in front of it and a populated dispatch list. Session
67's redirection upward was reasonable at the time but the ingress path has since
been fixed enough that it is no longer where the fault lives.

## Session 90: PM 0x1982 is correct; PM 0x1930's fill bounds are the fault

Traced with the core's own disassembler (`adsp2181_dis.py`, which shares the
emulator's dispatch tables) against a live page-14 PM dump, plus exec and DM
watches at V90D state `0x60`.

### The routine

```text
1982: I1 = DM($32F7)                    selector -> workspace base
1985: modify address register            I1 += M3(7)
1987: DM(I1,M3) = AR                     DM7 = 0001,  I1 += -6
1988: AR = AF - 1, SR0 = DM(I1,M2)
1990: AF = SR0 - AY0
1992: IF AC JUMP $1994
1994: AF = AR - AY0
1995: IF NOT AC AR = AY0
1996: AR = AR + 0
1997: IF GE JUMP $199A                   <-- decides AX0
1998: AR = SR0 + 0
1999: AX0 = $0004
199a: SR = LSHIFT AR (LO), AY1 = SE
199b: DM(I1,M1) = SR0                    DM0 = 03cd
199d: DM(I1,M1) = $0000                  DM2 = 0000
199e: DM(I1,M1) = SR0                    DM3 = 0001
19a0: AF = NOT AY1, AY1 = DM(I1,M1)
19a2: AR = AX0 OR AY0, AX0 = AR
19a3: DM(I1,M2) = AR, AR = AX0 AND AF    DM6 = 0000
19a5: DM(I1,M0) = AR                     DM4 = 0000
19a6: RTS
```

DM write watches confirm the offsets rather than inferring them from the I1
walk: `ppc=1987 -> DM7`, `199b -> DM0`, `199d -> DM2`, `199e -> DM3`,
`19a3 -> DM6`, `19a5 -> DM4`. Session 65's claim that PM `0x1982` writes `DM4=0`
is correct, and the instruction is `0x19a5`.

### DM4 = 0 is the intended result, not a missing publication

`DM4` is `(AX0 OR AY0) AND NOT AY1`. `AX0` is `0` from PM `0x1991` unless PM
`0x1999` sets it to `4`, and PM `0x1999` is only reached by falling through the
`IF GE JUMP $199A` at PM `0x1997`. In the traced pass `AR = 0x03cd` at PM
`0x1996` -- positive -- so the branch is taken, PM `0x1998/0x1999` are skipped,
`AX0` stays `0`, and `DM4` is `0`. `AY0` and `AY1` are `0` and `0xffff` at that
point, so the expression is `0`.

So the calculation is doing what it was written to do. Sessions 58 and 59 framed
this as "the unpublished initial far-bulk cursor" and Session 65 narrowed it to
"either an input handoff is missing or one of the calculation's ADSP semantics is
wrong". It is neither: `0` is the firmware's deliberate output for
`Nearbulklength = 0x03cd`, and PM `0x1935` then advances the cursor from `0`
normally -- observed writing `DM4` 640 times with `0, 1, 2, ...`. PM `0x1982`
should be struck off the open list. So should `--prime-v90d-bulk-cursor`, which
overwrites a correct value.

### The fault is where the fill lands

With the adapter live, the outer state word `DM(0x1ff7)` takes `0x0fc2` and a DM
watch names the writer: PM `0x2fea` -- the sequencer's own state store, the same
instruction that writes every legitimate state. Nothing overwrote `0x1ff7`; the
sequencer *read* a bad next state.

An exec watch on PM `0x1930`, the adapter's store, gives its destination. `I0`
sweeps from `0x0049` to `0x1b41`, 1556 distinct addresses. Meanwhile the outer
record pointer `DM(0x120f)` walks `0x18ba -> 0x18cc -> 0x18d8 -> 0x18e7 ->
0x18f6 -> 0x1902` and then jumps to `0x1b51`. The record table is inside the
swept range: the fill zeroes the records, the sequencer reads a zeroed one,
publishes `0x0fc2`, and the pointer follows the wreckage.

This is Session 65's collision with a different victim. There it reached
`DM(0x3fad)` and `DM(0x3fb3)` in the memory-mapped database and killed
`Core8kRoutine`; that no longer happens (Session 88), and the fill now reaches
the state-machine records instead. Same cause, new casualty, which is why
enabling the echo canceller stops the machine at `0x0068` rather than freezing
the equalizer.

### Next

The question is no longer what `DM4` should start at. It is why PM `0x1930`'s
destination is unbounded: a delay line described by `Nearbulklength = 0x03cd` has
no business writing to `0x1b41`. Trace how `I0` and `L0` are loaded on entry to
PM `0x1900`, and whether the modulo addressing that should confine the fill to
the delay buffer is being set up at all -- an `L` register left at zero disables
ADSP circular addressing, which would turn a bounded ring write into exactly this
linear sweep. `L0` is in the `[EXEC]` line, so this is one more trace.

## Session 91: the fill is unbounded because its modulo bound is zero

Continuing Session 90. The hypothesis there -- that `L0` is zero and ADSP circular
addressing is therefore disabled -- is **wrong**. `L0` is indeed `0x0000` at every
execution of PM `0x1930`, but the routine never loads `L0` or `B0` at all. It
reloads `I0` from a computed value on each pass (`I0 = AX1` at PM `0x190e` and
`0x191f`, `I0 = AR` at `0x1928`, `I0 = AX0` at `0x192e`), so linear addressing is
intended and the bound is supposed to come from arithmetic, not from the DAG.

### The arithmetic bound is zero

The routine does its own modulo. Twice it computes a candidate address, compares
it against an interval bound in `AY0`, and adds the length in `AY1` back if the
subtraction underflowed:

```text
1921: AR = AR - AY1, AY0 = DM(I1,M2)
1922: DM(I5,M5) = SR0, AF = AR - AY0
1923: IF NOT AC AR = AR + AY1        <-- wrap correction
1925: AR = AR - AF, AX0 = AR         <-- AX0 captured, becomes the destination
1926: DM(I5,M5) = SR0, AF = AR - AY0
1927: IF NOT AC AR = AR + AY1        <-- wrap correction
192e: I0 = AX0
1930: DM(I0,M1) = SR0                <-- the store
```

Traced at V90D state `0x60`, `AY0` is `0x0000` at both comparisons. `AF = AR - 0`
therefore never underflows for a positive `AR`, `AC` is set every time, and both
`IF NOT AC` corrections are skipped: **0 fires against 597 skips at PM `0x1923`
and the same at PM `0x1927`**. With no wrap, the destination walks linearly, which
is the `0x0049 -> 0x1b41` sweep from Session 90 that flattens the state-machine
record table.

`AX0` is also captured at PM `0x1925` before the correction at `0x1927`, so even a
working wrap would only fix `AR`, not the store destination. Whether that is
deliberate depends on the intended value of `AX0`, which brings it back to the
same place.

### Where the zero comes from

`AY0` is loaded by `DM(I1,M2)` from the eight-word workspace, and the workspace
after PM `0x1982` is:

```text
DM0..DM7 = 03cd 2ad2 0000 0001 0000 0000 0000 0001
```

`DM5` and `DM6` are zero. `DM6` is written zero by PM `0x19a3`
(`AR = AX0 AND AF`, with `AX0 = 0`), and `DM4` is written zero by PM `0x19a5`
for the same reason. Both trace to a single branch: PM `0x1997`
`IF GE JUMP $199A` is taken because `AR = 0x03cd` is positive, which skips PM
`0x1999` `AX0 = $0004`. If that branch fell through, `AX0` would be `4` and both
words would be non-zero.

So the whole failure -- Session 58's "unpublished cursor", Session 65's collision,
Session 88's three failure modes, Session 90's unbounded sweep -- reduces to one
predicate: whether `AR` at PM `0x1996` should be negative. `AR` there derives from
`Nearbulklength` (`DM(0x3fbc)`) through the chain at PM `0x1988..0x1995`, and
`Nearbulklength` is a read-database word the DSP computes, observed as `0x0413` at
page entry and reaching PM `0x1996` as `0x03cd`.

This is Session 66's "missing retained workspace words" made concrete: the missing
word is the modulo bound, and it is zero because the near-bulk path was selected
where the far-bulk path was expected.

### Next

Two candidates, and they are distinguishable:

- **The inputs are wrong.** `Nearbulklength`/`BulkLength` are DSP-computed in the
  read database, and if the near/far split is mis-derived the branch legitimately
  takes the near path. Trace who writes `DM(0x3fbc)` and `DM(0x3fbd)` and against
  what.
- **A flag semantic is wrong.** Sessions 46 and 52 both found real emulator
  defects of exactly this shape -- `ABS` flags and MAC rounding. The chain at PM
  `0x1988..0x1995` uses `NORM`, `SE`, and conditional `AR = AY0` on `AC`, and an
  `AC` or `AV` discrepancy across `SR = NORM SR0 (LO)` would flip the branch. The
  0-of-597 result is suspicious on its own: a modulo correction in shipped
  firmware that never once fires is more likely mis-evaluated than never needed.

The second is cheaper to test: audit `NORM` and subtract flag behaviour at PM
`0x1989..0x1996` against the ADSP-2181 manual, in the style of the Session 52
opcode audit.

## Session 92: the manual clears the emulator; the near/far fork is not the fix

Session 91 offered two candidates: wrong inputs, or a wrong flag semantic. The
flag semantics are correct, and the fork that selects near from far bulk is not
where the fault lives either.

### The emulator's flags are right

`shift_op` in `2100ops.inc` touches `ASTAT` only to read `CFLAG` for the rotate
forms and to set or clear `SS`. It never writes `AZ`, `AN`, `AV` or `AC`, which
matches the ADSP-2100 family shifter: shifter operations affect `SS` and nothing
else. So `SR = NORM SR0 (LO)` at PM `0x1989` correctly preserves the flags that
PM `0x1988`'s `AR = AF - 1` set, and PM `0x198d`'s `IF NE` correctly tests that
`AZ` four instructions later.

`CALC_C_SUB(r)` is `astat |= (~r >> 13) & 0x08`, i.e. `AC` is the complement of
bit 16 of the raw difference: set when there was no borrow. Checked against the
traced values -- `0x0049 - 0x0000` gives `AC=1`, `0x03cd - 0x0001` gives `AC=1` --
it is right. With `AY0 = 0` an always-set `AC` is the correct answer, not a bug.

Session 91's suspicion that a correction firing 0 times in 597 must be
mis-evaluated was wrong. Sessions 46 and 52 found real defects of this shape;
this is not one. **The emulator is exonerated here.**

### Session 90's L0 hypothesis was wrong twice over

PM `0x19ac..0x19b1` explicitly sets `L0`, `L1`, `L4`, `L5`, `L6` and `L7` to
`$0000` before calling the adapter. The firmware deliberately disables circular
addressing; the zero `L0` observed in Session 90 is intended, not missing.

### The fork, and why changing it does not help

The caller is PM `0x19a7`, and the near/far selection is explicit:

```text
19b8: AY0 = DM($3FBC)          Nearbulklength
19b9: AY1 = DM($3FBD)          BulkLength
19c2: AR = $0002               default
19c3: ASTAT = DM($32F0)        ASTAT restored from a DM word
19c4: IF AC AR = 0 + 1         AC selects AR = 1
19c5: I5 = $3FBC
19c6: CALL $1982
```

`DM(0x32f0)` reads `0x0009` at that instruction -- `AZ|AC` -- so `AC` is set and
`AR` becomes `1`. Worth noting that `attach_connected_bearer()` hard-writes
`self.dm[0x32F0] = 0x0004` with no explanation, and by the time PM `0x19c3` runs
the word is `0x0009`, so that magic constant is both unexplained and overwritten.

Forcing the other branch by NOPing PM `0x19c4`, so `AR` stays `2`:

| | AR = 1 (as-is) | AR = 2 (forced) |
|---|---|---|
| workspace at `0x62` | `03cd 2ad2 0271 0001 0271 0000 0000 0001` | `083a 2ad2 0334 079a 0334 0000 0000 0002` |
| outer states | `0050 0052 0053 0060 0062 0064 0066 0068` then garbage | `0050 0052 0053 0060 0000 0062 0001 0050` -- restarts |

The far path produces a materially different and more plausible workspace: `DM3`
becomes `0x079a` where it was `1`, and `DM0`/`DM4` roughly double. But `DM5` and
`DM6` are **still zero in both**, so the modulo bound `AY0` is still zero and the
fill is still unbounded. The state machine then restarts instead of walking to
`0x0068`. Not a fix.

### Why the far-bulk configuration is unreachable

`AX0 = $0004` at PM `0x1999` is only reached by falling through
`IF GE JUMP $199A` at PM `0x1997`, which needs `AR` negative at PM `0x1996`. `AR`
there is one of: the entry value (`1` or `2`), the constant `1`, or `AY0`. `AY0`
is `DM(I5,M5)` with `I5 = 0x3FBC`, i.e. `Nearbulklength`, observed at `0x0413`.
All positive. So with a positive `Nearbulklength` the far-bulk branch cannot be
taken by any value of the `0x19c4` fork, and `DM6 = AX0 AND NOT AY1` is zero
either way.

That leaves exactly one input: **`Nearbulklength` at `DM(0x3fbc)` would have to be
negative** for this routine to configure a non-zero modulo bound. It is a
DSP-computed read-database word, zero at page entry and `0x0413` by state `0x60`.

### Next

Find who writes `DM(0x3fbc)` and `DM(0x3fbd)` and what they are derived from --
a DM write watch on both, from page-14 load through state `0x60`, gives the writer
PC directly, the same technique that settled `DM4` in Session 90. If those words
are meant to be a signed delay relative to a reference the harness never supplies,
that is the missing input, and it is the last link in the chain from Session 58 to
here.

## Session 93: delaycorrection derives the bulk lengths, and near-bulk is probably right

Two hypotheses were put: that Linux or the firmware supplies the bulk lengths, or
that they relate to the T1/E1 code. The second is correct, at one remove.

### Not written by any host

A DM write watch across page-14 load through state `0x60` gives the writers of
`Nearbulklength` and `BulkLength` directly, and there are **no host writes to
either word at any point in boot**:

```text
625 PM 1a13 -> DM(0x3fbc) = 03cd      624 PM 19e2 -> DM(0x3fbc) = 03ed
625 PM 1a18 -> DM(0x3fbd) = 041d      624 PM 19e4 -> DM(0x3fbd) = 043d
  1 PM 3235 -> DM(0x3fbc) = 0031        1 PM 3ab7 -> DM(0x3fbd) = 0001
```

Two alternating DSP writers each, on chip, once per frame. So Linux does not
supply them and neither does the MIPS.

### But they are derived from a host-supplied delay calibration

`delaycorrection` at write-database `+0x24` (`DM(0x3f04)`) is `0x000c`, supplied by
the card's own 256-word DATABASE transfer (Session 89) and identified in the
Session 22 audit as "the Eicon build's supplementary-buffer calibration". Changing
it changes the lengths exactly:

| `delaycorrection` | `Nearbulklength` | `BulkLength` |
|---|---|---|
| `0x0000` | `0x03c1` | `0x0411` |
| `0x000c` (as shipped) | `0x03cd` | `0x041d` |
| `0x0040` | `0x0401` | `0x0451` |
| `0x8000` | `0x0000` | `0x0000`, workspace corrupted |

So `Nearbulklength = 0x03c1 + delaycorrection` and
`BulkLength = Nearbulklength + 0x50`, to the word. This is the host input into the
bulk workspace that Sessions 58 to 67 were looking for, and it is a span-delay
calibration -- exactly the T1/E1-shaped parameter the hypothesis predicted.

### It is not the fix, and it reframes the last two sessions

Across `0x0000`, `0x000c` and `0x0040` the workspace is identical apart from `DM0`,
`DM5` and `DM6` stay zero, and the run stalls the same way. The failure is
insensitive to it.

More importantly, the far-bulk path needs `Nearbulklength` negative (Session 92),
which needs `delaycorrection >= 0x7c3f`. That is not a delay calibration, it is
nonsense, and the one negative value tried (`0x8000`) zeroes both lengths and
scrambles the workspace into `0054 ff60 0070 ff62 006c ff64 004d ff66`.

**So near-bulk is almost certainly the correct configuration**, and the premise
carried through Sessions 91 and 92 -- that the far-bulk branch is the one that
should have been taken -- is probably wrong. If `AX0 = 0` and therefore `DM6 = 0`
are correct for a near-bulk configuration, then a zero `AY0` is not a defect and
the unbounded sweep has to be constrained by something else.

### The unverified assumption

`AY0` was attributed to `DM5`/`DM6` by inference from the workspace contents, not
by tracing `I1` at the two read sites (PM `0x1917` and PM `0x1921`). That
attribution is now load-bearing for the whole "zero modulo bound" reading and it
has never been checked. Trace `I1` at those two instructions -- the `[EXEC]` line
carries `i1`, so it is the same one-run technique that settled the PM `0x1982`
stores in Session 90. If `AY0` comes from a word that is legitimately non-zero in
a working configuration, the fault moves again; if it really is `DM6`, then
near-bulk genuinely configures no bound and the question becomes what else was
meant to limit PM `0x1930`.

## Session 94: port the driver's AT and IDI layers, and dismantle the V.34 CAI hypothesis

`docs/divas4linux-master/` is the Sangoma/Eicon Linux driver source. Two things in
it are directly useful here: `putcai()` (`tty_module/isdn.c:1209`), which is the
complete CAI builder, and `atPlusMS()` (`tty_module/atp.c:1879`), which is where a
modulation name becomes a disabled mask, an enabled mask and a pair of speed
windows. Both are now ported into `tools/eicon_idi.py`, with the AT command set
`/dev/ttyds*` presents on top in `tools/eicon_at.py`.

The motivation was that this project reached the firmware's modulation fields by
hand. `add_b1()` in `kernel/message.c` — the CAPI path the shim was transcribed
from — cannot express a modulation at all: `cai[10..12]` are reachable only through
the private V.18/VOWN extension, so the shim wrote those bytes itself and left the
rest zero. The tty driver reaches them directly.

The defaults did not change. `modem_sig_assign_payload()` and
`modem_nl_assign_payload()` emit byte-for-byte what they emitted before, pinned by
a test, and the offline replay reproduces.

### One correction to the handoff on the way through

The NL ASSIGN was documented as using the plain `B2_TRANSPARENT` branch. It never
did: `isdn.c:1533` overwrites the protocol map's B2 unconditionally on the modem
branch, so `B2_V42_in` has always gone out. **The DLC, not the LLC, is what
disables error control**, which also means enabling the card's own V.42 is a matter
of dropping the DLC rather than changing the LLC. `EICON_CARD_V42=1` now sends that
payload; it is still untried against hardware.

### The V.34 hypothesis, and its disproof

`atPlusMS()` ORs `unused_modulations` — `~(every disable bit the table names)` —
into any non-empty mask, which covers V.FC, K56flex and X2. That made the old
`EICON_FORCE_V34` (`disabled = 0x0080`) look badly under-specified against the
driver's `0xfc80`, and it looked like the first thing to try on the V.34 blocker.

It is not. An A/B across four configurations on `run34`:

| configuration | CAI disabled | host writes |
|---|---|---|
| default | `0x0000` | 51969 |
| `EICON_FORCE_V34=1` | `0x0080` | 51965 |
| `EICON_MODULATION=v34,1,,33600,,33600` | `0xfc80` | 51965, **identical to the above** |
| `EICON_MODULATION=v34,0,,33600,,33600` | `0xffbf` | 51965, differing |

`v34,1` is byte-identical to the old one-bit force across all 51965 writes. The
`0xfc00` bits never reach the card, so that mask is not worth a live call.

What the comparison does establish is where the CAI's disabled byte lands. Not in
the write database — all 160 words are identical in every configuration, `NORM_L`
holding `0xa13f`, `SPEED_SEL_L` `0xfffe`, `INFO0_SETUP` `0xf1fd`, consistent with
Session 89's finding that the card authors those itself. It lands in the DSP
assignment stream at host data port `0x6802`:

```text
CAI disabled 0x0000          3f00 1fb1 d200
CAI disabled 0x0080/0xfc80   3f00 1f31 d200
CAI disabled 0xffbf          0000 1f01 8000
```

Bit 7 of `0x1fb1` is V.90. Strict mode clears the fallbacks as well and changes
both companion words; the descriptor also shortens, the length word at `0x6800`
going 97 → 89 with four words dropping out of the stream.

The replay itself is uninformative about negotiation and was never going to be:
it is open loop against a V.90 recording, so the page-14 trace and the 9610 TX
datagrams come out identical in all four configurations. Only `v34,0` has any
prospect and only a live call can test it.

Also worth recording: the 56000 Rx ceiling this project sends is not a legal
driver selection. The `v90` row's `rx_map` is the V.34 speed map — the digital
side receives at V.34 rates — so `AT+IE=v90,1,,56000` is an error in the driver
while `legacy_modem_options()` asks for 56000 in both directions. Whether the
firmware minds is untested, and it is the kind of impossible advertisement worth
ruling in or out before blaming the peer.

## Session 95: two emulated cards call each other, and the calling side's gate is found

Every closed-loop test in this project has needed the Courier on a real line.
`tools/eicon_loopback.py` runs two `eicon_adsp_sip.py` instances on loopback,
points one at the other and captures both, so a failed handshake is readable
from both ends at once.

### Signalling direction is not the modem role

The obvious way to build this — teach the card to place an outgoing call — is a
dead end for now, and it was tried. `CALL_REQ` is accepted (`RC 0xff`) and the
firmware does allocate a call object at sig+0x1c, but there is no network to
answer the SETUP, and injecting the connected event 0x03 into the lower-PRI
parser leaves `call_state` at `0x00` and the firmware hangs the call up
(`IND 0x03`). That path is in the tree as `--simulate-outgoing-call` with the
`CALL_REQ` payload ported from `isdnDial()`; it is recorded, not working.

None of it is needed. Which side of the *modem* handshake an instance takes is
GEN_SETUP1 bit 3, not who sent the SETUP, so both instances are driven through
the existing incoming-call path and only that word differs
(`--modem-role`, `EICON_MODEM_ROLE`, GEN_SETUP1 `0x0484`/`0x048c`).

With `--native-mips` both ends boot, assign a modem DSP, exchange RTP and take
opposite roles. The answerer reaches TrnProgress `0x0026`. **The caller parks at
TrnProgress `0x0002` on page 12 and transmits nothing at all.**

### The calling side is inert, and it is not a tone problem

Reproduced without SIP, and then in a deterministic in-process cross-connect
(card A's transmit sample straight into card B's receive slot, one 8 kHz frame
at a time). Side B emits a genuine ANSam; side A's mean |TX| is exactly 0.0
across 12000 frames.

Swept and ruled out — do not re-derive: **ADET** (GEN_SETUP1 bit 0), **Dasen**
(bit 1) and **TonedetEnable** (GEN_SETUP2 bit 6) change nothing in any
combination, against silence and against a real answering pump. Bit 3 is the
only bit that matters.

### The gate, traced

```text
38ac: AR = DM($3EE1)        GEN_SETUP1
38ad: AR = AR AND $0008     bit 3 = CH, call(1)/answer(0)
38ae: AR = $0800
38af: IF EQ AR = 0
38b0: SR1 = AR
38b1: CALL $385B            OR SR1 into DM($046A)
```

GEN_SETUP1 bit 3 is copied into **bit 11 of DM(0x046A)**, which routes the
dial page:

```text
3576: AR = $0002
3577: DM($3FC2) = AR        TrnProgress = 2
3578: AX0 = DM($046A)
3579: AR = AX0 AND $0800
357a: IF EQ JUMP $3675      answering: training start, publishes TrnProgress = 4
357b: AX0 = $35D7           calling: park on this continuation
357c: DM($03EF) = AX0
```

and the continuation never completes:

```text
35d7: AY0 = DM($046C) ; AR = AY0 ; IF LT JUMP $35DD    proceed if DM(046C) < 0
35da: AR = DM($0554) ; AR = AR - $0010 ; IF LT RTS     or if DM(0554) >= 0x10
```

Measured and stable over 400+ frames: `DM(046A)=0x3948` (bit 11 set),
`DM(046C)=0x0064`, `DM(0554)=0x0000`, `DM(03EF)=0x35d7`.

### Which of the two conditions is live

| poke at frame 30 | page | TrnProgress | mean \|TX\| |
|---|---|---|---|
| none | 12 | `0x0002` | 0.0 |
| `DM(046C) = -1` | 12 | `0x0002` | 0.0 |
| `DM(0554) = 0x20` | 12 | `0x0051` | 1812.5 |

`DM(0x0554)` is the gate. It is held at zero by PM `0x3a36`, the tail of a
twelve-word scan of a `-1`-terminated table at `DM(0x056E)` — written 22 times
in 300 frames on the calling side and never on the answering side.

### Interpretation, and what it is not

`DM(0x0554)` reads like the dialler's progress count, with the calling side
refusing to train until the dial page reports the line established. Guide v5.3
§5.4.1 says the calling-mode script runs "when the PSTN connection already has
been established (by means of manual dialling, a data-pump dial script or any
other way)", and our "any other way" never tells the dial page anything. On an
analogue Diva the DIAL page's own dialler fills this in; on a digital span
nothing does.

Treat that as a hypothesis. What is established is the control flow above and
which word gates it.

**The poke is not a fix**: it starts transmission and moves TrnProgress to
`0x0051`, but page 12 stays resident and the V.8 overlay is never requested in
3000 frames. Note also that the standalone `Card` harness skips the
host-command dispatch path (`FRAME_ENTRY_NO_HOST`), which is where a "line
connected" command would arrive — but the native MIPS loopback stalls
identically at `0x0002`, so the real firmware is not sending one either.

Next: find the writer of the table at `DM(0x056E)`. That names the legitimate
way to satisfy this rather than poking a word.

### A caveat about loopback captures

The emulated clock free-runs: the caller reached 130 s of media in ~35 s of
wall time. Both endpoints drain a backlogged receive queue without sleeping,
and pointed at each other they mutually accelerate; a live peer paces it and
this never fires. The DSP is sample-clocked so state observations hold, but
**wall-clock timings in loopback captures are meaningless**.

## Session 96: the calling side waits on a tone detector the card never arms, and why

Session 95 left `DM(0x0554)` as the gate on the calling side and guessed it was a
dialler digit count. It is not. **That hypothesis is superseded.**

### The gate is a tone detector

`DM(0x0554)` is produced by the scan at PM `0x3a2b` over twelve channels at
`DM(0x056E)`, and PM `0x3a22` is what writes them:

```text
3a09: MR = MX0*MY0 + MX1*MY1     correlator
3a0b: ...                         |MR|
3a11: I0 = $056E ; SR1 = DM(I0)  the channel's history word
3a14: SR = LSHIFT SR1 (HI) BY 1  shift it left one place
3a15: AY1 = DM($057C)            threshold, low
3a17: AY1 = DM($057B)            threshold, high
3a19: IF LE JUMP $3A1C           below -> compute the quadrature product
3a21: SR = LSHIFT AR (HI, OR) BY 0   OR the decision bit in
3a22: DM(I0,M0) = SR1            store the channel back
```

Each word is a 16-bit shift register of per-frame decisions against the
threshold in `DM(0x057B:0x057C)` (measured `ea20:fcb2`), and the scan looks for
a channel that has filled with ones. So the calling side is waiting for **tone
detection**, sixteen consecutive frames of it.

### It can never fire

The correlator's state bank at `DM(0x2fc0..0x2fd7)` is all zeros and **nothing
writes it** — zero writers over 300 frames, in both roles. With the inputs zero
the product is zero, the decision bit is always zero, the registers never fill.
Confirmed against a genuine ANSam through the in-process cross-connect: 6000
frames, all twelve channels still `0000`.

The guide names the configuration block — write database **+0x30..+0x4F**,
"information for supervisory tone detection", with the layout in a separate
*DIALLER* document that is not in `docs/`. And the block is empty everywhere:

| source | WDB +0x30..+0x4F |
|---|---|
| standalone `Card`, answer | 32 words, all zero |
| standalone `Card`, calling | 32 words, all zero |
| native MIPS, the firmware's own answer WDB | 32 words, all zero |

**The card's own firmware never arms it either.** This is not a harness
omission. On a digital span there is no analogue line to listen to — no dial
tone, no ringback, no answer tone — so a PRI product has no use for a
supervisory tone detector and does not program one. `GEN_SETUP1 = 0x048c` is
therefore not a supported configuration on this firmware: the dial page's
calling branch waits on a detector this product never arms.

Session 74 saw calling mode "prevent progress" and attributed it to the recorded
peer. The real reason is not about the peer at all.

### On a PRI, dialling is the SETUP

Which raises the obvious question: how does a PRI card originate at all? Through
Q.931, not through the line. The host posts CALL_REQ and the card's protocol
image sends a SETUP on the D channel.

That path was exercised. CALL_REQ is accepted (`RC 0xff`), the firmware
allocates a call object, and it **parses the called party number out of our
request and stores it** — the plan octet and IA5 digits appear at
`0x80100877`, isolated in zeroed memory, without the IE header we sent, and it
is the *only* occurrence in the whole image and RAM. `tools/eicon_mips_shim.py
--scan-ram` finds it in one run; Q.931 encodes the number in IA5, so a message
built for transmission necessarily contains the digits verbatim.

**No SETUP is ever assembled.** The outgoing path stops between parsing the
request and building the message.

Nor can it be pushed along from the receive side. Every lower-PRI signalling
event `0x01..0x20` was delivered after CALL_REQ (`--connect-event`): `call_state`
stayed `0x00` in all 32 cases and the bearer was DISCONNECTED every time.
Events `0x03`, `0x0b`, `0x0c` and `0x0e` provoke a HANGUP indication; the rest
are ignored. The incoming-message parser is not the door for an outgoing call.

### Where the D channel actually lives

From the driver: a PRI card is given exactly two images (`divactrl/load/divaload.c`
around line 2458) — the protocol image `te_dmlt.pm`, which is the MIPS Q.921 and
Q.931 stack, and `dspdload.bin`, from which per-DSP tasks are downloaded. The D
channel's own framing layer is DSP work: `0x0209 SIGPRTX`, `0x020a SIGPRRX`, and
the `0x000b`/`0x000c` "DIVA Server PRI 2M TX/RX SIG Kernel" images. They are
staged in this emulation and never assigned to a core, because nothing here ever
brings a span up.

**Leading hypothesis, untested:** the MIPS never builds a SETUP because Q.921
never establishes. Layer 2 runs on the MIPS but its frames are carried by a SIG
task that is not running, so the datalink cannot come up and Q.931 will not
originate over a down datalink. The answering path works because injecting a
parsed message bypasses layers 1 and 2 entirely.

### What to high-level emulate, and why that boundary

If the hypothesis holds, the boundary worth HLE-ing is the **MIPS-to-SIG-DSP
D-channel queue**, because the payload crossing it is standard Q.921 framing
around standard Q.931 messages. Standing in for the far side means answering
SABME with UA, acknowledging I frames with RR, and delivering inbound network
messages as I frames. Above that, Q.931 then runs normally in both directions,
which is what makes the SIP mapping mechanical:

| Q.931 | SIP |
|---|---|
| outgoing SETUP (called number from CALL_REQ) | INVITE |
| CALL PROC / ALERTING | 100 / 180 |
| CONNECT | 200 OK |
| CONNECT ACK | ACK |

It also subsumes the incoming path: today's injected SETUP becomes an ordinary
inbound I frame instead of a poke at the parser with hand-set controller state.

Next step is to locate that queue. The number-scan technique above is the way
in — pick a distinctive dialled number, and whatever buffer the firmware
assembles for transmission will contain it.

### Ruled out, do not re-derive

- ADET (GEN_SETUP1 bit 0), Dasen (bit 1) and TonedetEnable (GEN_SETUP2 bit 6)
  change nothing on the calling side, in any combination, against silence and
  against a real answering pump. Bit 3 is the only bit that matters.
- `DM(0x046C)` is not the live condition in the `0x35d7` wait; forcing it
  negative does nothing. `DM(0x0554)` is.
- No lower-PRI event in `0x01..0x20` advances an outgoing call.

## Session 97: the D-channel tasks are never assigned, and the outgoing call dies before any DSP

Session 96 proposed the MIPS-to-SIG-DSP queue as the boundary worth high-level
emulating, and left two questions: where the firmware stages an outgoing
message, and whether the SIG tasks run at all.

### New tooling, and two gotchas worth keeping

`--watch-mem ADDR[:LEN]` logs firmware writes into a range with the writing PC;
`--hook-call ADDR[,...]` logs entries to MIPS addresses with `a0..a3` and `ra`.
Together with `--scan-ram` from Session 96 they answer "where did this end up",
"who put it there" and "is this routine reached, with what".

Two things cost time and are worth not rediscovering:

- **`--watch-mem` over a wide range perturbs the run.** Watching
  `0x100800:0x100` makes the SIG ASSIGN fail outright; the same run at `:0x18`
  works and reproduces. Adding a hook changes Unicorn's block boundaries, and
  the shim's `max_insns` budgets are sensitive to it. Narrow windows only, and
  cross-check anything surprising against an unwatched run.
- **Code hooks take the virtual address, write hooks report physical.**
  Unicorn's PC stays in kseg0 while memory is mapped at the physical
  equivalents, which is why `INTERCEPT_ADDRESSES` are unmasked. The first
  `--hook-call` run here masked them and reported zero entries for everything,
  including a routine known to run 4245 times. **Always include a
  known-executed address as a positive control**; the corrected run reports
  `0x800a4108` 4245 times at entry and 30 per call phase.

### Where the dialled number goes, and what is not built

`--watch-mem 0x80100870:24` with a distinctive number:

```text
write 0x08 to 0x00100875 from PC 0x800c9a04 (call-req)   length byte
write 0x81 to 0x00100876 from PC 0x800c9a04 (call-req)   numbering plan
write 0x35.. to 0x00100877..7d from PC 0x800c9a04        the IA5 digits
write 0x00 to 0x00100875 from PC 0x800163d8 (n-connect)  cleared again
```

Nothing is written at `0x100860..0x100874` or `0x100888..0x10089f`, so this is
an isolated length-prefixed field: no IE code byte, no protocol discriminator,
no neighbouring elements. It is the call record's stored called-party number,
placed by the IE-copy helper Session 89 identified at `0x800c99e4`/`0x800c9a04`
and cleared later by `0x800163d8`. Note the clear is why Session 96's scan saw
zeros around it: the length byte is gone by the time the run ends.

**No Q.931 message is assembled anywhere.** That also bounds the scan technique:
content scanning cannot find a transmit queue while nothing is ever queued.

### The SIG tasks are never assigned

Scanning the protocol image for the task download ids as MIPS immediates:

| id | task | immediates in `te_dmlt.pm` | first at file |
|---|---|---|---|
| `0x0209` | SIGPRTX | 3 | `0x097c20` |
| `0x020a` | SIGPRRX | 3 | `0x097c38` |
| `0x000b` | PRI 2M TX SIG kernel | 129 | `0x001028` |
| `0x000c` | PRI 2M RX SIG kernel | 312 | `0x0007f4` |

Both SIG task ids are referenced only inside **`dsp30_assign`**, which
`tools/eicon_dsp_assign.py` places at file `0x9775c..0x97dcc`, virtual
`0x800a875c..0x800a8dcc`. So the MIPS would start the D-channel framing tasks
through the same assignment machinery it uses for the modem.

Hooking that routine, its two SIG-id sites, and `dsp_assign` (file `0x79cc4`,
virtual `0x8008acc4`) for comparison:

| routine | answering (`--simulate-b-channel`) | outgoing (`--simulate-outgoing-call`) |
|---|---|---|
| `dsp_assign` `0x8008acc4` | **32 entries** | **0** |
| `dsp30_assign` `0x800a875c` | 0 | 0 |
| SIG id sites `0x800a8c20`/`0x800a8c38` | 0 | 0 |

Two results. **The D-channel signalling tasks are never assigned in any mode**,
so the framing layer never runs and there is no transport a SETUP could use.
And **the outgoing path never reaches DSP assignment at all**, where the
answering path enters `dsp_assign` 32 times — so the outgoing call dies well
before the point where the answering path does its useful work.

### What this means for standing in as the network

The intuition that the card sends a message and waits for the network to answer
is the right shape, but the card does not get that far. It parses CALL_REQ,
stores the called number, finds no D channel, and stops. Responding to its call
request therefore cannot be done at the Q.931 level, because no Q.931 is emitted;
it has to be done underneath, either by assigning and running the SIG tasks so
the firmware's own Q.921 has a transport, or by high-level emulating the
MIPS-to-SIG queue and standing in for layers 1 and 2.

Next step is to read `dsp30_assign` around `0x800a8c20` for the mailbox layout it
establishes, and to find what would call it — since in a real card something
brings the span up at start of day, and that trigger is absent here.

## Session 98: dsp30_assign is registered and then released, because a DSP fails its boot test

Session 97 established that `dsp30_assign` is never entered and asked what would
call it. It is called through a registration table, and it is deliberately
removed from that table during boot.

### Neither assign routine is called directly

A `jal` scan of `te_dmlt.pm` finds **no direct caller** of either
`dsp_assign` (`0x8008acc4`) or `dsp30_assign` (`0x800a875c`), and neither
appears as a data word. The scan is sound: the same method finds 51 callers of
the IE helper at `0x800c99e4` that Session 89 already documented. Both are
reached indirectly.

`--hook-call` on `dsp_assign`, which does run, gives the return address
`0x8002aa54` in every case, and the site is a table walk:

```text
8002a9dc: lui  $t2, 0x8012 ; addiu $t2, $t2, 0x2280   base + 4
8002a9e8: lw   $t0, ($a0)          the entry's data word
8002a9ec: beql $t0, $zero, ...     skip this entry when it is zero
8002a9f4: lui  $t1, 0x8012 ; addiu $t1, $t1, 0x227c   base
8002aa00: lw   $v0, ($v1)          the handler
8002aa4c: jalr $v0
```

### The table, and what happens to it

`--watch-mem 0x8012227c:0x18` during boot:

```text
write 0x800a875c to 0x0012227c  ra=0x800a6e5c   dsp30_assign   handler
write 0x8027c830 to 0x00122280  ra=0x800a6e5c   its data
write 0x8008595c to 0x00122284
write 0x8008acc4 to 0x00122288                  dsp_assign     handler
write 0x80272a24 to 0x0012228c                  its data
write 0x8008595c to 0x00122290
write 0x00000000 to 0x00122280  ra=0x800822fc   dsp30's data, nulled
```

Twelve-byte entries of `{handler, data, common}`. **`dsp30_assign` is slot 0 and
is registered normally** — then its data word is zeroed, and the dispatcher's
`beql $t0, $zero` skips it forever. It is not missing; it is released.

### Why it is released

The store is in a lookup-and-remove routine entered at `0x8002b934`, called from
`0x800822f4` and guarded three instructions earlier:

```text
800822e8: bnez $v0, 0x80082304    non-zero: keep the service
800822f0: lw   $a1, 0x10c($s4)
800822f4: jal  0x8002b934          release it
800822fc: sw   $zero, 0x10c($s4)
```

`$v0` is the result of the per-DSP boot test immediately above:

```text
80082250: jal 0x800a77e0 ; lw $a0, 0x10c($s4)   stream the kernel in
80082258: beqz $v0, 0x800824a0                   download failed
80082260: jal 0x800a7940 ; lw $a0, 0x10c($s4)   poll for the acknowledgement
80082268: beqz $v0, 0x800824b4                   no ack
```

Those are the two routines this file's own `DSP_BOOT_PROBE`/`DSP_BOOT_ACK`
constants already name. Both failure branches print the same string, and the
success path names what is being decided:

```text
0x800edd68: '[%d] DSP test failed'
0x800edd80: '[%d/%d] DSP OK, 23/30 channel mode'
```

### What this says, and what it does not

Established: the 30-channel service entry is registered at boot and then
released, the release is conditional on a DSP boot test, and that test is the
download-plus-acknowledge handshake the shim already reports on.

Inferred, and worth checking before relying on it: that `dsp30_assign` is the
**30-channel (E1) variant** of DSP assignment rather than a signalling-specific
routine — the message pairs 23 and 30 channels, which is the T1/E1 split, and
`dsp30_assign` is where the SIGPRTX/SIGPRRX ids appear because the E1 signalling
tasks differ. If that reading holds, the D-channel path is unreachable here for a
mundane reason: **`report_dsp_boot()` has been saying so all along** — "31 cores:
30 answered the boot handshake with 0xa5a5, 1 still held (no download)". One core
never gets a download, fails the test, and takes the 30-channel service with it.

Next step is therefore much cheaper than the HLE work Session 96 scoped: make
every core complete the boot handshake, then re-run the `--hook-call
0x800a875c` probe. If `dsp30_assign` starts being entered, the D-channel tasks
come up on their own and the queue Session 96 wanted to emulate becomes
observable instead of hypothetical.

## Session 99: the held core was a phantom, and it was not the cause

Session 98 inferred that `dsp30_assign` is released because a DSP fails its boot
test, and that the failing DSP is the one `report_dsp_boot()` has always
reported as "1 still held (no download)". **The first half stands; the second is
wrong.** The held core was an artifact of this emulation, and removing it
changes nothing about the release.

### The held core was never a DSP

The held block is `0x1c000020`. Watching it:

```text
7 writes, 1 byte each, values 0x00 and 0x12, from PC 0x80082ec4
0 writes to its IDMA address port at +0x80
```

against `0x1c000008`, the genuine on-board DSP, which takes 3345 two-byte writes
from `0x80082a80` — an actual download stream. And the writer is a helper:

```text
80082eb0: andi $a1, $a1, 0xff
80082eb4: lui  $v0, 0x8027 ; addiu $v0, $v0, 0x28c8    the card object
80082ebc: lw   $v1, 0x84($v0)
80082ec0: sb   $a1, 0x88($v0)
80082ec4: sb   $a1, ($v1)                              *(card+0x84) = byte
```

`0x802728c8` is the same card object the DSP scan carries in `$s4`. So
`0xbc000020` is a **card control register**, not the second on-board DSP the
shim's own comment claimed, and the firmware brackets its thirty-DSP init loop
with writes of `0x00` and `0x12` to it.

Routing those into the IDMA path spawned a phantom 31st core that could never be
downloaded — precisely the hazard the range comment warns about — and made every
run report a held core that does not exist. `CARD_CONTROL_REGISTER` now excludes
it. Boot reports `30 cores: 30 answered, 0 still held`, and the answering path is
unchanged: B-channel ACTIVE, `service_assign=1`, `switch_on=1`, and the 17 s
replay is byte-identical at 72.4% TX and 9610/9610 datagrams.

### It was not the cause

With the phantom gone, the release still fires from the same site, and
`dsp30_assign` is still never entered. The per-DSP guard at `0x800822e8` is hit
thirty times with `a1` counting `0x00..0x1c`, and **only the last one, index
`0x1c`, has `v0 = 0`** — before and after the fix, identically. So the 30-channel
service is released because index 28 of the scan fails, for a reason that has
nothing to do with the emulation's core bookkeeping.

Session 98's next-step suggestion — make every core complete the handshake and
the D-channel tasks come up on their own — is therefore retired. They all
complete it now, and nothing changed.

### A method correction

Session 98 read the release site's caller from `ra`. That was sound there
because `0x8002aa54` is a genuine `jalr` return, but it is **not** sound for a
hook on a mid-function address: `ra` is whatever the last call left behind.
`ra = 0x80082378` at the `0x800822e8` guard is a stale value pointing at
`lbu $v0, 0x108($s4)`, a counter increment on an unrelated path, not the code
that set `$v0`. Hook function entries when you want callers; for a branch inside
a function, `ra` means nothing.

### What is still open

What sets `$v0 = 0` for index `0x1c` and nothing else. The download and
acknowledge routines at `0x80082250`/`0x80082260` are **never entered** on this
path, so the guard's `$v0` is produced somewhere else in the scan, and the
Session 98 reading that it is the download-plus-ack result is unproven. That is
the next thing to establish, and it wants a hook on the scan's own function
entry rather than on the branch.

## Session 100: the loopback caller reaches V.34, and three defects were in this harness

The loopback rig from Session 95 now carries both ends through V.8 to a V.34
page load. Nothing in the firmware needed changing; all three faults were in
this harness, and each one was hiding the next.

### 1. The media clock started when the Call object was created

`Call.next_tick` defaulted to `time.monotonic()` at construction, which is SIP
setup time -- before the ring cadence and before several seconds of firmware
boot. By the time media actually started the endpoint owed every quantum in
between and served them at full CPU speed, two per wake-up but with the
selector returning immediately.

Measured on the answerer: **133 RTP packets in the caller's first captured
second against a steady 50**. The caller's receive queue hit its 3840-sample
high-water and discarded 9440 samples -- 1.18 s, and what was in it was the
start of ANSam. The caller then timed out of V.8, fell to V.22, then to FSK,
and both ends reported a "connect" at TrnProgress `0x00b0` that was a 300 bit/s
FSK link. That is the "V.8 falls back to V.22/FSK" of the previous commit.

Two changes, both in `eicon_adsp_sip.py`: start the media clock on the first
tick rather than at construction, and **boot the calling card in `dial()`
before the INVITE goes out** rather than on the 200 OK. The second is what
removes the skew -- firmware entry is several seconds, and doing it after the
answer means the answerer has been sending that whole time. A real modem is
initialised before it dials.

After this the two endpoints are aligned to the packet: the answerer's ANSam
starts at 0.40 s of its own clock and appears at 0.40 s in the caller's receive
capture, with zero drops and zero substitutions on either side.

For the record, the answerer's ANSam is correct and always was: 2100 Hz, 15 Hz
AM (79.2 against a noise floor under 1.6 in the envelope Goertzel), and 180
degree phase reversals every 450 ms.

### 2. NORM_L was being written into a read-database status word

The write database starts at **DM 0x3EE4**, not 0x3EE0. GEN_SETUP0 is 0x3EE4
and GEN_SETUP1 -- `0x0484` answer / `0x048c` calling -- is 0x3EE5, which is
what the V.8 page's own role tests at PM `0x37c3`/`0x37c8` read (`AR =
DM($3EE4) AND $0800` and its complement). Every `+0xNN` in this file's write-DB
notes is relative to 0x3EE4: INFO0_SETUP `+0x07` is 0x3EEB, NORM_L `+0x29` is
**0x3F0D**, SPEED_SEL_L `+0x2b` is 0x3F0F.

The previous commit's NORM_L fix used 0x3EE0 as the base and therefore wrote
`0xb13f` into **DM 0x3F09**, a read-database status word whose bit 13 the V.8
detector branch at PM `0x37f1` tests. With the address corrected the caller's
V.8 state machine stopped stalling at state 2.

Also visible in the same diff, and still open: the caller's SPEED_SEL_L is
`0x00c0` where the answerer's is `0xfffe`.

### 3. A page request for the resident page re-entered it mid-handshake

`_serve_page_request` fired whenever `DM(0x3FC1)` bit 8 and `DM(0x3131)` were
both set, and served whatever descriptor `DM(0x3132)` held. Nothing cleared
`DM(0x3131)`, and for the forced originate V.8 request (`ORIGINATE_V8`) nothing
ever could -- the shim writes it from outside. So the request re-fired, with
`DM(0x3132)` still holding `0x025F`, and the V.8 entry path ran again: it
zeroes the TX word `DM(0x3764)` and both timer sentinels `DM(0x3995)`/
`DM(0x3999)`. That landed in the middle of ANSam detection, at 1.62 s, exactly
at the state 2 to 3 transition.

The shim now acknowledges a request naming the resident page without
re-entering it. This is the change that made the caller transmit.

### The V.8 state machine, for whoever needs it next

The V.8 page's sequencer is a script interpreter. `DM(0x049F)` is the script
pointer; PM `0x37b7` walks (field, value) triples into `DM(0x073F + field)`
until field `0x11`; field `0x0C` is `DM(0x074B)`, whose low byte PM `0x3799`
publishes as TrnProgress. Each block installs three test routines
(`DM(0x0792..0x0794)`) and two alternative script pointers
(`DM(0x0790)`/`DM(0x0791)`); the tests are called at PM `0x37a5`/`0x37a9`/
`0x37ad` and a `LE` return either advances or branches. The useful ones:

| PM | test |
|---|---|
| `0x37d5` | constant 1 -- never fires |
| `0x37d7` | countdown of `DM(0x0749)` |
| `0x37f7` | `0x0780 - DM(0x07BD)`, the energy hit counter |
| `0x37dc` | `0x00F0 - DM(0x0778)`, the tone-classifier confidence |
| `0x37c3`/`0x37c8` | GEN_SETUP0 bit 11 |

`DM(0x07BD)` is incremented at PM `0x3ec8` when a filter magnitude beats the
threshold in `DM(0x0748)` (`0x07d0`); `DM(0x0778)` at PM `0x3f0d` against
`DM(0x0747)`. Watching PM `0x37a6`/`0x37aa`/`0x37ae` and reading `i4` and `ar`
out of the `[EXEC]` line is how all of the above was read, and it is cheap.

### Where it gets to now

```text
caller                                     answerer
0.08  V.8 page resident                    0.02  V.8 page resident
                                           0.54  ANSam (state 4)
1.24  ANSam confirmed (state 2)
2.10  transmits CM -- V.21 ch1             2.14  hears it
3.04  INFO page (7)                        3.04  INFO page (7)
5.20  V.34 page (8) requested and loaded   5.20  V.34 page (8), TrnProgress 0x0071 -> 0x0072
5.24  falls back to page 11, then page 0
5.44  TrnProgress 0x2f3e
```

So **V.8 selects V.34 and both ends load the V.34 page.** The answerer settles
at TrnProgress `0x0072`; the caller collapses within 40 ms of the load, walking
bootpage 8 -> 11 (AT offline) -> 0 (DIAL) and then publishing a garbage state
word. That is the next thing to look at, and it is a different problem from
everything above: the handshake is now good enough to get there.

Second open item from the same run: once page 8 is resident **neither endpoint
holds real time** -- the pacing ratio falls to 0.65x with thousands of clock
holds on both sides. V.34 costs more per sample than the 20 ms budget allows,
and because each end waits for the other they decelerate together. Loopback
observations of *state* stay valid (the DSP is sample-clocked), but anything
timing-derived after 5.2 s is not.

## Session 101: the caller's V.34 collapse is the echo canceller's unbounded fill

Session 100 left the loopback caller loading the V.34 page and collapsing 40 ms
later — bootpage 8 → 11 → 0 and a garbage state word. It is the near/far echo
bulk-delay adapter of Sessions 58–93, caught in the act on a page that is not
V.90.

### The abort is a deliberate branch in the V.34 page's entry

PM `0x27dd` is the V.34 page's per-frame entry: it reloads `L0..L7`, `M0..M6`
and `MODE_CTL`, then

```text
27eb: 821650  AX0 = DM($2165)
27ec: 22780f  AR = AX0 + 0
27ed: 1a90c1  IF NE JUMP $290C
```

and `0x290C` is the give-up path — it zeroes `DM(0x3FA7..0x3FA9)`, sets the
boot-request bit `0x0100` in `DM(0x3FC1)`, and boots whatever page number is in
`DM(0x2252)`:

```text
2910: AX0 = DM($3FC1)
2911: AR = AX0 OR $0100
2912: DM($3FC1) = AR
2913: AR = DM($2252)
2914: DM($3FB0) = AR
```

`0x27ed` is the only reference to `0x290C` in the overlay, and an exec watch
confirms the caller entered it from `0x27ed`, twice, while the answerer never
entered it at all. `DM(0x2252)` read `0` — DIAL — which is why the caller went
to page 0.

`DM(0x2165)` has exactly one writer in the V.34 overlay (PM `0x2a08`, which
sets it to 1) and is zero in both the V.34 and INFO overlay DM images. So a
nonzero value there at page entry is not the firmware latching anything.

### What wrote it

A DM write watch on `0x2160..0x2168` names the writer:

```text
dm w 2161=2161 ppc=1930 cyc=115220972 i0=2161
dm w 2163=feea ppc=1930 cyc=115222943 i0=2163
dm w 2165=2859 ppc=1930 cyc=115225680 i0=2165 mr0=2859
dm r 2165=2859 pc=27ec cyc=115225943      <-- the entry test, 263 cycles later
dm w 3fb0=0000 ppc=2914 cyc=115225954     <-- the abort
```

**PM `0x1930` is the bulk-delay adapter's store**, the instruction Session 90
traced sweeping `I0` linearly across 1556 addresses because its modulo bound
reads zero (Sessions 91–93). Here it walks up through the V.34 page's own
variables at DM `0x2160..0x2167` — further than the `0x0049..0x1b41` range
Session 90 measured — and one of the words it lands on is the page's abort
flag.

The asymmetry is stark. Counting writers into `0x2160..0x2168` over one call:

| writer | caller | answerer |
|---|---|---|
| PM `0x1930` | 6 | 0 |
| PM `0x1934` | 7 | 0 |

The adapter's cursor simply happened to be over that block on the caller. This
is the same lottery as the DIL blocker, with a victim that is much easier to
read: a single flag with a single test and an unambiguous consequence.

### Confirmed by removing it

`EICON_V90D_BULK_ADAPTER` already RTSes out the adapter's tail at PM `0x19c8`
when page `0x026A` loads. Extending that to `0x0261` removes the collapse
outright: the caller stays on page 8, and the two ends then loop symmetrically

```text
INFO (page 7) ~1.8 s -> V.34 (page 8) ~0.25 s -> INFO ...
```

for as long as the call runs — ten identical round trips in 26 s, both ends
switching within 20 ms of each other.

Note this does not make the harness *more* wrong: PM `0x1900..0x19c8` is
resident kernel, it was live on every non-V.90 page, and what it was doing was
corrupting page state. It is still a real functional gap, and it is still the
same unfixed defect.

### What is open now

Inside each V.34 attempt the two ends diverge:

| | caller | answerer |
|---|---|---|
| TrnProgress | `0x0060` for the whole 280 ms | `0x0071` → `0x0072` → `0x0074` → `0x0090` |

`0x0060` is the caller's page-entry state. The answerer walks phase 3; the
caller never leaves entry, so it transmits nothing for the answerer to train
against and both fall back to INFO. That is the next question, and it is the
same shape as the V.8 one Session 100 answered: an originate-side page that
loads and then does not start.

## Session 102: the V.34 caller parks on a silence that never comes, because INFO published nothing

The caller's V.34 page loads, publishes TrnProgress `0x0060` and never moves.
It is not stuck: it is waiting, correctly, for a condition its peer never
produces, and it is waiting there because the INFO page handed it a zero.

### The V.34 page's role fork, and how the two scripts are stored

The V.34 page uses the same script-interpreter shape as V.8. `DM(0x14A5)` is
sequencer A's script pointer, `DM(0x2192)` is sequencer B's, blocks are
(field, value) entries three words long terminated by field `0x19` (A) or
`0x24` (B), and field `0x10` lands in `DM(0x2147)`, which PM `0x2d83`/`0x2ddd`
publish as TrnProgress. Record base is `0x2137`, so:

| field | address | meaning |
|---|---|---|
| `0x0E` | `DM(0x2145)` | detector threshold |
| `0x0F` | `DM(0x2146)` | countdown, tested by PM `0x2e32` |
| `0x10` | `DM(0x2147)` | state → TrnProgress |
| `0x11..0x14` | `DM(0x2148..0x214B)` | branch targets, index into `DM(0x0676)` |
| `0x15..0x19` | `DM(0x214C..0x2150)` | test routines, index into `DM(0x064B)` |

The fork is at PM `0x1046`:

```text
1046: AR = DM($3F94)
1047: AR = AR AND $0008
1048: DM($2198) = AR
```

`DM(0x3F94)` bit 3 is the GEN_SETUP1 role bit, so `DM(0x2198)` is 8 on the
caller and 0 on the answerer. PM `0x2d6e` reads it and picks *the record
decoder*:

| `DM(0x2198)` | decoder | sequencer B script | measured |
|---|---|---|---|
| non-zero (calling) | PM `0x2E1A` — **low** byte of each word | `0x1EA2` | caller |
| zero (answer) | PM `0x2E24` — **high** byte of each word | `0x1E81` | answerer |

Both roles share sequencer A's script base `0x1A2E`. The two scripts are
**byte-interleaved into the same words** — one role reads the low bytes, the
other the high bytes. Confirmed live: `DM(0x2192)` takes `0x1ea2` on the caller
and `0x1e81` on the answerer, and the record stores land at PM `0x2e21`
(68,603 times) on the caller against PM `0x2e2d` (19,280) on the answerer.

That also settles the role question before it is asked: the caller is on the
correct half.

### Where the caller goes, and why

Decoding `0x1A2E` low-byte and following `DM(0x14A5)` live gives:

```text
1a2e -> 1a6d -> 1a79 (state 0x53) -> 1a91 (state 0x54) -> [branch] -> 1ae5 (state 0x60) <-> 1af7
```

Block `0x1a91` carries `test0 = PM 0x2ef1` with branch target `0x1ae5`, and

```text
2ef1: AR = DM($3F89)
2ef2: JUMP $2ED1        ; AR + 0, RTS  -> fires when DM(0x3F89) == 0
```

`DM(0x3F89)` is zero, so the caller branches straight to `0x1ae5` and skips
states `0x56`, `0x58`, `0x5a`, `0x5c`. Measured over sixteen V.34 attempts in
one call: evaluated 16 times, fired 16 times.

Block `0x1ae5` decodes to

```text
state 0x0060   threshold(0x0E) = 0x02bc   timeout(0x0F) = 50
test4 (primary) = PM 0x2e32   -- the countdown; on expiry run the next block
test0           = PM 0x2ef3   -- branch target 0x1ae5, i.e. itself
```

and PM `0x2ef3` reads, then clears, `DM(0x13BF)`. That flag is set by the
kernel's six-tap detector:

```text
0e36: AR = ABS MR1
0e37: AY0 = DM($2145)        ; the threshold the block just armed
0e38: AF = AR - AY0
0e3a: IF GT AR = 0 + 1
0e3b: DM($13BF) = AR
```

So state `0x0060` on the originate side means **"wait until the line has been
quiet for 50 ticks"**, and every detection re-enters the block — which reloads
its own timeout. Watching PM `0x0e3a` live, the caller's `|MR1|` oscillates
across the threshold (`0x0053`, `0x0268`, `0x03b4`, `0x0505`, `0x07cb` against
`0x02bc`), firing about every other evaluation. The countdown reads `0x31`,
`0x31`, `0x31`, `0x30`, `0x31` — it never gets near zero.

The answerer's own state-`0x0060` block, `0x1adc` in the high-byte script, is a
different thing entirely: timeout 128, `test0 = PM 0x2e6c` (which is `AR = 0+1;
RTS`, a placeholder that never fires), and no self-branch. It leaves on its
timer, which is why the answerer walks `0x0071 → 0x0072 → 0x0074 → 0x0090`
while the caller sits still.

### The zero that put it there

`DM(0x3F89)` has exactly one writer in the whole tree, and it is in the **INFO
overlay**, at PM `0x3dfd`, in the block that publishes phase 2's results:

```text
3df1: DM($3FBB) = SR0     BaudInfo
3df9: DM($3F88) = SR0     from DM(0x1703), DM(0x1704), DM(0x16FC), DM(0x16FD)
3dfb: DM($3F8A) = AR      from DM(0x16FE)
3dfd: DM($3F89) = AR      from DM(0x1705)
3dff: DM($3F8B) = AR      from DM(0x0609)
3e01: DM($3F8C) = AR      from DM(0x1706)
```

Read out of the capture at the moment page 8 loads, on both ends:

```text
3FBB=0x30dd  3F88=0x0000  3F89=0x0000  3F8A=0x0000  3F8B=0x0000  3F8C=0x0000
```

**Only BaudInfo is published.** The whole received-parameter group is zero. The
routine ran — `0x3FBB` proves it — so `DM(0x1703..0x1706)` and
`DM(0x16FC..0x16FE)` are themselves empty when INFO hands over.

Two corollaries worth recording:

- `DM(0x3F8A)` is the retrain reason code, not a parameter: it reads `0x5678`
  for exactly one frame at each fallback, written by PM `0x2d66` on the path
  that also sets `DM(0x2252) = 7` (INFO). `0x5679` is its sibling at PM
  `0x2d61`.
- **`DM(0x3F8B)`, the "DIL flag" this file has logged since Session 87, is one
  of these INFO-published words** (from `DM(0x0609)`), not an independent
  measurement. It is zero for the same reason the rest of the group is. That is
  a better explanation of why it split nine captures perfectly and then failed
  than "nine samples was not enough".

### What to do next

Find why the INFO page's `DM(0x1703..0x1706)`/`DM(0x16FC..0x16FE)` are empty at
handover. The INFO phase visibly runs to completion on both ends — TrnProgress
walks `0x0022` through `0x004f` in lockstep and BaudInfo comes out — so this is
a parse or store that is not happening, not a phase that did not run. `DM(0x1705)`
is the single word that decides the caller's first V.34 branch, so it is the one
to trace first: find its writer in the INFO overlay and watch what that writer
sees.

## Session 103: INFO does publish — the fields V.34 needs are the ones that come out empty

Session 102 asked why `DM(0x3F88..0x3F8C)` are zero when the INFO page hands
over. The answer is narrower than "INFO published nothing", and the correction
matters: the page runs its whole receive cycle, the message framing works, and
real side-specific content does arrive. What is empty is the first packed word,
which is where every field the V.34 originate script reads happens to live.

### The publication is a bit-field split of one word

PM `0x3d6f` (and its twin at `0x3e1c`) is the whole of it:

```text
3d6f: SR0 = DM($060A)
3d71: AR = SR0 AND $0007   -> DM($1703)      bits 0..2
3d75: AR = SR0>>3 AND 7    -> DM($1704)      bits 3..5
3d79: AR = SR0>>6 AND $7F  -> DM($1705)      bits 6..12   -> DM($3F89)
```

`DM(0x060A)` reads **`0x2000`** — bit 13, and nothing below it. So all three
fields are zero for one reason, not three, and `DM(0x3F89) = 0` is what parks
the V.34 caller at state `0x0060` (Session 102).

### Where that word comes from, and it is not truncated

`DM(0x0608..0x060E)` is filled by the message packer at PM `0x358E`, five words
per message, stored at PM `0x3597`. The destination is selected by length:

```text
3576: AY0 = $0110              ; or $01E0 when DM(0x3F94) bit 1 is set
3577: AX0 = DM($3F94)
357c: MR0 = DM(I1,M1)
357d: AX0 = DM($1651)
357e: AR = AX0 - AY0
357f: IF EQ JUMP $3588         ; scripted length matches -> store 5 words at 0x0608
3580: AR = DM(I5,M5)           ; otherwise skip two, i.e. store at 0x060A
3581: AR = DM(I5,M5)
```

`DM(0x1651)` is not measured — it is field `0x0F` of the INFO page's own script
record (base `0x1642`), and **the INFO page uses the same dual-decoder,
byte-interleaved script the V.34 page does**: PM `0x336A` reads the low byte of
each three-word entry, PM `0x3376` the high byte, and live the caller writes
through `0x3372` while the answerer writes through `0x3380`. Same fork, same
table, same trick as Session 102's `DM(0x2198)`.

Both layouts are exercised, and the layout selection is correct. Measured over
one call, per INFO residency:

| | first message | later messages |
|---|---|---|
| `DM(0x1651)` | `0x0110` = the hardcoded value | `0x0260` (caller) / `0x04d0` (answerer) |
| stored at | `DM(0x0608..0x060C)` | `DM(0x060A..0x060E)` |

and the V.34 handover is fed by the second layout, which is why the extractors
read `DM(0x060A)` onwards.

The packed words themselves:

```text
caller    0608..060C: 21fd 0000 0000 0000 8000
          060a..060e: 2000 0b78 0000 0000 4000
answerer  0608..060C: 21fd 0000 0000 0000 4000
          060a..060e: 2000 8068 0100 0740 c007
```

`0x21fd` is identical on both ends — a preamble, not data. Everything after it
**differs between the two sides**, so the receiver is genuinely demodulating the
peer and not echoing a constant. The content is simply concentrated in word 1,
and word 0 — the only word the field extractors touch — carries one bit.

### The soft-decision array is sparse, not short

The packer consumes 80 entries from `DM(0x068E..0x06DD)`, one per output bit,
where `±1` is a decision and `0` is "never written". Dumping all 80 for the
invocation that feeds the first V.34 load (`.` = no decision):

```text
word 0: 0x068e..0x069d   ...........0....
word 1: 0x069e..0x06ad   .0111.01.0......
word 2: 0x06ae..0x06bd   ................
word 3: 0x06be..0x06cd   ................
word 4: 0x06ce..0x06dd   ................
```

Eight decisions out of eighty. This corrects a first reading of the same data:
the array is not truncated after the first sixteen entries — entry 79 is
non-zero on every invocation on both ends — it is *sparse*, roughly one slot in
eight.

And it is not sync thrash. Watching the parser pointer `DM(0x16BD)`, which the
page moves between `0x3520` (sync hunt), `0x3546` (collect) and `0x3561`
(tail), the cycle completes exactly **eight times per call on both ends** — one
clean pass per message, no re-syncs.

### The next question, and it needs no live call

The collector and the packer disagree about slot cadence, and that is the thing
to settle:

- PM `0x3546` writes `DM(I1)` with `M0 = 0` — the same slot — on **every**
  symbol, shifting the new hard decision (`DM(0x060F)`, `AX1 >= 0x0578` at PM
  `0x3513`) in from the bottom. It advances `I1` only at PM `0x3559`, and only
  when the 16-word receive window at `DM(0x0620)` wraps, i.e. once per sixteen
  symbols.
- PM `0x358E` consumes **80 consecutive slots**, one per output bit.

One slot per sixteen symbols against one slot per bit is a factor of sixteen,
and it is the right size to explain one decision in eight. Establishing which
side is being read wrongly — most likely a misread of the `DM(I0,M0)`/`L0 =
0x0010` circular window set up at PM `0x3517` — is a static question about PM
`0x3546` and PM `0x358E` plus one `I1` trace, and it does not need the loopback
at all.

## Session 104: there is no cadence mismatch — the factor of sixteen is the oversampling

Session 103 ended by naming a "slot cadence mismatch": the collector at PM
`0x3546` advancing its destination once per sixteen symbols against the packer
at PM `0x358E` consuming one slot per output bit. **That mismatch does not
exist.** The two are the same cadence, and the factor of sixteen between them is
the receiver's oversampling. This session is the measurement that retires it,
and no code changed.

### What the collector actually does

Watching PM `0x354d` (the per-symbol store) and PM `0x3559` (the `MODIFY (I1,
M1)` that advances the slot) over one call, grouped by message:

| message | decisions collected | slots visited | decisions per slot |
|---|---|---|---|
| first, both ends | 272 | 17, `0x068c..0x069c` | 16.0 |
| later, caller | 608 | 38, `0x068c..0x06b1` | 16.0 |
| later, answerer | 1232 | 77, `0x068c..0x06d8` | 16.0 |

272, 608 and 1232 are exactly the `DM(0x1651)` values Session 103 recorded as
`0x0110`, `0x0260` and `0x04d0`, and 272/16 = 17, 608/16 = 38, 1232/16 = 77.
So `DM(0x1651)` is the message length **in hard decisions**, the collector runs
for exactly that many, and it packs sixteen consecutive decisions into each
slot. Every number is self-consistent to the symbol.

The receiver therefore runs at **16x oversampling**: each slot is one symbol's
worth of decisions, which is why slots read `0x0000` and `0xffff`. The packer
taking one bit per slot is the decimation back to symbol rate — the same
cadence, expressed once per symbol on each side.

The bit it takes is chosen deliberately. `SE` is `-1` throughout the packer
loop (measured at PM `0x3591`, all 80 iterations, both ends), so `SR0 = MR0 >>
1` and the extracted bit is bit **1** of the slot, not bit 0. Slots reading
`0x0001` and `0xfffe` — one decision of the next symbol having landed in this
slot — appear throughout the collector trace, and picking bit 1 is exactly what
makes the decimation immune to that one-decision boundary jitter.

### The observation it was built on was also wrong

Session 103 read a slot of `0x0000` as "never written" and concluded the array
was sparse — eight decisions in eighty. A slot of `0x0000` is **sixteen
consecutive zero decisions**. Measured at the collector's own store, `0x0000` is
81.7% of stores on the caller and 84.8% on the answerer, with the rest being a
run of ones shifting through (`0x0001`, `0x0003`, `0x0007`, … `0x7fff`,
`0xffff`). The array is fully written every time; the received bitstream is
simply mostly zeros.

That also disposes of the level worry left over from Session 102. The hard
decision at PM `0x3513` (`AX1 >= 0x0578`) comes out **1 for 54.3% of symbols on
the caller and 64.8% on the answerer**, over a sharply bimodal magnitude
distribution — 40,364 samples in `0x0000..0x00ff` against 40,346 in
`0x0f00..0x0fff` on the caller. The slicer is not marginal and the level is not
the problem.

### What is left, stated without the error

Two things survive, both narrower than the retired claim.

- **The packer's extent is fixed while the message length is not.** PM
  `0x3588..0x358d` is five hardcoded calls, 80 slots. The collector writes 17,
  38 or 77. On the caller's 38-symbol message, packed words 2 to 4 are slots 38
  to 79 — memory the collector never touched this pass. The answerer's
  77-symbol message very nearly fills the 80, which says the five-word packer is
  sized for *that* message, not the caller's.
- **The two ends expect different lengths, and the expectation is local.**
  `DM(0x1651)` is field `0x0F` of the INFO page's own script record, so each
  side's expected length comes from its own half of the byte-interleaved script:
  608 decisions on the caller, 1232 on the answerer. Whether each side is
  expecting the length its *peer* transmits — rather than the one it transmits
  itself — is the thing to check, and it is checkable by measuring how long each
  end's INFO transmitter runs and comparing that with the other end's
  `DM(0x1651)`.

The V.34-visible symptom is unchanged: word 0 of the caller's received message
packs to `0x2000`, so bits 0..12 — every field `DM(0x1703)`, `DM(0x1704)` and
`DM(0x1705)` is cut from — are zero, and `DM(0x3F89) = 0` parks the V.34
originate script at state `0x0060`.

## V.90 TX mailbox and TIKRNL ownership notes

The host-facing V.90 synchronous TX interface is `DM(0x3F05..0x3F07)`
(`TXD0..TXD2`), with `DI_control` bit 15 (`DM(0x3FAD)`) requesting a new
packet. In V90D mode TXD0 bit 0 is the oldest bit; one request carries 21--42
bits across the three words.

The resident `0258` TIKRNL task also owns these words. In the extracted task PM
`0x06D0` writes `TXD0 = 0xFFFF`, while PM `0x0732/0x0734`, `0x0738`, and
`0x0740` write TXD0, TXD1, and TXD2 from its internal bearer store. Those are
relocated to `0x06D7/0x0739/0x073B/0x073F/0x0747` in the live build-117-926
core. A host write was therefore overwritten in the same request; a later host
rewrite and a cleared request bit proved neither which owner won nor which words
the modem page consumed. Explicit host-source mode now suppresses this exact
five-store signature; see the end-to-end result below.

`DM(0x3FC0)` and `DM(0x3FC1)` are `RSTATUS_CH` and `RSTATUS`, not TX-buffer
ownership flags. Their `0x0400` bits are status/change state and must not be
fabricated as a TX handshake. The authoritative mailbox request/consume bit
is `DI_control` bit 15.

The task derives its internal TX word-count state in PM `0x05D6..0x05E6`,
using `DM(0x3F09..0x3F0B)` and the private lookup table at
`DM(0x31EE..0x31F4)`, then stores the result in `DM(0x31B2)`. The table was
observed cleared during the V.90 overlay handoff and is restored by the native
shim from the `0258` task image. The negotiated rate word remains authoritative
for the host packet width; mailbox ownership is no longer inferred from this
private word-count state.

## NL N_DATA bearer path: requests are posted without completion flow control

The live CX run with `EICON_V42_NL_DATA=1` isolated a separate failure from the
V.90 TX mailbox. The NL bridge is producing application/LAPM payloads, but the
firmware never demonstrates acceptance of an `N_DATA` request.

The important distinction is between the local queue and an IDI request. The
`[nl] N_DATA queued` diagnostic is emitted after the payload has been removed
from `nl_data_queue` and `post_request()` has placed an actual `N_DATA` request
in PR RAM. It does not mean that the request was accepted by NL. Acceptance
would require a matching return code, but the run produced no `[nl] RC=...`
lines at all.

The current call path is unconditional. `_step_mips()` calls
`_service_n_data()` before running the MIPS main loop. If the local queue is
empty, `_service_n_data()` obtains up to 270 octets from LAPM, removes the
payload, and posts it using local NL entity `Id=1`, channel `0`, and reference
`1`. It neither checks that `N_CONNECT` has completed nor records an
outstanding request and waits for its return code before posting another one.

The timing makes the problem unambiguous:

| event | observed time |
|---|---:|
| first `N_DATA` request posted | 6.447 s |
| `CONNECT` reported | 16.74 s |
| synchronous data state `0xc6` | 18.84 s |
| first speed-complete state `0xc8` | 19.58 s |
| NL return codes observed | none |

Thus the bridge was submitting bearer data during V.90 training, more than ten
seconds before the modem reported `CONNECT`. It continued posting requests as
the ring offsets wrapped (`0x03e0`, `0x0500`, ..., `0x26c0`) instead of applying
NL request/return-code flow control. The `66106/66106 accepted/requested`
summary at call end is the RTP media scheduler's datagram count; it is not an
NL acceptance count and must not be used as evidence that `N_DATA` reached the
DSP TX source.

This also explains the simultaneous DSP observation. Throughout the run the
V.90 source trace showed `TXD=ffff/ffff/ffff`, `DM(0x31B2)=0`, and
`DI_control=0`. The NL log proves that the shim attempted to submit data, but
there is no evidence that NL consumed any of those requests or transferred a
payload to the synchronous TX mailbox. The fill words therefore remain a DSP
side symptom, not proof that the LAPM payload itself was malformed.

The required bridge behavior is:

1. retain LAPM bytes in the local queue while the modem is training;
2. wait for successful `N_CONNECT` completion and the usable synchronous data
   state (`0xc6` or later);
3. post one `N_DATA` request;
4. retain it as outstanding until the matching NL return code is received; and
5. only then post the next chunk, handling rejection or other return codes
   explicitly.

Until a matching return code and a resulting change in the DSP TX source are
observed, `N_DATA queued` should be described as **submitted by the shim**, not
as delivered to the firmware.

### Four defects found in the bridge, and what was changed

Reviewing the bridge against `tty_module/isdn.c` found that the missing flow
control above was not the only fault, and probably not the first one to matter.

**The request was addressed to an entity that was never assigned.**
`_service_n_data()` posted `N_DATA` on a hardcoded Id of 1, on the belief that
the Linux driver used a separate local entity for bearer data. It does not.
`isdn.c:3282` issues every post-`ASSIGN` request on `C->Net.Id`, which is the
Id the adapter returned in `ASSIGN_OK`; `NL_ID` (`0x20`, `pc.h:84`) is only the
pre-assignment "assign me" Id, and `isdn.c:4143` restores it on removal. There
is no bearer-data Id. The shim's own `N_CONNECT` already used the assigned Id,
and the assigned value was already carried as `shim.nl_entity_id` -- the data
path simply never read it. This is the most likely reason the run produced no
return codes at all: requests to an unassigned Id are dropped before they reach
the NL state machine. The bridge now posts on `self.nl_entity_id` and refuses
to post at all when no NL entity was assigned.

**Two bit sources could feed one HDLC decoder.** In NL mode the transmit side
already substituted mark fill for the synchronous mailbox, but `_lapm_active`
was still set on that path, and `_lapm_active` was the only gate on
`_service_rx_data()`. So DSP `RXD` bits kept being shifted into
`LapmEndpoint.feed()` at the datagram rate while `N_DATA` indications were
expanded into the *same* decoder. Two unrelated streams interleaved into one
`HdlcDecoder` desynchronise the flag search and fail the FCS on everything.

The first live CX call settled which source is real, and the answer was not the
one this code assumed. With the NL entity assigned `B2_TRANSPARENT` the
firmware accepted all 857 `N_DATA` requests and returned an `N_DATA`
*indication* for none of them: the receive direction stays on the DSP mailbox
even while the transmit direction rides the NL entity. So the collision is real
in the code but has never occurred at runtime -- the indication source has
never produced anything. Suppressing the mailbox on the assumption that
indications would replace it starved the decoder completely: call 1 finished
`HDLC good/bad/abort=0/0/0`, no V.42 detection at all, and a T400 fallback.

`_service_rx_data()` therefore keeps decoding the mailbox and only stops if an
`N_DATA` indication is actually observed (`_nl_rx_seen`), which is reported when
it first happens. The mailbox acknowledgement is unconditional either way, or
the DSP stalls waiting for the host to consume the datagram.

**No gating and no flow control**, as described above. The bridge now checks
`N_CONNECT` acceptance (recorded at the call-setup site, which is the only
place that return code is consumed) and `DATASTATE >= 0xC6` before posting, and
keeps one request outstanding at a time: `_nl_busy` is set on submission and
cleared only by the matching return code, with `OK_FC` latching `_nl_fc` until
`READY_INT`, mirroring `net_busy`/`NetFC` in `isdn.c:3290` and `isdn.c:4184`.

**LAPM's timers ran on the main-loop clock.** `LapmEndpoint._service()` runs
once per `take()` call and its T401, T403 and poll counters advance per call,
so they are calibrated for the datagram rate. The old bridge pulled 270 octets
at a time from `_service_n_data()`, once per main-loop pass, which put every
LAPM timer on an unrelated clock. The media path now clocks LAPM at the line's
datagram rate exactly as the non-NL path does, and buffers the bits in a
transmit elastic store that `_service_n_data()` drains in whole octets. As a
side effect nothing is produced during training at all, since `_lapm_active`
does not start before `0xC6`, so there are no queued training-era bytes to
discard. `LapmEndpoint.take_octets()` has been removed: it was the block-pull
helper that made the wrong clock easy to reach for, and it had no other caller.

The end-of-call summary now reports `[nl] N_DATA totals:` with accepted and
submitted counted separately, so acceptance can no longer be inferred from the
RTP scheduler's datagram count.

### Live CX results

Three calls, CX93001-EIS_V0.2013-V92 on `/dev/cu.usbmodem123456781` dialling
6001, `AT+MS=V90,1,300,9600,300,48000` -- V.90 with the modem's upstream capped
at 9600, which connects more reliably than the 12000 the modem was set to.
Captures under `artifacts/interop/nldata-cx/`.

| | call 1 (NL) | call 2 (NL, mailbox restored) | call 3 (baseline, no NL) |
|---|---|---|---|
| `N_DATA` accepted/submitted | 857/857 | 877/877 | n/a |
| `N_DATA` rejected | 0 | 0 | n/a |
| octets submitted | 91388 | 93540 | n/a |
| `N_DATA` indications | 0 | 0 | n/a |
| HDLC good/bad/abort | 0/0/0 | 0/1/32 | 0/0/0 |
| V.42 detection | none | **ODP detected, ADP sent** | none |
| modem result | connected | connected | `NO CARRIER` |

**`N_DATA` is accepted.** Every request drew `RC=0xff (OK)` against its own
reference. Before the entity-Id fix the same path produced no return code at
all, which is what this section was originally written about. The bearer is
addressable and the flow control matches the driver's one-outstanding rule.

**The NL transmit path reaches the line where the synchronous mailbox does
not.** The peer sent a V.42 ODP only on the NL runs; the baseline run, with the
same LAPM stream going into the DSP TX mailbox instead, drew no response and
the modem gave `NO CARRIER`. That is consistent with the long-standing
`TXD=ffff/ffff/ffff`, `DM(0x31B2)=0` symptom -- the mailbox is not transmitting
-- and is the first evidence of a transmit route that does.

**LAPM still does not establish.** Call 2 reached the protocol phase and then
took 32 HDLC aborts and one bad FCS with no good frame.

The "ODP detected (4 DC1s)" line in that call must not be read as evidence that
the receive stream is real V.42. `ODP_EVEN`/`ODP_ODD` are 10-bit patterns and
`_scan_odp()` asks for four alternating matches; over roughly 260 kbit of data
state at 7200 bit/s, chance alone yields on the order of 500 matches of those
two patterns. The detector will fire on noise, and an earlier version of this
section wrongly cited it as proof that the bit order was right. It proves
nothing about the receive path.

So the Session 87 question was still open: either the receive side is misframed
(wrong datagram bit count, wrong bit order, or dropped/duplicated `RXD` valid
words), or the receiver is not producing a demodulated stream at all.

## The receive side is not misframed; it is misdemodulating

Session 87 could not separate those two because a live call tests one framing
guess at a time. `EICON_RX_TRACE=<path>` now records the raw
`(sample, count, mask, word)` of every datagram the mailbox publishes, and
`tools/rx_frame_search.py` replays one capture under every combination of bit
count, bit order and RXD0/RXD1 ordering, scoring each by HDLC frames that pass
FCS. A valid FCS is a 1-in-65536 accident, so even a handful of good frames
identifies the right hypothesis; `tests/test_rx_frame_search.py` plants frames
under a known hypothesis and requires the search to find them there and nowhere
else, which is what makes a null result trustworthy.

Capture: call 5, 47619 datagrams over samples 136748..290769 (19.25 s of data
state). **No hypothesis produced a single valid FCS** -- not one of the 64
combinations tried.

The framing assumptions were not the problem, and the capture shows why:

- **Only bits 15..13 are ever set**, in all 47619 datagrams. The left-aligned,
  MSB-first, 3-bit-per-datagram layout `_service_rx_data()` assumes is exactly
  the register layout the pump is using.
- **The datagram rate is right.** 47619 datagrams over 19.25 s is 2474/s
  against the expected 2400, about 3% high.
- **The count word is not being misread.** The 3927 datagrams published as 2
  bits rather than 3 form a single contiguous run at the end of the call
  (indices 43692..47618) -- a genuine 7200 to 4800 rate change, not scatter.

What the capture does show is the content. Only nine distinct words appear, and
the 3-bit payload distribution is dominated by all-ones:

| payload | observed | predicted if the peer sends continuous HDLC flags |
|---|---:|---:|
| `111` | 55.3% | 50.0% |
| `010` | 10.7% | **0** |
| `000` | 9.0% | **0** |
| `100` | 8.7% | 12.5% |
| `001` | 8.1% | 12.5% |
| `101` | 4.0% | **0** |
| `110` | 2.3% | 12.5% |
| `011` | 2.0% | 12.5% |

Continuous flags (`0x7e` repeated, LAPM idle) are periodic with period 8, so
grouping into 3-bit datagrams yields groups drawn only from
`{011, 111, 100, 001, 110}` at *any* alignment. `000`, `010` and `101` cannot
occur, whatever the phase. **23.7% of received datagrams are in those three
impossible values.** That rules out alignment as the explanation -- a phase slip
shifts which legal value appears, it cannot manufacture an illegal one -- and
leaves symbol errors.

So the peer is transmitting (the gross shape matches flag idle) and the card's
receiver is demodulating it with roughly a 24% symbol error rate. No framing
layer survives that, which is why LAPM has never established, and it is why
every framing hypothesis scores zero. **The fault is upstream of framing, in the
receive signal path.**

The obvious suspect is the echo canceller. Session 88 established that the
near/far echo bulk-delay adapter at PM `0x1900..0x19c8` is RTSed out on every
page-14 load and that enabling it is currently worse than leaving it off. This
path runs SIP/RTP to an ATA to two-wire to the modem, so there is a hybrid
generating exactly the echo that adapter exists to remove, and the receiver has
to pull the analogue upstream out of it. A 24% symbol error rate on the upstream
is what that would look like. This is a hypothesis, not a measurement: the
capture localises the fault to the receiver but does not name the cause.

## Why enabling the echo canceller destroyed the state word

Session 88 left this as "turning the echo canceller on is not a switch that is
being left unflipped": with `EICON_V90D_BULK_ADAPTER=1` the outer state word
went `0x00c4 -> 0x78f8` within a few hundred samples of the page load, on both
`DM(0x32f7)=0` and `=8`. The conclusion was right; the reason turns out to be a
sequencing problem, and it is now fixed.

Watching `DM(0x1FF7)` (the outer state word) through the offline replay finds
the corrupting store immediately. The legitimate writer is PM `0x2fea`. The one
that writes `0x78f8` is **PM `0x26d4`, `DM(I0,M1) = SR1`, with `I0` sitting
exactly on `0x1FF7`** -- a store cursor that has walked out of its buffer.

The routine sets `I0 = 0x1DD0` at PM `0x26b1` and never sets `L0`, so I0 is
linear by design. It sets `L1 = 0x001E` at PM `0x26b9` for the *other* cursor,
so the code is DAG-aware; I0's bound is not a modulo register but the loop
count, read from `DM(0x1E4F)` at PM `0x26b5`.

`DM(0x1E4F)` has exactly one writer in the overlay, PM `0x3dee`, and it sits in
the rate-publication routine, one instruction after PM `0x3ded` writes
DATASTATESpeed to `DM(0x3F61)`. What it stores is the datagram bit count:
DATASTATESpeed is assembled as `0x2000 | 0x0020 | (AX0 - 0x15)` and the host
reads bits per datagram back as `21 + (value & 0x1f)`, so the stored `AX0` is
that width, 21..42 for V.90.

At page load that routine has not run. The word is not in any of the overlay's
DM blocks either -- it falls in the gap between `0x1e2a` and `0x1e5c` -- so it
holds whatever the previous page left. In the traced capture that was `0x6613`:
**26131 iterations**, walking I0 from `0x1DD0` up across `0x1FF7`. The watch
confirms the sequence exactly: `dm r 1e4f=6613 pc=26b6`, then the runaway loop
writing over `0x1E4F` itself and over the state word. With the adapter RTSed
out the routine is never reached and `DM(0x1E4F)` is never read or written at
all, which is precisely the archived comparison.

So the adapter is not inherently broken in this harness. It is being run before
the rate that parameterises it exists. `_service_bulk_adapter()` now holds the
same RTS at page load when the adapter is enabled and lifts it once
DATASTATESpeed is published and `DM(0x1E4F)` is a legal datagram width.
Seeding a guessed count instead does not work -- it relocates the corruption
rather than removing it, because the rest of the block is unset too.

Result on the archived replay, `usr-v92-21240/call1.rx.ulaw` to 20 s:

| | outer-state walk | ends |
|---|---|---|
| adapter off (default) | 38 changes | `0x00c2` |
| adapter on, before | collapses at 6.918 s | `0x78f8` |
| adapter on, held | 38 changes, **identical to off** | `0x00c2` |

`EICON_V90D_BULK_ADAPTER=1` is therefore no longer destructive. That is the
blocker removed, not the canceller proven: this capture never publishes a rate
(`DM(0x3F61)` stays `0x0000` for the whole 20 s), so the adapter is held for the
entire replay and never executes there.

### Released on hardware: the parameter decode is confirmed, the adapter is not

Two live CX calls with `EICON_V90D_BULK_ADAPTER=1` both released it, and both
printed the same thing:

```
[native-mips] bulk adapter released: DATASTATESpeed=0x202b, DM(0x1E4F)=32 bits/datagram
```

That confirms the reading of `DM(0x1E4F)` against hardware. `0x202b` gives
`21 + (0x2b & 0x1f) = 32` bits per datagram, and the word holds exactly 32 --
the datagram width, arriving from the rate-publication routine as predicted,
and inside the legal 21..42 range that the release gate tests.

The adapter has therefore run for the first time. It does not help; it appears
to end the call:

| | receive datagrams captured | data state |
|---|---:|---|
| adapter off, call 2 | -- | full call, 877 N_DATA accepted |
| adapter off, call 5 | 47619 | ~19 s |
| adapter on, call 6 | 696 | ~0.3 s, then `NO CARRIER` |
| adapter on, call 7 | 63 | immediate, then `NO CARRIER` |

Two calls each and the connect rate is a lottery, so this is not conclusive.
But 47619 against 696 and 63 is a large enough gap to act on: releasing the
adapter collapses the data phase almost immediately. The receive error rate
cannot be compared across these -- 696 datagrams is far too small a sample, and
the 26.7% impossible-value figure from call 6 is noise next to call 5's 23.7%
over 43692.

The default stays as it was, with the adapter RTSed out. What has changed is
that the failure is now an understood one at a known point, rather than a state
word being overwritten by a runaway cursor during page load.

### What the collapse is not, and where the descriptor actually is

The first guess was that the adapter gets a half-initialised parameter block:
PM `0x3def`, `0x3df0` and `0x3df3` write `DM(0x1E4E)`, `DM(0x1E4D)` and
`DM(0x1E50)` in the same routine as the bit count, and all four sit in the same
uninitialised gap. That is wrong, and it is wrong statically --
`0x3dee..0x3df3` is straight-line code with no branch, so `DM(0x1E4F)=32`
proves the other three were written in the same pass. The block is coherent at
release. (Worth noting separately: PM `0x3de4` is `JUMP $3DEE`, a second entry
that sets the block *without* writing DATASTATESpeed, which is why the release
gate accepts `DM(0x3F62)` as well.)

Enumerating every direct DM read on the adapter path -- PM `0x1900..0x19c8`,
the frame loop at `0x26a8..0x26da`, and one level of callees -- against the
overlay's initialised DM words gives eight reads of words the image never
sets. Seven have writers elsewhere in the overlay. One does not:

    DM(0x32F7)  read at PM 0x1900, 0x1982   writers = NONE

`0x1900` is the adapter's first instruction, `I1 = DM($32F7)`: its
control-block pointer. No overlay writes it -- `0x0261` and `0x026a` both only
read it -- and the overlay's DM image has `0x0000` there. A watchpoint confirms
the live value is `0x0000`, read at PM `0x1983`, never written.

That is not necessarily a fault. With `I1 = 0` the descriptor is `DM(0..7)`,
which the image ships as `2aca 2ad2 2ae5 2b1b 0000 0000 0000 0000` -- four
addresses followed by four zero cursors. This is the same structure the
existing `--prime-v90d-bulk-cursor` seam already assumes when it notes that "PM
0x1982 preserves the far-bulk cursor in DM4" and primes `DM(4)` from `DM(0)`;
`0x1982` is the second of the two `DM(0x32F7)` readers. So the pointer being
zero looks correct and the real gap is the unpublished cursors in `DM(4..7)`,
which a real selected channel would fill.

Note also that "disabled" has always meant *tail* disabled: the RTS sits at
`0x19C8`, so `0x1900..0x19C7` runs either way. The watchpoint above fired with
the adapter held.

Session 88's third row -- "adapter enabled + `--prime-v90d-bulk-cursor`:
stalls at `0x0068`" -- was measured with the `DM(0x1E4F)` runaway still
present and is now stale. Re-run on the same capture with the gate in place,
that combination gives 38 state changes ending `0x00c2`, **identical to the
adapter-off baseline**. All three of Session 88's offline failure modes are
gone; what remains is the live collapse.

Priming the cursor does not fix that. A third live call with both the adapter
and `--prime-v90d-bulk-cursor` primed `DM(4)` from `DM(0)=0x0201` (the runtime
value, not the image's `0x2aca`), released the adapter on the same
`DATASTATESpeed=0x202b`, and collapsed like the others:

| live call | adapter | cursor prime | receive datagrams |
|---|---|---|---:|
| 5 | off | -- | 47619 |
| 6 | on | no | 696 |
| 7 | on | no | 63 |
| 8 | on | yes | 546 |

So the unpublished cursors are not the cause either, and the echo canceller is
still not usable. What has changed across this work is where the failure lives:
it is no longer a runaway store during page load, it is something in the live
data phase, with the adapter demonstrably running on correct rate parameters.

## The receive path was working; the fallback was throwing the frames away

Everything above localised the LAPM failure to the receiver, on the strength of
a capture where no framing hypothesis produced a valid FCS. That capture was
real, but it was not representative, and the conclusion drawn from it was too
strong.

Two things had been hiding the actual fault.

**The CX was never reporting its protocol.** `AT&V` shows `W0` and `X3`, so the
CONNECT result carries the DTE speed and nothing else -- no `CARRIER`, no
`PROTOCOL` line. Every call in this work had been run without knowing what the
modem negotiated. `ATX4W2` turns that on. (`ATI6`/`ATI11`, which handoff.md
recommends for this readout, are USR Courier commands; the CX answers `OK` and
`ERROR`.)

**The modem defaults to `S48:7`, V.42 detection enabled, with `S36:7` falling
back silently to async.** The handoff's `S48=0` -- force LAPM, skip the
detection phase -- had never been tried on the CX. With `S48=0` the modem sends
no ODP at all and goes straight to XID.

That combination exposed the real bug. A capture taken under it,
`p-1`, contains **45 frames with a valid FCS** at 3 bits, MSB-first, RXD pairs
in order -- exactly the hypothesis `_service_rx_data()` already uses. The live
run of that same call reported `HDLC good/bad/abort=0/0/0`.

The difference is `LapmEndpoint.feed()`. `_enter_raw()` fires when T400 expires
without an ODP, and `feed()` then returned after `_feed_raw()` without ever
reaching the HDLC decoder. The fallback was a one-way door. A peer with
detection disabled never sends an ODP, so T400 *always* expires and its XID
and SABME arrive strictly afterwards -- into a decoder that was no longer being
fed. V.42 7.2.1.3 makes receipt of an LAPM frame the start of the protocol
phase regardless, and `feed()` already implemented that for the non-raw paths;
raw was the one that returned early.

Replaying `p-1`'s captured datagrams through the fixed endpoint enters the
protocol phase and answers 45 XID commands. Live, with the fix:

    [v42] totals: HDLC good/bad/abort=73/0/9, XID rx/tx=73/73

against `0/0/0` on every previous call in this work. So the receive path
demodulates, frames, and passes FCS. The 24% impossible-value figure from the
earlier capture was a call where the receiver genuinely did not lock; it does
not generalise, and the "misdemodulating at 24%" conclusion above should be
read as applying to that capture only.

### What is still open

LAPM does not complete. The modem sends XID 73 times and never advances to
SABME, so it is not accepting our XID response -- either the response is not
reaching it or its content is unacceptable. Its XID is 77 bytes; ours is 25.

The transmit direction is the first thing to separate. That run had
`EICON_V42_NL_DATA=1`, so the XID responses went out over the NL entity, which
is proven to be *accepted by the firmware* but has never been proven to reach
the line. Two calls with the mailbox path instead did not get far enough to
compare -- one never published a receive rate at all. That is the next
experiment, and it wants a run of calls: the connect rate is a lottery, and
rapid cycling makes it worse, with BUSY and calls that never arrive until the
line is left to settle.

## The XID/SABME stall: a dead transmit bearer and a zero conformance mask

The previous section left LAPM answering 73 XID commands and never being sent a
SABME. Two defects were found by reading the transmit path and V.42 12.2.2
against each other. Neither has been tried on hardware yet; both are things the
code was doing wrong regardless of which one the CX was reacting to.

### 1. In NL mode the line carries mark, not our XID responses

`EICON_V42_NL_DATA=1` was set on that call. In `_next_tx_words()` that branch
put the LAPM stream into the NL transmit elastic store and gave the synchronous
transmit mailbox `[1] * count` -- **mark fill**. The mailbox is the data pump's
transmit source, so whatever the NL entity did with the octets, the line
carried mark for the whole call.

The receive direction had already worked this out and written it down: the
firmware accepted all 857 N_DATA requests and returned an N_DATA *indication*
for none of them, so `_service_rx_data()` keeps decoding the mailbox until
`_nl_rx_seen`. The transmit direction had no such condition and diverted
unconditionally. So the run that reported `XID rx/tx = 73/73` transmitted 73
XID responses into an entity that has never been shown to carry anything, while
the CX heard mark and retransmitted XID every T401 for 55 seconds. 55 s / 750 ms
is 73.

`_next_tx_words()` now applies the same test as the receive path: LAPM rides
the mailbox until an N_DATA indication proves the bearer live.
`EICON_V42_NL_DATA=force` restores the unconditional diversion for anyone
testing the bearer in isolation. This makes the "separate the two transmit
paths" experiment unnecessary -- the default is now the mailbox in both
directions, which is the only combination with hardware evidence behind it.

### 2. The XID response's optional-functions mask was zero

Table 11a/V.42 Note 1: the PI=3 parameter value is a 32-bit HDLC optional
functions mask, and "the transmitter of an XID command frame shall set bit
positions 2, 4, 8, 9, 12 and 16 to 1. The transmitter of an XID response frame
shall also set these bit positions to 1, except bit position 16 shall be set to
0 if bit position 17 is set to 1."

`encode_xid_parameters()` was sending `optional_functions = 0`, and `_handle()`
explicitly rebuilt the negotiated parameters with a literal `0` in that field.
The comment there -- "no optional procedure is advertised until its complete
procedure is implemented" -- is right about bits 3, 14, 17 and 24, and those
stay clear. It was wrong to extend that to the six non-negotiable bits: bit 9
is the only statement that the sender uses extended (modulo 128) sequence
numbering and bit 16 the only statement that it uses a 16-bit FCS, which is
exactly what a responder has to agree to before a SABME is worth sending. The
mask is now `0x0000898A`, encoded low-order octet first as `8a 89 00 00`.

The Recommendation does say a receiver "should ignore these bit positions", so
this is a candidate rather than a diagnosis. It is a `shall` we were violating
either way, and our 25-byte response against the CX's 77 was the only content
difference worth acting on without a capture of the CX's XID.

### 3. Command/response addressing (found while reading 8.2.1)

Table 6/V.42 makes the C/R bit depend both on the direction and on which end
originated the call:

| | originator -> answerer | answerer -> originator |
|---|---|---|
| command | C/R = 1 | C/R = 0 |
| response | C/R = 0 | C/R = 1 |

The endpoint echoed the received address onto everything it sent and kept the
last one in `self.address`. For an answerer that is accidentally correct for
responses -- UA, the XID response, RR acknowledgements -- and wrong for every
command it originates: I frames, the RR(P) window probe and DISC all went out
at 0x03, which the originator reads as a response. An I frame arriving as a
response is a frame-rejection condition at a conformant peer, so this would
have surfaced immediately after SABME even if SABME had arrived.

`command_address`/`response_address` are now derived from the role and a
learned DLCI; `address` remains as the response address, which is what the
tests and the fallback-recovery path were already using it as. A polled
supervisory *response* (F=1) is also no longer answered with an RR(F), which
was a standing RR ping-pong between two of these endpoints.

### 4. N401 was being applied to every frame, not just I frames

`_handle()` opened with `if len(frame) > self.n401 + 3: FRMR(too_long)`. N401
bounds the information field of an I frame and nothing else; a U frame carrying
an information field -- XID, FRMR, TEST, UI -- is not subject to it. The CX's
XID is 77 octets, so any peer that negotiated N401 below about 74 would have
had its own next XID answered with FRMR and the link torn down. The check now
lives in the I-frame branch.

### What this does not settle

No hardware has seen any of it. The next call should be the plain mailbox path
(no `EICON_V42_NL_DATA`) with `S48=0` and `ATX4W2` on the CX, and it wants
`EICON_RX_TRACE` set so the CX's 77-byte XID is on disk this time: if the modem
still stops at XID, its own parameter list is the only remaining place to look,
and it has never been captured.

## Twelve live calls: the V.42 fixes are untested, because nothing reached data mode

Twelve calls against the Courier, run to test the four fixes above. **Not one
reached `0x00c6`/`0x00d0`, so none of the four was exercised.** What the run
established instead is about the physical layer, and one part of it is a
regression check this document has owed since Session 85.

The Conexant CX on `/dev/cu.usbserial-21210` is dark — silent to `AT` at 115200,
57600, 38400, 19200 and 9600. The Courier V.Everything answers on
**`/dev/cu.usbserial-21240`**, which is the reverse of what the reproduction
section said; it reports `USRobotics Courier V.Everything`, ROM `5607A`. Two
command corrections while we are here: `ATW2` is a Conexant command and the
Courier answers `ERROR`; `AT&A3` is its equivalent. And `ATDT` immediately after
a previous call produces `NO CARRIER` with **no INVITE reaching the endpoint at
all** — two calls were lost that way before a 20 s settle after registration
made it reliable.

### Outcome of every call

| tag | data source | last TrnProgress | notes |
|---|---|---|---|
| xid1 | `--tx-v42` | `0x00b0` | held 40 s |
| xid2, xid3 | `--tx-v42` | — | no INVITE; dialled too soon after the last call |
| xid4 | `--tx-v42` | `0x002a` | never left INFO |
| xid5 | `--tx-v42` | `0x00c0` | DSR; 47243 TX datagrams |
| xid6 | `--tx-v42` | `0x00b3` | the documented stall |
| xid7 | `--tx-v42` | `0x0038` | **reached data mode**, then retrained; see below |
| xid8 | `--tx-v42` | `0x002c` | never left INFO |
| xid9 | `--tx-v42` | `0x00c0` | held 40 s |
| raw-regress1 | `--tx-prbs` | `0x00b3` | |
| raw-regress2 | `--tx-prbs` | `0x00b0` | |
| raw-regress3 | `--tx-prbs` | `0x00c0` | |

### The regression check, and its answer

Ranked step 3 was "re-run a raw-mode call on port 5060 to confirm the known-good
path still reaches `0x00c6`/`0x00d0` on the current tree". It does not. Three
`--tx-prbs` calls landed on `0x00b3`, `0x00b0` and `0x00c0` — the same
distribution as the nine V.42 calls, and the same three failure states.

That is worth stating carefully. It does **not** show a regression: `0x00b3`,
`0x00b0` and `0x00c0` are exactly the outcomes Sessions 87–93 describe as the
DIL lottery, and Session 87's success was one call. But it does mean the lottery
is currently losing every draw, on both data sources, and it removes the last
reason to read a failed V.42 call as a V.42 problem. **The data source makes no
difference to how far a call gets.** Any further V.42 work is blocked behind the
DIL blocker, which is where the effort belongs.

### xid7, the one call that reached data mode

It published TX 22 / RX 7 bits per datagram and ran 2.26 s (samples
132894..150979, 5430 datagrams) before retraining back through INFO to `0x0038`.
`tools/rx_frame_search.py` scores its trace at **zero valid FCS under all 64
hypotheses** — every bit count 1..16, both orders, both RXD pair orders. The
receiver published 128 distinct words, so it was producing something, but the
link retrained immediately afterwards, which is the signature of the capture in
"The receive side is not misframed" rather than of a framing error. Do not read
this as re-opening the framing question that the `p-1` capture settled.

## The XID/SABME blocker is not in V.42 at all: the transmit datagram path

The CX is back (`/dev/cu.usbmodem123456781`; a second Courier is on
`usbserial-21210`, the first on `21240`). It reaches V.90 data mode where the
Courier mostly does not, and it settles this question.

### What is now proven about our V.42

Four things, none of which was established before, and together they exclude
V.42 as the cause.

1. **The CX's XID is captured and decoded**, from the RXD trace of a data-mode
   call. It is 25 octets, not the 77 recorded earlier:

   ```text
   03 af 82 80 0013  03 03 8a8900  05 02 0400  06 02 0400  07 01 0f  08 01 0f
   ```

   Its optional-functions value is `8a 89 00` — **the same `0x898A` derived
   from Table 11a Note 1 two sessions ago**, which is independent confirmation
   of that mask from a shipping implementation. It carries it in three octets
   where Note 1 says four (ISO/IEC 8885's "smallest number of octets needed"),
   so `XidParameters` now carries the length and a responder answers in the
   form the initiator used. Against this peer our response is byte-identical to
   its own command.
2. **Our HDLC encoder is bit-for-bit identical to the CX's transmitter.**
   Re-encoding the decoded payload with `encode_frame()` and searching the raw
   trace matches all 60 on-air frames exactly — flags, stuffing and FCS.
3. **Our receive path is clean**: 60 good FCS, 0 bad, per call, repeatedly.
4. **The CX retransmits XID on a metronomic 700 ms T401** — measured from the
   trace, gaps of 0.700 s with no variance across 60 frames in two separate
   calls — *completely unaffected* by the 60 responses we send it. It is not
   rejecting our XID. It is not receiving anything at all.

That held across every response variant tried: PL=4, PL=3 (byte-identical to
its own), and both V90D transmit bit orders (`EICON_V90D_TX_MSB_FIRST`).

### The gate defect, found and fixed

`_next_tx_words()` tested `DM(0x3FC2) >= 0x00C6` **per datagram**. That word
does not sit still above 0xC6 on an established link — it moves around the
0xC0..0xC4 neighbourhood — but the DSP transmits a datagram every time it asks
for one. So the harness was handing it mark fill inside the LAPM stream:
**22587 of 82715 datagrams, 27% of a live call's downstream bits**, measured
with the new `payload / mark fill` counters on the call-end line.

`_lapm_active` is already the pump's own latch for "reached synchronous state",
so the test now uses it, and the last published datagram width is held so a
transiently unreadable rate word cannot reopen the same hole. On the line the
effect is visible: before, a peer in raw mode saw long runs of mark broken by
bursts; after, a continuous stream.

**It did not fix V.42.** The CX still answers 60 XIDs with no SABME.

### What was actually broken: two owners answered one TX request

`EICON_TX_PATTERN=<text>` was added because `--tx-prbs` cannot test a bit path
— random in, random out. Sending `ABCDEFGH` (a 64-bit period) to a CX dialled
with `AT\N0` and capturing its DTE bytes raw gives:

```text
0a 88f484fa 88f484fa 88f484fa 88f484fa ...
```

**A constant 32-bit block**, 90% of the capture in four octet values, where the
input alternates two *different* 32-bit datagrams. No bijective transform maps
two distinct inputs onto one output, so this is not a bit-order or alignment
question. Nor is it the scrambler: the V.34/V.90 GPC (18,23), the V.34 call-side
(5,23) and the V.32 (17,23) polynomials were all tried in both directions
offline, none matches, and a self-synchronising descrambler preserves the 64-bit
period anyway.

The request rate was a distraction. The resident `0258` TIKRNL task and the
host shim were both answering `DI_control` bit 15. `_service_tx_request()` wrote
the host datagram, then the later resident-task pass put its own data into the
same mailbox before the selected modem page consumed it. Clearing bit 15 only
proved that *an* owner had answered; it did not prove that the host words were
the words consumed.

The extracted task has five relevant stores:

```text
06d0 93f05a  DM(3f05) = AR    ; AR was loaded with ffff, mark-fill TXD0
0732 93f05f  DM(3f05) = SR1   ; short internal TXD0 path
0734 93f05f  DM(3f05) = SR1   ; long internal TXD0 path
0738 93f06f  DM(3f06) = SR1   ; internal TXD1
0740 93f07f  DM(3f07) = SR1   ; internal TXD2
```

MIPS relocates that task by seven words in the live build-117-926 core, to PM
`06d7/0739/073b/073f/0747`. Suppressing only the four internal-data stores made
the peer's old four-byte constant disappear, but the live snapshots showed
`TXD0=ffff` while `TXD1` alternated correctly. The first store was the remaining
writer.

When `--tx-prbs` or `--tx-v42` selects an explicit host source, startup now
claims the mailbox by finding the exact five-opcode relative signature and
NOPing all five stores. It requires exactly one match and fails before changing
PM on an unknown build. Normal firmware-owned operation is untouched. A saved
call replay with `ABCDEFGH` versus `AAAAAAAA` then differed in 2774 of 3200
post-sync PCM samples, with 15313/15313 requests accepted in both runs: distinct
host sources now reach the modulator distinctly.

### The raw-peer harness also needed V.14 framing

There was a second, independent error in the claimed identity test. `AT\N0`
turns off V.42, but the peer's DTE is still asynchronous: its V.14 converter
expects a start bit, eight data bits low-order first, and a stop bit. The first
`EICON_TX_PATTERN` implementation supplied bare octets. After mailbox ownership
was fixed, that bare `ABCDEFGH` stream decoded as the stable seven-octet cycle
`47 a4 90 68 44 2a 19`; that was the asynchronous converter consuming apparent
framing bits, not another lossy modem transform.

`EICON_TX_PATTERN` now emits repeating 8N1 start-stop characters. The live
`ownership-fix3` call connected at `CONNECT 115200`, accepted 55043/55043 TX
requests (40355 payload, 14688 training fill), and the raw DTE capture contained
a 46268-octet uninterrupted `ABCDEFGH` repetition. Its first visible steady
bytes were:

```text
43 44 45 46 47 48 41 42 43 44 45 46 47 48 41 42 ...
 C  D  E  F  G  H  A  B  C  D  E  F  G  H  A  B
```

That is the required end-to-end result: a deterministic host bit stream put in
the synchronous mailbox is recovered unchanged at the raw peer's DTE.

**This retires an over-reading made earlier in the same session.** A `--tx-prbs`
call producing `CONNECT 42667` and garbage on the CX's terminal was taken as
proof that our transmit reaches the peer. It proves the *samples* reach it. It
does not prove our *bits* do. The original pattern test showed they did not;
the corrected identity test above is the evidence that they now do.

### Where this leaves V.42

The physical transmit-bit blocker was removed; at this point V.42 itself was
still neither confirmed nor fixed by the pattern test. The earlier
optional-functions, C/R, N401 and mailbox/N_DATA changes could now be tested on
their own terms with the plain mailbox path. `EICON_TX_PATTERN` plus a raw-mode
peer remains the V.42-free regression harness for the layer below them. The
next section records the live V.42 test that followed and supersedes this
intermediate status.

## V.42 establishes: `GI=ff` is not a length-prefixed XID group

With exclusive TX-mailbox ownership proven, a clean plain-mailbox call finally
made the CX's remaining negotiation visible. The peer was a CX93001-EIS
V0.2013 V92 on `/dev/cu.usbmodem123456781`, configured with
`ATX4W2S48=0S36=4S46=136&K0`. For the repeatable bidirectional run its V.90
upstream was capped with `AT+MS=V90,1,300,9600,300,48000`.

The first useful call (`artifacts/interop/nldata-cx/v42-mailbox5`) passed DIL,
reported `CONNECT 42667`, and received a 59-octet XID command twice:

```text
03af8280001303038a8900050204000602040007010f08010f
ff40035634344101004201034302020044020200450120460120
4702040048020400
```

`FI=82`, `GI=80`, `GL=0013` and the following parameters are the already known
V.42 core: optional functions `8a8900`, N401 128 in both directions, and k=15
in both directions. Byte `ff` is then ISO/IEC 8885's user-data group identifier.
The repository's extracted `tty_module/xid.h` names it `XIDGI_UD` and records
the crucial wire rule: this subfield has **no group-length field**; its contents
continue to the frame's FCS. Here those contents are V.44 TLVs (`40 03 "V44"`,
then `41 01 00` declining compression, followed by capability values).

`parse_xid_parameters()` treated every group alike. At `ff` it consumed `40 03`
as a 16-bit group length of `0x4003`, found that impossible, and returned
`None`, discarding the V.42 group that had already parsed successfully. The
caller consequently constructed its fallback XID with a four-octet optional
mask instead of answering the initiator's three-octet encoding. That is why the
CX retransmitted XID and never sent SABME after the transmit path was repaired.

The parser now stops structured group parsing on `GI=ff`, retaining the valid
V.42 parameters and ignoring unsupported user-data protocols. Two captured-XID
tests pin both parsing and the exact response. The response sent on the next
live call was:

```text
03af8280001303038a8900050204000602040007010f08010f
```

The CX accepted it immediately, sent SABME (`03 7f`), and received UA
(`03 73`). It then sent the 18-byte DTE string `cx-to-eicon-v42\r\n` in an I
frame, which the endpoint accepted and acknowledged. The call ended with
`XID rx/tx=2/2`, `SABME rx=1`, `I rx=3`, and 24 undrained DTE bytes (the test
string plus the serial helper's later escape/hangup bytes). This proved
establishment and the peer-to-endpoint data path.

### Bidirectional proof

`artifacts/interop/nldata-cx/v42-mailbox8` repeated the call with `--v42-pty`.
The PTY helper waited until it had received `cx-to-eicon-v42\r\n`, then wrote
`eicon-to-cx-v42\r\n`; waiting matters because T400 raw fallback occurs before
an `S48=0` peer's first XID and pre-establishment PTY input would otherwise be
consumed by that temporary raw path.

The outbound frame was logged as:

```text
[v42] TX I N(S)=0 N(R)=1 17B:
0100026569636f6e2d746f2d63782d7634320d0a
```

The answerer command address is `01`, not the response address `03`, confirming
the earlier C/R fix on the live wire. The CX's DTE capture is exactly 17 octets:

```text
65 69 63 6f 6e 2d 74 6f 2d 63 78 2d 76 34 32 0d 0a
 e  i  c  o  n  -  t  o  -  c  x  -  v  4  2 CR LF
```

The CX acknowledged after three retransmissions; final state was `unacked=0`,
with `HDLC good/bad/abort=46/0/21`, `XID rx/tx=1/1`, `SABME rx=1`, `I rx=3`,
and `I tx/retx=1/3`. There were zero over-budget media ticks and zero catch-up
deferrals. Thus a datagram placed into the repaired transmit mailbox is now not
only recovered by a raw peer: LAPM establishes, carries exact application data
in both directions, and acknowledges the transmitted I frame.

The physical connect remains intermittent; several surrounding attempts
stopped below the V.42 boundary with only mark fill. Those failures produced no
HDLC frames and do not qualify the successful protocol result. Capping the CX's
V.90 upstream at 9600 made the useful calls more repeatable.

The LAPM suite is now 42 tests and the complete Python suite is 184 tests. The
ADSP core test also passes. Compression was deliberately disabled (`S46=136`),
and a large-window throughput soak is still future coverage; basic negotiated
V.42 establishment and bidirectional data transfer are closed.

## V.42bis live interop: Annex A negotiation and compressed data both ways

The Python LAPM endpoint now has an opt-in V.42bis implementation selected by
`--tx-v42bis` together with `--tx-v42`. It implements the Annex A private XID
group (`GI=f0`, parameter-set identifier `V42`, P0/P1/P2), transparent escape
handling, LSB-first packed codewords, STEPUP, FLUSH alignment, and leaf-node
dictionary recovery. The default remains uncompressed.

The CX93001-EIS V0.2013 V92 on `/dev/cu.usbmodem123456781` was configured as:

```text
ATX4W2S48=0S36=4S46=138&K0
AT+DS44=0
AT+DS=3,0,2048,32
AT+DR=1
AT+MS=V90,1,300,9600,300,48000
```

`+DS44=0` matters: the CX otherwise advertises and selects V.44 independently
of its V.42bis `+DS` settings. The first two calls stopped below LAPM with
`NO CARRIER`, zero valid HDLC frames and mark fill only. The third call passed
the physical training lottery and reported:

```text
+DR: V42B
CONNECT 42667
```

The first received frame was this XID command:

```text
03af8280001303038a8900050204000602040007010f08010f
f0000f000356343201010302020200030120
```

The `f0` group decodes as P0=3 (both directions), P1=512 codewords and P2=32
octets. The endpoint selected and returned those values. This is the first live
evidence that the Annex A encoding is accepted by the peer, rather than merely
round-tripping between two local codec instances.

The CX DTE then sent 524 application octets:
`cx-v42bis-`, 512 `A` octets and CR/LF. The receive trace contains six I frames
whose information fields total 118 octets; the V.42bis decoder recovered the
524-byte application payload exactly on the endpoint PTY. Thus the peer really
entered compressed mode—the result is not a transparent V.42 transfer with a
compression-capable XID.

The endpoint PTY sent the reverse 527-byte payload: `eicon-v42bis-`, 512 `B`
octets and CR/LF. Its encoder reduced that to one 79-octet I-frame information
field. `artifacts/interop/nldata-cx/v42bis-mailbox1.dte` contains exactly the
original 527 octets, and the CX's RR eventually released the frame. Final endpoint totals were 55
good frames, one bad FCS, 11 aborts, one XID each way, one SABME, six received
I frames, one transmitted I frame plus three retransmissions, and
`unacked=0`. There were no media ticks over budget. The capture is
`artifacts/interop/nldata-cx/v42bis-mailbox1`.

The focused V.42/V.42bis suite is 55 tests and the complete Python suite is
197 tests. V.42bis negotiation, compression, decompression and bidirectional
hardware interoperability are now confirmed; a long random/repetitive soak
and codeword-width step-up beyond the peer's negotiated 512-entry dictionary
remain future coverage.

## V.44 live interop: XID user data and overlapping string extensions

The host LAPM endpoint now also supports opt-in V.44 with `--tx-v44` and
`--tx-v42`. V.44 and V.42bis are mutually exclusive in both the CLI and XID.
The implementation follows the stream method: it begins in compressed mode,
packs prefixes and numeric codes least-significant bit first, implements ETM,
FLUSH, STEPUP and REINIT, and enforces negotiated codeword, maximum-string and
history limits separately in the two directions. Its encoder uses the
conforming append-only subset, creating one-character string segments and
reusing complete codewords. Its decoder also accepts variable-length string
extensions from a peer.

The XID parser now consumes the CX's V.44 TLVs in the unlengthened `GI=ff`
user-data subfield instead of merely preserving the preceding V.42 group. The
CX93001 offer used in the live call was:

```text
03af8280001303038a8900050204000602040007010f08010f
ff40035634344101034201034302020044020200450120460120
4702040048020400
```

This names `V44`, carries capability `03`, requests both directions, and
proposes P1=512 codewords, P2=32 characters and P3=1024 history characters in
each direction. Because P0 is relative to the sender of each XID, the responder
complements its direction bits. Parameter limits are cross-paired (local TX
with peer RX, local RX with peer TX) and the smaller valid value is selected.

The first call to reach `+DR: V44` found a real decoder defect. The peer's first
I-frame information field was:

```text
c6f05aec68685a8217316632994c2693c96432994c267311a106
```

It begins `cx-v44-`, creates C1 as the string `AA`, then extends that string by
30 `A` characters. The source starts with only one character beyond the
represented string; source and destination deliberately overlap, so each
character copied into history is available as the source of a later character
in the same extension. The decoder had required the complete source range to
pre-exist and raised C-ERROR. It now copies overlapping extensions one
character at a time. A regression test feeds this exact CX byte stream across
the same two I-frame boundary and requires the complete 521-byte output.

The post-fix `v44-mailbox2` call reported:

```text
+DR: V44
CONNECT 42667
CX -> Eicon: payload=True
Eicon -> CX: 524 DTE bytes; payload=True
```

The CX's `cx-v44-` + 512 `A` + CR/LF payload is 521 application octets and
occupied 36 compressed I-frame information octets. It appeared exactly on the
endpoint PTY. The endpoint then encoded `eicon-v44-` + 512 `B` + CR/LF from 524
application octets into 53 compressed octets; the CX DTE recovered the exact
524-byte payload, saved as `artifacts/interop/nldata-cx/v44-mailbox2.dte`.
Final totals were 55 good frames, one bad FCS, six aborts, one XID each way, one
SABME, six received I frames, one transmitted I frame plus three
retransmissions, and `unacked=0`.

Several surrounding calls, including the first post-fix redial, ended with
`NO CARRIER` below XID. They neither confirm nor falsify compression and remain
the same intermittent physical-training lottery seen in the V.42 and V.42bis
runs. The successful call establishes V.44 negotiation, decompression and
compression against independent hardware in both directions. Twelve focused
V.44 tests bring the complete Python suite to 209 tests.

## Session 105: restore the native V.34 echo bulk-delay call

The emulator was bypassing shared echo code that V.34 needs. Both Build
117-926 V.34 (`0x0261`) and V90D (`0x026a`) contain the identical worker at PM
`0x1900..0x19c8`; the shipped word at PM `0x19c8` is `0x19900f`, `JUMP $1900`.
The page-load shim replaced it with `0x0a000f`, `RTS`, for both overlays even
though the switch and release policy were developed for V90D.

The V.34 call contract is fully native:

```text
19d5: CALL (I4)       Core8kRoutine
19d7: CALL $19A7      bulk setup/service wrapper
19a8..19ab            gate on DM(3FC1) bit 0400
19b8..19b9            load Nearbulklength/BulkLength
19c6: CALL $1982      rebuild descriptor when lengths change
19c8: JUMP $1900      tail-call the bulk worker
```

V90D reaches the same PM `0x19a7` wrapper from PM `0x1a24`. The correct
emulator boundary is therefore the page handoff; Python must not call PM
`0x1900` directly or invent another cadence.

### The Session 93 ambiguity is closed

`--release-bulk-immediately` and `--bulk-dm5` were added to
`tools/v90_dpcm_vector_trace.py` for a short instruction-level A/B. At both
ambiguous load sites the live DAG state is conclusive:

```text
PM 1917: I1=0005 before AY0 = DM(I1,M2)
PM 1921: I1=0005 before AY0 = DM(I1,M2)
```

`AY0` comes from descriptor offset 5, not offset 6. PM `0x1982` writes offsets
`0,2,3,4,6,7` and deliberately retains offset 5. Both V.34 and V90D download
words `0..4` and `8..12`, leaving `5..7` sparse. The preceding INFO overlay,
however, executes PM `0x3734..0x3738`, a 0x400-word clear starting at DM zero,
so the reconstructed page transition leaves the retained word as zero.

Zero is destructive. With the original PM `0x19c8` live, PM `0x1922/0x1923`
compares its candidate address with that word and adds `BulkLength` on unsigned
underflow. A zero lower limit never underflows for a 16-bit address, so PM
`0x1930` walks into unrelated overlay state—the broad sweep and V.34
`DM(0x2165)` abort from Session 101.

The zero-based delay area requires the word immediately below DM zero,
`0xffff`. Publishing it before page resume changes the PM `0x1930` destination
from the broad sweep (for example `0x1596`) to the bounded zero-based area (the
first traced destination was `0x0001`). An eight-second archived-capture replay
with the shipped worker live then produced the clean walk:

```text
0050 0052 0053 0060 0062 0064 0066 0068 006a
0070 0072 0074 0076 0078 007a
```

The implementation now publishes `0xffff` at descriptor offset 5, following
the firmware selector as `(DM(0x32f7) + 5) & 0x3fff`, immediately after a V.34
overlay load and before resuming PM `0x06df`. V.34 is no longer included in
the V90D PM-`0x19c8` diagnostic patch, so PM `0x19d7 -> 0x19a7 -> 0x1900` runs
under the firmware's own enable and length gates. Leaving V90D clears any stale
page-14 hold state so its saved opcode cannot leak into another overlay.

A default 15-second native loopback loaded V.34 on both ends and printed the
new publication at `DM(0x0005)`. The answerer stayed at
`TrnProgress 0x0071 -> 0x0072`; the caller stayed in its known
`0x0060 <-> 0x0062` loop for the remainder of the run. Neither took the former
40 ms abort or returned to INFO because of bulk-worker corruption. The caller
loop is still explained by INFO word 0 decoding as `0x2000` (Sessions 102-104),
and loopback page 8 still misses real time, so this is memory-safety/call-path
verification rather than a V.34 connection result. A hardware V.34 call remains
required.

V90D is intentionally unchanged: its copy of the worker remains controlled by
`EICON_V90D_BULK_ADAPTER` and held behind the datagram-rate publication. The
V.34 result must not be generalized into a claim that the live V90D
data-phase collapse is fixed.

## Session 106: extend the retained-bound repair to V90D and verify hardware upstream

Session 105 established that the native PM `0x1900..0x19c8` worker needs the
retained lower-limit word at descriptor offset 5. V90D (`0x026a`) carries the
same worker and arrives through the same INFO clear, so its page handoff now
publishes `0xffff` at `(DM(0x32f7) + 5) & 0x3fff` as well. The V90D worker is
enabled by default; `EICON_V90D_BULK_ADAPTER=0` remains an explicit diagnostic
escape hatch.

The existing rate gate remains important. At page entry the shim holds the
worker's PM `0x19c8` tail jump as `RTS` until both a nonzero datagram rate
(`DM(0x3f61)` or `DM(0x3f62)`) and a valid V90D count (`DM(0x1e4f)` in
`21..42`) have appeared. It only releases while overlay `0x026a` is resident,
and clears the saved hold state when that page is left. This prevents stale
page state and prevents the native worker from running against an incomplete
rate block.

Archived replay covered both sides of the gate. A no-rate replay completed the
expected training-state walk through `0x007a` while the worker remained held.
The rate-bearing `v42-mailbox5.rx.ulaw` replay released at
`DATASTATESpeed=0x202b`, `DM(0x1e4f)=32`; an instruction trace showed the real
PM `0x1930` store use `I0=0x0001`, bounded inside the zero-based delay area.
PM `0x1930` executed once in the traced interval, and the V90D worker did not
overwrite the rate word.

The decisive test was a live Conexant-to-emulator V.90 hardware call with
`AT+MS=V90,1,300,9600,300,48000`. It reported `CONNECT 42667`, published
`DM5=ffff`, released the worker at `DATASTATESpeed=0x202b` and
`DM(0x1e4f)=32`, and reached V.90/V.34 synchronous data mode with 32 TX and 3
RX bits per datagram. The call stayed up for 67.24 seconds instead of the old
roughly 0.3-second data-phase collapse. The payload `cx-to-eicon-v42` arrived
at the emulator and `eicon-to-cx-v42` arrived at the Conexant, proving both
directions through LAPM. Final accounting was 82,010/82,010 accepted/requested
TX datagrams, 67,330 payload datagrams, and V.42 `I rx=3`, `I tx/retx=1/4`,
with no out-of-sequence or undrained bytes. The capture prefix is
`artifacts/interop/nldata-cx/v90-bulk-dm5-live1`.

A second call omitted `EICON_V90D_BULK_ADAPTER` to exercise the new default.
It loaded the same enabled-and-held path, but never published either rate word
and ended `NO CARRIER`; the endpoint consequently emitted mark fill only and
the worker was never released. This is a pre-data training/DIL miss, not a
repeat of the repaired data-phase collapse. Its capture prefix is
`artifacts/interop/nldata-cx/v90-bulk-dm5-live2`.

The hardware result answers the original boundary: the retained-bound repair
does fix V.34 upstream while operating in V.90 for the tested Conexant call.
It does not eliminate the separate V.90 training/connect lottery, but once a
rate is published the native worker is bounded, the call remains up, and user
payload crosses upstream and downstream.

## Session 107: measure both rates and sweep the first hardware matrix

Session 106's `CONNECT 42667` only named the PCM downstream rate. The ADDSP
V.90 guide supplies the missing digital-side measurement: read-database offset
`0x81` is `DATASTATEspeedTx`, the modem transmitter's selected speed, and
offset `0x82` is `DATASTATESpeed`, the modem receiver's selected speed. On the
digital V90D endpoint those are downstream and V.34 upstream respectively.
The database starts at DM `0x3f60`, so the live words are `DM(0x3f61)` and
`DM(0x3f62)`.

The successful Session 106 capture published `202b/11e9`. V90D index 11 is 32
bits per 8000/6-Hz datagram, or 42,667 bit/s downstream; V.34 speed index 9 is
7,200 bit/s upstream. The previously proven bidirectional LAPM call was
therefore **42,667 downstream / 7,200 upstream**, not 9,600 upstream merely
because the Conexant command capped its transmitter at that value.

The emulator now decodes and latches both read-database words as they appear,
because the firmware can replace them before the synchronous-state callback.
It reports the pair at V.42 entry, AT `CONNECT`, and call teardown. A new
`tools/cx_v90_rate_probe.py` makes the Conexant's six `+MS` rate fields
explicit: its TX range is upstream and its RX range is downstream.

The first live sweep produced these usable points. `BUSY` calls are PBX
failures and are excluded from modem conclusions.

| Conexant request | bulk worker | ADDSP-selected rates | result |
|---|---:|---:|---|
| upstream <= 9,600; downstream <= 48,000 | live | 42,667 / 7,200 | reached `0x00c8`, then `NO CARRIER`; no XID/SABME |
| upstream <= 9,600; downstream <= 40,000 | live | 40,000 / 7,200 | briefly reached `0x00cc`, then went offline; no LAPM |
| upstream <= 24,000; downstream <= 48,000 | live | 41,333 / 7,200 | PM `0x1930` swept through unrelated DM immediately after release |
| upstream exactly 9,600; downstream <= 48,000 | bypassed | transient 42,667 / 9,600 | PM `0x3180` published 9,600, then PM `0x31d5` replaced it with index 0 before sync; `NO CARRIER` |
| upstream <= 24,000; downstream <= 48,000 | bypassed | 42,667 / 7,200 | `CONNECT 42667`, XID/SABME, and two upstream LAPM I frames |
| upstream <= 24,000; downstream <= 32,000 | bypassed | none | PBX `BUSY`; excluded |

This narrows Session 106's worker conclusion. The 32-bit downstream case has a
real stable hardware proof, but legal width 31 is now proven unsafe. In the
41,333/7,200 call the first corrupt writes were all the native PM `0x1930`
store: `DM(0x3f61)=fac1`, `DM(0x3f62)=053f`, and `DM(0x3fb0)=fbc1`, with
`I0` equal to each victim address. Publishing descriptor offset 5 as `ffff`
is therefore necessary but not sufficient for every V90D rate. Widths other
than 31 and 32 remain unqualified; the current `21..42` release gate is not a
general safety proof.

The exact-9,600 bypass gives an independent upstream blocker. PM `0x3180`
repeatedly wrote `DM(0x3f62)=11ea`, which is a valid 9,600-bit/s selection.
Immediately before data state PM `0x31d5` wrote `11e0`, erasing the speed index.
Thus the physical negotiation can select 9,600 upstream, but the final rate
handoff discards it. Conversely, the successful bypassed 42,667/7,200 call
received XID, SABME, and two upstream I frames, proving the 7,200-bit/s
upstream transport itself works.

There are now three distinct boundaries rather than one vague upstream
failure: the pre-rate DIL/training lottery; PM `0x31d5` clearing a selected
9,600 upstream rate before sync; and the V90D bulk worker's rate-dependent
out-of-bounds sweep at 31 downstream bits. Further rate sweeps must keep
`EICON_V90D_BULK_ADAPTER=0` until each datagram width has been independently
qualified, otherwise a worker fault can masquerade as a negotiation result.

## Session 108: fail closed on unqualified V90D bulk widths

The Session 106 release gate treated every legal V.90 datagram width as safe,
but Session 107 proved that legality is not a worker-safety invariant: width 31
corrupts DM while width 32 has the only stable hardware proof. The gate now
requires three matching facts before restoring PM `0x19c8`: read-DB `0x81`
(`DM(0x3f61)`) must be a V.90 downstream speed word, its encoded width must
equal `DM(0x1e4f)`, and that width must be on the explicit qualified allowlist.
The allowlist currently contains only 32.

Read-DB `0x82` (`DM(0x3f62)`) is no longer accepted as a fallback release
signal. It describes the analogue V.34 upstream and cannot establish that the
PCM-downstream worker parameters are coherent. Width 31 and every other
unqualified width consequently remain behind the RTS hold instead of allowing
PM `0x1930` to sweep into the rate/state block. This is a memory-safety fix,
not a claim that the native worker has been repaired for those widths; each can
be added only after an independent hardware proof.

## Session 109: preserve an exact upstream selection through the quality handoff

Disassembly corrects Session 107's description of PM `0x31d5`. It is not an
unconditional erasure of a negotiated rate. PM `0x316a..0x3172` intersects the
peer's V.34 rate mask (`DM(0x1e3f)`), the local mask (`DM(0x210b)`), and a
quality-derived ceiling constructed from `DM(0x20ba)`. If that intersection is
empty, PM `0x31d1..0x31d5` deliberately publishes the no-common-rate setup:
`DM(0x3f9b)=0`, `DM(0x204e)=3`, and `DM(0x3f62)=0x11e0`.

The exact-9,600 archive shows why the handoff can nevertheless be too strict.
At sample 146302, PM `0x3180` selects `0x11ea` with peer mask `0x0008`, local
mask `0x1ffe`, ceiling 8, and smoothed quality `0x0069`. At the final handoff,
the same exact peer/local masks remain but the ceiling has transiently fallen
to 3 while the smoothed quality is `0x02cf`; `(1 << 3) - 1` excludes the sole
bit `0x0008`, so the no-common-rate branch is correct for its instantaneous
inputs. A broad-rate archive has the same final ceiling and quality
(`0x02e1`) and therefore selects 7,200 from its lower offered bits.

The shim now retains the complete setup from a genuine earlier selection:
the encoded rate word plus `DM(0x3f9b)` and `DM(0x204e)`. It restores those
three words only when all of these conditions hold: the peer mask contains
exactly that one rate, the local mask still permits it, the firmware has
published `0x11e0`, and the final ceiling excludes the selected bit. Broad
rate negotiations and selections already inside the ceiling are untouched.
`EICON_V90D_PRESERVE_EXACT_UPSTREAM=0` disables the guard for A/B testing.

With the bulk worker bypassed, replay of the exact-9,600 archive now preserves
`0x11ea/3/9` and continues through outer states `0x00c6`, `0x00c8`, `0x00ca`,
`0x00cc`, and `0x00d0` instead of publishing no rate. Because replay is open
loop, that establishes the local handoff and state progression only; it cannot
prove that the peer accepts the retained rate. The Python regression suite is
225 tests. `tools/cx_v90_rate_probe.py --endpoint-pty ...` now treats the live
test as one bilateral assertion: it waits for the Conexant payload at the V.42
PTY, injects a distinct reverse payload there, and requires that payload at the
Conexant DTE before collecting `ATI6`/`ATI11` and hanging up. A live
exact-12,000 call remains required: the earlier
`v90-exact-u12000-d48000-b1` structured snapshots contain no valid
`DM(0x3f62)=0x11eb` publication before their unrelated state corruption, so
they cannot supply that proof. The upstream-above-9,600 goal remains open.

## Session 110: native V90D selects and preserves an exact 12,000 upstream rate

The Conexant accepts the exact request
`AT+MS=V90,1,12000,12000,300,48000`. Its sole upstream capability bit is
`DM(0x1e3f)=0x0010`; the local mask remains `DM(0x210b)=0x1ffe`. The final
firmware quality limit still falls to 3, which excludes that bit, so the
exact-offer guard now raises the native mask length to 5 before the final
selection rather than restoring a rate only after the firmware rejects it.

A live call then made the complete native selection:

```text
DM(0x3f62) = 11eb       V.34 speed index 11 = 12,000 bit/s upstream
DM(0x3f9b) = 0004       selected capability bit number
DM(0x204e) = 000c       rate-derived setup parameter
DM(0x3f61) = 202b       42,667 bit/s PCM downstream
```

It advanced through synchronous states `0x00c8/0x00ca/0x00cc/0x00d0` with the
42,667/12,000 pair intact. This proves that the physical negotiation and
firmware handoff can exceed 9,600 upstream. It is not yet the goal's terminal
proof: with the native bulk worker held, the Courier never emitted `CONNECT`,
and a clean raw mailbox run contained no valid HDLC frames (98 bad candidates,
1,907 aborts). Those bytes are still Phase-4/retrain traffic rather than a
bilateral LAPM data stream.

The exact-12,000 native-worker release supplied the missing safety
counterexample. Width 32 is not generally safe: shortly after release it
destroyed unrelated DM even though the rate/count block and retained lower
limit were coherent. The qualified-width set is therefore empty by default.
`EICON_V90D_QUALIFIED_BULK_WIDTHS` exists only to reproduce an archived suspect
width under instruction tracing.

## Session 111: replace the unsafe V90D worker with its bounded database contract

An instruction replay of the exact-12,000 capture localized the visible
destructive writes more precisely than the earlier PM-`0x1930` watches. The
V90D adaptive update at PM `0x1b64..0x1b6a` walks `I4` upward in four-word
steps; PM `0x1b69` and `0x1b6a` eventually overwrite `DM(0x3f62)`,
`DM(0x1ff7)`, and the rest of the page state. The first observed coefficient
window begins near `DM(0x2a04)`, but after native bulk release the pointer is
no longer bounded there. NOPing those stores would disable adaptation and is
not a repair.

The ADDSP guide provides a smaller, explicit boundary that does not require
emulating this corrupted internal workspace. At 8 kHz the page publishes:

```text
DM 3fbc/3fbd   Nearbulklength / BulkLength, in X/Y sample pairs
DM 3fbe/3fbf   BulkInputX / BulkInputY
DM 3fb6/3fb7   near-delayed X / Y outputs
DM 3fb8/3fb9   oldest (far) X / Y outputs
```

The normal `0x03cd/0x041d` lengths are 973/1053 samples, about 122/132 ms at
8 kHz, consistent with the card's echo-tail timing. `PortableBulkDelay` now
implements exactly this ABI with a bounded deque of X/Y pairs. It starts under
the firmware's existing `DM(0x3fc1)&0x0400` enable bit, clears on invalid or
changed lengths, and rejects zero, reversed, signed, or larger-than-ADSP
descriptors. PM `0x19c8` remains `RTS`, so no datagram width can re-enter the
unsafe native worker. `EICON_V90D_PORTABLE_BULK=0` retains the held path for
diagnosis.

Twenty focused bulk/rate tests and the full 229-test Python suite pass. A live
exact-12,000 call must still show Courier `CONNECT`, sustained LAPM, exact
payload in both directions, and no watched-state corruption before this can be
called hardware-verified.

## Session 112: the bulk delay lengths are zero, because the seeder runs before its input

Sessions 105–111 repaired the delay line's *bounds* — the retained `0xffff`
lower limit, the qualified-width gate, the bounded `PortableBulkDelay`. None of
them ever gave it a *length*. `DM(0x3fbc)` (`Nearbulklength`) and `DM(0x3fbd)`
(`BulkLength`) read `0x0000` on every one of the 114,621 page-14 frames of
`v90-bulk-dm5-live1`, and identically on `v90-exact-u12000-d42667-live1`.

`PortableBulkDelay` was therefore correct and inert: 114,621 services, 114,621
rejections of an invalid descriptor, `DM(0x3fb6..0x3fb9)` pinned to zero. It had
never once run. The echo canceller has had no reference signal at all.

### The seeder, and why it misses

PM `0x3232..0x3243` with its tail at PM `0x1085/0x1086` is the only site that
turns a measured delay into delay-line lengths:

```text
3232: AX0 = DM(0x3F04)      delaycorrection, write-DB +0x24, 0x000c as shipped
3233: AY0 = 0x0025
3234: AR  = AX0 + AY0
3235: DM(0x3FBC) = AR       Nearbulklength = 0x25 + delaycorrection
3236: AR = DM(0x3FCB)
3238: IF LE JUMP 0x323C     skip when no round trip has been measured yet
323a: AR = AR + DM(0x3FBC)
323b: DM(0x3FBC) = AR       Nearbulklength += DM(0x3FCB)
323c: DM(0x0A5D) = min(Nearbulklength + 0x50, 0x0B00)
3243 -> 1086: DM(0x3FBD) = DM(0x0A5D)     BulkLength
```

`DM(0x3fcb)` is the measured round trip, `DM(0x3fc9) * 10/3` from PM `0x2cb4`.
Per-frame coverage puts the timing beyond doubt:

| sample | page | event |
|---|---|---|
| 31659 | 0x0260 | PM `0x3235` fires, `DM(0x3fcb)=0x0000` |
| 33455 | 0x0260 | PM `0x3235` fires, `DM(0x3fcb)=0x0000` |
| 45379 | 0x026a | page-14 entry; `DM(0x3fcb)=0x01a6` and stable for the residency |

Both firings land about 1.5 s before the measurement exists, so the `IF LE`
branch is taken both times and the seed is the bare `0x31` floor with no echo
delay in it. PM `0x1085/0x1086`, the only writer of `BulkLength`, executes zero
times in the whole run. Nothing re-seeds afterwards. The measurement is then
available and correct for the entire page-14 residency and is never used.

### This reframes Sessions 90–93 and 101

PM `0x1930`'s modulo bound is zero because **`BulkLength` is zero**, not because
descriptor offset 5 was missing. The Session 105 `0xffff` repair bounds the
pointer and genuinely stopped the memory corruption, but it treated a symptom.
The same applies to the width qualification: `DM(0x1e4f)` is the V.90 datagram
bit width and has nothing to do with the delay line. Sessions 107–110 qualified
and then disqualified widths 31 and 32 against it; width was never the variable,
because every release was against a zero-length delay line.

It is also not V90D-specific. The seed is computed on page `0x0260`, upstream of
the V.34/V90D fork, so V.34 (`0x0261`) inherits the same zero-length line while
keeping the native worker live. That is the shape of the Session 101 collapse.

### The repair

`bulk_delay_seed()` recomputes the firmware arithmetic, and
`_service_bulk_lengths()` publishes it once `DM(0x3fcb)` is positive, for both
`0x0261` and `0x026a`. It holds rather than writes once: PM `0x19e2/0x19e4`
restore both words from the saved context at `DM(0x3608)/DM(0x3609)` at the top
of every frame and PM `0x1a13/0x1a18` write them back one `0x20` decrement low
at the bottom, so a single write survives one frame and an alternating value
would flush the ring every frame. Publishing the same pair into both the live
words and the saved context keeps the firmware's own ping-pong intact. If the
firmware ever publishes lengths of its own that are neither the seed nor the
seed less one decrement, the hold stands down and the firmware's value wins.

`EICON_BULK_DELAY_SEED=0` restores the old behaviour for A/B.
`EICON_BULK_DELAY_EXTRA_PAIRS` adds sample pairs on top, for tuning the SIP
leg's packetisation and jitter-buffer delay if the card's own measurement turns
out not to include it; default 0, 8 pairs is 1 ms.

Offline, `v90-bulk-dm5-live1` now seeds 471/551 pairs (58.9/68.9 ms) and
`PortableBulkDelay` services all 114,621 page-14 frames with zero flushes,
against 114,621 rejections before. Nine new tests; the suite is 238 and passes.

### Hardware verification is blocked by a separate, older failure

Six live Conexant calls were placed. The seed works live and tracks the measured
round trip per call — 471/551, 507/587, 581/661 and 625/705 pairs across
attempts, 58.9 to 78.1 ms near — and the bounded delay reported active on
hardware for the first time.

No call reached data mode. All six stalled at `TrnProgress 0x0050` immediately
after page-14 entry, published neither rate word, and ended `NO CARRIER` with
`TX datagrams 0/0`. **This reproduces with `EICON_BULK_DELAY_SEED=0`**, and also
under Session 106's own successful configuration
(`EICON_V90D_PORTABLE_BULK=0 EICON_V90D_QUALIFIED_BULK_WIDTHS=32`), so it is not
caused by this change. Broad-rate and exact-12,000 requests behave identically.

Replay cannot arbitrate: replaying today's captures *and* the archived
`v90-bulk-dm5-live1` both stop at `0x0050`, because replay is open loop and the
recorded peer stream past that point was produced against a card that was
answering. The last call to pass `0x0050` live was Session 106's.

So the rate-ceiling claim — that a working echo canceller lifts `DM(0x20ba)` and
stops the ceiling collapsing to 3 — remains **unproven on hardware**. It is
supported by the mechanism and by `DM(0x20ba)` reading `0x088d` on pages
`0x025f`/`0x0260` and `0x0000` for all of page `0x026a`, and by nothing else.
Finding out why nothing gets past `0x0050` any more is now the blocker in front
of it, and it is independent of the echo canceller.

## Session 113: the 0x0050 stall was a dispatch vector, and the bulk delay does not cap upstream

### The stall

Every Session 112 hardware call died at `TrnProgress 0x0050` with `TX datagrams
0/0`. It bisects offline: replaying `v90-bulk-dm5-live1` — the capture that
connected in Session 106 — reproduces it exactly, 114,621 page-14 frames at
outer state `0x0050`. Two flags cleared it, `EICON_V90D_BULK_ADAPTER=0` and
`EICON_V90D_PORTABLE_BULK=0`, and the only thing they have in common is that
both stop `PortableBulkDelay` servicing.

`PortableBulkDelay` published the near/far outputs at DM `0x3fb6..0x3fb9`.
DM `0x3fb8` is not an output:

```text
19f3: 8bfb80  I4 = DM($3FB8)
19f4: 0b001f  CALL (I4)
```

The firmware holds `0x3cea` there, and `0x3cea` sets the DM `0x3fc1` `0x0400`
worker-enable bit and jumps to the generator dispatch at `0x2a56`. Writing a
delay sample over it called the page into garbage every frame, which is why the
generator went quiet and nothing was ever transmitted.

The database base is DM `0x3ee0` for every offset. That is the only base
consistent with the mappings already proved — write-DB `0x24` is
`delaycorrection` at DM `0x3f04`, read-DB `0x81/0x82` are the rate words, and
`0xdc..0xdf` are the lengths and inputs at DM `0x3fbc..0x3fbf`. Session 111 used
`0x3f60` for the `0x56` group alone. The near and far output pairs are therefore
DM `0x3f36..0x3f39`, and PM `0x19e7/0x19e8` (`DM(0x3F36) = DM(0x3F38)`)
context-switch that pair exactly as PM `0x19e2/0x19e4` do the lengths.

With that corrected, replay walks `0050 0052 0053 0060 0062 0064 0066 0068 006a
0070 0072 0074 0076 0078 007a 007b 007c 0080 00a6 00b0` with the delay enabled,
matching the disabled path. Ten live Conexant calls then produced four
`CONNECT 42667`s at `TrnProgress 0x00d0` with CTS/DSR/DCD and exact bilateral
payload, against nought from six before. The remaining six failed in the two
documented ways — the `0x0060 ↔ 0x0062` INFO loop and pre-data DIL misses — so
the lottery is back, but it is a lottery again rather than a certainty.

### The real echo delay, measured

`tools/echo_delay.py` cross-correlates the captured TX against the captured RX,
which measures the live path's echo directly. Every capture puts the peak at
41–100 sample pairs, 5.1–12.5 ms, standing about 35× clear of the noise floor.
`DM(0x3fcb)` reaches 490–540 pairs, 61–68 ms — an order out. That fits what
`v90_dpcm_replay.py` already documents about its source: `DM(0x3fc9)`, which
`DM(0x3fcb)` is 10/3 of, is an elapsed-time counter the INFO page maintains at
PM `0x3caf/0x3cb4`. The bare floor PM `0x3232` computes before the addend,
`0x25 + delaycorrection` = 49 pairs = 6.1 ms near and 129 = 16.1 ms far,
brackets every measurement. The addend is now opt-in behind
`EICON_BULK_DELAY_MEASURED=1`.

The Session 112 stand-down guard was also wrong: it fired on an incoherent
`near=17 far=0` transient in the second frame of every call and handed the delay
line straight back to zero. A candidate now needs `0 < near <= far` and twelve
consecutive frames, after which the firmware's own genuine publication of
439/519 pairs is what ends the hold.

### The bulk delay is not what caps V.34 upstream

Three configurations were run to `0x00d0` and their final handoff compared:

| bulk delay through the data phase | `DM(0x0fcf)` quality | upstream |
|---|---|---|
| 541/621 pairs (68/78 ms), measured seed | `0x02e2` | 7,200, retrained to 4,800 |
| 439/519 (55/65 ms), the firmware's own | `0x02d2` | 7,200 |
| 49/129 (6.1/16.1 ms), held with `EICON_BULK_DELAY_HOLD_ALWAYS=1` | `0x02d0`, `0x02d5` | 7,200 |

The quality metric is flat across a 10× range of bulk delay, and `DM(0x20ba)`
stays at 3 in all of them. **The echo bulk delay does not govern the upstream
ceiling.** The Session 112 hypothesis — that a working canceller would lift
`DM(0x20ba)` — is disproved.

`limit` is `DM(0x20ba)` read directly, not derived, and `quality` is
`DM(0x0fcf)`. Session 109's archive shows `0x0069` with ceiling 8 transiently
mid-call and `0x02cf` with ceiling 3 at the final handoff. Our `0x02d0..0x02e2`
at the same point is that same number. So the cap is the steady state of this
path and predates all of this work; it is not a regression.

The open question is therefore what makes `DM(0x0fcf)` degrade from `0x0069` to
`0x02d0` over a call, and that is a receiver/line question — equaliser
convergence, or the analogue leg genuinely being a 7,200 upstream path. Nothing
in the echo canceller chain is still implicated.

## Session 114: the INFO word is decoded correctly; the peer really does send those zeros

Sessions 102–104 traced the V.34 originate stall to word 0 of the received INFO
message reading `0x2000`, so that `DM(0x3F89)` — bits 6..12 of that word — comes
out zero and the caller's script branches to state `0x0060` and waits there.
Every step of that chain reads the firmware's own output, through this
project's emulation of its demodulator and framer, so a defect anywhere in that
stack is indistinguishable from a defect in the peer's message.

`tools/v34_info.py` removes the stack. It demodulates the captured audio in
Python and accepts a message only when the transmitter's own CRC validates, so
a frame it reports is a frame that was on the wire. Framing is the one
`tools/info_cc_framer_probe.py` documents from PM `0x3520` — fill ones, the
10-bit sync code `0x372`, the payload, then CRC-16 (reflected `0x8408`, preset
`0xffff`, sent LSB first and uncomplemented). Nothing else is assumed: the
payload length is searched rather than taken from `DM(0x1651)`.

### The two directions do not share a carrier

The first run found nothing but our own transmissions, echoed back 5–10 ms
later — the same echo `tools/echo_delay.py` measures. A tone scan of the
receive direction explains it: the card's control channel sits at **1200 Hz**
and the peer's at **2400 Hz**, both 600 bit/s. Decoding a `.rx.ulaw` at 1200 Hz
alone recovers only the echo and reports the peer as silent.

Between them, at 3.7–4.2 s, is a signal whose energy falls on multiples of
150 Hz across the whole band — the V.34 line probe, not a control channel.

### The false-positive rate is zero, so a reported frame is a real one

Over 24.5 s of signal that is definitely not the control channel — the line
probe, and two windows of post-handoff data — plus 10 s of synthetic Gaussian
noise, the search reports **no frames at all** at either carrier. Sync plus CRC
is a strong enough acceptance test that the frames below can be believed
individually.

### The measurement, on the two calls that fork

`abifix-2` and `abifix-3` are adjacent calls in one run. `abifix-2` loaded the
V.34 page `0x0008`/overlay `0x0261` and parked at `0x0060` for the rest of the
call; `abifix-3` loaded V.90 `0x000e`/`0x026a` and reached `0x00d0`. Their last
answer-side message before the handoff:

```text
abifix-3  5.400s  36 bits  000000000000011110010000101101011111
abifix-2  5.372s  36 bits  111100000000000000100100000001110111
```

Both validate. They are different messages — not one message recovered at two
sync offsets; the content after the leading run does not align under any shift.

**The decode agrees with the firmware.** The card published `DM(0x3F88)=0x0000`
on `abifix-3` and `0x000f` on `abifix-2`, and the two payloads begin `0000` and
`1111`. That fixes the packer's bit order as well: `DM(0x3F88)` is word 0's low
nibble under LSB-first packing at PM `0x358E`, and the MSB-first reading
contradicts the capture on `abifix-2`. The tool prints both orders.

### What this retires

Payload bits 6..12 — the `DM(0x1705)` field, the whole of `DM(0x3F89)` — are
zero on the wire, in both calls, under either bit order. So:

- **`DM(0x3F89)=0` is a correct decode of what the peer transmitted.** It is not
  a truncated array, a slot cadence, a marginal slicer, or a framer fault. The
  receiver read the message that was sent. Sessions 102–104's symptom stands;
  their attribution of it to the receive path does not.
- **The lengths match.** Session 104 left open whether each end expects the
  length its peer transmits. The peer's first message is 17 bits and its later
  ones 36, against the framer's `DM(0x1651)` of `0x0110` (17) and `0x0260` (38).
  There is no mismatch to fix.

The stall therefore has to be explained by what the caller does with a
legitimately zero field, or by what our own INFO0c asks the peer for, not by
recovering a value the peer never sent.

### The live failure mode is the V.34 page, not an INFO loop

A survey of the 256-word interface dump across ~60 live captures separates the
outcomes cleanly by which page the call lands on:

| overlay loaded at handoff | outcome |
|---|---|
| `0x026a` (V.90) | proceeds; `0x00b3`, `0x00c0`, `0x00c6`, `0x00d0` |
| `0x0261` (V.34) | parks at `0x0060`/`0x0062` for the rest of the call, always |

`seed-native-w32-1` and `v90-exact-u12000-safe-live9` each ran V.90 twice, fell
back on a third attempt, landed on `0x0261`, and stopped. `abifix-2` went to
V.34 directly. No capture in the archive leaves the V.34 page once on it.

So the "`0x0060 ↔ 0x0062` INFO loop" failure class and the loopback blocker are
the same thing — the V.34 originate script — and `DM(0x3F89)=0` is true of the
successful V.90 calls too. It simply does not matter on the V.90 page.

`BaudInfo` reads `0x3064` on every call that lands on V.90 and `0x305d` on
every call that falls back to V.34, which is Session 78's V90D/V.34 split
holding across the whole archive. `abifix-2`'s `0x3000` — the low byte absent
altogether — is the only capture of a third value.

`tests/test_v34_info.py` covers the CRC against the X-25 check value, both
packing orders against the two captured nibbles, and the demodulator against
synthetic frames with noise, a carrier phase offset, a 40 Hz frequency error,
and the opposite direction's carrier. Suite is 254.

## Session 114b: the V.34 page does not transmit, and that is the whole stall

Session 102 read state `0x0060` as "wait until the line has been quiet for 50
ticks" and observed, on the loopback, that `|MR1|` never stayed under the
threshold. That is true of the loopback, where both ends are this emulator.
It is not what happens live.

Timeline of `abifix-2`, which loaded the V.34 page `0x0261` at 5.56 s, against
the line level in each direction:

```text
  time    rx dBFS   tx dBFS   states
    5      -25.7     -32.4    0x0041 0x0044 0x0046 0x004f 0x0060 0x0062
    7      -25.6     -99.0    0x0060 0x0062
    9      -25.6     -99.0    0x0060 0x0062
   11      -33.9     -99.0    0x0060 0x0062
   13      -67.5     -99.0    0x0060 0x0062
   ...
   49      -67.6     -99.0    0x0060 0x0062
```

Two things follow, and the second retires the first reading.

- **The line does go quiet.** The peer keeps transmitting for about seven
  seconds, gets nothing back, and stops. From 13 s the receive direction is at
  −67 dBFS for the remaining 36 seconds. The caller still oscillates
  `0x0060 ↔ 0x0062` throughout. So the state pair is not blocked on line
  energy, and the quiet-detector account does not explain the live failure.
- **The card stops transmitting at the page load and never transmits again.**
  `-99.0 dBFS` is exact digital silence, from the instant overlay `0x0261`
  becomes resident.

The same measurement on the three archived calls that reached the V.34 page,
relative to their own page load, with a V.90 call for contrast:

| call | page | −1 s | +1 s | +3 s | +6 s |
|---|---|---|---|---|---|
| `abifix-2` | `0x0261` | −30.6 | **−99.0** | **−99.0** | **−99.0** |
| `seed-native-w32-1` | `0x0261` | −30.7 | **−99.0** | **−99.0** | **−99.0** |
| `v90-exact-u12000-safe-live9` | `0x0261` | −30.5 | **−99.0** | **−99.0** | — |
| `abifix-3` | `0x026a` | −30.4 | −99.0 | −30.2 | −31.6 |

The V.90 page goes quiet for a moment at handoff and then resumes. The V.34
page never does, in any capture.

### This is the Sessions 76–79 blocker, still open against hardware

Session 76 recorded exactly this: "Local TX RMS falls from 766 in the
5.3-second bin to zero at 5.4 seconds and remains zero." Session 78 ruled out a
hidden output path and concluded "the page truly fails to publish samples."
Session 79 found the PC-stack overflow behind it, fixed
`adsp2181_call()`/`adsp2181_modem_sample()`, got `GEN_CONTROL` nonzero and 77
nonzero V.34 samples out of an offline replay, and ended "live hardware
validation is next."

That validation never happened — the handoff has carried "V.34 has never been
tried against hardware since the tree changed" as an open blocker ever since.
It has now happened, by reading it out of the archive, and **the page still
publishes exact silence against hardware.** Session 79's fix was necessary but
is not sufficient, or has since regressed; 77 samples in a replay was a thin
margin to declare it cleared on.

### What this means for Sessions 102–114

The `0x0060` state, the `DM(0x3F89)` branch and the INFO word are downstream
scenery. A caller that emits nothing cannot complete a handshake whatever its
script state, and the peer's giving up after seven seconds is the direct
consequence. The loopback made this invisible: both ends are this emulator, so
both were mute, and the surviving symptom was a state number.

Session 114's decode still stands and is still worth having — the peer does
send zeros in bits 6..12, the receive path is sound, and the tool is the way to
read either direction of any capture. But the ranked next step is the
generator, not the script.

### A correction to Session 114

Session 114 said the peer's message lengths "match the framer's expectations,
so there is no mismatch". The peer's later message decodes at **36** bits
against the framer's `DM(0x1651) = 0x0260`, which is **38**. The decoder also
reports that message validating at several adjacent lengths, which is not an
artifact of ones-fill — a synthetic frame with the same tail validates at one
length only. The length question is therefore **not** settled and should not be
treated as closed.

## Session 114c: a live forced-V.34 call, and the correction it forces

Sessions 114/114b were read out of the archive. This is the first V.34 call
placed against hardware since the tree changed — the blocker the handoff has
carried open since Session 72 — and it changes the answer.

### Rebuilding the rig

`tools/cx_at.py` has been referenced by the handoff since Session 76 but is not
in the tree, so there was no way to place a call at all. It is restored here,
on termios rather than pyserial so it runs under the repo's own interpreter.
Device paths had also moved: the CX93001 that Sessions 72–79 reached at
`/dev/cu.usbmodem246802461` now enumerates at `/dev/cu.usbmodem123456781`, and
`cx_at.py ident` identifies what is attached before anything is dialled.

Attached now: two USR Courier V.Everything (`/dev/cu.usbserial-21210`,
`-21240`) and the CX93001 (`/dev/cu.usbmodem123456781`).

### Forcing V.34 on both ends removes the lottery

```bash
EICON_MODULATION=v34,0,,33600,,33600 /tmp/eicon-venv/bin/python -u tools/eicon_adsp_sip.py \
    --native-mips --force-info-after-v8 --native-bearer-activation --tx-prbs \
    --law pcmu --sip-port 5060 --rtp-port 4000 \
    --capture-prefix artifacts/interop/v34-live/callNN \
    --mips-kernel artifacts/eicon-dsp/build-117-926/kernel/0009-diva-server-pri-30m-kernel \
    --mips-tikrnl artifacts/eicon-dsp/build-117-926/tikrnl/0258-tikrnl81.f34-task \
    --registrar asterisk.net.cryan.nz --username 6001 --password 6001

/tmp/eicon-venv/bin/python -u tools/cx_at.py --dev /dev/cu.usbmodem123456781 \
    --setup 'AT&F' --setup 'AT+MS=V34,0,2400,33600' dial 6001 --wait 45
```

Both calls placed this way loaded page 8 / overlay `0x0261` at 5.5 s. That is
worth recording on its own: **the V.34 page can be reached deterministically**,
by denying V.90 at both ends, instead of waiting for a fallback. Every archived
V.34 landing was an accident of the DIL lottery.

The endpoint's SIP role is `answer` — the CX dials in — so the card is the
answering modem on these calls.

### The correction: it is not silence, it is a freeze

Session 114b concluded "the V.34 page publishes exact digital silence" from
`-99 dBFS` in three archived captures. Live, with V.34 forced, **the page
transmits `-27.8 dBFS`** — and the wire carries exactly **one distinct sample
value across twenty seconds**:

```text
first 40 TX samples at 20 s:
  1308 1308 1308 1308 1308 1308 1308 1308 1308 1308 ...
distinct values over 20-40 s: 1
```

So the correct statement is that the V.34 page **freezes**, and the archived
`-99 dBFS` was that freeze happening to latch zero. Everything stops together
and stays stopped for the remaining 46 seconds:

| | call 1 | call 2 |
|---|---|---|
| `TrnProgress` | `0x0072` | `0x0071` |
| `GEN_CONTROL` | `0x000f` | `0xc000` |
| `tx_ptr` | `0x3764` | `0x3764` |
| `tx_value` | `0x0514` | `0x0609` |
| wire | constant DC | constant DC |

This is Session 78's observation reproduced exactly — "it then freezes at
`0xf9a4` for all 23,810 observed page-8 samples" — with a different latched
constant. It was never a silence problem and never an output-format problem.

The peer behaves accordingly: it stops transmitting about a second after the
page loads (`-67 dBFS` from 6.6 s), waits, and the call is torn down.

### What this does and does not retire

- **Retired:** "the V.34 page publishes exact digital silence" (114b), and the
  `0x0060`/`0x0062` state pair as the signature. Forced V.34 freezes at
  `0x0071`/`0x0072` instead, so the state number is wherever the page happened
  to be when it stopped, not a meaningful wait.
- **Retired:** "V.34 has never been tried against hardware since the tree
  changed." It has now, twice, and the failure is a freeze.
- **Still open, and now the whole question:** what stops the page. Session 79
  found a PC-stack sentinel leak behind the same freeze and cleared it on 77
  nonzero samples in an offline replay; that did not hold. The next step is an
  execution trace on a forced-V.34 live call, not another replay.

`artifacts/interop/v34-live/call01` holds both calls.

## Session 114d: the freeze is rate-independent, and the action stream runs without the generator

Two live experiments on top of Session 114c's forced-V.34 recipe.

### An instrumented call: the dispatcher never stops, the generator fires once

`--watch-exec 0x23a0,0x23a3,0x23a7,0x290c,0x28be --watch-dm 0x2166,0x2165` on a
forced-V.34 call that froze at `TrnProgress 0x0072`:

| watched | hits |
|---|---|
| PM `0x23a0` generator action | **1** (called from `0x2490`) |
| PM `0x23a3`, `0x23a7` generator actions | 0 |
| PM `0x290c` stop branch | 0 |
| PM `0x28be` cursor init | 2 |
| `DM(0x2166)` action cursor writes | **1759**, for the whole call |
| `DM(0x2165)` | 2, both `0x0000` |

So the page is **not** stopped in the sense Session 77 looked for: the stop
branch is never taken, and the action cursor keeps cycling
`0x10 → 0x11 → 0x12 → 0x13 → 0x10` from the page load to the end of the call.
The dispatcher that drives it is the loop at PM `0x2816`:

```text
2816: I4 = DM($2166)            ; cursor
2817: NOP (MAC), AR = DM(I4,M5) ; fetch the action vector, post-increment
2818: DM($2166) = I4            ; store the advanced cursor
2819: I4 = AR
281a: CALL (I4)                 ; dispatch
2821: IF NOT CE JUMP $2808
```

which is why the writer PC settles at `0x2819` (1302 writes) with PM `0x2870`
doing the wrap to `0x10` (439). Session 79's `0x2834..0x2836` path accounts for
only the first 15.

**The dispatcher runs forever and never dispatches a generator action.** That
is a sharper statement of the Sessions 77–79 localization: not a stalled cursor,
not a taken stop branch, but four action slots that do not contain the
generator after the first pass.

One number worth following: 1759 cursor writes over 46.6 s is **~9.4 passes per
second**. A 3200-baud V.34 frame stream should drive this far faster, so either
this loop is not the per-symbol path or it is running about an order of
magnitude slow — the same order as Session 100's 0.65x, but much worse.

### A rate sweep: the ceiling changes nothing

Six forced-V.34 calls, `EICON_MODULATION=v34,0,,N,,N` against
`AT+MS=V34,0,2400,N`:

| ceiling | overlay from | frozen `TrnProgress` | `GEN_CONTROL` | `tx_value` | TX dBFS | distinct wire values in 20 s |
|---|---|---|---|---|---|---|
| 4,800 | 5.58 s | `0x0071` | `0x0014` | `0x18af` | −14.0 | **1** |
| 9,600 | 5.56 s | `0x0076` | `0x20be` | `0xfaff` | −27.8 | **1** |
| 14,400 | 5.54 s | `0x0076` | `0x20be` | `0xf904` | −24.9 | **1** |
| 19,200 | 5.54 s | `0x0071` | `0x00e7` | `0x0199` | −38.2 | **1** |
| 28,800 | 5.52 s | `0x0071` | `0xe055` | `0xfaf2` | −27.8 | **1** |
| 33,600 | 5.50 s | `0x0071` | `0xc000` | `0x0609` | −26.3 | **1** |

Every rate reaches the V.34 page at the same moment and every rate freezes. The
rate changes only *where* it stops and *what value gets latched* — and the
latched values look like residue, not control words (`0xe055`, `0x20be`,
`0x00e7`), which is what a register holding whatever it last had looks like.

So **the rate ceiling is not a variable in this failure**, and the freeze is not
a specific state waiting on a specific condition: `0x0071`, `0x0072` and
`0x0076` all occur, and none of them is the `0x0060` the loopback pointed at.
Whatever stops the page stops it wherever it happens to be.

### Rig note

`cx_at.py dial` reports `NO CARRIER` on these calls even though the call
connects and runs for a full 50 s of RTP in both directions. Draining the port
and ignoring the first two seconds after `ATD` did not suppress it, so it is
something the CX93001 emits mid-call rather than stale buffer. **Read the
endpoint log, not the dialler's exit code, for whether a call happened.**

## Session 114e: the page is not stuck, it is repeating — and the gate is DM(0x213B) bit 15

Watching PM `0x281a` — the `CALL (I4)` at the end of the dispatcher loop — with
`I4` logged names every action the V.34 page actually dispatches. Over a live
forced-V.34 call there are exactly three, each 434 times:

```text
  434  i4=285c
  434  i4=2868
  434  i4=2879
```

Never PM `0x23a0`, `0x23a3` or `0x23a7`. And the third of them is why:

```text
2879: CALL $286B            ; -> the rewind below
287a: AY1 = $001F
287b: JUMP $0DDC

286b: CALL $2916
286c: AX0 = DM($2166)       ; the action cursor
286d: AY0 = $FFFD           ; -3
286e: AR = AX0 + AY0
286f: DM($2166) = AR        ; cursor -= 3
2870: RTS
```

**PM `0x286b` rewinds the cursor by three.** So the cursor walking
`0x10 → 0x11 → 0x12 → 0x13 → 0x10` is not a dispatcher cycling a stale table
and it is not a reset — it is a deliberate *repeat* construct: three actions,
then rewind, indefinitely, until something moves the state on. Session 79 read
PM `0x286d..0x2870` as "resets it to `0x10`", which is the same instructions
seen as an initialisation rather than as a loop.

That also explains the arithmetic Session 114d left hanging: 1759 cursor writes
against 1302 dispatches is 4 writes per 3 dispatches, because the rewind writes
the cursor without going through `0x281a`.

### The condition it is waiting on

The first of the three actions is the test:

```text
285c: CALL $2916
285d: CALL $0D4D
285e: AX0 = DM($213B)
285f: AY0 = $8000
2860: AF = AX0 AND AY0
2861: IF NE JUMP $0900      ; bit 15 set -> leave, into the kernel at 0x0900
2862: RTS                   ; otherwise fall through and repeat
```

So the V.34 page repeats its three actions until **bit 15 of `DM(0x213B)`** is
set, and on this path it never is. That is the whole freeze: not a crash, not a
scheduler fault, not a stalled cursor — a documented wait whose condition does
not arrive, exactly as the caller's `0x0060` block was a wait whose condition
did not arrive. The generator is never dispatched because the page has not
reached the state that dispatches it.

This retires the Sessions 77–79 framing of the problem. "The page truly fails
to publish samples" and "which writer keeps the action cursor in its
receive/wait loop" both describe the symptom of a wait, and Session 79's
PC-stack fix addressed a real emulator defect that was not this one.

### Where DM(0x213B) comes from is the next question

Disassembling the whole of the V.34 overlay's PM page and grepping every
reference to `DM(0x213B)` gives **17 reads and no stores**:

```text
0b9a 0c20 0c52 0d5e 0ddc 0f4d 2552 2560 27c0 285e 2ae7 2cc0 31a8 31f2 324f 3f98 3fad
```

It is read all over the page and written nowhere in it. Two readings, and they
are distinguishable by one live watch rather than by more static reading:

- It is field 4 of the V.34 script record, whose base Session 102 established
  as `0x2137`, in which case the script interpreter writes it indirectly
  through a pointer and a static grep cannot see the store.
- It is supplied from outside the overlay — the kernel, another page, or this
  harness — in which case nothing in the V.34 page can ever set it and the
  question is who should.

**Next probe: `--watch-dm 0x213b` on a live forced-V.34 call.** That names the
writer, or proves there isn't one, in a single call.

## Session 114f: the gate is a script record field, written once, and never updated

`--watch-dm 0x213b` on a live forced-V.34 call answers Session 114e's question
outright. Over the whole call `DM(0x213B)` is:

- **read 126,738 times**
- **written twice**: `0x0000` from PC `0x0d94`, then `0x0200` from PC `0x2e2e`

`0x0200` has bit 9 set and bit 15 clear, so the gate at PM `0x285e..0x2861`
never opens.

PC `0x2e2e` identifies the writer exactly. It is the loop at PM
`0x2e25..0x2e2e`, whose store is `0x2e2d` — the **high-byte record decoder**,
which Session 102 established is the *answering* side's half of the
byte-interleaved script:

```text
2e24: SE = $FFF8
2e25: NOP (MAC), AR = DM(I4,M5)
...
2e2d: DM(I0,M1) = SR0, AR = MR1 XOR AF
2e2e: IF NE JUMP $2E25
```

So `DM(0x213B)` is **field 4 of the V.34 script record** based at `0x2137`, and
it takes its value from the block the interpreter loaded. That closes the
Session 114e fork: nothing outside the page supplies it, and the script
interpreter writes it indirectly through `DM(I0,M1)`, which is why a static
grep found 17 reads and no stores.

### Why that is a deadlock as configured

The record is written **once**, when the block loads. The three actions then
repeat, gated on a field that only a *new* record load can change — and a new
record load requires the state to advance, which the gate blocks. Nothing in
the loop can ever satisfy its own exit condition.

That is not a firmware defect; the real card completes V.34. It means **the
wrong block was loaded**, or the right block was decoded from the wrong half of
the interleaved word. Both are selection questions, decided before the page
starts running.

### The configuration word that selects the decoder is empty

`DM(0x3F94)` is `0x0000` for the entire call, start to finish. Sessions 102 and
103 established two of its bits:

| bit | selects | with `0x0000` |
|---|---|---|
| 3 | the record decoder — `DM(0x2198)` is 8 on the caller, 0 on the answerer | answer / high-byte path |
| 1 | the INFO message length, `0x0110` or `0x01E0` | `0x0110` |

The answer path is the correct one for these calls — the CX dials in, so the
card is the answering modem, and the observed writer `0x2e2d` is the answer
decoder. So the role is *not* obviously wrong. But the word carrying it is
uniformly zero, which is worth establishing rather than assuming: an
under-initialised configuration word and a correct-by-accident role bit look
identical when the correct value is zero.

Session 77 recorded `GEN_SETUP1=0x0484` being "correctly imported into both
`DM(0x219c)` and `DM(0x21e5)`". `DM(0x3F94)` sits in the *read* database
(`0x3F60..0x3FDF`), so it is not that word, and the relationship between them
has not been established. **Do not assume `DM(0x3F94)` should be non-zero.**

### Next

The question is now upstream of the page: which script block the V.34
interpreter should have loaded for this state, and what selects it. The Linux
driver's early initialisation is the place to look for anything this harness
does not replicate — `docs/divas4linux-master`, and the ADDSP database staging
in `tools/eicon_dsp_assign.py` / `tools/eicon_dsp_stage.py`.

## Session 114g: the CAI is faithful, and a block that opens the gate does exist

An audit of the harness's early initialisation against the shipping Linux
driver, following Session 114f's finding that the V.34 page waits on script
record field 4.

### The CAI is not where the gap is

`build_cai()` in `tools/eicon_idi.py` hardcodes three fields that looked like
obvious V.34 candidates:

```python
cai[21] = 0   # disabled symbol rates
cai[22] = 0   # modem info options
cai[23] = 0   # transmit level adjust
```

The driver does the same. `tty_module/isdn.c`'s `ISDN_PROT_MODEM` branch of
`putcai()` writes `p[21] = 0; p[22] = 0; p[23] = 0;` literally, and the CAPI
path in `kernel/message.c` only fills them from `mdm_cfg[6]` when the
application supplies a descriptor of length ≥ 24:

```c
cai[22] |= (byte) w;        /* info options mask */
cai[21] |= (byte)(w >> 8);  /* disabled symbol rates */
```

Nothing in the tty path ever supplies that. **Zero is the driver's own
default, not a missing initialisation**, and every other modem field in
`build_cai()` matches `putcai()` field for field. Do not re-audit the CAI.

### The script does contain a block that opens the gate

The answer-side record decoder at PM `0x2e24` takes the field from the high
byte of word 0 of a three-word entry and assembles the value from the high
bytes of words 1 and 2. Scanning the script area `0x1900..0x2100` of the
overlay's DM image for field-`0x04` entries — field 4 being `DM(0x213B)` off
the `0x2137` record base — finds both of the interesting values:

```text
0x1a3d: 0404 0000 0202  -> value 0x0200     the value observed live
0x1be7: 0401 0000 8240  -> value 0x8200     bit 15 set
```

The first matches what Session 114f watched being written, in the shared
sequencer-A script whose base Session 102 established as `0x1A2E` — `0x0404`
in word 0 means both roles read field 4 there.

The second matters more: **a block exists whose field 4 has bit 15 set**, so
the gate at PM `0x285e` is openable by loading a different block. The page is
not waiting on a condition the script can never express; it is sitting on the
wrong block.

**Caveat on that scan.** It tests every word position rather than walking the
block chains, so entries not on a real three-word boundary are false
alignments — `0x1c37`/`0x1c38` overlap, for instance, and cannot both be real.
`0x1be7` has not been confirmed to lie on a chain. Before building on it,
walk the answerer's script from `0x1E81` properly, the way Session 102 walked
the caller's from `0x1A2E`, and confirm the entry is reachable.

### Where this leaves the fix

The remaining question is block *selection*: which record the interpreter
should load for this state, and what advances it there. That is upstream of
everything Sessions 76–79 and 102–114 examined, and it is the last thing
between here and a working V.34 page.

## Session 114h: state 0x0066 is the only state that opens the gate, and it is skipped

`tools/v34_script.py` walks the V.34 page's state scripts out of the overlay's
DM image, from a base address rather than by scanning every word position, so
an entry is only reported where the block structure actually puts it. The
decoders are read off PM 0x2E1A and PM 0x2E24 with `SE = -8`:

```text
calling    field = word0 & 0xff   value = ((word2 & 0xff) << 8) | (word1 & 0xff)
answering  field = word0 >> 8     value = ((word2 >> 8)  << 8) | (word1 >> 8)
```

The shared sequencer-A script at `0x1A2E` decodes into a clean ordered state
sequence, which reproduces Session 102's live walk of the caller
(`1a2e -> 1a6d -> 1a79 (0x53) -> 1a91 (0x54) -> ... -> 1ae5 (0x60)`) exactly:

```text
0x0050 0x0052 0x0053 0x0054 0x0056 0x0058 0x005a 0x005c 0x0060 0x0062
0x0064 0x0066 0x0068 0x006a 0x0070 0x0072 0x0074 0x0076 0x0078 0x007a ...
```

### One block, in the whole script set, sets bit 15

Walking all three scripts — shared `0x1A2E`, answer `0x1E81`, call `0x1EA2` —
and filtering on field `0x04`:

```text
block 0x1b12  state 0x0066  gate 0x8200 [bit 15 SET]
    0x1b12  field 0x10 = 0x0066   state
    0x1b15  field 0x04 = 0x8200   gate DM(0x213B)
    0x1b18  field 0x0b = 0x0000
    0x1b1b  field 0x0f = 0x0028   countdown
    0x1b1e  field 0x19 = 0x0001   test4
```

**That is the only one.** No block in the answer script and none in the call
script sets it; Session 114g's `0x1be7` was a chance alignment, as the caveat
there allowed, and is withdrawn.

And the block we actually land on:

```text
block 0x1b36  state 0x0070  gate 0x0200
    0x1b36  field 0x10 = 0x0070   state
    0x1b39  field 0x00 = 0x2700
    ...
    0x1b42  field 0x04 = 0x0200   gate DM(0x213B)
    0x1b4e  field 0x0f = 0x000f   countdown
```

`0x0200` is exactly the value Session 114f watched being written once, from
the record decoder, and never updated.

### The whole chain, end to end

1. The call reaches the V.34 page and the script arrives at state `0x0070`
   **without passing through `0x0066`**.
2. Block `0x1b36` writes `DM(0x213B) = 0x0200` — bit 15 clear.
3. The three actions `0x285c`, `0x2868`, `0x2879` repeat, `0x2879` rewinding
   the action cursor by 3 each pass.
4. PM `0x285e..0x2861` tests bit 15, which only state `0x0066` would have set.
5. The generator action is never dispatched, so the TX word stops updating and
   the wire holds its last sample.
6. The peer hears DC for about seven seconds and gives up.

Every step of that is measured. The one remaining unknown is step 1: **what
should have routed the state machine through `0x0066`.** Neither `0x1b03`
(state `0x0064`) nor `0x1b36` carries branch fields `0x11..0x14` — both carry
`field 0x19 = test4` and a countdown — so the transition is decided by a test
routine, and test routines read state this harness may not be supplying.

That is where the AT-command options and any other pre-call configuration
should be checked, and it is a much narrower target than "why does V.34 fail":
find the test that chooses `0x0066` over `0x0070`, and find what it reads.

`tests/test_v34_script.py` covers the two role decoders against one shared
word, the record base and gate field against the firmware map, block
termination, and the distinction between a cleared gate and an absent one.
Suite is 261.

## Session 114i: correcting the role half, and why the state test never runs

### Correction to Session 114h

Session 114h walked the shared sequencer-A script at `0x1A2E` with the
**low-byte** decoder. That is the *calling* side. On these calls the CX dials
in, so the card is the answering modem and reads the **high** bytes. The
conclusion that state `0x0066` is the gate-opening state is therefore about the
wrong role and is withdrawn.

Walked correctly (high bytes, terminator field `0x19`, 60 blocks), the answer
half reads:

```text
0x0050 0x0052 0x0053 0x0054 0x0056 0x0058 0x005a 0x0060 0x0062 0x0064
0x0070 0x0071 0x0072 0x0074 0x0076 0x0080 0x0082 0x0084 0x0086
0x0090 0x0092 0x0094 0x0096 0x0097 0x0098 0x009a ...
```

There is no state `0x0066` on this side at all. The two blocks that matter:

```text
block 0x1a2e  state 0x0050  gate 0x0200            <- the single live write
block 0x1be4  state 0x0096  gate 0x8200 [bit 15]   <- the only opener
```

`0x0200` written at state `0x0050` matches Session 114f exactly: one write,
early, never updated. **The gate-opening state is `0x0096`.**

### The intended route to it runs through DM(0x3F89) — and the zero is correct

Fields `0x15..0x19` index a test table at `DM(0x064B)` and fields `0x11..0x14`
a branch table at `DM(0x0676)`. Resolving them for state `0x0076`:

| field | index | resolves to |
|---|---|---|
| test0 `0x15` | `0x1e` | `DM(0x0669)` = PM **`0x2ef1`** |
| branch0 `0x11` | `0x13` | `DM(0x0689)` = **`0x1ba5`** = state `0x0090` |
| test1 `0x16` | `0x03` | `DM(0x064e)` = PM `0x2e36` |
| test4 `0x19` | `0x01` | `DM(0x064c)` = PM `0x2e32` |

PM `0x2ef1` is Session 102's routine verbatim — `AR = DM($3F89)`, then
`0x2ed1`'s `AR = AR + 0; RTS`, which fires on zero. And `0x0090` runs
`0x0092 → 0x0094 → 0x0096`.

So **`DM(0x3F89) == 0` is the intended route to the state that opens the
gate**, and Session 114 measured that word as genuinely zero on the wire. The
zero that Sessions 102–104 spent three sessions treating as a receive defect is
the correct signal, and it points at the right branch. That thread is now
closed from both ends.

### Why the transition never happens

State `0x0072` — where most forced-V.34 calls freeze — carries no countdown and
no branch fields at all:

```text
block 0x1b30  state 0x0072
    0x1b30  field 0x10 = 0x0072  state
    0x1b33  field 0x09 = 0x02cc
    0x1b36  field 0x19 = 0x000e  test4
```

Its only exit is test4 index `0x0e` = `DM(0x0659)` = PM `0x2e38`, which is a
decrementer on `DM(0x21DA)`:

```text
2e38: I0 = $21DA
2e3d: NOP (MAC), AY0 = DM(I0,M0)
2e3e: AR = AY0 - 1
2e3f: DM(I0,M0) = AR, AF = AR + 0     <- writes on every evaluation
2e40: AR = AF + 1
2e41: RTS
```

`--watch-dm 0x21da` on a live forced-V.34 call:

```text
0x21DA  writes: 2      reads: 172,107
        21da=0000  pc=0d94      generic init
        21da=171a  pc=3169      set once, to 5914
```

The decrementer writes `DM(0x21DA)` every time it runs. **It ran zero times.**
So state `0x0072`'s exit test is never evaluated at all — the counter is
loaded, read constantly from somewhere else, and never counted down.

That is the shape of the whole failure, stated exactly: the action stream
repeats through PM `0x2879`'s cursor rewind, and while it repeats **the
sequencer never evaluates the current state's exit test**. The gate at PM
`0x285e` is the only escape from the action loop, and it needs a value only
state `0x0096` writes — which needs a state transition, which needs the test
that is not running.

### Next

The question is now the relationship between the action dispatcher at PM
`0x2816` and whatever should evaluate the state's test routines. One of them is
running and the other is not. Watch PM `0x2e38` and PM `0x2e32` — neither
should be silent on a healthy call — and find the caller that should reach
them.

## Session 114j: the last block load resolves its exit test into the INFO data area

Session 114i left an either/or: the state evaluator never runs, or it runs and
the resolved test is wrong. It is the second, and the mechanism is now exact.

### The evaluator and the resolver

PM `0x2d80` is the block loader. It publishes TrnProgress from `DM(0x2147)`,
then resolves the record's index fields through two tables:

```text
2d84: I0 = $2148   ; branch fields 0x11..0x14   2d86: AY0 = $0676   CNTR = 4
2d89: I0 = $214C   ; test fields  0x15..0x19    2d8b: AY0 = $064B   CNTR = 5
```

with PM `0x2e10` doing the arithmetic — `resolved = DM(table_base + field)` —
and storing the five test routines at `DM(0x21F2..0x21F6)`.

PM `0x2dc2` is the evaluator, and it runs test4 first:

```text
2dc2: I4 = DM($21F6)     ; resolved test4
2dc3: CALL (I4)
2dc4: IF LE JUMP $2DD7   ; block advances
2dc5: I4 = DM($21F2)     ; test0, then the branch targets
```

Live, all of that is running: PM `0x27f4` 733 times, `0x2db9` 733, `0x2dc2`
742, `0x2dc3` 1484. **The evaluator is not silent.** Session 114i's inference
that the test never runs was right about `PM 0x2e38` and wrong about the cause.

### What it resolves to

`--watch-dm 0x21f6` catches every block load, since test4 is written once per
load. Twelve loads over the call:

```text
  1  21f6=0000    generic init
  2  21f6=2e6c    DM(0x064B + 0x00)
  3..10  21f6=2e32   DM(0x064B + 0x01)   the countdown test
 11  21f6=2e3c    DM(0x064B + 0x10)
 12  21f6=11e4    <- not in the table at all
```

The call froze at `TrnProgress 0x0071`. The script's block for that state is
`0x1b24`, and its test4 field is `0x19 = 0x0001`, which resolves to
`DM(0x064C) = 0x2e32` — the value loads 3 to 10 got right. The twelfth load
produced `0x11e4` instead.

`0x11e4` is not in the test table. It appears in DM only at `0x0605`, `0x0606`,
`0x0609` and `0x060C`, which is the **INFO message packing area** — Session 103
established `DM(0x0608..0x060E)` as where the packer writes received control
channel words. Reaching it from base `0x064B` needs an index of about `-0x46`,
so the record's test4 field held roughly `0xFFBA` rather than `0x0001`.

So the page is calling received INFO data as if it were a test routine, on
every evaluation, and the block can never advance.

### Where that puts the fault

The resolver is fine — it got eleven loads right with the same code and the
same table. The twelfth **record** was wrong: its test4 field was not the value
the script holds for state `0x0071`. Either the script pointer landed off a
three-word boundary, or the record was decoded from the wrong place.

That is one probe away. `DM(0x14A5)` is sequencer A's script pointer, written
at PM `0x2d81` on every load and at PM `0x2dd6` when a branch is taken.
**Watch `DM(0x14A5)` and `DM(0x214C..0x2150)` across a forced-V.34 call**: the
first names every block address the interpreter walked to, the second the raw
index fields it decoded there. The load that goes bad, and the transition that
led into it, will both be in that trace.

The chain from there to the symptom is already established: bad test4 →
evaluator never advances the block → the three actions repeat through PM
`0x2879`'s rewind → the gate at PM `0x285e` never sees the `0x8200` that only
state `0x0096` writes → the generator is never dispatched → the TX word holds
its last sample → the peer gives up.

## Session 114k: the V.34 test table is being overwritten by the bulk worker at PM 0x1930

Session 114j found the twelfth block load resolving its exit test to `0x11e4`.
Session 114i's script-pointer trace rules out every explanation but one.

### The record is right; the table under it moves

`--watch-dm` on the script pointer `DM(0x14A5)` gives a walk with no anomaly at
all — every value is a real block address, in order, matching the answer-half
walk exactly:

```text
0x1a2e(0x0050) 0x1a6d(0x0052) 0x1a79(0x0053) 0x1a88(0x0054) 0x1aa6(0x0056)
0x1ac1(0x0058) 0x1adc(0x0060) 0x1aee(0x0062) 0x1afa(0x0064) 0x1b0f(0x0070)
0x1b24(0x0071) 0x1b30(0x0072)
```

The one non-linear step, `0x1ac1 → 0x1adc`, is written from PM `0x2dd7` — the
branch-taken path — so it is a scripted branch, not a slip.

And the raw index field is correct. `DM(0x2150)` is record field `0x19`, test4,
and on the final load it reads **`0x0001`** — exactly what block `0x1b24`
(state `0x0071`) holds, matching the state the call froze in.

So the pointer is right, the decode is right, and the field is right. The same
index `0x0001` resolved to `0x2e32` eight times earlier in the same call and to
`0x11e4` at the end. **`DM(0x064C)` changed underneath it.**

### It is written 441 times a call, by two workers

`--watch-dm 0x064b,0x064c,0x065b` — three entries of the test table that should
be constant for the life of the page:

```text
DM(0x064B)  441 writes    ... 0x0115 0x0117 pc=34d6 ... 0x11e4 pc=1931
DM(0x064C)  441 writes    ... 0xfdaa 0xfd87 pc=34d6 ... 0xee1c pc=1935
DM(0x065B)  441 writes    ... 0x0117 0x017e pc=34d6 ... 0x11e4 pc=1931
```

The reported PC is the instruction after the store, as elsewhere in these
traces. That makes the writers PM `0x1930` and PM `0x1934`, which are the two
`DM(I0,M1) = SR0` stores in one routine whose pointer comes from `AX0`:

```text
192e: I0 = AX0
192f: AF = SR1 + 0, SR0 = DM(I5,M5)
1930: DM(I0,M1) = SR0, AR = AX1 + AF
1931: M7 = -35
1932: AF = AF - 1, SR0 = DM(I5,M7)
1933: IF EQ JUMP $1935
1934: DM(I0,M1) = SR0
1935: DM(I1,M0) = AR, AR = AR - AY0
```

**PM `0x1930` is already in this file.** Session 100: "Width 31 corrupted DM at
PM `0x1930`". Sessions 106–108: "PM `0x1930` sweeps linearly into unrelated
V.34 or V90D state". It is the V90D bulk worker, and the handoff has carried
"the native V90D bulk worker corrupts DM" as an open blocker throughout, marked
*contained* because no width is released by default and a bounded host-side
implementation supplies the delay ABI instead.

The containment is not enough. The firmware's own worker still runs, and what
it sweeps over includes `DM(0x064B..0x065B)` — the V.34 page's test-routine
table. The result is that the state machine's resolved exit test points at
residue, the block never advances, the action stream repeats, the gate never
opens, and the generator is never dispatched.

### The full chain, all of it measured

1. The V90D bulk worker at PM `0x1930`/`0x1934` writes over the V.34 test table
   at `DM(0x064B..)`.
2. Block load 12 resolves test4 from index `0x0001` and gets `0x11e4` — INFO
   and echo residue — instead of `0x2e32`.
3. PM `0x2dc2` calls it every evaluation; the block never advances.
4. The three actions `0x285c`/`0x2868`/`0x2879` repeat through the cursor
   rewind at PM `0x286b`.
5. The gate at PM `0x285e` never sees the `0x8200` that only state `0x0096`
   writes.
6. The generator action is never dispatched; the TX word holds its last sample.
7. The peer hears DC for about seven seconds and hangs up.

### What to do

This is no longer a V.34 investigation. It is the DM-corruption blocker, and
the fix is to stop PM `0x1930` writing outside its own workspace — the same
question Sessions 91–93 were asking about `I1` at PM `0x1917`/`0x1921` and
where `AX0` comes from, which is what sets `I0` at PM `0x192e`.

`--watch-exec 0x192e` with `ax0` logged, on a forced-V.34 call, gives the
pointer the worker is about to write through, and its provenance is the whole
of the remaining question. Note that the second writer at PM `0x34d6` accounts
for 439 of the 441 writes and has not been identified; the two may be the same
mechanism at different widths, or two separate ones.

## Session 114l: the corruption is fixed; V.34 now stops earlier, at state 0x0064

### What the pointer was doing

`--watch-exec 0x192e` with `ax0` logged, on a forced-V.34 call, measures the
worker's write pointer directly:

```text
PM 0x192e executions: 712
ax0 range: 0x0061 .. 0x0769    distinct: 712
values landing inside the test table 0x064B..0x066A: 16
```

712 executions, 712 distinct values, stepping by two, never repeating. It is a
ring pointer that never wraps — it marches across roughly 1,800 words of DM and
crosses the V.34 script's test-routine table on the way. PM `0x191a`
(`AR = 0, AX0 = AR`) is taken 243 times of the 712.

That is `_service_bulk_lengths()`'s own docstring coming true: "the native V.34
worker's modulo bound at PM 0x1930 is zero for the same reason ... which is the
unbounded fill Sessions 90–93 and 101 chased."

### The fix

The seeder already holds a floor pair, but it *stands down* when the firmware
publishes a coherent one of its own — and on `0x0261` the firmware's pair
(near=203/256) is what unbounds the pointer. Holding the floor instead:

| | firmware pair | floor pair held |
|---|---|---|
| PM `0x192e` executions | 712 | **241** |
| `ax0` range | `0x0061..0x0769` | **`0x0061..0x0241`** |
| writes into the test table | 16 | **0** |

`_service_bulk_lengths()` now never yields on `0x0261`. The change is scoped to
that page: V90D `0x026A` keeps the existing yield exactly, because it reaches
`0x00d0` today and must not be disturbed. Two tests cover both halves of that.

Verified on hardware with no environment override: zero stand-downs, 241
executions, `0x0061..0x0241`, zero writes into the table.

### V.34 still does not complete

**The page still freezes — now at `TrnProgress 0x0064` instead of `0x0071`.**
One defect is fixed and the state machine gets *less* far, which is a real
result and not a regression to argue away: the corruption was letting the
machine run on through states whose exit tests had already been resolved from a
clean table, and removing it exposes whatever actually gates `0x0064`.

Its block, for whoever picks this up:

```text
block 0x1afa  state 0x0064
    field 0x10 = 0x0064   state
    field 0x00 = 0x9601
    field 0x03 = 0x0000
    field 0x0d = 0x4000
    field 0x0f = 0x0120   countdown = 288
    field 0x07 = 0x0000
    field 0x19 = 0x0001   test4 -> DM(0x064C) = PM 0x2e32, the countdown test
```

test4 is the plain countdown at PM `0x2e32`, which decrements `DM(0x2146)` —
the block's own field `0x0f`. With a clean table that should now resolve
correctly, so the question is whether the countdown runs and what the block's
other fields (`0x00 = 0x9601`, `0x0d = 0x4000`) arm. `--watch-dm 0x2146` on a
forced-V.34 call is the direct next probe: if it never decrements, the
evaluator is not reaching test4 for this block either.

### Status

| | before | after |
|---|---|---|
| test table corruption | 16 writes/call | **none** |
| ring pointer | unbounded, `0x0769` | bounded, `0x0241` |
| V.34 completes | no, freezes `0x0071` | **no, freezes `0x0064`** |

The corruption blocker is closed. V.34 is not yet working.

## Session 114m: what is actually missing — the V.34 page is barely being clocked

Every session from 76 onwards has treated the V.34 failure as a state-machine
question: which block, which test, which gate. The arithmetic says otherwise.

PM `0x27dd` is the per-symbol V.34 CoreRoutine. Its structure confirms it —
it reloads every `L`/`M` register, clears GEN_CONTROL at `0x27ea`
(`DM($3FB5) = M0`), sets the `0x0400` worker-enable in `DM(0x3FC1)`, and then
calls the state evaluator:

```text
27ea: DM($3FB5) = M0        GEN_CONTROL cleared, every invocation
27eb: AX0 = DM($2165)
27ed: IF NE JUMP $290C      the stop branch -- DM(0x2165) measured as 0 all call
27f0: AR = AR OR $0400      worker enable
27f4: CALL $2DB9            the state evaluator
```

`0x27f4` is unconditional once `DM(0x2165)` is zero, and Session 114j watched
that word: two writes, both `0x0000`. So its execution count *is* the page's
symbol count. Measured over one live call:

```text
0x0261 resident for 46.7 s
PM 0x27f4 executions: 733   ->  15.7 per second
```

Against what V.34 requires:

| symbol rate | invocations needed over 46.7 s | shortfall |
|---|---|---|
| 2400 | 112,032 | **153×** |
| 2800 | 130,704 | **178×** |
| 3200 | 149,376 | **204×** |
| 3429 | 160,066 | **218×** |

The 8 kHz sample clock delivered 373,440 samples in the same span.

**The V.34 page is being driven about 16 times a second instead of two to three
thousand.** That is not a state machine that is stuck; it is a state machine
running at roughly half a percent of real time.

### It explains the whole symptom set, better than any of the state readings do

- **The frozen wire.** A generator invoked 16 times a second cannot produce an
  8 kHz sample stream, and with the generator action never dispatched at all the
  TX word simply keeps its last value. Sessions 76, 78 and 114b–c all measured
  that constant and looked for a publication fault; there is none to find.
- **The countdowns.** Block `0x1afa`'s countdown is 288. At symbol rate that is
  under a tenth of a second; at 15.7/s it is 18 seconds, and every re-entry
  reloads it. No block with a countdown can time out inside a call.
- **Why the peer gives up.** It waits about seven seconds. In that window the
  page executes roughly 110 symbol slots out of the ~20,000 it should.
- **Why the state number kept moving** between `0x0060`, `0x0071`, `0x0072`,
  `0x0076` and now `0x0064` across configurations: it is wherever a machine
  running 200× slow happened to reach before the call ended.

Session 100 recorded "neither loopback endpoint holds real time once page 8 is
resident" at **0.65×**. This is live, not loopback, and it is **0.005×**.

### What this reframes

The DM corruption fixed in Session 114l was real and worth fixing — 16 writes a
call into the test table is a genuine defect, and the pointer is now bounded.
But it was never going to make V.34 work, and the freeze moving from `0x0071`
to `0x0064` is consistent with that: both are just where a 200×-slow machine
stopped.

**The question is no longer which script state gates V.34. It is why page
`0x0261`'s CoreRoutine is invoked 16 times a second when page `0x026A`'s
equivalent keeps up well enough to reach `0x00d0`.** That is a scheduler and
dispatch question in this harness — Session 75's `Init8kRoutine 0x19d2` /
`Core8kRoutine 0x19d5` / symbol routine `0x27dd` chain, and how the per-sample
pump reaches it for this page.

Direct next measurement: count PM `0x27dd` and the V90D equivalent per second
in the same run, and compare both against the sample clock. If V90D is at
symbol rate and V.34 is not, the difference is in how the two pages are
dispatched, and that is where the fix is.

## Session 114n: the per-sample callback itself is not being driven

Session 114m measured the V.34 symbol routine at 15.7/s against a required
2400–3429. The obvious next question is where between the 8 kHz sample clock
and the symbol routine the rate is lost. It is lost above both.

`DM(0x3FB3)` is the ADDSP write-database `Core8kRoutine` callback that the
kernel invokes at PM `0x0771` (`CALL (DM(3fb3))`) once per 8 kHz sample. Read
straight out of the captures, it is page-specific and correct on both pages:

```text
V.34 (0x0261)   DM(0x3FB2)=19d2  DM(0x3FB3)=19d5   DM(0x3FB4)=3764 constant
V.90 (0x026A)   DM(0x3FB2)=19d2  DM(0x3FB3)=19e1   DM(0x3FB4) carries samples
```

`DM(0x3FB4)` is TXSAMPLE. On V.90 it moves every frame; on V.34 it holds
`0x3764` for the life of the call, which is the frozen wire seen since
Session 76 restated one level lower.

Watching the callback itself on a live forced-V.34 call:

```text
0x0261 resident 46.72 s   ->  expected 8 kHz callbacks: 373,760
PM 0x19d5 (Core8kRoutine)  executions:   568      12 per second, 658x short
PM 0x27dd (symbol routine) executions:   242
```

**The break is not between the callback and the symbol routine.** The callback
is not being driven either. Whatever reaches PM `0x0771` for page `0x026A` —
which trains to `0x00d0` — reaches `0x0261` twelve times a second.

That places the fault above the V.34 page entirely, in the per-sample dispatch,
and it retires the last of the state-machine framing: every script reading in
Sessions 102–114, including the gate at PM `0x285e` and the block walk, is
describing a machine that is barely being clocked.

It also re-reads two older notes as the same fault seen earlier. Line 5133 of
this file records a path that "reaches `DM3fb3` and kills `Core8kRoutine`", and
Session 100's 0.65× loopback timing is the mild form of what is 0.0015× here.

### Next

Watch PM `0x0771` on a V.34 call and on a V.90 call in the same run. Three
outcomes, each pointing somewhere different:

- `0x0771` runs at 8 kHz on both — the dispatch is fine and something inside
  the call chain returns early for `0x0261`; look at `DM(0x3610)`, which
  `0x19d5` calls first.
- `0x0771` runs at 8 kHz on V.90 and 12/s on V.34 — the kernel's per-sample
  loop is being starved on this page, and the question is what gates it.
- `0x0771` is slow on both — the harness pump is the problem and V.90 tolerates
  it only because V90D is far less timing-critical than V.34 phase 3/4.

The third would also explain why V.90 works at all while `DM(0x0fcf)` sits at a
7,200-equivalent quality ceiling (Session 113) — a receiver being clocked at a
fraction of real time is exactly what a degraded quality metric looks like.
That is speculation and is flagged as such, but it is cheap to test in the same
run.

## Session 114o: the kernel's per-sample loop runs 8x slow on the V.34 page

Two calls, same run, same rig, watching PM `0x0771` — the kernel's
`CALL (DM(3fb3))`, which should execute once per 8 kHz sample.

| | page | residency | PM `0x0771` | rate | final `TrnProgress` |
|---|---|---|---|---|---|
| default (V.90) | `0x026A` | 39.28 s | 360,801 | **9,185/s** | `0x00c4` |
| forced V.34 | `0x0261` | 47.04 s | 44,974 | **956/s** | `0x0064` |

V.90 is at the sample clock. **V.34 is eight times short of it**, on the same
harness, the same peer and the same rig, minutes apart.

That is the third of Session 114n's three outcomes ruled out and the second
confirmed: the kernel per-sample loop is not being driven at rate while page
`0x0261` is resident. It is not an early return inside the page's call chain,
and it is not a pump that is uniformly slow — V.90 gets its 8 kHz.

### There are two losses, not one

Session 114n measured PM `0x19d5`, the V.34 page's own `Core8kRoutine`
callback, at 568 executions over 46.7 s. This session measures the kernel site
that calls it at 44,974 over 47.0 s. Those are different calls, so the ratio is
indicative rather than exact, but they cannot both be describing one problem:

- PM `0x0771` reaches **956/s** where it should reach 8,000 — an 8× loss in the
  kernel loop itself.
- Of those, only a small fraction reach `DM(0x3FB3)`'s target — a further loss
  inside the dispatch, since `CALL (DM(3fb3))` executing 44,974 times cannot
  produce 568 executions of `0x19d5` unless the callback word is not `0x19d5`
  for most of them.

The second is worth pinning before chasing the first: `DM(0x3FB3)` was read as
`0x19d5` in the capture summaries, but those sample it every 160 samples. If it
is being rewritten between samples — by the same class of sweep that Session
114k found writing over the test table, or by the page handoff — the callback
would be dispatching elsewhere most of the time.

### Next, and it is now a harness question

1. **`--watch-dm 0x3fb3` on a forced-V.34 call.** If it takes more than one
   value, the callback is being rewritten and that is the inner loss. Cheap and
   decisive, and it is the same shape of defect as Session 114k.
2. **Then the 8× on PM `0x0771` itself.** V.90 gets 8 kHz through the same pump,
   so the difference is in what the harness does while `0x0261` is resident —
   `--tick-budget-ms`, the catch-up deferrals and clock holds in the `[media]`
   lines, and how many emulated instructions a V.34 sample costs relative to
   V90D. The endpoint log already reports "ticks over 18 ms" and "catch-up
   deferrals" per call; those counters on a V.34 call against a V.90 call are
   the first thing to compare, and they cost nothing to read.

Neither is a firmware question. Every script reading from Session 102 onward
stands, and none of it was ever going to make V.34 train while the page runs at
an eighth of the sample clock and its callback at a fraction of that.

## Session 114p: the callback is never rewritten; the CPU stops dispatching entirely

Session 114o's next step was `--watch-dm 0x3fb3` on a forced-V.34 call, to test
whether the `Core8kRoutine` callback word is rewritten between the 160-sample
capture points and so dispatches somewhere other than `0x19d5` most of the
time. It is not. The answer is more definite than either branch allowed for,
and it retires the "two losses" framing.

One forced-V.34 call, CX93001-EIS V0.2013-V92 dialling in, page `0x0261`
loaded at sample 43,647 (5.456 s), 420,480 samples of media,
`TrnProgress` last moving `0x004f -> 0x0064` at 5.260 s, `NO CARRIER` at the
CX. Evidence in `artifacts/interop/v34-live/cb3fb3-v34.*`.

```bash
EICON_MODULATION=v34,0,,33600,,33600 python3 -u tools/eicon_adsp_sip.py \
    --native-mips --force-info-after-v8 --native-bearer-activation --tx-prbs \
    --law pcmu --sip-port 5060 --rtp-port 4000 --watch-dm 0x3fb3 \
    --capture-prefix artifacts/interop/v34-live/cb3fb3-v34 ...
python3 -u tools/cx_at.py --dev /dev/cu.usbmodem123456781 \
    --setup 'AT&F' --setup 'AT+MS=V34,0,2400,33600' dial 6001 --wait 60
```

`--watch-dm` watches reads as well as writes, so the same run answers both
halves at once.

### The callback is written twice in the whole call

```text
dm w 3fb3=204a  ppc=1ffd  cyc=33,056,429
dm w 3fb3=19d5  ppc=19d0  cyc=108,377,667
```

That is all of them. Read at the kernel dispatch site, the word is monotone in
time — three values, three eras, no flapping:

| value at `pc=0772` | reads | cycle span |
|---|---|---|
| `15dd` | 1 | 33,054,862 |
| `204a` | 24,555 | 33,059,458 – 82,719,846 |
| `1706` | 17,440 | 82,736,152 – 108,375,870 |
| **`19d5`** (V.34) | **568** | 108,392,096 – 109,258,972 |

The `204a -> 1706` boundary has no CPU write behind it, so the write database
is also loaded by the page transfer itself, not only by firmware stores. That
is a detail, not the finding.

**Hypothesis 1 from Session 114o is dead.** Once the V.34 page installs
`0x19d5` at `ppc=0x19d0`, every subsequent kernel dispatch reads `0x19d5` —
568 of them, and 568 is exactly the count of `0x19d5` executions Session 114n
measured on a different call. There is no inner loss. The 42,564 non-`19d5`
reads are all *earlier in the call*, dispatching the V.8 and INFO pages'
callbacks correctly.

### And it is not an 8x slow loop either — the dispatch stops dead

The two populations are strictly disjoint in time:

```text
last  read at pc=0772   cyc  109,258,972
first read at pc=2e1c   cyc  109,279,981
last  read at any pc    cyc  7,687,546,401   (end of call)
```

Zero dispatches after `cyc 109,258,972`. From there the CPU is inside a loop
containing PM `0x2e1b/0x2e1c/0x2e1d`, which reads `DM(0x3FB3)` 57,671 times
from each of the three sites, at a uniform 43,791 cycles per pass, for the
remaining **98.6% of the call's emulated cycles**.

So the kernel per-sample loop runs at rate for about 568 samples after the
V.34 callback is installed — roughly 71 ms of sample time — and then never
runs again for the remaining ~47 seconds. Session 114o's "956/s, 8x short" was
total hits divided by total page residency, which averages a loop that ran
normally and then stopped into one that looks uniformly slow. **The V.34 fault
is a hang, not a starvation.**

### What the loop is

Disassembled from the `0x0261` overlay image, the entry above it sets the base
and dispatches through `DM(0x14A6)`:

```text
2e17: MR0 = $2137
2e18: I6 = DM($14A6)
2e19: JUMP (I6)
2e1a: AY0 = $00FF
2e1b: AX0 = DM(I4,M5)
2e1c: AF = AX0 AND AY0,  AR = DM(I4,M5)
2e1d: AR = AR AND AY0,   SR0 = DM(I4,M5)
2e1e: AR = MR0 + AF,     SR1 = AR
2e1f: I0 = AR
2e20: SR = LSHIFT SR0 (HI, OR) BY 8
2e21: DM(I0,M1) = SR1,   AR = MR1 XOR AF
2e22: IF NE JUMP $2E1B
```

A sentinel-terminated walk: it marches `I4` through DM three words at a time,
masks each to a byte, and writes into `0x2137 + byte` — the script record area
that PM `0x2e10`'s resolver and Sessions 114j–114k are all working in. It exits
only when `MR1 XOR AF` goes zero. Reaching `DM(0x3FB3)` at all means `I4` is
well past any table that starts at `0x2137`, and the loop is re-entered tens of
thousands of times without the call ever getting a sample dispatched.

That reading of the loop's purpose is inference from the listing and is flagged
as such; the timing and the counts above are measured.

### Next

1. **Where does `I4` come from, and what is `DM(0x14A6)`?** The dispatch at
   `0x2e19` selects this routine; the same site presumably selects the healthy
   one on `0x026A`. `--watch-dm 0x14a6` plus `--watch-exec 0x2e17,0x2e1a` gives
   the caller and the entry pointer.
2. **The V.90 control.** Run the same `--watch-dm 0x3fb3` on a default call.
   If `0x026A` reaches `0x00c4` without ever entering `0x2e1a`, the loop is the
   whole V.34 difference and everything measured since Session 114m is one bug.
3. Sessions 114m–114o's rate figures should be re-read as "cycles spent
   elsewhere", not as a slow pump. The `[media]` counters on this call are
   unremarkable — 7 clock holds, 32 ticks over 18 ms, 8 catch-up deferrals —
   which is consistent with a firmware hang rather than a harness stall, and
   makes the tick-budget comparison in 114o's step 2 much less interesting.

Caveat: watching `0x3fb3` logs on every read, which adds I/O to the run. That
cannot manufacture a hard stop at a fixed cycle followed by 7.5 billion cycles
in one loop, so the finding stands, but the absolute cycle rates from this run
should not be compared against unwatched runs.

## Session 114q: the loop is normal machinery everywhere except V.34, where it never returns

Two calls, the follow-ups Session 114p asked for. Evidence in
`artifacts/interop/v34-live/loop-v34.*` and `loop-v90.*`.

### First, an off-by-one in how the WATCH lines were read

`RWORD_DATA` logs `a->pc`, which is already the *next* instruction; `WWORD_DATA`
logs `ppc` and `pc` together and the pair shows the skew directly
(`ppc=1ffd pc=1ffe`). So every read attributed to `pc=NNNN` in Sessions 114n–p
was executed at `NNNN-1`. The kernel dispatch site logged as `pc=0772` is
PM `0x0771`, as Session 114o had it, and the loop reads logged at
`2e1c/2e1d/2e1e` are the three `AR = DM(I4,M5)` sites at `2e1b/2e1c/2e1d`.
That confirms 114p's listing rather than contradicting it.

### `DM(0x14A6)` is a vector slot, and it is not what varies

Forced-V.34 call, `--watch-dm 0x14a6 --watch-exec 0x2e17,0x2e1a`. The slot is
written **twice in the whole call**, both times from PM `0x2d7a`:

```text
dm w 14a6=2e1a  ppc=2d7a  cyc=117,555,810
dm w 14a6=2e24  ppc=2d7a  cyc=117,566,738
```

and read 171,255 times — but only **11** of those reads are the dispatch at
PM `0x2e19` (`I6 = DM($14A6)` / `JUMP (I6)`). The other 171,244 are the loop
itself sweeping `I4` across the address and reading `0x14A6` as data, which is
also how it reaches `DM(0x3FB3)`: those two addresses are 10,765 words apart,
so the walk is crossing most of data memory on every pass.

PM `0x2e1a` executes **twice**; PM `0x2e17` eleven times. Each of the two
entries therefore spins for millions of iterations. The call froze at
`TrnProgress 0x0064` for the third consecutive time.

### The control: the loop is bounded on V.90 and unbounded on V.34

The V.90 call fell back through INFO into V.34 partway, which makes it a better
control than intended — both pages in one call, same rig, same peer, same run.
Kernel dispatches by callback era:

| era | callback | dispatches | cycle span |
|---|---|---|---|
| V.90 DPCM | `19e1` | 96,825 | 112,598,153 – 299,132,758 |
| INFO | `1706` | 19,381 | 299,147,897 – 327,534,463 |
| V.90 DPCM | `19e1` | 88,029 | 327,545,096 – 501,597,358 |
| INFO | `1706` | 19,350 | 501,612,497 – 529,963,200 |
| **V.34** | **`19d5`** | **1,900** | 529,979,318 – **533,353,130** |

After that last V.34 dispatch there are 353 million cycles — 40% of the call —
with zero per-sample dispatches, exactly the 114p signature.

PM `0x2e1b` splits across that boundary as cleanly as it is possible to split:

```text
2e1b executions before the last dispatch (cyc < 533,353,130):     4,576
2e1b executions after:                                        7,786,658
```

**The loop is not a V.34 routine and it is not inherently faulty.** It runs
4,576 times across two full V.90 DPCM eras and two INFO eras, bounded, with the
sample clock never interrupted. On V.34 it is entered and does not terminate:
1,700 times more iterations than the rest of the call put together, and the
dispatch never resumes.

So Session 114p's conclusion holds and is now localised further. The V.34 fault
is that this walk's exit condition — `AR = MR1 XOR AF` at PM `0x2e21`,
`IF NE JUMP $2E1B` — is never satisfied when the V.34 page sets it up, so `I4`
runs off its table and sweeps data memory forever.

### Next

The question is now what `I4`, `MR1` and the `0x2137` base are on entry, and how
the V.34 setup differs from the V.90 one. `--watch-exec 0x2e1a,0x2e24` logs
`i4`, `mr1` and `ax0` at both entries; there are only eleven of them in a call,
so it is cheap. Compare the entry that returns against the one that does not.

Two housekeeping notes. The V.90 control reached only `TrnProgress 0x0062` and
fell back on its own — the DIL lottery, not a new fault. And one earlier V.34
attempt this session timed out with no INVITE ever reaching the endpoint; it is
not in the artifacts and is a telephony miss, not a result.

## Session 114r: the loop is the block loader's field unpacker, and it hangs on block 0x1afa

`--watch-exec 0x2e1a,0x2e24` on a forced-V.34 call. Eleven entries in the
call, as Session 114q predicted, all reached from PM `0x2e19`. Fourth
consecutive freeze at `TrnProgress 0x0064`. Evidence in
`artifacts/interop/v34-live/entry-v34.*`.

```text
pc     ret    cyc          i4     mr0    mr1    l0    b0
2e1a   2d81   113305646    202e   2137   0019   0000  0000
2e1a   2d93   113305828    1ea2   2137   0024   0000  0000
2e24   2d81   113316574    1a2e   2137   0019   0000  0000
2e24   2d93   113316992    1e81   2137   0024   0000  0000
2e24   2ddb   113319447    1a6d   2137   0019   0000  0000
2e24   2ddb   113330798    1a79   2137   0019   0000  0000
2e24   2ddb   113335756    1a88   2137   0019   0000  0000
2e24   2ddb   113344470    1aa6   2137   0019   0000  0000
2e24   2ddb   113344936    1adc   2137   0019   0000  0000
2e24   2ddb   113807189    1aee   2137   0019   0000  0000
2e24   2ddb   113863280    1afa   2137   0019   0000  0000
```

`MR0` is `0x2137` on every entry and `L0`/`B0` are zero, so the destination is
a plain linear write into the script record area, not a circular buffer. `I4`
is the source, and `AY0` on each entry carries the previous entry's `I4`.

**`I4` is the block address, and the sequence is the block-load sequence.**
`0x1a2e`, `0x1a6d`, `0x1a79`, `0x1a88`, `0x1aa6`, `0x1adc`, `0x1aee`,
`0x1afa` — the same walk Session 114j counted as twelve block loads, with
`ret=0x2ddb` putting nine of the eleven inside PM `0x2dda`, the block loader.
So PM `0x2e24` is the loader's **field unpacker**: it scans the block record a
byte at a time and scatters the fields into `0x2137 + field`.

The last entry is block **`0x1afa`** — the block whose countdown the handoff
has carried as the next probe since Sessions 114b–l. It is entered at
cyc 113,863,280 and never returns. Everything after that is the 7.8 million
iterations Session 114q measured.

That closes the chain from Session 114m's "200x slow" to a single instruction
pair: the exit test at PM `0x2e21`/`0x2e22`, `AR = MR1 XOR AF` /
`IF NE JUMP $2E1B`, with `MR1 = 0x0019`, never firing for block `0x1afa`.

### But the static record does not explain it

The scan tests every third word's low byte, since the loop consumes three
words per iteration and only the first sets `AF`. Read out of the `0x0261`
overlay's `dm.bin`, offsets 0/3/6 of each block are:

```text
1a6d: 10 01 0f      1a79: 10 02 03      1a88: 12 16 19
1aa6: 0b 11 15      1adc: 10 0d 19      1aee: 11 15 19
1afa: 0e 15 19
```

Block `0x1afa` carries the `0x19` terminator at offset 6, in exactly the place
`0x1aee` and `0x1adc` carry theirs — and those two return normally. **The
static image gives no reason for this block to behave differently.** So either
the record is not what the image says by the time it is scanned, or the scan is
not striding the way the static reading assumes.

The second is not currently observable: the loop indexes `DM(I4,M5)` and the
`[EXEC]` line prints `m1` and `m3` but not `m5`. If `M5` is not 1 the scan
skips the terminator, and that would explain a hang with an intact record.

### Next, in order of cost

1. **Add `m5` (and `i4`'s stride) to the `[EXEC]` format** in
   `tools/adsp2181emu/adsp2181_core.c` and re-run this same probe. One field,
   one rebuild, and it decides between the two explanations above.
2. If `M5` is 1, **watch the record**: `--watch-dm 0x1afa,0x1afd,0x1b00` shows
   what the walk actually reads and whether anything wrote there first. The
   region is a plausible corruption target given Session 114k.
3. Only then is the countdown at `DM(0x2146)` worth returning to. It has been
   the "next probe" since 114b and it is downstream of a block whose fields are
   never unpacked.

## Session 114s: the stride is 1, so the record is the problem

`[EXEC]` now prints the DAG2 side of `DM(I4,M5)` — `m5`, `l4` and `b4` — added
to `tools/adsp2181emu/adsp2181_core.c`. Same probe re-run on a forced-V.34
call, fifth consecutive freeze at `TrnProgress 0x0064`. Evidence in
`artifacts/interop/v34-live/stride-v34.*`.

```text
pc     ret    cyc          i4     m5     l4     b4     mr1
2e1a   2d81   113066689    202e   0001   0000   202e   0019
2e1a   2d93   113066871    1ea2   0001   0000   1ea2   0024
2e24   2d81   113077617    1a2e   0001   0000   1a2e   0019
2e24   2d93   113078035    1e81   0001   0000   1e81   0024
2e24   2ddb   113080490    1a6d   0001   0000   1a6d   0019
2e24   2ddb   113091841    1a79   0001   0000   1a79   0019
2e24   2ddb   113096799    1a88   0001   0000   1a88   0019
2e24   2ddb   113105513    1aa6   0001   0000   1aa6   0019
2e24   2ddb   113105979    1adc   0001   0000   1adc   0019
2e24   2ddb   113568347    1aee   0001   0000   1aee   0019
2e24   2ddb   113624465    1afa   0001   0000   1afa   0019
```

`M5 = 1` and `L4 = 0` on every entry, block `0x1afa` included. The stride is
one word and the source pointer is linear — `L4 = 0` means no circular wrap, so
`I4` simply runs on through data memory once it passes the end of the record.
`B4` tracks `I4`, which is the loader setting up the pair together.

**The second explanation from Session 114r is dead.** The scan does stride the
way the static reading assumed: three words per iteration, testing every third
word's low byte against `MR1 = 0x0019`. Blocks `0x1adc` and `0x1aee` enter with
byte-for-byte identical `m5`/`l4`/`mr1` and return; `0x1afa` does not.

So by elimination the record at `0x1afa` is **not what the overlay image says
by the time it is scanned**. The image has `0e 15 19` at offsets 0/3/6, with
the terminator in the same place as the two blocks either side of it in the
load order.

That is the same class of defect as Session 114k — something writing into a
table it does not own — but at a different address, and this time the effect is
a hang rather than 16 stray words.

### Next

`--watch-dm 0x1afa,0x1afd,0x1b00` on a forced-V.34 call. Those are exactly the
three offsets the scan tests, and the watch answers both halves at once: the
read log gives the live values the walk sees, and any `dm w` line gives the
writer's PC. If offset 6 no longer holds `0x19`, the writer is on that line.

Note also that `0x1afa` is loaded ~56,000 cycles after `0x1aee` and both are
~460,000 cycles after the block before them, so the last two loads are already
separated from the main run of the sequence. Whether that gap is cause or
consequence is not yet established.

## Session 114t: correction — 114r/114s conflated two routines, and the loop terminates

Two things were checked offline first, and both are cheap negatives worth
recording:

- **Not a DM banking fault.** `adsp2181_dm()` returns `a->data`, the base bank,
  and `HOST_WRITE_DM_BLOCK` in `tools/eicon_mips_shim.py` writes through it
  without honouring `dmovlay`. The page load and the `dmovlay=0` unpacker read
  therefore use the same memory. A bank mismatch would have been an elegant
  explanation for a record that reads wrong; it is not available.
- **The static record reading in 114r/114s was methodologically sound.** All the
  block addresses fall inside one downloaded DM block (`address 6702`, 1719
  words) in the `0x0261` metadata, so those records really are downloaded data
  and indexing `dm.bin` by word address is right. `0x2137`, the unpacker's
  destination, is not covered by any block — it is runtime scratch, as expected.

### The error

PM `0x2e24`'s loop is `0x2e25..0x2e2e`, and its backedge is `IF NE JUMP $2E25`.
It is a self-contained routine. **The 7.8 million executions of PM `0x2e1b`
counted in Session 114q are therefore not the block unpacker's loop**; the
eleven `0x2e1a`/`0x2e24` entries and the `0x2e1b` spin are different code paths.
Sessions 114r and 114s treated them as one, which is how "block `0x1afa` is
entered and never returns" was arrived at.

Compounding it, the two measurements came from different calls watching
different addresses: `0x2e1b` was counted in the V.90 control run and
`0x2e1a`/`0x2e24` in the forced-V.34 runs. Session 114o flagged exactly this
kind of cross-run inference as indicative rather than exact, and it was done
anyway.

### What the same log actually shows

`[EXEC]` prints `from=`, so the V.90 control log answers it without a new call:

```text
pc=2e1b   from=2e22   6,232,813     the backedge
          from=2e1a   1,558,026     fresh entries
          from=00c9         389
          from=00d0           6
```

**The loop terminates.** 6,232,813 iterations across 1,558,026 entries is about
four iterations per call — the short scan a small record should produce. What is
pathological is not that it never exits; it is that something calls it one and a
half million times.

That inverts 114r/114s. The exit test at `0x2e21` is firing. Block `0x1afa`'s
record is not shown to be corrupt, and the handoff should not carry that claim.
The two entries from `0x00c9` and `0x00d0` are a second caller and are
unexplained.

### Next

Per-execution logging is the wrong instrument for this and produced the error:
watching one address at a time forced the cross-run comparison, and watching the
hot one is unaffordable (7.8 M lines). The core already has the right instrument
stubbed out — `TRACK_HOTSPOTS` and `pcbucket[0x4000]` at
`tools/adsp2181emu/adsp2181_core.c:11`, currently `#define TRACK_HOTSPOTS 0`
with no accessor.

Enable it, export the histogram, and dump it per call. That gives exact
execution counts for all 16,384 PCs at no log cost, for a V.34 call and a V.90
call, and answers "what is actually running while the samples stop" directly
instead of by inference across runs. Every rate claim from Session 114m onward
should be re-derived from that.

## Session 114u: the PC histogram, and what the V.34 page actually does

The instrument was already in the core and only needed a dump.
`adsp2181_core.c` increments `coverage[0x4000]` on every instruction fetch,
per CPU, and exports `adsp2181_coverage_clear()` / `adsp2181_coverage_count()`;
`tools/adsp_opcode_audit.py` has used it for opcode audits all along. Nothing
needed enabling. `eicon_adsp_sip.py` gains `--pc-histogram PATH` and
`--pc-histogram-from OVERLAY`, which zeroes the counters the moment that
overlay becomes resident so the dump covers one page's residency. The
redundant `TRACK_HOTSPOTS` / `pcbucket` stub, which was global rather than
per-CPU and had no accessor, is removed. 263 tests pass.

One forced-V.34 call, counters cleared at 5.940 s when `0x0261` went resident,
`artifacts/interop/v34-live/hist-v34.tsv`:

```text
[pc-histogram] 59 PCs executed, 7,490,906,000 instructions, resident=0x0261
```

**Fifty-nine.** For 46.8 seconds and seven and a half billion instructions, the
V.34 page executes fifty-nine distinct instructions, and they are two things.

### One: the loop, 99.7% of everything

```text
2e1b   933,978,490   NOP (MAC), AX0 = DM(I4,M5)
2e1c   933,978,490   AF = AX0 AND AY0, AR = DM(I4,M5)
2e1d   933,978,490   AR = AR AND AY0, SR0 = DM(I4,M5)
2e1e   933,978,490   AR = MR0 + AF, SR1 = AR
2e1f   933,978,490   I0 = AR
2e20   933,978,490   SR = LSHIFT SR0 (HI, OR) BY 8
2e21   933,978,490   DM(I0,M1) = SR1, AR = MR1 XOR AF
2e22   933,978,490   IF NE JUMP $2E1B
```

All eight equal, and **PM `0x2e1a` does not appear in the histogram at all**.
The loop head never executes during residency. The loop is entered once, before
the page goes resident, and never leaves.

That **reinstates Session 114q and over-turns part of 114t**. 114t was right
that 114r/114s attributed the spin to the wrong routine — the block unpacker at
`0x2e24` is a separate loop (`0x2e25..0x2e2e`) and does not appear here either,
nor do the block loader at `0x2d80`/`0x2dda` or any of the eleven load entries;
all of that happens in the 5.9 s *before* residency. But 114t's "the loop
terminates, about four iterations per entry" was read off the V.90 control run,
where the loop is healthy. It does not hold here. **On V.34 it is a genuine
infinite loop.**

### Two: the sample interrupt, still perfect

The other fifty-one PCs are one interrupt handler — `0x0014` jumping to
`0x0072..0x00c9`, executed **374,080** times. Residency was
421,600 − 47,520 = **374,080 samples**. Exactly one execution per sample, no
drift: the 8 kHz clock into this page is flawless.

The handler saves `I4`/`L4`/`M5` to `DM(0x2E4A..0x2E4C)`, sets `L4 = 0x0040`
and `M5 = 1`, appends a sample to the circular buffer at `DM(0x2E44)` with a
count at `DM(0x2E49)`, restores the three registers and ends `IF NE RTI` at
`0x00c9`. It is correct, it is cheap, and it is the whole of the modem's
foreground progress for 46.8 seconds.

`0x00c9` is also where Session 114t's unexplained `from=00c9` entries into
`0x2e1b` came from: that is the `RTI` returning to the interrupted PC, which is
inside the loop. Not a second caller — the interrupt landing back where it left.

### What this settles

- The sample clock is not the problem and never was. It is exact.
- PM `0x0771`, the kernel per-sample dispatch, does not execute at all while
  `0x0261` is resident, which is Session 114p's result restated with a direct
  count instead of an inference.
- Everything in Sessions 114m–114o expressed as a *rate* was measuring how
  often a hung machine happened to be interrupted. Those figures should not be
  quoted again.

### Next

The loop's exit test compares `MR1` against the masked byte, and neither `MR1`
nor `AY0` nor `MR0` is reloaded inside the loop — they are set by whatever
entered at `0x2e1a`, before residency. So the question is the state at that
entry, which the histogram cannot see because it happens earlier.

Run the same probe with `--pc-histogram-from 0x0260` (the INFO page, resident
from ~3.3 s) plus `--watch-exec 0x2e1a`. That brackets the entry: the watch
gives `i4`/`mr1`/`ay0` at the moment it is entered, and the histogram gives
what else was running around it, in one call.

## Session 114v: the hang is the second of two entries, and it is table 0x1ea2

`--pc-histogram-from 0x0260 --watch-exec 0x2e1a` on a forced-V.34 call.
Evidence in `artifacts/interop/v34-live/hist-info.*`.

**PM `0x2e1a` is entered exactly twice in the entire call**, 182 cycles apart,
both from `0x2e19`, both before `0x0261` goes resident:

```text
pc=2e1a from=2e19 ret=2d81 cyc=110810547  i4=202e  mr0=2137  mr1=0019  ax0=0800
pc=2e1a from=2e19 ret=2d93 cyc=110810729  i4=1ea2  mr0=2137  mr1=0024  ax0=0001
```

`AY0` reads `0000` at entry on both, which is irrelevant — `0x2e1a` is
`AY0 = $00FF` and sets the mask itself. `M5 = 1` and `L4 = 0` on both, as
Session 114s already established.

The first entry returns: 182 cycles later the second is called, which cannot
happen otherwise. **So the second entry — `I4 = 0x1ea2`, `MR1 = 0x0024`,
reached from PM `0x2d92` — is the one that never comes back.** The histogram
from INFO residency onward confirms the same single loop, 941,289,612
iterations, and no third entry.

That is the whole fault, narrowed from a call to one instruction:
`CALL` at PM `0x2d92`, scanning the table at `DM(0x1ea2)` for a low byte of
`0x24`.

### Both tables carry their terminator in the image

Scanning the `0x0261` overlay's `dm.bin` the way the loop does — three words
per iteration, testing the first one's low byte:

```text
from 202e, terminator 19:  15 16 17 18 19   -> match at iteration 4
from 1ea2, terminator 24:  1b 20 21 22 23 24 -> match at iteration 5
```

Both addresses are inside the same downloaded DM block, so both are real
downloaded data. The first table matches at iteration 4 and its entry does
return. The second matches at iteration 5 and its entry does not.

So the table at `0x1ea2..0x1eb1` is **not what the image says by the time the
second call scans it**. This is the same conclusion Session 114r reached about
block `0x1afa`, and Session 114t retracted — reached again on a different
address, from the entry that demonstrably hangs rather than from one inferred
across runs.

Note the loop cannot be corrupting its own source: it writes to
`MR0 + byte = 0x2137 + byte`, which for these tables is `0x214c..0x2150` and
`0x2152..0x215b`. Neither overlaps `0x1ea2..0x1eb1`.

### Next

`--watch-dm 0x1eb1` on a forced-V.34 call. That is the single word whose low
byte should be `0x24` and stop the scan. The read log gives the live value the
walk actually sees, and any `[WATCH] dm w` line names the writer's PC outright.

Volume is affordable for one address: 941 M iterations sweeping a 16 K space
read any given word on the order of 170,000 times, so expect a log of that
order rather than the billions a `--watch-exec` on the loop would produce.

## Session 114w: the table is intact — it is the comparison that fails, not the data

`--watch-dm 0x1eb1,0x1ea2 --watch-exec 0x2e1a` on a forced-V.34 call.
Evidence in `artifacts/interop/v34-live/term-v34.*`.

```text
dm r 1eb1=0d24   170,802 reads    0 writes
dm r 1ea2=221b   170,802 reads    0 writes
```

**The table is pristine.** `DM(0x1eb1)` holds `0x0d24` — low byte `0x24`,
exactly the terminator `MR1` carries — for the whole call, is read 170,802
times, and is never written by anything. `DM(0x1ea2)` likewise matches the
overlay image. `0x2e1a` is entered twice, as in Session 114v.

So the corruption hypothesis is wrong. That is the third time this thread has
concluded "something is overwriting a table" — Sessions 114r, 114v, and by
implication 114k's shape — and the first time it has been tested directly at
the address that matters. **The data is right and the comparison is failing.**

### What that leaves

At iteration 5 of the first pass the loop reads `0x0d24` into `AX0`, and
`AF = AX0 AND AY0` with `AY0 = 0x00FF` should give `0x24`, which XORed with
`MR1 = 0x0024` is zero and exits. It does not. So by iteration 5 either `AY0`
is no longer `0x00FF`, or `MR1` is no longer `0x0024`, or **`I4` is no longer
where the alignment assumes** — the loop consumes three words per iteration and
only the first sets `AF`, so a pointer knocked off by one or two makes the
terminator forever the second or third word of a triple. That last possibility
fits the evidence exactly: `0x1eb1` is read 170,802 times and never matches.

Two emulator-side explanations were checked in the source and both are clean:

- The ISR at `0x0072` sets `MSTAT = 97`, whose bit 0 selects the secondary
  register bank, so its use of `AY0`/`AR`/`AF`/`SR0` cannot reach the loop's
  primary-bank copies. `stat_stack_pop()` restores `mstat` on `RTI` and
  `update_mstat()` swaps the bank back. Correct.
- Bank switching does not cover the DAG registers, which is precisely why the
  ISR saves and restores `I4`/`L4`/`M5` by hand to `DM(0x2E4A..0x2E4C)`.

But that save area is **three plain words, not a stack**. If an interrupt ever
nests, the inner entry overwrites the saved `I4` and the outer exit restores
the wrong one — and the walk loses its three-word alignment permanently while
the table stays intact. That is a hypothesis, flagged as such, but it is the
only mechanism found so far that predicts both observations: a correct
terminator, read constantly, never matching.

### Next, and it needs a small harness change first

The decisive measurement is `AF`, `MR1`, `AY0` and `I4` at PM `0x2e21` for the
first ten iterations. `--watch-exec 0x2e21` would produce 941 million lines,
so what is missing is a **bounded** watch: log the first N executions of an
address and then stop counting. That is a few lines in
`adsp2181_watch_exec()`'s call site, it is generally useful, and it is the
instrument this whole line of investigation has needed since Session 114m —
the same way `--pc-histogram` was.

With it, one call answers whether the loop's registers or its alignment go bad,
and on which iteration.

## Session 114x: both real entries succeed — the hang is a third, wild arrival

`adsp2181_watch_exec_limited()` logs only the first N executions of an address,
so an instruction inside a 941-million-iteration loop can finally be watched.
`--watch-exec` takes `ADDR:LIMIT` (`0x2e21:14`); plain `ADDR` is unlimited as
before. Two notes on the implementation: the limited watch clears its own
`watch_exec` flag on the last logged execution, so the hot path keeps costing
one array test; and `ADSP` in `eicon_adsp_sip.py` comes from
`dial_tikrnl_drive`, whose own prototype table needed the new function — without
`argtypes` ctypes truncated the 64-bit `cpu` pointer to an int and the first run
segfaulted. 263 tests pass.

`--watch-exec 0x2e1a,0x2e1b:14,0x2e21:14` on a forced-V.34 call.
Evidence in `artifacts/interop/v34-live/iter-v34.*`.

### Both entries at 0x2e1a complete correctly

```text
entry 1  i4=202e  mr1=0019   AF per iteration: 15 16 17 18 19  -> match, exits
entry 2  i4=1ea2  mr1=0024   AF per iteration: 1b 20 21 22 23 24 -> match, exits
```

`AY0` is `0x00ff` throughout both, `MR0` is `0x2137`, and the terminator is hit
at iterations 4 and 5 — exactly where Session 114v's static scan of the overlay
image said it would be. The second entry ends at cyc 113,459,663 with
`ax0=0d24`, `af=0024`, `mr1=0024`.

**So Sessions 114v and 114w are retracted.** The tables are intact, *and* both
scans succeed. Neither of the two legitimate entries hangs. The whole line of
inquiry from 114r onward — that some record is corrupt and its scan runs away —
is wrong at its root.

### The hang is a third arrival, 875,000 cycles later, with foreign registers

```text
cyc=114,335,373  pc=2e21  from=2e20  ret=2726  psp=9 lsp=1 cntr=000c
                 i4=2132  ax0=a57e  ay0=5a82  af=0002  mr0=8000  mr1=026c
```

Nothing about this belongs to the scan:

- **`AY0 = 0x5a82`**, not `0x00ff`. `0x5a82` is 0.7071 in Q15 and `MR0 = 0x8000`
  is −1.0: these are twiddle/filter constants, not a byte mask.
- **`MR1 = 0x026c`**, not `0x0019` or `0x0024`.
- **`ret = 0x2726`**, not `0x2d81`/`0x2d93`. A different caller entirely.
- `psp=9`, `lsp=1`, `cntr=0x0c` — inside a hardware `DO UNTIL` with a count of
  12, which neither real entry had.
- `0x2e1a` is watched **unlimited** and logs exactly two executions all call.
  This arrival never passed through the loop head, so `AY0 = $00FF` was never
  executed and the mask was never set.

`AF = AX0 AND 0x5a82` against `MR1 = 0x026c` will essentially never be zero, so
`IF NE JUMP $2E1B` runs forever. That is the 941 million iterations.

### It is also writing into low data memory

The loop's store is `DM(I0,M1) = SR1` with `I0 = MR0 + AF`. With
`MR0 = 0x8000`, that address masks to 14 bits as `0x0000 + AF` — so every
iteration writes into **DM `0x0000..0x00ff`**, and it does so 941 million times.
This is a live, large-scale DM corruption source, and it is worth checking
against the symptoms Sessions 106–114l attributed to the bulk worker.

### Next

The question is now simply how control reaches `0x2e1b` with `ret=0x2726`.
`I5 = 0x2e0c` at every runaway sample, and Session 114r logged `i5` values of
`2e0c`, `2e14`, `2e1a`, `2e22`, `2e2a`, `2e30` — consistent with a table of
routine entry points, which would make this a dispatch landing one instruction
past its target.

Watch `0x2725`/`0x2726` and the `DO UNTIL` around them, with
`--watch-exec 0x2e1b:2` so the budget is not spent on the two healthy entries
first. That gives the caller and the value it dispatched through.

## Session 114y: PM 0x2725 is a dispatch table walker, and the table is corrupt

`--watch-exec 0x2725:40,0x2726:40,0x2e1a,0x2e1c:13,...` on a forced-V.34 call.
Evidence in `artifacts/interop/v34-live/arrive-v34.*`.

### The caller

```text
2719: AY0 = $00F1            271e: DM($2161) = I0
271a: DM($215C) = AY0        271f: DM($2162) = I7
271b: CNTR = $000D           2720: DO $272A UNTIL NOT CE
271c: I0 = $009B             2721:   I7 = DM($2162)
271d: I7 = $00A8             2722:   AR = DM(I7,M5)
                             2723:   DM($2162) = I7
                             2724:   I7 = AR
                             2725:   CALL (I7)
                             2726:   I0 = DM($2161)
                             2727:   AR = DM(I0,M1)
                             2728:   DM($2161) = I0
                             2729:   I0 = AR
                             272a:   DM(I0,M1) = MR1
```

Thirteen routine addresses at `DM(0x00A8)`, called in turn through `I7`, each
one's `MR1` stored through a second table at `DM(0x009B)`. That second table
holds `0x3f78..0x3f84` — read-database words — so this is a **read-database
publisher**: thirteen measurements written to DB `0x3F78..0x3F84` per pass.

### The table is right on the first pass and wrong on the second

Targets actually called, taken from `AR` at `0x2725`:

```text
pass 1, cyc 109,708,642..109,709,022
  0eab 0eaf 0eb7 0eb3 0e94 0e9a 0e91 0e96 0ece 0ed0 0ed2 0eda 0edc
  -- identical to the static table in the 0x0261 image, all 13

pass 2, cyc 110,574,037
  slot 0: 11e4        (image: 0eab)
  slot 1: ee1c        (image: 0eaf)   <-- masks to PM 0x2e1c
```

PM addresses are 14 bits, so `CALL (I7)` with `I7 = 0xee1c` jumps to
**`0x2e1c`**. That is the wild arrival of Session 114x, and it explains it
completely: nothing dispatched to `0x2e1a`, so `AY0 = $00FF` never ran, and the
registers the loop found were whatever the publisher had — `AY0 = 0x5a82`,
`MR0 = 0x8000`, `MR1 = 0x026c`.

`0xee1c` has bits set above the 14-bit PM range, so the stored word is not a
mangled address; it is **data that has been written over the table**.

### And the loop then writes back over the same table

The runaway's store is `DM(MR0 + AF)` with `MR0 = 0x8000`, which masks to
`DM(0x0000 + AF)` — the range `0x0000..0x00FF`. The dispatch table is at
`0x00A8..0x00B4` and its result pointers at `0x009B..0x00A7`. **Both are inside
the range the hung loop scribbles across 941 million times.** So once it starts
the damage is self-sustaining, and the DM corruption seen from Session 106
onward may be this rather than the bulk worker.

The first corruption still precedes the loop: the table was already wrong at
cyc 110,574,037, and the loop only ran away afterwards. Something else wrote it
in the ~865,000 cycles between the two passes.

### Next

`--watch-dm 0x00a8,0x00a9`. Two words, both provably wrong at a known moment.
The read log shows what the walker fetches on each pass, and the `dm w` line
names the PC that wrote `0x11e4` and `0xee1c` into a table of PM entry points.
That writer is the fault.

## Session 114z: root cause — the "fixed" bulk worker still corrupts, one table along

`--watch-dm 0x00a8:400,0x00a9:400` on a forced-V.34 call. `--watch-dm` now
takes `ADDR:LIMIT` like `--watch-exec`, sharing one budget across reads and
writes, because the hung loop sweeps every word of low DM millions of times and
an unbounded watch there is not affordable. 263 tests pass. Evidence in
`artifacts/interop/v34-live/table-v34.*`.

### The chain, in one call, with nothing inferred

```text
cyc 112,697,738  dm r 00a8=0eab  pc=2723   walker reads the table -- CORRECT
cyc 112,697,795  dm r 00a9=0eaf  pc=2723

cyc 112,831,330  dm w 00a8=11e4  ppc=1934  i4=3765  sr0=11e4
cyc 112,835,569  dm w 00a9=ee1c  ppc=1930  i4=3765  sr0=ee1c

cyc 113,009,522  dm r 00a8=11e4  pc=1917   the worker reading its own ring
cyc 113,012,597  dm r 00a9=ee1c  pc=1910

cyc 113,563,110  dm r 00a8=11e4  pc=2723   walker reads the table -- CORRUPT
cyc 113,563,131  dm r 00a9=ee1c  pc=2723
                                           CALL (I7), I7 = 0xee1c & 0x3fff
                                           -> PM 0x2e1c, and the call is over
```

**PM `0x1930` and PM `0x1934` write `0xee1c` and `0x11e4` into the read-database
dispatch table at `DM(0x00A8..0x00A9)`.** The walker at PM `0x2722` reads the
table correctly before those writes and incorrectly after. Nothing else is
required to explain the freeze.

### PM 0x1930 is the bulk worker the handoff records as fixed

Session 114k identified this worker — commit `443a566`, "the V90D bulk worker at
PM 0x1930 overwrites the V.34 test table" — and 114k–l bounded its ring pointer,
after which the handoff has carried it as **"fixed and hardware verified"**.

The comment in `_service_bulk_lengths()` states the fix exactly:

> the worker's ring pointer at PM `0x192e` stops wrapping and marches
> `0x0061..0x0769`, straight through the V.34 script's test-routine table at
> `DM(0x064B..0x066A)` [...] Holding the floor pair instead bounds the sweep to
> `0x0061..0x0241` and puts zero writes in the table.

Both halves of that are true. The sweep is bounded to `0x0061..0x0241` and it
does put zero writes in `DM(0x064B..0x066A)`. **But `0x00A8` and `0x00A9` are
inside `0x0061..0x0241`.** The fix bounded the march into a range that contains
the read-database dispatch table, and verification only ever checked the one
table the previous session had been looking at.

So the defect was never fixed. It was moved from a table whose corruption
resolved an exit test wrongly, to a table whose corruption sends `CALL (I7)`
into the middle of a routine — which is why the symptom changed character
between 114l and 114m and why every session since has been chasing a loop that
was only ever the victim.

### Where this leaves the earlier sessions

- Session 114x's "wild arrival" and 114y's "table overwritten" are both
  confirmed, with the writer now named.
- 114r/114v/114w (record corruption at `0x1afa`/`0x1ea2`) remain retracted;
  those tables are intact and their scans succeed.
- The "V.34 upstream stays at 7,200" and "exact upstream rate" entries are worth
  revisiting once this is fixed: a worker marching through `0x0061..0x0241`
  every call is not a V.34-only hazard.

### The fix

Bounding the sweep is the wrong shape of remedy — any bound still marches
through *something*. The worker's pointer fails to wrap; the repair is to make
it wrap, or to stop driving the firmware pair on this page at all, as
`_service_bulk_lengths()` already does for the `0x0261` yield.

Whatever is chosen, verification must assert **zero writes anywhere in
`0x0061..0x0241`**, not zero writes into one nominated table. That is a
`--watch-dm` on a handful of addresses across the range, and it is what the
114k–l verification should have been.

## Session 115: the verification instrument, and why the wrap fix was not made

Two things were asked for. The second is delivered; the first turned out to
rest on a premise this session disproved, and is not made.

### The wrap fix is not available as diagnosed

Session 114z proposed making the worker's ring pointer wrap, on the reading
that its length was zero — `_service_bulk_lengths()` documents "the native
V.34 worker's modulo bound at PM 0x1930 is zero". The wrap itself is real:

```text
1919/1923/1927:  IF NOT AC AR = AR + AY1
```

a conditional add of `AY1` on unsigned underflow past `AY0`. And `AY1` is
descriptor word 1, loaded at PM `0x1905`. So a new diagnostic prints the
descriptor as the worker reads it, at page-resident time:

```text
bulk descriptor @DM(0x0000): [0]=2852 [1]=2863 [2]=2876 [3]=28ac
                             [4]=0000 [5]=ffff [6]=0aab [7]=02a2
```

`AY1 = 0x2863`, not zero, and PM `0x1906` computes `AY1 - AX0 =
0x2863 - 0x2852 = 0x11`, a real length over ring pointers at `0x2852..0x28ac`.
**There is a length, and it is nowhere near `0x0061..0x0241`.** Publishing one
would be inventing a value on a disproved premise, which is exactly how the
114k–l fix came to pass its own verification. Why `AX0` — the write pointer
captured at PM `0x1925`, before the correction that PM `0x1927` applies to
`AR` — escapes into low DM is now the open question.

### `--assert-dm-clean LO:HI[@OVERLAY]`

The instrument 114k–l lacked. Every word of the range is write-watched once, so
each `[WATCH] dm w` line is a failure that names its writer. Reads are neither
logged nor charged (`adsp2181_watch_dm_writes()`), because low DM is read
constantly and a read would spend the budget before the write arrived.

The `@OVERLAY` suffix is not a convenience. Run unconditionally, the assertion
reports **481 writes from a single PC, `0x3738`, writing zeros** — a legitimate
one-shot clear of low DM early in the call, which consumes the entire budget so
that nothing later is ever seen. An assertion that cannot see past the memset
is the shape of verification that passed in 114k–l.

### Baseline on the current build

Armed at `0x0261` residency, `0x0061:0x0241@0x0261`:

```text
69 violations
  64  ppc=00c0   DM 0x00C0..0x00FF, contiguous
   4  ppc=2e21   DM 0x0080, 0x0082, 0x0200, 0x0202
```

The 64 are the sample interrupt handler's own ring — `L4 = 0x0040` at PM
`0x0087` and exactly 64 contiguous words — and are legitimate per-sample
traffic. The 4 are the runaway loop's store, confirming 114z.

**But that first line is the finding.** The ISR's live sample ring sits at
`DM(0x00C0..0x00FF)`, *inside* `0x0061..0x0241`. Sessions 114k–l bounded the
worker's march into a range containing the interrupt handler's ring buffer and
the read-database dispatch table both. `0x0061..0x0241` is not a safe place to
bound anything, and no bound is the right remedy.

### Next

`0x0061..0x0241` needs to be re-read as what actually lives there before any
fix: the ISR ring at `0x00C0..0x00FF`, the dispatch table at `0x00A8..0x00B4`
and its result pointers at `0x009B..0x00A7` are all mapped now. The remaining
question is PM `0x1925`'s `AX0`, and `--assert-dm-clean` is the gate for
whatever answer follows.

## Session 115b: the ring index never resets, and the reason is parity

Chasing `AX0` at PM `0x1925` leads one register further back. `AX0` is not the
pointer; it is a copy. **`AX1` is the ring index**, and the write address is
exactly `0x0062 + AX1`:

```text
pc=1930   i0=0061 ax1=ffff      pc=1930   i0=00c9 ax1=0067
          i0=0063 ax1=0001                i0=00cb ax1=0069
          i0=0065 ax1=0003                i0=00cd ax1=006b
          i0=0067 ax1=0005                i0=00cf ax1=006d
```

`AX1` starts at `0xffff` and **steps by 2 every pass**, monotonically, for the
life of the call. `0x0062 + AX1` walks it straight through low DM — which is
the `0x0061..0x0241` sweep, 240 passes of +2 across 480 words.

### The reset exists and never fires

```text
1935: DM(I1,M0) = AR, AR = AR - AY0
1936: AR = AR - AY1
1937: IF NE RTS
1938: DM(I1,M0) = AY0        <- the reset
```

Watched over 40 passes:

```text
1935: 40   1936: 40   1937: 40   1938: 0
```

**`0x1938` executes zero times.** The ring index is never reset, and the test
at `0x1937` is an *equality* — `IF NE RTS` returns unless the difference is
exactly zero.

The operands say why. At `0x1936`, after `AR = AR - AY0` with `AY0 = 0xffff`:

```text
ar = 0002 0004 0006 0008 000a 000c ...
```

**Always even, always +2.** It is then compared against `AY1`, which is
descriptor word 1 — `0x2863` from the Session 115 descriptor dump — and
`0x2863` is **odd**. An even value stepping by 2 can never equal an odd one, so
the equality is unreachable and `0x1938` is dead code for the whole call.

That is the defect, and it is as simple as the shape of the bug always
suggested: a pointer that advances two at a time, checked for equality against
a bound of the opposite parity. It never overshoots by a little and gets
caught; it steps over the target every single time.

### Two candidate remedies, and why neither is applied here

`AY0` is the harness's own `BULK_DESCRIPTOR_LOWER_LIMIT`, published into
descriptor word 5 by `publish_bulk_lower_limit()`. `AY1` is descriptor word 1
and comes from the page image. `AX1` is the stored running index, written back
at `0x1935`, so it and `AY0` are independent — changing the floor's parity
should make the equality reachable.

1. **Publish `0xFFFE` instead of `0xFFFF`.** One constant. But `0xFFFF` is
   documented as deliberate — "the word immediately below DM zero is the 16-bit
   -1 sentinel" — and `-2` misrepresents that contract.
2. **Treat the step of 2 as the anomaly.** The index advances once per near/far
   pair; if the bound was written for a step of 1, the floor is right and the
   pairing is wrong.

These predict the same A/B result but different things about the ADDSP
contract, and picking by experiment alone would be guessing at a protocol. Both
are one line and both are gated by `--assert-dm-clean 0x0061:0x0241@0x0261`,
whose current baseline is 69 violations — 64 the sample ISR's legitimate ring,
4 the runaway loop, and zero from the worker on the calls measured since.

### Next

Establish which of the two the ADDSP guide specifies for descriptor word 5 and
the near/far index, then apply that one and re-run the assertion. The bound
should also be checked as an inequality rather than an equality if the guide
allows it: an equality test on a stepping pointer is fragile even when the
parity happens to line up.

## Session 115c: the guide kills one candidate, does not document the other, and points elsewhere

`docs/addspv90guide.pdf`, write-database words `0xDC`/`0xDD`:

> **Nearbulklength** — parameter specifying the length of the Near Echo
> canceller delay line. This parameter gives the length in number of (X,Y)
> couples.
>
> **BulkLength** — parameter specifying the circular length of the Far Echo
> canceller Bulk delay line. This parameter gives the number of (X,Y) couples
> that have to be stored in the bulk.

**The step of 2 is by design.** The line stores an (X,Y) couple per symbol —
`BulkInputX`/`BulkInputY` at `0xDE`/`0xDF`, `BulkOutpuTX`/`BulkOutputY` at
read-DB `58`/`59` — so a couple is two DM words and the index advancing by two
per pass is correct. Session 115b's second candidate remedy, "treat the
step-of-2 as the anomaly", is **ruled out by the specification**.

The first candidate is not supported either. The guide has no descriptor word 5
matching this structure. The one place index 5 appears — "RoundTripDelay in
number of 2D-symbols (used for Bulk delay)" — belongs to a different block,
whose words 0..2 are Q15 scaling constants (`TX_precLev`, `RX_precLev`,
`AverRXCompensation`); ours reads `2852 2863 2876 28ac`, which are DM pointers.
So `BULK_DESCRIPTOR_LOWER_LIMIT = 0xFFFF` and the "-1 sentinel" reading are
this repo's inference, not the guide's, and the guide neither confirms nor
refutes them.

### What the guide does expose: the seeded lengths do not fit the rings

Read as start/end pairs, the descriptor gives two ring extents:

```text
near  [0]=2852 [1]=2863   ->  0x11 =  17 words
far   [2]=2876 [3]=28ac   ->  0x36 =  54 words
```

PM `0x1906` computes exactly this difference, `AY1 - AX0 = 0x11`.

`_service_bulk_lengths()` seeds **near=49, far=129** — and by the guide those
are counts of (X,Y) couples, so they claim **98 and 258 DM words** against rings
of **17 and 54**. The seeded length exceeds its buffer by roughly five times in
both directions.

A pointer told to wrap after 98 words in a 17-word ring does not wrap inside the
ring; it walks out of it. That is a better account of the march through low DM
than the parity coincidence, and it makes the parity mismatch a symptom —
`AX1` never reaches a bound that lies far outside the buffer it was derived
from.

It also explains why bounding the sweep in 114k–l changed the range but not the
behaviour: the bound was applied to the symptom, and the seed that oversizes the
ring was left in place.

### Next

Check `bulk_delay_seed()` against the ring extents rather than against
`BULK_SEED_CEILING = 0x0B00`, which is 2816 couples and cannot be right for a
17-word buffer. Two questions decide the fix:

1. Are `[0..3]` really start/end pairs? PM `0x1906`'s `AY1 - AX0` says yes for
   the near pair; the far pair should be confirmed the same way.
2. Is the seed's unit couples or words? If couples, the current values are ~5x
   the ring; if words, ~10x.

Then reseed to fit and re-run `--assert-dm-clean 0x0061:0x0241@0x0261`. The
parity finding in 115b stands as an observation and should not be fixed
directly — if the seed is corrected the bound becomes reachable on its own, and
if it is not, changing parity only moves where the pointer stops.

## Session 115d: there is no far pair on this path, and the rings belong in low DM

`--watch-exec 0x1906:12,0x190e:12,0x193e:12,0x1941:12` on a forced-V.34 call.
Evidence in `artifacts/interop/v34-live/pairs-v34.*`.

```text
1906: 12    190e: 12    193e: 0    1941: 0
```

**The far branch never executes.** PM `0x1907`'s `IF NOT AC JUMP $193A` is not
taken on V.34, so `0x193a..0x1949` — the second copy of the pair-consuming
sequence — does no work at all. There is no far pair to confirm. Session 115c's
reading of `[0..3]` as two start/end pairs is wrong twice over: wrong in kind,
and wrong to have treated `[2]`/`[3]` as an active ring.

### What the near path actually carries

At PM `0x190e` (`I0 = AX1`, immediately before `SR0 = DM(I0,M1)`):

```text
ax1=ffff  ay0=0062        ax1=0009  ay0=01dc
ax1=0001  ay0=0062        ax1=000b  ay0=01dc
ax1=0003  ay0=0062        ax1=000d  ay0=01dc
ax1=0005  ay0=0062
ax1=0007  ay0=0062
```

`AY0` is the ring **base**, and it is `0x0062` — then `0x01dc`. Two buffers, and
the write address `0x0062 + AX1` from Session 115b is that base plus the index,
exactly.

**So the bulk delay line lives in low DM by design.** The `0x0061..0x0241` march
is not a pointer that has escaped into foreign memory; it is the delay line
itself, in the region allocated for it. Every session from 114k onward,
including 114z and 115c, has been describing the buffer as though it were the
trespass.

### Which makes it an allocation conflict

The trespass is real, but it is the other way round. Inside the same region sit:

```text
0x0062          bulk delay ring base (near)
0x009B..0x00A7  read-database result pointers
0x00A8..0x00B4  read-database dispatch table   <- 114z's 0xee1c
0x00C0..0x00FF  sample ISR ring, L4 = 0x0040   <- 115's 64 "violations"
0x01DC          second bulk delay ring base
```

A near length of 49 couples from `_service_bulk_lengths()` is 98 words from
`0x0062`, reaching `0x00C4` — past the dispatch table at `0x00A8` and into the
ISR ring. The ring is not escaping its allocation; **its length is larger than
the gap the allocation leaves it**, and it overruns the two structures sitting
above it.

That also settles what `--assert-dm-clean 0x0061:0x0241` was really showing.
Writes in that range are not per se a fault — the delay line belongs there. The
assertion needs to be scoped to the structures that must not be written
(`0x009B..0x00B4` and `0x00C0..0x00FF` from a non-ISR PC), not to the whole
range.

### Not reseeded, and why

"Reseed to fit" now has a concrete meaning — fit the ring between `0x0062` and
the first structure above it, which is 0x46 words, or 35 couples against the
current 49. But the layout above is read off one call's registers and a static
image, and this is the third consecutive session in which measuring has
overturned the previous session's structural reading (114r/114v/114w, then
115b/115c, now 115c again). Publishing 35 on that record would be the same
mistake in a new place, and the 114k–l fix is in the tree precisely because
someone did that.

What makes it safe is cheap and specific: confirm the allocation rather than
infer it. `--watch-dm` on `0x009B`, `0x00A8`, `0x00C0` and `0x01DC` with the
writer PCs over a full call gives the true occupied extents, and the second
base at `0x01DC` needs the same treatment — 129 couples from there is 258 words,
reaching `0x02DE`, past the end of the region entirely.

Then reseed to the measured gap and gate on a **scoped** assertion.

## Session 115e: the extents are not confirmed — four "structures", one profile

`--watch-dm 0x009b:150,0x00a8:150,0x00c0:150,0x01dc:150` on a forced-V.34 call.
Evidence in `artifacts/interop/v34-live/extent-v34.*`.

The four addresses were chosen as one word from each structure Session 115d
mapped: read-database result pointers, the dispatch table, the sample ISR ring,
and the second bulk ring base. If that map were right they would have different
owners. They do not:

```text
            writers                                   readers
0x009b  14x 36fc  4x 32d3  4x 31df  1x 3738  1x 1930   48x 32a4  14x 37d6
0x00a8  14x 36fc  4x 32d3  4x 31df  1x 3738  1x 1934   48x 32a4  14x 37d6
0x00c0  14x 36fc  4x 32d3  4x 31df  1x 3738  1x 1934   48x 32a4  14x 37d6
0x01dc  14x 36fc  4x 32d3  4x 31df  1x 3738  1x 1934   48x 32a4  14x 37d6
```

Identical PCs in identical proportions at all four, including one at `0x01dc`,
which is 308 words away from the others. **These are not four structures with
four owners.** They are four samples of one region that a small set of routines
sweeps end to end — `0x36fc`, `0x32d3` and `0x31df` writing, `0x32a4` and
`0x37d6` reading, plus the one-shot memset at `0x3738` and the bulk worker at
`0x1930`/`0x1934`.

So the allocation map in Session 115d is **not confirmed**, and this method
cannot confirm it: watching representative words tells you who sweeps the
region, not where one structure ends and the next begins. The dispatch table at
`0x00A8` survives independently — Session 114y watched PM `0x2722` read it and
get all thirteen entries right on the first pass — but the extents around it,
and the `0x009B`/`0x00C0`/`0x01DC` assignments, rest on nothing measured.

Which means "reseed to fit" still has no target. The gap of 0x46 words quoted in
115d assumed the dispatch table is the first structure above `0x0062`; that
assumption is exactly what this probe failed to establish.

### What would actually settle it

Ownership has to be derived from the whole region at once, not sampled.
`--assert-dm-clean` already write-watches every word of a range with a budget of
one, which is why it reported the memset and nothing after it. Giving it a
per-address budget — `LO:HI:BUDGET@OVERLAY` — and grouping the resulting
`[WATCH] dm w` lines by address and writer produces the region's true partition
in a single call: every word, its owner, in order.

That is a small change to an instrument that already exists, and it replaces
three sessions of inferring layout from four addresses at a time.

### A note on this stretch of work

Sessions 114r, 114v, 114w, 115b, 115c and 115d each proposed a structural
reading that the next measurement overturned. The pattern is consistent: each
was built by inference from a static image or a handful of watched addresses,
and each fell to a probe that measured the whole thing instead. The instruments
that have held up — the PC histogram in 114u, the bounded watches in 114x —
are the ones that enumerate rather than sample. That is the lesson worth
carrying into the fix, and it is why no length has been published yet.

## Session 115f: there is no partition — and the descriptor base is zero

`--assert-dm-clean` gains a per-address budget, `LO:HI:BUDGET[@OVERLAY]`, so it
surveys ownership instead of stopping at the first writer of each word. Run
unarmed over the whole region with a budget of 6, then grouped by address and
writer:

```text
481 addresses written, 1 contiguous run
  0061..0241  (481 words)  36fc,3738
```

**One run. No boundaries anywhere in 481 words.** Every word has the same
owners in the same order — the one-shot memset at `0x3738` and the sweeper at
`0x36fc` — and they exhaust a budget of 6 everywhere before `0x32d3`, `0x31df`
or the bulk worker gets a look in.

So the question Sessions 115d and 115e were asking — where does one structure
end and the next begin — has no answer, because **the region is not partitioned
into structures at all.** There is no gap above `0x0062` to reseed into. The
dispatch table at `0x00A8..0x00B4` and the bulk delay ring are not neighbours
that have overrun each other; they are two consumers of the same words.

### Which points at the base, not the length

`publish_bulk_lower_limit()` reads the descriptor base from `DM(0x32F7)`:

```python
base = int(dm[0x32F7]) & 0x3FFF
```

Session 115 measured it: **`bulk descriptor @DM(0x0000)`**. The base is zero.

That single fact accounts for everything this region has shown. With a base of
zero, descriptor word 5 lands at `DM(0x0005)` — which is what the harness logs
every call — the ring bases resolve to `0x0062` and `0x01DC`, which are offsets
from zero rather than addresses in a buffer, and the delay line is written
straight across whatever low DM happens to hold, including the read-database
dispatch table the walker at PM `0x2722` reads back.

A base of zero is what an unpublished pointer looks like. The guide names this
class of word explicitly — `DTESCCstructPtr`, `HOSTSCCstructPtr`,
`ATdbaseAddress`, `DCESCCstructPtr` are all "base address of ..." words that a
layer publishes before the consumer runs — and `DM(0x32F7)` is the same shape.
Nothing in the tree publishes it.

**So the defect is probably not a length at all.** Every reading since 114k has
assumed the pointer is escaping a correctly-placed buffer. If the buffer was
never placed, the pointer is doing exactly what it was told, from address zero.

### Next

Establish what `DM(0x32F7)` should hold and who should write it: which layer
owns the bulk descriptor pointer, and whether the V.34 page expects the common
layer to publish it the way `publish_bulk_lower_limit()` publishes word 5. If
it should be non-zero, that is the fix, and it is one word — with the whole of
`0x0061..0x0241` freed as a side effect.

Two checks before touching it: read `DM(0x32F7)` on a **V.90** call, where the
same worker runs and the page trains to `0x00c4`; if it is non-zero there, the
comparison is decisive. And search the MIPS side for any write to `0x32F7`,
since the descriptor may be the host's to publish.

## Session 115g: the base is zero on V.90 too — that hypothesis is dead

A default call, same rig, which loaded `0x026A` and walked to
`TrnProgress 0x00b0`:

```text
V.90  bulk descriptor @DM(0x0000): [0]=2aca [1]=2ad2 [2]=2ae5 [3]=2b1b
                                   [4]=0000 [5]=ffff [6]=ffa5 [7]=094c
V.34  bulk descriptor @DM(0x0000): [0]=2852 [1]=2863 [2]=2876 [3]=28ac
                                   [4]=0000 [5]=ffff [6]=0aab [7]=02a2
```

**`DM(0x32F7)` is zero on V.90 as well**, and V.90 trains through it. So a zero
descriptor base is normal for this firmware, the descriptor really does live at
`DM(0x0000)`, and Session 115f's "unpublished pointer" account is wrong.

A related correction from the same search: **no overlay writes `DM(0x32F7)`.**
An earlier scan appeared to find a write in `026e-infoh` at PM `0x3734`, but
that scan indexed `pm.bin` as 4-byte words where it is 3, and the corrected
disassembly reads `DM(I4,M5) = $32F7` — the constant `0x32F7` being stored into
a table of addresses, not a store to that address. Nothing publishes the base on
either page, and V.90 is fine regardless.

### What the comparison does show

The two descriptors differ where it matters least according to the repo's own
comment, which calls words 5..7 sparse:

```text
        [0]    [1]    [2]    [3]    [4]    [5]    [6]    [7]
V.34   2852   2863   2876   28ac   0000   ffff   0aab   02a2
V.90   2aca   2ad2   2ae5   2b1b   0000   ffff   ffa5   094c
```

`[0..3]` are page-local buffer pointers in the `0x28xx`/`0x2axx` region — real
addresses well clear of low DM, which sits awkwardly with the `AY0 = 0x0062`
ring base measured at PM `0x190e` in Session 115d and is not resolved here.
`[4]` and `[5]` are identical, `[5]` being the harness's own publication.

`[6]` and `[7]` are the only free variables: `0x0aab`/`0x02a2` on V.34 against
`0xffa5`/`0x094c` on V.90. `0xffa5` is negative as a signed word where `0x0aab`
is a large positive one, and `[7]` is the value PM `0x1904` loads into `SR1` and
PM `0x192f`/`0x1930` add to the index. A sign difference in a word that feeds
pointer arithmetic, between the page that works and the page that hangs, is the
most concrete difference the two calls expose.

### Standing back

This is the fifth structural hypothesis in this stretch to be killed by the next
measurement: the corrupt record (114r/114v/114w), the parity mismatch as cause
(115b), the oversized seed (115c), the allocation conflict (115d/115e), and now
the unpublished base (115f). Each was plausible, each was cheap to test, and
each was wrong.

What has not moved once since Session 114z is the failure chain itself, which is
measured end to end and reproduced on every call: PM `0x1930`/`0x1934` overwrite
`DM(0x00A8..0x00A9)`, the walker at PM `0x2722` reads `0xee1c`, `CALL (I7)`
enters the scan loop at `0x2e1c` instead of `0x2e1a`, `AY0 = $00FF` never runs,
and the loop spins for 99.7% of the call while the 8 kHz interrupt keeps perfect
time.

The recommendation is to stop proposing mechanisms for *why* the worker writes
where it does and instead diff the two pages directly — the descriptor words
above, and the register state at PM `0x1900`..`0x1935` on a V.90 call against
the V.34 trace already captured. The bounded watches make that a single call per
side, and it compares a working configuration against a failing one rather than
reasoning from one side alone.

## Session 115h: EXTENDED_LEC is real, reaches the card, and does not fix it

The driver defines one host-side control over the echo canceller, and this
project has never sent it. `mdm_msg.h`:

```c
#define DSP_CAI_MODEM_DISABLE_2400_SYMBOLS 0x01
...
#define DSP_CAI_MODEM_DISABLE_3429_SYMBOLS 0x20
#define DSP_CAI_MODEM_EXTENDED_LEC         0x80    /* same byte */
```

`build_cai()` writes that byte as `cai[21] = 0   # disabled symbol rates` — a
comment that accounts for half of what is in it. Worse, the branch containing
it only runs when `s7`/`s10` are set, which this project's calls never do, so
on every call so far `cai[21]` has been transmitted as *padding*: present in
the 26 bytes `add_b1()` sends, always zero, never considered.

It is now settable with `EICON_EXTENDED_LEC=1`, applied to the padded buffer
after the length is fixed. Default behaviour is unchanged; 263 tests pass.

### The card takes it, and it moves the words we were looking at

Forced-V.34 call with the flag set:

```text
             [0]    [1]    [2]    [3]    [4]    [5]    [6]    [7]
V.34 plain  2852   2863   2876   28ac   0000   ffff   0aab   02a2
V.34 +LEC   2852   2863   2876   28ac   0000   ffff   ee60   eeaf
V.90 plain  2aca   2ad2   2ae5   2b1b   0000   ffff   ffa5   094c
```

`[6]` and `[7]` — the only two words Session 115g found differing between the
working and failing pages — **change when the LEC flag changes, and only they
do.** So those words are echo-canceller configuration, the flag is plumbed
through the CAI to the DSP, and the firmware acts on it. That is worth having:
it identifies `[6]`/`[7]` positively rather than by elimination.

It also disposes of 115g's reading. `[7]` going negative was proposed there as
the interesting V.34-vs-V.90 difference; with the flag, V.34 has negative
`[6]`/`[7]` too and still hangs. The sign is a LEC-mode difference, not the
fault.

### It does not fix the freeze

```text
TrnProgress   0x004f -> 0x0064          (unchanged, sixth consecutive call)
pc-histogram  59 PCs, 7,471,700,000 instructions, resident=0x0261
              2e1b..2e22  931,583,860 iterations each, 99.7%
assert-dm-clean  64x ppc=00c0 (ISR ring)   4x ppc=2e21 (the loop)
```

Identical to every previous call to within a fraction of a percent.

### Where that leaves it

The suggestion behind this session was that V.90 may simply not need what V.34
needs, so the two-page diff proposed in 115g could be comparing a page that uses
the echo canceller against one that does not — and that the driver would know
what the host is supposed to configure. Both halves were right, and the second
found a control that had been sent as zero for the life of the project.

Turning it on is not sufficient. But it is the first knob anyone has turned that
moved `[6]`/`[7]`, which means the mechanism connecting host configuration to
the bulk delay is intact and reachable — the fault is downstream of it.

Next worth trying, in order of cost: the same A/B on `EICON_EXTENDED_LEC=1` with
V.90 (does `[6]`/`[7]` move there too, and does V.90 still train?), and the
symbol-rate disables in the same byte, which are equally never set and would
narrow which symbol rate the page is configured for.

## Session 115i: symbol-rate disables also reach the card, also change nothing

The low six bits of `cai[21]` disable individual V.34 symbol rates and, like
`EXTENDED_LEC` above them, have never been set on this project's calls. They are
now settable with `EICON_DISABLE_SYMBOLS=2743,2800,3000,3200,3429` — naming
rates rather than a raw mask — which leaves V.34 with 2400 baud only, the
smallest configuration and the one whose delay-line sizing should differ most.

```text
                    [6]    [7]
plain              0aab   02a2
EXTENDED_LEC       ee60   eeaf
2400 baud only     0ae8   021a
```

The card takes it: `[6]`/`[7]` move again, and again nothing else does. Three
independent host-side configuration changes now move exactly those two words,
which settles what they are — configuration-dependent delay-line sizing — and
confirms the CAI path to the DSP is fully functional.

### And the failure does not move at all

```text
TrnProgress   ... 0x0044 -> 0x0046 -> 0x0064     (seventh consecutive call)
pc-histogram  59 PCs, 7,593,444,000 instructions, resident=0x0261
              2e1b..2e22  946,763,100 iterations, 99.7%
assert-dm-clean  64x ppc=00c0 (ISR ring)   4x ppc=2e21 (the loop)
```

Identical to the plain and `EXTENDED_LEC` calls, to within the ~2% run-to-run
variation in loop count. The state trail into the freeze is the same
instruction for instruction.

### What three negatives together are worth

Echo-canceller mode and symbol-rate configuration both reach the DSP, both
resize the bulk delay line, and **neither perturbs the failure in any measurable
way.** That is a much stronger statement than either result alone: the freeze is
insensitive to delay-line sizing, so the whole family of hypotheses that treated
this as a mis-sized or mis-bounded delay line — 114k–l's bound, 115c's oversized
seed, 115d's allocation conflict — is not just individually wrong but wrong in
kind.

PM `0x1930`/`0x1934` write over `DM(0x00A8..0x00A9)` under every configuration
tried. The write target is not configuration-dependent either.

### Next

The remaining untested remedy from Session 114z is the other one it named: stop
driving the firmware pair on this page at all, rather than trying to configure
it into safety. `_service_bulk_lengths()` already declines to yield to the
firmware's published lengths on `0x0261`, but the worker still runs; the
question is whether it can be held entirely, the way
`EICON_V90D_BULK_ADAPTER=0` holds the V90D path for A/Bs.

If the worker can be held and V.34 still freezes, the bulk worker is not the
cause and 114z's chain — which is measured, and which no configuration change
has touched — is a symptom of something upstream of both.

## Session 115j: holding the worker removes the freeze — it is cause, not symptom

PM `0x19c8` is `JUMP $1900` on `0x0261` exactly as it is on `0x026A`, so the
V90D hold applies unchanged. `EICON_V34_BULK_HOLD=1` RTSes it on the V.34 page;
default off, 263 tests pass.

### The hang is gone

| | plain V.34 | worker held |
|---|---|---|
| distinct PCs executed | **59** | **7,464** |
| PM `0x2e1b..0x2e22` | 931–947 M iterations, 99.7% | not in the hot set |
| PM `0x0771` (kernel per-sample dispatch) | **0** | **701,482** |
| highest `TrnProgress` | `0x0064` | **`0x0090`** |
| `assert-dm-clean` writers | `00c0` (ISR), `2e21` (the loop) | `3738` (memset), `14ac` |
| writes from `0x1930`/`0x1934` | yes | **none** |

Seven consecutive calls froze at `0x0064` with fifty-nine instructions
executing. With the worker held, the page executes seven and a half thousand,
the top of the profile is MAC work at PM `0x17aa`/`0x17b5` — a real filter loop
— the kernel per-sample dispatch runs where it previously ran *zero* times, and
the state machine keeps advancing to 51.7 s of a 52 s call instead of stopping
at 5.3 s.

**So the bulk worker is the cause.** Session 114z's chain is causal and complete,
established now by intervention rather than correlation: hold PM `0x1930`/
`0x1934`, and `DM(0x00A8..0x00A9)` is never overwritten, `CALL (I7)` never
resolves to `0x2e1c`, the scan loop never runs, and the sample clock keeps
feeding a live state machine.

It also retires the doubt raised in 115i. Configuration could not perturb the
failure because configuration was never the variable; the worker running at all
is.

### It is a diagnostic, not a fix

The call still ends `NO CARRIER`. `0x0261` is loaded fourteen times and the
state machine cycles rather than trains — `0x0090` is the high-water mark, not a
connection. That is expected: the hold removes the far-echo bulk delay outright,
and V.34 needs it. What it proves is causality, not a working configuration.

### Next

V90D already has the answer to this shape of problem. `V90D_PORTABLE_BULK`
services the documented near/far delay-line database ABI from
`PortableBulkDelay` while keeping the unsafe native tail jump held — the
firmware worker never runs, and the delay line is provided in the harness
instead. The V.34 page uses the same `DM(0x3FBC)`/`DM(0x3FBD)` length words and
the same `BulkInputX`/`BulkInputY`, `BulkOutpuTX`/`BulkOutputY` database
locations documented in the ADDSP guide, so the same class of adapter should
apply.

That is the first repair path in this whole stretch that is not a guess at a
value: hold the worker, which is now known to be safe and effective, and serve
the ABI the guide specifies — with `--assert-dm-clean` and the PC histogram as
the gate, both of which now have a clean reference run.

## Session 115k: the portable bulk delay runs on V.34, and changes nothing beyond the hold

`EICON_V34_PORTABLE_BULK=1` holds PM `0x19c8` and services the documented
near/far delay-line ABI from `PortableBulkDelay`, exactly as `V90D_PORTABLE_BULK`
does for page 14. `PortableBulkDelay.service()` needed no changes: it reads the
lengths and inputs at `DM(0x3FBC..0x3FBF)` and publishes the output pairs at
`DM(0x3F36..0x3F39)`, all of which the ADDSP guide defines for both pages. The
V.34 branch runs ahead of the page-14 `_bulk_adapter_held` guard, since the two
holds are tracked separately. Default off; 263 tests pass.

It runs, with coherent lengths:

```text
[native-mips] portable V.34 bulk delay active: near=49 far=129 sample pairs
```

### The result is the hold's result, to within noise

| | plain | worker held | portable delay |
|---|---|---|---|
| distinct PCs | 59 | 7,464 | **7,486** |
| instructions | 7.47 G | — | 1.117 G |
| PM `0x0771` | 0 | 701,482 | **711,328** |
| highest `TrnProgress` | `0x0064` | `0x0090` | **`0x0090`** |
| `assert-dm-clean` | `00c0`, `2e21` | `3738`, `14ac` | **`3738`, `14ac`** |

The freeze stays fixed and the assertion stays clean — no writes from
`0x1930`/`0x1934`, none from the scan loop. But serving the delay line adds
nothing measurable over simply holding the worker: the same PC count, the same
state ceiling, the same cycling, the same `NO CARRIER`.

### What that means

`0x0090` is now the ceiling under both configurations, which says the bulk delay
is **not** the remaining constraint. Something else stops the page between
`0x0090` and a trained connection, and it is not the echo canceller — the two
configurations either side of that question produce identical outcomes.

The honest reading is that the portable adapter is correct and inert: it is
wired, active, fed coherent lengths, and publishing output pairs, and the
firmware's behaviour does not depend on those pairs at this stage of training.
Whether the pairs are consumed at all is worth checking directly — `--watch-dm
0x3f36:40` on a call would show whether anything reads them — before assuming
the adapter is doing useful work rather than writing into a void.

### On the default

The default still freezes: 59 PCs, a 941 M-iteration runaway, and low-DM
corruption. The held and portable paths do neither, and reach `0x0090` instead
of `0x0064`. That is a strong argument for flipping the default, and it is
deliberately not flipped here on the strength of one call each — the failure
mode this whole stretch has been correcting is exactly that. Three or four calls
per configuration, with the histogram and assertion as the record, would settle
it.

## Session 115l: three calls each, and the default flips

```text
tag              PCs   PM 0x0771   top loop    maxTrn   assert writers
ab-plain-1        --          0         --        --    (no INVITE arrived)
ab-plain-2      1857         84  927,173,407    0x0064  00c0,1930,1934,2e21
ab-plain-3        59          0  929,585,585    0x0064  00c0,2e21
ab-portable-1   7471    633,866   21,661,675    0x0090  14ac,3738
ab-portable-2   7475     95,227  916,553,077    0x2804  14ac,3738
ab-portable-3   7471    705,779   24,580,224    0x0090  14ac,3738
```

`ab-plain-1` is a telephony miss — the INVITE never reached the endpoint — and
is not a result.

**Plain froze in both valid calls**, at `0x0064`, with the runaway and with
writes from `0x1930`/`0x1934` and the scan loop at `0x2e21`.

**Portable froze in none of three**, and in all three the assertion shows only
`0x3738` (the memset) and `0x14ac` — **zero writes from `0x1930`, `0x1934` or
`0x2e21` in any call.** Distinct PCs go from 59–1857 to ~7470 every time.

### The outlier is worth stating plainly

`ab-portable-2` is not a clean call. Its per-sample dispatch runs at 95,227
against 633,866 and 705,779, its hot path is 916 M executions of PM
`0x3b1e..0x3b23`, and `TrnProgress` oscillates between `0x1408` and `0x2804` —
values outside the normal `0x00xx` range — for the last seconds of the call.

But `0x3b1e..0x3b23` is `CNTR = $0010; DO $3B22 UNTIL NOT CE; ...; RTS`: a
sixteen-iteration multiply/shift subroutine **called** 458 M times, not a loop
that fails to exit. It returns. That is heavy work, not a hang, and it is not
the `0x2e1b` failure — the corruption that causes that never happens in this
configuration. `ab-portable-1` and `ab-portable-3` have their hot path where a
healthy V.34 receiver should, in the MAC filter at PM `0x17aa`/`0x17b5`.

So portable is better in every call and strictly better in the failure that has
blocked this page since Session 76. It is not uniformly healthy yet.

### Default flipped

`EICON_V34_PORTABLE_BULK` now defaults to on; `=0` restores the native worker
for A/Bs, mirroring `EICON_V90D_BULK_ADAPTER`. 263 tests pass.

None of the six calls connected. The blocker this changes is the freeze, not the
connection: `NO CARRIER` after a live, progressing state machine is a different
and more tractable failure than `NO CARRIER` after fifty-nine instructions.

### Next

`0x0090` is the ceiling in the two clean calls and the bulk delay demonstrably
is not what holds it there (115k). The open questions are now what stops the
page at `0x0090`, and what `ab-portable-2` did differently — the state word
leaving the `0x00xx` range is new and has no explanation yet.

## Session 115m: nothing stops it at 0x0090 — that is the top of a timeout walk

`0x0090` is not a terminal state. In the two clean calls it is reached **eleven
and twelve times**, and every time the state falls back:

```text
ab-portable-1   0x0090 -> 0x0020 (3)  -> 0x0022 (2)  -> 0x0024 (6)  -> 0x0090 (2)
ab-portable-3   0x0090 -> 0x0020 (5)  -> 0x0024 (9)  -> 0x0090 (5)
```

`0x0020`–`0x0024` are phase-2/INFO states, so each attempt runs the sequence,
tops out at `0x0090`, and restarts. That is the page cycling seen as fourteen
loads of `0x0261`.

### The walk skips the states that matter

The documented V.34 progression is

```text
0x0070 0x0071 0x0072 0x0074 0x0076 0x0080 0x0082 0x0084 0x0086
0x0090 0x0092 0x0094 0x0096 ...
```

and the live trail is `0x004f -> 0x0070 -> 0x0072 (x3) -> 0x0074 -> 0x0090`.
**`0x0076` and `0x0080..0x0086` never occur.** Those are the states between
`0x0074` and `0x0090`, and they are skipped entirely.

Session 102 already characterised this exact walk, on loopback:

> the answerer's state-`0x0060` block ... has timeout 128, `test0 = PM 0x2e6c`
> (`AR = 0+1; RTS`, a placeholder that never fires), and no self-branch. **It
> leaves on its timer**, which is why the answerer walks
> `0x0071 -> 0x0072 -> 0x0074 -> 0x0090`.

So `0x0074 -> 0x0090` is the answerer advancing on timeouts, not on anything it
received. The card is not stopping at `0x0090`; it is running out the clock
through phase 3 and starting over.

### It is not silence on the wire

RMS per 250 ms from the captures, both directions, `ab-portable-3`:

```text
   t(s)     RX(CX)   TX(card)
     2.0       500       245
    10.0       143       432
    20.0       642       317
    32.0      1283         0
    40.0       318       307
    50.0       752       237
```

Both ends transmit for the whole call, and the RX level cycles between roughly
120 and 1280 every couple of seconds — the CX retrying the handshake, in step
with the state machine's restarts. Session 114b's frozen-carrier finding does
not apply here: with the worker held there is a live TX and a live RX.

**So the peer is sending phase-3 training and the card is not detecting it.**
That is a receiver question, and it is the first time this page has been able to
pose one — until Session 115j it never got far enough to try.

### Next

The states that never occur are the lever. `0x0076` is the first one skipped,
so the block that should publish it, and the test that should fire to leave
`0x0074`, are what to read next — the same `--watch-dm` on the block record and
`--watch-exec` on its resolved test that Sessions 114i–114j used on `0x0064`,
pointed one phase later.

Worth noting what this retires: every V.34 finding from Sessions 76 through 114
was measured on a page that was hung inside a corrupted dispatch. The state
readings in that stretch describe a machine that was barely running, and the
sequence above is the first phase-3 trail taken on a page that is executing
normally.

## Session 115n: there is no 0x0076 block — those are sub-states inside 0x0070

`tools/v34_script.py` decodes the script directly, and its shape matches
everything re-derived this session independently: three-word entries, value to
`0x2137 + field`, terminator `0x19`/`0x24`, roles byte-interleaved with the
calling side reading low bytes at PM `0x2E1A` and the answering side high bytes
at PM `0x2E24`. The live entries in Session 115 — `i4=0x202e mr1=0x0019` and
`i4=0x1ea2 mr1=0x0024` — are those two role decoders.

The state field is **`0x1b`**, not `0x10`; `0x10` is the calling-side numbering
and the answering fields sit `0x0b` higher (branch targets `0x1c..0x1f`, tests
`0x20..0x24`).

### The answering script's states

```text
base 0x1e81, 16 blocks

0x1e81  0x0000     0x1ed5  0x0070     0x1f26  0x00d0
0x1e93  0x0020     0x1eed  0x0080     0x1f44  0x0020
0x1ea8  0x0050     0x1ef9  0x0090     0x1f6b  0x0030
0x1eba  0x0060     0x1f17  0x00a0     0x1f7d  0x0040
                                      0x1f92  0x00df
                                      0x1fa4  0x00e0
```

**There is no `0x0076` block, and no `0x0074`.** The calling script has the same
round set. So the values in the live trail — `0x0071`, `0x0072`, `0x0074` — are
not block states at all; they are sub-states published from *inside* the
`0x0070` block, and `0x0076` would be another one.

Session 115m's framing was therefore wrong: there is no block to read that
"should publish `0x0076`". The question is what advances the sub-state within
block `0x0070`, and why it stops at `0x0074`.

### Block 0x0070, in full

```text
block 0x1ed5
    0x1ed5  field 0x1b = 0x0070      state
    0x1ed8  field 0x0d = 0x0040
    0x1edb  field 0x20 = 0x001c      test 0
    0x1ede  field 0x1c = 0x001e      branch target 0
    0x1ee1  field 0x21 = 0x0012      test 1
    0x1ee4  field 0x22 = 0x0000      test 2
    0x1ee7  field 0x1a = 0x0001
    0x1eea  field 0x24 = 0x0002      test 4 / terminator
```

For comparison the next script state, `0x0080`, is a much smaller block:

```text
block 0x1eed
    0x1eed  field 0x1b = 0x0080
    0x1ef0  field 0x21 = 0x0008
    0x1ef3  field 0x1d = 0x0025
    0x1ef6  field 0x24 = 0x0000
```

and the live trail skips it: `0x0074 -> 0x0090` never enters `0x0080`. Note
`0x0090`'s block carries a gate (`0x0a18`) where `0x0080`'s does not.

### Next

Block `0x1ed5`'s branch targets and tests are indices, resolved through the two
tables PM `0x2d84`/`0x2d89` walk — `0x0676` for branches and `0x064B` for tests,
per Session 114j. Resolving `0x1c = 0x001e` and `0x20/0x21/0x22 = 0x001c/0x0012/
0x0000` against those tables gives the actual routine addresses, and
`--watch-exec` on them says which fires and which never does.

That is the same method 114j used to read block `0x1afa`'s test, pointed at the
block that is actually live now — and unlike that earlier work, it is being read
on a page that is executing normally.

## Session 116: page 14 reaches Phase 4 against a live Courier, and leaves on a ratechange — not on the timer

First live Courier call placed through the AT console rather than a script:
`ATS0=0` set on the terminal before the call existed, `ATD6001` on the Courier,
`ATA` to answer. Capture in `artifacts/interop/courier-v90/call1.*`.

The call reaches **page 14 (V.90 DPCM)** and walks Phase 4 to `0x00d0`:

```text
 3.22   page 6 V.8 -> page 7 INFO
 5.58   page 7 -> page 14 V.90 DPCM
 5.94   0x007a                     dwell ffff, held 2.20s, then released
 8.46   0x0080                     inner dwell counts 0x1382 -> 0x0008
12.26   0x00b0 -> 0x00b1           Rstatus_ch = 0x8200 [change_h|DSR]
14.30   0x00b3 -> 0x00b4 -> 0x00b6
14.40   0x00c0 -> 0x00c2 -> 0x00c4 -> 0x00c8 -> 0x00cc
16.74   0x00d0                     outer dwell ffff, inner dwell ffff
18.96   ratechange                 outer ptr 0x1c44 -> 0x1d2b, mode 147e -> 0000
18.98   0x0024, page 14 -> page 7  retrain
```

This is Session 68 reproduced on fresh hardware: 68 recorded `0x00d0` at 17.06 s
retraining at 19.34 s (2.28 s); this call holds it 16.74 s to 18.96 s (2.22 s).
Received level matches too — 68 measured 2085/2114/2119/2127 rising to 2343, and
the RTP capture here gives RMS 2133/2116/2655 across the three dwell seconds.

Page 14 is loaded **once**. After the retrain the remaining 37 s never returns to
it: nine retrains total, and seven page 8 -> page 7 fallbacks at 21.5, 27.0,
32.7, 38.5, 44.2, 50.0 and 55.8 s — the 5.8 s cycle of 115m, on the V.34 page.

### Host timing is not involved

Through the whole first attempt, the one that reaches `0x00d0`, the media path
is clean: **one** tick over 18 ms, and that is the startup tick at 0 s (23.2 ms).
Zero catch-up deferrals, ratio 1.00x, 0 substituted, 0 dropped. The call totals
of 88 over-budget ticks and 40 deferrals are all accumulated *after* 19 s, by the
repeated page loads of the retrain cycle. Over-budget ticks on this page are a
symptom of retraining, not a cause of it.

### The peer is transmitting, and it is not level

From the RTP capture, independent of any card tap:

```text
 9..11 s   rx rms    31    our DIL, Courier listening (TX constant 924)
12..15 s   rx rms  ~2110   Courier answering our Phase 4 signals
16..18 s   rx rms  2133 / 2116 / 2655    the 0x00d0 dwell
```

The Courier answers continuously and at full level for the entire gate.

### What ends the dwell

Not the countdown. `v90d_global_countdown` runs `0x7fe6 -> 0x643b` across the
dwell, 3191/s, with about **8.0 s still to run** when the state leaves. The exit
is a status assertion at 18.96 s:

```text
18.84  Rstatus_ch=0x8700[change_h|CTS|DSR|DCD]        Rstatus=0x0402[core|energy]
18.96  Rstatus_ch=0x9300[change_h|ratechange|DSR|DCD] Rstatus=0x0482[core|flow_blocked|energy]
18.98  TrnProgress 0x00d0 -> 0x0024, page 14 -> page 7
```

`ratechange` replaces `CTS`, `flow_blocked` appears in `Rstatus`, and in the same
20 ms row the outer script pointer moves `0x1c44 -> 0x1d2b`, the outer tests
`0x0014/0x0004/0x001d/0x0022` all clear, `v90d_outer_mode` drops `0x147e -> 0x0000`
and the inner dwell goes `0xffff -> 0x0007`. One row is not enough to order the
status write against the pointer move, so this is coincidence in time, not yet
causation — but the timer is excluded either way.

So `0x00d0` is a gate the card abandons by declaring a rate change, while the
peer is still sending. `0x007a` one phase earlier is the same shape (dwell
`0xffff`, held 2.20 s) and *does* release forward.

### The detector and eye taps do not read on page 14

Recorded so the next session does not misread it, as this one first did. Across
the whole of page 14's residency:

```text
rx_value        0x0000 throughout, live again (0x1588) at 19.04 on page 7
eye0/eye1/eye2  frozen at 0x38b1, live (0xf0cc/0xf0d2/0xde58) at 18.98 on page 7
detector_bit    constant 0x30db, becomes 0x0001 at 18.98 on page 7
detector_event/word/count/parser   all zero, parser becomes 0x25ab on page 7
dil_flag/dil_count                 constant 0x0000 / 0x000b
```

Every one of these comes alive at the *page switch*, not at any line event. They
are page-7/INFO structures and carry no information while page 14 is resident.
Zeros there are not silence and not a dead detector: 114b's frozen-carrier
reading must not be applied to them on this page. The `v90d_outer_*` and
`v90d_inner_*` group is the one that does track page 14.

### Next

- Resolve the `0x00d0` outer tests `0x0014/0x0004/0x001d/0x0022` and `next0`
  `0x000b -> 0x000f` against the branch/test tables PM `0x2d84`/`0x2d89` walk,
  the way 115n resolved block `0x0070`, and `--watch-exec` the results to see
  which fires at 18.96.
- Find who writes `ratechange` and `flow_blocked` into `Rstatus`. If the card is
  declaring the rate unusable, the downstream rate it settled on (38667 bit/s
  against 31200 upstream) is the thing being rejected, and that is checkable
  against a forced lower rate.
- `0x007a` holds `0xffff` for 2.20 s and releases forward; `0x00d0` holds 2.22 s
  and does not. Whatever releases `0x007a` is the model for what `0x00d0` waits
  on.

### Reproduce

```bash
./run at --capture-prefix artifacts/interop/courier-v90/callNN
```

which resolves to the native tower with the V.42 endpoint, `--preboot`, and the
terminal live from launch. Then, on the terminal, `ATS0=0`; and on the Courier:

```bash
/tmp/eicon-venv/bin/python -u tools/cx_at.py --dev /dev/cu.usbserial-21210 \
    --setup 'AT&F' dial 6001 --wait 120
```

answering with `ATA` when `RING` appears. The Courier reports no result code and
times out; the card publishes `CONNECT V90/NONE/38667:TX/31200:RX` from the raw
fallback in `at_watch` (logged `rate word 0x0000`), which is the emulator's own
claim and is contradicted by the peer. Do not read that CONNECT as interop.

## Session 117: the rate is not what it rejects — a lower ceiling moves the rates and not the gate

116's first Next item was: if the card is declaring `ratechange` at `0x00d0`, the
rate it settled on is the thing being rejected, and that is checkable against a
forced lower rate. It is checkable, and it is wrong.

Three Courier calls, same rig as 116, captures in `artifacts/interop/courier-v90/`:

```text
                    CAI sent            rates           0x00d0
call1  uncapped     tx=0..0             38667/31200     16.74..18.96  held 2.22s
call3  tx<=32000    tx=0..32000         37333/19200     17.18..19.42  held 2.24s
call4  tx<=28000    tx=0..28000         (none)          never reached
```

call3 moves the rates — upstream falls 31200 -> 19200, a 38% cut — and the gate
does not move at all:

```text
                    call1                     call3
held                2.22s                     2.24s
countdown           0x7fe6 -> 0x643b          0x7fc8 -> 0x63c8
                    8.0s still to run         8.0s still to run
outer tests         0014/0004/001d/0022       0014/0004/001d/0022
outer mode          0x147e                    0x147e
dwells              ffff / ffff               ffff / ffff
outer ptr           0x1c44 -> 0x1d2b          0x1c44 -> 0x1d2b
exit                ratechange|flow_blocked   ratechange|flow_blocked
```

Every measured quantity is the same to within a sample. Whatever `0x00d0` waits
on, it is not the rate, and `ratechange` at the exit is not the card rejecting
the speed it chose.

### Lower is worse, not better

call4 at the bottom of the V.90 ladder never reaches `0x00d0`. It stalls at
`0x00b3` at 14.50 s and stays there for the remaining 40 s with page 14 still
resident (5.66 s..55.02 s), one retrain in the whole call, and the outer dwell
frozen at `0x0027` rather than counting. That is Session 68's other open item —
"the `0x00b3` stall, five calls in six, the generator stops" — and it is
rate-dependent: it appears at 28000 and not at 32000 or uncapped.

So the ceiling does change behaviour. It never changes it in the direction of
passing the gate.

### EICON_MODULATION does not reach the card when --at is in use

The first attempt at this experiment was a silent no-op and is kept as `call2` to
document it. `-e EICON_MODULATION=v90,0,,,,32000` produced a call whose CAI went
out as `disabled=0x0000 tx=0..0 rx=0..0` — identical to uncapped — and which
settled on 38667/31200 again, which is why it looked like a clean null result.

`modem_options()` prefers `_MODEM_OPTIONS_OVERRIDE` over `MODULATION`, and
`at_apply_options()` installs that override from the AT parser on every call
whether or not `+IE` was ever issued. So `--at` silently defeats
`EICON_MODULATION`. The v34-live profile is unaffected (it does not set `--at`),
but any profile carrying both is.

The working route with `--at` is the AT layer itself: `AT+IE=v90,0,,,,32000` on
the terminal before the call, which is what call3 and call4 used. Check
`[at] next call: CAI[...]` in the log and confirm `tx=0..<ceiling>` and
`disabled=0xff7f` before believing any rate experiment on this path.

### Next

- `0x00b3` is now reproducible on demand by capping at 28000, on a page that
  stays resident and does not retrain out. That is a much better place to
  `--watch-exec` the generator than waiting for it five calls in six.
- `0x00d0` remains: outer tests `0014/0004/001d/0022` unresolved against the
  PM `0x2d84`/`0x2d89` tables, and the writer of `ratechange`/`flow_blocked`
  unidentified. Both are unchanged by rate, so neither is a rate question.

### Reproduce

```bash
./run at --capture-prefix artifacts/interop/courier-v90/callNN
```

then on the terminal `ATS0=0` and `AT+IE=v90,0,,,,32000` (or `28000`), and from
the Courier:

```bash
/tmp/eicon-venv/bin/python -u tools/cx_at.py --dev /dev/cu.usbserial-21210 \
    --setup 'AT&F' dial 6001 --wait 90
```

answering with `ATA` on `RING`. Between calls the Courier needs `ATH`/`ATZ` and
about ten seconds, or the PBX does not route the next one and no INVITE arrives.

## Session 118: 117's 0x00b3 claim was n=1 — the cap does not decide it, and 0x00d0's dwell is not fixed

117 ended by saying `0x00b3` was "reproducible on demand by capping at 28000".
That was one call. A second call at the same ceiling does something else.

```text
                 cap      0x00b3        0x00d0                    exit
call4   tx<=28000   stalled 14.50s..end (>=40s)   never reached   —
call5   tx<=28000   held 20.84s, released         16.70..25.92s   ratechange
                                                  held 9.22s
```

Same ceiling, same rig, same peer, opposite outcomes. `0x00b3` is not a hard
stop and the cap does not decide whether it releases: it is a long dwell that
sometimes outlasts the call. Session 68's "five of six" is the same variance
seen from the other side, and 117's framing of it as on-demand is withdrawn.

### 0x00d0 does not have a fixed dwell either

```text
        held      countdown at exit        budget used
call1   2.22s     0x643b  (8.0s unused)    2.2s of ~10.2s
call3   2.24s     0x63c8  (8.0s unused)    2.2s of ~10.2s
call5   9.22s     0x0ca9  (1.0s unused)    9.2s of ~10.2s
```

The budget is the same in all three — about `0x7fe0` at 3200/s, 10.2 s — and the
exit is `ratechange|flow_blocked` in all three. What varies is when `ratechange`
arrives: at 2.2 s twice and at 9.2 s once. So it is not the timer (call1/call3
leave with 8 s unused) and 117 already showed it is not the rate. It is an event
that can arrive early or late, and on call5 it arrived just before the timer
would have expired anyway.

### The histogram cannot answer a per-state question yet

`--pc-histogram-from` clears when an *overlay* becomes resident, and page 14 is
resident for the whole call, so the dump covers 52.24 s of everything and not
the state of interest. call5's is 9876 PCs and 3.93e9 instructions.

Counting is still worth something, because a routine that runs once per sample
in one state has a count proportional to that state's duration. At 8 kHz over
page 14's 52.24 s residency:

```text
417,920 samples  whole residency   0x0014/0x0072..0x0075 sit at exactly 417,920
                                   -- the sample ISR, one pass per sample
166,720 samples  0x00b3 (20.84s)   PM 0x2da3..0x2db5 at 172,993
 73,760 samples  0x00d0 (9.22s)    PM 0x290c..0x2911, 0x28e3..0x28e6 at ~75,000
```

The `0x00b3` candidate reads `DM(0x2005)` and tests bits `0x0100` then `0x0200`,
then takes `DM(0x10ED) + DM(0x117E)` and sets `I0 = 0x10F1`:

```text
2da3  AY0 = DM($2005)        2dab  AY0 = DM($2005)
2da4  AX0 = $0100            2dac  AX0 = $0200
2da5  AR = AX0 AND AY0       2dad  AR = AX0 AND AY0
2da6  IF EQ JUMP $2DAB       2dae  IF EQ JUMP $2DB2
                             2db2  AR = DM($10ED)
                             2db3  AY1 = DM($117E)
                             2db4  AR = AR + AY1
                             2db5  I0 = $10F1
```

This is correlation and nothing more: a matching count does not prove the PC ran
*during* `0x00b3`. It is a candidate to point `--watch-exec` at, not a finding.

### Next

- `--pc-histogram-from` takes an overlay; a per-state question needs it to clear
  and dump on a `TrnProgress` value instead. That is the missing tool, and it
  would settle `0x00b3` and `0x00d0` in one call each rather than by inference
  from counts.
- Until then, `--watch-exec 0x2da3` and `0x290c` on a 28000 call, which says
  directly whether they fire in the state their counts suggest.
- Anything measured from a single call on this path should be assumed to be
  variance until a second call agrees. 117 did not do that; this one is the
  cost of it.

### The default CAI is meant to look like that

Noted because 117 could be read the other way. The CAI is a *host* construct:
`putcai()` (`tty_module/isdn.c:1209`) builds it and the driver sends it with the
ASSIGN, so generating one per call is what the real driver does and not an
artefact of this harness.

`build_cai()`'s default — `disabled=0x0000 enabled=0x00 tx=0..0 rx=0..0`, padded
to 26 bytes — is deliberate. The driver grows the descriptor field by field and
stops at the last one the application set, so an unconfigured modem gets six
bytes; `min_length=26` pads back to what `add_b1()` always sends on the CAPI
path, which is byte-for-byte what this project has sent since the CAI was
corrected. So call2's descriptor was not malformed and was not ungenerated: it
was the known-good default, built from default `ModemOptions` because the AT
override supplied them. That is exactly why the no-op was invisible.

## Session 119: the histogram can be gated on TrnProgress

118's first Next item. `--pc-histogram-state 0x00b3` clears the per-PC counters
on entry to that TrnProgress and reads them out on exit, so the dump is that
state's residency and nothing else. A state entered several times contributes
every visit; the header records the visit count and the total samples.

```bash
./run at --capture-prefix artifacts/interop/courier-v90/callNN \
    --pc-histogram artifacts/interop/courier-v90/callNN.pc.tsv \
    --pc-histogram-state 0x00b3
```

It is exclusive with `--pc-histogram-from`: an overlay clear landing inside a
state visit would silently discard part of it, so the two cannot both own the
counters.

**The edges are accurate to one media quantum and not better.** The gate is
polled once per 20 ms tick, so the clear discards the quantum in which entry was
noticed — part of which ran under the previous state — and the read-out includes
one quantum of the state that follows. The bias is deliberately toward
discarding: a PC that genuinely runs in the state recurs across the remaining
quanta, whereas one admitted from a neighbour is indistinguishable from a
result. Against `0x00b3`'s 20.84 s in call5 that is 0.1%; against a visit of two
quanta it is everything, so check the reported duration before reading anything
into a short one.

### It works, and it did not answer the question

call6, same 28000 ceiling, transited `0x00b3` in 40 ms instead of stalling —
the third different outcome from that ceiling in three calls, which is 118's
point again. The dump is therefore a valid 40 ms sample and not a stall:

```text
2324 PCs, 518,970 instructions, TrnProgress 0x00b3 only, 1 visit, 0.040s

3130  25280  4.9%  MR = MR + MX0 * MY0 (SS), MX0 = DM(I1,M1), MY0 = PM(I4,M5)
2cd7  20160  3.9%  MR = MR + MX0 * MY0 (SS), MX0 = DM(I0,M1), MY0 = PM(I4,M5)
0b61   6784  1.3%  MR = MR - MX1 * MY1 (SS), MX0 = DM(I0,M3), MY0 = PM(I5,M5)
02a8    646  0.1%  IDLE
02a9    646  0.1%  AY0 = DM($2E45)
02aa    646  0.1%  AR = DM($2E44)
02ab    646  0.1%  AR = AR - AY0
02ac    646  0.1%  IF EQ JUMP $02A8
```

Worth contrasting with call5's ungated dump over the same page: there the top
PCs were `0x014e..0x0168` at 1.7% each, the sample ISR, because 52 s of
everything drowns any one state. Gated, the ISR is gone and what is left is
filter kernels plus the `0x02a8` IDLE spin on `DM(0x2E44)` against `DM(0x2E45)`.
None of that is the `0x00b3` stall, because this call did not stall.

### Next

- The tool now exists; what is still needed is a call that actually stalls at
  `0x00b3` with the gate armed. Three calls at 28000 gave a 40 s stall, a 20.84 s
  dwell and a 40 ms transit, so this is a matter of repeating until one lands,
  not of configuration.
- `0x02a8`'s spin on `DM(0x2E44)`/`DM(0x2E45)` is a producer/consumer pair worth
  identifying on its own; it is the only idle-wait in the gated window.
- `--pc-histogram-state 0x00d0` is the same experiment on the gate that has been
  reached in every call, and is the cheaper one to land.

## Session 120: the 0x00b3 stall is a runaway loop that eats the whole sample budget

Session 68 left `0x00b3` as "the generator stops; the owner is unknown". With
the gate from 119 armed, call12 held `0x00b3` for 20.64 s and the dump names it.

```text
                       instructions   samples    per sample   (budget 20,000)
call12  stall 20.68s   3,308,873,888   165,440      20,000.4
call11  transit 0.04s        519,796       320       1,624.4
```

**The stalled card burns its entire per-sample instruction allowance and never
reaches IDLE.** A healthy transit uses 1,624 of the 20,000 and runs out to the
`0x02A8` idle wait, which is how a sample ends. During the stall the allowance is
exhausted every sample for 20.64 s, 165,440 samples running, and the harness cuts
each one off at the budget.

The `0x02A8` IDLE is the proof: **646 executions in both dumps, absolutely
constant** — 16,150/s across a 40 ms transit and 31/s across a 20.68 s stall.
It is not entered at all while stalled. 119 flagged that spin as the one idle
wait in the window and worth identifying; it is not the stall, it is the thing
the stall prevents.

### What is running instead

124 PCs execute only in the stall. Thirty-six of them sit at exactly 43,136,076
executions — 2,085,884/s, about 261 iterations per sample:

```text
014e  I0 = DM($2F29)              0555  I1 = DM($2F2B)
014f  AR  = DM(I0,M1)             0556  modify address register
0150  AX1 = DM(I0,M1)             0557  AR = SE
0151  SR1 = DM(I0,M1)             0558  AY0 = DM(I1,M1)
0152  AX0 = DM(I0,M1)             0559  AR = AR + 0, SI = DM(I1,M1)
0153  AF = 0 + 1, AY0 = DM(I0,M1) 055a  SR = NORM SI (LO), SI = DM(I1,M1)
0154  AR = $3FFF                  055b  DM(I1,M2) = AX0, SR = NORM SI (HI, OR)
0155  AR = AR AND AY0             055c  DM(I1,M2) = SR1
0156  AR = AR - AY1, SR1 = AY0    055d  DM(I1,M2) = SR0, AR = AY0 - AR
0157  IF AC JUMP $0161            055e  M3 = 4
...                              055f  DM(I1,M3) = AR
0161  AY1 = DM(I0,M1)             0560  I4 = AX0
0162  AR = AY1 + 1, SR1 = AR      0561  JUMP (I4)
0167  AR = AX1 - AF
0168  IF EQ JUMP $0186
0186  DM(I0,M2) = AX0
0187  DM(I0,M0) = $4000
0188  RTS
```

and `0x035f..0x0364` runs at exactly half that rate, 21,568,039, so it is taken
every other iteration.

Two pointers drive it: `DM(0x2F29)` for the `0x014e` walk and `DM(0x2F2B)` for
the `0x0555` block. The first reads five words forward, masks with `0x3FFF`,
increments an index and compares it against a bound in AX1, writing `0x4000`
back on match — a ring walk with a 14-bit wrap. The second normalises a pair of
values and leaves through `I4 = AX0; JUMP (I4)`, an indirect dispatch.

### And the generator really does stop

The MAC kernels that dominate a healthy transit are not merely slower, they are
gone:

```text
        transit        stall
3130    632,000/s      1,222/s     x517 down
2cd7    504,000/s        975/s     x517
0b61    169,600/s        328/s     x517
314a    163,200/s        316/s     x516
```

All four fall by the same factor, which is what a foreground that no longer gets
scheduled looks like rather than a filter running badly. Session 68's "generator
stops" is this: the transmit path is not being reached because the sample never
gets that far.

### Caveats

The budget is a harness parameter (`adsp_budget`, default 20,000), so hitting it
exactly means the loop was cut off by us, and this dump cannot say whether it
would terminate on its own. What it does say is that it does not reach IDLE.
Worth noting for scale: a 33 MHz ADSP-2181 has about 4,125 cycles per 8 kHz
sample, so this loop is already several times a real sample's budget — 127-130
flag the page-8 figure as still under investigation and the same question
applies here.

`0x00b3` is entered and left normally in the same call — call12 has two visits,
20.640 s and 0.040 s — so this is one state behaving two ways, not two states.

### Next

- `DM(0x2F29)` and `DM(0x2F2B)`: dump both across the entry into `0x00b3` with
  `--watch-dm`. A ring walk that never terminates usually has a bound that is
  wrong, and `0x0167`'s `AR = AX1 - AF` against `0x0168`'s exit is where to look.
- `0x0561`'s `JUMP (I4)` with `I4 = AX0` is a computed dispatch; the reachable
  targets say what the loop thinks it is doing.
- The transit dump is the control for all of this and already exists twice
  (call6, call11, agreeing to 3%), so any candidate can be tested by difference
  rather than by another call.

### Reproduce

`tools/` scratch harness aside, the call is:

```bash
./run at --capture-prefix artifacts/interop/courier-v90/callNN \
    --pc-histogram artifacts/interop/courier-v90/callNN.pc.tsv \
    --pc-histogram-state 0x00b3
```

with `ATS0=0` then `ATA` on the terminal, dialled from the Courier. Roughly one
call in three stalls long enough to be useful; call12 was the first of the batch.

## Session 121: the pointers are constants, and 0x00b3 has a second, harder failure

120's Next asked for `DM(0x2F29)` and `DM(0x2F2B)` across the entry into
`0x00b3`, on the theory that a ring walk that never terminates has a bound that
is wrong. Both are answered, and the second one negatively.

### The pointers never vary

`--watch-dm-writes 0x2F29:40,0x2F2B:40` (new; see below):

```text
dm w 2f29=2f0e ppc=02b0 pc=02b1  i0=2f29 i4=2e57 ...
dm w 2f2b=2f4e ppc=02b2 pc=02b3  i0=2f2b i4=2e57 ...
```

Every write, without exception: `0x2F29 = 0x2F0E` and `0x2F2B = 0x2F4E`, from
one writer each, `PM 0x02b1` and `PM 0x02b3`. The prior-PC trail puts them
immediately after the `0x02a8` idle block — `... 00c8 00c9 02a9 02aa 02ab 02ac
02ad 02ae 02af 02b0 02b1 02b2` — so this is per-sample re-initialisation of two
scratch pointers to fixed addresses, about twenty times a sample each. They are
not a ring bound and they are not the variable. **The 120 hypothesis is dead.**

Caveat on the method: a limit of 40 was exhausted inside two samples, so every
logged write is from cycle ~33.05M, nowhere near the 14.44 s entry. What settles
it independently is the gated histogram below — `0x02b1` and `0x02b3` do not
appear among the PCs executed during the stall at all, so the writer is not
running then, and nothing is moving those pointers while the card is stuck.

### call13 is not call12

call13 stalled at `0x00b3` for 42.20 s and never left; the call ended in it. The
gated dump is **59 PCs**, against call12's 2514:

```text
1317   6,746,916,475   99.74%   JUMP (I4)
0014 .. 00c9  337,600 each      the sample ISR, one pass per sample
00ca .. 00d0    5,275 each      secondary ISR path: TOPPCSTACK, DM(0x2E46)++
0586/0589/058a  337,600         CALL from 0x00b5
```

337,600 passes over 42.20 s is exactly 8000/s, so **the sample interrupt is
still being serviced every sample** — the card is not dead. The foreground is
one instruction: `PM 0x1317`, `JUMP (I4)`, 6.75 billion times. For a single
instruction to loop, `I4` must hold `0x1317` — and `PM[0x1317]` is itself a
`JUMP (I4)`, so once the dispatch lands there nothing changes `I4` and it jumps
to itself forever. `0x1317` is below `0x2000`, i.e. root PM, resident on every
page.

Both stalls exhaust the budget — call13 runs 20,036 instructions per sample
against 20,000, call12 20,000.4 — so 120's budget finding holds. What does not
hold is that they are the same failure. call12's is a 2514-PC ring walk that
*released* after 20.64 s and went on to `0x00d0`; call13's is a one-instruction
self-jump that never released. `0x00b3` is where two different things end up,
which is the simplest explanation yet for why its duration ranges from 40 ms to
the rest of the call.

### 120's dispatch is the obvious suspect

120 found the loop leaving through `0x0560 I4 = AX0; 0x0561 JUMP (I4)`. A
computed dispatch whose target is taken from `AX0` landing on an address that is
itself `JUMP (I4)` is exactly the shape of this hang. Not proven here: nothing in
call13's dump shows the arrival, because the counters were cleared on entry to
`0x00b3` and by then it was already spinning.

### Next

- `--watch-exec 0x1317:1`. The core prints a prior-PC ring with each event
  (`[WATCH] prior pcs: ...`), so a single hit names the instruction that
  dispatched there and the trail before it. That is the whole question in one
  call.
- `DM(0x2E46)` and the `0x00ca..0x00d0` path, taken 5,275 times in 337,600
  (1.6%): it reads `TOPPCSTACK`, writes it back and increments a counter. A
  stack anomaly counted once per 64 samples during a hang is worth a name.
- `PM 0x1317`'s neighbourhood: if the surrounding words are also `JUMP (I4)`
  this is a dispatch table, and landing anywhere in it hangs the same way.

### New flag

`--watch-dm-writes ADDR[:LIMIT]` logs writes only. `--watch-dm` logs reads and
writes, and its own help warns about addresses a hung loop sweeps: these two are
read 43 million times across a stall (120) and written to the same constants, so
a read-inclusive watch spends its limit inside the loop and never reaches a
write. The core already had `watch_dm_wonly` and `adsp2181_watch_dm_writes`;
only the CLI wiring was missing. Note the limit is per address and these are
written ~20x a sample, so pick it against how long the interesting window is,
not against how many events look readable.

## Session 122: 0x1317 is a chained dispatch vector, and the hang is one slot pointing at itself

`--watch-exec 0x1317:3` caught it on the first call. The arrival is not a wild
jump at all:

```text
[EXEC] pc=1317 from=1316 ret=0ff4 pmovlay=0 dmovlay=0 op=0b000f
       i4=1318 b4=1318 l4=0000 psp=4 ...
```

`from=1316` is a fall-through from the preceding instruction, and `i4=1318` —
so on this pass the jump goes *forward*, to `0x1318`. Disassembling around it
shows why:

```text
1310: AX0 = DM($0000)
1312: MR1 = SR1
1313: RTS
1314: MR1 = M0
1315: RTS
1316: I4 = DM($0ADB)
1317: JUMP (I4)          <-- the hang
1318: I4 = DM($0ADC)
1319: JUMP (I4)
131a: SE = $0001 ...     (a MAC kernel)
```

Pairs of "load `I4` from a vector slot, jump through it" — a chained dispatch
table in root PM. `0x1316`/`0x1317` goes through `DM(0x0ADB)`, `0x1318`/`0x1319`
through `DM(0x0ADC)`. Healthy, `DM(0x0ADB)` holds `0x1318`, which chains to the
next pair: this entry declines and passes the call along.

**The hang is that slot holding `0x1317` instead of `0x1318`.** `0x1317` is the
`JUMP (I4)` itself, and it does not reload `I4`, so the vector jumps to the
instruction that jumped, forever. One off-by-one in a dispatch slot, and 121's
6.75-billion-execution single-instruction loop follows from it.

### The vector is written at run time

Freshly booted, before any call:

```text
DM(0ad6)=2060  DM(0ad7)=1c70  DM(0ad8)=0a00  DM(0ad9)=0b00
DM(0ada)=0e07  DM(0adb)=4010  DM(0adc)=2278  DM(0add)=0000
```

`DM(0x0ADB)` is `0x4010` at boot, not `0x1318` — and `I4` is 14 bits, so that
would dispatch to `0x0010`. The live value seen in call14 was `0x1318`, so
something rewrites this slot during the call. That writer is the whole question
now, and `--watch-dm-writes 0x0ADB` is the experiment: a slot written a few
times and read on every dispatch is exactly what that flag was added for.

This is the same shape as the PM `0x2725` finding — a dispatch table walker
whose table had been overwritten — but in DM and on a page that is running
normally.

### Correction

121 said `0x1317` is never executed in healthy operation. That was inferred from
its absence in every dump then available, and it is too strong: call14 executed
it three times without hanging, on a call whose only stall was the ring-walk
variant. What is true is that it is *rare* — absent from call5's ungated 52 s
dump entirely — which is why a limit of three was enough to catch an arrival
rather than being spent on routine dispatch. The stop condition worked, but the
reasoning behind it was wrong.

### Next

- `--watch-dm-writes 0x0ADB` with a generous limit: who writes the slot, with
  what, and from where. If a healthy call writes `0x1318` and a hanging one
  writes `0x1317`, that names the bug outright.
- The neighbours `DM(0x0AD6..0x0ADC)` are presumably the rest of the chain;
  `0x2060`, `0x1c70`, `0x0a00`, `0x0b00`, `0x0e07`, `0x2278` are all plausible
  PM targets, so the table is longer than the two pairs disassembled here and
  the same off-by-one could land on any of its `JUMP (I4)` instructions.
- Whether `0x0ADB` is written by the ADSP or by the host through the MIPS side:
  the watch's `pc` field answers it, and a host write would show as a different
  writer entirely.

## Session 123: 122's dispatch-vector claim is withdrawn — the read it depends on does not happen

`--watch-dm-writes 0x0ADB`, then a read-and-write watch on the same address,
say the opposite of what 122 concluded.

### What the watch shows

Two calls, `--watch-dm-writes 0x0ADB:500` then `--watch-dm 0x0ADB:30,0x16CB:30`:

```text
dm w 0adb=0000 ppc=3af2 pc=3af2 cyc=112771408
dm w 0adb=0000 ppc=3e9e pc=3e9e cyc=306381076
dm w 0adb=0000 ppc=3e9e pc=3e9e cyc=417406243   ... 15 events, all identical
```

**Every write is `0x0000`**, from two writers, `PM 0x3af2` once and `PM 0x3e9e`
about twice per 105M cycles. Nothing ever writes `0x1318`, and nothing ever
writes `0x1317`.

### And the read 122 assumed does not appear

The core's own decode is unambiguous — `case 0x88` is *"read data memory
(immediate addr) to reg group 2"*, address `(op >> 4) & 0x3fff`, which for PM
`0x1316`'s `0x88adb0` is `0x0ADB`. So `0x1316` should read `DM(0x0ADB)` every
time it runs, and `from=1316` in the `[EXEC]` line confirms it ran immediately
before `0x1317` (`from` is `exec_history[pos-2]`, the previous PC).

It was watched, and it did not read:

```text
0adb events logged : 15   (limit 30, never exhausted)
of which reads     : 0
0x1317 executed at : cyc 112,779,314 / 112,784,471 / 112,788,572
0adb writes either side: 112,771,408 and 306,381,076
```

The watch was armed and unspent across the exact window in which `0x1316` ran,
and logged no read. So one of these is false: that `0x1316` executed, that it
decodes as a read of `0x0ADB`, or that the read watch fires on this path. The
most likely of the three is the decode — **the offline disassembly was taken
from a card booted without a call, and PM at run time need not match it.**
`0x1316` never appears in any gated histogram (it runs outside `0x00b3`), so no
capture carries its runtime opcode to check against.

122's central claim — that `DM(0x0ADB)` is the dispatch slot and the hang is
that slot holding `0x1317` — rests on that read. **It is withdrawn.**

### What still stands

- `PM 0x1317` is `JUMP (I4)`, opcode `0b000f`, confirmed from a live dump
  (call13) and not only from the offline disassembly.
- Healthy, `i4 = b4 = 0x1318` at that instruction, across three calls, so it
  jumps forward and the surrounding code is a chain of `JUMP (I4)` trampolines.
- The hang requires `I4 = 0x1317`, which is what makes it a one-instruction
  loop. That much is arithmetic, not inference.
- `i4` and `b4` being equal every time is worth keeping: on this family the base
  register is loaded alongside the index, which points at the target arriving as
  an immediate rather than being fetched from a table. If so, a corrupted `I4`
  cannot come from a bad table entry at all, and 122 was looking in the wrong
  place twice over.

### A limitation of the DM watches, worth recording

The shim holds DM as `ADSP.adsp2181_dm(cpu)`, a raw array view, and writes
through it directly. Those writes never pass `WWORD_DATA`, so **no host-side DM
write is visible to `--watch-dm` or `--watch-dm-writes`**. Every page load,
every `card.dm[...] = ...` in the harness, is invisible. Any conclusion of the
form "nothing writes this address" means "no *DSP* instruction writes it" and
nothing more.

### Next

- `--watch-exec 0x1316:3`. The `[EXEC]` line carries `op=`, read back live, so
  one call says what that instruction actually is at run time and settles the
  contradiction outright. It should have been the first move rather than
  disassembling an offline boot.
- If the runtime opcode is `0x88adb0` after all, then the read watch does not
  fire on the direct-address path and that is a tooling bug to fix before any
  more DM conclusions are drawn from it.

## Session 124: the vector is DM(0x20A1), and root PM is not what an offline boot says it is

`--watch-exec 0x1316:3` settles 123's contradiction in one call. The `[EXEC]`
line carries the opcode read back live:

```text
[EXEC] pc=1316 from=1315 ret=0ff4 pmovlay=0 dmovlay=0 op=8a0a10 cyc=113896129
[EXEC] pc=1316 from=1315 ret=0ff4 pmovlay=0 dmovlay=0 op=8a0a10 cyc=113901286
[EXEC] pc=1316 from=1315 ret=0ff4 pmovlay=0 dmovlay=0 op=8a0a10 cyc=113905387
```

`op=8a0a10`. The offline boot dump said `88adb0`. **They are different
instructions**, and every conclusion drawn from the offline one was about code
that is not running.

Decoding the live word through the core's own table — `case 0x88..0x8b`, read
data memory at immediate address `(op >> 4) & 0x3fff` into reg group 2:

```text
8a0a10  ->  addr 0x20A1, reg 0        I4 = DM(0x20A1)
88adb0  ->  addr 0x0ADB, reg 0        I4 = DM(0x0ADB)   (offline only)
```

and the registers either side confirm it does exactly that:

```text
pc=1316  i4=1300      <- before
pc=1317  i4=1318      <- after: 0x1316 loaded I4 with 0x1318
```

So **the dispatch vector is `DM(0x20A1)`**, holding `0x1318` when healthy. The
hang is that word holding `0x1317`. `0x20A1` is at or above `0x2000`, so it is
outside the DM overlay window and there is no bank ambiguity about which copy is
meant — one more reason 122's `0x0ADB`, which is inside it, was suspect.

Zero reads of `0x0ADB` were logged in this call either, which is now simply
correct rather than mysterious: nothing reads it on this path.

### Root PM is rewritten at run time

`0x1316` is at `0x1316`, below `0x2000`, i.e. outside the PM overlay window —
which is why 122 assumed a card booted offline would show the same instruction.
It does not. Root PM below `0x2000` is modified during a call, presumably by the
page loads that report their host writes in the log.

The rule this establishes: **do not disassemble root PM from an offline boot and
reason about a live call from it.** The `[EXEC]` watch reports `op=` read back at
execution time and is the only authority. 122 and 123 between them cost four
calls to learn that, and 123's guess at the cause was right.

### Next

- `--watch-dm-writes 0x20A1` with a generous limit, and a read watch alongside:
  who writes the vector, with what, and from where. The same question as 122
  asked, pointed at the address that is actually read.
- Remember the host-write blind spot from 123: the shim writes DM through a raw
  array view, so a `0x20A1` write from the MIPS side would not appear. If the
  DSP is never seen writing it, that is where to look next rather than a
  conclusion.
- `0x20A1` sits in the same region as the block-loader records this log has been
  reading for several sessions; whether it is inside one of them is worth a
  glance before assuming it is a standalone word.

## Session 125: DM(0x20A1) is a mutable handler pointer with three writers

`--watch-dm-writes 0x20A1:500`, one call, three writes in the whole call:

```text
cyc 112,675,900   20a1=0000   pc=0d91
cyc 112,679,770   20a1=1318   pc=105c
cyc 112,702,258   20a1=1325   pc=13a7
```

and the three dispatches through it fall between the second and third:

```text
cyc 112,690,499 / 112,695,656 / 112,699,757   pc=1316, op=8a0a10, I4 <- 0x1318
```

which is why every observed dispatch had `i4=1318`. The vector is not a static
table entry at all — it is a **handler pointer the firmware rewrites as it moves
between states**, cleared to `0x0000` by `PM 0x0d91`, set to `0x1318` by
`PM 0x105c`, and moved on to `0x1325` by `PM 0x13a7`. All three values are
plausible PM targets in the `JUMP (I4)` chain at `0x1316..0x1325`.

That reframes the hang. `0x1317` is not a corrupted table entry and not a stale
index: it is a *handler address one lower than the real one*, written into a
pointer that is legitimately rewritten several times a call. `0x1318` is the
healthy value from `PM 0x105c`; `0x1317` is its own `JUMP (I4)`. An off-by-one
at a writer, or a writer computing a target that lands one short, produces
121's 6.75-billion-execution loop directly.

### Next

The experiment is now well posed and needs only a hanging call with this watch
armed: **which writer stores `0x1317`, and what is in its registers when it
does.** `dm w 20a1=1317` is the stop condition. The `0x1317` variant has shown
up once in about thirteen calls, so this is a long hunt rather than a hard one.

`PM 0x105c` is the site to read first regardless — it is the one that writes the
healthy `0x1318`, so whatever computes that value is the natural place for an
off-by-one to live, and it can be disassembled from a live `--watch-exec` rather
than an offline boot (124).

## Session 126: the handler pointers are literals, so 0x1317 cannot be a miscalculation

`--watch-exec` across `0x1055..0x105d`, opcodes read back live (124):

```text
1055: 233e0f  AR = 0 - SR0
1056: 9203da  DM($203D) = AR
1057: 9203e6  DM($203E) = MY0
1058: 414060  AX0 = $1406
1059: 920a20  DM($20A2) = AX0
105a: 413180  AX0 = $1318
105b: 920a10  DM($20A1) = AX0     <-- the write 125 traced
105c: 413ab0  AX0 = $13AB
105d: 920a30  DM($20A3) = AX0
```

This is not a computation. It is three **immediate constants stored into a
three-entry handler table**:

```text
DM(0x20A1) = 0x1318
DM(0x20A2) = 0x1406
DM(0x20A3) = 0x13AB
```

`0x1318` is a literal in the instruction word — `413180`, immediate
`(op >> 4) & 0x3fff`. There is no arithmetic to be off by one, so 125's guess
that a writer computes a target and lands one short is wrong for this site.

### Which leaves two possibilities

Either some **other** writer stores `0x1317` — `PM 0x0d91` and `PM 0x13a6` are
the two seen so far, writing `0x0000` and `0x1325`, and there may be more — or
**the instruction itself differs in a hanging call**:

```text
healthy   413180   AX0 = $1318
hang      413170   AX0 = $1317
```

one word, one nibble. And that is not idle speculation here: 124 established
that root PM below `0x2000` *is* rewritten during a call, which is precisely why
an offline disassembly of `0x1316` was the wrong instruction. `0x105a` is in the
same region and subject to the same rewriting.

### Next

- A hanging call with `--watch-exec 0x105a:3` armed alongside
  `--watch-dm-writes 0x20A1:500`. Between them they distinguish the two cases in
  one capture: if `op=413170` the instruction was patched, and if the opcode is
  intact but `20a1=1317` appears from some other `pc`, that `pc` is the culprit.
- Either way the stop condition stays `dm w 20a1=1317`, and the `0x1317` variant
  is about one call in thirteen.
- `DM(0x20A2)=0x1406` and `DM(0x20A3)=0x13AB` are the other two entries of the
  same table and are written by the same block; if root PM patching is the
  mechanism, they are equally exposed and worth watching together.

## Session 127: the 026a overlay supplies this code, and 0x1317 is in no shipped image

Offline, no call needed, and it settles two things while the hunt runs.

### Page 14's image writes root PM, which is why 124 happened

`artifacts/eicon-dsp/overlays/026a-v.90-dpcm-overlay/pm.bin` contains, at root
PM addresses, exactly the words the live `[EXEC]` watch reported:

```text
1055: 233e0f  AR = 0 - SR0
1058: 414060  AX0 = $1406
1059: 920a20  DM($20A2) = AX0
105a: 413180  AX0 = $1318
105b: 920a10  DM($20A1) = AX0
105c: 413ab0  AX0 = $13AB
1316: 8a0a10  I4 = DM($20A1)
1317: 0b000f  JUMP (I4)
1318: 8203db  MR0 = DM($203D)
1319: 8203e6  MY0 = DM($203E)
```

Byte for byte what runs. So the mechanism behind 124 is simply that the page-14
overlay image carries segments addressed below `0x2000`: loading it rewrites
root PM. A card booted without ever loading `0x026a` — which is what 122
disassembled — still holds the base image there, and shows different
instructions at the same addresses. That is the whole of the 122/123 confusion,
and it is now explained rather than merely worked around.

### 0x1318 is not a trampoline, and there is no chain

122 described `0x1316..0x1319` as a chain of paired `JUMP (I4)` trampolines.
That was the offline image, where `0x1318` reads `I4 = DM($0ADC)`. In the code
that actually runs, `0x1318` is `MR0 = DM($203D)` — **the handler body itself**.

The real structure is three lines and no chain:

```text
1316  I4 = DM($20A1)      load the selected handler
1317  JUMP (I4)           dispatch
1318  ...                 the default handler, which DM(0x20A1) normally points at
```

`DM(0x20A1)` selects which handler runs; `0x105a`/`0x105b` set it to the default
`0x1318` and `PM 0x13a6` later moves it to `0x1325`. The "chained dispatch
table" language in 122, and repeated in 123-126, is withdrawn along with it.

### The hang value is not shipped anywhere

Scanning all 37 extracted overlay `pm.bin` images for the two encodings,
word-aligned:

```text
413180  AX0 = $1318   1 occurrence   026a-v.90-dpcm-overlay PM 0x105a
413170  AX0 = $1317   0 occurrences
```

`0x413180` exists exactly once in the entire firmware, at the address under
investigation. `0x413170` exists nowhere. So the hang cannot be a shipped
variant of this code being loaded instead — **`0x1317` has to be produced at run
time**, by a write to `DM(0x20A1)`, or by the overlay load placing a corrupted
word, or by something reaching the dispatch with `I4` already wrong.

That is what the hunt's two stop conditions already separate: `dm w 20a1=1317`
for the first, `op=413170` at `0x105a` for the second.

## Session 128: a MAC output loop walks over the handler table

The hunt did not need to find `0x1317`. Its second call found the mechanism that
produces it.

### The writes

`--watch-dm-writes 0x20A1:500,0x20A2:500,0x20A3:500`, call21:

```text
dm w 20a1=0000 pc=0d91          the clear
dm w 20a1=1318 ppc=105b         the legitimate setup (126)
dm w 20a1=1325 ppc=13a6         the legitimate state change (125)

dm w 20a1=765c ppc=3542  i4=20a1 mr0=765c      <-- not legitimate
dm w 20a2=cd28 ppc=3542
dm w 20a3=0dae ppc=3542
dm w 20a1=0eea ppc=3543  i0=20a1 mr0=0eea      <-- not legitimate
dm w 20a2=a03c ppc=3543
dm w 20a3=728e ppc=3543
```

All three slots, twice, from `PM 0x3542` and `PM 0x3543`. In each case the
pointer register named in the store held `0x20A1` and `MR0` held exactly the
value written.

### What those instructions are

From the 026a image, live-verified addresses:

```text
3537: DO $3543 UNTIL NOT CE                      outer loop
3538:   CNTR = AY1
3539:   AR = AY1 - 1
353a:   AR = AR + AY0, AY1 = AR
353b:   I1 = AY0
353c:   I6 = AR
353d:   MR = MX0 * 0 (SS), MX0 = DM(I1,M1)
353e:   MY0 = DM(I6,M6)
353f:   DO $3541 UNTIL NOT CE                    inner MAC loop
3540:     MR = MR + MX0 * MY0 (UU), MX0 = DM(I1,M1)
3541:     MY0 = DM(I6,M6)
3542:   DM(I4,M5) = MR0                          <-- writer A
3543:   DM(I0,M3) = MR0                          <-- writer B
3544: RTS
```

A convolution: an inner multiply-accumulate over two input streams, and the
result `MR0` stored through **two auto-incrementing output pointers, `I4` and
`I0`**. Nothing about it is a dispatch or a table walk. It is a filter writing
its output, and its output pointers have marched as far as `0x20A1`.

**So the handler table sits in the path of a MAC output loop that overruns.**
Whatever the accumulator happens to hold lands in `DM(0x20A1)`. When that value
is `0x1317` the dispatch at `0x1317` jumps to itself and the card spends the
rest of the call in 121's single-instruction loop; when it is anything else the
dispatch goes somewhere else entirely, which is why `0x00b3` has produced a
different failure nearly every time it has been caught — a ring walk, a
self-jump, a `TrnProgress` of `0x95be`, a 42 s stall with 175 PCs.

This is the same family as the bulk-worker corruption of 114-115 and the
overwritten dispatch table behind PM `0x2725`: a wild output pointer sweeping DM
and destroying control structures that happen to lie downstream. It is the first
time the destroyed structure and the destroying instruction have both been named
in one capture.

### Honest limits

- Two of the three hunt calls did not show this. call20 was healthy; call22
  stalled at `0x00b3` for 42.48 s with 175 PCs and only the legitimate
  `0000/1318/1325` writes. So table corruption is not the only route into
  `0x00b3`, or the watch limit missed writes in that call.
- `0x105a` read `op=413180` in every call. The patched-instruction hypothesis
  from 126 is unsupported so far, and 127 already showed `413170` is in no
  shipped image.
- Why the output pointers run that far is not established here. The loop counts
  come from `AY0`/`AY1`/`AR` computed before entry, so an oversized count or a
  wrong base is the obvious next question, and both are readable with
  `--watch-exec 0x3536:5` on the setup.

### Next

- `--watch-exec 0x3536:5,0x3537:5` to capture `CNTR`, `AY0`, `AY1`, `I0`, `I4`
  at loop entry, in a call where the corruption fires. That gives the intended
  extent and the actual base in one line each.
- The buffer the loop is meant to write is whatever `I0`/`I4` are set to before
  `0x3537`; if that is a fixed base, `0x20A1` is a fixed distance past it and
  the overrun length is the count.
- `DM(0x20A1..0x20A3)` being a *handler table* immediately downstream of a
  filter output buffer is worth stating plainly as a firmware layout hazard,
  whatever the trigger turns out to be.

## Session 129: is it us? the addressing is right, but 128 overstated what was measured

Fair question, and it changes the reading of 128.

### The two plausible core defects are implemented

Circular addressing, `2100ops.inc:modified_address()`:

```c
INT32 i = (INT32)adsp->i[ireg] + adsp->m[mreg];
INT32 l = adsp->l[ireg];
if (l != 0) {
    INT32 base = adsp->base[ireg];
    if (i < base) i += l;
    else if (i >= base + l) i -= l;
}
```

That is the documented algorithm, with `L == 0` meaning linear — correct. The
nested `DO UNTIL` machinery has loop and counter stacks, and `wr_cntr()` already
carries a fix citing the User's Manual §3.2.3 about not wasting a count-stack
entry on the first load. Neither is obviously wrong.

### What the pointers actually are when healthy

`--watch-exec 0x3542:6,0x3543:6`:

```text
pc=3542  i4=3520 l4=0000 b4=3520 m5=0001      linear, from 0x3520 upward
pc=3543  i0=0626 l0=0010 b0=0620 m3=3fff      circular, 16 words at 0x0620
```

So writer B is a *circular* buffer of sixteen words at `0x0620` and writer A is
linear from `0x3520`. In normal operation neither is anywhere near `0x20A1`, and
neither walks toward it: `0x0620..0x062F` wraps on itself, and `0x3520` counts
*up*, away.

### Which means 128 was too strong

128 said the handler table "sits in the path of a MAC output loop that
overruns". It does not. In the corrupted call both `I0` and `I4` *already held*
`0x20A1` when the stores executed — they did not arrive there by incrementing
from `0x0620` or `0x3520`. The loop wrote where it was pointed. **The
table overwrite is downstream of whatever set those pointers, not the primary
fault**, and the wording in 128 should be read with that correction.

Note also that once `I0` is outside its buffer the modulo does not rescue it —
`0x20A1 - 1` against base `0x0620`, length `0x10`, subtracts `L` and yields
`0x2090`, still nowhere near the buffer. That is correct hardware behaviour, not
a bug: circular addressing assumes the index starts inside the buffer.

### So is it an emulator bug?

Not ruled out, and the strongest remaining candidate is ours rather than the
core's: **the harness stops the ADSP at a 20,000-instruction budget every
sample and resumes it**, which real hardware never does. Everything carried
across that cut — DAG registers, the loop stack, `CNTR`, the ISR injection at
`0x02A8` — is where a divergence from hardware would appear, and a loop cut
mid-flight is exactly the situation these pointers are in.

### The experiment that would settle it without hardware

`adsp_budget` is a harness parameter. If the corruption is ours, its frequency
should move with the budget; if it is the firmware's, it should not.

- Run batches at `adsp_budget` well above and well below 20,000 and count how
  often `dm w 20a1=` shows a value outside `{0000, 1318, 1325}`.
- The same batches give the `0x00b3` dwell distribution, which has been the
  noisiest thing in this whole sequence and would gain a control.
- If corruption tracks the budget, this is a harness artefact and several
  sessions of firmware conclusions need revisiting. If it does not, the pointer
  setup is a real firmware question and `I0`/`I4`'s writers are next.

That is a cheap batch and it should come before any more reasoning about what
the firmware "intends" here.

## Session 130: the harness fabricates a call every sample, and that is the real divergence

129 blamed the instruction budget. Reading the driver, the budget is the smaller
half of it.

### What actually happens per sample

```c
uint16_t adsp2181_modem_sample(a, active_word, idle_word, cycles_per_pass,
                               continuation, return_pc)
{
    tx = adsp2181_sport0_tdm_frame(a, 0, 0, active_word, idle_word, cycles_per_pass);
    if (a && a->idle) {
        discard_stale_synthetic_returns(a, return_pc);
        pc_stack_push_val(a, return_pc & 0x3fff);
        a->pc = continuation & 0x3fff;
        a->idle = 0;
        a->icount = cycles_per_pass;
        execute(a);
    }
    return tx;
}
```

with `continuation = 0x06C8` on page 14 (`0x02B7` on page 8) and
`return_pc = 0x02A8`, the IDLE. So each sample the harness delivers the SPORT
frame, and then, **if the core is sitting at IDLE, fabricates a CALL to a fixed
foreground address with a manufactured return onto the IDLE instruction.**
`discard_stale_synthetic_returns()` exists to clean up after the fabrication.

Hardware does none of this. There the SPORT interrupt fires on whatever the
foreground was doing, `RTI` resumes it exactly where it was, and the foreground
reaches `IDLE` by itself and waits. There is no fixed re-entry address and no
synthesised return.

### Which reverses 129's emphasis

The budget cut is **not** what runs in normal operation. 120 measured a healthy
sample at 1,624 instructions against a 20,000 budget — the card reaches IDLE on
its own and the cap never engages. The cap only fires once a runaway is already
under way, so it is a consequence of the fault, not a cause.

The fabricated call, by contrast, happens **every sample of every call**. And it
is a live candidate for the exact symptom 128-129 chased: re-entering the
foreground at a fixed address with whatever DAG state the previous sample left
behind is precisely how the filter loop at `0x3537` could run with `I0`/`I4`
already pointing at `0x20A1`, which is what was measured. Hardware would have
arrived there through the code that sets those pointers.

### Running it continuously is feasible

The core already models the hardware path: interrupt generation clears `idle`
(`adsp2181_core.c:319`), so a free-running core woken by a scheduled SPORT
interrupt needs no synthetic call. The shape would be: run until the next sample
boundary — 33 MHz / 8 kHz is about 4,125 cycles — assert the SPORT interrupt,
repeat, and let `IDLE` and `RTI` do their own work.

### But the fabrication is deliberate

The code comment says why it is there: on page 8 the V.34 overlay masks the
SPORT interrupt during Phase 3, and "resuming at 06c8 then runs only the kernel
tail and never invokes Core8kRoutine, leaving the answer modem silent at
TrnProgress 0x52". So it was added to fix a real failure, and removing it may
reintroduce that. It is a workaround, not an oversight.

### Proposed

Implement continuous execution **behind a flag**, not as a replacement, so the
two models can be run against each other on the same rig:

- `EICON_CONTINUOUS=1`: free-run the core, assert SPORT on a cycle schedule, no
  synthetic call, no per-sample re-entry.
- The A/B is direct: does the card still reach page 14 and `0x00d0`, and does the
  `DM(0x20A1)` corruption still appear? If the corruption is absent under
  continuous execution and present under the fabricated call, it is ours, and
  120-128 need re-reading.
- Keep the existing model as the default until that comparison exists, because
  every archived capture and every timeline in this log was taken under it.

## Session 131: an inventory of what the harness patches, and a second instance of a known hazard

The feasibility hacks are still in place and several are live by default. Listed
so they can be argued about individually rather than rediscovered one at a time.

### Runtime PM patches

```text
PM 0x00B5   rewritten to jump to 0x0586 and restored in a finally,
            EVERY SAMPLE (eicon_mips_shim.py:3825/3881, also 1230/1236)
PM 0x19C8   overwritten with RTS to hold the bulk worker.
            Page 14: on by default (EICON_V90D_BULK_ADAPTER=0 to disable)
            Page 8:  under V34_BULK_HOLD / V34_PORTABLE_BULK, on by default
PM 0x3A36   NOPed to 0x000000 to keep the originate dial-page pin alive
PM 0x06CD   saved and restored around the per-frame clear
```

plus five TIKRNL stores suppressed at PM `06d7/0739/073b/073f/0747` when the
host owns the synchronous TX mailbox, and the fabricated per-sample call of 130.

The one that runs unconditionally on every sample of every call is the `0x00B5`
ISR-vector rewrite. The one that is live on the page this log has spent the last
ten sessions on is the `0x19C8` hold.

### The hazard is already documented — we found a second instance

`_service_bulk_adapter()`'s docstring, from Session 88:

> The adapter's frame loop at PM `0x26b7..0x26d7` stores through I0 from PM
> `0x26b1`'s `I0 = 0x1DD0`, and **I0 has no L register — it is linear by design,
> bounded only by the loop count** the routine reads from `DM(0x1E4F)`... At page
> load that routine has not run, so `DM(0x1E4F)` holds whatever the previous page
> left — `0x6613`, or 26131 iterations... **I0 walks `0x1DD0` upward across
> `DM(0x1FF7)`, which is the outer state word, and writes `0x78f8` over it.**

That is structurally identical to 128's finding: a linear pointer with no `L`,
bounded only by a count that can be stale, walking over a control word. Session
88 found it writing `0x78f8` over the outer state word at `DM(0x1FF7)`; we found
`PM 0x3542/0x3543` writing filter output over the handler pointers at
`DM(0x20A1..0x20A3)`. **Same class, different routine, different victim.**

And the mitigation for the first instance was to RTS the worker out — one of the
patches above. So the fix for hazard #1 is itself a divergence that changes which
code runs and when, which is exactly the sort of thing that leaves a pointer
holding last page's value.

### What this means for 120-129

Those sessions traced a real chain — `0x00b3`, the self-jump at `0x1317`, the
handler pointer, the writers — and every measurement in them stands. What is now
in doubt is the attribution: whether the corrupted pointers are the firmware's
own behaviour on hardware, or a consequence of the harness running a different
routine at a different time than the card would. **129's correction already
narrowed it to "something set the pointers wrong"; 131 says the harness is a
credible candidate for that something.**

### Ranked A/Bs, cheapest first

1. `EICON_V90D_BULK_ADAPTER=0` on page 14 — restores the old bypass. If the
   `DM(0x20A1)` corruption frequency moves, the hold/lift sequencing is
   implicated. One flag, no code.
2. `EICON_CONTINUOUS` per 130 — removes the fabricated per-sample call. Bigger
   change, behind a flag, and the one that most closely restores hardware shape.
3. The `PM 0x00B5` per-sample rewrite has no flag at all. It should get one
   before anything else here is trusted, because it is the only patch that
   touches PM on every single sample and nothing has ever been run without it.

None of these needs the Courier to be re-tuned; all three are the same call with
a different environment.

## Session 132: the per-sample ISR patch is not load-bearing

`EICON_ISR_VECTOR_PATCH=0` now leaves `PM 0x00B5` alone (131, item 3). One live
Courier call with it off:

```text
[native-mips] EICON_ISR_VECTOR_PATCH=0: PM 0x00B5 left alone
- -> 6 V.8 -> 7 INFO -> 14 V.90 DPCM -> 7 INFO -> 8 V.34 -> ...
page 14 loads: 1      deepest TrnProgress: 0x00c2
dm w 20a1: 0000 (0d91), 1318 (105b), 1325 (13a6)   -- the legitimate three
```

The card boots, registers, answers, trains through V.8 and INFO, **reaches page
14**, and walks Phase 3/4 to `0x00c2`. So the patch that has been applied on
every sample of every call in this project's history is not required for the
card to run. That was not obvious beforehand and it is what makes the A/B
possible at all.

Offline, both settings produce identical boots: the same four page loads, the
same host-write counts, the same `connected bearer activated through DIAL (WDB
frames 1+1651)` and the same task attachment.

### What this does not show

`0x00c2` against a patched call's `0x00d0` is **one call against one call**, and
118 is explicit that single calls on this path settle nothing: with the patch on,
calls have ended at `0x00c0`, `0x00c2`, `0x00b3` and `0x00d0`. Nothing should be
read into the difference. Likewise the absence of `DM(0x20A1)` corruption here is
one clean call, and clean calls are the majority either way.

### Next

A batch A/B, same rig, N calls each with the flag on and off, comparing two
things that are countable rather than anecdotal:

- how often `dm w 20a1=` shows a value outside `{0000, 1318, 1325}`
- the distribution of deepest `TrnProgress`, which has never had a control

Given the observed spread, single figures per arm will not separate them; this
wants ten or more calls a side to say anything, which is an hour of dialling and
should be run as one batch rather than piecemeal.

## Session 133: the bulk A/B separates nothing, and gives the first variance baseline

Twelve calls, interleaved, `EICON_V90D_BULK_ADAPTER` alternating.

```text
default (=1)   0x00b3  0x00c2  0x00c2  0x00d0  0x00d0  0x00d0
=0             0x00b3  0x00c0  0x00d0  0x00d0  0x00d0  0x00ea

                       corruption   released   reached page 14
default (=1)           0 / 6        0 / 6      6 / 6
=0                     0 / 6        0 / 6      6 / 6
```

No difference on either measure. The distributions overlap almost exactly; the
single `0x00ea` in the `=0` arm is one call and `0x00ea` has not been seen
before or since.

### The release is dead code, confirmed

`released=False` in all twelve, and `bulk adapter released` appears in **0 of 54**
logs across this whole session plus the archived endpoint captures. Its
companion diagnostic, `bulk adapter remains held`, has never printed either. So
`_service_bulk_adapter()`'s lift has never once fired on this path, and the two
arms differed only in whether `PortableBulkDelay` — the harness's own
reimplementation of the echo bulk delay — was substituted for the held worker.

That is worth stating on its own: **the default does not merely disable the
firmware's worker, it runs a Python reimplementation in its place**, and the
"hold until the rate is published" logic the code describes has never executed.

### My base-rate estimate was wrong

I put the `DM(0x20A1)` corruption at "about one call in three" when proposing
this batch. That came from one corrupted call in a three-call hunt, which is
n=1 dressed as a rate. Zero in twelve says it is far rarer, and **this batch
therefore has no power to compare corruption frequency at all** — both arms are
zero because the event did not occur, not because the flag prevents it. The
experiment answered a different question than the one it was run for.

### What it does deliver: a control

Twelve calls under near-identical conditions, deepest `TrnProgress`:

```text
0x00b3  xx        0x00c0  x
0x00c2  xx        0x00d0  xxxxxx        0x00ea  x
```

This is the first time the spread has been measured rather than encountered.
Half the calls reach `0x00d0` and the rest scatter from `0x00b3` to `0x00ea`.
Sessions 116-129 read single calls against that distribution and, in 118 and
132, said so; this quantifies what "said so" was worth. Any future claim that a
change moved behaviour needs to clear this spread, and six calls a side does not
clear it.

### Next

- Corruption frequency needs either far more calls or a way to provoke it. The
  latter is worth thinking about first: `PM 0x3542/0x3543` write through `I0`
  and `I4`, and 128's capture had both already holding `0x20A1`, so whatever
  sets them is the thing to watch, not the store.
- The `PortableBulkDelay` substitution now looks like the more interesting of
  131's items, precisely because the release logic around it is dead: the card
  has never once run its own bulk worker on page 14 in any capture here.

## Session 134: V.90A is not missing from the PRI firmware — only from its file set

`docs/bri_target.md` concluded that `EICON_MODULATION=v90a` "cannot work on the
PRI image no matter what the IDI layer sends", because card type 23 maps to
combifile file set 5 and file set 5 has no V.90 APCM overlay. The premise is
right and the conclusion does not follow. The overlay is missing from the
*shipping download set*, and the download set is not the firmware — it is a
table this harness builds and stages itself.

### The gate, in te_dmlt.pm

`0x80091f78`, inside the per-channel modem configuration builder (the routine
that traces `CFG1`/`CFG2` at `0x800ee504`/`0x800ee568`):

```text
80091f78  lbu   $v0, 0x6a($s7)      # CAI-derived enable byte
80091f7c  li    $t4, 0x2000
80091f80  andi  $v0, $v0, 0x4       # DSP_CAI_MODEM_ENABLE_V90A
80091f84  beqz  $v0, 0x80092030     # not asked for -> skip entirely
...
80091fa0  li    $t0, 0x26b          # V.90 APCM
80091fa4  lw    $a1, 0x4($t4)       # staged download table
80091fb0  ...                       # linear scan, stride 0x30, id at +0
80091fc0  beq   $v1, $t0, 0x80091fd8
...
80092004  ori   $s0, $s0, 0x8000    # found: capability bit set
80092008  ori   $t4, $t4, 0x8000
...
80092014  ...                       # not found:
80092020  lui   $a0, 0x800f
80092024  addiu $a0, $a0, -6836     # 0x800ee54c "[%d,%d] V.90A not supported"
```

Two independent conditions, and this harness controls both: bit `0x04` of the
CAI enable byte (`DSP_CAI_MODEM_ENABLE_V90A`, which `select_modulation('v90a')`
already sets) and the presence of download `0x026b` in the staged table.

### Staging a download the file set omits

`eicon_dsp_stage.build_dsp_code_image(..., extra_download_ids=...)` appends
downloads the card type's file set does not select;
`EICON_DSP_EXTRA_DOWNLOADS=0x026b` reaches it from every harness, and
`EICON_HOOK_CALL` reaches `--hook-call`'s instrument from the paths that build
their own shim.

An id does not name a record — the combifile ships `0x026b` twice, as
"V.90 APCM Overlay" (file sets 9-17, 20) and "V90.ANA APCM Overlay" (18, 19) —
so the variant is resolved against the file sets sharing this one's `0x0258`
task kernel. File set 5 runs TIKRNL81.F34 and so do 9-12 and 15, which is
exactly why the 4BRI's APCM overlay is the same kind of object as the DPCM
overlay the PRI already runs, and why the `.ANA` one is not. Ambiguity or an
empty result is an error rather than a default. Twelve tests in
`tests/test_eicon_dsp_stage.py`, including that the staged image is byte-identical
to the old one when no extras are named and that appending moves nothing.

### The A/B, on the native call path

`v90_dpcm_replay.py` on run34, hooking the gate and both outcomes:

| `EICON_MODULATION` | `0x026b` staged | gate `0x80091f78` | outcome |
|---|---|---|---|
| `v90a` | no | reached | `0x80092014` — **V.90A not supported** |
| `v90a` | yes | reached | `0x80092004` — **found**, `$v0=0x80339304` |
| `v90a,1,,56000,,33600` | yes | reached | `0x80092004` — found |
| `v90` | yes | reached | neither: the `0x04` bit gates the search |
| (default) | yes | reached | neither |

Three variables, all behaving as the disassembly says. The search argument
count `$a2` goes `0x40 -> 0x41` with the extra download, which is the staged
table being searched and not some other one.

**So the PRI firmware admits V.90A.** The bri_target.md verdict is corrected in
place: a 4BRI re-target is not a precondition for V.90A, and whatever else
`.2q0` is worth, it is not worth it for this.

### What this does not yet show

The capability bit is set at CAI time, before any line signal. Nothing here
says the card *offers* V.90A in V.8, that it loads `0x026b` onto the DSP, or
that it trains as the analogue side. In order:

1. does the assignment stream at host data port `0x6802` change? Session 94
   established that is where the CAI's modulation bits land (`3f00 1fb1 d200`),
   so a capability word moving under `v90a` is the next cheap measurement;
2. does the DSP ever go resident on `0x026b`?
3. the closed-loop target: `eicon_loopback.py` with one endpoint `v90d` and one
   `v90a`. That is the first configuration in this project where both ends of a
   V.90 link are the card's own firmware, and unlike the analogue peers it does
   not depend on a Courier being on the desk.

Note that replay is open loop and cannot answer (1) as a negotiation question —
Session 82's warning applies unchanged. It can answer it as a "what does the
card write to its DSP" question, which is what Session 94 used it for.

## Session 135: the V.90A bit reaches the DSP, and then V.90A queues behind V.34

Session 134 stopped at the capability bit being set inside the MIPS. Two
questions after it: does that reach the DSP, and does a two-sided V.90 call
now do anything.

### One word in 51,967

Session 94's method — diff the host writes — on the native path, boot and
call, no media:

| configuration | total host writes | 0x6802 stream |
|---|---|---|
| default | 51,967 | 48 words |
| `v90a`, no overlay staged | 51,967 | **byte-identical to default** |
| `v90a` + `EICON_DSP_EXTRA_DOWNLOADS=0x026b` | 51,967 | one word differs |

The whole difference across the run is a single write, word 39 of the
assignment stream:

```text
...  47ff e402 013e 0000  4760  ee02 013e 0000  7920 ...    default / v90a alone
...  47ff e402 013e 0000  47e4  ee02 013e 0000  7920 ...    v90a + 0x026b staged
```

`0x60 -> 0xe4` sets bits 7 and 2. Nothing else moves — not the write database,
not the length word at `0x6800` (`0x0061` in all three), not the download
count. So the V.90A capability does reach the DSP, and it reaches it **only**
when the overlay is staged: the CAI bit on its own is invisible below the MIPS,
which is the same shape as the "V.90A not supported" branch and confirms the
capability word is written from `$s0` after `ori $s0, 0x8000`.

What the two bits mean is not established. `0x04` matching
`DSP_CAI_MODEM_ENABLE_V90A` is suggestive and may be coincidence: this is a
DSP-side descriptor, not the CAI.

### Two sides of a V.90 link, for the first time

`eicon_loopback.py` gained `--answerer-modulation` / `--caller-modulation`;
the old `--modulation` still sets both. The V.90A end gets
`EICON_DSP_EXTRA_DOWNLOADS=0x026b` added to its environment automatically,
because the run without it is not a weaker test but a different one — that end
would negotiate as though V.90A had never been named. The module docstring's
"V.90 is not reachable this way and never will be" is withdrawn.

```bash
tools/eicon_loopback.py --native-mips \
    --answerer-modulation v90 --caller-modulation v90a \
    --native-bearer-activation --force-info-after-v8 --trace-v90d-state \
    --seconds 90 --capture-dir artifacts/loopback-v90a/run01
```

The gate fires as it should inside the rig: the caller takes `0x80092004`
(found), the answerer — `v90`, no overlay — never enters the search.

**And then nothing V.90 happens.** Both ends walk V.8 -> INFO -> V.34 and then
cycle between pages 7 and 8. Neither end ever requests page 13 or 14, and
overlay `0x026a`/`0x026b` is never loaded by either.

```text
answerer (v90)   deepest 0x0090, the answering trail of 115m; parks at 0x002e
caller   (v90a)  deepest 0x0060; stops at 0x0041 on the INFO page
```

That is the V.34 blocker, not a V.90A one: `0x0090` reached and falling back is
exactly Session 115m's "cycling, not freezing". V.90 selection happens *after*
V.34 phase 2 completes, so V.90A cannot be exercised until the two emulated
ends get through phase 2 against each other.

(This paragraph first said the caller "falls out to page 3 (FSK) at 25.6 s" and
ended "oscillating 0x1408 / 0x2804". Both are withdrawn: Session 136 shows
neither the page nor the state ever changed. What happens at 25.6 s is that the
caller's state machine stops, after which the INFO overlay's FFT bit-reversal
buffer owns the DM the harness was reading those two numbers out of.)

### Correction: the two ends do not diverge, and pacing is not the blocker

An earlier version of this entry said the caller advanced 78.0 s of emulated
time against the answerer's 27.0 s and concluded that a 2.9x spread made any
loopback conclusion unsafe. **That is wrong, and it was a misreading of the
logs rather than a measurement.** 27.0 s is the answerer's last *TrnProgress
change*; it then sat at `0x002e` and kept being clocked. The per-sample
captures settle it — both `answerer.adsp.csv` and `caller.adsp.csv` end at
**sample 625280, 78.16 s, 3909 rows each**.

The two media clocks are locked together for the whole run:

```text
media  caller wall  answ wall     skew    ratio
     6         6.8        6.8    +0.00    0.88x
    13        15.9       15.9    +0.00    0.82x
    24        29.2       29.2    +0.00    0.82x
    40        45.2       45.2    +0.00    0.88x
    70        75.2       75.2    +0.00    0.93x
```

Worst skew anywhere is 0.10 s, at two checkpoints, and both ends report
`substituted 0, dropped 0` throughout. So there is no relative drift to fix,
and the absolute 0.82-0.93x does not distort a loopback handshake at all: both
ends run slow by the same factor and exchange samples one for one, so in
emulated terms each sees the peer in real time. A shared slow clock would only
matter against a real-time third party.

Session 100's 0.65x figure is also no longer what this rig does; 81's tick
work and 70's pacing defaults are both in the tree since.

The V.34 result above therefore stands on its own, and **pacing does not get
priority over the receiver questions** — the earlier version of this paragraph
put it there on the strength of a number that was never measured.

### Where V.90A stands

Established: the firmware admits it, configures its DSP for it, and the whole
chain is reachable from the harness with one environment variable. Untested:
everything on the line. The next thing that would actually test it is a live
call — an analogue peer that can be a V.90 *server* is not available, but the
card as V.90A against the Courier as V.90 analogue answerer is not the pairing
either. Against the emulated digital side is the right test and it is behind
V.34 and behind loopback pacing.

## Session 136: 0x1408/0x2804 is not a state — the status block is somebody else's buffer

Session 115l left `ab-portable-2` unexplained: `TrnProgress` oscillating between
`0x1408` and `0x2804`, "values outside the normal `0x00xx` range", with 916 M
executions of PM `0x3b1e..0x3b23` as the hot path. Session 135 hit the same
thing in loopback and read it as the caller falling back to FSK. Both readings
are wrong, and they are wrong in the same way.

### It reproduces on demand

`eicon_loopback.py --answerer-modulation v90 --caller-modulation v90a`, caller
side, every run. The wild values start at **the same sample** as the reported
`bootpage 7 INFO -> 3 FSK`, which was the first clue that they are one event
and not two.

### The writer

`--watch-dm-writes` (now forwarded by the loopback rig) on `0x3fc2`, budget
300,000 so it survives past the `0x0060` spam and reaches the interesting
window:

```text
3809  dm w 3fc2=1408 ppc=3b27 pc=3b25
3810  dm w 3fc2=2804 ppc=3b27 pc=3b25
```

and on `0x3fb0`, the "bootpage" word:

```text
 379  dm w 3fb0=0003 ppc=3b27 pc=3b25
   9  dm w 3fb0=0008 ppc=217e pc=217f      <- the real page writers
   9  dm w 3fb0=0007 ppc=2914 pc=2915
```

**One instruction writes both.** And it writes the whole region: sampling five
addresses across the block gives PC `0x3b25` hitting every one of them exactly
**8,456 times**, the same count at each.

```text
8456 3fb1 pc=3b25    8456 3fc0 pc=3b25    8456 3fca pc=3b25
8456 3fb8 pc=3b25    8456 3fc4 pc=3b25
```

### What PM 0x3b24 is

From the INFO overlay (`0x0260`), which is what is resident:

```text
3b24: DO $3B27 UNTIL NOT CE
3b25:   NOP (MAC), MR1 = DM(I0,M0)
3b26:   CALL $3B1D
3b27:   DM(I0,M1) = SR1
3b28: RTS

3b1d: MY0 = $4000
3b1e: CNTR = $0010
3b1f: DO $3B22 UNTIL NOT CE
3b20:   MR = MR1 * MY0 (SS)
3b21:   SR = LSHIFT SR1 (HI) BY 1
3b22:   SR = LSHIFT MR0 (LO, OR) BY 1
3b23: RTS
```

`0x3b1d` is a **16-bit bit reversal**. `MY0 = 0x4000` makes the fractional
multiply an arithmetic halving whose `MR0` holds the bit that fell off, so each
of the sixteen iterations moves the LSB of `MR1` into the LSB of `SR1` and
shifts `SR1` up: `SR1 = bitrev16(MR1)`. It checks out on the values seen —
`bitrev16(0x1028) = 0x1408` and `bitrev16(0x2014) = 0x2804`.

So `0x3b24` is a bit-reversing block copy, and the block it writes into is
`DM(0x3fb0..0x3fca)` — the status region. That is the reordering pass of a
radix-2 FFT, which is exactly what the INFO overlay should be doing: the
capture already carries `info_fft_span`, `info_fft_count` and `info_fft_stride`
columns.

Session 115l's `0x3b1e..0x3b23` hot path is this same helper, called once per
word per pass. It is not "heavy work" in the receiver; it is the FFT running
while nothing else is.

### The timing settles the causal order

Real writers of `0x3fc0` run from cycle 81 M to **663 M**. PC `0x3b25` starts
writing the block at **684 M** and continues to the end of the run at 2.62 G.
The state machine stops first; the buffer takes the region over afterwards.

**So no page change and no state change ever happened.** The card did not fall
back to FSK, `0x0003` is not a page request, and `0x1408`/`0x2804` are not
states. The state machine gave up at `TrnProgress 0x0041` on the INFO page, and
everything after that in the old logs is an FFT scratch buffer being read
through the status block's names.

### The instrument was wrong, and is fixed

`status_block_is_scratch()` in `eicon_adsp_sip.py` gates the page, state and
Rstatus reporting on `DM(0x3fc2)`'s high byte being zero. That is a measured
discriminator, not a guess: across 300,000 consecutive logged writes of that
word, on every page, every legitimate value is a small state number. The
takeover is now reported once and the readings are suspended until the state
machine takes the region back:

```text
[adsp] sample 204960 (25.620s): the status block DM(0x3fb0..0x3fca) has been
taken over as scratch by the bit-reversal copy at PM 0x3b25 (TrnProgress reads
0x2804); page, state and Rstatus reporting suspended -- Session 136
```

A verification run has zero `3 FSK` lines and zero `0x1408`/`0x2804` states,
and the last published state is `0x0041`.

### What this costs and what it buys

It costs two conclusions. Session 135's "the caller falls out to page 3 (FSK)"
is withdrawn, and 115l's "the state word leaving the `0x00xx` range is new and
has no explanation" is answered — there was no state word.

It buys a cleaner statement of the V.34 blocker. The caller does not fall back
and does not go anywhere: it reaches `0x0041` on the INFO page and **stops**,
while the answerer sits at `0x002e`. Two ends both parked in INFO with neither
advancing is a different question from a fallback, and a better-posed one.

## Session 137: they do act on the INFO message — the caller transmits nothing on page 8

The question was why neither loopback end acts on the INFO message. The premise
is wrong: both ends receive it, validate it and act on it, every cycle. The
stall is one step later and it is not symmetric.

### INFO is received, and the receive chain is entirely healthy

`tools/v34_info.py` on both captures, which decodes the wire with none of the
firmware in the path:

| direction | carrier | frames |
|---|---|---|
| caller receives (answerer transmits) | 2400 Hz | 17 bits at 3.099 s, then **38-bit** messages every ~2.29 s |
| answerer receives (caller transmits) | 1200 Hz | 17 bits at 3.091 s, then **77-bit** messages every ~2.29 s |

All CRC-valid. And the firmware agrees with the wire:

```text
caller     10 dm w 1651=0110 (17 bits)    9 dm w 1651=0260 (38 bits)
answerer   10 dm w 1651=0110 (17 bits)   10 dm w 1651=04d0 (77 bits)
```

`DM(0x1651)` is framer A's expected payload length, and each end reconfigures
it to exactly the length the other end is sending, alternating with the 17-bit
short message. Framer A then completes: `DM(0x0686) = 1` **25 times on the
caller and 36 on the answerer**, ten of each from PM `0x357b`, the success site
Session 44's framer work documents.

So the demodulator, both framers, the 16-lane phase search, the CRC and the
length reconfiguration all work, in both directions, repeatedly. Nothing on the
INFO page is stuck. `DM(0x16bd)` sitting at the hunt value `0x3520` in a
snapshot is the framer between frames, not a framer that never locks — that
reading cost this session an hour.

### The real loop, and the firmware's own reason code

Both ends leave INFO for the V.34 page nine times, and abandon it after
180–280 ms every time. The page request comes from PM `0x290c`:

```text
290c: I4 = $3FA7                  ; clear the mapping-frame block
290d..290f: DM(I4,M5) = $0000
2910: AX0 = DM($3FC1)             ; Rstatus
2911: AR = AX0 OR $0100           ;   |= boot_request
2912: DM($3FC1) = AR
2913: AR = DM($2252)              ; the page to boot
2914: DM($3FB0) = AR
```

and `DM(0x2252)` is loaded by one of two neighbouring handlers that also
publish a code:

```text
2d61: AR = $5679                  ; installed at DM(0x0049)
2d62: JUMP $2D64
2d63: AR = $5678                  ; installed at DM(0x0026) and DM(0x0043)
2d64: SR0 = $0007                 ; -> page 7, INFO
2d65: DM($2252) = SR0
2d66: DM($3F8A) = AR              ; the reason, in the read database
```

`DM(0x3F8A)` is inside the captured read-database window, so every abandonment
is on record. **All eighteen — nine per end — publish `0x5678`.** The sibling
code `0x5679` never appears.

```text
caller     5.200s page 8   5.480s page 7  DM(3F8A)=0x5678
           7.580s page 8   7.860s page 7  DM(3F8A)=0x5678      ... x9
answerer   5.200s page 8   5.500s page 7  DM(3F8A)=0x5678      ... x9
```

### Why: the calling side transmits nothing on page 8

Transmit RMS from each end's own `.ulaw`, over the page-8 windows:

```text
window (page 8)      caller TX   answerer TX
  5.20.. 5.48 s            4.9         251.9
  7.58.. 7.86 s            4.9         255.7
  9.94..10.14 s            5.8         253.4
 12.24..12.44 s            5.8         252.1
 21.40..21.60 s            5.8         250.7

INFO windows, for scale
  4.00.. 4.50 s          344.2         125.1
 20.00..20.50 s          323.4         201.7
```

In 20 ms slices the caller's output is **exactly zero** from 5.04 s to 5.48 s —
silent 160 ms *before* the page even loads — and back to 241 at 5.52 s, right
after the return to INFO. It transmits perfectly well on the INFO page.

So the answerer enters phase 3, hears silence for 300 ms, and abandons; the
caller enters phase 3, sends nothing, and abandons too. `0x5678` is what both
of them call it.

### Where this belongs

This is the same shape as Sessions 95–96, where the originate side reached a
page and transmitted nothing because an enable never arrived — there,
GEN_SETUP1 bit 3 routed PM `0x357a` to a continuation gated on `DM(0x046C)` or
`DM(0x0554)`, and `DM(0x0554)` came from a supervisory tone detector a PRI
image never programs. That specific gate was on the dial page and Session 100
got the caller past it; this is a second instance of the class on page 8, and
whether it is the same mechanism is unknown.

### Next

1. What gates the calling side's page-8 transmitter. The generator dispatch
   audit of Session 68 applies — count executions of the generator dispatch
   rather than inferring from block contents.
2. `0x5678` and `0x5679` are worth naming: two reasons to abandon V.34 for INFO,
   one of which has never fired here. The handlers sit at `DM(0x0026)`,
   `DM(0x0043)` and `DM(0x0049)`, which locates them in the script tables
   Sessions 114y–115n were mapping.
3. The answerer's side of this is *not* a receiver defect. Its 300 ms of silence
   is real, so any conclusion drawn from "the card does not detect phase-3
   training" in a loopback is measuring the caller's transmitter, not the
   answerer's receiver.

## Session 138: the page-8 transmitter is gated on DM(0x2140), which the calling side never sets

Session 137 established that the calling side transmits nothing on page 8.
This finds the gate.

### The transmit tail, and why it is not the answer

The published sample is `DM(0x3764)` — the word `DM(0x3FB4)` points at, which
is what the harness puts on the line. One instruction writes it:

```text
1746: AR = DM($3761)          ; transmit credit
1747: AR = AR + 0
1748: IF EQ JUMP $1750        ; no credit -> publish AR, which is 0
1749: AR = AR - $0001
174a: DM($3761) = AR
...
1750: I4 = $3764
1751: DM(I4,M5) = AR
```

Credit is topped up in units of `DM(0x3755)` by the third of three producer
stages at PM `0x1706..0x1716`, each gated on a level comparison. So "publishes
silence" has an exact mechanism: `DM(0x3761) == 0`.

**It is not what happens.** Gating a PC histogram on the caller's page-8 state
(`TrnProgress 0x0060`, six visits, 10,720 samples):

```text
1723  credit top-up          18,717
1749  credit was nonzero    150,700   of 150,754 publishes
1751  publish               150,754
```

The chain runs, the credit is there, and it publishes 150,754 samples. They are
zero. The producer is running dry of *content*, not of credit.

### A harness fault found on the way, which is not the cause either

Those numbers are 14.06 executions per sample. On a run-to-idle page (INFO,
`TrnProgress 0x002c`) the same publisher runs **exactly 1.00 per sample**.

The reason is ours: `V34_CYCLES_PER_SAMPLE` gives overlay `0x0261` a fixed
20,000-instruction budget per 8 kHz sample instead of running to idle, because
page 8 is a continuous foreground. 20,000 per 125 µs is 160 MIPS — an
ADSP-2181 at 33 MHz has 4,125 and a 2185N at 75 MHz has 9,375. The sweep
behaves exactly as a clock model should, and settles nothing:

| `EICON_V34_CYCLES_PER_SAMPLE` | publishes/sample | caller TX RMS on page 8 |
|---|---|---|
| 20000 (default) | 14.06 | 5.5 |
| 4125 (2181 at 33 MHz) | 2.43 | 2.2 |
| 1500 | 0.78 | 1.3 |

Silent at every rate. The budget is still wrong and should be fitted, but it is
not why the caller does not transmit. (It does change behaviour: below the
default the caller stops cycling and sits in `0x0060` for the rest of the run.)

### The diff, which is the answer

Both ends run the same overlay, so the histogram can be diffed directly.
`--pc-histogram-from 0x0261` on both: **399 words of the V.34 overlay execute
on the answerer and never once on the caller.** The hottest is not close:

```text
0x2f8b..0x2f9c   28 words   60,397,566 executions   answerer only
0x2c7f..0x2ca0   34 words    1,510,586
0x2840..0x2851   18 words    1,035,776
```

`0x2f8b..0x2f9c` is the body of the complex MAC filter at PM `0x2f81`, called
from `0x28e0`, `0x28f5` and `0x2908` over three separate coefficient and state
banks. Its first act is to test a gate:

```text
2f83: AY0 = DM($12FD)
...
2f88: AX0 = DM($2140)
2f89: AR = AX0 AND AY0
2f8a: IF EQ RTS               <- the caller returns here, every time
2f8b: MY1 = $0000
```

And `DM(0x2140)` is the discriminator:

```text
caller     20 writes, every one 0x0000
answerer   20 writes of 0x0000, plus 13x 0x0044, 7x 0x004c, 7x 0x02cc
```

**The calling side never sets it, so the filter returns immediately, 60 million
times' worth of work never happens, and the sample the transmit chain publishes
is zero.**

### Where the value comes from

The script block loader publishes it, and the two ends take different paths
through the loader's two record formats:

```text
2e18: I6 = DM($14A6)
2e19: JUMP (I6)               <- format selected here
2e1a: AY0 = $00FF             ; format A
2e21:   DM(I0,M1) = SR1       ;   the caller's writes land here
2e22:   IF NE JUMP $2E1B
2e24: SE = $FFF8              ; format B
2e2d:   DM(I0,M1) = SR0       ;   the answerer's writes land here
2e2e:   IF NE JUMP $2E25
```

Every caller write of `DM(0x2140)` comes from `0x2e21`, every nonzero answerer
write from `0x2e2d`. This is the same loader Sessions 114y–115l were working
in, where a corrupted dispatch put `CALL (I7)` into `0x2e1c` instead of
`0x2e1a`; here the selector is the indirect jump at `0x2e18` through
`DM(0x14A6)`.

### What is not established

- Whether `0x2f81` is the modulator's shaping filter, the precoder or an echo
  canceller. Three banks and a complex MAC fit all three, and "the transmitter
  is gated on it" is an observation about ordering, not about its function.
- Whether the two record formats are a legitimate calling/answering difference
  or a second instance of the 115j dispatch fault. `DM(0x14A6)` decides, and
  nothing here says what sets it.
- Causation. `DM(0x2140) = 0` is upstream of the silence in the execution
  order; the experiment that would make it the *cause* is to force it nonzero
  on the caller and see whether the line comes alive. There is no knob for that
  yet — the harness has no general "force a DM word" option — and building one
  is the immediate next step.

### Instrument changes

`--pc-histogram` only ever dumped on `[call] ended`, and a loopback run always
ends by SIGTERM, so **the rig had never produced a histogram at all**. It now
dumps from `run()`'s `finally`. The loopback forwards `--pc-histogram`,
`--pc-histogram-state` and `--pc-histogram-from`, writing one file per end,
which is what makes the two-ended diff above a single command.

## Session 139: the force-DM knob, and DM(0x2140) is not the cause

Session 138 ended with the honest caveat that `DM(0x2140) = 0` sits upstream of
the calling side's page-8 silence in execution order, and that turning that into
causation needed a way to write the word. `EICON_FORCE_DM` is that way, and the
answer is **no**.

### The knob

`EICON_FORCE_DM="ADDR=VALUE[@OVERLAY],..."` holds DM words at a value once per
sample, for as long as the named overlay is resident. Per sample rather than
once, because the words worth forcing are exactly the ones the firmware
republishes — `DM(0x2140)` is rewritten by the script block loader on every page
entry, so a single write would be undone before it changed anything. Restricting
to an overlay is the normal case: a DM address means different things on
different pages.

It announces itself twice and loudly — once at construction, once on the first
overwrite:

```text
[force-dm] PATCHED FIRMWARE: DM(0x2140) held at 0xffff while overlay 0x0261 is resident
[force-dm] first overwrite: DM(0x2140) 0x0000 -> 0xffff at sample 43174
```

The second line is not decoration. A force that never overwrites anything is a
null experiment, and a null experiment reads exactly like a negative result.

`eicon_loopback.py` gained `--caller-env` / `--answerer-env`, so the patch
reaches one end and the other stays a control. That is the rig's whole value
here and it had no way to express it before.

### The result

```text
                      page-8 visits   caller TX rms
control                     6              5.5
DM(0x2140)=0x02cc           6              5.0
DM(0x2140)=0xffff           4              4.9
```

Silent at both forced values, including `0xffff`, which opens the gate
whichever bit of `DM(0x12FD)` is set.

**And the gate really did open.** PM `0x2f8b..0x2f9c`, which had never executed
once on the caller in any run, now does:

```text
2f8a  111,354      (the IF EQ RTS, reached in both cases)
2f8b      880
2f91    1,762
2f99   70,464
```

So the filter at `0x2f81` runs on the caller and the line stays silent. It is
not the transmitter's enable. Whatever it is — shaping filter, precoder, echo
canceller — it is downstream of the silence or beside it, not upstream.

### What the patch did explain

Of the 399 words the answerer executed and the caller never did, opening this
one gate unlocked **45**. The other 354 are still answerer-only, and the ranking
has changed:

```text
0x2c63..0x2c69    7 words   1,683,136 answerer executions
0x2c7f..0x2ca0   34 words   1,510,586
0x2840..0x2851   18 words   1,035,776
0x2ce7..0x2cee    8 words     838,602
0x2e24..0x2e2e   11 words     734,434
0x2761..0x2774   20 words     731,171
```

`0x2e24..0x2e2e` in that list is the block loader's **format B** — the record
format the answerer reads `DM(0x2140)` out of and the caller never enters at
all (138). That is now the more interesting half of 138's finding: not the one
word, but that the calling side never takes that branch of the loader, and
`DM(0x2140)` was only the first consequence of it anyone noticed. The selector
is the indirect jump at PM `0x2e18` through `DM(0x14A6)`.

### Where this leaves the question

"What gates the calling side's page-8 transmitter" is still open. Ruled out so
far: the transmit credit chain (138), the page-8 instruction budget (138), and
now `DM(0x2140)` and the filter it gates. Ruled *in* as the next thing to look
at: `DM(0x14A6)` and the loader branch, because a whole record format going
unread is a much larger difference than any single word it would have set.

The knob is the reason this took one run instead of a session, and it applies
unchanged to whatever the next candidate word turns out to be.

## Session 140: it is the role word — the calling script never drives the transmitter

`DM(0x14A6)` turned out not to be a fault at all, and forcing it answered the
question anyway by leading one step further back.

### What DM(0x14A6) actually selects

```text
2d6b: MR0 = $1A2E
2d6c: MR1 = $1EA2          ; script base 0x1EA2
2d6d: AR  = $2E1A          ;   with record format A
2d6e: AX0 = DM($2198)
2d6f: AF = AX0 + 0
2d70: IF NE JUMP $2D7A     ; DM(0x2198) nonzero -> 0x1EA2 / format A
2d71: MR0 = $1A2E
2d72: MR1 = $1E81          ; script base 0x1E81
2d73: AR  = $2E24          ;   with record format B
2d74: JUMP $2D7A
2d7a: DM($14A6) = AR
```

`0x1E81` is the base Session 115n identified as the **answering** script.
So the record format is not a choice about parsing, it is a consequence of
*which script table is being walked*, and the two tables have different record
layouts — which is exactly 115n's "answering fields sit `0x0b` above the
calling ones", seen from the loader's side.

**So Session 139's lead was wrong.** The caller never entering format B is not
a defect; it is the caller correctly declining to walk the answering script.
Measured, unforced: `DM(0x14A6)` is written 20 times on the caller and every
one is `0x2e1a`; on the answerer, 13 are `0x2e1a` and 7 are `0x2e24`.

Forcing `DM(0x14A6) = 0x2e24` on the caller does what that now predicts —
it parses the calling script's records with the answering layout and the run is
incoherent (never a clean page-8 residency, states `0x001c`/`0x0028` appearing
where they never do). Not a result, and it was not going to be one.

### DM(0x2198) is the role, and forcing it makes the caller transmit

```text
caller     7 writes of 0x0000 from PM 0x0d94, 7 of 0x0008 from PM 0x1049
answerer   7 writes of 0x0000 from PM 0x0d94, 7 of 0x0000 from PM 0x1049
```

`0x0008` is GEN_SETUP1 bit 3 — `--modem-role`, the one bit that separates
`0x0484` (answer) from `0x048c` (calling), and the same bit Sessions 95–96
traced into the dial page. PM `0x1049` publishes it into `DM(0x2198)`, and
`0x2d6e` reads it to pick the script.

Holding it at zero on the calling end alone:

| caller | page-8 visits | TX RMS on page 8 | page-8 states reached |
|---|---|---|---|
| control | 6 | **5.5** | `0x0060` |
| `DM(0x2198)=0` | 6 | **248.8** | `0x0060 0x0064 0x0070 0x0072 0x0074 0x0090` |

**The caller transmits at full level and walks the answering trail.** That is
the causal demonstration Session 138 could not make and Session 139 failed to
make: the page-8 transmitter is driven by the script, the answering script
drives it, and the calling script does not.

### What this is not

It is not a fix, and the numbers should not be read as progress. Forcing the
role to zero makes the calling end a *second answerer*; a V.34 link needs one
of each, and unsurprisingly the two ends then both walk to `0x0090` and stop
there, which is the standing ceiling from 115m. Nothing connected.

What it buys is a properly posed question and a named place to ask it. The
calling script at base `0x1EA2` is now the object of study, on the same footing
as 115n's map of the answering script at `0x1E81` — and the specific thing to
find is what its page-8 blocks wait for before enabling transmission, since
Sessions 95–96 found the *dial* page's originate path waiting on a supervisory
tone detector a PRI image never programs. Whether page 8 has an equivalent is
the next question, and it is now a script-table question rather than a search.

### Ruled out, cumulatively

For "what gates the calling side's page-8 transmitter": the transmit credit
chain (138), the page-8 instruction budget (138), `DM(0x2140)` and the filter
at `0x2f81` (139), and the loader record format (140). The answer is the role
word `DM(0x2198)`, and the mechanism is script selection.

## Session 141: the calling script, mapped — and the transmitter's role dependence is not in it

Session 140 left the calling script at base `0x1EA2` as the object of study.
`tools/v34_script.py --role call` already walks it; what was missing was the
map, the resolution of its indices, and a comparison against 115n's answering
script. All three are below, and the conclusion is a negative that moves the
question somewhere else.

### The map

16 blocks, 13 of them carrying a state in field `0x1b`. Branch fields
(`0x1c..0x1f`) are indices resolved through `DM(0x0676) + index`, test fields
(`0x20..0x24`) through `DM(0x064B) + index` — the two walks PM `0x2d94..0x2d9d`
make with `CALL $2E10`. Every value lands at `DM(0x2137 + field)`.

```text
 1ea2  0x0000  20=0000->PM2e6c  21=0000  22=0000  23=0000  24=0004->PM2ef9
 1eb4  0x0020  04=171c  0b=9000  1d=000f->blk1f8c  21=0022->PM2e46  24=001b->PM2e4d
 1ec9  0x0050  04=1618  0b=a000  0d=1000  21=0000  24=0015->PM2edd
 1edb  0x0060  0b=9000  0d=0100  1a=0001  1d=000e->blk1eb4  1e=000e->blk1eb4
                 21=001f->PM2ed0  22=0028->PM2f16  24=0002->PM2e34
 1ef6  0x0070  0d=0040  1a=0001  1c=0010->blk1f1a  20=001c->PM2e57
                 21=0012->PM2ee5  24=0002->PM2e34
 1f0e  0x0080  1d=0021->blk1fb3  21=0008->PM2ea0  24=0000
 1f1a  0x0090  03=0090  04=0218  05=0001  06=0008  0b=8000  0d=0088  24=0002->PM2e34
 1f38  0x00a0  03=0000  05=0008  1a=0028  24=0002->PM2e34
 1f47  0x00d0  03=5000  04=411c  05=0100  06=0112  0b=8000  0d=2000
                 1d=000d->blk1ea2  21=0005->PM2efc  24=0025->PM2f0d
 1f65  0x0020  04=0200  06=0080  24=0026->PM2f10
 1f71   --     03=1000  04=0218  08=0001  1a=0064  24=0002->PM2e34
 1f80   --     03=1008  1d=000e->blk1eb4  21=000b->PM2e6a  24=0000
 1f8c  0x0030  04=1618  0b=a000  0d=0001  21=0000  24=0023->PM2f0a
 1f9e  0x0040  0b=9000  0d=0002  1a=0001  1d=000e->blk1eb4  21=0002->PM2e34  24=0000
 1fb3  0x00df  04=0600  06=0010  1a=0001  21=0000  24=0002->PM2e34
 1fc5  0x00e0  03=0100  24=0000
```

Branch targets resolve to *script blocks*, not routines: `0x000e -> 0x1eb4` is
the calling script's own `0x0020`, `0x0010 -> 0x1f1a` its `0x0090`. So the
observed page-8 loop is written down here — `0x0060`'s two branch fields both
return to `0x0020`, which is exactly the cycling Sessions 135–137 measured.

The field-to-DM map makes Session 138's word fall out for free:
`DM(0x2140) = 0x2137 + 0x09`, i.e. **field `0x09`** — a field the calling script
never sets and the answering one does, which is the same fact from the other
side.

### The two scripts are structurally the same

Diffed block by block against 115n's answering script at `0x1E81`, both have
**the same thirteen states**, and every state's tests and branch structure
match. The complete list of differences:

| | difference | in every state that has the field |
|---|---|---|
| field `0x04` -> `DM(0x213b)` | `0x0200/0x1618/0x0218/0x411c/0x0600` vs `0x0a00/0x1e18/0x0a18/0x491c/0x0e00` | answering has **bit 11** set |
| field `0x0b` -> `DM(0x2142)` | `0x9000/0xa000/0x8000` vs `0xd000/0xe000/0xc000` | answering has **bit 14** set |
| fields `0x1c..0x1e` | e.g. `0x000e` vs `0x001c` | each role's index into *its own* script |

The branch indices differing is not a difference in behaviour — `0x000e` and
`0x001c` resolve to `0x1eb4` and `0x1e93`, which are the two scripts' own
`0x0020` blocks. That leaves exactly two bits of real content.

### Both bits forced, and the caller stays silent

With `EICON_FORCE_DM` on the calling end only (state `0x0060`'s answering
values):

| caller | page-8 TX RMS | page-8 states |
|---|---|---|
| control | 5.5 | `0x0060` |
| `DM(0x2142)=0xd000` (bit 14) | 5.9 | `0x0062` |
| `DM(0x213b)=0x1e18` (bit 11) | 5.3 | `0x0060` |
| both | 5.8 | `0x0062` |
| *role* `DM(0x2198)=0` (140) | **248.8** | `0x0060 0x0064 0x0070 0x0072 0x0074 0x0090` |

Bit 14 does something — the sub-state advances `0x0060 -> 0x0062` — but neither
bit, nor both, transmits. **So the script is not where the transmitter's role
dependence lives.** Forcing `DM(0x2198)` changes the script selection *and*
every other consumer of the role, and it is one of the others that matters.

### The other consumers

`DM(0x2198)` is read in exactly four places in the V.34 overlay, one of which
is the script selector already understood:

```text
2b4a  AX0 = DM($2198)     ; assembles a control word: bit 14 set when calling
2d6e  AX0 = DM($2198)     ; the script selector (140)
3034  AY0 = DM($2198)
3102  AY0 = DM($2198)
```

`0x2b49..0x2b53` is the interesting one — it builds a word from the role and
from `DM(0x2278) & 0x2000`, setting `0x4000` when the role is *calling*, the
opposite polarity to the script's field `0x0b`. That, `0x3034` and `0x3102` are
the three places left to look, and they are a short list rather than a search.

### What this session settles

- The calling script is mapped, its indices resolve, and its page-8 loop is
  accounted for by its own branch fields.
- 115n's "the calling script has the same round set" is confirmed and
  strengthened: same states, same tests, same structure.
- The script is **eliminated** as the cause of the calling side's page-8
  silence, which is where 140 pointed and where the next session would
  otherwise have started.

## Session 142: 0x2b4a is dead code, and so is the rest of 141's short list

Session 141 narrowed the role's effect to three readers of `DM(0x2198)` outside
the script selector. All three are eliminated, and the first one is worth
decoding anyway because it looks so much like an answer.

### What PM 0x2b49 builds

```text
2b49: AR = $0000
2b4a: AX0 = DM($2198)          ; the role
2b4b: AF = AX0 + 0
2b4c: AY1 = $4000
2b4d: IF NE AR = AR OR AY1     ; calling  -> bit 14
2b4e: AX0 = DM($2278)
2b4f: AY0 = $2000
2b50: AF = AX0 AND AY0
2b51: AY1 = $0100
2b52: IF NE AR = AR OR AY1     ; DM(0x2278) bit 13 -> bit 8
2b53: AY0 = $4000
2b54: AF = AX0 AND AY0, SI = AX0
2b55: AY1 = $8000
2b56: IF NE AR = AR OR AY1     ; DM(0x2278) bit 14 -> bit 15
2b57: SR = LSHIFT SI (LO) BY -11
2b58: AY0 = $0003
2b59: AF = SR0 AND AY0         ; DM(0x2278) bits 11..12 -> bits 0..1
2b5a: AR = AR OR AF
2b5b: DM($223F) = AR
2b5c: RTS
```

A four-field control word, one field of which is the role, assembled into
`DM(0x223F)`.

**`DM(0x223F)` is written here and read nowhere.** Not elsewhere in the V.34
overlay, not in the INFO overlay, not in `TIKRNL81.F34`, not in the PRI 30M
kernel. Whatever consumes it is not in any image this harness loads.

### And none of it runs

The overlay-gated histograms from 141 settle it without another run:

```text
PM      caller    answerer
2b49         0           0   the control word, entry
2b5b         0           0     DM(0x223F) = AR
3012         0           0   its only call site
3034         0           0   role reader 2
3102         0           0   role reader 3
```

Against neighbours in the same histogram, which is what makes those zeros
meaningful rather than a coverage gap:

```text
2b61   158,415     139,797   reads DM(0x2140) bit 8
2b76   159,183     230,109   the MAC bank just below
28e0    52,805      46,599   calls the filter bank
```

So `0x2b4a`, `0x3034` and `0x3102` are all dead on this path, and **the only
live reader of `DM(0x2198)` is the script selector at `0x2d6e`** — which runs
five times per end, a setup-time decision, not a per-sample one.

### Which sharpens the contradiction rather than resolving it

Three things are now all true and do not obviously fit together:

1. Forcing `DM(0x2198) = 0` on the calling end takes its page-8 transmit RMS
   from 5.5 to 248.8 (140).
2. The only consumer of `DM(0x2198)` that executes is script selection (142).
3. The two scripts' per-state content differs by two bits, and forcing both of
   them changes nothing (141).

The way out is that (3) tested the wrong thing. Selecting the other script does
not just change field values, it changes **which blocks are visited and in what
order** — the branch fields resolve to blocks in whichever table is selected,
so the answering script's `0x0020` is a different block from the calling
script's, reached by a different path. Forcing two field values into the calling
script leaves the caller walking the calling script.

### A measurement that supports that reading

From the same histograms, the two loaders:

```text
2e1b   289,978         271   format A  (calling records)
2e25         0      72,241   format B  (answering records)
```

The caller re-loads records **289,978** times where the answerer's format-A
count is 271, and the answerer's total loading is a quarter of the caller's.
That is not a content difference, it is a rate difference: the calling end is
re-entering script blocks continuously, which is what a state machine that
cannot make progress looks like from the loader's side.

### Next

Trace the *sequence* of blocks, not their contents: `DM(0x2192)` holds the
script base and `DM(0x14A5)` the record cursor, both written per block entry at
PM `0x2d7b`/`0x2d93`, so write-watching them gives the visited-block trail per
end. Comparing the two trails — rather than the two tables — is the comparison
141 should have made and 142's rate asymmetry says will be productive.

## Session 143: the block trail is one block — both ends are parked in a designed wait state

Tracing the sequence, as 142 proposed, both corrects Session 141 and closes the
chain that Sessions 137–142 have been walking backwards.

### There are two sequencers, and 141 mapped the wrong one

```text
sequencer A   terminator 0x19   cursor DM(0x14A5)   publishes TrnProgress from
                                                    DM(0x2147) = field 0x10
sequencer B   terminator 0x24   cursor DM(0x2192)   bases 0x1EA2 / 0x1E81
```

PM `0x2ddd` is the publish — `AX0 = DM($2147); DM($3FC2) = AX0` — so **sequencer
A is what drives the state machine everything here has been reading.**

And sequencer B never runs:

```text
2dd6   caller 48,236   answerer 11,950    seq A: enters a new block
2dfe   caller      0   answerer      0    seq B: enters a new block
2deb   caller 53,027   answerer 46,822    seq B: test 0  (runs, never returns LE)
```

Its four tests execute 50,000 times an end and not one of them ever lets it
advance. **So Session 141's map of `0x1EA2` and 115n's of `0x1E81` are maps of a
table that is not walked in this configuration.** Both stand as decodes; neither
describes what the card is doing.

### The trail is one block per end

Write-watching `DM(0x14A5)`, the whole run, every block sequencer A enters:

```text
caller     49,105 x  block 0x1ae5        6 x 0x1e18
answerer   12,201 x  block 0x1ba5        6 x 0x1df7,  6 x 0x1adc
```

That is the entire trail. Decoded:

```text
block 0x1ae5  (caller, low lane)        block 0x1ba5  (answerer, high lane)
  0x10 = 0x0060   state                   0x10 = 0x0090   state
  0x0e = 0x02bc   threshold               0x0e = 0x02bc   threshold
  0x0f = 0x0032   countdown               0x0f = 0x0032   countdown
  0x11 = 0x0002   branch0                 0x11 = 0x0013   branch0
  0x15 = 0x000a   test0                   0x15 = 0x000a   test0
  0x19 = 0x0001   test4                   0x19 = 0x0001   test4
```

`0x0060` and `0x0090` are exactly the two deepest states Session 137 measured,
now with an address and a reason. The blocks are identical apart from `branch0`,
and resolving that through `DM(0x0676 + i)`:

```text
caller    index 0x02 -> DM(0x0678) = block 0x1ae5     itself
answerer  index 0x13 -> DM(0x0689) = block 0x1ba5     itself
```

**Both blocks branch to themselves.** Neither end is stuck by a fault or a
corruption: each is sitting in a *designed wait state*, re-arming a 50-tick
countdown, doing exactly what its script says.

### What they are waiting for

Both use the same exit test, index `0x0a` -> `DM(0x0655)` = PM `0x2ef3`:

```text
2ef3: AR = DM($13BF)
2ef5: AR = AR XOR AY0      ; 0 (advance) when the flag is set
2ef6: DM($13BF) = M0       ; latch, self-clearing
2ef7: DM($137C) = M0
```

`DM(0x13BF)` is a detector latch, set at PM `0x0e39..0x0e3b`:

```text
0e33: CNTR = $0006
0e34: DO $0E35 UNTIL NOT CE
0e35:   MR = MR + MX0 * MY0 (SS), MX0 = DM(I0,M1)   ; six-tap correlation
0e36: AR = ABS MR1
0e37: AY0 = DM($2145)                               ; field 0x0e = 0x02bc
0e38: AF = AR - AY0
0e3a: IF GT AR = 0 + 1                              ; over threshold -> latch
0e3b: DM($13BF) = AR
```

The threshold is the block's own field `0x0e`, `0x02bc` on both ends. The
detector runs constantly — 158,415 executions on the caller and 36,183 on the
answerer — and the latch never survives to the test.

### So the whole chain, end to end

```text
caller parked in block 0x1ae5 (state 0x0060), branching to itself
  -> exit test PM 0x2ef3
  -> waits on DM(0x13BF)
  -> set only when a six-tap correlator exceeds DM(0x2145) = 0x02bc
  -> never exceeds it
```

and the answerer is the same structure at `0x0090`. That reframes everything
from 137 onward: **the calling side's silence and the answering side's `0x0090`
ceiling are one phenomenon** — two ends each waiting on a detector that does not
trip, in blocks that are supposed to loop until it does. 115m's "0x0090 is the
top of a timeout walk" was right about the shape and is now located.

### Next

The correlator, not the script. Its input pointer `I0` and coefficients `MY0`
are set by whatever calls PM `0x0e33`, and the question is whether it is looking
at the wrong samples, using the wrong reference, or being asked to clear a
threshold that the emulated line level cannot reach. `EICON_FORCE_DM` can hold
`DM(0x2145)` low as a first, blunt discriminator: if a lower threshold advances
either end, the input is real and the scaling is wrong; if it does not, the
input is wrong.

## Session 144: the driver clears the level hypothesis, and page 8 is broadband where INFO is a tone

Three things looked worth checking after 143's never-tripping detector: whether
the driver configures something we do not, whether the line level is simply too
low to clear a threshold of `0x02bc`, and what is actually on the line during
page 8.

### The driver: we are faithful, and here is what we never set

`putcai()` (`tty_module/isdn.c:1330`) writes the modem CAI tail literally:

```c
p[22] = 0; /* modem info options    */
p[23] = 0; /* transmit level adjust */
p[24] = 0; /* speaker parameters    */
```

So a zero transmit-level adjust is what the shipping driver sends, and
`eicon_idi.build_cai()` matching it is correct, not an omission.

Worth having as an inventory, though: `kernel/mdm_msg.h` defines a V.34 shaping
and training byte this project has never set a bit in —

```text
DSP_CAI_MODEM_DISABLE_TX_REDUCTION 0x01   DSP_CAI_MODEM_DISABLE_PRECODING    0x02
DSP_CAI_MODEM_DISABLE_PREEMPHASIS  0x04   DSP_CAI_MODEM_DISABLE_SHAPING      0x08
DSP_CAI_MODEM_DISABLE_NONLINEAR_EN 0x10   DSP_CAI_MODEM_DISABLE_MANUALREDUCT 0x20
DSP_CAI_MODEM_DISABLE_16_POINT_TRN 0x40   DSP_CAI_MODEM_EXTENDED_TRAINING    0x80
```

plus `DSP_CAI_MODEM_TRANSMIT_LEVEL_MASK 0x0f`. All are reachable through the
ported CAI builder and none has ever been tried.

### Level does not predict outcome

The obvious hypothesis — the detector threshold `0x02bc` is never cleared
because the emulated line is quiet — is refuted by the archive. Card transmit
RMS against how far each call got, over the native-tower captures:

```text
run35   RMS   177  ->  0x00d0     run10   RMS 3033  ->  0x0037
run37   RMS   177  ->  0x00d0     run31   RMS 2454  ->  0x0060
run34   RMS   288  ->  0x00b0     run36   RMS 2157  ->  0x00c0
```

The quietest calls in the archive are among the *most* successful and the
loudest reach `0x0037`. Loopback sits at RMS 250–265 (about -36 dBm0), squarely
inside the archived range. **Transmit level is not the discriminator**; do not
spend another session on it.

### What is actually on the line

Spectra of the answerer's own transmit capture, INFO windows against page-8
windows:

```text
INFO   4.00..4.50 s   rms 125   peaks 1796,1798,1800,1802,1804 Hz   one tone
INFO  20.00..20.50 s  rms 202   peaks 1796,1798,1800,1802,1804 Hz   one tone
page8  5.22..5.46 s   rms 251   peaks  671,1375,1929,2492,3371 Hz   no structure
page8  7.60..7.84 s   rms 255   peaks  442, 529,1783,3913      Hz   no structure
```

On the INFO page the card emits a clean 1800 Hz carrier — narrow, coherent,
exactly what phase 2's control channel should be, and consistent with
`v34_info.py` recovering CRC-valid frames from it (137). On page 8 it emits the
same amount of energy spread across the band with no dominant component.

V.34 phase 3 opens with **S**, an alternating two-point sequence that is a
coherent pair of tones about the carrier. What the answerer transmits is not
that. **So Session 143's detector is behaving correctly: there is nothing on the
line for a six-tap correlator to lock to.** The caller is silent and the
answerer is emitting noise, and both are then waiting for a signal that neither
is sending.

### A hypothesis, explicitly not a result

Session 138 measured the page-8 transmit chain running **14.06 times per
sample** against exactly **1.00** on a run-to-idle page, because
`V34_CYCLES_PER_SAMPLE` gives overlay `0x0261` a fixed 20,000-instruction budget
per 8 kHz sample. Running an interpolating modulator fourteen times per sample
would alias a coherent signal into exactly this — broadband energy at the right
total power.

A first look supports it: the answerer's page-8 spectral flatness falls
`0.571 -> 0.527 -> 0.274` as the budget goes `20000 -> 4125 -> 1500`. But the
same metric returned 1.000 and 0.000 for INFO windows in two of those runs,
which is nonsense, so **the measurement is not trustworthy and the trend is not
evidence yet**. What it does is make the budget worth attacking properly, having
been set aside in 138 on the grounds that changing it did not make the *caller*
transmit — which was never the right test, because the caller transmits nothing
at any budget.

### Next

Fit the budget instead of sweeping it, and judge it on the *answerer's* signal
rather than the caller's silence: a correct budget should make the page-8 output
narrowband, and the test is whether an independent demodulator can find S in it,
the way `v34_info.py` finds INFO frames. That tool is the model — it already
demodulates the control channel off a capture with none of the firmware in the
path, and an S detector is a much simpler instrument than the one it already has.

## Session 145: the card's own state machine answers it — no S detector needed

Session 144 ended by proposing to build an S detector. That was the wrong
instrument: the card already has one, it already publishes its verdict, and
every run already records it.

### The budget question, settled with data already on disk

If a corrected instruction budget made the page-8 signal detectable, the wait
block of Session 143 would exit and the state machine would advance. It does
not, at any budget:

```text
budget            caller deepest   answerer deepest   answerer page-8 states
20000 (default)       0x0060            0x0090        60 64 70 71 72 74 90
 4125                 0x0060            0x0090        60 62 64 70 71 72 74 90
 1500                 0x0060            0x0090        60 62 64 70 71 72 74 90
```

Identical ceilings. The answerer picks up sub-state `0x62` below the default and
nothing else. **`V34_CYCLES_PER_SAMPLE` is not what stops either end**, and that
took one query against captures already taken.

### Correction to Session 144

144 said page 8 "should be a coherent two-tone alternation" and that broadband
output was therefore wrong. **That is too strong.** Measuring hardware over its
*real* page-8 windows, energy concentration (the fraction of spectral energy in
the top 5% of bins; ~0.05 is white noise, 1.0 is a pure tone):

```text
run37 hardware card TX   min 0.304   median 0.828   max 1.000   (n=81)
```

Hardware is broadband part of the time and fully tonal part of the time, which
is what phase 3 should look like — S and PP are tonal, TRN is a scrambled
wideband signal. So broadband on page 8 is normal for part of it, and 144's
spectral snapshot happened to land in an interval where that is expected.

Two earlier versions of this measurement were wrong and are worth naming so the
next one is not: sampling the whole capture mixes in the other pages, and
sampling from the first to the last page-8 sample mixes in the INFO periods
between visits, which is where a median of 0.925 came from. Slices must sit
inside a single contiguous page-8 window.

### What the corrected measurement does say

```text
loopback answerer, budget 20000   min 0.186   median 0.198   max 0.209   (n=11)
loopback answerer, budget  4125   min 0.179   median 0.207   max 0.255   (n=29)
loopback answerer, budget  1500   min 0.298   median 0.411   max 1.000   (n=122)
```

At the default budget the transmitter **never leaves the broadband floor** —
maximum concentration 0.209 across every page-8 window in the run, where
hardware reaches 1.000. At 4125 it is the same. At 1500 it does produce fully
concentrated intervals, like hardware.

So the budget is implicated in whether the modulator produces coherent output at
all, and 20000 is wrong by this measure as well as by the 14.06-executions-per-
sample measure of Session 138. It is simply not *sufficient*: at 1500 the signal
becomes tonal and the state machine still does not advance.

### Where that leaves it

- The wait block, its test and its latch are understood (143).
- The line level is not the problem (144).
- The instruction budget is wrong but is not the blocker (145).
- At a plausible budget the answerer's page-8 output does become coherent, and
  the ceiling is unchanged — so **at least one more thing is wrong**, and it is
  downstream of "is there a signal".

The concentration metric is worth keeping: it needs no new tool, runs off the
captures every run already writes, and separates hardware from loopback cleanly
at the default budget (max 0.209 against 1.000). Confine the slices to one
contiguous page-8 window and it is a one-number answer to "is this transmitter
producing anything a receiver could lock to".

## Session 146: the detector was never the problem — 143's inference was wrong

"What is downstream of *is there a signal*" turns out to be the wrong question,
because the signal, the detector and the test are all working.

### The threshold probe

Force the detector threshold `DM(0x2145)` down from its script value `0x02bc`
and see whether the wait block exits:

```text
threshold        caller deepest   answerer deepest
0x02bc (script)      0x0060            0x0090
0x0040 forced        0x0060            0x0090
0x0001 forced        0x0060            0x0090
```

Nothing. At that point Session 145's reading — that the correlator output is
essentially zero — looked confirmed. It is not.

### The latch sets, at the real threshold, thousands of times

Write-watching `DM(0x13BF)`, the latch the wait block's test reads:

```text
                        threshold 0x0001         threshold 0x02bc (real)
caller    set   pc=0e3c          2,375                     2,374
          clear pc=2ef7          1,595                     1,595
answerer  set   pc=0e3c          2,399                     2,399
          clear pc=2ef7          1,599                     1,599
```

**The real threshold and a threshold of 1 produce the same counts to within one
event.** The six-tap correlator clears `0x02bc` comfortably and routinely; the
latch sets about 2,400 times a run on each end; and PM `0x2ef6` — the test —
consumes it about 1,600 times. The signal is there, it is above threshold, the
detector works and the test fires.

**So Session 143's "the latch never survives to the test" is withdrawn.** That
was an inference from the block never advancing, not a measurement, and the
measurement says the opposite. Sessions 144 and 145 then spent their time on the
signal — level, spectrum, instruction budget — downstream of a premise that was
already wrong. 144's spectral work and 145's budget result stand on their own
merits; the reason they were undertaken does not.

### What is actually happening

Session 143 had the answer in its own numbers and misread them. The caller
enters block `0x1ae5` **49,105 times**. That is not a state machine waiting for
a test to fire — it is a state machine whose test fires constantly and whose
only branch target is the block it is already in:

```text
block 0x1ae5   field 0x11 (branch0) = 0x0002 -> DM(0x0678) = 0x1ae5   itself
block 0x1ba5   field 0x11 (branch0) = 0x0013 -> DM(0x0689) = 0x1ba5   itself
```

The test passes, the sequencer takes branch0, branch0 is this block, and it
re-enters. Two and a half thousand successful detections a run, every one of
them leading back to the same block. **Neither end is blocked on a signal;
both are in a block with no exit.**

### The question that replaces it

Both blocks define exactly one branch field. The sequencer at PM
`0x2dcc..0x2dd5` has four test/branch slots and falls through to `RTS` when none
takes; these blocks fill one of them, with themselves. So the exit cannot come
from within the block, and it does not come from sequencer B, which Session 143
showed never enters a block at all on either end.

That leaves: what moves a card out of a terminal self-looping block? Either the
countdown (field `0x0f = 0x0032`, 50 ticks, present in both), or something
outside both sequencers rewriting the block pointer — `DM(0x14A5)` has writers
at PM `0x2d7b`, `0x2dd6` and `0x2ddb`, and only `0x2dd6` is the sequencer's own.
The archived hardware calls reach `0x00d0`, so on hardware something does take
the card onward from here; the trail instrument from 143 applied to an archived
capture would show which block it goes to next, and that is the comparison to
make.

## Session 147: the wait block loops because the test passes, and raising the threshold moves both ends

Session 146 established that the detector fires constantly and the block still
never advances, and left "what takes a card out of a self-looping block" open.
The answer is that nothing has to: the loop is the *branch being taken*, and the
way out is for the test to stop passing.

### The script advances linearly; only two blocks branch at all

Walking the whole of sequencer A's script from base `0x1A2E`, 60 blocks a lane,
and resolving every branch field through `DM(0x0676 + i)`:

```text
caller lane     only 0x1ae5 (state 0x0060) has a branch field: b1 -> 0x1ae5, itself
answerer lane   only 0x1ba5 (state 0x0090) has a branch field: b1 -> 0x1ba5, itself
```

Every other block in both lanes carries none. So the script's normal advance is
**sequential** — the loader walks to the next record group — and a branch field
exists only to override that. The two blocks that have one use it to point at
themselves.

That makes the self-branch the "stay here" arm, and the sequential fall-through
the "move on" arm. **A test that passes keeps the card in the block.** Session
143 read this backwards, and 144–146 inherited the error: the block is not
waiting for its test to fire, it is waiting for its test to *stop* firing.

### Raising the threshold advances the state machine

If that reading is right, the detector latching too readily is the fault, and
raising `DM(0x2145)` should free both ends. It does:

```text
threshold             caller deepest   caller page-8 states   answerer deepest
0x02bc (script)           0x0060       60                          0x0090
0x0001 forced low         0x0060       60                          0x0090
0x2000 forced high        0x007a       60 68 72 7a                 0x0090
0x7fff forced high        0x004f       (never reaches page 8)      0x0092
```

At `0x2000` the caller leaves `0x0060` for the first time in any run of this
configuration and walks `0x60 -> 0x68 -> 0x72 -> 0x7a`. At `0x7fff` the answerer
passes its own `0x0090` ceiling to `0x0092`, and the caller breaks earlier — too
high a threshold costs it page 8 entirely, so there is an optimum rather than a
monotone improvement.

### The chain, complete

```text
the page-8 transmitter emits broadband energy, never leaving the noise floor
  at the default instruction budget where hardware reaches full spectral
  concentration                                                        (145)
    -> the six-tap correlator clears 0x02bc on that noise, routinely,
       ~2,400 times a run on each end                                  (146)
    -> the wait block's test therefore passes, every time
    -> its only branch field is taken, and points at itself
    -> the block is re-entered 49,105 times and the state never advances (143)
```

Each of 143's, 145's and 146's measurements was right; the causal direction
between them was not. What made it legible was the static graph — noticing that
two blocks out of 120 have a branch field and both are self-branches — and that
cost one query against the decoder that already existed.

### What this is and is not

**Not a fix.** Forcing the threshold treats the symptom: the correct behaviour is
a transmitter whose output is not broadband, against which `0x02bc` is the right
threshold and the detector latches on something real. The threshold sweep is a
demonstration of the mechanism, not a repair, and the caller still does not
connect at any value.

**It does re-rank the queue.** `V34_CYCLES_PER_SAMPLE` moves from "wrong but not
the blocker" (145) to the prime suspect: it is the one known defect that would
produce broadband output, 138 measured its effect on the transmit chain
directly (14.06 executions per sample against 1.00 on a run-to-idle page), and
145 showed the page-8 output does become spectrally concentrated when it is
lowered. The test that matters now is whether a fitted budget stops the detector
latching — measurable as the latch count at PM `0x0e3c` falling from ~2,400 —
rather than whether the deepest state changes, which is what 145 looked at and
why it read as a null result.

## Session 148: a fitted instruction budget does not stop the detector latching

Session 147 left one ranked test: whether fitting `V34_CYCLES_PER_SAMPLE` stops
the `DM(0x13BF)` correlator latching, measured on the latch count rather than on
the deepest `TrnProgress` (which 145 had already shown is flat). Three loopback
runs of the two-sided V.90 configuration, budget the only variable:

```bash
tools/eicon_loopback.py --native-mips --answerer-modulation v90 \
    --caller-modulation v90a --seconds 40 \
    --capture-dir artifacts/loopback-v90a/lat-1500 \
    --watch-dm-writes 0x13bf:400000 --pc-histogram --pc-histogram-from 0x0261 \
    --caller-env EICON_V34_CYCLES_PER_SAMPLE=1500 \
    --answerer-env EICON_V34_CYCLES_PER_SAMPLE=1500
```

| budget | end | latch sets | clears | **sets as % of writes** | deepest |
|---|---|---|---|---|---|
| 20000 (default) | caller | 142,734 | 120,902 | **54%** | `0x0060` |
| | answerer | 49,029 | 32,502 | **60%** | `0x0090` |
| 4125 | caller | 73,695 | 71,143 | **51%** | `0x0060` |
| | answerer | 20,060 | 13,346 | **60%** | `0x0090` |
| 1500 | caller | 27,825 | 27,234 | **51%** | `0x0060` |
| | answerer | 14,692 | 9,808 | **60%** | `0x0090` |

**The answer is no.** The absolute count falls about five-fold, which is what
147 said to look for, but it falls *only* because the correlator is invoked five
times less often: the PC histogram has PM `0x0e3b` at 154,330 executions in the
page-8 window at the default and 33,619 at 1500, tracking the same ratio. The
fraction of invocations that latch — the quantity that decides whether the wait
block's test passes — is **flat at 51–60% across a 13x range of budget**. The
detector sees the same thing at every clock model.

### And the signal itself barely moves

`tools/v34_page8_concentration.py` (new, and the metric 147 specified: energy in
the top 5% of FFT bins, over one contiguous page-8 window read off the endpoint
log) measures the transmitted `.ulaw` directly, with no firmware in the path:

```text
budget   end        window     RMS     concentration
20000    answerer   0.30s     1004.3      0.097
 4125    answerer   1.60s     1021.3      0.084
 1500    answerer   4.46s     1024.6      0.189
 1500    caller     4.24s        5.0      0.051
```

White noise scores 0.05. The answerer's page-8 output is broadband at every
budget; 1500 roughly doubles the concentration and is still four-fifths of the
way to noise. The caller is at the noise floor exactly, as expected from 137.

**So `V34_CYCLES_PER_SAMPLE` is eliminated as the cause of the latching**, and
147's re-ranking is withdrawn. It remains wrong — 160 MIPS against an
ADSP-2185N's 9,375 instructions per sample — and lowering it is not inert: page-8
residency goes from 0.30 s to 4.46 s per visit, and the answerer picks up
sub-state `0x0062`. But the ceilings are unchanged at `0x0060`/`0x0090` and the
transmitter is broadband either way, so it is a tidiness fix, not the blocker.

### A correction to Session 146

146's "the latch sets 2,374 / 2,399 times a run" is an artifact of the watch
limit: `--watch-dm-writes 0x13bf:4000` stopped logging at 4,000 lines, and
2,374 + 1,626 is exactly 4,000. Uncapped, the caller latches **142,734** times.
The conclusion 146 drew from it — that the detector is not the problem — is
unaffected and is reinforced here; only the magnitude was wrong. Watch limits
are a ceiling on the log, not a measurement, and any count that sums with its
companion to a round number should be read as one.

### What this leaves

Both ends still walk V.8 -> INFO -> page 8 and park in their designed wait
blocks. The chain of 143/146/147 stands except for its last link: the block's
test passes because the correlator latches on the signal that is there, and
nothing about the harness's instruction budget changes what that signal is. The
open question moves back one step, to what the page-8 transmitter is being fed —
143's `0x1ae5`/`0x1ba5` are wait states with a self-branch, so the question is
what is supposed to make the test *stop* passing.

## Session 149: the page-8 transmitter was being decimated by ten, and pacing it fixes that

148 eliminated the instruction budget as the reason the `DM(0x13BF)` detector
latches, on the grounds that the latch *rate* is flat across a 13x sweep. That
was the right measurement of the wrong quantity. The budget is the fault, but
not through the detector: it is what destroys the transmitted signal.

### The calibration that was missing

145 called the page-8 output "broadband" with nothing to compare it against.
There is a comparison on disk — a real analogue modem's V.34 phase-3 signal, in
the `.rx.ulaw` of the live tower calls:

```text
                              0-300  300-600  600-1200  1.2-1.8k  1.8-2.4k  2.4-3k  3-3.4k  3.4-4k
hardware peer (run25/28/30)    0.3%    2.1%     5.8%      4.3%      4.4%    81.1%   1.4%    0.2%
loopback answerer TX           6.6%    6.7%    16.5%     15.8%     16.1%    13.8%   9.6%   14.2%
```

Hardware is a carrier: 81% in 2400-3000 Hz, peaking in two bins at 2391/2406 Hz,
concentration **0.818**. Ours is flat white noise at **0.097**, with 14.2% of its
power above 3400 Hz and 6.6% below 300 Hz — bands no PSTN modulator emits into
at all. That is not a mis-fed modulator. It is a correct modulator sampled wrong.

### Where the transmit sample comes from, and how often

`DM(0x3fb4)` is a pointer, written twelve times in a whole call and always with
`0x3764`, so the line sample is the fixed word `DM(0x3764)`. Watching writes to
*that* and segmenting by `TrnProgress` gives the number:

```text
page-8 window            publishes   samples   per sample
41600..44000                 21589      2400        9.00
60960..63360                 22938      2400        9.56
80480..82240                 21897      1760       12.44
99520..101440                23259      1920       12.11
```

**The page publishes a transmit sample 9-12 times per 8 kHz tick and we take
one.** That is decimation by ten of a real waveform, which is exactly a flat
spectrum at the right total power. It is the same 14.06 figure Session 138
measured on the transmit chain, finally attached to a consequence.

The cause is ours and is the one 138 named: `V34_CYCLES_PER_SAMPLE` gives the
page a fixed 20,000-instruction budget because it never idles, so it goes round
its foreground ten times per tick. A run-to-idle page gets its boundary from
IDLE; hardware gets it from the SPORT interrupt; page 8 had neither.

### The fix: pace the page by its own publish

`adsp2181_stop_on_dm_write()` (new, in the core) ends a run at the instant the
watched word is written, and `EICON_V34_PUBLISH_PACED` (**default on**) arms it
on the transmit word for overlay `0x0261`, with
`EICON_V34_PUBLISH_MAX_CYCLES` as the ceiling so a page that stops publishing
cannot hang the media thread. `=0` restores the fixed budget.

```text
                        publishes/sample   TX concentration   page-8 residency
fixed budget (before)         10.58              0.097        4 segments, 0.30s each
publish-paced (after)          1.00              0.813        1 segment, 10.20s
live hardware peer                -              0.818                    -
```

### What it does to the state machine

Both ends leave the ceilings that have held since Session 115:

```text
        before (147/148)                 after
caller  0x0060                           0x0060 0x0062 0x0066 0x0068 0x0070 0x0072
                                         0x0074 0x0076 0x0078 0x007a 0x0090 0x0094
                                         0x00a0 0x00a1 0x00a2 0x00a4 0x00b0
answerer 0x0090                          ... 0x0090 0x0092 0x0097 0x0098 0x00a0
                                         0x00a4 0x00a6 0x00a8 0x00aa 0x00ac 0x00b0
```

The wait blocks `0x1ae5`/`0x1ba5` release, page 8 stops cycling, and both ends
reach `0x00b0` — the DIL region, where `0x00c6`/`0x00d0` is success. **The
caller also transmits for the first time**: page-8 TX RMS 5.0 -> 776.6, which
retires Session 137's "the calling side transmits nothing on page 8" as another
consequence of the same defect rather than a separate fault.

143/146/147's chain was right in every measurement and right in its direction —
the detector latches because it is given noise. What it was missing is that the
noise was our own sampling of the firmware's signal.

### What is still open

**No call connects yet.** Both ends stop at `0x00b0`. And the fix is asymmetric
in one respect that is now the obvious next thread: the caller publishes at
1.00/sample like the answerer and transmits at a healthy level, but its own
concentration is **0.090** against the answerer's 0.813. The answerer's signal is
now indistinguishable from hardware on this metric and the caller's is not, so
whatever remains is specific to the calling role — which is where Sessions
140-142 were looking before the pacing defect masked everything.

## Session 150: correcting 149's concentration claim — the metric was scoring DC

149 reported the paced answerer at **0.813** concentration against **0.818** for a
live modem and called the two indistinguishable. **They are not, and the
comparison was wrong.** The metric ranks bins over the whole spectrum, so a
signal parked on a constant scores as well as a carrier. Restricted to the
300-3400 Hz passband, with the peak reported:

```text
                              passband conc   peak      % below 300 Hz
live hardware peer (run25)        0.818      2406 Hz          0
paced answerer                    0.071      3094 Hz         90
paced caller                      0.081      1953 Hz          2
unpaced answerer (148 control)    0.096      2484 Hz          7
```

**The transmitted passband signal did not improve.** 149's headline number was
80-90% of the energy sitting below 300 Hz, which is not a V.34 signal at all.

### What the answerer is actually doing

Per second through the page-8 window:

```text
   0.0s rms 1033.6 passband 0.080 peak 2672Hz  dc   2%
   1.0s rms 1035.2 passband 0.076 peak 3344Hz  dc   1%
   2.0s rms  545.9 passband 0.100 peak 3250Hz  dc   2%
   3.0s rms    0.0            (silent)
   4.0s rms  325.8 passband 0.210 peak 1938Hz  dc  97%
   5.0s .. 16.0s  rms 1052.0 passband 0.732 peak 297Hz  dc 100%   <- constant
```

From about five seconds in it emits **one unchanging sample value** for twelve
seconds. It is still publishing at 1.00/sample — it publishes the same word every
time. The caller is silent for two seconds and then broadband at 0.074-0.126.

Neither end ever emits a carrier, so the deeper states 149 measured cannot have
been reached by training against each other. That is the pattern Session 102
already named on the answering side: **advancing on timers, not on received
signal.** The state trail is real and reproduces; what it means is not what 149
took it to mean.

### What survives from 149, and what does not

**Survives, and is still a genuine defect fixed:** the page published a transmit
sample 9-12 times per 8 kHz tick against the one the harness consumed. That is
measured directly (writes to `DM(0x3764)` segmented by `TrnProgress`), it is
plainly wrong as a clock model, and pacing takes it to exactly 1.00. Also
surviving: the state trails, and the caller transmitting at all (RMS 5.0 ->
776.6), which does retire 137's "the calling side transmits nothing".

**Withdrawn:** that the paced signal matches hardware. It does not, on either
end, and the in-passband figure barely moved (0.096 -> 0.071/0.080).

`tools/v34_page8_concentration.py` now measures the passband by default and
prints the peak frequency and the sub-300 Hz share alongside, so this particular
mistake cannot be made silently again. **Do not read a bare concentration number
without the peak**: 0.732 at 297 Hz is a stuck DC level, and 0.818 at 2406 Hz is
a modem.

### Where that leaves the caller's number

149 ended by ranking the caller's 0.090 as the next thread on the grounds that
the answerer's 0.813 was healthy. With both ends now measured in the passband at
0.071 and 0.081, **there is no asymmetry to chase**: neither end modulates. The
question is the same one for both, and it is the one 145 asked before the
pacing defect was found — what the page-8 transmitter is being fed.

## Session 151: the page-8 transmit chain, mapped end to end

150 dissolved the question 149 posed. There is no caller-specific defect to
chase: in the passband the caller is 0.081 and the answerer 0.071, against
hardware's 0.818. Both ends fail the same way, so the question is the one 145
asked — what the transmitter is being fed. The chain is now mapped, from the
line back to the overlay, off the PC histogram of a paced run.

### The publisher, PM 0x1746

```text
1746: AR = DM($3761)          ; transmit credit          39,263 executions
1747: AR = AR + 0
1748: IF EQ JUMP $1750        ; no credit -> publish AR, which is 0
1749: AR = AR - $0001         ; spend one                 39,262
174a: DM($3761) = AR
174b: I0 = DM($3768)          ; read cursor
174c: L0 = $0014              ; a 20-word circular buffer
174d: AR = DM(I0,M1)          ; take one sample
174e: DM($3768) = I0
1750: I4 = $3764
1751: DM(I4,M5) = AR          ; publish to the line word
1752: RTS
```

So `DM(0x3764)` is the tail of a 20-word ring gated by a credit at
`DM(0x3761)`. The starve arm at `0x1748` published on 1 of 39,263 ticks, so the
consumer is not outrunning the producer.

### The credit is balanced, not leaking

Writers of `DM(0x3761)`, with the values:

```text
ppc=1723   668 writes (answerer)   tops the credit up by 5
ppc=174a  3331 writes              spends one per publish
```

It oscillates 0 -> 5 -> 9 -> 0 and never grows, so the ring is neither
overflowing nor being read stale. The caller is the same, 2809:562, plus a
second pair at `0x1d4a`/`0x1d23` that is the V.90A overlay's copy of the same
code.

### The producer, PM 0x1769 — an interpolating filter

```text
1769: I1 = DM($3766)   L1 = $004A     ; 74-word history
176b: I0 = DM($3765)   L0 = $0014     ; the same 20-word ring, write side
176e: MX1 = DM($375D)  MY1 = DM($3759)
1770: AX0 = DM($376E)
1771: CNTR = DM($3755)                ; 7 samples per call
1772: DO $1780 UNTIL NOT CE
1773:   I6 = DM($376F)                ; index table, reset to 0x3788 each call
1775:   AR = AX0 + AY0                ; phase accumulate
1778:   CALL $17A6                    ; the filter proper; returns MR1
1779:   DM(I0,M1) = MR1               ; one output sample into the ring
1781: DM($3765) = I0
```

Executions: the routine runs 5,609 times and its body 39,263 — exactly the
number of publishes, so **production and consumption match one for one**. A
second, structurally identical producer at `0x1787` runs its body 50,481 times
into a different ring at `DM(0x376C)`; nothing on the transmit path consumes
those, and it is most likely the receive or echo chain rather than a second
transmitter, which is stated here as unconfirmed.

### What that leaves

Every stage from the ring to the line is now accounted for and balanced. The
content enters at the 74-word history the filter reads through `DM(0x3766)`, and
that is filled by the V.34 overlay's symbol mapper. So:

- the answerer's twelve seconds of one unchanging sample (150) means the
  overlay handed the filter a constant, not that the filter stopped;
- the broadband stretches mean the overlay handed it values with no symbol
  structure.

**Next: watch `DM(0x3766)`'s buffer, not the line.** The transmit history is a
74-word window at a known pointer, so the symbols themselves are readable
directly, one hop above everything 143-150 measured, and they can be compared
against what V.34 phase 3 is supposed to carry. That is a much smaller question
than "why is the line broadband", and it is now the only one left on this path.

## Session 152: the transmit history is fed from the V.90 mapping-frame block

151 said to watch `DM(0x3766)` rather than the line. Done, and the buffer and its
filler are now both identified.

### The buffer, and its one writer

Watching the cursor gives the extent directly: `DM(0x3766)` walks
**`0x3680..0x36c9`**, 74 words, matching the `L1 = $004A` the filter sets. An
ownership survey over that range (`--assert-dm-clean 0x3680:0x36c9:400@0x0261`,
now forwarded by `eicon_loopback.py`) finds **exactly one writer on both ends**:
PM `0x1742`, 29,600 writes each. Nothing else touches the transmit history.

`0x1742` is not the mapper. It is a resident block copy:

```text
173c: CNTR = DM($3F67)          ; 3 words
173d: I0 = $3FA7                ; source: the mapping-frame block
173e: I4 = DM($3767)            ; destination cursor
173f: L4 = $004A                ; the 74-word history
1740: DO $1742 UNTIL NOT CE
1741:   AR = DM(I0,M1)
1742:   DM(I4,M5) = AR
1744: DM($3767) = I4
```

So the transmit history is fed, three words per frame, from
**`DM(0x3FA7..)` — the V.90 mapping-frame block**, the same block
`EICON_V90D_TX_BLOCK_HOLD` exists to protect on page 14. The per-frame sequence
that drives it is at PM `0x1725`: copy the receive block, `CALL (DM(0x3FB8))` —
the page's own frame routine, the vector Session 113 found `PortableBulkDelay`
overwriting — then `CALL 0x173C` to move its output into the history.

### The clear is not the cause — disproved, do not re-derive

Two writers fill `DM(0x3fa7..0x3fa9)`: PM **`0x06cd`** 3,078 times and PM
`0x374e` 922 times in the first 4,000 writes. `0x06cd` is the resident kernel's
per-frame *clear*, and the shim suppresses it **only for page 14**. Three frames
in four being zeroed under a producer that fills the fourth is an impulse train,
which is white noise, which is the symptom.

It is not the cause. `EICON_V34_TX_BLOCK_HOLD` extends the same suppression to
`0x0261`, and with it on the transmitted signal is **byte-identical**: caller
RMS 776.6 and 0.081 at 1953 Hz, answerer 0.071 at 3094 Hz, deepest `0x00b0` on
both — every figure unchanged. The copy at `0x173C` must therefore run ahead of
the clear within the frame, so the zeroes never reach the history. The flag is
**off by default** and kept only so the A/B does not have to be rebuilt.

### Where this leaves it

The path is now complete and every stage in it is accounted for:

```text
page frame routine (DM 0x3FB8) -> DM(0x3FA7..) 3 words/frame
  -> PM 0x1742 copy -> history DM(0x3680..0x36c9), 74 words
    -> PM 0x17A6 interpolating FIR, ~38 taps, coefficients in PM
      -> 20-word ring, credit DM(0x3761)
        -> PM 0x1746 publisher -> DM(0x3764) -> the line
```

Everything from the block onwards is balanced and demonstrably correct, and the
only remaining upstream is `DM(0x3FA7..)` itself and the routine at PM `0x374e`
that fills it. **That is the next probe and it is a narrow one**: log the three
words `0x374e` writes per frame and ask whether they carry V.34 symbol structure
or noise. If they carry structure, the defect is between there and the line and
this map says there is nowhere left for it to hide; if they are noise, the
question moves into the page's own frame routine at `DM(0x3FB8)` and out of the
resident kernel entirely.

## Session 153: the noise is the overlay's own output, not anything the harness does to it

152 said to log what fills `DM(0x3FA7..)` and ask whether it carries symbol
structure. Run, with one correction to method: a plain `--watch-dm-writes` limit
is spent before page 8 even loads (92,310 clears plus 27,690 fills reached the
120,000-line cap during V.8 and INFO, and the page-8 window then logged nothing
at all). The `@OVERLAY` form of `--assert-dm-clean` arms on residency and is the
right instrument here.

### The page-8 writers of the frame block

```text
answerer   24,462 x PM 2ced      8,672 x each of PM 283c/283d/283e
caller     29,655 x PM 2ced      6,956 x each of PM 283c/283d/283e
```

`0x283c..0x283e` write **nothing but zeros** — they are the overlay's own
per-frame clear of the block, `DM(I4,M5) = $0000` three times. The data comes
from `0x2ced`, and that is not a mapper either:

```text
2ce7: CNTR = $0003
2ce8: DO $2CED UNTIL NOT CE
2ce9:   AR = DM(I0,M1)          ; from DM(0x0B92..)
2cea:   MR = AR * MY0 (SU)      ; scale
2ceb:   SR = LSHIFT MR0 (LO) BY 1
2cec:   SR = ASHIFT MR1 (HI, OR) BY 1
2ced:   DM(I7,M5) = SR1         ; into DM(0x3FA7..)
```

A gain-and-shift copy. `DM(0x0B92..0x0B94)` has exactly one writer on both ends,
PM **`0x3a57`**, which is another copy — three words per frame out of a 60-word
ring at cursor `DM(0x0F67)`.

### Every hop is a copy, and the noise is present at all of them

```text
                                   n       distinct    conc    peak bin
PM 3a57 -> DM(0x0b92)           16,827       5,570     0.074      135
PM 2ced -> DM(0x3fa7)           16,826       3,376     0.074      135
              the line (152)         -           -     0.071        -
```

Same concentration, same peak bin, at every stage. Nothing between the overlay
and the line changes the signal's character, which is the strongest possible
statement that the transport this project has spent Sessions 143-152 on is
**not** where the defect is.

And the stream is noise on its own terms, without reference to any spectrum.
Autocorrelation of the `DM(0x0b92)` sequence:

```text
lags 1..16   -0.013 -0.013 +0.012 +0.037 +0.048 +0.042 +0.043 +0.018 ...
largest |r| over lags 2..400:  +0.048 at lag 5
white-noise floor at n=16,827: +/-0.008
```

A modulated carrier would put |r| near 1 at its symbol period. This peaks at six
times the noise floor over lags 4-10 — a faint lowpass colouring on what is
otherwise a white sequence. **The V.34 overlay is generating noise at its own
output.** Its amplitude is right (RMS 2,052, range +/-8,494), so the arithmetic
is running; it is running on the wrong contents.

### What this closes and what it opens

**Closed:** the transmit path. From the overlay's own store at `0x2ced` through
`DM(0x0B92)`, `DM(0x3FA7)`, the 74-word history, the interpolating FIR, the
20-word ring and the publisher, every stage is a copy or a filter, all are
balanced one-for-one, and the signal's character is unchanged end to end. Do not
look here again.

**Open, and it is a different kind of question:** why the overlay computes noise.
Two readings, not yet separated:

1. the modulator is fed unseeded or wrong state — the natural continuation of
   Sessions 138-142, which were looking at exactly this (the `DM(0x2140)` gate,
   the role word, the script tables) before the pacing defect masked everything
   and should be re-derived now that page 8 runs at the right rate;
2. or the emulator's arithmetic diverges from an ADSP-2181 somewhere the
   modulator depends on — its MAC modes, the `(SU)`/`(RND)` variants or the
   shifter, which this chain uses heavily and which nothing has ever validated
   against hardware.

(2) is testable without another call and has never been tried: the same overlay
runs offline, and its output for a fixed input is deterministic.

## Session 154: the first arithmetic oracle, and a real 218x gap that is not the bug

153 left two readings for why the overlay computes noise, the second being that
the emulator's arithmetic diverges from the part. The part is settled and has
been since Session 61 — it is an **ADSP-2185N**, instruction-compatible with the
ADSP-2181 the emulator is named and configured for
(`chip_type = CHIP_TYPE_ADSP2181`, `mstat_mask = 0x7f`). What is not settled is
whether the emulation is faithful, and nothing in the tree had ever tested it:
`adsp_opcode_audit.py` says of itself that it is coverage, not a correctness
oracle.

### The datasheet's warning, checked

`docs/ADSP-218XN_SERIES.pdf` describes the part as "ADSP-2100 family code
compatible ... **with instruction set extensions**", and its instruction-set
section as "a superset of ADSP-2100 Family assembly language". An emulator
written to the 2100 baseline can therefore decode a 218x instruction as
something else without ever saying so.

At the top level it does not: every one of the **256** top-byte opcode classes
has a case in the dispatch, so nothing is silently swallowed wholesale.

### `tools/adsp_arith_oracle.py`, and what it establishes

The card's own G.711 encoder, TIKRNL PM `0x1810`, is shipped firmware — so it is
authoritative about what the hardware does — and G.711 is an ITU specification,
so its output is externally known for every input. Sweeping all 65,536 signed
inputs through it exercises the ALU, the shifter and the sequencer.

Exact code equality is the wrong bar and reporting it alone would mislead:
encoders differ legitimately in how a 16-bit input is folded onto a 13-bit
magnitude, and only 4,456 of 65,536 codes match a straightforward ITU reference.
The convention-free test is the reconstruction error, which no correct encoder
can get wrong by more than a quantisation step:

```text
worst reconstruction error          519, at input -31737
samples off by more than two steps    0
```

Every code lands in the correct segment. **The ALU, shifter and sequencer paths
that routine uses are faithful.** It does *not* exercise the MAC modes the
page-8 transmit filter is built from — `(SU)`, `(RND)`, `saturate MR` — which
remain unvalidated and are the natural extension of this tool.

(Recorded because it cost a false start: `Card()` does not load the kernel.
Without `card.boot()` PM `0x1810` is zero and the sweep reports a 99.6% mismatch
that is entirely the harness. A near-total mismatch against shipped firmware
means the harness, not the firmware.)

### A genuine 218x gap, with its bound

`docs/3110043388x_hardware/8xcompu.pdf` §2 documents an ADSP-218x extension the
emulator does not implement at all: **`BIASRND`**, a bit in the SPORT0
autobuffer control register that switches `RND` from unbiased to biased
rounding. `mac_round_unbiased()` is called unconditionally at all nine `RND`
sites, and neither the bit nor the register that holds it appears anywhere in
the core.

**This is a real fidelity defect and it is almost certainly not the page-8 bug.**
The manual is explicit about its scope: "This mode only has an effect when the
MR0 register contains 0x8000; all other rounding operations work normally." That
is one MAC result in 65,536 differing by one LSB. It cannot turn a carrier into
white noise, and saying otherwise would be Session 149's mistake again. Worth
fixing for correctness, not worth expecting anything from.

### Where the transmit question actually stands

Unchanged by any of this, and the honest ranking is unchanged with it: the
untraced hop is the one 153 named and did not take — the 60-word ring at cursor
`DM(0x0F67)` that PM `0x3a52..0x3a57` copies from, which is the first place in
the chain whose filler has not been identified. Sessions 138-142's work on what
feeds the modulator (the `DM(0x2140)` gate, the role word, the script tables)
also needs re-deriving now that page 8 runs at one publish per sample, because
every measurement in them was taken through the pacing defect.

## Session 155: the 0x0F67 ring, and an audit of the emulator against the 218x manuals

### The ring

`DM(0x0F67)` is the cursor of a 60-word buffer at **`DM(0x09C0..0x09FB)`**
(`L7 = $003C`, base `0x09C0`, which satisfies the DAG base rule). PM
`0x3a52..0x3a58` copies three words a frame out of it, and its sole cursor
writer is `0x3a58`, 16,827 times, `dmovlay=0`.

An ownership survey over the buffer gives two writers and no others:

```text
PM 3753   26,016 writes   every one 0x0000        (the CNTR=3 clear at 0x3750)
PM 3792   24,465 writes   9,920 distinct, RMS 3,080, range [-8494, +8381]
```

So `0x3792` is the generator, and **the noise is already fully present there** —
same distinct-value spread and amplitude as everything downstream. The chain
still has not reached a stage that holds a signal.

**A caveat that has to be recorded, because it invalidates a method used above.**
Both writers run at `pmovlay=0`, i.e. out of the base program image, yet the
end-of-call PC histogram disassembles `0x3792` as `AR = AX0 + AY0`, which stores
nothing. The image at that address is therefore **not** what ran — resident PM
above `0x2000` is rewritten during the call. Disassembly taken from an
end-of-call histogram is not evidence about what executed at a given address,
and Sessions 151-153 leaned on exactly that for `0x1746`, `0x1769` and `0x2ced`.
Those three are below `0x2000` and are not affected; anything above it needs a
live PM dump at the moment of interest. That is the first thing to do next.

The watch line now carries `pmov=` alongside `ov=` so the question can be asked
at all.

### The audit: what else the emulator gets wrong

Checked against `docs/3110043388x_hardware/` and the 218xN datasheet:

| area | verdict |
|---|---|
| opcode coverage | all **256** top-byte classes have a case; nothing swallowed wholesale |
| DAG modulo addressing | **correct**. `mask_table` implements the manual's rule exactly — base = I masked to a multiple of 2^n with 2^(n-1) < L <= 2^n — and `modified_address()` is `(I + M - B) mod L + B`. Verified for the L values this chain actually uses (0x14, 0x28, 0x3C, 0x40, 0x4A) |
| MAC fractional/integer placement | **correct**; `MSTAT_INTEGER` selects the shift at every MAC site |
| MAC unbiased rounding | **correct** per 8xcompu.pdf §2 |
| PM data read | **correct**; the upper 16 bits go to the register |
| ALU / shifter / sequencer | **validated** by `adsp_arith_oracle.py`, 65,536 inputs, no code outside its segment (Session 154) |
| **PX register width** | **was wrong — fixed here** |
| **BIASRND** | **missing**; bounded to MR0 == 0x8000 (Session 154) |
| MAC `(SU)`, `(RND)`, `saturate MR` | still unvalidated; the oracle does not reach them |

### The PX defect

`8xmemory.pdf` §8: "The PX register still latches the **lower eight bits** of the
program memory word." The emulator stored the whole 24-bit word in `px` on every
PM read, and `pgm_write_dag2()` then wrote `(val << 8) | px` **unmasked**, so a
PM write following a PM read ORed the high bits of the previously read word into
the word it stored. `wr_px()` was equally unmasked.

This is in the transmit path — PM `0x3758..0x375f`, reached from `0x3744`, does
a PM read at `0x375a` and a PM write at `0x375b` in consecutive instructions.

Fixed: `px` is eight bits at the read, at the write and at `wr_px()`.

**And it changes nothing measurable.** Same run, after the fix: answerer 0.071
at 3094 Hz, caller 0.081 at 1953 Hz, both ceilings `0x00b0`, caller RMS 776.6 —
identical to before. The G.711 oracle is unchanged and 388 tests pass. It is a
correctness fix and it is not the page-8 defect. Recorded that way so the next
session does not expect anything from it.

## Session 156: the live instruction at PM 0x3792, and structured inputs to a MAC

155 said resident PM above `0x2000` is rewritten during the call and that
end-of-call disassembly is not evidence. Confirmed, and the mechanism is now
visible: `--watch-exec` already prints the live opcode in its `op=` field, so
the instruction that actually ran at a PC is obtainable without new tooling.

**PM `0x3792` holds a different instruction in each phase**, all at `pmovlay=0`:

```text
before page 8   op 12faa5   DM(I1,M1) = AR, SR = EXPADJ     48,384 executions
during page 8   op 6800c5   DM(I1,M1) = MR1, NOP (MAC)      24,465 executions
```

24,465 is exactly the ring-write count 155 attributed to that PC, so the page-8
instruction is the one that fills the ring, and **it stores `MR1` — a MAC
result**. The `AR`-differencing loop 155 was reading is the *other* phase's code.
(The end-of-call histogram showed a third thing again, `AR = AX0 + AY0`. Three
different instructions at one address is the clearest possible statement that
static disassembly of this region is worthless.)

The same watch-limit trap caught this twice and is worth stating as a rule: a
limit of 300 was consumed entirely before page 8 loaded, and reported every
operand as zero. **Any `--watch-exec` or `--watch-dm-writes` limit on this rig
must be large enough to survive V.8 and INFO, or segmented by `TrnProgress`
after the fact.** Both of Sessions 153 and 156 lost a run to it.

### The inputs are structured; the output is not

Operands over the 24,465 page-8 executions, reached from PM `0x3791`:

```text
ax0   1 distinct value      constant 1698
ax1   4 distinct values     0 .. 8192
ay0   9 distinct values     -6476 .. +6476
--
mr0   4,071 distinct        RMS 18,567
mr1   9,920 distinct        RMS  3,080, range [-8494, +8381]   <- stored
```

A constant, a four-point set and a nine-point set are **symbol-like**: that is
what the input side of a modulator should look like. The MAC turns them into
9,920 distinct values.

That is not automatically wrong — modulating a small constellation onto a
carrier legitimately produces many values — but it locates the transformation
precisely, and it is the first stage in this whole chain whose input holds
structure.

### A correction to Session 153

153 concluded "the overlay is generating noise at its own output" and that the
transport was closed. That needs qualifying in both halves.

The generator's output is **not white**. Autocorrelation of the stored `MR1`
sequence over page 8:

```text
lags 1-10:  +0.22  -0.37  +0.06  +0.00  -0.17  +0.03  -0.05  -0.11  +0.02  -0.03
```

against the ±0.008 white-noise floor, and against the `DM(0x0b92)` stream
downstream which peaked at +0.048. So there is real short-range structure at the
generator that is **not present two hops later**. 153 measured the ring-copied
streams and read their whiteness back onto the generator; the generator is
broadband by the concentration metric (0.101, against 0.074 downstream and 0.071
at the line) but it is measurably not the same signal.

Something between the generator and `DM(0x0b92)` is destroying what structure
there is. The ring receives **26,016 zeros** from PM `0x3753` and **24,465 data
words** from `0x3792` — very nearly one zero per sample — and the reader takes
exactly 50,481, the sum. Zero-stuffing ahead of an interpolating filter is how
interpolation is supposed to work, so this may be correct; but whether the zeros
and the data land in the slots they are meant to, in the order they are meant
to, has not been checked, and a cursor that is not reset between the clear and
the fill would interleave them instead of overwriting.

**Next, and now well-posed:**

1. dump the ring's 60 words in slot order across a few frames and see whether
   data and zeros land where an interpolator would want them — this is the
   direct test of the paragraph above, and it is a `--watch-dm` on
   `0x09C0..0x09FB` with the cursor logged alongside;
2. extend `adsp_arith_oracle.py` to the MAC modes. This was ranked third before
   and moves to second: the stage that turns structure into broadband is a MAC,
   `MR1` is the high word of its result, and `(SU)`, `(RND)`, `saturate MR` and
   the fractional shift that decides which 16 bits `MR1` holds are precisely
   what has never been validated.

## Session 157: the ring is not interleaved — the generator stops halfway through page 8

156 proposed that the ring's zeros and data might be interleaved by a cursor
that is not reset between the clear and the fill. **Disproved.** Dumping every
write to `DM(0x09C0..0x09FB)` in log order shows both writers walking all 60
slots contiguously and ascending, and the two never alternate. They run in three
long phases, timed on the `cyc=` counter:

```text
PM 3792 (data)    24,246 writes    0.00 .. 44.74 Mcyc
PM 3753 (zeros)   26,016 writes   44.75 .. 95.51 Mcyc
PM 3792 (data)       219 writes   95.52 .. 95.81 Mcyc
```

**For the whole second half of page-8 residency the ring receives nothing but
zeros**, at 512 writes/Mcyc against the data phase's 542 — the same loop running
at the same rate down a different branch. This is what Session 150 was looking
at from the other end: the answerer goes silent and then sits on a constant,
which is the copy chain draining a ring that has stopped being refreshed.

So the transmitter does not emit noise for the whole page. It emits something
for half of it and then deliberately emits zeros.

### The page-8 code, live

`0x3790` swaps per phase exactly as `0x3792` does, all at `pmovlay=0`:

```text
op 20410f  x48,384   MR = MR + MX1 * MY0 (RND)     before page 8
op 20a40f  x24,594   MR = MR1 * MY0 (SU)           page 8, data phase
op 1b7a40  x 7,261   IF EQ JUMP $37A4
```

So the page-8 transmit sample is `MR1` of **`MR = MR1 * MY0 (SU)`**, stored by
`DM(I1,M1) = MR1` at `0x3792` — a self-multiplying accumulation with an unsigned
Y operand, and one of the three MAC modes Session 154 flagged as never
validated.

### `(SU)` is correct, checked against the manual

That looked like the answer and it is not. `8xcompu.pdf` Table 2-8 gives the
convention explicitly:

```text
X Input    Y Input    Code Example
Signed  x  Signed     MR=MX0*MY0(SS)
Unsigned x Signed     MR=MX0*MY0(US)
Signed  x  Unsigned   MR=MX0*MY0(SU)
Unsigned x Unsigned   MR=MX0*MY0(UU)
```

The emulator's `case 0x05<<13` takes X signed and Y unsigned, and `0x06<<13`
takes X unsigned and Y signed. Both match, and opcode `0x20a40f` does decode to
case 5. `(SU)` is faithful. It joins the DAG, the fractional placement, unbiased
rounding and the PM read in the "checked and correct" column.

### A method correction

Session 156's timings, and the first version of this session's, were wrong:
they stamped events with the last `TrnProgress` marker, which stops updating
once the state machine parks at its ceiling, so everything afterwards collapses
onto one timestamp and the generator looked like it stopped at 10.1 s. **Use
`cyc=` for any timing on this rig.** It is monotonic and independent of the
state machine. The table above is on the cycle clock; the earlier sample-based
version of it was an artifact.

### What is now the question

Not "why does the transmitter emit noise" — it emits zeros for half the window,
and something with structured MAC inputs for the other half. The question is
**what branch takes the loop into the zero path around 44.7 Mcyc into page 8 and
keeps it there.** `IF EQ JUMP $37A4` at `0x3790` is a candidate for that branch,
and the loop head at `0x378e..0x378f` reads `DM(0x076F)` in the pre-page-8
variant of this code, so the live page-8 form of `0x378c..0x3791` and whatever
DM word it tests is the next thing to read.

The MAC-mode oracle stays on the list but drops back: the one mode this stage
actually uses has now been checked by hand and is right.

## Session 158: the gate is a vector word, `DM(0x0B72)`

157 asked what branch takes the transmit loop into the zero path halfway through
page 8. It is not a branch, and 157's "the same loop taking a different branch"
— inferred from the two writers having nearly equal write rates — is withdrawn.

Climbing the call chain with `--watch-exec`, live ops only, each stage segmented
by the ring's writer phases on the `cyc=` clock:

```text
3739: I4 = DM($0B72)          ; a vector word
373a: JUMP (I4)               ; indirect
```

`0x373a` is reached from `0x3739` in both phases and jumps to **`0x373b`** (the
modulator) or **`0x3746`** (the zero path) according to what `DM(0x0B72)` holds.
`0x3746` is entered from `0x373a` 8,672 times, which is the zero phase's whole
count. The two paths are separate routines, not two arms of one loop: the data
path is `0x373b..0x3745` ending in `RTS`, and the zero path is
`0x3746..0x3750`, pointer bookkeeping on `DM(0x0EF1)`/`DM(0x0EF2)` with
`M7 = +3/-3` followed by `CNTR = $0003` and three zero stores. Both are called
with `ret=3624`.

### The whole gate is six writes

`DM(0x0B72)` has exactly one writer during page 8, PM **`0x36b0`**, and it runs
six times in the entire residency:

```text
value    Mcyc into page 8
0x373b     0.00      modulate
0x373b     1.04
0x373b    40.52
0x373b    42.95
0x3746    44.37      <- silence
0x373b    95.14      <- modulate again
```

Those match the ring's writer phases (44.75 and 95.52 Mcyc) to within the
sampling. **One word, written six times, decides whether the card transmits.**

### The modulator, and what it is fed

The data path is a double-precision polyphase FIR, `CNTR = $0003` outputs per
call, coefficients from program memory:

```text
3760..3767  prologue: cursors from DM(0x0EF1)/DM(0x0EF2), lengths from DM(0x2136)
3768        DO $3792 UNTIL NOT CE
378c          MR = MR + MX0 * MY0 (SS), MX0 = DM(I0,M1), MY0 = PM(I7,M6)
378d          MR = MR + MX0 * MY0 (SU), MX0 = DM(I0,M1), MY0 = PM(I7,M6)
378e          MR = MR + MX0 * MY0 (SS), MX0 = DM(I0,M0), MY0 = PM(I7,M6)
378f          MR = MR + MX0 * MY0 (RND), MY0 = PM(I7,M4)
3790          MR = MR1 * MY0 (SU)
3792          DM(I1,M1) = MR1
```

The `(SS)`/`(SU)` pairing on consecutive taps is the textbook 32x16
multiprecision product from 8xcompu.pdf's own description of those modes, so the
mode selection reads as correct firmware rather than anything anomalous.

And the symbol source is clean. At `0x373c` the operands are **two-valued**:
`ax0` is `0x11e4` (+4580) 3,768 times and `0xee1c` (-4580) 3,706 times — an
antipodal pair, near enough evenly split. That is what a V.34 training sequence
looks like at the input to a shaping filter, and it is the strongest evidence yet
that the *modulator's* input is not the problem.

### What this does and does not establish

It establishes the mechanism completely: the transmitter goes quiet because a
vector word is repointed, by one routine, at one moment, and the copy chain then
drains a ring nobody is refreshing — which is Session 150's constant DC tail.

It does **not** establish that this is a defect. V.34 has defined quiet periods,
and an answerer that stops transmitting partway through a phase may be doing
exactly what the recommendation says. Nothing here should be read as "the
firmware wrongly silences the transmitter" until what PM `0x36b0` is responding
to has been read.

**Next:** `--watch-exec` on PM `0x36b0` and its caller, segmented the same way,
to see what it tests before writing `0x3746`. That is one hop, and it is the
first hop in this whole chain where the answer could legitimately be "the
firmware is right and the peer never gave it what it was waiting for".

## Session 159: PM 0x36b0 tests nothing — it is a table-driven vector load

158 ended expecting `0x36b0` to test something before silencing the
transmitter, and flagged that the answer might be "the firmware is right". It is
neither. `0x36b0` is the last store of a **vector-table loader**, and there is no
test anywhere in it. Live ops, called from `0x367c` with `ret=367d`:

```text
36a6: DM($0A42) = SR0                          ; save the selector
36a7: I4 = $20D3                               ; a PM vector table
36a8: I0 = $0B70                               ; destination DM(0x0B70..0x0B72)
36a9: SE = $0002
36aa: CNTR = $0002
36ab: DO $36AF UNTIL NOT CE
36ac:   SR = LSHIFT SR0 (LO), AY1 = PM(I4,M5)  ; next field of SR0, next table entry
36ad:   DM(I0,M1) = AR, AR = SR1 + AY1         ; store previous vector, index the table
36ae:   I5 = AR
36af:   NOP (MAC), AR = PM(I5,M4)              ; dereference to a routine address
36b0: DM(I0,M1) = AR                           ; the third vector -> DM(0x0B72)
```

It walks successive fields of `SR0` (shifted by `SE = 2` each pass), uses each to
index the PM table at `0x20D3`, dereferences, and writes **three** consecutive
vectors into `DM(0x0B70..0x0B72)`. `DM(0x0B72)` — 158's gate — is simply the
third of them. The routine runs six times in page-8 residency, which is exactly
the six writes 158 counted.

So the transmitter is not being silenced by a decision taken here. It is being
reconfigured: the firmware loads a different set of three vectors, and one of
them happens to be a routine that emits zeros.

### The selector, and the moment it changes

`SR0` on entry, against the ring's writer phases on the same clock:

```text
  0.38 Mcyc   sr0=9601    modulate
  1.42 Mcyc   sr0=b700    modulate
 40.90 Mcyc   sr0=9b00    modulate
 43.33 Mcyc   sr0=9400    modulate
 44.75 Mcyc   sr0=a700    <- silence, and the ring's zero phase starts at 44.75
 95.52 Mcyc   sr0=9600    modulate, and the ring's data phase resumes at 95.52
```

The phase boundaries coincide with the loader's calls to the sample. **`SR0 =
0xa700` is the mode that silences the transmitter**, and the five other values
all select it.

`0x36a6` stores that selector to **`DM(0x0A42)`**, so the mode word is named,
addressable and watchable — which is what makes the next hop cheap.

(The exact field extraction — which bits of `SR0` index which of the three
vectors — is not pinned down here. The shift-and-dereference structure is
measured; the bit positions are arithmetic that has not been checked against a
second run, and nothing below depends on them.)

### What this does and does not settle

It settles 158's open question in the narrow sense: nothing at `0x36b0` waits on
the peer, so the silence is not this routine deciding the far end failed to
respond. The decision is upstream, in whatever computes `SR0`.

It does **not** yet say the behaviour is wrong. A modem that reconfigures its
transmit vectors partway through a training phase and goes quiet is doing
something V.34 explicitly provides for, and the run resumes modulating at 95.52
Mcyc, which is what a timed quiet period would look like. The question is
whether 50 Mcyc of silence is the intended length and whether `0xa700` is the
mode the state machine should be in at that point.

**Next:** `--watch-dm-writes 0x0a42` armed on `0x0261`, plus the caller at
`0x367c`, to find what computes the mode. `DM(0x0A42)` is one word with six
writes a call, so this is a small trace, and it lands directly in the state
machine Sessions 138-147 were working in — which now needs re-deriving anyway,
since every measurement in those was taken through the pacing defect.

## Session 160: the mode word is `DM(0x0F59)`, written by PM `0x3669`

159 named `DM(0x0A42)` as the selector the vector loader saves. It is not the
source — it is the **cached copy**. The code above the loader is a change
detector:

```text
3675: AY1 = DM($0F5B)
3676: AX1 = DM($0A43)
3677: AR = AX1 XOR AY1
3678: IF NE CALL $3692        ; reload the other vector set only if it changed
3679: SR0 = DM($0F59)         ; the requested mode
367a: AY1 = DM($0A42)         ; the mode currently loaded
367b: AR = SR0 XOR AY1
367c: IF NE CALL $36A6        ; reload the transmit vectors only if it changed
```

It runs 362 times in page-8 residency and calls the loader six times, which is
why 158 saw six writes to `DM(0x0B72)`: the vectors are reloaded on change, not
per frame. **`DM(0x0F59)` is the mode.**

`DM(0x0F59)` has exactly one writer, PM **`0x3669`**, 362 writes:

```text
0x9b00  x343      0xa700  x7      0x9400  x5
0xb700  x4        0x9600  x2      0x9601  x1
```

and in order against the ring's phases:

```text
 0.00 Mcyc  9600 / 9601      modulate
 1.10 .. 37.61  b700         modulate
40.58 .. 42.70  9b00         modulate  (343 writes -- a busy poll of the same value)
43.xx           9400         modulate
44.75           a700         SILENCE, held to ~85 Mcyc
95.52           9600         modulate again
```

### The complete chain, mode word to line

```text
PM 0x3669  ->  DM(0x0F59)                    the transmit mode
  PM 0x3675..0x367c   XOR against DM(0x0A42), reload on change
    PM 0x36a6..0x36b0 vector loader, PM table 0x20D3 -> DM(0x0B70..0x0B72)
      PM 0x373a       JUMP (DM(0x0B72))
        0x373b   modulator: double-precision polyphase FIR, 3 outputs/call
        0x3746   zero writer: 3 zeros/call
          -> ring DM(0x09C0..0x09FB), 60 words, cursor DM(0x0F67)
            -> PM 0x3a52 copy, 3 words/frame -> DM(0x0B92..0x0B94)
              -> PM 0x2ced gain/shift -> DM(0x3FA7..) mapping-frame block
                -> PM 0x1742 copy -> history DM(0x3680..0x36C9), 74 words
                  -> PM 0x17A6 interpolating FIR
                    -> 20-word ring, credit DM(0x3761)
                      -> PM 0x1746 publisher -> DM(0x3764) -> the line
```

Every stage is now identified, and each one is a copy, a filter or a table
lookup. Nothing in it decides anything except `0x3669`.

### Still not established: whether this is wrong

Repeating 158's and 159's caution because it still holds and it is the thing most
likely to be forgotten. `0xa700` silencing the transmitter for ~40 Mcyc mid-
training is exactly what a V.34 quiet period looks like from the inside, and the
run resumes afterwards. **Nothing measured so far shows the firmware
misbehaving.** What is shown is where the decision lives.

**Next:** the live op at PM `0x3669` and what it reads. That is one
`--watch-exec` and it is the last hop before this lands in the V.34 state
machine proper. Two things to carry into it:

- the mode is written 343 times with the same value in a 2 Mcyc window at 40.58
  Mcyc, so `0x3669` is inside a polled loop rather than a state transition, and
  the interesting writes are the six that *change* the value;
- Sessions 138-147 mapped that state machine through the pacing defect and need
  re-deriving before any of their block and script findings are relied on.

## Session 161: PM 0x3669 is a 13-word block copy, and the mode's real source is `DM(0x0B59)`

`0x3669` computes nothing. It is the store half of a guarded block copy that runs
once per frame:

```text
3661: I4 = $0B52                ; source block
3662: NOP (MAC), AR = DM(I4,M4) ; read the first word without advancing
3663: AR = AR + 0
3664: IF EQ RTS                 ; nothing pending -- 16,826 executions, most return here
3665: I7 = $0F52                ; destination block
3666: CNTR = $000D              ; 13 words
3667: DO $3669 UNTIL NOT CE
3668:   NOP (MAC), AR = DM(I4,M5)
3669:   DM(I7,M5) = AR
```

The guard at `0x3662..0x3664` runs **16,826** times in page-8 residency — once
per frame — and returns immediately unless `DM(0x0B52)` is non-zero. When it is,
thirteen words are copied from `DM(0x0B52..0x0B5E)` to `DM(0x0F52..0x0F5E)`. The
body executed **4,706** times, which is exactly 362 x 13, and 362 is the number
of `DM(0x0F59)` writes Session 160 counted. The arithmetic closes.

So `DM(0x0F59)` is the **eighth word of a copied parameter block**, and the mode
the transmitter obeys originates at **`DM(0x0B59)`**, latched by a flag in
`DM(0x0B52)`. This is a command-block handoff: something fills a staging block
and raises a flag, and the per-frame service copies it into the live parameter
area.

### The chain, updated

```text
??? -> DM(0x0B52..0x0B5E)                    a 13-word staging block + flag
  PM 0x3661..0x3669  per-frame copy          -> DM(0x0F52..0x0F5E)
    DM(0x0F59) is the transmit mode
      PM 0x3675..0x367c  XOR against DM(0x0A42), reload on change
        PM 0x36a6..0x36b0  vector loader     -> DM(0x0B70..0x0B72)
          PM 0x373a  JUMP (DM(0x0B72))       -> modulator 0x373b | zeros 0x3746
            ... ring, copies, interpolating FIR, publisher, line (Session 160)
```

Every stage from the staging block to the line is now identified and is a copy,
a filter, a table lookup or a flag test. The first thing in the chain that makes
a decision is whatever writes `DM(0x0B52..0x0B5E)`, and that has not been read.

### Note on the polling counts

Session 160 read 343 writes of `0x9b00` in a 2 Mcyc window as "a busy poll".
That is now explained precisely and was not quite the right description: the
staging flag stays raised across many frames, so the same block is re-copied
every frame, and `DM(0x0F59)` is rewritten with the value it already holds. The
change detector at `0x367c` is what stops that from reloading vectors 362 times.
The interesting events remain the six value changes.

**Next, and it is the last unknown in this chain:** `--watch-dm-writes` on
`0x0b52` and `0x0b59`, armed on `0x0261`. Those two words are where the V.34
state machine reaches the transmitter, so their writer is the state machine
itself — the code Sessions 138-147 were mapping, and which needs re-deriving
now that page 8 runs at one publish per sample.

## Session 162: the staging block is published by PM 0x2a75/0x2a7a

The last unknown in the transmit chain is read. `DM(0x0B52..0x0B5E)` is written
entirely from one place, the V.34 page's own code (`pmovlay=0`, PM above
`0x2000`):

```text
DM(0b52)  PM 2a75   16,826 writes   the request flag
DM(0b59)  PM 2a7a      362 writes   the transmit mode
DM(0b5a)  PM 2a7c      362
DM(0b5b)  PM 2a7e      362
DM(0b5e)  PM 2a80      362
DM(0b53..0b57)  PM 2a86  362 each
```

`DM(0x0B52)` is a **request flag**, not data: `0x0001` on 362 frames and
`0x0000` on the other 16,464. The 362 raises match the 362 block copies at PM
`0x3661` (Session 161) and the 362 writes to `DM(0x0F59)` (Session 160) exactly.
It is a one-shot handshake — the page publishes a transmit configuration, raises
the flag, and the per-frame service latches it.

`DM(0x0B59)` takes seven values in the whole of page-8 residency:

```text
 0.32 Mcyc  0x9600
 0.38 Mcyc  0x9601
 1.42 Mcyc  0xb700
40.90 Mcyc  0x9b00
43.33 Mcyc  0x9400
44.74 Mcyc  0xa700   <- the transmitter goes quiet
95.52 Mcyc  0x9600   <- and resumes
```

**PM `0x2a7a` writing `0xa700` at 44.74 Mcyc is the origin of the silence**, and
44.74 is the same cycle at which the ring's zero phase begins (Session 157).
The whole chain is now closed, from that store to the line.

### The complete transmit chain

```text
PM 0x2a75/0x2a7a  publish  -> DM(0x0B52) flag + DM(0x0B52..0x0B5E) block
  PM 0x3661..0x3669  per-frame latch     -> DM(0x0F52..0x0F5E)
    DM(0x0F59) = the transmit mode
      PM 0x3675..0x367c  XOR vs DM(0x0A42), reload on change
        PM 0x36a6..0x36b0  vector loader, PM table 0x20D3 -> DM(0x0B70..0x0B72)
          PM 0x373a  JUMP (DM(0x0B72))
            0x373b  modulator (double-precision polyphase FIR, 3 out/call)
            0x3746  zero writer (3 zeros/call)
              -> ring DM(0x09C0..0x09FB), cursor DM(0x0F67)
                -> PM 0x3a52 copy -> DM(0x0B92..0x0B94)
                  -> PM 0x2ced gain/shift -> DM(0x3FA7..)
                    -> PM 0x1742 copy -> history DM(0x3680..0x36C9)
                      -> PM 0x17A6 interpolating FIR
                        -> 20-word ring, credit DM(0x3761)
                          -> PM 0x1746 publisher -> DM(0x3764) -> the line
```

Ten stages, every one identified, and exactly one of them decides anything.

### What is established, and what is still not

**Established:** the mechanism, completely. The card stops transmitting because
its own V.34 page asks it to, through a documented-looking parameter handshake,
and everything downstream faithfully carries out that request.

**Not established, and this is now the whole question:** whether `0xa700` at
44.74 Mcyc is correct. Sessions 158-161 each ended with this caveat and it has
survived every hop. A V.34 answerer has defined quiet periods, the run resumes
modulating at 95.52 Mcyc, and nothing measured anywhere in this chain shows the
firmware doing something it was not asked to do.

**Next:** PM `0x2a70..0x2a7a` — what computes the value `0x2a7a` stores. That is
inside the V.34 state machine, so it is also the point where this work rejoins
Sessions 138-147, and their block and script findings need re-deriving first:
every one of them was measured while page 8 ran at 9-12 publishes per sample.

## Session 163: the transmit mode is script block field 0x00

The publisher reads, in full:

```text
2a74: AR = DM($224C)          ; a pending-request word
2a75: DM($0B52) = AR          ; the flag handed to the per-frame latch
2a76: AR = AR + 0
2a77: IF EQ JUMP $2A88        ; nothing pending, skip the block
2a78: DM($224C) = M0          ; consume the request
2a79: AR = DM($2137)          ; the mode
2a7a: DM($0B59) = AR          ; publish it
```

Two words, and both are already named in this log.

`DM(0x224C)` is a request flag internal to the page: `0x2a75` copies it straight
into `DM(0x0B52)` and `0x2a78` clears it, which is why the staging flag reads
`0x0001` on exactly 362 frames (Session 162).

**`DM(0x2137)` is field `0x00` of the current V.34 script block.** The
field-to-DM rule established in Sessions 114g-147 is `DM(0x2137 + field)`, so
`0x2137` is field zero, and the decoded blocks in this log carry it:

```text
block 0x1afa  state 0x0064    field 0x00 = 0x9601      (Session 114l)
block 0x1b36  state 0x0070    field 0x00 = 0x2700      (Session 114j)
```

`0x9601` is one of the seven mode values measured at `DM(0x0B59)` — the second
one, at 0.38 Mcyc. **Session 114l's unidentified "field 0x00" is the transmit
mode**, and that has been an open annotation in this log since 114j.

### What this means

The chain does not end in a computation. It ends in **firmware data**: the
transmit mode is a constant in the script block the sequencer is currently
executing, published to the transmit chain when the block arms itself.

So `0xa700` at 44.74 Mcyc is not a decision the page made about the peer. It is
what some block's field `0x00` says, and the card is doing what its script tells
it. The silence is *designed* — for whichever block that is.

That answers the question five sessions have been carrying, and it does so in
the direction the caveat kept allowing for: **nothing in the transmit path is
misbehaving.** The transmitter emits zeros because the script block it is in
says to emit zeros.

### Where the question goes

Straight back to Sessions 143-147, and to their exact subject: *which block the
sequencer is in, and why it does not advance.* The two are now connected — a
wait block that never exits is also a transmit mode that never changes, and
143's `0x1ae5`/`0x1ba5` self-branching wait blocks are the mechanism for both.

**Before any of that is used, it has to be re-derived.** Every block, script and
gate finding in 138-147 was measured while page 8 ran at 9-12 publishes per
sample (Session 149), and 147's own conclusion — that the wait block's test
passes because the correlator latches on broadband noise — was reasoning about a
signal the harness was mangling. The pacing fix changes the input to every one of
those measurements.

The concrete first step is small: identify which script block is current at 44.74
Mcyc, by reading the state field `DM(0x2147)` and the block cursor at the moment
`0x2a7a` publishes `0xa700`. That names the block whose field `0x00` is `0xa700`,
and from there the existing script decoders apply directly.

## Session 164: the ceilings are gone — both ends reach 0x00b0, and the blocker moves there

Chasing the mode word through instead of one hop at a time. The result changes
the status of the whole V.34 blocker.

### The answerer's full state/block trail (post-pacing-fix)

`DM(0x2147)` state, `DM(0x14A5)` block cursor, `DM(0x2137)` field 0 = transmit
mode, on the cycle clock from page-8 entry:

```text
 0.32  0x0062  1afa            0.37  0x0064  1b0f  mode 9601
 1.41  0x0070  1b24  mode b700 2.12  0x0071  1b30
 3.49  0x0072  1b39           37.93  0x0074  1b42
40.89  0x0076  1b6c -> 1ba5 -> 0x0090 1bb7  mode 9b00
       [40.89-42.69  1ba5 <-> 1bb7, the wait block of Session 143]
42.95  0x0092  1bc6           43.33  0x0094  1be4  mode 9400
43.40  0x0096  1bf3           43.46  0x0097  1bfc
43.57  0x0098  1c08           44.65  0x009a  1c14
44.74  0x00a0  1c32  mode a700  <- the quiet sequence begins
44.80  0x00a2  1c44           45.10  0x00a4  1c50
45.97  0x00a6  1c5c           47.40  0x00a8  1c74
47.59  0x00aa  1c80           85.47  0x00ac  1c95
95.51  0x00b0  1cb0  mode 9600  <- transmit resumes
```

**Twenty states.** `0x0090` is passed through in 2 Mcyc. The `0x0060`/`0x0090`
ceilings that Sessions 137-148 were built on **no longer exist** — they were an
artefact of the transmitter being decimated by ten (Session 149).

The silence is settled with it: `0xa700` is state `0x00a0`, and `0x00a0..0x00ac`
is a **designed quiet sequence** of six states which the script exits at `0x00b0`
by restoring mode `0x9600`. Nothing there is a fault, exactly as Sessions 158-163
kept allowing for.

### The new blocker: the page stops servicing the transmitter at 0x00b0

At 90 seconds both ends reach `0x00b0` — the answerer at 10.10 s, the caller at
9.54 s. Then:

- the **caller** waits 0.76 s, falls back to `0x0024` -> `0x002c` and restarts
  V.8/INFO, where it stays for the remaining 80 s;
- the **answerer** parks at `0x00b0` and its transmit chain **halts entirely**:
  last ring write at 95.81 Mcyc of a 60 s run, no further `DM(0x224C)` requests,
  and the line freezes on one sample value (RMS 1052, 100% below 300 Hz) for
  36 s.

So `0x00b0` sets mode `0x9600` (modulate), the modulator runs for 0.3 Mcyc, and
then the page stops publishing at all. That is the whole remaining gap on this
path, and it is a state the project has never previously reached in loopback.

### The 5.8 G instructions at 0x00b0 are ours, and the ceiling must stay at 20000

`--pc-histogram-state 0x00b0` reports 5,814,128,838 instructions over 290,400
samples, spinning in the kernel foreground at PM `0x051b..0x0520` (134 M) and
`0x00ff..0x0109` (83 M). That is not a firmware runaway: 290,400 x 20,000 is
5,814,128,838 exactly. It is `EICON_V34_PUBLISH_MAX_CYCLES` being spent in full
on every tick where the page publishes nothing — the fallback arm of the Session
149 pacing fix.

Lowering it is not the answer. At `EICON_V34_PUBLISH_MAX_CYCLES=4125` both ends
regress hard, caller to `0x0060` and answerer to `0x0071`, so the headroom is
load-bearing during the phases that do publish. Default stays 20000. What it does
cost is wall time whenever the page is quiet, which is worth knowing when a run
seems slow.

### Where this leaves the queue

The V.34 blocker is not "phase 2 never completes" any more. It is: **the
answering page stops publishing transmit data on entry to `0x00b0`, and the
calling end times out 0.76 s later and restarts.** Everything in Sessions 137-148
about ceilings, wait blocks, correlator thresholds and role words describes a
regime that no longer exists and should not be carried forward.

## Session 165: why the page stops publishing at 0x00b0 — it is the pacing fix starving the foreground

The answer is ours, not the firmware's.

`--pc-histogram-state 0x00b0` against a whole-residency histogram, and against
an `EICON_V34_PUBLISH_PACED=0` control:

```text
                        unpaced      paced (whole)   paced, at 0x00b0 only
PM 02a9  kernel fg      344,933          39,910               56
PM 02b7  selected fg    243,576          39,260                -
PM 1746  publisher      243,232          39,263               60
PM 051b  spin loop            0      45,024,810      134,022,497
PM 03dc  wait task            0      27,872,502       82,966,308
```

The V.34 page's own per-frame code runs **25-27 times in 290,400 samples** at
`0x00b0`, and PM `0x2e2d`/`0x2ddb` (state and block cursor) not at all. The page
is not stalling on anything — **it is not being dispatched.**

The cause is the Session 149 mechanism. `adsp2181_modem_sample()` runs its
continuation only `if (a->idle)`. Stopping the core at the transmit publish
leaves it mid-frame, never idle, so the continuation is skipped on every paced
tick, and the leftover budget goes into a background wait task (PM
`0x03dc`/`0x03e9` polling `CALL $01B2`) that never runs at all unpaced. The
kernel foreground is starved 8.6x across the call and effectively to zero at
`0x00b0`.

### Two fixes tried, both worse — recorded so they are not retried

**Latch instead of stop.** `adsp2181_latch_dm_write()` (new, in the core) takes
the *first* value a frame writes to the transmit word and lets the frame run to
completion, which should give one sample per tick without touching execution
flow. It restores the foreground exactly as predicted — PM `0x02a9` 922,329, PM
`0x051b` **0** — and the state machine **regresses to `0x0060`/`0x0072`**, back to
the pre-149 ceilings, with concentration back at 0.094.

That is the important negative: **Session 149's gain was not sample selection, it
was bounding the page to one pass per tick.** Latching keeps ten passes and picks
the first sample; the page still runs ten times too fast and fails exactly as it
did before. The mechanism is a clock, not a multiplexer.

**Stop, then drive the skipped continuation.** Calling the continuation
explicitly after the stop fires is worse still: both ends stop at `0x0052` and the
V.34 overlay never becomes resident at all.

Reverted; `EICON_V34_PUBLISH_PACED=1` remains the default and both ends reach
`0x00b0` again on re-verification. `EICON_V34_PUBLISH_LATCH` is kept, defaulting
**off**, because the A/B above is worth being able to reproduce cheaply.

### What this leaves

The blocker is now precisely stated and it is a harness problem with two
requirements that the current mechanism cannot satisfy at once:

1. the page must execute about **one pass per 8 kHz sample** — stopping at the
   publish achieves this and nothing else tried does;
2. the kernel foreground continuation must still run every sample — the stop
   prevents this, and neither driving it manually nor letting the frame run to
   completion works.

The right shape is probably to make the *core* honour both: let
`adsp2181_modem_sample()` treat a stop-on-publish as a yield rather than as a
mid-frame halt, resuming the frame after the continuation instead of restarting
it. That is a change to the C entry point rather than to the shim, and it is the
one thing on this path that has not been tried.

## Session 166: the yield works mechanically and still loses — and that casts doubt on 0x00b0

`adsp2181_yield_on_stop()` makes `adsp2181_modem_sample()` treat a
stop-on-publish as a yield: run the continuation, then put the core back where
the frame stopped so the next sample's SPORT interrupt lands on top of the
page's own foreground, as hardware does.

**First attempt failed for a reason worth keeping.** A plain
"push return_pc, jump to the continuation, restore PC" leaves both ends at
`0x0052` with the V.34 overlay never resident. The continuation is an ordinary
call, not an interrupt: it runs with the page's registers live and destroys the
computation the publish interrupted. The core saves nothing for it.

**With a full context save it works mechanically.** Saving and restoring both
register banks, `i`/`m`/`l`/`lmask`/`base`, the loop, counter, PC and status
stacks and all the status words around the continuation gives:

```text
                     stop only     stop + yield     latch      unpaced
PM 02a9  kernel fg      39,910          665,543   922,329      344,933
PM 051b  spin        45,024,810              0          0            0
PM 1746  publisher       39,263          457,005   651,141      243,232
deepest (answerer)       0x00b0           0x0090    0x0072       0x0090
deepest (caller)         0x00b0           0x0041    0x0060       0x0060
```

The starvation is completely fixed — foreground healthy, spin gone — **and the
state machine is worse.** The answerer reaches `0x0074`/`0x0090`, falls back to
`0x0024` and cycles in V.8/INFO for the rest of the run.

### The uncomfortable conclusion

Three independent ways of restoring the kernel foreground — latching, the naive
yield, the context-saving yield — all cost state progress, and the only
configuration that reaches `0x00b0` is the one where the foreground is starved
8.6x and the page is barely serviced.

That is not what a real fix looks like. **The `0x00b0` result of Sessions 164-165
should be treated as suspect**: if the states advance furthest precisely when the
foreground that would gate them is not running, the likeliest reading is that
they are advancing on timers with nothing checking them — the same pattern
Session 102 named on the answering side and Session 150 found behind the deep
states there. It is not established that `0x00b0` under stop-pacing is closer to
a connection than `0x0090` under a healthy foreground.

Defaults are unchanged and re-verified: `EICON_V34_PUBLISH_PACED=1`,
`EICON_V34_PUBLISH_LATCH=0`, `EICON_V34_PUBLISH_YIELD=0`, both ends at `0x00b0`.
All three mechanisms are kept behind their flags because the comparison above is
the most informative measurement on this path and should stay one command away.

**What to do next is a decision, not a probe:** either establish that the
stop-paced `0x00b0` trail is real by finding something in it that depends on
received signal, or accept the yield's healthier execution profile as the
correct base and re-attack from `0x0090` with the foreground running. The second
is the more honest starting point; the first is cheaper to test and should go
first — the answerer's `0x00a0..0x00ac` quiet sequence is timed, so a run with
the *caller* silenced deliberately would show whether the answerer's trail
changes at all. If it does not, the trail is timers.

## Session 167: the 0x00b0 trail is signal-driven — 166's caution withdrawn

166 suspected the stop-paced `0x00b0` trail of being timers, on the grounds that
it appears only when the kernel foreground is starved. The control settles it.

`EICON_FORCE_DM=0x3fb4=0x0000@0x0261` on the calling end zeroes the word the
harness reads the line sample through, but only once page 8 is resident, so V.8
and INFO proceed normally and the caller goes silent exactly when training
starts (caller page-8 TX RMS 776.6 -> 19.5). The answerer, unmodified:

```text
caller transmitting   0x0060 0x0064 0x0070 0x0071 0x0072 0x0074 0x0090
                      0x0092 0x0097 0x0098 0x00a0 0x00a4 0x00a6 0x00a8
                      0x00aa 0x00ac 0x00b0
caller silent         0x0060 0x0064 0x0070 0x0071 0x0072 0x0074 0x0090
                      0x0060 0x0064 0x0070 0x0071 0x0072 0x0074 0x0090   (cycles)
```

**Everything past `0x0090` requires the peer's signal.** With the caller silent
the answerer cycles back to `0x0060` indefinitely and never publishes `0x0092`.
So the trail is real, it is driven by received signal, and Session 166's
suspicion is **withdrawn**: `0x00b0` under stop-pacing is genuine progress, not a
state machine running on timers.

### And that explains why the yield and the latch lose

They do not lose because the foreground matters. They lose because they silence
the **caller**:

```text
                caller page-8 TX RMS    answerer deepest
stop (default)               776.6              0x00b0
stop + yield                   6.8              0x0090
latch                         19.5              0x0072
caller forced silent          19.5              0x0090  (the control above)
```

Under the yield the caller transmits nothing, and the answerer then behaves
exactly as it does in the deliberately-silenced control — stalling and cycling
around `0x0090`. The same for the latch. The foreground being healthy is
irrelevant to the outcome; what decides it is whether the calling end puts a
signal on the line at all.

So the causal chain, end to end:

```text
stop-pacing bounds the page to one pass per tick
  -> the calling end transmits at all (Session 149: RMS 5.0 -> 776.6)
    -> the answering end advances past 0x0090 on that signal
      -> 0x0092 .. 0x00b0, including the designed quiet sequence at 0x00a0
```

### The open problem, stated exactly

Two things are needed together and no mechanism yet achieves both:

1. **the calling end must transmit** — only stopping the core at the publish has
   ever produced this, and both alternatives silence it;
2. **the kernel foreground must run every sample** — the stop prevents it, which
   is why the page stops being serviced at `0x00b0` (Session 165).

Since (1) is now proven to be what drives the answerer forward, it takes
priority, and the default stays as it is. The question to answer next is
**why the caller only transmits under the stop** — that is a property of the
calling side's page, it has never been examined directly, and it is the one link
in the chain that is still unexplained rather than merely unfixed.

## Session 168: the caller only transmits under the stop because only the stop produces a carrier

167 left one link unexplained. It is the signal itself.

Comparing the **answerer's** transmit in the first 0.30 s of page 8 — before the
caller has transmitted anything, so the starting conditions are identical and
this is the only input the calling end has:

```text
config    rms      conc    peak      top bins
stop     1031.6   0.130   1953 Hz   1938:1.0%  1953:2.2%  1969:1.5%  2672:1.2%  3672:1.3%
yield    1034.5   0.095   2812 Hz   1953:0.8%  2109:0.9%  2500:0.9%  2797:1.0%  2812:1.1%
latch    1068.7   0.094   2953 Hz    188:0.8%  1719:0.8%  2938:0.8%  2953:0.8%  3859:0.8%
```

Under the stop the answerer emits a **carrier**: a coherent three-bin cluster at
1938/1953/1969 Hz carrying 4.7% of the band between them. Under the yield and the
latch the spectrum is flat — every top bin around 0.8-1.1%, no cluster, and the
nominal "peak" wanders to wherever the noise happens to be highest.

**1953 Hz is the V.34 carrier.** The recommendation puts it at 1959 Hz for the
3429 baud symbol rate, and the analysis bins here are 15.6 Hz wide, so
1953 +/- 8 Hz is that carrier and not a coincidence. The caller's own page-8
transmit peaks at the same 1953 Hz once it starts (Session 157).

### Why the stop is the only mechanism that produces one

Under the latch the page still runs about ten passes per 8 kHz sample and the
harness takes the first output of each group. That decimates cleanly enough as a
*sampling* strategy, which is why Session 165 expected it to work — but the
modulator's own state advances ten times per sample either way, so its carrier
phase rotates ten times too fast and nothing coherent survives at 8 kHz. The
stop is the only mechanism that throttles the page's *internal* rate rather than
just choosing among its outputs, which is the same point Session 165 made about
it being a clock and not a multiplexer, now visible in the spectrum.

### The chain, complete

```text
stop-pacing bounds the page to one modulator output per 8 kHz sample
  -> the answering end emits a coherent carrier at 1953 Hz             (168)
    -> the calling end's 0x0060 wait block detects it and exits
      -> the calling end transmits, page-8 RMS 5.0 -> 776.6            (149)
        -> the answering end advances past 0x0090 on that signal       (167)
          -> 0x0092 .. 0x00b0, including the designed quiet sequence   (164)
```

Every link is now measured, and the two ends are mutually dependent: neither
advances unless the other is emitting something detectable, which is why every
attempt to fix the foreground starvation collapsed the whole trail at once.

### What remains

The carrier is real but weak: 0.130 concentration against **0.818** for a live
modem on the same metric, with 4.7% of the band in the carrier cluster where
hardware puts 81% in 2400-3000 Hz. So the answering end is emitting a detectable
carrier buried in a great deal of broadband energy, and the caller detects it
anyway — which says the remaining gap to a connection is signal *quality*, not
signal *presence*.

That reframes the next question usefully. It is no longer "why is there no
signal" but "what is the broadband floor made of, given the modulator's symbol
input is a clean antipodal pair (157) and its arithmetic is faithful (154, 157)".
The most likely remaining candidate is the one structural thing still known to be
wrong: the page runs one pass per *sample* under the stop, but a V.34 modulator
at 3429 baud needs its interpolating filter run at the sample rate against a
symbol clock 2.33 times slower, and nothing in this harness establishes that the
page's internal symbol/sample ratio survives being throttled that way.
