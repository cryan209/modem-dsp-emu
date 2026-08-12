# Diva 4BRI-8M PCI v1 firmware replay

The card on `eicon420` is PCI `1133:e012` at `09:04.0`, a **conventional PCI**
endpoint (not PCIe), card type 22, serial 4931, IRQ 20.

## The card traps one second into boot, on a null pointer

This is the central fault.  The MIPS takes an exception immediately after it
processes its first *received* D-channel frame, having transmitted exactly one
frame of its own, and stops.

```
cause=TLB load/DBOUND  sr=0x1040ec03  cause-reg=0x00001008
epc=0x80063f68  instruction=0x8c8300b8  bad-vaddr=0x000000b8
gp=0x0c68d0f4  sp=0x80134260  ra=0x8009b348  class=0x00000101
image=0x80000000..0x80130370  stack-top=0x801343b0  trapped-sp=0x80134260
stack-depth=336 bytes  image-intrusion=0 bytes
```

The faulting instruction decodes as:

```asm
80063f68: 8c8300b8   lw v1, 0xb8(a0)      ; a0 == 0
```

so this is a **null-pointer read** of field `+0xb8`, reached from `ra=0x8009b348`.
The stack is healthy -- 336 bytes deep, with the image ending at `0x80130370`
far below it and zero image intrusion.  Nothing here resembles the task-3 stack
overflow recorded in the older snapshot; it is a different fault with a
different signature.

Timing places it in the **D-channel receive path**: the card gets through
hardware init and L1, transmits its SABME, receives the UA, and traps.  Some
structure pointer is still null when the first inbound frame is handled.

The last two lines the card ever writes to its XLOG are its Q.921 link setup:

```
0:0000:053 - L1_UP
0:0001:002 - D-X(003) 00 01 7F        SAPI 0, TEI 0, SABME (P=1)   -- card sends
0:0001:010 - D-R(003) 00 01 73        SAPI 0, TEI 0, UA    (F=1)   -- peer replies
```

Nothing follows, on any boot.  The peer side of the same link -- a Cisco 2911
BRI in network mode -- shows what happens next:

```
Q921: Net RX <- SABMEp sapi=0 tei=0      card establishes
Q921: Net TX -> UAf    sapi=0 tei=0      Cisco answers
   ... 10 s later, T203 expiry ...
Q921: Net TX -> RRp sapi=0 tei=0 nr=0    x4, one per second, unanswered
Q921: L2_EstablishDataLink: sending SABME
Q921: Net TX -> SABMEp sapi=0 tei=0      x4, unanswered
Q931: Ux_DLRelInd: DL_REL_IND received from L2
Q921: Net TX -> IDCKRQ ri=0 ai=0         TEI identity check
```

So the card answers nothing after that first exchange, and the peer tears the
link down.  Any inbound call therefore never reaches layer 3: the host sees a
silent card, and the switch sees a dead terminal.

Five independent measurements agree:

| Measurement | Observation |
| --- | --- |
| XLOG | frozen at `D-R` UA, ~1 s after start, indefinitely |
| Peer | RR polls and SABMEs unanswered, link released |
| IRQ 20 | static from that instant (265 on one boot, 211 on another) |
| BAR2 `0x80` | `0x99999999` -- `MP_XCPTC` frame present on adapter 1 |
| **BAR2 read twice, 12 s apart** | **byte-identical across all 4 MiB** |

A processor spinning in a loop still mutates stack, counters or timers.  Four
megabytes unchanged means the MIPS is stopped, and the trap marker says why.

The trap frame lives at the **logical adapter's base**, not at BAR2 `0x80`
unconditionally.  The v1 card presents four 1 MiB logical adapters, so the
candidate marker offsets are `0x000080`, `0x100080`, `0x200080` and `0x300080`;
this fault appears on adapter 1.  Checking only the first will miss a trap on
any of the others.

The trapped state is kept as an untracked artifact:

```
artifacts/diva-4bri-v1-nullptr-trap-bar2.bin
sha256 87df6bb224600277ff6eb5b5316a379de7a89a8bd7e0a284ddbac3dc172f5115
```

**Both protocol builds fail, in different ways.**  107-136 completes hardware
init on all four adapters, brings L1 up, completes one L2 exchange and then
takes the trap above.  108-130 fails earlier and differently: it does not start
at all, leaving the image resident in card RAM with **no** trap marker at any
adapter base.  Neither reaches a usable state, so no configuration described
here should be called working.

