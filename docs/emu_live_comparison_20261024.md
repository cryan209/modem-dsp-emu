# Fresh V.90 hardware and emulator comparison, 24 October 2026

Eicon controller 2, ttyds6, called MultiTech 7910 through Cisco with
`AT&F5`, `AT+iQ=o2`, `AT+iO7901`, `AT+MS=V90a,1`. CONNECT 56000
arrived 23.168 seconds after dialling. Card status confirmed 31,200 TX /
56,000 RX; MultiTech sent PPP LCP requests. This was a handshake test,
not a payload-integrity test. DTR was dropped at 28.184 seconds.

A bounded `mlog -c 2 -o -A3 -#3 -l2048` capture began before dialling.
The test used B2, yielding 227,240 samples (28.405 seconds) per direction.
B1 belonged to a pre-existing incoming call and is labelled `unrelated-b1`;
it is not the test's answerer audio. The initial controller-2 self-call
attempt aborted at the idle check before dialling because that call was
active. The subsequent MultiTech test used the remaining bearer.

Evidence lives in `artifacts/emu-diff-handshake-20261024/`: raw mlog,
`dial-mt.log`, caller RX/TX mu-law, both replay logs and `comparison.json`.
Hardware TRAINPROGRESS events are selected by `[P6,2]`, matching the
test TTY; the JSON retains raw trace times, not an assumed audio alignment.

## Initial results (superseded by the codec correction below)

Both replays consumed the complete, unchanged caller RX stream through
`analog_kernel_dispatch.py`, role calling, 9,600-Hz codec / 8,000-Hz bearer,
with the normal sinc converters. Neither run forced state transitions.

| Execution | Result |
|---|---|
| Physical BRI 117-926 | `0092 -> 0094 -> 00b0 ... 00c0 -> 00c1 ... 00d0` |
| Normal Analog 109-789 harness | Reached `00c0` at sample 173,936 (21.742 s); no later state through capture end |
| Analog harness with 117-926 APCM overlay override | Reached `0092` at sample 105,733 (13.217 s); no later state through capture end |

On hardware, `0092` lasted 0.796 s, `0094` 3.001 s, `00b3` 3.029 s
and `00c1` 1.000 s. The normal Analog replay passed `0092` and `0094`,
but `00b3` lasted 4.131 s before reaching `00b6`. It did not publish
`0095` in this capture. The saved JSON includes all first-seen emulator
states and the full hardware state-event sequence.

The override was:

```sh
EICON_OVERLAY_FROM=0x026b=artifacts/eicon-dsp/overlays/026b-v.90-apcm-overlay
```

This repeats the previously documented sensitivity to the firmware/kernel
combination. The override is a mixed BRI-overlay/Analog-kernel diagnostic,
not a faithful BRI machine. The normal run also uses a different firmware
family from hardware. Neither result isolates an instruction-semantics bug.
Open-loop playback cannot follow a different response from the emulated
caller, so later stalls alone do not establish the cause. Audio continuity
has not been independently proved against a peer-side recording.

Next comparison should reproduce the BRI 117-926 kernel, task initialization
and sample cadence before attributing a divergence to the CPU. The normal
Analog replay's `00b3` dwell and `00c0` gate are concrete trace targets;
the earlier `0092` stall is specific to the mixed-overlay run here.

Cleanup: tracing ended, ttyds6 had no owner, both controller-2 bearers were
idle at final verification, and bri-test-answerer, diva-sip-gateway and
multitech-ppp remained active. No persistent configuration changes or
service restarts were made.

## Corrected codec and cadence comparison

The initial splitter documentation incorrectly assumed every mlog audio
record was mu-law. The fresh capture's quiet interval at samples
40,000..47,999 contains 7,890 occurrences of `d5` out of 8,000 samples:
the A-law positive silence code. Decoding that code as mu-law produces
+716 PCM16 rather than the correct A-law +8. The earlier `.ulaw` suffix
was a label, not a verified encoding. Treat the initial comparisons above
as invalid codec controls, not evidence of a CPU defect.

