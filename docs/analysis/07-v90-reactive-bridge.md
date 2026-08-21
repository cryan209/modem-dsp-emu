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

## Phase-4 PM write-ownership correlation (2026-08-22)

The focused run armed both `PM(0x369f)` write logging and the phase-4
execution/`DM(0x0e4d)` watches. The PM watch saw only the initial zero-fill;
there was no runtime firmware PM store at `0x369f`. Later, the core fetched
`0xb37c71` at `ppc=0x369f` and immediately wrote `0x37c7` to `DM(0x0e4d)`,
while the following `pc=0x36a0` instruction fetched `0xb37491`.

Thus the transient opcode is not explained by a normal ADSP PM write path.
The remaining candidates are host-side direct PM memory mutation that bypasses
`WWORD_PGM`, or an emulator memory-lifetime/aliasing defect. The next code
audit should instrument direct PM bulk writes and verify the program/data
array boundaries before changing the modem algorithm.

## V90A-resident PM force A/B (2026-08-22)

An opt-in frame-boundary force restored `PM(0x369f)=0x6800c3` and
`PM(0x36a0)=0x6800b3` only while the V90A overlay was resident. This avoids
corrupting the shared V.8/INFO addresses; the ungated version correctly failed
startup and was discarded as an invalid control. The gated run preserved the
normal startup path but still ended at caller `0x0095`, with the answerer at
`0x00c6`. The transient PM image is therefore not, by itself, the sufficient
correction. The force remains diagnostic-only.

## V90A PM-fetch force A/B (2026-08-22)

The diagnostic was strengthened to override the PM fetch result itself for
`0x369f` and `0x36a0`, rather than merely reseeding memory at the frame
boundary. This directly suppresses any transient host-side PM mutation during
instruction fetch. The coupled run still ended at caller `0x0095` while v90d
held `0x00c6`. The transient opcode is therefore not causal for the result
gate and is removed from the primary suspect list; the residual/input path
remains the target.

## Bridge PCMU encoder correction A/B (2026-08-22)

The sibling bridge's outbound PCMU helper was corrected from a 14-bit
`+33`/`8159` formulation to the repository's verified 16-bit G.711 mapping
(`+0x84`, clip `32635`). The coupled run remained at caller `0x0095` and
answerer `0x00c4`; the bridge ended in `TRN2d` with no MP. Wire analysis also
showed no 2400 Hz-dominant frames in the caller's `0x0092/0x0095` windows,
while the gold analogue-client reference is tone-dominant. The encoder
discrepancy is real but not sufficient; the missing tone is upstream in the
sibling Phase-3 TX stage or its state handoff.

## V90D serializer sampling-phase A/B (2026-08-22)

The direct answerer's page-14 bearer sample was then taken from the frame half
instead of the normal continuation half using
`EICON_V90D_TX_READ_PHASE=frame`. This tests the remaining simple host-boundary
hypothesis that the serializer's six-word mapping cursor is sampled one harness
half late. The coupled run still ended at caller `0x0095`; the answerer reached
and held `0x00c6` with `Rstatus_ch=0xa600`, `DATASTATEspeedTx=0x2031`, and
`DATASTATESpeed=0x11e9`.

Changing the read phase therefore does not repair the caller's phase-4 result
decision. The bearer sampling boundary is not the current correction target;
the remaining mismatch is in the state-coupled mapping-frame content or the
caller symbols/results that consume it.

## V90D alternate SPORT execution-model A/B (2026-08-22)

The same coupled run was repeated with the direct answerer using
`EICON_EXECUTION_MODEL=sport` instead of the legacy continuation model. The
endpoint boundary was unchanged: caller `0x0095`, answerer `0x00c6`, with
`Rstatus_ch=0xa600`, `DATASTATEspeedTx=0x2031`, and `DATASTATESpeed=0x11e9`.

The captured mapping-frame statistics were also unchanged: the emulated
`DM(0x3fa7..0x3fac)` values still reached approximately `+/-32256` in
`0xc2..0xc6`, versus approximately `+/-3900` in native 2185 `run65`.
Changing the host execution chronology is therefore not sufficient; the
remaining defect is in the mapping producer's numeric inputs/arithmetic or
the live analogue symbols it receives.
## Native-like V90D speed-word A/B (2026-08-22)

The direct PRI backend gained an opt-in core-level diagnostic,
`EICON_V90D_SPEED_PIN=STATE:TX[:RX]`, so the negotiated speed words could be
held after the firmware's own stores. Pinning the answerer to the native
`run65` values (`0x2022` / `0x11ec`) changed its published rates as intended,
but the coupled run still ended at caller `0x0095` and answerer `0x00c6`.

The six-word mapping-frame distribution was unchanged, including values near
`+/-32256` in the emulated `0xc2..0xc6` window. Datagram width/rate selection
is therefore not the source of the numeric mapping divergence. The speed pin
is diagnostic-only and remains disabled by default.
## V90D mapping producer source A/B (2026-08-22)

The new opt-in `EICON_V90D_MAP_TRACE=period:after:limit` trace records the
six receiver-side words at `DM(0x10ae..0x10b3)` together with the six words
published at `DM(0x3fa7..0x3fac)`.  The coupled harness was run with:

```text
artifacts/loopback-v90a-maptrace-late-20260822
```

The answerer trace shows a sharp boundary at the V.90D mapping phase:

```text
state=00c0 source=0f40 0f40 0f40 0f40 0f40 0f40
state=00c2 source=0f40 0f40 0f40 0f40 0f40 0f40
         published=1680 db00 d700 fee8 5a00 e580
state=00c6 source=0f40 0f40 0f40 0f40 0f40 0f40
         published=eb80 fd70 fc30 4600 ed80 05a0
```

At `0x00c0`, the published block is still the small `0x0f40/0xf0c0`
pattern.  On entry to `0x00c2`, the source vector is unchanged but the
published block immediately becomes a moving six-word stream, and it remains
stateful through `0x00c6`.  This rules out the currently watched source vector
as the immediate cause of the late numeric divergence.  The next target is
the state-gated mapping producer input/control path between the fixed source
vector and the serializer block; changing the DAA/codec boundary or the
published rate word is not yet justified by this evidence.
## V90D c2 mapping intermediate path (2026-08-22)

Disassembly of the PRI117 V.90D overlay shows that `PM 0x2a52` is only an
indirect dispatch:

```text
2a51  I4 = DM($203a)
2a52  CALL (I4)
```

The live trace identifies the state-dependent path:

```text
state 00c0: DM(203a)=3db9, DM(0b07)=0000
state 00c2: DM(203a)=3e48, DM(0b07)=3e5e
             intermediate DM(1e7d..1e82)=7200 9600 2d00 ee80 5a00 c100
             published    DM(3fa7..3fac)=0b40 9600 2d00 ee80 5a00 c100
```

`PM 0x3db9` copies the fixed `DM(0x10ae..0x10b3)` vector directly.  In the
late path, `PM 0x3e48` calls the worker through `DM(0x0b07)` and then calls
`PM 0x28a6`, which copies `DM(0x1e7d..0x1e82)` into the mapping block.  The
intermediate vector changes continuously throughout `0x00c2` and is the
immediate source of the late mapping values; the first published word differs
only because the serializer is concurrently consuming the block.

This narrows the implementation target from the generic mapping block to the
state-selected worker at `PM 0x3e5e` and the inputs it consumes.  It also
explains why forcing the speed words or changing the serializer phase had no
effect: those controls occur after this worker has produced the vector.
## V90D c2 worker input capture (2026-08-22)

The follow-up live trace captured the state-selected worker's control words
while the answerer held `0x00c2`:

```text
worker=3e5e worker-in=001e/ffff work=0000/000e/fffc
```

