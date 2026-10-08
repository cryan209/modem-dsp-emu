# First live V.90A caller capture (eicon420, 9 October 2026)

Until now, every real-card capture this project used (run34, run48, run65,
run76, CX, Courier) had the card **answering**. The handoff's §"this project
has never originated a call" says so. This capture is the first with a real
Diva running the **V.90A calling side**, and it has both ends on record.

## Rig

- `eicon420` has a **Diva 4BRI-8 PCI v2** (PCI `1133:e013`, SN 1331). It
  replaced the 4BRI-v1, which had stuck SDRAM bits. The driver is
  divas4linux 9.6.8-124.26.
- The protocol is TE_DMLT 122-11 behind a Siemens CORNET PBX. **Only ISDN
  ports 1 and 2 have a line**; ports 3 and 4 do not.
- The DSP is the **BRI 2M Kernel, build 117-926**. That is the same build as
  `artifacts/eicon-dsp/build-117-926`, but the BRI kernel rather than the
  PRI-30M one. The caller ran task 619 (V.90 APCM); the answerer ran task
  618 (V.90 DPCM).
- The call was card-to-card on port 1: MSN 7911 (ttyds6, `AT&F5`,
  `AT+iQ=o1`, `AT+MS=V90a,0`) called 7910, where the `bri-test-answerer`
  service answers on ttyds3/4 with `AT+MS=V90,1`. Both ends are Diva running
  the same firmware, so this is a Diva-vs-Diva reference, not a third-party
  modem.
- **`AT&F14` cannot originate.** It is the incoming autodetect profile, and
  every `ATD` returns `NO DIALTONE` within a millisecond, with no SETUP on
  the D-channel. Use `AT&F5`.

Reproduce with `tools/eicon420_v90a_capture.sh` (run as root on the box) and
split the audio with `tools/mlog_audio_split.py`.

## Result

The call connected after 18.6 s. The two ends reported:

- Answerer: `CONNECT V90/LAPM/V42BIS/54666:TX/31200:RX`.
- Caller: RX 54666 / TX 31200, symbol rate 3200 up, 8k down. RX level −18
  dBm, SNR 39 dB, round trip 20 ms, no retrains.

Data mode lasted 21.6 s, with an echo test running over it, and then cleared
normally.

### Caller walk (`[P6,1] TRAINPROGRESS`, time from overlay 619 load)

| state | at (s) | dwell (s) | | state | at (s) | dwell (s) |
|---|---|---|---|---|---|---|
| 0050 | 0.021 | 0.002 | | 00b0 | 7.097 | 0.045 |
| 0054 | 0.023 | 0.012 | | 00b1 | 7.142 | 0.006 |
| 0060 | 0.035 | 0.041 | | 00b2 | 7.148 | 0.009 |
| 0062 | 0.076 | 0.004 | | 00b3 | 7.157 | **3.028** |
| 0064 | 0.080 | 0.090 | | 00b6 | 10.185 | 0.028 |
| 0070 | 0.170 | 0.036 | | 00b7 | 10.213 | 0.006 |
| 0071 | 0.206 | 0.717 | | 00c0 | 10.219 | 0.267 |
| 0072 | 0.923 | 0.125 | | **00c1** | 10.486 | **1.001** |
| 0073 | 1.048 | 1.827 | | 00c3 | 11.487 | 0.045 |
| 0075 | 2.875 | 0.006 | | 00c4 | 11.532 | 0.046 |
| 0076 | 2.881 | 0.002 | | 00c6 | 11.578 | 0.045 |
| **0092** | 2.883 | **0.431** | | 00c8 | 11.623 | 0.004 |
| 0094 | 3.314 | 3.000 | | 00ca | 11.627 | 0.040 |
| 0095 | 6.314 | 0.783 | | 00cc | 11.667 | 0.002 |
| | | | | 00cd | 11.669 | 0.024 |
| | | | | **00d0** | 11.693 | 21.648 |

### Answerer walk (`[P3,2]`, from overlay 618 load)