## Firmware pairing

The pairing that gets furthest is **generation-matched**: a 107-136 protocol
image against a 108-744 DSP set.

| Component | File | sha256 | Build |
| --- | --- | --- | --- |
| Protocol | `te_dmlt.qm` | `8bd5484a…` | TE_DMLT 107-136, Protocol 6.03 104-905 |
| DSP download | `dspdload.bin.qm` | `535c7553…` | 108-744 |

with all four `imagename` entries in `divas_cfg.rc` pointing at the DSP file.
That combination reaches:

```
Instance(0)=0x801ca000 image_start=0x80000000, shared_memory=0xa0001000 card=22
Protocol: 'TE_DMLT, Build 107-136, Protocol 6.03 104-905 [F#00FF]'
[0] DSP OK   [1] DSP OK
Hardware Initialisation done.        (adapters 1-4)
L1_UP                                (adapters 1-2; 3-4 have nothing attached)
```

before taking the trap described above.  The stock `docs/firmware/dspdload.bin` is
DSP build 117-926, ten build series away from the protocol image it was being
paired with.

Neither file was tracked before; both are now, because the card depends on them
and neither is reproducible from anything else here:

- `docs/firmware/te_dmlt.qm.107-136` -- the tracked `te_dmlt.qm` is the
  different 108-130 build.
- `docs/firmware/dspdload.bin.108-744` -- build 108-744 appears in no other
  collection here: not the stock file, not `dspdload.bin.old` (107-708), not
  the recovered older set (109-789), not DIVA SE.  How it was originally
  produced is not recorded; no build script or shell history survived.

**Do not install the tracked `docs/firmware/te_dmlt.qm` (108-130) over the
107-136 image on the card.**  Doing so once cost an hour of misdiagnosis: every
subsequent load failed outright, `divactrl load` errored, `CardState` reported
`trapped`, and neither a driver restart nor a full power cycle recovered it,
because each boot simply reloaded the bad image.  Recovery is to copy 107-136
back and reload the driver.  That failed-load state is also kept, since it is a
distinct specimen:

```
artifacts/diva-4bri-v1-notstarted-bar2.bin
sha256 037783177e4150df57eca793311e1199a0f01c5864e213b8c932e6943bdc791d
```

Layout as `derive_layout` reports it for the 108-130 file:

```
base 0x80000000  entry 0x800ba318  sp 0x80135e20  gp none
```

`gp none` means that the flat image has no boot-time global `$gp`; it does not
mean the register is unused.  Code modules establish/use their own value.

## Load path recovered from the live card

Read back from BAR2 while the card was halted, so these are measurements rather
than inferences:

- the protocol image is loaded at **BAR2 offset 0**, byte-identical to
  `docs/firmware/te_dmlt.qm.107-136` where the driver has not patched it;
- the reset vector is intact and jumps through kseg1 to physical `0x44004`:

  ```asm
  00: 3c088004  lui   t0, 0x8004
  04: 25084004  addiu t0, t0, 0x4004      ; t0 = 0x80044004
  08: 3c01a000  lui   at, 0xa000
  0c: 01014025  or    t0, t0, at          ; 0xa0044004
  10: 01000008  jr    t0                  ; -> physical 0x44004
  14: 0000d021  move  k0, zero            ; delay slot
  ```

- the driver's **header patches** are `0x68 = 0x04` (four initial tasks) and
  `0x69 = 0x16` (card type 22).  This is the same mechanism
  `docs/analog_v8_oracle.md` describes for the Analog image, with this card's
  values.

Beware when diffing a BAR2 dump against a file: the reset vector is identical
across builds, so the first words match even when the loaded image is a
different build.  The banner discriminates -- `TE_DMLT, Build 107-136, Protocol
6.03 104-905` versus `108-130, Protocol 6.03(V17) 106-1` -- but it sits at
`+0x80`, exactly where the `MP_XCPTC` marker and frame land.  On an adapter that
has trapped it is destroyed, leaving only fragments; read the build from the
XLOG's `Protocol:` line instead.

`tools/eicon_4bri_trap.py` prints a `firmware=` banner taken from the file given
to `--firmware`, not from the snapshot, so it will confidently echo whichever
image you passed.  It is not a check that the dump and the file agree.

## The task-3 exception snapshot

A snapshot captured earlier on this card, on the 107-136 image, contains a
concrete exception:

```
Cause: TLB load / DBOUND
EPC:   0x800b86c0
vaddr: 0x0c685460
GP:    0x0c6850f4
SP:    0x801300e8
RA:    0x800b86c0
class: 0x00000101
Exception caused by task nr 3
```

The EPC instruction is:

```asm
800b86c0: lw t0, 0x36c(gp)
```

and `0x0c6850f4 + 0x36c == 0x0c685460`, exactly the reported bad virtual
address. The direct cause is therefore settled, but it is secondary to a more
important corruption: **the task stack has crossed into the protocol image**.

The matching image declares stack top `0x801343b0` and ends at `0x80130370`.
The trapped SP is `0x801300e8`: 17,096 bytes below stack top and **648 bytes
inside the immutable image**. The exception handler then places its own 160-byte
frame below that, overwriting still more image data. The bytes around the SP in
the pristine file are the runtime helper/relocation pointer table; in the card
snapshot they have been replaced by task/exception state. This explains the
nonsensical saved `gp=0x0c6850f4`, `t9=0xe489b342`, and `ra=0x800b86c0` without
requiring a GP-specific firmware defect.

So the trapped state is a **task-3 stack overflow or a bad task-3 stack
allocation/context**, eventually surfacing as the invalid GP-relative load. It
is not PCI interrupt routing.

**It is also not the card's routine failure.**  Both are real `MP_XCPTC` frames
on adapter 1 of the same firmware build, but they record different faults:

| | This snapshot | Live fault |
| --- | --- | --- |
| bad-vaddr | `0x0c685460` (gp-relative) | `0x000000b8` (null + 0xb8) |
| EPC | `0x800b86c0` | `0x80063f68` |
| trapped sp | `0x801300e8` | `0x80134260` |
| stack depth | 17,096 bytes | 336 bytes |
| image intrusion | 648 bytes | none |

The live fault has a shallow, healthy stack, so nothing about it requires a
stack-overflow explanation.  Whatever produced the deep-stack snapshot was
situational -- one candidate is driving a 107-136 protocol image with the stock
117-926 DSP download, whose task and overlay tables the protocol image would not
expect, but that has not been isolated by re-running it.

One detail the two share: `gp` is garbage in both, and in the same
neighbourhood (`0x0c6850f4` and `0x0c68d0f4`).  In the older snapshot that
garbage is the direct cause; in the live fault the faulting access is
`a0`-relative and `gp` is merely along for the ride.  Whether a common
`gp`-initialisation defect underlies both is untested.

The analysis of the snapshot stands on its own terms.  Its status is a
register-exact study object, not the fault to fix.

## Snapshot replay

`tools/eicon_4bri_trap.py` reads the `MP_XCPTC` exception frame that
`divas4linux` recovers at BAR2 offsets `0x80/0x90`, restores all 32 MIPS
registers plus HI/LO, and resumes at EPC under Unicorn.

```
artifacts/diva-4bri-v1-trapped-bar2.bin
sha256 3a35fa44072fcc1186bc0540176526e135360a46b738f505e0d42cd6f544f08c
```

Run it with a Python environment containing Unicorn:

```bash
../v90modem/.venv/bin/python tools/eicon_4bri_trap.py \
  artifacts/diva-4bri-v1-trapped-bar2.bin \
  --firmware docs/firmware/te_dmlt.qm.107-136
```

The first emulated instruction reproduces the card exactly:

```
effective-address=0x0c685460 (matches BadVAddr)
stack-depth=17096 bytes image-intrusion=648 bytes
diagnosis: stack crossed into the protocol image; saved register/return state
is corrupt and the invalid GP-relative read is secondary
replayed-fault: read/write size=4 address=0x0c685460 pc=0x800b86c0
unicorn: Invalid memory read (UC_ERR_READ_UNMAPPED)
```

It reads the live fault the same way, and this is the quickest route from a
freshly-trapped card to a decoded frame:

```bash
../v90modem/.venv/bin/python tools/eicon_4bri_trap.py \
  artifacts/diva-4bri-v1-nullptr-trap-bar2.bin \
  --firmware docs/firmware/te_dmlt.qm.107-136
```

```
epc=0x80063f68 instruction=0x8c8300b8 bad-vaddr=0x000000b8
stack-depth=336 bytes image-intrusion=0 bytes
replay stopped at pc=0x80063f6c
```

