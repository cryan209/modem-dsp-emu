"""Small server-rendered PPP dashboard, compatible with Netscape 3."""
import html
import json
import subprocess
import time
from pathlib import Path

previous = {}
observed = {}
web = {'requests': 0, 'errors': 0, 'images': 0, 'original': 0, 'compressed': 0}
started = time.time()

def size(n):
    for unit in ('B', 'KiB', 'MiB', 'GiB'):
        if abs(n) < 1024 or unit == 'GiB':
            return f'{n:,.1f} {unit}'
        n /= 1024

def member_snapshot(name, index, now):
    try:
        data = json.loads(Path('/run/ppp-stats/members.json').read_text())
        bundle = data['bundles'].get(name, {})
        if 0 <= now-data['sampled_at'] <= 20 and bundle.get('ifindex') == index:
            return bundle.get('members', [])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return []


def combined_rates(members):
    result = {'member_count': len(members)}
    for direction in ('download', 'upload'):
        values = [m.get('modem_'+direction+'_bps') for m in members]
        result[direction+'_bps'] = sum(values) if values and all(isinstance(v, int) for v in values) else None
    return result


def snapshot(client):
    now = time.time()
    links = json.loads(subprocess.check_output(['ip', '-j', '-s', 'addr', 'show'], timeout=3))
    rows = []
    for link in links:
        if link.get('link_type') != 'ppp':
            continue
        addr = next((a for a in link.get('addr_info', []) if a['family'] == 'inet'), None)
        if not addr:
            continue
        name, index = link['ifname'], link['ifindex']
        key = (name, index)
        observed.setdefault(key, now)
        counters = link.get('stats64', link.get('stats', {}))
        rx, tx = counters.get('rx', {}), counters.get('tx', {})
        # Host RX is caller upload; host TX is caller download.
        up, down = rx.get('bytes', 0), tx.get('bytes', 0)
        prior = previous.get(key)
        up_rate = down_rate = None
        if prior and now-prior[0] >= 1:
            up_rate = max(0, up-prior[1])/(now-prior[0])
            down_rate = max(0, down-prior[2])/(now-prior[0])
        if not prior or now-prior[0] >= 1:
            previous[key] = (now, up, down)
        session_start = None
        modem = {}
        try:
            record = json.loads((Path('/run/ppp-stats') / (name+'.json')).read_text())
            if record['ifindex'] == index:
                session_start = record['connected_at']
                modem = {key: value for key,value in record.items() if key.startswith('modem_')}
        except (OSError, ValueError, KeyError):
            pass
        peer = addr.get('address', '')
        rows.append(dict(interface=name, peer=peer, local=addr['local'], yours=peer==client,
                         connected_seconds=int(now-session_start) if session_start else None,
                         observed_seconds=int(now-observed[key]), upload_bytes=up, download_bytes=down,
                         upload_bytes_per_second=up_rate, download_bytes_per_second=down_rate,
                         errors=rx.get('errors',0)+tx.get('errors',0),
                         dropped=rx.get('dropped',0)+tx.get('dropped',0),
                         members=member_snapshot(name, index, now), **modem))
    for row in rows:
        row['combined_line_rates'] = combined_rates(row['members'])
    live = {(x['ifname'],x['ifindex']) for x in links}
    for mapping in (previous, observed):
        for key in list(mapping):
            if key not in live:
                del mapping[key]
    return {'connections': rows, 'web': dict(web), 'proxy_uptime_seconds':int(now-started)}

