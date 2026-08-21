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

---

## Session 233: an empty-only arithmetic guard removes the runaway but does not make 7802 train

A narrower firmware A/B preserves valid DIL arithmetic. PM `0x055d` was
redirected to a scratch trampoline that first performs the original parallel
store and `AR = AY0 - AR`, then changes the result to `+1` **only when both**:

```text
AY0 == 0            empty destination/count
AY0 - SE < 0        positive SE would underflow it
```

All other results return unchanged. This matters because simply forcing every
positive `SE` negative turns the normal decrement into an increment and also
runs forever.

The empty-only guard has two useful offline controls:

- The previously healthy `courier-lag040-07` replay follows the same path to
  `0x00d0`; its non-empty arithmetic is unchanged.
- The canonical stalled `courier-lag000-03` replay no longer has a permanent
  foreground run. It advances `0x00b3 -> 0x00b4 -> 0x00c0 -> 0x00c2`.
- The 7802 replay likewise returns foreground and advances the internal output
  script at `0x00b3` from `0x1a46` through `0x1a4f` to `0x1a55`.

This is stronger than pinning `DM(0x2f4f)=8`: it changes only the impossible
empty-underflow case and leaves a healthy call unchanged.

### Live result: 0/3 CONNECT

The same PM patch was applied live on one persistent extension-6001
registration. Three valid calls from the CX on 7802 all selected page `0x026a`
and reached `TrnProgress 0x00b3`; all three ended `NO CARRIER`, with no CX
`CONNECT`. The endpoint transmitted only mark fill and never published a data
rate. Thus preventing the local runaway is necessary for emulator liveness but
**not sufficient for modem training**. The far modem still does not receive a
valid DIL response that advances the exchange.

Artifacts are under `artifacts/interop/cx-dil-empty-guard/`. The endpoint kept
one registration across all three calls and deregistered only at shutdown. The
CX was restored to `S202=0` and `+MR=0`.

The guard remains diagnostic, not a production fix. The next target moves
upstream of PM `0x055d`: determine what the PM `0x313f..0x3141` ring producer
and PM `0x0360..0x0388` selector mean by a candidate whose low signed byte is
positive, and identify the firmware condition that should reject or replace
that candidate before DIL response generation.

---

## Session 234: rejecting a positive candidate also fails live

A second A/B tested the more natural interpretation of Session 232: if the
signed ring byte is non-negative, reject that candidate rather than fabricate
a one-count work item. PM `0x038a` was redirected through a four-instruction
trampoline:

```text
AR = SE
AR = AR + 0             set sign flags
IF GE RTS               return to the scheduler; do not call PM 0x0555
JUMP 0x0555             original path for a negative candidate
```

The 7802 replay no longer runs away and its output script again advances within
`0x00b3`. This is a candidate-validation A/B, not a countdown repair.

Live, three valid calls were run on one persistent registration. Result:
**0/3 CX CONNECT**. The first two remained at `0x00b3` until the CX dropped.
The third went `0x00b3 -> 0x0001 -> 0x0000`, with `Rstatus_ch` briefly entering
secondary receive data, then reset. It retained only a tentative 32,000-bit/s
downstream rate and transmitted no payload. Simply discarding the positive
candidate therefore does not produce the missing DIL response either.

The source is now structurally clearer. PM `0x3d00..0x3d2b` writes a filtered
`MR1` into the 20-word ring at DM `0x2580..`; PM `0x2b4b` drains that ring into
`AX1`; PM `0x313f..0x3141` places the value into the 36-word delay/alignment
ring selected later by PM `0x0360..0x0388`. The low byte consumed as signed
`SE` is a detector/filter result, not raw PCMU.

Together the two live A/Bs close the obvious guards:

- convert the empty underflow to `+1`: locally live, no training;
- reject a non-negative candidate: locally live, no training/reset.

The defect is earlier than arithmetic error handling. The failed path does not
have a valid DIL detector result/response to send. The next useful comparison
is the filter/candidate state before the first PM `0x0388`, especially the
20-word PM `0x3d00` output ring and the PM `0x0360..0x037b` selection metrics,
not another consequence patch.

Artifacts are under `artifacts/interop/cx-dil-positive-reject/`. The endpoint
kept one registration for all three calls and deregistered at shutdown. The CX
was restored to `S202=0`, `+MR=0`.

---

## Session 235: the fitted 2185N exposes a real BIASRND emulator defect

The fitted part is an ADSP-2185N, while the MAME-derived core identifies itself
as an ADSP-2181-family target. The instruction set is compatible, but auditing
the 2185N control registers found one concrete computational mismatch that had
already been noted but not implemented.

`docs/3110043388x_hardware/8xcompu.pdf` defines `BIASRND`, bit 14 of the SPORT0
autobuffer control register at DM `0x3ff3`. The Eicon selected-channel setup
writes `0x4000` or `0x4035` to that register at PM `0x0066`; both values set
`BIASRND`. Thus the real 2185N uses biased rounding for every MAC instruction
with the `RND` option. The emulator treated DM `0x3ff3` as inert RAM and always
used ties-to-even unbiased rounding.

The core now selects biased rounding when DM `0x3ff3` bit 14 is set, with a
unit test at the exact midpoint:

```text
MR0=0x4000 + fractional (0x2000 * 1)
unbiased result MR1=0
the Eicon's BIASRND result MR1=1
```

This is a genuine 2185N fidelity fix, but it does **not** clear the known DIL
failure. The canonical CX 7802 and stalled Courier replays still enter the
permanent `0x00b3` foreground run; the healthy Courier still reaches
`0x00d0`. Therefore BIASRND is not the missing fix by itself.

The larger 2185N concern remains open: the core still treats the
memory-mapped SPORT control region as ordinary DM, while the harness
reconstructs selected-channel companding and interrupt delivery externally.
The next emulator audit should cover that boundary and the exact PM
`0x3d00..0x3d22` filter inputs, but should not claim BIASRND solved DIL.

---

## Session 236: live 2185N BIASRND result is also 0/3

The corrected core was tested live without any PM consequence patch. One
persistent extension-6001 registration served three valid CX calls from 7802.
All three selected page `0x026a`, advanced normally through `0x00b0/0x00b1/
0x00b2`, and stopped at `TrnProgress 0x00b3`. Every call ended `NO CARRIER`;
the endpoint transmitted mark fill only and published no connected data rate.

Thus the live result agrees with replay: **BIASRND is a real 2185N correctness
fix but not the DIL fix**. 7802 is now 0/18 for CX `CONNECT`. Artifacts are
under `artifacts/interop/cx-2185n-biasrnd/`. Registration was retained for the
batch and removed only at endpoint shutdown. The CX was restored to `S202=0`,
`+MR=0`.

---

## Session 237: right-justified 2185N SPORT expansion produces the first CX CONNECT

The remaining SPORT boundary contained the decisive emulator error. The
ADSP-218x Hardware Reference §5 says a companded receive word is expanded into
a **right-justified, sign-extended** RX value, with a 14-bit µ-law maximum and
a 13-bit A-law maximum. `sport_rx_word()` instead returned conventional
PCM16-scale G.711 values:

```text
                         old maximum       ADSP-2185N maximum
PCMU                     32124             8031       (divide by 4)
PCMA                     32256             4032       (divide by 8)
```

The selected-channel shim therefore drove every receive filter four times too
hard on the PCMU calls. It now emits the exact right-justified SPORT value.
Independent formulas test all 256 PCMU and all 256 PCMA codewords.

This overturns Session 62's rejection of the scale correction. That experiment
predated the present native selected-channel and bearer path; its failed state
`0x74` was not sufficient grounds to contradict the hardware manual.

### Replay controls

With correct SPORT scaling:

- healthy `courier-lag040-07` still reaches `0x00d0`;
- canonical stalled `courier-lag000-03` no longer runs away at `0x00b3` and
  advances through `0x00b4/0x00b6/0x00c0` to `0x00c2`;
- the old 7802 recording still runs away at `0x00b3`, so replay alone did not
  predict a guaranteed live success.

### Live result: confirmed end-to-end CX V.90 CONNECT

Three calls were served under one persistent extension-6001 registration,
with no PM patches. The first call produced:

```text
+MCR: V90
+MRR: 7200,45333
CONNECT 115200
```

The Eicon reached `TrnProgress 0x00d0`, raised CTS/DCD and both speed flags,
and the CX remained connected for the full 75-second hold while receiving the
PRBS data stream. The Eicon initially selected 45,333/7,200 bit/s and later
retrained/stepped to a final reported 44,000/7,200 bit/s. This is the first
confirmed end-to-end CX `CONNECT`, not merely server state `0x00d0`.

Calls two and three ended `NO CARRIER`; one advanced beyond the old wall to
`0x00c0`, while the other remained at `0x00b3`. The exact hardware correction
therefore solves the absolute CX blocker but does not eliminate all DIL
variability: this batch is 1/3 and 7802 is 1/21 overall.

Artifacts are under `artifacts/interop/cx-2185n-compand/`; the modem transcript
with `CONNECT` is `call1.modem.log`. Registration was removed only at endpoint
shutdown. The CX was restored to `S202=0`, `+MR=0`.

---

## Session 238: there is no missing SPORT receive ring

The receive-ring hypothesis is disproved by the shipping TIKRNL path and by an
existing runtime positive control. Receive and transmit are deliberately
asymmetric:

```text
PM 02b7  SR1 = DM(I5,M4)       selected channel's current ring word
PM 02b9  CALL 0703             once per selected 8 kHz frame
PM 0707  ASTAT = DM(32F0)
PM 070b  I0 = DM(3F0F)         coupled/pointer mode (DM32F0 = 4)
PM 070c  DM(I0,M0) = SR1       publish the current receive sample
...
PM 0771  CALL (DM(3FB3))       page Core8kRoutine consumes it
```

`attach_connected_bearer()` sets `DM(0x3F0F)=0x2B00`; therefore TIKRNL itself
writes `DM(0x2B00)` on every frame. The claim that the harness only sets the
pointer and nothing writes its target was false. V.22FC later changes
ShellInptr to its page scalar `DM(0x3763)`. A line-side ring is unnecessary:
the producer stores one scalar immediately before the one 8 kHz consumer call.
The selected foreground's underlying kernel ring advances before PM `0x02b7`;
the host-side selected-descriptor model fills its current word for that exact
frame.

V.34 does have a separate *internal* receive queue after this boundary:
`DM(0x228F/0x2290)` are its buffer pointers and `DM(0x2291)` its count, consumed
at PM `0x0FA3..0x0FA5`. V90D also has internal rings, but they are downstream
signal-processing state rather than a missing SPORT staging ring. PM
`0x3D00..0x3D2B` writes filtered samples through `DM(0x25BA)` into a 20-word
ring; PM `0x2B4B` drains it through `DM(0x25B9)`. PM `0x313F..0x3141` then
writes a 36-word delay/alignment ring through `DM(0x2062)`. These three V90D
pointers are now recorded beside `ShellInptr`, the V.34 queue and the transmit
resampler ring, so an internal consumer stall can be distinguished from a
missing SPORT sample.

This is not based on static inference alone. Session 61 traced diagnostic PCMU
`0x80` as signed-linear `0x7d7c` in SPORT RX0 and at the next PM `0x0703`, and
counted 127,087 continuation calls for 127,079 media samples (the difference is
setup). Thus V90D already receives the ordered 8 kHz sequence. There is no
receive-ring repair to make, and this hypothesis does not explain retrains or
low-rate selection.

---

## Session 239: successful and failed CX calls have identical receive-path cadence

The Session 237 three-call batch supplies the requested controlled comparison:
call 1 connected at V.90; call 2 reached `0x00c0` but never connected; call 3
stayed at `0x00b3`. All three were made under one endpoint process and one
registration. Their individual incoming PCMU streams were recovered from the
three inbound RTP SSRCs and replayed through the current native-MIPS harness.
`v90_dpcm_replay.py --rx-path` now audits the input publication, internal ring
pointers and producing/consuming PM sites.

Over approximately 100,000 V90D samples per call:

| measurement | connected call 1 | failed call 2 | failed call 3 |
|---|---:|---:|---:|
| `DM3763` vs exact SPORT expansion | 0 mismatches | 0 | 0 |
| selected continuation PM `0703` | 0.999990/sample | 1.000000 | 0.999990 |
| Core8k wrapper PM `19e1` | 0.999990/sample | 1.000000 | 0.999990 |
| filter stores PM `3d22` | 1.199976/sample | 1.199976 | 1.199976 |
| filter drains PM `2b4d` | 1.199976/sample | 1.199976 | 1.199976 |
| alignment stores PM `3141` | 1.199826/sample | 1.199827 | 1.199827 |
| downstream mapping generator PM `2a52` | 0.166667/sample | 0.166668 | 0.166673 |
| filter read-pointer values/transitions | 20 / 39,928 | 20 / 40,146 | 20 / 40,152 |
| filter write-pointer values/transitions | 10 / 19,964 | 10 / 20,073 | 10 / 20,076 |
| alignment-pointer values/transitions | 12 / 39,923 | 12 / 40,142 | 12 / 40,147 |
| longest filter pointer hold | 5 samples | 5 | 5 |
| longest alignment pointer hold | 16 samples | 16 | 16 |

The one-count differences are where the audit begins relative to page entry,
not lost recurring work. Producer and consumer counts are exactly paired in
every call. The downstream mapping generator also runs at the exact designed
one mapping frame per six line samples in all three calls. This closes a
missing, stuck, overflowing or underflowing V90D receive ring, and a missing
mapping-frame scheduler tick, for this batch.

The wire is also a negative result. Every inbound RTP stream has zero sequence
gaps and zero timestamp jumps, and simulated jitter-buffer occupancy never
starves. The successful call is the *worst* timed stream: −89 ppm, 30 ms p99
arrival gap and 35 ms maximum, versus failed call 2 at −5 ppm/21/24 ms and call
3 at −19 ppm/21/26 ms. Endpoint accounting reports zero substituted and zero
dropped samples in all three calls. The received SPORT-domain waveforms over
the first three seconds from `0x00b0` are likewise nearly identical in coarse
statistics: RMS 220/217/216, peak 1215 in all three, and 1.76/1.90/1.84% equal
adjacent samples. There is no ingress discontinuity unique to either failure.

The DSP's own quality outputs agree. In the same aligned window, Signalquality
is 7 in all calls, the settled SNRatio is `0x3b/0x3c/0x3b`, and settled timing
offset is −5/−5/−6. The first persistent divergence is instead the measured
delay and DIL state: live `RTDelay` is 11 on the successful call and 10 on both
failures; replay's high-resolution addend is `0x0368` versus `0x0314/0x0312`.
Call 3 stops its outer script at pointer `0x1a55`; call 2 gets to outer state
`0x00c0` but does not complete the inner exchange; call 1 advances through
`0x00c2..0x00d0`.

The remaining boundary is therefore after a healthy, advancing receive chain:
the delay-selected DIL candidate and the subsequent inner protocol decision,
or the far modem's response to our transmitted DIL signal. It is not G.711
expansion, RTP continuity, `DM3763`, Core8k cadence, or either internal receive
ring.

---

## Session 240: preserve the second before our local retrain

The V.90 DIL stall and the failure to maintain a low V.34 rate are not one
problem. More importantly, the retrain direction was already measured rather
than guessed: the Courier's `Retrains Requested 0 / Retrains Granted 1` means
it granted a request from the Eicon. The Eicon then leaves data state and
restarts training while retaining the nominal rate. That points to a local
receive synchronization or watchdog decision, not rate adaptation.

`eicon_adsp_sip.py --trace-retrain` now polls the two known one-frame reason
markers on every 8 kHz sample. In the runtime page-14 image PM `0x2f49` and
`0x2f47` select `0x5678` and sibling `0x5679`; PM `0x2f4a` publishes the value
to `DM(0x3F8A)`, and PM `0x2f4e` sets controller word `DM(0x2111)=7`.
A 20 ms capture alone can miss either marker. The new trace keeps 50 low-cost
20 ms snapshots and, on either marker or any departure from `TrnProgress
>=0x00d0`, dumps the preceding second plus the exact event frame. It records:

- overlay, training state, reason/controller and status words;
- SNR, Signalquality, frequency/timing offsets, phase jitter and phase errors;
- symbol rate, upstream quality/ceiling and round-trip delay state;
- V.34 queue pointers/count and both V90D ring boundaries.

The regular capture CSV also gains `retrain_reason` and
`retrain_controller`. Those columns are useful when the marker happens to
coincide with a packet record; the per-sample trace is authoritative for the
trigger. Run it buffered so the trace itself cannot consume the real-time log
budget:

```bash
python tools/eicon_adsp_sip.py [normal live options] \
  --trace-retrain --trace-file artifacts/interop/retrain.trace
```

The trace is deliberately dumped only after firmware has made the decision.
Its output therefore cannot cause the event it is diagnosing. The next live
retrain should distinguish a quality/lock threshold from a regular watchdog
expiry, and its exact `0x5678` versus `0x5679` path identifies which predecessor
block to disassemble.

---

## Session 241: live CX trace catches the local fallback

Seven immediate CX calls were made with the new trace: four normal V.90 calls
stopped before data, then two calls under the nominal V.34-limited profile both
ran the page-14 data pump far enough to exercise the failure. (The environment
requested a V.34 ceiling, but these calls still loaded overlay `0x026a`; this is
a V90D trace and is not claimed as a V.34-page result.) The second call produced
the complete sequence:

```text
18.440 s  0x00cc -> 0x00d0   speed words 202c / 11e9
24.107 s  0x00d0 -> 0x00bd -> 0x00c2
25.280 s  returns to 0x00d0  speed words 202b / 11e8
25.590 s  0x00d0 -> 0x00bd -> 0x00c2 again
32.915 s  DM(0x3f8a)=0x5678, Rstatus_ch ratechange,
          Rstatus flow_blocked; leaves page 14 for INFO/retrain
```

Thus the nominal rate does not remain bit-for-bit fixed inside the card: both
published speed words decrement on the first recovery, even if the CX's final
AT report presents the same rounded rate. More importantly, `0x5678` is **not
the event that first knocks the connection out of data**. It is published 7.325
seconds after the second `0x00d0 -> 0x00bd` transition, while the outer script
has remained in `0x00c2`. It is the failed-recovery/fallback marker.

The second before `0x5678` contains a real quality decline, not an RTP hole:
SNRatio falls from `0x000f` to `0x000b` (15.5 to 13.5 dB), the upstream error
metric `DM(0x0fcf)` rises sharply from approximately `0x02a9` to `0x03b7`, and
the exact marker frame has `Rstatus_ch=0x9300` (`ratechange`) and
`Rstatus=0x0482` (`flow_blocked`). Frequency offset remains about -1 and timing
offset about -8. There are zero sequence gaps, zero RTP timestamp jumps, zero
substituted/dropped samples and no jitter-buffer starvation.

Open-loop replay reproduces the same two data exits and fallback. A write watch
corrects an older address attribution: the live writer is runtime PM `0x2f4a`,
not `0x2d66`:

```text
DM(3f8a)=5678  ppc=2f4a pc=2f4b
AR=5678 AF=0008 MR0=1fe9 MR1=0017
```

Disassembly is unambiguous: `0x2f49` loads `0x5678`, `0x2f4a` stores it,
`0x2f4d` loads 7 and `0x2f4e` stores that at `DM(0x2111)`.

Data-state record `0x1c44` names state `0x00bd` in slot 3 and condition index
`0x24`, PM `0x30b4`, computes `DM(0x20b8)-1`. This identifies `DM(0x20b8)` as
an input to the selector, but not as the event source: Session 242's timed pins
at both zero and one do not prevent the recorded data exit. PM `0x23d7..0x23d9`
increments this word, PM `0x3047` clears it, and PM `0x305d` sets it to 2 on a
sibling path. `--trace-retrain` and the regular CSV preserve it as
`retrain_data_exit_input`; do not call it the trigger without tracing the
record evaluator that dispatches this particular visit.

The next target is the decoded recovery exchange itself; `0x5678` only says
that exchange did not finish.

---

## Session 242: `DM(0x0fcf)` is slicer error, but recovery waits for control signatures

Runtime PM `0x32f7..0x335d` identifies `DM(0x0fcf)` precisely. The receiver
forms the two-dimensional decision residual

```text
DM(0x0efb) = AY0 - AX0
DM(0x0efc) = AY1 - AX1
```

