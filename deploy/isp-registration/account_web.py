#!/usr/bin/python3
"""Separate-origin, legacy-browser ISP account dashboard."""
from datetime import datetime
from html import escape as esc
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import secrets
import socket
import threading
import time
from urllib.parse import parse_qs,urlsplit
from request_origin import same_origin

BASE='http://10.150.150.157:8087'
TOKENS={};LOCK=threading.Lock()


def rpc(data):
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
            client.settimeout(60);client.connect('/run/retro-isp-registration/provision.sock');client.sendall(json.dumps(data).encode()+b'\n')
            with client.makefile('rb') as stream:return json.loads(stream.readline(262144))
    except (OSError,ValueError):return dict(ok=False,error='Account service is temporarily unavailable. Please try again.')


def page(title,body):
    return ('<HTML><HEAD><TITLE>'+esc(title)+'</TITLE></HEAD><BODY BGCOLOR="#FFFFFF" TEXT="#000000" LINK="#000099"><H1>Xtra ISP Accounts</H1>'
            '<P><A HREF="/account">My account</A> | <A HREF="http://10.150.150.157:8086/">ISP services</A> | '
            '<A HREF="http://10.150.150.157:8086/signup">Join Xtra ISP</A> | <A HREF="http://mail.net.cryan.nz/mail">Webmail</A></P><HR>'+body+'</BODY></HTML>').encode('ascii',errors='xmlcharrefreplace')


def login(address,message=''):
    token=secrets.token_urlsafe(32);now=time.time()
    with LOCK:
        for key in list(TOKENS):
            if TOKENS[key][1]<now:del TOKENS[key]
        if len(TOKENS)>=500:TOKENS.pop(next(iter(TOKENS)))
        TOKENS[token]=(address,now+900)
    return page('Sign in',('<P><B>'+esc(message)+'</B></P>' if message else '')+
                '<H2>Account sign in</H2><FORM METHOD="POST" ACTION="/account/login"><INPUT TYPE="HIDDEN" NAME="token" VALUE="'+token+'">'
                '<P>Username: <INPUT NAME="username" MAXLENGTH="16"></P><P>Password: <INPUT TYPE="PASSWORD" NAME="password" MAXLENGTH="64"></P>'
                '<P><INPUT TYPE="SUBMIT" VALUE="Sign in"></P></FORM><P>Use your ISP/FTP account password. For accounts created by unified registration, this is also your mail password.</P>'
                '<P>Old browsers use plain HTTP. Use this only on your private network and keep a separate retro-service password.</P>')


def money(value):return 'unavailable' if value is None else f'NZD {value//100:,}.{value%100:02d}'
def size(value):return 'unavailable' if value is None else f'{value:,} bytes ({value/1000000:.2f} MB)'
def date(value):return datetime.fromtimestamp(value).strftime('%d %b %Y %H:%M')


def dashboard(data,message=''):
    username=data['username'];csrf=esc(data['csrf'],quote=True)
    def form(action,body,label):return '<FORM METHOD="POST" ACTION="/account/'+action+'"><INPUT TYPE="HIDDEN" NAME="csrf" VALUE="'+csrf+'">'+body+'<P><INPUT TYPE="SUBMIT" VALUE="'+label+'"></P></FORM>'
    out=('<H2>'+esc(username)+"'s account</H2>"+('<P><B>'+esc(message)+'</B></P>' if message else '')+
         '<P>Mailbox: <B>'+esc(data['mailbox'])+'</B></P>'+form('logout','','Sign out')+
         '<H2>Usage and mock bills</H2><P>Tariff: '+str(data['tariff'].get('cents_per_kb',7))+' cents per decimal KB uploaded + downloaded, plus '+str(data['tariff'].get('cents_per_minute',10))+' cents/minute. Rounded up, once per bundle. <B>Simulation only: no payment due.</B></P>')
    for active in data['active']:
        out+='<P><B>Connected now</B> as '+esc(active['account'])+' since '+date(active['connected_at'])+'<BR>Downloaded: '+size(active['download_bytes'])+'; uploaded: '+size(active['upload_bytes'])+'</P>'
    out+='<TABLE BORDER="1" CELLPADDING="5"><TR><TH>Disconnected</TH><TH>PPP login</TH><TH>Downloaded</TH><TH>Uploaded</TH><TH>Mock total</TH></TR>'
    for bill in data['bills']:out+='<TR><TD>'+date(bill['ended_at'])+'</TD><TD>'+esc(bill['account'])+'</TD><TD>'+size(bill['download_bytes'])+'</TD><TD>'+size(bill['upload_bytes'])+'</TD><TD>'+money(bill['total_cents'])+'</TD></TR>'
    if not data['bills']:out+='<TR><TD COLSPAN="5">No completed statements for this account yet.</TD></TR>'
    out+='</TABLE><P>Only this account and any administrator-linked PPP logins are shown. Current-session usage refreshes when you reload.</P>'
    out+='<H2>Billing email</H2>'+form('billing_email','<P><INPUT NAME="email" SIZE="40" MAXLENGTH="254" VALUE="'+esc(data['recipient'],quote=True)+'"></P>','Save billing email')
    out+='<H2>Personal web space</H2><P><A HREF="http://10.150.150.157:8086/~'+username+'/">Open your homepage</A>. FTP: 10.150.150.157:21, username '+esc(username)+', your ISP password.</P>'
    out+='<UL>'+''.join('<LI>'+esc(f['name'])+(' - '+str(f['size'])+' bytes' if f['size'] is not None else ' (directory)')+'</LI>' for f in data['files'])+'</UL>'
    out+=form('homepage','<P>Edit index.html (up to 32 KB):</P><TEXTAREA NAME="content" ROWS="18" COLS="76">'+esc(data['homepage'])+'</TEXTAREA>','Save and publish homepage')
    out+='<H2>Change password</H2><P>Updates PPP, mailbox and FTP together. Existing modem calls stay connected; reconnect using the new password next time.</P>'
    out+=form('password','<P>Current ISP/FTP password: <INPUT TYPE="PASSWORD" NAME="current" MAXLENGTH="64"></P><P>Current mail password (only if different): <INPUT TYPE="PASSWORD" NAME="mail_current" MAXLENGTH="64"></P><P>New password: <INPUT TYPE="PASSWORD" NAME="new" MAXLENGTH="64"></P><P>New password again: <INPUT TYPE="PASSWORD" NAME="new2" MAXLENGTH="64"></P><P>6-64 printable ASCII characters, no spaces, quotes, backslashes or colon.</P>','Change all passwords')
    return page('My account',out)


