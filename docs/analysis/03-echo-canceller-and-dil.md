# The echo canceller, the bulk delay, and the DIL lottery

Sessions 58-113. The bulk-delay adapter, the V90D record table, the DIL lottery, and the rate/quality measurements.

Part of the running log; the index is [`../eicon_adsp_firmware_analysis.md`](../eicon_adsp_firmware_analysis.md). The current picture is [`../handoff.md`](../handoff.md).

---

## Session 58: the destructive stream is the bulk-delay adapter; its cursor is unprimed

The ADDSP V.90 Guide's write-database map identifies `DM(0x3fbc..0x3fbf)` as
`Nearbulklength`, `BulkLength`, `BulkInputX`, and `BulkInputY`. PM
`0x1900..0x19c8` is the matching near/far echo bulk-delay adapter, not a state
image decoder. The identical routine is present at the same PM addresses in
the V.34 overlay. PM `0x19a7` detects bulk-configuration changes, PM `0x1982`
reconfigures its eight-word descriptor at DM zero, and PM `0x1900` services it.

This explains the zero source without requiring a missing producer. At PM
`0x190f` the adapter reads the cursor-selected DM word, but PM `0x1910..0x1914`
replaces it with zero while the cursor is outside the configured bulk
interval. PM `0x1930` then stores that deliberate zero. The watched low-DM
source happened to retain nonzero INFO-page data at V90D entry; its contents
were suppressed by the bounds check, not absent.

The descriptor exposes the actual seam. When state `0x60` activates the
adapter, run21 has:

```text
DM3fbc/DM3fbd = 1d77 / 0ae0
DM0..DM7       = 1d77 2ad2 0001 0001 0001 0000 0000 0001
```

DM4 is the far-bulk cursor. The portable V90D DM block explicitly initializes
it to zero, PM `0x1982` preserves that relative cursor, and the first service
increments it to one. Starting from one makes the zero-fill destination reach
`DM(0x1938)` before state `0x72`'s dwell expires.

A diagnostic replay primed only `DM4=DM0` on the first state-`0x60` sample.
No codec, record, countdown, or input value was changed. The previously
missing sequence immediately appeared:

```text
0060 -> 0062 -> 0064 -> 0066 -> 0068 -> 006a -> 0070 -> 0072 -> 0074
```

Without the prime, the same run goes `0066 -> 0072`, zeroes the pending
records, and remains there. Priming DM2 as well is unnecessary. This is causal
proof that the failure is the unpublished initial far-bulk cursor, not
`DM(0x2004)`, G.711, record-applier ordering, or the dwell implementation.
The primed open-loop replay later remains at `0x74`; by then its changed output
can no longer be expected to match the captured peer response, so that is not
evidence against the cursor seam.

`--prime-v90d-bulk-cursor` is retained as an explicit native-MIPS diagnostic.
It primes DM4 from DM0 once when V90D state `0x60` activates; it is not enabled
by default.

## Session 59: closed-loop run25 proves the missing cursor publication

Tower call `artifacts/eicon-native-tower/run25` enabled only
`--prime-v90d-bulk-cursor`. At state `0x60` the native seam published
`DM4=DM0=0x1e17`. The live peer—not an open-loop replay—then followed:

```text
0060 -> 0062 -> 0064 -> 0066 -> 0068 -> 006a -> 0070
     -> 0072 -> 0074 -> 0076 -> 0078 -> 007a
```

The unprimed run21 went `0066 -> 0072` and lost its pending records. Run25
therefore confirms in closed loop that DM4's zero origin is the missing
publication. It also disproves the earlier claim that `0x68`, `0x6a`, and
`0x70` were peer-dependent branches: they were simply among the records
clobbered by the bulk adapter.

Run25 stops at state `0x7a`, whose pretest condition `0x18` at PM
`0x3019..0x3038` requires both:

```text
(DM(0x206d) & 0x000f) == 0x000f
(DM(0x206e) & 0xfffc) == 0xfff8
```

That is the next receive-result boundary. It is downstream of the now-proven
bulk-cursor seam and should be traced separately; the peer disconnects before
publishing a matching result. The cursor prime remains diagnostic until its
correct owner—the omitted selected-channel continuation versus a generic
kernel initialization—is encoded without patching overlay-private DM from the
SIP layer.

## Session 60: state `0x7a` is the Ja receive gate, so silence is still correct

The tower log for run25 confirms the peer sequence explicitly:

```text
TRNSEG4 -> JTXMIT -> JaTXMIT
V90Phase3Demodulator: initial state set to WaitForSd
```

Our RTP output is PCMU `0xff` throughout the corresponding V90D window. That
matches V.90 §9.3.1.3 up to the failed receive decision: after the first 512T
of TRN, the digital modem conditions its receiver for Ja; only **after
receiving Ja** may it wait up to 500 ms and then transmit Sd for 384T followed
by Sd-bar for 48T. Sending Sd before the state-`0x7a` condition succeeds would
hide the receive fault and violate the specified ordering.

A cursor-primed run25 replay shows why condition `0x18` never succeeds. Its
32-bit detector result becomes `DM206d/DM206e = 0x1ab2/0xa604` and then freezes,
while the required masked value is `0x000f/0xfff8`. The bit input `DM2055`
remains zero. PM `0x2eac..0x2ecb` derives that bit from the receiver history
and PM `0x0ca6..0x0caf` shifts it into `DM206d/DM206e`; the scheduler itself is
working.

PM `0x19fd` copies the page's in-place scalar `DM3fa7` into `BulkInputX` before
the page reuses that slot. As a negative test, publishing the raw expanded
SPORT sample directly at `DM3fa7` made the detector registers move, but a live
run stalled back at state `0x60`. Therefore `DM3fa7` is not another raw-linear
SPORT inlet; it is a processed scalar owned by the selected-channel/V.34
receiver continuation. That experiment was reverted.

At this point the likely boundary was the selected-channel continuation, but
the direct runtime trace in Session 61 disproved that ownership hypothesis.
The downstream serializer should still remain silent until the receive
boundary passes.

## Session 61: ADSP-2185N runtime proves the V.34 core receives the SPORT sample

The hardware part is an ADSP-2185N, not literally an ADSP-2181. `TIKRNL81` and
`DSP_DOWNLOAD_FLAG_2181` identify the compatible ADSP-2181-family firmware ABI;
they do not identify the exact fitted part. The computational instruction set
used here is compatible, but SPORT/autobuffer assumptions must be checked
against the 2185N hardware rather than inferred from the emulator's name.

A dump of the fully relocated runtime code resolves the selected-channel path:

```text
PM 02b7: SR1 = DM(I5,M4)
PM 02b9: CALL 0703
PM 0703: SE = DM(313f)
PM 0704: SR = LSHIFT SR1 (HI)
PM 0771: CALL (DM(3fb3))       ; page Core8kRoutine
```

Coverage at V90D state `0x60` was 127087 executions of PM `0x0703` for 127079
media samples—the eight-call difference is setup—so the native foreground
runs once per selected 8 kHz sample. Execution-register tracing then showed
the expected one-sample pipeline exactly. For a diagnostic PCMU octet `0x80`,
SPORT RX0 and the next PM `0x0703` invocation both held signed-linear
`0x7d7c`. Thus the Ja failure is **not** caused by an omitted callback or by
feeding V90D from the wrong raw-sample address.

The ADDSP guide's write-database table also corrects the interpretation of
`DM3fbe/DM3fbf`: `BulkInputX/Y` are samples the **V.34 modemCore offers the
kernel at symbol rate** for the echo delay line. PM `0x19fd` copies the core's
in-place result at `DM3fa7` to `BulkInputX`. They are not receiver inputs;
zeros while the digital transmitter is deliberately silent are expected.
This also explains why forcing raw SPORT PCM into `DM3fa7` damaged state
`0x60`.

Two hardware-boundary alternatives were tested and rejected:

- running the call as PCMA because slmodemd reports `pcmType = A_LAW` made the
  outer sequencer oscillate between `0x50` and `0x52`; it did not repair Ja;
- presenting 32 SPORT interrupts per media sample to model a complete PRI TDM
  frame prevented INFO completion. The native MIPS assignment has already
  selected one B-channel before this task, so this firmware instance correctly
  receives one 8 kHz scalar per call.

The investigation boundary is now inside the live V.34 upstream receiver:
trace `DM11e8/DM11e9 -> DM2055 -> DM206d/DM206e` over Ja, compare its symbol
and bit cadence with the Ja descriptor in V.90 §8.3.1, and find the first
receiver stage whose decisions differ. No SPORT, codec, or bulk-input shim
should be added at this point.

## Session 62: ADSP-2185N manuals and the first Ja decision trace

The added Analog Devices references materially tighten the hardware model:

- `docs/ADSP-218XN_SERIES.pdf` identifies the ADSP-2185N as 16K-word PM plus
  16K-word DM and confirms the ADSP-2100-family instruction compatibility;
- `docs/3110043388x_hardware/8xsports.pdf` §5 confirms that companded RX0 is a
  right-justified, sign-extended linear value before the receive interrupt;
- its interrupt table gives SPORT0 TX/RX vectors `0x0010/0x0014`, matching the
  emulator's priority-to-vector mapping;
- `docs/3110043388x_hardware/8xcompu.pdf` §2 states that computational and
  register operands are read at the beginning of a cycle and results/data
  moves are written at the end. This supports simultaneous old-value semantics
  for multifunction instructions rather than sequential C-side updates.

The SPORT chapter describes µ-law and A-law as having 14-bit and 13-bit
maximums. A diagnostic interpretation that divided conventional int16 G.711
expansion by 4/8 was tested, but live run28 stopped receiving symbol events in
state `0x74`; it did not detect Ja. The wording describes the algorithms'
effective precision and saturation limits, not sufficient evidence for adding
an extra gain change at the reconstructed boundary. The divide was reverted.

A symbol-cadence run25 replay now captures the complete live chain. At state
`0x7a`, `DM11e9` publishes quadrant/dibit values and PM `0x2eac..0x2ecb`
publishes `DM2055` in the expected top-two-bit forms `0000/4000/8000/c000`.
PM `0x0ca6..0x0caf` shifts two bits per event into `DM206d/DM206e`. The rolling
register moves for 231 samples and settles at:

```text
DM206d/DM206e = 1ab2/a604
```

It never contains the frame-sync condition selected by outer precondition
`0x18`:

```text
(DM206d & 000f) == 000f
(DM206e & fffc) == fff8
```

V.90 §8.3.1 defines the start of every repeated Ja DIL descriptor as 17 one
bits followed by a zero start bit. Therefore the remaining failure is no
longer merely “no receive publication”: the live receiver produces dibits,
but its descrambled stream never publishes the Ja sync run. The next trace
was then split across PM `0x2eac`: `DM11e9`, the raw aligned word at
`DM0ee6`, histories `DM2067/DM2068`, and output `DM2055` all advance. The
history shifts continuously and agrees with the raw decision stream; the
output remains non-sync after far more than the 23-bit self-synchronization
length. Trying the four dibit inversion combinations and both within-dibit
orders against the GPA `x^-5/x^-23` recurrence produced no 17-one run (the
longest was nine). Setting `DM1fea bit 15` to select PM `0x2e99` was also
rejected: it changed the receiver mode broadly and still produced no sync.

The first wrong boundary is therefore before GPA descrambling, in the V.34
carrier/equalizer/differential-decision path that produces `DM11e9`. The most
useful next comparison is the trained V.34 receiver state immediately before
and after the INFO-to-V90D overlay load: if the segmented loader overwrites or
fails to retain equalizer/carrier state, Ja will look exactly like the observed
random but cadence-correct dibits.

## Session 63: the native loader retains the sparse V.34 handoff state

A run25 replay snapshotted all 16K DM words immediately before and immediately
after the native MIPS loader transferred overlay `0x026a`. The page has 8,098
explicit DM words; 7,963 runtime words changed and 616 non-zero INFO values
became zero. Those headline counts initially look destructive, but matching
them against `026a`'s actual block map shows the opposite: every change is in
an explicit V90D load block, while the sparse gaps are left untouched.

In the receiver/state range `0x0d00..0x21ff`, 365 non-zero words survive
bit-exact in unloaded gaps. The largest handoff islands include:

```text
10c1..10ca   10 words
1d8e..1dcf   66 words
1df2..1e5c  107 words (with small explicit block holes)
2094..2133  160 words
```

The corresponding addresses in the portable INFO flat image are zero, proving
these are runtime/inherited values rather than constants reloaded from INFO.
V90D directly references several islands—for example PM `0x0ad5`, `0x1025`,
`0x107b`, `0x26a9`, `0x26ae` and `0x3e78` use the retained `DM1e00` region.
Thus the segmented native loader is preserving the intended cross-page V.34
state, and broad loader zeroing is not the source of the random Ja decisions.

The remaining comparison must move one stage earlier: record the V.34
carrier/equalizer outputs and phase decisions on both sides of the page seam,
then audit the exact ADSP-2185N shifter/MAC instructions on the first path that
diverges. The added computational-unit manual is now the authority for that
audit, especially old-value multifunction semantics, fractional MAC placement
and EXP/NORM behavior.

## Session 64: `1ab2/a604` is the exact differential-decoded TRN-tail signature

Run30 finally reproduced the run25 path with symbol-cadence logging in the live
call. It reached state `0x7a` and produced exactly the same terminal detector
word as the offline replay. The additional fields change the interpretation
completely.

The equalizer outputs `DM11f5/DM11f6` form four tight clusters:

```text
phase index  centroid angle
0             +44.95 degrees
1            -135.91 degrees
2             -45.93 degrees
3            +135.07 degrees
```

Residual phase error remains within about +/-11 degrees and magnitude is
stable. `DM11eb` maps the sliced phase to the V.34 clockwise rotation index,
and `DM11e9` agrees with modulo-4 differential decoding on 89 of 90 transitions
(the first value is inherited). PM `0x2eac` then agrees bit-for-bit with GPA
`1 + x^-5 + x^-23` descrambling after its 23-bit history fills. The carrier,
equalizer, slicer, differential decoder and descrambler are all operating
correctly for the symbols they receive.

The supposedly random terminal word can be reproduced independently. Generate
GPA-scrambled binary ones as V.34 TRN (§10.1.3.8), map them directly to the
four-point constellation (TRN is *not* differential encoded), then feed those
phase indices through the Ja-mode differential decoder and GPA descrambler.
The rolling 32-bit register becomes exactly:

```text
DM206d/DM206e = 1ab2/a604
```

at TRN symbol 590. No other bit-order or polarity adjustment is needed. Thus
state `0x78/0x7a` is seeing the deterministic tail of TRN, not corrupt Ja data.
It publishes 91 symbols and then all of `DM11f5/DM11f6`, `DM11e8`, `DM11e9`
and the result register freeze at the TRN-to-Ja boundary, although SPORT input
and the 8 kHz foreground continue.

This corrects the Session 62 conclusion: the first wrong boundary is not a bad
V.34 decision. It is the missing **TRN-to-Ja receive continuation**. The DSP
successfully arms Ja mode near TRN symbol 500, consumes the remaining TRN tail,
but does not resume symbol publication for Ja. That is exactly why the remote
stays in `JaTXMIT/WaitForSd` while the digital side remains silent.

The next trace target is the timing/symbol-strobe gate feeding `DM11f5/DM11f6`
at the moment the result freezes (around live sample 136017), especially the
state selected by `DM1fec=0x4060`, `DM1fed=0x331c`, `DM1ff1=1` and the callback
at `DM2035`. Do not alter differential decoding, GPA taps or the equalizer.

## Session 65: the apparent TRN-to-Ja freeze is the delayed bulk-cursor collision

Instruction coverage on the two sides of the final `DM11f5/DM11f6` update
showed that this was not a symbol-strobe branch inside the V.34 receiver. The
entire V.34 foreground disappears. Before the freeze, PM `0x19e1` and the
receiver chain run once per 8 kHz or symbol event as appropriate; afterwards
they execute only twice while the already-dispatched calls drain.

A write watch identifies the destructive instruction exactly. At replay sample
130257 PM `0x1930` zeroes `DM3fad`; one sample later it zeroes `DM3fb3`:

```text
PM 1930: DM(I0,M1) = SR0
I0 = 3fad, then 3fb3
DM3fb3: 19e1 -> 0000
```

`DM3fb3` is the ADDSP write-database `Core8kRoutine` callback. PM `0x076d`
loads it and PM `0x0771` invokes it once per selected-channel sample. Once the
bulk adapter replaces it with zero, the modem core is no longer called, so the
equalizer and Ja detector necessarily freeze. SPORT and the kernel foreground
continue exactly as observed.

This is the original Session 58 collision returning later, not yet a distinct
Ja transition defect. Priming `DM4=DM0` merely moves the far-bulk cursor from
zero to the near-bulk cursor. It delays the zero-fill sweep long enough to
reach state `0x7a`, but does not put the cursor in a valid far-bulk interval.
The descriptor workspace at page entry is:

```text
DM0..DM11 = 2aca 2ad2 2ae5 2b1b 0000 0000 0000 0000
            2ac7 2ad2 2ae0 2b1b
DM32f7    = 0000
```

A subsequent selector watch corrects an important labeling error: these are
not two independently selected descriptors. PM `0x1900` and PM `0x1982` load
I1 from `DM32f7`; that selector remains zero throughout the failure and has no
DSP writer. Consequently both routines operate on the overlapping workspace
starting at DM zero. PM `0x1900` reaches offsets zero through seven, including
both the cursor at `DM0` and the second mutable pointer/state word at `DM4`;
under the observed selector it does not consume `DM8..DM11` as “far bounds.”

When state `0x60` reconfigures the workspace, PM `0x1982` itself deliberately
writes `DM4=0`; PM `0x1900` then advances it to one. The one-shot diagnostic
replaces that firmware-generated state with the unrelated `DM0` cursor. It
subsequently advances to `0x2a85`; PM `0x1930`'s delayed destination then
reaches `DM3fb3` and kills `Core8kRoutine`. The terminal `1ab2/a604` word is
still proven to be a deterministic TRN-tail signature, but its being the final
word is explained by this memory collision rather than by a demonstrated
TRN-to-Ja receiver-mode branch.

The next target is therefore the **PM `0x1982` bulk-workspace calculation**,
not `DM2035` or the slicer. Trace how `DM3fbc/DM3fbd`
(`Nearbulklength/BulkLength`), the RX sample/database workspace, `DM32f7`, and
ADSP carry/NORM semantics produce `DM0..DM7`. The evidence no longer supports
calling `DM4` an unpublished standalone cursor: zero is generated by executed
firmware, so either an input/selected-channel handoff is missing or one of the
calculation's ADSP semantics is wrong. Keep `--prime-v90d-bulk-cursor`
diagnostic-only; it is temporarily useful but eventually destructive.

## Session 66: PM `0x1900` exposes the missing retained workspace words

The unprimed collision was captured at the exact service invocation. When PM
`0x1930` first overwrites the state record at `DM1938`, the workspace is:

```text
DM0..DM7 = 1e17 2ad2 040a 0001 040a 0000 0000 0001
```

The executed path is `0x1900..0x191c, 0x192c..0x1930`. PM `0x190b` advances
`DM2`; PM `0x190d` reads the prior `DM4`; and PM `0x1917` subtracts the upper
workspace value `DM1=0x2ad2`. The old-value multifunction at PM `0x191a`
correctly copies that negative result (`0xd938`) into AX0 while clearing AR.
Loading the 14-bit DAG register at PM `0x192e` therefore produces I0=`0x1938`,
and PM `0x1930` stores the deliberate zero there. Carry, 14-bit DAG masking
and same-cycle old-value behavior all agree with the ADSP-2185N manuals for
this invocation; no emulator arithmetic discrepancy has yet been found.

A stronger lifecycle clue is in the segmented images. Both V.34 page `0x0261`
and V90D page `0x026a` explicitly load DM `0..4` and `8..12`, but leave
`DM5..7` sparse. Those three workspace words are designed to survive from a
common/kernel initializer. In the forced native path they are zero before the
V90D load and remain zero when PM `0x1982` runs. PM `0x1982` consequently
regenerates `DM2=0` and preserves the zero second-pointer state rather than
installing a bounded bulk interval.

The next comparison should therefore be a normal V.34/common-page startup—or
the omitted selected-channel initializer—at the writers of `DM5..7`. An
emulator defect remains possible, especially in the PM `0x1900` carry paths,
but simply changing carry or old-value semantics would contradict both the
manual and the captured instruction-level data. The first unexplained input is
the absent retained workspace state, not the resulting subtraction.

## Session 67: the missing owner is above the ADSP page, in call ingress/activation

The Linux and early-MIPS paths narrow the ownership boundary further. Linux
`drivers/isdn/hardware/eicon/message.c` (`connect_res()` and `add_b1()` in the
v4.0 tree) only builds the modem CAI—resource, framing, negotiation options and
speed limits—and attaches it to `CALL_RES`. It does not calculate or publish
ADDSP near/far bulk workspace words. Those are card-firmware/task activation
state, not Linux modem parameters.

The real MIPS `SERVICE_ASSIGN`/`SWITCH_ON` path was then stopped before the
SIP adapter's `attach_connected_bearer()` synthesis. At that point the modem
DSP has consumed its genuine TIKRNL assignment command, but has no active page:

```text
resident/page = 0000
DM2f86/2f87  = 3110/3108
DM32f0..32f7 = all zero
DM0..DM11    = 0000 0000 3150 000a 0000 ...
```

The initial write database left by `SWITCH_ON` is also materially different
from the two hand-built WDBs in `attach_connected_bearer()`:

```text
DM3ee0.. = 0040 0024 0038 0008 0000 0000 2105 f1fd
            000c 000c 00b8 0033 0003 0000 2000 abcd ...
```

`attach_connected_bearer()` therefore is not yet “the exact result” of the
SIG.MDM bearer-connected notification, despite its old comment. It manually
loads DIAL-related pages, writes a generic ADDSP Table 12/13/15 database, calls
PM `0x0581/0x13cc`, and later `_frame_core()` manually resumes the selected
continuation. This is precisely the layer capable of omitting private
selected-channel/bulk initialization.

A write-PC trace corrects the initial interpretation of the synthetic call
link. Event `0x17` allocates a staging object at `0x8028fda0`; SETUP clears the
controller's `+0x1c` at MIPS `0x8001855c`, in the delay slot of the successful
transfer path after `0x8002a89c` accepts the call. That clear is intentional,
not evidence that allocation failed. CALL_IND channel zero is also valid for
the first call. The missing event is later: an answering `CALL_RES` is not the
line-side connected indication.

Injecting lower-PRI event `0x03` immediately after successful `CALL_RES`
produces the previously absent native effects:

```text
CONNECT_ACTIVE indication: IND 0x01, Id 0x02, Ch 0
network indication:        IND 0x03, Id 0x03, Ch 2
MIPS host writes:          2274 -> 3814
modem DM2e58:              0000 -> 0277
modem DM2f08:              0000 -> 8000
```

The resident kernel compares `DM2f08` with `DM2f09` at PM `0x02b3..0x02b6`
and calls PM `0x01c1` to install the selected task vectors when they differ.
This is the genuine early-firmware selected-bearer seam that the compatibility
path omitted. It cannot simply be combined with `attach_connected_bearer()`:
doing so double-activates the task and its first synthetic WDB is no longer
consumed (`DM3131=0x000d`).

`--native-bearer-activation` now exposes this path diagnostically. It delivers
event `0x03` and disables the compatibility DIAL/WDB synthesis. The resulting
core intentionally remains at TIKRNL page `0x0258`, requesting SIG overlay
`0x0270`, until the remaining native supervisor/page-service continuation is
reconstructed. This is progress rather than a working media mode: it preserves
the real activation state so the next trace can follow PM `0x01c1`, the
`0x0270` request and the native WDB owner without overwriting them with the
hand-built DIAL setup.

Driving one media frame through that preserved state exposes the next missing
owner. Event `0x03` sets `DM2f08=0x8000`, and resident PM `0x02b3..0x02b6`
correctly notices that it differs from `DM2f09`. But the kernel dispatch-list
roots are still absent:

```text
DM2f27/DM2f28/DM2f29 = 0000/0000/0000
```

PM `0x01c1` therefore follows a null task-vector list and execution reaches PM
zero. The compatibility sample path hid this by patching PM `0x00b5` and
resuming TIKRNL directly at PM `0x06c8`; native activation now deliberately
does neither, so the missing early state is visible. The next MIPS/TIKRNL
target is the post-`SWITCH_ON` command consumer that should populate
`DM2f27..DM2f29` before the first selected-channel interrupt. This is earlier
than any V.90 overlay or bulk-delay calculation.

A comparison across every emulated DSP localised why those roots were absent.
The modem assignment selects block `0x1c000808`, the same exceptional core
reported at boot as held without a resident download. Its sparse TIKRNL task
is present around PM `0x0580`, but its resident interrupt and scheduler words
are still parking instructions:

```text
selected 0x1c000808: PM0014/0072/00b5 = 18000f/18000f/18000f (JUMP 0)
other active cores:  PM0014/0072/00b5 = 18072f/3c0611/2a7eea
```

The `kernel` argument to `create_native_mips_modem()` was unused. Thus this
was not yet evidence of a missing firmware write to `DM2f27`: there was no
resident kernel capable of receiving the first SPORT/IRQE event. The TIKRNL
image is genuinely sparse (its PM blocks begin at `0x0580` and `0x1800`), so
loading it cannot supply the resident vectors.

The earlier `SWITCH_ON` hook was still one lifecycle step too late. At
`SERVICE_ASSIGN`, the service object's `+0` pointer leads to the task object,
whose `+0x10` word is the DSP register base `0xbc000808`. The return address of
`SERVICE_ASSIGN` is therefore the first safe seam where all sparse task blocks
exist but the MIPS has not yet published its task commands. Native activation
now composes and initializes the selected core there, before the next DSP pump.
Both kernel and TIKRNL are restored from their declared PM blocks only; loading
a flattened 16K PM image erased the live command ring at PM `0x3327`.

The first selected-channel pipeline then behaves normally after its two-sample
SPORT delay:

```text
frame 0: DM2f08/DM2f09 = 8000/0000
frame 2: DM2f08/DM2f09 = 8000/8000
         DM2f27..DM2f29 = 2f21/2f00/2f0e
```

Tracing the task drivers identifies the real connected task path. Lower-PRI
event `0x03` dispatches service driver `0x80098310`; it verifies task ID
`0x0258`, describes the two native transfers as:

```text
KERNEL:   DSP address 0x6e68, 0x0010 words
DATABASE: DSP address 0x7ee0, 0x0100 words
```

and then calls `0x80097f60` plus `0x80095318`. The latter polls the DSP for
completion. On the physical card PRI SPORT continues clocking while that MIPS
poll runs. The shim instead pumped masked IRQE and supplied no selected-channel
clocks, so `0x80095318` timed out and its cleanup path removed the task.

Native activation now supplies a bounded pre-media SPORT window while phase
`call-ingress-connected` is executing, using the registered PM `0x0586` ISR
and PM `0x06c8` continuation. Four setup clocks are sufficient to change
the native task marker without fabricating any RTP samples:

```text
DM3137: 0000 -> 0001
DM3131/DM3132: 000d/0270
```

That makes the initial SIG request live. The page supervisor loads `0x0270`
once and resumes PM `0x06df`; unlike the old unconditional interpretation, it
does not repeatedly reload the static `000d/0270` image pair.

The remaining task boundary is now after SIG-page attachment: PM `0x0586` and
PM `0x06c8` execute, but PM `0x0703` is not yet reached, no DIAL successor is
requested, and the native network task eventually emits IND `0x04`. The next
trace belongs in the `0x80098310 -> 0x80095318` completion state and SIG
`0x0270` handoff, not in V.90 bulk workspace or kernel free-list code.

## Session 60: relocated native task attachment and first V90D transmit

The apparent post-SIG blocker was caused by restoring the wrong task image.
`SERVICE_ASSIGN` calls `SWITCH_ON` before it returns. The old hook initialized
and marked the task at `SWITCH_ON`, so the intended deferred return hook became
a no-op. It also copied the extracted source-address TIKRNL PM over the MIPS
loader's relocated image. The naturally completed download proves that no PM
composition is needed:

```text
PM0014 = 18072f                 resident kernel vector
PM0580 = 0a000f
PM0586 = 18589f                 selected-channel ISR entry
PM06a0 = 40703a                 relocated continuation address 0703
PM0703 = 8313f9                 source PM06fc after the +7 relocation
```

The `SWITCH_ON` initializer was removed. The selected core is released and
runtime PM `0x0679` is called only after `SERVICE_ASSIGN` returns, preserving
the genuine relocated kernel/task image. The bounded PRI setup-clock window
also starts at MIPS `0x800951d4`, where the connected driver actually publishes
its control toggle, rather than at entry to the much earlier event-03 driver.
Three clocks are consumed before the MIPS sees completion:

```text
host writes: 3814 timeout -> 2281 success
DM2f08/DM2f09: 8000/8000
```

Event `0x03` remains the sole TIKRNL attachment owner. The subsequent helper
only completes the documented ADDSP answer transaction: common pages plus the
two Table 12/13/15 WDB cycles already required by Sessions 38-42. It does not
run another task initializer or publish another kernel descriptor. After page
downloads replace the private kernel records, media uses the established
one-call PM `0x06c8` selected-channel adapter, preserving one continuation per
8 kHz sample.

The final V90D corruption was then reduced to one exact retained-publication
seam. PM `0x1982` builds the overlapping `DM0..DM7` bulk descriptor. Its
same-cycle old-value sequence preserves the incoming far cursor in `DM4` and
consumes the corresponding bound in `DM6`. The INFO-to-V90D handoff left both
zero. PM `0x19c6` is the first point where the newly allocated far-buffer base
is available in `DM3fbc`; publishing it to `DM4` and `DM6` immediately before
calling PM `0x1982` produces:

```text
DM0..DM7 = 1e17/2ad2/0001/0001/1e17/0000/1e17/0001
```

This is not the destructive diagnostic `DM4=DM0` applied after setup. It
models the missing common-layer retained publication at the consumer seam.
The PM `0x1900` sweep no longer reaches `DM1938` or `Core8kRoutine`.

A native-MIPS replay of `run25.rx.ulaw` now reaches the Ja gate, detects Ja,
and transmits only after entering outer state `0x0080`:

```text
page 0x026a: sample 127059
outer state 0x007a: before sample 140000
first nonzero V90D TX: sample 145852, value 4, outer state 0x0080
final outer state: 0x00b0
TX datagrams: 10157/10157 accepted/requested
```

This preserves V.90 §9.3.1.3 silence through state `0x007a`; the first output
is after Ja, not a forced Sd. Reproduce it with:

```bash
/tmp/eicon-venv/bin/python tools/v90_dpcm_replay.py \
  artifacts/eicon-native-tower/run25.rx.ulaw \
  --native-bearer-activation --tx-prbs --to 23.5
```

The result is an offline hardware-capture replay. A fresh tower call is still
required to validate the same retained publication and Sd/S-bar-d stream
against the live analogue modem.

## Session 61: run31 hardware test disproves the DM3fbc cursor bridge

A fresh closed-loop call was run against tower `slmodemd_trnref` with native
event-03 attachment, PRBS TX service and the Session 60 PM19c6 bridge. The
capture is `artifacts/eicon-native-tower/run31.*`. Native activation itself was
successful (`host_writes=2281`, `DM2f08/DM2f09=8000/8000`), and the call
naturally completed V.8 and INFO before loading V90D at sample 86528.

The retained-workspace conclusion from offline run25 was wrong. In run25,
`DM3fbc=0x1e17` happened to be a plausible resident-DM address. On the live
call it was `0x0be9`; it is capture-dependent bulk input, not an allocated
far-delay cursor. Publishing it into `DM4` and `DM6` produced:

```text
sample 86542: DM0..DM7 = 0be9/2ad2/0001/0001/0bea/0000/0be9/0001
sample 86566: descriptor words begin changing to ab3d
sample 87360: bootpage 14 -> 0, followed by nonsensical page/status values
```

The peer reported `NO CARRIER` and hung up after about 15.7 seconds. V90D did
not reach a valid `0x007a -> 0x0080` transition. Supplying a TX datagram at
sample 86529 only serviced `DI_control`; it is not evidence that valid Sd was
transmitted.

The PM19c6/DM3fbc bridge has therefore been removed from the default path. The
native task-relocation and event-03 fixes remain valid, but the retained
ADDSP delay workspace owner is still unresolved. In particular, the correct
`DM4..DM6` values must come from the common-layer allocation lifecycle; they
must not be inferred from `DM3fbc`, copied from a V.32 snapshot, or repaired
after PM1982. Until that owner is recovered, the hardware result supersedes
Session 60's offline transmit claim.

## Session 68: the downstream samples reach the line, and Phase 4 begins

