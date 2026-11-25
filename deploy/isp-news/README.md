# Private local NNTP newsgroups

Deployed on eicon420 (10.150.150.157), 5 November 2026.
NNTP: 10.150.150.157:119. No SSL or login, private LAN/VPN/PPP only.
Setup page: http://10.150.150.157:8086/news (also /newsgroups).
Portal navigation/index and live service status include newsgroups.

Groups in groups.json:

- xtra.general: general conversation
- xtra.help: ISP and dial-up help
- xtra.retro: vintage computing
- xtra.marketplace: wanted, swaps and for-sale posts
- xtra.announcements: read-only ISP announcements
- xtra.test: user newsreader tests

Configure a news account in Netscape News, Outlook Express or another NNTP
reader using the numeric host and port above, without login/SSL. Enter your
display name and email, fetch the group list and subscribe. news:// links
on the setup page open the installed newsreader, not an HTML web client.
Actual vintage application configuration/rendering remains untested.

Debian INN 2.7.3, inn2.service enabled at boot. Bound only to the private
IPv4 host address. Firewall restricts port 119 to RFC1918/loopback before
package installation. readers.conf independently restricts sources to
those same ranges; unauthenticated identities can read only xtra.* and
post only xtra.* except xtra.announcements. Display names are not verified.
Articles are readable by all private-network users, not private messages.
No incoming NNTP feed peers, outgoing peers or control-message handler.
No public DNS, router forwards, external feed subscriptions or news-to-mail.

64 KiB article limit; plain text requested. Expiration keeps xtra.* normally
365 days, minimum 30/maximum 365; remaining internal groups expire after
90 days. Debian's /etc/cron.d/inn2 runs news.daily for expiry/maintenance.
Article spool /var/spool/news/articles (tradspool), overview under
/var/spool/news/overview, group/history DB /var/lib/news. Include these
and /etc/news in persistent backups; a running-server backup needs the
usual INN consistency precautions. No separate storage quota configured.

Configuration source: configure.py, readers.conf, groups.json. Installed
under /usr/local/lib/retro-isp-news. The portal's news_page.py lives under
/usr/local/lib/retro-isp. patch_portal.py applies routes/navigation/status
to the existing web.py rather than overwriting unrelated live updates.
Only retro-isp-web restarted; modem, proxy, mail, IRC and radio services
were not restarted. Firewall mutation replaced only retro_isp atomically,
preserving PPP/NAT tables.

Backups: /var/backups/isp-news-20261105-135200 (firewall),
/var/backups/isp-news-config-20261105-135315 (INN defaults and portal).

Validation: innconfval and inncheck passed. Live reader mode, LIST,
LIST NEWSGROUPS and group selection passed. verify.py creates a disposable
xtra.verify* group, tests POST/reply, ARTICLE/dot unstuffing, XOVER References,
announcements-post denial and hidden-control-group denial, then cancels its
articles and removes the temporary group. No user discussion group was
seeded with test articles. User groups start empty. Run as root:

    python3 /usr/local/lib/retro-isp-news/verify.py

Administrative announcements can be injected locally as news using INN's
admin tools; network users cannot post to that group. Group creation:

    sudo runuser -u news -- /usr/sbin/ctlinnd newgroup xtra.example y retro-isp

Add its description to /var/lib/news/newsgroups and update groups.json /
news_page.py. No regular client can create groups. Configuration references:
https://www.eyrie.org/~eagle/software/inn/docs/install.html
https://www.eyrie.org/~eagle/software/inn/docs/readers.conf.html

The restricted portal could not query systemd and showed UNKNOWN for local
services. Its status checks now fall back to opening the corresponding
local TCP listener, including NNTP port 119, when process state is unknown.
The live setup page and NNTP ONLINE status were verified.

## Fictional demo discussions

At the user's request, seeded 44 articles: general 9, help 14, retro 7,
marketplace 9, announcements 2, test 3. Guides, troubleshooting questions,
helpful/comically unhelpful replies, imaginary classifieds and welcome posts.
Subjects carry [demo], From names say fictional with .invalid addresses, and
every body has a fiction footer. Listings explicitly forbid real payments
or collection; no real incidents/people are represented. Technical guides
use actual service URLs and distinguish jokes from useful instructions.

