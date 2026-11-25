#!/usr/bin/env python3
"""Local recreation of the obsolete Xtra CD registration endpoint."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote
import re
import uploads

ASSETS = Path(__file__).resolve().parent / 'assets'
# NP32REG scans for exact keys and a terminating LF, copying bytes before LF.
# Keep this below its 2048-byte receive buffer and its 100-byte field buffers.
# Omit PHONE to retain the caller's working local number.
REGISTRATION = ('UID=xtralab\nUPWD=lab\nDNS1=10.150.150.1\n'
                'DNS2=10.150.150.1\n'
                'URL=http://192.168.1.100:83/welcome.htm\n')

def layout(title, content):
    return '''<HTML><HEAD><TITLE>XTRA Online Registration</TITLE></HEAD>
<BODY BGCOLOR="#C0C0C0" TEXT="#000000" LINK="#0000EE" VLINK="#551A8B">
<CENTER><TABLE WIDTH="600" BORDER="0" CELLSPACING="0" CELLPADDING="14">
<TR><TD WIDTH="204" VALIGN="TOP" BGCOLOR="#000000">
<IMG SRC="/assets/xtra-navigator.gif" WIDTH="204" HEIGHT="250" ALT="XTRA Navigator 3"></TD>
<TD VALIGN="TOP" BGCOLOR="#FFFFFF"><FONT FACE="Arial,Helvetica">
<H2>''' + title + '''</H2>''' + content + '''</FONT></TD></TR>
<TR><TD COLSPAN="2"><FONT SIZE="2">Local recreation for the XTRA CD.
Original Navigator 3 artwork from the supplied installer.</FONT></TD></TR>
</TABLE></CENTER></BODY></HTML>'''

FORM = layout('Welcome to XTRA', '''
<P>Welcome to XTRA Online Registration.</P>
<P>Your connection is ready. Complete the local registration below to continue.</P>
<FORM METHOD="POST" ACTION="/ipac-bin/ipac_registration_complete">
<P>Your name:<BR><INPUT NAME="display_name" SIZE="24" MAXLENGTH="40"></P>
<P><INPUT TYPE="SUBMIT" VALUE="Complete Registration"></P></FORM>
<P><FONT SIZE="2">This configures your local XTRA profile with username <B>xtralab</B>,
password <B>lab</B>, working DNS and the local welcome page. Your current
telephone number is kept. No real account is created. The name is not saved.</FONT></P>''')

DONE = layout('Registration Complete', '''
<EMBED SRC="/registration.ea" TYPE="application/x-easy" WIDTH="1" HEIGHT="1">
<NOEMBED><P><A HREF="/registration.ea">Apply your registration settings</A></P></NOEMBED>
<P>Thank you for joining XTRA!</P>
<P>Please wait a few seconds for your settings to be applied.</P>
<P><B>Now close this Netscape window to finish registration.</B>
The Join XTRA program will then complete your setup.</P>
<FORM><INPUT TYPE="BUTTON" VALUE="Close Netscape" ONCLICK="window.close();"></FORM>
<P>If the button does not close this window, press <B>Alt-F4</B>.</P>
<P><FONT SIZE="2">This is a local recreation of the registration service.</FONT></P>''')

WELCOME = layout('XTRA Internet', '''
<P>Welcome to your Internet connection.</P>
<P><A HREF="http://10.150.150.157:8086/status">ISP status</A> | <A HREF="http://10.150.150.157:8086/irc">IRC chat</A> | <A HREF="http://10.150.150.157:8086/homepages">Personal homepages</A></P>
<UL><LI><A HREF="/upload">Upload PCI report</A>
<LI><A HREF="http://192.168.1.100/software">Drivers and Updates</A>
<LI><A HREF="http://192.168.1.100/stats">Connection Stats</A>
<LI><A HREF="http://neverssl.com/">Start Browsing</A>
<LI><A HREF="/ipac-bin/ipac_registration_form">Online Registration</A></UL>
<P>You can use this page as your browser home page:</P>
<P><FONT SIZE="2">http://192.168.1.100:83/welcome.htm</FONT></P>
<P><FONT SIZE="2">This local page uses the original CD artwork.
Modern websites may require a newer browser.</FONT></P>''')

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'
    def page(self, body, content_type='text/html; charset=iso-8859-1'):
        data = body.encode('ascii') if isinstance(body, str) else body
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)
    def download(self, path):
        name = unquote(path[len('/downloads/'):])
        if not name or '/' in name or '\\' in name:
            self.send_error(404); return
        root = Path('/srv/ppp-downloads')
        target = next((p for p in root.iterdir() if p.name.lower()==name.lower() and p.is_file() and not p.is_symlink()), None)
        if target is None:
            self.send_error(404); return
        total = target.stat().st_size
        start, end = 0, total-1
        requested = self.headers.get('Range')
        if requested:
            match = re.fullmatch(r'bytes=(\d+)-(\d*)', requested)
            if not match or int(match.group(1))>=total:
                self.send_response(416);self.send_header('Content-Range',f'bytes */{total}');self.end_headers();return
            start=int(match.group(1));end=min(int(match.group(2)) if match.group(2) else total-1,total-1)
            if end<start:
                self.send_error(416);return
        self.send_response(206 if requested else 200)
        self.send_header('Content-Type','application/octet-stream')
        self.send_header('Content-Disposition',f'attachment; filename="{target.name}"')
        self.send_header('Accept-Ranges','bytes')
        self.send_header('Content-Length',str(end-start+1))
        if requested:self.send_header('Content-Range',f'bytes {start}-{end}/{total}')
        self.end_headers()
        if self.command=='HEAD':return
        with target.open('rb') as stream:
            stream.seek(start);remaining=end-start+1
            while remaining:
                chunk=stream.read(min(65536,remaining))
                if not chunk:break
                self.wfile.write(chunk);remaining-=len(chunk)
    def do_HEAD(self):
        self.do_GET()
    def do_GET(self):
        path = urlsplit(self.path).path.lower()
        if path == '/upload':
            if self.client_address[0] not in uploads.CLIENTS:
                self.send_error(403); return
            self.page(uploads.FORM)
        elif path.startswith('/downloads/'):
            self.download(urlsplit(self.path).path)
        elif path == '/assets/xtra-navigator.gif':
            self.page((ASSETS / 'xtra-navigator.gif').read_bytes(), 'image/gif')
        elif path == '/start.htm':
            self.page((ASSETS / 'start.htm').read_bytes())
        elif path in ('/registration.ea', '/ipac-bin/ipac_registration_complete.ea'):
            self.page(REGISTRATION, 'application/x-easy')
        elif path == '/ipac-bin/ipac_registration_complete':
            self.page(DONE)
        elif path == '/welcome.htm':
            self.page(WELCOME)
        elif path in ('/', '/ipac-bin/ipac_registration_form'):
            self.page(FORM)
        else:
            self.send_error(404)
    def do_POST(self):
        if urlsplit(self.path).path == '/upload':
            if self.client_address[0] not in uploads.CLIENTS:
                self.send_error(403); return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0 < length <= uploads.LIMIT+16384:
                    self.send_error(413); return
                self.connection.settimeout(90)
                body=self.rfile.read(length)
                if len(body)!=length:raise ValueError('The upload was interrupted.')
                result=uploads.receive(self.headers.get('Content-Type',''),body,self.client_address[0])
            except (ValueError,UnicodeError) as error:
                self.send_error(400,str(error));return
            except TimeoutError:
                self.send_error(408);return
            self.page(result)
            return
        if urlsplit(self.path).path != '/ipac-bin/ipac_registration_complete':
            self.send_error(404)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            self.send_error(400)
            return
        if not 0 <= length <= 4096:
            self.send_error(413)
            return
        self.rfile.read(length)  # Deliberately discard submitted form contents.
        self.page(DONE)
    def log_message(self, fmt, *args):
        # Avoid logging query strings or form data from old installers.
        print('%s %s %s' % (self.client_address[0], self.command, urlsplit(self.path).path), flush=True)

if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8083), Handler).serve_forever()