The splitter now accepts `--law pcma`, which labels unchanged bytes `.alaw`.
Correctly labelled files are under `artifacts/emu-diff-handshake-20261024/alaw/`;
their caller RX bytes are identical to the original. B1 there remains the
unrelated call despite the splitter's generic `answerer` name.

`tools/eicon_live_caller_replay.py` requires explicit `--law` and records
state changes with bearer sample indices, codec switch point, input SHA-256,
served overlay paths and descriptions, and optional hardware dwell comparisons.
The tool does not force state transitions. Its native-rate switch is an
explicit experiment, not a production clock policy: it bypasses converters
from the next bearer sample after APCM loads and discards their filter tail.

| A-law replay | Result |
|---|---|
| Analog 109-789 APCM, 9,600 Hz | Passed `0092`, reached `00ca`, then fell back to INFO |
| Analog 109-789 APCM, switch to 8,000 Hz | Stalled at `0092` |
| BRI 117-926 APCM override, 9,600 Hz | Stalled at `0092` |
| BRI 117-926 APCM override, switch to 8,000 Hz | **Reached `00d0` at sample 176,789 (22.098625 s), retained through capture end** |

The successful run still uses the Analog 109-789 kernel, V.8 and INFO,
then switches clock cadence for the BRI 117-926 V.90 overlay. It is not
a complete BRI emulator. The mixed-overlay run's earlier `0092` stall
was sensitive to the 20% clock mismatch; it cannot be attributed to CPU
semantics from the initial test.

Hardware and successful replay dwell times, compared without an assumed
absolute audio/log time alignment:

| State -> successor | Hardware | Replay | Replay minus hardware |
|---|---:|---:|---:|
| `0092 -> 0094` | 0.796 s | 0.820625 s | +24.625 ms |
| `0094 -> 00b0` | 3.001 s | 3.000 s | -1.000 ms |
| `00b3 -> 00b6` | 3.029 s | 3.028125 s | -0.875 ms |
| `00c0 -> 00c1` | 0.268 s | 0.267500 s | -0.500 ms |
| `00c1 -> 00c3` | 1.000 s | 1.000 s | 0.000 ms |

Not every intermediate state matches: hardware publishes `00c6` and the
replay skips that visible state. At `00d0`, emulator speed words are
`speedTx=0013`, `speed=21f3`. Host status mirrors `3fa3/3fa4` remain zero;
no emulated host CONNECT or payload integrity is established. The physical
call's 23.168-second CONNECT timer and replay's audio-relative 22.098625
seconds have different origins and must not be subtracted as a timing error.

Reproduce the successful comparison from the repository root:

```sh
EICON_OVERLAY_FROM=0x026b=artifacts/eicon-dsp/overlays/026b-v.90-apcm-overlay \
  /Users/scottcryan/v90modem/.venv/bin/python tools/eicon_live_caller_replay.py \
  artifacts/emu-diff-handshake-20261024/alaw/caller.rx.alaw --law pcma \
  --apcm-native-rate \
  --hardware-trace artifacts/emu-diff-handshake-20261024/mlog2-mt.txt \
  --out artifacts/emu-diff-handshake-20261024/corrected-comparison.json
```

## BRI kernel bootstrap investigation

The BRI kernel had already been extracted locally; the older missing-kernel
note was stale. Card type 53 selects the same TIKRNL81.F34 task used by
the PRI harness (download 600), plus BRI kernel download 6. A standalone
probe boots the unmodified BRI kernel to IDLE at `028b`, delivers SPORT1
frames, and lets its foreground initialize its own ring pointers. The
second channel command ring at `2ee1` accepts the task entry `0672` and
registers continuation `06fc` at `02b0` plus ISR continuation `08f6` at
`0072`. These differ from the PRI/Analog layouts.

The provisional packed-word RX transport has not passed V.8 with the fresh
recording, even with internal SPORT companding enabled and both byte
placements tested. Boot/registration therefore works, but this probe is
not yet a qualified bearer model. Its scripts and logs are saved beside
the audio artifacts; no emulator-core semantics were changed to make it pass.

## Matching BRI kernel replay succeeds

