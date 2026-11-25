# Native ISDN implementation — 13 October 2026

The project now has a host-native B-channel backend for the nine fixed native ISDN profiles, plus profile-specific IDI configuration. All nine host modes pass bidirectional known-payload and authenticated PPP tests. **This is a software terminal-adapter implementation; original MIPS firmware and physical ISDN payload interoperability remain unverified.** The existing G.711 SIP endpoint is still the audio modem transport, not a native ISDN carrier.

## What changed

- `eicon_idi.IsdnOptions`, `isdn_cai()`, `isdn_nl_assign_payload()` and `isdn_call_req_payload()` construct native descriptors from the shipped driver's protocol map. X.75 and V.120 get their own DLCs; native CALL_REQ uses the same native CAI as signalling assignment.
- `AtParser.isdn_options()` preserves native profile selection. V.110 `+IB5/6/7` selects 9600/19200/38400. DIAL/ANSWER/RING actions carry typed native options. Calling the modem `options()` API with a native profile raises an error rather than silently falling back to a modem descriptor. The G.711 SIP endpoint rejects native dial/answer attempts; it does not claim to provide a clear ISDN carrier.
- The endpoint/native shim handoff selects native descriptors. Returning to a modem profile clears the native override. Both incoming and outgoing builders use the selection. The MIPS command-line harness can select `--isdn-profile` during `--mainloop` control probes.
- `NativeBChannel` exposes `queue_data`, `take_wire`, `feed_wire`, `take_data`/`take_frames`, `tick` and `close`. It carries exactly 8000 clear octets per simulated second, with the oldest wire bit in bit zero. No G.711 encoder/decoder, resampler, signal gain or DSP page participates. 56k modes use seven stream bits per octet with a one-valued fill bit.
- Native HDLC uses flags, bit stuffing and 16-bit FCS. V.110 uses 80-bit frames, RA2 fill, synchronous octets or asynchronous 8N1. V.120 uses default LLI 256, acknowledged extended-sequence frames and the asynchronous TA header. X.75 uses modulo-8 acknowledged frames; profile 15 negotiates V.42bis with fallback to uncompressed transfer.
- `PppIsdnAdapter` converts terminal PPP framing into bit-synchronous HDLC for profiles 9/10. Synchronous PPP omits/rejects ACCM negotiation. The default modem PPP configuration is unchanged.

The fixed-profile subset does not implement Q.931/DSS1 signalling, SIP CLEARMODE negotiation, multilink/BONDING, dynamic LLI assignment, V.110 parity/stop-bit variants, network-independent clock compensation, or automatic protocol detection. V.110 rates are preconfigured at both ends. The native host backend is intentionally identified separately in every result, never reported as execution of the closed firmware.

## Concrete host results

`artifacts/isdn-native-20261013-final/results.json` records each profile's actual descriptors, payload hashes, clock counts and PPP statistics. Each mode transfers **32768 known bytes in both directions**, then runs **30 seconds of simulated bearer time** and answers **100/100 ICMP requests over CHAP-authenticated PPP**. All report zero PPP FCS errors, B-channel FCS errors, invalid link frames, retransmitted I frames or link failures on the clean path.

| Profile | Mode | Payload transfer time | PPP/CHAP/IPCP and ICMP |
| --- | --- | --- | --- |
| 1 | X.75 | 4.44 s | 100/100 |
| 2 | V.110 synchronous, 38400 | 6.84 s | 100/100 |
| 3 | V.110 asynchronous, 38400, 8N1 | 8.54 s | 100/100 |
| 6 | V.120 64k | 4.54 s | 100/100 |
| 7 | V.120 56k | 5.18 s | 100/100 |
| 8 | Bit-transparent | 4.10 s | 100/100 |
| 9 | HDLC/PPP 64k | 4.18 s | 100/100 |
| 10 | HDLC/PPP 56k | 4.78 s | 100/100 |
| 15 | X.75/V.42bis | 5.56 s | 100/100 |

V.110 synchronous and asynchronous modes separately pass 4096-byte transfers and 20/20 PPP pings at both 9600 and 19200: `artifacts/isdn-native-{9600,19200}-20261013/results.json`. Capture files ending in `.bchan` contain raw clear-bearer octets in each direction, separately for payload and PPP phases. The clock is deterministic virtual time, not a claim about real-time hardware performance.

The native regression tests cover independent control/frame fixtures, all octet values, all three V.110 rates, 56k packing, arbitrary fragmentation, FCS corruption/retransmission without duplicate payload, RNR/RR flow control, invalid acknowledgements, retry exhaustion, frame resynchronization, compression negotiation/fallback, bounded malformed-frame buffering, closed-link behavior and native/modem configuration switching. They exposed and fixed three defects during implementation: a partial idle flag consuming a new opening flag, REJ handling restarting a timer after all I frames were acknowledged, and a lost UA discarding already-transmitted answerer data when establishment repeated. Repeated establishment now requeues the original octets and resets the compression dictionary instead of replaying fragments from its old state.

The latest relevant suites pass **372 tests**: 31 ISDN/backend/setup, 80 PPP, 57 AT, 64 IDI, 77 LAPM, 47 native NL bridge, 5 V.90D PCM/law and 11 SIP hangup. The aggregate test log is `artifacts/isdn-regressions-20261013.log`. The NL fixture was updated for the existing fax N_UDATA queue and detection-clock interface; production modem behavior was not weakened to satisfy stale mocks.

