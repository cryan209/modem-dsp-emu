# Build 109 Analog-card V.8/INFO oracle

## What is now runnable

`tools/dial_tikrnl_drive.py` accepts `--firmware-set analog109`. This is a
coherent direct-ADSP stack built from card type 77 / file set 18 of the
recovered 109-789 combifile, tracked as `docs/firmware/build-109/dspdload.bin`
(`2c106902…`) because nothing else here reproduces it:

- `0x000d` DIVA Server Analog kernel
- `0x0258` TIKRNL81.ANA
- `.ANA` V.8, INFO, DIAL and partial overlays
- APCM rather than DPCM selected in `V8_setup`

Extract it with:

```sh
mkdir -p artifacts/eicon-dsp/build-109-789-analog/{kernel,tikrnl,overlays}
python3 tools/eicon_dsp_extract.py docs/firmware/build-109/dspdload.bin \
  --card-type 77 --match '^DIVA Server Analog Kernel' \
  -o artifacts/eicon-dsp/build-109-789-analog/kernel
python3 tools/eicon_dsp_extract.py docs/firmware/build-109/dspdload.bin \
  --card-type 77 --match '^TIKRNL81\.ANA Task' \
  -o artifacts/eicon-dsp/build-109-789-analog/tikrnl
python3 tools/eicon_dsp_extract.py docs/firmware/build-109/dspdload.bin \
  --card-type 77 --match Overlay \
  -o artifacts/eicon-dsp/build-109-789-analog/overlays
```

The Analog task is not address-compatible with the PRI task. Recovered values
used by the harness are:

| Item | PRI 117 F34 | Analog 109 ANA |
|---|---:|---:|
| task entry | `0x0672` | `0x0679` |
| frame entry | `0x06bb` | `0x06d2` |
| no-host frame entry | `0x06c1` | `0x06d8` |
| sample continuation | `0x06fc` | `0x0713` |
| post-download resume | `0x06d8` | `0x06ef` |
| kernel idle | `0x02a8` | `0x02a6` |
| download request/type | `0x31aa/0x31a9` | `0x31ac/0x31ad` |

Reading the PRI request addresses against ANA looked superficially live but
produced alternating download IDs 0/2/4. Explicit firmware-set layouts prevent
that silent false result.

Changed-state snapshots, including the live WDB, can be captured with:

```sh
python3 tools/dial_tikrnl_drive.py --firmware-set analog109 \
  --role answer --freq 2100 --frames 12000 \
  --state-out artifacts/analog109-v8-info.json
```

`tools/eicon_adsp_sip.py` and `tools/eicon_loopback.py` also accept the firmware
set, so a paired direct-ADSP call is possible:

```sh
python3 tools/eicon_loopback.py --firmware-set analog109 \
  --realtime --seconds 20 --capture-dir artifacts/loopback-analog109
```

The MIPS backend is deliberately rejected with `analog109`: its hard-coded PRI
anchors do not support `te_dmlt.am` build 109-76 yet.

## First paired result

A 12-second paired run booted both coherent Analog stacks. The direct originate
policy put the caller on `V8.ANA`, and the answerer naturally requested
`V8.ANA`. It did **not** produce an INFO oracle:

- caller remained on V.8 with `TrnProgress=0x0000`;
- answerer remained in early V.8 (`0x0004`) for about six seconds;
- at sample 49,534 (6.192 s) the answerer fell back directly to V.32, loading
  `V22V32.ANA LEC` and `V32.ANA Partial`, without requesting INFO;
- neither endpoint entered overlay `0x0260`.

This is useful negative evidence. Merely swapping to the Analog kernel/task and
selecting APCM does not choose the missing `0x07xx` INFO chain. The direct
harness supplies the same synthetic ADDSP database to both families, so it has
not exercised the configuration path we wanted to compare.

Directly layering INFO with `--force-info-at` is retained as a narrow diagnostic,
but it is not an oracle: without the preceding firmware-owned handoff it keeps
stale V.8 state and can overflow the ADSP loop/counter stacks. Do not infer an
INFO defect from that counterfactual.

## Analog call and audio handling is part of the oracle

The recovered MIPS image confirms that ANA is not a PRI modem path with a
renamed overlay. `te_dmlt.am` is the POTS protocol itself:

