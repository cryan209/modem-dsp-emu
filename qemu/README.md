# Diva 4BRI v2 PCI frontend

`diva-bri.c` registers PCI `1133:e013`, subsystem `8001:0014`, with the four
measured BAR apertures and legacy INTA. It forwards register operations to
`tools/eicon_bri_rpc.py`, which owns the existing `BriCard` and real
`BriMachine`/ADSP execution. The frontend contains no protocol responses,
ready signatures or firmware function replacements.

Verified with QEMU **11.1.1** on the local ARM64 macOS host:

- PCI enumeration, BAR sizing/assignment, VPD serial reads, 64-bit SDRAM
  accesses split into supported transactions, and IOAPIC IRQ11 raise/lower.
- Explicitly staged, unmodified reentrant firmware boots all eight DSPs
  and publishes four real `0x4447` signatures through virtual-time callbacks.
- A separate Linux 4.9.0-13 guest, starting with empty board RAM, loads its
  **unmodified Debian Diva modules**. The driver binds, reads serial 12196,
  registers IRQ11, and creates all four controller entries. It then exits
  cleanly with no backend faults. No disks, NICs, host shares or physical
  PCI passthrough are attached.

- With the optional legacy load packets, that same unmodified driver writes
  the firmware through BAR2 and executes RESET/START. All four firmware
  signatures and eight DSP cores are checked from the final board state;
  the master reports `active` and the three proc entries retain `slave`.

Guest signalling entity allocation/removal also passes through the stock
`diva_idi` module. Native HDLC guest calls, 2,048-byte transfers and teardown
now also pass on all eight endpoints over two call cycles. Other guest
bearer/modem profiles and the stock userspace FPGA loader remain unqualified. See the limits below.

## Build

Use a C compiler, pkg-config/GLib, Python 3.10+, and an interpreter with
Unicorn for the board backend. The commands below run from the repository
root. The build helper changes only `hw/misc/diva-bri.c` and its Meson entry
in the supplied QEMU source tree; it installs nothing system-wide.

```sh
work=/private/tmp/bri-qemu-demo
mkdir -p "$work"
curl -fL https://download.qemu.org/qemu-11.1.1.tar.xz -o "$work/qemu.tar.xz"
tar -xf "$work/qemu.tar.xz" -C "$work"
python3.14 -m venv "$work/build-tools"
"$work/build-tools/bin/python" -m pip install meson ninja setuptools wheel
python tools/eicon_bri_qemu_build.py \
  --source "$work/qemu-11.1.1" --build "$work/build" \
  --python "$work/build-tools/bin/python" --ninja "$work/build-tools/bin/ninja"
```

The official QEMU tarball used here has SHA-256
`079ffbff8a7111bbc89022107cbabf3bbfd614d5fc9d7cc675991196aca12482`.
`bri-build.json` records the frontend source hash and executable path.
The ordinary installed QEMU executable does not contain this new device.

## Bus and firmware checks

Run with the project interpreter that has Unicorn/JIT execution support:

```sh
python tools/eicon_bri_qtest.py --qemu "$work/build/qemu-system-x86_64" \
  --out artifacts/qemu-pci.json
python tools/eicon_bri_qtest.py --qemu "$work/build/qemu-system-x86_64" \
  --firmware-dir docs/firmware --out artifacts/qemu-firmware.json
```

Each run saves its backend log, QEMU log and RPC trace beside its JSON.
`--firmware-dir` stages firmware explicitly for diagnostics, before QEMU
connects. The guest-visible RISC write and virtual timer callbacks still
execute the actual firmware. The recorded boot reaches all four signatures
after 300 ms of **functional virtual time**, not a qualified oscillator or
wall-clock timing measurement.

## Reproduce the stock Linux driver attachment

These archived Debian inputs match each other and include the stock modules.
The extraction commands use libarchive tar, available as `/usr/bin/tar` on
macOS or `bsdtar` on Linux. Device nodes are omitted during extraction; the
packer emits their CPIO metadata without root privileges.

