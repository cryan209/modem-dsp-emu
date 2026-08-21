# V.90A/V.90D reactive bridge boundary

## Finding (2026-08-21)

There is no existing V.90 protocol implementation in this repository that can
be connected to the loopback harness. `tools/dmodem_v90_bridge.patch` is only
a resampler transition fix. `tools/eicon_mips_shim.py` implements the ADDSP
synchronous TX/RX mailbox and LAPM path for V.90D after the DSP has reached
data state; it does not generate the V.90 Phase-3 response. The V.90A helper
in `tools/dial_tikrnl_drive.py` is intentionally diagnostic: it can supply
PRBS, Ja, TRN1u, patterns, or a captured TXD0 sequence, but none is a reactive
peer implementation.

The mailbox contract is nevertheless clear:

* `DM(0x3FAD).bit15` requests a host TX word.
* V.90A consumes `DM(0x3F05)` only, with the oldest bit at bit 15; `PM 0x3D84`
  copies it into the analogue page's source ring.
* V.90D consumes a 21--42-bit datagram across `DM(0x3F05..0x3F07)`, with bit
  0 of TXD0 oldest.

The ADDSP V.90 guide (v5.3, §5.3.1) describes the non-V90D TXD0 path as a
host-fed 2400-Hz baud packet, with bit 15 oldest and the packet left-aligned.
Its V90D exception is the 21--42-bit TXD0..2 layout above. The guide defines
V90A in the page table and describes `speed_sel_V90_*` as applying to the V90D
transmitter or V90A receiver, but it does not provide a V.90A Phase-3 source
sequence or a protocol state machine. Therefore implementing the mailbox
alone cannot produce the missing analogue-side response.

Static source tests are negative. PRBS, V.90-shaped Ja, and TRN1u leave the
fresh firmware-backed loopback at caller `0x00C0` / answerer `0x00C2`. The
previously labelled “native TXD0 replay” used a native V90D answerer fixture,
not a V90A source oracle, so it remains only a wrong-role mailbox probe and is
excluded from the V90A source-fidelity conclusion. A dual-prime test reaches
`0x00D0`, proving the resident DSP pages and codec path can complete when both
directions already contain a valid Phase-3 history. This makes the missing
piece a coupled protocol/media exchange, not mailbox ownership, DAA scale, or
codec packing.

## Bridge contract for the next implementation

The bridge must be driven at the media tick and must have both endpoint state
and received Phase-3 symbols available before answering a TXD0 request. It
needs to:

1. decode the V.90D receive/control state and negotiated mapping;
2. maintain the V.90A TRN/Ja/CP and differential/scrambler history, rather
   than replaying one capture;
3. publish exactly one V.90A TXD0 word per asserted request, holding it until
   the DSP consumes it; and
4. generate the corresponding V.90D segment waveform/control response from
   the live V.90A symbols.

The current code can provide the mailbox timing and instrumentation, but it
does not contain steps 1, 2, or 4. Implementing a source generator alone
would repeat the already-negative replay experiments and should remain
opt-in until a state-coupled peer exists.

## Verification baseline

The clean, unprimed qualified loopback currently reaches caller `0x00B6 ->
0x00C0` and answerer `0x00C0 -> 0x00C2`, with no data mode. The latest paired
native TX mailbox replay has the same result. A future bridge is successful
only when a fresh unprimed run reaches `0x00D0` without state/status pins or
receive priming.

## Independent software-peer boundary (2026-08-21)

The sibling `/Users/scottcryan/v90modem` implementation was checked as a
reference, not modified. Its `v90_analogue_tx` unit tests pass, and its
`vpcm_loopback_test --all-tests` completes the V.90 Phase-4 sequence through
CPt, CP, MP, MP-prime, Ed, B1d, and data mode over G.711.

The current patched fast-JM binary was then run as a reactive V.90 peer against
the emulated Analog109 caller. RTP had no loss or substitution. V.8 completed,
the peer entered V.90 Phase 3, and the Eicon caller advanced to `0x00B3`. The
peer nevertheless timed out after 42,304 Jd symbols waiting for the caller's S
response; it did not reach data mode. This is a useful independent boundary:
the DAA/codec path carries a live V.90 exchange far enough to reach Phase 3,
but the late caller response and the Eicon V.90D response are not mutually
compatible. The sibling stack is a reference implementation for the bridge,
not yet a drop-in fix for the two-firmware Eicon loopback.

## Full-engine peer admission boundary (2026-08-21)

The sibling's full `sip_v90_modem` engine was also run as the answerer on a
local PCMU RTP path, with the Eicon Analog109 V.90A caller using the normal
kernel-dispatch harness. SIP/RTP were healthy: 234 packets and 37,440 samples
arrived with no loss, substitution, or duplicate packets. The call did not
reach V.90 Phase 3, however. The full engine classified the incoming V.8
exchange as FAX CNG twice (`status=7`) and hung up after retrying ANSam; the
Eicon caller remained in `TrnProgress=0x0001`.

