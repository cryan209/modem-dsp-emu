#!/usr/bin/python3
"""Verify NNTP in a disposable group, not in user discussion groups."""
import email.utils
import json
from pathlib import Path
import secrets
import socket
import subprocess
import time

HOST = '10.150.150.157'
GROUP = 'xtra.verify' + secrets.token_hex(4)
IDS = []


def control(*args):
    subprocess.run(['runuser', '-u', 'news', '--', '/usr/sbin/ctlinnd', '-t', '20', *args], check=True, capture_output=True)


class News:
    def __enter__(self):
        self.sock = socket.create_connection((HOST, 119), timeout=10)
        self.file = self.sock.makefile('rwb', buffering=0)
        assert self.line().startswith(b'200 ')
        return self

    def line(self):
        line = self.file.readline(131072)
        if not line:
            raise RuntimeError('NNTP disconnected')
        return line.rstrip(b'\r\n')

    def command(self, text):
        self.file.write(text.encode('ascii') + b'\r\n')
        return self.line()

    def multiline(self):
        rows = []
        while True:
            line = self.line()
            if line == b'.':
                return rows
            rows.append(line[1:] if line.startswith(b'..') else line)

    def post(self, group, subject, reference=None):
        assert self.command('POST').startswith(b'340 ')
        mid = '<' + secrets.token_hex(12) + '@eicon420.net.cryan.nz>'
        article = ['From: ISP verification <verify@eicon420.net.cryan.nz>', 'Newsgroups: ' + group,
                   'Subject: ' + subject, 'Date: ' + email.utils.formatdate(localtime=False), 'Message-ID: ' + mid]
        if reference:
            article.append('References: ' + reference)
        article += ['', 'Disposable local NNTP verification.', '..Dot-stuffed line.', '.']
        self.file.write(('\r\n'.join(article) + '\r\n').encode('ascii'))
        response = self.line()
        if response.startswith(b'240 '):
            IDS.append(mid)
        return response, mid

    def __exit__(self, *args):
        self.file.close()
        self.sock.close()


control('newgroup', GROUP, 'y', 'retro-isp-verification')
try:
    with News() as n:
        assert n.command('MODE READER').startswith(b'200 ')
        assert n.command('LIST').startswith(b'215 ')
        listed = {x.split()[0].decode() for x in n.multiline()}
        expected = set(json.loads((Path(__file__).parent/'groups.json').read_text()))
        assert expected.issubset(listed), listed
        denied = n.command('GROUP control')
        assert denied[:3] in (b'411', b'502', b'480'), denied
        rejected, _ = n.post('xtra.announcements', 'Must be rejected')
        assert rejected.startswith(b'441 '), rejected
        first, mid = n.post(GROUP, 'Disposable verification')
        assert first.startswith(b'240 '), first
        second, reply = n.post(GROUP, 'Re: Disposable verification', mid)
        assert second.startswith(b'240 '), second
        for _ in range(20):
            status = n.command('GROUP ' + GROUP)
            if status.startswith(b'211 ') and int(status.split()[1]) == 2:
                break
            time.sleep(.1)
        assert int(status.split()[1]) == 2, status
        assert n.command('ARTICLE ' + mid).startswith(b'220 ')
        article = n.multiline()
        assert b'.Dot-stuffed line.' in article
        assert n.command('XOVER 1-').startswith(b'224 ')
        rows = n.multiline()
        assert len(rows) == 2 and any(mid.encode() in row and reply.encode() in row for row in rows), rows
        print('PASS: reader mode, group list, posting, article retrieval, dot-stuffing, threaded overview, announcements posting denial and control-group denial.')
finally:
    for mid in IDS:
        control('cancel', mid)
    control('rmgroup', GROUP)
    print('Removed disposable verification group and articles.')
