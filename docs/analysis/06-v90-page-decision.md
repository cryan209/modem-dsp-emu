# Why the Conexant does not get V.90

Sessions 190-194. The bootpage request walked back to INFO1a bits 37:39.

Part of the running log; the index is [`../eicon_adsp_firmware_analysis.md`](../eicon_adsp_firmware_analysis.md). The current picture is [`../handoff.md`](../handoff.md).

---

## Session 190: PPP carries a live dial-in — and the Conexant never reaches the V.90 page

First live PPP session on record. A Windows client dialled 6001 through a Cisco
VG224 and got a routed network:

```text
[ppp] LCP up  mru=1500 accm=0x00000000 auth=chap
[ppp] authenticated 'ppp'
[ppp] IPCP up  local=100.64.0.1 peer=100.64.0.3
[ppp] closed 4 flow(s) for 100.64.0.3
```

182 s, CHAP, four NAT flows, 705 bytes to the link and 1347 from it. The client
behaved as a Windows RAS stack should: Callback option rejected, CCP `0x80fd`
Protocol-Rejected, VJ and NBNS rejected, then it took the address. Every
successful PPP run before this was `eicon_loopback.py`; the log contained no
live SIP call anywhere in the PPP-era sessions, and this session started from
the belief that no live call had ever carried PPP. The user knew otherwise.

### The rig was losing 15% of its wall clock to one log line

The first live calls ran at `ratio 0.85x` with 23,200 substituted RX samples,
and transmitted at 7303 Hz and 6791 Hz against a nominal 8000 (-87,079 and
-151,094 ppm), in runs of 20 and 33 packets spaced 82-84 ms. Inbound was fine:
p99 arrival gap 22-24 ms, no sequence gaps. `rtp_pcap_timing.py --buffer` said
`STARVED`, but the occupancy goes negative at t=62-63 s of a 64 s call, which is
the far end hanging up rather than mid-call starvation.

The cause was `portable V.34 bulk delay active`, coded as a rising-edge one-shot
whose edge flaps: `service()` returns False on some frames and True on the next,
about 425 times a second, several times per 20 ms media tick. Across three calls
it printed 136,949 lines while everything else in the log came to 2,609.

Capping it: 139,558 lines -> 455, 10.8 MB -> 73 KB, 0.85x -> 1.00x, 23,200
substituted samples -> 0. `tools/logcap.py` keys the cap on the caller's file and
line rather than the message, because the runaway formatted a different value
every time and would have escaped any cap keyed on text. This is the third such
line (Session 81's `UC_HOOK_CODE` trace at 813 MB, `--trace-v90d-state` at
2,021,167 lines), so the cap reports what it dropped at exit rather than being
silent.

The flapping edge is left counted, not explained. It is the echo canceller
(Session 88) and deserves its own session.

### Not a regression, twice over

Replaying the captured RX through the known-good tree (`e80b050`) and through
HEAD gives byte-identical traces over 20 s, bar a new `cyc=` annotation. And
HEAD still reaches `trn=0x00d0` on the Aug 4 `courier-v90` capture. Replay is
open loop, so this clears the receive path and state advance, not the closed
loop -- but the 25 commits of that day changed nothing here.

### Three harness faults the live rig exposed

1. **The endpoint could only ever be hung up on.** `end_call()` tears down this
   side, the BYE-received path handles the far end, and `run()`'s finally
   deregisters -- but nothing sent a BYE for a call in progress. Killing the
   endpoint mid-call left Asterisk holding a leg to 6001 (`core show channels`
   confirmed `PJSIP/8405 -> 6001`, both Up). Fixed: the `Call` now keeps the
   caller's From-tag, our To and the Contact, and `hangup_call()` sends a UAS
   BYE at shutdown. Not wired into the BYE-received path; answering a BYE with
   a BYE is its own bug.

2. **`cx_at.py` dialled `ATD`, not `ATDT`.** The method was whatever the profile
   held, and `AT&F` -- which the tool sends as setup -- restores pulse on some
   firmware. Into an FXS port that does not decode loop disconnect, every number
   comes back BUSY at the same speed, including one that does not exist, which
   reads as a dead route rather than a dial-method problem.

3. **`pjsip.conf` had a section that has never parsed.** Extension 8403's AOR
   header was split across two lines (`[8403\n](aor-multi)`), so the endpoint
   did not load and every call from VG224 port 2/3 was rejected. Fixing it is
   what let the Conexant reach the endpoint at all.

### `EICON_MODULATION` still does not select a modulation — but `+MS` does

A `ppp-v22` profile was added and withdrawn the same evening. Pinning
`EICON_MODULATION=v22b` produced a live call that walked V.8 -> INFO -> V.90 ->
INFO -> V.34 on samples 25440, 43040, 76480 and 93120: the same four samples as
the unpinned call before it. The CAI is correct (`disabled=0xfff7`, both
DISABLE_V90 and DISABLE_V34 set) and reaches the card; it has no bearing on the
handshake, exactly as the "EICON_MODULATION does not reach V.8" section says.
The profile also inherited `--force-info-after-v8`, which replaces the DSP's own
post-V.8 page choice at index >= 12000, so the route was being forced by the
harness regardless of any mask.

`AT+MS` on the *calling* modem does reach V.8. `AT+MS=V34,0,2400,33600` on the
Conexant moved it from its own choice onto the V.34 page. Modulation is
selectable in this rig; just from the other end.

### Why the Conexant does not work

Thirteen calls, two modems, one night, same endpoint:

```text
caller            0x3f8e   maxTrn   overlays               V.42
2/5  Windows      0x3165   0x00d0   0260,0261,026a         SABME=0
2/5  Windows      0x314f   0x00d0   0260,0261,026a         SABME=1   <- PPP
2/5  Windows      0x315a   0x00b3   0260,026a              -
2/5  Windows      0x3097   0x00b3   0260,026a              -
2/5  Windows      0x310e   0x00b3   0260,026a              -
2/5  Windows      0x30fa   0x00c0   0260,026a              -
2/3  Conexant     0x398c   0x00c0   0260,0261              -
2/3  Conexant     0x39ab   0x00c0   0260,0261              -
2/3  Conexant     0x143d   0x00b0   0260,0261              -   +MS=V34,33600
2/3  Conexant     0x358c   0x00b4   0260,0261              -   +MS=V34,14400
2/3  Conexant     -        0x00b8   0260,0261              -   +MS=V90,56000
```

The Windows modem takes **V.90** (`0x026a`) and is the one that reaches data
mode; it also draws the `0x00b3` stall three times in six, which is the lottery
Sessions 86-88 describe. The Conexant takes **V.34** (`0x0261`) every time and
reliably reaches `0x00c0` and stops.

**`0x00c0` on V.34 is not a data path.** Every call that carried data in this
log reached `0x00d0` on `0x026a`. Session 72 named this from the other side --
"CX93001 V.34 does not reach the V.42 boundary" -- and this session adds the
control that was missing then: a different modem, same rig, same night, same
endpoint, completing over V.90.

The decisive part is that **pinning the Conexant to V.90 does not move it**.
With `+MS=V90,1,,56000,,33600` accepted by the modem (`+MS: V90,1,300,56000,
300,33600`), the call still loaded only `0x025f`, `0x0260`, `0x0261`. It never
requested `0x026a`. So the Conexant is not choosing V.34 over an available
V.90 -- the V.90 page is never reached for this peer at all, whatever it offers.
Rate ceiling does move the V.34 depth (`33600 -> 0x00b0`, `14400 -> 0x00b4`,
`V90 -> 0x00b8`), so training margin is involved in how far it gets, but not in
which page it lands on.

That is the question for the next session, and it matters more than the others
here: the CX93001 is the only one of these modems still purchasable new, so it
is what anyone reproducing this project will have.

### A hypothesis this session did not test, and one it disproved

Disproved: **input gain is not the mechanism.** The received level on port 2/5
measured -15.6 dBFS against -21.3 dBFS on the Aug 4 call that reached `0x00d0`,
and 2/3 measured -28.8 dBFS, so the obvious reading was that the VG224's
`input gain 6` on 2/5 was breaking V.90. The successful PPP call is on 2/5, the
hot port; the consistent failures are on 2/3, the quiet one. Modem AGC covers
that range. The hypothesis was built before there was a working call to compare
against and does not survive one.

Untested, and the confound to break next: **port and modem vary together.** 2/5
carries `input gain 6` and the Windows modem and gets V.90; 2/3 carries no input
gain and the Conexant and never does. Moving the Conexant to 2/5 -- or setting
`input gain 6` on 2/3 -- changes one variable and separates "this modem cannot
get the V.90 page" from "this port cannot".

### VG224 notes

`destination-pattern .T` on the outbound dial-peer means "collect digits until
the interdigit timer expires", and because `.T` matches any length the gateway
always waits it out before sending the INVITE -- 3-4 s on every call.
Fixed-length patterns (`6[0-9][0-9][0-9]`) match on the last digit with no timer.
`#` is the default terminator and works today: `ATDT6001#` connects immediately.

Ports 2/5 and 2/21 carry `input gain 6` / `output attenuation -6`; 2/3 and 2/8
carry only the attenuation. Setting the attenuation to 0 is wrong -- the gateway
digitally pads the G.711 output -- so it stays at -6.

Suite 428.

## Session 191: [WITHDRAWN] the V.90 decision is a branch at PM 0x2bc1 against PM 0x2b9a

> **↩ Withdrawn by Session 192.** `PM 0x2bc1` and `PM 0x2b9a` are not two arms
> of one branch and have nothing to do with V.90: they are the 1/√2 correction
> arm of a `sqrt()` and the negate arm of an `abs()`, in two library subroutines
> that are called four times each on *both* captures. "Entered exactly once" was
> true and meant nothing. The set-diff below is sound as a measurement; the
> reading of it is not. The real decision is `PM 0x3304..0x330f` — see Session
> 193 — and it is INFO1a bits 37:39, per Table 10/V.90 — see Session 194.

Session 190 left the Conexant's failure as "it never requests overlay `0x026a`,
and `INFO_mode` reaches `0x0009` identically on calls that do". Three more
hypotheses died before the instrument was right, and then it fell out in one
diff.

Disproved first, all live tests:

- **Not the missing APCM download.** `EICON_DSP_EXTRA_DOWNLOADS=0x026b`, which
  Session 134 needed before the card would admit V.90A, changes nothing: same
  three overlays, same `0x00b2`, and `0x026b` is never requested.
- **Not our transmit gaps.** One Conexant call ran with `substituted=0` and
  `clock holds 0` -- a clean downstream from this end -- and still took V.34,
  while a Windows call with 27 holds and 54 ms of gaps reached `0x00d0`.
- **Not the modem's offer.** `AT+MS=V34` and `AT+MS=V90,1,,56000,,33600` are
  both accepted by the CX93001 (`+MS: V90,1,300,56000,300,33600`) and neither
  changes which page the card picks.

### The instrument

`tools/info_page_diff.py` replays a capture, gates the core's per-address
execution coverage to the INFO overlay `0x0260`, and writes the counts. Two
captures, one that takes V.90 and one that does not, then diff.

Gating on residency is not optional: a PM address is a different instruction on
every page, which is what made Session 188l wrong twice.

Run to 20 s the diff is 626 addresses on one side and 725 on the other, because
it contains the decision *and everything downstream of it*. The decision is at
5.541 s (V.90) and 5.678 s (V.34), so stopping at **5.40 s** -- before either
call leaves the INFO page -- leaves only the code that decides:

```text
A  artifacts/interop/courier-v90/call41-bulk0.rx.ulaw   -> 0x026a at 5.541
B  artifacts/eicon-ppp/cx02.rx.ulaw                     -> 0x0261 at 5.678

executed only in A (3):     PM 0x2bc1  x1
                            PM 0x2bc2  x1
                            PM 0x2bc3  x1
executed only in B (5):     PM 0x2b9a  x1
                            PM 0x2b9b  x1
                            PM 0x2b9c  x1
                            PM 0x2b9d  x1
                            PM 0x2b9e  x1
shared, >8x count skew (2): PM 0x1cb9  A=39  B=1
                            PM 0x1cba  A=39  B=1
```

Eight addresses out of 1,351, and the two arms are adjacent: **PM 0x2bc1 is the
V.90 arm and PM 0x2b9a is the V.34 arm**, each entered exactly once. Everything
else the INFO page executes is identical across the two calls -- 5,161 and 5,163
addresses, differing by these alone.

`PM 0x1cb9/0x1cba` runs **39 times on the V.90 call and once on the Conexant's**.
It is the only shared address with a count skew, so it is the strongest
candidate for what the branch reads: a loop that accumulates something over the
INFO exchange and terminates after one iteration for this peer.

### What this does not yet say

Which way the causality runs. `0x1cb9` running 39 times could be what makes the
branch take the V.90 arm, or both could be downstream of a third thing measured
earlier. The diff establishes where the paths part, not why.

Both replays are deterministic and reproduce their live outcomes exactly --
the Conexant capture goes to `0x0261` at 5.678 s offline just as it did on the
line -- so the next step needs no hardware: disassemble `0x2b90..0x2bd0`, watch
what `0x2bc1` and `0x2b9a` are conditional on, and watch the DM the
`0x1cb9` loop accumulates into. The disassembler mislabels direct DM
read/write opcodes and mis-decodes some overlay pages wholesale, so the
watchpoints are the ground truth there, not the listing.

This matters more than the rest of Session 190: the CX93001 is the only one of
these modems still purchasable new, so it is what anyone reproducing this
project will have on the desk.