`0050 0060 0062 0064 0066 0068 006a 0070 0072 0074 0076 0078 007a(2.56 s)
007b 007c 0080(3.80 s) 00a6 00b0 00b1 00b2(3.04 s) 00b3 00b4 00b5 00b6 00c0
00c2(1.18 s) 00c4 00c6 00c8 00ca 00cc 00d0`. The answerer reaches data mode at
11.819 s, 126 ms after the caller. The two machines move in step: the
caller's `00b3`/`0094` holds line up with the answerer's `00b2`/`0080` holds.

### What it says about the emulator's caller blockers

- **`0x0092` is a 431 ms state, not a park.** The real caller leaves it on
  its own. Every emulator account of a permanent `0x0092` park (handoff §2,
  the V.90A rows) describes something the real card does not do.
- **`0x00c1` exits on the success branch.** After exactly 1.001 s (the
  3,200-tick timeout) the walk goes `00c1 → 00c3 → 00c4 → 00c6 → 00c8 → 00ca
  → 00cc → 00cd → 00d0`. That is the "sequential success exit" the handoff
  inferred, now seen on hardware. So the emulator's `DM(0x254B)` decline
  (`DM(0x2478)` = 20 against a floor of 26) is not what the real calling side
  computes at this point.
- **`0x00cd` lasts 24 ms**, so the inner-machine wait at `0x00cd` really is
  satisfied quickly on hardware.

## Files

These are untracked under `artifacts/eicon420-v90a-live-20261009/`, per
`.gitignore`:

- `caller.tx.ulaw`, `caller.rx.ulaw`, `answerer.tx.ulaw`, `answerer.rx.ulaw`:
  8 kHz μ-law, 40.0 s each.
  - mlog's `-A3` audio tap stores 260 words per record, with the high byte
    as the channel's receive and the low byte as its transmit.
  - The PBX path is digital, so the caller's TX equals the answerer's RX bit
    for bit (cross-correlation +1.000).
  - The caller's TX opens with the 1300 Hz calling tone. The answerer's TX
    opens with 2100 Hz ANSam at 1.33 s.
- `mlog1.txt`: the full card trace. It has the Q.931 exchange, overlay
  loads, `CFG1..3`, `TRAINPROGRESS`/`TXTRAINPROGRESS` for both ends, and a
  **`DATABASE[DM3ee0..]` dump of each end's DSP database at connect** (caller
  `DM3f30` = `f828 007d`, answerer `0167 0000`).
- `trainprogress.txt`: the extracted state lines.
- `mantool1.txt`: B-channel and modem state, including final rates, levels
  and SNR.
- `xlog1.txt`, `xlog2.txt`, `dial.log`, `answerer.journal`, `status.txt`.

## Replaying the real downstream into the emulated caller (9 October 2026)

**Setup**
- Baseline loopback: `--answerer-firmware-set pri117 --answerer-modulation
  v90 --caller-firmware-set analog109 --caller-modulation v90a
  --caller-kernel-dispatch --analog-codec-rate 9600 --answerer-env
  EICON_EXPAND_SPORT=1 --trace-v90a-state`.
- The primed runs add `--caller-env EICON_RX_PRIME_SYNC=<caller.rx.ulaw>:12.0:45:8.47:<map>`.
  The map is anchored on the **real caller's own milestones**, in recording
  seconds.
- Recording time is card-trace time − 405.040 s. This is fixed by the
  Phase-3 downstream onset in `caller.rx` at 13.10 s, which matches the
  caller's `0x00b0` at 13.109 s.
- The map: `0092@8.895, 0094@9.326, 0095@12.326, 00b0@13.109, 00b1@13.154,
  00b3@13.169-16.197, 00b6@16.197, 00c0@16.231, 00c1@16.498, 00c3@17.499,
  00d0@17.705`.

Runs are in `artifacts/loopback-v90a-eicon420/` (untracked).

### Control at HEAD (no prime) against the card

