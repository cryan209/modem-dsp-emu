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

## Offline Phase-3 waveform discriminator (2026-08-22)

`tools/p3_segment_probe.c` now runs the sibling streaming `p3_demod` over a
PCMU capture without touching the live harness. On the same 12--25 s window,
the native `run65.rx.ulaw` upstream gives a long coherent TRN region. The live
emulated caller output instead gives a short J/J-prime/TRN burst followed by
many low-confidence fragments; it does not reproduce an equivalent sustained
clean region.

This is a directional waveform discriminator, not a claim that the sibling
classifier is the Eicon receiver. It strengthens the wire-content diagnosis
and gives the next bridge experiment an objective acceptance check: the
caller-side generated Phase-3 waveform must become structurally coherent while
the answerer remains live.

## Synchronized sibling Phase-3 reset (2026-08-21)

The opt-in sibling analogue bridge was corrected for a distinct timing
problem: its Phase-3 state machine had been clocked from call setup, so by the
time Eicon overlay `0x026b` became active it could already be in Ja or waiting
for a later event. `EICON_V90A_PHASE3_START_S=<seconds>` now delays creation of
the sibling Phase-3 object and logs its internal TX/RX stages.

With a reset around `11.6 s`, the sibling followed the live Eicon downstream
through S/PP/TRN/Ja/DIL/CP, and the Eicon V90D answerer advanced from `0x00c2`
to `0x00c4` with quality `0x02af` and a 7200-bit/s ceiling over a 60-second
run. The Eicon V90A caller still held at `0x0095`. An earlier reset around
`9.3 s` regressed to caller `0x0092` and answerer `0x002c`.

This is the first bridge A/B showing that source phase alignment changes the
V90D response, but elapsed-time reset is not a fix because overlay residency
shifts with host pacing. The next bridge needs a live Eicon-state reset/hand-off
rather than a fixed wall-clock offset.

## State-triggered sibling reset (2026-08-21)

The subprocess bridge now exposes a one-shot reset method. The Eicon media loop
signals it on the first frame for which the configured replacement gate is
active, and the child reinitializes its Phase-3 object before processing that
frame. This removes the wall-clock ambiguity.

In a fresh realtime run the reset occurred at caller sample `92960`, exactly as
overlay `0x026b` became resident. The child then logged the expected
silence/S/PP/TRN/Ja progression, followed by S/DIL/CPt. The Eicon V90D
answerer advanced `0x00c2 -> 0x00c4`; the caller advanced only
`0x0092 -> 0x0094 -> 0x0095` and remained there through shutdown. The c4
quality/ceiling response is therefore reproducible with a state-triggered
source, but it does not yet provide the caller's missing `0x0095` receive
transition.

## Caller receive trace with synchronized Phase-3 bridge (2026-08-21)

The corrected caller-side sampler confirms that the V90A source remains live
at the remaining boundary. During the bridge's DIL/CP exchange,
`DM(0x0a92/0x0a93)` changed on each 20 ms sample, `DM(0x2119)=0x32ca`
selected the active producer, and `DM(0x2121)=-3` / `DM(0x20ed)=0x0200`
remained stable. The same run reached caller `0x0094 -> 0x0095` while V90D
reached `0x00c2 -> 0x00c4`.

This closes the remaining dead-producer and DAA/codec-transport leads for
this experiment. The unresolved boundary is the state-coupled APCM/DPCM
payload or the V90A receive decision that should follow it. The next trace
should be concentrated after `0x0095`, rather than changing the line codec
or forcing the transmitter selector.

## Post-`0x0095` caller window (2026-08-21)

A tightly gated capture beginning immediately after the reproducible
`0x0094 -> 0x0095` transition shows that the caller remains active rather
than frozen. Through the post-transition window, `DM(0x0a92/0x0a93)` keeps
changing, `DM(0x0e66)` varies widely, and `DM(0x2119)=0x32c4` with
`inner=0x003f` remains selected. The sharp boundary change is
`DM(0x2121): -3 -> -4`; `DM(0x20f7)=0x0578` remains fixed.

