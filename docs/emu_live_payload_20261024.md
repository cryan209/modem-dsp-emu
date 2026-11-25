# Physical modem to emulator: live payload test, 24 October 2026

Current result: **PASS on the matching BRI kernel backend**: a physical
Eicon modem connected to the emulator and exchanged 4,096 deterministic
bytes in each direction with exact matches. The original native-MIPS
attempts below failed; the successful follow-up is documented afterward.

The new BRI replay runner has no live duplex transport yet. This test used
the existing native-MIPS SIP/V.42 backend as a separate live control, not
the new BRI replay runner. It therefore does not directly qualify the new
BRI backend's transmit or payload path.

Path: physical Eicon ttyds6/controller 2 -> Cisco -> Asterisk -> emulator
SIP account 6001 on the Mac. Endpoint 6001 was unavailable with no contact
before the test. SIP/RTP used temporary ports 15060/14000. PCMU was selected
by the SIP endpoint; no switch or persistent service configuration changed.

Physical caller setup: `AT&F5`, `ATE0V1`, `AT+iQ=o2`, `AT+iO7901`,
`AT+MS=V90a,1`, `AT\N3%C0`, `ATD6001`. Compression was disabled for
the deterministic payload check. Test TTY ownership was checked first.

## Attempts

1. Native-MIPS endpoint with `--native-bearer-activation`,
   `--force-info-after-v8`, `--tx-v42`, `--v42-pty`.
   SIP INVITE arrived, but endpoint construction failed before SIP answer
   and media with `RuntimeError: native TIKRNL did not consume answer WDB`.
   Just before the error, a page request `0651` was unserved at sample 700,
   resident DIAL `0262`, bootpage `004d`. The physical caller returned
   NO CARRIER at 34.352 seconds. No media/payload reached the emulator.

2. Same endpoint without `--native-bearer-activation` (compatibility setup).
   Answer setup completed and V.8 was resident. SIP answered, audio flowed,
   and the emulator processed 445,600 samples / 2,777 RTP packets.
   TRAINPROGRESS stayed `0000`. Physical caller returned NO CARRIER at
   60.051 seconds. LAPM stayed down: zero links, XID, SABME, HDLC frames,
   and accepted/requested TX datagrams. No payload transfer was established.

The second call's media totals reported zero substituted/dropped RX samples,
zero truncated frames and zero TX underruns. RX audio level was about
-17 to -18 dBm0. Those observations show media availability, not successful
tone detection or training. They do not exclude a media-content or DSP
setup problem.

The deterministic probe was prepared to send 4,096 SHAKE256 bytes each way
after CONNECT, using seeds `hardware-to-emulator-20261024` and
`emulator-to-hardware-20261024`. Neither hardware call reached CONNECT.
The local PTY helper also ended with EIO and did not produce a result file;
no successful PTY write or payload match is claimed. LAPM counters independently
establish that no link or payload transfer occurred.

## Evidence and cleanup

All captures/logs and the staged hardware probe are under
`artifacts/emu-live-payload-20261024/`. Main files:

- `native-answer.log`: first attempt's setup error and traceback.
- `compat-answer.log`: second attempt's state and final media/LAPM totals.
- `compat-answer.rtp.pcap`, RX/TX G.711/WAV and ADSP snapshots.
- `emu-live-payload-20261024.log`, `emu-live-payload-compat.log`: physical
  caller results and explicit DTR/TTY cleanup.
- `hardware_probe.py`: bounded physical test with post-CONNECT payload check.

Both physical test calls dropped DTR and closed ttyds6. The test emulator
was stopped gracefully and deregistered 6001; Asterisk confirmed no contact
for 6001 and no remaining test call. ttyds6 had no owner afterward.
bri-test-answerer, diva-sip-gateway and multitech-ppp remained active.
Other calls were present at final inspection and were left alone.

## Fixed BRI duplex path and successful live retest