Here `worker-in` is `DM(0x1e4f)/DM(0x0b0a)` and `work` is
`DM(0x10b4)/DM(0x207c)/DM(0x0dff)`.  These values remain fixed across the
moving `DM(0x1e7d..0x1e82)` vector.  The c2 mapping stream is consequently
being generated from the worker's internal/history state and received-symbol
processing, not from a changing negotiated speed word or the fixed
`DM(0x10ae..0x10b3)` mapping seed.  The next A/B should compare or seed the
worker history at the c0-to-c2 handoff; a codec-level gain change would not
target this boundary.
## V90D generator-dispatch A/B (2026-08-22)

The opt-in `EICON_V90D_GENERATOR_PIN=0x00c2:0x3db9` test held `DM(0x203a)` at
the early mapping-copy routine after c2.  The pin was confirmed active at the
live state transition.  Compared with the clean learned-map baseline
(caller `0x0095`, answerer `0x00c6`), the pinned run ended at caller `0x0094`
and answerer `0x00c4`.

The c0-to-c2 dispatch switch is therefore required for the answerer's forward
progress; reverting it is not a viable correction.  The investigation stays
inside the `0x3e48 -> DM(0x0b07) -> 0x3e5e` worker/history path, with the
dispatch itself left at the firmware-selected value.
## V90D worker write watch and native-MIPS differential (2026-08-22)

The bounded c2 write watch names the active writers in the direct backend:

```text
DM(0x1dd0..0x1dd2), DM(0x10b4), DM(0x207c), DM(0x0dff) <- PM(0x3e5f..0x3e72)
DM(0x1e7d..0x1e82) <- PM(0x28a4..0x28a5)
```

The worker clears and rebuilds its internal words on every mapping pass, then
the intermediate vector is consumed immediately. This rules out a stale
history block or an absent c2 writer in the direct emulator.

An attempted same-caller differential with `--answerer-native-mips` was not a
valid waveform comparison: the native-MIPS endpoint remained at
`TrnProgress=0x0000` and never entered the V.90 exchange. It is retained as a
launch/integration failure, not evidence against the direct V90D waveform.
## V90D late TX phase A/B (2026-08-22)

The existing frame-boundary read (`EICON_V90D_TX_READ_PHASE=frame`) reduced
the coupled bridge's B1d error count modestly (1047 to 1035), so a one-sample
phase discrepancy was tested with the opt-in
`EICON_V90D_TX_DELAY=0x00c2:1` probe.  With the frame read plus that delay, the
answerer stalled at `0x00c2` and the bridge remained at `hunting Ri`, never
reaching Phase 4.

The improvement from frame-boundary sampling is therefore not explained by a
simple late one-sample shift.  The default continuation read remains intact;
the next target is the generated c2 vector's shape/history, not an output
queue delay.
## V90D c2 source/history ring snapshot (2026-08-22)

The source-ring trace shows the actual c0-to-c2 handoff.  At c0 the local
worker rings are empty:

```text
ring=0000 ... 0000
history=0000 0000 0000 0000 0000 3db9
```

On the first c2 frame they become populated:

```text
ring=0003 000d 0002 0005 0008 0000 0000 0000 0008 0000 0000 009d
history=9600 2d00 ee80 5a00 c100 3db9
intermediate=7200 9600 2d00 ee80 5a00 c100
```

This confirms that the late mapping vector is driven by a real state/history
handoff and small decoded control values, rather than a missing SPORT sample
or an empty mapping mailbox.  The direct backend's PCMU-law selection, DAA
law pointer, bulk adapter, and serializer reads have already had separate
negative or qualified A/Bs; the next correction must preserve this c2 ring
initialization while making its worker output match the native 2185 waveform.

## Runtime residual-producer execution trace (2026-08-22)

The earlier `EICON_WATCH_PM_WRITES=0x36a0:0x36e0` probe was not a runtime
producer trace: it reported page-memory staging loads from the overlay loader
(`PM 0x064d`).  It is therefore not evidence that the residual producer wrote
or failed to write anything.

A bounded execution watch at `PM 0x36a0` and `PM 0x36e0` corrected the
instrumentation.  In the live caller, `0x36a0` executes both during the early
page setup and later with the phase-4 producer context (`CNTR=4`,
`I4=0x0e4e`, `I5`/`I7` walking the receive/history buffers).  `0x36e0` runs
from `0x36df` with the corresponding runtime buffer pointers and the
phase-4 state active.  The coupled result remains caller `0x0095` and
answerer `0x00c4`/`0x00c6` depending on the bridge timing.

This confirms that the producer path is executing against live receive/history
data; it does not justify patching the PM words or pinning a residual value.
The next comparison remains the operands and outputs of this runtime path
against a valid native 2185 phase-4 capture, rather than another static PM
image or a DAA/codec change.

## V90D mapping amplitude A/B (2026-08-22)

The native `run65` comparison showed approximately `+/-0x0f00..0x1000` in
the c2-c6 mapping words, versus approximately `+/-0x7e00` in the direct
worker.  A default-off host-publication probe,
`EICON_V90D_MAP_SCALE=STATE:SCALE`, tested whether that difference was only a
numeric output gain:

| scale from c2 | caller | answerer |
|---:|---|---|
| `0.125` | `0x0095` | held `0x00c2` |
| `0.25` | `0x0095` | held `0x00c2` |
| default | `0x0095` | `0x00c4`/`0x00c6` |

Both trims are negative.  Reducing the published mapping amplitude does not
make the caller pass its phase-4 result gate and also prevents the answerer
from making its normal c2-to-c4/c6 progress.  The native/emulated amplitude
difference is therefore a symptom of the wrong vector/history, not a simple
post-generator gain error.  The probe remains diagnostic-only and disabled by
default.

The `0.125` result was repeated with the qualified global
`V90_ANALOGUE_LEARN_PEER_MAP=1` control enabled:
`artifacts/loopback-v90a-mapscale0125-learned-20260822`.  The clean learned
baseline reaches answerer `0x00c6`; the scaled run reaches only `0x00c2`, so
the negative result is not an artifact of using the wrong bridge mode.

## V90A MP-source selection A/B (2026-08-22)

The learned-peer bridge was rerun with `V90_ANALOGUE_MP_USE_CP=1`, making its
MP use the negotiated CP map rather than the learned peer map.  The result was
unchanged: the caller remained at `0x0095`, while the answerer reached
`0x00c6` and published `speedTx=0x2031`, `speed=0x11e9`.

This removes the bridge's MP map source as the immediate cause of the caller's
result-gate failure.  The remaining discrepancy is in the caller's decoded
phase-4 result sequence or the waveform that feeds it, not the DAA/codec
boundary or the answerer's outer mapping state.

Capture: `artifacts/loopback-v90a-mp-use-cp-20260822/`.

## Caller result-register ownership correction (2026-08-22)

A bounded caller write watch on `0x206d`, `0x206e`, `0x103e`, and `0x20eb`
corrected the earlier address assumption.  During the live V90A attempt there
were no writes to `0x206d` or `0x206e`; those addresses are not the caller's
active phase-4 result registers in this overlay.  The changing result value
is written to `DM(0x103e)` by `PM(0x0a17)` and its companion workspace/count
word `DM(0x103f)` is written by `PM(0x0a32)`.  `DM(0x20eb)` changes only at
the record unpacker (`PM(0x33e7)`).

The caller still ends at `0x0095`, so the next trace must follow the
`0x0a17 -> 0x0a23` result handler and its `0x103e/0x103f` operands.  The
`0x206d/0x206e` path is removed from the caller-side diagnosis; no code change
is justified from that stale address mapping.

Capture: `artifacts/loopback-v90a-result-writewatch-20260822/`.

## Caller phase-4 residual evaluator execution trace (2026-08-22)

The follow-up execution watch on `PM(0x0a17)`, `PM(0x0a23)`, and
`PM(0x0a32)` shows the caller's active result path is a live residual
evaluator, not a dormant or skipped handler.  `PM(0x0a23)` is entered from
the inner scheduler at `PM(0x3393)` with `I0` walking the six-word circular
residual buffer `DM(0x0e48..0x0e4d)`; its operands and accumulator state change
on each pass.  `PM(0x0a17)` then publishes the derived result into
`DM(0x103e)`, and `PM(0x0a32)` updates the companion count/work word
`DM(0x103f)`.