make_demo.py builds demo-posts.json. seed_demo.py injects it locally with
rnews, including announcements; network announcement posting stays blocked.
Stable Message-IDs and STAT checks prevent duplicate articles. References
carry the complete ancestor chain for threaded readers. Dates are recent
and staggered for offline browsing. All 44 IDs, a nested reply References
header, fiction footer and group counts were verified through NNTP. A
second run added zero articles. Manifest: /var/lib/news/retro-demo-seed.json.

    sudo python3 /usr/local/lib/retro-isp-news/seed_demo.py

rnews batches use local LF line endings, with From headers kept on one
line; rnews rejects empty-first-line folded From fields. No service restart
was needed. Reader groups were initially empty, so only requested demo
content was added. Existing user posts are never replaced or deleted.

## HTML web viewer

/news now opens a JavaScript-free web reader; /news/setup retains native
newsreader configuration. /newsgroups aliases the viewer. Group pages show
100 article numbers per page with older/newer navigation and nested replies
from References. Article pages display plain text, author/date, a parent
link and nearby replies. Navigation to parent/replies is limited to 100
article numbers either side; range boundaries can split a thread. Subject
and author MIME encoded words are decoded. Group index includes counts.
Web viewer is read only; posting/replying still uses NNTP clients.

Fixed NNTP target with group allowlist, numeric article validation and no
POST implementation. ARTICLE follows GROUP and uses a numeric article ID,
so user-selected Message-IDs cannot retrieve hidden groups. MIME HTML and
attachments are not rendered; all article/header content is HTML-escaped.
NNTP multiline lengths/response totals are capped, sockets time out after
five seconds, and at most four simultaneous web-reader connections are
allowed. No credentials, external assets or JavaScript are required.
The existing portal service/firewall provide private network access.

Validated all six groups and all 44 article links, article bodies, parent
and reply navigation, hidden group/bad article/injection rejection, plus
native setup page. Five focused unit tests cover XSS escaping, cycle-safe
threading, invalid input before connection, MIME plain-text handling, NNTP
dot unstuffing/response limits and article relationship links. Existing
personal-web tests pass. No NNTP/modem restart or article mutations.
Backup: /var/backups/isp-news-viewer-20261105-141058.

Second content batch: added 56 posts in 14 four-message conversations using
more_demo.py, bringing the fixture to 100 articles. Topics include missing
homepage images, email attachments, multilink diagnosis, cache refresh,
radio buffering, web rings, quoting etiquette, serial cables, ball mice,
disk preservation and imaginary CRT/keyboard/magazine classifieds. Native
NNTP verification found all 100 stable IDs; group counts are general 21,
help 30, retro 19, marketplace 21, announcements 2 and test 7. New threads,
replies and angle-bracket/dot text were verified in the live HTML viewer.
Original posts and any user content are preserved; no services restarted.

Fourth expansion: to_200.py adds 17 four-article conversations (68 posts),
bringing the v2 fixture to 200. Added DNS troubleshooting, transfer resume,
clock/thread ordering, FTP modes, mailbox retention, session expiry, image
formats, guestbooks, IRC nicknames, frames/bookmarks, serial-port ownership,
CD-ROM checks, RAM expectations, display readability, two classifieds and
newsreader tests. Existing article IDs and bodies are preserved by seeding.
The live seed and HTML group index were checked for exactly 200 articles.

Fifth expansion: cupholder_demo.py adds 48 posts in 12 absurd support
threads, bringing the fixture to 248. Recurring residents misunderstand
CD trays, compressed-file pressure, firewalls, browser cookies, recycling,
screen savers, modem speed limits, offline pages, laser printers, email
attachments, disk rotation and household Internet tidiness. No new repeated
demo labels. All IDs and live HTML group visibility/counts verified.

Sixth expansion: bandwidth_demo.py adds 24 articles in six threads,
bringing the fixture to 272. zer0_c00l, xX_CableGod_Xx, PacketLord and
r00tbeer boast about T3/cable, dispute ping benchmarks and post enormous
screenshots; established residents challenge the claims. All new IDs and
live group visibility/counts verified, without replacing existing articles.

Seventh expansion: quake_demo.py adds 32 articles in eight Quake/Doom
latency threads, total 304. Includes phone-line contention, 999 ping,
frame rate versus latency, scoring excuses, local Doom setup, radio versus
game traffic, losing on a low-latency LAN and peak-time complaints. All
article IDs and live web visibility/counts verified. Existing posts retained.