Suite 428.

### The DM at the branch, and three words that are not the cause

Same trick applied to data: snapshot DM with the INFO overlay resident at 5.40 s
for both captures and diff. 3,021 of 16,384 words differ, which is what two
different calls look like -- but the control block is small enough to read:

```text
DM 0x3f0f  v90=0xfd34  cx=0xfdb4      DM 0x3f9c  v90=0x3ab9  cx=0x3ec5
DM 0x3f30  v90=0xe71f  cx=0xfc2a      DM 0x3f9d  v90=0x3cb6  cx=0x40c3
DM 0x3f31  v90=0x030e  cx=0x06a8      DM 0x3f9e  v90=0x3cb8  cx=0x40c4
DM 0x3f32  v90=0x1d78  cx=0x044a      DM 0x3f9f  v90=0xa048  cx=0xd875
DM 0x3f33  v90=0x041e  cx=0xfb91      DM 0x3fc4  v90=0xa106  cx=0xa10f
DM 0x3f78  v90=0x0036  cx=0x002c      DM 0x3fc5  v90=0x0e03  cx=0x08ad
DM 0x3f8e  v90=0x408c  cx=0x3420      DM 0x3fc6  v90=0x0e07  cx=0x08a9
DM 0x3f8f  v90=0x0000  cx=0x0001      DM 0x3fc7  v90=0xff00  cx=0x7b00
                                      DM 0x3fc8  v90=0x0400  cx=0x0845
                                      DM 0x3fc9  v90=0x011e  cx=0x0159
                                      DM 0x3fcd  v90=0x8310  cx=0xa334
                                      DM 0x3fce  v90=0x0001  cx=0xfffe
```

Three looked like the decision and none of them is. Each was forced to the
V.90 call's value with `EICON_FORCE_DM=...@0x0260` and each replay still took
`0x0261` at 5.678 s, byte for byte:

| forced                | why it looked right                          | result |
|-----------------------|----------------------------------------------|--------|
| `DM(0x3f8f)=0x0000`   | clean 0/1 next to the measurement word        | no change |
| `DM(0x3fc4)=0xa106`   | Session 134 forces this word to select a page | no change |
| `DM(0x3f8e)=0x408c`   | the `measure` word the `[dil]` line prints    | no change |

The last two are *verified* negatives rather than null experiments: the harness
printed `first overwrite: DM(0x3fc4) 0xa10f -> 0xa106 at sample 27514` and
`DM(0x3f8e) 0x1dd0 -> 0x408c`, so the patch was live and the outcome still did
not move. (`0x3f8f` was not confirmed to overwrite and should be re-run before
being trusted as a negative.)

Note `DM(0x3f8e)` reads `0x1dd0` at the moment of the first overwrite but
`0x3420` in the 5.40 s snapshot, so it is still moving during the window -- a
single forced value may be the wrong shape of experiment for it.

The disassembler is no help here. `0x2b90..0x2bd0` off a live PM snapshot
decodes to interleaved NOPs and impossible instructions (`SR = LSHIFT SI BY
-77`), which is the wholesale overlay mis-decode the README warns about.
Watchpoints are the instrument.

So the branch is located and three of its plausible inputs are eliminated. What
has not been tried: the `PM 0x1cb9/0x1cba` loop that runs 39 times against 1 --
which DM it accumulates into, and whether *that* word moves the branch. That is
the next thing, and it needs no hardware.

### The 0x1cb9 loop is a symptom, and the menu is the same for both peers

`tools/loop_dm_writes.py` uses the coverage count of a PM address as a trigger:
snapshot DM every sample, diff only on samples where the count for that address
rose, and take a control set from 400 samples where it did not. The control is
the point -- an INFO page mid-handshake rewrites plenty of DM for its own
reasons, and without it every busy word reads as a hit.

First correction: the 39-against-1 skew is not 39 samples. All 39 iterations
happen inside **one frame**, so the loop fires on a single sample in both calls.

Both calls write the same two words, and nothing else that the control does not
also write:

```text
call41-bulk0 (V.90)   PM 0x1cb9 fired on 1 sample   DM 0x2f42, DM 0x3f08
cx02         (V.34)   PM 0x1cb9 fired on 1 sample   DM 0x2f42, DM 0x3f08
```

And at 5.40 s both hold the same values -- `DM(0x2f42)=0x000e`,
`DM(0x3f08)=0x0021` -- so the loop **converges to the same result whether it
takes 39 iterations or 1**. Its output cannot be what the branch reads. The
count skew is a symptom of the peer, not the cause of the decision, and
Session 191's "strongest candidate" is withdrawn.

Worth more than that: `DM(0x3F09)`, the NORM_L V.8 menu, is `0xb13f` on both
calls, as are `0x3f0a/0x3f0b`. **The card offers the same menu to the Conexant
that it offers to the modem that gets V.90.** So this is not the card declining
to advertise V.90 to this peer; the divergence is downstream of the offer, in
what the peer's answer measures to.

`DM(0x3fce)=0x0001` forced (verified: `0xffff -> 0x0001 at sample 27514`) is a
fourth word eliminated, joining `0x3f8f`, `0x3fc4` and `0x3f8e`.

Still unforced from the twenty that differ: `0x3f0f`, `0x3f30..0x3f33`,
`0x3f78`, `0x3f9c..0x3f9f`, `0x3fc5..0x3fc9`, `0x3fcd`. The method is settled
now -- force one to the V.90 call's value with `@0x0260`, confirm the
`first overwrite` line, and see whether the page at 5.678 s moves -- so this is
a list to work through rather than a puzzle. `0x3f30..0x3f33` are the most
interesting of them: four adjacent words, all differing, none of them touched
by any experiment so far.

## Session 192: `PM 0x2bc1` and `PM 0x2b9a` are arms of two arithmetic helpers — the branch Session 191 named is not a branch about V.90

Session 191 ended with a list to work through: force each of the twenty
differing control-block words to the V.90 call's value and see whether the
Conexant capture moves off `0x0261`. That list is finished, and the answer
is no — but the more useful result is that the thing the forcing was aimed at
was never the decision.

### The whole control block at once, not one word at a time

Twenty single-word runs answer twenty questions and leave every combination
untested. One run with all twenty forced answers the interesting one first: if
the block jointly does not move the outcome, no subset of it does either.

```text
EICON_FORCE_DM=0x3f0f=0xfd34@0x0260,0x3f30=0xe71f@0x0260,0x3f31=0x030e@0x0260,
  0x3f32=0x1d78@0x0260,0x3f33=0x041e@0x0260,0x3f78=0x0036@0x0260,
  0x3f8e=0x408c@0x0260,0x3f8f=0x0000@0x0260,0x3f9c=0x3ab9@0x0260,
  0x3f9d=0x3cb6@0x0260,0x3f9e=0x3cb8@0x0260,0x3f9f=0xa048@0x0260,
  0x3fc4=0xa106@0x0260,0x3fc5=0x0e03@0x0260,0x3fc6=0x0e07@0x0260,
  0x3fc7=0xff00@0x0260,0x3fc8=0x0400@0x0260,0x3fc9=0x011e@0x0260,
  0x3fcd=0x8310@0x0260,0x3fce=0x0001@0x0260
  python tools/info_page_diff.py artifacts/eicon-ppp/cx02.rx.ulaw out.cov --to 6.0
```

```text
[force-dm] first overwrite: DM(0x3f0f) 0x3763 -> 0xfd34 at sample 27514
pages: 0x0271@0.0 0x025f@0.0 0x0260@3.233 0x0261@5.678
```

Byte for byte the baseline, to the sample. This is a *live* patch, not a null
experiment: against the unforced run the INFO page's coverage differs at 105 PM
addresses, so the page reads these words and executes differently for them —
and still lands on V.34 at 5.678 s. All twenty are eliminated, jointly, and
`0x3f8f` is now confirmed as well.

### The counter was never gated

`adsp2181_coverage_gate()` defaults to **on** (`a->coverage_on = 1` at reset).
All three of the Session 191 tools open with `gated = False` and only call the
gate on a *transition*, so the first transition is the first time the gate is
pushed down at all — every page before the one under test was counted as if it
were on it. That is the Session 188l trap, in the tools written to avoid it.

Fixed in `info_page_diff.py`, `loop_dm_writes.py` and `branch_frame.py` by
setting the gate explicitly before the loop. Re-running Session 191's addresses
with the fix gives the same samples, so that particular finding survives — but
no gated count taken before this commit should be trusted without a re-run.

### The instrument: which *frame*, not which second

`tools/branch_frame.py` polls gated coverage once per sample and reports the
sample an address first executes on, together with the `EICON_TRACE_FRAMES`
value that arms the shim's instruction trace on that frame. The two counters
are not the same number and are not off by one: at loop index 37570 the shim's
`_media_samples` is **39223**, a lead of 1653. Aiming the trace with the loop
index traces the wrong frame and prints 407 instructions of resident kernel,
which reads exactly like "the address does not execute here".

With the right frame the opcodes come out of the trace itself, so the
disassembler never has to decode a page image — which is what made
`0x2b90..0x2bd0` come out as `SR = LSHIFT SI BY -77` last session.

### What the two addresses actually are

```text
3880  1eb94f  CALL $2B94
2b94  2aea0a  AR = AR - AY1, AX0 = AR
2b95  1ab9a5  IF GE JUMP $2B9A
              ...                        <- the AR < 0 arm
2b9a  233a0f  AR = 0 - AR
2b9b  0d009a  SE = AR
2b9c  1027ae  SR = ASHIFT SR1 (HI), AR = SR0
2b9d  2a78ea  AR = AX0 + 0, SR0 = AR
2b9e  0a000f  RTS
```

```text
2bbd  6638a1  AF = 0 + 1, AR = DM(I0,M1)
2bbe  0f22ff  SR = ASHIFT AR (HI) BY -1
2bbf  2e7eaf  AF = SR0 + 0, AR = SR1
2bc0  1abc40  IF EQ JUMP $2BC4
2bc1  45a822  MX0 = $5A82
2bc2  28204a  MR = MX0 * MY0 (RND), AY0 = AR
2bc3  22200f  AR = AY0 + 1
2bc4  6800c5  DM(I1,M1) = MR1
2bc5  6800a5  DM(I1,M1) = AR
2bc6  0a000f  RTS
```

`0x2b9a` is the negate arm of an **absolute value** feeding a shift exponent.
`0x2bc1` is the odd-exponent correction of a **square root**: `0x5A82` is
1/sqrt(2) in Q15, applied when the halved exponent lost a bit. Two arithmetic
library subroutines. Neither is a policy branch, and they are not two arms of
one branch — they are in different routines, eight addresses apart because the
assembler laid them out that way.

They are called on the same schedule on both calls, four times each in the
window, and the arms fall wherever the numbers put them:

```text
              call41-bulk0 (V.90)        cx02 (Conexant, V.34)
0x2b94        3.883  4.176  4.599  5.106   3.969  4.263  4.696  5.203
0x2b9a        -      -      -      -       -      -      4.696  -
0x2bbe/0x2bc0 3.883                        3.969
0x2bc1        3.883                        -
```

Same four calls, ~0.09 s apart, same helper, different arm on one iteration
each. Session 191's "PM 0x2bc1 is the V.90 arm and PM 0x2b9a is the V.34 arm,
each entered exactly once" is withdrawn. "Entered exactly once" was true and
meant nothing: a `sqrt` takes its correction arm once per call because the
argument is odd once, not because the modem is a Conexant.

### What this leaves

The set diff was honest and its reading was not. Up to 5.40 s the INFO page
executes **the same code** on both calls — 5,161 against 5,163 addresses, and
the only differences are two data-dependent arms inside `abs()` and `sqrt()`.
That is now a positive result rather than a near miss: the page is not choosing.
Nothing on the INFO page branches on the peer, the menu it offers is identical
(`DM(0x3F09)=0xb13f` on both, Session 191), and the twenty-word control block
around the measurement does not move the outcome.

So the decision is either made from a measured quantity this page only computes
and hands on — the `abs`/`sqrt` pair says a magnitude is being reduced to an
exponent, which is what a level or SNR estimate looks like — or it is not made
on the DSP at all. The second is worth taking seriously before more DM forcing:
`te_dmlt.pm` searches the staged download table at `0x80091f9c` before it will
admit V.90A (Session 134), so the MIPS protocol image has its own say in which
bootpage is requested, and every experiment since Session 190 has been aimed at
the DSP.

Two things to do next, in this order:

1. Find who requests the bootpage. Watch the overlay-request word on both
   captures and see whether the DSP asks for `0x0261` or is told to. This
   settles which processor to instrument and needs no hardware.
2. If it is the DSP: instrument the `0x2b94`/`0x2bbd` callers rather than their
   arms — the four call sites are the measurement schedule, and the argument
   they are called with is the quantity that differs. `branch_frame.py` already
   names the frames.

The forcing sweep is not worth continuing. Twenty words are eliminated jointly
and the page they sit on has been shown not to branch on the peer.

## Session 193: the DSP requests the bootpage, from three bits of a word the peer sent — and forcing it puts the Conexant on V.90

Session 192 left two things to do in order, and the first one answers the
second. The whole chain from received bits to overlay request is open now.

### The DSP asks; nothing on the MIPS side is consulted