This run is therefore not evidence for or against the V.90D Phase-3 waveform.
It establishes that the full sibling engine needs a V.8 admission/role
configuration compatible with the Eicon V.90A caller before it can be used as a
reactive V.90D control peer. The earlier fast-JM binary remains the only
independent peer run that reached the Phase-3 boundary.

## Peer-state selector coupling boundary (2026-08-21)

The kernel-dispatch Analog109 backend did not previously call the shared
`_exchange_v90_state()` hook used by the direct card backend. Consequently a
caller-side diagnostic that imported the answerer's live `TrnProgress` state
could never observe that state. The dispatch boundary now performs that
exchange after each emulated SPORT frame, and an opt-in
`EICON_V90A_TX_SHAPER_PEER_STATES` gate selects the V.90A reader only while the
imported peer state matches.

The seam was verified in a fresh loopback: the caller logged the answerer's
state transitions and armed the reader at peer `0x00b0`. This did not repair the
protocol exchange; the caller stalled at `0x0095` and never reached `0x00b3`.
The result rules out a missing state-feedback transport as the sole cause and
keeps the peer-state shaper diagnostic-only. The remaining fix is still the
protocol-aware V.90A source/control producer, not a selector pin.

The follow-up live Eicon-to-Eicon run used the same state export/import path
with the reader restricted to the caller's terminal pre-data states. The
answerer exported through `0x00b0`, and the caller imported that transition,
but the caller stalled at `0x0095` before the local `0x00b3` selector point.
Thus the live peer state is arriving at the kernel-dispatch boundary, yet it
does not create the structured caller TX needed to make the answerer advance.
This is a negative result for the peer-state gate, not a promotion of the
selector shaper into the normal path.

## Frame-synchronous software-peer adapter (2026-08-21)

`tools/v90_engine_frame_adapter.py` now exposes the sibling engine's exported
`me_rx_g711`/`me_tx_g711` calls as a strict 160-byte G.711 frame adapter. The
SIP endpoint can attach it with `EICON_REACTIVE_ENGINE=/path/to/binary`; the
Eicon card remains clocked and logged, while the adapter's live transmit frame
is substituted on RTP after the matching receive frame has been processed.
This is deliberately opt-in and is a control-peer seam, not a claim that the
two Eicon firmware roles now interoperate. The standalone adapter smoke test
consumed four PCMU frames and returned 640 bytes; focused repository tests
also pass. A fresh Eicon-to-Eicon data-mode result is still outstanding.

## Current serializer and native-answerer boundary (2026-08-21)

The fresh clean direct loopback reached caller `0x00b3` and answerer
`0x00b2`; it did not reach data. Inspecting the wire at the Phase-3 boundary
shows the direct V.90D answerer emitting a stable two-level alternating
waveform (decoded PCMU approximately `+988/-988`) for long windows. The native
`run65` answerer evolves its mapping waveform after the same phase instead of
holding that two-level pattern. Keeping the direct mapping block alive is
necessary for Phase-3 entry; releasing it restores the five-of-six zero
cadence and makes the caller stall earlier at `0x0092`. Holding the last
serializer sample or reading at the alternate frame boundary did not reach
data (the latter reproduced the earlier caller `0x00c0` / answerer `0x00c2`
boundary).

As a control, the native-MIPS V.90D answerer was run against the same
Analog109/kernel-dispatch caller. It booted and entered V.90 Phase 3, but the
caller remained in early Phase 3 for the bounded run. Therefore the stable
direct waveform identifies a genuine direct-emulation/mapping defect, while
the shared RTP/DAA path and the caller's live response timing remain separate
causes to test. The next implementation target is the state-coupled V.90D
mapping/source evolution, not another static waveform replay.

Enabling the existing direct-card `EICON_V90D_TX_PRBS=1` mailbox source did
not change the fresh loopback boundary: the caller still reached `0x00b3`
and the answerer `0x00b2`. The host mailbox was claimed and received a PRBS
datagram, so this is not simply an unserviced `DM(0x3FAD)` request. The
remaining missing behavior is downstream of that mailbox, in the live
mapping/source evolution and its response to the caller's Phase-3 symbols.

## Analog caller TX boundary (2026-08-21)

The direct loopback's `answerer.rx.ulaw` (the Analog109 caller's wire TX) is
active through INFO but becomes exactly PCMU silence after roughly 14 seconds,
when the caller is in V.90A Phase 3. The native `run65.rx.ulaw` remains active
and changes symbols at the corresponding phase. Enabling
`EICON_V90A_TX_PRBS=1` claims the V.90A TXD0 mark-fill stores but never sees a
TX request and does not change the silence, so the missing output is not fixed
by merely publishing a mailbox word.

The opt-in `EICON_ANALOG_USE_SPORT_TX=1` path was also tested. It produces
non-silent caller RTP, but disrupts V.8/INFO and leaves the answerer around
`0x0030`; the SPORT callback is therefore not a drop-in replacement for the
bearer sample. The remaining Analog109 issue is the timing/ownership boundary
that should transfer the page-13 analogue TX waveform into the 8-kHz bearer,
not a simple zero-output selector or TXD0 value.

