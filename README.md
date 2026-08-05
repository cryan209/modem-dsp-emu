# eicon-adsp-emu

An ADSP-2181 emulator and a MIPS firmware harness for the **Eicon Diva Server
PRI** card, used to make the card's own V.90 digital-modem firmware run — and be
observable — without the card.

Split out of [`v90modem`](../v90modem) because the goals diverge. v90modem is a
software V.90 digital-side modem: it implements the spec. This project runs
someone else's shipped implementation under emulation and reverse-engineers what
it does. The two share no code — the split moved files, it did not untangle
them.

## What is here

- `tools/adsp2181emu/` — the ADSP-2181 core in C (based on MAME's ADSP-21xx
  core, BSD-3-Clause), built as `libadsp2181.dylib`, plus a standalone
  disassembler under `dasm/`. Provides DM/PM write watches, execution watches
  and per-address execution coverage, which is how nearly every finding in the
  analysis doc was established.
- `tools/eicon_mips_shim.py` — runs the card's real MIPS firmware under Unicorn
  and drives the ADSP through it. `create_native_mips_modem()` is the harness
  that reproduces the live card on V.90 page 14.
- `tools/eicon_adsp_sip.py` — answers a real SIP call and puts the emulated card
  on the line, so an analogue modem can dial it. G.711 passthrough only.
- `tools/eicon_loopback.py` — runs two endpoints on loopback and calls one from
  the other, so a handshake can be traced from both ends without hardware.
- `tools/eicon_idi.py` — the IDI payloads (CAI, LLI/LLC/DLC) and the entity/call
  state machine, ported from divas4linux's `putcai()` and `atPlusMS()` rather
  than hand-built. `tools/eicon_at.py` — the AT command set `/dev/ttyds*`
  presents, on top of it. Both are pure Python with no emulator dependency, and
  are covered by `tests/test_eicon_idi.py` and `tests/test_eicon_at.py`.
- `tools/v90_dpcm_*.py`, `tools/eicon_*_replay.py` — offline replay of recorded
  line audio through the data pump, plus the state/vector tracers.
- `tools/dial_*.py` — the DIAL/TIKRNL dispatch investigation harnesses.
- `docs/eicon_adsp_firmware_analysis.md` — the running log, 78 sessions. Read
  the relevant session before changing anything; it records what has already
  been disproved, which is most of the value in this repo.
- `docs/firmware/` — the card's firmware images. Required inputs, tracked.

## Build and run

The top-level makefile does not exist here; the emulator has its own:

```bash
make -C tools/adsp2181emu
```

`libadsp2181.dylib` is gitignored, so build it before trusting any replay.

The Python harnesses need `unicorn`, which is why they run under a separate venv
rather than the system interpreter:

```bash
/tmp/eicon-venv/bin/python tools/v90_dpcm_vector_trace.py \
  artifacts/eicon-native-tower/run34.rx.ulaw --to 17.0 --refill-audit
```

A live call, answering as extension 6001:

```bash
/tmp/eicon-venv/bin/python -u tools/eicon_adsp_sip.py \
  --native-mips --force-info-after-v8 --native-bearer-activation --tx-prbs \
  --trace-v90d-state --law pcmu --capture-prefix artifacts/eicon-native-tower/runNN \
  --mips-kernel artifacts/eicon-dsp/build-117-926/kernel/0009-diva-server-pri-30m-kernel \
  --mips-tikrnl artifacts/eicon-dsp/build-117-926/tikrnl/0258-tikrnl81.f34-task \
  --registrar asterisk.example --username 6001 --password 6001
```

### Profiles

That command is the same every time bar the capture prefix, so it has a name.
`profiles.toml` records the combinations that already travel together, and
`./run` expands one:

```bash
./run native-tower --run 35
```

`./run --list` shows what is defined. Anything after the profile name is passed
through and *overrides* a same-named flag rather than repeating it, so
`./run native-tower --run 35 --law pcma` sends `--law pcma` only. `-e KEY=VAL`
sets one of the `EICON_*` variables for the run; profiles can carry them too,
which is how `v34-live` pins `EICON_MODULATION`.

The resolved command — environment included — is printed to stderr before it
runs, and `./run -n <profile>` prints it without running anything. That output
is the line to paste into a session entry in the analysis doc: profiles are a
shorthand for typing, not a substitute for recording what was run.

Registrar host and credentials come from `[vars]`. Override them, or point
`python` somewhere other than `/tmp/eicon-venv`, in `profiles.local.toml`,
which is gitignored and overlaid a table at a time.

### The terminal before the call

`--v42-pty` allocates the terminal at startup and prints its path, and with
`--at` the command set is answered from that moment — `./run at`, then attach
with `screen` and type. The endpoint services the terminal whenever no call is
up, so `ATS0=0`, `AT+MS=` and the S-registers can be set *before* the INVITE
lands, which is when they have to be set: `+IE` reaches the CAI of the next
call, not the one in progress.