```sh
mkdir -p "$work/guest/root"
curl -fL https://archive.debian.org/debian/pool/main/l/linux/linux-image-4.9.0-13-amd64_4.9.228-1_amd64.deb \
  -o "$work/guest/linux-image.deb"
curl -fL https://archive.debian.org/debian/dists/stretch/main/installer-amd64/current/images/netboot/debian-installer/amd64/initrd.gz \
  -o "$work/guest/installer-initrd.gz"
ar -p "$work/guest/linux-image.deb" data.tar.xz > "$work/guest/data.tar.xz"
tar -xf "$work/guest/installer-initrd.gz" -C "$work/guest/root" \
  --exclude=dev/console --exclude=dev/null
tar -xf "$work/guest/data.tar.xz" -C "$work/guest/root" \
  ./boot/vmlinuz-4.9.0-13-amd64 ./lib/modules/4.9.0-13-amd64/kernel/drivers/isdn
python tools/eicon_bri_guest_initrd.py --root "$work/guest/root" \
  --out "$work/guest/bri-initrd.gz"
python tools/eicon_bri_guest.py --qemu "$work/build/qemu-system-x86_64" \
  --kernel "$work/guest/root/boot/vmlinuz-4.9.0-13-amd64" \
  --initrd "$work/guest/bri-initrd.gz" --out artifacts/qemu-linux-driver.json
```

Input SHA-256 values:

| Input | SHA-256 |
|---|---|
| Kernel package | `d410dd577fac677bb50978d5fe91b622d1520b783cb4ef23a1bcd7851606e326` |
| Installer initramfs | `2665026ad4e561a5345b29158fd06494ac0ad3aeca324a5c3f705083d018c7ea` |

The output records the kernel/initramfs hashes, all four controller serials
and states, exit status and any backend faults. `guest-init` reports success
only after the real module has bound and its controller proc entries exist.

## Guest-driven firmware download and start

Starting from the extracted tree above, add the legacy firmware packets
before packing the initramfs. This uses the archived driver's real 24-byte
admin ABI. The backend starts with empty SDRAM; the guest writes all data.

```sh
python tools/eicon_bri_guest_load.py --firmware-dir docs/firmware \
  --out "$work/guest/root/bri-load"
python tools/eicon_bri_guest_initrd.py --root "$work/guest/root" \
  --out "$work/guest/bri-load-initrd.gz"
python tools/eicon_bri_guest.py --qemu "$work/build/qemu-system-x86_64" \
  --kernel "$work/guest/root/boot/vmlinuz-4.9.0-13-amd64" \
  --initrd "$work/guest/bri-load-initrd.gz" --shared-ram --require-firmware \
  --out artifacts/qemu-linux-firmware-load.json
```

`--shared-ram` maps one private temporary 16 MiB file into both QEMU BAR2
and the MIPS backend. It avoids a socket exchange for each byte the stock
driver writes. The backend creates the file exclusively and removes it on
exit. All control registers and interrupts still use the framed transport.
`--require-firmware` additionally checks successful driver START, an active
master controller, four actual `0x4447` signatures and eight allocated DSPs.
It does not treat successful driver attachment as firmware success.

The verified capture is
`artifacts/bri-card-20261026/qemu-linux-firmware-load-verified.json`, with
475 transport operations, a 10,300,000-instruction MIPS budget, no faults,
and clean guest shutdown. The 109 SDRAM packets contain the shipping four
legacy protocol images and 59 distributed DSP downloads. FPGA programming
is not part of this diagnostic packet sequence.

## Guest userspace IDI lifecycle

After preparing the firmware-load tree above, add the IDI packets and
repack. The first four-byte write selects an adapter. Subsequent requests
use Linux 4.9's 16-byte `diva_um_idi_req_hdr_t`; replies have a 152-byte
header. The probe checks the initialized fields, not union padding.

```sh
python tools/eicon_bri_guest_idi.py --out "$work/guest/root/bri-idi"
python tools/eicon_bri_guest_initrd.py --root "$work/guest/root" \
  --out "$work/guest/bri-idi-initrd.gz"
python tools/eicon_bri_guest.py --qemu "$work/build/qemu-system-x86_64" \
  --kernel "$work/guest/root/boot/vmlinuz-4.9.0-13-amd64" \
  --initrd "$work/guest/bri-idi-initrd.gz" --shared-ram \
  --require-firmware --require-idi --out artifacts/qemu-linux-idi.json
```

The stock `diva_idi.ko` opens every controller through `/dev/DivasIDI`,
checks the two-channel features response, and completes two signalling
ASSIGN/REMOVE cycles per controller. Successful replies must be exactly
152 bytes, have indication type 3, contain `ASSIGN_OK=0xef` / `OK=0xff`,
return channel zero and declare no payload. Reads have bounded retry waits.
Reassignment verifies removal released the entity; proc counts must return
to one baseline internal entity on every controller. IRQ11 must advance
beyond its pre-IDI count. Missing cycles, leaked entities, missing firmware
state or no new interrupts fail `--require-idi`.