Sessions 60 and 61 left page 14 reaching outer state `0x0072` at best, with the
`0x007a -> 0x0080` transition never observed and no valid downstream transmit.
Two defects between the transmit generator and the line are now fixed, the card
transmits genuine Phase 3 and Phase 4 signals, and a live Courier call reaches
outer state `0x00d0`.

### The generator was never the problem; the block's lifetime was

`tools/v90_dpcm_vector_trace.py` (added this session) traces the six-word
mapping-frame block with the core's DM-write and PM-execution watches. Per
8 kHz frame on page 14:

```text
PM 0x19ee   DM(0x3fb4) = 0x3764              generic pointer re-published
PM 0x2a52   CALL (I4)  with AX0 = 0x3fa7     generator dispatch, 0.167/frame
PM 0x2eef   MX0 = DM(I0)                     serializer reads one slot
PM 0x2ef1   DM(0x3fa7) = <slot, scaled >>2>
PM 0x1a1e   DM(0x3fb4) = DM(0x3fa7)          the sample leaves here
PM 0x07c1   reads DM(0x3fb4)
PM 0x06cd   DM(0x3fa7..0x3fac) = 0  x6       resident kernel frame tail
```

`DM(0x3fa7..0x3fac)` is the V.90 mapping frame, six DS0 samples. The generator
fills it once per 1333 Hz mapping frame — correctly, 8000/6 — and the serializer
walks cursor `DM(0x20de)` across it one slot per 8 kHz frame, so the block must
survive six frames. The resident kernel's frame tail zeroed all six words every
frame, measured at 6.000 executions of PM `0x06cd` per frame against the
generator's 0.167. Five of every six downstream samples were lost, producing the
one-nonzero-in-six impulse train seen on the wire in run33.

PM `0x2a52` is `0x0B001F`, an unconditional `CALL (I4)`: a dispatch table, not a
gate. I4 held `0x3db9` (the Session 54 vector copy) for the first 64 mapping
frames and `0x2199` thereafter. Session 54's "the copy never runs" reading was
really "the copy is one of at least two generators".

Second, `frame_fast()` read `DM(0x3fb4)` as a pointer and returned
`DM[DM(0x3fb4)]`. That is right for the earlier pages, where PM `0x19ee`
publishes `0x3764`, but on page 14 PM `0x1a1e` overwrites it with the sample
itself, and nothing writes `DM(0x3764)` at all while V90D transmits — zero
accesses over a 40-frame watch. Each surviving sample was being turned into
whatever unrelated word lived at its own numeric value.

With both fixed, replaying run33 takes the page-14 output from 2045 to 12125
nonzero samples of 31050, and the first 48 ms carry

```text
1919, 0, 1919, -1919, 0, -1919   repeating on the six-sample mapping frame
```

which is exactly the Sd of §9.3.1.3: 64 repetitions of `{+W, +0, +W, -W, -0, -W}`.
The shape and period are per spec.

### The block clear also publishes silence

Suppressing that clear outright is wrong in the other direction. When the
generator stops, the serializer re-emits the last mapping frame for ever: run35
froze on codeword 148 (linear +13948, near full scale) for 7.02 s and codeword
200 (linear +1372) for 8.64 s, both from the instant the state machine reached
`0x00b3`.

The first correction cleared the block at every cursor wrap, which would have
been worse. Phase 4 opens with Ri, "signal R using the single PCM codeword whose
Ucode is `UINFO` for all data frame intervals" (§9.4.1.1, sent for a minimum of
192T while the receiver is conditioned for CPt) — a constant block is a
legitimate transmitted signal and is indistinguishable from a stale one by
inspection. Clearing on content would replace Ri with silence.

The core can answer the question that content cannot: read the execution count
of PM `0x2a52` each frame and clear only after the generator has produced
nothing for two mapping frames, logging sample and outer state when it does.
A live call then reports whether the generator actually stops.

### run34: the first peer-side measurement of our downstream

Tower `slmodemd`, `artifacts/eicon-native-tower/run34.*`. The card's state
timeline matched the offline replay exactly (`0x007a` at 10.22 s, Ja detected
into `0x007b` at 11.92 s, `0x0080` at 12.20 s, `0x00b0` at 16.00 s) and TX came
up at 12.18 s.

```text
                            run33            run34
Error Energy during TX      -0.000           +117 .. +162 (first +1600)
Phase 3 held after Ja       1.94 s           8.12 s
Timing Offset               -0.000           +4750 .. +4884 ppm
```

The peer's first nonzero Error Energy is at its 794.945 and our TX starts at our
12.18 s — the same instant under the 782.77 s clock offset. The 1.94 s in run33
is the §9.3.2.4 deadline (retrain if the Sd-to-S̄d transition is not seen within
1500 ms of Ja); that deadline no longer fires.

### The +4800 ppm is in the signal, not the sample accounting

`tools/rtp_pcap_timing.py` (added) decodes the capture properly. `RtpCapture`
writes **LINKTYPE_RAW** (101), bare IP with no Ethernet header, so parsing it as
Ethernet produces plausible garbage — constant 146-byte payloads and nonsense
SSRCs — rather than an error. Both directions of run34 are clean 160-byte PCMU
with zero sequence gaps and zero timestamp jumps, reading 8012.84 Hz outbound
against 8010.37 Hz inbound on our host's one wall clock. The shared ~1500 ppm is
a stamping bias; the two media clocks differ from each other by +308 ppm.

Our own accounting is exact: the cursor advances +1 per frame with a -5 wrap
every sixth (32391/6478 over the transmit era), and PM `0x2a52` executes 6479
times in 38870 frames against 6478.33 for an exact 1333 Hz mapping frame — one
single-frame anomaly in the whole era, 26 ppm.

The peer's estimator is trustworthy: on this same rig, locked to our own
sample-exact C implementation, it reports ±0.04 to ±7 ppm (`20260723T045437Z`
shows +0.037, +0.038, +0.041 sustained).

**Methodology warning.** A first version of the refill audit reported ~4% missed
refills and was wrong. It detected refills by watching the block contents change,
which cannot distinguish a skipped refill from a refill that writes the same six
values; with a small symbol alphabet, chance collisions alone produce that rate.
Execution counts are the sound measure. Do not infer generator activity from
block contents.

What remains is content. The spec defines Sd's W as the PCM *codeword* whose
Ucode is `16 + UINFO`, while the card publishes linear values that the harness
mu-law encodes, and that encoding moves them: 943 -> 924 and 1919 -> 1980 on the
wire. Ri's codeword is Ucode `UINFO`, so Ri must be quieter than Sd's W. Measured
against run35: call 3's post-DIL constant is 1372 against Sd's 1980, plausible;
call 2's is 13948, seven times too loud. Whether `DM(0x3fb4)` is a codeword
rather than a linear sample, and whether the resulting Ucode matches `UINFO`, is
open — and it subsumes the serializer's `>>2`, which as a linear scale puts the
line at -20.7 dBm0 and would be meaningless as a codeword.

### Courier: DIL, DSR, and Phase 4 to `0x00d0`

Six live Courier calls, `run35.*` (three) and `run37.*` (three). All six reach

```text
 8.7 s   0x007b -> 0x0080        Sd/TRN1d/Jd, then DIL
12.5 s   0x0080 -> 0x00b0
12.7 s   0x00b0 -> 0x00b1        Rstatus_ch = 0x8200 [change_h|DSR]
12.9 s   0x00b1 -> 0x00b2
14.6 s   0x00b2 -> 0x00b3
```

DSR asserting at `0x00b1` is new. The DIL era is audible and visible as the
stepped TX levels through the `0x0080` dwell.

Five of the six stop at `0x00b3` with the generator genuinely idle — the
coverage-based clear logs it — so run35's constant was a stall, not Ri. run37
call 1 continued:

```text
14.64 s  0x00b3 -> 0x00b6 -> 0x00c0     single level +-924 alternating at 1332 Hz
15.24 s  0x00c2                         levels 652/748/556
16.64 s  0x00c4 -> 0x00c8 -> 0x00ca -> 0x00cc   levels 844/748/556
17.06 s  0x00d0                         outer mode -> 0x0000, dwell = ffff
19.34 s  0x0024                         retrain
```

The 1332 Hz alternation is the mapping-frame rate, i.e. an audible tone, and the
multi-level stretches have the shape TRN2d and MP should have. At `0x00d0` the
transmit mode field drops to zero and the dwell goes indefinite; 2.3 s later the
card retrains from the top.

The Courier is not silent while we wait. Received level is 147 during our DIL
(listening, as expected) and then 2085, 2114, 2119, 2127 across our Phase 4
signals, rising to **2343 during the whole `0x00d0` wait**. It is answering
continuously and the card is not consuming it: `0x00d0` is a receive-side gate,
the same shape as the `0x007a` Ja gate one phase earlier.

### Open

- `0x00d0`: identify what the outer record tests there, and why the Courier's
  continuous Phase 4 response is not accepted. Receive side, not transmit.
- the `0x00b3` stall, five calls in six. The generator stops; the owner is
  unknown.
- codeword versus linear at `DM(0x3fb4)`, and the Ucode/`UINFO` relationship
  above. This is the most likely cause of the peer's +4800 ppm.
- the Session 61 `DM4..DM6` retained-workspace owner is still unresolved, and
  reaching `0x0080` at all still depends on the diagnostic that RTS-es out the
  `0x1900..0x19c8` echo bulk-delay adapter.

### Reproduce

```bash
make -C tools/adsp2181emu
/tmp/eicon-venv/bin/python tools/v90_dpcm_vector_trace.py \
  artifacts/eicon-native-tower/run34.rx.ulaw --to 17.0 --refill-audit
/tmp/eicon-venv/bin/python tools/v90_dpcm_vector_trace.py \
  artifacts/eicon-native-tower/run34.rx.ulaw --to 17.0 --count 0x2a52
tools/rtp_pcap_timing.py artifacts/eicon-native-tower/run34.rtp.pcap
```

Both page-14 diagnostics are env-gated: `EICON_V90D_TX_BLOCK_HOLD=0` restores
the per-frame clear (one downstream sample in six), and
`EICON_V90D_BULK_ADAPTER=1` keeps the echo bulk-delay adapter live, in which
case the outer state machine never reaches `0x0080` and there is no transmit era
to trace.

## Session 69: first downstream data from the card, and the retrain blocker

Session 68 was written before the first Courier connect and is superseded twice
over by what followed. Two live results, both on `artifacts/eicon-native-tower/run37.*`.

### The V.90 connect, with error control on

The Courier trained and reported a connect:

```text
Modulation               V.90/V.34+
Symbol Rate              8000/3200
Speed                    37333/31200
Protocol                 LAPM SREJ 128/15
Retrains Requested       0        Retrains Granted     0
Chars sent 0                      Chars Received 0
Disconnect Reason is XID Timeout
```

37333 bps downstream on an 8000 Hz PCM symbol rate, with **zero retrains
requested**. The blocker recorded in `docs/courier_firmware_analysis.md` and
`docs/v90_phase3_s_and_rbs_false_positive.md` — the Courier retraining after DIL
— did not occur.

`XID Timeout` is V.42 LAPM, not the physical layer: the Courier brought up LAPM,
waited for our XID parameter-negotiation frames, and timed out having moved zero
characters. `eicon_adsp_sip.py --tx-prbs` answers the card's datagram requests
with deterministic pseudorandom bytes, so there are no XID frames to send and
LAPM cannot complete however long it waits. The Eicon path has no V.42 at all.

### With error control disabled, data crosses

Dialled with `AT&M0` so no XID is required:

```text
Modulation               V.90/V.34+
Symbol Rate              8000/2400
Speed                    48000/24000
Protocol                 NONE
Recv/Xmit Level (-dBm)   24.9/17.3  then  18.9/11.3
SNR             ( dB )   42.5
Chars sent 1606                   Chars Received 39507
Retrains Requested       0        Retrains Granted     1
Disconnect Reason is Unable to Retrain
```

**39507 characters delivered downstream**, the card's first data of any kind.
The PRBS arrived as garbage on the Courier's terminal, which is exactly right
with `Protocol NONE`. Downstream rate rose from 37333 to 48000 and the received
level improved by 6 dB between the two `ATI11` reads.

Our side of the same call is `run37` call 6: 3079 RTP packets, 492640 samples
(61.58 s), 46242/46242 TX datagrams accepted. The Courier's `Last Call 00:00:43`
counts from CONNECT while our capture counts from SIP answer, and the difference
(18.6 s) matches this call's training reaching outer state `0x00d0` at 17.0 s.
That reconciles the call-duration mismatch noted while the run was in progress.

### `0x00d0` is not a dead end

Session 68 described `0x00d0` as a receive-side gate ending in a retrain, on the
strength of one call. Three later calls leave it:

```text
17.06 s  0x00cc -> 0x00d0
21.96 s  0x00d0 -> 0x00c2        (call 4 via 0x00bd)
22.56 s  0x00c2 -> 0x00c4 -> 0x00c6
22.9 s+  0x00c6   dwell = ffff, outer mode = 0x147e (transmit active)
```

The transmitted signal there is a multi-level constellation, 14 distinct levels
on the `0x00c2`/`0x00c4` passes and 31 during the `0x00c6` dwell, not a training
pattern. The `0x00b3` stall with the generator idle still happens on some calls;
it is intermittent, not a fixed ceiling.

### The blocker is now our retrain path

`Unable to Retrain`, with `Retrains Requested 0 / Granted 1`: the Courier never
asked for one. Our card restarted its own training three times inside call 6
(`0x00c4 -> 0x0024`, then the full handshake again, ending mid-handshake at
`0x0052`). The Courier granted the first and gave up when the later ones did not
converge.

Throughput matches that picture: 39507 characters in 43 s is about 7.3 kbps
against a 48000 bps line rate, so data moved in bursts between training
restarts rather than continuously. Data mode is reachable and not yet holdable.

### The timing offset is not a fixed property of our downstream

Session 68 recorded +4800 ppm from the tower peer and −4413 ppm from the
Courier as corroborating measurements of one defect. The `&M0` call complicates
that. In the 8000/3200 configuration the Courier read −4432 ppm; after the
retrain to 8000/2400 it read 0.

That second reading cannot be taken at face value: the same register block
reports `SNR 6469.5 dB`, which is impossible, so it was sampled in a disturbed
state. What survives is that the offset differed between two upstream symbol
rates on one call, so it is not a constant of the downstream stream. Settling it
needs a deliberate test — two calls forced to each upstream rate, reading
`ATI11` while `Online` and stable — not inference from post-retrain registers.

### Open

- our retrain path: why the card restarts training from `0x00c4`, and why the
  restarts do not converge. This is what ends an otherwise working connection.
- the transmit level. `Recv Level` of 24.9 dBm and the serializer's `>>2` point
  the same way, and 48000 is short of the 56000 ceiling.
- the timing offset, per the deliberate test above.
- V.42: `--tx-prbs` is why LAPM times out. A real connection needs XID and LAPM
  on the Eicon data path, or `&M0`-style raw operation on the analogue side.
- unchanged from Session 68: the `0x00b3` intermittent stall, the codeword versus
  linear question at `DM(0x3fb4)`, the `DM4..DM6` retained-workspace owner, and
  the bulk-adapter RTS diagnostic that `0x0080` still depends on.

### Reproduce

```bash
/tmp/eicon-venv/bin/python -u tools/eicon_adsp_sip.py \
  --native-mips --force-info-after-v8 --native-bearer-activation --tx-prbs \
  --trace-v90d-state --law pcmu --capture-prefix artifacts/eicon-native-tower/runNN \
  --mips-kernel artifacts/eicon-dsp/build-117-926/kernel/0009-diva-server-pri-30m-kernel \
  --mips-tikrnl artifacts/eicon-dsp/build-117-926/tikrnl/0258-tikrnl81.f34-task \
  --registrar asterisk.net.cryan.nz --username 6001 --password 6001
./.venv/bin/python tools/cx_at.py --dev /dev/cu.usbserial-21210 dial 6001 --wait 120 --pre 'AT&M0'
./.venv/bin/python tools/cx_at.py --dev /dev/cu.usbserial-21210 usrdiag
```

Take `ATI6` and `ATI11` while the call is still `Online`, not only after it
drops: the post-disconnect register block is not reliable.

## Session 70: the media budget, and why a hitch lands on DIL

The card connects, but the calls are intermittent and the symptom is a repeated
DIL. Logging was the suspect. It is not the cause: measured on run19's received
audio, the whole diagnostic apparatus costs about 0.5 ms of the 20 ms media
tick, and the media path had two structural faults that turn any lost wall time
into corruption of the sequence the Courier is measuring.

### What a 20 ms tick actually costs

`tools/eicon_adsp_sip.py` runs the pump, the SIP/RTP sockets and every
diagnostic on one thread. Per 160-sample quantum, replaying
`artifacts/eicon-native-tower/run19.rx.ulaw` (arm64, `-O3` core):

| component | per tick | note |
|---|---|---|
| `_step_mips` | **8.4 ms** | one MIPS main-loop pass per RTP packet, `max_insns=500_000` |
| `_frame_core` x160 | 2.5 ms | 9-18 us/sample; page 14 is the expensive page |
| `write_diag` (CSV + DM + SCC) | 0.06 ms | ~1400 DM reads, three unbuffered files |
| `RtpCapture.write` x2 | 0.06 ms | pcap + G.711 + WAV, both directions |
| `[v90d]` trace | 0.42 ms | format 64 lines; the tty write itself is 0.13 ms |

So 11 ms of every 20 ms, i.e. a 55% duty cycle with no elasticity, and 0.5 ms
of that is diagnostics. Mid-call overlay loads through the MIPS loader are 3-9
ms, not the hundreds of ms they look like: the one 363 ms sample is Unicorn
warming up on the first tick of the call, before any modem state exists.

The `[v90d]` trace becomes 3200 lines/s the moment page 14 reaches state
`0x0078`, because the key includes the per-symbol equaliser words `DM(0x11f5)`
and `DM(0x11f6)`. That is a DIL-era artefact of the trace, not a cost problem.

### The two faults

- **No jitter buffer.** RTP arrived on the peer's clock and was consumed on
  ours, and an empty queue substituted `self.silence` per sample. One late
  packet put 20 ms of invented silence into the middle of the DIL sequence, and
  the analogue modem answers a hole in that sequence by asking for it again.
- **Uncapped catch-up.** After any stall, `while now >= call.next_tick` ran
  every elapsed quantum back to back without servicing the RTP socket, so the
  receive queue stayed empty for the whole burst and then overflowed its 1 s cap
  and discarded arrivals wholesale.

Driven with a stub pump at the measured 11 ms/tick, an 8 ms jittered feed and
one 300 ms process stall over 6 s:

```text
before: substituted 3680 samples (460 ms of silence), dropped 640, TX 300/303
after : substituted 0, dropped 0, TX 302/302, +42 ms latency, queue drains to 0
```

The modem's clock is virtual, so the fix is to hold it rather than invent
samples: `rx_ready()` waits up to `--rx-hold-ms` for late audio, and a queue
above `--rx-jitter-ms` + one packet is drained ahead of the wall clock so the
latency is given back instead of becoming permanent one-way delay. Catch-up
is capped at `--catchup-quanta` per wake-up so RTP keeps being read. Every
substitution, discard, hold and over-budget tick is counted and reported once
per second as `[media]`, so "flaky" is now a number.

### Headroom

`--mips-interval 320` (one MIPS pass per two RTP packets) takes the pump from
0.57x to 0.34x real time -- 11 ms/tick down to 6.8 ms -- and on run19 the
per-second TrnProgress timeline is identical at both intervals, page 14 and
state `0x007a` included. It is a knob, not a default: it was verified on one
capture, not across the retrain path.

## Session 71: USRobotics V.92 interop makes V.42 the next layer

A USRobotics 56K Fax External V.92 (product `00568606`, V5.4.5 dated
2012-06-29, DSP rev 15) on `/dev/cu.usbserial-21240` was called four times.
All runs used the native MIPS path, `--tx-prbs`, the Session 70 media pacing,
and `--mips-interval 320`. Evidence is under
`artifacts/interop/usr-v92-21240/`.

Two calls with the modem's normal fallback policy connected identically:

```text
CONNECT 45333/V90/NONE
Protocol               NONE
Speed                  45333/21600
Modulation              V90/V34
Symbol Rate             8000/3200
Recv/Xmit Level (-dBm)  25/12
SNR (dB)                46
Timing Offset (ppm)     0
Retrains Requested/Granted  0/0
```

Both traversed `0x00b3 -> 0x00b6 -> 0x00c0 -> 0x00c2 -> 0x00c4 -> 0x00c6`.
The calls were deliberately ended by DTR after six online seconds. There were
no substituted or dropped RX samples; each run had only three over-budget
media ticks. Unlike the older Courier result, this peer therefore gives a
repeatable physical V.90 link without first exercising the retrain defect.

Two calls forced to ARQ-only mode with `AT&M5` failed identically. The Eicon
reached and remained at `0x00b3`; after about 38 seconds the USR returned
`NO CARRIER`, `Last Call 00:00:00`, and no physical-layer diagnostics. The
receive stream was intact (zero substitutions and drops), so this is not a
jitter artefact. `&M5` requires an error-controlled connection and refuses the
raw fallback used by the successful calls.

### Priority

Implement V.42 XID/LAPM on the Eicon data path next. Raw V.90 is now repeatable
against this modem, while the normal error-controlled service is the exact
boundary between two successful and two failed calls. This is a more useful
next interoperability layer than further tuning the successful raw training
path. The Courier retrain defect remains independently open and should still be
fixed before retrain/rate-renegotiation can be called interoperable.

## Session 72: CX93001 V.34 does not reach the V.42 boundary

A Conexant `CX93001-EIS_V0.2013-V34` on
`/dev/cu.usbmodem246802461` was tried twice with `--tx-v42` and once with raw
PRBS (`AT\\N0`). It advertises V.34 through 33600 but no V.90. All three calls
selected V.34 cleanly (`bootpage 7 -> 8`, overlay `0x0261`) and then returned
`NO CARRIER`; none reached a published DATASTATE speed or emitted an upstream
HDLC frame. The V.42 counters consequently remained zero.

The synchronous host adapter was extended to service V.34 as well as V90D:
V.34 TXD0 is packed oldest-bit at bit 15, unlike V90D's oldest-bit at bit 0,
and its negotiated packet size comes from the 2400-Hz DATASTATE rate. On the
live call the V.34 page raised one TX request immediately after loading, but
never cleared it after the host supplied TXD0 (`TX datagrams 0/1 accepted`).
The same physical failure in `AT\\N0` proves this test is not blocked at XID.
Before this peer can validate LAPM, the V.34 page-8 mailbox/bring-up path must
be recovered far enough to establish a raw carrier.

Evidence is under `artifacts/interop/cx93001-v34-246802461/`.

## Session 73: page 8 was never dispatched

The page-8 silence was not initially a V.34 detector or mailbox failure. An
execution watch after the INFO-to-V.34 handoff counted PM `0x06c8` once per
sample but zero executions of PM `0x0703`, the selected-channel foreground,
zero of `Core8kRoutine` wrapper `0x19d5`, and zero of the V.34 symbol routine
at `0x27dd`. Only TIKRNL's input ring changed. The SIP adapter resumed the
kernel tail directly after every SPORT frame; V.34 masks the SPORT interrupt
used by the compatibility shortcut, so nothing invoked the page.

For resident V.34 page `0x0261`, the native adapter now resumes at PM `0x02b7`.
That is the real selected-channel foreground: it reads the queued SPORT word,
calls PM `0x0703`, and PM `0x076a` dispatches `DM(0x3fb3)`. Offline replay then
showed live V.34 state (`DM119d`, carrier words and ring cursors) where
previously every internal word was frozen. This establishes that the page is
executing, but not yet that its Phase-3 transmitter is running.

It does not connect yet. Three post-fix calls still ended `NO CARRIER` with
published `TrnProgress=0x52`. V.34 leaves its foreground continuously live,
unlike the run-to-idle V.90 pages, so instruction cadence is now the critical
boundary. At the existing 20,000-instruction allowance the media tick is often
over budget and the peer responds but does not advance; the ADSP-2185N's
nominal 80 MIPS implies 10,000 instructions per 8-kHz sample, which restores
headroom but did not produce the expected answer-side S/PP sequence in the
first call. `EICON_V34_CYCLES_PER_SAMPLE` selects this budget (default 20000)
without another code change. The next work is to pin the fitted clock/cycle cadence and trace
`DM2147`'s `0x52` transition conditions against V.34 §11.3.1.2, not to force a
state or add synthetic tones.

Post-fix evidence is `call5-v34-foreground`, `call6-v34-10k`, and
`call7-v34-20k-m800` under the Session 72 artifact directory.

## Session 74: the apparent Phase-3 output was stale INFO

A database-level audit found the critical overclaim in Session 73. ADDSP guide
§5.3 defines `DM(0x3fa7..0x3fac)` as `TXSAMPLE_0..5`; the V.34 core must fill
three to six samples there at symbol rate and TIKRNL serializes them through
`ShellOutptr` at `DM(0x3fb4)`. From the page-8 handoff through the entire replay,
all six TXSAMPLE words remain zero, `ShellOutptr` remains `0x3764`, and
`DM(0x3764)` remains zero. Thus page 8 still emits genuine silence.

The energy in `call5-v34-foreground` was the preceding INFO waveform retained
until about 7.3 seconds because its 320-sample host stepping cadence delayed the
handoff. With the 800-sample cadence (`call7`) TX becomes zero exactly at the
5.08-second page switch and stays zero. The CX93001 bursts attributed to a V.34
response were consequently responses/retries around stale Phase-2 signaling,
not proof of S/PP/TRN interoperability.

The answer/call role was checked as the obvious deadlock explanation. The live
answer WDB retains `GEN_SETUP1=0x0484` (Table 15 answer mode); changing it to
`0x048c` calling mode prevents this peer capture from progressing through V.8.
Role selection is therefore not the missing publication. PM `0x0703`, wrapper
`0x19d5`, and V.34 symbol code `0x27dd` now execute, but the symbol scheduler
never fills TXSAMPLE at state `0x52`. The immediate target is the scheduler's
sample-buffer-empty/GEN_CONTROL condition and the page-8 initialization that
feeds it—not SIP, LAPM, or TXD0.

## Session 75: preserve the driver's native CAI-to-WDB initialization

The `divas4linux` source provides a concrete correction to the initialization
rig. `kernel/message.c:add_b1()` marks an incoming call `CALL_DIR_ANSWER`, builds
the 26-byte hardware-modem CAI, and attaches it to `CALL_RES`. The closed card
firmware translates that transaction before Python constructs
`NativeMipsModem`. Its pending write database is not the generic ADDSP example:

```text
00: 0040 0024 0038 0008 0000 0000 2105 f1fd 000c 000c 00b8 0033 0003 0000 2000 abcd
10: 00ff 0080 0060 0046 0050 0023 0041 0050 0005 0005 0000 000f 0040 000a 0029 010c
20: 0116 0000 002b 0001 000c ffff ffff ffff 0001 a13f 001f fffe 0003 0014 0000 0000
...
70: 0000 0000 0000 0000 0000 0003 0000 0000 0000 003f ffff 0377 000e 0015 000e 0015
```

The old `attach_connected_bearer()` discarded this and substituted handbook
values including `Norm_L=0x8100`, `speed_sel_l=0xff00`, and
`INFO0D_setup=0x03b7`. Native CALL_RES had selected `0xa13f`, `0xfffe`, and
`0x0377` respectively, plus CAI-derived timing and capability words. That
replacement had no analogue in the Linux driver.

`complete_native_answer()` now snapshots `DM(0x3ee0..0x3f7f)` before loading
DIAL. Native activation consumes that exact firmware-produced WDB as its first
communication cycle. After DIAL imports its defaults, the second cycle
republishes the same native transaction and changes only the two operation
words required by ADDSP Table 15: `GEN_SETUP1=0x0484` and
`GEN_SETUP2=0x0030`. The generic Tables 12/13 setup remains only as a fallback
for the non-native harness.

Offline replay still reaches page 8 and state `0x52`, so this cleanup does not
by itself start TXSAMPLE. It does remove the hand-built configuration as a
confounder. An init execution watch also confirms the page-8 chain is complete:
`Init8kRoutine 0x19d2` calls V.34 `InitRoutine 0x1000` once (about 14k emulated
instructions), followed by `Core8kRoutine 0x19d5` and symbol routine `0x27dd`.
The unresolved boundary is therefore after V.34 init entry and before its
GEN_CONTROL/TXSAMPLE publication.

## Session 76: live CX93001 test with native driver WDB

The driver-faithful initialization was tested live against the
`CX93001-EIS_V0.2013-V34` on `/dev/cu.usbmodem246802461`, forced to raw mode
with `AT\\N0`. The call progressed cleanly through V.8 and INFO, loaded page 8
at sample 43040 (5.380 s), and published `TrnProgress=0x52`. It then ended
`NO CARRIER`; no synchronous TX datagram was accepted (`0/1`).

The waveform confirms the page boundary exactly. Local TX RMS falls from 766
in the 5.3-second bin to zero at 5.4 seconds and remains zero; peer RX falls to
near-codec silence at the same point. Thus preserving the native CAI/WDB does
not by itself clear the page-8 scheduler gate, but it validates the failure
against hardware without the generic initialization overwrite. Evidence is
`artifacts/interop/cx93001-v34-246802461/call9-native-cai-wdb*`.

## Session 77: V.34-only CAI and page-8 scheduler audit

The Linux driver's modem CAI has a real V.90-disable control (`cai[10]` bit 7).
`EICON_FORCE_V34=1` now sends that control through native CALL_RES and caps the
CAI rate at 33600, instead of patching the translated DSP database. A live
CX93001 call forced to V.34 with `AT+MS=V34,1,2400,33600,2400,33600` followed
the same path: page 8 at 5.12 seconds, state `0x52`, silence, then `NO CARRIER`.
The option is retained as a diagnostic because it removes V.90 policy at the
correct driver boundary, but it is not the page-8 fix. Evidence is
`call10-force-v34-cai*`.

The scheduler audit found that V.34 `CoreRoutine` PM `0x27dd` deliberately
clears `GEN_CONTROL` at `0x27ea` on every symbol invocation; state actions must
set it again. The stop branch at `0x290c` is not being taken (`DM2165=0`). The
active action cursor cycles around `DM2166=0x10..0x12`, while the generator
actions PM `0x23a0/0x23a3/0x23a7` are present later in the low-DM action table.
`GEN_SETUP1=0x0484` is correctly imported into both `DM219c` and `DM21e5`, and
the V.34 init briefly publishes generator controls `0x0180` then `0x0080`
before the state-`0x52` actions clear them. This localizes the remaining bug to
selection/advancement of the Phase-3 action stream, rather than a skipped init,
a stopped core, or an absent generator implementation.

## Session 78: INFO handoff A/B and SPORT-format falsification

`tools/eicon_handoff_compare.py` now locates the last complete EADSPDM2 record
before an overlay transition and compares all 256 ADDSP interface words. The
working USR V90D handoff (`call4`, page `0x026a`) and failing CX93001 V.34
handoff (`call9`, page `0x0261`) both finish INFO at `TrnProgress=0x4f` and
carry `BaudInfo` high bits `0x3000`. Their expected modulation fields differ:

```text
                         V90D       V.34
BaudInfo                 3064       305d
INFO mode selector       0009       0000
INFO variant             000e       0008
INFO internal progress   004f       004f
```

The shared `0x3000` high field disproves the simple theory that V.34 alone
carries a reversed call/answer bit there. Old generic-init CX (`call3`) and
native-WDB CX (`call9`) have identical `BaudInfo=0x305d`, selector 0 and variant
8 and both fail, despite their configuration words differing substantially.
The V.34-only CAI run also arrives with `BaudInfo=0x305d`. There is no malformed
or uninitialized INFO mode value unique to the failing native setup.

A second A/B tested whether the modem's real V.34 output bypasses
`ShellOutptr/TXSAMPLE` and appears in the SPORT0 TX latch. It does not. At the
page-8 handoff the first latch word is the prior RX sample; it then freezes at
`0xf9a4` for all 23,810 observed page-8 samples. It matches neither current nor
one-sample-delayed RX and is never updated by the V.34 generator. Publishing it
would put a stale DC word on the line, exactly why the adapter already discards
that kernel latch. Thus this is not a hidden output-format path: the page truly
fails to publish samples.

The remaining format-sensitive boundary is internal, between decoded INFO
state and the V.34 action-table selection. The captures rule out G.711/SIP
output extraction and the obvious `BaudInfo` role-bit hypothesis; the next
trace must identify which writer keeps the state-`0x52` action cursor in its
receive/wait loop instead of advancing to PM `0x23a0..0x23a7`.

## Session 79: action-table trace and synthetic PC-stack overflow

