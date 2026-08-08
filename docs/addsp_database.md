# The ADDSP data-pump database, by address

Every name in this file comes from `docs/addspv90guide.pdf` (ADDSP V.90 guide,
version 5.3, 6 February 1999), sections 5.3.1, 5.3.2 and 6.5. It exists because
this project keeps re-deriving these locations and mislabelling them, and a
mislabelled address costs a session: see the traps below, all of which are real
mistakes made in this repo rather than hypothetical ones.

**Read this before naming a DM address in the 0x3EE0..0x3FFF window.**

## Addressing

Guide 6.5, stated directly:

- the **write** database is 128 consecutive words, first address **`0x3EE0`**
- the **read** database is 128 consecutive words, first address **`0x3F60`**,
  and is "mapped into the address offset range 128 until 255"

So for the offsets used by the 5.3.1/5.3.2 tables, both halves share one
formula:

```text
DM = 0x3EE0 + offset        offset 0x00..0x7F write, 0x80..0xFF read
```

## Three traps

1. **The guide numbers read locations two different ways.** The 5.3.2 table
   uses the absolute offset (`0xBC`), but the prose in 6.6 uses the index
   *within* the read database (`3C`). `EYESAMPLE_0` is both: table offset
   `0xBC`, "read database location 3C", one address, `DM(0x3F9C)`. Feeding a
   read-relative index into the write-side formula gives `0x3EE0 + 0x3C =
   0x3F1C`, which is a real but unrelated location, reads zero, and looks
   exactly like a feature that is not implemented. That is how this project
   concluded the eye pattern "is not being generated" while it was being
   generated the whole time, into three columns the capture was already
   recording.

2. **A local name is not a definition.** `tools/eicon_adsp_sip.py` calls
   `DM(0x3F87)` `dil_count` and `DM(0x3F8E)` `dil_measure` after the DIL
   investigation that found them. The guide calls `0x3F87` `RTDelay`, round
   trip delay in 10 ms units. Both may be true -- the firmware is free to reuse
   a word -- but the guide name is the one with a specification behind it, and
   a local name in a CSV header is not evidence about what the firmware does.

3. **"The page never writes it" is a claim about a measurement, not a fact.**
   `DATASTATESpeed` at `DM(0x3F62)` was recorded as never written by the V.32
   page, "watched across a whole call, zero writes". It is written, and it
   carries the negotiated rate: measured live against slmodemd as 0x11aa,
   0x11a9 and 0x01a8 while the peer moved 9600 -> 7200 -> 4800. The word also
   documents itself -- bit C is a trellis bit the guide marks "Only for
   V32bis". Re-check a negative like this against `Rstatus_ch` bits D and B,
   which are the card's own statement that the speed is available to be read.

## The table

`kind` is which half of the database the location belongs to. Descriptions are
the guide's first line; go to the guide for bitfields.