Eighth expansion: added private local nz.general, carrying 32 posts in eight
mid-90s New Zealand life threads (flat milk disputes, Telecom toll bills,
video rentals, rugby/Sky arrangements, road trips, supermarket queues,
flatmate listings and TV remotes). Fixture total 336 across seven groups.
No public nz.general feed was subscribed to or relayed. readers.conf grants
private sources read/post to exactly nz.general in addition to xtra.*;
announcements remain read only. Retention is 365 days, as for xtra.*.
NNTP selection, all 32 HTML article links and reply navigation verified.
Backup: /var/backups/isp-nz-general-20261105-145626. nz_general_demo.py
contains the new fixture. Period rugby/TV background checked against:
https://www.rugbymuseum.co.nz/stories/history-of-nz-rugby/the-first-super-rugby-game-1996
https://www.beehive.govt.nz/feature/tvnz-scoping-study

Ninth expansion: thousand_demo.py and thousand-threads.txt add 664 posts
in 166 distinct four-article conversations, reaching exactly 1,000 posts.
Forty threads cover Kiwi everyday life, forty support, thirty retro,
thirty-six general discussion, twelve classifieds and eight reader tests.
Existing 336 IDs/bodies were retained. Unique keys, valid parent order and
ASCII bodies were checked before publishing. Seed verification now uses a
fresh reader connection after injection to avoid cached negative Message-ID
lookups. All 1,000 IDs and live viewer count, article links, bodies and
older/newer pagination across seven groups passed.

Totals: xtra.general 241; xtra.help 262; xtra.retro 179;
xtra.marketplace 81; xtra.announcements 2; xtra.test 43; nz.general 192.
Backup: /var/backups/isp-news-1000-20261105-151048. No service restart.

Tenth expansion: flame_demo.py adds three 18-article arguments (one root
and 17 replies each), bringing the fixture to 1,054. Milk responsibilities
in nz.general fork into flat finances, fridge advice and quoting etiquette;
T3 boasts in xtra.general become an availability dispute and bus analogy;
browser compatibility in xtra.retro splits into usability, standards and
personal grievances. Lengths vary from one-liners to multi-paragraph posts,
subjects drift, multiple branches persist, and endings do not settle the
argument. All 54 IDs and live nested trees/article replies verified.

Development groups added: dev.modem-dsp-emu and dev.v90modem, eight sourced
notebook summaries each. devlogs.py appends these after fiction cleanup,
with kind=devlog and X-Project-Devlog instead of X-Retro-Demo. Actual project
records provide dates, evidence, limitations and source paths. No invented
measurements, dialogue, credentials or personal contact details were posted.
These are selected notebook summaries, not a claim of a complete current
status audit or newly executed modem tests. Threads link related diagnosis,
implementation and physical/peer follow-up entries.

Private reader/post access covers exactly these two dev groups in addition
to the existing groups; announcements remain restricted. Retention is one
year. The index distinguishes sourced development notes from fictional
conversations. All 16 bodies/source references and NNTP header distinction
were verified. Total 1,070 articles across nine groups. Only the portal
restarted. Backup: /var/backups/isp-devlogs-20261105-153351.

Latest update: 134 fictional general articles now have deterministic
selective typos/rushed spelling via general_spelling.py. Only xtra.general
and nz.general are eligible; URLs, classified notes and real devlogs are
excluded. The 469 general seed articles move together to v3 Message-IDs so
references remain consistent. All replacements were verified before the
backed-up v2 versions were cancelled. Unrelated user/technical articles
were untouched. Full original NNTP articles and fixture/manifest backup:
/var/backups/isp-spelling-devlogs-20261105-153920.

more_devlogs.py adds 24 sourced notebook summaries, bringing each developer
group to 20 entries (40 total). New topics include physical ADSP conformance,
saturation/carry corrections, 33600 acceptance, packet-clock timing, tone
parity, native versus firmware ISDN evidence, PCI/guest boundaries, short
PTY writes, busy flow control, per-call queues, MNP/MNP5 qualification,
V.42bis/V.44, carrier-loss hold, stored profiles and Quick Connect limits.
Source references and result limitations are included in every entry.
Real devlogs retain X-Project-Devlog and never receive spelling transforms.
All 1,094 article IDs and live web totals, devlog links and representative
typos verified. No web, modem or NNTP restart required for this update.

