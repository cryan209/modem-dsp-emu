"""Authenticated ISP-account operations behind the private provisioning socket."""
import hashlib
import hmac
import http.cookiejar
import importlib.util
import json
from pathlib import Path
import re
import secrets
import shlex
import threading
import time
import urllib.parse
import urllib.request
import provision as p

SESSIONS={}
ATTEMPTS={}
LOCK=threading.Lock()
SETTINGS=Path('/etc/retro-isp/account-portal.json')
SPOOL=Path('/var/spool/retro-isp-mail')
LIVE=p.ROOT/'live-sessions'


class AccountError(Exception):pass


def authenticate(username,password):
    if not re.fullmatch('[a-z][a-z0-9_-]{2,15}',username):return False
    user=p.read(p.FTP,{}).get(username)
    if not user:return False
    try:
        digest=hashlib.scrypt(password.encode(),salt=bytes.fromhex(user['salt']),n=16384,r=8,p=1).hex()
        return hmac.compare_digest(digest,user['hash'])
    except (KeyError,ValueError):return False


def identities(username):
    return [username,*p.read(SETTINGS,{}).get('aliases',{}).get(username,[])]


def session(data,mutating=False):
    now=time.time()
    with LOCK:
        record=SESSIONS.get(data.get('session',''))
        if not record or record['expires']<now or record['address']!=data.get('address'):
            raise AccountError('Your session expired. Please sign in again.')
        if mutating and not hmac.compare_digest(str(data.get('csrf','')),record['csrf']):raise AccountError('The form expired. Reload your account page.')
        return dict(record)


