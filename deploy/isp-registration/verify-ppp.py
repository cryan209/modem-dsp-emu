#!/usr/bin/python3
"""Verify real PAP credentials inside a disposable network namespace."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

NS='isp-signup-verify'
CREDENTIALS=Path('/var/lib/retro-isp-registration/verification.json')


def attempt(credentials,correct):
    left,right=socket.socketpair();processes=[]
    with tempfile.TemporaryDirectory(prefix='isp-pap-') as directory:
        options=Path(directory)/'client-options';options.write_text('user '+credentials['username']+'\npassword '+(credentials['password'] if correct else 'incorrect-verification-password')+'\n');options.chmod(0o600)
        common=['ip','netns','exec',NS,'pppd','notty','nodetach','local','noipv6','noccp','novj','nodefaultroute','noproxyarp','nopersist','maxfail','1','logfd','2','hide-password']
        try:
            server=subprocess.Popen(common+['require-pap','name','multitech-7910','ifname','pppverify','10.254.104.1:10.254.104.2'],stdin=left,stdout=left,stderr=subprocess.PIPE);processes.append(server)
            client=subprocess.Popen(common+['noauth','noipdefault','ifname','pppclient','file',str(options)],stdin=right,stdout=right,stderr=subprocess.PIPE);processes.append(client)
            left.close();right.close()
            until=time.monotonic()+12;up=False
            while time.monotonic()<until:
                result=subprocess.run(['ip','-n',NS,'-j','addr','show'],capture_output=True,text=True)
                links=json.loads(result.stdout or '[]')
                up=sum(any(a.get('family')=='inet' for a in l.get('addr_info',[])) for l in links if l['ifname'] in ('pppverify','pppclient'))==2
                if up or any(p.poll() is not None for p in processes):break
                time.sleep(.2)
            assert up==correct, 'Unexpected PAP authentication result'
        finally:
            for process in processes:
                if process.poll() is None:process.terminate()
            logs=[]
            for process in processes:
                try:logs.append(process.communicate(timeout=4)[1].decode(errors='replace'))
                except subprocess.TimeoutExpired:process.kill();process.communicate()
            left.close();right.close()
        if not correct:
            assert any('authentication fail' in log.lower() or 'login incorrect' in log.lower() for log in logs),'Expected authentication rejection not observed'
        print('Correct password accepted and IPCP established' if correct else 'Wrong password rejected despite legacy wildcard secrets')


if __name__=='__main__':
    credentials=json.loads(CREDENTIALS.read_text())
    subprocess.run(['ip','netns','add',NS],check=True)
    try:
        subprocess.run(['ip','-n',NS,'link','set','lo','up'],check=True)
        attempt(credentials,True);attempt(credentials,False)
    finally:subprocess.run(['ip','netns','del',NS],check=True)
