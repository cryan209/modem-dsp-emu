# The receive path does not flatten ANSam; the codec was clocked at 8000

**This file previously claimed the opposite.** It reported the 15 Hz envelope at
0.182 on the wire and 0.007 at `DM(0x0772)` and concluded that this project's
receive path — RTP, `analog_line`, the resampler, SPORT1, the RXSAMPLE fill —
destroyed it. That conclusion was wrong, and the way it was wrong is worth
keeping: **the two numbers were measured over different windows.** The wire
figure came from the ANSam window; the `DM(0x0772)` figure came from the whole
call, which in `artifacts/loopback/analog-kernel` is 35 s of which ANSam is
4.5 s and plain 2100 Hz ANS (depth 0.019) is most of the rest. Dividing them
produced a factor of 18 that never existed.

## What the receive path actually delivers

Replaying that same capture's `caller.rx.ulaw` through `AnalogKernelModem` and
reading `DM(0x0772)` per second — the same signal, the same backend, the two
depths on the *same* window — gives:

```text
        wire   DM(0x0772)
19.0s   0.179    0.208
20.0s   0.207    0.218
21.0s   0.208    0.218
22.0s   0.207    0.140     <- ANSam ends mid-window at 23.5s
24.0s   0.019    0.019     <- plain ANS from here on
```

The envelope arrives intact. RTP, `analog_line`, the 8000→9600
`RationalResampler` and the kernel's RXSAMPLE fill are all exonerated; fed a
synthetic ANSam the resampler alone reproduces 0.192 in, 0.194 out.

## What was actually wrong: `--analog-codec-rate 8000`

The codec rate was the defect, and it was in plain sight — the flag's own help
text has always said V.8 asks for 9600 (`Samplerate` code 4) and that 8000
emits every tone at 5/6. The default was 8000 anyway. Replaying the same
capture at each rate, watching the envelope detector's own words:

```text
codec 8000: DM(0x0777)=0     DM(0x0778)=0    page stays 6 (V.8)
codec 9600: DM(0x0777)=659   DM(0x0778)=813  page 6 -> 7 (INFO) at 22.6s
```

`DM(0x0778)` needs 240. At 8000 it never leaves zero — not because the envelope
is missing but because the whole detector chain, biquad included, runs 5/6
slow, so its 14.4 Hz passband sits at 12 Hz and ANSam's 15 Hz falls outside it.
At 9600 it counts up and V.8 completes.

The live runs already said so and it went unread: `artifacts/loopback/analog-9600`
reaches `bootpage 6 V.8 -> 7 INFO` at 20.25 s, and `artifacts/loopback/analog-kernel`,
identical but for the rate, never leaves page 6.

## The fix

The default is now 9600 in all three places that carry one —
`tools/eicon_adsp_sip.py --analog-codec-rate`, `tools/eicon_loopback.py
--analog-codec-rate`, and `AnalogKernelModem(codec_rate=...)`. Passing 8000
still reproduces the old behaviour. A fresh loopback on the new default
(`--firmware-set analog109 --caller-kernel-dispatch --answerer-kernel-dispatch`)
takes V.8 to INFO at 3.67 s and TrnProgress to 0x002a.

## Method notes, since two measurements here were misread

- **Compare depths over the same window, and print the window.** The whole
  error above is one number from a 4.5 s window against one from a 35 s window.
- **A heavy write-watch can kill the call.** One run watching three addresses
  at 300,000 each went host-bound and the caller received silence — `rx.wav`
  rms 0. `artifacts/loopback/settle2/caller.rx.ulaw` is still that silence.
  Check rms before interpreting anything downstream.
- **The envelope estimator was validated against a known answer.** Moving-RMS
  reports 0.182 on the wire where complex demodulation reports 0.179.
- Rates from log timestamps are unreliable; use ratios of write counts. The
  ratio work in the previous version of this file stands: inner loop / outer
  block = 4.00, detector / inner loop = 0.997, biquad / detector = 1/15, so the
  biquad runs at codec/15 — 640 Hz only if the codec is 9600.
