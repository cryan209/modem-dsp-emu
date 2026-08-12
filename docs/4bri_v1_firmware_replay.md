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

The caller supplies the null directly:

```asm
8009b338: 8e04000c  lw    a0, 12(s0)      ; a0 = *(s0 + 12)   <-- NULL
8009b33c: 97a50010  lhu   a1, 16(sp)
8009b340: 0c018fda  jal   0x80063f68
8009b344: 00408821  addu  s1, v0, zero    ; delay slot
```

The frame corroborates itself: it records `s1 == v0 == 0x8009b340`, exactly what
that delay slot computes, and `ra` is the call site plus eight.  The callee
faults on its *first* instruction, so register state is the caller's untouched.

`s0` is the per-adapter context -- elsewhere in the same function it is indexed
at `+107` (a state byte, dispatched through a table at `0x8011d4d0`), at
`+1246..+1251`, and passed whole as `a0` to sibling routines.  Its field `+12`
is a pointer to a **statistics block**: the callee and its sibling at
`0x80063f40` do nothing but load `*(a0+0xb8)` / `*(a0+0xe0)` / `*(a0+0xe4)`,
add `a1` to a halfword counter, and store it back.

So the fault is: **a per-adapter statistics-block pointer is null when something
about a second into startup dereferences it.**

That "something" is not the D-channel.  Booting with the S0 lines shut down --
no L1, no SABME, no UA, no inbound frame of any kind -- produces the *same trap*,
and the frames are identical in 39 of 40 saved dwords:

```
lines-up vs lines-down: [29] t9  up=e48fb342  down=80064948   (all else equal)
```

`t9` is the unmaintained PIC register, so its value is incidental leftover.
`epc`, `bad-vaddr`, `gp`, `sp`, `ra` and every other register match exactly.
The lines-down control is kept:

```
artifacts/diva-4bri-v1-linedown-trap-bar2.bin
```

With the lines down the XLOG ends at `0:0001:001 - ACTIVATION_REQ` instead of
the `D-X`/`D-R` pair -- the card asks for L1 activation, gets no answer, and
traps anyway at the same instant.  The D-channel exchange seen in the earlier
logs merely fitted into the same second; it was never the trigger.

The fault is therefore **deterministic and time-based, not event-driven**.
Combined with what the callee does -- nothing but increment halfword counters --
the natural reading is a **periodic statistics tick firing about a second after
start**, walking the adapter contexts and dereferencing a `+12` that was never
populated.  Nothing external is required, and no race is being lost: in this
configuration the allocation simply never happens.

That field is null *by design* at structure setup.  `0x800ea898` sits in an
initialiser that zeroes it along with its neighbours:

```asm
800ea888: sw v0, 0(a0)
800ea894: sw a2, 8(a0)
800ea898: sw zero, 12(a0)     ; +12 deliberately nulled
800ea89c: sw zero, 16(a0)
800ea8a0: sw zero, 20(a0)
```

so `+12` starts null and something later is expected to populate it.  Which
step, and what triggers it, is the open question -- and the whole practical
question of whether this card can be made to boot turns on it.  Offset `+12` is
generic enough that 87 stores in the code region use it, so it cannot be
identified by offset alone; the caller's own structure has to be followed.

Note also that the read of `+12` happens immediately after a state-dispatched
handler runs with `s0` as its argument.  The state byte at `+107` indexes a byte
table at `0x8011d4d0`, which selects a 28-byte entry at `0x8011d490` whose first
word is the handler.  Only two distinct handlers exist across the low states --
`0x800e3508` for almost all of them and `0x800e5150` for state 8 -- so the
handler is a plausible place for `+12` to be filled, and the state byte
determines which one runs.

The trap marker is on **adapter 1**.  All four logical adapters share one MIPS,
so adapter 1 trapping halts the card before any other reaches the same code,
which is why only one marker is ever set.

### Where `+12` is written: nowhere

Splitting the image into functions at `jr ra` gives 2,396 of them.  Taking the
eleven that touch a signature offset (`+107`, `+1246..+1251`) through some base
register, `+12` is **loaded as a word 13 times and stored as a word not once**.
The only `+12` stores in those functions are `sh`/`sb` -- halfword and byte --
and belong to a different structure family that uses halfword accessors at the
same offsets, so they are not this pointer.

