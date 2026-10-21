# Diva SIP gateway to the MultiTech BRI, 22 October 2026

`tools/diva_sip_gateway.py` connects eicon420's Diva (SN 1331, logical
controller 3, NT to the MultiTech) to Asterisk as extension **2910**, so
SIP callers can reach the MultiTech's ISDN side and the MultiTech can call
Asterisk extensions.

## How it works

- It uses CAPI 2.0 directly on divacapi's `/dev/capi20`, so no libcapi20,
  chan_capi or local Asterisk is needed (Debian 13 packages none of them).
  Each `write()` is one message (DATA_B3_REQ followed by its payload). Each
  `read()` returns one message, with a DATA_B3_IND payload at the offset in
  its length field (`kernel/capimain.c`, `kernel/capifunc.c`).
- B channel: B1/B2/B3 transparent, 64 kbit/s. Calls toward ISDN use CIP 4
  (3.1 kHz audio), which the MultiTech's Lucent modem endpoints (ttyM4–7)
  answer. Its MSNs are empty, so it should accept any called number.
- Audio is passed byte-for-byte as PCMA and is never transcoded. The only
  change is CAPI's LSB-first bit order (`--no-bit-reverse` turns that off).
  The Asterisk endpoint is `endpoint-modem-alaw` (`clearmode,alaw`) for the
  same reason as the other modem extensions. A µ-law caller such as 6001 is
  transcoded by Asterisk, which is fine for V.34 but will hurt V.90.
- Line clocking: transmit to the line is paced by receive from it. Each
  octet the card delivers is answered with one octet from a 40 ms-prefill
  jitter buffer, so Asterisk's clock drift is absorbed there. Each call logs
  its underruns, overflows and the card's DATA_B3_IND block sizes at hangup.
  The block sizes are not yet known for this card.
- Incoming ISDN calls get ALERT_REQ immediately, so the caller's 4 s T303
  doesn't expire during SIP setup.

## Routing

| Direction | How |
|---|---|
| SIP → ISDN | Dial `2910` for the gateway's `--isdn-number` (empty by default), or `2910<digits>` to send `<digits>` as the called number (`Dial(PJSIP/${EXTEN:4}@2910)`). |
| ISDN → SIP | The called number becomes the Request-URI user in context `default`, so `ATD6001` on the MultiTech calls 6001. The calling number becomes the From user. |

## Deployment

- eicon420: `/usr/local/sbin/diva_sip_gateway.py`, run by
  `diva-sip-gateway.service` (enabled; after `divas4linux`). The password is
  in `/etc/diva-sip-gateway/password` (root, 0600), not on the command line.
  SIP uses UDP 5060 and RTP uses UDP 40000–40198 on 10.150.150.157.
- Asterisk (`root@asterisk.net.cryan.nz`, 192.168.88.122): endpoint, auth and
  aor `2910` in `pjsip.conf`; `2910` and `_2910X.` in `[default]` of
  `extensions.conf`. Both files were backed up as `*.before-diva2910-<ts>`.
- It coexists with `bri-test-answerer`, which uses controllers 1/2 via Divatty.

## Verified

- 12 unit tests (`tests/test_diva_sip_gateway.py`) cover the full call flow
  in both directions against a fake CAPI device and fake Asterisk. That
  includes digest auth, ALERTING → 180, B3 setup, bit-reversed media both
  ways, BYE and busy mapping.
- Live: registration works and Asterisk qualifies the contact (`Avail`,
  0.6 ms). LISTEN on controller 3 is accepted.
- Live: `channel originate Local/29107951@default` gives CONNECT_REQ on
  ctl 3. The D channel shows the Diva's broadcast SETUP:
  `02 FF 03 08 01 08 05 A1 04 03 90 90 A3 18 01 89 1E 02 80 83 70 05 80 37 39 35 31`
  (3.1 kHz A-law bearer, B1, called 7951). Nothing comes back. The call
  clears after 4.0 s (T303) with reason 0x3490 and goes to SIP as 480.

## Not yet working, and why

No call has connected. The MultiTech gets the SETUP, but its replies don't
reach SN 1331. This is the same failure as in
`docs/eicon420_bri_hardware_20261021.md`: every BRI lead is crossover with no
100 ohm termination. Once the resistors are fitted, the next checks are:

1. `channel originate Local/2910@default application Wait 60`: expect
   ALERTING (180), then a modem answer on ttyM4–7 if one has `ATS0=1`.
2. Note the logged B-channel block sizes and underruns from the first
   connected call. Pacing assumes the card delivers regular small blocks.
3. Check the bit order on a real call: a 1 kHz tone from Asterisk should
   arrive as a tone. Use `--no-bit-reverse` if it arrives as noise.
4. Whether a passive-side transparent call gets CONNECT_B3_IND. If it
   doesn't, the gateway sends its own CONNECT_B3_REQ after 300 ms.
