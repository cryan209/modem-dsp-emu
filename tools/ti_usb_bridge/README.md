# MT9234ZBA USB bridge for macOS

Native IOKit bridge for MultiTech 06e0:0319 (also recognizes runtime ID
06e0:f115). No kernel extension, Linux VM, or pyserial required.

Build from the repository root:

```sh
make -C tools/ti_usb_bridge
```

The USB controller firmware is loaded into RAM after a cold plug. The tested
`mts_mt9234zba.fw` is available from the linux-firmware mirror:
https://raw.githubusercontent.com/wkennington/linux-firmware/master/mts_mt9234zba.fw

Local tested copy: `artifacts/ti_usb_bridge/mts_mt9234zba.fw` (untracked).
SHA-256: `76fbe7aba462ca4a86a1fbac851f607947e86ad9c34b1a914cc0a90adab211b5`.
No firmware is embedded in the bridge source. The bridge accepts an explicit
firmware path and validates the size, then rebuilds the TI length/checksum header.

```sh
artifacts/ti_usb_bridge/bridge \
  --firmware artifacts/ti_usb_bridge/mts_mt9234zba.fw \
  --pty-link /tmp/mt9234zba
```

Keep the bridge running and open `/tmp/mt9234zba` from a serial terminal.
For example, `screen /tmp/mt9234zba 115200`, then type `AT` and Return.
Do not run two DTE clients simultaneously. The bridge refuses to overwrite an
existing link. After a crashed bridge, verify it is stopped before removing a
stale link. Ctrl-C shuts the bridge down, drops DTR/RTS, closes the TI port,
aborts reads and removes its link.

The TI UART runs at fixed 115200, 8N1, RTS/CTS. PTY baud/line-control changes
are not forwarded. Closing the last PTY client drops hardware DTR/RTS; the
first write from a new client raises them. This depends on the modem's active
DTR policy for hangup. A PTY does not expose real CTS/DSR/RI/CD ioctls.
Interrupt notifications are drained, not surfaced to the terminal. USB bulk
reads preserve all payload bytes, including NUL; partial PTY writes are handled.
USB write errors stop the bridge without retrying possibly transmitted bytes.
Unsolicited output with no terminal client is discarded.

Firmware upload requires a full USB re-enumeration, not just ResetDevice:
macOS otherwise retains the one-endpoint bootloader descriptor. If firmware
was uploaded by an older bridge but descriptors stayed cached, run:

```sh
artifacts/ti_usb_bridge/bridge --reenumerate
```

Then start normally. This refreshes USB enumeration without unplugging power.
Sandboxed tools need permission for USB and PTY access; ordinary Terminal
runs use the current user's IOKit access. Root was not required in live testing.

## Verification, 28 October 2026

On the connected 06e0:0319, firmware upload succeeded; re-enumeration exposed
bulk IN 0x81, bulk OUT 0x01 and interrupt IN 0x83. `AT` returned `OK`.
`ATI` / `ATI3` returned:

```
Agere OCM V.92 MT9234ZBA-USB Data/Fax Modem Version 1.02e
```

`ATM0` returned `OK`. Across a close/reopen, 100 back-to-back `ATI3`
requests produced 100 byte-exact replies in 1.11 seconds. This establishes AT
transport; no call, sustained binary transfer, hot unplug recovery or modem
hangup timing was qualified. The process intentionally exits on USB disconnect;
restart it after reconnecting.

Protocol reference: Linux `drivers/usb/serial/ti_usb_3410_5052.c`, including
firmware framing, big-endian UART configuration, XDATA modem-control register,
and the two-pass open/start/purge sequence:
https://github.com/torvalds/linux/blob/master/drivers/usb/serial/ti_usb_3410_5052.c
The bridge source is GPL-2.0-or-later.

## Measured payload benchmark

`payload_bench.py` runs sequential download/upload of 256 KiB each by default.
It uses different deterministic pseudorandom bodies in the two directions,
compares every byte and verifies SHA-256. The client records download time from
the request through the last body byte; upload time spans the first write
through the answerer's verified acknowledgement. Thus upload results measure
delivery, not only PTY buffer acceptance. Dial/training time is excluded.

Run the same script on the wired emulator host as `server`, with `--emulator`
pointing to the corrected emulator executable, a fresh `--out` directory and
an unused PTY given by `--port`. It uses lab SIP account 2903 by default
(`--number` overrides it), SIP port 5097 and RTP base 15320. The server starts
and stops only its own emulator process. Use a distinct executable name to
avoid unrelated rig cleanup of `sip_v90_modem` processes.

On the Mac:

```sh
python3 tools/ti_usb_bridge/payload_bench.py client \
  --port /tmp/mt9234zba --number 2903 --out artifacts/mt9234-bench-new
```

Use matching `--size` on both ends to change the byte count (maximum 4 MiB).
The current transfer deadlines suit the default 256 KiB; much larger sizes
at low negotiated rates require longer deadlines. Each output directory must
be new. `events.jsonl` holds timed results; client `summary.json` holds payload
rates and hashes, and `ati11.txt` preserves final modem diagnostics. The client
explicitly escapes/hangs up before reading diagnostics. This is a lab endpoint,
not an authenticated general-purpose file server.