The `0x0095` wall is therefore an active receive/adaptation state whose
decision word changes at entry, not an unserviced DAA path or a zeroed
source. This makes `DM(0x2121)` and the associated caller LMS/decision
logic the next targeted emulation comparison against the 2185 behavior.

## Caller LMS shift A/B with synchronized bridge (2026-08-21)

The caller's opt-in LMS shift was tested at `-5` and `-6` while retaining the
state-triggered sibling Phase-3 reset. Both runs reproduced caller
`0x0094 -> 0x0095`; in both, the V90D answerer reached `0x00c2 -> 0x00c4`.
Lowering the caller step therefore changes neither endpoint's terminal outcome
in this coupled experiment. The observed `DM(0x2121): -3 -> -4` transition is
a firmware state change, but pinning nearby values is not the missing fix.

## Card-style DIL profile A/B (2026-08-22)

The sibling Phase-3 bridge now accepts the opt-in
`EICON_V90A_PHASE3_DIL_PRESET=courier|card` selector, using its measured
66T, descending-ladder profile instead of the default 125x12 descriptor.
With the synchronized reset, the bridge logged the complete S/PP/TRN/Ja,
DIL, CPt, and CP progression. The Eicon endpoints still ended at caller
`0x0094 -> 0x0095` and answerer `0x00c2 -> 0x00c4`.

The Eicon receiver therefore remains active but does not accept the response
as the next control transition even when the bridge's DIL timing/profile is
made card-like. Descriptor shape alone is not the missing correction; the
remaining seam is the live APCM/DPCM mapping response after DIL.

The alternative `measurement` profile (120×66T, full ladder) was also run
through the same synchronized bridge. It completed the sibling S/DIL/CP
sequence and again ended at caller `0x0095` / answerer `0x00c4`. The profile
branch is closed: changing DIL length or ladder shape does not open the V90A
receive transition.

## Reader-at-`0x0095` coupled A/B (2026-08-22)

The V90A reader selector was forced only while caller state `0x0095` was
resident, with the synchronized Phase-3 bridge still supplying the peer-
reactive waveform. The result remained caller `0x0095` and answerer
`0x00c4`. The local silence/reader selector is therefore not the missing
handoff, even after the bridge improves the V90D side.

## Phase-4 bridge event trace (2026-08-22)

The bridge now logs nonzero receive events after it enters Phase 4. In the
state-triggered loopback it recognized `R` (`0x00000001`) and then
`R-bar/TRN2d` (`0x00000006`), but no `MP`, `MP-prime`, `B1d`, or `DATA`
events. The Eicon answerer still reached `0x00c2 -> 0x00c4`, while the
caller remained at `0x0095`.

This localizes the incompatibility to Phase-4 mapping/demodulation or the
upstream CP response, not the DAA/PCMU transport.

## Bridge-side Phase-4 TX gain A/B (2026-08-22)

The bridge now exposes opt-in `EICON_V90A_PHASE3_TX_GAIN`, applied only to
the sibling-generated upstream PCMU stream. Both `+3 dB` (`1.4125`) and
`-3 dB` (`0.7079`) regressed the exchange before the late bridge boundary,
ending around caller `0x0092` with the answerer falling back in INFO. Neither
level produces MP or data mode; the default bridge level remains unchanged.

## Late proven-waveform splice (2026-08-22)

The independently validated sibling `live-tx.g711` waveform was spliced onto
the caller's transmit wire at 17 s, with its known data-capable offset
(`6.24 s`) selected. This preserved the native V.8/early exchange but still
ended at caller `0x0095` / answerer `0x00c4`. A later 20 s splice produced the
same result. A static data-capable waveform cannot substitute for the missing
live CP/mapping coupling.

## Live CP configuration trace (2026-08-22)

