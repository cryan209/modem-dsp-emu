# PRI vs BRI firmware: where the DSP count actually matters

## Summary

The `.pm` (PRI, 30-DSP) image was thought to be blocked on a 30-way DSP
*presence* detect, making the `.2qm` (BRI, 2-DSP) image look like the way
forward. Re-reading the firmware shows that was only half right, and the
first blocker was something else entirely:

1. The card init's per-DSP **constructor** does no probing at all — it
   builds 30 DSP objects unconditionally.
2. The first thing that actually failed was a missing **DSP code image** in
   card RAM, which has nothing to do with DSP count. Fixed; see below.
3. The 30-way handshake is real, but it lives in the **validator**
   (`0x80082130`) that runs after the constructors, and it is a
   command/response boot handshake, not the host driver's IDMA signature
   probe.

Switching to `.2qm` still costs a full entry-point re-derivation (see the
code-model note at the end) and would not have avoided (2) at all.

## What the init actually does

`0x80082f90` (the image entry):

```
80082fb0: lui   $s1, 0xa001
80082fb4: lw    $s1, 0x106c($s1)   # protocol image + 0x6c
...
8008300c: lhu   $s2, ($s1)         # download count
80083020: addiu $s1, $s1, 4        # -> t_dsp_portable_desc[], stride 0x30
```

`+0x6c` is `OFFS_DSP_CODE_BASE_ADDR` (`kernel/mi_pc.h:205`) and the 0x30
stride is `sizeof(t_dsp_portable_desc)` (`kernel/dsp_defs.h:190`, 10 words +
7 dwords). So `$s2` is the **DSP download-table count**, not a presence
word. The host driver stages that table in `pri_telindus_load`
(`kernel/s_pri.c`): `sharedRam[0] = download_count`, table immediately
after.

Then `0x80081de0` (card init, called at `0x800830d0`):

- builds a fixed table of 30 DSP register bases (`0xbc000800`, `+8` …
  `0xbc001070`, plus the two on-board at `0xbc000008/0x20`);
- loops `slti $v0, $s0, 0x1e` (`0x80082090`) calling the per-DSP
  constructor `0x80085394` 30 times — unconditionally, no bitmask, no bail;
- calls the validator `0x80082130` at `0x800820bc`;
- returns nonzero on failure, and the entry then **hangs** at the
  `0x800830ec` self-loop.

`0x80085394` makes exactly two calls (a trace printf and `0x8002a800`) and
never touches the host port, so there is no presence probe in construction.
It only records the code-table count/pointer at object `+0/+4` and searches
the table for download id `0x190` (FAX05 — not in the PRI-30M set).

The host driver's `dsp_check_presence` with the `0x5a5a`/`0xa5a5` signature
(`kernel/io.c:1229`, `s_pri.c:238`) is **host-side only**. Its result
(`a->dsp_mask`) is never written to the card — it feeds a `DBG_FTL` when
fewer than 2 respond, and `/proc` reporting.

## Fixed: DSP code image staging

`tools/eicon_dsp_stage.py` builds the image the firmware expects and
`tools/eicon_mips_shim.py --mainloop` now stages it before the entry runs.
Layout mirrors `pri_telindus_load` + `dsp_read_file`
(`divactrl/load/common/dsp_file.c`):

```
+0x0000  dword  download_count
+0x0004  t_dsp_portable_desc[128]          (128 * 0x30)
+0x1804  section data, dword-aligned, in dsp_read_file order
```

Each descriptor's seven pointer fields hold the card address of that
section, or 0 when empty (`dsp_card_load_portable`). Which downloads are
required is decided by the combifile itself: its directory maps a
`card_type_number` to a file set, and each download carries a usage-mask bit.

Base address is the protocol image's own `OFFS_PROTOCOL_END_ADDR`
(`0x80338700` for `te_dmlt.pm`), dword-aligned — the same value the image
entry uses as its initial `sp`, so the stack grows down away from it.

For card type 23 (`CARDTYPE_DIVASRV_P_30M_PCI` -> file set 5) that is 64
downloads / 848,580 bytes, including everything the modem path needs:

```
0x0258 TIKRNL81.F34   0x0261 V.34 Overlay   0x026a V.90 DPCM Overlay
0x025f V8.F34         0x0262/0x0263 DIAL.F34
```