Nor is there a lazy-allocation path, because **no use is guarded**.  Every one of
the twelve word-loads dereferences immediately:

```asm
80096f84: lw v0, 12(s0)   ->  lhu  a0, 8(v0)
8009af54: lw v0, 12(s0)   ->  lhu  v0, 16(v0)       (and four more like it)
8009b338: lw a0, 12(s0)   ->  jal  0x80063f68       (the trap)
8009c0e0: lw a0, 12(s2)   ->  jal  0x80063f68
80088318: lw a0, 12(s3)   ->  jal  0x800a4ca4
```

Not one test against zero.  The firmware treats the pointer as an unconditional
invariant established once during setup.

The image does contain fourteen allocate-and-attach sequences of the shape
`call` then `sw v0, 12(x)` -- six of them through a single allocator at
`0x8009eba0` -- but none is in adapter-context code.

What *does* run is the constructor at `0x800ea87c`, which sets `+0` and `+8`,
zeroes `+12` through `+36`, then fills `+40`/`+48`/`+52`.  It has exactly one
caller, `0x800bcd68`, and that call site is unambiguously live init code:

```asm
800bcd68: jal   0x800ea87c          ; the constructor
800bcd70: jal   0x8005f2a8          ; shared_ram_alloc
800bcd74: addiu a0, zero, 2832      ; delay slot -- size 2832
```

`2832` is exactly the figure the card reports in its own XLOG on every boot:

```
0:0000:002 - shared_ram_alloc OK (2832/42332)
```

so this path demonstrably executes.  The field is zeroed by a constructor that
runs, and then nothing assigns it.

That closes the loop with every hardware observation: nulled at construction,
never assigned, dereferenced unguarded by a periodic tick a second later,
regardless of line state or management.

**Caveat.** This method cannot exclude an assignment made through an aliased
pointer, in code that touches neither the signature offsets nor the searched
patterns.  But since no use is null-guarded, the firmware plainly expects a
single setup-time assignment, and no such site was found.

So the open question is no longer "what gates the allocation" but **which setup
step, absent here, is supposed to attach that block**.  The likeliest shape is a
request the host driver is expected to make and does not.  Worth noting that the
driver in use, `divas4linux` 9.6.8-124.26-1 (build 124-43), is many build series
newer than the 107-136 protocol image it is loading; an older driver may issue a
startup request this one has dropped.  The vendored source under
`docs/divas4linux-master/` is that same 9.6.8-124.26 version, so it can be read
for what requests *are* issued at startup, but testing the hypothesis directly
would need an older release, which is not on hand.

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

### `$gp` is garbage in both, but is only the cause of one

Exactly two registers are implausible in both frames, and they are the same
two: `gp` (`0x0c6850f4` / `0x0c68d0f4`) and `t9` (`0xe489b342` / `0xe48fb342`).
Every other register in both frames holds zero, a small value, or a sane
`0x80xxxxxx` address.

`t9` and `gp` are the MIPS o32 PIC pair -- the called function's address and the
global pointer -- and neither is maintained in non-PIC code.  This firmware does
not maintain them:

- `derive_layout` reports `gp = None`; there is no boot-time global pointer;
- the image *has* accessors, `get_gp` at `0x80044250` (`jr ra` / `addu v0, gp,
  zero`) and `set_gp` at `0x80044258` (`jr ra` / `addu gp, a0, zero`), and
  **neither has a single call site**;
- the only other write is `lw gp, 128(sp)` at `0x80044410`, inside the task
  context restore, so `gp` is reloaded per task from a context slot that
  nothing ever populates.  `t9` comes from `116(sp)` in the same block.

So the garbage is a **standing condition of this firmware**, not an event during
either fault.  That is why the two values are near-identical rather than
unrelated: they are the same stale leftovers, read back from similar contexts.

That makes the causal split clean:

- the archived snapshot faults *on* `gp`, so uninitialised `gp` is its cause;
- the live trap faults on `a0`, and `gp` is merely present.

They are two distinct defects that happen to share a background condition.

