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

For the experimental V.42 endpoint, replace `--tx-prbs` with `--tx-v42`.
It supplies HDLC flags during idle, decodes the upstream synchronous mailbox,
answers XID and SABME, and acknowledges received I frames. It intentionally
does not yet implement V.42bis or an outbound application data source.

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
  live, in which case the outer state machine never reaches `0x0080` and the
  card transmits nothing.
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
- **Offline replay cannot see a missing capability.** `v90_dpcm_replay.py` is
  open loop: the recorded RX already contains the peer's responses, so it holds a
  V.90-accepting answer whatever the card offered. Session 82 found `V8_SETUP`
  (write DB `+0x04`, the V90_DPCM and digital-network enable) sitting at `0x0000`
  for whole calls since Session 75, with all five commits since giving
  byte-identical replay output. If a question is about what the card *advertises*,
  only hardware answers it. `EICON_WDB_OVERRIDE=0x04:0x6000` is the A/B.
- **`EICON_MIPS_WARMUP` shifts the timeline by a sample.** Three idle supervisor
  passes run at attachment so Unicorn translates the media-phase mainloop before
  the sample clock starts; without them the first in-call tick costs 93 ms
  offline and 390 ms live. It is the one part of Session 81 that is not
  behaviour-preserving. Set `EICON_MIPS_WARMUP=0` when diffing a replay against
  a recorded capture.
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

The card connects to both a USRobotics Courier and a USRobotics 56K Fax V.92
(V5.4.5). The V.92 modem made two repeatable raw V.90 connections at
45333/21600 with no retrain, no media loss and 46 dB reported SNR. In ARQ-only
`&M5` mode it instead stopped at Eicon state `0x00b3` on both attempts and
reported no connection. This makes V.42/XID the next implementation needed for
a normal usable link; `--tx-prbs` currently supplies only unframed payload.
The Courier's additional open blocker is retrain: the card restarts its own
training from `0x00c4` and the restarts do not converge. Sessions 69 and 71
have the details and open list.