Execution watches resolved the apparent action-table stall. `DM(0x2166)` is
the current action index: PM `0x28be` initializes it to `0x10`, PM
`0x2834..0x2836` advances it through `0x11`, `0x12`, and `0x13`, and PM
`0x286d..0x2870` resets it to `0x10`. The dispatcher does reach generator
action PM `0x23a0` and handlers PM `0x285c`, `0x2868`, and `0x2879`; the table
entry itself is valid. `DM(0x2291)` is instead the receive/sample queue count,
initialized to 8 at PM `0x0fbf` and consumed at PM `0x0fa3..0x0fa5` through
buffer pointers `DM(0x228f/0x2290)`.

Adding PC/count/loop stack depths to execution watches exposed the real fault.
The PC stack reached its hardware depth of 16 with this tail:

```text
...,02a8,02a8,02a8,02a8,0773,19d7,1712,1729,27fd,3617,3675,369a,36a1,3e0a,3b88
```

`0x02a8` is the resident IDLE sentinel supplied as the return address by
host-injected ADSP service calls. Some firmware paths jump to that IDLE instead
of executing `RTS`, leaving the synthetic return on the PC stack. Reinjecting
the per-sample continuation accumulated duplicate `0x02a8` entries. Once the
PC stack overflowed, a `DO` at PM `0x359d` could not push loop start `0x359e`;
its first iteration instead returned through unrelated caller PM `0x3b88`.
That recursively called PM `0x3dc1/0x3598`, filled the four-deep count and loop
stacks, left `CNTR=6`, and produced the misleading `DM2166=0x10..0x12` wait
loop.

`adsp2181_call()` and `adsp2181_modem_sample()` now discard consecutive stale
copies of their own synthetic return sentinel when re-entering from IDLE,
without disturbing the underlying firmware call frames. In the same offline
CX93001 replay, PC stack depth then remains 5 at `Core8kRoutine`, count and loop
stacks return to zero, PM `0x23a0` is reached again later in Phase 3,
`GEN_CONTROL` becomes nonzero, and the adapter publishes 77 nonzero V.34
samples between replay samples 40190 and 40272. The final zero block occurs
after the captured peer signal ends, not at the former page-8 handoff. This
clears the emulator-side action scheduler blocker; live hardware validation is
next.

## Session 80: defer firmware-side answer until SIP INVITE

The native SIP endpoint previously created a synthetic incoming call during
server startup. Consequently the MIPS firmware could finish CALL_RES and
connect its bearer before any SIP INVITE existed; accepting RTP later did not
model the ordering of a real network SETUP and answer. Native initialization
is now deferred until the first valid INVITE. The endpoint immediately sends
`100 Trying`, synchronously runs firmware entry, incoming-call assignment,
ADDSP answer completion, and bearer attachment, and sends `200 OK` with the
connected media line only after all of those steps return. This also prevents
INVITE retransmissions during the relatively long firmware setup without
exposing RTP prematurely.

## Session 81: the media budget again — the MIPS cost was the trace, not the MIPS

Session 70 measured `_step_mips` at 8.4 ms of the 20 ms media tick and treated
that as the price of running the firmware. It was not. Almost all of it was one
line of instrumentation.

`MipsShim.__init__` registered `hook_add(UC_HOOK_CODE, self._hook)` with no
address range, so every MIPS instruction became a Python callback. Unicorn only
instruments translation blocks that overlap a code hook's range; a rangeless
hook instruments all of them. Measured on `call1.rx.ulaw` with the native
harness, the supervisor executes about 12200 instructions per RTP packet and
took 8.5 ms to do it — roughly 700 ns per instruction, against about 20 ns
uninstrumented. Per callback the hook also re-ran `from unicorn.mips_const
import ...` and appended a formatted string to an unbounded list.

That list is the second fault. `trace_log` held every executed instruction
address: 17.5 million entries and 813 MB of RSS after 20 s of call, growing for
as long as the call lasted. Its only consumers were a 12-to-24-entry tail on
fault, and `.count()` of two addresses during boot.

### What replaced it

- One single-address `UC_HOOK_CODE` per intercepted address
  (`INTERCEPT_ADDRESSES`). The host-port helpers, `SERVICE_ASSIGN`, `SWITCH_ON`,
  the connected-driver publish and the stub return are all function-entry
  interceptions, so a hook per address is what the code always meant.
- `SERVICE_ASSIGN`'s return address is only known at call time and gets a hook
  added when captured. Removals are deferred to `_flush_hook_dels` before the
  next `emu_start`: `Uc.hook_del` drops the binding's reference to the ctypes
  trampoline, so calling it from inside its own callback frees the executing
  callback.
- A `UC_HOOK_BLOCK` hook carries the ADSP pump and instruction accounting.
  `_insn_count` still advances by real instruction counts (`size >> 2`); only
  the pump's phase within a basic block changes, and that phase never meant
  anything. The pump is load-bearing, not vestigial: with the block hook
  replaced by a no-op, boot fails at `native incoming call did not assign a
  modem DSP core`.
- `trace_log` is a 256-entry deque, and `exec_counts` replaces counting strings
  in it.
- `trace_calls` became a property that attaches a rangeless code hook only while
  the diagnostic is on. It is the one caller that genuinely needs every
  instruction.
- `NativeMipsModem` caches the `pm` pointer alongside `dm`. Both are fixed
  members of the core struct; `_frame_core` was re-crossing the FFI for it 8000
  times a second.

### Measured, on `usr-v92-21240/call1.rx.ulaw` to 20 s

| | before | after |
|---|---|---|
| per media tick | 11.04 ms | 3.91 ms |
| `_step_mips` | 8.91 ms | 1.88 ms |
| `_frame_core` x160 | 2.12 ms | 2.03 ms |
| real-time factor | 0.55x | 0.20x |
| peak RSS | 813 MB | 93 MB |
| boot to answer | 4.9 s | 1.7 s |

The overlay-switch list, the full TrnProgress timeline, the V90D outer-state
timeline and a SHA-256 of every transmitted sample are all byte-identical
across the change, with `EICON_MIPS_WARMUP=0`. The `--mainloop --connect
--simulate-b-channel` CLI path is byte-identical too.

### The first tick, and the one behavioural change

Unicorn JITs on first execution, and the mainloop path taken once the bearer is
up is not the path boot took. The first in-call `_step_mips` cost 93 ms and the
next 1.8 ms — five media ticks lost at the instant the call goes live, while
DIAL is choosing a modulation. On a live loopback call it measured worse still,
390 ms, because it contends with the socket loop.

`NativeMipsModem.warm_up()` runs three idle supervisor passes at attachment, so
the translation happens before the sample clock starts. This is the only change
here that is not behaviour-preserving: three extra polls move the whole replay
timeline one sample earlier (`0x0260` at 29438 becomes 29437), and on this
capture the run then also reaches outer state `0x007b`, which the baseline never
did. Arguably it is the more faithful model — a real card's supervisor runs
continuously during the SIP answer gap, where this harness ran zero polls,
because polls are driven by the sample clock. It is not proven better.
`EICON_MIPS_WARMUP=0` restores the old behaviour; pin it when diffing against a
recorded capture.

### Live loopback A/B, 22 s of `call1.rx.ulaw` at real-time pace

| | before | after |
|---|---|---|
| SIP answer latency | 4.79 s | 1.78 s |
| ticks over 18 ms | 2 | 0 |
| worst tick | 390.2 ms | 13.3 ms |
| catch-up deferrals | 20 | 0 |

The 390 ms tick and its 20 catch-up deferrals are the Session 70 corruption
mechanism firing on every call, at the worst possible moment. Substituted RX
samples were 160 before and 320 after, which is loopback jitter from a naive
same-machine sender, not signal.

### Not done, and why

`_set_load_result` is dead code. It reads PC inside a memory hook to decode the
load instruction, but decoded zero loads out of 100627 data-port reads — both
before and after this change. IDMA read-back works through the `mem_write` page
patch beside it. Removing the global code hook did not regress it; it never
worked.

The remaining `_step_mips` cost is about 3600 block callbacks per RTP packet,
roughly 1.4 ms. Chunking `emu_start` on exact instruction counts would remove
them, but resuming mid-stream risks splitting a MIPS branch-delay slot, and at
3.9 ms of a 20 ms budget the headroom is no longer worth that. For the same
reason the single-core question is closed: the pump does not need a second
thread, it needed a hook range.

At 3.9 ms/tick the Session 70 pacing defaults are conservative —
`--catchup-quanta 2` was chosen against an 11 ms tick. Untested on hardware.

## Session 82: Session 75 dropped the V90_DPCM enable, and replay cannot see it

Reported symptom: the V.34 work regressed V.90, with a suspicion of missing
nuance between the DSP and the MIPS regarding modulations. The suspicion is
correct and the word is `V8_SETUP`.

### The bisect that found nothing, and why

Replaying `usr-v92-21240/call1.rx.ulaw` to 20 s at `bb8dd03` (pre-V.34),
`16a7428`, `c50f43f`, `bb1adb5` and `4e71e5d` gives byte-identical output at
every commit, on both bearer paths. Without `--native-bearer-activation`,
page 14 transmits 0.0% non-zero at all five; with it, 63.7% at all five.

That is not evidence of no regression. `v90_dpcm_replay.py` is open loop: the
recorded RX stream already contains the peer's responses, so it contains a
V.90-accepting answer no matter what the card offered. A capability the card
fails to advertise costs nothing in replay and everything on a live call, and it
will look intermittent rather than broken, because the peer's decision is
marginal rather than forced. No offline harness in this repo can settle a
question of this shape.

### What actually changed, and where

Probing `DM(0x3ee0 + n)` immediately after bearer attachment on the
`--native-bearer-activation` path:

| write DB | bb8dd03 / 16a7428 | c50f43f onwards | documented meaning |
|---|---|---|---|
| `V8_SETUP +04` | `6000` | **`0000`** | **V90_DPCM + digital network** |
| `INFO0_SETUP +07` | `f0fd` | `f1fd` | V.34 Phase-2 capabilities, 3429 |
| `NORM_L +29` | `9100` | `b13f` | V.90 + V.34 |
| `SPEED_SEL_L +2b` | `ff00` | `fffe` | V.34 fallback rates through 33600 |
| `INFO0D_SETUP +7b` | `03b7` | `0337` | 3429 upstream, µ-law, −12 dBm0 |

The transition is exactly `c50f43f`, Session 75. That session replaced the
hardcoded ADDSP table with the driver's native CAI-to-WDB transaction, which was
the right correction — the handbook table had no analogue in the Linux driver.
But the native transaction leaves `V8_SETUP` at zero, and the second
communication cycle now overrides only `GEN_SETUP1` and `GEN_SETUP2`, so nothing
supplies it. Tracked across a whole call, `DM(0x3ee4)` is `0000` from
attachment to page 14 and never written. The session's own audit at
"Host-bit audit: V90D is enabled" documents `6000` as the V90_DPCM and
digital-network enable, so the card is now completing V.8 without having
requested V.90 DPCM in its setup word.

`NORM_L` is not the problem: `b13f` is a superset of `9100` in the bits the
handbook assigns, so V.90 and V.34 remain enabled there. `INFO0D_SETUP` is worth
a second look — `03b7` against `0337` differs in bit 7 only, inside the field the
audit reads as µ-law upstream and codec measurement, and every call here is
µ-law.

### Where the missing nuance probably is

`V8_SETUP` bit 13 is "digital network". That is a property of the bearer, not of
the modem CAI: on a PRI card the DSP has to be told it sits on a digital DS0.
This harness fabricates parts of call ingress (`fake_call_ingress`,
`inject_call_ingress`), so the likeliest explanation is that the real
CALL_RES/bearer path sets that bit and the synthetic ingress does not — i.e. the
MIPS never learns it is a digital call, so its CAI translation cannot enable
V90_DPCM. Bit 14 would then follow from the CAI, whose `cai[10]` bit 7 the
driver uses to *disable* V.90. `modem_cai()` also only ever offers
`DSP_CAI_HARDWARE_MODEM_ASYNC` (0x11), never `_SYNC` (0x12), which is unexamined.

Forcing `V8_SETUP` is a workaround, not the fix; the fix is to find who should
have set it.

### The lever

`EICON_WDB_OVERRIDE` applies words to the answer-cycle WDB on top of the native
transaction. Empty by default, because defaulting it on would silently
reintroduce the confounder Session 75 removed.

```text
EICON_WDB_OVERRIDE=0x04:0x6000
```

Confirmed inert offline, as predicted: with the override on, the 20 s replay is
identical except for its own log line. It has not been tested on hardware. That
test is the next step, and it is the only thing that can decide this.

## Session 83: correcting Session 82, and the page-14 exit that breaks fallback

### Session 82's conclusion was wrong

Session 82 found `V8_SETUP +0x04` at `0x0000` since Session 75 and framed the
missing V90_DPCM/digital-network enable as the V.90 regression. Hardware says
otherwise: V.90 **does** connect on the `--native-bearer-activation` path, with
`V8_SETUP=0x0000` throughout, and does not connect without that flag. So the
word is not a V.90 blocker and the handbook value is not required to reach a V.90
connection. The `EICON_WDB_OVERRIDE` lever stays, because the documented-vs-native
disagreement is still real and `INFO0D_SETUP` bit 7 is still unexplained on a
µ-law call, but it is no longer a suspect for "V.90 does not connect".

The reasoning error was treating an offline replay's ability to reach page 14 as
weak evidence and the capability table as strong evidence, when the only real
evidence available was a live call. Session 82 said no offline harness could
settle the question and then leaned on offline reasoning anyway.

The actual open items, from hardware:

- V.34 does not connect at all.
- V.90 connects only with `--native-bearer-activation`.
- A V.90 attempt that falls back to V.34 **drops the call**.

### The dropped call was the endpoint exiting

`EiconSipEndpoint.run()` had no guard around `media_tick`. Any exception from the
emulated pump propagated out of the loop, ran the `finally` that closes the
capture, and terminated the process — so the far modem saw the call vanish, and
the endpoint log ended mid-sentence.

`call10-force-v34-cai.endpoint.log` is exactly this, and it was in the artifacts
the whole time: it stops after `TrnProgress 0x0037` with only `[capture] wrote`,
and no `[call] ended` or `[media] call totals`, where `call9` has all three. A
firmware fault and a peer hanging up produced indistinguishable logs.

`run()` now calls `fail_call()`, which prints the traceback plus the overlay,
bootpage, TrnProgress and Rstatus at the fault, drops the call, and keeps the
endpoint listening. Verified by injecting a fault into a live loopback call: the
endpoint survives, reports `overlay=0x025f bootpage=6 TrnProgress=0x0004`, and
answers a second INVITE with a fresh firmware boot.

Replaying `call10-force-v34-cai.rx.ulaw` under the current tree no longer raises;
page `0x0261` loads at 5.037 s and reaches `TrnProgress 0x0071`. Session 79's PC
stack fix accounts for that, so the fault that killed call10 is likely already
gone — but the containment is what makes the next one legible.

### PM 0x06cd was never restored on the way out of page 14

The real fallback defect. The overlay switch reads:

```python
self.load_native_overlay(wanted)          # sets self.resident = wanted
...
elif self._v90d_saved_clear is not None and self.resident == 0x026A:
```

`load_native_overlay` assigns `self.resident = download_id` before returning, so
on the way out of page 14 `self.resident` is already the new page and the
condition can never hold. `previous` is captured immediately above for precisely
this test and was unused. The restore was dead code from the moment it was
written.

PM `0x06cd` is the six-count store that clears the V.90 mapping-frame block
`DM(0x3fa7..0x3fac)` in the resident kernel's frame tail. Page 14 needs it
suppressed. It is in the **resident kernel, not the overlay region**, so no later
page load replaces it. Measured on `usr-v92-21240/call1.rx.ulaw`:

```text
before:  6.6730s page=0x026a PM[0x06cd]=0x000000
        10.9977s page=0x0270 PM[0x06cd]=0x000000   <-- still NOP
        10.9979s page=0x0260 PM[0x06cd]=0x000000   <-- still NOP
after :  6.6730s page=0x026a PM[0x06cd]=0x000000
        10.9977s page=0x0270 PM[0x06cd]=0xa00001   <-- restored
```

So every page following a V.90 attempt ran on a kernel whose per-frame clear of
the mapping-frame block never executed. That is the V.90-to-V.34 fallback path,
and it is also the retrain path — the card restarts training from `0x00c4` by
leaving page 14, which makes this a candidate for the Courier retrain blocker in
Sessions 69 and 71 as well. Fixed by testing `previous`.

The sibling diagnostic at PM `0x19c8` (the bulk-adapter RTS) is deliberately not
restored: unlike `0x06cd` it sits inside the page-14 overlay region, so the next
overlay download overwrites it, and writing a saved page-14 word back over V.34
code would corrupt it.

### Still open

None of this explains why V.34 does not connect, or why V.90 needs
`--native-bearer-activation`. Both are now testable against a card that no longer
silently inherits a broken kernel, and against a log that distinguishes a fault
from a hang-up.

## Session 84: a terminal on the V.42 link

The V.42 endpoint could establish a link and acknowledge I frames but could not
send one. Every `_queue()` call site was XID, UA or RR: there was no V(S), no
window, no segmentation and no retransmission, and `rx_data` was a sink whose
only consumer was `len()` in the BYE handler, so payload the peer sent was
counted at hangup and discarded. There was no terminal device of any kind.

### Transmit side

`LapmEndpoint` now has the other half. `send()` appends to a byte stream;
`_fill_window()` segments it into N401-sized I frames while
`(V(S) - V(A)) mod 128 < k`, stamping each with the current V(R) so I frames
carry the receive acknowledgement. Incoming N(R) on both I and S frames releases
the window. RNR stops it and RR resumes it. REJ and SREJ go back N, and the
retransmitted frames are rebuilt rather than replayed, because N(R) has to carry
the receive state as of the retransmission and not as of the original. SABME
resets V(S)/V(A) and drops the unacknowledged set, but keeps unsent application
bytes, which were never on the wire.

Recovery is counted in `take()` calls, not seconds. The bit pipe is clocked by
the data pump and the harness can run at 0.2x real time, so a T401 in seconds
would fire at meaningless points during a replay. A stalled window is first
probed with RR(P): the likeliest cause is a lost acknowledgement rather than a
lost I frame, and the response carries the N(R) that resolves it without
resending anything. Only if that fails does it go back N.

Not done: V.42bis, and XID parsing for the peer's k and N401. Those remain at
the V.42 defaults as constructor arguments, which is the same conservatism the
existing XID echo already had.

### A pre-existing framing bug in `take()`

Found by the window test, not by inspection. Idle fill was synthesised one bit at
a time from `_idle_index`, so when a frame was queued partway through a flag, the
next `take()` switched to frame bits and abandoned the flag half-emitted. The
receiver then had to resync on the malformed delimiter and consumed the following
frame doing it, so **the first frame after any idle gap was lost**. With the
endpoint unable to transmit I frames this could only ever have corrupted an XID,
UA or RR, which is probably why it went unnoticed.

Idle flags are now queued whole into the same deque as the frames, so a partial
flag finishes before frame bits follow it even across calls.

### The terminal

`tools/v42_pty.py` allocates a PTY, prints the slave path, and is pumped once per
20 ms media quantum from `media_tick`. `--v42-pty` enables it, and it is created
at startup rather than on answer so a session can already be attached when the
call lands.

The PTY carries no line speed, parity or flow control: those belong to the
asynchronous side of a real modem's UART, and this link starts at the synchronous
V.42 boundary where the data pump has already framed the bits. `stty` on the
slave appears to work and changes nothing. Flow control is real, though — the
LAPM window is the only buffer, so the pump only reads from the PTY while the
window can take frames, and the terminal blocks when it cannot. Link-to-terminal
writes are dropped when nothing has the slave open, rather than buffered:
replaying a previous session's output at whoever attaches next is worse than
losing it.

Verified with two endpoints back to back through their own bit pipes and a PTY on
one side: `hello from the terminal` and `second line` arrive in the peer's
`rx_data`, `welcome to the card` arrives at the terminal, 5 I frames out, no
retransmissions. Eight new unit tests cover segmentation, the window limit,
piggybacked N(R), REJ go-back-N, RNR, the probe-then-retransmit sequence, SABME
reset and modulo-128 wrap.

### What this does not fix

The terminal is exactly as reliable as the carrier under it, and the carrier is
the open problem: V.34 does not connect, V.90 needs `--native-bearer-activation`,
and a live LAPM link has never been established through the emulated modem at
all — the loopback test above supplies its own SABME. The `[v42] totals` line now
reports the transmit counters, so the first real attempt will say which side
stopped.

## Session 85: live Courier V.42 test — the data path never switches on

First hardware test of the Session 84 transmitter. Five calls from the Courier
(`5607`/`1A11`, `/dev/cu.usbserial-21210`) to extension 6001, endpoint on port
5062 with `--native-bearer-activation --tx-v42 --v42-pty`. Answer: V.42 is not
reachable yet, and none of these calls put it under test.

| call | Courier setup | result | maxTrn | rate word `+0x01` |
|---|---|---|---|---|
| call1 | `&M4&K0S48=7` | 60 s call, clean stall | `0x00b3` | never |
| call2 | `&M4&K0S48=7` | no INVITE routed | -- | -- |
| call3 | `&M4&K0S48=7` | collapsed after `0x00b2` | garbage | never |
| raw1 | `&M0` | no INVITE routed | -- | -- |
| raw2 | `&M0` | no INVITE routed | -- | -- |

All calls that reached the card reported `SABME rx=0` and
`HDLC good/bad/abort=0/0/0`. The LAPM decoder was never fed a single bit.

### Why, exactly

`_v90d_tx_bits()` reads DATASTATEspeedTx at read-database `+0x01`
(`DM(0x3f61)`) and returns `None` unless bit 5 is set. That word is `0x0000` for
every sample of every failing call. `None` means `_lapm_active` never becomes
true, so `frame_fast` falls back to `_prbs_bits(48)` for transmit and
`_service_rx_data()` returns at its first line. The whole V.42 path is gated on a
word the card never publishes.

The gate itself is correct. Surveying every `.adsp-dm.bin` in `artifacts/interop`:
the two Session 71 raw successes published `+0x01 = 0x202d`, which is bit 5 set
with index 13, so `21 + 13 = 34` bits per datagram, and 34 x 8000/6 =
45333 bit/s -- exactly the rate that run reported. The address and the decode are
right end to end. The card simply does not get there.

### Error control has never once succeeded

Cross-referencing the modem logs for the AT setup actually used:

| setup | calls | reached a rate |
|---|---|---|
| `&M5` / `&M4` (error control) | usr call2, call3, call5-v42; courier call1 | **0 of 4** |
| `&M0` (raw) | usr call1, call4, call6-v42, call7-v42 | 2 of 4 |

So `0x00b3` is not exclusively an error-control state -- raw `call6-v42` reached
it too -- but no call that asked for error control has ever published a rate. At
n=4 each that is suggestive, not conclusive: under raw's own 50% failure rate,
four consecutive error-control failures has probability about 0.06. Worth more
samples before treating "error control breaks it" as established.

### The failure has a consistent position

`call1` walked `0x0078 -> 0x007a -> 0x007b -> 0x0080 -> 0x00b0 -> 0x00b1 ->
0x00b2 -> 0x00b3` and held `0x00b3` for the remaining 45 seconds. DSR asserts at
`0x00b1` (`Rstatus_ch=0x8200`), DCD never does. At the stall `DM(0x3fb4)` has
reverted from a real sample to the generic pointer `0x3764`, and
`DM(0x3fb2/0x3fb3)` moved from the page-14 routines `19d2/19e1` to `17bb/1706`,
so the V90D generator has stopped; `DI_control` stops requesting transmit.

`call3` reached `0x00b2` at essentially the same point and then the shared boot
word went to `0x8001`/`0xbfb2` and thrashed -- the modem task collapsing rather
than stalling. Same moment in the call (14.62 s vs 14.72 s), two manifestations.
Neither produced a media fault, so the Session 83 containment was not needed, and
neither printed the PM `0x06cd` restore, because the card never left page 14 by a
proper overlay request.

Media pacing was clean throughout: `call1` had 0 substituted RX samples, 0
dropped, 4 ticks over 18 ms, worst 26.4 ms. The Session 81 budget work holds on
live hardware and lost wall time is not implicated in any of this.

### Caveats on this run

- The raw-mode regression check is **inconclusive**. Both `&M0` attempts failed
  to route an INVITE at all, so this tree has not been shown to still reach
  `0x00c6`/`0x00d0` on a known-good raw call. That check is still owed.
- Three of five calls produced no INVITE. Cause not established; all 32 historical
  dials in the artifacts used the same `ATDT6001`, and the endpoint had registered
  in each case. Asterisk-side routing was not inspected.
- The test harness printed one misleading `SABME seen` line on `call3`. It matched
  the substring in `SABME rx=0` from the end-of-call totals, not a real frame.

### What this means for the terminal

The Session 84 transmitter and PTY are still unexercised against hardware. The
loopback test in that session supplies its own SABME, so what is proven is the
framing, the window and the state machine -- not that a Courier's XID and SABME
survive the carrier. Nothing above is evidence against the V.42 work; it is
evidence that the blocker is two layers below it.

## Session 86: the garbage is a missing V.42 detection phase

A Courier call finally connected: `V.90/V.34+`, 38666/24000, symbol rate
8000/3429, DCD asserted, 84 seconds, 86055 chars received. The terminal filled
with garbage, and the Courier's `ati6` explains it in one line:

```text
Protocol               NONE
Data Compression       NONE
Octets sent 0   Octets Received 0   Blocks sent 0   Blocks Received 0
Chars sent 1364  Chars Received 86055
Disconnect Reason is Unable to Retrain
```

`Protocol NONE` with zero octets and zero blocks: the Courier connected with **no
error control at all**. Our side was sending HDLC flags into a raw character
pipe, and the Courier was sending raw characters into our HDLC decoder, which is
why `[v42] totals` read `HDLC good/bad/abort=0/22/270` -- 292 framing attempts,
none of them real. Both directions were garbage by construction.

### Why the Courier gave up on error control

V.42 (03/2002) 7.2.1.3, answerer actions:

> the control function of the answerer shall transmit 1-bits (mark) until
> termination of the detection phase, receipt of the ODP, or detection of the
> start of the protocol phase (the start of the protocol phase is indicated by
> receipt of continuous flags, or of an LAPM or alternative procedure protocol
> frame).

And 7.2.1.2, originator actions:

> If the ADP is not observed within the period of T400 [...] the originator shall
> decide that the answerer does not possess V.42 error-correcting capability. In
> this case, the originator may fall back to non-error-correcting mode.

`LapmEndpoint` started on continuous flags the moment the data state opened. To
the Courier that reads as "the protocol phase has already begun", so it stopped
looking, never received an ADP, T400 expired, and it fell back exactly as
specified. The bug was that we skipped the detection phase entirely, and the
symptom was 86 KB of garbage.

### Implemented

The answerer role of 7.2.1, patterns taken verbatim from the Recommendation
rather than derived, since the parity convention in the printed patterns does not
match a naive reading:

```text
ODP  0 1000 1000 1 11...11  0 1000 1001 1 11...11   DC1, alternating parity
ADP  0 1010 0010 1 11...11  0 1100 0010 1 11...11   (E) and (C) = V.42 supported
```

Each is one start-stop character over the synchronous link: start bit, seven data
bits low-order first, parity, stop bit. The endpoint now sends mark until it sees
four DC1s of alternating parity, answers with the "V.42 supported" ADP from
Table 3 ten times, then enters the protocol phase and starts flags. Receipt of an
LAPM frame also enters the protocol phase directly, so an originator with
detection disabled (USR `S48=0`) still works. T400 is counted in service calls
for the reasons in Session 84; on expiry it stays on mark and says so, because
there is no asynchronous mode on this side to fall back to.

`EICON_V42_DETECT=0` restores the flags-immediately behaviour. Eight new tests
cover mark-not-flags, the four-DC1 threshold, the ADP contents and repetition
count, flags following the ADP, same-parity DC1s not counting, the LAPM-frame
shortcut, the timeout, and the opt-out. 21 tests total.

### The larger question this exposes

`modem_nl_assign_payload()` sets `DLC_MODEMPROT_DISABLE_V42_V42BIS`, so the
harness explicitly disables the **card's own** V.42 and runs the plain
B2_TRANSPARENT branch. That is why `v42_lapm.py` exists at all: with the
firmware's error control switched off, the Python is the V.42 entity and owes the
detection phase.

There is a second route that has never been tried. The card ships a real V.42
implementation, and `DLC_MODEMPROT_DISABLE_V42_DETECT` (0x08) exists as a
separate bit, so the firmware clearly has its own detection phase. Not setting
`DISABLE_V42_V42BIS`, and supplying the B2 error-correcting negotiation block
instead of the transparent one, would use the shipped implementation rather than
a from-scratch LAPM -- which is this project's whole premise. It moves the data
path off the synchronous pump and onto the protocol page, so it is not a small
change, but it is very likely less work than making our LAPM interoperate, and it
would exercise firmware nobody has run yet.

### Not validated on hardware

The fix is unit-tested and unvalidated. Five further calls were attempted
(`&M4&K0S48=7` and `&M4&K0S48=0`, the latter forcing LAPM without a detection
phase) and none routed: no INVITE reached the endpoint, which had registered
each time. Four of the last seven dials behaved this way, where earlier calls in
the same session routed normally, so something in the telephony path changed
rather than in the endpoint. `Disconnect Reason is Unable to Retrain` on the one
successful call also leaves the Session 69/71 retrain blocker untouched: the log
shows the card reaching page 8 for a retrain at 87.44 s and the shared boot word
going to `0xf770` immediately after.

## Session 87: a Courier call completes, and the DIL predictor is falsified

### Routing, and a retraction

Session 85 and 86 blamed "something in the telephony path" for five calls that
never produced an INVITE. That was wrong: Asterisk routes extension 6001 to port
**5060** specifically, and those runs bound 5062 because the user's own endpoint
held 5060 at the time. Registering successfully on another port is not enough.
Every "no route" result in Sessions 85 and 86 is explained by that and says
nothing about the card or the line.

### A complete connection

On port 5060, `AT&M4&K0S48=0`:

```text
[dil]  6.200s: flag DM(0x3f8b)=0x0000 count DM(0x3f87)=0x000d measure DM(0x3f8e)=0x2388
      0x00b3 -> 0x00b6 -> 0x00c0 -> 0x00c2 -> 0x00c4 -> 0x00c8 -> 0x00ca -> 0x00cc -> 0x00d0
[v42] V.90/V.34 synchronous data state: TX 29 bits/datagram, RX 13 bits/datagram
Rstatus_ch=0x8783[change_h|CTS|DSR|DCD|change_l|sec_rx_present|sec_rx_data]
```

`maxTrn=0x00d0`, rate word `+0x01 = 0x2028`: bit 5 set, index 8, so 21 + 8 = 29
bits per datagram and 29 x 8000/6 = 38666 bit/s -- the same rate the user's
successful call reported. DCD, CTS, speed_tx and speed_rx all assert.

**This is the first time the V.42 data path has activated on hardware.**
`_lapm_active` became true and the LAPM decoder was fed for the whole call. So
the Session 85 blocker is not permanent; it is the lottery the user describes.

### The predictor was wrong

Nine archived captures gave a clean split: `DM(0x3f8b)` was `0x0001` for every
call that published a rate and `0x0000` for every call that stalled at `0x00b3`,
0 for 6 against 2 for 3. A `[dil]` line was added to print it at TrnProgress
`0x007a`, where the outcome is set, along with `DM(0x3f87)` and `DM(0x3f8e)`,
which looked like a count and a channel measurement on the same split.

The very next live call printed `flag DM(0x3f8b)=0x0000` and then reached
`0x00d0`. The correlation does not hold and the wording that claimed it has been
removed from the code. The three words are kept as instrumentation for the phase
where the call is actually decided -- none of them appears anywhere else in 86
sessions -- but they do not predict anything. Nine samples was not enough, and
the split being perfect over those nine made it look stronger than it was.

### Still no LAPM

`HDLC good/bad/abort=0/2/15` and `SABME rx=0` over 44 seconds of data state. The
decoder ran and found no valid frame in 17 framing attempts. Two possibilities
remain open and this run cannot separate them:

- the Courier again connected with `Protocol NONE`, so there is no LAPM to
  decode. `S48=0` should force LAPM, but the successful call in Session 86 also
  reported `Protocol NONE` under `&M4`, and no `ati6` was captured this time;
- the receive side is misframed. `_service_rx_data()` takes 13 bits per datagram
  MSB-first from RXD; if the order or the count is wrong, a perfectly good LAPM
  stream would produce exactly this -- a handful of accidental flags and no valid
  FCS.

The next run must capture `ati6` immediately after the call. `Protocol` and the
octet/block counters separate those two cases in one line, and without them this
is guesswork. The V.42 detection phase added in Session 86 is still unvalidated:
with `S48=0` the Courier skips detection, so this call did not exercise it.

## Session 88: the echo canceller is still off, and still cannot be turned on