Note that no stack diagnosis is printed for the live fault, because the stack is
healthy -- the tool only reports intrusion when there is some.  Replay advances
one instruction and stops, since the null read cannot be satisfied from the
snapshot.

This is snapshot replay, not cold boot.

## Cold-boot work remaining

1. Load four copies of the flat `.qm` image using the v1 card's four 1 MiB
   logical-adapter layout, applying the recovered header patches (`0x68 = 0x04`,
   `0x69 = 0x16`) and reproducing the driver's config RAM.
2. Stage card type 22 / file set 7: 44 DSP downloads, 609,496 bytes, with the
   shared DSP table anchored at `0x80135e20`.
3. Model the v1 4BRI DSP ports and eight ADSP cores rather than the PRI's 30
   blocks.
4. Derive `.qm` init, main-loop, host read/write and request-dispatch anchors.
   PRI `BIAS + offset` anchors are invalid for this image.
5. **Reproduce the live trap.**  Boot from the reset vector, bring L1 up, deliver
   one inbound Q.921 UA in response to the card's SABME, and establish why `a0`
   is null on entry to `0x80063f68`.  The hardware gives an exact comparison for
   every step: the XLOG through `L1_UP` and `D-X`/`D-R`, the decoded frame, and
   a frozen 4 MiB post-trap memory image to check final state against.  This is
   a better target than the archived snapshot -- it is the fault that actually
   stops the card, it has a shallow stack and a single obviously-wrong operand,
   and it reproduces on every boot.

   Working backwards from `ra=0x8009b348` to find the caller that passes the
   null, and identifying which structure has field `+0xb8`, is the cheapest way
   in and needs no emulator at all.

## Host lockup: do not monitor BAR2 across driver init

On 2026-08-12 the card host hard-locked and needed a physical power cycle.  The
cause was host-side and has nothing to do with the MIPS firmware.

`/tmp/monitor-4bri` was started one second before `divas_cfg.rc`, so it was
polling the BAR2 window while the driver ran card insertion, `enabling device
(0100 -> 0103)` and the firmware download.  The machine died mid-init, one
second in.  The journal shows no MCE, no AER, no oops and no panic -- it simply
stops, which is the signature of a bus hang rather than a software fault, and
conventional PCI has no AER to report through in any case.

The evidence is a within-boot controlled comparison.  The same protocol image
had been installed and started 29 minutes earlier in that same boot *without*
the monitor and survived, along with four further stop/start cycles.  The only
hard death among the 11 retained boots coincides with the only appearance of
`monitor-4bri` in the whole journal.

So: bring the driver up, wait for `Hardware Initialisation done` and `L1_UP` in
`/var/log/diva1.log`, and only then read BAR2.  Polling across driver init is
what is unsafe, not reading as such -- single map-copy-unmap reads on a settled
driver have been done repeatedly since without incident.

## Diagnosing this card

Useful, non-destructive checks, in the order worth running them:

```bash
sudo /usr/lib/divas/divactrl load -c 1 -CardState   # active | trapped
sudo /usr/lib/divas/divas_status                    # controller/channel table
sudo grep -E "Protocol:|Hardware Initialisation|L1_UP|DSP OK" /var/log/diva1.log
grep "DIVA 4BRI" /proc/interrupts                   # sample twice; static = halted
```

Traps for the unwary, all of which cost time here:

- **`CardState` does not report MIPS trap state.**  It said `trapped` for the
  108-130 image, which had taken no exception and left no marker anywhere, and
  it said `active` for the 107-136 image while a real `MP_XCPTC` frame was
  sitting on adapter 1.  It is closer to "did the driver manage to start this
  card" than to anything about the CPU.  Read the marker instead.
- **Check all four adapter bases** for the marker (`0x000080`, `0x100080`,
  `0x200080`, `0x300080`), not just the first.
- `divas_status` reports `N/A` serial and 0 channels on this card even when it
  has booted successfully, so those rows are not evidence of failure.  The
  `-DumpMaint` core-dump path is unsupported on this adapter and always errors.
- When diffing a dump against a firmware file, confirm which build is actually
  resident first, via the banner at `+0x80`.  The reset vector is identical
  across builds, so a first-32-bytes comparison will match the wrong file.

The AT interface on `/dev/ttyds1..8` is **emulated entirely in the Divatty
driver**: `ATI`, `AT+MS=?` and friends answer normally, with zero card
interrupts, on a completely halted card.  It proves nothing about the hardware.
`/dev/ttyds0` is a control node and does not answer AT at all.
