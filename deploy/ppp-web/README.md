# Transparent PPP web gateway

Installed on eicon420.net.cryan.nz, 2 November 2026. Enabled at boot as
`ppp-web.service`. Python environment `/opt/ppp-web` contains mitmproxy 12.3.1;
Pillow is provided by Debian python3-pil. Source addon installed at
`/usr/local/lib/ppp-web/addon.py`.

PPP traffic arriving on `ppp*` / `mtppp*` for TCP 80 or 443 is redirected
to 8084 by `deploy/ppp/ppp-internet.nft`. The existing port 83 registration
redirect is preserved. The proxy rejects callers outside the six configured
PPP client addresses. No browser proxy settings are required.

Plain HTTP requests for public websites on port 80 are fetched using HTTPS,
with upstream certificate validation enabled. HTML/CSS links, redirects and
Secure cookies are adapted to HTTP for the old browser. Responses use
HTTP/1.0 without chunked encoding for legacy HTTP callers. Consequently the
PPP leg is plaintext for this mode; the gateway is the HTTPS endpoint.
HTTP-only public sites may fail because automatic upstream upgrade assumes
HTTPS support. Private addresses and nonstandard HTTP ports are not upgraded.

Native HTTPS is intercepted too. A compatible browser must trust the local
CA, offered at `http://192.168.1.100/ca.cer`. The gateway index is at
`http://192.168.1.100/`. CA private material stays under `/var/lib/ppp-web`
owned by the systemd service. Minimum client TLS is TLS 1.0; Netscape 3's
SSLv2/SSLv3 protocols are not supported by this modern TLS stack. For it,
enter `http://` and let the gateway handle upstream HTTPS. This setup does
not implement modern JavaScript or browser layout features.

Images up to 12 MiB are resized to at most 1024x768. Opaque images use
JPEG quality 55; images with transparency use GIF. Animated images are
preserved. Unsupported WebP/AVIF/PNG are converted when Pillow can decode
them, even when the conversion increases size; otherwise only smaller
representations are used. SVG and unsupported formats pass through.
Responses larger than 12 MiB stream without modification. No flow archive
is recorded and ordinary request logging is disabled.

Validation used an isolated namespace with client source 10.254.94.2 and
no proxy environment/configuration: HTTP/1.0 website fetch, automatic
upstream HTTPS, native HTTPS with CA verification, and image conversion all
passed. A real JPEG changed from 35,588 to 12,484 bytes (~65% reduction)
and decoded correctly. Temporary network namespace and interfaces removed.
Live persistent nft syntax checked before activation. Actual Netscape
rendering and certificate import remain client-side checks.

To bypass the gateway, remove just the TCP 80/443 redirect from the live
`preregistration` chain and `/etc/nftables.d/ppp-internet.nft`, then stop
`ppp-web`. Preserve the port 83 rule and postrouting NAT.
Prior nft source: `/var/backups/ppp-internet-before-web.nft`.

Reference: https://docs.mitmproxy.org/stable/howto-transparent/
and https://docs.mitmproxy.org/stable/concepts/options/.

## Connection statistics

`http://192.168.1.100/stats` is a Netscape-compatible, server-rendered stats
page refreshing every 15 seconds; `/stats.json` supplies the same counters.
Linked from the gateway index and Xtra welcome page. Shows active IPv4 PPP
interfaces, caller IP, elapsed connection time, bytes, sampled rates, errors
and drops. Host TX is caller download and host RX is caller upload.
`/etc/ppp/ip-up.d/90-connection-stats` records timestamp and ifindex under
`/run/ppp-stats`; interface identity prevents stale start times after reconnect.
Calls predating the hook show observation time instead. Web request/failure
and transformed-image byte totals start at proxy startup; no browsing history
or account names are recorded. Rates require at least two samples a second
apart. Stats source installed beside the addon as `stats.py`.

Verified HTML and JSON through transparent routing, actual live mtppp0
session counters/start time, and increasing image-compression totals after
a real image fetch. The caller reconnected during testing and the start-time
hook correctly recorded the new interface identity and session time.

