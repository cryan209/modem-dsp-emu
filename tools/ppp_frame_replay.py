#!/usr/bin/env python3
"""Feed the frames our converter produced to a real pppd over a pty pair."""
import os, pty, subprocess, sys, time

src = open('/root/mlppp-test/raw.rx', 'rb').read()
i = src.find(b'\x7e')
frames = src[i:]                      # just the async-framed part
print('replay bytes:', len(frames))

master, slave = pty.openpty()
name = os.ttyname(slave)
print('pty slave:', name)

p = subprocess.Popen(['/usr/sbin/pppd', name, '115200', 'nolock', 'local',
                      'nodetach', 'debug', 'noauth', 'passive',
                      'noipdefault', 'nodefaultroute', 'noproxyarp',
                      'noipv6', 'noccp', 'novj', 'asyncmap', '0',
                      'linkname', 'replay'],
                     stdout=open('/root/mlppp-test/replay.log', 'wb'),
                     stderr=subprocess.STDOUT)
time.sleep(3)
for _ in range(6):
    os.write(master, frames)
    time.sleep(1)
time.sleep(3)
p.terminate()
try:
    p.wait(timeout=5)
except subprocess.TimeoutExpired:
    p.kill()
os.close(master); os.close(slave)
log = open('/root/mlppp-test/replay.log').read()
print('--- pppd saw ---')
for line in log.splitlines():
    if 'rcvd' in line or 'bad' in line.lower() or 'fcs' in line.lower():
        print(' ', line)
print('rcvd lines:', log.count('rcvd'))
