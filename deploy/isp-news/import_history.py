#!/usr/bin/python3
"""Inject the selected historical articles; stable IDs make reruns safe."""
from pathlib import Path
from datetime import datetime,timezone
from collections import Counter
import base64,hashlib,json,socket,subprocess,time
ROOT=Path('/var/lib/retro-isp-news/archive-sources')
records=json.loads((ROOT/'selected-history.json').read_text())
ALLOWED={name for name in json.loads(Path('/usr/local/lib/retro-isp-news/groups.json').read_text()) if name.startswith('archive.')}
assert 0<len(records)<=20000


def connect():
    sock=socket.create_connection(('10.150.150.157',119),timeout=15)
    wire=sock.makefile('rwb',buffering=0)
    assert wire.readline().startswith(b'200 ')
    return sock,wire


def exists(wire,mid):
    assert '\r' not in mid and '\n' not in mid
    wire.write(('STAT '+mid+'\r\n').encode('ascii'))
    line=wire.readline()
    assert line[:3] in (b'223',b'430'),line
    return line.startswith(b'223 ')


sock,wire=connect();pending=[];batches=[];batch=bytearray()
for record in records:
    assert record['group'] in ALLOWED and 1990<=record['year']<=2002
    raw=base64.b64decode(record['article_b64'],validate=True)
    assert len(raw)<=131072 and hashlib.sha256(raw).hexdigest()==record['sha256']
    if exists(wire,record['mid']):continue
    pending.append(record['mid'])
    batch.extend(('#! rnews %s\n'%len(raw)).encode()+raw)
    if len(batch)>=1024*1024:batches.append(bytes(batch));batch.clear()
if batch:batches.append(bytes(batch))
wire.close();sock.close()
for batch in batches:
    run=subprocess.run(['runuser','-u','news','--','/usr/bin/rnews'],input=batch,capture_output=True,timeout=45)
    assert run.returncode==0,run.stderr.decode()
for attempt in range(12):
    sock,wire=connect()
    missing=[record['mid'] for record in records if not exists(wire,record['mid'])]
    wire.close();sock.close()
    if not missing:break
    time.sleep(.5)
assert not missing,('Articles not indexed',len(missing),missing[:5])
manifest=dict(imported_at=datetime.now(timezone.utc).isoformat(),added=len(pending),verified=len(records),
              counts=dict(Counter(record['group'] for record in records)),
              records=[{k:v for k,v in record.items() if k!='article_b64'} for record in records])
(ROOT/'import-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({k:v for k,v in manifest.items() if k!='records'},indent=2))
