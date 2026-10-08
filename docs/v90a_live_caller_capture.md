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

### Setup database (tested, not the cause)

Loading all 14 of the card's setup words into the emulated caller
(`--caller-db-word 0x3ee0:0x0040,0x3ee1:0x008f,…,0x3f0d:0x0014`) leaves the
primed walk unchanged: it still parks at `0x0095`. A DM dump at `0x0095`
confirms the values held (the firmware itself rewrites GEN_setup2 to `0x0078`).

### Next

- Find out what makes the `0x0CF0` correlator fire at offset 8.50 and not at
  8.49. Log `DM(0x10F3)` with `EICON_EVENT_LOG`, not an exec watch, across
  the two runs and diff them sample by sample. Then work out which part of
  the card's acquisition (timing recovery or segment sync) the emulated
  caller is missing.
