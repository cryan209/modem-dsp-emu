#!/usr/bin/python3
"""Patch the existing portal without replacing unrelated live changes."""
from pathlib import Path
import sys
p=Path(sys.argv[1]);s=p.read_text()
if '\nimport news_page\n' not in s:
    s=s.replace('\nimport signup\n','\nimport signup\nimport news_page\n')
needle="        elif path == '/status':"
if "elif path in ('/news', '/newsgroups')" not in s:
    assert needle in s
    s=s.replace(needle, "        elif path in ('/news', '/newsgroups'):\n            data, kind = page('Xtra newsgroups', news_page.content()), 'text/html; charset=us-ascii'\n"+needle)
if '<A HREF="/news">Newsgroups</A>' not in s:
    s=s.replace('<A HREF="/irc">IRC</A>', '<A HREF="/irc">IRC</A> | <A HREF="/news">Newsgroups</A>')
s=s.replace("['ppp-web', 'xtra-registration', 'ngircd', 'retro-ftp']", "['ppp-web', 'xtra-registration', 'ngircd', 'retro-ftp', 'inn2']")
s=s.replace("['Web gateway / stats', 'Software downloads', 'IRC', 'Personal web FTP']", "['Web gateway / stats', 'Software downloads', 'IRC', 'Personal web FTP', 'Newsgroups (NNTP)']")
needle='<TR><TD><A HREF="/irc"><B>IRC chat</B></A><BR>Visit #xtra and #helpdesk.</TD></TR>'
if '<B>Newsgroups</B>' not in s:
    assert needle in s
    s=s.replace(needle,needle+'<TR><TD><A HREF="/news"><B>Newsgroups</B></A><BR>Local discussions for your newsreader.</TD></TR>')
needle = "        state = states[i] if i < len(states) else 'unknown'\n"
if "[8085, 8083, 6667, 21, 119][i]" not in s:
    assert needle in s
    s = s.replace(needle, needle + "        if state == 'unknown':\n            try:\n                with socket.create_connection((NETWORK, [8085, 8083, 6667, 21, 119][i]), timeout=1):\n                    state = 'active'\n            except OSError:\n                state = 'unreachable'\n")
compile(s,str(p),'exec');p.write_text(s)