The coupled result remains caller `0x0095` / answerer `0x00c6`.  This moves the
next comparison one level upstream: the residual buffer and its producer at
`PM(0x0c2e)` must be compared against a valid native phase-4 response.  No
status pin or result-word patch is justified while the evaluator is consuming
changing live values.

Capture: `artifacts/loopback-v90a-result-execwatch-20260822/`.

## Caller phase-4 residual-buffer producer trace (2026-08-22)

The next bounded trace watched writes to the six residual-evaluator slots
`DM(0x0e48..0x0e4d)` while retaining the learned-peer coupled baseline. The
slots are populated by several DSP loops rather than a single bridge-side
assignment. The observed active writers include `PM(0x36d7)/0x36df`,
`PM(0x3684)/0x3689`, and `PM(0x369a)/0x369f`; the evaluator-side copy/read loop
is `PM(0x0c27)/0x0c2e`, with `I1` stepping through `0x0e48..0x0e4d` and `L1=6`.

This separates residual production from the later result publication. It also
means a global DAA/codec gain correction would conflate multiple producer
loops and is not justified by the current evidence. The next comparison is
the producer input/state against the native 2185 capture, especially around
the first writer loop, before changing either the analog boundary or the
result gate.

Capture: `artifacts/loopback-v90a-residual-producer-20260822/`.

## Caller phase-4 residual producer input trace (2026-08-22)

An execution watch on the residual writers distinguishes their setup and live
contexts. Early `PM(0x36d7)/0x36df` activity uses the initialization pointers
around `I0=0x3f34` and `I4=0x3f30`. In the active phase-4 result path,
`PM(0x3684)/0x3689` and `PM(0x369a)/0x369f` run with `I0=0x165c` and `I4`
stepping over `0x0e48`/`0x0e4d`; their operands and accumulators vary between
passes. The live residual vector is therefore sourced from phase-4 workspace
state at the point of evaluation, rather than being a direct read of the
codec/RXSAMPLE ring.

This is a boundary finding, not yet a correction: it narrows the native-2185
comparison to the phase-4 workspace producer and its source state. Changing
codec gain or replacing the result gate would bypass that evidence.

Capture: `artifacts/loopback-v90a-residual-inputs-20260822/`.

## Native-waveform answerer control (2026-08-22)

The known-good native analogue waveform was injected only at the PRI117
answerer's receive path, through the normal loopback RTP/codec boundary:
`EICON_RX_PRIME=artifacts/eicon-native-tower/run65.rx.ulaw:12.4:50:13.0`.
The V90D page advanced through its terminal data-side state (`0x00ea`) and
published non-zero speed/status fields. The live Analog109 V90A caller,
receiving the answerer's resulting wire output, still stopped at `0x0095`.

This is an independent boundary control: the DAA/PCMU transport and V90D
serializer can carry a valid analogue V.90 waveform, but the caller's own
reactive V90A exchange does not produce the response sequence needed to pass
its phase-4 result gate. No codec gain or V90D output patch is justified from
this control.

Capture: `artifacts/loopback-v90a-answerer-native-prime-20260822/`.

## Native versus emulated V90D result-word differential (2026-08-22)

The captured state CSVs provide a quantitative check on the late V90D worker.
In native `run65`, the c2 result words are near zero over 151 samples:
`v90d_result_lo` RMS `1.6` and `v90d_result_hi` RMS `1.2`. The current direct
emulator's c2 snapshot is `0x0000/0x03ff` (only one sampled c2 row in that
capture, so this is a point comparison rather than a distribution).

By c6, where both captures have sustained samples, the native result-pair
RMS is about `10.5k`, while the emulated pair is about `19.1k`. This supports
the existing worker/history boundary: the emulated late result vector has the
right changing, nonzero character but materially different magnitude/content.
It is not evidence for a codec gain change, because the comparison is after
the V90D page's state-selected worker and result publication.

The c2 sample-count limitation remains explicit; a longer c2 hold is needed
before promoting a numeric correction.

## Sustained c2 worker trace correction (2026-08-22)

The answerer's c2 rate predicate was held diagnostically with the hard
`EICON_V90D_RATE_PIN` hook while the worker writes were watched. The entry
transient (`result_lo=0x0000`, `result_hi=0x03ff`) was not representative: as
the c2 inner state settled, the emulator produced
`result_lo=0x000f`, `result_hi=0xfff8`, exactly matching the native c2 result
words. The c2 hold eventually released into c4, confirming the worker was
still running and updating its history.

This corrects the sparse-point comparison above: c2 initialization/result
publication is not the immediate numeric defect. The remaining differential
is the c2-to-c6 history evolution and the caller's interpretation of the
resulting response, not a missing c2 worker write or codec transport error.

Capture: `artifacts/loopback-v90d-c2-held-worker-20260822/`.

## Caller phase-4 gate disassembly (2026-08-22)

Disassembly of the analogue V90A overlay makes the live result requirement
explicit. `PM(0x0a23)` calls the six-word evaluator at `PM(0x09fb)`, then
checks the published evaluator words:

```text
0a28: AR = DM(0x103e)
0a29: AF = AR XOR 0xffff
0a2b: AR = DM(0x103f)
0a2c: AR = AR AND 0x0003
0a2d: AR = AR XOR 0x0001
0a2e: AF = AF OR AR
0a2f: IF EQ -> success
```

The caller must therefore produce `DM(0x103e)=0xffff` and
`(DM(0x103f) & 3)=1`; the changing live `0x103e` values observed in the
coupled run are not a low-amplitude threshold failure. They are evidence that
the six residual samples do not form the required sign/pattern result. This
also explains why numeric mapping gain A/Bs were unproductive: the gate is
pattern-valued. The next comparison is the six-sample residual pattern from a
working/native response versus the bridge-fed caller, not another scalar gain
or status pin.

## Live caller residual-pattern capture (2026-08-22)

A frame-aligned caller capture of `DM(0x0e48..0x0e4d)`, `DM(0x103e/0x103f)`,
and the detector words confirms the gate failure directly. Near the sustained
`0x0095` stall, observed six-slot sign patterns include:

```text
110000  010000  110100  101100  000001  010011
100001  100100  011001  110100  000011  001011
```

The slots carry large signed values, but their signs are not the required
`000111`/`111000` reversal pattern. Correspondingly, `DM(0x103e)` changes
through ordinary values while `DM(0x103f)=0` and the detector count does not
advance. This is direct evidence that the bridge-fed response has the wrong
phase/content pattern before the result gate; increasing mapping amplitude or
pinning the result word would not address the failure.

Capture: `artifacts/loopback-v90a-residual-pattern-20260822/`.

## Pre-gate waveform differential (2026-08-22)

The received caller wire was measured before the residual evaluator. In the
failed coupled capture, 0.5-second windows around the `0x0095` dwell have
RMS about `700..1,180`, zero-crossing rate about `0.49..0.50`, but no stable
carrier: the strongest spectral bins move from roughly `94 Hz` to `1.09 kHz`
and `2.90 kHz`. The native 2185 transmit capture has structured carriers in
the corresponding Phase-3 windows and a clear `1,332 Hz` component in its
late reversal segment.

Thus the random six-slot residual signs are a consequence of the received
V90D response content, not a caller-side scalar threshold. The live V90D
worker/history path is still producing energy, but not the native structured
phase/reversal waveform that `PM(0x2fd1)` expects.

Capture comparison: `artifacts/loopback-v90a-residual-pattern-20260822/` and
`artifacts/eicon-native-tower/run65.ulaw`.

## Native-waveform control TX differential (2026-08-22)