Verified in the shim by probing the entry's registers:

| | `$s1` (DspCodeBaseAddr) | `$s2` (count) |
|---|---|---|
| before | `0x805a0000` (computed fallback) | `0x0000` |
| after | `0x80338700` | `0x0040` (64) |

Every download's section sizes sum exactly to its record length, which is a
strong check that the layout matches the shipping loader.

## Next blocker: the 30-way boot handshake in `0x80082130`

Card init still returns 1 (failure), unchanged by the staging — the entry
would hang at `0x800830ec`; `run_mainloop` currently bulldozes past it and
calls the post-init functions directly, which is why the symptom is a quiet
`host_writes=0` rather than a hang.

`0x80082130` walks the 30 DSP objects twice: first calling `0x800a62cc` on
each (`slti $v0, $s1, 0x1e` at `0x800821a0`), then pairing them two at a
time (`addiu $s1, $s1, 2`) through `0x800a77e0` and `0x800a7940`. Index
0x1c (28) is special-cased against flags at `gp+0x5e97` / `0x802a b895`.

`0x800a77e0` is the real per-DSP boot handshake:

```
800a7848: jal   0x80082950        # host_write(reg_block, addr, 0x5a5a)
800a784c: addiu $a2, $zero, 0x5a5a
800a7868: jal   0x800a6298        # post command
800a7878: jal   0x80086af8        # wait, timeout 0xffff
800a78c0: jal   0x800a6204
800a78c4: ori   $s3, $zero, 0xa5a5 # expect 0xa5a5 back
```

So the pattern resembles `dsp_check_presence`, but it goes through the
firmware's command/wait machinery rather than raw IDMA, and it needs the DSP
side to actually run and answer.

### Resolved: the DSPs answer it

`0x800a77e0` is not just a probe — it is a full kernel download followed by
an alive check. It writes `0x5a5a` to the download's symbol 0, streams the
DSP kernel in through `0x80086af8`, releases the core, then polls that word
for `0xa5a5`. Getting the emulated DSPs to answer took four fixes:

**1. The IDMA destination-type bit was inverted.** Bit 14 of the IDMA
address selects 16-bit data memory when set and 24-bit program memory when
clear — see the comment in `adsp2181_idma_data_write` for the three
independent proofs in the firmware and combifile. The emulator had it the
other way round, which is also why a single PM write previously needed a
commit-on-address-change workaround to make the DSP presence check pass.
`symbol_host_address` in the shim now applies the same rule as the
firmware's resolver, and the harness call sites that hand a bare DM address
to the host port OR in `0x4000`.

**2. The shim hooked the wrong register region.** The card init computes its
own DSP register bases (`0xbc000800 + row + index*8`, kseg1 for physical
`0x1c000000`); the shim's hook was still at `0x380000` from the earlier
hand-synthesized assign path, so the bulk transfer helper's writes went to
a scratch page and its read-back verify failed.

**3. The DSP must be held for the download.** ADSP-2181 IDMA boot
(BMODE=1, MMAP=0): "Program execution is held off until on-chip program
memory location 0 is written to." The Eicon download streams from PM
`0x0001` up and releases the core with a final write to PM 0.
`adsp2181_set_idma_boot_hold` models this; a core left running executes its
own half-replaced image and corrupts the transfer, which the download's own
read-back verify catches. Any later PM write re-arms the hold, since that
means a new code download is starting; data-memory writes do not, so
mailboxes and command rings still reach a running DSP.

**4. One emulated core per DSP.** All 30 register blocks previously aliased
onto a single ADSP, so each download landed in the previous DSP's running
image. `MipsShim.core_for` now creates a core per register block (~350 KB
each, so 30 is free), each held in IDMA boot mode until its own download
completes.

With those in place `--mainloop` reports:

```
[dsp] 31 cores: 29 answered the boot handshake with 0xa5a5, 2 still held (no download)
```

Every DSP the firmware downloads to boots from its own downloaded kernel and
writes `0xa5a500` to PM `0x3fff` — the exact word `0x800a78d0` polls for.
The transfers are validated by the firmware itself: all 36 DM blocks and the
PM blocks pass its read-back verify (`0x80082a38` for DM, `0x80082b8c` for
the 24-bit PM form).

### The SIGSEGV: an over-wide register hook, not the emulator

