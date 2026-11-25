#!/usr/bin/python3
"""Use independent checksum-verified mailbox snapshots, not a rate-limit bypass."""
from pathlib import Path
from email.parser import BytesParser
from email import policy
from email.utils import parsedate_to_datetime
from collections import Counter
import urllib.request,json,hashlib,zipfile,concurrent.futures,re,base64
ROOT=Path('/var/lib/retro-isp-news/archive-sources')
PLAN={'usenet-comp':('comp.sys.ibm.pc.hardware.comm',),'usenet-alt':('alt.bbs','alt.bbs.lists','alt.bbs.allsysop','alt.bbs.doors')}
metadata={}
for ident in PLAN:
 p=ROOT/(ident+'-metadata.json')
 if p.exists():metadata[ident]=json.loads(p.read_text())
 else:
  metadata[ident]=json.load(urllib.request.urlopen('https://archive.org/metadata/'+ident,timeout=30));p.write_text(json.dumps(metadata[ident]))
parser=BytesParser(policy=policy.default)


def collect(job):
 ident,group=job;name=group+'.mbox.zip'
 info=next(x for x in metadata[ident]['files'] if x['name']==name)
 target=ROOT/name
 if not target.exists():
  part=target.with_suffix('.part')
  with urllib.request.urlopen('https://archive.org/download/'+ident+'/'+name,timeout=60) as source,part.open('wb') as out:
   while chunk:=source.read(1024*1024):out.write(chunk)
  part.rename(target)
 digest=hashlib.sha1()
 with target.open('rb') as source:
  while chunk:=source.read(1024*1024):digest.update(chunk)
 assert digest.hexdigest()==info['sha1'],name
 records=[];years=Counter();skipped=Counter()
 def consume(raw):
  if not raw:return
  header=raw.split(b'\n\n',1)[0]
  match=re.search(rb'(?im)^Date:\s*(.+)',header)
  if not match:return
  try:date=parsedate_to_datetime(match[1].decode('ascii','replace'))
  except (TypeError,ValueError,OverflowError):return
  years[date.year]+=1
  if not 1994<=date.year<=1998 or len(records)>=800:return
  if len(raw)>115000:skipped['oversized']+=1;return
  message=parser.parsebytes(raw)
  if message.get('Control') or message.get('Supersedes') or str(message.get('X-No-Archive','')).strip().lower()=='yes':skipped['excluded']+=1;return
  mid=str(message.get('Message-ID','')).strip()
  if not re.fullmatch(r'<[^<>\s]+>',mid):skipped['invalid-id']+=1;return
  if any(row['mid']==mid for row in records):return
  original=str(message.get('Newsgroups',group))
  for field in ('Path','Newsgroups','Xref','Lines','Bytes'):
   while field in message:del message[field]
  message['Path']='retro-history!not-for-mail';message['Newsgroups']='archive.'+group
  message['X-Original-Newsgroups']=original
  message['X-Archive-Source']='https://archive.org/download/'+ident+'/'+name
  message['X-Archive-Format']='original mailbox article; available RFC headers retained'
  result=message.as_bytes(policy=policy.SMTP.clone(linesep='\n',max_line_length=998))
  if len(result)>131072:skipped['oversized']+=1;return
  records.append(dict(mid=mid,group='archive.'+group,year=date.year,article_b64=base64.b64encode(result).decode(),sha256=hashlib.sha256(result).hexdigest()))
 with zipfile.ZipFile(target) as archive,archive.open(group+'.mbox') as source:
  buffer=bytearray();oversized=False
  for line in source:
   if line.startswith(b'From '):
    if not oversized:consume(bytes(buffer))
    buffer=bytearray();oversized=False
   elif not oversized:
    buffer.extend(line)
    if len(buffer)>256000:buffer.clear();oversized=True;skipped['oversized-record']+=1
  if not oversized:consume(bytes(buffer))
 report=dict(group=group,selected=len(records),years=dict(sorted(years.items())),skipped=dict(skipped))
 (ROOT/(group+'-bulk-report.json')).write_text(json.dumps(report,indent=2))
 (ROOT/(group+'-bulk-selected.json')).write_text(json.dumps(records))
 print(json.dumps(report),flush=True)
 return report


jobs=[(ident,group) for ident,groups in PLAN.items() for group in groups]
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as workers:
 futures={workers.submit(collect,job):job for job in jobs}
 for future in concurrent.futures.as_completed(futures):
  try:future.result()
  except Exception as error:print('Snapshot failure:',futures[future],type(error).__name__,str(error)[:120],flush=True)
