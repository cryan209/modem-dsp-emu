# The V.8 page is a script interpreter, and both builds gate CM the same way

`tools/v8_script_records.py` decodes the V.8 page's script-record table out of
an ADDSP V.90 overlay. `tests/test_v8_script_records.py` pins it to values
measured on live calls.

The V.8 page is not a hand-written state machine. It is an interpreter over a
table of variable-length **records**, one per V.8 state, and nearly everything
the page does — which message it builds, which detector it runs, where it goes
next — is record data rather than code. That makes two builds of the package
comparable as data, which is what this is for.

## The encoding

Read straight off the loader, `PM 0x37B7` in Eicon `0x025f V8.ANA`:

```text
37b7: AY0 = $00FF                     ; field mask
37b8: MR0 = $073F                     ; field base
37b9: AX0 = DM(I4,M5)                 ; w0 -> offset = w0 & 0xFF
37ba: AF = AX0 AND AY0, AR = DM(I4,M5); w1
37bb: AR = AR AND AY0, SR0 = DM(I4,M5); w2
37bc: AR = MR0 + AF, SR1 = AR         ; address = 0x073F + offset
37be: SR = LSHIFT SR0 (HI, OR) BY 8   ; value = (w2 & 0xFF) << 8 | (w1 & 0xFF)
37bf: DM(I0,M1) = SR1, AR = MR1 XOR AF
37c0: IF NE JUMP $37B9                ; until offset == MR1 == 0x11
37c1: DM($0780) = M1                  ; "a new record was loaded"
```

A record is a run of three-word triples terminated by — and including — the
triple whose offset is `0x11`. Records are contiguous, which is what makes
fall-through *available*; `I4` is the cursor `DM(0x049F)` and is left pointing
at the next record.

Only fields the record carries are written, so **every field persists into the
next record until something rewrites it**. That is the single most important
property of the format and the source of most of the confusion below.

| off | DM | role |
|---|---|---|
| 0x01 | 0x0740 | action mask — 9-entry routine table `PM 0x3DF6`; **bit 4 = CM builder `PM 0x3828`**, bit 5 = JM `0x3859`, bit 7 = CI `0x3817` |
| 0x03 | 0x0742 | second action mask (13-entry table `PM 0x3E0F`) |
| 0x0D/0x0E | 0x074C/D | slot-1 / slot-2 destination index |
| 0x0F/0x10 | 0x074E/F | slot-1 / slot-2 condition index |
| 0x11 | 0x0750 | slot-0 condition index — the fall-through gate, and the terminator |

`PM 0x379A..0x37A3` resolves indices through two tables, destinations at
`DM(0x035B) + index` and conditions at `DM(0x034A) + index`; `PM 0x37A4..0x37AF`
runs the three slots in order. Slot 0 returning `LE` means fall through to the
contiguously next record, slot 1 or 2 returning `LE` means jump to that slot's
destination, nothing taken means the state repeats.

Both tables are in the overlay's static DM in build 109 and need no
initialiser walk; the note in `docs/handoff.md` that they read as nonsense was
reading them at the wrong record phase.

## Validation

The Eicon decode reproduces every value measured on a live call: 43 records
tiling `0x0050..0x034A` exactly, masks `0x01DC = 0x0086` (CI), `0x021B = 0x0016`
(CM), `0x031D = 0x0100`, `0x033B = 0x0001`, fall-through condition indices 9 for
`0x029F`, 0 for `0x02AB`, 5 for `0x021B`, and destination indices 10 → `0x031D`,
14 → `0x02B7`, 17 → `0x021B`. **Records start at `0x0050`, not `0x0000`** — the
first 80 words are a different structure, and starting there puts the decode a
word out of phase.

## What it says about the caller: the fork is `0x01BB`, not `0x02AB`

Exactly one record builds a CM (`0x021B`), and the chain that reaches it is

```text
0x01BB --slot1, condition 14--> 0x02B7 -> 0x02C9 -> 0x02D5 --slot1--> 0x021B
```

`0x02B7` is destination index 14, and **`0x01BB` slot 1 is the only entry to it
anywhere in the table**. The caller's live walk is
`0x0341 → 0x0194 → 0x01BB → 0x01C7 → …`: it *falls through* `0x01BB` instead of
taking slot 1. So the branch that leads to CM is declined four records earlier
than the `0x02AB` fork the previous analysis settled on.

Condition 14 is `PM 0x3525`, and it is three instructions:

```text
3525: AR = DM($3F4B)
3526: AF = AR AND $0100
3527: IF EQ JUMP $3529     ; bit 8 clear -> AR = 1, never LE, slot NOT taken
3528: JUMP $352B           ; bit 8 set   -> AR = 0, LE, slot taken -> CM branch
```

**The entire CM branch is gated on bit 8 of `DM(0x3F4B)`.** Condition 15
(`PM 0x3510`) clears the same bit (`AND $FEFF`), so it is a one-shot.

`DM(0x3F4B)` is in the data-pump database window, offset 0x6B, inside the block
`docs/addsp_database.md` records only as reserved. It survives page boots — no
overlay loads it.

## Who sets bit 8

Across the whole build-109 Analog overlay set, kernel and TIKRNL:

| image | reads | writes |
|---|---|---|
| `V8.ANA` | `0x351C`, `0x3521`, `0x3525` | `0x351E`, `0x3523` — both **clear** bit 8 |
| `INFO.ANA` | 10 sites | `0x2436`, `0x247F` **set** bit 8 (`AR OR $0100`); `0x242E`, `0x260D` clear bit 7 |
| V.34, V.32, V.22, V.90, FSK | one or two reads each | none |
| DIAL overlays, kernel, TIKRNL | none | none |

So on this card the only setter is the **INFO page**, at `PM 0x2436` — one arm
of a five-way received-message dispatch (`0x2430..0x243F`, each arm setting
`M6 = 0..4` and jumping to `0x2446`). Nothing in DIAL or the kernel sets it.

That is consistent with the boot order in `docs/dial_v8_call.md`
(DIAL → V.8 → INFO → V.34/V.90) only if V.8 is re-entered after INFO has run.
Whether the intended origination path re-enters V.8, or whether the host is
expected to seed bit 8 before the first V.8 boot, is **not settled by this
data** — both are consistent with what is written here.

## The Aster cross-check

`docs/firmware/Aster 5 DSP/T8660014.00` page 6, a Telindus build from 2005
against Eicon's 1999 one, decodes with the same interpreter: 67 records tiling
`0x0050..0x046A`, condition table at `0x046A` (23 entries), destination table at
`0x0481` (23). Same builder masks — CI `0x0086` at `0x0257`, JM `0x0020` at
`0x015E`, and exactly one CM record, `0x02C9`.

The relevant chain is structurally identical, one hop longer:

```text
Eicon  0x01BB --slot1/cond14--> 0x02B7 -> 0x02C9 -> 0x02D5 --slot1--> 0x021B (CM)
Aster  0x0236 --slot1/cond14--> 0x038F -> 0x03A1 -> 0x03AD --slot1--> 0x02BD -> 0x02C9 (CM)
```

Same condition index, and the condition itself is the same code relocated:

```text
Eicon PM 0x3525          Aster PM 0x2425
  AR = DM($3F4B)           AR = DM($3F4B)
  AF = AR AND $0100        AF = AR AND $0100
  IF EQ JUMP $3529         IF EQ JUMP $2429
```

Instruction for instruction, including the one-shot clear at Eicon `0x3510` /
Aster `0x2410`. The condition tables line up on the same low 12 bits at indices
14/15/16 (`0x3525/0x3510/0x3529` against `0x2425/0x2410/0x2429`).

**So the `DM(0x3F4B)` bit-8 gate is package behaviour, not Eicon
customisation.** Two independent builds six years apart gate entry to the CM
branch on the same host-visible database bit. That was the one thing the Aster
image could settle that the Eicon image alone could not, and it settles it.

Two further results from the same comparison:

- **The two Aster data pumps share the record table byte for byte.** ASTDTP1
  (full ladder) and ASTDTP2 (the dial-backup channel, STARTUP/DIAL/V.22/V.8
  only) have identical page-6 DM across `0x0000..0x0497`. The hope that the
  originate-only pump would carry a different table is dead — but the reason it
  can be identical is that origination is selected by database bits, not by
  shipping a different script.
- **Aster's DIAL page does write `DM(0x3F4B)`** (`PM 0x2529`) — but it clears
  bit 15, not bit 8. Eicon's DIAL never touches the word at all.

## An open conflict, worth resolving before acting

The record data says **`0x02AB` has no script-level exit**. It carries no
destination index (so `DM(0x0790)`/`DM(0x0791)` persist from `0x029F`), and it
rewrites both its fall-through and slot-1 conditions to index 0 — `PM 0x37D5`,
`AR = 0 + 1`, which `IF LE` never takes. `0x028D` left the slot-2 condition at
index 0 as well. All three slots are therefore untakeable, which means the state
should repeat forever.

The archived live trace has it reaching `0x031D`. Both cannot be right. Either
the trace missed an intermediate state, or something outside the three slots
rewrites the cursor. This does not affect the `0x01BB` finding — that fork is
upstream of `0x02AB` and is reached on every run — but it does mean the earlier
"`0x02AB` is the fork" analysis rests on a measurement that the record data
contradicts.

## The test to run next

`EICON_ANALOG_PIN_DM=0x3F4B=0x0100` on the Analog caller, held from before the
V.8 page boots. If the walk becomes `0x0194 → 0x01BB → 0x02B7 → 0x02C9 →
0x02D5 → 0x021B`, mask `0x0016`, that is the same CM builder the
`0x0790=0x021b@0x049f:0x02ab` pin reached in `docs/handoff.md` — but reached
through the branch the firmware actually designed for it, rather than by
overwriting a destination word. The two pins landing on the same record from
different directions would be strong mutual confirmation.

Note the handoff's warning about that earlier pin: under it the caller
transmitted nothing at all. Reaching `0x021B` by the designed path may or may
not arm a transmit; that is a separate question and `PM 0x3E00`'s missing call
site is still loose.

## Usage

```bash
./tools/v8_script_records.py artifacts/eicon-dsp/build-109-789-analog/overlays/025f-v8.ana-overlay/dm.words --start 0x0050 --graph
```

```bash
./tools/v8_script_records.py "docs/firmware/Aster 5 DSP/T8660014.00" --aster --image 0 --page 6 --start 0x0050 --cond-base 0x046A --dest-base 0x0481 --table-count 23
```

`--fields` dumps every field of every record, `--find-mask-bit 4` reports only
the CM builders, `--graph` lists each record's predecessors.