then PM `0x3348..0x334d` computes approximately
`(|e0| + |e1|) / 2`. PM `0x3352..0x335d` feeds that into a saturating
first-order average held as the pair `DM(0x0fce):DM(0x0fcf)`. In the normal
coefficient set, the old value is weighted by `0x7ffa/0x8000` and the new
error by `6/0x8000`; the published high word is capped at `0x3fff`. Thus it is
a smoothed complex slicer/equalizer decision-error magnitude: **lower is
better**. PM `0x31ff..0x3213` compares it with the threshold table and publishes
the quantised upstream ceiling at `DM(0x20ba)`. It is not SNR in dB and it is
not itself a retrain timer.

A timed replay pin, armed only after the second data exit, held `DM(0x0fcf)` at
the earlier good `0x0097` against every firmware store. The failed recovery
remained in `0x00c2` and took the same fallback. Pins of `DM(0x20b8)` at both
zero and one likewise did not prevent either recorded `0x00d0 -> 0x00bd`
transition. These are causal negatives: falsifying the quality estimate does
not repair this recovery exchange.

The first and second recoveries run the same inner path
`0x00a2 -> 0x00a4 -> 0x00a6 -> 0x006a`, with the same effective candidate and
condition tables. The difference appears in the outer `0x00c2` precondition,
condition index `0x18` at PM `0x3019..0x3038`. It accepts decoded result words
only when

```text
(DM(0x206d) & 0x000f) == 0x000f
(DM(0x206e) & 0xfffc) == 0xfff8
```

The successful first recovery produces `0x400f/0xfff9` at replay sample
196340 and immediately starts the `0x0bc2 -> 0x0cc2 -> 0x0dc2 -> 0x0ec2 ->
0x0fc2 -> 0x00c4` walk. The second recovery produces no matching word pair and
stays in `0x00c2` until fallback. Pinning one matching pair starts the walk but
does not complete it; holding the pair retriggers earlier records. Therefore
the firmware expects an ordered sequence of decoded control results, not one
boolean gate.

The practical repair is not to clamp `DM(0x0fcf)` or bypass the timeout. Trace
the producer of `DM(0x206d/0x206e)` and compare the complete successful and
failed result sequences, together with the mapping-frame contents transmitted
on each recovery. Either the receiver fails to decode the CX response on the
second request, or our second transmitted request differs so the CX never
sends the required response. Timed DM pins were added to
`v90_dpcm_replay.py` (`--pin-dm`, `--pin-from`, `--pin-to`) for narrow causal
replay tests without corrupting page initialization.

---

## Session 243: the second MP advertises an upshift after the estimator reset

The `0x00c2` result pair is a rolling two-bit control shift register. PM
`0x2eac..0x2ecb` derives the next dibit in `DM(0x2055)`; PM `0x0ca6..0x0caf`
shifts it into `DM(0x206d/0x206e)`. The accepted mask is the V.90 CP frame-sync
pattern. The failed recovery receives TRN2u-like dibits continuously but no CP
sync, which means either the CX never accepted our MP or its CP is undecodable.

The transmitted MP itself is available before modulation at `DM(0x0fc0..)`,
LSB first exactly as V.90 Table 16 specifies. Both attempts are structurally
valid Type-1 MP frames with identical precoder coefficients, but their rate
offers differ:

```text
                         successful first recovery   failed second recovery
MP words 0..2            ffff / 8305 / ffd1          ffff / 8705 / ffe1
drn bits 24..27          3  (maximum 7200)           7  (maximum 16800)
capability bits 36..49   0x0ffd                       0x0ffe
published upstream word  0x11e9 (7200)                0x11e8 (4800)
DM(0x20ba)               3                            7
DM(0x210b)               0x1ffa                      0x1ffc
```

The first offer has one useful low-rate intersection and converges to 4800.
Only 321 ms after returning to data at that rate, the second recovery rebuilds
MP from the freshly reset slicer-error average: `DM(0x0fcf)` has fallen to
`0x009b/0x0124`, `DM(0x20ba)` jumps to 7, and MP asks the already struggling CX
for rates from 7200 through 16800 while omitting 4800. The CX then continues
TRN2u for the recovery deadline and never sends the CP pattern. This explains
both the absent result signature and why pinning the quality word after MP was
built could not help.

Two opt-in interop controls now permit a live causal test without patching PM:

```text
EICON_V90D_RECOVERY_LIMIT=3
EICON_V90D_RECOVERY_MASK=0x1ffa
```

They pin only while outer state is `0x00c2`; together they change the failed
second header to `ffff/8305/ffd3`, retaining a low-rate offer. A less invasive
candidate, `EICON_V90D_RECOVERY_HOLD=1`, leaves the first recovery untouched,
retains the limit/mask of the first recovery that reaches `0x00c4`, and applies
those only to a later `0x00c2` visit. Replay verifies the intended MP rewrite;
open-loop replay cannot make the recorded CX transmit a response to a different
MP.

Live qualification is incomplete. A fixed 3/`0x1ffa` run produced one call
that selected 4800 and stayed continuously in `0x00d0` for 37.8 seconds, with
no rate recovery or retrain before the CX ended the call; the other calls in
that and the hold-policy batches stopped before data, as the pre-existing DIL
lottery does. This is a promising recovery fix, not yet a default: no live call
has both entered a *second* recovery under the policy and demonstrated the new
CP response. Eleven subsequent CX attempts failed before data and therefore
provided no recovery-path verdict.

## Session 254: V.90 data mode in the loopback, on HEAD

`TrnProgress 0x00d0` with `CTS｜DSR`, entered at **30.18 s** and held to the end
of the 50 s window with **zero retrains**, on the current tree:

```bash
tools/eicon_loopback.py --answerer-firmware-set pri117 --answerer-modulation v90 \
    --caller-firmware-set analog109 --caller-modulation v90a \
    --caller-kernel-dispatch --analog-codec-rate 9600 \
    --answerer-env EICON_EXPAND_SPORT=1 --trace-v90a-state --seconds 55 \
    --caller-env EICON_RX_PRIME=artifacts/eicon-native-tower/run65.ulaw:12.4:50:14.0 \
    --caller-env "EICON_ANALOG_PIN_DM=0x20eb=0xc000@0x20f9:0x00c0>25,0x254b=!0x0001@0x20f9:0x00c1>30,0x20eb=0x1000@0x20f9:0x00c3>30,0x20eb=0x0400@0x20f9:0x00c6>30,0x2104=!0x00d0@0x20f9:0x00cd>30" \
    --capture-dir artifacts/loopback-v32-goal/v90a-datamode
```

The outer machine's walk, from the trace: `00b6` 23.22 s → `00b7` → `00c0`
23.26 s → `00c1` 25.06 s → `00c3` 26.06 s → `00c4` 30.03 s → `00c6` → `00c8` →
`00ca` → `00cc` → `00cd` → **`00d0` 30.17 s**, and the `[adsp]` line at the
crossing reads `Rstatus_ch=0x8600[change_h|CTS|DSR]`.

**What this is and is not.** The receive side is primed from `run65.ulaw`, the
gold V.90D downstream, and the five terminal gates are pinned, so this is the
Session-253 configuration rather than a pin-free connect between two emulated
ends — the answering instance is off in INFO throughout, which is what a
one-way prime does to it. What is new is that it still holds on HEAD: the
`Y - 1` carry correction and everything after it have not touched this path,
and it is the only V.90 data-mode result the rig has. Pin-free `0x00d0` still
needs a reactive V.90D peer on the SIP leg.

### The five pins, hunted: pin 1 is decoded to one detector and one pattern

Pin-free (`EICON_RX_PRIME` only, no `EICON_ANALOG_PIN_DM`) the caller walks
`0092 → 0094 → 0095 → 00b0 → 00b1 → 00b2 → 00b3 → 00b6 → 00b7 → 00c0` and
stops at `0x00c0` — the same wall the pinned run needs its first pin for. The
chain behind that pin is now measured end to end.

1. **`0x00c0`'s condition is `PM 0x3495`**, live-dumped: `DM(0x20EB) & 0x8000`
   and `DM(0x10D9)` non-zero. Its sibling `test1` is `PM 0x33F8` on
   `DM(0x21E6)`, whose `next[1]` is record `0x1938` — the terminal — so the
   state does not park, it *times out into the abandon path*, which is why the
   remaining four pins were needed after it.
2. **`DM(0x20EB)` has exactly two writers all call**, and both are record
   unpackers: `PM 0x33E7` (outer) and `PM 0x33DA` (inner). Watched over a whole
   call it takes `0000, 0008, 0800, 0000, 0020, 0001, 2000, 0100, 4010` — bit
   15 never. So the bit is an inner-machine record's, and the question is which
   record the inner machine cannot reach.
3. **The inner machine walks `0x00 → 0x10 → 0x20 → … → 0x61` and stops**
   (`DM(0x2104)`, write-watched: 36 writes, last value `0x61`, held for the
   rest of the call). Record `0x17b2`, and its only condition is entry 36 =
   handler index `0x2A`, which the live handler table at `DM(0x064B)` resolves
   to **`PM 0x2FD1`**.
4. **`PM 0x2FD1` decoded.** Six words at `DM(0x0E48..0x0E4D)` — a six-deep
   circular buffer, `L1 = 6`, pushed by `PM 0x0C2E` — are required to have
   `|x| ≥ 0x0200` *and* a sign pattern of exactly `0b000111` or `0b111000`
   (`AR XOR 7` / `AR XOR 0x38` at `PM 0x2FE2..0x2FE4`). The tail at
   `PM 0x2FEB..0x2FFF` then wants **32 consecutive evaluations carrying the
   same pattern** (`DM(0x2551)` counts, `0x20` is the floor) before it returns
   success.
5. **What the caller actually sees.** With the fixed-offset prime the pattern
   is noise — `0x21 0x3c 0x03 0x38 0x1f 0x01 0x3c 0x00 …` — because the
   recording's own tone segment is not where the caller is. `run65.ulaw` at
   **23.0–23.5 s carries a clean 1333 Hz tone** (8000/6, i.e. exactly the
   period-6 pattern this detector is built for) and broadband either side.
6. **`EICON_RX_PRIME_SYNC` fixes the alignment and the pattern goes coherent
   but wrong.** Anchored on the gold milestones
   (`00b0@17.96,00c0@23.14,00d0@27.5`) the caller lands on the tone and the
   detector reads a *stable* `0x15` — `0b010101`, alternating — where it wants
   `0b000111`. Alternating means the tone sits at half the buffer's fill rate
   where the firmware expects a sixth of it.
7. **The fill rate is the discrepancy, and it is measured.** Watching writes
   with equal budgets, `PM 0x0C2E` pushes once per **14,122** cycles against
   `PM 0x1733`'s per-frame `RXSAMPLE` store at **5,896** — one push per 2.40
   frames, i.e. ≈2.9 line samples once the 8000→9600 resample is taken out.
   Pinning `DM(0x3F67) = 1` (`Samplebuffersize`, 3 on this page) under
   `EICON_FORCE_DM` does not help and should not — the page reads it once, at
   init, to set up the accumulator `PM 0x1D1B` walks.

   ⚠ **The rate ratio is solid; the mechanism behind it is not, and a first
   reading of it is withdrawn here rather than carried.** An exec watch on
   `PM 0x0C27` shows it is called from `PM 0x29C2` inside a `CNTR` loop —
   `cntr = 3, 2, 1`, successive calls 1,329 cycles apart with the pointer
   walking down — so the buffer is filled in *bursts*, not one push per tick.
   Bursts of three at one push per 2.40 frames average out to a burst every
   ~7 frames, which is not "once per symbol" (that would be every three), so
   "filled on the symbol clock" was the wrong name for it. What is measured is
   only the average rate, and the open question is what sets the burst
   interval — `PM 0x29C2`'s own caller.

So pin 1 is no longer "a status bit nothing writes". It is: inner state `0x61`
waits on a 1333 Hz phase-reversal detector, the gold peer sends exactly that
tone, and this caller samples it into a six-slot buffer at roughly a third of
the rate the detector's pattern assumes. **Walked up one level, and the burst has a name.** `PM 0x29B5..0x29C6` is a
sample-consuming loop:

```text
29b5: AR = DM($2182) - 9 ; IF LT JUMP $29ED     ; run only with >= 9 queued
29b8: CALL $0DD0
29b9: AR = DM($2202) - 2 ; AR = DM($2182) ; IF LT JUMP $29B5   ; and loop back
29be: CALL $0EBC / $0B5A / $0B90
29c2: CALL $0C27                                ; one push into DM(0x0E48..4D)
29c3: CALL $0BBF / $0B69 / $0BAC / $0E3A
29c7: AR = DM($2183) - 1 ; IF GT JUMP $29EC
29cb: I0 = DM($0E69) ; L0 = $0030 ; CNTR = 6 ; I1 = $0E48
29cf: DO $29D1 ...                              ; archive the six into a
                                                ; 48-word history at DM(0x0E69)
```

So the six words are the six most recent passes of that loop — one push per
pass — and the pattern the detector wants is a period-**6 passes** sign
reversal. The loop's cadence is set by `DM(0x2182)` (samples queued, floor 9)
against `DM(0x2202)`, and its measured rate here is one pass per 2.40 frames.
A 1333 Hz tone reads as period 6 only if the pass rate is 8 kHz; at ours it
reads as the alternating `0x15` we see. **Next: measure `DM(0x2182)`/
`DM(0x2202)` per frame against what `PM 0x0DD0` consumes, which says whether
the queue is being filled short or drained wide — and only then re-run the
pin-free walk.** Pins 2–5 are untouched and stay downstream of this
one.

---

## Session 255: pin 1 is `DM(0x20EB)` bit 15 alone — the tone detector was never the gate

Three new instruments and one live sweep retire the reading Session 254 left
open, and most of what it built on top of it.

**New instruments** (all in `eicon_adsp_sip.py`, all inert unless set):

```text
EICON_DM_SAMPLE=<addr>[,<addr>…]:<period>[:<overlay>[:<after_s>[:<budget>]]]
EICON_RX_SWEEP=<f0>:<f1>:<start_s>:<end_s>[:<amp|a0-a1>[:<step_s>[:<duty>]]]
```

`DM_SAMPLE` logs a set of DM words on a fixed clock — the thing a write-watch
cannot give you when the question is a *rate*. `RX_SWEEP` replaces the caller's
received codeword with a stepped tone sweep, and its `<duty>` field is what
makes it usable: a continuous tone starves the outer machine, which abandons
within about five seconds and takes the state under test with it. At duty 0.15
the probe is a burst inside a live primed call, and `0x00c0` held for the whole
eighteen-second sweep.

### The fill rate was measured wrong, and the correct numbers exonerate it

Session 254 read the buffer's fill rate off a cycle budget and got "one push per
2.40 frames, ≈2.9 line samples", concluding the caller samples the peer's tone
at a third of the rate the detector's pattern assumes. A PC histogram gated on
`TrnProgress 0x00c0` (`--pc-histogram --pc-histogram-state 0x00c0`, one visit,
62,240 line samples / 7.780 s) counts the same sites directly:

```text
PM 0x1733   per-frame RXSAMPLE store    74,688   =  9600/s   (the codec rate)
PM 0x0C2E   one push into DM(0x0E48..)  62,357   =  8015/s   (the line rate)
PM 0x2FD1   the detector                10,393   =  1336/s   (= 8000/6)
```

So the page's front end already decimates the rig's 9600 Hz codec to a clean
8 kHz internal rate, the six-word buffer is pushed once per line sample, and the
detector evaluates it once per six pushes. **The rate the pattern assumes and
the rate it gets are the same**, and `8000/6 = 1333 Hz` is exactly the tone
`run65.ulaw` carries. There is nothing wrong here to fix; the cycle-budget
reading counted calls per cycle rather than per sample and is withdrawn.

### The detector is satisfiable, and satisfying it changes nothing

`PM 0x2FD1`'s tail was read off the histogram's own disassembly. `DM(0x2552)`
holds the pattern result, `DM(0x2551)` the consecutive count, and `PM 0x2FE8`
(`DM($2551) = M0`, the reset) executes 10,393 times in that state — every
evaluation — so the count never survives. An amplitude ramp at a fixed
1333.333 Hz (`EICON_RX_SWEEP=1333.333:1333.333:25:41:1000-26000:1.0:0.3`) finds
the window where it does:

```text
   amp     n  match%  2551 max
  7250   600     0.0         0
 11938   600    13.5        27
 13500   600     9.0        35
 16625   600    22.5        44      <- past the floor of 0x20 = 32
 19750   600     0.2         0
```

Held continuously at 16,625 from the moment `0x00c0` is entered, `DM(0x2551)`
reaches 45–56 in every one of the five seconds of tone — repeatedly past its
floor — and **nothing moves**: inner state stays `0x61`, `DM(0x20EB)` stays
`0x4010`, `TrnProgress` stays `0x00c0`. The same counter reaches 232 one state
earlier, at inner `0x60` in `0x00b3`, so it is not specific to `0x61` either.

The chain "inner `0x61` waits on a 1333 Hz phase-reversal detector" is therefore
broken at its last link. The detector runs, the gold peer's tone is the right
frequency, the rig delivers it at the right rate, the 32-count is met — and the
state does not advance. Do not re-derive it.

### What `0x00c0` actually waits on

`PM 0x3495` needs `DM(0x20EB) & 0x8000` **and** `DM(0x10D9)` non-zero. A
write-watch on `DM(0x10D9)` (25,021 writes) names its producer, and the
histogram disassembles it:

```text
2d5c: DM($10D9) = M0          ; clear, 24,896 times -- once per evaluation
2d5d: SR0 = DM($0F9E)         ; an accumulator
2d5e: AY0 = DM($2149)         ;   += this increment
2d64: DM($0F9E) = AY1
2d6b: AR = DM($10DA) ; AR = AR - AY1
2d6d: IF GT RTS               ; not yet at the limit -- leave it 0
2d6e: DM($0F9E) = M0          ; 116 times
2d6f: DM($10D9) = M1          ; 116 times -- it DOES fire
```

`DM(0x10D9)` is a periodic strobe, roughly one evaluation in 215, and it fires
throughout the call. It is not the blocker.

That leaves **`DM(0x20EB)` bit 15 as the sole condition pin 1 is waiting on**,
which is the one part of Session 254 that survives intact: the word's only two
writers are the record unpackers `PM 0x33E7` and `PM 0x33DA`, and over a whole
call it takes `0000, 0008, 0800, 0000, 0020, 0001, 2000, 0100, 4010` — bit 15
never. The question is which record carries it and why the inner machine cannot
reach that record, and it is now the *only* question in front of pin 1. Pins 2–5
remain downstream and untouched.

Captures: `artifacts/loopback-v32-goal/{dmsample,dmsample-six,sweep-duty,sweep-amp,amp1333,hold1333b,pchist,w10d9}`.

### The bit exists, and it is one inner state away

`DM(0x20EB)` bit 15 is not missing from the firmware. It was missing from the
*decoder*.

`record_table_decode.py --inner` took the inner unpacker's high byte as
`C & 0xFF00`, the same slot the outer unpacker reaches through `SE = 0xFFF8`.
Dumped and disassembled, `PM 0x33D2` does something else:

```text
33d2: AY0 = $00FF
33d3: MR0 = $20E9
33d5: AF = AX0 AND AY0, AR = DM(I4,M5)   ; index = A & 0xFF
33d6: AR = AR AND AY0, SR0 = DM(I4,M5)   ; low byte = B & 0xFF
33d7: AR = MR0 + AF, SR1 = AR
33d9: SR = LSHIFT SR0 (HI, OR) BY 8      ; high byte = C & 0x00FF  <-- left
33da: DM(I0,M1) = SR1, AR = MR1 XOR AF
```

A *left* shift by 8 into the high half, so the inner value's high byte is C's
**low** byte. The check is a live write rather than a second reading of the
opcodes: a write-watch on `DM(0x20EB)` over a whole call catches ten stores,
nine from the outer unpacker `PM 0x33E8` and **one of `0x4010` from the inner
unpacker `PM 0x33DB`** — a value no record produces under the old formula, and
exactly the entry at DM `0x1737` under the corrected one. Fixed, with the live
write and the consequence below as `tests/test_record_table_decode.py`.

With it corrected the table answers the question directly:

```text
tools/record_table_decode.py <dm.bin> --start 0x1689 --index 2 --inner
  0x1731 state=0043 index 2 = 4010
  0x17c4 state=0062 index 2 = c000     <- bit 15
  0x17d3 state=0064 index 2 = c000     <- bit 15
```