| state | card | emulator |
|---|---|---|
| caller `0x0092` | 0.431 s | 0.76 s |
| caller `0x0094` | 3.000 s | 3.000 s |
| answerer `0x0080` | 3.803 s | 3.80 s |
| **caller `0x0095`** | **0.783 s** | **9.22 s** |
| **answerer `0x00b0`** | **0.075 s** | **8.84 s** |
| caller `0x00b3` | 3.028 s | 5.54 s |
| reached | `0x00d0` at 11.7 s after page load | caller `0x00c0`, answerer `0x00c2`, at end of 45 s |

On the card, the answerer enters `0x00b0` 0.74 s after the caller enters
`0x0095`, and the caller leaves 40 ms later. In the emulator, the answerer is
at `0x00b0` before the caller reaches `0x0095`, and both then sit for about
9 s. **`0x0095` is the first state where the emulator departs from the card.**

### Primed with the real downstream

- The caller matches the card through `0x0092` (0.40 s) and `0x0094` (3.0 s).
- It then **never leaves `0x0095`**. This held at recording scale 1.0, at
  `EICON_RX_PRIME_LEVEL=auto` (×0.41), and at ×2.
- At ×4, `0x0092` itself was delayed by 7.5 s and the caller then stuck in
  `0x0095` again. So level is not the cause.

### What `0x0095` waits for (corrected)

The trace shows `test=0000/0006`. Condition 6 is `DM(0x21E6) ≥ 1200`, a
sustained-energy gate at `PM 0x2632..0x2641`. **It is the fallback exit, not
the success exit.** The handoff's reading of it as the route to `0x00b0` is
wrong.

```
359c/359f DM(0x0EF9/0x0EFA) = AGC(DM(0x3F8E) mant, DM(0x3F8F) exp) x DM(0x214B/0x214C)
                              ; the biquad-filtered receive pair (PM 0x358A), not an equaliser
0cbf  AR = sign(DM(0x0EFA)) * 0x1000
      y  = 0x00d3*AR + 2*0x5a19*y1 - 0x812a*y2   ; coefficients at PM 0x2119..0x211B
                              ; poles r=0.9955 at 45 degrees -> 9600/8 = 1200 Hz, BW ~14 Hz
2632  E = 0.95*E + 4*y^2  -> DM(0x21E5)
263c  DM(0x21E6) = (E >= 1500) ? DM(0x21E6)+1 : 0
```

- The chain runs 9,600 times a second, the page's codec rate. A PC histogram
  over `0x0095` counts 88,512 executions of `0x0CBF`, `0x262C`, `0x2632`
  and `0x359D` in 9.22 s.
- So this is a hard-limited, level-blind 1200 Hz tone detector, and 1200 Hz is
  the INFO carrier. That is why no level change touched it.
- **Verified by injection.** A held 1200 Hz tone (`EICON_RX_SWEEP=1200:1200:…`)
  releases `0x0095` in 0.18 s, **to `0x0024` (INFO)**.
- A 600–3000 Hz sweep in 185 Hz steps releases nothing, because the
  resonator is about 14 Hz wide.

The success exit to `0x00b0` is the inner machine advancing from istate `0x3f`
(record `0x1707`) to `0x43` (`0x1743`). That record sets bit 14 of
`DM(0x20EB)`, which is the handoff's `PM 0x348F` vocabulary bit. Istate `0x3f`
waits on the event flag `DM(0x10F3)` from the Phase-3 correlator at
`PM 0x0CF0`. In every failing primed run that flag reads 0 for the whole of
`0x0095`.

### The `0x0095` success exit is alignment-critical (fixed-offset primes)

Plain `EICON_RX_PRIME=<caller.rx.ulaw>:12.4:50:<offset>` gives fully
deterministic results (repeats are identical):

| offset (s) | caller walk after `0x0092` |
|---|---|
| 8.49 | `0094@13.20 0095@16.20`, stays |
| **8.50** | `0094@13.20 0095@16.20 00b0@17.02 00b3@17.08 00b6@20.06 00b7@20.08 00c0@20.10` |
| 8.51, 8.52 | `0095@16.18`, stays |
| 8.91–8.95 | `0095@15.74–15.78`, stays |
| 9.4 | `0094@19.40 0095@22.40`, stays |

