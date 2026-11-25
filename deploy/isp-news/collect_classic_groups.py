#!/usr/bin/python3
"""Stage 1994-98 articles from more checksum-verified archive.org snapshots.

Same rules as collect_bulk_history.py: SHA1 check against archive.org
metadata, Control/Supersedes/X-No-Archive excluded, original Message-ID
required, available RFC headers retained, routing headers remapped to
archive.<group>, and at most PER_GROUP records per group.
"""
from pathlib import Path
from email.parser import BytesParser
from email import policy
from email.utils import parsedate_to_datetime
from collections import Counter
import urllib.request,json,hashlib,zipfile,concurrent.futures,re,base64,sys
ROOT=Path('/var/lib/retro-isp-news/archive-sources')
# Batch 1 took the first PER_GROUP in-range posts in mailbox order.
# Batch 2 keeps up to PER_GROUP per year, then takes turns across years,
# so a dense 1994-95 run cannot crowd out the sparser 1996-98 posts.
# Batch 3 rescans the same snapshots for 1990-93: up to 60 per year, 240
# per group, written as *-classic3-selected.json beside the earlier files.
# Fields: per-year (or total) cap, balanced, plan, first/last year, total cap.
BATCHES={
 '1':(300,False,{
  'usenet-comp':('comp.sys.amiga.misc','comp.sys.cbm','comp.sys.apple2','comp.sys.mac.comm',
                 'comp.os.linux.setup','comp.os.ms-windows.win95.setup','comp.protocols.ppp',
                 'comp.infosystems.www.browsers.ms-windows','comp.dcom.modems.cable','comp.bbs.waffle'),
  'usenet-alt':('alt.folklore.computers','alt.sysadmin.recovery','alt.winsock','alt.winsock.trumpet',
                'alt.games.doom','alt.irc','alt.bbs.wildcat','alt.bbs.pcboard'),
  'usenet-rec':('rec.games.computer.doom.playing','rec.humor.funny')},1994,1998,300),
 '2':(200,True,{
  'usenet-comp':('comp.sys.amiga.games','comp.sys.mac.hardware','comp.sys.mac.misc','comp.os.linux.misc',
                 'comp.os.linux.announce','comp.os.msdos.misc','comp.sys.atari.st',
                 'comp.sys.ibm.pc.games.action','comp.sys.ibm.pc.games.misc','comp.sys.ibm.pc.soundcard.misc',
                 'comp.sys.ibm.pc.hardware.video','comp.sys.ibm.pc.hardware.misc','comp.os.os2.misc',
                 'comp.os.ms-windows.networking.tcp-ip','comp.mail.misc','comp.bbs.misc',
                 'comp.infosystems.www.browsers.misc'),
  'usenet-alt':('alt.internet.services','alt.fan.warlord','alt.bbs.searchlight','alt.bbs.majorbbs'),
  'usenet-rec':('rec.games.computer.doom.misc','rec.arts.sf.written','rec.games.computer.ultima.online')},1994,1998,200),
 '3':(60,True,{
  'usenet-comp':('comp.dcom.modems','comp.dcom.isdn','comp.dcom.fax','comp.sys.atari.st','comp.sys.mac.hardware',
                 'comp.sys.apple2','comp.os.msdos.misc','comp.mail.misc','comp.protocols.ppp','comp.bbs.misc',
                 'comp.bbs.waffle','comp.os.linux.announce','comp.sys.ibm.pc.games.misc',
                 'comp.sys.ibm.pc.hardware.comm','comp.sys.ibm.pc.hardware.misc'),
  'usenet-alt':('alt.bbs','alt.bbs.allsysop','alt.bbs.lists','alt.bbs.doors','alt.bbs.majorbbs','alt.bbs.pcboard',
                'alt.bbs.wildcat','alt.bbs.searchlight','alt.fan.warlord','alt.internet.services','alt.irc','alt.winsock'),
  'usenet-rec':('rec.humor.funny',),
  'usenet-nz':('nz.comp',)},1990,1993,240),
}
batch=sys.argv[1] if len(sys.argv)>1 else '1'
PER_GROUP,BALANCED,PLAN,FIRST,LAST,TOTAL=BATCHES[batch]
SUFFIX='classic' if batch in ('1','2') else 'classic'+batch
only=set(sys.argv[2:])
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
 buckets={y:[] for y in (0,*range(FIRST,LAST+1))};mids=set();years=Counter();skipped=Counter()
 def consume(raw):
  if not raw:return
  header=raw.split(b'\n\n',1)[0]
  match=re.search(rb'(?im)^Date:\s*(.+)',header)
  if not match:return
  try:date=parsedate_to_datetime(match[1].decode('ascii','replace'))
  except (TypeError,ValueError,OverflowError):return
  years[date.year]+=1
  if not FIRST<=date.year<=LAST:return
  bucket=buckets[date.year if BALANCED else 0]
  if len(bucket)>=PER_GROUP:return
  if len(raw)>115000:skipped['oversized']+=1;return
  message=parser.parsebytes(raw)
  if message.get('Control') or message.get('Supersedes') or str(message.get('X-No-Archive','')).strip().lower()=='yes':skipped['excluded']+=1;return
  mid=str(message.get('Message-ID','')).strip()
  if not re.fullmatch(r'<[^<>\s]+>',mid):skipped['invalid-id']+=1;return
  if mid in mids:return
  original=str(message.get('Newsgroups',group))
  for field in ('Path','Newsgroups','Xref','Lines','Bytes','Followup-To','Approved'):
   while field in message:del message[field]
  message['Path']='retro-history!not-for-mail';message['Newsgroups']='archive.'+group
  message['X-Original-Newsgroups']=original
  message['X-Archive-Source']='https://archive.org/download/'+ident+'/'+name
  message['X-Archive-Format']='original mailbox article; available RFC headers retained'
  try:result=message.as_bytes(policy=policy.SMTP.clone(linesep='\n',max_line_length=998))
  except Exception:skipped['unserialisable']+=1;return
  if len(result)>131072:skipped['oversized']+=1;return
  mids.add(mid)
  bucket.append(dict(mid=mid,group='archive.'+group,year=date.year,article_b64=base64.b64encode(result).decode(),sha256=hashlib.sha256(result).hexdigest()))
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
 records=[];queues=[b for b in buckets.values() if b]
 while len(records)<TOTAL and any(queues):
  for queue in queues:
   if queue and len(records)<TOTAL:records.append(queue.pop(0))
 records.sort(key=lambda row:row['year'])
 report=dict(group=group,selected=len(records),years=dict(sorted(years.items())),skipped=dict(skipped))
 (ROOT/(group+'-'+SUFFIX+'-report.json')).write_text(json.dumps(report,indent=2))
 (ROOT/(group+'-'+SUFFIX+'-selected.json')).write_text(json.dumps(records))
 print(json.dumps(report),flush=True)
 return report


jobs=[(ident,group) for ident,groups in PLAN.items() for group in groups if not only or group in only]
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as workers:
 futures={workers.submit(collect,job):job for job in jobs}
 for future in concurrent.futures.as_completed(futures):
  try:future.result()
  except Exception as error:print('Snapshot failure:',futures[future],type(error).__name__,str(error)[:120],flush=True)