A state-gated variant, `EICON_ANALOG_USE_SPORT_TX_AFTER_V90A=1`, was added
experimentally so the SPORT latch is selected only after page `0x026b` loads.
It leaves V.8 intact but still produces PCMU silence in the late caller
Phase-3 window and leaves the loopback at caller `0x0095`. This shows that the
SPORT callback is not receiving the missing page-13 waveform at that point;
the problem is upstream of the final bearer selector.

The existing `EICON_RXSAMPLE=1` receive-ring stand-in was also enabled on the
Analog109 caller. It leaves the state walk and late caller silence unchanged
(`0x0095`/`0x00b3` boundary), so the missing structured TX is not repaired by
repopulating the page's receive sample window.

## Reactive peer b3 selector control (2026-08-21)

The sibling fast-JM engine was attached as the answerer's live RTP wire peer,
leaving the Eicon Analog109 caller and its receive path in place. With the
normal V.90A selector, the sibling peer decoded the caller's Ja and then
timed out waiting for S; the caller reached only `0x00b3`. Repeating the run
with `EICON_V90A_TX_SHAPER=reader` restricted to local state `0x00b3` changed
both sides: the caller advanced `0x00b3 -> 0x00b6 -> 0x00b7 -> 0x00c0`, and
the live peer reached repeated Phase-4 MP frames.

This is a controlled positive for the b3 reader handoff: it enables a real
late V.90A response path, rather than merely adding energy. It is not a
data-mode result—the sibling peer is only a control reference, and the Eicon
V.90D answerer's own generated response is still not being used in this A/B.
The next correction must make the Eicon V.90D mapping/control exchange
compatible with the reader-produced response while preserving this b3
handoff.

The remaining PCMU microcode-table switch was tested against this positive
control (`EICON_V90D_PCMU_UCODE_TABLE=1`, reader only at `0x00b3`). It
regressed the exchange: the caller stopped at `0x0095` and the answerer at
`0x00b0`, before the b3 handoff. The default table path therefore remains the
better-qualified answerer configuration; this low-level table restoration is
not the missing late V.90 control correction.

An additional cadence probe inserted 200 ms on / 50 ms off gaps into the
answerer's generated Phase-3 wire from 14--30 seconds while retaining the b3
reader control. It regressed before the handoff (`0x0095` caller / `0x00b0`
answerer), so coarse gap insertion is not a substitute for native
mapping-frame evolution.

Finally, a recorded dynamic downstream from the same reactive-peer run was
fed through the Eicon answerer's normal TX path while retaining the live b3
reader caller. The caller reached `0x00c0`, but the Eicon answerer remained in
early V.90 (`0x006e`). This does not disprove the waveform—the file is not
state-synchronized to the Eicon answerer's receive machine—but it confirms
that a time-aligned recording cannot stand in for the missing live mapping
feedback.

The same reactive recording was then selected by the Eicon answerer's live
`TrnProgress` using `EICON_TX_FILE_STATE`, with separate source windows for
`0x00b0` through `0x00c2`. This state alignment probe regressed even earlier
(`0x0095` caller / `0x00b0` answerer). A reference waveform still cannot
replace the answerer's protocol-generated mapping response; the source must
be computed from the current bidirectional symbol history.

The late Analog109 source-ring trace confirms the repetition is not caused by
an empty or frozen ring. At local `0x00b3`, `DM(0x2119)=0x32ca` and the
`DM(0x3740..0x3753)` ring continues to change while `DM(0x376c)` advances
through it. However, the ring values are only small residual-scale samples,
and the emitted wire settles into the short periodic pattern seen by V.90D.
This puts the missing evolution in the page-13 source/control calculation
before the reader, not in the final RTP/DAA selector.

## Upstream source-scale sampler (2026-08-21)

A focused sampler was added around the same b3 reader run to distinguish a
stalled source from a scale/control problem. At caller sample 216080, while
the local state was `0x00b3`, it reported:

```text
DM(0x20f0) = 0x0001       ; source gate enabled
DM(0x2119) = 0x32ca       ; live reader selected
DM(0x211f) = 0x13d6       ; reader output scale
DM(0x2120..0x2127) = 636d, fffb, 1737, 4650, fffb, 4167, 7080, 1743
DM(0x3fca) = 0x209c
DM(0x0a92..0x0a94) = 09a4, 0a97, f796
DM(0x3740..0x3743) = 000f, 0001, 0003, fffb
DM(0x376c) = 0x374d
```

Across the following samples, `DM(0x0a92..0x0a94)` changes substantially and
`DM(0x376c)` walks the ring, while `DM(0x20f0)`, `DM(0x2119)`, `DM(0x211f)`,
and the sampled `DM(0x2120..0x2127)` context remain fixed. This independently
confirms that the QAM producer and reader are executing, but the selected
reader's emitted values are residual-scale before they reach the bearer. It
does not yet prove that `DM(0x211f)` is wrong: the low amplitude may be a
protocol residual that should evolve from the answerer's mapping feedback.
The next A/B is therefore a bounded scale-only probe, with no change to the
normal path unless it improves the live V.90 state boundary.