- At 8.50 the emulator follows the card closely. `0x0094` starts at
  recording time 9.30 (card 9.326) and `0x0095` lasts 0.82 s (card 0.783).
  `0x00b0` starts at recording time 13.12 (card 13.109) and `0x00b3` lasts
  2.98 s (card 3.028). It then holds at `0x00c0`, the bidirectional handshake
  a recording cannot answer.
- But 8.49 starts `0x0094` and `0x0095` at the *same* emulator times as 8.50
  and still fails, so a 10 ms shift in the recording decides the outcome.
- `0x0092` always exits at recording time ~9.30, where Sd starts. So every
  run reaches `0x0095` at the same point in the recording, to within 10 ms.

So the emulated caller does not acquire the downstream the way the card does.
It gets through `0x0095` only on one exact alignment. Two other methods fail
the same way: `RX_PRIME_SYNC`, whose small cursor jumps at `0x0092`/`0x0094`
land off the edge, and level scaling, which the hard limiter ignores.
`run65.ulaw` at its documented offset (`12.4:50:14.0`) still passes at HEAD:
`0095@15.62 → 00b0@16.78`.

⚠ `--watch-exec` on a hot PC changes this outcome. With `--watch-exec
0x0d01:20000`, the run65 prime parks at `0x0095`; without it, it passes. Do
not draw conclusions about this gate from a run with an exec watch.

### Database at the gate, against the card at connect

- The card's frozen receive gain `DM(0x3FC8)` is `0x096C`; the emulator's is
  `0x12D0`, exactly 2×. `DM(0x3FC7)` is half (`0x3600` against `0x6C00`).
- The card's caller setup block differs from ours:
  - `DM(0x3EE0..0x3EEC)`: `0040 008f 0038 · a000 · 2105 f1fd 000c 000c 00b8 · 0003`
    against `00c4 048c 0070 · 8000 · 0105 f0fd 0006 0006 00ff · 0000`.
  - `DM(0x3F04)` = `000c` against `0018`; `DM(0x3F0D)` = `0014` against `0003`.
- `Samplerate`/`Samplebuffersize` (`DM(0x3F66/0x3F67)` = 4/3, a 9600 Hz page)
  are the **same** on both, so the emulator's page rate is not the
  difference.

### Inside the `0x0095` success exit: a descrambled-ones counter

Sampling DM every line sample (`EICON_DM_SAMPLE`, passive, so it does not
disturb timing) at offset 8.50 (pass) and 8.49 (stick) gives this chain:

- The `0x0CF0` correlator ring and accumulator are frozen in both runs. It
  does not run in this phase.
- The inner machine walks `0x3f → 0x40 → 0x41` (records `0x16f8 → 0x1707 →
  0x1716`). Record `0x1716` has primary handler `0x29` = `PM 0x0A3D`
  (handler table `DM(0x064B)`; build 109-789):

  ```
  0a3d  CALL $09FB          ; receive step
  0a3e  AR = DM($103D)      ; bit count
  0a40  AF = 0x48 - AR      ; LE (advance) once 72 bits are in
  ```

- `DM(0x103E)` is a 12-bit window of **descrambled received bits**. Sd is
  scrambled binary ones, so a correct receiver sees `0x0FFF`.
- **Pass:** from 17.010 s the window reads `0xffff` and `DM(0x103D)` steps
  12 → 18 → … → 72 in 6-bit steps within 7 ms. The receiver then latches
  `DM(0x103B/0x103C) = ffff/c03f` and sets `DM(0x10DB) = 1`. Records
  `0x171f` (state `0x42`, `[6] = 0x0200`) and `0x1731` (state `0x43`,
  `[2] = 0x4010`, bit 14 of `DM(0x20EB)`) follow, and outer `0x0095 →
  0x00b0`.
