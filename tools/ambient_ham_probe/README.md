# Ambient/Intel HaM DSP probe

This is a deliberately small diagnostic PCI driver for the Ambient HaM modem
with PCI ID `1813:4000`. It maps BAR0 and reports the registers used by Intel's
2002 `Intel-v92ham-453` Linux driver.

Loading it normally is read-only:

```sh
make
sudo insmod ambient_ham_probe.ko
sudo dmesg | tail -20
sudo rmmod ambient_ham_probe
```

The recovered DSP reset sequence is opt-in:

```sh
sudo insmod ambient_ham_probe.ko do_reset=1
sudo dmesg | tail -20
sudo rmmod ambient_ham_probe
```

The reset path writes only the small register area used by the original
driver. It does not enable bus mastering, touch the DAA/telephone-line relays,
load a DSP patch, or read the interrupt-acknowledge register at `0xff`.
The side-effecting patch-data register at `0xfa` is not read during snapshots,
and the device's original PCI command word is restored when the module unloads.
Before restoring PCI decoding, every removal and post-probe failure masks DSP
interrupts, acknowledges any pending source, and holds the DSP in reset. This
prevents an asserted legacy INTx line from surviving after BAR0 is disabled.

The sequence reconstructed from the unstripped `hamcore.lib` is:

1. Write `0x02` to BAR0 offset `0xfc`.
2. Write `0x00` to `0xf7` to disable DSP interrupts.
3. Set bit 0 at `0xf6` (`SetDspCfg`).
4. Write `0x01` to `0x42`.
5. Write `0x01` to `0xfc`.
6. Poll `0x42` for zero for at most 500 ms, then settle for 150 ms.

Patch extraction and loading are intentionally separate from this first probe.

Extract the complete overlay catalog directly from the vendor object:

```sh
./extract_patch.py Intel-v92ham-453/coredrv/hamcore.lib ham_patch_array.bin
sudo install -D -m 0644 ham_patch_array.bin \
  /lib/firmware/ambient/ham_patch_array.bin
```

Power-on patch loading and the DSP version query are separately opt-in:

```sh
sudo insmod ambient_ham_probe.ko load_patch=1
sudo dmesg | tail -30
sudo rmmod ambient_ham_probe
```

`load_patch=1` parses the catalog, writes only overlay ID 0 using the recovered
`0xf8`/`0xf9` address and `0xfa` data ports, releases reset, sends the original
`0xa3` load-complete packet, services subsequent `0xd6` overlay requests using
the original `0x0c` packet format, then sends the original `0x94` version query.

## Verified hardware result

On the `EICON420` host with card revision 2 and Linux 6.12, the complete path
was verified on 2026-09-24:

- power-on overlay 0: 3 records, 823 DSP words;
- DSP release acknowledged after 13 ms;
- DSP request `d6 00 02 00 50 04`: overlay 4;
- overlay 4: 5 records transferred in 261 CRAM packets;
- both `0xa3` completion messages acknowledged;
- version response: `cb 00 06 00 37 00 08 00 00 00`;
- raw Cutlass version payload: `37 00 08 00 00 00`.

The probe was unloaded after the test and restored the pre-test PCI command
word `0x0100`.

Confirmed overlay semantics from on-hardware request tracing:

| Overlay | Confirmed role |
| ---: | --- |
| 0 | resident power-on patch, loaded while the DSP is held in reset |
| 3 | connection/training dispatcher, requested when preparing Bell 103, V.22, V.32, V.32bis, V.34, or V.90 |
| 4 | baseline/idle image, requested immediately after boot and when returning from data or speakerphone mode |
| 6 | speakerphone DSP image, requested by command `0xa2 00 02 00 01 00` |

The on-hook mode sweep deliberately does not close the DAA relay. It can
identify the common training dispatcher, but modulation-specific overlays are
requested only after signal-level training advances. A V.90 idle-return test
timed out after overlay 3 loaded; no line relay was operated.

An optional passive mode survey can enter and leave the DSP's speakerphone
patch mode without operating the DAA relays:

```sh
sudo insmod ambient_ham_probe.ko load_patch=1 survey_speakerphone=1
```

A modulation can likewise be prepared while the DAA remains on-hook. The mode
number is the index in the original `mode_table` (`0` Bell 103, `6` V.22,
`12` V.32, `16` V.32bis, `18` V.34, `23` V.90, and `24` V.92):

```sh
sudo insmod ambient_ham_probe.ko load_patch=1 survey_mode=18
```

The isolated playground line can be dialled with a bounded 15-second event
observation. The path forces on-hook on success, timeout, probe failure, and
module removal:

```sh
sudo insmod ambient_ham_probe.ko load_patch=1 dial_number=9099
```

Three playground destinations were exercised on `EICON420` on 2026-09-24:

| Number | Source | Result |
| ---: | --- | --- |
| 9099 | echo | dialled and observed for 15 seconds; only the initial call-progress events were reported |
| 5888 | music | dialled and observed for 15 seconds; sustained paired `0xd1` call-progress detector events appeared after answer |
| 3000 | fax answerer | dialled and observed for 15 seconds; one post-answer `0xd1` event pair ended in detector value `0x0037`, with no sustained events |

None of the calls requested another DSP overlay: overlay 4 remained active because
this test enables call-progress detection and DTMF dialing, but does not yet
issue a modem-connect/training command. All tests completed on-hook, unloaded
the module, restored PCI command word `0x0100`, and left the host reachable.

The six-byte payload in each `0xd1` event has not yet been assigned semantic
field names. The recovered host object routes this packet to
`mt_CallProgressCallback`; event pairs bracket detected tone/activity regions,
but interpreting the final word as a specific tone ID requires further
disassembly or controlled-tone tests.

## Inbound training experiment

`answer_mode=N` provides a bounded inbound experiment: it waits on-hook, takes
the line off-hook, sends command `0x68` for mode `N` with its answer-direction
bit set, observes for 30 seconds, then always returns on-hook. The answer bit
is reconstructed from the vendor `mt_connect` path: command byte 6 bit 2 is
packetized from bit 7 of `S14`. For V.32bis mode 16, command byte 4 is `0x09`,
the vendor `translate_to_dsp_line_rate(14400)` result.

Two calls from `/dev/cu.usbmodem8` to the card's playground extension 6314
were run with the caller started before the answer probe. The card seized the
ringing line and requested overlay 3, but generated no training carrier; the
USB modem ended with `NO CARRIER`. Correcting command byte 4 from zero to
`0x09` did not change that result. This establishes that command `0x68` and
overlay 3 select/prepare the training dispatcher, but do not by themselves
start signal-level training. The remaining prerequisite is in the recovered
vendor `system_start`/modulation initialization path (and V.90 additionally
requires its V.8 startup sequence).

Both failed training attempts completed the guarded on-hook/reset teardown,
restored PCI command word `0x0100`, and left `EICON420` reachable.