The scale-only A/B used `EICON_V90A_TX_SCALE=0x4000` while retaining the b3
reader. It changed the timing and terminal response but did not reach data:
the caller reached `0x00b6 -> 0x00c0` at 20.680 s and the answerer reached
`0x00b6 -> 0x00c0` at 18.980 s, then remained at c0 through the 34-second
window. The unscaled reader run continued `0x00c0 -> 0x00c2` on the answerer.
Thus amplitude is a real sensitivity in the late estimator, but a simple
reader gain does not supply the missing mapping/control sequence. The scale
override remains diagnostic-only and is not a candidate default fix.

## Dual reactive-engine control (2026-08-21)

As a bridge-boundary control, both SIP directions were attached to the live
fast-JM V.90 engine while retaining the Eicon Analog109 caller and PRI117
answerer DSPs. This deliberately tests whether a second reactive media engine
can supply the missing bidirectional response without changing either Eicon
firmware image. It did not reach V.90: the caller remained at `0x0025` and the
answerer reached only `0x0028` before the 40-second shutdown.

This is a negative compatibility result, not evidence that RTP or the DAA is
broken. The earlier one-sided fast-JM control still reaches the Eicon caller's
`0x00b3` boundary when the native b3 reader is selected; attaching the engine
to both directions changes the V.8/INFO admission itself. A useful reactive
bridge therefore needs an explicit V.90 event translator compatible with the
Eicon roles, rather than blindly replacing both RTP transmit directions with
an independent sibling state machine.

## Phase-gated direction split (2026-08-21)

The bridge was then made phase-gated: it clocks the sibling engine from setup,
but replaces RTP only after the local Eicon V.90 overlay is resident. This
preserves native V.8/INFO admission and separates the two directions.

* Answerer-only replacement (`0x026a`) preserved admission and reached the
  usual late boundary: caller `0x00b7 -> 0x00c0`, answerer `0x00b1 -> 0x00b2`.
* Caller-only replacement (`0x026b`) broke INFO earlier: caller `0x0073 ->
  0x0092`, answerer `0x0024 -> 0x002c`.
* Replacing both directions broke INFO as well: caller `0x0073 -> 0x0092`,
  answerer `0x0024 -> 0x002c`.

The first result confirms the phase-gated seam itself is sound and that the
answerer-side reactive substitution does not explain the earlier V.8 failure.
The latter two results show that sibling-generated analogue-side media is not
compatible with Eicon's V.90A admission/source boundary. A successful bridge
must translate Eicon's live Phase-3 state and source representation, not simply
substitute the sibling engine's PCMU frames.

The answerer-only capture adds one useful peer-side detail: the sibling engine
itself did progress through analogue Ja, Sd/S-bar/TRN1d, DIL, and CPt recovery
after the Eicon V.90D overlay became resident. The Eicon answerer still stopped
at `0x00b2`, while the Eicon caller stopped at `0x00c0`. Thus the gated bridge
is not failing because the sibling engine remains in V.8; it is failing at the
representation/phase compatibility between sibling-generated media and the
Eicon receive machines.

## b3 full-modulator branch A/B (2026-08-21)

The proven b3 reader selection was combined with a hard, b3-gated change of
the transmitter variant `DM(0x211a)` from the conditional modulator `0x2996`
to the full modulator `0x29fe`. This regressed the pair to caller
`0x00b1 -> 0x00b3` and answerer `0x00b1 -> 0x00b3`, rather than the reader-only
`0x00c0`/`0x00c2` terminal. The remaining transmitter-variant choice is
therefore not a safe substitute for the missing response; both selector pins
remain diagnostic-only.

## b3 native control-word A/B (2026-08-21)

The selector watch showed that the emulated record path writes
`DM(0x20e9)=0x0310` at b3, whereas the archived native 2185 selector trace
holds `0x1340`. A hard b3-gated pin of `DM(0x20e9)=0x1340`, combined with the
proven reader selection, produced the same terminal result as the reader-only
run: caller `0x00b7 -> 0x00c0`, answerer `0x00c0 -> 0x00c2`. The control-word
difference is real, but correcting that word alone does not alter the live
mapping exchange; it remains diagnostic until the other state/history inputs
that generate the native vector are recovered.

### Correction: native selector CSV audit

The preceding comparison used the wrong numeric interpretation for the
archived selector CSV and is withdrawn as native evidence. The CSV fields are
decimal: at b3, `artifacts/native-v90a-selector.csv` reports
`DM(0x20e9)=832` (`0x0340`) and `DM(0x2119)=12996` (`0x32c4`), matching the
emulated selector CSV. The `0x1340`/`0x32ca` tuple belongs to earlier records in
that capture, not b3. The hard `0x1340` pin was therefore a negative control
based on a misread native trace, and the reader override is a reactive-peer
diagnostic only—not a confirmed 2185 fidelity correction.