The near/far echo bulk-delay adapter at PM `0x1900..0x19c8` is the card's echo
canceller, and this harness RTSes out its tail on every page-14 load. That has
been the default since Session 65. It is a real functional gap and a plausible
contributor to the DIL lottery: our path runs SIP/RTP to an ATA to two-wire to
the Courier, so there is a hybrid generating exactly the echo this adapter
exists to remove, and the card's receiver has to pull the analogue modem's
upstream V.34 out of it.

Re-tested on `usr-v92-21240/call1.rx.ulaw` to 16 s, native-bearer path:

| configuration | V90D outer state walk | result |
|---|---|---|
| adapter disabled (default) | `0050 .. 0068 006a 0070 .. 007a 007b 007c 0080 00a6 00b0 00b1 00b2` | clean |
| adapter enabled, `DM(0x32f7)=0` | `0050 .. 0068` then `0fc2`, `00c4`, `78f8` | state word corrupted |
| adapter enabled, `DM(0x32f7)=8` | identical to the above, word for word | state word corrupted |
| adapter enabled + `--prime-v90d-bulk-cursor` | `0050 .. 0068` and stops | stalls |

So enabling it is still worse than leaving it off: the machine cannot get past
`0x0068`, where with the adapter off it walks to `0x00b2`. Turning the echo
canceller on is not a switch that is being left unflipped.

### Two things this narrows

**The Session 65 symptom no longer reproduces.** That session pinned the
destruction precisely: PM `0x1930` zeroing `DM(0x3fb3)`, the `Core8kRoutine`
callback, after which the equalizer and Ja detector necessarily freeze. Watching
`DM(0x3fb3)` across all four runs above, it is never zeroed. The failure has
moved -- plausibly because of Session 79's PC-stack fix or Session 83's PM
`0x06cd` restore, both of which changed what the resident kernel does per frame.
Instead the outer state word `DM(0x1ff7)` itself takes impossible values
(`0x0fc2`, `0x78f8`), so the corruption is now landing on the state image rather
than on the write database. Anyone resuming Session 65's trace should re-derive
the destructive store rather than trusting the `0x3fb3` finding.

**`DM(0x32f7)` can be dropped as a candidate.** Session 65 left "trace how
`DM(0x3fbc/0x3fbd)`, the RX workspace, `DM32f7` and ADSP carry/NORM semantics
produce `DM0..DM7`" as the next target, on the grounds that the selector stays
zero with no DSP writer. Setting it to `8` at page-14 entry -- before state
`0x60`, so PM `0x1982` reads the new value, and it is still `8` at the end of the
run -- produces byte-identical behaviour to leaving it zero. The page-entry
workspace does hold a second descriptor at `DM8..DM11`
(`2ac7 2ad2 2ae0 2b1b` against `2aca 2ad2 2ae5 2b1b` at `DM0`), so selecting it
is meaningful, and it changes nothing. Whichever descriptor is selected, its
cursor word is zero, which is Session 65's real point: PM `0x1982` deliberately
writes zero there, so the missing thing is an input to that calculation, not the
selector.

Cursor priming plus a live adapter now stalls at `0x0068` rather than corrupting,
which is a third distinct failure mode and consistent with Session 65's warning
that the prime is "temporarily useful but eventually destructive".

### What is not established

Whether the missing echo canceller is what makes DIL a lottery. It cannot be
tested by comparison while enabling it fails earlier than disabling it, so the
causal claim stays open. It is a good hypothesis with a mechanism, not a result.
The blocker remains where Session 67 left it: the owner of the bulk workspace
handoff is above the ADSP page, in call ingress/activation.

## Session 89: the ingress handoff is healthy, and V8_SETUP=0 is the firmware's

Session 67 left "the missing owner is above the ADSP page, in call ingress/
activation" as the blocker, and Sessions 82, 86 and 88 all pointed back at it.
Instrumenting the path says it is not missing anything.

Lower-PRI event `0x03` dispatches service driver `0x80098310`, which calls
`0x80097f60` and polls `0x80095318`. All three execute exactly once on the
`--native-bearer-activation` path. Two native transfers result:

- **KERNEL**, DSP `0x6e58` for `0x10` words. IDMA `0x6e58` is DM `0x2e58`, and it
  lands: `DM(0x2e58)` becomes `0x0277`, matching Session 67. (Session 67 records
  this address as `0x6e68`; the transfer object here says `0x6e58`.)
- **DATABASE**, 256 words to DSP `0x7ee0`, which is DM `0x3ee0` -- the entire
  write database. **It happens.** Exactly one transfer, 256 words, and
  `DM(0x2f27..0x2f29)` come up `2f21/2f00/2f0e`, so the dispatch roots are
  populated too.

An earlier attempt at this measurement reported zero database writes. That was an
instrumentation error: the bulk-write interception in `_hook_intercept` writes
`dm[]` directly and never calls `adsp2181_host_write`, which is what was being
counted. `shim.host_writes` does record them, as 256 successive writes to the
`0x7ee0` IDMA port.

### V8_SETUP is zero because the firmware writes zero

The values the card's own connected-task driver puts in that transfer:

| write DB | firmware writes | handbook (Session 22 audit) |
|---|---|---|
| `V8_SETUP +0x04` | **`0000`** | `6000` (V90_DPCM + digital network) |
| `INFO0_SETUP +0x07` | `f3fd` | `f0fd` |
| `NORM_H +0x28` | `0001` | `0001` |
| `NORM_L +0x29` | `b13f` | `8100` |
| `SPEED_SEL_L +0x2b` | `fffe` | `ff00` |
| `INFO0D_SETUP +0x7b` | `0337` | `03b7` |

So `V8_SETUP=0x0000` is authored by the shipped firmware, not dropped by this
harness and not a Session 75 regression. Session 82 asked whether something
should have set it and answered "probably the bearer, via the digital-network
bit"; the answer is that the card's own driver writes zero there on this build.
That closes the question. `EICON_WDB_OVERRIDE=0x04:0x6000` therefore forces a
value the firmware deliberately does not publish, which is worth knowing before
anyone reads an A/B result from it -- it is a deviation from the card, not a
restoration of it.

`NORM_H` also settles at the handbook's `0x0001`, so the `0x00ff` that Session 82
listed against it was a different word (`+0x10`) and the modulation masks are not
disturbed.

### The database is co-authored, which undercuts "preserve the exact transaction"

Comparing the transferred values against DM at the moment
`complete_native_answer()` snapshots it, with only one transfer in between:

```text
+0x06   written 0105   DM holds 2105
+0x07   written f3fd   DM holds f1fd
```

The DSP modifies the database after the host publishes it. Session 75's model --
snapshot the driver's transaction and republish it verbatim as the answer cycle --
treats it as host-authored state. It is not; the DSP is a co-author, and
republishing a snapshot taken at one instant overwrites whatever the DSP had
contributed by the next. That does not make Session 75 wrong to stop substituting
handbook values, but "the exact result of the notification" is not a thing a
single snapshot can capture.

### The bulk workspace is not an ingress problem

`Nearbulklength`, `BulkLength`, `BulkInputX` and `BulkInputY` are at
`DM(0x3fbc..0x3fbf)` -- the **read** database, which the DSP writes. They are zero
after activation because no page has computed them yet; Session 58 observed
`1d77/0ae0` at V90D state `0x60`, which is when PM `0x1982` derives them. Ingress
is not expected to publish them and its not doing so is not the fault.

Combined with Session 88 ruling out `DM(0x32f7)`, the echo-canceller blocker is
now firmly inside the DSP: the PM `0x1982` bulk-workspace calculation, with a
complete write database in front of it and a populated dispatch list. Session
67's redirection upward was reasonable at the time but the ingress path has since
been fixed enough that it is no longer where the fault lives.

## Session 90: PM 0x1982 is correct; PM 0x1930's fill bounds are the fault

Traced with the core's own disassembler (`adsp2181_dis.py`, which shares the
emulator's dispatch tables) against a live page-14 PM dump, plus exec and DM
watches at V90D state `0x60`.

### The routine

```text
1982: I1 = DM($32F7)                    selector -> workspace base
1985: modify address register            I1 += M3(7)
1987: DM(I1,M3) = AR                     DM7 = 0001,  I1 += -6
1988: AR = AF - 1, SR0 = DM(I1,M2)
1990: AF = SR0 - AY0
1992: IF AC JUMP $1994
1994: AF = AR - AY0
1995: IF NOT AC AR = AY0
1996: AR = AR + 0
1997: IF GE JUMP $199A                   <-- decides AX0
1998: AR = SR0 + 0
1999: AX0 = $0004
199a: SR = LSHIFT AR (LO), AY1 = SE
199b: DM(I1,M1) = SR0                    DM0 = 03cd
199d: DM(I1,M1) = $0000                  DM2 = 0000
199e: DM(I1,M1) = SR0                    DM3 = 0001
19a0: AF = NOT AY1, AY1 = DM(I1,M1)
19a2: AR = AX0 OR AY0, AX0 = AR
19a3: DM(I1,M2) = AR, AR = AX0 AND AF    DM6 = 0000
19a5: DM(I1,M0) = AR                     DM4 = 0000
19a6: RTS
```

DM write watches confirm the offsets rather than inferring them from the I1
walk: `ppc=1987 -> DM7`, `199b -> DM0`, `199d -> DM2`, `199e -> DM3`,
`19a3 -> DM6`, `19a5 -> DM4`. Session 65's claim that PM `0x1982` writes `DM4=0`
is correct, and the instruction is `0x19a5`.

### DM4 = 0 is the intended result, not a missing publication

`DM4` is `(AX0 OR AY0) AND NOT AY1`. `AX0` is `0` from PM `0x1991` unless PM
`0x1999` sets it to `4`, and PM `0x1999` is only reached by falling through the
`IF GE JUMP $199A` at PM `0x1997`. In the traced pass `AR = 0x03cd` at PM
`0x1996` -- positive -- so the branch is taken, PM `0x1998/0x1999` are skipped,
`AX0` stays `0`, and `DM4` is `0`. `AY0` and `AY1` are `0` and `0xffff` at that
point, so the expression is `0`.

So the calculation is doing what it was written to do. Sessions 58 and 59 framed
this as "the unpublished initial far-bulk cursor" and Session 65 narrowed it to
"either an input handoff is missing or one of the calculation's ADSP semantics is
wrong". It is neither: `0` is the firmware's deliberate output for
`Nearbulklength = 0x03cd`, and PM `0x1935` then advances the cursor from `0`
normally -- observed writing `DM4` 640 times with `0, 1, 2, ...`. PM `0x1982`
should be struck off the open list. So should `--prime-v90d-bulk-cursor`, which
overwrites a correct value.

### The fault is where the fill lands

With the adapter live, the outer state word `DM(0x1ff7)` takes `0x0fc2` and a DM
watch names the writer: PM `0x2fea` -- the sequencer's own state store, the same
instruction that writes every legitimate state. Nothing overwrote `0x1ff7`; the
sequencer *read* a bad next state.

An exec watch on PM `0x1930`, the adapter's store, gives its destination. `I0`
sweeps from `0x0049` to `0x1b41`, 1556 distinct addresses. Meanwhile the outer
record pointer `DM(0x120f)` walks `0x18ba -> 0x18cc -> 0x18d8 -> 0x18e7 ->
0x18f6 -> 0x1902` and then jumps to `0x1b51`. The record table is inside the
swept range: the fill zeroes the records, the sequencer reads a zeroed one,
publishes `0x0fc2`, and the pointer follows the wreckage.

This is Session 65's collision with a different victim. There it reached
`DM(0x3fad)` and `DM(0x3fb3)` in the memory-mapped database and killed
`Core8kRoutine`; that no longer happens (Session 88), and the fill now reaches
the state-machine records instead. Same cause, new casualty, which is why
enabling the echo canceller stops the machine at `0x0068` rather than freezing
the equalizer.

### Next

The question is no longer what `DM4` should start at. It is why PM `0x1930`'s
destination is unbounded: a delay line described by `Nearbulklength = 0x03cd` has
no business writing to `0x1b41`. Trace how `I0` and `L0` are loaded on entry to
PM `0x1900`, and whether the modulo addressing that should confine the fill to
the delay buffer is being set up at all -- an `L` register left at zero disables
ADSP circular addressing, which would turn a bounded ring write into exactly this
linear sweep. `L0` is in the `[EXEC]` line, so this is one more trace.

## Session 91: the fill is unbounded because its modulo bound is zero

Continuing Session 90. The hypothesis there -- that `L0` is zero and ADSP circular
addressing is therefore disabled -- is **wrong**. `L0` is indeed `0x0000` at every
execution of PM `0x1930`, but the routine never loads `L0` or `B0` at all. It
reloads `I0` from a computed value on each pass (`I0 = AX1` at PM `0x190e` and
`0x191f`, `I0 = AR` at `0x1928`, `I0 = AX0` at `0x192e`), so linear addressing is
intended and the bound is supposed to come from arithmetic, not from the DAG.

### The arithmetic bound is zero

The routine does its own modulo. Twice it computes a candidate address, compares
it against an interval bound in `AY0`, and adds the length in `AY1` back if the
subtraction underflowed:

```text
1921: AR = AR - AY1, AY0 = DM(I1,M2)
1922: DM(I5,M5) = SR0, AF = AR - AY0
1923: IF NOT AC AR = AR + AY1        <-- wrap correction
1925: AR = AR - AF, AX0 = AR         <-- AX0 captured, becomes the destination
1926: DM(I5,M5) = SR0, AF = AR - AY0
1927: IF NOT AC AR = AR + AY1        <-- wrap correction
192e: I0 = AX0
1930: DM(I0,M1) = SR0                <-- the store
```

Traced at V90D state `0x60`, `AY0` is `0x0000` at both comparisons. `AF = AR - 0`
therefore never underflows for a positive `AR`, `AC` is set every time, and both
`IF NOT AC` corrections are skipped: **0 fires against 597 skips at PM `0x1923`
and the same at PM `0x1927`**. With no wrap, the destination walks linearly, which
is the `0x0049 -> 0x1b41` sweep from Session 90 that flattens the state-machine
record table.

`AX0` is also captured at PM `0x1925` before the correction at `0x1927`, so even a
working wrap would only fix `AR`, not the store destination. Whether that is
deliberate depends on the intended value of `AX0`, which brings it back to the
same place.

### Where the zero comes from

`AY0` is loaded by `DM(I1,M2)` from the eight-word workspace, and the workspace
after PM `0x1982` is:

```text
DM0..DM7 = 03cd 2ad2 0000 0001 0000 0000 0000 0001
```

`DM5` and `DM6` are zero. `DM6` is written zero by PM `0x19a3`
(`AR = AX0 AND AF`, with `AX0 = 0`), and `DM4` is written zero by PM `0x19a5`
for the same reason. Both trace to a single branch: PM `0x1997`
`IF GE JUMP $199A` is taken because `AR = 0x03cd` is positive, which skips PM
`0x1999` `AX0 = $0004`. If that branch fell through, `AX0` would be `4` and both
words would be non-zero.

So the whole failure -- Session 58's "unpublished cursor", Session 65's collision,
Session 88's three failure modes, Session 90's unbounded sweep -- reduces to one
predicate: whether `AR` at PM `0x1996` should be negative. `AR` there derives from
`Nearbulklength` (`DM(0x3fbc)`) through the chain at PM `0x1988..0x1995`, and
`Nearbulklength` is a read-database word the DSP computes, observed as `0x0413` at
page entry and reaching PM `0x1996` as `0x03cd`.

This is Session 66's "missing retained workspace words" made concrete: the missing
word is the modulo bound, and it is zero because the near-bulk path was selected
where the far-bulk path was expected.

### Next

Two candidates, and they are distinguishable:

- **The inputs are wrong.** `Nearbulklength`/`BulkLength` are DSP-computed in the
  read database, and if the near/far split is mis-derived the branch legitimately
  takes the near path. Trace who writes `DM(0x3fbc)` and `DM(0x3fbd)` and against
  what.
- **A flag semantic is wrong.** Sessions 46 and 52 both found real emulator
  defects of exactly this shape -- `ABS` flags and MAC rounding. The chain at PM
  `0x1988..0x1995` uses `NORM`, `SE`, and conditional `AR = AY0` on `AC`, and an
  `AC` or `AV` discrepancy across `SR = NORM SR0 (LO)` would flip the branch. The
  0-of-597 result is suspicious on its own: a modulo correction in shipped
  firmware that never once fires is more likely mis-evaluated than never needed.

The second is cheaper to test: audit `NORM` and subtract flag behaviour at PM
`0x1989..0x1996` against the ADSP-2181 manual, in the style of the Session 52
opcode audit.

## Session 92: the manual clears the emulator; the near/far fork is not the fix

Session 91 offered two candidates: wrong inputs, or a wrong flag semantic. The
flag semantics are correct, and the fork that selects near from far bulk is not
where the fault lives either.

### The emulator's flags are right

`shift_op` in `2100ops.inc` touches `ASTAT` only to read `CFLAG` for the rotate
forms and to set or clear `SS`. It never writes `AZ`, `AN`, `AV` or `AC`, which
matches the ADSP-2100 family shifter: shifter operations affect `SS` and nothing
else. So `SR = NORM SR0 (LO)` at PM `0x1989` correctly preserves the flags that
PM `0x1988`'s `AR = AF - 1` set, and PM `0x198d`'s `IF NE` correctly tests that
`AZ` four instructions later.

`CALC_C_SUB(r)` is `astat |= (~r >> 13) & 0x08`, i.e. `AC` is the complement of
bit 16 of the raw difference: set when there was no borrow. Checked against the
traced values -- `0x0049 - 0x0000` gives `AC=1`, `0x03cd - 0x0001` gives `AC=1` --
it is right. With `AY0 = 0` an always-set `AC` is the correct answer, not a bug.

Session 91's suspicion that a correction firing 0 times in 597 must be
mis-evaluated was wrong. Sessions 46 and 52 found real defects of this shape;
this is not one. **The emulator is exonerated here.**

### Session 90's L0 hypothesis was wrong twice over

PM `0x19ac..0x19b1` explicitly sets `L0`, `L1`, `L4`, `L5`, `L6` and `L7` to
`$0000` before calling the adapter. The firmware deliberately disables circular
addressing; the zero `L0` observed in Session 90 is intended, not missing.

### The fork, and why changing it does not help

The caller is PM `0x19a7`, and the near/far selection is explicit:

```text
19b8: AY0 = DM($3FBC)          Nearbulklength
19b9: AY1 = DM($3FBD)          BulkLength
19c2: AR = $0002               default
19c3: ASTAT = DM($32F0)        ASTAT restored from a DM word
19c4: IF AC AR = 0 + 1         AC selects AR = 1
19c5: I5 = $3FBC
19c6: CALL $1982
```

`DM(0x32f0)` reads `0x0009` at that instruction -- `AZ|AC` -- so `AC` is set and
`AR` becomes `1`. Worth noting that `attach_connected_bearer()` hard-writes
`self.dm[0x32F0] = 0x0004` with no explanation, and by the time PM `0x19c3` runs
the word is `0x0009`, so that magic constant is both unexplained and overwritten.

Forcing the other branch by NOPing PM `0x19c4`, so `AR` stays `2`:

| | AR = 1 (as-is) | AR = 2 (forced) |
|---|---|---|
| workspace at `0x62` | `03cd 2ad2 0271 0001 0271 0000 0000 0001` | `083a 2ad2 0334 079a 0334 0000 0000 0002` |
| outer states | `0050 0052 0053 0060 0062 0064 0066 0068` then garbage | `0050 0052 0053 0060 0000 0062 0001 0050` -- restarts |

The far path produces a materially different and more plausible workspace: `DM3`
becomes `0x079a` where it was `1`, and `DM0`/`DM4` roughly double. But `DM5` and
`DM6` are **still zero in both**, so the modulo bound `AY0` is still zero and the
fill is still unbounded. The state machine then restarts instead of walking to
`0x0068`. Not a fix.

### Why the far-bulk configuration is unreachable

`AX0 = $0004` at PM `0x1999` is only reached by falling through
`IF GE JUMP $199A` at PM `0x1997`, which needs `AR` negative at PM `0x1996`. `AR`
there is one of: the entry value (`1` or `2`), the constant `1`, or `AY0`. `AY0`
is `DM(I5,M5)` with `I5 = 0x3FBC`, i.e. `Nearbulklength`, observed at `0x0413`.
All positive. So with a positive `Nearbulklength` the far-bulk branch cannot be
taken by any value of the `0x19c4` fork, and `DM6 = AX0 AND NOT AY1` is zero
either way.

That leaves exactly one input: **`Nearbulklength` at `DM(0x3fbc)` would have to be
negative** for this routine to configure a non-zero modulo bound. It is a
DSP-computed read-database word, zero at page entry and `0x0413` by state `0x60`.

### Next

Find who writes `DM(0x3fbc)` and `DM(0x3fbd)` and what they are derived from --
a DM write watch on both, from page-14 load through state `0x60`, gives the writer
PC directly, the same technique that settled `DM4` in Session 90. If those words
are meant to be a signed delay relative to a reference the harness never supplies,
that is the missing input, and it is the last link in the chain from Session 58 to
here.

## Session 93: delaycorrection derives the bulk lengths, and near-bulk is probably right

Two hypotheses were put: that Linux or the firmware supplies the bulk lengths, or
that they relate to the T1/E1 code. The second is correct, at one remove.

### Not written by any host

A DM write watch across page-14 load through state `0x60` gives the writers of
`Nearbulklength` and `BulkLength` directly, and there are **no host writes to
either word at any point in boot**:

```text
625 PM 1a13 -> DM(0x3fbc) = 03cd      624 PM 19e2 -> DM(0x3fbc) = 03ed
625 PM 1a18 -> DM(0x3fbd) = 041d      624 PM 19e4 -> DM(0x3fbd) = 043d
  1 PM 3235 -> DM(0x3fbc) = 0031        1 PM 3ab7 -> DM(0x3fbd) = 0001
```

Two alternating DSP writers each, on chip, once per frame. So Linux does not
supply them and neither does the MIPS.

### But they are derived from a host-supplied delay calibration

`delaycorrection` at write-database `+0x24` (`DM(0x3f04)`) is `0x000c`, supplied by
the card's own 256-word DATABASE transfer (Session 89) and identified in the
Session 22 audit as "the Eicon build's supplementary-buffer calibration". Changing
it changes the lengths exactly:

| `delaycorrection` | `Nearbulklength` | `BulkLength` |
|---|---|---|
| `0x0000` | `0x03c1` | `0x0411` |
| `0x000c` (as shipped) | `0x03cd` | `0x041d` |
| `0x0040` | `0x0401` | `0x0451` |
| `0x8000` | `0x0000` | `0x0000`, workspace corrupted |

So `Nearbulklength = 0x03c1 + delaycorrection` and
`BulkLength = Nearbulklength + 0x50`, to the word. This is the host input into the
bulk workspace that Sessions 58 to 67 were looking for, and it is a span-delay
calibration -- exactly the T1/E1-shaped parameter the hypothesis predicted.

### It is not the fix, and it reframes the last two sessions

Across `0x0000`, `0x000c` and `0x0040` the workspace is identical apart from `DM0`,
`DM5` and `DM6` stay zero, and the run stalls the same way. The failure is
insensitive to it.

More importantly, the far-bulk path needs `Nearbulklength` negative (Session 92),
which needs `delaycorrection >= 0x7c3f`. That is not a delay calibration, it is
nonsense, and the one negative value tried (`0x8000`) zeroes both lengths and
scrambles the workspace into `0054 ff60 0070 ff62 006c ff64 004d ff66`.

**So near-bulk is almost certainly the correct configuration**, and the premise
carried through Sessions 91 and 92 -- that the far-bulk branch is the one that
should have been taken -- is probably wrong. If `AX0 = 0` and therefore `DM6 = 0`
are correct for a near-bulk configuration, then a zero `AY0` is not a defect and
the unbounded sweep has to be constrained by something else.

### The unverified assumption

`AY0` was attributed to `DM5`/`DM6` by inference from the workspace contents, not
by tracing `I1` at the two read sites (PM `0x1917` and PM `0x1921`). That
attribution is now load-bearing for the whole "zero modulo bound" reading and it
has never been checked. Trace `I1` at those two instructions -- the `[EXEC]` line
carries `i1`, so it is the same one-run technique that settled the PM `0x1982`
stores in Session 90. If `AY0` comes from a word that is legitimately non-zero in
a working configuration, the fault moves again; if it really is `DM6`, then
near-bulk genuinely configures no bound and the question becomes what else was
meant to limit PM `0x1930`.

## Session 94: port the driver's AT and IDI layers, and dismantle the V.34 CAI hypothesis

`docs/divas4linux-master/` is the Sangoma/Eicon Linux driver source. Two things in
it are directly useful here: `putcai()` (`tty_module/isdn.c:1209`), which is the
complete CAI builder, and `atPlusMS()` (`tty_module/atp.c:1879`), which is where a
modulation name becomes a disabled mask, an enabled mask and a pair of speed
windows. Both are now ported into `tools/eicon_idi.py`, with the AT command set
`/dev/ttyds*` presents on top in `tools/eicon_at.py`.

The motivation was that this project reached the firmware's modulation fields by
hand. `add_b1()` in `kernel/message.c` — the CAPI path the shim was transcribed
from — cannot express a modulation at all: `cai[10..12]` are reachable only through
the private V.18/VOWN extension, so the shim wrote those bytes itself and left the
rest zero. The tty driver reaches them directly.

The defaults did not change. `modem_sig_assign_payload()` and
`modem_nl_assign_payload()` emit byte-for-byte what they emitted before, pinned by
a test, and the offline replay reproduces.

### One correction to the handoff on the way through

The NL ASSIGN was documented as using the plain `B2_TRANSPARENT` branch. It never
did: `isdn.c:1533` overwrites the protocol map's B2 unconditionally on the modem
branch, so `B2_V42_in` has always gone out. **The DLC, not the LLC, is what
disables error control**, which also means enabling the card's own V.42 is a matter
of dropping the DLC rather than changing the LLC. `EICON_CARD_V42=1` now sends that
payload; it is still untried against hardware.

### The V.34 hypothesis, and its disproof

`atPlusMS()` ORs `unused_modulations` — `~(every disable bit the table names)` —
into any non-empty mask, which covers V.FC, K56flex and X2. That made the old
`EICON_FORCE_V34` (`disabled = 0x0080`) look badly under-specified against the
driver's `0xfc80`, and it looked like the first thing to try on the V.34 blocker.

It is not. An A/B across four configurations on `run34`:

| configuration | CAI disabled | host writes |
|---|---|---|
| default | `0x0000` | 51969 |
| `EICON_FORCE_V34=1` | `0x0080` | 51965 |
| `EICON_MODULATION=v34,1,,33600,,33600` | `0xfc80` | 51965, **identical to the above** |
| `EICON_MODULATION=v34,0,,33600,,33600` | `0xffbf` | 51965, differing |

`v34,1` is byte-identical to the old one-bit force across all 51965 writes. The
`0xfc00` bits never reach the card, so that mask is not worth a live call.

What the comparison does establish is where the CAI's disabled byte lands. Not in
the write database — all 160 words are identical in every configuration, `NORM_L`
holding `0xa13f`, `SPEED_SEL_L` `0xfffe`, `INFO0_SETUP` `0xf1fd`, consistent with
Session 89's finding that the card authors those itself. It lands in the DSP
assignment stream at host data port `0x6802`:

```text
CAI disabled 0x0000          3f00 1fb1 d200
CAI disabled 0x0080/0xfc80   3f00 1f31 d200
CAI disabled 0xffbf          0000 1f01 8000
```

Bit 7 of `0x1fb1` is V.90. Strict mode clears the fallbacks as well and changes
both companion words; the descriptor also shortens, the length word at `0x6800`
going 97 → 89 with four words dropping out of the stream.

The replay itself is uninformative about negotiation and was never going to be:
it is open loop against a V.90 recording, so the page-14 trace and the 9610 TX
datagrams come out identical in all four configurations. Only `v34,0` has any
prospect and only a live call can test it.

Also worth recording: the 56000 Rx ceiling this project sends is not a legal
driver selection. The `v90` row's `rx_map` is the V.34 speed map — the digital
side receives at V.34 rates — so `AT+IE=v90,1,,56000` is an error in the driver
while `legacy_modem_options()` asks for 56000 in both directions. Whether the
firmware minds is untested, and it is the kind of impossible advertisement worth
ruling in or out before blaming the peer.

## Session 95: two emulated cards call each other, and the calling side's gate is found

Every closed-loop test in this project has needed the Courier on a real line.
`tools/eicon_loopback.py` runs two `eicon_adsp_sip.py` instances on loopback,
points one at the other and captures both, so a failed handshake is readable
from both ends at once.

### Signalling direction is not the modem role

The obvious way to build this — teach the card to place an outgoing call — is a
dead end for now, and it was tried. `CALL_REQ` is accepted (`RC 0xff`) and the
firmware does allocate a call object at sig+0x1c, but there is no network to
answer the SETUP, and injecting the connected event 0x03 into the lower-PRI
parser leaves `call_state` at `0x00` and the firmware hangs the call up
(`IND 0x03`). That path is in the tree as `--simulate-outgoing-call` with the
`CALL_REQ` payload ported from `isdnDial()`; it is recorded, not working.

None of it is needed. Which side of the *modem* handshake an instance takes is
GEN_SETUP1 bit 3, not who sent the SETUP, so both instances are driven through
the existing incoming-call path and only that word differs
(`--modem-role`, `EICON_MODEM_ROLE`, GEN_SETUP1 `0x0484`/`0x048c`).

With `--native-mips` both ends boot, assign a modem DSP, exchange RTP and take
opposite roles. The answerer reaches TrnProgress `0x0026`. **The caller parks at
TrnProgress `0x0002` on page 12 and transmits nothing at all.**

### The calling side is inert, and it is not a tone problem

Reproduced without SIP, and then in a deterministic in-process cross-connect
(card A's transmit sample straight into card B's receive slot, one 8 kHz frame
at a time). Side B emits a genuine ANSam; side A's mean |TX| is exactly 0.0
across 12000 frames.

Swept and ruled out — do not re-derive: **ADET** (GEN_SETUP1 bit 0), **Dasen**
(bit 1) and **TonedetEnable** (GEN_SETUP2 bit 6) change nothing in any
combination, against silence and against a real answering pump. Bit 3 is the
only bit that matters.

### The gate, traced

```text
38ac: AR = DM($3EE1)        GEN_SETUP1
38ad: AR = AR AND $0008     bit 3 = CH, call(1)/answer(0)
38ae: AR = $0800
38af: IF EQ AR = 0
38b0: SR1 = AR
38b1: CALL $385B            OR SR1 into DM($046A)
```

GEN_SETUP1 bit 3 is copied into **bit 11 of DM(0x046A)**, which routes the
dial page:

```text
3576: AR = $0002
3577: DM($3FC2) = AR        TrnProgress = 2
3578: AX0 = DM($046A)
3579: AR = AX0 AND $0800
357a: IF EQ JUMP $3675      answering: training start, publishes TrnProgress = 4
357b: AX0 = $35D7           calling: park on this continuation
357c: DM($03EF) = AX0
```

and the continuation never completes:

```text
35d7: AY0 = DM($046C) ; AR = AY0 ; IF LT JUMP $35DD    proceed if DM(046C) < 0
35da: AR = DM($0554) ; AR = AR - $0010 ; IF LT RTS     or if DM(0554) >= 0x10
```

Measured and stable over 400+ frames: `DM(046A)=0x3948` (bit 11 set),
`DM(046C)=0x0064`, `DM(0554)=0x0000`, `DM(03EF)=0x35d7`.

### Which of the two conditions is live

| poke at frame 30 | page | TrnProgress | mean \|TX\| |
|---|---|---|---|
| none | 12 | `0x0002` | 0.0 |
| `DM(046C) = -1` | 12 | `0x0002` | 0.0 |
| `DM(0554) = 0x20` | 12 | `0x0051` | 1812.5 |

`DM(0x0554)` is the gate. It is held at zero by PM `0x3a36`, the tail of a
twelve-word scan of a `-1`-terminated table at `DM(0x056E)` — written 22 times
in 300 frames on the calling side and never on the answering side.

### Interpretation, and what it is not

`DM(0x0554)` reads like the dialler's progress count, with the calling side
refusing to train until the dial page reports the line established. Guide v5.3
§5.4.1 says the calling-mode script runs "when the PSTN connection already has
been established (by means of manual dialling, a data-pump dial script or any
other way)", and our "any other way" never tells the dial page anything. On an
analogue Diva the DIAL page's own dialler fills this in; on a digital span
nothing does.

Treat that as a hypothesis. What is established is the control flow above and
which word gates it.

**The poke is not a fix**: it starts transmission and moves TrnProgress to
`0x0051`, but page 12 stays resident and the V.8 overlay is never requested in
3000 frames. Note also that the standalone `Card` harness skips the
host-command dispatch path (`FRAME_ENTRY_NO_HOST`), which is where a "line
connected" command would arrive — but the native MIPS loopback stalls
identically at `0x0002`, so the real firmware is not sending one either.