`--preboot` boots a card at startup rather than inside the answer path, and
keeps one booted between calls. Nothing clocks it while it waits — the ADSP
only advances on the sample clock — so the emulated timeline is unchanged and
only the wall-clock cost moves, off the INVITE-to-first-tick path. Each call
still consumes its card and the next is booted fresh, so no firmware state
crosses a call boundary and per-call boots stay comparable.

For the experimental V.42 endpoint, replace `--tx-prbs` with `--tx-v42`.
While the DSP has not published a negotiated data rate, this path normally
uses the legacy PRBS training fill. This is disabled by default so a real
modem does not receive random-looking host-generated bits; it uses mark fill
until the rate is known. Set `EICON_V42_TRAINING_PRBS=1` to enable PRBS for
training tests. It supplies HDLC flags during idle,
decodes the upstream synchronous mailbox,
answers XID and SABME, acknowledges received I frames, and transmits its own:
`send()` segments a byte stream into N401-sized I frames, tracks V(S)/V(A)
against the window, honours incoming N(R), stops on RNR and goes back N on REJ.
It still does not implement V.42bis. XID negotiates the V.42 N401 and window
parameters; the local defaults are k=15 and N401=128. The optional-functions
mask carries the six bit positions Table 11a/V.42 requires of every XID
transmitter (`0x0000898A`) and none of the four optional procedures of
clause 10, which are unimplemented. Frames are addressed per Table 6/V.42 —
the C/R bit depends on the direction and on which end originated the call, so
commands and responses do not share an address octet.

Add `--v42-pty` to put a terminal on the link. It allocates a pseudo-terminal
and prints the path, so a session can be attached before the call lands:

```text
[v42-pty] terminal ready on /dev/ttys012 -- attach with: screen /dev/ttys012
```

Anything typed becomes I frames; acknowledged payload is written back. The PTY
carries no line speed, parity or flow control -- those belong to a real modem's
UART, and this link starts at the synchronous V.42 boundary. `stty` will appear
to work and change nothing. LAPM's window is the only buffer, so when it fills,
reads stop and the terminal blocks, which is the intended back-pressure.

Retransmission is counted in data-pump service calls rather than seconds,
because the bit pipe has no wall clock and the harness can run far from real
time; a stalled window is probed with RR(P) before anything is resent.

The V.42 detection phase (7.2.1) is implemented for both roles: the answerer
sends mark until four DC1s of alternating parity arrive, then sends the
"V.42 supported" ADP ten times; the originator sends ODP until it sees two
adjacent ADPs. Both then enter protocol establishment. Without this exchange
an originator may fall back to no error control -- a Courier reports `Protocol NONE` and both directions become
garbage (Session 86). `EICON_V42_DETECT=0` restores the old behaviour.

Note that `modem_nl_assign_payload()` sets `DLC_MODEMPROT_DISABLE_V42_V42BIS`,
so the card's own V.42 is switched off and this Python is the V.42 entity. Using
the firmware's implementation instead has never been tried; Session 86 sketches
what it would take.

Rebuild the disassembler (only needed off arm64):

```bash
c++ -O2 -std=c++17 -o tools/adsp2181emu/dasm/dasm \
    tools/adsp2181emu/dasm/dasm_main.cpp tools/adsp2181emu/dasm/2100dasm.c
```

It decodes ALU/MAC and control flow correctly but mislabels the direct DM
read/write opcodes, and on some overlay pages it mis-decodes wholesale — the
watchpoints are the ground truth, not the disassembly.

## Gotchas

- **Two replay harnesses disagree past the INFO page.** `eicon_info_replay.py`
  uses `LiveKernelModem`; live captures and `v90_dpcm_replay.py` use
  `create_native_mips_modem()`. Only the native one reproduces the live card on
  page 14. Session 50 records what mixing them up costs.
- **Two page-14 diagnostics are default-on** in `eicon_mips_shim.py` and both
  change what the card puts on the line. `EICON_V90D_TX_BLOCK_HOLD=0` restores
  the resident kernel's per-frame clear of the mapping-frame block
  `DM(0x3fa7..0x3fac)`, which drops five of every six downstream samples;
  `EICON_V90D_BULK_ADAPTER=1` keeps the `0x1900..0x19c8` echo bulk-delay adapter
  live, in which case the outer state machine stops at `0x0068` and the card
  transmits nothing. That adapter is the card's echo canceller, so this is a real
  functional gap rather than a tidy diagnostic, and it cannot simply be switched
  back on: Session 88 has the three failure modes and the reason Session 65's
  `DM(0x3fb3)` finding no longer reproduces.