The bridge now exports the exact Phase-4 control frames it generates. In the
coupled `analog109` caller to `pri117` answerer run, it emitted CPt `drn=22`
and CP `drn=18`, both with `Sr=0`, `ld=0`, six constellations, upstream mask
`0x0fff`, and `trn1d_gain_q3_13=24854`.

The bridge then received `R` followed by `R-bar/TRN2d`, but no MP, MP-prime,
Ed, B1d, or DATA. The answerer stopped at `0x00c4` and the caller at
`0x0095`. This confirms that Phase 4 is initialized and transmitting a
concrete offer; the unresolved mismatch is the subsequent mapping
response/decoder compatibility, including CPt/CP constellation or training
interpretation, rather than failure to initialize Phase 4.

The native 2185-backed trace also reports CPt and CP lengths of 428 bits,
which is the two-constellation Table 14 form. A temporary bridge build tested
that compact form with (a) the measured interval-0/1 masks and (b) the native
odd-code/all-code mask convention, both with alternating DFI. Neither changed
the live result: caller `0x0095`, answerer `0x00c4`. Compact frame length and
the native mask convention are useful constraints, but neither is the sole
missing compatibility fix.

The Phase-4 counters then separated the remaining cases. With the normal
measured masks, 14,097 TRN2d frames were rejected as out-of-constellation. A
temporary all-128-ucode CPt mask removed that category entirely but produced
14,893 modulus-overflow frames and still zero TRN2d ones. Trying the observed
data-mode K=32 for the TRN2d demapper made no difference. The Eicon waveform is
therefore not merely using a sparse mask the bridge chose incorrectly, nor is
the problem only the CPt/CP K offset; DFI/phase or the answerer's actual
mapping state remains implicated.

A one-symbol Phase-4 frame-fill offset produced the same result (20,658
overflow frames, zero TRN2d ones). Raising the temporary demapper to the full
CP-style `D=drn+20` bound also failed (15,031 overflow frames). The observed
overflow is therefore not repaired by a simple six-symbol phase shift or by
choosing K=32/36/42; the Eicon-side mapping symbols are not the sequence the
bridge's mapper model expects.

## Full-modulus diagnostic and compact-CP combination (2026-08-22)

The diagnostic demapper was then widened to the full 128-ucode masks and the
full `D=48` receive interpretation (effective `K=42` after the six framing
bits). This removed both out-of-constellation and modulus-overflow failures:
over roughly 90,000 TRN2d symbols there were 13 recognized ones and zero
demap failures. The Eicon waveform is consequently a valid full-modulus
mapping stream at the PCMU/analogue boundary; the failure is not malformed
transport or an unusable codec signal.

That permissive receiver still did not see MP, and the endpoints remained
caller `0x0095` / answerer `0x00c4`. Combining the diagnostic full-modulus
receiver with a temporary native-shaped two-constellation CPt (alternating
DFI, all-code masks, 428-bit frame shape) produced the same result. These
experiments are diagnostic only and do not justify widening the production
V.90 masks or changing the negotiated K. They narrow the next investigation
to the CPt offer's exact wire construction/acceptance, including symbol
ordering, DFI phase, and the transition from CPt to CP/MP.

The native 2185 field set was also tested as a complete CPt override: `drn=9`,
upstream mask `0x1fff`, two constellations with alternating DFI, odd/all
masks, zero shaping and zero TRN1d gain. It produced the same endpoint states
and no MP (`TRN2d` failures remained out-of-constellation). Thus the native
frame metadata alone is not enough; the next comparison must be at the
modulated CPt symbol stream and its phase/state handoff.

## V90D producer-state sample (2026-08-22)

A read-only DM sample on the V90D answerer during the stalled run showed its
mapping-frame producer was active. DM `0x3fa7..0x3fac` cycled through signed
constellation amplitudes while internal `0x3fc2` advanced through `0x00b0` to
`0x00b2`; the endpoint then remained at `0x00c4`. The answerer is therefore
emitting training symbols while waiting, rather than failing to enter the
mapping producer or losing the DAA/codec bearer.

## Caller fed-RX codec audit (2026-08-22)

