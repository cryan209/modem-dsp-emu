#!/usr/bin/python3
"""Validate and stage an approved archive expansion without policy changes."""
from pathlib import Path
from datetime import datetime
import json,hashlib,base64,shutil,subprocess,re
ROOT=Path('/var/lib/retro-isp-news/archive-sources')
old=json.loads((ROOT/'selected-history.json').read_text())
new=json.loads((ROOT/'expanded-history.json').read_text())
assert 0<len(new)<=20000 and (ROOT/'expanded-history.json').stat().st_size<=64*1024*1024
mapping={row['mid']:row for row in new};assert len(mapping)==len(new)
for row in old:assert row['mid'] in mapping and row['sha256']==mapping[row['mid']]['sha256']
for row in new:
 assert re.fullmatch(r'archive\.[a-z0-9.-]+',row['group']) and 1990<=row['year']<=2002
 raw=base64.b64decode(row['article_b64'],validate=True)
 assert len(raw)<=131072 and hashlib.sha256(raw).hexdigest()==row['sha256']
catalog={}
for group in sorted({row['group'] for row in new}):
 rows=[row for row in new if row['group']==group]
 assert sorted(row['web_number'] for row in rows)==list(range(1,len(rows)+1))
 years=sorted({row['year'] for row in rows})
 span=str(years[0]) if len(years)==1 else '%s-%s'%(years[0],years[-1])
 catalog[group]='Historical '+group.removeprefix('archive.')+': '+span
backup=Path('/var/backups/isp-more-archives-'+datetime.now().strftime('%Y%m%d-%H%M%S'));backup.mkdir(mode=0o700)
for path in (ROOT/'selected-history.json',Path('/usr/local/lib/retro-isp-news/groups.json'),Path('/var/lib/news/newsgroups'),Path('/usr/local/lib/retro-isp/news_page.py'),Path('/usr/local/lib/retro-isp/history_archive.py')):
 shutil.copy2(path,backup/path.name)
active={line.split()[0] for line in Path('/var/lib/news/active').read_text().splitlines()}
for group in catalog:
 if group not in active:
  subprocess.run(['runuser','-u','news','--','/usr/sbin/ctlinnd','-t','20','newgroup',group,'y','retro-history'],check=True)
groups_file=Path('/usr/local/lib/retro-isp-news/groups.json');groups=json.loads(groups_file.read_text());groups.update(catalog);groups_file.write_text(json.dumps(groups,indent=2)+'\n')
descriptions=Path('/var/lib/news/newsgroups');lines=[line for line in descriptions.read_text().splitlines() if line.split() and line.split()[0] not in catalog]
descriptions.write_text('\n'.join(lines)+'\n'+''.join(group+'\t'+text+'\n' for group,text in catalog.items()))
shutil.copyfile(ROOT/'expanded-history.json',ROOT/'selected-history.json')
(ROOT/'web-groups.json').write_text(json.dumps(catalog,indent=2)+'\n')
print('Validated',len(new),'records across',len(catalog),'archive groups; backup:',backup,flush=True)
# This changes no INN size/age/retention/access parameters. Existing archive
# permissions and the previously approved policy cover the new groups.
subprocess.run(['/usr/bin/python3','/usr/local/lib/retro-isp-news/import_history.py'],check=True)