## Modem connect rates

The PPP start hook now records initial CONNECT result text. Eicon results
are read from the answerer's existing CONNECT journal entry for the tty:
TX is caller download, RX caller upload. MultiTech uses `modem-connect`
to give chat a separate root-only report per tty; REPORT CONNECT plus an
explicit newline wait captures the complete result without consuming PPP
payload. Shared public session JSON contains only the result and parsed
rates, never credentials. A single MultiTech rate is displayed as reported,
without guessing which direction it represents. Serial port speed is not
presented as the line rate; retrains are not monitored.

The active MultiTech service reload is deferred until hangup via a one-shot
marker and `/etc/ppp/ip-down.d/91-reload-modem-report`. Idle second-channel
service restarted immediately. Existing calls have no saved CONNECT result
and show unavailable until reconnect. Validated chat using a simulated PTY
modem (CONNECT 31200 V42bis), both pppd peer dry runs, and dashboard rendering
for separate TX/RX values. No active serial port was queried or interrupted.

## Simplified page mode

Gateway index offers POST buttons for Simplify pages and Normal pages.
Preference is per PPP caller IP, stored atomically in the service's private
`page-modes.json` under its confdir. Default is normal. The same assigned IP
on a later call retains that choice. Gateway/stat/certificate pages remain
unchanged. No browser proxy configuration is required.

`simplify.py` uses Beautiful Soup to rebuild static HTML with basic tags,
no scripts, CSS, event handlers, embedded objects or modern layout elements.
Resolves links/form actions/images against the original base URL, preserves
form field names/values/methods and converts unsupported input types to text.
Lazy image URLs are promoted when available. Outputs ISO-8859-1 with numeric
entities for other characters. HTTP clients retain the existing gateway's
HTTP link rewriting. Simplified responses are no-store to allow mode changes.
Client-side cached pages may still require Reload after switching.

This stage does not run JavaScript, render a modern browser, preserve every
interactive form behavior, or bypass site challenges. JavaScript-only content
will remain missing. Normal mode restores the previous handling.

Validation covered removal of scripts/styles/handlers, base-relative links,
POST form fields (including escaped values), lazy images, text encoding and
HTTPS links for native HTTPS callers. Isolated transparent HTTP/1.0 client
successfully toggled mode, fetched a simplified live website, then restored
normal mode. Actual caller preference was not changed by testing.

## SVG rasterization

External `image/svg+xml` responses become JPEGs in both page modes. Inline
SVG and SVG data images become ordinary IMG URLs served from a bounded
in-memory cache on the gateway. Inline conversion is limited to 16 SVG
nodes per page. Cache holds at most 128 JPEGs / 8 MiB; expired images need
a page reload. Original SVG transparency is composited onto white. Output
fits within 1024x768 and uses JPEG quality 65. Image conversion statistics
include these vectors, so small icons can increase rather than save bytes.

CairoSVG runs in a separate worker with 1 MiB input, six-second wall timeout,
four-second CPU and 384 MiB address-space limits. External resource fetching
is blocked; bounded embedded data resources are allowed. XML entity handling
uses defusedxml with unsafe mode disabled. Scripted/animated SVG and vectors
requiring external resources are not fully reproduced. Failed conversions
leave the original external image or the existing inline fallback.

Verified dimensions, actual colored pixels and white background, rejection
of external file references, inline HTML replacement, external response
conversion, and a live transparent HTTP/1.0 SVG request returning JPEG.

## Reader mode and Stuff

Gateway now offers Normal, Simplified and Reader modes, persisted per caller
IP. Reader uses readability-lxml for main-article extraction and the existing
HTML simplifier for legacy output. Homepages/common section paths become a
list of up to 100 story links. Article output includes a headline and author/
publication metadata when supplied by the source. Existing image conversion
continues for linked images and inline SVG.