Expanded viewer: defaults to 500 article-number slots per group; links offer
100/250/500/1000, with the selected size preserved in older/newer navigation.
Navigation appears above and below the list. Starters show descendant reply
counts within the displayed range. Overview response budget is now 1 MiB;
article body bounds are unchanged. Iterative thread rendering handles deep
1,000-article chains without recursion failure. Seven viewer tests and the
existing two personal-web tests pass. Live default xtra.general rendered all
267 articles rather than the previous 100; 100/1000 controls verified.

longform_demo.py adds 24 articles: six 392-415-word originals plus varied
follow-ups. Topics are a flat telephone constitution, cable installation,
a failed games night, dial-up-friendly pages, support diagnosis and a wet
bach weekend. Live bodies/source links and totals verified: 1,118 articles.
Backup: /var/backups/isp-news-expanded-view-20261105-180718. Only portal
restarted. Existing articles and developer entries were preserved.

## Historical archive import (web only)

328 real 1995-97 records are live under archive.nz.general (325),
archive.nz.comp (2), archive.comp.dcom.modems (1). They are separate from
fictional NZ/general posts and real project devlogs. All 328 links and
representative first/last article bodies/dates/source links were verified.
The native server retained 1,119 posts at verification, including a user
addition beyond the 1,118 seeded records; no user posts were modified.

harvest_history.py fetches 12 conversations from a 1995-96 Google Groups
search, two dated nz.comp FAQ threads, and 1994-98 candidates from the
checksum-verified Internet Archive comp.dcom.modems mailbox. The selected
record years are 1995 (1), 1996 (145), 1997 (182). Google conversations can
contain later replies than the search's nominal end date. This is a bounded
partial collection, not a complete export or complete thread guarantee.

Google hides original RFC headers/emails on these views. The importer uses
displayed author names, source calendar days normalized to noon UTC,
archive-local stable IDs and flat conversation grouping. It never claims
those IDs/References are the original RFC headers. One original mbox record
preserves available Message-ID/Date/From/References; routing/group headers
are mapped for the local archive. Source-provenance/precision headers are
retained and the web article links to its original archive. No generated
forum text or spelling changes are applied to imported material.

Source pages and selected JSON/base64 records are under
/var/lib/retro-isp-news/archive-sources. Verified zip sources:
https://archive.org/download/usenet-nz/nz.general.mbox.zip (only 2009-13;
NOT imported as 1990s content), nz.comp.mbox.zip (downloaded, not imported),
and https://archive.org/download/usenet-comp/comp.dcom.modems.mbox.zip.
Google source links are recorded per article in selected-history.json.
Control/Supersedes/X-No-Archive records and oversized/invalid records from
the raw mailbox are excluded. Full source bodies are not checked into Git.

Automatic approval review rejected the proposed native NNTP import because
it required persistent global policy changes: artcutoff 10 -> 0, maxartsize
65536 -> 131072, and indefinite archive retention. These were NOT applied;
readers.conf, INN groups, expiry and port policies are unchanged. Instead,
history_archive.py provides a bounded read-only local web backend with the
same group/article renderer. Its root-owned corpus copy lives at
/usr/local/lib/retro-isp/history/selected-history.json, no new listening port.
The backend validates checksums, group/MID uniqueness, <=1000 records,
<=16 MiB JSON and <=128 KiB per article; it has no posting method. Only the
portal restarted. Native newsreaders do not list these archive collections.

Eight viewer tests plus the two existing personal-web tests pass. The
historical reader regression verifies old dates, local-only access, source
links, text escaping and absence of a posting operation. Backup:
/var/backups/isp-web-history-20261105-185945.

### Approved NNTP activation

The user explicitly approved the NNTP policy changes. They are now live:
artcutoff 0 (old dates accepted), global maxartsize 131072, and
archive.*:A:never:never:never. Ordinary reader posts remain limited to
65536 bytes. Private readers may read archive.*, but archive groups are
absent from their posting mask. No feeds or public access were enabled.
Backup: /var/backups/isp-nntp-history-20261105-190626.

import_history.py injected all 328 selected records and verified every
served body plus From/Date/Message-ID/References/source headers against
the prepared corpus (ignoring transport line endings and trailing blank
lines). A second run added zero. Archive posting returned 441 as expected.
An archive-only expireover check kept all records. Native NNTP counts:
archive.nz.general 325, archive.nz.comp 2, archive.comp.dcom.modems 1.
Setup page now includes news:// links; archive notices no longer say
web-only. The web corpus remains its own stable-numbered read-only view,
so existing web article URLs do not change to match NNTP arrival order.
Both interfaces retain the same Message-IDs/provenance. All viewer tests
pass. Only NNTP and the portal were restarted; modem services unaffected.