The answerer-native-prime control was aligned to the native `run65` capture
by the measured approximately `0.82 s` state-walk offset.  The state machine
walks the same c2/c4 boundary, but the answerer TX waveform does not match the
native 2185 TX waveform.  Around native `22.5..24.5 s`, the control output is
approximately `2.5k` RMS and is dominated by strong `2/4 kHz` components,
where the native output is approximately `0.65..0.91k` RMS with structured,
changing carriers.  The later `25.5..26.5 s` windows converge in overall
energy, but still have different spectral peaks.

This control is useful because the receive side is the same known-good native
upstream recording and the answerer still reaches its terminal data-side
state.  Therefore, “the emulated V90D reached the state” is not equivalent to
“the emulated V90D generated the native response.”  The remaining correction
target is the c2-to-c6 TX waveform/history generation (including any
state-selected serializer inputs), rather than DAA/codec transport or the
V90A scalar result gate.

Capture comparison: `artifacts/loopback-v90a-answerer-native-prime-20260822/`
and `artifacts/eicon-native-tower/run65.ulaw`.

## Native-prime receive-history alignment A/B (2026-08-22)

The native-waveform answerer control was repeated with the receive recording
cursor aligned 1:1 (`EICON_RX_PRIME=...:12.4:50:12.4`) instead of the prior
600 ms offset (`...:12.4:50:13.0`).  Removing the offset made the coupled
exchange worse: the caller stopped at `0x0094` and the answerer stopped at
`0x00c2`, whereas the offset control reached the answerer's terminal
data-side `0x00ea`.

The receive-file cursor/DAA timing is therefore not a sufficient correction
for the c2-to-c6 waveform mismatch.  The useful offset control remains a
transport sanity check, but the unresolved defect is still in the emulated
V90D state-selected worker/history path under the live peer response.

Capture: `artifacts/loopback-v90a-answerer-native-prime-aligned-20260822/`.

## Phase-3 bridge upper-bound A/B (2026-08-22)

The coupled source was given a diagnostic upper bound so it remains
frame-clocked but stops replacing the caller's TX at a selected local V.90A
state.  Handing back to native Analog109 TX at `0x00c0` was ineffective because
the caller still stalled at `0x0095`.  Moving the handoff to the observed
`0x0095` gate also left the result unchanged: caller `0x0095`, answerer
`0x00c6`.

This removes the simple explanation that the Phase-3 bridge merely overwrites
the caller's native Phase-4 TX after the gate.  The upper-bound hook is kept
diagnostic-only; the unresolved failure remains the received V90D response
content and its caller-side phase-4 interpretation.

Captures: `artifacts/loopback-v90a-phase3-bridge-until-c0-20260822/` and
`artifacts/loopback-v90a-phase3-bridge-until-095-20260822/`.

## V90D mapping-clear control (2026-08-22)

The direct loopback was repeated with `EICON_V90D_TX_BLOCK_HOLD=0`, restoring
the firmware's per-frame clear of `DM(0x3fa7..0x3fac)`.  The caller stopped at
`0x0092` and the answerer at `0x00b0`, compared with the qualified held-block
baseline of caller `0x00c0` and answerer `0x00c2`.

This confirms that preserving the six-word mapping block is necessary for
Phase-3 progress, but it is not sufficient for data mode.  The clear cadence
is therefore not the remaining correction: the next target remains the
state-coupled V90D mapping/source response that the V90A receiver must decode.

Capture: `artifacts/loopback-v90d-clear-control-20260822/`.

## Reactive-source timing boundary (2026-08-22)

The sibling Phase-3 source was deliberately delayed until caller state
`0x0092`, and separately until `0x0073`, while native Analog109 handled the
preceding exchange.  Both controls were negative: the caller reached
`0x0092`, but the PRI117 answerer remained in INFO around `0x002c` and never
entered the V90D exchange.

This narrows the required coupling window.  A reactive source must participate
before `0x0073`, during V90A page entry/early Phase 3; arming it only at the
caller residual/result boundary cannot repair the answerer's admission.  It
does not identify the source waveform correction, so the bridge remains
diagnostic-only.

Captures: `artifacts/loopback-v90a-reactive-after-0073-20260822/` and
`artifacts/loopback-v90a-reactive-after-0092-20260822/`.

## Supported bridge DIL-profile A/B (2026-08-22)

The frame adapter was rerun with its supported `measurement` DIL profile
(`N=120`, `LSP=12`, `LTP=11`) instead of the default `N=125` profile.  The
live loopback remained at caller `0x0095`; the answerer reached and held
`0x00c6` with its negotiated speed fields.  The sibling bridge reached its
own data-stage/MP path, but its B1d error count was still 1,133.

Changing the bridge's valid DIL profile therefore does not make the Eicon
V90A result gate pass.  The remaining mismatch is in the live Eicon-compatible
Phase-3/Phase-4 waveform or result interpretation, not simply the default
bridge DIL preset.

Capture: `artifacts/loopback-v90a-reactive-dil-measurement-20260822/`.

## N=0 bridge probe implementation (2026-08-22)

The bridge probe now accepts `EICON_V90A_PHASE3_DIL_PRESET=none|zero` and
passes an explicit `n=0` descriptor to the sibling analogue Phase-3 state
machine.  This matches the Eicon diagnostic V90A Ja builder's N=0 descriptor
instead of silently falling back to the 125-segment default.

The first live build was not a valid modem result: the zero-length path spent
about 979 ms in its initial stream tick, dropped roughly 4,000 samples, and
left the answerer in INFO.  The new mode remains opt-in pending a bounded
stream-performance fix; the default bridge binary/path is unchanged.

Capture: `artifacts/loopback-v90a-reactive-dil-n0-20260822/`.

## N=0 bridge after child warm-up (2026-08-22)

The Phase-3 process adapter now sends one private 160-byte frame immediately
after spawning the child and discards its response.  The live stream is reset
when the V90A overlay becomes active, so this only moves dynamic loader/DSP
initialization out of the first real RTP quantum.  The warmed N=0 run had no
sample substitutions, drops, clock holds, or underruns.

With that transport artifact removed, the N=0 protocol A/B is still negative:
the caller advanced through `0x0073` to `0x0092`, while the answerer remained
in INFO around `0x002c`.  Thus matching the Eicon diagnostic's zero-length Ja
DIL descriptor is not sufficient to make V90D admit the exchange.  The
remaining issue is the coupled response waveform or its Phase-4 result, not
the child startup latency or a simple DIL-length mismatch.

Capture: `artifacts/loopback-v90a-reactive-dil-n0-warm-20260822/`.

## Current b3 reader and V90D c2 input trace (2026-08-22)

Against the current direct PRI117 answerer, forcing the caller's reader only
in local state `0x00b3` again produces the qualified late handoff: caller
`0x00b3 -> 0x00b6 -> 0x00c0`, answerer `0x00b3 -> 0x00c0 -> 0x00c2`.
It still does not reach data mode.

The bounded c2 mapping trace shows the answerer's six-word V90D source
vector fixed at `0x0f40 0x0f40 0x0f40 0x0f40 0x0f40 0x0f40`, while the
intermediate/history vector evolves.  The native 2185 c2 trace instead sees a
live decoded input (`0x00ff`) and a changing result ramp.  This identifies a
collapsed/incorrect upstream response history before the V90D mapping worker;
the worker's arithmetic and final serializer are not yet the first fault.

Two controls further rule out simple analogue calibration.  Caller TX gain
of `+20 dB` and `+32 dB`, applied only from `0x00b3` with the reader active,
did not improve the exchange and regressed the answerer to `0x00c0`/`0x00b2`.
Running the same reader path with an 8 kHz analogue codec also failed before
V.8/INFO completion (`caller 0x0001`, answerer fallback); the qualified 9.6
kHz SPORT/resampler path is required.  The remaining correction is therefore
the reactive V90D response/control waveform, not a scalar DAA gain or a
codec-rate substitution.