The caller's fed-input capture was enabled at the point where the Eicon DSP
receives each sample. For `analog109`, all 254,240 fed PCMU codewords matched
the corresponding signed-linear `line_rx_word` exactly against the harness's
µ-law decoder: 254,240/254,240 samples had zero error. The reactive bridge
consumes that same code stream before the line model. This rules out a
codeword/linear conversion defect at the DAA/SPORT boundary for the stalled
exchange; the remaining incompatibility is inside the V90A Phase-4/state
decision path.

The clean DFI A/Bs further constrain that path. With a temporary two-
constellation, all-ucode CPt and effective `K=42`, native alternating DFI
(`010101`) produced about 123,000 TRN2d symbols with zero demap failures but
no MP. Reversing the DFI (`101010`) was identical. An all-zero DFI prevented
the answerer from reaching its late `0x00c4` state. DFI ordering is therefore
required for the offer, but it is not the missing MP transition.

## U-code-floor diagnostic (2026-08-22)

The captured post-R-bar stream began at U-code 1 and never showed U-code 0 in
the observed boundary, so a temporary CPt mask containing only U-codes 1..127
was tested with the same full-modulus receiver. It still produced no MP and
left the endpoints at caller `0x0095` / answerer `0x00c4` (six initial
TRN2d-ones were recognized). The U-code floor is not sufficient evidence for
changing the production CPt masks; it may be a property of the Eicon-side
producer or of an unaccepted CPt offer.

## Forced-MP isolation probe (2026-08-22)

A diagnostic-only bridge build forced its transmitter to accept the digital
peer's MP event after 14,000 TRN2d symbols, without changing the wire-side
CPt/CP encoding. In that run the V90D answerer advanced from `0x00c4` to
`0x00c6` and reported negotiated speeds (`speedTx=0x2031`,
`speed=0x11e9`), whereas the ordinary run held at `0x00c4`. The caller still
held at `0x0095`, so this is not a protocol fix or a completed call.

This isolates the immediate coupled failure: the answerer and bearer can
finish once the analog side supplies the CP response, and the earlier DAA/
codec audit remains cleared. The missing live transition is specifically the
bridge's recognition of the answerer's MP (or an upstream state that prevents
that MP from being recognized), not simply answerer producer startup. The
forced event must not be promoted; the next work is to make the real TRN2d/MP
mapping decode and then verify the caller's `0x0095`→data transition.

## Learned peer constellation and caller status gate (2026-08-22)

The next diagnostic learned the six observed peer u-code sets during TRN2d,
then reused that learned map through MP and B1d. This is not yet a production
mapping change: it is a wire-derived experiment that avoids assuming the
caller-side CPt advertisement is the receiver's active constellation.

With the learned map, the bridge reached `stage=data`, recognized MP, and
completed all 48 B1d frames. The V90D answerer simultaneously reached
`0x00c6` and published `speedTx=0x2031`, `speed=0x11e9`. Returning to the
negotiated CP map for payload data reduced post-B1d demap failures from 12,246
to 6,473 in the same 30-second harness run, while B1d remained 48 frames
with 1,047 bit errors. This is a real Phase-4/data-boundary improvement, but
the payload mapping is still not correct enough for a completed bearer.

The Eicon caller nevertheless remained at `TrnProgress 0x0095` with
`Rstatus_ch=0`. A diagnostic hard pin of `DM(0x20EB)` bit 14 moves the caller
through `0x00b0 → 0x00b2 → 0x00b3`, confirming that `0x0095` is a caller-side
status/inner-state gate rather than evidence that the answerer is still at
the CP/MP boundary. The pin is a stand-in and must not be promoted. The next
work is to identify which genuine decoded Phase-4 result should cause the
Analog page to publish that status, while separately correcting the learned
payload map.

## 2185 biased-rounding A/B (2026-08-22)