### More NZ and modem archives

expand_history.py added 177 real 1996-97 records, bringing the historical
corpus to 505 across nine groups. Newly populated groups:
archive.nz.net.admin (6), archive.nz.rec (7), archive.nz.arts (12),
archive.nz.netstatus (15), archive.comp.dcom.fax (18),
archive.comp.dcom.isdn (70). archive.comp.dcom.modems grew from 1 to 50;
archive.nz.general remains 325 and archive.nz.comp remains 2.
Regional Auckland/Wellington, nz.tech, cable modem and USRobotics searches
had no matching period conversations; no empty groups were published.

Existing article hashes are preserved. Explicit web_number values freeze
the original 328 web article URLs before appending earlier-dated records.
The adapter validates contiguous unique numbers. A regression tests this
against reverse chronological arrival. All nine viewer tests and existing
personal-web tests pass. Catalog descriptions now load from root-owned
history/groups.json, bounded to 24 archive names / 16 KiB. Corpus and article
size limits are unchanged. New groups inherit the approved read-only archive
access/retention; no global NNTP policies or ports changed. Only the portal
restarted. Backup: /var/backups/isp-more-archives-20261105-191537.

### NZ classifieds, Win95, Quake and BBS expansion

Added 194 historical posts, bringing the archive corpus to 699 records in
21 groups. New groups: nz.wanted (6), nz.forsale (5), nz.politics (21),
comp.sys.ibm.pc.hardware.comm (30), comp.os.ms-windows.win95.misc (9),
rec.games.computer.quake.misc (8), quake.playing (1), quake.servers (1),
alt.bbs (30), alt.bbs.lists (30), alt.bbs.allsysop (24), alt.bbs.doors (29).
All names are published beneath archive.; existing groups remain intact.

Google collection stopped on HTTP 429. The remaining categories came from
SHA1-verified Internet Archive usenet-comp, usenet-rec and usenet-alt mailbox
snapshots, filtered to 1994–98. Cross-post Message-ID duplicates were skipped;
X-No-Archive requests and control messages were excluded. Two Quake records
without valid original Message-IDs received stable archive-local IDs and an
explicit provenance header; their available author, date and body were kept.
Existing article hashes and web numbers are unchanged. These additions use
the existing read-only archive access and retention policies. Only the portal
was restarted. Final-stage backup:
/var/backups/isp-more-archives-20261105-193558.

Verification: all 699 NNTP bodies and provenance headers match the prepared
corpus; all 21 web groups show the expected counts and working first/last
article pages. The previous 537 web article numbers and hashes are preserved.
Nine news viewer tests and two personal-web tests pass.

### Expansion to 3,000 historical posts

Added 2,301 unique 1994–98 records from the checksum-verified snapshots
already downloaded: hardware.comm +461 and alt.bbs, alt.bbs.lists,
alt.bbs.allsysop, alt.bbs.doors +460 each. Total: 3,000 across the existing
21 archive groups. collect_bulk_history.py stages bounded candidates;
merge_bulk_history.py takes turns across groups, deduplicates globally by
Message-ID and preserves all existing hashes and web numbers. Original
source exclusions and article-size checks remain in place.

Raised importer/publisher/viewer corpus bounds to 5,000 records / 64 MiB
(the current JSON is 9,614,304 bytes). Per-article limits, native posting,
retention and network access are unchanged. The viewer remains paginated.
Backup: /var/backups/isp-more-archives-20261105-194647. Only the portal
was restarted.
All 3,000 served NNTP bodies and provenance headers verified against the
corpus. All 21 web group counts and first/last article pages pass; all 699
previous article hashes/numbers are preserved. Nine news viewer tests and
two personal-web tests pass.

### Targeted x2 / K56flex / V.90 conversations

Imported 25 posts from five Google Groups conversations: comp.dcom.modems
+7 (Courier x2 activation, Gateway Telepath compatibility and update wizard),
comp.dcom.isdn +7 (Courier I-modem symmetric x2), and new
archive.de.comm.modem +11 (K56flex/V.90 upgrade discussion, German).
Total: 3,025 historical records across 22 archive groups.