Captures: `artifacts/loopback-v90a-current-b3-reader-20260822/`,
`artifacts/loopback-v90a-maptrace-c2-20260822/`,
`artifacts/loopback-v90a-b3-reader-gain20-20260822/`,
`artifacts/loopback-v90a-b3-reader-gain32-20260822/`, and
`artifacts/loopback-v90a-b3-reader-codec8k-20260822/`.

## Complete sibling digital-engine wire-peer A/B (2026-08-22)

The frame adapter was also attached to the Eicon PRI117 answerer with the
sibling tree's complete digital V.90 engine (`ME_V90_ROLE=digital`), while
the Eicon V90D DSP remained active and observable.  This is a stronger oracle
than the Phase-3-only source, but it is not a drop-in answerer: the caller
stayed in INFO around `0x002c` and the Eicon answerer reached only
`0x00b6 -> 0x00c0`.

The result means a generic digital V.90 engine cannot simply replace the Eicon
V90D wire after the overlay handoff.  Its V.8/V.90 admission and phase timing
must be adapted to the Eicon DAA/mailbox contract before it can serve as the
reactive mapping oracle.  The experiment did not alter the default harness.

Capture: `artifacts/loopback-v90a-sibling-digital-answerer-20260822/`.

The same oracle was paired with the known-positive Eicon caller b3 reader
handoff.  Even then the sibling digital waveform did not interoperate:
caller `0x00b7 -> 0x00c0`, Eicon answerer `0x00b1 -> 0x00b2`.  Therefore the
remaining gap is not just the caller's native silence selector plus a generic
digital peer.  The eventual bridge must preserve the Eicon page-14 timing,
mailbox, and state coupling while borrowing the sibling implementation's
protocol/mapping logic as a reference.

Capture: `artifacts/loopback-v90a-sibling-digital-b3reader-20260822/`.

## Sibling phase-4 oracle is still wire-incompatible (2026-08-22)

As a protocol-oracle check, the sibling digital engine was rerun with its
strict Phase-3 S confirmation disabled (`ME_V90_P3_CONFIRM=0`).  It then
accepted the exchange and repeatedly entered its own Phase-4 MP path.  This
removes the sibling's local confirmation gate as an explanation for the
earlier failure.

The Eicon endpoints still did not reach data mode: the caller advanced only
to `0x00c0`, while the Eicon answerer stopped at `0x00b2`.  The sibling log
also reports repeated MP frames but ultimately rejects its own received
CP/MP structure.  Thus the sibling engine is useful as a protocol/timing
oracle, but its phase-4 waveform is not an Eicon-compatible reactive V90D
source merely by disabling confirmation.  The required adapter still has to
translate the sibling's phase-4 output into the Eicon page-14 mailbox and
mapping/history contract.

Capture: `artifacts/loopback-v90a-sibling-digital-b3reader-noconfirm-20260822/`.

## Reactive sibling TX level is not the remaining wall (2026-08-22)

The complete sibling digital peer was rerun with its substituted RTP output
attenuated to `0.25` at the Eicon endpoint boundary.  This reduces the failed
peer's approximately 4.3k linear RMS output toward the 2185 reference level,
without changing the caller's b3-reader qualification.  The result remained
caller `0x00c0` and Eicon answerer `0x00b2`.

Reactive-peer TX amplitude is therefore not the missing correction.  The
remaining incompatibility is in the phase/timing or symbol/control format of
the peer output, rather than a DAA level calibration.  The gain remains
available only as an opt-in diagnostic control.

Capture: `artifacts/loopback-v90a-sibling-digital-b3reader-gain025-20260822/`.

## Native c2 admission followed by sibling phase-4 substitution (2026-08-22)

The full sibling digital peer was held off the wire until the Eicon answerer's
own `TrnProgress` reached `0x00c2`; native Eicon output therefore handled the
entire preceding admission path.  Substitution began only at the c2 boundary.

This also remained negative: caller `0x00c0`, answerer `0x00c2`.  Consequently
the failure is not explained by the Eicon transmitter's pre-c2 admission
waveform alone, nor by replacing its complete page-14 output from the first
overlay sample.  The caller's phase-4 result gate still sees an incompatible
response at the c0/c2 boundary; the next comparison needs the caller-side
result inputs and the native 2185 waveform on the same sample epoch.

Capture: `artifacts/loopback-v90a-sibling-digital-after-c2-20260822/`.

## Native Analog109 media-core oracle remains unavailable (2026-08-22)

The caller was switched from the kernel-dispatch Analog109 path to the
native-MIPS Analog109 media core and paired with the same sibling digital
wire peer.  With the Unicorn-enabled Courier emulator environment, the call
was transport-clean but both endpoints stopped in INFO (`0x0042`); the native
caller never loaded V90A page `0x026b`.

This does not compare Phase-3 source content: the native caller never reaches
the page where that source is constructed.  It does, however, rule out using
the current native-MIPS setup as an immediate V90A oracle and keeps the
implementation target on the missing coupled V.90A/V.90D exchange rather
than on a simple direct-vs-native codec switch.

Capture: `artifacts/loopback-v90a-native-analog-sibling-peer-courier-venv-20260822/`.

## Dual sibling engines are not a compatible closed-loop peer (2026-08-22)

As a coupled oracle experiment, the Eicon answerer used the sibling full
digital V.90 engine, while the Eicon caller's transmit side used the sibling
analogue Phase-3 stream bridge.  This supplies a protocol-aware source at
both endpoints rather than the Eicon caller's native selector.

It regressed before the V.90 page boundary: the caller remained around
`0x0051`, and the answerer fell back from INFO.  The two sibling role
implementations therefore do not share a wire contract that can simply be
threaded through the Eicon RTP/DAA boundary.  The bridge must translate the
Eicon page/state and mailbox timing, not just pair the two sibling roles.

Capture: `artifacts/loopback-v90a-dual-sibling-reactive-20260822/`.

## Full-peer startup history is not the c0 wall (2026-08-22)

The sibling digital wire peer was prebooted and the normal two-second setup
gap was removed, so it received the caller's media from the beginning of the
V.8/INFO exchange rather than attaching after SIP answer.  The loopback still
ended caller `0x00c0` / answerer `0x00b2`.

This rules out a missed early negotiation history in the sibling subprocess as
the cause of the incompatible phase-4 output.  The remaining adapter work is
format/state translation at the Eicon page-14 boundary, not peer startup
scheduling.

Capture: `artifacts/loopback-v90a-sibling-preboot-zero-gap-20260822/`.

## Early sibling response followed by native V90D output (2026-08-22)

The clocked sibling digital peer was allowed to replace the PRI117 answerer's
wire only through local state `0x00b2`; from b2 onward the native PRI117
generator resumed. The caller used the qualified b3 reader. The result was
unchanged: caller `0x00c0`, answerer `0x00b2`.

This rules out a simple ownership handoff in which the sibling supplies only
the early response and the native generator then completes c2. The late
mapping worker still lacks the caller-derived decoded history it needs; the
next correction must affect the state-coupled APCM/DPCM exchange itself.

Capture: `artifacts/loopback-v90a-reactive-hybrid-b2-20260822/`.

## Live sibling analogue source against native PRI117 (2026-08-22)

The phase3-only sibling source was rerun with its missing `libspandsp` runtime
path restored. It supplies only the caller's live analogue Phase-3 TX while
the native PRI117 V90D answerer remains the wire generator. The answerer
advanced through `0x00c2 -> 0x00c4`, but the Analog109 caller stopped at
`0x0095`.

This is evidence that the sibling analogue generator produces a more useful
native-answerer input than the current Eicon caller source, but it is not a
closed loop: the caller's receive/state machine does not consume the native
answerer's response in a way that advances its own Phase 3. The generator is
therefore a source oracle for the next coupling work, not a candidate default
wire substitution.

Capture: `artifacts/loopback-v90a-phase3-live-linked-20260822/`.