Next: find the writer of the table at `DM(0x056E)`. That names the legitimate
way to satisfy this rather than poking a word.

### A caveat about loopback captures

The emulated clock free-runs: the caller reached 130 s of media in ~35 s of
wall time. Both endpoints drain a backlogged receive queue without sleeping,
and pointed at each other they mutually accelerate; a live peer paces it and
this never fires. The DSP is sample-clocked so state observations hold, but
**wall-clock timings in loopback captures are meaningless**.

## Session 96: the calling side waits on a tone detector the card never arms, and why

Session 95 left `DM(0x0554)` as the gate on the calling side and guessed it was a
dialler digit count. It is not. **That hypothesis is superseded.**

### The gate is a tone detector

`DM(0x0554)` is produced by the scan at PM `0x3a2b` over twelve channels at
`DM(0x056E)`, and PM `0x3a22` is what writes them:

```text
3a09: MR = MX0*MY0 + MX1*MY1     correlator
3a0b: ...                         |MR|
3a11: I0 = $056E ; SR1 = DM(I0)  the channel's history word
3a14: SR = LSHIFT SR1 (HI) BY 1  shift it left one place
3a15: AY1 = DM($057C)            threshold, low
3a17: AY1 = DM($057B)            threshold, high
3a19: IF LE JUMP $3A1C           below -> compute the quadrature product
3a21: SR = LSHIFT AR (HI, OR) BY 0   OR the decision bit in
3a22: DM(I0,M0) = SR1            store the channel back
```

Each word is a 16-bit shift register of per-frame decisions against the
threshold in `DM(0x057B:0x057C)` (measured `ea20:fcb2`), and the scan looks for
a channel that has filled with ones. So the calling side is waiting for **tone
detection**, sixteen consecutive frames of it.

### It can never fire

The correlator's state bank at `DM(0x2fc0..0x2fd7)` is all zeros and **nothing
writes it** — zero writers over 300 frames, in both roles. With the inputs zero
the product is zero, the decision bit is always zero, the registers never fill.
Confirmed against a genuine ANSam through the in-process cross-connect: 6000
frames, all twelve channels still `0000`.

The guide names the configuration block — write database **+0x30..+0x4F**,
"information for supervisory tone detection", with the layout in a separate
*DIALLER* document that is not in `docs/`. And the block is empty everywhere:

| source | WDB +0x30..+0x4F |
|---|---|
| standalone `Card`, answer | 32 words, all zero |
| standalone `Card`, calling | 32 words, all zero |
| native MIPS, the firmware's own answer WDB | 32 words, all zero |

**The card's own firmware never arms it either.** This is not a harness
omission. On a digital span there is no analogue line to listen to — no dial
tone, no ringback, no answer tone — so a PRI product has no use for a
supervisory tone detector and does not program one. `GEN_SETUP1 = 0x048c` is
therefore not a supported configuration on this firmware: the dial page's
calling branch waits on a detector this product never arms.

Session 74 saw calling mode "prevent progress" and attributed it to the recorded
peer. The real reason is not about the peer at all.

### On a PRI, dialling is the SETUP

Which raises the obvious question: how does a PRI card originate at all? Through
Q.931, not through the line. The host posts CALL_REQ and the card's protocol
image sends a SETUP on the D channel.

That path was exercised. CALL_REQ is accepted (`RC 0xff`), the firmware
allocates a call object, and it **parses the called party number out of our
request and stores it** — the plan octet and IA5 digits appear at
`0x80100877`, isolated in zeroed memory, without the IE header we sent, and it
is the *only* occurrence in the whole image and RAM. `tools/eicon_mips_shim.py
--scan-ram` finds it in one run; Q.931 encodes the number in IA5, so a message
built for transmission necessarily contains the digits verbatim.

**No SETUP is ever assembled.** The outgoing path stops between parsing the
request and building the message.

Nor can it be pushed along from the receive side. Every lower-PRI signalling
event `0x01..0x20` was delivered after CALL_REQ (`--connect-event`): `call_state`
stayed `0x00` in all 32 cases and the bearer was DISCONNECTED every time.
Events `0x03`, `0x0b`, `0x0c` and `0x0e` provoke a HANGUP indication; the rest
are ignored. The incoming-message parser is not the door for an outgoing call.

### Where the D channel actually lives

From the driver: a PRI card is given exactly two images (`divactrl/load/divaload.c`
around line 2458) — the protocol image `te_dmlt.pm`, which is the MIPS Q.921 and
Q.931 stack, and `dspdload.bin`, from which per-DSP tasks are downloaded. The D
channel's own framing layer is DSP work: `0x0209 SIGPRTX`, `0x020a SIGPRRX`, and
the `0x000b`/`0x000c` "DIVA Server PRI 2M TX/RX SIG Kernel" images. They are
staged in this emulation and never assigned to a core, because nothing here ever
brings a span up.

**Leading hypothesis, untested:** the MIPS never builds a SETUP because Q.921
never establishes. Layer 2 runs on the MIPS but its frames are carried by a SIG
task that is not running, so the datalink cannot come up and Q.931 will not
originate over a down datalink. The answering path works because injecting a
parsed message bypasses layers 1 and 2 entirely.

### What to high-level emulate, and why that boundary

If the hypothesis holds, the boundary worth HLE-ing is the **MIPS-to-SIG-DSP
D-channel queue**, because the payload crossing it is standard Q.921 framing
around standard Q.931 messages. Standing in for the far side means answering
SABME with UA, acknowledging I frames with RR, and delivering inbound network
messages as I frames. Above that, Q.931 then runs normally in both directions,
which is what makes the SIP mapping mechanical:

| Q.931 | SIP |
|---|---|
| outgoing SETUP (called number from CALL_REQ) | INVITE |
| CALL PROC / ALERTING | 100 / 180 |
| CONNECT | 200 OK |
| CONNECT ACK | ACK |

It also subsumes the incoming path: today's injected SETUP becomes an ordinary
inbound I frame instead of a poke at the parser with hand-set controller state.

Next step is to locate that queue. The number-scan technique above is the way
in — pick a distinctive dialled number, and whatever buffer the firmware
assembles for transmission will contain it.

### Ruled out, do not re-derive

- ADET (GEN_SETUP1 bit 0), Dasen (bit 1) and TonedetEnable (GEN_SETUP2 bit 6)
  change nothing on the calling side, in any combination, against silence and
  against a real answering pump. Bit 3 is the only bit that matters.
- `DM(0x046C)` is not the live condition in the `0x35d7` wait; forcing it
  negative does nothing. `DM(0x0554)` is.
- No lower-PRI event in `0x01..0x20` advances an outgoing call.

## Session 97: the D-channel tasks are never assigned, and the outgoing call dies before any DSP

Session 96 proposed the MIPS-to-SIG-DSP queue as the boundary worth high-level
emulating, and left two questions: where the firmware stages an outgoing
message, and whether the SIG tasks run at all.

### New tooling, and two gotchas worth keeping

`--watch-mem ADDR[:LEN]` logs firmware writes into a range with the writing PC;
`--hook-call ADDR[,...]` logs entries to MIPS addresses with `a0..a3` and `ra`.
Together with `--scan-ram` from Session 96 they answer "where did this end up",
"who put it there" and "is this routine reached, with what".

Two things cost time and are worth not rediscovering:

- **`--watch-mem` over a wide range perturbs the run.** Watching
  `0x100800:0x100` makes the SIG ASSIGN fail outright; the same run at `:0x18`
  works and reproduces. Adding a hook changes Unicorn's block boundaries, and
  the shim's `max_insns` budgets are sensitive to it. Narrow windows only, and
  cross-check anything surprising against an unwatched run.
- **Code hooks take the virtual address, write hooks report physical.**
  Unicorn's PC stays in kseg0 while memory is mapped at the physical
  equivalents, which is why `INTERCEPT_ADDRESSES` are unmasked. The first
  `--hook-call` run here masked them and reported zero entries for everything,
  including a routine known to run 4245 times. **Always include a
  known-executed address as a positive control**; the corrected run reports
  `0x800a4108` 4245 times at entry and 30 per call phase.

### Where the dialled number goes, and what is not built

`--watch-mem 0x80100870:24` with a distinctive number:

```text
write 0x08 to 0x00100875 from PC 0x800c9a04 (call-req)   length byte
write 0x81 to 0x00100876 from PC 0x800c9a04 (call-req)   numbering plan
write 0x35.. to 0x00100877..7d from PC 0x800c9a04        the IA5 digits
write 0x00 to 0x00100875 from PC 0x800163d8 (n-connect)  cleared again
```

Nothing is written at `0x100860..0x100874` or `0x100888..0x10089f`, so this is
an isolated length-prefixed field: no IE code byte, no protocol discriminator,
no neighbouring elements. It is the call record's stored called-party number,
placed by the IE-copy helper Session 89 identified at `0x800c99e4`/`0x800c9a04`
and cleared later by `0x800163d8`. Note the clear is why Session 96's scan saw
zeros around it: the length byte is gone by the time the run ends.

**No Q.931 message is assembled anywhere.** That also bounds the scan technique:
content scanning cannot find a transmit queue while nothing is ever queued.

### The SIG tasks are never assigned

Scanning the protocol image for the task download ids as MIPS immediates:

| id | task | immediates in `te_dmlt.pm` | first at file |
|---|---|---|---|
| `0x0209` | SIGPRTX | 3 | `0x097c20` |
| `0x020a` | SIGPRRX | 3 | `0x097c38` |
| `0x000b` | PRI 2M TX SIG kernel | 129 | `0x001028` |
| `0x000c` | PRI 2M RX SIG kernel | 312 | `0x0007f4` |

Both SIG task ids are referenced only inside **`dsp30_assign`**, which
`tools/eicon_dsp_assign.py` places at file `0x9775c..0x97dcc`, virtual
`0x800a875c..0x800a8dcc`. So the MIPS would start the D-channel framing tasks
through the same assignment machinery it uses for the modem.

Hooking that routine, its two SIG-id sites, and `dsp_assign` (file `0x79cc4`,
virtual `0x8008acc4`) for comparison:

| routine | answering (`--simulate-b-channel`) | outgoing (`--simulate-outgoing-call`) |
|---|---|---|
| `dsp_assign` `0x8008acc4` | **32 entries** | **0** |
| `dsp30_assign` `0x800a875c` | 0 | 0 |
| SIG id sites `0x800a8c20`/`0x800a8c38` | 0 | 0 |

Two results. **The D-channel signalling tasks are never assigned in any mode**,
so the framing layer never runs and there is no transport a SETUP could use.
And **the outgoing path never reaches DSP assignment at all**, where the
answering path enters `dsp_assign` 32 times — so the outgoing call dies well
before the point where the answering path does its useful work.

### What this means for standing in as the network

The intuition that the card sends a message and waits for the network to answer
is the right shape, but the card does not get that far. It parses CALL_REQ,
stores the called number, finds no D channel, and stops. Responding to its call
request therefore cannot be done at the Q.931 level, because no Q.931 is emitted;
it has to be done underneath, either by assigning and running the SIG tasks so
the firmware's own Q.921 has a transport, or by high-level emulating the
MIPS-to-SIG queue and standing in for layers 1 and 2.

Next step is to read `dsp30_assign` around `0x800a8c20` for the mailbox layout it
establishes, and to find what would call it — since in a real card something
brings the span up at start of day, and that trigger is absent here.

## Session 98: dsp30_assign is registered and then released, because a DSP fails its boot test

Session 97 established that `dsp30_assign` is never entered and asked what would
call it. It is called through a registration table, and it is deliberately
removed from that table during boot.

### Neither assign routine is called directly

A `jal` scan of `te_dmlt.pm` finds **no direct caller** of either
`dsp_assign` (`0x8008acc4`) or `dsp30_assign` (`0x800a875c`), and neither
appears as a data word. The scan is sound: the same method finds 51 callers of
the IE helper at `0x800c99e4` that Session 89 already documented. Both are
reached indirectly.

`--hook-call` on `dsp_assign`, which does run, gives the return address
`0x8002aa54` in every case, and the site is a table walk:

```text
8002a9dc: lui  $t2, 0x8012 ; addiu $t2, $t2, 0x2280   base + 4
8002a9e8: lw   $t0, ($a0)          the entry's data word
8002a9ec: beql $t0, $zero, ...     skip this entry when it is zero
8002a9f4: lui  $t1, 0x8012 ; addiu $t1, $t1, 0x227c   base
8002aa00: lw   $v0, ($v1)          the handler
8002aa4c: jalr $v0
```

### The table, and what happens to it

`--watch-mem 0x8012227c:0x18` during boot:

```text
write 0x800a875c to 0x0012227c  ra=0x800a6e5c   dsp30_assign   handler
write 0x8027c830 to 0x00122280  ra=0x800a6e5c   its data
write 0x8008595c to 0x00122284
write 0x8008acc4 to 0x00122288                  dsp_assign     handler
write 0x80272a24 to 0x0012228c                  its data
write 0x8008595c to 0x00122290
write 0x00000000 to 0x00122280  ra=0x800822fc   dsp30's data, nulled
```

Twelve-byte entries of `{handler, data, common}`. **`dsp30_assign` is slot 0 and
is registered normally** — then its data word is zeroed, and the dispatcher's
`beql $t0, $zero` skips it forever. It is not missing; it is released.

### Why it is released

The store is in a lookup-and-remove routine entered at `0x8002b934`, called from
`0x800822f4` and guarded three instructions earlier:

```text
800822e8: bnez $v0, 0x80082304    non-zero: keep the service
800822f0: lw   $a1, 0x10c($s4)
800822f4: jal  0x8002b934          release it
800822fc: sw   $zero, 0x10c($s4)
```

`$v0` is the result of the per-DSP boot test immediately above:

```text
80082250: jal 0x800a77e0 ; lw $a0, 0x10c($s4)   stream the kernel in
80082258: beqz $v0, 0x800824a0                   download failed
80082260: jal 0x800a7940 ; lw $a0, 0x10c($s4)   poll for the acknowledgement
80082268: beqz $v0, 0x800824b4                   no ack
```

Those are the two routines this file's own `DSP_BOOT_PROBE`/`DSP_BOOT_ACK`
constants already name. Both failure branches print the same string, and the
success path names what is being decided:

```text
0x800edd68: '[%d] DSP test failed'
0x800edd80: '[%d/%d] DSP OK, 23/30 channel mode'
```

### What this says, and what it does not

Established: the 30-channel service entry is registered at boot and then
released, the release is conditional on a DSP boot test, and that test is the
download-plus-acknowledge handshake the shim already reports on.

Inferred, and worth checking before relying on it: that `dsp30_assign` is the
**30-channel (E1) variant** of DSP assignment rather than a signalling-specific
routine — the message pairs 23 and 30 channels, which is the T1/E1 split, and
`dsp30_assign` is where the SIGPRTX/SIGPRRX ids appear because the E1 signalling
tasks differ. If that reading holds, the D-channel path is unreachable here for a
mundane reason: **`report_dsp_boot()` has been saying so all along** — "31 cores:
30 answered the boot handshake with 0xa5a5, 1 still held (no download)". One core
never gets a download, fails the test, and takes the 30-channel service with it.

Next step is therefore much cheaper than the HLE work Session 96 scoped: make
every core complete the boot handshake, then re-run the `--hook-call
0x800a875c` probe. If `dsp30_assign` starts being entered, the D-channel tasks
come up on their own and the queue Session 96 wanted to emulate becomes
observable instead of hypothetical.

## Session 99: the held core was a phantom, and it was not the cause

Session 98 inferred that `dsp30_assign` is released because a DSP fails its boot
test, and that the failing DSP is the one `report_dsp_boot()` has always
reported as "1 still held (no download)". **The first half stands; the second is
wrong.** The held core was an artifact of this emulation, and removing it
changes nothing about the release.

### The held core was never a DSP

The held block is `0x1c000020`. Watching it:

```text
7 writes, 1 byte each, values 0x00 and 0x12, from PC 0x80082ec4
0 writes to its IDMA address port at +0x80
```

against `0x1c000008`, the genuine on-board DSP, which takes 3345 two-byte writes
from `0x80082a80` — an actual download stream. And the writer is a helper:

```text
80082eb0: andi $a1, $a1, 0xff
80082eb4: lui  $v0, 0x8027 ; addiu $v0, $v0, 0x28c8    the card object
80082ebc: lw   $v1, 0x84($v0)
80082ec0: sb   $a1, 0x88($v0)
80082ec4: sb   $a1, ($v1)                              *(card+0x84) = byte
```

`0x802728c8` is the same card object the DSP scan carries in `$s4`. So
`0xbc000020` is a **card control register**, not the second on-board DSP the
shim's own comment claimed, and the firmware brackets its thirty-DSP init loop
with writes of `0x00` and `0x12` to it.

Routing those into the IDMA path spawned a phantom 31st core that could never be
downloaded — precisely the hazard the range comment warns about — and made every
run report a held core that does not exist. `CARD_CONTROL_REGISTER` now excludes
it. Boot reports `30 cores: 30 answered, 0 still held`, and the answering path is
unchanged: B-channel ACTIVE, `service_assign=1`, `switch_on=1`, and the 17 s
replay is byte-identical at 72.4% TX and 9610/9610 datagrams.

### It was not the cause

With the phantom gone, the release still fires from the same site, and
`dsp30_assign` is still never entered. The per-DSP guard at `0x800822e8` is hit
thirty times with `a1` counting `0x00..0x1c`, and **only the last one, index
`0x1c`, has `v0 = 0`** — before and after the fix, identically. So the 30-channel
service is released because index 28 of the scan fails, for a reason that has
nothing to do with the emulation's core bookkeeping.

Session 98's next-step suggestion — make every core complete the handshake and
the D-channel tasks come up on their own — is therefore retired. They all
complete it now, and nothing changed.

### A method correction

Session 98 read the release site's caller from `ra`. That was sound there
because `0x8002aa54` is a genuine `jalr` return, but it is **not** sound for a
hook on a mid-function address: `ra` is whatever the last call left behind.
`ra = 0x80082378` at the `0x800822e8` guard is a stale value pointing at
`lbu $v0, 0x108($s4)`, a counter increment on an unrelated path, not the code
that set `$v0`. Hook function entries when you want callers; for a branch inside
a function, `ra` means nothing.

### What is still open

What sets `$v0 = 0` for index `0x1c` and nothing else. The download and
acknowledge routines at `0x80082250`/`0x80082260` are **never entered** on this
path, so the guard's `$v0` is produced somewhere else in the scan, and the
Session 98 reading that it is the download-plus-ack result is unproven. That is
the next thing to establish, and it wants a hook on the scan's own function
entry rather than on the branch.

## Session 100: the loopback caller reaches V.34, and three defects were in this harness

The loopback rig from Session 95 now carries both ends through V.8 to a V.34
page load. Nothing in the firmware needed changing; all three faults were in
this harness, and each one was hiding the next.

### 1. The media clock started when the Call object was created

`Call.next_tick` defaulted to `time.monotonic()` at construction, which is SIP
setup time -- before the ring cadence and before several seconds of firmware
boot. By the time media actually started the endpoint owed every quantum in
between and served them at full CPU speed, two per wake-up but with the
selector returning immediately.

Measured on the answerer: **133 RTP packets in the caller's first captured
second against a steady 50**. The caller's receive queue hit its 3840-sample
high-water and discarded 9440 samples -- 1.18 s, and what was in it was the
start of ANSam. The caller then timed out of V.8, fell to V.22, then to FSK,
and both ends reported a "connect" at TrnProgress `0x00b0` that was a 300 bit/s
FSK link. That is the "V.8 falls back to V.22/FSK" of the previous commit.

Two changes, both in `eicon_adsp_sip.py`: start the media clock on the first
tick rather than at construction, and **boot the calling card in `dial()`
before the INVITE goes out** rather than on the 200 OK. The second is what
removes the skew -- firmware entry is several seconds, and doing it after the
answer means the answerer has been sending that whole time. A real modem is
initialised before it dials.

After this the two endpoints are aligned to the packet: the answerer's ANSam
starts at 0.40 s of its own clock and appears at 0.40 s in the caller's receive
capture, with zero drops and zero substitutions on either side.

For the record, the answerer's ANSam is correct and always was: 2100 Hz, 15 Hz
AM (79.2 against a noise floor under 1.6 in the envelope Goertzel), and 180
degree phase reversals every 450 ms.

### 2. NORM_L was being written into a read-database status word

> **Corrected by Session 181: this whole section is wrong.** The write database
> base is 0x3EE0, NORM_L is DM `0x3F09`, and this "fix" moved the write to
> `0x3F0D`, a word the V.8 overlay never reads. The caller's NORM_L has been
> unforced ever since. The `0x00c0`/`0xfffe` asymmetry noted at the end of this
> section is itself read at the 0x3EE0-based address. Read 181 before using any
> `+0xNN` offset below.

The write database starts at **DM 0x3EE4**, not 0x3EE0. GEN_SETUP0 is 0x3EE4
and GEN_SETUP1 -- `0x0484` answer / `0x048c` calling -- is 0x3EE5, which is
what the V.8 page's own role tests at PM `0x37c3`/`0x37c8` read (`AR =
DM($3EE4) AND $0800` and its complement). Every `+0xNN` in this file's write-DB
notes is relative to 0x3EE4: INFO0_SETUP `+0x07` is 0x3EEB, NORM_L `+0x29` is
**0x3F0D**, SPEED_SEL_L `+0x2b` is 0x3F0F.

The previous commit's NORM_L fix used 0x3EE0 as the base and therefore wrote
`0xb13f` into **DM 0x3F09**, a read-database status word whose bit 13 the V.8
detector branch at PM `0x37f1` tests. With the address corrected the caller's
V.8 state machine stopped stalling at state 2.

Also visible in the same diff, and still open: the caller's SPEED_SEL_L is
`0x00c0` where the answerer's is `0xfffe`.

### 3. A page request for the resident page re-entered it mid-handshake

`_serve_page_request` fired whenever `DM(0x3FC1)` bit 8 and `DM(0x3131)` were
both set, and served whatever descriptor `DM(0x3132)` held. Nothing cleared
`DM(0x3131)`, and for the forced originate V.8 request (`ORIGINATE_V8`) nothing
ever could -- the shim writes it from outside. So the request re-fired, with
`DM(0x3132)` still holding `0x025F`, and the V.8 entry path ran again: it
zeroes the TX word `DM(0x3764)` and both timer sentinels `DM(0x3995)`/
`DM(0x3999)`. That landed in the middle of ANSam detection, at 1.62 s, exactly
at the state 2 to 3 transition.

The shim now acknowledges a request naming the resident page without
re-entering it. This is the change that made the caller transmit.

### The V.8 state machine, for whoever needs it next

The V.8 page's sequencer is a script interpreter. `DM(0x049F)` is the script
pointer; PM `0x37b7` walks (field, value) triples into `DM(0x073F + field)`
until field `0x11`; field `0x0C` is `DM(0x074B)`, whose low byte PM `0x3799`
publishes as TrnProgress. Each block installs three test routines
(`DM(0x0792..0x0794)`) and two alternative script pointers
(`DM(0x0790)`/`DM(0x0791)`); the tests are called at PM `0x37a5`/`0x37a9`/
`0x37ad` and a `LE` return either advances or branches. The useful ones:

| PM | test |
|---|---|
| `0x37d5` | constant 1 -- never fires |
| `0x37d7` | countdown of `DM(0x0749)` |
| `0x37f7` | `0x0780 - DM(0x07BD)`, the energy hit counter |
| `0x37dc` | `0x00F0 - DM(0x0778)`, the tone-classifier confidence |
| `0x37c3`/`0x37c8` | GEN_SETUP0 bit 11 |

`DM(0x07BD)` is incremented at PM `0x3ec8` when a filter magnitude beats the
threshold in `DM(0x0748)` (`0x07d0`); `DM(0x0778)` at PM `0x3f0d` against
`DM(0x0747)`. Watching PM `0x37a6`/`0x37aa`/`0x37ae` and reading `i4` and `ar`
out of the `[EXEC]` line is how all of the above was read, and it is cheap.

### Where it gets to now

```text
caller                                     answerer
0.08  V.8 page resident                    0.02  V.8 page resident
                                           0.54  ANSam (state 4)
1.24  ANSam confirmed (state 2)
2.10  transmits CM -- V.21 ch1             2.14  hears it
3.04  INFO page (7)                        3.04  INFO page (7)
5.20  V.34 page (8) requested and loaded   5.20  V.34 page (8), TrnProgress 0x0071 -> 0x0072
5.24  falls back to page 11, then page 0
5.44  TrnProgress 0x2f3e
```

So **V.8 selects V.34 and both ends load the V.34 page.** The answerer settles
at TrnProgress `0x0072`; the caller collapses within 40 ms of the load, walking
bootpage 8 -> 11 (AT offline) -> 0 (DIAL) and then publishing a garbage state
word. That is the next thing to look at, and it is a different problem from
everything above: the handshake is now good enough to get there.

Second open item from the same run: once page 8 is resident **neither endpoint
holds real time** -- the pacing ratio falls to 0.65x with thousands of clock
holds on both sides. V.34 costs more per sample than the 20 ms budget allows,
and because each end waits for the other they decelerate together. Loopback
observations of *state* stay valid (the DSP is sample-clocked), but anything
timing-derived after 5.2 s is not.

## Session 101: the caller's V.34 collapse is the echo canceller's unbounded fill

Session 100 left the loopback caller loading the V.34 page and collapsing 40 ms
later — bootpage 8 → 11 → 0 and a garbage state word. It is the near/far echo
bulk-delay adapter of Sessions 58–93, caught in the act on a page that is not
V.90.

### The abort is a deliberate branch in the V.34 page's entry

PM `0x27dd` is the V.34 page's per-frame entry: it reloads `L0..L7`, `M0..M6`
and `MODE_CTL`, then

```text
27eb: 821650  AX0 = DM($2165)
27ec: 22780f  AR = AX0 + 0
27ed: 1a90c1  IF NE JUMP $290C
```

and `0x290C` is the give-up path — it zeroes `DM(0x3FA7..0x3FA9)`, sets the
boot-request bit `0x0100` in `DM(0x3FC1)`, and boots whatever page number is in
`DM(0x2252)`:

```text
2910: AX0 = DM($3FC1)
2911: AR = AX0 OR $0100
2912: DM($3FC1) = AR
2913: AR = DM($2252)
2914: DM($3FB0) = AR
```

`0x27ed` is the only reference to `0x290C` in the overlay, and an exec watch
confirms the caller entered it from `0x27ed`, twice, while the answerer never
entered it at all. `DM(0x2252)` read `0` — DIAL — which is why the caller went
to page 0.

`DM(0x2165)` has exactly one writer in the V.34 overlay (PM `0x2a08`, which
sets it to 1) and is zero in both the V.34 and INFO overlay DM images. So a
nonzero value there at page entry is not the firmware latching anything.

### What wrote it

A DM write watch on `0x2160..0x2168` names the writer:

```text
dm w 2161=2161 ppc=1930 cyc=115220972 i0=2161
dm w 2163=feea ppc=1930 cyc=115222943 i0=2163
dm w 2165=2859 ppc=1930 cyc=115225680 i0=2165 mr0=2859
dm r 2165=2859 pc=27ec cyc=115225943      <-- the entry test, 263 cycles later
dm w 3fb0=0000 ppc=2914 cyc=115225954     <-- the abort
```

**PM `0x1930` is the bulk-delay adapter's store**, the instruction Session 90
traced sweeping `I0` linearly across 1556 addresses because its modulo bound
reads zero (Sessions 91–93). Here it walks up through the V.34 page's own
variables at DM `0x2160..0x2167` — further than the `0x0049..0x1b41` range
Session 90 measured — and one of the words it lands on is the page's abort
flag.

The asymmetry is stark. Counting writers into `0x2160..0x2168` over one call:

| writer | caller | answerer |
|---|---|---|
| PM `0x1930` | 6 | 0 |
| PM `0x1934` | 7 | 0 |

The adapter's cursor simply happened to be over that block on the caller. This
is the same lottery as the DIL blocker, with a victim that is much easier to
read: a single flag with a single test and an unambiguous consequence.

### Confirmed by removing it

`EICON_V90D_BULK_ADAPTER` already RTSes out the adapter's tail at PM `0x19c8`
when page `0x026A` loads. Extending that to `0x0261` removes the collapse
outright: the caller stays on page 8, and the two ends then loop symmetrically

```text
INFO (page 7) ~1.8 s -> V.34 (page 8) ~0.25 s -> INFO ...
```

for as long as the call runs — ten identical round trips in 26 s, both ends
switching within 20 ms of each other.

Note this does not make the harness *more* wrong: PM `0x1900..0x19c8` is
resident kernel, it was live on every non-V.90 page, and what it was doing was
corrupting page state. It is still a real functional gap, and it is still the
same unfixed defect.

### What is open now

Inside each V.34 attempt the two ends diverge:

| | caller | answerer |
|---|---|---|
| TrnProgress | `0x0060` for the whole 280 ms | `0x0071` → `0x0072` → `0x0074` → `0x0090` |

`0x0060` is the caller's page-entry state. The answerer walks phase 3; the
caller never leaves entry, so it transmits nothing for the answerer to train
against and both fall back to INFO. That is the next question, and it is the
same shape as the V.8 one Session 100 answered: an originate-side page that
loads and then does not start.

## Session 102: the V.34 caller parks on a silence that never comes, because INFO published nothing

The caller's V.34 page loads, publishes TrnProgress `0x0060` and never moves.
It is not stuck: it is waiting, correctly, for a condition its peer never
produces, and it is waiting there because the INFO page handed it a zero.

### The V.34 page's role fork, and how the two scripts are stored

The V.34 page uses the same script-interpreter shape as V.8. `DM(0x14A5)` is
sequencer A's script pointer, `DM(0x2192)` is sequencer B's, blocks are
(field, value) entries three words long terminated by field `0x19` (A) or
`0x24` (B), and field `0x10` lands in `DM(0x2147)`, which PM `0x2d83`/`0x2ddd`
publish as TrnProgress. Record base is `0x2137`, so:

| field | address | meaning |
|---|---|---|
| `0x0E` | `DM(0x2145)` | detector threshold |
| `0x0F` | `DM(0x2146)` | countdown, tested by PM `0x2e32` |
| `0x10` | `DM(0x2147)` | state → TrnProgress |
| `0x11..0x14` | `DM(0x2148..0x214B)` | branch targets, index into `DM(0x0676)` |
| `0x15..0x19` | `DM(0x214C..0x2150)` | test routines, index into `DM(0x064B)` |

The fork is at PM `0x1046`:

```text
1046: AR = DM($3F94)
1047: AR = AR AND $0008
1048: DM($2198) = AR
```

`DM(0x3F94)` bit 3 is the GEN_SETUP1 role bit, so `DM(0x2198)` is 8 on the
caller and 0 on the answerer. PM `0x2d6e` reads it and picks *the record
decoder*:

| `DM(0x2198)` | decoder | sequencer B script | measured |
|---|---|---|---|
| non-zero (calling) | PM `0x2E1A` — **low** byte of each word | `0x1EA2` | caller |
| zero (answer) | PM `0x2E24` — **high** byte of each word | `0x1E81` | answerer |

Both roles share sequencer A's script base `0x1A2E`. The two scripts are
**byte-interleaved into the same words** — one role reads the low bytes, the
other the high bytes. Confirmed live: `DM(0x2192)` takes `0x1ea2` on the caller
and `0x1e81` on the answerer, and the record stores land at PM `0x2e21`
(68,603 times) on the caller against PM `0x2e2d` (19,280) on the answerer.

That also settles the role question before it is asked: the caller is on the
correct half.

### Where the caller goes, and why

Decoding `0x1A2E` low-byte and following `DM(0x14A5)` live gives:

```text
1a2e -> 1a6d -> 1a79 (state 0x53) -> 1a91 (state 0x54) -> [branch] -> 1ae5 (state 0x60) <-> 1af7
```

Block `0x1a91` carries `test0 = PM 0x2ef1` with branch target `0x1ae5`, and

```text
2ef1: AR = DM($3F89)
2ef2: JUMP $2ED1        ; AR + 0, RTS  -> fires when DM(0x3F89) == 0
```

`DM(0x3F89)` is zero, so the caller branches straight to `0x1ae5` and skips
states `0x56`, `0x58`, `0x5a`, `0x5c`. Measured over sixteen V.34 attempts in
one call: evaluated 16 times, fired 16 times.

Block `0x1ae5` decodes to

```text
state 0x0060   threshold(0x0E) = 0x02bc   timeout(0x0F) = 50
test4 (primary) = PM 0x2e32   -- the countdown; on expiry run the next block
test0           = PM 0x2ef3   -- branch target 0x1ae5, i.e. itself
```

and PM `0x2ef3` reads, then clears, `DM(0x13BF)`. That flag is set by the
kernel's six-tap detector:

```text
0e36: AR = ABS MR1
0e37: AY0 = DM($2145)        ; the threshold the block just armed
0e38: AF = AR - AY0
0e3a: IF GT AR = 0 + 1
0e3b: DM($13BF) = AR
```

So state `0x0060` on the originate side means **"wait until the line has been
quiet for 50 ticks"**, and every detection re-enters the block — which reloads
its own timeout. Watching PM `0x0e3a` live, the caller's `|MR1|` oscillates
across the threshold (`0x0053`, `0x0268`, `0x03b4`, `0x0505`, `0x07cb` against
`0x02bc`), firing about every other evaluation. The countdown reads `0x31`,
`0x31`, `0x31`, `0x30`, `0x31` — it never gets near zero.

The answerer's own state-`0x0060` block, `0x1adc` in the high-byte script, is a
different thing entirely: timeout 128, `test0 = PM 0x2e6c` (which is `AR = 0+1;
RTS`, a placeholder that never fires), and no self-branch. It leaves on its
timer, which is why the answerer walks `0x0071 → 0x0072 → 0x0074 → 0x0090`
while the caller sits still.

### The zero that put it there

`DM(0x3F89)` has exactly one writer in the whole tree, and it is in the **INFO
overlay**, at PM `0x3dfd`, in the block that publishes phase 2's results:

```text
3df1: DM($3FBB) = SR0     BaudInfo
3df9: DM($3F88) = SR0     from DM(0x1703), DM(0x1704), DM(0x16FC), DM(0x16FD)
3dfb: DM($3F8A) = AR      from DM(0x16FE)
3dfd: DM($3F89) = AR      from DM(0x1705)
3dff: DM($3F8B) = AR      from DM(0x0609)
3e01: DM($3F8C) = AR      from DM(0x1706)
```

Read out of the capture at the moment page 8 loads, on both ends:

```text
3FBB=0x30dd  3F88=0x0000  3F89=0x0000  3F8A=0x0000  3F8B=0x0000  3F8C=0x0000
```

**Only BaudInfo is published.** The whole received-parameter group is zero. The
routine ran — `0x3FBB` proves it — so `DM(0x1703..0x1706)` and
`DM(0x16FC..0x16FE)` are themselves empty when INFO hands over.

Two corollaries worth recording:

- `DM(0x3F8A)` is the retrain reason code, not a parameter: it reads `0x5678`
  for exactly one frame at each fallback, written by PM `0x2d66` on the path
  that also sets `DM(0x2252) = 7` (INFO). `0x5679` is its sibling at PM
  `0x2d61`.
- **`DM(0x3F8B)`, the "DIL flag" this file has logged since Session 87, is one
  of these INFO-published words** (from `DM(0x0609)`), not an independent
  measurement. It is zero for the same reason the rest of the group is. That is
  a better explanation of why it split nine captures perfectly and then failed
  than "nine samples was not enough".

### What to do next

Find why the INFO page's `DM(0x1703..0x1706)`/`DM(0x16FC..0x16FE)` are empty at
handover. The INFO phase visibly runs to completion on both ends — TrnProgress
walks `0x0022` through `0x004f` in lockstep and BaudInfo comes out — so this is
a parse or store that is not happening, not a phase that did not run. `DM(0x1705)`
is the single word that decides the caller's first V.34 branch, so it is the one
to trace first: find its writer in the INFO overlay and watch what that writer
sees.

## Session 103: INFO does publish — the fields V.34 needs are the ones that come out empty

Session 102 asked why `DM(0x3F88..0x3F8C)` are zero when the INFO page hands
over. The answer is narrower than "INFO published nothing", and the correction
matters: the page runs its whole receive cycle, the message framing works, and
real side-specific content does arrive. What is empty is the first packed word,
which is where every field the V.34 originate script reads happens to live.

### The publication is a bit-field split of one word

PM `0x3d6f` (and its twin at `0x3e1c`) is the whole of it:

```text
3d6f: SR0 = DM($060A)
3d71: AR = SR0 AND $0007   -> DM($1703)      bits 0..2
3d75: AR = SR0>>3 AND 7    -> DM($1704)      bits 3..5
3d79: AR = SR0>>6 AND $7F  -> DM($1705)      bits 6..12   -> DM($3F89)
```

`DM(0x060A)` reads **`0x2000`** — bit 13, and nothing below it. So all three
fields are zero for one reason, not three, and `DM(0x3F89) = 0` is what parks
the V.34 caller at state `0x0060` (Session 102).

### Where that word comes from, and it is not truncated

`DM(0x0608..0x060E)` is filled by the message packer at PM `0x358E`, five words
per message, stored at PM `0x3597`. The destination is selected by length:

```text
3576: AY0 = $0110              ; or $01E0 when DM(0x3F94) bit 1 is set
3577: AX0 = DM($3F94)
357c: MR0 = DM(I1,M1)
357d: AX0 = DM($1651)
357e: AR = AX0 - AY0
357f: IF EQ JUMP $3588         ; scripted length matches -> store 5 words at 0x0608
3580: AR = DM(I5,M5)           ; otherwise skip two, i.e. store at 0x060A
3581: AR = DM(I5,M5)
```

`DM(0x1651)` is not measured — it is field `0x0F` of the INFO page's own script
record (base `0x1642`), and **the INFO page uses the same dual-decoder,
byte-interleaved script the V.34 page does**: PM `0x336A` reads the low byte of
each three-word entry, PM `0x3376` the high byte, and live the caller writes
through `0x3372` while the answerer writes through `0x3380`. Same fork, same
table, same trick as Session 102's `DM(0x2198)`.

Both layouts are exercised, and the layout selection is correct. Measured over
one call, per INFO residency:

| | first message | later messages |
|---|---|---|
| `DM(0x1651)` | `0x0110` = the hardcoded value | `0x0260` (caller) / `0x04d0` (answerer) |
| stored at | `DM(0x0608..0x060C)` | `DM(0x060A..0x060E)` |

and the V.34 handover is fed by the second layout, which is why the extractors
read `DM(0x060A)` onwards.

The packed words themselves:

```text
caller    0608..060C: 21fd 0000 0000 0000 8000
          060a..060e: 2000 0b78 0000 0000 4000