The harness reads the page request out of `DM(0x3132)` with `DM(0x3131)` as the
flag and `DM(0x3FB0)` as the bootpage, and serves what it finds.
`tools/page_request_writer.py` watches those words for *DSP* stores — the
shim's own `self.dm[...] = ...` assignments go through the array and not the
store path, so `--force-info-after-v8` rewriting `DM(0x3132)` does not appear —
and every one of them has a firmware PC behind it:

```text
cx02   dm w 3fb0=0008 ppc=217e ... ar=0008     dm w 3132=0261 ppc=069b ar=0261
call41 dm w 3fb0=000e ppc=217e ... ar=000e     dm w 3132=026a ppc=069b ar=026a
```

Same store, same 24 prior PCs to the instruction on both calls:

```text
2b6b 2b6c 2b6d 2b6e 2b6f 3645 3646 3647 3648 2111 2112 2113 2176 2177 2178
2179 2179 2179 2179 217a 217b 217c 217d 217e
```

`te_dmlt.pm` and the staged download table gate whether V.90A may be *offered*
(Session 134). They have no part in this decision. Session 192's first
next-step is closed: instrument the DSP.

### There is no branch in the frame that stores it

Traced whole, the two frames that write `DM(0x3FB0)` execute **1,623
instructions each, in the same order, with no PC divergence at all**. The
bootpage is not selected by a branch here; it is copied:

```text
217d  816b6a  AR = DM($16B6)
217e  93fb0a  DM($3FB0) = AR        <- 0x000e (V.90) / 0x0008 (V.34)
```

Which also disposes of four more of Session 191's twenty words. `PM
0x2b6a..0x2b6f` is a four-deep ring buffer write — `I0 = DM($3FCC)`, `L0 = 4`,
store, `DM($3FCC) = I0` — so `DM(0x3F9C..0x3F9F)` is a *log* of packed status
words, which is why `0x3ab9` in that ring and `AR = 0x3ab9` at the call site are
the same number. Not control.

### The decision, in nine instructions

`DM(0x16B6)` is written once per call, at `PM 0x3310`, and here the prior-PC
histories finally differ — the V.90 call jumps `330a -> 3310`, the Conexant
falls through `330b..330f`:

```text
3304  4000e0  AX0 = $000E            ; bootpage 14 = V.90
3305  83fbba  AR = DM($3FBB)         ; v90: 0x3064   cx: 0x3044
3306  400704  AY0 = $0070
3307  23820f  AR = AR AND AY0
3308  400604  AY0 = $0060
3309  23c20f  AR = AR XOR AY0
330a  1b3100  IF EQ JUMP $3310       ; (DM(0x3FBB) & 0x70) == 0x60 -> V.90
330b  4000d0  AX0 = $000D            ; bootpage 13
330c  83f94a  AR = DM($3F94)
330d  23825f  AR = AR AND $0002
330e  1b3101  IF NE JUMP $3310
330f  400080  AX0 = $0008            ; bootpage 8 = V.34
3310  916b60  DM($16B6) = AX0
```

One bit. `DM(0x3FBB)` is `0x3064` on the call that gets V.90 and `0x3044` on the
Conexant's, and bit 5 is the entire difference.

### Where the bit comes from

`DM(0x3FBB)` is a bitfield assembled by shifting single flags into place, and
bits 4-6 are `DM(0x170B)`:

```text
3e58  SI = DM($170B)
3e59  SR = LSHIFT SI (LO, OR) BY 4   ; 0x0004 -> 0x0064 (v90) / 0x0044 (cx)
...                                  ; bits 7, 8, 12, 13 from elsewhere
3df1  DM($3FBB) = SR0
```

and `DM(0x170B)` is three bits of a received word:

```text
3e14  8060b8  SI = DM($060B)         ; v90: 0x0d09   cx: 0xb934
3e15  0f10f7  SR = LSHIFT SI (LO) BY -9
3e16  400074  AY0 = $0007
3e17  23860f  AR = SR0 AND AY0
3e18  9170ba  DM($170B) = AR         ; 6 (v90) / 4 (cx)
```

`DM(0x060B)` is a slot in a deserialiser output, written at `PM 0x3597` through
`DM(I5,M5)` after `PM 0x3b1d..0x3b23` — `MR = MR1 * $4000`, shift, OR, sixteen
times under `CNTR` — which is a 16-bit **bit reversal**. So the word is
assembled from the line, bit-reversed, and bits 9..11 of it are the field the
decision reads.

The chain end to end:

```text
received bits -> deserialise + bit-reverse (PM 0x358e..0x3599) -> DM(0x060B)
  -> bits 9..11 (PM 0x3e14..0x3e18)            -> DM(0x170B)   6 or 4
  -> packed into bits 4..6 (PM 0x3de9..0x3df1) -> DM(0x3FBB)   0x3064 / 0x3044
  -> == 0x60 ? 14 : 13/8 (PM 0x3304..0x3310)   -> DM(0x16B6)   14 or 8
  -> copied (PM 0x217d/0x217e)                 -> DM(0x3FB0)   bootpage
  -> kernel posts (PM 0x069a/0x069b)           -> DM(0x3131)/DM(0x3132)
  -> the host serves overlay 0x026a or 0x0261
```

### The A/B: the Conexant capture takes V.90

`DM(0x16B6)` is written at cycle 113,880,634 and read at 113,970,208 — different
frames — so unlike `DM(0x3FBB)` and `DM(0x170B)`, which are written and read
about seventy cycles apart inside one frame and cannot be held by a per-sample
force, this one can be:

```text
EICON_FORCE_DM=0x16b6=0x000e@0x0260 python tools/info_page_diff.py \
    artifacts/eicon-ppp/cx02.rx.ulaw out.cov --to 10.0

[force-dm] first overwrite: DM(0x16b6) 0x0000 -> 0x000e at sample 27514
cx02.rx.ulaw: V.90=True
  pages: 0x0271@0.0 0x025f@0.0 0x0260@3.233 0x026a@5.678
```

The Conexant capture loads **overlay 0x026a**, at the same 5.678 s it used to
load `0x0261`. Twenty-two calls across Sessions 190-192 could not move this and
one word does, which makes the chain above cause rather than correlation.

What it is not: a fix, or a working V.90 link. This overrides the card's reading
of what the peer said rather than changing what the peer says, and the replay is
open loop — the recorded Conexant never hears our downstream, so nothing after
the page load can be trusted to mean the handshake would complete. It proves
where the decision is made and what it is made from. Whether a live Conexant
completes from here is a hardware run.

### What to ask next

The field is three bits wide, the accepted value is 6, and the Conexant produces
4 — one bit apart, in a word taken bit-reversed off the line during the INFO
exchange. Two readings, and they need separating before any more emulator work:

1. The Conexant genuinely advertises something different, in which case the card
   is behaving correctly and the lever is on the modem — and `AT+MS=V90` did not
   move it (Session 190), so it would be a country/profile or S-register matter,
   or a CX93001 that does not offer this at all.
2. The card mis-decodes what the Conexant sends — a framing or bit-order error
   in the deserialiser at `PM 0x358e..0x3599`, which would make `0xb934` a
   misread rather than a message.

Reading the INFO exchange out of the two captures directly and comparing it
against what bits 9..11 of `DM(0x060B)` should be under V.90 §9 separates them,
and needs no hardware. That is the next session.

Suite 428.

## Session 194: the Conexant asks for V.34 — INFO1a bits 37:39 are 4, and the card is doing exactly what V.90 Table 10 says

Session 193 ended with two readings to separate: either the Conexant advertises
something different, or the card mis-decodes what it sends. `tools/v34_info.py`
settles it without the emulator — it demodulates the DPSK control channel in
Python and accepts a frame only if the transmitter's own CRC checks, so a frame
it reports was genuinely on the wire.

### The peer's message, decoded twice by different means

```text
call41-bulk0.rx.ulaw   2400 Hz (peer)   5.430s   38 bits
   payload 00000000000000111001000010110000000000
   lsb-first  c000 0d09 0000
cx02.rx.ulaw           2400 Hz (peer)   5.566s   38 bits
   payload 01000000000000000010110010011101111111
   lsb-first  0002 b934 003f
```

`0x0d09` and `0xb934` are the values Session 193 watched the firmware read out
of `DM(0x060B)`, to the digit. Two independent paths — the card's demodulator
under emulation, and a Python one validated by CRC — agree on both calls. The
deserialiser at `PM 0x358e..0x3599` is correct. **The card is reading the peer
accurately.**

### It is INFO1a, and the field is the one the Recommendation names

The tool's 10-bit sync `0x372` is V.34's fill tail plus frame sync
(`11` + `01110010`), so payload bit *N* is INFO bit *N*+12. The field the
firmware tests — payload bits 25:27 — is **INFO1a bits 37:39**, and Table
10/V.90 is unambiguous:

> 37:39  Symbol rate of 8000 to be used by the digital modem: The integer 6

and above the table, "Bits 37:39 represent the integer 6, indicating that V.90
operation is desired". Table 11/V.90 covers the other case: "Bits 37:39
represent an integer between 0 and 5, indicating that V.34 operation is
desired".

```text
                Courier (V.90)      Conexant CX93001
INFO1a 37:39    6                   4
```

`PM 0x3304..0x330f` — `AX0 = $000E`, `(DM(0x3FBB) & 0x70) == 0x60 ?` — is that
test, literally, with the field in bits 4:6 of the packed word. The firmware
implements Table 10 and implements it correctly.

The rest of the two messages parses cleanly under the matching table, which is
the check that the field mapping is right rather than a coincidence:

```text
Courier, Table 10 (V.90):  reserved=0  MD len=0  UINFO=78  reserved=0
                           symbol rate=4 (3200)  MODE=6  freq offset +0.00 Hz
Conexant, Table 11 (V.34): power reduction 2 dB, additional 0 ...
                           symbol rate=4 (3200)  MODE=4  freq offset -0.10 Hz
```

`UINFO=78` satisfies the Recommendation's "shall be greater than 66"; both ends
ask for symbol rate 4 upstream; the offsets are sane. Nothing here is a misread.

**So the Conexant asks for V.34.** The card is not declining V.90 to this peer
and never was. Every session from 190 onwards has been looking at the wrong end
of the call.

### The card offers both modems the same thing

Decoding our own transmit captures at 1200 Hz:

```text
INFO0d   cx02.ulaw          3.377s  30 bits  101111111000010000110111011000
         call41-bulk0.ulaw  3.286s  30 bits  101111111000010000110111011000
INFO1d   cx02.ulaw          5.310s  75 bits  ...0101110000000001000000
         call41-bulk0.ulaw  5.193s  75 bits  ...0101110000000001111111
```

The INFO0d is **identical bit for bit**, and it is the 30-bit V.90 form (bits
12:41), not V.34's 17. Read against Table 7: symbol rates 2743 and 3429 but not
2800, both carriers at 3000 and 3200, power reduction available, 1664-point
constellations, nominal Phase 2 power -12 dBm0, maximum -12.0 dBm0 measured at
the codec output, bit 39 = 0 (µ-law, matching the call), bit 40 = 0 (no
upstream 3429). The INFO1d differ only in the trailing measurement field.

So the digital modem announces itself identically to both peers, in the V.90
form, and one peer asks for V.90 and the other for V.34. This is the INFO-level
version of Session 191's "the card offers the same menu to both", and it closes
that line: the offer is not the variable.

### ↩ Correction: the differing bit is V.92 capability, not an anomaly

Both modems send the 17-bit INFO0a (bits 12:28). This was originally decoded
only against V.90 Table 8 and called a reserved-bit anomaly. That was wrong:
V.92 Table 16 redefines the pair:

```text
INFO0a bit 26 = 1: V.92 capability
INFO0a bit 27 = 1: request short Phase 2
```

The Conexant's pair is `bit26=1, bit27=0`: **V.92 capable, normal Phase 2**.
The Courier sends both clear. Correspondingly, V.92 Table 15 defines INFO0d bit
27 as the digital modem's V.92 capability; ours is clear, so the CX correctly
continues using the V.90 procedure. This is neither malformed nor a cause of
mode 4. The earlier V.34 transmit-clock interpretation and "not interpreted"
conclusion are withdrawn.

### What this means for the actual problem

The decision is the **modem's**, made from line probing and from INFO0d/INFO1d.
The card's contribution is identical on both calls, so what differs is the line
as the Conexant measured it, or the Conexant's own policy.

The useful consequence is that this no longer needs the emulator at all. Any
capture answers it:

```bash
python tools/v34_info.py CAPTURE.rx.ulaw --from 3.0 --to 6.0
```

If the 38-bit peer frame's `lsb-first` word 1 has bits 9:11 equal to 6, that
call asked for V.90; 0..5 asked for V.34. One decode, no card, no replay.

Which makes Session 190's untested confound the thing to break, and cheap now:
**2/5 carries `input gain 6` and the Windows modem and gets V.90; 2/3 carries no
input gain and the Conexant and never does.** Move the Conexant to 2/5, capture,
decode, read bits 37:39. If it becomes 6 the port is the variable and the modem
is fine; if it stays 4 the modem is deciding V.90 is not available on any line
this gateway presents.

Worth testing alongside it, and against Session 190's note that setting it to
zero is wrong: the VG224's `output attenuation -6` is a *digital* pad on the
G.711 stream, and a digital pad in the downstream path is the classic reason an
analogue modem declines PCM downstream. Session 190 rejected level as a
mechanism on the grounds that modem AGC covers the range, which is right about
level and says nothing about PCM transparency. Both ports carry the attenuation,
so it cannot be the whole story, but it is the right class of impairment and it
is one config line to test.

Suite 428.

## Session 195: an independent V.90 server reads the same 4 — the Conexant declines PCM after line probing, not before

Session 194 left one fork: does the CX93001 ever ask for 6 on *any* path, or is
it our endpoint's downstream it rejects? The control needed a second V.90
digital modem, and there is one in the tree — `../v90modem/sip_v90_modem`, a
PJSIP + spandsp implementation sharing no code with this project.

Registered as extension 6000 on port 5062 (the Eicon endpoint kept 5060/6001
throughout and was not touched), with `VPCM_G711_TAP_DIR` armed. The Conexant
dialled `ATDT6000#` from **the same VG224 port 2/3**, the same gateway and the
same route as every call in Sessions 190–194.

