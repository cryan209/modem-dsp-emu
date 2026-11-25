#!/usr/bin/python3
"""Small HTTP/1.0 status, IRC help and personal-homepage service."""
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import socket
import subprocess
import time
from urllib.parse import unquote, urlsplit, parse_qs
import signup
import news_page
from request_origin import same_origin

BASE = 'http://10.150.150.157:8086'
USERS = Path('/srv/retro-isp/users')
NETWORK = '10.150.150.157'


def page(title, content):
    return ('<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN"><HTML><HEAD><TITLE>'+escape(title)+
            '</TITLE></HEAD><BODY BGCOLOR="#FFFFFF" TEXT="#000000" LINK="#000099">'
            '<TABLE WIDTH="100%" CELLPADDING="10" BGCOLOR="#000066"><TR><TD><FONT COLOR="#FFFFFF" SIZE="6"><B>Xtra ISP</B></FONT>'
            '<BR><FONT COLOR="#FFFFFF">The information superhighway starts here.</FONT></TD></TR></TABLE>'
            '<P><A HREF="/">Services</A> | <A HREF="/signup">Join the ISP</A> | <A HREF="http://10.150.150.157:8087/account">My account</A> | <A HREF="/status">ISP status</A> | <A HREF="/irc">IRC</A> | <A HREF="/news">Newsgroups</A> | '
            '<A HREF="http://10.150.150.157:8089/">Radio</A> | <A HREF="/homepages">Personal homepages</A> | <A HREF="http://10.150.150.157:8085/">Gateway</A> | '
            '<A HREF="http://mail.net.cryan.nz/">Mail</A></P><HR><H1>'+escape(title)+'</H1>'+content+
            '<HR><P><FONT SIZE="2">Private retro ISP for LAN/VPN and dial-up callers. No JavaScript required.</FONT></P></BODY></HTML>').encode('ascii', errors='xmlcharrefreplace')


def status():
    names = ['ppp-web', 'xtra-registration', 'ngircd', 'retro-ftp', 'inn2']
    try:
        states = subprocess.run(['systemctl', 'is-active', *names], capture_output=True, text=True, timeout=3).stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        states = []
    rows = []
    for i, label in enumerate(['Web gateway / stats', 'Software downloads', 'IRC', 'Personal web FTP', 'Newsgroups (NNTP)']):
        state = states[i] if i < len(states) else 'unknown'
        if state == 'unknown':
            try:
                with socket.create_connection((NETWORK, [8085, 8083, 6667, 21, 119][i]), timeout=1):
                    state = 'active'
            except OSError:
                state = 'unreachable'
        rows.append('<TR><TD>'+label+'</TD><TD>'+('ONLINE' if state == 'active' else escape(state.upper()))+'</TD></TR>')
    try:
        with socket.create_connection(('mail.net.cryan.nz', 25), timeout=2):
            mail = 'ONLINE'
    except OSError:
        mail = 'UNREACHABLE'
    rows.append('<TR><TD>Mail SMTP</TD><TD>'+mail+'</TD></TR>')
    members = []
    try:
        sample = json.loads(Path('/run/ppp-stats/members.json').read_text())
        if 0 <= time.time()-sample['sampled_at'] <= 20:
            for bundle in sample['bundles'].values():
                members.extend(bundle['members'])
            modem = str(len(members))+' active modem links'
        else:
            modem = 'Tracking sample is stale'
    except (OSError, ValueError, KeyError):
        modem = 'Tracking unavailable'
    links = ''.join('<LI>'+escape(m['card']+' '+m['device']+' - incoming '+m['incoming_number'])+'</LI>' for m in members)
    notices = []
    try:
        notices = json.loads(Path('/etc/retro-isp/notices.json').read_text())
    except (OSError, ValueError):
        pass
    text = '<P>Checked '+datetime.now().strftime('%d %b %Y %H:%M:%S')+' (New Zealand time).</P><TABLE BORDER="1" CELLPADDING="6">'+''.join(rows)+'</TABLE>'
    text += '<H2>Modem pool</H2><P>'+modem+'</P><UL>'+links+'</UL><P><A HREF="http://10.150.150.157:8085/stats">Detailed connection statistics</A></P>'
    text += '<H2>Service notices</H2>'+(''.join('<P><B>'+escape(n['date'])+'</B>: '+escape(n['text'])+'</P>' for n in notices) or '<P>No scheduled maintenance notices.</P>')
    return page('ISP service status', text+'<P>Reload for a fresh check. Status checks reflect the server, not guaranteed end-to-end modem performance.</P>')


def homepages():
    names = sorted(p.name for p in USERS.iterdir() if p.is_dir() and not p.is_symlink() and re.fullmatch('[a-z][a-z0-9_-]{0,31}', p.name))
    rows = ''.join('<LI><A HREF="/~'+name+'/">'+name+"'s homepage</A></LI>" for name in names)
    return page('Personal homepages', '<P>Your own small corner of the Web. Best viewed with enthusiasm.</P><UL>'+rows+'</UL>'
                '<H2>Publish with FTP</H2><P>Server: <B>'+NETWORK+'</B>, port <B>21</B>. Use your personal web-space login and password, with passive FTP enabled.</P>'
                '<P>Your FTP root is your public web directory. Upload <TT>index.html</TT> and your images; they appear immediately at <TT>'+BASE+'/~yourname/</TT>.</P>'
                '<P>Static HTML, GIF and JPEG work well. There is no CGI, PHP or server-side code. Files in this directory are public to other LAN/VPN and PPP users.</P>')