The provisional transport issues above were resolved in this session.
Kernel counter `2df3` cycles through 3, 2, 1, 0, so its main receive path
runs once per **four** SPORT1 words, not three. Its internal SPORT0
compander must also be enabled: selected BRI descriptor `2f03=3c37`
selects A-law expansion (dtype 3); `3c07` leaves companding disabled.
This is host/channel setup in the experimental harness, not an instruction
or DSP state-machine patch. Whether the stock host uses precisely the same
descriptor control word has not been directly captured.

`tools/eicon_bri_caller_replay.py` now boots the actual 117-926 BRI kernel,
the card-type-53 TIKRNL81.F34 task and card-type-53 overlays, with four
SPORT1 words per 8 kHz sample. The unused first channel receives A-law
silence; the test stream occupies the second channel's low octet. No
resampling or firmware override is used. The kernel handles its real
ISR, command ring and task continuations. The host answers download
doorbells and supplies the initial APCM calling database.

**The BRI replay reaches `00d0` at sample 176,792 (22.099 seconds),**
including hardware's `00c3 -> 00c4 -> 00c6 -> 00c8` sequence. Its
speed words are `speedTx=0013` (V.34 index 19, 31,200 bit/s) and
`speed=21f5` (V.90 index 21, 42 bits per 6 samples = 56,000 bit/s),
matching the physical call's negotiated directional rates. Raw
`Rstatus_ch=8700`, `Rstatus=0402` are present at entry to data mode.

| State -> successor | Hardware | BRI replay | Difference |
|---|---:|---:|---:|
| `0092 -> 0094` | 0.796 s | 0.820 s | +24.000 ms |
| `0094 -> 00b0` | 3.001 s | 3.000 s | -1.000 ms |
| `00b3 -> 00b6` | 3.029 s | 3.028125 s | -0.875 ms |
| `00c0 -> 00c1` | 0.268 s | 0.267500 s | -0.500 ms |
| `00c1 -> 00c3` | 1.000 s | 1.000 s | 0.000 ms |
| `00c3 -> 00c4` | 0.045 s | 0.045625 s | +0.625 ms |
| `00c4 -> 00c6` | 0.045 s | 0.045625 s | +0.625 ms |
| `00c6 -> 00c8` | 0.046 s | 0.045625 s | -0.375 ms |

The replay stays in data until 28.310 seconds, then falls back to INFO
near the end of the recording. The physical call was deliberately hung
up, and hardware records a later `00c1` event too. This tail is not evidence
of a stable live data link or of payload correctness.

Reproduce with no overlay override:

```sh
python3 tools/eicon_dsp_extract.py docs/firmware/dspdload.bin \
  --card-type 53 --match Overlay -o artifacts/eicon-dsp/build-117-926-bri/overlays
/Users/scottcryan/v90modem/.venv/bin/python tools/eicon_bri_caller_replay.py \
  artifacts/emu-diff-handshake-20261024/alaw/caller.rx.alaw --law pcma \
  --hardware-trace artifacts/emu-diff-handshake-20261024/mlog2-mt.txt \
  --out artifacts/emu-diff-handshake-20261024/bri-comparison.json
```

`bri-comparison.json` records the input hash, all observed emulator/hardware
states, speed/status words and dwell comparisons; `bri-comparison.log`
records the complete run. The saved initial BRI probes remain useful
negative controls but are superseded by this runner.

Validation covers all 256 A-law codes with different octets on the two
channels. Sampling the queue at ISR exit, before foreground overwrites it
with TX, returns the expected signed native SPORT expansion on each
channel and exactly one sample pair per four words. A separate test boots
and registers the real task and reaches V.8 without state pins. The
splitter tests ensure law labels preserve exact channel bytes; the dwell
tests exclude unrelated tasks, unmatched successors and truncated visits.
All 23 focused Python tests and the C core tests pass; `git diff --check`
passes.

Remaining work: qualify the full IOM control stream and stock channel
initialization, account for the 24 ms `0092` dwell difference, and test
RX2400/IDI status publication and bidirectional payload with a responding
peer. This session establishes a successful hardware-audio handshake
replay with matching BRI firmware, not a complete live modem connection.
