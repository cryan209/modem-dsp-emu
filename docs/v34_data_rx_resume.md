# Opt-in native V.34 data RX resume experiment

**Default recovery policy is unchanged. This experiment is not timing-qualified.**
Localhost emulation only; no physical peer, production service or registrar was
used. This follows the [phase-controlled model](v34_rx_recovery_model.md), not a
promotion of its synthetic observations into a native qualification pass.

## Enable explicitly

```sh
make qualify-v34 V34_ARGS='--calls 1 --producer-detail --experimental-data-rx-resume --max-tx-underruns 0 --max-tx-gap-ms 40 --output artifacts/qualification/NEW'
```

The qualifier enables media tracing and forwards the flag to both endpoints.
The endpoint refuses the experiment without localhost binding, real-time pacing,
PPP, native DSP, media tracing and `EICON_MODULATION=v34,...`. Native-MIPS and
BRI-kernel backends are excluded. Modulation is an environment setting in this
endpoint, not an argparse field.

At an already-started, held RX gate with **160–319 fresh samples** and normal
320-sample prefill, the experiment reads:

- **Current native DM(0x3fc2) == 0x00d0**, not cached `call.trn_progress`.
- Current PPP peer `up is True`.
- Current card LAPM `data_ready is True`.

Only then does this gate use a 160-sample threshold. The endpoint's `data_mode`
flag is deliberately not used: it describes TX-buffer promotion and stays false
in the qualifier's zero data-buffer-promotion configuration. Current link state
is rechecked for every candidate; there is no permanent data-mode latch.
`data_ready` is the existing LAPM API (which can also indicate raw fallback);
qualification independently requires exactly one logged LAPM connection, so
raw fallback does not satisfy its contract.

Startup, training, partial input, unheld streams, other current calls, exhausted
legacy starvation paths, configured prefill other than 320 and disabled flags
retain their existing behavior. The experiment is bypassed with RX repetition
enabled. It changes no audio values, DSP arithmetic, required captures, media
or wire deadline, wire clock, TX cushion, GC policy or default buffer setting.

The resume count means the **guard-selected branch was used**, not a
counterfactual count of saved underruns or latency. In particular, an expired
hold could already have resumed through the legacy path. Retained native
success events here all preceded their hold expiry, but capped detailed records
are not a complete history of every success. The qualifier's
`data_rx_resume_effect_observed` records nonzero branch use, not a causal
performance conclusion.

## Bounded, closed receipts

`tools/data_rx_resume.py` retains 128 success snapshots and 128 denials, plus
all-decision counters/reasons and explicit drop counts. Snapshots contain the
current native state, current PPP/link eligibility, real RX depth, media sample
position, hold time and queue/deadline observations. Wire snapshots are not
transactional with the sender thread. No per-sample timers, hot-path writes or
prints are added. At teardown, after sender stop:

- Receipts go under `MEDIA-TRACE-DIR/data-rx-resume/call-NNN.json`.
- One final logged resume count must agree with the closed receipt.
- Both roles' complete receipts are required when the qualifier flag is set.
- Zero use can have a complete receipt, but is **not evidence of RX recovery**.

Payload/lifecycle checks, closed source captures, sequence/timestamp continuity,
zero RX loss and the explicit clock/gap/underrun budgets still gate success.

## Fixed native observations

All commands used one call, 4096 independent bytes in each direction,
`--producer-detail`, zero TX-underrun budget, 40 ms source-gap budget and
±1000 ppm source-clock budget. No intentional concurrent test workload or
individual timing retry was used. Commands/statuses were written before running.

### Initial implementation failures retained

The first fixed five commands were retained under `v34-rx-resume-*`:

- Nominal control and requested-pause control verified exact payload/lifecycle,
  but failed timing with **385/383** and **420/419** caller/answerer underruns.
- Three opt-in commands exited before calling because validation incorrectly
  referenced nonexistent `args.modulation` rather than `EICON_MODULATION`.
- The pause-control request did not arm; no pause receipt/actual hold existed.

A separate receipt parent-directory defect was also found before a successful
opt-in call: atomic JSON writing does not create parents. Validation and nested
directory handling were fixed with regression tests. These were implementation
fixes, not changes to budgets, fault eligibility, audio or recovery policy.

### Corrected fixed five-command set

The entire corrected set is retained under `v34-rx-resume-fixed-*`:

| Case | Caller/answerer underruns | Caller/answerer max gap ms | Caller/answerer source Hz | Answerer guarded resumes | Strict verdict |
| --- | --- | --- | --- | --- | --- |
| Control | 451 / 453 | 167.165 / 185.888 | 6281.324 / 6284.321 | off | FAIL |
| Experiment nominal 001 | 210 / 208 | 192.398 / 125.858 | 7099.601 / 7105.341 | 1094 | FAIL |
| Experiment nominal 002 | 246 / 243 | 109.541 / 144.006 | 6909.082 / 6913.219 | 967 | FAIL |
| Requested 100 ms pause, control | 139 / 135 | 122.804 / 75.662 | 7366.489 / 7366.737 | off | FAIL; pause unapplied |
| Requested 100 ms pause, experiment | 259 / 255 | 77.611 / 116.230 | 6884.317 / 6889.171 | 1031 | FAIL; pause unapplied |

All five verified **40,960 total independent payload bytes**, one data-mode
entry/no logged departure, LAPM/CHAP/IPCP, zero final FCS errors/resets/RX loss,
closed continuous captures without sequence/timestamp discontinuities, clean
child exits and teardown. All ten CPU/detail profiles were complete, with no
primary profile or GC event drops. All five sets matched **410 runtime input
fingerprints** of the final implementation.

Only the PRI answerer's recovery branch was exercised live: **3,092 successes**,
**532 non-data** denials and **479 PPP-down** denials. The Analog caller had
zero candidate attempts: its RX recovery policy was not exercised by these
calls. All six experiment receipts closed and matched their final logged counts.
The three answerer success rings dropped **966/839/903** old snapshots; denial
rings dropped **346/61/220**. All 384 retained success snapshots had current
`0x00d0`, PPP up, link data-ready, 160 samples and a not-yet-expired hold. Do not
extend their detailed coverage to the dropped events.

Ordinary media traces dropped 806–4748 events; incident rings dropped 7–325
snapshots. Full counts remain accounted for, but those rings are **not complete
timelines** or complete per-incident explanations. Among the retained 128 data
incidents per role/run, the two controls' answerers had **16 and 5** snapshots
waiting with a full quantum below prefill; the three experiments had **zero**.
Their answerer incidents were producer-active in **383 of 384** retained
snapshots, with one waiting for less than a quantum. All 640 retained caller
incidents were producer-active. This is consistent with removing the targeted
gate condition in these windows, not a complete history, causal CPU diagnosis
or timing qualification. Raw counts are in `retained-incidents.json`.

Neither requested producer pause found an eligible post-publication point with
queued real output, a live wire thread and the unchanged **2 ms future wire
deadline** requirement. Both requests occurred at 1024 verified bytes and
finished at 4096; neither had an endpoint hold receipt or a wire-window proof.
The existing receipts do not record each failed eligibility term, so they do
not isolate deadline lead versus already-dequeued output for each attempt.
They are not successful 100 ms injections or recovery qualifications.
Eligibility was not relaxed and no deadline was moved to make the fault happen.

## What this does and does not establish

Caller mean measured data-quantum thread CPU was **26.670, 23.101, 24.528,
22.270 and 24.363 ms** across the fixed set, versus a 20 ms packet interval.
Answerer means were **18.627, 16.627, 17.408, 16.385 and 17.959 ms**.
All data quanta in the first three caller runs exceeded the 20 ms wall retention
reference. This is observed producer CPU demand, not an automatic diagnosis of
system-wide overload, scheduling policy, native-versus-Python cost or root cause.

The control/nominal-001 caller mean emulated work was essentially identical
(**512251/512247 cycles**), yet CPU means differed. The unapplied-pause control
also had substantially lower CPU and underrun counts than the nominal control.
This small fixed-order set is not CPU-matched or a statistical speedup estimate;
fewer underruns cannot be attributed solely to the recovery change. The much
smaller original model costs (12/9 ms) do not describe these measured caller
costs. A read-only host power snapshot was recorded; power settings were not
changed and do not identify the cause of the CPU difference.

The native result is narrower: current-state guards executed, fresh one-packet
input passed the real emulated firmware/LAPM/PPP path without observed payload
corruption/retraining, but **every strict timing verdict failed**. Caller recovery,
controlled producer-pause recovery, long soaks, transition faults, full-bandwidth
TCP/duplex and physical peers remain unqualified. Default recovery stays at 320.
Next isolate remaining sample-pump host CPU costs and non-CPU waits before
promoting a policy or changing fault eligibility.

Evidence: `artifacts/qualification/v34-rx-resume-analysis/summary.json`, both
five-command status files under `artifacts/test-results/`, and the individual
qualification directories. Existing failures and initial startup errors remain.