`qemu-linux-idi-verified.json` records all eight ASSIGNs and eight REMOVEs,
IRQ11 counts `0 → 2 → 111`, baseline counts `[1,1,1,1]`, four firmware
signatures, eight DSPs, zero faults and clean guest shutdown. It qualifies
signalling entity lifecycle, not guest call/media operation. The ABI and
implementation are the unmodified upstream
[um_xdi.h](https://github.com/torvalds/linux/blob/v4.9/drivers/isdn/hardware/eicon/um_xdi.h),
[um_idi.c](https://github.com/torvalds/linux/blob/v4.9/drivers/isdn/hardware/eicon/um_idi.c)
and [divasi.c](https://github.com/torvalds/linux/blob/v4.9/drivers/isdn/hardware/eicon/divasi.c).

## CAPI diagnostic and the archived 64-bit driver

`tools/eicon_bri_guest_capi.py` prepares a freestanding x86-64 Linux
client using clang's assembler. It registers a CAPI application, queries
the controller count and two-channel profiles, and sends the standard
26-byte LISTEN request. A successful confirmation must be exactly 14
bytes and match application, message, controller, command/subcommand and
`Info=0`. The ELF packer rejects unresolved references or other allocated
sections; the executable has no libc dependency and a nonexecuting stack.

```sh
python tools/eicon_bri_guest_capi.py --out "$work/guest/root/bri-capi"
# Repack the initramfs as above, then add --require-capi to the guest probe.
```

**This CAPI diagnostic currently fails with the archived 64-bit modules.**
All four stock CAPI controllers become ready, application registration and
the first profile query pass, but LISTEN write returns `EIO` with CAPI
error `0x1102` (bad message), before any confirmation. The preserved run
is `qemu-linux-capi-v4.json`; its audit requires `guest_capi=false` and
`completed=false`, while firmware boot and IDI lifecycle remain passing.
The prior `-v2` and `-v3` failures are retained as diagnostic history.

The matching upstream `capi20.h` puts native `void *pData` members in its
message union. On x86-64 that moves `CAPI_MSG.info` to offset 16, although
the CAPI wire header remains 12 bytes. Compiling that unmodified header
confirms offsets 16 on x86-64 and 12 on i386. The actual Debian module's
`api_put.part.30` disassembly passes `message+0x10` into `api_parse` while
subtracting `0x0c` from the wire length. The bad-message result is therefore
consistent with this driver-side layout mismatch. Source hashes, the
layout compile probe and module disassembly are preserved; the device
model and wire message were not altered to accommodate it.

The vendor-default CAPI module below now qualifies standard LISTEN on all
four controllers. Native HDLC call/data qualification is described below.
The archived-module diagnostic remains a separate failure.
The source is the upstream
[capi20.h](https://github.com/torvalds/linux/blob/v4.9/drivers/isdn/hardware/eicon/capi20.h),
[message.c](https://github.com/torvalds/linux/blob/v4.9/drivers/isdn/hardware/eicon/message.c)
and [CAPI userspace ABI](https://github.com/torvalds/linux/blob/v4.9/include/uapi/linux/capi.h).

## Vendor-default CAPI: registration, profiles and LISTEN pass

The available vendor source (`docs/divas4linux-master/kernel`, Sangoma
9.6LINSU8 packaging) uses eight-byte arrays for its 64-bit wire pointer
fields. Its unchanged x86-64 header has the correct payload offset 12.
The default `DIVA_EICON_CAPI=1` interface provides `/dev/capi20` directly.
It can coexist with the stock `divas`, `divadidd` and `diva_idi` modules;
the stock `capi.ko` character-device module is not loaded in this variant.

The build uses a separate offline VM with matching Debian GCC 6 and kernel
headers. All 43 package archives are checksum-pinned in
`debian-stretch-build-packages.json`. No package scripts or Linux compiler
binaries execute on the host. The build VM has no network, BRI endpoint,
host share or physical passthrough; a new disposable 16 MiB virtio disk
exports the module. The 141 driver source files remain unchanged. The only
extra include is `kernel-compat/linux/stdarg.h`, which includes the
compiler header for old kernel SDKs that lack that wrapper.

```sh
python tools/eicon_bri_guest_build_env.py \
  --installer-initrd "$work/guest/installer-initrd.gz" \
  --source docs/divas4linux-master/kernel \
  --out "$work/guest/build-root" --cache "$work/guest/compiler-debs"
python tools/eicon_bri_guest_initrd.py --root "$work/guest/build-root" \
  --init qemu/guest-build-init --out "$work/guest/compiler-initrd.gz"
python tools/eicon_bri_guest_compile.py --qemu "$work/build/qemu-system-x86_64" \
  --kernel "$work/guest/root/boot/vmlinuz-4.9.0-13-amd64" \
  --initrd "$work/guest/compiler-initrd.gz" --out artifacts/vendor-capi-build.json
```

For the probe, use the firmware/IDI tree from above, adding `bri-capi` as a
new directory. If it already contains an earlier CAPI diagnostic, keep that
tree separately and use a fresh copy for this variant.

```sh
python tools/eicon_bri_guest_capi.py --out "$work/guest/root/bri-capi" \
  --vendor-module artifacts/vendor-capi-build.ko
python tools/eicon_bri_guest_initrd.py --root "$work/guest/root" \
  --out "$work/guest/vendor-capi-initrd.gz"
python tools/eicon_bri_guest.py --qemu "$work/build/qemu-system-x86_64" \
  --kernel "$work/guest/root/boot/vmlinuz-4.9.0-13-amd64" \
  --initrd "$work/guest/vendor-capi-initrd.gz" --shared-ram \
  --require-firmware --require-idi --require-capi \
  --out artifacts/qemu-linux-vendor-capi.json
```

`vendor-capi-build-v5.json` records a successful 3,924,488-byte module build.
The source and package hashes are in `vendor-capi-source.json` and
`vendor-build-packages.json`. The reproducible environment tool was also
run into a fresh directory: all 16,876 input files/symlinks, executable
modes and file contents matched the successful build tree, excluding its
added environment manifest (`vendor-build-root-repro.json`). Earlier
failed compiler-guest setup/common-interface attempts are retained.

`qemu-linux-vendor-capi.json` passes firmware boot, the full preceding IDI
lifecycle, CAPI application registration, four two-channel profile queries,
and four exact successful LISTEN confirmations. It records 1,122 transport
operations, no faults, four firmware signatures, eight DSPs and clean guest
shutdown. The client hash is **identical** to the archived-driver failing
run (`2571ca1cb52b7dacdbc803ebd672e7a3fb69c25cf399e88cf89223b0540f86d9`);
the standard wire requests and board model were not changed for this pass.
This capture qualifies registration and LISTEN; the call/data qualification follows.

## Guest CAPI calls and maximum-frame transfers on all eight endpoints

`qemu-linux-capi-call-4-v3.json` passes four concurrent native HDLC calls
across both TE/NT pairs. All eight B-channel endpoints reach active state
before any payload is sent. Each endpoint sends a distinct 2,048-byte
binary message; the application compares every received byte and the
host verifier independently checks lengths and CRC32 values. Every frame
contains all 256 octet values. A second complete application/call cycle
uses fresh data, verifies reallocation and catches stale receive buffers.
The total received payload is 32 KiB across the two cycles.

The application uses standard CAPI CONNECT, CONNECT_ACTIVE, CONNECT_B3,
CONNECT_B3_ACTIVE, DATA_B3, DISCONNECT_B3 and DISCONNECT messages. PLCI and
NCCI values come from real confirmations/indications. It uses B1 HDLC,
B2 transparent and B3 transparent, with digital CIP 2. Both controlled B3
endpoints are explicitly disconnected; transparent B3 has no peer-release
handshake to wait for before clearing signalling. The initial one-call
probe preserved that client sequencing failure after successful data
transfer (`qemu-linux-capi-call-1.json`). The subsequent 18-byte eight-
endpoint smoke test is preserved as `qemu-linux-capi-call-4-v2.json`.

`--wire-pairs` is an explicit external cable fixture: IPAC D-channel peers
0/1 and 2/3, with raw SPORT peers 0/2, 1/3, 4/6 and 5/7 at the separately
qualified two-word delay. The firmware loader configures TE/NT/TE/NT in
PCINIT. Comparing its download packets with the default loader finds only
the two NT role bits changed; firmware/DSP bytes remain identical. The
backend still starts with empty SDRAM and the guest driver performs the
entire download and START. Normal runs have no peer wires by default.

Compile the application in the offline build environment above:

```sh
mkdir -p "$work/guest/build-root/bri-client"
cp qemu/guest-capi-call.c "$work/guest/build-root/bri-client/client.c"
cp qemu/guest-capi-call-start.S "$work/guest/build-root/bri-client/start.S"
python tools/eicon_bri_guest_initrd.py --root "$work/guest/build-root" \
  --init qemu/guest-client-build-init --out "$work/guest/client-build-initrd.gz"
python tools/eicon_bri_guest_compile.py --qemu "$work/build/qemu-system-x86_64" \
  --kernel "$work/guest/root/boot/vmlinuz-4.9.0-13-amd64" \
  --initrd "$work/guest/client-build-initrd.gz" --kind executable \
  --out artifacts/capi-call-client-build.json
```

Prepare a **fresh** probe tree, keeping earlier diagnostic trees separately:

```sh
mkdir -p "$work/guest/call-root"
tar -xf "$work/guest/installer-initrd.gz" -C "$work/guest/call-root" \
  --exclude=dev/console --exclude=dev/null
tar -xf "$work/guest/data.tar.xz" -C "$work/guest/call-root" \
  ./lib/modules/4.9.0-13-amd64/kernel/drivers/isdn
python tools/eicon_bri_guest_load.py --firmware-dir docs/firmware \
  --out "$work/guest/call-root/bri-load" --wire-pairs
python tools/eicon_bri_guest_idi.py --out "$work/guest/call-root/bri-idi"
python tools/eicon_bri_guest_capi.py --out "$work/guest/call-root/bri-capi" \
  --vendor-module artifacts/vendor-capi-build.ko \
  --call-client artifacts/capi-call-client-build.elf --call-count 4 --call-repeats 2
python tools/eicon_bri_guest_initrd.py --root "$work/guest/call-root" \
  --out "$work/guest/call-initrd.gz"
python tools/eicon_bri_guest.py --qemu "$work/build/qemu-system-x86_64" \
  --kernel "$work/guest/root/boot/vmlinuz-4.9.0-13-amd64" \
  --initrd "$work/guest/call-initrd.gz" --shared-ram --wire-pairs \
  --require-firmware --require-idi --require-capi --require-calls 4 \
  --require-call-repeats 2 --timeout 600 --out artifacts/qemu-linux-capi-calls.json
```

The verified run records IRQ11 `0 → 2 → 103 → 202 → 254`, all four real
firmware signatures, eight DSPs, 2,428 successful transport operations and
clean guest shutdown. Before/after entity counts are `[4,4,4,4]` in both
cycles. These include the vendor driver's persistent listeners/non-call
link and the internal driver entity; they are not an application leak.
The verifier also requires event ordering that proves eight concurrent
bearers, all data before teardown, complete B3/signalling release, fresh
payloads each cycle and no extra entities afterward. Later call IRQs
cannot mask missing earlier IDI IRQ activity.

This is bounded native HDLC guest call/media qualification, not modem CAPI,
other B protocols, long-stream reliability, physical timing or FPGA loader
compatibility. It adds no firmware-function stubs or manufactured responses.

## Transport and limits

The local Unix stream uses little-endian `<IBBHIQ` requests (magic, operation,
BAR, width, offset, value) and `<IIQII` replies (magic, status, result, flags,
reserved). Magic is `0x42524931`; HELLO negotiates version 1. Operations are
HELLO=0, READ=1, WRITE=2, ADVANCE=3, CONFIG_READ=4, CONFIG_WRITE=5, RESET=6.
Reply flags expose IRQ (bit 0), CPU-running (bit 1), fault (bit 2), and
local-decode unavailable during PLX reset/reload (bit 3). Errors latch a backend
fault and stop the VM; unsupported accesses do not return invented values.
Reset clears controls/cores while preserving SDRAM.

QEMU's default virtual tick is 1 ms with 50,000 requested MIPS instructions,
matching the existing functional scheduling ratio. It is not physical clock
qualification. The backend is single-threaded and sockets are local only.
Migration is explicitly unsupported.

Remaining work includes other guest bearer/modem profiles, longer transfers
and soak/restart coverage,
alternate FPGA designs and further PLX registers, power-state transitions,
EEPROM writes and further hotplug/reset coverage.
The archived guest driver uses the legacy `.2q0`–`.2q3` layout; the staged
firmware qtest uses the separately qualified reentrant `.2qm` layout.
The guest-driven load separately qualifies the legacy layout using actual
driver writes and START; it does not establish complete stock userspace
loader or all-profile guest call compatibility.


To include the qualified stock FPGA download, add
`--fpga-image docs/divas4linux-master/firmware/ds4bri2.bit` to the firmware
packet generation command above, and `--require-fpga` to the guest run.
Allow `--timeout 900` for the serial GPIO traffic. The physical trace and
model are documented in `docs/bri_card_model.md`; generic FPGA fabric,
CRC engines and alternate designs remain unsupported. The FPGA-plus-calls
capture passes 937089 RPC operations, firmware/IDI and both complete
four-call cycles with 32768 bytes verified and clean resource release.


The qtest now also pulses the driver's PLX soft-reset and EEPROM-reload
controls. It requires all-ones reads and ignored writes while local decode
is unavailable, then the original SDRAM marker after reload. Both ordinary
MMIO and shared-file RAM paths pass. The shared path uses an overlapping
MMIO fallback when direct RAM is disabled, preserving all-ones reads.
A subsequent reentrant firmware boot still produces four real signatures.
This qualifies the captured shipping reset sequence, not arbitrary bridge
reprogramming or EEPROM writes.


For the measured stopped-card power cycle, add this before packing the
probe root:

```sh
python tools/eicon_bri_guest_power.py --out "$work/guest/call-root/bri-power"
```

Use the current `qemu/guest-init` and add `--require-power-cycle` to the guest
command. The script cycles PCI PMCSR through D3hot and D0 before loading
firmware, compares actual configuration reads, and restores D0 on failure.
The verifier also requires actual backend transitions `[3,0]` and final D0.
No BAR access in D3hot, running-firmware power transition, D3cold, electrical
power removal or wake events are qualified. See the physical trace and
power-state section of `docs/bri_card_model.md` for the measured boundary.


Power qtest coverage now includes an already pending source. It requires
IRQ11 to lower in D3hot and remain low after D0, with IRQ_TEST cleared and
interrupt enables retained. This matches the masked-source spare-card
trace; a D0-only timing control retains the latch. The shared-path test boots
real firmware afterward. BAR responses during D3hot and transitions with
running firmware remain unqualified.


The guest CAPI reconnect probe accepts `--call-repeats 1..64` when preparing
its initramfs and `--require-call-repeats 1..64` when verifying a run. Each
cycle closes the application and calls, checks entity counts and IRQ
progress, then allocates fresh calls and transfers eight distinct 2,048-byte
frames. With four calls, 64 cycles would verify 1 MiB of received payload.
The client accepts decimal round indices through 64, and its payload records
the round to detect stale receive data. Increase the guest timeout for long
runs. The actual 64-cycle QEMU run now passes as described below. The synthetic
verifier regression remains a separate check of audit bounds and stale data.


### 64-cycle guest CAPI reconnect soak (2026-10-28)

`artifacts/bri-card-20261026/qemu-linux-capi-soak64.json` passes 64 complete
cycles of four concurrent native HDLC calls, with all eight endpoints active
before media starts each cycle. The updated client was compiled in the offline
Linux build VM (`capi-call-soak64-client-build.json`). Each cycle verifies eight
fresh 2,048-byte frames, for 512 received frames and 1,048,576 bytes total.
Every cycle clears B3 and signalling, closes the application and returns to
entity counts `[4,4,4,4]`. IRQ11 advances from 105 after IDI setup to 3531
after the final release. The host independently verifies every frame CRC,
cycle ordering, IRQ progression and resource counts.

This run also passes the stopped-card D3hot/D0 sequence, the shipping FPGA
load, firmware boot with four `0x4447` signatures and eight DSPs, and stock
IDI lifecycle checks. It finishes with 977,947 successful transport operations,
no backend or RPC faults, final D0 and clean guest shutdown. The raw serial,
board and RPC records share the report basename; `capi-soak64-manifest.json`
pins source and result hashes. The retained actual audit input is
`tests/data/bri_guest_soak64_linux49.json`.

Use `--call-repeats 64` when generating the client tree and
`--require-call-repeats 64 --timeout 7200` when running the guest. This
qualifies bounded native HDLC reconnect reuse in the functional emulator;
it does not establish modem profiles, uninterrupted long streams, physical
clock rates or electrical S/T behavior.
