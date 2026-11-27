# Current status

This is the short capability map. For setup and freshly run regression results,
see [testing.md](testing.md). For historical investigation notes, see
[handoff.md](handoff.md) and the [analysis index](eicon_adsp_firmware_analysis.md).

**Evidence categories matter:** a saved hardware capture is not a new card
run; local firmware emulation is not a QEMU guest; a modem reaching data mode
is not proof of sustained payload reliability.

| Area | What is established | Remaining qualification |
| --- | --- | --- |
| ADSP core | Local C/Python regressions and comparisons with saved physical-chip conformance results | Full live-firmware/peripheral timing is not implied by isolated arithmetic tests |
| Local BRI board/MIPS model | Stock firmware boot, signalling/bearer lifecycle, exact binary transfer; freshly rerun eight-bearer V.21 and V.22bis carrier-gated transfer/teardown | Other modem profiles, long-duration reliability, and the full physical reset-aperture extent |
| QEMU/Linux guest | Documented unmodified-driver boot, IDI/CAPI operation and concurrent bearer transfers; local tests also check saved guest fixtures | No fresh QEMU guest run was performed during the setup/regression work; see the dedicated reproduction procedure |
| Analog/PRI V.34 + LAPM/PPP | Executable qualifier passed 3 calls against one persistent answerer at 16,384 exact bytes each way per call, zero TX underruns/FCS errors/resets/RX loss, clean CHAP/IPCP/SIP BYE/PPP teardown, complete source captures and explicit clock/gap budgets; earlier 4,096-byte case also reproduced from a fresh tree and freshly extracted tracked firmware | Longer soaks, simultaneous full-bandwidth/TCP bulk transfer, recovery faults and physical-peer timing; TX underruns still occur under load despite a zero-underrun strict idle repeat |
| Live physical modem ↔ BRI firmware emulator | Documented V.90 38666/31200 connection, three 8,192-byte bidirectional reconnect transfers, and a 60-second exact duplex stream | Twenty consecutive reconnects, 30-minute soak, other peers and higher live-emulator rates remain unqualified; this does not qualify the standalone physical DSP ring |
| Physical DSP routines and tones | Saved hardware-backed FIR, modem-routine and tone-generation/playback comparisons | Offline routine replay and two-stage playback do not establish continuous live modem-task streaming |
| Continuous physical SPORT1 | Flow-continuous rings exist; unexpected substituted zero words were isolated to the board receive path/configuration in the documented controls | Zero-anomaly, repeated long-duration streaming; clock/framing/DU configuration remains the investigation target |
| Real-card V.90 musical DIL | Documented matching caller RX and Diva TX waveform prefixes, with V.90 startup/data mode | Sustained PPP payload and BER qualification for these custom-DIL calls; do not count the initial wrong-route baseline as a Diva V.90 result |
| Deployed retro ISP | PPP/networking and ISP services have documented deployment/validation notes | No production services or hardware were touched by the local regression work; current live availability is not asserted here |

## Reproduction and evidence

- **Local regression gates:** [testing.md](testing.md), `make test`,
  `make test-local`, `make test-all`.
- **BRI board scope:** [bri_card_model.md](bri_card_model.md).
  The two long training regressions can be selected with
  `.venv/bin/python -m pytest -m slow -vv` after the native preflight.
- **Live QEMU guest:** [qemu/README.md](../qemu/README.md).
- **V.34 PPP:** `make qualify-v34` and the
  [qualification guide/results](v34_qualification.md), plus the earlier
  [ping smoke](v34_smoke_20261125.md) and
  [T401 diagnosis](v34_data_mode_t401_20261023.md). These use local emulated
  endpoints, not a physical line.
- **Live modem ↔ emulator payloads:** [bri_wire_zero_20261026.md](bri_wire_zero_20261026.md).
  This includes a PCMU signed-zero boundary correction, distinct from physical
  receive-ring substitutions.
- **Physical stream blocker:** [sport_ring_status_20261024.md](sport_ring_status_20261024.md).
- **Hardware musical DIL:** [v90_dil_music_hardware_20261125.md](v90_dil_music_hardware_20261125.md).

## Next work, in order

