#!/usr/bin/python3
"""Harvest a bounded 1994-98 batch with source provenance; never invent headers."""
from pathlib import Path
from bs4 import BeautifulSoup
from email.parser import BytesParser
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime, parsedate_to_datetime
from datetime import datetime, timezone
import urllib.request
import hashlib
import json
import re
import time
import zipfile

ROOT=Path('/var/lib/retro-isp-news/archive-sources')
ROOT.mkdir(parents=True,exist_ok=True)
records=[]
parser=BytesParser(policy=policy.default)


def fetch(url,filename):
    target=ROOT/filename
    if not target.exists():
        request=urllib.request.Request(url,headers={'User-Agent':'RetroISP-Historical-Import/1.0'})
        with urllib.request.urlopen(request,timeout=25) as response:
            raw=response.read(4000001)
        if len(raw)>4000000:raise ValueError('Source page too large')
        target.write_bytes(raw)
        time.sleep(.6)
    return target.read_bytes()


search_url='https://groups.google.com/g/nz.general/search?q=after%3A1995-01-01%20before%3A1997-01-01'
search=BeautifulSoup(fetch(search_url,'google-nz-general-search.html'),'html.parser')
threads=[]
for anchor in search.select('a[href]'):
    value=anchor['href']
    match=re.search(r'/g/nz\.general/c/([A-Za-z0-9_-]+)',value)
    if match:
        key=('nz.general',match[1])
        if key not in threads:threads.append(key)
threads=threads[:12]+[('nz.comp','VjnRo9i87j4'),('nz.comp','zCybUbW-y1g')]
print('Selected',len(threads),'Google archive conversations',flush=True)
for group,thread in threads:
    url='https://groups.google.com/g/'+group+'/c/'+thread
    try:
        raw=fetch(url,'google-'+group+'-'+thread+'.html')
    except Exception as error:
        print('Source unavailable:',group,thread,type(error).__name__,flush=True)
        continue
    soup=BeautifulSoup(raw,'html.parser')
    pending=[]
    for section in soup.select('section[data-author][data-doc-id]'):
        body=section.select_one('.ptW7te')
        if body is None:continue
        header=BeautifulSoup(str(section),'html.parser')
        header.select_one('.ptW7te').decompose()
        visible=header.get_text(' ',strip=True)
        match=re.search(r'([A-Z][a-z]{2}) (\d{1,2}), (199[4-8])',visible)
        if not match:continue
        day=datetime.strptime(match.group(),'%b %d, %Y').replace(hour=12,tzinfo=timezone.utc)
        for tag in body.find_all(['script','style']):tag.decompose()
        for br in body.find_all('br'):br.replace_with('\n')
        for tag in body.find_all(['p','div','blockquote','pre']):tag.append('\n')
        text=body.get_text().strip()
        if not text or len(text.encode('utf-8'))>115000:continue
        doc=section['data-doc-id']
        mid='<gg-'+hashlib.sha256((group+'/'+thread+'/'+doc).encode()).hexdigest()[:32]+'@archive.retro.invalid>'
        message=EmailMessage(policy=policy.SMTP.clone(max_line_length=998))
        name=section['data-author'].replace('\r',' ').replace('\n',' ')
        message['From']=name+' <unavailable@archive.retro.invalid>'
        message['Subject']=section.get('data-subject') or soup.title.get_text()
        message['Date']=format_datetime(day)
        message['Message-ID']=mid
        message['Newsgroups']='archive.'+group
        message['X-Original-Newsgroups']=group
        message['X-Archive-Source']=url+'/m/'+doc
        message['X-Archive-Format']='Google Groups displayed-text transcription'
        message['X-Archive-Date-Precision']='displayed calendar day; time normalized to 12:00 UTC'
        message['X-Archive-Threading']='conversation grouping; original References unavailable'
        message['X-Archive-Original-Header-Status']='RFC headers unavailable; archive-local Message-ID; author email not exposed'
        message['Path']='retro-history!not-for-mail'
        message.set_content(text,charset='utf-8',cte='8bit')
        pending.append(dict(mid=mid,group='archive.'+group,year=day.year,message=message))
    if pending:
        root=pending[0]['mid']
        for record in pending[1:]:record['message']['References']=root
        for record in pending:
            record['raw']=record.pop('message').as_bytes(policy=policy.SMTP.clone(linesep='\n',max_line_length=998))
        records.extend(pending)
    print(group,thread,len(pending),'dated records',flush=True)

# The original mbox supplies RFC headers for these, unlike the Google view.
mbox=ROOT/'comp.dcom.modems.mbox.zip'
if mbox.exists():
    def consume(raw):
        if not raw or len(raw)>115000:return
        message=parser.parsebytes(raw)
        try:day=parsedate_to_datetime(message['Date'])
        except (ValueError,TypeError,IndexError):return
        if not 1994<=day.year<=1998:return
        mid=str(message.get('Message-ID','')).strip()
        if not re.fullmatch(r'<[^<>\s]+>',mid):return
        if message.get('Control') or message.get('Supersedes') or str(message.get('X-No-Archive','')).lower()=='yes':return
        for field in ('Path','Newsgroups','Xref','Lines','Bytes'):
            while field in message:del message[field]
        message['Path']='retro-history!not-for-mail'
        message['Newsgroups']='archive.comp.dcom.modems'
        message['X-Original-Newsgroups']='comp.dcom.modems'
        message['X-Archive-Source']='https://archive.org/download/usenet-comp/comp.dcom.modems.mbox.zip'
        message['X-Archive-Format']='original mbox article; original Message-ID/Date/From/References retained'
        out=message.as_bytes(policy=policy.SMTP.clone(linesep='\n',max_line_length=998))
        if len(out)<=131072:records.append(dict(mid=mid,group='archive.comp.dcom.modems',year=day.year,raw=out))
    with zipfile.ZipFile(mbox) as z,z.open('comp.dcom.modems.mbox') as source:
        buffer=bytearray();oversize=False
        for line in source:
            if line.startswith(b'From '):
                if not oversize:consume(bytes(buffer))
                buffer=bytearray();oversize=False
            elif not oversize:
                buffer.extend(line)
                if len(buffer)>115000:oversize=True;buffer.clear()
        if not oversize:consume(bytes(buffer))
seen=set();selected=[]
for record in records:
    if record['mid'] not in seen and len(record['raw'])<=131072:
        seen.add(record['mid']);selected.append(record)
assert selected,'No vintage records found; refusing an empty import'
import base64
out=[]
for record in selected:
    raw=record.pop('raw');record['article_b64']=base64.b64encode(raw).decode()
    record['sha256']=hashlib.sha256(raw).hexdigest();out.append(record)
(ROOT/'selected-history.json').write_text(json.dumps(out,indent=2)+'\n')
from collections import Counter
print('Prepared',len(out),'articles;',dict(Counter(r['group'] for r in out)),'years',dict(Counter(r['year'] for r in out)),flush=True)
