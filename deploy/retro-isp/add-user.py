#!/usr/bin/python3
"""Provision a virtual FTP account and starter personal homepage."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import sys
import tempfile
import fcntl


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('username'); ap.add_argument('--password-stdin', action='store_true'); args = ap.parse_args()
    if not re.fullmatch('[a-z][a-z0-9_-]{0,31}', args.username):
        raise SystemExit('Use a lowercase account name starting with a letter')
    password = sys.stdin.readline().rstrip('\r\n') if args.password_stdin else secrets.token_urlsafe(15)
    if len(password) < 6:
        raise SystemExit('Password must be at least 6 characters')
    account = pwd.getpwnam('retro-ftp'); config = Path('/etc/retro-isp/ftp-users.json')
    with config.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        users = json.loads(config.read_text()) if config.exists() else {}
        if args.username in users:
            raise SystemExit('Account already exists; no changes made')
        salt = secrets.token_bytes(16)
        users[args.username] = dict(salt=salt.hex(), hash=hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex())
        home = Path('/srv/retro-isp/users')/args.username; home.mkdir(mode=0o755)
        index = home/'index.html'
        index.write_text('<HTML><HEAD><TITLE>'+args.username+"'s homepage</TITLE></HEAD><BODY BGCOLOR=\"#FFFFCC\" TEXT=\"#000080\"><CENTER><H1>Welcome to "+args.username+"'s homepage!</H1><HR><H2>UNDER CONSTRUCTION</H2><P>You are visitor number ... well, hello anyway.</P><P>Upload your own index.html using your personal FTP login.</P><HR><A HREF=\"/homepages\">Back to the neighbourhood</A></CENTER></BODY></HTML>")
        os.chown(home, account.pw_uid, account.pw_gid); os.chown(index, account.pw_uid, account.pw_gid)
        fd, name = tempfile.mkstemp(dir=config.parent, prefix='ftp-users-')
        with os.fdopen(fd, 'w') as out:
            json.dump(users, out); os.fchmod(out.fileno(), 0o640); os.fchown(out.fileno(), 0, account.pw_gid)
        os.replace(name, config)
    print(json.dumps(dict(username=args.username, password=password)))


if __name__ == '__main__':
    main()