- the host loader selects protocol id 34 (`PROTTYPE_POTS`), patches image byte
  `0x68` to one initial task and byte `0x69` to card type 77/78, and places the
  card type again in shared configuration byte `0x1a`;
- entry `0x80107484` reads the image-patched card type from cached address
  `0x80000069` before constructing any controller or DSP resource;
- card types 77 and 78 select four and eight analog channels respectively from
  the firmware's card-property table;
- the image contains a dedicated `pots.c` state machine and reports physical
  `rxhook`, `txhook`, `Ring`, `OffHook`, CAS hook transitions, glare handling,
  ring count, answer delay, and caller-ID timing;
- the DSP interface defines `DSPDAA` restart/reboot paths, rather than treating
  the bearer as an already-connected PRI timeslot;
- the modem CAI has a distinct `DSP_CAI_MODEM_USE_POTS_INTERFACE` bit (`0x40`),
  mirrored by global `PCINIT_MODEMCONFIG_USE_POTS_INTERFACE`;
- the firmware separately publishes `Timeslots`, `AudioTS# Enable`, and
  `AudioCh# Enable`. The `AudioTS# Enable` callback at MIPS `0x80099d1c`
  clears an eight-byte mask, walks all 64 audio timeslots, specially remaps the
  first eight, and installs the enabled set. It is not a passive G.711 pipe;
- playing gain, recording gain, and line-interface gain boost are separate
  runtime controls. Their management records address controller fields around
  `+0x299..+0x29d`.

This makes the first direct loopback's V.8→V.32 result non-authoritative. It
selected APCM/analogue-network in the DSP WDB, but bypassed hook state, DAA,
POTS CAI, audio-timeslot enablement, gain, and the firmware-owned instant at
which the modem is attached to the line. Those omissions can change both the
audio presented to V.8 and the handoff state published to INFO.

## The Analog line boundary is signed-linear, not a G.711 octet

The first mixed PRI/Analog loopback also fed the same raw RTP G.711 octet to
both tasks. That is correct only for the PRI side. Static kernel evidence makes
the family split explicit:

- the PRI kernel's sample ISR reads `RX0` and writes `TX0`, the companded
  multichannel T1/E1 SPORT;
- the Analog kernel's ISR at PM `0x0047` reads `RX1` at `0x004d/0x004f` and
  writes `TX1` at `0x004c/0x00c5`;
- after boot the Analog kernel has the SPORT1 control pair at
  `DM(0x3ffc..0x3ffd)=0x2000/0x2000`, while PRI leaves that pair zero and
  configures its SPORT0/TDM path instead.

The SIP carrier can still be G.711, but it is transport outside the emulated
card. `line_codec_rx_word()` now decodes that octet to signed-linear PCM before
presenting it to `TIKRNL81.ANA`; PRI continues to receive the octet. Transmit
was already in the right shape: the task publishes a signed-linear sample and
the external SIP codec compands it once.

`tools/analog_line.py` now makes the direct backend's physical boundary
explicit. It holds the codec silent on-hook, attaches it at bearer seizure,
applies independently configurable ADC/DAC gain and 16-bit saturation, and can
feed delayed local transmit leakage back through a two-wire hybrid. Controls
are `EICON_ANALOG_RX_GAIN_DB`, `EICON_ANALOG_TX_GAIN_DB`,
`EICON_ANALOG_HYBRID_ECHO_DB` (unset means no synthetic echo), and
`EICON_ANALOG_HYBRID_DELAY` (8 kHz samples). Hook release flushes the echo
history. This is intentionally not a fabricated ring/caller-ID state machine;
those are MIPS/POTS policy.

A first A/B proves the boundary matters: with raw octets the Analog caller
stayed in V.8 for 7.3 s and returned to SIG; with linear samples it left V.8
after 2.63 s. It selected DIAL/V.29 rather than INFO, so firmware-owned POTS
configuration remains the next missing layer; the result is evidence for the
sample representation, not a V.90 success. Adding a plausible 18 dB, one-ms
hybrid echo (`EICON_ANALOG_HYBRID_ECHO_DB=18`, delay 8) does not move either
end's state trail, so absence of local two-wire leakage is not that selector.
Nor is the two-second attach gap: zero gap makes the Analog end select the same
path sooner (0.60 s instead of 2.63 s). The remaining selector is configuration,
not line timing or echo.