## Late-state PCMU table A/B (2026-08-21)

The direct answerer's staged PCMU V.90 table was restored only once its local
state reached `0x00b3`, leaving the qualified default table in V.8/INFO. With
the caller's b3 reader retained, this still regressed the pair to caller and
answerer `0x00b1 -> 0x00b3`; it did not reach the `0x00c0`/`0x00c2` terminal.
The PCMU table mismatch is therefore not isolated to the late estimator, and
the global or late table restoration remains diagnostic-only.

## Valid-upstream plus b3-reader control (2026-08-21)

The known-good `run65.rx.ulaw` upstream recording was combined with the b3
reader override while leaving the caller's receive DSP live. This particular
run retrained during INFO (`caller 0x002e -> 0x0030`, answerer
`0x0024 -> 0x002c`) and never reached V.90. Because the recording was not
phase-aligned to this run's V.8/INFO timeline, it is inconclusive for caller
RX-versus-source attribution and is not evidence for a new emulation defect.

## Synchronized downstream with held b3 (2026-08-21)

The caller-only receive prime was rerun with the documented `run65.ulaw`
timing, adding a held `0x00b3@18.54-23.06` milestone alongside the `0x00b0`,
`0x00c0`, and `0x00d0` anchors. This is the strongest replay control for the
receive-side phase-drift hypothesis: it keeps the known-good Phase-3 segment
under the caller while the live V90D answerer reacts.

It did not improve the live exchange. The caller stayed at
`0x0094 -> 0x0095` and the answerer at `0x0024 -> 0x002c`; neither endpoint
reached V.90. Since the same held-segment mechanism previously corrected the
offline caller equaliser, the remaining failure is not simply the caller
training on the wrong late replay segment. This still does not isolate the
DAA/codec from the Eicon V90A source/control path, because only the caller's
receive media was replaced and the answerer remained live.

Capture: `artifacts/loopback/caller.endpoint.log` and
`artifacts/loopback/answerer.endpoint.log` (the harness output directory is
overwritten on each run).

## Instrumented answerer-only reactive peer (2026-08-21)

The answerer-only phase-gated bridge was rerun with the sibling engine's
diagnostic snapshot enabled. The sibling did not fail at admission: it
decoded analogue Ja, accepted the DIL descriptor, completed Sd, S̄d, and
TRN1d, and emitted S events. Its V.90 trace then rejected the received S
structure (`rejected_p3_structural`, followed by `rejected_ratio rx/tx=0.002`)
and resynchronized to WAIT_JA. The Eicon endpoints ended at caller
`0x0037 -> 0x0038` and answerer `0x00b0 -> 0x00b2`.

This is the clearest live bridge boundary so far: the reactive peer reaches
late Phase 3 and produces S traffic, but the Eicon V90A/V90D pair does not
accept that traffic as its native S exchange. The result makes a missing
event/format translation at the Eicon source boundary more likely than a
failure to clock the peer or a basic DAA/codec failure. It is still a
compatibility finding, not a promoted emulation correction.

Capture: `artifacts/loopback/answerer.endpoint.log`.

## Caller-only reactive source after b3 (2026-08-21)

The sibling engine was then allowed to replace only the caller's transmitted
PCMU frames, beginning at local `TrnProgress >= 0x00b3`. V.8/INFO and the
earlier Eicon V90A source remained native; the answerer remained the native
PRI117 V90D. The caller advanced through `0x00b3 -> 0x00b6 -> 0x00c0`, but
the answerer stopped at `0x00b2` and its upstream-quality plateau remained
`0x0033`.

This rules out a generic live/reactive waveform as the missing ingredient.
The Eicon V90D receiver needs the Eicon-compatible V90A source encoding,
phase, and response timing; a sibling engine's late TX stream is not a
drop-in analogue source even when activated at the correct outer state. The
state gate is retained as a diagnostic control and is not enabled by default.

Capture: `artifacts/loopback/caller.endpoint.log` and
`artifacts/loopback/answerer.endpoint.log` (overwritten by the next harness
run).

## Peer-state-coupled native upstream bootstrap (2026-08-21)

The native upstream recording was selected from the live V90D answerer's
exported `TrnProgress`, rather than from the caller's local state. This
corrects the timing mismatch in the preceding replay: the answerer held its
own corresponding native segments while the caller supplied them.

The result advanced the V90D substantially: `0x00c2 -> 0x00c4 -> 0x00c6`,
with `DATASTATEspeed` published and upstream quality rising to `0x11e1`.
The caller did not yet reach data mode because its receive side was still
live against the emulated downstream. This is the strongest positive control
so far: the DAA/PCMU/RTP path can carry a peer-state-aligned V90A upstream
well past the normal c2 wall, while the local-state version stalled at b2.
The missing production behavior is therefore a live V90A source coupled to
the V90D peer state, not a static source table or generic codec gain.

