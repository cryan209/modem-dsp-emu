# Unified retro ISP registration

Live form: http://10.150.150.157:8086/signup (also /register). Linked from
retro services and the main gateway. Virtual PPP gateway /signup redirects
to it. Private LAN/VPN and PPP reachability inherited from retro-isp firewall.

One new name/password provisions:

- Explicit PAP credentials for the shared MLPPP name and other configured
  dial-in PPP server identities. Existing wildcard/legacy users remain
  unchanged; registered names choose more-specific password-checked secrets.
- Mailbox username@net.cryan.nz, through the mail island registration form,
  followed by actual POP3 authentication verification.
- Scrypt-authenticated virtual FTP login, isolated home under
  /srv/retro-isp/users/NAME, and an immediately available starter homepage.
- Connection statement recipient NAME@net.cryan.nz in the account billing
  map, inheriting the configured mock 7c/KB + 10c/minute tariff.

Form is ASCII HTML for old browsers, POST-only, with expiring single-use
address-bound tokens, Origin checking when supplied, size limits, no-store
responses and no password logging. User-selected passwords must be 6–64
printable ASCII characters without whitespace/quotes/backslashes. Generated
passwords are shown in confirmation or a retry notice, never in a URL.

Frontend is the existing unprivileged retro-isp-web service with supplementary
retro-signup group. It submits bounded JSON only to the root-owned Unix socket
/run/retro-isp-registration/provision.sock (0660, root:retro-signup, directory
0750). The privileged broker has fixed paths/operations and serialized
provisioning. It never executes user-provided commands. It refuses existing
names, including current mail/FTP/PAP/billing users and reserved service names.
The mail admin SSH was unavailable, so mailbox creation uses the existing
HTTP form; no mailbox password is needed for administration.

The broker records phases privately in /var/lib/retro-isp-registration/accounts.json.
A partially completed signup can resume only after the supplied password
successfully authenticates its mailbox. Existing completed accounts cannot
be reset/overwritten via signup. This is recoverable provisioning across two
hosts, not a distributed transaction: the mail account can exist before
local setup completes. Passwords are not stored in the registry. PAP needs
its normal root-only secret; FTP stores scrypt hashes. Billing configuration
now lives at /etc/retro-isp/connection-mail.json with the old
/etc/retro-isp-connection-mail.json retained as a compatibility symlink.

FTP loads account data on each new client connection, so signup needs no
FTP restart and does not interrupt other FTP uploads or PPP calls. New public
home directories explicitly use 0755 even with the broker umask 0077; web
path checks return 404 for inaccessible files and reject symlinks/traversal.

Validation: four unit tests cover input validation, POP3 lifecycle, exact
PAP entries/idempotence, partial failure/retry, account mappings and homepage
permissions under a restrictive umask; existing homepage path tests pass.
Live verification account ispcheck completed actual form signup/recovery,
POP3 login, FTP login/upload and immediate web publication, and billing
recipient mapping. Its real PAP secret accepted the correct password and
rejected a wrong one in a disposable network namespace; IPCP established.
No modem calls were interrupted and no test emails were sent. Single-use
form tokens and restricted broker access were checked live.

Two separate test mailboxes were created during verification: ispcheck is
fully provisioned; ispdemo is an empty mail-only pending test reservation
from the initial failed POP3 verification implementation. Its generated
password was not retained. Existing user mailboxes were not changed. Removing
that unused mailbox later requires mail-host administration. No mailbox
admin SSH access was available; no destructive cleanup was attempted.

Backups: /var/backups/isp-registration-20261104-235534 plus an addon-before-signup
backup. Scripts installed under /usr/local/lib/retro-isp-registration;
service: retro-isp-registration.service. Frontend signup.py is installed
beside web.py under /usr/local/lib/retro-isp. Admin account edits/disable/reset
and adoption of existing independently created mailboxes are not implemented.

## Account login and dashboard

Live: http://10.150.150.157:8087/account. Gateway and services link to it;
/account routes on both portals redirect there. It has a separate HTTP
origin from user-uploaded pages (8086), with the cookie restricted to
/account, HttpOnly/SameSite, no-store pages, frame denial, POST+CSRF+Origin
checks, address-bound 30-minute sessions and login attempt throttling.
Sessions live only in broker memory and are invalidated on restart/password
change. No passwords are stored in browser cookies or session memory.

Authenticate with the ISP/FTP password (scrypt). Unified accounts use the
same password for all services. Existing snuggi uses its previously issued
FTP password. Admin-only /etc/retro-isp/account-portal.json explicitly maps
snuggi to legacy PPP login scottc. Aliases cannot be supplied by the browser.
Dashboard history is filtered to the authenticated name and these aliases.
It never lists another account's statements. Private ip-up hook
93-account-session records PEERNAME/ifindex/session start under
/var/lib/retro-isp-registration/live-sessions, not in public stats. Existing
live session ownership was backfilled from its matching owner pppd journal.
Current usage is taken from the matching interface's sampled counters;
completed bills are read from private connection-mail sent/queued records.

Dashboard supports billing email changes for future statements, a personal
file list, an index.html editor (32 KB; larger files/images through FTP),
and password updates across PPP, mail and FTP. The broker authenticates
mail via /mail/login and uses the existing /mail/settings password form
and token, then verifies new POP3 credentials before updating local PAP
and FTP. Legacy users can supply a separate current mail password. All
sessions for that user are revoked after password changes; active modem
calls stay up. A local failure after mail accepted a password is reported
explicitly with recovery instructions; this cross-host update is not a
distributed atomic transaction. No test messages were sent.

Service retro-isp-accounts runs DynamicUser with only the broker socket
group; no direct read access to account databases or spool. Broker actions
are fixed and authenticated. Port 8087 is covered by the existing private
service nftables rules. Backend HTML edits stay under the authenticated
user's home; no arbitrary file paths are accepted and symlinks are refused.

Validation: unit tests for session/account isolation, address binding,
expiry, CSRF, billing mutation/header injection, failed login and logout.
Live verification account exercised real cookie login, denied tokenless
updates, billing email, homepage save/HTTP publication/FTP ownership, full
shared-password update then restoration, session invalidation and logout.
Existing snuggi login and linked scottc live usage also verified. Services
enabled across reboots. Account GUI has not been tested in actual Netscape
3/IE4, though it uses ordinary HTML forms and cookies without JavaScript.
Backup: /var/backups/isp-accounts-20261105-000942 and addon-before-accounts.

### Hostname-origin fix

Account/signup form origins now match the actual trusted Host and service
port rather than only a hard-coded numeric URL. Allowed aliases are the
server IP, eicon420.net.cryan.nz and eicon420. Account GETs via aliases
canonicalize to the numeric URL to avoid split cookie origins. Cross-port,
external, malformed and null origins remain blocked; token protection is
unchanged. Reproduced hostname form 403 and verified corrected same-host
routing, canonical redirect and rejection of invalid CSRF/external origins.

Account browser Origin fix, 5 November 2026: account responses and signup
POST responses now use Referrer-Policy: same-origin. no-referrer caused
Chromium form POSTs to carry Origin: null and fail the Origin check.
Origin validation and CSRF tokens remain required. Verified browser
submission reaches credential validation using an empty test form, without
using user credentials. Backup: /var/backups/isp-form-origin-20261105-132556.