def duration(seconds):
    hours, remainder = divmod(max(0, seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours:d}:{minutes:02d}:{seconds:02d}'

def page(data):
    rows = []
    esc = html.escape
    for row in data['connections']:
        connected = row['connected_seconds']
        age = duration(connected) if connected is not None else 'Tracked '+duration(row['observed_seconds'])
        rate = lambda value: '--' if value is None else size(value)+'/s'
        rows.append('<TR'+(' BGCOLOR="#FFFFCC"' if row['yours'] else '')+'><TD>'+esc(row['interface'])+
                    (' (you)' if row['yours'] else '')+'</TD><TD>'+esc(row['peer'])+'</TD><TD>'+age+
                    '</TD><TD>'+size(row['download_bytes'])+'</TD><TD>'+size(row['upload_bytes'])+
                    '</TD><TD>'+rate(row['download_bytes_per_second'])+'</TD><TD>'+rate(row['upload_bytes_per_second'])+
                    '</TD><TD>'+str(row['errors'])+' / '+str(row['dropped'])+'</TD></TR>')
    modem_rows = []
    for row in data['connections']:
        line = esc(row.get('modem_connect', 'Not captured for this call; available after reconnect.'))
        if 'modem_download_bps' in row:
            line += '<BR>Download: '+str(row['modem_download_bps'])+' bit/s; upload: '+str(row['modem_upload_bps'])+' bit/s'
        elif 'modem_reported_bps' in row:
            line += '<BR>Reported: '+str(row['modem_reported_bps'])+' bit/s (modem does not label the direction).'
        device = row.get('modem_device', '')
        label = ('Eicon ' if device.startswith('ttyds') else 'MultiTech ' if device.startswith('ttyM') else '')+device
        modem_rows.append('<TR><TD>'+esc(row['interface'])+('<BR>'+esc(label) if label else '')+'</TD><TD>'+line+'</TD></TR>')
    modem_table = '<H2>Modem Connect Rates</H2><TABLE BORDER="1" CELLPADDING="5">'+''.join(modem_rows)+'</TABLE><P>Initial negotiated rates for the link that brought the interface up. Multilink traffic totals above cover the whole bundle; this line rate is not the sum of its members. Retrains during the call are not tracked.</P>' if modem_rows else ''
    member_rows = []
    for row in data['connections']:
        for member in row.get('members', []):
            rates = esc(member.get('modem_connect', 'Connect rate unavailable'))
            if 'modem_download_bps' in member:
                rates += '<BR>Download: '+str(member['modem_download_bps'])+' bit/s; upload: '+str(member['modem_upload_bps'])+' bit/s'
            elif 'modem_reported_bps' in member:
                rates += '<BR>Reported: '+str(member['modem_reported_bps'])+' bit/s (direction unspecified)'
            member_rows.append('<TR><TD>'+esc(row['interface'])+'</TD><TD>'+esc(member['card'])+' '+esc(member['device'])+
                               '</TD><TD>'+esc(member['incoming_number'])+'</TD><TD>'+rates+'</TD></TR>')
        members = row.get('members', [])
        if members:
            total = combined_rates(members)
            def line_rate(value):
                return 'Unavailable' if value is None else f'{value:,} bit/s ({value/8000:.2f} kB/s before overhead)'
            combined = 'Download: '+line_rate(total['download_bps'])+'<BR>Upload: '+line_rate(total['upload_bps'])
            if total['download_bps'] is None or total['upload_bps'] is None:
                combined += '<BR>Some members do not report directional rates; their speeds are not guessed.'
            member_rows.append('<TR BGCOLOR="#DDDDDD"><TD>'+esc(row['interface'])+'</TD><TD COLSPAN="2"><B>Combined ('+
                               str(len(members))+' active links)</B></TD><TD>'+combined+'</TD></TR>')
    member_table = ('<H2>Active PPP Links</H2><TABLE BORDER="1" CELLPADDING="5"><TR><TH>Bundle</TH><TH>Modem</TH>'
                    '<TH>Incoming number (configured)</TH><TH>Connect rates</TH></TR>'+''.join(member_rows)+
                    '</TABLE><P>Member list updates about every 5 seconds. Incoming number is the configured answering number, '
                    'not caller ID or a captured called-number indication. Traffic counters above cover the whole bundle. Combined rates sum initial negotiated line rates; actual throughput is lower and retrains are not tracked.</P>') if member_rows else ''
    if member_rows:
        modem_table = member_table
    w = data['web']; saved = w['original']-w['compressed']
    percent = 100*saved/w['original'] if w['original'] else 0
    return '''<HTML><HEAD><TITLE>PPP Connection Stats</TITLE>
<META HTTP-EQUIV="REFRESH" CONTENT="15; URL=/stats"></HEAD>
<BODY BGCOLOR="#FFFFFF" TEXT="#000000"><FONT FACE="Arial,Helvetica">
<H1>PPP Connection Stats</H1><P><A HREF="/stats">Refresh</A> |
<A HREF="/">Gateway</A> | <A HREF="http://192.168.1.100:83/welcome.htm">XTRA Home</A></P>
<TABLE BORDER="1" CELLPADDING="5" CELLSPACING="0"><TR BGCOLOR="#DDDDDD">
<TH>Connection</TH><TH>Client IP</TH><TH>Connected</TH><TH>Downloaded</TH><TH>Uploaded</TH>
<TH>Down rate</TH><TH>Up rate</TH><TH>Errors / drops</TH></TR>'''+(''.join(rows) or '<TR><TD COLSPAN="8">No active PPP connections.</TD></TR>')+'''
</TABLE><P>Traffic and rates are from the caller's point of view. Rates are averaged
between page samples. Auto-refresh: 15 seconds.</P>
<P>For calls already active when tracking began, "Tracked" shows observation time;
new calls show their connection duration. Rates are traffic throughput, not modem line speed.</P>
'''+modem_table+'''
<H2>Web Compression</H2><TABLE BORDER="1" CELLPADDING="5" CELLSPACING="0">
<TR><TD>Web requests / failures</TD><TD>'''+str(w['requests'])+' / '+str(w['errors'])+'''</TD></TR>
<TR><TD>Images converted</TD><TD>'''+str(w['images'])+'''</TD></TR>
<TR><TD>Original image data</TD><TD>'''+size(w['original'])+'''</TD></TR>
<TR><TD>Compressed image data</TD><TD>'''+size(w['compressed'])+'''</TD></TR>
<TR><TD>Image data saved</TD><TD>'''+size(saved)+f' ({percent:.1f}%)'+'''</TD></TR></TABLE>
<P>Compression totals cover all callers since the web proxy last started (uptime '''+duration(data['proxy_uptime_seconds'])+''').
Only converted images are included. PPP traffic totals cover each current interface's lifetime.</P>
</FONT></BODY></HTML>'''