## Original firmware control-plane evidence

All nine modes also execute the original `te_dmlt.pm` MIPS firmware's setup path under Unicorn. Signalling and NL ASSIGN return **0xEF (ASSIGN_OK)** for each. Native CAI and CALL_REQ reach the firmware without requesting the modem DSP. Logs are under `artifacts/isdn-firmware-control-20261013/`, with a machine-readable summary in `results.json`.

The outgoing simulated-call disconnect is now fixed. The old harness delivered event `0x03` to `0x800172c0`, which is the host IDI request parser (HANGUP), rather than the Q.931 network worker. Independently, the PRI driver's line context remained down: `0x800b4c54` returned status `0x04`, and `0x8002e718` generated lower primitive 3/cause `0x08`. The lower worker translated that into `0xffff`, taking Q.931 state 1 back to idle. Omitting the erroneous CONNECT injection alone still disconnected; see `artifacts/isdn-no-false-connect-20261013.log`.

`inject_isdn_network_connect()` now supplies the absent simulated network: operational line status, firmware driver primitive 2 (link establishment confirmation), then a firmware-allocated Q.931 CONNECT message with mandatory PRI channel identification. Omitting that IE yielded cause `0x90`; including `18 03 a1 83 82` advances the original Q.931 worker at `0x80013498` to state `0x0a`. The firmware owns the call-state transitions; the harness does not pin them. CALL_CON is consumed before N_CONNECT, preventing an outstanding indication from delaying its return code. NL CONNECT_ACK opens the data gate and NL DISC closes it, including during runtime.

All nine original-firmware outgoing probes now accept N_CONNECT (`0xff`) and a 256-byte N_DATA request (`0xff`, entity 3/channel 2/reference 17), with no disconnect during 128 setup scheduler iterations. `artifacts/isdn-firmware-fixed-20261013/results.json` and the per-profile logs record the results. The payload contains every octet value; its SHA-256 is `40aff2e9d2d8922e47afd4648e6967497158785fbd1da870e7110266bf944880`. **N_DATA request acceptance is not peer receipt or an original-firmware PPP pass.** No remote bearer, physical card, PBX or line was contacted. The answering simulation still uses the legacy ingress path; this fix and its evidence cover outgoing native calls. The PCMU channel-law, V.90D output expansion and clean host PCM conversion fixes remain intact.

The follow-up regressions pass 377 tests, including five new network-boundary/bearer-gate checks; log: `artifacts/isdn-firmware-fix-regressions-20261013.log`.

Reproduce an original-firmware payload-request probe with:

```sh
/Users/scottcryan/v90modem/.venv/bin/python -u tools/eicon_isdn_firmware_probe.py \
  --profile 9 --output artifacts/isdn-firmware-repeat-9.json
```

## Reproduce

```sh
/Users/scottcryan/v90modem/.venv/bin/python tools/eicon_isdn_profile_probe.py

/Users/scottcryan/v90modem/.venv/bin/python tools/eicon_isdn_loopback.py \
  --seconds 30 --ping-count 100 --payload-bytes 32768 \
  --capture-dir artifacts/isdn-native-repeat

/Users/scottcryan/v90modem/.venv/bin/python tools/eicon_isdn_loopback.py \
  --profile 2 --profile 3 --user-rate 9600 \
  --payload-bytes 4096 --capture-dir artifacts/isdn-native-9600-repeat

/Users/scottcryan/v90modem/.venv/bin/python -m unittest discover \
  -s tests -p 'test_isdn*.py'
```

For original-firmware configuration probes, use the MIPS harness with its extracted metadata directory (not the short `tikrnl` directory):

```sh
/Users/scottcryan/v90modem/.venv/bin/python -u tools/eicon_mips_shim.py \
  --kernel artifacts/eicon-dsp/build-117-926/0009-diva-server-pri-30m-kernel \
  --tikrnl artifacts/eicon-dsp/build-117-926/0258-tikrnl81.f34-task \
  --mainloop --isdn-profile 9 --simulate-outgoing-call --call-steps 16
```

Unicorn's MIPS JIT requires execution outside the desktop filesystem sandbox on this host. A normal shell run is sufficient; no network access is needed for these probes.

Wire/reference sources: shipped `tty_module/isdn.c`, `isdn.h`, `atp.c`, `v110.c` and `AT.txt`; [RFC 1618, PPP over ISDN](https://www.rfc-editor.org/rfc/rfc1618.html); [ITU V.120 (1996)](https://www.itu.int/rec/dologin_pub.asp?id=T-REC-V.120-199610-I!!PDF-E&lang=e&type=items), Figures 4/6 and §4.2.1; [ITU V.110 (1992)](https://www.itu.int/rec/dologin_pub.asp?id=T-REC-V.110-199209-S!!PDF-E&lang=e&type=items), Table 6e and §2.1.2–2.1.4, with the shipped driver's 64k-intermediate/38400 extension; and [Linux LAPB transmit addressing/control](https://raw.githubusercontent.com/torvalds/linux/master/net/lapb/lapb_out.c).