`--bri-kernel-dispatch` in `eicon_adsp_sip.py` now exposes the matching
BRI 117-926 kernel as a duplex SIP backend. It uses the BRI kernel's real
SPORT1 ISR, command ring and task registration, four serial words per
8 kHz sample, a silent first channel, and the selected second channel.
The appropriate native SPORT0 compander is enabled per channel. The
main IOM word's low TX octet is decoded to PCM16 for the SIP media adapter.
The kernel's 128-word/two-channel shared ring gives TX a 64-frame (8 ms)
delay. A signed constant-producer test qualifies its TX output against
G.711 reference encoding across both signs, zero and rail values.

The SIP endpoint attaches the existing synchronous V.42 host mailbox to
the BRI task. Its explicit host ownership suppresses the task's competing
TX stores, just as the existing direct/Analog V.42 backends do. No training
state pins, post-V.8 INFO forcing, prerecorded TX or reactive modem engine
was used. This bypasses the failing native-MIPS answer-setup synthesis;
it does **not** repair that separate legacy native-MIPS setup error.

Two physical Eicon -> emulator calls reached V.90 `00d0`, decoded ODP,
exchanged XID and SABME/UA, and established LAPM. Both physical calls
reported `CONNECT 38666`; the DSP words `speedTx=2028`, `speed=11f3`
identify 38,666.7 bit/s emulator -> hardware and 31,200 bit/s reverse.
Do not substitute the earlier replay-only 56,000 rate for this live result.

The first call verified the 4,096-byte downstream payload, and PTY totals
showed 4,096 bytes each way. Its local verifier attached late and called
`tty.setraw()` with input flushing, so it could not verify the upstream
bytes. That result was not counted as a two-way integrity pass.

For the repeat, the PTY verifier was armed **before dialling** and retained
the raw mode already configured by PtyLink, without flushing input:

| Direction | Bytes | Verification |
|---|---:|---|
| Physical Eicon -> BRI emulator | 4,096 | Exact SHAKE256 payload match; SHA256 `c6699f00e4344f61dbc2e7bffd133762c5edb4bab8463afc7005e775bcfa5ece` |
| BRI emulator -> physical Eicon | 4,096 | Exact SHAKE256 payload match at physical TTY |

Physical CONNECT arrived at 23.700 seconds. The physical probe completed
its fixed receive window and hung up at 38.749 seconds. That window is not
a throughput measurement. The peer's DISC after DTR hangup is normal
test cleanup, not a spontaneous link failure.

New evidence in the same artifact directory:

- `bri-verified.log` and corresponding RTP/G.711/WAV/ADSP captures.
- `verified-pty-result.json` and `verified-pty-rx.bin` with the upstream
  byte-for-byte/hash result.
- `verified-summary.json`: independently reconstructs downstream bytes from
  the physical log and compares both directions with their expected payloads.
- `emu-live-payload-bri-verified.log` from the physical probe, including
  downstream equality and the final PASS result.
- `hardware_probe.py`, `verify_pty.py`, `run_bri_endpoint.py` for this
  bounded test setup. The older `bri-answer` files preserve the first pass.

Use `./run bri-v42 --run <name>` with the existing lab SIP credentials to
run this backend. Extract the card-type-53 BRI kernel/overlays as described
in `emu_live_comparison_20261024.md`; it shares the verified TIKRNL81.F34
task with the PRI extraction. The profile selects PCMU and test ports
15060/14000. `--bri-kernel-dispatch` rejects simultaneous native-MIPS,
other kernel-dispatch and incompatible firmware-set selections.

The live peer was an Eicon caller on the Cisco path; MultiTech was not
the peer in these passes. Longer transfers, sustained soak, compression,
PPP and the complete stock IOM control stream remain unqualified by these
initial passes. Subsequent long-transfer tests and the LAPM recovery fix are
tracked in [BRI reliability qualification](emu_bri_reliability_20261024.md).

Final regression validation: 171 focused Python tests pass, covering SIP/media,
mailboxes, V.42, BRI and Analog dispatch, resampling and trace parsing. C core
tests, Python compilation and `git diff --check` pass. Final cleanup confirmed
no owner of ttyds6, no SIP contact for 6001 and no test calls. All three
existing modem services remained active. The unsuccessful native-MIPS probes
were reverted; no CPU instruction-semantics changes were needed for the BRI
live pass.
