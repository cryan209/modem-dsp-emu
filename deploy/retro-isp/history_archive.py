"""Read-only, bounded historical corpus; independent of live NNTP policies."""
from pathlib import Path
from functools import lru_cache
from email.parser import BytesParser
from email import policy
from email.utils import parsedate_to_datetime
import base64,hashlib,json,re

PATH=Path(__file__).resolve().parent/'history'/'selected-history.json'
CATALOG=PATH.parent/'groups.json'
DESCRIPTIONS={
    'archive.nz.general':'Historical nz.general: 1996-97',
    'archive.nz.comp':'Historical nz.comp: 1995-96',
    'archive.comp.dcom.modems':'Historical comp.dcom.modems: 1997',
}
if CATALOG.exists():
    assert CATALOG.stat().st_size<=16384
    DESCRIPTIONS=json.loads(CATALOG.read_text())
assert len(DESCRIPTIONS)<=64 and all(re.fullmatch(r'archive\.[a-z0-9.-]+',name) for name in DESCRIPTIONS)
GROUPS=set(DESCRIPTIONS)


@lru_cache(maxsize=1)
def load():
    if PATH.stat().st_size>64*1024*1024:raise ValueError('Archive too large')
    data=json.loads(PATH.read_text());assert 0<len(data)<=20000
    grouped={name:[] for name in GROUPS}
    seen=set()
    for row in data:
        assert row['group'] in GROUPS and row['mid'] not in seen
        raw=base64.b64decode(row['article_b64'],validate=True)
        assert len(raw)<=131072 and hashlib.sha256(raw).hexdigest()==row['sha256']
        message=BytesParser(policy=policy.default).parsebytes(raw)
        assert str(message['Message-ID']).strip()==row['mid']
        seen.add(row['mid'])
        grouped[row['group']].append(dict(raw=raw,message=message,epoch=parsedate_to_datetime(message['Date']).timestamp(),web_number=row.get('web_number')))
    for rows in grouped.values():
        if any(row['web_number'] is not None for row in rows):
            assert all(isinstance(row['web_number'],int) for row in rows)
            assert sorted(row['web_number'] for row in rows)==list(range(1,len(rows)+1))
            rows.sort(key=lambda row:row['web_number'])
        else:
            rows.sort(key=lambda row:row['epoch'])
    return grouped


class Reader:
    def group(self,name):
        if name not in GROUPS:raise ValueError('Unknown archive group')
        self.rows=load()[name]
        return len(self.rows),1,len(self.rows)

    def overview(self,low,high):
        result=[]
        for i in range(max(1,low),min(len(self.rows),high)+1):
            message=self.rows[i-1]['message']
            result.append(dict(number=i,subject=str(message.get('Subject','')),author=str(message.get('From','')),
                               date=str(message.get('Date','')),mid=str(message['Message-ID']),
                               refs=re.findall(r'<[^<>\s]+>',str(message.get('References','')))))
        return result

    def command(self,text,codes):
        match=re.fullmatch(r'ARTICLE ([0-9]+)',text)
        if not match or not 1<=int(match[1])<=len(self.rows):raise ValueError('Article unavailable')
        self.article=self.rows[int(match[1])-1]['raw']
        return b'220 Historical article'

    def multiline(self,maximum=131072):
        if len(self.article)>maximum:raise ValueError('Article too large')
        return self.article.splitlines()