Stuff's initial HTML was an application shell with no paragraphs or headline
links. For Stuff GET/200 HTML responses (and sparse HTML on other sites), a
fresh server-side Chromium/Playwright context renders the public page before
extraction. Two render workers maximum; each has a 35-second timeout and a
private temporary profile. Timeout kills the worker process group. No browser
login state or cookies are reused. Rendering requests to nonpublic addresses
are rejected; images/media/fonts are not fetched during rendering. Hidden DOM
content is removed before extraction. No paywall/challenge handling is added.
If rendering fails, extraction falls back to the supplied HTML, so some pages
may still be empty. The browser render is separate from the caller's cookies
and cannot reproduce authenticated sessions. Chromium runs as the restricted
systemd service user with Playwright's default sandbox configuration.

Installed Debian Chromium and Python playwright/readability-lxml dependencies.
Verified public Stuff homepage rendering and 100 story links, extraction of
one public Stuff sports article with its headline and seven paragraphs, and
Reader-mode selection plus Stuff homepage delivery through a transparent
HTTP/1.0 test client. The test client's mode was restored to Normal. Actual
caller's mode remains selectable at http://192.168.1.100/.

## Drivers and Updates library

`http://192.168.1.100/software` (aliases `/drivers`, `/updates`) serves a basic
HTML catalogue linked from the gateway and Xtra welcome page. Metadata lives
in `software.json` beside the addon; `software.py` renders sections and source
links. Five archived EXE installers are mirrored under `/srv/ppp-downloads`:
Netscape Navigator 3.04 and 4.08, Communicator 4.8, Windows 95 Winsock 2 and
Dial-Up Networking 1.4. Browser binaries came from advertised archive mirrors;
Windows updates from AM.NET's archived Microsoft Win95 tool directory. Exact
source URLs, sizes and local SHA-256 values are in the manifest. Hashes identify
the downloaded copies and are not publisher authentication or installation tests.

IE 3/4/5.5 entries link to version-specific archive pages, with notes about
selecting the correct platform and offline setup. Utility publisher archives
and VOGONS driver library are external links, not locally hosted packages.
Drivers must be chosen for the caller's exact hardware. Windows packages are
not installed automatically. Requirements and archive status appear on page.

Local downloads use the existing registration listener on port 83 at
`/downloads/FILENAME`; it now uses ThreadingHTTPServer so slow file transfers
do not block registration or welcome requests. Supports HEAD and single explicit
byte ranges for resume; blocks path separators, symlinks and directory listing.
Verified catalogue and resume headers through transparent PPP, then checked
all five file sizes, hashes and range-delivered EXE magic. Full installation
on Windows 95 has not been tested. Serving catalogue/downloads does not depend
on the selected page mode.

## Library expansion, 3 November 2026

Added eight local Windows 95 packages: Year 2000 update, DCOM95 1.3, OLE32
update, Q256015 DOS device-name fix, MS00-029 IP fragment fix, AMD K6-2 timing
update, PowerToys and Kernel Toys. There are now 13 mirrored packages. The
addition manifest is `software-additions-20261103.json`; full source URLs,
notes, sizes and local hashes are recorded in `software.json`. Package notes
link to original publisher documentation or archived Microsoft pages, with
applicability notes rather than blanket installation recommendations.

Downloaded via HTTPS from the existing AM.NET archive, checked EXE magic,
size bounds, then independently verified on-disk SHA-256 and byte-range HTTP
delivery for every new package. Catalogue/documentation links and transparent
PPP page/download routing also passed. Windows installation remains untested.
Manifest updated atomically; prior live versions backed up under
`/var/backups/software-before-20261103.json` and
`/var/backups/software-page-before-20261103.py`.

## ListPCI, 3 November 2026

Added Hardware identification tools entries for ListPCI 1.03: direct
`LISTPCI.EXE` and complete `listpci103.zip` with README and corresponding C
source. Package came from the official FreeDOS repository at ibiblio and
matched its published size, SHA1 and CRC32; ZIP integrity and both local
byte-range downloads passed. Local SHA-256 values are in the catalogue.
Run in DOS (`LISTPCI`), or save output with `LISTPCI > PCIIDS.TXT`.
Actual PCI scanning on the caller's hardware has not been tested.

## Challenge handling, 3 November 2026

