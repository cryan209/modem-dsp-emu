#!/usr/bin/python3
"""Publish the explicitly fictional fixture locally; stable IDs avoid repeats."""
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime
import json
from pathlib import Path
import re
import socket
import subprocess
import time

HERE = Path(__file__).resolve().parent
posts = json.loads((HERE/'demo-posts.json').read_text())
groups = json.loads((HERE/'groups.json').read_text())
ids = {p['key']: '<retro-demo-v'+str(p.get('revision',2))+'.'+p['key']+'@eicon420.net.cryan.nz>' for p in posts}
assert len(ids) == len(posts)
for p in posts:
    assert re.fullmatch('[a-z0-9-]+', p['key']) and p['group'] in groups
    assert p['parent'] is None or p['parent'] in ids

sock = socket.create_connection(('10.150.150.157',119),timeout=15)
wire = sock.makefile('rwb',buffering=0)
assert wire.readline().startswith(b'200 ')


def exists(mid):
    wire.write(('STAT '+mid+'\r\n').encode('ascii'))
    line = wire.readline()
    assert line[:3] in (b'223',b'430'), line
    return line.startswith(b'223')


now = datetime.now(timezone.utc)
batch = bytearray()
added = []
for i, p in enumerate(posts):
    if exists(ids[p['key']]):
        continue
    msg = EmailMessage(policy=SMTP)
    msg['Path'] = 'retro-demo!not-for-mail'
    address = re.sub('[^a-z0-9]+','.',p['author'].lower()).strip('.')
    msg['From'] = p['author']+' <'+address+'@retro.invalid>'
    msg['Newsgroups'] = p['group']
    msg['Subject'] = p['subject']
    msg['Date'] = format_datetime(now-timedelta(minutes=(len(posts)-i)*10))
    msg['Message-ID'] = ids[p['key']]
    msg['Organization'] = 'Xtra private retro ISP'
    if p.get('kind') == 'devlog':
        msg['X-Project-Devlog'] = 'sourced-notes-summary'
    else:
        msg['X-Retro-Demo'] = 'fictional-v2'
    if p['parent']:
        ancestors = []
        parent = p['parent']
        while parent:
            ancestors.insert(0, ids[parent])
            parent = next(x['parent'] for x in posts if x['key']==parent)
        msg['References'] = ' '.join(ancestors)
    msg.set_content(p['body']+'\n',charset='us-ascii')
    # rnews batch input is local LF format; it creates NNTP wire framing.
    raw = msg.as_bytes(policy=SMTP.clone(linesep='\n', max_line_length=998))
    assert len(raw)<65536
    batch.extend(('#! rnews %s\n'%len(raw)).encode('ascii')+raw)
    added.append(p['key'])

if batch:
    result = subprocess.run(['runuser','-u','news','--','/usr/bin/rnews'],input=bytes(batch),capture_output=True,timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr.decode())
# A reader can cache negative Message-ID lookups from before injection.
# Verify from a fresh connection rather than retaining those misses.
wire.close(); sock.close()
sock = socket.create_connection(('10.150.150.157',119),timeout=15)
wire = sock.makefile('rwb',buffering=0)
assert wire.readline().startswith(b'200 ')
for _ in range(30):
    missing = [key for key in ids if not exists(ids[key])]
    if not missing:
        break
    time.sleep(.2)
assert not missing, missing
# Check the indexed articles, not just successful injection.
counts = {}
for group in groups:
    wire.write(('GROUP '+group+'\r\n').encode('ascii'))
    line = wire.readline()
    assert line.startswith(b'211 '),line
    counts[group] = int(line.split()[1])
wire.write(b'ARTICLE '+ids['slow-r3'].encode()+b'\r\n')
assert wire.readline().startswith(b'220 ')
article = bytearray()
while True:
    line=wire.readline()
    if line==b'.\r\n':break
    if not line:raise RuntimeError('Disconnected')
    article.extend(line)
assert b'References:' in article and ids['slow'].encode() in article
assert b'Fictional retro ISP demo' not in article and b'[demo]' not in article
wire.write(b'QUIT\r\n');wire.close();sock.close()
record=Path('/var/lib/news/retro-demo-seed.json')
record.write_text(json.dumps(dict(version=2,verified_at=now.isoformat(),ids=ids,counts=counts),indent=2)+'\n')
record.chmod(0o640)
print(json.dumps(dict(added=len(added),verified=len(posts),group_counts=counts),indent=2))
