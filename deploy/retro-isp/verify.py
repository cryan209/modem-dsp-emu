#!/usr/bin/python3
"""Integration checks; read temporary FTP credentials from stdin, never argv."""
import ftplib
import io
import json
import socket
import sys
import time
import urllib.request


def run():
    credentials = json.load(sys.stdin)
    host = '10.150.150.157'
    for path, expected in [('/status', b'ISP service status'), ('/irc', b'#helpdesk'), ('/homepages', b'snuggi'), ('/~snuggi/', b'UNDER CONSTRUCTION')]:
        with urllib.request.urlopen('http://'+host+':8086'+path, timeout=8) as response:
            assert response.status == 200 and expected in response.read()
    ftp = ftplib.FTP(); ftp.connect(host, 21, timeout=10)
    try:
        ftp.login(credentials['username'], 'incorrect-test-password')
        raise AssertionError('Wrong FTP password accepted')
    except ftplib.error_perm:
        pass
    ftp.close()
    ftp = ftplib.FTP(); ftp.connect(host, 21, timeout=10); ftp.login(credentials['username'], credentials['password'])
    filename = 'isp-verification-'+str(int(time.time()))+'.txt'
    content = b'Personal web FTP upload verification.\r\n'
    try:
        ftp.storbinary('STOR '+filename, io.BytesIO(content))
        for passive in (True, False):
            ftp.set_pasv(passive); chunks = []; ftp.retrbinary('RETR '+filename, chunks.append); assert b''.join(chunks) == content
        with urllib.request.urlopen('http://'+host+':8086/~snuggi/'+filename, timeout=8) as response:
            assert response.read() == content
        ftp.cwd('../../'); assert ftp.pwd() == '/'
        try:
            ftp.retrbinary('RETR /etc/passwd', lambda data: None)
            raise AssertionError('FTP escaped personal root')
        except ftplib.error_perm:
            pass
    finally:
        ftp.delete(filename); ftp.quit()
    print('HTTP pages, wrong-password rejection, passive/active FTP, immediate web publication and FTP confinement passed')
    clients = []
    try:
        for nick in ('ispqaA', 'ispqaB'):
            client = socket.create_connection((host, 6667), timeout=5); client.settimeout(.4); clients.append(client)
            client.sendall(('NICK '+nick+'\r\nUSER '+nick+' 0 * :ISP verification\r\n').encode())
            data = b''; until = time.monotonic()+5
            while time.monotonic() < until and b' 001 ' not in data:
                try:
                    chunk = client.recv(8192); data += chunk
                    for line in chunk.split(b'\r\n'):
                        if line.startswith(b'PING '): client.sendall(b'PONG '+line[5:]+b'\r\n')
                except socket.timeout:
                    pass
            assert b' 001 ' in data, data[-200:]
        clients[0].sendall(b'JOIN #xtra\r\nPRIVMSG ispqaB :retro-isp-verification\r\n')
        data = b''; until = time.monotonic()+4
        while time.monotonic() < until and b'retro-isp-verification' not in data:
            try: data += clients[1].recv(8192)
            except socket.timeout: pass
        assert b'retro-isp-verification' in data
        print('Two IRC registrations, channel join and private test-message delivery passed')
    finally:
        for client in clients:
            try: client.sendall(b'QUIT :verification complete\r\n')
            except OSError: pass
            client.close()


if __name__ == '__main__':
    run()