Running the DSPs during init (`--dsp-pump N`) used to crash. It was not a
memory-safety bug in the emulator, and AddressSanitizer would not have found
it — under lldb the fault is a NULL dereference inside `libunicorn`'s code
generator (`temp_load`), reached only *after* the MIPS had already faulted.
Two shim bugs were behind it:

**The DSP register hook was too wide.** It covered `0x1c000000..0x1c002000`,
but the DSP blocks stop at `0xbc0010f0` (two on-board DSPs at `+0x08`/`+0x20`,
module rows at `+0x800..+0x870` and `+0x1000..+0x1070`, each with its address
port `0x80` above). The card object's `+0x80` holds a *control* register
block at `0xbc001800`, and `0x80082dc0` writes a byte to
`[obj+0x80] + (a1 << 4)` — `0xbc0019b0` for `a1 = 0x1b`. The hook swallowed
that, read offset `0xb0` as an IDMA address-port write, and spawned a phantom
DSP core (31 cores became 46). The firmware then read back nonsense, computed
a bad pointer, and took a MIPS CPU exception; Unicorn crashed afterwards.
The hook now ends at `0x1c001100`.

**The auto-map hook could not map.** `_unmapped` called `mem_map` directly,
which throws when the page overlaps one of the larger fixed mappings, so an
auto-mappable access surfaced as `UC_ERR_WRITE_UNMAPPED`. It now goes through
`ensure_mapped`, which consults the live region list and maps at Unicorn's
4K granularity (a 64K unit can straddle a region edge and `mem_map` rejects
any overlap).

With both fixed, `--dsp-pump 256` runs clean:

```
[mainloop] running firmware entry (basic init)...      <- no fault
[mainloop] after init: gp+0x5e81=0x0000 gp+0x5eb9=0x0060
[dsp] 31 cores: 30 answered the boot handshake with 0xa5a5, 1 still held
[mainloop] ASSIGN posted: Sig=0x4447 NextReq=0x03e0 -> 0x0500 ReqInput 32->33
```

That is the whole init sequence working: the firmware entry completes with no
faults, the DSPs answer the handshake *in line* during the validator's poll,
96 resources are registered (`gp+0x5eb9`, previously 0), and the firmware
publishes its PR_RAM signature `0x4447` — the card has booted and is ready
for host requests.

The request queue is now set up by the firmware during boot, so
`run_mainloop` posts the modem ASSIGN afterwards, following `pr_out()`
(`kernel/di.c`): fill the REQ at `B[NextReq]`, advance `NextReq` to
`REQ->next`, and bump `ReqInput` from the firmware's `ReqOutput` so the
counters start level.

The DSP count never became a blocker: 30 cores are cheap, and the validator
handshakes them one at a time.

### Resolved: the main loop now consumes the request

Two bugs, both in what the shim was writing rather than in the firmware.

**The queue counters.** The main loop treats `(ReqOutput - ReqInput) & 0xff
== 0x20` as *empty* (`0x80027ae4`) and `0x20 -` that difference as the free
slot count (`0x80027a94`), so `ReqOutput` leads `ReqInput` by 32 when idle.
The firmware initialises `ReqOutput` to 32 with `ReqInput` at 0, and the host
only ever increments `ReqInput` — one per posted request, as `pr_out()` does.
Trying to "sync" the two first left the difference at `0xff` and the loop
never saw a request.

**The entity id.** `NL_ID` is **0x20** (`kernel/pc.h`; `DSIG_ID` 0x00,
`BLLC_ID` 0x60, `TASK_ID` 0x80, `MAN_ID` 0xe0) — the shim was sending 0x01.
That is not a harmless mislabel. The main loop matches a request against the
registered entities by `entity+0x14 == translate(ReqId)`, where
`0x80029ed4` indexes a byte table at `0x80121370` by `ReqId * 2`. Entry 0 is
`0x1f` and the rest are 0, so `translate(0x01)` returned 0 and matched the
first *free* entity (94 of the 96 registered entities have id 0). The request
was then handed to that entity's protocol handler (`0x80016564`), which
compares the raw `ReqId` against its own id, found `0x01 != 0x00`, and
returned without doing anything — never reaching the assign path at
`0x80027c4c` and never acknowledging.

