#!/usr/bin/env python3
"""Bounded physical PPP probe in an isolated Linux network namespace.

Run on the Eicon host as root. The HTTP peer must serve deterministic bytes
on GET and return a JSON SHA-256 receipt on POST. Only temporary files and a
dedicated namespace are created; the host's routes and PPP services stay put.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time


def main():
    def interrupted(signum, frame):
        raise RuntimeError(f'probe interrupted by signal {signum}')

    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--device', default='/dev/ttyds6')
    ap.add_argument('--number', default='6001')
    ap.add_argument('--http-host', required=True)
    ap.add_argument('--http-port', type=int, default=47902)
    ap.add_argument('--bytes', type=int, default=262144)
    args = ap.parse_args()
    if subprocess.run(['fuser', args.device], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode == 0:
        raise RuntimeError('test modem already owned')
    namespace = f'emu-ppp-{os.getpid()}'
    process = None
    created = False
    result = {'passed': False}
    with tempfile.TemporaryDirectory(prefix='emu-ppp-') as folder:
        root = Path(folder)
        try:
            subprocess.run(['ip', 'netns', 'add', namespace], check=True)
            created = True
            chat = root / 'dial.chat'
            chat.write_text("ABORT ERROR\nABORT BUSY\nABORT 'NO CARRIER'\n"
                "ABORT 'NO DIALTONE'\nTIMEOUT 5\n'' AT&F5\nOK ATE0V1\n"
                "OK AT+iQ=o2\nOK AT+iO7901\nOK AT+MS=V90a,1\n"
                "OK 'AT\\\\N3%C0'\nTIMEOUT 75\nOK ATD" + args.number + "\nCONNECT '\\c'\n")
            peer = root / 'peer'
            peer.write_text(f'{args.device}\n115200\nlock\nmodem\ncrtscts\n'
                'nodetach\nnoauth\nnoipdefault\nipcp-accept-local\n'
                'ipcp-accept-remote\nnodefaultroute\nnoproxyarp\nnoipv6\n'
                'noccp\nnovj\nasyncmap 0\nlcp-echo-interval 10\n'
                'lcp-echo-failure 6\nmaxconnect 600\nifname emuppp0\n'
                f'connect "/usr/sbin/chat -v -f {chat}"\n')
            log = (root / 'pppd.log').open('w+')
            process = subprocess.Popen(['ip', 'netns', 'exec', namespace,
                '/usr/sbin/pppd', 'file', str(peer)], stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('pppd exited before IPCP')
                addresses = subprocess.run(['ip', '-n', namespace, '-j', 'addr',
                    'show', 'dev', 'emuppp0'], capture_output=True, text=True)
                if addresses.returncode == 0 and any(
                        a.get('family') == 'inet' for row in json.loads(addresses.stdout)
                        for a in row.get('addr_info', [])):
                    break
                time.sleep(1)
            else:
                raise TimeoutError('IPCP did not come up')
            subprocess.run(['ip', '-n', namespace, 'route', 'add', args.http_host + '/32',
                            'dev', 'emuppp0'], check=True)
            ping = subprocess.run(['ip', 'netns', 'exec', namespace, 'ping',
                '-c', '10', '-W', '5', '100.64.0.1'], capture_output=True, text=True, timeout=90)
            print(json.dumps({'event': 'ping', 'returncode': ping.returncode,
                              'output': ping.stdout}), flush=True)
            if ping.returncode or '10 received' not in ping.stdout:
                raise RuntimeError('PPP ping failed')
            # Executed inside the namespace: no LAN route can bypass PPP.
            client = '''import hashlib,json,sys,urllib.request
host,port,count=sys.argv[1],int(sys.argv[2]),int(sys.argv[3])
url='http://%s:%d/'%(host,port)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
expected=hashlib.shake_256(b'emu-ppp-download-v1').digest(count)
actual=opener.open(url,timeout=180).read()
assert actual==expected, 'download mismatch'
payload=hashlib.shake_256(b'emu-ppp-upload-v1').digest(count)
request=urllib.request.Request(url,data=payload,method='POST')
receipt=json.loads(opener.open(request,timeout=180).read())
assert receipt['bytes']==count and receipt['sha256']==hashlib.sha256(payload).hexdigest(), 'upload mismatch'
print(json.dumps(dict(event='files',passed=True,bytes_each_way=count,download_sha256=hashlib.sha256(actual).hexdigest(),upload_sha256=receipt['sha256'])))
'''
            files = subprocess.run(['ip', 'netns', 'exec', namespace, 'python3',
                '-c', client, args.http_host, str(args.http_port), str(args.bytes)],
                capture_output=True, text=True, timeout=420)
            print(files.stdout, end='', flush=True)
            if files.returncode:
                raise RuntimeError(files.stderr)
            result['passed'] = True
        except Exception as exc:
            result['error'] = str(exc)
        finally:
            if process and process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if 'log' in locals():
                log.seek(0)
                print(json.dumps({'event': 'pppd_log', 'text': log.read()}), flush=True)
                log.close()
            if created:
                subprocess.run(['ip', 'netns', 'delete', namespace], check=True)
            print(json.dumps(dict(event='result', **result)), flush=True)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
