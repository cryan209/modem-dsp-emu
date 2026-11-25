# Private retro ISP services

Deployed on eicon420 (10.150.150.157), reachable from routed LAN/VPN and
PPP clients. Gateway navigation links to these services; /status, /irc,
/homepages and /~name redirects also work through the virtual PPP gateway.
No public DNS changes or router port forwards were created.

- http://10.150.150.157:8086/status: live service checks, active modem
  members and notices from /etc/retro-isp/notices.json. HTML 3.2, no JS.
- IRC 10.150.150.157:6667: packaged ngIRCd 27, channels #xtra/#helpdesk,
  no server links, ident/DNS/PAM disabled, custom MOTD, no password needed.
- http://10.150.150.157:8086/homepages and /~snuggi/: static web space.
- FTP 10.150.150.157:21, passive ports 50000–50009, active also tested.
  Virtual snuggi login; generated credential delivered separately, not in
  this repository. Scrypt hashes in /etc/retro-isp/ftp-users.json (0640,
  root:retro-ftp). No anonymous login. FTP root is that user's public web
  directory, /srv/retro-isp/users/NAME. Uploaded files publish immediately.

Services: retro-isp-web.service (DynamicUser, read-only filesystem),
retro-ftp.service (dedicated no-shell system user, write access only to
personal content, CAP_NET_BIND_SERVICE for port 21), and ngircd.service
(irc system user). Source under /usr/local/lib/retro-isp. All enabled.
Nftables /etc/nftables.d/retro-isp.nft is included by /etc/nftables.conf;
these ports accept only RFC1918/loopback sources. Existing PPP firewall
and services were preserved. Explicit service host IP is used as requested.

Add another homepage/FTP account:

    sudo python3 /usr/local/lib/retro-isp/add-user.py username

Tool generates a password and prints it once, or accepts --password-stdin.
It refuses to overwrite an existing account. Share the generated password
privately. FTP reloads accounts on each new connection; a restart is not needed.
Accounts have separate virtual roots; personal sites have no CGI/PHP or
server execution. Content is visible to other private-network users.

Validation: HTTP index/status/IRC/homepage, wrong-password rejection,
passive/active FTP upload+retrieval, immediate HTTP publication, FTP root
confinement, IRC registration and private test-message exchange. Repeated
through an isolated PPP-like 10.254.94.2 namespace, including transparent
192.168.1.100/status redirect; namespace/veth removed. Test uploads removed.
Unit checks cover URL traversal, encoded traversal, symlinks, hidden files
and HTML escaping. IRC configtest, persistent nft syntax and live service
health passed. Browser rendering in Netscape/IE4 has not been inspected.

Backups: /var/backups/retro-isp-20261104-233555 and
/var/backups/retro-portal-links-20261104-233841. The welcome server source
also has new links, but its restart was deferred to avoid interrupting
existing software downloads. Gateway links are live now.

Unified signup now provisions PPP/mail/FTP/homepage and billing together at
/signup; see ../isp-registration/README.md. Existing snuggi remains untouched.

## HTML IRC client

`irc_web.py` adds a JavaScript-free HTML 3.2 client at
`http://10.150.150.157:8088/`. The existing `/irc` page links to it.
Users choose a nickname and #xtra or #helpdesk, refresh messages manually,
send plain text and disconnect. It connects only to the existing private
IRC server, never a caller-selected server. No IRC commands are accepted
from message input. Transcript is capped at 100 events per session, in
memory only. Six concurrent sessions total / two per caller IP, with a
15-minute idle timeout. ngIRCd sees bridge connections from the ISP host.

A separate HTTP listener keeps the client away from uploaded homepage
scripts. Opaque HttpOnly cookies, address-bound sessions, form tokens,
Origin checks when supplied, HTML escaping and frame denial protect chat
actions. Cookies are required. No passwords or account login needed.
No chat/request archive is written. Restarting the bridge disconnects only
web chat sessions. Native IRC clients and modem calls are unaffected.

Deployment (root on eicon420, from repository files):

    install -m 0644 irc_web.py /usr/local/lib/retro-isp/irc_web.py
    install -m 0644 retro-irc-web.service /etc/systemd/system/retro-irc-web.service

`request_origin.py` from ../isp-registration must already be importable in
`/usr/local/lib/retro-isp`, as required by the existing web service. Back up
live web.py and the retro-ISP nftables include before replacing them with
these updated versions. Check persistent firewall syntax with `nft -c -f
/etc/nftables.conf`, then apply only the private-services port addition
(8088) without flushing existing PPP rules. Reload systemd, enable/start
retro-irc-web, and restart retro-isp-web to expose the new link. Verify
HTTP join/send/receive against ngIRCd from LAN and a PPP caller; confirm
port 8088 remains unavailable from non-private sources. No gateway restart
is required because its existing IRC link reaches `/irc`.

Local verification: `python3 -m unittest discover -s tests -p test_irc_web.py`
uses a real loopback HTTP client/cookie jar and a simulated IRC socket to
check registration/join, PING/PONG, incoming HTML escaping, outgoing message,
CSRF rejection, command-injection rejection and idle cleanup. Existing
personal-web tests also pass. Actual old-browser rendering remains untested.
Deployed on 5 November 2026 using scottcryan with sudo. Live HTTP form,
cookies, ngIRCd registration/channel join, received transcript, disconnect
and portal link passed. retro-irc-web is active and enabled at boot;
retro-isp-web and ngIRCd remain active. No chat messages were sent during
live verification. Backup: /var/backups/retro-irc-20261105-131842.
Only the retro_isp firewall table was replaced; PPP rules were preserved.

Browser form fix: changed IRC Referrer-Policy from no-referrer to same-origin.
Chromium sends a null Origin for these POSTs under no-referrer, triggering
the intended Origin rejection. Browser join, transcript refresh and disconnect
now pass; cross-origin/null-origin rejection remains enabled.