answerer  0608..060C: 21fd 0000 0000 0000 4000
          060a..060e: 2000 8068 0100 0740 c007
```

`0x21fd` is identical on both ends — a preamble, not data. Everything after it
**differs between the two sides**, so the receiver is genuinely demodulating the
peer and not echoing a constant. The content is simply concentrated in word 1,
and word 0 — the only word the field extractors touch — carries one bit.

### The soft-decision array is sparse, not short

The packer consumes 80 entries from `DM(0x068E..0x06DD)`, one per output bit,
where `±1` is a decision and `0` is "never written". Dumping all 80 for the
invocation that feeds the first V.34 load (`.` = no decision):

```text
word 0: 0x068e..0x069d   ...........0....
word 1: 0x069e..0x06ad   .0111.01.0......
word 2: 0x06ae..0x06bd   ................
word 3: 0x06be..0x06cd   ................
word 4: 0x06ce..0x06dd   ................
```

Eight decisions out of eighty. This corrects a first reading of the same data:
the array is not truncated after the first sixteen entries — entry 79 is
non-zero on every invocation on both ends — it is *sparse*, roughly one slot in
eight.

And it is not sync thrash. Watching the parser pointer `DM(0x16BD)`, which the
page moves between `0x3520` (sync hunt), `0x3546` (collect) and `0x3561`
(tail), the cycle completes exactly **eight times per call on both ends** — one
clean pass per message, no re-syncs.

### The next question, and it needs no live call

The collector and the packer disagree about slot cadence, and that is the thing
to settle:

- PM `0x3546` writes `DM(I1)` with `M0 = 0` — the same slot — on **every**
  symbol, shifting the new hard decision (`DM(0x060F)`, `AX1 >= 0x0578` at PM
  `0x3513`) in from the bottom. It advances `I1` only at PM `0x3559`, and only
  when the 16-word receive window at `DM(0x0620)` wraps, i.e. once per sixteen
  symbols.
- PM `0x358E` consumes **80 consecutive slots**, one per output bit.

One slot per sixteen symbols against one slot per bit is a factor of sixteen,
and it is the right size to explain one decision in eight. Establishing which
side is being read wrongly — most likely a misread of the `DM(I0,M0)`/`L0 =
0x0010` circular window set up at PM `0x3517` — is a static question about PM
`0x3546` and PM `0x358E` plus one `I1` trace, and it does not need the loopback
at all.

## Session 104: there is no cadence mismatch — the factor of sixteen is the oversampling

Session 103 ended by naming a "slot cadence mismatch": the collector at PM
`0x3546` advancing its destination once per sixteen symbols against the packer
at PM `0x358E` consuming one slot per output bit. **That mismatch does not
exist.** The two are the same cadence, and the factor of sixteen between them is
the receiver's oversampling. This session is the measurement that retires it,
and no code changed.

### What the collector actually does

Watching PM `0x354d` (the per-symbol store) and PM `0x3559` (the `MODIFY (I1,
M1)` that advances the slot) over one call, grouped by message:

| message | decisions collected | slots visited | decisions per slot |
|---|---|---|---|
| first, both ends | 272 | 17, `0x068c..0x069c` | 16.0 |
| later, caller | 608 | 38, `0x068c..0x06b1` | 16.0 |
| later, answerer | 1232 | 77, `0x068c..0x06d8` | 16.0 |

272, 608 and 1232 are exactly the `DM(0x1651)` values Session 103 recorded as
`0x0110`, `0x0260` and `0x04d0`, and 272/16 = 17, 608/16 = 38, 1232/16 = 77.
So `DM(0x1651)` is the message length **in hard decisions**, the collector runs
for exactly that many, and it packs sixteen consecutive decisions into each
slot. Every number is self-consistent to the symbol.

The receiver therefore runs at **16x oversampling**: each slot is one symbol's
worth of decisions, which is why slots read `0x0000` and `0xffff`. The packer
taking one bit per slot is the decimation back to symbol rate — the same
cadence, expressed once per symbol on each side.

The bit it takes is chosen deliberately. `SE` is `-1` throughout the packer
loop (measured at PM `0x3591`, all 80 iterations, both ends), so `SR0 = MR0 >>
1` and the extracted bit is bit **1** of the slot, not bit 0. Slots reading
`0x0001` and `0xfffe` — one decision of the next symbol having landed in this
slot — appear throughout the collector trace, and picking bit 1 is exactly what
makes the decimation immune to that one-decision boundary jitter.

### The observation it was built on was also wrong

Session 103 read a slot of `0x0000` as "never written" and concluded the array
was sparse — eight decisions in eighty. A slot of `0x0000` is **sixteen
consecutive zero decisions**. Measured at the collector's own store, `0x0000` is
81.7% of stores on the caller and 84.8% on the answerer, with the rest being a
run of ones shifting through (`0x0001`, `0x0003`, `0x0007`, … `0x7fff`,
`0xffff`). The array is fully written every time; the received bitstream is
simply mostly zeros.

That also disposes of the level worry left over from Session 102. The hard
decision at PM `0x3513` (`AX1 >= 0x0578`) comes out **1 for 54.3% of symbols on
the caller and 64.8% on the answerer**, over a sharply bimodal magnitude
distribution — 40,364 samples in `0x0000..0x00ff` against 40,346 in
`0x0f00..0x0fff` on the caller. The slicer is not marginal and the level is not
the problem.

### What is left, stated without the error

Two things survive, both narrower than the retired claim.

- **The packer's extent is fixed while the message length is not.** PM
  `0x3588..0x358d` is five hardcoded calls, 80 slots. The collector writes 17,
  38 or 77. On the caller's 38-symbol message, packed words 2 to 4 are slots 38
  to 79 — memory the collector never touched this pass. The answerer's
  77-symbol message very nearly fills the 80, which says the five-word packer is
  sized for *that* message, not the caller's.
- **The two ends expect different lengths, and the expectation is local.**
  `DM(0x1651)` is field `0x0F` of the INFO page's own script record, so each
  side's expected length comes from its own half of the byte-interleaved script:
  608 decisions on the caller, 1232 on the answerer. Whether each side is
  expecting the length its *peer* transmits — rather than the one it transmits
  itself — is the thing to check, and it is checkable by measuring how long each
  end's INFO transmitter runs and comparing that with the other end's
  `DM(0x1651)`.

The V.34-visible symptom is unchanged: word 0 of the caller's received message
packs to `0x2000`, so bits 0..12 — every field `DM(0x1703)`, `DM(0x1704)` and
`DM(0x1705)` is cut from — are zero, and `DM(0x3F89) = 0` parks the V.34
originate script at state `0x0060`.

## V.90 TX mailbox and TIKRNL ownership notes

The host-facing V.90 synchronous TX interface is `DM(0x3F05..0x3F07)`
(`TXD0..TXD2`), with `DI_control` bit 15 (`DM(0x3FAD)`) requesting a new
packet. In V90D mode TXD0 bit 0 is the oldest bit; one request carries 21--42
bits across the three words.

The resident `0258` TIKRNL task also owns these words. In the extracted task PM
`0x06D0` writes `TXD0 = 0xFFFF`, while PM `0x0732/0x0734`, `0x0738`, and
`0x0740` write TXD0, TXD1, and TXD2 from its internal bearer store. Those are
relocated to `0x06D7/0x0739/0x073B/0x073F/0x0747` in the live build-117-926
core. A host write was therefore overwritten in the same request; a later host
rewrite and a cleared request bit proved neither which owner won nor which words
the modem page consumed. Explicit host-source mode now suppresses this exact
five-store signature; see the end-to-end result below.

`DM(0x3FC0)` and `DM(0x3FC1)` are `RSTATUS_CH` and `RSTATUS`, not TX-buffer
ownership flags. Their `0x0400` bits are status/change state and must not be
fabricated as a TX handshake. The authoritative mailbox request/consume bit
is `DI_control` bit 15.

The task derives its internal TX word-count state in PM `0x05D6..0x05E6`,
using `DM(0x3F09..0x3F0B)` and the private lookup table at
`DM(0x31EE..0x31F4)`, then stores the result in `DM(0x31B2)`. The table was
observed cleared during the V.90 overlay handoff and is restored by the native
shim from the `0258` task image. The negotiated rate word remains authoritative
for the host packet width; mailbox ownership is no longer inferred from this
private word-count state.

## NL N_DATA bearer path: requests are posted without completion flow control

The live CX run with `EICON_V42_NL_DATA=1` isolated a separate failure from the
V.90 TX mailbox. The NL bridge is producing application/LAPM payloads, but the
firmware never demonstrates acceptance of an `N_DATA` request.

The important distinction is between the local queue and an IDI request. The
`[nl] N_DATA queued` diagnostic is emitted after the payload has been removed
from `nl_data_queue` and `post_request()` has placed an actual `N_DATA` request
in PR RAM. It does not mean that the request was accepted by NL. Acceptance
would require a matching return code, but the run produced no `[nl] RC=...`
lines at all.

The current call path is unconditional. `_step_mips()` calls
`_service_n_data()` before running the MIPS main loop. If the local queue is
empty, `_service_n_data()` obtains up to 270 octets from LAPM, removes the
payload, and posts it using local NL entity `Id=1`, channel `0`, and reference
`1`. It neither checks that `N_CONNECT` has completed nor records an
outstanding request and waits for its return code before posting another one.

The timing makes the problem unambiguous:

| event | observed time |
|---|---:|
| first `N_DATA` request posted | 6.447 s |
| `CONNECT` reported | 16.74 s |
| synchronous data state `0xc6` | 18.84 s |
| first speed-complete state `0xc8` | 19.58 s |
| NL return codes observed | none |

Thus the bridge was submitting bearer data during V.90 training, more than ten
seconds before the modem reported `CONNECT`. It continued posting requests as
the ring offsets wrapped (`0x03e0`, `0x0500`, ..., `0x26c0`) instead of applying
NL request/return-code flow control. The `66106/66106 accepted/requested`
summary at call end is the RTP media scheduler's datagram count; it is not an
NL acceptance count and must not be used as evidence that `N_DATA` reached the
DSP TX source.

This also explains the simultaneous DSP observation. Throughout the run the
V.90 source trace showed `TXD=ffff/ffff/ffff`, `DM(0x31B2)=0`, and
`DI_control=0`. The NL log proves that the shim attempted to submit data, but
there is no evidence that NL consumed any of those requests or transferred a
payload to the synchronous TX mailbox. The fill words therefore remain a DSP
side symptom, not proof that the LAPM payload itself was malformed.

The required bridge behavior is:

1. retain LAPM bytes in the local queue while the modem is training;
2. wait for successful `N_CONNECT` completion and the usable synchronous data
   state (`0xc6` or later);
3. post one `N_DATA` request;
4. retain it as outstanding until the matching NL return code is received; and
5. only then post the next chunk, handling rejection or other return codes
   explicitly.

Until a matching return code and a resulting change in the DSP TX source are
observed, `N_DATA queued` should be described as **submitted by the shim**, not
as delivered to the firmware.

### Four defects found in the bridge, and what was changed

Reviewing the bridge against `tty_module/isdn.c` found that the missing flow
control above was not the only fault, and probably not the first one to matter.

**The request was addressed to an entity that was never assigned.**
`_service_n_data()` posted `N_DATA` on a hardcoded Id of 1, on the belief that
the Linux driver used a separate local entity for bearer data. It does not.
`isdn.c:3282` issues every post-`ASSIGN` request on `C->Net.Id`, which is the
Id the adapter returned in `ASSIGN_OK`; `NL_ID` (`0x20`, `pc.h:84`) is only the
pre-assignment "assign me" Id, and `isdn.c:4143` restores it on removal. There
is no bearer-data Id. The shim's own `N_CONNECT` already used the assigned Id,
and the assigned value was already carried as `shim.nl_entity_id` -- the data
path simply never read it. This is the most likely reason the run produced no
return codes at all: requests to an unassigned Id are dropped before they reach
the NL state machine. The bridge now posts on `self.nl_entity_id` and refuses
to post at all when no NL entity was assigned.

**Two bit sources could feed one HDLC decoder.** In NL mode the transmit side
already substituted mark fill for the synchronous mailbox, but `_lapm_active`
was still set on that path, and `_lapm_active` was the only gate on
`_service_rx_data()`. So DSP `RXD` bits kept being shifted into
`LapmEndpoint.feed()` at the datagram rate while `N_DATA` indications were
expanded into the *same* decoder. Two unrelated streams interleaved into one
`HdlcDecoder` desynchronise the flag search and fail the FCS on everything.

The first live CX call settled which source is real, and the answer was not the
one this code assumed. With the NL entity assigned `B2_TRANSPARENT` the
firmware accepted all 857 `N_DATA` requests and returned an `N_DATA`
*indication* for none of them: the receive direction stays on the DSP mailbox
even while the transmit direction rides the NL entity. So the collision is real
in the code but has never occurred at runtime -- the indication source has
never produced anything. Suppressing the mailbox on the assumption that
indications would replace it starved the decoder completely: call 1 finished
`HDLC good/bad/abort=0/0/0`, no V.42 detection at all, and a T400 fallback.

`_service_rx_data()` therefore keeps decoding the mailbox and only stops if an
`N_DATA` indication is actually observed (`_nl_rx_seen`), which is reported when
it first happens. The mailbox acknowledgement is unconditional either way, or
the DSP stalls waiting for the host to consume the datagram.

**No gating and no flow control**, as described above. The bridge now checks
`N_CONNECT` acceptance (recorded at the call-setup site, which is the only
place that return code is consumed) and `DATASTATE >= 0xC6` before posting, and
keeps one request outstanding at a time: `_nl_busy` is set on submission and
cleared only by the matching return code, with `OK_FC` latching `_nl_fc` until
`READY_INT`, mirroring `net_busy`/`NetFC` in `isdn.c:3290` and `isdn.c:4184`.

**LAPM's timers ran on the main-loop clock.** `LapmEndpoint._service()` runs
once per `take()` call and its T401, T403 and poll counters advance per call,
so they are calibrated for the datagram rate. The old bridge pulled 270 octets
at a time from `_service_n_data()`, once per main-loop pass, which put every
LAPM timer on an unrelated clock. The media path now clocks LAPM at the line's
datagram rate exactly as the non-NL path does, and buffers the bits in a
transmit elastic store that `_service_n_data()` drains in whole octets. As a
side effect nothing is produced during training at all, since `_lapm_active`
does not start before `0xC6`, so there are no queued training-era bytes to
discard. `LapmEndpoint.take_octets()` has been removed: it was the block-pull
helper that made the wrong clock easy to reach for, and it had no other caller.

The end-of-call summary now reports `[nl] N_DATA totals:` with accepted and
submitted counted separately, so acceptance can no longer be inferred from the
RTP scheduler's datagram count.

### Live CX results

Three calls, CX93001-EIS_V0.2013-V92 on `/dev/cu.usbmodem123456781` dialling
6001, `AT+MS=V90,1,300,9600,300,48000` -- V.90 with the modem's upstream capped
at 9600, which connects more reliably than the 12000 the modem was set to.
Captures under `artifacts/interop/nldata-cx/`.

| | call 1 (NL) | call 2 (NL, mailbox restored) | call 3 (baseline, no NL) |
|---|---|---|---|
| `N_DATA` accepted/submitted | 857/857 | 877/877 | n/a |
| `N_DATA` rejected | 0 | 0 | n/a |
| octets submitted | 91388 | 93540 | n/a |
| `N_DATA` indications | 0 | 0 | n/a |
| HDLC good/bad/abort | 0/0/0 | 0/1/32 | 0/0/0 |
| V.42 detection | none | **ODP detected, ADP sent** | none |
| modem result | connected | connected | `NO CARRIER` |

**`N_DATA` is accepted.** Every request drew `RC=0xff (OK)` against its own
reference. Before the entity-Id fix the same path produced no return code at
all, which is what this section was originally written about. The bearer is
addressable and the flow control matches the driver's one-outstanding rule.

**The NL transmit path reaches the line where the synchronous mailbox does
not.** The peer sent a V.42 ODP only on the NL runs; the baseline run, with the
same LAPM stream going into the DSP TX mailbox instead, drew no response and
the modem gave `NO CARRIER`. That is consistent with the long-standing
`TXD=ffff/ffff/ffff`, `DM(0x31B2)=0` symptom -- the mailbox is not transmitting
-- and is the first evidence of a transmit route that does.

**LAPM still does not establish.** Call 2 reached the protocol phase and then
took 32 HDLC aborts and one bad FCS with no good frame.

The "ODP detected (4 DC1s)" line in that call must not be read as evidence that
the receive stream is real V.42. `ODP_EVEN`/`ODP_ODD` are 10-bit patterns and
`_scan_odp()` asks for four alternating matches; over roughly 260 kbit of data
state at 7200 bit/s, chance alone yields on the order of 500 matches of those
two patterns. The detector will fire on noise, and an earlier version of this
section wrongly cited it as proof that the bit order was right. It proves
nothing about the receive path.

So the Session 87 question was still open: either the receive side is misframed
(wrong datagram bit count, wrong bit order, or dropped/duplicated `RXD` valid
words), or the receiver is not producing a demodulated stream at all.

## The receive side is not misframed; it is misdemodulating

Session 87 could not separate those two because a live call tests one framing
guess at a time. `EICON_RX_TRACE=<path>` now records the raw
`(sample, count, mask, word)` of every datagram the mailbox publishes, and
`tools/rx_frame_search.py` replays one capture under every combination of bit
count, bit order and RXD0/RXD1 ordering, scoring each by HDLC frames that pass
FCS. A valid FCS is a 1-in-65536 accident, so even a handful of good frames
identifies the right hypothesis; `tests/test_rx_frame_search.py` plants frames
under a known hypothesis and requires the search to find them there and nowhere
else, which is what makes a null result trustworthy.

Capture: call 5, 47619 datagrams over samples 136748..290769 (19.25 s of data
state). **No hypothesis produced a single valid FCS** -- not one of the 64
combinations tried.

The framing assumptions were not the problem, and the capture shows why:

- **Only bits 15..13 are ever set**, in all 47619 datagrams. The left-aligned,
  MSB-first, 3-bit-per-datagram layout `_service_rx_data()` assumes is exactly
  the register layout the pump is using.
- **The datagram rate is right.** 47619 datagrams over 19.25 s is 2474/s
  against the expected 2400, about 3% high.
- **The count word is not being misread.** The 3927 datagrams published as 2
  bits rather than 3 form a single contiguous run at the end of the call
  (indices 43692..47618) -- a genuine 7200 to 4800 rate change, not scatter.

What the capture does show is the content. Only nine distinct words appear, and
the 3-bit payload distribution is dominated by all-ones:

| payload | observed | predicted if the peer sends continuous HDLC flags |
|---|---:|---:|
| `111` | 55.3% | 50.0% |
| `010` | 10.7% | **0** |
| `000` | 9.0% | **0** |
| `100` | 8.7% | 12.5% |
| `001` | 8.1% | 12.5% |
| `101` | 4.0% | **0** |
| `110` | 2.3% | 12.5% |
| `011` | 2.0% | 12.5% |

Continuous flags (`0x7e` repeated, LAPM idle) are periodic with period 8, so
grouping into 3-bit datagrams yields groups drawn only from
`{011, 111, 100, 001, 110}` at *any* alignment. `000`, `010` and `101` cannot
occur, whatever the phase. **23.7% of received datagrams are in those three
impossible values.** That rules out alignment as the explanation -- a phase slip
shifts which legal value appears, it cannot manufacture an illegal one -- and
leaves symbol errors.

So the peer is transmitting (the gross shape matches flag idle) and the card's
receiver is demodulating it with roughly a 24% symbol error rate. No framing
layer survives that, which is why LAPM has never established, and it is why
every framing hypothesis scores zero. **The fault is upstream of framing, in the
receive signal path.**

The obvious suspect is the echo canceller. Session 88 established that the
near/far echo bulk-delay adapter at PM `0x1900..0x19c8` is RTSed out on every
page-14 load and that enabling it is currently worse than leaving it off. This
path runs SIP/RTP to an ATA to two-wire to the modem, so there is a hybrid
generating exactly the echo that adapter exists to remove, and the receiver has
to pull the analogue upstream out of it. A 24% symbol error rate on the upstream
is what that would look like. This is a hypothesis, not a measurement: the
capture localises the fault to the receiver but does not name the cause.

## Why enabling the echo canceller destroyed the state word

Session 88 left this as "turning the echo canceller on is not a switch that is
being left unflipped": with `EICON_V90D_BULK_ADAPTER=1` the outer state word
went `0x00c4 -> 0x78f8` within a few hundred samples of the page load, on both
`DM(0x32f7)=0` and `=8`. The conclusion was right; the reason turns out to be a
sequencing problem, and it is now fixed.

Watching `DM(0x1FF7)` (the outer state word) through the offline replay finds
the corrupting store immediately. The legitimate writer is PM `0x2fea`. The one
that writes `0x78f8` is **PM `0x26d4`, `DM(I0,M1) = SR1`, with `I0` sitting
exactly on `0x1FF7`** -- a store cursor that has walked out of its buffer.

The routine sets `I0 = 0x1DD0` at PM `0x26b1` and never sets `L0`, so I0 is
linear by design. It sets `L1 = 0x001E` at PM `0x26b9` for the *other* cursor,
so the code is DAG-aware; I0's bound is not a modulo register but the loop
count, read from `DM(0x1E4F)` at PM `0x26b5`.

`DM(0x1E4F)` has exactly one writer in the overlay, PM `0x3dee`, and it sits in
the rate-publication routine, one instruction after PM `0x3ded` writes
DATASTATESpeed to `DM(0x3F61)`. What it stores is the datagram bit count:
DATASTATESpeed is assembled as `0x2000 | 0x0020 | (AX0 - 0x15)` and the host
reads bits per datagram back as `21 + (value & 0x1f)`, so the stored `AX0` is
that width, 21..42 for V.90.

At page load that routine has not run. The word is not in any of the overlay's
DM blocks either -- it falls in the gap between `0x1e2a` and `0x1e5c` -- so it
holds whatever the previous page left. In the traced capture that was `0x6613`:
**26131 iterations**, walking I0 from `0x1DD0` up across `0x1FF7`. The watch
confirms the sequence exactly: `dm r 1e4f=6613 pc=26b6`, then the runaway loop
writing over `0x1E4F` itself and over the state word. With the adapter RTSed
out the routine is never reached and `DM(0x1E4F)` is never read or written at
all, which is precisely the archived comparison.

So the adapter is not inherently broken in this harness. It is being run before
the rate that parameterises it exists. `_service_bulk_adapter()` now holds the
same RTS at page load when the adapter is enabled and lifts it once
DATASTATESpeed is published and `DM(0x1E4F)` is a legal datagram width.
Seeding a guessed count instead does not work -- it relocates the corruption
rather than removing it, because the rest of the block is unset too.

Result on the archived replay, `usr-v92-21240/call1.rx.ulaw` to 20 s:

| | outer-state walk | ends |
|---|---|---|
| adapter off (default) | 38 changes | `0x00c2` |
| adapter on, before | collapses at 6.918 s | `0x78f8` |
| adapter on, held | 38 changes, **identical to off** | `0x00c2` |

`EICON_V90D_BULK_ADAPTER=1` is therefore no longer destructive. That is the
blocker removed, not the canceller proven: this capture never publishes a rate
(`DM(0x3F61)` stays `0x0000` for the whole 20 s), so the adapter is held for the
entire replay and never executes there.

### Released on hardware: the parameter decode is confirmed, the adapter is not

Two live CX calls with `EICON_V90D_BULK_ADAPTER=1` both released it, and both
printed the same thing:

```
[native-mips] bulk adapter released: DATASTATESpeed=0x202b, DM(0x1E4F)=32 bits/datagram
```

That confirms the reading of `DM(0x1E4F)` against hardware. `0x202b` gives
`21 + (0x2b & 0x1f) = 32` bits per datagram, and the word holds exactly 32 --
the datagram width, arriving from the rate-publication routine as predicted,
and inside the legal 21..42 range that the release gate tests.

The adapter has therefore run for the first time. It does not help; it appears
to end the call:

| | receive datagrams captured | data state |
|---|---:|---|
| adapter off, call 2 | -- | full call, 877 N_DATA accepted |
| adapter off, call 5 | 47619 | ~19 s |
| adapter on, call 6 | 696 | ~0.3 s, then `NO CARRIER` |
| adapter on, call 7 | 63 | immediate, then `NO CARRIER` |

Two calls each and the connect rate is a lottery, so this is not conclusive.
But 47619 against 696 and 63 is a large enough gap to act on: releasing the
adapter collapses the data phase almost immediately. The receive error rate
cannot be compared across these -- 696 datagrams is far too small a sample, and
the 26.7% impossible-value figure from call 6 is noise next to call 5's 23.7%
over 43692.

The default stays as it was, with the adapter RTSed out. What has changed is
that the failure is now an understood one at a known point, rather than a state
word being overwritten by a runaway cursor during page load.

### What the collapse is not, and where the descriptor actually is

The first guess was that the adapter gets a half-initialised parameter block:
PM `0x3def`, `0x3df0` and `0x3df3` write `DM(0x1E4E)`, `DM(0x1E4D)` and
`DM(0x1E50)` in the same routine as the bit count, and all four sit in the same
uninitialised gap. That is wrong, and it is wrong statically --
`0x3dee..0x3df3` is straight-line code with no branch, so `DM(0x1E4F)=32`
proves the other three were written in the same pass. The block is coherent at
release. (Worth noting separately: PM `0x3de4` is `JUMP $3DEE`, a second entry
that sets the block *without* writing DATASTATESpeed, which is why the release
gate accepts `DM(0x3F62)` as well.)

Enumerating every direct DM read on the adapter path -- PM `0x1900..0x19c8`,
the frame loop at `0x26a8..0x26da`, and one level of callees -- against the
overlay's initialised DM words gives eight reads of words the image never
sets. Seven have writers elsewhere in the overlay. One does not:

    DM(0x32F7)  read at PM 0x1900, 0x1982   writers = NONE

`0x1900` is the adapter's first instruction, `I1 = DM($32F7)`: its
control-block pointer. No overlay writes it -- `0x0261` and `0x026a` both only
read it -- and the overlay's DM image has `0x0000` there. A watchpoint confirms
the live value is `0x0000`, read at PM `0x1983`, never written.

That is not necessarily a fault. With `I1 = 0` the descriptor is `DM(0..7)`,
which the image ships as `2aca 2ad2 2ae5 2b1b 0000 0000 0000 0000` -- four
addresses followed by four zero cursors. This is the same structure the
existing `--prime-v90d-bulk-cursor` seam already assumes when it notes that "PM
0x1982 preserves the far-bulk cursor in DM4" and primes `DM(4)` from `DM(0)`;
`0x1982` is the second of the two `DM(0x32F7)` readers. So the pointer being
zero looks correct and the real gap is the unpublished cursors in `DM(4..7)`,
which a real selected channel would fill.

Note also that "disabled" has always meant *tail* disabled: the RTS sits at
`0x19C8`, so `0x1900..0x19C7` runs either way. The watchpoint above fired with
the adapter held.

Session 88's third row -- "adapter enabled + `--prime-v90d-bulk-cursor`:
stalls at `0x0068`" -- was measured with the `DM(0x1E4F)` runaway still
present and is now stale. Re-run on the same capture with the gate in place,
that combination gives 38 state changes ending `0x00c2`, **identical to the
adapter-off baseline**. All three of Session 88's offline failure modes are
gone; what remains is the live collapse.

Priming the cursor does not fix that. A third live call with both the adapter
and `--prime-v90d-bulk-cursor` primed `DM(4)` from `DM(0)=0x0201` (the runtime
value, not the image's `0x2aca`), released the adapter on the same
`DATASTATESpeed=0x202b`, and collapsed like the others:

| live call | adapter | cursor prime | receive datagrams |
|---|---|---|---:|
| 5 | off | -- | 47619 |
| 6 | on | no | 696 |
| 7 | on | no | 63 |
| 8 | on | yes | 546 |

So the unpublished cursors are not the cause either, and the echo canceller is
still not usable. What has changed across this work is where the failure lives:
it is no longer a runaway store during page load, it is something in the live
data phase, with the adapter demonstrably running on correct rate parameters.

## The receive path was working; the fallback was throwing the frames away

Everything above localised the LAPM failure to the receiver, on the strength of
a capture where no framing hypothesis produced a valid FCS. That capture was
real, but it was not representative, and the conclusion drawn from it was too
strong.

Two things had been hiding the actual fault.

**The CX was never reporting its protocol.** `AT&V` shows `W0` and `X3`, so the
CONNECT result carries the DTE speed and nothing else -- no `CARRIER`, no
`PROTOCOL` line. Every call in this work had been run without knowing what the
modem negotiated. `ATX4W2` turns that on. (`ATI6`/`ATI11`, which handoff.md
recommends for this readout, are USR Courier commands; the CX answers `OK` and
`ERROR`.)

**The modem defaults to `S48:7`, V.42 detection enabled, with `S36:7` falling
back silently to async.** The handoff's `S48=0` -- force LAPM, skip the
detection phase -- had never been tried on the CX. With `S48=0` the modem sends
no ODP at all and goes straight to XID.

That combination exposed the real bug. A capture taken under it,
`p-1`, contains **45 frames with a valid FCS** at 3 bits, MSB-first, RXD pairs
in order -- exactly the hypothesis `_service_rx_data()` already uses. The live
run of that same call reported `HDLC good/bad/abort=0/0/0`.

The difference is `LapmEndpoint.feed()`. `_enter_raw()` fires when T400 expires
without an ODP, and `feed()` then returned after `_feed_raw()` without ever
reaching the HDLC decoder. The fallback was a one-way door. A peer with
detection disabled never sends an ODP, so T400 *always* expires and its XID
and SABME arrive strictly afterwards -- into a decoder that was no longer being
fed. V.42 7.2.1.3 makes receipt of an LAPM frame the start of the protocol
phase regardless, and `feed()` already implemented that for the non-raw paths;
raw was the one that returned early.

Replaying `p-1`'s captured datagrams through the fixed endpoint enters the
protocol phase and answers 45 XID commands. Live, with the fix:

    [v42] totals: HDLC good/bad/abort=73/0/9, XID rx/tx=73/73

against `0/0/0` on every previous call in this work. So the receive path
demodulates, frames, and passes FCS. The 24% impossible-value figure from the
earlier capture was a call where the receiver genuinely did not lock; it does
not generalise, and the "misdemodulating at 24%" conclusion above should be
read as applying to that capture only.

### What is still open

LAPM does not complete. The modem sends XID 73 times and never advances to
SABME, so it is not accepting our XID response -- either the response is not
reaching it or its content is unacceptable. Its XID is 77 bytes; ours is 25.

The transmit direction is the first thing to separate. That run had
`EICON_V42_NL_DATA=1`, so the XID responses went out over the NL entity, which
is proven to be *accepted by the firmware* but has never been proven to reach
the line. Two calls with the mailbox path instead did not get far enough to
compare -- one never published a receive rate at all. That is the next
experiment, and it wants a run of calls: the connect rate is a lottery, and
rapid cycling makes it worse, with BUSY and calls that never arrive until the
line is left to settle.

## The XID/SABME stall: a dead transmit bearer and a zero conformance mask

The previous section left LAPM answering 73 XID commands and never being sent a
SABME. Two defects were found by reading the transmit path and V.42 12.2.2
against each other. Neither has been tried on hardware yet; both are things the
code was doing wrong regardless of which one the CX was reacting to.

### 1. In NL mode the line carries mark, not our XID responses

`EICON_V42_NL_DATA=1` was set on that call. In `_next_tx_words()` that branch
put the LAPM stream into the NL transmit elastic store and gave the synchronous
transmit mailbox `[1] * count` -- **mark fill**. The mailbox is the data pump's
transmit source, so whatever the NL entity did with the octets, the line
carried mark for the whole call.

The receive direction had already worked this out and written it down: the
firmware accepted all 857 N_DATA requests and returned an N_DATA *indication*
for none of them, so `_service_rx_data()` keeps decoding the mailbox until
`_nl_rx_seen`. The transmit direction had no such condition and diverted
unconditionally. So the run that reported `XID rx/tx = 73/73` transmitted 73
XID responses into an entity that has never been shown to carry anything, while
the CX heard mark and retransmitted XID every T401 for 55 seconds. 55 s / 750 ms
is 73.

`_next_tx_words()` now applies the same test as the receive path: LAPM rides
the mailbox until an N_DATA indication proves the bearer live.
`EICON_V42_NL_DATA=force` restores the unconditional diversion for anyone
testing the bearer in isolation. This makes the "separate the two transmit
paths" experiment unnecessary -- the default is now the mailbox in both
directions, which is the only combination with hardware evidence behind it.

### 2. The XID response's optional-functions mask was zero

Table 11a/V.42 Note 1: the PI=3 parameter value is a 32-bit HDLC optional
functions mask, and "the transmitter of an XID command frame shall set bit
positions 2, 4, 8, 9, 12 and 16 to 1. The transmitter of an XID response frame
shall also set these bit positions to 1, except bit position 16 shall be set to
0 if bit position 17 is set to 1."

`encode_xid_parameters()` was sending `optional_functions = 0`, and `_handle()`
explicitly rebuilt the negotiated parameters with a literal `0` in that field.
The comment there -- "no optional procedure is advertised until its complete
procedure is implemented" -- is right about bits 3, 14, 17 and 24, and those
stay clear. It was wrong to extend that to the six non-negotiable bits: bit 9
is the only statement that the sender uses extended (modulo 128) sequence
numbering and bit 16 the only statement that it uses a 16-bit FCS, which is
exactly what a responder has to agree to before a SABME is worth sending. The
mask is now `0x0000898A`, encoded low-order octet first as `8a 89 00 00`.

The Recommendation does say a receiver "should ignore these bit positions", so
this is a candidate rather than a diagnosis. It is a `shall` we were violating
either way, and our 25-byte response against the CX's 77 was the only content
difference worth acting on without a capture of the CX's XID.

### 3. Command/response addressing (found while reading 8.2.1)

Table 6/V.42 makes the C/R bit depend both on the direction and on which end
originated the call:

| | originator -> answerer | answerer -> originator |
|---|---|---|
| command | C/R = 1 | C/R = 0 |
| response | C/R = 0 | C/R = 1 |

The endpoint echoed the received address onto everything it sent and kept the
last one in `self.address`. For an answerer that is accidentally correct for
responses -- UA, the XID response, RR acknowledgements -- and wrong for every
command it originates: I frames, the RR(P) window probe and DISC all went out
at 0x03, which the originator reads as a response. An I frame arriving as a
response is a frame-rejection condition at a conformant peer, so this would
have surfaced immediately after SABME even if SABME had arrived.

`command_address`/`response_address` are now derived from the role and a
learned DLCI; `address` remains as the response address, which is what the
tests and the fallback-recovery path were already using it as. A polled
supervisory *response* (F=1) is also no longer answered with an RR(F), which
was a standing RR ping-pong between two of these endpoints.

### 4. N401 was being applied to every frame, not just I frames

`_handle()` opened with `if len(frame) > self.n401 + 3: FRMR(too_long)`. N401
bounds the information field of an I frame and nothing else; a U frame carrying
an information field -- XID, FRMR, TEST, UI -- is not subject to it. The CX's
XID is 77 octets, so any peer that negotiated N401 below about 74 would have
had its own next XID answered with FRMR and the link torn down. The check now
lives in the I-frame branch.

### What this does not settle

No hardware has seen any of it. The next call should be the plain mailbox path
(no `EICON_V42_NL_DATA`) with `S48=0` and `ATX4W2` on the CX, and it wants
`EICON_RX_TRACE` set so the CX's 77-byte XID is on disk this time: if the modem
still stops at XID, its own parameter list is the only remaining place to look,
and it has never been captured.

## Twelve live calls: the V.42 fixes are untested, because nothing reached data mode

Twelve calls against the Courier, run to test the four fixes above. **Not one
reached `0x00c6`/`0x00d0`, so none of the four was exercised.** What the run
established instead is about the physical layer, and one part of it is a
regression check this document has owed since Session 85.

The Conexant CX on `/dev/cu.usbserial-21210` is dark — silent to `AT` at 115200,
57600, 38400, 19200 and 9600. The Courier V.Everything answers on
**`/dev/cu.usbserial-21240`**, which is the reverse of what the reproduction
section said; it reports `USRobotics Courier V.Everything`, ROM `5607A`. Two
command corrections while we are here: `ATW2` is a Conexant command and the
Courier answers `ERROR`; `AT&A3` is its equivalent. And `ATDT` immediately after
a previous call produces `NO CARRIER` with **no INVITE reaching the endpoint at
all** — two calls were lost that way before a 20 s settle after registration
made it reliable.

### Outcome of every call

| tag | data source | last TrnProgress | notes |
|---|---|---|---|
| xid1 | `--tx-v42` | `0x00b0` | held 40 s |
| xid2, xid3 | `--tx-v42` | — | no INVITE; dialled too soon after the last call |
| xid4 | `--tx-v42` | `0x002a` | never left INFO |
| xid5 | `--tx-v42` | `0x00c0` | DSR; 47243 TX datagrams |
| xid6 | `--tx-v42` | `0x00b3` | the documented stall |
| xid7 | `--tx-v42` | `0x0038` | **reached data mode**, then retrained; see below |
| xid8 | `--tx-v42` | `0x002c` | never left INFO |
| xid9 | `--tx-v42` | `0x00c0` | held 40 s |
| raw-regress1 | `--tx-prbs` | `0x00b3` | |
| raw-regress2 | `--tx-prbs` | `0x00b0` | |
| raw-regress3 | `--tx-prbs` | `0x00c0` | |

### The regression check, and its answer

Ranked step 3 was "re-run a raw-mode call on port 5060 to confirm the known-good
path still reaches `0x00c6`/`0x00d0` on the current tree". It does not. Three
`--tx-prbs` calls landed on `0x00b3`, `0x00b0` and `0x00c0` — the same
distribution as the nine V.42 calls, and the same three failure states.

That is worth stating carefully. It does **not** show a regression: `0x00b3`,
`0x00b0` and `0x00c0` are exactly the outcomes Sessions 87–93 describe as the
DIL lottery, and Session 87's success was one call. But it does mean the lottery
is currently losing every draw, on both data sources, and it removes the last
reason to read a failed V.42 call as a V.42 problem. **The data source makes no
difference to how far a call gets.** Any further V.42 work is blocked behind the
DIL blocker, which is where the effort belongs.

### xid7, the one call that reached data mode

It published TX 22 / RX 7 bits per datagram and ran 2.26 s (samples
132894..150979, 5430 datagrams) before retraining back through INFO to `0x0038`.
`tools/rx_frame_search.py` scores its trace at **zero valid FCS under all 64
hypotheses** — every bit count 1..16, both orders, both RXD pair orders. The
receiver published 128 distinct words, so it was producing something, but the
link retrained immediately afterwards, which is the signature of the capture in
"The receive side is not misframed" rather than of a framing error. Do not read
this as re-opening the framing question that the `p-1` capture settled.

## The XID/SABME blocker is not in V.42 at all: the transmit datagram path

The CX is back (`/dev/cu.usbmodem123456781`; a second Courier is on
`usbserial-21210`, the first on `21240`). It reaches V.90 data mode where the
Courier mostly does not, and it settles this question.

### What is now proven about our V.42

Four things, none of which was established before, and together they exclude
V.42 as the cause.

1. **The CX's XID is captured and decoded**, from the RXD trace of a data-mode
   call. It is 25 octets, not the 77 recorded earlier:

   ```text
   03 af 82 80 0013  03 03 8a8900  05 02 0400  06 02 0400  07 01 0f  08 01 0f
   ```

   Its optional-functions value is `8a 89 00` — **the same `0x898A` derived
   from Table 11a Note 1 two sessions ago**, which is independent confirmation
   of that mask from a shipping implementation. It carries it in three octets
   where Note 1 says four (ISO/IEC 8885's "smallest number of octets needed"),
   so `XidParameters` now carries the length and a responder answers in the
   form the initiator used. Against this peer our response is byte-identical to
   its own command.
2. **Our HDLC encoder is bit-for-bit identical to the CX's transmitter.**
   Re-encoding the decoded payload with `encode_frame()` and searching the raw
   trace matches all 60 on-air frames exactly — flags, stuffing and FCS.
3. **Our receive path is clean**: 60 good FCS, 0 bad, per call, repeatedly.
4. **The CX retransmits XID on a metronomic 700 ms T401** — measured from the
   trace, gaps of 0.700 s with no variance across 60 frames in two separate
   calls — *completely unaffected* by the 60 responses we send it. It is not
   rejecting our XID. It is not receiving anything at all.

That held across every response variant tried: PL=4, PL=3 (byte-identical to
its own), and both V90D transmit bit orders (`EICON_V90D_TX_MSB_FIRST`).

### The gate defect, found and fixed

`_next_tx_words()` tested `DM(0x3FC2) >= 0x00C6` **per datagram**. That word
does not sit still above 0xC6 on an established link — it moves around the
0xC0..0xC4 neighbourhood — but the DSP transmits a datagram every time it asks
for one. So the harness was handing it mark fill inside the LAPM stream:
**22587 of 82715 datagrams, 27% of a live call's downstream bits**, measured
with the new `payload / mark fill` counters on the call-end line.

`_lapm_active` is already the pump's own latch for "reached synchronous state",
so the test now uses it, and the last published datagram width is held so a
transiently unreadable rate word cannot reopen the same hole. On the line the
effect is visible: before, a peer in raw mode saw long runs of mark broken by
bursts; after, a continuous stream.

**It did not fix V.42.** The CX still answers 60 XIDs with no SABME.

### What was actually broken: two owners answered one TX request

`EICON_TX_PATTERN=<text>` was added because `--tx-prbs` cannot test a bit path
— random in, random out. Sending `ABCDEFGH` (a 64-bit period) to a CX dialled
with `AT\N0` and capturing its DTE bytes raw gives:

```text
0a 88f484fa 88f484fa 88f484fa 88f484fa ...
```

**A constant 32-bit block**, 90% of the capture in four octet values, where the
input alternates two *different* 32-bit datagrams. No bijective transform maps
two distinct inputs onto one output, so this is not a bit-order or alignment
question. Nor is it the scrambler: the V.34/V.90 GPC (18,23), the V.34 call-side
(5,23) and the V.32 (17,23) polynomials were all tried in both directions
offline, none matches, and a self-synchronising descrambler preserves the 64-bit
period anyway.

The request rate was a distraction. The resident `0258` TIKRNL task and the
host shim were both answering `DI_control` bit 15. `_service_tx_request()` wrote
the host datagram, then the later resident-task pass put its own data into the
same mailbox before the selected modem page consumed it. Clearing bit 15 only
proved that *an* owner had answered; it did not prove that the host words were
the words consumed.

The extracted task has five relevant stores:

```text
06d0 93f05a  DM(3f05) = AR    ; AR was loaded with ffff, mark-fill TXD0
0732 93f05f  DM(3f05) = SR1   ; short internal TXD0 path
0734 93f05f  DM(3f05) = SR1   ; long internal TXD0 path
0738 93f06f  DM(3f06) = SR1   ; internal TXD1
0740 93f07f  DM(3f07) = SR1   ; internal TXD2
```

MIPS relocates that task by seven words in the live build-117-926 core, to PM
`06d7/0739/073b/073f/0747`. Suppressing only the four internal-data stores made
the peer's old four-byte constant disappear, but the live snapshots showed
`TXD0=ffff` while `TXD1` alternated correctly. The first store was the remaining
writer.

When `--tx-prbs` or `--tx-v42` selects an explicit host source, startup now
claims the mailbox by finding the exact five-opcode relative signature and
NOPing all five stores. It requires exactly one match and fails before changing
PM on an unknown build. Normal firmware-owned operation is untouched. A saved
call replay with `ABCDEFGH` versus `AAAAAAAA` then differed in 2774 of 3200
post-sync PCM samples, with 15313/15313 requests accepted in both runs: distinct
host sources now reach the modulator distinctly.

### The raw-peer harness also needed V.14 framing

There was a second, independent error in the claimed identity test. `AT\N0`
turns off V.42, but the peer's DTE is still asynchronous: its V.14 converter
expects a start bit, eight data bits low-order first, and a stop bit. The first
`EICON_TX_PATTERN` implementation supplied bare octets. After mailbox ownership
was fixed, that bare `ABCDEFGH` stream decoded as the stable seven-octet cycle
`47 a4 90 68 44 2a 19`; that was the asynchronous converter consuming apparent
framing bits, not another lossy modem transform.

`EICON_TX_PATTERN` now emits repeating 8N1 start-stop characters. The live
`ownership-fix3` call connected at `CONNECT 115200`, accepted 55043/55043 TX
requests (40355 payload, 14688 training fill), and the raw DTE capture contained
a 46268-octet uninterrupted `ABCDEFGH` repetition. Its first visible steady
bytes were:

```text
43 44 45 46 47 48 41 42 43 44 45 46 47 48 41 42 ...
 C  D  E  F  G  H  A  B  C  D  E  F  G  H  A  B