The core already models the ADSP-2185N `BIASRND` control bit, but the Analog
kernel path had no way to exercise it. A temporary opt-in Analog A/B set
`DM(0x3ff3)` bit 14 before each SPORT frame. It did not improve the coupled
exchange: the answerer still reached `0x00c6`, while the caller remained at
`0x0095`; bridge post-B1d results were worse (`12,526` out-of-constellation
frames versus `6,469` without the bit). The control-bit difference is not the
current V90A wall, and the temporary hook was removed.

The state-`0x003f` result workspace is more informative. During the failed
run, `DM(0x103d)=0x000c`, `DM(0x103e)` varied, and `DM(0x103f)=0`, while the
inner handler is `PM(0x0a23)`. That handler requires a specific phase-4
result pattern before the inner machine can leave `0x003f`; the next target is
the result decoder/input sequence, not generic MAC rounding.

## `0x0095` state snapshot (2026-08-22)

A targeted trace sampled the caller at 160-sample intervals after the
Phase-4 exchange. At the outer `0x0095` transition, the inner state was
`0x003f`, its cursor was `0x1707`, `DM(0x20EB)=0x0100` (bit 14 clear), and
`DM(0x2104)=0x003f`; those values remained stable through the sampled window.
The inner cursor had already advanced `0x16b6 → 0x16c2 → 0x16ce` before the
outer `0x0094 → 0x0095` transition. Thus the remaining gate is a stable
caller-side status/result condition, not merely a frozen inner scheduler or a
missing answerer data state.

## Frame-aligned phase-4 workspace trace (2026-08-22)

The backend-specific analogue sampler was enabled for the caller while the
adaptive peer-map bridge was active. In the `0x0094/0x0095` window, the six
decoder inputs at `DM(0x0e4d..0x0e52)` were live and changing. The decoder
workspace showed:

```
DM(0x103d) = 0x000c       DM(0x103e) = changing result
DM(0x103f) = 0x0000       DM(0x104d) = 0x000c
DM(0x104e) = 0x103e       DM(0x2130/0x213a/0x213b) = changing
```

This rules out a dead phase-4 input ring and a missing decoder invocation. It
narrows the next comparison to the result-producing arithmetic/control path
(`PM 0x09fb -> 0x32a3 -> 0x3279`) or to the analogue symbols presented to that
path. The caller still stops at `0x0095`; the answerer reaches `0x00c4` but
does not provide a genuine caller data-mode result.

## Codec-rate and resampler A/B (2026-08-22)

The normal 9600-Hz internal codec path was rerun with both the default
windowed-sinc converter and the alternate Lagrange converter. Both reached the
same coupled boundary: caller `0x0095`, answerer `0x00c4`, with the learned
bridge active. Forcing the analogue codec to 8000 Hz instead made negotiation
fail much earlier at caller `0x0001` and answerer `0x0028`.

The 9600-Hz rate requested by the V.90A page is therefore necessary, but the
choice between the two host resamplers is not the current wall. This lowers
the probability of a basic DAA/codec-rate defect and leaves the phase-4
decoder/result path as the primary target.

## Raw V.90A TX boundary capture (2026-08-22)

The next boundary check captured the caller's raw 9600-Hz codec TX PCM through
`EICON_ANALOG_TX_PCM` during the same synchronized sibling-bridge run. The
physical TX was not empty: from approximately 9--17 s it stayed near 960--980
RMS, then went silent when the caller parked at `0x0095`. However, 40 ms
windows in the active interval showed only intermittent 2400-Hz concentration
and a broadband/noise-like waveform, rather than the sustained structured
Phase-3 tone/segment pattern present in the known-good analogue-client
capture. The answerer simultaneously reached `0x00c4`.

This places the present boundary after the page's TX producer and before any
claim about DAA companding: the codec-side sample stream is live, but the
selected V.90A waveform is not protocol-compatible. The sibling Phase-3
bridge can improve the answerer's state response, but it does not repair the
caller-side page-13 TX source/selector. The next implementation comparison is
therefore the V.90A TX producer/serializer selection at `DM(0x3fb4)` and its
state-coupled source, not the 9600-Hz resampler or mu-law conversion.