With both fixed the request is consumed and acknowledged on the first
iteration:

```
[mainloop] ASSIGN posted at B[0x03e0]: Sig=0x4447 NextReq->0x0500 ReqInput 0->1 ReqOutput=32
[mainloop] iter 0: v0=0x00000001 ReqIn=1 ReqOut=33 Sig=0x4447
```

`ReqOutput` 32 -> 33 is the firmware's own acknowledgement, written by
`0x80029f88` (`REQ->Reference` stamped, read offset `gp+0x5e99` advanced to
`REQ->next`, `ReqOutput++`).

`--dsp-pump` now defaults to 256: the DSPs have to run in line with the MIPS
for the validator's handshake to complete within one call, and the IDMA boot
hold makes that safe.

### The ASSIGN payload, checked against the driver

The hand-built CAI was wrong in three ways, and `add_modem_b23()` turned out
not to be where a CAI comes from at all.

**Framing.** An IDI request payload is a list of `{code, length, data}`
triples with a single zero code byte terminating it — `add_ie()`
(`kernel/message.c`) writes a `0` after each parameter and backs over it when
the next is appended. The shim was writing a bare 26-byte blob with no code,
no length and no terminator.

**Wrong entity.** `add_modem_b23()` builds **LLI/LLC/DLC**, not a CAI, and
those go on the *network-layer* ASSIGN (`nl_req_ncci(plci, ASSIGN, 0)`). The
CAI is built by `add_b1()` and rides on the *signalling* ASSIGN
(`sig_req(plci, ASSIGN, DSIG_ID)`). The driver sends the signalling ASSIGN
first.

**Length.** `add_b1()` sets `cai[0] = 26` for a modem B1 protocol; the shim
had 25.

The CAI *content* was largely right. `cai[1] = 0x11` is correct — `add_b1()`'s
`resource[] = {5,9,13,12,16,39,9,17,17,18}` maps B1 protocol 7/8
(`B1_MODEM_ALL_NEGOTIATE` / `B1_MODEM_ASYNC`) to 17 = `DSP_CAI_HARDWARE_MODEM_ASYNC`
— and the Tx/Rx speed words were already at the right offsets (`cai[15]` /
`cai[19]`, with the minima at `cai[13]` / `cai[17]`).

`idi_parameters()`, `modem_cai()`, `modem_sig_assign_payload()` and
`modem_nl_assign_payload()` now build both, selected by `--entity`:

```
sig: 10 1a <26-byte CAI> 2d 06 "Capi20" 00
nl:  19 01 01 · 7c 02 09 04 · 20 09 0004 03 01 07 07 0000 43 · 00
```

The NL payload decodes as MaxDataLength 1024, Addr A 3, Addr B 1, modulo 7,
window 7, no XID, and negotiation flags `0x43`
(`DISABLE_V42_V42BIS | DISABLE_MNP_MNP5 | DISABLE_SDLC`) — the plain
`B2_TRANSPARENT` branch of `add_modem_b23()`. LLC `{9, 4}` is V42_IN with L3
transparent, i.e. the answering side.

### The signalling ASSIGN succeeds

The "no return code" was a misreading on my side, twice over.

**The host does not build the buffer chains.** The driver only ever *reads*
`NextRc` and `NextInd` (`kernel/di.c:266,304`) and writes `NextReq` just to
advance it along a chain it did not create (`di.c:214`). The firmware builds
all three during boot, and the layout it produces confirms it:

| chain | head | stride | region |
|---|---|---|---|
| RC | `B[0x0000]` | 0x10 | 0x0000..0x03e0, 62 buffers |
| REQ | `B[0x03e0]` | 0x120 | 0x03e0..0x27e0, **32** buffers |
| IND | `B[0x27e0]` | 0x120 | 0x27e0.. |

32 REQ buffers is exactly the `0x20` in the main loop's queue arithmetic, so
`NextRc = 0` is a genuine chain head, not an uninitialised pointer.

**`RcOutput` is at +0x0b, not +0x0a.** I had miscounted `struct pr_ram`:
`ReqReserved` is +0x08, `Int` +0x09, `XLock` +0x0a, `RcOutput` +0x0b,
`IndOutput` +0x0c, `IMask` +0x0d. The publish step at `0x80029848` writes the
pending count with `sb $a0, 0xb($a1)` and the indication count with
`sb $a0, 0xc($a1)`, which settles it. The RC had been published all along;
the shim was reading `XLock`.

