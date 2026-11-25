#!/usr/bin/python3
"""Live account-portal checks using only the isolated verification account."""
import ftplib
import http.cookiejar
import json
from pathlib import Path
import poplib
import re
import secrets
import shlex
import urllib.error
import urllib.parse
import urllib.request

BASE='http://10.150.150.157:8087'
USER='ispcheck'


def password():
    for line in Path('/etc/ppp/pap-secrets').read_text().splitlines():
        fields=shlex.split(line,comments=True)
        if fields and fields[0]==USER:return fields[2]
    raise AssertionError('Missing verification account')


def open_client():return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def get(client,path):
    with client.open(BASE+path,timeout=30) as response:return response.read().decode()
def post(client,path,data):
    request=urllib.request.Request(BASE+path,data=urllib.parse.urlencode(data).encode(),headers={'Origin':BASE})
    with client.open(request,timeout=60) as response:return response.read().decode()
def csrf(html):return re.search(r'NAME="csrf" VALUE="([^"]+)"',html)[1]
def login(client,pw):
    form=get(client,'/account');token=re.search(r'NAME="token" VALUE="([^"]+)"',form)[1]
    return post(client,'/account/login',dict(token=token,username=USER,password=pw))
def credentials_work(pw):
    pop=poplib.POP3('mail.net.cryan.nz',110,timeout=10)
    try:pop.user(USER);pop.pass_(pw)
    finally:pop.quit()
    with ftplib.FTP() as ftp:ftp.connect('10.150.150.157',21,timeout=10);ftp.login(USER,pw)
    assert password()==pw


if __name__=='__main__':
    original=password();client=open_client();anonymous=get(client,'/account');assert 'Account sign in' in anonymous and 'Current-session usage' not in anonymous
    page=login(client,original);assert USER+"'s account" in page and 'Billing email' in page and 'scottc' not in page
    try:post(client,'/account/billing_email',dict(email='bad@host'));raise AssertionError('Missing CSRF accepted')
    except urllib.error.HTTPError as error:assert error.code==400
    page=post(client,'/account/billing_email',dict(csrf=csrf(page),email=USER+'@net.cryan.nz'));assert 'Billing email updated' in page
    home=Path('/srv/retro-isp/users')/USER/'index.html';original_home=home.read_text()
    try:
        page=post(client,'/account/homepage',dict(csrf=csrf(page),content='<HTML><BODY>Account editor verification</BODY></HTML>'))
        with urllib.request.urlopen('http://10.150.150.157:8086/~'+USER+'/',timeout=5) as response:assert b'Account editor verification' in response.read()
        assert home.stat().st_uid==__import__('pwd').getpwnam('retro-ftp').pw_uid
    finally:
        page=post(client,'/account/homepage',dict(csrf=csrf(page),content=original_home))
    temporary=secrets.token_urlsafe(15);changed=False
    try:
        result=post(client,'/account/password',dict(csrf=csrf(page),current=original,new=temporary,new2=temporary,mail_current=''))
        assert 'passwords updated' in result;changed=True;credentials_work(temporary)
        assert 'Account sign in' in get(client,'/account')
        page=login(client,temporary)
        result=post(client,'/account/password',dict(csrf=csrf(page),current=temporary,new=original,new2=original,mail_current=''))
        assert 'passwords updated' in result;changed=False;credentials_work(original)
    finally:
        if changed:
            # Private recovery material only if an unexpected test failure interrupts restore.
            recovery=Path('/var/lib/retro-isp-registration/account-test-recovery.json');recovery.write_text(json.dumps(dict(username=USER,password=temporary,original=original)));recovery.chmod(0o600)
    page=login(client,original);post(client,'/account/logout',dict(csrf=csrf(page)));assert 'Account sign in' in get(client,'/account')
    print('PASS: real login/isolation, CSRF, billing email, homepage edit/publication, shared mail+PPP+FTP password change and restoration, session invalidation and logout')