- **Stick:** the window is almost all ones, with **single-bit errors in a
  fixed cycle** (`00fd 0c03 0330 06cc 079b 0e1e 0ff8 0fff 0eff 0ffb 0fff
  0f7f`). The cycle repeats every 9.0 ms, which is 72 line samples. The
  count never leaves 12, and `0x41` keeps falling back to `0x3f`.

### The pass depends on exact sample alignment

Fixed-offset prime of `caller.rx.ulaw`, offsets in line samples from 8.50 s.
All runs use `--seconds ≥ 24`; see the trap below.

| shift (samples) | 0 | +1 | +5 | +6 | +20 | +30 | −30 | +60 | ±80 | +160 |
|---|---|---|---|---|---|---|---|---|---|---|
| → `0x00b0` | **yes** | no | no | no | **yes** | no | no | no | no | no |

- There is no period-6 (V.90 data frame) or period-5 (8000:9600 resampler)
  pattern, so neither of those phases explains it.
- The recording is an exact 8 kHz stream with no drift. A receiver that
  acquired timing and phase on the 3 s of Sd in `0x0094` would not care
  where the recording starts. The emulated caller is sensitive to a single
  sample, so **its timing/phase acquisition on Sd is not converging**, and
  it decodes clean ones only on rare lucky alignments.
- The error pattern fits that reading. The real Sd segment
  (`caller.rx` 9.2–13.05 s) is periodic, with its autocorrelation peak at lag
  73. A fixed sampling-phase error on a periodic signal gives the same bit
  errors every period.

⚠ **Trap: `--seconds` is wall clock.** `eicon_loopback.py` SIGTERMs both ends
`--seconds` after the answerer starts. Caller call-time runs about 2 s behind
wall clock, so `--seconds 19` kills the call before 17.02 s, and the result
looks like "stuck at `0x0095`". Check the last `TrnProgress` timestamp
before calling a run stuck.

### Root cause: the analogue build's timing PLL (and the fix the card already ships)

**Locating the loop.** A PC histogram over `0x0094` at a passing and a
failing shift points to the timing interpolator at `PM 0x0DD0..0x0E33`:

- `DM(0x1025)` is a fractional phase, quantised in `0x0CCC` bins. Each bin
  sets the ring step and count for the read pointer `DM(0x0F7F)`, and a
  5-tap polyphase FIR (`PM 0x26B8`) does the interpolation.
- The phase is steered by a **second-order PLL at `PM 0x0E3A`**, enabled by
  bit 4 of `DM(0x20ED)`. Its error is `DM(0x10F4)`, its gains
  `DM(0x2143..0x2145)`, its integrator `DM(0x1022:0x1023)`, and its phase
  `DM(0x1024:0x1025)`.
- The PLL runs once per symbol all through `0x0094`. Passive samples show
  what it does at each alignment:
  - **shift 0 (passes):** the integrator **runs away and pegs at −2³¹**, so
    the phase slews through every alignment and briefly crosses a good one;
  - **shift +1 (sticks):** the integrator **settles** (`1023` ≈ 100–170) and
    the loop locks to a *wrong* sampling point. That is the fixed 72-sample
    bit-error pattern above.

**The card's build has no PLL there.** In the 117-926 V.90 APCM overlay
(download 619, the task the card ran), `PM 0x0E3A` is `RTS`. The three call
sites that reset the phase are the same as in 109-789, re-pointed to the
moved `0x0E3B`. The 109-789 overlay the emulator loads comes from the
analogue-card firmware set. An analogue codec clock is independent of the
network, so that build recovers timing. A BRI card's PCM is
network-synchronous, so 117-926 removes the loop and keeps the interpolator
at its fixed phase. The emulator's line is exactly synchronous too: the
loopback runs at 8 kHz and the recordings are 8 kHz. So 117-926 behaviour is
the right one for this rig.

**Fix, as a PM patch:** `EICON_PATCH_PM=0x0e3a:0x0a000f:0x026b` on the
caller.