With the offsets fixed, the signalling ASSIGN completes:

```
[mainloop] RcOutput=1 NextRc=0x0000
[mainloop] RC 0xef (ASSIGN_OK) Id=0x02 Ch=0x00 Ref=0x0000 @B[0x0000]
```

`ASSIGN_OK` with the card's assigned local entity id `0x02`. The RC is queued
by `0x80029fc8` (called from the signalling handler at `0x8001701c`), which
fills Rc/RcId/RcCh/Reference and bumps the pending count at `gp+0x5e9d`;
`0x80029774` then publishes it into PR_RAM.

### The host must acknowledge the card's notification

Posting the two ASSIGNs in sequence exposed one more piece of the host side.
The second return code was queued in card RAM (`gp+0x5e9d` stuck at 1) but
never published, no matter how many main-loop iterations ran.

The main loop only calls the RC/IND flush (`0x80029774`) when `RcOutput` and
`IndOutput` are both zero **and** the byte pointed at by `gp+0x5eaf` is zero
(`0x80027d84`). That byte — `PR_RAM+0x3fe` here — is the card→host
notification the flush raises on its way out (`0x8002989c` sets it to 1). A
real host clears it when its ISR services the interrupt; the shim never did,
so after the first flush the firmware silently stopped publishing.
`drain_return_codes()` now clears it, and the next RC appears on the first
iteration.

### Where the sequence stands

```
[sig] ASSIGN Id=0x00 Ch=0x01 payload=101a11...2d0643617069323000
[sig] RC 0xef (ASSIGN_OK) Id=0x02 Ch=0x00 Ref=0x0000
[nl]  ASSIGN Id=0x20 Ch=0x01 payload=1901017c020904200900040301070700004300
[nl]  RC 0xe6 (assign rejected) Id=0x3f Ch=0x00 Ref=0x0001
```

The signalling ASSIGN succeeds and the card allocates entity id `0x02`. The
network-layer ASSIGN is rejected with `0xe6`, and doing it after a successful
signalling assign changes nothing — the standalone attempt returned the same
code — so ordering was not the problem.

`run_mainloop` now drives the whole sequence (`--entity sig|nl|both`, default
`both`) through `post_request()` / `run_until_rc()` / `drain_return_codes()`,
which mirror `pr_out()` and `pr_rc()` in `kernel/di.c`.

### Next: why the network-layer ASSIGN is rejected

The request is dispatched to the entity with id `0x3f`: `0x80029ed4` maps
`ReqId` through a byte table at `0x80121370` (`ReqId * 2`), and the live table
is `0x00 -> 0x1f`, `0x02 -> 0x1f`, `0x1f -> 0xfe`, `0x20 -> 0x3f`. That
entity's handler queues the rejection from `0x80065db8`, which is where the
reason lives. `isdn_rc()` never decodes the sub-code, so 0x06 has to be read
out of the firmware.

Still no host writes, so no DSP work has been triggered yet.

## Why `.2qm` is still expensive

The `.2qm` firmware (build 122-11) uses a different code model than `.pm`:

- `.pm` sets a global `$gp = 0x800fa3b5` via `lui gp, 0x8010; addiu gp, gp,
  -0x5c4b` at file `0x474`/`0x64c`;
- `.2qm` has **zero `lui gp` instructions** — position-independent or
  per-function gp. `lui a0` values cluster around `0x8028`/`0x8029`.

Re-deriving `MIPS_ENTRY`, `MIPS_POST_INIT1/2`, `MIPS_MAINLOOP`, the `$gp`
base and the trace-printf pointer is a real reverse-engineering task.
`.2qm` and `.am` are the same build and share structure, so entry points
found once would apply to both.

## But `.2qm` is not the image a 4BRI v2 loads — `.2q0` is

Session 105. The verdict above is correct about `.2qm` and does **not**
generalise to "BRI is expensive", which is how it has been read.