## V.90A TX mailbox scalar A/B (2026-08-22)

The generic TX mailbox interpretation was tested explicitly with the new
opt-in `EICON_ANALOG_V90A_TX_SCALAR=1` control. In this mode the kernel
dispatch consumes `DM(0x3fb4)` itself as the signed-linear TX word while the
normal path continues to dereference it as a pointer. With the synchronized
sibling Phase-3 source, the scalar run still ended at caller `0x0095` and
answerer `0x00c4`, matching the pointer-mode boundary.

The V.90A failure is therefore not explained by choosing the wrong generic
mailbox interpretation. The control remains diagnostic-only; the next
comparison must follow the page-local source/selector that supplies the value
published through that mailbox.

## V.90A TX producer trace correction (2026-08-22)

The raw-TX observation must be interpreted with the firmware producer trace.
While the caller is parked in its V.90A Phase-3 state, `Core8kRoutine`
(`PM 0x1706`) dispatches the page-local generator at `PM 0x292d`, copies the
three-word TX ring, and drains it to `DM(0x3764)`. The parked selector is
`DM(0x211a)=0x2996`; its producer stage `PM 0x32bf` selects the active
QAM pulse-shaper (`DM(0x2119)=0x32ca`) or its explicit silence arm
(`0x32c4`). The symbol buffer is filled by the page's pulse-shaper path.

Therefore the observed broadband TX is expected V.90A modulator output, not
evidence that the page should be forced to emit a 2400-Hz tone. The remaining
failure is the coupled protocol deadlock: the answerer holds a featureless
probe at `0x00b0` while waiting for the caller's reactive Phase-3 exchange,
and the caller's detector cannot advance on that probe. This supersedes the
previous raw-capture note's suggestion that the next fix should be a local
V.90A TX selector change. The next implementation target is a genuinely
reactive V.90D peer, including its state-coupled Phase-3 response.

## Coupled Phase-4 boundary with learned peer map (2026-08-22)

The learned-map bridge was rerun with the caller's documented pre-data status
conditions enabled only as a boundary probe. The caller advanced through
`0x0092 -> 0x0094 -> 0x00b0 -> 0x00b2 -> 0x00b6 -> 0x00c0`, while the V.90D
answerer reached `0x00c2 -> 0x00c4 -> 0x00c6` and published
`DATASTATEspeedTx=0x2031`, `DATASTATESpeed=0x11e9`. The bridge recognized MP
and completed all 48 B1d frames, but the caller then entered
`0x00c1 -> 0x00c3` and fell back to INFO.

This is the strongest current coupled boundary: the answerer and Phase-4
media path can complete their negotiated exchange, but the caller's own
rate/result decision still rejects the attempt. A follow-up hard probe of
`DM(0x254b)` did not change that branch, so it is not justified as a fix. The
next implementation target is the genuine V.90A `0x00c1` result/quality
producer and its mapping to the caller's status vocabulary.

## Native V90D mapping comparison (2026-08-22)

The preserved native 2185 capture (`artifacts/eicon-native-tower/run65`) and
the emulated answerer were compared at the outer-state transitions. Both
publish the same structural sequence after the CP boundary:
`0x00b0 -> 0x00b1 -> 0x00b2 -> 0x00b3 -> 0x00b6 -> 0x00c0 -> 0x00c2 ->
0x00c4 -> 0x00c6 -> 0x00c8 -> 0x00cc -> 0x00d0`. Their six-word mapping
blocks are populated at each transition, rather than remaining zero or
stalled. The exact signed values differ with the analogue exchange, as
expected; the emulated answerer also reaches `0x00d0` when fed the gold
native upstream capture.

This removes a simple V90D `0x00b0` hold, mapping-block clear, or DAA/codec
transport correction as the explanation for the live caller failure. In the
state-coupled loopback the answerer reaches `0x00c6`, while the bridge
recognizes MP and completes 48 B1d frames; the caller then takes its own
`0x00c1 -> 0x00c3` reject path. The next implementation target remains the
V90A phase-4 result/quality producer or the symbols that feed it. No status
pin or forced result is promoted because those only manufacture the missing
DSP decision.