**Inner state `0x62` sets `DM(0x20EB) = 0xC000`**, and the inner machine sits on
`0x61`. Pin 1 is one inner state wide.

### Why `0x61` cannot take that step

The inner scheduler is `PM 0x3392`:

```text
3392: I4 = DM($217A) ; CALL (I4)     ; slot 4 -- the primary
3394: IF LE JUMP $33A7               ; LE -> 33a7: CALL $33BB, which unpacks the
                                     ;   record DM(0x2127) points at and advances
3395: I4 = DM($2176) ; CALL (I4)     ; otherwise consult tests 0..3, and the
3397: MR0 = DM($2172)                ;   first to answer writes its next-address
3398: IF LE JUMP $33A6               ;   into the cursor
33a5: RTS                            ; none answered -- nothing moves
```

So the primary returning **LE is the advance**, and the branch tests are the
alternative. Sampled live at inner `0x61`:

```text
DM(0x2127) = 0x17c4        the cursor already points at inner state 0x62
DM(0x217A) = 0x2fd1        primary = the detector
DM(0x2176..0x2179) = 0x340a  all four tests are the never-handler
```

**Every branch out of `0x61` is the never-handler, and the record waiting under
the cursor is the one that sets the bit.** The whole of pin 1 is therefore
`PM 0x2FD1` returning LE.

### `PM 0x2FD1` is a phase-reversal detector, and that is why a tone cannot pass it

Dumped, its tail reads:

```text
2fe7: IF NE JUMP $2FEB        ; pattern matched
2fe8: DM($2551) = M0          ; else reset the count, DM($2550) = 1, return 1
2feb: AR = DM($2550) ; IF EQ JUMP $2FF2
2fee: DM($2553) = DM($2552) ; DM($2550) = M0 ; JUMP $3000   ; latch, return 1
2ff2: AR = DM($2552) XOR DM($2553)
2ff5: IF NE JUMP $2FFA        ; the pattern CHANGED
2ff6: DM($2551) += 1 ; JUMP $3000                            ; same -- count, return 1
2ffa: AR = DM($2551) - $0020 ; IF LT JUMP $3000               ; changed too early
2ffe: AR = 0 ; RTS            ; >= 32 then changed -> LE -> ADVANCE
3000: AR = 0 + 1 ; RTS        ; every other path -> GT -> stay
```

It counts a **stable** matching pattern for at least 32 evaluations and then
returns success only when that pattern **changes**. The two accepted patterns,
`0b000111` and `0b111000`, are exact opposites: this is a phase-reversal
detector, and the V.90 peer's tone reversing phase is the event it is built for.

That retires the last of Session 254's reading and the first half of this
session's. **No steady tone can ever satisfy it** — held at 1333.333 Hz and
16,625 the count runs to 44–62, far past the floor, and returns GT every single
time because the comparison at `PM 0x2FF4` never differs. "The detector is
satisfiable" was measuring the count, which is the necessary half; the
sufficient half is the reversal after it.

Phase-reversing the probe (`EICON_RX_SWEEP` grew a `<flip_ms>` field) is
necessary but not yet sufficient: at 40 ms and 120 ms the count still reaches
26–62 and the inner state still does not move, because a reversal that does not
land on an evaluation boundary walks the six-slot window through rotations that
are neither accepted pattern, and each of those hits `PM 0x2FE8` and resets the
count to zero. The evaluation period is 6 pushes = **0.75 ms**, so the probe has
to reverse on a multiple of that *and* in phase with it, and its level has to
stay inside the 13,500–17,000 window where the magnitude floor and the pattern
both hold — at 18,000 the pattern fails on 98% of evaluations. That alignment is
the next step, and the gold recording already has it: `run65.ulaw` reverses in
the caller's own reference frame, so replaying its 23.0–23.5 s tone segment on a
loop is the probe-free version of the same test.

Captures: `artifacts/loopback-v32-goal/{w20eb,slots,flip40,flip120,flipx30,flipx60}`.

### The gold tone segment, measured — and why looping it does not work either

The obvious probe-free test is to replay the recording's own tone so its real
reversals arrive in the caller's frame. Measured, the segment is shorter and
quieter than Session 254 recorded it:

```text
run65.ulaw   22.5s rms 898  peaks 1800 / 265 / 2200 Hz     broadband
             23.0s rms 828  peaks 1335 / 1330 / 1340 Hz    the tone
             23.2s rms 662  peaks 3695 / 3420 / 830 Hz     broadband again
```

So the tone runs **23.00–23.19 s, about 0.19 s**, not the 23.0–23.5 s used
before — and the 0.3 s of broadband inside that window is enough to hold the
count at zero by itself. Its crest factor is 924/822 = 1.12 against a sine's
1.41, and its peak is spread over 1330–1340 Hz rather than a line, which is what
a reversing tone looks like and is consistent with what the detector wants.

Three loops of just the tone (`23.00–23.19 s` × 60, spliced after the head of
the recording and anchored with `RX_PRIME_SYNC`) at gains 1, 6 and 14 all fail:
`DM(0x2552)` is zero on 94–96% of evaluations, `DM(0x2551)` peaks at 3, and the
inner machine stays on `0x61`. Looping introduces a discontinuity every 0.19 s
that the six-slot window walks through, and the loop boundary is no more aligned
to the 0.75 ms evaluation clock than the synthetic flips were.

⚠ One quantity from this is worth keeping even though the test failed. The gold
tone reaches the caller at **RMS 822**, and the synthetic sine needed **RMS
≈11,800** (amplitude 16,625) before the pattern appeared at all — a factor of
about **14**. That is the same order as the ~30× receive attenuation this page's
front end has been noted to have, and it is the first time it has been measured
against a reference that is known to work on real hardware. Whether pin 1 is an
alignment problem or a gain problem is now a single experiment: present the
gold segment, aligned, at gain 1 and at gain 14, and see which one fires.

### Regression: both of the rig's data modes still stand on this tree

```text
V.22bis  --native-mips, EICON_FORCE_DM=0x3FC4=0x0004@0x025f on both ends
         answerer 0x00d0 at 21.80 s, caller 0x00d0 at 24.08 s,
         both CTS｜DSR｜DCD, DATASTATESpeed=0x0047 -- pin-free
V.90     the Session-253 recipe (RX_PRIME + the five terminal pins)
         caller 0x00d0 with CTS｜DSR at 30.18 s
```

Unchanged by the three instruments added here, all of which are inert unless
their variable is set.

Captures: `artifacts/loopback-v32-goal/{toneloop,tone-g1,tone-g6,tone-g14,regress-v22,regress-v90a}`.

## Session 256: the aligned gold segment, at gain 1 and gain 14

Session 255 left one experiment: present the gold tone segment *aligned*, at
gain 1 and gain 14, and see which of alignment or gain is pin 1's blocker. Run,
the answer is neither.

### The segment, measured exactly

Session 255 recorded the tone as 23.00–23.19 s. Walked outward from a
known-clean centre while the sample stays at a constant magnitude, it is

```text
23.10213 - 23.22587 s   990 samples = exactly 165 periods of 6
values: exactly two, +924 and -924        (a square wave, not a sine)
best-fit frequency: 1333.330 Hz           (8000/6 = 1333.333)
```

and the sign string is a perfect `+++---` repeat with **one break**, at offset
966 of 990:

```text
+++---+++---+++---+++---+++------+++---+++---+++---+++
                              ^^^^^^  six, not three
```

A six-long run where every other is three is one inserted half-period — **a
180° phase reversal, 24 samples before the segment ends**. That is precisely the
event `PM 0x2FD1` is built for, and Session 255's looped probe cut the segment at
23.19 s, which threw the reversal away. So the earlier loop failed for a reason
that had nothing to do with what it was testing.

⚠ The "no reversals, constant phase ramp" reading taken from 10 ms correlation
hops was an artifact of restarting the reference phase at each hop: +120° per
10 ms is exactly what a perfectly on-frequency 1333.333 Hz tone gives when
measured that way. The fine frequency fit and the sign string are the reliable
reads.

### The test

The 990-sample segment, spliced whole after the head of `run65.ulaw` at the
anchor `RX_PRIME_SYNC` maps `0x00c0` to and repeated 80 times, so the caller
enters `0x00c0` onto the start of a repetition and gets ~10 s of it:

```text
                       n(0x00c0)  DM(0x2551) max   match %   inner
gain 1   (amp   924)      10067          0           3.1      0x61
gain 14  (amp 12936)      10063          3           5.0      0x61
gain 18  (amp 16632)      10066          0           3.7      0x61
```

None advances. `DM(0x20EB)` stays `0x4010` and the inner machine stays on `0x61`
in every one.

**Alignment is not the variable.** Rotating the segment by k = 0 and k = 1
samples changes nothing (`2551` max 3 and 2, match 5.0% and 4.8%), and more
decisively it cannot matter: the DSP's evaluation clock is set by its own frame
pacing and drifts against the file, so ten seconds of replay sweeps the relative
phase through every offset by itself.

### The control that names the real variable

The same file path, the same 990-sample loop with the same reversal at offset
966, the same frequency — but a **sine** instead of the gold square, at the
amplitude the `RX_SWEEP` probe found:

```text
sine,         amplitude 16625:   DM(0x2551) max = 138,  match 19.7%
gold square,  amplitude 16632:   DM(0x2551) max =   0,  match  3.7%
```

Identical level, identical delivery, identical structure. **The sine counts to
138, four times the floor of 32; the gold square never counts at all.** So pin 1
is neither alignment nor gain: it is that *this rig's receive path does not
deliver the gold square wave in a form the detector can read*. A square at
8000/6 puts its third harmonic at exactly 4000 Hz — the line's Nyquist — and the
rig resamples 8000→9600 in front of the page, which is where a component sitting
on Nyquist is least likely to survive. That is the next thing to measure.

⚠ **The trap that cost this session a wrong conclusion first time round:**
`EICON_RX_SWEEP`'s `<amp>` is an *amplitude*, and the gold segment was being
compared against it by *RMS*. A sine matched to the square's RMS has 1.41× its
amplitude, which lands outside the 13,500–16,625 window where the pattern gate
holds — so the first sine control "failed" (match 2.6%, count 0) and looked like
it exonerated the waveform. Match amplitudes, not RMS, whenever this detector is
involved.

### And a count past 32 is still not enough

Even the sine, at 138, does not advance. `PM 0x2FD1` needs the stable run to be
followed **immediately** by the opposite valid pattern, and the six-slot window
has to traverse the reversal to get there. During that traversal it holds
neither accepted pattern — a phase reversal of a period-6 waveform passes
through an all-one-sign window — so `PM 0x2FE8` resets the count before the
opposite pattern is ever presented. The 19.7% match rate says the same thing
from the other side: four evaluations in five are a rejected rotation.

So the stable-run half of the condition is now demonstrably reachable, and what
is left is the transition. Either the buffer is not six raw samples (the
patterns already do not behave like raw sample signs, which is the stronger
hint), or the real V.90 reversal is shaped so the window never holds an invalid
pattern. Decoding what actually fills `DM(0x0E48..0x0E4D)` — `PM 0x0C27`'s
input, not its output — is the way in.

Captures: `artifacts/loopback-v32-goal/{gold-g1,gold-g14,gold14-k0,gold14-k1,sine-s1,sine-s14,sine-a16625,gold-a16632}`.

## Session 257: what fills `DM(0x0E48..0x0E4D)`

The six words the detector reads are **not six samples**. Dumped and
disassembled, `PM 0x0C27` is four lines of arithmetic:

```text
0c27: I1 = DM($0E67)          ; the 6-deep circular write pointer
0c28: L1 = $0006
0c29: AY0 = DM($0E6B)         ; a tracked baseline
0c2a: AR = SR0 - AY0          ; SR0 is the CALLER's -- the demodulator's output
0c2b: MY0 = DM($1AF7)
0c2c: MR = AR * MY0 (SS)      ; scale
0c2d: MR = MR + MX0 * 0 (RND) ; round
0c2e: DM(I1,M1) = MR1         ; push
0c2f: DM($0E67) = I1
0c30: L1 = $0000 ; RTS
```

So each slot holds **(demodulator output − baseline) × scale**. The detector's
sign pattern is therefore the sign of a *baseline-removed* quantity, which is
the whole reason its patterns never behaved like sample signs and why chasing
the line waveform's period through them was the wrong frame.

### The baseline is a gated 9-tap FIR

`PM 0x0B90`, the routine that runs immediately before `0x0C27` in the per-pass
sequence at `PM 0x29BE..0x29C2`, computes it:

```text
0b90: AX0 = DM($20ED) ; AY0 = $1000 ; AR = AX0 AND AY0
0b93: IF EQ JUMP $0BAA        ; bit 12 clear -> 0baa: DM($0E6B) = 0, RTS
0b94: L0 = $000C              ; a 12-deep circular history
0b96: AY1 = DM($0E6A)
0b98: AR  = DM($1AF7) ; DIVS ; DO $0B9B UNTIL NOT CE ; DIVQ   ; AY0 = 0E6A/1AF7
0b9c: I0 = DM($0E6C) ; DM(I0,M0) = AY0 ; DM($0E6C) = I0       ; push the quotient
0ba1: I5 = $1F8C              ; coefficients, in program memory
0ba2: CNTR = DM($0E6F)
0ba5: DO ... MR = MR + MX1 * MY1 (SS)                         ; the FIR
0ba7: DM($0E6B) = MR1
```

A 15-step `DIVS`/`DIVQ` normalises `DM(0x0E6A)` by the same `DM(0x1AF7)` that
`0x0C27` later multiplies back, the quotient goes into a 12-deep circular
history at `DM(0x0E6C)`, and the baseline is an FIR over it with coefficients
from `PM 0x1F8C`. **It is gated on bit 12 of `DM(0x20ED)`** — and that word is
block index 4, which the inner records write: `0x17b2` (inner `0x61`) sets it to
`0x1210`, so the gate is open exactly where pin 1 lives.

Measured live in `0x00c0`:

```text
DM(0x20ED) = 0x1210   (bit 12 set -- the baseline is active)
DM(0x1AF7) = 0x7FFF   (unity: the scale at 0x0C2C is effectively identity)
DM(0x0E6F) = 9        (nine taps)
```

### And `SR0` is the measurement that ends the argument

Exec-watching `PM 0x0C27` reports the `SR0` it is entered with — the
demodulator's output, the actual input to all of this:

```text
sine, amplitude 16625      +++---+++---+++---+++---+++---+++---+++---
                           values 2934, 9841, 6055, -3645, -9776, -6896, …

gold square, amp 16632     ---+-+-+++++++++++-+-------------+-+++-+++
                           values -20827, -28121, -30909, 13104, -28026, …
```

**A clean sine at 8000/6 produces a perfect period-6 sign sequence with
comfortable magnitudes; the gold square produces noise at saturation.** The
corruption is entirely upstream of `PM 0x0C27` — in the demodulator, not in the
buffer, the baseline, or the detector.

