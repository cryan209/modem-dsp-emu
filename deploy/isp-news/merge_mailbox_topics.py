#!/usr/bin/python3
"""Append the verified mailbox selections without replacing existing records."""
from pathlib import Path
from collections import Counter
import json,base64,hashlib
ROOT=Path('/var/lib/retro-isp-news/archive-sources')
records=json.loads((ROOT/'selected-history.json').read_text());seen={r['mid'] for r in records}
next_number={g:max(r['web_number'] for r in records if r['group']==g) for g in {r['group'] for r in records}}
groups=('comp.sys.ibm.pc.hardware.comm','comp.os.ms-windows.win95.misc',
        'rec.games.computer.quake.misc','rec.games.computer.quake.playing','rec.games.computer.quake.servers',
        'alt.bbs','alt.bbs.lists','alt.bbs.allsysop','alt.bbs.doors')
added=Counter();duplicates=Counter()
for group in groups:
    rows=json.loads((ROOT/(group+'-mailbox-selected.json')).read_text())
    for row in rows:
        assert row['group']=='archive.'+group and 1994<=row['year']<=1998
        raw=base64.b64decode(row['article_b64'],validate=True)
        assert len(raw)<=131072 and hashlib.sha256(raw).hexdigest()==row['sha256']
        if row['mid'] in seen:duplicates[group]+=1;continue
        dest=row['group'];next_number[dest]=next_number.get(dest,0)+1
        row['web_number']=next_number[dest];records.append(row);seen.add(row['mid']);added[group]+=1
counts=Counter(r['group'] for r in records)
missing=[g for g in groups if not counts['archive.'+g]]
assert len(records)<=5000
p=ROOT/'expanded-history.json';p.write_text(json.dumps(records,indent=2)+'\n');assert p.stat().st_size<=64*1024*1024
report=dict(added=dict(added),crosspost_duplicates=dict(duplicates),records=len(records),missing=missing,
            groups={g:dict(count=counts[g],years=sorted({r['year'] for r in records if r['group']==g})) for g in sorted(counts)})
(ROOT/'expanded-history-report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
assert not missing,'Need more unique records before publishing every requested category'