## Fresh live reproduction with the reactive bridge (2026-08-22)

After supplying the bridge's `libspandsp` runtime path, a clean 35-second
loopback reproduced the boundary without any caller status or result pins.
The V90D answerer reached and held `0x00c6`, published
`Rstatus_ch=0xa600` (`CTS|DSR|speed_tx`), `DATASTATEspeedTx=0x2031`, and
`DATASTATESpeed=0x11e9`. The caller reached `0x0095` at 20.02 seconds and
remained there through shutdown. This is a repeatable live result, not an
artifact of the earlier one-way gold-upstream test: the answerer is receiving
the caller's bridge-generated reactive Phase-3/Phase-4 exchange.

The first attempt in the same batch failed at 20 ms because the external
diagnostic bridge could not load `libspandsp`; that was a harness launch issue,
not a modem result. The successful rerun used
`DYLD_LIBRARY_PATH=/Users/scottcryan/v90modem/spandsp-master/src/.libs`.

## V90D TX-level A/B against the caller result gate (2026-08-22)

The reactive answerer waveform is hotter than the preserved native 2185
capture, so the direct V90D transmitter was rerun at `EICON_V90D_TX_GAIN=0.5`
and `0.25`, with the same learned-map bridge and no caller pins. Neither
changed the caller's result: v90a still stopped at `0x0095`. The lower levels
also made the answerer stop earlier (`0x00c4`, versus `0x00c6` at unity), so
TX attenuation is not the missing correction and remains diagnostic-only.

## Phase-3 peer-capability A/B (2026-08-22)

The diagnostic bridge now exposes the two peer capabilities that the sibling
engine normally derives from INFO1d/INFO0d: high versus low carrier and the
digital modem's maximum transmit power. The default remains low carrier with
no cap, matching the previous bridge behavior.

Forcing the high carrier made the caller fail earlier at `0x0092` and sent the
answerer back to INFO, so the low-carrier choice is correct for this peer.
Applying a `-6 dBm0` power cap preserved the live boundary: the caller still
stopped at `0x0095`, while the answerer reached `0x00c6` and changed only its
published `DATASTATEspeedTx` from `0x2031` to `0x2030`. Neither capability
correction explains the V90A result rejection. The new environment controls
are diagnostic-only and are not enabled by default.

## DIL-preset A/B (2026-08-22)

Replacing the bridge's default measured-JA preset with the courier preset also
left the caller at `0x0095`. The answerer still reached `0x00c6` with
`CTS|DSR`, although its published `DATASTATEspeedTx` changed from `0x2031`
to `0x202d`. The DIL-to-CP selection is therefore active and sensitive, but
the preset choice is not sufficient to satisfy the caller's result decision.

The measurement preset was also tested live. It again left v90a at `0x0095`
while v90d reached `0x00c6` (`DATASTATEspeedTx=0x2035`). Across the default,
courier, and measurement profiles, changing the DIL-derived CP profile has
not crossed the caller's result gate; the next comparison must be inside the
caller-side phase-4 decoder/result path rather than another CP preset.

## Caller phase-4 result-path comparison (2026-08-22)

The caller was traced at PM `0x09fb` and `0x3279` in two runs: the genuine
reactive v90d loopback and the existing one-way gold-upstream diagnostic. The
same phase-4 result routines execute in both cases. In the one-way run,
diagnostic status conditions allow v90a to continue from `0x0095` through
`0x00b0 -> ... -> 0x00d0`; in the genuine loopback, the caller remains at
`0x0095` while the answerer completes its side of the exchange.

This distinguishes a missing caller-side result decision from a dead result
producer or a basic ADSP execution failure. The current evidence points to
the live decoded symbol/quality inputs (or their thresholds/mapping) failing
the `0x0095` gate. The status and result pins remain diagnostic only; the next
test should compare the phase-4 decoder inputs and quality accumulators before
attempting an emulator change.