`divaload.c:4776` picks the protocol image suffix as `(revision) ? ".2q" :
".qm"`, one image per logical adapter, so a Diva 4BRI-8 v2 (card type 53,
`CARDTYPE_DIVASRV_Q_8M_V2_PCI`) loads `te_dmlt.2q0`…`.2q3`. Those are **build
108-971**, one generation off `.pm`'s 107-79, and they keep `.pm`'s code
model. `.2qm`, `.2qf`, `.qpm` and `.am` are all build 122-11; `.qm` (4BRI v1)
is 108-130 and also has no global `$gp`.

Why this looked like it mattered for V.90A: card type 53 maps to combifile
file set 9, which selects **`0x026b` V.90 APCM** alongside the `0x026a` DPCM
the PRI already gets — and it does so with the same `.F34` overlay family this
harness already boots (`TIKRNL81.F34`, `V8.F34`, `INFO`, `V.34 Overlay`), not
the `.ANA` variants. Card type 23 (file set 5) has no APCM at all.

**Corrected, Session 134: that does not make V.90A a BRI-only capability, and
the sentence that used to stand here — "`EICON_MODULATION=v90a` cannot work on
the PRI image no matter what the IDI layer sends" — is wrong.** The IDI layer
was never the obstacle and neither is the protocol image: `te_dmlt.pm` gates
V.90A at `0x80091f78` on CAI bit `0x04` *and* on finding download `0x026b` in
the staged table, tracing "V.90A not supported" when the search misses. The
staged table is this harness's own (`eicon_dsp_stage.py`), so
`EICON_DSP_EXTRA_DOWNLOADS=0x026b` supplies the overlay and the PRI firmware
takes the supported branch. Same-file-set membership is what makes that
legitimate rather than a bodge: the two file sets run the same `0x0258` task
kernel. Session 134 has the disassembly and the A/B.

So V.90A is **not** a reason to re-target `.2q0`. Everything below still
stands as the BRI analysis; it just no longer has this as its motivation.

### What transfers from the PRI harness, and what does not

Anchors were located by matching instruction *shape* (opcode and register
fields, immediates and jump targets masked) from `.pm` into `.2q0`.
Calibration: `.pm` against `.pm3` matches nothing at all, so the hit rate
below is signal, not coincidence.

| shim anchor | `.pm` | `.2q0` | strength |
|---|---|---|---|
| `HOST_READ` | `0x80082920` | `0x8008c528` | whole function |
| `HOST_WRITE` | `0x80082950` | `0x8008c558` | whole function |
| `HOST_WRITE_DM_BLOCK` | `0x80082a38` | `0x8008c640` | whole function |
| `HOST_WRITE_PM_BLOCK` | `0x80082b8c` | `0x8008c794` | whole function |
| `DSP_DOWNLOAD` | `0x80086af8` | `0x800a56a8` | 512 instructions identical |
| `MIPS_MAINLOOP` | `0x80027970` | `0x80018480` | first ~32 only, unconfirmed |
| `CONNECTED_DRIVER` | `0x800951d4` | `0x800b828c` | 22/24, unconfirmed |

The four host helpers keep identical spacing (`+0x30/+0xe8/+0x154`) in both
images, so that module is one relocated unit.

No match above noise for `CARD_INIT`, `MIPS_INIT`, `REQUEST_PARSER`,
`SCRIPT_SENDER`, `SWITCH_ON`, `SERVICE_ASSIGN` — the card-geometry-dependent
routines, which is where a PRI/BRI difference belongs. `SERVICE_ASSIGN` has
no `jal` in `.pm` either (it is reached through the service-driver table at
`0x800eaec8`), so in `.2q0` it has to be found through the equivalent table.

### The IDMA address port moves

```
.pm    80082950  sh $a1, 0x80($a0)     # PRI: address port at +0x80
.2q0   8008c558  sh $a1, 0x08($a0)     # BRI: address port at +0x08
```

Confirmed independently by `kernel/mi_pc.h`: `MQ_DSP1_ADDR_OFFSET 0x0008` /
`MQ_DSP1_DATA_OFFSET 0x0000`, `MQ_DSP2_*` at `0x0208`/`0x0200`, subboard
stride `MQ_DSP_JUNK_OFFSET 0x0400` — **8 DSPs, two per subboard across four
subboards**, against the PRI's 30. `MipsShim._hostreg_write` / `_dsp_write`
decode `+0x80` as the address port and `+0x00` as data, and the hook window
stops at `0x1c001100`; both are PRI-shaped. The "port decode only looks at
the low byte" aliasing that makes 30 blocks share one core does not survive
data and address being 8 bytes apart.