- **Never infer generator activity from block contents.** A constant block is a
  legitimate signal — Phase 4 opens with Ri on a single PCM codeword (V.90
  §9.4.1.1) — so "constant" does not mean "stale". Count executions of the
  generator dispatch at PM `0x2a52` instead. Session 68 records the audit that
  got this wrong.
- **The media thread has 20 ms and spends 3.9 of them.** It was 11 ms until
  Session 81: a rangeless `UC_HOOK_CODE` made every MIPS instruction a Python
  callback, which was 8.5 ms of the tick, and the trace it appended to grew to
  813 MB over a 20 s call. `_step_mips` is now 1.9 ms and the ADSP 2.0 ms.
  Diagnostics are 0.5 ms, so logging is still not what makes a call flaky; what
  does is losing wall time, so watch the `[media]` line for substituted RX
  samples, discards and clock holds. `--mips-interval 320` is still there if you
  need more headroom, and the Session 70 pacing defaults are now conservative.
- **Offline replay cannot see a missing capability, and reaching page 14 in
  replay proves nothing.** `v90_dpcm_replay.py` is open loop: the recorded RX
  already holds a V.90-accepting answer whatever the card offered. Session 82
  used that to argue `V8_SETUP` (write DB `+0x04`) had broken V.90 and was wrong
  — hardware connects V.90 with it at `0x0000`. `EICON_WDB_OVERRIDE=0x04:0x6000`
  remains as an A/B for the still-unexplained documented-vs-native capability
  gap, not as a fix. If the question is what the card *advertises*, only a call
  answers it.
- **A media-path exception no longer kills the endpoint.** It used to propagate
  out of `run()` and exit the process, so a firmware fault and the peer hanging
  up produced identical logs — `[capture] wrote` with no `[call] ended` above it
  is the tell, and `call10-force-v34-cai.endpoint.log` is the example. `run()`
  now reports the overlay, bootpage and TrnProgress at the fault and keeps
  listening. Session 83.
- **`EICON_MIPS_WARMUP` shifts the timeline by a sample.** Three idle supervisor
  passes run at attachment so Unicorn translates the media-phase mainloop before
  the sample clock starts; without them the first in-call tick costs 93 ms
  offline and 390 ms live. It is the one part of Session 81 that is not
  behaviour-preserving. Set `EICON_MIPS_WARMUP=0` when diffing a replay against
  a recorded capture.
- **A capability the card "does not support" may just be a download you did not
  stage.** The protocol image decides what a channel can do by searching the DSP
  download table this harness builds, so a shipping file set that omits an
  overlay reads as a missing feature. V.90A is the case: the PRI file set has no
  V.90 APCM overlay, and `EICON_DSP_EXTRA_DOWNLOADS=0x026b` supplies it, after
  which `EICON_MODULATION=v90a` gets the supported branch instead of the
  firmware's "V.90A not supported" trace. Session 134.
- **Never transcode the G.711 stream.** The RTP payload *is* the DS0 PCM stream
  the far-end converter sees. No resampling, VAD/CNG, comfort noise, echo
  cancellation or gain anywhere in the audio path.
- `artifacts/` is untracked and large; a single hardware session runs to
  hundreds of megabytes.

## Still in v90modem

Two files this workflow uses were deliberately left there, because v90modem
depends on them:

- `tools/cx_at.py` — Courier/USR AT diagnostics and dialling, referenced by
  `docs/v90_hardware_interop.md`. Courier calls against this emulator are placed
  with it: `./.venv/bin/python tools/cx_at.py --dev /dev/cu.usbserial-21210 dial 6001 --wait 120 --pre 'AT&M0'`
- `docs/courier_firmware_analysis.md` — peer-modem analysis serving both
  projects, and cited from v90modem's status notes.

## Where things stand

**Start with [`docs/handoff.md`](docs/handoff.md).** It is the current picture:
the three live blockers, the full echo-canceller trace, an explicit list of what
has already been disproved, reproduction commands and the ranked next steps.
`docs/eicon_adsp_firmware_analysis.md` is the chronological record of how each
finding was established, and is the place to look once the handoff points you at a
session.

In short, as of Session 93: the card reaches full V.90 data mode and has walked
the whole state machine to `0x00d0` at 38666/24000 with DCD and CTS asserted. Three
blockers are open — V.34 does not connect at all, V.90 needs
`--native-bearer-activation` for reasons unknown, and DIL is a lottery whose
leading suspect is the card's echo canceller, which this harness disables because
enabling it corrupts the V90D record table. A LAPM transmitter and PTY terminal
exist and are unit-tested. Against hardware the receive path now demodulates,
frames and passes FCS, but establishment does not complete: the peer
retransmits XID and no SABME has ever arrived. See `docs/handoff.md` for the
fixes waiting on the next live call.
