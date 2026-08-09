# Eicon/Dialogic ADSP V.34/V.90 firmware analysis — index

The running log, 213 sessions, split into six volumes under
[`analysis/`](analysis/). This file is the way in: a subject index first, then
every entry in order.

**Read [`handoff.md`](handoff.md) before this.** It is the current picture and
carries the list of what has already been disproved. This log is the record of
*how* each thing was established, and is where the addresses, traces and
measurements live.

**Search this index before opening a volume.** Session 194 spent five sessions
re-deriving a result that had been sitting in volume 02 since the unnumbered
23–30 stretch (commit `6788c56`, not "Session 25" — see that stretch's note) — the
V.90 page decision, at the same PM addresses, with the same Recommendation
citation. The log was complete; it was just 23,000 lines with no way in. That is
what this file is for. If you are about to go looking for something in the
firmware, look for it here first.

---

## Subject index

Where each recurring thing was established. Later entries supersede earlier ones
where they overlap.

### The V.90 / V.34 page decision

- **[The real V90D load path is INFO1a mode 6 → bootpage 14](analysis/02-native-tower-v8-and-info.md#the-real-v90d-load-path-is-info1a-mode-6---bootpage-14)** — `PM 0x3304..0x3310` disassembled, `DM(0x3fbb) & 0x0070 == 0x0060` → `DM(0x16b6)` = 14, `PM 0x2176..0x217f` → `DM(0x3fb0)`, then TIKRNL's table at `DM(0x31d5)` → overlay `0x026a`. Cites V.90 §9.2.1.1.8. **This is the decision; everything in volume 06 is downstream of it.** Commit `6788c56`, in the unnumbered 23–30 stretch; the older "~Session 25" dating is not supported by the commit order.
- [Host-bit audit: V90D is enabled; the remaining selector is received INFO1a](analysis/02-native-tower-v8-and-info.md#host-bit-audit-v90d-is-enabled-the-remaining-selector-is-received-info1a) — the write-database side of the same question.
- [Session 193](analysis/06-v90-page-decision.md) — the provenance *above* `DM(0x3FBB)`: bits 9..11 of `DM(0x060B)`, deserialised and bit-reversed off the line at `PM 0x358e..0x3599`. Plus the forced-`DM(0x16B6)` A/B.
- [Session 194](analysis/06-v90-page-decision.md) — the two peers' INFO1a decoded off the captures: Courier 6, Conexant 4 and 5. Tables 10 and 11/V.90.
- [Session 203](analysis/06-v90-page-decision.md) — **nothing is missing.** The per-rate array is full and correct; the *enable mask* at `DM(0x0f8b..0x0f90)` is zeroed by straight-line firmware at `PM 0x3915..0x391a`, and only the 3200 re-enable fired. Disable-then-selectively-re-enable.
- [Session 202](analysis/06-v90-page-decision.md) — budget hypothesis dead with a positive control; the rate is loaded from a **second array at `DM(0x0f71..)` that is mostly zeros**, and `0x0c-2=10`, `0x0f-2=13`, `0-2=clamped` accounts for every number in the report. The arithmetic is correct; the input is unpopulated.
- [Session 201](analysis/06-v90-page-decision.md) — the rate is `AX0 - (DM(0x0DFF) - 14)` clamped at 0, and **four of the six symbol rates produce exactly −2** — one missing quantity, not six bad measurements. Not any write of ours; the harness's per-frame budget is still a candidate.
- [Session 200](analysis/06-v90-page-decision.md) — **`EICON_PIN_DM`**, the DM twin of the PM pin, because FORCE_DM cannot reach a word written and read in one frame. With it: the projected rate does **not** derive from the measured words; the high-carrier bit does and works.
- [Session 199](analysis/06-v90-page-decision.md) — the probe *is* measured and *does* differ per call (`DM(0x0f6d..0x0f72)`); the conversion at `PM 0x3e63..0x3e7d` flattens a 2% spread into one fixed report. Full assembly chain from `DM(0x142f)` to the wire.
- [Session 198](analysis/06-v90-page-decision.md) — **our INFO1d probing results are a constant** across three calls and two modems, and declare 3200 usable at 31200 bit/s while declaring 2743/2800/3000 unusable. The 150 Hz probe is present in the received audio, so the input is not starved.
- [Session 197](analysis/06-v90-page-decision.md) — the media transits Asterisk, and VG224 ports 2/3 and 2/5 are **different pjsip endpoints** (8403 / 8405) with independently-written config — 8403 was broken and hand-repaired in Session 190. A leg with any DSP attached is not PCM-transparent.
- [Session 196](analysis/06-v90-page-decision.md) — `AT#UD` decoded against Microsoft's Unimodem spec (`tools/unimodem_ud.py`). It defines no V.90 parameters and this modem reports none of the useful optional fields, so it cannot say why PCM was declined — but its carrier and symbol-rate keys corroborate the INFO1a decode.
- [Session 195](analysis/06-v90-page-decision.md) — the control: an independent spandsp V.90 server reads the same 4 from the same modem on the same port, so the rig is exonerated. The Conexant **offers V.90 in V.8** and declines it in INFO1a, i.e. after line probing — the decision is a measurement, not a policy.

### Selecting a modulation

- **`DM(0x3FC4)` is the selector** — Session 184, volume 05. `0x0016`→V.22, `0x6000`→V.32, `0x0029`→FSK, `0x0040`→page 17, `0x0E00`→FAX, `0x0080`→page 20, default→INFO/V.34. The V.8 classifier is `PM 0x3ba1..0x3bfb`.
- **The CAI does not select one** — Session 183, volume 05. Nor does NORM_L. `AT+MS` on the *calling* modem does.
- **The write database base is `DM 0x3EE0`, not `0x3EE4`** — Session 181, volume 05. Any `+0xNN` offset resolved through `0x3EE4` since Session 100 is off by four.

### The overlay / bootpage mechanism

- The request words `DM(0x3131)`/`DM(0x3132)`/`DM(0x3FB0)`, and the kernel poster at `PM 0x069a/0x069b` — volume 06, Session 193.
- The partial overlay (`0x0267`) and why it must be served inside the requesting frame — Sessions 185 and 188e, volume 05.
- The descriptor-driven unpacker at `PM 0x1F82`/`0x1fbb`, and PM staged from resident PM — Sessions 188m, 188w, volume 05.
- Self-modifying program memory, first confirmed instance — Session 188l, volume 05.

### The INFO control channel

- Framing (fill ones, 10-bit sync `0x372`, payload, CRC-16 reflected `0x8408`) and the packer at `PM 0x358E` — Sessions 102–104, volume 03.
- **`tools/v34_info.py`**, the independent CRC-validated demodulator — Session 114, volume 04. Card transmits at 1200 Hz, peer at 2400 Hz.
- The peer really does send zeros in payload bits 6..12 — Session 114. Do not re-derive.

### The echo canceller and the bulk delay

- The bulk-delay adapter as the destructive stream, and its unprimed cursor — Session 58, volume 03.
- The lengths are zero because the seeder runs before its input exists — Sessions 112–113, volume 03.
- `EICON_V34_PORTABLE_BULK`, and the native worker overwriting `DM(0x00A8..0x00A9)` — Sessions 115j–l, volume 04.
- Quality `DM(0x0fcf)` is flat across a 10× range of bulk delay — Session 113. The rate question is a receiver/line one.
- **`EcLevel`, `FarEcLevel` and `SNRPROB` are published as zero in every capture** — Session 207, volume 03. `tools/dil_database_scan.py` reads it out of the archive with no live call. **The zero is the clamp, and it is unconditional**: the routines run every pass and compute a negative dB, which `PM 0x0ede` floors at 0. Calibrated at 6.0206 dB per binary exponent with the reference 116 dB above `MR = 1`, the *largest* value the accumulator can hold still lands 38 dB under the floor — so `EcLevel` cannot publish a non-zero value for any echo at all. Sessions 209, 210.
- **The whole quality block is published by one table-driven loop in the kernel, `PM 0x29c1..0x29d3`** — thirteen routines at `PM 0x0e86..0x0ed8` against destinations `DM(0x3F78..0x3F84)`, tables at `DM(0x00AB)` and `DM(0x00B8)`. `FarEchoPhaseRoll`'s routine is the two-word stub `MR1 = 0; RTS`, and **page 14 reuses `DM(0x3F7C)` for the data pump's inner state** (`PM 0x2fbf` stores `DM(0x2008)`), which is what every non-zero value at that address in the archive actually is. Session 208, volume 03.

### V.34 page 8

- The script-block format, the field rule `DM(0x2137 + field)`, branch indices through `DM(0x0676 + i)`, tests through `DM(0x064B + i)` — Sessions 115n, 141, volume 04.
- The two sequencers, and which one is live — Session 143, volume 04.
- The self-branch is the "stay here" arm — Session 147 (143 read it backwards), volume 04.
- The transmitter decimated by ten, and `EICON_V34_PUBLISH_PACED` — Session 149; read 150 before believing the signal improved.

### The V.32 page

- The LEC tap-count runaway and the stack saturation chain — Sessions 188b–188d, volume 05.
- `PM 0x3536` never returns; the frame handler tail-transfers — Session 188r, volume 05.
- The 1,158-word PM fill that erases `0x2929`, and what dispatches it — Sessions 188t–188y, volume 05.
- **All of it is under `EICON_PIN_PM=0x3805=0x38ab00`**, a counterfactual — Session 188n.

### The data path above the pump

- V.42/LAPM establishment, the `GI=ff` XID parser bug — volume 03.
- V.42bis and V.44 live interop, both directions — volumes 03 and 05.
- PPP, CHAP, IPCP over V.22bis between two emulated ends — Session 183, volume 05.
- A live PPP dial-in from a Windows client — Session 190, volume 06.
- The datagram width per page, and why V.32's was wrong — Sessions 184, 188f, volume 05.

### The MIPS side

- The driver never touches the DSP — Session 188m, volume 05.
- `dsp30_assign` is registered then released; no Q.931 is ever emitted — Sessions 96–99, volume 03. The "held DSP core" was a phantom (99). Do not re-try.
- `EICON_ORIGINATE_LINE_READY` / `EICON_ORIGINATE_V8`, and the dial page's two gates — volume 05.
- V.90A is admitted by the PRI firmware with `EICON_DSP_EXTRA_DOWNLOADS=0x026b` — Sessions 134–135, volume 04.

### The emulator core itself

- The standalone ADSP-2181 prototype and the opcode tables — volume 01.
- The sequencer's equality test and the ABS flags — Session 46, volume 02.
- Incorrect MAC rounding, found by executed-opcode audit — Session 52, volume 02.
- The flags are *correct* where Session 92 checked them — volume 03. Not every anomaly is an emulator defect; two were, most were not.

---

## Every entry, in order

### [Extraction and the emulator](analysis/01-extraction-and-emulator.md)  

*28 entries*

- [Why this firmware matters](analysis/01-extraction-and-emulator.md#why-this-firmware-matters)
- [Extractor](analysis/01-extraction-and-emulator.md#extractor)
- [Verified extraction results](analysis/01-extraction-and-emulator.md#verified-extraction-results)
- [Container references](analysis/01-extraction-and-emulator.md#container-references)
- [Standalone ADSP-2181 execution prototype](analysis/01-extraction-and-emulator.md#standalone-adsp-2181-execution-prototype)
- [Next reverse-engineering step](analysis/01-extraction-and-emulator.md#next-reverse-engineering-step)
- [Script sender and command-ring semantics (2026-07-27)](analysis/01-extraction-and-emulator.md#script-sender-and-command-ring-semantics-2026-07-27)
- [Next reverse-engineering step](analysis/01-extraction-and-emulator.md#next-reverse-engineering-step)
- [Database ring commit and kernel task dispatch (2026-07-27, session 2)](analysis/01-extraction-and-emulator.md#database-ring-commit-and-kernel-task-dispatch-2026-07-27-session-2)
- [Host doorbell and kernel command queue (2026-07-27, session 3)](analysis/01-extraction-and-emulator.md#host-doorbell-and-kernel-command-queue-2026-07-27-session-3)
- [MIPS shim: real firmware routines drive the emulator (2026-07-27, session 4)](analysis/01-extraction-and-emulator.md#mips-shim-real-firmware-routines-drive-the-emulator-2026-07-27-session-4)
- [Session 5: parser path live, kernel scheduler model complete (2026-07-27)](analysis/01-extraction-and-emulator.md#session-5-parser-path-live-kernel-scheduler-model-complete-2026-07-27)
- [Session 6: service-assign entry runs live in the shim (2026-07-27)](analysis/01-extraction-and-emulator.md#session-6-service-assign-entry-runs-live-in-the-shim-2026-07-27)
- [Session 7: Linux driver source + PR_RAM request queue (2026-07-27)](analysis/01-extraction-and-emulator.md#session-7-linux-driver-source--pr_ram-request-queue-2026-07-27)
- [Session 8: linked call assignment and bearer activation](analysis/01-extraction-and-emulator.md#session-8-linked-call-assignment-and-bearer-activation)
- [Session 9: native signalling trace and direct service-assign proof](analysis/01-extraction-and-emulator.md#session-9-native-signalling-trace-and-direct-service-assign-proof)
- [Session 10: fake ingress state in the MIPS shim](analysis/01-extraction-and-emulator.md#session-10-fake-ingress-state-in-the-mips-shim)
- [Session 11: ingress field seeding](analysis/01-extraction-and-emulator.md#session-11-ingress-field-seeding)
- [Session 12: PRI/E1 signalling DSP lead](analysis/01-extraction-and-emulator.md#session-12-prie1-signalling-dsp-lead)
- [Session 13: SIG task registration recovered](analysis/01-extraction-and-emulator.md#session-13-sig-task-registration-recovered)
- [Session 14: forced modem DSP assignment during fake call](analysis/01-extraction-and-emulator.md#session-14-forced-modem-dsp-assignment-during-fake-call)
- [Session 15: raw G.711 RX probe into forced TIKRNL](analysis/01-extraction-and-emulator.md#session-15-raw-g711-rx-probe-into-forced-tikrnl)
- [Session 16: forcing SPORT0 TX from the assigned core](analysis/01-extraction-and-emulator.md#session-16-forcing-sport0-tx-from-the-assigned-core)
- [Session 17: tone-driven RX and live TX pointer bridge](analysis/01-extraction-and-emulator.md#session-17-tone-driven-rx-and-live-tx-pointer-bridge)
- [Session 18: DM 0x3764 TX producer recovered](analysis/01-extraction-and-emulator.md#session-18-dm-0x3764-tx-producer-recovered)
- [Session 19: BRI experiment and a simpler direct driver](analysis/01-extraction-and-emulator.md#session-19-bri-experiment-and-a-simpler-direct-driver)
- [Session 20: firmware G.711 encoder called](analysis/01-extraction-and-emulator.md#session-20-firmware-g711-encoder-called)
- [Session 21: direct SIP/RTP endpoint](analysis/01-extraction-and-emulator.md#session-21-direct-siprtp-endpoint)

### [The native tower, V.8 and INFO](analysis/02-native-tower-v8-and-info.md)  

*52 entries*

- [Session 22: PDF setup correction and physical Courier call](analysis/02-native-tower-v8-and-info.md#session-22-pdf-setup-correction-and-physical-courier-call)
- [Recovered: V.8's indirect page-7 handoff](analysis/02-native-tower-v8-and-info.md#recovered-v8s-indirect-page-7-handoff)
- [Replay result: page 7 loads; INFO receive acquisition stalls](analysis/02-native-tower-v8-and-info.md#replay-result-page-7-loads-info-receive-acquisition-stalls)
- [Blocker isolated: direct INFO RX is disconnected](analysis/02-native-tower-v8-and-info.md#blocker-isolated-direct-info-rx-is-disconnected)
- [Sessions 23–30: the unnumbered stretch — what is in it, and what is not recoverable](analysis/02-native-tower-v8-and-info.md#sessions-2330-the-unnumbered-stretch--what-is-in-it-and-what-is-not-recoverable)
- [INFO `0x37` terminal FFT corruption](analysis/02-native-tower-v8-and-info.md#info-0x37-terminal-fft-corruption)
- [The control-channel framer is not the `0x37` fault](analysis/02-native-tower-v8-and-info.md#the-control-channel-framer-is-not-the-0x37-fault)
- [Live against slmodemd: no `0x37` stall, no FFT corruption](analysis/02-native-tower-v8-and-info.md#live-against-slmodemd-no-0x37-stall-no-fft-corruption)
- [The `0x37` exit: candidates captured, and the missing event](analysis/02-native-tower-v8-and-info.md#the-0x37-exit-candidates-captured-and-the-missing-event)
- [What raises event 1: not the MIPS, and not a tone](analysis/02-native-tower-v8-and-info.md#what-raises-event-1-not-the-mips-and-not-a-tone)
- [The action block gates the transmitter, and running it unblocks the peer](analysis/02-native-tower-v8-and-info.md#the-action-block-gates-the-transmitter-and-running-it-unblocks-the-peer)
- [The real Tone B path, and the reset is the FFT overrun](analysis/02-native-tower-v8-and-info.md#the-real-tone-b-path-and-the-reset-is-the-fft-overrun)
- [The state-record format, and what actually seeds `DM(0x1667)`](analysis/02-native-tower-v8-and-info.md#the-state-record-format-and-what-actually-seeds-dm0x1667)
- [INFOH is not it; the INFO page has two state chains and we take the wrong one](analysis/02-native-tower-v8-and-info.md#infoh-is-not-it-the-info-page-has-two-state-chains-and-we-take-the-wrong-one)
- [What selects the `0x07xx` chain: `GEN_setup1` bit 7 and the reserved word `DM(0x3f8a)`](analysis/02-native-tower-v8-and-info.md#what-selects-the-0x07xx-chain-gen_setup1-bit-7-and-the-reserved-word-dm0x3f8a)
- [No: the 8-bit class belongs to the V.90 decoding, not mode `0x0006`](analysis/02-native-tower-v8-and-info.md#no-the-8-bit-class-belongs-to-the-v90-decoding-not-mode-0x0006)
- [Stepping back: our own stack already documents this exact failure](analysis/02-native-tower-v8-and-info.md#stepping-back-our-own-stack-already-documents-this-exact-failure)
- [The tone detector: found, armed, counting — and two profiles are dead code](analysis/02-native-tower-v8-and-info.md#the-tone-detector-found-armed-counting--and-two-profiles-are-dead-code)
- [The Tone A detector is armed at state `0x0c41`, which we never reach](analysis/02-native-tower-v8-and-info.md#the-tone-a-detector-is-armed-at-state-0x0c41-which-we-never-reach)
- [The `0x41` family is the no-message path, not downstream of Tone B](analysis/02-native-tower-v8-and-info.md#the-0x41-family-is-the-no-message-path-not-downstream-of-tone-b)
- [The real V90D load path is INFO1a mode 6 -> bootpage 14](analysis/02-native-tower-v8-and-info.md#the-real-v90d-load-path-is-info1a-mode-6---bootpage-14)
- [Host-bit audit: V90D is enabled; the remaining selector is received INFO1a](analysis/02-native-tower-v8-and-info.md#host-bit-audit-v90d-is-enabled-the-remaining-selector-is-received-info1a)
- [SPORT0 setup does not feed this Eicon PRI kernel](analysis/02-native-tower-v8-and-info.md#sport0-setup-does-not-feed-this-eicon-pri-kernel)
- [V34SLOT is also bypassed; the real input is the PRI channel descriptor](analysis/02-native-tower-v8-and-info.md#v34slot-is-also-bypassed-the-real-input-is-the-pri-channel-descriptor)
- [What hardware still has that this harness does not](analysis/02-native-tower-v8-and-info.md#what-hardware-still-has-that-this-harness-does-not)
- [Session 31: native incoming-call assignment recovered](analysis/02-native-tower-v8-and-info.md#session-31-native-incoming-call-assignment-recovered)
- [Session 32: native MIPS supervisor attached to the SIP media clock](analysis/02-native-tower-v8-and-info.md#session-32-native-mips-supervisor-attached-to-the-sip-media-clock)
- [Session 33: native loader relocation, not a relocated continuation](analysis/02-native-tower-v8-and-info.md#session-33-native-loader-relocation-not-a-relocated-continuation)
- [Session 34: the real MIPS overlay loader is live](analysis/02-native-tower-v8-and-info.md#session-34-the-real-mips-overlay-loader-is-live)
- [Session 35: native activation, V.8, and INFO](analysis/02-native-tower-v8-and-info.md#session-35-native-activation-v8-and-info)
- [Session 36: live tower/slmodemd result](analysis/02-native-tower-v8-and-info.md#session-36-live-towerslmodemd-result)
- [Session 37: the Linux driver identifies the TX-state boundary](analysis/02-native-tower-v8-and-info.md#session-37-the-linux-driver-identifies-the-tx-state-boundary)
- [Session 38: owner-path experiment disproves the proposed direct replacement](analysis/02-native-tower-v8-and-info.md#session-38-owner-path-experiment-disproves-the-proposed-direct-replacement)
- [Session 39: the bearer callback is not missing](analysis/02-native-tower-v8-and-info.md#session-39-the-bearer-callback-is-not-missing)
- [Session 40: V.8's four-frame exit fixed; TX encoding remains](analysis/02-native-tower-v8-and-info.md#session-40-v8s-four-frame-exit-fixed-tx-encoding-remains)
- [Session 41: native G.711 TX and ANSam recovered](analysis/02-native-tower-v8-and-info.md#session-41-native-g711-tx-and-ansam-recovered)
- [Session 42: private G.711 RX publication recovered](analysis/02-native-tower-v8-and-info.md#session-42-private-g711-rx-publication-recovered)
- [Session 43: forcing event 1 is the wrong response](analysis/02-native-tower-v8-and-info.md#session-43-forcing-event-1-is-the-wrong-response)
- [Session 44: restore SPORT companding; recovery now exits naturally](analysis/02-native-tower-v8-and-info.md#session-44-restore-sport-companding-recovery-now-exits-naturally)
- [Session 45: not overlay paging, and the installer never runs at all](analysis/02-native-tower-v8-and-info.md#session-45-not-overlay-paging-and-the-installer-never-runs-at-all)
- [Session 46: the sequencer's equality test, and the emulator's ABS flags](analysis/02-native-tower-v8-and-info.md#session-46-the-sequencers-equality-test-and-the-emulators-abs-flags)
- [Session 47: live tower call — INFO completes, V.90 DPCM loads, TX stops](analysis/02-native-tower-v8-and-info.md#session-47-live-tower-call--info-completes-v90-dpcm-loads-tx-stops)
- [Session 48: the page-14 transmit source — reproduced offline, two candidates ruled out](analysis/02-native-tower-v8-and-info.md#session-48-the-page-14-transmit-source--reproduced-offline-two-candidates-ruled-out)
- [Session 49: the V.90 data pump's TrnProgress table — `0xea` is a timeout abort](analysis/02-native-tower-v8-and-info.md#session-49-the-v90-data-pumps-trnprogress-table--0xea-is-a-timeout-abort)
- [Session 50: `DM(0x20e0)` across the handoff — the deadline is inherited from INFO, and `0xea` is not a timeout](analysis/02-native-tower-v8-and-info.md#session-50-dm0x20e0-across-the-handoff--the-deadline-is-inherited-from-info-and-0xea-is-not-a-timeout)
- [Session 51: the host never answers the transmit request — and the rig is not real time](analysis/02-native-tower-v8-and-info.md#session-51-the-host-never-answers-the-transmit-request--and-the-rig-is-not-real-time)
- [Session 52: executed-opcode audit finds incorrect MAC rounding](analysis/02-native-tower-v8-and-info.md#session-52-executed-opcode-audit-finds-incorrect-mac-rounding)
- [Session 53: live Phase 3 stall localised to the page-14 callback transition](analysis/02-native-tower-v8-and-info.md#session-53-live-phase-3-stall-localised-to-the-page-14-callback-transition)
- [Session 54: watchdog audit points back to the synthetic kernel continuation](analysis/02-native-tower-v8-and-info.md#session-54-watchdog-audit-points-back-to-the-synthetic-kernel-continuation)
- [Session 55: live slmodemd run disproves the expected `0x72 -> 0x74` dwell path](analysis/02-native-tower-v8-and-info.md#session-55-live-slmodemd-run-disproves-the-expected-0x72---0x74-dwell-path)
- [Session 56: `DM(0x2004)` is generated by the state-image stream](analysis/02-native-tower-v8-and-info.md#session-56-dm0x2004-is-generated-by-the-state-image-stream)
- [Session 57: live run21 shows `DM(0x2004)` is not the pre-`0x80` blocker](analysis/02-native-tower-v8-and-info.md#session-57-live-run21-shows-dm0x2004-is-not-the-pre-0x80-blocker)

### [The echo canceller and DIL](analysis/03-echo-canceller-and-dil.md)  

*76 entries*

- [Session 58: the destructive stream is the bulk-delay adapter; its cursor is unprimed](analysis/03-echo-canceller-and-dil.md#session-58-the-destructive-stream-is-the-bulk-delay-adapter-its-cursor-is-unprimed)
- [Session 59: closed-loop run25 proves the missing cursor publication](analysis/03-echo-canceller-and-dil.md#session-59-closed-loop-run25-proves-the-missing-cursor-publication)
- [Session 60: state `0x7a` is the Ja receive gate, so silence is still correct](analysis/03-echo-canceller-and-dil.md#session-60-state-0x7a-is-the-ja-receive-gate-so-silence-is-still-correct)
- [Session 61: ADSP-2185N runtime proves the V.34 core receives the SPORT sample](analysis/03-echo-canceller-and-dil.md#session-61-adsp-2185n-runtime-proves-the-v34-core-receives-the-sport-sample)
- [Session 62: ADSP-2185N manuals and the first Ja decision trace](analysis/03-echo-canceller-and-dil.md#session-62-adsp-2185n-manuals-and-the-first-ja-decision-trace)
- [Session 63: the native loader retains the sparse V.34 handoff state](analysis/03-echo-canceller-and-dil.md#session-63-the-native-loader-retains-the-sparse-v34-handoff-state)
- [Session 64: `1ab2/a604` is the exact differential-decoded TRN-tail signature](analysis/03-echo-canceller-and-dil.md#session-64-1ab2a604-is-the-exact-differential-decoded-trn-tail-signature)
- [Session 65: the apparent TRN-to-Ja freeze is the delayed bulk-cursor collision](analysis/03-echo-canceller-and-dil.md#session-65-the-apparent-trn-to-ja-freeze-is-the-delayed-bulk-cursor-collision)
- [Session 66: PM `0x1900` exposes the missing retained workspace words](analysis/03-echo-canceller-and-dil.md#session-66-pm-0x1900-exposes-the-missing-retained-workspace-words)
- [Session 67: the missing owner is above the ADSP page, in call ingress/activation](analysis/03-echo-canceller-and-dil.md#session-67-the-missing-owner-is-above-the-adsp-page-in-call-ingressactivation)
- [Session 60: relocated native task attachment and first V90D transmit](analysis/03-echo-canceller-and-dil.md#session-60-relocated-native-task-attachment-and-first-v90d-transmit)
- [Session 61: run31 hardware test disproves the DM3fbc cursor bridge](analysis/03-echo-canceller-and-dil.md#session-61-run31-hardware-test-disproves-the-dm3fbc-cursor-bridge)
- [Session 68: the downstream samples reach the line, and Phase 4 begins](analysis/03-echo-canceller-and-dil.md#session-68-the-downstream-samples-reach-the-line-and-phase-4-begins)
- [Session 69: first downstream data from the card, and the retrain blocker](analysis/03-echo-canceller-and-dil.md#session-69-first-downstream-data-from-the-card-and-the-retrain-blocker)
- [Session 70: the media budget, and why a hitch lands on DIL](analysis/03-echo-canceller-and-dil.md#session-70-the-media-budget-and-why-a-hitch-lands-on-dil)
- [Session 71: USRobotics V.92 interop makes V.42 the next layer](analysis/03-echo-canceller-and-dil.md#session-71-usrobotics-v92-interop-makes-v42-the-next-layer)
- [Session 72: CX93001 V.34 does not reach the V.42 boundary](analysis/03-echo-canceller-and-dil.md#session-72-cx93001-v34-does-not-reach-the-v42-boundary)
- [Session 73: page 8 was never dispatched](analysis/03-echo-canceller-and-dil.md#session-73-page-8-was-never-dispatched)
- [Session 74: the apparent Phase-3 output was stale INFO](analysis/03-echo-canceller-and-dil.md#session-74-the-apparent-phase-3-output-was-stale-info)
- [Session 75: preserve the driver's native CAI-to-WDB initialization](analysis/03-echo-canceller-and-dil.md#session-75-preserve-the-drivers-native-cai-to-wdb-initialization)
- [Session 76: live CX93001 test with native driver WDB](analysis/03-echo-canceller-and-dil.md#session-76-live-cx93001-test-with-native-driver-wdb)
- [Session 77: V.34-only CAI and page-8 scheduler audit](analysis/03-echo-canceller-and-dil.md#session-77-v34-only-cai-and-page-8-scheduler-audit)
- [Session 78: INFO handoff A/B and SPORT-format falsification](analysis/03-echo-canceller-and-dil.md#session-78-info-handoff-ab-and-sport-format-falsification)
- [Session 79: action-table trace and synthetic PC-stack overflow](analysis/03-echo-canceller-and-dil.md#session-79-action-table-trace-and-synthetic-pc-stack-overflow)
- [Session 80: defer firmware-side answer until SIP INVITE](analysis/03-echo-canceller-and-dil.md#session-80-defer-firmware-side-answer-until-sip-invite)
- [Session 81: the media budget again — the MIPS cost was the trace, not the MIPS](analysis/03-echo-canceller-and-dil.md#session-81-the-media-budget-again--the-mips-cost-was-the-trace-not-the-mips)
- [Session 82: Session 75 dropped the V90_DPCM enable, and replay cannot see it](analysis/03-echo-canceller-and-dil.md#session-82-session-75-dropped-the-v90_dpcm-enable-and-replay-cannot-see-it)
- [Session 83: correcting Session 82, and the page-14 exit that breaks fallback](analysis/03-echo-canceller-and-dil.md#session-83-correcting-session-82-and-the-page-14-exit-that-breaks-fallback)
- [Session 84: a terminal on the V.42 link](analysis/03-echo-canceller-and-dil.md#session-84-a-terminal-on-the-v42-link)
- [Session 85: live Courier V.42 test — the data path never switches on](analysis/03-echo-canceller-and-dil.md#session-85-live-courier-v42-test--the-data-path-never-switches-on)
- [Session 86: the garbage is a missing V.42 detection phase](analysis/03-echo-canceller-and-dil.md#session-86-the-garbage-is-a-missing-v42-detection-phase)
- [Session 87: a Courier call completes, and the DIL predictor is falsified](analysis/03-echo-canceller-and-dil.md#session-87-a-courier-call-completes-and-the-dil-predictor-is-falsified)
- [Session 88: the echo canceller is still off, and still cannot be turned on](analysis/03-echo-canceller-and-dil.md#session-88-the-echo-canceller-is-still-off-and-still-cannot-be-turned-on)
- [Session 89: the ingress handoff is healthy, and V8_SETUP=0 is the firmware's](analysis/03-echo-canceller-and-dil.md#session-89-the-ingress-handoff-is-healthy-and-v8_setup0-is-the-firmwares)
- [Session 90: PM 0x1982 is correct; PM 0x1930's fill bounds are the fault](analysis/03-echo-canceller-and-dil.md#session-90-pm-0x1982-is-correct-pm-0x1930s-fill-bounds-are-the-fault)
- [Session 91: the fill is unbounded because its modulo bound is zero](analysis/03-echo-canceller-and-dil.md#session-91-the-fill-is-unbounded-because-its-modulo-bound-is-zero)
- [Session 92: the manual clears the emulator; the near/far fork is not the fix](analysis/03-echo-canceller-and-dil.md#session-92-the-manual-clears-the-emulator-the-nearfar-fork-is-not-the-fix)
- [Session 93: delaycorrection derives the bulk lengths, and near-bulk is probably right](analysis/03-echo-canceller-and-dil.md#session-93-delaycorrection-derives-the-bulk-lengths-and-near-bulk-is-probably-right)
- [Session 94: port the driver's AT and IDI layers, and dismantle the V.34 CAI hypothesis](analysis/03-echo-canceller-and-dil.md#session-94-port-the-drivers-at-and-idi-layers-and-dismantle-the-v34-cai-hypothesis)
- [Session 95: two emulated cards call each other, and the calling side's gate is found](analysis/03-echo-canceller-and-dil.md#session-95-two-emulated-cards-call-each-other-and-the-calling-sides-gate-is-found)
- [Session 96: the calling side waits on a tone detector the card never arms, and why](analysis/03-echo-canceller-and-dil.md#session-96-the-calling-side-waits-on-a-tone-detector-the-card-never-arms-and-why)
- [Session 97: the D-channel tasks are never assigned, and the outgoing call dies before any DSP](analysis/03-echo-canceller-and-dil.md#session-97-the-d-channel-tasks-are-never-assigned-and-the-outgoing-call-dies-before-any-dsp)
- [Session 98: dsp30_assign is registered and then released, because a DSP fails its boot test](analysis/03-echo-canceller-and-dil.md#session-98-dsp30_assign-is-registered-and-then-released-because-a-dsp-fails-its-boot-test)
- [Session 99: the held core was a phantom, and it was not the cause](analysis/03-echo-canceller-and-dil.md#session-99-the-held-core-was-a-phantom-and-it-was-not-the-cause)
- [Session 100: the loopback caller reaches V.34, and three defects were in this harness](analysis/03-echo-canceller-and-dil.md#session-100-the-loopback-caller-reaches-v34-and-three-defects-were-in-this-harness)
- [Session 101: the caller's V.34 collapse is the echo canceller's unbounded fill](analysis/03-echo-canceller-and-dil.md#session-101-the-callers-v34-collapse-is-the-echo-cancellers-unbounded-fill)
- [Session 102: the V.34 caller parks on a silence that never comes, because INFO published nothing](analysis/03-echo-canceller-and-dil.md#session-102-the-v34-caller-parks-on-a-silence-that-never-comes-because-info-published-nothing)
- [Session 103: INFO does publish — the fields V.34 needs are the ones that come out empty](analysis/03-echo-canceller-and-dil.md#session-103-info-does-publish--the-fields-v34-needs-are-the-ones-that-come-out-empty)
- [Session 104: there is no cadence mismatch — the factor of sixteen is the oversampling](analysis/03-echo-canceller-and-dil.md#session-104-there-is-no-cadence-mismatch--the-factor-of-sixteen-is-the-oversampling)
- [V.90 TX mailbox and TIKRNL ownership notes](analysis/03-echo-canceller-and-dil.md#v90-tx-mailbox-and-tikrnl-ownership-notes)
- [NL N_DATA bearer path: requests are posted without completion flow control](analysis/03-echo-canceller-and-dil.md#nl-n_data-bearer-path-requests-are-posted-without-completion-flow-control)
- [The receive side is not misframed; it is misdemodulating](analysis/03-echo-canceller-and-dil.md#the-receive-side-is-not-misframed-it-is-misdemodulating)
- [Why enabling the echo canceller destroyed the state word](analysis/03-echo-canceller-and-dil.md#why-enabling-the-echo-canceller-destroyed-the-state-word)
- [The receive path was working; the fallback was throwing the frames away](analysis/03-echo-canceller-and-dil.md#the-receive-path-was-working-the-fallback-was-throwing-the-frames-away)
- [The XID/SABME stall: a dead transmit bearer and a zero conformance mask](analysis/03-echo-canceller-and-dil.md#the-xidsabme-stall-a-dead-transmit-bearer-and-a-zero-conformance-mask)
- [Twelve live calls: the V.42 fixes are untested, because nothing reached data mode](analysis/03-echo-canceller-and-dil.md#twelve-live-calls-the-v42-fixes-are-untested-because-nothing-reached-data-mode)
- [The XID/SABME blocker is not in V.42 at all: the transmit datagram path](analysis/03-echo-canceller-and-dil.md#the-xidsabme-blocker-is-not-in-v42-at-all-the-transmit-datagram-path)
- [V.42 establishes: `GI=ff` is not a length-prefixed XID group](analysis/03-echo-canceller-and-dil.md#v42-establishes-giff-is-not-a-length-prefixed-xid-group)
- [V.42bis live interop: Annex A negotiation and compressed data both ways](analysis/03-echo-canceller-and-dil.md#v42bis-live-interop-annex-a-negotiation-and-compressed-data-both-ways)
- [V.44 live interop: XID user data and overlapping string extensions](analysis/03-echo-canceller-and-dil.md#v44-live-interop-xid-user-data-and-overlapping-string-extensions)
- [Session 105: restore the native V.34 echo bulk-delay call](analysis/03-echo-canceller-and-dil.md#session-105-restore-the-native-v34-echo-bulk-delay-call)
- [Session 106: extend the retained-bound repair to V90D and verify hardware upstream](analysis/03-echo-canceller-and-dil.md#session-106-extend-the-retained-bound-repair-to-v90d-and-verify-hardware-upstream)
- [Session 107: measure both rates and sweep the first hardware matrix](analysis/03-echo-canceller-and-dil.md#session-107-measure-both-rates-and-sweep-the-first-hardware-matrix)
- [Session 108: fail closed on unqualified V90D bulk widths](analysis/03-echo-canceller-and-dil.md#session-108-fail-closed-on-unqualified-v90d-bulk-widths)
- [Session 109: preserve an exact upstream selection through the quality handoff](analysis/03-echo-canceller-and-dil.md#session-109-preserve-an-exact-upstream-selection-through-the-quality-handoff)
- [Session 110: native V90D selects and preserves an exact 12,000 upstream rate](analysis/03-echo-canceller-and-dil.md#session-110-native-v90d-selects-and-preserves-an-exact-12000-upstream-rate)
- [Session 111: replace the unsafe V90D worker with its bounded database contract](analysis/03-echo-canceller-and-dil.md#session-111-replace-the-unsafe-v90d-worker-with-its-bounded-database-contract)
- [Session 112: the bulk delay lengths are zero, because the seeder runs before its input](analysis/03-echo-canceller-and-dil.md#session-112-the-bulk-delay-lengths-are-zero-because-the-seeder-runs-before-its-input)
- [Session 113: the 0x0050 stall was a dispatch vector, and the bulk delay does not cap upstream](analysis/03-echo-canceller-and-dil.md#session-113-the-0x0050-stall-was-a-dispatch-vector-and-the-bulk-delay-does-not-cap-upstream)
- [Session 207: the echo *level* block is never written on the pages that measure echo phase roll — and `DM(0x3F87)` is `RTDelay`, not a DIL count](analysis/03-echo-canceller-and-dil.md#session-207-the-echo-level-block-is-never-written-on-the-pages-that-measure-echo-phase-roll--and-dm0x3f87-is-rtdelay-not-a-dil-count)
- [Session 208: the quality block is table-driven from the kernel, `FarEchoPhaseRoll`'s routine is a stub, and page 14 reuses `DM(0x3F7C)` for the data pump's inner state](analysis/03-echo-canceller-and-dil.md#session-208-the-quality-block-is-table-driven-from-the-kernel-farechophaserolls-routine-is-a-stub-and-page-14-reuses-dm0x3f7c-for-the-data-pumps-inner-state)
- [Session 209: `EcLevel` is a floored negative, not an absent measurement — and a full-scale accumulator does not lift it off the floor](analysis/03-echo-canceller-and-dil.md#session-209-eclevel-is-a-floored-negative-not-an-absent-measurement--and-a-full-scale-accumulator-does-not-lift-it-off-the-floor)
- [Session 210: the conversion's reference is 38 dB above the largest value the accumulator can hold, so `EcLevel` can never publish anything but zero](analysis/03-echo-canceller-and-dil.md#session-210-the-conversions-reference-is-38-db-above-the-largest-value-the-accumulator-can-hold-so-eclevel-can-never-publish-anything-but-zero)
- [Session 211: the far pair is never accumulated as a level — page 14 only clears it, and the V.34 page uses the region as scratch](analysis/03-echo-canceller-and-dil.md#session-211-the-far-pair-is-never-accumulated-as-a-level--page-14-only-clears-it-and-the-v34-page-uses-the-region-as-scratch)
- [Session 212: RTDelay does not predict the DIL outcome — and the archive is five times larger than Session 207 said](analysis/03-echo-canceller-and-dil.md#session-212-rtdelay-does-not-predict-the-dil-outcome--and-the-archive-is-five-times-larger-than-session-207-said)
- [Session 213: the loopback rig cannot test the delay cliff — it never loads page 14, and it is host-bound on this machine](analysis/03-echo-canceller-and-dil.md#session-213-the-loopback-rig-cannot-test-the-delay-cliff--it-never-loads-page-14-and-it-is-host-bound-on-this-machine)
- [Session 214: live delay clears the supposed cliff; one `0x00b3` runaway reproduces offline, but bypassing it does not fix DIL](analysis/03-echo-canceller-and-dil.md#session-214-live-delay-clears-the-supposed-cliff-one-0x00b3-runaway-reproduces-offline-but-bypassing-it-does-not-fix-dil)
- [Session 215: a second Courier has the same lottery; the runaway is an empty work-list dispatch, and the Conexant still declines PCM](analysis/03-echo-canceller-and-dil.md#session-215-a-second-courier-has-the-same-lottery-the-runaway-is-an-empty-work-list-dispatch-and-the-conexant-still-declines-pcm)

### [V.34 page 8](analysis/04-v34-page8.md)  

*75 entries*

- [Session 114: the INFO word is decoded correctly; the peer really does send those zeros](analysis/04-v34-page8.md#session-114-the-info-word-is-decoded-correctly-the-peer-really-does-send-those-zeros)
- [Session 114b: the V.34 page does not transmit, and that is the whole stall](analysis/04-v34-page8.md#session-114b-the-v34-page-does-not-transmit-and-that-is-the-whole-stall)
- [Session 114c: a live forced-V.34 call, and the correction it forces](analysis/04-v34-page8.md#session-114c-a-live-forced-v34-call-and-the-correction-it-forces)
- [Session 114d: the freeze is rate-independent, and the action stream runs without the generator](analysis/04-v34-page8.md#session-114d-the-freeze-is-rate-independent-and-the-action-stream-runs-without-the-generator)
- [Session 114e: the page is not stuck, it is repeating — and the gate is DM(0x213B) bit 15](analysis/04-v34-page8.md#session-114e-the-page-is-not-stuck-it-is-repeating--and-the-gate-is-dm0x213b-bit-15)
- [Session 114f: the gate is a script record field, written once, and never updated](analysis/04-v34-page8.md#session-114f-the-gate-is-a-script-record-field-written-once-and-never-updated)
- [Session 114g: the CAI is faithful, and a block that opens the gate does exist](analysis/04-v34-page8.md#session-114g-the-cai-is-faithful-and-a-block-that-opens-the-gate-does-exist)
- [Session 114h: state 0x0066 is the only state that opens the gate, and it is skipped](analysis/04-v34-page8.md#session-114h-state-0x0066-is-the-only-state-that-opens-the-gate-and-it-is-skipped)
- [Session 114i: correcting the role half, and why the state test never runs](analysis/04-v34-page8.md#session-114i-correcting-the-role-half-and-why-the-state-test-never-runs)
- [Session 114j: the last block load resolves its exit test into the INFO data area](analysis/04-v34-page8.md#session-114j-the-last-block-load-resolves-its-exit-test-into-the-info-data-area)
- [Session 114k: the V.34 test table is being overwritten by the bulk worker at PM 0x1930](analysis/04-v34-page8.md#session-114k-the-v34-test-table-is-being-overwritten-by-the-bulk-worker-at-pm-0x1930)
- [Session 114l: the corruption is fixed; V.34 now stops earlier, at state 0x0064](analysis/04-v34-page8.md#session-114l-the-corruption-is-fixed-v34-now-stops-earlier-at-state-0x0064)
- [Session 114m: what is actually missing — the V.34 page is barely being clocked](analysis/04-v34-page8.md#session-114m-what-is-actually-missing--the-v34-page-is-barely-being-clocked)
- [Session 114n: the per-sample callback itself is not being driven](analysis/04-v34-page8.md#session-114n-the-per-sample-callback-itself-is-not-being-driven)
- [Session 114o: the kernel's per-sample loop runs 8x slow on the V.34 page](analysis/04-v34-page8.md#session-114o-the-kernels-per-sample-loop-runs-8x-slow-on-the-v34-page)
- [Session 114p: the callback is never rewritten; the CPU stops dispatching entirely](analysis/04-v34-page8.md#session-114p-the-callback-is-never-rewritten-the-cpu-stops-dispatching-entirely)
- [Session 114q: the loop is normal machinery everywhere except V.34, where it never returns](analysis/04-v34-page8.md#session-114q-the-loop-is-normal-machinery-everywhere-except-v34-where-it-never-returns)
- [Session 114r: the loop is the block loader's field unpacker, and it hangs on block 0x1afa](analysis/04-v34-page8.md#session-114r-the-loop-is-the-block-loaders-field-unpacker-and-it-hangs-on-block-0x1afa)
- [Session 114s: the stride is 1, so the record is the problem](analysis/04-v34-page8.md#session-114s-the-stride-is-1-so-the-record-is-the-problem)
- [Session 114t: correction — 114r/114s conflated two routines, and the loop terminates](analysis/04-v34-page8.md#session-114t-correction--114r114s-conflated-two-routines-and-the-loop-terminates)
- [Session 114u: the PC histogram, and what the V.34 page actually does](analysis/04-v34-page8.md#session-114u-the-pc-histogram-and-what-the-v34-page-actually-does)
- [Session 114v: the hang is the second of two entries, and it is table 0x1ea2](analysis/04-v34-page8.md#session-114v-the-hang-is-the-second-of-two-entries-and-it-is-table-0x1ea2)
- [Session 114w: the table is intact — it is the comparison that fails, not the data](analysis/04-v34-page8.md#session-114w-the-table-is-intact--it-is-the-comparison-that-fails-not-the-data)
- [Session 114x: both real entries succeed — the hang is a third, wild arrival](analysis/04-v34-page8.md#session-114x-both-real-entries-succeed--the-hang-is-a-third-wild-arrival)
- [Session 114y: PM 0x2725 is a dispatch table walker, and the table is corrupt](analysis/04-v34-page8.md#session-114y-pm-0x2725-is-a-dispatch-table-walker-and-the-table-is-corrupt)
- [Session 114z: root cause — the "fixed" bulk worker still corrupts, one table along](analysis/04-v34-page8.md#session-114z-root-cause--the-fixed-bulk-worker-still-corrupts-one-table-along)
- [Session 115: the verification instrument, and why the wrap fix was not made](analysis/04-v34-page8.md#session-115-the-verification-instrument-and-why-the-wrap-fix-was-not-made)
- [Session 115b: the ring index never resets, and the reason is parity](analysis/04-v34-page8.md#session-115b-the-ring-index-never-resets-and-the-reason-is-parity)
- [Session 115c: the guide kills one candidate, does not document the other, and points elsewhere](analysis/04-v34-page8.md#session-115c-the-guide-kills-one-candidate-does-not-document-the-other-and-points-elsewhere)
- [Session 115d: there is no far pair on this path, and the rings belong in low DM](analysis/04-v34-page8.md#session-115d-there-is-no-far-pair-on-this-path-and-the-rings-belong-in-low-dm)
- [Session 115e: the extents are not confirmed — four "structures", one profile](analysis/04-v34-page8.md#session-115e-the-extents-are-not-confirmed--four-structures-one-profile)
- [Session 115f: there is no partition — and the descriptor base is zero](analysis/04-v34-page8.md#session-115f-there-is-no-partition--and-the-descriptor-base-is-zero)
- [Session 115g: the base is zero on V.90 too — that hypothesis is dead](analysis/04-v34-page8.md#session-115g-the-base-is-zero-on-v90-too--that-hypothesis-is-dead)
- [Session 115h: EXTENDED_LEC is real, reaches the card, and does not fix it](analysis/04-v34-page8.md#session-115h-extended_lec-is-real-reaches-the-card-and-does-not-fix-it)
- [Session 115i: symbol-rate disables also reach the card, also change nothing](analysis/04-v34-page8.md#session-115i-symbol-rate-disables-also-reach-the-card-also-change-nothing)
- [Session 115j: holding the worker removes the freeze — it is cause, not symptom](analysis/04-v34-page8.md#session-115j-holding-the-worker-removes-the-freeze--it-is-cause-not-symptom)
- [Session 115k: the portable bulk delay runs on V.34, and changes nothing beyond the hold](analysis/04-v34-page8.md#session-115k-the-portable-bulk-delay-runs-on-v34-and-changes-nothing-beyond-the-hold)
- [Session 115l: three calls each, and the default flips](analysis/04-v34-page8.md#session-115l-three-calls-each-and-the-default-flips)
- [Session 115m: nothing stops it at 0x0090 — that is the top of a timeout walk](analysis/04-v34-page8.md#session-115m-nothing-stops-it-at-0x0090--that-is-the-top-of-a-timeout-walk)
- [Session 115n: there is no 0x0076 block — those are sub-states inside 0x0070](analysis/04-v34-page8.md#session-115n-there-is-no-0x0076-block--those-are-sub-states-inside-0x0070)
- [Session 116: page 14 reaches Phase 4 against a live Courier, and leaves on a ratechange — not on the timer](analysis/04-v34-page8.md#session-116-page-14-reaches-phase-4-against-a-live-courier-and-leaves-on-a-ratechange--not-on-the-timer)
- [Session 117: the rate is not what it rejects — a lower ceiling moves the rates and not the gate](analysis/04-v34-page8.md#session-117-the-rate-is-not-what-it-rejects--a-lower-ceiling-moves-the-rates-and-not-the-gate)
- [Session 118: 117's 0x00b3 claim was n=1 — the cap does not decide it, and 0x00d0's dwell is not fixed](analysis/04-v34-page8.md#session-118-117s-0x00b3-claim-was-n1--the-cap-does-not-decide-it-and-0x00d0s-dwell-is-not-fixed)
- [Session 119: the histogram can be gated on TrnProgress](analysis/04-v34-page8.md#session-119-the-histogram-can-be-gated-on-trnprogress)
- [Session 120: the 0x00b3 stall is a runaway loop that eats the whole sample budget](analysis/04-v34-page8.md#session-120-the-0x00b3-stall-is-a-runaway-loop-that-eats-the-whole-sample-budget)
- [Session 121: the pointers are constants, and 0x00b3 has a second, harder failure](analysis/04-v34-page8.md#session-121-the-pointers-are-constants-and-0x00b3-has-a-second-harder-failure)
- [Session 122: 0x1317 is a chained dispatch vector, and the hang is one slot pointing at itself](analysis/04-v34-page8.md#session-122-0x1317-is-a-chained-dispatch-vector-and-the-hang-is-one-slot-pointing-at-itself)
- [Session 123: 122's dispatch-vector claim is withdrawn — the read it depends on does not happen](analysis/04-v34-page8.md#session-123-122s-dispatch-vector-claim-is-withdrawn--the-read-it-depends-on-does-not-happen)
- [Session 124: the vector is DM(0x20A1), and root PM is not what an offline boot says it is](analysis/04-v34-page8.md#session-124-the-vector-is-dm0x20a1-and-root-pm-is-not-what-an-offline-boot-says-it-is)
- [Session 125: DM(0x20A1) is a mutable handler pointer with three writers](analysis/04-v34-page8.md#session-125-dm0x20a1-is-a-mutable-handler-pointer-with-three-writers)
- [Session 126: the handler pointers are literals, so 0x1317 cannot be a miscalculation](analysis/04-v34-page8.md#session-126-the-handler-pointers-are-literals-so-0x1317-cannot-be-a-miscalculation)
- [Session 127: the 026a overlay supplies this code, and 0x1317 is in no shipped image](analysis/04-v34-page8.md#session-127-the-026a-overlay-supplies-this-code-and-0x1317-is-in-no-shipped-image)
- [Session 128: a MAC output loop walks over the handler table](analysis/04-v34-page8.md#session-128-a-mac-output-loop-walks-over-the-handler-table)
- [Session 129: is it us? the addressing is right, but 128 overstated what was measured](analysis/04-v34-page8.md#session-129-is-it-us-the-addressing-is-right-but-128-overstated-what-was-measured)
- [Session 130: the harness fabricates a call every sample, and that is the real divergence](analysis/04-v34-page8.md#session-130-the-harness-fabricates-a-call-every-sample-and-that-is-the-real-divergence)
- [Session 131: an inventory of what the harness patches, and a second instance of a known hazard](analysis/04-v34-page8.md#session-131-an-inventory-of-what-the-harness-patches-and-a-second-instance-of-a-known-hazard)
- [Session 132: the per-sample ISR patch is not load-bearing](analysis/04-v34-page8.md#session-132-the-per-sample-isr-patch-is-not-load-bearing)
- [Session 133: the bulk A/B separates nothing, and gives the first variance baseline](analysis/04-v34-page8.md#session-133-the-bulk-ab-separates-nothing-and-gives-the-first-variance-baseline)
- [Session 134: V.90A is not missing from the PRI firmware — only from its file set](analysis/04-v34-page8.md#session-134-v90a-is-not-missing-from-the-pri-firmware--only-from-its-file-set)
- [Session 135: the V.90A bit reaches the DSP, and then V.90A queues behind V.34](analysis/04-v34-page8.md#session-135-the-v90a-bit-reaches-the-dsp-and-then-v90a-queues-behind-v34)
- [Session 136: 0x1408/0x2804 is not a state — the status block is somebody else's buffer](analysis/04-v34-page8.md#session-136-0x14080x2804-is-not-a-state--the-status-block-is-somebody-elses-buffer)
- [Session 137: they do act on the INFO message — the caller transmits nothing on page 8](analysis/04-v34-page8.md#session-137-they-do-act-on-the-info-message--the-caller-transmits-nothing-on-page-8)
- [Session 138: the page-8 transmitter is gated on DM(0x2140), which the calling side never sets](analysis/04-v34-page8.md#session-138-the-page-8-transmitter-is-gated-on-dm0x2140-which-the-calling-side-never-sets)
- [Session 139: the force-DM knob, and DM(0x2140) is not the cause](analysis/04-v34-page8.md#session-139-the-force-dm-knob-and-dm0x2140-is-not-the-cause)
- [Session 140: it is the role word — the calling script never drives the transmitter](analysis/04-v34-page8.md#session-140-it-is-the-role-word--the-calling-script-never-drives-the-transmitter)
- [Session 141: the calling script, mapped — and the transmitter's role dependence is not in it](analysis/04-v34-page8.md#session-141-the-calling-script-mapped--and-the-transmitters-role-dependence-is-not-in-it)
- [Session 142: 0x2b4a is dead code, and so is the rest of 141's short list](analysis/04-v34-page8.md#session-142-0x2b4a-is-dead-code-and-so-is-the-rest-of-141s-short-list)
- [Session 143: the block trail is one block — both ends are parked in a designed wait state](analysis/04-v34-page8.md#session-143-the-block-trail-is-one-block--both-ends-are-parked-in-a-designed-wait-state)
- [Session 144: the driver clears the level hypothesis, and page 8 is broadband where INFO is a tone](analysis/04-v34-page8.md#session-144-the-driver-clears-the-level-hypothesis-and-page-8-is-broadband-where-info-is-a-tone)
- [Session 145: the card's own state machine answers it — no S detector needed](analysis/04-v34-page8.md#session-145-the-cards-own-state-machine-answers-it--no-s-detector-needed)
- [Session 146: the detector was never the problem — 143's inference was wrong](analysis/04-v34-page8.md#session-146-the-detector-was-never-the-problem--143s-inference-was-wrong)
- [Session 147: the wait block loops because the test passes, and raising the threshold moves both ends](analysis/04-v34-page8.md#session-147-the-wait-block-loops-because-the-test-passes-and-raising-the-threshold-moves-both-ends)
- [Session 148: a fitted instruction budget does not stop the detector latching](analysis/04-v34-page8.md#session-148-a-fitted-instruction-budget-does-not-stop-the-detector-latching)
- [Session 149: the page-8 transmitter was being decimated by ten, and pacing it fixes that](analysis/04-v34-page8.md#session-149-the-page-8-transmitter-was-being-decimated-by-ten-and-pacing-it-fixes-that)
- [Session 150: correcting 149's concentration claim — the metric was scoring DC](analysis/04-v34-page8.md#session-150-correcting-149s-concentration-claim--the-metric-was-scoring-dc)

### [The data path and modulation selection](analysis/05-data-path-and-modulation-selection.md)  

*66 entries*

- [Session 151: the page-8 transmit chain, mapped end to end](analysis/05-data-path-and-modulation-selection.md#session-151-the-page-8-transmit-chain-mapped-end-to-end)
- [Session 152: the transmit history is fed from the V.90 mapping-frame block](analysis/05-data-path-and-modulation-selection.md#session-152-the-transmit-history-is-fed-from-the-v90-mapping-frame-block)
- [Session 153: the noise is the overlay's own output, not anything the harness does to it](analysis/05-data-path-and-modulation-selection.md#session-153-the-noise-is-the-overlays-own-output-not-anything-the-harness-does-to-it)
- [Session 154: the first arithmetic oracle, and a real 218x gap that is not the bug](analysis/05-data-path-and-modulation-selection.md#session-154-the-first-arithmetic-oracle-and-a-real-218x-gap-that-is-not-the-bug)
- [Session 155: the 0x0F67 ring, and an audit of the emulator against the 218x manuals](analysis/05-data-path-and-modulation-selection.md#session-155-the-0x0f67-ring-and-an-audit-of-the-emulator-against-the-218x-manuals)
- [Session 156: the live instruction at PM 0x3792, and structured inputs to a MAC](analysis/05-data-path-and-modulation-selection.md#session-156-the-live-instruction-at-pm-0x3792-and-structured-inputs-to-a-mac)
- [Session 157: the ring is not interleaved — the generator stops halfway through page 8](analysis/05-data-path-and-modulation-selection.md#session-157-the-ring-is-not-interleaved--the-generator-stops-halfway-through-page-8)
- [Session 158: the gate is a vector word, `DM(0x0B72)`](analysis/05-data-path-and-modulation-selection.md#session-158-the-gate-is-a-vector-word-dm0x0b72)
- [Session 159: PM 0x36b0 tests nothing — it is a table-driven vector load](analysis/05-data-path-and-modulation-selection.md#session-159-pm-0x36b0-tests-nothing--it-is-a-table-driven-vector-load)
- [Session 160: the mode word is `DM(0x0F59)`, written by PM `0x3669`](analysis/05-data-path-and-modulation-selection.md#session-160-the-mode-word-is-dm0x0f59-written-by-pm-0x3669)
- [Session 161: PM 0x3669 is a 13-word block copy, and the mode's real source is `DM(0x0B59)`](analysis/05-data-path-and-modulation-selection.md#session-161-pm-0x3669-is-a-13-word-block-copy-and-the-modes-real-source-is-dm0x0b59)
- [Session 162: the staging block is published by PM 0x2a75/0x2a7a](analysis/05-data-path-and-modulation-selection.md#session-162-the-staging-block-is-published-by-pm-0x2a750x2a7a)
- [Session 163: the transmit mode is script block field 0x00](analysis/05-data-path-and-modulation-selection.md#session-163-the-transmit-mode-is-script-block-field-0x00)
- [Session 164: the ceilings are gone — both ends reach 0x00b0, and the blocker moves there](analysis/05-data-path-and-modulation-selection.md#session-164-the-ceilings-are-gone--both-ends-reach-0x00b0-and-the-blocker-moves-there)
- [Session 165: why the page stops publishing at 0x00b0 — it is the pacing fix starving the foreground](analysis/05-data-path-and-modulation-selection.md#session-165-why-the-page-stops-publishing-at-0x00b0--it-is-the-pacing-fix-starving-the-foreground)
- [Session 166: the yield works mechanically and still loses — and that casts doubt on 0x00b0](analysis/05-data-path-and-modulation-selection.md#session-166-the-yield-works-mechanically-and-still-loses--and-that-casts-doubt-on-0x00b0)
- [Session 167: the 0x00b0 trail is signal-driven — 166's caution withdrawn](analysis/05-data-path-and-modulation-selection.md#session-167-the-0x00b0-trail-is-signal-driven--166s-caution-withdrawn)
- [Session 168: the caller only transmits under the stop because only the stop produces a carrier](analysis/05-data-path-and-modulation-selection.md#session-168-the-caller-only-transmits-under-the-stop-because-only-the-stop-produces-a-carrier)
- [Session 169: the stop truncates the modulator's polyphase loop](analysis/05-data-path-and-modulation-selection.md#session-169-the-stop-truncates-the-modulators-polyphase-loop)
- [Session 170: completing the polyphase group is worse — 169's proposed fix is disproved](analysis/05-data-path-and-modulation-selection.md#session-170-completing-the-polyphase-group-is-worse--169s-proposed-fix-is-disproved)
- [Session 171: the generator is broadband in every configuration; the carrier comes from the filter](analysis/05-data-path-and-modulation-selection.md#session-171-the-generator-is-broadband-in-every-configuration-the-carrier-comes-from-the-filter)
- [Session 172: the multiplier is faithful too — the arithmetic hypothesis is closed](analysis/05-data-path-and-modulation-selection.md#session-172-the-multiplier-is-faithful-too--the-arithmetic-hypothesis-is-closed)
- [Session 173: the hardware timer is not used, and the symbol clock is already correct](analysis/05-data-path-and-modulation-selection.md#session-173-the-hardware-timer-is-not-used-and-the-symbol-clock-is-already-correct)
- [Session 174: the sevenths are V.34's own, and the symbol generator runs at half the symbol rate](analysis/05-data-path-and-modulation-selection.md#session-174-the-sevenths-are-v34s-own-and-the-symbol-generator-runs-at-half-the-symbol-rate)
- [Session 175: the generator rate is anti-correlated with success — 174's requirement is wrong](analysis/05-data-path-and-modulation-selection.md#session-175-the-generator-rate-is-anti-correlated-with-success--174s-requirement-is-wrong)
- [Session 176: PM 0x17A6 is a polyphase kernel, and the 9/7 "surplus" is a resampler ratio](analysis/05-data-path-and-modulation-selection.md#session-176-pm-0x17a6-is-a-polyphase-kernel-and-the-97-surplus-is-a-resampler-ratio)
- [Session 177: only the working mechanism completes the resampler loops — and pacing is now finished](analysis/05-data-path-and-modulation-selection.md#session-177-only-the-working-mechanism-completes-the-resampler-loops--and-pacing-is-now-finished)
- [Session 178: round-trip delay does not move the V.34 wall — but V.8's modulation selection depends on it](analysis/05-data-path-and-modulation-selection.md#session-178-round-trip-delay-does-not-move-the-v34-wall--but-v8s-modulation-selection-depends-on-it)
- [Session 179: V.8 picks V.22 because its result word is never written — PM 0x3982 does not run](analysis/05-data-path-and-modulation-selection.md#session-179-v8-picks-v22-because-its-result-word-is-never-written--pm-0x3982-does-not-run)
- [Session 180: the answerer's V.8 receiver is starved, not broken — the calling end leaves V.8 at 1 s](analysis/05-data-path-and-modulation-selection.md#session-180-the-answerers-v8-receiver-is-starved-not-broken--the-calling-end-leaves-v8-at-1-s)
- [Session 181: the caller's V.8 has been negotiating with the dial page's V.22-only mask — NORM_L was never actually forced](analysis/05-data-path-and-modulation-selection.md#session-181-the-callers-v8-has-been-negotiating-with-the-dial-pages-v22-only-mask--norm_l-was-never-actually-forced)
- [Session 182: the caller's V.22 fallback is this harness's off-hook guard, and the rig starts both modems at once](analysis/05-data-path-and-modulation-selection.md#session-182-the-callers-v22-fallback-is-this-harnesss-off-hook-guard-and-the-rig-starts-both-modems-at-once)
- [Session 183: V.22bis is the only modulation that has ever connected here — and `--modulation` does not select a modulation](analysis/05-data-path-and-modulation-selection.md#session-183-v22bis-is-the-only-modulation-that-has-ever-connected-here--and---modulation-does-not-select-a-modulation)
- [Session 184: the classifier is the modulation selector, and V.32 stalls on an unserved partial overlay](analysis/05-data-path-and-modulation-selection.md#session-184-the-classifier-is-the-modulation-selector-and-v32-stalls-on-an-unserved-partial-overlay)
- [Session 185: partial overlays are served now, and V.32 runs away in the LEC instead of training](analysis/05-data-path-and-modulation-selection.md#session-185-partial-overlays-are-served-now-and-v32-runs-away-in-the-lec-instead-of-training)
- [Session 186: the LEC bound was fine — the partial was overwriting it — and V.32 still does not train](analysis/05-data-path-and-modulation-selection.md#session-186-the-lec-bound-was-fine--the-partial-was-overwriting-it--and-v32-still-does-not-train)
- [Session 187: DM(0x3fb8) is the dispatch vector and the partial sets it correctly — V.32 is Session 165's blocker](analysis/05-data-path-and-modulation-selection.md#session-187-dm0x3fb8-is-the-dispatch-vector-and-the-partial-sets-it-correctly--v32-is-session-165s-blocker)
- [Session 188: the non-idle continuation is built — it unblocks V.32's state machine and regresses V.34, so they are not one blocker](analysis/05-data-path-and-modulation-selection.md#session-188-the-non-idle-continuation-is-built--it-unblocks-v32s-state-machine-and-regresses-v34-so-they-are-not-one-blocker)
- [Session 188b: nothing enters `0x1d90` without the `DO` — the LEC loop goes immortal when the 4-deep counter stack saturates](analysis/05-data-path-and-modulation-selection.md#session-188b-nothing-enters-0x1d90-without-the-do--the-lec-loop-goes-immortal-when-the-4-deep-counter-stack-saturates)
- [Session 188c: the stack-overflow warning, and it says the PC stack goes first](analysis/05-data-path-and-modulation-selection.md#session-188c-the-stack-overflow-warning-and-it-says-the-pc-stack-goes-first)
- [Session 188d: nothing nests 16 deep — it is two frames sharing one stack, and the LEC never gets off it](analysis/05-data-path-and-modulation-selection.md#session-188d-nothing-nests-16-deep--it-is-two-frames-sharing-one-stack-and-the-lec-never-gets-off-it)
- [Session 188e: serve the partial at the request, not at the end of the sample — V.32 reaches `TrnProgress 0x00d0`](analysis/05-data-path-and-modulation-selection.md#session-188e-serve-the-partial-at-the-request-not-at-the-end-of-the-sample--v32-reaches-trnprogress-0x00d0)
- [Session 188f: the pump has a V.32 width now — and the width was never the blocker](analysis/05-data-path-and-modulation-selection.md#session-188f-the-pump-has-a-v32-width-now--and-the-width-was-never-the-blocker)
- [Session 188g: `DI_control` does not stop on page 2 — it never starts, and the page abandons](analysis/05-data-path-and-modulation-selection.md#session-188g-di_control-does-not-stop-on-page-2--it-never-starts-and-the-page-abandons)
- [Session 188h: the condition is `DM(0x0571) != 0`, tested at the top of every V.32 frame — and the partial *does* carry PM](analysis/05-data-path-and-modulation-selection.md#session-188h-the-condition-is-dm0x0571--0-tested-at-the-top-of-every-v32-frame--and-the-partial-does-carry-pm)
- [Session 188i: `0x18f3` is not a status code — it is a record field scattered into a parameter block from DM the page never loads](analysis/05-data-path-and-modulation-selection.md#session-188i-0x18f3-is-not-a-status-code--it-is-a-record-field-scattered-into-a-parameter-block-from-dm-the-page-never-loads)
- [Session 188j: the "record" is a cosine table — the pointer is wrong, and nothing is missing or corrupt](analysis/05-data-path-and-modulation-selection.md#session-188j-the-record-is-a-cosine-table--the-pointer-is-wrong-and-nothing-is-missing-or-corrupt)
- [Session 188k: `AX0` comes from `DM(0x05B7)`, and `PM 0x28c0` puts a terminator-`0x1A` database into the terminator-`0x1F` slot](analysis/05-data-path-and-modulation-selection.md#session-188k-ax0-comes-from-dm0x05b7-and-pm-0x28c0-puts-a-terminator-0x1a-database-into-the-terminator-0x1f-slot)
- [Session 188l: the handler table is swapped by self-modifying code — bit 7 should reach `PM 0x2C79`, not `PM 0x28BF`](analysis/05-data-path-and-modulation-selection.md#session-188l-the-handler-table-is-swapped-by-self-modifying-code--bit-7-should-reach-pm-0x2c79-not-pm-0x28bf)
- [Session 188m: what the host driver does — and `PM 0x2909` is a trampoline copied in from resident PM, not a page writing its own code](analysis/05-data-path-and-modulation-selection.md#session-188m-what-the-host-driver-does--and-pm-0x2909-is-a-trampoline-copied-in-from-resident-pm-not-a-page-writing-its-own-code)
- [Session 188n: the A/B — pin `PM 0x3805` to `I4 = $0AB0` and V.32 stops abandoning](analysis/05-data-path-and-modulation-selection.md#session-188n-the-ab--pin-pm-0x3805-to-i4--0ab0-and-v32-stops-abandoning)
- [Session 188o: nineteen V.32 frames complete, then the PC stack fills and a non-reentrant kernel scribbles the status block](analysis/05-data-path-and-modulation-selection.md#session-188o-nineteen-v32-frames-complete-then-the-pc-stack-fills-and-a-non-reentrant-kernel-scribbles-the-status-block)
- [Session 188p: the stack does not leak and does not merely nest — it unwinds cleanly for 69 frames and then stalls in three](analysis/05-data-path-and-modulation-selection.md#session-188p-the-stack-does-not-leak-and-does-not-merely-nest--it-unwinds-cleanly-for-69-frames-and-then-stalls-in-three)
- [Session 188q: gate the watches to the page under test — and `PM 0x0774` is cleared, with a positive control](analysis/05-data-path-and-modulation-selection.md#session-188q-gate-the-watches-to-the-page-under-test--and-pm-0x0774-is-cleared-with-a-positive-control)
- [Session 188r: `PM 0x3536` is entered once per frame and never returns](analysis/05-data-path-and-modulation-selection.md#session-188r-pm-0x3536-is-entered-once-per-frame-and-never-returns)
- [Session 188s: log volume changes the answer — the rig is wall-clock paced, and a hot watch moves the V.32 stall by 1.8 M cycles](analysis/05-data-path-and-modulation-selection.md#session-188s-log-volume-changes-the-answer--the-rig-is-wall-clock-paced-and-a-hot-watch-moves-the-v32-stall-by-18-m-cycles)
- [Session 188t: V.22 returns through the same dispatcher — and V.32 calls a routine its own frame erased](analysis/05-data-path-and-modulation-selection.md#session-188t-v22-returns-through-the-same-dispatcher--and-v32-calls-a-routine-its-own-frame-erased)
- [Session 188u: the fill is not the pin's doing — the window is staged once, cleared once, and never re-staged](analysis/05-data-path-and-modulation-selection.md#session-188u-the-fill-is-not-the-pins-doing--the-window-is-staged-once-cleared-once-and-never-re-staged)
- [Session 188v: bit 0 of `DM(0x0554)` dispatches the clear — and the two runs reach it for opposite reasons](analysis/05-data-path-and-modulation-selection.md#session-188v-bit-0-of-dm0x0554-dispatches-the-clear--and-the-two-runs-reach-it-for-opposite-reasons)
- [Session 188w: `PM 0x1fbb` is the page's overlay unpack, gated on `DM(0x3FB0) == 2` — and the gate is not what stops it re-running](analysis/05-data-path-and-modulation-selection.md#session-188w-pm-0x1fbb-is-the-pages-overlay-unpack-gated-on-dm0x3fb0--2--and-the-gate-is-not-what-stops-it-re-running)
- [Session 188x: neither post-clear routine touches program memory — there is no restore](analysis/05-data-path-and-modulation-selection.md#session-188x-neither-post-clear-routine-touches-program-memory--there-is-no-restore)
- [Session 188y: nothing on this page reads them — and the clear is the *optional* half of the configure step](analysis/05-data-path-and-modulation-selection.md#session-188y-nothing-on-this-page-reads-them--and-the-clear-is-the-optional-half-of-the-configure-step)
- [Session 189: the card's own firmware runs T.30 — the fax protocol row reaches it, and the DSP side does not follow](analysis/05-data-path-and-modulation-selection.md#session-189-the-cards-own-firmware-runs-t30--the-fax-protocol-row-reaches-it-and-the-dsp-side-does-not-follow)
- [Session 204: the V.32 rate word and the database map — and three conclusions read off windows nobody had aligned](analysis/05-data-path-and-modulation-selection.md#session-204-the-v32-rate-word-and-the-database-map--and-three-conclusions-read-off-windows-nobody-had-aligned)
- [Session 205: the V.32 transmit is not decimated — one publish per tick, and the clipped stimulus every host-side probe has used](analysis/05-data-path-and-modulation-selection.md#session-205-the-v32-transmit-is-not-decimated--one-publish-per-tick-and-the-clipped-stimulus-every-host-side-probe-has-used)
- [Session 206: the width tests were still asserting the constant Session 204 withdrew](analysis/05-data-path-and-modulation-selection.md#session-206-the-width-tests-were-still-asserting-the-constant-session-204-withdrew)

### [The V.90 page decision](analysis/06-v90-page-decision.md)  

*36 entries*

- [Session 190: PPP carries a live dial-in — and the Conexant never reaches the V.90 page](analysis/06-v90-page-decision.md#session-190-ppp-carries-a-live-dial-in--and-the-conexant-never-reaches-the-v90-page)
- [Session 191: the V.90 decision is a branch at PM 0x2bc1 against PM 0x2b9a](analysis/06-v90-page-decision.md#session-191-withdrawn-the-v90-decision-is-a-branch-at-pm-0x2bc1-against-pm-0x2b9a) **↩ withdrawn**
- [Session 192: `PM 0x2bc1` and `PM 0x2b9a` are arms of two arithmetic helpers — the branch Session 191 named is not a branch about V.90](analysis/06-v90-page-decision.md#session-192-pm-0x2bc1-and-pm-0x2b9a-are-arms-of-two-arithmetic-helpers--the-branch-session-191-named-is-not-a-branch-about-v90)
- [Session 193: the DSP requests the bootpage, from three bits of a word the peer sent — and forcing it puts the Conexant on V.90](analysis/06-v90-page-decision.md#session-193-the-dsp-requests-the-bootpage-from-three-bits-of-a-word-the-peer-sent--and-forcing-it-puts-the-conexant-on-v90)
- [Session 194: the Conexant asks for V.34 — INFO1a bits 37:39 are 4, and the card is doing exactly what V.90 Table 10 says](analysis/06-v90-page-decision.md#session-194-the-conexant-asks-for-v34--info1a-bits-3739-are-4-and-the-card-is-doing-exactly-what-v90-table-10-says)
- [Session 195: an independent V.90 server reads the same 4 — the Conexant declines PCM after line probing, not before](analysis/06-v90-page-decision.md#session-195-an-independent-v90-server-reads-the-same-4--the-conexant-declines-pcm-after-line-probing-not-before)
- [Session 196: `AT#UD` decoded against the Unimodem spec — it cannot answer the question, and it corroborates the answer we have](analysis/06-v90-page-decision.md#session-196-atud-decoded-against-the-unimodem-spec--it-cannot-answer-the-question-and-it-corroborates-the-answer-we-have)
- [Session 197: Asterisk is in the media path, and the two ports do not share an endpoint config](analysis/06-v90-page-decision.md#session-197-asterisk-is-in-the-media-path-and-the-two-ports-do-not-share-an-endpoint-config)
- [Session 198: our INFO1d probing results are a constant, and they are not a shape a real probe produces](analysis/06-v90-page-decision.md#session-198-our-info1d-probing-results-are-a-constant-and-they-are-not-a-shape-a-real-probe-produces)
- [Session 199: [PARTLY WITHDRAWN] the probe *is* measured — the conversion to a projected rate flattens it](analysis/06-v90-page-decision.md#session-199-partly-withdrawn-the-probe-is-measured--the-conversion-to-a-projected-rate-flattens-it)  **↩ partly withdrawn**
- [Session 200: `EICON_PIN_DM`, and the projected rate does not come from the measured words](analysis/06-v90-page-decision.md#session-200-eicon_pin_dm-and-the-projected-rate-does-not-come-from-the-measured-words)
- [Session 201: the rate is `AX0 - (DM(0x0DFF) - 14)`, and four of the six rates produce *exactly* the same value](analysis/06-v90-page-decision.md#session-201-the-rate-is-ax0---dm0x0dff---14-and-four-of-the-six-rates-produce-exactly-the-same-value)
- [Session 202: [PARTLY WITHDRAWN] the budget is not it, and the rate comes from a second array that is mostly zeros](analysis/06-v90-page-decision.md#session-202-partly-withdrawn-the-budget-is-not-it-and-the-rate-comes-from-a-second-array-that-is-mostly-zeros)  **↩ partly withdrawn**
- [Session 203: nothing is missing — the mask is set deliberately, and four symbol rates are switched off on purpose](analysis/06-v90-page-decision.md#session-203-nothing-is-missing--the-mask-is-set-deliberately-and-four-symbol-rates-are-switched-off-on-purpose)
- [Session 216: the Conexant control exonerated the Eicon implementation, not the shared media harness](analysis/06-v90-page-decision.md#session-216-the-conexant-control-exonerated-the-eicon-implementation-not-the-shared-media-harness)
- [Session 217: B5 is already selected, and the VG224 proves modem passthrough never activates](analysis/06-v90-page-decision.md#session-217-b5-is-already-selected-and-the-vg224-proves-modem-passthrough-never-activates)
- [Session 218: disabling the VG224 echo canceller and NLP is not enough](analysis/06-v90-page-decision.md#session-218-disabling-the-vg224-echo-canceller-and-nlp-is-not-enough)
- [Session 219: `+MS=V90,0` is the real hard force; it makes the Conexant abort, not request PCM](analysis/06-v90-page-decision.md#session-219-msv900-is-the-real-hard-force-it-makes-the-conexant-abort-not-request-pcm)
- [Session 220: a 34,667 minimum downstream rate still cannot make INFO1a request PCM](analysis/06-v90-page-decision.md#session-220-a-34667-minimum-downstream-rate-still-cannot-make-info1a-request-pcm)
- [Session 221: the V.8 JM does include digital PCM — the summary decoder selected a false short candidate](analysis/06-v90-page-decision.md#session-221-the-v8-jm-does-include-digital-pcm--the-summary-decoder-selected-a-false-short-candidate)
- [Session 222: disabling the Conexant's dual-PCM detector changes INFO1a from V.34 to V.90](analysis/06-v90-page-decision.md#session-222-disabling-the-conexants-dual-pcm-detector-changes-info1a-from-v34-to-v90)
- [Session 223: another analogue line passes the CX dual-PCM test without an override](analysis/06-v90-page-decision.md#session-223-another-analogue-line-passes-the-cx-dual-pcm-test-without-an-override)
- [Session 224: extension 7802 also passes dual-PCM detection normally](analysis/06-v90-page-decision.md#session-224-extension-7802-also-passes-dual-pcm-detection-normally)
- [Session 225: 7802 selects V.90 but does not connect](analysis/06-v90-page-decision.md#session-225-7802-selects-v90-but-does-not-connect)
- [Session 226: unloaded and 40 ms-lag batches still cannot connect on 7802](analysis/06-v90-page-decision.md#session-226-unloaded-and-40-ms-lag-batches-still-cannot-connect-on-7802)
- [Session 227: CX diagnostic states show a Phase-2 restart after the Eicon DIL stall](analysis/06-v90-page-decision.md#session-227-cx-diagnostic-states-show-a-phase-2-restart-after-the-eicon-dil-stall)
- [Session 228: the successful CX call runs a missing DIL work initializer](analysis/06-v90-page-decision.md#session-228-the-successful-cx-call-runs-a-missing-dil-work-initializer)
- [Session 229: correction — the decisive dispatch is delay-derived PM 0x0375, not a missing PM 0x3f73 initializer](analysis/06-v90-page-decision.md#session-229-correction--the-decisive-dispatch-is-delay-derived-pm-0x0375-not-a-missing-pm-0x3f73-initializer)
- [Session 230: DM 0x3fcb is scaled INFO elapsed-time carryover, not RTDelay](analysis/06-v90-page-decision.md#session-230-dm-0x3fcb-is-scaled-info-elapsed-time-carryover-not-rtdelay)
- [Session 231: correction — DM 0x3fcb is RTDelay at 8 kHz resolution](analysis/06-v90-page-decision.md#session-231-correction--dm-0x3fcb-is-rtdelay-at-8-khz-resolution)
- [Session 232: the bad operand is a positive signed byte from the delay-aligned signal ring](analysis/06-v90-page-decision.md#session-232-the-bad-operand-is-a-positive-signed-byte-from-the-delay-aligned-signal-ring)
- [Session 233: an empty-only arithmetic guard removes the runaway but does not make 7802 train](analysis/06-v90-page-decision.md#session-233-an-empty-only-arithmetic-guard-removes-the-runaway-but-does-not-make-7802-train)
- [Session 234: rejecting a positive candidate also fails live](analysis/06-v90-page-decision.md#session-234-rejecting-a-positive-candidate-also-fails-live)
- [Session 235: the fitted 2185N exposes a real BIASRND emulator defect](analysis/06-v90-page-decision.md#session-235-the-fitted-2185n-exposes-a-real-biasrnd-emulator-defect)
- [Session 236: live 2185N BIASRND result is also 0/3](analysis/06-v90-page-decision.md#session-236-live-2185n-biasrnd-result-is-also-03)
- [Session 237: right-justified 2185N SPORT expansion produces the first CX CONNECT](analysis/06-v90-page-decision.md#session-237-right-justified-2185n-sport-expansion-produces-the-first-cx-connect)
