"""Plain HTML setup guide for the private Xtra NNTP server."""
import history_archive

GROUPS = tuple(history_archive.DESCRIPTIONS.items()) + (
    ('dev.modem-dsp-emu', 'Modem DSP emulation: sourced development notebook'),
    ('dev.v90modem', 'SIP V.90/V.92 softmodem: sourced development notebook'),
    ('nz.general', 'Everyday New Zealand life, mid-1990s style'),
    ('xtra.general', 'The ISP lounge: general discussion'),
    ('xtra.help', 'Dial-up, mail and ISP help from fellow users'),
    ('xtra.retro', 'Vintage computers, software, modems and memories'),
    ('xtra.marketplace', 'Retro computing wanted, swaps and for-sale posts'),
    ('xtra.announcements', 'ISP service announcements (read only)'),
    ('xtra.test', 'Try your newsreader and test posting'),
)


def content():
    rows = ''.join('<TR><TD><A HREF="news://10.150.150.157/'+name+'">'+name+'</A></TD><TD>'+description+'</TD></TR>' for name, description in GROUPS)
    return ('<P><A HREF="/news">Read newsgroups on the web</A></P><P>The original discussion forums: download messages, read them offline and reply when you reconnect.</P>'
            '<TABLE BORDER="1" CELLPADDING="6"><TR><TD>News server</TD><TD><B>10.150.150.157</B></TD></TR>'
            '<TR><TD>Port</TD><TD>119 (NNTP)</TD></TR><TR><TD>Login</TD><TD>No username or password required on the private network</TD></TR></TABLE>'
            '<H2>Choose a newsgroup</H2><TABLE BORDER="1" CELLPADDING="6">'+rows+'</TABLE>'
            '<H2>Netscape or Outlook Express</H2><OL><LI>Add a news / newsgroup account.</LI>'
            '<LI>Use <B>10.150.150.157</B> as the news server, port <B>119</B>. Leave secure connection / SSL and server login disabled.</LI>'
            '<LI>Enter your display name and your ISP email address for your posts.</LI>'
            '<LI>Download the group list, subscribe to the groups you want, then get new messages.</LI>'
            '<LI>Try your first post in <B>xtra.test</B>. Reply to a message to keep the discussion threaded.</LI></OL>'
            '<P>The group links above open a configured newsreader; they are not a web discussion client.</P>'
            '<P>These are local groups for LAN/VPN and dial-up users. Posts stay on this server; there is no public Usenet feed. '
            'Everyone on the private network can read posts and choose their displayed name. Messages are not private or identity-verified. '
            'Please use plain text and keep posts below 64 KiB. Posts normally remain for one year. '
            'Announcements are read only. No SSL is provided on this legacy port.</P>')


# The browser reader only issues bounded read commands to the fixed local NNTP
# server. Posting remains in native newsreaders.
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from email.header import decode_header, make_header
from html import escape
import re
import socket
import threading
import textwrap
import history_archive
from urllib.parse import urlencode, urlsplit

ALLOWED = {name for name, _ in GROUPS}
SLOTS = threading.BoundedSemaphore(4)
PAGE_SIZE = 500
PAGE_SIZES = (100, 250, 500, 1000)


def header_text(value):
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, ValueError, UnicodeError):
        return value


class NewsError(Exception):
    pass


class NNTP:
    def __init__(self, wire):
        self.wire = wire

    def line(self):
        value = self.wire.readline(65538)
        if not value or len(value) > 65536 or not value.endswith(b'\n'):
            raise NewsError('Invalid response from the news server.')
        return value.rstrip(b'\r\n')

    def command(self, text, codes):
        if '\r' in text or '\n' in text:
            raise NewsError('Invalid news request.')
        self.wire.write(text.encode('ascii') + b'\r\n')
        self.wire.flush()
        line = self.line()
        if line[:3] not in codes:
            raise NewsError('This group or article is unavailable. It may have expired.')
        return line

    def multiline(self, maximum=262144):
        rows, size = [], 0
        while True:
            line = self.line()
            if line == b'.':
                return rows
            if line.startswith(b'..'):
                line = line[1:]
            size += len(line) + 2
            if size > maximum or len(rows) >= 2000:
                raise NewsError('News response is too large.')
            rows.append(line)

    def group(self, name):
        fields = self.command('GROUP ' + name, (b'211',)).split()
        return tuple(int(x) for x in fields[1:4])

    def overview(self, low, high):
        if low > high:
            return []
        self.command('XOVER %s-%s' % (low, high), (b'224',))
        rows = []
        for raw in self.multiline(maximum=1048576):
            fields = raw.decode('utf-8', 'replace').split('\t')
            if len(fields) >= 6 and fields[0].isdigit():
                rows.append(dict(number=int(fields[0]), subject=header_text(fields[1]), author=header_text(fields[2]), date=fields[3],
                                 mid=fields[4], refs=re.findall(r'<[^<>\s]+>', fields[5])))
        return rows


