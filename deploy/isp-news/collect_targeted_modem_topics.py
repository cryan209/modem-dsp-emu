#!/usr/bin/python3
"""Append bounded, provenance-marked 1994-2002 conversations to the corpus."""
from pathlib import Path
from bs4 import BeautifulSoup
from email.message import EmailMessage
from email.parser import BytesParser
from email import policy
from email.utils import format_datetime,parsedate_to_datetime
from datetime import datetime,timezone
from collections import Counter
import urllib.request,hashlib,json,re,time
ROOT=Path('/var/lib/retro-isp-news/archive-sources')
FILE=ROOT/'selected-history.json'
records=json.loads(FILE.read_text());existing={r['mid'] for r in records}
parser=BytesParser(policy=policy.default)
# Freeze existing web article numbers before appending earlier source dates.
for group in {r['group'] for r in records}:
    ordered=[r for r in records if r['group']==group]
    if not all('web_number' in r for r in ordered):
        ordered.sort(key=lambda r:parsedate_to_datetime(parser.parsebytes(__import__('base64').b64decode(r['article_b64']))['Date']).timestamp())
        for i,r in enumerate(ordered,1):r['web_number']=i
next_number={g:max(r['web_number'] for r in records if r['group']==g) for g in {r['group'] for r in records}}
GROUPS=('nz.wanted','nz.forsale','nz.politics',
        'comp.sys.ibm.pc.hardware.comm','comp.os.ms-windows.win95.misc',
        'rec.games.computer.quake.misc','rec.games.computer.quake.playing','rec.games.computer.quake.servers',
        'alt.bbs','alt.bbs.lists','alt.bbs.allsysop','alt.bbs.doors')


def fetch(url,name):
    p=ROOT/name
    if not p.exists():
        request=urllib.request.Request(url,headers={'User-Agent':'RetroISP-Historical-Import/1.0'})
        with urllib.request.urlopen(request,timeout=25) as response:raw=response.read(4000001)
        if len(raw)>4000000:raise ValueError('Page too large')
        p.write_bytes(raw);time.sleep(.8)
    return p.read_bytes()


def conversation(group,thread):
    url='https://groups.google.com/g/'+group+'/c/'+thread
    soup=BeautifulSoup(fetch(url,'google-'+group+'-'+thread+'.html'),'html.parser')
    found=[]
    for section in soup.select('section[data-author][data-doc-id]'):
        body=section.select_one('.ptW7te')
        if body is None:continue
        header=BeautifulSoup(str(section),'html.parser');header.select_one('.ptW7te').decompose()
        date=re.search(r'([A-Z][a-z]{2}) (\d{1,2}), (199[4-9]|200[0-2])',header.get_text(' ',strip=True))
        if not date:continue
        day=datetime.strptime(date.group(),'%b %d, %Y').replace(hour=12,tzinfo=timezone.utc)
        for tag in body.select('script,style,button,[role=button]'):tag.decompose()
        for tag in body.find_all('br'):tag.replace_with('\n')
        for tag in body.find_all(['p','div','blockquote','pre']):tag.append('\n')
        text=body.get_text().strip()
        if not text or len(text.encode())>100000:continue
        doc=section['data-doc-id'];mid='<gg-'+hashlib.sha256((group+'/'+thread+'/'+doc).encode()).hexdigest()[:32]+'@archive.retro.invalid>'
        m=EmailMessage(policy=policy.SMTP.clone(max_line_length=998))
        m['From']=section['data-author'].replace('\r',' ').replace('\n',' ')+' <unavailable@archive.retro.invalid>'
        m['Subject']=(section.get('data-subject') or group).replace('\r',' ').replace('\n',' ')
        m['Date']=format_datetime(day);m['Message-ID']=mid;m['Newsgroups']='archive.'+group
        m['X-Original-Newsgroups']=group;m['X-Archive-Source']=url+'/m/'+doc
        m['X-Archive-Format']='Google Groups displayed-text transcription'
        m['X-Archive-Date-Precision']='displayed calendar day; time normalized to 12:00 UTC'
        m['X-Archive-Threading']='conversation grouping; original References unavailable'
        m['X-Archive-Original-Header-Status']='RFC headers unavailable; archive-local Message-ID; author email not exposed'
        m['Path']='retro-history!not-for-mail';m.set_content(text,charset='utf-8',cte='8bit')
        found.append((m,day.year))
    root=str(found[0][0]['Message-ID']) if found else None
    import base64
    result=[]
    for i,(m,year) in enumerate(found):
        if i:m['References']=root
        raw=m.as_bytes(policy=policy.SMTP.clone(linesep='\n',max_line_length=998))
        if len(raw)>131072:continue
        result.append(dict(mid=str(m['Message-ID']),group='archive.'+group,year=year,article_b64=base64.b64encode(raw).decode(),sha256=hashlib.sha256(raw).hexdigest()))
    return result


TARGETS=[(row['group'],row['thread']) for row in json.loads((ROOT/'modem-browser-targeted.json').read_text())]
added=Counter();failures=[]
for group,thread in TARGETS:
 try:
  candidates=conversation(group,thread)
  for row in candidates:
   if row['mid'] in existing:continue
   dest=row['group'];next_number[dest]=next_number.get(dest,0)+1;row['web_number']=next_number[dest]
   records.append(row);existing.add(row['mid']);added[group]+=1
  print(group,thread,len(candidates),'visible period records',flush=True)
 except urllib.error.HTTPError as error:
  failures.append([group,thread,error.code])
  if error.code==429:break
 except Exception as error:failures.append([group,thread,type(error).__name__,str(error)[:150]])
assert len(records)<=5000
output=ROOT/'expanded-history.json';output.write_text(json.dumps(records,indent=2)+'\n');assert output.stat().st_size<=64*1024*1024
report=dict(added=dict(added),records=len(records),failures=failures)
(ROOT/'modem-targeted-report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report),flush=True)
