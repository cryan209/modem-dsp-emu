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
start**, walking the adapter contexts and dereferencing a `+12` that setup left
null.  Nothing external is required, and no race is being lost -- the object's
statistics block is not merely late, it is never allocated at all (see the
measurement below).

That field is null *by design* at structure setup.  `0x800ea898` sits in an
initialiser that zeroes it along with its neighbours:

```asm
800ea888: sw v0, 0(a0)
800ea894: sw a2, 8(a0)
800ea898: sw zero, 12(a0)     ; +12 deliberately nulled
800ea89c: sw zero, 16(a0)
800ea8a0: sw zero, 20(a0)
```

so `+12` starts null and a later step is expected to populate it.  The only two
sites in the image that populate a `+12` with a statistics block are identified
below -- and the measurement below shows that neither can be the one this trap
is missing, because the object they build is never constructed on this card.

For orientation while reading the code around the fault: the read of `+12` at
`0x8009b338` happens immediately after a state-dispatched handler runs with `s0`
as its argument.  The state byte at `+107` indexes a byte table at `0x8011d4d0`,
selecting a 28-byte entry at `0x8011d490` whose first word is the handler.  Only
two distinct handlers exist across the low states, `0x800e3508` for almost all
of them and `0x800e5150` for state 8.  That dispatch is *not* where `+12` is
filled -- it was an early guess and the attach turned out to be elsewhere -- but
it is worth knowing when tracing this function.

The trap marker is on **adapter 1**.  All four logical adapters share one MIPS,
so adapter 1 trapping halts the card before any other reaches the same code,
which is why only one marker is ever set.

### The only two writers of a statistics `+12`, and why neither is this trap's

The image contains exactly two sites that store a statistics block into a
`+12`, both inside one constructor at `0x800821a8`:

```asm
80082294: beq  s3, zero, 0x800822d0   ; arg5 == 0 -> take the variant-B path
800822c0: jal  0x80061d94             ;   build statistics struct A
800822cc: sw   v0, 12(s0)             ;   ATTACH, then j 0x800823e4

800823d8: jal  0x80062d88             ;   build statistics struct B
800823e0: sw   v0, 12(s0)             ;   ATTACH

800823e4: lw   v0, 12(s0)             ; both paths converge
800823e8: beq  v0, zero, 0x800824ec   ; still null -> return 0
```

`0x80061d94` and `0x80062d88` are the two constructors: each fills a structure
with the pointers at `+184`, `+224` and `+228` that the counter routines later
dereference.  Both attach sites store the constructor's result straight into
`+12`.

`0x800824ec` is the function epilogue.  It is reached with `v0` forced to zero,
so this is a **failure return**, not a silent fall-through, and the caller does
check it -- `beq v0, zero, 0x8009cec4` at `0x8009cd70`, which unwinds through
`0x80060050` and logs via `0x8009db08` with code 231.

Read as a whole the constructor does **not** leave `+12` null on an object it
returns:

- `arg5 != 0` builds variant A and attaches it;
- otherwise variant B is built and attached **unconditionally**.  `arg6` is not
  a gate: `0x800822d0..0x800822f4` only maps it to a mode selector (`arg6 == 2`
  gives 2, `arg6 == 6` gives `arg8 == 2`, anything else gives 0), which is
  passed on to the constructor as `28(sp)`;
- the one path that returns without attaching is the signature test at
  `0x80082314..0x80082364` -- `arg9[0] < 7`, `arg9[1] == 159`, `arg9[2] == 225`,
  `arg9[4] == 162`, `arg9[6] == 161`, itself reached only when bit 1 of
  `*(*(instance + 1144) + 12)` is set -- and that path returns 0, which the
  caller reports.

So an object that exists has a statistics block.  An earlier revision of this
section described `arg6 == 2` as a second gate and the null path as a silent
return; both readings were wrong.

### Where `arg5`/`arg6` come from -- and why the answer stops mattering

`s3` and `s4` in the constructor are stack arguments -- `96(sp)` and `100(sp)`
against an 80-byte frame, so the caller's arg5 and arg6.  The single caller is
`0x8009cd4c`, and they come from two descriptors:

```asm
8009cb84: lw   s3, 1752(s0)       ; s3 = *(instance + 0x6d8), the parameter block
8009cbbc: addiu s4, s3, 2        ; descriptor 2 is at +2
8009ccdc: lbu  v1, 0(s3)          ; descriptor 1: type byte
8009cce4: bnel v1, v0, 0x8009ced4 ;   require type == 1, else log 230 and stop
8009ccec: lbu  v1, 0(s4)          ; descriptor 2: type byte
8009ccf4: bnel v1, v0, 0x8009ced4 ;   require type == 2, else log 230 and stop
8009cd10: lbu  v1, 1(s3)
8009cd1c: sw   v1, 16(sp)         ; arg5 = *(descriptor1 + 1)
8009cd20: lbu  v1, 1(s4)
8009cd30: sw   v1, 20(sp)         ; arg6 = *(descriptor2 + 1)
```

They are **not** host configuration.  `+0x6d8` is a 592-byte per-instance block
that the firmware allocates and zeroes for itself during startup, alongside the
320-byte slot table at `+0x6d0` and a 44-byte block at `+0x6d4`:

```asm
8009e038: addiu a0, zero, 592     ; size
8009e03c: jal   0x800b7bd0        ; allocate -> *(instance + 0x6d8)
8009e040: addiu a1, s0, 1752
8009e050: jal   0x800b7960        ; memset(block, 0, 592)
```

The descriptors are bytes 0..4 of that block; the rest of it is what the
constructor's remaining arguments point into (`+5` and `+69`, 64 and 256 bytes,
then `+325` and `+337`).  Nothing in `divas_cfg.rc` reaches them directly:
`tools/eicon_mips_dis.py --field 0x6d8` finds only two instructions in the whole
image that load the pointer at all, the one above and the allocation site.

### The measurement that settles it: the object never exists

The instance structures are readable in the trap dumps, so this does not have to
be argued from the disassembly:

```bash
python3 tools/eicon_4bri_instances.py artifacts/diva-4bri-v1-nullptr-trap-bar2.bin
```

The global at `0x800442d4` holds `0x801ca000` -- the `Instance(0)` the XLOG
prints -- and the other three logical adapters' instances follow at a `0xd40`
stride, each carrying the instance-0 address in its own word 0.  For **all four**
instances, in **both** trap snapshots (`nullptr` and the lines-down control):

- the 592-byte parameter block is entirely zero, so the descriptor type bytes
  are `0x00`, not `1` and `2`: the caller's type test at `0x8009ccdc` cannot
  pass, and the constructor is never even reached;
- the pool of ten 1364-byte objects at `+0x7fc` is entirely zero -- no object
  has ever been constructed;
- the current-object pointer at `+0x7f0` is null;
- the ten-entry slot table at `+0x6d0` is empty.

The surrounding memory is populated (the 16 KiB windows on either side of the
pools hold live data), so this is real emptiness and not an unmapped window.

**Therefore the trap's null `+12` is not this statistics pointer.**  The object
the trapping function was called with cannot be one of these -- none exists on
any adapter -- so the constructor's arguments, whatever they are, are not the
lever that fixes this card.  The configuration angle is closed.

#### Snapshot replay cannot identify the object: a closed avenue

The obvious next move is to let the emulator decide.  The trap halts the MIPS,
so the dump is the machine state at the fault; replay `0x8009b2a0` against it
with each plausible pointer in `a0` and see which one reaches
`jal 0x80063f68` with `a0 == 0`.  `tools/eicon_4bri_find_object.py` does
exactly that, one forked child per candidate so a bad guess cannot take the
scan down with it:

```bash
../v90modem/.venv/bin/python tools/eicon_4bri_find_object.py \
  artifacts/diva-4bri-v1-nullptr-trap-bar2.bin
```

```
7148 candidate object(s) to replay
0 of 7148 candidate(s) reach the state dispatch at 0x8009b330
0 reproduce the null dereference
```

The lines-down control behaves identically (7140 candidates, none).  The
result is not "the object is absent" -- it is that **the method cannot work
here**.  Every candidate is lost inside `0x8009b184`, the call the function
makes *before* the dispatch, and the reason is structural: the snapshot is the
state after that call already ran.  Whatever it drained is drained, whatever it
advanced has advanced, so running it a second time diverges regardless of which
pointer is in `a0` -- the real object included.  Stepping over the state handler
(`--skip-dispatch`) does not help, because the loss is earlier than that.

Two things this does settle:

- the whole of card RAM is in the dump.  `MQ_MEMORY_SIZE` in
  `kernel/mi_pc.h` is `0x00400000` -- 4 MB is the entire SDRAM on a standard
  4BRI, not the visible half of an 8 MB card -- so "the object is above the
  window" is not an available explanation for anything here;
- the state byte is no filter.  The dispatch table at `0x8011d4d0` maps almost
  every byte value to entry 1, so all 7148 candidates resolve to a real handler
  (`0x800e3508`, or `0x800e5150` for state 8).  Nothing is excluded by it.

So the object has to be recovered from a **cold boot**, where the firmware
creates it rather than us guessing it: run from the reset vector, break at
`0x8009b340`, read `s0` and the return address that reached it.  That is the
work item under "Cold-boot work remaining", and it is now the only route left.

What the object at the fault actually is remains **unresolved**.  It has the
right shape for this type (`+80` flags with bit `0x20000` set, `+107` state byte,
`+102` and `+1246..+1251` counters, `+12` statistics), but its address is not
recoverable from the snapshot: the frame's `s0` is `0x00000001`, which cannot be
right, and the saved `a1` is `0x80250924` where the code loads it with `lhu` and
it can only be a 16-bit value.  Two independent slots in that register array are
therefore not the interrupted context's.

### Correction history for this section

Two earlier readings were wrong and are retracted:

1. "`+12` is never written."  Wrong: the search keyed on functions touching the
   context's signature offsets (`+107`, `+1246..+1251`), and both attach sites
   live in a function that touches neither.
2. "`+12` is written but gated on host configuration, so this is a config fix."
   Also wrong, on two counts: `arg6` selects a mode rather than gating an
   attach, and the object carrying that `+12` is never constructed on this card
   at all, which the dumps show directly.

What survives both corrections is the original measurement -- 13 unguarded word
loads of the statistics pointer, no null test at any consumer -- and that is
still a real defect, just not one with a configuration-side remedy.

Decoding this needed MIPS-II *likely* branches; `bnel` was previously printed as
`.word`, which hid both type checks outright.  `tools/eicon_mips_dis.py` now
decodes `beql`/`bnel`/`blezl`/`bgtzl`, and takes `--field` and `--callers` for
the structure-offset and call-site scans this section rests on.


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

## The cold boot runs, and its allocations match the card exactly

`tools/eicon_4bri_boot.py` boots the 107-136 image from its reset vector under
Unicorn and reaches the firmware's idle loop at `0x800b6bd0` in about 113,000
instructions, with the build banner intact at `+0x80`.

```bash
../v90modem/.venv/bin/python tools/eicon_4bri_boot.py \
  docs/firmware/te_dmlt.qm.107-136 --dsp-length 0x94000 \
  --driver-state artifacts/diva-4bri-v1-nullptr-trap-bar2.bin \
  --verify artifacts/diva-4bri-v1-nullptr-trap-bar2.bin
```

```
instance pointer: booted 0x801ca000, card 0x801ca000  match
  instance 0 @0x801ca000  table=8022f8d0  params=8022fa3c  current=00000000  pool=80217dc0
  instance 1 @0x801cad40  table=8022fc8c  params=8022fdf8  current=00000000  pool=8021b8a8
  instance 2 @0x801cba80  table=80230048  params=802301b4  current=00000000  pool=8021f390
  instance 3 @0x801cc7c0  table=80230404  params=80230570  current=00000000  pool=80222e78
all instance allocations match the card
```

