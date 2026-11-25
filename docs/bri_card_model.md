# Diva 4BRI v2 board emulator

The stock reentrant BRI protocol, `te_dmlt.2qm` build 122-11, now boots
through the board model. All eight real ADSP emulator cores boot their
firmware, and the protocol itself publishes `0x4447` on all four
controllers. Host signalling and network-layer ASSIGN, REMOVE and
signalling reallocation succeed independently on all four queues, with
processor timer and IPAC interrupts enabled. No ready signature, IDI return
code or entity ID is synthesized.

Native HDLC and X.75 bearer payloads now pass through the DSP/SPORT path.
Modem data is qualified through V.32 at 9600 bps, and all eight V.21
endpoints pass concurrent binary transfers. The QEMU PCI frontend also
passes stock firmware boot and a separate unmodified Linux driver
attachment on all four controllers. Guest-driven legacy firmware loading
and START now also pass, as does signalling entity allocation/removal
through the stock guest IDI driver. Guest native HDLC calls and full
2,048-byte transfers pass on all eight endpoints over two complete cycles;
this is **not yet a complete PCI/ISDN replacement**.
See the remaining integration below and `qemu/README.md`.

## Run

Use an interpreter with Unicorn installed; on this workspace that is
`/Users/scottcryan/v90modem/.venv/bin/python`. On macOS, the desktop sandbox
terminates even a minimal Unicorn MIPS JIT program with SIGILL, so these
commands need the approved JIT execution context.

```sh
python tools/eicon_bri_machine.py \
  --firmware-dir docs/firmware --reentrant \
  --dsp-image docs/firmware/dspdload.bin --instructions 20000000 \
  --idi-probe --out artifacts/bri-boot.json
python -m unittest discover -s tests -p test_eicon_bri_machine.py -v
python -m unittest discover -s tests -p test_eicon_bri_card.py -v
python -m unittest discover -s tests -p test_eicon_ipacx.py -v
```

The JSON includes all four firmware signatures, fault diagnostics, IPAC
state, timer/device interrupt counts and actual ASSIGN return codes. A
budget ending before ready/ASSIGN completion is not a successful CLI run.

## Model boundaries

- `tools/eicon_bri_card.py`: BAR2 16 MiB SDRAM; BAR3 reset, CPU, IRQ,
  TE/NT configuration and eight separate ADSP IDMA ports; BAR0 PLX interrupt
  byte. DSP reset gates IDMA and execution, without erasing SDRAM.
- Legacy `.2q0`–`.2q3` staging remains available with four 4 MiB regions
  ending in 64 KiB shared windows. It is a separate layout, not the one
  used by stock build 122-11. Legacy staging alone is not a boot pass.
- Reentrant shared windows are at `0x1000`, `0x12000`, `0x23000`,
  `0x34000`. Loader staging applies the driver's task/card header patch,
  aligned DSP code pointer, actual combifile-selected download table and
  PCINIT DSP-image-length TLV.
- `tools/eicon_bri_pci.py`: measured PCI identity/subsystem fields and a
  configuration-header API with BAR sizing/assignment, command and interrupt
  line controls, the captured feature EEPROM via VPD, and its capability
  chain. The MIPS PCI configuration apertures expose that same header.
- `tools/eicon_bri_machine.py`: Unicorn MIPS shares the same SDRAM as the
  host; board registers are MMIO callbacks, not PRI firmware function
  hooks. CP0 Count/Compare and pending Cause bits drive the firmware's own
  exception vector. CP0 PRId/config/reset status match hardware measurements
  (`0x2620`, `0x26880`, `0x1040ec04`), so firmware recognizes the board
  rather than entering its unknown-processor fallback. Reset restores CPU context and invalidates translated
  code while preserving SDRAM. Two measured memory-controller registers
  retain configuration writes; SDRAM timing is not simulated.
- `tools/eicon_bri_host.py`: bounded driver-style IDI request, return and
  indication queues, including the separate board/per-controller interrupt
  acknowledgement. Queue descriptors come from firmware.
- `tools/eicon_ipacx.py`: four independent IPAC register banks, read/write
  aliases, reset identification/status, masks, C/I event buffering, and a
  D-channel FIFO/frame boundary. TX supports multi-FIFO assembly; RX
  supports full/end interrupts, status byte, acknowledgement and overflow.
  Frame-level peer connection is a simulation API, not a physical S/T link.
  B-channel transparent-mode-0 HDLC now has two independent frame FIFOs,
  receive pools/status, transmit assembly/reset and internal test loops.
  Receive-CRC storage, FCS checking, CRC-disabled transmit and measured
  transparent-0 status are modeled for aligned frames. Four address-filtered
  modes support unmasked comparators. MHA/MLA address masks, extended
  transparent mode and serial bit-level framing remain unsupported.
- Firmware IOM configuration and reset release gate explicit SPORT1 word
  events on all eight DSPs. Inputs default to measured all-ones idle; raw
  words and outputs are exposed separately per DSP. These events preserve
  the kernel ISR path; B-channel slot routing is not inferred from them.

The processor clock is an instruction-based functional model. IOM event
service uses 6,250 Count ticks per frame as a functional schedule, with
four 16-bit SPORT1 word events per frame. The
probe results below do not establish absolute clock rates, pin phases,
cycle-accurate interrupt latency or real-time modem performance.

## Physical card evidence, 26 October 2026

Used spare SN12196, PCI `0000:02:02.0`, controllers 5–8, with the owner's
explicit approval to stop/probe/restore it. Card identity and idle channel
state were checked. Every probe used the existing identity/stopped-MIPS
guards and an EXIT handler restoring stock firmware. SN1331 remained
active. The spare returned to `active` after every session.

Measurements are preserved in `tests/data/bri_card12196.json`; raw inputs,
bundles and results are under `artifacts/bri-card-20261026/`.

1. The same MIPS/IDMA ALU bundle ran on hardware and the new board model:
   1,166 cases on each of eight DSPs, with exact result-array and completion
   matches on every port. This validates the tested reset/download/execute/
   readback path, not the entire instruction set or media path.
2. A stopped-card header snapshot confirmed the four reentrant shared-RAM
   windows. Runtime queue counters were captured without payload buffers.
3. A MIPS probe read memory-controller registers `0xffffe200=0xaaaaaaaa`
   and `0xffffe204=0xffffffff`.
4. All four IPACs returned identical reset values for registers
   `20,21,2e,33,60,62,63,64`: `00,40,1a,00,00,00,00,01`.
5. CP0 PRId/config/reset Status measured `0x2620/0x26880/0x1040ec04`.
   PCI subsystem identity is `8001:0014`. The same CPU probe matches the
   emulator exactly, and firmware now identifies `Diva Server 4BRI-8M 2.0`.
6. The C/I probe read CIR0 twice after reset, TIM, RES and DI. Every IPAC
   returned `1a,12,fa,f2,7a,72,1a,12`. A direct status-overwrite model was
   insufficient: CIR0 buffers transitions. After implementing that queue,
   the same MIPS probe matches all 32 hardware readings exactly.

`tools/eicon_bri_probe.py` reproducibly builds processor, memory-controller, reset, B-channel reset/mask and
C/I probes. Run its bundles only with `eicon_dsp_bench_card.py` on the
stopped spare and an EXIT restoration handler, as above. Generic bench
`probe/polls/done` job fields are not meaningful for these register-only
programs; their `finished` marker and `dm` arrays carry the measurements.

The card's installed protocol/DSP firmware files match the existing local
files byte-for-byte; their hashes are in the fixture. No private service
configuration, credential files or journal contents were copied.