## MIPS image layout recovered

`tools/eicon_mips_image.py` now recognizes this later flat image format. Unlike
build 107 PRI, the file includes physical address zero, the reset vector, and
the low shared-memory hole. The reset vector jumps via kseg1 to physical
`0x11004`; that bootstrap sets the stack and calls the protocol entry. For the
paired Analog build -- `docs/firmware/build-109/te_dmlt.am` (`bf71b254…`), tracked
alongside the DSP combifile and distinct from the tracked `te_dmlt.am`, which is
the 122-11 build -- it derives:

| item | value |
|---|---:|
| image virtual base | `0x80000000` |
| image size | `0x001ebf30` |
| protocol entry | `0x80107484` |
| initial stack / protocol end | `0x801eff70` |
| global pointer | none (absolute-address code model) |

The same derivation now handles the tracked 122-11 `.am`/`.2qm` and 108-130
`.qm` flat images. The existing MIPS shim still cannot run them: its intercepted
host-port, DSP-download, service-assignment, and main-loop addresses are all
build-107 PRI anchors, not relocations of these functions.

## POTS configuration records recovered statically

`tools/analog_pots_layout.py` parses the image's 28-byte named management
records instead of treating debug strings as evidence by themselves. For build
109-76 it recovers:

| control | callback | controller field |
|---|---:|---:|
| `rxhook` | `0x8009b294` | `+0x4d4` |
| `txhook` | `0x8009b294` | `+0x07b` |
| `AudioTS# Enable` | `0x80099d1c` | `+0x050` |
| `AudioCh# Enable` | `0x80096488` | `+0x050` |
| `Playing Gain dB` | `0x80095818` | `+0x29c` |
| `Recording Gain dB` | `0x80095818` | `+0x29e` |
| `LI Gain Boost dB` | `0x80095718` | `+0x11fe` |

There is a second AudioTS/AudioCh pair at controller field `+0x33c`, using
callbacks `0x80099f38`/`0x800963ec`; it is the other controller layout carried
in the same image, not a duplicate operation on one object.

The callbacks establish the representation. `AudioCh# Enable` stores the
selected channel in bits 1..8 of the field and publishes `(word >> 1) & 0xff`
at controller `+0x308`. `AudioTS# Enable` clears the eight-byte bitmap at
controller `+0x30c..+0x313`, walks all 64 input bits, remaps logical slots 1..8
through the hardware mapping helpers, and sets the resulting physical bits.
The global controller root is the absolute word at `0x800112c0`; its `+0xd14`
pointer is the object these callbacks update.

Card properties are equally concrete. The table is 28-byte records at file
`0x1c4880 + card_type*28`; byte 13 is channel count. Card type 77 carries four,
78 carries eight, and 92 carries two. The protocol entry reads patched card
type byte `0x80000069`, selects the property record, then takes the common
Analog branch at `0x8010781c`. That branch installs per-adapter bases at root
`+0x128/+0x12c`, enables the MIPS interrupt mask, and enters the POTS main loop.

This means the minimum faithful MIPS seam is now bounded: patched card type 77,
root/controller allocation, hook fields, one enabled AudioCh bit, its matching
64-bit AudioTS mask, and zero/default gain fields. It is not necessary to guess
a DAA waveform protocol before booting the POTS state machine.

## Build-109 now executes from reset, and the remaining emulator boundary is explicit

`tools/analog_mips_boot.py` is the first build-specific executable backend. It
maps the flat image at physical zero, patches initial task/card type, starts at
the real reset vector, follows the uncached jump to bootstrap `0xa0011004`,
initializes the allocator, calls protocol entry `0x80107484`, and allocates the
controller object. With the host's DSP archive staged, its current address is
`0x802c9000`. Hardware register writes use the
CPU TLB/direct kseg mappings; trace/time callbacks remain explicit
no-result/monotonic seams as in the PRI shim.

The Unicorn blocker is fixed. Two missing pieces had interacted. First, the
real Analog loader patches header `+0x6c` with the aligned protocol-end address,
which is the DSP download base. Leaving it zero made the allocator start in low
memory and eventually overlap the image; the apparent low-pointer problem was
therefore partly an incomplete loader emulation. Build 109 receives
`0x801eff70` there. `analog_telindus_load()` then places the card-type-77 DSP
archive at that address, and `PCINIT_DSP_IMAGE_LENGTH` advances the heap past
its 881,124 bytes. The resulting controller root is `0x802c9000` rather than
the earlier no-archive diagnostic value `0x801f1000`.

