#!/usr/bin/python3
"""Publish active MLPPP member metadata without reading serial payloads."""
import array
import fcntl
import json
import os
import re
import tempfile
import subprocess
import time
from pathlib import Path

# Answering numbers provisioned by these peer/chat configurations.
PEERS = {
    'multitech-7910': ('ttyM4', '7910'),
    'multitech-7910-b': ('ttyM5', '7910'),
    'eicon-7910-c': ('ttyds3', '7910'),
    'eicon-7910-d': ('ttyds4', '7910'),
    'eicon-7900-1': ('ttyds1', '7900'),
    'eicon-7900-2': ('ttyds2', '7900'),
    'eicon-7920-14': ('ttyds14', '7920'),
    'eicon-7920-15': ('ttyds15', '7920'),
    'eicon-7930-12': ('ttyds12', '7930'),
    'eicon-7930-13': ('ttyds13', '7930'),
}
PPPIOCGUNIT = 0x80047456


def collect():
    bundles = {}
    for peer, (device, number) in PEERS.items():
        try:
            config = (Path('/etc/ppp/peers') / peer).read_text()
            match = re.search(r'^ifname\s+(\S+)\s*$', config, re.M)
            if not match or '/' in match[1] or '\nmultilink\n' not in config:
                continue
            interface = match[1]
            index = int((Path('/sys/class/net') / interface / 'ifindex').read_text())
            fd = os.open('/dev/'+device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            try:
                unit = array.array('i', [-1])
                fcntl.ioctl(fd, PPPIOCGUNIT, unit, True)
            finally:
                os.close(fd)
            # A chat listener has no PPP discipline/unit and must not appear live.
            if unit[0] < 0:
                continue
            row = dict(device=device, card='Eicon' if device.startswith('ttyds') else 'MultiTech',
                       incoming_number=number, peer=peer, ppp_unit=unit[0])
            try:
                report = (Path('/run/ppp-modem') / (device+'.report')).read_text()[-4096:]
            except OSError:
                report = ''
            connect = re.search(r'CONNECT[^\r\n]*', report)
            if connect:
                row['modem_connect'] = connect[0].strip()[:200]
                tx = re.search(r'(\d+):TX', connect[0])
                rx = re.search(r'(\d+):RX', connect[0])
                if tx and rx:
                    row.update(modem_download_bps=int(tx[1]), modem_upload_bps=int(rx[1]))
                else:
                    rate = re.match(r'CONNECT\s+(\d+)', connect[0])
                    if rate:
                        row['modem_reported_bps'] = int(rate[1])
            bundle = bundles.setdefault(interface, dict(ifindex=index, members=[]))
            bundle['members'].append(row)
        except (OSError, ValueError):
            continue
    links = json.loads(subprocess.check_output(['ip', '-j', '-s', 'addr', 'show'], timeout=3))
    for link in links:
        bundle = bundles.get(link['ifname'])
        if bundle and bundle['ifindex'] == link['ifindex']:
            counters = link.get('stats64', link.get('stats', {}))
            bundle['download_bytes'] = counters.get('tx', {}).get('bytes', 0)
            bundle['upload_bytes'] = counters.get('rx', {}).get('bytes', 0)
    return dict(sampled_at=time.time(), bundles=bundles)


def main():
    root = Path('/run/ppp-stats')
    root.mkdir(mode=0o755, exist_ok=True)
    data = collect()
    for interface, bundle in data['bundles'].items():
        history_file = root/('history-'+str(bundle['ifindex'])+'.json')
        try:
            history = json.loads(history_file.read_text())
        except (OSError, ValueError):
            history = {}
        members = {m['device']: m for m in history.get('members', [])}
        members.update({m['device']: m for m in bundle['members']})
        history.update(bundle, interface=interface, sampled_at=data['sampled_at'])
        history['members'] = list(members.values())
        tmp = history_file.with_suffix('.tmp')
        tmp.write_text(json.dumps(history)); tmp.chmod(0o644); tmp.replace(history_file)
    fd, filename = tempfile.mkstemp(prefix='members-', suffix='.tmp', dir=root)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream)
            os.fchmod(stream.fileno(), 0o644)
        os.replace(filename, root/'members.json')
    finally:
        Path(filename).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