```

That is the required end-to-end result: a deterministic host bit stream put in
the synchronous mailbox is recovered unchanged at the raw peer's DTE.

**This retires an over-reading made earlier in the same session.** A `--tx-prbs`
call producing `CONNECT 42667` and garbage on the CX's terminal was taken as
proof that our transmit reaches the peer. It proves the *samples* reach it. It
does not prove our *bits* do. The original pattern test showed they did not;
the corrected identity test above is the evidence that they now do.

### Where this leaves V.42

The physical transmit-bit blocker was removed; at this point V.42 itself was
still neither confirmed nor fixed by the pattern test. The earlier
optional-functions, C/R, N401 and mailbox/N_DATA changes could now be tested on
their own terms with the plain mailbox path. `EICON_TX_PATTERN` plus a raw-mode
peer remains the V.42-free regression harness for the layer below them. The
next section records the live V.42 test that followed and supersedes this
intermediate status.

## V.42 establishes: `GI=ff` is not a length-prefixed XID group

With exclusive TX-mailbox ownership proven, a clean plain-mailbox call finally
made the CX's remaining negotiation visible. The peer was a CX93001-EIS
V0.2013 V92 on `/dev/cu.usbmodem123456781`, configured with
`ATX4W2S48=0S36=4S46=136&K0`. For the repeatable bidirectional run its V.90
upstream was capped with `AT+MS=V90,1,300,9600,300,48000`.

The first useful call (`artifacts/interop/nldata-cx/v42-mailbox5`) passed DIL,
reported `CONNECT 42667`, and received a 59-octet XID command twice:

```text
03af8280001303038a8900050204000602040007010f08010f
ff40035634344101004201034302020044020200450120460120
4702040048020400
```

`FI=82`, `GI=80`, `GL=0013` and the following parameters are the already known
V.42 core: optional functions `8a8900`, N401 128 in both directions, and k=15
in both directions. Byte `ff` is then ISO/IEC 8885's user-data group identifier.
The repository's extracted `tty_module/xid.h` names it `XIDGI_UD` and records
the crucial wire rule: this subfield has **no group-length field**; its contents
continue to the frame's FCS. Here those contents are V.44 TLVs (`40 03 "V44"`,
then `41 01 00` declining compression, followed by capability values).

`parse_xid_parameters()` treated every group alike. At `ff` it consumed `40 03`
as a 16-bit group length of `0x4003`, found that impossible, and returned
`None`, discarding the V.42 group that had already parsed successfully. The
caller consequently constructed its fallback XID with a four-octet optional
mask instead of answering the initiator's three-octet encoding. That is why the
CX retransmitted XID and never sent SABME after the transmit path was repaired.

The parser now stops structured group parsing on `GI=ff`, retaining the valid
V.42 parameters and ignoring unsupported user-data protocols. Two captured-XID
tests pin both parsing and the exact response. The response sent on the next
live call was:

```text
03af8280001303038a8900050204000602040007010f08010f
```

The CX accepted it immediately, sent SABME (`03 7f`), and received UA
(`03 73`). It then sent the 18-byte DTE string `cx-to-eicon-v42\r\n` in an I
frame, which the endpoint accepted and acknowledged. The call ended with
`XID rx/tx=2/2`, `SABME rx=1`, `I rx=3`, and 24 undrained DTE bytes (the test
string plus the serial helper's later escape/hangup bytes). This proved
establishment and the peer-to-endpoint data path.

### Bidirectional proof

`artifacts/interop/nldata-cx/v42-mailbox8` repeated the call with `--v42-pty`.
The PTY helper waited until it had received `cx-to-eicon-v42\r\n`, then wrote
`eicon-to-cx-v42\r\n`; waiting matters because T400 raw fallback occurs before
an `S48=0` peer's first XID and pre-establishment PTY input would otherwise be
consumed by that temporary raw path.

The outbound frame was logged as:

```text
[v42] TX I N(S)=0 N(R)=1 17B:
0100026569636f6e2d746f2d63782d7634320d0a
```

The answerer command address is `01`, not the response address `03`, confirming
the earlier C/R fix on the live wire. The CX's DTE capture is exactly 17 octets:

```text
65 69 63 6f 6e 2d 74 6f 2d 63 78 2d 76 34 32 0d 0a
 e  i  c  o  n  -  t  o  -  c  x  -  v  4  2 CR LF
