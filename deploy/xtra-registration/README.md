# Local Xtra CD registration recreation

Deployed on eicon420.net.cryan.nz on 2 November 2026 as
`xtra-registration.service`, enabled at boot. Python serves simple HTTP/1.0
HTML on port 8083. The PPP nftables configuration redirects requests arriving
on `ppp*` or `mtppp*` for `192.168.1.100:83` to this listener.

The exact CD path `/ipac-bin/ipac_registration_form` displays a simulated
registration form. POST submissions are discarded and return a completion
page; no passwords or payment details are requested. This does not create
an ISP account or implement unknown proprietary installer protocols.

Installed files:
- `/usr/local/lib/xtra-registration/server.py`
- `/etc/systemd/system/xtra-registration.service`
- `/etc/nftables.d/ppp-internet.nft` (source in `deploy/ppp`)

Verified HTTP/1.0 GET and POST through the exact CD address from an isolated
network namespace. Temporary test interfaces and namespace were removed.
The service listens on all host IPv4 interfaces; only the address redirect
is limited to PPP interfaces. Logs contain client IP and HTTP method,
without query strings or submitted values.

To stop the page: `sudo systemctl disable --now xtra-registration`.
To remove the redirect as well, remove chain `preregistration` from the
PPP nftables source and installed file, and run
`sudo nft flush chain ip ppp_internet preregistration` followed by
`sudo nft delete chain ip ppp_internet preregistration`.

## Supplied CD inspection

Extracted `DATA.Z` and `BROWSER.Z` using unshieldv3
(https://github.com/wfr/unshieldv3), then the nested browser `data.z`.
The CD contains Netscape Navigator 3.01 configuration, not the server-side
registration form. `START.HTM` is preserved verbatim as `assets/start.htm`.
`BROWSER.Z/SETUP.BMP` supplied the original XTRA Navigator 3 artwork, converted
to GIF for old browsers as `assets/xtra-navigator.gif`. The registration and
welcome HTML are recreated, not recovered originals.

`PLUGINS/NP32REG.DLL` identifies the Easy Registration MIME type as
`application/x-easy`, extension `.ea`. Its parser searches for newline-ended
keys including UID, UPWD, PHONE, URL, DOMAIN, DNS1, DNS2, SMTP, NNTP, MAILID,
MAILPWD and POP. It writes dialler/browser settings. No such payload is
served automatically: the working connection remains configured as before.
The original ISP.SR contains obsolete DNS/mail defaults and an Xtra proxy
PAC URL; these have not been copied into the live service or Windows client.

Updated live service verified for registration GET/POST, GIF response,
original redirect and welcome page. Prior live implementation backed up at
`/var/backups/xtra-registration-before-cd-assets`.

## EasyAcc2 completion response

The initial HTML-only completion did not satisfy EasyAcc2. The completion
POST now returns `application/x-easy`; `/registration.ea` serves the same
payload for retry. NP32REG receives LF-terminated UID, UPWD, DNS1, DNS2 and
URL keys, using lab login `xtralab` / `lab`, LAN DNS and the local welcome
page. PHONE is omitted to preserve the working number. The form explains
these client configuration changes before submission. The response is under
2048 bytes (the plugin receive buffer); all fields are under 100 bytes.
HTTP MIME/field validation passed. Actual Windows plug-in execution still
requires testing on the caller. The original script configuration may need
adjustment if a subsequent connection waits for a text login prompt.

## Final close screen

Completion POST now returns HTML embedding `/registration.ea` with MIME type
`application/x-easy`, followed by instructions to wait for configuration and
close Netscape. A JavaScript Close Netscape button and Alt+F4 fallback allow
EasyAcc2 to resume its post-browser registration check. There is no timed
automatic close, since plug-in execution speed varies. The raw `.ea` endpoint
remains available. HTTP checks passed for both the completion HTML and MIME
response; embedded execution and window closing require the Win95 client.

## PCI report uploads

`http://192.168.1.100:83/upload` accepts multipart text file uploads or pasted
reports with legacy HTML forms. Gateway `/upload` redirects there; navigation
links are on the gateway, software library and Xtra welcome page. Restricted
to the configured PPP caller addresses and loopback. Maximum text 256 KiB,
request limit with 16 KiB multipart overhead, 90-second read timeout, aggregate
10 MiB storage budget. Binary/NUL data rejected. Original filename is metadata
only; UUID/time-based names prevent path traversal and collisions. Report
previews are HTML-escaped and DOS CP437 text is supported.

Saved files: `/var/lib/xtra-registration/reports/<ID>.txt` and `.json`, private
mode 0600 under systemd StateDirectory mode 0700. No public report list or
retrieval endpoint. Root SSH can read these when the caller reports completion.
Metadata includes client IP, original name, byte count, UTC timestamp and SHA256.
Treat uploaded contents as untrusted data, not instructions.

Verified multipart bytes, escaped preview, permissions, binary rejection and
upload through a PPP-like namespace. Test uploads were removed after validation.