### `.2q0` does not read `DspCodeBaseAddr` where `.pm` does

`.pm` reads the header field directly: `lui $s1, 0xa001; lw $s1, 0x106c($s1)`
(physical `0x1106c`, i.e. image + `0x6c`). `.2q0` contains **no**
`lw *, 0x6c(*)` at all — the 4BRI maps four protocol images into shared SDRAM
at per-task offsets (`s_4bri.c:723-735` takes the base from the *highest*
adapter), so the pointer is computed. `stage_dsp_code()` publishing into the
header at `+0x6c` will not be picked up unchanged.

Note also that card type 53's full download set stages to 984 KB, against the
real 4BRI's `MQ_V90D_MAX_DSP_CODE_SIZE` of 384 KB (`mi_pc.h:124`) — the
shipping driver must stage a subset. Not a constraint in emulation, but it
means "stage everything" diverges from hardware here in a way it does not on
the PRI.

### Resolved: the image layout is derived, not hardcoded

`tools/eicon_mips_image.py` recovers the four numbers the shim has carried as
`.pm` constants from any image of this generation: load base (from the .bss
clear, which starts one byte past the file image), `$gp`, initial `$sp`, and
the entry the boot stub jumps to. It reproduces `.pm`'s known-good
`0x80011000 / 0x800fa3b5 / 0x80338700 / 0x80082f90` exactly, which is the test
that matters — `tests/test_eicon_mips_image.py`, 11 tests.

```
te_dmlt.pm    base 0x80011000  entry 0x80082f90  gp 0x800fa3b5  sp 0x80338700
te_dmlt.2q0   base 0x80000000  entry 0x8008e5f8  gp 0x8015231c  sp 0x801ebfd0
te_dmlt.2q1   base 0x80400000  entry 0x8048e5f8  gp 0x8055231c  sp 0x805ebfd0
te_dmlt.2q2   base 0x80800000  entry 0x8088e5f8  gp 0x8095231c  sp 0x809ebfd0
te_dmlt.2q3   base 0x80c00000  entry 0x80c8e5f8  gp 0x80d5231c  sp 0x80debfd0
```

The four adapters are one build linked at 4 MB intervals, same entry at file
offset `0x8e5f8` in each. **The BRI image loads 0x11000 lower than the PRI
one**, so every `BIAS + <file offset>` anchor in `eicon_mips_shim.py` is off by
that much before anything else is considered. The 122-11 generation raises
`FormatError` rather than guessing.

`0x8008e5f8` is the entry, not a helper: it reads `0xa0000068` —
`OFFS_DIVA_INIT_TASK_COUNT`, where `s_4bri.c:728-729` writes `tasks` and
`cardType` — and defaults the card type to `0x35 = 53`, the 4BRI-8 v2. It then
loops four times building per-task state, checks a signature at `0x80000500`,
and self-loops at `0x8008e7a8` on failure, the same hang-on-init-failure shape
`.pm` has at `0x800830ec`.

### Remaining, in order

1. Thread the derived layout through the shim, replacing `BIAS`, `GP`,
   `STACK_TOP` and `MIPS_ENTRY`. *(the anchors themselves stay `.pm`'s until
   step 3)*
2. Rework the DSP register decode for `+0x08` / `+0x0208` / `+0x400` and 8
   cores.
3. Re-derive the six missing anchors: `CARD_INIT` via the `jal` from the entry
   (`.pm`: `0x800830d0 → 0x80081de0`), `SERVICE_ASSIGN` via the service-driver
   table, the rest from their call sites into the located host helpers.
4. Find how `.2q0` locates the DSP code table, and stage to match.

None of this has been run: it is static analysis, and with `.pm` addresses
hardcoded a `.2q0` run would execute unrelated bytes.

## Version note

`te_dmlt.pm` identifies as build 107-79 (`TE_DMLT, Build 107-79, Protocol
6.03(V11) 104-102`), while `dspdload.bin` is build 117-926 and
`dspdvmdm.bin` is 103-492. No combifile in `docs/firmware/` matches the
protocol build exactly. The container format is versioned separately
(`format_version_bcd`) and the firmware accepted the 117-926 table, but a
mismatch is worth ruling out if a download is later rejected by id.
