#!/usr/bin/python3
"""Run integration checks from an isolated PPP-like source address."""
import subprocess
import sys

payload = sys.stdin.read()
ns, host, peer = 'retro-isp-test', 'ppp-retro-test', 'retro-test-peer'
def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True)
try:
    run(['ip', 'netns', 'add', ns])
    run(['ip', 'link', 'add', host, 'type', 'veth', 'peer', 'name', peer])
    run(['ip', 'link', 'set', peer, 'netns', ns])
    run(['ip', 'addr', 'add', '10.254.94.1/30', 'dev', host])
    run(['ip', 'link', 'set', host, 'up'])
    for args in (['link','set','lo','up'], ['addr','add','10.254.94.2/30','dev',peer], ['link','set',peer,'up'], ['route','add','default','via','10.254.94.1']):
        run(['ip','netns','exec',ns,'ip',*args])
    result = subprocess.run(['ip','netns','exec',ns,'python3','/tmp/retro-isp-stage/verify.py'], input=payload, capture_output=True, text=True, timeout=60)
    print(result.stdout)
    if result.returncode:
        print(result.stderr); raise SystemExit(result.returncode)
    result = run(['ip','netns','exec',ns,'curl','--noproxy','*','-sS','--max-time','8','-D','-','http://192.168.1.100/status'])
    assert '302' in result.stdout and 'http://10.150.150.157:8086/status' in result.stdout
    print('PPP transparent /status route passed')
finally:
    subprocess.run(['ip','link','del',host],capture_output=True)
    subprocess.run(['ip','netns','del',ns],capture_output=True)
