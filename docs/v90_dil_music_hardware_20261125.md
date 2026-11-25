# Custom musical V.90 DIL: real Diva hardware, 25 November 2026

**Confirmed:** the unmodified Diva digital modem emits Vladimir's requested
Beethoven DIL. Both short and full-motif calls also reached V.90 data mode.
This is a real-card result, not an emulator result or substituted audio.

## Rig

- Tinyprox (`tinyprox.net.cryan.nz`): new unprivileged Debian 12 **CT 100**, hostname
  `v90-dil-test`, DHCP `192.168.88.234`, 2 cores, 2 GiB RAM, 12 GiB disk,
  `onboot=0`. Existing containers were not modified.
- Caller: `https://github.com/themylogin/v90modem`, commit
  `29728e2316ac7165aa6cab7ae0ad7149ca38c37f`, `/root/v90modem` in CT 100.
  Binary SHA256:
  `bbf2007efbd2fc0f2476d86f9f287a35ca1be23a538a19070e118e45e4bd79e0`.
- SIP through existing Asterisk CT 105 (`192.168.88.122`), idle account 6002,
  dialling **7930**, using PCMA/A-law throughout the recorded bearer.
- Answerer: `eicon420.net.cryan.nz`, **controller 7, Diva 4BRI-8 PCI v2
  SN 12196**, BRI 2M DSP build **117-926**, protocol TE_DMLT **122-11**.
  Existing `eicon-7930-12` service answered via `/dev/ttyds12`; controller 7
  B1 carries all three calls in the hardware trace.
- Answerer configuration and existing PPP services were not changed.

The historical 7900/controller-2 instructions were stale. An initial 7900 call
connected as V.34 31200 and did not appear as a call in controller 2's trace;
that controller's physical line is down. Preserve this first attempt as
`baseline`, but **do not count it as a Diva V.90 experiment**. The actual
hardware baseline and both music calls used 7930/controller 7.

## Build and caller settings

Installed build-essential, git, pkg-config, libssl-dev, libasound2-dev,
libsamplerate0-dev, libtiff-dev, libjpeg-dev, python3 and tcpdump in the new CT.
Build the checked-in PJProject/SpanDSP, then link with the libraries actually
available/enabled on Debian:

```sh
make -j2 sip_v90_modem \
  SYSTEM_LIBS='-ltiff -ljpeg -lssl -lcrypto -lm -lpthread -lutil -lasound -lstdc++'
```

No source patches. The default Linux makefile links optional FFmpeg/V4L2/Opus
libraries even though the fresh PJProject configuration disables them; the
command-line library override avoids that unrelated linking failure.

Common environment:

```sh
SIP_FORCE_PCMA=1 ME_MODE=v90 ME_V90_ROLE=analogue \
VPCM_ME_VERBOSE=1 VPCM_G711_TAP_DIR=/root/dil-results/RUN
```

Via the PTY: `AT`, `ATE1V1`, `AT+MS=V90,0`, then `ATD7930`.
The capture directory must exist before starting the modem.

Short extra settings:

```sh
ME_V90_ANALOGUE_DIL=file:/root/v90modem/dil/beethoven-cut.dil
ME_V90_ANALOGUE_DIL_COVERAGE=0.68
```

Full-motif extra settings:

```sh
ME_V90_ANALOGUE_DIL=file:/root/v90modem/dil/beethoven.dil
ME_V90_ANALOGUE_DIL_COVERAGE=0.91
ME_V90_ANALOGUE_JA_SD_BAR_MS=3000
```

## Results

| Run | Requested DIL | Down / up | Completed V.90 startup |
|---|---|---|---|
| baseline-eicon | measurement, 120 segments | 56000 / 31200 | Yes, 17.277 s |
| short | Beethoven cut, 73 segments | 48000 / 31200 | Yes, 18.488 s |
| full | Beethoven full, 206 segments | 56000 / 31200 | Yes, 21.926 s |

All times are caller media-relative, not wall clock. The classic console's
`CONNECT 31200` reflects upstream; the detailed engine logs supply downstream
and explicitly identify `mod=V90`, B1d completed with 48/48 frames and zero bit
errors. These are startup/data-mode results, not PPP transfer or sustained BER
tests. No retrain was needed to obtain these connections.

### Independent audio verification

`analyze.py` reconstructs the expected A-law codewords directly from each `.dil`
descriptor, finds a 1024-byte prefix in the caller receive tap and extends the
exact comparison. It then finds that entire matching span independently in the
Diva's own TX audio extracted from `mlog -A3`.

| Run | Caller RX DIL starts | Exact descriptor prefix | Also exact in Diva TX |
|---|---|---|---|
| short | sample 116168, 14.521 s | **18144 samples, 2.268 s** | Yes |
| full | sample 119045, 14.880625 s | **42666 samples, 5.33325 s** | Yes |

Requested complete cycles are 21744 and 43266 samples respectively. The caller
terminates DIL after sufficient coverage, so do not claim it received every
sample of either requested complete cycle. The extracted WAVs contain only the
verified prefix. The full call captures all but the final 600 samples of its
requested cycle. The card trace, rather than just caller-side interpretation,
proves which endpoint transmitted the matching waveform.

## Evidence

Local: `artifacts/v90-dil-hardware-20261125/` (untracked).

- `short/beethoven-dil.wav`, `full/beethoven-dil.wav`: listenable excerpts of
  **actual hardware audio**, decoded to 8 kHz PCM16.
- `short/diva-tx-handshake.wav`, `full/diva-tx-handshake.wav`: received answerer
  audio from call start through DIL and one second beyond the verified span.
- `{baseline,baseline-eicon,short,full}/live-{rx,tx}.g711`: original raw A-law
  taps; corresponding WAVs, console, engine logs, RTP CSV and tap status files.
- `diva/mlog7.txt`: full bounded controller-7 trace including audio and real
  `TRAINPROGRESS 00d0` for all three calls.
- `diva/audio/diva-b1.{rx,tx}.alaw`: hardware audio split by byte; RX is high
  byte, TX is low byte of each `SAMPLE[]` word. Three successive calls are
  concatenated; there is no wall-clock silence padding between calls.
- `descriptors/`: both original DIL descriptor files.
- `verification.json`, `analyze.py`: exact comparison results and reproduction.

Remote originals:

- CT 100: `/root/dil-results/`, `/root/run_call.py`, build logs `/root/build*.log`.
- eicon420: `/var/tmp/v90-dil-hw-20261125-ctl7/`.

The bounded hardware trace was stopped after the last call. No modem test
process remains; Asterisk has zero active calls and both 7930 answerer services
are active. CT 100 is left running for follow-up, with boot autostart disabled.