Register definitions and host sequences come from bundled
`docs/divas4linux-master/kernel/{mi_pc.h,dsrv4bri.h,s_4bri.c,os_4bri.c,di.c,pr_pc.h}`.
IPAC FIFO/interrupt handling also follows the upstream
[Linux IPAC-X driver](https://github.com/torvalds/linux/blob/v2.6.32/drivers/isdn/hisax/ipacx.c).
The manufacturer's [IPAC-X datasheet](https://www.application-datasheet.com/pdf/infineon-technologies/psb-21150-f-v1-4.pdf)
describes the C/I FIFO and Timing/Power Up transition. Hardware measurements
qualify the bounded sequences above, rather than every register side effect.

## Remaining integration

- Complete bearer slot routing and firmware
  mailbox ownership for the remaining modem/channel paths. The existing `bri-v42` SIP adapter still uses its own
  single-task backend; its successful live modem transfers do not qualify
  this whole-board media path.
- Complete transceiver states, ISDN line timing, D-channel framing/CRC and
  B-channel peripheral behaviour, then qualify more Q.921/Q.931 states and
  more bearer profiles, mixed call types, reconnects and long streams through
  the board firmware. Bounded native HDLC/X.75 payload and call teardown
  checks now pass.
- The QEMU PCI frontend and unmodified Linux driver attachment now pass;
  guest-driven legacy firmware download/start also passes. Qualify other guest
  bearer/modem profiles and long streams, and implement the remaining
  bridge/EEPROM/FPGA and power-state registers. See `qemu/README.md`.
- Measure clock ratios, concurrent channel scheduling and restart/soak
  behaviour before making real-time or full-card reliability claims.

## Current call diagnostic

`tools/eicon_bri_pair_probe.py` configures point-to-point TE/NT pairs using
the fixed PCINIT fields, connects the simulated IPAC frame endpoints, boots
stock firmware, and sends actual IDI ASSIGN/LISTEN/CALL_REQ transactions.
The default probe requires the peer firmware to publish its incoming-call
indication (`INDICATE_IND`, code 10). `--pair 1` selects controllers 2/3 instead of 0/1. `--answer` echoes its channel in CALL_RES
and requires the caller's CALL_CON plus the answering side's CALL_IND. Both
sides receive network-layer ASSIGN linked to their signalling entity; omitting
that assignment leaves the call waiting for DSP resource setup.

The bounded native X.75 experiment now exchanges LAPD SABME/UA, Q.931 SETUP,
CONNECT and CONNECT ACKNOWLEDGE, and publishes both signalling connection
indications. The actual DSP task 104 starts through the firmware. This is a
signalling pass. With `--hangup`, both sides also publish real HANGUP
indications following the Q.931 disconnect/release exchange. Native bearer
checks are described below. Both controller pairs also pass concurrently.
The full-card probe now exercises two calls per pair, using B1 and B2 on
every controller. Results are in
`artifacts/bri-card-20261026/virtual-pair-cleared.json` and
`virtual-pair23-cleared.json`.
Fixed TEI 0 uses PCINIT encoding 1, and ETSI protocol selection uses 0x81.
The D FIFO follows the 32-byte pool default and XME-only final-block command.

```sh
python tools/eicon_bri_pair_probe.py --hangup --wait-instructions 14000000 \
  --out artifacts/bri-virtual-pair.json
```

## Validation outcome

Board, PCI and IPAC checks pass (18 tests), along with nine MIPS/firmware
tests. Two host fragmentation/flow-control checks and five existing BRI
caller-replay checks also pass (34 focused tests
in total). The firmware tests cover real
boot, signalling/network ASSIGN, REMOVE, reallocation, measured CPU/C-I
outputs, two concurrent pair calls, and four concurrent calls using all
eight B-channel endpoints. Every endpoint delivers a distinct 2,048-byte
binary frame before network/signalling teardown. No modem-media,
kernel PCI-driver attachment or real-time reliability claim follows from
these bounded checks.

## Network and bearer diagnostic

The pair probe now supports `--network`. It follows `tty_module/isdn.c`:
N_CONNECT after signalling confirmation, and N_CONNECT_ACK in response to
an incoming network indication. Initiator completion is its N_CONNECT_ACK
indication; responder completion is the successful return from its ACK
request. Both network entities connect with native profile 9.

`--pair both` starts the two TE/NT calls concurrently, answers both,
waits for every network entity, then posts distinct controller-prefixed
payloads to all four endpoints. Completion requires exact peer data on
every endpoint and, with `--hangup`, all four signalling disconnects.

`--raw-sport` explicitly connects corresponding DSP endpoints on the chosen
pair or both pairs. `--sport-delay-words` sets the raw peer delay (default 2). Every DSP
samples its input before outputs propagate, so iteration order cannot
change the delay. This is raw-word simulation; physical FPGA slot mapping
and oscillator timing remain unqualified.

The stock BRI kernel services a four-word serial cycle. It publishes the
active bearer word in one slot and captures the incoming bearer in a
slot two word events later. A one-word peer delay delivers valid transmit
data in a discarded receive slot: accepted N_DATA then produces no host
receive indication. Two words deliver the expected frame through the real
CRC/bit-stuffing and receive handlers.

BriMachine now clocks all four words per IOM frame. Previously it advanced
one word per frame, allowing ports enabled at different times to start on
different slot phases. Full frame scheduling keeps all eight DSP phases
aligned without writing their firmware state. A separate word API remains
available for raw timing experiments. Snapshot `clock.iom_frames` counts
IOM frames; the legacy `board.sport_frames` counters count SPORT word events.

```sh
python tools/eicon_bri_pair_probe.py --pair both --profile 9 --raw-sport \
  --payload hello-bri --hangup --wait-instructions 26000000 \
  --out artifacts/bri-payload.json
python tools/eicon_bri_pair_probe.py --profile 1 --raw-sport \
  --payload hello-x75 --hangup --wait-instructions 26000000 \
  --out artifacts/bri-x75.json
```

Success requires distinct exact peer payloads in both directions, then both
HANGUP indications when requested. Transmit return codes and network
readiness alone cannot pass the payload probe. The regression sends a
2,048-byte frame on each of four endpoints (a distinct controller prefix
plus a repeating 256-byte alphabet), checks exact delivery without cross-talk,
then disconnects all four network entities and clears both calls. This exercises zeroes, HDLC flag bytes, bit stuffing and CRC.
Latest complete-frame results are
`artifacts/bri-card-20261026/pair23-iom-payload-cleared.json` and
`x75-iom-payload-cleared.json`; both require exact bidirectional receive
data and both disconnect indications. Older failing one-word runs remain
as evidence of the phase defect; they no longer describe the default probe.

Add `--dump-dsps artifacts/bri-dsps` to a pair probe to save actual executing
PM/DM on every DSP. Each `dsp-N/pm.bin` contains packed little-endian
24-bit instructions, as required by `tools/adsp2181emu/dasm/dasm`;
`dm.bin` contains 16-bit words. Reading through the core pointers does not
change the IDMA address or boot state. `first_foreground` and `first_isr`
in snapshots distinguish the active first channel from the idle second.

## B-channel peripheral measurements and implementation

A bounded probe on spare SN12196 now asserts peripheral reset, releases
all four IPACs with DSPs held, reads both B engines, then unmasks each
ISTAB and reads twice. All four ports produced identical 22-value results
(88 hardware readings total), preserved in `ipac_b_reset` in the fixture.
MODEB=0xc0 and STARB=0x40 match the datasheet; RSTAB=0x0f differs from
its advertised 0x0e reset value. The model follows the physical card.
Masked ISTAB reads return zero without clearing reset XPR. After MASKB=0,
its first read returns 0x10 and its second returns zero. The exact MIPS
probe now runs against the model in a hardware-comparison test.

The B engines support transparent-mode-0 frame FIFOs at 0x7a/0x8a,
64/32-byte transmit pools, 64/32/16/8-byte receive pools, XTF/XME,
RMC/RRES/XRES, status/count aliases, masks, overflow detection and the
internal test loop. Unit tests transfer a multi-pool frame, exercise the
second channel independently and check masked/overflow/reset behavior.
Frame peers abstract HDLC serialization and CRC; they do not establish
bit-clock timing, address comparison or the other operating modes.

The firmware bearer experiment only writes MASKB=0xff on both engines;
it never configures or transfers a B FIFO. That trace rules out these
missing peripheral FIFOs as the cause of the DSP bearer receive failure,
which was subsequently traced to the serial-slot phase. Transparent
profile 8 failed under the earlier one-word setup and is not qualified. Both cards and all four existing services were verified active
after the final probe and stock-firmware restoration.

## Host frame segmentation

`BriDataSender` queues frames up to the configured network frame limit,
fragments them into 270-byte request buffers, sends intermediate fragments
as N_MDATA and terminates each frame with N_DATA. It posts one fragment
at a time and matches real return codes by entity, channel and request
reference. Request-buffer exhaustion keeps the fragment queued. OK_FC
pauses further posting until the entity's READY_INT; unrelated entities,
channels and references cannot acknowledge its outstanding fragment.

The concurrent regression transfers four distinct 2,048-byte frames, each
using eight host fragments, and receives their complete bytes before
teardown. The smaller reproducible result
`artifacts/bri-card-20261026/fragmented-1028-payload.json` records four
fragments and 1,028 accepted/received bytes on each endpoint of one call.
Flow-controlled pause/resume and rejection handling are additionally
checked in host unit tests; these bounded live-firmware transfers do not
establish long-running queue saturation or full-card throughput.

## All eight B channels

`tools/eicon_bri_full_probe.py` allocates two signalling entities and two
network entities per controller, and connects four calls across the TE/NT
pairs. It matches incoming calls by their actual called-number IE rather
than assuming which listening entity receives which call. The host supports broadcast offers and re-arms an unused listener when
its offer is cancelled. The current CAPI listener advertises call-waiting
support and can keep the unused listener armed. The second call starts
only when a free listener is ready, while the first call stays up.

```sh
python tools/eicon_bri_full_probe.py --wait-instructions 50000000 \
  --out artifacts/bri-all-bearers.json
```

Completion requires all eight endpoint connections to overlap, B1 and B2
reported on every controller, exact distinct peer data in both directions
on all four calls, successful transmission acknowledgments, and every
signalling disconnect. The regression sends 2,048-byte frames with every
byte value through eight PR_RAM fragments per endpoint. Matching actual
entity IDs, logical channels and request references prevents a response
for the other call from advancing a sender. Maximum-frame evidence is in
`artifacts/bri-card-20261026/all-eight-binary-2048.json`.

The received Sending Complete IE (`0xa1`) is a single octet. The IDI parser
now handles it without consuming the following escape IE as a length.
Its dedicated case passes with the 66 IDI tests; 23 related native bearer
checks also pass. Raw SPORT peer wiring and instruction-based clocking
remain functional simulation boundaries. These checks do not establish
physical serial timing, modem transfers, reconnect/soak reliability or a
kernel-visible PCI device.

## Whole-board modem call path

The pair probe now uses the shipping `add_p_listen` and `add_p_indicate`
payloads for incoming entities: CAI 0xc0, wildcard OAD, CA20 key, Capi20 UID,
listener LLI/capabilities, and codeset-6 all-services SIN. Previously it
assigned the outgoing B1 descriptor to listeners. A 3.1-kHz modem SETUP
was rejected as incompatible destination before any answering DSP started;
changing A-law/μ-law alone did not fix it. The driver listener sequence
admits the call and keeps the native eight-bearer regression passing.
The outgoing modem descriptor remains in CALL_REQ and CALL_RES.

Profile 0 now assigns linked network entities on both sides, as the native
profiles already did. `--modulation` selects the real CAI with automode off;
`--law` configures the port's PCINIT law. No DSP initialization routines,
training flags or IDI responses are supplied by the probe. The modem
regression requires actual call indications, loaded DSP handlers and the
firmware's `Supervisor training start` log.

```sh
python tools/eicon_bri_pair_probe.py --profile 0 --modulation v21 \
  --law pcma --answer --raw-sport --wait-instructions 20000000 \
  --out artifacts/bri-modem-startup.json
python tools/eicon_bri_pair_probe.py --profile 0 --modulation v21 \
  --law pcma --network --raw-sport --payload whole-board-v21 --hangup \
  --wait-instructions 420000000 --out artifacts/bri-modem-payload.json \
  --progress artifacts/bri-modem-progress.jsonl --dump-dsps artifacts/bri-modem-dsps
```

The startup check passes. In the longer experiment the actual firmware
loads the modem kernel, DIAL/FSK, V.8 and FSK OWN overlays, reports
300 TX/300 RX and N_CONNECT_ACK on both network entities, then accepts
19-byte transmit requests on each side. With the original zero-byte CAI receive limit, neither side delivered
receive data by the 420-million-instruction budget. That failing payload
probe (`waiting-for-bearer-payload`) is recorded in
`artifacts/bri-card-20261026/modem-v21-payload.json`. A link-ready report is
not treated as a modem data pass.

At 280 million instructions the DSP input ring still held the data. At
420 million its producer and consumer pointers match, so the transmit
input has eventually drained. The subsequent investigation followed that queue through the
asynchronous framer/modulation/receive path. JSON-line progress and
side-effect-free DSP dumps preserve the real state for that investigation.
Unrequested HANGUP indications now end the probe as `call-rejected`
instead of spending the remaining budget in a generic connect wait.

### Passive V.21 serial-boundary capture

`eicon_bri_wire_capture.py` observes each raw SPORT event after the host
queues N_DATA. It records the eight inputs before the event and the eight
outputs afterward, including the core's TX-written flag. Optional `--dm`
addresses are sampled through direct memory pointers at the end of each
four-word IOM frame; neither observer reads IDMA registers or modifies DSP
state. A native profile-9 capture still completes an exact bidirectional
19-byte transfer and disconnect (`native-capture-check.json`).

```sh
python tools/eicon_bri_wire_capture.py --out artifacts/v21-wire \
  --modulation v21 --law pcma --payload whole-board-v21
python tools/eicon_bri_v21_analyze.py artifacts/v21-wire
```

The independent analyzer uses fixed 300-baud 8N1 timing and the two V.21
frequency pairs. It does not search for expected bytes or tune the baud
rate to the payload. Its tests cover all 256 byte values on both channels,
idle input, incomplete frames, G.711 expansion and transport corruption.
Wire decoding and the firmware's N_DATA result are reported separately.

The `v21-uart-wire` capture contains 96,000 word events. Both directions
propagate exactly across the two-word peer delay (95,998 comparisons per
direction). Both the transmitted waveform and its peer's input decode to
the distinct complete 19-byte payloads. Firmware receive queues remain
empty. Execution traces at DSP addresses 0x0586/0x0589 and 0x0703 further
show the caller's interrupt and foreground registers receiving the peer's
1650-Hz signal while its own output is 980 Hz. DM 0x277b contains the local
transmit signal and must not be treated as evidence of incorrect receive
routing. The defect was downstream of the actual serial input; correct waveforms
alone did not establish modem data completion.

### V.21 host payload and receive-limit correction

The next execution trace (`v21-rx-bits-exec`) follows the receive mailbox at
PM 0x0790 and character assembly at PM 0x032c/0x0356. Its 512 observed bits
decode to the complete peer payload with zero framing errors. The stock
UART also assembles the expected characters. The MIPS log then reports
`Receive overflow 0080 0 2 0 2`: its configured receive limit is zero.

The probe had inherited the standalone shim's zero `build_cai(max_frame)`
default. Shipping CAPI `kernel/message.c:add_b1` writes the application's
`MaxDataLength` into CAI bytes 5..6 (including the descriptor length byte).
The whole-board probe now supplies 2,048 in modem ASSIGN, CALL_REQ and
CALL_RES, matching its network-layer DLC limit. This corrects the host
transaction; no DSP or MIPS firmware routine, receive data, interrupt or
return code is synthesized. The standalone shim's default is unchanged.

`artifacts/bri-card-20261026/v21-maxframe-2048.json` now completes at
348 million wait instructions: both network entities connect, each host
receives its exact distinct 19-byte peer payload, both transmitters finish,
and both signalling entities disconnect. There are no receive-overflow
logs. This qualifies the selected A-law, V.21, two-word peer-delay path;
it does not qualify other modulation profiles or physical serial timing.
The machine regression also exercises binary NUL and 0xff data end to end.

### Additional modem and full-card qualification

The full-card probe now accepts modem profile 0, `--modulation`, `--law`
and JSON-line `--progress`. It uses the same complete modem CAI and
2,048-byte receive limit in each endpoint's real host transactions. The
existing native eight-bearer binary regression still passes.

```sh
python tools/eicon_bri_full_probe.py --profile 0 --modulation v21 \
  --law pcma --payload all-eight-modems --wait-instructions 600000000 \
  --out artifacts/all-eight-v21.json --progress artifacts/all-eight-v21.jsonl
```

`all-eight-v21-pcma.json` completes four concurrent modem calls. B1 and B2
are reported on every controller, peak connected endpoints is eight, every
endpoint receives its exact distinct 20-byte peer payload, all eight
transmitters finish and all calls clear. This exercises both DSP allocations
per controller, including the second channel while the first is still up.
The integration regression adds NUL, 0xff, 0x7e, 0x7d, XON and XOFF bytes
and requires exact delivery at all eight endpoints.

`v21-pcmu-both-pairs.json` also completes both port pairs concurrently with
mu-law: four distinct 24-byte payloads and four disconnect indications.
`v23-pcma-payload.json` completes a stock-firmware V.23 call with exact
19-byte data in each direction and both disconnect indications. These
qualify the stated functional schedules and profiles, not all modem modes
or physical timing.

### Revised physical FL0 stimulus

The old exploratory `fl0-level` program compared AX0, rather than the
command in AR, before selecting a flag level. Its Cause observations must
not be used to establish FL0 wiring. `eicon_bri_fl0_probe.py` replaces it
with a bounded 19-phase all-eight-DSP matrix: all high, each individual
low, all low, each individual high, and all high again. DM 0x101 echoes
execution of each command. A separate emulator check counts executed FL0
falling transitions, so an echoed command alone cannot validate stimulus.

The new program ran on spare SN12196 with stock interfaces retained;
all 152 command acknowledgments matched. CP0 Cause was initially 0x9000,
changed to 0x9400 and remained latched at 0x9400 after all flags returned
high. All sampled IPAC AUX values remained zero. This establishes reliable
stimulus, not a level-to-IRQ mapping; IPAC interrupt status/acknowledgment
must be distinguished before changing board interrupt wiring.
`tests/data/bri_fl0_card12196.json` preserves all phase values and the
program hash. Raw captures are `fl0-matrix.{card,emu}.json` in the artifact
directory. The spare's stock firmware was restored, both cards reported
active, and all four existing services were verified active afterward.

### C/I interrupt acknowledgment and independent Stop/Go status

The follow-up `eicon_bri_fl0_probe.py --status-ack` captures global ISTA,
AUX, D-channel status, CIR0 and both B-channel interrupt statuses before
sampling CP0 Cause again. The 19 phases again verify all 152 DSP commands.
On SN12196 the initial Cause 0x9400 coincides with IPAC 1 ISTA=0x10 and
CIR0=0x4a; the next change is CIR0=0xca. Reading the interrupt sources and
CIR0 clears Cause bit 0x400, returning it to 0x9000. Remaining phases,
including all flags low, stay at 0x9000. D-channel, B-channel and AUX status
are zero. Thus the previously latched 0x400 change was a C/I event, not
an established DSP-flag interrupt mapping.

The active C/I value 0xca (then 0xc2 on later reads) also disproves deriving
CIR0's Stop/Go bit solely from activation. The [IPAC-X datasheet, section
4.1.18](https://www.application-datasheet.com/pdf/infineon-technologies/psb-21150-f-v1-4.pdf)
defines S/G and Bus Access Status independently of the C/I code. C/I
updates now preserve those two status bits; they no longer clear S/G for
codes 0xc..0xe. This is not a complete D-channel arbitration model.

`tests/data/bri_fl0_status_ack_card12196.json` preserves the raw phase
values and program hash. Its regression compares exact C/I values and
IPAC 1's CPU interrupt before and after acknowledgment, while masking the
unidentified background IP4/IP7 sources. The probe finished in 1.5 seconds;
stock firmware was restored and both cards plus all four existing services
were verified active. This capture alone qualified only the first port; the all-port follow-up
below independently checks the remaining assignments.

The subsequent `eicon_bri_probe.py --kind ipac-ci-irq` isolates each IPAC
with only its C/I interrupt unmasked. On all four ports both RES and TIM
sequences assert the expected CPU bit, and reading CIR0 clears it:

| IPAC | Cause before acknowledgment | Cause after | Isolated IRQ bit |
|---|---|---|---|
| 1 | 0x9400 | 0x9000 | 0x0400 |
| 2 | 0x9800 | 0x9000 | 0x0800 |
| 3 | 0xb000 | 0x9000 | 0x2000 |
| 4 | 0xd000 | 0x9000 | 0x4000 |

Thus all four mask-table assignments now have physical evidence. The
fixture `bri_ipac_irq_card12196.json` preserves eight measured sequences
and the program hash. The regression replays their C/I input codes and
compares CPU routing, global ISTA and both CIR0 reads exactly; it does not
claim complete transceiver-state reconstruction. In retained stock setup,
RES produced 0x0a/0x02 rather than the cold-reset probe's 0x1a/0x12. That
mode/state distinction remains open. The 0.75-second probe restored stock
firmware, and both cards and all four services were verified active.

### Transparent modem protocol detection

The initial V.22bis mu-law run (`v22bis-pcmu-payload.json`) negotiated
2400 TX/2400 RX, accepted both 22-byte transmissions, and received all
22 bytes in one direction but only the exact final ten in the other.
The leading twelve were missing through 800 million wait instructions;
the probe correctly remained incomplete. Firmware logs showed
`V.42 detection: TRUE` despite the intended transparent data path.

The old network assignment disabled V.42, MNP and SDLC negotiation but
omitted the driver's separate detection-disable flag, compression-disable
flag and final protocol-options byte. `transparent_modem_nl_assign_payload`
now emits the shipping tty driver's complete `dlc_mdm_no_ec` descriptor:
`00080301070700005b00` for a 2,048-byte maximum. Both whole-board probes
use it for transparent modem calls; the standalone shim's legacy defaults
remain unchanged. The pure byte-builder regression compares both incoming
and outgoing descriptors with that driver template. End-to-end V.22bis
binary coverage requires the leading bytes to arrive, exact complete data
in both directions, 2400 TX/2400 RX in firmware logs and full teardown.

Disabling detection alone did **not** repair the missing prefix:
`v22bis-pcmu-no-detect.json` still receives only ten of 22 bytes in one
direction, with several corrupted bytes. The unchanged A-law control
reproduces the original missing-prefix result.
The earlier V.42-detection hypothesis was insufficient.

The actual host sequence exposes a separate readiness error. In the
original V.22bis trace both N_CONNECT_ACK indications have arrived by
310 million instructions, so the probe starts sending. The second modem's
CTS_ON and DCD_ON indications do not arrive until 316 and 318 million.
The shipping CAPI path in `kernel/message.c` requires both real DCD_ON
and CTS_ON indications before B3 becomes active; `tty_module/isdn.c`
also delays modem Up until its DCD indication. N_CONNECT_ACK alone is not
a safe data-ready signal.

`BriModemSignals` now tracks these real NL N_UDATA indications and their
reported TX/RX rates. Both whole-board probes wait for DCD and CTS at
every participant before queueing their payloads, and stop posting new
fragments while either signal is off. The pair probe also matches an
N_CONNECT_ACK request's return by reference rather than accepting an
unrelated RC_OK. Progress and final reports expose modem signal state;
`waiting-for-modem-signals` distinguishes that wait from network setup.
No flags, carrier events, receive bytes or firmware routines are fabricated.

The corrected gate passes `test_stock_v22bis_modem_waits_for_carrier_and_transmits_binary_data`.
The saved `v22bis-pcmu-carrier-gated.json` and its progress trace record
transmission starting at 318 million instructions, after both endpoints
report real DCD and CTS. By 332 million both hosts have received the exact
distinct 24-byte binary payloads (including NUL and 0xff), both senders are
idle and both calls have cleared. The actual N_UDATA rate fields report
2400 TX/2400 RX on each endpoint, and controller logs confirm detection is
disabled. The old ungated path started at 310 million and lost/corrupted
the prefix. Readiness gating, not a guessed delay or fabricated data,
repairs that failure.

The all-eight-endpoint V.21 binary regression also passes with the complete
transparent descriptor and per-endpoint carrier/CTS gating. V.22's earlier
bounded A-law experiment (`v22-pcma-payload.json`) completes exact 19-byte
bidirectional data and teardown at reported 1200 TX/1200 RX. V.22bis mu-law
is now qualified for this bounded binary call; higher modem profiles,
physical oscillator timing, long-stream/reconnect behavior and PCI driver
attachment remain open.

## PCI frontend and real guest driver attachment

The bounded V.32 A-law probe (`v32-pcma-payload.json`) completes exact
19-byte payloads in both directions, real 9600 TX/9600 RX indications and
both disconnects. This uses the same carrier/CTS gate, without modem
firmware stubs.

A read-only PCI/VPD probe on stopped spare SN12196 captured the complete
256-byte feature EEPROM and configuration header. The probe writes only
VPD **read** selectors at PCI 0x4e, polls completion and reads data at 0x50;
it never writes the EEPROM data register or sets the EEPROM-write bit.
The capture preserves serial 12196 and the PM/Hot Swap/VPD capability chain
at 0x40/0x48/0x4c. The spare's stock firmware was restored and both cards and
all four existing services were verified active. The fixture is
`tests/data/bri_pci_card12196.json`.

This closes a concrete driver-probe gap: `diva_get_serial_number` refuses
the non-PCIe card when its feature-byte read fails. `BriPci` now implements
the real read path, with immutable captured feature bytes and an optional
serial override. Driver-style reads reproduce all 256 bytes exactly.
EEPROM writes, out-of-range reads and unsupported accesses fail explicitly;
read-completion timing and power-state transitions remain unqualified.

`qemu/diva-bri.c` is a real PCI device frontend. It owns PCI mapping, virtual
timer callbacks and legacy INTA, forwarding supported register operations
to the existing board model through `eicon_bri_rpc.py`. Reset preserves
SDRAM. Guest firmware is scanned for architectural CP0 operations when
its RISC start is observed, so the transport does not depend on a host-side
firmware preload or supply function responses. The backend's stream tests
exercise fragmented requests, VPD, SDRAM, IRQ acknowledgment, fault/reset,
guest-loaded code restart and actual eight-DSP firmware boot.

Three separate artifacts establish the current integration boundary:

- `qemu-pci-smoke.json`: real QEMU enumeration, all BAR sizes, VPD serial,
  64-bit SDRAM access and IRQ11 assertion/deassertion.
- `qemu-stock-boot.json`: explicitly staged firmware started by a QEMU BAR
  write; all four real 0x4447 signatures arrive through virtual-time steps.
- `qemu-linux-driver.json`: an isolated Linux 4.9.0-13 guest with unmodified
  Debian Diva modules and empty initial board RAM binds the card, reads
  serial 12196, registers IRQ11 and creates controller entries 12196,
  12196-2, 12196-3 and 12196-4. The master reports ready and the other three
  slave; the guest exits cleanly with 257 successful RPCs and no faults.

The kernel/module, frontend, QEMU source and firmware hashes are preserved
in `qemu-integration-manifest.json`. The guest driver is not patched; only
the initramfs probe script and the external emulated device are supplied.
The driver-attachment run does **not** stage or start firmware. Its legacy
loader ABI is distinct from the reentrant firmware used in the separate
qtest. The subsequent legacy guest download/start is qualified below;
FPGA programming and guest call/media qualification are recorded in later
sections; remaining bridge registers still need integration work.
Build and reproduction instructions are in `qemu/README.md`.

### Reset entry and legacy firmware (2026-10-27)

Stopped-spare probes on SN 12196 measured the branch-link return address
`0xbfc00110`, establishing native execution through the reset aperture.
A separate writable-alias probe established that its first 4 KiB shares
low SDRAM. The `0xb0000000` aperture matched low SDRAM in the entry probe;
a second bounded scan compared the first 64 bytes of every 4 KiB page
across 16 MiB (65,536 words), reporting zero mismatches and final pointers
`0xa1000000` / `0xb1000000`. The attempted exhaustive word scan exceeded
its eight-second limit; it is retained as negative evidence, not a pass.
Captures are `boot-entry.card.json`, `boot-high-pages.card.json`, and
`boot-high-sweep.card.json` under `artifacts/bri-card-20261026`.
The spare was restored to stock firmware after each probe.

`BriMachine` now starts at the measured reset PC, maps the two SDRAM
apertures to the same backing storage, and honors CP0 Status.BEV for
interrupt vector selection. This resolves the legacy bootstrap's
pseudo-direct jumps through `0xb0000000` without replacing instructions.
`eicon_bri_legacy.py` stages all four shipping `.2q0`–`.2q3` images and
places DSP sections using the driver's first-fit per-controller regions.
A 30-million-instruction run boots all eight DSPs and produces all four
real `0x4447` signatures without a CPU fault (`legacy-boot.json`).

The optional QEMU `ram-file` property and backend `--shared-ram` use a
shared 16 MiB mapping so the archived guest driver's byte-at-a-time
SDRAM writes do not require millions of socket transactions. Reset
preserves this SDRAM. `test_eicon_bri_rpc.py` checks coherence between
both mappings and actual MIPS execution. Guest firmware loading is qualified below, separately from driver
attachment and the explicitly staged qtest boot.

The subsequent guest-driven legacy load passed:
`qemu-linux-firmware-load-verified.json` records the unmodified Linux
4.9 `divas` driver's RESET, 109 SDRAM writes, and successful START from an
initially empty board. With 10.3 million requested MIPS instructions, all four
firmware signatures are `0x4447`, all eight DSPs exist, the master proc
state is `active`, and the remaining entries are `slave` as defined by
that driver. All 475 transport operations completed without a fault and
the guest shut down cleanly. The loader packets stage 59 DSP downloads;
they do not implement the stock userspace FPGA programming sequence.
Use `eicon_bri_guest.py --shared-ram --require-firmware` to require this
stronger check rather than attachment alone. The guest IDI lifecycle is
qualified below; subsequent sections cover guest call/media operation.

A final `boot-high-alias.card.json` probe also confirmed bidirectional
write-through at `0xb0000024`: a low-SDRAM increment appeared through the
high window, a high-window increment appeared in low SDRAM, and both
returned to the original word after restoration. Its first 4 KiB matched
in both apertures. Regression tests replay both reset/high alias probes,
check their physical stimulus hashes and write results, and require the
original program bytes to remain intact afterward.

Validation after these changes: all 14 firmware/machine regressions passed
(622 seconds), including concurrent eight-bearer native and V.21 transfers,
V.22bis carrier-gated binary transfer, entity lifecycle, legacy boot, and
reset/interrupt probes. The additional reset/high-window write test and
entry/page comparison test passed separately. Board tests (11), PCI tests
(3), and framed/shared-memory transport tests (5) also passed. The shared
QEMU reentrant boot, bus discovery and IRQ11 check were rerun successfully
as `qemu-shared-stock-boot-reset-alias.json`. Both physical cards and all
four pre-existing services were active after the final spare restoration.

### Stock guest userspace IDI (2026-10-27)

`qemu-linux-idi-verified.json` extends the successful guest-driven firmware
load through the unmodified `diva_idi` and `divas` modules. Each of the four
controllers returns two-channel features, completes signalling ASSIGN and
REMOVE, and repeats both operations on the same userspace handle. The
probe checks exact 152-byte response length, return-code indication type,
`0xef` / `0xff`, channel zero and zero payload length, ignoring uninitialized
union padding. No IDI response is synthesized by the external board model.

The run records eight successful ASSIGNs and eight REMOVEs, IRQ11 increasing
from 2 after firmware start to 111 after IDI work, entity counts restored
to `[1,1,1,1]`, all four real firmware signatures and eight DSPs. All 892
transport operations succeed; the guest exits cleanly. The archived ABI
sources and module hashes are in `qemu-integration-manifest.json`.
`eicon_bri_guest.py --require-idi` additionally requires every cycle,
resource cleanup and new IRQ activity. Tests deliberately remove a cycle,
leak an entity, stop the CPU, remove firmware evidence or freeze IRQ counts
to ensure a success marker cannot falsely qualify the run. Guest call setup
and bearer transfers remain separate work.

### Guest CAPI diagnostic: preserved failure

The unmodified `divacapi.ko` and `capi.ko` also load in the isolated guest.
All four controllers register as ready. The freestanding client registers
an application and reads controller one's two-channel profile. Its standard
26-byte LISTEN request is rejected with `EIO`, CAPI error `0x1102`; no LISTEN
confirmation or guest CAPI success is claimed. Firmware signatures, all
eight DSPs and the full preceding IDI lifecycle remain valid, with no
backend fault (`qemu-linux-capi-v4.json` and its audit).

There is concrete evidence of a layout defect in this archived 64-bit
CAPI driver: its upstream header's native pointer fields align the info
union at byte 16, while the wire header is 12 bytes. Compiling the unchanged
header verifies offsets 16 for x86-64 and 12 for i386. Disassembly of the
actual guest module likewise passes `msg+0x10` to its parser but computes
payload length as `length-0x0c`. The compile probe, both layout assemblies,
module parser disassembly and source hashes are retained in
`artifacts/bri-card-20261026`. The bad-message result is consistent with
that host-driver mismatch, before the card can process LISTEN. The emulator
has not been changed to compensate for malformed host-driver parsing.

The CAPI probe explicitly fails unless all four profiles and all four exact
LISTEN confirmations succeed. Its setup reuses the kernel-created device
node. The vendor-default 64-bit CAPI module is now available and qualifies
registration/LISTEN below; further guest call/media qualification remains. Reproduction and the strict negative result
are documented in `qemu/README.md`; the known failure is not a completion
or an emulator-fidelity claim.

### Vendor-default CAPI registration and LISTEN (2026-10-27)

`qemu-linux-vendor-capi.json` now passes application registration, all four
two-channel profile queries and four standard LISTEN confirmations through
the vendor-default CAPI module, alongside the stock PCI/IDI/DIDD modules.
The same freestanding client binary and standard 26-byte LISTEN requests
that the archived 64-bit driver rejected now pass. Confirmations are
exactly 14 bytes, match application/message/controller/command and contain
`Info=0`. The emulator does not replace the CAPI or firmware handlers.

The source header already uses byte arrays for 64-bit pointer fields;
x86-64 compilation verifies its wire payload offset is 12. The module was
compiled inside an offline disposable VM with matching GCC/kernel headers.
All 141 vendor driver files are unchanged, with the default optimized-CAPI
interface enabled. A build-only `linux/stdarg.h` wrapper supplies the
compiler header absent from the old SDK. The attempted common-CAPI build
also found an older proc-API mismatch; that failed variant is retained,
and no proc-API or CAPI-protocol patch was applied to obtain this pass.

`vendor-capi-build-v5.json` records the successful module export, with hash
`a565b0566557eb08e8509998f6a58cf43dfb3c41ffea3d6fe258e605b2982fb7`.
The 43 package archives, driver source hashes and compiler-guest setup are
pinned. A fresh run of the environment preparer matched all 16,876 build
input entries, contents, symlinks and executable modes. Reproduction is in
`qemu/README.md`.

The full guest run retains four real `0x4447` signatures and eight DSPs,
passes all preceding IDI lifecycle checks, completes 1,122 successful
transport operations and shuts down cleanly. Regression coverage includes
the actual vendor-CAPI capture and rejection of missing confirmations or
driver errors. LISTEN alone does not establish an end-to-end guest call;
the following sections qualify call/data/disconnect and FPGA programming.
Remaining registers and physical timing still require work.

### Guest CAPI native calls, data and reuse (2026-10-27)

`qemu-linux-capi-call-4-v3.json` now qualifies four concurrent guest-driven
native HDLC calls across both TE/NT port pairs. The vendor CAPI module and
stock PCI/IDI/DIDD drivers process the actual CONNECT/ACTIVE/B3/DATA and
disconnect exchanges. The application uses returned PLCI/NCCI identifiers;
no IDs, confirmations, bearer contents or readiness are manufactured by
the board model. All eight endpoints are active before sending data.

Every endpoint sends 2,048 bytes containing all octet values and a unique
endpoint/round tag. The guest compares every received byte, and the host
verifier independently checks length/CRC32 against the peer's expected
payload. Both full cycles pass, delivering 32 KiB total. This exceeds the
270-byte IDI request buffer and exercises the guest driver's normal
segmentation and firmware reassembly. B3 is explicitly disconnected at
both controlled transparent endpoints before signalling is cleared; the
initial one-call run preserved a client sequencing timeout after correct
data delivery, then the 18-byte eight-endpoint smoke test passed.

The repeated application/call cycle verifies fresh data and resource reuse.
Counts are `[4,4,4,4]` before and after each cycle, including persistent
vendor listeners/non-call link plus the internal driver entity. IRQ11
advances `0 → 2 → 103 → 202 → 254`; firmware signatures stay `0x4447` on all
four controllers, all eight DSPs remain present, and all 2,428 transport
operations succeed with clean guest shutdown. Requested MIPS execution is
82.95 million instructions; this is a functional schedule, not a physical
clock qualification.

The external fixture is explicit (`--wire-pairs`): D-channel peers 0/1 and
2/3, raw SPORT peers 0/2, 1/3, 4/6 and 5/7 with the qualified two-word delay.
The guest-written PCINIT roles are TE/NT/TE/NT. A packet comparison proves
only the two NT role bits change from default loading; protocol/DSP bytes
remain identical. Default card execution still has no peer wires.

The freestanding application source, startup assembly and offline build
init are in `qemu/guest-capi-call*` and `qemu/guest-client-build-init`.
`eicon_bri_guest.py --require-calls 4 --require-call-repeats 2` requires full
messages, eight simultaneous bearers, correct phase ordering, all release
indications, IRQ activity and stable entity counts. Capture-based tests
reject missing endpoints, CRC corruption, stale round data, leaked entities,
frozen IRQs, cumulative rather than concurrent activation and wrong wiring.
Native guest call/data/teardown is now qualified; other guest B protocols,
modem profiles, long streams, remaining registers and
physical timing remain work. Full reproduction is in `qemu/README.md`.

Validation for this guest-call change: 15 guest/build/packet/provenance-gate
regressions pass, and all five framed-transport/shared-memory/stock-boot
regressions pass. The full two-cycle VM capture also passes the stronger
phase-ordering verifier when replayed. Core firmware execution was not
replaced or adjusted to obtain this result.

### FPGA serial programming qualified against spare SN 12196 (2026-10-27)

`eicon_bri_fpga_probe.py` stopped only the authorized spare, checked every
bearer was idle, programmed its installed `ds4bri2.bit`, and restored stock
firmware on exit. Both cards and all four existing services were active
following the probe. Native aligned 16-bit accesses are required; an earlier
byte-copy write attempt did not configure the FPGA and is preserved as a
negative capture.

The successful physical trace is `tests/data/bri_fpga_card12196.json`.
The model matches all 83 sampled GPIO values, including PROGRAM clearing
DONE, clock/data pin readback, and DONE first rising at serial clock 233652
for declared length 233649. Duplicate high writes do not create extra
clocks. Post-program RISC and interrupt status are both zero. BAR0 INTCSR
word/dword aliases preserve the measured status bits.

`eicon_bri_fpga.py` recognizes the actual clocked body of the qualified
shipping Diva design (image SHA256
`a3973120d0869f8ee2f20b10863616e449dd0bb3deffa19b77b681e77fcc533f`).
This is a fixed board personality model, not a generic FPGA fabric or
configuration CRC implementation. Unknown designs remain unconfigured.
The default configured state models an already configured, stopped card;
cold power-on behavior remains unqualified. EEPROM operations are unsupported.

`qemu-linux-fpga-calls.json` qualifies the Linux driver's WRITE_FPGA path,
then real firmware boot, IDI, and two complete four-call/eight-endpoint CAPI
cycles. All sixteen 2048-byte received frames compare exactly (32768 bytes),
resources return to baseline after each cycle, IRQ11 advances
`0 → 3 → 117 → 217 → 270`, and all 937089 RPC operations complete without
faults. `--require-fpga` checks the measured clocks, length, personality,
DONE and guest download result. The replay fixture also rejects missing
markers and incorrect configuration state. Other FPGA designs, remaining
bridge registers, power transitions and wider call/profile coverage remain work.

### PLX soft reset and EEPROM reload (2026-10-27)

`eicon_bri_reset_probe.py` ran the driver's native 16-bit control sequence
on stopped, idle spare SN12196. The stock firmware was restored after both
captures and both cards plus all four existing services were verified active.
The repeat capture is `tests/data/bri_reset_card12196.json`.

Soft reset (`BAR0:0x6e`, bit 14) changes the measured bridge registers:

| Register | Configured | After soft reset | After EEPROM reload |
| --- | --- | --- | --- |
| LAS0RR (`0x00`) | `ff000000` | `fff00000` | `ff000000` |
| LBRD0 (`0x18`) | `8a430143` | `42430043` | `8a430143` |
| MARBR (`0xac`) | `0201000c` | `00200000` | `0201000c` |

BAR2 SDRAM and BAR3 controls return all ones between reset and reload.
Reload (bit 13) restores decode and the original SDRAM marker: the temporary
all-ones reads do not mean RAM was erased. FPGA DONE remains high throughout.
The model now implements this sequence, preserves SDRAM/configuration,
halts CPU execution and prevents local accesses until reload. The probe
only qualifies the shipping reset/reload sequence and sampled apertures;
arbitrary writable bridge mappings, DMA and electrical reset timing remain open.

Local interrupt status at INTCSR bit 15 (byte `0x69`, bit 7) appears only
when local-interrupt enable bit 11 is set, independently of PCI enable bit 8.
The model's byte and dword aliases now follow that measurement. The physical
probe also latched INTCSR bit 7, the local parity-error status described by
the [PLX data book](https://www.nikhef.nl/~peterj/datasheets/9054db-1C.pdf).
Its cause is not isolated, and parity-error injection is not modeled; the
trace regression explicitly excludes that bit rather than claiming a full
INTCSR match.

The first shared-RAM QEMU check exposed an incorrect zero response when a
RAM region was simply disabled. A fallback MMIO region now supplies the
board's all-ones reads and ignores writes while decode is absent.
`qemu-shared-plx-reset-v2.json` qualifies isolation, marker preservation,
reload, INTA and four stock firmware signatures after reset (334 RPCs).
`qemu-mmio-plx-reset.json` independently qualifies the same controls through
ordinary MMIO (41 RPCs). Transport RESET also recovers an interrupted PLX
sequence while preserving SDRAM. Remaining bridge controls, power states,
EEPROM writes, alternate FPGA designs and wider profile coverage remain open.

The final rebuilt frontend passes `qemu-linux-plx-fpga-calls-v2.json`: FPGA
programming, four firmware signatures/eight DSPs, IDI, and two complete
four-call cycles with 32768 verified bytes, clean teardown and stable entity
counts. All 937052 RPCs succeed; IRQ11 advances `0 → 2 → 105 → 203 → 254`.
This guest run checks normal operation after the frontend and IRQ changes;
the separate qtests exercise the actual reset/reload isolation sequence.
43 focused regressions pass, including both architectural CPU reset paths
with changed code to detect stale CPU/JIT state. Source, binary and evidence
hashes are recorded in `plx-reset-integration-manifest.json`.

### Stopped-card D3hot / D0 power cycle (2026-10-27)

`eicon_bri_power_probe.py` measured the advertised PM capability on stopped,
idle spare SN12196. It writes only PMCSR state bits at PCI offset `0x44`;
all accesses during D3hot are to PCI configuration space. A `finally` path
returns to D0 and waits before reading BARs. The caller then restores the
stock firmware. Both physical cards and all four existing services were
verified active after restoration.

`tests/data/bri_power_card12196.json` records PMCSR `0 → 3 → 0`, unchanged
configuration header/BAR addresses in D3hot and after resume, and preserved
FPGA pins/DONE, bridge settings and sampled SDRAM after D0. As in the reset
probe, the local-parity status bit appeared during observation; its cause
remains unqualified and is explicitly excluded from the trace comparison.
No BAR behavior in D3hot, running-firmware power transition, D3cold,
electrical power removal or wake-up event is claimed from this experiment.

`BriPci` now accepts D0/D3hot writes and records actual state transitions;
D1/D2 are not advertised and requests for them leave the state unchanged.
PM fields without support remain read-only. The QEMU frontend previously
created a PM capability with no writable PMCSR bits, silently ignoring
power-state requests. Its state-bit write mask is now enabled, allowing
QEMU's PCI core to perform its normal power-state mapping updates. Normal
INTA is gated outside D0. Transport RESET returns the backend to D0; BAR
accesses in D3hot and transition to D3hot with running firmware are explicit
unqualified errors, not invented card responses.

`qemu-mmio-power-cycle.json` and `qemu-shared-power-cycle.json` pass three
D3hot/D0 cycles using byte, word and dword configuration writes, retained
configuration/SDRAM/FPGA state after resume, and rejection of unsupported
D1/D2 requests. The shared run additionally boots four real firmware
signatures and eight DSPs after resuming (348 RPCs); the ordinary MMIO
run completes 61 RPCs. These are functional tests without physical timing
qualification.

`eicon_bri_guest_power.py` packages a guest-side configuration probe that
compares actual PMCSR reads against D3hot and D0, uses no BAR access in
D3hot and restores D0 on failure. `qemu/guest-init` runs it before firmware
loading when present. `--require-power-cycle` additionally requires the
observed backend transitions `[3,0]`, final D0, ordered guest confirmations
before firmware start and no backend fault.

The full `qemu-linux-power-fpga-calls.json` guest run passes D3hot/D0,
stock FPGA download, four firmware signatures/eight DSPs, IDI and two
complete four-call cycles across eight endpoints. Sixteen 2048-byte received
frames compare exactly (32768 bytes), both entity counts return to baseline,
and all 937101 RPC operations succeed. IRQ11 advances
`0 → 3 → 117 → 220 → 276`. The replay fixture rejects missing/wrong/reversed
power transitions, wrong final state, faults and missing/reordered guest
confirmations. Running-firmware and D3 BAR-access rejection guards were
added afterward and are covered by the transport regressions; the positive
guest run never enters either unqualified path.

### Pending interrupt across stopped-card power resume (2026-10-27)

Three additional spare-card observations resolve a missing power-state edge:

| Capture | Local/PCI IRQ enables | Operation | IRQ_TEST before / after |
| --- | --- | --- | --- |
| `bri_power_pending_card12196.json` | local enabled / PCI disabled | D3hot → D0 | 1 / 0 |
| `bri_power_pending_masked_card12196.json` | both disabled | D3hot → D0 | 1 / 0 |
| `bri_power_pending_control_card12196.json` | both disabled | remain in D0 for 30 ms | 1 / 1 |

The fully masked repeat matters: the shipping ISR clears IRQ_TEST before
checking whether firmware is initialized. A shared IRQ could otherwise
acknowledge an enabled local source during observation. With local enable
also masked, INTCSR's active bit remains clear and the ISR does not recognize
the card's latch. The timing control uses the same register/SDRAM observations
without power-state writes and retains the latch. Both controls support a
power-cycle effect rather than an ISR or observation-time artifact. No BAR
access occurs in D3hot. Stock firmware was restored after each probe; both
cards and all four existing services were verified active afterward.

The earlier model retained the test latch after resume. The new physical
fixture reproduced that mismatch before the fix. A PCI power-transition
callback now clears the board latch on return from D3hot to D0; its exact
internal clearing time during D3 remains unobserved. The backend's IRQ flag
also respects D0, agreeing with the frontend's existing PCI IRQ gate rather
than reporting an asserted PCI line while the frontend suppresses it.
IRQ enables, FPGA configuration, bridge mappings and SDRAM are retained.

`qemu-mmio-power-pending.json` (67 RPCs) and
`qemu-shared-power-pending.json` (354 RPCs) pass pending source → D3hot → D0
with exactly one IRQ11 raise/lower pair and no stale reassertion after resume.
Both retain the prior aperture/reset/power checks; the shared run then boots
four real firmware signatures and eight DSPs. Framed transport tests also
verify that a fresh interrupt can be raised and acknowledged after resume.
The power replay includes all three observations and still explicitly
excludes the separately unqualified local-parity status bit.

### B-FIFO qualification attempt and IOM reset correction (2026-10-27)

`eicon_bri_ipac_fifo_probe.py` performs bounded transparent-mode-0 B-FIFO
internal-loop experiments on stopped spare SN12196, with DSPs held reset
and PCI interrupts masked. Each channel is tested with and without receive
CRC storage; the probe preserves results and restores board controls before
the caller restores stock firmware. Both cards and all four existing
services were verified active after the experiments.

All three captures remain unsuccessful receive experiments:

- `ipac-fifo.card.json`: XME-only, no receive frame on any of eight engines.
- `ipac-fifo-v2.card.json`: TIM clock request plus XTF/XME, no receive frame.
- `ipac-fifo-v3.card.json`: software-power-up/TIM sequence plus XTF/XME,
  still no receive frame. Initial IOM_CR was `0x08` on all four interfaces.

Counts remain zero and receive status remains `0x0f`; none of these captures
qualifies CRC byte order, stored-CRC operation or the physical loop. They
also do not justify treating a frame-level simulation loop as physical
IOM-clock evidence. The next hardware experiment needs the clock and time-slot
setup established by real firmware before attempting B-FIFO traffic.
Stored CRC was still unqualified at that stage; the subsequent successful
clock-setup experiment and implementation are recorded below.

The captures did identify a concrete reset mismatch: the model initialized
IOM_CR to zero. It now returns the measured `0x08` (EN_BCL) both on construction
and peripheral reset. A physical-fixture regression failed on all four
interfaces before this correction and passes afterward. This reset default
does not qualify firmware configuration: `iom_configured` still stays false
until the firmware actually configures the interface.

### B-FIFO stored CRC and raw receive status (2026-10-27)

The next clock/setup experiment succeeded. A 20-million-instruction trace
of shipping firmware established its IOM settings, including IOM_CR `0x58`.
An earlier physical stock-interface capture in
`tests/data/sport_duplex_card12196.json` records board control `0x0f` and the
same IOM setting. The bounded native probe applies that combined setup and
explicitly enables each B controller's BC1 timeslot. It does not establish
which individual setting was necessary; earlier reset/TIM-only attempts
remain preserved as unsuccessful controls.

`ipac-fifo-v4.card.json` receives canonical frames through internal HDLC loops
on all eight B engines. With RCRC clear, `123456789` produces nine bytes plus
status `ac`; with RCRC set, it produces nine bytes, FCS `6e 90`, then status
`ac`. RCRC changes RBCL from 10 to 12. ISTAB reports RME plus XPR (`90`).

`tests/data/bri_ipac_crc_card12196.json` then qualifies 64 binary-payload
cases across all eight engines, both RCRC settings, embedded zero/flag/escape
bytes and lengths up to 32. `bri_ipac_address_card12196.json` qualifies 80
additional address-bit cases on one engine. Their raw status bytes include
C/R from the first byte, fixed high-group flags and the low group comparison
against the second byte. The model now matches every captured count, status
and FIFO byte instead of supplying a constant `a0` status.

`eicon_ipacx.py` now implements RCRC for transparent mode 0, reusing the
existing RFC1662 CRC-16 calculation and storing the complemented FCS low
byte first, followed by the status byte. FIFO counts and receive pools
include those additional bytes. Regressions also split a captured FCS
across two eight-byte receive pools and preserve the status after it.
The physical replay failed on the old unsupported-CRC/constant-status model
and passes after the change. Existing B-FIFO expectations now use the
measured status; D-channel status behavior is unchanged.

This first qualification covered valid-frame boundaries. The subsequent
section adds FCS errors and CRC-disabled transmission. Bit stuffing/serial
clock timing and address-filtered modes remain unqualified or unsupported. The internal
hardware loop does not qualify an external S/T connection. DSP bearer
traffic still follows its separate firmware/SPORT path. The spare was
restored after each probe; both cards and all four services were verified
active following the last capture.

Validation for this change: 28 focused test methods pass, including 144
physical-frame replay cases and two receive-pool layouts (one splits the
FCS itself across pools). Real firmware boot/entity lifecycle, captured C/I
behavior and all four CPU interrupt routes pass. `qemu-shared-b-crc.json`
also retains reset/power/IRQ checks and boots four real signatures with
354 successful RPC operations. `b-crc-integration-manifest.json` records
source/firmware/capture hashes and the remaining qualification boundaries.

### CRC-disabled transmit and receive FCS errors (2026-10-27)

`tests/data/bri_ipac_bad_crc_card12196.json` adds 80 physical internal-loop
cases across all eight B engines. EXMB.XCRC disables automatic transmit CRC;
the caller supplies a previously measured correct FCS, a one-bit corruption,
or payload without adding a FCS. Both RCRC settings are exercised.

The physical receiver still reports RME and supplies data when FCS is bad,
with CRC-good clear in RSTAB. For example, `123456789 6f 90` returns payload
`123456789` plus status `8c` with RCRC clear. With RCRC set, the FIFO also
contains the actual bad bytes `6f 90` before that status. A caller that sends
only `123456789` with XCRC set gets `1234567` plus `8c` when RCRC is clear:
the receiver interprets the final two received bytes as FCS regardless of
whether the transmitter generated them. Correct supplied FCS restores the
normal `ac`/`ae` status. Counts include exactly what the FIFO exposes.

The B receiver now accepts aligned HDLC octets including FCS through
`receive_b_frame_with_fcs`, checks the two received bytes, and retains those
actual bytes under RCRC. The existing `receive_b_frame` API remains a valid
payload injection convenience that explicitly adds a correct FCS. Normal
transmit generates FCS; XCRC transmit forwards caller bytes unchanged.
Internal loops and frame peers use the same FCS-bearing receive path, so
peer delivery cannot silently repair a damaged FCS.

All 224 captured cases (the previous 144 plus these 80) compare exact FIFO
bytes, status and counts. The new 80 cases failed before this change because
XCRC was unsupported. A separate peer regression splits a bad FCS across
an eight-byte pool boundary, preserves the hardware status and bytes, then
sends a good frame to prove CRC state recovers. Disabled receivers ignore
input as before. Frames shorter than their two-byte FCS remain explicit
unqualified errors; bit alignment, abort sequences, serial-clock timing,
address-filtered modes, D-channel FCS errors and external S/T behavior
are not established by this internal-loop experiment.

The authorized spare was restored to stock firmware after the bounded
probe. Both cards and all four existing services were verified active.

29 focused test methods pass, including all 224 physical frame replays and
peer/pool-boundary recovery from bad to good CRC. Real firmware C/I, four
CPU interrupt routes and boot/entity lifecycle pass. The PCI frontend also
passes reset/power/IRQ checks and four real firmware signatures with 354
RPC operations (`qemu-shared-b-crc-errors.json`). Source and evidence hashes
are in `b-crc-errors-integration-manifest.json`.

### Four B-channel address-filtered modes (2026-10-27)

Three bounded native captures on the stopped spare qualify the four
previously rejected address-filtered MODEB settings. DSPs remain held reset,
PCI interrupts remain masked, and the established IOM setup is reused.
Each experiment resets the receive/transmit FIFO between frames and tests
both SRA address storage and RCRC FCS storage settings.

| Mode | Receive comparison | Bytes removed when SRA is clear |
| --- | --- | --- |
| Transparent 1 (`e0`) | high address against RAH1/RAH2 or FC/FE group | first address byte |
| Transparent 2 (`a0`) | low address against RAL1/RAL2 or FF group | second address byte |
| Non-auto 8 (`40`) | one-byte address against RAL1 or RAL2 | first address byte |
| Non-auto 16 (`60`) | paired RAH1/RAL1 or RAH2/RAL2, with measured group exceptions | both address bytes |

`bri_ipac_filter_card12196.json` contains 192 cases with distinct individual
addresses. `bri_ipac_filter_pairs_card12196.json` adds 384 cross-pair cases
and reverses the programmed C/R bit polarity to distinguish raw received
bits from comparator identity. The physical non-auto 16 controller does
not accept the Cartesian product of high/low individual matches: each
high address selects its corresponding low comparator. A group low address
is accepted with either individual high address; group high addresses
accept either individual low address or the group low address. Non-auto 8
has no implicit FF group match, although an explicitly programmed individual
address can match the received byte.

`bri_ipac_filter_all8_card12196.json` adds 768 accepted/rejected cases across
all eight engines (320 received, 448 filtered). The model compares all 1344
captured filter cases exactly: acceptance, FIFO bytes, RBCL/RBCH, status,
RME/XPR and storage settings. The original implementation failed all 192
initial cases because these modes were unsupported.

The B transmitter now frames these modes normally; the receiver filters
before consuming FIFO capacity, preserves the previous pending frame when
an unmatched address arrives, and strips only the compared address fields
when SRA is off. CRC validation always covers the full received frame,
before stripping addresses. Stored CRC bytes remain those of the original
frame. SRA keeps the complete original address fields. The normal
transparent-0 path and the prior 224 valid/bad-FCS cases still pass.

MHA/MLA address-mask controls remain explicit unsupported operations in the
filtered path until qualified. Extended transparent streaming, short address
fields, physical serial timing/abort behavior and external S/T operation
remain open. These captures contain good FCS; composition with malformed
FCS in filtered modes has not yet been physically qualified. Stock firmware
was restored after each probe; both cards and all four existing services
were verified active after the final capture. Earlier filter capture
`completed=false` fields meant not every transmitted frame was received;
filtered drops were intentional. The later probe records this separately
as `all_received=false` and recognizes a completed mixed accept/reject suite.

34 focused test methods pass, including all 1344 filter cases, the prior
224 CRC/status cases and preservation of a pending accepted frame when an
unmatched frame arrives. Real firmware C/I, four CPU interrupt routes and
boot/entity lifecycle pass. `qemu-shared-b-filter.json` retains PCI/reset/
power/IRQ checks and four real signatures with 354 RPC operations. Current
source and capture hashes are in `b-filter-integration-manifest.json`.


### Guest reconnect soak (2026-10-28)

The QEMU guest now passes 64 complete four-call native HDLC cycles: 512
endpoint frames, 1 MiB verified receive data, fresh payloads each cycle,
complete B3/signalling teardown and stable `[4,4,4,4]` entity counts after
every application release. The same run passes power-cycle, shipping FPGA,
firmware boot and IDI checks, with no backend/RPC faults and clean shutdown.
See `qemu/README.md` and `artifacts/bri-card-20261026/qemu-linux-capi-soak64.json`.
This extends bounded reconnect qualification; modem media, continuous stream
soaks and physical timing remain open.