### The server says it itself

```text
[TRACE +6340ms] V8 result: status=V.8 call negotiation successful (2)
                mods=V90|V34|V22 (0x1804) protocol=0x1 pstn=0x0 pcm=0x1
FLOW Rx - V.90: INFO1a declined PCM (downstream code=4, not 6); V.34 fallback,
     we take the call-modem role
```

**`downstream code=4, not 6`** — the same value the Eicon card read out of
`DM(0x060B)` on cx02, produced by an unrelated implementation on an unrelated
codebase.

Decoding the server's own G.711 tap with `tools/v34_info.py`, which touches
neither implementation:

```text
live-rx.g711  2400 Hz (peer)  6.379s  17 bits  lsb-first 61ff 0000
                              8.116s  37 bits  lsb-first c00f c934 001f
```

`0xc934 >> 9 & 7 = 4`. The INFO0a is `0x61ff`, byte-identical to cx01 and cx02.
So bits 37:39 are 4 by three independent routes: the card's demodulator under
emulation, spandsp's, and ours.

**The rig is exonerated.** The Conexant asks for V.34 against a completely
different V.90 digital modem on the same line, so nothing about what this
project puts downstream is what it is rejecting, and `PM 0x3304..0x330f` is
doing exactly what Table 10/V.90 says. Session 194's fork is closed on the
first branch.

### The part that is new, and it moves the question

`mods=V90|V34|V22 (0x1804) … pcm=0x1`. **The Conexant offers V.90 in V.8.** It
is not a modem with V.90 disabled, and this is the first direct read of its CM
rather than an inference from `AT+MS`. It then declines PCM in INFO1a — which is
sent *after* Phase 2 line probing.

So the modem is willing until it measures the line, and the decision is a
measurement, not a policy. That rules out the whole "country profile /
S-register / capability" family that Session 190 opened and 194 could not
close.

### ↩ Correction: the pad was named because it was the only knob, not because anything pointed at it

The section below survives as written, and its reasoning does not. It was
challenged the same evening and does not hold:

- **The VG224 config has never been inspected.** Everything here and in Session
  190 about `input gain` and `output attenuation` comes from one summary
  sentence. The pad's existence, size, direction and whether 2/3 and 2/5 match
  on it are all unverified.
- **The Courier reaches V.90 through the same attenuation on 2/5.** That is
  counter-evidence to the pad being what destroys transparency, and the section
  below disposes of it in one clause — "a difference in tolerance" — which is a
  placeholder written in the grammar of a mechanism.
- It was nominated because it was the only named knob in reach. That is the
  §0 failure mode in `handoff.md`, committed the same day the section was
  written.

What the evidence supports is only this: **the Conexant declines PCM after
Phase 2, and what it measured is not known.** The modem keeps its own record of
that measurement — `AT#UD` returns ~40 `DIAG <2A4D3263 nn=...>` fields — but the
field map is Conexant's and is in neither tree, so the dump is currently
unreadable. Getting that map, or putting the Courier on 2/3, beats any further
reasoning about impairments.

### What that leaves, and it is one config line

The remaining variable is what the CX93001 measures during Phase 2 that the
Courier does not object to. The obvious candidate is the VG224's
`output attenuation -6` on the FXS port: it is a **digital** pad on the G.711
stream in the downstream direction, and a digital pad is precisely what destroys
the PCM codeword transparency that INFO1a's downstream code reports on.

Session 190 rejected setting it to zero on the grounds that "the gateway
digitally pads the G.711 output". That is correct about *level* and silent about
*PCM transparency*, which is the thing at issue here — the two are different
questions and 190 only answered the first.

It cannot be the whole story: the Courier reaches V.90 through the same
attenuation on 2/5. So the reading is a **difference in tolerance between the
two modems against a real impairment**, not an absolute block — which is exactly
the shape that makes one modem work and another not on the same line.

Next, and it needs no emulator: set `output attenuation 0` on the Conexant's
port, re-dial either endpoint, and decode bits 37:39. If it becomes 6, the pad
is the impairment and the fix is gateway configuration for every V.90 caller.

### Two caveats

The extension differed (6000/5062 against 6001/5060), so in principle a
different dial-peer. The result matching the 6001 path exactly — same INFO0a,
same field value — is itself the evidence that the route is not what changed.

The modem reported `NO CARRIER` at 21 s because the *server* hung up: its V.34
fallback mis-selected a descrambler tap (`TRN selected tap=17 but role requires
tap=4`) and never trained. That is a `v90modem` defect on the V.34 fallback path
and has no bearing on the INFO1a reading, which happened at 8.1 s and is
CRC-valid.

Suite 428.

## Session 196: `AT#UD` decoded against the Unimodem spec — it cannot answer the question, and it corroborates the answer we have

`AT#UD` is Microsoft's Unimodem Diagnostic Command, not a Conexant extension,
so the key numbering is documented. The spec is `umud10.rtf`, and
`tools/unimodem_ud.py` now decodes a report against it — Tables 2, 6 and 9,
the spec ranges, and a list of what the modem did *not* report.

### It cannot answer the question, for two independent reasons

Specification **note 5**: v1.0 "is being developed while V.90 is in development
in ITU-T SG16… it is likely that future versions of this specification will add
parameters based on V.90." There are **no V.90 parameters in this version at
all**, so no conforming `#UD` report can say why a modem declined PCM.

And separately, every field that would have been indirectly useful is `Rec10`
— recommended, not required — and the CX93001-EIS V0.2013 reports none of them:

```text
0x04  V.8 CM octet string          0x14  near echo loss
0x05  V.8 JM octet string          0x15  far echo loss
0x13  normalized mean squared err  0x16  far echo delay
0x18  V.34 INFO bit map            0x17  round trip delay
```

`0x04`/`0x05` would have confirmed the V.8 offer independently of `v90modem`'s
parse, and `0x18` is literally the V.34 INFO bit map. Neither is present. So
Session 195's "the modem tells us what it measured" was optimistic twice over,
and this closes it rather than leaving it as a lead.

### What it does give, and one of them is a real cross-check

```text
0x01  call setup result      07    data answering signal detected (V.25 ANS / V.8 ANSam)
0x20  transmit carrier       0C    V.34
0x21  receive carrier        0C    V.34
0x22  transmit symbol rate 0C80    3200 symbol/s
0x23  receive symbol rate  0C80    3200 symbol/s
0x26  initial rates        2580    9600 bit/s both directions
0x11  transmit power         0C    -12 dBm
0x12  estimated noise        1B    -27 dBm
0x60  termination cause      2C    call setup fail timer expired (e.g. S7 timeout)
```

**Keys 20/21 are the modem's own record that the call ran V.34**, from a fourth
independent source. And **key 22/23 at 3200 symbol/s matches INFO1a bits 34:36 =
4** — which Table 10/V.90 defines as symbol rate 3200 — decoded off the audio in
Session 194 by a completely different route. Two unrelated instruments agreeing
on a field of the same message is the strongest confirmation the decode chain
has had.

`0x60 = 0x2C` also explains Session 195's `NO CARRIER`: the S7 call-setup timer
expired, consistent with the `v90modem` server's V.34 fallback never training.
It was not a modem-side abort, so nothing about the INFO1a reading is in doubt.

### Two out-of-spec values, deliberately not interpreted

`0x10` (received signal power) reads `0xD8` against a spec range of `0..0x2F`,
and `0x50`/`0x51` (flow control) read `3` against `0..2`. The decoder flags both
rather than converting them. A unit or encoding could be invented that makes
`0xD8` a plausible receive level, and inventing one is how Session 195 produced
a lead with counter-evidence beside it. They are recorded as anomalies.

Suite 428.

## Session 197: Asterisk is in the media path, and the two ports do not share an endpoint config

The port-move framing in Sessions 190–196 was wrong and is dropped. It treated
2/3 and 2/5 as differing only in VG224 gain, which is a knob on the wrong side
of the call. What actually differs between them is bigger and was recorded in
Session 190 without being connected to any of this.

### Measured: the media transits Asterisk

Parsing `artifacts/eicon-ppp/cx02.rtp.pcap` (DLT 101, raw IP):

```text
192.168.88.122:10588 -> 192.168.88.167:4000   PT=0  ssrc=0x2e0c2f18  160B  2604 pkts
192.168.88.167:4000 -> 192.168.88.122:10588   PT=0  ssrc=0xeabc9522  160B  2600 pkts
```

`192.168.88.122` is `asterisk.net.cryan.nz`. So the RTP is **endpoint ↔ Asterisk**,
not endpoint ↔ VG224: Asterisk is relaying the media rather than re-inviting the
two ends together. PCMU both directions, 20 ms, no loss to speak of.

That matters because V.90's downstream requirement is that our PCM codewords
reach the VG224's codec **unaltered**. A native bridge between two PCMU legs
relays packets and is transparent, but Asterisk cannot native-bridge a leg that
has any DSP attached — inband DTMF detection, fax tone detection, a jitter
buffer, denoise or AGC all force a decode to signed linear and a re-encode, and
a re-encoded codeword stream is not PCM-transparent even when it sounds
identical and measures identical in level.

### The thing Session 190 recorded and nobody joined up

```text
3. `pjsip.conf` had a section that has never parsed. Extension 8403's AOR
   header was split across two lines ([8403\n](aor-multi)), so the endpoint did
   not load and every call from VG224 port 2/3 was rejected.
```

**8403 is port 2/3 — the Conexant's port — and its endpoint config was broken
and hand-reconstructed during Session 190.** **8405 is port 2/5**, where the
Windows modem reaches V.90 and always has; that section was never touched.

So the two ports do not merely differ in VG224 gain. They are **different
Asterisk endpoints with independently-written configuration**, one of them
freshly hand-repaired, and every V.90 success in this project is on the one that
was already working.

### The hypothesis, and what kills it

If 8403's reconstructed section differs from 8405's in anything that attaches a
DSP to the leg — `allow`/`disallow` leaving a transcode possible, `dtmf_mode`,
`faxdetect`, `jitterbuffer`, a different template — then calls from 2/3 are
decoded and re-encoded on their way to us, the downstream stops being
PCM-transparent, and a modem that checks for transparency before requesting PCM
correctly declines. A modem that does not check as strictly would ask for V.90
anyway.

That accounts for every observation without needing the two modems to differ at
all:

- the Conexant declines PCM on 2/3, against our card *and* against spandsp (195),
  because both calls cross the same Asterisk leg;
- the Courier reaches V.90 data mode on 2/5, because that leg is clean;
- gain, level and the VG224 pad are irrelevant, which is what the measurements
  already said (190).

**It dies immediately if `8403` and `8405` are identical bar the AOR.** That is
one diff, needs no call, no cabling and no emulator, and it is the next thing to
do. If they are identical, the leg is exonerated and the difference really is
the two modems — at which point the Courier on 2/3 is the confirming test, for
the *endpoint*, not for the gain.

Nothing here is measured yet beyond the media path and the port-to-extension
mapping. It is a hypothesis with a cheap disproof, recorded as one.

Suite 428.

## Session 198: our INFO1d probing results are a constant, and they are not a shape a real probe produces

The 8403/8405 hypothesis is dead — the two endpoints are configured the same.
Turning to what we transmit, which is the other thing that reaches the peer at
this stage.

INFO1d is 77 payload bits (INFO1d bits 12:88, Table 9/V.90), and the tool's
default report gives the shortest CRC-valid length rather than the spec one, so
these are taken at `--min-bits 77 --max-bits 77`.

### The same numbers on every call, to two different modems

Projected maximum data rate per symbol rate, bits 30:33 of each 9-bit probing
field, where **0 means "the symbol rate cannot be used"**:

```text
   field | cx02 (Conexant) | cx01 (Conexant) | call41 (Courier)
  minpwr |               0 |               0 |               0
  addpwr |               0 |               0 |               0
   MDlen |               0 |               0 |               0
    2400 |              10 |              10 |              10     24000 bit/s
    2743 |               0 |               0 |               0     cannot be used
    2800 |               0 |               0 |               0     cannot be used
    3000 |               0 |               0 |               0     cannot be used
    3200 |              13 |              13 |              13     31200 bit/s
    3429 |               0 |              14 |               0
    foff |               2 |               3 |              -2
```

Three calls, two different modems, three different sessions. Everything but the
3429 field and the frequency offset is **identical**. A probing result is a
measurement of the peer's L1/L2 probe; measurements of two different modems on
two different occasions do not come out bit-for-bit equal.

### And the pattern is not one a line produces

**2400 usable, 2743/2800/3000 unusable, 3200 usable at 31200 bit/s.** A line
that carries 3200 baud carries the narrower symbol rates — they need less
bandwidth. Declaring 3200 good for 31200 bit/s while declaring 2743, 2800 and
3000 unusable is not a result a real probe can return. (2800 alone is
defensible: our own INFO0d bit 13 says we do not support it. 3429 being unusable
is defensible too, being the widest. 2743 and 3000 are not.)