Reader mode now checks for usable paragraphs or headline lists before launching
Chromium, instead of always rendering Stuff pages or counting script text as
article content. Recognised verification pages (including Cloudflare's
`cf-mitigated: challenge` response header) get a small legacy-compatible
explanation and no additional renderer fetch. Chromium also stops early on
recognised challenge titles. This does not solve challenges or guarantee access.

Verified on the router with static fixtures: challenge responses preserve their
HTTP status, normal article extraction works, and neither case starts a second
fetch. swines.nz returned HTTP 200 with its normal placeholder page during the
check; its reported challenge was not reproduced. Installed reader.py/addon.py
and restarted ppp-web successfully.

Legacy User-Agent compatibility: public web requests identifying as Mozilla
1.x through 4.x now use a Firefox 128 User-Agent upstream. LAN requests and
modern browser identifiers are preserved. This changes browser identification,
not JavaScript capabilities. Tested the request hook with Netscape 3.04:
swines.nz returned HTTP 200 and its normal page with the rewritten headers.
Verified LAN/modern identifiers stay unchanged; deployed and restarted ppp-web.

## USRobotics INFs, 4 November 2026

Added local Courier V.90/x2 (mdmcrv90.inf), legacy Courier (mdmusrcr.inf)
and older Sportster (mdmusrsp.inf) definitions. usr-w95-infs.zip includes
all three, the Courier READINF.TXT and a short model-selection README.
Courier files were extracted from the archived SDL313US.ZIP / USRSDL.ZIP
at the zx.net.nz Metropoli mirror; no firmware executables are included.
Sportster INF is the 1995-era archive version, not a 56K Sportster driver.
Sources, file sizes and SHA-256 hashes are in
software-additions-usr-20261104.json and software.json. Validated Chicago
INF signature and modem class, ZIP integrity, and local HTTP full-download
hashes and partial-range responses. Installation on caller hardware remains
untested. Catalogue reads the manifest dynamically; no service restart needed.

## IE4 direct offline download, 4 November 2026

Added IE401SP2.EXE from https://sdfox7.com/ie/win32/IE401SP2.EXE as a local
Browsers entry. 54,641,495-byte self-extracting archive, inspected with 7z
without executing it: all 123 archived files passed integrity checks;
contains IE4SETUP/IE4SETUP.EXE, SETUPW95.CAB, Windows 95 shell components,
and English setup metadata. Extract the package and run the enclosed setup.
Full local HTTP download matched SHA-256; explicit byte-range resume passed.
Catalogue manifest is read dynamically; no proxy/PPP restart. Installation
on Windows 95 remains untested. Windows Desktop Update changes the shell.

## Direct Eicon stats repair, 4 November 2026

The ip-up hook now reads ttyds CONNECT reports from /run/ppp-modem,
matching the dedicated PPP listeners, and records modem_device. Previously
it only read MultiTech reports or the retired menu answerer journal/IP map.
Dashboard labels Eicon/MultiTech devices and explains that modem rates are
for the interface's initial link while traffic counters cover the bundle.
Backfilled the confirmed current ttyds14 session without changing its start
time or ifindex. Backups: /var/backups/ppp-stats-eicon-20261104-223513.
Restarted only ppp-web to load display changes; PPP and download services
were not restarted. Fixture verified direct Eicon report parsing, directional
rates, bundle byte counters and HTML label; live snapshot/page also passed.
Additional MLPPP members do not run ip-up separately, so per-member modem
rates and counters are not yet collected by this dashboard.

## Active bundle members and incoming numbers

collect-members.py runs as a root oneshot under ppp-stats-members.timer
(every five seconds). For the ten configured bundle peers, it reads only
PPPIOCGUNIT from the tty: no payload reads, writes or modem AT commands.
Idle chat listeners are excluded. Publishes public member device/card,
configured answering number and initial CONNECT rates atomically in
/run/ppp-stats/members.json; no credentials/account names are included.
Dashboard accepts this only for the current interface ifindex and a sample
at most 20 seconds old. Labels the number as configured; it is not a
captured called-party number or caller ID. Traffic byte counters remain
bundle totals; individual-link byte counters are not supplied.