Capture: `artifacts/loopback/{caller,answerer}.endpoint.log` from the
peer-state run (overwritten by the next harness run).

## Bidirectional peer-state replay control (2026-08-21)

The receive replay was given the symmetric peer-state selector and run
together with the peer-state-coupled native upstream. The answerer again
reached `0x00c2 -> 0x00c4 -> 0x00c6` and published `DATASTATEspeed`, proving
that both replay directions traverse the ordinary PCMU/RTP and DAA paths
without packet loss. The caller nevertheless remained at `0x0095`, even
when the native `0xb0` downstream segment was held while the caller waited.

This is a useful separation of responsibilities: the emulated V90D can reach
data-state signaling when supplied with a peer-state-aligned native upstream,
so its codec/serializer path is capable; the V90A caller still needs the
correct pre-`0xb0` phase history and cannot be bootstrapped by late native
segments alone. The new `EICON_RX_PRIME_SYNC_PEER_STATE_FILE` gate is
diagnostic-only and is not a production fix.

Capture: `artifacts/loopback/{caller,answerer}.endpoint.log` from the
bidirectional control (overwritten by the next harness run).

## Explicit pre-b0 RX anchors still alter the caller source (2026-08-21)

The native downstream replay was re-anchored continuously across the caller's
`0x0092`, `0x0094`, and `0x0095` windows before the late `b0` segment, while
the caller TX remained peer-state-coupled to the native upstream. This was
intended to preserve the pre-`b0` phase history that the fixed-offset replay
had previously supplied.

It instead caused the answerer to fall back during INFO; the caller stopped at
`0x0095`. In contrast, the same peer-state TX replay with the caller RX live
advanced the answerer to `0x00c6` and `DATASTATEspeed`. Therefore the RX
replay is not a neutral observation point: changing the caller's received
history changes its V90A source/control output enough to destroy the valid
upstream bootstrap. The production target is a coupled V90A receive/source
implementation, not independent RX and TX waveform replays.

## Sibling implementation seam (2026-08-21)

Inspection of `/Users/scottcryan/v90modem` confirms that the available sibling
V.90A implementation cannot be used as a frame-level waveform oracle. Its
working path is a coupled Phase-3 object: `v90_analogue_phase3_rx()` consumes
the downstream G.711 stream, raises Sd/Jd/DIL events into the transmitter, and
`v90_analogue_phase3_tx()` produces the upstream samples from that same state.
The module then hands its CP/Phase-4 state to the analogue-side data path.

The loopback adapter in this repository exposes only `me_rx_g711()` followed by
`me_tx_g711()` for each 160-sample packet. It has no event or V.90 state bridge
at that boundary. Consequently, loading the sibling engine as a generic
reactive peer starts an independent V.8/V.90 state machine and breaks INFO;
the phase-gated experiment can preserve admission but rejects the Eicon's
Phase-3 S structure. This is an integration limitation, not evidence that the
DAA/PCMU/RTP path cannot carry the signal.

The justified implementation direction is therefore a narrow V90A coupling
seam: expose the Eicon's received Phase-3 milestones (or decode them in the
emulator), feed those milestones into a stateful V90A source, and keep the
source's timing/scrambler/history continuous across the pre-b0 transition.
Raw native segment replay remains useful as a diagnostic oracle, but it cannot
be promoted to the production path because changing the caller RX history
changes the caller's transmit bootstrap.

## Scoped sibling analogue-role probe (2026-08-21)

The frame adapter now has an opt-in role parameter scoped to the sibling
library's `me_init()` call, so `ME_V90_ROLE=analogue` does not alter the Eicon
endpoint's own role. Running that sibling analogue engine in the caller's
media process still failed during V.8 (`status=4`); the Eicon caller never
loaded V90A and the sibling remained in its independent V.8 state.

This is a timing/integration negative, not a DAA or codec diagnosis. The
sibling engine is clocked for every 160-sample frame even while its output is
held behind the local-state gate, and its independent V.8 processing perturbs
the Eicon media thread before the phase boundary. A useful next adapter must
either start the sibling at a synchronized V.90 phase or run it out of the
Eicon media thread; simply selecting its analogue role is insufficient.

A second run with a 1000 ms transmit buffer and 200 ms receive hold made the
failure earlier (`caller 0x0025`, `answerer 0x0028`) rather than recovering
V.8. Extra RTP buffering therefore does not make the in-thread sibling a
usable reactive peer; it changes the measured timing without supplying the
missing synchronized V.90 history.

## Late two-way replay still perturbs the caller bootstrap (2026-08-21)

As a final replay control, the caller RX remained live until its expected c0
window; only then was native downstream introduced, while the answerer used
peer-state-selected native upstream. The caller nevertheless stopped at
`0x0095` after the TX replay entered its earlier b0 bucket, and the answerer
stopped at `0x00b2`. Delaying RX replay does not make a state-selected native
TX recording neutral: the caller's transmit history must remain continuously
reactive from before b0.

