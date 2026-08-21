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