The latent defect is small and precisely locatable.  `tools/eicon_mips_dis.py
--scan-gp` finds only **three** gp-relative instructions in the code, and all
three are the same instruction:

```
800b7a8c: 8f88036c  lw t0, 876(gp)
800b7aa0: 8f88036c  lw t0, 876(gp)
800b86c0: 8f88036c  lw t0, 876(gp)     <-- this snapshot's EPC
```

Every other hit that scan reports lies in the table region from `0x80120430`
onward and is data misread as code (repeating patterns such as `0x93939393`).
So the archived trap is one of three identical reads of a global at `gp+0x36c`
in a build that never establishes `gp` -- it needs no DSP-mismatch explanation,
and the earlier hypothesis to that effect is withdrawn.

What that does *not* explain is the trapped SP sitting 648 bytes inside the
image.  That corruption is real and separate; the faulting instruction is
accounted for by `gp` alone.

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

### A marker does not guarantee a frame

Build 107-234 sets `0x99999999` on adapter 1 and leaves nothing usable behind
it: `sp` and `ra` zero, `epc` at `0x00001008` down in the low shared-memory
hole, cause recorded as `Interrupt` rather than a memory fault.  Fed through the
stack arithmetic that frame yields a confident two-gigabyte "overflow", which is
how the tool originally reported it.

`tools/eicon_4bri_trap.py` now sanity-checks first.  A frame is rejected when
`sp` is zero or outside the cached window, when `epc` is not in that window, when
`ra` is zero, or when `sp` sits above the image's declared stack top:

```
warning: this is not a live context (sp is zero; epc 0x00001008 is not in the
         cached image window; ra is zero)
stack analysis skipped: the frame is not a live context, so its depth and image
         overlap would be meaningless
```

Both real frames -- the archived task-3 overflow and the live null-pointer trap
-- pass unchanged, so the guard costs nothing on genuine data.

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

   The caller and the structure are already identified above; what remains is to
   find where `*(s0+12)` is *assigned*, and why that has not happened by the time
   the first inbound frame arrives.

## Getting the card to boot: things to try

None of these requires patching firmware.  Note the split in purpose: the first
is the best *diagnostic*, the second is the best bet for actually getting a
working card, and they are not the same experiment.

**1. ~~Boot with the S0 lines down, then bring them up.~~ Done; the race reading
is dead.**  With the Cisco's BRI interfaces shut down the card traps identically
-- 39 of 40 saved dwords equal, the only difference being the unmaintained `t9`.
No line, no D-channel traffic, same fault at the same instruction.  Nothing is
losing a race; the allocation never happens at all in this configuration.  See
the fault section above for the control dump.

**1b. ~~The management interface.~~ Checked; it is a consequence, not a cause.**
`divactrl mantool` is the management path (`divas_status` is a Perl wrapper
around `mantool -b`).  On the trapped card its descriptor list works --
`-L` returns `6:{1,2,3,4,1000,1001}` -- but every variable read fails with
`can't open user mode IDI[1], errno=19` (ENODEV), which is why `divas_status`
prints `N/A` and zero channels.

That looked like a promising root cause and is not one.  The card's own XLOG
shows management working *before* the trap:

```
0:0000:014 - CREATEID ok: context:0  assigned Id:1  freeIds=f0
0:0000:014 - manufacturer features: 0x0b203f94
0:0000:433 - CREATEID ok: context:ff assigned Id:2  freeIds=ef
0:0000:444 - DELETEID ok: deleted  Id:2  freeIds=ef
0:0000:461 - CREATEID ok: context:ff assigned Id:2  freeIds=ef
```

Card-side management entities are created and queried successfully through
0.461 s.  The interface only becomes unreachable once the MIPS stops at ~1 s.
So management failure is downstream of the trap, and cannot be what fails to
allocate the statistics block.

**2. ~~Try the third protocol image.~~ Tried; it is worse.**
`docs/firmware/build-109/te_dmlt.qm` (`cae6e7eb…`) is **Build 107-234**, the same
protocol version `6.03 104-905` as the 107-136 image and a later build in the
same series -- a close sibling, and the best of the untried candidates on paper.
Loaded on the card it fails *earlier* than either image tested before: its XLOG
never gets past the six-line header, so there is no `Protocol:` banner, no
`DSP OK` and no `Hardware Initialisation done`.  `divactrl load` errors and
`CardState` reports `trapped`.