CLI requests from the ISP server and vmhost LXC received HTTP 429, while
the user's browser could view the pages. Captured the five rendered message
section DOMs using the connected browser, saved via a temporary loopback-only
form, and stopped that form server after capture. collect_modem_topics.py
processes those saved source pages without further HTTP requests. Browser
controls are removed from message bodies; original spelling and quoted
content remain. Provenance identifies displayed-text transcriptions, missing
RFC headers, archive-local IDs, calendar-day dates normalized to noon UTC,
and flat conversation threading. No original RFC headers are invented.
All prior records and web numbers preserved. Existing read-only archive
policies apply; only the portal restarted. Backup:
/var/backups/isp-more-archives-20261105-201201.
Verification: all 3,025 NNTP bodies/provenance headers and all 22 web group
counts plus first/last article pages pass. All 3,000 previous URLs/hashes
remain stable. Nine news viewer and two personal-web tests pass.

### Ten more modem conversations

Browser search for x2/K56flex/V.90 in comp.dcom.modems (1997–98) provided
another ten conversations. Captured all message sections in bounded chunks
to avoid browser-tool output truncation, then processed their saved DOMs
with collect_more_modem_topics.py. Added 62 unique posts within the existing
1994–98 window; later replies in those conversations are excluded.
Topics include V.90 connection failures and V.34 fallback, symmetric server
modes, USR x2 firmware, Sony laptop upgrades, the FCC speed cap, MultiTech
USB modems, K56flex differences, Zoom connections and disconnects.
comp.dcom.modems now has 119 posts; total history is 3,087 in 22 groups.
All original source links and prior article numbers are preserved.
Temporary loopback capture form stopped after saving. No NNTP policy or
service changes; only portal restarted. Backup:
/var/backups/isp-more-archives-20261105-201530.
Verified all 3,087 NNTP bodies/provenance headers, 22 web group counts and
first/last pages, plus preservation of all 3,025 previous hashes/URLs.

### Further modem archive batch

Captured the remaining 20 conversations from the first modem search page
and 30 from its next page, plus four already imported conversations that
were automatically deduplicated. Date-filtered to the existing 1994–98
window: 246 new posts, bringing comp.dcom.modems to 365 and the full archive
to 3,333 across 22 groups. Topics include ThinkPad/Zoom/Linux modem support,
Cardinal x2/V.90 upgrades, K56flex downgrades, Courier firmware revisions,
serial-port CRC/overrun errors, ATY11 diagnostics and ISDN data-over-voice.
Source DOMs and modem-batch3-report.json retained on the ISP host. Used
collect_more_modem_topics.py with the batch manifest/report paths changed;
existing Message-IDs deduplicated and article numbers preserved.
Temporary loopback capture server stopped after saving. Only portal
restarted; archive permissions/retention and modem services unchanged.
Backup: /var/backups/isp-more-archives-20261105-202024.
Verified all 3,333 NNTP article bodies/provenance headers, all 22 web group
counts and endpoint pages, and all 3,087 previous article hashes/URLs.

### Modem search page 61–90

Imported 135 new period posts from 30 additional conversations on the third
page of the same 1997–98 modem search. comp.dcom.modems now has 500 posts;
full archive is 3,468 records across 22 groups. Topics include ATI6 line
statistics, ESS firmware, Linux PPP to Annex/Cisco servers, hangup problems,
Hayes support, Zoom upgrades and related connection troubleshooting.
Captured message sections in bounded browser DOM chunks; saved source pages
and modem-batch4-report.json on the ISP host. Existing date/size/provenance
rules and global deduplication applied. No original article or URL replaced.
Temporary loopback capture server stopped; only the portal restarted.
Backup: /var/backups/isp-more-archives-20261105-202334.
Verified all 3,468 NNTP bodies/provenance headers, 22 web group counts and
first/last pages, and preservation of all 3,333 previous hashes/URLs.

### Modem search page 91–120

Added 170 period posts from 30 more browser-captured conversations, including
46 eligible posts from one long discussion. Topics include minimum connect
speeds, Global Village V.90 firmware, USR external diagnostics, Supra/Zoom
upgrades and throughput problems. comp.dcom.modems is now 670; total corpus
3,638 across 22 groups. Saved modem-browser-batch5.json and
modem-batch5-report.json on the ISP host. Date filtering, stable Message-ID
deduplication, original-text provenance and prior web numbering unchanged.
Only portal restarted; temporary loopback capture form stopped.
Backup: /var/backups/isp-more-archives-20261105-203513.
Verified all 3,638 NNTP bodies/provenance headers, all 22 web group counts
using the 1,000-article page option, endpoint pages and all 3,468 previous
article hashes/URLs.