Every instance, pool, slot table and parameter block lands on the address the
hardware put it on.  Those come out of a heap whose base the driver decides and
are allocated in a fixed order, so matching all sixteen of them says the
emulated boot took the path the card took.  It also reproduces, from a cold
start, the thing the snapshots showed: `current` is null and the pools are
empty, because nothing constructs one of those objects during startup.

### What the card gives the image that a bare load does not

Three things had to be modelled before the image would boot at all, and each
was a real property of the machine rather than a fudge:

1. **The TLB, twice.**  The memory-controller registers at `0xffffe200` are in
   kseg3, which a MIPS translates through the TLB; QEMU has no entry and the
   fault surfaces as a write to `pc 0`.  Wired identity entries fix it -- and
   then the image's own TLB init at `0x8004429c` invalidates all sixteen of
   them, after which it allocates from a heap whose pointers are raw useg
   addresses like `0x00002040`.  It also clears `Status.ERL` at `0x800440a4`,
   so useg is not unmapped either.  On this card that combination can only work
   if the core has a **fixed mapping** rather than a TLB -- consistent with the
   `DBOUND` half of the exception cause the trap tool prints, which is a bounds
   register, not a TLB miss.  The harness reinstalls identity entries once the
   image's wipe loop is past.
2. **The driver's header words.**  `0x68 = 0x04` and `0x69 = 0x16` were already
   recorded, but the live header carries more, and `kernel/mi_pc.h` names them:
   `0x6c` is `OFFS_DSP_CODE_BASE_ADDR`, which the driver sets to the end of the
   protocol image (`0x801343b0`), and `0x70`/`0x74`/`0x78` are
   `OFFS_XLOG_BUF_ADDR`, `OFFS_XLOG_COUNT_ADDR` and `OFFS_XLOG_OUT_ADDR` --
   the card's own debug log (`0xa0002f08`, `0xa0002f00`, `0xa0002f04`).  The
   image reads `0x6c` at `0x800b534c` to place its heap.  Without it the arena
   starts at `0x2000` -- **on top of the image itself** -- and the boot destroys
   its own code.
3. **The DSP image length.**  `0x80060100` walks the host's PCINIT list at
   shared-RAM offset 224 for tag `0x34`, `PCINIT_DSP_IMAGE_LENGTH`, and the
   heap is placed past the protocol image plus that length.  Publishing
   `0x94000` puts every allocation exactly where the card has it; leaving it
   zero puts them all exactly `0x94000` low.  That is the DSP code the driver
   stages and the harness does not, and its size is now pinned to the range
   `0x93c4d..0x94c4c` by the arithmetic at `0x800b5350`.

The image touches three register windows on its way through: `0xffffe000`
(memory controller), `0x1f800000` and `0x1fa00000` (the on-board DSP/ISAC
space).  The harness maps them on demand as zero pages and logs them; `--mmio`
prints every access.

### Past hardware initialisation

Left alone the boot stops in a halt loop at `0x800b6bd0`, having written
`0x18888803` to `0xa0000280` -- the image's own "Error(%d): Hardware
Initialisation failed" path, reached because `0x800b5e48` drives the ISAC and
the eight ADSP cores through register pointers this machine does not have.
`--stub 0x800b5e48` returns success from it without running it, which is an
approximation and worth checking rather than assuming.  Two things say it is a
fair one:

- the same word at `0xa0000280` then comes out `0x8888880b`, which is exactly
  what the live card has there;
- `Status` settles at `0x1040ec01` against the trap frame's `0x1040ec03` -- the
  same interrupt mask, IM 2, 3, 5, 6 and 7, differing only in the `EXL` the
  trap itself sets.

With it stubbed the image runs on into its scheduler loop at `0x800633b0`,
which walks a table of 500-byte timer blocks, and all sixteen instance
allocations still match the card.

### The clock, and the two exception vectors

The image installs its vectors at run time: `0x80000200` and `0x80000380` both
jump to `0x800442e0`, differing only in the `k1` they load -- 1 and 0.  The
prologue stores that as the frame's class word (`| 0x100`), and the dispatcher
at `0x800b4e68` reads the low byte back and treats **anything non-zero as
fatal**.  So an interrupt has to arrive through the `k1 = 0` vector at
`0x80000380`; delivering one at `0x80000200` produces a frame and a halt at
`0x800b4e60`, which is the mechanism behind every `MP_XCPTC` on this card.  The
live trap's class of `0x00000101` is that other vector, as it should be.

That also confirms the frame layout the replay tool assumes: the prologue saves
register *n* at `k1 + 16 + 4n`, which is why `gp`, `sp` and `ra` cross-check
while `s0` does not.

Unicorn runs neither the CP0 timer nor `Count`, so `--ticks N` hands the image
N timer interrupts by hand: park the interrupted pc in `EPC`, put a timer cause
in `Cause`, raise `EXL`, enter the vector.  Each one also pushes `Count` just
past whatever `Compare` the image last armed (`mfc0 k0, $11; addiu k0, 1;
mtc0 k0, $9`), so a tick is worth exactly one of the image's own timer periods
rather than a rate invented here.  With the clock moving the image services
timers out of `0x80060c78..0x80060da4` and returns to its scheduler each time.

### The card's own log comes out

Because `0x70` points at the XLOG buffer, the emulated card writes the same log
the hardware writes to `/var/log/diva1.log`, and `--xlog` prints it.  That is
the most direct fidelity check available -- the card describing its own boot:

```
0:088  Instance(0)=0x801ca000 image_start=0x80000000, shared_memory=0xa0001000 card=22
0:123  Diva Server 4BRI-8M (2600)
0:199  Protocol: 'TE_DMLT, Build 107-136, Protocol 6.03 104-905 [F#00FF]'
0:247  Conf: DLI21st=1,MWIREG=1,ECTA=1,ECTF=1
...
0:648  Hardware Initialisation done.
0:666  PSI: init
0:754  CREATEID ok: context:0 assigned Id:1 freeIds=f0
0:862  D2Assign  -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
0:908  MDL: init
```

