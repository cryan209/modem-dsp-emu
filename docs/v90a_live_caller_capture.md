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
