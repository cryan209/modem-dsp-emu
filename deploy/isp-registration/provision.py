#!/usr/bin/python3
"""Private Unix-socket broker for new retro ISP accounts."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import poplib
import pwd
import re
import secrets
import shlex
import shutil
import socketserver
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request

ROOT = Path('/var/lib/retro-isp-registration')
FTP = Path('/etc/retro-isp/ftp-users.json')
PAP = Path('/etc/ppp/pap-secrets')
BILLING = Path('/etc/retro-isp/connection-mail.json')
HOMES = Path('/srv/retro-isp/users')
MAIL_URL = 'http://mail.net.cryan.nz'
RESERVED = set('root admin administrator postmaster abuse support help billing xtraisp www ftp irc mail guest master stats nobody daemon'.split())
SERVERS = [('multitech-7910','10.254.104.2'), ('multitech-7910-b','10.254.104.6'),
           ('multitech-7900','10.254.104.10'), ('multitech-7900-b','10.254.104.14'), ('isdn-7950','10.254.105.2')]


class RegistrationError(Exception):
    pass


def validate(data):
    username = str(data.get('username', '')).strip().lower()
    if not re.fullmatch('[a-z][a-z0-9_-]{2,15}', username) or username in RESERVED:
        raise RegistrationError('Choose an available name: 3 to 16 lowercase letters, digits, hyphens or underscores, starting with a letter.')
    password = data.get('password', '')
    generated = bool(data.get('generate') and not password)
    if generated:
        password = secrets.token_urlsafe(15)
    if not isinstance(password, str) or not 6 <= len(password) <= 64 or not re.fullmatch(r'[!-~]+', password) or any(c in password for c in '\\"\x27'):
        raise RegistrationError('Use 6 to 64 printable ASCII characters, without spaces, quotes or backslashes, or ask us to generate a password.')
    if not generated and password != data.get('password2'):
        raise RegistrationError('The passwords do not match.')
    return username, password


def read(path, default):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def atomic(path, content, mode=0o600, gid=None, uid=0):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.signup-')
    try:
        with os.fdopen(fd,'w') as out:
            out.write(content); os.fchmod(out.fileno(),mode)
            if gid is not None:
                os.fchown(out.fileno(),uid,gid)
            out.flush(); os.fsync(out.fileno())
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)


def mailbox_names():
    with urllib.request.urlopen(MAIL_URL+'/mailboxes',timeout=10) as response:
        content=response.read(1024*1024).decode('ascii',errors='replace')
    return set(re.findall(r'([a-z][a-z0-9_-]*)@net\.cryan\.nz',content.lower()))


def verify_mail(username,password):
    pop=poplib.POP3('mail.net.cryan.nz',110,timeout=10)
    try:
        pop.user(username); pop.pass_(password)
    finally:
        try: pop.quit()
        except (OSError, poplib.error_proto): pop.close()


def create_mail(username,password):
    body=urllib.parse.urlencode(dict(username=username,password=password,password2=password)).encode()
    request=urllib.request.Request(MAIL_URL+'/register',data=body,headers={'Content-Type':'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(request,timeout=20) as response:
        response.read(1024*1024)
    # Authenticate rather than trusting the registration page's HTTP status.
    verify_mail(username,password)


def pap_names(text):
    names=set()
    for line in text.splitlines():
        try:
            words=shlex.split(line,comments=True)
        except ValueError:
            continue
        if words and words[0]!='*': names.add(words[0])
    return names


def pap_account(text,username,password):
    # Generated entries are the only lines replaced on recovery.
    marker='# ISP registration: '+username
    lines=text.splitlines(); out=[]; i=0
    while i<len(lines):
        if lines[i]==marker:
            i+=1
            while i<len(lines) and lines[i].startswith('"'+username+'" '): i+=1
        else:
            out.append(lines[i]);i+=1
    out.append(marker)
    for server,address in SERVERS:
        out.append(' '.join(json.dumps(s) for s in (username,server,password,address)))
    return '\n'.join(out)+'\n'


def provision(data):
    username,password=validate(data)
    ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
    with (ROOT/'provision.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        registry=read(ROOT/'accounts.json',{})
        entry=registry.get(username)
        if entry and entry.get('phase')=='complete':
            raise RegistrationError('That ISP account already exists. Existing accounts are not changed by registration.')
        users=read(FTP,{})
        billing=read(BILLING,{})
        pap=PAP.read_text()
        if not entry:
            if username in users or username in pap_names(pap) or username in billing.get('accounts',{}) or (HOMES/username).exists() or username in mailbox_names():
                raise RegistrationError('That name is already in use. Choose another name; registration will not overwrite an existing mailbox or account.')
            entry=dict(created_at=time.time(),phase='reserved')
            registry[username]=entry
            atomic(ROOT/'accounts.json',json.dumps(registry))
        # Recover partial provisioning only after verifying the original mailbox password.
        try:
            names=mailbox_names()
            if username in names:
                verify_mail(username,password)
            else:
                create_mail(username,password)
            entry['phase']='mail-ready'
            atomic(ROOT/'accounts.json',json.dumps(registry))
            ftp_account=pwd.getpwnam('retro-ftp')
            salt=secrets.token_bytes(16)
            users=read(FTP,{})
            users[username]=dict(salt=salt.hex(),hash=hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1).hex())
            home=HOMES/username
            if not home.exists():
                home.mkdir(mode=0o755)
                home.chmod(0o755)
                index=home/'index.html'
                index.write_text('<HTML><HEAD><TITLE>'+username+"\x27s homepage</TITLE></HEAD><BODY BGCOLOR=\"#FFFFCC\"><CENTER><H1>Welcome to "+username+"\x27s homepage!</H1><H2>UNDER CONSTRUCTION</H2><P>Upload your index.html with FTP to make this your own.</P><A HREF=\"/homepages\">The neighbourhood</A></CENTER></BODY></HTML>")
                os.chown(home,ftp_account.pw_uid,ftp_account.pw_gid);os.chown(index,ftp_account.pw_uid,ftp_account.pw_gid);index.chmod(0o644)
            billing=read(BILLING,{})
            billing.setdefault('accounts',{})[username]={'recipient':username+'@net.cryan.nz'}
            atomic(FTP,json.dumps(users,indent=2)+'\n',0o640,ftp_account.pw_gid)
            atomic(PAP,pap_account(PAP.read_text(),username,password))
            atomic(BILLING,json.dumps(billing,indent=2)+'\n')
            entry.update(phase='complete',mailbox=username+'@net.cryan.nz')
            atomic(ROOT/'accounts.json',json.dumps(registry))
        except Exception:
            # Keep a private recovery marker, never the password. Same form/name/password can resume.
            if entry.get('phase')!='complete':
                entry['phase']='pending-retry';atomic(ROOT/'accounts.json',json.dumps(registry))
            raise RegistrationError('Setup could not finish. Your name is reserved. Retry with the same name and password; existing account data will be preserved.') from None
    return dict(ok=True,username=username,password=password,mailbox=username+'@net.cryan.nz',homepage='http://10.150.150.157:8086/~'+username+'/',ftp_host='10.150.150.157',dial_numbers=['7920','7930','7900','7910'])


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(60)
        try:
            line=self.rfile.readline(131073)
            if len(line)>131072: raise RegistrationError('Request too large')
            data=json.loads(line)
            if data.get('action','register')=='register':
                result=provision(data)
            else:
                import accounts
                try:result=accounts.handle(data)
                except accounts.AccountError as error:raise RegistrationError(str(error)) from None
        except RegistrationError as error:
            result=dict(ok=False,error=str(error))
        except Exception:
            result=dict(ok=False,error='Registration is temporarily unavailable. Please try again.')
        self.wfile.write(json.dumps(result).encode()+b'\n')


if __name__=='__main__':
    path=Path('/run/retro-isp-registration/provision.sock')
    path.unlink(missing_ok=True)
    with socketserver.ThreadingUnixStreamServer(str(path),Handler) as server:
        path.chmod(0o660);server.serve_forever()