Second, Unicorn's virtual-TLB callback mode proved unstable across this image's
long initialization. The harness now uses the CPU TLB like the existing 4BRI
backend: it runs through the firmware's own 16-entry TLB wipe, stops at
`0xa00110e4`, then installs one 1 MiB-page pair mapping the 2 MiB useg work area
to separate physical backing. This runs the full allocator and initialization
without pointer rewrites or generic control-flow recovery.

Two bounded hardware seams then allow standalone progress: CP0 Count advances
from a host counter, and the absent FPGA/DSPDAA channel mailbox reports one
available channel plus its ready bit. With those in place the firmware allocates
and publishes the real POTS object at root `+0xd14 = 0x804364c0`, passes both
fatal waits (`0x801057ec` and `0x801058cc`), and remains active in its native
POTS service loop around `0x8012f2c0..0x8012f468`. Address translation,
scheduler timing, global allocation, and POTS controller construction are no
longer blockers.

The standalone backend can also apply the first host management values through
the real callbacks (`--configure-audio`). It invokes `AudioCh# Enable` and
`AudioTS# Enable` in setter and publication modes; channel 1 produces
`controller+0x308 = 1`, and logical timeslot 1 produces the native physical
bitmap `01 00 00 00 00 00 00 00` at `+0x30c`.

## Native POTS supervision is connected to SIP

`tools/analog_mips_modem.py` now combines that native supervisor with the
Analog ADSP media engine. The indexed 16-bit hardware helpers at
`0x80104418/0x8010444c` are explicit mailbox callbacks keyed by physical DSP
block and ADSP address. Board discovery identified channel 1 as block
`0xbf803800`; runtime reads are snapshotted from its live DM before each MIPS
slice, while MIPS writes remain in a shadow mailbox until the native MIPS DSP
download replaces the direct task. Applying discovery writes to an already
running direct task is not equivalent and corrupts its overlay state.

SIP seizure now also publishes the channel-1 receive-hook sensor bit in the
native DAA sensor block (root `+0x1294`, plane `+8`), advances the POTS loop,
and clears it on BYE/failure. Mixed `--native-mips` loopback is enabled for an
Analog caller, and automatically selects build-109 `te_dmlt.am`.

The first live test, `/tmp/analog-native-loop4`, completed SIP INVITE/180/200,
attached the Analog codec off-hook, ran native POTS concurrently with media,
and emitted the same strong 820 Hz signal beginning near 2.5 s. The mailbox
and hook plumbing therefore carry a SIP call without silence or a firmware
fatal wait. It still selects DIAL/V.29 rather than V.90A because modem CAI/IDI
assignment and the MIPS-owned native DSP download are not connected yet; the
current bridge deliberately retains direct ADSP modem assignment.

A tty `ATD` must not preload V8.ANA. AT is host-side call control: it creates
the SIP call and a calling modem assignment, which starts the DSP in DIAL;
DIAL then owns dial progress and the request for page 6. The early Analog MIPS
wrapper incorrectly loaded `0x025f` during `configure_modem()`. That synthetic
entry has been removed. `/tmp/tty-dial2` verifies the corrected sequence:
`ATDT6001` -> SIP INVITE/200 -> native off-hook -> SIG.A96 -> DIAL.ANA partial.
The natural next request is currently page 21/V29FC.ANA at 1.0 s, not V.8.
That is now useful evidence at the real boundary: native CAI/IDI assignment
must tell DIAL this is a V.90A data call; forcing V.8 would only conceal it.

The Analog FXO call has another phase before that assignment. Build 109 retains
the complete POTS dial path (`senddialtone`, tone/pulse dialing and its digit
string); the MIPS POTS layer, not the modem overlay, emits PSTN DTMF. SIP must
therefore not become the line until those digits finish. The intended bridge
is: ATD -> native POTS off-hook -> local dial tone -> MIPS-generated DTMF ->
interdigit quiet -> SIP INVITE -> remote RTP -> modem DIAL/V.8.