This is what we put on the wire — decoded from the transmit capture with the
CRC-validating demodulator, not read out of the emulator — so it is not an
artefact of how we are looking.

### The probe is arriving, so it is not a starved input

Energy at multiples of 150 Hz against the midpoints between them, in the window
between the INFO0 exchange and INFO1:

```text
              3.4-3.7s   3.7-4.2s   4.2-4.7s   4.7-5.2s
cx02          +26.4 dB   +38.6 dB   +28.4 dB   +40.4 dB
call41        +26.7 dB   +35.3 dB   +26.0 dB   +36.1 dB
```

The V.34 line probe is present and strong on both calls, so the firmware has a
real signal to measure and publishes a constant regardless. The defect is
downstream of the audio: either the probe analysis never runs under this
harness, or its output never reaches the INFO1d builder, or a fixed table
overwrites it.

### What this is and is not

It **is** a real defect in what this project transmits, at exactly the stage
where there is little else to go wrong, and it is in reach of the same backward
walk that worked in Session 193 — find the DM words holding the per-symbol-rate
projected rates, watch their writers, and see whether the probe analysis ever
runs.

It is **not** shown to be why the Conexant declines PCM. Bits 37:39 are a
statement about the *downstream* and these fields describe the *upstream*, and
both modems picked symbol rate 4 (3200) out of this report, so neither was
confused by it in the obvious way. Saying more than that is the mistake of
Sessions 195 and 197. What can be said is that a peer receiving an incoherent
probing report has been given a reason to distrust the digital modem, and that
the report is wrong on its own merits and should be fixed whatever it turns out
to explain.

Suite 428.

## Session 199: [PARTLY WITHDRAWN] the probe *is* measured — the conversion to a projected rate flattens it

> **↩ The "conversion flattens it" reading is withdrawn by Session 200.** The
> experiment proposed at the end of this entry was run with `EICON_FORCE_DM`
> and could not work — writer and consumer are 1,100 cycles apart in the same
> frame. With the pin that can (`EICON_PIN_DM`), the projected rate does not
> respond to `DM(0x0f6d..0x0f72)` anywhere in a 32,768-fold sweep, so it does
> not derive from them and there is no flattening conversion to blame. The
> assembly chain below stands; so does the finding that the probe is measured
> and differs per call.

How the constant of Session 198 happens, walked back the way Session 193 was.
`tools/dm_find.py` (new) replays to a moment and searches DM for a sequence of
words, which is how the message was located without guessing an address.

### The assembly chain

The INFO1d we transmit, MSB-first packed, is `0000 1601 0080 02e0 0800`. Those
words are at **DM(0x0637..0x063B)** at 5.35 s, and the writers walk back in five
hops:

```text
DM(0x0637..0x063B)   the message, bit-reversed in place   PM 0x3b27  (the 16-bit
                                                          reverse loop of 193)
   ^ packed from
DM(0x06fd..0x0706)   the fields, MSB-packed               PM 0x3d63
   ^ derived from
DM(0x0700..0x0705)   per-rate projected rates             PM 0x3e63..0x3e7d
   ^ derived from
DM(0x0f6d..0x0f72)   per-rate measured values             PM 0x38e2 / PM 0x38e6
   ^ read from
DM(0x142f + n*15)    per-symbol-rate probe blocks
```

Nothing here is a hardcoded message. Every stage is a computation.

### And the measurement is real

Snapshot `DM(0x0f6d..0x0f72)` at 5.35 s on both calls:

```text
            0f6d    0f6e    0f6f    0f70    0f71    0f72
  cx02       f3c     f2b     fd4     fd4       c       c
  call41     ef3     eed     fb2     fb2       c       c
```

**They differ.** The probe analysis runs, reads the peer's L1/L2 signal, and
produces different numbers for the two modems — 0xf3c against 0xef3, 0xf2b
against 0xeed, 0xfd4 against 0xfb2. Session 198's "the firmware publishes a
constant" was right about the output and wrong about the cause: the input is
not constant and is not starved.

### Where it goes flat

The measured values span 0xeed..0xfd4 — 3821 to 4052, a **2% spread** — and the
conversion at `PM 0x3e63..0x3e7d` turns everything in that band into the same
answer: 10 for 2400, 0 for 2743, 0 for 2800, 0 for 3000, 13 for 3200, 0 for
3429. Two different lines, one report.

That is the shape of a **scaling error**, not a broken measurement. If these are
quality or SNR figures compared against per-rate requirements, and our figures
land in a narrow band well away from every threshold, then the per-rate
comparisons all resolve the same way on every call and the 2% of real variation
never crosses anything. The physically incoherent pattern of Session 198 — 3200
usable while 2743 and 3000 are not — follows from the same thing: the
comparisons are not landing where the firmware's authors expected them to.

### Why this is worth more than the V.90 question

The projected-rate report is what a peer uses to pick its upstream symbol rate,
so **every modulation that trains against it is training against the same fixed
answer**, which is a candidate for far more than the Conexant. The V.34
answering page stalling at `0x00b0` and V.32 reaching its data state without
running its data interface are both downstream of a handshake that used this
report.

Not established, and it must not be asserted the way Sessions 195 and 197 were:
that this causes the V.90 decline, or the `0x00b0` stall, or anything else. It
is a defect in what we transmit with a located mechanism and an obvious next
experiment.

**Next:** force `DM(0x0f6d..0x0f72)` across a range with
`EICON_FORCE_DM=...@0x0260` and watch `DM(0x0700..0x0705)`. If plausible values
produce a coherent monotonic report — 3429 highest, then 3200, then 3000 — the
measurement's scaling is the bug and the conversion is fine. If nothing moves
the output, the conversion is.

Suite 428.

## Session 200: `EICON_PIN_DM`, and the projected rate does not come from the measured words

Session 199 ended with an experiment: force `DM(0x0f6d..0x0f72)` and watch
`DM(0x0700..0x0705)`. Run with `EICON_FORCE_DM` it produced a clean, flat
negative — no movement at any value from `0x0000` to `0x7fff`.

**That was a null experiment.** `PM 0x38e2` (which writes those words) and
`PM 0x3e63..0x3e7d` (which consumes them) both execute in frame 43298, sample
41645, about 1,100 cycles apart. `EICON_FORCE_DM` writes once per sample before
the page runs, so the firmware overwrote the forced value long before the
conversion saw it. The final snapshot showed the forced value only because the
force ran again on a later sample. This is the third word of this shape —
`DM(0x3FBB)` and `DM(0x170B)` in Session 193 were the first two — and each time
it reads exactly like a negative result.

### The instrument

`EICON_PIN_DM=ADDR=VALUE[,ADDR=VALUE]`, the data-memory twin of
`EICON_PIN_PM`: the store lands and is undone, so anything watching the address
still sees the firmware's value while the memory keeps the pinned one. It
reports its hit count at exit and says so loudly when a pin never fired, because
that is the failure mode above.

### The A/B, now that it runs

Pinning individual words and reading the outputs at 5.40 s:

```text
pin                            DM(0x0f6d..)                out(0x0700..0x0705)
none                    0f3c,0f2b,0fd4,0fd4,000c,000c   0000,0000,000d,0001,0000,0000
0x0f6d=0x0000           0000,0f2b,...                   0001,0000,000d,0001,0000,0000
0x0f6d=0x7fff           7fff,0f2b,...                   0000,0000,000d,0001,0000,0000
0x0f6e=0x0000           0f3c,0000,...                   0000,0000,000d,0001,0000,0000
0x0f6e=0x7fff           0f3c,7fff,...                   0001,0000,000d,0001,0000,0000
both = 0x7fff           7fff,7fff,...                   0001,0000,000d,0001,0000,0000
```

`DM(0x0700)` is `DM(0x0f6d) <= DM(0x0f6e)` — a comparison of two measured
values, and **it tracks them correctly in all six runs**. That word is bit 25 of
the 9-bit probing field: "set to 1 indicates that the high carrier frequency is
to be used". So the carrier selection is a real measurement-driven decision and
it works.

`DM(0x0702)` and `DM(0x0703)` — the projected data rates — **do not move at
all**, across the full representable range, on either input.

### What that changes

A read watch confirms `PM 0x3e64` and `PM 0x3e69` genuinely read
`DM(0x0f6d/0x0f6e/0x0f6f)`, so the link is not imaginary. But those reads feed
the *carrier comparison*, not the rate. Within one 9-bit field, one consumer of
these words responds to the measurement and the other is inert.

So **Session 199's "the conversion at `PM 0x3e63..0x3e7d` flattens the
measurement" is too strong and is withdrawn.** What is established is narrower
and more useful: the projected rate does not derive from `DM(0x0f6d..0x0f72)`
at all, so the constant of Session 198 has its source somewhere else, one hop
further back than the walk had reached. The "scaling error" reading is
unsupported — a scaling error would have moved the rate somewhere across a
32,768-fold sweep.

**Next:** read-watch the inputs of the store at `PM 0x3e7c` (the one that writes
`DM(0x0702)` with `ar=000d`) rather than assuming, or trace frame 43298 and read
where `AR` gets 13. The instrument for the next A/B now exists either way.

Suite 428.

## Session 201: the rate is `AX0 - (DM(0x0DFF) - 14)`, and four of the six rates produce *exactly* the same value

Tracing frame 43298 gives the computation Session 200 said was one hop further
back. `PM 0x3e63..0x3e7c` is a per-symbol-rate loop, and its tail is:

```text
3e74  7800a1  DM(I4,M5) = AR        ; high-carrier bit  (the working half)
3e75  780031  DM(I4,M5) = MX1       ; pre-emphasis
3e77  80dff5  AY1 = DM($0DFF)
3e78  4000e1  AX1 = $000E
3e79  27290f  AF = AY1 - AX1        ; AF = DM(0x0DFF) - 14
3e7a  22f00f  AR = AX0 - AF         ; rate = measured - offset
3e7b  221804  IF LT AR = 0          ; clamp: "this symbol rate cannot be used"
3e7c  7800a1  DM(I4,M5) = AR        ; the projected rate
```

### The pre-clamp values are the finding

`AR` at `0x3e7a`, before the clamp, for the six iterations:

```text
 iter   symbol rate   pre-clamp   stored
   1        2400          10        10
   2        2743          -2         0
   3        2800          -2         0
   4        3000          -2         0
   5        3200          13        13
   6        3429          -2         0
```

**Four different symbol rates produce exactly −2.** Not −1 and −4 and −7, which
is what six independent measurements landing near a threshold would look like —
the same value, four times. `AX0` is identical on all four iterations, so those
rates are not being measured separately at all: two of six get a real value and
the rest share one. The incoherent shape of Session 198 is that, seen from the
outside, and it is one missing quantity rather than six bad ones.

The clamp itself is correct firmware doing what Table 9/V.90 says: 0 means the
symbol rate cannot be used.

### Is it our doing? Not through any write we make

The shim writes exactly these DM addresses:

```text
0x03EF 0x0491 0x0554 0x1FF7 0x204E 0x20BA 0x2F22 0x3131 0x3132 0x31EE 0x32F0
0x32F6 0x3763 0x3764 0x3995 0x3999 0x3EEE 0x3F05 0x3F08 0x3F0F 0x3F62 0x3F89
0x3F9B 0x3FB0 0x3FB4 0x3FBC 0x3FBD  (plus the 0x3EE0 write-database block)
```

None of them is in this chain, and `DM(0x0DFF)` — the offset — is firmware
scratch, rewritten by `PM 0x38ea` in its own loop (`000c, 000c, 000d, 000d,
000e, 000e, 000e …`). This matters because **host writes bypass `WWORD_DATA` and
so are invisible to every write watch in this log**: "the shim does not do it"
had to be established by reading the shim, not by watching.

What is *not* cleared is the harness's per-frame instruction budget. If the
probe analysis at `PM 0x38cb..0x38ea` needs more cycles than a frame is given,
some rates would keep a default while others get a real value — which is the
shape observed. That is ours, and it is untested.

### ↩ And a correction to Session 198

"The results are identical across three calls to two different modems, therefore
they are not a measurement" is weaker than it was written. **Both calls traverse
the same path** — same VG224 port, same Asterisk, same codec, same RTP — and the
peers send standardised L1/L2 probe tones. Two modems measured over an identical
channel *should* come out close, and the raw values differing by 2% is
consistent with that rather than evidence against it.

The anomaly that survives is not the cross-call constancy. It is the four
identical −2s inside a single call.

**Next:** find what `AX0` is on iterations 2, 3, 4 and 6, and why it is the same
on all of them — read-watch the `DM(I0,M1)` and `DM(I1,M1)` fetches at
`PM 0x3e63..0x3e6c` and see which addresses they walk, then check whether the
per-rate probe blocks at `DM(0x142f + n*15)` are populated for all six. Test the
budget hypothesis by raising `self.adsp_budget` for the probe frame alone.

Suite 428.

## Session 202: [PARTLY WITHDRAWN] the budget is not it, and the rate comes from a second array that is mostly zeros

> **↩ "a second array that is mostly zeros" is withdrawn by Session 203.** The
> exec watch dumps registers before the instruction runs, and `AX0` is written
> twice in that loop, so the column read as "the array value" was `PM 0x3e73`'s.
> The array at `DM(0x0f71..)` is fully populated with a coherent monotonic set.
> What is zero is the **enable mask** at `DM(0x0f8b..0x0f90)`, written
> deliberately. The budget half of this entry stands, control and all.