@contextmanager
def connection():
    if not SLOTS.acquire(blocking=False):
        raise NewsError('The web reader is busy. Please try again shortly.')
    try:
        with socket.create_connection(('10.150.150.157', 119), timeout=5) as sock:
            with sock.makefile('rwb') as wire:
                nntp = NNTP(wire)
                if nntp.line()[:3] not in (b'200', b'201'):
                    raise NewsError('News server is temporarily unavailable.')
                yield nntp
    finally:
        SLOTS.release()


def url(group=None, article=None, before=None, limit=None):
    values = {}
    if group is not None:
        values['group'] = group
    if article is not None:
        values['article'] = article
    if before is not None:
        values['before'] = before
    if limit is not None:
        values['limit'] = limit
    return '/news' + ('?' + urlencode(values) if values else '')


def link(target, title):
    return '<A HREF="' + escape(target, quote=True) + '">' + escape(title) + '</A>'


def render_threads(group, rows):
    by_id = {r['mid']: r for r in rows}
    children = {}
    for row in rows:
        parent = next((ref for ref in reversed(row['refs']) if ref in by_id and ref != row['mid']), None)
        children.setdefault(parent, []).append(row)
    if not rows:
        return '<P>No articles in this range yet.</P>'
    seen = set()
    result = ['<UL>']
    roots = sorted(children.get(None, []), key=lambda x:x['number'], reverse=True)
    # Explicit stack supports large/deep pages without Python recursion limits.
    for root in roots + rows:
        if root['mid'] in seen:
            continue
        pending = [('node', root, 0)]
        while pending:
            action, row, depth = pending.pop()
            if action == 'close':
                result.append(row)
                continue
            if row['mid'] in seen:
                continue
            seen.add(row['mid'])
            label = ('Re: ' if depth and not row['subject'].lower().startswith('re:') else '') + row['subject']
            result.append('<LI>' + link(url(group, row['number']), label or '(no subject)'))
            if not depth:
                descendants, todo = set(), list(children.get(row['mid'], []))
                while todo:
                    child = todo.pop()
                    if child['mid'] not in descendants and child['mid'] != row['mid']:
                        descendants.add(child['mid'])
                        todo.extend(children.get(child['mid'], []))
                if descendants:
                    result.append(' <FONT SIZE="2">(' + str(len(descendants)) + ' replies in this range)</FONT>')
            result.append('<BR><FONT SIZE="2">' + escape(row['author']) + ' | ' + escape(row['date']) + '</FONT>')
            pending.append(('close', '</LI>', 0))
            replies = sorted(children.get(row['mid'], []), key=lambda x:x['number'])
            if replies:
                result.append('<UL>')
                pending.append(('close', '</UL>', 0))
                pending.extend(('node', child, depth+1) for child in reversed(replies))
    result.append('</UL>')
    return ''.join(result)


def plain_body(message):
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.get_content_type() != 'text/plain' or part.get_content_disposition() == 'attachment':
            continue
        raw = part.get_payload(decode=True)
        if raw is None:
            continue
        try:
            return raw.decode(part.get_content_charset() or 'ascii', 'replace')
        except LookupError:
            return raw.decode('utf-8', 'replace')
    return '[No plain-text body. Use a newsreader for other formats or attachments.]'