## Phase-3 DIL profile A/B against the captured native downstream (2026-08-22)

The captured native V90D downstream was replayed through the sibling Phase-3
probe with the default, Courier-style, measurement, and zero-length DIL
profiles. All four profiles followed the same `Sd -> Jd -> DIL -> CP`
milestones and produced the same Phase-4 handover. The profile choice changes
the analogue source's advertised descriptor, but it does not determine whether
the native downstream is recognized by the sibling receiver.

This makes a DIL preset mismatch an unlikely explanation for the live caller's
`0x0095` wall. The remaining integration fault is specifically the Eicon
V90A receive/state transition: the sibling can consume the native answerer's
downstream, while the Eicon caller does not advance its own state from that
same wire exchange.

Capture: `artifacts/loopback-v90a-phase3-live-linked-20260822/caller.rx.ulaw`.

## Live Phase-3 carrier/DIL A/B (2026-08-22)

The live phase-3 source was rerun with both a zero-length DIL profile and the
high-carrier option enabled. This deliberately removes the two simplest
configuration differences from the preceding live run. The result was
unchanged: the native PRI117 answerer still reached `0x00c2 -> 0x00c4`, while
the Analog109 caller still stopped at `0x0094 -> 0x0095`.

Therefore neither DIL length nor the selected carrier band explains the
phase-4 interoperability failure. The remaining mismatch is in the
negotiated phase-4 waveform/decoder state handoff, not the phase-3 carrier
descriptor. Capture: `artifacts/loopback-v90a-phase3-none-high-20260822/`.

The corresponding wire audit adds an endpoint boundary: after the answerer
reaches `0x00c4` it emits a changing, approximately `-19.4 dBFS` stream, but
the caller's receive window is still classified as silent/invalid and remains
at `0x0095`. Thus reaching `c4` is not sufficient evidence that the answerer's
post-`c4` APCM/DPCM output is a decodable downstream; the next trace should
follow the answerer's generated mapping/codec samples at the `c4` boundary.

## Fresh unpinned V90A/V90D baseline (2026-08-22)

The correct mixed topology was rerun without TX/RX pins, replay, or reactive
peer substitution: native PRI117 V90D answerer, Analog109 V90A caller,
Analog109 kernel dispatch, 9600 Hz codec, 2 s answerer setup gap, and realtime
pacing. The caller reached `0x00b6 -> 0x00c0`; the answerer reached
`0x00c0 -> 0x00c2`, but neither entered data mode during the 30 s run.

Capture: `artifacts/loopback-v90a-baseline-20260822b/`.

This confirms the late failure is reproducible on the current unpinned path,
while also showing that the earlier `0x0095` result was not a permanent
baseline limit. The next A/B should therefore preserve this exact topology and
focus on the V90A/2185 receive and Phase-4 handoff rather than reopening V.8,
DAA hook, or 9600 Hz admission.

## 2185 SPORT expansion and native-MIPS caller controls (2026-08-22)

Disabling `EICON_EXPAND_SPORT` on the PRI117 V90D answerer regressed the same
topology to caller `0x0075 -> 0x0092` and answerer INFO fallback around
`0x002c`. The hardware-correct right-justified 2185 SPORT expansion is
therefore required for V90 admission and is not the remaining c0/c2 fault.

The native-MIPS Analog109 caller was also tested, with and without
`EICON_NATIVE_SKIP_DSPDAA_CLOCK=1`. Both runs stalled during incomplete INFO
around `0x0041/0x0042`, before V90; the DSPDAA clock bypass did not improve the
walk. This backend lacks a qualifying native Analog media reference for the
late V90 exchange, so those runs are integration negatives rather than
evidence against the recovered Analog kernel/SPORT path.

Captures: `artifacts/loopback-v90a-no-sport-expand-20260822/`,
`artifacts/loopback-v90a-nativecaller-20260822b/`, and
`artifacts/loopback-v90a-nativecaller-nodspdaa-20260822/`.

## Full sibling analogue peer as caller source (2026-08-22)

The complete sibling modem was attached to the Analog109 caller as a
role-correct (`analogue`) reactive subprocess, with clock-before-active and
the local V90A overlay gate. This was not a static replay. It regressed the
mixed loopback before Phase 3: caller `0x0073 -> 0x0092`, answerer INFO
fallback around `0x002c`.

The full sibling V.8/INFO state machine is therefore not a drop-in replacement
at the caller TX seam. The earlier phase3-only source remains the more useful
oracle because it preserves the Eicon caller's native V.8/INFO admission and
only replaces the live analogue Phase-3 source.

Capture: `artifacts/loopback-v90a-full-analogue-peer-20260822/`.

## Fresh c2 worker trace (2026-08-22)

The unpinned mixed topology was rerun with the answerer's V90D map trace
enabled across the c0/c2 boundary. At c0, the V90D worker is already active,
but its source window is six repeated `0x0f40` words and its published result
is effectively a constant/zero bootstrap. At c2, the worker input, ring, and
history begin changing, while the source window remains the same repeated
`0x0f40` sequence. The worker therefore is not stuck or missing its dispatch;
it is consuming a collapsed upstream history that does not contain the
state-coupled APCM/DPCM response expected by the native 2185 path.

This is the most useful current emulator boundary: generic ADSP dispatch,
SPORT expansion, codec rate, and worker execution are live, but the V90D
mapping/control history is not evolving as it does with a valid native-quality
upstream. A valid fix must couple the V90A/V90D APCM/DPCM exchange; a static
replay, scalar gain, or worker pin cannot satisfy that requirement.

Capture: `artifacts/loopback-v90a-maptrace-c2-fresh-20260822/`.

## Synchronized source/estimator trace (2026-08-22)

The same unpinned topology was traced at 4-sample intervals from 19.015 s
through the terminal wall. The V90D equalizer input changes continuously in
both I and Q (for example `0xee8c/0xfe30`, `0x1487/0xe8dc`, and
`0xf206/0xf9ca`), so the SPORT/codec path is delivering a live waveform to the
answerer. In the same rows, the V90D mapping source remains
`0x0f40 0x0f40 0x0f40 0x0f40 0x0f40 0x0f40`; the intermediate vector is zero
and the history/generator value remains at its bootstrap. The answerer thus
has changing receive samples but no decoded V90A mapping content to feed its
rate estimator.

This correlation rules out treating the c2 wall as a missing V90D clock or
frozen equalizer input. It also makes a DAA/PCMU level change unlikely: the
line reaches the equalizer, but the V90A source/control sequence is not
protocol-valid. The producer implementation must replace the sentinel source
with a stateful V.90A Phase-3/4 source driven by the live downstream exchange.

Capture: `artifacts/loopback-v90a-source-estimator-sync-20260822/`.

## Correction: active V90A generator source (2026-08-22)

The earlier `0x209c`/TXD0 sentinel observation was not the active analogue
waveform source. PM `0x39a0` reads the generated `DM(0x0900..0x093b)` ring,
which is populated by the live filter/MAC chain beginning at the source
history around `DM(0x06c7)` and PM `0x38a0..0x38c8`. The ring and the
`DM(0x0a92..)` reader output change during the terminal dwell. The TXD0
mailbox remains a separate host/data path and its diagnostic probes are not a
qualified source fix.

The implementation target is therefore not “fill the V90A ring”; it is a
state-coupled V90A/V90D protocol bridge whose receive decisions and generated
APCM/DPCM response remain aligned in real time.

## Old data-mode artifact provenance (2026-08-22)

The archived `artifacts/loopback-v90a-datamode/` run was rechecked before
using it as a regression target.  It is not an unpinned V90A/V90D success:
the caller-side data-mode walk used the known-good `run65` downstream replay
plus terminal status pins, and the separate `answerer-native-generation`
capture supplied `run65.rx.ulaw` through `EICON_TX_FILE`.  In that latter
capture the PRI117 V90D firmware itself generates the response and reaches
`0x00d0`, which confirms the answerer, SPORT/DAA boundary, and page-14
generator can complete when upstream audio is valid.