### The budget hypothesis is dead, with a control

Frame 43298 — the one that computes the whole report — runs **6,586 cycles of a
20,000 allowance** and reaches IDLE. It was never budget-bound, so raising
`EICON_ADSP_BUDGET` cannot help, and it does not:

```text
budget    20000 / 60000 / 200000     rates 10,0,0,0,13,0   (identical)
budget    12000 / 10000              rates 10,0,0,0,13,0   but the *measured*
                                     words move: 0efe,0eec,0f92 against
                                     0f3c,0f2b,0fd4 at 20000
budget    8000 and below             RuntimeError: native TIKRNL did not
                                     consume answer WDB — setup breaks first
```

The 10000/12000 rows are the positive control this needs: the knob demonstrably
changes what the firmware measures, and the projected rates still do not move.
That is now the **third** independent demonstration that the rates do not track
the measurement — after the pin sweep of Session 200 and the cross-call
comparison of 198.

### The rate comes from an array nobody had looked at

Exec watches on `PM 0x3e66`/`0x3e6b` print the index registers, and the loop
walks **two** arrays in parallel, not one:

```text
pc=3e66 i0=0f66 i1=0f71 ax0=f1fd      <- stale, pre-load
pc=3e6b i0=0f67 i1=0f72 ax0=000c
pc=3e66 i0=0f68 i1=0f73 ax0=000c
pc=3e6b i0=0f69 i1=0f74 ax0=0000
pc=3e66 i0=0f6a i1=0f75 ax0=0000
pc=3e6b i0=0f6b i1=0f76 ax0=0000
pc=3e66 i0=0f6c i1=0f77 ax0=0000
pc=3e6b i0=0f6d i1=0f78 ax0=0000
pc=3e66 i0=0f6e i1=0f79 ax0=0000
pc=3e6b i0=0f6f i1=0f7a ax0=000f
pc=3e66 i0=0f70 i1=0f7b ax0=000f
pc=3e6b i0=0f71 i1=0f7c ax0=0000
```

`I0` walks from **0x0f66** and `I1` from **0x0f71**. `AX0` — the term the rate
is computed from — is loaded from `DM(I1,M1)`, the *second* array. Sessions
199–201 chased the `I0` array at `0x0f6d..0x0f72`, which is why pinning it moved
the high-carrier bit (that comparison does read it) and never moved the rate.

And the `I1` array is **mostly zeros**, with a handful of populated entries:
`000c`, `000c`, then zeros, then `000f`, `000f`. Against
`rate = AX0 - (DM(0x0DFF) - 14)` with the offset measuring 2:

```text
AX0 = 0x000c  ->  12 - 2 = 10    the 2400 report
AX0 = 0x000f  ->  15 - 2 = 13    the 3200 report
AX0 = 0x0000  ->   0 - 2 = -2    clamped to 0, "cannot be used"   x4
```

Every number in Session 198's incoherent report is accounted for exactly. **The
arithmetic is correct throughout; the input array is unpopulated.** The four
identical −2s of Session 201 are four zeros, not four measurements.

### What is now the question

`PM 0x38e6` was seen writing `DM(0x0f71)` and `DM(0x0f72)` — two entries — and
the consumer reads twelve. So: what should fill `DM(0x0f73..0x0f7c)`, and why
does it not run here? That is the whole defect, and it is one write watch on
the range plus a trace of whatever should be walking it.

Still not established: that any of this causes the Conexant to decline PCM.
It remains a defect in what we transmit, now with its mechanism pinned down to
one unpopulated array.

Suite 428.

## Session 203: nothing is missing — the mask is set deliberately, and four symbol rates are switched off on purpose

### ↩ Session 202 was wrong about which array is zero

The exec watch dumps registers *before* the instruction executes, and `AX0` is
written twice in the loop — at `PM 0x3e66` from the array and again at
`PM 0x3e73` from `AY0`. Reading the `ax0` column as "the value loaded from the
array" therefore attributed `0x3e73`'s value to the array. It is not the array.

Watching `DM(0x0f71..0x0f7c)` for the whole call settles it: **every entry is
written, once, by `PM 0x38e6`, with a coherent monotonic set** —
`000c 000c 000d 000d 000e 000e 000e 000e 000f 000f` — and none of them is ever
written again. `DM(0x0f75)` takes `000e` at cyc 109008860 and is never touched
after. The array is fine. There is nothing missing to fill.

### It is the enable mask

Reading the registers at the multiply itself (`PM 0x3e6c`, `AR = SR0 AND AY0`):

```text
SR0=000c  AY0=ffff  -> 000c -> rate 10     2400
SR0=000d  AY0=0000  -> 0000 -> clamped     2743
SR0=000e  AY0=0000  -> 0000 -> clamped     2800
SR0=000e  AY0=0000  -> 0000 -> clamped     3000
SR0=000f  AY0=ffff  -> 000f -> rate 13     3200
SR0=0010  AY0=0000  -> 0000 -> clamped     3429
```

`SR0` — the measured per-rate value — is good on every iteration. `AY0`, the
mask fetched from `DM(I6,M5)`, is what zeroes four of them.

### And the mask is written on purpose, in straight-line code

```text
3911  7800ad  DM(I7,M5) = AR         ; loop: fills 0x0f8a..0x0f94 with ffff
3912  4ffff0  AX0 = $FFFF
3913  90f890  DM($0F89) = AX0        ; enabled
3914  90f8a0  DM($0F8A) = AX0        ; enabled          -> 2400
3915  94f8b4  DM($0F8B) = M0         ; M0 = 0, disabled -> 2743
3916  94f8c4  DM($0F8C) = M0
3917  94f8d4  DM($0F8D) = M0         ; disabled         -> 2800
3918  94f8e4  DM($0F8E) = M0
3919  94f8f4  DM($0F8F) = M0         ; disabled         -> 3000
391a  94f904  DM($0F90) = M0
391b  82408a  AR = DM($2408)
391c  4ffe94  AY0 = $FFE9
391d  22e20f  AR = AR - AY0          ; DM(0x2408) + 23
391e  1b9224  IF LT JUMP $3922
391f  4ffffa  AR = $FFFF
3920  90f92a  DM($0F92) = AR         ; re-enabled       -> 3200
3921  90f91a  DM($0F91) = AR
3922  83fc9a  AR = DM($3FC9)
3923  401184  AY0 = $0118
3924  22e20f  AR = AR - AY0
3925  0a0004  IF LT RTS
```

The shape is **disable-then-selectively-re-enable**: `PM 0x3911` turns every
rate on, `PM 0x3915..0x391a` unconditionally turns six entries off by storing
`M0` (zero — the conventional zero register on this family, used here instead of
loading a constant), and the conditional blocks that follow turn specific ones
back on. Only the `DM(0x2408)` test at `0x391b..0x3921` fired, re-enabling
`0x0f91/0x0f92` — which is 3200.

So the four "cannot be used" rates are not a measurement failure, an
unpopulated array, a starved probe or a scaling error. They are **switched off
by firmware that always switches them off**, and then not switched back on
because the conditions that would do so did not hold.

### What the question is now

Not "what should fill the array" — the array is full and correct. It is: **which
of the re-enable conditions after `PM 0x391a` should have fired, and on what?**
The visible inputs are `DM(0x2408)` (fired), `DM(0x3FC9)` (compared against
`0x0118`; `0x0159` on the Conexant call and `0x011e` on the Courier's — one of
Session 191's twenty words, and it is *not* one that was eliminated by the joint
force, since that test covered `0x3f..` words at the INFO page and this is a
different consumer), and `DM(0x16E6)` at `0x3908`.

That is a bounded read: trace `PM 0x3922` onward through the rest of the
re-enable chain and record which test rejects 2743, 2800 and 3000.

Still not established that any of this bears on the Conexant declining PCM.

Suite 428.

---

## Session 216: the Conexant control exonerated the Eicon implementation, not the shared media harness

Session 195's statement “the rig is exonerated” was too broad. Its independent
spandsp server shared the exact components now under suspicion: Asterisk's
anchored RTP bridge and VG224 port 2/3. It proves that the Eicon INFO parser did
not invent mode 4 and that this DSP implementation's waveform is not uniquely
responsible. It does **not** prove that the shared IP/FXS bearer is PCM
transparent.

### Two endpoint-side candidates are now dead

First, three calls negotiated a PCMA-only endpoint instead of the PCMU used by
every prior run. The endpoint's own transmit capture contained a CRC-valid
77-bit INFO1d, so this was a functioning companding path, not a codec mismatch
that silently corrupted INFO. All three calls still sent INFO1a mode 4 and
loaded V.34 (`0x0261`).

Second, the sparse INFO1d report was changed causally. Pinning
`DM(0x0f8b..0x0f90)=0xffff` kept 2743/2800/3000 enabled; every pin fired eight
times per call, and independent demodulation of the transmitted G.711 showed a
different CRC-valid INFO1d (`msb-first 0000 141a 0301 82e0 e400`, versus the
usual `0000 1601 0080 02e0 ...`). The Conexant still selected V.34 in 3/3
answered calls. It retrained repeatedly afterwards, which says the fabricated
report was not benign, but it did not change the decision under test.

Thus neither PCMU-vs-PCMA nor the disabled projected-rate fields explain mode
4. No more INFO1d field forcing is justified without a field-specific
hypothesis.

### What the SIP/RTP audit actually shows

The live INVITE for VG224 2/3 is:

```text
m=audio 18754 RTP/AVP 0 8 101
 a=rtpmap:0 PCMU/8000
 a=rtpmap:8 PCMA/8000
 a=rtpmap:101 telephone-event/8000
```

There is **no `X-NSE`/Cisco NSE payload** for modem passthrough. A second call
instrumented all non-audio RTP payloads and received none: no hidden NSE event
arrived after ANSam either. Every media packet in the capture is between this
host (`192.168.88.167`) and **Asterisk (`192.168.88.122`)**, with Asterisk-owned
SSRCs and ports. The VG224 and endpoint are not direct-media peers.

That is the first positive harness-side finding. The bearer used by the only
modem that declines PCM has:

1. an Asterisk RTP timing domain in the middle;
2. no negotiated modem-passthrough signalling;
3. an unknown VG224 jitter-buffer/echo-canceller/modem-passthrough state.

V.34 surviving this is expected; V.90's downstream decision is specifically a
PCM-transparency test. Both Couriers that request mode 6 are on other media
paths (AudioCodes L1/6311 and caller 7800), so they are not controls for this
bearer. The spandsp run is not a control for it either, because it used the same
8403/Asterisk/VG224 leg.

This does not yet prove whether the destructive component is Asterisk's relay,
the VG224 configuration, or the physical port. It does restore the harness as
the leading cause and narrows the decisive tests:

- enable/directly verify modem passthrough on VG224 2/3, including NSE or the
  equivalent IOS configuration;
- make 8403 direct-media if the gateway and endpoint permit it, then verify RTP
  no longer terminates at `192.168.88.122`;
- capture both Asterisk RTP legs and compare G.711 payloads and sample slips,
  rather than inspecting only the endpoint leg;
- or move the CX93001 to either known-V.90 FXS bearer. A mode-6 result there
  would settle the shared-harness question in one call.

The SIP endpoint now records the offered media formats on every INVITE and logs
the first ignored RTP payload type. Those are controls for the eventual NSE or
direct-media test; on the present route they positively report its absence.

---

## Session 217: B5 is already selected, and the VG224 proves modem passthrough never activates

The VG224 console is `/dev/cu.usbserial-630`, 9600 baud. Read-only inspection
settles both configuration questions.

### The Conexant is already in the US country profile

```text
ATI5       B5
AT+GCI?    +GCI: B5
AT+GCI=?   (...,B3,B4,B5,B7,...)
AT+MS?     V90,1,300,56000,300,33600
```

`B5` is supported and active; this is not a modem left in a New Zealand or
unknown country profile. There is no country-code change left to test.

### The VG224 is configured for u-law and NSE — in configuration

`show voice port 2/3` reports:

```text
Companding Type is u-law
Region Tone is set for US
Out Attenuation is Set to -6 dB
Echo Cancellation is enabled
Non Linear Processing is enabled
Echo Cancel Coverage is set to 128 ms
Playout-delay Mode is adaptive (nominal 60 ms, max 1000 ms)
```

The active outbound VoIP peer is also explicit:

```text
dial-peer voice 8999 voip
 destination-pattern .T
 modem passthrough nse codec g711ulaw
 dtmf-relay rtp-nte
 playout-delay maximum 200
 playout-delay nominal 80
 playout-delay minimum low
 codec g711ulaw
 fax protocol pass-through g711ulaw
 no vad
```

So Session 216's “maybe modem passthrough is not configured” branch is closed:
it **is configured on the VG224**. The question is whether it activates through
Asterisk.

### Live state: it does not activate

During a valid CX93001 call, 12 seconds after dial, the VG224 reports:

```text
DSP 001/02   g711ulaw   busy   voice-port 2/3
Tele 2/3 ... g711ulaw  noise:-50  acom:9  i/o:-23/-36 dBm
IP 192.168.88.122:18944 ... delay:35/35/95 ms g711ulaw
media control received: n/a
```

The detailed call display has fields for `MODEMPASS`/`MODEMRELAY`, but publishes
no MODEMPASS state for this call. `show voice port 2/3` simultaneously still
reports echo cancellation, NLP and adaptive playout enabled. The call remains
an ordinary G.711 voice call. It never enters the configured modem-passthrough
mode before the Conexant sends INFO1a mode 4.

