#!/usr/bin/python3
"""Append the staged classic-group records, preserving every published URL."""
from pathlib import Path
from collections import Counter
import json,base64,hashlib,re,sys
ROOT=Path('/var/lib/retro-isp-news/archive-sources')
LIMIT=20000
records=json.loads((ROOT/'selected-history.json').read_text())
seen={r['mid'] for r in records}
numbers=Counter()
for r in records:numbers[r['group']]=max(numbers[r['group']],r['web_number'])
added=Counter();missing=[]
for path in sorted(ROOT.glob('*-classic*-selected.json')):
 for row in json.loads(path.read_text()):
  if row['mid'] in seen:added['duplicate']+=1;continue
  raw=base64.b64decode(row['article_b64'],validate=True)
  # INN rejects these originals: missing From/Subject, two '@' in the
  # Message-ID (or a quoted X.400 one), or an asctime-style Date without the RFC day-month-year form.
  head=raw.split(b'\n\n',1)[0]
  date=re.search(rb'(?im)^Date:[ \t]*(.*)$',head)
  if (not re.search(rb'(?im)^From:[ \t]*\S',head) or not re.search(rb'(?im)^Subject:[ \t]*\S',head)
      or row['mid'].count('@')!=1 or not date or '"' in row['mid'] or '\\' in row['mid']
      or not re.search(rb'(?im)^Message-ID:[ \t]*'+re.escape(row['mid'].encode())+rb'[ \t]*$',head)
      or not re.search(rb'\b\d{1,2}[ -][A-Za-z]{3}[ -]\d{2,4}\b',date[1])):
   added['malformed']+=1;continue
  assert row['group'].startswith('archive.') and 1990<=row['year']<=1998
  assert len(raw)<=131072 and hashlib.sha256(raw).hexdigest()==row['sha256']
  numbers[row['group']]+=1;row['web_number']=numbers[row['group']]
  records.append(row);seen.add(row['mid']);added[row['group']]+=1
assert len(records)<=LIMIT,len(records)
groups={r['group'] for r in records};assert len(groups)<=64
p=ROOT/'expanded-history.json';p.write_text(json.dumps(records,indent=2)+'\n');assert p.stat().st_size<=64*1024*1024
report=dict(records=len(records),groups=len(groups),added=dict(added),bytes=p.stat().st_size)
(ROOT/'classic-history-report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