### Modem search page 121–150

Added 154 eligible posts from 30 more conversations captured through the
browser. Topics include Rockwell initialization strings, Lucent Winmodem
speeds, Freeserve, ZyXEL 2864 V.90 upgrades, Courier connections and
internal-versus-external modem speeds. comp.dcom.modems now has 824 posts;
full corpus is 3,792 in 22 groups. Source DOMs and modem-batch6-report.json
retained on the ISP host. Same date window, source provenance, Message-ID
deduplication and stable article numbering. Temporary loopback form stopped;
only portal restarted. Backup:
/var/backups/isp-more-archives-20261105-203810.
Verified all 3,792 NNTP bodies/provenance headers, 22 web group counts and
endpoint pages, and all 3,638 previous article hashes/URLs.

### Modem search page 151–180

Added 123 period posts from another 30 conversations: upload speeds,
repeated V.90 flashes, USR jumperless modems, Rockwell K56flex upgrades and
related connection issues. comp.dcom.modems now has 947 posts; historical
corpus totals 3,915 across 22 groups. Browser-captured source DOMs and
modem-batch7-report.json saved on the ISP host. Same provenance, date
window, deduplication and stable numbering. Temporary loopback form stopped;
only portal restarted. Backup:
/var/backups/isp-more-archives-20261105-204127.
Verified all 3,915 NNTP bodies/provenance headers, all 22 web group counts
and endpoint pages, and all 3,792 previous article hashes/URLs.

### Modem search page 181–210

Added 159 period posts from 30 browser-captured conversations on digital
padding, V.90 impairment learning, block errors, USR model numbers and
related troubleshooting. comp.dcom.modems now holds 1,106 posts; full corpus
4,074 across 22 groups. Source DOMs and modem-batch8-report.json retained
on ISP host. Same date window, provenance, deduplication and stable numbers.
Temporary loopback capture form stopped; only portal restarted.
Backup: /var/backups/isp-more-archives-20261105-204607.
Verified all 4,074 NNTP bodies/provenance headers, 22 web groups and endpoint
pages, all 3,915 previous hashes/URLs, plus modem pagination covering the
older 106 articles beyond the newest 1,000.

### Requested I-modem / V.91 / V.92 / PCM RAS collection

Imported all seven selected conversations, adding 60 records (modems +52,
ISDN +8). V.90/91 Server Symmetrical had two 1998 posts already archived;
its 30 later replies were appended. Full corpus: 4,134 records in 22 groups;
comp.dcom.modems 1,158 and comp.dcom.isdn 85. Topics: two I-modems on one
BRI, I-modem V.90 client limitations, V.91 clear-channel/LSB discussion,
symmetric 56/64k mode, Cisco MICA/NextPort PCM upstream, Lucent V.92 RAS
(including Wellington deployment), and PCM upstream implementation.

User explicitly requested these later conversations, so importer and
publisher date bounds are now 1994–2002. collect_targeted_modem_topics.py
accepts that same bounded range. Existing bulk/period collectors still
select their original ranges. Catalog year spans generated from actual
record dates; no dates rewritten to force later material into the 1990s.
Other corpus/article limits and NNTP policies unchanged. Browser DOM
captures retained in modem-browser-targeted.json; results in
modem-targeted-report.json. Only portal restarted; loopback form stopped.
Backup: /var/backups/isp-more-archives-20261105-205117.
Nine news viewer tests and two personal-web tests pass.
Verified all 4,134 NNTP bodies/provenance headers, all 22 web groups and
endpoint pages, modem older-page pagination, and all 4,074 prior hashes/URLs.

### Classic group expansion (14 new groups)

collect_classic_groups.py staged up to 300 posts per group from 20 more
SHA1-verified archive.org snapshots (usenet-comp/alt/rec), 1994–98 only,
with the same exclusions and header handling as collect_bulk_history.py.
merge_classic_groups.py appended 3,591 posts: 26 Message-ID duplicates
skipped, plus 7 originals INN rejects (missing From/Subject, a double-@ or
mismatched raw Message-ID, an asctime Date). Total: 7,725 records in 36
archive groups. New: alt.bbs.pcboard 285, alt.bbs.wildcat 298,
alt.games.doom 299, alt.irc 298, alt.winsock 300, alt.winsock.trumpet 295,
comp.bbs.waffle 299, comp.os.ms-windows.win95.setup 5,
comp.protocols.ppp 300, comp.sys.amiga.misc 13, comp.sys.apple2 300,
comp.sys.mac.comm 299, rec.games.computer.doom.playing 300,
rec.humor.funny 300 (all beneath archive.).

