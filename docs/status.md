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
| Analog/PRI V.34 + LAPM/PPP | Fresh 60-second local smoke: data mode, one LAPM connection per end, CHAP/IPCP, 20/20 pings, zero final PPP FCS errors/resets | Sustained exact bulk payloads, reconnect/teardown qualification, and physical-peer timing; the smoke reported TX underruns and sub-real-time media progress |
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
- **V.34 PPP:** [fresh smoke result](v34_smoke_20261125.md) and the
  [T401 diagnosis](v34_data_mode_t401_20261023.md). These use local emulated
  endpoints, not a physical line.
- **Live modem ↔ emulator payloads:** [bri_wire_zero_20261026.md](bri_wire_zero_20261026.md).
  This includes a PCMU signed-zero boundary correction, distinct from physical
  receive-ring substitutions.
- **Physical stream blocker:** [sport_ring_status_20261024.md](sport_ring_status_20261024.md).
- **Hardware musical DIL:** [v90_dil_music_hardware_20261125.md](v90_dil_music_hardware_20261125.md).

## Next work, in order

1. **Turn one V.34/LAPM/PPP path into a repeatable qualification command.**
   Require explicit pass/fail criteria for training, negotiation, exact payloads,
   reconnects and teardown. Preserve captures and structured results. The
   general loopback runner's exit code alone is not a qualification verdict.
2. **Resolve physical receive substitutions if live hardware streaming is the
   immediate goal.** Keep constant-input and RX-only DMA controls. Do not mask
   unexplained words in emulator arithmetic or host scheduling.
3. **Extend sustained-transfer and recovery coverage**, then refactor stable
   media/control/capture boundaries behind those tests rather than rewriting
   the large MIPS/SIP modules wholesale.

Firmware emulation and physical-card research have separate proof obligations;
advancing one does not silently qualify the other.