`AnalogLineInterface` now has a streaming 8 kHz DTMF detector for this boundary.
It requires two matching 40 ms windows and 300 ms post-digit quiet, reports
outgoing digits in the SIP trace, and rejects the existing 820 Hz modem tone.
Running it over `/tmp/analog-native-loop4/caller.wav` reports no digits, which
confirms the present direct assignment bypasses the MIPS POTS dial generator.
The detector is ready; delaying INVITE on it must wait until ATD is delivered
to the native build-109 POTS call path, otherwise the endpoint would deadlock
waiting for digits no component was asked to generate.

### ATD now reaches native build-109 IDI

The supposedly missing host interface did not move: build 109 initializes the
standard Eicon PR_RAM request ring at physical `0x1000` and publishes signature
`0x4447` at `+0x1e`. `AnalogMipsBoot.post_idi_request()` now implements that
ring directly. A modem signalling `ASSIGN` carrying the tty CAI is consumed by
the real protocol and returns `ASSIGN_OK (0xef)`, allocating entity ID 2.
`ATDT6001` then submits the driver's real `CALL_REQ` payload, including UID,
full modem CAI, called-party IE `6001`, and service 2/3. The live SIP trace now
reports `[analog-mips] native CALL_REQ queued for 6001` before its INVITE.

The request exposes the next exact wait. Build 109 consumes CALL_REQ but does
not enter native `senddialtone()` at `0x800206d4`; instead it repeatedly reads
DSPDAA registers `0x4000`, `0x4009`, `0x400a`, and `0x400b` on all four blocks.
There are no post-CALL writes and no return code because the current channel-
available bootstrap seam never performed the native DSPDAA task download.
Thus the MIPS POTS dial generator has no tone-generation DSP service to call.
This is not a digit-format or DTMF-detector issue anymore: completing native
DSPDAA bootstrap/mailbox command acknowledgement is what unlocks the digits.

The first half of that bootstrap is now restored. `AnalogMipsBoot` stages
`build-109/dspdload.bin` exactly as `analog_telindus_load()` does: 38 portable
download descriptors for card type 77/file set 18 at `0x801eff70`, plus the
`PCINIT_DSP_IMAGE_LENGTH` tuple in shared RAM. This matters independently of
ADSP execution. Without the table, native CALL_REQ traced `Resource unavailable
07 11`; with it, resource 0x11 is admitted, `dsp_assign` selects channel 1, and
the firmware requests download `0x0258` (`TIKRNL81.ANA`).

The live bridge now services transient TIKRNL mailbox pointers immediately
instead of every 20 ms and commits post-attachment MIPS replies to DM. It
observes native pointer replies on DM 8/10, but the 0x0258 assignment is still
released before `senddialtone()`: the direct task was loaded outside the native
segmented loader, so its completion state is not attached to the native
`dsp_assign` request. The remaining seam is consequently much narrower than
"load the archive": connect native 0x0258 loader completion to the selected
ADSP core, then let the already-posted CALL_REQ continue.

The native loader's direct path is now captured too. Build 109 does not route
bulk downloads through `0x8010444c`; it writes the physical IDMA ports at each
DSP block (`+8` address, `+0` data). `AnalogMipsBoot` models those MMIO ports,
including two-cycle 24-bit PM writes, and recovers 426 nonzero PM words plus 98
DM words per core during kernel download. The selected image is attached to the
live ADSP before CALL_REQ. At the later `dsp_assign` boundary, the firmware's
requested ID and channel object are now recorded (`0x0258`, object
`0x8035dfa0`) and the normal `0xa5a5` running acknowledgement is published.

Driver and MIPS evidence exposed an important correction here. The selected
channel descriptor is `*(request+0)`, and its `+0x10` field is the physical DSP
block. For this call it is `0xbf804800`, not `0xbf803800`; Analog channel order
is board-wiring order rather than ascending MMIO order. The former
`hw_reads.most_common()` selection was only a discovery heuristic and put the
running acknowledgement on the wrong core. Runtime synchronization now adopts
the native descriptor's block as soon as `dsp_assign` selects it.