class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.0'
    def log_message(self,*args):pass
    def token(self):
        try:
            cookie=SimpleCookie();cookie.load(self.headers.get('Cookie',''));return cookie['isp_session'].value if 'isp_session' in cookie else ''
        except Exception:return ''
    def respond(self,content,status=200,cookie=None,location=None,head=False):
        self.send_response(status);self.send_header('Content-Type','text/html; charset=us-ascii');self.send_header('Content-Length',str(len(content)));self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','same-origin');self.send_header('X-Frame-Options','DENY')
        if cookie is not None:self.send_header('Set-Cookie','isp_session='+cookie+'; Path=/account; HttpOnly; SameSite=Strict; Max-Age='+('1800' if cookie else '0'))
        if location:self.send_header('Location',location)
        self.end_headers()
        if not head:self.wfile.write(content)
    def do_HEAD(self):self.do_GET(head=True)
    def do_GET(self,head=False):
        path=urlsplit(self.path).path
        if path=='/':self.respond(b'',302,location='/account',head=head);return
        if path not in ('/account','/account/login'):self.send_error(404);return
        if self.headers.get('Host','').lower()!='10.150.150.157:8087':
            self.respond(b'',302,location=BASE+path,head=head);return
        if path=='/account/login' or not self.token():self.respond(login(self.client_address[0]),head=head);return
        data=rpc(dict(action='profile',session=self.token(),address=self.client_address[0]))
        self.respond(dashboard(data) if data.get('ok') else login(self.client_address[0],data.get('error','Sign in again.')),cookie=None if data.get('ok') else '',head=head)
    def do_POST(self):
        action=urlsplit(self.path).path.removeprefix('/account/')
        if action not in ('login','logout','billing_email','homepage','password'):self.send_error(404);return
        if not same_origin(self.headers.get('Origin'),self.headers.get('Host',''),8087):
            self.respond(login(self.client_address[0],'This form came from a different address. Open the account page directly, then sign in.'),403);return
        try:length=int(self.headers.get('Content-Length','0'))
        except ValueError:self.send_error(400);return
        if not 0<length<=131072:self.send_error(413);return
        if self.headers.get('Content-Type','').split(';')[0]!='application/x-www-form-urlencoded':self.send_error(415);return
        form=parse_qs(self.rfile.read(length).decode('ascii',errors='replace'),keep_blank_values=True)
        data={key:value[0] for key,value in form.items()};data.update(action=action,address=self.client_address[0])
        if action=='login':
            with LOCK:issued=TOKENS.pop(data.pop('token',''),None)
            if not issued or issued[0]!=self.client_address[0] or issued[1]<time.time():self.respond(login(self.client_address[0],'The form expired.'),400);return
            result=rpc(data)
            if result.get('ok'):self.respond(b'',303,cookie=result['session'],location='/account')
            else:self.respond(login(self.client_address[0],result.get('error','Sign in failed.')),400)
            return
        data['session']=self.token();result=rpc(data)
        if result.get('ok') and (action=='logout' or result.get('logout')):self.respond(login(self.client_address[0],result.get('message','Signed out.')),cookie='');return
        profile=rpc(dict(action='profile',session=self.token(),address=self.client_address[0]))
        if not profile.get('ok'):self.respond(login(self.client_address[0],profile.get('error','Sign in again.')),400,cookie='');return
        self.respond(dashboard(profile,result.get('message') or result.get('error','')),200 if result.get('ok') else 400)


if __name__=='__main__':ThreadingHTTPServer(('10.150.150.157',8087),Handler).serve_forever()