| DM | offset | kind | name | guide description |
|---|---|---|---|---|
| `0x3EE0` | 0x00 | write | `GEN_setup0` | training defining parameters |
| `0x3EE1` | 0x01 | write | `GEN_setup1` | operation mode parameters |
| `0x3EE3` | 0x03 | write | `DISP_setup` | See appendix 6.6 |
| `0x3EE4` | 0x04 | write | `V8_setup` | default:[0000] |
| `0x3EE5` | 0x05 | write | `FAX_setup` | F,   spare |
| `0x3EE6` | 0x06 | write | `V34_setup` | default:[0000] |
| `0x3EE7` | 0x07 | write | `Info0_setup` | default:[F0FD] |
| `0x3EE8` | 0x08 | write | `TD` | 4 LSB:TX level (0 -> 0 dB, F -> -15 dB) of transmit |
| `0x3EE9` | 0x09 | write | `TA` | 4 LSB :TX level of single tones (answer tone, dial |
| `0x3EEA` | 0x0A | write | `TX_LEVEL_TUNE` | 2 dB tuning of all TX levels according to the formula |
| `0x3EEB` | 0x0B | write | `DCD_OFF` | If the energy level of the received signal drops below |
| `0x3EED` | 0x0D | write | `TXD` | TXD is a 16 bit location containing the main channel |
| `0x3EEE` | 0x0E | write | `WSTATUS` | this location contains the write database status. |
| `0x3EEF` | 0x0F | write | `P2SD` | P2SD is a 16 bit location containing the input data to |
| `0x3F02` | 0x22 | write | `called` | ‘DIALLER’. |
| `0x3F04` | 0x24 | write | `delaycorrection` | The Tuning procedure of the modem with respect to |
| `0x3F05` | 0x25 | write | `TXD0` | TXD0 is a 16 bit location containing the main |
| `0x3F06` | 0x26 | write | `TXD1` | TXD1, b0 is the sixteenth bit of the Datagram, |
| `0x3F08` | 0x28 | write | `Norm_H` | default=[0] |
| `0x3F09` | 0x29 | write | `Norm_L` | 0001                       V21 |
| `0x3F0A` | 0x2A | write | `speed_sel_h` | This location contains the high word of the speed rate |
| `0x3F0B` | 0x2B | write | `speed_sel_l` | This location contains the low word the speed rate |
| `0x3F0C` | 0x2C | write | `Maxtimer` | Period during which the MSE should be greater than |
| `0x3F0D` | 0x2D | write | `Mintimer` | Period during which the MSE should be smaller than |
| `0x3F0E` | 0x2E | write | `rebootop_` | default=[0] |
| `0x3F0F` | 0x2F | write | `shellinptr` | default=[0] |
| `0x3F10` | 0x30 | write | `reserved` | The locations in this range contain information for |
| `0x3F2F` | 0x4F | write | `supervisory` | tone detection can be found in a related |
| `0x3F30` | 0x50 | write | `RXSAMPLE_0` | At symbol rate the kernel writes 3,4 or 5 samples in |
| `0x3F31` | 0x51 | write | `RXSAMPLE_1` |  |
| `0x3F32` | 0x52 | write | `RXSAMPLE_2` |  |
| `0x3F33` | 0x53 | write | `RXSAMPLE_3` |  |
| `0x3F34` | 0x54 | write | `RXSAMPLE_4` |  |
| `0x3F35` | 0x55 | write | `RXSAMPLE_5` | Near Bulk output point of the bulk delay line and |
| `0x3F36` | 0x56 | write | `BulkOutNeaRX` | At symbolrate the kernel fetches the X sample of the |
| `0x3F37` | 0x57 | write | `BulkOutNearY` | At symbolrate the kernel fetches the Y sample of the |
| `0x3F38` | 0x58 | write | `BulkOutpuTX` | At symbolrate the kernel fetches the oldest X sample |
| `0x3F39` | 0x59 | write | `BulkOutputY` | At symbolrate the kernel fetches the oldest Y sample |
| `0x3F4F` | 0x6F | write | `Bootcoreptr` | location indicating the starting address of the boot     read |
| `0x3F50` | 0x70 | write | `Sp0CntrlReg` | This location has an identical bit setting as the |
| `0x3F51` | 0x71 | write | `Sp0MCRecL` | Location with identical bit setting as the SPORT0 |
| `0x3F52` | 0x72 | write | `Sp0MCRecM` | Location with identical bit setting as the SPORT0 |
| `0x3F53` | 0x73 | write | `Sp0MCTXL` | Location with identical bit setting as the SPORT0 |
| `0x3F54` | 0x74 | write | `Sp0MCTXM` | Location with identical bit setting as the SPORT0 |
| `0x3F55` | 0x75 | write | `MinReduction_dbs` | Location specifying the minimum transmission level |
| `0x3F56` | 0x76 | write | `AddReduction_dbs` | Location specifying the additional transmission level |
| `0x3F57` | 0x77 | write | `DATACONFIG` | write |
| `0x3F58` | 0x78 | write | `V34SLOT` | For a TDM line interface V34SLOT |
| `0x3F5A` | 0x7A | write | `speed_sel_V90_L` | In case of V.90 operation this location determines the |
| `0x3F5B` | 0x7B | write | `Info0D_setup` | F |
| `0x3F5C` | 0x7C | write | `MAXTXSPEED` | In case of V.34 split speed operation this location |
| `0x3F5D` | 0x7D | write | `MAXTXSPEED_V90` | In case of V.90 operation this location determines the |
| `0x3F5F` | 0x7F | write | `MAXRXSPEED_V90` | In case of V.90 operation this location determines the |
| `0x3F60` | 0x80 | read | `DatagramRate` | location specifying the frequency at which the |
| `0x3F61` | 0x81 | read | `DATASTATEspeedTx` | F  spare |
| `0x3F62` | 0x82 | read | `DATASTATESpeed` | F  spare |
| `0x3F63` | 0x83 | read | `V33MultiPlToHost` | V33 Received Multiplexer Configuration (numbering |
| `0x3F64` | 0x84 | read | `ErrorMessage` | ErrorMessage of Tfast |
| `0x3F65` | 0x85 | read | `Symbolrate` | 8                   8000/6 |
| `0x3F66` | 0x86 | read | `Samplerate` | 8                   8000 |
| `0x3F67` | 0x87 | read | `Samplebuffersize` | The ratio of sample- and symbolrate=number of |
| `0x3F68` | 0x88 | read | `ToneLevelA` | output tone detection circuit A in dB. |
| `0x3F69` | 0x89 | read | `ToneLevelB` | output tone detection circuit B in dB. |
| `0x3F6A` | 0x8A | read | `VersionA_L` | contains XXS value for processor A described in |
| `0x3F6B` | 0x8B | read | `VersionB_L` | contains XXS value for processor B described in |
| `0x3F6C` | 0x8C | read | `HW_Norm_H` | Modulations not supported, based on Hardware |
| `0x3F6D` | 0x8D | read | `HW_Norm_L` | Modulations not supported, based on Hardware |
| `0x3F6E` | 0x8E | read | `ZerCrosCntr` | *definition =No. of zero crossings. |
| `0x3F6F` | 0x8F | read | `Eventbufferlength` | location containing the length of the eventbuffer |
| `0x3F70` | 0x90 | read | `Eventstructptr` | location containing the first address of the event |
| `0x3F71` | 0x91 | read | `Datastructptr` | location containing the first address of the parallel |
| `0x3F72` | 0x92 | read | `Unitimer` | real time clock which ticks at 1 kHz frequency, can |
| `0x3F73` | 0x93 | read | `UnitimerLSW` |  |
| `0x3F74` | 0x94 | read | `Unitimerdelta` | functions’ database of Channel 00 . |
| `0x3F75` | 0x95 | read | `LLFbasePtr` | location contain the base address of the ‘low level |
| `0x3F76` | 0x96 | read | `DCESCCstructptr` | location containing the first address of the SCC1 data |
| `0x3F77` | 0x97 | read | `LLFbaseLength` | location containing the length of the ‘low level |
| `0x3F79` | 0x99 | read | `EcLevel` | *definition =10 LOG (AVERAGE POWER OF THE |
| `0x3F7A` | 0x9A | read | `NearEcLevel` | *definition = cfr Echolevel |
| `0x3F7B` | 0x9B | read | `FarEcLevel` | *definition = cfr Echolevel |
| `0x3F7C` | 0x9C | read | `FarEchoPhaseRoll` | *definition = cfr Frequency offset (Only measured in |
| `0x3F7D` | 0x9D | read | `SNRatio` | Signal To Noise Ratio |
| `0x3F7E` | 0x9E | read | `FreqOffset` | Frequency Offset |
| `0x3F7F` | 0x9F | read | `TimOffset` | Timing Offset |
| `0x3F81` | 0xA1 | read | `PeakGainErr` | PeakGainErr (Gain Hits, only V.32) |
| `0x3F82` | 0xA2 | read | `PhaseJit` | Single frequency phase jitter |
| `0x3F83` | 0xA3 | read | `PeakPhasErr` | Peak Phase Error |
| `0x3F84` | 0xA4 | read | `INR` | Impulse Noise Ratio {only V.32} |
| `0x3F86` | 0xA6 | read | `Signalquality` | MAE (mean absolute error) calculated at the receiver |
| `0x3F87` | 0xA7 | read | `RTDelay` | Round Trip Delay |
| `0x3F88` | 0xA8 | read | `reserved` | … |
| `0x3F97` | 0xB7 | read | `reserved` |  |
| `0x3F98` | 0xB8 | read | `spare` | .. |
| `0x3F9B` | 0xBB | read | `spare` |  |
| `0x3F9C` | 0xBC | read | `EYESAMPLE_0` | At symbol rate the modem core writes a (X,Y) |
| `0x3F9D` | 0xBD | read | `EYESAMPLE_1` | At symbol rate the modem core writes a (X,Y) |
| `0x3F9E` | 0xBE | read | `EYESAMPLE_2` | At symbol rate the modem core writes a (X,Y) |
| `0x3F9F` | 0xBF | read | `Gen_Control` |  |
| `0x3FA0` | 0xC0 | read | `RXD` | RXD is a 16 bit location that contains 16 data bits |
| `0x3FA1` | 0xC1 | read | `changeBITS` | F     ch_rstatus_ch_dbs    Set to one during one Host-Kernel RX_2400 |
| `0x3FA2` | 0xC2 | read | `S2PD` | S2PD is a 16 bit location that contains output data |
| `0x3FA3` | 0xC3 | read | `RSTATUS_CH_dbs` | definition identical to Rstatus_ch |
| `0x3FA4` | 0xC4 | read | `RSTATUS_dbs` | definition identical to Rstatus |
| `0x3FA5` | 0xC5 | read | `TRNPROGRESS_dbs` | definition identical to TrnProgess |
| `0x3FA6` | 0xC6 | read | `PF_setup` | This location contains the value of the programmable |
| `0x3FA7` | 0xC7 | read | `TXSAMPLE_0` | At symbol rate the modem core writes 3,4,5 or 6 |
| `0x3FA8` | 0xC8 | read | `TXSAMPLE_1` |  |
| `0x3FA9` | 0xC9 | read | `TXSAMPLE_2` |  |
| `0x3FAA` | 0xCA | read | `TXSAMPLE_3` |  |
| `0x3FAB` | 0xCB | read | `TXSAMPLE_4` |  |
| `0x3FAC` | 0xCC | read | `TXSAMPLE_5` |  |
| `0x3FAD` | 0xCD | read | `DI_control` | F   TX request bit   if 1, the modem core asks the kernel to give a new |
| `0x3FAE` | 0xCE | read | `RXD0` | RXD0 is a 16 bit location that contains 16 data bits |
| `0x3FAF` | 0xCF | read | `RXD1` | RXD1 is a 16 bit location that contains 16 data bits |
| `0x3FB0` | 0xD0 | read | `bootpage_nr` | parameter specifying the page number to be loaded |
| `0x3FB1` | 0xD1 | read | `spare` |  |
| `0x3FB2` | 0xD2 | read | `Init8kRoutine` |  |
| `0x3FB3` | 0xD3 | read | `Core8kRoutine` |  |
| `0x3FB4` | 0xD4 | read | `ShellOutptr` |  |
| `0x3FB5` | 0xD5 | read | `GEN_CONTROL` |  |
| `0x3FB6` | 0xD6 | read | `CODECRXPllPhaseShift` |  |
| `0x3FB7` | 0xD7 | read | `uOffset` |  |
| `0x3FB8` | 0xD8 | read | `CoreRoutine` |  |
| `0x3FB9` | 0xD9 | read | `InitRoutine` |  |
| `0x3FBA` | 0xDA | read | `RTVal` |  |
| `0x3FBC` | 0xDC | read | `Nearbulklength` | parameter specifying the length of the Near Echo |
| `0x3FBD` | 0xDD | read | `BulkLength` | parameter specifying the circular length of the Far |
| `0x3FBE` | 0xDE | read | `BulkInputX` | at Symbolrate the V.34 modemCore offers the kernel |
| `0x3FBF` | 0xDF | read | `BulkInputY` | at Symbolrate the V.34 modemCore offers the |
| `0x3FC0` | 0xE0 | read | `Rstatus_ch` | F    change_h     set to one if b8..b14 has changed |
| `0x3FC1` | 0xE1 | read | `Rstatus` | F    spare |
| `0x3FC2` | 0xE2 | read | `TrnProgress` | Training progress as defined on the training |
| `0x3FC3` | 0xE3 | read | `reserved` | ... |
| `0x3FDF` | 0xFF | read | `reserved` |  |

## Locations this project uses that are not in the table above

These are referenced by `tools/eicon_mips_shim.py` or `tools/eicon_adsp_sip.py`
and were not matched to a guide entry by the extraction. Some are genuinely
undocumented and were established here by watchpoint; some are simply formatted
differently in the PDF and are worth another look before being treated as
proprietary:

`0x3F07` `0x3F80` `0x3F89` `0x3F8B` `0x3F8D` `0x3F8E` `0x3F94` `0x3FBB`
`0x3FC4` `0x3FCB` `0x3FFF`

Regenerate with:

```bash
grep -ohE '0x3[EF][0-9A-Fa-f]{2}' tools/eicon_mips_shim.py tools/eicon_adsp_sip.py | sort -u
```

`0x3FC4` is the one to know: the classifier at PM `0x3ba1..0x3bfb` selects the
pending page from it alone, which makes it the real modulation selector in this
harness (analysis volume 05).

## Modulation and speed masks

Four locations are bit-per-capability rather than scalar, and one read location
decodes against them. They are the reason a modulation question can usually be
answered by reading a word rather than by watching the firmware.

`Norm_H` and `Norm_L` select which modulations are offered. `speed_sel_h` and
`speed_sel_l` are the V.34-format speed capability mask; `speed_sel_V90_H` and
`speed_sel_V90_L` are the V.90-format one, and `DATASTATESpeed` bit D says which
of the two formats its speed number indexes. The guide's notes on Norm_L are
worth keeping with the table:

- V.32 is a subset of V32ext with only 4800 and 9600U selected
- V32ext has the same speed range as V32bis but does not support the rate
  change procedure
- AUTO and AutoV8 select 9600 and 9600U if 9600 is in the user's range,
  according to V.32bis

### Reading a selection back out

`DATASTATESpeed` (read 0x82, `DM(0x3F62)`) is not only the rate. Bits 4..0 are
the speed number, an index into the selected speed mask; bits 9..5 are the
*norm* number, the bit position in `Norm_L`/`Norm_H` of the modulation actually
running; bit C is a trellis flag the guide marks "Only for V32bis"; bit D picks
the speed-mask format. The three words captured live against slmodemd decode
against the tables below with no free parameters:

```text
0x11aa   speednumber 10 -> 9600   normnumber 13 -> V32bis   trellis 1
0x11a9   speednumber  9 -> 7200   normnumber 13 -> V32bis   trellis 1
0x01a8   speednumber  8 -> 4800   normnumber 13 -> V32bis   trellis 0
```

The trellis bit dropping at 4800 is the internal check: 4800 is the uncoded
rate in V.32bis. A word that decodes this cleanly is a word being published on
purpose, which is the evidence that retired "the V.32 page never writes it".

### `Norm_H` — write 0x28, `DM(0x3F08)`

| bit | mask | meaning |
|---|---|---|
| 0 | `0x0001` | V8 |
| 1 | `0x0002` | V110 |
| 2 | `0x0004` | V18 |
| 3 | `0x0008` | speakerphone |
| 4 | `0x0010` | Low Level |
| 5 | `0x0020` | — |
| 6 | `0x0040` | — |
| 7 | `0x0080` | — |
| 8 | `0x0100` | — |
| 9 | `0x0200` | — |
| 10 | `0x0400` | — |
| 11 | `0x0800` | — |
| 12 | `0x1000` | — |
| 13 | `0x2000` | — |
| 14 | `0x4000` | unload |
| 15 | `0x8000` | boot |

### `Norm_L` — write 0x29, `DM(0x3F09)`

| bit | mask | meaning |
|---|---|---|
| 0 | `0x0001` | — |
| 1 | `0x0002` | V22 |
| 2 | `0x0004` | V22B |
| 3 | `0x0008` | V23 |
| 4 | `0x0010` | BEL212A |
| 5 | `0x0020` | BELL103 |
| 6 | `0x0040` | V21ch2 |
| 7 | `0x0080` | V29FDX |
| 8 | `0x0100` | V34 |
| 9 | `0x0200` | V27Ter |
| 10 | `0x0400` | V29 |
| 11 | `0x0800` | V17 |
| 12 | `0x1000` | V32ext |
| 13 | `0x2000` | V32bis |
| 14 | `0x4000` | V33 |
| 15 | `0x8000` | V90 |

### `speed_sel_h` — write 0x2A, `DM(0x3F0A)`

| bit | mask | meaning |
|---|---|---|
| 0 | `0x0001` | — |
| 1 | `0x0002` | — |
| 2 | `0x0004` | — |
| 3 | `0x0008` | 31200 |
| 4 | `0x0010` | 33600 |
| 5 | `0x0020` | — |
| 6 | `0x0040` | — |
| 7 | `0x0080` | — |
| 8 | `0x0100` | — |
| 9 | `0x0200` | — |
| 10 | `0x0400` | — |
| 11 | `0x0800` | — |
| 12 | `0x1000` | — |
| 13 | `0x2000` | 9600U |
| 14 | `0x4000` | — |
| 15 | `0x8000` | — |

### `speed_sel_l` — write 0x2B, `DM(0x3F0B)`

| bit | mask | meaning |
|---|---|---|
| 0 | `0x0001` | cleardown |
| 1 | `0x0002` | 75 |
| 2 | `0x0004` | 110 |
| 3 | `0x0008` | 150 |
| 4 | `0x0010` | 300 |
| 5 | `0x0020` | 600 |
| 6 | `0x0040` | 1200 |
| 7 | `0x0080` | 2400 |
| 8 | `0x0100` | 4800 |
| 9 | `0x0200` | 7200 |
| 10 | `0x0400` | 9600 |
| 11 | `0x0800` | 12000 |
| 12 | `0x1000` | 14400 |
| 13 | `0x2000` | 16800 |
| 14 | `0x4000` | 19200 |
| 15 | `0x8000` | 21600 |

### `speed_sel_V90_H` — write 0x79, `DM(0x3F59)`

| bit | mask | meaning |
|---|---|---|
| 0 | `0x0001` | 49000+1000/3 |
| 1 | `0x0002` | 50000+2000/3 |
| 2 | `0x0004` | 52000 |
| 3 | `0x0008` | 53000+1000/3 |
| 4 | `0x0010` | 54000+2000/3 |
| 5 | `0x0020` | 56000 |
| 6 | `0x0040` | — |
| 7 | `0x0080` | — |
| 8 | `0x0100` | — |
| 9 | `0x0200` | — |
| 10 | `0x0400` | — |
| 11 | `0x0800` | — |
| 12 | `0x1000` | — |
| 13 | `0x2000` | — |
| 14 | `0x4000` | — |
| 15 | `0x8000` | — |

### `speed_sel_V90_L` — write 0x7A, `DM(0x3F5A)`

| bit | mask | meaning |
|---|---|---|
| 0 | `0x0001` | 28000 |
| 1 | `0x0002` | 29000+1000/3 |
| 2 | `0x0004` | 30000+2000/3 |
| 3 | `0x0008` | 32000 |
| 4 | `0x0010` | 33000+1000/3 |
| 5 | `0x0020` | 34000+2000/3 |
| 6 | `0x0040` | 36000 |
| 7 | `0x0080` | 37000+1000/3 |
| 8 | `0x0100` | 38000+2000/3 |
| 9 | `0x0200` | 40000 |
| 10 | `0x0400` | 41000+1000/3 |
| 11 | `0x0800` | 42000+2000/3 |
| 12 | `0x1000` | 44000 |
| 13 | `0x2000` | 45000+1000/3 |
| 14 | `0x4000` | 46000+2000/3 |
| 15 | `0x8000` | 48000 |