## V90D b0 quality pin control (2026-08-21)

For a separate Eicon-side control, the answerer's `DM(0x2117)` quality was
hard-pinned to the native-like `0x0156` only while V90D outer state `0x00b0`
was active. The pin was observed and held, but the state walk remained the
same: answerer `0x00b0 -> 0x00b1 -> 0x00b2 -> 0x00b3 -> 0x00b6 -> 0x00c0 ->
0x00c2`, caller `0x00b6 -> 0x00c0`. This does not open data mode, so the
remaining failure is not a single b0 quality threshold or DAA gain gate.

## V90D late-output gain control (2026-08-21)

The answerer's page-14 output was then boosted by +8 dB from outer state
`0x00b0` onward, leaving V.8/INFO and the caller untouched. This regressed the
pair: caller stopped at `0x0095` and answerer remained at `0x00b0`, versus the
qualified baseline's caller `0x00c0` / answerer `0x00c2`. The weak-looking
late waveform is therefore not repaired by level alone; no DAA/codec gain
change is justified.

## Subprocess-isolated analogue peer (2026-08-21)

The sibling analogue-role engine was then moved behind a separate process with
the same 160-byte synchronous frame protocol. This removed the earlier timing
interference: V.8 and Eicon V90A admission completed, and replacement became
active at local `0x00b3`. The result was caller `0x00c0` / answerer `0x00b2`,
with no sibling Phase-3 progress or data-mode transition.

This separates the two failure modes. The in-thread adapter was unsuitable as
a timing experiment, but isolating it does not make its analogue signal/state
format compatible with the Eicon V90A/V90D exchange. The missing work remains
an Eicon-compatible protocol translator, not a Python scheduling or RTP
buffering correction.

## Native late-segment hold as a directional control (2026-08-21)

Holding the native V90D downstream recording through the answerer's local
`0x00b0`, `0x00b1`, and `0x00b2` windows, then releasing the native `b3/b6/c0`
segments, produced the first useful directional improvement. The caller left
its otherwise repeatable `0x0095` stall and reached `0x00b0 -> 0x00b3`; the
answerer then advanced through `0x00b0 -> 0x00b1 -> 0x00b2 -> 0x00b3 ->
0x00b6 -> 0x00c0 -> 0x00c2`. The caller's upstream was also replaced with
native V90A material from its `b3` window onward, but the answerer still did
not reach data mode.

This is evidence that the late waveform's segment shape and timing matter, but
not that a static replay is a fix. The answerer can consume the held
downstream far enough to reach `c2`, while the caller's source remains coupled
to receive history and the pair never establishes the final data transition.
The next useful experiment is to select the caller's native upstream segment
from the answerer's exported state at the synchronized `b3` boundary. If that
also stops at `c2`, the missing behavior is a live V90A protocol/source coupling
primitive rather than a local DAA, codec, or single-state quality threshold.

## Peer-state-synchronized upstream replay (2026-08-21)

The caller was then kept on its live source until `0x00b3`; from that point,
its native upstream replay was selected from the answerer's exported outer
state. The answerer simultaneously used the native downstream hold that had
previously reached `0x00c2`. This run left the caller at `0x00b3` and the
answerer at `0x00b2`, so selecting the upstream segment from the peer's live
state did not recover the earlier `c2` advance.

The result rules out a simple local-state/replay-start ordering error. The
native segment control is useful for locating the sensitivity, but the final
transition still requires a continuously coupled V90A source and receive
history; static or peer-state-indexed packet replay is not sufficient.

## Symmetric native mailbox replay control (2026-08-22)

To separate the V90D generator from the V90A response, the native `run65`
EADSPDM2 mailbox capture was replayed on both endpoints: V90A TXD0 words on the
caller, and V90D TXD0/TXD1/TXD2 datagrams on the answerer. The caller reached
`0x00c0` and the answerer reached `0x00c2`, exactly the same late boundary as
the caller-only native replay. Replacing the answerer's generated mailbox
source with native 2185 words therefore does not open `c2 -> c4 -> c6` in this
loopback; the decisive missing behavior remains the coupled phase-3 exchange,
not just one endpoint's mailbox generator.

## c2 quality pin control (2026-08-22)

The answerer's `DM(0x2117)` quality was then hard-pinned to the native-like
`0x11e1` specifically in local state `0x00c2`, while the caller supplied the
peer-state-selected native V90A mailbox source. This did not produce the
`c2 -> c4 -> c6` transition. Instead, the answerer took a different late
branch, `b0 -> b2 -> b5 -> b6 -> b8 -> ba`, while the caller settled at
`0x00b8`.

The quality value is therefore not a safe missing threshold to inject: it is
an input to the state/control decision, and fabricating it changes the branch
rather than restoring the native exchange. This further favors a missing
V90D mapping/control response over a single DAA/codec level correction.

## Late V90D wire gain A/B after mapping-frame trace (2026-08-22)

