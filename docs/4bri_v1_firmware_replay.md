# Diva 4BRI-8M PCI v1 firmware replay

## Hardware oracle

The card on `eicon420` is PCI `1133:e012`, card type 22, serial 4931.  The
protocol file loaded by the driver is byte-for-byte the tracked
`docs/firmware/te_dmlt.qm`:

```
sha256 7f5e20da79b31f3a4101de209c19bf9a6173586134f7036b5e62a0173df3f409
TE_DMLT, Build 108-130, Protocol 6.03(V17) 106-1 [F#00FF]
base 0x80000000  entry 0x800ba318  sp 0x80135e20  gp none
```

`gp none` means that the flat image has no boot-time global `$gp`; it does not
mean the register is unused.  Code modules establish/use their own value.

The current build starts and publishes its signature, then stops answering the
first management-interface operation.  It leaves an XLOG but no
`0x99999999` exception marker.  An older protocol build tested on the same card
produces a concrete exception:

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

So the older build's failure is a **task-3 stack overflow or a bad task-3 stack
allocation/context**, eventually surfacing as the invalid GP-relative load. It
is not PCI interrupt routing. The current build's silent hang may have a
different immediate cause, so the two builds must not be conflated.

## Snapshot replay

`tools/eicon_4bri_trap.py` reads the `MP_XCPTC` exception frame that
`divas4linux` recovers at BAR2 offsets `0x80/0x90`, restores all 32 MIPS
registers plus HI/LO, and resumes at EPC under Unicorn.

A read-only 4 MiB BAR2 dump was captured from the physical card with the older
build and kept as an untracked artifact:

```
artifacts/diva-4bri-v1-trapped-bar2.bin
sha256 3a35fa44072fcc1186bc0540176526e135360a46b738f505e0d42cd6f544f08c
```

Run it with a Python environment containing Unicorn:

```bash
../v90modem/.venv/bin/python tools/eicon_4bri_trap.py \
  artifacts/diva-4bri-v1-trapped-bar2.bin \
  --firmware artifacts/te_dmlt.qm.build-107-136
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

This is snapshot replay, not cold boot.  It is the first oracle for retargeting
the PRI harness without guessing about register state.

## Cold-boot work remaining

1. Load four copies of the flat `.qm` image using the v1 card's four 1 MiB
   logical-adapter layout and reproduce the driver's header patches/config RAM.
2. Stage card type 22 / file set 7: 44 DSP downloads, 609,496 bytes, with the
   shared DSP table anchored at `0x80135e20`.
3. Model the v1 4BRI DSP ports and eight ADSP cores rather than the PRI's 30
   blocks.
4. Derive `.qm` init, main-loop, host read/write and request-dispatch anchors.
   PRI `BIAS + offset` anchors are invalid for this image.
5. Reproduce the driver's initial management request and compare execution
   against the hardware XLOG.  First target the older build's deterministic
   task-3 exception; then use that instrumentation to find where build 108-130
   stops progressing.