```

The CX acknowledged after three retransmissions; final state was `unacked=0`,
with `HDLC good/bad/abort=46/0/21`, `XID rx/tx=1/1`, `SABME rx=1`, `I rx=3`,
and `I tx/retx=1/3`. There were zero over-budget media ticks and zero catch-up
deferrals. Thus a datagram placed into the repaired transmit mailbox is now not
only recovered by a raw peer: LAPM establishes, carries exact application data
in both directions, and acknowledges the transmitted I frame.

The physical connect remains intermittent; several surrounding attempts
stopped below the V.42 boundary with only mark fill. Those failures produced no
HDLC frames and do not qualify the successful protocol result. Capping the CX's
V.90 upstream at 9600 made the useful calls more repeatable.

The LAPM suite is now 42 tests and the complete Python suite is 184 tests. The
ADSP core test also passes. Compression was deliberately disabled (`S46=136`),
and a large-window throughput soak is still future coverage; basic negotiated
V.42 establishment and bidirectional data transfer are closed.

## V.42bis live interop: Annex A negotiation and compressed data both ways

The Python LAPM endpoint now has an opt-in V.42bis implementation selected by
`--tx-v42bis` together with `--tx-v42`. It implements the Annex A private XID
group (`GI=f0`, parameter-set identifier `V42`, P0/P1/P2), transparent escape
handling, LSB-first packed codewords, STEPUP, FLUSH alignment, and leaf-node
dictionary recovery. The default remains uncompressed.

The CX93001-EIS V0.2013 V92 on `/dev/cu.usbmodem123456781` was configured as:

```text
ATX4W2S48=0S36=4S46=138&K0
AT+DS44=0
AT+DS=3,0,2048,32
AT+DR=1
AT+MS=V90,1,300,9600,300,48000
```

`+DS44=0` matters: the CX otherwise advertises and selects V.44 independently
of its V.42bis `+DS` settings. The first two calls stopped below LAPM with
`NO CARRIER`, zero valid HDLC frames and mark fill only. The third call passed
the physical training lottery and reported:

```text
+DR: V42B
CONNECT 42667
```

The first received frame was this XID command:

```text
03af8280001303038a8900050204000602040007010f08010f
f0000f000356343201010302020200030120
```

The `f0` group decodes as P0=3 (both directions), P1=512 codewords and P2=32
octets. The endpoint selected and returned those values. This is the first live
evidence that the Annex A encoding is accepted by the peer, rather than merely
round-tripping between two local codec instances.

The CX DTE then sent 524 application octets:
`cx-v42bis-`, 512 `A` octets and CR/LF. The receive trace contains six I frames
whose information fields total 118 octets; the V.42bis decoder recovered the
524-byte application payload exactly on the endpoint PTY. Thus the peer really
entered compressed mode—the result is not a transparent V.42 transfer with a
compression-capable XID.

The endpoint PTY sent the reverse 527-byte payload: `eicon-v42bis-`, 512 `B`
octets and CR/LF. Its encoder reduced that to one 79-octet I-frame information
field. `artifacts/interop/nldata-cx/v42bis-mailbox1.dte` contains exactly the
original 527 octets, and the CX's RR eventually released the frame. Final endpoint totals were 55
good frames, one bad FCS, 11 aborts, one XID each way, one SABME, six received
I frames, one transmitted I frame plus three retransmissions, and
`unacked=0`. There were no media ticks over budget. The capture is
`artifacts/interop/nldata-cx/v42bis-mailbox1`.

The focused V.42/V.42bis suite is 55 tests and the complete Python suite is
197 tests. V.42bis negotiation, compression, decompression and bidirectional
hardware interoperability are now confirmed; a long random/repetitive soak
and codeword-width step-up beyond the peer's negotiated 512-entry dictionary
remain future coverage.

## V.44 live interop: XID user data and overlapping string extensions

The host LAPM endpoint now also supports opt-in V.44 with `--tx-v44` and
`--tx-v42`. V.44 and V.42bis are mutually exclusive in both the CLI and XID.
The implementation follows the stream method: it begins in compressed mode,
packs prefixes and numeric codes least-significant bit first, implements ETM,
FLUSH, STEPUP and REINIT, and enforces negotiated codeword, maximum-string and
history limits separately in the two directions. Its encoder uses the
conforming append-only subset, creating one-character string segments and
reusing complete codewords. Its decoder also accepts variable-length string
extensions from a peer.

The XID parser now consumes the CX's V.44 TLVs in the unlengthened `GI=ff`
user-data subfield instead of merely preserving the preceding V.42 group. The
CX93001 offer used in the live call was:

```text
03af8280001303038a8900050204000602040007010f08010f
ff40035634344101034201034302020044020200450120460120
4702040048020400
```

This names `V44`, carries capability `03`, requests both directions, and
proposes P1=512 codewords, P2=32 characters and P3=1024 history characters in
each direction. Because P0 is relative to the sender of each XID, the responder
complements its direction bits. Parameter limits are cross-paired (local TX
with peer RX, local RX with peer TX) and the smaller valid value is selected.

The first call to reach `+DR: V44` found a real decoder defect. The peer's first
I-frame information field was:

```text
c6f05aec68685a8217316632994c2693c96432994c267311a106
```

It begins `cx-v44-`, creates C1 as the string `AA`, then extends that string by
30 `A` characters. The source starts with only one character beyond the
represented string; source and destination deliberately overlap, so each
character copied into history is available as the source of a later character
in the same extension. The decoder had required the complete source range to
pre-exist and raised C-ERROR. It now copies overlapping extensions one
character at a time. A regression test feeds this exact CX byte stream across
the same two I-frame boundary and requires the complete 521-byte output.

The post-fix `v44-mailbox2` call reported:

```text
+DR: V44
CONNECT 42667
CX -> Eicon: payload=True
Eicon -> CX: 524 DTE bytes; payload=True
```

The CX's `cx-v44-` + 512 `A` + CR/LF payload is 521 application octets and
occupied 36 compressed I-frame information octets. It appeared exactly on the
endpoint PTY. The endpoint then encoded `eicon-v44-` + 512 `B` + CR/LF from 524
application octets into 53 compressed octets; the CX DTE recovered the exact
524-byte payload, saved as `artifacts/interop/nldata-cx/v44-mailbox2.dte`.
Final totals were 55 good frames, one bad FCS, six aborts, one XID each way, one
SABME, six received I frames, one transmitted I frame plus three
retransmissions, and `unacked=0`.

Several surrounding calls, including the first post-fix redial, ended with
`NO CARRIER` below XID. They neither confirm nor falsify compression and remain
the same intermittent physical-training lottery seen in the V.42 and V.42bis
runs. The successful call establishes V.44 negotiation, decompression and
compression against independent hardware in both directions. Twelve focused
V.44 tests bring the complete Python suite to 209 tests.

## Session 105: restore the native V.34 echo bulk-delay call

The emulator was bypassing shared echo code that V.34 needs. Both Build
117-926 V.34 (`0x0261`) and V90D (`0x026a`) contain the identical worker at PM
`0x1900..0x19c8`; the shipped word at PM `0x19c8` is `0x19900f`, `JUMP $1900`.
The page-load shim replaced it with `0x0a000f`, `RTS`, for both overlays even
though the switch and release policy were developed for V90D.

The V.34 call contract is fully native:

```text
19d5: CALL (I4)       Core8kRoutine
19d7: CALL $19A7      bulk setup/service wrapper
19a8..19ab            gate on DM(3FC1) bit 0400
19b8..19b9            load Nearbulklength/BulkLength
19c6: CALL $1982      rebuild descriptor when lengths change
19c8: JUMP $1900      tail-call the bulk worker
```

V90D reaches the same PM `0x19a7` wrapper from PM `0x1a24`. The correct
emulator boundary is therefore the page handoff; Python must not call PM
`0x1900` directly or invent another cadence.

### The Session 93 ambiguity is closed

`--release-bulk-immediately` and `--bulk-dm5` were added to
`tools/v90_dpcm_vector_trace.py` for a short instruction-level A/B. At both
ambiguous load sites the live DAG state is conclusive:

```text
PM 1917: I1=0005 before AY0 = DM(I1,M2)
PM 1921: I1=0005 before AY0 = DM(I1,M2)
```

`AY0` comes from descriptor offset 5, not offset 6. PM `0x1982` writes offsets
`0,2,3,4,6,7` and deliberately retains offset 5. Both V.34 and V90D download
words `0..4` and `8..12`, leaving `5..7` sparse. The preceding INFO overlay,
however, executes PM `0x3734..0x3738`, a 0x400-word clear starting at DM zero,
so the reconstructed page transition leaves the retained word as zero.

Zero is destructive. With the original PM `0x19c8` live, PM `0x1922/0x1923`
compares its candidate address with that word and adds `BulkLength` on unsigned
underflow. A zero lower limit never underflows for a 16-bit address, so PM
`0x1930` walks into unrelated overlay state—the broad sweep and V.34
`DM(0x2165)` abort from Session 101.

The zero-based delay area requires the word immediately below DM zero,
`0xffff`. Publishing it before page resume changes the PM `0x1930` destination
from the broad sweep (for example `0x1596`) to the bounded zero-based area (the
first traced destination was `0x0001`). An eight-second archived-capture replay
with the shipped worker live then produced the clean walk:

```text
0050 0052 0053 0060 0062 0064 0066 0068 006a
0070 0072 0074 0076 0078 007a
```

The implementation now publishes `0xffff` at descriptor offset 5, following
the firmware selector as `(DM(0x32f7) + 5) & 0x3fff`, immediately after a V.34
overlay load and before resuming PM `0x06df`. V.34 is no longer included in
the V90D PM-`0x19c8` diagnostic patch, so PM `0x19d7 -> 0x19a7 -> 0x1900` runs
under the firmware's own enable and length gates. Leaving V90D clears any stale
page-14 hold state so its saved opcode cannot leak into another overlay.

A default 15-second native loopback loaded V.34 on both ends and printed the
new publication at `DM(0x0005)`. The answerer stayed at
`TrnProgress 0x0071 -> 0x0072`; the caller stayed in its known
`0x0060 <-> 0x0062` loop for the remainder of the run. Neither took the former
40 ms abort or returned to INFO because of bulk-worker corruption. The caller
loop is still explained by INFO word 0 decoding as `0x2000` (Sessions 102-104),
and loopback page 8 still misses real time, so this is memory-safety/call-path
verification rather than a V.34 connection result. A hardware V.34 call remains
required.

V90D is intentionally unchanged: its copy of the worker remains controlled by
`EICON_V90D_BULK_ADAPTER` and held behind the datagram-rate publication. The
V.34 result must not be generalized into a claim that the live V90D
data-phase collapse is fixed.

## Session 106: extend the retained-bound repair to V90D and verify hardware upstream

Session 105 established that the native PM `0x1900..0x19c8` worker needs the
retained lower-limit word at descriptor offset 5. V90D (`0x026a`) carries the
same worker and arrives through the same INFO clear, so its page handoff now
publishes `0xffff` at `(DM(0x32f7) + 5) & 0x3fff` as well. The V90D worker is
enabled by default; `EICON_V90D_BULK_ADAPTER=0` remains an explicit diagnostic
escape hatch.

The existing rate gate remains important. At page entry the shim holds the
worker's PM `0x19c8` tail jump as `RTS` until both a nonzero datagram rate
(`DM(0x3f61)` or `DM(0x3f62)`) and a valid V90D count (`DM(0x1e4f)` in
`21..42`) have appeared. It only releases while overlay `0x026a` is resident,
and clears the saved hold state when that page is left. This prevents stale
page state and prevents the native worker from running against an incomplete
rate block.

Archived replay covered both sides of the gate. A no-rate replay completed the
expected training-state walk through `0x007a` while the worker remained held.
The rate-bearing `v42-mailbox5.rx.ulaw` replay released at
`DATASTATESpeed=0x202b`, `DM(0x1e4f)=32`; an instruction trace showed the real
PM `0x1930` store use `I0=0x0001`, bounded inside the zero-based delay area.
PM `0x1930` executed once in the traced interval, and the V90D worker did not
overwrite the rate word.

The decisive test was a live Conexant-to-emulator V.90 hardware call with
`AT+MS=V90,1,300,9600,300,48000`. It reported `CONNECT 42667`, published
`DM5=ffff`, released the worker at `DATASTATESpeed=0x202b` and
`DM(0x1e4f)=32`, and reached V.90/V.34 synchronous data mode with 32 TX and 3
RX bits per datagram. The call stayed up for 67.24 seconds instead of the old
roughly 0.3-second data-phase collapse. The payload `cx-to-eicon-v42` arrived
at the emulator and `eicon-to-cx-v42` arrived at the Conexant, proving both
directions through LAPM. Final accounting was 82,010/82,010 accepted/requested
TX datagrams, 67,330 payload datagrams, and V.42 `I rx=3`, `I tx/retx=1/4`,
with no out-of-sequence or undrained bytes. The capture prefix is
`artifacts/interop/nldata-cx/v90-bulk-dm5-live1`.

A second call omitted `EICON_V90D_BULK_ADAPTER` to exercise the new default.
It loaded the same enabled-and-held path, but never published either rate word
and ended `NO CARRIER`; the endpoint consequently emitted mark fill only and
the worker was never released. This is a pre-data training/DIL miss, not a
repeat of the repaired data-phase collapse. Its capture prefix is
`artifacts/interop/nldata-cx/v90-bulk-dm5-live2`.

The hardware result answers the original boundary: the retained-bound repair
does fix V.34 upstream while operating in V.90 for the tested Conexant call.
It does not eliminate the separate V.90 training/connect lottery, but once a
rate is published the native worker is bounded, the call remains up, and user
payload crosses upstream and downstream.

## Session 107: measure both rates and sweep the first hardware matrix

Session 106's `CONNECT 42667` only named the PCM downstream rate. The ADDSP
V.90 guide supplies the missing digital-side measurement: read-database offset
`0x81` is `DATASTATEspeedTx`, the modem transmitter's selected speed, and
offset `0x82` is `DATASTATESpeed`, the modem receiver's selected speed. On the
digital V90D endpoint those are downstream and V.34 upstream respectively.
The database starts at DM `0x3f60`, so the live words are `DM(0x3f61)` and
`DM(0x3f62)`.

The successful Session 106 capture published `202b/11e9`. V90D index 11 is 32
bits per 8000/6-Hz datagram, or 42,667 bit/s downstream; V.34 speed index 9 is
7,200 bit/s upstream. The previously proven bidirectional LAPM call was
therefore **42,667 downstream / 7,200 upstream**, not 9,600 upstream merely
because the Conexant command capped its transmitter at that value.

The emulator now decodes and latches both read-database words as they appear,
because the firmware can replace them before the synchronous-state callback.
It reports the pair at V.42 entry, AT `CONNECT`, and call teardown. A new
`tools/cx_v90_rate_probe.py` makes the Conexant's six `+MS` rate fields
explicit: its TX range is upstream and its RX range is downstream.

The first live sweep produced these usable points. `BUSY` calls are PBX
failures and are excluded from modem conclusions.

| Conexant request | bulk worker | ADDSP-selected rates | result |
|---|---:|---:|---|
| upstream <= 9,600; downstream <= 48,000 | live | 42,667 / 7,200 | reached `0x00c8`, then `NO CARRIER`; no XID/SABME |
| upstream <= 9,600; downstream <= 40,000 | live | 40,000 / 7,200 | briefly reached `0x00cc`, then went offline; no LAPM |
| upstream <= 24,000; downstream <= 48,000 | live | 41,333 / 7,200 | PM `0x1930` swept through unrelated DM immediately after release |
| upstream exactly 9,600; downstream <= 48,000 | bypassed | transient 42,667 / 9,600 | PM `0x3180` published 9,600, then PM `0x31d5` replaced it with index 0 before sync; `NO CARRIER` |
| upstream <= 24,000; downstream <= 48,000 | bypassed | 42,667 / 7,200 | `CONNECT 42667`, XID/SABME, and two upstream LAPM I frames |
| upstream <= 24,000; downstream <= 32,000 | bypassed | none | PBX `BUSY`; excluded |

This narrows Session 106's worker conclusion. The 32-bit downstream case has a
real stable hardware proof, but legal width 31 is now proven unsafe. In the
41,333/7,200 call the first corrupt writes were all the native PM `0x1930`
store: `DM(0x3f61)=fac1`, `DM(0x3f62)=053f`, and `DM(0x3fb0)=fbc1`, with
`I0` equal to each victim address. Publishing descriptor offset 5 as `ffff`
is therefore necessary but not sufficient for every V90D rate. Widths other
than 31 and 32 remain unqualified; the current `21..42` release gate is not a
general safety proof.

The exact-9,600 bypass gives an independent upstream blocker. PM `0x3180`
repeatedly wrote `DM(0x3f62)=11ea`, which is a valid 9,600-bit/s selection.
Immediately before data state PM `0x31d5` wrote `11e0`, erasing the speed index.
Thus the physical negotiation can select 9,600 upstream, but the final rate
handoff discards it. Conversely, the successful bypassed 42,667/7,200 call
received XID, SABME, and two upstream I frames, proving the 7,200-bit/s
upstream transport itself works.

There are now three distinct boundaries rather than one vague upstream
failure: the pre-rate DIL/training lottery; PM `0x31d5` clearing a selected
9,600 upstream rate before sync; and the V90D bulk worker's rate-dependent
out-of-bounds sweep at 31 downstream bits. Further rate sweeps must keep
`EICON_V90D_BULK_ADAPTER=0` until each datagram width has been independently
qualified, otherwise a worker fault can masquerade as a negotiation result.

## Session 108: fail closed on unqualified V90D bulk widths

The Session 106 release gate treated every legal V.90 datagram width as safe,
but Session 107 proved that legality is not a worker-safety invariant: width 31
corrupts DM while width 32 has the only stable hardware proof. The gate now
requires three matching facts before restoring PM `0x19c8`: read-DB `0x81`
(`DM(0x3f61)`) must be a V.90 downstream speed word, its encoded width must
equal `DM(0x1e4f)`, and that width must be on the explicit qualified allowlist.
The allowlist currently contains only 32.

Read-DB `0x82` (`DM(0x3f62)`) is no longer accepted as a fallback release
signal. It describes the analogue V.34 upstream and cannot establish that the
PCM-downstream worker parameters are coherent. Width 31 and every other
unqualified width consequently remain behind the RTS hold instead of allowing
PM `0x1930` to sweep into the rate/state block. This is a memory-safety fix,
not a claim that the native worker has been repaired for those widths; each can
be added only after an independent hardware proof.

## Session 109: preserve an exact upstream selection through the quality handoff

Disassembly corrects Session 107's description of PM `0x31d5`. It is not an
unconditional erasure of a negotiated rate. PM `0x316a..0x3172` intersects the
peer's V.34 rate mask (`DM(0x1e3f)`), the local mask (`DM(0x210b)`), and a
quality-derived ceiling constructed from `DM(0x20ba)`. If that intersection is
empty, PM `0x31d1..0x31d5` deliberately publishes the no-common-rate setup:
`DM(0x3f9b)=0`, `DM(0x204e)=3`, and `DM(0x3f62)=0x11e0`.

The exact-9,600 archive shows why the handoff can nevertheless be too strict.
At sample 146302, PM `0x3180` selects `0x11ea` with peer mask `0x0008`, local
mask `0x1ffe`, ceiling 8, and smoothed quality `0x0069`. At the final handoff,
the same exact peer/local masks remain but the ceiling has transiently fallen
to 3 while the smoothed quality is `0x02cf`; `(1 << 3) - 1` excludes the sole
bit `0x0008`, so the no-common-rate branch is correct for its instantaneous
inputs. A broad-rate archive has the same final ceiling and quality
(`0x02e1`) and therefore selects 7,200 from its lower offered bits.

The shim now retains the complete setup from a genuine earlier selection:
the encoded rate word plus `DM(0x3f9b)` and `DM(0x204e)`. It restores those
three words only when all of these conditions hold: the peer mask contains
exactly that one rate, the local mask still permits it, the firmware has
published `0x11e0`, and the final ceiling excludes the selected bit. Broad
rate negotiations and selections already inside the ceiling are untouched.
`EICON_V90D_PRESERVE_EXACT_UPSTREAM=0` disables the guard for A/B testing.

With the bulk worker bypassed, replay of the exact-9,600 archive now preserves
`0x11ea/3/9` and continues through outer states `0x00c6`, `0x00c8`, `0x00ca`,
`0x00cc`, and `0x00d0` instead of publishing no rate. Because replay is open
loop, that establishes the local handoff and state progression only; it cannot
prove that the peer accepts the retained rate. The Python regression suite is
225 tests. `tools/cx_v90_rate_probe.py --endpoint-pty ...` now treats the live
test as one bilateral assertion: it waits for the Conexant payload at the V.42
PTY, injects a distinct reverse payload there, and requires that payload at the
Conexant DTE before collecting `ATI6`/`ATI11` and hanging up. A live
exact-12,000 call remains required: the earlier
`v90-exact-u12000-d48000-b1` structured snapshots contain no valid
`DM(0x3f62)=0x11eb` publication before their unrelated state corruption, so
they cannot supply that proof. The upstream-above-9,600 goal remains open.

## Session 110: native V90D selects and preserves an exact 12,000 upstream rate

The Conexant accepts the exact request
`AT+MS=V90,1,12000,12000,300,48000`. Its sole upstream capability bit is
`DM(0x1e3f)=0x0010`; the local mask remains `DM(0x210b)=0x1ffe`. The final
firmware quality limit still falls to 3, which excludes that bit, so the
exact-offer guard now raises the native mask length to 5 before the final
selection rather than restoring a rate only after the firmware rejects it.

A live call then made the complete native selection:

```text
DM(0x3f62) = 11eb       V.34 speed index 11 = 12,000 bit/s upstream
DM(0x3f9b) = 0004       selected capability bit number
DM(0x204e) = 000c       rate-derived setup parameter
DM(0x3f61) = 202b       42,667 bit/s PCM downstream
```

It advanced through synchronous states `0x00c8/0x00ca/0x00cc/0x00d0` with the
42,667/12,000 pair intact. This proves that the physical negotiation and
firmware handoff can exceed 9,600 upstream. It is not yet the goal's terminal
proof: with the native bulk worker held, the Courier never emitted `CONNECT`,
and a clean raw mailbox run contained no valid HDLC frames (98 bad candidates,
1,907 aborts). Those bytes are still Phase-4/retrain traffic rather than a
bilateral LAPM data stream.

The exact-12,000 native-worker release supplied the missing safety
counterexample. Width 32 is not generally safe: shortly after release it
destroyed unrelated DM even though the rate/count block and retained lower
limit were coherent. The qualified-width set is therefore empty by default.
`EICON_V90D_QUALIFIED_BULK_WIDTHS` exists only to reproduce an archived suspect
width under instruction tracing.

## Session 111: replace the unsafe V90D worker with its bounded database contract

An instruction replay of the exact-12,000 capture localized the visible
destructive writes more precisely than the earlier PM-`0x1930` watches. The
V90D adaptive update at PM `0x1b64..0x1b6a` walks `I4` upward in four-word
steps; PM `0x1b69` and `0x1b6a` eventually overwrite `DM(0x3f62)`,
`DM(0x1ff7)`, and the rest of the page state. The first observed coefficient
window begins near `DM(0x2a04)`, but after native bulk release the pointer is
no longer bounded there. NOPing those stores would disable adaptation and is
not a repair.

The ADDSP guide provides a smaller, explicit boundary that does not require
emulating this corrupted internal workspace. At 8 kHz the page publishes:

```text
DM 3fbc/3fbd   Nearbulklength / BulkLength, in X/Y sample pairs
DM 3fbe/3fbf   BulkInputX / BulkInputY
DM 3fb6/3fb7   near-delayed X / Y outputs
DM 3fb8/3fb9   oldest (far) X / Y outputs
```

The normal `0x03cd/0x041d` lengths are 973/1053 samples, about 122/132 ms at
8 kHz, consistent with the card's echo-tail timing. `PortableBulkDelay` now
implements exactly this ABI with a bounded deque of X/Y pairs. It starts under
the firmware's existing `DM(0x3fc1)&0x0400` enable bit, clears on invalid or
changed lengths, and rejects zero, reversed, signed, or larger-than-ADSP
descriptors. PM `0x19c8` remains `RTS`, so no datagram width can re-enter the
unsafe native worker. `EICON_V90D_PORTABLE_BULK=0` retains the held path for
diagnosis.

Twenty focused bulk/rate tests and the full 229-test Python suite pass. A live
exact-12,000 call must still show Courier `CONNECT`, sustained LAPM, exact
payload in both directions, and no watched-state corruption before this can be
called hardware-verified.

## Session 112: the bulk delay lengths are zero, because the seeder runs before its input

Sessions 105–111 repaired the delay line's *bounds* — the retained `0xffff`
lower limit, the qualified-width gate, the bounded `PortableBulkDelay`. None of
them ever gave it a *length*. `DM(0x3fbc)` (`Nearbulklength`) and `DM(0x3fbd)`
(`BulkLength`) read `0x0000` on every one of the 114,621 page-14 frames of
`v90-bulk-dm5-live1`, and identically on `v90-exact-u12000-d42667-live1`.

`PortableBulkDelay` was therefore correct and inert: 114,621 services, 114,621
rejections of an invalid descriptor, `DM(0x3fb6..0x3fb9)` pinned to zero. It had
never once run. The echo canceller has had no reference signal at all.

### The seeder, and why it misses

PM `0x3232..0x3243` with its tail at PM `0x1085/0x1086` is the only site that
turns a measured delay into delay-line lengths:

```text
3232: AX0 = DM(0x3F04)      delaycorrection, write-DB +0x24, 0x000c as shipped
3233: AY0 = 0x0025
3234: AR  = AX0 + AY0
3235: DM(0x3FBC) = AR       Nearbulklength = 0x25 + delaycorrection
3236: AR = DM(0x3FCB)
3238: IF LE JUMP 0x323C     skip when no round trip has been measured yet
323a: AR = AR + DM(0x3FBC)
323b: DM(0x3FBC) = AR       Nearbulklength += DM(0x3FCB)
323c: DM(0x0A5D) = min(Nearbulklength + 0x50, 0x0B00)
3243 -> 1086: DM(0x3FBD) = DM(0x0A5D)     BulkLength
```

`DM(0x3fcb)` is the measured round trip, `DM(0x3fc9) * 10/3` from PM `0x2cb4`.
Per-frame coverage puts the timing beyond doubt:

| sample | page | event |
|---|---|---|
| 31659 | 0x0260 | PM `0x3235` fires, `DM(0x3fcb)=0x0000` |
| 33455 | 0x0260 | PM `0x3235` fires, `DM(0x3fcb)=0x0000` |
| 45379 | 0x026a | page-14 entry; `DM(0x3fcb)=0x01a6` and stable for the residency |

Both firings land about 1.5 s before the measurement exists, so the `IF LE`
branch is taken both times and the seed is the bare `0x31` floor with no echo
delay in it. PM `0x1085/0x1086`, the only writer of `BulkLength`, executes zero
times in the whole run. Nothing re-seeds afterwards. The measurement is then
available and correct for the entire page-14 residency and is never used.

### This reframes Sessions 90–93 and 101

PM `0x1930`'s modulo bound is zero because **`BulkLength` is zero**, not because
descriptor offset 5 was missing. The Session 105 `0xffff` repair bounds the
pointer and genuinely stopped the memory corruption, but it treated a symptom.
The same applies to the width qualification: `DM(0x1e4f)` is the V.90 datagram
bit width and has nothing to do with the delay line. Sessions 107–110 qualified
and then disqualified widths 31 and 32 against it; width was never the variable,
because every release was against a zero-length delay line.

It is also not V90D-specific. The seed is computed on page `0x0260`, upstream of
the V.34/V90D fork, so V.34 (`0x0261`) inherits the same zero-length line while
keeping the native worker live. That is the shape of the Session 101 collapse.

### The repair

`bulk_delay_seed()` recomputes the firmware arithmetic, and
`_service_bulk_lengths()` publishes it once `DM(0x3fcb)` is positive, for both
`0x0261` and `0x026a`. It holds rather than writes once: PM `0x19e2/0x19e4`
restore both words from the saved context at `DM(0x3608)/DM(0x3609)` at the top
of every frame and PM `0x1a13/0x1a18` write them back one `0x20` decrement low
at the bottom, so a single write survives one frame and an alternating value
would flush the ring every frame. Publishing the same pair into both the live
words and the saved context keeps the firmware's own ping-pong intact. If the
firmware ever publishes lengths of its own that are neither the seed nor the
seed less one decrement, the hold stands down and the firmware's value wins.

`EICON_BULK_DELAY_SEED=0` restores the old behaviour for A/B.
`EICON_BULK_DELAY_EXTRA_PAIRS` adds sample pairs on top, for tuning the SIP
leg's packetisation and jitter-buffer delay if the card's own measurement turns
out not to include it; default 0, 8 pairs is 1 ms.

Offline, `v90-bulk-dm5-live1` now seeds 471/551 pairs (58.9/68.9 ms) and
`PortableBulkDelay` services all 114,621 page-14 frames with zero flushes,
against 114,621 rejections before. Nine new tests; the suite is 238 and passes.

### Hardware verification is blocked by a separate, older failure

Six live Conexant calls were placed. The seed works live and tracks the measured
round trip per call — 471/551, 507/587, 581/661 and 625/705 pairs across
attempts, 58.9 to 78.1 ms near — and the bounded delay reported active on
hardware for the first time.

No call reached data mode. All six stalled at `TrnProgress 0x0050` immediately
after page-14 entry, published neither rate word, and ended `NO CARRIER` with
`TX datagrams 0/0`. **This reproduces with `EICON_BULK_DELAY_SEED=0`**, and also
under Session 106's own successful configuration
(`EICON_V90D_PORTABLE_BULK=0 EICON_V90D_QUALIFIED_BULK_WIDTHS=32`), so it is not
caused by this change. Broad-rate and exact-12,000 requests behave identically.

Replay cannot arbitrate: replaying today's captures *and* the archived
`v90-bulk-dm5-live1` both stop at `0x0050`, because replay is open loop and the
recorded peer stream past that point was produced against a card that was
answering. The last call to pass `0x0050` live was Session 106's.

So the rate-ceiling claim — that a working echo canceller lifts `DM(0x20ba)` and
stops the ceiling collapsing to 3 — remains **unproven on hardware**. It is
supported by the mechanism and by `DM(0x20ba)` reading `0x088d` on pages
`0x025f`/`0x0260` and `0x0000` for all of page `0x026a`, and by nothing else.
Finding out why nothing gets past `0x0050` any more is now the blocker in front
of it, and it is independent of the echo canceller.

## Session 113: the 0x0050 stall was a dispatch vector, and the bulk delay does not cap upstream

### The stall

Every Session 112 hardware call died at `TrnProgress 0x0050` with `TX datagrams
0/0`. It bisects offline: replaying `v90-bulk-dm5-live1` — the capture that
connected in Session 106 — reproduces it exactly, 114,621 page-14 frames at
outer state `0x0050`. Two flags cleared it, `EICON_V90D_BULK_ADAPTER=0` and
`EICON_V90D_PORTABLE_BULK=0`, and the only thing they have in common is that
both stop `PortableBulkDelay` servicing.

`PortableBulkDelay` published the near/far outputs at DM `0x3fb6..0x3fb9`.
DM `0x3fb8` is not an output:

```text
19f3: 8bfb80  I4 = DM($3FB8)
19f4: 0b001f  CALL (I4)
```

The firmware holds `0x3cea` there, and `0x3cea` sets the DM `0x3fc1` `0x0400`
worker-enable bit and jumps to the generator dispatch at `0x2a56`. Writing a
delay sample over it called the page into garbage every frame, which is why the
generator went quiet and nothing was ever transmitted.

The database base is DM `0x3ee0` for every offset. That is the only base
consistent with the mappings already proved — write-DB `0x24` is
`delaycorrection` at DM `0x3f04`, read-DB `0x81/0x82` are the rate words, and
`0xdc..0xdf` are the lengths and inputs at DM `0x3fbc..0x3fbf`. Session 111 used
`0x3f60` for the `0x56` group alone. The near and far output pairs are therefore
DM `0x3f36..0x3f39`, and PM `0x19e7/0x19e8` (`DM(0x3F36) = DM(0x3F38)`)
context-switch that pair exactly as PM `0x19e2/0x19e4` do the lengths.

With that corrected, replay walks `0050 0052 0053 0060 0062 0064 0066 0068 006a
0070 0072 0074 0076 0078 007a 007b 007c 0080 00a6 00b0` with the delay enabled,
matching the disabled path. Ten live Conexant calls then produced four
`CONNECT 42667`s at `TrnProgress 0x00d0` with CTS/DSR/DCD and exact bilateral
payload, against nought from six before. The remaining six failed in the two
documented ways — the `0x0060 ↔ 0x0062` INFO loop and pre-data DIL misses — so
the lottery is back, but it is a lottery again rather than a certainty.

### The real echo delay, measured

`tools/echo_delay.py` cross-correlates the captured TX against the captured RX,
which measures the live path's echo directly. Every capture puts the peak at
41–100 sample pairs, 5.1–12.5 ms, standing about 35× clear of the noise floor.
`DM(0x3fcb)` reaches 490–540 pairs, 61–68 ms — an order out. That fits what
`v90_dpcm_replay.py` already documents about its source: `DM(0x3fc9)`, which
`DM(0x3fcb)` is 10/3 of, is an elapsed-time counter the INFO page maintains at
PM `0x3caf/0x3cb4`. The bare floor PM `0x3232` computes before the addend,
`0x25 + delaycorrection` = 49 pairs = 6.1 ms near and 129 = 16.1 ms far,
brackets every measurement. The addend is now opt-in behind
`EICON_BULK_DELAY_MEASURED=1`.

The Session 112 stand-down guard was also wrong: it fired on an incoherent
`near=17 far=0` transient in the second frame of every call and handed the delay
line straight back to zero. A candidate now needs `0 < near <= far` and twelve
consecutive frames, after which the firmware's own genuine publication of
439/519 pairs is what ends the hold.

### The bulk delay is not what caps V.34 upstream

Three configurations were run to `0x00d0` and their final handoff compared:

| bulk delay through the data phase | `DM(0x0fcf)` quality | upstream |
|---|---|---|
| 541/621 pairs (68/78 ms), measured seed | `0x02e2` | 7,200, retrained to 4,800 |
| 439/519 (55/65 ms), the firmware's own | `0x02d2` | 7,200 |
| 49/129 (6.1/16.1 ms), held with `EICON_BULK_DELAY_HOLD_ALWAYS=1` | `0x02d0`, `0x02d5` | 7,200 |

The quality metric is flat across a 10× range of bulk delay, and `DM(0x20ba)`
stays at 3 in all of them. **The echo bulk delay does not govern the upstream
ceiling.** The Session 112 hypothesis — that a working canceller would lift
`DM(0x20ba)` — is disproved.

`limit` is `DM(0x20ba)` read directly, not derived, and `quality` is
`DM(0x0fcf)`. Session 109's archive shows `0x0069` with ceiling 8 transiently
mid-call and `0x02cf` with ceiling 3 at the final handoff. Our `0x02d0..0x02e2`
at the same point is that same number. So the cap is the steady state of this
path and predates all of this work; it is not a regression.

The open question is therefore what makes `DM(0x0fcf)` degrade from `0x0069` to
`0x02d0` over a call, and that is a receiver/line question — equaliser
convergence, or the analogue leg genuinely being a 7,200 upstream path. Nothing
in the echo canceller chain is still implicated.

---

## Session 207: the echo *level* block is never written on the pages that measure echo phase roll — and `DM(0x3F87)` is `RTDelay`, not a DIL count

Question: now that `docs/addsp_database.md` gives every database location a
guide name, does any of it bear on the V.90 connect rate or on the DIL region?
Method: no live call. A `.adsp-dm.bin` holds all 256 words of the memory-mapped
interface for every RTP packet, so this is a read over the whole archive —
`tools/dil_database_scan.py`, new here, with the snapshot index taken as the
guide offset directly (write 0x00..0x7F, read 0x80..0xFF).

**The corpus is thinner than it looks, and that governs everything below.**
Thirty captures, two empty (`courier01`, `run13`), and of the 28 remaining only
**five** ever load page 14: `eye_70` and `run10` (peak `TrnProgress 0x00b0`),
`run02` (`0x00c0`), `local01` (`0x00d0`, loopback) and `eicon-ppp-v22/run01`
(`0x00ea`). Every other `0x00d0` in `artifacts/eicon-ppp` — run11–run31,
td0_40, eye_50, eye_80 — is **page 2, V.32bis**, `DM(0x3FC4) = 0x2000`, and
most are one call replayed under different flags. A DIL split measured over
"twenty successes and six failures" here would be measuring V.32 replays of a
single call. Any future use of this archive as a sample needs `--v90-only`.

### `DM(0x3F87)` is `RTDelay`, and this log has printed it as a DIL count since Session 87

Trap 2 of `addsp_database.md` said the guide calls the word `RTDelay`, round
trip delay in 10 ms units, and that both names might be true. Over the archive
it takes 6..0x1d plus the sentinel 0xffff, changing 2–10 times per call: **60 to
290 ms, varying run to run on one rig.** That is the round trip this
SIP/RTP/ATA/two-wire path actually has, it is not a count of anything, and the
V.8 classifier is already known to be delay-sensitive (`PM 0x3982` never
executes at a 25 ms round trip, `DM(0x3FC4)` note). The `[dil]` line and the
`.adsp.csv` column are relabelled; the column name `dil_count` is gone.

### The echo and probe words are never written — with the positive control

`EcLevel` `DM(0x3F79)`, `FarEcLevel` `DM(0x3F7B)` and `SNRPROB` `DM(0x3F85)`
are **constant `0x0000` across all 28 non-empty captures, whole call, both
modulations.** `SNRPROB` reaches only `0xffff` in five of them, which is the
no-value sentinel, never a dB. `NearEcLevel` is non-zero in `local01` alone
(`0x8001`).

The negative has a control, per §0.4: in the same records `FarEchoPhaseRoll`,
`Signalquality`, `SNRatio` and `RTDelay` all move — `SNRatio` takes 7–35
distinct values per call — so the read half is live and being polled, and these
three words are specifically never written. Sessions 58–113 worked the echo
canceller and the bulk delay without reading the two words the guide says
report echo level.

`SNRPROB` is the sharper of the two. The guide's description is "SNR at
receiver slicer, as projected by the line probe" — the same quantity the INFO1d
projected-rate report is built from, and that report is the open item in
handoff §7.1: measured probe in, physically incoherent constant out. A
guide-named word for the projection stage, permanently zero across every
capture, says the same thing from an independent direction.

### `FarEchoPhaseRoll` is modulation-conditioned, not outcome-conditioned

Stated carefully because the first reading of it here was wrong. `DM(0x3F7C)`
is flat `0x0000` in all thirteen page-2 V.32 captures and non-zero in **every**
page-14 capture — `0x0001` at DIL entry, peaking `0x0040` in `eye_70` and
`run10` and `0x00d0` in `local01`, which reached `0x00d0`. So it tracks the
page, matching the guide's "only measured in V.34" note, and it does **not**
separate the DIL stalls from the completion. What it does establish is sharper
than a correlation: on the pages that measure far echo *phase roll*, the same
firmware never writes far echo *level*. The far-bulk branch that §3 says was
probably never taken is the obvious suspect, and settling it is one DM write
watch on `0x3F7C` and `0x3F7B` on one page-14 call.

### The two rate decodes in this repo are the same decode

`DATASTATESpeed` bit D picks which speed mask the speed number indexes, and
reading it reconciles the two formulas this project had for `DM(0x3F61)` and
`DM(0x3F62)`. The page-2 calls publish `0x11aa`/`0x11a9`/`0x01a8` — bit D
clear, V.34-format mask, norm 13, speeds 9600/7200/4800. `local01`'s page-14
call publishes `0x2029`/`0x202b`/`0x202c` — bit D set, so the number indexes
`speed_sel_V90_L`: 40000, 42666, 44000. Handoff §6's independent formula,
`21 + (value & 0x1f)` bits per datagram at 8000/6 datagrams per second, gives
the identical rates, and its worked example agrees exactly: `0x2028` → 29 bits
→ 38666 bit/s, and `speed_sel_V90_L` bit 8 is 38000+2000/3. Two derivations,
no free parameters, same answer. One decoder reading bit D now serves both
modulations; `rate_of()` in the new tool is it.

### `MAXTXSPEED` and `MAXRXSPEED` hold a 19200 ceiling nobody had read

`DM(0x3F5C)` and `DM(0x3F5E)` are `0x000e` on **every** capture, while
`speed_sel_l = 0xfffe` and `speed_sel_h = 0x001f` enable everything up to
33600. Under the numbering the rate decode above establishes — speed number is
the bit position, `_h` adds 16 — 14 is **19200**. The V.90 pair is
unconstrained: `MAXRXSPEED_V90 = 0x0015` is 56000, `speed_sel_V90_H/L` are
`0x003f`/`0xffff`, everything on.

This is a candidate, not a finding, and it has a live counter-example: Session
87 reported 24000 upstream, above 19200. So either `MAXTXSPEED_V90` (`0x0015`,
out of range in the V.34 numbering and therefore no cap) governs the upstream
of a V.90 call while `MAXTXSPEED` governs a V.34-only one, or the numbering
differs for these two words. Both readings are testable on one V.34 call:

```bash
EICON_PIN_DM=0x3F5C=0x0014 EICON_PIN_DM=0x3F5E=0x0014 ./run native-tower --run 43
```

A pin reporting zero hits tested nothing (§4). If the pin fires and the rates
do not move, the ceiling is not here and this closes.

### Two things withheld on purpose

Both split the corpus cleanly and neither is offered as a result.

- **`MinReduction_dbs` `DM(0x3F55)`** is `0x0de1` on run19, `0xff5d` on run12,
  `0xf5dc` on run17, `0x0000` on the Conexant failures. A minimum transmission
  level cannot be `0xff5d`, so the firmware reuses the word — trap 2 again, in
  the other direction — and the apparent split is over V.32 replays anyway.
- **`Signalquality` at DIL entry** is `0x0007` on every capture that failed to
  reach `0x00d0` and `0x0000` on nearly every one that did. This is precisely
  the shape of `DM(0x3f8b)`, which split perfectly over nine captures and was
  broken by the next live call (Sessions 87, 102), over a corpus whose
  successes are one page-2 call. `local01` already breaks it.

### Instrumented

`tools/dil_database_scan.py`, `--span` for the distinct-value count that makes
a zero readable as never-written rather than never-armed, `--v90-only` for the
page-14 subset, `--write` for the configuration half. The `[dil]` line now
carries `RTDelay` in ms, the four echo/probe words, `Signalquality`, both
training timers and both speed ceilings; `.adsp.csv` gains `eclevel`,
`nearectlevel`, `farectlevel` and `snrprob`.

`Maxtimer` `DM(0x3F0C)` and `Mintimer` `DM(0x3F0D)` read `0x0003` and `0x0014`
on every call and nothing in this repo sets them deliberately — 0x3F0C appears
in no tool at all, and 0x3F0D only in a comment about a write that was wrong.
The guide describes them as the periods the MSE must stay above and below a
threshold, so they are the only host-writable training-patience knobs in the
database and the only lever here that a DIL-region stall could plausibly answer
to. Their bitfields are in guide 5.3.1 and have not been read.