def render_article(nntp, group, number, low, high):
    nntp.command('ARTICLE ' + str(number), (b'220',))
    raw = b'\r\n'.join(nntp.multiline(maximum=131072)) + b'\r\n'
    message = BytesParser(policy=policy.default).parsebytes(raw)
    subject = str(message.get('Subject', '(no subject)'))
    body = plain_body(message)
    body = '\n'.join(textwrap.fill(line, width=78, replace_whitespace=False, drop_whitespace=False) if line else '' for line in body.splitlines())
    out = '<H2>' + escape(subject) + '</H2><P><B>From:</B> ' + escape(str(message.get('From', ''))) + '<BR><B>Date:</B> ' + escape(str(message.get('Date', ''))) + '</P>'
    source = str(message.get('X-Archive-Source', ''))
    parsed_source = urlsplit(source)
    if (parsed_source.scheme == 'https' and parsed_source.hostname in ('groups.google.com', 'archive.org')
            and not parsed_source.username and not parsed_source.password):
        out += '<P>' + link(source, 'Original archive source') + '</P>'
        precision = message.get('X-Archive-Date-Precision')
        if precision:
            out += '<P><FONT SIZE="2">Date precision: ' + escape(str(precision)) + '.</FONT></P>'
    # Resolve parent/reply navigation within the selected group only.
    nearby = nntp.overview(max(low, number-PAGE_SIZE), min(high, number+PAGE_SIZE))
    refs = re.findall(r'<[^<>\s]+>', str(message.get('References', '')))
    parent = next((row for mid in reversed(refs) for row in nearby if row['mid'] == mid), None)
    if parent:
        out += '<P>' + link(url(group, parent['number']), 'In reply to: ' + parent['subject']) + '</P>'
    out += '<HR><PRE>' + escape(body) + '</PRE><HR>'
    current_mid = str(message.get('Message-ID', ''))
    replies = [row for row in nearby if row['refs'] and row['refs'][-1] == current_mid]
    if replies:
        out += '<H3>Replies</H3><UL>' + ''.join('<LI>'+link(url(group, row['number']), row['subject'])+' - '+escape(row['author'])+'</LI>' for row in replies) + '</UL>'
    out += '<P>' + link(url(group), 'Back to ' + group) + ' | ' + link('/news', 'All groups') + '</P>'
    out += '<P>This web viewer is read only. Historical collections are also read only; current local groups accept posts through a newsreader. Nearby replies are shown within 500 article numbers; the group page lists other ranges.</P>'
    return out


@contextmanager
def reader(group):
    if group in history_archive.GROUPS:
        yield history_archive.Reader()
    else:
        with connection() as client:
            yield client


def render(query):
    group = query.get('group', [''])[0]
    if group and group not in ALLOWED:
        raise NewsError('Unknown newsgroup.')
    requested = query.get('limit', [str(PAGE_SIZE)])[0]
    if requested not in tuple(map(str, PAGE_SIZES)):
        raise NewsError('Invalid page size.')
    size = int(requested)
    numbers = {}
    for key in ('article', 'before'):
        value = query.get(key, [''])[0]
        if value:
            if not re.fullmatch(r'[0-9]{1,10}', value) or not 1 <= int(value) <= 2147483647:
                raise NewsError('Invalid article number.')
            numbers[key] = int(value)
    if numbers and not group:
        raise NewsError('Select a newsgroup first.')
    with reader(group) as nntp:
        if not group:
            rows = []
            for name, description in GROUPS:
                count, low, high = (history_archive.Reader() if name in history_archive.GROUPS else nntp).group(name)
                rows.append('<TR><TD>' + link(url(name), name) + '</TD><TD>' + str(count) + '</TD><TD>' + escape(description) + '</TD></TR>')
            return '<P><FONT SIZE="2">General groups include fictional conversations. Development groups contain sourced project notes.</FONT></P><P>Read the local discussions right here. No newsreader or JavaScript required.</P><TABLE BORDER="1" CELLPADDING="6"><TR><TH>Group</TH><TH>Articles</TH><TH>Description</TH></TR>' + ''.join(rows) + '</TABLE><P>' + link('/news/setup', 'Set up Netscape News or Outlook Express to post and read offline') + '</P>'
        count, low, high = nntp.group(group)
        heading = '<P>' + link('/news', 'All groups') + ' | ' + link('/news/setup', 'Newsreader setup') + '</P><H2>' + escape(group) + '</H2>'
        if group.startswith('archive.'):
            heading += '<P><B>Historical archive.</B> Original period content, separate from the fictional local groups. Google Groups imports preserve displayed authors and calendar dates, with archive-local IDs and conversation grouping where original headers are unavailable. Use each article source link for provenance. These historical collections are also available through NNTP, read only.</P>'
        if 'article' in numbers:
            if not low <= numbers['article'] <= high:
                raise NewsError('Article unavailable. It may have expired.')
            return heading + render_article(nntp, group, numbers['article'], low, high)
        end = min(high, numbers.get('before', high+1)-1)
        start = max(low, end-size+1)
        rows = nntp.overview(start, end) if count else []
        nav = []
        if start > low:
            nav.append(link(url(group, before=start, limit=size), 'Older articles'))
        if end < high:
            nav.append(link(url(group, before=min(high+1, end+size+1), limit=size), 'Newer articles'))
        nav.append(link(url(group, limit=size), 'Latest articles'))
        sizes = '<P>Page size: ' + ' | '.join(link(url(group, limit=n), str(n)) if n != size else '<B>' + str(n) + '</B>' for n in PAGE_SIZES) + '</P>'
        navigation = '<P>' + ' | '.join(nav) + '</P>'
        return heading + sizes + '<P>' + str(count) + ' articles. Showing up to ' + str(size) + ' article numbers, with replies indented. Parents outside this range appear as separate threads.</P>' + navigation + render_threads(group, rows) + navigation