This aligns exactly with the SIP-side observation: the VG224's NSE-configured
leg terminates at Asterisk, while Asterisk originates a new leg offering only
PT 0/8/101 (PCMU, PCMA, telephone-event), with no `X-NSE`; no non-audio RTP
arrives at the endpoint. The NSE state transition cannot traverse the anchored
bridge as currently negotiated.

**This is now a demonstrated harness defect, not just a physical-path
confound.** The Conexant performs Phase 2 through an active echo canceller,
non-linear processor and adaptive jitter buffer, then correctly declines PCM.
Session 195's independent software server necessarily got the same answer
because those impairments are upstream of either server implementation.

The corrective A/B is infrastructure-side: bypass Asterisk media/direct-media,
make NSE traverse both SIP legs, or temporarily disable EC/NLP/adaptive playout
on 2/3. No firmware or AT patch should be tested until one of those makes the
VG live call display MODEMPASS (or at minimum shows those DSP functions off).

---

## Session 218: disabling the VG224 echo canceller and NLP is not enough

With approval, VG224 port 2/3 was changed in running configuration only:

```text
voice-port 2/3
 no echo-cancel enable
 no non-linear
```

A readback before the calls and a live readback during call 1 both confirmed
`Echo Cancellation is disabled` and `Non Linear Processing is disabled`.
Adaptive playout was deliberately left unchanged, so this A/B tests only the
two named DSP functions.

Four valid answered CX93001 calls, one persistent SIP registration:

```text
call       1       2       3       4
page      0261    0261    0261    0261
max Trn   00b2    00b2    00b2    00b0
```

**0/4 selected V.90.** EC/NLP alone are therefore not sufficient to explain
INFO1a mode 4. The result does not exonerate the bearer: the call still used
Asterisk-anchored RTP, still had no NSE/MODEMPASS transition, and `show voice
port 2/3` still reported adaptive playout.

The cleanup path restored and verified the original state:

```text
Non Linear Processing is enabled
Echo Cancellation is enabled
Playout-delay Mode is adaptive
```

Next infrastructure A/Bs are now separate and ordered: fixed playout on 2/3,
then direct media/NSE traversal. Neither should be conflated with the completed
EC/NLP test.

---

## Session 219: `+MS=V90,0` is the real hard force; it makes the Conexant abort, not request PCM

Session 215 called `+MS=V90,1,...` an explicit V.90 pin. That was wrong: the
second parameter is automode, and `1` permits fallback. The actual hard setting
is accepted and retained:

```text
AT+MS=V90,0,300,56000,300,33600   OK
AT+MS?                             V90,0,300,56000,300,33600
```

The nearby S-register audit found no second hidden control:

```text
S37=0       S38=20
S109? ERROR S110? ERROR
+GCI=B5
```

Three valid answered calls were run with VG224 2/3 EC and NLP disabled. Every
call's INFO1a still requested V.34 and the Eicon correctly loaded `0x0261`:

```text
call       1       2       3
max Trn   00b2    00c0    00b0 (then retrain)
```

The modem returned `NO CARRIER` on all three. Its retained `#UD` confirms what
hard force means here: transmit and receive carrier V.34, initial/final 9600,
then termination cause `0x2c` (setup timer). It does not change the Phase-2
measurement or make INFO1a claim PCM transparency; it refuses to complete the
V.34 fallback that mode 4 requested.

So `+MS=V90,0` is a useful policy control and a clean negative, not a route to
page 14. There is no supported `S109`/`S110` override on this firmware, and
inventing another S-register cannot repair the line condition the modem is
reporting.

At the operator's request, EC and NLP were left disabled in the VG224 **running
configuration** after the batch; no `copy running-config startup-config` was
issued. Readback confirms both disabled and adaptive playout still enabled.
The remaining causal A/B is fixed playout/direct media, not another modem
selection command.

---

## Session 220: a 34,667 minimum downstream rate still cannot make INFO1a request PCM

The requested `34333` is not a V.90 rate step and the CX93001 rejects it with
`ERROR`. It accepts both neighboring standard steps, 33,333 and 34,667. The
actual A/B used the next step above the requested threshold:

```text
AT+MS=V90,0,300,33600,34667,56000
                        ^^^^^ minimum downstream receive rate
```

This also corrects the parameter reading: the first pair is the analogue
modem's upstream range (capped at 33,600 for V.90); the second pair is its V.90
downstream receive range. The setting was accepted before every call.

Three valid answered calls with VG EC/NLP still disabled all sent INFO1a mode 4
and loaded V.34 page `0x0261`, never V.90 page `0x026a`. All ended `NO CARRIER`.
The retained diagnostics remained V.34 at 9600 in both directions with setup
timer expiry `0x2c`.

One V.34 retry reached numeric `TrnProgress 0x00ea`; that is not a V.90 success
and is a useful warning against classifying calls by `max(TrnProgress) >= 0xd0`
without requiring page 14. The page history and modem result are authoritative.

Changing the permitted V.90 rate range therefore does not affect the earlier
binary decision that PCM operation is unavailable. Rate bounds are consulted
only if that decision succeeds. This closes the modem rate/S-register family;
the remaining work is the bearer timing/passthrough A/B.

---

## Session 221: the V.8 JM does include digital PCM — the summary decoder selected a false short candidate

The first pass over `vpcm_decode --v8` appeared to find a wire-level defect. Its
summary reported this on cx01 and all three Session 220 calls:

```text
C1 65 13 94 8D F1   PCM unavailable
```

That result is false. Running the same decoder with `--verbose` exposes its
independent soft bit-level candidates. In every supposedly bad capture it
recovers the actual repeated JM:

```text
C1 65 13 94 8D 47 FF F0
                  ^^ digital PCM available
```

The exact `C1_65_13_94_8D_47` sequence occurs 1014, 1032 and 1011 times in the
three Session 220 outbound captures, and 1014 times in archived cx01. Those are
multi-phase search hits rather than independent transmitted repetitions, but
they leave no ambiguity about the octet string. The non-verbose summary path
selected a shorter/misaligned candidate ending in `F1`, labelled it “stable,”
and parsed the absent category as PCM unavailable. Archived cx02 happened not
to trigger that summary-path error.

The incoming CX CM is also valid and includes analogue PCM:

```text
C1 65 13 94 2A 0D 27
```

Therefore the complete V.8 exchange is compatible and correct: the CX offers
analogue PCM and the Eicon responds with digital PCM. `DM(0x3f09)=0xb13f` was
not sufficient proof by itself, but its Session 191 conclusion survives direct
wire decoding. V.8 does **not** explain INFO1a mode 4. The earlier claim in this
session that live timing caused the card to omit `0x47` is withdrawn; live
versus replay only changed which bad summary candidate was selected.

The defect is in `vpcm_decode` candidate arbitration: its main V.8 probe accepts
the short SpanDSP-derived payload while the verbose soft decoder has the longer
repeated payload containing `0x47`. Future V.8 checks must inspect the verbose
raw candidate (or fix that arbitration) before treating the summary's
“PCM unavailable” as evidence.

---

## Session 222: disabling the Conexant's dual-PCM detector changes INFO1a from V.34 to V.90

The CX930xx command reference `REM-201692C`, supplied with the StarTech
USB56KEMH2, exposes the vendor diagnostic that the V.90 Recommendation does
not. `S202` is a bit-mapped diagnostic register; bit 5 is documented as
**Disable dual PCM detection**. The attached CX accepts the register and reads
back its writes.

A baseline and two intervention calls used the same hard V.90 setup and the
same persistent endpoint registration:

```text
AT+MS=V90,0,300,33600,34667,56000

S202=0    INFO1a mode 4   loaded 0x0261 (V.34)
S202=32   INFO1a mode 6   loaded 0x026a (V.90)
S202=32   INFO1a mode 6   loaded 0x026a (V.90)
```

This is the first intervention that moves the CX's binary Phase-2 choice. The
CX's proprietary **dual PCM detector is what rejects normal V.90 on this
bearer**. V.8 is correct, rate forcing cannot override the detector, and the
standard does not specify this vendor acceptance policy.

The override only solves page selection. Both V.90 calls reached the known DIL
family and stopped at `TrnProgress/state 0x00b3`; they therefore also make the
CX usable as a page-14 DIL peer for the first time, but do not solve the Eicon
DIL defect.

`AT&V1` is supported and reports handshake states, EQM, robbed-bit pattern,
digital loss and rate drops. Because these forced calls were locally aborted
after enough time to score the page, most quality fields remained sentinel
values. The useful differences were highest RX/TX states `60/63` at baseline
and `62/64` with the detector disabled. The direct page transition is stronger
than those incomplete statistics.

Interpretation must remain precise: this proves what the **CX detector thinks**,
not yet which component causes that classification. “Dual PCM” ordinarily
means a tandem PCM conversion or a signal that has the same observable damage.
Asterisk anchoring, VG224 playout/sample adjustment, or another codeword/sample
transformation can create that signature. It does not make NSE modem
passthrough the preferred fix.

`S202` was restored to `0`; the endpoint deregistered and stopped. Artifacts are
under `artifacts/interop/cx-dual-pcm/`.

---

## Session 223: another analogue line passes the CX dual-PCM test without an override

The CX was physically moved from VG224 port 2/3 to the line presented as
`AudioCodes L3` / extension 6313. Two calls used the same Asterisk endpoint,
hard-V.90 rate command, and the native `S202=0` dual-PCM detector setting.
Neither needed the Session 222 override:

```text
line                         S202   page choice   result
VG224 2/3 / extension 8403      0   0261 / V.34  dual-PCM rejection
AudioCodes L3 / extension 6313  0   026a / V.90  DIL 00b3
AudioCodes L3 / extension 6313  0   026a / V.90  data state 00d0
```

The second call negotiated 42,667 bit/s downstream and 7,200 bit/s upstream.
The CX's own `AT&V1` corroborates V.90 on both calls. The second report was:

```text
LAST/HIGHEST RX rate  42667 / 42667
LAST/HIGHEST TX rate   7200 / 7200
Line QUALITY             035
Rx LEVEL                  022 dBm
EQM Sum                  00EB
RBS Pattern                00
Rate Drop                  00
Digital Loss             2000
V90
```

This localizes the normal-mode rejection to the original VG224 line/path, not
the CX, Eicon V.8/INFO exchange, or shared Asterisk endpoint. Whatever the
Conexant calls “dual PCM” is genuinely path-dependent. The AudioCodes result
also makes NSE modem passthrough unnecessary for V.90 selection: this line
selects and trains V.90 as ordinary audio.

The two calls also extend the DIL lottery to the CX: one `0x00b3`, one
`0x00d0`. The second is the first normal-detector CX call to cross DIL and
confirms that disabling dual-PCM detection on the original line did not merely
fabricate an impossible mode.

`S202` remains restored at `0`; the endpoint deregistered and stopped. Artifacts
are under `artifacts/interop/cx-dual-pcm-other-line/`.

---

## Session 224: extension 7802 also passes dual-PCM detection normally

A third physical line, presented as extension 7802, was tested twice with the
same hard-V.90 setup and native `S202=0`. Both calls selected mode 6 and loaded
page `0x026a`; both subsequently drew the DIL `0x00b3` stall.

```text
line/identity       S202   V.90 selection
VG224 2/3 / 8403       0   0/normal calls; mode 4
AudioCodes L3 / 6313   0   2/2; one reached 0x00d0
third line / 7802      0   2/2; both reached DIL 0x00b3
```

The `AT&V1` quality fields remained sentinel values on 7802 because neither
call crossed DIL before local termination. The page choice is nevertheless
unambiguous. Two independent alternate lines now pass the CX detector without
an override, making the defect specific to the original 8403/VG224 path rather
than a generic property of Asterisk-carried analogue calls.

`S202` remains `0`; the endpoint deregistered and stopped. Artifacts are under
`artifacts/interop/cx-dual-pcm-third-line/`.

---

## Session 225: 7802 selects V.90 but does not connect

Two additional 7802 calls were allowed to run to the modem's own terminal
result rather than being stopped after page scoring. Both selected V.90, both
stalled in Eicon DIL state `0x00b3`, and both ended `NO CARRIER` after about 52
seconds. The CX reported `S86=22` (no connection established), highest RX/TX
states `62/64`, and no valid rate or digital-loss measurement.

Together with Session 224, extension 7802 is now 4/4 for normal V.90 selection
but 0/4 through DIL. It passes the CX dual-PCM detector but does **not** provide
a completed modem connection. The AudioCodes call that reached Eicon state
`0x00d0` was locally stopped before the CX printed `CONNECT`, so it too is only
a server-side data-state result, not yet a confirmed end-to-end CX connection.

`S202` remains `0`; the endpoint deregistered and stopped. Artifacts are under
`artifacts/interop/cx-third-line-connect/`.

---

## Session 226: unloaded and 40 ms-lag batches still cannot connect on 7802

Four more full calls tested whether instrumentation load or the previously
best Courier delay explained 7802's DIL failures:

```text
no V90D hot trace, 0 ms lag   0/2 CONNECT; both 0x00b3
no V90D hot trace, 40 ms lag  0/2 CONNECT; both 0x00b3
```

The CX again ended `NO CARRIER`; `S202` remained zero. Across Sessions 224-226,
7802 is now 8/8 for V.90 page selection and 0/8 for a modem `CONNECT`, every
failure at the same Eicon DIL state. Removing trace overhead and applying the
40 ms setting that gave Couriers 6/8 did not move it.