| run (PLL off) | caller | answerer |
|---|---|---|
| primed, shifts 0 / +1 / +5 / +30 / +80 / +3200 | all leave `0x0095` (16.98 s at shift 0) and reach `0x00c0 → 0x00c1 → 0x00c3` | — |
| **unprimed loopback**, 45 s | `0092 0094 0095(0.78 s) 00b0 00b2 00b3 00b6 00c0 00c1(1.00 s) 00c3 00c4 00c6 00c8 00ca 00cd` **`00d0` @ 22.50 s**, held to end of call | `… 00b0 00b1 00b2 00b3 00b6 00c0 00c2 00c4 00c6 00c8 00ca 00cc` **`00d0` @ 20.80 s**, held |
| unprimed, 70 s, `--ppp` | same, `00d0` @ 22.50 s; held 27.5 s, then a fast retrain (`00c1..00cd`) back to `00d0` @ 52.84 s | same, `00d0` @ 20.80 s, back to `00d0` @ 50.80 s |

- Both ends show `CTS|DSR|DCD`, with **no state pins and no recording**.
- The caller's dwell times now match the card: `0x0094` 3.0 s (card 3.000),
  `0x0095` 0.78 s (0.783), `0x00c1` 1.00 s (1.001).
- The rate words almost match the card's connect dump:
  - caller `DATASTATEspeedTx` `0x0013` (card `0x0013`), `DATASTATESpeed`
    `0x21f1` (card `0x21f4`);
  - answerer `0x11f3` (card `0x11f3`), speedTx `0x2031` (card `0x2034`).
- Primed runs fall back to INFO after `0x00c3`. That is expected: from
  `0x00c0` on, the caller needs a peer that answers its own transmit, which
  a recording can't do.