Band-limiting the gold square to 2 kHz (a 127-tap windowed sinc, keeping the
1333 Hz fundamental and its reversal, dropping the 4 kHz third harmonic that
sits exactly on the line's Nyquist) confirms the direction without closing it:

```text
                                    match %   DM(0x2551) max
gold square, amp 16632                  3.7          0
band-limited, fundamental 16625         8.2          9
band-limited, peak 16625                4.7          1
sine,          amplitude 16625         19.7        138
```

The harmonic is part of it — removing it doubles the match rate — but the
band-limited gold still drives `SR0` to ±30,000 where the sine reaches ±10,000
for the same peak input, so the demodulator's gain for this waveform is roughly
3× and it saturates. Its sign sequence degrades accordingly
(`+++---+++---+++---+++--+++-+-+-+-+-+---`).

So the chain now reads, end to end and every link measured: line → demodulator
(`SR0`) → minus a 9-tap FIR baseline gated on `DM(0x20ED)` bit 12 → scaled →
6-deep ring at `DM(0x0E48)` → sign pattern → `PM 0x2FD1`'s stable-run-then-
reversal test → inner `0x61 → 0x62` → `DM(0x20EB) = 0xC000` → `0x00c0` clears.
**The one link that is wrong is the first**, and it is a demodulator gain and
band question rather than anything in the detector.

Captures: `artifacts/loopback-v32-goal/{oper-sine-a16625,oper-gold-a16632,sr0-sine-a16625,sr0-gold-a16632,goldbl,goldbl-pk}`.

## Session 258: the demodulator gain is not broken

Asked to fix it, the measurements say there is nothing to fix. Recording the
evidence so the question is not reopened.

### The chain, with every stage's gain measured

```text
line (u-law)
  -> RationalResampler 8000->9600      unity: per-phase DC gain 0.9972-1.0000,
                                       and a 1333.33 Hz sine at amplitude 16625
                                       comes out at 16650 (x1.002)
  -> x DM(0x3FC8) and a shift          DM(0x2131); 0x12D0 = 4816 gives x1.176
  -> the 179-tap front end             -> the 16-deep ring at DM(0x0E76)
  -> PM 0x0EBC                         2 samples a pass into the 248-deep
                                       delay line at DM(0x2117)
  -> PM 0x0B5A                         matched-filter FIR, PM 0x1EB4 coefficients,
                                       DM(0x0E6D) = 216 taps, then a fixed x4
                                       (`SR = ASHIFT MR1 (HI) BY 2`) -> SR0
```

The only programmable gain in it is `DM(0x3FC8)`, and pinning it confirms the
stage is linear and behaving: `DM(0x2131)` goes 539 → 1477 → 2988 → 3888 as the
word goes `0x12D0 → 0x2fff → 0x5fff → 0x7fff`.

### `DM(0x3FC8)` is frozen on this page — and that turns out to be fine

A write-watch gated on overlay `0x026B` catches **zero** writes to it: the AGC
that sets it (`PM 0x3964`, 6,891 writes climbing `0x0858 → 0x11d7`) runs on the
*earlier* page, and the V.90A page inherits the converged value. That is safe
only if the level is continuous across the page boundary — and `EICON_RX_PRIME`
substitutes a recording, so it looked like exactly the discontinuity that would
break it.

**New instrument, `EICON_RX_PRIME_LEVEL=auto|<rms>`** (inert unless set), which
measures both sides and matches them. Its answer closes the question:

```text
[prime-level] live RMS 901, recording RMS 905 at cursor 112000
              -> scaling the recording by 0.995
```

`run65.ulaw` is already at the level the live line was running at, to within
half a percent. The splice is level-continuous, so the inherited gain is the
right one and the hypothesis is dead. (`auto` measures the most recent
non-silent block rather than the block before the splice: the answerer
transmits nothing between about 11 s and 14.6 s, so a naive read returns zero
and would scale the recording to silence. The first version of this did exactly
that, which is why the floor is in the code.)

### And level is not the axis anyway, which is the decisive control

```text
input                            |DM(0x2131)|     match %   DM(0x2551) max
gold, real level (0x3FC8=0x12D0)   mean   539        0.0          0
gold, gain word 0x2fff             mean  1477        0.0          0
gold, gain word 0x5fff             mean  2988        3.8          0
gold, gain word 0x7fff (maximum)   mean  3888        4.8          0
gold, x18 in the file              max  24352        3.7          0
sine, amplitude 16625              max  19767       19.7        138
```

The fifth row is the one that settles it: with the gold square scaled *above*
the sine's operating point, the detector still reads 3.7% against the sine's
19.7% and never counts. **A waveform at higher level than the one that works
fails, so level is not what separates them** — and at `0x5fff`/`0x7fff` the
buffer values themselves reach a mean of 17,700–19,100, far above the `0x200`
magnitude floor, so the gate that fails is the pattern, not the magnitude.

Raising the gain also cannot reach the sine's operating point even in principle:
`DM(0x3FC8)` saturates at `0x7FFF`, a factor of 6.8, where 24 would be needed.
If anything is missing it is a fixed analogue front-end gain this rig does not
model, not a wrong value in a word — and the control above says that would not
help either.

**So `DM(0x3FC8)` is left alone.** Changing a value that measures correct, to
chase a symptom the same measurement says it does not cause, would be a
stand-in dressed as a fix. The axis remains the one Session 257 named: the gold
square's content through this receive path, `SR0` being clean period-6 for a
sine and noise at saturation for the square.

Regression, with the new instrument in the tree and unset: V.22bis reaches
`0x00d0` on both ends (answerer 21.88 s, caller 24.16 s, `DATASTATESpeed`
`0x0047`), and the V.90 recipe reaches `0x00d0` with `CTS｜DSR` at 30.18 s.

Captures: `artifacts/loopback-v32-goal/{agc,agc-gated,lvl-off,lvl-auto2,gain-0x2fff,gain-0x5fff,gain-0x7fff,regress2-v22,regress2-v90a}`.

## Session 259: the receive path's missing band limit — real, and not the fix

`AnalogLineInterface.receive()` was two lines: a gain and the hybrid echo.

```python
echo = self._tx_history[0] if self._tx_history else 0
return _clip16(far_sample * self.rx_gain + echo * self.echo_gain)
```

No band limit anywhere. A real path from a V.90 downstream to an analogue
modem's ADC — the central office's reconstruction filter, the local loop, the
DAA, the modem's own codec anti-alias filter — passes nothing at 4 kHz, and this
model handed the codewords straight to the DSP. That is a genuine gap, and it
looked like exactly the gap: the gold Phase-3 tone is a period-6 *square*, and a
period-6 square at 8 kHz is a 1333.33 Hz fundamental **plus a component exactly
at the 4 kHz Nyquist** at a quarter of its amplitude.

So the gap was closed: `rx_bandlimit_hz` / `rx_bandlimit_taps` on
`AnalogLineInterface`, `EICON_ANALOG_RX_BANDLIMIT_HZ` / `_TAPS` from the
environment, a linear-phase FIR whose even tap count makes the Nyquist response
exactly zero rather than approximately so. Twenty-four taps at 4000 Hz measures
flat to within **0.07 dB from DC to 3400 Hz**, 0.00 dB at 1333 Hz, and **−257 dB
at 4000 Hz**. Through it the gold square becomes a clean sine: fundamental 1232,
residual **−69.9 dB**.

**And it makes things worse, for a reason that is worth having.**

```text
band limit 3600 Hz (12 taps):  caller stalls at TrnProgress 0x0095
band limit 4000 Hz (24 taps):  caller stalls at TrnProgress 0x0095
band limit off:                caller reaches 0x00c0 as before
```

The square is not a sine with an unfortunate harmonic. It is a construction
aimed squarely at a sign-and-magnitude detector, and the numbers say so:

```text
                    magnitudes on the six samples        signs
raw square          924 on every one                     +++---
band-limited        {0, 1067} -- one in three is ZERO    ++++--
```

`PM 0x2FD1` requires `|x| >= 0x200` on all six words. The raw square clears that
on **100%** of its samples; band-limited, **33% fall under the floor**, because
what survives is a period-6 sine whose samples land on its own zero crossings.
Removing the 4 kHz component removes the very property that makes the waveform
readable. (The even tap count contributes: it carries a *half*-sample delay,
which is what walks the tone onto its zero crossings. An odd-length design with
a Nyquist null would not, and is the thing to try if this is picked up again.)

There is a second finding in the stall. The band limit is applied to everything
the caller receives, including the emulated answerer's Phase-3 output — which is
broadband noise — and the walk to `0x00b0` does not survive losing its top end.
So part of the caller's progress through `0x0092 → 0x0095 → 0x00b0` in the
current rig is being driven by noise energy rather than by structure. That is
worth knowing independently of pin 1.

**Left off by default**, therefore: `rx_bandlimit_hz=0`, and `receive()` is
byte-identical to before unless it is asked for. Shipping it on would trade a
real fidelity improvement for a working walk, and it would not buy the square
anything. `tests/test_analog_rx_bandlimit.py` asserts the Nyquist null, the
unity passband, and the two magnitude facts above, so the negative result is
re-runnable rather than re-arguable.

Regression with it off: V.22bis `0x00d0` on both ends (21.88 s / 24.16 s,
`DATASTATESpeed 0x0047`), V.90 `0x00d0` with `CTS｜DSR` at 30.18 s.

### What this leaves

The square reaches the demodulator intact and comes out as noise at saturation
(Session 257), and neither level (258) nor band (259) is the difference. What
has not been tested is the one structural difference left between this rig and
the hardware the gold recording came from: **the codec runs at 9600 Hz for the
whole call**, because V.8's tone constants need it, where V.90 is an 8 kHz
standard and the page's own front end decimates back to 8000. A component at
4 kHz is at Nyquist on an 8 kHz codec and is an ordinary in-band tone on a
9600 Hz one, which is exactly the difference between "harmless" and "noise the
matched filter integrates". Per-page codec rate — what the page descriptor's
`Samplerate` field is for — is the next thing to build.

Captures: `artifacts/loopback-v32-goal/{bl-0,bl-3600,bl2-0,bl2-4000,prime-bl,regress3-v22,regress3-v90a}`.

## Session 260: per-page codec rate — retracted, the page asks for 9600

Session 259 closed by proposing a per-page codec rate, on the reasoning that
V.90 is an 8 kHz standard and a 4 kHz component is at Nyquist on an 8 kHz codec
but in-band on a 9600 Hz one. **That is wrong, and `docs/handoff.md` already
said so** — "✗ Wrong, and the page says so itself — do not build a per-page
codec rate" — which Session 259 did not check before proposing it. Re-measured
on the current tree, sampling the rate triple across a whole call:

```text
    t        Symbolrate   Samplerate   Samplebuffersize   0x3754/0x3755
  0.000 s  (boot/DIAL)  9      8 -> 8000 Hz     4            16/36
  0.100 s  (V.8)        0      4 -> 9600 Hz     4            15/15
  9.200 s  (V.8)        4      4 -> 9600 Hz     4            15/15
  9.400 s  (V.90 APCM)  4      4 -> 9600 Hz     3            15/15
```

**The V.90A page asks for `Samplerate` code 4 — 9600 Hz — the same rate V.8
asked for.** It changes only `Samplebuffersize`, 4 → 3, which makes 9600/3 =
3200 symbol/s, the V.34-family upstream symbol rate the analogue side transmits
at; and it does that itself, visibly, at 9.40 s. The internal ratio
`DM(0x3754)`:`DM(0x3755)` stays 15:15 across the page change, so there is no
resampling ratio to follow either.

So `--analog-codec-rate 9600` is what this page wants, the rig already gives it,
and there is nothing per-page to build. The 8 kHz internal rate the six-word
buffer runs at (Session 255: `PM 0x0C2E` at 8015/s against `PM 0x1733` at
9600/s) is the page's own decimation from that 9600, not a codec rate the
harness is getting wrong.

The one real discrepancy the table shows is at boot: the page publishes
`Samplerate` 8 = 8000 Hz for the first 0.1 s while the rig is already at 9600.
It lasts until V.8 loads and asks for 9600, and nothing in the V.90 path reads
across it. Building a rate-following mechanism for that window would put every
data-mode result the rig has at risk to fix 100 ms of DIAL, so it is recorded
rather than acted on.

**What this retracts.** Session 259's closing paragraph, and with it the last of
the "the square arrives wrong" family. The chain from Session 257 stands
unchanged and unexplained at exactly one link: `SR0`, the matched filter's
output, is a clean period-6 sign sequence for a sine and noise at saturation for
the gold square, at the same amplitude, through a receive path whose gain
(258), band (259) and now codec rate (260) have each been measured and
exonerated in turn.

What has *not* been examined is the matched filter itself — `PM 0x0B5A`, 216
taps from `PM 0x1EB4`, over a 248-deep delay line, fed two samples a pass by
`PM 0x0EBC`. Every session so far has assumed it is correctly fed and has gone
looking upstream. The coefficients are readable and the delay line's contents
are measurable, so "what is this filter matched to, and is the square inside its
band" is answerable directly rather than by elimination — and after three
exonerations upstream, it is where the remaining evidence points.

Capture: `artifacts/loopback-v32-goal/ratetriple`.

## Session 261: `PM 0x0B5A` is not a matched filter — it is an adaptive equaliser, and it diverges

Three sessions exonerated the receive path upstream of `PM 0x0B5A` by
elimination. Read directly, the filter itself is the defect.

### It is adaptive, and its coefficients are in program memory

`PM 0x0B5A` is a 216-tap FIR (`CNTR = DM(0x0E6D)` = 0xD8) over the 248-deep
delay line at `DM(0x2117)`, with coefficients from `PM 0x1EB4`. Those
coefficients are not a table — `PM 0x0B7E` **writes** them:

```text
0b7f: DO $0B86 UNTIL NOT CE
0b80:   MR = MX0 * MY1 (SU)              ; input x step size DM(0x0EA4)
0b81:   MR = MR1 * MY0 (SS), MX0 = DM(I0,M1)   ; x error DM(0x0E66)
0b83:   SR = LSHIFT MR0 (LO, OR), AY0 = PM(I5,M4)
0b84:   AR = SR0 + AY0,          AY1 = PM(I4,M4)
0b85:   PM(I5,M5) = AR, AR = SR1 + AY1 + C
0b86:   PM(I4,M5) = AR
```

`coefficient += step x error x input`, accumulated in double precision across
two PM arrays — `PM 0x1EB4` the high words, `PM 0x21F4` the low. It is an LMS
adaptive equaliser. (A first dump found all 216 taps zero and looked like a
dead table; that dump was taken 2 s after residency, before the filter had
adapted at all.)

### It adapts in two windows, and the second one blows it up

`PM 0x0B69` gates adaptation on bit 2 of `DM(0x20ED)`. Sampled across a call:

```text
    t        state   DM(0x20ED)  bit 2   DM(0x0E66)  -- the LMS error
  9.4-12.6   0060..0092  0x0200  clear        0
 12.7-16.6   0094,0095   0x3714  SET      -2328 .. +2855, mostly +/-100..2000
 16.7-20.8   00b0,00b3   0x1210  clear     660 .. -79
 20.9-23.5   00b3        0x3b14  SET      -30858, +32767, +29594, -25723, ...
 23.6-       00c0        0x1210  clear    -16012, frozen
```

The first window is a healthy training: the error settles to a few hundred. The
second, entered at 20.8 s when a record changes `DM(0x20ED)` from `0x1a10` to
`0x3b14`, is divergence — the error swings the full 16-bit range and saturates
at `32767`. Adaptation then switches off at `0x00c0` and the result is frozen
for the rest of the call.

Dumping the coefficients either side measures the damage:

```text
 dump at                        RMS tap   |tap| > 30000   peak of |H|
  17.4 s   after window 1          1312         0            296 Hz
  19.9 s   after window 1          1312         0            296 Hz
  26.4 s   after window 2         19032        22           1248 Hz
```

**A 14.5x blow-up, with 22 of 216 taps against the rail.** What the detector
reads all through `0x00c0` is the output of a 216-tap filter whose coefficients
are full-scale noise.

### And that is exactly the sine/square asymmetry

This closes Session 257's open link without needing anything else. A
random-coefficient filter is still **linear and time-invariant**, so a pure tone
goes through it as a pure tone — only its amplitude and phase change. That is
why a 1333.33 Hz sine comes out of `PM 0x0B5A` as a clean period-6 sign
sequence and drives `DM(0x2551)` to 138: *any* filter would do that.

The gold tone is a period-6 **square**, which is two components — 1333.33 Hz and
one at exactly 4 kHz. A diverged filter gives those two components arbitrary
relative gain and phase, so their sum is no longer a square, no longer
constant-magnitude, and no longer three-positive/three-negative. It arrives as
noise at saturation, which is what was measured.

So the sine never demonstrated a healthy path. It demonstrated the one stimulus
that cannot detect this fault. Every "the square arrives wrong" hypothesis —
gain (258), band (259), codec rate (260) — was chasing a symptom of a wrecked
equaliser.

### What is not yet established

Whether the divergence is a defect in this rig or the correct response to an
input that is not what the page expects. The second window trains at `0x00b3`
against the primed recording, which is a replay rather than a peer reacting to
this caller, and an LMS trained against a reference it cannot predict is
*supposed* to run away. The step size `DM(0x0EA4)` = `0x8CCC` and shift
`DM(0x2121)` = `-4` are the other candidates and are one measurement each.

The question to settle first is therefore which of the two windows the gold
run65 call itself had — the recording is a capture of a *successful* connection,
so the answering side's own equaliser converged. Next: run the same dump
schedule on the answerer's V.90D page, where the filter is the same shape, and
compare its coefficient trajectory against this one.

Captures: `artifacts/loopback-v32-goal/{coef,coef-sine-a16625,coef-gold-a16632,lms,lms2,coef-t8,coef-t10.5,coef-t17}`.

## Session 262: tested against the V.90D page — it is not a generic rig defect

The hypothesis after Session 261 was that the equaliser divergence is a defect
in this rig's arithmetic, which would make it the common cause behind everything
above. Tested against the answering page in the same call, it is not.

### The V.90D page carries the same equaliser, in the same shape

`PM 0x0B5A` is different code on overlay `0x026a` — a *complex* filter,
`MR = MR − MX1·MY1` / `MR = MR + MX0·MY0`, stride `M3 = 2`, 54 complex taps
(`L5 = L6 = 0x0036`) whose PM cursors come from `DM(0x2023)`/`DM(0x2024)` —
but the surrounding structure is the exact counterpart of the caller's:

```text
                       V.90A caller (0x026b)      V.90D answerer (0x026a)
adaptation gate        bit 2 of DM(0x20ED)        bit 2 of DM(0x1FED)
"already in data?"     DM(0x20F9) XOR 0x00D0      DM(0x1FF7) XOR 0x00D0
countdown word         DM(0x0EA5) = 0x7FFF        DM(0x0EF0) = 0x7FFF
coefficients           PM 0x1EB4 / 0x21F4         PM 0x1F84 / 0x1FC4
taps                   216 real                   54 complex
```

Both are the same block index — 4 — of their page's own record block, which is
why the two words differ only by the block base.

### The control: a V.90D connect that succeeds, in the same rig

Priming the answerer's receive with the gold analogue upstream
(`EICON_RX_PRIME=artifacts/eicon-native-tower/run65.rx.ulaw:12.4:50:13.0`) walks
its own V.90D firmware to **`TrnProgress 0x00d0` with `CTS｜DSR｜DCD` at
26.68 s**. That is a successful connection, on the same emulator, in the same
call, with the same MAC, the same `(SU)` multiply and the same double-precision
PM accumulate the caller's LMS uses.

Its adaptation windows and coefficient trajectory:

```text
  9.0-11.2 s   states 0x7a,0x7b   DM(0x1FED)=0x331c  bit 2 SET
 11.2-22.3 s                      DM(0x1FED)=0x0210  bit 2 clear
 22.4-27.6 s   0xc0..0xd0         DM(0x1FED)=0x171c  bit 2 SET

                        taps changed     max |delta|     RMS
  9.6 -> 20.6 s  real       43/54              10       2703 -> 2704
  9.6 -> 20.6 s  imag       47/54               9       7247 -> 7247
 20.6 -> 27.6 s  real       50/54             804       2703 -> 2681
 20.6 -> 27.6 s  imag       50/54            1054       7247 -> 7246
```

**Fifty of fifty-four taps move and the norm does not.** That is textbook
convergent LMS: the filter is tracking, and its energy stays put. Against the
caller's, over its own second window:

```text
  V.90D  RMS 2704 -> 2681   (-0.9%),  0 taps at the rail
  V.90A  RMS 1312 -> 19032  (+1350%), 22 of 216 taps at the rail
```

**Same rig, same arithmetic, opposite outcomes.** So the divergence is not a
generic defect in the emulator's MAC, its signed/unsigned multiply or its
add-with-carry — a broken one of those could not let the answerer's equaliser
converge in the same seconds of the same call. (`(SU)` was also read directly:
`2100ops.inc` case `0x05<<13` takes X signed and Y unsigned, so the step word
`DM(0x0EA4) = 0x8CCC` enters as +36044 and not as −29492, which was the specific
sign-flip worth ruling out.)

### One structural difference, and it is the caller's page that is unusual

The answerer keeps adapting **through** `0x00c0` to `0x00d0` — bit 2 stays set
from 22.4 s to the end. The caller's page **clears** bit 2 on entry to `0x00c0`
(`DM(0x20ED)` `0x3b14 → 0x1210`) and freezes whatever the previous window left.
So the caller has one window to get it right and no opportunity to recover,
where the answerer is still correcting while it walks into data mode. That is a
per-page record difference, not a rig behaviour.

### What this does and does not settle

It settles that the arithmetic is sound and that "a rig defect underneath
everything" is the wrong frame: the same code path converges on the other page,
in the same call, on gold input.

It does not settle why the caller's window diverges. Its input there is also
gold — the primed `run65.ulaw` — so "bad input" is not automatically the answer
either. ⚠ The honest caveat is alignment: `EICON_RX_PRIME_SYNC` anchors only
`0x00b0` and `0x00c0`, and the divergent window sits at `0x00b3` **between**
them, so what the caller trains against there is a replay running on its own
clock rather than the segment the gold analogue modem saw at its `0x00b3`. An
LMS trained against a reference that is the right signal at the wrong time is
expected to run away, and that is now the first thing to test — a third anchor
at `0x00b3` costs one line of the milestone map.

The other two candidates stay open and are one measurement each: the step size
`DM(0x0EA4) = 0x8CCC` and the shift `DM(0x2121) = -4`, neither of which has been
compared against the answerer's equivalents.

Captures: `artifacts/loopback-v32-goal/{ans-probe,v90d-lms,v90dc-2,v90dc-13,v90dc-20}`.

## Session 263: the `0x00b3` anchor — the divergence was the misalignment, and it was not the blocker

Session 262 predicted that the caller's equaliser diverges because
`EICON_RX_PRIME_SYNC` anchors only `0x00b0` and `0x00c0`, leaving the divergent
window at `0x00b3` training against the right signal at the wrong time. Adding a
third anchor tests it directly, and the prediction holds.

```text
  00b3@     RMS tap after window 2   taps at the rail   DM(0x2551) max
  (none)            19032                  22                 0
    14                342                   0                 0
    16              10252                   0                 1
    18                250                   0                 0
    20               1312                   0                 0
```

**Every anchor time eliminates the divergence** — 22 taps against the rail
become none, at all four. And `00b3@20` lands on **1312**, which is exactly the
value window 1 converged to: with the replay aligned there, the second window
does no damage at all. Across window 2 itself:

```text
  no anchor   1312 -> 19032    x14.5, 22 taps railed
  00b3@18     1312 ->   250    bounded, none railed
```

So the divergence was an artefact of the priming instrument, not of the page,
and it is now controllable. That is worth having on its own — the recipe gains
one field.

### And it does not fire the detector, which retires a claim

`DM(0x2551)` stays at 0 (once, 1) across the whole sweep. Watching the
producer's input directly with `00b3@18`:

```text
  SR0 into PM 0x0C27, gold square, 0x00b3 anchored
    113, -45, 152, 146, 118, -67, -13, -44, -50, 233, 134, -51, 13, -252, ...
    +-+++----++-+--+--+--++-++--+++++--+++++-++-++-----++++--+-+----++-----+
```

The saturation is gone — `±30000` has become `±250` — and the period-6 structure
still is not there.

**⚠ This retires Session 261's "root cause", which was over-claimed.** The
reasoning there was that a diverged filter scrambles a two-component signal
while passing a one-component one, so the equaliser explained the sine/square
asymmetry. The reasoning is sound and the divergence is real, but it was not the
blocker: with the divergence removed the square still does not read. And the
control that made the story look complete is weaker than it appeared — going
back to the Session 257 dumps, **the sine run's equaliser was diverged too**
(coefficient RMS 19029, essentially the same as the gold run's 18014). The sine
reached `DM(0x2551) = 138` *through a wrecked filter*, which is exactly what
"any LTI filter passes a pure tone" predicts — so that measurement never
depended on the equaliser being right, and could never have distinguished the
two hypotheses. It was a confound, and it is removed rather than confirmed.

### What the fix exposes underneath

With the equaliser bounded, `SR0` is **±250** where the sine drove it to
**±10,000** — about 40x down — and the answerer's equaliser converges to RMS
2703/7247 against the caller's 250. So the caller's now converges to a filter
with roughly an order of magnitude too little gain, and the detector's
`|x| >= 0x200` floor cannot be met downstream of it.

That is a different problem from the one Session 258 examined and closed:
that measured `DM(0x2131)`, *upstream* of the equaliser, and correctly found it
healthy. The gain now in question is the equaliser's own converged norm, which
nothing has looked at until this session had a bounded one to look at.

**Next:** the caller's converged norm against the answerer's, and the two
parameters that set it — step `DM(0x0EA4) = 0x8CCC` and shift
`DM(0x2121) = -4` — read on both pages and compared. Those were listed as open
in 262 and are now the whole remaining question.

Recipe note: the milestone map that keeps the equaliser bounded is
`00b0@17.96,00b3@20,00c0@23.14`.

Captures: `artifacts/loopback-v32-goal/{anch-14,anch-16,anch-18,anch-20,anch18-pre,anch18-sr0}`.

## Session 264: the two equalisers side by side — step size, loop gain and converged norm

### Step size, read off both update routines

The answerer's LMS update is `PM 0x0BAB..0x0BBD`, the counterpart of the
caller's `PM 0x0B7E..0x0B86`. They build their step differently:

```text
caller  V.90A   MY1 = DM(0x0EA4) = 0x8CCC, taken UNSIGNED by the (SU) multiply
                  -> 36044/65536 = 0.5500
                SE  = DM(0x2121) = -4, ASHIFT right 4 -> /16
                effective mu = 0.03437

answerer V.90D  SR0 = 0x8000; SR = LSHIFT SR0 (HI) by SE = DM(0x2042) = -6
                  -> SR1 = 0x8000 >> 6 = 0x0200 = 512
                effective mu = 512/65536 = 0.00781
```

So the caller's step is **4.4x** the answerer's — and it also runs **216 real
taps against 54 complex**, so the quantity that governs LMS stability, `mu·N`,
is **17.6x** larger on the caller. That is the measurable difference between a
loop that diverges on any timing imperfection and one that does not, and it is
not a rig value: both numbers come from the firmware's own data.

### Converged gain, as filter response rather than tap RMS

Tap RMS is not comparable across a 216-tap real filter and a 54-tap complex
one, so both were evaluated as `|H(f)|` with the taps read as 1.15 fractional:

```text
                                        RMS tap   peak |H|   |H| @1333 Hz
 caller V.90A, unanchored (diverged)      18017      18.24        6.25
 caller V.90A, 0x00b3 anchored             1312       1.76        0.25
 answerer V.90D (reaches 0x00d0)           5463       2.71        0.91
```

The answerer's converged equaliser has **0.91** at the tone frequency. The
caller's best bounded result is **0.25** — 3.6x short — and its diverged one is
6.25, which is where the outsized `SR0` values of Sessions 257-261 came from.

### Lowering the caller's step confirms the mechanism and does not fix the filter

Pinning `DM(0x2121)` to bring the caller's `mu` down to the answerer's:

```text
 DM(0x2121)   effective mu   RMS tap   rails   |H| @1333   DM(0x2551) max
   -4 stock        0.03437     19032      22        6.25        0
   -6              0.00859       239       0        0.05        0
   -8              0.00215       559       0        0.19        0
 answerer          0.00781      5463       0        0.91     (reaches 0x00d0)
```

At the answerer's step size the caller's equaliser stops diverging — no taps at
the rail — which confirms `mu·N` as the mechanism behind the blow-up. But the
converged gain gets *worse*, not better: 0.05 and 0.19 against the anchored
stock run's 0.25 and the answerer's 0.91.

**So there are now two independent ways to stop the divergence — align the
replay at `0x00b3`, or reduce the step — and neither produces a filter anywhere
near the answerer's, and neither fires the detector.** `DM(0x2551)` is 0 in
every one.

### What that converges on

The caller's equaliser is not mis-parameterised and not mis-implemented. It is
**under-trained**. Every configuration that keeps it stable leaves it 3.6x to
18x short of the gain the answerer reaches, because the answerer trains against
`run65.rx.ulaw` — a real analogue modem's output, the other half of a
conversation that completed — while the caller trains against a one-way replay
that does not respond to it. A large `mu·N` is what a firmware designer chooses
when the training sequence is known, short and reactive; against a recording it
is exactly the wrong choice, and lowering it only trades divergence for
under-convergence.

That is the same wall this whole thread keeps arriving at from new directions,
now with a number on it: **0.91 against 0.25**, and no parameter in the page
closes the gap. Pin 1 needs a reactive V.90D peer on the SIP leg, which is what
[[v90a-deadlock-and-tone-comb]] concluded from the signal side two hundred
sessions ago and what the equaliser now says from the receiver side.

Captures: `artifacts/loopback-v32-goal/{steps,steps2,ans-lms,mu-0xfff8,mu-0xfffa}`.

## Session 265: a segment-holding peer — what a replay can react to, and what it cannot

Asked for a reactive V.90D peer. A full one — Phase 1-4, INFO, probing, MP/CP,
a PCM downstream computed from what the caller actually sent — is a project, not
a session, and the rig already contains a reactive V.90D: the emulated answerer,
whose own firmware reaches `0x00d0` when it is fed a valid upstream. What it has
never had is a peer that **sustains its segment until the caller responds**,
which Session 263 measured as the thing that breaks the caller's equaliser. That
is what was built.

### Windowed milestones

`EICON_RX_PRIME_SYNC`'s map now takes a window as well as a point:

```text
  00c0@23.14              point   -- jump the cursor here on entry (as before)
  00b3@18.54-23.06        window  -- hold this segment while the state holds
```

Inside a window the cursor **loops within the segment** for as long as the
caller stays in that state, instead of running past its end. The boundaries come
from the gold call's own trace: `run65.endpoint.log` has the digital side in
`0x00b2` — the long Phase-3 training segment — from **18.54 s to 23.06 s**, and
the caller's 6.8 s `0x00b3` is that segment's counterpart by role and duration.

### What it does

```text
  caller dwell in 0x00b3     6.8 s  ->  15.3 s, sustained by the loop, then
                                         advancing 0x00b6 -> 0x00c0 normally

                              RMS tap  rails  |H| @1333  DM(0x2551) max
  two anchors (baseline)        19032     22       6.25        0
  00b3 point anchor              1312      0       0.25        0
  segment hold (this build)      1312      0       0.25        6
  answerer V.90D (0x00d0)        5463      0       0.91       --
```

**`DM(0x2551)` moves for the first time on the gold recording.** Every previous
configuration — every anchor time, every gain, every band, every step size —
left it at exactly 0 from `run65.ulaw`; the only thing that had ever moved it
was a synthetic sine. Holding the segment gets it to 6, with a 5.8% pattern
rate. The equaliser stays bounded, no taps at the rail.

Six is not thirty-two, and pin 1 does not clear. Tightening the `0x00c0` window
onto the tone and its reversal (`23.102-23.226`, `23.102-23.23`) does not help
either — 5 and 5 — so the limit is the ~5% pattern-validity rate rather than
which samples the window contains, and that traces back to the converged gain of
0.25 against the answerer's 0.91.

### What this is not

It reacts to the caller's **state** and to nothing else. It cannot answer a
handshake, cannot negotiate or change rate, and cannot produce a single sample
the recording does not already contain. The bidirectional gates from `0x00c0`
onward are exchanges — the caller says something and the response depends on
what it said — and no replay, however it is cursored, can be on the other end of
one. Session 253 said this from the signal side and it is still true.

What would actually close it is a V.90 digital modem on the SIP leg: the
`slmodemd` role from the gold call, either driven as an external process over
the existing SIP/RTP path or implemented against `docs/ITU Docs`. The parts this
rig already has that such a build would use are the INFO framer
(`tools/eicon_info_replay.py`), the V.90 DPCM replay and state records
(`tools/v90_dpcm_replay.py`, `v90_dpcm_state_records.py`) and the downstream
validator (`tools/v90_tx_validate.py`). That is the honest next step and it is
much larger than anything in this thread so far.

Regression, with the windowed form in the tree and unused: V.22bis `0x00d0` on
both ends (21.80 s / 24.08 s, `DATASTATESpeed 0x0047`), V.90 recipe `0x00d0`
with `CTS｜DSR` at 30.18 s. 640 tests OK;
`tests/test_rx_prime_sync_windows.py` pins the point form against regression and
the window form against the gold boundaries.

Captures: `artifacts/loopback-v32-goal/{peer,peerw,regress4-v22,regress4-v90a}`.

## Session 266: differential against the real card — the transmit is 83% zeros

The framing for the last nine sessions was wrong and this corrects it. Both
firmwares are known good, and the pairing works on the hardware being emulated.
That makes "the loopback needs a better peer" a rationalisation: **the loopback
already has a reactive V.90D peer — the emulated answerer, running the real
firmware — and if a known-good pairing fails here, the defect is ours.** The
test that follows from that framing is differential against the real card's own
capture, and it was never run.

### The state machine is faithful. The transmit is not.

Feed the emulated answerer exactly what the real card received
(`run65.rx.ulaw`) and compare its walk against the real card's own log:

```text
              real card   emulated   offset
  0x00b1        18.500      17.680    0.820
  0x00b2        18.540      17.720    0.820
  0x00b3        23.060      22.240    0.820
  0x00b6        23.100      22.280    0.820
  0x00c0        23.140      22.320    0.820
  0x00c2        23.240      22.420    0.820
  0x00c4        26.820      26.000    0.820
  0x00c8        27.360      26.540    0.820
  0x00cc        27.400      26.580    0.820
  0x00d0        27.500      26.680    0.820
```

**Every milestone, constant to the sample.** The V.90D emulation reproduces the
real card's entire Phase-3 and Phase-4 walk. Now the same comparison on the
transmit, aligned by that 0.820 s:

```text
  window            real RMS   emu RMS    corr   real ZCR   emu ZCR
  0x00b2 early           686       403   0.001      0.502     0.333
  0x00b2 mid            1369       360  -0.001      0.500     0.060
  0x00c2                 603       400  -0.010      0.542     0.060
  0x00d0 data            607       258  -0.004      0.546     0.333
```

Correlation zero, level 2-4x low, and a zero-crossing rate of **0.06 against
0.50**. Run lengths of identical consecutive samples say it plainly:

```text
  real card    mean run 1.27   (83% are runs of 1)
  emulated     mean run 16.63  (25% in runs of 23, 25% in runs of 41)
```

### Localised to one line, and measured

`dial_tikrnl_drive.py`, `frame_fast`:

```python
if self.resident == V90D_ID:
    value = self.dm[DM_TX_POINTER]      # DM(0x3FB4), polled once per line sample
else:
    pointer = self.dm[DM_TX_POINTER] & 0x3FFF
    value = self.dm[pointer] if pointer else 0
```

Its own comment records that this branch had never been exercised: *"It reads
the same 0 either way while the serializer is idle — which is the state the
loopback is currently stuck in — so this changes nothing today and everything on
the first frame that does publish."* The prime is the first thing that has ever
made it publish.

Sampled every tick in data mode, `DM(0x3FB4)` carries a real sample **once every
six** and reads `0x0000` in between:

```text
  180001  3fb4=0xfc90   180007  3fb4=0x02f0   180013  3fb4=0xfe18   180019  3fb4=0xfd90
  (every other tick in that window reads 0x0000)
```

A write-watch names both writers and the ratio:

```text
  PM 0x19EF   writes 0x3764   26,720 times   <- a POINTER to the block
  PM 0x1A1F   writes 0x0000   22,267 times   <- 83% of published values are zero
  PM 0x1A1F   writes samples  ~250 each of 0xff24, 0x00dc, 0x0370, 0xfea8, ...
```

**So the emulated V.90D transmit is 83% zeros**, and that — not a missing peer,
not gain, not band, not the equaliser — is why the caller cannot train on it.
Everything from Session 257 onward was measuring the consequences of a transmit
path that emits a sample one tick in six.

### The fix, and why it is not being guessed at here

`PM 0x19EF` publishes `0x3764` into `DM(0x3FB4)` every frame, which is a
*pointer*, and `PM 0x1A1F` then overwrites it with a value. The existing comment
concluded from that ordering that the pointer is dead and the value is the
sample. The measurement says the value is zero five times in six, which is much
more consistent with `0x3764` being a live block pointer and the page publishing
**six samples per serializer pass** — the generic branch's `self.dm[pointer]`
being right after all, and needing to be drained rather than read once.

That is a change to the one path every V.90 result in this tree runs through, at
the end of a long session, so it is written down rather than attempted. The next
step is to watch `DM(0x3764..)` across a serializer pass and count how many
words it publishes per frame; if it is six, the branch inverts and the transmit
rate is restored.

⚠ **This supersedes Session 265's conclusion.** "Pin 1 needs a reactive V.90D
peer on the SIP leg" was wrong. The peer is real, reactive, and — as the
milestone table above shows — faithful. Our transmit path is broken.

Captures: `artifacts/loopback-v32-goal/{ans-probe,txring2,txprod}`.

## Session 267: the block, not the pointer — counted before it was changed

Session 266 ended with a hypothesis and an instruction not to guess: `0x3764`
might be a live block pointer with the page publishing six samples per
serializer pass, which would make the generic dereference right and the fix a
drain. **Counted, that is not what page 14 does.** The census
(`EICON_V90D_TX_CENSUS=1`, `tools/v90d_tx_block_census.py`) over 151,202
page-14 frames of a data-mode call, per frame and per `TrnProgress`:

```text
  TrnProgress 00d0: 25731 frames, 4289 published a nonzero sample (16.7%)
      DM 20de:  1.167/frame     serializer cursor + 1/6 generator reset
      DM 3fa7:  2.167/frame     clear + serializer output + 1/6 refill
      DM 3fa8:  1.167/frame     clear + 1/6 refill
      ... 3fa9, 3faa, 3fab, 3fac the same
      DM 3fb4:  2.000/frame     context restore + the published sample
      DM 3764:  never written on page 14 at all
```

`DM(0x3764)` takes **no writes** while page 14 is resident — the one write in
the whole call lands at page load — so there is no block there to drain, and
the disassembly says why the pointer is there at all:

```text
  19ee  DM($3FB4) = AR      ; AR = DM($3607), the page's saved copy: a context
                            ;   *restore*, not a prime. 0x3764 is what an
                            ;   earlier page left in the shared word.
  1a1b  AR = DM($3FB4)      ; ... and the epilogue saves it back to 0x3607
  1a1d  AR = DM($3FA7)      ; the serializer's output port
  1a1e  DM($3FB4) = AR      ; the sample leaves here, one word per sample
```

So `frame_fast`'s page-14 branch — take the value, do not dereference — is
**right**, and 83% of the values it takes were zero for a reason one level up.

### The mapping frame lives one sample and has to live six

```text
  2a4e  AX0 = $3FA7         ; generator, 0.167/frame: reset the cursor
  2a4f  DM($20DE) = AX0
  2eed  I0 = DM($20DE)      ; serializer, 1.000/frame: one slot per sample
  2eee  AR = DM(I0,M1)
  2eef  DM($20DE) = I0
  2ef1  DM($3FA7) = SR0     ; the slot, scaled, into the output port
  06c3  I0 = $3FA7          ; resident kernel frame path, every sample:
  06c5  DO $06C6 UNTIL CE   ;   zero all six words of the mapping frame
  06c6  DM(I0,M1) = $0000
```

The generator fills six words once per 1333 Hz mapping frame; the serializer
reads one slot per 8 kHz sample, so the block must survive six samples; the
kernel's frame path zeroes all six of them **every** sample. Five of six reads
therefore find zero — `16.7%` nonzero, to three digits, in every transmitting
state. Split by which kernel entry the harness calls, the clear is in the frame
half and the whole page-14 transmit chain (generator, serializer, publish) is
in the sample-continuation half, so the clear always precedes the read.

The native tower has held that store since Session 62
(`EICON_V90D_TX_BLOCK_HOLD`); the direct backend — the one every loopback
answerer runs on — never had it. It does now, same env name and default,
`Card._hold_tx_block()`, restored on the way out of page 14.

### Differential against the real card, before and after

Same call both times: the answerer primed with `run65.rx.ulaw`, the capture the
real card received, aligned by its own `0x00b1` at 18.28 s against the card's
18.50 s.

```text
  window     real rms  zcr   run | before rms  zcr   run | after rms  zcr   run
  18-19 s        881  .497  1.61 |       404  .169  3.00 |      988  .504  1.98
  23-24 s        666  .510  1.18 |       700  .167  3.00 |     1715  .500  1.31
  27-28 s        602  .544  1.06 |       258  .165  3.00 |      620  .535  1.06
  28-30 s        605  .535  1.06 |       257  .164  3.00 |      626  .547  1.06
```

Published nonzero goes `16.7% -> 100.0%` in every transmitting state, the
zero-crossing rate goes `0.16 -> 0.50` against the real card's `0.50`, mean run
length `3.00 -> 1.06` against `1.06`, and data-mode RMS `258 -> 620` against
`602`. The walk is unchanged and still reaches `0x00d0`, holding it for 30,957
frames against 25,731 before. Sample-level correlation stays near zero and
should: the payload and carrier phase are ours, not the recorded call's — the
statistics are the comparable part, and they now match.

⚠ **Still open:** the `23-24 s` window transmits at RMS 1715 against the real
card's 666. That is a level question in `0x00c2`, not a rate one, and it is the
next thing to measure rather than to correct.

⚠ **And the hold is a firmware patch, not an explanation.** The kernel really
does zero the block every sample in this build, and a card that transmits
correctly cannot be doing that — so something about *when* the harness enters
the frame path is still wrong, and the hold stands in for it. 645 tests OK.

Captures: `artifacts/loopback-v32-goal/{txcensus2,txcensus3,txhold}`.

## Session 268: c2 predicate disassembly separates the rate gate from CP

A resident-page-14 PM dump of the corrected unpinned direct pair decodes the
two predicates that had been conflated in the state-table notes. `PM 0x3015`
computes `0x04b0 - DM(0x2117)` and is the rate/threshold test used by the
record's `test[04]`; it does not inspect the CP result. `PM 0x3019..0x3038`
does the CP comparison directly:

```text
(DM(0x206d) & 0x000f) == 0x000f
(DM(0x206e) & 0xfffc) == 0xfff8
```

The live answerer trace then shows the expected producer, PM `0x0cae..0x0cb0`,
writing `DM(0x206d/0x206e)` repeatedly before bounded executions of `0x3019`.
The values are ordinary rolling dibits (`0012/c000`, `004b/0000`,
`04b0/0009`, …), never the CP mask; the endpoint ends at `0x00b2` in this
short run. This rules out a stale result-word handoff or a dispatcher failure:
the remaining defect is upstream waveform/control decoding in the live
V.90D exchange. The state-record decoder now labels condition indices `0x04`
and `0x18` with these meanings.

## Session 269: unprimed retry versus warm-start convergence

The direct unpinned pair was extended to 65 seconds with the V.90D
mapping-block hold active. It remained at caller `0x00c0` / answerer `0x00c2`;
simply waiting does not trigger a useful second attempt.

A synchronized diagnostic prime changes that result. Feeding both endpoints the
real `run65` directions only through 25 seconds, then releasing both receive
paths back to the live RTP loop, makes the first attempt fall back but the next
fully live attempt walk both sides to `0x00d0`:

```text
answerer: 0x00b0 -> 0x00d0 at 37.10 s
caller:   0x00b0 -> 0x00d0 at 39.28 s
```

No pins are active after release. The second caller ladder is materially
different (`0x0090 -> 0x0092 -> 0x00a4 -> ...`), so this is evidence of a
missing initial training transition/state handoff, not proof that the normal
loopback is complete. The prime remains diagnostic and is not a valid goal
solution.

The caller's LMS shift was tested both globally and only during `0x00b3` using
the opt-in `EICON_V90A_EQ_SHIFT` / state pin. Both variants fail to improve the
unpinned pair; the global `-6` shift regresses earlier to `0x0095/0x00b0`.
### Session 270 — V90A transmit silence selector is the immediate wall, but not a safe override

The caller-side kernel-dispatch DM sampler captured the decisive transmit
selection during the unpinned `analog109`/`pri117` pair. While the caller is
parked in its `0x0092 -> 0x0094 -> 0x0095 -> 0x00b0/0x00b3` phase, `DM(0x2119)`
is `0x32c4`, the V90A silence writer. `DM(0x3764)` consequently remains zero.
It changes to `0x32ca`, the symbol-buffer reader, only when the caller reaches
`0x00c0` (about 20.7 s). This initially looked like the source of the weak
upstream exchange, but the native selector trace shows the same reader/silence
sequence at the corresponding V90A states; the silence is therefore an
intentional Phase-3 segment, not yet a proven emulation defect.

The existing opt-in `EICON_V90A_TX_SHAPER=reader` override was then tested at
14 s and 17 s. Both runs were negative: the answerer advanced only to
`0x00b1`, while the caller stopped at `0x0095`. Therefore `0x32c4` is not
corrected by blindly forcing `0x32ca`; the missing control/record selection
must be reproduced so the symbol reader is enabled at the correct Phase-3
segment and with the corresponding symbol state. The override remains
diagnostic-only. The loopback wrapper now forwards `--trace-retrain`, but the
normal stall emits no retrain marker. A follow-up control capture also matched
the native `DM(0x20f9)` state sequence (`0x0092`, `0x0094`, `0x0095`, `0x00b3`,
then `0x00c0`). The next target is the missing transition input that should
move the caller through that record ladder, rather than the transmit selector.

### Session 271 — source cursor comparison corrected; media A/Bs remain negative

The V.90A overlay disassembly confirms that `PM 0x2479` uses `DM(0x3fca)` as
the analogue source-ring cursor: it temporarily sets `L4=4`, consumes one word,
and stores the advanced cursor. The live caller enters overlay `0x026b` with
`DM(0x3fca)=0x209c`. The earlier comparison against `0x1fe0` in `run65` was
invalid: that dump is from the digital V.90D endpoint, not the analogue caller.
No source-cursor correction is justified by that comparison.

Unpinned A/Bs also leave the baseline unchanged: caller TX gain `+2 dB`, caller
RX gain `+2 dB`, output resampler phases 1 and 2, and the 210 ms/native-like
buffer each fail to move the pair past caller `0x00c0` / answerer `0x00c2`.
The remaining issue is therefore the reactive V.90D/V.90A training handoff, not
a simple level, filter, phase, or delay parameter.
## Session 272: V90A source mailbox is real, but PRBS is not the handshake fix

The runtime PM dump of the analog page's initializer shows that the V90A
source ring is deliberately selected, rather than accidentally discovered:

```text
PM 0x2b1c: AX0 = 0x209c
PM 0x2b1d: DM(0x210f) = AX0
PM 0x2b1e: AX0 = 0x209c
PM 0x2b1f: DM(0x3fca) = AX0
```

The page's `PM 0x3d84` then copies `DM(0x3f05)` into that ring. A gated live
watch caught the caller repeatedly writing `0xffff` at `PM 0x3d84`; this is
TIKRNL's mark-fill mailbox value, not a missing V90A source pointer. The ring
at `DM(0x209c)` remains sentinel-filled (`ffff ffff ffff ffff 0001 0001 ...`)
in the normal loopback.

The existing opt-in `EICON_V90A_TX_PRBS=1` probe suppresses the mark-fill
store and publishes a deterministic host source. It does not change the
faithful loopback result: the caller still holds at `0x00c0` and the answerer
at `0x00c2`. Therefore the mailbox ownership boundary is confirmed, but a
random host source is not the missing Phase-3 protocol source and must not be
made the default.

As a media cross-check, a V90D output captured while driven by the known-good
caller TX has the same structured 3070/1800/2200 Hz progression as the native
`run65.ulaw` capture. Replaying that file into V90A did not reproduce the
earlier `0x00d0` run, so that result depends on reactive timing/state and is
not evidence for a fixed DAA gain, source-pointer, or codec-rate correction.
## Session 273 — SPORT-result TX is not the missing handoff

The remaining concrete boundary alternative was tested on the faithful,
unpinned loopback: have the analog kernel-dispatch caller publish the SPORT
frame return value as its physical TX sample (`EICON_ANALOG_USE_SPORT_TX=1`)
instead of the normal post-frame `DM(0x3fb4)` publication path. This is a
path-level A/B, not a state or mailbox pin.

It is negative. The caller does not reach the V.90A Phase-3 ladder and falls
back around INFO state `0x0030`; the answerer consequently never reaches its
V.90D Phase-3 states. The normal DM publication path therefore remains the
correct kernel-dispatch boundary. Combined with the native-waveform prime,
which reaches the same caller inner `0x61` gate before the injected exchange
expires, the unresolved defect is the reactive Phase-3 waveform/control
exchange rather than a simple SPORT result-versus-DM sample selection.
## Session 274 — V90D c2 wall is upstream waveform validity, not its CP consumer

The direct answerer was write-watched at `DM(0x2117)`, the threshold used by
the c2 record's `PM 0x3015` rate predicate. In the ordinary unpinned loopback,
`PM 0x258f` repeatedly stores `0x0000` there while the answerer remains at
`0x00c2`. The same answerer, with the known-good native caller waveform placed
on the caller TX path, fills the word with a nonzero sequence (`0x0001,
0x0002, …`) and walks `0x00c2 -> 0x00c4 -> 0x00c6 -> 0x00c8 -> 0x00cc ->
0x00d0`.

This also explains why the live CP watch was misleading: the V.90D producer
does generate the exact CP acceptance mask (`0x206d=0x000f`,
`0x206e=0xfff8`), but c2 has not met the independent rate predicate. Raising
`DM(0x2117)` through the existing answerer database override did not survive
the page's own writes and did not change the walk. The CP consumer and the
V.90D page entry are therefore not the immediate defect.

The remaining emulation boundary is the V.90A caller's live Phase-3 symbol/
codec waveform: it reaches the terminal exchange, but its waveform does not
drive the V.90D delay-line/rate estimator the way the native caller waveform
does. A wider caller TX resampler passband also regresses before Phase 3, so
the correction should be made in the V.90A symbol/codec path rather than by
loosening the V.90D threshold.

### Session 276 — input-clock and answerer-delay A/Bs are negative

The analog kernel now exposes the previously untested input-resampler phase as
`EICON_ANALOG_RESAMPLER_IN_PHASE`. The direct loopback sweep found phase 0 at
the established `0x00c0/0x00c2` terminal pair; phases 3 and 5 regress the
caller to `0x0095`, and phase 1 is unstable. The input 8000-to-9600 phase is
therefore sensitive but not a missing fixed correction. Existing output-phase,
filter-length, cutoff, gain, and PCMU encoder A/Bs remain negative as well.

Changing the answerer's media setup gap from 2,000 ms to 0, 1,000, or 3,000 ms
also does not produce data mode: 0 ms stalls at the earlier `0x00b2/0x00b1`
pair, 1,000 ms fails before Phase 3, and 3,000 ms fails to establish the
caller. The failure is not explained by a simple endpoint clock offset.

Finally, feeding the answerer the known-good upstream recording while leaving
the caller live makes the corrected V.90D answerer reach `0x00d0`, but the live
caller remains at `0x00c0`. During the caller's corresponding window the
answerer's generated downstream has the right level but collapses to a
low-coherence stream (approximately 0.18 zero-crossing rate versus about 0.50
for the native downstream). This is the reciprocal training dependency: the
answerer only produces the native structured response after it has received a
valid upstream; a level, delay, or codec-phase tweak cannot bootstrap both
directions.

### Session 275 — live symbol-buffer capture closes the missing-producer lead

A per-codec-frame DM capture of the unpinned analog109 caller shows the actual
V.90A producer at the Phase-3 boundary, rather than inferring it from the wire:

```text
state window       DM(0x2119)   DM(0x211a)   DM(0x0a92..0a94)
0x0092 / 0x0094    0x32ca       0x2996/29fe changing, nonzero
0x0095             0x32c4       0x2996       changing, nonzero (stale buffer)
0x00b0 onward      0x32c4       0x2996       changing, nonzero (not selected)
```

The selector and variant are therefore being changed by the page's own record
ladder at the same boundary seen in the firmware trace; the symbol buffer is
not stuck at zero and the caller is not silently bypassing the QAM shaper. This
also explains why forcing the reader is not a fix: it overrides a legitimate
terminal control transition. The open mismatch remains the coupled V.90A/V.90D
response timing/content that drives the answerer's `DM(0x2117)` rate estimator,
not a missing analogue producer or PCMU/DAA serialization error.

### Session 277 — V90D TXD mailbox ownership is not the c2 bootstrap

The direct V90D mailbox was traced while the answerer was in its c2 wall. The
live and native-control paths both publish right-justified input `0x00ff` and
the same bulk-delay lengths (`0x0031/0x0081`); the live detector still settles
at `0x0006` with NCO `0x511f`, while the native valid-upstream path reaches
`0x001f` and `0x7313`. This separates the estimator's waveform/control input
from SPORT packing and delay-word representation.

The native DM capture does show TXD0 changing when its request bits become
active, whereas the direct firmware repeatedly restores `DM(0x3f05)=0xffff`.
An opt-in continuous changing-PRBS mailbox probe was used to hold non-mark
V90D words through that ownership boundary. It did not move the faithful
loopback from caller `0x00c0` / answerer `0x00c2`, so a simple TXD0 mark-fill or
mailbox timing correction is not sufficient. The remaining defect is still in
the coupled V90A/V90D training waveform/control exchange, with no evidence yet
for a DAA or codec serialization correction.

### Session 279 — native TXD contents reach the direct page but do not move c2

The native DM capture contains changing V90D `TXD0..TXD2` words throughout
page 14, unlike the direct firmware's recurring mark fill. An opt-in
`EICON_V90D_TX_DM_REPLAY` now replays those native datagrams from an
`EADSPDM2` capture. The replay also claims the five known TIKRNL TXD stores so
the direct page actually sees the supplied words; the captured direct c2
records confirm changing TXD values rather than `0xffff`.

Despite that stronger mailbox test, the normal live loopback remains caller
`0x00c0` / answerer `0x00c2`. Therefore the native TXD payload is not the
missing c2 transition input; the opt-in replay and mailbox claim remain
diagnostic-only.

### Session 280 — V90A TXD0 request pacing and Ja-shaped source are not sufficient

The page-13 consumer at PM `0x3d7e` explicitly polls `DI_control` bit 15 before
copying `TXD0` into the analogue source ring. The earlier opt-in V90A PRBS
probe advanced its word every frame, so it was not a faithful host handoff. The
probe now holds one word until the request clears. A Ja-shaped diagnostic word
source (17 one bits, a zeroed V.90 DIL descriptor, then repetition) was also
tested through that request-paced path. Both the corrected PRBS and the
protocol-shaped source leave the normal loopback at caller `0x00c0` /
answerer `0x00c2`.

### Session 299 — selected-block mailbox routing is not the V.90 control path

The native replacement A/B was extended so post-assignment MIPS writes to
`0xbf804800` were delivered to the replaced ANA media core and its `0x029e`
foreground, rather than to the separate DSPDAA core. This also made no
difference: the caller remained at `0x0030` while the answerer reached only
`0x0034`.

The write trace is informative. The selected-block traffic consists of the
boot token `0x3fff`, DSPDAA command words `0x0229/0x3fe5/1/0x00f5`, and
`0x6e4f` supervision toggles. It contains no V.90 APCM/DPCM control payload.
Routing those writes cannot supply the missing Phase-3 exchange; the remaining
native bridge gap is upstream in modem CAI/IDI assignment or another media
control channel that has not yet been connected.

### Session 298 — native ANA task runs on the existing SPORT1 core, but control state is missing

The selected-core crash was separated from the image itself by an opt-in
experiment that reuses the already-running Analog SPORT1 ADSP instead of
creating a second native core. At the native `dsp_assign` boundary it resets
that core, loads the complete `0x000d -> 0x0063 -> 0x0258` lifecycle, registers
TIKRNL through the Analog command ring and SPORT foreground, then lays the ANA
base/DIAL overlays.

This path is stable, but the mixed loopback stalls much earlier at caller
`0x0030` / answerer `0x0034` (12-second A/B), compared with the recovered-media
path's late Phase-3 boundary. The native ANA image is therefore executable;
the missing behavior is the native MIPS assignment/control state and selected
mailbox handoff, not merely a missing portable image. The experiment remains
opt-in as `EICON_NATIVE_REPLACE_MEDIA=1`; the default recovered-media path is
unchanged.

### Session 306 — live-state native replay reaches V.90D c6 but not V.90A data

The native 2185 upstream capture was selected from the answerer's published
live V.90D state, rather than from the caller's lagging state. This is the
first replay that makes the direct answerer advance beyond the normal c2 wall:
it reaches `0x00c6` and asserts `speed_tx|CTS|DSR`. The caller nevertheless
remains at `0x00c0`, even when the answerer's downstream is simultaneously
replayed from the native capture and its PCMU bytes are preserved raw.

Two controls did not change that asymmetry: caller input-resampler phase 1
(the default is phase 0), and removing the harness's 160 ms transmit buffer.
The result identifies a direction-specific V.90A response/control problem;
fixed codec phase and RTP latency are not sufficient explanations.

### Session 307 — aligned synthetic phase reversal does not clear the terminal pair

The V.90A caller's `0x00c0` entry is repeatable at about 20.68 s in the
state-feedback loop. A controlled receive-side probe was therefore started at
20.50 s, before that entry, using a 1333.333 Hz sine at amplitude 16,625, with
phase reversal every 24 ms and 0.75 ms stimulus windows:

```text
EICON_RX_SWEEP=1333.333:1333.333:20.5:28:16625:0.75:0.3:24
```

The caller still followed the normal path only through `0x00b3 -> 0x00b6 ->
0x00c0` at 20.68 s and remained there for the rest of the 29-second run. The
answerer remained at `0x00c2`. This is a useful negative control: phase
reversal and detector-level amplitude are not sufficient to make the V.90A
side advance when the V.90D end is not reacting to the caller's transmitted
symbols. The remaining defect is therefore in the closed-loop V.90D response
(or the caller's phase-3 transmitted control content), not just in the
`0x00c0` detector's tone input.

Capture: `artifacts/loopback-v90a-phase24-c0/`.

### Session 308 — native DSPDAA TX A/B is not a valid Phase-3 fix

The separate native Analog DSPDAA/2185 codec core was tested through its
opt-in `EICON_NATIVE_USE_DSPDAA_TX=1` route. In the native-MIPS caller
topology, enabling it changed the early caller endpoint from `0x0041` (control)
to `0x0051`, while both the A/B and its control stopped before V.90 Phase 3.
It therefore does not reproduce the established kernel-dispatch
`0x00c0/0x00c2` wall and is not evidence for changing the default DAA or codec
boundary.

The established kernel-dispatch caller's wire TX during its `0x00c0` dwell is
also materially unlike the native 2185 reference: the live stream is roughly
960 RMS with a changing broadband peak near 1--3 kHz, while the native capture
has the structured Phase-3 progression and the expected 1333 Hz segment. This
keeps the investigation focused on the V.90A symbol/source generation and its
reactive control exchange, not RTP PCMU serialization alone.

Captures: `artifacts/loopback-v90a-native-dspdaa-tx/` and
`artifacts/loopback-v90a-native-mips-control/`.

### Session 309 — V.90A reaches its reader with an unserviced TXD0 request

A corrected frame-boundary sampler captured the caller's live V.90A words
through the terminal dwell. At `0x00b3`, the page intentionally selects the
silence writer (`DM(0x2119)=0x32c4`). On entry to `0x00c0`, it changes to the
symbol reader (`DM(0x2119)=0x32ca`) and `DM(0x3764)` becomes a changing line
sample. However, the same `0x00c0` rows consistently show:

```text
DM(0x3f05) = 0xffff     TXD0 mark fill
DM(0x3fad) = 0x8000     host TXD0 request asserted
DM(0x3fca) = 0x209c     V.90A source-ring cursor
DM(0x3fb4) = 0x3764     published sample pointer
```

The native 2185 control capture has changing TXD0 words when the request is
active. The recovered kernel-dispatch caller therefore reaches the correct
V.90A reader but is not receiving a host-provided TXD0 symbol word; it is
transmitting mark fill into the phase-3 exchange. This is the strongest
implementation lead so far: the missing behavior is the protocol-aware MIPS /
TXD0 service, not another PCMU codepoint or DAA gain adjustment.

The existing PRBS, Ja-shaped, and arbitrary pattern probes remain diagnostic
only and are not evidence for a default source. The next implementation step
is to connect the native/control-side TXD0 producer to the kernel-dispatch
media page with the same request/ack pacing, then compare the resulting
`DM(0x3f05)` sequence and V.90D `DM(0x2117)` estimator against the native
trace.

Capture: `artifacts/loopback-v90a-tx-boundary3/`.

### Session 310 — direct V.90D c2 receives the same input word but never builds rate

The TXD0 observation is not sufficient as a fix: the existing request-paced
PRBS and Ja-shaped sources already prove that arbitrary changing mailbox data
does not clear c2. A sharper comparison uses the answerer's own V.90D trace.

In the current clean loopback, while the direct answerer dwells at `0x00c2`,
the trace reports `input=0x00ff`, `result=0x0000/0x0000`, and inner state
`0x006a`. In the native 2185 c2 trace, the same `input=0x00ff` is followed by
nonzero results (for example `0x0000/0x000f`, then larger values), the inner
state is still progressing, and the outer machine eventually reaches `0x00c6`
and data mode.

This rules out the raw right-justified input codeword as the differentiator.
The remaining emulation boundary is downstream of the SPORT word: the direct
V.90D receive/filter/rate-estimator chain is not accumulating the caller's
phase-3 waveform as the native 2185 path does. The next correction should
compare the estimator's delay/filter state and update cadence, rather than
promoting a guessed TXD0 source or another codec representation.

Captures: `artifacts/loopback-v90a-tx-boundary3/` and
`artifacts/eicon-native-tower/run65.endpoint.log`.

### Session 311 — live caller TX is spectrally unlike the native upstream

The raw PCMU captures were compared after proper μ-law expansion at the
corresponding Phase-3 windows. The live kernel-dispatch caller produces about
960 RMS with broad peaks around 1.8--3.1 kHz and zero-crossing rate near 0.51.
The native 2185 upstream capture is about 1,095 RMS, has the structured
1333 Hz training component, and has zero-crossing rate near 0.58. A short FFT
shows the live dominant bins around 1.82/2.18/2.96 kHz, while the native window
is dominated by approximately 1.29 kHz and its related components.

This explains why equal diagnostic words such as V90D `input=0x00ff` do not
imply equal estimator input: that field is a control/sample representation,
not the complete waveform entering the receiver filter. The direct V90D
estimator is healthy with the native upstream replay; the missing behavior is
the V.90A caller's protocol-coupled APCM source/modulator waveform. Arbitrary
TXD0 changes remain insufficient, so the next implementation target is the
native/control source that drives the analogue page's symbol ring, not another
gain or PCMU conversion.

Capture: `artifacts/loopback-v90a-tx-boundary3/`.

### Session 305 — raw downstream PCMU replay still leaves the caller at c0

The state-feedback replay was repeated with the native downstream PCMU bytes
preserved directly on the RTP wire, bypassing both the recovered DSP encoder
and the host reference encoder. The answerer again advanced to `0x00c6` and
asserted speed/CTS flags, while the caller remained at `0x00c0`. Therefore the
one-code-step downstream codec representation difference is not sufficient to
explain the caller's terminal state; the remaining mismatch is in the V.90A
receiver's response/control state, not RTP serialization.

### Session 303 — ordered selected-block writes do not repair native ANA attach

The selected block's indexed-write event log was replayed in original order,
while the native `0x0258` task was resident and before the `0x026d/0x025c/0x0262`
overlays were installed. The captured lifecycle contains only six selected
block writes, so this removes the possibility that the snapshot experiment
had lost a substantial command sequence. The 16-second loopback still stalled
at caller `0x0071` / answerer `0x007a`. Ordered replay is therefore also
diagnostic-only; the native replacement path is not the productive route to
the V.90A Phase-3 fix.

This excludes an arbitrary or simply mistimed analogue TXD0 source, while
leaving the opt-in source/pattern controls disabled by default. The native
caller-side Phase-3 waveform/control exchange remains the completion target.

### Session 281 — standards-shaped Ja mailbox source is also insufficient

The analogue TXD0 probe now has an opt-in `EICON_V90A_TX_JA=1` mode. It feeds
the page a request-paced 16-bit stream consisting of the V.90/V.92 Ja preamble
(24 one bits) followed by the N=0, 276-bit DIL-descriptor placeholder, with
the bit cursor preserved across 16-bit mailbox requests. This tests the
mailbox boundary with the protocol's framing shape rather than PRBS or a
fixed-word pattern.

The clean loopback result is unchanged: caller `0x00c0`, answerer `0x00c2`
(`artifacts/loopback-v90a-ja276`). The probe therefore does not identify a
missing arbitrary/Ja-shaped TXD0 source as the c2 bootstrap. It remains
diagnostic-only; the unresolved boundary is the stateful APCM/DPCM response
exchange and its rate estimator input.

### Session 282 — caller equalizer normalization does not clear the c2 wall

The caller-side LMS has a bounded response of about 0.25 at the relevant
training frequency, versus about 0.91 for a converged V90D receiver. An
opt-in `EICON_V90A_EQ_COEFF_SCALE=3.6` diagnostic therefore scales the
V90A 216-tap double-precision coefficient pairs once, on entry to caller
state `0x00c0`, after the page has stopped adapting.

The loopback is unchanged: caller `0x00c0`, answerer `0x00c2`.
The scale was applied at sample 165280/165440 in
`artifacts/loopback-v90a-eqscale36/caller.endpoint.log`, so the negative is
not a gate that failed to arm. Equalizer response amplitude is not the direct
c2 bootstrap; the remaining mismatch is the cross-direction training content
and timing that feeds the V90D rate estimator.

### Session 278 — native bulk descriptor A/B does not move the c2 wall

The raw native `run65.adsp-dm.bin` snapshots were re-read instead of relying
on the trace label: during native c2 they contain `DM(0x3fbc/0x3fbd) =
0x0e69/0x0ae0`, while the direct adapter publishes `0x0031/0x0081`. The
native-looking pair is reversed under the documented near/far contract, so it
was tested only as an opt-in override. The loopback remained caller `0x00c0` /
answerer `0x00c2`; the default descriptor is unchanged. This is another
representation difference that does not explain the terminal exchange, and
the c2 detector mismatch remains a stateful waveform/control issue.

### Session 283 — state-feedback reference TX reaches the terminal pair, not data mode

The answerer now has a diagnostic-only `EICON_TX_FILE_STATE` source. It selects
one reference downstream segment from the answerer's live `DM(0x3fc2)` state and
loops inside that segment until the state changes. This removes the wall-clock
alignment assumption from `EICON_TX_FILE` while preserving the real firmware
state transition as the feedback signal.

Using the native `run65.ulaw` segment map over the real answerer TX path moved
the loopback through the terminal pair, but not through the final handshake:

```text
caller:   ... -> 0x00b6 -> 0x00c0
answerer: ... -> 0x00c0 -> 0x00c2
```

Extending the short native `0x00c2/0x00c4/0x00c6` windows while holding each
state produced the same result. State-aligned segment timing is useful but
insufficient: the missing piece is the state-dependent APCM/DPCM control
content that makes the V90A response advance the V90D rate estimator. The
feature remains diagnostic and disabled by default; it is not evidence of data
mode.
### Session 284 — native-observed V90D delay lengths do not clear the c2 wall

A fresh frame-boundary trace compared the clean direct loopback with the
native 2185 control capture.  The native successful c2 window showed live
delay lengths `DM(0x3fbc/0x3fbd) = 0x0415/0x0465`, whereas the direct adapter
held its bootstrap pair `0x0031/0x0081`.  The direct answerer was therefore
run with opt-in `EICON_V90D_BULK_NEAR=0x0415` and
`EICON_V90D_BULK_FAR=0x0465`.  The result was unchanged: caller
`0x00c0`, answerer `0x00c2`.

The trace also confirms that both paths expose the same right-justified V90D
input representation (`input=0x00ff`); changing only the delay dimensions does
not reproduce the native estimator sequence (`result`, rate word, and state
advance).  This rules out the bootstrap near/far lengths as the remaining
emulation or DAA/codec fix.  The unresolved boundary remains the reactive
Phase-3 APCM/DPCM control exchange that causes the native path to generate a
nonzero `DM(0x2117)` and leave `0x00c2`.

### Session 285 — state-reactive receive muting is not the missing peer response

The existing `EICON_RX_REACT` hook was also re-run with a 500 ms stall period
and `EICON_RX_REACT_CLEAR=1`, so it muted the caller's receive sample and
cleared the detector latch after each state stall.  This is a stronger test
than a fixed wall-clock mute, but it still stopped at caller `0x0092` and
answerer `0x00b0` (the normal unpinned run at least reaches the later
`0x00c0/0x00c2` terminal pair).  The hook changes the local audio presented to
the caller; it does not cause the V90D firmware to generate the stateful
APCM/DPCM response that advances its own rate estimator.  It therefore remains
diagnostic-only and is not promoted as a loopback fix.

### Session 286 — direct-card-frame V90A is not a valid comparator

The same unpinned loopback was run with the caller's Analog kernel-dispatch
backend disabled, leaving the generic direct `Card.frame()` path on both
endpoints.  The caller stopped during initialization at
`TrnProgress 0xffff -> 0x0000` (sample 160), while the answerer stopped at
`0x0004`; it never reached the V.90A page.  This separates the host-service
problem from the Phase-3 problem: kernel dispatch is required just to reach the
qualified caller boundary, and the direct frame path cannot be used as a
2185/V90A control oracle.  The valid baseline remains the kernel-dispatch
caller at `0x00c0` against the direct V90D answerer at `0x00c2`.

### Session 287 — Analog doorbell dispatch is a real defect, but not the c0/c2 blocker

The Analog kernel service was compared with the PRI service and corrected to
execute every asserted DSP-to-host doorbell slot through the registered entry
table, rather than only counting and clearing the bits. A standalone
Analog-kernel run showed the previously dropped bit 10 dispatching to
`PM(0x08f5)`; overlay bit 1 continues to dispatch its completion entry after
the requested page is resident.

The clean unpinned V90A/V90D loopback was then rerun with this correction in
`artifacts/loopback-v90a-doorbell-dispatch`. It remained caller `0x00c0` /
answerer `0x00c2`, identical to the baseline. The loopback does not raise the
bit-10 request before reaching that wall, so the missing doorbell service is a
valid Analog emulation correction but not the remaining Phase-3 control-
exchange cause. The fix is retained for correctness; further work stays
focused on the live APCM/DPCM exchange after the c0/c2 boundary.

### Session 288 — Actual SPORT1 TX callback is not the V90A loopback source

The earlier SPORT-vs-DM A/B selected `last_frame_result`, which is the
emulator's frame-status return rather than the SPORT callback word. The
instrumentation was corrected to retain all callback ports and the opt-in
`EICON_ANALOG_USE_SPORT_TX=1` path now consumes the actual SPORT1 TX callback.
The callback is nonzero and does diverge from the `DM(0x3fb4)` publication,
so this was a real measurement error, not a no-write artifact.

A clean 28-second loopback with that corrected callback source regressed to
caller `0x0030` and answerer `0x0028`, versus the default DM source's
qualified caller `0x00c0` / answerer `0x00c2`. The callback path is therefore
not the missing codec/DAA correction and remains diagnostic-only. The default
source is unchanged; the remaining investigation stays on the reactive
Phase-3 APCM/DPCM control exchange after `0x00c0/0x00c2`.

### Session 289 — SPORT1 RX-edge substitution cannot be the late failure

As an emulator-timing A/B, `adsp2181_sport1_frame()` was temporarily changed
to assert the SPORT1 RX edge instead of the configured TX alias. That variant
failed before V.90: the Analog kernel never registered TIKRNL.ANA, and the
focused Analog dispatch tests errored at boot. Restoring the TX alias makes
the same tests pass. The SPORT event model therefore remains the established
one for this firmware; the unresolved defect is later than kernel startup.

### Session 290 — post-continuation V.90A TXD0 servicing is not the c2 bootstrap

The Analog kernel-dispatch path now mirrors the direct/native host cadence by
servicing a V.90A TXD0 request once before the SPORT frame and again after the
completed continuation. The second service is inert for the normal
firmware-owned mailbox and only stages opt-in host sources for the following
frame.

The request-paced Ja diagnostic was rerun with this ordering in
`artifacts/loopback-v90a-ja-postservice`. It remained caller `0x00c0` /
answerer `0x00c2`, so the remaining c2 wall is not caused by this mailbox
service edge. The ordering correction is retained for host-boundary fidelity;
the next target remains the live V.90A Phase-3 producer.

### Session 291 — holding every native V90D segment still does not close c2

The state-reactive TX replay was extended across the complete native run65
walk, including the long `0x00c2` response and every later c2-to-data segment.
The answerer selected each segment from its live `DM(0x3fc2)` state and looped
within the selected interval until the firmware advanced. The result was still
caller `0x00c0` / answerer `0x00c2` in
`artifacts/loopback-v90a-state-coupled-full`.

This rules out a missing c2 envelope, gap length, or wall-clock alignment as
the sole fix. The unresolved difference is the protocol-specific APCM/DPCM
control content that the direct V90D producer computes from the live caller
response; reference downstream samples can reach the terminal pair but cannot
make the direct estimator publish its native rate sequence.

### Session 292 — live fed-RX is nonzero; the late wall is not a silent DAA

The ordinary unpinned loopback was repeated with `EICON_DUMP_FED_RX=1`, which
records both the PCMU codewords and the signed words actually handed to
`frame_fast`. In `artifacts/loopback-v90a-fed-rx`, the answerer's fed-RX stream
is nonzero through the c2 interval (the signed fed words reach roughly
`+-2.5k`), and its timing tracks the caller's outbound waveform. The answerer
still stops at `0x00c2`, while the caller's outbound stream becomes silent in
the corresponding c0 exchange.

This separates the remaining issue from a completely detached codec/DAA
boundary: the emulated V90D is receiving live samples. The diagnostic
`page_rx_sample` field remains `0x0000` in the direct-card CSV, so that field
is likely an unmapped diagnostic mailbox rather than proof of silence. The
useful next comparison is the V90A producer/selector and its c0 response
content against the native 2185 upstream stream, not another PCMU gain or
SPORT-edge A/B.

### Session 293 — the V90A b3 silence is real, but the reader output is not the protocol source

The existing selector probe was narrowed to caller state `0x00b3`. With the
normal selector, the caller's outbound stream measured only about 53 RMS over
the b3 dwell, with 99.6% exact-zero samples. Forcing the symbol-buffer reader
only in b3 raised it to about 831 RMS with a 0.457 zero-crossing rate, close in
level to the native upstream stream (about 1104 RMS and 0.425 zero crossings).
The caller still reached only `0x00c0` and the answerer remained at `0x00c2`.

The forced waveform is nevertheless strongly periodic (large correlations at
lags 20, 40, and 160), unlike the native V90A upstream. Thus the silence
selector is a real emulation discrepancy, but simply exposing the current
symbol buffer does not reconstruct the protocol's b3 source. The probe remains
opt-in; changing the firmware-owned selector globally would mask the deeper
source/record mismatch.

### Session 294 — native downstream plus b3 reader does not bootstrap the pair

Combining the b3 reader probe with state-reactive replay of every native V90D
downstream segment made the caller stop at `0x00b3` and did not move the
answerer through its response ladder. Substituting the native V90A upstream
recording at the caller's RTP boundary likewise failed, stopping the live
answerer at `0x00b2`. Those experiments are timing-misaligned diagnostics, not
fixes, but they confirm that level restoration and raw reference media do not
replace the protocol-coupled V90A source sequence.

### Session 295 — c2 rate pin is not a viable shortcut

Pinning the direct answerer's `DM(0x2117)` to `0x1000` while it was in
`0x00c2` did not advance the call. The answerer entered c2 with a
`ratechange|flow_blocked` status and immediately retrained; the caller later
reset through a new low-level training walk. The rate predicate is therefore
downstream of a valid coupled response, not an independent missing threshold
that can be safely forced.

### Session 296 — caller-state-selected native upstream still does not close the loop

The existing state-reactive source hook was applied to the caller instead of
the answerer, using `run65.rx.ulaw` and the caller's live APCM state word
`DM(0x20f9)` to select each native upstream segment. This left the V90A page
and its state machine active but produced caller `0x00b3` / answerer `0x00b0`,
not data mode. A reference segment selected by state is still not a substitute
for the V90A producer's protocol-coupled source sequence.

### Session 297 — native ANA download is staged, but selected-core execution remains open

The native Analog MIPS bridge was instrumented at the actual `dsp_assign`
boundary. Before the asynchronous task request, the selected physical block is
`0xbf804800` and its captured shadow contains only the resident bootstrap:
`426` nonzero PM words and `102` nonzero DM words. The acknowledged `0x0258`
request is now followed by a host-side portable-image transfer of the card-77
ANA variant, growing the selected shadow to `1,285` PM and `304` DM words. This
fixes a real loader omission; it does not alter the default recovered-media
owner.

An opt-in attempt to execute that shadow as a second selected ANA core was
then made using the native PRI tower's lifecycle (resident kernel, IDLE.ANA,
TIKRNL task, then overlays). The ADSP emulator terminated with signal `-10`
while the live MIPS bridge was active, so that path was removed rather than
left as a crashable switch. The evidence therefore separates two operations
that had previously been conflated: staging `0x0258` is now correct, but the
selected core is still not executing/routed in the Analog MIPS bridge.

The mixed native-PRI-answerer / Analog-caller loopback also remains an invalid
oracle: the native answerer never reaches V.90D page 14 in this configuration.
The authoritative clean baseline is still the direct PRI117 V.90D answerer
against the Analog kernel-dispatch V.90A caller, ending at caller `0x00c0` /
answerer `0x00c2`.

### Session 300 — native V.90A CAI is aligned, but the loopback still stalls

The native Analog MIPS caller previously sent the legacy modem CAI on both
signalling `ASSIGN` and `CALL_REQ`, even when the harness selected
`EICON_MODULATION=v90a`. The native path now derives one environment-driven
`ModemOptions` value and carries it on both requests; `v90a` resolves to the
V.90 descriptor (`enabled=0x04`). This removes a real native-control mismatch
and keeps the two requests internally consistent.

A 32-second native-MIPS-caller / direct-PRI117-answerer loopback with that
change still ended before data mode, at caller `0x00b3` and answerer `0x00b2`.
The CAI payload is therefore not the missing V.90A-to-V.90D transition. The
remaining leading hypothesis is the reactive Phase-3 APCM/DPCM control/media
exchange at the selected Analog media core, rather than call-option selection.

### Session 301 — host G.711 serialization does not change the terminal pair

The analogue caller's RTP return path was switched from the recovered DSP
G.711 encoder to the host reference PCMU encoder. This isolates the final
wire serialization from the signed-linear SPORT/DAA boundary. The 32-second
loopback still ended at caller `0x00c0` / answerer `0x00c2`, so the PCMU
encoder choice is not the missing Phase-3 transition.

### Session 302 — replaying the native selected-block register snapshot is harmful

The opt-in native ANA replacement path now has a diagnostic mode that replays
the six loader-time registers captured for selected block `0xbf804800` after
the portable ANA image and overlays are installed. This tests whether the
native core was merely missing SPORT/DAA initialization after its PM/DM image
was loaded. With the snapshot replayed, a 16-second native-MIPS-caller /
direct-PRI117-answerer loopback stalled much earlier, at caller `0x0071` /
answerer `0x007a`, versus the replacement path's prior `0x0030` / `0x0034`
boundary. The register snapshot is therefore not a safe native-media attach
fix; the mode remains opt-in and the default recovered-media owner is
unchanged.

### Session 304 — disabling the V.90D bulk adapter does not clear c2

The direct PRI117 answerer was rerun with `EICON_V90D_BULK_ADAPTER=0`, leaving
the firmware's own bulk worker and delay words untouched. The 32-second
loopback still ended at caller `0x00c0` / answerer `0x00c2`. The adapter's
derived delay ABI is therefore not the c2 rate-estimator gate; the default
adapter remains enabled and the investigation returns to the live V.90A
Phase-3 response waveform/control exchange.

### Session 312 — state-gated V90A reader selection does not close the loop

The live source watch showed that the V90A symbol generator (`PM39A0`) is active
while the selector at caller state `0x0095` chooses the silence producer
(`PM32C4`) rather than the symbol reader (`PM32CA`). As a focused diagnostic, the
caller was rerun with the reader forced only while `DM(0x20f9)` matched
`0x0095`, `0x00b0`, or `0x00b3`. The shaper logged a reader selection at the
`0x0095` transition, but the unpinned loop still ended at caller `0x0095` /
answerer `0x00b1`, with no movement toward `0x00d0`.

This rules out a simple selector-bit correction. The silence/reader choice is
coupled to the phase-3 protocol state and/or to the waveform produced at the
reader boundary; forcing the reader in these states is not retained as a
default fix.

### Session 313 — clean c0 source capture puts the selector after the fault

The clean source watch was extended through the caller's terminal transition.
Before `0x00c0`, `PM39A0` repeatedly generated changing pulse-shaper symbols,
but `PM32C4` was the selected output producer. At the caller transition
`0x00b6 -> 0x00c0` (sample `165440`), the resident page switched to `PM32CA`;
the reader then executed repeatedly while the pair remained caller `0x00c0` /
answerer `0x00c2`.

This makes the ordering explicit: the c0 reader transition is present in the
default emulation and is not the missing trigger. The unresolved mismatch is
earlier, in the protocol-coupled Phase-3 mapping/control or the waveform that
feeds it; changing the final silence/reader selector cannot repair that input.

### Session 314 — V90A symbol ring is active before the silence selector

The caller-side DM sample at `0x0095` shows `PM39A0` continually changing the
symbol-buffer words `DM(0x0a92/0x0a93)` while the resident selector remains
`DM(0x2119)=0x32c4`. The selected silence producer consequently leaves
`DM(0x3764)=0`. The source ring is therefore populated; it is not an empty or
unserviced producer caused by the DAA callback.

This reinforces the causal ordering from Session 313: the V90A page is making a
state-dependent silence decision while its symbol generator is alive. The
changing symbols are not yet the native protocol sequence—their periodicity is
consistent with the failed peer response—but routing them directly does not
constitute a correction and remains diagnostic-only.

### Session 315 — the native/current `DM(0x3fa7)` difference is not the wall

The native selector trace was compared with the current caller trace because
`DM(0x3fa7)` initially appeared to be missing from the V90A path. That
interpretation was incorrect. In both traces the six-word mapping block is
zero while `DM(0x2119)=0x32c4` selects the silence producer, and it contains
the same changing frame values after `DM(0x2119)=0x32ca` selects the reader.
The word is therefore the resident mapping-frame block already identified for
the V90D/page-14 path, not an unimplemented V90A source mailbox.

The useful remaining difference is state progression: the current caller
records `DM(0x20f9)=0x00c0` / `DM(0x20e9)=0x1330` during its terminal dwell,
whereas the native selector oracle proceeds through the surrounding V90A
records without a `0x00c0` sample. This keeps the defect upstream of the
already-correct c0 reader transition: the emulated V90A/DAA closed-loop
exchange is failing to produce the native response that would let the caller
leave the c0 gate. No `DM(0x3fa7)` default patch is warranted.

### Session 316 — opt-in native TXD0 bridge does not provide a source

The existing `EICON_NATIVE_BRIDGE_V90A_TX=1` path was run in the normal
analog109 kernel-dispatch caller against the PRI V90D answerer, with the
diagnostic TXD0 source disabled. The bridge produced no native-mailbox-change
events and the pair stalled earlier, at caller `0x0095` /
answerer `0x00b0`, rather than reaching the ordinary `0x00c0` /
`0x00c2` wall. It therefore neither establishes the missing mailbox ownership
nor supplies a usable Phase-3 waveform. The bridge remains opt-in and is not
promoted into the default path.

Capture: `artifacts/loopback-v90a-native-txd0-bridge/`.

### Session 317 — native `DM(0x3fc4)=0xa100` does not clear c2

The page-14 snapshots expose another native/current difference: the native
2185 V90D answerer carries `DM(0x3fc4)=0xa100`, while the direct answerer
carries `0xa10f`. A page-entry force was insufficient because firmware rewrote
the word, so the native value was then held with `EICON_PIN_DM` through the
V90D run. The loopback remained caller `0x00c0` / answerer `0x00c2`; the
answerer still entered the same inner `0x0060 -> 0x0062` path and did not
publish data mode. The classifier/capability low nibble is consequently a
representation difference, not the c2 gate.

The c2 traces sharpen the remaining boundary: current and native both accept
the same right-justified `input=0x00ff` and traverse the inner estimator
records, but only the native upstream waveform causes the estimator's rate
word to accumulate and the outer machine to leave c2. No classifier-word
default change is warranted.

Captures: `artifacts/loopback-v90a-answerer-a100/` and
`artifacts/loopback-v90a-answerer-pin-a100/`.

### Session 318 — native-MIPS answerer comparison does not reach V.90D

The current analog109 kernel-dispatch caller was paired with the native-MIPS
PRI117 answerer, first normally and then with the native bearer-activation
option used by the standalone tower capture. Both runs were invalid as a
late-Phase-3 comparator: the native answerer stopped at `TrnProgress=0x004f`
and never loaded or entered the V.90D page, while the caller stopped at
`0x0092`. This does not distinguish direct SPORT/codec behavior from the V90A
source path. The direct PRI117 answerer remains the valid peer for the c2
estimator measurements.

Captures: `artifacts/loopback-v90a-native-mips-answerer/` and
`artifacts/loopback-v90a-native-mips-answerer-activated/`.

### Session 319 — firmware-shaped Lagrange resampling regresses before Phase 3

The analogue firmware documentation identifies its 8 kHz -> 9.6 kHz boundary
as a six-coefficient Lagrange/Farrow interpolator, whereas the harness's
qualified default is a windowed-sinc converter. An opt-in
`EICON_ANALOG_RESAMPLER_KIND=lagrange` implementation was added to test that
specific codec-boundary hypothesis without changing the default.

The clean loopback did not improve. It regressed to caller
`TrnProgress=0x0095` and answerer `0x00b0`, compared with the default caller
`0x00c0` / answerer `0x00c2` terminal pair. The analogue and IDI suites still
pass (`73 passed`), so this is a behavioral negative rather than an
implementation crash. The firmware-shaped interpolation is not the missing
V90A->V90D Phase-3 correction in this form; the windowed-sinc default remains
the less-regressive boundary model.

Capture: `artifacts/loopback-v90a-lagrange/`.

### Session 320 — native/current V90A mapping words are phase-shifted

The existing selector CSVs were compared at the first `0xb0 -> 0xb3` ladder,
where both callers select the same reader (`DM(0x2119)=0x32ca`) and expose the
same control words (`DM(0x20e9)=0x1340`, then `0x1340`, then `0x1340`). The
changing mapping word `DM(0x3fa7)` nevertheless has a different sequence:

```text
native : 0xfe10 -> 0x02fc -> 0x00d7
current: 0x00d7 -> 0x02e1 -> 0x02fc
```

This is not the previously ruled-out all-zero mailbox difference. It is a
dynamic frame/control sequence whose ordering and values differ at the exact
ladder that feeds the terminal exchange. The current caller subsequently
re-enters `0xb6 -> 0xb7 -> 0xc0`, while the native selector capture ends after
`0xb3`. The next diagnostic is to capture all six words of the mapping block
and their producer timing; no single-word `DM(0x3fa7)` patch is justified yet.

### Session 321 — current full mapping block is structured, not an empty mailbox

A fresh 24-second clean capture sampled `DM(0x3fa7..0x3fac)` on every caller
frame. At the first live ladder the block contains three populated words and
three zero words:

```text
0xb0: d7 ff37 fad4 0000 0000 0000
0xb1: 2e1 04e0 01ea 0000 0000 0000
0xb2: 2fc 0348 fd64 0000 0000 0000
0xb3: 000 0000 0000 0000 0000 0000
```

The later `0xb6 -> 0xb7 -> 0xc0` sequence repeats/reorders the same populated
frames (`b6` matches `b0`, `b7` matches `b2`, and `c0` matches `b6`) while the
caller remains at the terminal exchange. This confirms that the live block is
being generated and consumed as a three-word mapping frame; it is not a
detached DAA mailbox. The native CSV currently contains only `DM(0x3fa7)`, so
the six-word native comparison remains outstanding and no block patch is
warranted from this capture alone.

Capture: `artifacts/loopback-v90a-map-block/`.

### Session 322 — V90A source cursor does not drive the mapping block

The follow-up caller trace added the serializer/source registers around the
same `0xb0 -> 0xb3` window. They remained constant throughout:

```text
DM(0x20de) = 0x0000
DM(0x3f89) = 0x0000
DM(0x3f8a) = 0x0000
DM(0x3fca) = 0x209c
```

The analogue source cursor `DM(0x3fca)` never advanced while the three-word
`DM(0x3fa7..0x3fa9)` sequences appeared. The mapping block is therefore shared
page-14 state/residue in this caller trace, not the V90A source-ring producer.
This closes the apparent mapping-block lead and returns the investigation to
the actual V90A symbol/modulator waveform and its receive-side state gate.

Capture: `artifacts/loopback-v90a-map-cursor/`.

### Session 323 — V90A ring generator and selected handlers are executing

The bounded execution trace targeted the actual V90A producer path rather than
the mailbox residue. `PM(0x39a0)` executes repeatedly from the page's shaping
caller with `I1=0x0a92` and changing phase/output registers, matching the live
symbol-ring updates observed in `DM(0x0a92..0x0a94)`. The selected record
handlers at `PM(0x2996)` and `PM(0x29fe)` also execute repeatedly during the
same page interval.

This confirms that the analogue source generator and its selector-side handler
are alive in the kernel-dispatch path. The persistent `DM(0x3f05)=0xffff`
TXD0 value is a parallel request/mailbox artifact, not proof that the selected
V90A waveform source is empty or unserviced. The next comparison must inspect
the handler inputs/outputs and their native 2185 equivalents; no TXD0 producer
or mailbox-ownership patch is promoted from this trace.

Capture: `artifacts/loopback-v90a-source-exec/` and
`artifacts/loopback-v90a-source-reader-exec/`.

### Session 324 — TXD0 mailbox plumbing works; arbitrary source content does not

The existing opt-in `EICON_V90A_TX_PRBS=1` probe was rerun with the source-ring
capture enabled. In the same caller window where the normal path holds
`DM(0x209c..0x20a3)` at the sentinel pattern, PRBS causes those words to change
continuously and `DM(0x3f05)` carries the changing supplied 16-bit values while
`DM(0x3fad)` remains the asserted request bit. This proves the TXD0 request,
host publication, TIKRNL copy, and analogue source-ring path are wired.

The live result regresses to caller `0x0092` / answerer `0x0080`, rather than
approaching the default `0x00c0` / `0x00c2` wall. Therefore the missing behavior
is not mailbox ownership or a detached source ring: it is the protocol-aware
V.90A TXD0 content/producer that the native 2185 supplies. PRBS, fixed words,
and the earlier Ja-shaped source remain diagnostic negatives and must not be
promoted as the correction.

Capture: `artifacts/loopback-v90a-source-ring-prbs/`.

### Session 325 — normal V90A source ring is the sentinel path

A focused caller capture sampled the actual source-ring words rather than the
downstream symbol buffer. During the `0x0092 -> 0x0094` interval the normal
ring is stable at:

```text
ffff ffff ffff ffff 0001 0001 0000 0000
```

The same sentinel pattern persists into the later V90A source states, while
`DM(0x0a92..0x0a94)` continues changing. Enabling the existing PRBS mailbox
probe replaces the first four ring words with changing host values, proving
that this ring is the protocol-data input to the analogue producer. The
default emulation therefore has a real, repeatable missing-input condition:
the V90A symbol arithmetic runs, but its protocol source ring contains no
native training/data words. The remaining implementation task is to recover
the native 2185 TXD0 producer content and pacing, not to modify the codec
boundary or the downstream V90D estimator.

Captures: `artifacts/loopback-v90a-source-ring/` and
`artifacts/loopback-v90a-source-ring-prbs/`.

### Session 326 — add an opt-in encoded Ja source probe

V.92 8.5.4 specifies Ja as 24 ones followed by a 276-bit N=0 DIL descriptor,
then scrambler and modulo-2 differential encoding. The earlier
`EICON_V90A_TX_JA=1` probe supplied the raw bits and therefore did not test the
specified wire sequence. Added `EICON_V90A_TX_JA_SCRAMBLED=1` as a diagnostic
source using the V.34 GPA recurrence `y[n] = x[n] xor y[n-18] xor y[n-23]`,
followed by differential encoding with a zero seed. The descriptor remains the
existing zero placeholder so this isolates coding and boundary behavior from
capability-mask/CRC recovery. It is deliberately opt-in pending a live result.

### Session 327 — encoded Ja probe is a negative

The clean mixed loopback with `EICON_V90A_TX_JA_SCRAMBLED=1` ran for 24 s but
stalled at caller `0x0095` / answerer `0x00b0`. The unmodified source path in
the comparable run reached caller `0x00c0` / answerer `0x00c2`, so the encoded
source is not a drop-in correction. This does not disprove the V.92 coding
rules; it shows that the TXD0 mailbox boundary likely expects a different
pre-modulation source representation, cadence, or differential seed, and that
the placeholder descriptor cannot be treated as a faithful native producer.

Capture: `artifacts/loopback-v90a-ja-scrambled/`.

### Session 328 — prepare a source-start timing A/B

The encoded-Ja probe changed the analogue waveform from the beginning of the
TXD0 request stream and regressed the handshake. Since the clean trace keeps
the V90A source ring at sentinel values through the early ladder and changes
its selected reader only at the later `0x00b6/0x00c0` region, add the opt-in
`EICON_V90A_TX_SOURCE_START` gate. It leaves the firmware mark-fill owner in
place until the requested `TrnProgress` value, allowing source content and
source start timing to be separated. Default remains disabled.

### Session 329 — delayed encoded-Ja A/B does not open the source window

With `EICON_V90A_TX_JA_SCRAMBLED=1` and
`EICON_V90A_TX_SOURCE_START=0x00b6`, the pair again ended at caller `0x0095`
/ answerer `0x00b0`, before the source gate could open. Delaying the encoded
source therefore produced no observed improvement; this run cannot separate
source content from the known pre-`0x00b6` training variability. The timing
gate remains diagnostic-only.

Capture: `artifacts/loopback-v90a-ja-start-b6/`.

### Session 330 — PM 0x39a0 reads the generated 0x0900 ring, not TXD0 0x209c

Disassembly of the live V90A overlay shows PM `0x39a0` loading its source
pointer from `DM(0x0de5)` and copying four words from the pointed circular
buffer into `DM(0x0a92..0a95)`. A focused live capture shows the pointer walking
`0x0900, 0x0903, ...` and the source words are nonzero. The 60-word buffer at
`DM(0x0900..0x093b)` is populated by PM `0x38c8`, whose writes are visible in
the same capture.

Therefore the previously watched `DM(0x209c..)` values are the separate TXD0
mailbox/source-ring path at PM `0x3d84`, not the buffer selected by the active
PM `0x39a0` symbol reader. The earlier conclusion that the selected V90A
generator was sentinel-filled is retracted. The remaining source investigation
must follow PM `0x38c8` inputs (`DM(0x0c3c/0x0c3d)` and its PM coefficient/data
state) and the reader/selector transition.

Capture: `artifacts/loopback-v90a-source-real-buffer/`.

### Session 331 — the PM 0x38c8 input ring is populated by V90A state setup

Tracing PM `0x38c8` and its `DM(0x0c3c/0x0c3d)` pointers shows both pointers
are initialized to `0x2120`, then the generator consumes the circular samples
from that region while producing the `0x0900` ring. The `0x2120` words are
written during the V90A state/control setup (including PM `0x32da` and the
nearby record initialization at PM `0x2acd..0x2ae8`) and are subsequently
read by the active generator. They are not left at zero and there is no
missing DAA callback at this boundary.

This further narrows the mismatch to the V90A record/state values or the
2185-compatible arithmetic/codec processing of those values, rather than the
previously suspected absent TXD0 source or detached analogue input.

Capture: `artifacts/loopback-v90a-codec-buffer/`.

### Session 332 — fresh clean loopback still fails before Phase 3

A fresh 40-second unprimed mixed loopback was run with the current defaults,
including the direct V90D six-word mapping-block hold and the 9600-Hz analogue
codec. It ended at caller `0x0095` / answerer `0x00b0`; neither endpoint
reached the V.90 Phase-3 pair. This confirms the serializer correction does not
by itself solve the requested V90A↔V90D loopback. The remaining target is the
V90A/V90D training transition before the data serializer, with the active V90A
generator chain now identified as `DM(0x2120)` -> PM `0x38c8` ->
`DM(0x0900..0x093b)` -> PM `0x39a0` -> `DM(0x0a92..)`, not the separate TXD0
mailbox ring.

Capture: `artifacts/loopback-v90a-clean-current/`.

### Session 333 — native-downstream replay was inconclusive for the generator

The corrected `EICON_RX_PRIME_SYNC` invocation (recording path, timing range,
and milestone map in one value) was run against the live answerer. It reached
caller `0x00b3` / answerer `0x00b2` but did not reach `0x00c0`. Because only the
caller receive path was replayed and the answerer remained live, this run does
not isolate the V90A generator or establish a source correction; it is retained
only as a harness/measurement result.

Capture: `artifacts/loopback-v90a-native-downstream-current/`.

### Session 334 — native/current V90A record ladder matches through 0x00b3

The archived native selector capture and the current caller DM capture were
compared at every `DM(0x20f9)` transition. Both follow the same ladder and
control values through the pre-terminal phase (`0x50, 0x52, 0x53, 0x54,
0x60, 0x62, 0x64, 0x70..0x76, 0x92, 0x94, 0x95, 0xb0, 0xb1, 0xb2, 0xb3`),
including matching `DM(0x20e9)`, selector `DM(0x2119)`, handler pointer
`DM(0x211a)`, and `DM(0x20f0)`. The current samples are time-shifted, and
then continue into `0xb6/0xc0` where the native selector capture ends, but
there is no evidence of a missing or corrupt V90A record-table load before the
wall.

The remaining difference is therefore the generated mapping/waveform response
that drives the live V90D peer, not the basic V90A state ladder. No record-table
patch is justified.

Artifacts compared: `artifacts/native-v90a-selector.csv` and
`artifacts/loopback-v90a-selector-csv/caller.dm.csv`.

### Session 335 — setup-gap timing does not change the clean failure

A fresh 40-second unprimed loopback was repeated with a 2500-ms answerer
setup gap, while retaining the direct V90D mapping-block hold and the 9600-Hz
analogue codec. It again reached the late pre-terminal transition on the
caller (`0x00b6 -> 0x00c0`) and stopped there; the answerer reached
`0x00c2`. Delaying answerer attachment therefore does not resolve the
V90A-to-V90D transition. This strengthens the waveform/peer-response
hypothesis and does not justify a startup-timing change.

Capture: `artifacts/loopback-v90a-clean-gap2500/`.

### Session 336 — native Analog/MIPS caller is not a valid late-stage comparator

The same unprimed loopback was run with the caller switched from the
kernel-dispatch Analog backend to the native Analog/MIPS path, retaining the
9600-Hz codec and direct V90D answerer. The native caller stalled at
`0x0041` / the answerer at `0x0042`, well before the kernel-dispatch caller's
repeatable `0x00c0` / `0x00c2` terminal pair. This A/B therefore does not
isolate the late codec or V90A gate: the native Analog/MIPS path has an
independent earlier handoff problem and cannot serve as the 2185 reference for
the current wall.

Capture: `artifacts/loopback-v90a-native-caller-current/`.

### Session 337 — c0 reaches inner 0x61, but the detector never validates the response

A frame-boundary DM capture of the clean kernel-dispatch pair shows that the
caller does enter the expected inner V90A record: at the outer transition
`0x00b6 -> 0x00c0`, `DM(0x2104)` changes to `0x0061`, while
`DM(0x20eb)` remains `0x4010` and `DM(0x10f3)` remains clear for the entire
c0 dwell. The c0 detector counter `DM(0x2551)` reaches only `0..2` (the
previous b3 detector had reached `0xe8`), so the missing bit is not a record
dispatch or stale-state problem; the live response fails the detector's
stable/reversal test.

The same capture puts the V90D side's c2 estimator beside the native
successful reference. In the live pair `upstream_quality` remains only
`0x14..0x32` and the inner estimator stays at `0x66`; in the native c2
reference it rises through `0x101..0x156` and the inner state advances to
`0x6a`. The current caller's c0 transmit is present (about 961 RMS, 0.512
zero-crossing rate), so this is not a silent TX/DAA boundary, but its spectral
content is unlike the native upstream reference and produces a much weaker
estimator response.

This narrows the remaining correction to the protocol waveform/source
content feeding the coupled c0/c2 exchange, rather than the state table,
SPORT callback selection, or missing c0 detector execution.

Capture: `artifacts/loopback-v90a-inner-state/`.

### Session 338 — the c0 waveform is already wrong at the 9.6-kHz SPORT TX boundary

The caller's raw codec-rate TX stream was captured before the 5:6 conversion
back to the 8-kHz RTP bearer. Around the c0 transition, the raw 9.6-kHz
waveform and the 8-kHz wire waveform have the same broad spectral peaks and
similar zero-crossing rate; the resampler is not creating the weak c2
response. The raw stream is already the same non-native, broadband control
waveform seen on the wire.

This closes the remaining simple DAA/codec placement hypothesis: the failure
is upstream of the RTP-facing output resampler, in the V90A DSP's live source
or its protocol-coupled response state. The raw capture also confirms that
the caller is producing nonzero c0 TX samples, so the answerer's low
`upstream_quality` is a waveform-content mismatch rather than a missing
SPORT1 TX publication.

Capture: `artifacts/loopback-v90a-raw-tx/`.

### Session 339 — selecting the SPORT1 callback TX value regresses before V.90

The Analog kernel backend was rerun with `EICON_ANALOG_USE_SPORT_TX=1`, which
exposes the value written by the emulator's SPORT1 TX callback instead of the
V.90 page's published `DM(0x3FB4)`/pointer boundary. This is a targeted test of
the remaining transmit-latch interpretation, not a waveform modification.
The caller then stopped at `0x0092` and the answerer at `0x002c`, compared with
the normal clean pair's repeatable `0x00c0` / `0x00c2`. The callback value is
therefore not the missing 2185-equivalent TX source; retaining the existing
page-published boundary is justified. The unresolved issue remains the
content/state of the V90A source waveform before RTP conversion.

Capture: `artifacts/loopback-v90a-sporttx-ab/`.

### Session 340 — sample-and-hold of sparse V90D TX is not the cadence fix

The direct V90D output was tested with the opt-in
`EICON_V90D_TX_HOLD_LAST=1` diagnostic. It replaces zero-valued bearer frames
after a nonzero page-14 publication with the last nonzero sample, testing the
hypothesis that the serializer's sparse updates should be exposed as a
sample-and-hold stream. The loopback regressed to caller `0x0092` / answerer
`0x002c`, rather than the default `0x00c0` / `0x00c2`. The sparse publication
must therefore remain zero-filled between firmware updates; repeating the
last sample is not a valid 2185/DAA cadence correction. The diagnostic is
disabled by default.

Capture: `artifacts/loopback-v90a-v90d-holdlast/`.

### Session 341 — native 2185 TX-level database values regress the Analog call

The caller-side Analog database was tested against the native 2185 control
values recorded in the handoff notes. The full tuple
`TD=0x000c, TA=0x000c, TX_LEVEL_TUNE=0x00b8` regressed the clean loopback to
caller `0x0092` / answerer `0x002c`, rather than the default
`0x00c0` / `0x00c2`. Isolated A/Bs of `TX_LEVEL_TUNE=0x00b8` alone and
`TD/TA=0x000c` alone produced the same early regression. These fields are
therefore not portable 2185 values for the Analog build's V.8/DIAL path, and
no DAA/codec database change is promoted.

Captures: `artifacts/loopback-v90a-native-db-setup/`,
`artifacts/loopback-v90a-db-txlevel/`, and
`artifacts/loopback-v90a-db-tonelevels/`.

### Session 342 — ADSP MAC rounding is not the remaining V90A source defect

The generic arithmetic hypothesis was checked against the ADSP-2181 core's
focused tests. `make -C tools/adsp2181emu test` passes, including the exact
midpoint distinction between complete-accumulator unbiased rounding and the
2185N `BIASRND` mode selected by bit 14 of `DM(0x3ff3)`. The MAC implementation
rounds the accumulated 40-bit result, and the biased/unbiased tie behavior is
covered by an executable test; this is not an untested product of arithmetic
emulation.

This does not prove every V90A coefficient or record value correct, but it
rules out a missing generic `RND`/`BIASRND` implementation as the explanation
for the active `DM(0x2120)` → PM `0x38c8` source chain. No emulator arithmetic
patch is justified. The next comparison should remain at the V90A source
state/coefficient or protocol-coupled waveform boundary.
