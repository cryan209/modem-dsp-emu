# V.34 fax interop profile

`v34-fax-live` selects the Eicon firmware's Group 3 fax protocol row.  It is
not the `v34-live` data-modem profile with a different maximum rate:

- `EICON_FAX=1` sets B1 to `B1_T30` and the fax B2/B3 protocols.
- `EICON_FAX_OPERATING_MODE=2` selects the card's Class 1 terminal interface,
  matching `efax` in the sibling `fax-work` project.
- `EICON_FAX_CONTROL_BITS=0x1002` enables ECM (`0x0002`) and Super G3 / V.34
  fax (`0x1000`).

Start the SIP endpoint with:

```sh
./run v34-fax-live -e EICON_FAX_STATION_ID='EICON V34 TEST'
```

It prints a Class 1 modem PTY on startup. Attach `efax` directly to that path
while bringing up the call with the normal `ATD`/`ATA` terminal sequence.

The profile deliberately drops the V.90 PRBS and V.90 state-trace switches
inherited by the data profile.  Those are data-modem diagnostics and do not
provide the T.30 host interface that a fax call needs.

## Current boundary

This selects and boots the firmware's real fax path. The interactive AT parser
supports `AT+FCLASS` selection plus the Class 1 media commands `+FTM`, `+FRM`,
`+FTH`, and `+FRH`: transmit data is unescaped from the terminal's DLE form and
emitted as a `FAX_SEND` event; receive data is DLE-escaped and ends with the
required DLE ETX/result code sequence.

The native adapter translates those events into the firmware's `N_UDATA`
reconfiguration requests and `N_DATA` frame transfers, and feeds Class 1
`N_DATA` indications back through `fax_receive()`. A TCP serial listener is
still needed for the Docker `fax-work` harness; the supplied PTY is already
sufficient for a direct local `efax` interop run.
