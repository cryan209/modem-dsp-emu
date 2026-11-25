# Direct PPP on MultiTech 7910, 24 October 2026

25 October update: answerer chat initialization now uses
`ATE0V1S0=1S109=1`, enabling both V.90 and K56flex. The intervening
K56-only experimental setting S109=0 prevented V.90. RASFinder .33
WAN1 has now twice dialled this BRI with its stock automatic V.90
profile and achieved 31200 upstream / 46666 downstream with verified
bidirectional raw data. Details are in `rasfinder_fxs_20261025.md`.
This update does not alter PPP authentication or routing settings.

MultiTech's first modem (`/dev/ttyM4`, first BRI on Cisco 0/0/1) now has a
persistent PPP answer service. Dial **7910** and start PPP immediately after
CONNECT. There is no test menu or terminal login on this endpoint.

| Setting | Value |
|---|---|
| Service | `multitech-ppp.service`, active and enabled at boot |
| Answering device | `/dev/ttyM4`, 115200, hardware flow control |
| Server interface | `mtppp0` while a PPP session is connected |
| Server address | `10.254.104.1` |
| Assigned client address | `10.254.104.2` |
| PPP authentication | None, matching the existing isolated Eicon PPP lab link |
| Routing | Point-to-point link to eicon420; no forwarding, NAT, DNS or default-route changes |

The daemon initializes the modem's existing autoanswer behavior, waits for
CONNECT, then negotiates PPP LCP/IPCP directly. It persists across hangup
and rearms automatically. This deployment covers one concurrent session on
ttyM4; other MultiTech endpoints were not added to the service.

The Eicon answerer at **7900** retains its existing menu; option **7** starts
PPP there. This deployment changes only the MultiTech answering endpoint.

## Installed files and sources

| Repository source | Installed on eicon420 |
|---|---|
| `deploy/ppp/multitech-7910` | `/etc/ppp/peers/multitech-7910` |
| `deploy/ppp/multitech-answer` | `/etc/chatscripts/multitech-answer` |
| `deploy/ppp/multitech-ppp.service` | `/etc/systemd/system/multitech-ppp.service` |

Peer/chat files are root-owned mode 0600; the unit is root-owned mode 0644.
`pppd dryrun call multitech-7910` accepted the options. Unit verification
reported only an existing warning about divas4linux's unrelated unit file.
The isicom driver already loads via `/etc/modules-load.d/isicom.conf`;
the new service starts after systemd-modules-load. Boot enablement was
verified, but a reboot was not performed.

Operational commands on eicon420:

```sh
sudo systemctl status multitech-ppp
sudo journalctl -u multitech-ppp
sudo systemctl stop multitech-ppp
sudo systemctl start multitech-ppp
```

Stop this service before future raw-serial diagnostics on ttyM4, since it
owns that device continuously while waiting for calls. Restart it afterward.

## Verification

Eicon controller 2/ttyds6 dialled 7910 with V.90A automatic selection.
The direct call used 31,200 TX / 56,000 RX at Eicon. A real Linux pppd
client ran in a separate network namespace (`mt-ppp-test`), ensuring IP
traffic could not bypass the serial PPP link through local host routing.
No menu input was sent.

- LCP and IPCP completed, assigning client 10.254.104.2 and server
  10.254.104.1, MTU 1500.
- Ten 256-byte ICMP payloads each direction: 20/20 replies, zero loss.
- TCP upload 16,384 bytes, exact deterministic payload match:
  SHA256 `b507d9de5e1aef09bdce2093be485fd1f652fbed746f7120e126122060def5bb`.
- TCP download 16,384 bytes, exact deterministic payload match:
  SHA256 `fb8a4c0d4a248d708328a42f63170ee569d18e6bcec8451e9e10ffd0da54ac3a`.
- Cleanly terminated the client; the server rearmed. There was one
  transient chat initialization timeout after hangup, handled by its
  existing persist/holdoff retry without intervention.
- A second direct PPP call completed LCP/IPCP and passed 5/5 pings,
  confirming reconnect behavior. Client termination used LCP TermReq/Ack.
- Removed both test clients and the temporary network namespace. The
  server remains active/enabled and waiting for the next call; the Eicon
  answerer and SIP gateway remain active. Host default/LAN routes unchanged.

Temporary test client files on eicon420:
`/var/tmp/eicon-to-multitech.peer` and `/var/tmp/eicon-to-multitech.chat`.
They use linkname `multitech-ppp-test` and a 180-second maximum connection
time; those limits apply only to the test client, not the installed server.
