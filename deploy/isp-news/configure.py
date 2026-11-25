#!/usr/bin/python3
"""Install the private NNTP configuration on eicon420 as root."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = Path('/etc/news')
backup = Path('/var/backups/isp-news-config-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
backup.mkdir(mode=0o700)
for name in ('inn.conf', 'readers.conf', 'newsfeeds', 'incoming.conf', 'expire.ctl'):
    shutil.copy2(ROOT/name, backup/name)

settings = {
    'organization': '"Xtra private retro ISP"',
    'pathhost': 'eicon420.net.cryan.nz',
    'domain': 'net.cryan.nz',
    'fromhost': 'net.cryan.nz',
    'bindaddress': '10.150.150.157',
    'bindaddress6': 'none',
    'maxartsize': '131072',
    'artcutoff': '0',
    'localmaxartsize': '65536',
    'addinjectionpostinghost': 'false',
    'maxconnections': '16',
    'initialtimeout': '15',
    'clienttimeout': '900',
}
s = (ROOT/'inn.conf').read_text()
for key, value in settings.items():
    pattern = r'(?m)^#?' + re.escape(key) + r':[^\n]*$'
    if re.search(pattern, s):
        s = re.sub(pattern, key + ': ' + value, s)
    else:
        s += '\n' + key + ': ' + value + '\n'
(ROOT/'inn.conf').write_text(s)
(ROOT/'readers.conf').write_text((HERE/'readers.conf').read_text())
(ROOT/'newsfeeds').write_text('# Local only: no outgoing feeds or control-message handler.\nME:::\n')
(ROOT/'incoming.conf').write_text('# No NNTP feed peers. All network clients use reader permissions.\n')
(ROOT/'expire.ctl').write_text('/remember/:11\n*:A:1:90:90\nxtra.*:A:30:365:365\nnz.general:A:30:365:365\ndev.*:A:30:365:365\narchive.*:A:never:never:never\n')
subprocess.run(['/usr/lib/news/bin/innconfval', '-v'], check=True)
subprocess.run(['systemctl', 'restart', 'inn2'], check=True)
groups = json.loads((HERE/'groups.json').read_text())
for group in groups:
    subprocess.run(['runuser', '-u', 'news', '--', '/usr/sbin/ctlinnd', '-t', '20', 'newgroup', group, 'y', 'retro-isp'], check=True)
p = Path('/var/lib/news/newsgroups')
shutil.copy2(p, backup/'newsgroups')
lines = [line for line in p.read_text().splitlines() if line.split() and line.split()[0] not in groups]
p.write_text('\n'.join(lines) + '\n' + ''.join(name + '\t' + text + '\n' for name, text in groups.items()))
subprocess.run(['systemctl', 'enable', 'inn2'], check=True)
print('Configured groups; backup:', backup)