A connection is therefore not presently obtainable on 7802 by retrying or by
the known delay A/B. The best next physical target is AudioCodes L3/6313,
where one of two calls already reached server data state `0x00d0`; it needs a
full, un-aborted call to establish whether the CX prints `CONNECT`.

Endpoints deregistered and stopped. Artifacts are under
`artifacts/interop/cx-third-line-connect-clean/` and
`artifacts/interop/cx-third-line-connect-lag40/`.

---

## Session 227: CX diagnostic states show a Phase-2 restart after the Eicon DIL stall

A dedicated 7802 call enabled `S202=20`: bit 2 prints live RX/TX data-pump
states and bit 4 prints the private `&V2` block. `+MR=2` was also enabled. The
complete state stream was:

```text
R00 T00 R01 T02 T03 R02 T04 T05
R20 T20 T21 R21 R22 T22 R23 T23 R24 T24 R25 T25 R26 T26 T27
R27 T28 R28 R29 R2C T2A R2E T2C R2F
T40 T44 T45 T46 T47 R41 T48 R42 R43 R45 T60 R46
R61 T61 R62 T62 R49 R61 T63 T64
R20 T20 R21
```

The Eicon selected V.90 and stopped at `0x00b3` at 14.080 s. The CX reaches its
reported maxima RX state 62 / TX state 64, then falls back to the earlier
`R20/T20/R21` sequence rather than reaching a connect state. It finally reports
`NO CARRIER`, `S86=22` (no connection established). No `+MCR/+MRR` result is
emitted, so the modem never reaches the point at which it declares a negotiated
modulation/rate to the DTE.

The automatic `&V2` block was emitted twice and was byte-identical. A direct
post-call `&V2` differed only in six undocumented two-letter fields (`ga`,
`ia`/`ib`/`ic`, `kl`, `oe`); the reference manual supplies no mapping for them,
so assigning semantics would be invention. Most documented `&V1` quality
fields remain sentinels because training never completes.

This diagnostic corroborates the endpoint trace rather than revealing a second
CX-side rejection: after choosing V.90, the CX advances through its handshake,
waits while the Eicon is stuck in DIL, and restarts Phase 2. 7802 is now 9/9
selecting V.90 and 0/9 for `CONNECT`.

`S202` and `+MR` were restored to zero; the endpoint deregistered and stopped.
Artifacts are under `artifacts/interop/cx-third-line-s202-diag/`.

---

## Session 228: the successful CX call runs a missing DIL work initializer

The AudioCodes call that reached live `0x00d0` and a 7802 `0x00b3` call form a
same-modem good/bad pair. Their inbound streams were extracted by RTP SSRC from
the aggregate pcaps rather than split at outbound sample counts. Open-loop
replay preserves the decisive divergence.

Both enter output state `0x00b3`. After that, only the good call's scheduler at
PM `0x2a93..0x2a97` indirectly calls PM `0x3f73`. PM `0x3f73..0x3f7a` finishes
a record unpack and branches to PM `0x3fb2`; that routine copies `8 * AR` words
into the work area beginning at DM `0x24f4`. During this copy it publishes:

```text
good: DM(0x2f4f) = 0x070d   writer PM 0x3fb8
```

Later good-path callbacks write `0x00d0` and then `0x0001` to the same word.
The bad call never executes PM `0x3f73` or `0x3fb2`. Its first write to
`DM(0x2f4f)` is therefore the known consumer at PM `0x055f` subtracting from
zero:

```text
bad: 0x0000 -> 0xffff -> 0xfffe -> ...
```

That is why foreground never returns and training cannot advance: **the bad
call consumes the DIL work record without running the initializer that the good
call runs first.** This is now demonstrated on the same CX rather than inferred
from Courier captures.

The scheduler callback stream gives the remaining boundary. Near the seam the
good replay eventually dispatches target `0x3f73`; the bad replay continues
through `0x2acb/0x2ad7/0x2ae8` and does not. Several values in the surrounding
record differ with the line waveform, but forcing the superficially matching
low-memory word `DM(0x0006)=0xff73` does not cause the callback or move the bad
replay. The missing dispatch is not fixed by fabricating that one descriptor
field.

So the large mystery is narrower but not closed: determine which record
condition enqueues PM `0x3f73`, and why the AudioCodes waveform satisfies it
while 7802 does not. The fault is before the empty subtraction, in work-record
production/scheduling, not in the CX waiting logic or the subtract loop itself.

`v90_dpcm_replay.py` now accepts repeatable `--watch-exec ADDR` alongside its
DM-write watches, which exposed the indirect callback and prior PC trail.

---

## Session 229: correction — the decisive dispatch is delay-derived PM 0x0375, not a missing PM 0x3f73 initializer

Deeper tracing corrects Session 228's interpretation. PM `0x3f73` is the middle
of an unrolled record unpacker, not a generally required DIL initializer. The
scheduler deliberately switches `DM(0x201b)` from callback group 16 to group 0
when the table condition at DM `0x0046` matches mask `0x0200`. Both the good and
bad calls make that identical switch through PM `0x244d..0x2457 -> 0x2b23`.
The difference is the generated group-0 callback table itself.

PM `0x1982..0x19a6` builds that eight-word table at DM `0x0000..0x0007`.
The decisive first word is:

```text
AudioCodes good: DM(0x0000) = 0x022b
7802 bad:        DM(0x0000) = 0x0375
```

The scheduler eventually dispatches that word as a PM address. PM `0x022b` is
a bounded block-copy loop and returns. PM `0x0375` enters the DIL helper in its
middle; it reaches PM `0x0555` with empty entry 6 and starts the known
`DM(0x2f4f)` underflow. This is the direct producer/consumer divergence.

The callback address is not random. PM `0x3232..0x323b` constructs it from the
DPCM value at DM `0x3fcb`, and PM `0x1a0f..0x1a13` applies the final offset. In
these two calls DM `0x3f04` is `0x000c`, giving:

```text
DM(0x0000) = DM(0x3fcb) + DM(0x3f04) + 5

good: 0x021a + 0x000c + 5 = 0x022b
bad:  0x0364 + 0x000c + 5 = 0x0375
```

Two causal replay A/Bs remove the runaway:

1. forcing only `DM(0x0000)=0x022b` on the bad recording; and
2. forcing upstream `DM(0x3fcb)=0x021a`, which naturally generates `0x022b`.

Both let foreground return and advance the output script within `0x00b3` from
`0x1a46` through `0x1a4f` to `0x1a55`. Open-loop media cannot prove a completed
call after changing the server response, but the empty-entry runaway is gone.
Forcing only `DM(0x0006)=0xff73` did not help, disproving the previous
single-callback explanation.

The remaining root question is now earlier and more concrete: why the firmware
produces DPCM value `0x0364` on 7802 and `0x021a` on AudioCodes, and whether the
computed jump to PM `0x0375` exposes a firmware precondition, an emulated
arithmetic error, or a legitimate delay-bin path whose work record should have
been initialized elsewhere. A production fix must preserve the measured line
parameter; hard-coding the AudioCodes value is diagnostic only.

---

## Session 230: DM 0x3fcb is scaled INFO elapsed-time carryover, not RTDelay

The vendor database guide leaves read offset `0xeb` reserved, so DM `0x3fcb`
has no published name. Calling it a DPCM value or measured round trip obscures
what the firmware actually does.

Its source, DM `0x3fc9`, belongs to the preceding INFO page:

```text
3ca8: AY1 = DM(0x1649)
3ca9: AX1 = 1
3caa: AR = AX1 AND AY1
3cab: IF EQ RTS
3cac: AY0 = DM(0x3fc9)
3cad: AR = AY0 + 1
3cae: DM(0x3fc9) = AR
```

PM `0x3cb0..0x3cb3` can first preload the counter with
`0xff86 - DM(0x3f04)`, and PM `0x3cbd..0x3cbf` uses its current value as an
offset in an INFO deadline. It is therefore a gated elapsed-time/phase counter
with a compensated negative origin. The exact tick epoch and stop condition
remain to be decoded.

On page 14, PM `0x2cb4..0x2cb8` multiplies the inherited `DM(0x3fc9)` by the
fixed-point constant `0xd555` and shifts once, approximately a `10/3`
conversion, storing the result at DM `0x3fcb`. The best evidence-based name is
**scaled INFO elapsed-time carryover**. It is distinct from the guide's
`RTDelay` at DM `0x3f87`, and direct TX/RX correlation has already shown that
it is not the physical echo delay on these paths.

Page 14 nevertheless reuses it as a generic timing bias: in global countdown
seeds, in the bulk-length formula, and in the computed callback target exposed
by Session 229. Thus `0x021a` versus `0x0364` means the calls inherited different
INFO timing histories; it does not by itself mean AudioCodes has a 538-unit
echo and 7802 an 868-unit echo. `docs/addsp_database.md` and the shim/replay
comments now carry this definition and explicitly distinguish it from
`RTDelay`.

---

## Session 231: correction — DM 0x3fcb is RTDelay at 8 kHz resolution

Per-sample tracing resolves Session 230's remaining epoch question and corrects
its conclusion that DM `0x3fcb` is distinct from `RTDelay`.

The timer is bracketed exactly by INFO states:

```text
                         AudioCodes good        7802 bad
TrnProgress 0x0032       3.570500 s              3.518750 s
TrnProgress 0x0036       3.693750 s              3.683125 s
raw interval             123.250 ms              164.375 ms
DM(0x3fc9) final         162                     261
DM(0x3fcb)               0x021a = 538            0x0364 = 868
DM(0x3f87) RTDelay       7                       11
```

At state `0x0032`, DM `0x1649` becomes `0x8001` and PM `0x3cb0..0x3cb3`
preloads DM `0x3fc9` to `0xff7a`: minus 134 ticks, or 55.83 ms at 2400 Hz.
PM `0x3cac..0x3cae` then increments it at a measured **2400 ticks/s**. State
`0x0036` changes DM `0x1649` to `0x8000`, clearing the gate. The compensated
results are 67.50 and 108.75 ms.

Two consumers prove the units and identity:

1. PM `0x3300..0x3303` multiplies the 2400-Hz result by `0x0555`, approximately
   `/24`, and publishes 7 and 11 at the guide's DM `0x3f87` `RTDelay` location,
   whose unit is 10 ms.
2. Page-14 PM `0x2cb4..0x2cb8` multiplies the same result by approximately
   `10/3`, exactly the 2400-to-8000 rate ratio, producing 538 and 868 sample
   units at DM `0x3fcb`.

Therefore **DM `0x3fcb` is the internal high-resolution `RTDelay`, expressed in
8 kHz sample/sample-pair units**. It measures the compensated INFO
state-32-to-state-36 round-trip training interval. It is not the direct local
echo peak, which explains why echo correlation can be much shorter without
invalidating this result.

The key good/bad difference is now physical and quantified: 7802's measured
round-trip interval is **41.25 ms longer** (99 ticks at 2400 Hz). That legitimate
longer-delay bin generates callback PM `0x0375`, where the emulator enters the
empty work item. The next question is no longer whether `DM(0x3fcb)` is garbage;
it is why the valid 109 ms path reaches PM `0x0375` without the state that path
requires, especially since prior successful Courier calls tolerated still
larger published `RTDelay` values.

---

## Session 232: the bad operand is a positive signed byte from the delay-aligned signal ring

A successful Courier call with the same published `RTDelay=11` separates delay
from the final trigger:

```text
                         Courier DATA       CX 7802 stall
DM(0x3fc9)                    257                261
DM(0x3fcb)                 0x0358             0x0364
callback DM(0)             PM 0x0369          PM 0x0375
PM 0x0388 I1               0x0ec5             0x0ed5
DM(I1) at PM 0x0388        0x0aa1             0xf201
SE after PM 0x0388         0xffa1 (-95)       0x0001 (+1)
```

PM `0x0388` is `SE = DM(I1,M0)`. `SE` is an eight-bit signed exponent register,
so only the low byte is retained and sign-extended. The emulator is correct:
`0xa1 -> -95`, while `0x01 -> +1`. Logging the full `DM(I1)` alongside the next
instruction proved this is truncation by the ADSP register, not a missing write
or stale memory.

Both paths then execute:

```text
0557: AR = SE
0558: AY0 = DM(I1,M1)       ; zero in both calls
055d: AR = AY0 - AR
055f: DM(I1,M3) = AR        ; observed at DM 0x2f4f
```

The successful call therefore creates `0 - (-95) = 95` and counts down. The
7802 call creates `0 - (+1) = -1`, after which the helper descends to saturated
`0x8000`. This corrects the earlier shorthand that `DM(0x2f4f)` was consumed
while uninitialized: **the common input is zero; the divergence is the sign of
`SE`, loaded from a round-trip-delay-aligned signal-ring sample.**

The ring sample itself is written normally by PM `0x3141`; at the exact read it
is `0xf201` on 7802. Forcing the callback back twelve instructions to PM
`0x0369` does not fix the bad replay: it still reaches the helper with a
positive `SE` and underflows. Thus neither RTDelay alone nor one skipped
prologue is sufficient. The computed callback and ring pointer jointly select
the signal byte, and the helper has an undocumented precondition that this byte
be negative.

This gives the production-fix boundary: either find the missing sign/validity
gate before PM `0x0388 -> 0x0555`, or establish why the DIL detector supplies a
positive low byte on failed calls. Fabricating RTDelay or a callback target is
not appropriate. The execution diagnostic now logs DAG1 `L1/B1` and the full
word at `I1`, which were needed to prove the signed-byte conversion.
