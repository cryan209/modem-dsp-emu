# Build 109 Analog-card V.8/INFO oracle

## What is now runnable

`tools/dial_tikrnl_drive.py` accepts `--firmware-set analog109`. This is a
coherent direct-ADSP stack built from card type 77 / file set 18 of the
recovered 109-789 combifile, tracked as `docs/firmware/dspdload.bin.109-789`
(`2c106902…`) because nothing else here reproduces it:

- `0x000d` DIVA Server Analog kernel
- `0x0258` TIKRNL81.ANA
- `.ANA` V.8, INFO, DIAL and partial overlays
- APCM rather than DPCM selected in `V8_setup`

Extract it with:

```sh
mkdir -p artifacts/eicon-dsp/build-109-789-analog/{kernel,tikrnl,overlays}
python3 tools/eicon_dsp_extract.py docs/firmware/dspdload.bin.109-789 \
  --card-type 77 --match '^DIVA Server Analog Kernel' \
  -o artifacts/eicon-dsp/build-109-789-analog/kernel
python3 tools/eicon_dsp_extract.py docs/firmware/dspdload.bin.109-789 \
  --card-type 77 --match '^TIKRNL81\.ANA Task' \
  -o artifacts/eicon-dsp/build-109-789-analog/tikrnl
python3 tools/eicon_dsp_extract.py docs/firmware/dspdload.bin.109-789 \
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

## MIPS image layout recovered

`tools/eicon_mips_image.py` now recognizes this later flat image format. Unlike
build 107 PRI, the file includes physical address zero, the reset vector, and
the low shared-memory hole. The reset vector jumps via kseg1 to physical
`0x11004`; that bootstrap sets the stack and calls the protocol entry. For the
paired Analog build -- `docs/firmware/te_dmlt.am.109-76` (`bf71b254…`), tracked
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
