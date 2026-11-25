# Retro ISP connection statements

User replaced the monthly-billing request with one summary email after a
PPP bundle disconnects, from xtraisp@net.cryan.nz to snuggi@net.cryan.nz. Monthly files in this folder
are unused drafts and were not deployed or enabled.

Installed connection-mail.py and /etc/ppp/ip-down.d/92-connection-mail.
The hook queues a private JSON statement for mtppp0 when the bundle's IP
session ends. Uses pppd BYTES_SENT/BYTES_RCVD final counters when available,
otherwise labels the last sampled counters. Duration is IP session elapsed
time. collect-members.py preserves per-ifindex member history and sampled
bundle counters in /run/ppp-stats/history-N.json, so links that left earlier
still appear, with configured answering numbers and initial CONNECT rates.
The summary is a retro simulation with no payment due, not an invoice.

retro-isp-connection-mail.service drains the private spool via LAN SMTP to
mail.net.cryan.nz:25, from Xtra ISP <xtraisp@net.cryan.nz>. Sender, recipient and SMTP endpoint
are configurable in /etc/retro-isp-connection-mail.json (root-only).
No mailbox password is used or stored. A 30-second timer retries failures;
the ip-down hook also triggers delivery asynchronously. Existing PPP calls
need not reconnect for this hook to work. Sent items remain as .sent records
for deduplication. As with SMTP generally, a crash after server acceptance
but before marking sent can cause a duplicate; deterministic Message-ID
helps identify it. Account mailbox login credentials are not needed.

Verified queue deduplication, final counters, recipient, members/numbers,
message body and one delivery per item with SMTP mocked. Server accepted
MAIL FROM and RCPT TO, then RSET; no DATA or test message submitted. Installed
and enabled retry timer; no queued messages at deployment. Live end-to-end
delivery is pending the next natural bundle disconnect.

Address correction: default recipient is snuggi@net.cryan.nz, not xtraisp.
Verified recipient acceptance with SMTP envelope then RSET; no test email sent.
Other configured recipients remain subject to the mail island server relay policy.

## Account recipients and mock usage charges

Disconnect records capture pppd's authenticated PEERNAME privately in the
mail spool (not in the public stats). /etc/retro-isp-connection-mail.json
has an accounts map keyed by that exact PPP login. scottc defaults to
snuggi@net.cryan.nz; unlisted logins use the top-level recipient. Add e.g.
"anotherlogin": {"recipient": "another@net.cryan.nz"} under accounts.
The mail island relay policy still controls which addresses it can deliver.

Global tariff: currency NZD, cents_per_kb 7, cents_per_minute 10. Each
account can override tariff fields. Traffic is downloaded + uploaded PPP
bytes, rounded up once to decimal KB (1,000 bytes); elapsed bundle time
rounded up to whole minutes, charged once for the bundle rather than each
member. No tax/fees added. Missing counters produce an unavailable total,
not a zero charge. Recipient and tariff are snapshotted at queue time so
retries retain the same destination and price. Existing queued records
without a snapshot use current defaults. Messages explicitly state that
this is a retro simulation with no payment due.

Seven unit tests verify rounding boundaries, unknown usage, account routing,
per-account tariff overrides, queued settings and header-injection rejection.
Installed and checked an offline email preview: 1,000,000 total bytes plus
10 minutes = NZD 71.00. No test email sent; next disconnect uses the change.