This gets past the wall in `docs/analysis/08-v90-reliability-investigation.md`
§"Native firmware data-state boundary" ("caller `0x00c0`, answerer `0x00c2`
… neither endpoint enters data mode"). That section expected the fix in the
V90D response waveform. The cause is on the caller's side instead: a timing
loop the BRI firmware does not have.

**Not yet:** user data. With `--ppp`, V.42 attaches on both ends, but no
LAPM or PPP traffic appears in data mode. That is the next layer:
native-data-state → V.42.

### Setup database (tested, not the cause)

Loading all 14 of the card's setup words into the emulated caller
(`--caller-db-word 0x3ee0:0x0040,0x3ee1:0x008f,…,0x3f0d:0x0014`) leaves the
primed walk unchanged: it still parks at `0x0095`. A DM dump at `0x0095`
confirms the values held (the firmware itself rewrites GEN_setup2 to `0x0078`).

### User data over native V.90 (`tools/v34_mailbox.py`)

**Why nothing flowed.** The direct-backend host data interface served V.34
(`0x0261`) only. It returned on any other page, so in `0x00d0` V.42 never
saw a bit. It now serves both V.90 pages.

| page | TX | RX |
|---|---|---|
| V90D `0x026A` | `21 + (speedTx & 0x1f)` bits (speedTx bit 5), TXD0..2, LSB first as the shim does | V.34 13 bits, left aligned, flags `0x2000`/`0x4000` |
| V90A `0x026B` | V.34 13 bits, TXD0 MSB first | `21 + (speed & 0x1f)` bits (speed bit 13), left aligned across **RXD0, RXD1, `DM(0x3FB1)`**, flag `0x2000` |

Three V90A-specific facts had to be measured (109-789 page, analogue kernel):

1. **The kernel consumes wide receive datagrams itself.** `PM 0x0798..0x07CD`
   reads RXD0 and leaves bit 13 for the host when the width is < 16, which
   is why V.34 worked. When the width is ≥ 16 it reads all three words into
   its own ring and clears bit 13 (`AND $DFFF`, store at `0x07B7`) inside the
   same sample. `claim_wide_rx_mailbox()` NOPs that store by signature. The
   PRI kernel has the same code (`0x079F`) and gets the same patch.
2. **The downstream width is transient until `0x00d0`.** It reads `0x21e4`
   (25 bits) through `0xC6..0xCD`, then `0x21f1` (38). Starting LAPM at
   `0xC6` latched the wrong width and spent T400 on training, so the V90A
   arm starts at `0x00d0`.
3. **The upstream request is a scheduler, not a handshake.** `DM(0x1213)`
   counts symbols (4 per period) and `DM(0x1214)` the datagrams left
   (3 per period, from `DM(0x1212)`), so the page requests 2,400/s. Bit F stays
   set across the period:
   - waiting for F to clear supplied **800/s**;
   - self-acknowledging F supplied about 6,600/s.

   Each step of `DM(0x1214)` is one TXD0 read (`PM 0x3D84`), so the mailbox
   supplies on that step. Measured: 2,400/s.

**Result** (`EICON_V42_DETECT=0` on both ends; see below):

- The answerer's V.42 receives the caller's XID (26 bytes) and SABME and
  sends UA. The caller logs `LAPM connected (UA(F) received)`.
- PPP exchanges 1,135 bytes each way with no PPP FCS errors.
- **Upstream is clean**: 78 good frames, 0 bad FCS.
- **Downstream is not**: 65 good frames, 1,213 bad FCS. So LAPM REJects,
  the answerer's window fills, and LCP never completes.

Offline, `EICON_MAILBOX_RX_TRACE`/`_TX_TRACE` record every bit each end's
LAPM sent and received:

- Received downstream datagram *i* is sent datagram *i + 44*, for the whole
  call. There are no drops, duplicates or reordering, so the mailbox is
  correct.
- The **downstream BER is 12.9%**, flat over the 28 s. 59% of 38-bit
  datagrams are clean and the rest carry about 12 errors each. The errors
  are spread over all 38 bit positions.
- The same sent idle datagram arrives clean 59% of the time and otherwise
  shows varied errors. With scrambling before mapping, that is a codeword
  decision fault, systematic or noisy, not a framing one.
- None of these change the BER: receive resampler taps (16/32/64: 12.9%),
  resampler phase (0–5: 12.5–12.9%), or level. Lagrange breaks training.

**Start-up race.** In the emulator the answerer reaches `0x00d0` about 1.7 s
before the caller (on the card it was 126 ms *after*). The answerer's T400
(600 datagrams) therefore expires before the caller sends ODP, and with
detection on both ends fall back to non-error-corrected mode.
`EICON_V42_DETECT=0` skips detection. It is the test configuration here, not
a fix for that ordering.

**The retrain about 28 s into data mode** (both ends drop to
`0xC1..0xC4` and return at lower rates: caller RX 38 → 34 bits, answerer
RX 13 → 12 → 13) fits the caller's quality monitor reacting to that BER.

### The card's own build on the caller (`EICON_OVERLAY_FROM`)

`EICON_OVERLAY_FROM=0x026b=artifacts/eicon-dsp/overlays/026b-v.90-apcm-overlay`
(new, `dial_tikrnl_drive._index_overlays`) serves the 117-926 V.90 APCM page
(download 619, the task the card ran) under the analog109 kernel.

- The early walk then matches the card more closely:
  `0050 0054 0060 0062 0064 0070 0071 0072 0073 0075 0092`. `0x0062` and
  `0x0075` are on the card's walk, and 109-789 skips them.
- But it stalls at `0x0092`, and the answerer falls back to INFO from
  `0x0060` at 12.3 s.
- Running the whole caller on `pri117` (kernel, TIKRNL and page) doesn't
  originate at all (`0x0000`); the PRI direct backend has never originated
  a call.

The card ran this page under its **BRI 2M kernel 117-926**. That kernel is in
`dspdload.bin` on eicon420 but not yet extracted here, and a faithful 117-926
caller needs it.

### The retrain is answerer-initiated, on a ~28 s clock

From the per-frame CSV (`retrain_reason`/`retrain_controller` stay 0 on
V.90):

- The answerer leaves `0x00d0` first: `0x00c2` at 49.02 s, then a fast
  `0xC2↔0xC4` loop.
- The caller follows 1.8 s later, `0x00c1` at 50.80 s.
- Across runs the answerer's retrain comes **27.5–28.2 s after its own
  `0x00d0`**, with or without the V.90 data path running, and the 45 s run
  ended before reaching it.

The answerer can't see the caller's downstream errors, and its own upstream
decodes with 0 bad FCS. So this is **not** the downstream BER. It points to
a V90D-side timer or slow drift, a candidate for the `EICON_V90D_TX_BLOCK_HOLD`
/ mapping-frame interventions, which run continuously in data mode.

### Downstream BER root cause: the direct backend ran V.90D as A-law on a μ-law line

**Locating it.**

- The card's data-mode downstream uses 64 Ucodes (4–98), each used
  uniformly in all six frame slots.
- The emulated V90D used 48, in tiers:
  - ~1,060 counts on codes 48/49, where two labels merge onto one code;
  - ~250 on `2/3, 6/7, 10/11, 14/15`, where one label splits across
    neighbouring codes;
  - ~520 on the rest.
- The transmitted μ-law is the G.711 encoding (×1, exact at a 16,000-sample
  ring offset) of the page's linear output `DM(0x3FB4)`. That output is a
  uniform ladder, 18 + 32k: the page's level table was the wrong law, so the
  μ-law encoder rounded every off-grid level onto a neighbouring code.
- The caller learned those same wrong levels in training, so the two ends
  partly agreed: 59% of frames survived, and BER was 12.9%.
  - Restoring the μ-law Ucode table mid-call (`EICON_V90D_PCMU_UCODE_TABLE`
    with `_AFTER_STATE` at `0xC6`/`0xD0`) raised BER to 47–50%.
  - Restoring it at boot broke training.

**The bit.** The answerer's `Info0D_setup` (`DM(0x3F5B)`) read `0x03F7`, with
bit 6 `PCMcoding` = **A-law**; the card's read `0x0337`. TIKRNL writes it
every frame at `PM 0x066C..0x0670`:

```
0662  CALL $001E -> 0272: I0 = DM(0x2F27)+1; AR = DM(I0) - 0x3C27
      EQ: mu-law (DM 0x31B6=0x2000, 0x31B7=2, bit 6 clear)
      NE: A-law  (DM 0x31B6=0x1000, 0x31B7=3, bit 6 set)
```

`DM(0x2F27)` points at `0x2F21`, so the word is `DM(0x2F22)`. Kernel init
leaves the A-law `0x3C07`. The native shim's `attach_connected_bearer()` sets
`0x3C27` for PCMU, but the direct backend's `configure_g711_law()` set only
the encoder table `DM(0x3309)`. It now sets `DM(0x2F22)` as well.

**Result**, unprimed loopback with PLL patch, `EICON_V42_DETECT=0`, `--ppp
--ppp-ping peer`:

- `Info0D_setup` reads `0x03b7` (μ-law). Downstream negotiates `0x21e8` =
  29 bits = **38,666 bit/s**.
- **BER 0.15%**, 99.8% of datagrams clean (was 12.9%).
- LAPM connects; LCP up, CHAP authenticated, IPCP up (100.64.0.2 ↔
  100.64.0.1); **8/8 pings answered in 456–464 ms**; 0 PPP FCS errors.
- The answerer-initiated retrain still comes at about 27.5 s into data mode.
  It takes the link down; LCP/IPCP go down.

**Without the PLL patch**, the law fix alone lets the caller leave `0x0095`
(after 1.1 s, which never happened before), but both ends then stall at
`0x00b0`. Both fixes are needed.

### Next

- The answerer-initiated retrain about 28 s into data mode: it now takes down
  a working PPP link, so it is the top item.
- The downstream rate is 38,666 against the card's 54,666, and 0.15% BER
  remains. Compare the caller's DIL/CP choices against the card's.
- The V.42 start-up race (`EICON_V42_DETECT=0` is still needed).
- `EICON_V90D_PCMU_UCODE_TABLE` treated a symptom of the same bit. It should
  be redundant now; retire it once that is confirmed.
