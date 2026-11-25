#!/usr/bin/python3
"""Expand existing groups to 3,000 records, preserving every published URL."""
from pathlib import Path
from collections import Counter,deque
import json,base64,hashlib
ROOT=Path('/var/lib/retro-isp-news/archive-sources')
records=json.loads((ROOT/'selected-history.json').read_text())
seen={r['mid'] for r in records}
numbers={g:max(r['web_number'] for r in records if r['group']==g) for g in {r['group'] for r in records}}
groups=('comp.sys.ibm.pc.hardware.comm','alt.bbs','alt.bbs.lists','alt.bbs.allsysop','alt.bbs.doors')
queues=[deque(json.loads((ROOT/(g+'-bulk-selected.json')).read_text())) for g in groups]
added=Counter()
while len(records)<3000 and any(queues):
 for queue in queues:
  while queue:
   row=queue.popleft()
   if row['mid'] in seen:continue
   raw=base64.b64decode(row['article_b64'],validate=True)
   assert row['group'] in numbers and 1994<=row['year']<=1998
   assert len(raw)<=131072 and hashlib.sha256(raw).hexdigest()==row['sha256']
   numbers[row['group']]+=1;row['web_number']=numbers[row['group']]
   records.append(row);seen.add(row['mid']);added[row['group']]+=1
   break
  if len(records)==3000:break
assert len(records)==3000
p=ROOT/'expanded-history.json';p.write_text(json.dumps(records,indent=2)+'\n');assert p.stat().st_size<=64*1024*1024
report=dict(records=len(records),added=dict(added),bytes=p.stat().st_size,counts=dict(Counter(r['group'] for r in records)))
(ROOT/'bulk-history-report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