These snapshots are dense for 1994–95 and sparse afterwards. comp.sys.cbm (3),
comp.infosystems.www.browsers.ms-windows (1), comp.dcom.modems.cable,
comp.os.linux.setup, alt.folklore.computers and alt.sysadmin.recovery
(0 each) had too little in range and were not published (*-classic-skipped.json).

Bounds raised: importer/publisher/viewer 5,000 → 12,000 records, viewer
catalog 24 → 64 groups. Corpus JSON is 22 MB, under the unchanged 64 MiB cap.
Per-article size, posting masks, retention and network access unchanged.
Backup: /var/backups/isp-more-archives-20261105-211001 (pre-expansion corpus).
Verified: all 7,725 NNTP articles match the corpus; LIST ACTIVE shows 36
archive groups; the web index shows all counts; prior hashes/URLs preserved.
Only the portal restarted.

### Classic group expansion, batch 2 (20 new groups)

`collect_classic_groups.py 2` takes up to 200 posts per group, kept per
year and then picked in turns across 1994–98, so dense 1994–95 runs do not
crowd out later posts. In practice these archive.org snapshots barely cover
1996–98 for any group; the gap is in the source. Added 3,300 posts (9
malformed originals excluded): 11,025 records in 56 archive groups.
200 each unless noted: comp.sys.amiga.games, comp.sys.mac.hardware,
comp.os.linux.announce, comp.os.msdos.misc, comp.sys.atari.st,
comp.sys.ibm.pc.games.misc, comp.sys.ibm.pc.soundcard.misc,
comp.sys.ibm.pc.hardware.misc, comp.os.os2.misc (198),
comp.os.ms-windows.networking.tcp-ip, comp.mail.misc (198), comp.bbs.misc,
alt.internet.services, alt.fan.warlord, alt.bbs.searchlight,
alt.bbs.majorbbs, rec.games.computer.doom.misc and others. Skipped as
too thin (<10 in range): comp.sys.mac.misc, comp.os.linux.misc,
comp.sys.ibm.pc.games.action, comp.infosystems.www.browsers.misc,
comp.sys.ibm.pc.hardware.video, rec.games.computer.ultima.online,
rec.arts.sf.written.

Record bound raised again to 15,000 (corpus JSON 32 MB, cap 64 MiB; portal
RSS ~145 MB). history_archive.py now strips whitespace before comparing the
parsed Message-ID: one comp.mail.misc original ends its Message-Id header
with a trailing space, which made the loader assert and broke archive
pages for about two minutes until fixed. Backup:
/var/backups/isp-more-archives-20261105-213919. Verified: all 11,025 NNTP
articles match the corpus, LIST ACTIVE shows 56 archive groups, the web
index shows every count, and new and old article pages return 200.
Only the portal restarted. Nine viewer tests pass.

### 1990–93 back-fill, batch 3

`collect_classic_groups.py 3` rescans the snapshots already on disk (plus
comp.dcom.isdn and comp.dcom.fax) for 1990–93: up to 60 posts per year,
240 per group, written as *-classic3-selected.json. Importer, publisher
and merge now accept years from 1990; the record bound is 20,000.
Added 3,735 posts (12 malformed originals excluded, including a quoted
X.400 Message-ID that INN's STAT rejects as a syntax error). Total: 14,760
records in 56 groups; corpus JSON 45 MB (cap 64 MiB); portal RSS ~190 MB.
The first archive page after a portal restart takes ~5 s to load the corpus.
1990–93 posts were added to 28 existing groups, e.g. comp.sys.atari.st,
comp.sys.mac.hardware, comp.os.msdos.misc, comp.mail.misc, alt.bbs, alt.irc
and rec.humor.funny (240 each), comp.dcom.fax 224, comp.dcom.isdn 120,
alt.fan.warlord 180. comp.dcom.modems and nz.comp have no 1990–93 posts
in their snapshots; 1996 is nearly empty in every snapshot.

Web article numbers stay frozen, so these older posts are numbered after
the 1994–98 ones in each group's web listing; NNTP readers that sort by
date show them in order. The loader was tested against the new corpus
before it was installed. Backup: /var/backups/isp-more-archives-20261105-215749.
Verified: all 14,760 NNTP articles match; web index counts and new article
pages return 200.
