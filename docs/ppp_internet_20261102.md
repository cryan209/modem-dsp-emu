# Eicon420 PPP internet access, 2 November 2026

A Windows 95 caller on 7910 authenticated and completed IPCP, but the
host had `net.ipv4.ip_forward=0`, an empty nftables ruleset, and no active
`ms-dns` option. The earlier deployment provided only host-to-client IP.

Installed on eicon420.net.cryan.nz:

- `/etc/sysctl.d/70-ppp-internet.conf`: `net.ipv4.ip_forward=1`, also applied live.
- `/etc/nftables.d/ppp-internet.nft`: source NAT through `eno1` for Eicon
  client addresses 10.254.91.2 through 10.254.94.2 and MultiTech addresses
  10.254.104.2 and 10.254.104.6. Source: `deploy/ppp/ppp-internet.nft`.
- `/etc/nftables.conf`: includes that file; nftables enabled at boot.
- `/etc/ppp/options`: `ms-dns 10.150.150.1`, the verified LAN resolver.

Original PPP options and nftables configuration are backed up under
`/var/backups/ppp-internet-20261102`. No modem services were restarted.
Existing sessions get forwarding immediately; DNS is negotiated on reconnect.

Validation: nftables syntax check and pppd dry run passed. A temporary
network namespace using source 10.254.94.2 successfully forwarded 3/3
pings to 1.1.1.1, resolved example.com through 10.150.150.1, and received
an HTTP response from 1.1.1.1. The NAT counter incremented. Removed the
test namespace and veth afterward. This verifies host routing and NAT;
Windows 95 end-to-end testing remains with the caller.