The provenance does not provide a live V90A source oracle.  The current
unpinned loopback still reaches caller `0x00c0` / answerer `0x00c2`, while the
known-good upstream drives the answerer to `0x00d0`.  The remaining target is
therefore the caller's protocol-coupled Phase-3 source and the feedback path
that makes it react to the answerer's decoded mapping/control state.  The
archived replay and pin configuration must not be promoted to the default
harness or counted as data-mode completion.

## Gated bridge startup cost (2026-08-22)

The frame adapter originally called the sibling engine for every media frame,
even when `EICON_REACTIVE_ENGINE_AFTER_OVERLAY` delayed wire substitution until
the local V90A overlay. The fast-JM subprocess took roughly 334 ms for an early
frame on this host; that made the caller miss its V.8 media deadline and hit
the Eicon frame budget before reaching Phase 3. The adapter now defers the
exchange until the configured overlay/state is active, then resumes the
one-frame-in/one-frame-out exchange when armed.

A short clean loopback control also reproduced a V.8 sample-1 stack overflow
when it was accidentally run with the wrong firmware topology, so that result
was not a valid mixed V90 control. The corrected kernel-dispatch
Analog109/PRI117 topology reaches the V90A page without that startup failure.
The deferred exchange change is restricted to the opt-in reactive path and does
not alter normal Eicon media processing.

## Clocked digital peer and b3 reader boundary (2026-08-22)

The direction-correct full peer must be attached to the PRI117 answerer: its
received wire frame is the Analog109 caller's upstream. A new opt-in
`EICON_REACTIVE_ENGINE_CLOCK_BEFORE_ACTIVE=1` mode clocks that peer through
V.8/INFO while discarding its output, then substitutes the peer output only
after local overlay `0x026a` is active. Without this history, the peer starts
at V90D page entry and times out its own V.8/INFO state.

With the peer clocked and the caller's diagnostic b3 reader enabled, the
correct mixed loopback reaches caller `0x00c0` at 20.04 s; the answerer remains
at `0x00b2`. Extending the reader hold through caller c0 does not change that
boundary. This confirms the caller selector correction is causal for the c0
advance, but wholesale sibling TX substitution still does not produce the
native PRI117 c2 response. The next bridge must use the peer's decoded state
or mapping as input to the native V90D generator instead of replacing its
wire output with the sibling stream.

## Native-prime worker trace timing control (2026-08-22)

The successful `EICON_RX_PRIME` control remains the qualified evidence that
the PRI117 V90D page, SPORT/DAA path, and downstream generator can reach
`0x00d0` on valid analogue upstream. A follow-up run armed the V90D map trace
near the expected c2 handoff, but the live Analog109 caller had already
stopped at `0x0095` before the answerer reached c2 at approximately 22.42 s.
The answerer consequently produced no post-c2 trace window before shutdown.

This run is timing-inconclusive and is not evidence for a new worker or codec
defect. It does establish that a wall-clock trace trigger is insufficient for
native/emulated c2 comparison when the caller's bounded failure tears down the
call first; a future native reference capture must use a state-triggered or
standalone answerer feeder. The existing live c2 worker/history traces remain
the stronger comparison: emulated page-14 dispatch is active, but its
stateful mapping history does not evolve like the native path.

## State-triggered native c2 worker comparison (2026-08-22)

The new `EICON_V90D_MAP_TRACE_STATE=0x00c2` trigger was used with the
qualified native-prime upstream. It captured the answerer's actual c2 window
before the caller teardown. The native-prime worker is active and produces a
continuously changing mapping/history stream even though its six-word source
window remains the fixed `0x0f40` seed:

```text
native-prime c2: worker-in=0017/ffff work=0000/0007/fe00
                 history=f040 f440 0fc0 0370 0dc0 3db9
emulated c2:     worker-in=0019/ffff work=0000/0009/ff80
                 history=fec8 1380 1380 f9e0 1380 3db9
```

The exact emulated values are from the synchronized live c2 map trace; the
two captures are not phase-aligned, so the history words are not expected to
match sample-for-sample. The useful invariant is the worker control
difference: both workers execute the same `0x3e5e` path, but the emulated
received-symbol/history input reaches it in a different state. This is
additional evidence against a DAA/PCMU serializer defect or a frozen
generator. The next implementation comparison should trace the V90D
equalizer/decoded-symbol history at the c0-to-c2 handoff and repair that
producer, rather than pinning the published six-word mapping block.

## Symmetric native-recording control (2026-08-22)

Both Eicon endpoints were fed the matching native `run65` recordings at their
receive boundaries: `run65.rx.ulaw` to the PRI117 V90D answerer and
`run65.ulaw` to the Analog109 V90A caller. The answerer reproduced its normal
late progress through `0x00c0 -> 0x00c2`, while the caller stopped at
`0x0094 -> 0x0095`.

This is a useful negative control. It confirms that the caller's RTP/PCMU
receive path can be fed native-quality downstream audio without opening its
late V90A gate, but it is not a phase-aligned peer exchange: the replay does
not respond to the caller's transmitted symbols. The remaining caller defect
is therefore its V90A receive/state handoff or the reactive source history,
not a generic DAA/codec transport failure. Capture:
`artifacts/loopback-v90a-symmetric-gold-control-20260822/`.

## Phase-3 source plus native downstream control (2026-08-22)

The caller's opt-in sibling Phase-3 source was combined with native-quality
`run65.ulaw` replay at the caller's receive boundary. This separates the
caller-side source replacement from the live PRI117 V90D output. The caller
advanced through `0x00b6 -> 0x00c0`, but the answerer fell back around
`0x0080 -> 0x00b0`; the sibling source is not a V90D-compatible upstream for
the Eicon answerer.

Together with the symmetric-recording control, this rules out combining two
independent one-way “good” media paths as a closed-loop fix. The missing
implementation must translate the live V90A receive decisions into the
Eicon-compatible APCM/DPCM response history while preserving both endpoints'
native state machines. Capture:
`artifacts/loopback-v90a-phase3-source-native-rx-20260822/`.

## Streaming bridge API seam (2026-08-22)

The sibling reference was inspected at its lowest reusable interface. Its
`p3_demod_process()` API accepts incremental PCM blocks and appends
per-symbol decisions to a `p3_result_t`; the analogue transmitter is advanced
through explicit receive-side events (`Sd-bar`, `Jd`, `Jd-prime`, DIL, and
Phase-4 CP/MP transitions). The current Eicon `Phase3ProcessEngine` adapter
exposes only a synchronous 160-byte PCM-in/PCM-out transform. It discards the
demodulator's symbol/event information and has no CP-frame or Eicon mailbox
translation layer.

This explains why the existing sibling source can improve the Eicon answerer's
late state but cannot complete the pair: it is a wire surrogate, not a
state-coupled APCM/DPCM bridge. The next implementation should add an
explicit event-bearing adapter boundary, with acceptance checks for symbol
decisions and event timing before connecting it to the Eicon V90A/V90D media
path. No incompatible sibling wire replacement is promoted as the fix.

## Event-bearing Phase-3 adapter prototype (2026-08-22)

`tools/v90_stream_event_bridge.c` now provides an ABI-stable wrapper around the
sibling `p3_demod` API. It accepts incremental signed-PCM blocks and exports
only compact symbol decisions (`dibit`, descrambled bits, complex value, and
sample index) plus detected segment records. It deliberately does not expose
the sibling's private demodulator/result structs and does not replace Eicon
RTP output.

The prototype was built against the existing sibling checkout and processed
the 12--25 s native `run65.rx.ulaw` window in 160-sample blocks, producing
32,768 bounded symbols. This validates the streaming ABI and chunk boundary;
it is not yet a modem bridge. The remaining implementation work is to map
these decisions to Eicon V90A/V90D APCM/DPCM state and to add CP/event timing,
with a closed-loop data-mode run as the acceptance test.