## Phase-4 referenced-word comparison (2026-08-22)

The execution watch was extended to print the DM words behind the phase-4 DAG
pointers. At PM `0x3279`, both the reactive and gold runs have the same
structural inputs: `DM(I0)=0`, `DM(I1)=1`, and `DM(I5)=0x7600`. At PM `0x09fb`,
however, the gold run's `DM(I0)` sequence stays in narrow residual bands such
as `0x0e..`, `0x0f..`, and `0xf1..`, while the live reactive run produces
substantially wider values including `0xe405`, `0x4aec`, `0x379b`, `0x5813`,
and `0xe4ee`.

The phase-4 routines and their pointer structure are therefore intact, but
the live symbol/residual values reaching the v90a result gate are materially
different from the known-good input. This is the first direct evidence tying
the `0x0095` rejection to the analogue decoder's symbol/quality input rather
than to V90D's outer-state mapping, DAA transport, or the result routine's
ADSP execution. The next target is the decoder/equalizer path that produces
`DM(I0)` before `0x09fb`.

## Residual producer write trace (2026-08-22)

A write watch on `DM(0x0e4d)` shows that `0x09fb` is only the consumer. The
live caller writes this word from PM `0x36e0` and the repeated PM `0x36a0`
path, with values such as `0x385c`, `0x3700`, `0x3771`, and `0x37c7`; the
phase-4 transition later clears it through PM `0x0c2e`. The corresponding
`0x3279` path writes `DM(0x2130)` separately.

This identifies the next concrete inspection boundary: the PM `0x36a0/0x36e0`
producer and its analogue/equalizer inputs. No correction is justified yet,
because the producer is active and the current evidence does not distinguish
an incorrect live waveform from an emulator arithmetic defect inside that
producer.

## V90D equalizer-shift A/B (2026-08-22)

The direct V90D receiver's LMS shift was tested at `-5` and `-7` around the
stock `-6` (`DM(0x2042)`), with the learned reactive bridge unchanged. Both
runs left the answerer at `0x00c4` and the caller around `0x0092 -> 0x0094`,
with no improvement toward the `0x0095` boundary or data mode. The stock
equalizer shift remains; changing this receiver adaptation word is not the
missing correction.

## Runtime-overlaid phase-4 opcode trace (2026-08-22)

The DM watch now records the resident PM opcode and overlay alongside each
access. In the reactive run, the phase-4 producer writes `DM(0x0e4d)` from
`ppc=0x369f`, `op=0xb37c71`, while the earlier setup writes come from
`ppc=0x36df`, `op=0xb385c1` and `ppc=0x36e5`, `op=0xb37001`. Decoding the
ADSP opcode shows these are literal DAG2 stores (`DM(I4,M4)=0x37c7`, etc.),
not MAC instructions. All execute with `PMOVLAY=0`; the runtime PM page is
therefore supplying the residual values directly, and the earlier static PM
dump was from a different resident code image.

The coupled run again ended with v90a at `0x0095` and v90d holding `0x00c6`.
This narrows the live fault further: the producer/consumer control flow is
functioning, but the runtime-generated PM store stream supplies values such as
`0x37c7` where the gold path supplies narrow residual values. The next
comparison should trace PM writes or the page-loader inputs that generate the
`0x36df..0x369f` instruction stream, rather than changing the V90D outer-state
mapping.

## Phase-4 PM residency timing check (2026-08-22)

Late snapshots of PM `0x3680..0x36a0` taken at 12.23 s and 18.23 s both
show the ordinary resident image, including `0x369f=0x6800c3` and
`0x36a0=0x6800b3`. This does not erase the opcode-aware DM-write observation
of transient `0xb37c71`/`0xb385c1` stores: it means the instruction image seen
by the core is changing between snapshots or during a host/page operation and
then returning to the resident image. The remaining emulator question is now
the timing/ownership of those PM updates; a static PM dump is insufficient.