It does leave a trap marker on adapter 1, unlike 108-130, but the frame behind
it is empty -- see the plausibility note below.  The dump is kept:

```
artifacts/diva-4bri-v1-107-234-trap-bar2.bin
```

The DIVA SE `TE_DMLT.QM0..QM3` set (build 99-45) remains untried.  On this
evidence, though, swapping protocol images is not the easy win it looked like:
of three builds only 107-136 gets the card running at all, which is some
support for the 108-744 DSP set being matched to it specifically.

Order of standing for the three tested images:

| Image | Build | Reaches |
| --- | --- | --- |
| `te_dmlt.qm.107-136` | 107-136 | full init, L1 + L2, then the null-pointer trap |
| `build-109/te_dmlt.qm` | 107-234 | dies in bootstrap, before announcing itself |
| `te_dmlt.qm` | 108-130 | does not start; no trap marker at all |

**3. Vary the DSP pairing** against the 107-136 protocol image -- `107-708`,
`109-789` and the stock `117-926` are all present.

**4. Configure only the two adapters that have lines.**  `CCardSUBADAPTER[1..4]`
currently declares four.  The line-down result rules out any timing motive for
this, but it still tests whether the missing allocation is per-adapter: if the
statistics block is only ever populated for adapters the configuration actually
provisions, declaring fewer -- or different -- adapters changes which contexts
the periodic tick walks.

**5. ~~Find the assignment.~~ Done: there isn't one.**  See "Where `+12` is
written: nowhere" above.  The field is zeroed by a constructor that provably
runs and never assigned in any code that handles the structure, and no use of it
is null-guarded.

**6. Read the driver for the missing setup request.**  Since the firmware
expects a single setup-time assignment that never happens, the likeliest missing
piece is a request the host driver should issue.  `docs/divas4linux-master/` is
the same 9.6.8-124.26 version that is running, so it can be read for what the
startup sequence *does* send -- and compared against what the 107-136 image
waits for.  The driver is many build series newer than this firmware, so an
older release may issue something that has since been dropped; no older release
is on hand to test that directly.

A targeted binary patch is the last resort, and note one complication before
attempting it: the caller consumes the callee's return value (`addu s1, v0,
zero` in the delay slot), so stubbing the function out with `jr ra` / `nop`
would leave `s1` holding garbage.  Any patch has to return a sane `v0`.

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
sudo /usr/lib/divas/divactrl mantool -c 1 -L        # descriptor list (driver-side)
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
- `mantool` splits: `-L` answers from the driver and works on a dead card, while
  `-r` needs the card and fails with `errno=19`.  A working `-L` is therefore no
  evidence that the card is alive.  With `-b` the read failure is silent -- it
  prints nothing rather than reporting the error -- so run reads without `-b`
  when diagnosing.
- When diffing a dump against a firmware file, confirm which build is actually
  resident first, via the banner at `+0x80`.  The reset vector is identical
  across builds, so a first-32-bytes comparison will match the wrong file.
- **Do not trust the saved s-registers in an `MP_XCPTC` frame.**  In the live
  trap the frame records `s0 = 0x00000001`, which cannot have been the value at
  fault time: the caller had just executed `lw a0, 12(s0)` successfully, and an
  `s0` of 1 would have faulted there instead, at a different EPC.  `epc`,
  `badvaddr`, `gp`, `sp` and `ra` all cross-check exactly against the
  disassembly, so the layout is right where it is load-bearing, but either the
  exception handler clobbers callee-saved registers before storing them or that
  part of the frame differs.  The consequence for analysis is concrete: the
  adapter context is identified from the code, not the frame, and the live state
  byte at `+107` -- which would say which dispatch handler ran -- cannot be
  recovered from the snapshot.

The AT interface on `/dev/ttyds1..8` is **emulated entirely in the Divatty
driver**: `ATI`, `AT+MS=?` and friends answer normally, with zero card
interrupts, on a completely halted card.  It proves nothing about the hardware.
`/dev/ttyds0` is a control node and does not answer AT at all.