1. **Qualify residual V.34 host stalls/RX recovery, then longer cases.**
   [Media diagnostics and wakeup repairs](media_underrun_diagnostics.md) now
   cover overdue sender notification and producer-selector busy polling.
   Those mechanisms are fixed without larger buffers. A strict idle three-call
   repeat passed with zero underruns on both ends and ~8,000-Hz source TX
   timing; a concurrent-native-gate repeat still counted five underruns.
   Per-packet jitter and RX starvation under sufficiently long stalls remain.
   The executable command now also requires complete, continuous source TX
   captures within an explicit clock budget. A larger strict **3 × 16 KiB per
   direction** run passed, with zero underruns and a requested 40 ms maximum
   source-gap budget (29.814 ms observed maximum). Historical slow-clock
   captures fail the new gate despite successful protocol transfers.
   [Controlled whole-caller pauses](v34_stall_recovery.md) now also preserve
   recovery and timing as separate results: 20/100 ms requested pauses both
   recovered exact data without logged mode exits/resets, but failed strict
   timing. The 20 ms case had zero underruns yet a 41.883 ms gap against the
   unchanged 40 ms budget. These are not producer-only or physical-peer faults.
   [Independent-wire producer/RX-dispatch stalls](v34_producer_stall.md) now
   isolate the main thread: a real source packet left during a 108.607 ms
   producer hold while DSP samples/RX queue stayed fixed. Payloads recovered,
   but strict timing failed; its no-fault control also failed, so no clean
   performance delta is claimed. [Bounded CPU/wall profiling](v34_producer_cpu.md)
   now separates main-thread CPU consumption from elapsed time: a fixed set
   retained a passing trace-only control, one passing and two failing profiled
   nominal calls, and a failing 100 ms producer pause. All recovered exact data.
   Data CPU means stayed below 20 ms, but a 36.453 ms CPU quantum and >53 ms
   non-CPU components both occurred. Sample-pump work dominates measured CPU;
   [GC and emulated-work observations](v34_gc_work.md) then found 8–10 ms
   caller gen-2 collection contributions, alongside long no-GC/non-CPU tails.
   Live Analog kernel dispatch no longer retains duplicate unbounded SPORT
   histories: native replay is bit-exact and observed caller collections fell
   from 192 to 14, without observed gen-2 events in the history-off set. Timing
   remains variable: one of three history-off nominals passed; another failed
   a 41.231 ms gap despite zero underruns. Next distinguish non-CPU waits and
   remaining native/Python CPU costs without assuming CPU saturation.
   [Phase-controlled RX recovery models](v34_rx_recovery_model.md) now isolate
   usable 160-sample input waiting for 320-sample recovery prefill. In a 12/9 ms
   cost control, data-only reference resumption shortened full-input-to-output
   latency from 25 to 9 ms, but the peer's 74-to-58 ms maximum-gap reduction
   still failed the 40 ms model budget and caused more frequent RX waits.
   Training was unchanged; four saved firmware incident snapshots anchor this
   gate state. A [guarded native opt-in](v34_data_rx_resume.md) now checks current
   native data state plus PPP/link readiness, with bounded closed receipts.
   Five corrected calls all verified exact payload/lifecycle; the PRI answerer
   used the guarded branch 3,092 times, but **all five timing verdicts failed**.
   Both requested producer pauses could not arm with 2 ms future deadline;
   neither is recovery evidence. Caller mean data-quantum CPU was 22.27–26.67 ms
   against a 20 ms interval. Default 320-sample recovery remains unchanged;
   isolate remaining sample-pump CPU costs/non-CPU waits before promotion.
   [Scalar FIR bookkeeping optimization](v34_resampler_cpu.md) now preserves
   binary64 outputs/native state while reducing isolated paired FIR CPU from
   about 2.22 to 0.74 ms per 160 inputs. Fixed live observations retained one
   before-call failure and three after calls: **one timing pass, two failures**;
   no modem-wide 3× speedup or repeatable pacing claim is made. Residual data
   incidents include 40.529/18.016 ms wall/CPU with ordinary emulated work and
   no GC overlap. [External owned-caller stack observations](v34_stack_observations.md)
   now add a macOS-only, explicit diagnostic option with no endpoint changes.
   Two sampled calls completed their ownership/report/progress gates; one main
   snapshot showed a selector-return GIL condition-wait path, not a DSP-return
   wait. Fixed controls/observations yielded PASS/FAIL/FAIL/FAIL, all exact
   payload/lifecycle. Aggregated stacks lack per-snapshot timestamps and may
   perturb pacing, so the remaining 110–136 ms wall tails are not root-caused
   and no policy or timing budget changed. Increase
   call counts/duration further, then cover TCP bulk, simultaneous
   full-bandwidth duplex and injected recovery faults. Preserve reports;
   neither the default case nor three ~98-second streams constitute a long soak.
2. **Resolve physical receive substitutions if live hardware streaming is the
   immediate goal.** Keep constant-input and RX-only DMA controls. Do not mask
   unexplained words in emulator arithmetic or host scheduling.
3. **Extend sustained-transfer and recovery coverage**, then refactor stable
   media/control/capture boundaries behind those tests rather than rewriting
   the large MIPS/SIP modules wholesale.

Firmware emulation and physical-card research have separate proof obligations;
advancing one does not silently qualify the other.