The Linux configuration script also shows the board parameters a real Analog
installation supplies: global `PCINIT_CAS_DIALTYPE=0` (DTMF),
`PCINIT_CAS_BC=1`, and `PCINIT_POTS_DIRECTION=0` by default. Injecting those
three exact tuples before boot does not change this release. Likewise, setting
CAI line-taking bit `DSP_CAI_MODEM_USE_POTS_INTERFACE` does not select
`senddialtone()`. Those are therefore real configuration controls, but not the
current branch condition.

Staging, native resource selection, IDMA byte transport and loader identity are
now in place. The asynchronous completion callback has now been identified and
executed too. It is `0x80138fec`, not request `+0x10` (`0x80111ec0`, the deferred
free callback). It consumes the channel object at `*(request+0)`, writes the
TIKRNL81 boot token `0x5a5a` to the descriptor-derived register `0x3fff`, polls
for `0xa5a5`, validates transfer state, and advances the channel through
`0x8012b7cc`. With the already-attached MMIO shadow segments supplying the
bounded validation seam, the native callback returns success (`1`).

The successful callback removes the download-verification failure, but the
resumed CALL still performs CAS establishment/release and `dsp_release` without
entering `senddialtone()`.

The post-callback branch is now identified. The Linux Analog loader always sets
`IoAdapter->protocol_id = 34` and `ProtVersion = 0x80|34`; byte 19 of shared RAM
carries that selector. The harness had left byte 19 zero, so the DMLT image was
indeed booting its default CAS personality. It now publishes `0xa2`, and native
traces confirm `Conf: Prot=34` plus the POTS defaults (`FXS/FXO Loop Start
without Answer Supervision`, `POTS TX DTMF`). This changes CAS L1 from state 0
to state 3 and proceeds through states 8/9, proving the selector is effective.

The next failure is explicit rather than an ambiguous release:
`CAS-E all lines out of service, L1 error (cable or trunc)`. The POTS layer is
polling DSPDAA status registers `0x6e19/0x6e5e/0x6e5f/0x6e02/0x6e60`; all remain
zero because the standalone MIPS board has no running native DSPDAA kernel.
Boot-time `0x80139130` was only a one-channel availability seam, and the live
ADSP still runs the direct modem image. The physical line model now supplies explicit exchange battery and loop state:
48 V idle by default, approximately 9 V/24 mA while seized, and zero when the
line is disconnected. Native CALL admission rejects a line without battery.
At the CAS boundary, a battery-present line replaces only the missing DAA
`all-lines-out-of-service` result and resumes the firmware's normal state-9
branch; a disconnected line retains the native cable/trunk error. Environment
controls are `EICON_ANALOG_LINE_VOLTAGE` and `EICON_ANALOG_LOOP_CURRENT_MA`.
This removes the cable/trunk diagnostic, although the subsequent call still
releases before `senddialtone()` and remains the next state to trace.

## Tone-generation test

`tools/modem_tone_probe.py` applies a Goertzel scan to the emitted G.711 capture.
The mixed direct-ADSP run is unambiguous:

```text
PRI/DPCM answerer: 2100 Hz, 20-25 dB above mean spectral-bin power
ANA/APCM caller:     820 Hz, 23.9 dB above mean, beginning at 2.6 s
```

The PRI signal is ANSam. The Analog signal begins exactly when `V8.ANA` leaves
for DIAL/V.29 and then remains a constant 820 Hz for the capture. Thus the
Analog codec path **is generating a real narrowband tone**, not silence or
broadband garbage, but it is the wrong state-machine path and wrong signal for
the desired V.90A handshake. This narrows the work to getting the MIPS POTS
configuration/assignment into control; transmitter execution itself is alive.

## Next decisive step

Adapt the MIPS shim to build 109-76 using its own function anchors and emulate
the Analog line interface—not just its load layout. Start with card type 77,
protocol 34, one initial task, POTS CAI bit `0x40`, hook/ring state, and the
64-timeslot audio-enable mask. Once it boots, compare the last V.8 frame through
first INFO state `0x37` against PRI, including:

- full WDB and writers;
- selected INFO entry/vector;
- `DM(0x164c)`, `DM(0x19cf)`, and `DM(0x198e)`;
- bootpage request and TIKRNL request type;
- whether state `0x24` executes PM `0x2602` to install framer B.

Because all implicated INFO PM regions are identical between F34 and ANA, any
successful difference at that point belongs to MIPS/TIKRNL configuration, not
the INFO demodulator implementation.