The same wrapper was used as a fixed-configuration differential probe over an
8-second window of each downstream capture (3429-baud, high-carrier candidate,
160-sample blocks). The native stream produced a dibit transition fraction of
about `0.537`; the failed Analog109 receive stream produced about `0.660` and
had fewer sustained four-symbol runs (`4550` versus `7795`). These numbers are
not a negotiated V.90 decode—the capture does not expose the native caller's
carrier/timing selection—but they provide an objective event-quality check for
the future state mapper: its response should move the live stream toward the
native structured pattern, not merely increase RMS level.
## Baud/carrier hypothesis differential (2026-08-22, corrected)

The repeatable `tools/v90_hypothesis_diff.py` scan is useful only when its
window is aligned to the same protocol milestone. The original 12--20 s
comparison mixed different phases and incorrectly suggested a native
3429/low-carrier versus failed 2800/low-carrier split. Aligning the windows to
the observed c2 transitions (native c2 at about 23.22 s; failed c2 at about
19.08 s) removes that conclusion: the pre-c2 windows both favor roughly
3000-baud/low-carrier candidates, while the post-c2 windows do not yield a
stable negotiated selector. This tool remains an event-quality aid, not a
rate-selection oracle.

## V90D worker-input pin A/B (2026-08-22)

Native-prime c2 traces expose `DM(0x1e4f)=0x0017` at the mapping worker input;
the failed direct loopback exposes `0x0019`. The default-off
`EICON_V90D_WORKER_INPUT_PIN=0x00c2:0x0017` probe held that one word in the
live answerer. The unprimed caller still stopped at `0x00b6 -> 0x00c0`, while
the answerer still reached `0x00c0 -> 0x00c2`. Therefore this operand is a
correlated symptom of the caller's received Phase-3 history, not by itself
the missing correction. Capture: `artifacts/loopback-v90d-worker-input-pin-20260822/`.
## State-held native downstream replay (2026-08-22)

As a causal waveform check, the answerer's real transmit boundary was given
`EICON_TX_PRIME_SYNC` windows copied from native `run65.ulaw`, keyed to the
live answerer states `0x00b0` through `0x00c6`. The replay was visibly active:
the answerer reached `0x00c0 -> 0x00c2`, but the Analog109 caller still stopped
at `0x00b6 -> 0x00c0`. Thus native downstream segment content alone does not
open the caller's terminal result gate when the caller's own response remains
uncoupled. This is a negative for static/state-held replay, not for the
physical RTP/DAA path. Capture: `artifacts/loopback-v90d-native-state-held-20260822/`.
## Hybrid replay plus sibling Phase-3 source (2026-08-22)

The state-held native downstream replay was combined with the existing
sibling Phase-3 process source on the caller (`EICON_V90A_PHASE3_ENGINE`), so
the caller's TX was generated from its live received replay rather than from
the Eicon page alone. The bridge child started successfully with the sibling
SpanDSP runtime, but the pair regressed: caller `0x0094 -> 0x0095`, answerer
`0x00b6 -> 0x00c0`. This rules out composing two independently timed waveform
substitutions. The missing implementation must couple demodulated events,
state transitions, and both transmitters at the same media tick.
Capture: `artifacts/loopback-v90d-native-replay-phase3-coupled-20260822b/`.

## Caller-peer-state-aligned downstream replay (2026-08-22)

The state-held replay was rerun with the answerer reading the caller's live
`TrnProgress` publication rather than its own lagging state. This changed the
boundary materially: caller `0x00b0 -> 0x00b3`, answerer `0x00b1 -> 0x00b2`.
The pair still did not reach data mode, and no state pin was used. Peer-state
alignment is therefore a real control variable, but the native segment-to-
Eicon-state mapping/content is still not the required reactive exchange.
Capture: `artifacts/loopback-v90d-native-peerstate-held-20260822b/`.

## Widened caller-b3 replay window (2026-08-22)

The caller-peer-state replay was repeated with the native downstream window
for caller `0x00b3` widened from `23.06--23.10 s` to `23.06--23.24 s`. The
boundary was unchanged: caller `0x00b0 -> 0x00b3`, answerer `0x00b1 -> 0x00b2`.
The earlier short replay window was therefore not the limiting duration; the
missing behavior is the live response/content evolution that a recording
cannot provide. Capture: `artifacts/loopback-v90d-native-peerstate-b3wide-20260822/`.

## Stateful digital Phase-3 event bridge checkpoint (2026-08-22)

`tools/v90_digital_phase3_event_bridge.c` is the first bounded stateful
adapter on the digital side. It consumes 160-sample PCMU blocks, feeds the
sibling streaming Phase-3 demodulator, translates recognized S/TRN/J/J′
segments into the sibling `v90_handle_rx_event()` API, and emits the sibling
V.90D codeword stream from the resulting transmitter state. The sample offset
is carried across blocks, and unstable/opaque segment classifications are not
published as modem events.

The bridge builds and runs against the existing sibling object files and
SpanDSP library. An offline run over the native `run65.rx.ulaw` reference
stream produced the expected bounded output and recognized the analogue Ja,
S/TRN/J/J′ sequence. The sibling V90 state machine accepted those events and
advanced through Sd, S̄d, TRN1d, Jd, J′d, DIL, and into the Phase-4 Ri state;
the offline run still ended with `complete=0` because CP/MP events were not
translated. This is an adapter/probe checkpoint, not a claimed data-mode fix.
It still lacks negotiated baud/carrier selection and Phase-4 CP/MP event
translation, so it is not connected to the default Eicon harness path yet.

## Live digital bridge integration A/B (2026-08-22)

The bridge was made compatible with the media adapter's `--stream
--reset-file` protocol and exposed through the opt-in
`EICON_V90D_PHASE3_ENGINE` answerer path. It clocks from call setup so its
demodulator sees the caller's Ja, but only replaces the Eicon TX after the
PRI V90D overlay `0x026a` becomes resident. A bounded 4,096-symbol tail keeps
the event scan from starving the 20-ms media loop.

The first live run exposed that starvation: the unbounded scan drove the
answerer to 58.6-ms mean ticks, 626 underruns, and an early pre-V.90 stop. The
bounded-tail rerun stayed close to realtime through early training and
accepted J and S, but the actual pair still failed: answerer
`0x0080 -> 0x00b0`, caller `0x0094 -> 0x0095`, with no data mode. Therefore
the stateful sibling V90D TX generator is now a measured live seam, but it is
not sufficient as a drop-in Eicon downstream. The next required seam is
coupled Phase-4 receive/event handling and negotiated CP/MP timing, not more
static TX replay.
Capture: `artifacts/loopback/answerer.endpoint.log` and
`artifacts/loopback/caller.endpoint.log` from the bounded-tail rerun.

## Strict CP receiver checkpoint (2026-08-22)

The bridge now feeds its streaming descrambled bit decisions into the
sibling `v90_cp_rx` receiver and applies CRC/semantic-valid frames through
`v90_set_phase4_cp()` followed by `V90_RX_EVENT_CP_VALID`. Offline replay of
the native reference and the live failed answerer upstream capture produced
zero valid CP callbacks. The digital bridge reaches Ri, but the analogue
side does not present a recoverable CP frame in the current exchange. This
narrows the remaining defect to Phase-4 upstream waveform/timing or the
Analog109 CP-generation gate; simply adding a digital CP state transition is
not enough.

## Late-only bridge control (2026-08-22)

To isolate the bridge's early Phase-3 output, the same stateful child was
clocked from call setup but replacement was delayed until answerer
`TrnProgress 0x00b0`. The result was effectively unchanged: caller
`0x0094 -> 0x0095`, with no data mode. This rules out the bridge's early
Phase-3 waveform as the sole explanation for the late caller failure and
keeps the focus on negotiated Phase-4 response/timing and the Analog109
upstream gate.