def personal_file(path):
    decoded = unquote(path)
    match = re.fullmatch(r'/~([a-z][a-z0-9_-]{0,31})(/.*)?', decoded)
    if not match:
        return None
    user, suffix = match[1], (match[2] or '/')
    parts = suffix.split('/')
    if any(p in ('.', '..') or p.startswith('.') for p in parts if p) or '\\' in suffix or '\x00' in suffix:
        return None
    root = USERS/user
    target = root/suffix.lstrip('/')
    try:
        ancestors = [p for p in target.parents if p == root or root in p.parents]
        if any(p.is_symlink() for p in [root, target, *ancestors]):
            return None
        target.resolve().relative_to(root.resolve())
        if target.is_dir():
            target = target/'index.html'
        return target if target.is_file() and not target.is_symlink() else None
    except (ValueError, OSError):
        return None



class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def do_POST(self):
        if urlsplit(self.path).path != '/signup':
            self.send_error(404); return
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            self.send_error(400); return
        if not 0 < length <= 4096:
            self.send_error(413); return
        origin = self.headers.get('Origin')
        if not same_origin(origin,self.headers.get('Host',''),8086):
            self.send_error(403); return
        if self.headers.get('Content-Type','').split(';')[0] != 'application/x-www-form-urlencoded':
            self.send_error(415); return
        data = parse_qs(self.rfile.read(length).decode('ascii', errors='replace'), keep_blank_values=True)
        ok, body = signup.submit(data, self.client_address[0])
        content = page('Welcome to Xtra ISP' if ok else 'Join Xtra ISP', body)
        self.send_response(200 if ok else 400); self.send_header('Content-Type','text/html; charset=us-ascii'); self.send_header('Content-Length',str(len(content))); self.send_header('Cache-Control','no-store'); self.send_header('Referrer-Policy','same-origin'); self.end_headers(); self.wfile.write(content)

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_GET(self, head=False):
        path = urlsplit(self.path).path
        if path == '/account' or path.startswith('/account/'):
            self.send_response(302); self.send_header('Location','http://10.150.150.157:8087'+self.path); self.send_header('Content-Length','0'); self.end_headers(); return
        if path in ('/signup', '/register'):
            data, kind = page('Join Xtra ISP', signup.form(self.client_address[0])), 'text/html; charset=us-ascii'
        elif path in ('/news/setup', '/newsgroups/setup'):
            data, kind = page('Newsreader setup', news_page.content()), 'text/html; charset=us-ascii'
        elif path in ('/news', '/newsgroups'):
            try:
                content = news_page.render(parse_qs(urlsplit(self.path).query))
            except (news_page.NewsError, OSError, ValueError):
                content = '<P>This group or article is unavailable, or the news server is busy. Please try again.</P><P><A HREF="/news">All groups</A></P>'
            data, kind = page('Xtra newsgroups', content), 'text/html; charset=us-ascii'
        elif path == '/status':
            data, kind = status(), 'text/html; charset=us-ascii'
        elif path in ('/homepages', '/homepages/'):
            data, kind = homepages(), 'text/html; charset=us-ascii'
        elif path in ('/irc', '/irc/'):
            data, kind = page('IRC chat', '<P><A HREF="http://10.150.150.157:8088/"><B>Open HTML IRC chat</B></A> - no JavaScript or IRC software required.</P><P>Meet the other dial-up survivors.</P><TABLE BORDER="1" CELLPADDING="6"><TR><TD>Server</TD><TD>'+NETWORK+'</TD></TR><TR><TD>Port</TD><TD>6667 (plain IRC)</TD></TR><TR><TD>Channels</TD><TD>#xtra, #helpdesk</TD></TR></TABLE><P>In mIRC or another IRC client:</P><PRE>/server '+NETWORK+' 6667\n/join #xtra</PRE><P>Pick your own nickname. No registration or password required on this private network. Chat is live, without an offline archive.</P>'), 'text/html; charset=us-ascii'
        elif path == '/':
            data, kind = page('Welcome to your ISP', '<P><B>Now with chat rooms, radio and your very own homepage!</B></P><P><A HREF="http://10.150.150.157:8089/"><B>Xtra Radio: The Cheese, 8-64 kbit/s</B></A></P><P><A HREF="/signup"><B>Join Xtra ISP: create your PPP, mail and web account</B></A></P><TABLE BORDER="1" CELLPADDING="12"><TR><TD><A HREF="/status"><B>ISP service status</B></A><BR>Live services, modem links and maintenance notices.</TD></TR><TR><TD><A HREF="/irc"><B>IRC chat</B></A><BR>Visit #xtra and #helpdesk.</TD></TR><TR><TD><A HREF="/news"><B>Newsgroups</B></A><BR>Browse local discussions on the web or in your newsreader.</TD></TR><TR><TD><A HREF="/homepages"><B>Personal web space</B></A><BR>Browse homepages and publish yours with FTP.</TD></TR></TABLE>'), 'text/html; charset=us-ascii'
        else:
            target = personal_file(path)
            if target is None:
                self.send_error(404); return
            kind = mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
            self.send_response(200); self.send_header('Content-Type', kind); self.send_header('Content-Length', str(target.stat().st_size)); self.send_header('Cache-Control', 'no-cache'); self.end_headers()
            if not head:
                with target.open('rb') as source:
                    while chunk := source.read(65536):
                        self.wfile.write(chunk)
            return
        self.send_response(200); self.send_header('Content-Type', kind); self.send_header('Content-Length', str(len(data))); self.send_header('Cache-Control', 'no-store'); self.end_headers()
        if not head:
            self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    ThreadingHTTPServer(('10.150.150.157', 8086), Handler).serve_forever()
