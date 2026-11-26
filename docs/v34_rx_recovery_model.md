# Phase-controlled RX recovery model

This is a deterministic **scheduling/identity model**, not modem audio, native
DSP execution, PPP payload verification, source-PCAP timing or physical recovery
qualification. Production recovery policy is unchanged.

## Run

```sh
make build
.venv/bin/python tools/rx_recovery_probe.py \
  --output artifacts/qualification/my-new-rx-model.json
```

The output file must be new. Exit 0 means requested model observations completed
and identity/conservation invariants held, **not that every model budget passed**.
Exit 1 means failed invariants or an unapplied/unfinished requested pause;
argument/output-reuse errors return 2. The JSON records Python version and hashes
of the model, production endpoint and media-trace source.

## What the model does

It calls production `rx_ready()`, `wants_quantum()` and `service_transmit()` on
two `Call` objects. There are no sockets, firmware execution, signal samples,
codec work or real sleeps. Source packets carry unique synthetic identities;
new output can be published only after consuming a full received identity
quantum. Explicit prehistory seeds 320 RX identities and one prior TX packet
per peer. This starts an already-warmed stream, not initial SIP/modem training.

Both wire clocks and RX dispatch remain active. A producer-only pause is armed
between completed quanta after publication with a real queued identity packet
and at least 2 ms of future send deadline. This differs from the live
producer/RX-dispatch fault, which freezes both producer and incoming dispatch.
A fixed logical producer cost is elapsed simulated time, not measured CPU.

Each 1 ms grid step delivers arrivals, completes producers, services both wires,
then starts eligible production. Zero-cost output published in the final stage
can be sent at the next grid step. No send is allowed early; RX gating cannot
edit media/wire deadlines. The production sender's existing empty-queue
underrun accounting/reset behavior is left intact. Grid quantization and this
explicit event ordering are not native thread scheduling. The model polls RX
on the grid rather than executing selector retries; the legacy nominal hold
accumulator is reported separately and must not be read as elapsed wait.

Packet/input/output conservation, FIFO identities, a one-packet TX cushion,
zero RX substitution/loss and restored production prefill are checked even
under Python `-O`. In-flight production, queued output, network packets in
transit, unsent tails and right-censored RX holds remain visible. A timeout
fallback that would use missing RX input is recorded as an invariant failure;
the model never executes it using fabricated identities. Arrival reordering is
recorded as a failure, not sorted away by source sequence.

## Production gate versus test-only reference

With repetition disabled, production waits for 320 samples after the RX queue
runs dry, even if a whole 160-sample quantum is available. Continuing an
unheld stream requires only 160. Startup is distinct and still needs prefill.

The reference temporarily requests 160 **only for an already-started call with
observed model training progress `0x00d0`**. The endpoint object is restored to
320 immediately after each gate call, even on an exception. Training remains at
320. This reference exists only inside the model. A later, separate
[guarded native experiment](v34_data_rx_resume.md) has an explicit endpoint flag;
neither implementation changes default recovery behavior.

Do not reuse the endpoint's `data_mode` flag as proof of modem data mode:
qualification uses `--data-tx-buffer-ms 0`, so that flag's buffer-promotion branch
does not run, even when training progress is `0x00d0` and PPP is connected. The
model deliberately leaves `data_mode=False`. Any future native experiment needs
an explicit, current firmware/link-state guard and transition tests.

## Fixed controls and observations

The final 40-scenario matrix covers:

- Training and data labels, production and data-only reference.
- Logical producer costs 0/0, 12/9 and 17/17 ms.
- No pause, 20 ms and 100 ms producer pauses, with 5 ms delivery.
- Separate no-pause jitter controls with repeating 2/10/4/8 ms delivery delays.

All 40 observations completed with conservation invariants intact. All unstalled
and jitter controls met the model's zero-underrun/40 ms maximum observed silence
budget. There were **23 failing per-peer budget observations** across the full
matrix, retained in JSON. There is no source-clock-ppm qualification in this
short, quantized model.

For a 100 ms producer-only pause:

| Cost A/B ms | A gap/underruns, both policies | B production gap/underruns | B reference gap/underruns |
| --- | --- | --- | --- |
| 0/0 | 81 ms / 3 | 46 ms / 1 | 27 ms / 0 |
| 12/9 | 104 ms / 4 | 74 ms / 2 | 58 ms / 1 |
| 17/17 | 114 ms / 4 | 93 ms / 3 | 76 ms / 2 |

In the 12/9 case, A pauses at 112–212 ms. B has a whole real-to-the-model
identity quantum at **229 ms**; production resumes at **245 ms**, then sends its
output at **254 ms**. The reference resumes at **229 ms**, sending at **238 ms**:
**25 versus 9 ms** from available full input to output. All corresponding
training results are identical between policies.

The reference is not a universal improvement metric: it causes more frequent
RX waits (10 versus 5 episodes for B in this case). At zero producer cost, it
has **210 versus 145 ms** cumulative RX wait despite a smaller source gap.
Waiting totals are not computation cost or an instantaneous latency measure.
Neither whole-call 100 ms pause case passes both peers' model budgets.

### Eligibility boundary retained, not forced

An initial 18/18 ms grid design could not arm the pause while proving 2 ms of
future deadline. That receipt is retained as
`artifacts/qualification/rx-recovery-model/initial-18ms-grid.json` (exit 1).
The positive-application matrix uses 17/17 ms, while dedicated tests preserve
18/18 as an **unapplied-fault negative control**. No deadline is rounded forward
or eligibility waived to force application. This is a discrete-grid/floating
boundary observation, not a claim that real 18 ms producer work always prevents
fault injection. Model-development receipts were not real modem qualifications.

## Saved firmware observations anchoring the question

Closed traces from the prior `v34-sport-history-*` set were rechecked against
final underrun counters. Four retained snapshots show `0x00d0`, RX waiting, and
160 available samples below the 320 recovery prefill:

- History-off nominal 001 answerer: **3 of its 3** underrun snapshots.
- History-off producer-pause answerer: **1 of its 7** snapshots.

All incident snapshots were retained; older ordinary trace events were truncated
(377–952 events across the inspected files). These are gate-state observations,
not proof that the model reference would fix firmware pacing or explain every
source gap. No new live call or physical measurement was made in this step.

Evidence is `artifacts/qualification/rx-recovery-model/controls-final.json`
and `live-anchors.json`. Earlier development receipts remain alongside them.

## Decision boundary

The model isolates additional prefill delay without DSP CPU cost, packet loss,
stale repeats, invented input, larger buffers or deadline forgiveness. It also
shows why fewer underruns or shorter single-episode waits are not sufficient
qualification. A subsequent **[guarded, opt-in native firmware experiment](v34_data_rx_resume.md)**
preserves training/startup behavior and checks current data/link state under the
unchanged capture, clock, gap, lifecycle and exact payload contracts. All five
native timing verdicts failed, and requested producer pauses could not arm;
there is still no evidence to ship a new default RX threshold.
