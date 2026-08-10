# Harness intervention inventory

This inventory separates host actions from firmware execution. Line numbers refer
to `tools/eicon_mips_shim.py` at the time of writing; search the symbol when a
later edit moves them.

| Source / intervention | Default | Pages | Firmware behaviour replaced | Evidence that introduced it | Current causal status |
|---|---:|---|---|---|---|
| `_run_execution_sample()` / `adsp2181_modem_sample()` (around 1290, 4900) | legacy | all | Host supplies a continuation PC after the SPORT allowance; the C helper may CALL `0x06c8` or `0x02b7` | Existing replay needed one selected task invocation per media sample; the hardware chronology was not yet modeled | **Active legacy intervention.** `EICON_EXECUTION_MODEL=sport` bypasses it and calls `adsp2181_sport0_tdm_frame()`.
| `ADSP.adsp2181_call(cpu, 0x06c8, 0x02a8)` (4900) | enabled on native bearer / non-direct path | V90D path | Synthetic no-host continuation and synthetic return to idle | Runtime traces showed one `0x06c8` hit per sample while the private descriptor was not reconstructible | **Not used by sport mode.** Natural ownership is unresolved; retain only as a measured legacy control.
| SPORT selected-slot compatibility fill (`adsp2181_sport0_tdm_frame()`) | enabled | all selected SPORT media | Invokes one selected IRQ and duplicates its expanded word over the kernel's 64-word TDM history instead of clocking 32 physical slots | Original private-descriptor reconstruction bypassed the unavailable per-slot owner | **Active fidelity gap.** `EICON_SPORT_FULL_TDM=1` is a diagnostic 32-slot chronology, but stops V90D at `0x0060` before the slicer because natural descriptor dispatch is still absent; do not use it as a rate oracle or default.
| continuation selection `0x02b7` vs `0x06c8` (4905–4910) | enabled | V.34 / V90D | Selected foreground and callback ownership are chosen by page | V.34 stalled when the old `0x06c8` tail was resumed; V.34 needed the selected foreground to reach `0x0703` | **Legacy only.** Sport mode does not pass either address to the C core.
| C-core non-idle continuation/context save-restore (`adsp2181_modem_sample()`, around core 2000) | enabled only by `EICON_CONTINUE_NON_IDLE` or publish yield | configured pages | Host injects a call beneath a live foreground and restores volatile state | V.32/V.34 did not receive another dispatch after a budget cut; A/B showed page-specific benefit and regression | **Legacy intervention.** Must be replaced by real interrupt preemption before qualification.
| `stop_on_dm_write_n()` / `yield_on_stop()` (4965–4971) | V.34 pacing enabled | `0x0261` | Core stops at a page TX publish instead of running the SPORT clock budget | Fixed budget produced 9–12 page publishes per sample; pacing produced one | **Disabled in sport mode.** It changes chronology and is not evidence of a natural sample owner.
| `latch_dm_write()` / first-publish selection (4935–4940, 5660+) | opt-in | `0x0261`, V22 diagnostics | Host chooses the first publish instead of the firmware/SPORT boundary word | Used to distinguish decimation from page publication count | **Measurement/legacy only.** Disabled by sport mode for page 8.
| PM `0x06cd` clear suppression (5080–5100) | V90D enabled; V.34 opt-in | `0x026a`, optionally `0x0261` | Resident kernel clears `DM(0x3fa7..0x3fac)`; host replaces PM with NOP | Mapping block survived only when the clear was suppressed; this fixed the observed impulse train | **Legacy diagnostic only.** Sport mode restores firmware clear and records mapping writes.
| Synthetic bulk-length seed (`_service_bulk_lengths()`, 4346+) | enabled | `0x026a`, `0x0261` | Host holds `DM(0x3fbc/0x3fbd)` and saved context at nonzero delay lengths | Firmware left both lengths at zero in the legacy captures; portable delay servicing needs a coherent bounded ring | **Legacy only.** SPORT tracing now proves PM `0x3235`/`0x1086` naturally publish the page's lengths; no selected-channel host writer is missing.
| Portable bulk-delay servicing (`_service_bulk_adapter()`, 4397+) | V90D/V.34 enabled by defaults | `0x026a`, `0x0261` | Host services the delay-line ABI and holds the native worker at RTS | Native worker corrupted shared state on hardware and again in closed-loop SPORT run39 despite natural length publication | **Active fail-closed intervention in both execution models.** Native release is diagnostic-only until its remaining memory/phase precondition is recovered.
| Selected PCMU Ucode table (`_install_v90d_pcmu_ucode_table()`) | enabled for PCMU | `0x026a` | Restores the page's staged Table 1/V.90 magnitudes after its initializer replaces them with the resident A-law table; retains the +/-2 linear sentinel for distinct PCMU zero polarities | Run40 emitted TRN1d Ucode 49 for negotiated UINFO 48; run43 became exact over Sd, S-bar-d, Ucode, and GPC signs; run65/73 completed Phase 4 and data | **Active selected-law correction.** Uses firmware-staged data, not fabricated state. `EICON_V90D_PCMU_UCODE_TABLE=0` reproduces the old boundary.
| PM `0x19c8` bulk-worker RTS patch (5115+) | enabled by V90D/V34 bulk settings | `0x026a`, `0x0261` | Firmware bulk worker is replaced with RTS | Same native-width/descriptor failures as portable service; SPORT run39 escaped into unrelated DM at sample 99651 | **Active in both models while portable service owns the bounded ABI.** Disable portable mode only for explicit native diagnostics.
| Direct selected-channel dispatch (`complete_native_answer()`, 3965) | legacy | assigned modem | Host marks the private selected descriptor path rather than allowing normal dispatch | Native bearer setup leaves a private descriptor that generic kernel dispatch cannot reconstruct | **Disabled by sport mode.** The missing descriptor/owner remains a Phase 1 investigation.
| ISR vector installation (`install_isr_vector()`, 4862) | enabled | assigned modem | Host temporarily installs selected-channel ISR vector around each media frame | Generic kernel slot does not retain the private channel callback after overlay downloads | **Still active in both modes.** This is a firmware-vector reconstruction, not yet a pure hardware path; it must be documented or removed in Phase 1.
| Partial-overlay service (`_service_partial_overlay()`, 5180+) | enabled by `EICON_PARTIAL_STOP=1` | V.8/V.32 partial pages | Host stops/resumes at a bootpage request and loads the partial image between frames | Late servicing left V.32 in an unseeded echo loop and saturated stacks | **Unresolved.** It is outside the selected SPORT callback but remains a host execution intervention.
| Page-14 transmit companding (`_sport_tx_sample()`, around 5977) | enabled | `0x026a` | Host expands the page's right-justified 14-bit line word at `DM(0x3FB4)` by four before `encode_g711()`, which takes PCM16 | The transmit half of Session 237's SPORT boundary: 100% of the archived page-14 words in training and in data mode are exact mu-law codepoints at x4 and 0.0% at x1, and the emitted wire measured `-36.4 dBFS` rms against the peer's `-22.6` | **Active boundary correction, not yet qualified by a call.** It restores the companding the SPORT would have done, so it replaces no firmware decision; `EICON_V90D_TX_SPORT_SCALE=0` reproduces the old boundary. Words above 8031 are the un-overwritten `0x3764` pointer and pass through untouched, counted separately in the end-of-call census.
| Force DM / PM pins and patches (`_apply_force_dm()`, 4846–4848) | environment-controlled | selected experiments | Host overwrites or pins firmware state/instructions | Narrow diagnostics for rate/recovery and opcode hypotheses | **Diagnostic only.** Must be absent from acceptance oracles.
| `publish_bulk_lower_limit()` (798) | enabled | `0x0261`, `0x026a` | Host writes `0xffff` to `DM(DM(0x32f7) + 5)` as the selected bulk descriptor's sparse common-layer lower limit | Native bulk worker underflowed on a descriptor whose lower limit was never published | **Active, and misaddressed.** `DM(0x32f7)` is zero on entry to `0x026a`, so the write lands on `DM(0x0005)`, inside the page-zero block that holds receive handler addresses at page entry. Session 244 read-watched `DM(0x0005)` across the whole V90D training window and recorded no firmware read, so it is inert there -- but it is not writing the word it claims to, and the real descriptor base is still unknown.
| Forced INFO/fallback decisions (5010+, `force_info_after_v8`) | opt-in | V.8 / INFO | Host fabricates page/rate requests after a timeout | Used to test post-V.8 selection and INFO configuration separately | **Diagnostic only.** Never use for execution-model qualification.

## Intended ownership chain

The firmware-faithful chain to prove is:

```text
SPORT0 RX assertion
  -> resident kernel SPORT vector
  -> TIKRNL selected-channel owner
  -> selected foreground / task
  -> PM 0x0703 callback
  -> RTI
  -> interrupted foreground
```

The current C-core coverage counters and per-frame history record executions of
`0x02b7`, `0x0703`, and `0x06c8`, but do not yet label a call as natural versus
host-injected. The next A/B should run the same replay with `legacy` and `sport`
using identical image hashes and compare the first frame where that ownership
chain differs.

## PM `0x06c8` caller question

Static/runtime evidence currently identifies two paths:

1. The legacy harness explicitly calls `0x06c8` after the selected SPORT frame.
2. Firmware PM `0x06ca..0x06cd` is reached from the resident kernel tail through
   the relocated selected-channel continuation; its mapping-block clear is a
   kernel-owned store, not proof that the host should call `0x06c8`.

The natural caller still needs an execution watch on the PM call site and a
resident-page-qualified trace. Until that trace exists, the harness must not
promote the explicit call to a hardware model.