The first line is character-for-character the one the hardware logs, instance
address included, and `CREATEID ok: context:0 assigned Id:1 freeIds=f0` is the
line the real card logs at 14 ms.  (The timestamps are the image's own counter,
which advances per entry here rather than per millisecond, so they are not
comparable to the hardware's.)

The `Diva Server 4BRI-8M` line needs one more stub.  The image reads `PRId` via
`0x80044204` and requires `PRId & 0xff00` to be `0x0700` or `0x2600`; Unicorn's
CPU is neither, so it logs `CPU type is unknown! unsupported card type (19700)`
and -- more than cosmetically -- skips the jump table at `0x80127448` that runs
the per-card-type setup for card 22.  `--stub 0x80044204=0x2600` supplies an id
the image recognises, and it then names the card correctly.

Still missing from the log, against the hardware's: `DSP OK`, `L1_UP`, the
later `CREATEID`s at 0.433 and 0.461, and the `D-X`/`D-R` frames.  All of them
are downstream of hardware this harness does not model.

### The clock alone does not reach the trap

```bash
../v90modem/.venv/bin/python tools/eicon_4bri_boot.py \
  docs/firmware/te_dmlt.qm.107-136 --dsp-length 0x94000 \
  --driver-state artifacts/diva-4bri-v1-nullptr-trap-bar2.bin \
  --stub 0x800b5e48 --ticks 300 --steps 200000 \
  --watch 0x8009b2a0 --watch 0x800821a8 --watch 0x80063f68 --watch 0x8009cb40
```

```
delivered 300 timer interrupt(s)
watched addresses:
  0x8009b2a0: 0 entries      <- the trapping function
  0x800821a8: 0 entries      <- the object constructor
  0x80063f68: 0 entries      <- the counter routine that faults
  0x8009cb40: 0 entries      <- the constructor's caller
```

Three hundred of the image's own timer periods, and none of the four is entered
once.  The fault being time-*triggered* does not make it time-*caused*: the
object it dereferences still has to be brought into existence by something, and
on a machine with a clock and nothing else, nothing does.

That leaves the host, and the host side now works.

### The request queue, and the card answering it

`struct pr_ram` from `kernel/pr_pc.h` sits at the base of the adapter's shared
memory, `0xa0001000`.  The emulated card publishes `0x4447` ("GD") at `+0x1e`
-- the signature `idi_diva_4bri_start_adapter` waits three seconds for -- and
`ReqOutput - ReqInput` reports twelve free request buffers, exactly as
`pr_ready` computes it.

`--assign ID` posts an ASSIGN the way `pr_out` does: take the buffer at
`NextReq`, fill in `XBuffer.length`, `ReqId`, `ReqCh` and `Req`, relink
`NextReq` from the buffer's own `next`, and bump `ReqInput`.  There is no
doorbell in the driver's request path -- `ReqInput` moving is the signal.

One thing has to be got right: **the card only publishes its signature once the
clock is running**, so a request posted at the end of the boot goes into a queue
that does not exist yet.  The harness holds requests until `ready()`.

With the management and signalling entities assigned, the card answers in its
own log:

```
1:005  CREATEID ok: context:ff assigned Id:2 freeIds=ef
1:063  CREATEID ok: context:1f assigned Id:3 freeIds=ee
1:087  TransactId:0x1
1:115  alloc cr in use =1
```

The first of those is the live card's `0:0000:433 - CREATEID ok: context:ff
assigned Id:2  freeIds=ef` verbatim.  The emulated card is taking host requests,
creating entities and allocating call references.

### Return codes, and the ASSIGNs being genuinely accepted

`--assign 0x00=sig` attaches the CAI and user id that `add_b1()` sends, built
by `tools/eicon_idi.py` from `divas4linux`'s own code rather than by hand, and
the harness drains the return-code ring the way `pr_dpc` does.  Draining it
matters twice over: the card stops answering once the ring fills, and without
reading it an ASSIGN the card *rejected* looks exactly like one it accepted.

```
return codes from the card:
  Rc=0xef Id=0x02 Ch=0x00  ASSIGN_OK
  Rc=0xef Id=0x03 Ch=0x00  ASSIGN_OK
```

Both entities assigned cleanly, the CAI-bearing one included, with the ids the
card allocated.  That is a complete IDI transaction against emulated silicon.

### Still not the trap, and the parameter blocks were not why

With management and signalling both assigned -- bare or with a real CAI, it
makes no difference to the log or to this -- the trapping function, the object
constructor, its caller and the counter routine are entered **zero** times over
150 ticks.  Carrying the driver's parameters was the cheapest of the three
remaining differences and it is now ruled out.

What the evidence points at instead is **layer 1**.  The pieces of the D-channel
stack are already alive in our boot -- `D2Assign 0 d_id=01` and `MDL: init` are
in the log, so the layer-2 objects exist -- and the trapping function lives in
the same `0x8009xxxx` module as the rest of that stack.  What the hardware log
has that ours does not is `ACTIVATION_REQ`: the card asking layer 1 to come up.
That request goes to the ISAC, and hardware initialisation is exactly what this
harness stubs out wholesale.  Even the lines-down control -- which trapped with
no answer to that request -- had *sent* it.

So the next piece is the ISAC: enough of it for `0x800b5e48` to run for real
rather than be stubbed, or enough to answer the activation request on its own.
That is a narrower target than "model the card", and the card's own log will say
when it works, because `L1_UP` is the line to wait for.

### The DSP host port, and where hardware initialisation really stops

`--dsp` models the ADSP host port the way `0x800b61d0` and `0x800b6200` drive
it: write a DSP address to `+8`, then read or write data at `+0`.  With it,
hardware initialisation runs instead of being stubbed, and the card stops
guessing and says what is wrong in its own log:

```
0:641  [0] Starting kernel...
0:695  [0,*] DSP test failed (download not running)
0:724  [0] DSP test failed
```

Two different tests share DSP address `0x4000`, and they want opposite things
from a memoryless model:

- **presence**, at `0x800bad50`, is a plain echo -- write `0x5a5a`, read it
  back, then the same with `0xa5a5`.  A port that always answers `0xa5a5` fails
  it, and the card logs `No DSP present`;
- **liveness**, at `0x800baf50`, writes a command (`0x3e8`, `0x3e9`, ...) and
  polls the same location up to 999 times for the DSP's own reply of `0xa5a5`.
  A port that echoes fails it, and the card logs `download not running`.

Echoing the probe pattern and acknowledging everything else satisfies both
reads, and the card still reports `download not running` -- because the check
behind that message is not a mailbox read at all.  `0x800bae78` writes `0x5a5a`
to a computed address and then runs the real download handshake through
`0x800ba78c` and `0x800ba7c0`; the message is what happens when that handshake
does not come back.  Answering it means an ADSP actually executing the
downloaded kernel.

### The real chip, and the real download

That turned out to be much less work than the paragraph above assumed, because
both halves already existed.  `tools/adsp2181emu/` is an ADSP-2181 core with
`adsp2181_host_read`/`adsp2181_host_write` taking exactly the address this port
latches -- bit 14 selecting DM over PM, which is why `0x800b61d0` tests
`sltiu a1, a1, 16384` and reads twice for the 24-bit PM case.  And
`tools/eicon_dsp_stage.py` already builds the download table the driver stages,
for any card type.

`--dsp-image docs/firmware/dspdload.bin.108-744` stages it:

```
DSP code: 42 downloads, 606588 bytes at 0x801343b0 (file set 7)
```

That is a better input than the `--dsp-length 0x94000` used until now, and it
proves the arithmetic rather than assuming it: `0x801343b0 + 0x940bc + 0x2003`,
rounded down to a page, is `0x801ca000` -- the live card's instance address, now
*derived* from the real download rather than supplied to make it come out right.
`--verify` still reports every allocation matching.

`--adsp` puts a real core behind each port, running 2000 instructions after
each host access so the downloaded kernel has time to answer between polls.
With the table staged and chips to load it into, the card walks the whole
download list by name:

```
0:666  DSP task 202: V.110 Overlay (1200) Version 1.00 Build 108-744
...
1:663  DSP task 600: TIKRNL81.F34 Task Version 1.00 Build 108-744
2:619  DSP task 618: V.90 DPCM Overlay Version 1.00 Build 108-744
3:090  DSP task 722: CKRNLBR.SEC Task Version 1.00 Build 108-744
3:531  [0] Starting kernel...
```

All 42 of them, in the order the table lists them.  `--stub 0x800b5e48` remains
the fast path when the DSPs are not what is being studied: the enumeration and
download cost most of the instruction budget.

The chips are running real code by then -- after `[0] Starting kernel...` both
cores report a program counter that has moved off reset (`0x0480`), so the
download reached them and they executed it.

### Where the ADSP path stops: a reserved instruction on the MIPS

The run then ends on a **MIPS-side** exception, QEMU's `EXCP_RI` (20), with the
last traced instruction the `jr ra` at `0x800b621c` returning into
`0x800baee4`.  It is not the DSPs: they are fine, and it is not a missing
exception vector either.  The image runs with `Status.BEV` set, so hardware
exceptions vector through the boot ROM at `0xbfc00200`/`0xbfc00380` rather than
the handlers it installed at `0x80000200`/`0x80000380`, and that ROM is in no
dump here -- the harness now synthesises the two stubs, which is what the ROM
must contain, and the failure is unchanged.

An earlier revision of this section guessed that this was a real instruction
the non-standard core has and QEMU's MIPS32 does not.  The evidence does not
support that, and the guess is withdrawn:

- the last instruction to execute is the `jr ra` at `0x800b621c`; its delay slot
  is a `nop` and its return target is `lw a1, 16(s0)`, both perfectly ordinary,
  and both **byte-identical to the file** in live memory at the moment of the
  fault, so nothing has been overwritten;
- the last COP0 accesses before it are `Count` and `Status`, not one of the
  implementation-specific registers QEMU would reject;
- it is not re-entrancy from running the DSP inside a memory hook: with the
  core attached but given zero cycles, so that it never executes, the exception
  is unchanged.

What *is* established, from a controlled set, is that it takes **both** a
staged DSP download and a port that answers.  Neither alone does it:

| DSP code staged | Port answers | Result |
| --- | --- | --- |
| no | stand-in | loops on `download not running` |
| yes | none | halts on `Hardware Initialisation failed` |
| yes | stand-in | the exception |
| yes | real ADSP | the exception |

So the firmware only reaches it once the download actually proceeds.  An
earlier revision blamed the ADSP specifically; that comparison was confounded,
because the stand-in run it was compared against had no staged code.  The cause is still unknown,
and the next pass needs the CPU's own view of it: let the exception vector
through the stubs at `0xbfc00200`/`0xbfc00380` into the firmware's handler and
read the `MP_XCPTC` frame it writes, which `tools/eicon_4bri_trap.py` already
decodes.  That gives the exception code and EPC from the machine rather than
from Unicorn's summary.

**It is not the card's fault.**  The two are different exceptions in different
places at different times:

| | Hardware | This harness |
| --- | --- | --- |
| exception code | 2, TLB load / DBOUND | 10, reserved instruction |
| epc | `0x80063f68`, the counter routine | the DSP host-port write path |
| when | ~1 s in, long after init finished | during `[0] Starting kernel...` |

What they do share is a cause: **this is not a stock MIPS32 core**.  That is why
QEMU rejects an instruction it is asked to run, and it is also why the
hardware's exception label is misleading.  With an empty TLB and a fixed
mapping, a load of `0xb8` cannot be a translation failure -- there is no
translation to fail.  Exception code 2 on this core is the *bounds* register,
catching a null-pointer dereference.

### The harness cannot reproduce the trap yet, and now says so

That has a consequence worth stating plainly, because it invalidates the plan
of "just get far enough and the trap will happen".  This machine has no bounds
register, so the fault cannot occur.  Run the exact faulting instruction with
the exact faulting operand:

```
lw v1, 0xb8(a0) with a0 = 0 -> 0x0  -- no fault
```

Identity-mapped useg makes physical `0xb8` an ordinary readable address inside
the loaded image.  It is, in fact, the word immediately after the protocol
banner: the string at `+0x80` runs to `0xb6`.  So even a boot that reached
`0x8009b340` with a null `s0` would sail straight through the dereference and
carry on, and we would learn nothing.

`--dbound` supplies the missing register.  An address window is not enough to
tell a null dereference from ordinary data -- the image reads its own
`DspCodeBaseAddr` at `0x6c` from `0x800b6adc`, and its banner at `0x80`, both
legitimately -- so the check decodes the load at the faulting pc and fires only
when its **base register holds zero**.  That is a null pointer whatever the
offset, and it stops the machine with the registers intact, which is exactly
the state the exception frame failed to preserve on the card.

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

   The call site is identified above, but the *structure* is not: the two known
   writers of a statistics `+12` belong to an object that is never constructed
   on this card, and the frame does not preserve a usable `s0`.  Snapshot
   replay cannot recover it either -- see below -- so a cold boot is now the
   only route, and recovering `s0` at `0x8009b340` is one of the things it
   would buy.

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

Order of standing for every protocol/DSP combination tested:

| Protocol image | Build | DSP set | Reaches |
| --- | --- | --- | --- |
| `te_dmlt.qm.107-136` | 107-136 | 108-744 | full init, L1 + L2, then the null-pointer trap |
| `build-109/te_dmlt.qm` | 107-234 | 108-744 | dies in bootstrap, before announcing itself |
| `te_dmlt.qm` | 108-130 | 108-744 | runs, faults immediately, eats its own image |
| `te_dmlt.qm` | 108-130 | **117-926 (stock)** | same, with the same fault |

That last row was run specifically to test whether the DSP pairing was what
stopped 108-130 -- it is the fully vendor-shipped combination, protocol and DSP
both from this driver package.  It fails identically.  ~~The image is resident
in card RAM, its banner readable at `+0x80`, and the MIPS never runs.~~
Retracted below: the MIPS *does* run, and the reason only the banner survives
is that the image is gone.  **The DSP pairing was still never why 108-130
failed** -- both DSP sets produce the same fault -- but see "`divactrl load`
does not reject 108-130" for what does, and note that the DSP image's *length*
is now the one live variable worth changing.

Note also that 108-130's originally recorded symptom -- "starts and publishes
its signature, then stops answering the first management-interface operation" --
has not reproduced under any configuration tested here.  It never gets as far as
its signature.

**3. Vary the DSP pairing** against the 107-136 protocol image -- `107-708`,
`109-789` and the stock `117-926` are all present.

**4. Configure only the two adapters that have lines.**  `CCardSUBADAPTER[1..4]`
currently declares four.  The line-down result rules out any timing motive for
this, but it still tests whether the missing allocation is per-adapter: if the
statistics block is only ever populated for adapters the configuration actually
provisions, declaring fewer -- or different -- adapters changes which contexts
the periodic tick walks.

**5. ~~Find the assignment.~~ Partly done: two writers exist, and neither is
this one.**  See "The only two writers of a statistics `+12`" above.  Both live
in the constructor at `0x800821a8`, whose object is never constructed on this
card -- all four instances show an empty pool in both trap dumps -- so the
assignment that *should* have filled the trapping object's `+12` is still
unidentified.

**7. ~~Map the two gate descriptors to configuration.~~ Dead; they are not
configuration.**  The two descriptors are bytes 0..4 of a 592-byte block the
firmware allocates and zeroes for itself at `0x8009e038`, reachable from exactly
two instructions in the image, and nothing in `divas_cfg.rc` writes them.  On
this card the block is still all zeros at the trap, so the type test that guards
the constructor cannot pass and the constructor never runs.  There is no
host-side knob here, and the "works on some cards" report gets no support from
it.

**6. ~~Read the driver for the missing setup request.~~ Done; there is no such
request, and the firmware predates a documented fix.**  See below.

## What the driver does at startup, and what it does not

`docs/divas4linux-master/` is the same 9.6.8-124.26 version running on the host,
so it answers directly.  The whole of `idi_diva_4bri_start_adapter` is:

```c
start_qBri_hardware (IoAdapter);              /* release the MIPS from reset */

signature = (volatile word *)(&IoAdapter->ram[0x1E]);
for (i = 0; i < 300; ++i) {                   /* wait up to 3 s */
    diva_os_wait (10);
    if (signature[0] == 0x4447) { started = i+1; break; }
}
if (started == 0) { IoAdapter->disIrq (IoAdapter); return (-1); }

diva_os_sleep (200);
check_qBri_interrupt (IoAdapter);
```

after which `diva_4bri_start_adapter` copies the feature word across all four
`QuadroAdapter` entries and sets `Initialized = 1`.  Start the hardware, wait for
a `0x4447` signature, check the interrupt, mark initialised -- that is all.

**There is no per-adapter statistics request anywhere in it.**  That settles the
question the `+12` analysis left open: the block is allocated *firmware-side* --
the card does its own `shared_ram_alloc` -- so a missing host request is not the
explanation.  The allocation is the firmware's own job and it is not doing it.

### The firmware predates a documented fix for this exact fault

`docs/divas4linux-master/CHANGES`, release `3.0.6-107.725-1`:

> *New firmware to fix 4BRI Rev. 1 startup*

This card is Rev. 1, the symptom is startup, and the fix shipped **in firmware**
rather than in the driver.  The image in use is build **107-136**, far older
than **107-725**, so it is from before that fix.  Nothing on hand is between the
two: the available `.qm` images are 107-136, 107-234 and 108-130, and the only
one newer than the fix does not start at all.

~~**Obtaining a 107-725-or-later `te_dmlt.qm` is now the most valuable thing
that could be done for this card.**~~  **Retracted: we already have one, and it
has already been tried.**

The fix shipped in release `3.0.6-107.725-1`.  The tracked driver package is
9.6.8-124.26, and its `te_dmlt.qm` is build **108-130** -- a later series than
107-725, so it carries the fix.  That is the image the doc records as "does not
start; no trap marker".  There is no newer `.qm` to find:

| Family | Extension | Builds present | Newest |
| --- | --- | --- | --- |
| 4BRI **Rev. 1** (this card) | `.qm` | 107-136, 107-234, 108-130 | **108-130** |
| 4BRI Rev. 2 | `.2q0..3`, `.2qm`, `.2qf` | 108-971, 122-11 | 122-11 |
| PRI | `.pm`, `.pm2`, `.pm3` | 107-79, 107-199, 122-11 | 122-11 |
| Analog | `.am`, `.af` | 122-11 | 122-11 |
| `.qpm` | `.qpm` | 122-11 | 122-11 |

Every other family in the shipping package is at 122-11.  Only the Rev. 1 `.qm`
is frozen, thirteen series behind -- which is what
`DIVA_INCLUDE_DISCONTINUED_HARDWARE` looks like from the firmware side.  The
`build-109` package is no help either: its siblings are 109-76 while its `.qm`
is 107-234, *older* than the one we run.

So the firmware avenue is not "unobtained", it is **tried and failed**, and the
interesting question moves to why 108-130 does not start.

### 108-130 runs perfectly well in emulation

Which makes its failure on the card considerably more interesting.  Booted in
`tools/eicon_4bri_boot.py` -- with its DSP code placed where *its own* header
says, `0x80135e20`, not where the 107-136 card's header says -- it reaches
exactly the same place 107-136 does:

```
Instance(0)=0x801cb000 image_start=0x80000000, shared_memory=0xa0001000 card=22
...
[0] Starting kernel...
```

Its instance lands at `0x801cb000` rather than `0x801ca000`, which is simply
its larger image.  Nothing about the image is broken.

Note the trap this document is about is a *107-136* fault.  If 108-130 could be
made to load, the null-pointer trap may simply not be there.

### `divactrl load` does not reject 108-130.  The card runs it and eats itself

Retract "does not start; no trap marker", "the MIPS never runs", and "the image
is resident in card RAM, its banner readable at `+0x80`".  All three are wrong,
and they are wrong in the same way: the banner and the driver's header patches
are the *only* part of the protocol image still in card RAM by the time BAR2 is
read.  Everything above `+0x280` is debris.

Measured against the three saved 108-130 dumps
(`diva-4bri-v1-notstarted-bar2.bin`, `…-build108-pristine-bar2.bin`,
`…-build108-trapped-bar2.bin`), comparing 64-byte blocks at the same offset and
skipping all-zero blocks:

| Snapshot | Image | Blocks matching the file |
| --- | --- | --- |
| `nullptr-trap` (control) | 107-136 | 15046 / 15103 = **99.6 %** |
| `notstarted` | 108-130 | 3 / 15208 = **0.02 %** |

The load path itself is provably fine, from the same dumps:

- the driver's header patches are correct -- `0x68 = 04`, `0x69 = 16`,
  `0x6c = 0x80135e20`, which is 108-130's own `OFFS_PROTOCOL_END_ADDR`
  paragraph-aligned, so `qBri_reentrant_protocol_load` parsed the image and
  computed its layout;
- the staged DSP download at `0x80135e20` is **byte-identical** for 606,588
  bytes to what `eicon_dsp_stage.build_dsp_code_image()` produces for card type
  22 -- so `dsp_read_file` ran to completion against the right base;
- every size check in `qBri_reentrant_protocol_load` and
  `qBri_reentrant_telindus_load` passes with room to spare (`FileLength`
  0x131de0 against 0x135e20 available; DSP image 0x9417c against 0x2ca1e0).

  This card takes the **reentrant** load path -- `divas_cfg.rc` sets
  `ProtocolImageVersion`, one image for four tasks, shared RAM at
  `MP_SHARED_RAM_OFFSET` -- which is why `shared_memory=0xa0001000`.  Do not
  reason about it with the per-controller `qBri_protocol_load` arithmetic: that
  path's "Protocol code too long" test cannot pass for any image over ~213 KB
  on controller 1, and it is not the path that runs.

What destroyed the image is the card.  RAM from `0x380` up to just under the
DSP code base is filled with 6,591 copies of one 192-byte record, laid out on a
strict 192-byte stride.  That stride is the signature: the exception vector at
`0x800442e0` does `addiu k1, sp, -160`, aligns to 8, saves the register file
there and sets `sp = k1`; it then `jal`s `0x800b9998`, which opens with
`addiu sp, sp, -32`.  160 + 32 = 192 bytes of stack per nested exception.

Decoding one record with that handler's own layout (`+0` SR, `+4` Cause, `+8`
EPC, `+12` BadVAddr, then the register file at `at@+20 … sp@+132 ra@+140`, all
read straight out of the store instructions at `0x8004434c`):

```
sr=0x1040ec03  cause=0x00009008 (TLB load)  epc=0x800600e8  bad=0x00406000
ra=0x800443c8   <- inside the exception handler: this is a nested fault
sp=0x80001120   <- and 192 bytes below the frame above it
```

Every steady-state frame is identical.  So the card takes one fault, the
handler returns to the faulting instruction without fixing anything, and the
stack marches down from the image's stack top `0x80135e20` to `0x380`,
overwriting the whole protocol image on the way.  No `0x4447` signature is ever
published, the driver's three-second wait in `idi_diva_4bri_start_adapter`
times out, and `divactrl` reports the failure -- which is the "`divactrl load`
errors" that was previously read as a rejected image.

This also disposes of a standing puzzle.  The "impossible" `gp = 0x0c68d0f4`
and `t9 = 0xe48fb342` in the *107-136* trap frames are ordinary fields of this
same saved-context record; they are not registers the interrupted code ever
held.

### The faulting object sits inside the DSP download

The faulting instruction is

```asm
800600d4: lui   s2, 0x8004
800600d8: lw    s2, 0x42d4(s2)      ; s2 = the current context object
800600dc: lw    v1, 0x5ec(s2)       ; v1 = a table pointer out of it
800600e0: sll   v0, s1, 2
800600e4: addu  v0, v0, v1
800600e8: lw    s0, 4(v0)           ; <- TLB load, bad vaddr 0x00406000
```

and the frame records `s2 = 0x801c979c`.  The staged DSP download occupies
`0x80135e20 .. 0x801c9f9c`, so **the context object is 0x800 bytes inside the
DSP image**, and `+0x5ec` reads DSP payload as a pointer.  On 107-136 the
equivalent object is instance 0 at `0x801ca000` and the DSP image ends at
`0x801c852c` -- above it, with `0x1ad4` to spare.  The emulator puts 108-130's
instance at `0x801cb000`, also clear of its DSP end, so the emulated boot does
not reproduce the overlap and that is now the interesting divergence: both
images publish the same `PCINIT_DSP_IMAGE_LENGTH` (`0x9417c`, same DSP file)
and differ only in `DspCodeBaseAddr`, which should shift the heap with the DSP
image rather than into it.

### Shortening the DSP image changes nothing.  Run, and its controls

Ran on the card 2026-08-13: 108-130 against `dspdload.bin.old` (107-708),
staged image `0x8d138`, `0x7044` shorter, ending at `0x801c2f58` -- below the
`0x801c979c` the previous run faulted on.  If the firmware assumed a fixed heap
address, the overlap would have disappeared.  It did not.

```
frame @0x001080: sr=0x1040ec03 cause=0x00009008 (TLBload) epc=0x800600e8 bad=0x006f1c00
   v1=0x00101893-derived garbage, s2=0x801c0000, ra=0x800443c8, 6603 records from 0x380
```

Identical fault, identical instruction, identical runaway, identical record
count.  The context object moved *down with the DSP image*, `0x801c979c` ->
`0x801c0000`, staying below the DSP end in both runs.  So the heap is not a
fixed address colliding with a growing DSP image, and "the object sits inside
the DSP download" is a symptom of wherever the firmware thinks its heap starts,
not the cause.  Snapshot kept: `artifacts/diva-4bri-v1-108130-dspold-bar2.bin`.

Two controls came out of the same session, and both matter more than the
experiment did:

- **`divactrl load` returns 0 for 108-130.**  Both DSP sets, `exit=0`, the
  script printing ` OK` and going on to load IDI, CAPI and TTY.  The
  "`divactrl load` errors" recorded here does not reproduce at all; whatever
  produced it was situational, most likely a card left un-stopped from a
  previous attempt.  Nothing rejects this image.
- **`Dsp image length = 0x0009417c` is published**, and correctly.  It appears
  once, for port 1 -- the reentrant path sets `DspImageLength` on
  `QuadroAdapter[0]` only and copies just `features` and `InitialDspInfo` to
  the slaves, so ports 2-4 legitimately omit it.  Do not read the three
  missing lines as a bug; the tail of the load output is ports 2-4 and looks
  alarming out of context.

The outermost frame -- the one real fault, everything below it being the
runaway -- is a *different* exception from the steady state:

```
frame @0x135cc0: sr=0x1040ec03 cause=0x00001010 (AdEL) epc=0x800600e8 bad=0x00d238b7
   v0=0x00d238b3  v1=0x00101893  s1=0x00308808  s2=0x801c0000  ra=0x80062690
```

`ra` is the call site at `0x8006268c` (`jal 0x80060088`).  Both `v1` --
`*(s2+0x5ec)` -- and the index `s1` are garbage, and `v0` is unaligned, so this
is not a null pointer with an offset: the whole structure at `s2` is wrong.
The next question is what sets `0x800442d4`, and whether it was ever right.

Two operational notes from the run:

- **`dd` on `resource2` returns EIO**; BAR2 has to be read through `mmap`.  A
  six-line Python `mmap.mmap(..., MAP_SHARED, PROT_READ)` copy works and is the
  single map-copy-unmap the lockup note above asks for.
- **Recovery is trivial**, contrary to the warning above: `divas_stop.rc`, copy
  `te_dmlt.qm.build-107-136` back, `divas_cfg.rc`.  The card came back
  `active` with `DSP OK`, `Hardware Initialisation done` and `L1_UP` first
  time, with no power cycle.

### Rev. 1 is formally discontinued hardware

```c
/* 4BRI Rev 1 Cards */
#if defined(DIVA_INCLUDE_DISCONTINUED_HARDWARE)
	{CARDTYPE_DIVASRV_Q_8M_PCI,       diva_4bri_init_card},
```

`CARDTYPE_DIVASRV_Q_8M_PCI` is 22 -- this card.  The macro is defined in
`kernel/platform.h:63`, so support does compile in, but Rev. 1 sits behind a
legacy gate where Rev. 2 does not.  Worth knowing before assuming any Rev. 1
path is as well-exercised as its Rev. 2 equivalent.

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
  108-130 image, which left no marker anywhere despite taking 6,591 exceptions, and
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
- **Do not trust every register in an `MP_XCPTC` frame.**  Two slots in the live
  trap are provably not the interrupted context's.  `s0 = 0x00000001` cannot
  have been the value at fault time: the caller had just executed
  `lw a0, 12(s0)` successfully, and an `s0` of 1 would have faulted there
  instead, at a different EPC.  `a1 = 0x80250924` is impossible for the same
  reason in the other direction: the instruction two before the call is
  `lhu a1, 16(sp)`, so `a1` can only hold a 16-bit value.  `epc`, `badvaddr`,
  `a0`, `gp`, `sp` and `ra` all cross-check exactly against the disassembly, so
  the layout is right where it is load-bearing -- the same register array is
  also readable in memory at `0x801341d0..0x8013424c`, below the trapped `sp`,
  and agrees with the frame word for word.  Either the exception handler
  clobbers those registers before storing them, or those slots hold something
  else.  The consequence for analysis is concrete: the object at the fault
  cannot be identified from the frame, and neither can the live state byte at
  `+107` that would say which dispatch handler ran.

The AT interface on `/dev/ttyds1..8` is **emulated entirely in the Divatty
driver**: `ATI`, `AT+MS=?` and friends answer normally, with zero card
interrupts, on a completely halted card.  It proves nothing about the hardware.
`/dev/ttyds0` is a control node and does not answer AT at all.