Validated two simulated active members, idle-listener exclusion, connect
rates, incoming numbers, stale/changed-ifindex rejection and HTML. Installed
collector/service/timer and stats.py, enabled timer and restarted ppp-web
only. Live collector and dashboard snapshot/page passed. No PPP or download
service restarted. Additional connected members appear automatically.

Combined active-member download/upload rates now appear beneath each
bundle's member rows, in bit/s and decimal kB/s before overhead. JSON
connections include combined_line_rates with member_count, download_bps
and upload_bps. A directional total is unavailable if any member lacks a
rate for that direction; unlabelled MultiTech speeds are never guessed.
Verified 49,333 + 50,666 = 99,999 bit/s download and 57,600 bit/s upload,
unknown-rate handling, and live dashboard rendering. Initial connect rates
are not measured throughput or retrain monitoring.

## Private hostnames keep HTTP, 4 November 2026

Fixed mail.net.cryan.nz (192.168.88.20): the public-site heuristic treated
all dotted DNS names as public and upgraded HTTP to HTTPS, but this retro
mail service only listens on port 80. Before upgrading, the proxy now also
checks the original transparent destination IP and preserves HTTP for
non-global addresses. Legacy browser User-Agent is preserved for those
services too. Public-site HTTPS upgrading still works.

Verified actual request hook for private hostname/private IP/public hostname,
then an isolated PPP-like client using transparent NAT returned HTTP 200
from retro-register for mail.net.cryan.nz with HTTP/1.0. Test namespace and
veth removed. Installed addon.py and restarted only ppp-web; no modem calls
or local downloads restarted.

## LAN/VPN portal

Full portal is also served at http://10.150.150.157:8085/ by an additional
reverse-mode listener in the same mitmdump process, preserving shared web
compression counters. systemd drop-in: ppp-web.service.d/lan-portal.conf.
Only RFC1918 source addresses can use this listener; PPP transparent access
remains restricted to configured PPP caller addresses. Portal requests only
serve local UI hosts. HTML and redirects rewrite virtual gateway links to
server IP ports 8085 (UI) and 8083 (welcome/download/upload). No external
DNS/port forwarding configured. Tested private/public source ACL, relative
and absolute links, plus live index/stats/catalogue HTTP responses. PPP
services were not restarted.

Member collector also retains per-interface-ifindex history and sampled
bundle traffic counters for post-disconnect summary mail. History preserves
modem links that disconnected earlier in a bundle session. See
../isp-billing/README.md for the disconnect mail workflow.

## Media players, 5 November 2026

Added a Media players section with five local packages, all from the AM.NET
archive already used for the Windows 95 updates (manifest
`software-additions-media-20261105.json`, merged into `software.json`):

- `winamp291_full.exe`: Winamp 2.91 full, from the last 2.x line that runs on
  Windows 95. Its installer is compressed, so the version comes from the archive
  filename only.
- `mp64.exe`: Windows Media Player 6.4, build 6.4.05.0803. Its setup states that
  it needs IE 4.0 or later.
- `mp64_98.exe`: a later 6.4 build, 6.4.07.1110. Its license names Windows 95 and
  98, but the archive filename suggests a Windows 98 release.
- `WindowsMedia64-KB828026-x86-ENU.exe`: KB828026 security update for WMP 6.4
  (6.4.7.1128, replaces msdxm.ocx).
- `wmp6cdcs.exe`: Windows Media 8 codec pack (8.0.0.4487), adding WMA/WMV 8
  and MPEG-4 decoders to WMP 6.4.

WMP 7 and later do not support Windows 95 and were left out. Version strings
were read from the PE version resources and CAB listings without running
anything. Installed into `/srv/ppp-downloads` (root, 0644). Full local HTTP
downloads matched SHA-256 and byte-range requests returned 206; the
catalogue page lists all five. The previous catalogue is in
`/var/backups/software-before-media-20261105-223040.json`. Installation on
Windows 95 has not been tested. The catalogue is read dynamically, so no
service was restarted.
