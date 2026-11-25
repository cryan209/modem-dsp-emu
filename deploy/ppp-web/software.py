"""Netscape-friendly software catalogue with local and external sources."""
import json
from html import escape
from pathlib import Path

RESOURCES = [
 ('Internet Explorer archives', 'Internet Explorer 3', 'https://winworldpc.com/product/internet-explorer/ie-30', 'Archive page: choose the 32-bit Windows 95 release, not Windows 3.1 or Mac.'),
 ('Internet Explorer archives', 'Internet Explorer 4 / 4.01', 'https://winworldpc.com/product/internet-explorer/ie-4', 'Archive page: look for a full offline installer. Desktop Update changes the Windows shell.'),
 ('Internet Explorer archives', 'Internet Explorer 5.5', 'https://winworldpc.com/product/internet-explorer/ie-55', 'Archive page: choose a full Windows 95-compatible package; small web setup programs may depend on missing Microsoft servers.'),
 ('Utilities', 'PuTTY legacy downloads', 'https://www.chiark.greenend.org.uk/~sgtatham/putty/releases/', 'Publisher release archive. Check the selected release requirements for Windows 95; current builds are not suitable.'),
 ('Utilities', '7-Zip download archive', 'https://www.7-zip.org/download.html', 'Publisher download page with older releases. Check operating system requirements before choosing a version.'),
 ('Drivers', 'VOGONS Vintage Driver Library (text version)', 'https://www.vogonsdrivers.com/lofi/', 'Graphics, sound, network, storage and other vintage drivers. Match the exact hardware model and Windows version.'),
 ('Drivers', 'VOGONS Vintage Driver Library', 'https://www.vogonsdrivers.com/', 'Main driver collection, with model descriptions and supplied documentation.'),
 ('Windows 95 documentation', 'Microsoft Winsock 2 documentation', 'https://learn.microsoft.com/en-us/windows/win32/api/ws2spi/nf-ws2spi-wspstartup', 'Documents Windows 95 support with the Windows Sockets 2 update.'),
]

def page():
    try:entries=json.loads(Path(__file__).with_name('software.json').read_text())
    except (OSError,ValueError):entries=[]
    sections={}
    for entry in entries:sections.setdefault(entry['category'],[]).append(entry)
    for category,title,source,note in RESOURCES:
        sections.setdefault(category,[]).append(dict(title=title,source=source,note=note))
    out=['''<HTML><HEAD><TITLE>Drivers and Updates</TITLE></HEAD>
<BODY BGCOLOR="#FFFFFF" TEXT="#000000" LINK="#0000EE"><FONT FACE="Arial,Helvetica">
<H1>Drivers and Updates</H1><P><A HREF="http://192.168.1.100/">Gateway</A> |
<A HREF="http://192.168.1.100/stats">Connection stats</A> |
<A HREF="http://192.168.1.100:83/upload">Upload PCI report</A> |
<A HREF="http://192.168.1.100:83/welcome.htm">XTRA Home</A></P>
<P>A software shelf for the Windows 95 dial-up lab. Local downloads work without
leaving this server. External archive links open another website.</P>
<P>These are archived packages, not modern security updates. Installation on your
specific Windows build has not been tested. Match the language, Windows edition
and hardware model, and read the supplied setup notes.</P>''']
    for category,items in sections.items():
        out.append('<H2>'+escape(category)+'</H2><TABLE BORDER="1" CELLPADDING="6" CELLSPACING="0">')
        for e in items:
            out.append('<TR><TD VALIGN="TOP"><B>'+escape(e['title'])+'</B><BR>'+escape(e['note']))
            if e.get('filename'):
                size=e['size']/1024/1024
                minutes=e['size']*8/28800/60
                out.append('<P><A HREF="http://192.168.1.100:83/downloads/'+escape(e['filename'],quote=True)+'">Download '+escape(e['filename'])+'</A> ('+f'{size:.2f} MiB'+')</P>')
                out.append('<P>About '+str(max(1,round(minutes)))+' minutes at 28.8 kbit/s before overhead.</P>')
                out.append('<FONT SIZE="2">SHA-256:<BR><TT>'+escape(e['sha256'])+'</TT><BR>Checksum identifies our stored copy; it is not a publisher signature.</FONT><BR>')
            out.append('<A HREF="'+escape(e['source'].replace('https://','http://',1),quote=True)+'">'+('Original source' if e.get('filename') else 'Open archive / source page')+'</A>')
            if e.get('documentation'):
                out.append(' | <A HREF="'+escape(e['documentation'].replace('https://','http://',1),quote=True)+'">Package notes</A>')
            if e.get('added'):
                out.append('<BR><FONT SIZE="2">Added '+escape(e['added'])+'</FONT>')
            out.append('</TD></TR>')
        out.append('</TABLE>')
    out.append('<P>Save installers to disk before running them. Local downloads support resume if your downloader does.</P></FONT></BODY></HTML>')
    return ''.join(out).encode('ascii',errors='xmlcharrefreplace')