def billing_module():
    spec=importlib.util.spec_from_file_location('usage_bill','/usr/local/lib/retro-isp-billing/connection-mail.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def profile(username):
    ids=identities(username);billing=p.read(p.BILLING,{})
    account=billing.get('accounts',{}).get(username,{})
    bills=[]
    module=billing_module()
    paths=sorted([*SPOOL.glob('*.sent'),*SPOOL.glob('*.json')],key=lambda f:f.stat().st_mtime,reverse=True)[:500]
    for path in paths:
        record=p.read(path,{})
        if record.get('account') not in ids:continue
        tariff=record.get('tariff') or module.tariff_for(record,billing)
        bill=module.charges(record,tariff)
        bills.append(dict(ended_at=record['ended_at'],download_bytes=record.get('download_bytes'),upload_bytes=record.get('upload_bytes'),total_cents=bill['total_cents'],account=record['account']))
        if len(bills)>=30:break
    active=[]
    for path in LIVE.glob('*.json'):
        record=p.read(path,{})
        if record.get('account') not in ids:continue
        try:index=int((Path('/sys/class/net')/record['interface']/'ifindex').read_text())
        except (OSError,ValueError):continue
        if index!=record['ifindex']:continue
        history=p.read(Path('/run/ppp-stats')/('history-'+str(index)+'.json'),{})
        active.append(dict(account=record['account'],connected_at=record['connected_at'],download_bytes=history.get('download_bytes'),upload_bytes=history.get('upload_bytes'),members=history.get('members',[])))
    home=p.HOMES/username;files=[];content=''
    if home.is_dir() and not home.is_symlink():
        for item in sorted(home.iterdir())[:100]:
            if not item.name.startswith('.') and not item.is_symlink():files.append(dict(name=item.name,size=item.stat().st_size if item.is_file() else None))
        index=home/'index.html'
        if index.is_file() and not index.is_symlink() and index.stat().st_size<=32768:content=index.read_text(encoding='utf-8',errors='replace')
    tariff=dict(billing.get('tariff',{}));tariff.update(account.get('tariff',{}))
    return dict(username=username,mailbox=username+'@net.cryan.nz',recipient=account.get('recipient',username+'@net.cryan.nz'),tariff=tariff,bills=bills,active=active,files=files,homepage=content)


def mail_password(username,current,new):
    opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    body=urllib.parse.urlencode(dict(username=username,password=current)).encode()
    opener.open(p.MAIL_URL+'/mail/login',body,timeout=15).read(1048576)
    content=opener.open(p.MAIL_URL+'/mail/settings',timeout=15).read(1048576).decode('ascii',errors='replace')
    match=re.search(r'NAME="t" VALUE="([^"]+)"',content,re.I)
    if not match:raise AccountError('Mailbox login failed. Supply the current mail password if it differs from your ISP/FTP password.')
    body=urllib.parse.urlencode(dict(action='password',t=match[1],current=current,new=new,new2=new)).encode()
    opener.open(p.MAIL_URL+'/mail/settings',body,timeout=15).read(1048576)
    try:p.verify_mail(username,new)
    except Exception:raise AccountError('The mailbox password was not updated; ISP and FTP passwords were left unchanged.') from None


def handle(data):
    action=data.get('action');now=time.time()
    if action=='login':
        username=str(data.get('username','')).strip().lower();key=(data.get('address',''),username)
        with LOCK:
            attempts=[t for t in ATTEMPTS.get(key,[]) if t>now-300]
            if len(attempts)>=8:raise AccountError('Too many login attempts. Wait five minutes and try again.')
            attempts.append(now);ATTEMPTS[key]=attempts
            for token in list(SESSIONS):
                if SESSIONS[token]['expires']<now:del SESSIONS[token]
            for item in list(ATTEMPTS):
                if not ATTEMPTS[item] or ATTEMPTS[item][-1]<now-300:del ATTEMPTS[item]
        if not authenticate(username,str(data.get('password',''))):raise AccountError('Incorrect username or password.')
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
        with LOCK:
            if len(SESSIONS)>=500:SESSIONS.pop(next(iter(SESSIONS)))
            SESSIONS[token]=dict(username=username,address=data.get('address'),csrf=csrf,expires=now+1800)
            ATTEMPTS.pop(key,None)
        return dict(ok=True,session=token,csrf=csrf)
    auth=session(data,action!='profile');username=auth['username']
    if action=='profile':return dict(ok=True,csrf=auth['csrf'],**profile(username))
    if action=='logout':
        with LOCK:SESSIONS.pop(data.get('session'),None)
        return dict(ok=True)
    with (p.ROOT/'provision.lock').open('a') as lock:
        import fcntl
        fcntl.flock(lock,fcntl.LOCK_EX)
        if action=='billing_email':
            email=str(data.get('email','')).strip()
            if len(email)>254 or not re.fullmatch(r'[A-Za-z0-9.!#$%&*+/?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?',email):raise AccountError('Enter a valid billing email address.')
            billing=p.read(p.BILLING,{})
            # Explicit admin aliases map legacy PPP logins to this web account.
            for identity in identities(username):billing.setdefault('accounts',{}).setdefault(identity,{})['recipient']=email
            p.atomic(p.BILLING,json.dumps(billing,indent=2)+'\n');return dict(ok=True,message='Billing email updated for future connection statements.')
        if action=='homepage':
            content=data.get('content','')
            if not isinstance(content,str) or len(content.encode())>32768:raise AccountError('The browser editor supports up to 32 KB. Use FTP for larger files.')
            home=p.HOMES/username;index=home/'index.html'
            if not home.is_dir() or home.is_symlink() or index.is_symlink():raise AccountError('Homepage is unavailable; contact the ISP administrator.')
            account=p.pwd.getpwnam('retro-ftp');p.atomic(index,content,0o644,account.pw_gid,account.pw_uid)
            return dict(ok=True,message='Homepage saved and published.')
        if action=='password':
            current=str(data.get('current',''));new=str(data.get('new',''))
            if not authenticate(username,current):raise AccountError('Current ISP/FTP password is incorrect.')
            try:p.validate(dict(username=username,password=new,password2=data.get('new2')))
            except p.RegistrationError as error:raise AccountError(str(error)) from None
            if ':' in new:raise AccountError('The mail system does not accept a colon in the password.')
            users=p.read(p.FTP,{});salt=secrets.token_bytes(16)
            users[username]=dict(salt=salt.hex(),hash=hashlib.scrypt(new.encode(),salt=salt,n=16384,r=8,p=1).hex())
            pap=p.PAP.read_text()
            for identity in identities(username):pap=p.pap_account(pap,identity,new)
            mail_password(username,str(data.get('mail_current','')) or current,new)
            try:
                account=p.pwd.getpwnam('retro-ftp');p.atomic(p.FTP,json.dumps(users,indent=2)+'\n',0o640,account.pw_gid);p.atomic(p.PAP,pap)
            except Exception:raise AccountError('Mail password changed, but the local update could not finish. Retry with the original ISP password and the new password in the current-mail-password box.') from None
            with LOCK:
                for token in list(SESSIONS):
                    if SESSIONS[token]['username']==username:del SESSIONS[token]
            return dict(ok=True,logout=True,message='PPP, mail and FTP passwords updated. Sign in again with your new password.')
    raise AccountError('Unknown account operation.')