The c2 mapping-frame trace showed the emulated six-word frame at about 4.6 dB
above the native c2 capture. A narrowly gated `-4 dB` correction was applied
to the answerer's emitted V90D sample from local `0x00c0` onward, with the
caller still using peer-state-selected native V90A mailbox words. The result
was unchanged: caller `0x00b7 -> 0x00c0`, answerer `0x00c0 -> 0x00c2`.

The internal mapping-frame amplitude difference is therefore not corrected at
the final DAA/codec output level. The likely fault is upstream in the mapping
producer, estimator inputs, or V90A response history; the gain override remains
diagnostic-only.

## Native-upstream bootstrap release (2026-08-22)

The caller was fed the native `run65.rx.ulaw` upstream only through 25 seconds,
then released to its live V90A source while the answerer remained fully live.
The bootstrap moved the pair to caller `0x00c0` / answerer `0x00c2`, but the
exchange did not continue after release. This confirms that getting the V90D
out of its initial deadlock is insufficient: the live V90A source must continue
to produce the peer-specific Phase-3 response as the answerer's mapping state
changes. A time-bounded native bootstrap is not a fix.

## Native-MIPS answerer admission control (2026-08-22)

The live recovered V90A caller was paired with the native-MIPS/2185-backed
PRI117 answerer using the loopback's V.8 admission override. This control did
not reach the V90D overlay: the answerer stopped at INFO/V.8 `0x004f`, while
the caller eventually parked at `0x0092`. It is therefore inconclusive for the
late mapping exchange and does not distinguish the DSP implementations; the
native tower still needs a separately qualified V90 admission path before it
can serve as a direct late-phase oracle.

## Caller 0x0092 silence-window control (2026-08-22)

The existing transmit shaper was forced to the silence writer only while the
caller held outer state `0x0092`, matching the V.90 quiet-window requirement;
all earlier and later states retained the firmware source. This did not open
the exchange. The answerer fell back during INFO (`0x0024 -> 0x002c`) and the
caller remained at `0x0092`, so silence at that local state is not a sufficient
deadlock breaker and is not a candidate default change.

## Peer-state-selected native wire surrogate (2026-08-22)

The closest available reactive-waveform surrogate selected `run65.ulaw` on the
answerer and `run65.rx.ulaw` on the caller from the answerer's exported live
`TrnProgress`, holding each native segment while that state remained active.
The answerer advanced through `0x00c2 -> 0x00c4 -> 0x00c6` and published
`DATASTATEspeed`; the caller still did not complete the exchange. This confirms
that peer-state feedback is sufficient to drive the V90D side when supplied
with native-shaped upstream, but it does not synthesize the caller's matching
receive/source history.

Adding a symmetric RX replay from startup broke V.8/INFO, so replaying both
directions is not a production bridge. The result is retained as a diagnostic
boundary: the missing implementation must carry the peer's phase-3 mapping
feedback into the live V90A source without replacing early media.

## Phase-gated peer-state RX handoff (2026-08-22)

The downstream replay was then introduced only after the caller's V90A timing
window (`17.5s`), with its segment selected from the answerer's exported state;
V.8/INFO remained live. This avoided the startup admission failure, but the
caller still did not advance while the answerer reached `c6` and published
data-state speed. Late receive-history replacement is therefore not enough
either: the V90A source must be generated from the same live mapping feedback,
not merely paired with a correctly timed downstream recording.

## Mapping-frame event export (2026-08-22)

The V90D event export now includes the complete six-word `DM(0x3fa7..0x3fac)`
mapping frame at the same sample boundary as the state, result, quality, and
TX-mailbox fields. This is diagnostic-only and no endpoint imports the words.
It gives the next bridge experiment the actual page-14 residue/serializer input
to correlate against the b2 history divergence, instead of treating outer
`TrnProgress` or a replayed TX mailbox as a substitute for the mapping
exchange. Python compilation and the focused 49-test suite pass (35 skips).

The first live capture after this change produced a six-word frame in
`/tmp/v90d-events-new.json` (`mapping_frame` length 6), confirming the export
at the actual media boundary. The bounded 22-second control reached caller
`0x0071` and answerer `0x007a`; it did not reach V.90 data and is not used as
protocol evidence.

## Sparse live source-vector sampling (2026-08-22)

The Analog kernel DM sampler now accepts `EICON_ANALOG_DM_EVERY=N`; the
default remains every bearer frame, while a stride reduces observer overhead
for realtime calls. A stride-32 capture reached the ordinary wall without
timing distortion: caller `0xc0`, answerer `0xc2`. It showed that the V90A
producer is active rather than frozen: `DM(0x2120)` remains initialized,
`DM(0x2122/0x2125)` changes through `0x0094`, `0x0095`, and `0x00b3`, and the
`DM(0x0a92..0x0a94)` generator ring continues changing through `0xc0`.

This moves the fault boundary away from a missing/empty source producer. The
remaining live mismatch is the generated APCM/DPCM waveform/control content
or its phase relationship to the V90D estimator. The capture was diagnostic
only; no source values were pinned or imported.
