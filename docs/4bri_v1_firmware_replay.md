# Diva 4BRI-8M PCI v1 firmware replay

The card on `eicon420` is PCI `1133:e012` at `09:04.0`, a **conventional PCI**
endpoint (not PCIe), card type 22, serial 4931, IRQ 20.

## Working configuration

The card runs.  Verified on the 2026-08-12 21:33 boot, all four logical
adapters:

| Component | File | sha256 | Build |
| --- | --- | --- | --- |
| Protocol | `te_dmlt.qm` | `8bd5484a…` | TE_DMLT 107-136, Protocol 6.03 104-905 |
| DSP download | `dspdload.bin.qm` | `535c7553…` | 108-744 |

with all four `imagename` entries in `divas_cfg.rc` pointing at the DSP file.
The result:

```
Instance(0)=0x801ca000 image_start=0x80000000, shared_memory=0xa0001000 card=22
Protocol: 'TE_DMLT, Build 107-136, Protocol 6.03 104-905 [F#00FF]'
[0] DSP OK   [1] DSP OK
Hardware Initialisation done.        (adapters 1-4)
L1_UP                                (adapters 1-2; 3-4 have nothing attached)
D-X(003) 00 01 7F  /  D-R(003) 00 01 73
```

No `0x99999999` exception marker in any of the four logs.

What makes this work is that the two images are **generation-matched**: a
107-136 protocol image against a 108-744 DSP set.  The stock
`docs/firmware/dspdload.bin` is DSP build 117-926, ten build series away from
the protocol image it was being paired with.

Neither working file was tracked before; both are now, because the card depends
on them and neither is reproducible from anything else here:

- `docs/firmware/te_dmlt.qm.107-136` -- the tracked `te_dmlt.qm` is the
  different 108-130 build, described below.
- `docs/firmware/dspdload.bin.108-744` -- build 108-744 appears in no other
  collection here: not the stock file, not `dspdload.bin.old` (107-708), not
  the recovered older set (109-789), not DIVA SE.  How it was originally
  produced is not recorded; no build script or shell history survived.

The tracked `docs/firmware/te_dmlt.qm` (`7f5e20da…`, TE_DMLT Build 108-130,
Protocol 6.03(V17) 106-1) is a *different* and less useful image on this card:
it starts and publishes its signature, then stops answering the first
management-interface operation, leaving an XLOG but no exception marker.  Do
not "restore" it over the working 107-136 image.

Layout as `derive_layout` reports it for the 108-130 file:

```
base 0x80000000  entry 0x800ba318  sp 0x80135e20  gp none
```

`gp none` means that the flat image has no boot-time global `$gp`; it does not
mean the register is unused.  Code modules establish/use their own value.

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

**This is not a property of the 107-136 build.** That image boots cleanly and
brings layer 1 up, as recorded above, so the exception was induced by the
configuration in force when the snapshot was taken rather than by the firmware
itself.  The snapshot predates pairing 107-136 with the generation-matched
108-744 DSP set; driving a 107-136 protocol image with the stock 117-926 DSP
download is the most likely trigger, since the task and overlay tables the
protocol image expects would not match what the DSP set provides.  That is a
hypothesis, not a measurement -- the trigger has not been isolated by
re-running it.

The analysis of the snapshot stands on its own terms.  What changed is its
status: it is a reproducible study object for retargeting the emulator, not an
open fault on the physical card.

## Snapshot replay

`tools/eicon_4bri_trap.py` reads the `MP_XCPTC` exception frame that
`divas4linux` recovers at BAR2 offsets `0x80/0x90`, restores all 32 MIPS
registers plus HI/LO, and resumes at EPC under Unicorn.

A read-only 4 MiB BAR2 dump was captured from the physical card in the trapped
state described above, and kept as an untracked artifact:

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
   against the hardware XLOG.  The oracle to match is now the **working**
   107-136 + 108-744 boot, which reaches `Hardware Initialisation done` and
   `L1_UP` and therefore gives a full successful trace to compare against --
   a better target than either failure.  The trapped snapshot stays useful as
   a register-exact starting state, but it is no longer the thing to reproduce.

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
`/var/log/diva1.log`, and only then attach a BAR monitor.  Polling across driver
init is what is unsafe, not polling as such.
