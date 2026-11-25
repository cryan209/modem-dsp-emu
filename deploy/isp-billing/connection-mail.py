#!/usr/bin/python3
"""Queue one connection summary at ip-down and retry local SMTP separately."""
import argparse
from datetime import datetime
from email.message import EmailMessage
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import smtplib
import tempfile
import time

ROOT = Path('/var/spool/retro-isp-mail')
STATS = Path('/run/ppp-stats')
CONFIG = Path('/etc/retro-isp-connection-mail.json')


def atomic(path, data):
    fd, name = tempfile.mkstemp(prefix='queue-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream)
            os.fchmod(stream.fileno(), 0o600)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def load(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def queue():
    interface = os.environ.get('PPP_IFACE', '')
    if interface != 'mtppp0':
        return
    session = load(STATS/(interface+'.json'))
    if not session.get('ifindex'):
        return
    history = load(STATS/('history-'+str(session['ifindex'])+'.json'))
    ended = time.time()
    record = dict(interface=interface, ended_at=ended,
                  connected_at=session.get('connected_at'), members=history.get('members', []),
                  account=os.environ.get('PEERNAME', ''))
    config = mail_settings()
    record['recipient'] = recipient_for(record, config)
    record['tariff'] = tariff_for(record, config)
    for field, env in (('download_bytes', 'BYTES_SENT'), ('upload_bytes', 'BYTES_RCVD')):
        value = os.environ.get(env, '')
        record[field] = int(value) if value.isdigit() else history.get(field)
        record[field+'_source'] = 'final PPP counter' if value.isdigit() else 'last sampled counter'
    identity = interface+':'+str(session['ifindex'])+':'+str(session.get('connected_at'))
    key = hashlib.sha256(identity.encode()).hexdigest()[:24]
    record['id'] = key
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not (ROOT/(key+'.sent')).exists() and not (ROOT/(key+'.json')).exists():
        atomic(ROOT/(key+'.json'), record)


def mail_settings():
    config = json.loads(CONFIG.read_text())
    addresses = [config['sender'], config['recipient']]
    addresses.extend(a['recipient'] for a in config.get('accounts', {}).values())
    for value in addresses:
        if not isinstance(value, str) or '\r' in value or '\n' in value or value.count('@') != 1:
            raise ValueError('Invalid configured email address')
    tariff_for({}, config)
    for account in config.get('accounts', {}):
        tariff_for({'account': account}, config)
    return config


def recipient_for(record, config):
    return config.get('accounts', {}).get(record.get('account', ''), {}).get('recipient', config['recipient'])


def tariff_for(record, config):
    tariff = dict(config.get('tariff', {}))
    tariff.update(config.get('accounts', {}).get(record.get('account', ''), {}).get('tariff', {}))
    for field in ('cents_per_kb', 'cents_per_minute'):
        if type(tariff.get(field)) is not int or tariff[field] < 0:
            raise ValueError('Tariff must use nonnegative integer cents')
    if tariff.get('currency') != 'NZD':
        raise ValueError('Only NZD mock tariffs are supported')
    return tariff


def charges(record, tariff):
    down, up = record.get('download_bytes'), record.get('upload_bytes')
    kb = (down+up+999)//1000 if type(down) is int and type(up) is int and down >= 0 and up >= 0 else None
    start = record.get('connected_at')
    minutes = math.ceil(max(0, record['ended_at']-start)/60) if start is not None else None
    data_cents = kb*tariff['cents_per_kb'] if kb is not None else None
    time_cents = minutes*tariff['cents_per_minute'] if minutes is not None else None
    return dict(kb=kb, minutes=minutes, data_cents=data_cents, time_cents=time_cents,
                total_cents=data_cents+time_cents if data_cents is not None and time_cents is not None else None)


def money(cents):
    return 'unavailable' if cents is None else f'NZD {cents//100:,}.{cents%100:02d}'


def message(record, config=None):
    config = mail_settings() if config is None else config
    msg = EmailMessage()
    msg['From'] = 'Xtra ISP <'+config['sender']+'>'
    msg['To'] = record.get('recipient') or recipient_for(record, config)
    msg['Subject'] = 'Xtra ISP - mock connection bill - '+datetime.fromtimestamp(record['ended_at']).strftime('%d %b %Y %H:%M')
    msg['Message-ID'] = '<connection-'+record['id']+'@net.cryan.nz>'
    from email.utils import formatdate
    msg['Date'] = formatdate(record['ended_at'], localtime=True)
    lines = ['Xtra ISP connection statement', '', 'This is a retro ISP simulation. No payment is due.', '',
             'PPP login: '+(record.get('account') or 'not recorded'), 'Bundle: '+record['interface']]
    start = record.get('connected_at')
    if start:
        seconds = max(0, int(record['ended_at']-start))
        lines += ['Connected: '+datetime.fromtimestamp(start).strftime('%d %b %Y %H:%M:%S'),
                  'Duration: '+str(seconds//3600)+':'+f'{seconds//60%60:02d}:{seconds%60:02d}']
    lines += ['Disconnected: '+datetime.fromtimestamp(record['ended_at']).strftime('%d %b %Y %H:%M:%S')]
    for field, label in (('download_bytes', 'Downloaded'), ('upload_bytes', 'Uploaded')):
        value = record.get(field)
        lines.append(label+': '+(f'{value:,} bytes ({value/1048576:.2f} MiB)' if isinstance(value, int) else 'unavailable')+' - '+record.get(field+'_source', ''))
        if start and isinstance(value, int) and record['ended_at']>start:
            lines.append('Average '+label.lower()+': '+f'{value/(record["ended_at"]-start)/1000:.2f} kB/s')
    tariff = record.get('tariff') or tariff_for(record, config)
    bill = charges(record, tariff)
    lines += ['', 'Your delightfully dreadful mock bill:',
              'Data (download + upload): '+(str(bill['kb'])+' KB' if bill['kb'] is not None else 'unavailable')+
              ' at '+str(tariff['cents_per_kb'])+' cents/KB = '+money(bill['data_cents']),
              'Connection time: '+(str(bill['minutes'])+' minutes' if bill['minutes'] is not None else 'unavailable')+
              ' at '+str(tariff['cents_per_minute'])+' cents/minute = '+money(bill['time_cents']),
              'MOCK TOTAL: '+money(bill['total_cents']),
              '1 KB = 1,000 bytes. Combined traffic and elapsed bundle time are',
              'rounded UP to whole KB and minutes. Time is charged once per bundle,',
              'not once per modem. No tax or other fees added.',
              '', 'Modem links observed during this connection:']
    for member in record.get('members', []):
        lines.append(member['card']+' '+member['device']+' - incoming number '+member['incoming_number']+' (configured)')
        lines.append('  '+member.get('modem_connect', 'Connect rate unavailable'))
    if not record.get('members'):
        lines.append('Member history unavailable.')
    lines += ['', 'Traffic totals are PPP counters from the caller perspective, not a charge.', 'Link rates are initial rates; retrains are not tracked.']
    msg.set_content('\n'.join(lines)+'\n', charset='us-ascii')
    return msg


def drain():
    config = mail_settings()
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (ROOT/'send.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for path in sorted(ROOT.glob('*.json')):
            record = load(path)
            if not record:
                continue
            msg = message(record, config)
            with smtplib.SMTP(config.get('smtp_host', 'mail.net.cryan.nz'), config.get('smtp_port', 25), timeout=20) as smtp:
                smtp.send_message(msg)
            path.rename(path.with_suffix('.sent'))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--queue', action='store_true'); args = ap.parse_args()
    if args.queue:
        queue()
    else:
        drain()
